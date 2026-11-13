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
    assert gb == {"level": 20, "trainer": {**tr.request(1), "language": 2}, "moves": [84]}      # Gen 1 has no nature, ability, ball, item
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
    trade.control()                                     # the "PKHeX is missing" notice inside the Trade page
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
    """The Pokemon box is a pick box you can type in: leaving it picks what was typed (the first match), and its own
    pick rebuilds the builder, which must not fail on a box no longer on the page."""
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
    monkeypatch.setattr(panel, "_helper_ready", lambda: True)
    # The lists before the builder opens: opened first, it asks the real helper for them, and a warm helper's answer
    # (721 species) could land over these.
    trade.builder.lists["xy"] = {"species": [[25, "Pikachu"], [658, "Greninja"]], "moves": [[84, "Thunder Shock"]],
                                 "items": [[1, "Master Ball"]], "balls": [[4, "Poke Ball"]], "natures": [[3, "Adamant"]]}
    trade.toggle_builder()

    def box(control, label, seen=None):
        seen = seen if seen is not None else set()
        if id(control) in seen or not dataclasses.is_dataclass(control) or isinstance(control, type):
            return None
        seen.add(id(control))
        if isinstance(control, flet.Dropdown) and control.label == label:
            return control
        for f in dataclasses.fields(control):
            value = getattr(control, f.name, None)
            for child in value if isinstance(value, (list, tuple)) else [value]:
                if (found := box(child, label, seen)) is not None:
                    return found
        return None

    field = box(trade.control(), "Pokemon")
    assert field is not None and field.editable and field.enable_filter and len(field.options) == 2
    field.on_text_change(SimpleNamespace(control=field, data="gren"))
    field.on_blur(SimpleNamespace(control=field))       # not on a page, as after the rebuild
    assert trade.builder.wish.species == 658 and field.value == "658"
    field.on_text_change(SimpleNamespace(control=field, data="zzz"))
    field.on_blur(SimpleNamespace(control=field))
    assert trade.builder.wish.species == 658             # nothing matches: it keeps what it had
    moves = [box(trade.control(), f"Move {i}") for i in (1, 2, 3, 4)]
    assert all(m is not None and m.options[0].text == "None" for m in moves)


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


# -- import paste ---------------------------------------------------------------------------------

SET = {"usable": True, "species": 26, "species_name": "Raichu", "form": 1, "form_name": "Alola",
       "nickname": "Sparky", "level": 50, "shiny": True, "gender": "male", "nature": 10, "nature_name": "Timid",
       "ability": "1", "ability_name": "Surge Surfer", "held_item": 803, "held_item_name": "Aloraichium Z",
       "moves": [94, 85, 411, 417], "move_names": ["Psychic", "Thunderbolt", "Focus Blast", "Nasty Plot"],
       "errors": [], "notes": ["The set's IVs and EVs are not set yet: PKHeX picks legal ones."], "text": "Sparky..."}


class FakePaste:
    def __init__(self, sets):
        self.sets, self.asked = sets, []

    def paste(self, game, text):
        self.asked.append((game, text))
        return self.sets


def test_a_pasted_set_fills_a_wish():
    helper = FakePaste([SET, {"usable": False, "species": 778, "species_name": "Mimikyu", "level": 100,
                              "errors": ["Mimikyu is not in this game."], "notes": [], "moves": []}])
    raichu, mimikyu = builder.paste("sm", "the text", helper=helper)
    assert helper.asked == [("sm", "the text")]
    assert raichu.usable and not mimikyu.usable and mimikyu.errors == ["Mimikyu is not in this game."]
    assert raichu.title() == "Sparky (Raichu-Alola) @ Aloraichium Z"
    assert raichu.summary() == "Lv 50, shiny, male, Timid, Surge Surfer, Psychic / Thunderbolt / Focus Blast / Nasty Plot"
    wish = raichu.apply(Wish("sm", 25, 30, ball=4))
    assert (wish.species, wish.form, wish.level, wish.shiny, wish.gender, wish.nature, wish.ability) == \
        (26, 1, 50, True, "male", 10, "1")
    assert wish.held_item == 803 and wish.moves == [94, 85, 411, 417] and wish.nickname == "Sparky"
    assert wish.ball == 4                                   # a Showdown set names no ball: the wish's stays
    request = wish.request(Trainer())
    assert request["form"] == 1 and request["held_item"] == 803 and "form" not in Wish("sm", 26).request(Trainer())
    bare = builder.Pasted.from_reply({"usable": True, "species": 128, "species_name": "Tauros"}).apply(Wish("rby"))
    assert (bare.species, bare.level, bare.moves, bare.nature, bare.ability) == (128, 100, [], None, "")
    with pytest.raises(ValueError, match="unknown game"):
        builder.paste("swsh", "x", helper=helper)


def test_import_paste_beside_build_one_headless(tmp_path, monkeypatch):
    """Import paste is the Trade page's own button; using a set closes it and opens the builder, filled."""
    pytest.importorskip("flet")
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    from gui.app import App
    from gui.views import builder as panel
    from gui.views.trade import TradeView
    from pokeuds.app.settings import Settings

    app = App(None, Settings(output=str(tmp_path / "received"), last_game="sm"))
    app.ui = lambda fn: fn()                                # the read's thread hands back at once
    monkeypatch.setattr(panel, "_helper_ready", lambda: True)
    monkeypatch.setattr(panel.threading, "Thread", lambda target, **k: type("T", (), {"start": lambda self: target()})())
    monkeypatch.setattr(panel.builder, "paste", lambda game, text: [builder.Pasted.from_reply(SET)])
    trade = TradeView(app)
    b = trade.builder
    b.lists["sm"] = {"species": [[25, "Pikachu"], [26, "Raichu"]], "moves": [[85, "Thunderbolt"]],
                     "items": [[803, "Aloraichium Z"]], "balls": [[4, "Poke Ball"]], "natures": [[10, "Timid"]]}
    trade.toggle_builder()
    trade.toggle_paste()                                    # one panel at a time
    assert trade.pasting and not trade.building
    trade.control()
    b._paste_changed(type("E", (), {"control": type("C", (), {"value": "Raichu-Alola @ Aloraichium Z"})()})())
    b._read_paste(None)
    assert not b.reading and [p.species for p in b.pasted] == [26]
    trade.control()                                         # the found set, with its note and Use this one
    b._use_paste(0)
    assert not trade.pasting and trade.building             # the builder opens with the set in it
    assert b.pasted is None and b.wish.species == 26 and b.wish.form == 1
    assert b.form_name == "Alola" and "Filled in Sparky (Raichu-Alola)" in b.message
    trade.control()
    b._pick_species(25)                                     # another species: the pasted form goes
    assert b.wish.form == 0 and b.form_name == ""
    monkeypatch.setattr(panel, "_helper_ready", lambda: False)
    trade.toggle_paste()
    trade.control()                                         # without PKHeX the paste says so, as the builder does


def test_build_and_save_in_bank_does_not_queue(tmp_path, monkeypatch):
    pytest.importorskip("flet")
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    from gui.app import App
    from gui.views import builder as panel
    from gui.views.trade import TradeView
    from pokeuds.app import bank
    from pokeuds.app.settings import Settings
    from pokeuds.radio.demo import sample_mon

    app = App(None, Settings(output=str(tmp_path / "received"), last_game="rby"))
    app.ui = lambda fn: fn()
    monkeypatch.setattr(panel, "_helper_ready", lambda: True)
    monkeypatch.setattr(panel.threading, "Thread", lambda target, **k: type("T", (), {"start": lambda self: target()})())
    record = sample_mon(1, 25, 12, "PIKA").to_file()
    monkeypatch.setattr(panel.builder, "build", lambda wish, trainer, **kw: pkhex.Built(True, "Legal", "Static", "pk1", record, {}))
    trade = TradeView(app)
    trade.toggle_builder()
    b = trade.builder
    b.lists["rby"] = {"species": [[25, "Pikachu"]], "moves": [], "items": [], "balls": [], "natures": []}
    trade.control()
    b._build(None, offer=False)
    assert not b.building and trade.building                # the builder stays open for the next one
    assert "saved it in the bank" in b.message and not trade.queue
    (entry,) = bank.Bank().entries()
    assert entry.source == "built" and entry.species == "Pikachu" and entry.legal is True
    b._build(None)                                          # Build and offer it: the same Pokemon, now queued
    assert not trade.building and len(trade.queue) == 1 and "Pikachu" in trade.offer.summary


# -- IVs, EVs, DVs and Hidden Power ---------------------------------------------------------------

def test_hidden_power_types_and_spreads():
    for kind, name in enumerate(builder.HIDDEN_POWER):
        ivs = builder.with_hidden_power(None, kind, 7)
        assert builder.hidden_power(ivs, 7) == kind, name
        assert all(v in (30, 31) for v in ivs)                         # only lowest bits move
    assert builder.with_hidden_power(None, builder.HIDDEN_POWER.index("Ice"), 6) == [31, 30, 30, 31, 31, 31]
    assert builder.with_hidden_power(None, builder.HIDDEN_POWER.index("Fire"), 6) == [31, 30, 31, 30, 31, 30]
    assert builder.hidden_power([31] * 6, 6) == builder.HIDDEN_POWER.index("Dark")
    assert builder.hidden_power([31, -1, 31, 31, 31, 31], 6) is None and builder.hidden_power(None, 6) is None
    zero_atk = builder.with_hidden_power([31, 0, 31, 31, 31, 31], builder.HIDDEN_POWER.index("Ice"), 7)
    assert zero_atk[1] == 0 and builder.hidden_power(zero_atk, 7) == builder.HIDDEN_POWER.index("Ice")
    # Gen 2: the Attack and Defense DVs' two lowest bits; Gen 1 has none.
    for kind in range(16):
        dvs = builder.with_hidden_power([-1, 14, 9, 12, 12, 7], kind, 2)
        assert builder.hidden_power(dvs, 2) == kind and dvs[1] >> 2 == 14 >> 2 and dvs[3] == dvs[4] == 12
    assert builder.hidden_power([-1, 15, 15, 15, 15, 15], 1) is None
    assert builder.gb_hp_dv([-1, 15, 15, 15, 15, 15]) == 15 and builder.gb_hp_dv([-1, 14, 15, 15, 15, 15]) == 7


def test_stat_wishes_are_checked_and_sent():
    tr = Trainer()
    six = Wish("xy", 25, ivs=[31, 0, 31, 31, 31, 31], evs=[0, 0, 4, 252, 0, 252]).request(tr)
    assert six["ivs"] == [31, 0, 31, 31, 31, 31] and six["evs"] == [0, 0, 4, 252, 0, 252]
    assert "ivs" not in Wish("xy", 25, ivs=[-1] * 6, evs=[0] * 6).request(tr)        # all open: nothing sent
    assert Wish("xy", 25, evs=[252, 252, 252, 0, 0, 0]).problems() == ["EVs total at most 510"]
    assert Wish("xy", 25, ivs=[32, 0, 0, 0, 0, 0]).problems() == ["an IV is 0 to 31"]
    assert Wish("gsc", 143, ivs=[-1, 16, 0, 0, 0, 0]).problems() == ["a DV is 0 to 15"]
    gb = Wish("gsc", 143, ivs=[9, 14, 13, 12, 3, 11], evs=[1, 2, 3, 4, 99, 5]).request(tr)
    assert gb["ivs"] == [-1, 14, 13, 12, 12, 11] and gb["evs"] == [1, 2, 3, 4, 4, 5]   # one Special, HP DV derived
    assert "all five" in Wish("rby", 25, evs=[1000, 0, 0, 0, 0, 0]).problems()[0]
    assert Wish("rby", 25, evs=[5] * 6).problems() == []
    assert "shiny Gen 2" in Wish("gsc", 143, shiny=True, ivs=[-1, 15, 15, 15, 15, 15]).problems()[0]
    assert Wish("gsc", 143, shiny=True, ivs=[-1, 14, 10, 10, 10, 10]).problems() == []
    pasted = builder.Pasted.from_reply({**SET, "ivs": [31, 0, 31, 30, 31, 30], "evs": [4, 0, 0, 252, 0, 252],
                                        "hidden_power": 8})
    wish = pasted.apply(Wish("sm"))
    assert wish.ivs == [31, 0, 31, 30, 31, 30] and wish.evs == [4, 0, 0, 252, 0, 252]
    assert pasted.summary().endswith("Hidden Power Fire")


def test_the_stats_section_headless(tmp_path, monkeypatch):
    pytest.importorskip("flet")
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    from gui.app import App
    from gui.views import builder as panel
    from pokeuds.app.settings import Settings

    def typed(value):
        return type("E", (), {"control": type("C", (), {"value": value})()})()

    app = App(None, Settings(output=str(tmp_path / "received")))
    monkeypatch.setattr(panel, "_helper_ready", lambda: True)
    b = panel.BuilderPanel(app, "xy", lambda entry, text: None)
    for game in ("xy", "gsc"):
        b.lists[game] = {"species": [[25, "Pikachu"], [143, "Snorlax"]], "moves": [[85, "Thunderbolt"]],
                         "items": [[234, "Leftovers"]], "balls": [[4, "Poke Ball"]], "natures": [[3, "Adamant"]]}
    b.control()
    b._toggle_stats(None)
    b.control()
    b._set_iv(1, typed("0"))
    b._set_ev(3, typed("252"))
    assert b.wish.ivs == [-1, 0, -1, -1, -1, -1] and b.wish.evs == [0, 0, 0, 252, 0, 0]
    b._hidden_power(typed(str(builder.HIDDEN_POWER.index("Ice"))))
    assert b.wish.ivs[1] == 0 and builder.hidden_power(b.wish.ivs, 6) == builder.HIDDEN_POWER.index("Ice")
    b.control()
    b._set_iv(1, typed(""))                                 # emptied: back to the encounter's roll
    assert b.wish.ivs[1] == -1
    b._clear_stats(None)
    assert b.wish.ivs is None and b.wish.evs is None
    b._best_ivs(None)
    assert b.wish.ivs == [31] * 6
    b.set_game("gsc")                                       # IVs do not carry over to DVs
    assert b.wish.ivs is None
    b.control()
    b._set_iv(3, typed("12"))                               # Special: SpA and SpD together
    assert b.wish.ivs == [-1, -1, -1, 12, 12, -1]
    b._hidden_power(typed(str(builder.HIDDEN_POWER.index("Ice"))))
    assert builder.hidden_power(b.wish.ivs, 2) == builder.HIDDEN_POWER.index("Ice")
    b.control()


def test_a_closest_build_waits_for_the_player(tmp_path, monkeypatch):
    """Nothing legal is exactly what was asked: the builder shows the closest and what changed, keeps nothing until
    Keep this one, then takes its values into the fields. Repairs alone keep at once and are said."""
    pytest.importorskip("flet")
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    from gui.app import App
    from gui.views import builder as panel
    from gui.views.trade import TradeView
    from pokeuds.app import bank
    from pokeuds.app.settings import Settings
    from pokeuds.radio.demo import sample_mon

    app = App(None, Settings(output=str(tmp_path / "received"), last_game="rby"))
    app.ui = lambda fn: fn()
    monkeypatch.setattr(panel, "_helper_ready", lambda: True)
    monkeypatch.setattr(panel.threading, "Thread", lambda target, **k: type("T", (), {"start": lambda self: target()})())
    record = sample_mon(1, 25, 36, "PIKA").to_file()
    asked = []
    summary = {"level": 36, "move_ids": [85, 0, 0, 0], "shiny": False}
    closest = pkhex.Built(True, "Legal!", "Static", "pk1", record, summary, ["Traded once to a partner and back."],
                          ["Level 1 became 36.", "Left out Spore: no legal Pikachu knows it with the rest."])
    monkeypatch.setattr(panel.builder, "build", lambda wish, trainer, **kw: asked.append(kw) or closest)
    trade = TradeView(app)
    trade.toggle_builder()
    b = trade.builder
    b.lists["rby"] = {"species": [[25, "Pikachu"]], "moves": [], "items": [], "balls": [], "natures": []}
    b.wish.level, b.wish.moves = 1, [147, 85]
    trade.control()
    b._build(None)
    assert asked == [{"fix": True}] and b.closest is closest and not bank.Bank().entries() and not trade.queue
    assert "closest legal one" in b.message and "Level 1 became 36." in b.message and "Traded once" in b.message
    trade.control()                                         # Keep this one / Back to editing drawn
    b._drop_closest()
    assert b.closest is None and b.message == "" and b.wish.level == 1
    b._build(None)
    b._keep_closest()
    assert b.wish.level == 36 and b.wish.moves == [85] and len(trade.queue) == 1 and len(bank.Bank().entries()) == 1

    repaired = pkhex.Built(True, "Legal!", "Static", "pk1", record, summary, ["Traded once to a partner and back."], [])
    monkeypatch.setattr(panel.builder, "build", lambda wish, trainer, **kw: repaired)
    trade.toggle_builder()
    b._build(None, offer=False)
    assert b.closest is None and "saved it in the bank" in b.message and "PKHeX also: Traded once" in b.message


def test_the_language_a_pokemon_comes_from():
    """Each generation's languages; the trainer request carries PKHeX's LanguageID; a Gen 1/2 Japanese or Korean
    game's names are checked before PKHeX is asked; a paste keeps the language chosen."""
    assert builder.languages_for(1) == ["en", "jp", "fr", "it", "de", "es"]
    assert "kr" in builder.languages_for(2) and "zh-hans" not in builder.languages_for(6) and "zh-hant" in builder.languages_for(7)
    wish = builder.Wish(game="rby", species=25, level=20, language="kr")
    assert any("Korean" in p for p in wish.problems())                       # no Korean Gen 1 game
    wish = builder.Wish(game="gsc", species=25, level=20, language="jp")
    assert wish.request(builder.Trainer("サトシ"))["trainer"]["language"] == 1 and wish.problems() == []
    assert wish.name_problems(builder.Trainer("サトシ")) == []
    assert "use kana" in wish.name_problems(builder.Trainer("ASH"))[0]
    wish.language = "kr"
    assert wish.name_problems(builder.Trainer("골드")) == []
    assert any("starts in Hangul" in p for p in wish.name_problems(builder.Trainer("ASH")))
    assert builder.Wish(game="xy", species=25, language="jp").name_problems(builder.Trainer("ASH")) == []   # Gen 6 writes both
    with pytest.raises(ValueError, match="use kana"):
        builder.build(builder.Wish(game="gsc", species=25, level=20, language="jp"), builder.Trainer("ASH"), helper=object())
    pasted = builder.Pasted.from_reply({"species": 25, "species_name": "Pikachu", "level": 50, "usable": True})
    assert pasted.apply(builder.Wish(game="xy", language="de")).language == "de"


def test_a_pick_box_takes_what_is_typed():
    from gui.views.builder import pick

    choices = [("0", "None"), ("6", "Charizard"), ("4", "Charmander"), ("5", "Charmeleon"), ("122", "Mr. Mime")]
    assert pick("charizard", choices) == "6" and pick("charm", choices) == "4"      # exact, then the first that starts
    assert pick("mrmime", choices) == "122" and pick("Mr. M", choices) == "122"   # spaces and dots ignored
    assert pick("izard", choices) == "6" and pick("", choices) is None and pick("zzz", choices) is None


def test_the_builder_follows_the_title_pokeuds_stands_in_for(tmp_path, monkeypatch):
    """German Crystal: the builder opens in German; a language the player picks stays; the 3DS hosting gives English."""
    pytest.importorskip("flet")
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    from gui.app import App
    from gui.views.trade import TradeView
    from pokeuds.app.settings import Settings

    trade = TradeView(App(None, Settings(output=str(tmp_path / "received"), last_game="gsc")))
    trade.pick_game("gsc")
    assert trade.builder.wish.language == "en"
    trade.role = "host"
    trade._host_title_changed(type("E", (), {"control": type("C", (), {"value": "crystal-de"})()})())
    assert trade.builder.wish.language == "de"
    trade._host_title_changed(type("E", (), {"control": type("C", (), {"value": "gold-kr"})()})())
    assert trade.builder.wish.language == "kr"
    trade.builder._language(type("E", (), {"control": type("C", (), {"value": "fr"})()})())     # the player's choice
    trade._host_title_changed(type("E", (), {"control": type("C", (), {"value": "silver-it"})()})())
    assert trade.builder.wish.language == "fr"
    trade.pick_game("rby")                                  # another game: the console's language leads again
    assert trade.builder.wish.language == "en"
