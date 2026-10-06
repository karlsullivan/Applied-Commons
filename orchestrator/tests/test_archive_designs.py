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
