"""The PKHeX helper end to end. Skipped unless it is built (dotnet build -c Release services/pkhex)."""

import pytest

from pokeuds import gbpk, pkhex, pkx

pytestmark = pytest.mark.skipif(pkhex.helper_path() is None, reason="the PKHeX helper is not built")


@pytest.fixture(scope="module")
def helper():
    h = pkhex.Helper()
    yield h
    h.close()


def test_ping_and_lists(helper):
    assert helper.ping().startswith("26.")
    rby = helper.lists("rby")
    assert len(rby["species"]) == 151 and not rby["natures"]
    xy = helper.lists("xy")
    assert len(xy["species"]) == 721 and len(xy["natures"]) == 25 and xy["balls"]


@pytest.mark.parametrize("game,species,level", [("xy", 25, 30), ("oras", 383, 45), ("sm", 785, 60), ("usum", 25, 20),
                                                ("rby", 6, 36), ("gsc", 157, 40)])
def test_builds_are_legal_and_load_in_pokeuds(helper, game, species, level):
    built = helper.build(game, species, level=level, trainer={"ot": "ASH", "tid": 1234, "sid": 4321})
    assert built.legal, built.report
    assert built.summary["species"] == species and built.summary["level"] == level
    if game in ("rby", "gsc"):
        record = gbpk.Record.from_file(built.data)
        assert record.dex == species and record.level == level and gbpk.decode_name(record.ot_name) == "ASH"
    else:
        record = pkx.decrypt(built.data) if not pkx.checksum_valid(built.data) else built.data
        assert pkx.checksum_valid(record) and pkx.summary(record).species == species


def test_wishes(helper):
    built = helper.build("xy", 25, level=30, shiny=True, nature=3, nickname="Sparky")
    assert built.legal and built.summary["shiny"] and built.summary["nature"] == "Adamant"
    assert built.summary["nickname"] == "Sparky"
    with pytest.raises(ValueError):
        helper.build("xy", 722)                        # Rowlet is not in X/Y


def test_check_reads_both_generations(helper):
    built = helper.build("gsc", 155, level=5)
    again = helper.check("gsc", built.data)
    assert again.legal and again.summary["species_name"] == "Cyndaquil"


def plain(data: bytes) -> bytes:
    return data if pkx.checksum_valid(data) else pkx.decrypt(data)


@pytest.mark.parametrize("game,species,level,vc,origin", [("rby", 6, 36, "red", "Red"), ("rby", 151, 50, "blue", None), ("rby", 25, 30, "yellow", "Yellow"),
                                                          ("gsc", 157, 40, "silver", "Silver"), ("gsc", 251, 30, "gold", None)])
def test_virtual_console_pokemon_migrate_as_transporter_moves_them(helper, game, species, level, vc, origin):
    built = helper.build(game, species, level=level, trainer={"ot": "ASH", "tid": 1234})
    moved = helper.migrate("sm", built.data, game, vc_game=vc, trainer={"ot": "PLAYER", "tid": 5, "sid": 6})
    s = moved.summary
    assert moved.legal, moved.report
    assert moved.extension == "pk7" and s["species"] == species and s["level"] == level and s["ot_name"] == built.summary["ot_name"]
    assert s["ball"].startswith("Pok") and s["ivs"].count(31) >= (5 if species in (151, 251) else 3)
    assert "Virtual Console" in moved.encounter
    assert origin is None or s["origin"] == origin              # a Pokemon with Crystal's met data is always Crystal's
    assert pkx.summary(plain(moved.data)).species == species


def test_gen6_pokemon_migrate_as_bank_moves_them(helper):
    for game, species, level in (("xy", 25, 40), ("oras", 383, 45)):
        built = helper.build(game, species, level=level, trainer={"ot": "ASH", "tid": 1234, "sid": 4321})
        for trainer in ({"ot": "ASH", "tid": 1234, "sid": 4321}, {"ot": "PLAYER"}):    # its own OT, or another
            moved = helper.migrate("usum", built.data, game, trainer=trainer)
            assert moved.legal, moved.report
            assert moved.extension == "pk7" and moved.summary["species"] == species


@pytest.mark.parametrize("language,byte", [("en", 2), ("fr", 3), ("it", 4), ("de", 5), ("es", 7)])
def test_a_vc_pokemon_keeps_its_games_language(helper, language, byte):
    """The language of the VC title it came from (pokeuds/games/vc.py), legal with the console region that suits it."""
    built = helper.build("rby", 25, level=20, nickname="SPARKY", trainer={"ot": "ASH", "tid": 1234})
    moved = helper.migrate("sm", built.data, "rby", vc_game="red", trainer={"ot": "PLAYER", "tid": 5, "sid": 6},
                           language=language)
    assert moved.legal, moved.report
    assert plain(moved.data)[0xE3] == byte                      # PK7's language


@pytest.mark.parametrize("game,language,ot", [("rby", "jp", "サトシ"), ("gsc", "jp", "サトシ"), ("gsc", "kr", "골드")])
def test_japanese_and_korean_records_are_read_checked_and_migrated(helper, game, language, ot):
    """PokeUDS's Japanese (59/63-byte) and Korean records, as PKHeX reads them: names and all, legal, and still
    legal in Gen 7 with their language. (A Korean record whose OT is in Latin letters is not: PKHeX finds a Korean
    game's OT must use its own characters.)"""
    from pokeuds import gbpk
    from pokeuds.vc import gbtext

    english = gbpk.Record.from_file(helper.build(game, 25, level=20, trainer={"ot": "ASH", "tid": 1234}).data)
    moved = english.in_language("en", language)
    record = gbpk.Record(moved.generation, moved.data, gbtext.encode(ot, language), moved.nickname, moved.family)
    checked = helper.check(game, record.to_file())
    assert checked.legal, checked.report
    assert checked.summary["nickname"] == {"jp": "ピカチュウ", "kr": "피카츄"}[language] and checked.summary["ot_name"] == ot
    vc = "red" if game == "rby" else "gold"
    migrated = helper.migrate("sm", record.to_file(), game, vc_game=vc, trainer={"ot": "PLAYER", "tid": 5, "sid": 6},
                              language=language)
    assert migrated.legal, migrated.report
    assert plain(migrated.data)[0xE3] == {"jp": 1, "kr": 8}[language] and migrated.summary["ot_name"] == ot


def test_what_cannot_migrate(helper):
    gold = helper.build("gsc", 152, level=5)
    with pytest.raises(ValueError):
        helper.migrate("xy", gold.data, "gsc")                     # nothing official goes to Gen 6
    with pytest.raises(ValueError):
        helper.migrate("sm", gold.data, "gsc", vc_game="red")      # Red is not a Gen 2 game
    pikachu = bytearray(plain(helper.build("oras", 25, level=20).data))
    cosplay = bytearray(pikachu)
    cosplay[0x1D] = (cosplay[0x1D] & 7) | (1 << 3)                 # Cosplay Pikachu, Rock Star: Bank keeps it in Gen 6
    with pytest.raises(ValueError, match="Cosplay"):
        helper.migrate("sm", pkx.fix_checksum(bytes(cosplay)), "oras")
    egg = bytearray(pikachu)
    egg[0x77] |= 0x40                                              # the IV word's egg bit
    with pytest.raises(ValueError, match="Egg"):
        helper.migrate("sm", pkx.fix_checksum(bytes(egg)), "oras")


PASTE = """=== [gen7] Team ===

Sparky (Raichu-Alola) (M) @ Aloraichium Z
Ability: Surge Surfer
Level: 50
Shiny: Yes
EVs: 252 SpA / 4 SpD / 252 Spe
Timid Nature
- Psychic
- Thunderbolt
- Focus Blast
- Nasty Plot

Mimikyu @ Life Orb
Ability: Disguise
Jolly Nature
- Swords Dance
- Play Rough
- Shadow Claw
- Notamove
"""


def test_paste_reads_sets_for_each_game(helper):
    from pokeuds.app import builder

    raichu, mimikyu = builder.paste("sm", PASTE, helper=helper)
    assert raichu.usable and not raichu.errors and (raichu.species, raichu.form, raichu.form_name) == (26, 1, "Alola")
    assert raichu.fields["nature"] == 10 and raichu.fields["ability"] == "1" and raichu.fields["shiny"]
    assert raichu.names["held_item_name"] == "Aloraichium Z" and len(raichu.fields["moves"]) == 4
    assert raichu.fields["evs"] == [0, 0, 0, 252, 4, 252] and raichu.fields["ivs"] == [31] * 6 and not raichu.notes
    assert mimikyu.usable and mimikyu.errors == ["Move not recognized: Notamove"] and len(mimikyu.fields["moves"]) == 3
    # X/Y has no Alolan form, no Surge Surfer, no Z-Crystal and no Mimikyu: each is said, none is silently dropped.
    raichu6, mimikyu6 = builder.paste("xy", PASTE, helper=helper)
    assert raichu6.usable and raichu6.form == 0 and "held_item" not in raichu6.fields
    assert {"Raichu-Alola is not in this game.", "Raichu cannot have Surge Surfer in this game.",
            "Aloraichium Z is not in this game."} <= set(raichu6.errors)
    assert not mimikyu6.usable and "Mimikyu is not in this game." in mimikyu6.errors
    # Gen 2 numbers its items apart; natures and abilities are notes there.
    (snorlax,) = builder.paste("gsc", "Snorlax @ Leftovers\nAbility: Thick Fat\nAdamant Nature\n- Rest\n- Curse",
                               helper=helper)
    assert snorlax.names["held_item_name"] == "Leftovers" and "nature" not in snorlax.fields
    assert any("Natures start" in n for n in snorlax.notes) and any("Abilities start" in n for n in snorlax.notes)
    assert builder.paste("xy", "\n\n=== Team ===\n\n", helper=helper) == []


def test_a_pasted_alolan_form_builds_legal(helper):
    from pokeuds.app import builder

    raichu = builder.paste("sm", PASTE, helper=helper)[0]
    built = builder.build(raichu.apply(builder.Wish("sm")), builder.Trainer(), helper=helper)
    assert built.legal, built.report
    s = built.summary
    assert (s["species"], s["form"], s["level"], s["shiny"]) == (26, 1, 50, True)
    assert s["ability"] == "Surge Surfer" and s["nature"] == "Timid" and s["held_item"] == "Aloraichium Z"
    (snorlax,) = builder.paste("gsc", "Snorlax @ Leftovers\n- Rest", helper=helper)
    gen2 = builder.build(snorlax.apply(builder.Wish("gsc")), builder.Trainer(), helper=helper)
    assert gen2.legal and gen2.summary["held_item"] == "Leftovers"      # named from Gen 2's own item list


def test_builds_with_ivs_evs_and_dvs(helper):
    tr = {"ot": "ASH", "tid": 1234, "sid": 4321}
    six = helper.build("xy", 25, level=50, trainer=tr, ivs=[31, 0, 31, 31, 31, 31], evs=[0, 0, 4, 252, 0, 252])
    assert six.legal and six.summary["ivs"] == [31, 0, 31, 31, 31, 31] and six.summary["evs"] == [0, 0, 4, 252, 0, 252]
    assert six.summary["hidden_power"] == "Dragon"
    gb = helper.build("gsc", 143, level=50, trainer=tr, ivs=[-1, 15, 14, 13, 13, 12], evs=[1000] * 6)
    assert gb.legal and gb.summary["ivs"][1:] == [15, 14, 13, 13, 12] and gb.summary["evs"] == [1000] * 6
    with pytest.raises(ValueError, match="all five stats"):            # stat experience in one stat only
        helper.build("rby", 25, level=50, trainer=tr, evs=[1000, 0, 0, 0, 0, 0])
    with pytest.raises(ValueError, match="510"):
        helper.build("xy", 25, level=50, trainer=tr, evs=[252, 252, 252, 0, 0, 0])


def test_a_pasted_spread_builds_legal(helper):
    from pokeuds.app import builder

    (magnezone,) = builder.paste("sm", "Magnezone @ Choice Specs\nAbility: Magnet Pull\nLevel: 50\n"
                                       "EVs: 4 HP / 252 SpA / 252 Spe\nModest Nature\nIVs: 0 Atk\n- Thunderbolt\n"
                                       "- Hidden Power [Fire]", helper=helper)
    assert magnezone.fields["ivs"] == [31, 0, 31, 30, 31, 30] and magnezone.fields["evs"] == [4, 0, 0, 252, 0, 252]
    built = builder.build(magnezone.apply(builder.Wish("sm")), builder.Trainer(), helper=helper)
    assert built.legal and built.summary["hidden_power"] == "Fire" and built.summary["ivs"] == [31, 0, 31, 30, 31, 30]
    # Gen 2: named DVs (moved to Hidden Power Ice), the stat experience Showdown means (unnamed ones at the most).
    (snorlax,) = builder.paste("gsc", "Snorlax @ Leftovers\nIVs: 26 Atk / 30 Def\nEVs: 252 HP / 100 Atk\n- Rest\n"
                                      "- Hidden Power [Ice]", helper=helper)
    assert snorlax.fields["evs"] == [63504, 10000, 65535, 65535, 65535, 65535]
    gen2 = builder.build(snorlax.apply(builder.Wish("gsc")), builder.Trainer(), helper=helper)
    assert gen2.legal and gen2.summary["hidden_power"] == "Ice"
    (plain,) = builder.paste("gsc", "Snorlax\n- Rest", helper=helper)    # no IVs line: the encounter rolls the DVs
    assert "ivs" not in plain.fields


AROMATISSE = """Aromatisse @ Lum Berry
Ability: Healer
EVs: 252 HP / 252 Def / 4 SpA
Relaxed Nature
IVs: 0 Atk / 0 Spe
- Odor Sleuth
- Dazzling Gleam
- Moonblast
- Trick Room"""


def test_a_trade_evolution_is_given_its_trade(helper):
    """Aromatisse evolves only by trade: built from a wild Spritzee, it is traded to a partner and back, and is legal
    with every wish of the paste, still the player's."""
    from pokeuds.app import builder

    (pasted,) = builder.paste("xy", AROMATISSE, helper)
    built = builder.build(pasted.apply(builder.Wish(game="xy")), builder.Trainer("ASH", 1234, 4321), helper)
    s = built.summary
    assert built.legal and built.changes == [] and any("traded" in f.lower() for f in built.fixes)
    assert s["level"] == 100 and s["moves"] == ["Odor Sleuth", "Dazzling Gleam", "Moonblast", "Trick Room"]
    assert s["ability"] == "Healer" and s["nature"] == "Relaxed" and s["ivs"] == [31, 0, 31, 31, 31, 0]
    assert s["ot_name"] == "ASH"
    pk = plain(built.data)
    assert pk[0x93] == 0                             # PK6's current handler: back with its original trainer
    machamp = helper.build("oras", 68, trainer={"ot": "ASH", "tid": 1234, "sid": 4321})
    assert machamp.legal and machamp.summary["level"] == 28 and "Wild" in machamp.encounter      # traded, and Machoke's level
    assert len(machamp.fixes) == 2


def test_an_egg_move_gets_its_relearn_move(helper):
    built = helper.build("xy", 25, level=30, moves=[344, 85], trainer={"ot": "ASH", "tid": 1234, "sid": 4321})
    assert built.legal and "Volt Tackle" in built.summary["moves"] and any("relearnable" in f for f in built.fixes)


def test_the_closest_build_says_what_changed(helper):
    trainer = {"ot": "ASH", "tid": 1234, "sid": 4321}
    with pytest.raises(ValueError):
        helper.build("xy", 6, level=50, moves=[147, 53], trainer=trainer)           # Spore: never, without fix
    spore = helper.build("xy", 6, level=50, moves=[147, 53], trainer=trainer, fix=True)
    assert spore.legal and spore.summary["moves"][0] == "Flamethrower" and spore.changes == [
        "Left out Spore: no legal Charizard in X/Y knows it with the rest."]
    mewtwo = helper.build("xy", 150, level=70, ivs=[0, 0, 0, 0, 0, 0], trainer=trainer, fix=True)
    assert mewtwo.legal and mewtwo.summary["ivs"][:3] == [0, 0, 0] and len(mewtwo.changes) == 1     # three fixed at 31
    zygarde = helper.build("xy", 718, level=70, shiny=True, trainer=trainer, fix=True)
    assert zygarde.legal and not zygarde.summary["shiny"] and zygarde.changes == ["It is not shiny: no legal one can be."]
    assert zygarde.summary["move_ids"][0] and zygarde.summary["ball_id"] == 4
