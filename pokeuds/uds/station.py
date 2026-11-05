"""A UDS station: what the 3DS NWM service does for a game, on top of a raw radio.

One station is one console on the air. It can scan for networks, host one (beaconing, accepting
joins, assigning node ids) or join one as a client, and then exchange SecureData packets on data
channels, which is all a game sees of local wireless.

The join handshake, both directions (azahar_uds ``nwm_uds.cpp``, verified with retail consoles):

    client                                   host
    auth seq 1              ------------->
                            <-------------   auth seq 2 (+ association response)
    association request     ------------->
                            <-------------   association response (status 0, AID)
    EAPoL-Start (CCMP)      ------------->   node info: name, friend code seed
                            <-------------   EAPoL-Logoff (CCMP): assigned node id, node list
    SecureData ch 3 [00] every 1.1 s  ---->  mirrored back (keepalive)

The host's beacon must list a new node before the station acts on its Logoff: a retail console
reads the next beacon right after it and leaves again (deauth reason 3) if it still shows only the
host, so the beacon is updated before the Logoff is sent.
"""

from __future__ import annotations

import collections
import enum
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from ..radio.base import DISCOVERY_CHANNELS, Radio
from ..util import BROADCAST, mac_str
from . import beacon as bcn
from . import crypto, data, dot11

log = logging.getLogger(__name__)

KEEPALIVE_INTERVAL = 1.1
JOIN_RETRY_INTERVAL = 0.5
CHANNEL_DWELL = 0.25
PROBE_INTERVAL = 1.0


class Role(enum.Enum):
    IDLE = "idle"
    HOST = "host"
    CONNECTING = "connecting"
    CLIENT = "client"


class JoinStage(enum.Enum):
    AUTH = 1
    ASSOC = 2
    EAPOL = 3


@dataclass
class Peer:
    mac: bytes
    node_id: int = 0
    connected: bool = False
    spectator: bool = False
    last_mgmt_seq: int | None = None
    recent_pns: collections.deque = field(default_factory=lambda: collections.deque(maxlen=64))
    last_eapol: bytes = b""


class UdsError(RuntimeError):
    pass


class Station:
    def __init__(self, radio: Radio, mac: bytes, uds_key: bytes | None, *, name: str = "PokeUDS",
                 friend_code_seed: int | None = None) -> None:
        self.radio = radio
        self.mac = bytes(mac)
        self.uds_key = uds_key
        self.node = bcn.NodeInfo.named(name, friend_code_seed if friend_code_seed is not None
                                       else int.from_bytes(os.urandom(8), "big") & 0x00FFFFFFFFFFFFFF)
        self.role = Role.IDLE
        self.network: bcn.NetworkInfo | None = None
        self.nodes: list[bcn.NodeInfo] = []
        self.node_id = 0
        self.key: bytes | None = None
        self.peers: dict[bytes, Peer] = {}
        self.beacons: dict[bytes, bcn.Beacon] = {}
        self.on_beacon: Callable[[bcn.Beacon], None] | None = None
        self.on_frame: Callable[[dot11.Frame], None] | None = None
        self.on_node_change: Callable[[], None] | None = None

        self._lock = threading.RLock()
        self._channels: dict[int, queue.Queue[data.SecureData]] = collections.defaultdict(queue.Queue)
        self._dot11_seq = 0
        self._pn = 1
        self._secure_seq = 0
        self._join_stage: JoinStage | None = None
        self._join_aid = 0
        self._next_join_try = 0.0
        self._next_keepalive = 0.0
        self._connected = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_beacon_heard = 0.0
        self._beacon_times: dict[bytes, float] = {}

    # -- lifecycle --------------------------------------------------------------------------------

    def start(self, channel: int = DISCOVERY_CHANNELS[-1]) -> None:
        self.radio.start(channel, self.mac)
        self._thread = threading.Thread(target=self._run, name="uds-station", daemon=True)
        self._thread.start()

    def close(self) -> None:
        with self._lock:
            if self.role is Role.CLIENT and self.network:
                self._send_mgmt(dot11.FC_DEAUTH, self.network.host_mac, self.network.host_mac, dot11.deauth_body(3))
            self.radio.set_beacon(None)
            self.role = Role.IDLE
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self.radio.close()

    # -- scanning ---------------------------------------------------------------------------------

    def scan(self, seconds: float, *, wlan_comm_id: int | Iterable[int] | None = None, channels=DISCOVERY_CHANNELS,
             until: Callable[[bcn.Beacon], bool] | None = None) -> list[bcn.Beacon]:
        """Hop ``channels`` for ``seconds`` collecting the UDS beacons heard meanwhile (optionally of
        one game). While hosting or connected the radio stays on the network's channel."""
        if self.role is not Role.IDLE:
            channels = (self.radio.channel,)
        started = time.monotonic()
        deadline = started + seconds
        index = 0
        probe_seq = 0
        next_probe = 0.0
        while time.monotonic() < deadline:
            self.radio.set_channel(channels[index % len(channels)])
            dwell_end = time.monotonic() + CHANNEL_DWELL
            while time.monotonic() < min(dwell_end, deadline):
                now = time.monotonic()
                if now - self._last_beacon_heard < 2 * CHANNEL_DWELL:
                    dwell_end = max(dwell_end, now + CHANNEL_DWELL)  # Stay on a channel with traffic.
                    if now >= next_probe:
                        self.radio.send(dot11.probe_request(self.mac, probe_seq, self.radio.channel))
                        probe_seq += 1
                        next_probe = now + PROBE_INTERVAL
                found = self._matching(wlan_comm_id, started)
                if until and any(until(b) for b in found):
                    return found
                time.sleep(0.02)
            index += 1
        return self._matching(wlan_comm_id, started)

    def _matching(self, wlan_comm_id: int | Iterable[int] | None, since: float) -> list[bcn.Beacon]:
        wanted = None if wlan_comm_id is None else {wlan_comm_id} if isinstance(wlan_comm_id, int) else set(wlan_comm_id)
        with self._lock:
            return [b for host, b in self.beacons.items() if self._beacon_times.get(host, 0) >= since
                    and (wanted is None or b.info.wlan_comm_id in wanted)]

    # -- hosting ----------------------------------------------------------------------------------

    def host(self, *, wlan_comm_id: int, passphrase: bytes, channel: int, max_nodes: int,
             application_data: bytes = b"", id_: int = 0, network_id: int | None = None,
             attributes: int = 0) -> bcn.NetworkInfo:
        if self.uds_key is None:
            raise UdsError("hosting needs the keyslot 0x2D key (aes_keys.txt)")
        if max_nodes < 2:
            raise UdsError("a network needs room for at least two nodes")
        with self._lock:
            self.network = bcn.NetworkInfo(host_mac=self.mac, channel=channel, wlan_comm_id=wlan_comm_id, id=id_,
                                           update_counter=1, network_id=network_id or int.from_bytes(os.urandom(4), "big") or 1,
                                           attributes=attributes,
                                           total_nodes=1, max_nodes=max_nodes, application_data=application_data)
            self.key = self.network.ccmp_key(self.uds_key, passphrase)
            self.node_id = data.HOST_NODE
            self.node.network_node_id = data.HOST_NODE
            self.nodes = [self.node] + [bcn.NodeInfo() for _ in range(max_nodes - 1)]
            self.peers.clear()
            self._reset_counters()
            self.role = Role.HOST
            self.radio.set_channel(channel)
            self._update_beacon()
        log.info("hosting network %08X on channel %d (comm id %08X)", self.network.network_id, channel, wlan_comm_id)
        return self.network

    def set_application_data(self, application_data: bytes) -> None:
        with self._lock:
            if self.network is None:
                raise UdsError("not hosting")
            self.network.application_data = application_data
            self._update_beacon()

    def retune(self, channel: int) -> None:
        """Move the radio, and a hosted network's beacon with it (a Gen 6 PSS hops while it advertises)."""
        with self._lock:
            self.radio.set_channel(channel)
            if self.network is not None and self.role is Role.HOST:
                self.network.channel = channel
            self._update_beacon()

    def stop_hosting(self) -> None:
        """Take a hosted network down without a word to its nodes (the PSS re-hosts constantly)."""
        with self._lock:
            if self.role is Role.HOST:
                self.radio.set_beacon(None)
                self.role = Role.IDLE
                self.network = None
                self.peers.clear()
                self.node_id = 0

    def _update_beacon(self) -> None:
        if self.role is Role.HOST and self.network:
            self.network.channel = self.radio.channel or self.network.channel
            self.radio.set_beacon(bcn.beacon_frame(self.network, self.nodes))

    # -- joining ----------------------------------------------------------------------------------

    def connect(self, target: bcn.Beacon, passphrase: bytes, timeout: float = 15.0,
                spectator: bool = False) -> None:
        if self.uds_key is None:
            raise UdsError("joining needs the keyslot 0x2D key (aes_keys.txt)")
        with self._lock:
            self.network = bcn.NetworkInfo(**{**target.info.__dict__})
            self.key = self.network.ccmp_key(self.uds_key, passphrase)
            self._connection_type = data.CONNECTION_SPECTATOR if spectator else data.CONNECTION_CLIENT
            self.peers = {self.network.host_mac: Peer(self.network.host_mac, data.HOST_NODE)}
            self._reset_counters()
            self._connected.clear()
            self.role = Role.CONNECTING
            self._join_stage = JoinStage.AUTH
            self._next_join_try = 0.0
            self.radio.set_channel(target.channel)
            self.radio.watch(self.network.host_mac)
        log.info("joining %s network %08X on channel %d", mac_str(target.host), target.info.network_id, target.channel)
        if not self._connected.wait(timeout):
            with self._lock:
                stage = self._join_stage
                self.role = Role.IDLE
                self._join_stage = None
            raise UdsError(f"join timed out waiting for {stage.name.lower() if stage else 'the host'}")

    def _reset_counters(self) -> None:
        self._pn = 1
        self._secure_seq = 0
        for q in self._channels.values():
            while not q.empty():
                q.get_nowait()

    # -- data -------------------------------------------------------------------------------------

    def send(self, dest_node: int, channel: int, payload: bytes, *, broadcast: bool = False) -> None:
        if len(payload) > data.MAX_SEND_SIZE:
            raise UdsError(f"SecureData payload of {len(payload)} bytes exceeds {data.MAX_SEND_SIZE}")
        with self._lock:
            if self.role not in (Role.HOST, Role.CLIENT):
                raise UdsError("not connected")
            if dest_node == self.node_id:
                raise UdsError("cannot send to ourselves")
            packet = data.SecureData(channel, self._next_secure_seq(), dest_node, self.node_id, payload)
            self._send_securedata(packet, broadcast=broadcast)

    def recv(self, channel: int, timeout: float | None = None) -> data.SecureData | None:
        try:
            return self._channels[channel].get(timeout=timeout)
        except queue.Empty:
            return None

    def _next_secure_seq(self) -> int:
        seq = self._secure_seq
        self._secure_seq = (self._secure_seq + 1) & 0xFFFF
        return seq

    def _send_securedata(self, packet: data.SecureData, *, broadcast: bool = False) -> None:
        assert self.network is not None
        if broadcast or packet.dest_node == data.BROADCAST_NODE:
            dest = BROADCAST
        elif packet.dest_node == data.HOST_NODE:
            dest = self.network.host_mac
        else:
            peer = next((p for p in self.peers.values() if p.node_id == packet.dest_node and p.connected), None)
            if peer is None:
                raise UdsError(f"no node {packet.dest_node} in the network")
            dest = peer.mac
        self._send_data(dest, data.pack_securedata(packet))

    def _send_data(self, dest: bytes, plaintext: bytes) -> None:
        assert self.network is not None and self.key is not None
        frame = crypto.build_data_frame(self.key, plaintext, transmitter=self.mac, destination=dest,
                                        bssid=self.network.host_mac, from_ds=self.role is Role.HOST,
                                        pn=self._pn, seq=self._next_dot11_seq())
        self._pn += 1
        self.radio.send(frame)

    def _send_mgmt(self, fc: int, dest: bytes, bssid: bytes, body: bytes) -> None:
        self.radio.send(dot11.management(fc, self.mac, dest, bssid, self._next_dot11_seq(), body))

    def _next_dot11_seq(self) -> int:
        seq = self._dot11_seq
        self._dot11_seq = (self._dot11_seq + 1) & 0x0FFF
        return seq

    # -- engine -----------------------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            rx = self.radio.recv(0.01)
            try:
                with self._lock:
                    if rx is not None:
                        self._handle(rx.mpdu, rx.channel, rx.rssi)
                    self._timers()
            except Exception:
                log.exception("UDS engine error")

    def _timers(self) -> None:
        now = time.monotonic()
        if self.role is Role.CONNECTING and now >= self._next_join_try:
            self._next_join_try = now + JOIN_RETRY_INTERVAL
            self._send_join_step()
        elif self.role is Role.CLIENT and now >= self._next_keepalive:
            self._next_keepalive = now + KEEPALIVE_INTERVAL
            packet = data.SecureData(data.MANAGEMENT_CHANNEL, self._next_secure_seq(), data.HOST_NODE,
                                     self.node_id, b"\x00", management=True)
            self._send_securedata(packet)

    def _send_join_step(self) -> None:
        host = self.network.host_mac
        if self._join_stage is JoinStage.AUTH:
            self._send_mgmt(dot11.FC_AUTH, host, host, dot11.auth_body(1))
        elif self._join_stage is JoinStage.ASSOC:
            self._send_mgmt(dot11.FC_ASSOC_REQ, host, host, dot11.assoc_request_body(self.network.network_id))
        elif self._join_stage is JoinStage.EAPOL:
            self._send_data(host, data.eapol_start(self._join_aid, self.node, self._connection_type))

    def _handle(self, mpdu: bytes, channel: int, rssi: int | None) -> None:
        frame = dot11.parse(mpdu)
        if frame is None or frame.transmitter == self.mac:
            return
        if frame.type == dot11.TYPE_MGMT and frame.subtype in (dot11.SUBTYPE_BEACON, dot11.SUBTYPE_PROBE_RESP):
            heard = bcn.parse_beacon(frame, channel, rssi)
            if heard is not None:
                self._last_beacon_heard = time.monotonic()
                self.beacons[heard.host] = heard
                self._beacon_times[heard.host] = self._last_beacon_heard
                if self.on_beacon:
                    self.on_beacon(heard)
            return
        if self.on_frame:
            self.on_frame(frame)
        if self.network is None or frame.bssid != self.network.host_mac:
            return
        if frame.destination not in (self.mac, BROADCAST):
            return
        if frame.type == dot11.TYPE_MGMT:
            peer = self.peers.get(frame.transmitter)
            if peer and frame.retry and peer.last_mgmt_seq == frame.sequence:
                return  # A retransmission the monitor also captured.
            if peer:
                peer.last_mgmt_seq = frame.sequence
            if self.role is Role.HOST:
                self._host_mgmt(frame)
            else:
                self._client_mgmt(frame)
        elif frame.type == dot11.TYPE_DATA and frame.protected:
            self._data_frame(frame)

    # host side

    def _host_mgmt(self, frame: dot11.Frame) -> None:
        sender = frame.transmitter
        if frame.subtype == dot11.SUBTYPE_AUTH:
            auth = dot11.parse_auth(frame.body)
            if not auth or auth[1] != 1:
                return
            peer = self.peers.get(sender)
            if peer and peer.connected:
                return
            if self.network.total_nodes >= self.network.max_nodes:
                log.info("refusing %s: the network is full", mac_str(sender))
                return
            self.peers[sender] = Peer(sender, last_mgmt_seq=frame.sequence)
            self._send_mgmt(dot11.FC_AUTH, sender, self.mac, dot11.auth_body(2))
            # azahar_uds also answers authentication with an association response straight away,
            # which retail consoles accept; keep that proven behaviour.
            self._send_mgmt(dot11.FC_ASSOC_RESP, sender, self.mac, dot11.assoc_response_body(1, self.network.network_id))
        elif frame.subtype == dot11.SUBTYPE_ASSOC_REQ:
            if sender in self.peers:
                self._send_mgmt(dot11.FC_ASSOC_RESP, sender, self.mac, dot11.assoc_response_body(1, self.network.network_id))
        elif frame.subtype == dot11.SUBTYPE_DEAUTH:
            self._drop_peer(sender)

    def _drop_peer(self, mac: bytes) -> None:
        peer = self.peers.pop(mac, None)
        if not peer or not peer.connected or peer.spectator:
            return
        slot = peer.node_id - 1
        if 0 <= slot < len(self.nodes):
            self.nodes[slot] = bcn.NodeInfo()
        self.network.total_nodes -= 1
        self.network.update_counter = (self.network.update_counter + 1) & 0xFF
        self._update_beacon()
        log.info("node %d (%s) left", peer.node_id, mac_str(mac))
        if self.on_node_change:
            self.on_node_change()

    def _host_eapol_start(self, peer: Peer, payload: bytes) -> None:
        if peer.connected:
            return
        start = data.parse_eapol_start(payload)
        if start is None:
            return
        node = start.node
        if start.connection_type == data.CONNECTION_CLIENT:
            free = next((i for i, n in enumerate(self.nodes) if n.empty()), None)
            if free is None:
                return
            node.network_node_id = free + 1
            self.nodes[free] = node
            self.network.total_nodes += 1
            self.network.update_counter = (self.network.update_counter + 1) & 0xFF
            peer.node_id, peer.connected = node.network_node_id, True
            self._update_beacon()  # Before the Logoff: see the module docstring.
        elif start.connection_type == data.CONNECTION_SPECTATOR:
            peer.node_id, peer.connected, peer.spectator = 0, True, True
        else:
            log.warning("unknown EAPoL connection type %d", start.connection_type)
            return
        self._send_data(peer.mac, data.eapol_logoff(peer.mac, node.network_node_id, self.nodes,
                                                    self.network.max_nodes, self.network.total_nodes))
        log.info("node %d joined: %s (%s)", node.network_node_id, node.name, mac_str(peer.mac))
        if self.on_node_change:
            self.on_node_change()

    # client side

    def _client_mgmt(self, frame: dot11.Frame) -> None:
        if frame.transmitter != self.network.host_mac:
            return
        if frame.subtype == dot11.SUBTYPE_AUTH and self._join_stage is JoinStage.AUTH:
            auth = dot11.parse_auth(frame.body)
            if auth and auth[1] == 2 and auth[2] == 0:
                self._join_stage = JoinStage.ASSOC
                self._next_join_try = 0.0
        elif frame.subtype == dot11.SUBTYPE_ASSOC_RESP and self._join_stage in (JoinStage.AUTH, JoinStage.ASSOC):
            result = dot11.parse_assoc_response(frame.body)
            if result and result[0] == 0 and self._join_stage is JoinStage.ASSOC:
                self._join_aid = result[1]
                self._join_stage = JoinStage.EAPOL
                self._next_join_try = 0.0
            elif result and result[0] != 0:
                log.warning("host refused the association (status %d)", result[0])
        elif frame.subtype == dot11.SUBTYPE_DEAUTH:
            if self.role is Role.CLIENT:
                log.info("the host closed the connection")
                self.role = Role.IDLE
                if self.on_node_change:
                    self.on_node_change()

    def _client_logoff(self, payload: bytes) -> None:
        logoff = data.parse_eapol_logoff(payload)
        if logoff is None:
            return
        self.network.total_nodes = logoff.connected_nodes
        self.nodes = logoff.nodes[:max(logoff.max_nodes, 1)]
        if self.role is Role.CONNECTING:
            self.node_id = logoff.assigned_node_id
            self.node.network_node_id = self.node_id
            self.peers[self.network.host_mac].connected = True
            self.role = Role.CLIENT
            self._join_stage = None
            self._next_keepalive = time.monotonic() + KEEPALIVE_INTERVAL
            log.info("joined as node %d", self.node_id)
            self._connected.set()
        if self.on_node_change:
            self.on_node_change()

    # both

    def _data_frame(self, frame: dot11.Frame) -> None:
        peer = self.peers.get(frame.transmitter)
        if peer is None or self.key is None:
            return
        pn = crypto.ccmp_pn(frame.body) if len(frame.body) >= 8 else -1
        if pn in peer.recent_pns:
            return  # A retransmission.
        plaintext = crypto.open_data_frame(self.key, frame)
        if plaintext is None:
            log.debug("CCMP check failed for a frame from %s", mac_str(frame.transmitter))
            return
        peer.recent_pns.append(pn)
        kind = data.ethertype(plaintext)
        if kind == data.ETHERTYPE_EAPOL:
            if plaintext == peer.last_eapol:
                return
            peer.last_eapol = plaintext
            eapol = data.eapol_kind(plaintext)
            if self.role is Role.HOST and eapol == data.EAPOL_START:
                self._host_eapol_start(peer, plaintext)
            elif self.role in (Role.CONNECTING, Role.CLIENT) and eapol == data.EAPOL_LOGOFF:
                self._client_logoff(plaintext)
        elif kind == data.ETHERTYPE_SECUREDATA and self.role in (Role.HOST, Role.CLIENT):
            for packet in data.unpack_securedata(plaintext):
                self._securedata(peer, packet)

    def _securedata(self, peer: Peer, packet: data.SecureData) -> None:
        if packet.src_node == self.node_id:
            return
        if packet.dest_node not in (self.node_id, data.BROADCAST_NODE):
            if self.role is Role.HOST:
                self._send_securedata(packet, broadcast=True)  # Relay for the intended client.
            return
        if packet.management:
            if self.role is Role.HOST and packet.channel == data.MANAGEMENT_CHANNEL and packet.payload == b"\x00":
                reply = data.SecureData(packet.channel, self._next_secure_seq(), packet.src_node, self.node_id,
                                        packet.payload, management=True)
                self._send_data(peer.mac, data.pack_securedata(reply))
            return
        self._channels[packet.channel].put(packet)
