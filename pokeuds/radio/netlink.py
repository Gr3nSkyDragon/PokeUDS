"""Netlink, generic netlink and nl80211 message encoding.

ldnd.exe runs the Linux Wi-Fi driver for a USB adapter and forwards Linux socket calls over a
named pipe, so a monitor interface is set up exactly as on Linux: nl80211 over generic netlink for
the wireless side, rtnetlink for the link state and MAC address. Constants and request shapes
follow azahar_uds ``uds_real/nl80211_monitor.cpp``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

AF_NETLINK = 16
AF_PACKET = 17
SOCK_DGRAM = 2
SOCK_RAW = 3
NETLINK_ROUTE = 0
NETLINK_GENERIC = 16
ETH_P_ALL = 0x0003

GENL_ID_CTRL = 0x10
CTRL_CMD_GETFAMILY = 3
CTRL_ATTR_FAMILY_ID = 1
CTRL_ATTR_FAMILY_NAME = 2

NL80211_CMD_GET_INTERFACE = 5
NL80211_CMD_NEW_INTERFACE = 7
NL80211_CMD_DEL_INTERFACE = 8
NL80211_CMD_SET_CHANNEL = 65

NL80211_ATTR_WIPHY = 1
NL80211_ATTR_IFINDEX = 3
NL80211_ATTR_IFNAME = 4
NL80211_ATTR_IFTYPE = 5
NL80211_ATTR_MAC = 6
NL80211_ATTR_MNTR_FLAGS = 23
NL80211_ATTR_WIPHY_FREQ = 38

NL80211_IFTYPE_MONITOR = 6
NL80211_MNTR_FLAG_OTHER_BSS = 4
NL80211_MNTR_FLAG_ACTIVE = 6

NLM_F_REQUEST = 0x0001
NLM_F_MULTI = 0x0002
NLM_F_ACK = 0x0004
NLM_F_DUMP = 0x0100 | 0x0200
NLMSG_ERROR = 2
NLMSG_DONE = 3
NLA_F_NESTED = 1 << 15
NLA_TYPE_MASK = 0x3FFF

RTM_NEWLINK = 16
IFLA_ADDRESS = 1
IFF_UP = 1

NLMSG_HDRLEN = 16
GENL_HDRLEN = 4


def align4(n: int) -> int:
    return (n + 3) & ~3


def attr(type_: int, value: bytes = b"") -> bytes:
    raw = struct.pack("<HH", 4 + len(value), type_) + value
    return raw + b"\0" * (align4(len(raw)) - len(raw))


def attr_u32(type_: int, value: int) -> bytes:
    return attr(type_, struct.pack("<I", value))


def attr_str(type_: int, value: str) -> bytes:
    return attr(type_, value.encode() + b"\0")


def sockaddr_nl() -> bytes:
    return struct.pack("<HHII", AF_NETLINK, 0, 0, 0)


def sockaddr_ll(ifindex: int) -> bytes:
    # sll_protocol is in network byte order.
    return struct.pack("<H", AF_PACKET) + struct.pack(">H", ETH_P_ALL) + struct.pack("<I", ifindex) + bytes(12)


def parse_sockaddr_nl_port(address: bytes) -> int:
    if len(address) < 12 or struct.unpack_from("<H", address, 0)[0] != AF_NETLINK:
        raise ValueError("ldnd returned an invalid sockaddr_nl")
    return struct.unpack_from("<I", address, 4)[0]


def genl_request(family: int, flags: int, seq: int, port: int, cmd: int, attrs: bytes = b"") -> bytes:
    length = NLMSG_HDRLEN + GENL_HDRLEN + len(attrs)
    return struct.pack("<IHHII", length, family, flags | NLM_F_REQUEST, seq, port) + bytes([cmd, 1, 0, 0]) + attrs


def rtnl_setlink(seq: int, port: int, ifindex: int, *, up: bool | None = None, address: bytes | None = None) -> bytes:
    # struct ifinfomsg: family u8, pad u8, type u16, index s32, flags u32, change u32.
    flags = IFF_UP if up else 0
    change = IFF_UP if up is not None else 0
    body = struct.pack("<BBHiII", 0, 0, 0, ifindex, flags, change)
    if address is not None:
        body += attr(IFLA_ADDRESS, address)
    return struct.pack("<IHHII", NLMSG_HDRLEN + len(body), RTM_NEWLINK, NLM_F_REQUEST | NLM_F_ACK, seq, port) + body


@dataclass
class Message:
    type: int
    flags: int
    seq: int
    payload: bytes

    def error_code(self) -> int:
        return struct.unpack_from("<i", self.payload, 0)[0] if len(self.payload) >= 4 else -1

    def genl_attrs(self) -> dict[int, bytes]:
        found: dict[int, bytes] = {}
        offset = GENL_HDRLEN
        while offset + 4 <= len(self.payload):
            length, type_ = struct.unpack_from("<HH", self.payload, offset)
            if length < 4 or offset + length > len(self.payload):
                break
            found[type_ & NLA_TYPE_MASK] = self.payload[offset + 4:offset + length]
            offset += align4(length)
        return found


def parse_messages(data: bytes) -> list[Message]:
    messages = []
    offset = 0
    while offset + NLMSG_HDRLEN <= len(data):
        length, type_, flags, seq, _port = struct.unpack_from("<IHHII", data, offset)
        if length < NLMSG_HDRLEN or offset + length > len(data):
            raise ValueError("malformed netlink message")
        messages.append(Message(type_, flags, seq, data[offset + NLMSG_HDRLEN:offset + length]))
        offset += align4(length)
    return messages


@dataclass
class Interface:
    wiphy: int = 0
    ifindex: int = 0
    iftype: int = 0
    name: str = ""
    mac: bytes = field(default=b"")

    @classmethod
    def from_attrs(cls, attrs: dict[int, bytes]) -> "Interface":
        info = cls()
        if (v := attrs.get(NL80211_ATTR_WIPHY)) and len(v) >= 4:
            info.wiphy = struct.unpack_from("<I", v)[0]
        if (v := attrs.get(NL80211_ATTR_IFINDEX)) and len(v) >= 4:
            info.ifindex = struct.unpack_from("<I", v)[0]
        if (v := attrs.get(NL80211_ATTR_IFTYPE)) and len(v) >= 4:
            info.iftype = struct.unpack_from("<I", v)[0]
        if v := attrs.get(NL80211_ATTR_IFNAME):
            info.name = v.rstrip(b"\0").decode(errors="replace")
        info.mac = attrs.get(NL80211_ATTR_MAC, b"")
        return info
