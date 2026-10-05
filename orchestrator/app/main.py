import os
from typing import Any

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
DISCOVERY_JOB_TYPE = "literature-collection"

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

@app.post("/backlog/discover")
def discover_backlog(item: BacklogDiscover):
    """Enqueue research work for existing open questions.

    For each ``open`` question in an ``active`` project that has no
    literature-collection unit yet, enqueue one, keyed by a stable unit key
    so repeated runs never enqueue it twice. Bounded by ``limit`` and
    ordered by question priority. Nothing is generated beyond the questions
    already recorded: no new topics.
    """
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (
                    project_id, question_id, job_type, priority, input, unit_key
                )
                SELECT
                    q.project_id,
                    q.id,
                    %(job_type)s,
                    LEAST(GREATEST(COALESCE(q.priority, 0.5), 0), 1),
                    jsonb_build_object(
                        'question', q.question,
                        'project_code', p.code,
                        'project_name', p.name,
                        'maslow_level', p.maslow_level,
                        'domain', p.domain
                    ),
                    %(job_type)s || ':question:' || q.id
                FROM questions q
                JOIN projects p ON p.id = q.project_id
                WHERE p.status = 'active'
                  AND q.status = 'open'
                  AND NOT EXISTS (
                        SELECT 1 FROM jobs j
                        WHERE j.unit_key = %(job_type)s || ':question:' || q.id
                  )
                ORDER BY COALESCE(q.priority, 0.5) DESC, q.id
                LIMIT %(limit)s
                ON CONFLICT (unit_key) WHERE unit_key IS NOT NULL DO NOTHING
                RETURNING id, question_id, unit_key, priority;
                """,
                {"job_type": DISCOVERY_JOB_TYPE, "limit": item.limit},
            )
            created = cur.fetchall()
        conn.commit()
    return {"created": created}


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
    # Optional structured results: findings and evidence carried in the
    # output are validated here and stored in the same transaction as
    # the completion, so a completed job and its results commit together.
    try:
        findings = [
            FindingIn(**f) for f in item.output.get("findings") or []
        ]
        evidence = [
            EvidenceIn(**e) for e in item.output.get("evidence") or []
        ]
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"invalid findings/evidence: {exc}",
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
    }


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
