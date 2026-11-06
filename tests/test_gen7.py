"""Generation 7 (Sun/Moon): gflnet2 framing against real messages, the blocks, and a whole session between two
PokeUDS engines over the simulated air (one hosting the trade network, one joining it)."""

import os
import struct
import threading
import time

import pytest

from pokeuds import pkx
from pokeuds.games.gen6 import session as gen6_session
from pokeuds.games.gen6 import wire as gen6
from pokeuds.games.gen7 import session, templates, wire
from pokeuds.radio.sim import Air, SimRadio
from pokeuds.uds import Role, Station
from tests.test_pkx import make_record

COMM, PASS = 0x00164810, b"GNgBwMcVotYs\x00"

# A message a retail Sun/Moon sent (Azahar log of a Link Trade): its request for sync 0x88.
RETAIL_SYNC = bytes.fromhex("61630a00" "40000000" "0102ffff" "b4000000" "01020000" "0200000002000000" "8800")


def test_gflnet2_framing_matches_the_console():
    m = gen6.gfl_decode(RETAIL_SYNC)
    assert m.generation == 7 and m.index == 0x40 and m.counts == 0xB4
    body = gen6.parse_body(m.body)
    assert body.kind == "sync" and body.ident == wire.SYNC_START and not body.done
    assert gen6.gfl_encode(0x40, 0xB4, m.body, generation=7) == RETAIL_SYNC
    assert gen6.gfl_encode(0x40, 0xB4, m.body) != RETAIL_SYNC          # Gen 6's framing differs
    assert gen6.gfl_decode(gen6.gfl_encode(3, 0x0201, b"", 6)).generation == 6


def test_blocks_carry_the_trainer():
    t = wire.Trainer("Lillie", 1234, 5678, principal_id=0x2EC5A060, id8=bytes(range(8)), version=31)
    p = wire.profile(t)
    assert len(p) == templates.PROFILE_SIZE and wire.player_of(p) == ("Lillie", 0x2EC5A060)
    assert struct.unpack_from("<I", p, 0)[0] == (5678 << 16) | 1234 and p[24:32] == bytes(range(8))
    person = wire.person(t, now=wire.EPOCH_2000 + 1000)
    assert person[8:20] == "Lillie".encode("utf-16-le") and struct.unpack_from("<I", person, 4)[0] == 1000
    assert person[36:40] == struct.pack("<I", 0x2EC5A060) and person[40:48] == bytes(range(8))
    host, client = wire.netz_host(0x5A, b"\x01\x02\x03\x04"), wire.netz_client(0x5A)
    assert len(host) == 29 and len(client) == 13 and host[1][6] == host[2][6] == client[2][6] == 0x5A
    assert host[4][55:59] == b"\x01\x02\x03\x04" and b"Pokemon1kujira" in host[4]


@pytest.fixture(autouse=True)
def quick_syncs(monkeypatch):
    monkeypatch.setattr(gen6_session, "SYNC_ASK_AFTER", 0.05)
    monkeypatch.setattr(gen6_session, "SCREEN_AFTER", 0.05)


def pk7(ec, species, name):
    return pkx.fix_checksum(make_record(ec, species=species, nickname=name, version=31))


def test_whole_session_between_two_engines():
    air, key = Air(), os.urandom(16)
    host_st = Station(SimRadio(air), bytes.fromhex("02000000B701"), key, name="HOST")
    join_st = Station(SimRadio(air), bytes.fromhex("02000000B702"), key, name="JOIN")
    host_st.start(1)
    join_st.start(1)
    ours, theirs = [pk7(0x1111, 19, "Rattata"), pk7(0x3333, 755, "Morelull")], pk7(0x2222, 773, "Silvally")
    errors, results = [], {}

    def host():
        try:
            host_st.host(wlan_comm_id=COMM, passphrase=PASS, channel=1, max_nodes=4, id_=1)
            deadline = time.monotonic() + 10
            while not any(p.connected for p in host_st.peers.values()):
                assert time.monotonic() < deadline, "nobody joined"
                time.sleep(0.02)
            s = session.Gen7Session(host_st, wire.Trainer("Hosty"), ours[0], trades=2, queue=ours[1:])
            results["host"] = s.run()
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(f"host: {exc!r}")

    def join():
        try:
            found = join_st.scan(10, until=lambda b: b.info.wlan_comm_id == COMM)
            join_st.connect(next(b for b in found if b.info.wlan_comm_id == COMM), PASS)
            s = session.Gen7Session(join_st, wire.Trainer("Joiny"), theirs, trades=2, answer_first=True)
            results["join"] = s.run()
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(f"join: {exc!r}")

    threads = [threading.Thread(target=f, daemon=True) for f in (host, join)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(90)
        assert not errors and not any(t.is_alive() for t in threads), errors
        assert results["join"] == ours                       # both queued Pokemon, in order
        assert results["host"] == [theirs, theirs]
    finally:
        host_st.close()
        join_st.close()
        air.close()


@pytest.mark.parametrize("pokeuds_role", ["join", "host"])
def test_plaza_invite_and_trade_over_the_air(tmp_path, pokeuds_role, monkeypatch):
    """PokeUDS (hopping 1/6/11) against a second PokeUDS standing in for a console on channel 6."""
    from pokeuds.games import get, profile
    from pokeuds.games.gen7 import trade as gen7_trade
    from pokeuds.games.gen7.trade import Gen7Trade

    monkeypatch.setattr(gen7_trade, "FIRST_CHANNEL", 6)
    air, key = Air(), os.urandom(16)
    prof = profile.load("sm")

    def make(mac, record, name, channels, folder):
        st = Station(SimRadio(air), bytes.fromhex(mac), key, name=name)
        st.start(channels[0])
        return Gen7Trade(st, get("sm"), prof, record, tmp_path / folder, wire.Trainer(name), channels=channels)

    ours_offer, console_offer = pk7(0x1111, 19, "Rattata"), pk7(0x2222, 773, "Silvally")
    ours = make("02000000B711", ours_offer, "POKEUDS", (1, 6, 11), "ours")
    console = make("02000000B712", console_offer, "Lillie", (6,), "console")
    console.answer_first = True                       # its "player" says yes first, as a person at a console does
    console_role = "host" if pokeuds_role == "join" else "join"
    errors = []

    def run(trade, role):
        try:
            trade.run(role, 30.0)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(f"{role}: {exc!r}")

    threads = [threading.Thread(target=run, args=(ours, pokeuds_role), daemon=True),
               threading.Thread(target=run, args=(console, console_role), daemon=True)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(90)
        assert not errors and not any(t.is_alive() for t in threads), errors
        (mine,) = ours.saved
        (theirs,) = console.saved
        assert mine.read_bytes() == console_offer[:pkx.STORED_SIZE] and mine.suffix == ".pk7"
        assert theirs.read_bytes() == ours_offer[:pkx.STORED_SIZE]
    finally:
        ours.station.close()
        console.station.close()
        air.close()


def test_heartbeats_name_sun_moons_object():
    """Captured Sun/Moon heartbeats (host and client); PokeUDS's match them but for the counter and the clocks."""
    host = bytes.fromhex("26000000000a03003c0800000100c0000400400203091104000000000000670a00000000000000000000")
    client = bytes.fromhex("1a000000000a09003c0400000200c0000400400202091104000000000000")
    ours_h = gen6.heartbeat_host(0x03, host[22:26], 0, session.Gen7Session.HEARTBEAT_OBJECT)
    ours_c = gen6.heartbeat_client(0x09, struct.unpack_from("<I", client, 22)[0], session.Gen7Session.HEARTBEAT_OBJECT)
    assert ours_c == client
    same = [i for i in range(len(host)) if not (6 <= i < 8 or 22 <= i < 36)]
    assert [ours_h[i] for i in same] == [host[i] for i in same]


def test_one_message_may_hold_several_commands():
    """Sun/Moon packs the client's "yes" (its Pokemon, module 2) and module 3 cmd 05 into one message, and may pack
    the Pokemon under its cursor with its offer: every command in a body counts."""
    record = bytes(range(256)) + bytes(4)
    body = gen6.command(wire.CURSOR, wire.CMD_CURSOR, record) + gen6.command(wire.TRADE, wire.CMD_CLIENT_ACCEPT, b"\x05")
    parts = gen6.parse_bodies(body)
    assert [(b.module, b.ident) for b in parts] == [(2, 1), (3, 5)] and parts[0].data == record
    sync = gen6.sync(gen6.SYNC_REQUEST, 0, 0x88)
    assert [(b.kind, b.ident) for b in gen6.parse_bodies(sync + body)] == [("sync", 0x88), ("command", 1), ("command", 5)]
