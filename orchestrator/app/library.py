"""Production library: an outside model of the means of production, staged
beside AC's canonical records (policy section 4l; migration 024).

The first package is Applied Commons Production Library v0.4: equipment,
materials, process routes, designs, enterprises, shared modules, external
dependencies, eight local service systems and ten wider industries, for a
small community farm and workshop that joins a regional network of
specialist producers. Its own contract (IMPORT_CONTRACT_v0.4.md) and AC's
policies agree on what an import may and may not do:

- Idempotent: records are keyed by external_key (origin:collection:id); an
  unchanged payload hash changes nothing; nothing is ever deleted.
- Protective: a record a human reviewed or verified is never overwritten by
  a seed, a record's name (its identity) never changes silently, and the
  canonical graph and catalogue are not written at all. Each refused change
  becomes a task.
- Faithful: requirement expressions (all_of / any_of / ref_id) are checked
  and stored intact; the projection edges are navigation only.
- Honest: located is not validated. Nothing here turns a seed into evidence:
  states stay as the package gives them, a missing value stays unknown,
  and assessments are UNKNOWN until a verified review supplies evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any, Iterable

from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator, model_validator

import research as _research

Json = dict[str, Any]

#: The record ID field of each collection. Staged collections come from the
#: package's import_staging.json; the rest are supplementary files the
#: importer keys itself (ops/library/import_library.py).
ID_FIELDS = {
    "items": "item_id", "capabilities": "capability_id", "resources": "resource_id",
    "designs": "design_id", "sources": "source_id", "enterprises": "enterprise_id",
    "acquisition_routes": "route_id", "production_chains": "chain_id",
    "community_systems": "community_system_id", "industries": "industry_id",
    "material_product_nodes": "flow_id", "process_routes": "recipe_id",
    "shared_modules": "module_id", "external_dependencies": "dependency_id",
    "organisation_models": "organisation_id", "assessment_criteria": "criterion_id",
    # supplementary
    "community_scope": "item_id", "community_core_candidates": "item_id",
    "revenue_links": None, "design_audit_backlog": "audit_id",
    "discovery_queue": "lead_id", "priority_backlog": None,
    "implementation_milestones": "milestone_id", "industry_assessments": "industry_id",
    "coverage_matrix": "capability_id", "gvcs_crosswalk": None,
    "network_scenarios": "scenario_id", "network_examples": "example_id",
    "deployment_profiles": "profile_id", "scope_profile": None,
    "community_requirements_template": "profile_id", "overlap_review": None,
}
#: Requirement expressions, by collection: authoritative AND/OR groups.
EXPRESSIONS = {
    "process_routes": ("input_requirements", "equipment_requirements"),
    "enterprises": ("minimum_candidate_requirements",),
    "acquisition_routes": ("requires",),
}
#: What an expression may refer to.
REFERABLE = ("items", "resources", "material_product_nodes")
#: Community scope classes (provisional, editorial in v0.4).
SCOPES = {
    "LOCAL_CORE_CANDIDATE": "Core candidate",
    "OPTIONAL_LOCAL_OR_SHARED_ENTERPRISE": "Optional enterprise",
    "REGIONAL_SPECIALIST_CANDIDATE": "Regional specialist",
    "EXTERNAL_SUPPLY_OR_LONG_TERM_RESEARCH": "External supply or research",
    "REVIEW_BY_DEMAND_AND_SCALE": "Not yet classified",
}
#: What a design audit must capture before a design counts as reviewed
#: (IMPORT_CONTRACT promotion gates; policy section 4l).
AUDIT_CAPTURE = (
    "Exact revision: a release tag or commit, with its date",
    "Editable source assets (CAD, drawings), not only exports",
    "A complete bill of materials with quantities",
    "The licence of each asset: hardware, documentation and software",
    "Build evidence: a traceable physical build",
    "Test evidence for the intended duty",
)
#: Shared-module assessment dimensions (policy section 4l): never inferred
#: from equipment names or graph connectivity.
MODULE_DIMENSIONS = ("interoperability", "repairability", "independent_reproduction")
#: Package evidence labels and the AC evidence state they amount to.
EDGE_STATES = {"INFERRED": "INFERRED", "UNVERIFIED_SEED": "INFERRED"}
STATES = ("SUPPORTED", "INFERRED", "UNKNOWN", "CONFLICTED")


class PackageError(ValueError):
    """The package cannot be imported as it is; nothing was written."""


def payload_hash(payload: Any) -> str:
    """The package's own hash: SHA-256 of the payload as sorted JSON."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Requirement expressions
# ---------------------------------------------------------------------------


def expression_refs(expr: Any) -> list[str]:
    """Every ref_id in an all_of / any_of expression. Raises ValueError on
    an empty group, an unknown operator or a malformed reference."""
    if not isinstance(expr, dict) or len(expr) != 1:
        raise ValueError(f"not a requirement expression: {str(expr)[:80]}")
    (op, value), = expr.items()
    if op == "ref_id":
        if not isinstance(value, str) or not value:
            raise ValueError("ref_id must be a non-empty string")
        return [value]
    if op not in ("all_of", "any_of"):
        raise ValueError(f"unknown operator {op!r}")
    if not isinstance(value, list) or not value:
        raise ValueError(f"{op} must be a non-empty list")
    return [ref for part in value for ref in expression_refs(part)]


def satisfied(expr: Json, available: set[str]) -> bool:
    """Whether the expression is met by what is available, keeping AND/OR
    meaning: all_of needs every part, any_of needs one. This is topology
    only, never engineering or economic readiness."""
    (op, value), = expr.items()
    if op == "ref_id":
        return value in available
    parts = [satisfied(p, available) for p in value]
    return all(parts) if op == "all_of" else any(parts)


def unresolved(expr: Json, available: set[str]) -> Json | None:
    """The part of the expression still unmet, as an expression (None when
    met): a met alternative removes its whole any_of group."""
    (op, value), = expr.items()
    if op == "ref_id":
        return None if value in available else expr
    if op == "any_of":
        if any(satisfied(p, available) for p in value):
            return None
        return {"any_of": [unresolved(p, available) for p in value]}
    rest = [u for u in (unresolved(p, available) for p in value) if u is not None]
    return {"all_of": rest} if rest else None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _record_id(entity_type: str, key: str) -> str:
    return key.split(":", 2)[2]


def validate(package: Json) -> dict[str, dict[str, Json]]:
    """Check a package before anything is written; returns {collection:
    {record_id: payload}}. Raises PackageError with every problem found."""
    problems: list[str] = []
    by_type: dict[str, dict[str, Json]] = defaultdict(dict)
    origin = package.get("origin") or ""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", origin):
        raise PackageError(f"origin {origin!r} is not a short slug")
    keys = set()
    for node in package.get("nodes") or []:
        key, kind, payload = node.get("external_key"), node.get("entity_type"), node.get("payload")
        if not isinstance(key, str) or key.count(":") < 2:
            problems.append(f"bad external_key {key!r}")
            continue
        if key in keys:
            problems.append(f"{key}: duplicate external_key")
            continue
        keys.add(key)
        k_origin, k_type, record_id = key.split(":", 2)
        if k_origin != origin or k_type != kind:
            problems.append(f"{key}: key does not match origin {origin} and type {kind}")
            continue
        if kind not in ID_FIELDS:
            problems.append(f"{key}: unknown collection {kind}")
            continue
        if not isinstance(payload, dict):
            problems.append(f"{key}: payload is not an object")
            continue
        field = ID_FIELDS[kind]
        if field and payload.get(field) != record_id:
            problems.append(f"{key}: payload {field} is {payload.get(field)!r}")
        if payload_hash(payload) != node.get("payload_sha256"):
            problems.append(f"{key}: payload_sha256 does not match the payload")
        by_type[kind][record_id] = payload
    referable = set().union(*(by_type.get(c, {}).keys() for c in REFERABLE))
    for kind, fields in EXPRESSIONS.items():
        for record_id, payload in by_type.get(kind, {}).items():
            for field in fields:
                if payload.get(field) is None:
                    continue
                try:
                    missing = sorted(set(expression_refs(payload[field])) - referable)
                except ValueError as exc:
                    problems.append(f"{kind}:{record_id}.{field}: {exc}")
                    continue
                if missing:
                    problems.append(f"{kind}:{record_id}.{field}: unknown refs {missing[:5]}")
    for chain_id, chain in by_type.get("production_chains", {}).items():
        for step in [s for s in chain.get("steps") or [] if s.get("requires") is not None]:
            try:
                expression_refs(step.get("requires"))
            except ValueError as exc:
                problems.append(f"production_chains:{chain_id}.{step.get('step_id')}: {exc}")
    items = by_type.get("items", {})
    for item_id, name in (package.get("baseline_ids") or {}).items():
        if item_id not in items:
            problems.append(f"baseline item {item_id} ({name}) is missing")
        elif items[item_id].get("name") != name:
            problems.append(f"baseline item {item_id} renamed to {items[item_id].get('name')!r}")
    if problems:
        raise PackageError(f"{len(problems)} problem(s): " + "; ".join(problems[:20]))
    return dict(by_type)


def name_of(payload: Json, record_id: str) -> str:
    for field in ("name", "title", "gvcs_label", "callout", "workstream", "task", "profile"):
        if isinstance(payload.get(field), str) and payload[field]:
            return payload[field][:300]
    return record_id


# ---------------------------------------------------------------------------
# Derived research: bootstrap cycles, module assessments, tasks, links
# ---------------------------------------------------------------------------


def bootstrap_cycles(by_type: dict[str, dict[str, Json]], limit: int = 50) -> list[list[str]]:
    """Manufacturing cycles: a flow or item that, through the routes that
    make it, requires itself. A cycle in any alternative is reported: it
    is unresolved work, closed only by a named purchased or outsourced
    starting point, never by assumption."""
    makes: dict[str, set[str]] = defaultdict(set)
    for route in by_type.get("process_routes", {}).values():
        needs = set()
        for field in ("input_requirements", "equipment_requirements"):
            if route.get(field):
                needs |= set(expression_refs(route[field]))
        for out in route.get("outputs") or []:
            makes[out.get("flow_id")] |= needs
    for route in by_type.get("acquisition_routes", {}).values():
        if route.get("method") in ("BUILD", "FABRICATE", "RESTORE") and route.get("requires"):
            makes[route.get("item_id")] |= set(expression_refs(route["requires"]))
    found: dict[tuple, list[str]] = {}

    def walk(node: str, path: list[str], on_path: set[str]) -> None:
        if len(found) >= limit:
            return
        for nxt in sorted(makes.get(node, ())):
            if nxt in on_path:
                cycle = path[path.index(nxt):]
                turn = min(range(len(cycle)), key=lambda i: cycle[i])
                canon = tuple(cycle[turn:] + cycle[:turn])
                found.setdefault(canon, list(canon) + [canon[0]])
            elif len(path) < 12:
                walk(nxt, path + [nxt], on_path | {nxt})

    for start in sorted(makes):
        walk(start, [start], {start})
    return list(found.values())


def assess_module(module: Json, reviews: Iterable[Json] = ()) -> dict[str, Json]:
    """Interoperability, repairability and independent reproduction of a
    proposed shared module. UNKNOWN unless a verified review gives sourced
    evidence for that dimension; UNKNOWN is not FAIL."""
    params = module.get("interface_requirements") or []
    unspecified = [p.get("parameter") for p in params
                   if p.get("value") is None or p.get("verification") in (None, "UNSPECIFIED")]
    evidence: dict[str, Json] = {}
    for review in reviews:
        if review.get("status") != "verified":
            continue
        for item in review.get("evidence") or []:
            if item.get("dimension") in MODULE_DIMENSIONS and item.get("source"):
                evidence[item["dimension"]] = item
    out = {}
    for dim in MODULE_DIMENSIONS:
        if dim in evidence:
            e = evidence[dim]
            out[dim] = {"state": "SUPPORTED", "result": e.get("result") or "",
                        "source": e["source"], "reason": e.get("note") or ""}
            continue
        reason = {
            "interoperability": (f"{len(unspecified)} of {len(params)} interface parameters are "
                                 "unspecified and no conformance test has been run"
                                 if params else "No interface is specified"),
            "repairability": "No maintenance or repair record for a built module",
            "independent_reproduction": "No independent build of the module is recorded",
        }[dim]
        out[dim] = {"state": "UNKNOWN", "reason": reason}
    out["unspecified_parameters"] = unspecified
    out["compatibility_status"] = module.get("compatibility_status") or "UNKNOWN"
    return out


def _missing(values: Json) -> list[str]:
    return [k for k, v in values.items() if v is None]


def research_tasks(by_type: dict[str, dict[str, Json]],
                   cycles: list[list[str]]) -> list[Json]:
    """Actionable research tasks from the staged records, each with a
    stable key. Source discovery never closes a task: only evidence does."""
    designs = by_type.get("designs", {})
    sources = by_type.get("sources", {})
    tasks: list[Json] = []

    def add(key, kind, subject, title, priority, **detail):
        tasks.append({"key": key, "kind": kind, "subject": subject, "title": title,
                      "priority": priority, "detail": detail})

    audits = {a.get("design_id"): a for a in by_type.get("design_audit_backlog", {}).values()}
    for design_id, d in sorted(designs.items()):
        audit = audits.get(design_id)
        add(f"design-audit:{design_id}", "design-audit", f"designs:{design_id}",
            f"Audit the design: {d.get('name')}", 1 if audit else 3,
            backlog=audit or None, capture=list(AUDIT_CAPTURE),
            blocking_gaps=d.get("blocking_gaps") or [],
            sources=[sources.get(s, {}).get("url") for s in d.get("source_ids") or []],
            discovery_status=d.get("discovery_status"), openness=d.get("openness"))
        if str(d.get("openness") or "").startswith("RESTRICTED"):
            add(f"licence:{design_id}", "licence", f"designs:{design_id}",
                f"Review the restricted licence of {d.get('name')} asset by asset", 2,
                openness=d.get("openness"), blocking_gaps=d.get("blocking_gaps") or [])
    for cap_id, c in sorted(by_type.get("capabilities", {}).items()):
        add(f"reconcile:{cap_id}", "reconcile", f"capabilities:{cap_id}",
            f"Reconcile proposed capability {cap_id} ({c.get('name')}) with AC's "
            "need and capability records", 2, domain=c.get("domain"),
            mapping_status=c.get("mapping_status"))
    for mod_id, m in sorted(by_type.get("shared_modules", {}).items()):
        a = assess_module(m)
        add(f"interface:{mod_id}", "interface", f"shared_modules:{mod_id}",
            f"Specify and test the interface of {m.get('name')}", 3,
            unspecified=a["unspecified_parameters"],
            validation_needed=m.get("validation_needed") or [])
    for dep_id, d in sorted(by_type.get("external_dependencies", {}).items()):
        missing = [k for k in ("supplier_count", "lead_time_days", "stock_cover_days")
                   if d.get(k) is None]
        missing += [k for k in ("substitutability", "concentration_risk")
                    if d.get(k) in (None, "UNASSESSED", "UNKNOWN")]
        if missing:
            add(f"dependency:{dep_id}", "dependency", f"external_dependencies:{dep_id}",
                f"Gather supply data for {d.get('name')}", 4, missing=missing,
                remaining_limit=d.get("remaining_limit"))
    for ent_id, e in sorted(by_type.get("enterprises", {}).items()):
        add(f"enterprise-evidence:{ent_id}", "enterprise-evidence", f"enterprises:{ent_id}",
            f"Gather market and cost evidence for {e.get('name')}",
            3 if e.get("community_priority") == "EARLY_CANDIDATE_SUBJECT_TO_DEMAND" else 4,
            missing_economics=_missing(e.get("economics") or {}),
            gates={g: e.get(g) for g in ("technical_gate", "market_gate",
                                          "local_requirements_gate")})
    for lead_id, lead in sorted(by_type.get("discovery_queue", {}).items()):
        add(f"discovery:{lead_id}", "discovery", f"discovery_queue:{lead_id}",
            lead.get("task") or f"Discovery lead {lead_id}", 3,
            capability=lead.get("capability_id"), source=lead.get("source_id"))
    for cycle in cycles:
        add("bootstrap-cycle:" + "-".join(cycle[:-1]), "bootstrap-cycle", cycle[0],
            "Resolve a bootstrap cycle: " + " → ".join(cycle), 3, cycle=cycle,
            resolution="Name a purchased or outsourced starting point, or an alternative route")
    unclassified = sorted(i for i, x in by_type.get("items", {}).items()
                          if x.get("community_scope") == "REVIEW_BY_DEMAND_AND_SCALE")
    if unclassified:
        add("scope:unclassified", "scope", "items",
            f"Classify {len(unclassified)} equipment entries by community scope", 4,
            count=len(unclassified))
    return tasks


_STOP = {"and", "the", "of", "for", "with", "into", "basic", "open", "system", "family",
         "hardware", "original", "timber", "house", "project", "source", "design"}


def _words(text: Any) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z0-9]+", str(text or "").lower())
            if len(w) > 3 and w not in _STOP}


def _url_key(url: Any) -> str:
    text = str(url or "").lower().split("#")[0].rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", text).removesuffix(".git")


def _host(url: Any) -> str:
    return _url_key(url).split("/")[0]


#: Code hosts where the host alone says nothing; the owner/repo path must agree.
CODE_HOSTS = {"github.com", "gitlab.com", "codeberg.org"}
#: Proposed capability domains and the AC need or enabler categories they
#: plausibly serve (editorial: candidates for review, never links).
DOMAIN_CATEGORIES = {
    "food": ["Food and nutrition"], "water": ["Water"],
    "sanitation": ["Sanitation and hygiene"], "energy": ["Energy"],
    "shelter": ["Shelter and thermal comfort"], "transport": ["Transport and logistics"],
    "textiles": ["Clothing and bodily protection"],
    "digital": ["Computing and open data", "Communications and information",
                "Instrumentation and control"],
    "knowledge": ["Skills and know-how", "Learning, competence and achievement"],
    "health": ["Physical and mental health", "Sanitation and hygiene"],
    "environment": ["Environment and climate", "Materials and circularity"],
    "workshop": ["Fabrication and tools"], "materials": ["Materials and circularity"],
    "production": ["Fabrication and tools", "Autonomy and productive capability"],
}


def candidate_links(by_type: dict[str, dict[str, Json]], projects: list[Json],
                    capabilities: list[Json], categories: list[Json],
                    graph_sources: list[Json]) -> list[Json]:
    """Possible links to canonical AC records, each with its basis. They
    are candidates for a reviewer; none is a link until confirmed."""
    out: list[Json] = []
    sources = by_type.get("sources", {})
    for design_id, d in by_type.get("designs", {}).items():
        urls = [sources.get(s, {}).get("url") for s in d.get("source_ids") or []]
        keys = {_url_key(u) for u in urls if u}
        hosts = {_host(u) for u in urls if u} - CODE_HOSTS
        words = _words(d.get("name"))
        brand = re.sub(r"[^a-z0-9]", "", "".join(sorted(hosts)))
        for p in projects:
            p_urls = [u for u in [p.get("repository"), *(p.get("source_uris") or [])] if u]
            p_keys = {_url_key(u) for u in p_urls}
            shared = words & _words(p.get("name"))
            p_words = _words(p.get("name"))
            if keys & p_keys:
                basis = "same source URL"
            elif not hosts & {_host(u) for u in p_urls} or not shared:
                continue
            elif {w for w in shared if w not in brand}:
                basis = "same site and a shared name word"
            elif p_words and all(w in brand for w in p_words):
                # The catalogue project is the site's whole family (Precious
                # Plastic, WikiHouse): a family-level match.
                basis = "same site; the catalogue project covers the whole family"
            else:
                continue  # only the site's own name in common (Farm Hack's tools)
            out.append({"record": f"designs:{design_id}", "target_type": "project",
                        "target_id": p["id"], "basis": basis})
    by_name = {c["name"]: c for c in categories}
    for cap_id, c in by_type.get("capabilities", {}).items():
        words = _words(c.get("name"))
        domain = set(DOMAIN_CATEGORIES.get(c.get("domain"), []))
        for g in capabilities:
            shared = words & _words(f"{g.get('name')} {g.get('code')}")
            # A shared word alone is noise ("storage" of food and of water):
            # the AC capability must also serve a category of this domain.
            if shared and domain & set(g.get("categories") or []):
                out.append({"record": f"capabilities:{cap_id}", "target_type": "capability",
                            "target_id": g["id"],
                            "basis": "shared name words: " + ", ".join(sorted(shared))})
        for name in DOMAIN_CATEGORIES.get(c.get("domain"), []):
            if name in by_name:
                out.append({"record": f"capabilities:{cap_id}", "target_type": "category",
                            "target_id": by_name[name]["id"],
                            "basis": f"domain {c.get('domain')} (editorial map)"})
    graph_keys = {_url_key(s.get("uri")): s for s in graph_sources if s.get("uri")}
    for src_id, s in sources.items():
        hit = graph_keys.get(_url_key(s.get("url")))
        if hit:
            out.append({"record": f"sources:{src_id}", "target_type": "graph-source",
                        "target_id": hit["id"], "basis": "same URL"})
    return out


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _canonical(cur) -> tuple[list[Json], list[Json], list[Json], list[Json]]:
    cur.execute(
        """
        SELECT p.id, p.code, p.name, p.source_uris, sc.repository
        FROM projects p
        LEFT JOIN LATERAL (SELECT repository FROM source_checks WHERE project_id = p.id
                           ORDER BY checked_at DESC, id DESC LIMIT 1) sc ON TRUE
        WHERE p.status <> 'merged'
        """)
    projects = cur.fetchall()
    cur.execute(
        """
        SELECT c.id, c.code, c.name,
               array_remove(array_agg(DISTINCT nc.name), NULL) AS categories
        FROM capabilities c
        LEFT JOIN capability_links cl ON cl.capability_id = c.id AND cl.status = 'active'
        LEFT JOIN need_categories nc ON nc.id = cl.category_id
        WHERE c.status <> 'rejected' GROUP BY c.id
        """)
    capabilities = cur.fetchall()
    cur.execute("SELECT id, name FROM need_categories")
    categories = cur.fetchall()
    cur.execute("SELECT id, uri FROM graph_sources")
    return projects, capabilities, categories, cur.fetchall()


def run_import(cur, package: Json, imported_by: str) -> Json:
    """Stage a package. All or nothing: a package that fails validation
    writes nothing; the caller's transaction covers the rest."""
    by_type = validate(package)
    origin = package["origin"]
    nodes = package.get("nodes") or []
    cur.execute(
        "INSERT INTO library_imports (origin, version, package_sha256, imported_by) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (origin, str(package.get("version") or ""), str(package.get("package_sha256") or ""),
         imported_by))
    import_id = cur.fetchone()["id"]
    cur.execute("SELECT id, external_key, name, payload_sha256, review_status "
                "FROM library_records WHERE origin = %s", (origin,))
    existing = {r["external_key"]: r for r in cur.fetchall()}
    report = {"inserted": 0, "updated": 0, "unchanged": 0, "protected": 0,
              "identity_conflicts": 0, "missing": 0, "tasks_opened": 0,
              "links_proposed": 0, "edges": 0}
    task_rows: list[Json] = []
    for node in nodes:
        key, kind, payload = node["external_key"], node["entity_type"], node["payload"]
        record_id = _record_id(kind, key)
        name = name_of(payload, record_id)
        sha = node["payload_sha256"]
        old = existing.pop(key, None)
        if old is None:
            cur.execute(
                "INSERT INTO library_records (external_key, origin, entity_type, record_id, "
                "name, payload, payload_sha256, first_import_id, last_import_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (key, origin, kind, record_id, name, Jsonb(payload), sha, import_id, import_id))
            report["inserted"] += 1
            continue
        if old["payload_sha256"] == sha:
            cur.execute("UPDATE library_records SET last_import_id = %s, in_latest_import = true "
                        "WHERE id = %s", (import_id, old["id"]))
            report["unchanged"] += 1
            continue
        if old["name"] != name:
            report["identity_conflicts"] += 1
            task_rows.append({
                "key": f"conflict:{key}:{sha[:16]}", "kind": "conflict", "subject": key,
                "title": f"{key} was renamed from {old['name']!r} to {name!r}: same record, "
                         "or a new one?", "priority": 2,
                "detail": {"old_name": old["name"], "new_name": name, "new_sha256": sha}})
            cur.execute("UPDATE library_records SET last_import_id = %s, in_latest_import = true "
                        "WHERE id = %s", (import_id, old["id"]))
            continue
        if old["review_status"] in ("reviewed", "verified"):
            report["protected"] += 1
            task_rows.append({
                "key": f"conflict:{key}:{sha[:16]}", "kind": "conflict", "subject": key,
                "title": f"The package changed {key}, which a reviewer has "
                         f"{old['review_status']}: compare before accepting", "priority": 2,
                "detail": {"review_status": old["review_status"], "new_sha256": sha,
                           "new_payload": payload}})
            cur.execute("UPDATE library_records SET last_import_id = %s, in_latest_import = true "
                        "WHERE id = %s", (import_id, old["id"]))
            continue
        cur.execute("INSERT INTO library_record_versions (record_id, payload, payload_sha256, "
                    "import_id) SELECT id, payload, payload_sha256, %s FROM library_records "
                    "WHERE id = %s", (import_id, old["id"]))
        cur.execute("UPDATE library_records SET payload = %s, payload_sha256 = %s, "
                    "last_import_id = %s, in_latest_import = true, updated_at = NOW() "
                    "WHERE id = %s", (Jsonb(payload), sha, import_id, old["id"]))
        report["updated"] += 1
        task_rows.append({
            "key": f"change-review:{key}:{sha[:16]}", "kind": "change-review", "subject": key,
            "title": f"Review the package's change to {key} ({name})", "priority": 4,
            "detail": {"new_sha256": sha, "old_sha256": old["payload_sha256"]}})
    for key, old in existing.items():
        report["missing"] += 1
        cur.execute("UPDATE library_records SET in_latest_import = false WHERE id = %s",
                    (old["id"],))
        task_rows.append({"key": f"missing:{key}", "kind": "missing",
                          "subject": key, "priority": 3,
                          "title": f"{key} ({old['name']}) is not in the latest package; "
                                   "kept, not deleted", "detail": {}})
    report["edges"] = _store_edges(cur, origin, package.get("edges") or [], import_id)
    cycles = bootstrap_cycles(by_type)
    report["bootstrap_cycles"] = len(cycles)
    task_rows += research_tasks(by_type, cycles)
    for t in task_rows:
        cur.execute(
            """
            INSERT INTO library_tasks (key, kind, subject, title, detail, priority, import_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET detail = EXCLUDED.detail, title = EXCLUDED.title
                WHERE library_tasks.status = 'open'
            RETURNING (xmax = 0) AS inserted
            """,
            (t["key"], t["kind"], t["subject"], t["title"][:500], Jsonb(t["detail"]),
             t["priority"], import_id))
        row = cur.fetchone()
        report["tasks_opened"] += bool(row and row["inserted"])
    cur.execute("SELECT id, entity_type, record_id FROM library_records WHERE origin = %s",
                (origin,))
    ids = {f"{r['entity_type']}:{r['record_id']}": r["id"] for r in cur.fetchall()}
    proposed = set()
    for link in candidate_links(by_type, *_canonical(cur)):
        proposed.add((ids[link["record"]], link["target_type"], link["target_id"]))
        cur.execute(
            "INSERT INTO library_links (record_id, target_type, target_id, basis) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id",
            (ids[link["record"]], link["target_type"], link["target_id"], link["basis"]))
        report["links_proposed"] += cur.fetchone() is not None
    # Undecided candidates the matching no longer proposes are withdrawn; a
    # link someone decided on (confirmed or rejected) is kept.
    cur.execute("SELECT l.id, l.record_id, l.target_type, l.target_id FROM library_links l "
                "JOIN library_records r ON r.id = l.record_id WHERE r.origin = %s "
                "AND l.status = 'candidate' AND l.decided_by IS NULL", (origin,))
    stale = [r["id"] for r in cur.fetchall()
             if (r["record_id"], r["target_type"], r["target_id"]) not in proposed]
    if stale:
        cur.execute("DELETE FROM library_links WHERE id = ANY(%s)", (stale,))
    report["links_withdrawn"] = len(stale)
    cur.execute("UPDATE library_imports SET report = %s WHERE id = %s",
                (Jsonb(report), import_id))
    return {"import_id": import_id, **report}


def _store_edges(cur, origin: str, edges: list[Json], import_id: int) -> int:
    """The navigational projection, replaced as a whole on each import:
    edges are derived and carry no review of their own."""
    stored = 0
    for e in edges:
        e = {k.lstrip("﻿"): v for k, v in e.items()}  # v0.4 legacy CSV keys carry a BOM
        if not e.get("from_id") or not e.get("to_id") or not e.get("relation"):
            continue
        original = str(e.get("evidence_state") or e.get("evidence") or "")
        cur.execute(
            """
            INSERT INTO library_edges (origin, kind, from_id, to_id, relation, logic,
                                       group_record, evidence_original, state, source_ref,
                                       import_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (origin, kind, from_id, to_id, relation) DO UPDATE SET
                logic = EXCLUDED.logic, group_record = EXCLUDED.group_record,
                evidence_original = EXCLUDED.evidence_original, state = EXCLUDED.state,
                source_ref = EXCLUDED.source_ref, import_id = EXCLUDED.import_id
            """,
            (origin, e.get("kind") or "projection", e["from_id"], e["to_id"], e["relation"],
             e.get("logic") or "", e.get("requirement_group_record") or "", original,
             EDGE_STATES.get(original, "UNKNOWN"), e.get("source_ref") or "", import_id))
        stored += 1
    cur.execute("DELETE FROM library_edges WHERE origin = %s AND import_id <> %s",
                (origin, import_id))
    return stored


# ---------------------------------------------------------------------------
# Reviews and export
# ---------------------------------------------------------------------------


def review(cur, key: str, status: str, note: str, decided_by: str,
           evidence: list[Json]) -> Json | None:
    """A human decision on a staged record. Verified needs sourced evidence:
    an assumed inventory or a located source is not verification."""
    if status == "verified" and not any(isinstance(e, dict) and e.get("source")
                                        for e in evidence):
        raise ValueError("verified needs at least one evidence item with a source")
    cur.execute("SELECT id FROM library_records WHERE external_key = %s", (key,))
    row = cur.fetchone()
    if row is None:
        return None
    cur.execute("INSERT INTO library_reviews (record_id, status, note, evidence, decided_by) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (row["id"], status, note, Jsonb(evidence), decided_by))
    review_id = cur.fetchone()["id"]
    cur.execute("UPDATE library_records SET review_status = %s, updated_at = NOW() "
                "WHERE id = %s", (status, row["id"]))
    return {"review_id": review_id, "external_key": key, "review_status": status}


def export(cur) -> Json:
    """Everything the public site needs, in one read: records by
    collection (payloads intact), reviews, links with their targets,
    tasks, module assessments and bootstrap cycles."""
    cur.execute("SELECT * FROM library_imports ORDER BY id DESC LIMIT 1")
    latest = cur.fetchone()
    if latest is None:
        return {"imported": False}
    cur.execute("SELECT id, external_key, entity_type, record_id, name, payload, "
                "payload_sha256, review_status, in_latest_import FROM library_records "
                "ORDER BY entity_type, record_id")
    rows = cur.fetchall()
    cur.execute("SELECT r.record_id, r.status, r.note, r.evidence, r.decided_by, r.created_at "
                "FROM library_reviews r ORDER BY r.id")
    reviews = defaultdict(list)
    for r in cur.fetchall():
        reviews[r["record_id"]].append({k: v for k, v in r.items() if k != "record_id"})
    cur.execute(
        """
        SELECT l.id, l.record_id, l.target_type, l.target_id, l.basis, l.status,
               COALESCE(p.code, c.code, s.key, m.code, st.code) AS target_code,
               COALESCE(p.name, c.name, nc.name, s.title, m.name, st.name) AS target_name
        FROM library_links l
        LEFT JOIN projects p ON l.target_type = 'project' AND p.id = l.target_id
        LEFT JOIN capabilities c ON l.target_type = 'capability' AND c.id = l.target_id
        LEFT JOIN need_categories nc ON l.target_type = 'category' AND nc.id = l.target_id
        LEFT JOIN graph_sources s ON l.target_type = 'graph-source' AND s.id = l.target_id
        LEFT JOIN modules m ON l.target_type = 'module' AND m.id = l.target_id
        LEFT JOIN standards st ON l.target_type = 'standard' AND st.id = l.target_id
        WHERE l.status <> 'rejected' ORDER BY l.id
        """)
    links = defaultdict(list)
    for r in cur.fetchall():
        links[r["record_id"]].append({k: v for k, v in r.items() if k != "record_id"})
    cur.execute(
        "SELECT f.record_id, f.attribute, f.value, f.state, f.source_uri, f.locator, "
        "f.excerpt, f.note, r.assessor, r.task_key, r.created_at FROM library_findings f "
        "JOIN library_research r ON r.id = f.research_id ORDER BY f.id")
    findings = defaultdict(list)
    for f in cur.fetchall():
        findings[f["record_id"]].append({k: v for k, v in f.items() if k != "record_id"})
    cur.execute("SELECT task_key, assessor, summary, recommendation, remaining_gaps, job_id, "
                "created_at, (SELECT count(*) FROM library_findings f "
                "WHERE f.research_id = r.id) AS findings FROM library_research r ORDER BY id")
    research = defaultdict(list)
    for r in cur.fetchall():
        research[r["task_key"]].append({k: v for k, v in r.items() if k != "task_key"})
    records: dict[str, list[Json]] = defaultdict(list)
    by_type: dict[str, dict[str, Json]] = defaultdict(dict)
    for r in rows:
        item = {"key": r["external_key"], "id": r["record_id"], "name": r["name"],
                "payload": r["payload"], "review_status": r["review_status"],
                "in_latest_import": r["in_latest_import"],
                "reviews": reviews.get(r["id"], []), "links": links.get(r["id"], []),
                "findings": findings.get(r["id"], [])}
        records[r["entity_type"]].append(item)
        by_type[r["entity_type"]][r["record_id"]] = r["payload"]
    cur.execute("SELECT key, kind, subject, title, detail, priority, status, note, created_at, "
                "resolved_at FROM library_tasks ORDER BY status, priority, key")
    tasks = [{**t, "research": research.get(t["key"], [])} for t in cur.fetchall()]
    assessments = {}
    for item in records.get("shared_modules", []):
        assessments[item["id"]] = assess_module(item["payload"], item["reviews"])
    cur.execute("SELECT kind, count(*) AS n FROM library_edges GROUP BY kind")
    edges = {r["kind"]: r["n"] for r in cur.fetchall()}
    return {"imported": True,
            "import": {k: latest[k] for k in ("id", "origin", "version", "package_sha256",
                                               "imported_by", "created_at", "report")},
            "records": dict(records), "tasks": tasks, "module_assessments": assessments,
            "bootstrap_cycles": bootstrap_cycles(by_type), "edges": edges,
            "scopes": SCOPES, "audit_capture": list(AUDIT_CAPTURE)}


# ---------------------------------------------------------------------------
# AC working across the library (maintainer decision 2026-10-25): the
# catalogue audit and module synthesis see it, the design-audit backlog
# enters the catalogue, and library-research jobs work its open tasks
# ---------------------------------------------------------------------------

RESEARCH = "library-research"
RESEARCH_PRIORITY = 0.5
#: library-research jobs a calendar month (maintainer decision 2026-10-25).
MONTHLY_QUOTA = 10
#: Task kinds a research job can work, in the order they are taken (within
#: a task's priority). Design audits of designs already in the catalogue go
#: through the catalogue pipeline instead; cycles, scope, conflicts and
#: package changes need a person.
RESEARCHABLE = ("design-audit", "licence", "reconcile", "interface", "discovery",
                "enterprise-evidence", "dependency")
RECOMMENDATIONS = ("evidence-sufficient", "more-research", "not-findable")
_KIND_INSTRUCTIONS = {
    "design-audit": (
        "Audit the design: find its exact current revision (release tag or commit, with "
        "date), whether editable source files (CAD, drawings) are published, whether a "
        "complete bill of materials exists, the licence of each asset (hardware, "
        "documentation, software), and any record of a physical build or test. Give each "
        "as a finding with attribute revision, editable_assets, bom, licence_hardware, "
        "licence_documentation, licence_software, build_evidence or test_evidence."),
    "licence": (
        "Establish the licence terms of each asset of the design from its own licence "
        "pages: what may be copied, changed and used commercially, and what may not. "
        "Attribute: licence_hardware, licence_documentation or licence_software."),
    "reconcile": (
        "Compare the proposed capability with Applied Commons' own capabilities and need "
        "categories given in context. Say which existing capability or category it "
        "matches, overlaps or extends, or that it is new. Attribute: matches, overlaps, "
        "extends or new, with the existing code or name as value. This is a proposal for "
        "the maintainer; it is not a link."),
    "interface": (
        "Find published specifications for the shared module's interface parameters "
        "(dimensions, loads, duty, power, protocols, environmental limits) in the module's "
        "sources or comparable open designs, and any record of the same module being used "
        "on two machines, repaired, or built independently. Attribute: the parameter "
        "name, or interoperability, repairability or independent_reproduction."),
    "discovery": (
        "Follow the discovery lead: find current open designs with published plans for "
        "what it asks. Attribute: design, with the name and URL as value."),
    "enterprise-evidence": (
        "Find published evidence for the enterprise in comparable small-scale settings: "
        "market prices, demand, channels, input and labour costs, and capacity. Keep "
        "technical, market and economic evidence apart, and give place, date and currency "
        "for every figure. Attribute: price, demand, channel, cost_<item>, labour, "
        "capacity. A figure from another place or year is context, not this community's."),
    "dependency": (
        "Find supply facts for the external dependency: typical suppliers and their "
        "number, lead times, interchangeability and standard sizes, and how concentrated "
        "supply is. Attribute: supplier_count, lead_time_days, substitutability, "
        "concentration_risk or standard."),
}


class Finding(BaseModel):
    attribute: str = Field(min_length=1, max_length=100)
    value: Any = None
    state: str = "INFERRED"
    source_uri: str | None = Field(default=None, max_length=2000)
    locator: str = Field(default="", max_length=300)
    excerpt: str = Field(default="", max_length=1000)
    note: str = Field(default="", max_length=1000)
    record: str | None = Field(default=None, max_length=300)

    @field_validator("locator", "excerpt", "note", mode="before")
    @classmethod
    def _clip(cls, value, info):
        return str(value or "")[:{"locator": 300, "excerpt": 1000, "note": 1000}[info.field_name]]

    @model_validator(mode="after")
    def _honest(self):
        state = str(self.state or "").upper()
        self.state = state if state in STATES else "INFERRED"
        if self.source_uri and not re.match(r"^https?://\S+$", self.source_uri):
            self.source_uri = None
        if self.state == "SUPPORTED" and not self.source_uri:
            self.state = "INFERRED"  # a claim without a source is not supported
            self.note = (self.note + " (no source given: kept as INFERRED)").strip()
        return self


class ResearchResult(BaseModel):
    summary: str = Field(default="", max_length=4000)
    claims: list[Finding] = Field(default_factory=list)
    remaining_gaps: list[str] = Field(default_factory=list)
    recommendation: str = "more-research"

    @field_validator("claims", mode="before")
    @classmethod
    def _cap(cls, items):
        return list(items or [])[:40]

    @field_validator("remaining_gaps", mode="before")
    @classmethod
    def _gaps(cls, items):
        return [str(i)[:500] for i in list(items or [])[:20]]

    @field_validator("recommendation")
    @classmethod
    def _recommendation(cls, value):
        value = str(value or "").strip().lower()
        return value if value in RECOMMENDATIONS else "more-research"


def _record(cur, external_key: str) -> Json | None:
    cur.execute("SELECT id, external_key, entity_type, record_id, name, payload "
                "FROM library_records WHERE external_key = %s", (external_key,))
    return cur.fetchone()


def _origin(cur) -> str | None:
    cur.execute("SELECT origin FROM library_imports ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    return row["origin"] if row else None


def research_due(cur, budget: int) -> list[Json]:
    """Open library tasks a research job can take this month, best first,
    within the monthly quota; one job per task."""
    cur.execute("SELECT count(*) AS n FROM jobs WHERE job_type = %s "
                "AND created_at >= date_trunc('month', NOW())", (RESEARCH,))
    room = min(budget, MONTHLY_QUOTA - cur.fetchone()["n"])
    if room <= 0:
        return []
    # Taken in turn across kinds, so every kind of research moves each month
    # (32 reconciliations would otherwise take the quota for three months).
    cur.execute(
        """
        SELECT * FROM (
            SELECT t.*, row_number() OVER (PARTITION BY t.kind
                                           ORDER BY t.priority, t.key) AS turn
            FROM library_tasks t
            WHERE t.status = 'open' AND t.kind = ANY(%s)
              AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.unit_key = %s || ':' || t.key)
              -- a design already in the catalogue is audited by the catalogue pipeline
              AND NOT (t.kind = 'design-audit' AND EXISTS (
                  SELECT 1 FROM library_links l JOIN library_records r ON r.id = l.record_id
                  WHERE r.entity_type || ':' || r.record_id = t.subject
                    AND l.target_type = 'project' AND l.status <> 'rejected'))
        ) due
        ORDER BY turn, priority, array_position(%s, kind), key
        LIMIT %s
        """,
        (list(RESEARCHABLE), RESEARCH, list(RESEARCHABLE), room))
    return [{k: v for k, v in r.items() if k != "turn"} for r in cur.fetchall()]


def research_input(cur, task: Json) -> Json:
    """What a research job needs: the task, its record, the records it
    refers to by name, and for reconciliation AC's own capabilities."""
    origin = _origin(cur)
    subject = _record(cur, f"{origin}:{task['subject']}") if task.get("subject") else None
    context: Json = {}
    if subject:
        refs = set()
        for value in subject["payload"].values():
            if isinstance(value, dict) and len(value) == 1 and next(iter(value)) in (
                    "all_of", "any_of", "ref_id"):
                try:
                    refs |= set(expression_refs(value))
                except ValueError:
                    pass
        for field in ("source_ids", "item_ids", "component_flow_ids", "flow_ids",
                      "process_route_ids"):
            refs |= set(subject["payload"].get(field) or [])
        if refs:
            cur.execute("SELECT record_id, entity_type, name, payload->>'url' AS url "
                        "FROM library_records WHERE origin = %s AND record_id = ANY(%s)",
                        (origin, sorted(refs)))
            context["referenced"] = {r["record_id"]: {k: v for k, v in r.items()
                                                      if k != "record_id" and v}
                                     for r in cur.fetchall()}
    if task["kind"] == "reconcile":
        cur.execute(
            """
            SELECT c.code, c.name, c.status,
                   array_remove(array_agg(DISTINCT nc.name), NULL) AS categories
            FROM capabilities c
            LEFT JOIN capability_links cl ON cl.capability_id = c.id AND cl.status = 'active'
            LEFT JOIN need_categories nc ON nc.id = cl.category_id
            WHERE c.status <> 'rejected' GROUP BY c.id ORDER BY c.code
            """)
        context["ac_capabilities"] = cur.fetchall()
        cur.execute("SELECT name, layer, COALESCE(track, 'need') AS track FROM need_categories "
                    "ORDER BY layer NULLS LAST, name")
        context["ac_categories"] = cur.fetchall()
    return {
        "task": {k: task[k] for k in ("key", "kind", "title", "detail")},
        "record": ({"key": subject["external_key"], "name": subject["name"],
                    "payload": subject["payload"]} if subject else None),
        "context": context,
        "instructions": (
            "Applied Commons is mapping the means of production for a small community farm "
            "and workshop in a regional network of specialist producers. Its production "
            "library is an outside planning package: its records are unverified seeds and "
            "proposals. Research the open task below. "
            + _KIND_INSTRUCTIONS.get(task["kind"], "")
            + " Give each fact as a claim with an attribute, a value, an evidence state "
            "(SUPPORTED only with a source URL you retrieved and a locator such as a page or "
            "section; INFERRED for your own reasoning, saying its basis in note; UNKNOWN when "
            "you looked and could not find it; CONFLICTED when sources disagree, one claim "
            "per source), and a short excerpt. Never turn a missing value into zero or a "
            "guess, and never present a located source as a validated design. In record, "
            "give the library key a claim is about when it is not the task's own record. "
            "List what is still missing in remaining_gaps, and recommend evidence-sufficient "
            "(a person can now close the task), more-research or not-findable. "
            + _research._COMMON),
        "output_format": (
            '{"summary": "<one paragraph>", "claims": [{"attribute": "", "value": null, '
            '"state": "SUPPORTED|INFERRED|UNKNOWN|CONFLICTED", "source_uri": "<url>", '
            '"locator": "", "excerpt": "", "note": "", "record": "<library key, optional>"}], '
            '"remaining_gaps": [""], "recommendation": '
            '"evidence-sufficient|more-research|not-findable", "evidence": [{"source_uri": '
            '"<url>", "title": "", "source_type": ""}]}'),
    }


def store_research(cur, task_key: str, result: ResearchResult, assessor: str,
                   job_id: int | None = None) -> Json | None:
    """Record one research run on a task: its summary and sourced findings.
    It verifies nothing and closes nothing."""
    cur.execute("SELECT key, subject FROM library_tasks WHERE key = %s", (task_key,))
    task = cur.fetchone()
    if task is None:
        return None
    origin = _origin(cur)
    cur.execute(
        "INSERT INTO library_research (task_key, job_id, assessor, summary, recommendation, "
        "remaining_gaps) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (task_key, job_id, assessor[:200], result.summary, result.recommendation,
         Jsonb(result.remaining_gaps)))
    research_id = cur.fetchone()["id"]
    stored = 0
    for f in result.claims:
        key = f.record or (f"{origin}:{task['subject']}" if task["subject"] else None)
        record = _record(cur, key) if key and ":" in key else None
        cur.execute(
            "INSERT INTO library_findings (research_id, record_id, attribute, value, state, "
            "source_uri, locator, excerpt, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (research_id, record["id"] if record else None, f.attribute, Jsonb(f.value),
             f.state, f.source_uri, f.locator, f.excerpt, f.note))
        stored += 1
    return {"research_id": research_id, "findings": stored,
            "recommendation": result.recommendation}


def audit_summary(cur) -> Json | None:
    """The library as the monthly catalogue audit sees it: where local
    systems lack core equipment, which proposed capabilities have no
    located design, and what is still open. None before an import."""
    origin = _origin(cur)
    if origin is None:
        return None
    cur.execute("SELECT entity_type, record_id, name, payload FROM library_records "
                "WHERE origin = %s AND entity_type = ANY(%s)",
                (origin, ["community_systems", "community_scope", "coverage_matrix",
                          "external_dependencies"]))
    by: dict[str, list[Json]] = defaultdict(list)
    for r in cur.fetchall():
        by[r["entity_type"]].append(r)
    scope_counts: dict[str, Counter] = defaultdict(Counter)
    for r in by["community_scope"]:
        for sid in r["payload"].get("community_system_ids") or []:
            scope_counts[sid][r["payload"].get("scope")] += 1
    cur.execute("SELECT kind, count(*) AS n FROM library_tasks WHERE status = 'open' "
                "GROUP BY kind")
    return {
        "note": ("An outside planning package: proposals and unverified seeds, not "
                 "evidence. A gap it suggests still needs a reference or a brief."),
        "local_systems": [{"name": r["name"],
                           "service_outcome": r["payload"].get("service_outcome"),
                           "scope_counts": dict(scope_counts[r["record_id"]])}
                          for r in sorted(by["community_systems"], key=lambda r: r["record_id"])],
        "capability_coverage": [{"capability": r["name"],
                                 "coverage_state": r["payload"].get("coverage_state"),
                                 "located_designs": r["payload"].get("located_design_count")}
                                for r in sorted(by["coverage_matrix"], key=lambda r: r["record_id"])],
        "external_dependencies": sorted(r["name"] for r in by["external_dependencies"]),
        "open_tasks": {r["kind"]: r["n"] for r in cur.fetchall()},
    }


def module_proposals(cur, category_name: str, limit: int = 6) -> Json | None:
    """The library's shared-module proposals and external dependencies for
    a module-synthesis domain: proposals to reconcile, never specifications."""
    domains = {d for d, names in DOMAIN_CATEGORIES.items() if category_name in names}
    origin = _origin(cur)
    if not domains or origin is None:
        return None
    cur.execute("SELECT entity_type, record_id, name, payload FROM library_records "
                "WHERE origin = %s AND entity_type = ANY(%s)",
                (origin, ["capabilities", "industries", "shared_modules",
                          "external_dependencies"]))
    by: dict[str, list[Json]] = defaultdict(list)
    for r in cur.fetchall():
        by[r["entity_type"]].append(r)
    caps = {r["record_id"] for r in by["capabilities"] if r["payload"].get("domain") in domains}
    industries = {r["record_id"] for r in by["industries"]
                  if caps & set(r["payload"].get("capability_ids") or [])}
    modules = [{"name": r["name"], "compatibility": r["payload"].get("compatibility_status"),
                "interface_parameters": [p.get("parameter") for p in
                                         r["payload"].get("interface_requirements") or []],
                "validation_needed": r["payload"].get("validation_needed") or []}
               for r in by["shared_modules"]
               if industries & set(r["payload"].get("candidate_industry_ids") or [])][:limit]
    dependencies = [{"name": r["name"], "remaining_limit": r["payload"].get("remaining_limit")}
                    for r in by["external_dependencies"]
                    if industries & set(r["payload"].get("industry_ids") or [])][:limit]
    if not modules and not dependencies:
        return None
    return {"shared_modules": modules, "external_dependencies": dependencies}


def backlog_designs_without_project(cur) -> list[Json]:
    """Design-audit backlog designs not yet linked to a catalogue project,
    with what a suggestion needs (section 4l: the backlog enters the
    catalogue as maintainer priorities)."""
    origin = _origin(cur)
    if origin is None:
        return []
    cur.execute(
        """
        SELECT d.id, d.record_id, d.name, d.payload, b.payload AS audit
        FROM library_records b
        JOIN library_records d ON d.origin = b.origin AND d.entity_type = 'designs'
                              AND d.record_id = b.payload->>'design_id'
        WHERE b.origin = %s AND b.entity_type = 'design_audit_backlog'
          AND d.review_status <> 'rejected'
          AND NOT EXISTS (SELECT 1 FROM library_links l WHERE l.record_id = d.id
                          AND l.target_type = 'project' AND l.status <> 'rejected')
        ORDER BY b.record_id
        """, (origin,))
    rows = cur.fetchall()
    out = []
    for r in rows:
        audit = r["audit"]
        cur.execute("SELECT payload FROM library_records WHERE origin = %s AND "
                    "entity_type = 'sources' AND record_id = ANY(%s)",
                    (origin, list(r["payload"].get("source_ids") or [])))
        urls = [s["payload"].get("url") for s in cur.fetchall() if s["payload"].get("url")]
        cur.execute("SELECT payload FROM library_records WHERE origin = %s AND "
                    "entity_type = 'community_systems' AND record_id = %s",
                    (origin, audit.get("community_system_id")))
        system = cur.fetchone()
        cur.execute("SELECT payload->>'domain' AS domain FROM library_records WHERE origin = %s "
                    "AND entity_type = 'capabilities' AND record_id = ANY(%s)",
                    (origin, list((system or {}).get("payload", {}).get("capability_ids") or [])))
        categories = [c for row in cur.fetchall() for c in DOMAIN_CATEGORIES.get(row["domain"], [])]
        out.append({
            "record_id": r["id"], "design": r["record_id"], "name": r["name"],
            "categories": categories or ["Fabrication and tools"], "source_uris": urls,
            "summary": (f"{r['name']}: a design in the production library's audit backlog "
                        f"({audit.get('record_id') or audit.get('audit_id')}, "
                        f"{audit.get('workstream')}). {audit.get('acceptance_task')}"),
            "note": f"design-audit backlog {audit.get('audit_id')} ({r['record_id']})"})
    return out
