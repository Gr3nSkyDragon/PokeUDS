"""The app layer (settings, catalog, progress, the managed child) and the GUI's pages, built headless."""

import threading

import pytest

from pokeuds.app import catalog
from pokeuds.app.progress import Progress
from pokeuds.app.runner import Run
from pokeuds.app.settings import Settings
from pokeuds.radio.demo import sample_mon


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture
def offers(tmp_path):
    pk1 = tmp_path / "charmy.pk1"
    pk1.write_bytes(sample_mon(1, 4, 10, "CHARMY").to_file())
    pk2 = tmp_path / "cynda.pk2"
    pk2.write_bytes(sample_mon(2, 155, 10, "CYNDA").to_file())
    return pk1, pk2


def test_settings_round_trip(data):
    s = Settings(keys="k.txt", radio="ldnd", port=r"\\.\pipe\x", debug=True, offers={"rby": "a.pk1"})
    s.save()
    back = Settings.load()
    assert back == s and back.radio_spec() == r"ldnd:\\.\pipe\x"
    assert Settings().keys_problem() == "No aes_keys.txt chosen"


def test_catalog_commands_and_modes(data, offers):
    pk1, pk2 = offers
    s = Settings(keys="/keys.txt", output="/out", trainer_name="ASH")
    rby, gsc = catalog.BY_KEY["rby"], catalog.BY_KEY["gsc"]
    assert catalog.command(rby, s, offer=str(pk1), role="join", trades=2) == [
        "vc", "trade", "--radio", "esp32", "--offer", str(pk1), "--output", "/out", "--role", "join", "--name", "ASH",
        "--keys", "/keys.txt", "--partner", "gen1", "--trades", "2"]
    assert catalog.command(gsc, s, offer=str(pk2), role="host", host_title="silver")[-2:] == ["--as", "silver"]
    assert catalog.command(catalog.BY_KEY["xy"], s, offer="a.pk6", role="join")[:2] == ["trade", "xy"]
    assert "--keys" not in catalog.command(rby, Settings(radio="demo", port="red", keys="/k"), offer="a", role="join")
    assert catalog.trade_mode(rby, catalog.describe_offer(rby, str(pk2))).startswith("Time Capsule")
    assert catalog.trade_mode(gsc, catalog.describe_offer(gsc, str(pk2))).startswith("Gen 2")


def test_what_stops_a_start(data, offers, tmp_path):
    pk1, pk2 = offers
    rby = catalog.BY_KEY["rby"]
    demo = Settings(radio="demo", port="red")
    assert catalog.missing_for_start(rby, demo, catalog.describe_offer(rby, str(pk1))) == []
    assert catalog.missing_for_start(rby, demo, None) == ["Choose a Pokemon file to offer"]
    assert any("Console keys" in p for p in catalog.missing_for_start(rby, Settings(), catalog.describe_offer(rby, str(pk1))))
    totodile = tmp_path / "toto.pk2"
    totodile.write_bytes(sample_mon(2, 158, 5, "TOTO").to_file())
    assert "Time Capsule" in catalog.describe_offer(rby, str(totodile)).problem          # Gen 2 only species
    xy = catalog.BY_KEY["xy"]
    gaps = catalog.missing_for_start(xy, demo, catalog.Offer("a.pk6", "x"))
    assert any("profiles/xy.toml" in g for g in gaps)
    assert catalog.describe_offer(rby, str(tmp_path / "missing.pk1")).problem


def test_progress_from_events():
    p = Progress()
    p.start()
    for event in [{"event": "radio", "description": "demo"}, {"event": "searching", "game": "vc"},
                  {"event": "joined"}, {"event": "session", "state": "joined"}, {"event": "phase", "phase": "select/pick"},
                  {"event": "note", "text": "round: exchanging rn, party, patch"},
                  {"event": "note", "text": "partner offers slot 0: Eevee"},
                  {"event": "received", "trade": 1, "path": "/x.pk1", "summary": "Eevee"}]:
        p.take(event)
    assert all(p.steps.values()) and p.headline == "Trade 1 complete"
    assert p.notes == ["The 3DS offers Eevee"]
    p.take({"event": "stopped", "reason": "session-ended"})
    assert p.state == "done"
    q = Progress()
    q.start()
    q.take({"event": "error", "message": "RuntimeError: no ESP32-S3 found"})
    q.exited(1)
    assert q.state == "error" and "ESP32" in q.detail


def test_managed_run_stops_when_asked(data, offers, tmp_path):
    pk1, _ = offers
    events, done = [], threading.Event()
    codes = []
    run = Run(["vc", "trade", "--radio", "demo:red", "--role", "host", "--as", "red", "--offer", str(pk1),
               "--output", str(tmp_path / "out"), "--seconds", "60"],
              on_line=lambda line: None, on_event=events.append, on_exit=lambda c: (codes.append(c), done.set()))
    deadline = threading.Event()
    for _ in range(100):                         # wait until it is hosting (nobody will join)
        if any(e["event"] == "hosting" for e in events):
            break
        deadline.wait(0.1)
    assert any(e["event"] == "hosting" for e in events)
    run.stop()
    assert done.wait(10)
    assert codes == [130] and events[-1] == {"event": "stopped", "reason": "user"}


def test_gui_pages_build(data, offers):
    pytest.importorskip("flet")
    from gui.app import App
    from gui.views.debug import DebugView
    from gui.views.radio import RadioView
    from gui.views.bank import BankView
    from gui.views.board import BoardView
    from gui.views.settings import SettingsView, name_problem
    from gui.views.trade import TradeView

    pk1, pk2 = offers
    settings = Settings(radio="demo", port="red", debug=True, output=str(pk1.parent), offers={"rby": str(pk1)})
    app = App(None, settings)
    trade = TradeView(app)
    assert trade.offer and "Charmander" in trade.offer.summary and trade.problems() == []
    for cls in (TradeView, BankView, RadioView, BoardView, SettingsView, DebugView):
        cls(app).control()
    app.views = {"trade": trade}
    bank_view = BankView(app)
    bank_view.control()                              # deposits what the received folder holds
    entries = bank_view.bank.entries()
    assert sorted(e.extension for e in entries) == ["pk1", "pk2"]
    bank_view.selected = entries[0].id
    bank_view.control()                              # with the detail panel
    pk2_entry = next(e for e in entries if e.extension == "pk2")
    bank_view._offer(pk2_entry, "gsc")
    assert settings.offers["gsc"] == str(pk2_entry.path) and trade.entry.key == "gsc" and trade.offer
    trade.pick_game("rby")
    settings.offers.pop("usum", None)
    trade.pick_game("usum")
    assert trade.offer is None and settings.last_game == "usum"
    assert name_problem("POKEUDS") == "" and name_problem("TOOLONGNAME") and name_problem("")
