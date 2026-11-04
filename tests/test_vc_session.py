"""The VC Pia layer: codec against a retail capture, our joiner fed the retail host's frames, and both
roles against each other."""

import json
import struct
from pathlib import Path

import pytest

from pokeuds.vc import pia
from pokeuds.vc.session import MESH_STATE, SYSTEM_DATA, Config, Role, Session, State

CAPTURE = json.loads((Path(__file__).parent / "data/vc_retail_session.json").read_text())
FRAMES = {f["name"]: bytes.fromhex(f["hex"]) for f in CAPTURE["frames"]}
APPDATA = bytes.fromhex(CAPTURE["application_data"])


@pytest.mark.parametrize("name", list(FRAMES))
def test_codec_rebuilds_every_captured_frame(name):
    raw = FRAMES[name]
    frame = pia.parse(raw)
    assert frame.crc_ok and frame.tail_ok
    if frame.kind == pia.KIND_PIA:
        assert pia.build(frame.messages, connection_id=frame.connection_id, packet_id=frame.packet_id,
                         clock=frame.clock, peer_clock=frame.peer_clock) == raw


def test_captured_units_and_profile_value():
    unit = pia.Unit.unpack(pia.parse(FRAMES["client unit"]).messages[0].payload)
    assert (unit.index, unit.ack, unit.byte, unit.role_flags) == (-2000, -1999, 0x60, b"\x01\x01\x00")
    assert unit.pack() == pia.parse(FRAMES["client unit"]).messages[0].payload
    window = [pia.Unit.unpack(m.payload) for m in pia.parse(FRAMES["host window"]).messages]
    assert len(window) == 25 and [u.index for u in window] == list(range(window[0].index, window[0].index + 25))
    assert pia.profile_value(APPDATA) == 0x8C4ED336
    assert pia.hello_reply() == FRAMES["join 0"]


def test_host_templates_are_the_captured_ones():
    mesh = [m.payload for m in pia.parse(FRAMES["join 8"]).messages if len(m.payload) == 128][0]
    assert MESH_STATE[:-4] == mesh[:-4]
    assert SYSTEM_DATA == pia.parse(FRAMES["join 12"]).messages[0].payload


def sent_messages(out):
    return [(m.protocol, m.payload) for raw, _ in out for m in (pia.parse(raw).messages if raw[1] == 1 else [])]


def test_joiner_answers_the_retail_hosts_frames():
    out = []
    s = Session(Role.JOINER, lambda raw, b: out.append((raw, b)), Config(name="BBGAME", application_data=APPDATA))
    s.start(0)
    s.receive(0, pia.hello(0x79CD0B19))
    assert out[0][0] == FRAMES["join 0"]                                   # the hello reply
    s.receive(100, FRAMES["join 2"])                                        # host station info, seq c14e4a1c
    s.receive(170, FRAMES["join 5"])                                        # host profile, seq ...1d
    msgs = sent_messages(out)
    acks = [p for proto, p in msgs if proto == pia.PROTO_SETUP and len(p) == 8]
    assert acks[:2] == [bytes.fromhex("05000000c14e4a1c"), bytes.fromhex("05000000c14e4a1d")]
    station = next(p for proto, p in msgs if proto == pia.PROTO_SETUP and len(p) == 62)
    assert station[0] == 1 and station[3] == 0 and station[11] == 2         # not a host; station id 2
    profile = next(p for proto, p in msgs if proto == pia.PROTO_SETUP and len(p) == 80)
    assert profile[68] == 0x06 and struct.unpack_from(">I", profile, 70)[0] == 0x8C4ED336
    assert profile[4:16] == "BBGAME".encode("utf-16-le")
    join = next(p for proto, p in msgs if proto == pia.PROTO_SYSTEM and len(p) == 16)
    assert join[:2] == b"\x01\xfd" and join[11] == 2
    s.receive(260, FRAMES["join 8"])                                        # ping + mesh state
    assert s.state is State.JOINED and s.station == pia.STATION_JOINER and s.numbered
    s.receive(320, FRAMES["join 12"])                                       # system data, index -2001
    last = pia.parse(out[-1][0]).messages[-1]
    assert last.protocol == pia.PROTO_SYSTEM and pia.record_ack_index(last.payload) == -2000
    pongs = [p for proto, p in sent_messages(out) if proto == pia.PROTO_PING and p[3] == 1]
    assert pongs and pongs[0][8:] == bytes.fromhex("00000025c3b5d2f0")     # the host's tick echoed


def linked_sessions():
    """Our host and our joiner, frames passed directly."""
    a_out, b_out = [], []
    host = Session(Role.HOST, lambda raw, bc: a_out.append(raw), Config(name="HOST", application_data=APPDATA))
    joiner = Session(Role.JOINER, lambda raw, bc: b_out.append(raw), Config(name="JOIN", application_data=APPDATA))
    host.start(0)
    joiner.start(0)

    def pump(now):
        host.poll(now)
        joiner.poll(now)
        for _ in range(4):
            while a_out:
                joiner.receive(now, a_out.pop(0))
            while b_out:
                host.receive(now, b_out.pop(0))
    pump.queues = (a_out, b_out)
    return host, joiner, pump


def test_host_and_joiner_reach_the_game_stream():
    host, joiner, pump = linked_sessions()
    for now in range(0, 3000, 50):
        pump(now)
    assert host.state is State.JOINED and joiner.state is State.JOINED
    assert joiner.station == pia.STATION_JOINER
    host.queue_unit(0xEF)
    host.flush(3000)
    pump(3000)
    assert joiner.pop_units() == [0xEF]
    for b in (0x00, 0x60, 0x60):
        joiner.queue_unit(b)
    joiner.flush(3050)
    pump(3050)
    assert host.pop_units() == [0x00, 0x60, 0x60]
    assert host.send_acked == pia.FIRST_INDEX + 1 and joiner.send_acked == pia.FIRST_INDEX + 3


def test_lost_units_are_resent():
    host, joiner, pump = linked_sessions()
    for now in range(0, 3000, 50):
        pump(now)
    _, to_host = pump.queues
    joiner.queue_unit(0x42)
    joiner.flush(3000)
    assert to_host
    to_host.clear()                     # the frame is lost on the air
    joiner.poll(3200)
    assert not to_host                  # not yet: re-sends wait 500 ms
    joiner.poll(3600)
    assert to_host                      # re-sent from the oldest unacknowledged index
    pump(3600)
    assert host.pop_units() == [0x42] and joiner.send_acked == pia.FIRST_INDEX + 1


def test_silence_closes_and_bye_is_honoured():
    host, joiner, pump = linked_sessions()
    for now in range(0, 2000, 50):
        pump(now)
    joiner.poll(20000)
    assert joiner.state is State.CLOSED
    host.receive(2000, pia.bye())
    assert host.state is State.CLOSED


def test_analyze_reads_vc_streams_from_a_transcript():
    from pokeuds.capture import vc as vccap
    from pokeuds.capture.transcript import Message

    messages = [Message(i, f["seconds"], "rx" if f["from_host"] else "tx", pia.UDS_CHANNEL, i, 1, 2,
                        bytes.fromhex(f["hex"])) for i, f in enumerate(CAPTURE["frames"])]
    streams = vccap.unit_streams(messages)
    assert streams["tx"] == [0x60]                                     # the joiner's unit -2000
    host = streams["rx"]
    assert host[0] == 0x00 and len(host) == 26                         # the host unit, then a 25-unit window
