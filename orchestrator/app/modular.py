"""Stage 2, the modular set: interface standards, modules, systems and the
roadmap (policy section 4e; maintainer decisions 2026-10-07).

Like Open Source Ecology's Global Village Construction Set, but across
every need and enabler: reusable open hardware and software modules that
connect through shared interface standards, composed into systems for
five scenarios, sequenced by a roadmap that feeds field trials at the
proving ground (stage 3).

- standards-synthesis (monthly): propose interface standards from
  everything stage 1 has found. The maintainer approves or rejects each.
- module-synthesis (monthly, per need or enabler domain, once standards
  are approved): reusable modules specified against approved standards,
  costed at EU and low-income-country prices.
- system-design (monthly, per scenario, after the month's modules): the
  engineering step, composing modules into systems with calculations kept
  as code.
- roadmap-revision (monthly, after the month's systems): the ordered plan.
  The maintainer approves each version.

Models may only claim a module is a concept or documented; built and
tested come from trials.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

import research

STANDARDS = "standards-synthesis"
VERIFY = "standard-verification"
MODULES = "module-synthesis"
SYSTEMS = "system-design"
ROADMAP = "roadmap-revision"
REVIEW = "standards-review"
JOB_TYPES = (STANDARDS, VERIFY, MODULES, SYSTEMS, ROADMAP, REVIEW)
PRIORITY = 0.88
VERIFY_PRIORITY = 0.9

#: Keeping approved standards current (policy section 4e): a monthly
#: standards-review of each against what has been found since, and a
#: re-verification every REVERIFY_DAYS against current editions of the
#: established standards.
REVIEW_PRIORITY = 0.86
REVERIFY_DAYS = 365
REVIEW_OUTCOMES = ("keep", "revise", "retire")

#: Verification of a proposed standard (policy section 4e).
OUTCOMES = ("aligned", "deviates", "no-standard", "unsafe")
RECOMMENDATIONS = ("approve", "revise", "reject")
RISKS = ("low", "medium", "high")

#: The five scenarios systems are designed for (maintainer, 2026-10-07).
SCENARIOS = {
    "household": "A single-family household (4 to 6 people) on a small plot.",
    "homestead": "The same family on a larger plot (about 1 to 5 ha) with gardens, "
                 "animals and outbuildings.",
    "small-farm": "A small farm (about 5 to 20 ha) producing for sale, with a few workers.",
    "community-facility": "A shared facility such as a clinic or school serving about "
                          "50 to 500 people a day.",
    "village": "A village of about 50 to 200 households (300 to 1,000 people) sharing "
               "infrastructure.",
}
STANDARD_KINDS = ("electrical", "fluid", "mechanical", "data", "thermal", "software", "other")
MODULE_KINDS = ("hardware", "software", "mixed")
STEP_KINDS = ("standard", "module", "system", "trial", "other")
MATURITY = ("concept", "documented", "built", "tested")
MODEL_MATURITY = ("concept", "documented")

MIN_ASSESSED_FOR_STANDARDS = 10
MIN_ASSESSED_PER_DOMAIN = 2
MIN_MODULES_FOR_SYSTEMS = 5


def spec_hash(spec: str) -> str:
    """Identifies the version of a standard's spec that was verified."""
    return hashlib.sha256(spec.encode()).hexdigest()[:16]


def month(now: float | None = None) -> str:
    return time.strftime("%Y%m", time.gmtime(now))


def slug(text: Any, limit: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:limit]


def _choice(value: Any, options: tuple[str, ...], default: str) -> str:
    value = str(value or "").strip().lower()
    return value if value in options else default


def _money(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


# ---------------------------------------------------------------------------
# Result contracts
# ---------------------------------------------------------------------------


class _Coded(BaseModel):
    code: str
    name: str = Field(min_length=3, max_length=160)

    @field_validator("code")
    @classmethod
    def _code(cls, value):
        value = slug(value)
        if len(value) < 3:
            raise ValueError("code must have at least 3 letters or digits")
        return value


class StandardIn(_Coded):
    kind: str = "other"
    spec: str = Field(min_length=10, max_length=3000)
    rationale: str = Field(min_length=10, max_length=2000)
    used_by: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("kind")
    @classmethod
    def _kind(cls, value):
        return _choice(value, STANDARD_KINDS, "other")

    @field_validator("used_by")
    @classmethod
    def _used_by(cls, items):
        return [str(i)[:200] for i in items]


class StandardsResult(BaseModel):
    standards: list[StandardIn] = Field(max_length=10)


class Reference(BaseModel):
    body: str = Field(default="", max_length=40)
    number: str = Field(min_length=1, max_length=80)
    title: str = Field(default="", max_length=300)
    url: str | None = None
    relevance: str = Field(default="", max_length=600)

    @field_validator("url")
    @classmethod
    def _url(cls, value):
        return research._check_url(value)


class Deviation(BaseModel):
    reference: str = Field(default="", max_length=120)
    deviation: str = Field(min_length=5, max_length=1500)
    risk: str = "medium"
    justification: str = Field(default="", max_length=1500)

    @field_validator("risk")
    @classmethod
    def _risk(cls, value):
        return _choice(value, RISKS, "medium")


class Verification(BaseModel):
    outcome: str
    safety_critical: bool
    references: list[Reference] = Field(default_factory=list, max_length=15)
    deviations: list[Deviation] = Field(default_factory=list, max_length=15)
    required_controls: list[str] = Field(default_factory=list, max_length=15)
    recommendation: str
    rationale: str = Field(min_length=20, max_length=3000)

    @field_validator("outcome")
    @classmethod
    def _outcome(cls, value):
        value = str(value).strip().lower()
        if value not in OUTCOMES:
            raise ValueError(f"outcome must be one of {list(OUTCOMES)}")
        return value

    @field_validator("recommendation")
    @classmethod
    def _recommendation(cls, value):
        value = str(value).strip().lower()
        if value not in RECOMMENDATIONS:
            raise ValueError(f"recommendation must be one of {list(RECOMMENDATIONS)}")
        return value

    @field_validator("required_controls")
    @classmethod
    def _controls(cls, items):
        return [str(i)[:500] for i in items]

    @model_validator(mode="after")
    def _deviations_called_out(self):
        # Every deviation is called out; listed deviations make it "deviates".
        if self.outcome in ("deviates", "unsafe") and not self.deviations:
            raise ValueError(f"outcome {self.outcome!r} needs the deviations listed")
        if self.outcome == "aligned" and self.deviations:
            self.outcome = "deviates"
        return self


class VerificationResult(BaseModel):
    verification: Verification


class Interface(BaseModel):
    standard: str
    role: Literal["provides", "consumes"] = "provides"
    detail: str = Field(default="", max_length=500)

    @field_validator("standard")
    @classmethod
    def _standard(cls, value):
        return slug(value)


class BomLine(BaseModel):
    part: str = Field(min_length=1, max_length=300)
    quantity: float | str | None = None
    unit_cost_eu: float | None = None
    unit_cost_low_income: float | None = None
    notes: str = Field(default="", max_length=500)

    @field_validator("unit_cost_eu", "unit_cost_low_income")
    @classmethod
    def _cost(cls, value):
        return _money(value)


class ModuleIn(_Coded):
    kind: str = "hardware"
    purpose: str = Field(min_length=10, max_length=3000)
    interfaces: list[Interface] = Field(default_factory=list, max_length=20)
    variants: list[str] = Field(default_factory=list, max_length=10)
    bom: list[BomLine] = Field(default_factory=list, max_length=60)
    cost_eu: float | None = None
    cost_low_income: float | None = None
    maturity: str = "concept"
    source_projects: list[str] = Field(default_factory=list, max_length=30)
    new_standards: list[StandardIn] = Field(default_factory=list, max_length=5)
    notes: str = Field(default="", max_length=3000)

    @field_validator("kind")
    @classmethod
    def _kind(cls, value):
        return _choice(value, MODULE_KINDS, "hardware")

    @field_validator("maturity")
    @classmethod
    def _maturity(cls, value):
        return _choice(value, MODEL_MATURITY, "concept")

    @field_validator("cost_eu", "cost_low_income")
    @classmethod
    def _cost(cls, value):
        return _money(value)

    @field_validator("variants")
    @classmethod
    def _variants(cls, items):
        return [str(i)[:500] for i in items]


class ModulesResult(BaseModel):
    modules: list[ModuleIn] = Field(max_length=8)


class Calculation(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    method: str = Field(default="", max_length=2000)
    inputs: str = Field(default="", max_length=3000)
    result: str = Field(min_length=1, max_length=2000)
    code: str = Field(default="", max_length=8000)
    label: str = "calculated"

    @field_validator("label")
    @classmethod
    def _label(cls, value):
        return _choice(value, ("calculated", "simulated"), "calculated")


class SystemModule(BaseModel):
    module: str
    quantity: float | str | None = None
    role: str = Field(default="", max_length=500)

    @field_validator("module")
    @classmethod
    def _module(cls, value):
        return slug(value)


class SystemIn(_Coded):
    purpose: str = Field(min_length=10, max_length=3000)
    modules: list[SystemModule] = Field(min_length=1, max_length=40)
    calculations: list[Calculation] = Field(default_factory=list, max_length=20)
    interface_check: str = Field(default="", max_length=4000)
    cost_eu: float | None = None
    cost_low_income: float | None = None
    failure_modes: list[str] = Field(default_factory=list, max_length=20)
    missing_modules: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("cost_eu", "cost_low_income")
    @classmethod
    def _cost(cls, value):
        return _money(value)

    @field_validator("failure_modes", "missing_modules")
    @classmethod
    def _texts(cls, items):
        return [str(i)[:600] for i in items]


class SystemsResult(BaseModel):
    systems: list[SystemIn] = Field(min_length=1, max_length=3)


class RoadmapStep(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    kind: str = "other"
    refs: list[str] = Field(default_factory=list, max_length=20)
    depends_on: list[int] = Field(default_factory=list, max_length=20)
    rationale: str = Field(default="", max_length=3000)
    season: str = Field(default="", max_length=200)
    target_climates: list[str] = Field(default_factory=list, max_length=5)
    cost_eu: float | None = None
    cost_low_income: float | None = None

    @field_validator("kind")
    @classmethod
    def _kind(cls, value):
        return _choice(value, STEP_KINDS, "other")

    @field_validator("refs")
    @classmethod
    def _refs(cls, items):
        return [slug(i) for i in items if slug(i)]

    @field_validator("target_climates")
    @classmethod
    def _climates(cls, items):
        return research._check_climates(items)

    @field_validator("cost_eu", "cost_low_income")
    @classmethod
    def _cost(cls, value):
        return _money(value)


class Roadmap(BaseModel):
    summary: str = Field(min_length=20, max_length=6000)
    steps: list[RoadmapStep] = Field(min_length=1, max_length=60)


class RoadmapResult(BaseModel):
    roadmap: Roadmap


class Conflict(BaseModel):
    finding: str = Field(min_length=5, max_length=800)
    source: str = Field(default="", max_length=300)


class StandardReview(BaseModel):
    code: str
    recommendation: str
    rationale: str = Field(min_length=10, max_length=2000)
    conflicts: list[Conflict] = Field(default_factory=list, max_length=10)
    revision: StandardIn | None = None

    @field_validator("code")
    @classmethod
    def _code(cls, value):
        return slug(value)

    @field_validator("recommendation")
    @classmethod
    def _recommendation(cls, value):
        value = str(value).strip().lower()
        if value not in REVIEW_OUTCOMES:
            raise ValueError(f"recommendation must be one of {list(REVIEW_OUTCOMES)}")
        return value

    @model_validator(mode="after")
    def _revision_only_when_revising(self):
        if self.recommendation == "revise" and self.revision is None:
            raise ValueError("a revise recommendation needs the revised standard")
        if self.recommendation != "revise":
            self.revision = None
        return self


class StandardsReviewResult(BaseModel):
    reviews: list[StandardReview] = Field(min_length=1, max_length=40)


def validate(job_type: str, output: dict) -> Any:
    model = {STANDARDS: StandardsResult, VERIFY: VerificationResult,
             MODULES: ModulesResult, SYSTEMS: SystemsResult,
             ROADMAP: RoadmapResult, REVIEW: StandardsReviewResult}[job_type]
    return model(**output)


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------

_SET = (
    "Applied Commons is building a modular set of open hardware and software "
    "building blocks, like Open Source Ecology's Global Village Construction "
    "Set but across every human need and enabler (energy, water, food, "
    "shelter, health, communications, fabrication and more). Modules connect "
    "through shared interface standards, so they can be combined and swapped. "
    "The cheaper and more locally buildable the better: results must stay "
    "usable in low-income countries as well as in cold climates."
)
_COSTS = (
    "Give costs in US dollars twice: at EU prices, and at low-income-country "
    "prices using the cheapest locally available substitutes."
)

_STANDARD_FORMAT = (
    '{"code": "<short-slug>", "name": "", "kind": "electrical|fluid|'
    'mechanical|data|thermal|software|other", "spec": "<precise values>", '
    '"rationale": "<why; cost and availability>", "used_by": ["<project or '
    'module idea>"]}'
)


def standards_input(projects: list[dict], standards: list[dict]) -> dict[str, Any]:
    return {
        "projects": projects,
        "existing_standards": standards,
        "instructions": (
            f"{_SET} Propose up to 10 interface standards that would let the "
            "projects below (and the modules drawn from them) connect: "
            "electrical (DC bus voltages, connectors, protection), fluid (pipe "
            "sizes, threads, pressures), mechanical (mounting, frame profiles, "
            "fasteners), data (protocols, connectors, formats), thermal, and "
            "software (APIs, data schemas). Prefer existing open or widely used "
            "standards (ISO, IEC, common parts) that are cheap and available "
            "worldwide. Do not repeat existing_standards; fill gaps or propose "
            "a revision under a new code. The maintainer approves each one "
            "before modules use it. " + research._COMMON
        ),
        "output_format": ('{"summary": "<one paragraph>", "standards": ['
                          + _STANDARD_FORMAT + '], "evidence": [{"source_uri": '
                          '"<url>", "title": "", "source_type": ""}]}'),
    }


def verification_input(standard: dict) -> dict[str, Any]:
    return {
        "standard": {k: standard[k] for k in ("code", "name", "kind", "spec", "rationale")},
        "instructions": (
            "Verify this proposed interface standard for Applied Commons' modular "
            "set before the maintainer decides on it; the aim is to build "
            "nothing unsafe without adding needless overhead. Find the "
            "established standards that govern this interface: international "
            "(ISO, IEC), European (EN, and national adoptions such as DIN or "
            "EVS-EN), Australian/New Zealand (AS/NZS), and others where relevant "
            "(for example UL, NFPA, ASME). Prefer adopting an existing standard "
            "outright. Cite each relevant standard (body, number, title, a link "
            "to its catalogue or summary page, why it applies). List every way "
            "the proposal deviates from them, with its risk (low, medium, high) "
            "and any justification; do not hide deviations. safety_critical: "
            "true if a fault could injure people or damage property (mains or "
            "high current electricity, gas, pressure, heat, structures and "
            "lifting, potable water, food contact). outcome: aligned (consistent "
            "with the cited standards), deviates (any deviation), no-standard "
            "(nothing applicable found), unsafe (conflicts with a safety "
            "requirement). recommendation: approve, revise (say what in "
            "required_controls) or reject. Full texts are often paywalled: work "
            "from official catalogue pages, summaries and reputable secondary "
            "sources, and say where a clause could not be checked. Be concise. "
            + research._COMMON
        ),
        "output_format": (
            '{"summary": "<one paragraph>", "verification": {"outcome": '
            '"aligned|deviates|no-standard|unsafe", "safety_critical": false, '
            '"references": [{"body": "IEC", "number": "IEC 60364-4-41", '
            '"title": "", "url": "<url>", "relevance": ""}], "deviations": '
            '[{"reference": "<standard number>", "deviation": "", "risk": '
            '"low|medium|high", "justification": ""}], "required_controls": '
            '[""], "recommendation": "approve|revise|reject", "rationale": ""}, '
            '"evidence": [{"source_uri": "<url>", "title": "", "source_type": ""}]}'
        ),
    }


def module_input(category: dict, brief: dict | None, projects: list[dict],
                 standards: list[dict], modules: list[dict],
                 superseded: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "domain": category["name"],
        **({"track": category["track"]} if category.get("track") else {}),
        **({"need_brief": brief} if brief else {}),
        "projects": projects,
        "approved_standards": standards,
        "existing_modules": modules,
        # A module still on a superseded standard moves to its successor.
        **({"superseded_standards": superseded,
            "superseded_note": "existing_modules that use a standard listed in "
                               "superseded_standards (old code: new code) must be "
                               "updated to the new standard, keeping their code."}
           if superseded else {}),
        "scenarios": SCENARIOS,
        "instructions": (
            f"{_SET} For the domain {category['name']}, identify up to 8 "
            "reusable modules: self-contained building blocks (hardware, "
            "software or both) that several of the projects below, or several "
            "systems in the scenarios, could share. Base them on the projects' "
            "documented designs, and only on designs whose licences allow "
            "modification and commercial use (such as CERN-OHL, CC BY or BY-SA, "
            "GPL, MIT, Apache). Applied Commons releases modules under "
            "CERN-OHL-S-2.0 (one derived from a share-alike design keeps that "
            "design's licence, which you must name in notes), so never build on "
            "a non-commercial or unlicensed "
            "design; name it in notes instead. Each project's licence_class "
            "comes from a source check of its repository: build only on open or "
            "share-alike ones; restricted or none means do not build on it; "
            "unknown or unchecked means confirm the licence from its repository "
            "first and give it in notes. Connect modules only through "
            "approved_standards "
            "(reference each by its code in interfaces); if a module needs an "
            "interface no approved standard covers, propose that standard in "
            "new_standards and the module waits for its approval. Reuse or "
            "extend existing_modules by giving the same code rather than "
            "duplicating them. List each module's bill of materials. "
            + _COSTS + " maturity: concept (an idea) or documented (public "
            "designs exist). source_projects: names from projects. "
            + research._COMMON
        ),
        "output_format": (
            '{"summary": "<one paragraph>", "modules": [{"code": "<short-slug>", '
            '"name": "", "kind": "hardware|software|mixed", "purpose": "", '
            '"interfaces": [{"standard": "<standard code>", "role": '
            '"provides|consumes", "detail": ""}], "variants": [""], "bom": '
            '[{"part": "", "quantity": 1, "unit_cost_eu": null, '
            '"unit_cost_low_income": null, "notes": ""}], "cost_eu": null, '
            '"cost_low_income": null, "maturity": "concept|documented", '
            '"source_projects": [""], "new_standards": [' + _STANDARD_FORMAT
            + '], "notes": ""}], "evidence": [{"source_uri": "<url>", "title": '
            '"", "source_type": ""}]}'
        ),
    }


def system_input(scenario: str, briefs: list[dict], modules: list[dict],
                 standards: list[dict]) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "scenario_description": SCENARIOS[scenario],
        "needs": briefs,
        "modules": modules,
        "approved_standards": standards,
        "instructions": (
            f"{_SET} Design up to 3 systems for this scenario, each composed of "
            "modules from the list (by code), that together meet the most "
            "important needs. Do the engineering: for each system, calculations "
            "such as power and energy budgets, water demand, flow and head, "
            "thermal loads, storage and structural capacity. Run them in Python "
            "and give the code, its inputs and its results; label each "
            "calculated or simulated. Check that the modules' interfaces "
            "connect (interface_check). " + _COSTS + " List failure_modes and "
            "missing_modules (modules the system needs that do not exist yet). "
            + research._COMMON
        ),
        "output_format": (
            '{"summary": "<one paragraph>", "systems": [{"code": "<short-slug>", '
            '"name": "", "purpose": "", "modules": [{"module": "<module code>", '
            '"quantity": 1, "role": ""}], "calculations": [{"name": "", '
            '"method": "", "inputs": "", "result": "", "code": "<python>", '
            '"label": "calculated|simulated"}], "interface_check": "", '
            '"cost_eu": null, "cost_low_income": null, "failure_modes": [""], '
            '"missing_modules": [""]}], "evidence": [{"source_uri": "<url>", '
            '"title": "", "source_type": ""}]}'
        ),
    }


def roadmap_input(version: str, systems: list[dict], modules: list[dict],
                  standards: list[dict], previous: dict | None,
                  sites: list[dict]) -> dict[str, Any]:
    return {
        "version": version,
        "systems": systems,
        "modules": modules,
        "approved_standards": standards,
        **({"previous_roadmap": previous} if previous else {}),
        "proving_ground": sites,
        "scenarios": SCENARIOS,
        "instructions": (
            f"{_SET} Write the roadmap: an ordered plan of steps, each a "
            "standard to settle, a module to develop, a system to integrate, or "
            "a field trial at the proving ground. Order by dependency (depends_on "
            "gives earlier step numbers, counting from 1) and by leverage: "
            "modules reused by the most systems first, the cheapest steps that "
            "settle the biggest uncertainties first. For each trial give the "
            "season and target_climates: the site's summer stands in for "
            "temperate climates, its winter for cold ones, so the site does not "
            "limit the climates tested. Prefer steps that are cheap and "
            "transferable to low-income countries. " + _COSTS + " In summary, "
            "explain the plan and what changed from previous_roadmap. "
            + research._COMMON
        ),
        "output_format": (
            '{"summary": "<one paragraph>", "roadmap": {"summary": "", "steps": '
            '[{"title": "", "kind": "standard|module|system|trial", "refs": '
            '["<standard, module or system code>"], "depends_on": [1], '
            '"rationale": "", "season": "", "target_climates": ["cold"], '
            '"cost_eu": null, "cost_low_income": null}]}, "evidence": '
            '[{"source_uri": "<url>", "title": "", "source_type": ""}]}'
        ),
    }


def standards_review_input(standards: list[dict], projects: list[dict],
                           modules: list[dict]) -> dict[str, Any]:
    return {
        "approved_standards": standards,
        "projects": projects,
        "modules": modules,
        "instructions": (
            f"{_SET} Review each approved standard below against everything "
            "found so far: the projects (including findings since it was "
            "approved) and the modules built on it. adoption gives counts from "
            "the records; do not recount. List conflicts: findings that "
            "contradict the standard or show it fits poorly (most comparable "
            "designs use a different value; a part has become scarce or costly "
            "where it is needed; modules need workarounds to meet it; a safety "
            "concern), each with its source (a project name or URL). Recommend "
            "keep (it still fits), revise (give the revised standard in "
            "revision, under a new code; it replaces this one once the "
            "maintainer approves it and is verified first) or retire (no longer "
            "useful). Change a standard only for a clear reason: every module "
            "built on it would have to change. " + research._COMMON
        ),
        "output_format": (
            '{"summary": "<one paragraph>", "reviews": [{"code": "<standard code>", '
            '"recommendation": "keep|revise|retire", "rationale": "", "conflicts": '
            '[{"finding": "", "source": "<project or url>"}], "revision": '
            + _STANDARD_FORMAT + ' or null}], "evidence": [{"source_uri": "<url>", '
            '"title": "", "source_type": ""}]}'
        ),
    }
