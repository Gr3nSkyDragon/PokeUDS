"""The bank: the Pokemon the app keeps between trades.

Each record is a file in the bank folder (``<data>/bank``, or Settings' bank folder) with a ``.json``
beside it, as pokeldn's bank keeps them:

    20261104-153012_025_Sparky_1a2b3c4d.pk6     the record, as PKHeX writes it (decrypted, stored)
    20261104-153012_025_Sparky_1a2b3c4d.json    {"source", "created", "origin", "game", "note", "legal", ...}
    20261104-153012_025_Sparky_1a2b3c4d.mail    a Gen 2 Pokemon's mail, when it holds one

Records are kept once: a file whose content is already banked is not added again. What a trade
receives lands in the output folder first (pokeuds.received) and is deposited from there.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import gbpk, pkx, species
from .settings import data_dir

EXTENSIONS = (".pk1", ".pk2", ".pk6", ".pk7")
IMPORTABLE = EXTENSIONS + (".ek6", ".ek7")
SOURCES = {"received": "Received", "imported": "Imported", "built": "Built", "converted": "Converted"}
_UNSAFE = re.compile(r"[^\w\-]+", re.UNICODE)


def default_folder() -> Path:
    return data_dir() / "bank"


@dataclass(frozen=True)
class Entry:
    id: str
    path: Path
    extension: str           # "pk1", "pk2", "pk6", "pk7"
    generation: int
    dex: int
    species: str
    nickname: str
    level: int | None
    shiny: bool
    ot: str
    tid: int
    held_item: int
    source: str              # a SOURCES key
    created: float
    origin: str = ""         # the file it came from
    note: str = ""
    legal: bool | None = None
    report: str = ""
    has_mail: bool = False
    meta: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def when(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.created))

    def describe(self) -> str:
        name = f"{self.species}" + (f" '{self.nickname}'" if self.nickname and self.nickname.upper() != self.species.upper() else "")
        level = f" Lv {self.level}" if self.level else ""
        return f"{name}{level}, OT {self.ot} {self.tid:05d}"

    def data(self) -> bytes:
        return self.path.read_bytes()


# -- reading records ------------------------------------------------------------------------------

@dataclass
class Parsed:
    content: bytes           # what the bank writes: a .pk1/.pk2 as is, a Gen 6/7 record decrypted and stored
    extension: str
    generation: int
    dex: int
    nickname: str
    level: int | None
    shiny: bool
    ot: str
    tid: int
    held_item: int


def parse(path: str | Path, raw: bytes | None = None) -> Parsed:
    """Read a .pk1/.pk2/.pk6/.pk7/.ek6/.ek7, or raise ValueError saying why it is not one."""
    path = Path(path)
    raw = Path(path).expanduser().read_bytes() if raw is None else raw
    ext = path.suffix.lower()
    if ext in (".pk1", ".pk2"):
        record = gbpk.Record.from_file(raw)
        if record.dex == 0:
            raise ValueError(f"{path.name}: not a real species (index {record.data[0]:#04x})")
        if f".{record.extension}" != ext:
            raise ValueError(f"{path.name}: the file holds a Generation {record.generation} Pokemon")
        shiny = record.generation == 2 and _gen2_shiny(record.data[21:23])   # Gen 1 has no shininess
        return Parsed(raw, ext[1:], record.generation, record.dex, gbpk.decode_name(record.nickname), record.level,
                      shiny, gbpk.decode_name(record.ot_name), record.ot_id, record.held_item)
    if ext in (".pk6", ".pk7", ".ek6", ".ek7"):
        if len(raw) not in pkx.SIZES:
            raise ValueError(f"{path.name}: {len(raw)} bytes is not a Generation 6/7 record (232 or 260)")
        plain = raw if pkx.checksum_valid(raw) else pkx.decrypt(raw)
        if not pkx.checksum_valid(plain):
            raise ValueError(f"{path.name}: checksum fails both as plain and as encrypted data")
        s = pkx.summary(plain)
        gen = 6 if ext in (".pk6", ".ek6") else 7
        fmt = pkx.PK6 if gen == 6 else pkx.PK7
        if not 0 < s.species <= fmt.max_species:
            raise ValueError(f"{path.name}: species {s.species} does not exist in Generation {gen}")
        level = s.level or species.level_for_exp(s.species, int.from_bytes(plain[0x10:0x14], "little"))
        return Parsed(pkx.to_stored(plain), f"pk{gen}", gen, s.species, s.nickname, level, s.shiny, s.ot_name,
                      s.tid if gen == 6 else _g7_tid(plain), s.held_item)
    raise ValueError(f"{path.name}: not a Pokemon file ({', '.join(IMPORTABLE)})")


def _gen2_shiny(dvs: bytes) -> bool:
    """pokecrystal CheckShininess: Defense, Speed and Special DVs 10, Attack 2, 3, 6, 7, 10, 11, 14 or 15."""
    atk, dfn, spd, spc = dvs[0] >> 4, dvs[0] & 15, dvs[1] >> 4, dvs[1] & 15
    return dfn == 10 and spd == 10 and spc == 10 and atk in (2, 3, 6, 7, 10, 11, 14, 15)


def _g7_tid(plain: bytes) -> int:
    """Gen 7 shows a six-digit trainer ID: (SID << 16 | TID) mod 1,000,000."""
    import struct

    tid, sid = struct.unpack_from("<HH", plain, 0x0C)
    return ((sid << 16) | tid) % 1_000_000


# -- the folder -----------------------------------------------------------------------------------

class Bank:
    def __init__(self, folder: str | Path | None = None) -> None:
        self.folder = Path(folder).expanduser() if folder else default_folder()

    # listing

    def entries(self) -> list[Entry]:
        """Newest first."""
        if not self.folder.is_dir():
            return []
        found = [e for p in self.folder.iterdir() if p.suffix in EXTENSIONS and (e := self._entry(p))]
        return sorted(found, key=lambda e: (e.created, e.id), reverse=True)

    def find(self, entry_id: str) -> Entry | None:
        for ext in EXTENSIONS:
            path = self.folder / f"{entry_id}{ext}"
            if path.exists():
                return self._entry(path)
        return None

    def _entry(self, path: Path) -> Entry | None:
        try:
            parsed = parse(path)
        except (OSError, ValueError):
            return None
        meta = self._meta(path)
        return Entry(path.stem, path, parsed.extension, parsed.generation, parsed.dex, species.name(parsed.dex),
                     parsed.nickname, parsed.level, parsed.shiny, parsed.ot, parsed.tid, parsed.held_item,
                     meta.get("source", "imported"), float(meta.get("created", path.stat().st_mtime)),
                     meta.get("origin", ""), meta.get("note", ""), meta.get("legal"), meta.get("report", ""),
                     path.with_suffix(".mail").exists(), meta)

    @staticmethod
    def _meta(path: Path) -> dict:
        try:
            meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
            return meta if isinstance(meta, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_meta(self, path: Path, meta: dict) -> None:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path.with_suffix(".json"))

    def _digests(self) -> dict[str, str]:
        """content digest -> entry id, for everything in the bank."""
        out = {}
        if self.folder.is_dir():
            for p in self.folder.iterdir():
                if p.suffix in EXTENSIONS:
                    meta = self._meta(p)
                    digest = meta.get("digest") or hashlib.sha256(p.read_bytes()).hexdigest()
                    out[digest] = p.stem
        return out

    # adding

    def deposit(self, content: bytes, extension: str, *, source: str, origin: str = "", mail: bytes | None = None,
                note: str = "", extra: dict | None = None, digests: dict | None = None) -> tuple[Entry, bool]:
        """Keep a record; returns (entry, added). A record already in the bank is not added again."""
        parsed = parse(Path(f"x.{extension}"), content)
        digest = hashlib.sha256(parsed.content).hexdigest()
        digests = self._digests() if digests is None else digests
        if digest in digests and (existing := self.find(digests[digest])):
            return existing, False
        self.folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        nick = _UNSAFE.sub("", parsed.nickname)[:12] or "unnamed"
        entry_id = f"{stamp}_{parsed.dex:03d}_{nick}_{digest[:8]}"
        path = self.folder / f"{entry_id}.{parsed.extension}"
        path.write_bytes(parsed.content)
        if mail and parsed.extension == "pk2":
            path.with_suffix(".mail").write_bytes(mail)
        self._write_meta(path, {"source": source, "created": time.time(), "origin": origin, "note": note,
                                "digest": digest, **(extra or {})})
        digests[digest] = entry_id
        return self._entry(path), True

    def import_file(self, path: str | Path, source: str = "imported") -> tuple[Entry, bool]:
        path = Path(path).expanduser()
        parsed = parse(path)
        mail = path.with_suffix(".mail")
        return self.deposit(parsed.content, parsed.extension, source=source, origin=str(path),
                            mail=mail.read_bytes() if parsed.extension == "pk2" and mail.exists() else None)

    def sync_received(self, folder: str | Path) -> list[Entry]:
        """Deposit every Pokemon in the received folder that the bank does not hold yet."""
        root = Path(folder).expanduser()
        if not root.is_dir():
            return []
        digests = self._digests()
        seen = set(self._seen(root))
        added = []
        for path in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime):
            if path.suffix.lower() not in EXTENSIONS or path.name in seen:
                continue
            try:
                parsed = parse(path)
                mail = path.with_suffix(".mail")
                entry, new = self.deposit(parsed.content, parsed.extension, source="received", origin=str(path),
                                          mail=mail.read_bytes() if mail.exists() else None, digests=digests)
            except (OSError, ValueError):
                continue
            seen.add(path.name)
            if new:
                added.append(entry)
        self._write_seen(root, seen)
        return added

    def _seen_path(self) -> Path:
        return self.folder / "received.json"

    def _seen(self, root: Path) -> list[str]:
        """Received files already looked at, so one deleted from the bank is not brought back."""
        try:
            return json.loads(self._seen_path().read_text(encoding="utf-8")).get(str(root), [])
        except (OSError, ValueError, AttributeError):
            return []

    def _write_seen(self, root: Path, names: set[str]) -> None:
        try:
            data = json.loads(self._seen_path().read_text(encoding="utf-8"))
            data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            data = {}
        data[str(root)] = sorted(names)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._seen_path().write_text(json.dumps(data, indent=1), encoding="utf-8")

    # changing

    def set_note(self, entry: Entry, note: str) -> Entry:
        meta = self._meta(entry.path)
        meta["note"] = note
        self._write_meta(entry.path, meta)
        return self._entry(entry.path)

    def set_legality(self, entry: Entry, legal: bool, report: str, encounter: str = "") -> Entry:
        meta = self._meta(entry.path)
        meta.update(legal=legal, report=report, encounter=encounter)
        self._write_meta(entry.path, meta)
        return self._entry(entry.path)

    def remove(self, entry: Entry) -> None:
        for suffix in (entry.path.suffix, ".json", ".mail"):
            entry.path.with_suffix(suffix).unlink(missing_ok=True)

    def export(self, entry: Entry, folder: str | Path) -> Path:
        """Copy the record (and its mail) out under a readable name."""
        target_dir = Path(folder).expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
        name = _UNSAFE.sub("", entry.nickname or entry.species)[:12] or "pokemon"
        target = target_dir / f"{entry.dex:03d}_{name}.{entry.extension}"
        n = 2
        while target.exists():
            target = target_dir / f"{entry.dex:03d}_{name}_{n}.{entry.extension}"
            n += 1
        shutil.copyfile(entry.path, target)
        if entry.has_mail:
            shutil.copyfile(entry.path.with_suffix(".mail"), target.with_suffix(".mail"))
        return target

    def convert(self, entry: Entry) -> tuple[Entry, bool]:
        """Through the Time Capsule: a .pk1 to the .pk2 Gen 2 would make of it, or back.

        A .pk2 must be a Gen 1 species knowing only Gen 1 moves; its held item and mail do not cross."""
        record = gbpk.Record.from_file(entry.data())
        if record.generation == 1:
            out = gbpk.gen1_to_gen2(record)
        else:
            if problems := gbpk.gen1_compatible(record):
                raise ValueError("Cannot go through the Time Capsule: " + "; ".join(problems))
            out = gbpk.gen2_to_gen1(record)
        return self.deposit(out.to_file(), out.extension, source="converted", origin=entry.id,
                            note=f"Time Capsule copy of {entry.id}")


# -- trades ---------------------------------------------------------------------------------------

def games_for(entry: Entry) -> list[str]:
    """The catalog keys whose trades take this record (Gen 1 and 2 both reach either VC game)."""
    from .catalog import ENTRIES

    keys = []
    for game in ENTRIES:
        if entry.extension not in game.extensions:
            continue
        if entry.extension == "pk6" and game.key in ("xy", "oras") and entry.dex > pkx.PK6.max_species:
            continue
        keys.append(game.key)
    return keys


# -- legality -------------------------------------------------------------------------------------

def pkhex_game(entry: Entry) -> str:
    """The PKHeX helper's game for a record: by format, and for Gen 6/7 by the game it came from."""
    if entry.extension in ("pk1", "pk2"):
        return "rby" if entry.extension == "pk1" else "gsc"
    version = pkx.summary(entry.data()).version
    if entry.extension == "pk6":
        return "oras" if version in (26, 27) else "xy"
    return "usum" if version in (32, 33) else "sm"


def check(bank: Bank, entry: Entry) -> Entry:
    """Ask PKHeX (services/pkhex) whether the record is legal, and keep the answer with it."""
    from .. import pkhex

    result = pkhex.shared().check(pkhex_game(entry), entry.data())
    return bank.set_legality(entry, result.legal, result.report, result.encounter)
