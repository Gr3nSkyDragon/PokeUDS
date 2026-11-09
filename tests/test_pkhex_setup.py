"""Finding PKHeX: the packaged app's own copy (repaired when it cannot run), a source tree's builds, SDK detection."""

import os
import subprocess
import sys
import time

import pytest

from pokeuds import pkhex


def _helper(path, mode=0o755, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


@pytest.fixture
def places(tmp_path, monkeypatch):
    """A bundled copy, two source builds and the data folder, all under tmp_path."""
    monkeypatch.delenv("POKEUDS_PKHEX", raising=False)
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path / "data"))
    bundled = tmp_path / "app" / pkhex.EXE
    old, new = tmp_path / "old" / pkhex.EXE, tmp_path / "new" / pkhex.EXE
    monkeypatch.setattr(pkhex, "bundled_path", lambda: bundled)
    monkeypatch.setattr(pkhex, "candidates", lambda: [old, new, tmp_path / "missing" / pkhex.EXE])
    return bundled, old, new


def test_from_source_the_newest_helper_wins(places, monkeypatch):
    bundled, old, new = places
    _helper(old)
    _helper(new)
    os.utime(old, (time.time() - 100, time.time() - 100))
    assert pkhex.helper_path() == new
    _helper(bundled)                    # a copy scripts/build_exe.py left behind is one more build: the newest wins
    os.utime(bundled, (time.time() - 200, time.time() - 200))
    assert pkhex.helper_path() == new
    monkeypatch.setenv("POKEUDS_PKHEX", str(old))
    assert pkhex.helper_path() == old


def test_the_packaged_app_uses_its_own_copy(places, monkeypatch):
    bundled, old, new = places
    _helper(new)                        # newer, but not the app's: an old download or build never wins in the app
    _helper(bundled)
    os.utime(bundled, (time.time() - 100, time.time() - 100))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert pkhex.helper_path() == bundled
    assert pkhex.status().startswith("PKHeX is ready (part of the app")


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no executable bit")
def test_a_copy_that_lost_its_executable_bit_is_repaired(places, monkeypatch):
    """The Mac app's copy without its executable bit is copied to the data folder and made runnable, offline."""
    bundled, _, _ = places
    _helper(bundled, 0o644, "helper v1")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    fixed = pkhex.helper_path()
    assert fixed == pkhex.repair_path() and os.access(fixed, os.X_OK) and fixed.read_text() == "helper v1"
    made = fixed.stat().st_mtime
    assert pkhex.helper_path() == fixed and fixed.stat().st_mtime == made         # made once
    time.sleep(0.01)
    _helper(bundled, 0o644, "helper v2, a newer app")
    assert pkhex.helper_path().read_text() == "helper v2, a newer app"              # made again for a new app


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no executable bit")
def test_from_source_a_helper_that_cannot_run_is_skipped(places):
    _, old, new = places
    _helper(old)
    _helper(new, 0o644)
    os.utime(old, (time.time() - 100, time.time() - 100))         # older, yet the only one that runs
    assert pkhex.helper_path() == old
    old.chmod(0o644)
    assert pkhex.helper_path() is None


def test_sdk_detection_and_status(monkeypatch):
    monkeypatch.setattr(pkhex, "helper_path", lambda: None)
    monkeypatch.setattr(pkhex, "has_source", lambda: False)
    assert "built without it" in pkhex.status() and not pkhex.can_build()      # a packaged app made with --no-helper
    monkeypatch.setattr(pkhex, "has_source", lambda: True)
    monkeypatch.setattr(pkhex, "dotnet_sdks", lambda: [])
    assert pkhex.BUILD_COMMAND in pkhex.status() and not pkhex.can_build()
    monkeypatch.setattr(pkhex, "dotnet_sdks", lambda: ["8.0.404"])
    assert "this computer has 8.0.404" in pkhex.status() and not pkhex.can_build()
    with pytest.raises(pkhex.HelperMissing, match="needs the .NET 10 SDK"):
        monkeypatch.setattr(pkhex.shutil, "which", lambda _: "/usr/bin/dotnet")
        pkhex.build_helper()
    monkeypatch.setattr(pkhex, "dotnet_sdks", lambda: ["8.0.404", "10.0.114"])
    assert pkhex.can_build()
    done = subprocess.CompletedProcess([], 1, "x.cs(3,4): error CS1002: ; expected\nBuild FAILED.\n", "")
    assert pkhex.build_problem(done) == "x.cs(3,4): error CS1002: ; expected"
    assert pkhex.runtime_id().split("-")[0] in ("win", "osx", "linux")
