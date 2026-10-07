#!/usr/bin/env python3
"""Off-site copy of the Applied Commons backups to one SharePoint folder,
encrypted (run by backup-applied-commons.sh as root; stdlib and gpg only).

The app registration has write access to that folder only (Microsoft
Graph ListItems.SelectedOperations.Selected, granted on the folder), so
everything is addressed from the folder's item id, never from the
library root. In the folder:

- ``backups/``: each local dump and site-profile archive, encrypted with
  gpg (AES-256, passphrase in $AC_BACKUP_KEY_FILE), kept $AC_SP_KEEP_DAYS
  days (30);
- ``exports/``: the design archives, encrypted, uploaded once each (their
  names never change) and never deleted here.

Configuration (/etc/applied-commons/backup.env, root 0600): AC_SP_TENANT,
AC_SP_CLIENT_ID, AC_SP_CLIENT_SECRET, AC_SP_DRIVE_ID, AC_SP_FOLDER_ID.
The key file (/etc/applied-commons/backup.key) is created by ``key``; a
copy must be kept outside the VM, or the off-site copies cannot be read.

    offsite_sharepoint.py key           create the key if there is none
    offsite_sharepoint.py check         sign in and list the folder
    offsite_sharepoint.py sync          upload what is new, prune old backups
    offsite_sharepoint.py restore-test  download the newest dump, decrypt
                                        it and list it with pg_restore

Restore by hand: download a ``.gpg`` file, then
``gpg --decrypt --passphrase-file backup.key --pinentry-mode loopback
--batch -o file file.gpg``.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

GRAPH = "https://graph.microsoft.com/v1.0"
BACKUP_DIR = Path(os.environ.get("AC_BACKUP_DIR", "/var/backups/applied-commons"))
EXPORTS = Path(os.environ.get("AC_EXPORTS", "/srv/orchestrator/tenants/applied-commons/exports"))
KEY_FILE = Path(os.environ.get("AC_BACKUP_KEY_FILE", "/etc/applied-commons/backup.key"))
KEEP_DAYS = int(os.environ.get("AC_SP_KEEP_DAYS", "30"))
SIMPLE_MAX = 4 * 2**20          # larger files go through an upload session
CHUNK = 320 * 2**10 * 32        # 10 MiB; Graph wants multiples of 320 KiB
BACKUP_NAME = re.compile(r"^(applied_commons|sites)_(\d{8}T\d{6}Z)\.")

Json = dict[str, Any]


class GraphError(RuntimeError):
    def __init__(self, status: int, text: str):
        super().__init__(f"Graph HTTP {status}: {text[:300]}")
        self.status = status


class Graph:
    """Minimal Microsoft Graph client: app-only token, retries on 429/5xx."""

    def __init__(self, tenant: str, client_id: str, secret: str, drive: str,
                 opener: Callable = urllib.request.urlopen):
        self.tenant, self.client_id, self.secret, self.drive = tenant, client_id, secret, drive
        self._open, self._token, self._expires = opener, None, 0.0

    def token(self) -> str:
        if self._token and time.time() < self._expires - 120:
            return self._token
        body = urllib.parse.urlencode({
            "client_id": self.client_id, "client_secret": self.secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials"}).encode()
        url = f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token"
        with self._open(urllib.request.Request(url, data=body), timeout=30) as resp:
            data = json.loads(resp.read())
        self._token, self._expires = data["access_token"], time.time() + int(data["expires_in"])
        return self._token

    def request(self, method: str, url: str, body: bytes | Json | None = None,
                headers: dict | None = None, auth: bool = True, raw: bool = False) -> Any:
        url = url if url.startswith("https://") else GRAPH + url
        headers = dict(headers or {})
        if isinstance(body, dict):
            body, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        for attempt in range(6):
            if auth:
                headers["Authorization"] = f"Bearer {self.token()}"
            req = urllib.request.Request(url, data=body, method=method, headers=headers)
            try:
                with self._open(req, timeout=300) as resp:
                    data = resp.read()
                    return data if raw else (json.loads(data) if data else {})
            except urllib.error.HTTPError as exc:
                text = exc.read().decode(errors="replace")
                if exc.code in (429, 500, 502, 503, 504) and attempt < 5:
                    time.sleep(min(int(exc.headers.get("Retry-After") or 2 ** attempt), 60))
                    continue
                raise GraphError(exc.code, text) from None
        raise AssertionError("unreachable")

    def children(self, item: str) -> dict[str, Json]:
        out, url = {}, (f"/drives/{self.drive}/items/{item}/children"
                        "?$select=id,name,size,createdDateTime,folder&$top=200")
        while url:
            page = self.request("GET", url)
            out.update({c["name"]: c for c in page.get("value", [])})
            url = page.get("@odata.nextLink")
        return out

    def folder(self, parent: str, name: str, known: dict[str, Json] | None = None) -> str:
        known = self.children(parent) if known is None else known
        if name in known:
            return known[name]["id"]
        made = self.request("POST", f"/drives/{self.drive}/items/{parent}/children",
                            {"name": name, "folder": {},
                             "@microsoft.graph.conflictBehavior": "fail"})
        return made["id"]

    def upload(self, parent: str, name: str, path: Path) -> None:
        target = f"/drives/{self.drive}/items/{parent}:/{urllib.parse.quote(name)}:"
        size = path.stat().st_size
        if size <= SIMPLE_MAX:
            self.request("PUT", f"{target}/content", path.read_bytes(),
                         {"Content-Type": "application/octet-stream"})
            return
        session = self.request("POST", f"{target}/createUploadSession",
                               {"item": {"@microsoft.graph.conflictBehavior": "replace"}})
        with path.open("rb") as fh:
            start = 0
            while start < size:
                chunk = fh.read(CHUNK)
                end = start + len(chunk) - 1
                # The upload URL carries its own authorisation.
                self.request("PUT", session["uploadUrl"], chunk, {
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Content-Length": str(len(chunk))}, auth=False)
                start = end + 1

    def delete(self, item: str) -> None:
        self.request("DELETE", f"/drives/{self.drive}/items/{item}")

    def download(self, item: str) -> bytes:
        meta = self.request("GET", f"/drives/{self.drive}/items/{item}")
        return self.request("GET", meta["@microsoft.graph.downloadUrl"], auth=False, raw=True)


def _gpg(args: list[str], key: Path) -> None:
    # A throwaway home: systemd gives the service no HOME, and symmetric
    # encryption needs no keyring.
    with tempfile.TemporaryDirectory() as home:
        subprocess.run(["gpg", "--homedir", home, "--batch", "--yes", "--quiet",
                        "--pinentry-mode", "loopback", "--passphrase-file", str(key), *args],
                       check=True)


def encrypt(src: Path, dest: Path, key: Path = KEY_FILE) -> None:
    _gpg(["--symmetric", "--cipher-algo", "AES256", "-o", str(dest), str(src)], key)


def decrypt(src: Path, dest: Path, key: Path = KEY_FILE) -> None:
    _gpg(["--decrypt", "-o", str(dest), str(src)], key)


def _send(graph: Graph, parent: str, src: Path, name: str, work: Path,
          crypt: Callable = encrypt) -> None:
    sealed = work / name
    crypt(src, sealed)
    try:
        graph.upload(parent, name, sealed)
    finally:
        sealed.unlink(missing_ok=True)


def expired(names: list[str], now: datetime, days: int = KEEP_DAYS) -> list[str]:
    """Backup file names older than ``days`` by the stamp in their name."""
    out = []
    for name in names:
        m = BACKUP_NAME.match(name)
        if m and datetime.strptime(m[2], "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc) < now - timedelta(days=days):
            out.append(name)
    return out


def sync(graph: Graph, root: str, backups: Path = BACKUP_DIR, exports: Path = EXPORTS,
         now: datetime | None = None, crypt: Callable = encrypt) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    report = {"uploaded": 0, "pruned": 0, "exports": 0}
    top = graph.children(root)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        folder = graph.folder(root, "backups", top)
        remote = graph.children(folder)
        for src in sorted(backups.glob("*")):
            if src.is_file() and BACKUP_NAME.match(src.name) and f"{src.name}.gpg" not in remote:
                _send(graph, folder, src, f"{src.name}.gpg", work, crypt)
                report["uploaded"] += 1
        for name in expired(list(remote), now):
            graph.delete(remote[name]["id"])
            report["pruned"] += 1
        if exports.is_dir():
            ids: dict[Path, str] = {Path("."): graph.folder(root, "exports", top)}
            listing: dict[str, dict[str, Json]] = {}
            for src in sorted(p for p in exports.rglob("*")
                              if p.is_file() and not p.name.startswith(".")):
                rel = src.relative_to(exports)
                parent = Path(".")
                for part in rel.parent.parts:  # make the folders on the way
                    here = parent / part
                    if here not in ids:
                        known = listing.setdefault(ids[parent], graph.children(ids[parent]))
                        ids[here] = graph.folder(ids[parent], part, known)
                    parent = here
                known = listing.setdefault(ids[parent], graph.children(ids[parent]))
                if f"{src.name}.gpg" not in known:
                    _send(graph, ids[parent], src, f"{src.name}.gpg", work, crypt)
                    report["exports"] += 1
    return report


def restore_test(graph: Graph, root: str) -> str:
    folder = graph.children(root).get("backups")
    if not folder:
        raise SystemExit("no backups folder in SharePoint yet")
    dumps = sorted(n for n in graph.children(folder["id"]) if n.startswith("applied_commons_"))
    if not dumps:
        raise SystemExit("no dump in SharePoint yet")
    item = graph.children(folder["id"])[dumps[-1]]
    with tempfile.TemporaryDirectory() as tmp:
        sealed, plain = Path(tmp) / "x.gpg", Path(tmp) / "x.dump"
        sealed.write_bytes(graph.download(item["id"]))
        decrypt(sealed, plain)
        listing = subprocess.run(["docker", "exec", "-i", "ac-postgres", "pg_restore", "--list"],
                                 stdin=plain.open("rb"), capture_output=True, text=True,
                                 check=True).stdout
    tables = sum(1 for line in listing.splitlines() if " TABLE DATA " in line)
    if not tables:
        raise SystemExit(f"{dumps[-1]}: decrypted, but lists no tables")
    return f"{dumps[-1]}: downloaded, decrypted, {tables} tables"


def make_key(path: Path = KEY_FILE) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(secrets.token_urlsafe(32) + "\n")
    return True


def from_env() -> tuple[Graph, str]:
    env = os.environ
    missing = [k for k in ("AC_SP_TENANT", "AC_SP_CLIENT_ID", "AC_SP_CLIENT_SECRET",
                           "AC_SP_DRIVE_ID", "AC_SP_FOLDER_ID") if not env.get(k)]
    if missing:
        raise SystemExit(f"not configured: {', '.join(missing)} (/etc/applied-commons/backup.env)")
    return (Graph(env["AC_SP_TENANT"], env["AC_SP_CLIENT_ID"], env["AC_SP_CLIENT_SECRET"],
                  env["AC_SP_DRIVE_ID"]), env["AC_SP_FOLDER_ID"])


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "sync"
    if command == "key":
        print("key created" if make_key() else "key exists", KEY_FILE)
        return 0
    graph, root = from_env()
    if command == "check":
        print("signed in; folder holds:", ", ".join(sorted(graph.children(root))) or "nothing yet")
    elif command == "sync":
        if not KEY_FILE.exists():
            raise SystemExit(f"no key at {KEY_FILE}: run '{argv[0]} key' first")
        print("sharepoint:", json.dumps(sync(graph, root)))
    elif command == "restore-test":
        print(restore_test(graph, root))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
