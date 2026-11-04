"""The ldnd backend against a scripted daemon that answers like ldnd 0.0.5 with one adapter."""

import struct
import threading
import time

from pokeuds.radio import ldnd
from pokeuds.radio import netlink as nl
from pokeuds.radio import radiotap

FAMILY = 0x1C
WIPHY = 0
PHYS_IFINDEX = 4
MONITOR_IFINDEX = 9


class FakeDaemon:
    """A Stream whose far end behaves like ldnd plus a Linux kernel with one Wi-Fi adapter."""

    def __init__(self):
        self.inbox = bytearray()     # Bytes for the client to read.
        self.lock = threading.Lock()
        self.sockets: dict[int, tuple[int, int, int]] = {}
        self.ports: dict[int, int] = {}
        self.interfaces = [nl.Interface(WIPHY, PHYS_IFINDEX, 2, "wlan0", bytes(6))]
        self.commands: list[int] = []
        self.links: list[bytes] = []
        self.injected: list[bytes] = []
        self._buffer = bytearray()

    # Stream API -------------------------------------------------------------------------------
    def read_exact(self, count):
        deadline = time.monotonic() + 2
        while True:
            with self.lock:
                if len(self.inbox) >= count:
                    out = bytes(self.inbox[:count])
                    del self.inbox[:count]
                    return out
            if time.monotonic() > deadline:
                raise EOFError("fake daemon has nothing to say")
            time.sleep(0.001)

    def available(self):
        with self.lock:
            return len(self.inbox)

    def close(self):
        pass

    def write(self, data):
        self._buffer += data
        while len(self._buffer) >= ldnd.HEADER.size:
            op, sid, a0, a1, a2, length = ldnd.HEADER.unpack_from(self._buffer)
            if len(self._buffer) < ldnd.HEADER.size + length:
                return
            blob = bytes(self._buffer[ldnd.HEADER.size:ldnd.HEADER.size + length])
            del self._buffer[:ldnd.HEADER.size + length]
            self._handle(op, sid, a0, a1, a2, blob)

    # Daemon -----------------------------------------------------------------------------------
    def _send(self, op, sid, a0=0, blob=b""):
        with self.lock:
            self.inbox += ldnd.HEADER.pack(op, sid, a0, 0, 0, len(blob)) + blob

    def deliver_packet(self, packet):
        sid = next(s for s, (d, _t, _p) in self.sockets.items() if d == nl.AF_PACKET)
        self._send(ldnd.Op.DATA, sid, blob=packet)

    def _handle(self, op, sid, a0, a1, a2, blob):
        if op == ldnd.Op.SOCKET:
            self.sockets[sid] = (a0, a1, a2)
            self.ports[sid] = 1000 + sid
            self._send(ldnd.Op.REPLY, sid)
        elif op in (ldnd.Op.BIND, ldnd.Op.START, ldnd.Op.SETSOCKOPT):
            self._send(ldnd.Op.REPLY, sid)
        elif op == ldnd.Op.GETSOCKNAME:
            self._send(ldnd.Op.REPLY, sid, blob=struct.pack("<HHII", nl.AF_NETLINK, 0, self.ports[sid], 0))
        elif op == ldnd.Op.CLOSE:
            self.sockets.pop(sid, None)
        elif op == ldnd.Op.SENDTO:
            domain, _type, proto = self.sockets[sid]
            if domain == nl.AF_PACKET:
                self.injected.append(blob)
            elif proto == nl.NETLINK_GENERIC:
                self._genl(sid, blob)
            else:
                self._rtnl(sid, blob)

    def _reply(self, sid, type_, flags, seq, payload):
        msg = struct.pack("<IHHII", 16 + len(payload), type_, flags, seq, self.ports[sid]) + payload
        self._send(ldnd.Op.DATA, sid, blob=msg + b"\0" * (nl.align4(len(msg)) - len(msg)))

    def _ack(self, sid, seq, error=0):
        self._reply(sid, nl.NLMSG_ERROR, 0, seq, struct.pack("<i", error) + bytes(16))

    def _genl(self, sid, blob):
        (msg,) = nl.parse_messages(blob)
        cmd = msg.payload[0]
        attrs = msg.genl_attrs()
        if msg.type == nl.GENL_ID_CTRL:
            self._reply(sid, nl.GENL_ID_CTRL, 0, msg.seq, bytes([1, 1, 0, 0]) + nl.attr(nl.CTRL_ATTR_FAMILY_ID, struct.pack("<H", FAMILY)))
            return
        self.commands.append(cmd)
        if cmd == nl.NL80211_CMD_GET_INTERFACE:
            for iface in self.interfaces:
                body = (bytes([cmd, 1, 0, 0]) + nl.attr_u32(nl.NL80211_ATTR_WIPHY, iface.wiphy)
                        + nl.attr_u32(nl.NL80211_ATTR_IFINDEX, iface.ifindex) + nl.attr_u32(nl.NL80211_ATTR_IFTYPE, iface.iftype)
                        + nl.attr_str(nl.NL80211_ATTR_IFNAME, iface.name))
                self._reply(sid, FAMILY, nl.NLM_F_MULTI, msg.seq, body)
            self._reply(sid, nl.NLMSG_DONE, nl.NLM_F_MULTI, msg.seq, bytes(4))
        elif cmd == nl.NL80211_CMD_NEW_INTERFACE:
            name = attrs[nl.NL80211_ATTR_IFNAME].rstrip(b"\0").decode()
            self.interfaces.append(nl.Interface(WIPHY, MONITOR_IFINDEX, nl.NL80211_IFTYPE_MONITOR, name))
            self.monitor_flags = attrs[nl.NL80211_ATTR_MNTR_FLAGS]
            self._ack(sid, msg.seq)
        elif cmd == nl.NL80211_CMD_DEL_INTERFACE:
            index = struct.unpack("<I", attrs[nl.NL80211_ATTR_IFINDEX])[0]
            self.interfaces = [i for i in self.interfaces if i.ifindex != index]
            self._ack(sid, msg.seq)
        elif cmd == nl.NL80211_CMD_SET_CHANNEL:
            self.frequency = struct.unpack("<I", attrs[nl.NL80211_ATTR_WIPHY_FREQ])[0]
            self._ack(sid, msg.seq)

    def _rtnl(self, sid, blob):
        (msg,) = nl.parse_messages(blob)
        self.links.append(msg.payload)
        self._ack(sid, msg.seq)


def test_ldnd_setup_inject_and_capture():
    daemon = FakeDaemon()
    daemon.interfaces.append(nl.Interface(WIPHY, 7, nl.NL80211_IFTYPE_MONITOR, "udsmon0"))  # Stale.
    radio = ldnd.LdndRadio(stream_factory=lambda: daemon)
    mac = bytes.fromhex("020000000099")
    radio.start(6, mac)
    try:
        assert radio.active_monitor and radio.ifindex == MONITOR_IFINDEX
        assert nl.NL80211_CMD_DEL_INTERFACE in daemon.commands  # The stale monitor went first.
        assert daemon.frequency == 2437
        # Active monitor flag requested, then our MAC set, then the link brought up.
        assert struct.pack("<HH", 4, nl.NL80211_MNTR_FLAG_ACTIVE) in daemon.monitor_flags
        assert daemon.links[0].endswith(nl.attr(nl.IFLA_ADDRESS, mac))
        assert struct.unpack_from("<I", daemon.links[1], 8)[0] == nl.IFF_UP

        unicast = bytes.fromhex("0841") + bytes(2) + bytes.fromhex("020000000001") + mac + bytes.fromhex("020000000001") + bytes(2) + b"data"
        broadcast = bytes.fromhex("0800") + bytes(2) + b"\xff" * 6 + mac + mac + bytes(2) + b"bcast"
        radio.send(unicast)
        radio.send(broadcast)
        deadline = time.monotonic() + 2
        while len(daemon.injected) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        first, second = (radiotap.strip(p) for p in daemon.injected[:2])
        assert first[0] == unicast and second[0] == broadcast
        assert daemon.injected[0][8] == 22                                         # Client data rate.
        assert struct.unpack_from("<H", daemon.injected[1], 10)[0] == radiotap.TX_NOACK

        captured = struct.pack("<BBHI", 0, 0, 8, 0) + broadcast
        daemon.deliver_packet(captured)
        rx = radio.recv(2.0)
        assert rx is not None and rx.mpdu == broadcast

        radio.set_channel(11)
        assert daemon.frequency == 2462
    finally:
        radio.close()
