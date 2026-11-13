"""The certificate a DS trusts for Nintendo's servers (docs/ds_wifi.md, "The certificate").

The DS does not check whether a certificate may sign others ("nds-constrain't", shutterbug2000, 2018). The Wii's
built-in SSL client certificate ("Wii NWC Prod 1", issued by Nintendo CA, the same on every Wii) is one the DS's own
roots vouch for, so a server certificate signed with the Wii's key and sent with the Wii's certificate behind it is
trusted. Following KaeruTeam/nds-constraint: an RSA 1024 key, SHA-1, ten years.

PokeUDS carries neither Wii file. The player gives them (Dolphin's ``clientca.pem`` and ``clientcakey.pem``, which its
Tools > Perform Online System Update extracts from IOS13); they are checked against the SHA-256 Dolphin checks them by
(Source/Core/Core/IOS/Network/SSL.cpp), and any other pair is used with a warning.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from Crypto.Hash import SHA1
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15

CERT_NAME, KEY_NAME = "clientca.pem", "clientcakey.pem"
# Dolphin checks the files it extracts by these (SSL.cpp); a match means the real Wii pair.
DOLPHIN_SHA256 = {
    CERT_NAME: "229ec678525e060588e8ea23e5459ec14af3c2ebb7b9e69ec46b0faf011730d9",
    KEY_NAME: "723be9b32c3afb83a4a3757adf352529e90c0ad6fad52509963ba8942ae625df",
}
ALT_NAMES = {CERT_NAME: ("nwc.crt", "NWC.crt"), KEY_NAME: ("nwc.key", "NWC.key")}   # nds-constraint's names
DEFAULT_CN = "*.*.*"        # every three-label name (flewkey, 2021); --cn nas.nintendowifi.net for the one host
SHA1_RSA = "1.2.840.113549.1.1.5"
CN_OID = "2.5.4.3"
SAN_OID = "2.5.29.17"
# The DS matches a certificate name label-for-label, with '*' matching one label. '*.*.*' covers nas/dls1, but the
# SAN list also covers the four- and five-label hosts other WFC services use (gamestats2.gs..., pkvldtprod...).
SAN_NAMES = ("*.*.*", "*.*.*.*", "*.*.*.*.*", "*.nintendowifi.net", "nas.nintendowifi.net",
             "dls1.nintendowifi.net", "conntest.nintendowifi.net", "*.nintendo.co.jp")


# -- DER, the little a certificate needs -------------------------------------------------------------------------

def _length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _length(len(content)) + content


def seq(*parts: bytes) -> bytes:
    return tlv(0x30, b"".join(parts))


def oid(dotted: str) -> bytes:
    nums = [int(x) for x in dotted.split(".")]
    body = bytearray([40 * nums[0] + nums[1]])
    for n in nums[2:]:
        chunk = [n & 0x7F]
        while n > 0x7F:
            n >>= 7
            chunk.append(0x80 | (n & 0x7F))
        body += bytes(reversed(chunk))
    return tlv(0x06, bytes(body))


def integer(n: int) -> bytes:
    body = n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big", signed=False)
    return tlv(0x02, body)


def read_tlv(data: bytes, at: int = 0) -> tuple[int, int, int]:
    """-> (tag, where the content starts, where it ends)."""
    tag, first = data[at], data[at + 1]
    at += 2
    if first < 0x80:
        size = first
    else:
        count = first & 0x7F
        size = int.from_bytes(data[at:at + count], "big")
        at += count
    return tag, at, at + size


def children(data: bytes, start: int, end: int) -> list[tuple[int, int, int, int]]:
    """The TLVs inside ``data[start:end]``: (tag, element start, content start, end)."""
    out, at = [], start
    while at < end:
        tag, body, stop = read_tlv(data, at)
        out.append((tag, at, body, stop))
        at = stop
    return out


def _oid_text(body: bytes) -> str:
    nums, n = [body[0] // 40, body[0] % 40], 0
    for b in body[1:]:
        n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            nums.append(n)
            n = 0
    return ".".join(map(str, nums))


def pem_to_der(text: str, label: str = "CERTIFICATE") -> bytes:
    found = re.search(rf"-----BEGIN {label}-----(.*?)-----END {label}-----", text, re.S)
    if not found:
        raise ValueError(f"no {label} in the file")
    return base64.b64decode("".join(found.group(1).split()))


def der_to_pem(der: bytes, label: str = "CERTIFICATE") -> str:
    body = base64.b64encode(der).decode()
    return f"-----BEGIN {label}-----\n" + "\n".join(body[i:i + 64] for i in range(0, len(body), 64)) + f"\n-----END {label}-----\n"


# -- a certificate read -------------------------------------------------------------------------------------------

@dataclass
class Certificate:
    der: bytes
    subject: bytes            # the subject Name, DER
    issuer: bytes
    public_key: RSA.RsaKey

    @classmethod
    def parse(cls, der: bytes) -> "Certificate":
        _, body, end = read_tlv(der)
        tbs = children(der, body, end)[0]
        fields = children(der, tbs[2], tbs[3])
        if fields and fields[0][0] == 0xA0:              # [0] version (v3)
            fields = fields[1:]
        issuer, subject, spki = fields[2], fields[4], fields[5]
        return cls(der, der[subject[1]:subject[3]], der[issuer[1]:issuer[3]], RSA.import_key(der[spki[1]:spki[3]]))

    @property
    def tbs(self) -> bytes:
        _, body, end = read_tlv(self.der)
        first = children(self.der, body, end)[0]
        return self.der[first[1]:first[3]]

    @property
    def signature(self) -> bytes:
        _, body, end = read_tlv(self.der)
        bits = children(self.der, body, end)[2]
        return self.der[bits[2] + 1:bits[3]]             # past the unused-bits byte

    @staticmethod
    def common_name(name: bytes) -> str:
        """The CN of a Name (DER), "" without one."""
        _, body, end = read_tlv(name)
        for _, _, set_body, set_end in children(name, body, end):
            for _, _, attr_body, attr_end in children(name, set_body, set_end):
                parts = children(name, attr_body, attr_end)
                if len(parts) == 2 and _oid_text(name[parts[0][2]:parts[0][3]]) == CN_OID:
                    return name[parts[1][2]:parts[1][3]].decode("utf-8", "replace")
        return ""

    def signed_by(self, issuer: "Certificate") -> bool:
        try:
            pkcs1_15.new(issuer.public_key).verify(SHA1.new(self.tbs), self.signature)
            return True
        except (ValueError, TypeError):
            return False


def make_server_cert(issuer: Certificate, issuer_key: RSA.RsaKey, cn: str = DEFAULT_CN,
                     key: RSA.RsaKey | None = None, now: datetime.datetime | None = None,
                     days: int = 3650, san: tuple[str, ...] = SAN_NAMES) -> tuple[bytes, RSA.RsaKey]:
    """A v3 certificate for ``cn`` (as ``openssl x509 -req -sha1`` makes one), signed with the issuer's key.
    -> (DER, its RSA 1024 key).

    The SAN list is there for the DS's name check and for the four-label hosts; the CN stays ``*.*.*``. It is valid
    from 2000: a DS whose clock battery has died starts at 2000, and an old SSL library may check the dates, so a
    certificate made "yesterday" could be refused. Ten years from now keeps it short of the Wii certificate's own
    2036 expiry."""
    key = key or RSA.generate(1024)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    start = datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)
    stamp = lambda t: tlv(0x17, t.strftime("%y%m%d%H%M%SZ").encode())       # UTCTime
    algorithm = seq(oid(SHA1_RSA), tlv(0x05, b""))
    name = seq(tlv(0x31, seq(oid(CN_OID), tlv(0x0C, cn.encode()))))        # CN, UTF8String as OpenSSL writes it
    names = seq(*(tlv(0x82, value.encode("ascii")) for value in san))       # GeneralNames, dNSName [2]
    extensions = tlv(0xA3, seq(seq(oid(SAN_OID), tlv(0x04, names))))
    tbs = seq(tlv(0xA0, integer(2)),                                        # v3
              integer(int.from_bytes(os.urandom(8), "big") >> 1),
              algorithm, issuer.subject,
              seq(stamp(start), stamp(now + datetime.timedelta(days=days))),
              name, key.publickey().export_key("DER"), extensions)
    signature = pkcs1_15.new(issuer_key).sign(SHA1.new(tbs))
    return seq(tbs, algorithm, tlv(0x03, b"\0" + signature)), key


# -- the Wii pair, and the server certificate made from it --------------------------------------------------------

@dataclass
class WiiPair:
    cert_path: Path
    key_path: Path
    cert: Certificate
    key: RSA.RsaKey
    dolphin: bool             # both files are the ones Dolphin checks for

    @property
    def name(self) -> str:
        return Certificate.common_name(self.cert.subject)

    @property
    def issuer_name(self) -> str:
        return Certificate.common_name(self.cert.issuer)


def find_pair(where: str | Path) -> tuple[Path, Path] | None:
    """The Wii certificate and key in a folder (or beside a file in it, as aes_keys.txt): Dolphin's names, else
    nds-constraint's."""
    folder = Path(where)
    if folder.is_file():
        folder = folder.parent
    found = []
    for name in (CERT_NAME, KEY_NAME):
        path = next((folder / n for n in (name, *ALT_NAMES[name]) if (folder / n).is_file()), None)
        if path is None:
            return None
        found.append(path)
    return found[0], found[1]


def load_pair(cert_path: str | Path, key_path: str | Path) -> WiiPair:
    cert_path, key_path = Path(cert_path), Path(key_path)
    cert_bytes, key_bytes = cert_path.read_bytes(), key_path.read_bytes()
    try:
        der = cert_bytes if cert_bytes[:1] == b"\x30" else pem_to_der(cert_bytes.decode("ascii"))
        cert = Certificate.parse(der)
    except (ValueError, IndexError, UnicodeDecodeError) as exc:
        raise ValueError(f"{cert_path.name} is not a certificate PokeUDS can read ({exc})") from None
    try:
        key = RSA.import_key(key_bytes)
    except (ValueError, IndexError, TypeError) as exc:
        raise ValueError(f"{key_path.name} is not an RSA private key PokeUDS can read ({exc})") from None
    if not key.has_private() or key.publickey() != cert.public_key:
        raise ValueError(f"{key_path.name} is not the private key of {cert_path.name}")
    dolphin = (hashlib.sha256(cert_bytes).hexdigest() == DOLPHIN_SHA256[CERT_NAME]
               and hashlib.sha256(key_bytes).hexdigest() == DOLPHIN_SHA256[KEY_NAME])
    return WiiPair(cert_path, key_path, cert, key, dolphin)


@dataclass
class ServerIdentity:
    chain: list[bytes]        # DER: the server certificate, then the Wii's
    key: RSA.RsaKey
    cn: str

    def chain_pem(self) -> str:
        return "".join(der_to_pem(c) for c in self.chain)

    def key_pem(self) -> str:
        return self.key.export_key("PEM").decode()


def server_identity(pair: WiiPair, folder: str | Path, cn: str = DEFAULT_CN) -> ServerIdentity:
    """The server certificate for ``cn``, made once and kept in ``folder`` (server.pem, server.key): made again when
    the Wii pair or the name changes."""
    folder = Path(folder)
    cert_file, key_file, note_file = folder / "server.pem", folder / "server.key", folder / "server.json"
    stamp = {"wii": hashlib.sha256(pair.cert.der).hexdigest(), "cn": cn, "format": 3}
    try:
        if json.loads(note_file.read_text(encoding="utf-8")) == stamp:
            der = pem_to_der(cert_file.read_text(encoding="ascii"))
            key = RSA.import_key(key_file.read_bytes())
            parsed = Certificate.parse(der)
            if parsed.signed_by(pair.cert) and parsed.public_key == key.publickey():
                return ServerIdentity([der, pair.cert.der], key, cn)
    except (OSError, ValueError, IndexError, binascii.Error):
        pass
    der, key = make_server_cert(pair.cert, pair.key, cn)
    folder.mkdir(parents=True, exist_ok=True)
    cert_file.write_text(der_to_pem(der), encoding="ascii")
    key_file.write_bytes(key.export_key("PEM"))
    note_file.write_text(json.dumps(stamp), encoding="utf-8")
    return ServerIdentity([der, pair.cert.der], key, cn)
