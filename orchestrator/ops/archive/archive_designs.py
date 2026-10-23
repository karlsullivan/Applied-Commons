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
stay links only. A non-commercial (NC) licence is archived too, as a
preservation copy marked non-commercial wherever it is shown (maintainer
decision 2026-10-23, policy section 4c); no-derivatives and unknown
licences are not. Only https sources on public addresses are fetched (no
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
from datetime import datetime, timezone
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


#: Licences that forbid commercial use or changes are not open (policy
#: section 9: open means modification and commercial use are allowed).
RESTRICTED = re.compile(
    r"\bNC\b|non[- ]?commercial|\bND\b|no[- ]?deriv|proprietary|all rights reserved|"
    r"\bunknown\b|\bnone\b", re.I)


#: Non-commercial terms, and what still rules out a copy alongside them.
NON_COMMERCIAL = re.compile(r"\bNC\b|non[- ]?commercial", re.I)
NOT_COPYABLE = re.compile(
    r"\bND\b|no[- ]?deriv|proprietary|all rights reserved|\bunknown\b|\bnone\b", re.I)
#: Matches NON_COMMERCIAL_WEIGHED in the API: a source skipped for its
#: licence before NC copies were kept is offered once more, until noted.
NON_COMMERCIAL_WEIGHED = "non-commercial weighed"


def _words(name: str) -> str:
    return name.replace("-", " ").replace("_", " ")


def open_licence(name: str | None) -> bool:
    """Copying, changes and commercial use allowed: what AC builds on."""
    if not name or RESTRICTED.search(_words(name)):
        return False
    if re.fullmatch(r"(CC|Creative Commons)( licen[cs]e)?", name.strip(), re.I):
        return False  # which Creative Commons licence is not said
    return bool(OPEN_NAME.search(name))


def non_commercial(name: str | None) -> bool:
    """A named licence that allows copying and changes but not commercial
    use, such as CC BY-NC or BY-NC-SA."""
    if not name or not NON_COMMERCIAL.search(_words(name)) or NOT_COPYABLE.search(_words(name)):
        return False
    return bool(OPEN_NAME.search(name))


def archivable(name: str | None) -> bool:
    """May be kept as a copy: an open licence, or a non-commercial one."""
    return open_licence(name) or non_commercial(name)


def _licence_skip(note: str) -> str:
    return f"{note} ({NON_COMMERCIAL_WEIGHED})"


def licence_from_text(text: str) -> str | None:
    for name, pattern in OPEN_TEXT:
        if pattern.search(text):
            if name == "CC":  # which Creative Commons licence, NC included
                if re.search(r"NoDerivatives|NoDerivs|\bBY[- ](NC[- ])?ND\b", text, re.I):
                    return None
                if re.search(r"NonCommercial|\bBY[- ]NC\b", text, re.I):
                    return ("CC-BY-NC-SA" if re.search(r"ShareAlike|NC[- ]SA\b", text, re.I)
                            else "CC-BY-NC")
                return "CC-BY-SA" if re.search(r"ShareAlike|BY[- ]SA", text, re.I) else (
                    "CC0" if re.search(r"\bCC0\b|Public Domain Dedication", text, re.I) else
                    "CC-BY" if re.search(r"Attribution", text, re.I) else None)
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


#: Matches PROJECT_LICENCE_WEIGHED in the API: once noted, a source the
#: project's licence could not cover is not offered again.
PROJECT_LICENCE_WEIGHED = "project licence weighed"
CODE_HOSTS = ("github.com", "gitlab.com", "codeberg.org", "bitbucket.org", "sourceforge.net")


def origin(url: str) -> tuple[str, str]:
    """Who hosts ``url``: the host, and on a code host the owner account
    (raw.githubusercontent.com is GitHub)."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    if host == "raw.githubusercontent.com":
        host = "github.com"
    segments = [s for s in parts.path.split("/") if s]
    if host in CODE_HOSTS:
        return host, (segments[0].lower() if segments else "")
    return host, ""


def project_licence_for(item: dict) -> str | None:
    """The project's own open licence when the project itself hosts this
    source (same site, or the same owner on a code host); a file merely
    linked from elsewhere is not covered by it."""
    licence = item.get("project_licence")
    if not archivable(licence):
        return None
    mine = {origin(o) for o in item.get("project_origins") or []}
    return licence if origin(item["source_uri"]) in mine else None


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
        if licence is None and archivable(reported):
            licence = reported
            note += "licence as reported by the build pack (no licence file recognised). "
        if licence is None and project_licence_for(item):
            licence = project_licence_for(item)
            note += "licence of the project, which hosts this repository. "
        if licence is None:
            weighed = f" ({PROJECT_LICENCE_WEIGHED})" if item.get("project_licence") else ""
            return {"status": "skipped", "resolved_revision": commit, "licence": reported,
                    "note": _licence_skip(note + "no open or non-commercial licence found; "
                                          "kept as a link" + weighed)}
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


USER_AGENT = "Mozilla/5.0 (compatible; AppliedCommonsArchiver/1.0; open design preservation)"
#: The Internet Archive's latest snapshot of a URL, as originally served.
WAYBACK = "https://web.archive.org/web/2id_/"
#: Refusals worth trying the Wayback Machine for: blocked, gone, throttled.
WAYBACK_CODES = (401, 403, 404, 410, 429, 451)


class _TooLarge(Exception):
    pass


def _download(url: str, partial: Path) -> tuple[int, str, str]:
    """Fetch ``url`` into ``partial``: (bytes, sha256, the URL finally
    served). Raises _TooLarge past the cap."""
    opener = urllib.request.build_opener(_PublicRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    size = 0
    with opener.open(request, timeout=HTTP_TIMEOUT) as response, partial.open("wb") as out:
        for chunk in iter(lambda: response.read(1 << 20), b""):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise _TooLarge
            digest.update(chunk)
            out.write(chunk)
        served = response.geturl()
    return size, digest.hexdigest(), served


def archive_file(item: dict, dest: Path) -> dict:
    url = item["source_uri"]
    licence = item.get("licence") or "unknown"
    note = ""
    if not archivable(licence):
        inherited = project_licence_for(item)
        if inherited is None:
            weighed = f" ({PROJECT_LICENCE_WEIGHED})" if item.get("project_licence") else ""
            return {"status": "skipped", "licence": licence,
                    "note": _licence_skip("no open or non-commercial licence reported; "
                                          "kept as a link" + weighed)}
        licence = inherited
        note = "licence of the project, which hosts this file. "
    reason = public_https(url)
    if reason:
        return {"status": "skipped", "note": reason}
    dest.mkdir(parents=True, exist_ok=True)
    partial = dest / ".partial"
    try:
        try:
            size, sha, _ = _download(url, partial)
        except urllib.error.HTTPError as exc:
            if exc.code not in WAYBACK_CODES:
                raise
            # Blocked to servers, or gone: the Internet Archive's copy, if
            # it has one, is the same file as originally published.
            try:
                size, sha, served = _download(WAYBACK + url, partial)
            except urllib.error.HTTPError:
                raise exc from None
            note += (f"the original refused us (HTTP {exc.code}); copied from the Internet "
                     f"Archive's snapshot {served}. ")
    except _TooLarge:
        partial.unlink(missing_ok=True)
        return {"status": "skipped", "licence": licence, "note": "file larger than the cap"}
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    base = safe_name(Path(urllib.parse.urlsplit(url).path).name)
    target = dest / f"{sha[:12]}-{base}"
    partial.replace(target)
    return {"status": "archived", "licence": licence, "path": str(target),
            "bytes": size, "sha256": sha, "note": note.strip()}


# ---------------------------------------------------------------------------
# Public preservation: Software Heritage for repositories, the Wayback
# Machine for files and pages (GET /preservation/pending, POST /preservation)
# ---------------------------------------------------------------------------

SWH = os.environ.get("AC_SWH_API", "https://archive.softwareheritage.org/api/1")
WAYBACK_AVAILABLE = os.environ.get("AC_WAYBACK_AVAILABLE",
                                   "https://archive.org/wayback/available?url=")
WAYBACK_SAVE = os.environ.get("AC_WAYBACK_SAVE", "https://web.archive.org/save/")
PRESERVE_BATCH = int(os.environ.get("AC_PRESERVE_BATCH", "15"))
#: A Wayback snapshot older than this is saved again.
FRESH_DAYS = 365
#: Stop asking Software Heritage below this many requests left in the hour.
SWH_RESERVE = 20
SAVE_TIMEOUT = 180
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SNAPSHOT_PATH = re.compile(r"/web/(\d{14})/")


class _Throttled(Exception):
    """The service asked us to slow down: stop using it for this pass."""


def _get_json(url: str, method: str = "GET", timeout: int = HTTP_TIMEOUT):
    """(HTTP status, parsed body or None, headers). 429 raises _Throttled."""
    request = urllib.request.Request(url, method=method, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None, response.headers
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise _Throttled(url) from None
        return exc.code, None, exc.headers


def swh_origin(url: str) -> str:
    """The origin URL Software Heritage files a repository under."""
    return git_url(url).rstrip("/").removesuffix(".git")


def _swh_budget(headers) -> None:
    remaining = (headers or {}).get("X-RateLimit-Remaining")
    if remaining is not None and remaining.isdigit() and int(remaining) < SWH_RESERVE:
        raise _Throttled("Software Heritage rate limit")


def preserve_git(item: dict) -> dict:
    """Software Heritage's copy of a repository: the pinned commit when it
    holds it, else its latest snapshot of the origin; otherwise a save is
    requested and checked again later."""
    origin = swh_origin(item["source_uri"])
    revision = (item.get("revision") or "").lower()
    if SHA1.match(revision):
        status, _, headers = _get_json(f"{SWH}/revision/{revision}/")
        _swh_budget(headers)
        if status == 200:
            return {"status": "held", "reference": f"swh:1:rev:{revision};origin={origin}",
                    "note": "the pinned commit is archived"}
    status, visit, headers = _get_json(
        f"{SWH}/origin/{origin}/visit/latest/?require_snapshot=true")
    _swh_budget(headers)
    snapshot = visit.get("snapshot") if status == 200 and visit else None
    latest = ({"reference": f"swh:1:snp:{snapshot};origin={origin}",
               "captured_at": visit.get("date")} if snapshot else {})
    pinned = bool(SHA1.match(revision))
    if snapshot and (not pinned or item.get("last_status") == "requested"):
        note = ("latest snapshot; the pinned commit is not in it" if pinned
                else "latest snapshot of the repository")
        return {"status": "held", **latest, "note": note}
    status, save, headers = _get_json(f"{SWH}/origin/save/git/url/{origin}/", method="POST")
    _swh_budget(headers)
    if status == 200 and save and save.get("save_request_status") != "rejected":
        return {"status": "requested", **latest,
                "note": f"save requested ({save.get('save_task_status') or 'pending'})"}
    reason = (save or {}).get("save_request_status") or f"HTTP {status}"
    return {"status": "failed", **latest, "note": f"Software Heritage save refused: {reason}"}


def _wayback_time(stamp: str) -> datetime | None:
    try:
        return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _wayback_ref(stamp: str, url: str) -> dict:
    when = _wayback_time(stamp)
    return {"reference": f"https://web.archive.org/web/{stamp}/{url}",
            "captured_at": when.isoformat() if when else None}


def preserve_file(item: dict) -> dict:
    """The Wayback Machine's copy of a file or page: a snapshot from the
    last FRESH_DAYS, or else a new one saved now."""
    url = item["source_uri"]
    _, data, _ = _get_json(WAYBACK_AVAILABLE + urllib.parse.quote(url, safe=""))
    closest = ((data or {}).get("archived_snapshots") or {}).get("closest") or {}
    stamp = (closest.get("timestamp") or "") if closest.get("available") else ""
    when = _wayback_time(stamp)
    if when and (datetime.now(timezone.utc) - when).days <= FRESH_DAYS:
        return {"status": "held", **_wayback_ref(stamp, url), "note": "existing snapshot"}
    older = {"status": "held", **_wayback_ref(stamp, url)} if when else None
    request = urllib.request.Request(WAYBACK_SAVE + url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=SAVE_TIMEOUT) as response:
            served = response.geturl() + " " + (response.headers.get("Content-Location") or "")
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise _Throttled(url) from None
        if older:
            return {**older, "note": f"snapshot over a year old; a new save failed (HTTP {exc.code})"}
        return {"status": "failed", "note": f"Wayback save failed: HTTP {exc.code}"}
    found = SNAPSHOT_PATH.search(served)
    if found:
        return {"status": "held", **_wayback_ref(found.group(1), url), "note": "saved now"}
    if older:
        return {**older, "note": "snapshot over a year old; a new save was requested"}
    return {"status": "requested", "note": "Wayback save requested"}


def preserve() -> dict[str, int]:
    """One preservation pass. A throttled service is left for the next
    pass; one failing source does not stop the batch."""
    counts: dict[str, int] = {}
    throttled: set[str] = set()
    for item in _api("GET", f"/preservation/pending?limit={PRESERVE_BATCH}"):
        service = item["service"]
        if service in throttled:
            continue
        try:
            outcome = (preserve_git if service == "software-heritage" else preserve_file)(item)
        except _Throttled as exc:
            print(f"throttled by {service} ({exc}); the rest waits for the next pass")
            throttled.add(service)
            continue
        except Exception as exc:  # recorded, not raised
            outcome = {"status": "failed", "note": f"{type(exc).__name__}: {exc}"[:2000]}
        _api("POST", "/preservation", {"source_uri": item["source_uri"], "service": service,
                                       **outcome})
        counts[outcome["status"]] = counts.get(outcome["status"], 0) + 1
        print(f"{service} {outcome['status']}: {item['source_uri']} "
              f"{outcome.get('reference') or ''} {outcome.get('note', '')}")
    return counts


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
        if record["status"] == "archived" and non_commercial(record["licence"]):
            record["note"] = ("non-commercial licence: a preservation copy, labelled "
                              "non-commercial. " + (record.get("note") or "")).strip()
        _api("POST", "/archives", record)
        counts[record["status"]] = counts.get(record["status"], 0) + 1
        print(f"{record['status']}: {item['code']} {item['source_uri']} {record.get('note', '')}")
    print("archive pass:", counts or "nothing pending")
    if PRESERVE_BATCH > 0:
        print("preservation pass:", preserve() or "nothing pending")
    return 0


if __name__ == "__main__":
    sys.exit(run())
