"""Serial framing between the host and the ESP32-S3 UDS radio firmware.

The firmware is VC-Adapter's ``firmware/esp32-uds-bridge`` (the same board and firmware azahar_uds
uses). This module mirrors azahar_uds ``uds_real/esp32_wire.{h,cpp}``; keep them in step.

    raw frame = | version=1 | type | seq | flags | length:u16 LE | payload | crc32:u32 LE |

The CRC is CRC-32/ISO-HDLC (zlib) over everything before it. The raw frame is COBS encoded and
terminated by one 0x00 byte.
"""

from __future__ import annotations

import enum
import struct
import zlib
from dataclasses import dataclass

WIRE_VERSION = 1
HEADER_SIZE = 6
MAX_PAYLOAD = 2432  # Largest 802.11 MPDU plus the small per-type headers.
MAX_RAW_FRAME = HEADER_SIZE + MAX_PAYLOAD + 4


class Type(enum.IntEnum):
    # Host -> device.
    HELLO = 0x01        # {} -> HELLO_ACK
    START = 0x02        # {channel:u8, mac[6], twin:u8} -> STATUS
    STOP = 0x03         # {} -> STATUS
    SET_CHANNEL = 0x04  # {channel:u8} -> STATUS
    TX_FRAME = 0x06     # {tx_flags:u8, rate500k:u8, mpdu...} -> (TX_DONE for unicast)
    SET_BEACON = 0x07   # {mpdu...} periodic beacon template; empty clears -> STATUS
    SET_WATCH = 0x08    # {mac[6]} also forward frames touching this address -> STATUS
    PING = 0x09         # {token:u32} -> PONG

    # Device -> host.
    HELLO_ACK = 0x81    # {proto:u8, fw_major:u8, fw_minor:u8, factory_mac[6]}
    STATUS = 0x82       # {request_type:u8, result:s32 LE}, echoes the request's seq
    RX = 0x83           # {channel:u8, rssi:s8, rx_flags:u8, mpdu...}
    LOG = 0x84          # {text...}
    STATS = 0x85        # 7 or 9 x u32 LE
    PONG = 0x86         # {token:u32}
    TX_DONE = 0x87      # {acked:u8, length:u16 LE, first bytes of the frame}


TX_NO_ACK = 1 << 0       # Group addressed: do not wait for an ACK.
RX_TRUNCATED = 1 << 0    # The radio delivered fewer bytes than the frame's length.


@dataclass
class Frame:
    type: int
    seq: int
    flags: int
    payload: bytes


@dataclass
class DeviceStats:
    rx_seen: int
    rx_forwarded: int
    rx_dropped: int
    tx_ok: int
    tx_failed: int
    beacons_sent: int
    usb_dropped: int
    tx_unicast: int = 0
    acks_rx: int = 0

    @classmethod
    def parse(cls, payload: bytes) -> "DeviceStats | None":
        if len(payload) < 28:
            return None
        count = 9 if len(payload) >= 36 else 7
        return cls(*struct.unpack_from(f"<{count}I", payload))


def cobs_encode(data: bytes) -> bytes:
    out = bytearray([0])
    code_index, code = 0, 1
    for byte in data:
        if byte == 0:
            out[code_index] = code
            code_index, code = len(out), 1
            out.append(0)
        else:
            out.append(byte)
            code += 1
            if code == 0xFF:
                out[code_index] = code
                code_index, code = len(out), 1
                out.append(0)
    out[code_index] = code
    return bytes(out)


def cobs_decode(data: bytes) -> bytes | None:
    out = bytearray()
    index = 0
    while index < len(data):
        code = data[index]
        index += 1
        if code == 0:
            return None
        run = code - 1
        if index + run > len(data):
            return None
        out += data[index:index + run]
        index += run
        if code != 0xFF and index < len(data):
            out.append(0)
    return bytes(out)


def encode(type_: int, seq: int, flags: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"ESP32 payload of {len(payload)} bytes exceeds {MAX_PAYLOAD}")
    raw = struct.pack("<BBBBH", WIRE_VERSION, int(type_), seq & 0xFF, flags & 0xFF, len(payload))
    raw += payload
    raw += struct.pack("<I", zlib.crc32(raw) & 0xFFFFFFFF)
    return cobs_encode(raw) + b"\0"


class Decoder:
    """Incremental decoder: feed bytes, complete valid frames come out."""

    def __init__(self) -> None:
        self._pending = bytearray()
        self._overflowed = False
        self.bad_frames = 0

    def feed(self, data: bytes) -> list[Frame]:
        frames: list[Frame] = []
        limit = MAX_RAW_FRAME + MAX_RAW_FRAME // 254 + 4
        for byte in data:
            if byte != 0:
                if len(self._pending) > limit:
                    self._overflowed = True
                elif not self._overflowed:
                    self._pending.append(byte)
                continue
            had_overflow, self._overflowed = self._overflowed, False
            if not self._pending and not had_overflow:
                continue  # Idle delimiter.
            raw = None if had_overflow else cobs_decode(bytes(self._pending))
            self._pending.clear()
            frame = self._parse(raw)
            if frame is None:
                self.bad_frames += 1
            else:
                frames.append(frame)
        return frames

    @staticmethod
    def _parse(raw: bytes | None) -> Frame | None:
        if raw is None or len(raw) < HEADER_SIZE + 4 or raw[0] != WIRE_VERSION:
            return None
        length = struct.unpack_from("<H", raw, 4)[0]
        if length > MAX_PAYLOAD or len(raw) != HEADER_SIZE + length + 4:
            return None
        (expected,) = struct.unpack_from("<I", raw, len(raw) - 4)
        if zlib.crc32(raw[:-4]) & 0xFFFFFFFF != expected:
            return None
        return Frame(raw[1], raw[2], raw[3], bytes(raw[HEADER_SIZE:HEADER_SIZE + length]))
