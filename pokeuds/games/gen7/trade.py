"""A Generation 7 trade from the Festival Plaza (Sun/Moon): being seen, inviting or being invited, the trade network.

Every player in the plaza hosts a presence network (``id=2``) whose application data (``plaza``) says who they are
and cycles through five records; an invite travels in those beacons. The other way round from Generation 6's PSS,
the INVITED player hosts the trade network (``id=1``, four nodes) with an accept in its data, and the inviter joins
it (read off Azahar logs of Sun/Moon Link Trades, each console inviting once):

``role="join"`` ("3DS asks"): PokeUDS shows up in the console's plaza and waits for the player there to invite it,
then accepts by hosting the trade network for the console to join (PokeUDS is node 1).

``role="host"`` ("PokeUDS asks"): PokeUDS invites a player it hears in the plaza (the first, or the one named
``partner``), waits for that console's trade network to accept, and joins it (PokeUDS is node 2).

Consoles host their plaza networks on channel 1, 6 or 11, so PokeUDS hops all three while it waits, its own beacon
moving with the radio and changing record every RECORD_EVERY, as a console's does.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from ...uds import Beacon
from ...util import mac_str
from ..gen6 import trade as gen6_trade
from . import plaza, session, wire

log = logging.getLogger(__name__)

PLAZA_CHANNELS = (1, 6, 11)
DWELL = 0.45
RECORD_EVERY = 1.2           # a console's presence data changes record about this often
STAY_FOR = 8.0
FIRST_CHANNEL = 11           # where both consoles of the captures kept their presence networks
JOIN_WAIT = 30.0


@dataclass
class Seen:
    player: plaza.Player
    mac: bytes
    channel: int
    at: float


@dataclass
class Gen7Trade(gen6_trade.Gen6Trade):
    trainer: wire.Trainer = field(default_factory=wire.Trainer)
    channels: tuple[int, ...] = PLAZA_CHANNELS
    answer_first: bool = False          # a PokeUDS standing in for a console's player (tests): ``Gen7Session``

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self.players: dict[int, Seen] = {}
        self.trade_networks: dict[bytes, tuple[Beacon, plaza.Player | None]] = {}
        self.party = session.party_record(self.offer)
        self.queue_party = [session.party_record(o) for o in self.queue]
        self._logged: set[bytes] = set()
        self._beacon_args: dict = {}
        self._turn = 0
        self._next_record = 0.0

    # -- beacons ----------------------------------------------------------------------------------

    def _on_beacon(self, beacon: Beacon) -> None:
        if beacon.info.wlan_comm_id not in self.profile.join_comm_ids():
            return
        data = beacon.info.application_data
        player = plaza.parse(data)
        with self._lock:
            if beacon.info.id == plaza.TRADE_NETWORK:
                if beacon.host not in self.trade_networks:
                    log.info("trade network from %s on channel %d%s", mac_str(beacon.host), beacon.channel,
                             f" ({player.name})" if player else "")
                self.trade_networks[beacon.host] = (beacon, player)
            elif beacon.info.id == plaza.PRESENCE_NETWORK and player and player.principal_id != self.trainer.principal_id:
                if player.slot in (plaza.SLOT_INVITE, plaza.SLOT_ACCEPT) and data not in self._logged:
                    self._logged.add(data)
                    log.info("plaza record %d from %s: token %08X naming %08X", player.slot, player.name,
                             player.token, player.other_pid)
                if player.principal_id not in self.players:
                    log.info("plaza: %s (%s) on channel %d", player.name, mac_str(beacon.host), beacon.channel)
                    self._emit("found", name=player.name, channel=beacon.channel)
                self.players[player.principal_id] = Seen(player, beacon.host, beacon.channel, time.monotonic())

    def _beacon(self) -> bytes:
        target = self._beacon_args.get("invite")
        if target is not None:
            return plaza.invite(self.trainer, self._beacon_args["token"], target.principal_id, target.id8)
        self._turn += 1
        return plaza.presence(self.trainer, self._turn)

    def _advertise(self, **beacon_args) -> None:
        """Host (or update) our presence network; ``invite=Player, token=int`` makes its data an invite."""
        if beacon_args or not self._beacon_args:
            self._beacon_args = beacon_args
        data = self._beacon()
        self._next_record = time.monotonic() + RECORD_EVERY
        hosting = self.station.network is not None and self.station.network.id == plaza.PRESENCE_NETWORK
        if hosting:
            self.station.set_application_data(data)
            return
        self.station.stop_hosting()
        self.station.host(wlan_comm_id=self.profile.wlan_comm_id, passphrase=self.profile.passphrase,
                          channel=self.station.radio.channel or FIRST_CHANNEL, max_nodes=plaza.MAX_NODES,
                          application_data=data, id_=plaza.PRESENCE_NETWORK, attributes=plaza.NETWORK_ATTRIBUTES)

    def _tick(self) -> None:
        if time.monotonic() >= self._next_record and self.station.network is not None:
            self._advertise()

    def _hop(self, until, seconds: float, what: str):
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
                self._tick()
                time.sleep(0.02)
        raise TimeoutError(f"no {what} within {seconds:.0f} s; is the console in the Festival Plaza with wireless on?")

    def _stay(self, channel: int, until, seconds: float, what: str):
        deadline = time.monotonic() + seconds
        stay_until = min(deadline, time.monotonic() + STAY_FOR)
        self.station.retune(channel)
        while time.monotonic() < stay_until:
            with self._lock:
                found = until()
            if found:
                return found
            self._tick()
            time.sleep(0.02)
        log.info("nothing on channel %d for %.0f s; hopping again", channel, STAY_FOR)
        return self._hop(until, max(1.0, deadline - time.monotonic()), what)

    # -- the two ways in --------------------------------------------------------------------------

    def wait_to_be_asked(self, seconds: float) -> None:
        """Show up in the plaza, accept the first invite by hosting the trade network, trade as its host."""
        self.station.on_beacon = self._on_beacon
        self.station.retune(FIRST_CHANNEL)
        self._advertise()
        self._say(f"in the Festival Plaza as {self.trainer.name}; invite PokeUDS to a Link Trade from the console")
        self._emit("searching", game=self.game.key, phase="plaza")
        asker = self._hop(lambda: next((s for s in self.players.values() if s.player.invites(self.trainer)), None),
                          seconds, "trade invite")
        log.info("%s invites PokeUDS to trade; accepting", asker.player.name)
        self._emit("phase", phase="asked", partner=asker.player.name)
        self.station.stop_hosting()
        self.station.on_beacon = None
        self.station.retune(asker.channel)
        self.station.host(wlan_comm_id=self.profile.wlan_comm_id, passphrase=self.profile.passphrase,
                          channel=asker.channel, max_nodes=plaza.MAX_NODES, id_=plaza.TRADE_NETWORK,
                          application_data=plaza.accept(self.trainer, asker.player.token, asker.player.principal_id,
                                                        asker.player.id8),
                          attributes=plaza.NETWORK_ATTRIBUTES)
        self._emit("hosting", game=self.game.key, channel=asker.channel)
        deadline = time.monotonic() + JOIN_WAIT
        while not any(p.connected and not p.spectator for p in self.station.peers.values()):
            if time.monotonic() > deadline:
                raise TimeoutError("the console invited PokeUDS but never joined its trade network")
            time.sleep(0.05)
        self._emit("partner-joined")
        self._session(asker.player.name, clock=None)

    def ask(self, seconds: float) -> None:
        """Find a player in the plaza, invite them, join the trade network their console hosts to accept."""
        self.station.on_beacon = self._on_beacon
        self.station.retune(FIRST_CHANNEL)
        self._advertise()
        self._emit("searching", game=self.game.key, phase="plaza")

        def target():
            for seen in self.players.values():
                if self.partner is None or seen.player.name.casefold() == self.partner.casefold():
                    return seen
            return None

        seen = self._hop(target, seconds, "player" + (f" named {self.partner}" if self.partner else ""))
        token = wire.stamp()
        self._say(f"inviting {seen.player.name} to a Link Trade; accept it on the console")
        self._emit("phase", phase="asking", partner=seen.player.name)
        self._advertise(invite=seen.player, token=token)

        def accepted():
            for beacon, player in self.trade_networks.values():
                if player is not None and player.accepts(self.trainer, token):
                    return beacon
            return None

        network = self._stay(seen.channel, accepted, 120.0, "answer from the console")
        self.station.stop_hosting()
        self.station.on_beacon = None
        self.station.connect(network, self.profile.passphrase)
        self._emit("joined", channel=self.station.radio.channel)
        self._session(seen.player.name, clock=token)

    # -- the session ------------------------------------------------------------------------------

    def _session(self, partner: str, clock: int | None) -> None:
        self._say(f"connected to {partner} as {self.station.role.value}, node {self.station.node_id}")
        self._emit("session", state="trading")
        trade = session.Gen7Session(self.station, self.trainer, self.party, trades=self.trades, queue=self.queue_party,
                                    on_received=self._received, announce=self.announce, person_clock=clock,
                                    answer_first=self.answer_first)
        trade.run()
