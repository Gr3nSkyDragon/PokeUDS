"""Game protocols on top of UDS.

Implemented as alpha (UDS session in both roles, trade messages pending captures): Pokemon X/Y and
ORAS (``gen6``), Sun/Moon and Ultra Sun/Ultra Moon (``gen7``). The Virtual Console Game Boy titles
(``vc``, with the trade itself in ``pokeuds.vc``) implement the whole trade, tested in simulation.
"""

from __future__ import annotations

from . import gen6, gen7
from .base import Game

GAMES: dict[str, Game] = {g.key: g for g in (*gen6.GAMES, *gen7.GAMES)}

# Origin versions a Generation 6/7 record can carry, for display: the 3DS games, plus Pokemon GO
# and the Virtual Console games that Pokemon Bank moves into Generation 7.
VERSION_NAMES: dict[int, str] = {
    **{v: n for g in GAMES.values() for v, n in g.versions.items()},
    34: "Pokemon GO", 35: "Red (VC)", 36: "Green (VC)", 37: "Blue (VC)", 38: "Yellow (VC)",
    39: "Gold (VC)", 40: "Silver (VC)", 41: "Crystal (VC)",
}


def get(key: str) -> Game:
    try:
        return GAMES[key]
    except KeyError:
        raise KeyError(f"unknown game {key!r}; known: {', '.join(GAMES)}") from None


__all__ = ["GAMES", "Game", "VERSION_NAMES", "get"]
