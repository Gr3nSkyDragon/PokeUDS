"""The bank: deposits, de-duplication, the received folder, Time Capsule copies, export, games."""

import struct
from dataclasses import replace

import pytest

from pokeuds import gbpk, pkhex, pkx, received
from pokeuds.app import bank
from pokeuds.radio.demo import sample_mon
from tests.test_pkx import make_record


def pk6(species=25, nickname="Sparky", exp=27000, ec=0x1234_5678):
    data = bytearray(make_record(ec, species, nickname=nickname, size=pkx.STORED_SIZE))
    struct.pack_into("<I", data, 0x10, exp)
    return pkx.fix_checksum(bytes(data))


@pytest.fixture
def b(tmp_path):
    return bank.Bank(tmp_path / "bank")


def test_import_reads_and_keeps_once(b, tmp_path):
    src = tmp_path / "sparky.ek6"
    src.write_bytes(pkx.encrypt(pk6()))                # an encrypted file is banked decrypted
    entry, added = b.import_file(src)
    assert added and entry.extension == "pk6" and entry.species == "Pikachu" and entry.level == 30
    assert pkx.checksum_valid(entry.data()) and entry.source == "imported" and entry.origin == str(src)
    again, added = b.import_file(src)
    assert not added and again.id == entry.id and len(b.entries()) == 1
    assert entry.describe().startswith("Pikachu 'Sparky' Lv 30, OT Ash")


def test_refuses_what_is_not_a_pokemon(b, tmp_path):
    (tmp_path / "x.pk6").write_bytes(bytes(100))
    with pytest.raises(ValueError, match="not a Generation 6/7 record"):
        b.import_file(tmp_path / "x.pk6")
    (tmp_path / "x.pk6").write_bytes(pk6(species=722))
    with pytest.raises(ValueError, match="does not exist in Generation 6"):
        b.import_file(tmp_path / "x.pk6")
    (tmp_path / "x.txt").write_text("hi")
    with pytest.raises(ValueError, match="not a Pokemon file"):
        b.import_file(tmp_path / "x.txt")


def test_received_folder_is_deposited_once(b, tmp_path):
    out = tmp_path / "received"
    received.save(pk6(), out, game="xy", stamp="20261104-120000")
    gold = out / "cynda.pk2"
    gold.write_bytes(sample_mon(2, 155, 10, "CYNDA").to_file())
    gold.with_suffix(".mail").write_bytes(b"\x01" * 47)
    added = b.sync_received(out)
    assert sorted(e.extension for e in added) == ["pk2", "pk6"] and all(e.source == "received" for e in added)
    assert next(e for e in added if e.extension == "pk2").has_mail
    assert b.sync_received(out) == []
    b.remove(b.entries()[0])                           # removed from the bank: not brought back
    assert b.sync_received(out) == [] and len(b.entries()) == 1


def test_time_capsule_copies(b, tmp_path):
    entry, _ = b.deposit(sample_mon(1, 4, 12, "CHARMY").to_file(), "pk1", source="imported")
    copy, added = b.convert(entry)
    assert added and copy.extension == "pk2" and copy.dex == 4 and copy.source == "converted"
    back, _ = b.convert(copy)
    assert back.extension == "pk1" and back.dex == 4
    cynda, _ = b.deposit(sample_mon(2, 155, 10, "CYNDA").to_file(), "pk2", source="imported")
    with pytest.raises(ValueError, match="not a Generation 1 species"):
        b.convert(cynda)


def test_gen2_shiny_and_export(b, tmp_path):
    mon = sample_mon(2, 25, 20, "PIKA")
    data = bytearray(mon.data)
    data[21:23] = bytes([0xEA, 0xAA])                  # Attack 14, Defense/Speed/Special 10
    shiny = gbpk.Record(2, bytes(data), mon.ot_name, mon.nickname)
    entry, _ = b.deposit(shiny.to_file(), "pk2", source="built")
    assert entry.shiny and entry.level == 20
    first = b.export(entry, tmp_path / "out")
    second = b.export(entry, tmp_path / "out")
    assert first.name == "025_PIKA.pk2" and second.name == "025_PIKA_2.pk2"
    assert gbpk.load(first).dex == 25


def test_games_for(b):
    gen1, _ = b.deposit(sample_mon(1, 4, 12, "CHARMY").to_file(), "pk1", source="imported")
    xy, _ = b.deposit(pk6(), "pk6", source="imported")
    rowlet = pkx.fix_checksum(pk6(species=722)[:])
    sm, _ = b.deposit(rowlet, "pk7", source="imported")
    assert bank.games_for(gen1) == ["rby", "gsc"]
    assert bank.games_for(xy) == ["xy", "oras"] and bank.games_for(sm) == ["sm", "usum"]


def test_notes_and_legality_are_kept(b):
    entry, _ = b.deposit(pk6(), "pk6", source="imported")
    entry = b.set_note(entry, "for Dana")
    entry = b.set_legality(entry, True, "Legal!", "Wild")
    again = b.find(entry.id)
    assert again.note == "for Dana" and again.legal is True and again.report == "Legal!"


def traded(record: bytes, handler="Refresh", memory=3) -> bytes:
    """``record`` as a 3DS sends it back after a trade: its player as the handler, with memories."""
    d = bytearray(record)
    d[0x78:0x92] = handler.encode("utf-16-le").ljust(26, b"\0")
    d[0x93], d[0xA2], d[0xA4], d[0xA5], d[0xA6], d[0x95] = 1, 70, 1, 4, memory, 49
    return pkx.fix_checksum(bytes(d))


def test_the_same_pokemon_after_trades_is_kept_once(b, tmp_path):
    first, added = b.deposit(pk6(), "pk6", source="built")
    assert added
    out = tmp_path / "received"
    for n, memory in enumerate((3, 4, 14), 1):         # traded away and back three times
        received.save(traded(pk6(), memory=memory), out, game="xy", trade=n, stamp=f"20261105-21000{n}")
    assert b.sync_received(out) == [] and [e.id for e in b.entries()] == [first.id]
    again, added = b.deposit(pkx.encrypt(traded(pk6(), handler="Other")), "pk6", source="imported")
    assert not added and again.id == first.id
    grown, added = b.deposit(pk6(exp=30000), "pk6", source="received")      # it changed: a new entry
    assert added and grown.id != first.id
    gold = sample_mon(2, 155, 10, "CYNDA").to_file()
    b.deposit(gold, "pk2", source="built")
    happier = bytearray(gold)
    happier[3 + 27] = 200                              # a trade sets a Gen 2 Pokemon's friendship anew
    assert not b.deposit(bytes(happier), "pk2", source="received")[1]


def test_duplicates_banked_before_are_removed(b, tmp_path):
    first, _ = b.deposit(pk6(), "pk6", source="built")
    for n in range(2):                                 # as an older PokeUDS banked them, by exact content
        path = b.folder / f"20261105-21000{n}_025_Sparky_{n}.pk6"
        path.write_bytes(traded(pk6(), memory=n + 3))
        b._write_meta(path, {"source": "received", "created": first.created + n + 1})
    assert len(b.entries()) == 3
    pairs = b.duplicates()
    assert len(pairs) == 2 and all(kept.id == first.id for _, kept in pairs)
    assert [d for d, _ in b.remove_duplicates()] and [e.id for e in b.entries()] == [first.id]
    assert b.duplicates() == []


class FakeHelper:
    """Stands in for the PKHeX helper: checks by species, migrates to a fixed .pk7."""

    def __init__(self):
        self.migrated = []

    def check(self, game, data):
        legal = bank.parse(f"x.{ {'rby': 'pk1', 'gsc': 'pk2'}.get(game, 'pk6' if game in ('xy', 'oras') else 'pk7') }",
                           data).dex != 150
        return pkhex.Built(legal, "Legal!" if legal else "Invalid: no.", "Wild", "pk6", data, {})

    def migrate(self, game, data, source, vc_game=None, trainer=None):
        self.migrated.append((game, source, vc_game, trainer))
        return pkhex.Built(True, "Legal!", "Virtual Console Transfer", "pk7", pk6(species=6, ec=len(self.migrated)), {})


def test_the_whole_bank_is_checked(b, monkeypatch):
    monkeypatch.setattr(pkhex, "shared", lambda: FakeHelper())
    b.deposit(pk6(), "pk6", source="imported")
    b.deposit(sample_mon(1, 150, 70, "MEWTWO").to_file(), "pk1", source="imported")
    seen = []
    result = bank.check_all(b, progress=lambda done, total, e: seen.append((done, total)))
    assert len(result.legal) == 1 and [e.species for e in result.illegal] == ["Mewtwo"] and not result.failed
    assert seen[-1] == (2, 2) and all(e.legal is not None for e in b.entries())
    assert bank.check_all(b, stop=lambda: True).legal == []


def test_migrating_keeps_the_original_and_makes_one_copy(b):
    helper = FakeHelper()
    gen1, _ = b.deposit(sample_mon(1, 4, 12, "CHARMY").to_file(), "pk1", source="imported")
    xy, _ = b.deposit(pk6(), "pk6", source="imported")
    assert bank.migration_target(gen1) == 7 and bank.migration_target(xy) == 7
    assert bank.migrates_to(gen1) == ["sm", "usum"] and bank.migrates_to(xy) == ["sm", "usum"]
    copy, made = bank.migrate(b, gen1, vc_game="blue", trainer={"ot": "ME"}, helper=helper)
    assert made and copy.extension == "pk7" and copy.source == "migrated" and copy.legal is True
    assert copy.meta["migrated_from"] == gen1.id and helper.migrated == [("sm", "rby", "blue", {"ot": "ME"})]
    assert b.find(gen1.id) is not None and bank.games_for(b.find(gen1.id)) == ["rby", "gsc"]   # the original stays
    assert bank.games_for(copy) == ["sm", "usum"] and bank.migration_target(copy) is None   # and the copy stays in Gen 7
    again, made = bank.migrate(b, b.find(gen1.id), helper=helper)
    assert not made and again.id == copy.id and len(helper.migrated) == 1
    with pytest.raises(ValueError):
        bank.migrate(b, copy, helper=helper)                       # a .pk7 goes nowhere
    with pytest.raises(ValueError):
        bank.migrate(b, replace(gen1, meta={}), vc_game="gold", helper=helper)   # Gold is not a Gen 1 game
    xy_copy, made = bank.migrate(b, xy, helper=helper)
    assert made and helper.migrated[-1][1] == "xy" and xy_copy.note.startswith("Pokemon Bank copy")
