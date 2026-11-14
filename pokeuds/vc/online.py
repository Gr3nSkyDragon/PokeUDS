"""A Virtual Console trade with a player far away: PokeUDS plays the partner's console to its own (docs/online.md,
The Virtual Console).

Each 3DS links locally with its own PokeUDS, as in an ordinary VC trade, and the two apps pass the consoles' link
data to each other over the relays (``pokeuds.online.link.Partner``). What reaches each 3DS is what the other 3DS
sent, so both play one trade as they would over a cable:

- **the room** (the "Please wait" sync, the room choice, the round opening): answered as ``link.TradeEngine``
  does. The room the console chose sets the mode, and with it the relay room (``gb1``: Gen 1 cable and the Time
  Capsule; ``gb2``: the Gen 2 Trade Center), where the partner is met.
- **the blocks** (party, patch lists, Gen 2 mail): the 3DS sends each block in one burst and then waits for the
  partner's. PokeUDS reads its console's block as it comes, sends it to the partner, and answers with the
  partner's console's block once it has arrived, checked first (``sane_party``). The random numbers are made here.
- **the trade menu** (pick, accept): the console's choice goes to the partner; until the partner's has come,
  PokeUDS answers ``$FE``, no choice yet, as Lorenzooone's PokemonGB_Online_Trades does, so the game keeps asking
  instead of waiting on the link. Each console therefore sees the other's own pick and answer, and both trade, or
  neither, as over a cable.
- **after a trade**: the check byte (Gen 2: Mew or Celebi traded) is worked out here, as both sides would; the next
  round's blocks come fresh from the partner's console, a trade evolution included.

Measured on hardware: nothing yet. How long a 3DS waits at the block window, and in the trade menu, decides how far
apart in time the two players may be (docs/online.md).
"""

from __future__ import annotations

import logging
import os
from collections import deque
from typing import Callable, Protocol

from .. import gbpk
from . import link
from .link import (ACCEPT, CANCEL, CELEBI, DECLINE, GEN1, GEN2, LAYOUTS, MAIL_ITEMS, MEW, NO_DATA, PREAMBLE,
                   TIME_CAPSULE, Decision, LinkMode, Party, State, Trade, is_sync, parse_mail, parse_party, rn_block)

log = logging.getLogger(__name__)

PATCH_BLOCK = 200
MAX_MOVE = {1: 165, 2: 251}         # Struggle; Beat Up
EGG = 0xFD                          # Gen 2's species list holds this for an egg


class Remote(Protocol):
    """The partner far away, as the engine sees it (``PartnerRemote`` over the relays; a pair of dicts in tests)."""

    def begin(self, mode: LinkMode) -> None:
        """The console chose its room: meet the partner in it."""

    def send(self, key: str, data: bytes) -> None:
        """Something this console sent: a block (``party``, ``patch``, ``mail``) or a choice (``pick``, ``confirm``)."""

    def get(self, key: str) -> bytes | None:
        """What the partner's console sent under ``key`` in this round of trades, or None."""

    def next_round(self) -> None:
        """A trade completed: what either console sends next belongs to the next one."""

    def refuse(self, reason: str) -> None:
        """Tell the partner its console's data will not be shown to this console, and why."""

    def gone(self) -> str:
        """Why the partner is no longer there, or ""."""


def room_name(mode: LinkMode, family: str) -> str:
    """The relay room: ``gb1`` for the Gen 1 cable (Red, Blue, Yellow, and Gold, Silver, Crystal's Time Capsule),
    ``gb2`` for the Gen 2 Trade Center, with the language family when it is not the international one (their blocks
    differ, so the families meet apart)."""
    room = "gb1" if mode.generation == 1 else "gb2"
    return room if family == "intl" else f"{room}-{family}"


def shared_sizes(mode: LinkMode, family: str) -> dict[str, int]:
    """What a partner may send, and the exact size of each (``link.Partner``'s ``shared_sizes``)."""
    layout = LAYOUTS[family]
    sizes = {"party": layout.party_block(mode.generation), "patch": PATCH_BLOCK, "pick": 2, "confirm": 2}
    if mode.mail:
        sizes["mail"] = layout.mail_body
    return sizes


def mode_for(console_generation: int, room_byte: int) -> LinkMode:
    """The mode a console's room choice means: a Gen 1 game links only by cable; a Gen 2 game sends $D1 for the
    Trade Center and $D0 (pokecrystal's Time Capsule; Lorenzooone's notes) or $D4 for the Time Capsule."""
    if console_generation == 1:
        return GEN1
    return TIME_CAPSULE if room_byte in (0xD0, 0xD4) else GEN2


def sane_party(party: Party, generation: int, family: str = "intl") -> str:
    """Why a partner's party must not reach a console, or "": what a game could crash on or misread (after
    Lorenzooone's sanity checks): a count past six, a species or move out of the game's tables, a level outside
    1-100, a name with no end."""
    if not 1 <= len(party.mons) <= 6:
        return f"a party of {len(party.mons)} Pokemon"
    if gbpk.TERMINATOR not in party.trainer_name:
        return "a trainer name with no end"
    for slot, mon in enumerate(party.mons, 1):
        dex = mon.dex
        if not 1 <= dex <= (151 if generation == 1 else 251):
            return f"slot {slot}: species {mon.data[0]:#04x} is not in the game"
        if not 1 <= mon.level <= 100:
            return f"slot {slot}: level {mon.level}"
        moves = mon.moves
        if not moves[0] or any(m > MAX_MOVE[generation] for m in moves):
            return f"slot {slot}: a move the game does not have ({', '.join(str(m) for m in moves)})"
        if gbpk.TERMINATOR not in mon.nickname or gbpk.TERMINATOR not in mon.ot_name:
            return f"slot {slot}: a name with no end"
    return ""


class OnlineEngine:
    """The partner's console, one unit at a time, for the local 3DS: ``respond(unit, ahead)`` returns the reply, or
    None to hold it (the partner's block is not here yet). ``ahead`` is what the 3DS has sent after ``unit``, so a
    block it bursts can be read, and passed on, before it is answered."""

    def __init__(self, generation: int, remote: Remote, *, family: str = "intl",
                 on_trade: Callable[[Trade], None] | None = None, rng=os.urandom) -> None:
        if family == "kr" and generation == 1:
            raise ValueError("there is no Korean Gen 1 game")
        self.generation = generation           # the console's game
        self.family = family
        self.layout = LAYOUTS[family]
        self.remote = remote
        self.on_trade = on_trade
        self.rng = rng
        self.mode: LinkMode | None = None
        self.state = State.START
        self.log: list[str] = []
        self.trades: list[Trade] = []
        self.console_party: Party | None = None
        self.remote_party: Party | None = None
        self.remote_mail: list[bytes] = []
        self.waiting = ""                      # what a held reply waits for, for the player
        self._blocks: deque[str] = deque()
        self._block, self._size = "", 0
        self._out: deque[int] = deque()
        self._window: list[int] = []
        self._party_window: list[int] = []
        self._used: dict[str, bytes] = {}      # the partner's blocks answered with, this exchange
        self._sent: set[str] = set()           # this console's blocks passed on, this exchange
        self._refused = ""
        self._mail_body: list[int] = []
        self._in_sync = self._sync_zeros = False
        self._console_nybble = self._remote_nybble = 0
        self._decision = Decision.PICK
        self._step = 0                         # choices made in this round of trades: the two sides count alike
        self._told: tuple[str, int] | None = None
        self._console_pick = self._remote_pick = 0

    # Public ---------------------------------------------------------------------------------

    def respond(self, byte: int, ahead=()) -> int | None:
        reply = self._respond(byte, list(ahead))
        return None if reply is None else reply & 0xFF

    def phase(self) -> str:
        return self.state.value if self.state is not State.SELECT else f"select/{self._decision.value}"

    # Dispatch -------------------------------------------------------------------------------

    def _note(self, text: str) -> None:
        self.log.append(text)
        log.info(text)

    def _hold(self, why: str) -> None:
        if why != self.waiting:
            self.waiting = why
            self._note(why)

    def _respond(self, b: int, ahead: list[int]) -> int | None:
        if self.state is State.START:
            self.state = State.ROOM
            return 0x00
        if self.state is State.BLOCK:
            return self._in_block(b)
        if self.state is State.WAIT_BLOCK:
            return self._start_block(b, ahead) if b == PREAMBLE else PREAMBLE
        if self.state in (State.MAIL_WAIT, State.MAIL_RUN, State.MAIL_BODY):
            return self._mail(b, ahead)
        if self.state is State.ROOM:
            if b == PREAMBLE:
                self._new_round()
                return self._start_block(b, ahead)
            return self._room(b)
        return self._select(b, ahead)

    def _room(self, b: int) -> int:
        if is_sync(b):
            return b                              # mirrored, as the partner's game would
        if (b & 0xF0) == 0xD0:
            if self.mode is None:
                self._set_mode(mode_for(self.generation, b))
            return self.mode.room_byte
        return 0 if b == 0 else NO_DATA

    def _set_mode(self, mode: LinkMode) -> None:
        self.mode = mode
        mirror = isinstance(self.remote, MirrorRemote)
        where = "" if mirror else f"; meeting the partner in {room_name(mode, self.family)}"
        self._note(f"room: {mode.key}{where}")
        self.remote.begin(mode)

    # Blocks ---------------------------------------------------------------------------------

    def _new_round(self) -> None:
        if self.mode is None:                     # the room choice went unseen: the console's own kind of room
            self._set_mode(GEN1 if self.generation == 1 else GEN2)
        names = ["rn", "party", "patch"] + (["mail"] if self.mode.mail else [])
        self._blocks = deque(names)
        self._used, self._sent, self._refused = {}, set(), ""
        self.state = State.WAIT_BLOCK
        self._note(f"round: exchanging {', '.join(names)}")

    def _size_of(self, name: str) -> int:
        return {"rn": 17, "party": self.layout.party_block(self.mode.generation), "patch": PATCH_BLOCK,
                "mail": self.layout.mail_body}[name]

    def _start_block(self, b: int, ahead: list[int]) -> int | None:
        name = self._blocks[0]
        size = self._size_of(name)
        if name == "rn":
            data = rn_block(self.rng)
        else:
            data = self._partner_block(name, [b, *ahead], size)
            if data is None:
                return None
        self._blocks.popleft()
        self.waiting = ""
        self._block, self._size = name, size
        self._out = deque(data[1:])               # data[0] went out as padding at T-1
        self._window = [b]
        self.state = State.BLOCK
        return self._out.popleft()

    def _partner_block(self, name: str, burst: list[int], size: int) -> bytes | None:
        """The partner's ``name`` block to answer this console's with, once this console's has gone to the partner
        and the partner's has come and passed the checks; None (hold) until then."""
        if name not in self._sent:
            if len(burst) < size:
                self._hold(f"reading the 3DS's {name} block ({len(burst)} of {size})")
                return None
            self.remote.send(name, bytes(burst[:size]))
            self._sent.add(name)
        if self._refused:
            return None
        data = self.remote.get(name)
        if data is None or len(data) != size:
            gone = self.remote.gone()
            self._hold(f"{gone}: the 3DS waits at the table" if gone else f"waiting for the partner's {name} block")
            return None
        if why := self._check(name, data):
            self._refused = why
            self.remote.refuse(why)
            self._note(f"refused the partner's {name} block: {why}")
            return None
        self._used[name] = data
        return data

    def _check(self, name: str, data: bytes) -> str:
        generation = self.mode.generation
        try:
            if name == "party":
                return sane_party(parse_party(list(data), [], generation, self.family), generation, self.family)
            if name == "patch":
                party = parse_party(list(self._used["party"]), list(data), generation, self.family)
                return sane_party(party, generation, self.family)
            parse_mail(list(data), self.family)
        except (ValueError, IndexError, KeyError) as exc:
            return f"it cannot be read ({exc})"
        return ""

    def _in_block(self, b: int) -> int:
        self._window.append(b)
        reply = self._out.popleft() if self._out else None
        if len(self._window) >= self._size:
            self._finish_block()
        if reply is not None:
            return reply
        return PREAMBLE if self.state is State.WAIT_BLOCK else (self.layout.mail_preamble if self.state is State.MAIL_WAIT
                                                                else NO_DATA)

    def _finish_block(self) -> None:
        generation = self.mode.generation
        if self._block == "party":
            self._party_window = self._window
        elif self._block == "patch":
            self.console_party = parse_party(self._party_window, self._window, generation, self.family)
            self.remote_party = parse_party(list(self._used["party"]), list(self._used["patch"]), generation,
                                            self.family)
            self._note(f"partner {gbpk.decode_name(self.remote_party.trainer_name, self.family)}: "
                       + ", ".join(m.describe() for m in self.remote_party.mons))
        nxt = self._blocks[0] if self._blocks else None
        if nxt == "mail":
            self.state = State.MAIL_WAIT
        elif nxt:
            self.state = State.WAIT_BLOCK
        else:
            self._enter_select()

    def _mail(self, b: int, ahead: list[int]) -> int | None:
        run = self.layout.mail_preamble
        if self.state is State.MAIL_WAIT:
            if b == run:
                self.state = State.MAIL_RUN       # units before the 3DS's $20 run are patch-list leftovers
            return run
        if self.state is State.MAIL_RUN:
            if b in (run, NO_DATA):
                return run
            data = self._partner_block("mail", [b, *ahead], self.layout.mail_body)
            if data is None:
                return None
            self._blocks.popleft()
            self.waiting = ""
            self.state = State.MAIL_BODY
            self._out = deque(data)
            self._mail_body = []
        self._mail_body.append(b)
        reply = self._out.popleft() if self._out else 0
        if len(self._mail_body) >= self.layout.mail_body:
            self.remote_mail = parse_mail(list(self._used["mail"]), self.family)
            self._enter_select()
        return reply

    # Trade screen ---------------------------------------------------------------------------

    def _enter_select(self) -> None:
        self.state = State.SELECT
        self._decision = Decision.PICK
        self._in_sync = False

    def _partner_choice(self, console: int) -> int | None:
        """The partner's console's choice at this step, once it has come; this console's goes to the partner first.
        A partner gone cancels, or declines."""
        if self._decision is Decision.AFTER_TRADE:            # worked out alike on both sides
            if self.mode.generation == 2 and self.trades:
                species = {self.trades[-1].sent.dex, self.trades[-1].received.dex}
                return 1 if MEW in species else 2 if CELEBI in species else 0
            return ACCEPT
        key = "pick" if self._decision is Decision.PICK else "confirm"
        if self._told != (key, self._step):
            self.remote.send(key, bytes([self._step & 0xFF, console & 0x0F]))
            self._told = (key, self._step)
        got = self.remote.get(key)
        if got and got[0] == self._step & 0xFF:
            return got[1] & 0x0F
        if gone := self.remote.gone():
            self._hold(f"{gone}: answering {'Cancel' if key == 'pick' else 'No'}")
            return CANCEL if key == "pick" else DECLINE
        self._hold("waiting for the partner to choose" if key == "pick" else "waiting for the partner's answer")
        return None

    def _select(self, b: int, ahead: list[int]) -> int:
        if is_sync(b):
            if not self._in_sync or self._sync_zeros:
                if self._in_sync:
                    self._end_sync()
                    if self.state is not State.SELECT:
                        return self._respond(b, ahead)
                theirs = self._partner_choice(b & 0x0F)
                if theirs is None:
                    return NO_DATA                # no choice yet: the console asks again
                self.waiting = ""
                self._in_sync, self._sync_zeros = True, False
                self._console_nybble, self._remote_nybble = b & 0x0F, theirs
            return (b & 0xF0) | self._remote_nybble
        if b == 0 and self._in_sync:
            self._sync_zeros = True
            return 0
        if self._in_sync:
            self._end_sync()
            if self.state is not State.SELECT:
                return self._respond(b, ahead)
        if b == PREAMBLE:                         # a new round without the post-trade sync expected
            self.state = State.ROOM
            return self._respond(b, ahead)
        return 0 if b == 0 else NO_DATA

    def _end_sync(self) -> None:
        self._in_sync = self._sync_zeros = False
        console, partner = self._console_nybble, self._remote_nybble
        if self._decision is not Decision.AFTER_TRADE:
            self._step += 1
        if self._decision is Decision.PICK:
            if console == CANCEL and partner == CANCEL:
                self._note("both left the trade screen")
                self.state = State.ROOM
            elif CANCEL in (console, partner):
                self._note("the partner cancelled" if partner == CANCEL else "the 3DS cancelled")
            else:
                self._console_pick, self._remote_pick = console, partner
                self._decision = Decision.CONFIRM
                mon = self._mon(self.remote_party, partner)
                self._note(f"partner offers slot {partner}: {mon.describe() if mon else '?'}")
        elif self._decision is Decision.CONFIRM:
            if console == ACCEPT and partner == ACCEPT:
                self._trade()
                self._decision = Decision.AFTER_TRADE
            else:
                self._note("the partner declined the trade" if partner != ACCEPT else "the 3DS declined the trade")
                self._decision = Decision.PICK
        else:
            self.state = State.ROOM

    @staticmethod
    def _mon(party: Party | None, slot: int) -> gbpk.Record | None:
        return party.mons[slot] if party and slot < len(party.mons) else None

    def _trade(self) -> None:
        sent, received = self._mon(self.console_party, self._console_pick), self._mon(self.remote_party, self._remote_pick)
        self.remote.next_round()
        self._step, self._told = 0, None
        if sent is None or received is None:
            self._note("traded, but a party was not read")
            return
        mail = self.remote_mail[self._remote_pick] if self.remote_mail and self._remote_pick < len(self.remote_mail) else None
        if mail is not None and (received.held_item not in MAIL_ITEMS or not any(mail)):
            mail = None
        name = gbpk.decode_name(self.remote_party.trainer_name, self.family)
        trade = Trade(sent, received, mail, name)
        self.trades.append(trade)
        self._note(f"traded {sent.describe()} for {received.describe()}")
        if self.on_trade:
            self.on_trade(trade)


class PartnerRemote:
    """``Remote`` over the relays: a ``link.Partner`` in the room the console's choice names, made then."""

    def __init__(self, family: str = "intl", code: str = "", name: str = "", say=None, partner_factory=None) -> None:
        self.family, self.code, self.name, self.say = family, code, name, say
        self.factory = partner_factory
        self.partner = None

    def begin(self, mode: LinkMode) -> None:
        if self.partner is not None:
            return
        room, sizes = room_name(mode, self.family), shared_sizes(mode, self.family)
        if self.factory is not None:
            self.partner = self.factory(room, sizes)
            return
        from .. import __version__
        from ..online.link import Partner

        self.partner = Partner(room, self.code, name=self.name, app=__version__, say=self.say, shared_sizes=sizes)
        self.partner.start()

    def send(self, key: str, data: bytes) -> None:
        self.partner.share(key, data)

    def get(self, key: str) -> bytes | None:
        with self.partner.lock:
            return self.partner.theirs().shared.get(key)

    def next_round(self) -> None:
        self.partner.done()

    def refuse(self, reason: str) -> None:
        self.partner.refuse(reason)

    def gone(self) -> str:
        if self.partner is not None and self.partner.state in ("lost", "closed"):
            return self.partner.ended or "the partner left"
        return ""

    def close(self, reason: str = "the trade ended") -> None:
        if self.partner is not None:
            self.partner.close(reason)


class MirrorRemote:
    """``Remote`` for a Touch Trade: the partner is the console itself. Its party comes back as the partner's, its pick
    as the partner's pick and its answer as the partner's answer, so it trades each Pokemon for itself and a trade
    evolution (Kadabra, Machoke, Graveler, Haunter; Gen 2's held-item evolutions) happens on the console. Nothing is
    kept, and no relay is used."""

    family = "intl"

    def __init__(self) -> None:
        self.box: dict[tuple[int, str], bytes] = {}
        self.round = 0
        self.mode: LinkMode | None = None

    def begin(self, mode: LinkMode) -> None:
        self.mode = mode

    def send(self, key: str, data: bytes) -> None:
        self.box[(self.round, key)] = bytes(data)

    def get(self, key: str) -> bytes | None:
        return self.box.get((self.round, key))

    def next_round(self) -> None:
        self.round += 1
        self.box = {k: v for k, v in self.box.items() if k[0] >= self.round}

    def refuse(self, reason: str) -> None:
        log.warning("touch trade: the console's own block fails the checks: %s", reason)

    def gone(self) -> str:
        return ""

    def close(self, reason: str = "") -> None:
        pass
