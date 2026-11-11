"""The packaged app's updates (pokeuds/app/update.py): which release is offered, the download and its checks, and
the swap, which replaces only what a release ships and never the player's folders."""

import hashlib
import http.server
import json
import subprocess
import sys
import threading
import zipfile

import pytest

from pokeuds.app import update

PAGE = "https://github.com/Gr3nSkyDragon/PokeUDS/releases/tag/"
DOWNLOAD = "https://github.com/Gr3nSkyDragon/PokeUDS/releases/download/"


def release(tag, *, draft=False, prerelease=True, assets=("PokeUDS-windows.zip", "SHA256SUMS")):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "html_url": PAGE + tag, "body": f"notes {tag}",
            "assets": [{"name": a, "browser_download_url": DOWNLOAD + f"{tag}/{a}"} for a in assets]}


def test_the_newest_release_is_offered():
    replies = [release("v0.1.4"), release("v0.2.0", draft=True), release("v0.1.10"), release("nightly"),
               release("v0.1.3")]
    found = update.newest(replies, "0.1.3", "win32")
    assert found.version == "0.1.10" and found.installable                  # pre-releases count; drafts never do
    assert found.download == DOWNLOAD + "v0.1.10/PokeUDS-windows.zip" and found.notes == "notes v0.1.10"
    assert update.newest(replies, "0.1.10", "win32") is None
    assert not update.newest(replies, "0.1.3", "darwin").installable          # the Mac app: download by hand
    forged = release("v0.1.5")
    forged["assets"][0]["browser_download_url"] = "https://example.com/PokeUDS-windows.zip"
    assert not update.newest([forged], "0.1.3", "win32").installable          # only the release's own files


def zipped(path, names):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in names.items():
            z.writestr(name, data)
    return path


def test_unpacking_refuses_what_is_not_an_app(tmp_path):
    good = zipped(tmp_path / "good.zip", {"PokeUDS/PokeUDS.exe": b"exe", "PokeUDS/_internal/a.dll": b"a"})
    assert (update.unpack(good, tmp_path / "out") / "PokeUDS.exe").read_bytes() == b"exe"
    for name, bad in (("climb", {"PokeUDS/PokeUDS.exe": b"", "PokeUDS/../evil.txt": b""}),
                      ("bank", {"PokeUDS/PokeUDS.exe": b"", "PokeUDS/Bank/x.pk6": b""}),
                      ("outside", {"PokeUDS/PokeUDS.exe": b"", "other/x": b""}),
                      ("no exe", {"PokeUDS/_internal/a.dll": b""})):
        with pytest.raises(OSError):
            update.unpack(zipped(tmp_path / f"{name}.zip", bad), tmp_path / name)
        assert not (tmp_path / name).exists()


@pytest.fixture
def server(tmp_path):
    """GitHub's release list and files, from 127.0.0.1."""
    files = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = files.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.send_header("Content-Length", str(len(body or b"")))
            self.end_headers()
            self.wfile.write(body or b"")

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, files
    httpd.shutdown()


def test_check_and_download(server, tmp_path):
    base, files = server
    archive = zipped(tmp_path / "PokeUDS-windows.zip", {"PokeUDS/PokeUDS.exe": b"new exe", "PokeUDS/LICENSE.txt": b"AGPL"})
    data = archive.read_bytes()
    files["/releases"] = json.dumps([{"tag_name": "v9.0.0", "draft": False, "html_url": base + "/tag/v9.0.0",
                                      "assets": [{"name": "PokeUDS-windows.zip", "browser_download_url": base + "/PokeUDS-windows.zip"},
                                                 {"name": "SHA256SUMS", "browser_download_url": base + "/SHA256SUMS"}]}]).encode()
    files["/PokeUDS-windows.zip"] = data
    files["/SHA256SUMS"] = f"{hashlib.sha256(data).hexdigest()}  PokeUDS-windows.zip\n".encode()
    found = update.check(base + "/releases", "0.1.3")
    assert found.version == "9.0.0" and found.installable
    root = tmp_path / "app"
    root.mkdir()
    new = update.prepare(found, root)
    assert new == root / "Data" / "update" / "new" / "PokeUDS" and (new / "PokeUDS.exe").read_bytes() == b"new exe"
    assert not (root / "Data" / "update" / "PokeUDS-windows.zip").exists()
    files["/SHA256SUMS"] = ("0" * 64 + "  PokeUDS-windows.zip\n").encode()
    with pytest.raises(OSError, match="does not match its SHA-256"):
        update.prepare(found, root)
    assert update.check(base + "/releases", "9.0.0") is None


def app_folder(root, exe=b"old exe"):
    for rel, data in {"PokeUDS.exe": exe, "_internal/python.dll": b"old dll", "_internal/old_only.pyd": b"gone",
                      "LICENSE.txt": b"AGPL", "Bank/a.pk6": b"pk6", "Bank/.info/a.json": b"{}",
                      "Bank/.gts/listing-1/state.json": b"{\"kind\":\"listing\"}", "Data/settings.json": b"{}",
                      "received/t.pk2": b"pk2", "my notes.txt": b"mine"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)


KEPT = ("Bank/a.pk6", "Bank/.info/a.json", "Bank/.gts/listing-1/state.json", "Data/settings.json", "received/t.pk2",
        "my notes.txt")


def finished_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def new_app(root):
    new = root / "Data" / "update" / "new" / "PokeUDS"
    for rel, data in {"PokeUDS.exe": b"new exe", "_internal/python.dll": b"new dll", "LICENSE.txt": b"AGPL 2"}.items():
        (new / rel).parent.mkdir(parents=True, exist_ok=True)
        (new / rel).write_bytes(data)
    return new


def test_the_swap_keeps_the_players_folders(tmp_path):
    root = tmp_path / "PokeUDS"
    app_folder(root)
    before = {rel: (root / rel).read_bytes() for rel in KEPT}
    new = new_app(root)
    opened = []
    assert update.apply(new, root, finished_pid(), "9.0.0", relaunch=opened.append, helper=12345)
    assert opened == [root] and (root / "PokeUDS.exe").read_bytes() == b"new exe"
    assert (root / "_internal" / "python.dll").read_bytes() == b"new dll" and not (root / "_internal" / "old_only.pyd").exists()
    assert {rel: (root / rel).read_bytes() for rel in KEPT} == before          # not one of the player's files moved
    outcome = update.finish(root)
    assert outcome == {"version": "9.0.0", "error": "", "helper": 12345}
    assert not (root / update.OLD).exists() and update.finish(root) is None   # said once


def test_a_failed_swap_puts_the_old_app_back(tmp_path, monkeypatch):
    root = tmp_path / "PokeUDS"
    app_folder(root)
    new = new_app(root)

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(update.shutil, "copytree", broken)
    assert not update.apply(new, root, finished_pid(), "9.0.0", relaunch=lambda r: None)
    assert (root / "PokeUDS.exe").read_bytes() == b"old exe" and (root / "_internal" / "old_only.pyd").exists()
    assert all((root / rel).exists() for rel in KEPT)
    assert "disk full" in update.finish(root)["error"]


def test_a_release_that_would_replace_a_players_folder_is_refused(tmp_path):
    root = tmp_path / "PokeUDS"
    app_folder(root)
    new = new_app(root)
    (new / "Bank").mkdir()
    assert not update.apply(new, root, finished_pid(), "9.0.0", relaunch=lambda r: None)
    assert (root / "PokeUDS.exe").read_bytes() == b"old exe" and (root / "Bank" / "a.pk6").exists()


def test_who_can_update(tmp_path):
    assert update.install_root(frozen=False) is None and update.install_root(str(tmp_path / "x.exe"), True, "darwin") is None
    assert update.install_root(str(tmp_path / "PokeUDS.exe"), True, "win32") == tmp_path.resolve()
    assert "packaged Windows app" in update.blocker(None) and update.blocker(tmp_path) == ""


def test_a_newer_release_adds_the_rail_entry_and_no_banner(tmp_path, monkeypatch):
    pytest.importorskip("flet")
    import time

    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    from gui.app import App
    from gui.updates import Updates
    from pokeuds.app.settings import Settings

    monkeypatch.setattr(update, "check", lambda *a, **k: update.Release("9.0.0", "v9.0.0", PAGE + "v9.0.0", "", "", ""))
    shown = []
    updates = Updates(App(None, Settings()))
    updates.on_available = lambda: shown.append(True)
    updates.check(quiet=True)
    for _ in range(100):
        if shown:
            break
        time.sleep(0.02)
    assert shown and updates.available() and not updates.banner.visible
