"""The Game Boy side of a Virtual Console trade: what a Gen 1 or Gen 2 game says on the link cable.

The 3DS VC sends one unit per serial exchange and waits for ours of the same index (except its
blocks, which it sends in one burst). PokeUDS therefore answers each 3DS unit after seeing it,
the way a game paused at every exchange would; it never has to guess ahead like a cartridge.
``TradeEngine.respond`` takes the 3DS's unit k and returns ours.

The byte layer and the room sequence come from pret (pokered ``engine/link/cable_club.asm``,
``home/serial.asm``; pokecrystal ``engine/link/link.asm``):

- **syncs** (``Serial_SyncAndExchangeNybble`` / ``WaitLinkTransfer``): ``range + nybble`` until the
  partner's arrives, more of the same, then zeros. Range ``$60`` in Gen 1 and the Time Capsule,
  ``$70`` in the Gen 2 Trade Center. Mirrored unit for unit, so both sides leave together.
- **room choice**: Gen 1 ``LinkMenu`` (``$D4`` = A on Trade Center; a partner's A is taken by a
  player who has not pressed anything) and Gen 2 ``Link_EnsureSync`` (``$D1`` Trade Center,
  ``$D4`` Time Capsule).
- **blocks**: random numbers (17), party (424 Gen 1 / 450 Gen 2), patch lists (200), and in the
  Gen 2 Trade Center only, mail (390). The Time Capsule uses Gen 1's blocks and has no mail.
- **trade screen**: pick (party slot, ``$F`` cancel), confirm (2 accept, 1 decline), one sync
  after the trade (Gen 2: 1 if a Mew, 2 if a Celebi was traded, else 0), then the room repeats.

Block alignment is measured (mgba_LDN ``wiki/vc_link.md``): for a block whose first preamble
``$FD`` the 3DS sends at index T, it stores our units from T-1. Our unit T-1 is a padding ``$FD``
and our block's remaining bytes go out from T on, so the 3DS sees a normal preamble.

Languages (``Layout``; pokeuds.vc.gbtext; from Lorenzooone's PokemonGB_Online_Trades, MIT): the structs and patch
lists are the same everywhere; only the names and the mail differ.

| family | names | party block Gen 1 / Gen 2 | mail body |
|---|---|---|---|
| international | 11 bytes | 424 / 450 | ``$20`` run; six 33-byte messages, six 14-byte authors, the authors' patch list (103): 385 |
| Japanese | 6 bytes | 359 / 385 | ``$20`` run; six (33-byte message, 9-byte author), one patch list over all of it (33): 285 |
| Korean | 11 bytes | - / 450 | ``$52`` run; six 65-byte messages, six 14-byte authors, the authors' patch list (103): 577 |

Only the international layout has been seen on the air from a 3DS; the Japanese and Korean ones are those the
cartridges use, as Lorenzooone's project trades with them, and unmeasured on the Virtual Console.
"""

from __future__ import annotations

import enum
import logging
import os
import struct
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .. import gbpk

log = logging.getLogger(__name__)

PREAMBLE, NO_DATA, PATCH_END = 0xFD, 0xFE, 0xFF
MAIL_PREAMBLE, MAIL_REPLACEMENT = 0x20, 0x21
SYNC_RANGES = (0x60, 0x70, 0x80)
CANCEL = 0xF
ACCEPT, DECLINE = 2, 1
PATCH_PART = 0xFC
MAIL_MSG = 33          # two 16-character lines and a line break
MAIL_META = 14         # author name 10, author id 2, species, mail type
MAIL_BODY = MAIL_MSG * 6 + MAIL_META * 6 + (MAIL_META + 3) * 6 + 1   # 385, after the 5-byte preamble
MEW, CELEBI = 151, 251
MAIL_ITEMS = range(0x9E, 0xBE)     # Flower Mail .. Mirage Mail (pokecrystal item constants)


@dataclass(frozen=True)
class LinkMode:
    key: str
    generation: int        # block formats: 1 (Gen 1 and the Time Capsule) or 2 (Gen 2 Trade Center)
    room_byte: int
    decision_range: int
    mail: bool

    @property
    def party_block(self) -> int:
        return 424 if self.generation == 1 else 450


@dataclass(frozen=True)
class Layout:
    """How a language family lays out its blocks (the table above)."""
    family: str
    name: int                  # a name field
    mail_preamble: int
    mail_message: int          # one mail's message
    mail_author: int           # one mail's author record
    interleaved: bool          # Japanese: each message followed by its author, one patch list over both

    def party_block(self, generation: int) -> int:
        structs = gbpk.GEN1_STRUCT * 6 if generation == 1 else 2 + gbpk.GEN2_STRUCT * 6     # Gen 2: the trainer id too
        return 6 + self.name + 1 + 7 + structs + self.name * 12 + 3

    @property
    def mail(self) -> int:
        """One slot's mail as the bank keeps it: message then author."""
        return self.mail_message + self.mail_author

    @property
    def mail_patch(self) -> int:
        return 33 if self.interleaved else (self.mail_author + 3) * 6 + 1

    @property
    def mail_body(self) -> int:
        return self.mail * 6 + self.mail_patch


LAYOUTS = {"intl": Layout("intl", 11, MAIL_PREAMBLE, MAIL_MSG, MAIL_META, False),
           "jp": Layout("jp", 6, MAIL_PREAMBLE, MAIL_MSG, 9, True),
           "kr": Layout("kr", 11, 0x52, 0x41, MAIL_META, False)}


GEN1 = LinkMode("gen1", 1, 0xD4, 0x60, False)
GEN2 = LinkMode("gen2", 2, 0xD1, 0x70, True)
TIME_CAPSULE = LinkMode("timecapsule", 1, 0xD4, 0x60, False)


def is_sync(byte: int) -> bool:
    return (byte & 0xF0) in SYNC_RANGES


# -- blocks ---------------------------------------------------------------------------------------

def rn_block(rng=os.urandom) -> bytes:
    numbers = bytes(b % PREAMBLE for b in rng(10))      # each below $FD
    return bytes([PREAMBLE] * 7) + numbers


def _patch(region: bytearray) -> bytes:
    """Replace $FE in the party structs with $FF and list their 1-based offsets, in two parts."""
    entries = []
    for part, (start, end) in enumerate(((0, PATCH_PART), (PATCH_PART, len(region)))):
        for i in range(start, end):
            if region[i] == NO_DATA:
                region[i] = PATCH_END
                entries.append(i - start + 1)
        entries.append(PATCH_END)
    out = bytes([PREAMBLE] * 3) + bytes(7) + bytes(entries)
    return out.ljust(200, b"\x00")[:200]


@dataclass
class Party:
    trainer_name: bytes
    trainer_id: int
    mons: list[gbpk.Record]
    mail: list[bytes] = field(default_factory=list)      # Gen 2: one 47-byte mail per slot (message + metadata)


def party_blocks(party: Party, generation: int, family: str = "intl") -> tuple[bytes, bytes]:
    """The party block and its patch lists, as ``Link_PrepPartyData_*`` + ``FixDataForLinkTransfer``, with the
    names of ``family``'s length."""
    layout = LAYOUTS[family]
    struct_len = gbpk.GEN1_STRUCT if generation == 1 else gbpk.GEN2_STRUCT
    if any(m.family != family for m in party.mons) or len(party.trainer_name) != layout.name:
        raise ValueError(f"a {family} party block holds {family} names")
    count = len(party.mons)
    species = bytes(m.data[0] for m in party.mons) + bytes([0xFF])
    species = species.ljust(7, b"\x00")
    structs = b"".join(m.data for m in party.mons).ljust(struct_len * 6, b"\x00")
    names = lambda pick: b"".join(pick(m) for m in party.mons).ljust(layout.name * 6, b"\x00")
    region = bytearray((struct.pack(">H", party.trainer_id) if generation == 2 else b"") + structs)
    patch = _patch(region)
    block = (bytes([PREAMBLE] * 6) + party.trainer_name + bytes([count]) + species + bytes(region)
             + names(lambda m: m.ot_name) + names(lambda m: m.nickname) + bytes(3))
    assert len(block) == layout.party_block(generation), (len(block), layout.party_block(generation))
    return block, patch


def mail_block(party: Party, family: str = "intl") -> bytes:
    """The Gen 2 Trade Center's mail block (``Link_PrepPartyData_Gen2``, Trade Center part), as ``family``'s games
    send it: international and Korean patch the authors and write ``$21`` for a ``$FE`` in a message; Japanese
    puts each author after its message and patches the whole of it (Lorenzooone's ``GSCJPMailConverter``)."""
    layout = LAYOUTS[family]
    mails = [bytes(party.mail[i] if i < len(party.mail) else b"")[:layout.mail].ljust(layout.mail, b"\x00")
             for i in range(6)]
    if layout.interleaved:
        region = bytearray(b"".join(mails))
        part = region
    else:
        messages = bytes(MAIL_REPLACEMENT if b == NO_DATA else b for m in mails for b in m[:layout.mail_message])
        part = bytearray(b"".join(m[layout.mail_message:] for m in mails))
    patches = []
    for i, b in enumerate(part):
        if b == NO_DATA:
            part[i] = PATCH_END
            patches.append(i + 1)
    patch_set = bytes(patches + [PATCH_END]).ljust(layout.mail_patch, b"\x00")[:layout.mail_patch]
    body = bytes(part) if layout.interleaved else messages + bytes(part)
    out = bytes([layout.mail_preamble] * 5) + body + patch_set
    assert len(out) == 5 + layout.mail_body
    return out


def _skip_control(data: list[int], allow_zero: bool = False) -> int:
    i = 0
    while i < len(data) and (data[i] in (PREAMBLE, NO_DATA) or (not allow_zero and data[i] == 0)):
        i += 1
    return i


def _copy_skipping_no_data(data: list[int], start: int, count: int) -> list[int]:
    out, i = [], start
    while len(out) < count and i < len(data):
        if data[i] != NO_DATA:
            out.append(data[i])
        i += 1
    return out + [0] * (count - len(out))


def parse_party(window: list[int], patch_window: list[int], generation: int, family: str = "intl") -> Party:
    """The partner's party from its stored block and patch lists, as the receiving ROM unpacks them."""
    n = LAYOUTS[family].name
    struct_len = gbpk.GEN1_STRUCT if generation == 1 else gbpk.GEN2_STRUCT
    id_len = 2 if generation == 2 else 0
    total = n + 1 + 7 + id_len + struct_len * 6 + n * 12
    data = _copy_skipping_no_data(window, _skip_control(window), total)
    region_start = n + 1 + 7
    region = data[region_start:region_start + id_len + struct_len * 6]
    pos, base = _skip_control(patch_window), 0
    parts = 0
    while pos < len(patch_window) and parts < 2:
        b = patch_window[pos]
        pos += 1
        if b in (0, PREAMBLE, NO_DATA):
            continue
        if b == PATCH_END:
            parts += 1
            base = PATCH_PART
            continue
        if 0 <= base + b - 1 < len(region):
            region[base + b - 1] = NO_DATA
    name = bytes(data[:n])
    count = data[n]
    trainer_id = struct.unpack(">H", bytes(region[:2]))[0] if generation == 2 else 0
    structs = region[id_len:]
    names_at = region_start + id_len + struct_len * 6
    mons = []
    for i in range(min(count, 6)):
        ot = bytes(data[names_at + n * i:names_at + n * (i + 1)])
        nick = bytes(data[names_at + 6 * n + n * i:names_at + 6 * n + n * (i + 1)])
        mons.append(gbpk.Record(generation, bytes(structs[struct_len * i:struct_len * (i + 1)]), ot, nick, family))
    return Party(name, trainer_id, mons)


def parse_mail(body: list[int], family: str = "intl") -> list[bytes]:
    """Six mails (message then author, ``Layout.mail`` bytes each) from the body that follows the partner's mail
    run (pokecrystal ``.skip_mail``)."""
    layout = LAYOUTS[family]
    size = layout.mail_body
    data = list(body[:size]) + [0] * (size - len(body))
    if layout.interleaved:
        region = data[:layout.mail * 6]
        for b in data[layout.mail * 6:]:
            if b == PATCH_END:
                break
            if 0 < b <= len(region):
                region[b - 1] = NO_DATA
        return [bytes(region[layout.mail * i:layout.mail * (i + 1)]) for i in range(6)]
    msg, meta = layout.mail_message, layout.mail_author
    messages = [NO_DATA if b == MAIL_REPLACEMENT else b for b in data[:msg * 6]]
    metadata = data[msg * 6:msg * 6 + meta * 6]
    for b in data[msg * 6 + meta * 6:]:
        if b == PATCH_END:
            break
        if 0 < b <= len(metadata):
            metadata[b - 1] = NO_DATA
    return [bytes(messages[msg * i:msg * (i + 1)] + metadata[meta * i:meta * (i + 1)]) for i in range(6)]


# -- the engine -----------------------------------------------------------------------------------

class State(enum.Enum):
    START = "start"
    ROOM = "room"              # receptionist, link menu, walking, the round's opening sync
    WAIT_BLOCK = "wait-block"
    BLOCK = "block"
    MAIL_WAIT = "mail-wait"
    MAIL_RUN = "mail-run"
    MAIL_BODY = "mail-body"
    SELECT = "select"


class Decision(enum.Enum):
    PICK = "pick"
    CONFIRM = "confirm"
    AFTER_TRADE = "after-trade"


@dataclass
class Trade:
    sent: gbpk.Record
    received: gbpk.Record
    received_mail: bytes | None
    partner_name: str


class TradeEngine:
    """Our half of the cable, one unit at a time.

    The party is the trade queue (up to six, as a Game Boy party is): each trade offers the next queued Pokemon,
    and the game's own bookkeeping follows it, the Pokemon sent leaving the party and the one received joining
    its end, so the next round shows the console the party a real game would have. ``trades`` (default: one per
    queued Pokemon) above the queue offers the first slot again, for tests."""

    def __init__(self, mode: LinkMode, party: Party, *, trades: int | None = None,
                 on_trade: Callable[[Trade], None] | None = None, rng=os.urandom, family: str = "intl") -> None:
        if not party.mons or any(m.generation != mode.generation for m in party.mons):
            raise ValueError(f"a {mode.key} trade sends Generation {mode.generation} records")
        if family == "kr" and mode.generation == 1:
            raise ValueError("Korean Gold and Silver trade in the Trade Center only: there is no Korean Gen 1 game")
        self.mode = mode
        self.family = family
        self.layout = LAYOUTS[family]
        self.party = party
        self.trades_left = len(party.mons) if trades is None else trades
        self._pending = list(party.mons)          # the queue not traded yet, in order
        self._our_pick = 0
        self.on_trade = on_trade
        self.rng = rng
        self.state = State.START
        self.trades: list[Trade] = []
        self.partner: Party | None = None
        self.partner_mail: list[bytes] = []
        self.log: list[str] = []
        # Round state.
        self._blocks: deque[str] = deque()
        self._block = ""
        self._out: deque[int] = deque()
        self._window: list[int] = []
        self._patch_window: list[int] = []
        self._party_window: list[int] = []
        self._mail_body: list[int] = []
        # Sync state.
        self._in_sync = False
        self._sync_zeros = False
        self._their_nybble = 0
        self._our_nybble = 0
        self._decision = Decision.PICK
        self._their_pick = 0

    # Public ---------------------------------------------------------------------------------

    def respond(self, byte: int) -> int:
        reply = self._respond(byte)
        return reply & 0xFF

    def phase(self) -> str:
        return self.state.value if self.state is not State.SELECT else f"select/{self._decision.value}"

    # Dispatch -------------------------------------------------------------------------------

    def _note(self, text: str) -> None:
        self.log.append(text)
        log.info(text)

    def _respond(self, b: int) -> int:
        if self.state is State.START:
            self.state = State.ROOM
            return 0x00                           # The joiner's first unit (the host's is $EF).
        if self.state is State.BLOCK:
            return self._in_block(b)
        if self.state is State.WAIT_BLOCK:
            if b == PREAMBLE:
                return self._start_block(b)
            return PREAMBLE                       # Padding: also our unit T-1 of the next block.
        if self.state in (State.MAIL_WAIT, State.MAIL_RUN, State.MAIL_BODY):
            return self._mail(b)
        if self.state is State.ROOM:
            if b == PREAMBLE:
                self._new_round()
                return self._start_block(b)
            return self._room(b)
        return self._select(b)

    def _room(self, b: int) -> int:
        if is_sync(b):
            self._in_sync = True
            return b                              # Mirror: the same range and nybble.
        if (b & 0xF0) == 0xD0:
            return self.mode.room_byte
        if b == 0:
            return 0
        return NO_DATA

    # Blocks ---------------------------------------------------------------------------------

    def _new_round(self) -> None:
        names = ["rn", "party", "patch"] + (["mail"] if self.mode.mail else [])
        self._blocks = deque(names)
        self._party_block, self._patch_block = party_blocks(self.party, self.mode.generation, self.family)
        self._note(f"round: exchanging {', '.join(names)}")

    def _block_bytes(self, name: str) -> tuple[bytes, int]:
        if name == "rn":
            return rn_block(self.rng), 17
        if name == "party":
            return self._party_block, self.layout.party_block(self.mode.generation)
        return self._patch_block, 200

    def _start_block(self, b: int) -> int:
        self._block = self._blocks.popleft()
        data, self._size = self._block_bytes(self._block)
        self._out = deque(data[1:])               # data[0] went out as padding at T-1.
        self._window = [b]
        self.state = State.BLOCK
        return self._out.popleft()

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
        if self._block == "party":
            self._party_window = self._window
        elif self._block == "patch":
            self._patch_window = self._window
            self.partner = parse_party(self._party_window, self._patch_window, self.mode.generation, self.family)
            self._note(f"partner {gbpk.decode_name(self.partner.trainer_name, self.family)}: "
                       + ", ".join(m.describe() for m in self.partner.mons))
        nxt = self._blocks[0] if self._blocks else None
        if nxt == "mail":
            self._blocks.popleft()
            self.state = State.MAIL_WAIT
        elif nxt:
            self.state = State.WAIT_BLOCK
        else:
            self._enter_select()

    def _mail(self, b: int) -> int:
        run = self.layout.mail_preamble
        if self.state is State.MAIL_WAIT:
            if b == run:
                self.state = State.MAIL_RUN       # Units before the 3DS's $20 run are patch-list leftovers.
            return run
        if self.state is State.MAIL_RUN:
            if b in (run, NO_DATA):
                return run
            self.state = State.MAIL_BODY
            self._out = deque(mail_block(self.party, self.family)[5:])
            self._mail_body = []
        self._mail_body.append(b)
        reply = self._out.popleft() if self._out else 0
        if len(self._mail_body) >= self.layout.mail_body:
            self.partner_mail = parse_mail(self._mail_body, self.family)
            self._enter_select()
        return reply

    # Trade screen ---------------------------------------------------------------------------

    def _enter_select(self) -> None:
        self.state = State.SELECT
        self._decision = Decision.PICK
        self._in_sync = False

    def _decide(self) -> int:
        if self._decision is Decision.PICK:
            return self._next_slot() if self.trades_left > 0 else CANCEL
        if self._decision is Decision.CONFIRM:
            return ACCEPT
        if self.mode.generation == 2 and self.trades:                 # Gen 2's check byte
            last = self.trades[-1]
            species = {last.sent.dex, last.received.dex}
            return 1 if MEW in species else 2 if CELEBI in species else 0
        return ACCEPT                                                  # Gen 1: the leftover value

    def _next_slot(self) -> int:
        """The party slot of the next queued Pokemon (the first slot once the queue is done)."""
        if self._pending:
            for slot, mon in enumerate(self.party.mons):
                if mon is self._pending[0]:
                    return slot
        return 0

    def _select(self, b: int) -> int:
        if is_sync(b):
            if not self._in_sync or self._sync_zeros:
                if self._in_sync:
                    self._end_sync()
                self._in_sync, self._sync_zeros = True, False
                self._their_nybble = b & 0x0F
                self._our_nybble = self._decide()
            if self.state is not State.SELECT:
                return self._respond(b)
            return (b & 0xF0) | self._our_nybble
        if b == 0 and self._in_sync:
            self._sync_zeros = True
            return 0
        if self._in_sync:
            self._end_sync()
            if self.state is not State.SELECT:
                return self._respond(b)
        if b == PREAMBLE:                       # A new round without the post-trade sync we expected.
            self.state = State.ROOM
            return self._respond(b)
        return 0 if b == 0 else NO_DATA

    def _end_sync(self) -> None:
        self._in_sync = self._sync_zeros = False
        theirs, ours = self._their_nybble, self._our_nybble
        if self._decision is Decision.PICK:
            if theirs == CANCEL and ours == CANCEL:
                self._note("both left the trade screen")
                self.state = State.ROOM
            elif theirs == CANCEL or ours == CANCEL:
                pass                                                   # Back to picking.
            else:
                self._their_pick, self._our_pick = theirs, ours
                self._decision = Decision.CONFIRM
                mon = self.partner.mons[theirs] if self.partner and theirs < len(self.partner.mons) else None
                self._note(f"partner offers slot {theirs}: {mon.describe() if mon else '?'}")
        elif self._decision is Decision.CONFIRM:
            if theirs == ACCEPT and ours == ACCEPT:
                self._trade()
                self._decision = Decision.AFTER_TRADE
            else:
                self._note("the partner declined the trade")
                self._decision = Decision.PICK
        else:
            self.state = State.ROOM                                    # The room repeats its exchanges.

    def _trade(self) -> None:
        if not self.partner or self._their_pick >= len(self.partner.mons):
            self._note("trade confirmed but the partner's party is unknown")
            return
        received = self.partner.mons[self._their_pick]
        mail = self.partner_mail[self._their_pick] if self.partner_mail else None
        if mail is not None and (received.held_item not in MAIL_ITEMS or not any(mail)):
            mail = None
        slot = self._our_pick if self._our_pick < len(self.party.mons) else 0
        sent = self.party.mons[slot]
        trade = Trade(sent, received, mail, gbpk.decode_name(self.partner.trainer_name, self.family))
        self.trades.append(trade)
        self.trades_left -= 1
        if self._pending and sent is self._pending[0]:
            self._pending.pop(0)
        # As the ROM does it: the Pokemon sent leaves the party, the one received joins at its end.
        mons = self.party.mons[:slot] + self.party.mons[slot + 1:] + [received]
        mails = list(self.party.mail) + [b""] * (len(self.party.mons) - len(self.party.mail))
        mails = mails[:slot] + mails[slot + 1:] + [mail or b""] if self.mode.mail else []
        self.party = Party(self.party.trainer_name, self.party.trainer_id, mons, mails)
        self._note(f"traded {sent.describe()} for {received.describe()}")
        if self.on_trade:
            self.on_trade(trade)
