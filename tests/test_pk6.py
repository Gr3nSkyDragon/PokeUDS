import os
import struct
from pathlib import Path

import pytest

from pokeuds import pk6, received

# PKHeX's BlockPositionInvert (PokeCrypto.cs), first 24 entries.
PKHEX_INVERT = [0, 1, 2, 4, 3, 5, 6, 7, 12, 18, 13, 19, 8, 10, 14, 20, 16, 22, 9, 11, 15, 21, 17, 23]


def make_record(ec: int, species: int = 25, size: int = pk6.PARTY_SIZE, nickname: str = "Pika") -> bytes:
    rng = __import__("random").Random(ec)
    data = bytearray(rng.randbytes(size))
    struct.pack_into("<I", data, 0, ec)
    struct.pack_into("<H", data, 4, 0)            # Sanity.
    struct.pack_into("<H", data, 8, species)
    data[0x40:0x5A] = nickname.encode("utf-16-le").ljust(26, b"\0")
    data[0xB0:0xCA] = "Ash".encode("utf-16-le").ljust(26, b"\0")
    return pk6.fix_checksum(bytes(data))


def test_inverse_table_matches_pkhex():
    for sv, order in enumerate(pk6.BLOCK_POSITION):
        inverse = pk6.BLOCK_POSITION[PKHEX_INVERT[sv]]
        assert [order[inverse[i]] for i in range(4)] == [0, 1, 2, 3]


@pytest.mark.parametrize("sv", range(32))
@pytest.mark.parametrize("size", pk6.SIZES)
def test_round_trip_every_shuffle(sv, size):
    record = make_record((sv << 13) | 0x1234_0007, size=size)
    encrypted = pk6.encrypt(record)
    assert encrypted[:8] == record[:8] and encrypted != record
    assert pk6.decrypt(encrypted) == record
    assert pk6.checksum_valid(record) and not pk6.checksum_valid(encrypted)


def test_known_identity_shuffle_is_plain_xor():
    record = make_record(0)  # sv 0: no shuffle, so the first word is XORed with the first RNG output.
    encrypted = pk6.encrypt(record)
    seed = 0x6073  # 0x41C64E6D * 0 + 0x6073
    assert struct.unpack_from("<H", encrypted, 8)[0] == struct.unpack_from("<H", record, 8)[0] ^ (seed >> 16)


def test_summary_and_find_records():
    record = make_record(0xDEADBEEF, species=35, nickname="Clefairy")
    s = pk6.summary(record)
    assert (s.species, s.nickname, s.ot_name, s.encryption_constant) == (35, "Clefairy", "Ash", 0xDEADBEEF)
    blob = b"\x01\x02\x03" + pk6.encrypt(record) + b"\xff" * 9
    found = pk6.find_records(blob)
    assert [(o, n) for o, n, _ in found] == [(3, pk6.PARTY_SIZE)]
    assert found[0][2] == record


def test_load_and_save(tmp_path):
    record = make_record(0x0BADF00D)
    encrypted_file = tmp_path / "x.ek6"
    encrypted_file.write_bytes(pk6.encrypt(record))
    assert pk6.load(encrypted_file) == record
    path = received.save(pk6.encrypt(record), tmp_path / "out", game="xy", trade=2, stamp="20260101-000000")
    assert path.name == f"20260101-000000_xy_trade2_025_Pika_{pk6.summary(record).pid:08X}.pk6"
    assert path.read_bytes() == record[:pk6.STORED_SIZE]


SAMPLE = os.environ.get("POKEUDS_PK6_SAMPLE")


@pytest.mark.skipif(not SAMPLE, reason="set POKEUDS_PK6_SAMPLE to a real .pk6 to check against game data")
def test_real_record():
    record = pk6.load(Path(SAMPLE))
    assert pk6.looks_like_pk6(record)
    assert pk6.decrypt(pk6.encrypt(record)) == record
