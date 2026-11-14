"""Where each trade's offer comes from, and who hears what the console did (docs/online.md, How a trade takes its
partner).

A trade session asks its source for the record to offer, tells it what the console offered, whether the console said
yes or took its offer back, and when a swap is done. Every method returns at once; a session that has to wait for an
answer keeps its console's session running and asks again (``Gen6Session.wait``), so a console is never left
without its heartbeats while a partner far away decides.

Two sources:

- ``Queue``: what PokeUDS has always done. The records are known up front, PokeUDS offers before the console does,
  and its yes needs nobody else's.
- an online partner (``pokeuds.online.trade``): the record offered to the console is the one the partner's console
  offered, so PokeUDS offers only after its own console has, and says yes only once both consoles have.
- ``Mirror`` (Touch Trade): the record offered to the console is the console's own offer, so it trades a Pokemon for
  itself and a trade evolution happens on the console.
"""

from __future__ import annotations

from typing import Protocol


class OfferSource(Protocol):
    # PokeUDS offers as soon as the trade screen is up, before the console's offer (the queue). False: PokeUDS takes
    # the console's offer first and offers once ``offer()`` has a record (an online partner).
    offers_first: bool

    def offer(self) -> bytes | None:
        """This trade's record (decrypted), or None while it is not known yet."""

    def ended(self) -> str:
        """Why PokeUDS should leave the trade screen now (every trade done, the partner gone), or ""."""

    def console_offers(self, record: bytes) -> None:
        """The console put ``record`` (decrypted) up for this trade."""

    def console_withdrew(self) -> None:
        """The console declined, said no, or took its offer back; it may offer again."""

    def console_accepts(self) -> None:
        """The console said yes to this trade."""

    def partner_accepts(self) -> bool | None:
        """Whether PokeUDS may say yes to its console now: True yes, False not yet, None never (the other side
        took its offer back, so PokeUDS declines)."""

    def traded(self, record: bytes) -> None:
        """The swap is done and ``record`` arrived; the next trade starts."""


class Queue:
    """The offers known up front: each trade the next record (round again past the end), ``trades`` of them."""

    offers_first = True

    def __init__(self, records: list[bytes], trades: int = 1) -> None:
        if not records:
            raise ValueError("a queue needs at least one record to offer")
        self.records = list(records)
        self.trades = trades
        self.done = 0

    def offer(self) -> bytes | None:
        return self.records[self.done % len(self.records)]

    def ended(self) -> str:
        return "every trade is done" if self.done >= self.trades else ""

    def console_offers(self, record: bytes) -> None:
        pass

    def console_withdrew(self) -> None:
        pass

    def console_accepts(self) -> None:
        pass

    def partner_accepts(self) -> bool | None:
        return True

    def traded(self, record: bytes) -> None:
        self.done += 1


class Mirror:
    """Touch Trade: PokeUDS offers back whatever the console offers, in the same trade, so the console receives its own
    Pokemon and a trade evolution happens on it (Kadabra, Machoke, an Onix holding a Metal Coat...). PokeUDS keeps
    nothing: the console trades its Pokemon for itself. Karrablast and Shelmet evolve only when traded for each other,
    so not this way. ``trades``: how many before PokeUDS leaves the trade screen (None: until the console leaves)."""

    offers_first = False

    def __init__(self, trades: int | None = None) -> None:
        self.trades = trades
        self.done = 0
        self._offer: bytes | None = None
        self.last: tuple[bytes, bytes | None] | None = None      # the last trade: (sent, received)

    def offer(self) -> bytes | None:
        return self._offer

    def ended(self) -> str:
        return "every trade is done" if self.trades is not None and self.done >= self.trades else ""

    def console_offers(self, record: bytes) -> None:
        self._offer = bytes(record)

    def console_withdrew(self) -> None:
        self._offer = None

    def console_accepts(self) -> None:
        pass

    def partner_accepts(self) -> bool | None:
        return True if self._offer is not None else None

    def traded(self, record: bytes) -> None:
        self.last = (bytes(record), self._offer)
        self.done += 1
        self._offer = None

    def close(self, reason: str = "") -> None:
        pass
