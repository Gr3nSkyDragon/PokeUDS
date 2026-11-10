"""A model of a retail 3DS Virtual Console game, for tests and the demo radio.

It plays the measured behaviour (mgba_LDN wiki/vc_link.md): host's first unit $EF, then one unit per
exchange with the partner's reply to the same index; random numbers paced, party block, patch lists
and mail sent in bursts; for a block whose first $FD it sends at index T it stores the partner's
units from T-1. It decodes what it stores with the same rules as the ROM (pokeuds.vc.link.parse_*). ``family``
plays a Japanese or Korean game's blocks (pokeuds.vc.link, Languages).
"""

from __future__ import annotations

from pokeuds import gbpk
from pokeuds.vc import link


class Model3DS:
    def __init__(self, mode: link.LinkMode, party: link.Party, *, pick: int = 0, accept: bool = True,
                 trades: int = 1, console_generation: int | None = None, role: str = "host",
                 family: str = "intl") -> None:
        self.mode = mode
        self.family = family
        self.layout = link.LAYOUTS[family]
        self.party = party
        self.pick, self.accept, self.trades = pick, accept, trades
        self.console_generation = console_generation or mode.generation
        self.role = role
        self.sent: list[int] = []
        self.ours: list[int] = []
        self.received: list[gbpk.Record] = []      # what the partner traded us
        self.partner_parties: list[link.Party] = []
        self.partner_mail: list[list[bytes]] = []
        self.done = False

    def _x(self, b: int):
        reply = yield b
        self.sent.append(b)
        self.ours.append(reply)
        return reply

    def _sync(self, value: int, before: int = 0):
        for _ in range(before):
            yield from self._x(link.NO_DATA)
        rng = value & 0xF0
        theirs = None
        while theirs is None:
            r = yield from self._x(value)
            if (r & 0xF0) == rng:
                theirs = r & 0x0F
        for _ in range(5):
            yield from self._x(value)
        for _ in range(5):
            yield from self._x(0)
        return theirs

    def _block(self, data: bytes):
        start = len(self.sent)
        for b in data:
            yield from self._x(b)
        return start

    def run(self):
        g2 = self.mode.generation == 2
        rng = self.mode.decision_range
        if self.role == "host":
            yield from self._x(0xEF)                                  # the host's first unit
        yield from self._x(0x00)
        yield from self._sync(0x60)                                   # "Please wait"
        if self.console_generation == 1:
            pressed = False                                           # LinkMenu, cursor on Trade Center
            while not pressed:
                for b in (0xD0, 0xD0, 0x00):
                    r = yield from self._x(b)
                    pressed |= (r & 0xF0) == 0xD0 and bool(r & 0x0C)
            for b in (0xD4, 0xD4, 0x00):
                yield from self._x(b)
        else:                                                         # Link_EnsureSync
            got = False
            while not got:
                for _ in range(2):
                    r = yield from self._x(self.mode.room_byte)
                    got |= (r & 0xF0) == 0xD0
        for _ in range(20):
            yield from self._x(link.NO_DATA)                          # walking to the table
        party = self.party
        while True:
            if g2:
                yield from self._sync(rng | 5)
                yield from self._sync(rng | 6)
            else:
                yield from self._sync(0x60)
            yield from self._x(0)
            yield from self._x(0)
            rn_at = yield from self._block(link.rn_block())
            yield from self._x(0x00)
            party_block, patch_block = link.party_blocks(party, self.mode.generation, self.family)
            party_at = yield from self._block(party_block)
            yield from self._x(0x00)
            patch_at = yield from self._block(patch_block)
            if self.mode.mail:
                for _ in range(11):
                    yield from self._x(0x00)                          # patch-list leftovers
                mail_at = yield from self._block(link.mail_block(party, self.family))
            for _ in range(10):
                yield from self._x(link.NO_DATA)
            # What it stored of ours, from T-1 of each block.
            window = lambda at, size: self.ours[at - 1:at - 1 + size]
            theirs = link.parse_party(window(party_at, self.layout.party_block(self.mode.generation)), window(patch_at, 200),
                                      self.mode.generation, self.family)
            self.partner_parties.append(theirs)
            if self.mode.mail:
                run = self.layout.mail_preamble
                stored = window(mail_at, 5 + self.layout.mail_body)
                i = stored.index(run)
                while i < len(stored) and stored[i] in (run, link.NO_DATA):
                    i += 1
                self.partner_mail.append(link.parse_mail(stored[i:], self.family))
            pick = self.pick if self.trades > 0 else link.CANCEL
            their_pick = yield from self._sync(rng | pick)
            if pick == link.CANCEL or their_pick == link.CANCEL:
                self.done = True
                while True:
                    yield from self._x(link.NO_DATA)
            yield from self._sync(rng | (link.ACCEPT if self.accept else link.DECLINE), before=5)
            if not self.accept:
                self.done = True
                while True:
                    yield from self._x(link.NO_DATA)
            self.received.append(theirs.mons[their_pick])
            party = link.Party(party.trainer_name, party.trainer_id, [theirs.mons[their_pick]] + party.mons[1:])
            self.trades -= 1
            yield from self._sync(rng | (0 if g2 else link.ACCEPT), before=5)


def play(engine: link.TradeEngine, model: Model3DS, max_units: int = 20000) -> int:
    """Run the model (leading) against the engine (answering each unit) until the model is done."""
    gen = model.run()
    byte = next(gen)
    for count in range(max_units):
        reply = engine.respond(byte)
        if model.done:
            return count
        byte = gen.send(reply)
    raise AssertionError(f"no end after {max_units} units; engine in {engine.phase()}")
