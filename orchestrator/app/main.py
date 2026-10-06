import os
import time
from typing import Any

import research

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


DATABASE_URL = os.environ["DATABASE_URL"]

# Job types reserved for one claiming worker. Apollo executes AC research
# classes through Hermes; reserving them means no other worker (such as
# the NUC worker) can claim them, even after a lease expires. Override with
# JOB_TYPE_OWNERS="type=worker,type=worker" (an empty value disables it).
DEFAULT_JOB_TYPE_OWNERS = {
    "need-brief": "apollo-hermes",
    "candidate-discovery": "apollo-hermes",
    "project-review": "apollo-hermes",
    "build-pack": "apollo-hermes",
    "candidate-assessment": "apollo-hermes",
    "literature-collection": "apollo-hermes",
    "source-summarisation": "apollo-hermes",
    "evidence-extraction": "apollo-hermes",
}


def job_type_owners() -> dict[str, str]:
    raw = os.environ.get("JOB_TYPE_OWNERS")
    if raw is None:
        return dict(DEFAULT_JOB_TYPE_OWNERS)
    owners = {}
    for pair in filter(None, (p.strip() for p in raw.split(","))):
        job_type, _, worker = pair.partition("=")
        if not job_type or not worker:
            raise RuntimeError(f"invalid JOB_TYPE_OWNERS entry: {pair!r}")
        owners[job_type.strip()] = worker.strip()
    return owners


QUESTION_STATUSES = ("candidate", "open", "answered", "parked")

app = FastAPI(
    title="Applied Commons Orchestrator",
    version="0.1.0",
)


def db():
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
    )


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class QuestionCreate(BaseModel):
    project_id: int
    question: str
    parent_question_id: int | None = None
    priority: float | None = None


class JobCreate(BaseModel):
    project_id: int
    question_id: int | None = None
    job_type: str
    worker: str = "unassigned"
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    input: dict[str, Any] = Field(default_factory=dict)


class JobClaim(BaseModel):
    worker: str
    job_types: list[str]


class FindingIn(BaseModel):
    finding: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0.0, le=1.0)


class EvidenceIn(BaseModel):
    source_uri: str = Field(pattern=r"^https?://", max_length=2000)
    title: str | None = Field(default=None, max_length=500)
    source_type: str | None = Field(default=None, max_length=100)


class JobComplete(BaseModel):
    worker: str
    output: dict[str, Any] = Field(default_factory=dict)


class JobFail(BaseModel):
    worker: str
    error: str


class JobHeartbeat(BaseModel):
    worker: str


class QuestionStatus(BaseModel):
    status: str


class ProjectCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    maslow_level: int = Field(ge=1, le=5)
    domain: str = Field(min_length=1, max_length=100)
    status: str = Field(default="candidate", pattern="^(candidate|active|paused|parked|completed)$")


class ProjectStatus(BaseModel):
    status: str = Field(pattern="^(candidate|active|paused|parked|completed)$")


class BacklogDiscover(BaseModel):
    limit: int = Field(default=20, ge=1, le=200)


class ProjectSteer(BaseModel):
    steer: str | None = Field(default=None, pattern="^(build|park)$")
    note: str = Field(default="", max_length=2000)


class ArchiveRecord(BaseModel):
    project_id: int
    source_uri: str = Field(pattern=r"^https://", max_length=2000)
    kind: str = Field(pattern="^(git|file)$")
    revision: str = Field(default="", max_length=64)
    resolved_revision: str | None = Field(default=None, max_length=64)
    licence: str = Field(default="unknown", max_length=200)
    status: str = Field(pattern="^(archived|skipped|failed)$")
    path: str | None = Field(default=None, max_length=1000)
    bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    note: str = Field(default="", max_length=2000)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()

    return {
        "status": "ok",
        "database": "ok",
    }


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@app.get("/projects")
def get_projects():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    code,
                    name,
                    maslow_level,
                    domain,
                    status,
                    created_at
                FROM projects
                ORDER BY id;
                """
            )

            return cur.fetchall()


@app.post("/projects")
def create_project(item: ProjectCreate):
    """Record a project. New projects are candidates: making one active
    (and so eligible for backlog discovery) is an explicit status change."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO projects (code, name, maslow_level, domain, status)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, code, name, maslow_level, domain, status, created_at;
                """,
                (item.code, item.name, item.maslow_level, item.domain, item.status),
            )
            result = cur.fetchone()
        conn.commit()
    return result


@app.post("/projects/{project_id}/status")
def set_project_status(project_id: int, item: ProjectStatus):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE projects SET status = %s WHERE id = %s
                RETURNING id, code, name, maslow_level, domain, status;
                """,
                (item.status, project_id),
            )
            result = cur.fetchone()
        conn.commit()
    if result is None:
        raise HTTPException(status_code=404, detail="no such project")
    return result


@app.post("/projects/{project_id}/steer")
def steer_project(project_id: int, item: ProjectSteer):
    """Maintainer override (policy section 4d): 'build' forces a build
    pack, 'park' parks the project; null clears the steer without undoing
    what it caused. The next pipeline step acts on it."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE projects SET steer = %s, steer_note = %s, steered_at = NOW()
                WHERE id = %s
                RETURNING id, code, name, status, steer, steer_note, steered_at;
                """,
                (item.steer, item.note or None, project_id),
            )
            result = cur.fetchone()
        conn.commit()
    if result is None:
        raise HTTPException(status_code=404, detail="no such project")
    return result


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

@app.get("/questions")
def get_questions():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    project_id,
                    parent_question_id,
                    question,
                    status,
                    priority,
                    created_at
                FROM questions
                ORDER BY id;
                """
            )

            return cur.fetchall()


@app.post("/questions")
def create_question(item: QuestionCreate):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO questions (
                    project_id,
                    parent_question_id,
                    question,
                    priority
                )
                VALUES (%s, %s, %s, %s)
                RETURNING
                    id,
                    project_id,
                    parent_question_id,
                    question,
                    status,
                    priority,
                    created_at;
                """,
                (
                    item.project_id,
                    item.parent_question_id,
                    item.question,
                    item.priority,
                ),
            )

            result = cur.fetchone()

        conn.commit()

    return result


@app.post("/questions/{question_id}/status")
def set_question_status(question_id: int, item: QuestionStatus):
    """Move a question between candidate, open, answered and parked.

    Only ``open`` questions in ``active`` projects are turned into research
    jobs by backlog discovery.
    """
    if item.status not in QUESTION_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {list(QUESTION_STATUSES)}",
        )
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE questions SET status = %s
                WHERE id = %s
                RETURNING id, project_id, question, status, priority;
                """,
                (item.status, question_id),
            )
            result = cur.fetchone()
        conn.commit()
    if result is None:
        raise HTTPException(status_code=404, detail="no such question")
    return result


# ---------------------------------------------------------------------------
# Backlog discovery
# ---------------------------------------------------------------------------

def _enqueue(cur, *, unit_key, job_type, priority, payload,
             project_id=None, question_id=None):
    cur.execute(
        """
        INSERT INTO jobs (project_id, question_id, job_type, worker, priority, input, unit_key)
        VALUES (%s, %s, %s, 'unassigned', %s, %s, %s)
        ON CONFLICT (unit_key) WHERE unit_key IS NOT NULL DO NOTHING
        RETURNING id, job_type, unit_key, priority;
        """,
        (project_id, question_id, job_type, min(max(priority, 0.0), 1.0),
         Jsonb(payload), unit_key),
    )
    return cur.fetchone()


def _unit_exists(cur, unit_key) -> bool:
    cur.execute("SELECT 1 FROM jobs WHERE unit_key = %s", (unit_key,))
    return cur.fetchone() is not None


def _focus_categories(cur) -> list:
    cur.execute(
        "SELECT id, layer, name, scope FROM need_categories "
        "WHERE layer = ANY(%s) ORDER BY layer, id",
        (list(research.DISCOVERY_LAYERS),),
    )
    return cur.fetchall()


def _latest_brief(cur, category_id) -> dict | None:
    cur.execute(
        "SELECT brief, created_at FROM need_briefs WHERE category_id = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (category_id,),
    )
    row = cur.fetchone()
    return row["brief"] if row else None


def _brief_needs(cur, budget: int) -> list:
    """Stage 0: a need brief per focus-layer category, refreshed every
    BRIEF_REFRESH_DAYS; discovery in a category waits for its brief."""
    period = int(time.time() // (research.BRIEF_REFRESH_DAYS * 86400))
    created = []
    for category in _focus_categories(cur):
        if len(created) >= budget:
            break
        cur.execute(
            "SELECT 1 FROM need_briefs WHERE category_id = %s "
            "AND created_at > NOW() - make_interval(days => %s)",
            (category["id"], research.BRIEF_REFRESH_DAYS),
        )
        if cur.fetchone():
            continue
        key = f"{research.NEED_BRIEF}:category:{category['id']}:{period}"
        if _unit_exists(cur, key):
            continue
        job = _enqueue(cur, unit_key=key, job_type=research.NEED_BRIEF,
                       priority=0.75,
                       payload={"category_id": category["id"],
                                **research.need_brief_input(category)})
        if job:
            created.append(job)
    return created


def _discover_candidates(cur, budget: int) -> list:
    """Stage 1: scan each briefed focus-layer need category once per
    period."""
    period = int(time.time() // (research.DISCOVERY_PERIOD_DAYS * 86400))
    created = []
    for category in _focus_categories(cur):
        if len(created) >= budget:
            break
        key = f"{research.DISCOVERY}:category:{category['id']}:{period}"
        if _unit_exists(cur, key):
            continue
        brief = _latest_brief(cur, category["id"])
        if brief is None:
            continue
        cur.execute(
            "SELECT name FROM projects WHERE category_id = %s ORDER BY id",
            (category["id"],),
        )
        known = [r["name"] for r in cur.fetchall()]
        payload = {"category_id": category["id"],
                   **research.discovery_input(category, known, brief)}
        job = _enqueue(cur, unit_key=key, job_type=research.DISCOVERY,
                       priority=research.LAYER_SCORE[category["layer"]] * 0.6,
                       payload=payload)
        if job:
            created.append(job)
    return created


def _assess_candidates(cur, budget: int) -> list:
    """Stage 2: assess each discovered candidate once."""
    cur.execute(
        """
        SELECT p.id, p.name, p.summary, p.source_uris, p.maslow_level AS layer,
               c.name AS category, p.category_id
        FROM projects p JOIN need_categories c ON c.id = p.category_id
        WHERE p.status = 'candidate'
          AND NOT EXISTS (SELECT 1 FROM assessments a WHERE a.project_id = p.id)
        ORDER BY p.maslow_level, p.id
        """
    )
    created = []
    for project in cur.fetchall():
        if len(created) >= budget:
            break
        key = f"{research.ASSESSMENT}:project:{project['id']}"
        if _unit_exists(cur, key):
            continue
        project["brief"] = _latest_brief(cur, project["category_id"])
        job = _enqueue(cur, unit_key=key, job_type=research.ASSESSMENT,
                       priority=research.LAYER_SCORE[project["layer"]] * 0.7,
                       payload=research.assessment_input(project),
                       project_id=project["id"])
        if job:
            created.append(job)
    return created


def _admit(cur) -> list:
    """Stage 3: autonomous admission under rubric v1 (policy section 4a)."""
    cur.execute("SELECT count(*) AS n FROM projects WHERE status = 'active'")
    slots = research.max_active_projects() - cur.fetchone()["n"]
    if slots <= 0:
        return []
    cur.execute(
        """
        SELECT DISTINCT ON (p.id) p.id, p.name, p.source_uris, a.composite,
               a.scores, a.requirements, a.rationale,
               (SELECT count(*) FROM evidence e WHERE e.project_id = p.id) AS evidence_rows
        FROM projects p JOIN assessments a ON a.project_id = p.id
        WHERE p.status = 'candidate'
        ORDER BY p.id, a.created_at DESC
        """
    )
    eligible = [
        row for row in cur.fetchall()
        if row["composite"] >= research.ADMIT_THRESHOLD
        and row["scores"]["evidence"] >= research.ADMIT_MIN_EVIDENCE
        and research.admission_case_made(row["requirements"])
        and len(row["source_uris"]) + row["evidence_rows"] >= research.ADMIT_MIN_SOURCES
    ]
    eligible.sort(key=lambda r: (-r["composite"], r["id"]))
    admitted = []
    for row in eligible[:slots]:
        cur.execute("UPDATE projects SET status = 'active' WHERE id = %s", (row["id"],))
        cur.execute(
            "UPDATE questions SET status = 'open' "
            "WHERE project_id = %s AND status = 'candidate'",
            (row["id"],),
        )
        cur.execute(
            """
            INSERT INTO decisions (project_id, decision, rationale, author, policy_revision)
            VALUES (%s, 'admit', %s, %s, %s)
            """,
            (row["id"],
             f"composite {row['composite']:.3f} >= {research.ADMIT_THRESHOLD}; "
             f"evidence {row['scores']['evidence']:.2f}; {row['rationale'][:1500]}",
             research.DECISION_AUTHOR, research.POLICY_REVISION),
        )
        admitted.append({"project_id": row["id"], "composite": row["composite"]})
    return admitted


def _collect_literature(cur, budget: int) -> list:
    """Stage 4: research each open question of each active project."""
    cur.execute(
        """
        SELECT q.id, q.project_id, q.question, q.priority, p.code AS project_code,
               p.name AS project_name, p.maslow_level
        FROM questions q JOIN projects p ON p.id = q.project_id
        WHERE p.status = 'active' AND q.status = 'open'
        ORDER BY COALESCE(q.priority, 0.5) DESC, q.id
        """
    )
    created = []
    for question in cur.fetchall():
        if len(created) >= budget:
            break
        key = f"{research.LITERATURE}:question:{question['id']}"
        if _unit_exists(cur, key):
            continue
        job = _enqueue(cur, unit_key=key, job_type=research.LITERATURE,
                       priority=question["priority"] if question["priority"] is not None else 0.5,
                       payload=research.literature_input(question),
                       project_id=question["project_id"], question_id=question["id"])
        if job:
            created.append({**job, "question_id": question["id"]})
    return created


def _decide(cur, project_id, decision, rationale, author) -> None:
    cur.execute(
        """
        INSERT INTO decisions (project_id, decision, rationale, author, policy_revision)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (project_id, decision, rationale[:2000], author, research.POLICY_REVISION),
    )


def _has_decision(cur, project_id, decision) -> bool:
    cur.execute("SELECT 1 FROM decisions WHERE project_id = %s AND decision = %s",
                (project_id, decision))
    return cur.fetchone() is not None


def _apply_steers(cur) -> list:
    """Maintainer overrides first (policy section 4d). Idempotent: a park
    steer acts on a project that is not parked yet, a build steer on one
    without a build decision."""
    cur.execute(
        "SELECT id, status, steer, steer_note FROM projects "
        "WHERE steer IS NOT NULL ORDER BY steered_at, id"
    )
    applied = []
    for p in cur.fetchall():
        note = f"maintainer steer: {p['steer']}" + (
            f" ({p['steer_note']})" if p["steer_note"] else "")
        if p["steer"] == "park" and p["status"] in ("candidate", "active", "paused"):
            cur.execute("UPDATE projects SET status = 'parked' WHERE id = %s", (p["id"],))
            _decide(cur, p["id"], "park", note, research.STEER_AUTHOR)
        elif p["steer"] == "build" and not _has_decision(cur, p["id"], "build"):
            cur.execute("UPDATE projects SET status = 'active' WHERE id = %s", (p["id"],))
            _decide(cur, p["id"], "build", note, research.STEER_AUTHOR)
        else:
            continue
        applied.append({"project_id": p["id"], "steer": p["steer"]})
    return applied


def _dossier(cur, project_id) -> dict:
    """What a review or build pack job needs to know about a project."""
    cur.execute(
        """
        SELECT p.id, p.code, p.name, p.summary, p.source_uris, p.category_id,
               p.maslow_level AS layer, c.name AS category
        FROM projects p LEFT JOIN need_categories c ON c.id = p.category_id
        WHERE p.id = %s
        """,
        (project_id,),
    )
    p = cur.fetchone()
    cur.execute(
        "SELECT scores, requirements, rationale FROM assessments "
        "WHERE project_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (project_id,),
    )
    assessment = cur.fetchone()
    cur.execute(
        "SELECT finding, confidence FROM findings WHERE project_id = %s "
        "ORDER BY confidence DESC NULLS LAST, id LIMIT 80",
        (project_id,),
    )
    findings = [{"finding": f["finding"][:600], "confidence": f["confidence"]}
                for f in cur.fetchall()]
    cur.execute(
        "SELECT DISTINCT source_uri FROM evidence WHERE project_id = %s "
        "AND source_uri IS NOT NULL LIMIT 60",
        (project_id,),
    )
    sources = list(dict.fromkeys(
        list(p["source_uris"] or []) + [r["source_uri"] for r in cur.fetchall()]))[:60]
    cur.execute(
        "SELECT fit, gaps FROM reviews WHERE project_id = %s ORDER BY id",
        (project_id,),
    )
    reviews = cur.fetchall()
    return {
        "id": p["id"], "code": p["code"], "name": p["name"],
        "summary": p["summary"] or "", "category": p["category"] or "",
        "layer": p["layer"],
        "brief": _latest_brief(cur, p["category_id"]) if p["category_id"] else None,
        "assessment": assessment or {},
        "findings": findings, "sources": sources,
        "earlier_gaps": [g for r in reviews for g in r["gaps"]][:20],
        "fit": reviews[-1]["fit"] if reviews else "",
    }


def _review_projects(cur, budget: int) -> list:
    """Stage 5: go/no-go review (policy section 4b) of each active project
    whose research questions are settled (answered, parked, or whose
    research job failed for good) and that has no build decision yet."""
    cur.execute(
        """
        SELECT p.id,
               (SELECT count(*) FROM reviews r WHERE r.project_id = p.id) AS done
        FROM projects p
        WHERE p.status = 'active'
          AND NOT EXISTS (SELECT 1 FROM decisions d
                          WHERE d.project_id = p.id AND d.decision = 'build')
          AND NOT EXISTS (
              SELECT 1 FROM questions q
              WHERE q.project_id = p.id AND q.status = 'open'
                AND NOT EXISTS (SELECT 1 FROM jobs j
                                WHERE j.unit_key = 'literature-collection:question:' || q.id
                                  AND j.status = 'failed'))
        ORDER BY p.score DESC NULLS LAST, p.id
        """
    )
    created = []
    for row in cur.fetchall():
        if len(created) >= budget:
            break
        number = row["done"] + 1
        if number > research.MAX_REVIEWS:
            continue  # the last review always decides build or park
        key = f"{research.REVIEW}:project:{row['id']}:{number}"
        cur.execute("SELECT status FROM jobs WHERE unit_key = %s", (key,))
        existing = cur.fetchone()
        if existing:
            if existing["status"] == "failed":
                cur.execute("UPDATE projects SET status = 'parked' WHERE id = %s",
                            (row["id"],))
                _decide(cur, row["id"], "park",
                        f"review {number} failed three times; steer 'build' to "
                        "override", research.REVIEW_AUTHOR)
            continue
        job = _enqueue(cur, unit_key=key, job_type=research.REVIEW, priority=0.85,
                       payload=research.review_input(_dossier(cur, row["id"]), number),
                       project_id=row["id"])
        if job:
            created.append(job)
    return created


def _build_packs(cur, budget: int) -> list:
    """Stage 6: one build-pack job per section for each active project
    with a build decision (policy section 4c)."""
    cur.execute(
        """
        SELECT p.id FROM projects p
        WHERE p.status = 'active'
          AND EXISTS (SELECT 1 FROM decisions d
                      WHERE d.project_id = p.id AND d.decision = 'build')
        ORDER BY p.score DESC NULLS LAST, p.id
        """
    )
    created = []
    for row in cur.fetchall():
        dossier = None
        for section in research.BUILD_SECTIONS:
            if len(created) >= budget:
                return created
            key = f"{research.BUILD_PACK}:project:{row['id']}:{section}"
            if _unit_exists(cur, key):
                continue
            dossier = dossier or _dossier(cur, row["id"])
            job = _enqueue(cur, unit_key=key, job_type=research.BUILD_PACK,
                           priority=0.9,
                           payload=research.build_input(dossier, section),
                           project_id=row["id"])
            if job:
                created.append(job)
    return created


def _complete_builds(cur) -> list:
    """Stage 7: a project whose build pack sections have all finished
    (completed, or failed for good) is complete; its slot frees up."""
    sections = list(research.BUILD_SECTIONS)
    cur.execute(
        """
        SELECT p.id,
               count(*) FILTER (WHERE j.status = 'completed') AS completed,
               count(*) FILTER (WHERE j.status = 'failed') AS failed
        FROM projects p
        JOIN jobs j ON j.project_id = p.id AND j.job_type = %s
        WHERE p.status = 'active'
        GROUP BY p.id
        HAVING count(*) FILTER (WHERE j.status IN ('completed', 'failed')) = %s
        """,
        (research.BUILD_PACK, len(sections)),
    )
    done = []
    for row in cur.fetchall():
        cur.execute("UPDATE projects SET status = 'completed' WHERE id = %s", (row["id"],))
        _decide(cur, row["id"], "complete",
                f"build pack finished: {row['completed']} of {len(sections)} "
                f"sections completed, {row['failed']} failed",
                research.REVIEW_AUTHOR)
        done.append({"project_id": row["id"], "failed_sections": row["failed"]})
    return done


@app.post("/backlog/discover")
def discover_backlog(item: BacklogDiscover):
    """Advance the autonomous research pipeline by one bounded step.

    0. steers: apply maintainer overrides (build / park);
    1. briefs: one need-brief job per focus-layer need category per
       refresh period;
    2. discovery: one candidate-discovery job per briefed category per
       period;
    3. assessment: one candidate-assessment job per new candidate, judged
       against its category's brief;
    4. admission: admit the best-scoring candidates under rubric v1, up to
       the active-project limit, opening their questions;
    5. literature: one literature-collection job per open question of an
       active project;
    6. review: a go/no-go project-review once an active project's
       questions are settled (build, continue with new questions, or park);
    7. build: one build-pack job per section for projects with a build
       decision, and completion once every section has finished.

    Every job carries a stable unit key, so repeated runs never enqueue the
    same unit twice; completed or failed units are not re-enqueued. Topics
    come only from the needs taxonomy and from what earlier steps found.
    """
    with db() as conn:
        with conn.cursor() as cur:
            steered = _apply_steers(cur)
            briefs = _brief_needs(cur, item.limit)
            discovered = _discover_candidates(cur, item.limit)
            assessed = _assess_candidates(cur, item.limit)
            admitted = _admit(cur)
            literature = _collect_literature(cur, item.limit)
            reviews = _review_projects(cur, item.limit)
            builds = _build_packs(cur, item.limit)
            completed = _complete_builds(cur)
        conn.commit()
    return {
        "steered": steered,
        "briefs": briefs,
        "discovery": discovered,
        "assessment": assessed,
        "admitted": admitted,
        "created": literature,
        "reviews": reviews,
        "builds": builds,
        "completed": completed,
    }


@app.get("/portfolio")
def portfolio():
    """Projects ranked by their latest rubric score, with recent decisions."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.code, p.name, p.status, p.maslow_level,
                       c.name AS category, p.score, p.source_uris,
                       (SELECT count(*) FROM questions q WHERE q.project_id = p.id) AS questions,
                       (SELECT count(*) FROM findings f WHERE f.project_id = p.id) AS findings
                FROM projects p LEFT JOIN need_categories c ON c.id = p.category_id
                ORDER BY (p.status = 'active') DESC, p.score DESC NULLS LAST, p.id
                """
            )
            projects = cur.fetchall()
            cur.execute(
                "SELECT project_id, decision, rationale, author, policy_revision, created_at "
                "FROM decisions ORDER BY id DESC LIMIT 50"
            )
            decisions = cur.fetchall()
    return {"projects": projects, "decisions": decisions}


#: Catalogue stage of a project, from its status and decisions.
_STAGE_SQL = """
    CASE
        WHEN p.status = 'parked' THEN 'parked'
        WHEN p.status = 'completed' THEN 'built'
        WHEN p.status = 'active' AND EXISTS (
            SELECT 1 FROM decisions d WHERE d.project_id = p.id AND d.decision = 'build')
            THEN 'build pack'
        WHEN p.status = 'active' THEN 'fit research'
        WHEN EXISTS (SELECT 1 FROM assessments a WHERE a.project_id = p.id) THEN 'screened'
        ELSE 'discovered'
    END
"""


@app.get("/catalogue")
def catalogue():
    """Every project with its catalogue stage, scores and progress, and
    ``updated_at``: the latest change to anything shown in its record (for
    one-way publishing such as the Notion sync). Read-only."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT p.id, p.code, p.name, p.status, {_STAGE_SQL} AS stage,
                       p.maslow_level AS layer, c.name AS category, p.score,
                       p.summary, p.source_uris, p.steer, p.steer_note,
                       (SELECT d.decision FROM decisions d WHERE d.project_id = p.id
                        ORDER BY d.id DESC LIMIT 1) AS decision,
                       (SELECT count(*) FROM questions q WHERE q.project_id = p.id
                          AND q.status <> 'candidate') AS questions,
                       (SELECT count(*) FROM questions q WHERE q.project_id = p.id
                          AND q.status = 'answered') AS answered,
                       (SELECT count(*) FROM findings f WHERE f.project_id = p.id) AS findings,
                       (SELECT count(DISTINCT b.section) FROM build_packs b
                        WHERE b.project_id = p.id) AS build_sections,
                       GREATEST(
                           p.created_at, p.steered_at,
                           (SELECT max(created_at) FROM decisions WHERE project_id = p.id),
                           (SELECT max(created_at) FROM assessments WHERE project_id = p.id),
                           (SELECT max(created_at) FROM reviews WHERE project_id = p.id),
                           (SELECT max(created_at) FROM findings WHERE project_id = p.id),
                           (SELECT max(created_at) FROM build_packs WHERE project_id = p.id),
                           (SELECT max(created_at) FROM design_archives WHERE project_id = p.id),
                           (SELECT max(created_at) FROM questions WHERE project_id = p.id)
                       ) AS updated_at
                FROM projects p LEFT JOIN need_categories c ON c.id = p.category_id
                ORDER BY p.maslow_level, c.name, p.score DESC NULLS LAST, p.id
                """
            )
            return {"projects": cur.fetchall(),
                    "stages": ["discovered", "screened", "fit research",
                               "build pack", "built", "parked"]}


@app.get("/briefs")
def briefs():
    """The latest need brief of each need category that has one."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (c.id) c.id AS category_id, c.layer, c.name,
                       c.scope, b.brief, b.created_at
                FROM need_categories c JOIN need_briefs b ON b.category_id = c.id
                ORDER BY c.id, b.created_at DESC, b.id DESC
                """
            )
            return cur.fetchall()


@app.get("/projects/{project_id}/record")
def project_record(project_id: int):
    """Everything known about one project, for reading: assessment,
    reviews, decisions, questions with their findings, evidence, the
    latest build pack sections and archived design files."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT p.id, p.code, p.name, p.status, {_STAGE_SQL} AS stage,
                       p.maslow_level AS layer, c.name AS category, p.category_id,
                       p.score, p.summary, p.source_uris, p.steer, p.steer_note,
                       p.created_at
                FROM projects p LEFT JOIN need_categories c ON c.id = p.category_id
                WHERE p.id = %s
                """,
                (project_id,),
            )
            project = cur.fetchone()
            if project is None:
                raise HTTPException(status_code=404, detail="no such project")

            def rows(query):
                cur.execute(query, (project_id,))
                return cur.fetchall()

            assessments = rows(
                "SELECT scores, composite, requirements, rationale, created_at "
                "FROM assessments WHERE project_id = %s ORDER BY id DESC LIMIT 1")
            questions = rows(
                "SELECT id, question, status, priority, created_at FROM questions "
                "WHERE project_id = %s AND status <> 'candidate' "
                "ORDER BY priority DESC NULLS LAST, id")
            findings = rows(
                "SELECT question_id, finding, confidence, created_at FROM findings "
                "WHERE project_id = %s ORDER BY id")
            for q in questions:
                q["findings"] = [f for f in findings if f["question_id"] == q["id"]]
            return {
                "project": project,
                "brief": (_latest_brief(cur, project["category_id"])
                          if project["category_id"] else None),
                "assessment": assessments[0] if assessments else None,
                "reviews": rows(
                    "SELECT review_number, recommended, outcome, scores, composite, "
                    "fit, rationale, gaps, created_at FROM reviews "
                    "WHERE project_id = %s ORDER BY id"),
                "decisions": rows(
                    "SELECT decision, rationale, author, created_at FROM decisions "
                    "WHERE project_id = %s ORDER BY id"),
                "questions": questions,
                "other_findings": [f for f in findings if f["question_id"] is None],
                "evidence": rows(
                    "SELECT DISTINCT ON (source_uri) source_uri, title, source_type "
                    "FROM evidence WHERE project_id = %s AND source_uri IS NOT NULL "
                    "ORDER BY source_uri, id"),
                "build": {r["section"]: r["content"] for r in rows(
                    "SELECT DISTINCT ON (section) section, content FROM build_packs "
                    "WHERE project_id = %s ORDER BY section, created_at DESC, id DESC")},
                "archives": rows(
                    "SELECT source_uri, kind, revision, resolved_revision, licence, "
                    "status, path, bytes, sha256, note, created_at "
                    "FROM design_archives WHERE project_id = %s ORDER BY id"),
            }


@app.get("/archives/pending")
def archives_pending(limit: int = 20):
    """Design sources from the latest build pack 'design' sections that
    have no archive record yet (ops/archive/archive-designs.py)."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (b.project_id) b.project_id, p.code, b.content
                FROM build_packs b JOIN projects p ON p.id = b.project_id
                WHERE b.section = 'design'
                ORDER BY b.project_id, b.created_at DESC, b.id DESC
                """
            )
            packs = cur.fetchall()
            # A failed attempt is retried after a day; archived and skipped
            # sources are settled.
            cur.execute(
                "SELECT project_id, source_uri, revision FROM design_archives "
                "WHERE status <> 'failed' OR created_at > NOW() - INTERVAL '1 day'")
            done = {(r["project_id"], r["source_uri"], r["revision"]) for r in cur.fetchall()}
    pending = []
    for pack in packs:
        content = pack["content"] or {}
        items = [("git", r.get("url"), r.get("commit") or "", r.get("licence"))
                 for r in content.get("repositories") or []]
        items += [("file", f.get("uri"), "", f.get("licence"))
                  for f in content.get("files") or []]
        for kind, uri, revision, licence in items:
            if not uri or (pack["project_id"], uri, revision) in done:
                continue
            done.add((pack["project_id"], uri, revision))
            pending.append({"project_id": pack["project_id"], "code": pack["code"],
                            "kind": kind, "source_uri": uri, "revision": revision,
                            "licence": licence or "unknown"})
            if len(pending) >= limit:
                return pending
    return pending


@app.post("/archives")
def record_archive(item: ArchiveRecord):
    """Record the outcome of archiving one design source."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO design_archives (project_id, source_uri, kind, revision,
                    resolved_revision, licence, status, path, bytes, sha256, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (project_id, source_uri, revision) DO UPDATE SET
                    resolved_revision = EXCLUDED.resolved_revision,
                    licence = EXCLUDED.licence, status = EXCLUDED.status,
                    path = EXCLUDED.path, bytes = EXCLUDED.bytes,
                    sha256 = EXCLUDED.sha256, note = EXCLUDED.note,
                    created_at = NOW()
                RETURNING id, status;
                """,
                (item.project_id, item.source_uri, item.kind, item.revision,
                 item.resolved_revision, item.licence, item.status, item.path,
                 item.bytes, item.sha256, item.note),
            )
            result = cur.fetchone()
        conn.commit()
    return result


def _counts(cur, query: str) -> dict[str, int]:
    cur.execute(query)
    return {row["key"]: row["n"] for row in cur.fetchall()}


@app.get("/summary")
def summary():
    """Stage counts for an operator overview: the research pipeline from
    need categories to findings, and the job queue. Read-only."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT count(*) FROM need_categories) AS need_categories,
                    (SELECT count(DISTINCT category_id) FROM projects
                       WHERE category_id IS NOT NULL) AS categories_with_candidates,
                    (SELECT count(*) FROM projects) AS projects,
                    (SELECT count(DISTINCT project_id) FROM assessments) AS assessed,
                    (SELECT count(*) FROM questions) AS questions,
                    (SELECT count(*) FROM findings) AS findings,
                    (SELECT count(*) FROM evidence) AS evidence,
                    (SELECT count(DISTINCT category_id) FROM need_briefs) AS briefed,
                    (SELECT count(*) FROM reviews) AS reviews,
                    (SELECT count(DISTINCT project_id) FROM build_packs) AS build_packs,
                    (SELECT count(*) FROM design_archives
                       WHERE status = 'archived') AS archived_designs,
                    (SELECT count(*) FROM jobs WHERE status = 'completed'
                       AND completed_at > NOW() - INTERVAL '24 hours') AS jobs_completed_24h,
                    (SELECT count(*) FROM jobs WHERE status = 'failed'
                       AND completed_at > NOW() - INTERVAL '24 hours') AS jobs_failed_24h
                """
            )
            totals = cur.fetchone()
            projects = _counts(cur, "SELECT status AS key, count(*) AS n FROM projects GROUP BY 1")
            questions = _counts(cur, "SELECT status AS key, count(*) AS n FROM questions GROUP BY 1")
            decisions = _counts(cur, "SELECT decision AS key, count(*) AS n FROM decisions GROUP BY 1")
            cur.execute(
                "SELECT job_type, status, count(*) AS n FROM jobs GROUP BY 1, 2 ORDER BY 1, 2"
            )
            jobs: dict[str, dict[str, int]] = {}
            for row in cur.fetchall():
                jobs.setdefault(row["job_type"], {})[row["status"]] = row["n"]
    return {
        "pipeline": {
            "need_categories": totals["need_categories"],
            "briefed_categories": totals["briefed"],
            "categories_with_candidates": totals["categories_with_candidates"],
            "candidates": totals["projects"],
            "assessed": totals["assessed"],
            "admitted": projects.get("active", 0),
            "questions": totals["questions"],
            "findings": totals["findings"],
            "evidence": totals["evidence"],
            "reviews": totals["reviews"],
            "build_packs": totals["build_packs"],
            "completed": projects.get("completed", 0),
            "archived_designs": totals["archived_designs"],
        },
        "projects_by_status": projects,
        "questions_by_status": questions,
        "decisions": decisions,
        "jobs": jobs,
        "jobs_completed_24h": totals["jobs_completed_24h"],
        "jobs_failed_24h": totals["jobs_failed_24h"],
    }


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@app.get("/jobs")
def get_jobs():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    project_id,
                    question_id,
                    job_type,
                    worker,
                    status,
                    priority,
                    attempts,
                    lease_until,
                    input,
                    output,
                    error,
                    created_at,
                    started_at,
                    completed_at
                FROM jobs
                ORDER BY id;
                """
            )

            return cur.fetchall()


@app.post("/jobs")
def create_job(item: JobCreate):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (
                    project_id,
                    question_id,
                    job_type,
                    worker,
                    priority,
                    input
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING
                    id,
                    project_id,
                    question_id,
                    job_type,
                    worker,
                    status,
                    priority,
                    attempts,
                    input,
                    created_at;
                """,
                (
                    item.project_id,
                    item.question_id,
                    item.job_type,
                    item.worker,
                    item.priority,
                    Jsonb(item.input),
                ),
            )

            result = cur.fetchone()

        conn.commit()

    return result


# ---------------------------------------------------------------------------
# Job claiming
# ---------------------------------------------------------------------------

@app.post("/jobs/claim")
def claim_job(item: JobClaim):
    if not item.job_types:
        raise HTTPException(
            status_code=400,
            detail="job_types must contain at least one job type",
        )

    owners = job_type_owners()
    foreign = sorted(
        t for t in item.job_types
        if t in owners and owners[t] != item.worker
    )
    if foreign:
        raise HTTPException(
            status_code=403,
            detail=f"job types reserved for another worker: {foreign}",
        )

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH candidate AS (
                    SELECT id
                    FROM jobs
                    WHERE job_type = ANY(%s)
                      AND (
                            (
                                status = 'queued'
                                AND (
                                    worker = 'unassigned'
                                    OR worker = %s
                                )
                            )
                            OR
                            (
                                status = 'running'
                                AND lease_until < NOW()
                            )
                          )
                    ORDER BY
                        priority DESC,
                        created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE jobs AS j
                SET
                    status = 'running',
                    worker = %s,
                    started_at = COALESCE(j.started_at, NOW()),
                    attempts = j.attempts + 1,
                    lease_until = NOW() + INTERVAL '15 minutes',
                    error = NULL
                FROM candidate
                WHERE j.id = candidate.id
                RETURNING j.*;
                """,
                (
                    item.job_types,
                    item.worker,
                    item.worker,
                ),
            )

            result = cur.fetchone()

        conn.commit()

    return {
        "job": result,
    }


# ---------------------------------------------------------------------------
# Job lease heartbeat
# ---------------------------------------------------------------------------

@app.post("/jobs/{job_id}/heartbeat")
def heartbeat_job(job_id: int, item: JobHeartbeat):
    """Renew the lease of a running job, owner-checked.

    Long-running executors (Apollo/Hermes) call this while a job runs so
    its lease does not lapse and let another worker re-claim it.
    """
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET lease_until = NOW() + INTERVAL '15 minutes'
                WHERE id = %s
                  AND status = 'running'
                  AND worker = %s
                RETURNING id, status, worker, attempts, lease_until;
                """,
                (
                    job_id,
                    item.worker,
                ),
            )

            result = cur.fetchone()

        conn.commit()

    if result is None:
        raise HTTPException(
            status_code=409,
            detail="Job is not running or is not owned by this worker",
        )

    return {
        "job": result,
    }


# ---------------------------------------------------------------------------
# Job completion
# ---------------------------------------------------------------------------

@app.post("/jobs/{job_id}/complete")
def complete_job(job_id: int, item: JobComplete):
    # Structured results carried in the output are validated here, before
    # any write, and stored in the same transaction as the completion, so
    # a completed job and its results commit together. Pipeline job types
    # carry their own result contract (see research.py).
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT job_type, input FROM jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
    job_type = row["job_type"] if row else None
    section = (row["input"] or {}).get("section") if row else None
    try:
        findings = [
            FindingIn(**f) for f in item.output.get("findings") or []
        ]
        evidence = [
            EvidenceIn(**e) for e in item.output.get("evidence") or []
        ]
        discovery = (research.DiscoveryResult(**item.output)
                     if job_type == research.DISCOVERY else None)
        assessment = (research.AssessmentResult(**item.output).assessment
                      if job_type == research.ASSESSMENT else None)
        brief = (research.NeedBriefResult(**item.output).brief
                 if job_type == research.NEED_BRIEF else None)
        review = (research.ReviewResult(**item.output).review
                  if job_type == research.REVIEW else None)
        build = (research.validate_build(section, item.output)
                 if job_type == research.BUILD_PACK else None)
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"invalid result for {job_type}: {exc}",
        ) from exc

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET
                    status = 'completed',
                    output = %s,
                    completed_at = NOW(),
                    lease_until = NULL,
                    error = NULL
                WHERE id = %s
                  AND status = 'running'
                  AND worker = %s
                RETURNING *;
                """,
                (
                    Jsonb(item.output),
                    job_id,
                    item.worker,
                ),
            )

            result = cur.fetchone()
            extra: dict = {}

            if result is not None:
                for f in findings:
                    cur.execute(
                        """
                        INSERT INTO findings (
                            project_id, question_id, finding, confidence
                        )
                        VALUES (%s, %s, %s, %s);
                        """,
                        (
                            result["project_id"],
                            result["question_id"],
                            f.finding,
                            f.confidence,
                        ),
                    )
                for e in evidence:
                    cur.execute(
                        """
                        INSERT INTO evidence (
                            project_id, source_uri, title, source_type, metadata
                        )
                        VALUES (%s, %s, %s, %s, %s);
                        """,
                        (
                            result["project_id"],
                            e.source_uri,
                            e.title,
                            e.source_type,
                            Jsonb({"job_id": job_id, "worker": item.worker}),
                        ),
                    )
                if discovery is not None:
                    extra = _store_candidates(cur, result, discovery)
                elif assessment is not None:
                    extra = _store_assessment(cur, result, assessment)
                elif brief is not None:
                    cur.execute(
                        "INSERT INTO need_briefs (category_id, job_id, brief) "
                        "VALUES (%s, %s, %s)",
                        (result["input"]["category_id"], job_id,
                         Jsonb(brief.model_dump())),
                    )
                elif review is not None:
                    extra = _store_review(cur, result, review)
                elif build is not None:
                    cur.execute(
                        "INSERT INTO build_packs (project_id, job_id, section, content) "
                        "VALUES (%s, %s, %s, %s)",
                        (result["project_id"], job_id, section, Jsonb(build)),
                    )
                elif job_type == research.LITERATURE and result["question_id"]:
                    cur.execute(
                        "UPDATE questions SET status = 'answered' "
                        "WHERE id = %s AND status = 'open'",
                        (result["question_id"],),
                    )

        conn.commit()

    if result is None:
        raise HTTPException(
            status_code=409,
            detail="Job is not running or is not owned by this worker",
        )

    return {
        "job": result,
        "findings": len(findings),
        "evidence": len(evidence),
        **extra,
    }


def _store_candidates(cur, job, discovery) -> dict:
    category_id = job["input"]["category_id"]
    cur.execute("SELECT layer, name FROM need_categories WHERE id = %s", (category_id,))
    category = cur.fetchone()
    added = 0
    for candidate in discovery.candidates:
        cur.execute(
            """
            INSERT INTO projects (code, name, maslow_level, domain, status,
                                  category_id, summary, source_uris, discovered_by_job)
            VALUES (%s, %s, %s, %s, 'candidate', %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING id;
            """,
            (f"c{category_id}-{research.slug(candidate.name)}", candidate.name,
             category["layer"], category["name"], category_id, candidate.summary,
             Jsonb(candidate.source_uris), job["id"]),
        )
        added += cur.fetchone() is not None
    return {"candidates": added}


def _store_assessment(cur, job, assessment) -> dict:
    project_id = job["project_id"]
    cur.execute("SELECT maslow_level FROM projects WHERE id = %s", (project_id,))
    layer = cur.fetchone()["maslow_level"]
    composite = research.composite_score(layer, assessment.scores)
    cur.execute(
        """
        INSERT INTO assessments (project_id, job_id, rubric_version, scores,
                                 composite, requirements, rationale)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (project_id, job["id"], research.RUBRIC_VERSION, Jsonb(assessment.scores),
         composite, Jsonb(assessment.requirements), assessment.rationale),
    )
    cur.execute("UPDATE projects SET score = %s WHERE id = %s", (composite, project_id))
    for text in assessment.questions:
        cur.execute(
            "INSERT INTO questions (project_id, question, priority) VALUES (%s, %s, %s)",
            (project_id, text, composite),
        )
    return {"composite": composite, "questions": len(assessment.questions)}


def _store_review(cur, job, review) -> dict:
    """Record a go/no-go review and act on the orchestrator's outcome
    (research.review_outcome) while the project is still active."""
    project_id = job["project_id"]
    number = int(job["input"]["review_number"])
    cur.execute("SELECT maslow_level, status FROM projects WHERE id = %s", (project_id,))
    project = cur.fetchone()
    outcome, composite, reason = research.review_outcome(
        project["maslow_level"], review, number)
    cur.execute(
        """
        INSERT INTO reviews (project_id, job_id, review_number, recommended,
                             outcome, scores, composite, fit, rationale, gaps)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (project_id, job["id"], number, review.decision, outcome,
         Jsonb(review.scores), composite, review.fit, review.rationale,
         Jsonb(review.gaps)),
    )
    if project["status"] != "active":
        return {"outcome": outcome, "composite": composite, "acted": False}
    cur.execute("UPDATE projects SET score = %s WHERE id = %s", (composite, project_id))
    _decide(cur, project_id, outcome, f"review {number}: {reason}; {review.rationale}",
            research.REVIEW_AUTHOR)
    if outcome == "park":
        cur.execute("UPDATE projects SET status = 'parked' WHERE id = %s", (project_id,))
    elif outcome == "continue":
        for text in review.questions:
            cur.execute(
                "INSERT INTO questions (project_id, question, status, priority) "
                "VALUES (%s, %s, 'open', %s)",
                (project_id, text, composite),
            )
    return {"outcome": outcome, "composite": composite, "acted": True}


# ---------------------------------------------------------------------------
# Job failure / retry
# ---------------------------------------------------------------------------

@app.post("/jobs/{job_id}/fail")
def fail_job(job_id: int, item: JobFail):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET
                    status = CASE
                        WHEN attempts < 3 THEN 'queued'
                        ELSE 'failed'
                    END,

                    worker = CASE
                        WHEN attempts < 3 THEN 'unassigned'
                        ELSE worker
                    END,

                    error = %s,
                    lease_until = NULL,

                    completed_at = CASE
                        WHEN attempts >= 3 THEN NOW()
                        ELSE NULL
                    END

                WHERE id = %s
                  AND status = 'running'
                  AND worker = %s

                RETURNING *;
                """,
                (
                    item.error,
                    job_id,
                    item.worker,
                ),
            )

            result = cur.fetchone()

        conn.commit()

    if result is None:
        raise HTTPException(
            status_code=409,
            detail="Job is not running or is not owned by this worker",
        )

    return {
        "job": result,
    }
