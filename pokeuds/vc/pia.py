"""The datagrams 3DS Virtual Console Game Boy titles exchange over UDS (data channel 243).

The VC runs an old Pia (``nn::pia`` 5.6 or earlier, unencrypted) inside the app. Everything here
is measured from retail and Azahar captures; the evidence is in mgba_LDN ``wiki/vc_link.md``
(Gr3nSkyDragon). One UDS payload is one frame:

    0   01 kind              kind: 01 Pia data, 11 host hello, 21 joiner hello reply, 12 bye
    2   u16 LE               frame length - 12 (hello: always 0x18)
    4   6 zeros
    10  u16 LE               CRC-16/ARC of bytes 0..9
    --- Pia data frames only ---
    12  32 AB 98 64          Pia magic
    16  01                   "not encrypted"
    17  connection id        01 joiner / 00 host before the mesh state, then the station constant id
    18  u16 BE packet id     0 before the mesh state, then counting from 1
    20  u16 BE               sender's clock in ms
    22  u16 BE               sender's estimate of the peer's clock
    24  messages             each a 20-byte header and a payload padded to 4 bytes
    -16 16 bytes             HMAC-MD5(key "PokemonSIO", bytes 12 .. end-16)

Message header: 00, sender station index (host 00, joiner 01, joiner FD until assigned), u16 BE
payload length, u32 BE destination station id (0 broadcast, 1 host, 2 joiner), 4 zeros, protocol,
subtype (C0 keep-alive, 10 clock sync), 00, reliable flag, 4 zeros.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass, field

UDS_CHANNEL = 243
KEY = b"PokemonSIO"
MAGIC = bytes.fromhex("32AB9864")

KIND_PIA, KIND_HELLO, KIND_BYE, KIND_HELLO_REPLY = 0x01, 0x11, 0x12, 0x21
PROTO_KEEPALIVE, PROTO_SETUP, PROTO_SYSTEM, PROTO_PING, PROTO_GAME = 0x00, 0x01, 0x02, 0x06, 0x30
SUBTYPE_KEEPALIVE, SUBTYPE_CLOCK_SYNC = 0xC0, 0x10
STATION_HOST, STATION_JOINER, STATION_UNASSIGNED = 0x00, 0x01, 0xFD
ID_BROADCAST, ID_HOST, ID_JOINER = 0, 1, 2

PREFIX_SIZE = 12
HEADER_SIZE = 24
TAIL_SIZE = 16
MESSAGE_HEADER = 20
MAX_FRAME = 1440           # 25 game units, the largest frame the VC sends
UNIT_WINDOW = 25
FIRST_INDEX = -2001        # every reliable stream starts here


def crc16_arc(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def tag(data: bytes) -> bytes:
    return hmac.new(KEY, data, hashlib.md5).digest()


def _prefix(kind: int, length_field: int) -> bytes:
    head = bytes([0x01, kind]) + struct.pack("<H", length_field) + bytes(6)
    return head + struct.pack("<H", crc16_arc(head))


@dataclass
class Message:
    protocol: int
    payload: bytes = b""
    destination: int = ID_BROADCAST
    sender: int = STATION_UNASSIGNED
    subtype: int = 0
    reliable: int = 0

    def pack(self) -> bytes:
        head = struct.pack(">BBHI4xBBBB4x", 0, self.sender, len(self.payload), self.destination, self.protocol,
                           self.subtype, 0, self.reliable)
        return head + self.payload + bytes(-len(self.payload) % 4)


@dataclass
class Frame:
    kind: int
    crc_ok: bool
    connection_id: int = 0
    packet_id: int = 0
    clock: int = 0
    peer_clock: int = 0
    messages: list[Message] = field(default_factory=list)
    tail_ok: bool = True
    hello_value: int = 0


def build(messages: list[Message], *, connection_id: int, packet_id: int, clock: int, peer_clock: int) -> bytes:
    body = MAGIC + bytes([0x01, connection_id]) + struct.pack(">HHH", packet_id & 0xFFFF, clock & 0xFFFF, peer_clock & 0xFFFF)
    body += b"".join(m.pack() for m in messages)
    length = PREFIX_SIZE + len(body) + TAIL_SIZE
    if length > MAX_FRAME:
        raise ValueError(f"a {length}-byte frame exceeds the VC's {MAX_FRAME}")
    return _prefix(KIND_PIA, length - PREFIX_SIZE) + body + tag(body)


def hello(value: int) -> bytes:
    out = bytearray(_prefix(KIND_HELLO, 0x18) + bytes(40))
    out[12] = 0x02
    struct.pack_into(">I", out, 16, value)
    out[20] = 0x01
    out[28] = 0x01
    out[30] = 0x02
    return bytes(out)


def hello_reply() -> bytes:
    out = bytearray(_prefix(KIND_HELLO_REPLY, 0) + bytes(8))
    out[12] = 0x02
    return bytes(out)


def bye() -> bytes:
    return _prefix(KIND_BYE, 0) + bytes(4)


def parse(raw: bytes) -> Frame | None:
    if len(raw) < 16 or raw[0] != 0x01:
        return None
    crc_ok = crc16_arc(raw[:10]) == struct.unpack_from("<H", raw, 10)[0]
    kind = raw[1]
    if kind == KIND_HELLO:
        return Frame(kind, crc_ok, hello_value=struct.unpack_from(">I", raw, 16)[0] if len(raw) >= 20 else 0)
    if kind in (KIND_HELLO_REPLY, KIND_BYE):
        return Frame(kind, crc_ok)
    if kind != KIND_PIA or len(raw) < HEADER_SIZE + TAIL_SIZE:
        return None
    if struct.unpack_from("<H", raw, 2)[0] + PREFIX_SIZE != len(raw) or raw[12:16] != MAGIC:
        return None
    conn, packet_id, clock, peer_clock = raw[17], *struct.unpack_from(">HHH", raw, 18)
    frame = Frame(kind, crc_ok, conn, packet_id, clock, peer_clock,
                  tail_ok=hmac.compare_digest(tag(raw[PREFIX_SIZE:-TAIL_SIZE]), raw[-TAIL_SIZE:]))
    body, pos = raw[HEADER_SIZE:-TAIL_SIZE], 0
    while pos + MESSAGE_HEADER <= len(body):
        _z, sender, length, dest, proto, subtype, _r, reliable = struct.unpack_from(">BBHI4xBBBB4x", body, pos)
        end = pos + MESSAGE_HEADER + length
        if end > len(body):
            break
        frame.messages.append(Message(proto, body[pos + MESSAGE_HEADER:end], dest, sender, subtype, reliable))
        pos += MESSAGE_HEADER + length + (-length % 4)
    return frame


# -- reliable streams (game protocol 30, system protocol 02 with the reliable flag) -----------------

@dataclass
class Unit:
    """One game-stream record: one Game Boy serial exchange."""

    index: int
    ack: int
    byte: int
    role_flags: bytes = b"\x00\x00\x00"
    loss_bits: bytes = bytes(8)
    value: int = 0

    def pack(self) -> bytes:
        counter = (self.index - FIRST_INDEX) & 0xFFFF
        return (b"\x00\x03\x00\x0c" + bytes(4) + struct.pack(">ii", self.index, self.ack) + self.loss_bits
                + bytes([self.byte]) + self.role_flags + struct.pack(">I", self.value) + struct.pack("<H", counter) + bytes(2))

    @classmethod
    def unpack(cls, payload: bytes) -> "Unit | None":
        if len(payload) != 36 or payload[:4] != b"\x00\x03\x00\x0c":
            return None
        index, ack = struct.unpack_from(">ii", payload, 8)
        return cls(index, ack, payload[24], payload[25:28], payload[16:24], struct.unpack_from(">I", payload, 28)[0])


def record_ack(ack: int) -> bytes:
    """A reliable-stream acknowledgement: type 00 00, the ack index at offset 12."""
    return bytes(12) + struct.pack(">i", ack) + bytes(8)


def record_ack_index(payload: bytes) -> int | None:
    if len(payload) != 24 or payload[:2] != b"\x00\x00":
        return None
    return struct.unpack_from(">i", payload, 12)[0]


def profile_value(application_data: bytes) -> int:
    """The 32-bit value both profiles carry: bytes 4..7 of the host beacon's application data, LE."""
    return struct.unpack_from("<I", application_data.ljust(8, b"\0"), 4)[0]
