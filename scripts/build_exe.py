r"""Build the desktop app for this computer (docs/release.md):

- Windows: dist/PokeUDS/PokeUDS.exe, zipped to dist/PokeUDS-windows.zip
- macOS: dist/PokeUDS.app, zipped to dist/PokeUDS-macos-arm64.zip (Apple Silicon) or -x64.zip (Intel)

    pip install -r requirements-gui.txt pyinstaller
    python scripts/build_exe.py [--icon some.ico|some.icns] [--no-zip] [--no-helper]

``flet pack`` (PyInstaller) builds the app with Python, the game profiles, the strings and Flet's window inside.

On Windows, Flet ships its window as a zip that it unpacks into ``%USERPROFILE%\.flet\client`` on first start; this
script unpacks it into the app's own folder instead (``_internal/flet_view``, found through ``FLET_VIEW_PATH`` by
packaging/pokeuds_app.py), so the app writes nothing there and runs wherever it is unzipped. The app keeps its bank in
``PokeUDS/Bank``; a rebuild keeps that folder and the zip never holds it, so an update never replaces a bank.

On macOS the app is a one-folder ``.app`` (flet pack refuses ``--onedir`` there, so it goes to PyInstaller directly)
and keeps Flet's own handling of its window, which unpacks into ``~/.flet`` on first start. The bank lives in
``~/Library/Application Support/PokeUDS/bank``: an app bundle is replaced whole by an update and is often read-only.
The zip is made with ``ditto``, which keeps the bundle's links, permissions and signatures. PyInstaller signs the
app ad hoc; it is not notarized, so the first start needs Open Anyway (README).

The icon is ``--icon``, else ``gui/assets/icon.ico`` (Windows) or ``icon.icns`` (macOS), made from
``packaging/icon.jpg`` by scripts/make_icon.py. The PKHeX helper rides inside: this script first publishes the
self-contained helper for this computer into ``pokeuds/app/data/pkhex`` (needs the .NET 10 SDK on the build machine),
so the packaged app needs no download and no SDK of its own; ``--no-helper`` leaves it out.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pokeuds import __version__, pkhex  # noqa: E402
from pokeuds.util import NO_WINDOW  # noqa: E402

NAME = "PokeUDS"
BUNDLE_ID = "io.github.gr3nskydragon.pokeuds"
MAC = sys.platform == "darwin"
ENTRY = ROOT / "packaging" / "pokeuds_app.py"
DEFAULT_ICON = ROOT / "gui" / "assets" / ("icon.icns" if MAC else "icon.ico")
DATA = [("profiles", "profiles"), ("pokeuds/app/data", "pokeuds/app/data"), ("gui/assets", "gui/assets"),
        ("LICENSE", ".")]       # the AGPL-3.0 inside the app (and beside PokeUDS.exe on Windows)
BANK = "Bank"               # the Windows app's bank (pokeuds/app/bank.py): kept across builds, never zipped
VIEW = "flet_view"          # the unpacked window, beside the app's Python in _internal (packaging/pokeuds_app.py)


def file_version(version: str) -> str:
    parts = [p for p in version.split(".") if p.isdigit()][:4]
    return ".".join(parts + ["0"] * (4 - len(parts)))


def mac_arch() -> str:
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"


def zip_name() -> str:
    return f"{NAME}-macos-{mac_arch()}.zip" if MAC else f"{NAME}-windows.zip"


def bundle_helper() -> Path:
    """Publish the self-contained PKHeX helper into pokeuds/app/data/pkhex so flet pack ships it (DATA already covers
    that folder). The player then needs neither a download nor the .NET SDK; this build machine needs the .NET 10 SDK."""
    dest = pkhex.bundled_path()
    rid = pkhex.runtime_id()
    print(f"publishing the PKHeX helper ({rid}) into {dest} ...", flush=True)
    pkhex.publish(rid, dest)
    print(f"bundled helper: {dest} ({dest.stat().st_size >> 20} MB)", flush=True)
    return dest


def pack(icon: Path | None, dist: Path) -> Path:
    """Run flet pack; -> the built app: dist/PokeUDS (Windows) or dist/PokeUDS.app (macOS)."""
    flet = Path(sys.executable).with_name("flet.exe" if os.name == "nt" else "flet")
    args = [str(flet) if flet.exists() else "flet", "pack", str(ENTRY), "--name", NAME,
            "--distpath", str(dist), "--yes",
            "--product-name", NAME, "--file-description", f"{NAME} {__version__}",
            "--product-version", __version__, "--file-version", file_version(__version__),
            "--copyright", "Gr3nSkyDragon and contributors"]
    args += ["--bundle-id", BUNDLE_ID] if MAC else ["--onedir"]
    for source, target in DATA:
        args += ["--add-data", f"{ROOT / source}{os.pathsep}{target}"]
    if icon:
        args += ["--icon", str(icon)]
    # The trade child imports pokeuds.cli from the same exe; esptool reads its flasher stubs from its package data.
    args += ["--", "--paths", str(ROOT), "--collect-data", "esptool", "--collect-submodules", "pokeuds",
             "--collect-submodules", "gui", "--workpath", str(ROOT / "build" / "pyinstaller"),
             "--specpath", str(ROOT / "build")]
    if MAC:
        args += ["--onedir"]        # after flet pack's own --onefile, so PyInstaller builds a one-folder .app
    print(" ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True, **NO_WINDOW)
    if MAC:
        app = dist / f"{NAME}.app"
        if not (app / "Contents" / "MacOS" / NAME).is_file():
            sys.exit(f"flet pack finished but {app}/Contents/MacOS/{NAME} is missing")
        return app
    folder = dist / NAME
    if not (folder / f"{NAME}.exe").is_file():
        sys.exit(f"flet pack finished but {folder / NAME}.exe is missing")
    return folder


def unpack_view(folder: Path) -> Path:
    """Windows: unpack Flet's window (already given the icon by flet pack) into the app and drop the zip it came in."""
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


def zip_app(app: Path, out: Path) -> Path:
    """macOS: zip the .app as Finder does (ditto keeps symlinks, permissions and the code signature)."""
    out.unlink(missing_ok=True)
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(out)], check=True)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--icon", type=Path, help="a .ico (Windows) or .icns (macOS) for the app and its window "
                                                  f"(default: gui/assets/{DEFAULT_ICON.name}, when it exists)")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--no-zip", action="store_true")
    parser.add_argument("--no-helper", action="store_true",
                        help="do not publish and bundle the PKHeX helper (the app will fall back to a download)")
    a = parser.parse_args(argv)
    if sys.platform not in ("win32", "darwin"):
        sys.exit("build_exe.py builds the Windows and macOS apps; on Linux, run PokeUDS from source (README)")
    icon = a.icon or (DEFAULT_ICON if DEFAULT_ICON.is_file() else None)
    if icon and not icon.is_file():
        sys.exit(f"{icon} does not exist")
    print(f"PokeUDS {__version__} for {'macOS ' + mac_arch() if MAC else 'Windows'}, "
          f"icon: {icon or 'none (Flet default)'}", flush=True)
    if not a.no_helper:
        bundle_helper()
    # A bank made by running the built exe here survives the rebuild: moved aside, then put back. Not into dist/ or
    # build/: flet pack --yes deletes both whole before it starts. (The Mac app keeps its bank outside the bundle.)
    kept = ROOT / ".bank-kept"
    if kept.exists():
        sys.exit(f"{kept} is a bank kept by an earlier build that stopped: move it back to {a.dist / NAME / BANK}")
    if not MAC and (a.dist / NAME / BANK).is_dir():
        (a.dist / NAME / BANK).rename(kept)
    shutil.rmtree(a.dist / NAME, ignore_errors=True)
    try:
        built = pack(icon, a.dist.resolve())
    finally:
        if kept.is_dir():
            (a.dist / NAME).mkdir(parents=True, exist_ok=True)
            kept.rename(a.dist / NAME / BANK)
    if MAC:
        print(f"built {built}")
        if not a.no_zip:
            print(f"zipped {zip_app(built, a.dist / zip_name())}")
        return 0
    print(f"built {built / NAME}.exe")
    shutil.copyfile(ROOT / "LICENSE", built / "LICENSE.txt")
    print(f"window unpacked to {unpack_view(built)}")
    if not a.no_zip:
        print(f"zipped {zip_folder(built, a.dist / zip_name())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
