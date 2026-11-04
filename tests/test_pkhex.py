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
