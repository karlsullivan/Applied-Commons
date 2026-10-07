"""The source checker (ops/checks/check_sources.py) against fake HTTP;
no network or database needed."""

import json
import pathlib
import sys
import urllib.error

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "ops" / "checks"))

import check_sources as cs  # noqa: E402


@pytest.mark.parametrize("name, cls", [
    ("MIT", "open"), ("Apache-2.0", "open"), ("BSD-3-Clause", "open"), ("CC0-1.0", "open"),
    ("CC-BY-4.0", "open"), ("CC BY 4.0", "open"), ("CERN-OHL-P-2.0", "open"),
    ("GPL-3.0", "share-alike"), ("AGPL-3.0", "share-alike"), ("LGPL-2.1", "share-alike"),
    ("CC-BY-SA-4.0", "share-alike"), ("CC BY-SA 4.0", "share-alike"),
    ("CERN-OHL-S-2.0", "share-alike"), ("CERN OHL v1.2", "share-alike"),
    ("TAPR Open Hardware License", "share-alike"), ("MPL-2.0", "share-alike"),
    ("CC-BY-NC-SA-4.0", "restricted"), ("CC BY-NC 4.0", "restricted"),
    ("CC-BY-ND-4.0", "restricted"), ("PolyForm Noncommercial 1.0.0", "restricted"),
    ("NOASSERTION", "unknown"), ("Other", "unknown"), ("", "unknown"), (None, "unknown"),
])
def test_licences_are_classed(name, cls):
    assert cs.classify(name) == cls


def test_repository_urls():
    assert cs.repository("https://github.com/o/r/tree/main/hw") == ("github", "o/r")
    assert cs.repository("https://github.com/o/r.git") == ("github", "o/r")
    assert cs.repository("https://gitlab.com/g/sub/r/-/blob/main/x") == ("gitlab", "g/sub/r")
    assert cs.repository("https://example.org/o/r") is None
    assert cs.repository("https://github.com/o") is None


def fake_web(routes):
    """get(method, url, headers) answering from {(method, url): status or (status, body)}."""
    calls = []

    def get(method, url, headers=None):
        calls.append((method, url))
        answer = routes.get((method, url), routes.get(("*", url)))
        if answer is None:
            raise urllib.error.URLError("no route")
        status, body = answer if isinstance(answer, tuple) else (answer, b"")
        return status, {}, body if isinstance(body, bytes) else json.dumps(body).encode(), url
    get.calls = calls
    return get


def allow(url):
    return None


def test_link_states():
    get = fake_web({("HEAD", "https://a.org"): 200, ("HEAD", "https://b.org"): 404,
                    ("HEAD", "https://c.org"): 405, ("GET", "https://c.org"): 200,
                    ("HEAD", "https://d.org"): 403, ("GET", "https://d.org"): 403,
                    ("HEAD", "https://e.org"): 503})
    states = {u: cs.check_link(u, get, allow)["state"] for u in
              ("https://a.org", "https://b.org", "https://c.org", "https://d.org",
               "https://e.org", "https://f.org")}
    assert states == {"https://a.org": "ok", "https://b.org": "broken", "https://c.org": "ok",
                      "https://d.org": "blocked", "https://e.org": "unreachable",
                      "https://f.org": "unreachable"}
    assert cs.check_link("http://10.0.0.1/x")["state"] == "skipped"
    assert cs.check_link("file:///etc/passwd")["state"] == "skipped"


GH = "https://api.github.com/repos/o/r"


def item(**kw):
    return {"id": 1, "code": "c1-x", "urls": ["https://github.com/o/r", "https://x.org"],
            "reported": [], **kw}


def web(github):
    return fake_web({("*", "https://github.com/o/r"): 200, ("*", "https://x.org"): 200,
                     ("GET", GH): github})


def test_a_github_licence_is_verified():
    get = web((200, {"license": {"spdx_id": "CERN-OHL-S-2.0", "name": "CERN OHL S"},
                     "pushed_at": "2026-05-01T00:00:00Z", "archived": False}))
    result = cs.check_project(item(), get, allow)
    assert (result["licence"], result["licence_class"]) == ("CERN-OHL-S-2.0", "share-alike")
    assert result["licence_source"] == "GitHub (verified)"
    assert result["last_activity"] == "2026-05-01T00:00:00Z" and result["archived"] is False
    assert [link["state"] for link in result["links"]] == ["ok", "ok"]


def test_a_repository_without_a_licence_is_all_rights_reserved():
    get = web((200, {"license": None, "pushed_at": "2020-01-01T00:00:00Z", "archived": True}))
    result = cs.check_project(item(), get, allow)
    assert result["licence_class"] == "none" and result["archived"] is True
    # A licence the build pack found (say in the README) is used, marked unverified.
    result = cs.check_project(item(reported=[{"url": "u", "licence": "CC BY-SA 4.0"}]),
                              get, allow)
    assert result["licence_class"] == "share-alike"
    assert result["licence_source"] == "reported by the build pack (not verified)"


def test_an_unrecognised_licence_file_is_unknown():
    get = web((200, {"license": {"spdx_id": "NOASSERTION", "name": "Other"}}))
    result = cs.check_project(item(), get, allow)
    assert (result["licence"], result["licence_class"]) == ("Other", "unknown")
    assert "check it by hand" in result["licence_source"]


def test_no_repository_uses_the_reported_licence_or_unknown():
    get = fake_web({("*", "https://x.org"): 200})
    assert cs.check_project({"urls": ["https://x.org"]}, get, allow)["licence_class"] == "unknown"
    result = cs.check_project({"urls": ["https://x.org"],
                               "reported": [{"licence": "CC BY-NC-SA 4.0"}]}, get, allow)
    assert result["licence_class"] == "restricted"


def test_a_rate_limit_stops_the_batch_without_recording():
    posted = []

    def api(method, path, body=None):
        if method == "GET":
            return [item(id=1, code="first"), item(id=2, code="second")]
        posted.append((path, body))
        return {}

    limited = fake_web({("*", "https://github.com/o/r"): 200, ("*", "https://x.org"): 200,
                        ("GET", GH): (429, {})})
    assert cs.run(api, limited, allow) == {}
    assert posted == []
    ok = web((200, {"license": {"spdx_id": "MIT"}}))
    assert cs.run(api, ok, allow) == {"open": 2, "broken_links": 0}
    assert [p for p, _ in posted] == ["/projects/1/check", "/projects/2/check"]
