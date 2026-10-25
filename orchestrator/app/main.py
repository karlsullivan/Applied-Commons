import json
import os
import re
import time
from datetime import datetime
from typing import Any

import audit
import graph
import library
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
    "catalogue-audit": "apollo-hermes",
    "graph-review": "apollo-hermes",
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


class ProjectPriority(BaseModel):
    priority: bool
    note: str = Field(default="", max_length=2000)


class Suggestion(research.Candidate):
    """A project the maintainer names for a category (policy section 4d)."""
    category_id: int
    priority: bool = True
    note: str = Field(default="", max_length=2000)


class FocusedDiscovery(BaseModel):
    """A discovery the maintainer asks for in a category (section 4d);
    with ``pick``, its best assessed candidate becomes a priority."""
    focus: str = Field(min_length=3, max_length=500)
    note: str = Field(default="", max_length=2000)
    pick: bool = True


class ProjectMerge(BaseModel):
    """Merge a duplicate into the project that replaces it (section 4d);
    ``category_id`` optionally moves the survivor to a better-fitting
    primary category."""
    into: int
    category_id: int | None = None
    note: str = Field(default="", max_length=2000)


class Solved(BaseModel):
    """The maintainer's judgement that a need is substantially solved."""
    solved: bool
    note: str = Field(default="", max_length=2000)


class Decision(BaseModel):
    """The maintainer's decision on a standard or a roadmap version."""
    decision: str = Field(pattern="^(approved|rejected|proposed)$")
    note: str = Field(default="", max_length=2000)


class StandardProposal(modular.StandardIn):
    """A standard the maintainer proposes; ``supersedes`` names an approved
    standard it would replace once approved."""
    supersedes: str | None = None
    note: str = Field(default="", max_length=2000)


class MaintainerVerification(BaseModel):
    """A verification the maintainer supplies instead of waiting for a
    standard-verification job; ``spec_hash`` is the spec it checked."""
    verification: modular.Verification
    verified_by: str = Field(min_length=3, max_length=200)
    spec_hash: str = Field(min_length=16, max_length=16)


class StandardRevision(BaseModel):
    """The maintainer's revision of a proposed standard; fields left out
    keep their value."""
    spec: str = Field(min_length=10, max_length=3000)
    name: str | None = Field(default=None, min_length=3, max_length=160)
    rationale: str | None = Field(default=None, min_length=10, max_length=2000)
    used_by: list[str] | None = Field(default=None, max_length=20)
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


class PreservationRecord(BaseModel):
    """A public archive's copy of a design source (section 4c)."""
    source_uri: str = Field(pattern=r"^https://", max_length=2000)
    service: str = Field(pattern="^(software-heritage|wayback)$")
    status: str = Field(pattern="^(held|requested|failed)$")
    reference: str | None = Field(default=None, max_length=2000)
    captured_at: datetime | None = None
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
    licence_class: str = Field(
        pattern="^(open|share-alike|non-commercial|restricted|none|unknown)$")
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


@app.post("/projects/{project_id}/priority")
def prioritise_project(project_id: int, item: ProjectPriority):
    """Maintainer priority (policy section 4d): a priority project is
    admitted once assessed, outside the slot limit, and its jobs are claimed
    first. Clearing it does not undo an admission."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE projects SET priority = %s, priority_note = %s,
                       prioritised_at = CASE WHEN %s THEN NOW() ELSE prioritised_at END
                WHERE id = %s
                RETURNING id, code, name, status, priority, priority_note, prioritised_at;
                """,
                (item.priority, item.note or None, item.priority, project_id),
            )
            result = cur.fetchone()
        conn.commit()
    if result is None:
        raise HTTPException(status_code=404, detail="no such project")
    return result


@app.post("/projects/suggest")
def suggest_project(item: Suggestion):
    """A project the maintainer names (policy section 4d). It enters as a
    candidate and is assessed like a discovered one; a project already in
    the category under that name is returned (and prioritised if asked)
    instead of being added twice."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT layer, name FROM need_categories WHERE id = %s",
                        (item.category_id,))
            category = cur.fetchone()
            if category is None:
                raise HTTPException(status_code=404, detail="no such category")
            cur.execute(
                """
                INSERT INTO projects (code, name, maslow_level, domain, status, category_id,
                                      summary, source_uris, priority, priority_note,
                                      prioritised_at)
                VALUES (%s, %s, %s, %s, 'candidate', %s, %s, %s, %s, %s,
                        CASE WHEN %s THEN NOW() END)
                ON CONFLICT DO NOTHING
                RETURNING id, code, name, status, priority;
                """,
                (f"c{item.category_id}-{research.slug(item.name)}", item.name,
                 category["layer"], category["name"], item.category_id, item.summary,
                 Jsonb(item.source_uris), item.priority, item.note or None, item.priority),
            )
            result = cur.fetchone()
            added = result is not None
            if not added:
                cur.execute(
                    "SELECT id, code, name, status, priority FROM projects "
                    "WHERE (category_id = %s AND lower(name) = lower(%s)) OR code = %s",
                    (item.category_id, item.name,
                     f"c{item.category_id}-{research.slug(item.name)}"))
                result = cur.fetchone()
                if result and item.priority and not result["priority"]:
                    cur.execute(
                        "UPDATE projects SET priority = TRUE, priority_note = %s, "
                        "prioritised_at = NOW() WHERE id = %s",
                        (item.note or None, result["id"]))
                    result["priority"] = True
        conn.commit()
    return {**result, "added": added}


#: A project listed under a category: its own, or one it was also listed
#: under (project_categories). Format with the project alias and the
#: category placeholder or column.
IN_CATEGORY = ("({p}.category_id = {c} OR EXISTS (SELECT 1 FROM project_categories pc "
               "WHERE pc.project_id = {p}.id AND pc.category_id = {c}))")


def _also_list(cur, project_id, category_id, reason) -> None:
    """List a project under another category too (not its own)."""
    cur.execute(
        "INSERT INTO project_categories (project_id, category_id, reason) "
        "SELECT %s, %s, %s WHERE NOT EXISTS (SELECT 1 FROM projects "
        "WHERE id = %s AND category_id = %s) ON CONFLICT DO NOTHING",
        (project_id, category_id, reason[:2000], project_id, category_id))


@app.post("/projects/{project_id}/merge")
def merge_project(project_id: int, item: ProjectMerge):
    """Merge a duplicate project into the one that replaces it (policy
    section 4d). The duplicate keeps its records, with status 'merged';
    the survivor is listed under the duplicate's categories too, gains its
    sources, relations, priority and the more basic of their layers, and
    the duplicate's queued jobs are stopped."""
    if item.into == project_id:
        raise HTTPException(status_code=422, detail="a project cannot be merged into itself")
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM projects WHERE id = ANY(%s) FOR UPDATE",
                        ([project_id, item.into],))
            rows = {r["id"]: r for r in cur.fetchall()}
            dup, keep = rows.get(project_id), rows.get(item.into)
            if dup is None or keep is None:
                raise HTTPException(status_code=404, detail="no such project")
            if dup["merged_into"] or keep["merged_into"]:
                raise HTTPException(status_code=409, detail="already merged")
            reason = f"merged from #{dup['id']} {dup['name']}"
            if item.category_id is not None and item.category_id != keep["category_id"]:
                cur.execute("SELECT id, layer, name FROM need_categories WHERE id = %s",
                            (item.category_id,))
                category = cur.fetchone()
                if category is None:
                    raise HTTPException(status_code=404, detail="no such category")
                earlier = keep["category_id"]
                cur.execute("DELETE FROM project_categories WHERE project_id = %s "
                            "AND category_id = %s", (keep["id"], category["id"]))
                cur.execute("UPDATE projects SET category_id = %s, domain = %s, "
                            "maslow_level = LEAST(maslow_level, COALESCE(%s, maslow_level)) "
                            "WHERE id = %s",
                            (category["id"], category["name"], category["layer"], keep["id"]))
                keep["category_id"] = category["id"]
                if earlier:
                    _also_list(cur, keep["id"], earlier, "its earlier primary category")
            if dup["category_id"]:
                _also_list(cur, keep["id"], dup["category_id"], reason)
            cur.execute("SELECT category_id FROM project_categories WHERE project_id = %s",
                        (dup["id"],))
            for row in cur.fetchall():
                _also_list(cur, keep["id"], row["category_id"], reason)
            sources = list(dict.fromkeys(list(keep["source_uris"] or [])
                                         + list(dup["source_uris"] or [])))
            serves = list(dict.fromkeys(list(keep["serves"] or []) + list(dup["serves"] or [])))
            cur.execute(
                "UPDATE projects SET source_uris = %s, serves = %s, "
                "maslow_level = LEAST(maslow_level, %s), "
                "priority = priority OR %s, "
                "prioritised_at = COALESCE(prioritised_at, %s) WHERE id = %s",
                (Jsonb(sources), Jsonb(serves) if serves or keep["serves"] is not None else None,
                 dup["maslow_level"], dup["priority"], dup["prioritised_at"], keep["id"]))
            cur.execute("UPDATE projects SET merged_into = %s WHERE merged_into = %s",
                        (keep["id"], dup["id"]))
            # Relations move to the survivor, minus any it now has with itself
            # or already holds.
            cur.execute("DELETE FROM project_relations r WHERE (r.a = %(d)s AND r.b = %(k)s) "
                        "OR (r.a = %(k)s AND r.b = %(d)s)", {"d": dup["id"], "k": keep["id"]})
            for col, other in (("a", "b"), ("b", "a")):
                cur.execute(
                    f"DELETE FROM project_relations r WHERE r.{col} = %(d)s AND EXISTS ("
                    f"SELECT 1 FROM project_relations s WHERE s.{col} = %(k)s "
                    f"AND s.{other} = r.{other} AND s.kind = r.kind)",
                    {"d": dup["id"], "k": keep["id"]})
                cur.execute(f"UPDATE project_relations SET {col} = %s WHERE {col} = %s",
                            (keep["id"], dup["id"]))
            cur.execute(
                "UPDATE jobs SET status = 'failed', error = %s, completed_at = NOW(), "
                "lease_until = NULL WHERE project_id = %s AND status = 'queued' "
                "RETURNING id",
                (f"project merged into #{keep['id']}", dup["id"]))
            stopped = [r["id"] for r in cur.fetchall()]
            cur.execute("UPDATE projects SET status = 'merged', merged_into = %s, "
                        "merged_at = NOW(), priority = FALSE WHERE id = %s",
                        (keep["id"], dup["id"]))
            _decide(cur, dup["id"], "merge",
                    f"merged into #{keep['id']} {keep['name']}"
                    + (f" ({item.note})" if item.note else ""), research.MERGE_AUTHOR)
        conn.commit()
    return {"id": dup["id"], "merged_into": keep["id"], "stopped_jobs": stopped}


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


def _known_names(cur, category_id) -> list[str]:
    """Names discovery skips in a category: the projects listed under it
    (its own and those also listed there) and the duplicates merged into
    them."""
    listed = IN_CATEGORY.format(p="p", c="%(c)s")
    cur.execute(
        f"SELECT p.id, p.name FROM projects p WHERE {listed} "
        f"UNION SELECT m.id, m.name FROM projects m JOIN projects p ON p.id = m.merged_into "
        f"WHERE {listed} ORDER BY 1",
        {"c": category_id})
    return list(dict.fromkeys(r["name"] for r in cur.fetchall()))


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
        # One waiting scan per category: a new period does not queue a
        # second beside one that has not run yet (duplicates queued before
        # this rule are folded into the oldest). Scans the maintainer asked
        # for on an audit gap are separate and never folded.
        cur.execute("SELECT id FROM jobs WHERE job_type = %s AND status = 'queued' "
                    "AND input->>'category_id' = %s AND unit_key LIKE %s "
                    "ORDER BY created_at, id",
                    (research.DISCOVERY, str(category["id"]),
                     f"{research.DISCOVERY}:category:%"))
        waiting = [r["id"] for r in cur.fetchall()]
        if waiting:
            if len(waiting) > 1:
                cur.execute("DELETE FROM jobs WHERE id = ANY(%s)", (waiting[1:],))
            continue
        brief = _latest_brief(cur, category["id"])
        if brief is None:
            continue
        known = _known_names(cur, category["id"])
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
        ORDER BY p.priority DESC, p.maslow_level NULLS LAST, p.id
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
        ORDER BY p.priority DESC, COALESCE(q.priority, 0.5) DESC, q.id
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


def _apply_priorities(cur) -> list:
    """Maintainer priorities (policy section 4d). A focused discovery with
    pick, once every candidate it found is assessed (or its assessment
    failed for good), makes its best-scoring one a priority, once. A
    priority candidate with an assessment is admitted, outside the slot
    limit and the admission threshold; research and review still decide
    whether it is built."""
    applied = []
    cur.execute(
        """
        SELECT j.id, j.input->'maintainer_focus'->>'focus' AS focus,
               (SELECT p.id FROM projects p JOIN assessments a ON a.project_id = p.id
                WHERE p.discovered_by_job = j.id AND p.status = 'candidate'
                ORDER BY a.composite DESC, p.id LIMIT 1) AS best
        FROM jobs j
        WHERE j.job_type = %s AND j.status = 'completed'
          AND (j.input->'maintainer_focus'->>'pick')::boolean
          AND NOT EXISTS (SELECT 1 FROM projects p WHERE p.discovered_by_job = j.id
                          AND p.prioritised_at IS NOT NULL)
          AND NOT EXISTS (
              SELECT 1 FROM projects p
              WHERE p.discovered_by_job = j.id
                AND NOT EXISTS (SELECT 1 FROM assessments a WHERE a.project_id = p.id)
                AND NOT EXISTS (SELECT 1 FROM jobs aj
                                WHERE aj.unit_key = %s || p.id AND aj.status = 'failed'))
        ORDER BY j.id
        """,
        (research.DISCOVERY, f"{research.ASSESSMENT}:project:"),
    )
    for row in cur.fetchall():
        if row["best"] is None:
            continue
        cur.execute(
            "UPDATE projects SET priority = TRUE, prioritised_at = NOW(), "
            "priority_note = %s WHERE id = %s",
            (f"best assessed candidate of the maintainer's discovery: {row['focus']}"[:2000],
             row["best"]))
        applied.append({"project_id": row["best"], "picked_by_job": row["id"]})
    cur.execute(
        """
        SELECT p.id, p.priority_note FROM projects p
        WHERE p.priority AND p.status = 'candidate'
          AND EXISTS (SELECT 1 FROM assessments a WHERE a.project_id = p.id)
        ORDER BY p.prioritised_at, p.id
        """
    )
    for p in cur.fetchall():
        cur.execute("UPDATE projects SET status = 'active' WHERE id = %s", (p["id"],))
        cur.execute("UPDATE questions SET status = 'open' "
                    "WHERE project_id = %s AND status = 'candidate'", (p["id"],))
        _decide(cur, p["id"], "admit",
                "maintainer priority" + (f" ({p['priority_note']})" if p["priority_note"] else ""),
                research.PRIORITY_AUTHOR)
        applied.append({"project_id": p["id"], "admitted": True})
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
        ORDER BY p.priority DESC, p.score DESC NULLS LAST, p.id
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
        ORDER BY p.priority DESC, p.score DESC NULLS LAST, p.id
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

    0. steers: apply maintainer overrides (build / park), then maintainer
       priorities (admitted once assessed, jobs claimed first);
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
       it changes (section 4i);
    12. audit: a catalogue-audit job each month, listing gaps against
       reference lists for the maintainer to act on or ignore (section 4j).
    13. graph reviews: an independent graph-review of each safety-critical
       or medical capability graph candidate, again when it changes
       (section 4k).

    Every job carries a stable unit key, so repeated runs never enqueue the
    same unit twice; completed or failed units are not re-enqueued. Topics
    come only from the needs taxonomy and from what earlier steps found.
    """
    with db() as conn:
        with conn.cursor() as cur:
            steered = _apply_steers(cur)
            prioritised = _apply_priorities(cur)
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
            audited = _audit_catalogue(cur)
            graph_reviews = _review_graph(cur, item.limit)
        conn.commit()
    return {
        "steered": steered,
        "prioritised": prioritised,
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
        "audit": audited,
        "graph_reviews": graph_reviews,
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
        WHERE p.status <> 'merged'
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


@app.post("/categories/{category_id}/discover")
def discover_focus(category_id: int, item: FocusedDiscovery):
    """A candidate-discovery job the maintainer asks for, focused on one
    topic in a category and claimed ahead of the queue (section 4d). Asking
    again for the same topic returns the existing job."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, layer, name, scope, track FROM need_categories "
                        "WHERE id = %s", (category_id,))
            category = cur.fetchone()
            if category is None:
                raise HTTPException(status_code=404, detail="no such category")
            key = f"{research.DISCOVERY}:maintainer:{category_id}:{research.slug(item.focus)}"
            known = _known_names(cur, category_id)
            payload = research.discovery_input(category, known,
                                               _latest_brief(cur, category_id))
            payload = {"category_id": category_id, **payload,
                       "maintainer_focus": {"focus": item.focus, "note": item.note,
                                            "pick": item.pick},
                       "instructions": (f"Focus this search on a topic the maintainer "
                                        f"asked for: {item.focus}. " + payload["instructions"])}
            job = _enqueue(cur, unit_key=key, job_type=research.DISCOVERY,
                           priority=1.0, payload=payload)
            if job is None:
                cur.execute("SELECT id, job_type, unit_key, priority, status FROM jobs "
                            "WHERE unit_key = %s", (key,))
                job = cur.fetchone()
        conn.commit()
    return job


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
        WHERE (p.category_id = %s OR p.serves ? %s OR EXISTS (
                   SELECT 1 FROM project_categories pc
                   WHERE pc.project_id = p.id AND pc.category_id = %s))
          AND p.status <> 'merged'
          AND EXISTS (SELECT 1 FROM assessments a WHERE a.project_id = p.id)
        ORDER BY p.score DESC NULLS LAST, p.id LIMIT 60
        """,
        (category["id"], category["name"], category["id"]),
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
        payload = {"category_id": category["id"],
                   **research.progress_input(category, brief["brief"].get("requirements") or [],
                                             projects, modules, systems)}
        # One waiting job per need: a change refreshes the oldest waiting
        # job (keeping its age) rather than queuing another beside it.
        cur.execute("SELECT id, unit_key FROM jobs WHERE job_type = %s AND status = 'queued' "
                    "AND input->>'category_id' = %s ORDER BY created_at, id",
                    (research.PROGRESS, str(category["id"])))
        waiting = cur.fetchall()
        if waiting:
            if len(waiting) > 1:
                cur.execute("DELETE FROM jobs WHERE id = ANY(%s)",
                            ([w["id"] for w in waiting[1:]],))
            if waiting[0]["unit_key"] == key:
                continue
            if _unit_exists(cur, key):  # this state was already assessed
                cur.execute("DELETE FROM jobs WHERE id = %s", (waiting[0]["id"],))
            else:
                cur.execute("UPDATE jobs SET unit_key = %s, input = %s WHERE id = %s",
                            (key, Jsonb(payload), waiting[0]["id"]))
            continue
        if _unit_exists(cur, key):
            continue
        cur.execute("SELECT 1 FROM jobs WHERE job_type = %s AND input->>'category_id' = %s "
                    "AND created_at > NOW() - make_interval(days => %s) LIMIT 1",
                    (research.PROGRESS, str(category["id"]), research.PROGRESS_MIN_DAYS))
        if cur.fetchone():
            continue
        job = _enqueue(cur, unit_key=key, job_type=research.PROGRESS,
                       priority=research.PROGRESS_PRIORITY, payload=payload)
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
        WHERE p.status <> 'merged' AND (%s::bigint IS NULL OR p.category_id = %s::bigint
               OR EXISTS (SELECT 1 FROM project_categories pc
                          WHERE pc.project_id = p.id AND pc.category_id = %s::bigint))
        ORDER BY p.id, a.created_at DESC
        """,
        (category_id, category_id, category_id),
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


def _verified_for(row) -> dict | None:
    """The verification of a standard's current spec, if it has one."""
    if row["verification"] and row["verified_hash"] == modular.spec_hash(row["spec"]):
        return row["verification"]
    return None


def _held(row) -> bool:
    """A proposed standard waiting for the maintainer, which a synthesis
    must not rewrite: the maintainer wrote or revised it, or its current
    spec is verified for approval."""
    verification = _verified_for(row)
    return row["revised_at"] is not None or bool(
        verification and verification.get("recommendation") == "approve")


def _standards_digest(cur, approved_only=True) -> list[dict]:
    """Approved standards for modules; for standards-synthesis, every one
    not rejected, a proposed one with its verification's findings when
    they recommend revising it, so the synthesis can revise it."""
    cur.execute(
        "SELECT code, name, kind, spec, status, verification, verified_hash, revised_at "
        "FROM standards "
        + ("WHERE status = 'approved' " if approved_only else "WHERE status <> 'rejected' ")
        + "ORDER BY code"
    )
    digest = []
    for r in cur.fetchall():
        # Modules are specified from an approved standard's text: all of it.
        entry = {"code": r["code"], "name": r["name"], "kind": r["kind"],
                 "spec": r["spec"] if approved_only else _clip(r["spec"], 600)}
        if not approved_only:
            entry["status"] = r["status"]
            verification = _verified_for(r)
            if r["status"] == "proposed" and _held(r):
                entry["held"] = True
            elif (r["status"] == "proposed" and verification
                  and verification.get("recommendation") == "revise"):
                entry["spec"] = r["spec"]
                entry["verification"] = {
                    "recommendation": "revise",
                    "required_controls": [_clip(c, 400) for c in
                                          verification.get("required_controls", [])],
                    "deviations": [{"risk": d.get("risk"),
                                    "deviation": _clip(d.get("deviation"), 400)}
                                   for d in verification.get("deviations", [])],
                }
        digest.append(entry)
    return digest


def _modules_digest(cur, limit=150) -> list[dict]:
    cur.execute("SELECT code, name, kind, domain, maturity, cost_eu, cost_low_income, spec "
                "FROM modules ORDER BY domain, code LIMIT %s", (limit,))
    return [{"code": r["code"], "name": r["name"], "kind": r["kind"], "domain": r["domain"],
             "maturity": r["maturity"], "purpose": _clip(r["spec"].get("purpose"), 240),
             "interfaces": [{"standard": i.get("standard"), "role": i.get("role")}
                            for i in r["spec"].get("interfaces", [])],
             "cost_eu": r["cost_eu"], "cost_low_income": r["cost_low_income"],
             **({"non_commercial": True, "licence": r["spec"].get("licence")}
                if r["spec"].get("non_commercial") else {})}
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
                "spec": r["spec"], "approved": str(r["decided_at"])[:10],
                "adoption": adoption.get(r["code"], {"modules": 0, "systems": 0})}
               for r in cur.fetchall()]
    if settled:
        enqueue(f"{modular.REVIEW}:{month}", modular.REVIEW,
                {"adoption": {s["code"]: s["adoption"] for s in settled},
                 **modular.standards_review_input(settled, _projects_digest(cur),
                                                  _modules_digest(cur))},
                priority=modular.REVIEW_PRIORITY)

    context = _standards_context(cur)
    superseded, pending, rejected = (context["superseded"], context["pending"],
                                     context["rejected"])
    for category in _focus_categories(cur):
        cur.execute("SELECT count(DISTINCT a.project_id) AS n FROM assessments a "
                    "JOIN projects p ON p.id = a.project_id WHERE p.status <> 'merged' AND "
                    + IN_CATEGORY.format(p="p", c="%(c)s"),
                    {"c": category["id"]})
        if cur.fetchone()["n"] < modular.MIN_ASSESSED_PER_DOMAIN:
            continue
        key = f"{modular.MODULES}:category:{category['id']}:{month}"
        if _unit_exists(cur, key):
            continue
        enqueue(key, modular.MODULES, {
            "category_id": category["id"],
            **modular.module_input(category, _latest_brief(cur, category["id"]),
                                   _projects_digest(cur, category["id"], 25, detail=True),
                                   standards, _modules_digest(cur), superseded,
                                   pending, rejected)})

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


def _standards_context(cur) -> dict[str, Any]:
    """The standards a stage 2 job works against: approved ones in full,
    superseded codes with their successors, and those waiting for the
    maintainer or turned down (so modules reference rather than duplicate
    them)."""
    cur.execute("SELECT supersedes, code FROM standards "
                "WHERE status = 'approved' AND supersedes IS NOT NULL")
    superseded = {r["supersedes"]: r["code"] for r in cur.fetchall()}
    cur.execute("SELECT code, name, kind, spec, status, decision_note FROM standards "
                "WHERE status IN ('proposed', 'rejected') ORDER BY code")
    pending, rejected = [], []
    for r in cur.fetchall():
        if r["status"] == "proposed":
            pending.append({"code": r["code"], "name": r["name"], "kind": r["kind"],
                            "spec": _clip(r["spec"], 300)})
        else:
            rejected.append({"code": r["code"], "name": r["name"],
                             "why": _clip(r["decision_note"], 200)})
    return {"approved": _standards_digest(cur), "superseded": superseded,
            "pending": pending, "rejected": rejected}


#: Stage 2 jobs whose standards and modules are read again when claimed.
#: A wave can wait days in the queue, and the maintainer decides standards
#: meanwhile: the October 2026 module wave was queued with 3 approved
#: standards, 16 more were approved before it ran, and it proposed
#: duplicates of them.
REFRESH_ON_CLAIM = (modular.MODULES, modular.SYSTEMS, modular.ROADMAP)


def _refresh_on_claim(cur, job: dict) -> dict:
    """The job with its standards (and, for modules, the existing modules
    and the waiting, rejected and superseded standards) as they are now."""
    if job["job_type"] not in REFRESH_ON_CLAIM:
        return job
    context = _standards_context(cur)
    payload = dict(job["input"] or {})
    payload["approved_standards"] = context["approved"]
    if job["job_type"] == modular.MODULES:
        payload["existing_modules"] = _modules_digest(cur)
        for key, value in (("pending_standards", context["pending"]),
                           ("rejected_standards", context["rejected"]),
                           ("superseded_standards", context["superseded"])):
            if value:
                payload[key] = value
            else:
                payload.pop(key, None)
        if not context["superseded"]:
            payload.pop("superseded_note", None)
    else:
        payload["modules"] = _modules_digest(cur)
    payload["refreshed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cur.execute("UPDATE jobs SET input = %s WHERE id = %s RETURNING *",
                (Jsonb(payload), job["id"]))
    return cur.fetchone()


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
    overwritten, nor is a held one (see ``_held``: the SQL below is the
    same test). ``supersedes`` names the approved standard a revision
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
        WHERE standards.status = 'proposed' AND standards.revised_at IS NULL
          AND NOT COALESCE(standards.verification->>'recommendation' = 'approve'
                           AND standards.verified_hash = left(encode(sha256(
                               convert_to(standards.spec, 'UTF8')), 'hex'), 16), false)
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
        cur.execute("SELECT p.id, p.name, sc.licence, sc.licence_class FROM projects p "
                    "LEFT JOIN LATERAL (SELECT licence, licence_class FROM source_checks "
                    "WHERE project_id = p.id ORDER BY checked_at DESC, id DESC LIMIT 1) sc "
                    "ON TRUE WHERE p.status <> 'merged' AND "
                    + IN_CATEGORY.format(p="p", c="%(c)s"),
                    {"c": inp.get("category_id")})
        by_name = {r["name"].lower(): r for r in cur.fetchall()}
        stored, refused = 0, []
        for m in result.modules:
            sources = [by_name[n.lower()] for n in m.source_projects if n.lower() in by_name]
            # Licence terms are decided here, from the source checks, not
            # taken on the model's word (policy section 9).
            terms = modular.module_terms(m.licence, m.licence_note, sources)
            if "refused" in terms:
                refused.append({"code": m.code, "reason": terms["refused"]})
                continue
            for s in m.new_standards:
                _upsert_standard(cur, job_id, s)
            projects = sorted({s["id"] for s in sources})
            spec = {**m.model_dump(exclude={"new_standards"}), **terms}
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
            stored += 1
        return {"modules": stored, **({"refused": refused} if refused else {})}

    if job_type == modular.SYSTEMS:
        scenario = inp["scenario"]
        cur.execute("SELECT code, spec FROM modules WHERE (spec->>'non_commercial')::boolean")
        nc_modules = {r["code"]: r["spec"].get("licence") for r in cur.fetchall()}
        for s in result.systems:
            code = s.code if s.code.startswith(scenario) else modular.slug(f"{scenario}-{s.code}")
            # Everything built from a non-commercial module is non-commercial.
            nc = sorted({m.module for m in s.modules if m.module in nc_modules})
            spec = {**s.model_dump(), "non_commercial": bool(nc),
                    **({"non_commercial_modules": nc,
                        "licences": sorted({nc_modules[c] for c in nc if nc_modules[c]})}
                       if nc else {})}
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
                 Jsonb([m.model_dump() for m in s.modules]), Jsonb(spec), job_id),
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
                WHERE p.status <> 'merged'
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
        WHEN p.status = 'merged' THEN 'merged'
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
                       p.summary, p.source_uris, p.steer, p.steer_note, p.priority,
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
                           p.created_at, p.steered_at, p.prioritised_at,
                           (SELECT max(created_at) FROM decisions WHERE project_id = p.id),
                           (SELECT max(created_at) FROM assessments WHERE project_id = p.id),
                           (SELECT max(created_at) FROM reviews WHERE project_id = p.id),
                           (SELECT max(created_at) FROM findings WHERE project_id = p.id),
                           (SELECT max(created_at) FROM build_packs WHERE project_id = p.id),
                           (SELECT max(created_at) FROM design_archives WHERE project_id = p.id),
                           (SELECT max(created_at) FROM questions WHERE project_id = p.id),
                           (SELECT max(added_at) FROM project_categories WHERE project_id = p.id)
                       ) AS updated_at,
                       (SELECT COALESCE(jsonb_agg(c2.name ORDER BY c2.layer NULLS LAST, c2.name),
                                        '[]'::jsonb)
                        FROM project_categories pc JOIN need_categories c2 ON c2.id = pc.category_id
                        WHERE pc.project_id = p.id) AS also_in,
                       (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                                   'id', m.id, 'code', m.code, 'name', m.name) ORDER BY m.id),
                                        '[]'::jsonb)
                        FROM projects m WHERE m.merged_into = p.id) AS merged,
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
                WHERE p.status <> 'merged'
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
                       p.score, p.summary, p.source_uris, p.steer, p.steer_note, p.priority,
                       p.merged_into,
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
                "preservation": rows(
                    "WITH me AS (SELECT %s::bigint AS id) "
                    "SELECT source_uri, service, status, reference, captured_at, checked_at "
                    "FROM design_preservation WHERE source_uri IN ("
                    "SELECT source_uri FROM design_archives WHERE project_id = (SELECT id FROM me) "
                    "UNION SELECT repository FROM source_checks "
                    "WHERE project_id = (SELECT id FROM me)) ORDER BY source_uri, service"),
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
                       s.supersedes, s.revised_at, s.revision_note, s.history,
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


@app.post("/standards", status_code=201)
def propose_standard(item: StandardProposal):
    """The maintainer proposes a standard (one a synthesis missed, or a
    revision of an approved one under a new code). It is verified and
    decided like any other, and no synthesis rewrites it."""
    with db() as conn:
        with conn.cursor() as cur:
            if item.supersedes:
                cur.execute("SELECT 1 FROM standards WHERE code = %s AND status = 'approved'",
                            (item.supersedes,))
                if cur.fetchone() is None:
                    raise HTTPException(status_code=409,
                                        detail="supersedes must name an approved standard")
            cur.execute(
                "INSERT INTO standards (code, name, kind, spec, rationale, used_by, "
                "supersedes, revised_at, revision_note) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s) "
                "ON CONFLICT (code) DO NOTHING RETURNING id, code, status",
                (item.code, item.name, item.kind, item.spec, item.rationale,
                 Jsonb(item.used_by), item.supersedes, item.note or None),
            )
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(status_code=409, detail="a standard with this code exists")
    return row


@app.post("/standards/{standard_id}/revision")
def revise_standard(standard_id: int, item: StandardRevision):
    """The maintainer revises a proposed standard in place, for example to
    meet its verification's required controls. The earlier version and its
    verification go to history; the new spec is verified before it can be
    approved, and no synthesis rewrites it. A decided standard changes only
    through a new proposal that supersedes it."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, name, spec, rationale, used_by, verification, "
                        "verified_at, revision_note FROM standards WHERE id = %s FOR UPDATE",
                        (standard_id,))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="not found")
            if row["status"] != "proposed":
                raise HTTPException(status_code=409, detail=(
                    f"standard is {row['status']}: propose a revision under a new code "
                    "(POST /standards with supersedes)"))
            earlier = {"name": row["name"], "spec": row["spec"], "rationale": row["rationale"],
                       "used_by": row["used_by"], "verification": row["verification"],
                       "verified_at": str(row["verified_at"]) if row["verified_at"] else None,
                       "note": row["revision_note"], "replaced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            used_by = None if item.used_by is None else [str(u)[:200] for u in item.used_by]
            cur.execute(
                "UPDATE standards SET spec = %s, name = COALESCE(%s, name), "
                "rationale = COALESCE(%s, rationale), used_by = COALESCE(%s, used_by), "
                "verification = NULL, verified_hash = NULL, verified_at = NULL, "
                "history = history || %s, revised_at = NOW(), revision_note = %s, "
                "updated_at = NOW() WHERE id = %s RETURNING id, code, status",
                (item.spec, item.name, item.rationale,
                 None if used_by is None else Jsonb(used_by), Jsonb([earlier]),
                 item.note or None, standard_id),
            )
            result = cur.fetchone()
        conn.commit()
    return result


@app.post("/standards/{standard_id}/verification")
def verify_standard(standard_id: int, item: MaintainerVerification):
    """Record a verification the maintainer supplies (for example, done by
    an assistant with more capacity than the research worker), against the
    spec it checked. ``verified_by`` is kept with it, and the standard's
    queued verification jobs are removed. Refused while a verification job
    is running for the standard, or if the spec has changed since."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, spec FROM standards WHERE id = %s FOR UPDATE",
                        (standard_id,))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="not found")
            if row["status"] not in ("proposed", "approved"):
                raise HTTPException(status_code=409, detail=f"standard is {row['status']}")
            if item.spec_hash != modular.spec_hash(row["spec"]):
                raise HTTPException(status_code=409,
                                    detail="the spec has changed since it was verified")
            cur.execute("SELECT 1 FROM jobs WHERE job_type = %s AND status = 'running' "
                        "AND input->>'standard_id' = %s", (modular.VERIFY, str(standard_id)))
            if cur.fetchone():
                raise HTTPException(status_code=409,
                                    detail="a verification job is running for this standard")
            verification = {**item.verification.model_dump(), "verified_by": item.verified_by}
            cur.execute("UPDATE standards SET verification = %s, verified_hash = %s, "
                        "verified_at = NOW() WHERE id = %s",
                        (Jsonb(verification), item.spec_hash, standard_id))
            cur.execute("DELETE FROM jobs WHERE job_type = %s AND status = 'queued' "
                        "AND input->>'standard_id' = %s RETURNING id",
                        (modular.VERIFY, str(standard_id)))
            removed = len(cur.fetchall())
        conn.commit()
    return {"id": standard_id, "verified": True, "outcome": item.verification.outcome,
            "removed_jobs": removed}


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


#: Written into an archive note once the archiver has considered the
#: project's own licence for a source without one (see archives_pending).
PROJECT_LICENCE_WEIGHED = "project licence weighed"
#: Written into the note of a licence skip once non-commercial licences are
#: archived too (2026-10-23): an older licence skip is offered once more.
NON_COMMERCIAL_WEIGHED = "non-commercial weighed"
#: Project licence classes that may cover the project's own files as
#: archived copies (a non-commercial copy is labelled so).
ARCHIVABLE_CLASSES = ("open", "share-alike", "non-commercial")


@app.get("/archives/pending")
def archives_pending(limit: int = 20):
    """Design sources from the latest build pack 'design' sections that
    have no archive record yet (ops/archive/archive-designs.py).

    Each item carries the project's own checked licence when it is open,
    with the places the project is hosted (its repository and source
    URLs): a file the project hosts itself, given no licence of its own,
    is covered by the project's. A source skipped for want of a licence is
    offered once more when its project's licence is open, until the
    archiver has weighed that licence (its note says so). A source skipped
    for its licence before non-commercial copies were kept is offered once
    more too."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (b.project_id) b.project_id, p.code, p.source_uris, b.content,
                       sc.licence AS project_licence, sc.licence_class, sc.repository
                FROM build_packs b JOIN projects p ON p.id = b.project_id
                LEFT JOIN LATERAL (SELECT licence, licence_class, repository FROM source_checks
                                   WHERE project_id = p.id ORDER BY checked_at DESC LIMIT 1) sc
                       ON true
                WHERE b.section = 'design'
                ORDER BY b.project_id, b.created_at DESC, b.id DESC
                """
            )
            packs = cur.fetchall()
            # A failed attempt is retried after a day; archived and skipped
            # sources are settled.
            cur.execute(
                "SELECT project_id, source_uri, revision, status, licence, note "
                "FROM design_archives "
                "WHERE status <> 'failed' OR created_at > NOW() - INTERVAL '1 day'")
            rows = cur.fetchall()
    open_projects = {p["project_id"] for p in packs if p["licence_class"] in ARCHIVABLE_CLASSES}

    def reoffer(r) -> bool:
        if r["status"] != "skipped":
            return False
        note = r["note"] or ""
        if "no open licence" in note and NON_COMMERCIAL_WEIGHED not in note:
            return True
        return (r["project_id"] in open_projects
                and (r["licence"] or "unknown").lower() in ("unknown", "none", "")
                and PROJECT_LICENCE_WEIGHED not in note)

    done = {(r["project_id"], r["source_uri"], r["revision"]) for r in rows if not reoffer(r)}
    pending = []
    for pack in packs:
        content = pack["content"] or {}
        origins = [u for u in [pack["repository"], *(pack["source_uris"] or [])]
                   if isinstance(u, str) and u.startswith("https://")]
        project_licence = (pack["project_licence"] if pack["project_id"] in open_projects
                           else None)
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
                            "licence": licence or "unknown",
                            "project_licence": project_licence, "project_origins": origins})
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


#: Public archives (section 4c): a repository goes to Software Heritage,
#: a file or page to the Wayback Machine.
PRESERVATION_SERVICE = "CASE WHEN {kind} = 'git' THEN 'software-heritage' ELSE 'wayback' END"


@app.get("/preservation/pending")
def preservation_pending(limit: int = 20):
    """Design sources (every source the archiver has seen, and the
    repositories of projects with a design section) due a public-archive
    check: never checked, a save requested over 6 hours ago, a failure
    over a day ago, or held but last checked over 90 days ago. Sources
    with no local copy come first, since a public archive is all that
    keeps them."""
    service = PRESERVATION_SERVICE.format(kind="d.kind")
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH sources AS (
                    (SELECT DISTINCT ON (source_uri) source_uri, kind,
                            COALESCE(resolved_revision, NULLIF(revision, '')) AS revision,
                            status <> 'archived' AS at_risk
                     FROM design_archives ORDER BY source_uri, created_at DESC)
                    UNION ALL
                    (SELECT DISTINCT ON (sc.repository) sc.repository, 'git', NULL, TRUE
                     FROM source_checks sc
                     WHERE sc.repository LIKE 'https://%%'
                       AND EXISTS (SELECT 1 FROM build_packs b WHERE b.project_id = sc.project_id
                                   AND b.section = 'design')
                     ORDER BY sc.repository, sc.checked_at DESC)
                ), d AS (
                    SELECT DISTINCT ON (source_uri) * FROM sources
                    ORDER BY source_uri, revision NULLS LAST, at_risk
                )
                SELECT d.source_uri, d.kind, d.revision, {service} AS service,
                       p.status AS last_status, p.reference AS last_reference
                FROM d LEFT JOIN design_preservation p
                  ON p.source_uri = d.source_uri AND p.service = {service}
                WHERE p.id IS NULL
                   OR (p.status = 'requested' AND p.checked_at < NOW() - INTERVAL '6 hours')
                   OR (p.status = 'failed' AND p.checked_at < NOW() - INTERVAL '1 day')
                   OR (p.status = 'held' AND p.checked_at < NOW() - INTERVAL '90 days')
                ORDER BY (p.id IS NOT NULL), d.at_risk DESC, d.source_uri
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()


@app.post("/preservation")
def record_preservation(item: PreservationRecord):
    """Record a public archive's copy of a design source. A copy already
    held stays recorded when a later check fails or is only requested."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO design_preservation (source_uri, service, status, reference,
                                                 captured_at, note)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_uri, service) DO UPDATE SET
                    status = CASE WHEN design_preservation.status = 'held'
                                   AND EXCLUDED.status <> 'held'
                                  THEN 'held' ELSE EXCLUDED.status END,
                    reference = CASE WHEN design_preservation.status = 'held'
                                      AND EXCLUDED.status <> 'held'
                                     THEN design_preservation.reference
                                     ELSE EXCLUDED.reference END,
                    captured_at = CASE WHEN design_preservation.status = 'held'
                                        AND EXCLUDED.status <> 'held'
                                       THEN design_preservation.captured_at
                                       ELSE EXCLUDED.captured_at END,
                    note = EXCLUDED.note, checked_at = NOW()
                RETURNING id, status, reference;
                """,
                (item.source_uri, item.service, item.status, item.reference,
                 item.captured_at, item.note or None),
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
# Monthly catalogue audit and gap analysis (policy section 4j; audit.py)
# ---------------------------------------------------------------------------


def _audit_catalogue(cur, month: str | None = None) -> list:
    """One catalogue-audit job a month, once enough projects are screened."""
    month = month or modular.month()
    key = f"{audit.AUDIT}:{month}"
    cur.execute("SELECT count(DISTINCT project_id) AS n FROM assessments")
    if cur.fetchone()["n"] < audit.MIN_ASSESSED or _unit_exists(cur, key):
        return []
    cur.execute(
        """
        SELECT c.id, c.layer, c.name, COALESCE(c.track, 'need') AS track, c.solved_at,
               COALESCE(c.layer = ANY(%s) OR c.track = %s, FALSE) AS focus,
               b.brief, g.requirements AS progress
        FROM need_categories c
        LEFT JOIN LATERAL (SELECT brief FROM need_briefs WHERE category_id = c.id
                           ORDER BY created_at DESC, id DESC LIMIT 1) b ON TRUE
        LEFT JOIN LATERAL (SELECT requirements FROM need_progress WHERE category_id = c.id
                           ORDER BY created_at DESC, id DESC LIMIT 1) g ON TRUE
        ORDER BY c.layer NULLS LAST, c.name
        """,
        (list(research.DISCOVERY_LAYERS), research.ENABLER),
    )
    rows = cur.fetchall()
    cur.execute("SELECT category_id, status, count(*) AS n FROM projects "
                "WHERE status <> 'merged' GROUP BY 1, 2")
    counts: dict = {}
    for r in cur.fetchall():
        counts.setdefault(r["category_id"], {})[r["status"]] = r["n"]
    categories = [{
        "name": r["name"], "layer": r["layer"], "track": r["track"], "in_focus": r["focus"],
        "requirements": [_clip(t, 120) for t in ((r["brief"] or {}).get("requirements")
                                                 or [])[:6]],
        "status": _need_status(r["solved_at"], r["progress"]),
        "levels": [p.get("level") for p in r["progress"] or []],
        "projects": counts.get(r["id"], {}),
    } for r in rows]
    projects = [{"name": p["name"], "category": p["category"], "status": p["status"],
                 "licence_class": p["licence_class"], "summary": _clip(p["summary"], 100)}
                for p in _projects_digest(cur, limit=250)]
    cur.execute("SELECT code, name, status FROM standards WHERE status <> 'rejected' "
                "ORDER BY code")
    standards = cur.fetchall()
    cur.execute("SELECT code, name, domain FROM modules ORDER BY domain, code")
    modules = cur.fetchall()
    cur.execute("SELECT id, title, category_name AS category, action, status FROM audit_gaps "
                "WHERE status <> 'closed' ORDER BY id")
    gaps = cur.fetchall()
    job = _enqueue(cur, unit_key=key, job_type=audit.AUDIT, priority=audit.AUDIT_PRIORITY,
                   payload=audit.audit_input(categories, projects, standards, modules, gaps))
    return [job] if job else []


def _store_audit(cur, job, result, summary: str) -> dict:
    """Record the month's audit and its gaps (each matched to its category
    by name where it names an existing one), and close the earlier gaps the
    audit found covered."""
    month = (job.get("unit_key") or "").rsplit(":", 1)[-1] or modular.month()
    cur.execute("INSERT INTO catalogue_audits (month, summary, job_id) VALUES (%s, %s, %s) "
                "RETURNING id", (month, _clip(summary, 3000), job["id"]))
    audit_id = cur.fetchone()["id"]
    cur.execute("SELECT id, lower(name) AS name FROM need_categories")
    by_name = {r["name"]: r["id"] for r in cur.fetchall()}
    for gap in result.gaps:
        cur.execute(
            "INSERT INTO audit_gaps (audit_id, title, kind, category_id, category_name, "
            "reference, detail, severity, action, focus) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (audit_id, gap.title, gap.kind, by_name.get(gap.category.strip().lower()),
             gap.category, gap.reference, gap.detail, gap.severity, gap.action, gap.focus))
    cur.execute("UPDATE audit_gaps SET status = 'closed', closed_by = %s "
                "WHERE id = ANY(%s) AND status IN ('proposed', 'approved') AND audit_id <> %s "
                "RETURNING id", (audit_id, result.closed, audit_id))
    closed = len(cur.fetchall())
    return {"audit": audit_id, "gaps": len(result.gaps), "closed": closed}


@app.get("/gaps")
def get_gaps():
    """Catalogue gaps from the monthly audits, newest first, with the
    maintainer's decision (``status``: proposed, approved, rejected or
    closed) and, for an acted discover gap, its discovery job's status."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT g.id, g.title, g.kind, g.category_id, g.category_name AS category,
                       g.reference, g.detail, g.severity, g.action, g.focus, g.status,
                       g.decision_note, g.decided_at, g.created_at, a.month,
                       (SELECT j.status FROM jobs j WHERE j.unit_key =
                        'candidate-discovery:gap:' || g.id) AS discovery
                FROM audit_gaps g JOIN catalogue_audits a ON a.id = g.audit_id
                ORDER BY g.created_at DESC, g.id DESC
                """
            )
            return cur.fetchall()


@app.get("/audits")
def get_audits():
    """Catalogue audits, newest first, with gap counts by status."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.id, a.month, a.summary, a.created_at,
                       (SELECT jsonb_object_agg(status, n) FROM (
                           SELECT status, count(*) AS n FROM audit_gaps
                           WHERE audit_id = a.id GROUP BY status) s) AS gaps
                FROM catalogue_audits a ORDER BY a.created_at DESC, a.id DESC
                """
            )
            return cur.fetchall()


@app.post("/gaps/{gap_id}/decision")
def decide_gap(gap_id: int, item: Decision):
    """The maintainer acts on a gap (approved), ignores it (rejected) or
    reopens it (proposed). Acting on a discover gap in an existing category
    queues a candidate-discovery job focused on the gap."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM audit_gaps WHERE id = %s FOR UPDATE", (gap_id,))
            gap = cur.fetchone()
            if gap is None:
                raise HTTPException(status_code=404, detail="not found")
            if gap["status"] == "closed":
                raise HTTPException(status_code=409, detail="gap is closed")
            cur.execute("UPDATE audit_gaps SET status = %s, decision_note = %s, "
                        "decided_at = NOW() WHERE id = %s",
                        (item.decision, item.note or None, gap_id))
            job = None
            if item.decision == "approved" and gap["action"] == "discover" and gap["category_id"]:
                cur.execute("SELECT id, layer, name, scope, track FROM need_categories "
                            "WHERE id = %s", (gap["category_id"],))
                category = cur.fetchone()
                known = _known_names(cur, category["id"])
                payload = {"category_id": category["id"],
                           **research.discovery_input(category, known,
                                                      _latest_brief(cur, category["id"]))}
                job = _enqueue(cur, unit_key=f"{research.DISCOVERY}:gap:{gap_id}",
                               job_type=research.DISCOVERY,
                               priority=audit.DISCOVERY_PRIORITY,
                               payload=audit.focused_discovery(payload, gap))
        conn.commit()
    return {"id": gap_id, "status": item.decision, "discovery_job": job["id"] if job else None}


# ---------------------------------------------------------------------------
# Civilisation Capability Graph (policy section 4k; graph.py)
# ---------------------------------------------------------------------------

@app.post("/graph/updates")
def graph_update(item: graph.GraphUpdate):
    """A candidate graph update: sources, needs, capabilities, their links
    and dependencies, implementations and assessments, referenced by code.
    Candidates are created or replaced; decided records are kept and only
    gain assessments. Items that cannot be placed are listed in
    ``skipped`` rather than failing the whole update."""
    with db() as conn:
        with conn.cursor() as cur:
            result = graph.apply_update(cur, item)
        conn.commit()
    return result


@app.get("/graph/updates")
def graph_updates(limit: int = 50):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, origin, proposed_by, job_id, agent_run, summary, counts, "
                        "created_at FROM graph_updates ORDER BY id DESC LIMIT %s",
                        (max(1, min(limit, 500)),))
            return cur.fetchall()


@app.get("/graph/needs")
def graph_needs():
    with db() as conn:
        with conn.cursor() as cur:
            return graph.list_needs(cur)


@app.get("/graph/needs/{need_id}")
def graph_need(need_id: int, max_bootstrap: int | None = None):
    """A need with the capabilities it requires, their implementations and
    the gaps; ``max_bootstrap`` (0-7) counts only implementations
    buildable at that bootstrap level."""
    if max_bootstrap is not None and not 0 <= max_bootstrap <= 7:
        raise HTTPException(status_code=422, detail="max_bootstrap is 0 to 7")
    with db() as conn:
        with conn.cursor() as cur:
            result = graph.need_detail(cur, need_id, max_bootstrap)
    if result is None:
        raise HTTPException(status_code=404, detail="not found")
    return result


@app.get("/graph/capabilities")
def graph_capabilities():
    with db() as conn:
        with conn.cursor() as cur:
            return graph.list_capabilities(cur)


@app.get("/graph/capabilities/{capability_id}")
def graph_capability(capability_id: int):
    with db() as conn:
        with conn.cursor() as cur:
            result = graph.capability_detail(cur, capability_id)
    if result is None:
        raise HTTPException(status_code=404, detail="not found")
    return result


@app.get("/graph/sources")
def graph_sources():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM graph_sources ORDER BY key")
            return cur.fetchall()


@app.get("/graph/reviews")
def graph_reviews(limit: int = 100):
    """Independent reviews of safety-critical and medical candidates,
    newest first, with the record each one checked."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT r.*, COALESCE(n.code, c.code, i.code) AS code, "
                "COALESCE(n.revision, c.revision, i.revision) AS current_revision "
                "FROM graph_reviews r "
                "LEFT JOIN needs n ON r.subject_type = 'need' AND n.id = r.subject_id "
                "LEFT JOIN capabilities c ON r.subject_type = 'capability' AND c.id = r.subject_id "
                "LEFT JOIN implementations i ON r.subject_type = 'implementation' "
                "AND i.id = r.subject_id ORDER BY r.created_at DESC, r.id DESC LIMIT %s",
                (max(1, min(limit, 500)),))
            return cur.fetchall()


def _review_graph(cur, budget: int) -> list:
    """A graph-review job for each safety-critical or medical candidate
    without a review of its current revision (section 4k)."""
    created = []
    for kind, row in graph.review_due(cur):
        if len(created) >= budget:
            break
        key = f"{graph.REVIEW}:{kind}:{row['id']}:{row['revision']}"
        if _unit_exists(cur, key):
            continue
        job = _enqueue(cur, unit_key=key, job_type=graph.REVIEW,
                       priority=graph.REVIEW_PRIORITY,
                       payload=graph.review_input(cur, kind, row))
        if job:
            created.append(job)
    return created


@app.get("/graph/export")
def graph_export(candidates: bool = False):
    """The approved graph (with candidates if asked) as one document that
    is readable without Applied Commons (offline package, section 35)."""
    with db() as conn:
        with conn.cursor() as cur:
            return graph.export(cur, candidates)


# ---------------------------------------------------------------------------
# Production library (section 4l; library.py): staged beside the canonical
# records, never into them
# ---------------------------------------------------------------------------


class LibraryPackage(BaseModel):
    origin: str = Field(min_length=2, max_length=60)
    version: str = Field(default="", max_length=40)
    package_sha256: str = Field(default="", max_length=64)
    imported_by: str = Field(min_length=2, max_length=200)
    nodes: list[dict[str, Any]] = Field(max_length=20000)
    edges: list[dict[str, Any]] = Field(default_factory=list, max_length=50000)
    baseline_ids: dict[str, str] = Field(default_factory=dict)


class LibraryReview(BaseModel):
    status: str = Field(pattern="^(staged|reviewed|verified|rejected)$")
    note: str = Field(min_length=3, max_length=4000)
    decided_by: str = Field(min_length=2, max_length=200)
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=50)


class LibraryDecision(BaseModel):
    status: str = Field(pattern="^(confirmed|rejected|candidate|open|done|dismissed)$")
    note: str = Field(default="", max_length=4000)
    decided_by: str = Field(min_length=2, max_length=200)


@app.post("/library/import")
def library_import(item: LibraryPackage):
    """Stage a production library package (ops/library/import_library.py).
    Idempotent; a package that fails validation writes nothing (422)."""
    with db() as conn:
        with conn.cursor() as cur:
            try:
                return library.run_import(cur, item.model_dump(), item.imported_by)
            except library.PackageError as exc:
                conn.rollback()
                raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/library/export")
def library_export():
    """The staged library, its reviews, links, tasks and assessments."""
    with db() as conn:
        with conn.cursor() as cur:
            return library.export(cur)


@app.get("/library/tasks")
def library_tasks(status: str = "open", kind: str | None = None):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, key, kind, subject, title, detail, priority, status, note "
                        "FROM library_tasks WHERE status = %s AND (%s::text IS NULL OR kind = %s) "
                        "ORDER BY priority, key", (status, kind, kind))
            return cur.fetchall()


@app.post("/library/records/{key:path}/review")
def library_review(key: str, item: LibraryReview):
    """A human review of a staged record: reviewed or verified protects it
    from later seeds; verified needs sourced evidence."""
    with db() as conn:
        with conn.cursor() as cur:
            try:
                result = library.review(cur, key, item.status, item.note, item.decided_by,
                                        item.evidence)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if result is None:
                raise HTTPException(status_code=404, detail="no such library record")
            return result


@app.post("/library/links/{link_id}/decision")
def library_link_decision(link_id: int, item: LibraryDecision):
    """Confirm or reject a proposed link to a canonical AC record."""
    if item.status not in ("confirmed", "rejected", "candidate"):
        raise HTTPException(status_code=422, detail="confirmed, rejected or candidate")
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE library_links SET status = %s, note = %s, decided_by = %s, "
                        "decided_at = NOW() WHERE id = %s RETURNING id, status",
                        (item.status, item.note, item.decided_by, link_id))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="no such link")
            return row


@app.post("/library/tasks/{task_id}")
def library_task_status(task_id: int, item: LibraryDecision):
    """Close, dismiss or reopen a research task; a closed task is not
    reopened by a later import."""
    if item.status not in ("open", "done", "dismissed"):
        raise HTTPException(status_code=422, detail="open, done or dismissed")
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE library_tasks SET status = %s, note = %s, resolved_at = "
                        "CASE WHEN %s = 'open' THEN NULL ELSE NOW() END WHERE id = %s "
                        "RETURNING id, status",
                        (item.status, f"{item.decided_by}: {item.note}".strip(), item.status,
                         task_id))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="no such task")
            return row


@app.post("/graph/{kind}/{item_id}/decision")
def graph_decision(kind: str, item_id: int, item: graph.GraphDecision):
    """The maintainer approves, rejects or reopens a need, capability or
    implementation (kind needs, capabilities or implementations), or
    rejects or restores a link or dependency (kind links or dependencies)."""
    with db() as conn:
        with conn.cursor() as cur:
            status, body = graph.decide(cur, kind, item_id, item)
        if status == 200:
            conn.commit()
    if status != 200:
        raise HTTPException(status_code=status, detail=body["detail"])
    return body


# ---------------------------------------------------------------------------
# Job claiming
# ---------------------------------------------------------------------------

#: A waiting job gains priority with age, so lower-priority work (need
#: progress, discovery, literature) is not starved by a steady flow of
#: higher-priority jobs (maintainer decision 2026-10-19). Capped, so a
#: fresh verification or module job still goes first.
AGING_PER_DAY = 0.01
AGING_MAX = 0.15


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
                        priority + LEAST(
                            EXTRACT(EPOCH FROM NOW() - created_at) / 86400 * %s, %s
                        ) + CASE WHEN input ? 'maintainer_focus'
                                   OR project_id IN (SELECT id FROM projects WHERE priority)
                                 THEN 1 ELSE 0 END DESC,
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
                    AGING_PER_DAY,
                    AGING_MAX,
                    item.worker,
                ),
            )

            result = cur.fetchone()
            if result is not None:
                result = _refresh_on_claim(cur, result)

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
        audited = (audit.AuditResult(**item.output)
                   if job_type == audit.AUDIT else None)
        graph_review = (graph.GraphReviewResult(**item.output)
                        if job_type == graph.REVIEW else None)
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
                elif audited is not None:
                    extra = _store_audit(cur, result, audited,
                                         str(item.output.get("summary") or ""))
                elif graph_review is not None:
                    extra = graph.store_review(cur, result, graph_review)
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


_CODE_REPO = re.compile(r"^https?://(?:www\.)?(github\.com|gitlab\.com|codeberg\.org)/"
                        r"([^/#?]+)/([^/#?]+)", re.I)


def identity_keys(name: str, uris) -> set[str]:
    """What identifies a project across categories: its name without
    bracketed asides, punctuation or case, and each code-host repository
    it cites."""
    plain = re.sub(r"[^a-z0-9]+", "", re.sub(r"\(.*?\)", " ", str(name).lower()))
    keys = {f"name:{plain}"} if plain else set()
    for uri in uris or []:
        found = _CODE_REPO.match(str(uri))
        if found:
            host, owner, repo = (g.lower() for g in found.groups())
            keys.add(f"repo:{host}/{owner}/{repo.removesuffix('.git')}")
    return keys


def _known_identities(cur) -> dict[str, int]:
    """Identity key -> the project that now carries it (a merged duplicate
    resolves to its survivor)."""
    cur.execute("SELECT id, name, source_uris, merged_into FROM projects")
    rows = cur.fetchall()
    survivor = {r["id"]: r["merged_into"] for r in rows}

    def resolve(pid):
        for _ in range(10):
            if not survivor.get(pid):
                return pid
            pid = survivor[pid]
        return pid
    known: dict[str, int] = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        for key in identity_keys(r["name"], r["source_uris"]):
            known.setdefault(key, resolve(r["id"]))
    return known


def _store_candidates(cur, job, discovery) -> dict:
    """New candidates from a discovery. A project already catalogued under
    another category (same name or repository) is listed under this one
    too instead of being added twice (section 4d)."""
    category_id = job["input"]["category_id"]
    cur.execute("SELECT layer, name FROM need_categories WHERE id = %s", (category_id,))
    category = cur.fetchone()
    known = _known_identities(cur)
    added = also = 0
    for candidate in discovery.candidates:
        keys = identity_keys(candidate.name, candidate.source_uris)
        existing = next((known[k] for k in sorted(keys) if k in known), None)
        if existing is not None:
            cur.execute("SELECT category_id FROM projects WHERE id = %s", (existing,))
            if cur.fetchone()["category_id"] != category_id:
                _also_list(cur, existing, category_id,
                           f"found again by discovery job {job['id']} as {candidate.name}")
                also += 1
            continue
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
        row = cur.fetchone()
        if row is not None:
            added += 1
            for key in keys:
                known.setdefault(key, row["id"])
    return {"candidates": added, "also_listed": also}


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
