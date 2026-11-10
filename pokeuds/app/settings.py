"""App settings, kept as JSON in the app's data folder (``data_dir``; ``POKEUDS_DATA`` overrides it).

The packaged Windows app keeps everything in its own folder: ``Data`` beside PokeUDS.exe (hidden) holds the
settings, sprites, plugins and gift projects; received Pokemon go to ``received`` beside the exe, as ``Bank``
holds the bank. Nothing is written to the user's profile, and a folder the app cannot write is reported rather
than replaced by one outside it: unzip PokeUDS somewhere writable. The release zip holds none of these folders,
so an update unzipped over the app leaves them as they are. On its first start ``Data`` takes a copy of what an
older version kept in ``%APPDATA%\\PokeUDS``. From source or in the Mac app (whose bundle an update replaces),
the data folder is the platform's own (``system_data_dir``).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)
PORTABLE_DATA = "Data"
RECEIVED = "received"                    # the received folder, beside PokeUDS.exe (not inside Data)
PORTABLE_OUTPUT = RECEIVED               # written to settings.json for the default output, so a moved app follows it
ADOPTED = ("settings.json", "sprites", "plugins", "plugin-data", "gifts")    # what the first start copies over
_portable: dict[Path, Path | None] = {}


class AppFolderNotWritable(RuntimeError):
    """The packaged app cannot write its own folder, and it will not fall back to a folder outside it."""


def system_data_dir() -> Path:
    """The platform's data folder: %APPDATA%\\PokeUDS, ~/Library/Application Support/PokeUDS, ~/.local/share/pokeuds."""
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "PokeUDS"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PokeUDS"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "pokeuds"


def app_folder() -> Path | None:
    """The packaged Windows app's own folder (where PokeUDS.exe is); None from source and on other systems."""
    if getattr(sys, "frozen", False) and sys.platform == "win32" and not os.environ.get("POKEUDS_DATA"):
        return Path(sys.executable).resolve().parent
    return None


def hide(folder: Path) -> None:
    """Windows shows a folder whose name starts with a dot, or any folder: mark it hidden."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(str(folder), 0x02)      # FILE_ATTRIBUTE_HIDDEN
        except (AttributeError, OSError):
            pass


def portable_data_dir(folder: Path | None = None) -> Path | None:
    """``Data`` in the app's folder (``folder``, default ``app_folder()``), made the first time with a copy of the
    older data folder's files; None when there is no app folder or it cannot be written."""
    folder = app_folder() if folder is None else folder
    if folder is None:
        return None
    if folder in _portable:
        return _portable[folder]
    target = folder / PORTABLE_DATA
    try:
        fresh = not target.exists()
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write-test"
        probe.write_bytes(b"")
        probe.unlink()
        if fresh:
            hide(target)
            _adopt(system_data_dir(), target)
    except OSError:
        target = None
    _portable[folder] = target
    return target


def _adopt(old: Path, new: Path) -> None:
    """Copy what an older version kept in ``old`` (read only; ``old`` is left as it was). A file that cannot be read
    is skipped: the app starts with its default instead."""
    for name in ADOPTED:
        source = old / name
        try:
            if source.is_dir():
                shutil.copytree(source, new / name, dirs_exist_ok=True)
            elif source.is_file():
                text = source.read_text(encoding="utf-8")
                if name == "settings.json":
                    text = _portable_settings(text, new)
                (new / name).write_text(text, encoding="utf-8")
        except (OSError, ValueError) as exc:
            log.info("not copied from %s: %s (%s)", old, name, exc)


def _portable_settings(text: str, data: Path) -> str:
    """Older settings, moved into the app's folder: a received folder left at the old default moves beside the app."""
    raw = json.loads(text)
    if isinstance(raw, dict) and raw.get("output") in (None, "", OLD_OUTPUT, str(data / RECEIVED)):
        raw["output"] = str(data.parent / RECEIVED)
    return json.dumps(raw, indent=2)


def data_dir() -> Path:
    if override := os.environ.get("POKEUDS_DATA"):
        return Path(override)
    app = app_folder()
    if app is not None:
        portable = portable_data_dir()
        if portable is None:
            raise AppFolderNotWritable(
                f"PokeUDS cannot write to its own folder ({app}). Move the whole PokeUDS folder to a writable "
                "place, such as Documents or the Desktop, and start it again. PokeUDS keeps its settings, bank "
                "and received Pokemon inside its folder.")
        return portable
    return system_data_dir()


OLD_OUTPUT = str(Path.home() / "PokeUDS" / "received")      # the received folder's default outside the packaged app


def default_output() -> str:
    """Where trades write what they receive: ``received`` beside the packaged app; else ~/PokeUDS/received."""
    app = app_folder()
    if app is not None:
        return str(app / RECEIVED)
    portable = portable_data_dir()
    return str(portable / RECEIVED) if portable is not None else OLD_OUTPUT


def _migrate_output(raw: dict) -> None:
    """An older settings file's output: its received folder moves beside the app, not inside Data or the profile."""
    if not isinstance(raw, dict):
        return
    app = app_folder()
    if app is not None and raw.get("output") in (None, "", OLD_OUTPUT, str(app / PORTABLE_DATA / RECEIVED)):
        raw["output"] = default_output()


@dataclass
class Settings:
    keys: str = ""                     # aes_keys.txt
    gen7_key: str = ""                 # rsa_d_key.txt (the Gen 7 distributor's OPSSR signing key)
    radio: str = "esp32"               # "esp32" or "ldnd"
    port: str = ""                     # ESP32 serial port or ldnd pipe; "" finds it
    output: str = field(default_factory=default_output)
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
        if isinstance(raw, dict) and raw.get("output") == PORTABLE_OUTPUT:
            raw["output"] = default_output()       # the default output follows the folder when the app is moved
        _migrate_output(raw)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> bool:
        """Write the settings; -> whether they were written. A file another program holds for a moment (an antivirus
        scanning the last save, a backup or sync tool) is tried again for about a second; after that the save is
        given up and logged, and the settings in memory stay as they are: a click never fails over it."""
        path = self.path()
        data = asdict(self)
        if data.get("output") == default_output():
            data["output"] = PORTABLE_OUTPUT       # keep the default relative to the folder, so a moved app follows it
        text = json.dumps(data, indent=2)
        problem: OSError | None = None
        for attempt in range(8):
            tmp = path.with_name(f"settings.{os.getpid()}.{attempt}.tmp")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(text, encoding="utf-8")
                tmp.replace(path)
                return True
            except OSError as exc:
                problem = exc
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                time.sleep(0.02 * (attempt + 1))
        try:
            path.write_text(text, encoding="utf-8")      # in place, as a last try
            return True
        except OSError:
            log.warning("could not save the settings to %s: %s", path, problem)
            return False

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
