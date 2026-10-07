#!/usr/bin/env python3
"""Check catalogue projects' sources: do their links answer, what licence
are they under, are they maintained (policy section 4g).

Reads ``GET /checks/pending`` (projects never checked, or not for 14
days) and records each result with ``POST /projects/{id}/check``:

- links: each project URL and the repositories its build pack names,
  fetched with HEAD (GET when HEAD is refused): ok, broken (404 and other
  client errors), blocked (401/403/429: the site refuses automated
  checks, which says nothing about the project), unreachable (server
  errors, timeouts) or skipped (not a public web address);
- licence: from the GitHub or GitLab API for the project's first
  repository (verified), else from the build pack or design archive
  (reported by a model). Classed open, share-alike, restricted
  (non-commercial or no-derivatives), none (a repository without a
  licence: all rights reserved) or unknown. Modules build only on open
  and share-alike projects (policy section 9);
- activity: the repository's last push and whether it is archived.

Only http(s) URLs on public addresses are fetched, including after
redirects, so links in model output cannot reach services on this host.
Nothing fetched is stored but status codes. Standard library only; runs
as the orchestrator user (ops/systemd/applied-commons-checks.*). The
GitHub API allows 60 unauthenticated requests an hour; a batch stops
early when it runs out, and the rest wait for the next run.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

API = os.environ.get("AC_API_URL", "http://127.0.0.1:8100")
BATCH = int(os.environ.get("AC_CHECK_BATCH", "20"))
TIMEOUT = 20
USER_AGENT = "applied-commons-source-check (+https://github.com/karlsullivan/Applied-Commons)"
MAX_LINKS = 20

#: Non-commercial, no-derivatives and source-available terms: not open.
RESTRICTED = re.compile(r"\b(NC|ND)\b|non-?commercial|no-?deriv|polyform|commons clause|"
                        r"\bSSPL\b|\bBUSL\b|business source|elastic licen", re.I)
#: Reciprocal licences: derivatives stay under the same licence.
SHARE_ALIKE = re.compile(r"\b(A?GPL|LGPL|GNU)\b|BY-SA|share-?alike|CERN[- ]?OHL(?![- ]?(v?2(\.0)?[- ]?)?P\b)|"
                         r"reciprocal|\bMPL\b|mozilla|\bEUPL\b|TAPR|\bOSL\b|\bEPL\b|\bCDDL\b", re.I)
#: Permissive licences.
OPEN = re.compile(r"\b(MIT|Apache|BSD|0BSD|ISC|Unlicense|CC0|zlib|BSL-1\.0|Solderpad|PSF|"
                  r"BlueOak|Artistic|PostgreSQL|X11|NCSA)\b|public domain|CERN[- ]?OHL[- ]?(v?2(\.0)?[- ]?)?P\b|"
                  r"\bCC[- ]BY\b(?![- ]?(SA|NC|ND))|creative commons attribution(?! ?-?(share|non|no))", re.I)


def classify(name: str | None) -> str:
    """open, share-alike, restricted or unknown, from a licence name or SPDX id."""
    if not name or name.strip().upper() in ("NOASSERTION", "OTHER", "UNKNOWN", "NONE"):
        return "unknown"
    if RESTRICTED.search(name):
        return "restricted"
    if SHARE_ALIKE.search(name):
        return "share-alike"
    if OPEN.search(name):
        return "open"
    return "unknown"


def repository(url: str) -> tuple[str, str] | None:
    """("github", "owner/repo") or ("gitlab", "group/sub/repo") for a repository URL."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    if host in ("github.com", "www.github.com") and len(segments) >= 2:
        return "github", f"{segments[0]}/{segments[1].removesuffix('.git')}"
    if host == "gitlab.com" and len(segments) >= 2:
        path = parts.path.split("/-/")[0].strip("/").removesuffix(".git")
        return "gitlab", path
    return None


def public_web(url: str) -> str | None:
    """Why ``url`` may not be fetched, or None when it may."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "not a web URL"
    if parts.username or parts.password:
        return "credentials in URL"
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        return f"cannot resolve host: {exc}"
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            return "host resolves to a non-public address"
    return None


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = public_web(newurl)
        if reason:
            raise urllib.error.URLError(f"redirect refused: {reason}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_PublicRedirects())


def fetch(method: str, url: str, headers: dict | None = None) -> tuple[int, dict, bytes, str]:
    """(status, headers, up to 1 MiB of body, final URL); HTTP errors are returned."""
    request = urllib.request.Request(url, method=method,
                                     headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with _OPENER.open(request, timeout=TIMEOUT) as resp:
            body = resp.read(1 << 20) if method == "GET" else b""
            return resp.status, dict(resp.headers), body, resp.geturl()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read(1 << 16), url


def check_link(url: str, get: Callable = fetch, guard: Callable = public_web) -> dict:
    reason = guard(url)
    if reason:
        return {"url": url, "state": "skipped", "note": reason}
    try:
        status, _, _, final = get("HEAD", url)
        if status in (400, 403, 405, 501):  # many sites refuse HEAD only
            status, _, _, final = get("GET", url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"url": url, "state": "unreachable", "note": str(exc)[:300]}
    if status < 400:
        state = "ok"
    elif status in (401, 403, 429):
        state = "blocked"
    elif status >= 500:
        state = "unreachable"
    else:
        state = "broken"
    out = {"url": url, "state": state, "status": status}
    if final and final != url:
        out["final_url"] = final[:2000]
    return out


class RateLimited(RuntimeError):
    pass


def repository_facts(kind: str, path: str, get: Callable = fetch) -> dict | None:
    """Licence, last activity and archived flag from the host's API; None
    when the repository does not exist."""
    if kind == "github":
        headers = {"Accept": "application/vnd.github+json"}
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        status, hdrs, body, _ = get("GET", f"https://api.github.com/repos/{path}", headers)
        remaining = {k.lower(): v for k, v in hdrs.items()}.get("x-ratelimit-remaining")
        if status == 429 or (status == 403 and str(remaining) == "0"):
            raise RateLimited("GitHub API rate limit")
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f"GitHub API HTTP {status}")
        data = json.loads(body)
        lic = data.get("license") or {}
        spdx = lic.get("spdx_id")
        return {"licence": spdx if spdx and spdx != "NOASSERTION" else lic.get("name"),
                "has_licence": bool(lic), "last_activity": data.get("pushed_at"),
                "archived": bool(data.get("archived")), "source": "GitHub"}
    status, _, body, _ = get("GET", "https://gitlab.com/api/v4/projects/"
                             f"{urllib.parse.quote(path, safe='')}?license=true")
    if status == 429:
        raise RateLimited("GitLab API rate limit")
    if status == 404:
        return None
    if status != 200:
        raise RuntimeError(f"GitLab API HTTP {status}")
    data = json.loads(body)
    lic = data.get("license") or {}
    return {"licence": lic.get("name") or lic.get("key"), "has_licence": bool(lic),
            "last_activity": data.get("last_activity_at"),
            "archived": bool(data.get("archived")), "source": "GitLab"}


def check_project(item: dict, get: Callable = fetch, guard: Callable = public_web) -> dict:
    urls = [u for u in item.get("urls") or [] if isinstance(u, str)][:MAX_LINKS]
    result: dict[str, Any] = {"links": [check_link(u, get, guard) for u in urls],
                              "licence": None, "licence_class": "unknown",
                              "licence_source": None, "repository": None,
                              "last_activity": None, "archived": None}
    reported = [r for r in item.get("reported") or [] if r.get("licence")
                and r["licence"].strip().lower() != "unknown"]
    repo = next(((u, r) for u in urls if (r := repository(u))), None)
    if repo:
        url, (kind, path) = repo
        facts = repository_facts(kind, path, get)
        if facts:
            result.update(repository=url, last_activity=facts["last_activity"],
                          archived=facts["archived"])
            if facts["licence"] and classify(facts["licence"]) != "unknown":
                result.update(licence=facts["licence"], licence_class=classify(facts["licence"]),
                              licence_source=f"{facts['source']} (verified)")
                return result
            if not reported:
                if facts["has_licence"]:
                    result.update(licence=(facts["licence"] or "unrecognised")[:200],
                                  licence_source=f"{facts['source']}: licence file not "
                                                 "recognised; check it by hand")
                else:
                    result.update(licence_class="none",
                                  licence_source=f"{facts['source']}: no licence file in the "
                                                 "repository (all rights reserved)")
                return result
    if reported:
        name = reported[0]["licence"]
        result.update(licence=name[:200], licence_class=classify(name),
                      licence_source="reported by the build pack (not verified)")
    return result


def _api(method: str, path: str, body: dict | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def run(api: Callable = _api, get: Callable = fetch, guard: Callable = public_web) -> dict:
    counts: dict[str, int] = {}
    for item in api("GET", f"/checks/pending?limit={BATCH}"):
        try:
            result = check_project(item, get, guard)
        except RateLimited as exc:
            print(f"stopping: {exc}; the rest wait for the next run")
            break
        except (RuntimeError, ValueError, OSError) as exc:  # one bad project must not stop the batch
            print(f"error: {item.get('code')}: {exc}")
            counts["errors"] = counts.get("errors", 0) + 1
            continue
        api("POST", f"/projects/{item['id']}/check", result)
        broken = sum(1 for link in result["links"] if link["state"] in ("broken", "unreachable"))
        counts[result["licence_class"]] = counts.get(result["licence_class"], 0) + 1
        counts["broken_links"] = counts.get("broken_links", 0) + broken
        print(f"{item.get('code')}: {result['licence'] or '-'} ({result['licence_class']}), "
              f"{len(result['links'])} links, {broken} broken")
    print("check pass:", json.dumps(counts) if counts else "nothing due")
    return counts


if __name__ == "__main__":
    run()
    sys.exit(0)
