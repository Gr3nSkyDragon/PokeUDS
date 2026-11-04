"""Generation 6: Pokemon X/Y and Omega Ruby/Alpha Sapphire.

Both pairs trade locally from the PSS on the bottom screen with ``.pk6`` records, and X/Y and ORAS
trade with each other (verified with azahar_uds: X with Alpha Sapphire). What differs per game
lives in ``profiles/<game>.toml``.
"""

from ...pkx import PK6
from ..base import Game

XY = Game("xy", "Pokemon X/Y", 6, PK6, "PSS",
          {"X": 0x0004000000055D00, "Y": 0x0004000000055E00}, {24: "X", 25: "Y"}, ("oras",))
ORAS = Game("oras", "Pokemon Omega Ruby/Alpha Sapphire", 6, PK6, "PSS",
            {"Omega Ruby": 0x000400000011C400, "Alpha Sapphire": 0x000400000011C500},
            {27: "Omega Ruby", 26: "Alpha Sapphire"}, ("xy",))

GAMES = (XY, ORAS)
