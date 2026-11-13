"""The DS Wi-Fi Mystery Gift server: DNS, conntest, NAS and DLS1 in one process (docs/ds_wifi.md).

    python -m pokeuds wfc serve --cert clientca.pem --key clientcakey.pem --gifts dlc

The DS's DNS is pointed at this computer (or at the board, in the tunnel design). Every Nintendo name resolves
here, the connection test is answered, the NAS login is accepted, and DLS1 sends a gift. The NAS and DLS1 traffic is
SSL 3.0 with RC4, using a certificate signed with the player's own Wii NWC certificate: the "nds-constrain't" flaw
lets the DS follow that chain to Nintendo's root (docs/ds_wifi.md, "The certificate").

This computer must be reachable from the DS: the same LAN, an open (or WEP) access point, and the firewall allowing
TCP 80 and 443 and UDP/TCP 53. The DS cannot join a WPA2 network, and neither can a DSi or 3DS running a DS game.
"""

from __future__ import annotations

import logging
import socketserver
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import certs, http
from .dls1 import DLS1
from .dns import DEFAULT_SUFFIXES, Resolver, Server as DNSServer
from .nas import NAS, Sessions

log = logging.getLogger(__name__)


@dataclass
class Config:
    address: str = "0.0.0.0"                  # what the servers bind to
    answer_ip: str = ""                       # what DNS answers with; "" finds the LAN address
    dns_port: int = 53
    http_port: int = 80
    https_port: int = 443
    gifts: Path = Path("dlc")
    cache: Path = Path("wfc-certs")
    cert: Path | None = None                  # the Wii pair, or None to look in cache
    key: Path | None = None
    cn: str = certs.DEFAULT_CN
    gift: str | None = None                   # always serve this file, ignoring the date
    dns: bool = True
    http: bool = True
    https: bool = True
    dns_all: bool = False                     # answer every name, not only the Nintendo suffixes
    dns_upstream: str | None = None           # "8.8.8.8" or "8.8.8.8:53"; None answers other names NXDOMAIN
    dns_suffixes: tuple[str, ...] = DEFAULT_SUFFIXES

    def dls_host(self) -> str:
        return "dls1.nintendowifi.net"


@dataclass
class Bound:
    dns: int = 0
    http: int = 0
    https: int = 0
    address: str = ""
    certificate: str = ""
    issuer: str = ""
    chain: list[str] = field(default_factory=list)


class _Threading(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, handler, app, tls=None):
        self.app = app
        self.tls = tls
        super().__init__(address, handler)


class _HTTPHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client = self.client_address[0]
        log.info("HTTP connection from %s", client)
        try:
            request = http.read_request(self.request, client)
            response = self.server.app(request)
            http.write_response(self.request, response)
        except http.BadRequest as exc:
            log.debug("HTTP from %s: %s", client, exc)
        except (OSError, ValueError) as exc:
            log.debug("HTTP from %s: %r", client, exc)
        finally:
            http.close(self.request)


class _TLSHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client = self.client_address[0]
        log.info("TLS connection from %s", client)
        try:
            self.request.settimeout(30.0)
        except OSError:
            pass
        try:
            conn = self.server.tls.wrap(self.request)
        except Exception as exc:                    # a failed handshake is the DS giving up, not an error here
            log.info("TLS handshake with %s failed: %s", client, exc)
            http.close(self.request)
            return
        try:
            from . import tls as tls_mod

            log.info("TLS from %s: %s", client, tls_mod.describe(conn))
            request = http.read_request(conn, client)
            response = self.server.app(request)
            http.write_response(conn, response)
        except http.BadRequest as exc:
            log.debug("TLS HTTP from %s: %s", client, exc)
        except (OSError, ValueError) as exc:
            log.debug("TLS from %s: %r", client, exc)
        finally:
            http.close(conn)


class WFCServer:
    """All four listeners (DNS UDP/TCP, HTTP, HTTPS) and the request log."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.bound = Bound()
        self.identity: certs.ServerIdentity | None = None
        self.sessions = Sessions()
        self.nas = NAS(self.sessions, config.dls_host())
        self.dls = DLS1(config.gifts, self.sessions, config.gift, config.dls_host())
        self.dns: DNSServer | None = None
        self._http: _Threading | None = None
        self._https: _Threading | None = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    # -- setup ----------------------------------------------------------------------------------------------------

    def prepare(self) -> None:
        """Load the Wii pair, make the server certificate and start the protocol objects (no sockets yet)."""
        config = self.config
        pair_paths = ((config.cert, config.key) if config.cert and config.key
                      else certs.find_pair(config.cache))
        if not pair_paths or not all(pair_paths):
            raise FileNotFoundError(
                f"no Wii certificate pair (clientca.pem and clientcakey.pem) in {config.cache}. "
                "Dolphin extracts them from IOS13 with Tools > Perform Online System Update (see docs/ds_wifi.md), "
                "or pass --cert and --key.")
        pair = certs.load_pair(*pair_paths)
        if not pair.dolphin:
            log.warning("this is not the pair Dolphin checks by hash; its subject is %r, issuer %r, %d-bit key",
                        pair.name, pair.issuer_name, pair.key.size_in_bits())
        self.identity = certs.server_identity(pair, config.cache, config.cn)
        self.bound.chain = [certs.Certificate.common_name(certs.Certificate.parse(der).subject)
                            for der in self.identity.chain]
        self.bound.certificate = self.identity.cn
        self.bound.issuer = pair.issuer_name
        log.info("certificate for %r, chain: %s -> %s", self.identity.cn, " -> ".join(self.bound.chain),
                 pair.issuer_name)

    def _route(self, request: http.Request):
        """Which server the request is for: by Host, else by path."""
        host = request.host
        if host.startswith("dls1") or request.path == "/download":
            return self.dls.handle(request)
        if host.startswith(("nas", "conntest")) or request.path in ("/ac", "/pr"):
            return self.nas.handle(request)
        if request.method == "GET":
            return self.nas.handle(request)                 # conntest without a Host header
        log.info("HTTP %s %s%s: no server for it", request.method, host, request.path)
        return http.Response(404, b"", version="1.0")

    # -- sockets --------------------------------------------------------------------------------------------------

    def start(self) -> Bound:
        config = self.config
        if self.identity is None:
            self.prepare()
        if config.answer_ip:
            self.bound.address = config.answer_ip
        else:
            self.bound.address = http.local_address()
        if config.dns:
            upstream = None
            if config.dns_upstream:
                host, _, port = config.dns_upstream.partition(":")
                upstream = (host, int(port or 53))
            resolver = Resolver(self.bound.address, config.dns_suffixes, upstream, config.dns_all)
            self.dns = DNSServer(resolver, config.address, config.dns_port)
            try:
                self.dns.start()
            except OSError as exc:
                self.close()
                raise OSError(http.free_port_error(config.dns_port, exc)) from None
            self.bound.dns = self.dns.port
        if config.http:
            try:
                self._http = _Threading((config.address, config.http_port), _HTTPHandler, self._route)
            except OSError as exc:
                self.close()
                raise OSError(http.free_port_error(config.http_port, exc)) from None
            self.bound.http = self._http.server_address[1]
            self._threads.append(threading.Thread(target=self._http.serve_forever, name="wfc-http", daemon=True))
        if config.https:
            try:
                from .tls import Server as TLSServer
            except ImportError as exc:
                self.close()
                raise RuntimeError(str(exc)) from None
            try:
                self._https = _Threading((config.address, config.https_port), _TLSHandler, self._route,
                                         tls=TLSServer(self.identity))
            except OSError as exc:
                self.close()
                raise OSError(http.free_port_error(config.https_port, exc)) from None
            self.bound.https = self._https.server_address[1]
            self._threads.append(threading.Thread(target=self._https.serve_forever, name="wfc-https", daemon=True))
        for thread in self._threads:
            thread.start()
        if self._https is not None:
            log.info("HTTPS listening on %s:%d", config.address, self.bound.https)
        if self._http is not None:
            log.info("HTTP listening on %s:%d", config.address, self.bound.http)
        return self.bound

    def serve_forever(self, seconds: float = 0.0) -> None:
        """Block until ``seconds`` pass (0: until closed) or :meth:`close` is called."""
        deadline = time.monotonic() + seconds if seconds else None
        try:
            while not self._stop.is_set():
                if deadline is not None and time.monotonic() >= deadline:
                    break
                time.sleep(0.25)
        finally:
            self.close()

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self.dns is not None:
            self.dns.close()
            self.dns = None
        for server in (self._http, self._https):
            if server is not None:
                try:
                    server.shutdown()
                except (OSError, RuntimeError):
                    pass
                try:
                    server.server_close()
                except OSError:
                    pass
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._http = self._https = None


def summary(bound: Bound, config: Config) -> str:
    """What to do next, in the order a player does it."""
    address = bound.address or config.answer_ip or "this computer"
    chain = " -> ".join([*bound.chain, bound.issuer]) if bound.issuer else " -> ".join(bound.chain)
    lines = [
        "DS Wi-Fi Mystery Gift server running.",
        f"  DNS:        {address}:{bound.dns}" if bound.dns else "  DNS:        off",
        f"  conntest:   http://{address}:{bound.http}/" if bound.http else "  conntest:   off",
        (f"  NAS/DLS1:   https://{address}:{bound.https}/ (SSL 3.0, RC4)" if bound.https
         else f"  NAS/DLS1:   http://{address}:{bound.http}/ (no TLS: testing only)" if bound.http
         else "  NAS/DLS1:   off"),
        f"  certificate: {bound.certificate} ({chain})",
        f"  gifts:      {config.gifts}",
        "",
        "On the DS: Nintendo WFC Settings > Connection Settings > your connection > Auto-obtain DNS: No,",
        f"Primary DNS {address}, Secondary DNS {address} (both: a public secondary may answer for Nintendo's names).",
        "The DS must be on an open or WEP network, and this computer's firewall must allow UDP/TCP 53 and TCP 80/443.",
        "Then: Mystery Gift > Receive via Wi-Fi (Gen 4) or Receive via Nintendo WFC (Gen 5).",
        f"If the DS cannot reach {address}, find this computer's LAN address (ipconfig) and pass --ip with it.",
    ]
    return "\n".join(lines)
