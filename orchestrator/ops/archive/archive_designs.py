#!/usr/bin/env python3
"""Archive build pack design sources as pinned local copies.

Reads ``GET /archives/pending`` from the Applied Commons API: git
repositories and design files indexed by build pack 'design' sections
(policy section 4c). Each source is copied under
``$AC_EXPORTS/designs/<project code>/`` when its licence permits copying,
and the outcome is recorded with ``POST /archives``:

- git: a shallow fetch of the requested commit (the default branch head
  when none was given), saved as ``git archive`` tar.gz named by commit;
  the licence comes from the repository's own licence file, or else from
  the build pack;
- file: a direct https download (size-capped), named by its sha256 prefix;
  the licence is the one the build pack reported.

Sources without a recognisable open licence are recorded as 'skipped' and
stay links only. Only https sources on public addresses are fetched (no
loopback, private or link-local hosts, including after redirects), so a
link in model output cannot reach services on this host. Standard library
plus git; runs as the orchestrator user (ops/systemd/applied-commons-archive.*).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = os.environ.get("AC_API_URL", "http://127.0.0.1:8100")
EXPORTS = Path(os.environ.get(
    "AC_EXPORTS", "/srv/orchestrator/tenants/applied-commons/exports"))
BATCH = int(os.environ.get("AC_ARCHIVE_BATCH", "10"))
MAX_REPO_BYTES = int(os.environ.get("AC_ARCHIVE_MAX_REPO_BYTES", str(500 * 2**20)))
MAX_FILE_BYTES = int(os.environ.get("AC_ARCHIVE_MAX_FILE_BYTES", str(200 * 2**20)))
MIN_FREE_BYTES = int(os.environ.get("AC_ARCHIVE_MIN_FREE_BYTES", str(5 * 2**30)))
GIT_TIMEOUT = 900
HTTP_TIMEOUT = 60

#: Licence names (as reported) that permit copying.
OPEN_NAME = re.compile(
    r"\b(MIT|Apache|[AL]?GPL|MPL|BSD|0BSD|ISC|CC0|CC[- ]?BY|Creative Commons|"
    r"CERN[- ]?OHL|TAPR|Solderpad|Unlicense|public domain|zlib|EUPL|"
    r"Artistic|BlueOak)", re.I)
#: Licence file texts that permit copying.
OPEN_TEXT = (
    ("MIT", re.compile(r"Permission is hereby granted, free of charge", re.I)),
    ("Apache-2.0", re.compile(r"Apache License", re.I)),
    ("GPL", re.compile(r"GNU (Affero |Lesser )?General Public License", re.I)),
    ("MPL-2.0", re.compile(r"Mozilla Public License", re.I)),
    ("BSD", re.compile(r"Redistribution and use in source and binary forms", re.I)),
    ("CERN-OHL", re.compile(r"CERN Open Hardware Licen[cs]e", re.I)),
    ("TAPR-OHL", re.compile(r"TAPR Open Hardware License", re.I)),
    ("Solderpad", re.compile(r"Solderpad Hardware Licen[cs]e", re.I)),
    ("CC", re.compile(r"Creative Commons", re.I)),
    ("Unlicense", re.compile(r"free and unencumbered software released into the public domain", re.I)),
    ("ISC", re.compile(r"Permission to use, copy, modify, and/or distribute", re.I)),
)
LICENCE_FILE = re.compile(r"^(LICEN[CS]E|COPYING|COPYRIGHT)([.-].*)?$", re.I)


def open_licence(name: str | None) -> bool:
    return bool(name) and bool(OPEN_NAME.search(name)) and not re.search(
        r"\b(proprietary|all rights reserved|unknown|none)\b", name, re.I)


def licence_from_text(text: str) -> str | None:
    for name, pattern in OPEN_TEXT:
        if pattern.search(text):
            return name
    return None


def public_https(url: str) -> str | None:
    """The reason ``url`` may not be fetched, or None when it may."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return "not an https URL"
    if parts.username or parts.password:
        return "credentials in URL"
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443,
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return f"cannot resolve host: {exc}"
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            return f"host resolves to non-public address {address}"
    return None


def git_url(url: str) -> str:
    """A clone URL: GitHub and GitLab page links are reduced to the
    repository."""
    parts = urllib.parse.urlsplit(url)
    if parts.hostname in ("github.com", "www.github.com"):
        segments = [s for s in parts.path.split("/") if s][:2]
        if len(segments) == 2:
            name = segments[1].removesuffix(".git")
            return f"https://github.com/{segments[0]}/{name}.git"
    if parts.hostname == "gitlab.com" and "/-/" in parts.path:
        return f"https://gitlab.com{parts.path.split('/-/')[0]}.git"
    return url


def safe_name(text: str, limit: int = 80) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")[:limit] or "file"


def _git(args: list[str], cwd: Path, timeout: int = GIT_TIMEOUT) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false",
           "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(cwd)}
    safe = ["-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
            "-c", "http.followRedirects=false", "-c", "core.hooksPath=/dev/null",
            "-c", "submodule.recurse=false"]
    done = subprocess.run(["git", *safe, *args], cwd=cwd, env=env, timeout=timeout,
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"git {args[0]}: {done.stderr.strip()[-300:]}")
    return done.stdout


def _tree_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_git(item: dict, dest: Path) -> dict:
    url = git_url(item["source_uri"])
    reason = public_https(url)
    if reason:
        return {"status": "skipped", "note": reason}
    with tempfile.TemporaryDirectory(prefix="ac-archive-") as tmp:
        work = Path(tmp)
        _git(["init", "-q", "repo"], work)
        repo = work / "repo"
        wanted = item["revision"] or "HEAD"
        note = ""
        try:
            _git(["fetch", "-q", "--depth", "1", "--no-tags", url, wanted], repo)
        except RuntimeError:
            if wanted == "HEAD":
                raise
            _git(["fetch", "-q", "--depth", "1", "--no-tags", url, "HEAD"], repo)
            note = f"commit {wanted} not fetchable; archived the default branch head. "
        if _tree_bytes(repo / ".git") > MAX_REPO_BYTES:
            return {"status": "skipped", "note": note + "repository larger than the cap"}
        commit = _git(["rev-parse", "FETCH_HEAD"], repo).strip()
        licence = None
        for name in _git(["ls-tree", "--name-only", "FETCH_HEAD"], repo).splitlines():
            if LICENCE_FILE.match(name):
                text = _git(["show", f"FETCH_HEAD:{name}"], repo)[:20000]
                licence = licence_from_text(text)
                if licence:
                    break
        reported = item.get("licence") or "unknown"
        if licence is None and open_licence(reported):
            licence = reported
            note += "licence as reported by the build pack (no licence file recognised). "
        if licence is None:
            return {"status": "skipped", "resolved_revision": commit, "licence": reported,
                    "note": note + "no open licence found; kept as a link"}
        dest.mkdir(parents=True, exist_ok=True)
        repo_name = safe_name(url.rstrip("/").split("/")[-1].removesuffix(".git"))
        target = dest / f"{repo_name}-{commit[:12]}.tar.gz"
        _git(["archive", "--format=tar.gz", "-o", str(target), "FETCH_HEAD"], repo)
    return {"status": "archived", "resolved_revision": commit, "licence": licence,
            "path": str(target), "bytes": target.stat().st_size,
            "sha256": _sha256(target), "note": note.strip()}


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = public_https(newurl)
        if reason:
            raise urllib.error.URLError(f"redirect refused: {reason}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def archive_file(item: dict, dest: Path) -> dict:
    url = item["source_uri"]
    licence = item.get("licence") or "unknown"
    if not open_licence(licence):
        return {"status": "skipped", "licence": licence,
                "note": "no open licence reported; kept as a link"}
    reason = public_https(url)
    if reason:
        return {"status": "skipped", "note": reason}
    opener = urllib.request.build_opener(_PublicRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": "applied-commons-archiver"})
    dest.mkdir(parents=True, exist_ok=True)
    partial = dest / ".partial"
    digest = hashlib.sha256()
    size = 0
    with opener.open(request, timeout=HTTP_TIMEOUT) as response, partial.open("wb") as out:
        for chunk in iter(lambda: response.read(1 << 20), b""):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                out.close()
                partial.unlink()
                return {"status": "skipped", "licence": licence,
                        "note": "file larger than the cap"}
            digest.update(chunk)
            out.write(chunk)
    sha = digest.hexdigest()
    base = safe_name(Path(urllib.parse.urlsplit(url).path).name)
    target = dest / f"{sha[:12]}-{base}"
    partial.replace(target)
    return {"status": "archived", "licence": licence, "path": str(target),
            "bytes": size, "sha256": sha}


def _api(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def run() -> int:
    pending = _api("GET", f"/archives/pending?limit={BATCH}")
    counts: dict[str, int] = {}
    for item in pending:
        if shutil.disk_usage(EXPORTS).free < MIN_FREE_BYTES:
            print("stopping: less than the minimum free space in", EXPORTS)
            break
        dest = EXPORTS / "designs" / safe_name(item["code"])
        dest = dest / ("repos" if item["kind"] == "git" else "files")
        try:
            outcome = (archive_git if item["kind"] == "git" else archive_file)(item, dest)
        except Exception as exc:  # recorded, not raised: one bad source must not stop the batch
            outcome = {"status": "failed", "note": f"{type(exc).__name__}: {exc}"[:2000]}
        record = {"project_id": item["project_id"], "source_uri": item["source_uri"],
                  "kind": item["kind"], "revision": item["revision"],
                  "licence": item.get("licence") or "unknown", **outcome}
        _api("POST", "/archives", record)
        counts[record["status"]] = counts.get(record["status"], 0) + 1
        print(f"{record['status']}: {item['code']} {item['source_uri']} {record.get('note', '')}")
    print("archive pass:", counts or "nothing pending")
    return 0


if __name__ == "__main__":
    sys.exit(run())
