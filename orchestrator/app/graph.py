"""Civilisation Capability Graph (policy section 4k and capability-graph.md;
maintainer decision 2026-10-21).

Below the existing layer and category model: need records with their
baseline and improvement thresholds; capabilities, which are what a
community must be able to do to meet them, linked to the categories and
needs they serve and to the capabilities they require; implementations,
which are ways of providing a capability; sources with provenance and
licence; and assessments, which are claims about a record, each with an
evidence state and its source.

Three axes are kept apart: the Maslow layer (why it matters, from the
category), the bootstrap level B0-B7 (what it takes to make) and the
evidence maturity E0-E5 (how well it is shown to work).

Everything arrives as a candidate graph update. A need, capability or
implementation is authoritative only once approved, and an update never
changes a decided one: new information about it arrives as assessments.
A claim marked SUPPORTED needs an independent source, otherwise it is kept
as INFERRED; Applied Commons' own publications never count as independent
evidence. An implementation's evidence maturity is the level its sourced
evidence supports, E0 without any, whatever is claimed for it.
"""

from __future__ import annotations

import re
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

import research
from modular import _Clipped

STATES = ("SUPPORTED", "INFERRED", "UNKNOWN", "CONFLICTED")
SOURCE_TYPES = ("mediawiki", "git", "book_manual", "technical_collection",
                "established_engineering", "standard", "web", "other")
QUALITY = ("low", "medium", "high", "unknown")
AUTHORITY = ("standards-body", "peer-reviewed", "established-reference", "manufacturer",
             "practitioner", "community", "applied-commons", "unknown")
DERIVATIVE = ("none", "excerpt", "summary", "derivative", "copy")
#: Source families that are Applied Commons' own publications (section 32).
OWN_FAMILIES = ("applied-commons", "ac-wiki", "applied-commons-wiki")
BASELINE = ("unknown", "unmet", "partial", "met")
PATHWAYS = ("purchase", "salvage", "recondition", "fabricate", "bootstrap",
            "experimental", "practice", "other")
ROLES = ("candidate_implementation", "baseline", "alternative")
RELEVANCE = ("required", "supporting")
DEPENDENCY_KINDS = ("requires", "benefits-from")
LICENCE_CLASSES = ("open", "share-alike", "restricted", "none", "unknown")
CONFIDENCE = ("low", "medium", "high", "unknown")
DEPENDENCY_LEVELS = ("none", "low", "medium", "high", "unknown")
RESILIENCE = ("grid_dependency", "electronics_dependency", "specialist_dependency",
              "supply_chain_dependency")
REQUIRES = ("materials", "tools", "skills", "energy", "infrastructure", "knowledge")
COMPARISON = ("bootstrap_difficulty", "capital", "energy", "materials", "skills", "time",
              "productivity", "precision", "repairability", "external_dependencies",
              "supply_chain_dependency", "self_reproduction", "field_history")
#: Categories whose records need an independent check before approval,
#: like anything marked safety-critical (section 18: medical material).
MEDICAL = ("Physical and mental health", "Reproductive and early-life needs")

BOOTSTRAP = {
    0: "human labour and natural or local materials",
    1: "basic hand tools, forge, simple workshop",
    2: "casting and basic machine tools",
    3: "general-purpose machine shop",
    4: "precision workshop, motors, hydraulics, instrumentation",
    5: "industrial production systems",
    6: "advanced industry, electronics, optics, sophisticated materials",
    7: "globally interdependent high technology",
}
MATURITY = {
    0: "concept",
    1: "prototype",
    2: "independently replicated",
    3: "field deployed",
    4: "established engineering",
    5: "industrial commodity",
}
#: The validation questions asked of every implementation (section 11).
VALIDATION = {
    "built": "Has it physically been built?",
    "built_repeatedly": "Has it been built more than once?",
    "independently_reproduced": "Has someone independent reproduced it?",
    "real_world_operation": "Has it operated under real-world conditions?",
    "operating_duration": "For how long?",
    "measured_performance": "Are performance claims measured?",
    "independently_verified": "Are results independently verified?",
    "failures_documented": "Are failures documented?",
    "maintenance_experience": "Is maintenance experience available?",
    "conventional_alternative": "Is there an established conventional alternative?",
    "improves_on_alternative": "Does this implementation materially improve upon it?",
    "next_level_evidence": "What evidence is required to move to the next maturity level?",
}
#: Assessment attributes holding a bootstrap or maturity range.
RANGES = {"bootstrap_level": ("B", 7), "evidence_maturity": ("E", 5)}

CODE = r"^[a-z0-9][a-z0-9-]{1,79}$"
NODES = {"needs": "need", "capabilities": "capability", "implementations": "implementation"}
EDGES = ("links", "dependencies")
EDGE_TABLES = {"links": "capability_links", "dependencies": "capability_dependencies"}


# ---------------------------------------------------------------------------
# Parsing helpers: model output is normalised, not rejected, where the
# meaning is clear; an unclear value becomes unknown.
# ---------------------------------------------------------------------------

def _choice(value: Any, options: tuple[str, ...], default: str) -> str:
    text = str(value or "").strip().lower().replace(" ", "-")
    if text in options:
        return text
    text = text.replace("-", "_")
    return text if text in options else default


def _state(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if text in STATES else "UNKNOWN"


def _texts(value: Any, items: int = 30, size: int = 300) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v).strip()[:size] for v in value[:items] if str(v).strip()]


def _code(value: Any) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(value or "").strip().lower()).strip("-")[:80]


def parse_range(value: Any, prefix: str, top: int) -> tuple[int, int] | None:
    """B2, "B1-B3", 2, [1, 3] or {"min": 1, "max": 3} as (low, high);
    anything unclear or out of range is unknown (None)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        value = [value.get("min"), value.get("max", value.get("min"))]
    if isinstance(value, (list, tuple)):
        found = [parse_range(v, prefix, top) for v in value if v is not None]
        if not found or None in found:
            return None
        low, high = found[0][0], found[-1][1]
    elif isinstance(value, int):
        low = high = value
    else:
        digits = [int(d) for d in re.findall(r"\d+", str(value))]
        if not digits:
            return None
        low, high = digits[0], digits[-1]
    if not 0 <= low <= high <= top:
        return None
    return low, high


def show_range(low: int | None, high: int | None, prefix: str) -> str:
    if low is None:
        return "unknown"
    return f"{prefix}{low}" if low == high else f"{prefix}{low}-{prefix}{high}"


# ---------------------------------------------------------------------------
# A candidate graph update
# ---------------------------------------------------------------------------

class Requires(BaseModel):
    materials: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    energy: list[str] = Field(default_factory=list)
    infrastructure: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)

    @field_validator("*", mode="before")
    @classmethod
    def _lists(cls, value):
        return _texts(value)


class SourceIn(_Clipped):
    key: str = Field(min_length=2, max_length=200)
    family: str = Field(min_length=2, max_length=60)
    source_type: str = "other"
    title: str = Field(min_length=2, max_length=400)
    uri: str | None = Field(default=None, max_length=2000)
    revision: str | None = Field(default=None, max_length=200)
    bibliographic: dict[str, Any] = Field(default_factory=dict)
    source_quality: str = "unknown"
    source_authority: str = "unknown"
    origin: str = "external"
    license_original: str = Field(default="unknown", max_length=200)
    license_verified: bool = False
    attribution: str = Field(default="", max_length=500)
    derivative_status: str = "none"
    redistribution_allowed: bool = False

    @field_validator("family", mode="after")
    @classmethod
    def _family(cls, value):
        return _code(value) or "other"

    @field_validator("source_type")
    @classmethod
    def _type(cls, value):
        return _choice(value, SOURCE_TYPES, "other")

    @field_validator("source_quality")
    @classmethod
    def _quality(cls, value):
        return _choice(value, QUALITY, "unknown")

    @field_validator("source_authority")
    @classmethod
    def _authority(cls, value):
        return _choice(value, AUTHORITY, "unknown")

    @field_validator("derivative_status")
    @classmethod
    def _derivative(cls, value):
        return _choice(value, DERIVATIVE, "none")

    def row(self) -> dict[str, Any]:
        own = self.family in OWN_FAMILIES or self.origin == "applied-commons"
        return {
            "key": self.key, "family": self.family, "source_type": self.source_type,
            "title": self.title, "uri": self.uri, "revision": self.revision,
            "bibliographic": Jsonb(self.bibliographic), "source_quality": self.source_quality,
            "source_authority": "applied-commons" if own else self.source_authority,
            "origin": "applied-commons" if own else "external",
            "license_original": self.license_original or "unknown",
            "license_verified": self.license_verified,
            "attribution": self.attribution, "derivative_status": self.derivative_status,
            # An unverified licence is never redistributable (section 34).
            "redistribution_allowed": self.redistribution_allowed and self.license_verified,
        }


class NeedIn(_Clipped):
    code: str = Field(pattern=CODE)
    category: str = Field(min_length=2, max_length=160)
    name: str = Field(min_length=3, max_length=200)
    desired_state: str = Field(default="", max_length=1000)
    minimum_threshold: str = Field(default="", max_length=1000)
    baseline_threshold: str = Field(default="", max_length=1000)
    improved_threshold: str = Field(default="", max_length=1000)
    improvements: list[str] = Field(default_factory=list)
    baseline_status: str = "unknown"
    baseline_note: str = Field(default="", max_length=1000)

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, value):
        return _code(value)

    @field_validator("improvements", mode="before")
    @classmethod
    def _improvements(cls, value):
        return _texts(value, 20)

    @field_validator("baseline_status")
    @classmethod
    def _baseline(cls, value):
        return _choice(value, BASELINE, "unknown")

    def fields(self) -> dict[str, Any]:
        return {"name": self.name, "desired_state": self.desired_state,
                "minimum_threshold": self.minimum_threshold,
                "baseline_threshold": self.baseline_threshold,
                "improved_threshold": self.improved_threshold,
                "improvements": Jsonb(self.improvements),
                "baseline_status": self.baseline_status, "baseline_note": self.baseline_note}


class CapabilityIn(_Clipped):
    code: str = Field(pattern=CODE)
    name: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=2000)
    purpose: str = Field(default="", max_length=1000)
    requires: Requires = Field(default_factory=Requires)
    produces: list[str] = Field(default_factory=list)
    bootstrap_level: Any = None
    external_dependencies: list[str] = Field(default_factory=list)
    self_reproduction_fraction: float | None = None
    resilience: dict[str, str] = Field(default_factory=dict)
    failure_modes: list[str] = Field(default_factory=list)
    confidence: str = "unknown"
    uncertainties: list[str] = Field(default_factory=list)
    safety_critical: bool = False

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, value):
        return _code(value)

    @field_validator("requires", mode="before")
    @classmethod
    def _requires(cls, value):
        return value if isinstance(value, dict) else {}

    @field_validator("produces", "external_dependencies", "failure_modes", "uncertainties",
                     mode="before")
    @classmethod
    def _lists(cls, value):
        return _texts(value)

    @field_validator("self_reproduction_fraction", mode="before")
    @classmethod
    def _fraction(cls, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if 0 <= value <= 1 else None

    @field_validator("resilience", mode="before")
    @classmethod
    def _resilience(cls, value):
        value = value if isinstance(value, dict) else {}
        return {k: _choice(value.get(k), DEPENDENCY_LEVELS, "unknown") for k in RESILIENCE}

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, value):
        return _choice(value, CONFIDENCE, "unknown")

    def fields(self) -> dict[str, Any]:
        low, high = parse_range(self.bootstrap_level, "B", 7) or (None, None)
        return {"name": self.name, "description": self.description, "purpose": self.purpose,
                "requires": Jsonb(self.requires.model_dump()), "produces": Jsonb(self.produces),
                "bootstrap_min": low, "bootstrap_max": high,
                "external_dependencies": Jsonb(self.external_dependencies),
                "self_reproduction_fraction": self.self_reproduction_fraction,
                "resilience": Jsonb(self.resilience), "failure_modes": Jsonb(self.failure_modes),
                "confidence": self.confidence, "uncertainties": Jsonb(self.uncertainties),
                "safety_critical": self.safety_critical}


class LinkIn(_Clipped):
    """A capability serves a category, or one need within it."""
    capability: str
    category: str | None = Field(default=None, max_length=160)
    need: str | None = None
    relevance: str = "required"
    note: str = Field(default="", max_length=1000)
    state: str = "INFERRED"
    source: str | None = None
    locator: str = Field(default="", max_length=300)

    @field_validator("capability", "need", mode="before")
    @classmethod
    def _code(cls, value):
        return _code(value) if value else None

    @field_validator("relevance")
    @classmethod
    def _relevance(cls, value):
        return _choice(value, RELEVANCE, "required")

    @field_validator("state", mode="before")
    @classmethod
    def _state(cls, value):
        return _state(value)


class DependencyIn(_Clipped):
    """``capability`` requires (or benefits from) ``requires``."""
    capability: str
    requires: str
    kind: str = "requires"
    note: str = Field(default="", max_length=1000)
    state: str = "INFERRED"
    source: str | None = None
    locator: str = Field(default="", max_length=300)

    @field_validator("capability", "requires", mode="before")
    @classmethod
    def _code(cls, value):
        return _code(value)

    @field_validator("kind")
    @classmethod
    def _kind(cls, value):
        return _choice(value, DEPENDENCY_KINDS, "requires")

    @field_validator("state", mode="before")
    @classmethod
    def _state(cls, value):
        return _state(value)


class Answer(_Clipped):
    """An answer to one validation question (section 11)."""
    answer: str = Field(default="", max_length=600)
    state: str = "UNKNOWN"
    source: str | None = None
    locator: str = Field(default="", max_length=300)

    @field_validator("state", mode="before")
    @classmethod
    def _state(cls, value):
        return _state(value)


class ImplementationIn(_Clipped):
    code: str = Field(pattern=CODE)
    capability: str
    name: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=2000)
    pathway: str = "other"
    role: str = "candidate_implementation"
    project_id: int | None = None
    module: str | None = None
    bootstrap_level: Any = None
    evidence_maturity: Any = None
    validation: dict[str, Answer] = Field(default_factory=dict)
    comparison: dict[str, str] = Field(default_factory=dict)
    requires: Requires = Field(default_factory=Requires)
    external_dependencies: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    licence_class: str = "unknown"
    safety_critical: bool = False

    @field_validator("code", "capability", mode="before")
    @classmethod
    def _code(cls, value):
        return _code(value)

    @field_validator("pathway")
    @classmethod
    def _pathway(cls, value):
        return _choice(value, PATHWAYS, "other")

    @field_validator("role")
    @classmethod
    def _role(cls, value):
        return _choice(value, ROLES, "candidate_implementation")

    @field_validator("validation", mode="before")
    @classmethod
    def _validation(cls, value):
        value = value if isinstance(value, dict) else {}
        return {k: {"answer": v} if isinstance(v, str) else v
                for k, v in value.items() if k in VALIDATION}

    @field_validator("comparison", mode="before")
    @classmethod
    def _comparison(cls, value):
        value = value if isinstance(value, dict) else {}
        return {k: str(v)[:300] for k, v in value.items() if k in COMPARISON and v is not None}

    @field_validator("requires", mode="before")
    @classmethod
    def _requires(cls, value):
        return value if isinstance(value, dict) else {}

    @field_validator("external_dependencies", "failure_modes", mode="before")
    @classmethod
    def _lists(cls, value):
        return _texts(value)

    @field_validator("licence_class")
    @classmethod
    def _licence(cls, value):
        return _choice(value, LICENCE_CLASSES, "unknown")


class AssessmentIn(_Clipped):
    subject_type: str = Field(pattern="^(need|capability|implementation)$")
    subject: str
    attribute: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,59}$")
    value: Any
    state: str = "INFERRED"
    source: str | None = None
    locator: str = Field(default="", max_length=300)
    excerpt: str = Field(default="", max_length=300)
    basis: str = Field(default="", max_length=1000)

    @field_validator("subject", mode="before")
    @classmethod
    def _code(cls, value):
        return _code(value)

    @field_validator("state", mode="before")
    @classmethod
    def _state(cls, value):
        return _state(value)


class GraphUpdate(_Clipped):
    origin: str = Field(pattern="^(applied-commons|maintainer|external-human)$")
    proposed_by: str = Field(min_length=3, max_length=200)
    job_id: int | None = None
    agent_run: str | None = Field(default=None, max_length=200)
    summary: str = Field(default="", max_length=2000)
    sources: list[SourceIn] = Field(default_factory=list, max_length=200)
    needs: list[NeedIn] = Field(default_factory=list, max_length=100)
    capabilities: list[CapabilityIn] = Field(default_factory=list, max_length=200)
    links: list[LinkIn] = Field(default_factory=list, max_length=500)
    dependencies: list[DependencyIn] = Field(default_factory=list, max_length=500)
    implementations: list[ImplementationIn] = Field(default_factory=list, max_length=300)
    assessments: list[AssessmentIn] = Field(default_factory=list, max_length=1000)


class GraphDecision(BaseModel):
    """The maintainer's decision on a need, capability or implementation
    (approved, rejected, or back to candidate), or on a link or dependency
    (rejected, or approved to restore it). Safety-critical and medical
    records name the independent check behind the approval."""
    decision: str = Field(pattern="^(approved|rejected|candidate)$")
    note: str = Field(default="", max_length=2000)
    decided_by: str = Field(default="maintainer", min_length=3, max_length=200)
    independent_check: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Applying an update
# ---------------------------------------------------------------------------

class _Update:
    """Code-to-id lookups and the outcome counts for one update."""

    def __init__(self, cur, update_id: int, proposed_by: str):
        self.cur, self.id, self.by = cur, update_id, proposed_by
        self.counts: dict[str, int] = {}
        self.skipped: list[str] = []
        self._ids: dict[tuple[str, str], dict | None] = {}

    def count(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    def skip(self, why: str) -> None:
        self.skipped.append(why[:300])

    def lookup(self, table: str, column: str, value: Any) -> dict | None:
        key = (table, str(value))
        if key not in self._ids:
            self.cur.execute(f"SELECT * FROM {table} WHERE {column} = %s", (value,))
            self._ids[key] = self.cur.fetchone()
        return self._ids[key]

    def forget(self, table: str, value: Any) -> None:
        self._ids.pop((table, str(value)), None)

    def category(self, name: str | None) -> dict | None:
        """A category of the existing taxonomy, by name (any case): the
        graph never adds a hierarchy of its own (section 2)."""
        key = ("need_categories", str(name).lower())
        if key not in self._ids:
            self.cur.execute("SELECT * FROM need_categories WHERE lower(name) = lower(%s)",
                             (str(name).strip(),))
            self._ids[key] = self.cur.fetchone()
        return self._ids[key]

    def source(self, key: str | None) -> dict | None:
        return self.lookup("graph_sources", "key", key) if key else None

    def evidence(self, state: str, source_key: str | None, what: str) -> tuple[str, int | None]:
        """A SUPPORTED claim needs an independent source; without one it
        is kept as INFERRED (section 12: an inference never becomes fact)."""
        source = self.source(source_key)
        if source_key and source is None:
            self.skip(f"{what}: unknown source {source_key!r}, kept without it")
        if state == "SUPPORTED" and (source is None or source["origin"] != "external"):
            self.count("downgraded")
            state = "INFERRED"
        return state, source["id"] if source else None


def _upsert_node(up: _Update, table: str, code: str, fields: dict[str, Any]) -> int | None:
    """A candidate is replaced by a newer proposal; a decided record is
    kept as it is."""
    row = up.lookup(table, "code", code)
    if row is not None and row["status"] != "candidate":
        up.count(f"{table}_kept")
        return row["id"]
    columns = list(fields)
    if row is None:
        up.cur.execute(
            f"INSERT INTO {table} (code, proposed_by, update_id, {', '.join(columns)}) "
            f"VALUES (%s, %s, %s, {', '.join(['%s'] * len(columns))}) RETURNING id",
            (code, up.by, up.id, *fields.values()))
        up.count(f"{table}_created")
    else:
        sets = ", ".join(f"{c} = %s" for c in columns)
        up.cur.execute(
            f"UPDATE {table} SET {sets}, proposed_by = %s, update_id = %s, updated_at = NOW(), "
            f"revision = nextval('graph_revision') WHERE id = %s RETURNING id",
            (*fields.values(), up.by, up.id, row["id"]))
        up.count(f"{table}_updated")
    up.forget(table, code)
    return up.cur.fetchone()["id"]


def _edge(up: _Update, table: str, conflict: str, keys: tuple[str, ...],
          values: dict[str, Any]) -> None:
    """A claimed link or dependency; a newer claim replaces an active one
    and a rejected one stays rejected."""
    columns = list(values)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in keys)
    up.cur.execute(
        f"INSERT INTO {table} ({', '.join(columns)}, update_id) "
        f"VALUES ({', '.join(['%s'] * len(columns))}, %s) "
        f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}, update_id = EXCLUDED.update_id, "
        f"revision = nextval('graph_revision') WHERE {table}.status = 'active' "
        f"RETURNING (xmax = 0) AS inserted",
        (*values.values(), up.id))
    row = up.cur.fetchone()
    up.count(f"{table}_kept" if row is None else
             f"{table}_created" if row["inserted"] else f"{table}_updated")


def _range_value(attribute: str, value: Any) -> Any:
    if attribute in RANGES:
        prefix, top = RANGES[attribute]
        found = parse_range(value, prefix, top)
        return {"min": found[0], "max": found[1]} if found else None
    return value


def apply_update(cur, item: GraphUpdate) -> dict[str, Any]:
    if item.job_id is not None:
        cur.execute("SELECT 1 FROM jobs WHERE id = %s", (item.job_id,))
        if cur.fetchone() is None:
            item.job_id = None
    cur.execute(
        "INSERT INTO graph_updates (origin, proposed_by, job_id, agent_run, summary, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (item.origin, item.proposed_by, item.job_id, item.agent_run, item.summary,
         Jsonb(item.model_dump(mode="json"))))
    up = _Update(cur, cur.fetchone()["id"], item.proposed_by)

    for s in item.sources:
        row = s.row()
        cur.execute(
            f"INSERT INTO graph_sources ({', '.join(row)}, update_id) "
            f"VALUES ({', '.join(['%s'] * len(row))}, %s) ON CONFLICT (key) DO NOTHING "
            f"RETURNING id", (*row.values(), up.id))
        up.count("sources_created" if cur.fetchone() else "sources_known")

    for n in item.needs:
        category = up.category(n.category)
        if category is None:
            up.skip(f"need {n.code}: no category named {n.category!r}")
            continue
        _upsert_node(up, "needs", n.code, {"category_id": category["id"], **n.fields()})

    for c in item.capabilities:
        _upsert_node(up, "capabilities", c.code, c.fields())

    for link in item.links:
        capability = up.lookup("capabilities", "code", link.capability)
        need = up.lookup("needs", "code", link.need) if link.need else None
        category = up.category(link.category) if link.category else None
        what = f"link {link.capability} -> {link.need or link.category}"
        if capability is None:
            up.skip(f"{what}: no capability {link.capability!r}")
            continue
        if link.need and need is None:
            up.skip(f"{what}: no need {link.need!r}")
            continue
        if need is not None and category is not None and category["id"] != need["category_id"]:
            up.skip(f"{what}: need {link.need} is not in {link.category}")
            continue
        if need is None and category is None:
            up.skip(f"{what}: no category named {link.category!r}")
            continue
        state, source_id = up.evidence(link.state, link.source, what)
        _edge(up, "capability_links", "capability_id, category_id, (COALESCE(need_id, 0))",
              ("capability_id", "category_id", "need_id"), {
            "capability_id": capability["id"],
            "category_id": need["category_id"] if need else category["id"],
            "need_id": need["id"] if need else None, "relevance": link.relevance,
            "note": link.note, "state": state, "source_id": source_id,
            "locator": link.locator})

    for d in item.dependencies:
        what = f"dependency {d.capability} -> {d.requires}"
        capability = up.lookup("capabilities", "code", d.capability)
        required = up.lookup("capabilities", "code", d.requires)
        if capability is None or required is None:
            up.skip(f"{what}: unknown capability")
            continue
        if capability["id"] == required["id"]:
            up.skip(f"{what}: a capability cannot require itself")
            continue
        state, source_id = up.evidence(d.state, d.source, what)
        _edge(up, "capability_dependencies", "capability_id, requires_id",
              ("capability_id", "requires_id"), {
            "capability_id": capability["id"], "requires_id": required["id"],
            "kind": d.kind, "note": d.note, "state": state, "source_id": source_id,
            "locator": d.locator})

    for i in item.implementations:
        capability = up.lookup("capabilities", "code", i.capability)
        if capability is None or capability["status"] == "rejected":
            up.skip(f"implementation {i.code}: no usable capability {i.capability!r}")
            continue
        project_id = i.project_id
        if project_id is not None and up.lookup("projects", "id", project_id) is None:
            up.skip(f"implementation {i.code}: no project {project_id}, kept without it")
            project_id = None
        module = up.lookup("modules", "code", i.module) if i.module else None
        if i.module and module is None:
            up.skip(f"implementation {i.code}: no module {i.module!r}, kept without it")
        validation = {}
        for key, answer in i.validation.items():
            state, source_id = up.evidence(answer.state, answer.source,
                                           f"implementation {i.code} {key}")
            validation[key] = {"answer": answer.answer, "state": state,
                               "source_id": source_id, "locator": answer.locator}
        boot = parse_range(i.bootstrap_level, "B", 7) or (None, None)
        claimed = parse_range(i.evidence_maturity, "E", 5) or (0, 0)
        _upsert_node(up, "implementations", i.code, {
            "capability_id": capability["id"], "name": i.name, "description": i.description,
            "pathway": i.pathway, "role": i.role, "project_id": project_id,
            "module_id": module["id"] if module else None,
            "bootstrap_min": boot[0], "bootstrap_max": boot[1],
            "claimed_maturity_min": claimed[0], "claimed_maturity_max": claimed[1],
            "validation": Jsonb(validation), "comparison": Jsonb(i.comparison),
            "requires": Jsonb(i.requires.model_dump()),
            "external_dependencies": Jsonb(i.external_dependencies),
            "failure_modes": Jsonb(i.failure_modes), "licence_class": i.licence_class,
            "safety_critical": i.safety_critical})

    tables = {v: k for k, v in NODES.items()}
    for a in item.assessments:
        what = f"assessment {a.subject_type} {a.subject} {a.attribute}"
        subject = up.lookup(tables[a.subject_type], "code", a.subject)
        if subject is None:
            up.skip(f"{what}: no such {a.subject_type}")
            continue
        value = _range_value(a.attribute, a.value)
        if value is None:
            up.skip(f"{what}: unreadable value {a.value!r}")
            continue
        state, source_id = up.evidence(a.state, a.source, what)
        cur.execute(
            "SELECT 1 FROM graph_assessments WHERE subject_type = %s AND subject_id = %s "
            "AND attribute = %s AND value = %s AND state = %s AND source_id IS NOT DISTINCT "
            "FROM %s AND assessor = %s",
            (a.subject_type, subject["id"], a.attribute, Jsonb(value), state, source_id,
             item.proposed_by))
        if cur.fetchone():
            up.count("assessments_known")
            continue
        cur.execute(
            "INSERT INTO graph_assessments (subject_type, subject_id, attribute, value, state, "
            "source_id, locator, excerpt, basis, assessor, update_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (a.subject_type, subject["id"], a.attribute, Jsonb(value), state, source_id,
             a.locator, a.excerpt, a.basis, item.proposed_by, up.id))
        up.count("assessments_created")

    cur.execute("UPDATE graph_updates SET counts = %s WHERE id = %s",
                (Jsonb({**up.counts, "skipped": len(up.skipped)}), up.id))
    return {"update_id": up.id, "counts": up.counts, "skipped": up.skipped}


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

def _medical(cur, table: str, row: dict) -> bool:
    if table == "needs":
        cur.execute("SELECT name FROM need_categories WHERE id = %s", (row["category_id"],))
        return cur.fetchone()["name"] in MEDICAL
    capability_id = row["id"] if table == "capabilities" else row["capability_id"]
    cur.execute("SELECT 1 FROM capability_links l JOIN need_categories c ON c.id = l.category_id "
                "WHERE l.capability_id = %s AND l.status = 'active' AND c.name = ANY(%s)",
                (capability_id, list(MEDICAL)))
    return cur.fetchone() is not None


def decide(cur, kind: str, item_id: int, item: GraphDecision) -> tuple[int, dict]:
    """(HTTP status, body) for a decision on a record of ``kind``."""
    if kind in EDGES:
        status = "rejected" if item.decision == "rejected" else "active"
        cur.execute(f"UPDATE {EDGE_TABLES[kind]} SET status = %s, "
                    f"revision = nextval('graph_revision') WHERE id = %s RETURNING id, status",
                    (status, item_id))
        row = cur.fetchone()
        return (200, row) if row else (404, {"detail": "not found"})
    if kind not in NODES:
        return 404, {"detail": "not found"}
    cur.execute(f"SELECT * FROM {kind} WHERE id = %s FOR UPDATE", (item_id,))
    row = cur.fetchone()
    if row is None:
        return 404, {"detail": "not found"}
    check = (item.independent_check or "").strip()
    if item.decision == "approved":
        if kind == "capabilities":
            cur.execute("SELECT 1 FROM capability_links WHERE capability_id = %s "
                        "AND status = 'active'", (item_id,))
            if cur.fetchone() is None:
                return 409, {"detail": "link it to the needs it serves first"}
        if kind == "implementations":
            cur.execute("SELECT status FROM capabilities WHERE id = %s", (row["capability_id"],))
            if cur.fetchone()["status"] != "approved":
                return 409, {"detail": "approve its capability first"}
        if (row.get("safety_critical") or _medical(cur, kind, row)) and not check:
            # The independent check is the graph-review of this revision,
            # unless the maintainer names another one.
            review = latest_review(cur, NODES[kind], item_id, row["revision"])
            if review is None:
                return 409, {"detail": "safety-critical or medical: waiting for its independent "
                                       "review (graph-review), or name the independent check"}
            if review["recommendation"] != "approve":
                return 409, {"detail": f"its independent review recommends "
                                       f"{review['recommendation']}: revise it, or name an "
                                       f"independent check to approve anyway"}
            check = (f"graph-review {review['id']} (job {review['job_id']}, "
                     f"{review['reviewer']}): approve")
    cur.execute(
        f"UPDATE {kind} SET status = %s, decision_note = %s, decided_by = %s, "
        f"independent_check = %s, decided_at = NOW(), revision = nextval('graph_revision') "
        f"WHERE id = %s RETURNING id, code, status, independent_check",
        (item.decision, item.note or None, item.decided_by, check or None, item_id))
    return 200, cur.fetchone()


# ---------------------------------------------------------------------------
# Reading the graph
# ---------------------------------------------------------------------------

def _maturity(row: dict, supported: int | None) -> dict[str, Any]:
    """Claimed and supported evidence maturity; the level is what
    independent sourced evidence supports, E0 without any."""
    return {"claimed": show_range(row["claimed_maturity_min"], row["claimed_maturity_max"], "E"),
            "supported": None if supported is None else f"E{supported}",
            "level": supported or 0,
            "meaning": MATURITY[supported or 0]}


def _supported(cur, ids: list[int]) -> dict[int, int]:
    """The highest evidence maturity each implementation's SUPPORTED
    assessments from independent sources establish (the lower end of a
    claimed range counts)."""
    if not ids:
        return {}
    cur.execute(
        "SELECT a.subject_id, max((a.value->>'min')::int) AS level "
        "FROM graph_assessments a JOIN graph_sources s ON s.id = a.source_id "
        "WHERE a.subject_type = 'implementation' AND a.subject_id = ANY(%s) "
        "AND a.attribute = 'evidence_maturity' AND a.state = 'SUPPORTED' "
        "AND s.origin = 'external' GROUP BY a.subject_id", (ids,))
    return {r["subject_id"]: r["level"] for r in cur.fetchall()}


def _assessments(cur, subject_type: str, subject_id: int) -> list[dict]:
    """A record's assessments, with each attribute marked conflicted when
    the claims about it disagree (section 33: disagreement is kept)."""
    cur.execute(
        "SELECT a.id, a.attribute, a.value, a.state, a.locator, a.excerpt, a.basis, a.assessor, "
        "a.created_at, s.key AS source, s.title AS source_title, s.origin AS source_origin "
        "FROM graph_assessments a LEFT JOIN graph_sources s ON s.id = a.source_id "
        "WHERE a.subject_type = %s AND a.subject_id = %s ORDER BY a.attribute, a.id",
        (subject_type, subject_id))
    rows = cur.fetchall()
    values: dict[str, set] = {}
    for r in rows:
        if r["state"] in ("SUPPORTED", "INFERRED"):
            values.setdefault(r["attribute"], set()).add(repr(r["value"]))
        elif r["state"] == "CONFLICTED":
            values.setdefault(r["attribute"], set()).update({"a", "b"})
    for r in rows:
        r["conflicted"] = len(values.get(r["attribute"], ())) > 1
    return rows


def _implementation(row: dict, supported: dict[int, int]) -> dict[str, Any]:
    return {
        "id": row["id"], "code": row["code"], "name": row["name"], "status": row["status"],
        "pathway": row["pathway"], "role": row["role"], "description": row["description"],
        "bootstrap": show_range(row["bootstrap_min"], row["bootstrap_max"], "B"),
        "bootstrap_min": row["bootstrap_min"], "bootstrap_max": row["bootstrap_max"],
        "evidence_maturity": _maturity(row, supported.get(row["id"])),
        "project_id": row["project_id"], "module_id": row["module_id"],
        "licence_class": row["licence_class"], "safety_critical": row["safety_critical"],
        "validation": {k: row["validation"].get(k, {"answer": "", "state": "UNKNOWN"})
                       for k in VALIDATION},
        "comparison": row["comparison"], "requires": row["requires"],
        "external_dependencies": row["external_dependencies"],
        "failure_modes": row["failure_modes"], "revision": row["revision"],
    }


def _capability(row: dict) -> dict[str, Any]:
    out = {k: row[k] for k in ("id", "code", "name", "status", "description", "purpose",
                               "requires", "produces", "external_dependencies",
                               "self_reproduction_fraction", "resilience", "failure_modes",
                               "confidence", "uncertainties", "safety_critical", "revision")}
    out["bootstrap"] = show_range(row["bootstrap_min"], row["bootstrap_max"], "B")
    return out


def _links(cur, where: str, params: tuple) -> list[dict]:
    cur.execute(
        "SELECT l.id, l.capability_id, cap.code AS capability, cap.name AS capability_name, "
        "cap.status AS capability_status, l.category_id, c.name AS category, c.layer, c.track, "
        "l.need_id, n.code AS need, n.name AS need_name, l.relevance, l.note, l.state, "
        "l.locator, s.key AS source, l.status "
        "FROM capability_links l JOIN capabilities cap ON cap.id = l.capability_id "
        "JOIN need_categories c ON c.id = l.category_id "
        "LEFT JOIN needs n ON n.id = l.need_id LEFT JOIN graph_sources s ON s.id = l.source_id "
        f"WHERE {where} ORDER BY c.layer NULLS LAST, c.name, n.code NULLS FIRST, cap.code",
        params)
    return cur.fetchall()


def _dependencies(cur, where: str, params: tuple) -> list[dict]:
    cur.execute(
        "SELECT d.id, a.code AS capability, a.name AS capability_name, b.code AS requires, "
        "b.name AS requires_name, d.kind, d.note, d.state, d.locator, s.key AS source, d.status "
        "FROM capability_dependencies d JOIN capabilities a ON a.id = d.capability_id "
        "JOIN capabilities b ON b.id = d.requires_id "
        "LEFT JOIN graph_sources s ON s.id = d.source_id "
        f"WHERE {where} ORDER BY a.code, b.code", params)
    return cur.fetchall()


def list_needs(cur) -> list[dict]:
    cur.execute(
        "SELECT n.id, n.code, n.name, n.status, n.baseline_status, c.id AS category_id, "
        "c.name AS category, c.layer, n.revision, "
        "(SELECT count(*) FROM capability_links l WHERE l.status = 'active' AND "
        " (l.need_id = n.id OR (l.need_id IS NULL AND l.category_id = n.category_id))) "
        " AS capabilities "
        "FROM needs n JOIN need_categories c ON c.id = n.category_id "
        "ORDER BY c.layer NULLS LAST, c.name, n.code")
    return cur.fetchall()


def need_detail(cur, need_id: int, max_bootstrap: int | None = None) -> dict | None:
    """Layer, category, need, the capabilities it requires, their
    implementations with bootstrap level and supported evidence maturity,
    and the gaps the records show (section 37's chain). Capabilities linked
    to the whole category count for each of its needs. ``max_bootstrap``
    limits the implementations that count to those buildable at that level
    (section 38: B1-B2)."""
    cur.execute("SELECT n.*, c.name AS category, c.layer, c.track FROM needs n "
                "JOIN need_categories c ON c.id = n.category_id WHERE n.id = %s", (need_id,))
    need = cur.fetchone()
    if need is None:
        return None
    links = _links(cur, "l.status = 'active' AND (l.need_id = %s OR "
                        "(l.need_id IS NULL AND l.category_id = %s))",
                   (need_id, need["category_id"]))
    cap_ids = sorted({l["capability_id"] for l in links})
    cur.execute("SELECT * FROM implementations WHERE capability_id = ANY(%s) "
                "AND status <> 'rejected' ORDER BY capability_id, code", (cap_ids,))
    impls = cur.fetchall()
    supported = _supported(cur, [i["id"] for i in impls])
    capabilities, gaps = [], []
    for link in links:
        mine = [_implementation(i, supported) for i in impls
                if i["capability_id"] == link["capability_id"]]
        usable = [i for i in mine if i["status"] == "approved"
                  and (max_bootstrap is None or (i["bootstrap_min"] is not None
                                                 and i["bootstrap_min"] <= max_bootstrap))]
        best = max((i["evidence_maturity"]["level"] for i in usable), default=None)
        capabilities.append({**link, "scope": "need" if link["need_id"] else "category",
                             "implementations": mine,
                             "best_supported_maturity": None if best is None else f"E{best}"})
        if link["relevance"] == "required" and (best is None or best < 3):
            gaps.append({
                "capability": link["capability"], "capability_name": link["capability_name"],
                "kind": "no approved implementation" if best is None
                        else f"best supported evidence E{best}, below field deployed (E3)",
                "within_bootstrap": None if max_bootstrap is None else f"B{max_bootstrap}"})
    return {"need": need, "capabilities": capabilities, "gaps": gaps,
            "assessments": _assessments(cur, "need", need_id),
            "review": latest_review(cur, "need", need_id),
            "note": "Gaps are derived from approved records and sourced evidence only; "
                    "the baseline status is a separate judgement."}


def list_capabilities(cur) -> list[dict]:
    cur.execute(
        "SELECT cap.id, cap.code, cap.name, cap.status, cap.bootstrap_min, cap.bootstrap_max, "
        "cap.safety_critical, cap.revision, "
        "(SELECT coalesce(jsonb_agg(DISTINCT c.name), '[]'::jsonb) FROM capability_links l "
        " JOIN need_categories c ON c.id = l.category_id "
        " WHERE l.capability_id = cap.id AND l.status = 'active') AS serves, "
        "(SELECT count(*) FROM implementations i WHERE i.capability_id = cap.id "
        " AND i.status <> 'rejected') AS implementations "
        "FROM capabilities cap ORDER BY cap.code")
    rows = cur.fetchall()
    for r in rows:
        r["bootstrap"] = show_range(r.pop("bootstrap_min"), r.pop("bootstrap_max"), "B")
    return rows


def capability_detail(cur, capability_id: int) -> dict | None:
    """A capability with what it serves (navigation by capability, section
    29), what it requires and what requires it, its implementations and
    the assessments about it."""
    cur.execute("SELECT * FROM capabilities WHERE id = %s", (capability_id,))
    row = cur.fetchone()
    if row is None:
        return None
    cur.execute("SELECT * FROM implementations WHERE capability_id = %s ORDER BY code",
                (capability_id,))
    impls = cur.fetchall()
    supported = _supported(cur, [i["id"] for i in impls])
    implementations = []
    for i in impls:
        out = _implementation(i, supported)
        out["assessments"] = _assessments(cur, "implementation", i["id"])
        out["review"] = latest_review(cur, "implementation", i["id"])
        implementations.append(out)
    return {
        "capability": _capability(row),
        "serves": _links(cur, "l.capability_id = %s", (capability_id,)),
        "requires": _dependencies(cur, "d.capability_id = %s", (capability_id,)),
        "required_by": _dependencies(cur, "d.requires_id = %s", (capability_id,)),
        "implementations": implementations,
        "assessments": _assessments(cur, "capability", capability_id),
        "review": latest_review(cur, "capability", capability_id),
    }


def export(cur, include_candidates: bool = False) -> dict[str, Any]:
    """The graph as one self-describing document, readable without Applied
    Commons or a model (section 35): the scales, the taxonomy, the records,
    their claims and the sources they cite (metadata only, never content)."""
    wanted = ("approved", "candidate") if include_candidates else ("approved",)
    cur.execute("SELECT last_value, is_called FROM graph_revision")
    seq = cur.fetchone()
    cur.execute("SELECT id, layer, name, scope, track FROM need_categories ORDER BY layer "
                "NULLS LAST, name")
    categories = cur.fetchall()
    cur.execute("SELECT * FROM needs WHERE status = ANY(%s) ORDER BY code", (list(wanted),))
    needs = cur.fetchall()
    cur.execute("SELECT * FROM capabilities WHERE status = ANY(%s) ORDER BY code",
                (list(wanted),))
    caps = cur.fetchall()
    cap_ids = [c["id"] for c in caps]
    need_ids = [n["id"] for n in needs]
    cur.execute("SELECT * FROM implementations WHERE status = ANY(%s) AND capability_id = ANY(%s) "
                "ORDER BY code", (list(wanted), cap_ids))
    impls = cur.fetchall()
    supported = _supported(cur, [i["id"] for i in impls])
    links = [l for l in _links(cur, "l.status = 'active' AND l.capability_id = ANY(%s)",
                               (cap_ids,))
             if l["need_id"] is None or l["need_id"] in need_ids]
    deps = _dependencies(cur, "d.status = 'active' AND d.capability_id = ANY(%s) "
                              "AND d.requires_id = ANY(%s)", (cap_ids, cap_ids))
    cur.execute(
        "SELECT a.subject_type, a.subject_id, a.attribute, a.value, a.state, a.locator, "
        "a.excerpt, a.basis, a.assessor, s.key AS source FROM graph_assessments a "
        "LEFT JOIN graph_sources s ON s.id = a.source_id WHERE "
        "(a.subject_type = 'need' AND a.subject_id = ANY(%s)) OR "
        "(a.subject_type = 'capability' AND a.subject_id = ANY(%s)) OR "
        "(a.subject_type = 'implementation' AND a.subject_id = ANY(%s)) ORDER BY a.id",
        (need_ids, cap_ids, [i["id"] for i in impls]))
    assessments = cur.fetchall()
    cur.execute("SELECT key, family, source_type, title, uri, revision, bibliographic, "
                "source_quality, source_authority, origin, license_original, license_verified, "
                "attribution, derivative_status, redistribution_allowed FROM graph_sources "
                "ORDER BY key")
    sources = cur.fetchall()
    codes = {"need": {n["id"]: n["code"] for n in needs},
             "capability": {c["id"]: c["code"] for c in caps},
             "implementation": {i["id"]: i["code"] for i in impls}}
    for a in assessments:
        a["subject"] = codes[a.pop("subject_type")].get(a.pop("subject_id"))
    cap_code = codes["capability"]
    return {
        "format": "applied-commons capability graph 1",
        "graph_revision": seq["last_value"] if seq["is_called"] else 0,
        "includes": list(wanted),
        "scales": {"bootstrap": {f"B{k}": v for k, v in BOOTSTRAP.items()},
                   "evidence_maturity": {f"E{k}": v for k, v in MATURITY.items()},
                   "evidence_states": list(STATES), "validation_questions": VALIDATION},
        "categories": categories,
        "needs": [{**{k: n[k] for k in ("code", "name", "desired_state", "minimum_threshold",
                                        "baseline_threshold", "improved_threshold",
                                        "improvements", "baseline_status", "baseline_note",
                                        "status")},
                   "category_id": n["category_id"]} for n in needs],
        "capabilities": [_capability(c) for c in caps],
        "links": [{k: l[k] for k in ("capability", "category", "need", "relevance", "note",
                                     "state", "source", "locator")} for l in links],
        "dependencies": [{k: d[k] for k in ("capability", "requires", "kind", "note", "state",
                                            "source", "locator")} for d in deps],
        "implementations": [{**_implementation(i, supported),
                             "capability": cap_code[i["capability_id"]]} for i in impls],
        "assessments": assessments,
        "sources": sources,
    }


# ---------------------------------------------------------------------------
# Independent review of safety-critical and medical candidates
# ---------------------------------------------------------------------------

REVIEW = "graph-review"
REVIEW_PRIORITY = 0.85
VERDICTS = ("confirmed", "unsupported", "contradicted", "unclear")
RECOMMENDATIONS = ("approve", "revise", "reject")


class Finding(_Clipped):
    """One claim of the record, checked against a source."""
    attribute: str = "claim"
    claim: str = Field(min_length=3, max_length=600)
    verdict: str = "unclear"
    value: Any = None
    source_url: str | None = Field(default=None, max_length=2000)
    source_title: str = Field(default="", max_length=400)
    locator: str = Field(default="", max_length=300)
    note: str = Field(default="", max_length=1000)

    @field_validator("attribute", mode="before")
    @classmethod
    def _attribute(cls, value):
        text = re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").strip().lower()).strip("_")[:60]
        return text if re.fullmatch(r"[a-z][a-z0-9_.-]{1,59}", text) else "claim"

    @field_validator("verdict", mode="before")
    @classmethod
    def _verdict(cls, value):
        return _choice(value, VERDICTS, "unclear")

    @field_validator("source_url", mode="before")
    @classmethod
    def _url(cls, value):
        text = str(value or "").strip()
        return text if re.match(r"^https?://", text) else None


class GraphReview(_Clipped):
    recommendation: str
    summary: str = Field(min_length=10, max_length=2000)
    findings: list[Finding] = Field(default_factory=list, max_length=40)
    missing_hazards: list[str] = Field(default_factory=list)
    unchecked: list[str] = Field(default_factory=list)
    supported_maturity: Any = None

    @field_validator("recommendation", mode="before")
    @classmethod
    def _recommendation(cls, value):
        text = str(value or "").strip().lower()
        if text not in RECOMMENDATIONS:
            raise ValueError(f"recommendation must be one of {list(RECOMMENDATIONS)}")
        return text

    @field_validator("missing_hazards", "unchecked", mode="before")
    @classmethod
    def _lists(cls, value):
        return _texts(value, 20, 400)


class GraphReviewResult(BaseModel):
    review: GraphReview


def latest_review(cur, subject_type: str, subject_id: int,
                  revision: int | None = None) -> dict | None:
    """The newest review of a record (of that revision, if given)."""
    cur.execute(
        "SELECT * FROM graph_reviews WHERE subject_type = %s AND subject_id = %s "
        "AND (%s::bigint IS NULL OR revision = %s) ORDER BY created_at DESC, id DESC LIMIT 1",
        (subject_type, subject_id, revision, revision))
    return cur.fetchone()


def review_due(cur) -> list[tuple[str, dict]]:
    """Candidates that need an independent review: safety-critical ones, and
    medical ones (records in or serving the health and early-life
    categories). A review covers one revision; a changed candidate is
    reviewed again."""
    medical = list(MEDICAL)
    cur.execute(
        "SELECT n.* FROM needs n JOIN need_categories c ON c.id = n.category_id "
        "WHERE n.status = 'candidate' AND c.name = ANY(%s) ORDER BY n.id", (medical,))
    due = [("need", r) for r in cur.fetchall()]
    cur.execute(
        "SELECT cap.* FROM capabilities cap WHERE cap.status = 'candidate' AND "
        "(cap.safety_critical OR EXISTS (SELECT 1 FROM capability_links l JOIN need_categories c "
        " ON c.id = l.category_id WHERE l.capability_id = cap.id AND l.status = 'active' "
        " AND c.name = ANY(%s))) ORDER BY cap.id", (medical,))
    due += [("capability", r) for r in cur.fetchall()]
    cur.execute(
        "SELECT i.* FROM implementations i WHERE i.status = 'candidate' AND "
        "(i.safety_critical OR EXISTS (SELECT 1 FROM capability_links l JOIN need_categories c "
        " ON c.id = l.category_id WHERE l.capability_id = i.capability_id AND l.status = 'active' "
        " AND c.name = ANY(%s))) ORDER BY i.id", (medical,))
    due += [("implementation", r) for r in cur.fetchall()]
    return [(kind, row) for kind, row in due
            if latest_review(cur, kind, row["id"], row["revision"]) is None]


def _review_record(cur, kind: str, row: dict) -> dict[str, Any]:
    if kind == "need":
        cur.execute("SELECT name, layer FROM need_categories WHERE id = %s", (row["category_id"],))
        category = cur.fetchone()
        record = {k: row[k] for k in ("code", "name", "desired_state", "minimum_threshold",
                                      "baseline_threshold", "improved_threshold",
                                      "improvements", "baseline_status", "baseline_note")}
        return {**record, "category": category["name"], "layer": category["layer"]}
    if kind == "capability":
        detail = capability_detail(cur, row["id"])
        return {**detail["capability"],
                "serves": [{k: l[k] for k in ("category", "need", "relevance", "note", "state",
                                              "source", "locator")} for l in detail["serves"]],
                "requires": [{k: d[k] for k in ("requires", "kind", "note", "state")}
                             for d in detail["requires"]],
                "implementations": [{k: i[k] for k in ("code", "name", "pathway", "bootstrap")}
                                    for i in detail["implementations"]]}
    supported = _supported(cur, [row["id"]])
    cur.execute("SELECT code, name, description FROM capabilities WHERE id = %s",
                (row["capability_id"],))
    return {**_implementation(row, supported), "capability": cur.fetchone()}


def review_input(cur, kind: str, row: dict) -> dict[str, Any]:
    """The job input for an independent review of one candidate."""
    assessments = _assessments(cur, kind, row["id"])
    keys = {a["source"] for a in assessments if a.get("source")}
    record = _review_record(cur, kind, row)
    if kind == "implementation":
        keys |= {s for s in (v.get("source_id") for v in row["validation"].values()) if s}
    cur.execute("SELECT key, title, uri, revision FROM graph_sources WHERE key = ANY(%s) "
                "OR id = ANY(%s) ORDER BY key",
                ([k for k in keys if isinstance(k, str)], [k for k in keys if isinstance(k, int)]))
    sources = cur.fetchall()
    return {
        "subject_type": kind, "subject_id": row["id"], "revision": row["revision"],
        "proposed_by": row["proposed_by"],
        "record": record,
        "claims": [{k: a[k] for k in ("attribute", "value", "state", "source", "locator",
                                      "basis", "assessor")} for a in assessments],
        "sources": sources,
        "instructions": (
            "Independently review this candidate record in Applied Commons' capability "
            "graph before the maintainer decides on it. It is safety-critical or medical, "
            "and it was drafted by someone else (proposed_by): you are its independent "
            "check, so do not take its claims on trust. Check each material claim, such "
            "as evidence maturity, validation answers, performance figures and thresholds, "
            "hazards and failure modes, and bootstrap level, against the cited source or "
            "other reputable sources you retrieve yourself (standards bodies, WHO and other "
            "UN agencies, peer-reviewed studies, established engineering references, "
            "practitioner organisations). Applied Commons' own pages do not count. Give "
            "one finding per claim: attribute (what the claim is about, e.g. "
            "evidence_maturity, validation.built, threshold, hazard), the claim, and a "
            "verdict: confirmed (a retrieved source says so: give its URL and the page or "
            "section), unsupported (nothing found either way), contradicted (a source "
            "says otherwise: give it) or unclear. For an evidence_maturity finding give "
            "value as E0-E5. For an implementation, supported_maturity is the evidence "
            "maturity the sources you checked establish (E0 concept, E1 prototype, E2 "
            "independently replicated, E3 field deployed, E4 established engineering, "
            "E5 industrial commodity), or null. List hazards and failure modes the "
            "record misses. recommendation: approve (the claims hold and the hazards are "
            "covered), revise (say what to change in the summary) or reject (unsafe or "
            "wrong). List what you could not check. Health and safety material needs "
            "conservative inference: an unknown stays unknown. " + research._COMMON
        ),
        "output_format": (
            '{"summary": "<one paragraph>", "review": {"recommendation": '
            '"approve|revise|reject", "summary": "", "findings": [{"attribute": '
            '"evidence_maturity", "claim": "", "verdict": "confirmed|unsupported|'
            'contradicted|unclear", "value": "E3", "source_url": "<url>", '
            '"source_title": "", "locator": "<page or section>", "note": ""}], '
            '"missing_hazards": [""], "unchecked": [""], "supported_maturity": "E3"}, '
            '"evidence": [{"source_uri": "<url>", "title": "", "source_type": ""}]}'
        ),
    }


def store_review(cur, job: dict, result: GraphReviewResult) -> dict[str, Any]:
    """Record the review against the revision it checked, and its sourced
    findings as assessments by the reviewer (sources it cites are added)."""
    payload = job["input"] or {}
    kind, subject_id = payload.get("subject_type"), payload.get("subject_id")
    table = {v: k for k, v in NODES.items()}.get(kind)
    if table is None:
        raise ValueError("graph-review job without a subject")
    cur.execute(f"SELECT code FROM {table} WHERE id = %s", (subject_id,))
    subject = cur.fetchone()
    if subject is None:
        return {"review": None, "note": "the record no longer exists"}
    r = result.review
    reviewer = f"ac-research worker (graph-review job {job['id']})"
    maturity = (parse_range(r.supported_maturity, "E", 5) if kind == "implementation"
                else None) or (None, None)
    cur.execute(
        "INSERT INTO graph_reviews (subject_type, subject_id, revision, job_id, reviewer, "
        "recommendation, summary, findings, missing_hazards, unchecked, supported_maturity_min, "
        "supported_maturity_max) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id",
        (kind, subject_id, payload.get("revision") or 0, job["id"], reviewer, r.recommendation,
         r.summary, Jsonb([f.model_dump() for f in r.findings]), Jsonb(r.missing_hazards),
         Jsonb(r.unchecked), maturity[0], maturity[1]))
    review_id = cur.fetchone()["id"]
    sources, assessments = {}, []
    for f in r.findings:
        if f.source_url:
            host = re.sub(r"^https?://(www\.)?", "", f.source_url).split("/")[0]
            sources[f.source_url[:200]] = SourceIn(
                key=f.source_url[:200], family=host or "web", source_type="web",
                title=f.source_title or f.source_url[:400], uri=f.source_url)
        state = {"confirmed": "SUPPORTED", "contradicted": "CONFLICTED"}.get(f.verdict, "UNKNOWN")
        if state == "SUPPORTED" and not f.source_url:
            state = "INFERRED"
        value = f.value if f.attribute in RANGES and f.value is not None else f.claim
        assessments.append(AssessmentIn(
            subject_type=kind, subject=subject["code"], attribute=f.attribute, value=value,
            state=state, source=f.source_url[:200] if f.source_url else None,
            locator=f.locator, basis=f"{f.verdict}: {f.note}"[:1000]))
    applied = apply_update(cur, GraphUpdate(
        origin="applied-commons", proposed_by=reviewer, job_id=job["id"],
        summary=f"Independent review {review_id}: {r.recommendation}",
        sources=list(sources.values()), assessments=assessments))
    return {"review": review_id, "recommendation": r.recommendation,
            "assessments": applied["counts"].get("assessments_created", 0)}
