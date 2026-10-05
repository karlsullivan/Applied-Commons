"""Applied Commons autonomous research pipeline: job specifications,
result validation, rubric scoring and admission.

The policy this implements is orchestrator/policies/project-selection.md
(rubric v1, section 4a). Applied Commons owns what each job asks for and
how results are judged; executors (Apollo/Hermes, the NUC worker) only
run the instructions carried in each job's input.
"""

from __future__ import annotations

import os
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

RUBRIC_VERSION = "rubric-v1"
POLICY_REVISION = "project-selection 2026-10-05 (autonomous admission, rubric v1)"
DECISION_AUTHOR = "applied-commons orchestrator, rubric v1"

#: Pipeline job types (all executed by Apollo through Hermes).
DISCOVERY = "candidate-discovery"
ASSESSMENT = "candidate-assessment"
LITERATURE = "literature-collection"

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


# ---------------------------------------------------------------------------
# Instructions carried in each job's input
# ---------------------------------------------------------------------------

_COMMON = (
    "Use web search and page extraction. Work only from sources you actually "
    "retrieved; cite their URLs. Never invent projects, sources, measurements "
    "or results. Be brief: finish within eight minutes."
)


def discovery_input(category: dict, known: list[str]) -> dict[str, Any]:
    return {
        "category": category["name"],
        "layer": category["layer"],
        "scope": category["scope"],
        "already_known": known[:100],
        "instructions": (
            f"Find up to 8 existing open-source projects (hardware or software, "
            f"with public designs or code) that address this human need: "
            f"{category['name']} - {category['scope']} Prefer mature, "
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
    return {
        "project": project["name"],
        "category": project["category"],
        "layer": project["layer"],
        "summary": project["summary"],
        "source_uris": project["source_uris"],
        "instructions": (
            "Assess this open-source project for Applied Commons. Score each "
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
