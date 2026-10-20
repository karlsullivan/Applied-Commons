"""Monthly catalogue audit and gap analysis (policy section 4j; maintainer
decision 2026-10-20).

Once a month a catalogue-audit job looks at the whole catalogue (every
need and enabler with its brief and progress, the projects, standards and
modules) against independent reference lists of what people need and what
an open, buildable kit should cover, and lists the gaps: needs or parts of
needs nothing in the catalogue meets, categories with too few or too weak
candidates, missing standards or modules, and quality problems. Each gap
names the reference it comes from, its severity and a suggested action.

The maintainer decides each gap (act or ignore). Acting on a discover gap
queues a candidate-discovery job for its category focused on the gap; other
actions are recorded for the maintainer. The next audit sees every open,
acted and ignored gap, closes the ones the catalogue now covers and does
not raise ignored ones again.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

import research

AUDIT = "catalogue-audit"
AUDIT_PRIORITY = 0.7
DISCOVERY_PRIORITY = 0.7
MIN_ASSESSED = 10
MAX_GAPS = 30

KINDS = ("missing-need", "thin-coverage", "missing-standard", "missing-module",
         "quality", "other")
ACTIONS = ("discover", "new-category", "standard", "module", "review")
SEVERITIES = ("low", "medium", "high")
#: Gap status as stored; decisions use the same words as standards
#: (proposed = open, approved = act on it, rejected = ignore), and a later
#: audit can close a gap the catalogue now covers.
STATUSES = ("proposed", "approved", "rejected", "closed")

#: Independent lists the catalogue is checked against.
REFERENCES = (
    "the Sphere Handbook minimum standards (water, sanitation and hygiene; food security "
    "and nutrition; shelter and settlement; health)",
    "the WHO Model List of Essential Medicines and WHO priority medical devices",
    "Open Source Ecology's Global Village Construction Set (its 50 machines)",
    "Lewis Dartnell's The Knowledge (what a community needs to rebuild essential "
    "technology)",
    "the UN Sustainable Development Goals' basic-needs targets (SDGs 1, 2, 3, 6, 7)",
)


def _choice(value: Any, options: tuple[str, ...], default: str) -> str:
    value = str(value or "").strip().lower().replace(" ", "-")
    return value if value in options else default


class Gap(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    kind: str = "other"
    category: str = Field(default="", max_length=160)
    reference: str = Field(default="", max_length=300)
    detail: str = Field(min_length=10, max_length=1500)
    severity: str = "medium"
    action: str = "review"
    focus: str = Field(default="", max_length=500)

    @field_validator("title", "category", "reference", "detail", "focus", mode="before")
    @classmethod
    def _clip(cls, value, info):
        limit = {"title": 200, "category": 160, "reference": 300, "detail": 1500,
                 "focus": 500}[info.field_name]
        return str(value or "")[:limit]

    @field_validator("kind")
    @classmethod
    def _kind(cls, value):
        return _choice(value, KINDS, "other")

    @field_validator("severity")
    @classmethod
    def _severity(cls, value):
        return _choice(value, SEVERITIES, "medium")

    @field_validator("action")
    @classmethod
    def _action(cls, value):
        return _choice(value, ACTIONS, "review")


class AuditResult(BaseModel):
    gaps: list[Gap] = Field(default_factory=list)
    closed: list[int] = Field(default_factory=list, max_length=200)

    @field_validator("gaps", mode="before")
    @classmethod
    def _cap(cls, items):
        return list(items or [])[:MAX_GAPS]


def audit_input(categories: list[dict], projects: list[dict], standards: list[dict],
                modules: list[dict], gaps: list[dict]) -> dict[str, Any]:
    return {
        "categories": categories,
        "projects": projects,
        "standards": standards,
        "modules": modules,
        "previous_gaps": gaps,
        "references": list(REFERENCES),
        "instructions": (
            "Applied Commons is building a public-good catalogue of open, locally "
            "buildable solutions for every human need and the enablers those needs "
            "depend on, then a modular set built from them. Audit the catalogue "
            "below for gaps. categories gives every need and enabler with its brief's "
            "requirements, its progress (status, and the level each requirement has "
            "reached) and its project counts by stage; projects, standards and "
            "modules list what exists. Compare it with the reference lists in "
            "references and with the briefs themselves, and list up to "
            f"{MAX_GAPS} gaps, most important first: a need or part of a need nothing "
            "in the catalogue meets (missing-need), a category with too few or too "
            "weak candidates for its requirements (thin-coverage), an interface or "
            "module the set lacks (missing-standard, missing-module), or a quality "
            "problem such as unknown licences or unverified claims (quality). For "
            "each gap give: category (an existing category name, or a proposed name "
            "for a new one), reference (which reference item or brief requirement "
            "shows it), detail (what is missing and why it matters, with numbers "
            "where the material has them), severity (high: basic survival or safety "
            "needs; medium; low), action (discover: search for projects in the "
            "category, with focus saying exactly what to look for; new-category; "
            "standard; module; review), and focus. previous_gaps lists earlier gaps "
            "with their id and status: give in closed the ids of open (proposed) or "
            "acted (approved) gaps the catalogue now covers; do not raise again a gap "
            "the maintainer ignored (rejected) unless something has changed, and do "
            "not repeat a gap that is still open. Work from the material below and "
            "from the reference lists you can retrieve; cite sources in evidence. "
            + research._COMMON
        ),
        "output_format": (
            '{"summary": "<one paragraph: how complete the catalogue is and its biggest '
            'gaps>", "gaps": [{"title": "", "kind": "missing-need|thin-coverage|'
            'missing-standard|missing-module|quality|other", "category": "", '
            '"reference": "", "detail": "", "severity": "low|medium|high", "action": '
            '"discover|new-category|standard|module|review", "focus": ""}], '
            '"closed": [<gap id>], "evidence": [{"source_uri": "<url>", "title": "", '
            '"source_type": ""}]}'
        ),
    }


def focused_discovery(payload: dict, gap: dict) -> dict:
    """A category's discovery input, focused on one acted gap."""
    focus = gap.get("focus") or gap.get("detail") or gap.get("title")
    return {**payload, "gap": {"id": gap["id"], "title": gap["title"], "focus": focus},
            "instructions": (f"Focus this search on a gap the maintainer asked to fill: "
                             f"{gap['title']}. {focus} " + payload["instructions"])}
