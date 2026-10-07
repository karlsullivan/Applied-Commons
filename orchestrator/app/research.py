"""Applied Commons autonomous research pipeline: job specifications,
result validation, rubric scoring and admission.

The policy this implements is orchestrator/policies/project-selection.md
(sections 4a to 4d). Applied Commons owns what each job asks for and
how results are judged; executors (Apollo/Hermes, the NUC worker) only
run the instructions carried in each job's input.
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

RUBRIC_VERSION = "rubric-v1"
POLICY_REVISION = ("project-selection 2026-10-06 (rubric v1 admission, need briefs, "
                   "go/no-go review, build packs)")
DECISION_AUTHOR = "applied-commons orchestrator, rubric v1"
REVIEW_AUTHOR = "applied-commons orchestrator, review v1"
STEER_AUTHOR = "maintainer (steer)"

#: Pipeline job types (all executed by Apollo through Hermes).
NEED_BRIEF = "need-brief"
DISCOVERY = "candidate-discovery"
ASSESSMENT = "candidate-assessment"
LITERATURE = "literature-collection"
REVIEW = "project-review"
BUILD_PACK = "build-pack"

#: A category's need brief is refreshed after this many days.
BRIEF_REFRESH_DAYS = 90

#: Go/no-go review (policy section 4b). A project gets at most
#: MAX_REVIEWS reviews; a "continue" on the last one parks it.
MAX_REVIEWS = 3
BUILD_THRESHOLD = 0.60
BUILD_MIN_EVIDENCE = 0.4

#: Build pack sections, each one job (policy section 4c).
BUILD_SECTIONS = ("bom", "design", "assembly", "test")

#: Maintainer overrides (policy section 4d).
STEERS = ("build", "park")

#: Layers whose categories are scanned for candidates (policy section 1:
#: the development focus is the physiological and safety layers).
DISCOVERY_LAYERS = (1, 2)
DISCOVERY_PERIOD_DAYS = 14

#: Rubric v1 weights (sum to 1). The evidence score is not weighted in;
#: it scales the composite (see composite_score).
WEIGHTS = {
    "need_layer": 0.20,
    "need_severity_reach": 0.20,
    "effectiveness": 0.15,
    "ease": 0.15,
    "cost": 0.15,
    "practicality": 0.15,
}
LAYER_SCORE = {1: 1.0, 2: 0.8, 3: 0.4, 4: 0.3, 5: 0.2}
ASSESSED_DIMENSIONS = (
    "need_severity_reach", "effectiveness", "ease", "cost", "practicality",
    "evidence",
)
REQUIREMENTS = (
    "need", "baseline", "improvement", "demonstration", "burden_removed",
    "practical_independence",
)

ADMIT_THRESHOLD = 0.55
ADMIT_MIN_EVIDENCE = 0.3
ADMIT_MIN_SOURCES = 2


def max_active_projects() -> int:
    return int(os.environ.get("MAX_ACTIVE_PROJECTS", "8"))


def composite_score(layer: int, scores: dict[str, float]) -> float:
    """Weighted rubric score scaled by evidence confidence.

    Weak evidence discounts a candidate (down to half) rather than
    zeroing it, so a promising but under-evidenced candidate stays on the
    shortlist without being admitted on unsupported claims.
    """
    values = {"need_layer": LAYER_SCORE[layer], **scores}
    weighted = sum(WEIGHTS[k] * values[k] for k in WEIGHTS)
    return round(weighted * (0.5 + 0.5 * scores["evidence"]), 4)


def slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "x"


# ---------------------------------------------------------------------------
# Result contracts (validated before anything is written)
# ---------------------------------------------------------------------------

_HTTP = r"^https?://"


class Candidate(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    summary: str = Field(min_length=10, max_length=2000)
    source_uris: list[str] = Field(min_length=1, max_length=10)

    @field_validator("source_uris")
    @classmethod
    def _http(cls, uris):
        for uri in uris:
            if not re.match(_HTTP, uri) or len(uri) > 2000:
                raise ValueError("source_uris must be http(s) URLs")
        return uris


class DiscoveryResult(BaseModel):
    candidates: list[Candidate] = Field(max_length=20)


class Assessment(BaseModel):
    scores: dict[str, float]
    requirements: dict[str, str]
    rationale: str = Field(min_length=20, max_length=4000)
    questions: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("scores")
    @classmethod
    def _scores(cls, scores):
        if set(scores) != set(ASSESSED_DIMENSIONS):
            raise ValueError(f"scores must be exactly {list(ASSESSED_DIMENSIONS)}")
        for value in scores.values():
            if isinstance(value, bool) or not 0 <= value <= 1:
                raise ValueError("scores must be in 0..1")
        return scores

    @field_validator("requirements")
    @classmethod
    def _requirements(cls, reqs):
        if set(reqs) != set(REQUIREMENTS):
            raise ValueError(f"requirements must be exactly {list(REQUIREMENTS)}")
        for text in reqs.values():
            if len(text) > 3000:
                raise ValueError("requirement text too long")
        return reqs

    @field_validator("questions")
    @classmethod
    def _questions(cls, questions):
        for q in questions:
            if not 10 <= len(q) <= 500:
                raise ValueError("questions must be 10..500 characters")
        return questions


class AssessmentResult(BaseModel):
    assessment: Assessment


def _check_scores(scores: dict[str, float]) -> dict[str, float]:
    if set(scores) != set(ASSESSED_DIMENSIONS):
        raise ValueError(f"scores must be exactly {list(ASSESSED_DIMENSIONS)}")
    for value in scores.values():
        if isinstance(value, bool) or not 0 <= value <= 1:
            raise ValueError("scores must be in 0..1")
    return scores


def _check_texts(items: list[str], low: int, high: int) -> list[str]:
    for text in items:
        if not low <= len(text) <= high:
            raise ValueError(f"list entries must be {low}..{high} characters")
    return items


def _check_url(value: str | None, *, https_only: bool = False) -> str | None:
    if not value:
        return None
    pattern = r"^https://" if https_only else _HTTP
    if not re.match(pattern, value) or len(value) > 2000:
        raise ValueError("links must be https URLs" if https_only
                         else "links must be http(s) URLs")
    return value


class NeedBrief(BaseModel):
    users: str = Field(min_length=10, max_length=3000)
    severity: str = Field(min_length=10, max_length=3000)
    current_practice: str = Field(min_length=10, max_length=4000)
    requirements: list[str] = Field(min_length=1, max_length=10)
    constraints: list[str] = Field(default_factory=list, max_length=10)
    gaps: str = Field(default="", max_length=4000)

    @field_validator("requirements", "constraints")
    @classmethod
    def _items(cls, items):
        return _check_texts(items, 5, 600)


class NeedBriefResult(BaseModel):
    brief: NeedBrief


class Review(BaseModel):
    decision: Literal["build", "continue", "park"]
    scores: dict[str, float]
    fit: str = Field(min_length=10, max_length=4000)
    rationale: str = Field(min_length=20, max_length=4000)
    gaps: list[str] = Field(default_factory=list, max_length=10)
    questions: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("scores")
    @classmethod
    def _scores(cls, scores):
        return _check_scores(scores)

    @field_validator("gaps")
    @classmethod
    def _gaps(cls, items):
        return _check_texts(items, 5, 600)

    @field_validator("questions")
    @classmethod
    def _questions(cls, items):
        return _check_texts(items, 10, 500)


class ReviewResult(BaseModel):
    review: Review


class BomItem(BaseModel):
    part: str = Field(min_length=1, max_length=300)
    quantity: float | str | None = None
    spec: str = Field(default="", max_length=1500)
    unit_cost: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=8)
    source_uri: str | None = None
    alternatives: str = Field(default="", max_length=1500)

    @field_validator("source_uri")
    @classmethod
    def _uri(cls, value):
        return _check_url(value)


class Bom(BaseModel):
    items: list[BomItem] = Field(min_length=1, max_length=200)
    total_cost: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=8)
    cost_basis: str = Field(default="", max_length=2000)


class Repository(BaseModel):
    url: str
    commit: str | None = Field(default=None, max_length=64)
    licence: str = Field(default="unknown", max_length=200)

    @field_validator("url")
    @classmethod
    def _url(cls, value):
        if not _check_url(value, https_only=True):
            raise ValueError("repository url is required")
        return value


DESIGN_KINDS = ("cad", "schematic", "pcb", "firmware", "drawing", "document",
                "data", "other")


class DesignFile(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    kind: str = "other"
    uri: str
    format: str = Field(default="", max_length=40)
    licence: str = Field(default="unknown", max_length=200)

    @field_validator("kind")
    @classmethod
    def _kind(cls, value):
        return value if value in DESIGN_KINDS else "other"

    @field_validator("uri")
    @classmethod
    def _uri(cls, value):
        if not _check_url(value, https_only=True):
            raise ValueError("design file uri is required")
        return value


class Design(BaseModel):
    repositories: list[Repository] = Field(default_factory=list, max_length=10)
    files: list[DesignFile] = Field(default_factory=list, max_length=100)


class Step(BaseModel):
    step: str = Field(min_length=1, max_length=300)
    detail: str = Field(default="", max_length=3000)
    safety: str = Field(default="", max_length=1000)


class Assembly(BaseModel):
    tools: list[str] = Field(default_factory=list, max_length=60)
    skills: list[str] = Field(default_factory=list, max_length=30)
    time_estimate: str = Field(default="", max_length=300)
    steps: list[Step] = Field(min_length=1, max_length=150)


class Criterion(BaseModel):
    criterion: str = Field(min_length=1, max_length=600)
    method: str = Field(default="", max_length=2000)
    target: str = Field(default="", max_length=600)


class FailureMode(BaseModel):
    mode: str = Field(min_length=1, max_length=600)
    effect: str = Field(default="", max_length=1000)
    mitigation: str = Field(default="", max_length=1000)


class Verification(BaseModel):
    acceptance: list[Criterion] = Field(min_length=1, max_length=40)
    safety: list[str] = Field(default_factory=list, max_length=40)
    failure_modes: list[FailureMode] = Field(default_factory=list, max_length=40)


SECTION_MODELS = {"bom": Bom, "design": Design, "assembly": Assembly,
                  "test": Verification}


def validate_build(section: str, output: dict) -> dict:
    """Validate one build pack section's result. Returns the content to
    store: the section body plus the gaps the worker reported (what the
    upstream project does not document)."""
    build = output.get("build")
    if not isinstance(build, dict):
        raise ValueError("a build pack result needs a 'build' object")
    body = SECTION_MODELS[section](**build).model_dump()
    gaps = build.get("gaps") or []
    if not isinstance(gaps, list):
        raise ValueError("gaps must be a list")
    body["gaps"] = _check_texts([str(g) for g in gaps[:20]], 1, 600)
    return body


# ---------------------------------------------------------------------------
# Instructions carried in each job's input
# ---------------------------------------------------------------------------

_COMMON = (
    "Use web search and page extraction. Work only from sources you actually "
    "retrieved; cite their URLs. Never invent projects, sources, measurements "
    "or results. Be concise: prefer a few well-sourced results over many "
    "weak ones."
)


def need_brief_input(category: dict) -> dict[str, Any]:
    return {
        "category": category["name"],
        "layer": category["layer"],
        "scope": category["scope"],
        "instructions": (
            f"Write a need brief for this human need, before any solution is "
            f"chosen: {category['name']} - {category['scope']} Establish, from "
            f"authoritative sources (UN agencies, WHO, World Bank, national "
            f"statistics, peer-reviewed studies, field organisations): who lacks "
            f"this need met and where (users, settings, numbers); how severe and "
            f"persistent the harm is; what people currently do about it and what "
            f"that costs (current_practice); the requirements a good open, "
            f"locally buildable solution must meet, measurable where possible "
            f"(for example 'removes 99.9% of E. coli', 'under US$50 in "
            f"materials'); practical constraints (power, tools, skills, "
            f"materials, climate, connectivity); and where existing solutions "
            f"fall short (gaps). The brief is used to judge candidate projects, "
            f"so make the requirements specific enough to test against. "
            f"{_COMMON}"
        ),
        "output_format": (
            '{"summary": "<one paragraph>", "brief": {"users": "<who and where, '
            'with numbers>", "severity": "<how severe and persistent>", '
            '"current_practice": "<what people do now and its cost>", '
            '"requirements": ["<measurable requirement>"], "constraints": '
            '["<practical constraint>"], "gaps": "<where existing solutions '
            'fall short>"}, "evidence": [{"source_uri": "<url>", "title": "", '
            '"source_type": ""}]}'
        ),
    }


def discovery_input(category: dict, known: list[str],
                    brief: dict | None = None) -> dict[str, Any]:
    against = ("Prefer candidates that can meet the need brief's requirements "
               "within its constraints. " if brief else "")
    return {
        "category": category["name"],
        "layer": category["layer"],
        "scope": category["scope"],
        **({"need_brief": brief} if brief else {}),
        "already_known": known[:100],
        "instructions": (
            f"Find up to 8 existing open-source projects (hardware or software, "
            f"with public designs or code) that address this human need: "
            f"{category['name']} - {category['scope']} {against}Prefer mature, "
            f"documented, low-cost, locally buildable and maintainable work. "
            f"Skip projects listed in already_known. {_COMMON}"
        ),
        "output_format": (
            '{"summary": "<one paragraph>", "candidates": [{"name": "<project '
            'name>", "summary": "<what it is, who it helps, maturity>", '
            '"source_uris": ["<project or documentation URL>", ...]}]}'
        ),
    }


def assessment_input(project: dict) -> dict[str, Any]:
    brief = project.get("brief")
    against = (
        "Judge it against the need brief: which of its requirements the "
        "project meets, partly meets or misses, within its constraints. "
        if brief else ""
    )
    return {
        "project": project["name"],
        "category": project["category"],
        "layer": project["layer"],
        "summary": project["summary"],
        "source_uris": project["source_uris"],
        **({"need_brief": brief} if brief else {}),
        "instructions": (
            f"Assess this open-source project for Applied Commons. {against}"
            "Score each "
            "dimension from 0 to 1 using the evidence you retrieve: "
            "need_severity_reach (how severe the need is and how many people "
            "or ecosystems it reaches), effectiveness (credible improvement "
            "over current practical alternatives), ease (ease of use and of "
            "implementation: build, deploy, operate), cost (1 = very low "
            "lifetime cost including materials, energy, maintenance and "
            "labour), practicality (parts, tools and skills realistically "
            "available locally; repairable; low connectivity needs), evidence "
            "(0 = no support, 0.5 = documented field use, 1 = independently "
            "reproduced results). Then state, briefly, each admission "
            "requirement: need, baseline, improvement, demonstration, "
            "burden_removed, practical_independence; write 'unknown' where "
            "the evidence does not say. Propose up to 3 immediate engineering "
            f"questions whose answers would most improve the project. {_COMMON}"
        ),
        "output_format": (
            '{"summary": "<one paragraph>", "assessment": {"scores": '
            '{"need_severity_reach": 0.0, "effectiveness": 0.0, "ease": 0.0, '
            '"cost": 0.0, "practicality": 0.0, "evidence": 0.0}, '
            '"requirements": {"need": "", "baseline": "", "improvement": "", '
            '"demonstration": "", "burden_removed": "", '
            '"practical_independence": ""}, "rationale": "<why these scores>", '
            '"questions": ["<question>"]}, "evidence": [{"source_uri": "<url>", '
            '"title": "", "source_type": ""}]}'
        ),
    }


def literature_input(question: dict) -> dict[str, Any]:
    return {
        "question": question["question"],
        "project": question["project_name"],
        "project_code": question["project_code"],
        "maslow_level": question["maslow_level"],
        "instructions": (
            "Collect and summarise the best available public evidence on this "
            "engineering question for the named project: designs, test results, "
            "field reports, costs. Record findings with honest confidence. "
            + _COMMON
        ),
        "output_format": (
            '{"summary": "<one paragraph>", "findings": [{"finding": "<claim>", '
            '"confidence": 0.0}], "evidence": [{"source_uri": "<url>", '
            '"title": "", "source_type": ""}]}'
        ),
    }


_SCORES_FORMAT = (
    '{"need_severity_reach": 0.0, "effectiveness": 0.0, "ease": 0.0, '
    '"cost": 0.0, "practicality": 0.0, "evidence": 0.0}'
)


def review_input(project: dict, review_number: int) -> dict[str, Any]:
    """Go/no-go review of an admitted project (policy section 4b).
    ``project`` carries its brief, latest assessment, findings, sources
    and earlier reviews' gaps."""
    last = review_number >= MAX_REVIEWS
    return {
        "project": project["name"],
        "project_code": project["code"],
        "category": project["category"],
        "layer": project["layer"],
        "summary": project["summary"],
        "review_number": review_number,
        "max_reviews": MAX_REVIEWS,
        **({"need_brief": project["brief"]} if project.get("brief") else {}),
        "assessment": project["assessment"],
        "findings": project["findings"],
        "sources": project["sources"],
        "earlier_gaps": project.get("earlier_gaps") or [],
        "instructions": (
            "Decide whether this project deserves a full build pack: a priced "
            "bill of materials, design files (CAD, schematics, PCB, firmware), "
            "assembly steps and a test procedure, so someone can build it "
            "(for a software or data project: what it takes to deploy and run "
            "it). Use the research findings above and check anything material "
            "against the sources. Re-score each dimension from 0 to 1 with "
            "the evidence now available (same meanings as the assessment). "
            "In fit, say how the project meets the need brief's requirements "
            "(met, partly, missed, unknown). Choose decision 'build' when the "
            "case is compelling: a real need, credible effectiveness, and "
            "enough public design material to document a full build; 'park' "
            "when the evidence undermines it or no build is possible from "
            "public material; 'continue' only when up to 3 specific questions "
            "would settle the decision"
            + (" (this is the last review: decide build or park)" if last else "")
            + ". List gaps: what the upstream project does not document. "
            + _COMMON
        ),
        "output_format": (
            '{"summary": "<one paragraph>", "review": {"decision": '
            '"build|continue|park", "scores": ' + _SCORES_FORMAT + ', "fit": '
            '"<requirement by requirement>", "rationale": "<why this '
            'decision>", "gaps": ["<undocumented item>"], "questions": '
            '["<question, only for continue>"]}, "evidence": [{"source_uri": '
            '"<url>", "title": "", "source_type": ""}]}'
        ),
    }


_SECTION_TASKS = {
    "bom": (
        "Produce the complete bill of materials for building one unit: every "
        "part, material and consumable with specification, quantity, unit "
        "cost and currency, a supplier or datasheet link where you found one, "
        "and alternatives that are easier to source locally. Start from the "
        "project's own BOM files where they exist and complete them; state "
        "the cost basis (supplier, region, date). Leave unit_cost null "
        "rather than guess.",
        '{"items": [{"part": "", "quantity": 1, "spec": "", "unit_cost": '
        'null, "currency": "USD", "source_uri": "<url or null>", '
        '"alternatives": ""}], "total_cost": null, "currency": "USD", '
        '"cost_basis": "", "gaps": ["<missing item>"]}',
    ),
    "design": (
        "Index every design file needed to build the project: CAD models, "
        "drawings, schematics, PCB layouts, firmware and data. For each git "
        "repository give its https clone URL, the commit you inspected (the "
        "full hash of the default branch head) and its licence (SPDX id "
        "where possible, 'unknown' if none is stated). For each file give a "
        "direct https download link (for GitHub, a raw or release link "
        "pinned to that commit), its kind and format, and its licence. Do "
        "not download or modify anything; just index it.",
        '{"repositories": [{"url": "https://...", "commit": "<sha>", '
        '"licence": "CERN-OHL-S-2.0"}], "files": [{"name": "", "kind": '
        '"cad|schematic|pcb|firmware|drawing|document|data|other", "uri": '
        '"https://...", "format": "STEP", "licence": ""}], "gaps": '
        '["<missing file>"]}',
    ),
    "assembly": (
        "Write the assembly procedure for one unit as ordered steps, each "
        "with enough detail to follow and any safety precaution; list the "
        "tools and skills needed and a realistic time estimate. Base it on "
        "the project's own instructions and builders' reports; where the "
        "upstream instructions skip a step, fill it only from a cited "
        "source and say so.",
        '{"tools": [""], "skills": [""], "time_estimate": "", "steps": '
        '[{"step": "", "detail": "", "safety": ""}], "gaps": ["<undocumented '
        'step>"]}',
    ),
    "test": (
        "Write how to check a finished unit works and is safe: acceptance "
        "criteria tied to the need brief's requirements (criterion, test "
        "method, target value), safety checks, and known failure modes with "
        "their effect and mitigation (from field reports, issues and "
        "forums). Prefer published test standards where they exist.",
        '{"acceptance": [{"criterion": "", "method": "", "target": ""}], '
        '"safety": [""], "failure_modes": [{"mode": "", "effect": "", '
        '"mitigation": ""}], "gaps": ["<untested item>"]}',
    ),
}


#: Software and data projects stay in the catalogue; their build pack
#: documents a working deployment (maintainer decision 2026-10-07).
_SOFTWARE = (
    "If the project is software or data rather than a physical device, "
    "document a working deployment instead: the bill of materials is the "
    "hardware, hosting and services it needs, with their costs; the design "
    "files are its source repositories, data downloads and configuration; "
    "assembly is installation and deployment; test is verifying a running "
    "instance."
)


def build_input(project: dict, section: str) -> dict[str, Any]:
    """One section of a build pack (policy section 4c)."""
    task, fmt = _SECTION_TASKS[section]
    return {
        "project": project["name"],
        "project_code": project["code"],
        "category": project["category"],
        "section": section,
        "summary": project["summary"],
        **({"need_brief": project["brief"]} if project.get("brief") else {}),
        "fit": project.get("fit") or "",
        "sources": project["sources"],
        "instructions": (
            f"Build pack, section '{section}', for this open-source project. "
            f"{task} {_SOFTWARE} List gaps: what the upstream project does not "
            f"document for this section. {_COMMON}"
        ),
        "output_format": (
            '{"summary": "<one paragraph>", "build": ' + fmt + ', "evidence": '
            '[{"source_uri": "<url>", "title": "", "source_type": ""}]}'
        ),
    }


def review_outcome(layer: int, review: Review, review_number: int,
                   ) -> tuple[str, float, str]:
    """The orchestrator's decision on a review: ``build``, ``continue`` or
    ``park``, the recomputed composite, and the reason. The model's
    decision is necessary but not sufficient for a build: the rubric must
    agree (policy section 4b)."""
    composite = composite_score(layer, review.scores)
    evidence = review.scores["evidence"]
    if review.decision == "park":
        return "park", composite, "review recommends parking"
    strong = composite >= BUILD_THRESHOLD and evidence >= BUILD_MIN_EVIDENCE
    if review.decision == "build" and strong:
        return "build", composite, (
            f"composite {composite:.3f} >= {BUILD_THRESHOLD}; "
            f"evidence {evidence:.2f} >= {BUILD_MIN_EVIDENCE}")
    if review_number < MAX_REVIEWS and review.questions:
        why = ("review asks for more evidence" if review.decision == "continue"
               else f"build recommended but composite {composite:.3f} / "
                    f"evidence {evidence:.2f} below the build bar")
        return "continue", composite, why
    return "park", composite, (
        f"no compelling case after review {review_number}: composite "
        f"{composite:.3f}, evidence {evidence:.2f}")


#: Admission needs a credible case on these; the remaining requirements
#: (demonstration, burden removed, practical independence) may still be
#: unknown — they are what the admitted project's research investigates.
ADMISSION_REQUIRED = ("need", "baseline", "improvement")


def admission_case_made(requirements: dict[str, str]) -> bool:
    return all(
        requirements.get(key, "").strip()
        and requirements[key].strip().lower() != "unknown"
        for key in ADMISSION_REQUIRED
    )
