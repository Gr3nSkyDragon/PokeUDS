"""The local trade session for every game, in both roles, with a simulated console on the other end."""

import os

import pytest

from pokeuds import pkx
from pokeuds.games import GAMES, VERSION_NAMES, get, profile
from pokeuds.games.trade import LocalTrade, ProtocolNotMapped
from pokeuds.radio.sim import Air, SimRadio
from pokeuds.uds import Role, Station
from tests.test_pkx import make_record

# Test values only; the real ones come from captures.
COMM = {"xy": 0x00055D00, "oras": 0x0011C400, "sm": 0x00164800, "usum": 0x001B5000}
PASS = b"local-test\0"
CHANNEL = 1


def make_profile(game, also=()):
    return profile.Profile(game, wlan_comm_id=COMM[game], passphrase=PASS, max_nodes=2, channel=6,
                           application_data=b"\x01\x02\x03\x04", also_join_comm_ids=[COMM[g] for g in also],
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


def test_registry():
    assert list(GAMES) == ["xy", "oras", "sm", "usum"]
    assert {g.hub for g in GAMES.values() if g.generation == 6} == {"PSS"}
    assert {g.hub for g in GAMES.values() if g.generation == 7} == {"Festival Plaza"}
    assert get("sm").record is pkx.PK7 and get("xy").record is pkx.PK6
    for game in GAMES.values():  # Local trades never cross a generation.
        assert all(get(other).generation == game.generation for other in game.trades_with)
        assert all(game.key in get(other).trades_with for other in game.trades_with)
    assert VERSION_NAMES[30] == "Sun" and VERSION_NAMES[33] == "Ultra Moon" and VERSION_NAMES[39] == "Gold (VC)"


@pytest.mark.parametrize("game_key,species", [("xy", 658), ("usum", 785)])
def test_host_role_saves_what_the_console_sends(air, key, tmp_path, game_key, species):
    ours, console = stations(air, key)
    try:
        trade = LocalTrade(ours, get(game_key), make_profile(game_key), make_record(1), tmp_path)
        trade.host()
        target = console.scan(2.0, wlan_comm_id=COMM[game_key], channels=(6,), until=lambda b: True)[0]
        assert target.info.application_data == b"\x01\x02\x03\x04"   # From the profile.
        console.connect(target, PASS, timeout=5.0)
        assert trade.wait_for_partner(2.0) == 2
        theirs = make_record(0x2468ACE0, species=species, nickname="Partner")
        console.send(1, CHANNEL, b"\x10\x00" + pkx.encrypt(theirs))
        with pytest.raises(ProtocolNotMapped, match="as host"):
            trade.run(listen_seconds=1.0)
        assert len(trade.heard) == 1 and len(trade.saved) == 1
        name = trade.saved[0].name
        assert name.split("_")[1:4] == [game_key, "trade1", f"{species:03d}"]
        assert name.endswith("." + get(game_key).record.extension)
        assert trade.saved[0].read_bytes() == theirs[:pkx.STORED_SIZE]
    finally:
        console.close()
        ours.close()


@pytest.mark.parametrize("game_key", ["oras", "sm"])
def test_join_role(air, key, tmp_path, game_key):
    ours, console = stations(air, key)
    try:
        console.host(wlan_comm_id=COMM[game_key], passphrase=PASS, channel=6, max_nodes=2)
        trade = LocalTrade(ours, get(game_key), make_profile(game_key), make_record(2), tmp_path)
        trade.join(3.0)
        assert ours.role is Role.CLIENT and trade.partner.host == console.mac
        console.send(2, CHANNEL, pkx.encrypt(make_record(0x13579BDF, species=383)))
        with pytest.raises(ProtocolNotMapped, match="as client"):
            trade.run(listen_seconds=1.0)
        assert f"_{game_key}_trade1_383_" in trade.saved[0].name
    finally:
        ours.close()
        console.close()


@pytest.mark.parametrize("ours_key,theirs_key", [("oras", "xy"), ("sm", "usum")])
def test_join_partner_game_through_also_join(air, key, tmp_path, ours_key, theirs_key):
    ours, console = stations(air, key)
    try:
        console.host(wlan_comm_id=COMM[theirs_key], passphrase=PASS, channel=6, max_nodes=2)
        lonely = LocalTrade(ours, get(ours_key), make_profile(ours_key), make_record(3), tmp_path)
        with pytest.raises(TimeoutError, match=get(ours_key).hub):
            lonely.join(0.6)
        trade = LocalTrade(ours, get(ours_key), make_profile(ours_key, also=[theirs_key]), make_record(3), tmp_path)
        assert trade.join(2.0).info.wlan_comm_id == COMM[theirs_key]
    finally:
        ours.close()
        console.close()


def test_gen6_game_refuses_a_gen7_offer(air, key, tmp_path):
    ours, console = stations(air, key)
    try:
        rowlet = make_record(4, species=722)
        with pytest.raises(ValueError, match="Generation 6"):
            LocalTrade(ours, get("xy"), make_profile("xy"), rowlet, tmp_path)
        LocalTrade(ours, get("sm"), make_profile("sm"), rowlet, tmp_path)
    finally:
        console.close()
        ours.close()


def test_host_times_out_without_a_console(air, key, tmp_path):
    ours, console = stations(air, key)
    try:
        trade = LocalTrade(ours, get("usum"), make_profile("usum"), make_record(4), tmp_path)
        trade.host()
        with pytest.raises(TimeoutError):
            trade.wait_for_partner(0.3)
    finally:
        console.close()
        ours.close()


def test_profiles_ship_empty_and_load(tmp_path):
    for key in GAMES:
        shipped = profile.load(key, profile.PROFILE_DIR / f"{key}.toml")
        assert shipped.missing() == ["network.wlan_comm_id", "network.passphrase_hex", "trade.data_channels"]
    filled = tmp_path / "sm.toml"
    filled.write_text('[network]\nwlan_comm_id = "0x00164800"\npassphrase_hex = "41:42:00"\n'
                      'application_data_hex = "0a0b"\nalso_join_comm_ids = ["0x001B5000"]\n'
                      '[trade]\ndata_channels = [1, 2]\n')
    loaded = profile.load("sm", filled)
    assert loaded.missing() == []
    assert (loaded.passphrase, loaded.application_data, loaded.join_comm_ids()) == (b"AB\0", b"\x0a\x0b", [0x00164800, 0x001B5000])
