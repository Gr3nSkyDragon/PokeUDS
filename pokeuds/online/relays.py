"""Nostr events, and a pool of relays to store and fetch them (NIP-01).

An event is a signed JSON object: ``id`` is the SHA-256 of ``[0, pubkey, created_at, kind, tags, content]`` and
``sig`` its BIP-340 signature. A relay stores events and returns those a filter matches. This client does the two
things the GTS needs, against several relays at once, since any one may be down or keep nothing:

- ``Pool.publish(event)``: send it to every relay; how many said OK.
- ``Pool.fetch(filter)``: ask every relay, until each says it has sent everything stored (EOSE); the events,
  checked (id and signature) and once each.

The WebSocket client is the little of RFC 6455 a relay conversation uses: the upgrade, masked text frames out,
text, ping and close frames in. ``POKEUDS_GTS_RELAYS`` (comma-separated ``wss://`` addresses) replaces the pool.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import socket
import ssl
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from . import schnorr

log = logging.getLogger(__name__)
# Relays that kept a classified listing and a gift wrap from a fresh key and served them back (pokeldn's docs/online.md,
# Stored events, tested there in 2026). Any one failing only makes a round slower.
DEFAULT_RELAYS = ("wss://relay.primal.net", "wss://nostr.oxtr.dev", "wss://nos.lol", "wss://nostr.mom",
                  "wss://offchain.pub", "wss://nostr-pub.wellorder.net", "wss://relay.nostr.net")
CONNECT_TIMEOUT = 8.0
REPLY_TIMEOUT = 8.0
RETRY_AFTER = 60.0              # s a relay that could not be reached is left alone, so a round does not wait on it again
FRAME_LIMIT = 1 << 20           # a relay message larger than this is not one of ours
EVENT_LIMIT = 16384             # bytes of JSON an event of ours may take


def relay_urls() -> tuple[str, ...]:
    urls = tuple(u.strip() for u in os.environ.get("POKEUDS_GTS_RELAYS", "").split(",") if u.strip())
    return urls or DEFAULT_RELAYS


# -- events ---------------------------------------------------------------------------------------

def event_id(event: dict) -> str:
    body = [0, event["pubkey"], event["created_at"], event["kind"], event["tags"], event["content"]]
    return hashlib.sha256(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def sign_event(secret: bytes, kind: int, content: str, tags: list[list[str]], created_at: int | None = None) -> dict:
    event = {"pubkey": schnorr.public_key(secret).hex(), "created_at": int(time.time() if created_at is None else created_at),
             "kind": kind, "tags": tags, "content": content}
    event["id"] = event_id(event)
    event["sig"] = schnorr.sign(secret, bytes.fromhex(event["id"])).hex()
    return event


def valid(event) -> bool:
    """A well-formed event whose id and signature are right. Everything a relay sends is checked with this."""
    try:
        if not isinstance(event, dict) or not isinstance(event.get("content"), str):
            return False
        if type(event.get("kind")) is not int or type(event.get("created_at")) is not int:
            return False
        tags = event.get("tags")
        if not isinstance(tags, list) or not all(isinstance(t, list) and t and all(isinstance(x, str) for x in t)
                                                 for t in tags):
            return False
        public, ident, sig = (bytes.fromhex(event[k]) for k in ("pubkey", "id", "sig"))
        return (len(json.dumps(event).encode()) <= EVENT_LIMIT and event_id(event) == event["id"]
                and schnorr.verify(public, ident, sig))
    except (KeyError, TypeError, ValueError):
        return False


def tag(event: dict, name: str) -> str | None:
    """The first value of the event's tag ``name``."""
    return next((t[1] for t in event.get("tags") or [] if len(t) >= 2 and t[0] == name), None)


def tags(event: dict, name: str) -> list[str]:
    return [t[1] for t in event.get("tags") or [] if len(t) >= 2 and t[0] == name]


def matches(event: dict, flt: dict) -> bool:
    """Whether a NIP-01 filter takes the event (what a relay does; the tests' relay and a last check on replies)."""
    if "ids" in flt and event["id"] not in flt["ids"]:
        return False
    if "authors" in flt and event["pubkey"] not in flt["authors"]:
        return False
    if "kinds" in flt and event["kind"] not in flt["kinds"]:
        return False
    if "since" in flt and event["created_at"] < flt["since"]:
        return False
    if "until" in flt and event["created_at"] > flt["until"]:
        return False
    for key, wanted in flt.items():
        if key.startswith("#") and not set(wanted) & set(tags(event, key[1:])):
            return False
    return True


# -- a WebSocket ----------------------------------------------------------------------------------

class WebSocket:
    """One client connection: ``send`` a text message, ``recv`` the next one (None at the timeout)."""

    def __init__(self, url: str, timeout: float = CONNECT_TIMEOUT) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("ws", "wss") or not parts.hostname:
            raise ValueError(f"{url} is not a ws:// or wss:// address")
        port = parts.port or (443 if parts.scheme == "wss" else 80)
        sock = socket.create_connection((parts.hostname, port), timeout=timeout)
        try:
            if parts.scheme == "wss":
                sock = _tls().wrap_socket(sock, server_hostname=parts.hostname)
            self.sock = sock
            self._buffer = b""
            self._message = b""                 # the pieces of a fragmented message so far
            self._lock = threading.Lock()
            key = base64.b64encode(os.urandom(16)).decode()
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            host = parts.hostname if port in (80, 443) else f"{parts.hostname}:{port}"
            sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: PokeUDS\r\n\r\n").encode())
            head = self._until(b"\r\n\r\n", timeout).decode("latin-1")
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            lines = head.split("\r\n")
            headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
            if " 101 " not in lines[0] + " " or headers.get("sec-websocket-accept") != accept:
                raise OSError(f"{url} refused the WebSocket: {lines[0][:80]}")
        except BaseException:
            sock.close()
            raise

    def _fill(self, deadline: float) -> bool:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        self.sock.settimeout(remaining)
        try:
            chunk = self.sock.recv(65536)
        except (socket.timeout, TimeoutError):
            return False
        if not chunk:
            raise OSError("the relay closed the connection")
        self._buffer += chunk
        return True

    def _until(self, mark: bytes, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while mark not in self._buffer:
            if len(self._buffer) > 65536 or not self._fill(deadline):
                raise OSError("no WebSocket answer")
        head, _, self._buffer = self._buffer.partition(mark)
        return head

    def _need(self, count: int, deadline: float) -> bool:
        """Have ``count`` bytes buffered, reading more until the deadline. Nothing is consumed."""
        while len(self._buffer) < count:
            if not self._fill(deadline):
                return False
        return True

    def _frame(self, opcode: int, payload: bytes) -> None:
        head = bytes([0x80 | opcode])
        size = len(payload)
        head += bytes([0x80 | size]) if size < 126 else (bytes([0x80 | 126]) + struct.pack(">H", size) if size < 65536
                                                          else bytes([0x80 | 127]) + struct.pack(">Q", size))
        mask = os.urandom(4)
        with self._lock:
            self.sock.settimeout(REPLY_TIMEOUT)
            self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, text: str) -> None:
        self._frame(0x1, text.encode())

    def recv(self, timeout: float) -> str | None:
        """The next text message, or None when ``timeout`` passes first. OSError when the connection ends."""
        deadline = time.monotonic() + timeout
        while True:
            # A frame is taken from the buffer only once it is whole, so a timeout loses nothing.
            if not self._need(2, deadline):
                return None
            first, second = self._buffer[0], self._buffer[1]
            opcode, size, masked = first & 0x0F, second & 0x7F, second & 0x80
            offset = 2
            if size == 126:
                if not self._need(4, deadline):
                    return None
                size, offset = struct.unpack(">H", self._buffer[2:4])[0], 4
            elif size == 127:
                if not self._need(10, deadline):
                    return None
                size, offset = struct.unpack(">Q", self._buffer[2:10])[0], 10
            if size > FRAME_LIMIT or len(self._message) + size > FRAME_LIMIT:
                raise OSError("a relay message is too large")
            body = offset + (4 if masked else 0)
            if not self._need(body + size, deadline):
                return None
            payload = self._buffer[body:body + size]
            if masked:
                mask = self._buffer[offset:offset + 4]
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            self._buffer = self._buffer[body + size:]
            if opcode == 0x8:
                raise OSError("the relay closed the connection")
            if opcode == 0x9:
                self._frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            self._message += payload            # a text frame, or the next piece of one
            if first & 0x80:                    # FIN: the message is whole
                text, self._message = self._message.decode("utf-8", "replace"), b""
                return text

    def close(self) -> None:
        try:
            self._frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


_context: ssl.SSLContext | None = None


def _tls() -> ssl.SSLContext:
    """The system's certificates, or certifi's where a packaged app has none of its own (a Mac bundle)."""
    global _context
    if _context is None:
        try:
            import certifi

            _context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            _context = ssl.create_default_context()
    return _context


# -- the pool -------------------------------------------------------------------------------------

class Relay:
    """One relay, connected when first used and again after it drops."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.ws: WebSocket | None = None
        self.lock = threading.Lock()
        self.failed = ""
        self.down_until = 0.0

    def _open(self) -> WebSocket:
        if self.ws is None:
            if time.monotonic() < self.down_until:
                raise OSError(self.failed or "unreachable")
            try:
                self.ws = WebSocket(self.url)
            except (OSError, ValueError):
                self.down_until = time.monotonic() + RETRY_AFTER
                raise
            self.failed = ""
        return self.ws

    def _drop(self, why: Exception) -> None:
        self.failed = str(why) or type(why).__name__
        log.debug("%s: %s", self.url, self.failed)
        if self.ws is not None:
            self.ws.close()
            self.ws = None

    def publish(self, event: dict) -> bool:
        with self.lock:
            try:
                ws = self._open()
                ws.send(json.dumps(["EVENT", event], separators=(",", ":"), ensure_ascii=False))
                deadline = time.monotonic() + REPLY_TIMEOUT
                while (left := deadline - time.monotonic()) > 0:
                    text = ws.recv(left)
                    if text is None:
                        break
                    reply = _json(text)
                    if isinstance(reply, list) and len(reply) >= 3 and reply[0] == "OK" and reply[1] == event["id"]:
                        if reply[2] is not True:
                            self.failed = str(reply[3]) if len(reply) > 3 else "refused"
                        return reply[2] is True
                self.failed = "no answer"
                return False
            except (OSError, ValueError) as exc:
                self._drop(exc)
                return False

    def fetch(self, flt: dict, timeout: float) -> list[dict]:
        with self.lock:
            sub = os.urandom(6).hex()
            found = []
            try:
                ws = self._open()
                ws.send(json.dumps(["REQ", sub, flt], separators=(",", ":")))
                deadline = time.monotonic() + timeout
                while (left := deadline - time.monotonic()) > 0:
                    text = ws.recv(left)
                    if text is None:
                        break
                    reply = _json(text)
                    if not isinstance(reply, list) or len(reply) < 2 or reply[1] != sub:
                        continue
                    if reply[0] == "EVENT" and len(reply) >= 3:
                        found.append(reply[2])
                    elif reply[0] in ("EOSE", "CLOSED"):
                        break
                ws.send(json.dumps(["CLOSE", sub]))
            except (OSError, ValueError) as exc:
                self._drop(exc)
            return found

    def close(self) -> None:
        with self.lock:
            if self.ws is not None:
                self.ws.close()
                self.ws = None


def _json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return None


class Pool:
    """Several relays, used together. ``with Pool() as pool:`` keeps the connections for a round and closes them."""

    def __init__(self, urls: tuple[str, ...] | list[str] | None = None) -> None:
        self.relays = [Relay(url) for url in (urls or relay_urls())]

    def __enter__(self) -> "Pool":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _each(self, call) -> list:
        if not self.relays:
            return []
        with ThreadPoolExecutor(max_workers=len(self.relays), thread_name_prefix="relay") as workers:
            return list(workers.map(call, self.relays))

    def publish(self, event: dict) -> int:
        """Send the event to every relay; -> how many took it."""
        return sum(self._each(lambda relay: relay.publish(event)))

    def fetch(self, flt: dict, timeout: float = REPLY_TIMEOUT) -> list[dict]:
        """Every stored event the filter matches, from all relays: valid ones, once each, oldest first."""
        seen: dict[str, dict] = {}
        for events in self._each(lambda relay: relay.fetch(flt, timeout)):
            for event in events:
                if valid(event) and matches(event, flt):
                    seen.setdefault(event["id"], event)
        return sorted(seen.values(), key=lambda e: (e["created_at"], e["id"]))

    def reached(self) -> int:
        """How many relays are connected now (after a publish or fetch)."""
        return sum(relay.ws is not None for relay in self.relays)

    def problems(self) -> list[str]:
        return [f"{relay.url}: {relay.failed}" for relay in self.relays if relay.failed and relay.ws is None]

    def check(self, timeout: float = REPLY_TIMEOUT) -> list[tuple[str, bool, str]]:
        """Ask each relay for one GTS event: -> (address, answered, how long it took or why not), one per relay."""
        def one(relay: Relay) -> tuple[str, bool, str]:
            started = time.monotonic()
            relay.fetch({"kinds": [30402], "#t": ["pokeuds-gts"], "limit": 1}, timeout)
            if relay.ws is not None:
                return relay.url, True, f"{(time.monotonic() - started) * 1000:.0f} ms"
            return relay.url, False, relay.failed or "no connection"

        return self._each(one)

    def close(self) -> None:
        for relay in self.relays:
            relay.close()
