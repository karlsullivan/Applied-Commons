"""The SharePoint off-site copy (ops/backup/offsite_sharepoint.py) against
a fake Microsoft Graph; no network or database needed."""

import io
import json
import pathlib
import shutil
import sys
import urllib.error
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "ops" / "backup"))

import offsite_sharepoint as osp  # noqa: E402

NOW = datetime(2026, 11, 20, 3, 0, tzinfo=timezone.utc)


class FakeGraph:
    """A folder tree addressed by item id, like the drive endpoints."""

    def __init__(self):
        self.items = {"root": {"name": "Apollo Backups", "kids": {}}}
        self.uploads, self.deleted, self.n = [], [], 0

    def _new(self, parent, name, **extra):
        self.n += 1
        item_id = f"i{self.n}"
        self.items[item_id] = {"name": name, "kids": {}, **extra}
        self.items[parent]["kids"][name] = item_id
        return item_id

    def children(self, item):
        return {name: {"id": i, "name": name} for name, i in self.items[item]["kids"].items()}

    def folder(self, parent, name, known=None):
        return self.items[parent]["kids"].get(name) or self._new(parent, name)

    def upload(self, parent, name, path):
        self._new(parent, name, data=path.read_bytes())
        self.uploads.append(name)

    def delete(self, item):
        for node in self.items.values():
            node["kids"] = {k: v for k, v in node["kids"].items() if v != item}
        self.deleted.append(self.items.pop(item)["name"])

    def path(self, *names):
        item = "root"
        for name in names:
            item = self.items[item]["kids"][name]
        return self.items[item]


def fake_crypt(src, dest):
    dest.write_bytes(b"sealed:" + src.read_bytes())


def layout(tmp_path):
    backups, exports = tmp_path / "b", tmp_path / "e"
    backups.mkdir()
    for name in ("applied_commons_20261119T020000Z.dump", "sites_20261119T020000Z.tar.gz",
                 ".applied_commons_20261120T020000Z.dump.tmp", "notes.txt"):
        (backups / name).write_bytes(b"x")
    repo = exports / "designs" / "c1-sand" / "repos"
    repo.mkdir(parents=True)
    (repo / "sand-abc123.tar.gz").write_bytes(b"repo")
    (repo / ".partial").write_bytes(b"half")
    return backups, exports


def test_sync_uploads_new_backups_and_exports_once(tmp_path):
    graph = FakeGraph()
    backups, exports = layout(tmp_path)
    report = osp.sync(graph, "root", backups, exports, NOW, fake_crypt)
    assert report == {"uploaded": 2, "pruned": 0, "exports": 1}
    assert sorted(graph.path("backups")["kids"]) == [
        "applied_commons_20261119T020000Z.dump.gpg", "sites_20261119T020000Z.tar.gz.gpg"]
    sealed = graph.path("exports", "designs", "c1-sand", "repos", "sand-abc123.tar.gz.gpg")
    assert sealed["data"] == b"sealed:repo"
    assert osp.sync(graph, "root", backups, exports, NOW, fake_crypt) == {
        "uploaded": 0, "pruned": 0, "exports": 0}
    assert len(graph.uploads) == 3


def test_old_backups_are_pruned_but_exports_are_kept(tmp_path):
    graph = FakeGraph()
    backups, exports = layout(tmp_path)
    osp.sync(graph, "root", backups, exports, NOW, fake_crypt)
    shutil.rmtree(exports / "designs" / "c1-sand")  # gone locally: kept off-site
    later = datetime(2026, 12, 20, 3, 0, tzinfo=timezone.utc)
    report = osp.sync(graph, "root", backups, exports, later, fake_crypt)
    # The local copies still exist, so they would go up again: the local
    # retention (14 days) is shorter than the off-site one (30).
    assert report["pruned"] == 2
    assert "sand-abc123.tar.gz.gpg" in graph.path("exports", "designs", "c1-sand", "repos")["kids"]


def test_expired_reads_the_stamp_in_the_name():
    names = ["applied_commons_20261020T020000Z.dump.gpg", "sites_20261021T020000Z.tar.gz.gpg",
             "applied_commons_20261101T020000Z.dump.gpg", "readme.txt"]
    assert osp.expired(names, NOW, days=30) == names[:2]


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_requests_sign_in_once_and_retry_throttling(monkeypatch):
    monkeypatch.setattr(osp.time, "sleep", lambda s: None)
    calls = []

    def opener(req, timeout):
        calls.append((req.get_method(), req.full_url, dict(req.header_items())))
        if "login.microsoftonline.com" in req.full_url:
            return Resp(json.dumps({"access_token": "tok", "expires_in": 3600}).encode())
        if sum(1 for c in calls if "children" in c[1]) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "slow down",
                                         {"Retry-After": "1"}, io.BytesIO(b"{}"))
        return Resp(json.dumps({"value": [{"id": "a", "name": "backups"}]}).encode())

    graph = osp.Graph("tenant", "client", "secret", "drive", opener)
    assert graph.children("root") == {"backups": {"id": "a", "name": "backups"}}
    assert graph.children("root")
    assert sum(1 for c in calls if "login" in c[1]) == 1
    assert calls[1][1].startswith(f"{osp.GRAPH}/drives/drive/items/root/children")
    assert calls[1][2]["Authorization"] == "Bearer tok"


def test_large_files_go_through_an_upload_session(tmp_path, monkeypatch):
    monkeypatch.setattr(osp, "SIMPLE_MAX", 10)
    monkeypatch.setattr(osp, "CHUNK", 8)
    sent = []

    def opener(req, timeout):
        if "login.microsoftonline.com" in req.full_url:
            return Resp(json.dumps({"access_token": "tok", "expires_in": 3600}).encode())
        sent.append((req.get_method(), req.full_url, dict(req.header_items()), req.data))
        if req.full_url.endswith("createUploadSession"):
            return Resp(json.dumps({"uploadUrl": "https://upload.example/s1"}).encode())
        return Resp(b"{}")

    big = tmp_path / "big.gpg"
    big.write_bytes(b"0123456789abcdefghij")  # 20 bytes: chunks of 8, 8, 4
    osp.Graph("t", "c", "s", "d", opener).upload("folder", "big.gpg", big)
    assert sent[0][1].endswith("/drives/d/items/folder:/big.gpg:/createUploadSession")
    puts = sent[1:]
    assert [p[2]["Content-range"] for p in puts] == [
        "bytes 0-7/20", "bytes 8-15/20", "bytes 16-19/20"]
    assert all("Authorization" not in p[2] for p in puts)  # the URL carries its own auth
    assert b"".join(p[3] for p in puts) == big.read_bytes()


def test_the_key_is_made_once_and_private(tmp_path):
    key = tmp_path / "etc" / "backup.key"
    assert osp.make_key(key) and not osp.make_key(key)
    assert key.stat().st_mode & 0o777 == 0o600
    assert len(key.read_text().strip()) >= 40
