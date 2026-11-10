"""PKHeX (``services/pkhex``): legal Pokemon built and checked by PKHeX.Core.

PKHeX.Core runs in a small .NET program, one long-lived child process taking one JSON request per line
(docs/pkhex.md), as pokeldn runs its own. Players never see it: it is part of the app, and nothing is downloaded.

- **The packaged app** carries it inside (``bundled_path``; scripts/build_exe.py publishes it there), self-contained,
  so nothing is installed. On macOS, should the copy in the app bundle have lost its executable bit, it is copied to
  ``<data>/pkhex`` and made executable there (``repair_path``), with no network.
- **From source**, ``dotnet build -c Release services/pkhex`` (the .NET 10 SDK) builds it into ``services/pkhex/bin``;
  ``python scripts/publish_pkhex.py`` makes the self-contained file in ``services/pkhex/dist`` instead. The newer of
  the two is used.

``POKEUDS_PKHEX`` points at a helper elsewhere and wins over all of these.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .util import NO_WINDOW

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "services" / "pkhex"
GAMES = ("rby", "gsc", "xy", "oras", "sm", "usum")
EXE = "pokeuds-pkhex.exe" if sys.platform == "win32" else "pokeuds-pkhex"
PKHEX_VERSION = "26.10.18"
BUILD_COMMAND = "dotnet build -c Release services/pkhex"


class HelperMissing(RuntimeError):
    pass


def runtime_id() -> str:
    """This computer as .NET names it: win-x64, linux-arm64, osx-arm64..."""
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    system = {"win32": "win", "darwin": "osx"}.get(sys.platform, "linux")
    return f"{system}-{arch}"


def bundled_path() -> Path:
    """The self-contained helper a packaged PokeUDS carries inside itself (scripts/build_exe.py puts it here, the way
    the ESP32 firmware rides in ``pokeuds/app/data``). Present in a packaged build, absent when running from source."""
    return ROOT / "pokeuds" / "app" / "data" / "pkhex" / EXE


def repair_path() -> Path:
    """Where a bundled helper that cannot run in place (no executable bit) is copied and made runnable."""
    from .app.settings import data_dir

    return data_dir() / "pkhex" / EXE


def candidates() -> list[Path]:
    """A source tree's helpers, newest first wins: the self-contained publish and a ``dotnet build``."""
    return [PROJECT / "dist" / EXE, PROJECT / "bin" / "Release" / "net10.0" / EXE]


def _runnable(path: Path) -> bool:
    return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))


def _repaired(bundled: Path) -> Path | None:
    """A copy of ``bundled`` that can run: made once (again when the app's copy changes), None when it cannot be."""
    target = repair_path()
    try:
        if not _runnable(target) or target.stat().st_size != bundled.stat().st_size \
                or target.stat().st_mtime < bundled.stat().st_mtime:
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".part")
            shutil.copyfile(bundled, part)
            part.chmod(part.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            part.replace(target)
        return target if _runnable(target) else None
    except OSError:
        return None


def helper_path() -> Path | None:
    if override := os.environ.get("POKEUDS_PKHEX"):
        return Path(override) if Path(override).exists() else None
    bundled = bundled_path()
    if getattr(sys, "frozen", False) and bundled.is_file():     # the packaged app: always its own copy
        return bundled if _runnable(bundled) else _repaired(bundled)
    # From source the newest wins: a copy scripts/build_exe.py left in pokeuds/app/data must not hide a later build.
    found = [p for p in (bundled, *candidates()) if _runnable(p)]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


_sdks: list[str] | None = None


def dotnet_sdks(again: bool = False) -> list[str]:
    """The .NET SDK versions installed, or [] when there is no dotnet. Asked once per run (``again`` asks anew):
    the pages that show the helper's state are drawn often, and each ask starts dotnet."""
    global _sdks
    if _sdks is not None and not again:
        return list(_sdks)
    dotnet = shutil.which("dotnet")
    found: list[str] = []
    if dotnet:
        try:
            out = subprocess.run([dotnet, "--list-sdks"], capture_output=True, text=True, timeout=30, **NO_WINDOW).stdout
            found = [line.split()[0] for line in out.splitlines() if line.strip()]
        except (OSError, subprocess.SubprocessError):
            pass
    _sdks = found
    return list(found)


def has_source() -> bool:
    """The helper's C# project is here to build (it ships with the source tree, not with the packaged app)."""
    return (PROJECT / "pokeuds-pkhex.csproj").is_file()


def can_build() -> bool:
    return has_source() and any(v.split(".")[0].isdigit() and int(v.split(".")[0]) >= 10 for v in dotnet_sdks())


def status() -> str:
    """What PokeUDS says about PKHeX, in a sentence (the command line's ``pkhex status``; the app shows it only when
    PKHeX is missing, which a packaged app never is)."""
    if path := helper_path():
        how = ("part of the app" if getattr(sys, "frozen", False) and path in (bundled_path(), repair_path())
               else "left by scripts/build_exe.py" if path == bundled_path() else "published" if path.parent.name == "dist"
               else "built from source" if "bin" in path.parts else "set by POKEUDS_PKHEX")
        return f"PKHeX is ready ({how}: {path})."
    if not has_source():
        return ("PKHeX is missing: this copy of PokeUDS was built without it. Download the app again from PokeUDS's "
                "releases.")
    sdks = dotnet_sdks()
    if sdks and not can_build():
        return (f"PKHeX is missing. Running from source, it needs building with the .NET 10 SDK (this computer has "
                f"{', '.join(sdks)}): {BUILD_COMMAND}")
    return f"PKHeX is missing. Running from source, build it once with the .NET 10 SDK: {BUILD_COMMAND}"


def build_helper() -> subprocess.CompletedProcess:
    """``dotnet build`` the helper (needs the .NET 10 SDK and the C# source tree)."""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise HelperMissing("the .NET SDK is not installed (dotnet not found): install the .NET 10 SDK to build PKHeX")
    if not has_source():
        raise HelperMissing(f"PKHeX's C# source is not here to build ({PROJECT / 'pokeuds-pkhex.csproj'} is missing); "
                            "a packaged PokeUDS carries PKHeX already, so download the app again instead")
    if not can_build():
        raise HelperMissing(f"the helper needs the .NET 10 SDK; this computer has {', '.join(dotnet_sdks()) or 'none'}")
    return subprocess.run([dotnet, "build", "-c", "Release", "-nologo", str(PROJECT)], capture_output=True, text=True,
                          **NO_WINDOW)


def publish(rid: str, out: Path) -> Path:
    """Publish the helper as one self-contained file at ``out`` (nothing to install where it runs). Needs the .NET 10
    SDK and the C# source; used by scripts/publish_pkhex.py and scripts/build_exe.py (which bundles it)."""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise HelperMissing("dotnet not found: install the .NET 10 SDK to publish the helper")
    if not has_source():
        raise HelperMissing(f"the helper's C# source is not here to publish ({PROJECT / 'pokeuds-pkhex.csproj'} is missing)")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as stage:
        subprocess.run([dotnet, "publish", str(PROJECT), "-c", "Release", "-r", rid, "-o", stage, "-nologo"],
                       check=True, **NO_WINDOW)
        name = "pokeuds-pkhex.exe" if rid.startswith("win") else "pokeuds-pkhex"
        shutil.copy2(Path(stage) / name, out)
    return out


def build_problem(done: subprocess.CompletedProcess) -> str:
    """The lines of a failed build worth showing (the errors, else the end)."""
    lines = (done.stdout or "").splitlines() + (done.stderr or "").splitlines()
    errors = [line.strip() for line in lines if re.search(r"\berror\b", line)]
    return "\n".join(dict.fromkeys(errors).keys() if errors else lines[-6:])[:1200]


@dataclass
class Built:
    legal: bool
    report: str
    encounter: str
    extension: str
    data: bytes
    summary: dict


@dataclass
class Gift:
    card: dict                # card_id, title, kind, games, species_name, level, shiny, ot, moves, ...
    card_data: bytes          # the 264-byte card (.wc6 / .wc7)
    full_data: bytes          # the 784-byte distribution file (.wc6full / .wc7full)
    extension: str            # "wc6" or "wc7"
    pokemon: Built | None     # None for an item, BP or beans card
    custom: bool


class Helper:
    def __init__(self, path: Path | None = None) -> None:
        path = path or helper_path()
        if path is None:
            raise HelperMissing(status())
        self.proc = subprocess.Popen([str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     **NO_WINDOW,
                                     text=True, encoding="utf-8", bufsize=1)
        self._lock = threading.Lock()
        self._next = 0

    def request(self, cmd: str, **fields) -> dict:
        with self._lock:
            if self.proc.poll() is not None:
                raise RuntimeError("PKHeX has stopped")
            self._next += 1
            self.proc.stdin.write(json.dumps({"cmd": cmd, "id": self._next, **fields}) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("PKHeX stopped answering")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise ValueError(reply.get("error", "PKHeX refused the request"))
        return reply

    def ping(self) -> str:
        return self.request("ping")["pkhex"]

    def lists(self, game: str) -> dict:
        return self.request("lists", game=game)

    def paste(self, game: str, text: str) -> list[dict]:
        """Each Showdown set in ``text`` as this game's wishes (builder.Pasted.from_reply), with its errors and notes."""
        return self.request("paste", game=game, text=text).get("sets", [])

    def build(self, game: str, species: int, **wish) -> Built:
        return self._built(self.request("build", game=game, species=species, **{k: v for k, v in wish.items() if v is not None}))

    def check(self, game: str, data: bytes) -> Built:
        return self._built(self.request("check", game=game, data=base64.b64encode(data).decode()))

    def migrate(self, game: str, data: bytes, source: str, *, vc_game: str | None = None,
                trainer: dict | None = None, language: str = "") -> Built:
        """A Gen 1/2/6 record (``source``: its helper game, rby/gsc/xy/oras) moved to Gen 7 (``game``: sm/usum) as
        Poke Transporter or Pokemon Bank does it (services/pkhex/Migrate.cs). ``vc_game`` is the Virtual Console game
        a Gen 1/2 Pokemon is transferred from (red, blue, yellow, gold, silver, crystal); ``trainer`` runs the app."""
        fields = {"from": source, "data": base64.b64encode(data).decode(), "trainer": trainer or {}}
        if vc_game:
            fields["source"] = vc_game
        if language:                # of the VC title a Gen 1/2 record came from ("de", "jp"...): Transporter keeps it
            fields["language"] = language
        return self._built(self.request("migrate", game=game, **fields))

    # Mystery Gift cards, Gen 6 and 7 (docs/mystery_gift.md).

    def gifts(self, game: str, all: bool = False) -> list[dict]:
        """The official Pokemon cards PKHeX knows that reach the game's versions, newest card id first, one per
        card, species and form; ``all`` lists every card (items and revisions too) with its bytes (``card_data``)."""
        return self.request("gifts", game=game, all=all)["gifts"]

    def gift(self, game: str, *, key: str | None = None, data: bytes | None = None, trainer: dict | None = None) -> "Gift":
        """An official card (by key) or a .wc6/.wc7/full file's bytes, made into its Pokemon for the trainer."""
        fields = {"key": key} if key else {"data": base64.b64encode(data or b"").decode()}
        return self._gift(self.request("gift", game=game, trainer=trainer or {}, **fields))

    def card(self, game: str, trainer: dict | None = None, **fields) -> "Gift":
        """A custom card from the form's fields (services/pkhex/Gifts.cs), and the Pokemon it gives."""
        return self._gift(self.request("card", game=game, trainer=trainer or {},
                                       **{k: v for k, v in fields.items() if v is not None}))

    def card_fields(self, game: str, *, key: str | None = None, data: bytes | None = None) -> dict:
        """Any card (an official key, or a .wc6/.wc7/full file's bytes) as the editor's fields, which ``card`` takes
        back unchanged: the schema is in services/pkhex/CardEditor.cs and docs/wonder_cards.md."""
        fields = {"key": key} if key else {"data": base64.b64encode(data or b"").decode()}
        return self.request("card_fields", game=game, **fields)["fields"]

    def _gift(self, reply: dict) -> "Gift":
        pokemon = self._built({**reply["pokemon"]}) if reply.get("pokemon") else None
        return Gift(reply["card"], base64.b64decode(reply.get("card_data", "")), base64.b64decode(reply.get("full_data", "")),
                    reply.get("card_extension", ""), pokemon, bool(reply.get("custom")))

    @staticmethod
    def _built(reply: dict) -> Built:
        return Built(reply["legal"], reply["report"], reply.get("encounter") or "", reply["extension"],
                     base64.b64decode(reply["data"]), reply["summary"])

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


_shared: Helper | None = None
_shared_lock = threading.Lock()


def shared() -> Helper:
    """One helper for the process, started on first use."""
    global _shared
    with _shared_lock:
        if _shared is None or _shared.proc.poll() is not None:
            _shared = Helper()
        return _shared


def game_for(path: str | Path) -> str:
    """The helper's game for a file, by its format (a .pk6 is X/Y, a .pk7 Sun/Moon; good enough for checks)."""
    return {".pk1": "rby", ".pk2": "gsc", ".pk6": "xy", ".ek6": "xy", ".pk7": "sm", ".ek7": "sm"}[Path(path).suffix.lower()]
