"""One Generation 6 trade session on an established UDS network: PRUDP, Net-Z, gflnet and the trade.

The console and PokeUDS each play their part of the same script, the order in which X sent its
messages in every captured session (``wire`` has the message formats):

1. PRUDP: each side connects to the other (the joiner first).
2. Net-Z: the host announces its objects, the client its own; ``wire.netz_host``/``netz_client``
   replay X's messages with the per-session values filled in. The client then streams clock
   heartbeats, which the host echoes.
3. gflnet: the host's first message, then sync 0x11, the PSS profiles, syncs 0x13 and 0x81.
4. The trade screen (module 1): state 08, block 09; each side offers (08 "offering" if first, then
   01 with the Pokemon and 06), confirms (07), and finishes (0a). After the swap animation: sync
   0x82, state 08, the host's 0b, syncs 0xCB-0xCD, and the screen opens again (09).
5. Leaving: quit (05), the next hub sync, the leave block (00), syncs 0x0A (module 1) and 0x16.

PokeUDS never waits on a human: it answers each step as soon as the console has made it, and offers
right away. Every sync the console requests is answered at once.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ... import pkx, status
from ...nex import prudp
from ...uds import Role, Station
from ...uds.station import UdsError
from . import wire

log = logging.getLogger(__name__)

DATA_CHANNEL = 243
HEARTBEAT_BURST, HEARTBEAT_BURST_EVERY, HEARTBEAT_EVERY = 16, 0.033, 2.0
HOST_KEEPALIVE_FIRST, HOST_KEEPALIVE_EVERY = 1.0, 5.0
CLIENT_NETZ_TAIL_DELAY = 0.25
# Sync points: PokeUDS answers the console's request at once, and asks first only after SYNC_ASK_AFTER without one,
# then again every SYNC_RESEND. A console drops a request that arrives before it reaches the point: one sent right
# after a swap, while the console still played the trade animation (about 27 s), left it at "Communicating".
SYNC_ASK_AFTER, SYNC_RESEND = 2.0, 8.0
# The trade screen's block (command 09): sent when the console sends its own, or after this long. A console's
# client sends it about 4 s after the last sync, once its screen is up; PokeUDS sending it at once, while the console
# still changed screens, left the console at "Communicating" after a trade.
SCREEN_AFTER = 4.5
# After the last sync point the host says goodbye in Net-Z and the session simply ends: no console sends a PRUDP
# disconnect. The client waits this long for the goodbye.
GOODBYE_WAIT = 5.0
YES_BYTE = wire.YES[:1]


class TradeEnded(RuntimeError):
    pass


@dataclass
class Gen6Session:
    # What differs by generation (Gen7Session overrides these): the gflnet framing, the Net-Z templates and how many
    # objects Net-Z sets up (the host's messages after its first three, and the answer pairs it sends the client).
    GENERATION = 6
    NETZ_OBJECTS = 10
    NETZ_PAIRS = 6
    HEARTBEAT_OBJECT = 3
    # The index a count report sent on receiving a message carries, past our last message's: X/Y's is the last one's
    # own, Sun/Moon's the next one's (every acknowledgment in the Sun/Moon captures).
    ACK_INDEX_AHEAD = 0

    def netz_host_messages(self, call_id: int, nonce: bytes) -> list[bytes]:
        return wire.netz_host(call_id, nonce)

    def netz_client_messages(self, call_id: int) -> list[bytes]:
        return wire.netz_client(call_id)

    def host_init_bodies(self) -> list[bytes]:
        """The host's first gflnet messages once Net-Z is up."""
        return [template_init()]

    station: Station
    trainer: wire.Trainer
    offer: bytes                         # The Pokemon we offer: 260 bytes, decrypted, party format.
    partner_id: bytes = bytes(8)
    trades: int = 1                      # How many trades before PokeUDS leaves the trade screen.
    queue: list[bytes] = field(default_factory=list)   # Offered after ``offer``, one per trade, the same form.
    on_received: Callable[[bytes], None] | None = None
    clock: Callable[[], float] = time.monotonic
    step_timeout: float = 300.0          # Longest wait for the console (a human picks and confirms).
    announce: bool = True                # Emit status events.
    received: list[bytes] = field(default_factory=list)

    def __post_init__(self) -> None:
        if any(len(o) != pkx.PARTY_SIZE or not pkx.checksum_valid(o) for o in (self.offer, *self.queue)):
            raise ValueError("the offer must be a decrypted 260-byte party record")
        self._offers = [self.offer, *self.queue]
        self._trade_no = 0
        self.node = self.station.node_id
        self.peer = 2 if self.node == 1 else 1
        self.host = self.node == 1
        self.ep = prudp.Endpoint(self._send_uds, self.node, self.peer, client=not self.host, clock=self.clock)
        self._start = self.clock()
        # Net-Z
        self._call_id = 0x02
        self._netz_in = 0
        self._netz_sent = 0
        self._netz_client_tail_at: float | None = None
        self._host_messages = self.netz_host_messages(self._call_id, os.urandom(4)) if self.host else []
        self._client_messages: list[bytes] = []
        self._pairs_sent = 0
        self._client_tail = 0
        self._quit_answered = False
        self._host_said_goodbye = False
        self._hb_counter = 0x08 if not self.host else 0x03
        self._hb_sent = 0
        self._hb_next: float | None = None
        self._keepalive_next: float | None = None
        self.netz_done = False
        # gflnet
        self._index = 0
        self._counts = {1: 0, 2: 0}
        self._inbox: list[wire.Body] = []
        self._syncs: dict[tuple[int, int], dict[str, bool]] = {}
        self._hub = wire.SYNC_HUB
        self._peer_profile: bytes | None = None
        self._last_progress = self.clock()

    def _emit(self, event: str, **fields) -> None:
        if self.announce:
            status.emit(event, **fields)

    # -- transport --------------------------------------------------------------------------------

    def _send_uds(self, payload: bytes) -> None:
        if self.station.role in (Role.HOST, Role.CLIENT):
            try:
                self.station.send(self.peer, DATA_CHANNEL, payload)
            except UdsError:
                raise TradeEnded("the console left the network") from None

    def _now_ms(self) -> int:
        return int((self.clock() - self._start) * 1000) + 4000

    def pump(self, wait: float = 0.02) -> None:
        """Receive, react, run the timers once."""
        packet = self.station.recv(DATA_CHANNEL, timeout=wait)
        while packet is not None:
            if packet.src_node == self.peer:
                for message in self.ep.feed(packet.payload):
                    self._dispatch(message)
            packet = self.station.recv(DATA_CHANNEL, timeout=0)
        self.ep.tick()
        self._netz_timers()
        if self.ep.closed:
            raise TradeEnded("the console left" if self.ep.close_reason == "disconnect" else
                             "the console stopped acknowledging")
        if self.station.role not in (Role.HOST, Role.CLIENT):
            raise TradeEnded("the UDS network is gone")

    def _dispatch(self, message: prudp.Message) -> None:
        self._last_progress = self.clock()
        if message.stream == "game":
            self._game_message(message.payload)
        elif message.stream == "netz":
            self._netz_message(message.payload)
        elif wire.is_heartbeat(message.payload) and self.host:
            self.ep.send_unreliable(wire.heartbeat_host(self._hb_counter, message.payload[wire.HB_TIME:wire.HB_TIME + 4],
                                                        self._now_ms(), self.HEARTBEAT_OBJECT))
            self._hb_counter += 1

    # -- Net-Z ------------------------------------------------------------------------------------

    def _netz_send(self, messages: list[bytes]) -> None:
        for m in messages:
            log.debug("us: Net-Z %02X, %d B (PRUDP out %s)", wire.netz_kind(m), len(m), self.ep.outbound)
            self.ep.send_reliable(m)

    def _netz_message(self, message: bytes) -> None:
        if not self.host and self.netz_done and wire.is_host_goodbye(message):
            self._host_said_goodbye = True
            return
        self._netz_in += 1
        n = self._netz_in
        log.debug("console: Net-Z #%d %02X, %d B (PRUDP out %s)", n, wire.netz_kind(message), len(message),
                  self.ep.outbound)
        if self.host:
            if n == 1 and self.ep.outbound == "idle":
                self.ep.open(wire.HOST_CONNECT_PAYLOAD)
            elif n == 4:
                self._netz_send(self._host_messages[3:3 + self.NETZ_OBJECTS])
            elif wire.netz_kind(message) == 0x0A and self._pairs_sent < self.NETZ_PAIRS:
                start = 3 + self.NETZ_OBJECTS + 2 * self._pairs_sent
                self._netz_send(self._host_messages[start:start + 2])
                self._pairs_sent += 1
            elif wire.netz_kind(message) == 0x02 and not self.netz_done:
                self._client_tail += 1      # The client's last two messages (its station's URL).
                if self._client_tail == 2:
                    self.netz_done = True
                    log.info("Net-Z set up (host)")
                    for body in self.host_init_bodies():
                        self._gfl_send(body)
        else:
            if n == 2:
                self._call_id = message[wire.NETZ_CALL_OFFSET]
            if n == 3:
                self._client_messages = self.netz_client_messages(self._call_id)
                self._netz_send(self._client_messages[1:4])
            elif n == 3 + self.NETZ_OBJECTS:
                self._netz_send(self._client_messages[4:4 + self.NETZ_PAIRS])
                self._hb_next = self.clock()
            elif n == 5 + self.NETZ_OBJECTS and self._netz_client_tail_at is None:
                self._netz_client_tail_at = self.clock() + CLIENT_NETZ_TAIL_DELAY

    def _netz_timers(self) -> None:
        now = self.clock()
        if self.host:
            if self.ep.outbound == "open" and self._netz_sent == 0:
                self._netz_send(self._host_messages[0:3])
                self._netz_sent = 3
            if self._pairs_sent == self.NETZ_PAIRS:   # X's first keepalive follows its replies by about a second.
                if self._keepalive_next is None:
                    self._keepalive_next = now + HOST_KEEPALIVE_FIRST
                elif now >= self._keepalive_next:
                    self.ep.send_reliable(wire.host_keepalive(self._hb_counter))
                    self._hb_counter += 1
                    self._keepalive_next = now + HOST_KEEPALIVE_EVERY
            return
        if self.ep.outbound == "idle":
            self.ep.open(wire.CLIENT_CONNECT_PAYLOAD)
        if self.ep.outbound == "open" and self._netz_sent == 0:
            self.ep.send_reliable(self.netz_client_messages(self._call_id)[0])
            self._netz_sent = 1
        if self._netz_client_tail_at is not None and now >= self._netz_client_tail_at and not self.netz_done:
            self._netz_send(self._client_messages[4 + self.NETZ_PAIRS:6 + self.NETZ_PAIRS])
            self.netz_done = True
            log.info("Net-Z set up (client)")
        if self._hb_next is not None and now >= self._hb_next:
            self.ep.send_unreliable(wire.heartbeat_client(self._hb_counter, self._now_ms(), self.HEARTBEAT_OBJECT))
            self._hb_counter += 1
            self._hb_sent += 1
            self._hb_next = now + (HEARTBEAT_BURST_EVERY if self._hb_sent < HEARTBEAT_BURST else HEARTBEAT_EVERY)

    # -- gflnet -----------------------------------------------------------------------------------

    def _counts_word(self) -> int:
        return wire.counts_word(self._counts[1], self._counts[2])

    def _gfl_send(self, body: bytes) -> None:
        if log.isEnabledFor(logging.DEBUG):
            log.debug("us: %s", describe(wire.parse_body(body)))
        self._index += 1
        self.ep.send_reliable(wire.gfl_encode(self._index, self._counts_word(), body, self.GENERATION), game=True)
        self._counts[self.node] = self._index
        self.ep.send_reliable(wire.gfl_encode(self._index, self._counts_word(), b"", self.GENERATION), game=True)

    def _game_message(self, raw: bytes) -> None:
        try:
            m = wire.gfl_decode(raw)
        except ValueError as exc:
            log.warning("dropping a gflnet message: %s", exc)
            return
        if not m.body:
            return
        if m.index <= self._counts[self.peer]:
            return
        self._counts[self.peer] = m.index
        self.ep.send_reliable(wire.gfl_encode(self._index + self.ACK_INDEX_AHEAD, self._counts_word(), b"",
                                              self.GENERATION), game=True)
        for body in wire.parse_bodies(m.body):
            if body.kind == "sync":
                self._peer_sync(body)
            else:
                log.debug("console: %s", describe(body))
                self._inbox.append(body)

    # -- syncs ------------------------------------------------------------------------------------

    def _sync(self, ident: int, module: int, *, fresh: bool = False) -> dict[str, bool]:
        """The current round of a sync point. Ids repeat (0xCB-0xCD every trade), so a round gives way
        to a new one once PokeUDS has passed it; a round the console finished early (it asked before
        PokeUDS got there, and PokeUDS answered at once) still counts for the step that comes to it."""
        s = self._syncs.get((module, ident))
        if s is None or (fresh and s["passed"]):
            s = self._syncs[(module, ident)] = {"mine": False, "peer": False, "done": False, "passed": False}
        return s

    def _request_sync(self, ident: int, module: int) -> None:
        s = self._sync(ident, module)
        if not s["mine"]:
            s["mine"] = True
            self._gfl_send(wire.sync(wire.SYNC_REQUEST, module, ident))
        self._maybe_finish_sync(ident, module)

    def _maybe_finish_sync(self, ident: int, module: int) -> None:
        s = self._sync(ident, module)
        if self.host and s["mine"] and s["peer"] and not s["done"]:
            s["done"] = True
            self._gfl_send(wire.sync(wire.SYNC_DONE, module, ident))

    def _peer_sync(self, body: wire.Body) -> None:
        log.debug("console: %s", describe(body))
        s = self._sync(body.ident, body.module, fresh=not body.done)
        if body.done:
            s["done"] = True
            return
        s["peer"] = True
        self._request_sync(body.ident, body.module)     # Answer every request at once.

    def sync_point(self, ident: int, module: int = wire.SESSION) -> None:
        s = self._sync(ident, module, fresh=True)
        self._last_progress = start = last_ask = self.clock()
        while not s["done"]:
            now = self.clock()
            if not s["mine"] and now - start >= SYNC_ASK_AFTER:
                self._request_sync(ident, module)
                last_ask = now
            elif s["mine"] and now - last_ask >= SYNC_RESEND:
                log.debug("sync %02X: asking again", ident)
                self._gfl_send(wire.sync(wire.SYNC_REQUEST, module, ident))
                last_ask = now
            if now - self._last_progress > self.step_timeout:
                raise TimeoutError(f"the console stopped answering (waiting for sync {ident:02X})")
            try:
                self.pump()
            except TradeEnded as exc:
                raise TradeEnded(f"{exc} while PokeUDS waited for sync {ident:02X}") from None
        s["passed"] = True

    # -- waiting ----------------------------------------------------------------------------------

    def wait(self, condition: Callable[[], bool], what: str) -> None:
        self._last_progress = self.clock()
        while not condition():
            if self.clock() - self._last_progress > self.step_timeout:
                raise TimeoutError(f"the console stopped answering (waiting for {what})")
            try:
                self.pump()
            except TradeEnded as exc:
                raise TradeEnded(f"{exc} while PokeUDS waited for {what}") from None

    def wait_or(self, condition: Callable[[], bool], seconds: float) -> None:
        """Run the session until ``condition`` or for ``seconds``, whichever comes first."""
        deadline = self.clock() + seconds
        while not condition() and self.clock() < deadline:
            self.pump()

    def take(self, module: int, cmd: int, what: str, *, also: tuple[int, ...] = (),
             drop_before: Callable[[wire.Body], bool] | None = None) -> wire.Body:
        """Wait for the console's next command ``cmd`` (or one of ``also``) in ``module``. ``drop_before`` also
        removes the messages it matches that arrived before that command (the inbox keeps arrival order)."""
        found: list[wire.Body] = []

        def ready() -> bool:
            for i, body in enumerate(self._inbox):
                if body.kind == "command" and body.module == module and body.ident in (cmd, *also):
                    earlier = self._inbox[:i]
                    if drop_before is not None:
                        earlier = [b for b in earlier if not drop_before(b)]
                    self._inbox = earlier + self._inbox[i + 1:]
                    found.append(body)
                    return True
            return False

        self.wait(ready, what)
        return found[0]

    def _command(self, module: int, cmd: int, data: bytes) -> None:
        self._gfl_send(wire.command(module, cmd, data))

    def _peer_sent(self, module: int, cmd: int) -> bool:
        return any(b.kind == "command" and b.module == module and b.ident == cmd for b in self._inbox)

    # -- the trade --------------------------------------------------------------------------------

    def run(self) -> list[bytes]:
        self.wait(lambda: self.netz_done, "Net-Z setup")
        self._emit("phase", phase="session")
        if not self.host:
            self.wait(lambda: any(b.kind == "init" for b in self._inbox), "the host's first message")
            self._inbox = [b for b in self._inbox if b.kind != "init"]
        self.sync_point(wire.SYNC_START)
        self._command(wire.SESSION, wire.CMD_PROFILE, wire.profile(self.trainer, self.partner_id, self.node))
        peer_profile = self.take(wire.SESSION, wire.CMD_PROFILE, "the console's profile")
        self._peer_profile = peer_profile.data
        partner = peer_profile.data[wire.P_NAME:wire.P_NAME + wire.NAME_BYTES].decode("utf-16-le", "replace").split("\0")[0]
        log.info("trading with %s", partner)
        self._emit("phase", phase="profiles", partner=partner)
        self.sync_point(wire.SYNC_PROFILES)
        self.sync_point(self._hub)
        self._command(wire.TRADE, wire.CMD_STATE, wire.STATE_IDLE)
        self.take(wire.TRADE, wire.CMD_STATE, "the trade screen")
        # The trade screen opens before every trade and once more after the last: players leave from it (exit,
        # then confirm), and PokeUDS does too once it has traded ``trades`` times, or as soon as the console's player
        # leaves.
        done = 0
        while True:
            self._open_screen()
            if done >= self.trades or not self._one_trade():
                break
            done += 1
        self._leave()
        return self.received

    def _open_screen(self) -> None:
        """Both sides open the trade screen (command 09). Ours goes when the console's comes, or after SCREEN_AFTER."""
        self.wait_or(lambda: self._peer_sent(wire.TRADE, wire.CMD_SCREEN), SCREEN_AFTER)
        self._command(wire.TRADE, wire.CMD_SCREEN, wire.screen_block())
        self.take(wire.TRADE, wire.CMD_SCREEN, "the console's trade screen")

    def _one_trade(self) -> bool:
        """One offer and swap on the open trade screen. False when the console left the trade screen instead."""
        while True:
            record = self._offer_and_confirm()
            if record is None:
                return False
            if record:
                break
            log.info("the console declined the trade; offering again")
        self._command(wire.TRADE, wire.CMD_DONE, wire.DONE)
        self.take(wire.TRADE, wire.CMD_DONE, "the swap")
        self.received.append(record)
        self._trade_no += 1
        if self.on_received:
            self.on_received(record)
        self._emit("phase", phase="swapped")
        self._hub += 1
        self.sync_point(self._hub)
        self._command(wire.TRADE, wire.CMD_STATE, wire.STATE_IDLE)
        self.take(wire.TRADE, wire.CMD_STATE, "the trade screen to reopen")
        if self.host:
            self._command(wire.TRADE, wire.CMD_AFTER, bytes([os.urandom(1)[0], 0]))
        else:
            self.take(wire.TRADE, wire.CMD_AFTER, "the host's after-trade step")
        for ident in wire.SYNC_AFTER_TRADE:
            self.sync_point(ident, wire.TRADE)
        return True

    def _offer_and_confirm(self) -> bytes | None:
        """Offer, take the console's offer, confirm. The console's record once both confirmed, b""
        if it declined, None if it left the trade screen."""
        self._emit("phase", phase="offer")
        if not self._peer_sent(wire.TRADE, wire.CMD_POKEMON):
            self._command(wire.TRADE, wire.CMD_STATE, wire.STATE_OFFERING)
        # The queue in order (round again past its end, when asked for more trades than it holds).
        self._command(wire.TRADE, wire.CMD_POKEMON, pkx.encrypt(self._offers[self._trade_no % len(self._offers)]))
        self._command(wire.TRADE, wire.CMD_OFFERED, wire.OFFERED)
        while True:
            body = self.take(wire.TRADE, wire.CMD_POKEMON, "the console's offer", also=(wire.CMD_QUIT,))
            if body.ident == wire.CMD_QUIT:
                self._quit_answered = True
                return None
            try:
                record = pkx.decrypt(body.data)
            except ValueError:
                record = b""
            if pkx.checksum_valid(record):
                break
            log.warning("the console's offer fails its checksum; waiting for another")
        s = pkx.summary(record)
        log.info("the console offers species %d %r", s.species, s.nickname)
        self._emit("phase", phase="their-offer", summary=f"species {s.species} '{s.nickname}'")
        self.take(wire.TRADE, wire.CMD_OFFERED, "the console's offer to settle")
        self._command(wire.TRADE, wire.CMD_CONFIRM, wire.YES)
        self._emit("phase", phase="confirm")
        answer = self.take(wire.TRADE, wire.CMD_CONFIRM, "the console's confirmation", also=(wire.CMD_QUIT,))
        if answer.ident == wire.CMD_QUIT:
            self._quit_answered = True
            return None
        return record if answer.data[:1] == YES_BYTE else b""

    def _leave(self) -> None:
        self._emit("phase", phase="leaving")
        self._command(wire.TRADE, wire.CMD_QUIT, wire.YES)
        if not self._quit_answered:
            self.take(wire.TRADE, wire.CMD_QUIT, "the console to leave the trade screen")
        self._hub += 1
        self.sync_point(self._hub)
        self._command(wire.TRADE, wire.CMD_LEAVE_DATA, wire.leave_data(self.trainer))
        self.take(wire.TRADE, wire.CMD_LEAVE_DATA, "the console's leave block")
        self.sync_point(wire.SYNC_LEAVE, wire.TRADE)
        self.sync_point(wire.SYNC_END)
        self._emit("phase", phase="left")
        # End as a console does: the host says goodbye, the client waits for it, nobody disconnects PRUDP. The
        # consoles then ask their players about registering friends on their own, with no traffic.
        if self.host:
            self.ep.send_reliable(wire.host_keepalive(self._hb_counter, goodbye=True))
            done = lambda: self.ep.idle
        else:
            done = lambda: self._host_said_goodbye
        try:
            self.wait_or(done, GOODBYE_WAIT)
        except TradeEnded:
            pass         # The console took the network down first, as it may once the last sync point is done.


def template_init() -> bytes:
    return wire.template(wire.templates.HOST_INIT)


def party_record(offer: bytes, game: str = "xy") -> bytes:
    """The 260-byte party form of ``offer`` (decrypted). A box record gets its battle stats from the
    PKHeX helper when it is built; otherwise they are left zero, which a console shows as level 0."""
    if len(offer) == pkx.PARTY_SIZE:
        return offer
    try:
        from ... import pkhex

        built = pkhex.shared().check(game, offer)
        data = built.data
        # The checksum covers only the box part, so check the party part has stats too (level, max HP).
        if (len(data) == pkx.PARTY_SIZE and pkx.checksum_valid(data) and data[PARTY_LEVEL]
                and data[PARTY_MAX_HP] | data[PARTY_MAX_HP + 1]):
            return data
        log.warning("the PKHeX helper returned no party stats for the offer; update it, or offer a party .pk6")
    except Exception as exc:  # The helper is optional.
        log.warning("no party stats for the offer (%s); get the PKHeX helper or offer a party .pk6", exc)
    return offer + bytes(pkx.PARTY_SIZE - len(offer))


PARTY_LEVEL, PARTY_MAX_HP = 0xEC, 0xF2     # In the party block (PKHeX PK6: Stat_Level, Stat_HPMax).


def trainer_from_offer(offer: bytes, version: int, settings: dict | None = None) -> wire.Trainer:
    """Our PSS identity: the settings' trainer, else the offered Pokemon's original trainer."""
    settings = settings or {}
    s = pkx.summary(offer)
    pss_id = bytes.fromhex(settings["pss_id"]) if settings.get("pss_id") else os.urandom(8)
    key = bytes.fromhex(settings["pss_key"]) if settings.get("pss_key") else os.urandom(4)
    return wire.Trainer(settings.get("name") or s.ot_name or "PokeUDS", int(settings.get("tid", s.tid)),
                        int(settings.get("sid", s.sid)), pss_id, key, version,
                        settings.get("user_name", "PokeUDS"))


def describe(body: wire.Body) -> str:
    """One gflnet body in a log line."""
    if body.kind == "sync":
        return f"sync {'03' if body.done else '02'} module {body.module} id {body.ident:02X}"
    if body.kind == "command":
        return f"command module {body.module} id {body.ident:02X}, {len(body.data)} bytes"
    return f"{body.kind}, {len(body.data)} bytes"
