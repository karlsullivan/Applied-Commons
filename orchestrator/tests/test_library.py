"""The production library's pure logic (app/library.py); no database."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import library as lib  # noqa: E402
from library_fixture import EGG_EQUIPMENT, find, package, ref, rehash  # noqa: E402


@pytest.mark.parametrize("bad", [{"any_of": []}, {"maybe": [ref("H001")]}, {"ref_id": ""},
                                 {"all_of": [ref("A"), "B"]}, {"all_of": [ref("A")], "x": 1}])
def test_malformed_requirement_groups_are_refused(bad):
    with pytest.raises(ValueError):
        lib.expression_refs(bad)


def test_and_or_semantics_and_eggs_without_an_incubator():
    base = {"X201", "X207", "X208", "X212"}
    # A conventional nest is an alternative to roll-away nests.
    assert lib.satisfied(EGG_EQUIPMENT, base | {"RES47"})
    assert lib.satisfied(EGG_EQUIPMENT, base | {"X203"})
    assert not lib.satisfied(EGG_EQUIPMENT, base)
    assert lib.unresolved(EGG_EQUIPMENT, base) == {"all_of": [
        {"any_of": [ref("X203"), ref("RES47")]}]}
    assert "X220" not in lib.expression_refs(EGG_EQUIPMENT)  # no incubator for eggs
    hatchery = {"all_of": [ref("X220"), ref("X221")]}
    assert not lib.satisfied(hatchery, base | {"X221"})
    assert lib.unresolved({"any_of": [ref("A"), ref("B")]}, {"B"}) is None


def test_a_valid_package_passes_and_problems_are_all_reported():
    assert set(lib.validate(package())["items"]) >= {"H001", "X220"}

    def tamper(p):
        find(p, "items", "H001")["payload"]["name"] = "Tape"           # hash now wrong
        route = find(p, "process_routes", "PROC03")
        route["payload"]["equipment_requirements"] = {"any_of": [ref("NOPE")]}
        rehash(route)
        del p["baseline_ids"]["X201"]
        p["baseline_ids"]["Z999"] = "Gone"
    with pytest.raises(lib.PackageError) as err:
        lib.validate(package(t=tamper))
    text = str(err.value)
    assert "payload_sha256 does not match" in text and "unknown refs ['NOPE']" in text
    assert "baseline item Z999 (Gone) is missing" in text


def test_a_renamed_baseline_item_is_refused():
    def rename(p):
        n = find(p, "items", "H001")
        n["payload"]["name"] = "Measuring tape"
        rehash(n)
    with pytest.raises(lib.PackageError, match="baseline item H001 renamed"):
        lib.validate(package(r=rename))


def test_bootstrap_cycles_are_found_not_closed():
    cycles = lib.bootstrap_cycles(lib.validate(package()))
    assert cycles == [["MAT46", "MAT51", "MAT48", "MAT46"]]  # power needs a controller...


def test_module_assessment_stays_unknown_without_verified_evidence():
    module = lib.validate(package())["shared_modules"]["MOD01"]
    a = lib.assess_module(module)
    assert {a[d]["state"] for d in lib.MODULE_DIMENSIONS} == {"UNKNOWN"}
    assert "1 of 1 interface parameters are unspecified" in a["interoperability"]["reason"]
    evidence = [{"dimension": "interoperability", "result": "fits two machines",
                 "source": "https://example.org/test"}]
    assert lib.assess_module(module, [{"status": "reviewed", "evidence": evidence}])[
        "interoperability"]["state"] == "UNKNOWN"
    verified = lib.assess_module(module, [{"status": "verified", "evidence": evidence}])
    assert verified["interoperability"]["state"] == "SUPPORTED"
    assert verified["repairability"]["state"] == "UNKNOWN"


def test_research_tasks():
    by_type = lib.validate(package())
    tasks = {t["key"]: t for t in lib.research_tasks(by_type, lib.bootstrap_cycles(by_type))}
    audit = tasks["design-audit:DES09"]
    assert audit["priority"] == 1 and audit["detail"]["backlog"]["audit_id"] == "AUD01"
    assert any("licence of each asset" in c for c in audit["detail"]["capture"])
    assert tasks["design-audit:DES10"]["priority"] == 3
    assert tasks["licence:DES10"]["kind"] == "licence"
    assert tasks["reconcile:CAP15"]["kind"] == "reconcile"
    assert tasks["interface:MOD01"]["detail"]["unspecified"] == ["Shaft and coupling"]
    assert tasks["enterprise-evidence:ENT01"]["priority"] == 3
    assert "bootstrap-cycle:MAT46-MAT51-MAT48" in tasks
    assert tasks["scope:unclassified"]["detail"]["count"] == 10


def test_candidate_links_need_more_than_a_shared_host():
    by_type = lib.validate(package())
    projects = [{"id": 1, "name": "Example pump", "source_uris": ["https://github.com/example/pump/"]},
                {"id": 2, "name": "Other pump", "source_uris": ["https://github.com/other/pump"]}]
    caps = [{"id": 7, "code": "safe-water-storage", "name": "Safe water storage",
             "categories": ["Water"]},
            {"id": 8, "code": "cold-storage", "name": "Cold storage of water samples",
             "categories": ["Cold chain and storage"]}]  # shares "water", not the domain
    cats = [{"id": 2, "name": "Water"}]
    links = lib.candidate_links(by_type, projects, caps, cats, [])
    assert {(l["record"], l["target_type"], l["target_id"]) for l in links} == {
        ("designs:DES09", "project", 1), ("capabilities:CAP15", "capability", 7),
        ("capabilities:CAP15", "category", 2)}


def test_a_shared_site_name_alone_is_not_a_match():
    by_type = {"sources": {"S1": {"url": "https://farmhack.org/tools/pedal-rootwasher"},
                           "S2": {"url": "https://www.preciousplastic.com/machines/basic"}},
               "designs": {"D1": {"name": "Farm Hack pedal rootwasher", "source_ids": ["S1"]},
                           "D2": {"name": "Precious Plastic basic shredder", "source_ids": ["S2"]}}}
    projects = [
        {"id": 1, "name": "Farm Hack mobile chicken coop",
         "source_uris": ["https://farmhack.org/tools/mobile-chicken-coop"]},
        {"id": 2, "name": "Precious Plastic", "source_uris": ["https://preciousplastic.com/"]},
        {"id": 3, "name": "Farm Hack pedal rootwasher v2",
         "source_uris": ["https://farmhack.org/tools/other"]}]
    links = {(l["record"], l["target_id"]): l["basis"]
             for l in lib.candidate_links(by_type, projects, [], [], [])}
    assert ("designs:D1", 1) not in links  # only "farm hack" in common
    assert links[("designs:D1", 3)] == "same site and a shared name word"
    assert links[("designs:D2", 2)].endswith("covers the whole family")
