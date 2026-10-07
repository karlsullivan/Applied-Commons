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
            "TRUNCATE jobs, findings, evidence, questions, projects, sites, modules, "
            "systems, standards, roadmaps RESTART IDENTITY CASCADE"
        )
        conn.execute("UPDATE need_categories SET solved_at = NULL, solved_note = NULL")
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

FOCUS_CATEGORIES = 26  # layers 1 and 2 of the needs taxonomy, plus 12 enablers


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
        "SELECT id, %s FROM need_categories WHERE layer <= 2 OR track = 'enabler'", Jsonb(BRIEF))


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
    assert empty["pipeline"]["need_categories"] == 32

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


# ---------------------------------------------------------------------------
# Enablers (policy section 2a)
# ---------------------------------------------------------------------------


def complete(client, job, output):
    r = client.post(f"/jobs/{job['id']}/complete", json={"worker": APOLLO, "output": output})
    assert r.status_code == 200, r.text
    return r


def enabler_candidate(client, name="Solar Water Pump"):
    """Discover one enabler candidate (enabler discovery queues first)."""
    seed_briefs()
    discover(client)
    job = claim(client, APOLLO, ["candidate-discovery"]).json()["job"]
    assert job["input"]["track"] == "enabler"
    assert "essential human needs" in job["input"]["instructions"]
    complete(client, job, candidates(name))
    return job


def test_enablers_are_screened_on_the_needs_they_serve(client):
    import research

    enabler_candidate(client)
    assert sql("SELECT maslow_level FROM projects") == [(None,)]
    discover(client)
    job = claim(client, APOLLO, ["candidate-assessment"]).json()["job"]
    assert job["priority"] == research.ENABLER_ASSESSMENT_PRIORITY
    assert job["input"]["track"] == "enabler" and len(job["input"]["serves_options"]) == 14
    assert '"serves"' in job["input"]["output_format"]
    output = strong_assessment()
    output["assessment"]["serves"] = ["Physical and mental health", "Water", "Not a category"]
    r = complete(client, job, output)
    [(level, serves, score)] = sql("SELECT maslow_level, serves, score FROM projects")
    assert level == 1  # Water is layer 1, the most basic it serves
    assert serves == ["Physical and mental health", "Water"]
    assert score == r.json()["composite"] == research.composite_score(
        1, output["assessment"]["scores"])
    row = client.get("/catalogue").json()["projects"][0]
    assert (row["track"], row["layer"], row["serves"]) == ("enabler", 1, serves)


@pytest.mark.parametrize("serves", [[], ["Not a category"]])
def test_an_enabler_screening_must_say_what_it_serves(client, serves):
    enabler_candidate(client)
    discover(client)
    output = strong_assessment()
    output["assessment"]["serves"] = serves
    job = claim(client, APOLLO, ["candidate-assessment"]).json()["job"]
    r = client.post(f"/jobs/{job['id']}/complete", json={"worker": APOLLO, "output": output})
    assert r.status_code == 422
    assert sql("SELECT count(*) FROM assessments") == [(0,)]


def test_a_need_screening_ignores_serves(client):
    seed_briefs()
    discover(client, limit=1)
    run_job(client, "candidate-discovery", candidates("Slow Sand Filter"))
    discover(client)
    output = strong_assessment()
    output["assessment"]["serves"] = ["Water"]
    jobs = [claim(client, APOLLO, ["candidate-assessment"]).json()["job"]]
    assert "track" not in jobs[0]["input"]
    complete(client, jobs[0], output)
    assert sql("SELECT maslow_level, serves FROM projects") == [(1, [])]


def test_enablers_get_a_slight_admission_bump(client, monkeypatch):
    monkeypatch.setenv("MAX_ACTIVE_PROJECTS", "1")
    enabler_candidate(client, "Solar Water Pump")
    while True:  # the other enabler discoveries queue ahead of the needs
        need_job = claim(client, APOLLO, ["candidate-discovery"]).json()["job"]
        if "track" not in need_job["input"]:
            break
        complete(client, need_job, {"summary": "s", "candidates": []})
    complete(client, need_job, candidates("Rope Pump"))
    discover(client)
    for _ in range(2):
        job = claim(client, APOLLO, ["candidate-assessment"]).json()["job"]
        if job["input"].get("track") == "enabler":
            output = strong_assessment(cost=0.8)
            output["assessment"]["serves"] = ["Water"]
        else:
            output = strong_assessment(cost=0.9)
        complete(client, job, output)
    scores = dict(sql("SELECT name, score FROM projects"))
    assert scores["Rope Pump"] > scores["Solar Water Pump"]  # recorded honestly
    assert len(discover(client)["admitted"]) == 1
    assert sql("SELECT name FROM projects WHERE status = 'active'") == [("Solar Water Pump",)]


def test_the_briefly_deployed_sensing_category_is_removed(client):
    sql("INSERT INTO need_categories (name, scope, track) "
        "VALUES ('Sensing and monitoring', 'x', 'enabler')")
    discover(client)  # queues its brief
    assert sql("SELECT count(*) FROM jobs j JOIN need_categories c "
               "ON j.input->>'category_id' = c.id::text "
               "WHERE c.name = 'Sensing and monitoring'") == [(1,)]
    sql((ROOT / "database" / "migrations" / "008_drop_sensing.sql").read_text())
    assert sql("SELECT count(*) FROM need_categories "
               "WHERE name = 'Sensing and monitoring'") == [(0,)]
    assert sql("SELECT count(*) FROM jobs WHERE job_type = 'need-brief'") == [(FOCUS_CATEGORIES,)]


# ---------------------------------------------------------------------------
# Climates (policy section 2b)
# ---------------------------------------------------------------------------


def test_cold_climates_are_asked_for_and_recorded(client):
    discover(client)
    [(brief,)] = sql("SELECT input FROM jobs WHERE job_type = 'need-brief' ORDER BY id LIMIT 1")
    assert "Scandinavia" in brief["instructions"]
    sql("DELETE FROM jobs")
    pid = admitted_project(client)
    [(discovery,)] = sql("SELECT input FROM jobs WHERE job_type = 'candidate-discovery' "
                         "ORDER BY id LIMIT 1")
    assert "at least 2 that work year-round in cold climates" in discovery["instructions"]
    # The screening's climates: unknown groups dropped, CLIMATES order.
    assert sql("SELECT climates FROM projects") == [([],)]
    run_job(client, "literature-collection", FINDINGS)
    discover(client)
    output = review_output("build")
    output["review"]["climates"] = ["Cold", "temperate", "lunar"]
    run_job(client, "project-review", output)
    assert client.get("/catalogue").json()["projects"][0]["climates"] == ["temperate", "cold"]
    assert client.get(f"/projects/{pid}/record").json()["project"]["climates"] == [
        "temperate", "cold"]


def test_screening_records_climates(client):
    seed_briefs()
    discover(client, limit=1)
    run_job(client, "candidate-discovery", candidates("Slow Sand Filter"))
    discover(client)
    job = claim(client, APOLLO, ["candidate-assessment"]).json()["job"]
    assert "Alaska" in job["input"]["instructions"] and '"climates"' in job["input"]["output_format"]
    output = strong_assessment()
    output["assessment"]["climates"] = ["polar", "tropical"]
    complete(client, job, output)
    assert sql("SELECT climates FROM projects") == [(["tropical", "polar"],)]


# ---------------------------------------------------------------------------
# Stage 2: the modular set (policy section 4e)
# ---------------------------------------------------------------------------


def screened(n, category="Water", prefix="P"):
    """n assessed projects in a category, inserted directly."""
    from psycopg.types.json import Jsonb

    [(cid,)] = sql("SELECT id FROM need_categories WHERE name = %s", category)
    for i in range(n):
        [(pid,)] = sql(
            "INSERT INTO projects (code, name, maslow_level, domain, status, category_id, "
            "summary, score) VALUES (%s, %s, 1, %s, 'candidate', %s, 'An open design.', 0.6) "
            "RETURNING id", f"{prefix.lower()}-{category[:5].lower()}-{i}",
            f"{prefix} {category} {i}", category, cid)
        sql("INSERT INTO assessments (project_id, rubric_version, scores, composite, "
            "requirements, rationale) VALUES (%s, 'rubric-v1', %s, 0.6, %s, 'ok')",
            pid, Jsonb({"evidence": 0.5}), Jsonb({}))
    return cid


STANDARD = {"code": "DC Bus 24V", "name": "24 V DC bus", "kind": "Electrical",
            "spec": "24 V nominal DC, XT60 connectors, 20 A fuse per branch.",
            "rationale": "Cheap, safe extra-low voltage; parts sold worldwide."}


def module_out(code="solar-pump", standard="dc-bus-24v", projects=("P Water 0",)):
    return {"summary": "s", "modules": [{
        "code": code, "name": "Solar pump stage", "kind": "Hardware",
        "purpose": "Lifts water from a well using DC power.",
        "interfaces": [{"standard": standard, "role": "consumes"}],
        "bom": [{"part": "DC pump", "quantity": 1, "unit_cost_eu": 80,
                 "unit_cost_low_income": 45}],
        "cost_eu": 120, "cost_low_income": 70, "maturity": "tested",
        "source_projects": list(projects),
        "new_standards": [{"code": "pipe-32", "name": "32 mm pipe", "kind": "fluid",
                           "spec": "32 mm OD HDPE, compression fittings.",
                           "rationale": "Common worldwide."}]}]}


def system_out():
    return {"summary": "s", "systems": [{
        "code": "water", "name": "Household water", "purpose": "Safe water for a family.",
        "modules": [{"module": "solar-pump", "quantity": 1}],
        "calculations": [{"name": "Daily demand", "result": "120 L/day",
                          "code": "print(6 * 20)", "label": "calculated"}],
        "cost_eu": 300, "cost_low_income": 150, "missing_modules": ["chlorine doser"]}]}


def roadmap_out():
    return {"summary": "s", "roadmap": {
        "summary": "Start with power, then water, then trials.",
        "steps": [{"title": "Settle the DC bus", "kind": "standard", "refs": ["dc-bus-24v"]},
                  {"title": "Trial the pump in summer", "kind": "trial",
                   "refs": ["solar-pump"], "depends_on": [1], "season": "summer",
                   "target_climates": ["Temperate", "moon"], "cost_eu": 200}]}}


def verify_standards():
    """Mark every standard's current spec verified (as a verification would)."""
    sql("UPDATE standards SET verified_hash = "
        "left(encode(sha256(convert_to(spec, 'UTF8')), 'hex'), 16)")
    sql("UPDATE jobs SET status = 'completed' WHERE job_type = 'standard-verification'")


def approve_first_standard(client):
    verify_standards()
    sid = client.get("/standards").json()[0]["id"]
    r = client.post(f"/standards/{sid}/decision", json={"decision": "approved", "note": "ok"})
    assert r.json()["status"] == "approved"


def test_standards_start_once_enough_projects_are_screened(client):
    import modular

    screened(9)
    assert discover(client)["modular"] == []
    screened(1, prefix="Q")
    [job] = discover(client)["modular"]
    assert job["unit_key"] == f"standards-synthesis:{modular.month()}"
    assert discover(client)["modular"] == []  # once a month
    [(payload,)] = sql("SELECT input FROM jobs WHERE job_type = 'standards-synthesis'")
    assert len(payload["projects"]) == 10 and "Global Village" in payload["instructions"]


def test_modules_wait_for_an_approved_standard(client):
    screened(10)
    discover(client)
    _, r = run_job(client, "standards-synthesis", {"summary": "s", "standards": [
        STANDARD, {**STANDARD, "code": "lora-node", "name": "LoRa sensor node"}]})
    assert r.status_code == 200, r.text
    step = discover(client)["modular"]  # verifications only: nothing approved yet
    assert {j["job_type"] for j in step} == {"standard-verification"} and len(step) == 2
    standards = client.get("/standards").json()
    assert {s["code"]: s["status"] for s in standards} == {
        "dc-bus-24v": "proposed", "lora-node": "proposed"}
    assert standards[0]["kind"] == "electrical"
    approve_first_standard(client)
    [job] = discover(client)["modular"]
    assert job["unit_key"].startswith("module-synthesis:category:")
    [(payload,)] = sql("SELECT input FROM jobs WHERE job_type = 'module-synthesis'")
    assert [s["code"] for s in payload["approved_standards"]] == ["dc-bus-24v"]
    assert payload["domain"] == "Water" and len(payload["projects"]) == 10


def test_a_decided_standard_is_not_overwritten(client):
    screened(10)
    discover(client)
    run_job(client, "standards-synthesis", {"summary": "s", "standards": [STANDARD]})
    approve_first_standard(client)
    job_id = client.post("/jobs", json={"project_id": project(client, code="JOB-HOLDER"), "job_type": "standards-synthesis",
                                        "input": {}}).json()["id"]
    claim(client, APOLLO, ["standards-synthesis"])
    r = client.post(f"/jobs/{job_id}/complete", json={"worker": APOLLO, "output": {
        "summary": "s", "standards": [{**STANDARD, "spec": "48 V instead, a new idea."}]}})
    assert r.status_code == 200, r.text
    [standard] = client.get("/standards").json()
    assert standard["status"] == "approved" and standard["spec"].startswith("24 V")


def test_modules_are_upserted_and_keep_trial_maturity(client):
    screened(10)
    discover(client)
    run_job(client, "standards-synthesis", {"summary": "s", "standards": [STANDARD]})
    approve_first_standard(client)
    discover(client)
    _, r = run_job(client, "module-synthesis", module_out())
    assert r.status_code == 200, r.text
    [module] = client.get("/modules").json()
    assert module["maturity"] == "concept"  # a model cannot claim "tested"
    assert module["standards"] == ["dc-bus-24v"] and len(module["projects"]) == 1
    assert module["cost_low_income"] == 70 and module["spec"]["domains"] == ["Water"]
    assert {s["code"] for s in client.get("/standards").json()} == {"dc-bus-24v", "pipe-32"}
    # Trials later mark it tested; a re-synthesis keeps that and merges projects.
    sql("UPDATE modules SET maturity = 'tested'")
    [(cid,)] = sql("SELECT id FROM need_categories WHERE name = 'Water'")
    job_id = client.post("/jobs", json={
        "project_id": project(client, code="JOB-HOLDER"), "job_type": "module-synthesis",
        "input": {"category_id": cid, "domain": "Water"}}).json()["id"]
    claim(client, APOLLO, ["module-synthesis"])
    r = client.post(f"/jobs/{job_id}/complete", json={
        "worker": APOLLO, "output": module_out(projects=("P Water 1",))})
    assert r.status_code == 200, r.text
    [module] = client.get("/modules").json()
    assert module["maturity"] == "tested" and len(module["projects"]) == 2


def test_systems_follow_the_module_wave_then_the_roadmap(client):
    from psycopg.types.json import Jsonb

    screened(10)
    discover(client)
    run_job(client, "standards-synthesis", {"summary": "s", "standards": [STANDARD]})
    approve_first_standard(client)
    discover(client)  # the module wave
    for i in range(5):
        sql("INSERT INTO modules (code, name, kind, domain, spec) VALUES (%s, %s, 'hardware', "
            "'Water', %s)", f"m{i}", f"Module {i}", Jsonb({"purpose": "x", "interfaces": []}))
    assert discover(client)["modular"] == []  # the month's module job is still queued
    run_job(client, "module-synthesis", module_out())
    systems = [j for j in discover(client)["modular"] if j["job_type"] == "system-design"]
    assert sorted(j["unit_key"].split(":")[1] for j in systems) == sorted(
        ["household", "homestead", "small-farm", "community-facility", "village"])
    _, r = run_job(client, "system-design", system_out())
    assert r.status_code == 200, r.text
    [system] = client.get("/systems").json()
    assert system["code"].endswith("-water") and system["spec"]["calculations"][0]["code"]
    assert discover(client)["modular"] == []  # four system jobs still queued
    sql("UPDATE jobs SET status = 'failed' WHERE job_type = 'system-design' AND status = 'queued'")
    client.post("/sites", json={"code": "proving-ground", "profile": {"well": "not potable"}})
    [roadmap_job] = discover(client)["modular"]
    [(payload,)] = sql("SELECT input FROM jobs WHERE job_type = 'roadmap-revision'")
    assert payload["proving_ground"] == [{"code": "proving-ground", "well": "not potable"}]
    assert payload["systems"][0]["missing_modules"] == ["chlorine doser"]
    _, r = run_job(client, "roadmap-revision", roadmap_out())
    assert r.status_code == 200, r.text
    [road] = client.get("/roadmaps").json()
    assert road["status"] == "proposed" and road["steps"][1]["target_climates"] == ["temperate"]
    assert road["steps"][1]["depends_on"] == [1]


def test_one_approved_roadmap_at_a_time(client):
    from psycopg.types.json import Jsonb

    for v in ("202610", "202611"):
        sql("INSERT INTO roadmaps (version, summary, steps) VALUES (%s, 'plan', %s)",
            v, Jsonb([]))
    first, second = sorted(client.get("/roadmaps").json(), key=lambda r: r["version"])
    client.post(f"/roadmaps/{first['id']}/decision", json={"decision": "approved"})
    client.post(f"/roadmaps/{second['id']}/decision", json={"decision": "approved"})
    assert {r["version"]: r["status"] for r in client.get("/roadmaps").json()} == {
        "202610": "superseded", "202611": "approved"}
    assert client.post("/roadmaps/999/decision", json={"decision": "approved"}).status_code == 404


@pytest.mark.parametrize("job_type, output", [
    ("standards-synthesis", {"standards": [{**STANDARD, "spec": "short"}]}),
    ("module-synthesis", {"modules": [{"code": "x", "name": "Pump", "purpose": "Pumps water."}]}),
    ("system-design", {"systems": [{**system_out()["systems"][0], "modules": []}]}),
    ("roadmap-revision", {"roadmap": {"summary": "too short", "steps": []}}),
])
def test_invalid_stage2_results_write_nothing(client, job_type, output):
    job_id = client.post("/jobs", json={"project_id": project(client, code="JOB-HOLDER"), "job_type": job_type,
                                        "input": {}}).json()["id"]
    claim(client, APOLLO, [job_type])
    r = client.post(f"/jobs/{job_id}/complete", json={"worker": APOLLO,
                                                      "output": {"summary": "s", **output}})
    assert r.status_code == 422, r.text
    for table in ("standards", "modules", "systems", "roadmaps"):
        assert sql(f"SELECT count(*) FROM {table}") == [(0,)]


VERIFIED = {"summary": "s", "verification": {
    "outcome": "deviates", "safety_critical": False,
    "references": [{"body": "IEC", "number": "IEC 60364-4-41", "title": "Protection for safety",
                    "url": "https://webstore.iec.ch/publication/1878"}],
    "deviations": [{"reference": "IEC 60364-4-41", "deviation": "No RCD on the DC bus.",
                    "risk": "Low", "justification": "Extra-low voltage."}],
    "required_controls": ["Fuse each branch."], "recommendation": "Approve",
    "rationale": "Extra-low voltage DC; consistent apart from the listed deviation."}}


def test_standards_are_verified_before_they_can_be_approved(client):
    import modular

    screened(10)
    discover(client)
    run_job(client, "standards-synthesis", {"summary": "s", "standards": [STANDARD]})
    [job] = discover(client)["modular"]
    [standard] = client.get("/standards").json()
    assert job["unit_key"] == (f"standard-verification:{standard['id']}:"
                               f"{modular.spec_hash(standard['spec'])}")
    assert job["priority"] == modular.VERIFY_PRIORITY and standard["verified"] is False
    r = client.post(f"/standards/{standard['id']}/decision", json={"decision": "approved"})
    assert r.status_code == 409
    claimed, r = run_job(client, "standard-verification", VERIFIED)
    assert r.status_code == 200 and r.json()["outcome"] == "deviates", r.text
    assert "AS/NZS" in claimed["input"]["instructions"]
    [standard] = client.get("/standards").json()
    assert standard["verified"] is True
    verification = standard["verification"]
    assert verification["deviations"][0]["risk"] == "low"
    assert verification["recommendation"] == "approve"
    assert discover(client)["modular"] == [] or all(
        j["job_type"] != "standard-verification" for j in discover(client)["modular"])
    r = client.post(f"/standards/{standard['id']}/decision", json={"decision": "approved"})
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_a_revised_proposal_is_verified_again(client):
    screened(10)
    discover(client)
    run_job(client, "standards-synthesis", {"summary": "s", "standards": [STANDARD]})
    discover(client)
    run_job(client, "standard-verification", VERIFIED)
    # A later proposal revises the still-proposed standard's spec.
    job_id = client.post("/jobs", json={"project_id": project(client, code="JOB-HOLDER"),
                                        "job_type": "standards-synthesis", "input": {}}).json()["id"]
    claim(client, APOLLO, ["standards-synthesis"])
    client.post(f"/jobs/{job_id}/complete", json={"worker": APOLLO, "output": {
        "summary": "s", "standards": [{**STANDARD, "spec": "48 V nominal DC, Anderson SB50."}]}})
    [standard] = client.get("/standards").json()
    assert standard["verified"] is False  # the earlier verification was of the old spec
    jobs = [j for j in discover(client)["modular"] if j["job_type"] == "standard-verification"]
    assert len(jobs) == 1
    # Rejecting needs no verification.
    r = client.post(f"/standards/{standard['id']}/decision", json={"decision": "rejected"})
    assert r.status_code == 200


@pytest.mark.parametrize("change", [
    {"outcome": "deviates", "deviations": []},
    {"outcome": "unsafe", "deviations": []},
    {"outcome": "maybe"},
    {"recommendation": "shrug"},
])
def test_verifications_must_call_out_deviations(client, change):
    job_id = client.post("/jobs", json={"project_id": project(client, code="JOB-HOLDER"),
                                        "job_type": "standard-verification",
                                        "input": {"standard_id": 1, "spec_hash": "x"}}).json()["id"]
    claim(client, APOLLO, ["standard-verification"])
    output = {"summary": "s", "verification": {**VERIFIED["verification"], **change}}
    r = client.post(f"/jobs/{job_id}/complete", json={"worker": APOLLO, "output": output})
    assert r.status_code == 422, r.text


def test_listed_deviations_make_an_aligned_verification_deviate():
    import modular

    v = modular.Verification(**{**VERIFIED["verification"], "outcome": "aligned"})
    assert v.outcome == "deviates"


def test_relations_run_monthly_and_as_the_catalogue_grows(client):
    import modular

    screened(9)
    assert discover(client)["relations"] == []
    screened(1, prefix="Q")
    [job] = discover(client)["relations"]
    assert job["unit_key"] == f"project-relations:{modular.month()}:0"
    assert discover(client)["relations"] == []
    [(payload,)] = sql("SELECT input FROM jobs WHERE job_type = 'project-relations'")
    assert len(payload["projects"]) == 10
    assert {"code", "name", "category", "summary"} <= set(payload["projects"][0])
    screened(15, prefix="R")  # 25 assessed: the next step
    [job] = discover(client)["relations"]
    assert job["unit_key"].endswith(":1")


def test_relations_are_stored_between_known_projects(client):
    screened(10)
    discover(client)
    out = {"summary": "s", "relations": [
        {"a": "p-water-1", "b": "p-water-0", "kind": "Uses", "why": "Cut on the router."},
        {"a": "p-water-3", "b": "p-water-2", "kind": "alternative", "why": "Same need."},
        {"a": "p-water-2", "b": "nope", "kind": "enables", "why": "Unknown project."},
        {"a": "p-water-4", "b": "p-water-4", "kind": "part-of", "why": "Itself."},
    ]}
    job, r = run_job(client, "project-relations", out)
    assert r.status_code == 200, r.text
    assert r.json()["relations"] == 2 and r.json()["skipped"] == 2
    rels = client.get("/relations").json()
    assert [(x["a_code"], x["b_code"], x["kind"]) for x in rels] == [
        ("p-water-1", "p-water-0", "uses"), ("p-water-2", "p-water-3", "alternative")]
    assert rels[0]["a_name"] == "P Water 1"
    # Found again: one row, the newer reason; the next job lists it as known.
    sql("UPDATE jobs SET unit_key = 'old' WHERE id = %s", job["id"])
    discover(client)
    [(payload,)] = sql("SELECT input FROM jobs WHERE job_type = 'project-relations' "
                       "AND status = 'queued'")
    assert {"a": "p-water-1", "b": "p-water-0", "kind": "uses"} in payload["existing_relations"]
    _, r = run_job(client, "project-relations", {"summary": "s", "relations": [
        {"a": "p-water-1", "b": "p-water-0", "kind": "uses", "why": "A newer reason."}]})
    rels = client.get("/relations").json()
    assert len(rels) == 2 and rels[0]["why"] == "A newer reason."


@pytest.mark.parametrize("relation", [
    {"a": "x", "b": "y", "kind": "loves", "why": "Not a kind."},
    {"a": "x", "b": "y", "kind": "uses", "why": ""},
])
def test_invalid_relations_write_nothing(client, relation):
    screened(10)
    discover(client)
    job, r = run_job(client, "project-relations", {"summary": "s", "relations": [relation]})
    assert r.status_code == 422
    assert sql("SELECT status FROM jobs WHERE id = %s", job["id"]) == [("running",)]


def test_categories_list_every_category_with_its_brief(client):
    from psycopg.types.json import Jsonb

    [(cid,)] = sql("SELECT id FROM need_categories WHERE name = 'Water'")
    sql("INSERT INTO need_briefs (category_id, brief) VALUES (%s, %s)", cid, Jsonb(BRIEF))
    cats = client.get("/categories").json()
    assert len(cats) == sql("SELECT count(*) FROM need_categories")[0][0]
    water = next(c for c in cats if c["name"] == "Water")
    assert water["focus"] and water["brief"]["users"] == BRIEF["users"]
    assert all(c["brief"] is None for c in cats if c["name"] != "Water")
    assert {c["focus"] for c in cats if c["layer"] and c["layer"] > 2} == {False}
    tracks = [c["track"] for c in cats]
    assert tracks == sorted(tracks, key=lambda t: t == "enabler")  # needs first
    assert all(c["focus"] for c in cats if c["track"] == "enabler")


def test_source_checks_are_due_recorded_and_shown(client):
    from psycopg.types.json import Jsonb

    screened(2)
    [(pid,)] = sql("SELECT id FROM projects WHERE code = 'p-water-0'")
    sql("UPDATE projects SET source_uris = %s WHERE id = %s",
        Jsonb(["https://github.com/o/r", "https://x.org"]), pid)
    sql("INSERT INTO build_packs (project_id, section, content) VALUES (%s, 'design', %s)",
        pid, Jsonb({"repositories": [{"url": "https://gitlab.com/g/r", "licence": "MIT"}]}))
    due = client.get("/checks/pending").json()
    assert len(due) == 2
    first = next(d for d in due if d["id"] == pid)
    assert first["urls"] == ["https://github.com/o/r", "https://x.org", "https://gitlab.com/g/r"]
    assert first["reported"] == [{"url": "https://gitlab.com/g/r", "licence": "MIT"}]

    r = client.post(f"/projects/{pid}/check", json={
        "links": [{"url": "https://x.org", "state": "broken", "status": 404}],
        "licence": "CERN-OHL-S-2.0", "licence_class": "share-alike",
        "licence_source": "GitHub (verified)", "repository": "https://github.com/o/r",
        "last_activity": "2026-05-01T00:00:00Z", "archived": False})
    assert r.status_code == 200, r.text
    assert pid not in [d["id"] for d in client.get("/checks/pending").json()]

    row = next(p for p in client.get("/catalogue").json()["projects"] if p["id"] == pid)
    assert (row["licence"], row["licence_class"], row["broken_links"]) == (
        "CERN-OHL-S-2.0", "share-alike", 1)
    other = next(p for p in client.get("/catalogue").json()["projects"] if p["id"] != pid)
    assert other["licence_class"] == "unchecked"
    record = client.get(f"/projects/{pid}/record").json()
    assert record["check"]["licence_source"] == "GitHub (verified)"
    # Module synthesis sees the class.
    import main
    with main.db() as conn, conn.cursor() as cur:
        digest = {d["name"]: d for d in main._projects_digest(cur)}
    assert digest["P Water 0"]["licence_class"] == "share-alike"
    # Due again after the period.
    sql("UPDATE source_checks SET checked_at = NOW() - INTERVAL '15 days'")
    assert pid in [d["id"] for d in client.get("/checks/pending").json()]


@pytest.mark.parametrize("body", [
    {"licence_class": "maybe"},
    {"licence_class": "open", "links": [{"url": "https://x.org", "state": "fine"}]},
])
def test_invalid_checks_are_refused(client, body):
    pid = project(client, code="CHK-1")
    assert client.post(f"/projects/{pid}/check", json=body).status_code == 422
    assert client.post("/projects/999999/check",
                       json={"licence_class": "open"}).status_code == 404


def water_progress_setup():
    """Water briefed, two screened projects: one with a complete build pack
    and an open licence, one without."""
    from psycopg.types.json import Jsonb

    cid = screened(2)
    sql("INSERT INTO need_briefs (category_id, brief) VALUES (%s, %s)", cid, Jsonb(BRIEF))
    sql("UPDATE projects SET status = 'completed' WHERE code = 'p-water-0'")
    [(pid,)] = sql("SELECT id FROM projects WHERE code = 'p-water-0'")
    sql("INSERT INTO source_checks (project_id, licence, licence_class) VALUES (%s, 'MIT', 'open')",
        pid)
    return cid


def progress_out(*items):
    return {"summary": "Close on filtration, far on volume.", "progress": list(items)}


def test_progress_is_tracked_when_what_meets_a_need_changes(client):
    cid = water_progress_setup()
    [job] = discover(client)["progress"]
    assert job["unit_key"].startswith(f"need-progress:{cid}:")
    [(payload,)] = sql("SELECT input FROM jobs WHERE job_type = 'need-progress'")
    assert [r["text"] for r in payload["requirements"]] == BRIEF["requirements"]
    complete_project = next(p for p in payload["projects"] if p["code"] == "p-water-0")
    assert complete_project["build_pack_complete"] and complete_project["licence_class"] == "open"
    assert discover(client)["progress"] == []  # unchanged
    sql("UPDATE jobs SET status = 'completed' WHERE job_type = 'need-progress'")
    screened(1, prefix="Q")  # changed, but within the week
    assert discover(client)["progress"] == []
    sql("UPDATE jobs SET created_at = NOW() - INTERVAL '8 days' WHERE job_type = 'need-progress'")
    assert len(discover(client)["progress"]) == 1


def test_progress_levels_are_capped_by_the_records(client):
    cid = water_progress_setup()
    discover(client)
    _, r = run_job(client, "need-progress", progress_out(
        {"requirement": 1, "level": "field-tested",
         "met_by": [{"code": "p-water-0", "how": "Lab test shows 99.9%."}]},
        {"requirement": 2, "level": "documented",
         "met_by": [{"code": "p-water-1", "how": "Claims 20 L/day."},
                    {"code": "made-up", "how": "?"}]},
        {"requirement": 9, "level": "designed", "met_by": []}))
    assert r.status_code == 200, r.text
    assert r.json()["requirements"] == 2 and r.json()["lowered"] == 2
    water = next(c for c in client.get("/categories").json() if c["id"] == cid)
    first, second = water["progress"]
    assert (first["level"], first["claimed"]) == ("documented", "field-tested")
    assert (second["level"], [m["code"] for m in second["met_by"]]) == ("candidate", ["p-water-1"])
    assert water["status"] == "candidates found"
    assert water["progress_summary"].startswith("Close on filtration")
    others = [c for c in client.get("/categories").json() if c["id"] != cid]
    assert {c["status"] for c in others} == {"researching"}


def test_only_the_maintainer_marks_a_need_solved_and_only_once_field_tested(client):
    from psycopg.types.json import Jsonb

    cid = water_progress_setup()
    discover(client)
    run_job(client, "need-progress", progress_out(
        {"requirement": 1, "level": "documented", "met_by": [{"code": "p-water-0"}]},
        {"requirement": 2, "level": "none"}))
    r = client.post(f"/categories/{cid}/solved", json={"solved": True})
    assert r.status_code == 409 and "field-tested" in r.json()["detail"]
    sql("INSERT INTO modules (code, name, kind, domain, maturity, spec) "
        "VALUES ('solar-filter', 'Solar filter', 'hardware', 'Water', 'tested', %s)",
        Jsonb({"purpose": "Filters water.", "domains": ["Water"]}))
    sql("UPDATE jobs SET created_at = NOW() - INTERVAL '8 days' WHERE job_type = 'need-progress'")
    discover(client)
    run_job(client, "need-progress", progress_out(
        {"requirement": 1, "level": "field-tested", "met_by": [{"code": "solar-filter"}]},
        {"requirement": 2, "level": "field-tested", "met_by": [{"code": "solar-filter"}]}))
    water = next(c for c in client.get("/categories").json() if c["id"] == cid)
    assert water["status"] == "field-tested"
    assert client.post(f"/categories/{cid}/solved",
                       json={"solved": True, "note": "Works all winter."}).status_code == 200
    water = next(c for c in client.get("/categories").json() if c["id"] == cid)
    assert water["status"] == "substantially solved" and water["solved_note"] == "Works all winter."
    client.post(f"/categories/{cid}/solved", json={"solved": False})
    water = next(c for c in client.get("/categories").json() if c["id"] == cid)
    assert water["status"] == "field-tested" and water["solved_at"] is None


def test_invalid_progress_writes_nothing(client):
    water_progress_setup()
    discover(client)
    job, r = run_job(client, "need-progress", progress_out(
        {"requirement": 1, "level": "nearly", "met_by": []}))
    assert r.status_code == 422
    assert sql("SELECT count(*) FROM need_progress") == [(0,)]
