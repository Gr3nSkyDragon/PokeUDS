"""SSL 3.0 with RC4, the only TLS a DS knows (docs/ds_wifi.md, "SSL 3.0").

Python's ``ssl`` module (OpenSSL 3) refuses both, so the DS's connections are answered by tlslite-ng (pure Python,
LGPL-2.1), which still has SSL 3.0 and RC4 as choices to opt into. Each accepted socket is wrapped and handshaken
here; what comes out reads and writes like a socket for :mod:`pokeuds.wfc.http`.

tlslite-ng is an optional dependency: the other PokeUDS features do not need it, and the import error says how to
install it.
"""

from __future__ import annotations

import socket

try:
    from tlslite import HandshakeSettings, TLSConnection, X509CertChain
    from tlslite.constants import CipherSuite
    from tlslite.utils.keyfactory import parsePEMKey
except ImportError as exc:                     # pragma: no cover - depends on the environment
    raise ImportError("the DS's SSL 3.0 server needs tlslite-ng: python -m pip install tlslite-ng") from exc

from .certs import ServerIdentity

SSL3 = (3, 0)


def settings(newest: tuple[int, int] = SSL3) -> HandshakeSettings:
    """SSL 3.0 (up to ``newest``, for tests), RSA key exchange, RC4 with SHA-1 or MD5: RC4-SHA and RC4-MD5.

    The DS offers only those two cipher suites (RC4-MD5 usually first). RC4, MD5 and SSL 3.0 are all opt-in in
    tlslite-ng; nothing newer is allowed because a DS cannot speak it, and offering it changes the ServerHello.
    """
    s = HandshakeSettings()
    s.minVersion, s.maxVersion = SSL3, newest
    # HandshakeSettings' default list stops at TLS 1.0 (SSL 3.0 is not in it), and validate() only filters it by
    # maxVersion. List SSL 3.0 explicitly so the server never has to fall back to a version the DS cannot speak.
    s.versions = [version for version in ((3, 0), (3, 1), (3, 2), (3, 3)) if SSL3 <= version <= newest]
    s.cipherNames = ["rc4"]
    # MD5 first: the DS's SSL library is happiest with RC4-MD5 (0x0004); RC4-SHA stays as a fallback for clients that
    # do not offer MD5.
    s.macNames = ["md5", "sha"]
    s.keyExchangeNames = ["rsa"]
    s.useEncryptThenMAC = False
    s.useExtendedMasterSecret = False
    s.requireExtendedMasterSecret = False
    return s


class Server:
    """The server side of every DS connection, from one identity (the chain and key are made once)."""

    def __init__(self, identity: ServerIdentity) -> None:
        self.identity = identity
        self.chain = X509CertChain()
        self.chain.parsePemList(identity.chain_pem())
        self.key = parsePEMKey(identity.key_pem(), private=True)
        self.settings = settings()

    def wrap(self, sock: socket.socket) -> TLSConnection:
        """Handshake on an accepted socket; -> the connection (raises on a failed handshake).

        The caller sets the socket timeout; a DS that gives up mid-handshake must not hold a thread.
        """
        conn = TLSConnection(sock)
        conn.handshakeServer(certChain=self.chain, privateKey=self.key, settings=self.settings)
        return conn


def describe(conn: TLSConnection) -> str:
    """What was agreed, for the log: "SSL 3.0 TLS_RSA_WITH_RC4_128_SHA"."""
    major, minor = conn.version
    version = "SSL 3.0" if (major, minor) == SSL3 else f"TLS 1.{minor - 1}"
    names = getattr(CipherSuite, "ietfNames", {})
    suite = conn.session.cipherSuite
    name = names.get(suite) if hasattr(names, "get") else None
    return f"{version} {name or hex(suite)}"
