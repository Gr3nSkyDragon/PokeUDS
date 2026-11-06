"""Small helpers shared by every layer."""

from __future__ import annotations

import os
import random
import subprocess
import sys

BROADCAST = b"\xff" * 6

# Every program PokeUDS starts is a console program (the PKHeX helper, dotnet, the CLI behind the app). Started
# from the app, which has no console of its own, Windows would open a console window for each, flashing on screen
# for an instant; every launch passes this, so none ever shows. tests/test_util.py holds every launch to it.
NO_WINDOW: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def mac_str(mac: bytes) -> str:
    return ":".join(f"{b:02X}" for b in mac)


def parse_mac(text: str) -> bytes:
    parts = text.replace("-", ":").split(":")
    if len(parts) != 6:
        raise ValueError(f"not a MAC address: {text!r}")
    return bytes(int(p, 16) for p in parts)


def random_mac() -> bytes:
    """A locally administered unicast address (bit 1 set, bit 0 clear in the first octet)."""
    first = (random.randrange(256) & 0xFC) | 0x02
    return bytes([first]) + os.urandom(5)


def hexdump(data: bytes, sep: str = "") -> str:
    return data.hex(sep) if sep else data.hex()


def parse_hex(text: str) -> bytes:
    """Hex in any of the forms the Azahar logs and 3DS tools print: ``AA:BB``, ``aa bb``, ``aabb``."""
    cleaned = text.strip()
    if cleaned in ("", "<none>"):
        return b""
    for ch in ":- \t":
        cleaned = cleaned.replace(ch, "")
    return bytes.fromhex(cleaned)
