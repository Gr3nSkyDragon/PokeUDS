"""The Generation 6 trade's own messages: gflnet framing, syncs, commands, the profile, PSS beacons.

Above PRUDP (``pokeuds.nex.prudp``) the game runs GameFreak's gflnet. Each gflnet message is one
reliable PRUDP message on the game stream::

    u16 CRC-16/CCITT-FALSE of everything after it | u16 body length | u32 index | u32 0x201
    | u32 received counts (byte 0: node 1's, byte 1: node 2's) | u32 0x201 | body

Generation 7 (Sun/Moon's gflnet2) frames messages the same way with two differences, read off Azahar logs of
Sun/Moon trades (every message of two sessions verifies): the checksum is CRC-16/USB (polynomial 0x8005 reflected,
init and final XOR 0xFFFF) over the same bytes, and the first node-pair word is ``01 02 ff ff``.

``index`` numbers the sender's messages that carry a body. After each one the sender, and the
receiver, send an empty message that reports the counts (the sender counts its own message too).

Bodies are either a sync point, ``[02 or 03][module][00 00][u32 2][u16 id]`` (each side sends 02;
the host answers 03 once it has both), or a command, ``[01][module][00][command][u32 length][data]``.
Module 0 is the session (the PSS profile is its command 06), module 1 the trade:

| command | data | meaning (from the captures) |
|---|---|---|
| 01 | 260 bytes | the offered Pokemon, party format, encrypted |
| 05 | ``01 e2`` | leave the trade screen |
| 06 | ``67 e2`` | sent right after an offer |
| 07 | ``01 e2`` | confirm the trade |
| 08 | 8 bytes | screen state; ``00 00 01 00 ...`` from the side that offers first |
| 09 | 964 bytes | the trade screen opens (X sends zeros past the first words) |
| 0a | ``01 b6`` | the swap is done |
| 0b | ``xx 00`` | host only, after a trade |
| 00 | 552 bytes | leaving: the sender's PSS id, then zeros |

PSS beacons are the UDS application data of the ``id=2`` networks every console in the PSS keeps
hosting. gflnet's BeaconManager frames them as ``[CRC-16][game][record count][version][02 02]
[0x41][PSS id 8]`` followed by records ``[length+4][type][sub][0] data``; the PSS sends one record,
type 4 sub 1, the player (152 bytes, the same as the profile from 0x1A0). Its bytes at beacon
offsets 0x84-0x99 carry a trade request: the requester puts a token (its own nonce from 0x18) and
the target's key and PSS id there with 0x94 = 1 and 0x96 = 01, and its state byte at 0x17 says
"asking"; the target answers with the same token, the requester's key and id, and state "answering".
The requester then hosts the trade network (``id=1``, four nodes) for the target to join; its application data is
the request beacon again in state "trading" (0x98). Read off Pokemon X asking a retail Y in Azahar and retail Y
asking PokeUDS on hardware; an idle beacon holds other values there, so only a match on our own PSS id counts as a request.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from . import templates

GFL_HEADER = 20
NODE_PAIR = 0x201
NODE_PAIR_GEN7 = 0xFFFF0201     # Sun/Moon's first node-pair word: 01 02 ff ff
SYNC_REQUEST, SYNC_DONE = 2, 3
SESSION, TRADE = 0, 1

CMD_LEAVE_DATA, CMD_POKEMON, CMD_QUIT, CMD_OFFERED, CMD_CONFIRM, CMD_STATE, CMD_SCREEN, CMD_DONE, CMD_AFTER = (
    0x00, 0x01, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B)
CMD_PROFILE = 0x06          # In module 0.

SYNC_START, SYNC_PROFILES, SYNC_HUB, SYNC_LEAVE, SYNC_END = 0x11, 0x13, 0x81, 0x0A, 0x16
SYNC_AFTER_TRADE = (0xCB, 0xCC, 0xCD)   # Module 1, after each trade.

STATE_IDLE = bytes.fromhex("0000000001000000")
STATE_OFFERING = bytes.fromhex("0000010001000000")
OFFERED = bytes.fromhex("67e2")
YES = bytes.fromhex("01e2")
DONE = bytes.fromhex("01b6")


def template(hex_text: str, size: int | None = None) -> bytes:
    data = bytes.fromhex(hex_text)
    return data.ljust(size, b"\0") if size else data


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


# -- gflnet ---------------------------------------------------------------------------------------

def crc16_usb(data: bytes) -> int:
    """CRC-16/USB: polynomial 0x8005 reflected, init 0xFFFF, final XOR 0xFFFF (Generation 7's gflnet2)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def gfl_encode(index: int, counts: int, body: bytes = b"", generation: int = 6) -> bytes:
    first = NODE_PAIR_GEN7 if generation == 7 else NODE_PAIR
    rest = struct.pack("<HIIII", len(body), index, first, counts, NODE_PAIR) + body
    return struct.pack("<H", crc16_usb(rest) if generation == 7 else crc16(rest)) + rest


@dataclass
class GflMessage:
    index: int
    counts: int
    body: bytes
    generation: int = 6


def gfl_decode(data: bytes) -> GflMessage:
    """A gflnet message of either generation (told apart by which checksum holds)."""
    if len(data) < GFL_HEADER:
        raise ValueError("short gflnet message")
    crc, size, index, _, counts, _ = struct.unpack_from("<HHIIII", data)
    if size != len(data) - GFL_HEADER:
        raise ValueError("gflnet message fails its CRC or length")
    if crc == crc16(data[2:]):
        return GflMessage(index, counts, data[GFL_HEADER:], 6)
    if crc == crc16_usb(data[2:]):
        return GflMessage(index, counts, data[GFL_HEADER:], 7)
    raise ValueError("gflnet message fails its CRC or length")


def counts_word(node1: int, node2: int) -> int:
    return (node1 & 0xFF) | ((node2 & 0xFF) << 8)


def sync(kind: int, module: int, ident: int) -> bytes:
    return bytes([kind, module, 0, 0]) + struct.pack("<IH", 2, ident)


def command(module: int, cmd: int, data: bytes) -> bytes:
    return bytes([1, module, 0, cmd]) + struct.pack("<I", len(data)) + data


@dataclass
class Body:
    kind: str            # "sync", "command", "init" or "other"
    module: int = 0
    ident: int = 0       # The sync id or the command.
    done: bool = False   # A sync's 03.
    data: bytes = b""


def parse_bodies(body: bytes) -> list[Body]:
    """Every sync and command in one gflnet message: a console may pack several into one (Sun/Moon sent the Pokemon
    under its cursor and its offer as one message), so reading only the first loses the rest."""
    out: list[Body] = []
    rest = body
    while rest:
        if len(rest) >= 10 and rest[0] in (SYNC_REQUEST, SYNC_DONE):
            out.append(parse_body(rest[:10]))
            rest = rest[10:]
        elif len(rest) >= 8 and rest[0] == 1:
            size = struct.unpack_from("<I", rest, 4)[0]
            out.append(parse_body(rest[:8 + size]))
            rest = rest[8 + size:]
        else:
            out.append(parse_body(rest))
            break
    return out


def parse_body(body: bytes) -> Body:
    if len(body) == 10 and body[0] in (SYNC_REQUEST, SYNC_DONE):
        return Body("sync", body[1], struct.unpack_from("<H", body, 8)[0], body[0] == SYNC_DONE)
    if len(body) >= 8 and body[0] == 1:
        size = struct.unpack_from("<I", body, 4)[0]
        return Body("command", body[1], body[3], data=body[8:8 + size])
    if body[:1] == b"\x04":
        return Body("init", data=body)
    return Body("other", data=body)


# -- trainer, profile -----------------------------------------------------------------------------

VERSION_CODES = {"X": 24, "Y": 25, "Alpha Sapphire": 26, "Omega Ruby": 27}

# Offsets inside the profile (command 06's data). The player record of the PSS beacon is a copy of
# the profile from PLAYER_RECORD on.
P_TID, P_SID, P_VERSION, P_ID, P_KEY, P_NAME, P_USER_NAME, P_PARTNER_ID, P_STATION = (
    0x00, 0x02, 0x04, 0x10, 0x20, 0x48, 0x62, 0x170, 0x188)
PLAYER_RECORD = 0x1A0
NAME_BYTES = 26             # 12 characters and a terminator, UTF-16LE.

# Offsets inside the player record (beacon offset = record offset + 0x14).
R_NAME, R_KEY, R_ID, R_VERSION, R_TOKEN, R_PARTNER_KEY, R_PARTNER_ID, R_REQUEST, R_REQUEST_KIND = (
    0x08, 0x44, 0x48, 0x5A, 0x70, 0x74, 0x78, 0x80, 0x82)
RECORD_SIZE = 152
# The player's state and a nonce, at record 0x03-0x07 (beacon 0x17-0x1B). Seen on retail X/Y: idle 0x05; asking
# 0x96, with a fresh nonce that is also the request's token; and the emulated X's accepted answer carried 0x99. An
# answer left at 0x05 reads as an idle player and the asking console waits on, then says the trainer is unavailable.
R_STATE, R_NONCE = 0x03, 0x04
PSS_IDLE, PSS_ASKING, PSS_ANSWERING = 0x05, 0x96, 0x99
# The asker's record once the answer is in, as the application data of the trade network it hosts: Pokemon X in
# Azahar (2026-10-17) put its request beacon there with state 0x98 and a new nonce (byte 0 moved on, the token kept),
# and the retail console it asked joined that network 3.7 s later. A trade network without it was never joined.
PSS_TRADING = 0x98
# Beacon 0x96 on a retail trade request (0x97-0x98 keep their idle 44 44): 01, read off retail Y asking PokeUDS on
# hardware (pokeuds-run.log, 2026-11-05). PokeUDS used to send 02, which no capture shows.
REQUEST_KIND_TRADE = bytes.fromhex("01")
IDLE_KIND = bytes.fromhex("444444")

# The NetworkInfo attributes every console's PSS and trade networks carry (bit 15, the 3DS default for a network a
# game hosts); a PSS network without it was listed with Trade greyed out.
NETWORK_ATTRIBUTES = 0x8000



def _name(text: str) -> bytes:
    return text[:12].encode("utf-16-le").ljust(NAME_BYTES, b"\0")


@dataclass
class Trainer:
    """Who PokeUDS appears as in the PSS and on the trade screen."""

    name: str
    tid: int
    sid: int
    pss_id: bytes                 # 8 bytes, as a console's PSS id; random and kept per install
    key: bytes                    # 4 bytes the PSS pairs with the id
    version: int = VERSION_CODES["X"]
    user_name: str = "PokeUDS"    # The console name (the UDS node name)

    @classmethod
    def new(cls, name: str, tid: int, sid: int, version: int = VERSION_CODES["X"], user_name: str = "PokeUDS") -> "Trainer":
        return cls(name, tid, sid, os.urandom(8), os.urandom(4), version, user_name)


def profile(trainer: Trainer, partner_id: bytes = bytes(8), node: int = 1) -> bytes:
    p = bytearray(template(templates.PROFILE, templates.PROFILE_SIZE))
    struct.pack_into("<HH", p, P_TID, trainer.tid & 0xFFFF, trainer.sid & 0xFFFF)
    p[P_VERSION] = trainer.version
    p[P_ID:P_ID + 8] = trainer.pss_id
    p[P_KEY:P_KEY + 4] = trainer.key
    p[P_NAME:P_NAME + NAME_BYTES] = _name(trainer.name)
    p[P_USER_NAME:P_USER_NAME + NAME_BYTES] = _name(trainer.user_name)
    p[P_PARTNER_ID:P_PARTNER_ID + 8] = partner_id
    p[P_STATION] = node
    record = player_record(trainer)
    p[PLAYER_RECORD:PLAYER_RECORD + RECORD_SIZE] = record
    p[PLAYER_RECORD + R_PARTNER_ID:PLAYER_RECORD + R_PARTNER_ID + 8] = partner_id
    return bytes(p)


def player_record(trainer: Trainer) -> bytes:
    beacon = template(templates.IDLE_BEACON)
    r = bytearray(beacon[0x14:0x14 + RECORD_SIZE])
    r[R_NAME:R_NAME + NAME_BYTES] = _name(trainer.name)
    r[R_KEY:R_KEY + 4] = trainer.key
    r[R_ID:R_ID + 8] = trainer.pss_id
    r[R_VERSION] = trainer.version
    return bytes(r)


def leave_data(trainer: Trainer) -> bytes:
    return trainer.pss_id.ljust(templates.LEAVE_SIZE, b"\0")


def screen_block() -> bytes:
    return template(templates.BLOCK09, templates.BLOCK09_SIZE)


# -- PSS beacons ----------------------------------------------------------------------------------

@dataclass
class PssPlayer:
    pss_id: bytes
    name: str
    version: int
    key: bytes
    token: bytes
    partner_key: bytes
    partner_id: bytes
    request: int
    request_kind: bytes
    state: int = PSS_IDLE

    def wants(self, pss_id: bytes) -> bool:
        """This player asks ``pss_id`` for a trade."""
        return self.partner_id == pss_id and self.request == 1

    def answers(self, pss_id: bytes) -> bool:
        """This player accepts a request from ``pss_id``: state "answering" and ``pss_id`` named as its partner. The
        partner bytes alone are not enough: an idle beacon keeps old values there, and one still naming PokeUDS
        (from an earlier trade, say) read as an answer before the player had accepted anything."""
        return self.state == PSS_ANSWERING and self.partner_id == pss_id


def pss_beacon(trainer: Trainer, *, partner: PssPlayer | None = None, request: bool = False,
               token: bytes | None = None) -> bytes:
    """Our PSS beacon: idle, a trade request to ``partner`` (``request``), or the answer to its request."""
    b = bytearray(template(templates.IDLE_BEACON))
    b[4] = trainer.version
    b[8:16] = trainer.pss_id
    base = 0x14
    b[base:base + RECORD_SIZE] = player_record(trainer)
    if partner is not None:
        if request:
            # Our own nonce doubles as the request's token, as on a retail request.
            nonce = token if token is not None else os.urandom(4)
            b[base + R_NONCE:base + R_NONCE + 4] = nonce
            b[base + R_TOKEN:base + R_TOKEN + 4] = nonce
            b[base + R_STATE] = PSS_ASKING
            b[base + R_REQUEST] = 1
            b[base + R_REQUEST_KIND:base + R_REQUEST_KIND + len(REQUEST_KIND_TRADE)] = REQUEST_KIND_TRADE
        else:
            b[base + R_TOKEN:base + R_TOKEN + 4] = token if token is not None else partner.token
            b[base + R_NONCE] = os.urandom(1)[0]
            b[base + R_STATE] = PSS_ANSWERING
        b[base + R_PARTNER_KEY:base + R_PARTNER_KEY + 4] = partner.key
        b[base + R_PARTNER_ID:base + R_PARTNER_ID + 8] = partner.pss_id
    struct.pack_into("<H", b, 0, crc16(bytes(b[2:])))
    return bytes(b)


def trade_network_data(trainer: Trainer, partner: PssPlayer, token: bytes) -> bytes:
    """The application data of the trade network the asker hosts: its request beacon to ``partner`` with ``token``,
    in state PSS_TRADING, with a new nonce byte as the game's had."""
    b = bytearray(pss_beacon(trainer, partner=partner, request=True, token=token))
    base = 0x14
    b[base + R_STATE] = PSS_TRADING
    b[base + R_NONCE] = (token[0] + 1 + os.urandom(1)[0] % 0xFE) & 0xFF      # anything but the token's own byte
    struct.pack_into("<H", b, 0, crc16(bytes(b[2:])))
    return bytes(b)


def parse_pss_beacon(data: bytes) -> PssPlayer | None:
    if len(data) < 0x14 + RECORD_SIZE or struct.unpack_from("<H", data)[0] != crc16(data[2:]):
        return None
    if data[7] & 0x3F != 1 or data[17] != 4:
        return None
    r = data[0x14:0x14 + RECORD_SIZE]
    name = r[R_NAME:R_NAME + NAME_BYTES].decode("utf-16-le", "replace").split("\0", 1)[0]
    return PssPlayer(bytes(data[8:16]), name, r[R_VERSION], bytes(r[R_KEY:R_KEY + 4]), bytes(r[R_TOKEN:R_TOKEN + 4]),
                     bytes(r[R_PARTNER_KEY:R_PARTNER_KEY + 4]), bytes(r[R_PARTNER_ID:R_PARTNER_ID + 8]), r[R_REQUEST],
                     bytes(r[R_REQUEST_KIND:R_REQUEST_KIND + 3]), r[R_STATE])


# -- Net-Z ----------------------------------------------------------------------------------------

NETZ_CALL_OFFSET = 6        # In host messages 1 and 2, echoed in client messages 2 and 3.
NETZ_NONCE = slice(55, 59)  # In host message 4: a per-session value.
HOST_CONNECT_PAYLOAD = bytes.fromhex("080000000100c0000200c000")
CLIENT_CONNECT_PAYLOAD = bytes.fromhex("080000000000000000000000")
HB_COUNTER, HB_TIME, HB_HOST_ECHO, HB_HOST_TIME = 6, 22, 22, 32


def netz_host(call_id: int, nonce: bytes) -> list[bytes]:
    out = [bytearray(template(t)) for t in templates.NETZ_HOST]
    out[1][NETZ_CALL_OFFSET] = out[2][NETZ_CALL_OFFSET] = call_id
    out[4][NETZ_NONCE] = nonce
    return [bytes(m) for m in out]


def netz_client(call_id: int) -> list[bytes]:
    out = [bytearray(template(t)) for t in templates.NETZ_CLIENT]
    out[2][NETZ_CALL_OFFSET] = out[3][NETZ_CALL_OFFSET] = call_id
    return [bytes(m) for m in out]


def netz_kind(message: bytes) -> int:
    """The Net-Z message type (byte 5, after the length and a zero)."""
    return message[5] if len(message) > 5 else -1


HB_OBJECT = 16             # The Net-Z object the heartbeats belong to: 3 in X/Y, 4 in Sun/Moon (one object more).


def heartbeat_client(counter: int, now_ms: int, obj: int = 3) -> bytes:
    m = bytearray(template(templates.HEARTBEAT_CLIENT))
    m[HB_OBJECT] = obj
    struct.pack_into("<H", m, HB_COUNTER, counter & 0xFFFF)
    struct.pack_into("<I", m, HB_TIME, now_ms & 0xFFFFFFFF)
    return bytes(m)


def heartbeat_host(counter: int, echo: bytes, now_ms: int, obj: int = 3) -> bytes:
    m = bytearray(template(templates.HEARTBEAT_HOST))
    m[HB_OBJECT] = obj
    struct.pack_into("<H", m, HB_COUNTER, counter & 0xFFFF)
    m[HB_HOST_ECHO:HB_HOST_ECHO + 4] = echo
    struct.pack_into("<I", m, HB_HOST_TIME, now_ms & 0xFFFFFFFF)
    return bytes(m)


def host_keepalive(counter: int, goodbye: bool = False) -> bytes:
    m = bytearray(template(templates.HOST_GOODBYE if goodbye else templates.HOST_KEEPALIVE))
    struct.pack_into("<H", m, HB_COUNTER, counter & 0xFFFF)
    return bytes(m)


def is_host_goodbye(message: bytes) -> bool:
    """The host's last Net-Z message of a session (``templates.HOST_GOODBYE``, with its own counter)."""
    goodbye = template(templates.HOST_GOODBYE)
    return len(message) == len(goodbye) and message[:6] == goodbye[:6] and message[8:] == goodbye[8:]


def is_heartbeat(message: bytes) -> bool:
    return netz_kind(message) == 0x0A and len(message) in (30, 42)
