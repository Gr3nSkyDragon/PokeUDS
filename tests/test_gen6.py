"""Generation 6: PRUDP as Pokemon X signs it, gflnet, PSS beacons, and whole trades over the simulated air."""

import os
import struct
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


def test_a_queue_offers_each_pokemon_in_turn(air, tmp_path):
    key = os.urandom(16)
    first, second = make_record(0x11111111, species=658, nickname="Froakie"), make_record(0x33333333, species=650,
                                                                                            nickname="Chespin")
    ours = gen6_trade(air, key, tmp_path / "ours", "02000000A601", first, "POKEUDS", (6,))
    ours.queue, ours.queue_party, ours.trades = [second], [session.party_record(second)], 2
    console = gen6_trade(air, key, tmp_path / "console", "02000000A602",
                         make_record(0x22222222, species=653, nickname="Fennekin"), "Serena", (6,))
    console.trades = 2
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
        assert [p.read_bytes() for p in console.saved] == [first[:pkx.STORED_SIZE], second[:pkx.STORED_SIZE]]
        assert len(ours.saved) == 2
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
    assert asking[0x18:0x1C] == parsed.token and asking[0x96] == 0x01 and asking[0x97:0x99] == b"\x44\x44"
    answer = wire.parse_pss_beacon(wire.pss_beacon(console, partner=parsed))
    assert answer.state == wire.PSS_ANSWERING and answer.request == 0 and answer.token == parsed.token
    assert answer.answers(me.pss_id) and not answer.wants(me.pss_id)
    assert wire.parse_pss_beacon(idle).state == wire.PSS_IDLE


# Captured on hardware (pokeuds-run.log, 2026-11-05): PokeUDS idle in the PSS, then retail Y ("Refresh") idle, and
# Refresh asking PokeUDS for a trade.
POKEUDS_IDLE = bytes.fromhex(
    "9503000118020241ac8af6f03d99420a9c0401003905c705514acb3050006f006b0065005500440053000000000000000000000000004e0069"
    "0063006500200074006f0020006d0065006500740020007900610021000000a02d0bbeac8af6f03d99420a0101e000000002310b07180104"
    "0000004864090101cb080341000000000000006a0000008a0200000000a0000000000000004444445e0000070000c0010000000000000000"
    "0b0b6c000000000000000000000000")
REFRESH_IDLE = bytes.fromhex(
    "4eff0001190202416bef19746f17b7529c0401003905c705cbb37f3252006500660072006500730068000000000000000000000000004e0069"
    "0063006500200074006f0020006d006500650074002000790061002100000032b4aa6d6bef19746f17b752000041080000003101011901"
    "04000000c03824020020130341050000000000000f0000008b0201000000f000000000000000444444640600000000000000000000000000"
    "000b0b6c000000000000000000000000")
REFRESH_ASKING = bytes.fromhex(
    "c8780001190202416bef19746f17b7529c0401003905c796d7b37f3252006500660072006500730068000000000000000000000000004e0069"
    "0063006500200074006f0020006d006500650074002000790061002100000032b4aa6d6bef19746f17b752000041080000003101011901"
    "04000000c0382402002013034105000000000000d7b37f32a02d0bbeac8af6f03d99420a0100014444640600000000000000000000000000"
    "000b0b6c000000000000000000000000")


def test_our_request_matches_a_retail_request():
    """PokeUDS asking must write the request bytes a retail console writes: state 0x17, the token, the partner's key
    and id, the flag at 0x94 and the kind at 0x96 (01 for a trade; PokeUDS sent 02 and "PokeUDS asks" failed)."""
    refresh = wire.parse_pss_beacon(REFRESH_IDLE)
    pokeuds = wire.parse_pss_beacon(POKEUDS_IDLE)
    assert refresh and pokeuds
    me = wire.Trainer("Refresh", 0, 0, refresh.pss_id, refresh.key, refresh.version)
    ours = wire.pss_beacon(me, partner=pokeuds, request=True, token=REFRESH_ASKING[0x18:0x1C])
    assert ours[0x17] == REFRESH_ASKING[0x17]
    assert ours[0x18:0x1C] == REFRESH_ASKING[0x18:0x1C]
    assert ours[0x84:0x99].hex() == REFRESH_ASKING[0x84:0x99].hex()
    asked = wire.parse_pss_beacon(REFRESH_ASKING)
    assert asked.wants(pokeuds.pss_id) and asked.request_kind[:1] == wire.REQUEST_KIND_TRADE


# Pokemon X ("Dummy") asking retail Y (Refresh) in Azahar (azahar_log.txt, 2026-10-17): the application data of the
# trade network X hosted once Refresh had answered. Refresh joined it 3.7 s later.
X_TRADE_NETWORK_DATA = bytes.fromhex(
    "dc32000118020241ec417cc6f91945959c0401003905c798754acb30440075006d006d00790000000000000000000000000000000000"
    "4e00690063006500200074006f0020006d0065006500740020007900610021000000" "60a0c52eec417cc6f91945950101e000000002"
    "310b071801040000004864090101cb080341000000000000005f4acb3032b4aa6d6bef19746f17b7520100014444580000070000c001"
    "00000000000000000d116c000000000000000000000000")


def test_the_trade_network_carries_the_request_as_the_games_does():
    """The asker's trade network names the console it asked: its request beacon in state "trading" (0x98), the
    token kept and the nonce moved on. PokeUDS hosted it with no data and the console never joined."""
    x = wire.parse_pss_beacon(X_TRADE_NETWORK_DATA)
    refresh = wire.parse_pss_beacon(REFRESH_IDLE)
    assert x and refresh and X_TRADE_NETWORK_DATA[0x17] == wire.PSS_TRADING
    me = wire.Trainer("Dummy", 0, 0, x.pss_id, x.key, x.version)
    token = X_TRADE_NETWORK_DATA[0x84:0x88]
    ours = wire.trade_network_data(me, refresh, token)
    assert wire.parse_pss_beacon(ours) is not None                       # the CRC holds
    assert ours[0x17] == wire.PSS_TRADING and ours[0x84:0x99].hex() == X_TRADE_NETWORK_DATA[0x84:0x99].hex()
    assert ours[0x18] != token[0] and ours[0x19:0x1C] == token[1:]


def test_pokeuds_trade_network_carries_its_request(air, tmp_path, monkeypatch):
    key = os.urandom(16)
    ours = gen6_trade(air, key, tmp_path / "ours", "02000000A641", make_record(0x51), "POKEUDS", (6,))
    console = gen6_trade(air, key, tmp_path / "console", "02000000A642", make_record(0x52), "Serena", (6,))
    hosted = []
    real_host = ours.station.host

    def host(**kw):
        hosted.append(kw)
        return real_host(**kw)

    monkeypatch.setattr(ours.station, "host", host)
    monkeypatch.setattr(ours, "_session", lambda partner: None)
    monkeypatch.setattr(console, "_session", lambda partner: None)
    errors = []

    def run(trade, role):
        try:
            trade.run(role, 10.0)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(f"{role}: {exc!r}")

    threads = [threading.Thread(target=run, args=(ours, "host"), daemon=True),
               threading.Thread(target=run, args=(console, "join"), daemon=True)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert not errors, errors
        trade = [kw for kw in hosted if kw["id_"] == 1]
        assert trade and trade[0]["channel"] == 6
        data = wire.parse_pss_beacon(trade[0]["application_data"])
        assert trade[0]["application_data"][0x17] == wire.PSS_TRADING and data.wants(console.trainer.pss_id)
    finally:
        ours.station.close()
        console.station.close()


def test_a_stale_partner_is_not_an_answer():
    """An idle beacon can still name PokeUDS at 0x84-0x93 (old values stay there); only state "answering" (0x99)
    with our id is an answer. Reading the partner bytes alone hosted the trade network before the player accepted."""
    me, console = trainer("POKEUDS"), trainer("Serena", wire.VERSION_CODES["Y"])
    asking = wire.parse_pss_beacon(wire.pss_beacon(me, partner=wire.parse_pss_beacon(wire.pss_beacon(console)),
                                                   request=True))
    stale = bytearray(wire.pss_beacon(console, partner=asking))
    stale[0x17] = wire.PSS_IDLE
    struct.pack_into("<H", stale, 0, wire.crc16(bytes(stale[2:])))
    idle = wire.parse_pss_beacon(bytes(stale))
    assert idle.partner_id == me.pss_id and not idle.answers(me.pss_id)
    assert wire.parse_pss_beacon(wire.pss_beacon(console, partner=asking)).answers(me.pss_id)


def test_asking_stays_on_the_consoles_channel(air, tmp_path, monkeypatch):
    """Once PokeUDS has asked, its request stays on the console's channel until the answer (or the wait ends), as
    the game's does; it used to hop 1/6/11 after 8 s while the player was still reading the request."""
    from pokeuds.games.gen6 import trade as gen6

    monkeypatch.setattr(gen6, "ANSWER_WAIT", 12.0)        # past the old STAY_FOR (8 s), when hopping began
    key = os.urandom(16)
    ours = gen6_trade(air, key, tmp_path / "ours", "02000000A631", make_record(0x41), "POKEUDS", (1, 6, 11))
    console = gen6_trade(air, key, tmp_path / "console", "02000000A632", make_record(0x42), "Serena", (6,))
    console.station.on_beacon = console._on_beacon
    console._advertise()                                  # in the PSS on channel 6, never answering
    tuned, asked = [], []
    real_set = ours.station.radio.set_channel

    def set_channel(channel):
        if asked:
            tuned.append(channel)
        real_set(channel)

    real_advertise = ours._advertise

    def advertise(**args):
        if args.get("request"):
            asked.append(ours.station.radio.channel)
        real_advertise(**args)

    monkeypatch.setattr(ours.station.radio, "set_channel", set_channel)
    monkeypatch.setattr(ours, "_advertise", advertise)
    try:
        with pytest.raises(TimeoutError, match="answer from the console"):
            ours.run("host", 10.0)
        assert asked and set(asked) == {6} and set(tuned) <= {6}
    finally:
        ours.station.close()
        console.station.close()


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
