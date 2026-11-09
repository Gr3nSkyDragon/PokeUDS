"""Gen 7 local Mystery Gift: synthetic 200-byte beacon fragments, the OPSSR-signed message, and a gift heard over a
simulated air.

Every byte here is synthetic: the fragments are built from the format's constants (the magic, the distribution id,
the delivery word and the card id), the full file is generated in the test, and its CRC and signature are computed.
Nothing is a captured Nintendo distribution.
"""

import struct
import threading

import pytest

from pokeuds.games.gen7 import delivery
from pokeuds.radio.sim import Air, SimRadio
from pokeuds.uds import Station

needs_key = pytest.mark.skipif(
    delivery.RSA_D is None,
    reason="the distributor's signing key is not installed (pokeuds/games/gen7/delivery_key.py)",
)


def synthetic_appdata(payload: bytes, *, card_id: int = 289, count: int = 2, index: int = 0,
                      distribution_id: bytes = delivery.DISTRIBUTION_ID,
                      word: int = delivery.DELIVERY_WORD) -> bytes:
    """A synthetic 200-byte beacon: the format's header fields, this payload, zero padding, and its tag."""
    rest = (delivery.MAGIC + distribution_id + struct.pack("<I", word)
            + struct.pack("<HBB", card_id, count, index)
            + payload.ljust(delivery.PAYLOAD_SIZE, b"\0") + bytes(delivery.PADDING_SIZE))
    return delivery.crc16_usb(rest).to_bytes(2, "little") + rest


def synthetic_full(card_id: int = 289, seed: int = 0) -> bytes:
    """A synthetic 784-byte full file: a version, some text, a card and a valid full-file CRC."""
    full = bytearray(784)
    full[0:4] = (15).to_bytes(4, "little")                   # version mask: every Gen 7 game
    text = "Synthetic test gift".encode("utf-16-le")
    full[4:4 + len(text)] = text
    for i in range(delivery.CARD_AT + 2, len(full)):
        full[i] = (i * 17 + seed) & 0xFF
    full[delivery.CARD_AT:delivery.CARD_AT + 2] = card_id.to_bytes(2, "little")
    full[0x1FE:0x202] = b"\0\0\0\0"                           # no movie, language 0, not random
    full[0x202:0x204] = delivery.crc16_ccitt(bytes(full[:0x202] + b"\0\0" + full[0x204:])).to_bytes(2, "little")
    return bytes(full)


def test_the_tag_is_crc16_usb():
    assert delivery.crc16_usb(b"123456789") == 0xB4C8          # the CRC-16/USB check value
    frag = synthetic_appdata(bytes(range(160)))
    assert delivery.parse(frag) is not None
    assert int.from_bytes(frag[:2], "little") == delivery.beacon_tag(frag)


def test_a_synthetic_fragment_parses():
    parts = [delivery.parse(synthetic_appdata(bytes([i]) * 160, index=i)) for i in range(2)]
    assert all(parts)
    assert [(p.card_id, p.count, p.index, p.word) for p in parts] == [(289, 2, 0, 0xBA8), (289, 2, 1, 0xBA8)]
    assert all(p.distribution_id == delivery.DISTRIBUTION_ID for p in parts)
    assert parts[0].payload == bytes(160)
    assert parts[1].payload == bytes([1]) * 160
    assert parts[0].tag == int.from_bytes(synthetic_appdata(bytes(160))[:2], "little")


def test_a_damaged_or_foreign_beacon_is_not_a_fragment():
    frag = bytearray(synthetic_appdata(bytes(160)))
    frag[100] ^= 1
    assert delivery.parse(bytes(frag)) is None                  # the CRC-16/USB tag catches it
    assert delivery.parse(bytes(200)) is None and delivery.parse(b"\x01\x02\x03") is None
    frag = bytearray(synthetic_appdata(bytes(160)))
    frag[2:8] = bytes(6)
    assert delivery.parse(bytes(frag)) is None


@needs_key
def test_our_fragments_have_the_distributor_header():
    full = synthetic_full()
    parts = delivery.fragments(full)
    assert parts and all(len(a) == delivery.APPDATA_SIZE for a in parts)
    first = parts[0]
    assert first[2:8] == delivery.MAGIC
    assert first[8:16] == delivery.DISTRIBUTION_ID
    assert first[0x10:0x14] == struct.pack("<I", delivery.DELIVERY_WORD)
    assert first[0x14:0x18] == struct.pack("<HBB", 289, len(parts), 0)
    assert delivery.parse(first) is not None
    assert delivery.reassemble([delivery.parse(a) for a in parts]) == full


@needs_key
def test_a_gift_round_trips_and_an_event_can_hold_several_full_files():
    one = synthetic_full(card_id=2001)
    parts = delivery.fragments(one)
    assert 1 <= len(parts) <= 20
    assert delivery.parse(parts[0]).card_id == 2001
    assert delivery.reassemble([delivery.parse(a) for a in parts]) == one
    with pytest.raises(ValueError, match="1 to 20 full files"):
        delivery.fragments(one[:700])


@needs_key
def test_a_scanning_console_hears_the_whole_gift():
    """PokeUDS's beacons, cycling their fragments, give a scanning station every piece of the gift."""
    full = synthetic_full(seed=3)
    parts = delivery.fragments(full)
    air = Air()
    key = bytes.fromhex("000102030405060708090A0B0C0D0E0F")
    ours = Station(SimRadio(air), bytes.fromhex("020000000101"), key, name="PokeUDS")
    console = Station(SimRadio(air), bytes.fromhex("020000000102"), key, name="Console")
    heard: dict[int, delivery.Fragment] = {}
    console.on_beacon = lambda b: (f := delivery.parse(b.info.application_data)) and heard.setdefault(f.index, f)
    try:
        ours.start(delivery.CHANNEL)
        console.start(delivery.CHANNEL)
        ours.host(wlan_comm_id=0x00164810, passphrase=b"GNgBwMcVotYs\0", channel=delivery.CHANNEL,
                  max_nodes=delivery.MAX_NODES, application_data=parts[0], id_=delivery.NETWORK_ID8)
        stop = threading.Event()

        def cycle():
            i = 0
            while not stop.wait(0.06):
                i = (i + 1) % len(parts)
                ours.set_application_data(parts[i])
        threading.Thread(target=cycle, daemon=True).start()
        console.scan(3.0, wlan_comm_id=0x00164810, channels=(delivery.CHANNEL,),
                     until=lambda b: len(heard) == len(parts))
        stop.set()
    finally:
        ours.close()
        console.close()
        air.close()
    assert delivery.reassemble(list(heard.values())) == full


def test_sanitized_clears_the_card_date_and_computes_its_crc():
    full = bytearray(784)
    full[delivery.CARD_AT:delivery.CARD_AT + 2] = (289).to_bytes(2, "little")
    full[delivery.CARD_DATE:delivery.CARD_DATE + 3] = bytes.fromhex("f4fb03")  # a stamped date
    full[0x202:0x204] = b"xx"
    clean = delivery.sanitized(bytes(full))
    assert clean[delivery.CARD_DATE:delivery.CARD_DATE + 3] == b"\0\0\0"
    copy = bytearray(clean)
    stored = int.from_bytes(copy[0x202:0x204], "little")
    copy[0x202:0x204] = b"\0\0"
    assert delivery.crc16_ccitt(bytes(copy)) == stored
