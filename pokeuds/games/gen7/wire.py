"""The Generation 7 trade's own messages (Sun/Moon), read off Azahar logs of Link Trades with a retail 3DS.

Below gflnet everything is Generation 6's: Pia's local header, NEX PRUDP V1 with the same checksum key, Net-Z with
the same per-session fields (``gen6.wire``). gflnet2 frames its messages as gflnet does, with CRC-16/USB and a
``01 02 ff ff`` node-pair word (``gen6.wire.gfl_encode(..., generation=7)``); syncs and commands have the same
bodies. Net-Z sets up one object more than X/Y: twelve host messages after its first three, seven answer pairs.

The session, in the order both consoles ran it (node 1 hosts and answers every sync with 03):

| step | messages |
|---|---|
| start | host: init ``04..`` and ``05..``; sync 0x88; both: module 0 cmd 0x91 (profile, 1828 B); sync 0xC8; both: cmd 0x93 (person, 1828 B); syncs 0xCA, 0x81, 0x82, 0x83 |
| trade screen | each side: module 1 cmd 00 ``[0c 00 00 00][u32 species][260-byte Pokemon]`` (the party's first Pokemon on show) and module 2 cmd 01 (the Pokemon under the cursor, 260 B, repeated as it moves) |
| offer | module 1 cmd 00 with ``0a`` (the Pokemon chosen), then module 3 cmd 00 (it, 260 B) |
| confirm | host: module 3 cmd 01 ``01``; client: cmd 02 ``01 00 00 00``; host: cmd 03 ``01 01 01 00``; then each player's "yes": the host's module 2 cmd 01 (its offer), the client's module 2 cmd 01 and module 3 cmd 05 ``05`` in ONE message; host: cmd 06 ``06`` |
| swap | module 3 syncs 0x5A, 0x57, 0x58 (the trade animation), 0x59; client: cmd 0B ``0b``; host: cmd 0C ``0c``; then the trade screen again |
| leave | both: module 3 cmd 09 ``09``; module 0 sync 0x89 |

A message may hold several commands back to back (``gen6.wire.parse_bodies``): the client's "yes" always does.

Pokemon travel encrypted, in party form (260 bytes). The profile and person blocks are fixed per player except a
timestamp (seconds since 2000) and a few counters in the person block; ``templates`` holds an emulated player's,
renamed "Dummy", and this module puts the trainer in.
"""

from __future__ import annotations

import os
import struct
import time
from dataclasses import dataclass, field

from ..gen6 import wire as gen6
from . import templates

SESSION, SCREEN, CURSOR, TRADE = 0, 1, 2, 3          # gflnet2 modules

SYNC_START, SYNC_PROFILES, SYNC_PERSONS, SYNC_HUB, SYNC_END = 0x88, 0xC8, 0xCA, (0x81, 0x82, 0x83), 0x89
SYNC_SWAP = (0x5A, 0x57, 0x58, 0x59)                  # module 3, around the trade animation

CMD_PROFILE, CMD_PERSON = 0x91, 0x93                  # module 0
CMD_SHOW = 0x00                                       # module 1: the Pokemon on show / chosen
CMD_CURSOR = 0x01                                     # module 2: the Pokemon under the cursor
CMD_OFFER, CMD_HOST_READY, CMD_CLIENT_YES, CMD_BOTH_YES, CMD_START = 0x00, 0x01, 0x02, 0x03, 0x06   # module 3
CMD_CLIENT_DONE, CMD_HOST_DONE, CMD_LEAVE = 0x0B, 0x0C, 0x09
CMD_CLIENT_ACCEPT = 0x05      # module 3: sent with the client's "yes", in the same message as its module 2 Pokemon

SHOW_BROWSING, SHOW_CHOSEN = 0x0C, 0x0A
HOST_READY = b"\x01"
CLIENT_YES = bytes.fromhex("01000000")
BOTH_YES = bytes.fromhex("01010100")

NAME_BYTES = 26                                        # 12 UTF-16 characters and a terminator
PROFILE_ID32, PROFILE_VERSION, PROFILE_ID8, PROFILE_PID, PROFILE_NAME = 0x00, 0x04, 0x18, 0x28, 0x38
PERSON_TIME, PERSON_NAME, PERSON_PID, PERSON_ID8 = 0x04, 0x08, 0x24, 0x28
EPOCH_2000 = 946684800

VERSION_CODES = {"Sun": 30, "Moon": 31, "Ultra Sun": 32, "Ultra Moon": 33}


@dataclass
class Trainer:
    """Who PokeUDS is in the Festival Plaza: the OT name and ids, and the two ids the plaza knows a player by."""

    name: str = "PokeUDS"
    tid: int = 12345
    sid: int = 54321
    principal_id: int = field(default_factory=lambda: int.from_bytes(os.urandom(4), "little") & 0x3FFFFFFF or 1)
    id8: bytes = field(default_factory=lambda: os.urandom(8))
    version: int = VERSION_CODES["Moon"]

    @property
    def id32(self) -> int:
        return (self.sid << 16) | self.tid


def _name(text: str) -> bytes:
    return text[:12].encode("utf-16-le").ljust(NAME_BYTES, b"\0")


def stamp(now: float | None = None) -> int:
    """Seconds since 2000-01-01, as the person block and the plaza's records carry it."""
    return int((time.time() if now is None else now) - EPOCH_2000) & 0xFFFFFFFF


def netz_host(call_id: int, nonce: bytes) -> list[bytes]:
    out = [bytearray(gen6.template(t)) for t in templates.NETZ_HOST]
    out[1][gen6.NETZ_CALL_OFFSET] = out[2][gen6.NETZ_CALL_OFFSET] = call_id
    out[4][gen6.NETZ_NONCE] = nonce
    return [bytes(m) for m in out]


def netz_client(call_id: int) -> list[bytes]:
    out = [bytearray(gen6.template(t)) for t in templates.NETZ_CLIENT]
    out[2][gen6.NETZ_CALL_OFFSET] = out[3][gen6.NETZ_CALL_OFFSET] = call_id
    return [bytes(m) for m in out]


def host_init() -> list[bytes]:
    return [gen6.template(templates.HOST_INIT, 24), gen6.template(templates.HOST_INIT2, 24)]


def profile(trainer: Trainer) -> bytes:
    b = bytearray(gen6.template(templates.PROFILE, templates.PROFILE_SIZE))
    struct.pack_into("<I", b, PROFILE_ID32, trainer.id32)
    b[PROFILE_VERSION] = trainer.version
    b[PROFILE_ID8:PROFILE_ID8 + 8] = trainer.id8
    struct.pack_into("<I", b, PROFILE_PID, trainer.principal_id)
    b[PROFILE_NAME:PROFILE_NAME + NAME_BYTES] = _name(trainer.name)
    return bytes(b)


def person(trainer: Trainer, now: float | None = None, clock: int | None = None) -> bytes:
    """The person block. ``clock`` (seconds since 2000) is the invite's token when PokeUDS invited, as a console's
    person block carries it; otherwise the time now."""
    b = bytearray(gen6.template(templates.PERSON, templates.PERSON_SIZE))
    struct.pack_into("<I", b, PERSON_TIME, stamp(now) if clock is None else clock & 0xFFFFFFFF)
    b[PERSON_NAME:PERSON_NAME + NAME_BYTES] = _name(trainer.name)
    struct.pack_into("<I", b, PERSON_PID, trainer.principal_id)
    b[PERSON_ID8:PERSON_ID8 + 8] = trainer.id8
    return bytes(b)


def show(record: bytes, species: int, chosen: bool) -> bytes:
    """Module 1 command 00: the Pokemon on show (``chosen``: the one offered), encrypted party form."""
    return struct.pack("<II", SHOW_CHOSEN if chosen else SHOW_BROWSING, species) + record


def player_of(profile_block: bytes) -> tuple[str, int]:
    """The name and principal id in a console's profile block."""
    name = profile_block[PROFILE_NAME:PROFILE_NAME + NAME_BYTES].decode("utf-16-le", "replace").split("\0")[0]
    return name, struct.unpack_from("<I", profile_block, PROFILE_PID)[0]
