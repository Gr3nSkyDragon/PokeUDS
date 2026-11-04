"""Radiotap headers for monitor-mode capture and injection (ldnd backend)."""

from __future__ import annotations

import struct
from dataclasses import dataclass

PRESENT_FLAGS = 1 << 1
PRESENT_RATE = 1 << 2
PRESENT_TX_FLAGS = 1 << 15
PRESENT_EXT = 1 << 31
FLAG_FCS = 0x10
TX_NOACK = 0x0008

# (alignment, size) of radiotap fields 0..22, enough to walk to the fields we read.
_FIELDS = {
    0: (8, 8), 1: (1, 1), 2: (1, 1), 3: (2, 4), 4: (1, 2), 5: (1, 1), 6: (1, 1), 7: (2, 2),
    8: (2, 2), 9: (2, 2), 10: (1, 1), 11: (1, 1), 12: (1, 1), 13: (1, 1), 14: (2, 2),
    15: (2, 2), 16: (1, 1), 17: (1, 1), 18: (4, 8), 19: (1, 3), 20: (4, 8), 21: (2, 12),
    22: (8, 12),
}


@dataclass
class Parsed:
    length: int
    has_fcs: bool = False
    channel_freq: int | None = None
    signal: int | None = None


def parse(packet: bytes) -> Parsed | None:
    if len(packet) < 8 or packet[0] != 0:
        return None
    length = struct.unpack_from("<H", packet, 2)[0]
    if length < 8 or length > len(packet):
        return None
    words = []
    offset = 4
    while True:
        if offset + 4 > length:
            return Parsed(length)
        (word,) = struct.unpack_from("<I", packet, offset)
        words.append(word)
        offset += 4
        if not word & PRESENT_EXT:
            break
    result = Parsed(length)
    present = words[0]
    for bit in range(23):
        if not present & (1 << bit):
            continue
        align, size = _FIELDS[bit]
        offset = (offset + align - 1) & ~(align - 1)
        if offset + size > length:
            break
        if bit == 1:
            result.has_fcs = bool(packet[offset] & FLAG_FCS)
        elif bit == 3:
            result.channel_freq = struct.unpack_from("<H", packet, offset)[0]
        elif bit == 5:
            result.signal = struct.unpack_from("b", packet, offset)[0]
        offset += size
    return result


def strip(packet: bytes) -> tuple[bytes, Parsed] | None:
    """The MPDU without radiotap or FCS, and the parsed header."""
    parsed = parse(packet)
    if parsed is None:
        return None
    mpdu = packet[parsed.length:]
    if parsed.has_fcs and len(mpdu) >= 28:
        mpdu = mpdu[:-4]
    return mpdu, parsed


def build(mpdu: bytes, *, rate: int = 0, no_ack: bool = False) -> bytes:
    present, body = 0, b""
    if rate:
        present |= PRESENT_RATE
        body += bytes([rate])
    if no_ack:
        present |= PRESENT_TX_FLAGS
        if len(body) % 2:
            body += b"\0"  # TX flags are 2-byte aligned.
        body += struct.pack("<H", TX_NOACK)
    return struct.pack("<BBHI", 0, 0, 8 + len(body), present) + body + mpdu


def frequency_to_channel(freq: int | None) -> int | None:
    if freq is None:
        return None
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    return None
