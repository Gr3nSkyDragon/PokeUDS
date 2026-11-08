"""Gen 6 local Mystery Gift: the distributor's beacon fragments, LZ10, and a distribution heard over a simulated air."""

import os
import struct
import threading
import time

import pytest

from pokeuds.games.gen6 import delivery
from pokeuds.radio.sim import Air, SimRadio
from pokeuds.uds import Station

# The two beacons of a real Gen 6 distribution of card 165, as `pokeuds scan --json` saved them.
CAPTURED = [bytes.fromhex(
    "fb9a07011801024122e96d80edd99969a8050000a5000200100300003101000010100300000c000000dd30b13000c330c830e230f33000"
    "b930bf30fc3020000042304b30fb307f300069308a300a006f300464307030440013893000003012ff10ff573000853046306d3093300501"
    "ff0a0044001f820019d5104910459200254a001f48001f000a00573066304f30448c0005003042004f4c306e68003b10375300151053103b"
    "880b308d305700276d00510094fff002f0"), bytes.fromhex(
    "188407011801024122e96d80edd99969a8050000a500020114f026f038f04af05cf06ef080fff092f0a4f0b6f0c8f0daf0ecf0fef110f0"
    "f122f134f146f1580036020002464646a500de01fbc008306e30b30005c330af8201e7d730ec30bc0213c87130f18ff1a1b1b33a0001f1c4"
    "0200ff00e408a561dc80200010027d5d003200f40c00850036f1f4820609000101030000879c14404872ff100140b3f22d00001027021e28"
    "6bee505df251f263127500000000000000")]


def full_file_checksum_ok(full: bytes) -> bool:
    copy = bytearray(full)
    stored = struct.unpack_from("<H", copy, 0x202)[0]
    copy[0x202:0x204] = b"\0\0"
    return delivery.crc16(bytes(copy)) == stored


def test_the_captured_distribution_decodes():
    parts = [delivery.parse(a) for a in CAPTURED]
    assert all(parts) and [p.index for p in parts] == [0, 1] and parts[0].count == 2 and parts[0].card_id == 165
    full = delivery.reassemble(list(reversed(parts)))       # any order
    assert len(full) == 784 and delivery.card_id(full) == 165 and full_file_checksum_ok(full)
    assert full[0] == 0x0C                                  # for Alpha Sapphire and Omega Ruby
    assert full[4:22].decode("utf-16-le") == "ポケットモンスター"


def test_our_fragments_match_the_distributor_header_for_header():
    full = delivery.reassemble([delivery.parse(a) for a in CAPTURED])
    ours = delivery.fragments(full)
    assert [a[2:delivery.HEADER_SIZE] for a in ours] == [a[2:delivery.HEADER_SIZE] for a in CAPTURED]
    assert delivery.reassemble([delivery.parse(a) for a in ours]) == full


def test_a_damaged_or_foreign_beacon_is_not_a_fragment():
    bad = bytearray(CAPTURED[0])
    bad[100] ^= 1
    assert delivery.parse(bytes(bad)) is None
    assert delivery.parse(bytes(184)) is None and delivery.parse(b"\x01\x02\x03") is None
    with pytest.raises(ValueError, match="missing"):
        delivery.reassemble([delivery.parse(CAPTURED[0])])


def test_an_event_with_several_full_files():
    """An event's file can hold several full files (a card's X/Y and ORAS copies): one message carries them all."""
    one = delivery.reassemble([delivery.parse(a) for a in CAPTURED])
    two = one + bytes([0x03]) + one[1:]
    parts = delivery.fragments(two)
    assert delivery.parse(parts[0]).card_id == 165 and delivery.reassemble([delivery.parse(a) for a in parts]) == two
    with pytest.raises(ValueError, match="1 to 20 full files"):
        delivery.fragments(one[:700])


@pytest.mark.parametrize("data", [b"", b"a", bytes(784), os.urandom(784), b"abc" * 300])
def test_lz10_round_trip(data):
    assert delivery.lz10_decompress(delivery.lz10_compress(data)) == data


def test_a_scanning_console_hears_the_whole_gift():
    """PokeUDS's beacons, cycling their fragments, give a scanning station every piece of the gift."""
    full = bytearray(os.urandom(784))                      # incompressible: the most fragments a gift needs
    full[delivery.CARD_AT:delivery.CARD_AT + 2] = (2001).to_bytes(2, "little")
    parts = delivery.fragments(bytes(full))
    assert len(parts) >= 5
    air = Air()
    key = os.urandom(16)
    ours = Station(SimRadio(air), bytes.fromhex("020000000101"), key, name="PokeUDS")
    console = Station(SimRadio(air), bytes.fromhex("020000000102"), key, name="Console")
    heard: dict[int, delivery.Fragment] = {}
    console.on_beacon = lambda b: (f := delivery.parse(b.info.application_data)) and heard.setdefault(f.index, f)
    try:
        ours.start(11)
        console.start(11)
        ours.host(wlan_comm_id=0x00055D10, passphrase=b"x\0", channel=11, max_nodes=2, application_data=parts[0],
                  id_=delivery.NETWORK_ID8)
        stop = threading.Event()

        def cycle():
            i = 0
            while not stop.wait(0.06):
                i = (i + 1) % len(parts)
                ours.set_application_data(parts[i])
        threading.Thread(target=cycle, daemon=True).start()
        console.scan(3.0, wlan_comm_id=0x00055D10, channels=(11,), until=lambda b: len(heard) == len(parts))
        stop.set()
    finally:
        ours.close()
        console.close()
        air.close()
    assert delivery.reassemble(list(heard.values())) == bytes(full)
