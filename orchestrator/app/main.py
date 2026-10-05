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
    "candidate-discovery": "apollo-hermes",
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


def _discover_candidates(cur, budget: int) -> list:
    """Stage 1: scan each focus-layer need category once per period."""
    period = int(time.time() // (research.DISCOVERY_PERIOD_DAYS * 86400))
    cur.execute(
        "SELECT id, layer, name, scope FROM need_categories "
        "WHERE layer = ANY(%s) ORDER BY layer, id",
        (list(research.DISCOVERY_LAYERS),),
    )
    created = []
    for category in cur.fetchall():
        if len(created) >= budget:
            break
        key = f"{research.DISCOVERY}:category:{category['id']}:{period}"
        if _unit_exists(cur, key):
            continue
        cur.execute(
            "SELECT name FROM projects WHERE category_id = %s ORDER BY id",
            (category["id"],),
        )
        known = [r["name"] for r in cur.fetchall()]
        payload = {"category_id": category["id"],
                   **research.discovery_input(category, known)}
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
               c.name AS category
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


@app.post("/backlog/discover")
def discover_backlog(item: BacklogDiscover):
    """Advance the autonomous research pipeline by one bounded step.

    1. discovery: one candidate-discovery job per focus-layer need category
       per period;
    2. assessment: one candidate-assessment job per new candidate;
    3. admission: admit the best-scoring candidates under rubric v1, up to
       the active-project limit, opening their questions;
    4. literature: one literature-collection job per open question of an
       active project.

    Every job carries a stable unit key, so repeated runs never enqueue the
    same unit twice; completed or failed units are not re-enqueued. Topics
    come only from the needs taxonomy and from what earlier steps found.
    """
    with db() as conn:
        with conn.cursor() as cur:
            discovered = _discover_candidates(cur, item.limit)
            assessed = _assess_candidates(cur, item.limit)
            admitted = _admit(cur)
            literature = _collect_literature(cur, item.limit)
        conn.commit()
    return {
        "discovery": discovered,
        "assessment": assessed,
        "admitted": admitted,
        "created": literature,
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
            cur.execute("SELECT job_type FROM jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
    job_type = row["job_type"] if row else None
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
    except (TypeError, ValueError) as exc:
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
