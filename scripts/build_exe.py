r"""Build the Windows app: dist/PokeUDS/PokeUDS.exe and dist/PokeUDS-windows.zip (docs/release.md).

    pip install -r requirements-gui.txt pyinstaller
    python scripts/build_exe.py [--icon PokeUDS.ico]

``flet pack`` (PyInstaller) builds one folder holding the app, Python, the game profiles, the strings and Flet's
window. Flet ships its window as a zip that it unpacks into ``%USERPROFILE%\.flet\client`` on first start; this
script unpacks it into the app's own folder instead (``_internal/flet_view``, found through ``FLET_VIEW_PATH`` by
packaging/pokeuds_app.py), so the app writes nothing there and runs wherever it is unzipped. The icon is ``--icon``,
else ``gui/assets/icon.ico`` (made from ``packaging/icon.jpg`` by scripts/make_icon.py), else Flet's own. The PKHeX
helper is not inside: the app downloads it from the release when a page needs it. The app keeps its bank in
``PokeUDS/Bank``; a rebuild keeps that folder and the zip never holds it, so an update never replaces a bank.
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
BANK = "Bank"               # the packaged app's bank (pokeuds/app/bank.py): kept across builds, never zipped
VIEW = "flet_view"          # the unpacked window, beside the app's Python in _internal (packaging/pokeuds_app.py)


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


def unpack_view(folder: Path) -> Path:
    """Unpack Flet's window (already given the icon by flet pack) into the app and drop the zip it came in."""
    internal = folder / "_internal"
    archive = internal / "flet_desktop" / "app" / "flet-windows.zip"
    if not archive.is_file():
        sys.exit(f"{archive} is missing: flet pack's layout changed")
    view = internal / VIEW
    shutil.rmtree(view, ignore_errors=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(view)
    if not (view / "flet" / "flet.exe").is_file():
        sys.exit(f"{archive} holds no flet/flet.exe")
    for leftover in archive.parent.iterdir():       # the zip, its .sha256, the .last-used flet pack touched
        leftover.unlink()
    return view / "flet"


def zip_folder(folder: Path, out: Path) -> Path:
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.relative_to(folder).parts[0] != BANK:     # the player's bank stays theirs
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
    # A bank made by running the built exe here survives the rebuild: moved aside, then put back. Not into dist/ or
    # build/: flet pack --yes deletes both whole before it starts.
    kept = ROOT / ".bank-kept"
    if kept.exists():
        sys.exit(f"{kept} is a bank kept by an earlier build that stopped: move it back to {a.dist / NAME / BANK}")
    if (a.dist / NAME / BANK).is_dir():
        (a.dist / NAME / BANK).rename(kept)
    shutil.rmtree(a.dist / NAME, ignore_errors=True)
    try:
        folder = pack(icon, a.dist.resolve())
    finally:
        if kept.is_dir():
            (a.dist / NAME).mkdir(parents=True, exist_ok=True)
            kept.rename(a.dist / NAME / BANK)
    print(f"built {folder / NAME}.exe")
    print(f"window unpacked to {unpack_view(folder)}")
    if not a.no_zip:
        print(f"zipped {zip_folder(folder, a.dist / f'{NAME}-windows.zip')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
