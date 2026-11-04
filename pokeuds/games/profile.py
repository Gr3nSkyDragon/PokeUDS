"""Per-game constants, loaded from ``profiles/<game>.toml`` (or ``<game>.local.toml``).

Nothing here is guessed: each value comes from a capture and is recorded in the profile file, so
the code never ships a made-up comm id or passphrase.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..util import parse_hex

PROFILE_DIR = Path(__file__).resolve().parents[2] / "profiles"


@dataclass
class Profile:
    game: str
    wlan_comm_id: int | None = None
    passphrase: bytes | None = None
    id: int = 0
    max_nodes: int = 2
    channel: int = 0
    application_data: bytes = b""
    # Networks of other games this one can join (e.g. ORAS joining X/Y), if their comm ids differ.
    also_join_comm_ids: list[int] = field(default_factory=list)
    data_channels: list[int] = field(default_factory=list)
    source: str = ""

    def missing(self) -> list[str]:
        gaps = []
        if self.wlan_comm_id is None:
            gaps.append("network.wlan_comm_id")
        if not self.passphrase:
            gaps.append("network.passphrase_hex")
        if not self.data_channels:
            gaps.append("trade.data_channels")
        return gaps

    def join_comm_ids(self) -> list[int]:
        ids = [self.wlan_comm_id] if self.wlan_comm_id is not None else []
        return ids + [c for c in self.also_join_comm_ids if c not in ids]


def default_path(game: str) -> Path:
    local = PROFILE_DIR / f"{game}.local.toml"
    return local if local.exists() else PROFILE_DIR / f"{game}.toml"


def _int(value) -> int | None:
    text = str(value).strip()
    return int(text, 0) if text else None


def load(game: str, path: str | Path | None = None) -> Profile:
    path = Path(path) if path else default_path(game)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    network, trade = raw.get("network", {}), raw.get("trade", {})
    return Profile(
        game=game,
        wlan_comm_id=_int(network.get("wlan_comm_id", "")),
        passphrase=parse_hex(network.get("passphrase_hex", "")) or None,
        id=int(network.get("id", 0)),
        max_nodes=int(network.get("max_nodes", 2)),
        channel=int(network.get("channel", 0)),
        application_data=parse_hex(network.get("application_data_hex", "")),
        also_join_comm_ids=[c for c in (_int(v) for v in network.get("also_join_comm_ids", [])) if c is not None],
        data_channels=[int(c) for c in trade.get("data_channels", [])],
        source=str(path),
    )
