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
    s = Settings(keys="k.txt", gen7_key="rsa_d_key.txt", radio="ldnd", port=r"\\.\pipe\x", debug=True,
                 offers={"rby": "a.pk1"})
    s.save()
    back = Settings.load()
    assert back == s and back.radio_spec() == r"ldnd:\\.\pipe\x"
    assert Settings().keys_problem() == "No aes_keys.txt chosen"
    assert "does not exist" in Settings(gen7_key="missing.rsa").gen7_key_problem()


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


def test_gifts_page_sends_gen6_and_gen7_cards_over_local_wireless(data, tmp_path, monkeypatch):
    """A Gen 6 or Gen 7 card gets Start sending (`gift distribute`, a .wc6full/.wc7full)."""
    pytest.importorskip("flet")
    from types import SimpleNamespace

    from gui.app import App
    from gui.views.gifts import GiftsView

    keys = tmp_path / "aes_keys.txt"
    keys.write_text("slot0x2DKeyN=" + "00" * 16 + "\n")
    gen7_key = tmp_path / "rsa_d_key.txt"
    gen7_key.write_text("d=1\n")
    app = App(None, Settings(radio="esp32", port="COM9", keys=str(keys), gen7_key=str(gen7_key),
                             output=str(tmp_path / "received")))
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
    view.control()                                       # Gen 7 gets Start sending too
    assert view.generation == 7
    assert any(getattr(c, 'value', None) == 'Send over local wireless' for c in view._wireless())
    view._distribute()
    kind, sm_args = started[1]
    assert kind == "gift" and sm_args[:3] == ["gift", "distribute", "sm"]
    assert sm_args[3].endswith("sending.wc7full")
    assert sm_args[sm_args.index("--gen7-key") + 1] == str(gen7_key)


def test_gifts_page_sends_ds_cards_over_ds_local_wireless(data, tmp_path, monkeypatch):
    """The fifth game, DS: a Gen 4/5 card gets Start sending (`gift distribute <ds game>`, its .myg/.bin), with no
    console keys and no bank; Build your own stays open (custom DS cards)."""
    pytest.importorskip("flet")
    from pathlib import Path
    from types import SimpleNamespace

    from gui.app import App
    from gui.views.gifts import GAMES, GiftsView

    assert list(GAMES)[4] == "ds"
    app = App(None, Settings(radio="esp32", port="COM9", output=str(tmp_path / "received")))   # no keys file
    started = []
    monkeypatch.setattr(app, "start", lambda kind, args, **kw: started.append((kind, args)))
    view = GiftsView(app)
    view.game, view.ds_game, view.mode = "ds", "bw", "custom"
    wifi = bytes(range(256)) * 2 + bytes(208)
    view.gift = SimpleNamespace(card={"title": "Darkrai", "card_id": 28, "games": ["Black", "White"], "kind": "item",
                                      "item": "Liberty Pass", "quantity": 1}, pokemon=None, extension="pgf",
                                custom=False, full_data=b"", card_data=bytes(204), wifi_data=wifi)
    view.control()
    assert view.mode == "custom" and view.generation == 5 and view.helper_game == "bw"
    rows = view._wireless()
    assert not any(getattr(c, "value", None) == app.settings.keys_problem() for c in rows if app.settings.keys_problem())
    view.ds_language = "FRE"
    view._distribute()
    kind, args = started[0]
    assert kind == "gift" and args[:3] == ["gift", "distribute", "bw"]
    assert args[args.index("--language") + 1] == "FRE" and "--keys" not in args
    assert args[3].endswith("sending.bin") and Path(args[3]).read_bytes() == wifi
    view.ds_game = "hgss"
    assert view.generation == 4 and view.helper_game == "hgss"


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


def test_each_run_leaves_its_log(tmp_path, monkeypatch):
    """A run's log is written to <data>/logs when it ends (beside the packaged app: Data/logs), the newest 30 kept."""
    pytest.importorskip("flet")
    import time as clock

    from gui.app import LOGS_KEPT, save_log

    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    path = save_log("trade", ["trade", "xy", "--role", "host"], ["$ pokeuds trade xy", "(exited with code 1)"])
    assert path.parent == tmp_path / "logs" and path.name.endswith("_trade_trade-xy.txt")
    assert path.read_text(encoding="utf-8") == "$ pokeuds trade xy\n(exited with code 1)\n"
    for i in range(LOGS_KEPT + 5):
        save_log("scan", ["scan"], [str(i)])
        clock.sleep(0.002)
    assert len(list((tmp_path / "logs").glob("*.txt"))) == LOGS_KEPT


def test_a_runs_log_is_on_disk_while_it_runs_and_keeps_every_line(tmp_path, monkeypatch):
    """The log file fills as the run goes (a run that never exits, or an app closed midway, still leaves it), holds
    more than the 2000 lines the page keeps, and Copy log copies all of it."""
    pytest.importorskip("flet")
    from gui.app import App, RunLog
    from gui.views.common import full_log

    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    log = RunLog("trade", ["trade", "xy"])
    log.write("$ pokeuds trade xy")
    log.write("PSS beacon from Refresh")
    assert log.path.read_text(encoding="utf-8") == "$ pokeuds trade xy\nPSS beacon from Refresh\n"   # not closed yet
    for i in range(2500):
        log.write(f"line {i}")
    log.close()
    app = App(None)
    app.saved_log, app.log = log.path, [f"line {i}" for i in range(500, 2500)]
    text = full_log(app)
    assert text.startswith("$ pokeuds trade xy\nPSS beacon from Refresh\n") and text.endswith("line 2499\n")
    app.saved_log = None
    assert full_log(app).startswith("line 500\n")


def test_an_online_trade_offers_no_queue_and_needs_pkhex(data, monkeypatch):
    from pokeuds import pkhex

    xy, rby = catalog.BY_KEY["xy"], catalog.BY_KEY["rby"]
    online = [k for k, e in catalog.BY_KEY.items() if catalog.online_ready(e)]
    assert sorted(online) == ["gsc", "oras", "rby", "sm", "usum", "xy"]
    s = Settings(radio="demo", port="x", output="/out", trainer_name="ASH", online_code=" desk ")
    run = catalog.command(xy, s, offer=["a.pk6"], role="join", online=True)
    assert "--offer" not in run and run[-3:] == ["--online", "--online-code", "desk"]
    s.online_code = ""
    assert catalog.command(xy, s, offer=["a.pk6"], role="join", online=True)[-1] == "--online"
    assert "--online" not in catalog.command(xy, s, offer=["a.pk6"], role="join")
    monkeypatch.setattr(pkhex, "helper_path", lambda: "pkhex.exe")
    assert catalog.missing_for_start(xy, s, None, online=True) == []
    monkeypatch.setattr(pkhex, "helper_path", lambda: None)
    assert ["PKHeX" in p for p in catalog.missing_for_start(xy, s, None, online=True)] == [True]
    assert catalog.missing_for_start(rby, s, None, online=True) == []        # the VC checks without PKHeX
    vc = catalog.command(rby, s, offer=[""], role="join", online=True)
    assert vc[:2] == ["vc", "trade"] and "--offer" not in vc and "--online" in vc
    assert catalog.missing_for_start(rby, s, None) == ["Choose a Pokemon to offer: from the bank, Build one, or a file"]


def test_progress_follows_the_online_partner():
    p = Progress()
    p.start()
    for event in [{"event": "online", "state": "searching", "text": "waiting for a partner"},
                  {"event": "online", "state": "paired", "text": "paired with Misty", "partner": "Misty"},
                  {"event": "online", "state": "paired", "text": "Misty offers Staryu", "partner": "Misty",
                   "offer": "Staryu level 20 [species 120]"}]:
        p.take(event)
    assert (p.online, p.partner, p.partner_offer) == ("paired", "Misty", "Staryu level 20 [species 120]")
    p.take({"event": "online", "state": "paired", "text": "Misty took back their offer", "offer": ""})
    assert p.partner_offer == "" and p.notes[-1] == "Misty took back their offer"
    p.take({"event": "online", "state": "lost", "text": "lost the partner"})
    assert p.online == "lost" and p.notes[-1] == "Lost the partner"


def test_the_trade_page_trades_online(data, monkeypatch):
    pytest.importorskip("flet")
    from gui.app import App
    from gui.views.trade import TradeView
    from pokeuds import pkhex

    monkeypatch.setattr(pkhex, "helper_path", lambda: "pkhex.exe")
    settings = Settings(radio="demo", port="x", last_game="xy", online=True, online_code="desk")
    app = App(None, settings)
    trade = TradeView(app)
    assert trade.online and trade.problems() == []
    trade.control()
    assert trade.command()[-3:] == ["--online", "--online-code", "desk"] and "--offer" not in trade.command()
    app.progress.take({"event": "online", "state": "paired", "text": "paired", "partner": "Misty", "offer": "Staryu"})
    trade.control()
    trade.pick_game("rby")                       # the VC trades online too
    assert trade.online and trade.command()[:3] == ["-v", "vc", "trade"] and "--online" in trade.command()
    trade.control()
    trade._touch_changed(type("E", (), {"control": type("C", (), {"value": True})()})())
    assert settings.touch_trade and not settings.online and trade.touch          # Touch Trade turns Online off
    assert "--touch-trade" in trade.command() and "--offer" not in trade.command() and trade.problems() == []
    trade.control()
    trade._online_changed(type("E", (), {"control": type("C", (), {"value": True})()})())
    assert settings.online and not settings.touch_trade and not trade.touch      # and Online turns it off
    settings.online = False                      # Online off: the queue is back
    assert not trade.online and trade.problems() == ["Choose a Pokemon to offer: from the bank, Build one, or a file"]
    trade.control()


def test_the_card_editor_builds_ds_cards(tmp_path):
    """Build your own under Nintendo DS: Gen 4 and Gen 5 Pokemon, egg (Gen 5) and item cards, each with the fields its
    card holds; moving between a DS and a 3DS game starts the card again."""
    pytest.importorskip("flet")
    import flet as ft

    from gui.app import App
    from gui.views.gift_editor import GiftEditor
    from gui.views.gifts import GiftsView
    from pokeuds.app import giftfile

    app = App(None, Settings(radio="esp32", output=str(tmp_path / "received")))
    lists = {"species": [[25, "Pikachu"]], "moves": [[1, "Pound"]], "items": [[1, "Master Ball"]], "balls": [[16, "Cherish"]],
             "natures": [[3, "Adamant"]], "languages": [[2, "English"]], "locations": [[40001, "a lovely place"]],
             "egg_locations": []}

    def texts(control) -> list[str]:
        found: list[str] = []

        def walk(c):
            for attr in ("value", "text", "label", "content"):       # a button's label is its str content
                v = getattr(c, attr, None)
                if isinstance(v, str):
                    found.append(v)
            for attr in ("content", "controls"):
                v = getattr(c, attr, None)
                for child in (v if isinstance(v, list) else [v] if isinstance(v, ft.Control) else []):
                    walk(child)
        walk(control)
        return found

    editor = GiftEditor(app, "pt", lambda: {"ot": "DANA", "tid": 1, "sid": 2}, lambda gift: None)
    for game in ("dp", "pt", "hgss", "bw", "b2w2"):
        editor.lists[game], editor.texts[game] = lists, []
    editor.f = giftfile.new_fields("pt", "pokemon")
    shown = texts(editor.control())
    assert "Card text" in shown and "Export .pcd" in shown and "Export .myg" in shown
    assert "Nature" not in shown and "Relearn 1" not in shown and "Halo while receiving" not in shown
    assert editor.f["shiny"] == "never"
    editor._shiny("always")                                      # Gen 4 Always shiny: the PID's choices show
    shown = texts(editor.control())
    assert "Nature" in shown and "Gender" in shown and "Ability" in shown
    editor.set_game("bw")
    assert editor.gen == 5 and "date" in editor.f and editor.f["ivs"] == [-1] * 6
    shown = texts(editor.control())
    assert "Distribution text" in shown and "Nature" in shown and "Export .bin" in shown and "Origin game" in shown
    for kind in ("egg", "item"):
        editor._kind(kind)
        assert editor.control() is not None
    assert "How many" not in texts(editor.control())          # a DS item card gives one
    editor.set_game("xy")                                        # back to a 3DS card: Gen 6's fields again
    assert editor.gen == 6 and editor.f["kind"] == "item" and "text" in editor.f and "halo" in editor.f

    view = GiftsView(app)
    view.game, view.ds_game, view.mode = "ds", "hgss", "custom"
    view.editor.lists["hgss"], view.editor.texts["hgss"] = lists, []
    view.control()
    assert view.editor.game == "hgss" and view.mode == "custom"
    view.follow_editor("b2w2")
    assert (view.game, view.ds_game) == ("ds", "b2w2")
