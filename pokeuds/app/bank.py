"""The bank: the Pokemon the app keeps between trades.

Each record is a file in the bank folder (``default_folder``, or Settings' bank folder); what the app knows about
it is a ``.json`` of the same name in the hidden ``.info`` folder inside, so the bank shows only Pokemon:

    20261104-153012_025_Sparky_1a2b3c4d.pk6          the record, as PKHeX writes it (decrypted, stored)
    20261104-153012_025_Sparky_1a2b3c4d.mail         a Gen 2 Pokemon's mail, when it holds one (beside its .pk2,
                                                     where the trade reads it)
    .info/20261104-153012_025_Sparky_1a2b3c4d.json   {"source", "created", "origin", "game", "note", "legal", ...}
    .info/received.json                              the received files already deposited

A bank from before ``.info`` (the ``.json`` beside each record) is tidied into it the first time it is opened.

Records are kept once: a Pokemon already banked is not added again, even when a trade changed what a
trade changes (``identity``): the same Abra traded to a 3DS and back carries the 3DS player as its handler,
their memories and a new checksum, and is still the same Abra. What a trade receives lands in the output
folder first (pokeuds.received) and is deposited from there.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import gbpk, pkx, species
from .settings import data_dir

EXTENSIONS = (".pk1", ".pk2", ".pk6", ".pk7")
IMPORTABLE = EXTENSIONS + (".ek6", ".ek7")
SOURCES = {"received": "Received", "imported": "Imported", "built": "Built", "converted": "Converted",
           "gift": "Mystery Gift", "migrated": "Migrated", "gts": "GTS"}
_UNSAFE = re.compile(r"[^\w\-]+", re.UNICODE)


PORTABLE = "Bank"           # the packaged Windows app's bank, beside PokeUDS.exe
PORTABLE_ON = ("win32",)    # a Mac app bundle is replaced whole by an update and is often read-only
INFO = ".info"              # inside a bank: the records' .json and received.json, hidden
SEEN = "received.json"
_portable: dict[Path, Path | None] = {}
_tidied: set[Path] = set()


_RECEIVED_LANGUAGE = re.compile(r"_(?:rby|gsc|timecapsule)-([a-z]{2})_trade\d+_")


def received_language(name: str) -> str:
    """The language of the Virtual Console title a received file came from ("de", "jp"...), which pokeuds.vc.trade
    keeps in the file's name; "" when the name carries none (older files, or PokeUDS hosted)."""
    found = _RECEIVED_LANGUAGE.search(name)
    return found.group(1) if found else ""


def language_of(path: str | Path) -> str:
    """The game language of a .pk1/.pk2 file: a Japanese record by its size, else what the bank keeps beside it
    (``.info``), else what a received file's name says; "" when nothing does. A record does not hold its own."""
    path = Path(path)
    try:
        if len(path.read_bytes()) in (gbpk.PK1_SIZE_JP, gbpk.PK2_SIZE_JP):
            return "jp"
    except OSError:
        return ""
    for meta in (path.parent / INFO / f"{path.stem}.json", path.with_suffix(".json")):
        try:
            language = json.loads(meta.read_text(encoding="utf-8")).get("language", "")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(language, str) and language:
            return language
    return received_language(path.name)


def default_folder() -> Path:
    """Where the bank lives when Settings names no folder.

    The packaged Windows app keeps it in ``Bank`` beside PokeUDS.exe: the release zip holds no such folder, so an
    update unzipped over the app's folder leaves it as it is. From source, in the Mac app (whose bundle an update
    replaces), with ``POKEUDS_DATA`` set, or when the exe's folder cannot be written (Program Files), it is
    ``<data>/bank`` (on a Mac, ``~/Library/Application Support/PokeUDS/bank``)."""
    if getattr(sys, "frozen", False) and sys.platform in PORTABLE_ON and not os.environ.get("POKEUDS_DATA"):
        if (folder := portable_folder(Path(sys.executable).resolve().parent)) is not None:
            return folder
    return data_dir() / "bank"


def portable_folder(app_folder: Path) -> Path | None:
    """``app_folder/Bank``, made the first time, with the records of the old ``<data>/bank`` copied into it once
    (the old folder is left as it was). None when the folder cannot be written."""
    if app_folder in _portable:
        return _portable[app_folder]
    folder = app_folder / PORTABLE
    try:
        fresh = not folder.exists()
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_bytes(b"")
        probe.unlink()
        legacy = data_dir() / "bank"
        if fresh and legacy.is_dir() and legacy.resolve() != folder.resolve():
            for item in legacy.iterdir():
                if item.is_file() and not (folder / item.name).exists():
                    shutil.copy2(item, folder / item.name)
                elif item.is_dir() and item.name == INFO:
                    shutil.copytree(item, folder / INFO, dirs_exist_ok=True)
    except OSError:
        folder = None
    _portable[app_folder] = folder
    return folder


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
    form: int = 0            # Gen 6/7: the form (Alolan, Unown's letter...), for its sprite

    @property
    def when(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.created))

    def describe(self) -> str:
        name = f"{self.species}" + (f" '{self.nickname}'" if self.nickname and self.nickname.upper() != self.species.upper() else "")
        level = f" Lv {self.level}" if self.level else ""
        return f"{name}{level}, OT {self.ot} {self.shown_tid()}"

    def shown_tid(self) -> str:
        """The trainer ID as the game shows it: five digits, six from Gen 7 on."""
        return f"{self.tid:06d}" if self.generation >= 7 else f"{self.tid:05d}"

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
    form: int = 0
    family: str = "intl"     # a Game Boy record's language family (pokeuds.vc.gbtext); "intl" for Gen 6/7


def parse(path: str | Path, raw: bytes | None = None, language: str = "") -> Parsed:
    """Read a .pk1/.pk2/.pk6/.pk7/.ek6/.ek7, or raise ValueError saying why it is not one. ``language`` is a Game
    Boy record's game language, which tells a Korean one (the size tells a Japanese one)."""
    path = Path(path)
    raw = Path(path).expanduser().read_bytes() if raw is None else raw
    ext = path.suffix.lower()
    if ext in (".pk1", ".pk2"):
        record = gbpk.Record.from_file(raw, "kr" if language == "kr" else None)
        if record.dex == 0:
            raise ValueError(f"{path.name}: not a real species (index {record.data[0]:#04x})")
        if f".{record.extension}" != ext:
            raise ValueError(f"{path.name}: the file holds a Generation {record.generation} Pokemon")
        shiny = record.generation == 2 and _gen2_shiny(record.data[21:23])   # Gen 1 has no shininess
        return Parsed(raw, ext[1:], record.generation, record.dex, record.nickname_text(), record.level,
                      shiny, record.ot_text(), record.ot_id, record.held_item, family=record.family)
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
                      s.tid if gen == 6 else _g7_tid(plain), s.held_item, form=plain[0x1D] >> 3)
    raise ValueError(f"{path.name}: not a Pokemon file ({', '.join(IMPORTABLE)})")


# What a trade writes into a record, blanked when telling whether two records are the same Pokemon.
# Gen 6/7 (the stored record): the checksum; the handling trainer's name, gender, the current-handler flag,
# the geolocations, the handler's friendship, affection and memory, Amie fullness and enjoyment
# (0x78-0xAF); the original trainer's friendship, affection and memory (0xCA-0xD0).
TRADE_FIELDS_PKX = ((0x06, 0x08), (0x78, 0xB0), (0xCA, 0xD1))
# Gen 2 (.pk2 file: 01 species FF, then the party struct): friendship, which a trade sets anew.
TRADE_FIELDS_PK2 = ((3 + 27, 3 + 28),)


def identity(parsed: Parsed) -> str:
    """A digest that is the same for a Pokemon before and after it went through trades: the record without
    what a trade writes into it (TRADE_FIELDS_*). Anything else, a level, a move, an evolution, still counts."""
    blank = {"pk6": TRADE_FIELDS_PKX, "pk7": TRADE_FIELDS_PKX, "pk2": TRADE_FIELDS_PK2}.get(parsed.extension, ())
    data = bytearray(parsed.content)
    for start, end in blank:
        data[start:end] = bytes(end - start)
    return hashlib.sha256(parsed.extension.encode() + bytes(data)).hexdigest()


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

def _hide(folder: Path) -> None:
    """Windows shows a folder whose name starts with a dot; mark it hidden as well."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(str(folder), 0x02)      # FILE_ATTRIBUTE_HIDDEN
        except (AttributeError, OSError):
            pass


class Bank:
    def __init__(self, folder: str | Path | None = None) -> None:
        self.folder = Path(folder).expanduser() if folder else default_folder()
        self.info = self.folder / INFO
        self._tidy()                # an older bank's .json files go into .info as soon as the app touches it

    def _info_dir(self) -> Path:
        if not self.info.is_dir():
            self.info.mkdir(parents=True, exist_ok=True)
            _hide(self.info)
        return self.info

    def _tidy(self) -> None:
        """Move an older bank's .json files (beside each record) and received.json into .info, once a run."""
        if not self.folder.is_dir():
            return
        key = self.folder.resolve()
        if key in _tidied:
            return
        _tidied.add(key)
        try:
            records = {p.stem for p in self.folder.iterdir() if p.suffix in EXTENSIONS}
            loose = [p for p in self.folder.glob("*.json") if p.stem in records or p.name == SEEN]
            for path in loose:
                target = self._info_dir() / path.name
                if target.exists():
                    path.unlink()           # .info already holds the newer copy
                else:
                    path.replace(target)
        except OSError:
            _tidied.discard(key)            # try again next time; the old files are still read meanwhile

    # listing

    def entries(self) -> list[Entry]:
        """Newest first."""
        if not self.folder.is_dir():
            return []
        self._tidy()
        found = [e for p in self.folder.iterdir() if p.suffix in EXTENSIONS and (e := self._entry(p))]
        return sorted(found, key=lambda e: (e.created, e.id), reverse=True)

    def find(self, entry_id: str) -> Entry | None:
        for ext in EXTENSIONS:
            path = self.folder / f"{entry_id}{ext}"
            if path.exists():
                return self._entry(path)
        return None

    def _entry(self, path: Path) -> Entry | None:
        meta = self._meta(path)
        try:
            parsed = parse(path, language=str(meta.get("language", "")))
        except (OSError, ValueError):
            return None
        return Entry(path.stem, path, parsed.extension, parsed.generation, parsed.dex, species.name(parsed.dex),
                     parsed.nickname, parsed.level, parsed.shiny, parsed.ot, parsed.tid, parsed.held_item,
                     meta.get("source", "imported"), float(meta.get("created", path.stat().st_mtime)),
                     meta.get("origin", ""), meta.get("note", ""), meta.get("legal"), meta.get("report", ""),
                     path.with_suffix(".mail").exists(), meta, form=parsed.form)

    def _meta_path(self, path: Path) -> Path:
        return self.info / f"{path.stem}.json"

    def _meta(self, path: Path) -> dict:
        # .info's copy, else the old place beside the record (a bank _tidy could not move yet)
        for candidate in (self._meta_path(path), path.with_suffix(".json")):
            try:
                meta = json.loads(candidate.read_text(encoding="utf-8"))
                return meta if isinstance(meta, dict) else {}
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                return {}
        return {}

    def _write_meta(self, path: Path, meta: dict) -> None:
        target = self._info_dir() / f"{path.stem}.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
        path.with_suffix(".json").unlink(missing_ok=True)       # an old copy beside the record would go stale

    def _digests(self) -> dict[str, str]:
        """identity -> entry id, for everything in the bank (the oldest entry, when there are duplicates)."""
        return {key: group[0].id for key, group in self._by_identity().items()}

    def _by_identity(self) -> dict[str, list[Entry]]:
        """identity -> the entries holding that Pokemon, oldest first."""
        groups: dict[str, list[Entry]] = {}
        for entry in reversed(self.entries()):
            try:
                key = identity(parse(entry.path))
            except (OSError, ValueError):
                continue
            groups.setdefault(key, []).append(entry)
        return groups

    def duplicates(self) -> list[tuple[Entry, Entry]]:
        """(duplicate, kept): entries holding a Pokemon an older entry already holds (banked before duplicates
        were caught), each with that oldest entry."""
        return [(e, group[0]) for group in self._by_identity().values() for e in group[1:]]

    def remove_duplicates(self) -> list[tuple[Entry, Entry]]:
        """Remove every duplicate ``duplicates`` lists, keeping the oldest of each Pokemon. -> what it removed."""
        pairs = self.duplicates()
        for duplicate, _ in pairs:
            self.remove(duplicate)
        return pairs

    # adding

    def deposit(self, content: bytes, extension: str, *, source: str, origin: str = "", mail: bytes | None = None,
                note: str = "", extra: dict | None = None, digests: dict | None = None) -> tuple[Entry, bool]:
        """Keep a record; returns (entry, added). A Pokemon already in the bank (``identity``) is not added again."""
        extra = dict(extra or {})
        parsed = parse(Path(f"x.{extension}"), content, extra.get("language", ""))
        if parsed.family == "jp" and not extra.get("language"):
            extra["language"] = "jp"            # a Japanese record says so by its size
        digest = hashlib.sha256(parsed.content).hexdigest()
        key = identity(parsed)
        digests = self._digests() if digests is None else digests
        if key in digests and (existing := self.find(digests[key])):
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
        digests[key] = entry_id
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
                language = received_language(path.name)
                entry, new = self.deposit(parsed.content, parsed.extension, source="received", origin=str(path),
                                          mail=mail.read_bytes() if mail.exists() else None, digests=digests,
                                          extra={"language": language} if language else None)
            except (OSError, ValueError):
                continue
            seen.add(path.name)
            if new:
                added.append(entry)
        self._write_seen(root, seen)
        return added

    def _seen_path(self) -> Path:
        self._tidy()
        return self.info / SEEN

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
        self._info_dir()
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
        self._meta_path(entry.path).unlink(missing_ok=True)

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


@dataclass
class Checked:
    legal: list[Entry] = field(default_factory=list)
    illegal: list[Entry] = field(default_factory=list)
    failed: list[tuple[Entry, str]] = field(default_factory=list)


def check_all(bank: Bank, entries: list[Entry] | None = None, *, progress=None, stop=None) -> Checked:
    """``check`` every record (or ``entries``), one after another. ``progress(done, total, entry)`` follows it;
    ``stop()`` returning true ends it early. A record PKHeX cannot check is kept in ``failed`` with the reason."""
    todo = bank.entries() if entries is None else entries
    out = Checked()
    for n, entry in enumerate(todo):
        if stop and stop():
            break
        if progress:
            progress(n, len(todo), entry)
        try:
            checked = check(bank, entry)
        except (OSError, ValueError, RuntimeError) as exc:      # a record it refuses, or the helper gone
            out.failed.append((entry, str(exc)))
            if not isinstance(exc, ValueError):
                break
            continue
        (out.legal if checked.legal else out.illegal).append(checked)
    if progress:
        progress(len(todo), len(todo), None)
    return out


# -- migrating to a later generation ----------------------------------------------------------------

# Format -> the generation Nintendo's apps move it to, one way: Poke Transporter takes Gen 1/2 Virtual Console
# Pokemon to Gen 7, Pokemon Bank takes Gen 6 to Gen 7 (services/pkhex/Migrate.cs). Nothing official reaches Gen 6
# from the Game Boy, and nothing comes back.
MIGRATIONS = {"pk1": 7, "pk2": 7, "pk6": 7}
MIGRATION_APPS = {"pk1": "Poke Transporter", "pk2": "Poke Transporter", "pk6": "Pokemon Bank"}
VC_GAMES = {1: (("red", "Red"), ("blue", "Blue"), ("yellow", "Yellow")),
            2: (("gold", "Gold"), ("silver", "Silver"), ("crystal", "Crystal"))}


def migration_target(entry: Entry) -> int | None:
    """The generation the record can be migrated to, or None."""
    return MIGRATIONS.get(entry.extension)


def migrates_to(entry: Entry) -> list[str]:
    """The catalog keys the record reaches once migrated (not those it already reaches as it is)."""
    from .catalog import ENTRIES

    target = migration_target(entry)
    if target is None:
        return []
    return [g.key for g in ENTRIES if f"pk{target}" in g.extensions and g.key not in games_for(entry)]


def migrated_copy(bank: Bank, entry: Entry) -> Entry | None:
    """The migrated copy made of this record before, while it is still in the bank."""
    copy_id = (entry.meta.get("migrated") or {}).get(str(migration_target(entry)))
    return bank.find(copy_id) if copy_id else None


def migrate(bank: Bank, entry: Entry, *, vc_game: str | None = None, trainer: dict | None = None,
            helper=None) -> tuple[Entry, bool]:
    """Move a copy of the record to Gen 7 as Transporter (Gen 1/2) or Bank (Gen 6) would, and keep it in the bank
    beside the original, which stays as it was. A record migrated before gives the copy made then: the apps move
    a Pokemon once, and a second copy would have other IVs and another PID. -> (copy, made now)"""
    if (copy := migrated_copy(bank, entry)) is not None:
        return copy, False
    target = migration_target(entry)
    if target is None:
        raise ValueError(f"a .{entry.extension} does not migrate anywhere")
    if entry.extension in ("pk1", "pk2") and vc_game and vc_game not in dict(VC_GAMES[entry.generation]):
        raise ValueError(f"{vc_game} is not a Generation {entry.generation} game")
    if helper is None:
        from .. import pkhex

        helper = pkhex.shared()
    # Transporter keeps the language of the game a Gen 1/2 Pokemon came from: hand it over when the bank knows it.
    language = entry.meta.get("language", "") if entry.extension in ("pk1", "pk2") else ""
    built = helper.migrate("sm", entry.data(), pkhex_game(entry), vc_game=vc_game, trainer=trainer,
                           **({"language": language} if language else {}))
    app = MIGRATION_APPS[entry.extension]
    copy, _ = bank.deposit(built.data, f"pk{target}", source="migrated", origin=entry.id,
                           note=f"{app} copy of {entry.describe()}",
                           extra={"legal": built.legal, "report": built.report, "encounter": built.encounter,
                                  "migrated_from": entry.id, "vc_game": vc_game or ""})
    meta = bank._meta(entry.path)
    meta.setdefault("migrated", {})[str(target)] = copy.id
    bank._write_meta(entry.path, meta)
    return copy, True
