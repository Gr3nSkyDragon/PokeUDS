"""Two stations on a simulated air: the full join handshake, data both ways, keepalive, leaving."""

import os
import time

import pytest

from pokeuds.radio.sim import Air, SimRadio
from pokeuds.uds import Role, Station, data, dot11
from pokeuds.uds.sniffer import Sniffer

COMM_ID = 0x00055D00
PASS = b"test-passphrase\0"


@pytest.fixture
def air():
    a = Air()
    yield a
    a.close()


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_join_exchange_and_leave(air):
    key = os.urandom(16)
    host = Station(SimRadio(air), bytes.fromhex("020000000001"), key, name="Host")
    client = Station(SimRadio(air), bytes.fromhex("020000000002"), key, name="Client")
    host.start(11)
    client.start(1)
    try:
        host.host(wlan_comm_id=COMM_ID, passphrase=PASS, channel=11, max_nodes=2, application_data=b"\xaa\xbb")
        found = client.scan(3.0, wlan_comm_id=COMM_ID, until=lambda b: True)
        assert len(found) == 1 and found[0].info.application_data == b"\xaa\xbb"
        assert found[0].nodes[0].name == "Host"

        client.connect(found[0], PASS, timeout=5.0)
        assert client.role is Role.CLIENT and client.node_id == 2
        assert [n.name for n in client.nodes] == ["Host", "Client"]
        assert wait_for(lambda: host.network.total_nodes == 2)
        assert host.nodes[1].name == "Client"

        # The host's beacon now lists the client (a retail console checks this right after joining).
        rescanned = client.scan(0.5, wlan_comm_id=COMM_ID)
        assert rescanned[0].info.total_nodes == 2

        client.send(data.HOST_NODE, 1, b"offer from client")
        got = host.recv(1, timeout=2.0)
        assert (got.src_node, got.payload) == (2, b"offer from client")
        host.send(2, 1, b"offer from host")
        got = client.recv(1, timeout=2.0)
        assert (got.src_node, got.payload) == (1, b"offer from host")

        # Keepalives are management packets, answered by the host and never delivered to the game.
        time.sleep(1.3)
        assert client.recv(3, timeout=0.1) is None

        client.close()
        assert wait_for(lambda: host.network.total_nodes == 1)
        assert host.nodes[1].empty()
    finally:
        host.close()
        air.close()


def test_wrong_passphrase_cannot_join(air):
    key = os.urandom(16)
    host = Station(SimRadio(air), bytes.fromhex("020000000011"), key)
    client = Station(SimRadio(air), bytes.fromhex("020000000012"), key)
    host.start(6)
    client.start(6)
    try:
        host.host(wlan_comm_id=COMM_ID, passphrase=PASS, channel=6, max_nodes=2)
        target = client.scan(1.0, wlan_comm_id=COMM_ID, channels=(6,), until=lambda b: True)[0]
        with pytest.raises(Exception, match="eapol"):
            client.connect(target, b"wrong\0", timeout=1.5)
    finally:
        client.close()
        host.close()


def test_sniffer_reads_a_session(air):
    key = os.urandom(16)
    host = Station(SimRadio(air), bytes.fromhex("020000000021"), key, name="H")
    client = Station(SimRadio(air), bytes.fromhex("020000000022"), key, name="C")
    spy = SimRadio(air)
    spy.start(11, bytes.fromhex("020000000023"))
    sniffer = Sniffer(spy, key, PASS, wlan_comm_id=COMM_ID)
    host.start(11)
    client.start(11)
    try:
        host.host(wlan_comm_id=COMM_ID, passphrase=PASS, channel=11, max_nodes=2)
        client.connect(client.scan(1.0, channels=(11,), until=lambda b: True)[0], PASS, timeout=5.0)
        client.send(1, 1, b"secret pk6 bytes")
        packets = []
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(p.packet and p.packet.payload == b"secret pk6 bytes" for p in packets):
            rx = spy.recv(0.05)
            if rx:
                packets += sniffer.feed(rx.mpdu, rx.channel)
        kinds = [p.kind for p in packets]
        assert "eapol-start" in kinds and "eapol-logoff" in kinds
        game = [p for p in packets if p.kind == "securedata" and not p.packet.management]
        assert game[-1].role == "client" and game[-1].packet.payload == b"secret pk6 bytes"
    finally:
        client.close()
        host.close()
        spy.close()


def test_retransmissions_are_delivered_once(air):
    key = os.urandom(16)
    host = Station(SimRadio(air), bytes.fromhex("020000000031"), key)
    client = Station(SimRadio(air), bytes.fromhex("020000000032"), key)
    host.start(11)
    client.start(11)
    try:
        host.host(wlan_comm_id=COMM_ID, passphrase=PASS, channel=11, max_nodes=2)
        client.connect(client.scan(1.0, channels=(11,), until=lambda b: True)[0], PASS, timeout=5.0)
        sent = []
        original = client.radio.send
        client.radio.send = lambda mpdu: (sent.append(mpdu), original(mpdu))
        client.send(1, 2, b"once")
        assert host.recv(2, timeout=2.0).payload == b"once"
        frame = next(m for m in sent if dot11.parse(m).type == dot11.TYPE_DATA)
        retry = bytearray(frame)
        retry[1] |= 0x08
        original(bytes(retry))
        assert host.recv(2, timeout=0.5) is None
    finally:
        client.close()
        host.close()
