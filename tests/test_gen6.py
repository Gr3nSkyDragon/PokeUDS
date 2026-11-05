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


@pytest.fixture(autouse=True)
def quick_syncs(monkeypatch):
    # Two PokeUDS engines would each wait SYNC_ASK_AFTER for the other at every sync point; a console asks on its own.
    monkeypatch.setattr(session, "SYNC_ASK_AFTER", 0.05)
    monkeypatch.setattr(session, "SCREEN_AFTER", 0.05)


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


def test_pss_beacon_keeps_the_template_apart_from_who_we_are():
    """Only identity changes: name, PSS key and id, version. Byte 0x70 (04 in every console's beacon) once got the
    version written over it, and the PSS greyed out Trade for us."""
    me = trainer("POKEUDS", wire.VERSION_CODES["Y"])
    ours, template = wire.pss_beacon(me), bytes.fromhex(wire.templates.IDLE_BEACON)
    identity = {4, *range(8, 16), *range(0x1C, 0x1C + wire.NAME_BYTES), *range(0x58, 0x64), 0x6E}
    assert [i for i in range(2, len(ours)) if ours[i] != template[i] and i not in identity] == []
    assert ours[0x70] == 0x04 and ours[0x6E] == wire.VERSION_CODES["Y"]
    assert wire.parse_pss_beacon(ours).version == wire.VERSION_CODES["Y"]


def test_parses_a_retail_beacon():
    # Pokemon Y idling in the PSS (trainer, PSS id and key replaced).
    y = bytearray(bytes.fromhex(
        "a30100011901024100112233445566779c0401003905c7052ca47f3252006500660072006500730068000000000000000000000000"
        "004e00690063006500200074006f0020006d0065006500740020007900610021000000aabbccdd001122334455667700004108000000"
        "010101190004000000c0382402002013034105000000000000070000008b0201000000700000000000000044440442060000000000"
        "0000000000000000000b0b6c000000000000000000000000"))
    y[0:2] = wire.crc16(bytes(y[2:])).to_bytes(2, "little")
    player = wire.parse_pss_beacon(bytes(y))
    assert (player.name, player.version, player.pss_id, player.key) == (
        "Refresh", wire.VERSION_CODES["Y"], bytes.fromhex("0011223344556677"), bytes.fromhex("aabbccdd"))
    assert player.request == 0


def test_pss_network_carries_the_default_attributes(air, tmp_path):
    key = os.urandom(16)
    ours = gen6_trade(air, key, tmp_path, "02000000A611", make_record(7), "POKEUDS", (6,))
    listener = Station(SimRadio(air), bytes.fromhex("02000000A612"), key)
    listener.start(6)
    try:
        ours._advertise()
        heard = listener.scan(1.0, wlan_comm_id=0x00055D10, channels=(6,), until=lambda b: True)
        assert heard and heard[0].info.attributes == wire.NETWORK_ATTRIBUTES and heard[0].info.id == 2
    finally:
        listener.close()
        ours.station.close()


def test_pss_identity_survives_saving_the_builders_trainer(tmp_path, monkeypatch):
    import argparse

    from pokeuds import cli
    from pokeuds.app.settings import Settings

    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    args = argparse.Namespace(trainer=None, tid=None, sid=None, name="PokeUDS")
    first = cli._gen6_trainer(get("xy"), make_record(8), args)
    settings = Settings.load()
    settings.trainer = {"ot": "ASH", "tid": 1, "sid": 2, "female": False}   # what the Builder saves
    settings.save()
    again = cli._gen6_trainer(get("xy"), make_record(8), args)
    assert (again.pss_id, again.key) == (first.pss_id, first.key) and again.name == "ASH"


def test_request_and_answer_change_what_a_console_changes():
    """A retail X/Y asking changes its CRC, state (0x17), nonce (0x18), token/partner (0x84-0x93), flag (0x94) and
    kind (0x96); its token is its nonce. An answer says "answering" (0x99): one left "idle" (0x05) was ignored."""
    me, console = trainer("POKEUDS"), trainer("Serena", wire.VERSION_CODES["Y"])
    them = wire.parse_pss_beacon(wire.pss_beacon(console))
    idle = wire.pss_beacon(me)
    asking = wire.pss_beacon(me, partner=them, request=True)
    changed = {i for i in range(len(idle)) if idle[i] != asking[i]}
    assert changed <= {0, 1, 0x17, *range(0x18, 0x1C), *range(0x84, 0x94), 0x94, 0x96}
    parsed = wire.parse_pss_beacon(asking)
    assert parsed.state == wire.PSS_ASKING and parsed.request == 1 and parsed.wants(console.pss_id)
    assert asking[0x18:0x1C] == parsed.token and asking[0x96] == 0x02 and asking[0x97:0x99] == b"\x44\x44"
    answer = wire.parse_pss_beacon(wire.pss_beacon(console, partner=parsed))
    assert answer.state == wire.PSS_ANSWERING and answer.request == 0 and answer.token == parsed.token
    assert answer.answers(me.pss_id) and not answer.wants(me.pss_id)
    assert wire.parse_pss_beacon(idle).state == wire.PSS_IDLE


def test_a_sync_request_the_console_dropped_is_sent_again(air, tmp_path, monkeypatch):
    """A console drops a sync request that arrives before it reaches the point (after a swap it is still playing the
    animation). PokeUDS asks again, so the trade finishes instead of hanging at "Communicating"."""
    monkeypatch.setattr(session, "SYNC_RESEND", 0.3)
    dropped = []
    real = session.Gen6Session._peer_sync

    def console_drops_the_first_hub_request(self, body):
        if self.trainer.name == "Serena" and body.ident == wire.SYNC_HUB + 1 and not body.done and not dropped:
            dropped.append(body.ident)
            return
        real(self, body)

    monkeypatch.setattr(session.Gen6Session, "_peer_sync", console_drops_the_first_hub_request)
    key = os.urandom(16)
    ours = gen6_trade(air, key, tmp_path / "ours", "02000000A621", make_record(0x31, species=658), "POKEUDS", (6,))
    console = gen6_trade(air, key, tmp_path / "console", "02000000A622", make_record(0x32, species=653), "Serena", (6,))
    errors = []

    def run(trade, role):
        try:
            trade.run(role, 20.0)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(f"{role}: {exc!r}")

    threads = [threading.Thread(target=run, args=(ours, "join"), daemon=True),
               threading.Thread(target=run, args=(console, "host"), daemon=True)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert dropped == [wire.SYNC_HUB + 1] and not errors and len(ours.saved) == 1 and len(console.saved) == 1, errors
    finally:
        ours.station.close()
        console.station.close()


def test_the_session_ends_as_a_consoles_does(air, tmp_path, monkeypatch):
    """After the last trade the trade screen opens again (09), the player leaves from it (05), and the session ends
    with the leave block and syncs 0x0A and 0x16. No console sends a PRUDP disconnect, so PokeUDS does not either."""
    sent, disconnects = [], []
    real_send = session.Gen6Session._gfl_send

    def record(self, body):
        if self.trainer.name == "POKEUDS":
            b = wire.parse_body(body)
            sent.append((b.kind, b.module, b.ident) if b.kind != "sync" else ("sync", b.module, b.ident, b.done))
        real_send(self, body)

    monkeypatch.setattr(session.Gen6Session, "_gfl_send", record)
    monkeypatch.setattr(prudp.Endpoint, "disconnect", lambda self: disconnects.append(self.node))
    key = os.urandom(16)
    ours = gen6_trade(air, key, tmp_path / "ours", "02000000A631", make_record(0x41, species=658), "POKEUDS", (6,))
    console = gen6_trade(air, key, tmp_path / "console", "02000000A632", make_record(0x42, species=653), "Serena", (6,))
    ours.trades = console.trades = 2
    errors = []

    def run(trade, role):
        try:
            trade.run(role, 20.0)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(f"{role}: {exc!r}")

    threads = [threading.Thread(target=run, args=(ours, "join"), daemon=True),
               threading.Thread(target=run, args=(console, "host"), daemon=True)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert not errors and not any(t.is_alive() for t in threads), errors
        assert len(ours.saved) == 2 and disconnects == []
        commands = [s for s in sent if s[0] == "command"]
        last_swap = max(i for i, c in enumerate(commands) if c[2] == wire.CMD_DONE)
        assert [c[2] for c in commands[last_swap + 1:]] == [wire.CMD_STATE, wire.CMD_SCREEN, wire.CMD_QUIT,
                                                          wire.CMD_LEAVE_DATA]
        syncs = [s for s in sent if s[0] == "sync" and not s[3]]
        assert syncs[-2:] == [("sync", wire.TRADE, wire.SYNC_LEAVE, False), ("sync", wire.SESSION, wire.SYNC_END, False)]
    finally:
        ours.station.close()
        console.station.close()
