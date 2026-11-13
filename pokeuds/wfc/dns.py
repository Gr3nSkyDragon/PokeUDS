"""The DNS half of the trick: the DS asks for Nintendo's names and is sent here (docs/ds_wifi.md, "DNS").

A retail DS whose Primary DNS is this computer resolves ``conntest``, ``nas`` and ``dls1.nintendowifi.net`` to it, so
no game patch is needed (Wiimmfi, Kaeru WFC and PokeGateway all work this way). Every name under the configured
suffixes gets an A record for ``answer_ip``; anything else is either forwarded to a real resolver or answered
NXDOMAIN, so the same DNS server can stay configured for the console's other lookups.

UDP is what the DS uses; TCP is served too because a DNS client retries over TCP after a truncated answer, and some
tools test it.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading

log = logging.getLogger(__name__)

QTYPE_A, QTYPE_AAAA, QCLASS_IN = 1, 28, 1
RCODE_NOERROR, RCODE_SERVFAIL, RCODE_NXDOMAIN = 0, 2, 3
DEFAULT_SUFFIXES = ("nintendowifi.net", "nintendo.co.jp")
FLAG_QR, FLAG_AA, FLAG_RD, FLAG_RA = 0x8000, 0x0400, 0x0100, 0x0080


def parse_question(packet: bytes) -> tuple[int, int, str, int, int, int]:
    """(id, flags, name, qtype, qclass, where the question ends) for the first question.

    Raises ValueError for anything that is not a plain query; the DS and every resolver send uncompressed names in
    the question, so compression there is refused rather than followed.
    """
    if len(packet) < 12:
        raise ValueError("short DNS packet")
    txid, flags, qdcount = struct.unpack(">HHH", packet[:6])
    if qdcount < 1:
        raise ValueError("DNS packet without a question")
    labels, at = [], 12
    while True:
        if at >= len(packet):
            raise ValueError("truncated DNS name")
        length = packet[at]
        at += 1
        if length == 0:
            break
        if length & 0xC0:
            raise ValueError("compressed DNS name in the question")
        if at + length > len(packet):
            raise ValueError("truncated DNS label")
        labels.append(packet[at:at + length].decode("ascii", "replace"))
        at += length
    if at + 4 > len(packet):
        raise ValueError("truncated DNS question")
    qtype, qclass = struct.unpack(">HH", packet[at:at + 4])
    return txid, flags, ".".join(labels).lower(), qtype, qclass, at + 4


def build_response(query: bytes, question_end: int, address: str | None = None, ttl: int = 300,
                   rcode: int = RCODE_NOERROR) -> bytes:
    """An answer to ``query``: one A record when ``address`` is given, else none (AAAA and other types)."""
    txid, flags = struct.unpack(">HH", query[:4])
    rd = flags & FLAG_RD
    out = struct.pack(">HHHHHH", txid, FLAG_QR | FLAG_AA | rd | FLAG_RA | (rcode & 0xF),
                      1, 1 if address else 0, 0, 0)
    out += query[12:question_end]
    if address:
        out += struct.pack(">HHHIH", 0xC00C, QTYPE_A, QCLASS_IN, ttl, 4) + socket.inet_aton(address)
    return out


def query(name: str, qtype: int = QTYPE_A, qclass: int = QCLASS_IN, txid: int = 0x1234) -> bytes:
    """A query packet, for tests and for checking a running server."""
    body = b"".join(bytes([len(label)]) + label.encode() for label in name.rstrip(".").split(".")) + b"\0"
    return struct.pack(">HHHHHH", txid, FLAG_RD, 1, 0, 0, 0) + body + struct.pack(">HH", qtype, qclass)


def answer_address(packet: bytes) -> str | None:
    """The IPv4 address in a response built here, for tests (None when it has no A record)."""
    try:
        _, _, _, qtype, _, end = parse_question(packet)
    except ValueError:
        return None
    if qtype != QTYPE_A or len(packet) < end + 16:
        return None
    return socket.inet_ntoa(packet[end + 12:end + 16])


class Resolver:
    """Answers the Nintendo names; forwards (or refuses) the rest."""

    def __init__(self, answer_ip: str, suffixes=DEFAULT_SUFFIXES, forward: tuple[str, int] | None = None,
                 catch_all: bool = False, ttl: int = 300) -> None:
        self.answer_ip = answer_ip
        self.suffixes = tuple(s.lower().strip(".") for s in suffixes)
        self.forward_to = forward
        self.catch_all = catch_all
        self.ttl = ttl

    def is_ours(self, name: str) -> bool:
        name = name.strip(".").lower()
        return any(name == suffix or name.endswith("." + suffix) for suffix in self.suffixes)

    def resolve(self, packet: bytes) -> bytes:
        try:
            _, _, name, qtype, qclass, end = parse_question(packet)
        except ValueError as exc:
            log.debug("ignoring a malformed DNS query: %s", exc)
            return b""
        ours = self.is_ours(name) or self.catch_all
        if ours:
            if qtype == QTYPE_A and qclass == QCLASS_IN:
                log.info("DNS %s -> %s", name, self.answer_ip)
                return build_response(packet, end, self.answer_ip, self.ttl)
            log.debug("DNS %s type %d -> no answer (the DS falls back to IPv4)", name, qtype)
            return build_response(packet, end)
        if self.forward_to:
            return self._forward(packet, end)
        log.debug("DNS %s -> NXDOMAIN (not a Nintendo name)", name)
        return build_response(packet, end, rcode=RCODE_NXDOMAIN)

    def _forward(self, packet: bytes, end: int) -> bytes:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3.0)
        try:
            sock.sendto(packet, self.forward_to)
            reply, _ = sock.recvfrom(4096)
            return reply
        except OSError as exc:
            log.debug("DNS forward to %s failed: %s", self.forward_to, exc)
            return build_response(packet, end, rcode=RCODE_SERVFAIL)
        finally:
            sock.close()


class Server:
    """UDP and TCP DNS on one port, in daemon threads."""

    def __init__(self, resolver: Resolver, bind: str = "0.0.0.0", port: int = 53) -> None:
        self.resolver = resolver
        self.bind = bind
        self.port = port
        self.udp: socket.socket | None = None
        self.tcp: socket.socket | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.udp.bind((self.bind, self.port))
        except OSError:
            self.udp.close()
            self.udp = None
            raise
        self.port = self.udp.getsockname()[1]
        self.udp.settimeout(1.0)
        self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.tcp.bind((self.bind, self.port))
            self.tcp.listen(8)
            self.tcp.settimeout(1.0)
        except OSError:
            self.udp.close()
            self.tcp.close()
            self.udp = self.tcp = None
            raise
        self._threads = [threading.Thread(target=self._udp_loop, name="wfc-dns-udp", daemon=True),
                         threading.Thread(target=self._tcp_loop, name="wfc-dns-tcp", daemon=True)]
        for thread in self._threads:
            thread.start()
        log.info("DNS listening on %s:%d (Nintendo names -> %s)", self.bind, self.port, self.resolver.answer_ip)

    def _udp_loop(self) -> None:
        assert self.udp is not None
        while not self._stop.is_set():
            try:
                packet, address = self.udp.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            reply = self.resolver.resolve(packet)
            if reply:
                try:
                    self.udp.sendto(reply, address)
                except OSError:
                    pass

    def _tcp_loop(self) -> None:
        assert self.tcp is not None
        while not self._stop.is_set():
            try:
                conn, address = self.tcp.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._tcp_conn, args=(conn, address), name="wfc-dns-tcp-conn",
                             daemon=True).start()

    def _tcp_conn(self, conn: socket.socket, address) -> None:
        try:
            conn.settimeout(15.0)
            while not self._stop.is_set():
                header = _recv_exact(conn, 2)
                if header is None:
                    return
                (length,) = struct.unpack(">H", header)
                packet = _recv_exact(conn, length)
                if packet is None:
                    return
                reply = self.resolver.resolve(packet)
                if not reply:
                    return
                conn.sendall(struct.pack(">H", len(reply)) + reply)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        self._stop.set()
        for sock in (self.udp, self.tcp):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        for thread in self._threads:
            thread.join(timeout=2.0)
        self.udp = self.tcp = None


def _recv_exact(conn: socket.socket, size: int) -> bytes | None:
    data = b""
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            return None
        data += chunk
    return data
