"""The Gen 7 signing-key file: writing/reading it, and refusing public-only sources."""

from pathlib import Path

import pytest

from pokeuds.games.gen7 import keyextract
from pokeuds.games.gen7.keys import PUBLIC_KEY


def test_a_text_key_round_trips(tmp_path):
    path = tmp_path / "rsa_d_key.txt"
    keyextract.write_key(path, 0x1234, "a unit test", 0)
    assert keyextract.read_text_key(path) == 0x1234
    assert "SECRET" in path.read_text(encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        keyextract.read_text_key(tmp_path / "absent.txt")


def test_a_public_key_is_not_a_private_key():
    with pytest.raises(ValueError, match="no Gen 7 distributor private key"):
        keyextract.extract(PUBLIC_KEY)


def test_the_local_distributor_der_extracts_when_present():
    der = Path(".re/g7/key_66b2f7.der")
    if not der.exists():
        pytest.skip("no local distributor DER")
    offset, private = keyextract.extract(der.read_bytes())
    assert offset == 0
    assert 0 < private.bit_length() <= 768


def test_a_cci_dump_extracts_like_a_cxi():
    """A decrypted CCI (.cci/.3ds) puts the distributor's NCCH at a partition offset of its NCSD: the key is found
    there as in a CXI or code.bin."""
    code = Path(".re/g7/code.bin")
    if not code.exists():
        pytest.skip("no local distributor dump")
    data = code.read_bytes()
    _, expected = keyextract.extract(data)
    ncsd = bytearray(0x4000)
    ncsd[0x100:0x104] = b"NCSD"
    ncsd[0x120:0x128] = (0x20).to_bytes(4, "little") + (len(data) // 0x200 + 1).to_bytes(4, "little")   # partition 0
    cci = bytes(ncsd) + bytes(0x200) + b"NCCH" + bytes(0x1FC) + data                                     # header, exefs
    offset, private = keyextract.extract(cci)
    assert private == expected and offset > 0x4000
