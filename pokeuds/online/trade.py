"""An online partner as a trade's offer source (``games.offers``), and the check a partner's record passes before the
console sees it (docs/online.md, Online trades).

Each side is an ordinary local trade: PokeUDS hosts its console's trade over its own board, and ``Offers`` hands the
session the record the partner's console offered, tells the partner what this console offered and answered, and
lets PokeUDS say yes only once the partner's console has said yes to this console's offer.
"""

from __future__ import annotations

import logging

from .. import __version__, pkx, species
from .link import Partner

log = logging.getLogger(__name__)

# Which games meet in which room: the games that trade with each other locally.
ROOMS = {"xy": "gen6", "oras": "gen6", "sm": "gen7", "usum": "gen7"}


def checker(game: str):
    """-> validate(record) -> (the reason it is refused or None, what it is). ``game`` is this console's (PKHeX's
    key: xy, oras, sm, usum), so a record holding what only the partner's game has never reaches it. A record PKHeX
    cannot read is refused, as is one with something the game never released; any other legality flag reaches the
    player with the record."""
    def validate(record: bytes):
        if len(record) not in (pkx.STORED_SIZE, pkx.PARTY_SIZE) or not pkx.checksum_valid(record):
            return "it is not a Generation 6 or 7 Pokemon (wrong size or checksum)", ""
        from .. import pkhex

        try:
            built = pkhex.shared().check(game, record)
        except pkhex.HelperMissing:
            return "PKHeX is not set up in the receiver's PokeUDS, so it cannot check a Pokemon", ""
        except (ValueError, RuntimeError, OSError) as exc:
            return f"PKHeX could not read it ({exc})", ""
        s = pkx.summary(record)
        shiny = " shiny" if s.shiny else ""
        level = f" level {s.level}" if s.level else ""
        what = f"{s.nickname or species.name(s.species)}{level} [species {s.species}{shiny}]"
        report = [line.strip() for line in str(built.report).splitlines() if line.strip()]
        unreleased = [line for line in report if "unreleased" in line.lower()]
        if unreleased:
            return f"PKHeX finds something the game never released ({unreleased[0]})", ""
        if not built.legal:
            what += f" (PKHeX flags it: {report[0] if report else 'not legal'})"
        return None, what
    return validate


def partner(game: str, code: str = "", name: str = "", say=print, **kw) -> Partner:
    """A started partner for this console's game (``ROOMS``), checking what it offers with ``checker(game)``."""
    found = Partner(ROOMS[game], code, name=name, app=__version__, say=say, validate=checker(game), **kw)
    found.start()
    return found


class Offers:
    """``games.offers.OfferSource`` over a ``Partner``. The partner's offer is what PokeUDS offers its console; it
    says yes once its console has and the partner's console has said yes to this console's offer (``link.digest``).
    After any decline the partner's standing offer is not offered again: both consoles offer afresh, and so do both
    partners (the side that declined took its offer back, the other declines its console in turn)."""

    offers_first = False

    def __init__(self, partner: Partner, trades: int | None = None) -> None:
        self.partner = partner
        self.trades = trades
        self.done = 0
        self._shown: bytes | None = None     # the partner's record offered to the console
        self._stale: bytes | None = None     # the partner's record from before a decline

    def offer(self) -> bytes | None:
        with self.partner.lock:
            record = self.partner.theirs().offer
            if record is None or record is self._stale:
                return None
            self._shown = record
            return record

    def ended(self) -> str:
        if self.partner.state in ("lost", "closed"):
            return self.partner.ended or "the online partner is gone"
        if self.trades is not None and self.done >= self.trades:
            return "every trade is done"
        return ""

    def console_offers(self, record: bytes) -> None:
        self.partner.offer(record)

    def console_withdrew(self) -> None:
        self.partner.withdraw()
        self._stale, self._shown = self._shown, None

    def console_accepts(self) -> None:
        self.partner.accept(self._shown)

    def partner_accepts(self) -> bool | None:
        with self.partner.lock:
            theirs = self.partner.theirs()
            if theirs.refused or theirs.offer is None or theirs.offer is not self._shown:
                return None
            return True if theirs.accepted else False

    def traded(self, record: bytes) -> None:
        self.partner.done()
        self.done += 1
        self._shown = self._stale = None

    def close(self, reason: str = "the trade ended") -> None:
        self.partner.close(reason)
