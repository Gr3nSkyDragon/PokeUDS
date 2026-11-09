"""Build the Windows app: dist/PokeUDS/PokeUDS.exe and dist/PokeUDS-windows.zip (docs/release.md).

    pip install -r requirements-gui.txt pyinstaller
    python scripts/build_exe.py [--icon PokeUDS.ico]

``flet pack`` (PyInstaller) builds one folder holding the app, Python, the game profiles, the strings and Flet's
window, which it unpacks on first start. The icon is ``--icon``, else ``gui/assets/icon.ico`` (made from
``packaging/icon.jpg``), else Flet's own. The PKHeX helper is not inside: the app downloads it from the release
when a page needs it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pokeuds import __version__  # noqa: E402
from pokeuds.util import NO_WINDOW  # noqa: E402

NAME = "PokeUDS"
ENTRY = ROOT / "packaging" / "pokeuds_app.py"
DEFAULT_ICON = ROOT / "gui" / "assets" / "icon.ico"
DATA = [("profiles", "profiles"), ("pokeuds/app/data", "pokeuds/app/data"), ("gui/assets", "gui/assets")]


def file_version(version: str) -> str:
    parts = [p for p in version.split(".") if p.isdigit()][:4]
    return ".".join(parts + ["0"] * (4 - len(parts)))


def pack(icon: Path | None, dist: Path) -> Path:
    flet = Path(sys.executable).with_name("flet.exe" if os.name == "nt" else "flet")
    args = [str(flet) if flet.exists() else "flet", "pack", str(ENTRY), "--name", NAME, "--onedir",
            "--distpath", str(dist), "--yes",
            "--product-name", NAME, "--file-description", f"{NAME} {__version__}",
            "--product-version", __version__, "--file-version", file_version(__version__),
            "--copyright", "Gr3nSkyDragon and contributors"]
    for source, target in DATA:
        args += ["--add-data", f"{ROOT / source}{os.pathsep}{target}"]
    if icon:
        args += ["--icon", str(icon)]
    # The trade child imports pokeuds.cli from the same exe; esptool reads its flasher stubs from its package data.
    args += ["--", "--paths", str(ROOT), "--collect-data", "esptool", "--collect-submodules", "pokeuds",
             "--collect-submodules", "gui", "--workpath", str(ROOT / "build" / "pyinstaller"),
             "--specpath", str(ROOT / "build")]
    print(" ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True, **NO_WINDOW)
    folder = dist / NAME
    if not (folder / f"{NAME}.exe").is_file():
        sys.exit(f"flet pack finished but {folder / NAME}.exe is missing")
    return folder


def zip_folder(folder: Path, out: Path) -> Path:
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                z.write(path, Path(NAME) / path.relative_to(folder))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--icon", type=Path, help=f"a .ico for the exe and its window (default: gui/assets/{DEFAULT_ICON.name}, "
                                                  "when it exists)")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--no-zip", action="store_true")
    a = parser.parse_args(argv)
    icon = a.icon or (DEFAULT_ICON if DEFAULT_ICON.is_file() else None)
    if icon and not icon.is_file():
        sys.exit(f"{icon} does not exist")
    print(f"PokeUDS {__version__}, icon: {icon or 'none (Flet default)'}", flush=True)
    shutil.rmtree(a.dist / NAME, ignore_errors=True)
    folder = pack(icon, a.dist.resolve())
    print(f"built {folder / NAME}.exe")
    if not a.no_zip:
        print(f"zipped {zip_folder(folder, a.dist / f'{NAME}-windows.zip')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
