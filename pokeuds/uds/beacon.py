"""UDS network descriptions: NetworkInfo, NodeInfo and the beacon that advertises them.

A UDS beacon carries, after the usual 802.11 elements, three Nintendo vendor elements (OUI
00:1F:32): a dummy tag (type 20), the network info tag (type 21) with the game's application
data and a SHA-1 over both, and the node list (types 24/25), AES-CTR encrypted with the public
NWM beacon key and protected by an MD5. Layouts follow azahar_uds ``uds_beacon.{h,cpp}``.
"""

from __future__ import annotations

import hashlib
import struct
import time
from dataclasses import dataclass, field

from ..util import BROADCAST
from . import crypto, dot11

NINTENDO_OUI = b"\x00\x1f\x32"
TAG_DUMMY, TAG_NETWORK_INFO, TAG_ENCRYPTED0, TAG_ENCRYPTED1 = 20, 21, 24, 25
ENCRYPTED_TAG_CAPACITY = 0xFA
APPLICATION_DATA_SIZE = 0xC8
MAX_NODES = 16
BEACON_INTERVAL_TU = 100
NETWORK_INFO_SIZE = 0x108


def _name_units(name: str) -> list[int]:
    units = list(name.encode("utf-16-le"))
    words = [units[i] | units[i + 1] << 8 for i in range(0, len(units), 2)][:10]
    return words + [0] * (10 - len(words))


def _units_name(units: list[int]) -> str:
    raw = b"".join(struct.pack("<H", u) for u in units)
    return raw.decode("utf-16-le", "replace").split("\0", 1)[0]


@dataclass
class NodeInfo:
    """One console in a network: its friend code seed, Mii name and node id (1 is the host)."""

    friend_code_seed: int = 0
    username: list[int] = field(default_factory=lambda: [0] * 10)
    network_node_id: int = 0

    @classmethod
    def named(cls, name: str, friend_code_seed: int, node_id: int = 0) -> "NodeInfo":
        return cls(friend_code_seed, _name_units(name), node_id)

    @property
    def name(self) -> str:
        return _units_name(self.username)

    # Beacon form (big endian, 0x1E bytes).
    def pack_beacon(self) -> bytes:
        return struct.pack(">Q10HH", self.friend_code_seed, *self.username, self.network_node_id)

    @classmethod
    def unpack_beacon(cls, data: bytes) -> "NodeInfo":
        values = struct.unpack_from(">Q10HH", data)
        return cls(values[0], list(values[1:11]), values[11])

    # EAPoL form (big endian, 0x28 bytes).
    def pack_eapol(self, trailer: bytes = bytes(6)) -> bytes:
        return struct.pack(">Q10H4xH", self.friend_code_seed, *self.username, self.network_node_id) + trailer

    @classmethod
    def unpack_eapol(cls, data: bytes) -> "NodeInfo":
        values = struct.unpack_from(">Q10H4xH", data)
        return cls(values[0], list(values[1:11]), values[11])

    def empty(self) -> bool:
        return self.network_node_id == 0


@dataclass
class NetworkInfo:
    """The network a host advertises; the fields a client needs to join and to derive keys."""

    host_mac: bytes = bytes(6)
    channel: int = 0
    wlan_comm_id: int = 0
    id: int = 0
    update_counter: int = 1
    attributes: int = 0
    network_id: int = 0
    total_nodes: int = 1
    max_nodes: int = 2
    application_data: bytes = b""
    # The 11 bytes of the network info tag after max_nodes and its padding (zero in hosts seen).
    tag_trailer: bytes = bytes(11)

    def tag_body(self) -> bytes:
        """The 0x1F network-info bytes of the beacon tag, starting at the OUI."""
        return (NINTENDO_OUI + bytes([TAG_NETWORK_INFO]) + struct.pack(">IBBHIBB2x", self.wlan_comm_id, self.id,
                self.update_counter, self.attributes, self.network_id, self.total_nodes, self.max_nodes)
                + self.tag_trailer[:11].ljust(11, b"\0"))

    def to_struct(self) -> bytes:
        """The 0x108-byte NetworkInfo the 3DS UDS service exchanges with games."""
        body = self.host_mac + bytes([self.channel, 0, 1, 0, 0, 0]) + self.tag_body()
        body = body.ljust(0x3F, b"\0") + bytes([len(self.application_data)])
        return (body + self.application_data).ljust(NETWORK_INFO_SIZE, b"\0")

    def crypt_args(self) -> tuple[bytes, int, int, int]:
        return self.host_mac, self.wlan_comm_id, self.id, self.network_id

    def ccmp_key(self, uds_key: bytes, passphrase: bytes) -> bytes:
        return crypto.ccmp_key(uds_key, passphrase, *self.crypt_args())


def _network_info_tag(info: NetworkInfo) -> bytes:
    if len(info.application_data) > APPLICATION_DATA_SIZE:
        raise ValueError("application data is larger than 0xC8 bytes")
    body = info.tag_body() + bytes(20) + bytes([len(info.application_data)]) + info.application_data
    digest = hashlib.sha1(body).digest()
    return dot11.ie(dot11.IE_VENDOR, body[:0x1F] + digest + body[0x1F + 20:])


def encrypted_node_data(info: NetworkInfo, nodes: list[NodeInfo]) -> bytes:
    """MD5 + bitmask + every node slot, AES-CTR encrypted."""
    plain = struct.pack(">H", 0) + b"".join(node.pack_beacon() for node in nodes)
    return crypto.beacon_crypt(hashlib.md5(plain).digest() + plain, *info.crypt_args())


def beacon_body(info: NetworkInfo, nodes: list[NodeInfo], timestamp_us: int | None = None) -> bytes:
    """The beacon body from the fixed parameters on; ``nodes`` holds all max_nodes slots."""
    if len(nodes) != info.max_nodes:
        raise ValueError("a beacon lists exactly max_nodes node slots")
    stamp = int(time.monotonic() * 1_000_000) if timestamp_us is None else timestamp_us
    body = struct.pack("<QHH", stamp, BEACON_INTERVAL_TU, dot11.CAPABILITIES)
    # SSID, rates, DS, TIM, country, ERP: order and values of a retail 3DS XL host. A retail host
    # broadcasts an eight-byte zero SSID; the network id appears only in association frames.
    body += dot11.ie(dot11.IE_SSID, bytes(8))
    body += dot11.ie(dot11.IE_RATES, bytes([0x96, 0x98, 0xA4, 0xB0, 0xC8, 0xE0, 0xEC]))
    body += dot11.ie(dot11.IE_DS, bytes([info.channel]))
    body += dot11.ie(dot11.IE_TIM, bytes([0, 1, 1, 0]))
    body += dot11.ie(dot11.IE_COUNTRY, b"JP\x00\x01\x0d\x14")
    body += dot11.ie(dot11.IE_ERP, b"\x00")
    body += dot11.ie(dot11.IE_VENDOR, NINTENDO_OUI + bytes([TAG_DUMMY]) + b"\x0a\x00\x00")
    body += _network_info_tag(info)
    data = encrypted_node_data(info, nodes)
    body += dot11.ie(dot11.IE_VENDOR, NINTENDO_OUI + bytes([TAG_ENCRYPTED0]) + data[:ENCRYPTED_TAG_CAPACITY])
    if len(data) > ENCRYPTED_TAG_CAPACITY:
        body += dot11.ie(dot11.IE_VENDOR, NINTENDO_OUI + bytes([TAG_ENCRYPTED1]) + data[ENCRYPTED_TAG_CAPACITY:])
    return body


def beacon_frame(info: NetworkInfo, nodes: list[NodeInfo], seq: int = 0) -> bytes:
    return dot11.header(dot11.FC_BEACON, BROADCAST, info.host_mac, info.host_mac, seq) + beacon_body(info, nodes)


@dataclass
class Beacon:
    """A UDS beacon heard on the air."""

    info: NetworkInfo
    nodes: list[NodeInfo] | None   # None when the node list failed its MD5.
    sha1_valid: bool
    channel: int
    rssi: int | None = None

    @property
    def host(self) -> bytes:
        return self.info.host_mac


def parse_beacon(frame: dot11.Frame, channel: int, rssi: int | None = None) -> Beacon | None:
    if frame.type != dot11.TYPE_MGMT or frame.subtype not in (dot11.SUBTYPE_BEACON, dot11.SUBTYPE_PROBE_RESP):
        return None
    body = frame.body
    if len(body) < 12:
        return None
    info = NetworkInfo(host_mac=frame.transmitter, channel=channel)
    found = False
    sha_ok = False
    encrypted = [b"", b""]
    for tag, value in dot11.iter_ies(body, 12):
        if tag == dot11.IE_DS and value:
            info.channel = value[0]
        if tag != dot11.IE_VENDOR or len(value) < 4 or value[:3] != NINTENDO_OUI:
            continue
        kind = value[3]
        if kind == TAG_NETWORK_INFO and len(value) >= 0x1F + 20 + 1:
            (info.wlan_comm_id, info.id, info.update_counter, info.attributes, info.network_id,
             info.total_nodes, info.max_nodes) = struct.unpack_from(">IBBHIBB", value, 4)
            info.tag_trailer = value[20:0x1F]
            size = value[0x1F + 20]
            info.application_data = value[0x1F + 21:0x1F + 21 + size]
            hashed = value[:0x1F] + bytes(20) + value[0x1F + 20:]
            sha_ok = hashlib.sha1(hashed).digest() == value[0x1F:0x1F + 20]
            found = True
        elif kind == TAG_ENCRYPTED0:
            encrypted[0] = value[4:]
        elif kind == TAG_ENCRYPTED1:
            encrypted[1] = value[4:]
    if not found:
        return None
    nodes = None
    size = 18 + 0x1E * info.max_nodes
    blob = (encrypted[0] + encrypted[1])[:size]
    if len(blob) == size:
        plain = crypto.beacon_crypt(blob, *info.crypt_args())
        if hashlib.md5(plain[16:]).digest() == plain[:16]:
            nodes = [NodeInfo.unpack_beacon(plain[18 + 0x1E * i:]) for i in range(info.max_nodes)]
    return Beacon(info, nodes, sha_ok, info.channel, rssi)
