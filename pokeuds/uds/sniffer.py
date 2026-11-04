"""Passive decryption of other consoles' UDS traffic.

With the game's passphrase and the keyslot 0x2D key, every network's CCMP key follows from its
beacon, so a radio in monitor mode can read a trade between two retail consoles without taking
part. This is the capture path that needs no emulator (azahar_uds ``AZAHAR_UDS_SNIFF`` does the
same inside Azahar).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from ..radio.base import DISCOVERY_CHANNELS, Radio
from . import beacon as bcn
from . import crypto, data, dot11

log = logging.getLogger(__name__)


@dataclass
class SniffedPacket:
    time: float
    bssid: bytes
    transmitter: bytes
    destination: bytes
    role: str                 # "host" or "client"
    dot11_seq: int
    pn: int
    retry: bool
    kind: str                 # "securedata", "eapol-start", "eapol-logoff", "other"
    packet: data.SecureData | None
    plaintext: bytes


class Sniffer:
    def __init__(self, radio: Radio, uds_key: bytes, passphrase: bytes, *, wlan_comm_id: int | None = None) -> None:
        self.radio = radio
        self.uds_key = uds_key
        self.passphrase = passphrase
        self.wlan_comm_id = wlan_comm_id
        self.networks: dict[bytes, tuple[bcn.NetworkInfo, bytes]] = {}
        self.ccmp_failures = 0
        self.on_network: Callable[[bcn.Beacon], None] | None = None

    def feed(self, mpdu: bytes, channel: int, rssi: int | None = None) -> list[SniffedPacket]:
        frame = dot11.parse(mpdu)
        if frame is None:
            return []
        if frame.type == dot11.TYPE_MGMT:
            heard = bcn.parse_beacon(frame, channel, rssi)
            if heard and (self.wlan_comm_id is None or heard.info.wlan_comm_id == self.wlan_comm_id):
                known = self.networks.get(heard.host)
                if known is None or known[0].network_id != heard.info.network_id:
                    key = heard.info.ccmp_key(self.uds_key, self.passphrase)
                    self.networks[heard.host] = (heard.info, key)
                    if self.on_network:
                        self.on_network(heard)
            return []
        if frame.type != dot11.TYPE_DATA or not frame.protected or frame.bssid not in self.networks:
            return []
        info, key = self.networks[frame.bssid]
        plain = crypto.open_data_frame(key, frame)
        if plain is None:
            self.ccmp_failures += 1
            return []
        role = "host" if frame.transmitter == frame.bssid else "client"
        common = dict(time=time.time(), bssid=frame.bssid, transmitter=frame.transmitter,
                      destination=frame.destination, role=role, dot11_seq=frame.sequence,
                      pn=crypto.ccmp_pn(frame.body), retry=frame.retry, plaintext=plain)
        kind = data.ethertype(plain)
        if kind == data.ETHERTYPE_SECUREDATA:
            return [SniffedPacket(kind="securedata", packet=p, **common) for p in data.unpack_securedata(plain)]
        if kind == data.ETHERTYPE_EAPOL:
            eapol = data.eapol_kind(plain)
            name = {data.EAPOL_START: "eapol-start", data.EAPOL_LOGOFF: "eapol-logoff"}.get(eapol, "other")
            return [SniffedPacket(kind=name, packet=None, **common)]
        return [SniffedPacket(kind="other", packet=None, **common)]

    def run(self, on_packet: Callable[[SniffedPacket], None], seconds: float | None = None,
            channels=DISCOVERY_CHANNELS, dwell: float = 0.25) -> None:
        """Hop ``channels`` until a matching network's beacon is heard, then stay (``on_network``
        normally tunes to it) and decode everything until ``seconds`` pass."""
        deadline = None if seconds is None else time.monotonic() + seconds
        hop_at, index = time.monotonic() + dwell, 0
        while deadline is None or time.monotonic() < deadline:
            if not self.networks and time.monotonic() >= hop_at:
                index += 1
                self.radio.set_channel(channels[index % len(channels)])
                hop_at = time.monotonic() + dwell
            rx = self.radio.recv(0.05)
            if rx is None:
                continue
            for packet in self.feed(rx.mpdu, rx.channel, rx.rssi):
                on_packet(packet)
