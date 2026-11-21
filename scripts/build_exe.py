r"""Build the desktop app for this computer (docs/release.md):

- Windows: dist/PokeUDS/PokeUDS.exe (the launcher) and dist/PokeUDS/_internal (the app), zipped to
  dist/PokeUDS-windows.zip
- macOS: dist/PokeUDS.app, zipped to dist/PokeUDS-macos-arm64.zip (Apple Silicon) or -x64.zip (Intel)

    pip install -r requirements-gui.txt pyinstaller
    python scripts/build_exe.py [--icon some.ico|some.icns] [--no-zip] [--no-helper]
    python scripts/build_exe.py --zip-only       # Windows: zip dist/PokeUDS as it is (after signing it)

``flet pack`` (PyInstaller) builds the app with Python, the game profiles, the strings and Flet's window inside.

On Windows the app is ``_internal/PokeUDS-app.exe``, built flat (PyInstaller's ``--contents-directory .``, so its
Python sits beside it in ``_internal``), and the ``PokeUDS.exe`` players click is a small launcher compiled here from
packaging/launcher with MSVC (Visual Studio or its Build Tools, "Desktop development with C++"). An app moved away
from the files beside it cannot start; the launcher can be moved anywhere and still finds the app's folder
(docs/gui.md, The launcher). The top of the folder stays ``PokeUDS.exe`` and ``_internal``, as before the launcher,
so an older app's updater installs this layout without knowing it changed.

On Windows, Flet ships its window as a zip that it unpacks into ``%USERPROFILE%\.flet\client`` on first start; this
script unpacks it into the app's own folder instead (``_internal/flet_view``, found through ``FLET_VIEW_PATH`` by
packaging/pokeuds_app.py), so the app writes nothing there and runs wherever it is unzipped. The app keeps its bank in
``PokeUDS/Bank``, its settings in ``PokeUDS/Data`` and received Pokemon in ``PokeUDS/received``; a rebuild keeps those
folders and the zip never holds them, so an update never replaces them.

On macOS the app is a one-folder ``.app`` (flet pack refuses ``--onedir`` there, so it goes to PyInstaller directly)
and keeps Flet's own handling of its window, which unpacks into ``~/.flet`` on first start. The bank lives in
``~/Library/Application Support/PokeUDS/bank``: an app bundle is replaced whole by an update and is often read-only.
The zip is made with ``ditto``, which keeps the bundle's links, permissions and signatures. PyInstaller signs the
app ad hoc; it is not notarized, so the first start needs Open Anyway (README).

The bundle's own process never opens a window (Flet's client draws it, with its own Dock icon), so the bundle is marked
``LSUIElement``: a background app with no Dock icon or menu bar of its own. Without it the Dock showed two PokeUDS
icons, and the bundle's bounced for about a minute before macOS called it "Application Not Responding" (a Mac player,
2026-11-19). The trade runs, second copies of the app, get no Dock icon either. Changing Info.plist breaks the
bundle's signature, so the app is signed again ad hoc and checked.

Flet's window is signed again by flet pack without the entitlements Flet's own build gave it, and its file dialogs then
fail with ENTITLEMENT_NOT_FOUND (a Mac player, 2026-11-20). flet pack therefore runs through scripts/pack_flet.py
(pokeldn's fix), which seals the window's own entitlements back in, and the build checks the packed window has them.

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
APP_NAME = "PokeUDS-app"    # Windows: the app behind the launcher, _internal/PokeUDS-app.exe (pokeuds/app/home.py)
LAUNCHER = ROOT / "packaging" / "launcher"
BUNDLE_ID = "io.github.gr3nskydragon.pokeuds"
MAC = sys.platform == "darwin"
ENTRY = ROOT / "packaging" / "pokeuds_app.py"
DEFAULT_ICON = ROOT / "gui" / "assets" / ("icon.icns" if MAC else "icon.ico")
DATA = [("profiles", "profiles"), ("pokeuds/app/data", "pokeuds/app/data"), ("gui/assets", "gui/assets"),
        ("LICENSE", ".")]       # the AGPL-3.0 inside the app (and beside PokeUDS.exe on Windows)
BANK = "Bank"               # the Windows app's bank (pokeuds/app/bank.py): kept across builds, never zipped
APP_DATA = "Data"           # its data folder (pokeuds/app/settings.py): the same
RECEIVED = "received"       # and the received Pokemon (pokeuds/app/settings.py): the same
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
    """Run flet pack; -> the built app: dist/PokeUDS-app, flat (Windows), or dist/PokeUDS.app (macOS)."""
    # flet pack through scripts/pack_flet.py, as pokeldn runs it: on macOS it keeps the window's entitlements (file dialogs).
    args = [sys.executable, str(ROOT / "scripts" / "pack_flet.py"), "pack", str(ENTRY), "--name", NAME if MAC else APP_NAME,
            "--distpath", str(dist), "--yes",
            "--product-name", NAME, "--file-description", f"{NAME} {__version__}",
            "--product-version", __version__, "--file-version", file_version(__version__),
            "--copyright", "Gr3nSkyDragon and contributors",
            "-v"]       # Flet 1.1's pack prints only warnings without it; the build log keeps PyInstaller's progress
    args += ["--bundle-id", BUNDLE_ID] if MAC else ["--onedir"]
    for source, target in DATA:
        args += ["--add-data", f"{ROOT / source}{os.pathsep}{target}"]
    if icon:
        args += ["--icon", str(icon)]
    # The trade child imports pokeuds.cli from the same exe. esptool and esp_pylib (its logger, serial and reset code
    # since esptool 5) are bundled whole, modules and data, as pokeldn bundles them: --collect-data alone left their
    # modules to PyInstaller's import scan, which misses what they import at run time.
    args += ["--", "--paths", str(ROOT), "--collect-all", "esptool", "--collect-all", "esp_pylib",
             "--collect-submodules", "pokeuds",
             "--collect-submodules", "gui", "--workpath", str(ROOT / "build" / "pyinstaller"),
             "--specpath", str(ROOT / "build")]
    if MAC:
        args += ["--onedir"]        # after flet pack's own --onefile, so PyInstaller builds a one-folder .app
    else:
        args += ["--contents-directory", "."]       # flat: the folder becomes _internal, the exe inside it
    print(" ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True, **NO_WINDOW)
    if MAC:
        app = dist / f"{NAME}.app"
        if not (app / "Contents" / "MacOS" / NAME).is_file():
            sys.exit(f"flet pack finished but {app}/Contents/MacOS/{NAME} is missing")
        return app
    folder = dist / APP_NAME
    if not (folder / f"{APP_NAME}.exe").is_file():
        sys.exit(f"flet pack finished but {folder / APP_NAME}.exe is missing")
    if (folder / "_internal").exists():
        sys.exit(f"{folder} has an _internal folder of its own: PyInstaller ignored --contents-directory .")
    return folder


def msvc() -> dict[str, str]:
    """The environment of Visual Studio's x64 build tools (vcvars64.bat), found with vswhere."""
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio" \
        / "Installer" / "vswhere.exe"
    found = ""
    if vswhere.is_file():
        found = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                               capture_output=True, text=True).stdout.strip().split("\n")[0].strip()
    vcvars = Path(found) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat" if found else None
    if vcvars is None or not vcvars.is_file():
        sys.exit("The launcher needs Microsoft's C compiler: install Visual Studio or its Build Tools with "
                 '"Desktop development with C++", then build again.')
    out = subprocess.run(f'cmd /d /s /c ""{vcvars}" >nul && set"', capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line and not line.startswith("="))


def build_launcher(icon: Path | None, dest: Path) -> Path:
    """Windows: compile packaging/launcher into dest (PokeUDS.exe), with the app's icon and version."""
    work = ROOT / "build" / "launcher"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    header = [f'#define POKEUDS_VERSION "{__version__}"',
              f"#define POKEUDS_VERSION_COMMAS {file_version(__version__).replace('.', ',')}"]
    if icon:
        header.append(f'#define POKEUDS_ICON "{icon.resolve().as_posix()}"')
    (work / "version.h").write_text("\n".join(header) + "\n", encoding="utf-8")
    env = msvc()
    path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    tools = {name: shutil.which(name, path=path) for name in ("rc", "cl")}
    if not all(tools.values()):
        sys.exit(f"vcvars64.bat gave no {' or '.join(n for n, t in tools.items() if not t)}.exe")
    subprocess.run([tools["rc"], "/nologo", "/I", str(work), "/fo", str(work / "launcher.res"), "launcher.rc"],
                   cwd=LAUNCHER, env=env, check=True)
    subprocess.run([tools["cl"], "/nologo", "/O1", "/MT", "/W4", "/GS", "/utf-8", "launcher.c",
                    str(work / "launcher.res"), f"/Fo{work}\\", f"/Fe{dest}",
                    "/link", "/SUBSYSTEM:WINDOWS", "/MANIFEST:NO",
                    "user32.lib", "shell32.lib", "ole32.lib", "comctl32.lib"],
                   cwd=LAUNCHER, env=env, check=True)
    if not dest.is_file():
        sys.exit(f"the launcher was compiled but {dest} is missing")
    return dest


def assemble(app: Path, folder: Path, icon: Path | None) -> Path:
    """Windows: the flat app becomes folder/_internal and the launcher folder/PokeUDS.exe. -> folder."""
    internal = folder / "_internal"
    if internal.exists() or (folder / f"{NAME}.exe").exists():
        sys.exit(f"{folder} still holds an earlier build's program files")
    folder.mkdir(parents=True, exist_ok=True)
    app.rename(internal)
    build_launcher(icon, folder / f"{NAME}.exe")
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
            if path.is_file() and path.relative_to(folder).parts[0] not in (BANK, APP_DATA, RECEIVED):
                z.write(path, Path(NAME) / path.relative_to(folder))       # the player's folders stay theirs
    return out


def background_app(app: Path) -> None:
    """macOS: mark the bundle LSUIElement (no Dock icon or menu bar of its own; Flet's window has its own icon)."""
    import plistlib

    info = app / "Contents" / "Info.plist"
    with info.open("rb") as f:
        plist = plistlib.load(f)
    plist["LSUIElement"] = True
    with info.open("wb") as f:
        plistlib.dump(plist, f)


FILE_DIALOGS = "com.apple.security.files.user-selected.read-write"


def check_window_entitlements(app: Path) -> None:
    """macOS: Flet's window, as packed in the app, carries the entitlement its file dialogs need and a valid signature
    (scripts/pack_flet.py; pokeldn's scripts/check_app.py checks the same). Without it every file picker fails with
    ENTITLEMENT_NOT_FOUND on the players' Macs, so the build stops here."""
    import plistlib
    import tarfile
    import tempfile

    archives = list(app.rglob("flet-macos.tar.gz"))
    if len(archives) != 1:
        sys.exit(f"{app} holds {len(archives)} flet-macos.tar.gz, not one: flet pack's layout changed")
    with tempfile.TemporaryDirectory(prefix="pokeuds-view-check-") as folder:
        with tarfile.open(archives[0]) as archive:
            archive.extractall(folder, filter="data")
        windows = list(Path(folder).glob("*.app"))
        if len(windows) != 1:
            sys.exit(f"{archives[0]} holds {len(windows)} .app bundles, not one")
        result = subprocess.run(["codesign", "--display", "--entitlements", "-", "--xml", str(windows[0])],
                                capture_output=True, check=True)
        entitlements = plistlib.loads(result.stdout) if result.stdout else {}
        if not entitlements.get(FILE_DIALOGS):
            sys.exit(f"Flet's window ({archives[0].name}) is signed without {FILE_DIALOGS}, so file dialogs would fail "
                     f"(ENTITLEMENT_NOT_FOUND): scripts/pack_flet.py no longer reaches Flet's signing. "
                     f"It has: {sorted(entitlements) or 'none'}")
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(windows[0])], check=True)


def resign(app: Path) -> None:
    """macOS: sign the bundle again ad hoc after its Info.plist changed (its binaries keep PyInstaller's signatures),
    and check it as the release workflow does, so a broken signature stops the build here."""
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)], check=True)


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
    parser.add_argument("--zip-only", action="store_true",
                        help="Windows: zip the app already in dist/PokeUDS, as the release does once it is signed")
    parser.add_argument("--no-helper", action="store_true",
                        help="do not publish and bundle the PKHeX helper (the app will fall back to a download)")
    a = parser.parse_args(argv)
    if sys.platform not in ("win32", "darwin"):
        sys.exit("build_exe.py builds the Windows and macOS apps; on Linux, run PokeUDS from source (README)")
    if a.zip_only:
        missing = [p for p in (a.dist / NAME / f"{NAME}.exe", a.dist / NAME / "_internal" / f"{APP_NAME}.exe")
                   if not p.is_file()]
        if MAC or missing:
            sys.exit(f"--zip-only zips a built Windows app: {missing[0] if missing else 'not on a Mac'} is missing")
        print(f"zipped {zip_folder(a.dist / NAME, a.dist / zip_name())}")
        return 0
    icon = a.icon or (DEFAULT_ICON if DEFAULT_ICON.is_file() else None)
    if icon and not icon.is_file():
        sys.exit(f"{icon} does not exist")
    print(f"PokeUDS {__version__} for {'macOS ' + mac_arch() if MAC else 'Windows'}, "
          f"icon: {icon or 'none (Flet default)'}", flush=True)
    if not a.no_helper:
        bundle_helper()
    # The bank, data and received folders made by running the built exe here survive the rebuild: moved aside, then
    # put back. Not into dist/ or build/: flet pack --yes deletes both whole before it starts. (The Mac app keeps
    # its bank outside its bundle.)
    kept = {BANK: ROOT / ".bank-kept", APP_DATA: ROOT / ".data-kept", RECEIVED: ROOT / ".received-kept"}
    for name, aside in kept.items():
        if aside.exists():
            sys.exit(f"{aside} was kept by an earlier build that stopped: move it back to {a.dist / NAME / name}")
    for name, aside in kept.items():
        if not MAC and (a.dist / NAME / name).is_dir():
            (a.dist / NAME / name).rename(aside)
    shutil.rmtree(a.dist / NAME, ignore_errors=True)
    shutil.rmtree(a.dist / APP_NAME, ignore_errors=True)
    try:
        built = pack(icon, a.dist.resolve())
    finally:
        for name, aside in kept.items():
            if aside.is_dir():
                (a.dist / NAME).mkdir(parents=True, exist_ok=True)
                aside.rename(a.dist / NAME / name)
    if MAC:
        print(f"built {built}")
        check_window_entitlements(built)
        print(f"Flet's window is signed with {FILE_DIALOGS} (file dialogs)")
        background_app(built)
        resign(built)
        print("marked LSUIElement (one Dock icon: the window's) and signed again ad hoc")
        if not a.no_zip:
            print(f"zipped {zip_app(built, a.dist / zip_name())}")
        return 0
    built = assemble(built, a.dist / NAME, icon)
    print(f"built {built / NAME}.exe (the launcher) and {built / '_internal' / APP_NAME}.exe")
    shutil.copyfile(ROOT / "LICENSE", built / "LICENSE.txt")
    print(f"window unpacked to {unpack_view(built)}")
    if not a.no_zip:
        print(f"zipped {zip_folder(built, a.dist / zip_name())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
