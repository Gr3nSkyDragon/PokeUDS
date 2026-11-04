"""The Generation 6 trade session in both roles, with a simulated console on the other end."""

import os

import pytest

from pokeuds import pk6
from pokeuds.games import gen6
from pokeuds.games.gen6 import profile
from pokeuds.games.gen6.trade import Gen6Trade, ProtocolNotMapped
from pokeuds.radio.sim import Air, SimRadio
from pokeuds.uds import Role, Station
from tests.test_pk6 import make_record

XY_COMM = 0x00055D00     # Test values only; the real ones come from captures.
ORAS_COMM = 0x0011C400
PASS = b"gen6-test\0"
CHANNEL = 1


def make_profile(game, comm, also=()):
    return profile.Profile(game, wlan_comm_id=comm, passphrase=PASS, max_nodes=2, channel=6,
                           application_data=b"\x01\x02\x03\x04", also_join_comm_ids=list(also),
                           data_channels=[CHANNEL])


@pytest.fixture
def air():
    a = Air()
    yield a
    a.close()


@pytest.fixture
def key():
    return os.urandom(16)


def stations(air, key):
    ours = Station(SimRadio(air), bytes.fromhex("020000000101"), key, name="PokeUDS")
    console = Station(SimRadio(air), bytes.fromhex("020000000102"), key, name="Console")
    ours.start(6)
    console.start(6)
    return ours, console


def test_host_role_saves_what_the_console_sends(air, key, tmp_path):
    ours, console = stations(air, key)
    try:
        trade = Gen6Trade(ours, gen6.get("xy"), make_profile("xy", XY_COMM), make_record(1), tmp_path)
        trade.host()
        target = console.scan(2.0, wlan_comm_id=XY_COMM, channels=(6,), until=lambda b: True)[0]
        assert target.info.application_data == b"\x01\x02\x03\x04"   # From the profile.
        console.connect(target, PASS, timeout=5.0)
        assert trade.wait_for_partner(2.0) == 2
        theirs = make_record(0x2468ACE0, species=658, nickname="Froakie")
        console.send(1, CHANNEL, b"\x10\x00" + pk6.encrypt(theirs))
        with pytest.raises(ProtocolNotMapped, match="as host"):
            trade.run(listen_seconds=1.0)
        assert len(trade.heard) == 1 and len(trade.saved) == 1
        assert trade.saved[0].name.split("_")[1:4] == ["xy", "trade1", "658"]
        assert trade.saved[0].read_bytes() == theirs[:pk6.STORED_SIZE]
    finally:
        console.close()
        ours.close()


def test_join_role(air, key, tmp_path):
    ours, console = stations(air, key)
    try:
        console.host(wlan_comm_id=ORAS_COMM, passphrase=PASS, channel=6, max_nodes=2)
        trade = Gen6Trade(ours, gen6.get("oras"), make_profile("oras", ORAS_COMM), make_record(2), tmp_path)
        trade.join(3.0)
        assert ours.role is Role.CLIENT and trade.partner.host == console.mac
        console.send(2, CHANNEL, pk6.encrypt(make_record(0x13579BDF, species=383)))
        with pytest.raises(ProtocolNotMapped, match="as client"):
            trade.run(listen_seconds=1.0)
        assert "_oras_trade1_383_" in trade.saved[0].name
    finally:
        ours.close()
        console.close()


def test_oras_joins_xy_host_through_also_join(air, key, tmp_path):
    ours, console = stations(air, key)
    try:
        console.host(wlan_comm_id=XY_COMM, passphrase=PASS, channel=6, max_nodes=2)
        lonely = Gen6Trade(ours, gen6.get("oras"), make_profile("oras", ORAS_COMM), make_record(3), tmp_path)
        with pytest.raises(TimeoutError):
            lonely.join(0.6)
        trade = Gen6Trade(ours, gen6.get("oras"), make_profile("oras", ORAS_COMM, also=[XY_COMM]), make_record(3), tmp_path)
        assert trade.join(2.0).info.wlan_comm_id == XY_COMM
    finally:
        ours.close()
        console.close()


def test_host_times_out_without_a_console(air, key, tmp_path):
    ours, console = stations(air, key)
    try:
        trade = Gen6Trade(ours, gen6.get("xy"), make_profile("xy", XY_COMM), make_record(4), tmp_path)
        trade.host()
        with pytest.raises(TimeoutError):
            trade.wait_for_partner(0.3)
    finally:
        console.close()
        ours.close()


def test_profiles_ship_empty_and_load(tmp_path):
    for key in gen6.GAMES:
        shipped = profile.load(key, profile.PROFILE_DIR / f"{key}.toml")
        assert shipped.missing() == ["network.wlan_comm_id", "network.passphrase_hex", "trade.data_channels"]
    filled = tmp_path / "oras.toml"
    filled.write_text('[network]\nwlan_comm_id = "0x0011C400"\npassphrase_hex = "41:42:00"\n'
                      'application_data_hex = "0a0b"\nalso_join_comm_ids = ["0x00055D00"]\n'
                      '[trade]\ndata_channels = [1, 2]\n')
    loaded = profile.load("oras", filled)
    assert loaded.missing() == []
    assert (loaded.passphrase, loaded.application_data, loaded.join_comm_ids()) == (b"AB\0", b"\x0a\x0b", [0x0011C400, 0x00055D00])


def test_version_names():
    record = bytearray(make_record(5))
    record[0xDF] = 26
    assert gen6.VERSION_NAMES[pk6.summary(pk6.fix_checksum(bytes(record))).version] == "Alpha Sapphire"
