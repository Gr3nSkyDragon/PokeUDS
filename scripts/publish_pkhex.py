"""Publish the PKHeX helper as self-contained single files: nothing to install where it runs (docs/pkhex.md).

    python scripts/publish_pkhex.py                     # this computer: services/pkhex/dist/pokeuds-pkhex[.exe]
    python scripts/publish_pkhex.py --rid win-x64 --rid linux-x64 --out release/   # release assets + SHA256SUMS

Needs the .NET 10 SDK on the machine that publishes. A release asset is named pokeuds-pkhex-<rid>[.exe]; macOS
ones must be published on a Mac, where the SDK signs them (Apple Silicon refuses an unsigned program).
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pokeuds import pkhex  # noqa: E402


def publish(rid: str, out: Path) -> Path:
    dotnet = shutil.which("dotnet") or sys.exit("dotnet not found: install the .NET 10 SDK")
    with tempfile.TemporaryDirectory() as stage:
        subprocess.run([dotnet, "publish", str(pkhex.PROJECT), "-c", "Release", "-r", rid, "-o", stage, "-nologo"],
                       check=True)
        name = "pokeuds-pkhex.exe" if rid.startswith("win") else "pokeuds-pkhex"
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(stage) / name, out)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rid", action="append", help="a .NET runtime id (win-x64, linux-x64, linux-arm64, osx-arm64...)")
    parser.add_argument("--out", help="a folder for release assets (pokeuds-pkhex-<rid>) and their SHA256SUMS")
    args = parser.parse_args()
    if not args.out:
        rid = (args.rid or [pkhex.runtime_id()])[0]
        path = publish(rid, pkhex.PROJECT / "dist" / pkhex.EXE)
        print(f"{path} ({path.stat().st_size >> 20} MB): the app uses it now")
        return 0
    folder = Path(args.out)
    sums = folder / "SHA256SUMS"
    lines = dict(line.split()[::-1] for line in sums.read_text().splitlines() if line.strip()) if sums.exists() else {}
    for rid in args.rid or [pkhex.runtime_id()]:
        path = publish(rid, folder / pkhex.asset_name(rid))
        lines[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"{path} ({path.stat().st_size >> 20} MB)")
    sums.write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(lines.items())))
    print(f"{sums}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
