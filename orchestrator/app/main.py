import json
import os
import time
from datetime import datetime
from typing import Any

import modular
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
    "standards-synthesis": "apollo-hermes",
    "standard-verification": "apollo-hermes",
    "module-synthesis": "apollo-hermes",
    "system-design": "apollo-hermes",
    "roadmap-revision": "apollo-hermes",
    "project-relations": "apollo-hermes",
    "need-progress": "apollo-hermes",
    "standards-review": "apollo-hermes",
    "safety-review": "apollo-hermes",
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


class Solved(BaseModel):
    """The maintainer's judgement that a need is substantially solved."""
    solved: bool
    note: str = Field(default="", max_length=2000)


class Decision(BaseModel):
    """The maintainer's decision on a standard or a roadmap version."""
    decision: str = Field(pattern="^(approved|rejected|proposed)$")
    note: str = Field(default="", max_length=2000)


class SiteIn(BaseModel):
    code: str = Field(pattern="^[a-z0-9-]{2,40}$")
    profile: dict[str, Any]


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


class LinkCheck(BaseModel):
    url: str = Field(max_length=2000)
    state: str = Field(pattern="^(ok|broken|blocked|unreachable|skipped)$")
    status: int | None = None
    final_url: str | None = Field(default=None, max_length=2000)
    note: str = Field(default="", max_length=300)


class SourceCheck(BaseModel):
    """One check of a project's sources (ops/checks/check_sources.py)."""
    links: list[LinkCheck] = Field(default_factory=list, max_length=40)
    licence: str | None = Field(default=None, max_length=200)
    licence_class: str = Field(pattern="^(open|share-alike|restricted|none|unknown)$")
    licence_source: str | None = Field(default=None, max_length=200)
    repository: str | None = Field(default=None, max_length=2000)
    last_activity: datetime | None = None
    archived: bool | None = None


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
    """The focus-layer need categories, then the enablers (section 2a)."""
    cur.execute(
        "SELECT id, layer, name, scope, track FROM need_categories "
        "WHERE layer = ANY(%s) OR track = %s ORDER BY layer NULLS LAST, id",
        (list(research.DISCOVERY_LAYERS), research.ENABLER),
    )
    return cur.fetchall()


def _need_layers(cur) -> dict[str, int]:
    """Focus-layer need category name -> layer: what an enabler can serve."""
    cur.execute(
        "SELECT name, layer FROM need_categories WHERE layer = ANY(%s) ORDER BY layer, id",
        (list(research.DISCOVERY_LAYERS),),
    )
    return {r["name"]: r["layer"] for r in cur.fetchall()}


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
        priority = (research.ENABLER_DISCOVERY_PRIORITY
                    if category["track"] == research.ENABLER
                    else research.LAYER_SCORE[category["layer"]] * 0.6)
        job = _enqueue(cur, unit_key=key, job_type=research.DISCOVERY,
                       priority=priority, payload=payload)
        if job:
            created.append(job)
    return created


def _assess_candidates(cur, budget: int) -> list:
    """Stage 2: assess each discovered candidate once."""
    cur.execute(
        """
        SELECT p.id, p.name, p.summary, p.source_uris, p.maslow_level AS layer,
               c.name AS category, p.category_id, c.track
        FROM projects p JOIN need_categories c ON c.id = p.category_id
        WHERE p.status = 'candidate'
          AND NOT EXISTS (SELECT 1 FROM assessments a WHERE a.project_id = p.id)
        ORDER BY p.maslow_level NULLS LAST, p.id
        """
    )
    created = []
    options = None
    for project in cur.fetchall():
        if len(created) >= budget:
            break
        key = f"{research.ASSESSMENT}:project:{project['id']}"
        if _unit_exists(cur, key):
            continue
        project["brief"] = _latest_brief(cur, project["category_id"])
        if project["track"] == research.ENABLER:
            options = options if options is not None else list(_need_layers(cur))
            project["serves_options"] = options
            priority = research.ENABLER_ASSESSMENT_PRIORITY
        else:
            priority = research.LAYER_SCORE[project["layer"]] * 0.7
        job = _enqueue(cur, unit_key=key, job_type=research.ASSESSMENT,
                       priority=priority,
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
               a.scores, a.requirements, a.rationale, c.track,
               (SELECT count(*) FROM evidence e WHERE e.project_id = p.id) AS evidence_rows
        FROM projects p JOIN assessments a ON a.project_id = p.id
        LEFT JOIN need_categories c ON c.id = p.category_id
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
    # Enablers get a slight attention bump in the order (section 2a).
    eligible.sort(key=lambda r: (
        -(r["composite"] + (research.ENABLER_ATTENTION
                            if r["track"] == research.ENABLER else 0)), r["id"]))
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
       decision, and completion once every section has finished;
    8. modular set (stage 2, section 4e): monthly standards synthesis;
       module synthesis per domain once standards are approved; system
       design per scenario after the month's modules; the roadmap after the
       month's systems;
    9. relations: a project-relations job over the catalogue each month
       and each time enough new projects have been assessed (section 4f);
    10. progress: a need-progress job per briefed need when what could meet
       it changes, at most weekly (section 4h);
    11. safety: a safety-review of each completed build pack, again when
       it changes (section 4i).

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
            modular_set = _modular_set(cur, item.limit)
            relations = _relate_projects(cur)
            progress = _track_progress(cur, item.limit)
            safety = _review_safety(cur, item.limit)
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
        "modular": modular_set,
        "relations": relations,
        "progress": progress,
        "safety": safety,
    }


# ---------------------------------------------------------------------------
# Project relations (policy section 4f)
# ---------------------------------------------------------------------------


def _relate_projects(cur, month: str | None = None) -> list:
    """A project-relations job each month, and again each time another
    RELATIONS_STEP projects have been assessed."""
    cur.execute("SELECT count(DISTINCT project_id) AS n FROM assessments")
    assessed = cur.fetchone()["n"]
    if assessed < research.RELATIONS_MIN_ASSESSED:
        return []
    key = (f"{research.RELATIONS}:{month or modular.month()}:"
           f"{assessed // research.RELATIONS_STEP}")
    if _unit_exists(cur, key):
        return []
    cur.execute(
        """
        SELECT DISTINCT ON (p.id) p.id, p.code, p.name, p.summary, p.serves, p.score,
               c.name AS category, COALESCE(c.track, 'need') AS track
        FROM projects p JOIN assessments a ON a.project_id = p.id
        LEFT JOIN need_categories c ON c.id = p.category_id
        ORDER BY p.id
        """
    )
    rows = sorted(cur.fetchall(), key=lambda r: -(r["score"] or 0))
    projects = [{"code": r["code"], "name": r["name"], "category": r["category"],
                 "track": r["track"], "summary": _clip(r["summary"], 240),
                 **({"serves": r["serves"]} if r["track"] == research.ENABLER else {})}
                for r in rows[:research.RELATIONS_MAX_PROJECTS]]
    cur.execute("SELECT pa.code AS a, pb.code AS b, r.kind FROM project_relations r "
                "JOIN projects pa ON pa.id = r.a JOIN projects pb ON pb.id = r.b "
                "ORDER BY r.id")
    existing = cur.fetchall()
    job = _enqueue(cur, unit_key=key, job_type=research.RELATIONS,
                   priority=research.RELATIONS_PRIORITY,
                   payload=research.relations_input(projects, existing))
    return [job] if job else []


def _store_relations(cur, job, result) -> dict:
    """Relations between known projects; an alternative is stored once
    (a < b). A relation found again gets the newer reason."""
    cur.execute("SELECT id, code FROM projects")
    ids = {r["code"]: r["id"] for r in cur.fetchall()}
    stored = skipped = 0
    for rel in result.relations:
        a, b = ids.get(rel.a), ids.get(rel.b)
        if a is None or b is None or a == b:
            skipped += 1
            continue
        if rel.kind == "alternative" and a > b:
            a, b = b, a
        cur.execute(
            """
            INSERT INTO project_relations (a, b, kind, why, job_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (a, b, kind) DO UPDATE SET
                why = EXCLUDED.why, job_id = EXCLUDED.job_id, updated_at = NOW()
            """,
            (a, b, rel.kind, rel.why, job["id"]),
        )
        stored += 1
    return {"relations": stored, "skipped": skipped}


@app.get("/relations")
def relations():
    """Relations between catalogue projects, for linking them (the wiki)."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.a, pa.code AS a_code, pa.name AS a_name,
                       r.b, pb.code AS b_code, pb.name AS b_name,
                       r.kind, r.why, r.updated_at
                FROM project_relations r
                JOIN projects pa ON pa.id = r.a JOIN projects pb ON pb.id = r.b
                ORDER BY r.a, r.b, r.kind
                """
            )
            return cur.fetchall()


def _need_status(solved_at, requirements) -> str:
    """A need's status: the maintainer's mark, else its weakest requirement."""
    if solved_at:
        return research.SOLVED
    levels = [r.get("level") for r in requirements or [] if r.get("level") in research.LEVELS]
    if not levels:
        return research.STATUS["none"]
    return research.STATUS[min(levels, key=research.LEVELS.index)]


@app.get("/categories")
def categories():
    """Every need category, briefed or not, with its latest brief (or
    null), whether discovery covers it (``focus``), and its progress
    towards done (section 4h): the latest requirement levels, ``status``
    and the maintainer's ``solved_at``. The wiki's browse tree."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.layer, c.name, c.scope, COALESCE(c.track, 'need') AS track,
                       COALESCE(c.layer = ANY(%s) OR c.track = %s, FALSE) AS focus,
                       b.brief, b.created_at AS brief_at, c.solved_at, c.solved_note,
                       g.requirements AS progress, g.summary AS progress_summary,
                       g.created_at AS progress_at
                FROM need_categories c
                LEFT JOIN LATERAL (
                    SELECT brief, created_at FROM need_briefs WHERE category_id = c.id
                    ORDER BY created_at DESC, id DESC LIMIT 1) b ON TRUE
                LEFT JOIN LATERAL (
                    SELECT requirements, summary, created_at FROM need_progress
                    WHERE category_id = c.id ORDER BY created_at DESC, id DESC LIMIT 1) g ON TRUE
                ORDER BY (COALESCE(c.track, 'need') = %s), c.layer NULLS LAST, c.name
                """,
                (list(research.DISCOVERY_LAYERS), research.ENABLER, research.ENABLER),
            )
            rows = cur.fetchall()
    for row in rows:
        row["status"] = _need_status(row["solved_at"], row["progress"])
    return rows


@app.post("/categories/{category_id}/solved")
def mark_solved(category_id: int, item: Solved):
    """The maintainer marks a need substantially solved (or not). Marking
    needs at least one requirement met by a module tested in the real
    world (section 4h)."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM need_categories WHERE id = %s", (category_id,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="no such category")
            if item.solved:
                cur.execute("SELECT requirements FROM need_progress WHERE category_id = %s "
                            "ORDER BY created_at DESC, id DESC LIMIT 1", (category_id,))
                row = cur.fetchone()
                if not row or not any(r.get("level") == "field-tested"
                                      for r in row["requirements"]):
                    raise HTTPException(status_code=409, detail=(
                        "nothing field-tested yet: a need is done when modules tested "
                        "in the real world substantially solve it"))
            cur.execute(
                "UPDATE need_categories SET solved_at = CASE WHEN %s THEN NOW() END, "
                "solved_note = %s WHERE id = %s RETURNING id, solved_at",
                (item.solved, item.note or None, category_id))
            result = cur.fetchone()
        conn.commit()
    return result


# ---------------------------------------------------------------------------
# Progress towards done per need (policy section 4h)
# ---------------------------------------------------------------------------


def _progress_facts(cur, category) -> tuple[list, list, list]:
    """What could meet a need: its screened projects and the enablers
    serving it, the modules of its domain, and the systems using them."""
    cur.execute(
        f"""
        SELECT p.id, p.code, p.name, p.status, {_STAGE_SQL} AS stage, p.summary,
               COALESCE(sc.licence_class, 'unchecked') AS licence_class,
               (SELECT r.fit FROM reviews r WHERE r.project_id = p.id
                ORDER BY r.id DESC LIMIT 1) AS fit
        FROM projects p
        LEFT JOIN LATERAL (SELECT licence_class FROM source_checks WHERE project_id = p.id
                           ORDER BY checked_at DESC, id DESC LIMIT 1) sc ON TRUE
        WHERE (p.category_id = %s OR p.serves ? %s)
          AND EXISTS (SELECT 1 FROM assessments a WHERE a.project_id = p.id)
        ORDER BY p.score DESC NULLS LAST, p.id LIMIT 60
        """,
        (category["id"], category["name"]),
    )
    projects = [{"code": r["code"], "name": r["name"], "stage": r["stage"],
                 "build_pack_complete": r["status"] == "completed",
                 "licence_class": r["licence_class"], "summary": _clip(r["summary"], 300),
                 **({"fit": _clip(r["fit"], 400)} if r["fit"] else {})}
                for r in cur.fetchall()]
    cur.execute("SELECT code, name, maturity, spec FROM modules "
                "WHERE domain = %s OR spec->'domains' ? %s ORDER BY code",
                (category["name"], category["name"]))
    modules = [{"code": r["code"], "name": r["name"], "maturity": r["maturity"],
                "purpose": _clip(r["spec"].get("purpose"), 300)} for r in cur.fetchall()]
    codes = {m["code"] for m in modules}
    cur.execute("SELECT code, name, scenario, modules, spec FROM systems ORDER BY code")
    systems = [{"code": r["code"], "name": r["name"], "scenario": r["scenario"],
                "purpose": _clip(r["spec"].get("purpose"), 300)}
               for r in cur.fetchall()
               if codes & {m.get("module") for m in r["modules"]}]
    return projects, modules, systems


def _track_progress(cur, budget: int) -> list:
    """A need-progress job per briefed focus need when what could meet it
    has changed, at most every PROGRESS_MIN_DAYS days per need."""
    created = []
    for category in _focus_categories(cur):
        if len(created) >= budget:
            break
        cur.execute("SELECT id, brief FROM need_briefs WHERE category_id = %s "
                    "ORDER BY created_at DESC, id DESC LIMIT 1", (category["id"],))
        brief = cur.fetchone()
        if not brief:
            continue
        projects, modules, systems = _progress_facts(cur, category)
        if not projects:
            continue
        fingerprint = modular.spec_hash(json.dumps(
            [brief["id"], [(p["code"], p["stage"], p["licence_class"]) for p in projects],
             [(m["code"], m["maturity"]) for m in modules], [s["code"] for s in systems]],
            sort_keys=True))
        key = f"{research.PROGRESS}:{category['id']}:{fingerprint}"
        if _unit_exists(cur, key):
            continue
        cur.execute("SELECT 1 FROM jobs WHERE job_type = %s AND input->>'category_id' = %s "
                    "AND created_at > NOW() - make_interval(days => %s) LIMIT 1",
                    (research.PROGRESS, str(category["id"]), research.PROGRESS_MIN_DAYS))
        if cur.fetchone():
            continue
        job = _enqueue(cur, unit_key=key, job_type=research.PROGRESS,
                       priority=research.PROGRESS_PRIORITY,
                       payload={"category_id": category["id"],
                                **research.progress_input(category,
                                                          brief["brief"].get("requirements") or [],
                                                          projects, modules, systems)})
        if job:
            created.append(job)
    return created


def _level_cap(kind: str, fact: dict) -> str:
    """The highest level the records support for one reference."""
    if kind == "module":
        return "field-tested" if fact["maturity"] == "tested" else "designed"
    if kind == "system":
        return "designed"
    if fact["status"] == "completed" and fact["licence_class"] in ("open", "share-alike"):
        return "documented"
    return "candidate"


def _store_progress(cur, job, result, summary: str) -> dict:
    """Record a need's requirement levels, each lowered to what the
    records support; references to unknown codes are dropped."""
    inp = job["input"] or {}
    texts = {r["requirement"]: r["text"] for r in inp.get("requirements") or []}
    cur.execute(
        """
        SELECT p.code, p.status, COALESCE(sc.licence_class, 'unchecked') AS licence_class
        FROM projects p
        LEFT JOIN LATERAL (SELECT licence_class FROM source_checks WHERE project_id = p.id
                           ORDER BY checked_at DESC, id DESC LIMIT 1) sc ON TRUE
        """
    )
    facts = {r["code"]: ("project", r) for r in cur.fetchall()}
    cur.execute("SELECT code, maturity FROM modules")
    facts.update({r["code"]: ("module", r) for r in cur.fetchall()})
    cur.execute("SELECT code FROM systems")
    facts.update({r["code"]: ("system", r) for r in cur.fetchall()})
    order = research.LEVELS.index
    out, lowered = [], 0
    for item in result.progress:
        if item.requirement not in texts:
            continue
        met_by = [{"code": m.code, "kind": facts[m.code][0], "how": m.how}
                  for m in item.met_by if m.code in facts]
        cap = max((_level_cap(*facts[m["code"]]) for m in met_by), key=order, default="none")
        level = min(item.level, cap, key=order)
        lowered += level != item.level
        out.append({"requirement": item.requirement, "text": texts[item.requirement],
                    "level": level, "claimed": item.level, "met_by": met_by, "gap": item.gap})
    out.sort(key=lambda r: r["requirement"])
    cur.execute("INSERT INTO need_progress (category_id, requirements, summary, job_id) "
                "VALUES (%s, %s, %s, %s)",
                (inp["category_id"], Jsonb(out), _clip(summary, 3000), job["id"]))
    return {"requirements": len(out), "lowered": lowered}


# ---------------------------------------------------------------------------
# Build pack safety review (policy section 4i)
# ---------------------------------------------------------------------------

#: A build pack larger than this goes to the reviewer as a digest.
SAFETY_MAX_CHARS = 40_000


def _review_safety(cur, budget: int) -> list:
    """A safety-review of each completed build pack, and again whenever
    any of its sections is rewritten."""
    cur.execute(
        """
        SELECT p.id, p.name, p.summary, p.climates,
               (SELECT string_agg(b.id::text, ',' ORDER BY b.id) FROM build_packs b
                WHERE b.project_id = p.id) AS sections
        FROM projects p WHERE p.status = 'completed' ORDER BY p.id
        """
    )
    created = []
    for project in cur.fetchall():
        if len(created) >= budget:
            break
        fingerprint = modular.spec_hash(project["sections"] or "")
        key = f"{research.SAFETY}:{project['id']}:{fingerprint}"
        if _unit_exists(cur, key):
            continue
        cur.execute("SELECT DISTINCT ON (section) section, content FROM build_packs "
                    "WHERE project_id = %s ORDER BY section, created_at DESC, id DESC",
                    (project["id"],))
        build = {r["section"]: r["content"] for r in cur.fetchall()}
        if len(json.dumps(build, default=str)) > SAFETY_MAX_CHARS:
            build = {**_build_digest(cur, project["id"]),
                     **({"test": build["test"]} if "test" in build else {})}
        job = _enqueue(cur, unit_key=key, job_type=research.SAFETY,
                       priority=research.SAFETY_PRIORITY, project_id=project["id"],
                       payload={"fingerprint": fingerprint,
                                **research.safety_input(project, build)})
        if job:
            created.append(job)
    return created


def _store_safety(cur, job, result) -> dict:
    s = result.safety
    cur.execute(
        "INSERT INTO safety_reviews (project_id, verdict, hazards, summary, fingerprint, job_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (job["project_id"], s.verdict, Jsonb([h.model_dump() for h in s.hazards]),
         s.summary, (job["input"] or {}).get("fingerprint", ""), job["id"]))
    return {"verdict": s.verdict, "hazards": len(s.hazards)}


# ---------------------------------------------------------------------------
# Stage 2: the modular set (policy section 4e; modular.py)
# ---------------------------------------------------------------------------


def _clip(text: Any, limit: int) -> str:
    return str(text or "")[:limit]


def _build_digest(cur, project_id) -> dict:
    """The parts of a project's latest build pack a synthesis needs."""
    cur.execute(
        "SELECT DISTINCT ON (section) section, content FROM build_packs "
        "WHERE project_id = %s ORDER BY section, created_at DESC, id DESC",
        (project_id,),
    )
    build = {r["section"]: r["content"] for r in cur.fetchall()}
    out = {}
    if build.get("bom"):
        out["bom"] = [{"part": _clip(i.get("part"), 120), "spec": _clip(i.get("spec"), 120),
                       "unit_cost": i.get("unit_cost"), "currency": i.get("currency")}
                      for i in build["bom"].get("items", [])[:30]]
    if build.get("design"):
        out["design"] = ([_clip(r.get("url"), 200) for r in build["design"].get("repositories", [])]
                         + [f"{_clip(f.get('name'), 80)} ({f.get('kind')})"
                            for f in build["design"].get("files", [])[:15]])
    if build.get("assembly"):
        out["assembly_steps"] = [_clip(s.get("step"), 120)
                                 for s in build["assembly"].get("steps", [])[:20]]
    return out


def _projects_digest(cur, category_id=None, limit=120, detail=False) -> list[dict]:
    """Assessed projects (all, or one category's), most promising first."""
    cur.execute(
        """
        SELECT DISTINCT ON (p.id) p.id, p.name, p.status, p.summary, p.climates,
               p.serves, p.score, c.name AS category, c.track, a.rationale,
               sc.licence, COALESCE(sc.licence_class, 'unchecked') AS licence_class
        FROM projects p JOIN assessments a ON a.project_id = p.id
        LEFT JOIN need_categories c ON c.id = p.category_id
        LEFT JOIN LATERAL (SELECT licence, licence_class FROM source_checks
                           WHERE project_id = p.id
                           ORDER BY checked_at DESC, id DESC LIMIT 1) sc ON TRUE
        WHERE (%s::bigint IS NULL OR p.category_id = %s::bigint)
        ORDER BY p.id, a.created_at DESC
        """,
        (category_id, category_id),
    )
    rows = sorted(cur.fetchall(), key=lambda r: -(r["score"] or 0))[:limit]
    out = []
    for r in rows:
        item = {"name": r["name"], "category": r["category"], "status": r["status"],
                "summary": _clip(r["summary"], 600 if detail else 300),
                "climates": r["climates"], "licence": r["licence"],
                "licence_class": r["licence_class"]}
        if r["track"] == research.ENABLER:
            item["serves"] = r["serves"]
        if detail:
            cur.execute("SELECT finding FROM findings WHERE project_id = %s "
                        "ORDER BY confidence DESC NULLS LAST, id LIMIT 8", (r["id"],))
            item["rationale"] = _clip(r["rationale"], 500)
            item["findings"] = [_clip(f["finding"], 300) for f in cur.fetchall()]
        item.update(_build_digest(cur, r["id"]))
        if not detail:
            item.pop("assembly_steps", None)
            item["bom"] = [b["part"] for b in item.get("bom", [])][:12]
        out.append(item)
    return out


def _standards_digest(cur, approved_only=True) -> list[dict]:
    cur.execute(
        "SELECT code, name, kind, spec, status FROM standards "
        + ("WHERE status = 'approved' " if approved_only else "WHERE status <> 'rejected' ")
        + "ORDER BY code"
    )
    return [{"code": r["code"], "name": r["name"], "kind": r["kind"],
             "spec": _clip(r["spec"], 600), **({} if approved_only else {"status": r["status"]})}
            for r in cur.fetchall()]


def _modules_digest(cur, limit=150) -> list[dict]:
    cur.execute("SELECT code, name, kind, domain, maturity, cost_eu, cost_low_income, spec "
                "FROM modules ORDER BY domain, code LIMIT %s", (limit,))
    return [{"code": r["code"], "name": r["name"], "kind": r["kind"], "domain": r["domain"],
             "maturity": r["maturity"], "purpose": _clip(r["spec"].get("purpose"), 240),
             "interfaces": [{"standard": i.get("standard"), "role": i.get("role")}
                            for i in r["spec"].get("interfaces", [])],
             "cost_eu": r["cost_eu"], "cost_low_income": r["cost_low_income"]}
            for r in cur.fetchall()]


def _wave_pending(cur, job_type: str, month: str) -> bool:
    """A job of this type for this month is still queued or running."""
    cur.execute("SELECT 1 FROM jobs WHERE job_type = %s AND unit_key LIKE %s "
                "AND status IN ('queued', 'running') LIMIT 1",
                (job_type, f"%:{month}"))
    return cur.fetchone() is not None


def _modular_set(cur, budget: int, month: str | None = None) -> list:
    """Stage 2, one bounded step (policy section 4e): a verification of each
    proposed standard (again whenever its spec changes); standards, then
    modules per domain once any standard is approved, then systems per
    scenario after the month's modules, then the roadmap after the month's
    systems. Each of those kinds runs once a month."""
    month = month or modular.month()
    created = []

    def enqueue(key, job_type, payload, priority=modular.PRIORITY):
        if len(created) >= budget or _unit_exists(cur, key):
            return
        job = _enqueue(cur, unit_key=key, job_type=job_type,
                       priority=priority, payload=payload)
        if job:
            created.append(job)

    # Proposed standards are verified (again whenever the spec changes);
    # approved ones again every REVERIFY_DAYS, against current editions.
    period = int(time.time() // (modular.REVERIFY_DAYS * 86400))
    cur.execute("SELECT id, code, name, kind, spec, rationale, verified_hash, status, "
                "(verified_at IS NULL OR verified_at < NOW() - make_interval(days => %s)) "
                "AS stale FROM standards WHERE status IN ('proposed', 'approved') ORDER BY id",
                (modular.REVERIFY_DAYS,))
    for standard in cur.fetchall():
        digest = modular.spec_hash(standard["spec"])
        payload = {"standard_id": standard["id"], "spec_hash": digest,
                   **modular.verification_input(standard)}
        if standard["status"] == "proposed" and standard["verified_hash"] != digest:
            enqueue(f"{modular.VERIFY}:{standard['id']}:{digest}", modular.VERIFY,
                    payload, priority=modular.VERIFY_PRIORITY)
        elif standard["status"] == "approved" and standard["stale"]:
            enqueue(f"{modular.VERIFY}:{standard['id']}:{digest}:{period}", modular.VERIFY,
                    {**payload, "reverification": True}, priority=modular.VERIFY_PRIORITY)

    cur.execute("SELECT count(DISTINCT project_id) AS n FROM assessments")
    if cur.fetchone()["n"] < modular.MIN_ASSESSED_FOR_STANDARDS:
        return created
    enqueue(f"{modular.STANDARDS}:{month}", modular.STANDARDS,
            modular.standards_input(_projects_digest(cur), _standards_digest(cur, False)))

    standards = _standards_digest(cur)
    if not standards:
        return created  # modules wait for the maintainer's first approvals

    # Monthly fit review of the standards approved at least a week ago.
    adoption = _adoption(cur)
    cur.execute("SELECT code, name, kind, spec, decided_at FROM standards "
                "WHERE status = 'approved' AND decided_at < NOW() - INTERVAL '7 days' "
                "ORDER BY code")
    settled = [{"code": r["code"], "name": r["name"], "kind": r["kind"],
                "spec": _clip(r["spec"], 1200), "approved": str(r["decided_at"])[:10],
                "adoption": adoption.get(r["code"], {"modules": 0, "systems": 0})}
               for r in cur.fetchall()]
    if settled:
        enqueue(f"{modular.REVIEW}:{month}", modular.REVIEW,
                {"adoption": {s["code"]: s["adoption"] for s in settled},
                 **modular.standards_review_input(settled, _projects_digest(cur),
                                                  _modules_digest(cur))},
                priority=modular.REVIEW_PRIORITY)

    cur.execute("SELECT supersedes, code FROM standards "
                "WHERE status = 'approved' AND supersedes IS NOT NULL")
    superseded = {r["supersedes"]: r["code"] for r in cur.fetchall()}
    for category in _focus_categories(cur):
        cur.execute("SELECT count(DISTINCT a.project_id) AS n FROM assessments a "
                    "JOIN projects p ON p.id = a.project_id WHERE p.category_id = %s",
                    (category["id"],))
        if cur.fetchone()["n"] < modular.MIN_ASSESSED_PER_DOMAIN:
            continue
        key = f"{modular.MODULES}:category:{category['id']}:{month}"
        if _unit_exists(cur, key):
            continue
        enqueue(key, modular.MODULES, {
            "category_id": category["id"],
            **modular.module_input(category, _latest_brief(cur, category["id"]),
                                   _projects_digest(cur, category["id"], 25, detail=True),
                                   standards, _modules_digest(cur), superseded)})

    if _wave_pending(cur, modular.MODULES, month):
        return created
    cur.execute("SELECT count(*) AS n FROM modules")
    if cur.fetchone()["n"] < modular.MIN_MODULES_FOR_SYSTEMS:
        return created
    briefs = _briefs_digest(cur)
    for scenario in modular.SCENARIOS:
        key = f"{modular.SYSTEMS}:{scenario}:{month}"
        if not _unit_exists(cur, key):
            enqueue(key, modular.SYSTEMS, {
                "scenario": scenario,
                **modular.system_input(scenario, briefs, _modules_digest(cur), standards)})

    if _wave_pending(cur, modular.SYSTEMS, month):
        return created
    cur.execute("SELECT code, name, scenario, cost_eu, cost_low_income, modules, spec "
                "FROM systems ORDER BY scenario, code")
    systems = [{"code": r["code"], "name": r["name"], "scenario": r["scenario"],
                "purpose": _clip(r["spec"].get("purpose"), 300),
                "modules": [m.get("module") for m in r["modules"]],
                "missing_modules": r["spec"].get("missing_modules", []),
                "cost_eu": r["cost_eu"], "cost_low_income": r["cost_low_income"]}
               for r in cur.fetchall()]
    if not systems:
        return created
    key = f"{modular.ROADMAP}:{month}"
    if not _unit_exists(cur, key):
        cur.execute("SELECT version, summary, steps FROM roadmaps WHERE status = 'approved' "
                    "ORDER BY created_at DESC LIMIT 1")
        previous = cur.fetchone()
        cur.execute("SELECT code, profile FROM sites ORDER BY code")
        sites = [{"code": r["code"], **r["profile"]} for r in cur.fetchall()]
        enqueue(key, modular.ROADMAP, {
            "version": month,
            **modular.roadmap_input(month, systems, _modules_digest(cur), standards,
                                    previous, sites)})
    return created


def _briefs_digest(cur) -> list[dict]:
    cur.execute(
        """
        SELECT DISTINCT ON (c.id) c.name, c.layer, c.track, b.brief
        FROM need_categories c JOIN need_briefs b ON b.category_id = c.id
        ORDER BY c.id, b.created_at DESC, b.id DESC
        """
    )
    return [{"category": r["name"], "layer": r["layer"], "track": r["track"],
             "requirements": (r["brief"].get("requirements") or [])[:6],
             "constraints": (r["brief"].get("constraints") or [])[:4]}
            for r in cur.fetchall()]


def _adoption(cur) -> dict[str, dict]:
    """Per standard code: the modules that use it and the systems built
    from those modules, counted from the records."""
    cur.execute("SELECT code, standards FROM modules")
    uses = {r["code"]: set(r["standards"]) for r in cur.fetchall()}
    out: dict[str, dict] = {}
    for module, codes in uses.items():
        for code in codes:
            out.setdefault(code, {"modules": [], "systems": set()})["modules"].append(module)
    cur.execute("SELECT code, modules FROM systems")
    for system in cur.fetchall():
        for m in system["modules"]:
            for code in uses.get(m.get("module"), ()):
                out[code]["systems"].add(system["code"])
    return {code: {"modules": len(a["modules"]), "module_codes": sorted(a["modules"])[:20],
                   "systems": len(a["systems"])} for code, a in out.items()}


def _upsert_standard(cur, job_id, s, supersedes: str | None = None) -> str:
    """Insert a proposed standard, or refresh one still proposed; a
    decided standard (approved, rejected or superseded) is never
    overwritten. ``supersedes`` names the approved standard a revision
    would replace."""
    cur.execute(
        """
        INSERT INTO standards (code, name, kind, spec, rationale, used_by, job_id, supersedes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (code) DO UPDATE SET
            name = EXCLUDED.name, kind = EXCLUDED.kind, spec = EXCLUDED.spec,
            rationale = EXCLUDED.rationale, used_by = EXCLUDED.used_by,
            job_id = EXCLUDED.job_id,
            supersedes = COALESCE(EXCLUDED.supersedes, standards.supersedes),
            updated_at = NOW()
        WHERE standards.status = 'proposed'
        RETURNING status
        """,
        (s.code, s.name, s.kind, s.spec, s.rationale, Jsonb(s.used_by), job_id, supersedes),
    )
    row = cur.fetchone()
    return row["status"] if row else "kept"


def _store_modular(cur, job, result) -> dict:
    job_type, job_id, inp = job["job_type"], job["id"], job["input"] or {}
    if job_type == modular.VERIFY:
        # Recorded against the spec that was verified; a spec changed since
        # stays unverified and gets a new verification. An approved
        # standard's yearly re-verification is recorded the same way.
        cur.execute(
            "UPDATE standards SET verification = %s, verified_hash = %s, verified_at = NOW() "
            "WHERE id = %s AND status IN ('proposed', 'approved') RETURNING id",
            (Jsonb(result.verification.model_dump()), inp.get("spec_hash"),
             inp.get("standard_id")),
        )
        return {"verified": cur.fetchone() is not None,
                "outcome": result.verification.outcome}

    if job_type == modular.STANDARDS:
        statuses = [_upsert_standard(cur, job_id, s) for s in result.standards]
        return {"standards": len(statuses)}

    if job_type == modular.REVIEW:
        # Each approved standard's fit; a revision becomes a proposal that
        # supersedes it once approved (after its own verification).
        adoption = inp.get("adoption") or {}
        month = (job.get("unit_key") or "").rsplit(":", 1)[-1] or modular.month()
        stored = revisions = 0
        for review in result.reviews:
            cur.execute("SELECT id FROM standards WHERE code = %s AND status = 'approved'",
                        (review.code,))
            row = cur.fetchone()
            if row is None:
                continue
            revision = None
            if review.revision is not None:
                rev = review.revision
                if rev.code == review.code:
                    rev = rev.model_copy(update={"code": modular.slug(f"{rev.code}-rev-{month}")})
                if _upsert_standard(cur, job_id, rev, supersedes=review.code) != "kept":
                    revision, revisions = rev.code, revisions + 1
            cur.execute(
                "INSERT INTO standard_reviews (standard_id, month, adoption, conflicts, "
                "recommendation, rationale, revision, job_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (row["id"], month, Jsonb(adoption.get(review.code) or {}),
                 Jsonb([c.model_dump() for c in review.conflicts]), review.recommendation,
                 review.rationale, revision, job_id))
            stored += 1
        return {"reviews": stored, "revisions": revisions}

    if job_type == modular.MODULES:
        cur.execute("SELECT id, lower(name) AS name FROM projects WHERE category_id = %s",
                    (inp.get("category_id"),))
        by_name = {r["name"]: r["id"] for r in cur.fetchall()}
        for m in result.modules:
            for s in m.new_standards:
                _upsert_standard(cur, job_id, s)
            projects = sorted({by_name[n.lower()] for n in m.source_projects
                               if n.lower() in by_name})
            spec = m.model_dump(exclude={"new_standards"})
            cur.execute("SELECT maturity, projects, spec FROM modules WHERE code = %s", (m.code,))
            old = cur.fetchone()
            maturity = m.maturity
            if old:
                if modular.MATURITY.index(old["maturity"]) > modular.MATURITY.index(maturity):
                    maturity = old["maturity"]  # built/tested come from trials
                projects = sorted(set(projects) | set(old["projects"]))
                spec["domains"] = sorted(set(old["spec"].get("domains", [])) | {inp["domain"]})
            else:
                spec["domains"] = [inp["domain"]]
            cur.execute(
                """
                INSERT INTO modules (code, name, kind, domain, maturity, cost_eu,
                                     cost_low_income, standards, projects, spec, job_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name, kind = EXCLUDED.kind, maturity = EXCLUDED.maturity,
                    cost_eu = EXCLUDED.cost_eu, cost_low_income = EXCLUDED.cost_low_income,
                    standards = EXCLUDED.standards, projects = EXCLUDED.projects,
                    spec = EXCLUDED.spec, job_id = EXCLUDED.job_id, updated_at = NOW()
                """,
                (m.code, m.name, m.kind, inp["domain"], maturity, m.cost_eu,
                 m.cost_low_income, Jsonb(sorted({i.standard for i in m.interfaces})),
                 Jsonb(projects), Jsonb(spec), job_id),
            )
        return {"modules": len(result.modules)}

    if job_type == modular.SYSTEMS:
        scenario = inp["scenario"]
        for s in result.systems:
            code = s.code if s.code.startswith(scenario) else modular.slug(f"{scenario}-{s.code}")
            cur.execute(
                """
                INSERT INTO systems (code, name, scenario, cost_eu, cost_low_income,
                                     modules, spec, job_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name, cost_eu = EXCLUDED.cost_eu,
                    cost_low_income = EXCLUDED.cost_low_income, modules = EXCLUDED.modules,
                    spec = EXCLUDED.spec, job_id = EXCLUDED.job_id, updated_at = NOW()
                """,
                (code, s.name, scenario, s.cost_eu, s.cost_low_income,
                 Jsonb([m.model_dump() for m in s.modules]), Jsonb(s.model_dump()), job_id),
            )
        return {"systems": len(result.systems)}

    # roadmap-revision: a new proposed version; older proposals are superseded.
    version = inp.get("version") or modular.month()
    cur.execute("SELECT count(*) AS n FROM roadmaps WHERE version LIKE %s", (f"{version}%",))
    n = cur.fetchone()["n"]
    version = version if n == 0 else f"{version}-{n + 1}"
    cur.execute("UPDATE roadmaps SET status = 'superseded' WHERE status = 'proposed'")
    road = result.roadmap
    cur.execute(
        "INSERT INTO roadmaps (version, summary, steps, job_id) VALUES (%s, %s, %s, %s)",
        (version, road.summary, Jsonb([s.model_dump() for s in road.steps]), job_id),
    )
    return {"roadmap": version}


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
                       COALESCE(c.track, 'need') AS track, p.serves, p.climates,
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
                       ) AS updated_at,
                       sc.licence, COALESCE(sc.licence_class, 'unchecked') AS licence_class,
                       sc.last_activity, sc.archived AS repository_archived,
                       sc.checked_at AS sources_checked_at,
                       (SELECT verdict FROM safety_reviews WHERE project_id = p.id
                        ORDER BY created_at DESC, id DESC LIMIT 1) AS safety,
                       (SELECT count(*) FROM jsonb_array_elements(sc.links) l
                        WHERE l->>'state' IN ('broken', 'unreachable')) AS broken_links
                FROM projects p LEFT JOIN need_categories c ON c.id = p.category_id
                LEFT JOIN LATERAL (SELECT * FROM source_checks WHERE project_id = p.id
                                   ORDER BY checked_at DESC, id DESC LIMIT 1) sc ON TRUE
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
                       c.scope, c.track, b.brief, b.created_at
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
                       COALESCE(c.track, 'need') AS track, p.serves, p.climates,
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
                "check": next(iter(rows(
                    "SELECT links, licence, licence_class, licence_source, repository, "
                    "last_activity, archived, checked_at FROM source_checks "
                    "WHERE project_id = %s ORDER BY checked_at DESC, id DESC LIMIT 1")), None),
                "safety": next(iter(rows(
                    "SELECT verdict, hazards, summary, created_at FROM safety_reviews "
                    "WHERE project_id = %s ORDER BY created_at DESC, id DESC LIMIT 1")), None),
            }


@app.get("/standards")
def get_standards():
    """Interface standards, proposed and decided (section 4e), with their
    verification (``verified``: the current spec was verified), what they
    supersede or are superseded by, their adoption counted from the
    records, and the latest fit review."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.id, s.code, s.name, s.kind, s.spec, s.rationale, s.used_by,
                       s.status, s.decision_note, s.decided_at, s.verification,
                       s.verified_hash, s.verified_at, s.created_at, s.updated_at,
                       s.supersedes,
                       (SELECT n.code FROM standards n WHERE n.supersedes = s.code
                        AND n.status <> 'rejected' ORDER BY n.id DESC LIMIT 1) AS superseded_by,
                       (SELECT to_jsonb(r) - 'standard_id' - 'job_id' FROM standard_reviews r
                        WHERE r.standard_id = s.id
                        ORDER BY r.created_at DESC, r.id DESC LIMIT 1) AS review
                FROM standards s ORDER BY s.kind, s.code
                """
            )
            rows = cur.fetchall()
            adoption = _adoption(cur)
    for row in rows:
        row["verified"] = row.pop("verified_hash") == modular.spec_hash(row["spec"])
        row["adoption"] = adoption.get(row["code"], {"modules": 0, "module_codes": [],
                                                     "systems": 0})
    return rows


def _decide_on(table: str, item_id: int, item: Decision) -> dict:
    with db() as conn:
        with conn.cursor() as cur:
            if table == "roadmaps" and item.decision == "approved":
                # One approved roadmap at a time: the older one is superseded.
                cur.execute("UPDATE roadmaps SET status = 'superseded' "
                            "WHERE status = 'approved' AND id <> %s", (item_id,))
            cur.execute(
                f"UPDATE {table} SET status = %s, decision_note = %s, decided_at = NOW() "
                f"WHERE id = %s RETURNING id, status",
                (item.decision, item.note or None, item_id),
            )
            result = cur.fetchone()
        conn.commit()
    if result is None:
        raise HTTPException(status_code=404, detail="not found")
    return result


@app.post("/standards/{standard_id}/decision")
def decide_standard(standard_id: int, item: Decision):
    """The maintainer approves or rejects a proposed standard; modules are
    specified only against approved ones. Approval needs a verification of
    the current spec (standard-verification); rejection does not.
    Approving a revision supersedes the standard it replaces; rejecting an
    approved standard retires it."""
    if item.decision == "approved":
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT spec, verified_hash FROM standards WHERE id = %s",
                            (standard_id,))
                row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        if row["verified_hash"] != modular.spec_hash(row["spec"]):
            raise HTTPException(status_code=409,
                                detail="not verified yet: approve after its verification")
    result = _decide_on("standards", standard_id, item)
    if item.decision == "approved":
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE standards SET status = 'superseded', decided_at = NOW() "
                    "WHERE status = 'approved' AND code = (SELECT supersedes FROM standards "
                    "WHERE id = %s)", (standard_id,))
            conn.commit()
    return result


@app.get("/modules")
def get_modules():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, code, name, kind, domain, maturity, cost_eu, "
                        "cost_low_income, standards, projects, spec, created_at, updated_at "
                        "FROM modules ORDER BY domain, code")
            return cur.fetchall()


@app.get("/systems")
def get_systems():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, code, name, scenario, cost_eu, cost_low_income, "
                        "modules, spec, created_at, updated_at FROM systems "
                        "ORDER BY scenario, code")
            return cur.fetchall()


@app.get("/roadmaps")
def get_roadmaps():
    """Roadmap versions, newest first (proposed, approved, superseded)."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, version, status, summary, steps, decision_note, "
                        "decided_at, created_at FROM roadmaps ORDER BY created_at DESC, id DESC")
            return cur.fetchall()


@app.post("/roadmaps/{roadmap_id}/decision")
def decide_roadmap(roadmap_id: int, item: Decision):
    return _decide_on("roadmaps", roadmap_id, item)


@app.post("/sites")
def set_site(item: SiteIn):
    """A proving-ground profile, set from a private file on the host; it
    reaches only roadmap revisions."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sites (code, profile) VALUES (%s, %s) ON CONFLICT (code) "
                "DO UPDATE SET profile = EXCLUDED.profile, updated_at = NOW() "
                "RETURNING code, updated_at",
                (item.code, Jsonb(item.profile)),
            )
            result = cur.fetchone()
        conn.commit()
    return result


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


#: A project's sources are checked again after this many days.
CHECK_EVERY_DAYS = 14


@app.get("/checks/pending")
def checks_pending(limit: int = 20):
    """Projects whose sources are due a check (never checked, or not for
    CHECK_EVERY_DAYS), most advanced first, with the URLs to check and the
    licences reported for them (build pack design section, archives)."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.code, p.name, p.source_uris
                FROM projects p
                LEFT JOIN LATERAL (SELECT max(checked_at) AS at FROM source_checks
                                   WHERE project_id = p.id) c ON TRUE
                WHERE c.at IS NULL OR c.at < NOW() - make_interval(days => %s)
                ORDER BY c.at NULLS FIRST, p.status = 'active' DESC,
                         p.score DESC NULLS LAST, p.id
                LIMIT %s
                """,
                (CHECK_EVERY_DAYS, max(1, min(limit, 100))),
            )
            projects = cur.fetchall()
            for p in projects:
                cur.execute(
                    "SELECT content FROM build_packs WHERE project_id = %s AND section = 'design' "
                    "ORDER BY created_at DESC, id DESC LIMIT 1", (p["id"],))
                design = (cur.fetchone() or {}).get("content") or {}
                p["reported"] = [{"url": r.get("url"), "licence": r.get("licence")}
                                 for r in design.get("repositories") or [] if r.get("url")]
                cur.execute(
                    "SELECT source_uri AS url, licence FROM design_archives "
                    "WHERE project_id = %s AND licence IS NOT NULL AND licence <> 'unknown'",
                    (p["id"],))
                p["reported"] += cur.fetchall()
                p["urls"] = list(dict.fromkeys(
                    [u for u in p.pop("source_uris") or [] if isinstance(u, str)]
                    + [r["url"] for r in p["reported"]]))[:20]
    return projects


@app.post("/projects/{project_id}/check")
def record_check(project_id: int, item: SourceCheck):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM projects WHERE id = %s", (project_id,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="no such project")
            cur.execute(
                """
                INSERT INTO source_checks (project_id, links, licence, licence_class,
                    licence_source, repository, last_activity, archived)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, licence_class, checked_at
                """,
                (project_id, Jsonb([link.model_dump() for link in item.links]), item.licence,
                 item.licence_class, item.licence_source, item.repository,
                 item.last_activity, item.archived),
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
            enabler = bool(row) and (row["input"] or {}).get("track") == research.ENABLER
            need_layers = _need_layers(cur) if enabler else {}
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
        stage2 = (modular.validate(job_type, item.output)
                  if job_type in modular.JOB_TYPES else None)
        relations = (research.RelationsResult(**item.output)
                     if job_type == research.RELATIONS else None)
        progress = (research.ProgressResult(**item.output)
                    if job_type == research.PROGRESS else None)
        safety = (research.SafetyResult(**item.output)
                  if job_type == research.SAFETY else None)
        if assessment is not None:
            # Only an enabler lists the needs it serves (section 2a).
            assessment.serves = list(dict.fromkeys(
                s for s in assessment.serves if s in need_layers)) if enabler else []
            if enabler and not assessment.serves:
                raise ValueError("an enabler's assessment must list the need "
                                 "categories it serves, by name")
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
                elif stage2 is not None:
                    extra = _store_modular(cur, result, stage2)
                elif relations is not None:
                    extra = _store_relations(cur, result, relations)
                elif progress is not None:
                    extra = _store_progress(cur, result, progress,
                                            str(item.output.get("summary") or ""))
                elif safety is not None:
                    extra = _store_safety(cur, result, safety)
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
    if assessment.serves:  # an enabler: it takes the layer of what it serves
        layer = research.enabler_layer(assessment.serves, _need_layers(cur))
        cur.execute("UPDATE projects SET maslow_level = %s, serves = %s WHERE id = %s",
                    (layer, Jsonb(assessment.serves), project_id))
    else:
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
    cur.execute("UPDATE projects SET score = %s, climates = %s WHERE id = %s",
                (composite, Jsonb(assessment.climates), project_id))
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
    if review.climates:  # the review's view supersedes the screening's
        cur.execute("UPDATE projects SET climates = %s WHERE id = %s",
                    (Jsonb(review.climates), project_id))
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
