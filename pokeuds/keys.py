"""Console keys from an ``aes_keys.txt`` file (the format Citra and Azahar read).

UDS needs one console key: the normal key of AES keyslot 0x2D, which turns a game's passphrase
into the network's CCMP key (``pokeuds.uds.crypto.ccmp_key``). The file may give it directly as
``slot0x2DKeyN``, or as ``slot0x2DKeyX`` + ``slot0x2DKeyY`` together with ``generatorConstant``,
from which the normal key is derived the way the 3DS key scrambler does:

    NormalKey = ((KeyX ROL 2) XOR KeyY) + C) ROL 87        (128-bit arithmetic)

Keys are console secrets. They are read from the user's own file and never stored by this project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

UDS_KEYSLOT = 0x2D
MASK128 = (1 << 128) - 1


def _rol128(value: int, shift: int) -> int:
    shift %= 128
    return ((value << shift) | (value >> (128 - shift))) & MASK128


def scramble(key_x: bytes, key_y: bytes, constant: bytes) -> bytes:
    x = int.from_bytes(key_x, "big")
    y = int.from_bytes(key_y, "big")
    c = int.from_bytes(constant, "big")
    normal = _rol128((((_rol128(x, 2) ^ y) + c) & MASK128), 87)
    return normal.to_bytes(16, "big")


@dataclass
class KeyFile:
    entries: dict[str, bytes] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "KeyFile":
        return cls.parse(Path(path).expanduser().read_text(encoding="utf-8", errors="replace"))

    @classmethod
    def parse(cls, text: str) -> "KeyFile":
        entries: dict[str, bytes] = {}
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or "=" not in line:
                continue
            name, value = (part.strip() for part in line.split("=", 1))
            try:
                key = bytes.fromhex(value)
            except ValueError:
                continue
            if len(key) >= 16:
                entries[name.lower()] = key[:16]
        return cls(entries)

    def get(self, name: str) -> bytes | None:
        return self.entries.get(name.lower())

    def normal_key(self, slot: int) -> bytes | None:
        prefix = f"slot0x{slot:02x}key"
        normal = self.get(prefix + "n")
        if normal:
            return normal
        key_x, key_y = self.get(prefix + "x"), self.get(prefix + "y")
        constant = self.get("generatorconstant")
        if key_x and key_y and constant:
            return scramble(key_x, key_y, constant)
        return None

    def uds_key(self) -> bytes:
        key = self.normal_key(UDS_KEYSLOT)
        if key is None:
            raise KeyError(
                "aes_keys.txt has no keyslot 0x2D key: add slot0x2DKeyN, or slot0x2DKeyX, "
                "slot0x2DKeyY and generatorConstant")
        return key
