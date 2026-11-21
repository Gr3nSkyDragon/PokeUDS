"""Sword/Shield records (pokeuds.pk8): the layout the bank reads, encryption both ways, the checksum."""

import struct

import pytest

from pokeuds import pk8


def make_pk8(species=25, form=0, nickname="Sparky", ot="Ash", exp=27000, version=30, ec=0x2FC9_1D9D,
             tid=1234, sid=4321, pid=0x3173_4BBA, party=False):
    data = bytearray(pk8.PARTY_SIZE if party else pk8.STORED_SIZE)
    struct.pack_into("<I", data, 0x00, ec)
    struct.pack_into("<HHHHI", data, 0x08, species, 0, tid, sid, exp)
    struct.pack_into("<I", data, 0x1C, pid)
    struct.pack_into("<H", data, 0x24, form)
    data[0x58:0x58 + 2 * len(nickname)] = nickname.encode("utf-16-le")
    data[0xF8:0xF8 + 2 * len(ot)] = ot.encode("utf-16-le")
    data[pk8.VERSION] = version
    data[0xE2] = 2
    if party:
        data[0x148] = 30
    return pk8.fix_checksum(bytes(data))


def test_reads_the_fields_the_bank_shows():
    s = pk8.summary(make_pk8(species=26, form=1, party=True))
    assert (s.species, s.form, s.nickname, s.ot_name, s.version, s.level, s.exp) == (26, 1, "Sparky", "Ash", 30, 30, 27000)
    assert s.shown_tid == ((4321 << 16) | 1234) % 1_000_000 and not s.shiny and not s.is_egg


@pytest.mark.parametrize("party", [False, True])
def test_encryption_round_trips(party):
    plain = make_pk8(party=party)
    sealed = pk8.encrypt(plain)
    assert sealed != plain and not pk8.checksum_valid(sealed)
    assert pk8.decrypt(sealed) == plain and pk8.plain(sealed) == plain and pk8.plain(plain) == plain


def test_refuses_what_is_not_one():
    with pytest.raises(ValueError):
        pk8.decrypt(bytes(232))
    with pytest.raises(ValueError, match="checksum"):
        pk8.plain(bytes(range(256)) + bytes(72))
