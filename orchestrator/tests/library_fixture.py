"""A small production library package in the shape of v0.4, for tests:
eggs without an incubator (conventional nests as the alternative to
roll-away nests), a hatchery that needs one, a bootstrap cycle, a
restricted design and a shared module with no specified interface."""

import copy
import hashlib
import json

ORIGIN = "ac-tool-library"


def h(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def node(collection, record_id, payload):
    return {"external_key": f"{ORIGIN}:{collection}:{record_id}", "entity_type": collection,
            "payload_sha256": h(payload), "payload": payload,
            "import_action": "STAGE_FOR_RECONCILIATION"}


def ref(x):
    return {"ref_id": x}


ITEMS = {
    "H001": "Tape measure", "X201": "Mobile poultry house", "X202": "Fixed laying hen house",
    "X203": "Roll-away nest boxes", "X204": "Automatic egg collection", "X207": "Drinker",
    "X208": "Feeder", "X212": "Egg packing table", "X220": "Incubator", "X221": "Brooder",
    "M007": "Metal lathe", "P013": "Charge controller", "X300": "Shared rotary drive",
}
RESOURCES = {"RES47": "Conventional suitable nest boxes", "RES38": "Laying flock"}
FLOWS = {"MAT05": "Formulated feed", "MAT06": "Clean water", "MAT07": "Saleable table eggs",
         "MAT10": "Fertile eggs", "MAT11": "Chicks", "MAT27": "Precision bearings",
         "MAT46": "Electrical power", "MAT48": "Power electronics", "MAT51": "Control board"}

EGG_EQUIPMENT = {"all_of": [{"any_of": [ref("X201"), ref("X202")]}, ref("X207"), ref("X208"),
                            {"any_of": [ref("X203"), ref("RES47")]}, ref("X212")]}


def package(**changes):
    nodes = []
    for i, name in ITEMS.items():
        scope = "LOCAL_CORE_CANDIDATE" if i in ("H001", "X207") else (
            "REGIONAL_SPECIALIST_CANDIDATE" if i == "M007" else "REVIEW_BY_DEMAND_AND_SCALE")
        nodes.append(node("items", i, {"item_id": i, "name": name, "community_scope": scope,
                                       "verification_status": "UNVERIFIED_SEED",
                                       "community_system_ids": ["COM01"]}))
    for i, name in RESOURCES.items():
        nodes.append(node("resources", i, {"resource_id": i, "name": name, "quantity": None}))
    for i, name in FLOWS.items():
        nodes.append(node("material_product_nodes", i, {"flow_id": i, "name": name,
                                                        "quantity": None, "unit": None}))
    routes = [
        {"recipe_id": "PROC02", "name": "Produce and pack table eggs",
         "input_requirements": {"all_of": [ref("MAT05"), ref("MAT06")]},
         "equipment_requirements": EGG_EQUIPMENT, "outputs": [{"flow_id": "MAT07"}]},
        {"recipe_id": "PROC03", "name": "Hatch chicks",
         "input_requirements": {"all_of": [ref("MAT10")]},
         "equipment_requirements": {"all_of": [ref("X220"), ref("X221")]},
         "outputs": [{"flow_id": "MAT11"}]},
        # Power electronics need power; the controller needs electronics; power needs the controller.
        {"recipe_id": "PROC20", "name": "Assemble a control board",
         "input_requirements": {"all_of": [ref("MAT48")]},
         "equipment_requirements": {"all_of": [ref("H001")]}, "outputs": [{"flow_id": "MAT51"}]},
        {"recipe_id": "PROC21", "name": "Generate power",
         "input_requirements": {"all_of": [ref("MAT51")]},
         "equipment_requirements": {"all_of": [ref("P013")]}, "outputs": [{"flow_id": "MAT46"}]},
        {"recipe_id": "PROC22", "name": "Make power electronics",
         "input_requirements": {"all_of": [ref("MAT46")]},
         "equipment_requirements": {"all_of": [ref("M007")]}, "outputs": [{"flow_id": "MAT48"}]},
    ]
    for r in routes:
        r.update(capacity=None, yield_fraction=None, approved_for_build=False,
                 evidence_state="INFERRED")
        nodes.append(node("process_routes", r["recipe_id"], r))
    enterprises = [
        {"enterprise_id": "ENT01", "name": "Table egg sales", "saleable_outputs": ["Table eggs"],
         "minimum_candidate_requirements": {"all_of": [EGG_EQUIPMENT, ref("RES38")]},
         "economics": {"currency": None, "revenue": None, "cash_surplus": None},
         "community_priority": "EARLY_CANDIDATE_SUBJECT_TO_DEMAND", "status":
         "REVENUE_POTENTIAL_NOT_PROVEN", "technical_gate": "PRODUCTION_AND_QUALITY_UNVERIFIED"},
        {"enterprise_id": "ENT02", "name": "Chick and pullet sales", "saleable_outputs": ["Chicks"],
         "minimum_candidate_requirements": {"all_of": [ref("X220"), ref("X221")]},
         "economics": {"currency": None, "revenue": None},
         "community_priority": "CONDITIONAL_ENTERPRISE", "status": "REVENUE_POTENTIAL_NOT_PROVEN"},
    ]
    nodes += [node("enterprises", e["enterprise_id"], e) for e in enterprises]
    nodes.append(node("capabilities", "CAP15", {
        "capability_id": "CAP15", "domain": "water", "name": "Collect and store water",
        "canonical_ac_id": None}))
    nodes.append(node("sources", "SRC10", {
        "source_id": "SRC10", "title": "Pump", "url": "https://github.com/example/pump",
        "pinned_revision": None}))
    nodes.append(node("designs", "DES09", {
        "design_id": "DES09", "name": "Example pump", "item_ids": ["X300"],
        "source_ids": ["SRC10"], "discovery_status": "SOURCE_LOCATED", "openness": "UNASSESSED",
        "approved_for_build": False, "blocking_gaps": ["Pin exact release"]}))
    nodes.append(node("designs", "DES10", {
        "design_id": "DES10", "name": "LowRider CNC", "item_ids": ["M007"], "source_ids": [],
        "discovery_status": "SOURCE_LOCATED", "openness": "RESTRICTED_SOURCE_AVAILABLE",
        "approved_for_build": False}))
    nodes.append(node("shared_modules", "MOD01", {
        "module_id": "MOD01", "name": "Shared rotary drive", "item_id": "X300",
        "interface_requirements": [{"parameter": "Shaft and coupling", "value": None,
                                    "unit": None, "verification": "UNSPECIFIED"}],
        "compatibility_status": "PROPOSED_NOT_INTERCHANGEABILITY_VERIFIED"}))
    nodes.append(node("design_audit_backlog", "AUD01", {
        "audit_id": "AUD01", "design_id": "DES09", "workstream": "Pump",
        "acceptance_task": "Pin the revision", "status": "NOT_STARTED"}))
    pkg = {"origin": ORIGIN, "version": "test", "package_sha256": "0" * 64,
           "imported_by": "test", "nodes": nodes,
           "edges": [{"﻿from_id": "X220", "to_id": "MAT11", "relation": "supports_operation_of",
                      "logic": "CONDITIONAL", "evidence": "UNVERIFIED_SEED",
                      "kind": "legacy-candidate-support"},
                     {"from_id": "PROC02", "to_id": "MAT07", "relation": "candidate_produces",
                      "evidence_state": "INFERRED", "requirement_group_record": "PROC02",
                      "kind": "projection"}],
           "baseline_ids": {"H001": "Tape measure", "X201": "Mobile poultry house"}}
    pkg = copy.deepcopy(pkg)
    for key, edit in changes.items():
        edit(pkg)
    return pkg


def find(pkg, collection, record_id):
    return next(n for n in pkg["nodes"]
                if n["external_key"] == f"{ORIGIN}:{collection}:{record_id}")


def rehash(n):
    n["payload_sha256"] = h(n["payload"])
