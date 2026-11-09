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
    gen7_key: str = ""                 # rsa_d_key.txt (the Gen 7 distributor's OPSSR signing key)
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
    plaza: dict = field(default_factory=dict)       # Gen 7 Festival Plaza identity, made once: {pid, id8} (hex)
    language: str = ""                              # a language pack's plugin id; "" is English
    sprites: bool = True                            # download Pokemon sprites from PokeAPI (pokeuds.app.sprites)
    window: dict = field(default_factory=dict)      # the app window's last place and size (gui/window.py)

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

    def gen7_key_path(self) -> str:
        """The chosen rsa_d_key.txt, one beside aes_keys.txt, or the legacy local delivery_key.py."""
        if self.gen7_key:
            return self.gen7_key
        if self.keys:
            for name in ("rsa_d_key.txt", "gen7_delivery_key.txt"):
                path = Path(self.keys).with_name(name)
                if path.exists():
                    return str(path)
        from ..games.gen7 import delivery

        legacy = Path(delivery.__file__).with_name("delivery_key.py")
        if delivery.RSA_D is not None and legacy.exists():
            return str(legacy)
        return ""

    def gen7_key_problem(self) -> str:
        """Why the Gen 7 signing key cannot be used; "" when it is installed and readable."""
        from ..games.gen7 import delivery

        path = self.gen7_key_path()
        if not path:
            return "" if delivery.RSA_D is not None else "No Gen 7 signing key installed"
        legacy = Path(delivery.__file__).with_name("delivery_key.py")
        if Path(path).resolve() == legacy.resolve():
            return "" if delivery.RSA_D is not None else f"{path} has no RSA_D"
        from ..games.gen7 import keyextract

        try:
            key = keyextract.read_text_key(path)
        except FileNotFoundError:
            return f"{path} does not exist"
        except (OSError, ValueError) as exc:
            return str(exc)
        return "" if key is not None else f"{path} has no d= line"

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
