"""Writing the Pokemon a console sends into the output folder.

Like pokeldn, every received record is written as soon as the trade delivers it, decrypted, in
the stored (box) format PKHeX opens. One run can trade several times; each trade gets its own
file, named so that a folder sorts in trade order:

    <YYYYmmdd-HHMMSS>_<game>_trade<N>_<species>_<nickname>_<PID>.<pk6|pk7>
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import pkx

_UNSAFE = re.compile(r"[^\w\-]+", re.UNICODE)


def file_name(record: bytes, game: str, trade: int, stamp: str | None = None, fmt: pkx.Format = pkx.PK6) -> str:
    s = pkx.summary(record)
    nickname = _UNSAFE.sub("", s.nickname)[:12] or "unnamed"
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    return f"{stamp}_{game}_trade{trade}_{s.species:03d}_{nickname}_{s.pid:08X}.{fmt.extension}"


def save(record: bytes, folder: str | Path, *, game: str, trade: int = 1, stamp: str | None = None,
         fmt: pkx.Format = pkx.PK6) -> Path:
    """Write ``record`` (encrypted or decrypted, stored or party) and return the file's path."""
    plain = record if pkx.checksum_valid(record) else pkx.decrypt(record)
    if not pkx.checksum_valid(plain):
        raise ValueError("the received record fails its checksum")
    target_dir = Path(folder).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / file_name(plain, game, trade, stamp, fmt)
    target.write_bytes(pkx.to_stored(plain))
    return target
