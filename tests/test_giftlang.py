"""The language of a Mystery Gift card (pokeuds.app.giftlang): stated, else read from its title."""

import pytest

from pokeuds import pkhex
from pokeuds.app import giftlang


@pytest.mark.parametrize("title,language", [
    ("Get Oak's Letter!", 2), ("Unlock the Secret!", 2), ("Battle in Orlando", 2),
    ("La Lettre Chen!", 3), ("La Carte Membre !", 3), ("Voici votre Pass Liberté!", 3),
    ("Eichs Brief ist eingetroffen!", 5), ("Hol dir den Gartenpass!", 5),
    ("Ecco la Lettera Oak!", 4), ("Il Liberticket in regalo!", 4),
    ("¡La Carta del Profesor Oak!", 7), ("¡Consigue el Ticket Libertad!", 7),
    ("オーキドのてがみ　プレゼント", 1), ("비밀의열쇠 선물", 8), ("阿罗拉精灵", 9),
    ("Zekrom!", 0), ("", 0),
])
def test_titles(title, language):
    assert giftlang.from_title(title) == language


def test_the_archive_then_the_card_then_the_title():
    table = giftlang._table()
    assert table, "pokeuds/app/data/event_languages.json is missing"
    key, filed = next(iter(table.items()))
    gen, fingerprint = key.split(":")
    card = {"fingerprint": fingerprint, "card_language": 5, "title": "Get Oak's Letter!"}
    want = frozenset(giftlang._CODES[c] for c in filed.split("-"))
    assert giftlang.of_card(card, int(gen)) == want                        # the archive wins
    assert giftlang.of_card({"card_language": 3, "title": "Get Oak's Letter!"}, 6) == {3}
    assert giftlang.of_card({"card_language": 0, "title": "Get Oak's Letter!"}, 6) == {2}
    assert giftlang.of_card({"card_language": 0, "title": "Zekrom!"}, 6) == frozenset()


def test_universal_and_several_languages():
    every = giftlang.EVERY[6]
    assert giftlang.universal(frozenset(), 6) and giftlang.universal(every, 6)
    assert not giftlang.universal(frozenset({2, 5}), 6)
    assert giftlang.describe(frozenset(), 6) == "Universal" and giftlang.describe(frozenset({5, 2}), 6) == "English, German"
    english, unknown = frozenset({2}), frozenset()
    assert giftlang.matches(english, 6, set()) and giftlang.matches(unknown, 6, set())          # none chosen: all
    assert giftlang.matches(english, 6, {2, 3}) and not giftlang.matches(english, 6, {3})
    assert not giftlang.matches(unknown, 6, {2})                           # a card nothing places is not English
    assert giftlang.matches(unknown, 6, {giftlang.UNIVERSAL}) and giftlang.matches(every, 6, {2})


@pytest.mark.skipif(pkhex.helper_path() is None, reason="the PKHeX helper is not built")
def test_titles_read_as_the_languages_cards_state():
    """Against every Gen 5 card (all state their language): right far more often than not, and rarely wrong."""
    helper = pkhex.Helper()
    try:
        cards = [c for game in ("bw", "b2w2") for c in helper.gifts(game, all=True) if c.get("card_language")]
    finally:
        helper.close()
    guessed = [(c["card_language"], giftlang.from_title(c["title"])) for c in cards]
    right = sum(1 for stated, read in guessed if read == stated)
    wrong = sum(1 for stated, read in guessed if read and read != stated)
    assert right / len(guessed) > 0.9 and wrong / len(guessed) < 0.05, (right, wrong, len(guessed))


@pytest.mark.skipif(pkhex.helper_path() is None, reason="the PKHeX helper is not built")
def test_the_archive_places_the_oras_cards_seen_under_english():
    """ORAS 1508-1510 went out once per language; each copy is placed under its own (they showed under English
    before, read from nothing but their titles)."""
    helper = pkhex.Helper()
    try:
        cards = [c for c in helper.gifts("oras") if c["card_id"] in (1508, 1509, 1510)]
    finally:
        helper.close()
    placed = [giftlang.of_card(c, 6) for c in cards]
    assert len(cards) == 21 and all(len(p) == 1 for p in placed)
    assert sum(1 for p in placed if p == {2}) == 3                         # one English card of each
