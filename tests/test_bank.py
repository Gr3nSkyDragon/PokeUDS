"""The bank: deposits, de-duplication, the received folder, Time Capsule copies, export, games."""

import struct

import pytest

from pokeuds import gbpk, pkx, received
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
