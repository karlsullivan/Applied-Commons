import os
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


DATABASE_URL = os.environ["DATABASE_URL"]


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


class JobComplete(BaseModel):
    worker: str
    output: dict[str, Any] = Field(default_factory=dict)


class JobFail(BaseModel):
    worker: str
    error: str


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
# Job completion
# ---------------------------------------------------------------------------

@app.post("/jobs/{job_id}/complete")
def complete_job(job_id: int, item: JobComplete):
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
