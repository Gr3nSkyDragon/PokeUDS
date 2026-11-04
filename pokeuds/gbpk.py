"""Game Boy Pokemon records: Generation 1 (.pk1) and Generation 2 (.pk2), and the Time Capsule.

Files use PKHeX's single-Pokemon list layout (international games, 11-byte names):

    .pk1  69 bytes: 01, Gen 1 species index, FF, the 44-byte party struct, OT name, nickname
    .pk2  73 bytes: 01, national dex number, FF, the 48-byte party struct, OT name, nickname

Struct layouts and the conversions follow pret: pokered ``constants/pokemon_data_constants.asm``
and pokecrystal ``engine/link/link.asm`` (``Link_PrepPartyData_Gen1`` sends a Gen 2 Pokemon to a
Gen 1 game, ``Link_ConvertPartyStruct1to2`` takes one in).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path

from .vc import gbdata

NAME_LENGTH = 11
GEN1_STRUCT = 44
GEN2_STRUCT = 48
PK1_SIZE = 3 + GEN1_STRUCT + 2 * NAME_LENGTH   # 69
PK2_SIZE = 3 + GEN2_STRUCT + 2 * NAME_LENGTH   # 73
TERMINATOR = 0x50
BASE_HAPPINESS = 70
MAX_GEN1_MOVE = 165          # Struggle; moves above it do not exist in Gen 1.
MAGNEMITE, MAGNETON = 81, 82

_DEX_TO_INDEX = {dex: i for i, dex in enumerate(gbdata.GEN1_INDEX_TO_DEX) if dex}
_DECODE = {v: k for k, v in gbdata.CHARMAP.items()}


# -- text ------------------------------------------------------------------------------------------

def encode_name(text: str, length: int = NAME_LENGTH) -> bytes:
    out = bytearray()
    for ch in text:
        code = gbdata.CHARMAP.get(ch)
        if code is None:
            raise ValueError(f"{ch!r} has no Game Boy character")
        out.append(code)
    if len(out) > length - 1:
        raise ValueError(f"{text!r} is longer than {length - 1} characters")
    out.append(TERMINATOR)
    return bytes(out).ljust(length, b"\x00")


def decode_name(raw: bytes) -> str:
    chars = []
    for b in raw:
        if b == TERMINATOR:
            break
        chars.append(_DECODE.get(b, "?"))
    return "".join(chars)


# -- species ---------------------------------------------------------------------------------------

def index_to_dex(index: int) -> int:
    return gbdata.GEN1_INDEX_TO_DEX[index] if 0 < index < len(gbdata.GEN1_INDEX_TO_DEX) else 0


def dex_to_index(dex: int) -> int:
    try:
        return _DEX_TO_INDEX[dex]
    except KeyError:
        raise ValueError(f"species {dex} does not exist in Generation 1") from None


def species_name(dex: int) -> str:
    return gbdata.SPECIES_NAMES.get(dex, f"#{dex}").replace("_", " ").title()


# -- records ---------------------------------------------------------------------------------------

@dataclass
class Record:
    """One Game Boy Pokemon: its party struct and its two names, as the games send them."""

    generation: int
    data: bytes          # 44 (Gen 1) or 48 (Gen 2) bytes
    ot_name: bytes       # 11 bytes, game text
    nickname: bytes      # 11 bytes, game text

    @property
    def dex(self) -> int:
        return index_to_dex(self.data[0]) if self.generation == 1 else self.data[0]

    @property
    def level(self) -> int:
        return self.data[33] if self.generation == 1 else self.data[31]

    @property
    def ot_id(self) -> int:
        return struct.unpack_from(">H", self.data, 12 if self.generation == 1 else 6)[0]

    @property
    def moves(self) -> list[int]:
        start = 8 if self.generation == 1 else 2
        return list(self.data[start:start + 4])

    @property
    def held_item(self) -> int:
        return 0 if self.generation == 1 else self.data[1]

    def describe(self) -> str:
        return (f"{species_name(self.dex)} (#{self.dex}) '{decode_name(self.nickname)}' Lv {self.level}, "
                f"OT {decode_name(self.ot_name)} {self.ot_id:05d}")

    def to_file(self) -> bytes:
        species = self.data[0]
        return bytes([1, species, 0xFF]) + self.data + self.ot_name + self.nickname

    @classmethod
    def from_file(cls, raw: bytes) -> "Record":
        if len(raw) == PK1_SIZE:
            gen, size = 1, GEN1_STRUCT
        elif len(raw) == PK2_SIZE:
            gen, size = 2, GEN2_STRUCT
        else:
            raise ValueError(f"{len(raw)} bytes is neither a .pk1 ({PK1_SIZE}) nor a .pk2 ({PK2_SIZE})")
        if raw[0] != 1 or raw[2] != 0xFF:
            raise ValueError("not a single-Pokemon list (expected 01 <species> FF)")
        body = raw[3:3 + size]
        return cls(gen, body, raw[3 + size:3 + size + NAME_LENGTH], raw[3 + size + NAME_LENGTH:3 + size + 2 * NAME_LENGTH])

    @property
    def extension(self) -> str:
        return f"pk{self.generation}"


def load(path: str | Path) -> Record:
    record = Record.from_file(Path(path).expanduser().read_bytes())
    if record.dex == 0:
        raise ValueError(f"{path}: not a real species (index {record.data[0]:#04x})")
    return record


# -- stats -----------------------------------------------------------------------------------------

def _stat_exp_term(stat_exp: int) -> int:
    root = 0                                  # pret GetSquareRoot: the square root rounded up, at most 255
    while root < 255 and root * root < stat_exp:
        root += 1
    return root // 4


def calc_stat(base: int, dv: int, stat_exp: int, level: int) -> int:
    return min(((base + dv) * 2 + _stat_exp_term(stat_exp)) * level // 100 + 5, 999)


def _special_dv(dvs: bytes) -> int:
    return dvs[1] & 0x0F


# -- Time Capsule ----------------------------------------------------------------------------------

def gen1_compatible(record: Record) -> list[str]:
    """Why a Gen 2 Pokemon cannot go through the Time Capsule (pokecrystal CheckTimeCapsuleCompatibility)."""
    problems = []
    if not 1 <= record.dex <= 151:
        problems.append(f"{species_name(record.dex)} is not a Generation 1 species")
    if any(m > MAX_GEN1_MOVE for m in record.moves):
        problems.append("it knows a move Generation 1 does not have")
    return problems


def gen2_to_gen1(record: Record) -> Record:
    """What a Gen 2 game sends for its Pokemon in the Time Capsule (``.ConvertPartyStruct2to1``)."""
    if record.generation != 2:
        raise ValueError("not a Generation 2 record")
    if problems := gen1_compatible(record):
        raise ValueError("; ".join(problems))
    d = record.data
    dex = d[0]
    if dex in (MAGNEMITE, MAGNETON):
        types = (gbdata.TYPES["ELECTRIC"], gbdata.TYPES["ELECTRIC"])
    else:
        types = gbdata.GEN2_BASE[dex][1]
    level = d[31]
    special = calc_stat(gbdata.KANTO_SPECIAL[dex], _special_dv(d[21:23]), struct.unpack_from(">H", d, 19)[0], level)
    out = (bytes([dex_to_index(dex)]) + d[34:36] + b"\x00" + bytes([d[32]]) + bytes(types)
           + d[1:27]                       # catch rate <- held item, moves, OT ID, exp, stat exp, DVs, PP
           + bytes([level]) + d[36:44]     # level, max HP, attack, defense, speed
           + struct.pack(">H", special))
    assert len(out) == GEN1_STRUCT
    return Record(1, out, record.ot_name, record.nickname)


def catch_rate_item(catch_rate: int) -> int:
    """The held item a Gen 2 game gives a Gen 1 Pokemon (``TimeCapsule_ReplaceTeruSama``)."""
    if catch_rate == 0:
        return 0
    return gbdata.CATCH_RATE_ITEMS.get(catch_rate, catch_rate)


def gen1_to_gen2(record: Record) -> Record:
    """What a Gen 2 game makes of a Gen 1 Pokemon it receives (``Link_ConvertPartyStruct1to2``)."""
    if record.generation != 1:
        raise ValueError("not a Generation 1 record")
    d = record.data
    dex = record.dex
    if not dex:
        raise ValueError(f"index {d[0]:#04x} is not a Pokemon")
    level = d[33]
    base = gbdata.GEN2_BASE[dex][0]
    spc_dv, spc_exp = _special_dv(d[27:29]), struct.unpack_from(">H", d, 25)[0]
    out = (bytes([dex, catch_rate_item(d[7])]) + d[8:33]        # item, moves .. PP
           + bytes([BASE_HAPPINESS, 0, 0, 0])                  # friendship, Pokerus, caught data
           + bytes([level, d[4], 0]) + d[1:3]                  # level, status, unused, current HP
           + d[34:42]                                          # max HP, attack, defense, speed
           + struct.pack(">HH", calc_stat(base[4], spc_dv, spc_exp, level), calc_stat(base[5], spc_dv, spc_exp, level)))
    assert len(out) == GEN2_STRUCT
    return Record(2, out, record.ot_name, record.nickname)
