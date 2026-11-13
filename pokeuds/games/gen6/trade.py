"""A Generation 6 trade from the PSS: being seen, asking or being asked, then the trade network.

Every console in the PSS keeps hosting a small ``id=2`` network whose application data is its PSS
beacon (``wire.pss_beacon``), and listens for the others'. A trade request travels in those
beacons: the requester names the target, the target names the requester back, and the requester
then hosts the trade network (``id=1``, four nodes) that the target joins, its application data the requester's
record in state "trading" (``wire.trade_network_data``).

``role="join"``: PokeUDS shows up in the console's PSS and waits for the player there to ask it for
a trade, answers, and joins the console's trade network (PokeUDS is node 2).

``role="host"``: PokeUDS asks a console it hears in the PSS (the first, or the one whose trainer is
``partner``), waits for the answer, and hosts the trade network (PokeUDS is node 1).

Retail consoles advertise on channel 1, 6 or 11, so PokeUDS hops all three while it looks for one, its
own beacon moving with the radio. Once it has asked, it stays on the console's channel, as the game does.
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
# Once a console has asked (or been asked), PokeUDS stays on its channel and re-hosts its beacon this often, as the
# game does: a console that is waiting on an answer did not notice one sent while PokeUDS hopped channels.
REHOST_EVERY = 2.0
STAY_FOR = 8.0                     # then hop again: a console that accepted opens its trade network where it likes
TRADE_CHANNEL = 11              # Where PokeUDS first advertises.
ANSWER_WAIT = 120.0             # How long PokeUDS's request stays up for the player to accept.


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
    partner: str | None = None
    queue: list[bytes] = field(default_factory=list)   # Offered after ``offer``, one per trade (decrypted).     # As host: the trainer name to ask (default: the first heard).
    channels: tuple[int, ...] = PSS_CHANNELS
    announce: bool = True          # Print progress and emit status events (off for the demo console).
    saved: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self.players: dict[bytes, Seen] = {}
        self.trade_networks: dict[bytes, Beacon] = {}
        self.party = session.party_record(self.offer)
        self.queue_party = [session.party_record(o) for o in self.queue]
        self._logged: set[bytes] = set()          # Beacons already in the log.
        self._beacon_args: dict = {}              # What we advertise now (partner, request, token).

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
                if beacon.host not in self.trade_networks:
                    log.info("trade network from %s on channel %d (%d/%d nodes), data: %s", mac_str(beacon.host),
                             beacon.channel, beacon.info.total_nodes, beacon.info.max_nodes,
                             beacon.info.application_data.hex() or "none")
                self.trade_networks[beacon.host] = beacon
            elif beacon.info.id == PSS_NETWORK:
                data = beacon.info.application_data
                player = wire.parse_pss_beacon(data)
                if player is None or player.pss_id == self.trainer.pss_id:
                    return
                if data not in self._logged:
                    self._logged.add(data)
                    log.info("PSS beacon from %s (%s), attributes 0x%04X: %s", player.name, mac_str(beacon.host),
                             beacon.info.attributes, data.hex())
                if player.pss_id not in self.players:
                    log.info("PSS: %s (%s) on channel %d", player.name, mac_str(beacon.host), beacon.channel)
                    self._emit("found", name=player.name, channel=beacon.channel)
                self.players[player.pss_id] = Seen(player, beacon.host, beacon.channel, time.monotonic())

    def _advertise(self, **beacon_args) -> None:
        """Host our PSS network with the beacon for ``beacon_args`` (``wire.pss_beacon``'s partner, request, token)."""
        self._beacon_args = beacon_args
        beacon = wire.pss_beacon(self.trainer, **beacon_args)
        if beacon not in self._logged:
            self._logged.add(beacon)
            log.info("our PSS beacon: %s", beacon.hex())
        self.station.stop_hosting()
        self.station.host(wlan_comm_id=self.profile.wlan_comm_id, passphrase=self.profile.passphrase,
                          channel=self.station.radio.channel or TRADE_CHANNEL, max_nodes=PSS_MAX_NODES,
                          application_data=beacon, id_=PSS_NETWORK, attributes=wire.NETWORK_ATTRIBUTES)

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

    def _stay(self, channel: int, until, seconds: float, what: str):
        """Wait on ``channel``, re-hosting our beacon every REHOST_EVERY, for up to STAY_FOR; then hop for the rest."""
        deadline = time.monotonic() + seconds
        stay_until = min(deadline, time.monotonic() + STAY_FOR)
        self.station.retune(channel)
        next_host = time.monotonic() + REHOST_EVERY
        while time.monotonic() < stay_until:
            with self._lock:
                found = until()
            if found:
                return found
            if time.monotonic() >= next_host:
                self._advertise(**self._beacon_args)
                next_host = time.monotonic() + REHOST_EVERY
            time.sleep(0.02)
        log.info("nothing on channel %d for %.0f s; hopping again", channel, STAY_FOR)
        return self._hop(until, max(1.0, deadline - time.monotonic()), what)

    def _hold(self, channel: int, until, seconds: float, what: str):
        """Stay on ``channel`` for up to ``seconds``, re-hosting our beacon every REHOST_EVERY, and never hop.

        This is how the game asks: Pokemon X in Azahar kept every PSS network it hosted while asking on one channel
        (the radio parked there), and the retail console it asked, whose own PSS was on another channel, still
        found it and joined the trade network there. A request that hops 1/6/11 is on the wrong channel two thirds
        of the time, claiming a different channel each time, while the player reads the request and accepts."""
        deadline = time.monotonic() + seconds
        self.station.retune(channel)
        next_host = time.monotonic() + REHOST_EVERY
        while time.monotonic() < deadline:
            with self._lock:
                found = until()
            if found:
                return found
            if time.monotonic() >= next_host:
                self._advertise(**self._beacon_args)
                next_host = time.monotonic() + REHOST_EVERY
            time.sleep(0.02)
        raise TimeoutError(f"no {what} within {seconds:.0f} s on channel {channel}")

    # -- the two ways in --------------------------------------------------------------------------

    def wait_to_be_asked(self, seconds: float) -> None:
        """Show up in the PSS, answer the first trade request, join the requester's trade network."""
        self.station.on_beacon = self._on_beacon
        self._advertise()
        self._say(f"in the PSS as {self.trainer.name}; ask PokeUDS for a trade from the console")
        self._emit("searching", game=self.game.key, phase="pss")
        asker = self._hop(lambda: next((s for s in self.players.values() if s.player.wants(self.trainer.pss_id)), None),
                          seconds, "trade request")
        log.info("%s asks for a trade; answering", asker.player.name)
        self._emit("phase", phase="asked", partner=asker.player.name)
        self._advertise(partner=asker.player)
        network = self._stay(asker.channel, lambda: self.trade_networks.get(asker.mac), 60.0,
                             "trade network from the console")
        self.station.stop_hosting()
        self.station.on_beacon = None
        self.station.connect(network, self.profile.passphrase)
        self._emit("joined", channel=self.station.radio.channel)
        self._session(asker.player)

    def ask(self, seconds: float) -> None:
        """Find a console in the PSS, ask it for a trade, host the trade network once it answers."""
        self.station.on_beacon = self._on_beacon
        self._advertise()
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
        channel = seen.channel
        self.station.retune(channel)
        self._advertise(partner=seen.player, request=True, token=token)
        log.info("asking %s on channel %d, token %s; staying on this channel until it answers", seen.player.name,
                 channel, token.hex())
        def answered():
            player = self.players[seen.player.pss_id].player
            return player if player.answers(self.trainer.pss_id) else None

        answer = self._hold(channel, answered, ANSWER_WAIT, "answer from the console")
        log.info("%s answers (state 0x%02X, token %s)", seen.player.name, answer.state, answer.token.hex())
        if answer.token != token:
            log.warning("%s answered with token %s, not ours (%s)", seen.player.name, answer.token.hex(), token.hex())
        # The trade network goes on the channel the request was on, as the game's did, and carries our request in
        # state "trading" (wire.trade_network_data): the console joins the network that names it.
        data = wire.trade_network_data(self.trainer, seen.player, token)
        log.info("our trade network data: %s", data.hex())
        self.station.stop_hosting()
        self.station.on_beacon = None
        self.station.retune(channel)
        self.station.host(wlan_comm_id=self.profile.wlan_comm_id, passphrase=self.profile.passphrase,
                          channel=channel, max_nodes=TRADE_MAX_NODES, id_=TRADE_NETWORK,
                          application_data=data, attributes=wire.NETWORK_ATTRIBUTES)
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
                                    queue=self.queue_party,
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
