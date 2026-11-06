"""One Generation 7 (Sun/Moon) trade session on an established UDS network.

The transport is Generation 6's (``gen6.session.Gen6Session``: PRUDP, Net-Z, gflnet, the sync points); this class
gives it Sun/Moon's templates and runs Sun/Moon's script (``wire`` has the table). PokeUDS offers as soon as the
trade screen is up, answers each confirmation step as soon as the console reaches it, and leaves after the queue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ... import pkx
from ..gen6 import session as gen6
from ..gen6.session import TradeEnded
from . import wire

log = logging.getLogger(__name__)

SCREEN_WAIT = 20.0      # how long PokeUDS waits for the console's trade screen before it offers anyway
YES_AFTER = 0.05        # as the client, how long after the console's "yes" PokeUDS answers it

__all__ = ["Gen7Session", "TradeEnded", "party_record"]


@dataclass
class Gen7Session(gen6.Gen6Session):
    GENERATION = 7
    NETZ_OBJECTS = 12
    NETZ_PAIRS = 7
    HEARTBEAT_OBJECT = 4      # heartbeats name Net-Z object 4 in Sun/Moon (3 in X/Y)
    ACK_INDEX_AHEAD = 1       # an acknowledgment carries our next message's index (Gen 6: the last one's)
    person_clock: int | None = None      # the invite's token when PokeUDS invited (``wire.person``)
    # Say yes before the other side does, as a console's player may: only for a PokeUDS standing in for a console
    # (tests). Against a console PokeUDS always answers its player's yes.
    answer_first: bool = False

    def netz_host_messages(self, call_id: int, nonce: bytes) -> list[bytes]:
        return wire.netz_host(call_id, nonce)

    def netz_client_messages(self, call_id: int) -> list[bytes]:
        return wire.netz_client(call_id)

    def host_init_bodies(self) -> list[bytes]:
        return wire.host_init()

    # -- the trade --------------------------------------------------------------------------------

    def run(self) -> list[bytes]:
        self.wait(lambda: self.netz_done, "Net-Z setup")
        self._emit("phase", phase="session")
        if not self.host:
            self.wait(lambda: any(b.kind == "init" for b in self._inbox), "the host's first message")
            self._inbox = [b for b in self._inbox if b.kind not in ("init", "other")]
        self.sync_point(wire.SYNC_START)
        self._command(wire.SESSION, wire.CMD_PROFILE, wire.profile(self.trainer))
        peer = self.take(wire.SESSION, wire.CMD_PROFILE, "the console's profile")
        self._peer_profile = peer.data
        partner, _ = wire.player_of(peer.data)
        log.info("trading with %s", partner)
        self._emit("phase", phase="profiles", partner=partner)
        self.sync_point(wire.SYNC_PROFILES)
        self._command(wire.SESSION, wire.CMD_PERSON, wire.person(self.trainer, clock=self.person_clock))
        self.take(wire.SESSION, wire.CMD_PERSON, "the console's person record")
        self.sync_point(wire.SYNC_PERSONS)
        for ident in wire.SYNC_HUB:
            self.sync_point(ident)
        done = 0
        self._peer_left = False
        while done < self.trades:
            record = self._one_trade()
            if record is None:
                break
            done += 1
        self._leave()
        return self.received

    def _drop_cursor(self) -> None:
        """The console reports every Pokemon its cursor passes; only the latest matters, and none to PokeUDS."""
        self._inbox = [b for b in self._inbox if not (b.kind == "command" and b.module in (wire.SCREEN, wire.CURSOR))]

    def _console_screen_up(self) -> bool:
        return self._peer_sent(wire.SCREEN, wire.CMD_SHOW) or self._peer_sent(wire.TRADE, wire.CMD_OFFER)

    def _one_trade(self) -> bytes | None:
        """Offer, take the console's offer, confirm, swap. The console's record, or None when it left instead."""
        first = True
        while True:
            offer = self._offers[self._trade_no % len(self._offers)]
            sealed, species = pkx.encrypt(offer), pkx.summary(offer).species
            self._emit("phase", phase="offer")
            if first:
                self._command(wire.SCREEN, wire.CMD_SHOW, wire.show(sealed, species, chosen=False))
                self._command(wire.CURSOR, wire.CMD_CURSOR, sealed)
                # Offer once the console's trade screen is up (it announces it with the Pokemon it shows first), as
                # both consoles of the captures did: an offer sent before the console is there can be dropped, and
                # the console then shows our Pokemon but waits for an offer it never got.
                self.wait_or(self._console_screen_up, SCREEN_WAIT)
                if not self._console_screen_up():
                    log.info("the console has not shown its trade screen after %.0f s; offering anyway", SCREEN_WAIT)
                first = False
            self._command(wire.SCREEN, wire.CMD_SHOW, wire.show(sealed, species, chosen=True))
            self._command(wire.TRADE, wire.CMD_OFFER, sealed)
            body = self.take(wire.TRADE, wire.CMD_OFFER, "the console's offer", also=(wire.CMD_LEAVE,))
            if body.ident == wire.CMD_LEAVE:
                self._peer_left = True
                return None
            try:
                record = pkx.decrypt(body.data)
            except ValueError:
                record = b""
            if not pkx.checksum_valid(record):
                log.warning("the console's offer fails its checksum; waiting for another")
                continue
            s = pkx.summary(record)
            log.info("the console offers species %d %r", s.species, s.nickname)
            self._emit("phase", phase="their-offer", summary=f"species {s.species} '{s.nickname}'")
            if not self._confirm():
                log.info("the console declined the trade; offering again")
                continue
            if self.answer_first:
                self._say_yes(sealed, wait=False)
            answer = self._their_yes()
            if answer == "left":
                self._peer_left = True
                return None
            if answer == "no":
                log.info("the console's player said no; offering again")
                continue
            break
        # Our "yes", sent once the console's player has said yes, as the second console answered in both captures (the
        # client's carries module 3 cmd 05: without it the console host waited for "the other trainer's response").
        if not self.answer_first:
            self._say_yes(sealed)
        if self.host:
            self._command(wire.TRADE, wire.CMD_START, bytes([wire.CMD_START]))
        else:
            self.take(wire.TRADE, wire.CMD_START, "the console to start the trade")
        self._emit("phase", phase="confirm")
        for ident in wire.SYNC_SWAP:
            self.sync_point(ident, wire.TRADE)
        self.received.append(record)
        self._trade_no += 1
        if self.on_received:
            self.on_received(record)
        self._emit("phase", phase="swapped")
        self._drop_cursor()       # before the last exchange: the console's next screen message comes after it
        if self.host:
            self.take(wire.TRADE, wire.CMD_CLIENT_DONE, "the console to finish the trade")
            self._command(wire.TRADE, wire.CMD_HOST_DONE, bytes([wire.CMD_HOST_DONE]))
        else:
            self._command(wire.TRADE, wire.CMD_CLIENT_DONE, bytes([wire.CMD_CLIENT_DONE]))
            self.take(wire.TRADE, wire.CMD_HOST_DONE, "the console to finish the trade")
        return record

    def _say_yes(self, sealed: bytes, wait: bool = True) -> None:
        """Our player's "yes": the host sends its Pokemon again (module 2); the client sends that and module 3 cmd 05
        in one message, as the client of both captures did (the host starts the trade only on that 05)."""
        if self.host:
            self._command(wire.CURSOR, wire.CMD_CURSOR, sealed)
            return
        if wait:
            self.wait_or(lambda: False, YES_AFTER)      # about when the captured client answered (50 ms)
        self._gfl_send(gen6.wire.command(wire.CURSOR, wire.CMD_CURSOR, sealed)
                       + gen6.wire.command(wire.TRADE, wire.CMD_CLIENT_ACCEPT, bytes([wire.CMD_CLIENT_ACCEPT])))

    def _their_yes(self) -> str:
        """After the confirmation steps each console asks its player "Trade?" and sends its Pokemon again (module 2)
        for yes; the second one answers within a moment of the first in both captures. "yes", "no" (the console went
        back to the trade screen) or "left"."""
        found: list[str] = []

        def ready() -> bool:
            for b in self._inbox:
                if b.kind != "command":
                    continue
                if b.module == wire.CURSOR and b.ident == wire.CMD_CURSOR:
                    self._inbox.remove(b)
                    found.append("yes")
                elif b.module == wire.TRADE and b.ident == wire.CMD_LEAVE:
                    found.append("left")
                elif b.module == wire.SCREEN and b.ident == wire.CMD_SHOW:
                    found.append("no")
                if found:
                    return True
            return False

        self.wait(ready, "the console's player to accept the trade")
        return found[0]

    def _confirm(self) -> bool:
        """The three confirmation steps after both offers. False if the console's answer was not yes."""
        if self.host:
            self._command(wire.TRADE, wire.CMD_HOST_READY, wire.HOST_READY)
            answer = self.take(wire.TRADE, wire.CMD_CLIENT_YES, "the console's confirmation", also=(wire.CMD_LEAVE,))
            if answer.ident == wire.CMD_LEAVE:
                self._inbox.append(answer)
                return False
            yes = answer.data[:1] == b"\x01"
            self._command(wire.TRADE, wire.CMD_BOTH_YES, wire.BOTH_YES if yes else bytes(4))
            self._drop_cursor()   # what the console showed before its player was asked is no answer
            return yes
        ready = self.take(wire.TRADE, wire.CMD_HOST_READY, "the console to be ready", also=(wire.CMD_LEAVE,))
        if ready.ident == wire.CMD_LEAVE:
            self._inbox.append(ready)
            return False
        self._command(wire.TRADE, wire.CMD_CLIENT_YES, wire.CLIENT_YES)
        both = self.take(wire.TRADE, wire.CMD_BOTH_YES, "the console's confirmation", also=(wire.CMD_LEAVE,),
                         drop_before=lambda b: b.kind == "command" and b.module in (wire.SCREEN, wire.CURSOR))
        if both.ident == wire.CMD_LEAVE:
            self._inbox.append(both)
            return False
        return both.data[:3] == wire.BOTH_YES[:3]

    def _leave(self) -> None:
        """Leave as the consoles do: one side sends 09 from the trade screen and both then meet at sync 0x89. PokeUDS
        waits for the console to be back on its trade screen first (after the trade animation and the summary of what
        it received): a 09 sent at once, mid-animation, left the console waiting for PokeUDS when it got back."""
        self._emit("phase", phase="leaving")
        if not self._peer_left and not self._peer_sent(wire.TRADE, wire.CMD_LEAVE) and self.received:
            # Our trade screen is back too: show its first Pokemon, as a console does (the one just received).
            last = pkx.encrypt(self.received[-1])
            self._command(wire.SCREEN, wire.CMD_SHOW, wire.show(last, pkx.summary(self.received[-1]).species, False))
        if not self._peer_left and not self._peer_sent(wire.TRADE, wire.CMD_LEAVE):
            self.wait(lambda: self._console_screen_up() or self._peer_sent(wire.TRADE, wire.CMD_LEAVE),
                      "the console to be back on the trade screen")
            if not self._peer_sent(wire.TRADE, wire.CMD_LEAVE):
                self._command(wire.TRADE, wire.CMD_LEAVE, bytes([wire.CMD_LEAVE]))
        self.sync_point(wire.SYNC_END)
        self._emit("phase", phase="left")
        if self.host:
            self.ep.send_reliable(gen6.wire.host_keepalive(self._hb_counter, goodbye=True))
            done = lambda: self.ep.idle
        else:
            done = lambda: self._host_said_goodbye
        try:
            self.wait_or(done, gen6.GOODBYE_WAIT)
        except TradeEnded:
            pass


def party_record(offer: bytes) -> bytes:
    """The 260-byte party form of a .pk7 (decrypted); battle stats from the PKHeX helper when it is there."""
    return gen6.party_record(offer, game="sm")
