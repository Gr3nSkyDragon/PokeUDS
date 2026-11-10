"""Settings: the packaged app's own Data folder, the first start's copy of older data, and saves that never fail a
click."""

import json
import os

import pytest

from pokeuds.app import settings as s


@pytest.fixture
def portable(tmp_path, monkeypatch):
    """A packaged Windows app in tmp_path/app, with an older version's data in tmp_path/old."""
    app, old = tmp_path / "app", tmp_path / "old"
    app.mkdir()
    old.mkdir()
    monkeypatch.delenv("POKEUDS_DATA", raising=False)
    monkeypatch.setattr(s, "app_folder", lambda: app)
    monkeypatch.setattr(s, "system_data_dir", lambda: old)
    s._portable.clear()
    yield app, old
    s._portable.clear()


def test_the_packaged_app_keeps_its_data_in_its_folder(portable):
    app, old = portable
    (old / "settings.json").write_text(json.dumps({"trainer_name": "RED", "last_game": "gsc", "output": s.OLD_OUTPUT}))
    (old / "sprites" / "normal").mkdir(parents=True)
    (old / "sprites" / "normal" / "25.png").write_bytes(b"png")
    (old / "plugin-data" / "x").mkdir(parents=True)
    (old / "bank").mkdir()
    (old / "bank" / "a.pk6").write_bytes(b"not copied here")
    assert s.data_dir() == app / "Data"
    loaded = s.Settings.load()
    assert loaded.trainer_name == "RED" and loaded.last_game == "gsc"
    assert loaded.output == str(app / "Data" / "received")             # the old default moves in with it
    assert (app / "Data" / "sprites" / "normal" / "25.png").read_bytes() == b"png"
    assert (app / "Data" / "plugin-data" / "x").is_dir() and not (app / "Data" / "bank").exists()
    loaded.trainer_name = "BLUE"
    assert loaded.save() and json.loads((app / "Data" / "settings.json").read_text())["trainer_name"] == "BLUE"
    assert json.loads((old / "settings.json").read_text())["trainer_name"] == "RED"      # the old folder is left alone
    assert s.Settings().output == str(app / "Data" / "received")
    assert not list((app / "Data").glob("*.tmp"))


def test_a_received_folder_the_player_chose_is_kept(portable):
    app, old = portable
    (old / "settings.json").write_text(json.dumps({"output": "D:/My Pokemon"}))
    assert s.Settings.load().output == "D:/My Pokemon"


def test_an_unwritable_app_folder_uses_the_platform_folder(portable, monkeypatch):
    app, old = portable
    (app / "Data").write_text("a file where the folder would be")          # cannot be made
    assert s.data_dir() == old and s.Settings().output == s.OLD_OUTPUT


def test_from_source_nothing_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path / "d"))
    s._portable.clear()
    assert s.app_folder() is None and s.data_dir() == tmp_path / "d"


def test_a_save_outlasts_a_file_held_for_a_moment(tmp_path, monkeypatch):
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    first = tmp_path / f"settings.{os.getpid()}.0.tmp"
    first.mkdir()                                     # the first temporary name cannot be written
    settings = s.Settings(trainer_name="GOLD")
    assert settings.save() and s.Settings.load().trainer_name == "GOLD"
    monkeypatch.setattr(s.Path, "replace", lambda self, target: (_ for _ in ()).throw(PermissionError(13, "held")))
    monkeypatch.setattr(s.Path, "write_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError(13, "held")))
    monkeypatch.setattr(s.time, "sleep", lambda seconds: None)
    settings.trainer_name = "SILVER"
    assert settings.save() is False                   # given up, never raised
