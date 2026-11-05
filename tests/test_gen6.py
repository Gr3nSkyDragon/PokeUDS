"""Generation 6: PRUDP as Pokemon X signs it, gflnet, PSS beacons, and whole trades over the simulated air."""

import os
import threading
import time

import pytest

from pokeuds import pkx
from pokeuds.games import get, profile
from pokeuds.games.gen6 import session, wire
from pokeuds.games.gen6.trade import Gen6Trade
from pokeuds.nex import prudp
from pokeuds.radio.sim import Air, SimRadio
from pokeuds.uds import Station
from tests.test_pkx import make_record

# Packets Pokemon X sent (azahar_uds logs): (sender node, receiver node, UDS payload).
CAPTURED = [
    (2, 1, "02010201010039000000000000000000ead0011b00001111c0000000000016535b1b6e65665bdea423826163f02e"
           "000402000000011000000000000000000000000000000000040100"),                      # SYN
    (1, 2, "02010102010039000000000000000000ead0011b0000111190000000000081b1102ceb1c42063072f8e155a2b7e5"
           "000402000000011029d8ab2bb34987a40c6e4f68e383847d040100"),                      # SYN-ACK
    (2, 1, "02010201010049000000000000000000ead0011f0c001111e100a2000100bfaa139e392fb973b8eba117fbed5f10"
           "00040200000001109dca285364b71c1094c8e74f87284c530302fd34040100080000000000000000000000"),  # CONNECT
    (2, 1, "0201020101005e000000000000000000ead001033d001111e200a20002002efc1e9d8014039b64a77a31899f73d6"
           "020100390000000000000202120300030004000800000000000000000000000000000100000000000001000001"
           "000000000000002c0100000000000003030300"),                                        # Net-Z
    (1, 2, "02010102010025000000000000000000ead001030400111182208c01000099567f1a0f5b545a18d10e3efa68255a"
           "02010000000200"),                                                                 # multi-ack
    (1, 2, "0201010201003f000000000000000000ead001031e001111e2088c001d00d074cdf602c7e5a9a6a632c876c684c6"
           "020100de2b0a000200000001020000010000000102000002000000020000001100"),            # gflnet sync
    (2, 1, "0201020101001e000000000000000000ead0010000001111c400a200010060b7d12890b41caba158555a566a53c8"),  # PING
]


@pytest.mark.parametrize("src,dst,payload", CAPTURED)
def test_captured_packets_verify_and_reencode(src, dst, payload):
    raw = bytes.fromhex(payload)
    assert prudp.unwrap(raw)[:2] == (src, dst)
    packet = prudp.decode(prudp.unwrap(raw)[2])
    assert packet.signature == prudp.signature(packet, prudp.CONNECTION_SIGNATURE[dst])
    assert prudp.wrap(src, dst, prudp.encode(packet, prudp.CONNECTION_SIGNATURE[dst])) == raw


def test_captured_gflnet_sync():
    packet = prudp.decode(prudp.unwrap(bytes.fromhex(CAPTURED[5][2]))[2])
    assert packet.flags & prudp.F_GAME
    message = wire.gfl_decode(packet.payload)
    assert (message.index, message.counts) == (2, wire.counts_word(1, 0))
    body = wire.parse_body(message.body)
    assert (body.kind, body.module, body.ident, body.done) == ("sync", 0, wire.SYNC_START, False)
    assert wire.gfl_encode(2, message.counts, wire.sync(wire.SYNC_REQUEST, 0, wire.SYNC_START)) == packet.payload


def test_endpoint_pair_connects_and_delivers_in_order():
    wires = {1: [], 2: []}
    host = prudp.Endpoint(wires[2].append, 1, 2, client=False)
    client = prudp.Endpoint(wires[1].append, 2, 1, client=True)
    got = {1: [], 2: []}

    def run():
        for _ in range(50):
            for node, ep in ((1, host), (2, client)):
                while wires[node]:
                    got[node] += ep.feed(wires[node].pop(0))
            if host.open_both and client.open_both:
                return

    client.open()
    run()
    host.open()
    run()
    assert host.open_both and client.open_both
    big = os.urandom(3000)
    client.send_reliable(b"netz")
    client.send_reliable(big, game=True)
    run()
    assert [(m.stream, m.payload) for m in got[1]] == [("netz", b"netz"), ("game", big)]
    assert client.idle


def trainer(name, version=wire.VERSION_CODES["X"]):
    return wire.Trainer.new(name, 1234, 5678, version)


def test_pss_beacon_round_trip_and_request():
    me, them = trainer("POKEUDS"), trainer("Serena")
    idle = wire.pss_beacon(me)
    assert len(idle) == 184 and wire.crc16(idle[2:]) == int.from_bytes(idle[:2], "little")
    seen = wire.parse_pss_beacon(idle)
    assert (seen.name, seen.pss_id, seen.key, seen.request) == ("POKEUDS", me.pss_id, me.key, 0)
    other = wire.parse_pss_beacon(wire.pss_beacon(them))
    asking = wire.parse_pss_beacon(wire.pss_beacon(me, partner=other, request=True, token=b"tokn"))
    assert asking.wants(them.pss_id) and asking.token == b"tokn"
    answer = wire.parse_pss_beacon(wire.pss_beacon(them, partner=asking))
    assert answer.answers(me.pss_id) and not answer.wants(me.pss_id) and answer.token == b"tokn"


def test_profile_carries_the_trainer():
    me = trainer("POKEUDS")
    p = wire.profile(me, partner_id=b"partner!", node=2)
    assert len(p) == 1616
    assert p[wire.P_NAME:wire.P_NAME + 14].decode("utf-16-le") == "POKEUDS"
    assert int.from_bytes(p[0:2], "little") == 1234 and int.from_bytes(p[2:4], "little") == 5678
    assert p[wire.P_ID:wire.P_ID + 8] == me.pss_id and p[wire.P_PARTNER_ID:wire.P_PARTNER_ID + 8] == b"partner!"
    assert p[wire.PLAYER_RECORD + wire.R_ID:wire.PLAYER_RECORD + wire.R_ID + 8] == me.pss_id


@pytest.fixture
def air():
    a = Air()
    yield a
    a.close()


def gen6_trade(air, key, tmp, mac, record, name, channels):
    station = Station(SimRadio(air), bytes.fromhex(mac), key, name=name)
    station.start(channels[0])
    prof = profile.load("xy")
    return Gen6Trade(station, get("xy"), prof, record, tmp, trainer(name), channels=channels)


@pytest.mark.parametrize("pokeuds_role", ["join", "host"])
def test_whole_trade_over_the_air(air, tmp_path, pokeuds_role):
    """PokeUDS (hopping 1/6/11) against a second engine standing in for a console on channel 6."""
    key = os.urandom(16)
    ours_offer = make_record(0x11111111, species=658, nickname="Froakie")
    console_offer = make_record(0x22222222, species=653, nickname="Fennekin")
    ours = gen6_trade(air, key, tmp_path / "ours", "02000000A601", ours_offer, "POKEUDS", (1, 6, 11))
    console = gen6_trade(air, key, tmp_path / "console", "02000000A602", console_offer, "Serena", (6,))
    console_role = "host" if pokeuds_role == "join" else "join"
    errors = []

    def run(trade, role):
        try:
            trade.run(role, 20.0)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(f"{role}: {exc!r}")

    threads = [threading.Thread(target=run, args=(ours, pokeuds_role), daemon=True),
               threading.Thread(target=run, args=(console, console_role), daemon=True)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert not errors and not any(t.is_alive() for t in threads), errors
        (mine,) = ours.saved
        (theirs,) = console.saved
        assert mine.read_bytes() == console_offer[:pkx.STORED_SIZE]
        assert theirs.read_bytes() == ours_offer[:pkx.STORED_SIZE]
        assert "_xy_trade1_653_Fennekin_" in mine.name
    finally:
        ours.station.close()
        console.station.close()


def test_box_record_is_padded_without_the_helper(monkeypatch):
    import pokeuds.pkhex as pkhex

    monkeypatch.setattr(pkhex, "shared", lambda: (_ for _ in ()).throw(pkhex.HelperMissing("no helper")))
    box = make_record(5, size=pkx.STORED_SIZE)
    party = session.party_record(box)
    assert len(party) == pkx.PARTY_SIZE and party[:pkx.STORED_SIZE] == box and pkx.checksum_valid(party)


def test_analyze_decodes_captured_packets():
    from pokeuds.capture import nex
    from pokeuds.capture.transcript import Message

    messages = [Message(i, float(i), "tx", 243, i, src, dst, bytes.fromhex(hexed)) for i, (src, dst, hexed) in enumerate(CAPTURED)]
    decoded = nex.decode(messages)
    assert (decoded.packets, decoded.signed_ok) == (len(CAPTURED), len(CAPTURED))
    texts = [e.text for e in decoded.events]
    assert "sync 02 module 0 id 11" in texts and any(t.startswith("Net-Z type 00") for t in texts)
