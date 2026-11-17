"""Gen 4/5 Mystery Gift over DS local wireless (pokeuds.games.ds.delivery).

The checks mirror what the PokeLDN-Bluetooth sender's firmware checks of an upload (ds_compat.c ``ds_receive``) and
the beacon it builds from it (``ds_image``), on synthetic gift files; the PKHeX test runs PKHeX's own Gen 4/5 files
through the same path when the helper is built.
"""

from __future__ import annotations

import random
import struct

import pytest

from pokeuds.games.ds import delivery as ds


def _myg(seed: int = 4) -> bytes:
    return random.Random(seed).randbytes(ds.GEN4_FILE_SIZE)


def _bin(seed: int = 5, mask: int = 0x00300000) -> bytes:
    r = bytearray(random.Random(seed).randbytes(ds.GEN5_FILE_SIZE))
    struct.pack_into("<I", r, 0xCC, mask)
    return bytes(r)


# -- Gen 4 -----------------------------------------------------------------------------------------------------------

def test_gen4_checksum_and_key():
    assert ds.gen4_checksum(b"\x01\x00") == 0x0002            # 1, rotated left once
    assert ds.gen4_checksum(b"\x00\x80") == 0x0001            # the top bit wraps round
    key = ds.gen4_key(0x1234)
    # MAC 02:00:00:00:00:01 -> 02 00 34 12 00 01 00 00, each u16 XORed into a running value from 0x3FA2.
    words = [0x0002, 0x1234, 0x0100, 0x0000]
    h, want = 0x3FA2, b""
    for w in words:
        h ^= w
        want += h.to_bytes(2, "little")
    assert key == want


def test_gen4_fragments_are_what_the_sender_firmware_accepts():
    myg = _myg()
    frags = ds.gen4_fragments(myg)
    assert len(frags) == 10 and all(len(f) == 112 for f in frags)
    checksum = ds.gen4_checksum(myg)
    for i, f in enumerate(frags):
        # ds_receive: u16 at +2 is the slot (0xFFFF for the tenth), u16 +4 is 0x3A8 and u16 +6 zero.
        assert struct.unpack_from("<HHHH", f) == (checksum, 0xFFFF if i == 9 else i, 0x3A8, 0)
    assert frags[9][8:8 + 0x50] == myg[:0x50]                  # the tenth carries the plain head
    assert frags[0][8:8 + 104] != myg[:104]                    # the others are encrypted
    assert ds.gen4_decode(frags) == myg
    assert ds.gen4_decode(list(reversed(frags))) == myg


def test_gen4_depends_on_the_sender_mac():
    myg = _myg()
    other = ds.gen4_fragments(myg, mac=bytes([2, 0, 0, 0, 0, 2]))
    assert other[0] != ds.gen4_fragments(myg)[0]
    with pytest.raises(ValueError):
        ds.gen4_decode(other)                                  # read with the usual MAC, it does not check out


def test_gen4_wants_a_myg():
    with pytest.raises(ValueError, match="936"):
        ds.gen4_fragments(bytes(856))


# -- Gen 5 -----------------------------------------------------------------------------------------------------------

def test_gen5_record_is_undated_unused_and_for_the_language():
    raw = _bin()
    r = ds.gen5_record(raw, ds.LANGUAGES["GER"])
    assert r[0xAC:0xB0] == bytes(4) and r[0xB4] == 0 and r[0x2CB] == 5
    assert struct.unpack_from("<H", r, 0x2CE)[0] == ds.crc16(r[:0x2CE])
    assert r[:0xAC] == raw[:0xAC] and r[0xCC:0x2CB] == raw[0xCC:0x2CB]


def test_gen5_fragments_are_what_the_sender_firmware_accepts():
    raw = _bin()
    frags = ds.gen5_fragments(raw, ds.LANGUAGES["ENG"])
    assert len(frags) == 9 and all(len(f) == 112 for f in frags)
    keys = []
    for i, f in enumerate(frags):
        # ds_receive: the CRC of 0x02-0x6F at 0, the CRC of the 84 data bytes at 8, 9 at +4, index+1 and 9 at +100.
        assert struct.unpack_from("<H", f)[0] == ds.crc16(f[2:112])
        assert struct.unpack_from("<H", f, 8)[0] == ds.crc16(f[16:100])
        assert f[4] == 9 and f[100] == i + 1 and f[101] == 9
        assert struct.unpack_from("<H", f, 2)[0] == 0x1380
        assert f[103] == 2 and f[104:108] == raw[0xCC:0xD0]
        keys.append(struct.unpack_from("<I", f, 12)[0])
    key, want = 0x13579BDF, []
    for _ in range(9):
        key = (key * 0x6C078965 + 1) & 0xFFFFFFFF
        want.append(key)
    assert keys == want
    assert ds.gen5_decode(frags) == ds.gen5_record(raw, 2)


def test_gen5_needs_a_game_mask():
    with pytest.raises(ValueError, match="0xCC"):
        ds.gen5_fragments(_bin(mask=0), 2)


# -- the beacon ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("gen", [4, 5])
def test_beacon_matches_the_sender_firmware(gen):
    dist = ds.prepare(gen, _myg() if gen == 4 else _bin(), "FRE")
    frame = ds.beacon_frame(dist.fragments[3], gen, dist.ggid, seq=5, timestamp_us=0x0102030405)
    assert len(frame) == 24 + 164
    assert frame[:2] == b"\x80\x00" and frame[4:10] == b"\xff" * 6
    assert frame[10:16] == ds.MAC and frame[16:22] == ds.MAC
    assert struct.unpack_from("<H", frame, 22)[0] == 5 << 4
    b = frame[24:]
    assert struct.unpack_from("<QHH", b) == (0x0102030405, 10 if gen == 4 else 20, 0x0021)
    assert b[12:32] == bytes.fromhex("01028284 030107 05050102000000 dd880009bf00")
    assert struct.unpack_from("<IHHIHBBHH", b, 32) == (10, 1, 1, dist.ggid, 0, 112, 0 if gen == 4 else 1, 0x28, 0x0C)
    assert b[52:] == dist.fragments[3]
    assert b[26] == 0xDD and 26 + 2 + b[27] == len(b)           # the vendor element (0x88 long) runs to the end
    assert dist.ggid == (0x008000CD if gen == 4 else 0x1380)


def test_languages():
    assert ds.language_code("eng") == "ENG" and ds.language_code(5) == "GER" and ds.language_code("4") == "ITA"
    with pytest.raises(ValueError):
        ds.language_code("XYZ")
    assert set(ds.GEN4_GGID) == set(ds.LANGUAGES) == set(ds.LANGUAGE_NAMES)


def test_send_cycles_the_fragments_at_the_sender_pace():
    dist = ds.prepare(5, _bin(), "ENG")
    now = [0.0]
    sent: list[bytes] = []

    class Radio:
        def send(self, mpdu: bytes) -> None:
            sent.append(mpdu)

    def sleep(seconds: float) -> None:
        now[0] += seconds

    count = ds.send(Radio(), dist, 1.0, clock=lambda: now[0], sleep=sleep)
    assert count == len(sent) == round(1.0 / 0.021)
    assert [f[24 + 52:] for f in sent[:18]] == dist.fragments * 2
    assert [struct.unpack_from("<H", f, 22)[0] >> 4 for f in sent[:3]] == [0, 1, 2]


def test_send_stops_when_asked():
    dist = ds.prepare(4, _myg(), "ENG")
    sent = []

    class Radio:
        def send(self, mpdu: bytes) -> None:
            sent.append(mpdu)

    now = [0.0]
    ds.send(Radio(), dist, 60, stop=lambda: len(sent) >= 3, clock=lambda: now[0],
            sleep=lambda s: now.__setitem__(0, now[0] + s))
    assert len(sent) == 3


# -- PKHeX's own files -----------------------------------------------------------------------------------------------

def test_pkhex_gift_files_go_through():
    from pokeuds import pkhex

    if pkhex.helper_path() is None:
        pytest.skip("the PKHeX helper is not built")
    helper = pkhex.shared()
    for game in ("dp", "pt", "hgss", "bw", "b2w2"):
        for card in helper.gifts(game)[:2]:
            gift = helper.gift(game, key=card["key"])
            gen = ds.generation(game)
            dist = ds.prepare(gen, gift.wifi_data, "ENG")
            if gen == 4:
                assert ds.gen4_decode(dist.fragments) == gift.wifi_data
            else:
                assert ds.gen5_decode(dist.fragments) == ds.gen5_record(gift.wifi_data, 2)
