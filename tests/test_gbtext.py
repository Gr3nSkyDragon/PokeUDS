"""Game Boy text in every language (pokeuds.vc.gbtext), Japanese and Korean records, blocks and mail, and the bank."""

import json

import pytest

from pokeuds import gbpk
from pokeuds.app import bank
from pokeuds.vc import gbtext, link
from tests.test_vc_link import gen1_mon, gen2_mon


def test_each_family_reads_and_writes_its_names():
    assert gbtext.decode(gbtext.encode("PIKACHU")) == "PIKACHU" and len(gbtext.encode("PIKACHU")) == 11
    assert gbtext.decode(gbtext.encode("ÄÖÜé", "intl")) == "ÄÖÜé"             # German and French letters
    jp = gbtext.encode("ピカチュウ", "jp")
    assert len(jp) == 6 and jp[:5] == bytes.fromhex("418590ae82") and gbtext.decode(jp, "jp") == "ピカチュウ"
    kr = gbtext.encode("피카츄", "kr")
    assert kr[:6] == bytes.fromhex("0a67090b08f2") and gbtext.decode(kr, "kr") == "피카츄"      # two bytes a syllable
    assert gbtext.decode(gbtext.encode("GOLD 1", "kr"), "kr") == "GOLD 1"     # and Latin letters in one
    with pytest.raises(ValueError, match="no Game Boy character"):
        gbtext.encode("PIKA", "jp")                                          # no Latin letters in Japanese games
    with pytest.raises(ValueError, match="longer than 5"):
        gbtext.encode("ピカチュウピ", "jp")
    with pytest.raises(ValueError, match="longer than 10"):
        gbtext.encode("피카츄피카츄", "kr")                                    # 12 bytes
    assert gbtext.decode(gbtext.encode("피카츄피카츄", "kr", lenient=True), "kr") == "피카츄피카"   # cut whole


def test_species_and_trainer_names_in_every_language():
    names = {lang: gbtext.decode(gbtext.species_name(25, lang), gbtext.family_of(lang))
             for lang in ("en", "de", "fr", "it", "es", "jp", "kr")}
    assert names == {"en": "PIKACHU", "de": "PIKACHU", "fr": "PIKACHU", "it": "PIKACHU", "es": "PIKACHU",
                     "jp": "ピカチュウ", "kr": "피카츄"}
    assert gbtext.decode(gbtext.species_name(4, "de")) == "GLUMANDA" and gbtext.decode(gbtext.species_name(4, "fr")) == "SALAMECHE"
    assert gbtext.decode(gbtext.species_name(122, "en")) == "MR.MIME"         # the Game Boy name, not Gen 3's
    assert gbtext.decode(gbtext.trainer_name("POKEUDS", "jp"), "jp") == "トレーナー"   # no Latin letters to show it
    assert gbtext.decode(gbtext.trainer_name("POKEUDS", "kr"), "kr") == "POKEUDS"


def test_a_name_moves_between_languages():
    charmander = gbtext.encode("GLUMANDA")
    assert gbtext.decode(gbtext.convert_name(charmander, "de", "fr", dex=4)) == "SALAMECHE"    # never nicknamed
    assert gbtext.decode(gbtext.convert_name(charmander, "de", "jp", dex=4), "jp") == "ヒトカゲ"
    assert gbtext.decode(gbtext.convert_name(gbtext.encode("FLAMMY"), "de", "fr", dex=4)) == "FLAMMY"   # a nickname stays
    assert gbtext.decode(gbtext.convert_name(gbtext.encode("FLAMMY"), "de", "jp", dex=4), "jp") == "?????"
    assert gbtext.decode(gbtext.convert_name(gbtext.encode("ASH"), "en", "kr"), "kr") == "ASH"
    assert gbtext.decode(gbtext.convert_name(gbtext.encode("サトシ", "jp"), "jp", "en")) == "???"


def test_japanese_and_korean_records_and_files(tmp_path):
    jp = gbpk.Record(1, gen1_mon(dex=25).data, gbtext.encode("サトシ", "jp"), gbtext.encode("ピカチュウ", "jp"), "jp")
    raw = jp.to_file()
    assert len(raw) == gbpk.PK1_SIZE_JP == 59 and gbpk.Record.from_file(raw) == jp
    assert jp.describe() == "Pikachu (#25) 'ピカチュウ' Lv 12, OT サトシ 12345"
    kr = gbpk.Record(2, gen2_mon(dex=25).data, gbtext.encode("골드", "kr"), gbtext.encode("피카츄", "kr"), "kr")
    raw2 = kr.to_file()
    assert len(raw2) == gbpk.PK2_SIZE == 73 and gbpk.Record.from_file(raw2, "kr") == kr
    assert gbpk.Record.from_file(raw2).family == "intl"                      # the size cannot tell a Korean one
    with pytest.raises(ValueError, match="not a Japanese record"):
        gbpk.Record.from_file(gen1_mon().to_file(), "jp")
    assert len(gbpk.gen1_to_gen2(jp).to_file()) == gbpk.PK2_SIZE_JP and gbpk.gen1_to_gen2(jp).family == "jp"
    with pytest.raises(ValueError, match="no Generation 1 game"):
        gbpk.gen2_to_gen1(kr)


def test_blocks_and_mail_in_each_layout():
    for family, sizes in (("intl", (424, 450, 385)), ("jp", (359, 385, 285)), ("kr", (424, 450, 577))):
        layout = link.LAYOUTS[family]
        assert (layout.party_block(1), layout.party_block(2), layout.mail_body) == sizes
        a, b, c = ("ア", "イ", "ウ") if family == "jp" else ("A", "B", "C")
        mons = [gbpk.Record(2, gen2_mon(dex=d).data, gbtext.encode(a, family), gbtext.encode(b, family), family)
                for d in (152, 155)]
        party = link.Party(gbtext.encode(c, family), 0x1234, mons,
                           [bytes([0x80, 0xFE, 0x81]).ljust(layout.mail, b"\x00"), bytes(range(0x80, 0x80 + layout.mail))])
        block, patch = link.party_blocks(party, 2, family)
        back = link.parse_party(list(block), list(patch), 2, family)
        assert back.mons == mons and back.trainer_id == 0x1234
        mail = link.mail_block(party, family)
        assert mail[:5] == bytes([layout.mail_preamble] * 5) and len(mail) == 5 + layout.mail_body
        got = link.parse_mail(list(mail[5:]), family)
        assert got[:2] == party.mail and got[2] == bytes(layout.mail)
    # Japanese mail puts each author after its message and patches all of it: a $FE in an author comes through too.
    jp = link.LAYOUTS["jp"]
    author_fe = bytes(33) + b"\xfe" + bytes(8)
    party = link.Party(gbtext.encode("ウ", "jp"), 1, [gbpk.Record(2, gen2_mon().data, gbtext.encode("ア", "jp"),
                                                                  gbtext.encode("イ", "jp"), "jp")], [author_fe])
    assert link.parse_mail(list(link.mail_block(party, "jp")[5:]), "jp")[0] == author_fe
    with pytest.raises(ValueError, match="jp party block holds jp names"):
        link.party_blocks(link.Party(gbtext.encode("C"), 1, [gen2_mon()]), 2, "jp")


def test_the_bank_keeps_and_reads_each_language(tmp_path):
    b = bank.Bank(tmp_path / "bank")
    jp = gbpk.Record(1, gen1_mon(dex=150).data, gbtext.encode("サトシ", "jp"), gbtext.encode("ミュウツー", "jp"), "jp")
    imported = tmp_path / "mewtwo.pk1"
    imported.write_bytes(jp.to_file())
    entry, _ = b.import_file(imported)
    assert entry.nickname == "ミュウツー" and entry.ot == "サトシ" and entry.meta["language"] == "jp"
    assert bank.language_of(entry.path) == "jp"
    kr = gbpk.Record(2, gen2_mon(dex=25).data, gbtext.encode("골드", "kr"), gbtext.encode("피카츄", "kr"), "kr")
    out = tmp_path / "received"
    out.mkdir()
    (out / "20261110-120000_gsc-kr_trade1_025_x_00001.pk2").write_bytes(kr.to_file())
    (got,) = b.sync_received(out)
    assert got.nickname == "피카츄" and got.ot == "골드" and got.meta["language"] == "kr"
    assert bank.language_of(got.path) == "kr" and bank.language_of(out / "20261110-120000_gsc-kr_trade1_025_x_00001.pk2") == "kr"
    plain = tmp_path / "plain.pk2"
    plain.write_bytes(gen2_mon().to_file())
    assert bank.language_of(plain) == "" and bank.language_of(tmp_path / "missing.pk2") == ""
    plain.with_suffix(".json").write_text(json.dumps({"language": "de"}))
    assert bank.language_of(plain) == "de"


def test_the_command_line_offers_a_korean_pokemon_to_japanese_gold(tmp_path):
    """The bank says the file is Korean; the demo console is Japanese Gold: the names cross over, the Japanese
    Pokemon that comes back is saved as a Japanese file, and the bank reads it back as one."""
    from pokeuds import cli

    b = bank.Bank(tmp_path / "bank")
    kr = gbpk.Record(2, gen2_mon(dex=25).data, gbtext.encode("골드", "kr"), gbtext.encode("피카츄", "kr"), "kr")
    entry, _ = b.deposit(kr.to_file(), "pk2", source="received", extra={"language": "kr"})
    out = tmp_path / "out"
    code = cli.main(["vc", "trade", "--radio", "demo:gold-jp", "--offer", str(entry.path), "--output", str(out),
                     "--seconds", "5", "--run-seconds", "20"])
    assert code == 0
    (saved,) = list(out.glob("*.pk2"))
    assert "_gsc-jp_trade1_" in saved.name and len(saved.read_bytes()) == gbpk.PK2_SIZE_JP
    got, _ = b.deposit(saved.read_bytes(), "pk2", source="received", extra={"language": bank.received_language(saved.name)})
    assert got.meta["language"] == "jp" and got.nickname == "トゲピー"
