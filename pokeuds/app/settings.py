"""App settings, kept as JSON in the platform's data folder (``POKEUDS_DATA`` overrides it)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def data_dir() -> Path:
    if override := os.environ.get("POKEUDS_DATA"):
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "PokeUDS"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PokeUDS"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "pokeuds"


@dataclass
class Settings:
    keys: str = ""                     # aes_keys.txt
    radio: str = "esp32"               # "esp32" or "ldnd"
    port: str = ""                     # ESP32 serial port or ldnd pipe; "" finds it
    output: str = str(Path.home() / "PokeUDS" / "received")
    bank: str = ""                     # the bank folder; "" is <data>/bank
    trainer_name: str = "POKEUDS"
    debug: bool = False
    last_game: str = "rby"
    offers: dict = field(default_factory=dict)      # game -> the offer: the first Pokemon of its trade queue
    queued: dict = field(default_factory=dict)      # game -> the files queued after the offer, traded in order
    trainer: dict = field(default_factory=dict)     # the Builder's OT: {ot, tid, sid, female}
    pss: dict = field(default_factory=dict)         # Gen 6 PSS identity, made once: {id, key} (hex)
    language: str = ""                              # a language pack's plugin id; "" is English
    sprites: bool = True                            # download Pokemon sprites from PokeAPI (pokeuds.app.sprites)

    def offer_queue(self, game: str) -> list[str]:
        """The game's trade queue: the offer, then what is queued after it."""
        first = self.offers.get(game) or ""
        return ([first] if first else []) + [p for p in self.queued.get(game) or [] if p]

    def set_offer_queue(self, game: str, paths: list[str]) -> None:
        paths = [str(p) for p in paths if p]
        if paths:
            self.offers[game] = paths[0]
        else:
            self.offers.pop(game, None)
        if len(paths) > 1:
            self.queued[game] = paths[1:]
        else:
            self.queued.pop(game, None)

    def radio_spec(self) -> str:
        return f"{self.radio}:{self.port}" if self.port else self.radio

    @classmethod
    def path(cls) -> Path:
        return data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        try:
            raw = json.loads(cls.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)

    def keys_problem(self) -> str:
        if not self.keys:
            return "No aes_keys.txt chosen"
        try:
            from ..keys import KeyFile

            KeyFile.load(self.keys).uds_key()
        except FileNotFoundError:
            return f"{self.keys} does not exist"
        except KeyError as exc:
            return str(exc).strip("'\"")
        except OSError as exc:
            return str(exc)
        return ""
