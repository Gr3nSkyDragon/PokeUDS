"""The Windows app's home (pokeuds/app/home.py): the folder above _internal is where it lives and keeps the player's
files, the note its launcher reads, and how an update finds and starts the app behind the launcher."""

import sys

import pytest

from pokeuds.app import home, update


def layout(folder, launcher=True):
    """A home as the release unzips it: the launcher, then the app flat in _internal."""
    (folder / "_internal").mkdir(parents=True)
    (folder / "_internal" / "PokeUDS-app.exe").write_bytes(b"app")
    if launcher:
        (folder / "PokeUDS.exe").write_bytes(b"launcher")
    return folder / "_internal" / "PokeUDS-app.exe"


def test_the_home_is_the_folder_above_internal(tmp_path):
    app = layout(tmp_path / "PokeUDS")
    assert home.install_folder(str(app)) == (tmp_path / "PokeUDS").resolve()
    assert home.install_folder(str(tmp_path / "PokeUDS" / "_INTERNAL" / "pokeuds-app.EXE")) == (tmp_path / "PokeUDS").resolve()
    # An app from before the launcher, and the launcher itself, live where they are.
    assert home.install_folder(str(tmp_path / "Old" / "PokeUDS.exe")) == (tmp_path / "Old").resolve()
    assert home.install_folder(str(tmp_path / "PokeUDS" / "PokeUDS.exe")) == (tmp_path / "PokeUDS").resolve()
    # Another program that happens to sit in some _internal folder is not the app.
    assert home.install_folder(str(tmp_path / "x" / "_internal" / "other.exe")) == (tmp_path / "x" / "_internal").resolve()


def test_the_update_root_is_the_home(tmp_path):
    app = layout(tmp_path / "PokeUDS")
    assert update.install_root(str(app), frozen=True, system="win32") == (tmp_path / "PokeUDS").resolve()
    assert update.install_root(str(app), frozen=False, system="win32") is None
    assert update.install_root(str(app), frozen=True, system="darwin") is None


@pytest.mark.skipif(sys.platform != "win32", reason="the packaged Windows app")
def test_the_app_behind_the_launcher_keeps_its_data_in_the_home(tmp_path, monkeypatch):
    from pokeuds.app import settings

    app = layout(tmp_path / "PokeUDS")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app))
    monkeypatch.delenv("POKEUDS_DATA", raising=False)
    assert settings.app_folder() == (tmp_path / "PokeUDS").resolve()
    assert home.in_home() and home.behind_launcher()
    (tmp_path / "PokeUDS" / "PokeUDS.exe").unlink()          # the launcher dragged away: still home, no shortcut target
    assert home.in_home() and not home.behind_launcher()
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Old" / "PokeUDS.exe"))
    assert not home.in_home()


def test_the_saved_home_is_written_once(tmp_path):
    note = tmp_path / "local" / "PokeUDS" / "home.txt"
    assert home.remember(tmp_path / "Pokémon UDS", note)
    assert note.read_bytes() == str(tmp_path / "Pokémon UDS").encode("utf-8") + b"\n"     # UTF-8, as the launcher reads it
    stamp = note.stat().st_mtime_ns
    assert home.remember(tmp_path / "Pokémon UDS", note) and note.stat().st_mtime_ns == stamp
    assert home.remember(tmp_path / "Elsewhere", note) and note.read_text(encoding="utf-8").strip() == str(tmp_path / "Elsewhere")
    assert not list(note.parent.glob("*.part"))


def test_the_helper_is_the_app_behind_the_launcher(tmp_path):
    new = tmp_path / "new" / "PokeUDS"
    new.mkdir(parents=True)
    (new / "PokeUDS.exe").write_bytes(b"exe")
    assert update.helper_executable(new) == new / "PokeUDS.exe"          # a release from before the launcher
    layout(new)
    assert update.helper_executable(new) == new / "_internal" / "PokeUDS-app.exe"


def test_an_old_app_folder_takes_the_launcher_layout(tmp_path):
    """The first update to the launcher: an older folder's exe and _internal go, the launcher and the flat app come,
    and the player's folders stay."""
    from tests.test_update import KEPT, app_folder, finished_pid

    root = tmp_path / "PokeUDS"
    app_folder(root)
    before = {rel: (root / rel).read_bytes() for rel in KEPT}
    new = root / "Data" / "update" / "new" / "PokeUDS"
    layout(new)
    (new / "_internal" / "python313.dll").write_bytes(b"new dll")
    (new / "LICENSE.txt").write_bytes(b"AGPL")
    opened = []
    assert update.apply(new, root, finished_pid(), "0.3.0", relaunch=opened.append)
    assert opened == [root]
    assert (root / "PokeUDS.exe").read_bytes() == b"launcher"
    assert (root / "_internal" / "PokeUDS-app.exe").read_bytes() == b"app"
    assert not (root / "_internal" / "python.dll").exists()                # the old _internal went whole
    assert {rel: (root / rel).read_bytes() for rel in KEPT} == before


def test_no_shortcut_offer_outside_the_packaged_app(tmp_path):
    pytest.importorskip("flet")
    from gui import shortcut
    from gui.app import App
    from pokeuds.app.settings import Settings

    app = App(None, Settings(output=str(tmp_path / "received")))
    assert app.settings.shortcut_banner and not shortcut.available()
    assert shortcut.banner(app).visible is False                          # from source: nothing to make a shortcut to
