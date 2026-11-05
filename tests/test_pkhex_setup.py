"""Getting the PKHeX helper: where it is looked for, the release download and its checks, SDK detection."""

import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from pokeuds import pkhex

FAKE_HELPER = "#!/bin/sh\nread line\necho '{\"ok\":true,\"pkhex\":\"26.10.18.0\"}'\n"


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("POKEUDS_PKHEX", raising=False)
    root = tmp_path / "www"
    root.mkdir()

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Quiet(*a, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setattr(pkhex, "RELEASES", f"{base}/releases.json")
    yield root, base
    httpd.shutdown()


def publish(root: Path, base: str, data: bytes, digest: str | None = None, name: str | None = None) -> None:
    name = name or pkhex.asset_name()
    (root / name).write_bytes(data)
    (root / "SHA256SUMS").write_text(f"{digest or hashlib.sha256(data).hexdigest()}  {name}\n")
    releases = [{"tag_name": "v9.9.9", "draft": True, "assets": []},
                {"tag_name": "v0.2.0", "assets": [{"name": name, "browser_download_url": f"{base}/{name}"},
                                                  {"name": "SHA256SUMS", "browser_download_url": f"{base}/SHA256SUMS"}]}]
    (root / "releases.json").write_text(json.dumps(releases))


@pytest.mark.skipif(sys.platform == "win32", reason="the fake helper is a shell script")
def test_download_checks_and_installs(server):
    root, base = server
    publish(root, base, FAKE_HELPER.encode())
    path = pkhex.download_helper(say=lambda _: None)
    assert path == pkhex.downloaded_path() and os.access(path, os.X_OK)
    assert pkhex.helper_path() in (path, *pkhex.candidates())          # the newest found wins
    publish(root, base, FAKE_HELPER.encode(), digest="0" * 64)
    path.unlink()
    with pytest.raises(OSError, match="SHA256SUMS"):
        pkhex.download_helper(say=lambda _: None)
    assert not path.exists()


def test_no_release_for_this_computer(server):
    root, base = server
    publish(root, base, b"x", name="pokeuds-pkhex-plan9-mips")
    with pytest.raises(OSError, match="no PokeUDS release carries a helper"):
        pkhex.download_helper(say=lambda _: None)


@pytest.mark.skipif(sys.platform == "win32", reason="the fake helper is a shell script")
def test_a_broken_download_is_refused(server):
    root, base = server
    publish(root, base, b"#!/bin/sh\necho nope\n")
    with pytest.raises(OSError, match="does not start"):
        pkhex.download_helper(say=lambda _: None)


def test_the_newest_helper_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("POKEUDS_PKHEX", raising=False)
    old, new = tmp_path / "old" / pkhex.EXE, tmp_path / "new" / pkhex.EXE
    for path in (old, new):
        path.parent.mkdir()
        path.write_text("x")
    os.utime(old, (time.time() - 100, time.time() - 100))
    monkeypatch.setattr(pkhex, "candidates", lambda: [old, new, tmp_path / "missing" / pkhex.EXE])
    assert pkhex.helper_path() == new
    monkeypatch.setenv("POKEUDS_PKHEX", str(old))
    assert pkhex.helper_path() == old


def test_sdk_detection_and_status(monkeypatch):
    monkeypatch.setattr(pkhex, "helper_path", lambda: None)
    monkeypatch.setattr(pkhex, "dotnet_sdks", lambda: [])
    assert "Get it" in pkhex.status() and not pkhex.can_build()
    monkeypatch.setattr(pkhex, "dotnet_sdks", lambda: ["8.0.404"])
    assert "older than the .NET 10" in pkhex.status() and not pkhex.can_build()
    with pytest.raises(pkhex.HelperMissing, match="needs the .NET 10 SDK"):
        monkeypatch.setattr(pkhex.shutil, "which", lambda _: "/usr/bin/dotnet")
        pkhex.build_helper()
    monkeypatch.setattr(pkhex, "dotnet_sdks", lambda: ["8.0.404", "10.0.114"])
    assert pkhex.can_build()
    done = subprocess.CompletedProcess([], 1, "x.cs(3,4): error CS1002: ; expected\nBuild FAILED.\n", "")
    assert pkhex.build_problem(done) == "x.cs(3,4): error CS1002: ; expected"
    assert pkhex.runtime_id().split("-")[0] in ("win", "osx", "linux")
