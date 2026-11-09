"""Generation 7 local Mystery Gift: the distributor's beacon, as the Gen 7 distributor sends it.

The receiving game (Mystery Gift, Get via Local Wireless) only scans; it never joins.  The
distributor hosts a UDS network of the Gen 7 comm id (``0x00164810``, Sun/Moon's and USUM's),
network id 2, room for two, on channel 11, and puts the gift in its beacons' 200 bytes of
application data.  Read off a real broadcast of card 289 (the 20th-anniversary Shaymin); see
docs/mystery_gift.md, "The Gen 7 distribution, on the air".

Each beacon's 200 bytes of application data::

    0x00  2   CRC-16/USB (reflected 0x8005, init 0xFFFF, xorout 0xFFFF) of bytes 0x02-0xC7
    0x02  6   78 01 02 1E 02 41 (constant)
    0x08  8   a distribution id (the captured distributor's; constant across its runs)
    0x10  4   0xBA8 (constant in the capture; the Gen 6 distributor's word was 0x5A8)
    0x14  4   card id (u16), fragment count (u8), this fragment's index (u8)
    0x18  160 this fragment's slice of the message, the last one zero-padded
    0xB8  16  zero padding (part of the CRC)

The message is u32 the full file's size (784), u32 the compressed size, then the LZ10 stream
**signed with OPSSR** (Nintendo's Zorro library: RSA-768, AES-CMC and SHA-1, overhead 8).  The
signature keeps the first ``len(compressed) - 88`` bytes clear and replaces the last 88 with a
96-byte RSA block that recovers them; that is why the message is 8 bytes longer than the LZ10
stream and why the stream stops looking like LZ10 at about offset 210.  The receiving game
verifies/recoveres it before decompressing.  Two fragments carry a typical card.  The public key is
embedded (it is public); the private exponent is the distributor app's key at 0x66B2F7 and lives in
the local, gitignored ``delivery_key.py`` — see docs/mystery_gift.md.
"""

from __future__ import annotations

import binascii
import hashlib
import struct
from dataclasses import dataclass

from Crypto.Cipher import AES

from ..gen6.delivery import lz10_compress, lz10_decompress

APPDATA_SIZE = 0xC8
HEADER_SIZE = 0x18
PAYLOAD_SIZE = 0xA0              # the distributor's message slice per beacon (Gen 6's 160)
PADDING_SIZE = APPDATA_SIZE - HEADER_SIZE - PAYLOAD_SIZE
MAGIC = bytes.fromhex("7801021E0241")
DISTRIBUTION_ID = bytes.fromhex("720740AA876FA5C1")
DELIVERY_WORD = 0xBA8
NETWORK_ID8 = 2                 # NetworkInfo.id of the distribution
MAX_NODES = 2
CHANNEL = 11
FULL_SIZE = 784
MAX_FILES = 20                  # the game takes up to 20 full files from one distribution
CARD_AT = 0x208                 # the card inside a full file; its first u16 is the card's id


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE, the full file's checksum at 0x202 (PKHeX's, and the distributor's)."""
    return binascii.crc_hqx(data, 0xFFFF)


def crc16_usb(data: bytes) -> int:
    """CRC-16/USB: reflected 0x8005, init 0xFFFF, xorout 0xFFFF — the beacon's two-byte tag."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def beacon_tag(appdata: bytes) -> int:
    """The tag the captured distributor puts at 0x00: CRC-16/USB over the 198 bytes 0x02-0xC7."""
    return crc16_usb(appdata[2:])


def card_id(full: bytes) -> int:
    return struct.unpack_from("<H", full, CARD_AT)[0]


CARD_DATE = CARD_AT + 0x4C       # the card's YYMMDD date
CRC_AT = 0x202


def sanitized(full: bytes) -> bytes:
    """Clear each card's receive date and recompute its full-file CRC.

    The captured distributor's patched extdata carries every card with its date zeroed (``00 00 00``) and a
    CRC over that, so it can distribute an old event at any time.  PKHeX's full file, by contrast, stamps a
    date into the card (today's, for a card that has none); a receiving game that compares that date with its
    clock can then ignore the gift.  Clearing it, as the "all dates" extdata does, removes that comparison.
    """
    if len(full) % FULL_SIZE:
        raise ValueError(f"a distribution is whole {FULL_SIZE}-byte full files, not {len(full)} bytes")
    out = bytearray(full)
    for start in range(0, len(out), FULL_SIZE):
        out[start + CARD_DATE:start + CARD_DATE + 3] = b"\0\0\0"
        out[start + CRC_AT:start + CRC_AT + 2] = b"\0\0"
        struct.pack_into("<H", out, start + CRC_AT, crc16_ccitt(bytes(out[start:start + FULL_SIZE])))
    return bytes(out)


# The distributor's Zorro/OPSSR keys: the 126-byte SubjectPublicKeyInfo and the RSA-768 private
# exponent.  These are the app's own keys (NetStatic/NetLib DeliveryKeys.cpp).
PUBLIC_KEY = bytes.fromhex(
    "307c300d06092a864886f70d0101010500036b003068026100b756e1dcd8cece78e148107b1bac115fdb17de843453"
    "cab7d4e6df8dd21f5a3d17b4477a8a531d97d57eb558f0d58a4af5bfaddda4a0bc1dc22ff87576c7268b942819d4c8"
    "3f78e1ee92d406662f4e68471e4de833e5126c32eb63a868345d1d0203010001")
RSA_N = int.from_bytes(bytes.fromhex(
    "b756e1dcd8cece78e148107b1bac115fdb17de843453cab7d4e6df8dd21f5a3d17b4477a8a531d97d57eb558f0d5"
    "8a4af5bfaddda4a0bc1dc22ff87576c7268b942819d4c83f78e1ee92d406662f4e68471e4de833e5126c32eb63a8"
    "68345d1d"), "big")
try:
    from .delivery_key import RSA_D       # the distributor app's private exponent; local, never committed
except ImportError:                        # verification still works; signing does not
    RSA_D = None
RSA_E = 65537
MODULUS_SIZE = 96               # RSA-768
OVERHEAD = 8                    # OPSSR's digest bytes kept in the signature
SIGNATURE_SIZE = MODULUS_SIZE   # the signed block replacing the compressed stream's last 88 bytes


def _opssr_mask(xored: bytes) -> bytes:
    value = int.from_bytes(xored, "big")
    carry = value >> 127
    mask = ((value << 1) & ((1 << 128) - 1)).to_bytes(16, "big")
    if carry:
        mask = mask[:-1] + bytes([mask[-1] ^ 0x87])
    return mask


def opssr_sign(message: bytes) -> bytes:
    """Sign the compressed stream the way Nintendo's Zorro/OPSSR does (AES-CMC + SHA-1 + RSA-768).

    Returns ``message[:-88] + (message[-88:] + sha1(message)[:8])`` block, where the last 96 bytes are
    the RSA-768 signature; the signer keeps the first ``len(message) - 88`` bytes clear.
    """
    if RSA_D is None:
        raise ValueError("the Gen 7 distributor signing key is not installed; put the app's RSA private "
                         "exponent in pokeuds/games/gen7/delivery_key.py (docs/mystery_gift.md)")
    part2 = MODULUS_SIZE - OVERHEAD
    if len(message) < part2:
        raise ValueError(f"the compressed message is {len(message)} bytes; OPSSR needs {part2}")
    clear = message[:-part2]
    sign_part = message[-part2:] + hashlib.sha1(message).digest()[:OVERHEAD]
    aes = AES.new(hashlib.sha1(PUBLIC_KEY + clear).digest()[:16], AES.MODE_ECB)
    iv = bytes(16)
    interm = b""
    for i in range(0, len(sign_part), 16):
        block = sign_part[i:i + 16]
        iv = aes.encrypt(bytes(a ^ b for a, b in zip(block, iv)))
        interm += iv
    mask = _opssr_mask(bytes(a ^ b for a, b in zip(interm[:16], interm[-16:])))
    iv = bytes(16)
    result = b""
    blocks = [interm[i:i + 16] for i in range(0, len(interm), 16)]
    for i in range(len(blocks) - 1, -1, -1):
        next_iv = bytes(a ^ b for a, b in zip(blocks[i], mask))
        cur = aes.encrypt(next_iv)
        result = bytes(a ^ b for a, b in zip(cur, iv)) + result
        iv = next_iv
    result = bytes([result[0] & 0x7F]) + result[1:]
    signature = pow(int.from_bytes(result, "big"), RSA_D, RSA_N).to_bytes(MODULUS_SIZE, "big")
    return clear + signature


def opssr_verify(signed: bytes) -> bytes | None:
    """Recover the compressed stream from an OPSSR-signed one, or None if the digest does not verify."""
    if len(signed) < SIGNATURE_SIZE:
        return None
    clear, signature = signed[:-SIGNATURE_SIZE], signed[-SIGNATURE_SIZE:]
    block = pow(int.from_bytes(signature, "big"), RSA_E, RSA_N).to_bytes(MODULUS_SIZE, "big")
    aes = AES.new(hashlib.sha1(PUBLIC_KEY + clear).digest()[:16], AES.MODE_ECB)
    for top in (0, 0x80):
        candidate = bytes([block[0] | top]) + block[1:]
        recovered = [b""] * (MODULUS_SIZE // 16)
        iv = bytes(16)
        for i in range(len(recovered) - 1, -1, -1):
            x = bytes(a ^ b for a, b in zip(candidate[i * 16:(i + 1) * 16], iv))
            iv = aes.decrypt(x)
            recovered[i] = iv
        mask = _opssr_mask(bytes(a ^ b for a, b in zip(recovered[0], recovered[-1])))
        sign_part = b""
        iv = bytes(16)
        for chunk in recovered:
            c = bytes(a ^ b for a, b in zip(chunk, mask))
            sign_part += bytes(a ^ b for a, b in zip(aes.decrypt(c), iv))
            iv = c
        message = clear + sign_part[:-OVERHEAD]
        if hashlib.sha1(message).digest()[:OVERHEAD] == sign_part[-OVERHEAD:]:
            return message
    return None


def message(full: bytes) -> bytes:
    packed = lz10_compress(full)
    return struct.pack("<II", len(full), len(packed)) + opssr_sign(packed)


def fragments(full: bytes, *, distribution_id: bytes = DISTRIBUTION_ID, word: int = DELIVERY_WORD,
              sanitize: bool = False) -> list[bytes]:
    """The beacon application data, one per fragment, for one 784-byte ``.wc7full`` or up to 20 back to back.

    ``sanitize`` optionally clears the card date (the distributor's extdata files carry date zero, but its
    runtime full file uses the event's date, so the date is kept by default).  The compressed message is
    always OPSSR-signed, as the distributor signs it.
    """
    if not full or len(full) % FULL_SIZE or len(full) > MAX_FILES * FULL_SIZE:
        raise ValueError(f"a distribution is 1 to {MAX_FILES} full files of {FULL_SIZE} bytes, not {len(full)} bytes")
    full = sanitized(full) if sanitize else full
    body = message(full)
    count = -(-len(body) // PAYLOAD_SIZE)
    if count > 255:
        raise ValueError("the gift does not fit in 255 fragments")
    out = []
    for index in range(count):
        chunk = body[index * PAYLOAD_SIZE:(index + 1) * PAYLOAD_SIZE].ljust(PAYLOAD_SIZE, b"\0")
        rest = (MAGIC + distribution_id + struct.pack("<I", word)
                + struct.pack("<HBB", card_id(full), count, index) + chunk + bytes(PADDING_SIZE))
        appdata = crc16_usb(rest).to_bytes(2, "little") + rest
        out.append(appdata)
    return out


@dataclass
class Fragment:
    tag: int
    distribution_id: bytes
    word: int
    card_id: int
    count: int
    index: int
    payload: bytes


def parse(appdata: bytes) -> Fragment | None:
    """One beacon's fragment, or None for any other application data (the Plaza's, a bad length)."""
    if len(appdata) != APPDATA_SIZE or appdata[2:8] != MAGIC:
        return None
    tag = struct.unpack_from("<H", appdata, 0)[0]
    if beacon_tag(appdata) != tag:
        return None
    word = struct.unpack_from("<I", appdata, 0x10)[0]
    cid, count, index = struct.unpack_from("<HBB", appdata, 0x14)
    if not count or index >= count:
        return None
    return Fragment(tag, appdata[8:16], word, cid, count, index, appdata[HEADER_SIZE:HEADER_SIZE + PAYLOAD_SIZE])


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
    payload = body[8:]
    if len(payload) >= packed + OVERHEAD:
        packed_data = opssr_verify(payload[:packed + OVERHEAD])
        if packed_data is None:
            raise ValueError("the OPSSR signature did not verify")
    else:
        packed_data = payload[:packed]
    full = lz10_decompress(packed_data)
    if len(full) != size:
        raise ValueError(f"the message says {size} bytes, LZ10 gave {len(full)}")
    return full
