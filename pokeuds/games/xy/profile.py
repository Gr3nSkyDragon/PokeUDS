"""Pokemon X/Y constants, loaded from ``profiles/xy.toml`` (or ``xy.local.toml``).

Nothing here is guessed: each value comes from a capture and is recorded in the profile file, so
the code never ships a made-up comm id or passphrase.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ...util import parse_hex

PROFILE_DIR = Path(__file__).resolve().parents[3] / "profiles"

# Title ids, for reference in captures and docs.
TITLE_IDS = {"X": 0x0004000000055D00, "Y": 0x0004000000055E00}


@dataclass
class XyProfile:
    wlan_comm_id: int | None = None
    passphrase: bytes | None = None
    id: int = 0
    max_nodes: int = 2
    channel: int = 0
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


def load(path: str | Path | None = None) -> XyProfile:
    if path is None:
        local = PROFILE_DIR / "xy.local.toml"
        path = local if local.exists() else PROFILE_DIR / "xy.toml"
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    network, trade = raw.get("network", {}), raw.get("trade", {})
    comm = str(network.get("wlan_comm_id", "")).strip()
    return XyProfile(
        wlan_comm_id=int(comm, 0) if comm else None,
        passphrase=parse_hex(network.get("passphrase_hex", "")) or None,
        id=int(network.get("id", 0)),
        max_nodes=int(network.get("max_nodes", 2)),
        channel=int(network.get("channel", 0)),
        data_channels=[int(c) for c in trade.get("data_channels", [])],
        source=str(path),
    )
