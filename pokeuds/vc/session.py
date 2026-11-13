"""The Pia session between two 3DS Virtual Console Game Boy titles, in either role.

Joiner (measured: the retail 2DS and Azahar as joiners; mGBA-LDN trades with retail hosts this way):

    host hello (every 0.5 s)       -> hello reply, then station info (seq S)
    host station info (seq T)      -> seq ack, then profile (S+1, re-sent every 0.5 s until acked)
    host profile (T+1)             -> seq ack, then join request (S+2)
    host mesh state (T+2)          -> seq ack; station index FD -> 01; ping and clock sync start
    host system data (every 10 s)  -> system ack
    game stream                    one reliable unit per Game Boy serial exchange, from index -2001

Host (**experimental**: built from the retail host's frames in the same capture; Azahar hosting a
retail joiner never completed a trade, for a cause not visible on the wire): the same messages in
the other direction, with the mesh state and system data replayed from the retail host's. Its setup
messages (station info, profile, mesh state) go to destination 0, as a retail 3DS XL host's did in a sniff of
two retail consoles (2026-10-04): the joiner has no station id until the mesh state, and a retail joiner ignored
station info and a profile addressed to station 2 (it took only PokeUDS's acks, addressed to 0). And the host
answers each of the joiner's setup messages in turn, as that host did (its packet numbers place the acks the sniff
missed): the joiner's station info -> ack, our station info; its profile -> ack, our profile; its join request ->
ack, the mesh state. Sending our station info at the hello reply and our profile at its station info, one step
early, a retail joiner acked both and never sent its join request.

The session is deterministic: every entry point takes the time in milliseconds, and frames leave
through ``send(frame, broadcast)``.
"""

from __future__ import annotations

import enum
import logging
import os
import struct
from dataclasses import dataclass, field
from typing import Callable

from . import pia

log = logging.getLogger(__name__)

SETUP_RESEND_MS = 500
UNIT_RESEND_MS = 500
PING_MS = 2000
CLOCK_SYNC_MS = 2000
SYSTEM_DATA_MS = 10000
SYSTEM_RESEND_MS = 250              # the host re-sends system data until the joiner acks it (Azahar's hosting VC)
HOST_START_FLAGS = b"\xfe\x00\x00"  # the role flags of the host's first unit, EF: its Start
HELLO_MS = 500
SILENCE_MS = 10000
SETUP_FALLBACK_MS = 1500
TICKS_PER_SECOND = 268_111_856      # the 3DS ARM11 clock the ping and clock-sync payloads count
SEND_RING = 1024

# The retail host's mesh state and system data (VC Red, 3DS XL; capture of 2026-10-04): station
# tables for host station 1 and joiner station 2. The trailing sequence value (mesh state) and the
# stream indexes (system data) are filled in per session.
MESH_STATE = bytes.fromhex(
    "0202000101000200000000000000000000000001000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000002000000000000000000000000"
    "00000000000000000000000000000000000000000000000000000000000000000000010000000000")
SYSTEM_DATA = bytes.fromhex(
    "0003007c00000000fffff82ffffff82f00000000000000002002000000000001010002000000000000000001"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000020000000000000000000000000000000000000000000000000000000000000000"
    "00000000000000000000000000000100")
assert len(MESH_STATE) == 128 and len(SYSTEM_DATA) == 148


def _describe(m: pia.Message) -> str:
    """One setup-time message for the log: what it is, its addresses, and its sequence value."""
    p = m.payload
    if m.protocol == pia.PROTO_SETUP and len(p) == 8 and p[0] == 0x05:
        what = f"ack {struct.unpack_from('>I', p, 4)[0]:08X}"
    elif m.protocol == pia.PROTO_SETUP and len(p) in (62, 80):
        what = f"{'station info' if len(p) == 62 else 'profile'} {struct.unpack_from('>I', p, len(p) - 4)[0]:08X}"
    elif m.protocol == pia.PROTO_SYSTEM and len(p) == 16 and p[:2] == bytes([0x01, pia.STATION_UNASSIGNED]):
        what = f"join request {struct.unpack_from('>I', p, 12)[0]:08X}"
    elif m.protocol == pia.PROTO_SYSTEM and len(p) == 128:
        what = f"mesh state {struct.unpack_from('>I', p, 124)[0]:08X}"
    else:
        what = f"protocol {m.protocol:02X}/{m.subtype:02X}, {len(p)} bytes"
    return f"{what} (station {m.sender:02X} -> {m.destination})"


class Role(enum.Enum):
    JOINER = "joiner"
    HOST = "host"


class State(enum.Enum):
    IDLE = "idle"
    SETUP = "setup"
    JOINED = "joined"
    CLOSED = "closed"


@dataclass
class _Setup:
    protocol: int
    payload: bytearray
    sequence: int
    acked: bool = False
    last_sent: int | None = None


@dataclass
class Config:
    name: str = "POKEUDS"
    application_data: bytes = bytes(16)   # the host beacon's; a joiner copies bytes 4..7 into its profile
    connection_id: int = 0                 # the station constant id, 2..255; 0 picks one
    tick_base: int = 0                     # 3DS ticks at start; 0 picks one
    unit_value: int | None = None          # the 32-bit value on every game unit; None: a random one for a host, 0 for a
                                           # joiner (retail hosts send a per-session value on every unit: D3613024, 30583202)


@dataclass
class Session:
    role: Role
    send: Callable[[bytes, bool], None]
    config: Config = field(default_factory=Config)

    def __post_init__(self) -> None:
        cfg = self.config
        self.connection_id = cfg.connection_id or 2 + os.urandom(1)[0] % 254
        self.tick_base = cfg.tick_base or int.from_bytes(os.urandom(5), "big")
        self.state = State.IDLE
        self.station = pia.STATION_HOST if self.role is Role.HOST else pia.STATION_UNASSIGNED
        self.own_id = pia.ID_HOST if self.role is Role.HOST else pia.ID_JOINER
        self.peer_id = pia.ID_JOINER if self.role is Role.HOST else pia.ID_HOST
        self.numbered = False
        self.packet_id = 0
        self.start_ms = self.last_rx = self.last_tx = 0
        self.next_sequence = self.tick_base & 0xFFFFFFFF
        self.setup: dict[str, _Setup] = {}
        self.seen_sequences: set[int] = set()
        self.peer_station_info = self.peer_profile = self.peer_join_request = False
        self.peer_clock: tuple[int, int] | None = None
        self.peer_leaving = False
        self.hello_answered = False
        self.last_hello = self.last_ping = self.last_clock_sync = self.last_system = -10**9
        self.system_next = pia.FIRST_INDEX       # joiner: the host's next system index; host: our own
        # Reliable game stream.
        self.send_ring: dict[int, int] = {}
        self.send_next = self.send_acked = self.send_flushed = pia.FIRST_INDEX
        self.sent_at: dict[int, int] = {}
        self.last_resend = 0
        self.recv_next = pia.FIRST_INDEX
        self.pending: dict[int, int] = {}
        self.received: list[int] = []
        self.ack_owed = False
        self.unit_value = cfg.unit_value if cfg.unit_value is not None else (
            int.from_bytes(os.urandom(4), "big") | 1 if self.role is Role.HOST else 0)
        # Host: the system stream's last sent index and the joiner's ack of it. The VC host re-sends a system data
        # message until it is acked, and only then sends its first game unit, EF: its Start (Azahar hosting a retail
        # joiner that went on to the Trade Center: system data at 1.86 s, re-sent at 2.12 and 2.37, acked at 2.44,
        # EF at 4.86).
        self.system_sent: int | None = None
        self.system_acked = pia.FIRST_INDEX
        self.last_system_send = -10**9
        self.frames_rejected = 0
        self.hellos_sent = 0
        self.frames_received: dict[str, int] = {}     # by kind, for the log when a session fails

    # -- frames ---------------------------------------------------------------------------------

    def _ticks(self, now: int) -> int:
        return self.tick_base + (now - self.start_ms) * TICKS_PER_SECOND // 1000

    def _header(self, now: int, direct: bool) -> dict:
        if direct:
            conn, pid = 0, 0
        elif self.numbered:
            self.packet_id = (self.packet_id + 1) & 0xFFFF
            conn, pid = self.connection_id, self.packet_id
        else:
            conn, pid = (pia.STATION_JOINER if self.role is Role.JOINER else 0), 0
        peer = 0
        if self.peer_clock:
            peer = self.peer_clock[0] + (now - self.peer_clock[1])
        return dict(connection_id=conn, packet_id=pid, clock=now - self.start_ms, peer_clock=peer)

    def _emit(self, now: int, raw: bytes, broadcast: bool = False) -> None:
        self.last_tx = now
        self.send(raw, broadcast)

    def _send(self, now: int, messages: list[pia.Message], *, direct: bool = False, broadcast: bool = False) -> None:
        for m in messages:
            m.sender = self.station
            if self.state is not State.JOINED:
                log.info("VC setup: sent %s", _describe(m))
        self._emit(now, pia.build(messages, **self._header(now, direct)), broadcast)

    def _msg(self, protocol: int, payload: bytes = b"", destination: int | None = None, **kw) -> pia.Message:
        return pia.Message(protocol, payload, self.peer_id if destination is None else destination, **kw)

    # -- setup ----------------------------------------------------------------------------------

    def _queue(self, key: str, protocol: int, payload: bytes) -> None:
        seq = self.next_sequence
        self.next_sequence = (seq + 1) & 0xFFFFFFFF
        body = bytearray(payload)
        struct.pack_into(">I", body, len(body) - 4, seq)
        self.setup[key] = _Setup(protocol, body, seq)

    def _queue_station_info(self) -> None:
        p = bytearray(62)
        p[0], p[1], p[2] = 0x01, self.connection_id, 0x05
        p[3] = 0x01 if self.role is Role.HOST else 0x00
        p[11] = self.own_id
        self._queue("station", pia.PROTO_SETUP, p)

    def _queue_profile(self) -> None:
        p = bytearray(80)
        p[0:4] = b"\x02\x00\x05\x02"
        units = [ord(c) for c in self.config.name[:10]]
        for i, u in enumerate(units):
            struct.pack_into("<H", p, 4 + 2 * i, u)
            struct.pack_into(">H", p, 36 + 2 * i, u)
        p[68] = 0x01 if self.role is Role.HOST else 0x06
        struct.pack_into(">I", p, 70, pia.profile_value(self.config.application_data))
        self._queue("profile", pia.PROTO_SETUP, p)

    def _queue_join_request(self) -> None:
        p = bytearray(16)
        p[0], p[1], p[11] = 0x01, pia.STATION_UNASSIGNED, self.own_id
        self._queue("join", pia.PROTO_SYSTEM, p)

    def _queue_mesh_state(self) -> None:
        self._queue("mesh", pia.PROTO_SYSTEM, MESH_STATE)

    def _pump_setup(self, now: int) -> None:
        for setup in self.setup.values():
            if setup.acked:
                continue
            if setup.last_sent is None or now - setup.last_sent >= SETUP_RESEND_MS:
                setup.last_sent = now
                dest = pia.ID_BROADCAST     # both roles: the peer has no station id before the mesh state
                if setup is self.setup.get("mesh"):
                    self.numbered = True              # the host's mesh-state frame is its first numbered one
                self._send(now, [self._msg(setup.protocol, bytes(setup.payload), dest)])

    def _ack_sequence(self, now: int, sequence: int) -> None:
        self._send(now, [self._msg(pia.PROTO_SETUP, b"\x05\x00\x00\x00" + struct.pack(">I", sequence), pia.ID_BROADCAST)])

    def _joined(self, now: int) -> None:
        if self.state is State.JOINED:
            return
        self.state = State.JOINED
        self.last_ping = self.last_clock_sync = self.last_system = now
        log.info("VC session joined as %s", self.role.value)
        if self.role is Role.JOINER:
            self._send_ping(now)
            self._send_clock_sync(now)
        else:
            self._send_system_data(now)

    # -- public ---------------------------------------------------------------------------------

    def start(self, now: int) -> None:
        self.start_ms = self.last_rx = self.last_tx = now
        if self.role is Role.HOST:
            self.state = State.SETUP
            self._emit(now, pia.hello(int.from_bytes(os.urandom(4), "big")), broadcast=True)
            self.hellos_sent += 1
            self.last_hello = now

    def receive(self, now: int, raw: bytes) -> None:
        if self.state is State.CLOSED:
            return
        frame = pia.parse(raw)
        if frame is None:
            self.frames_rejected += 1
            return
        kind = {pia.KIND_HELLO: "hello", pia.KIND_HELLO_REPLY: "hello reply", pia.KIND_BYE: "bye",
                pia.KIND_PIA: "pia"}.get(frame.kind, f"kind {frame.kind:02X}")
        if not self.frames_received:
            log.info("VC session: first frame from the peer: %s, %d bytes", kind, len(raw))
        self.frames_received[kind] = self.frames_received.get(kind, 0) + 1
        self.last_rx = now
        if frame.kind == pia.KIND_HELLO and self.role is Role.JOINER:
            self._emit(now, pia.hello_reply())
            self._begin_joiner_setup(now)
            return
        if frame.kind == pia.KIND_HELLO_REPLY and self.role is Role.HOST:
            self.hello_answered = True            # the joiner's station info comes next; ours answers it
            return
        if frame.kind == pia.KIND_BYE:
            self.state = State.CLOSED
            return
        if frame.kind != pia.KIND_PIA or not frame.tail_ok:
            self.frames_rejected += 1
            return
        self.peer_clock = (frame.clock, now)
        if self.state is not State.JOINED:
            for m in frame.messages:
                log.info("VC setup: heard %s", _describe(m))
        if self.role is Role.JOINER:
            self._begin_joiner_setup(now)
        pongs = []
        for m in frame.messages:
            pong = self._on_message(now, m)
            if pong:
                pongs.append(pong)
        for pong in pongs:
            self._send(now, [self._msg(pia.PROTO_PING, pong)])
        if self.ack_owed and self.state is State.JOINED:
            self._send(now, [self._msg(pia.PROTO_GAME, pia.record_ack(self.recv_next))])
            self.ack_owed = False
        self._advance_setup(now)

    def _begin_joiner_setup(self, now: int) -> None:
        if self.state is State.IDLE:
            self.state = State.SETUP
            self.setup_start = now
            self._queue_station_info()
            self._pump_setup(now)

    def _advance_setup(self, now: int) -> None:
        if self.role is Role.JOINER and self.state is State.SETUP:
            if self.peer_station_info and "profile" not in self.setup:
                self._queue_profile()
            if self.peer_profile and "join" not in self.setup:
                self._queue_join_request()
        if self.role is Role.HOST and self.state is State.SETUP:
            if self.peer_station_info and "station" not in self.setup:
                self._queue_station_info()
            if self.peer_profile and "profile" not in self.setup:
                self._queue_profile()
            if self.peer_join_request and "mesh" not in self.setup:
                self._queue_mesh_state()
        self._pump_setup(now)
        if self.role is Role.HOST and "mesh" in self.setup and self.setup["mesh"].acked:
            self._joined(now)

    def _on_message(self, now: int, m: pia.Message) -> bytes | None:
        p = m.payload
        if m.protocol == pia.PROTO_SETUP:
            if len(p) == 8 and p[0] == 0x05:
                seq = struct.unpack_from(">I", p, 4)[0]
                for setup in self.setup.values():
                    if setup.sequence == seq:
                        setup.acked = True
            elif (len(p) == 62 and p[0] == 0x01) or (len(p) == 80 and p[0] == 0x02):
                seq = struct.unpack_from(">I", p, len(p) - 4)[0]
                if len(p) == 62:
                    self.peer_station_info = True
                else:
                    self.peer_profile = True
                self.seen_sequences.add(seq)
                self._ack_sequence(now, seq)
        elif m.protocol == pia.PROTO_SYSTEM:
            if m.subtype == pia.SUBTYPE_CLOCK_SYNC and len(p) == 16:
                if self.role is Role.HOST and p[8:16] == bytes(8):
                    reply = p[:8] + bytes(4) + struct.pack(">I", (now - self.start_ms) & 0xFFFFFFFF)
                    self._send(now, [self._msg(pia.PROTO_SYSTEM, reply, subtype=pia.SUBTYPE_CLOCK_SYNC)])
            elif m.reliable and len(p) >= 16 and p[1] == 0x03:
                index = struct.unpack_from(">i", p, 8)[0]
                if index == self.system_next:
                    self.system_next += 1
                self._send(now, [self._msg(pia.PROTO_SYSTEM, pia.record_ack(self.system_next), reliable=1)])
                if len(p) == 36:
                    self.peer_leaving = True        # the short end-of-session record (Gen 2 seen)
            elif m.reliable and (ack := pia.record_ack_index(p)) is not None:
                if self.role is Role.HOST and ack > self.system_acked:  # the joiner's system ack
                    if self.system_acked == pia.FIRST_INDEX:
                        log.info("VC session: the console acked our system data (index %d)", ack - 1)
                    self.system_acked = ack
            elif len(p) == 128 and p[0] == 0x02 and p[1] == 0x02 and self.role is Role.JOINER:
                seq = struct.unpack_from(">I", p, 124)[0]
                self.station = pia.STATION_JOINER   # the ack of the mesh state already carries index 01
                self.numbered = True
                self._ack_sequence(now, seq)
                self._joined(now)
            elif len(p) == 16 and p[0] == 0x01 and p[1] == pia.STATION_UNASSIGNED and self.role is Role.HOST:
                self.peer_join_request = True
                self._ack_sequence(now, struct.unpack_from(">I", p, 12)[0])
        elif m.protocol == pia.PROTO_PING and len(p) == 16 and p[3] == 0x00:
            return p[:3] + b"\x01" + p[4:]
        elif m.protocol == pia.PROTO_GAME:
            unit = pia.Unit.unpack(p)
            if unit:
                self._on_unit(unit)
            elif (ack := pia.record_ack_index(p)) is not None:
                self._on_ack(ack)
        return None

    def _on_ack(self, ack: int) -> None:
        if self.send_acked == pia.FIRST_INDEX < ack <= self.send_next:
            log.info("VC session: the peer acked our first game unit")
        if self.send_acked < ack <= self.send_next:
            self.send_acked = ack

    def _on_unit(self, unit: pia.Unit) -> None:
        if unit.index == pia.FIRST_INDEX and self.recv_next == pia.FIRST_INDEX:
            log.info("VC session: first game unit from the peer: %02X", unit.byte)
        self._on_ack(unit.ack)
        if unit.index == self.recv_next:
            self.received.append(unit.byte)
            self.recv_next += 1
            while self.recv_next in self.pending:
                self.received.append(self.pending.pop(self.recv_next))
                self.recv_next += 1
        elif self.recv_next < unit.index < self.recv_next + 64:
            self.pending[unit.index] = unit.byte
        self.ack_owed = True

    # -- game stream ----------------------------------------------------------------------------

    def queue_unit(self, byte: int) -> bool:
        if self.state is not State.JOINED or self.send_next - self.send_acked >= SEND_RING - pia.UNIT_WINDOW:
            return False
        self.send_ring[self.send_next] = byte & 0xFF
        self.send_next += 1
        return True

    def pop_units(self) -> list[int]:
        out, self.received = self.received, []
        return out

    def flush(self, now: int) -> None:
        if self.state is State.JOINED and self.send_next > self.send_flushed:
            self._send_units(now, self.send_flushed, self.send_next)
            self.send_flushed = self.send_next

    def _flags(self, index: int) -> bytes:
        """A unit's role flags: 01 01 00 from a joiner; 00 00 00 from a host, except its first unit (EF, its Start),
        FE 00 00 in all six captures (four retail hosts, two hosting VCs in Azahar). An EF with 00 00 00 was acked by a
        retail joiner that then stayed in the link menu."""
        if self.role is Role.JOINER:
            return b"\x01\x01\x00"
        return HOST_START_FLAGS if index == pia.FIRST_INDEX else b"\x00\x00\x00"

    def _send_units(self, now: int, first: int, end: int) -> None:
        while first < end:
            count = min(end - first, pia.UNIT_WINDOW)
            messages = [self._msg(pia.PROTO_GAME, pia.Unit(i, self.recv_next, self.send_ring[i], self._flags(i),
                                                           value=self.unit_value).pack())
                        for i in range(first, first + count)]
            for i in range(first, first + count):
                self.sent_at[i] = now
            self._send(now, messages)
            self.ack_owed = False
            first += count
        for old in [i for i in self.send_ring if i < self.send_acked - SEND_RING]:
            self.send_ring.pop(old, None)
            self.sent_at.pop(old, None)

    # -- timers ---------------------------------------------------------------------------------

    def _send_ping(self, now: int) -> None:
        self._send(now, [self._msg(pia.PROTO_PING, bytes(8) + struct.pack(">Q", self._ticks(now)))])

    def _send_clock_sync(self, now: int) -> None:
        self._send(now, [self._msg(pia.PROTO_SYSTEM, struct.pack(">Q", self._ticks(now)) + bytes(8),
                                   pia.ID_BROADCAST, subtype=pia.SUBTYPE_CLOCK_SYNC)], direct=True)

    def _send_system_data(self, now: int, index: int | None = None) -> None:
        """System data at ``index`` (a re-send), or at the next index."""
        if index is None:
            index = self.system_next
            self.system_next += 1
        payload = bytearray(SYSTEM_DATA)
        struct.pack_into(">ii", payload, 8, index, pia.FIRST_INDEX)
        self.system_sent, self.last_system_send = index, now
        self._send(now, [self._msg(pia.PROTO_SYSTEM, bytes(payload), reliable=1)])

    @property
    def ready_to_start(self) -> bool:
        """Host: joined and the joiner has acked our first system data, so it may get our first game unit."""
        return self.state is State.JOINED and self.system_acked > pia.FIRST_INDEX

    def poll(self, now: int) -> None:
        if self.state in (State.IDLE, State.CLOSED):
            return
        if now - self.last_rx >= SILENCE_MS:
            log.info("VC session: the peer has been silent for %d s (%s)", SILENCE_MS // 1000, self.counts())
            self.state = State.CLOSED
            return
        if self.role is Role.HOST and not self.hello_answered and now - self.last_hello >= HELLO_MS:
            self.last_hello = now
            self._emit(now, pia.hello(int.from_bytes(os.urandom(4), "big")), broadcast=True)
            self.hellos_sent += 1
        if self.state is State.SETUP:
            if self.role is Role.JOINER and now - self.setup_start >= SETUP_FALLBACK_MS:
                if "profile" not in self.setup:
                    self._queue_profile()
                if "join" not in self.setup:
                    self._queue_join_request()
            self._pump_setup(now)
            return
        self._pump_setup(now)
        if now - self.last_ping >= PING_MS:
            self.last_ping = now
            self._send_ping(now)
        if self.role is Role.JOINER and now - self.last_clock_sync >= CLOCK_SYNC_MS:
            self.last_clock_sync = now
            self._send_clock_sync(now)
        if self.role is Role.HOST:
            unacked = self.system_sent is not None and self.system_acked <= self.system_sent
            if unacked and now - self.last_system_send >= SYSTEM_RESEND_MS:
                self._send_system_data(now, self.system_sent)
            elif not unacked and now - self.last_system >= SYSTEM_DATA_MS:
                self.last_system = now
                self._send_system_data(now)
        if self.send_flushed > self.send_acked and now - self.last_resend >= UNIT_RESEND_MS \
                and now - self.sent_at.get(self.send_acked, now) >= UNIT_RESEND_MS:
            self.last_resend = now
            self._send_units(now, self.send_acked, min(self.send_flushed, self.send_acked + 2 * pia.UNIT_WINDOW))
        keepalive = 4000 if self.role is Role.HOST else 2000
        if now - self.last_tx >= keepalive:
            self._send(now, [self._msg(pia.PROTO_KEEPALIVE, b"", subtype=pia.SUBTYPE_KEEPALIVE)])

    def counts(self) -> str:
        """What the session heard and sent, for the log."""
        heard = ", ".join(f"{v} {k}" for k, v in self.frames_received.items()) or "no frames"
        return (f"state {self.state.value}; heard {heard}, {self.frames_rejected} rejected; "
                f"sent {self.hellos_sent} hello(s)")

    def leave(self, now: int) -> None:
        """Our end-of-session record on the system stream (a Gen 2 host waits for it)."""
        payload = bytearray(36)
        payload[1], payload[3] = 0x03, 0x0C
        struct.pack_into(">ii", payload, 8, pia.FIRST_INDEX, self.system_next)
        payload[24], payload[35] = 0x10, 0x01
        self._send(now, [self._msg(pia.PROTO_SYSTEM, bytes(payload), reliable=1)])
