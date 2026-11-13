"""The little HTTP/1.x the DS speaks, for the NAS and DLS1 stand-ins (docs/ds_wifi.md).

The DS's DWC client sends a plain ``POST`` with ``application/x-www-form-urlencoded`` and a Content-Length, one
request per connection, and reads a Content-Length of its own; nothing here needs keep-alive, chunking or a request
body larger than a few kilobytes. A TLS connection from :mod:`pokeuds.wfc.tls` has ``recv`` and ``send`` rather than
a real socket, so the parsing and writing are done over those two calls and both a socket and a TLSConnection work.

Two versions matter (docs/ds_wifi.md, "HTTP"): the NAS client rejects HTTP/1.0 as bad data (error 25101), while the
DLS1 download client was tested against HTTP/1.0.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from dataclasses import dataclass, field

MAX_HEAD = 64 * 1024
MAX_BODY = 1024 * 1024
REASONS = {200: "OK", 204: "No Content", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
           405: "Method Not Allowed", 500: "Internal Server Error"}


class BadRequest(ValueError):
    """A request that cannot be read: malformed, too large, or cut off."""


@dataclass
class Request:
    method: str
    path: str
    query: dict
    headers: dict
    body: bytes
    client: str

    @property
    def host(self) -> str:
        """The Host header's name, lower-case, without its port: which of the three servers was asked."""
        return self.headers.get("host", "").split(":")[0].lower()

    def form(self) -> dict:
        """A URL-encoded POST body (or a GET's query) as single values. NAS values are decoded in
        :func:`pokeuds.wfc.nas.decode_form` instead: they are Nintendo's flavour of base64."""
        if self.method == "POST":
            text = self.body.decode("latin-1")
        else:
            text = urllib.parse.urlencode(self.query)
        parsed = urllib.parse.parse_qs(text, keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items()}


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict = field(default_factory=dict)
    version: str = "1.0"                  # DLS1 is answered 1.0 (tested); NAS 1.1 (error 25101)

    def encode(self) -> bytes:
        headers = {"Content-Type": "text/plain", **self.headers}
        headers["Content-Length"] = str(len(self.body))
        headers["Connection"] = "close"
        lines = [f"HTTP/{self.version} {self.status} {REASONS.get(self.status, 'Unknown')}"]
        lines += [f"{key}: {value}" for key, value in headers.items()]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + self.body


def _send_all(conn, data: bytes) -> None:
    """Write everything: TLSConnection.send may take only part of it (a plain socket has sendall)."""
    sendall = getattr(conn, "sendall", None)
    if sendall is not None:
        sendall(data)
        return
    sent = 0
    while sent < len(data):
        sent += conn.send(data[sent:])


def _recv(conn, size: int = 4096) -> bytes:
    try:
        data = conn.recv(size)
    except (OSError, ValueError):
        return b""
    return data or b""


def read_request(conn, client: str = "?", timeout: float = 30.0) -> Request:
    """One request from ``conn``. Raises :class:`BadRequest` when it is not one, or the peer went away."""
    settimeout = getattr(conn, "settimeout", None)
    if settimeout is not None:
        settimeout(timeout)
    buffer = b""
    while b"\r\n\r\n" not in buffer:
        if len(buffer) > MAX_HEAD:
            raise BadRequest("the request header is too large")
        chunk = _recv(conn)
        if not chunk:
            raise BadRequest("the connection closed before the request was read")
        buffer += chunk
    head, _, rest = buffer.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    parts = (lines[0].split(" ", 2) + ["", ""])[:3]
    method, target, version = parts[0].upper(), parts[1], parts[2]
    if not method or not target:
        raise BadRequest("the request line is not METHOD TARGET HTTP/1.x")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise BadRequest(f"a header line has no colon: {line[:80]!r}")
        headers[key.strip().lower()] = value.strip()
    if headers.get("transfer-encoding", "").lower() not in ("", "identity"):
        raise BadRequest("chunked requests are not used by the DS")
    try:
        length = int(headers.get("content-length", "0") or 0)
    except ValueError:
        raise BadRequest("Content-Length is not a number") from None
    if length < 0 or length > MAX_BODY:
        raise BadRequest(f"Content-Length {length} is out of range")
    while len(rest) < length:
        chunk = _recv(conn)
        if not chunk:
            raise BadRequest("the connection closed in the request body")
        rest += chunk
    body, extra = rest[:length], rest[length:]
    if extra:
        # The DS sends one request per connection; anything after the body is not ours to answer.
        pass
    parsed = urllib.parse.urlsplit(target)
    query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
    return Request(method, parsed.path or "/", query, headers, body, client)


def write_response(conn, response: Response) -> None:
    _send_all(conn, response.encode())


def serve_one(conn, app, client: str = "?", timeout: float = 30.0) -> None:
    """Read one request, let ``app`` answer it, write the answer. The caller closes ``conn``."""
    request = read_request(conn, client, timeout)
    write_response(conn, app(request))


def close(conn) -> None:
    try:
        conn.close()
    except (OSError, ValueError):
        pass


def peer_name(sock) -> str:
    try:
        return (sock.getpeername() or ("?", 0))[0]
    except (OSError, ValueError):
        return "?"


def free_port_error(port: int, exc: OSError) -> str:
    """A sentence for a bind that failed, naming the usual Windows culprits."""
    if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (10013,):
        return (f"port {port} is not allowed (another program or policy holds it); PokeUDS needs it for the DS. "
                "On Windows, allow PokeUDS through the firewall, and stop Internet Connection Sharing or Hyper-V's "
                "NAT if they hold port 53.")
    if getattr(exc, "winerror", None) in (10048,) or exc.errno in (48, 98, 10048):
        return (f"port {port} is already in use. Another server, Internet Connection Sharing or Hyper-V may hold "
                f"it; close it, or choose another port with --dns-port/--http-port/--https-port.")
    return f"cannot listen on port {port}: {exc}"


def local_address() -> str:
    """This computer's LAN address, as a DS on the same network should see it (no packet is actually sent).

    The host's own names are asked first, preferring an RFC 1918 address over a VPN's or a benchmark range; then the
    interface the default route uses. ``--ip`` overrides this when the guess is wrong, which is common with a VPN.
    """
    lan = [ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("10.0.0.0/8"),
           ipaddress.ip_network("172.16.0.0/12")]
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            parsed = ipaddress.ip_address(address)
            if not (parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified):
                rank = next((i for i, network in enumerate(lan) if parsed in network), len(lan))
                found.append((rank, address))
    except OSError:
        pass
    if found:
        return min(found)[1]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()
