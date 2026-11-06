"""Festival Plaza beacons (Sun/Moon): being seen by other players, inviting one to trade, accepting an invite.

Every player in the plaza hosts a presence network (UDS id 2, four nodes) whose 200 bytes of application data say
who they are, and keeps changing that data. Layout, from Sun/Moon's own beacons:

| offset | field |
|---|---|
| 0 | CRC-16/USB of everything after it, padded with zeros to 200 bytes |
| 2 | ``c8`` presence, ``07`` the data of an asked player's trade network (184 bytes) |
| 8 | the player's 8-byte id |
| 20-127 | the head of the player's person block (``wire.person``): the clock (seconds since 2000) at 24, the name at 28, the principal id at 56, the 8-byte id again at 60 |
| 22 | which record bytes 132-179 hold: 7-11 the idle records a player cycles through, 3 an invite, 4 an accept |
| 128 | a per-player tag, then the 48-byte record |

An invite (record 3) is ``[token][target's principal id][target's 8-byte id][01 01 00 00]...``; the token is the
inviter's clock at that moment, and its person block carries it in the session. The invited player answers by
hosting the trade network (UDS id 1, four nodes) with record 4, ``[token][inviter's principal id][inviter's 8-byte
id]``, in its data; the inviter joins it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..gen6 import wire as gen6
from . import templates
from .wire import NAME_BYTES, Trainer, stamp

PRESENCE_NETWORK, TRADE_NETWORK = 2, 1
MAX_NODES = 4
KIND_PRESENCE, KIND_TRADE = 0xC8, 0x07
SLOT_INVITE, SLOT_ACCEPT = 0x03, 0x04
B_KIND, B_ID8, B_SLOT, B_CLOCK, B_NAME, B_PID, B_ID8_AGAIN, B_RECORD = 2, 8, 22, 24, 28, 56, 60, 132
R_TOKEN, R_PID, R_ID8, R_FLAG = 0, 4, 8, 16
INVITE_FLAG = b"\x01\x01"
# The NetworkInfo attributes every plaza network carries (as Generation 6's: bit 15).
NETWORK_ATTRIBUTES = gen6.NETWORK_ATTRIBUTES


BEACON_SIZE = 200


def _crc(data: bytes) -> int:
    """CRC-16/USB of everything after the checksum, the data padded with zeros to a presence beacon's 200 bytes (the
    184 bytes of a trade network's data are checked as if they were that long)."""
    return gen6.crc16_usb(bytes(data[2:]).ljust(BEACON_SIZE - 2, b"\0"))


def _seal(b: bytearray) -> bytes:
    struct.pack_into("<H", b, 0, _crc(b))
    return bytes(b)


def _stamp_player(b: bytearray, trainer: Trainer, now: float | None) -> None:
    b[B_ID8:B_ID8 + 8] = trainer.id8
    struct.pack_into("<I", b, B_CLOCK, stamp(now))
    b[B_NAME:B_NAME + NAME_BYTES] = trainer.name[:12].encode("utf-16-le").ljust(NAME_BYTES, b"\0")
    struct.pack_into("<I", b, B_PID, trainer.principal_id)
    b[B_ID8_AGAIN:B_ID8_AGAIN + 8] = trainer.id8


def presence(trainer: Trainer, turn: int, now: float | None = None) -> bytes:
    """Our presence beacon; ``turn`` picks the idle record (a player cycles through five)."""
    b = bytearray(gen6.template(templates.PLAZA_IDLE[turn % len(templates.PLAZA_IDLE)]))
    _stamp_player(b, trainer, now)
    return _seal(b)


def invite(trainer: Trainer, token: int, target_pid: int, target_id8: bytes, now: float | None = None) -> bytes:
    """Our presence beacon asking the player ``target_pid``/``target_id8`` for a trade."""
    b = bytearray(gen6.template(templates.PLAZA_INVITE))
    _stamp_player(b, trainer, now)
    r = B_RECORD
    struct.pack_into("<II", b, r + R_TOKEN, token, target_pid)
    b[r + R_ID8:r + R_ID8 + 8] = target_id8
    b[r + R_FLAG:r + R_FLAG + 2] = INVITE_FLAG
    return _seal(b)


def accept(trainer: Trainer, token: int, inviter_pid: int, inviter_id8: bytes, now: float | None = None) -> bytes:
    """The data of the trade network we host for the player who invited us."""
    b = bytearray(gen6.template(templates.PLAZA_ACCEPT))
    _stamp_player(b, trainer, now)
    r = B_RECORD
    struct.pack_into("<II", b, r + R_TOKEN, token, inviter_pid)
    b[r + R_ID8:r + R_ID8 + 8] = inviter_id8
    return _seal(b)


@dataclass
class Player:
    name: str
    principal_id: int
    id8: bytes
    slot: int
    kind: int
    token: int = 0           # the record's token (invite and accept records)
    other_pid: int = 0       # who the record names
    other_id8: bytes = b""

    def invites(self, trainer: Trainer) -> bool:
        return self.slot == SLOT_INVITE and self.other_pid == trainer.principal_id and self.other_id8 == trainer.id8

    def accepts(self, trainer: Trainer, token: int) -> bool:
        return (self.slot == SLOT_ACCEPT and self.token == token and self.other_pid == trainer.principal_id
                and self.other_id8 == trainer.id8)


def parse(data: bytes) -> Player | None:
    """A plaza beacon's player and record, or None when the data is not one (too short, checksum fails)."""
    if len(data) < B_RECORD + 16 or len(data) > BEACON_SIZE or struct.unpack_from("<H", data)[0] != _crc(data):
        return None
    name = data[B_NAME:B_NAME + NAME_BYTES].decode("utf-16-le", "replace").split("\0")[0]
    pid = struct.unpack_from("<I", data, B_PID)[0]
    player = Player(name, pid, bytes(data[B_ID8:B_ID8 + 8]), data[B_SLOT], data[B_KIND])
    if player.slot in (SLOT_INVITE, SLOT_ACCEPT):
        r = B_RECORD
        player.token, player.other_pid = struct.unpack_from("<II", data, r + R_TOKEN)
        player.other_id8 = bytes(data[r + R_ID8:r + R_ID8 + 8])
    return player
