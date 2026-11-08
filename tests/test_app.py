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
    assert catalog.missing_for_start(rby, demo, None) == ["Choose a Pokemon to offer: from the bank, Build one, or a file"]
    assert any("Console keys" in p for p in catalog.missing_for_start(rby, Settings(), catalog.describe_offer(rby, str(pk1))))
    totodile = tmp_path / "toto.pk2"
    totodile.write_bytes(sample_mon(2, 158, 5, "TOTO").to_file())
    assert "Time Capsule" in catalog.describe_offer(rby, str(totodile)).problem          # Gen 2 only species
    assert catalog.missing_for_start(catalog.BY_KEY["usum"], demo, catalog.Offer("a.pk7", "x")) == []
    assert catalog.missing_for_start(catalog.BY_KEY["sm"], demo, catalog.Offer("a.pk7", "x")) == []
    assert catalog.missing_for_start(catalog.BY_KEY["xy"], demo, catalog.Offer("a.pk6", "x")) == []
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
    from gui.views.bank import BankView
    from gui.views.board import BoardView
    from gui.views.settings import SettingsView, name_problem
    from gui.views.trade import TradeView

    pk1, pk2 = offers
    settings = Settings(radio="demo", port="red", debug=True, output=str(pk1.parent), offers={"rby": str(pk1)})
    app = App(None, settings)
    trade = TradeView(app)
    assert trade.offer and "Charmander" in trade.offer.summary and trade.problems() == []
    for cls in (TradeView, BankView, BoardView, SettingsView, DebugView):
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


def test_the_packaged_app_runs_trades_through_its_own_exe(monkeypatch):
    """PokeUDS.exe has no ``python -m pokeuds``: a trade child is the exe with --cli (packaging/pokeuds_app.py)."""
    from pokeuds.app import runner

    assert runner.command("scan")[1:] == ["-u", "-m", "pokeuds", "scan"]
    monkeypatch.setattr(runner.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runner.sys, "executable", r"C:\PokeUDS\PokeUDS.exe")
    assert runner.command("trade", "sm") == [r"C:\PokeUDS\PokeUDS.exe", "--cli", "trade", "sm"]


def test_no_handler_drops_a_coroutine():
    """Flet awaits a handler only when it is async itself: a lambda calling an async method makes the coroutine and
    drops it, and the button does nothing (the bank's Export did). Such handlers go through ``common.awaiting``."""
    import ast
    from pathlib import Path

    found = []
    for path in sorted((Path(__file__).resolve().parents[1] / "gui").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        asyncs = {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
        for node in ast.walk(tree):
            call = node.body if isinstance(node, ast.Lambda) else None
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in asyncs
                    and isinstance(call.func.value, ast.Name) and call.func.value.id == "self"):
                found.append(f"{path.name}:{node.lineno} self.{call.func.attr}")
    assert found == []


def test_gifts_page_sends_gen6_cards_over_local_wireless(data, tmp_path, monkeypatch):
    """A Gen 6 card gets Start sending (`gift distribute`); a Gen 7 card says it goes by trade."""
    pytest.importorskip("flet")
    from types import SimpleNamespace

    from gui.app import App
    from gui.views.gifts import GiftsView

    keys = tmp_path / "aes_keys.txt"
    keys.write_text("slot0x2DKeyN=" + "00" * 16 + "\n")
    app = App(None, Settings(radio="esp32", port="COM9", keys=str(keys), output=str(tmp_path / "received")))
    started = []
    monkeypatch.setattr(app, "start", lambda kind, args, **kw: started.append((kind, args)))
    view = GiftsView(app)
    full = bytes(784)
    view.game = "xy"
    view.gift = SimpleNamespace(card={"title": "Item", "card_id": 1, "games": ["X", "Y"], "kind": "item", "item": "Rare Candy",
                                      "quantity": 1}, pokemon=None, extension="wc6", custom=False, full_data=full,
                                card_data=bytes(264))
    view.control()
    assert view.generation == 6 and any(getattr(c, 'value', None) == 'Send over local wireless' for c in view._wireless())
    view._distribute()
    kind, args = started[0]
    assert kind == "gift" and args[:3] == ["gift", "distribute", "xy"]
    assert args[args.index("--radio") + 1] == "esp32:COM9" and args[args.index("--keys") + 1] == str(keys)
    from pathlib import Path
    assert Path(args[3]).read_bytes() == full
    view.game = "sm"
    view.control()                                       # the Gen 7 note, no Start button
    assert view.generation == 7


def test_send_over_local_wireless_sits_between_legal_and_the_bank(data, tmp_path):
    pytest.importorskip("flet")
    from types import SimpleNamespace

    import flet as ft

    from gui.app import App
    from gui.views.gifts import GiftsView

    app = App(None, Settings(radio="esp32", output=str(tmp_path / "received")))
    view = GiftsView(app)
    view.game = "xy"
    summary = {"species": 25, "species_name": "Pikachu", "level": 10, "shiny": False, "ot_name": "ASH", "tid": 1,
               "moves": ["Thunder Shock"]}
    view.gift = SimpleNamespace(card={"title": "Pikachu", "card_id": 2, "games": ["X"], "kind": "pokemon"},
                                pokemon=SimpleNamespace(summary=summary, legal=True, report=""), extension="wc6",
                                custom=False, full_data=bytes(784), card_data=bytes(264))
    texts: list[str] = []

    def walk(c):
        for attr in ("value", "text", "content"):
            v = getattr(c, attr, None)
            if isinstance(v, str):
                texts.append(v)
        for attr in ("content", "controls"):
            v = getattr(c, attr, None)
            for child in (v if isinstance(v, list) else [v] if isinstance(v, ft.Control) else []):
                walk(child)
    walk(view._result())
    order = [texts.index(x) for x in ("Legal", "Send over local wireless", "Start sending", "Keep its Pokemon in the bank")]
    assert order == sorted(order), texts
