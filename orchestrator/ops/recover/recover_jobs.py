#!/usr/bin/env python3
"""Recover failed AC jobs from results their workers did return.

Input: a JSON file mapping an AC job id to its candidate results, best
first: {"716": [{"task_id": "t_...", "output": {...}}, ...], ...}. For a
Hermes worker, ``output`` is what the Apollo bridge would have sent
(``ac_bridge.parse_result``, without ``_repaired``).

Each candidate goes to ``POST /jobs/{id}/recover`` in turn until one is
accepted. A refused candidate leaves the job failed as it was, so trying
the next is safe. A job that is no longer failed is skipped.

    python3 -I recover_jobs.py payload.json --by "maintainer (...)" [--api URL]
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request


def post(api: str, path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(f"{api}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except ValueError:
            return exc.code, {"detail": exc.reason}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("payload")
    ap.add_argument("--by", required=True)
    ap.add_argument("--api", default="http://127.0.0.1:8100")
    args = ap.parse_args()
    with open(args.payload, encoding="utf-8") as fh:
        payload = json.load(fh)
    recovered = 0
    for job_id, candidates in payload.items():
        outcome = "no usable result"
        for cand in candidates:
            status, body = post(args.api, f"/jobs/{int(job_id)}/recover", {
                "output": cand["output"], "recovered_by": args.by,
                "source": f"hermes {cand['task_id']}"})
            if status == 200:
                outcome = f"recovered from {cand['task_id']}"
                recovered += 1
                break
            if status in (404, 409):
                outcome = f"skipped: {body.get('detail')}"
                break
            outcome = f"refused ({status}): {str(body.get('detail'))[:200]}"
        print(f"job {job_id}: {outcome}")
    print(f"recovered {recovered} of {len(payload)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
