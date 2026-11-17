"""Gen 4 and 5 Mystery Gift over DS local wireless: a gift sent in beacons, as a store distribution sent it.

The receiving game (Mystery Gift > Receive Gift, the wireless option rather than Wi-Fi) only listens: the distributor
broadcasts 802.11 beacons carrying Nintendo's DS vendor element (OUI 00:09:BF), each with one 112-byte fragment of
the gift, and cycles through the fragments. Nobody joins. PokeUDS sends the beacons itself, one frame at a time
through the radio's raw transmit (firmware 2.0-uds's TX_FRAME, at 1 Mbps), so no firmware change is needed.

Ported from Shinygati's PokeLDN-Bluetooth fork of pokeldn (AGPL-3.0; https://github.com/Shinygati/PokeLDN-Bluetooth):
the encoders from ``Gen4.java`` and ``Gen5.java``, the beacon from ``firmware/esp32/main/ds_compat.c``. Those
credit an unpublished DS sender (``gen4_crypto.py``, ``gen5_crypto.py``). Its author reports receiving gifts on DS
games with it; PokeUDS's port has worked on a retail DS for Gen 4, Gen 5 is not yet tried (docs/mystery_gift.md).

**The gift.** Both generations carry exactly the Wi-Fi gift file PokeUDS already makes from PKHeX (docs/mystery_gift.md,
"DS Wi-Fi gift files"):

- Gen 4: the 936-byte (0x3A8) ``.myg``: the card information's first 0x50 bytes, then the 856-byte ``.pcd`` with its
  Pokemon encrypted. Its checksum (a 16-bit add-and-rotate) keys RC4 together with the sender's MAC address, so the
  beacons must come from ``MAC``. Ten fragments: nine of 104 encrypted bytes, and a tenth (index 0xFFFF) carrying
  the plain first 0x50 bytes.
- Gen 5: the 720-byte ``.bin``, its received date and used flag cleared, the receiving game's language at 0x2CB and
  its CRC renewed. Nine fragments of 84 bytes, each XORed with the games' usual LCG stream under its own key and
  carrying two CRC-16s, its index, the language and the game mask.

**The beacon** (164 bytes of body after the 24-byte header, from and to ``MAC`` as BSSID, to broadcast):

    0x00  8   TSF timestamp
    0x08  2   beacon interval, TU: 10 (Gen 4), 20 (Gen 5)
    0x0A  2   capability 0x0021
    0x0C  20  01 02 82 84 (rates 1, 2 Mbps), 03 01 <channel>, 05 05 01 02 00 00 00 (TIM), DD 88 00 09 BF 00
    0x20  4   0x0000000A
    0x24  4   0x00010001
    0x28  4   GGID: Gen 4 one per language (``GEN4_GGID``), Gen 5 0x1380
    0x2C  2   stream code 0
    0x2E  1   payload size 112
    0x2F  1   beacon type: 0 Gen 4, 1 Gen 5
    0x30  4   0x0028, 0x000C
    0x34  112 the fragment

The sender's firmware sends the next fragment every 10 ms (Gen 4) or 21 ms (Gen 5); ``send`` keeps that pace.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from typing import Callable, Protocol

MAC = bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x01])
"""The sender's address. The Gen 4 RC4 key is made from it, so the beacons must carry exactly this one."""
CHANNEL = 7
FRAGMENT_SIZE = 112

GAMES = {"dp": 4, "pt": 4, "hgss": 4, "bw": 5, "b2w2": 5}
GAME_NAMES = {"dp": "Diamond / Pearl", "pt": "Platinum", "hgss": "HeartGold / SoulSilver", "bw": "Black / White",
              "b2w2": "Black 2 / White 2"}

GEN4_FILE_SIZE = 0x3A8          # 936: the .myg
GEN4_FRAGMENTS = 10
GEN4_CHUNK = 104
GEN5_FILE_SIZE = 720            # the .bin
GEN5_FRAGMENTS = 9
GEN5_CHUNK = 84
GEN5_GGID = 0x1380

LANGUAGES = {"ENG": 2, "JPN": 1, "FRE": 3, "ITA": 4, "GER": 5, "SPA": 7, "KOR": 8}
"""The receiving game's language, as the games and PKHeX number them. (The fork's Gen5.java numbers German 4 and
Italian 5; PKHeX's LanguageID, which PokeUDS's .bin writer follows, has Italian 4 and German 5.)"""
LANGUAGE_NAMES = {"ENG": "English", "JPN": "Japanese", "FRE": "French", "ITA": "Italian", "GER": "German",
                  "SPA": "Spanish", "KOR": "Korean"}

GEN4_GGID = {"ENG": 0x00400318, "JPN": 0x00000345, "FRE": 0x008000CD, "GER": 0x008000CE, "ITA": 0x008000CF,
             "SPA": 0x008000D0, "KOR": 0x00C00018}
"""Gen 4's GGID by the receiving cartridge's language, from the fork. It marks the European four as provisional."""

INTERVAL = {4: 0.010, 5: 0.021}
"""Seconds between beacons (one fragment each), the sender firmware's pace."""
_BEACON_INTERVAL_TU = {4: 10, 5: 20}


def generation(game: str) -> int:
    try:
        return GAMES[game]
    except KeyError:
        raise ValueError(f"not a DS game: {game} (one of {', '.join(GAMES)})") from None


def language_code(value: str | int) -> str:
    """``ENG``, ``eng``, or a language id (2) as ``ENG``."""
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        for code, number in LANGUAGES.items():
            if number == int(value):
                return code
    elif value.upper() in LANGUAGES:
        return value.upper()
    raise ValueError(f"unknown language {value!r} (one of {', '.join(LANGUAGES)})")


# -- checksums and ciphers -------------------------------------------------------------------------------------------

def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF), as the Gen 5 file and fragments use."""
    c = 0xFFFF
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def gen4_checksum(data: bytes) -> int:
    """Gen 4's: add each little-endian u16, then rotate left by one."""
    c = 0
    for (word,) in struct.iter_unpack("<H", data[: len(data) & ~1]):
        c = (c + word) & 0xFFFF
        c = ((c << 1) | (c >> 15)) & 0xFFFF
    return c


def gen4_key(checksum: int, mac: bytes = MAC) -> bytes:
    """The 8-byte RC4 key: the MAC's bytes and the checksum, each u16 XORed into a running value from 0x3FA2."""
    k = bytearray([mac[0], mac[1], checksum & 0xFF, checksum >> 8, mac[4], mac[5], mac[2], mac[3]])
    h = 0x3FA2
    for i in range(4):
        h = int.from_bytes(k[2 * i:2 * i + 2], "little") ^ h
        k[2 * i:2 * i + 2] = h.to_bytes(2, "little")
    return bytes(k)


def rc4(data: bytes, key: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray(data)
    i = j = 0
    for n in range(len(out)):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[n] ^= s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


def _xor_lcg(data: bytes, state: int) -> bytes:
    """Each u16 XORed with the next LCG output's high half (0x41C64E6D, 0x6073): its own inverse."""
    out = bytearray(data)
    for i in range(0, len(out) - 1, 2):
        state = (state * 0x41C64E6D + 0x6073) & 0xFFFFFFFF
        word = int.from_bytes(out[i:i + 2], "little") ^ (state >> 16)
        out[i:i + 2] = word.to_bytes(2, "little")
    return bytes(out)


# -- Gen 4 -----------------------------------------------------------------------------------------------------------

def gen4_fragments(myg: bytes, mac: bytes = MAC) -> list[bytes]:
    """The ten 112-byte fragments of a 936-byte Gen 4 ``.myg``."""
    if len(myg) != GEN4_FILE_SIZE:
        raise ValueError(f"a Gen 4 gift is a {GEN4_FILE_SIZE}-byte .myg, not {len(myg)} bytes")
    checksum = gen4_checksum(myg)
    sealed = rc4(myg, gen4_key(checksum, mac))
    out = []
    for i in range(GEN4_FRAGMENTS):
        p = bytearray(FRAGMENT_SIZE)
        struct.pack_into("<HHI", p, 0, checksum, 0xFFFF if i == 9 else i, GEN4_FILE_SIZE)
        if i == 9:
            p[8:8 + 0x50] = myg[:0x50]
        else:
            p[8:8 + GEN4_CHUNK] = sealed[i * GEN4_CHUNK:(i + 1) * GEN4_CHUNK]
        out.append(bytes(p))
    return out


def gen4_decode(fragments: list[bytes], mac: bytes = MAC) -> bytes:
    """The ``.myg`` back from its fragments (any order), checked as the sender's firmware checks an upload."""
    slots: dict[int, bytes] = {}
    for f in fragments:
        checksum, index, size = struct.unpack_from("<HHI", f)
        if size != GEN4_FILE_SIZE or not (index < 9 or index == 0xFFFF):
            raise ValueError("not a Gen 4 fragment")
        slots[9 if index == 0xFFFF else index] = f
    if sorted(slots) != list(range(GEN4_FRAGMENTS)):
        raise ValueError("Gen 4 fragments missing")
    sealed = b"".join(slots[i][8:8 + GEN4_CHUNK] for i in range(9))
    checksum = struct.unpack_from("<H", slots[0])[0]
    myg = rc4(sealed, gen4_key(checksum, mac))
    if gen4_checksum(myg) != checksum or myg[:0x50] != slots[9][8:8 + 0x50]:
        raise ValueError("Gen 4 fragments do not check out")
    return myg


# -- Gen 5 -----------------------------------------------------------------------------------------------------------

def gen5_record(data: bytes, language: int) -> bytes:
    """A 720-byte ``.bin`` as it goes on the air: undated, not used, for ``language``, its CRC renewed."""
    if len(data) != GEN5_FILE_SIZE:
        raise ValueError(f"a Gen 5 gift is a {GEN5_FILE_SIZE}-byte .bin, not {len(data)} bytes")
    r = bytearray(data)
    if struct.unpack_from("<I", r, 0xCC)[0] == 0:
        raise ValueError("the Gen 5 gift names no game that may receive it (0xCC is zero)")
    r[0xAC:0xB0] = bytes(4)
    r[0xB4] = 0
    r[0x2CB] = language & 0xFF
    struct.pack_into("<H", r, 0x2CE, crc16(r[:0x2CE]))
    return bytes(r)


def gen5_fragments(data: bytes, language: int) -> list[bytes]:
    """The nine 112-byte fragments of a 720-byte Gen 5 ``.bin``, for a game of ``language`` (``LANGUAGES``)."""
    record = gen5_record(data, language)
    mask = record[0xCC:0xD0]
    out = []
    key = 0x13579BDF
    for i in range(GEN5_FRAGMENTS):
        key = (key * 0x6C078965 + 1) & 0xFFFFFFFF
        chunk = record[i * GEN5_CHUNK:(i + 1) * GEN5_CHUNK].ljust(GEN5_CHUNK, b"\0")
        p = bytearray(FRAGMENT_SIZE)
        struct.pack_into("<H", p, 2, GEN5_GGID)
        p[4] = GEN5_FRAGMENTS
        struct.pack_into("<I", p, 12, key)
        p[16:16 + GEN5_CHUNK] = _xor_lcg(chunk, key)
        struct.pack_into("<H", p, 8, crc16(p[16:16 + GEN5_CHUNK]))
        p[100], p[101], p[102], p[103] = i + 1, GEN5_FRAGMENTS, 0, language & 0xFF
        p[104:108] = mask
        struct.pack_into("<H", p, 0, crc16(p[2:FRAGMENT_SIZE]))
        out.append(bytes(p))
    return out


def gen5_decode(fragments: list[bytes]) -> bytes:
    """The 720-byte record back from its fragments (any order), checked as the sender's firmware checks an upload."""
    slots: dict[int, bytes] = {}
    for f in fragments:
        if (struct.unpack_from("<H", f)[0] != crc16(f[2:FRAGMENT_SIZE])
                or struct.unpack_from("<H", f, 8)[0] != crc16(f[16:16 + GEN5_CHUNK])
                or f[4] != GEN5_FRAGMENTS or f[101] != GEN5_FRAGMENTS or not 1 <= f[100] <= GEN5_FRAGMENTS):
            raise ValueError("a Gen 5 fragment fails its checks")
        slots[f[100] - 1] = f
    if sorted(slots) != list(range(GEN5_FRAGMENTS)):
        raise ValueError("Gen 5 fragments missing")
    plain = b"".join(_xor_lcg(slots[i][16:16 + GEN5_CHUNK], struct.unpack_from("<I", slots[i], 12)[0])
                     for i in range(GEN5_FRAGMENTS))
    record = plain[:GEN5_FILE_SIZE]
    if struct.unpack_from("<H", record, 0x2CE)[0] != crc16(record[:0x2CE]):
        raise ValueError("the Gen 5 record's CRC does not check out")
    return record


# -- the beacon ------------------------------------------------------------------------------------------------------

def beacon_body(fragment: bytes, gen: int, ggid: int, channel: int = CHANNEL, timestamp_us: int = 0) -> bytes:
    """The 164-byte beacon body carrying one fragment (the layout is in the module's docstring)."""
    if len(fragment) != FRAGMENT_SIZE:
        raise ValueError(f"a fragment is {FRAGMENT_SIZE} bytes")
    tags = bytes([0x01, 0x02, 0x82, 0x84, 0x03, 0x01, channel, 0x05, 0x05, 0x01, 0x02, 0x00, 0x00, 0x00,
                  0xDD, 0x88, 0x00, 0x09, 0xBF, 0x00])
    body = (struct.pack("<QHH", timestamp_us & 0xFFFFFFFFFFFFFFFF, _BEACON_INTERVAL_TU[gen], 0x0021) + tags
            + struct.pack("<IHHIHBBHH", 10, 1, 1, ggid, 0, FRAGMENT_SIZE, 0 if gen == 4 else 1, 0x28, 0x0C)
            + fragment)
    assert len(body) == 164 and body[13 + 13] == 0xDD
    return body


def beacon_frame(fragment: bytes, gen: int, ggid: int, *, seq: int = 0, timestamp_us: int = 0,
                 mac: bytes = MAC, channel: int = CHANNEL) -> bytes:
    """The whole 802.11 beacon (MPDU, no FCS): broadcast, from and with BSSID ``mac``."""
    header = struct.pack("<HH6s6s6sH", 0x0080, 0, b"\xff" * 6, mac, mac, (seq & 0x0FFF) << 4)
    return header + beacon_body(fragment, gen, ggid, channel, timestamp_us)


# -- a distribution --------------------------------------------------------------------------------------------------

@dataclass
class Distribution:
    generation: int
    language: str                   # LANGUAGES' code: the receiving game's
    ggid: int
    fragments: list[bytes]

    @property
    def interval(self) -> float:
        return INTERVAL[self.generation]


def prepare(gen: int, data: bytes, language: str | int = "ENG") -> Distribution:
    """A Gen 4 ``.myg`` (936 bytes) or Gen 5 ``.bin`` (720 bytes) as the fragments for a game of ``language``."""
    code = language_code(language)
    if gen == 4:
        return Distribution(4, code, GEN4_GGID[code], gen4_fragments(data))
    if gen == 5:
        return Distribution(5, code, GEN5_GGID, gen5_fragments(data, LANGUAGES[code]))
    raise ValueError(f"DS local wireless gifts are Gen 4 or 5, not Gen {gen}")


class _Sender(Protocol):
    def send(self, mpdu: bytes) -> None: ...


def send(radio: _Sender, dist: Distribution, seconds: float, *, channel: int = CHANNEL,
         stop: Callable[[], bool] | None = None, clock: Callable[[], float] = time.monotonic,
         sleep: Callable[[float], None] = time.sleep) -> int:
    """Broadcast the fragments in turn, one beacon every ``dist.interval`` s, for ``seconds`` or until ``stop()``.
    The radio must already be started on ``channel`` with ``MAC``. Returns the number of beacons sent."""
    start = clock()
    deadline = start + seconds
    due = start
    sent = 0
    n = len(dist.fragments)
    while (now := clock()) < deadline and not (stop and stop()):
        radio.send(beacon_frame(dist.fragments[sent % n], dist.generation, dist.ggid, seq=sent,
                                timestamp_us=int((now - start) * 1_000_000), channel=channel))
        sent += 1
        due += dist.interval
        delay = due - clock()
        if delay > 0:
            sleep(delay)
        elif delay < -0.5:          # the host stalled: carry on at the pace from here, without a burst
            due = clock()
    return sent
