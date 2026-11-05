"""The PKHeX helper (``services/pkhex``): legal Pokemon built and checked by PKHeX.Core.

The helper is a small .NET program that runs as one long-lived child process, one JSON request per line
(docs/pkhex.md). There are three ways to have it, and the newest one found is used:

- **downloaded**: the self-contained executable a PokeUDS release carries for this computer (``download_helper``;
  the app's Get the PKHeX helper), in ``<data>/pkhex/``. Needs nothing installed.
- **published**: ``python scripts/publish_pkhex.py`` makes the same self-contained file in ``services/pkhex/dist``
  (needs the .NET 10 SDK once, on the machine that makes it).
- **built**: ``dotnet build -c Release services/pkhex``, for development (needs the .NET 10 SDK, and its runtime
  to run).

``POKEUDS_PKHEX`` points at a helper elsewhere and wins over all three.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "services" / "pkhex"
GAMES = ("rby", "gsc", "xy", "oras", "sm", "usum")
EXE = "pokeuds-pkhex.exe" if sys.platform == "win32" else "pokeuds-pkhex"
PKHEX_VERSION = "26.10.18"
RELEASES = os.environ.get("POKEUDS_RELEASES", "https://api.github.com/repos/Gr3nSkyDragon/PokeUDS/releases")
HELPER_BYTES = 80 << 20


class HelperMissing(RuntimeError):
    pass


def runtime_id() -> str:
    """This computer as .NET names it: win-x64, linux-arm64, osx-arm64..."""
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    system = {"win32": "win", "darwin": "osx"}.get(sys.platform, "linux")
    return f"{system}-{arch}"


def asset_name(rid: str | None = None) -> str:
    rid = rid or runtime_id()
    return f"pokeuds-pkhex-{rid}" + (".exe" if rid.startswith("win") else "")


def downloaded_path() -> Path:
    from .app.settings import data_dir

    return data_dir() / "pkhex" / EXE


def candidates() -> list[Path]:
    return [downloaded_path(), PROJECT / "dist" / EXE, PROJECT / "bin" / "Release" / "net10.0" / EXE]


def helper_path() -> Path | None:
    if override := os.environ.get("POKEUDS_PKHEX"):
        return Path(override) if Path(override).exists() else None
    found = [p for p in candidates() if p.is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def dotnet_sdks() -> list[str]:
    """The .NET SDK versions installed, or [] when there is no dotnet."""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        return []
    try:
        out = subprocess.run([dotnet, "--list-sdks"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split()[0] for line in out.splitlines() if line.strip()]


def can_build() -> bool:
    return any(v.split(".")[0].isdigit() and int(v.split(".")[0]) >= 10 for v in dotnet_sdks())


def status() -> str:
    """What the app says about the helper, in a sentence."""
    if path := helper_path():
        how = ("downloaded" if path == downloaded_path() else "published" if path.parent.name == "dist"
               else "built from source" if "bin" in path.parts else "set by POKEUDS_PKHEX")
        return f"The PKHeX helper is ready ({how}: {path})."
    sdks = dotnet_sdks()
    if not sdks:
        return ("The PKHeX helper is not here yet. Get it (a download, nothing to install), or build it with the "
                ".NET 10 SDK.")
    if not can_build():
        return (f"The PKHeX helper is not here yet. The .NET SDK installed ({', '.join(sdks)}) is older than the "
                ".NET 10 the helper needs: get the helper as a download instead, or install the .NET 10 SDK.")
    return "The PKHeX helper is not here yet. Get it as a download, or build it with the .NET 10 SDK you have."


def build_helper() -> subprocess.CompletedProcess:
    """``dotnet build`` the helper (needs the .NET 10 SDK)."""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise HelperMissing("the .NET SDK is not installed (dotnet not found); get the helper as a download instead")
    if not can_build():
        raise HelperMissing(f"the helper needs the .NET 10 SDK; this computer has {', '.join(dotnet_sdks()) or 'none'}")
    return subprocess.run([dotnet, "build", "-c", "Release", "-nologo", str(PROJECT)], capture_output=True, text=True)


def build_problem(done: subprocess.CompletedProcess) -> str:
    """The lines of a failed build worth showing (the errors, else the end)."""
    lines = (done.stdout or "").splitlines() + (done.stderr or "").splitlines()
    errors = [line.strip() for line in lines if re.search(r"\berror\b", line)]
    return "\n".join(dict.fromkeys(errors).keys() if errors else lines[-6:])[:1200]


def _get(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "PokeUDS", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=60) as reply:       # noqa: S310 - a fixed https address
        data = reply.read(limit + 1)
    if len(data) > limit:
        raise OSError(f"{url} is larger than expected")
    return data


def download_helper(say=print) -> Path:
    """The newest PokeUDS release's helper for this computer, checked against the release's SHA256SUMS, into
    ``<data>/pkhex``. OSError says why not (no release yet, none for this computer, a hash mismatch)."""
    name = asset_name()
    releases = json.loads(_get(RELEASES, 4 << 20))
    if not isinstance(releases, list):
        raise OSError("the release listing holds no releases")
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        files = {a.get("name"): a.get("browser_download_url", "") for a in release.get("assets", []) if isinstance(a, dict)}
        if name not in files or "SHA256SUMS" not in files:
            continue
        sums = {}
        for line in _get(files["SHA256SUMS"], 100_000).decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) == 2:
                sums[parts[1].lstrip("*")] = parts[0].lower()
        say(f"Downloading {name} from {release.get('tag_name', '?')}")
        data = _get(files[name], HELPER_BYTES)
        if hashlib.sha256(data).hexdigest() != sums.get(name):
            raise OSError(f"{name} does not match the release's SHA256SUMS; nothing was written")
        target = downloaded_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        part.write_bytes(data)
        part.chmod(part.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        part.replace(target)
        check = subprocess.run([str(target)], input='{"cmd":"ping"}\n', capture_output=True, text=True, timeout=60)
        if '"ok":true' not in check.stdout:
            raise OSError(f"the downloaded helper does not start: {(check.stderr or check.stdout).strip()[:300]}")
        say(f"Saved {target}")
        return target
    raise OSError(f"no PokeUDS release carries a helper for this computer ({name}) yet: build it with the .NET 10 SDK "
                  "(dotnet build -c Release services/pkhex) or python scripts/publish_pkhex.py")


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
            raise HelperMissing("the PKHeX helper is not built: run `dotnet build -c Release services/pkhex`")
        self.proc = subprocess.Popen([str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", bufsize=1)
        self._lock = threading.Lock()
        self._next = 0

    def request(self, cmd: str, **fields) -> dict:
        with self._lock:
            if self.proc.poll() is not None:
                raise RuntimeError("the PKHeX helper has exited")
            self._next += 1
            self.proc.stdin.write(json.dumps({"cmd": cmd, "id": self._next, **fields}) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("the PKHeX helper closed its output")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise ValueError(reply.get("error", "the PKHeX helper refused the request"))
        return reply

    def ping(self) -> str:
        return self.request("ping")["pkhex"]

    def lists(self, game: str) -> dict:
        return self.request("lists", game=game)

    def build(self, game: str, species: int, **wish) -> Built:
        return self._built(self.request("build", game=game, species=species, **{k: v for k, v in wish.items() if v is not None}))

    def check(self, game: str, data: bytes) -> Built:
        return self._built(self.request("check", game=game, data=base64.b64encode(data).decode()))

    def migrate(self, game: str, data: bytes, source: str, *, vc_game: str | None = None,
                trainer: dict | None = None) -> Built:
        """A Gen 1/2/6 record (``source``: its helper game, rby/gsc/xy/oras) moved to Gen 7 (``game``: sm/usum) as
        Poke Transporter or Pokemon Bank does it (services/pkhex/Migrate.cs). ``vc_game`` is the Virtual Console game
        a Gen 1/2 Pokemon is transferred from (red, blue, yellow, gold, silver, crystal); ``trainer`` runs the app."""
        fields = {"from": source, "data": base64.b64encode(data).decode(), "trainer": trainer or {}}
        if vc_game:
            fields["source"] = vc_game
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
