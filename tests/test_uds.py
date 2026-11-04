import os
import struct

import pytest

from pokeuds.keys import KeyFile, scramble
from pokeuds.uds import beacon as bcn
from pokeuds.uds import crypto, data, dot11
from pokeuds.util import BROADCAST

HOST = bytes.fromhex("40D28A000001")
CLIENT = bytes.fromhex("40D28A000002")


def network(max_nodes=2, appdata=b"\x10\x20\x30"):
    return bcn.NetworkInfo(host_mac=HOST, channel=11, wlan_comm_id=0x00055D00, id=0, update_counter=1,
                           network_id=0x12345678, total_nodes=1, max_nodes=max_nodes, application_data=appdata)


def test_network_info_struct():
    raw = network().to_struct()
    assert len(raw) == 0x108
    assert raw[0xC:0x10] == b"\x00\x1f\x32\x15"
    assert struct.unpack_from(">I", raw, 0x10)[0] == 0x00055D00
    assert struct.unpack_from(">I", raw, 0x18)[0] == 0x12345678
    assert raw[0x3F] == 3 and raw[0x40:0x43] == b"\x10\x20\x30"


@pytest.mark.parametrize("max_nodes", [2, 8, 16])
def test_beacon_round_trip(max_nodes):
    info = network(max_nodes)
    host = bcn.NodeInfo.named("Calem", 0x1122334455667788, 1)
    nodes = [host] + [bcn.NodeInfo() for _ in range(max_nodes - 1)]
    frame = dot11.parse(bcn.beacon_frame(info, nodes))
    heard = bcn.parse_beacon(frame, channel=1)
    assert heard.sha1_valid
    assert heard.channel == 11  # From the DS element, not the capture channel.
    assert heard.info.network_id == info.network_id and heard.info.wlan_comm_id == info.wlan_comm_id
    assert heard.info.application_data == info.application_data
    assert heard.nodes[0].name == "Calem" and heard.nodes[0].friend_code_seed == 0x1122334455667788
    assert all(n.empty() for n in heard.nodes[1:])
    # Past 0xFA bytes (8 or more nodes) the node list continues in a second encrypted tag.
    tags = [v[3] for t, v in dot11.iter_ies(frame.body, 12) if t == 221]
    assert tags == ([20, 21, 24, 25] if 18 + 0x1E * max_nodes > 0xFA else [20, 21, 24])


def test_tampered_beacon_fails_checks():
    info = network()
    mpdu = bytearray(bcn.beacon_frame(info, [bcn.NodeInfo.named("A", 1, 1), bcn.NodeInfo()]))
    mpdu[-3] ^= 1
    heard = bcn.parse_beacon(dot11.parse(bytes(mpdu)), 11)
    assert heard.nodes is None


def test_ccmp_key_inputs_matter():
    key = os.urandom(16)
    base = network().ccmp_key(key, b"pass\0")
    assert len(base) == 16
    other = network()
    other.network_id ^= 1
    assert other.ccmp_key(key, b"pass\0") != base
    assert network().ccmp_key(key, b"pass") != base


@pytest.mark.parametrize("dest,from_ds,ds_bits", [
    (CLIENT, True, dot11.FC_FROM_DS),   # Host to a client.
    (HOST, False, dot11.FC_TO_DS),      # Client to the host.
    (BROADCAST, True, 0),               # Group addressed: NoDS.
])
def test_ccmp_round_trip(dest, from_ds, ds_bits):
    key = os.urandom(16)
    sender = HOST if from_ds else CLIENT
    payload = data.pack_securedata(data.SecureData(1, 7, 2, 1, b"hello trade"))
    mpdu = crypto.build_data_frame(key, payload, transmitter=sender, destination=dest, bssid=HOST,
                                   from_ds=from_ds, pn=0x0102030405, seq=99)
    frame = dot11.parse(mpdu)
    assert frame.fc & 0x0300 == ds_bits and frame.protected
    assert (frame.transmitter, frame.destination, frame.bssid) == (sender, dest, HOST)
    assert crypto.ccmp_pn(frame.body) == 0x0102030405
    assert crypto.open_data_frame(key, frame) == payload
    # A retransmission (retry bit) still authenticates: the bit is masked out of the AAD.
    retry = bytearray(mpdu)
    retry[1] |= 0x08
    assert crypto.open_data_frame(key, dot11.parse(bytes(retry))) == payload
    tampered = bytearray(mpdu)
    tampered[-10] ^= 1
    assert crypto.open_data_frame(key, dot11.parse(bytes(tampered))) is None


def test_securedata_aggregate():
    one = data.pack_securedata(data.SecureData(1, 1, 2, 1, b"abc"))
    two = data.pack_securedata(data.SecureData(4, 2, 0xFFFF, 1, b"defgh", management=True))
    # A retail host may bundle packets: one container, count 2, each packet with its own size.
    bundle = (data.llc(data.ETHERTYPE_SECUREDATA) + struct.pack(">HH", 4 + len(one) - 12 + len(two) - 12, 2)
              + one[12:] + two[12:])
    packets = data.unpack_securedata(bundle)
    assert [(p.channel, p.payload, p.management) for p in packets] == [(1, b"abc", False), (4, b"defgh", True)]
    assert struct.unpack_from(">HHH", one, 8) == (3 + 14, 1, 3 + 10)


def test_eapol_layouts():
    node = bcn.NodeInfo.named("Serena", 0xABCDEF, 0)
    start = data.eapol_start(1, node)
    assert len(start) == 8 + 0x30
    assert start[8 + 6:8 + 8] == b"\x01\x00" and start[8 + 42:8 + 48] == data.EAPOL_NODE_TRAILER
    parsed = data.parse_eapol_start(start)
    assert (parsed.association_id, parsed.connection_type, parsed.node.name) == (1, 1, "Serena")
    nodes = [bcn.NodeInfo.named("Host", 1, 1), bcn.NodeInfo.named("Serena", 2, 2)]
    logoff = data.eapol_logoff(CLIENT, 2, nodes, 2, 2)
    assert len(logoff) == 8 + 0x298
    parsed = data.parse_eapol_logoff(logoff)
    assert (parsed.assigned_node_id, parsed.client_mac, parsed.connected_nodes, parsed.max_nodes) == (2, CLIENT, 2, 2)
    assert [n.name for n in parsed.nodes[:2]] == ["Host", "Serena"]


def test_management_bodies():
    assoc = dot11.assoc_request_body(0x0012ABCD)
    assert assoc[:4] == b"\x31\x04\x01\x00" and b"0012ABCD" in assoc
    status, aid = dot11.parse_assoc_response(dot11.assoc_response_body(1, 0x0012ABCD))
    assert (status, aid) == (0, 1)


def test_key_file():
    x, y, c = os.urandom(16), os.urandom(16), os.urandom(16)
    text = f"slot0x2DKeyX={x.hex().upper()}\nslot0x2DKeyY={y.hex()}\ngeneratorConstant={c.hex()}\n# comment\n"
    assert KeyFile.parse(text).uds_key() == scramble(x, y, c)
    assert KeyFile.parse(f"slot0x2DKeyN={x.hex()}").uds_key() == x
    with pytest.raises(KeyError):
        KeyFile.parse("slot0x2CKeyN=00000000000000000000000000000000").uds_key()
    assert scramble(bytes(16), bytes(16), bytes(16)) == bytes(16)
