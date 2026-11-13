"""The download server a DS Mystery Gift comes from: ``dls1.nintendowifi.net/download`` (docs/ds_wifi.md, "DLS1").

``action=count`` says how many files there are, ``action=list`` gives one line per file, and ``action=contents``
sends the file itself. A gift folder is a game code folder under ``--gifts``, laid out like the DLS1-Files archive
Wiimmfi serves and dwc_network_server_emulator's ``dlc``::

    dlc/ADA/10dp.myg                   Gen 4: Diamond, Pearl, Platinum, HeartGold and SoulSilver
    dlc/IRA/...
    dlc/IRAO/G0028_darkrai_en.bin      Gen 5: Black, White, Black 2 and White 2 share IRAO/IRAJ/IRAK

A ``_list.txt`` in a folder is six tab-separated fields per line (``name, description, attr1, attr2, attr3, size``);
without one every file in the folder is offered with blank attributes.

Gen 4 takes exactly one gift per connection, chosen by the DS's day of the year (change the DS date to choose).
Gen 5's Mystery Gift asks for ``attr1=MYSTERY_<language>`` and the file's ``attr2`` is a game mask (``0x100000``
Black, ``0x200000`` White, ``0x400000`` White 2, ``0x800000`` Black 2; ``0xF00000`` all four), further narrowed by
the real game code in ``rhgamecd``; one is chosen by date too. Files with other ``attr1`` values (the PWT
tournaments, C-Gear skins) use ``num``/``offset`` as the other DLC downloads do.

A game code is four characters (``ADAE`` US Diamond, ``CPUE`` US Platinum, ``IRAO`` international Gen 5). The
region's folder is used when it exists; else the three-letter shared folder (``ADA``, ``CPU``, ``IPG``, ``IRA``) is,
so one export serves every region of a game.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .http import Request, Response
from .nas import Sessions, decode_form

log = logging.getLogger(__name__)

SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")
GEN4_PREFIXES = ("ADA", "APA", "CPU", "IPG", "IPK")
GEN5_GAME_BITS = {"A": 0x100000, "B": 0x200000, "D": 0x400000, "E": 0x800000}
SHARED_FOLDER = {"ADA": "ADA", "APA": "ADA", "CPU": "CPU", "IPG": "IPG", "IPK": "IPG", "IRA": "IRA"}


@dataclass
class Entry:
    name: str
    description: str = ""
    attr1: str = ""
    attr2: str = ""
    attr3: str = ""
    size: int = 0

    def line(self) -> str:
        return "\t".join((self.name, self.description, self.attr1, self.attr2, self.attr3, str(self.size)))

    def mask(self) -> int:
        try:
            return int(self.attr2 or "0", 16)
        except ValueError:
            return 0


class DLS1:
    def __init__(self, gifts: str | Path, sessions: Sessions, gift: str | Path | None = None,
                 host: str = "dls1.nintendowifi.net") -> None:
        self.root = Path(gifts).resolve()
        self.sessions = sessions
        self.host = host
        self.forced: Entry | None = None
        self.forced_path: Path | None = None
        if gift:
            path = Path(gift)
            if not path.is_absolute():
                path = self.root / path
            if path.is_file():
                self.forced_path, self.forced = path.resolve(), self._entry(path)
            else:
                # A bare name is looked for in the folder the game asks about.
                self.forced = Entry(Path(gift).name)
        self.warned = False

    # -- the folder ------------------------------------------------------------------------------------------------

    def folder(self, gamecd: str) -> Path | None:
        """``root/<gamecd>``, or the shared three-letter folder; None for an unsafe code."""
        if not SAFE.match(gamecd or ""):
            return None
        prefix = gamecd[:3].upper()
        candidates = [self.root / gamecd]
        shared = SHARED_FOLDER.get(prefix)
        if shared:
            candidates.append(self.root / shared)
        for path in candidates:
            if path.is_dir() and path.resolve().is_relative_to(self.root):
                return path.resolve()
        return None

    @staticmethod
    def _entry(path: Path) -> Entry:
        return Entry(path.name, "", "", "", "", path.stat().st_size)

    def entries(self, folder: Path) -> list[Entry]:
        listing = folder / "_list.txt"
        out: list[Entry] = []
        if listing.is_file():
            try:
                for line in listing.read_text(encoding="latin-1").splitlines():
                    if not line.strip():
                        continue
                    parts = (line.split("\t") + [""] * 6)[:6]
                    try:
                        size = int(parts[5] or 0)
                    except ValueError:
                        size = 0
                    out.append(Entry(parts[0], parts[1], parts[2], parts[3], parts[4], size))
            except OSError:
                out = []
        else:
            try:
                for path in sorted(folder.iterdir()):
                    if path.is_file() and not path.name.startswith(("_", ".")):
                        out.append(self._entry(path))
            except OSError:
                out = []
        return out

    # -- choosing --------------------------------------------------------------------------------------------------

    @staticmethod
    def _filter(entries: list[Entry], post: dict) -> list[Entry]:
        """Keep the entries whose non-blank requested attributes match."""
        out = []
        for entry in entries:
            if all(not post.get(key) or getattr(entry, key) == post[key] for key in ("attr1", "attr2", "attr3")):
                out.append(entry)
        return out

    @staticmethod
    def _by_date(entries: list[Entry], devtime: str) -> list[Entry]:
        if not entries:
            return []
        try:
            day = time.strptime(devtime, "%y%m%d%H%M%S").tm_yday
        except (TypeError, ValueError):
            day = time.localtime().tm_yday
        return [entries[(day - 1) % len(entries)]]

    def _devtime(self, post: dict) -> str:
        session = self.sessions.get(post.get("token", ""))
        return session.devtime if session else ""

    def _select(self, entries: list[Entry], post: dict) -> tuple[list[Entry], int]:
        """(what list sends, what count says) for this request."""
        if self.forced is not None:
            if self.forced_path is not None:
                return [self.forced], 1
            matches = [entry for entry in entries if entry.name == self.forced.name]
            return matches[:1], len(matches)
        gamecd = post.get("gamecd", "").upper()
        attr1 = post.get("attr1", "")
        if gamecd.startswith("IRA") and attr1.upper().startswith("MYSTERY"):
            filtered = self._filter_mystery(entries, post)
            game_filtered = self._filter_gen5_game(filtered, post.get("rhgamecd", ""))
            return self._by_date(game_filtered, self._devtime(post)), len(game_filtered)
        filtered = self._filter(entries, post)
        if gamecd[:3] in GEN4_PREFIXES:
            picked = self._by_date(filtered, self._devtime(post))
            return picked, len(picked)
        offset = _int(post.get("offset"), 0)
        number = _int(post.get("num"), len(filtered)) or len(filtered)
        return filtered[offset:offset + number], len(filtered)

    @staticmethod
    def _filter_mystery(entries: list[Entry], post: dict) -> list[Entry]:
        """A MYSTERY_* entry is a gift for a set of games: the request's attr2, when present, is intersected
        rather than compared, because an "all Black and White" file (0x300000) reaches a Black cartridge (0x100000)."""
        attr1 = post.get("attr1", "")
        attr3 = post.get("attr3", "")
        mask = _mask(post.get("attr2"))
        return [entry for entry in entries
                if (not attr1 or entry.attr1 == attr1)
                and (not attr3 or entry.attr3 == attr3)
                and (not mask or entry.mask() & mask)]

    @staticmethod
    def _filter_gen5_game(entries: list[Entry], rhgamecd: str) -> list[Entry]:
        if len(rhgamecd) < 3:
            return entries
        bit = GEN5_GAME_BITS.get(rhgamecd[2].upper())
        if bit is None:
            return entries
        return [entry for entry in entries if entry.mask() & bit == bit]

    # -- HTTP ------------------------------------------------------------------------------------------------------

    def handle(self, request: Request) -> Response:
        if request.method != "POST" or request.path != "/download":
            return Response(404, b"", {"X-DLS-Host": f"http://{self.host}/"}, version="1.0")
        post = decode_form(request.body)
        post["ipaddr"] = request.client
        action = post.get("action", "").lower()
        gamecd = post.get("gamecd", "")
        folder = self.folder(gamecd)
        log.info("DLS1 %s: gamecd=%s rhgamecd=%s attr1=%s attr2=%s contents=%s num=%s offset=%s",
                 action, gamecd, post.get("rhgamecd", ""), post.get("attr1", ""), post.get("attr2", ""),
                 post.get("contents", ""), post.get("num", ""), post.get("offset", ""))
        headers = {"X-DLS-Host": f"http://{self.host}/"}
        if folder is None:
            log.warning("DLS1: no gift folder for gamecd %r under %s", gamecd, self.root)
            return Response(200, b"\r\n" if action == "list" else b"0", headers, version="1.0")
        if action in ("count", "list"):
            listed, count = self._select(self.entries(folder), post)
            if action == "count":
                return Response(200, str(count).encode("ascii"), headers, version="1.0")
            body = "".join(entry.line() + "\r\n" for entry in listed) or "\r\n"
            log.info("DLS1 list %s -> %s", gamecd, [entry.name for entry in listed])
            return Response(200, body.encode("latin-1"), headers, version="1.0")
        if action == "contents":
            return self._contents(folder, post, headers)
        log.warning("DLS1: unknown action %r", action)
        return Response(200, b"", headers, version="1.0")

    def _contents(self, folder: Path, post: dict, headers: dict) -> Response:
        name = Path(post.get("contents", "")).name
        if not name or name.startswith("_") or not SAFE.match(name):
            return Response(403, b"", headers, version="1.0")
        path = (folder / name).resolve()
        if not path.is_relative_to(folder) or not path.is_file():
            log.warning("DLS1: %s is not in %s", name, folder)
            return Response(404, b"", headers, version="1.0")
        data = path.read_bytes()
        log.info("DLS1 contents: %s (%d bytes) to %s", path.name, len(data), post.get("ipaddr", "?"))
        return Response(200, data, {**headers, "Content-Type": "application/x-dsdl",
                                    "Content-Disposition": f'attachment; filename="{name}"'}, version="1.0")


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mask(value) -> int:
    try:
        return int(value or "0", 16)
    except (TypeError, ValueError):
        return 0
