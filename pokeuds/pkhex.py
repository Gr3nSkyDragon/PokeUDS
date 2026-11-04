"""The PKHeX helper (``services/pkhex``): legal Pokemon built and checked by PKHeX.Core.

The helper is a small .NET program; it is built once with

    dotnet build -c Release services/pkhex

and runs as one long-lived child process, one JSON request per line (docs/pkhex.md). ``POKEUDS_PKHEX``
points at a helper elsewhere (a published single file, say).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "services" / "pkhex"
GAMES = ("rby", "gsc", "xy", "oras", "sm", "usum")
EXE = "pokeuds-pkhex.exe" if sys.platform == "win32" else "pokeuds-pkhex"


class HelperMissing(RuntimeError):
    pass


def helper_path() -> Path | None:
    if override := os.environ.get("POKEUDS_PKHEX"):
        return Path(override) if Path(override).exists() else None
    for candidate in (PROJECT / "bin" / "Release" / "net10.0" / EXE, PROJECT / "publish" / EXE):
        if candidate.exists():
            return candidate
    return None


def build_helper() -> subprocess.CompletedProcess:
    """``dotnet build`` the helper (needs the .NET 10 SDK)."""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise HelperMissing("the .NET 10 SDK is not installed (dotnet not found)")
    return subprocess.run([dotnet, "build", "-c", "Release", "-nologo", str(PROJECT)], capture_output=True, text=True)


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
