"""Generation 6 local Mystery Gift: a distribution sent in beacons, as an official distribution is sent.

The receiving game (Mystery Gift, Get via Local Wireless) only scans: it never joins. The distributor hosts a
network of the game's own comm id (0x00055D10 for X/Y and ORAS alike), network id 2, and puts the gift in its beacons'
application data, a fragment at a time (docs/mystery_gift.md, "The Gen 6 distribution, on the air"). Read off a
real distribution of card 165; every byte of the reassembled message matched that event's ``.wc6full``.

Each beacon's 184 bytes of application data:

    0x00  2   CRC-16/CCITT-FALSE (init 0xFFFF) of bytes 0x02-0xB7, little-endian
    0x02  6   07 01 18 01 02 41 (constant; 0x18 is the header's size)
    0x08  8   a distribution id (the captured distributor's; its meaning is unknown)
    0x10  4   0x5A8 (constant in the capture; meaning unknown)
    0x14  2   the card's id
    0x16  1   fragment count
    0x17  1   this fragment's index
    0x18  160 the fragment: a slice of the message, the last one zero-padded

The message: u32 the full file's size (784), u32 the compressed size, then the ``.wc6full`` compressed with
Nintendo's LZ10 (0x10 and a 24-bit size, then flag bytes MSB first; a reference is 4 bits of length - 3 and 12
bits of distance - 1).
"""

from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass

APPDATA_SIZE = 0xB8
HEADER_SIZE = 0x18
PAYLOAD_SIZE = APPDATA_SIZE - HEADER_SIZE
MAGIC = bytes.fromhex("070118010241")
DISTRIBUTION_ID = bytes.fromhex("22e96d80edd99969")
DELIVERY_WORD = 0x5A8
NETWORK_ID8 = 2                 # NetworkInfo.id of the distribution (the PSS presence's too)
MAX_NODES = 2
CHANNEL = 11
FULL_SIZE = 784
MAX_FILES = 20                  # the game takes up to 20 full files from one distribution
CARD_AT = 0x208                 # the card inside a full file; its id is the card's first u16


def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


# -- LZ10 -----------------------------------------------------------------------------------------

def lz10_decompress(src: bytes) -> bytes:
    if len(src) < 4 or src[0] != 0x10:
        raise ValueError("not an LZ10 stream")
    size = int.from_bytes(src[1:4], "little")
    out, i = bytearray(), 4
    while len(out) < size:
        flags = src[i]
        i += 1
        for k in range(8):
            if len(out) >= size:
                break
            if flags & (0x80 >> k):
                b0, b1 = src[i], src[i + 1]
                i += 2
                length, distance = (b0 >> 4) + 3, ((b0 & 0xF) << 8 | b1) + 1
                if distance > len(out):
                    raise ValueError("LZ10 reference before the start")
                for _ in range(length):
                    out.append(out[-distance])
            else:
                out.append(src[i])
                i += 1
    return bytes(out)


def lz10_compress(data: bytes) -> bytes:
    """Greedy LZ10 (any valid stream decompresses the same; the game's own compressor need not be matched)."""
    out = bytearray([0x10]) + len(data).to_bytes(3, "little")
    i = 0
    while i < len(data):
        flag_at = len(out)
        out.append(0)
        for k in range(8):
            if i >= len(data):
                break
            best_len, best_dist = 0, 0
            for dist in range(1, min(i, 0x1000) + 1):
                n = 0
                while n < 18 and i + n < len(data) and data[i + n] == data[i + n - dist]:
                    n += 1
                if n > best_len:
                    best_len, best_dist = n, dist
                    if n == 18:
                        break
            if best_len >= 3:
                out[flag_at] |= 0x80 >> k
                out += bytes([(best_len - 3) << 4 | (best_dist - 1) >> 8, (best_dist - 1) & 0xFF])
                i += best_len
            else:
                out.append(data[i])
                i += 1
    return bytes(out)


# -- fragments ------------------------------------------------------------------------------------

def card_id(full: bytes) -> int:
    return struct.unpack_from("<H", full, CARD_AT)[0]


def message(full: bytes) -> bytes:
    packed = lz10_compress(full)
    return struct.pack("<II", len(full), len(packed)) + packed


def fragments(full: bytes, *, distribution_id: bytes = DISTRIBUTION_ID, word: int = DELIVERY_WORD) -> list[bytes]:
    """The beacon application data, one per fragment, for a distribution: one 784-byte ``.wc6full`` or up to 20 back
    to back, as an event's file holds them (a card's X/Y and ORAS copies, a set); the header names the first card."""
    if not full or len(full) % FULL_SIZE or len(full) > MAX_FILES * FULL_SIZE:
        raise ValueError(f"a distribution is 1 to {MAX_FILES} full files of {FULL_SIZE} bytes, not {len(full)} bytes")
    body = message(full)
    count = -(-len(body) // PAYLOAD_SIZE)
    if count > 255:
        raise ValueError("the gift does not fit in 255 fragments")
    out = []
    for index in range(count):
        chunk = body[index * PAYLOAD_SIZE:(index + 1) * PAYLOAD_SIZE].ljust(PAYLOAD_SIZE, b"\0")
        rest = MAGIC + distribution_id + struct.pack("<IHBB", word, card_id(full), count, index) + chunk
        out.append(struct.pack("<H", crc16(rest)) + rest)
    return out


@dataclass
class Fragment:
    distribution_id: bytes
    word: int
    card_id: int
    count: int
    index: int
    payload: bytes


def parse(appdata: bytes) -> Fragment | None:
    """One beacon's fragment, or None for any other application data (the PSS's, a bad checksum)."""
    if len(appdata) != APPDATA_SIZE or appdata[2:8] != MAGIC:
        return None
    if struct.unpack_from("<H", appdata, 0)[0] != crc16(appdata[2:]):
        return None
    word, cid, count, index = struct.unpack_from("<IHBB", appdata, 0x10)
    if index >= count:
        return None
    return Fragment(appdata[8:16], word, cid, count, index, appdata[HEADER_SIZE:])


def reassemble(parts: list[Fragment]) -> bytes:
    """The full file(s) from a full set of fragments (any order, duplicates allowed)."""
    if not parts:
        raise ValueError("no fragments")
    count = parts[0].count
    by_index = {p.index: p for p in parts if p.count == count and p.card_id == parts[0].card_id}
    missing = [i for i in range(count) if i not in by_index]
    if missing:
        raise ValueError(f"fragments {missing} of {count} are missing")
    body = b"".join(by_index[i].payload for i in range(count))
    size, packed = struct.unpack_from("<II", body, 0)
    full = lz10_decompress(body[8:8 + packed])
    if len(full) != size:
        raise ValueError(f"the message says {size} bytes, LZ10 gave {len(full)}")
    return full
