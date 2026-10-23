"""The design archiver's pure helpers (ops/archive/archive_designs.py);
no database needed."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "ops" / "archive"))

import archive_designs as ad  # noqa: E402


def test_open_licences():
    for name in ("MIT", "CERN-OHL-S-2.0", "CC-BY-SA-4.0", "GPL-3.0-or-later", "Apache-2.0"):
        assert ad.open_licence(name), name
    for name in ("unknown", "", None, "Proprietary", "All rights reserved"):
        assert not ad.open_licence(name), name


def test_licence_from_text():
    assert ad.licence_from_text(
        "MIT License\n\nPermission is hereby granted, free of charge") == "MIT"
    assert ad.licence_from_text(
        "CERN Open Hardware Licence Version 2 - Strongly Reciprocal") == "CERN-OHL"
    assert ad.licence_from_text("Copyright 2020. All rights reserved.") is None


def test_git_url_reduces_page_links_to_the_repository():
    assert ad.git_url("https://github.com/o/r/tree/main/hardware") == "https://github.com/o/r.git"
    assert ad.git_url("https://github.com/o/r.git") == "https://github.com/o/r.git"
    assert ad.git_url("https://gitlab.com/g/sub/r/-/tree/main") == "https://gitlab.com/g/sub/r.git"
    assert ad.git_url("https://codeberg.org/o/r.git") == "https://codeberg.org/o/r.git"


def test_only_public_https_is_fetched():
    assert ad.public_https("http://example.org/x") == "not an https URL"
    assert "non-public" in ad.public_https("https://127.0.0.1/x")
    assert "non-public" in ad.public_https("https://10.50.0.2/x")
    assert "non-public" in ad.public_https("https://169.254.169.254/latest")
    assert ad.public_https("https://user:pw@example.org/x") == "credentials in URL"


def test_safe_names():
    assert ad.safe_name("../../etc/passwd") == "etc-passwd"
    assert ad.safe_name("Main Board v2.kicad_pcb") == "Main-Board-v2.kicad_pcb"
    assert ad.safe_name("") == "file"


def test_origin_is_the_host_and_owner_on_code_hosts():
    assert ad.origin("https://github.com/Open-Air/firmware/blob/x") == ("github.com", "open-air")
    assert ad.origin("https://raw.githubusercontent.com/open-air/fw/abc/a.pdf") == ("github.com", "open-air")
    assert ad.origin("https://www.example.org/docs/a.pdf") == ("example.org", "")


def test_a_project_licence_covers_only_what_the_project_hosts():
    item = {"source_uri": "https://raw.githubusercontent.com/maker/pump/abc/drawing.pdf",
            "project_licence": "CERN-OHL-S-2.0",
            "project_origins": ["https://github.com/maker/pump", "https://maker.org/pump"]}
    assert ad.project_licence_for(item) == "CERN-OHL-S-2.0"
    assert ad.project_licence_for({**item, "source_uri": "https://maker.org/files/a.stl"}) == "CERN-OHL-S-2.0"
    assert ad.project_licence_for({**item, "source_uri": "https://github.com/someone/else/a.pdf"}) is None
    assert ad.project_licence_for({**item, "project_licence": "unknown"}) is None


def _fake_download(monkeypatch, responses):
    calls = []

    def download(url, partial):
        calls.append(url)
        outcome = responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        partial.write_bytes(b"design")
        return 6, "f" * 64, outcome
    monkeypatch.setattr(ad, "_download", download)
    monkeypatch.setattr(ad, "public_https", lambda url: None)
    return calls


def test_a_file_without_its_own_licence_takes_the_project_licence(tmp_path, monkeypatch):
    _fake_download(monkeypatch, ["https://maker.org/a.pdf"])
    out = ad.archive_file({"source_uri": "https://maker.org/a.pdf", "licence": "unknown",
                           "project_licence": "CC-BY-SA-4.0",
                           "project_origins": ["https://maker.org/"]}, tmp_path)
    assert out["status"] == "archived" and out["licence"] == "CC-BY-SA-4.0"
    assert "licence of the project" in out["note"]


def test_a_source_the_project_licence_cannot_cover_is_settled(tmp_path, monkeypatch):
    _fake_download(monkeypatch, [])
    out = ad.archive_file({"source_uri": "https://elsewhere.org/a.pdf", "licence": "unknown",
                           "project_licence": "MIT", "project_origins": ["https://maker.org/"]},
                          tmp_path)
    assert out["status"] == "skipped" and ad.PROJECT_LICENCE_WEIGHED in out["note"]


def test_a_refused_file_is_copied_from_the_wayback_machine(tmp_path, monkeypatch):
    import urllib.error
    refused = urllib.error.HTTPError("https://wiki.example/a", 403, "Forbidden", {}, None)
    calls = _fake_download(monkeypatch, [refused, "https://web.archive.org/web/2026id_/https://wiki.example/a"])
    out = ad.archive_file({"source_uri": "https://wiki.example/a", "licence": "CC-BY-SA"}, tmp_path)
    assert calls == ["https://wiki.example/a", ad.WAYBACK + "https://wiki.example/a"]
    assert out["status"] == "archived" and "HTTP 403" in out["note"] and "web.archive.org" in out["note"]
    assert not (tmp_path / ".partial").exists()


def test_a_server_error_is_not_sent_to_the_wayback_machine(tmp_path, monkeypatch):
    import urllib.error
    import pytest
    broken = urllib.error.HTTPError("https://x.example/a", 500, "Error", {}, None)
    calls = _fake_download(monkeypatch, [broken])
    with pytest.raises(urllib.error.HTTPError):
        ad.archive_file({"source_uri": "https://x.example/a", "licence": "MIT"}, tmp_path)
    assert calls == ["https://x.example/a"]


# ---------------------------------------------------------------------------
# Public preservation (Software Heritage, the Wayback Machine)
# ---------------------------------------------------------------------------

SNP = "e828f259885e241176ef813d8cc34b8c0094fcfc"
REV = "a" * 40


def swh(monkeypatch, answers, remaining="600"):
    """Answer _get_json from {url fragment: (status, body)}; record calls."""
    calls = []

    def get_json(url, method="GET", timeout=None):
        calls.append((method, url))
        for fragment, (status, body) in answers.items():
            if fragment in url:
                return status, body, {"X-RateLimit-Remaining": remaining}
        return 404, None, {"X-RateLimit-Remaining": remaining}
    monkeypatch.setattr(ad, "_get_json", get_json)
    return calls


def test_swh_origin_is_the_repository_without_dot_git():
    assert ad.swh_origin("https://github.com/o/r/tree/main/cad") == "https://github.com/o/r"
    assert ad.swh_origin("https://codeberg.org/o/r.git/") == "https://codeberg.org/o/r"


def test_a_pinned_commit_held_by_software_heritage_is_cited_by_swhid(monkeypatch):
    swh(monkeypatch, {f"/revision/{REV}/": (200, {"id": REV})})
    out = ad.preserve_git({"source_uri": "https://github.com/o/r", "revision": REV})
    assert out["status"] == "held"
    assert out["reference"] == f"swh:1:rev:{REV};origin=https://github.com/o/r"


def test_a_repository_held_by_software_heritage_cites_its_latest_snapshot(monkeypatch):
    calls = swh(monkeypatch, {"/visit/latest/": (200, {"snapshot": SNP, "date": "2026-06-08T04:35:40+00:00"})})
    out = ad.preserve_git({"source_uri": "https://github.com/o/r.git", "revision": "main"})
    assert out == {"status": "held", "reference": f"swh:1:snp:{SNP};origin=https://github.com/o/r",
                   "captured_at": "2026-06-08T04:35:40+00:00",
                   "note": "latest snapshot of the repository"}
    assert not any(method == "POST" for method, _ in calls)


def test_an_unknown_repository_gets_a_save_request(monkeypatch):
    calls = swh(monkeypatch, {"/origin/save/git/url/": (200, {"save_request_status": "accepted",
                                                              "save_task_status": "pending"})})
    out = ad.preserve_git({"source_uri": "https://github.com/o/r", "revision": ""})
    assert out == {"status": "requested", "note": "save requested (pending)"}
    assert ("POST", f"{ad.SWH}/origin/save/git/url/https://github.com/o/r/") in calls
    swh(monkeypatch, {"/origin/save/git/url/": (200, {"save_request_status": "rejected"})})
    out = ad.preserve_git({"source_uri": "https://example.org/r.git"})
    assert out["status"] == "failed" and "rejected" in out["note"]


def test_a_missing_pinned_commit_is_requested_once_then_the_snapshot_is_held(monkeypatch):
    answers = {"/visit/latest/": (200, {"snapshot": SNP, "date": "2026-01-01T00:00:00+00:00"}),
               "/origin/save/git/url/": (200, {"save_request_status": "accepted"})}
    swh(monkeypatch, answers)
    item = {"source_uri": "https://github.com/o/r", "revision": REV}
    first = ad.preserve_git(item)
    assert first["status"] == "requested" and first["reference"].startswith("swh:1:snp:")
    again = ad.preserve_git({**item, "last_status": "requested"})
    assert again["status"] == "held" and "not in it" in again["note"]


def test_software_heritage_is_left_alone_near_its_rate_limit(monkeypatch):
    swh(monkeypatch, {}, remaining="5")
    try:
        ad.preserve_git({"source_uri": "https://github.com/o/r"})
    except ad._Throttled:
        pass
    else:
        raise AssertionError("expected _Throttled")


class _Saved:
    def __init__(self, url, location=""):
        self.url, self.headers = url, {"Content-Location": location}

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def wayback(monkeypatch, stamp, save):
    closest = {"available": True, "timestamp": stamp} if stamp else {}
    swh(monkeypatch, {"wayback/available": (200, {"archived_snapshots": {"closest": closest}})})
    saved = []

    def urlopen(request, timeout=None):
        saved.append(request.full_url)
        if isinstance(save, Exception):
            raise save
        return save
    monkeypatch.setattr(ad.urllib.request, "urlopen", urlopen)
    return saved


def test_a_recent_wayback_snapshot_is_held_without_saving(monkeypatch):
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    saved = wayback(monkeypatch, stamp, None)
    out = ad.preserve_file({"source_uri": "https://example.org/a.pdf"})
    assert out["status"] == "held" and saved == []
    assert out["reference"] == f"https://web.archive.org/web/{stamp}/https://example.org/a.pdf"


def test_an_old_or_missing_snapshot_is_saved_now(monkeypatch):
    url = "https://example.org/a.pdf"
    saved = wayback(monkeypatch, "20190101000000",
                    _Saved(f"https://web.archive.org/web/20261023031500/{url}"))
    out = ad.preserve_file({"source_uri": url})
    assert saved == [ad.WAYBACK_SAVE + url]
    assert out["status"] == "held" and out["note"] == "saved now"
    assert out["reference"].endswith(f"/web/20261023031500/{url}")
    assert out["captured_at"].startswith("2026-10-23T03:15:00")
    # The save fails: the old snapshot is still a copy.
    import urllib.error
    wayback(monkeypatch, "20190101000000", urllib.error.HTTPError(url, 520, "x", {}, None))
    out = ad.preserve_file({"source_uri": url})
    assert out["status"] == "held" and "/web/20190101000000/" in out["reference"]
    wayback(monkeypatch, "", urllib.error.HTTPError(url, 520, "x", {}, None))
    assert ad.preserve_file({"source_uri": url})["status"] == "failed"


def test_a_throttled_service_waits_while_the_other_carries_on(monkeypatch):
    pending = [{"source_uri": "https://github.com/o/a", "service": "software-heritage"},
               {"source_uri": "https://github.com/o/b", "service": "software-heritage"},
               {"source_uri": "https://example.org/c.pdf", "service": "wayback"}]
    posted = []

    def api(method, path, body=None):
        if method == "GET":
            return pending
        posted.append(body)
        return {}
    monkeypatch.setattr(ad, "_api", api)

    def throttled(item):
        raise ad._Throttled("limit")
    monkeypatch.setattr(ad, "preserve_git", throttled)
    monkeypatch.setattr(ad, "preserve_file", lambda item: {"status": "held", "reference": "r"})
    assert ad.preserve() == {"held": 1}
    assert [p["source_uri"] for p in posted] == ["https://example.org/c.pdf"]


def test_non_commercial_and_no_derivatives_licences_are_not_open():
    for name in ("CC-BY-NC", "CC-BY-NC-SA-4.0", "CC BY-ND 4.0", "CC", "Creative Commons",
                 "Creative Commons Attribution-NonCommercial 4.0"):
        assert not ad.open_licence(name), name
    for name in ("CC-BY-4.0", "CC-BY-SA", "CC0-1.0", "MIT AND GPL-3.0-only",
                 "public domain (RWSN specification)"):
        assert ad.open_licence(name), name
    assert ad.licence_from_text(
        "Creative Commons Attribution-NonCommercial-ShareAlike 4.0") is None
    assert ad.licence_from_text("Creative Commons Attribution-ShareAlike 4.0") == "CC-BY-SA"
    assert ad.licence_from_text("Creative Commons Attribution 4.0 International") == "CC-BY"
