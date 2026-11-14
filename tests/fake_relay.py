"""A Nostr relay for the tests: the store a relay keeps, a pool over it, and the same store behind a real WebSocket.

``Store`` does what the GTS relies on from a relay: it checks signatures, keeps the newest addressable event per
key and ``d`` (kinds 30000-39999), drops what a deletion (kind 5) of the same key names, forgets expired events
(NIP-40) and answers filters. ``FakePool`` has ``relays.Pool``'s interface over one store, for tests with no
sockets. ``serve`` runs the store as a loopback WebSocket relay, to exercise the real client.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
import time

from pokeuds.online import relays


class Store:
    def __init__(self, clock=time.time) -> None:
        self.events: dict[str, dict] = {}
        self.clock = clock
        self.lock = threading.Lock()
        self.refuse = False             # a relay that takes nothing
        self.live: list[tuple[object, str, list[dict]]] = []    # open subscriptions: (connection, id, filters)

    def add(self, event: dict) -> bool:
        if self.refuse or not relays.valid(event):
            return False
        with self.lock:
            kind = event["kind"]
            if kind == 5:               # NIP-09: the same key's events named by "e" go
                for ident in relays.tags(event, "e"):
                    if self.events.get(ident, {}).get("pubkey") == event["pubkey"]:
                        del self.events[ident]
            if 30000 <= kind < 40000:   # addressable: the newest per key, kind and d
                d = relays.tag(event, "d")
                for ident, old in list(self.events.items()):
                    if (old["pubkey"], old["kind"], relays.tag(old, "d")) == (event["pubkey"], kind, d):
                        if old["created_at"] > event["created_at"]:
                            return True
                        del self.events[ident]
            if not 20000 <= kind < 30000:           # ephemeral events are passed on, never stored
                self.events[event["id"]] = event
            live = [(conn, sub) for conn, sub, filters in self.live
                    if any(relays.matches(event, f) for f in filters)]
        for conn, sub in live:                      # every open subscription it matches hears it now
            conn.push(json.dumps(["EVENT", sub, event]))
        return True

    def subscribe(self, conn, sub: str, filters: list[dict]) -> None:
        with self.lock:
            self.live = [entry for entry in self.live if entry[:2] != (conn, sub)] + [(conn, sub, filters)]

    def forget(self, conn) -> None:
        with self.lock:
            self.live = [entry for entry in self.live if entry[0] is not conn]

    def query(self, flt: dict) -> list[dict]:
        now = self.clock()
        with self.lock:
            found = [e for e in self.events.values() if relays.matches(e, flt)
                     and not ((expires := relays.tag(e, "expiration")) and int(expires) <= now)]
        found.sort(key=lambda e: e["created_at"], reverse=True)
        return found[:flt.get("limit", len(found))]


class FakePool:
    """``relays.Pool`` over one store: what was published and asked is kept for the tests to look at."""

    def __init__(self, store: Store | None = None) -> None:
        self.store = store or Store()
        self.published: list[dict] = []
        self.filters: list[dict] = []
        self.down = False               # no relay answers

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass

    def publish(self, event: dict) -> int:
        if self.down:
            return 0
        self.published.append(event)
        return 1 if self.store.add(event) else 0

    def fetch(self, flt: dict, timeout: float = 0.0) -> list[dict]:
        self.filters.append(flt)
        if self.down:
            return []
        return sorted(self.store.query(flt), key=lambda e: (e["created_at"], e["id"]))

    def reached(self) -> int:
        return 0 if self.down else 1

    def problems(self) -> list[str]:
        return ["fake: down"] if self.down else []

    def close(self) -> None:
        pass


# -- the store behind a WebSocket -----------------------------------------------------------------

def _read(conn: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = conn.recv(count - len(data))
        if not chunk:
            raise OSError("closed")
        data += chunk
    return data


def _send(conn: socket.socket, text: str, pieces: int = 1, opcode: int = 0x1) -> None:
    """A server frame (unmasked); ``pieces`` > 1 fragments the message, as RFC 6455 allows."""
    payload = text.encode()
    size = max(1, -(-len(payload) // pieces))
    chunks = [payload[i:i + size] for i in range(0, len(payload), size)] or [b""]
    for i, chunk in enumerate(chunks):
        first = (0x80 if i == len(chunks) - 1 else 0) | (opcode if i == 0 else 0)
        head = bytes([first]) + (bytes([len(chunk)]) if len(chunk) < 126 else bytes([126]) + struct.pack(">H", len(chunk))
                                 if len(chunk) < 65536 else bytes([127]) + struct.pack(">Q", len(chunk)))
        conn.sendall(head + chunk)


class _Client:
    """One connection to the relay; ``push`` sends from any thread."""

    def __init__(self, conn: socket.socket, fragment: bool) -> None:
        self.conn, self.fragment, self.lock = conn, fragment, threading.Lock()

    def push(self, text: str, opcode: int = 0x1) -> None:
        with self.lock:
            try:
                _send(self.conn, text, pieces=3 if self.fragment and opcode == 0x1 else 1, opcode=opcode)
            except OSError:
                pass


def _serve_one(conn: socket.socket, store: Store, fragment: bool) -> None:
    client = _Client(conn, fragment)
    try:
        head = b""
        while b"\r\n\r\n" not in head:
            head += conn.recv(4096)
        key = next(line.split(":", 1)[1].strip() for line in head.decode().split("\r\n")
                   if line.lower().startswith("sec-websocket-key"))
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode())
        client.push("", opcode=0x9)                         # a ping first: the client answers and carries on
        while True:
            first, second = _read(conn, 2)
            size = second & 0x7F
            if size == 126:
                size = struct.unpack(">H", _read(conn, 2))[0]
            elif size == 127:
                size = struct.unpack(">Q", _read(conn, 8))[0]
            mask = _read(conn, 4)
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(_read(conn, size)))
            if first & 0x0F == 0x8:
                return
            if first & 0x0F != 0x1:
                continue
            message = json.loads(payload.decode())
            if message[0] == "EVENT":
                ok = store.add(message[1])
                client.push(json.dumps(["OK", message[1].get("id", ""), ok, "" if ok else "invalid: refused"]))
            elif message[0] == "REQ":
                filters = message[2:]
                store.subscribe(client, message[1], filters)
                stored = {e["id"]: e for f in filters for e in store.query(f)}
                for event in stored.values():
                    client.push(json.dumps(["EVENT", message[1], event]))
                client.push(json.dumps(["EOSE", message[1]]))
            elif message[0] == "CLOSE":
                with store.lock:
                    store.live = [e for e in store.live if e[:2] != (client, message[1])]
    except (OSError, ValueError, StopIteration, IndexError):
        pass
    finally:
        store.forget(client)
        conn.close()


def serve(store: Store, fragment: bool = False):
    """Run the store as a relay on a loopback port. -> (ws:// address, a function that stops it)"""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    stopped = threading.Event()

    def accept() -> None:
        while not stopped.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=_serve_one, args=(conn, store, fragment), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()

    def stop() -> None:
        stopped.set()
        server.close()

    return f"ws://127.0.0.1:{server.getsockname()[1]}", stop
