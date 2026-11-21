"""Where the packaged Windows app lives, the note that lets its launcher find it, and its desktop shortcut
(docs/gui.md, The launcher).

The Windows app is two programs in one folder, its home: ``PokeUDS.exe``, the small launcher players click
(packaging/launcher), and ``_internal/PokeUDS-app.exe``, the app, which cannot start away from the files beside it.
The home also holds the player's ``Bank``, ``Data`` and ``received``. Each time the app starts it writes its home to
``%LOCALAPPDATA%\\PokeUDS\\home.txt``, so a launcher dragged out of the folder still opens it; the launcher searches,
then asks, only when the app has never started from that folder.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

LAUNCHER = "PokeUDS.exe"
APP = "PokeUDS-app.exe"
INTERNAL = "_internal"
SHORTCUT = "PokeUDS.lnk"


def install_folder(executable: str | None = None) -> Path:
    """The folder a packaged exe belongs to: the PokeUDS folder above ``_internal`` for the app behind the launcher,
    the exe's own folder for an app from before the launcher (and for the launcher itself)."""
    exe = Path(executable or sys.executable).resolve()
    if exe.name.lower() == APP.lower() and exe.parent.name.lower() == INTERNAL.lower():
        return exe.parent.parent
    return exe.parent


def in_home(executable: str | None = None) -> bool:
    """The packaged Windows app running as ``_internal/PokeUDS-app.exe`` in its home, launcher or not."""
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        return False
    exe = Path(executable or sys.executable).resolve()
    return install_folder(str(exe)) != exe.parent


def behind_launcher(executable: str | None = None) -> bool:
    """In its home, with the launcher still beside ``_internal`` (what a shortcut opens)."""
    return in_home(executable) and (install_folder(executable) / LAUNCHER).is_file()


def home_file() -> Path | None:
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "PokeUDS" / "home.txt" if local else None


def remember(folder: Path, path: Path | None = None) -> bool:
    """Write ``folder`` to home.txt (UTF-8, one line) when it is not already there. -> whether home.txt names it."""
    path = path or home_file()
    if path is None:
        return False
    text = str(folder)
    try:
        if path.is_file() and path.read_text(encoding="utf-8-sig").strip() == text:
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_text(text + "\n", encoding="utf-8", newline="\n")
        os.replace(part, path)
        return True
    except OSError as exc:
        log.info("could not write %s: %s", path, exc)
        return False


def desktop() -> Path | None:
    """The player's Desktop as Windows knows it (OneDrive may move it); None elsewhere or when Windows does not say."""
    if sys.platform != "win32":
        return None
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    folder_id = GUID.from_buffer_copy(uuid.UUID("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}").bytes_le)   # FOLDERID_Desktop
    found = ctypes.c_wchar_p()
    try:
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(found)) != 0:
            return None
        return Path(found.value) if found.value else None
    except (AttributeError, OSError):
        return None
    finally:
        if found:
            ctypes.windll.ole32.CoTaskMemFree(found)


def shortcut_path() -> Path | None:
    folder = desktop()
    return folder / SHORTCUT if folder else None


def has_shortcut() -> bool:
    path = shortcut_path()
    return path is not None and path.is_file()


# The paths go in through the environment, never into the script's text: no quoting to get wrong.
_MAKE = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:POKEUDS_LINK); "
         "$s.TargetPath = $env:POKEUDS_TARGET; $s.WorkingDirectory = $env:POKEUDS_HOME; "
         "$s.IconLocation = $env:POKEUDS_TARGET + ',0'; $s.Description = 'PokeUDS'; $s.Save()")


def make_shortcut(home: Path, link: Path | None = None) -> Path:
    """``PokeUDS.lnk`` on the Desktop, opening ``home``'s launcher. -> the shortcut; OSError when it cannot be made."""
    link = link or shortcut_path()
    if link is None:
        raise OSError("Windows did not say where the Desktop is")
    target = home / LAUNCHER
    if not target.is_file():
        raise OSError(f"{target} is missing")
    env = {**os.environ, "POKEUDS_LINK": str(link), "POKEUDS_TARGET": str(target), "POKEUDS_HOME": str(home)}
    from ..util import NO_WINDOW

    try:
        done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-Command", _MAKE], env=env, capture_output=True, text=True, timeout=60, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(f"PowerShell did not run: {exc}") from exc
    if done.returncode != 0 or not link.is_file():
        raise OSError((done.stderr or done.stdout).strip().splitlines()[-1] if (done.stderr or done.stdout).strip()
                      else f"PowerShell exited with code {done.returncode}")
    return link
