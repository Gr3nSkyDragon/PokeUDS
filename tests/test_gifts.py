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
    with pytest.raises(ValueError, match="Gen 4 to 7"):
        helper.gifts("gsc")
    with pytest.raises(ValueError, match="neither a .pcd"):
        helper.gift("dp", data=bytes(264))
    with pytest.raises(ValueError, match="neither a .pgf"):
        helper.gift("bw", data=bytes(264))
    with pytest.raises(ValueError, match="not in this game"):
        helper.card("xy", species=722)


@pytest.mark.parametrize("game", ["xy", "oras", "sm", "usum"])
def test_official_cards_rebuild_from_their_fields(helper, game):
    """The editor's schema holds a whole card: fields out, card back, byte for byte (all of them: scripts/check_gifts.py)."""
    cards = [c for c in helper.gifts(game, all=True) if c["card_id"] > 0]
    for card in cards[::max(1, len(cards) // 60)]:
        raw = base64.b64decode(card["card_data"])
        fields = helper.card_fields(game, data=raw)
        assert helper.card(game, **fields).card_data == raw, (card["card_id"], card["title"])


def test_a_fully_custom_pokemon(helper):
    from pokeuds.app import giftfile

    f = giftfile.new_fields("usum")
    f.update(species=778, level=50, met_level=50, shiny="always", nature=3, gender=1, ability="1", ball=1,
             held_item=234, additional_item=50, moves=[389, 421, 104, 0], relearn=[0, 0, 0, 0],
             ivs=[31, 31, 31, -1, 31, 31], evs=[252, 252, 0, 0, 0, 4], nickname="Mimi",
             title="A shiny Mimikyu for you!", ribbons=["classic", "wishing"], contest=[10, 0, 0, 0, 0, 0])
    gift = helper.card("usum", trainer=TRAINER, **f)
    s = gift.pokemon.summary
    assert (s["species_name"], s["level"], s["nature"], s["ability"], s["ball"], s["held_item"]) == \
        ("Mimikyu", 50, "Adamant", "Disguise", "Master Ball", "Leftovers")
    assert s["moves"] == ["Sucker Punch", "Shadow Claw", "Double Team"] and s["shiny"] and s["gender"] == "female"
    assert s["ivs"][:3] == [31, 31, 31] and s["evs"] == [252, 252, 0, 0, 0, 4] and s["nickname"] == "Mimi"
    assert s["stats"][0] > 0 and len(s["stats"]) == 6                   # what the game will show
    back = helper.card_fields("usum", data=gift.card_data)
    for key in ("title", "nature", "ivs", "evs", "additional_item", "ball", "held_item", "moves", "contest"):
        assert back[key] == f[key], key
    assert set(back["ribbons"]) == {"classic", "wishing"} and back["met_location"] == giftfile.LOVELY_PLACE


def test_perfect_ivs_and_kinds(helper):
    gift = helper.card("xy", trainer=TRAINER, species=25, level=30, perfect_ivs=3)
    assert gift.card_data[0xAF] == 0xFE and gift.card_data[0xB0] == 0xFF       # the marker in the HP byte alone
    assert sum(1 for v in gift.pokemon.summary["ivs"] if v == 31) >= 3
    assert helper.card("sm", kind="bp", quantity=50).card_data[0x51] == 3        # Gen 7: 3 Battle Points
    assert helper.card("sm", kind="beans", bean=1, quantity=20).card_data[0x51] == 2   # 2 Poke Beans
    six = helper.card("usum", kind="item", items=[{"item": 50, "quantity": 5}, {"item": 1, "quantity": 2}])
    assert helper.card_fields("usum", data=six.card_data)["items"] == [{"item": 50, "quantity": 5}, {"item": 1, "quantity": 2}]
    with pytest.raises(ValueError, match="one item"):
        helper.card("xy", kind="item", items=[{"item": 50, "quantity": 1}, {"item": 1, "quantity": 1}])
    with pytest.raises(ValueError, match="510"):
        helper.card("xy", species=25, evs=[252, 252, 252, 0, 0, 0])


def test_gift_project_files(helper, tmp_path):
    from pokeuds.app import giftfile

    f = giftfile.new_fields("oras")
    f.update(species=384, level=70, shiny="always", title="Our Rayquaza")
    card = helper.card("oras", trainer=TRAINER, **f).card_data
    path = giftfile.save(tmp_path / "ray", giftfile.GiftProject("oras", f, "Rayquaza", card))
    assert path.suffix == ".udsgift"
    back = giftfile.load(path)
    assert back.game == "oras" and back.fields == f and back.card == card and not back.problems
    assert helper.card("oras", trainer=TRAINER, **back.fields).card_data[:0x4C] == card[:0x4C]
    body = path.read_text().replace('"version": 1', '"version": 9')
    (tmp_path / "new.udsgift").write_text(body)
    with pytest.raises(ValueError, match="newer PokeUDS"):
        giftfile.load(tmp_path / "new.udsgift")
    (tmp_path / "x.udsgift").write_text("{}")
    with pytest.raises(ValueError, match="not a PokeUDS gift file"):
        giftfile.load(tmp_path / "x.udsgift")
    texts = giftfile.card_texts(helper.gifts("usum", all=True))
    assert texts[0][0] == 44 and texts[0][1] > 100 and any(i == 56 for i, *_ in texts)


def test_egg_cards(helper):
    from pokeuds.app import giftfile

    # Official event eggs are legal, and stay so when opened in the editor and rebuilt.
    for game in ("oras", "sm"):
        egg = next(c for c in helper.gifts(game, all=True) if c["kind"] == "egg" and c["card_id"] > 0)
        fields = giftfile.unreceived(helper.card_fields(game, data=base64.b64decode(egg["card_data"])))
        assert fields["is_egg"] and fields["level"] == 1 and fields["egg_location"] in (40001, 40010)
        rebuilt = helper.card(game, trainer=TRAINER, **fields)
        assert rebuilt.card["kind"] == "egg" and rebuilt.pokemon.legal, rebuilt.pokemon.report
    # A custom egg: an Egg at level 1, from a Pokemon Center, met nowhere yet; flagged only for being custom.
    for game in ("xy", "usum"):
        f = giftfile.new_fields(game, "egg")
        f["species"] = 25 if game == "xy" else 778
        gift = helper.card(game, trainer=TRAINER, **f)
        p = gift.pokemon
        assert gift.card["kind"] == "egg" and p.summary["nickname"] == "Egg" and p.summary["level"] == 1
        assert p.summary["ball"] == ("Poké Ball" if game == "xy" else "Cherish Ball")
        problems = [line for line in p.report.splitlines() if line.startswith("Invalid")]
        assert problems and not any("Met Date" in line for line in problems)
        assert any("event Egg" in line for line in problems)              # no such event: PKHeX says so
    short = helper.card("oras", trainer=TRAINER, kind="egg", species=371)       # the helper's own egg kind
    assert short.card["kind"] == "egg" and short.card_data[0xA6] == 0 and short.card_data[0xA4] == 0x4A   # 40010


def full_crc(full: bytes) -> int:
    """CRC-16/CCITT-FALSE over the full file with its checksum field (0x202) zero."""
    crc = 0xFFFF
    for b in full[:0x202] + b"\0\0" + full[0x204:]:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def distribution_text(full: bytes) -> str:
    return full[4:0x1FE].decode("utf-16-le").split("\0")[0]


def test_full_files_carry_a_whole_distribution_header(helper):
    six = helper.card("xy", trainer=TRAINER, species=25, level=30, title="PokeUDS Pikachu")
    full = six.full_data
    assert int.from_bytes(full[0x202:0x204], "little") == full_crc(full)
    assert distribution_text(full) == "Pikachu\nwill be given to you as a gift!"
    assert full[0] == 15 and full[0x205:0x208] == b"FFF" and full[0x208:] == six.card_data
    seven = helper.card("sm", trainer=TRAINER, species=25, level=30, distribution_text="Hello\nfrom PokeUDS", halo=True)
    full = seven.full_data
    assert int.from_bytes(full[0x202:0x204], "little") == full_crc(full)
    assert distribution_text(full) == "Hello\nfrom PokeUDS" and full[0x1FE] == 1 and full[0x205:0x208] == b"\0FF"
    assert helper.gift("sm", data=full, trainer=TRAINER).full_data == full        # opened again: the header stays
    fields = helper.card_fields("sm", data=full)
    assert (fields["distribution_text"], fields["halo"]) == ("Hello\nfrom PokeUDS", True)
    with pytest.raises(ValueError, match="at most 252"):
        helper.card("sm", species=25, distribution_text="x" * 253)


def test_official_cards_keep_their_own_full_file(helper):
    """A card PKHeX has a distributor's full file for comes back as that file, distribution text and all."""
    for game in ("oras", "usum"):
        cards = [c for c in helper.gifts(game, all=True) if c["kind"] == "pokemon"]
        texts = []
        for c in cards[:40]:
            g = helper.gift(game, data=base64.b64decode(c["card_data"]), trainer=TRAINER)
            assert int.from_bytes(g.full_data[0x202:0x204], "little") == full_crc(g.full_data)
            texts.append(distribution_text(g.full_data))
        assert any("will be given" not in t for t in texts)      # some carry an official text of their own


# -- the DS games' Wi-Fi gift files (docs/mystery_gift.md, "DS Wi-Fi gift files") ------------------------------------

DS_GAMES = {"dp": 936, "pt": 936, "hgss": 936, "bw": 720, "b2w2": 720}


def ds_trainer(card: dict) -> dict:
    """The receiver in the card's own language when it has one (a Gen 5 card is often for one language only)."""
    return {**TRAINER, "language": card.get("restrict_language") or 2}


def undated(game: str, card: bytes) -> bytes:
    """A Gen 5 Wi-Fi file holds its card with no received date (0xAC); a Gen 4 card has none to drop."""
    return card if game in ("dp", "pt", "hgss") else card[:0xAC] + b"\0" * 4 + card[0xB0:]


@pytest.mark.parametrize("game", DS_GAMES)
def test_ds_cards_make_wifi_files_that_open_again(helper, game):
    cards = helper.gifts(game, all=True)
    assert len(cards) > 50
    for card in cards[::max(1, len(cards) // 25)]:
        g = helper.gift(game, data=base64.b64decode(card["card_data"]), trainer=ds_trainer(card))
        assert g.extension == ("pcd" if game in ("dp", "pt", "hgss") else "pgf") and not g.full_data
        assert len(g.wifi_data) == DS_GAMES[game]
        again = helper.gift(game, data=g.wifi_data, trainer=ds_trainer(card))
        assert again.card_data == undated(game, g.card_data) and again.wifi_data == g.wifi_data
        if card["kind"] in ("pokemon", "egg"):
            assert g.pokemon.legal, (card["title"], g.pokemon.report)


def wifi_crc(data: bytes) -> int:
    crc = 0xFFFF                                            # CRC-16/CCITT-FALSE, as the Gen 6 full file's
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def test_gen5_wifi_file_layout(helper):
    card = next(c for c in helper.gifts("b2w2", all=True) if c["kind"] == "pokemon" and c["restrict_version"])
    g = helper.gift("b2w2", data=base64.b64decode(card["card_data"]), trainer=ds_trainer(card),
                    distribution_text="Line one\nLine two")
    f = g.wifi_data
    assert f[:0xCC] == g.card_data[:0xAC] + b"\0" * 4 + g.card_data[0xB0:]            # the card, its date zero
    assert int.from_bytes(f[0xCC:0xD0], "little") == card["restrict_version"] << 20
    assert f[0xD0:0xD0 + 34] == "Line one".encode("utf-16-le") + b"\xfe\xff" + "Line two".encode("utf-16-le")
    assert set(f[0xD0 + 34:0x2CA]) == {0xFF} and f[0x2CB] == ds_trainer(card)["language"]
    assert int.from_bytes(f[0x2CE:], "little") == wifi_crc(f[:0x2CE])


# SHA-256 of files dwc_network_server_emulator serves (dlc/ADAE/10dp.myg, dlc/IPGE/24dppgsAU.myg,
# dlc/IRAO/G0028_darkrai_en.bin): what PokeUDS makes from PKHeX's card is the real file, byte for byte.
REAL_FILES = [
    ("dp", 10, "", "7fd166b5e09ba951562fbdd302720831adf759d240ea27062ec5e586a4abbf8a"),
    ("hgss", 24, "", "e5419afec658e989ed4f6b51d8efd09aad811ecf671a5b4f388d3f4b772e9281"),
    ("bw", 28, "Here's Darkrai!\nIt's a super-popular and\nMythical Pokémon!",
     "90b23c93905f0e9046b70dd2ad2c0510ed6ce3bb455b4cf23d28bfe2df1049c9"),
]


@pytest.mark.parametrize("game,card_id,text,sha", REAL_FILES)
def test_ds_wifi_files_match_the_real_ones(helper, game, card_id, text, sha):
    import hashlib

    made = set()
    for card in helper.gifts(game, all=True):
        if card["card_id"] == card_id:
            g = helper.gift(game, data=base64.b64decode(card["card_data"]), trainer=ds_trainer(card), distribution_text=text)
            made.add(hashlib.sha256(g.wifi_data).hexdigest())
    assert sha in made
