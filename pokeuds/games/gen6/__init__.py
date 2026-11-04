"""Generation 6: Pokemon X/Y and Omega Ruby/Alpha Sapphire.

Both pairs trade locally from the PSS on the bottom screen and use the same 232/260-byte record
(``pokeuds.pk6``); X/Y and ORAS also trade with each other (verified with azahar_uds: X with
Alpha Sapphire). What differs per game lives in ``profiles/<game>.toml``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Game:
    key: str                     # Command-line name and profile file name.
    name: str
    titles: dict[str, int]       # Version name -> title id.
    versions: dict[int, str]     # pk6 origin version byte (0xDF) -> version name.


GAMES = {
    "xy": Game("xy", "Pokemon X/Y", {"X": 0x0004000000055D00, "Y": 0x0004000000055E00}, {24: "X", 25: "Y"}),
    "oras": Game("oras", "Pokemon Omega Ruby/Alpha Sapphire",
                 {"Omega Ruby": 0x000400000011C400, "Alpha Sapphire": 0x000400000011C500},
                 {27: "Omega Ruby", 26: "Alpha Sapphire"}),
}

# Origin versions a Generation 6 record can carry, for display (Gen 6 games plus transfers).
VERSION_NAMES = {v: n for g in GAMES.values() for v, n in g.versions.items()}


def get(key: str) -> Game:
    try:
        return GAMES[key]
    except KeyError:
        raise KeyError(f"unknown Generation 6 game {key!r}; known: {', '.join(GAMES)}") from None
