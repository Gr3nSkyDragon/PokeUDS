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
