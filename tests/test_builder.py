"""The Builder's wishes and trainer, and the page built headless (PKHeX itself: tests/test_pkhex.py)."""

import pytest

from pokeuds import pkhex
from pokeuds.app import builder
from pokeuds.app.builder import Trainer, Wish


def test_trainer_ids_per_generation():
    tr = Trainer("DANA", 2468, 1357, True)
    assert tr.shown_id(6) == "02468" and tr.request(1) == {"ot": "DANA", "tid": 2468, "sid": 0, "gender": 0}
    assert tr.request(6)["sid"] == 1357 and tr.request(2)["gender"] == 1
    assert tr.shown_id(7) == f"{((1357 << 16) | 2468) % 1_000_000:06d}"
    tr.set_g7(123456, 789)
    assert (tr.shown_id(7), tr.g7_sid()) == ("123456", 789)
    with pytest.raises(ValueError):
        tr.set_g7(999999, 4294)                         # past 2^32
    assert Trainer("A" * 20).request(1)["ot"] == "A" * 7
    assert Trainer.from_dict({}, "POKEUDS").ot == "POKEUDS"


def test_wish_checks_and_request():
    assert Wish("xy", 722).problems() == ["X / Y has species 1 to 721"]
    assert "no shiny" in Wish("rby", 25, shiny=True).problems()[0]
    assert "at most 10" in Wish("gsc", 25, nickname="ELEVENCHARS").problems()[0]
    tr = Trainer()
    gb = Wish("rby", 25, 20, nature=3, ability="hidden", ball=4, held_item=1, moves=[84, 0]).request(tr)
    assert gb == {"level": 20, "trainer": tr.request(1), "moves": [84]}      # Gen 1 has no nature, ability, ball, item
    six = Wish("xy", 25, 30, shiny=True, nature=0, ability="1", ball=4, held_item=234, nickname="Sparky").request(tr)
    assert six["nature"] == 0 and six["ability"] == "1" and six["ball"] == 4 and six["held_item"] == 234 and six["shiny"]


def test_trade_page_builder_headless(tmp_path, monkeypatch):
    pytest.importorskip("flet")
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    from gui.app import App
    from gui.views import builder as panel
    from gui.views.trade import TradeView
    from pokeuds.app import bank
    from pokeuds.app.settings import Settings

    app = App(None, Settings(output=str(tmp_path / "received"), last_game="gsc"))
    trade = TradeView(app)
    trade.toggle_builder()
    assert trade.building and trade.builder.wish.game == "gsc"
    monkeypatch.setattr(panel, "_helper_ready", lambda: False)
    trade.control()                                     # the "build the helper" card inside the Trade page
    monkeypatch.setattr(panel, "_helper_ready", lambda: True)
    for game in builder.GAMES:
        trade.builder.lists[game] = {"species": [[25, "Pikachu"], [1, "Bulbasaur"]], "moves": [[84, "Thunder Shock"]],
                                     "items": [[1, "Master Ball"]], "balls": [[4, "Poke Ball"]], "natures": [[3, "Adamant"]]}
        trade.pick_game(game)
        assert trade.builder.wish.game == game
        trade.control()
    # A built Pokemon becomes the offer and closes the panel.
    from pokeuds.radio.demo import sample_mon
    trade.pick_game("rby")
    entry, _ = bank.Bank().deposit(sample_mon(1, 25, 12, "PIKA").to_file(), "pk1", source="built")
    trade.building = True
    trade._built(entry, "Built it")
    assert not trade.building and trade.offer and "Pikachu" in trade.offer.summary
    assert app.settings.offers["rby"] == str(entry.path) and trade.problems() == ["Console keys: No aes_keys.txt chosen (Settings)"]
    trade.control()


def test_species_field_survives_the_rebuild_its_pick_causes(tmp_path, monkeypatch):
    """Picking a species rebuilds the builder, so the field whose blur picked it is gone by then; its blur used to
    fail with "Control must be added to the page first"."""
    flet = pytest.importorskip("flet")
    import dataclasses
    from types import SimpleNamespace

    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    from gui.app import App
    from gui.views import builder as panel
    from gui.views.trade import TradeView
    from pokeuds.app.settings import Settings

    app = App(None, Settings(output=str(tmp_path / "received"), last_game="xy"))
    trade = TradeView(app)
    trade.toggle_builder()
    monkeypatch.setattr(panel, "_helper_ready", lambda: True)
    trade.builder.lists["xy"] = {"species": [[25, "Pikachu"], [658, "Greninja"]], "moves": [[84, "Thunder Shock"]],
                                 "items": [[1, "Master Ball"]], "balls": [[4, "Poke Ball"]], "natures": [[3, "Adamant"]]}

    def species_field(control, seen=None):
        seen = seen if seen is not None else set()
        if id(control) in seen or not dataclasses.is_dataclass(control) or isinstance(control, type):
            return None
        seen.add(id(control))
        if isinstance(control, flet.TextField) and control.label == "Species":
            return control
        for f in dataclasses.fields(control):
            value = getattr(control, f.name, None)
            for child in value if isinstance(value, (list, tuple)) else [value]:
                if (found := species_field(child, seen)) is not None:
                    return found
        return None

    field = species_field(trade.control())
    assert field is not None
    field.value = "gren"
    field.on_blur(SimpleNamespace(control=field))       # not on a page, as after the rebuild
    assert trade.builder.wish.species == 658 and field.value == "Greninja" and field.error is None
    field.value = "zzz"
    field.on_blur(SimpleNamespace(control=field))
    assert field.error == "nothing matches" and trade.builder.wish.species == 658


@pytest.mark.skipif(pkhex.helper_path() is None, reason="the PKHeX helper is not built")
def test_build_and_keep(tmp_path):
    from pokeuds.app import bank

    b = bank.Bank(tmp_path)
    wish = Wish("gsc", 152, 10, shiny=True)
    entry = builder.keep(builder.build(wish, Trainer("DANA", 2468)), b, wish)
    assert entry.shiny and entry.legal and entry.source == "built" and entry.meta["built_for"] == "gsc"
    assert bank.games_for(entry) == ["rby", "gsc"]


@pytest.mark.skipif(pkhex.helper_path() is None, reason="the PKHeX helper is not built")
def test_gifts_page_builds_headless(tmp_path, monkeypatch):
    pytest.importorskip("flet")
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    from gui.app import App
    from gui.views.gifts import GiftsView
    from pokeuds.app.settings import Settings

    app = App(None, Settings(output=str(tmp_path / "received")))
    view = GiftsView(app)
    view.cards["oras"] = pkhex.shared().gifts("oras")
    view.control()
    ray = next(c for c in view.cards["oras"] if c["species_name"] == "Rayquaza" and c["shiny"] == "always")
    view.gift = pkhex.shared().gift("oras", key=ray["key"], trainer=view.trainer())
    view.control()                                   # with the result panel
    view._keep(None)
    assert view.saved and view.saved.species == "Rayquaza" and view.saved.source == "gift"
    view._customize(None)                            # the official card into the editor
    assert view.mode == "custom" and view.editor.f["species"] == 384 and view.editor.f["shiny"] == "always"
    lists = pkhex.shared().lists("oras")
    view.editor.lists["oras"], view.editor.texts["oras"] = lists, [(43, 233, ["An example"])]
    view.editor.more = True
    view.control()
    for kind in ("pokemon", "item"):
        view.editor._kind(kind)
        view.control()
    view.editor.f["ot"] = "KUKUI"                    # named OT shows its own fields
    view.control()
    gift = view.editor.make()
    assert gift.card["kind"] == "item" and len(gift.card_data) == 264
    view.mode = "file"
    view.control()
