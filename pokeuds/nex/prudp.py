"""PRUDP V1, the reliable-UDP transport of NEX, as Pokemon X/Y and ORAS run it over UDS.

Every UDS payload of a Generation 6 trade is one PRUDP V1 packet behind a 16-byte header from Pia's
local layer (``wrap``/``unwrap``)::

    02 01 <src node> <dst node> 01 00 <length u16 LE> 00*8 | EA D0 <PRUDP V1 packet>

The packet layout is NEX's (kinnay's NintendoClients, ``nintendo/nex/prudp.py``); what differs is
the signature. The game signs every packet with NEX's ``MD5ChecksumWithKey`` and a fixed 8-byte key,
``01 02 03 04 05 06 07 08`` (found in the game's code: the NEX setup stores it as the key of the
checksum the P2P streams use), over::

    header[4:12] | 00 00 00 00 | the receiver's connection signature | options | payload | key

SYN and SYN-ACK carry no connection signature in the digest. A node's connection signature is a
constant of its node id (``CONNECTION_SIGNATURE``); peers never check it, they only feed it into
the digest. Every packet of five captured X sessions (both roles, about 2,400 packets) verifies
with this.

Each side opens its own connection to the other (two SYN/CONNECT handshakes), then numbers all its
reliable packets in one sequence (CONNECT is 1, data from 2) and its unreliable ones in another,
starting at the value its CONNECT (client) or CONNECT-ACK (host) declared. Reliable packets are
acknowledged with multi-acks: ``[0][n][u16 all up to][n x u16 more]``. Flag 0x80 marks the game's
own stream (gflnet) apart from Net-Z's.
"""

from __future__ import annotations

import hashlib
import logging
import os
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

KEY = bytes(range(1, 9))
CONNECTION_SIGNATURE = {
    1: bytes.fromhex("29d8ab2bb34987a40c6e4f68e383847d"),
    2: bytes.fromhex("9dca285364b71c1094c8e74f87284c53"),
}
MAGIC = b"\xEA\xD0"
PORT = 0x11                      # Source and destination: port 1, type 1 (VPort), both directions.
SUPPORT = 2                      # OPTION_SUPPORT as the game sends it (minor version 2, no functions).
FRAGMENT_SIZE = 1300             # The game splits larger messages at this payload size.

SYN, CONNECT, DATA, DISCONNECT, PING = 0, 1, 2, 3, 4
F_ACK, F_RELIABLE, F_NEED_ACK, F_HAS_SIZE = 0x001, 0x002, 0x004, 0x008
F_GAME = 0x080                   # The game's stream (gflnet); Net-Z's messages go without it.
F_MULTI_ACK = 0x200

OPT_SUPPORT, OPT_CONNECTION_SIG, OPT_FRAGMENT, OPT_UNRELIABLE_SEQ, OPT_MAX_SUBSTREAM = 0, 1, 2, 3, 4

PIA_HEADER = 16


def wrap(src_node: int, dst_node: int, packet: bytes) -> bytes:
    return bytes([2, 1, src_node, dst_node, 1, 0]) + struct.pack("<H", len(packet)) + bytes(8) + packet


def unwrap(payload: bytes) -> tuple[int, int, bytes]:
    if len(payload) < PIA_HEADER + 2 or payload[:2] != b"\x02\x01" or payload[PIA_HEADER:PIA_HEADER + 2] != MAGIC:
        raise ValueError("not a Pia-local PRUDP payload")
    size = struct.unpack_from("<H", payload, 6)[0]
    return payload[2], payload[3], payload[PIA_HEADER:PIA_HEADER + size]


@dataclass
class Packet:
    type: int
    flags: int
    session: int = 0
    seq: int = 0
    options: bytes = b""
    payload: bytes = b""
    substream: int = 0
    signature: bytes = b""

    @property
    def fragment(self) -> int:
        return self.options[2] if self.type == DATA and len(self.options) >= 3 else 0

    def option(self, ident: int) -> bytes | None:
        i = 0
        while i + 2 <= len(self.options):
            kind, size = self.options[i], self.options[i + 1]
            if kind == ident:
                return self.options[i + 2:i + 2 + size]
            i += 2 + size
        return None


def _header(p: Packet) -> bytes:
    return struct.pack("<BBHBBHBBH", 1, len(p.options), len(p.payload), PORT, PORT, p.type | (p.flags << 4),
                       p.session, p.substream, p.seq)


def signature(p: Packet, receiver_signature: bytes) -> bytes:
    sig = b"" if p.type == SYN else receiver_signature
    return hashlib.md5(_header(p)[4:] + bytes(4) + sig + p.options + p.payload + KEY).digest()


def encode(p: Packet, receiver_signature: bytes) -> bytes:
    p.signature = signature(p, receiver_signature)
    return MAGIC + _header(p) + p.signature + p.options + p.payload


def decode(data: bytes) -> Packet:
    if len(data) < 30 or data[:2] != MAGIC or data[2] != 1:
        raise ValueError("not a PRUDP V1 packet")
    _, optlen, size, _src, _dst, type_flags, session, substream, seq = struct.unpack_from("<BBHBBHBBH", data, 2)
    if len(data) < 30 + optlen + size:
        raise ValueError("truncated PRUDP packet")
    return Packet(type_flags & 0xF, type_flags >> 4, session, seq, data[30:30 + optlen],
                  data[30 + optlen:30 + optlen + size], substream, data[14:30])


def handshake_options(connection_signature: bytes, unreliable_seq: int | None = None) -> bytes:
    out = struct.pack("<BBI", OPT_SUPPORT, 4, SUPPORT) + bytes([OPT_CONNECTION_SIG, 16]) + connection_signature
    if unreliable_seq is not None:
        out += struct.pack("<BBH", OPT_UNRELIABLE_SEQ, 2, unreliable_seq)
    return out + bytes([OPT_MAX_SUBSTREAM, 1, 0])


def data_options(fragment: int = 0) -> bytes:
    return bytes([OPT_FRAGMENT, 1, fragment])


def multi_ack(base: int, more: list[int]) -> bytes:
    return struct.pack("<BBH", 0, len(more), base) + b"".join(struct.pack("<H", s) for s in more)


def parse_multi_ack(payload: bytes) -> tuple[int, list[int]]:
    _, count, base = struct.unpack_from("<BBH", payload)
    return base, [struct.unpack_from("<H", payload, 4 + 2 * i)[0] for i in range(count)]


@dataclass
class _Unacked:
    data: bytes
    sent: float
    tries: int = 1


@dataclass
class Message:
    stream: str                 # "netz", "game" or "unreliable"
    payload: bytes


@dataclass
class Endpoint:
    """Our PRUDP side towards one peer node: both connections, reliability, pings, fragments.

    ``send`` takes a whole UDS payload (Pia header included). ``feed`` takes one received payload
    and returns the messages it completed; ``tick`` retransmits and pings. Thread safe.
    """

    send: Callable[[bytes], None]
    node: int
    peer: int
    client: bool                       # The joiner opens first and declares its unreliable start in CONNECT.
    clock: Callable[[], float] = time.monotonic
    retransmit_after: float = 0.3
    max_tries: int = 25
    ping_every: float = 4.0
    session_id: int = field(default_factory=lambda: os.urandom(1)[0] or 1)
    unreliable_seq: int = field(default_factory=lambda: struct.unpack("<H", os.urandom(2))[0])

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._my_sig = CONNECTION_SIGNATURE[self.node]
        self._peer_sig = CONNECTION_SIGNATURE[self.peer]
        self._next_seq = 2
        self._unacked: dict[int, _Unacked] = {}
        self._received: set[int] = set()
        self._delivered = 1                      # Highest reliable seq handed up (CONNECT is 1).
        self._pending: dict[int, Packet] = {}    # Reliable packets waiting for a gap to fill.
        self._fragments: dict[bool, list[bytes]] = {False: [], True: []}
        self._ping_seq = 0
        self._next_ping = 0.0
        self.outbound = "idle"                   # idle, syn, connect, open
        self.inbound = False                     # The peer's connection to us is open.
        self.closed = False
        self.close_reason = ""                   # "disconnect" (the peer said so) or "timeout" (no acks)
        self.peer_session = 0
        self._connect_payload = b""

    # -- sending ----------------------------------------------------------------------------------

    def _emit(self, p: Packet) -> bytes:
        raw = wrap(self.node, self.peer, encode(p, self._peer_sig))
        self.send(raw)
        return raw

    def open(self, connect_payload: bytes = bytes(12)) -> None:
        """Start our connection to the peer (SYN; CONNECT follows on the SYN-ACK)."""
        with self._lock:
            self._connect_payload = connect_payload
            self.outbound = "syn"
            self._unacked[0] = _Unacked(self._emit(Packet(SYN, F_NEED_ACK | F_HAS_SIZE, 0, 0,
                                                          handshake_options(bytes(16)))), self.clock())

    def send_reliable(self, payload: bytes, game: bool = False) -> None:
        with self._lock:
            chunks = [payload[i:i + FRAGMENT_SIZE] for i in range(0, len(payload), FRAGMENT_SIZE)] or [b""]
            flags = F_RELIABLE | F_NEED_ACK | F_HAS_SIZE | (F_GAME if game else 0)
            for index, chunk in enumerate(chunks):
                fragment = 0 if index == len(chunks) - 1 else index + 1
                seq = self._next_seq
                self._next_seq = (self._next_seq + 1) & 0xFFFF
                raw = self._emit(Packet(DATA, flags, self.session_id, seq, data_options(fragment), chunk))
                self._unacked[seq] = _Unacked(raw, self.clock())

    def send_unreliable(self, payload: bytes) -> None:
        with self._lock:
            self._emit(Packet(DATA, F_HAS_SIZE, self.session_id, self.unreliable_seq, data_options(), payload))
            self.unreliable_seq = (self.unreliable_seq + 1) & 0xFFFF

    def disconnect(self) -> None:
        with self._lock:
            if not self.closed:
                for _ in range(2):
                    self._emit(Packet(DISCONNECT, F_HAS_SIZE, self.session_id, 0))
                self.closed, self.close_reason = True, "we left"

    # -- receiving --------------------------------------------------------------------------------

    def feed(self, payload: bytes) -> list[Message]:
        try:
            _src, _dst, raw = unwrap(payload)
            p = decode(raw)
        except (ValueError, struct.error) as exc:
            log.debug("dropping a non-PRUDP payload: %s", exc)
            return []
        if p.signature != signature(p, self._my_sig):
            log.warning("PRUDP packet with a bad signature (type %d, seq %d) dropped", p.type, p.seq)
            return []
        if log.isEnabledFor(logging.DEBUG) and p.type != PING:
            log.debug("PRUDP in: type %d flags %#x seq %d, %d B", p.type, p.flags, p.seq, len(p.payload))
        with self._lock:
            return self._handle(p)

    def _handle(self, p: Packet) -> list[Message]:
        acked = bool(p.flags & F_ACK)
        if p.type == SYN:
            if acked:
                self._unacked.pop(0, None)
                if self.outbound == "syn":
                    self.outbound = "connect"
                    unrel = self.unreliable_seq if self.client else None
                    self._unacked[1] = _Unacked(self._emit(Packet(
                        CONNECT, F_RELIABLE | F_NEED_ACK | F_HAS_SIZE, self.session_id, 1,
                        handshake_options(self._my_sig, unrel), self._connect_payload)), self.clock())
            else:
                self._emit(Packet(SYN, F_ACK | F_HAS_SIZE, 0, 0, handshake_options(self._my_sig)))
            return []
        if p.type == CONNECT:
            if acked:
                self._unacked.pop(1, None)
                self.outbound = "open"
            else:
                self.peer_session = p.session
                self.inbound = True
                unrel = None if self.client else self.unreliable_seq
                self._emit(Packet(CONNECT, F_ACK | F_HAS_SIZE, self.session_id, 1,
                                  handshake_options(bytes(16), unrel)))
            return []
        if p.type == PING:
            if not acked:
                self._emit(Packet(PING, F_ACK | F_HAS_SIZE, self.session_id, p.seq))
            return []
        if p.type == DISCONNECT:
            log.info("the peer sent a PRUDP disconnect (our connection: %s)", self.outbound)
            self.closed, self.close_reason = True, "disconnect"
            return []
        if p.type != DATA:
            return []
        if p.flags & F_MULTI_ACK:
            base, more = parse_multi_ack(p.payload)
            for seq in list(self._unacked):
                if seq >= 2 and (seq <= base or seq in more):
                    del self._unacked[seq]
            return []
        if acked:
            self._unacked.pop(p.seq, None)
            return []
        if not p.flags & F_RELIABLE:
            return [Message("unreliable", p.payload)]
        self._received.add(p.seq)
        self._send_ack()
        if p.seq <= self._delivered or p.seq in self._pending:
            return []
        self._pending[p.seq] = p
        out = []
        while self._delivered + 1 in self._pending:
            self._delivered += 1
            q = self._pending.pop(self._delivered)
            game = bool(q.flags & F_GAME)
            self._fragments[game].append(q.payload)
            if q.fragment == 0:
                out.append(Message("game" if game else "netz", b"".join(self._fragments[game])))
                self._fragments[game] = []
        return out

    def _send_ack(self) -> None:
        base = self._delivered
        while base + 1 in self._received:
            base += 1
        more = sorted(s for s in self._received if s > base)[:16]
        self._received = {s for s in self._received if s > base}
        self._received.add(base)
        self._emit(Packet(DATA, F_MULTI_ACK | F_HAS_SIZE, self.session_id, 0, data_options(), multi_ack(base, more)))

    # -- timers -----------------------------------------------------------------------------------

    def tick(self) -> None:
        now = self.clock()
        with self._lock:
            if self.closed:
                return
            for seq, entry in list(self._unacked.items()):
                if now - entry.sent >= self.retransmit_after:
                    if entry.tries >= self.max_tries:
                        log.warning("PRUDP packet %d never acknowledged; the peer is gone", seq)
                        self.closed, self.close_reason = True, "timeout"
                        return
                    self.send(entry.data)
                    entry.sent, entry.tries = now, entry.tries + 1
            if self.outbound == "open" and now >= self._next_ping:
                if self._next_ping:
                    self._ping_seq = (self._ping_seq + 1) & 0xFFFF or 1
                    self._emit(Packet(PING, F_NEED_ACK | F_HAS_SIZE, self.session_id, self._ping_seq))
                self._next_ping = now + self.ping_every

    @property
    def open_both(self) -> bool:
        return self.outbound == "open" and self.inbound

    @property
    def idle(self) -> bool:
        """Nothing of ours waits for an acknowledgement."""
        with self._lock:
            return not self._unacked
