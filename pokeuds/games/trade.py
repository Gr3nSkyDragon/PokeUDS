"""A local wireless trade with a 3DS Pokemon game, in either role (Generations 6 and 7).

Status: the UDS session is complete in both roles.

- **join**: find a console of the game (or of a game it trades with), join its network.
- **host**: open a network the console can join, advertising the profile's application data,
  and wait for the console's EAPoL join.

The game-level exchange inside the session (presence in the PSS or Festival Plaza,
request/accept, showing the offer, confirmation, swap) is not mapped yet; it needs the captures
in docs/captures.md. ``run`` therefore records what the console sends, saves any Pokemon record
found in it, and then stops with ``ProtocolNotMapped`` instead of sending invented messages.

A finished trade does what pokeldn's do: offer one record, receive the console's Pokemon, write
it to the output folder (``pokeuds.received.save``).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import pkx, received, status
from ..uds import Beacon, Role, SecureData, Station
from ..util import mac_str
from .base import Game
from .profile import Profile

log = logging.getLogger(__name__)
DEFAULT_HOST_CHANNEL = 11


class ProtocolNotMapped(NotImplementedError):
    pass


@dataclass
class LocalTrade:
    station: Station
    game: Game
    profile: Profile
    offer: bytes                         # Decrypted record we send.
    output: Path
    trades_done: int = 0
    heard: list[SecureData] = field(default_factory=list)
    saved: list[Path] = field(default_factory=list)
    partner: Beacon | None = None

    def __post_init__(self) -> None:
        pkx.check_format(self.offer, self.game.record)

    # -- session ----------------------------------------------------------------------------------

    def join(self, seconds: float = 60.0) -> Beacon:
        """Find a console of this game and join its network."""
        ids = self.profile.join_comm_ids()
        log.info("looking for %s (comm id %s)", self.game.name, ", ".join(f"{c:08X}" for c in ids))
        found = self.station.scan(seconds, wlan_comm_id=ids, until=lambda b: True)
        if not found:
            raise TimeoutError(f"no {self.game.name} network heard; is the console in the {self.game.hub} "
                               "with wireless on?")
        target = max(found, key=lambda b: b.rssi if b.rssi is not None else -100)
        log.info("joining %s on channel %d", mac_str(target.host), target.channel)
        self.station.connect(target, self.profile.passphrase)
        self.partner = target
        return target

    def host(self, application_data: bytes | None = None) -> None:
        """Open a network for the console to join."""
        self.station.host(wlan_comm_id=self.profile.wlan_comm_id, passphrase=self.profile.passphrase,
                          channel=self.profile.channel or DEFAULT_HOST_CHANNEL, max_nodes=self.profile.max_nodes,
                          application_data=self.profile.application_data if application_data is None else application_data,
                          id_=self.profile.id)

    def wait_for_partner(self, seconds: float) -> int:
        """As host: wait until a console has joined; its node id."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.station.role is not Role.HOST:
                break
            joined = [p for p in self.station.peers.values() if p.connected and not p.spectator]
            if joined:
                log.info("node %d joined (%s)", joined[0].node_id, mac_str(joined[0].mac))
                return joined[0].node_id
            time.sleep(0.05)
        raise TimeoutError(f"no console joined within {seconds:.0f} s")

    # -- trade ------------------------------------------------------------------------------------

    def receive_record(self, record: bytes) -> Path:
        """Called once the console's Pokemon has arrived."""
        self.trades_done += 1
        path = received.save(record, self.output, game=self.game.key, trade=self.trades_done, fmt=self.game.record)
        s = pkx.summary(record if pkx.checksum_valid(record) else pkx.decrypt(record))
        log.info("received species %d %r (PID %08X) -> %s", s.species, s.nickname, s.pid, path)
        print(f"[done] trade {self.trades_done} complete: {path}")
        status.emit("received", trade=self.trades_done, path=str(path),
                    summary=f"species {s.species} '{s.nickname}' PID {s.pid:08X}", files=[str(path)])
        self.saved.append(path)
        return path

    def _listen(self, seconds: float) -> None:
        # Record what the console sends so a run is never wasted: this is capture data.
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and self.station.role in (Role.HOST, Role.CLIENT):
            for channel in self.profile.data_channels:
                packet = self.station.recv(channel, timeout=0.05)
                if packet is None:
                    continue
                self.heard.append(packet)
                status.emit("note", text=f"console sent {len(packet.payload)} bytes on channel {packet.channel}")
                log.info("console -> us: channel %d node %d, %d bytes: %s", packet.channel, packet.src_node,
                         len(packet.payload), packet.payload[:32].hex())
                for _off, _size, record in pkx.find_records(packet.payload, self.game.record):
                    self.receive_record(record)

    def run(self, listen_seconds: float = 20.0) -> None:
        if self.station.role not in (Role.HOST, Role.CLIENT):
            raise RuntimeError("join or host before running the trade")
        self._listen(listen_seconds)
        raise ProtocolNotMapped(
            f"The UDS session is up as {self.station.role.value}, but the {self.game.name} trade messages are "
            "not mapped yet, so PokeUDS cannot offer its Pokemon. See docs/captures.md for the captures "
            "that fill this in.")
