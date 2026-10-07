"""API tests against a real PostgreSQL database.

Set TEST_DATABASE_URL to an empty, disposable database; the schema and
migrations are applied to it and every table is truncated between tests.
Run from the repository root:

    TEST_DATABASE_URL=postgresql://... pytest orchestrator/tests
"""

import os
import pathlib
import sys

import psycopg
import pytest

DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

ROOT = pathlib.Path(__file__).resolve().parents[1]
APOLLO = "apollo-hermes"
NUC = "nuc-worker-01"


@pytest.fixture(scope="session")
def client():
    os.environ["DATABASE_URL"] = DB_URL
    os.environ.pop("JOB_TYPE_OWNERS", None)
    with psycopg.connect(DB_URL) as conn:
        conn.execute((ROOT / "database" / "schema.sql").read_text())
        for migration in sorted((ROOT / "database" / "migrations").glob("*.sql")):
            conn.execute(migration.read_text())
    sys.path.insert(0, str(ROOT / "app"))
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


@pytest.fixture(autouse=True)
def clean(client):
    with psycopg.connect(DB_URL) as conn:
        conn.execute(
            "TRUNCATE jobs, findings, evidence, questions, projects RESTART IDENTITY CASCADE"
        )
    yield


def sql(query, *params):
    with psycopg.connect(DB_URL) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else None


def project(client, status="active", code="WATER-1", level=1):
    created = client.post("/projects", json={
        "code": code, "name": f"Project {code}", "maslow_level": level,
        "domain": "water"}).json()
    assert created["status"] == "candidate"
    if status != "candidate":
        client.post(f"/projects/{created['id']}/status", json={"status": status})
    return created["id"]


def question(client, project_id, text, priority=None, status="open"):
    q = client.post("/questions", json={
        "project_id": project_id, "question": text, "priority": priority}).json()
    if status != "candidate":
        r = client.post(f"/questions/{q['id']}/status", json={"status": status})
        assert r.status_code == 200, r.text
    return q["id"]


def job(client, job_type="literature-collection", project_id=None):
    return client.post("/jobs", json={
        "project_id": project_id, "job_type": job_type}).json()["id"]


def claim(client, worker, types):
    return client.post("/jobs/claim", json={"worker": worker, "job_types": types})


# ---------------------------------------------------------------------------
# Lease heartbeat
# ---------------------------------------------------------------------------


def test_heartbeat_renews_only_the_owners_lease(client):
    pid = project(client)
    jid = job(client, project_id=pid)
    assert claim(client, APOLLO, ["literature-collection"]).json()["job"]["id"] == jid
    sql("UPDATE jobs SET lease_until = NOW() + INTERVAL '1 minute' WHERE id = %s", jid)

    r = client.post(f"/jobs/{jid}/heartbeat", json={"worker": APOLLO})
    assert r.status_code == 200
    [(remaining,)] = sql(
        "SELECT EXTRACT(EPOCH FROM lease_until - NOW()) FROM jobs WHERE id = %s", jid)
    assert remaining > 14 * 60

    assert client.post(f"/jobs/{jid}/heartbeat", json={"worker": NUC}).status_code == 409
    client.post(f"/jobs/{jid}/complete", json={"worker": APOLLO, "output": {}})
    assert client.post(f"/jobs/{jid}/heartbeat", json={"worker": APOLLO}).status_code == 409


def test_expired_lease_is_reclaimable_and_heartbeat_prevents_it(client):
    pid = project(client)
    jid = job(client, project_id=pid)
    claim(client, APOLLO, ["literature-collection"])
    sql("UPDATE jobs SET lease_until = NOW() - INTERVAL '1 second' WHERE id = %s", jid)
    again = claim(client, APOLLO, ["literature-collection"]).json()["job"]
    assert again["id"] == jid and again["attempts"] == 2
    client.post(f"/jobs/{jid}/heartbeat", json={"worker": APOLLO})
    assert claim(client, APOLLO, ["literature-collection"]).json()["job"] is None


# ---------------------------------------------------------------------------
# Job-type ownership (partition between Apollo and the NUC worker)
# ---------------------------------------------------------------------------


def test_reserved_job_types_cannot_be_claimed_by_another_worker(client):
    pid = project(client)
    jid = job(client, project_id=pid)
    for types in (["literature-collection"], ["summarise", "literature-collection"]):
        r = claim(client, NUC, types)
        assert r.status_code == 403, types
    sql("UPDATE jobs SET status = 'running', worker = %s, "
        "lease_until = NOW() - INTERVAL '1 second' WHERE id = %s", APOLLO, jid)
    assert claim(client, NUC, ["literature-collection"]).status_code == 403
    assert claim(client, APOLLO, ["literature-collection"]).json()["job"]["id"] == jid


def test_nuc_worker_types_are_unaffected(client):
    pid = project(client)
    jid = job(client, job_type="summarise", project_id=pid)
    r = claim(client, NUC, ["smoke_test", "classify", "organise", "summarise",
                            "question_generate", "deduplicate"])
    assert r.status_code == 200 and r.json()["job"]["id"] == jid


# ---------------------------------------------------------------------------
# Completion with structured results
# ---------------------------------------------------------------------------

RESULT = {
    "summary": "s",
    "findings": [{"finding": "Flow is 2 L/min at 1 bar.", "confidence": 0.6}],
    "evidence": [{"source_uri": "https://example.org/a", "title": "Spec",
                  "source_type": "datasheet"}],
}


def test_complete_stores_findings_and_evidence_atomically(client):
    pid = project(client)
    qid = question(client, pid, "What flow rate?")
    jid = client.post("/jobs", json={
        "project_id": pid, "question_id": qid,
        "job_type": "literature-collection"}).json()["id"]
    claim(client, APOLLO, ["literature-collection"])

    r = client.post(f"/jobs/{jid}/complete", json={"worker": APOLLO, "output": RESULT})
    assert r.status_code == 200 and r.json()["findings"] == 1
    assert sql("SELECT project_id, question_id, finding, confidence, status FROM findings") == [
        (pid, qid, "Flow is 2 L/min at 1 bar.", 0.6, "draft")]
    [(uri, meta)] = sql("SELECT source_uri, metadata FROM evidence")
    assert uri == "https://example.org/a" and meta["job_id"] == jid
    assert sql("SELECT status FROM jobs WHERE id = %s", jid) == [("completed",)]


@pytest.mark.parametrize("output", [
    {"findings": [{"finding": "f", "confidence": 1.5}]},
    {"evidence": [{"source_uri": "file:///etc/passwd"}]},
    {"findings": ["not an object"]},
])
def test_invalid_results_are_rejected_before_any_write(client, output):
    pid = project(client)
    jid = job(client, project_id=pid)
    claim(client, APOLLO, ["literature-collection"])
    r = client.post(f"/jobs/{jid}/complete", json={"worker": APOLLO, "output": output})
    assert r.status_code == 422
    assert sql("SELECT status FROM jobs WHERE id = %s", jid) == [("running",)]
    assert sql("SELECT count(*) FROM findings") == [(0,)]
    assert sql("SELECT count(*) FROM evidence") == [(0,)]


def test_non_owner_completion_writes_nothing(client):
    pid = project(client)
    jid = job(client, project_id=pid)
    claim(client, APOLLO, ["literature-collection"])
    r = client.post(f"/jobs/{jid}/complete", json={"worker": NUC, "output": RESULT})
    assert r.status_code == 409
    assert sql("SELECT count(*) FROM findings") == [(0,)]


def test_fail_requeues_until_three_attempts(client):
    pid = project(client)
    jid = job(client, project_id=pid)
    for attempt in (1, 2, 3):
        claimed = claim(client, APOLLO, ["literature-collection"]).json()["job"]
        assert claimed["attempts"] == attempt
        client.post(f"/jobs/{jid}/fail", json={"worker": APOLLO, "error": "x"})
    assert sql("SELECT status FROM jobs WHERE id = %s", jid) == [("failed",)]


# ---------------------------------------------------------------------------
# Backlog discovery
# ---------------------------------------------------------------------------


def test_discovery_enqueues_open_questions_in_active_projects_only(client):
    active = project(client, code="A")
    candidate = project(client, status="candidate", code="B")
    q_open = question(client, active, "open q", priority=0.9)
    question(client, active, "candidate q", status="candidate")
    question(client, active, "parked q", status="parked")
    question(client, candidate, "open q in candidate project")

    created = client.post("/backlog/discover", json={}).json()["created"]
    assert [(c["question_id"], c["unit_key"]) for c in created] == [
        (q_open, f"literature-collection:question:{q_open}")]
    [(priority, payload)] = sql(
        "SELECT priority, input FROM jobs WHERE job_type = 'literature-collection'")
    assert priority == 0.9
    assert payload["question"] == "open q" and payload["project_code"] == "A"
    assert "instructions" in payload and "output_format" in payload


def test_discovery_is_idempotent_bounded_and_progresses(client):
    pid = project(client)
    ids = [question(client, pid, f"q{i}", priority=p)
           for i, p in enumerate([0.2, 0.8, 0.5])]
    first = client.post("/backlog/discover", json={"limit": 2}).json()["created"]
    assert [c["question_id"] for c in first] == [ids[1], ids[2]]
    second = client.post("/backlog/discover", json={"limit": 2}).json()["created"]
    assert [c["question_id"] for c in second] == [ids[0]]
    assert client.post("/backlog/discover", json={}).json()["created"] == []
    assert sql("SELECT count(*) FROM jobs WHERE job_type = 'literature-collection'") == [(3,)]


def test_completed_or_failed_units_are_not_rediscovered(client):
    pid = project(client)
    question(client, pid, "q")
    [created] = client.post("/backlog/discover", json={}).json()["created"]
    sql("UPDATE jobs SET status = 'failed' WHERE id = %s", created["id"])
    assert client.post("/backlog/discover", json={}).json()["created"] == []


def test_discovered_jobs_are_claimable(client):
    pid = project(client)
    question(client, pid, "q")
    [created] = client.post("/backlog/discover", json={}).json()["created"]
    claimed = claim(client, APOLLO, ["literature-collection"]).json()["job"]
    assert claimed["id"] == created["id"]


def test_status_endpoints_validate(client):
    pid = project(client)
    qid = question(client, pid, "q", status="candidate")
    assert client.post(f"/questions/{qid}/status", json={"status": "bogus"}).status_code == 422
    assert client.post("/questions/999/status", json={"status": "open"}).status_code == 404
    assert client.post(f"/projects/{pid}/status", json={"status": "bogus"}).status_code == 422


# ---------------------------------------------------------------------------
# Autonomous research pipeline (rubric v1)
# ---------------------------------------------------------------------------

FOCUS_CATEGORIES = 14  # layers 1 and 2 of the needs taxonomy


def discover(client, limit=200):
    return client.post("/backlog/discover", json={"limit": limit}).json()


def run_job(client, job_type, output):
    """Claim the next job of a type as Apollo and complete it."""
    job = claim(client, APOLLO, [job_type]).json()["job"]
    assert job is not None, job_type
    response = client.post(f"/jobs/{job['id']}/complete",
                           json={"worker": APOLLO, "output": output})
    return job, response


def strong_assessment(**overrides):
    scores = {"need_severity_reach": 0.9, "effectiveness": 0.8, "ease": 0.8,
              "cost": 0.9, "practicality": 0.8, "evidence": 0.6}
    scores.update(overrides)
    return {
        "summary": "s",
        "assessment": {
            "scores": scores,
            "requirements": {
                "need": "Households lack safe water.",
                "baseline": "Boiling, costly chlorine.",
                "improvement": "Removes 99% of bacteria at low cost.",
                "demonstration": "unknown", "burden_removed": "unknown",
                "practical_independence": "unknown",
            },
            "rationale": "Field reports show sustained use in several regions.",
            "questions": ["What flow rate does the filter sustain over a year?"],
        },
        "evidence": [{"source_uri": "https://example.org/field-report"}],
    }


def candidates(*names):
    return {"summary": "s", "candidates": [
        {"name": n, "summary": f"{n} is an open design.",
         "source_uris": [f"https://example.org/{research_slug(n)}"]}
        for n in names]}


def research_slug(name):
    return name.lower().replace(" ", "-")


BRIEF = {
    "users": "Rural households without piped water, about 2 billion people.",
    "severity": "Unsafe water causes about 500,000 diarrhoeal deaths a year.",
    "current_practice": "Boiling with firewood or buying bottled water.",
    "requirements": ["Removes 99% of E. coli", "Treats 20 L per day"],
    "constraints": ["Under US$50 in materials"],
    "gaps": "Filters clog and spare parts are hard to find.",
}


def seed_briefs():
    """A need brief for every focus category, so discovery can start."""
    from psycopg.types.json import Jsonb

    sql("INSERT INTO need_briefs (category_id, brief) "
        "SELECT id, %s FROM need_categories WHERE layer <= 2", Jsonb(BRIEF))


def test_briefs_come_first_and_gate_discovery(client):
    first = discover(client)
    assert len(first["briefs"]) == FOCUS_CATEGORIES and first["discovery"] == []
    assert discover(client)["briefs"] == []  # one brief job per category per period
    job, r = run_job(client, "need-brief", {"summary": "s", "brief": BRIEF,
                                            "evidence": [{"source_uri": "https://who.int/x"}]})
    assert r.status_code == 200, r.text
    assert job["input"]["category"] == "Air and breathing"
    assert "instructions" in job["input"] and "output_format" in job["input"]
    [(stored,)] = sql("SELECT brief FROM need_briefs")
    assert stored["requirements"] == BRIEF["requirements"]

    [discovery] = discover(client)["discovery"]  # only the briefed category
    assert discovery["unit_key"].startswith(f"candidate-discovery:category:{job['input']['category_id']}:")
    [(payload,)] = sql("SELECT input FROM jobs WHERE job_type = 'candidate-discovery'")
    assert payload["need_brief"] == stored and "need brief" in payload["instructions"]
    assert [b["name"] for b in client.get("/briefs").json()] == ["Air and breathing"]


def test_invalid_brief_writes_nothing(client):
    discover(client)
    _, r = run_job(client, "need-brief", {"summary": "s", "brief": {**BRIEF, "requirements": []}})
    assert r.status_code == 422
    assert sql("SELECT count(*) FROM need_briefs") == [(0,)]


def test_discovery_scans_each_focus_category_once_per_period(client):
    seed_briefs()
    first = discover(client)["discovery"]
    assert len(first) == FOCUS_CATEGORIES
    assert discover(client)["discovery"] == []
    [(payload,)] = sql(
        "SELECT input FROM jobs WHERE job_type = 'candidate-discovery' "
        "ORDER BY id LIMIT 1")
    assert payload["category"] == "Air and breathing" and payload["layer"] == 1
    assert "instructions" in payload and payload["already_known"] == []
    assert sql("SELECT count(*) FROM jobs j JOIN need_categories c "
               "ON c.id = (j.input->>'category_id')::bigint WHERE c.layer > 2") == [(0,)]


def test_discovery_results_become_deduplicated_candidates(client):
    seed_briefs()
    discover(client, limit=1)
    job, r = run_job(client, "candidate-discovery",
                     candidates("Slow Sand Filter", "slow sand filter", "Ceramic Pot Filter"))
    assert r.status_code == 200 and r.json()["candidates"] == 2
    rows = sql("SELECT name, status, maslow_level, category_id, discovered_by_job "
               "FROM projects ORDER BY id")
    assert [(n, s) for n, s, *_ in rows] == [
        ("Slow Sand Filter", "candidate"), ("Ceramic Pot Filter", "candidate")]
    assert all(row[3] == job["input"]["category_id"] and row[4] == job["id"] for row in rows)


def test_assessment_admission_and_research_run_autonomously(client):
    seed_briefs()
    discover(client, limit=1)
    run_job(client, "candidate-discovery", candidates("Slow Sand Filter"))
    assert len(discover(client)["assessment"]) == 1
    _, r = run_job(client, "candidate-assessment", strong_assessment())
    assert r.status_code == 200 and r.json()["questions"] == 1
    composite = r.json()["composite"]
    assert composite >= 0.55

    step = discover(client)
    [admitted] = step["admitted"]
    assert admitted["composite"] == composite
    [(status, score)] = sql("SELECT status, score FROM projects")
    assert status == "active" and score == composite
    [(decision, author, revision)] = sql(
        "SELECT decision, author, policy_revision FROM decisions")
    assert decision == "admit" and "rubric v1" in author and "rubric v1" in revision
    # The admitted project's question was opened and its research queued.
    [created] = step["created"]
    assert sql("SELECT status FROM questions") == [("open",)]
    assert created["unit_key"].startswith("literature-collection:question:")


@pytest.mark.parametrize("change", [
    {"scores": {"cost": 0.1, "ease": 0.1, "practicality": 0.1, "effectiveness": 0.1}},
    {"scores": {"evidence": 0.1}},
    {"requirement": "need"},
])
def test_weak_cases_are_not_admitted(client, change):
    seed_briefs()
    discover(client, limit=1)
    run_job(client, "candidate-discovery", candidates("Slow Sand Filter"))
    discover(client)
    output = strong_assessment(**change.get("scores", {}))
    if "requirement" in change:
        output["assessment"]["requirements"][change["requirement"]] = "unknown"
    run_job(client, "candidate-assessment", output)
    assert discover(client)["admitted"] == []
    assert sql("SELECT status FROM projects") == [("candidate",)]


def test_admission_respects_the_active_project_limit(client, monkeypatch):
    monkeypatch.setenv("MAX_ACTIVE_PROJECTS", "1")
    seed_briefs()
    discover(client, limit=1)
    run_job(client, "candidate-discovery", candidates("Filter A", "Filter B"))
    discover(client)
    run_job(client, "candidate-assessment", strong_assessment(cost=0.6))
    run_job(client, "candidate-assessment", strong_assessment(cost=1.0))
    [admitted] = discover(client)["admitted"]
    assert sql("SELECT name FROM projects WHERE status = 'active'") == [("Filter B",)]
    assert discover(client)["admitted"] == []


@pytest.mark.parametrize("job_type, output", [
    ("candidate-discovery", {"candidates": [{"name": "X", "summary": "s",
                                             "source_uris": []}]}),
    ("candidate-discovery", {"candidates": [{"name": "Valid name", "summary": "long enough",
                                             "source_uris": ["ftp://x"]}]}),
    ("candidate-assessment", {"assessment": {"scores": {"cost": 2}, "requirements": {},
                                             "rationale": "r"}}),
])
def test_invalid_pipeline_results_write_nothing(client, job_type, output):
    seed_briefs()
    discover(client, limit=1)
    if job_type == "candidate-assessment":
        run_job(client, "candidate-discovery", candidates("Slow Sand Filter"))
        discover(client)
    before = sql("SELECT count(*) FROM projects")
    job, r = run_job(client, job_type, output)
    assert r.status_code == 422
    assert sql("SELECT count(*) FROM projects") == before
    assert sql("SELECT count(*) FROM assessments") == [(0,)]
    assert sql("SELECT status FROM jobs WHERE id = %s", job["id"]) == [("running",)]


def test_composite_scoring():
    import research

    top = {d: 1.0 for d in research.ASSESSED_DIMENSIONS}
    assert research.composite_score(1, top) == 1.0
    assert research.composite_score(1, {**top, "evidence": 0.0}) == 0.5
    assert research.composite_score(5, top) < research.composite_score(1, top)


def test_portfolio_ranks_projects(client):
    seed_briefs()
    discover(client, limit=1)
    run_job(client, "candidate-discovery", candidates("Filter A", "Filter B"))
    discover(client)
    run_job(client, "candidate-assessment", strong_assessment(evidence=0.1))
    run_job(client, "candidate-assessment", strong_assessment())
    discover(client)
    body = client.get("/portfolio").json()
    assert [p["name"] for p in body["projects"]][:1] == ["Filter B"]
    assert body["projects"][0]["status"] == "active"
    assert body["decisions"][0]["decision"] == "admit"


def test_summary_counts_the_pipeline_and_the_queue(client):
    empty = client.get("/summary").json()
    assert empty["pipeline"]["candidates"] == 0
    assert empty["pipeline"]["need_categories"] == 20

    seed_briefs()
    discover(client, limit=1)
    run_job(client, "candidate-discovery", candidates("Filter A", "Filter B"))
    discover(client)
    run_job(client, "candidate-assessment", strong_assessment())
    discover(client)
    body = client.get("/summary").json()
    pipeline = body["pipeline"]
    assert pipeline["categories_with_candidates"] == 1
    assert pipeline["candidates"] == 2
    assert pipeline["assessed"] == 1
    assert pipeline["admitted"] == 1
    assert pipeline["questions"] >= 1
    assert body["projects_by_status"]["active"] == 1
    assert body["decisions"]["admit"] == 1
    assert body["jobs"]["candidate-discovery"]["completed"] == 1
    assert body["jobs_completed_24h"] == 2
    assert body["jobs_failed_24h"] == 0


# ---------------------------------------------------------------------------
# Go/no-go review, build packs, steering and design archives
# ---------------------------------------------------------------------------

FINDINGS = {"summary": "s",
            "findings": [{"finding": "Sustains 50 L/h for a year", "confidence": 0.7}],
            "evidence": [{"source_uri": "https://example.org/flow-study"}]}

BUILD = {
    "bom": {"items": [{"part": "Washed sand", "quantity": "50 kg", "unit_cost": 0.2,
                       "currency": "USD", "source_uri": "https://example.org/sand"}],
            "total_cost": 10, "currency": "USD", "cost_basis": "US retail, 2026"},
    "design": {"repositories": [{"url": "https://github.com/example/filter",
                                 "commit": "abc123", "licence": "CERN-OHL-S-2.0"}],
               "files": [{"name": "Drawing", "kind": "drawing",
                          "uri": "https://example.org/drawing.pdf", "format": "PDF",
                          "licence": "CC-BY-4.0"}]},
    "assembly": {"tools": ["Shovel"], "steps": [{"step": "Wash the sand"}]},
    "test": {"acceptance": [{"criterion": "Flow", "method": "Timed fill",
                             "target": ">= 40 L/h"}]},
}


def admitted_project(client, questions=("What flow rate does the filter sustain?",)):
    """Run the pipeline up to an admitted project with open questions."""
    seed_briefs()
    discover(client, limit=1)
    run_job(client, "candidate-discovery", candidates("Slow Sand Filter"))
    discover(client)
    output = strong_assessment()
    output["assessment"]["questions"] = list(questions)
    run_job(client, "candidate-assessment", output)
    assert len(discover(client)["admitted"]) == 1
    [(pid,)] = sql("SELECT id FROM projects")
    return pid


def review_output(decision="build", questions=(), **scores):
    values = {"need_severity_reach": 0.9, "effectiveness": 0.8, "ease": 0.8,
              "cost": 0.9, "practicality": 0.8, "evidence": 0.7}
    values.update(scores)
    return {"summary": "s", "review": {
        "decision": decision, "scores": values, "fit": "Meets the flow requirement.",
        "rationale": "Field data supports documenting a full build.",
        "gaps": ["No enclosure drawing"], "questions": list(questions)}}


def run_build_section(client):
    job = claim(client, APOLLO, ["build-pack"]).json()["job"]
    section = job["input"]["section"]
    r = client.post(f"/jobs/{job['id']}/complete", json={"worker": APOLLO, "output": {
        "summary": "s", "build": {**BUILD[section], "gaps": [f"{section} gap"]}}})
    assert r.status_code == 200, r.text
    return section


def test_research_answers_questions_then_a_review_is_queued(client):
    pid = admitted_project(client, questions=("Question one about flow?", "Question two on cost?"))
    run_job(client, "literature-collection", FINDINGS)
    assert sorted(s for (s,) in sql("SELECT status FROM questions")) == ["answered", "open"]
    assert discover(client)["reviews"] == []  # one question still open
    run_job(client, "literature-collection", FINDINGS)
    [review] = discover(client)["reviews"]
    assert review["unit_key"] == f"project-review:project:{pid}:1"
    [(payload,)] = sql("SELECT input FROM jobs WHERE job_type = 'project-review'")
    assert payload["need_brief"] == BRIEF and payload["review_number"] == 1
    assert payload["findings"][0]["finding"] == "Sustains 50 L/h for a year"
    assert "https://example.org/flow-study" in payload["sources"]
    assert discover(client)["reviews"] == []  # idempotent


def test_failed_research_does_not_block_the_review(client):
    admitted_project(client)
    sql("UPDATE jobs SET status = 'failed' WHERE job_type = 'literature-collection'")
    assert len(discover(client)["reviews"]) == 1


def test_build_decision_runs_a_build_pack_to_completion(client):
    pid = admitted_project(client)
    run_job(client, "literature-collection", FINDINGS)
    discover(client)
    _, r = run_job(client, "project-review", review_output("build"))
    assert r.json()["outcome"] == "build", r.text
    builds = discover(client)["builds"]
    assert sorted(b["unit_key"].rsplit(":", 1)[1] for b in builds) == sorted(BUILD)
    assert client.get("/catalogue").json()["projects"][0]["stage"] == "build pack"
    for _ in BUILD:
        run_build_section(client)
    [done] = discover(client)["completed"]
    assert done == {"project_id": pid, "failed_sections": 0}
    record = client.get(f"/projects/{pid}/record").json()
    assert record["project"]["stage"] == "built" and record["project"]["status"] == "completed"
    assert record["build"]["bom"]["items"][0]["part"] == "Washed sand"
    assert record["build"]["test"]["gaps"] == ["test gap"]
    assert [d["decision"] for d in record["decisions"]] == ["admit", "build", "complete"]
    assert record["reviews"][0]["outcome"] == "build"
    assert record["questions"][0]["status"] == "answered"
    assert record["questions"][0]["findings"][0]["confidence"] == 0.7
    assert record["brief"] == BRIEF


def test_weak_build_recommendation_continues_with_new_questions(client):
    pid = admitted_project(client)
    run_job(client, "literature-collection", FINDINGS)
    discover(client)
    _, r = run_job(client, "project-review", review_output(
        "build", questions=["What is the flow after six months?"], evidence=0.2))
    assert r.json()["outcome"] == "continue"
    step = discover(client)
    assert step["builds"] == [] and len(step["created"]) == 1  # the new question's research
    run_job(client, "literature-collection", FINDINGS)
    [review] = discover(client)["reviews"]
    assert review["unit_key"] == f"project-review:project:{pid}:2"


def test_park_decision_parks_the_project(client):
    pid = admitted_project(client)
    run_job(client, "literature-collection", FINDINGS)
    discover(client)
    _, r = run_job(client, "project-review", review_output("park"))
    assert r.json()["outcome"] == "park"
    assert sql("SELECT status FROM projects WHERE id = %s", pid) == [("parked",)]
    assert client.get("/catalogue").json()["projects"][0]["stage"] == "parked"


def test_review_outcome_rules():
    import research

    review = research.Review(**review_output("build")["review"])
    assert research.review_outcome(1, review, 1)[0] == "build"
    weak = research.Review(**review_output("build", evidence=0.2)["review"])
    assert research.review_outcome(1, weak, 1)[0] == "park"  # weak and no questions
    asking = research.Review(**review_output("continue", questions=["Is the flow stable?"])["review"])
    assert research.review_outcome(1, asking, 1)[0] == "continue"
    assert research.review_outcome(1, asking, research.MAX_REVIEWS)[0] == "park"
    parked = research.Review(**review_output("park")["review"])
    assert research.review_outcome(1, parked, 1)[0] == "park"


@pytest.mark.parametrize("section, body", [
    ("bom", {"items": []}),
    ("design", {"repositories": [{"url": "http://insecure.example/repo"}]}),
    ("assembly", {"tools": ["x"]}),
])
def test_invalid_build_sections_write_nothing(client, section, body):
    pid = project(client)
    job_id = client.post("/jobs", json={"project_id": pid, "job_type": "build-pack",
                                        "input": {"section": section}}).json()["id"]
    claim(client, APOLLO, ["build-pack"])
    r = client.post(f"/jobs/{job_id}/complete", json={
        "worker": APOLLO, "output": {"summary": "s", "build": body}})
    assert r.status_code == 422
    assert sql("SELECT count(*) FROM build_packs") == [(0,)]


def test_steer_build_and_park(client):
    seed_briefs()
    discover(client, limit=1)
    run_job(client, "candidate-discovery", candidates("Filter A", "Filter B"))
    [(a,), (b,)] = sql("SELECT id FROM projects ORDER BY id")
    r = client.post(f"/projects/{a}/steer", json={"steer": "build", "note": "I want this"})
    assert r.status_code == 200 and r.json()["steer"] == "build"
    client.post(f"/projects/{b}/steer", json={"steer": "park"})
    step = discover(client)
    assert sorted(s["steer"] for s in step["steered"]) == ["build", "park"]
    assert len(step["builds"]) == len(BUILD)  # straight to the build pack
    assert sql("SELECT id, status FROM projects ORDER BY id") == [(a, "active"), (b, "parked")]
    [(author, rationale)] = sql(
        "SELECT author, rationale FROM decisions WHERE project_id = %s", a)
    assert author == "maintainer (steer)" and "I want this" in rationale
    assert discover(client)["steered"] == []  # applied once
    assert client.post("/projects/999/steer", json={"steer": "park"}).status_code == 404
    assert client.post(f"/projects/{a}/steer", json={"steer": "maybe"}).status_code == 422


def test_design_sources_are_listed_for_archiving_once(client):
    from psycopg.types.json import Jsonb

    pid = project(client)
    sql("INSERT INTO build_packs (project_id, section, content) VALUES (%s, 'design', %s)",
        pid, Jsonb(BUILD["design"]))
    pending = client.get("/archives/pending").json()
    assert [(p["kind"], p["source_uri"], p["revision"]) for p in pending] == [
        ("git", "https://github.com/example/filter", "abc123"),
        ("file", "https://example.org/drawing.pdf", "")]
    r = client.post("/archives", json={
        "project_id": pid, "source_uri": "https://github.com/example/filter", "kind": "git",
        "revision": "abc123", "resolved_revision": "abc123" + "0" * 34,
        "licence": "CERN-OHL", "status": "archived", "path": "/x.tar.gz", "bytes": 10,
        "sha256": "a" * 64})
    assert r.status_code == 200, r.text
    client.post("/archives", json={
        "project_id": pid, "source_uri": "https://example.org/drawing.pdf", "kind": "file",
        "status": "failed", "note": "timeout"})
    assert client.get("/archives/pending").json() == []
    sql("UPDATE design_archives SET created_at = NOW() - INTERVAL '2 days' "
        "WHERE status = 'failed'")
    assert [p["kind"] for p in client.get("/archives/pending").json()] == ["file"]  # retried
    record = client.get(f"/projects/{pid}/record").json()
    assert {a["status"] for a in record["archives"]} == {"archived", "failed"}


def test_build_instructions_cover_software_projects():
    import research

    project = {"name": "Open Food Facts", "code": "c3-off", "category": "Food",
               "summary": "An open food database.", "sources": []}
    for section in research.BUILD_SECTIONS:
        assert "software or data" in research.build_input(project, section)["instructions"]
