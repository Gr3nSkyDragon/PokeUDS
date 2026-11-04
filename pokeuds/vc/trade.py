"""A Virtual Console trade end to end: UDS, the Pia session, the Game Boy engine, the output folder.

Modes, by what each side runs:

| our offer | the 3DS runs | mode | on the wire | we save |
|---|---|---|---|---|
| .pk1 | Red/Blue/Yellow | ``gen1`` | Gen 1 | .pk1 |
| .pk2 | Gold/Silver/Crystal | ``gen2`` | Gen 2, with mail | .pk2 (+ .mail if it holds mail) |
| .pk1 | Gold/Silver/Crystal in the Time Capsule | ``timecapsule`` (we are Gen 1) | Gen 1 | .pk1 |
| .pk2 | Red/Blue/Yellow | ``timecapsule`` (we are Gen 2) | Gen 1 | .pk2 as a Gen 2 game converts it, and the .pk1 |
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import gbpk
from ..games import vc as titles
from ..uds import BROADCAST_NODE, Role as UdsRole, Station
from ..util import mac_str
from . import link, pia
from .session import Config, Role, Session, State

log = logging.getLogger(__name__)
_UNSAFE = re.compile(r"[^\w\-]+")


def choose_mode(offer: gbpk.Record, partner_generation: int) -> link.LinkMode:
    if offer.generation == partner_generation:
        return link.GEN1 if offer.generation == 1 else link.GEN2
    return link.TIME_CAPSULE


def wire_offer(offer: gbpk.Record, mode: link.LinkMode) -> gbpk.Record:
    """What goes on the cable for our offer: a Gen 2 Pokemon crosses the Time Capsule as Gen 1."""
    if mode is link.TIME_CAPSULE and offer.generation == 2:
        return gbpk.gen2_to_gen1(offer)
    return offer


def host_application_data() -> bytes:
    # 16 bytes shaped like the retail host's (08, seven per-session bytes, 01, zeros). Their meaning,
    # and whether a joining VC checks them, is not known yet.
    return b"\x08" + os.urandom(7) + b"\x01" + bytes(7)


def save(record: gbpk.Record, folder: Path, *, game: str, trade: int, stamp: str, suffix: str = "") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    nick = _UNSAFE.sub("", gbpk.decode_name(record.nickname))[:12] or "unnamed"
    path = folder / f"{stamp}_{game}_trade{trade}_{record.dex:03d}_{nick}_{record.ot_id:05d}{suffix}.{record.extension}"
    path.write_bytes(record.to_file())
    return path


@dataclass
class VcTrade:
    station: Station
    offer: gbpk.Record                  # as the user gave it (.pk1 or .pk2)
    output: Path
    trades: int = 1
    trainer_name: str = "POKEUDS"
    offer_mail: bytes = b""
    saved: list[Path] = field(default_factory=list)
    mode: link.LinkMode | None = None
    session: Session | None = None
    engine: link.TradeEngine | None = None
    partner_generation: int | None = None
    _stamp: str = field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S"))

    # -- the UDS part ---------------------------------------------------------------------------

    def join(self, seconds: float = 60.0, generation: int | None = None) -> None:
        """Find a VC host (of ``generation``, or either) and join it as node 2."""
        wanted = [generation] if generation else [1, 2]
        found = self.station.scan(seconds, until=lambda b: titles.generation_of(b.info.wlan_comm_id) in wanted)
        hosts = [b for b in found if titles.generation_of(b.info.wlan_comm_id) in wanted]
        if not hosts:
            raise TimeoutError("no Virtual Console host heard; choose the host option in the VC link menu")
        target = max(hosts, key=lambda b: b.rssi if b.rssi is not None else -100)
        self.partner_generation = titles.generation_of(target.info.wlan_comm_id)
        title = titles.title_of(target.info.wlan_comm_id)
        log.info("joining %s (%s) on channel %d", title.name if title else f"comm {target.info.wlan_comm_id:08X}",
                 mac_str(target.host), target.channel)
        self.station.connect(target, titles.PASSPHRASE)
        self._open(Role.JOINER, target.info.application_data)

    def host(self, title: titles.VcTitle, partner_generation: int, seconds: float = 120.0) -> None:
        """Open a network as ``title`` and wait for the 3DS to join. Experimental (see session.py)."""
        appdata = host_application_data()
        self.station.host(wlan_comm_id=title.comm_id, passphrase=titles.PASSPHRASE, channel=titles.CHANNEL,
                          max_nodes=2, application_data=appdata)
        self.partner_generation = partner_generation
        deadline = time.monotonic() + seconds
        while not any(p.connected for p in self.station.peers.values()):
            if time.monotonic() > deadline:
                raise TimeoutError(f"no console joined within {seconds:.0f} s")
            time.sleep(0.05)
        self._open(Role.HOST, appdata)

    def _open(self, role: Role, application_data: bytes) -> None:
        self.mode = choose_mode(self.offer, self.partner_generation)
        sent = wire_offer(self.offer, self.mode)
        party = link.Party(gbpk.encode_name(self.trainer_name), self.offer.ot_id, [sent],
                           [self.offer_mail] if self.mode is link.GEN2 else [])
        self.engine = link.TradeEngine(self.mode, party, trades=self.trades, on_trade=self._on_trade)
        peer_node = 1 if role is Role.JOINER else 2

        def send(frame: bytes, broadcast: bool) -> None:
            try:
                self.station.send(BROADCAST_NODE if broadcast else peer_node, pia.UDS_CHANNEL, frame)
            except Exception as exc:
                log.debug("UDS send failed: %s", exc)

        self.session = Session(role, send, Config(name=self.trainer_name, application_data=application_data))
        self._role = role
        self._first_sent = False
        log.info("VC %s trade, %s, offering %s", self.mode.key, role.value, sent.describe())

    # -- the game part --------------------------------------------------------------------------

    def _on_trade(self, trade: link.Trade) -> None:
        n = len(self.engine.trades)
        game = {link.GEN1: "rby", link.GEN2: "gsc"}.get(self.mode, "timecapsule")
        received = trade.received
        if self.mode is link.TIME_CAPSULE and self.offer.generation == 2:
            # We play a Gen 2 game: keep what arrived and what a Gen 2 game would make of it.
            self.saved.append(save(received, self.output, game=game, trade=n, stamp=self._stamp))
            received = gbpk.gen1_to_gen2(received)
        path = save(received, self.output, game=game, trade=n, stamp=self._stamp)
        self.saved.append(path)
        if trade.received_mail:
            mail = path.with_suffix(".mail")
            mail.write_bytes(trade.received_mail)
            self.saved.append(mail)
        print(f"[done] trade {n} complete: {received.describe()} -> {path}")

    def run(self, seconds: float | None = None) -> None:
        """Drive the session until it closes, the trades are done and the partner leaves, or ``seconds`` pass."""
        clock0 = time.monotonic()
        now = lambda: int((time.monotonic() - clock0) * 1000)
        self.session.start(now())
        deadline = None if seconds is None else time.monotonic() + seconds
        while self.session.state is not State.CLOSED and self.station.role in (UdsRole.HOST, UdsRole.CLIENT):
            if deadline and time.monotonic() > deadline:
                break
            packet = self.station.recv(pia.UDS_CHANNEL, timeout=0.005)
            while packet is not None:
                self.session.receive(now(), packet.payload)
                packet = self.station.recv(pia.UDS_CHANNEL, timeout=0)
            self.step(now())
        if self.session.state is State.CLOSED:
            log.info("the VC session closed")

    def step(self, now: int) -> None:
        session, engine = self.session, self.engine
        session.poll(now)
        if session.state is State.JOINED and self._role is Role.HOST and not self._first_sent:
            self._first_sent = session.queue_unit(0xEF)        # the host's first unit, before the joiner's
        for byte in session.pop_units():
            session.queue_unit(engine.respond(byte))           # joiner: unit k; host: unit k+1
        session.flush(now)
