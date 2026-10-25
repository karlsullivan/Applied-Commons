#!/usr/bin/env python3
"""Stage a production library package in Applied Commons (policy section
4l; app/library.py).

    import_library.py PACKAGE_DIR [--by NAME] [--dry-run]

Reads an extracted package (first: Applied Commons Production Library
v0.4), checks every file against the package manifest's SHA-256, and posts
to ``POST /library/import``:

- the staged records of ``import_staging.json``, as given (their keys and
  payload hashes are the package's own);
- the supplementary collections the staging file leaves out (community
  scope, core candidates, revenue links, the design-audit backlog,
  discovery leads, assessments, milestones, scenarios and worked
  examples), keyed here the same way: origin:collection:id, with the same
  payload hash;
- the projection and legacy support edges, marked by kind;
- the v0.3 baseline item IDs and names, which the API checks all survive.

The package is data, never code: nothing in it is executed. Re-running
with the same package changes nothing. Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("AC_API_URL", "http://127.0.0.1:8100")

#: Supplementary collections: file stem -> how a record's ID is made.
SUPPLEMENTARY = {
    "community_scope": lambda r: r["item_id"],
    "community_core_candidates": lambda r: r["item_id"],
    "revenue_links": lambda r: f"{r['item_id']}-{r['enterprise_id']}",
    "design_audit_backlog": lambda r: r["audit_id"],
    "discovery_queue": lambda r: r["lead_id"],
    "priority_backlog": lambda r: f"R{int(r['rank']):02d}",
    "implementation_milestones": lambda r: r["milestone_id"],
    "industry_assessments": lambda r: r["industry_id"],
    "coverage_matrix": lambda r: r["capability_id"],
    "gvcs_crosswalk": lambda r: re.sub(r"[^a-z0-9]+", "-", r["gvcs_label"].lower()).strip("-"),
    "network_scenarios": lambda r: r["scenario_id"],
    "network_examples": lambda r: r["example_id"],
    "deployment_profiles": lambda r: r["profile_id"],
}
#: Single-object files.
SINGLE = {"scope_profile": lambda r: "scope_profile",
          "community_requirements_template": lambda r: r["profile_id"]}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_hash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def check_manifest(root: Path) -> dict:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    bad = [name for name, sha in (manifest.get("files") or {}).items()
           if not (root / name).is_file() or sha256_file(root / name) != sha]
    if bad:
        raise SystemExit(f"refused: {len(bad)} file(s) differ from the manifest: {bad[:10]}")
    return manifest


def node(origin: str, collection: str, record_id: str, payload) -> dict:
    return {"external_key": f"{origin}:{collection}:{record_id}", "entity_type": collection,
            "payload_sha256": payload_hash(payload), "payload": payload}


def build(root: Path, by: str) -> dict:
    manifest = check_manifest(root)
    staging = json.loads((root / "import_staging.json").read_text(encoding="utf-8"))
    nodes = list(staging["nodes"])
    origin = nodes[0]["external_key"].split(":", 1)[0]
    for stem, make_id in SUPPLEMENTARY.items():
        for row in json.loads((root / f"{stem}.json").read_text(encoding="utf-8")):
            nodes.append(node(origin, stem, make_id(row), row))
    for stem, make_id in SINGLE.items():
        payload = json.loads((root / f"{stem}.json").read_text(encoding="utf-8"))
        nodes.append(node(origin, stem, make_id(payload), payload))
    with (root / "overlap_review.csv").open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            nodes.append(node(origin, "overlap_review", f"{row['item_a']}-{row['item_b']}", row))
    edges = [{**e, "kind": "projection"} for e in staging.get("production_graph_edges") or []]
    edges += [{**e, "kind": "legacy-candidate-support"}
              for e in staging.get("legacy_candidate_support_edges") or []]
    baseline = json.loads((root / "baseline_v0.3_ids.json").read_text(encoding="utf-8"))
    return {"origin": origin, "version": str(manifest.get("version") or staging.get("version")),
            "package_sha256": sha256_file(root / "import_staging.json"), "imported_by": by,
            "nodes": nodes, "edges": edges, "baseline_ids": baseline}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("package", type=Path)
    parser.add_argument("--by", default="maintainer (production library import)")
    parser.add_argument("--dry-run", action="store_true", help="check and count; post nothing")
    args = parser.parse_args(argv)
    package = build(args.package, args.by)
    counts: dict[str, int] = {}
    for n in package["nodes"]:
        counts[n["entity_type"]] = counts.get(n["entity_type"], 0) + 1
    print(f"package {package['origin']} {package['version']}: {len(package['nodes'])} records, "
          f"{len(package['edges'])} edges, {len(package['baseline_ids'])} baseline items")
    print(json.dumps(counts, sort_keys=True))
    if args.dry_run:
        return 0
    request = urllib.request.Request(
        f"{API}/library/import", data=json.dumps(package).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            print(json.dumps(json.load(response), indent=1))
    except urllib.error.HTTPError as exc:
        print(f"refused: HTTP {exc.code}: {exc.read().decode()[:3000]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
