"""Publish PokeUDS's PKHeX program as one self-contained file for this computer (docs/pkhex.md).

    python scripts/publish_pkhex.py                 # services/pkhex/dist/pokeuds-pkhex[.exe]: the app uses it now
    python scripts/publish_pkhex.py --rid osx-x64   # another computer's (a macOS one must be published on a Mac)

Needs the .NET 10 SDK. Running from source, `dotnet build -c Release services/pkhex` is enough; this makes the
same self-contained file scripts/build_exe.py puts inside the packaged app, which needs nothing installed.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pokeuds import pkhex  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rid", help="a .NET runtime id (win-x64, linux-x64, linux-arm64, osx-arm64...); default: this computer")
    parser.add_argument("--out", type=Path, help="the file to write (default: services/pkhex/dist/pokeuds-pkhex[.exe])")
    args = parser.parse_args()
    rid = args.rid or pkhex.runtime_id()
    name = "pokeuds-pkhex.exe" if rid.startswith("win") else "pokeuds-pkhex"
    path = pkhex.publish(rid, args.out or pkhex.PROJECT / "dist" / name)
    print(f"{path} ({path.stat().st_size >> 20} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
