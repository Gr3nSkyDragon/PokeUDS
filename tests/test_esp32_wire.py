import struct
import zlib

import pytest

from pokeuds.radio import esp32_wire as wire


@pytest.mark.parametrize("length", [0, 1, 2, 253, 254, 255, 256, 600, wire.MAX_PAYLOAD])
def test_round_trip(length):
    payload = bytes((i * 7 + length) % 3 and (i % 251) or 0 for i in range(length))
    encoded = wire.encode(wire.Type.TX_FRAME, 5, 1, payload)
    assert encoded.endswith(b"\0") and b"\0" not in encoded[:-1]
    frames = wire.Decoder().feed(encoded)
    assert [(f.type, f.seq, f.flags, f.payload) for f in frames] == [(wire.Type.TX_FRAME, 5, 1, payload)]


def test_raw_layout_and_crc():
    raw = wire.cobs_decode(wire.encode(wire.Type.START, 2, 0, b"\x06\x00\x1f\x32\xaa\x00\x01")[:-1])
    assert raw[:6] == bytes([1, 0x02, 2, 0, 7, 0])
    assert struct.unpack("<I", raw[-4:])[0] == zlib.crc32(raw[:-4])


def test_byte_at_a_time_and_idle_delimiters():
    decoder = wire.Decoder()
    stream = b"\0\0" + wire.encode(wire.Type.PING, 1, 0, b"\1\2\3\4") + b"\0" + wire.encode(wire.Type.HELLO, 2, 0)
    frames = [f for byte in stream for f in decoder.feed(bytes([byte]))]
    assert [f.type for f in frames] == [wire.Type.PING, wire.Type.HELLO]
    assert decoder.bad_frames == 0


def test_corrupt_frame_is_dropped_and_decoder_resyncs():
    good = wire.encode(wire.Type.HELLO, 3, 0)
    bad = bytearray(wire.encode(wire.Type.RX, 4, 0, b"\x06\xc4\x00" + bytes(30)))
    bad[5] ^= 0x55
    decoder = wire.Decoder()
    frames = decoder.feed(bytes(bad) + good)
    assert [f.seq for f in frames] == [3]
    assert decoder.bad_frames == 1


def test_oversized_payload_is_refused():
    with pytest.raises(ValueError):
        wire.encode(wire.Type.TX_FRAME, 1, 0, bytes(wire.MAX_PAYLOAD + 1))


def test_stats():
    stats = wire.DeviceStats.parse(struct.pack("<9I", *range(1, 10)))
    assert stats.tx_unicast == 8 and stats.acks_rx == 9
    assert wire.DeviceStats.parse(struct.pack("<7I", *range(7))).tx_unicast == 0
