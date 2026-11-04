import os
import struct
from pathlib import Path

import pytest

from pokeuds import pkx, received

# PKHeX's BlockPositionInvert (PokeCrypto.cs), first 24 entries.
PKHEX_INVERT = [0, 1, 2, 4, 3, 5, 6, 7, 12, 18, 13, 19, 8, 10, 14, 20, 16, 22, 9, 11, 15, 21, 17, 23]


def make_record(ec: int, species: int = 25, size: int = pkx.PARTY_SIZE, nickname: str = "Pika",
                version: int = 24) -> bytes:
    rng = __import__("random").Random(ec)
    data = bytearray(rng.randbytes(size))
    struct.pack_into("<I", data, 0, ec)
    struct.pack_into("<H", data, 4, 0)            # Sanity.
    struct.pack_into("<H", data, 8, species)
    data[0xDF] = version                          # Origin game, 24 = X.
    data[0x40:0x5A] = nickname.encode("utf-16-le").ljust(26, b"\0")
    data[0xB0:0xCA] = "Ash".encode("utf-16-le").ljust(26, b"\0")
    return pkx.fix_checksum(bytes(data))


def test_inverse_table_matches_pkhex():
    for sv, order in enumerate(pkx.BLOCK_POSITION):
        inverse = pkx.BLOCK_POSITION[PKHEX_INVERT[sv]]
        assert [order[inverse[i]] for i in range(4)] == [0, 1, 2, 3]


@pytest.mark.parametrize("sv", range(32))
@pytest.mark.parametrize("size", pkx.SIZES)
def test_round_trip_every_shuffle(sv, size):
    record = make_record((sv << 13) | 0x1234_0007, size=size)
    encrypted = pkx.encrypt(record)
    assert encrypted[:8] == record[:8] and encrypted != record
    assert pkx.decrypt(encrypted) == record
    assert pkx.checksum_valid(record) and not pkx.checksum_valid(encrypted)


def test_known_identity_shuffle_is_plain_xor():
    record = make_record(0)  # sv 0: no shuffle, so the first word is XORed with the first RNG output.
    encrypted = pkx.encrypt(record)
    seed = 0x6073  # 0x41C64E6D * 0 + 0x6073
    assert struct.unpack_from("<H", encrypted, 8)[0] == struct.unpack_from("<H", record, 8)[0] ^ (seed >> 16)


def test_summary_and_find_records():
    record = make_record(0xDEADBEEF, species=35, nickname="Clefairy")
    s = pkx.summary(record)
    assert (s.species, s.nickname, s.ot_name, s.encryption_constant) == (35, "Clefairy", "Ash", 0xDEADBEEF)
    blob = b"\x01\x02\x03" + pkx.encrypt(record) + b"\xff" * 9
    found = pkx.find_records(blob)
    assert [(o, n) for o, n, _ in found] == [(3, pkx.PARTY_SIZE)]
    assert found[0][2] == record


def test_load_and_save(tmp_path):
    record = make_record(0x0BADF00D)
    encrypted_file = tmp_path / "x.ek6"
    encrypted_file.write_bytes(pkx.encrypt(record))
    assert pkx.load(encrypted_file) == record
    path = received.save(pkx.encrypt(record), tmp_path / "out", game="xy", trade=2, stamp="20260101-000000")
    assert path.name == f"20260101-000000_xy_trade2_025_Pika_{pkx.summary(record).pid:08X}.pk6"
    assert path.read_bytes() == record[:pkx.STORED_SIZE]


def test_generation_limits():
    rowlet = make_record(0x0000_0042, species=722)
    assert pkx.looks_like(rowlet, pkx.PK7) and not pkx.looks_like(rowlet, pkx.PK6)
    assert pkx.find_records(pkx.encrypt(rowlet), pkx.PK6) == []
    assert len(pkx.find_records(pkx.encrypt(rowlet), pkx.PK7)) == 1
    with pytest.raises(ValueError, match="Generation 6"):
        pkx.check_format(rowlet, pkx.PK6)
    pkx.check_format(make_record(1, species=807), pkx.PK7)
    with pytest.raises(ValueError):
        pkx.check_format(make_record(1, species=808), pkx.PK7)


def test_guess_format_and_pk7_output(tmp_path):
    assert pkx.guess_format(make_record(1, species=25)) is pkx.PK6
    assert pkx.guess_format(make_record(1, species=722)) is pkx.PK7
    sun_pikachu = make_record(1, species=25, version=30)
    assert pkx.guess_format(sun_pikachu) is pkx.PK7
    path = received.save(sun_pikachu, tmp_path, game="sm", trade=1, stamp="20260101-000000", fmt=pkx.PK7)
    assert path.suffix == ".pk7"


# Real records to check against game data, e.g. PKHeX's test files: paths separated by os.pathsep.
SAMPLES = [s for s in os.environ.get("POKEUDS_PKX_SAMPLES", "").split(os.pathsep) if s]


@pytest.mark.skipif(not SAMPLES, reason="set POKEUDS_PKX_SAMPLES to real .pk6/.pk7 files to check against game data")
@pytest.mark.parametrize("sample", SAMPLES or ["-"])
def test_real_record(sample):
    record = pkx.load(Path(sample))
    fmt = pkx.FORMATS[Path(sample).suffix.lstrip(".").replace("ek", "pk")]
    assert pkx.looks_like(record, fmt)
    assert pkx.decrypt(pkx.encrypt(record)) == record
