"""802.11 MPDU headers and the management frames of a UDS join.

UDS uses an infrastructure-style layout around the host, whose MAC is the BSSID: a client's data
is ToDS, the host's is FromDS, and group-addressed data is NoDS. Frame layouts follow what
azahar_uds captured from retail consoles (Pokemon X and VC Pokemon Red).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..util import BROADCAST

TYPE_MGMT, TYPE_CTRL, TYPE_DATA = 0, 1, 2
SUBTYPE_ASSOC_REQ = 0
SUBTYPE_ASSOC_RESP = 1
SUBTYPE_PROBE_REQ = 4
SUBTYPE_PROBE_RESP = 5
SUBTYPE_BEACON = 8
SUBTYPE_AUTH = 11
SUBTYPE_DEAUTH = 12

FC_ASSOC_REQ = 0x0000
FC_ASSOC_RESP = 0x0010
FC_PROBE_REQ = 0x0040
FC_BEACON = 0x0080
FC_AUTH = 0x00B0
FC_DEAUTH = 0x00C0
FC_DATA = 0x0008
FC_TO_DS = 0x0100
FC_FROM_DS = 0x0200
FC_RETRY = 0x0800
FC_PROTECTED = 0x4000

HEADER_SIZE = 24
CAPABILITIES = 0x0431           # Every retail UDS frame seen so far.
SUPPORTED_RATES = bytes([0x82, 0x84, 0x8B, 0x96, 0x0C, 0x12, 0x18, 0x24])
ASSOC_REQ_RATES = bytes([0x82, 0x84, 0x8B, 0x0C, 0x12, 0x96, 0x18, 0x24])  # Retail Pokemon X.
EXTENDED_RATES = bytes([0x30, 0x48, 0x60, 0x6C])
NINTENDO_SCAN_SSID = b"Nintendo_3DS_continuous_scan_000"

IE_SSID, IE_RATES, IE_DS, IE_TIM, IE_COUNTRY, IE_ERP, IE_EXT_RATES, IE_VENDOR = 0, 1, 3, 5, 7, 42, 50, 221


@dataclass
class Frame:
    """A parsed MPDU with logical addresses, whatever the ToDS/FromDS layout."""

    mpdu: bytes
    fc: int
    seq_ctrl: int
    transmitter: bytes
    destination: bytes
    bssid: bytes
    body: bytes

    @property
    def type(self) -> int:
        return (self.fc >> 2) & 0x3

    @property
    def subtype(self) -> int:
        return (self.fc >> 4) & 0xF

    @property
    def protected(self) -> bool:
        return bool(self.fc & FC_PROTECTED)

    @property
    def retry(self) -> bool:
        return bool(self.fc & FC_RETRY)

    @property
    def sequence(self) -> int:
        return self.seq_ctrl >> 4


def parse(mpdu: bytes) -> Frame | None:
    if len(mpdu) < HEADER_SIZE:
        return None
    fc, _duration = struct.unpack_from("<HH", mpdu, 0)
    if (fc >> 2) & 0x3 == TYPE_CTRL:
        return None  # Control frames have shorter headers and play no part in UDS.
    a1, a2, a3 = mpdu[4:10], mpdu[10:16], mpdu[16:22]
    (seq_ctrl,) = struct.unpack_from("<H", mpdu, 22)
    to_ds, from_ds = bool(fc & FC_TO_DS), bool(fc & FC_FROM_DS)
    if (fc >> 2) & 0x3 == TYPE_DATA and to_ds and not from_ds:
        dest, bssid = a3, a1
    elif (fc >> 2) & 0x3 == TYPE_DATA and from_ds and not to_ds:
        dest, bssid = a1, a2
    else:
        dest, bssid = a1, a3
    return Frame(mpdu, fc, seq_ctrl, a2, dest, bssid, mpdu[HEADER_SIZE:])


def header(fc: int, a1: bytes, a2: bytes, a3: bytes, seq: int) -> bytes:
    return struct.pack("<HH", fc, 0) + a1 + a2 + a3 + struct.pack("<H", (seq & 0x0FFF) << 4)


def management(fc: int, transmitter: bytes, destination: bytes, bssid: bytes, seq: int, body: bytes) -> bytes:
    return header(fc, destination, transmitter, bssid, seq) + body


def data_addresses(transmitter: bytes, destination: bytes, bssid: bytes, *, from_ds: bool) -> tuple[int, bytes, bytes, bytes]:
    """(DS bits, A1, A2, A3) for a UDS data frame."""
    if destination == BROADCAST:
        return 0, destination, transmitter, bssid
    if from_ds:
        return FC_FROM_DS, destination, bssid, transmitter
    return FC_TO_DS, bssid, transmitter, destination


def ie(tag: int, value: bytes) -> bytes:
    return bytes([tag, len(value)]) + value


def iter_ies(body: bytes, offset: int = 0):
    while offset + 2 <= len(body):
        tag, length = body[offset], body[offset + 1]
        offset += 2
        if offset + length > len(body):
            return
        yield tag, body[offset:offset + length]
        offset += length


def find_ie(body: bytes, tag: int, offset: int = 0) -> bytes | None:
    return next((value for t, value in iter_ies(body, offset) if t == tag), None)


def ssid_for(network_id: int) -> bytes:
    """The SSID a UDS network uses in association frames: its id as 8 uppercase hex digits."""
    return f"{network_id:08X}".encode()


# -- Authentication (open system) ------------------------------------------------------------------

def auth_body(seq: int, status: int = 0) -> bytes:
    return struct.pack("<HHH", 0, seq, status)


def parse_auth(body: bytes) -> tuple[int, int, int] | None:
    """(algorithm, sequence, status)."""
    return struct.unpack_from("<HHH", body) if len(body) >= 6 else None


# -- Association ----------------------------------------------------------------------------------

def assoc_request_body(network_id: int) -> bytes:
    # A retail Pokemon X association request, captured on the air by azahar_uds.
    return (struct.pack("<HH", CAPABILITIES, 1) + ie(IE_SSID, ssid_for(network_id))
            + ie(IE_RATES, ASSOC_REQ_RATES) + ie(IE_EXT_RATES, EXTENDED_RATES))


def assoc_response_body(association_id: int, network_id: int, status: int = 0) -> bytes:
    # The association id carries the 802.11 magic 0xC000 in its top bits.
    return (struct.pack("<HHH", CAPABILITIES, status, association_id | 0xC000) + ie(IE_SSID, ssid_for(network_id))
            + ie(IE_RATES, SUPPORTED_RATES) + ie(IE_EXT_RATES, EXTENDED_RATES))


def parse_assoc_response(body: bytes) -> tuple[int, int] | None:
    """(status, association id)."""
    if len(body) < 6:
        return None
    _caps, status, aid = struct.unpack_from("<HHH", body)
    return status, aid & 0x3FFF


def deauth_body(reason: int = 3) -> bytes:
    return struct.pack("<H", reason)


def probe_request(source: bytes, seq: int, channel: int) -> bytes:
    """The directed probe of the 3DS continuous scanner; retail hosts answer it, wildcards not always."""
    body = (ie(IE_SSID, NINTENDO_SCAN_SSID) + ie(IE_RATES, SUPPORTED_RATES) + ie(IE_EXT_RATES, EXTENDED_RATES)
            + ie(IE_DS, bytes([channel])))
    return header(FC_PROBE_REQ, BROADCAST, source, BROADCAST, seq) + body
