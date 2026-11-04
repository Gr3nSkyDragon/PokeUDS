"""What PokeUDS knows about a game, independent of its wire protocol."""

from __future__ import annotations

from dataclasses import dataclass

from ..pkx import Format


@dataclass(frozen=True)
class Game:
    key: str                     # Command-line name and profile file name.
    name: str
    generation: int
    record: Format               # The Pokemon record the game trades.
    hub: str                     # Where trading starts in the game.
    titles: dict[str, int]       # Version name -> title id.
    versions: dict[int, str]     # Record origin version byte (0xDF) -> version name.
    trades_with: tuple[str, ...] = ()   # Other game keys it trades with locally.
