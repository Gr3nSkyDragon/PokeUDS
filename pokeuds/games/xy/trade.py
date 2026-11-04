"""Pokemon X/Y local wireless trade.

Status: the UDS session (finding the partner, joining or hosting, CCMP, keepalive) is complete.
The game-level exchange inside it (how X/Y find each other in the PSS, request and accept a
trade, show the offer, confirm and swap) is not mapped yet; it needs the captures described in
docs/captures.md. ``XyTrade.run`` therefore stops with ``ProtocolNotMapped`` once the session is
up, after recording what the console says, instead of sending invented messages.

What a finished trade does, as in pokeldn: offer one .pk6, receive the console's Pokemon, and
write it to the output folder (``pokeuds.received.save``).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from ... import pk6, received
from ...uds import Beacon, Role, Station
from ...util import mac_str
from .profile import XyProfile

log = logging.getLogger(__name__)
GAME = "xy"


class ProtocolNotMapped(NotImplementedError):
    pass


@dataclass
class XyTrade:
    station: Station
    profile: XyProfile
    offer: bytes                         # Decrypted pk6 we send.
    output: Path
    trades_done: int = 0
    heard: list = field(default_factory=list)

    def join(self, seconds: float = 30.0) -> Beacon:
        """Find a console of this game and join its network."""
        log.info("looking for Pokemon X/Y (comm id %08X)", self.profile.wlan_comm_id)
        found = self.station.scan(seconds, wlan_comm_id=self.profile.wlan_comm_id, until=lambda b: True)
        if not found:
            raise TimeoutError("no Pokemon X/Y network heard; is the console in the PSS with wireless on?")
        target = max(found, key=lambda b: b.rssi if b.rssi is not None else -100)
        log.info("joining %s on channel %d", mac_str(target.host), target.channel)
        self.station.connect(target, self.profile.passphrase)
        return target

    def host(self, application_data: bytes = b"") -> None:
        self.station.host(wlan_comm_id=self.profile.wlan_comm_id, passphrase=self.profile.passphrase,
                          channel=self.profile.channel or 11, max_nodes=self.profile.max_nodes,
                          application_data=application_data, id_=self.profile.id)

    def receive_record(self, record: bytes) -> Path:
        """Called by the exchange once the console's Pokemon has arrived."""
        self.trades_done += 1
        path = received.save(record, self.output, game=GAME, trade=self.trades_done)
        s = pk6.summary(pk6.decrypt(record) if not pk6.checksum_valid(record) else record)
        log.info("received species %d %r (PID %08X) -> %s", s.species, s.nickname, s.pid, path)
        print(f"[done] trade {self.trades_done} complete: {path}")
        return path

    def run(self, listen_seconds: float = 20.0) -> None:
        if self.station.role not in (Role.HOST, Role.CLIENT):
            raise RuntimeError("join or host before running the trade")
        # Record what the console sends so a run is never wasted: this is capture data.
        deadline = time.monotonic() + listen_seconds
        while time.monotonic() < deadline:
            for channel in self.profile.data_channels:
                packet = self.station.recv(channel, timeout=0.05)
                if packet is not None:
                    self.heard.append(packet)
                    log.info("console -> us: channel %d node %d, %d bytes: %s", packet.channel, packet.src_node,
                             len(packet.payload), packet.payload[:32].hex())
                    for _off, _size, record in pk6.find_records(packet.payload):
                        self.receive_record(record)
        raise ProtocolNotMapped(
            "The UDS session is up, but the X/Y trade messages are not mapped yet, so PokeUDS cannot "
            "offer its Pokemon. See docs/captures.md for the captures that fill this in.")
