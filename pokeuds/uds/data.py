"""Payloads of UDS data frames: LLC/SNAP, Nintendo SecureData, and the EAPoL join messages.

Every UDS data frame is CCMP protected and starts with an 802.2 LLC/SNAP header naming either
SecureData (0x876D, game and management traffic) or EAPoL (0x888E, the join handshake). Layouts
follow azahar_uds ``uds_data.{h,cpp}``; the non-zero "padding" bytes come from retail captures.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .beacon import MAX_NODES, NodeInfo

ETHERTYPE_SECUREDATA = 0x876D
ETHERTYPE_EAPOL = 0x888E
LLC_SIZE = 8
SECUREDATA_HEADER_SIZE = 14

EAPOL_START = 0x0201
EAPOL_LOGOFF = 0x0202
CONNECTION_CLIENT = 1
CONNECTION_SPECTATOR = 2

BROADCAST_NODE = 0xFFFF
HOST_NODE = 1
MANAGEMENT_CHANNEL = 3
MAX_SEND_SIZE = 0x5C6

# Fixed bytes a retail EAPoL-Start carries: 01 00 after the connection type, and this trailer after
# the node id (seen from several consoles and two games, so likely an NWM constant).
EAPOL_START_FLAGS = b"\x01\x00"
EAPOL_NODE_TRAILER = bytes.fromhex("150004E91300")


def llc(ethertype: int) -> bytes:
    return b"\xaa\xaa\x03\x00\x00\x00" + struct.pack(">H", ethertype)


def ethertype(payload: bytes) -> int | None:
    if len(payload) < LLC_SIZE or payload[:3] != b"\xaa\xaa\x03":
        return None
    return struct.unpack_from(">H", payload, 6)[0]


@dataclass
class SecureData:
    """One SecureData packet. A frame's container may aggregate several."""

    channel: int
    sequence: int
    dest_node: int
    src_node: int
    payload: bytes
    management: bool = False


def pack_securedata(packet: SecureData) -> bytes:
    size = len(packet.payload) + SECUREDATA_HEADER_SIZE - 4
    # Container header: total size after the LLC header, and the packet count (a retail console
    # ignores a frame whose count is 0).
    return (llc(ETHERTYPE_SECUREDATA) + struct.pack(">HHHBBHHH", size + 4, 1, size, int(packet.management),
            packet.channel, packet.sequence & 0xFFFF, packet.dest_node, packet.src_node) + packet.payload)


def unpack_securedata(payload: bytes) -> list[SecureData]:
    """Every packet in a SecureData frame (retail hosts aggregate several into one frame)."""
    if len(payload) < LLC_SIZE + 4:
        return []
    container_size, count = struct.unpack_from(">HH", payload, LLC_SIZE)
    end = min(len(payload), LLC_SIZE + container_size)
    offset = LLC_SIZE + 4
    packets = []
    for _ in range(max(count, 0)):
        if offset + 2 > end:
            break
        (size,) = struct.unpack_from(">H", payload, offset)
        if size < 10 or offset + size > end:
            break
        management, channel, sequence, dest, src = struct.unpack_from(">BBHHH", payload, offset + 2)
        packets.append(SecureData(channel, sequence, dest, src, payload[offset + 10:offset + size], bool(management)))
        offset += size
    return packets


# -- EAPoL ----------------------------------------------------------------------------------------

def eapol_start(association_id: int, node: NodeInfo, connection_type: int = CONNECTION_CLIENT) -> bytes:
    return (llc(ETHERTYPE_EAPOL) + struct.pack(">HHH", EAPOL_START, association_id, connection_type)
            + EAPOL_START_FLAGS + node.pack_eapol(EAPOL_NODE_TRAILER))


@dataclass
class EapolStart:
    association_id: int
    connection_type: int
    node: NodeInfo


@dataclass
class EapolLogoff:
    assigned_node_id: int
    client_mac: bytes
    connected_nodes: int
    max_nodes: int
    nodes: list[NodeInfo]


def eapol_kind(payload: bytes) -> int | None:
    if ethertype(payload) != ETHERTYPE_EAPOL or len(payload) < LLC_SIZE + 2:
        return None
    return struct.unpack_from(">H", payload, LLC_SIZE)[0]


def parse_eapol_start(payload: bytes) -> EapolStart | None:
    body = payload[LLC_SIZE:]
    if len(body) < 0x30:
        return None
    _magic, aid, conn_type = struct.unpack_from(">HHH", body)
    return EapolStart(aid, conn_type, NodeInfo.unpack_eapol(body[8:]))


def eapol_logoff(client_mac: bytes, node_id: int, nodes: list[NodeInfo], max_nodes: int, total_nodes: int) -> bytes:
    body = struct.pack(">H2xH", EAPOL_LOGOFF, node_id) + client_mac + bytes(6) + bytes([total_nodes, max_nodes]) + bytes(4)
    slots = [nodes[i].pack_eapol() if i < len(nodes) else bytes(0x28) for i in range(MAX_NODES)]
    return llc(ETHERTYPE_EAPOL) + body + b"".join(slots)


def parse_eapol_logoff(payload: bytes) -> EapolLogoff | None:
    body = payload[LLC_SIZE:]
    if len(body) < 24:
        return None
    _magic, node_id = struct.unpack_from(">H2xH", body)
    client_mac = body[6:12]
    connected, max_nodes = body[18], body[19]
    nodes = []
    for i in range(MAX_NODES):
        raw = body[24 + 0x28 * i:24 + 0x28 * (i + 1)]
        if len(raw) < 0x28:
            break
        nodes.append(NodeInfo.unpack_eapol(raw))
    return EapolLogoff(node_id, client_mac, connected, max_nodes, nodes)
