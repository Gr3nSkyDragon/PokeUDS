"""Mystery Gift cards through the PKHeX helper: PKHeX's own official cards written as files and opened again,
full files, custom cards. Skipped unless the helper is built. (Every card, not a sample, is a slow run:
docs/mystery_gift.md, Testing.)"""

import base64

import pytest

from pokeuds import pkhex, pkx
from pokeuds.app import bank

pytestmark = pytest.mark.skipif(pkhex.helper_path() is None, reason="the PKHeX helper is not built")
TRAINER = {"ot": "DANA", "tid": 2468, "sid": 1357}


@pytest.fixture(scope="module")
def helper():
    h = pkhex.Helper()
    yield h
    h.close()


@pytest.mark.parametrize("game", ["xy", "oras", "sm", "usum"])
def test_official_cards_round_trip_and_are_legal(helper, game, tmp_path):
    cards = helper.gifts(game, all=True)
    assert len(cards) > 100
    sample = cards[::max(1, len(cards) // 40)]                 # about forty, items and eggs among them
    ext = "wc6" if game in ("xy", "oras") else "wc7"
    for card in sample:
        path = tmp_path / f"{card['card_id']}.{ext}"
        path.write_bytes(base64.b64decode(card["card_data"]))
        opened = helper.gift(game, data=path.read_bytes(), trainer=TRAINER)
        assert opened.card_data == path.read_bytes() and len(opened.full_data) == 784
        again = helper.gift(game, data=opened.full_data, trainer=TRAINER)
        assert again.card_data == opened.card_data and again.card["card_id"] == card["card_id"]
        if card["kind"] in ("pokemon", "egg"):
            assert opened.pokemon.legal, (card["title"], opened.pokemon.report)
        else:
            assert opened.pokemon is None


def test_card_142_eggs_are_legal(helper):
    egg = next(c for c in helper.gifts("xy", all=True) if c["card_id"] == 142 and c["kind"] == "egg")
    assert helper.gift("xy", data=base64.b64decode(egg["card_data"]), trainer=TRAINER).pokemon.legal


def test_shiny_locked_event_and_bank(helper, tmp_path):
    ray = next(c for c in helper.gifts("oras") if c["species_name"] == "Rayquaza" and c["shiny"] == "always")
    gift = helper.gift("oras", key=ray["key"], trainer=TRAINER)
    p = gift.pokemon
    assert p.legal and p.summary["shiny"] and p.summary["ot_name"] == ray["ot"]          # the event's OT
    entry, _ = bank.Bank(tmp_path).deposit(pkx.to_stored(p.data), "pk6", source="gift")
    assert entry.species == "Rayquaza" and entry.shiny and bank.games_for(entry) == ["xy", "oras"]


def test_your_ot_card_takes_the_trainer(helper):
    yours = next(c for c in helper.gifts("sm") if not c["ot"] and c["kind"] == "pokemon" and c["card_id"] > 0)
    p = helper.gift("sm", key=yours["key"], trainer=TRAINER).pokemon
    assert p.legal and p.summary["ot_name"] == "DANA" and p.summary["tid"] == 2468


def test_custom_cards(helper):
    c = helper.card("xy", trainer=TRAINER, species=25, level=30, shiny="always", title="PokeUDS Pikachu", card_id=2001)
    assert len(c.card_data) == 264 and len(c.full_data) == 784 and c.custom and c.extension == "wc6"
    assert c.card["title"] == "PokeUDS Pikachu" and c.card["card_id"] == 2001
    assert c.pokemon.summary["shiny"] and c.pokemon.summary["ot_name"] == "DANA" and c.pokemon.summary["moves"]
    assert c.pokemon.summary["tid"] == 2468                     # a "your OT" card: the receiver's IDs too
    theirs = helper.card("sm", trainer=TRAINER, species=25, ot="KUKUI", tid=1111)
    assert theirs.pokemon.summary["ot_name"] == "KUKUI" and theirs.pokemon.summary["tid"] == 1111
    assert not c.pokemon.legal                                  # no such event: PKHeX says so
    reopened = helper.gift("xy", data=c.card_data, trainer=TRAINER)
    assert reopened.card["title"] == "PokeUDS Pikachu" and reopened.pokemon.summary["species"] == 25
    item = helper.card("usum", kind="item", item=50, quantity=5, title="Rare Candies", versions=4)
    assert item.pokemon is None and item.card["item"] == "Rare Candy" and item.card["games"] == ["Ultra Sun"]
    assert item.full_data[0] == 4                               # RestrictVersion: Ultra Sun


def test_refusals(helper):
    with pytest.raises(ValueError, match="neither a card"):
        helper.gift("xy", data=bytes(100))
    with pytest.raises(ValueError, match="empty"):
        helper.gift("xy", data=bytes(264))
    with pytest.raises(ValueError, match="Gen 6 and 7"):
        helper.gifts("gsc")
    with pytest.raises(ValueError, match="not in this game"):
        helper.card("xy", species=722)
