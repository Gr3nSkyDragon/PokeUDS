"""Sword/Shield Pokemon records (``.pk8``), as far as the bank needs them: read, decrypt, checksum.

PokeUDS never trades with a Switch; a ``.pk8`` is what the Bank's Escape Rope makes of a 3DS-era Pokemon (moved the
way Pokemon HOME moves it, services/pkhex/Migrate.cs), to be taken into PKHeX or a Switch save from there.

A record is 0x148 (328) bytes in a box and 0x158 (344) in a party. The layout is PKHeX's G8PKM; the encryption is
Gen 6/7's (pokeuds.pkx) with 80-byte blocks: the 0x140 bytes after the 8-byte header are four blocks shuffled by
``(EC >> 13) & 31`` and XORed with the LCRNG stream seeded by the encryption constant, the party bytes with a stream
restarted from the same seed. The checksum at 0x06 is the 16-bit sum of the words from 0x08 to 0x148.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .pkx import BLOCK_POSITION, _crypt, _shuffle_value

EXTENSION = "pk8"
STORED_SIZE = 0x148
PARTY_SIZE = 0x158
BLOCK_SIZE = 0x50
SIZES = (STORED_SIZE, PARTY_SIZE)
VERSION = 0xDE              # the origin game: what the Switch games' origin mark is drawn from


def _blocks(data: bytes | bytearray) -> list[bytes]:
    return [bytes(data[8 + BLOCK_SIZE * i:8 + BLOCK_SIZE * (i + 1)]) for i in range(4)]


def decrypt(ek8: bytes) -> bytes:
    if len(ek8) not in SIZES:
        raise ValueError(f"a Sword/Shield record is 328 or 344 bytes, not {len(ek8)}")
    out = bytearray(ek8)
    ec = struct.unpack_from("<I", out, 0)[0]
    _crypt(out, 8, STORED_SIZE, ec)
    if len(out) == PARTY_SIZE:
        _crypt(out, STORED_SIZE, PARTY_SIZE, ec)
    blocks = _blocks(out)
    order = BLOCK_POSITION[_shuffle_value(ec)]
    out[8:STORED_SIZE] = b"".join(blocks[order[i]] for i in range(4))
    return bytes(out)


def encrypt(pk8: bytes) -> bytes:
    if len(pk8) not in SIZES:
        raise ValueError(f"a Sword/Shield record is 328 or 344 bytes, not {len(pk8)}")
    out = bytearray(pk8)
    ec = struct.unpack_from("<I", out, 0)[0]
    blocks = _blocks(out)
    order = BLOCK_POSITION[_shuffle_value(ec)]
    shuffled = [b""] * 4
    for i in range(4):
        shuffled[order[i]] = blocks[i]
    out[8:STORED_SIZE] = b"".join(shuffled)
    _crypt(out, 8, STORED_SIZE, ec)
    if len(out) == PARTY_SIZE:
        _crypt(out, STORED_SIZE, PARTY_SIZE, ec)
    return bytes(out)


def checksum(pk8: bytes) -> int:
    return sum(struct.unpack_from("<160H", pk8, 8)) & 0xFFFF


def checksum_valid(pk8: bytes) -> bool:
    return len(pk8) in SIZES and struct.unpack_from("<H", pk8, 6)[0] == checksum(pk8)


def fix_checksum(pk8: bytes) -> bytes:
    out = bytearray(pk8)
    struct.pack_into("<H", out, 6, checksum(out))
    return bytes(out)


def plain(raw: bytes) -> bytes:
    """The decrypted record, from a decrypted or encrypted one; ValueError when it is neither."""
    if checksum_valid(raw):
        return raw
    decrypted = decrypt(raw)
    if checksum_valid(decrypted):
        return decrypted
    raise ValueError("checksum fails both as plain and as encrypted data")


def to_stored(pk8: bytes) -> bytes:
    return pk8[:STORED_SIZE]


def _text(raw: bytes) -> str:
    return raw.decode("utf-16-le", "replace").split("\0", 1)[0]


@dataclass
class Summary:
    species: int
    form: int
    held_item: int
    tid: int
    sid: int
    exp: int
    pid: int
    nickname: str
    ot_name: str
    language: int
    is_egg: bool
    level: int | None
    version: int

    @property
    def shiny(self) -> bool:
        return (self.tid ^ self.sid ^ (self.pid >> 16) ^ (self.pid & 0xFFFF)) < 16

    @property
    def shown_tid(self) -> int:
        """The six-digit ID the Switch games show, as Gen 7's."""
        return ((self.sid << 16) | self.tid) % 1_000_000


def summary(pk8: bytes) -> Summary:
    species, item, tid, sid = struct.unpack_from("<HHHH", pk8, 0x08)
    exp, = struct.unpack_from("<I", pk8, 0x10)
    pid, = struct.unpack_from("<I", pk8, 0x1C)
    form, = struct.unpack_from("<H", pk8, 0x24)
    iv32, = struct.unpack_from("<I", pk8, 0x8C)
    level = pk8[0x148] if len(pk8) == PARTY_SIZE else None
    return Summary(species, form, item, tid, sid, exp, pid, _text(pk8[0x58:0x72]), _text(pk8[0xF8:0x112]),
                   pk8[0xE2], bool(iv32 >> 30 & 1), level, pk8[VERSION])
