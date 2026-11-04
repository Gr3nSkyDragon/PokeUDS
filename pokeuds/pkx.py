"""Generation 6 and 7 Pokemon records (``.pk6``: X/Y, ORAS; ``.pk7``: Sun/Moon, USUM).

PKHeX derives PK7 from the same base as PK6 (``G6PKM``): one layout for the fields read here, one
encryption, one checksum. The formats differ in the species range and the meaning of some later
fields, so each game names its ``Format`` and everything else is shared.

A record is 232 bytes in a box (``.pk6``/``.pk7`` decrypted, ``.ek6``/``.ek7`` encrypted) and 260 in a party
(the extra 28 bytes hold status and battle stats). Encryption: the 0xE0 bytes after the 8-byte
header are four 56-byte blocks, shuffled by ``(EC >> 13) & 31`` and XORed with an LCRNG stream
seeded by the encryption constant; party stats are XORed with a stream restarted from the same
seed. The checksum at 0x06 is the 16-bit sum of the words from 0x08 to 0xE8.

Offsets and tables follow PKHeX (PK6.cs, PK7.cs, G6PKM.cs, PokeCrypto.cs).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path



@dataclass(frozen=True)
class Format:
    generation: int
    extension: str
    max_species: int     # The last species the generation's newest games know.


PK6 = Format(6, "pk6", 721)
PK7 = Format(7, "pk7", 807)   # Sun/Moon end at 802; Ultra Sun/Ultra Moon add up to 807.
FORMATS = {f.extension: f for f in (PK6, PK7)}

STORED_SIZE = 232
PARTY_SIZE = 260
BLOCK_SIZE = 56
SIZES = (STORED_SIZE, PARTY_SIZE)

# Unshuffle order: decrypted block i is the encrypted block BLOCK_POSITION[sv][i].
BLOCK_POSITION = [
    (0, 1, 2, 3), (0, 1, 3, 2), (0, 2, 1, 3), (0, 3, 1, 2), (0, 2, 3, 1), (0, 3, 2, 1),
    (1, 0, 2, 3), (1, 0, 3, 2), (2, 0, 1, 3), (3, 0, 1, 2), (2, 0, 3, 1), (3, 0, 2, 1),
    (1, 2, 0, 3), (1, 3, 0, 2), (2, 1, 0, 3), (3, 1, 0, 2), (2, 3, 0, 1), (3, 2, 0, 1),
    (1, 2, 3, 0), (1, 3, 2, 0), (2, 1, 3, 0), (3, 1, 2, 0), (2, 3, 1, 0), (3, 2, 1, 0),
]


def _shuffle_value(ec: int) -> int:
    return ((ec >> 13) & 31) % 24


def _crypt(data: bytearray, start: int, end: int, seed: int) -> None:
    for offset in range(start, end, 2):
        seed = (0x41C64E6D * seed + 0x6073) & 0xFFFFFFFF
        word = struct.unpack_from("<H", data, offset)[0] ^ (seed >> 16)
        struct.pack_into("<H", data, offset, word)


def _blocks(data: bytes | bytearray) -> list[bytes]:
    return [bytes(data[8 + BLOCK_SIZE * i:8 + BLOCK_SIZE * (i + 1)]) for i in range(4)]


def decrypt(ekx: bytes) -> bytes:
    if len(ekx) not in SIZES:
        raise ValueError(f"a Generation 6 record is 232 or 260 bytes, not {len(ekx)}")
    out = bytearray(ekx)
    ec = struct.unpack_from("<I", out, 0)[0]
    _crypt(out, 8, STORED_SIZE, ec)
    if len(out) == PARTY_SIZE:
        _crypt(out, STORED_SIZE, PARTY_SIZE, ec)
    blocks = _blocks(out)
    order = BLOCK_POSITION[_shuffle_value(ec)]
    out[8:STORED_SIZE] = b"".join(blocks[order[i]] for i in range(4))
    return bytes(out)


def encrypt(pkx: bytes) -> bytes:
    if len(pkx) not in SIZES:
        raise ValueError(f"a Generation 6 record is 232 or 260 bytes, not {len(pkx)}")
    out = bytearray(pkx)
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


def checksum(pkx: bytes) -> int:
    return sum(struct.unpack_from("<112H", pkx, 8)) & 0xFFFF


def checksum_valid(pkx: bytes) -> bool:
    return len(pkx) in SIZES and struct.unpack_from("<H", pkx, 6)[0] == checksum(pkx)


def fix_checksum(pkx: bytes) -> bytes:
    out = bytearray(pkx)
    struct.pack_into("<H", out, 6, checksum(out))
    return bytes(out)


def _text(raw: bytes) -> str:
    return raw.decode("utf-16-le", "replace").split("\0", 1)[0]


@dataclass
class Summary:
    encryption_constant: int
    pid: int
    species: int
    held_item: int
    tid: int
    sid: int
    nickname: str
    ot_name: str
    language: int
    is_egg: bool
    level: int | None
    version: int                 # Origin game (0xDF), e.g. 24 X, 27 Omega Ruby, 30 Sun, 33 Ultra Moon.

    @property
    def shiny(self) -> bool:
        return ((self.tid ^ self.sid ^ (self.pid >> 16) ^ (self.pid & 0xFFFF)) < 16)


def summary(pkx: bytes) -> Summary:
    ec, = struct.unpack_from("<I", pkx, 0x00)
    species, item, tid, sid = struct.unpack_from("<HHHH", pkx, 0x08)
    pid, = struct.unpack_from("<I", pkx, 0x18)
    iv32, = struct.unpack_from("<I", pkx, 0x74)
    level = pkx[0xEC] if len(pkx) == PARTY_SIZE else None
    return Summary(ec, pid, species, item, tid, sid, _text(pkx[0x40:0x5A]), _text(pkx[0xB0:0xCA]), pkx[0xE3],
                   bool(iv32 >> 30 & 1), level, pkx[0xDF])


def looks_like(pkx: bytes, fmt: Format = PK7) -> bool:
    """A decrypted record that passes the checks a game would make before showing it.

    The default, PK7, accepts every Gen 6 species too: use it when the generation is not known.
    """
    if not checksum_valid(pkx):
        return False
    s = summary(pkx)
    return 0 < s.species <= fmt.max_species and struct.unpack_from("<H", pkx, 4)[0] == 0  # Sanity word is 0.


def load(path: str | Path) -> bytes:
    """A decrypted record from a .pk6/.ek6/.pk7/.ek7 file, as PKHeX and pokeldn's bank write them."""
    raw = Path(path).expanduser().read_bytes()
    if len(raw) not in SIZES:
        raise ValueError(f"{path}: {len(raw)} bytes is not a Generation 6/7 record (232 or 260)")
    if checksum_valid(raw):
        return raw
    decrypted = decrypt(raw)
    if checksum_valid(decrypted):
        return decrypted
    raise ValueError(f"{path}: checksum fails both as plain and as encrypted data")


def to_stored(pkx: bytes) -> bytes:
    return pkx[:STORED_SIZE]


def check_format(pkx: bytes, fmt: Format) -> None:
    """Refuse a record the target game cannot hold (a Gen 7 species offered to X/Y, say)."""
    s = summary(pkx)
    if not 0 < s.species <= fmt.max_species:
        raise ValueError(f"species {s.species} does not exist in Generation {fmt.generation} "
                         f"(1-{fmt.max_species})")


def guess_format(pkx: bytes) -> Format:
    """PK7 for a record that can only be Generation 7 (its species or origin), else PK6.

    Only a guess: a Gen 6-born Pokemon moved to Gen 7 by Pokemon Bank reads as PK6 here. Use the
    game's own format whenever the game is known.
    """
    s = summary(pkx)
    return PK7 if s.species > PK6.max_species or s.version >= 30 else PK6


def find_records(blob: bytes, fmt: Format = PK7) -> list[tuple[int, int, bytes]]:
    """(offset, size, decrypted record) for every encrypted or plain Gen 6/7 record inside ``blob``.

    Used on captured trade messages, where the record's position is not known yet.
    """
    found = []
    for size in (PARTY_SIZE, STORED_SIZE):
        for offset in range(0, len(blob) - size + 1):
            window = blob[offset:offset + size]
            if window[4:6] != b"\0\0":   # The sanity word is 0 and is not encrypted.
                continue
            for candidate in (decrypt(window), window):
                if looks_like(candidate, fmt):
                    if not any(o <= offset < o + s for o, s, _ in found):
                        found.append((offset, size, candidate))
                    break
    return sorted(found)
