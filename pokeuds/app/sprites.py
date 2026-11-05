"""Pokemon sprites for the app's previews: PokeAPI's sprite repository, fetched when first shown and kept.

The same source pokeldn's app uses (``sprites.front_default`` / ``front_shiny`` of PokeAPI's Pokemon):
https://github.com/PokeAPI/sprites, ``sprites/pokemon/<id>.png`` and ``sprites/pokemon/shiny/<id>.png``. Nothing
is bundled; each sprite is downloaded once into ``<data>/sprites`` and read from there after. Settings can turn
downloading off; the cache is still read.

A sprite is never needed: ``cached`` and ``get`` return PNG bytes or None and never raise. ``get`` blocks for at
most TIMEOUT, so it runs on a worker thread. A failed network is left alone for OFFLINE_COOLDOWN, and a sprite
PokeAPI does not have (404) is not asked for again for MISSING_TTL. Only a small, plain PNG is kept, rebuilt from
the chunks that draw it, so nothing else in a downloaded file reaches the image decoder.
"""

from __future__ import annotations

import os
import struct
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

from .settings import data_dir

BASE = os.environ.get("POKEUDS_SPRITE_BASE", "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon")
TIMEOUT = 5.0
OFFLINE_COOLDOWN = 60.0
MISSING_TTL = 7 * 86400.0
MAX_BYTES = 200_000
MAX_SIDE = 256
PNG = b"\x89PNG\r\n\x1a\n"
DRAWING_CHUNKS = (b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND")

# Game form numbers (PKHeX's, as Gen 6/7 records and cards hold them) -> PokeAPI's file for that form, for the
# forms that look different. A form not here shows the species' default sprite. From pokeldn's table, limited
# to the Pokemon and forms of Generations 1-7.
FORM_SPRITES: dict[tuple[int, int], int | str] = {
    **{(201, n): f"201-{name}" for n, name in enumerate((*"bcdefghijklmnopqrstuvwxyz", "exclamation", "question"),
                                                        start=1)},
    (351, 1): 10013, (351, 2): 10014, (351, 3): 10015,                          # Castform
    (386, 1): 10001, (386, 2): 10002, (386, 3): 10003,                          # Deoxys
    (412, 1): "412-sandy", (412, 2): "412-trash", (413, 1): 10004, (413, 2): 10005,
    (421, 1): "421-sunshine", (422, 1): "422-east", (423, 1): "423-east",
    (479, 1): 10008, (479, 2): 10009, (479, 3): 10010, (479, 4): 10011, (479, 5): 10012,   # Rotom
    (487, 1): 10007, (492, 1): 10006,                                           # Giratina, Shaymin
    (550, 1): 10016, (555, 1): 10017,                                           # Basculin, Darmanitan
    (641, 1): 10019, (642, 1): 10020, (645, 1): 10021, (646, 1): 10023, (646, 2): 10022, (647, 1): 10024,
    (649, 1): "649-douse", (649, 2): "649-shock", (649, 3): "649-burn", (649, 4): "649-chill",
    (678, 1): 10025, (681, 1): 10026,                                           # Meowstic, Aegislash
    (710, 1): 10027, (710, 2): 10028, (710, 3): 10029, (711, 1): 10030, (711, 2): 10031, (711, 3): 10032,
    (716, 0): "716-neutral", (716, 1): "716-active",                            # Xerneas
    # Alolan forms
    (19, 1): 10091, (20, 1): 10092, (26, 1): 10100, (27, 1): 10101, (28, 1): 10102, (37, 1): 10103,
    (38, 1): 10104, (50, 1): 10105, (51, 1): 10106, (52, 1): 10107, (53, 1): 10108, (74, 1): 10109,
    (75, 1): 10110, (76, 1): 10111, (88, 1): 10112, (89, 1): 10113, (103, 1): 10114, (105, 1): 10115,
    (744, 1): 10151, (745, 1): 10126, (745, 2): 10152, (746, 1): 10127,          # Rockruff, Lycanroc, Wishiwashi
    (778, 1): 10143, (800, 1): 10155, (800, 2): 10156, (801, 1): 10147,          # Mimikyu, Necrozma, Magearna
    **{(s, n): f"{s}-{name}" for s in (493, 773) for n, name in enumerate((
        "fighting", "flying", "poison", "ground", "rock", "bug", "ghost", "steel", "fire",
        "water", "grass", "electric", "psychic", "ice", "dragon", "dark", "fairy"), start=1)},
    **{(s, n): f"{s}-{name}" for s in (585, 586) for n, name in enumerate(("summer", "autumn", "winter"), start=1)},
}


def sprite_key(species: int, form: int = 0) -> int | str | None:
    """PokeAPI's file name for a species and game form; None for no species."""
    if not 0 < species <= 1025:
        return None
    return FORM_SPRITES.get((species, form), species)


def clean(data: bytes) -> bytes | None:
    """``data`` as a plain PNG, rebuilt from its drawing chunks alone; None for anything else: not a PNG, damaged,
    animated, too large, or bigger than MAX_SIDE either way."""
    if len(data) > MAX_BYTES or not data.startswith(PNG):
        return None
    chunks: dict[bytes, bytes] = {}
    pos = len(PNG)
    try:
        while pos < len(data):
            size, kind = struct.unpack_from(">I4s", data, pos)
            end = pos + 12 + size
            if end > len(data) or (pos == len(PNG) and kind != b"IHDR") or kind in (b"acTL", b"fcTL", b"fdAT"):
                return None
            body = data[pos + 8:pos + 8 + size]
            if zlib.crc32(kind + body) != struct.unpack_from(">I", data, end - 4)[0]:
                return None
            if kind in DRAWING_CHUNKS:
                if kind in chunks and kind != b"IDAT":
                    return None
                chunks[kind] = chunks.get(kind, b"") + body
            elif not kind[0] & 0x20:                      # an unknown critical chunk: not a PNG we can trust
                return None
            pos = end
            if kind == b"IEND":
                break
        width, height = struct.unpack_from(">II", chunks[b"IHDR"])
    except (struct.error, KeyError):
        return None
    if b"IEND" not in chunks or b"IDAT" not in chunks or not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
        return None
    return PNG + b"".join(struct.pack(">I", len(chunks[k])) + k + chunks[k] + struct.pack(">I", zlib.crc32(k + chunks[k]))
                          for k in DRAWING_CHUNKS if k in chunks)


class SpriteCache:
    def __init__(self, folder: Path | None = None, base: str = BASE) -> None:
        self.folder = Path(folder) if folder else data_dir() / "sprites"
        self.base = base.rstrip("/")
        self.online = True
        self._offline_until = 0.0
        self._memory: dict[tuple[int | str, bool], bytes] = {}
        self._slots = threading.BoundedSemaphore(4)

    def _path(self, key: int | str, shiny: bool) -> Path:
        return self.folder / ("shiny" if shiny else "normal") / f"{key}.png"

    def cached(self, key: int | str | None, shiny: bool = False) -> bytes | None:
        """The sprite already here, or None. Never touches the network; a damaged file is removed."""
        if key is None:
            return None
        if (key, shiny) in self._memory:
            return self._memory[(key, shiny)]
        path = self._path(key, shiny)
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        data = clean(raw)
        if data is None:
            path.unlink(missing_ok=True)
            return None
        self._memory[(key, shiny)] = data
        return data

    def missing(self, key: int | str, shiny: bool = False) -> bool:
        """PokeAPI said it has no such sprite, lately."""
        try:
            return time.time() - self._path(key, shiny).with_suffix(".none").stat().st_mtime < MISSING_TTL
        except OSError:
            return False

    def get(self, key: int | str | None, shiny: bool = False) -> bytes | None:
        """The sprite: from here, else downloaded once. Blocks up to TIMEOUT; call it off the UI thread."""
        if key is None:
            return None
        if (data := self.cached(key, shiny)) is not None:
            return data
        if not self.online or self.missing(key, shiny) or time.monotonic() < self._offline_until:
            return None
        with self._slots:
            if (data := self.cached(key, shiny)) is not None:
                return data
            return self._download(key, shiny)

    def _download(self, key: int | str, shiny: bool) -> bytes | None:
        url = f"{self.base}/{'shiny/' if shiny else ''}{key}.png"
        path = self._path(key, shiny)
        request = urllib.request.Request(url, headers={"User-Agent": "PokeUDS"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as reply:   # noqa: S310 - a fixed https address
                raw = reply.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.with_suffix(".none").touch()
                except OSError:
                    pass
            else:
                self._offline_until = time.monotonic() + OFFLINE_COOLDOWN
            return None
        except (OSError, ValueError):                     # no network, DNS, a timeout
            self._offline_until = time.monotonic() + OFFLINE_COOLDOWN
            return None
        data = clean(raw)
        if data is None:
            return None
        self._memory[(key, shiny)] = data
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{key}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
            path.with_suffix(".none").unlink(missing_ok=True)
        except OSError:
            pass                                          # a read-only data folder still shows it this session
        return data

    def clear(self) -> int:
        """Delete every kept sprite; returns how many files went."""
        self._memory.clear()
        removed = 0
        if self.folder.is_dir():
            for path in self.folder.rglob("*"):
                if path.is_file() and path.suffix in (".png", ".none", ".tmp"):
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed


CACHE = SpriteCache()
