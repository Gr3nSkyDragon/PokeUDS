"""A Generation 6 trade from the PSS: being seen, asking or being asked, then the trade network.

Every console in the PSS keeps hosting a small ``id=2`` network whose application data is its PSS
beacon (``wire.pss_beacon``), and listens for the others'. A trade request travels in those
beacons: the requester names the target, the target names the requester back, and the requester
then hosts the trade network (``id=1``, four nodes, no application data) that the target joins.

``role="join"``: PokeUDS shows up in the console's PSS and waits for the player there to ask it for
a trade, answers, and joins the console's trade network (PokeUDS is node 2).

``role="host"``: PokeUDS asks a console it hears in the PSS (the first, or the one whose trainer is
``partner``), waits for the answer, and hosts the trade network (PokeUDS is node 1).

Retail consoles advertise on channel 1, 6 or 11, so PokeUDS hops all three while it waits, its own
beacon moving with the radio.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ... import pkx, received, status
from ...uds import Beacon, Role, Station
from ...util import mac_str
from ..base import Game
from ..profile import Profile
from . import session, wire

log = logging.getLogger(__name__)

PSS_NETWORK, TRADE_NETWORK = 2, 1
PSS_MAX_NODES, TRADE_MAX_NODES = 2, 4
PSS_CHANNELS = (1, 6, 11)
DWELL = 0.45
TRADE_CHANNEL = 11              # Where PokeUDS first advertises.


@dataclass
class Seen:
    player: wire.PssPlayer
    mac: bytes
    channel: int
    at: float


@dataclass
class Gen6Trade:
    station: Station
    game: Game
    profile: Profile
    offer: bytes                   # Decrypted, box or party.
    output: Path
    trainer: wire.Trainer
    trades: int = 1
    partner: str | None = None     # As host: the trainer name to ask (default: the first heard).
    channels: tuple[int, ...] = PSS_CHANNELS
    announce: bool = True          # Print progress and emit status events (off for the demo console).
    saved: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self.players: dict[bytes, Seen] = {}
        self.trade_networks: dict[bytes, Beacon] = {}
        self.party = session.party_record(self.offer)

    def _say(self, text: str) -> None:
        if self.announce:
            print(text, flush=True)

    def _emit(self, event: str, **fields) -> None:
        if self.announce:
            status.emit(event, **fields)

    # -- beacons ----------------------------------------------------------------------------------

    def _on_beacon(self, beacon: Beacon) -> None:
        if beacon.info.wlan_comm_id not in self.profile.join_comm_ids():
            return
        with self._lock:
            if beacon.info.id == TRADE_NETWORK:
                self.trade_networks[beacon.host] = beacon
            elif beacon.info.id == PSS_NETWORK:
                player = wire.parse_pss_beacon(beacon.info.application_data)
                if player is None or player.pss_id == self.trainer.pss_id:
                    return
                if player.pss_id not in self.players:
                    log.info("PSS: %s (%s) on channel %d", player.name, mac_str(beacon.host), beacon.channel)
                    self._emit("found", name=player.name, channel=beacon.channel)
                self.players[player.pss_id] = Seen(player, beacon.host, beacon.channel, time.monotonic())

    def _advertise(self, beacon: bytes) -> None:
        self.station.stop_hosting()
        self.station.host(wlan_comm_id=self.profile.wlan_comm_id, passphrase=self.profile.passphrase,
                          channel=self.station.radio.channel or TRADE_CHANNEL, max_nodes=PSS_MAX_NODES,
                          application_data=beacon, id_=PSS_NETWORK)

    def _hop(self, until, seconds: float, what: str):
        """Hop the PSS channels until ``until()`` returns something, or raise after ``seconds``."""
        deadline = time.monotonic() + seconds
        index = 0
        while time.monotonic() < deadline:
            self.station.retune(self.channels[index % len(self.channels)])
            index += 1
            end = time.monotonic() + DWELL
            while time.monotonic() < end:
                with self._lock:
                    found = until()
                if found:
                    return found
                time.sleep(0.02)
        raise TimeoutError(f"no {what} within {seconds:.0f} s; is the console in the PSS with wireless on?")

    # -- the two ways in --------------------------------------------------------------------------

    def wait_to_be_asked(self, seconds: float) -> None:
        """Show up in the PSS, answer the first trade request, join the requester's trade network."""
        self.station.on_beacon = self._on_beacon
        self._advertise(wire.pss_beacon(self.trainer))
        self._say(f"in the PSS as {self.trainer.name}; ask PokeUDS for a trade from the console")
        self._emit("searching", game=self.game.key, phase="pss")
        asker = self._hop(lambda: next((s for s in self.players.values() if s.player.wants(self.trainer.pss_id)), None),
                          seconds, "trade request")
        log.info("%s asks for a trade; answering", asker.player.name)
        self._emit("phase", phase="asked", partner=asker.player.name)
        self._advertise(wire.pss_beacon(self.trainer, partner=asker.player))
        network = self._hop(lambda: self.trade_networks.get(asker.mac), 60.0, "trade network from the console")
        self.station.stop_hosting()
        self.station.on_beacon = None
        self.station.connect(network, self.profile.passphrase)
        self._emit("joined", channel=self.station.radio.channel)
        self._session(asker.player)

    def ask(self, seconds: float) -> None:
        """Find a console in the PSS, ask it for a trade, host the trade network once it answers."""
        self.station.on_beacon = self._on_beacon
        self._advertise(wire.pss_beacon(self.trainer))
        self._emit("searching", game=self.game.key, phase="pss")

        def target():
            for seen in self.players.values():
                if self.partner is None or seen.player.name.casefold() == self.partner.casefold():
                    return seen
            return None

        seen = self._hop(target, seconds, "console" + (f" with trainer {self.partner}" if self.partner else ""))
        token = os.urandom(4)
        self._say(f"asking {seen.player.name} for a trade; accept it on the console")
        self._emit("phase", phase="asking", partner=seen.player.name)
        self._advertise(wire.pss_beacon(self.trainer, partner=seen.player, request=True, token=token))
        self._hop(lambda: self.players[seen.player.pss_id].player.answers(self.trainer.pss_id) or None, 120.0,
                  "answer from the console")
        # The trade network goes where the console was last heard, the channel it listens on.
        channel = self.players[seen.player.pss_id].channel
        self.station.stop_hosting()
        self.station.on_beacon = None
        self.station.retune(channel)
        self.station.host(wlan_comm_id=self.profile.wlan_comm_id, passphrase=self.profile.passphrase,
                          channel=channel, max_nodes=TRADE_MAX_NODES, id_=TRADE_NETWORK)
        self._emit("hosting", game=self.game.key, channel=channel)
        deadline = time.monotonic() + 30.0
        while not any(p.connected and not p.spectator for p in self.station.peers.values()):
            if time.monotonic() > deadline:
                raise TimeoutError("the console answered but never joined the trade network")
            time.sleep(0.05)
        self._emit("partner-joined")
        self._session(seen.player)

    # -- the session ------------------------------------------------------------------------------

    def _session(self, partner: wire.PssPlayer) -> None:
        self._say(f"connected to {partner.name} as {self.station.role.value}, node {self.station.node_id}")
        self._emit("session", state="trading")
        trade = session.Gen6Session(self.station, self.trainer, self.party, partner.pss_id, self.trades,
                                    on_received=self._received, announce=self.announce)
        trade.run()

    def _received(self, record: bytes) -> None:
        n = len(self.saved) + 1
        path = received.save(record, self.output, game=self.game.key, trade=n, fmt=self.game.record)
        s = pkx.summary(record)
        self._say(f"[done] trade {n} complete: {path}")
        self._emit("received", trade=n, path=str(path), summary=f"species {s.species} '{s.nickname}' PID {s.pid:08X}",
                    files=[str(path)])
        self.saved.append(path)

    def run(self, role: str, seconds: float) -> list[Path]:
        if self.station.role is Role.CLIENT:
            raise RuntimeError("already connected")
        (self.ask if role == "host" else self.wait_to_be_asked)(seconds)
        return self.saved
