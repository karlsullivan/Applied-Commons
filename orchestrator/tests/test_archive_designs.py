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
