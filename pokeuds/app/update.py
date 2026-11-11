"""Updates for the packaged Windows app: check GitHub for a newer release, download and check it, and let the new
copy replace the old one once it has quit (docs/gui.md, Updates).

The design is pokeldn's (pokeldn/app/update.py, AGPL-3.0, Decryptu; its waiting for processes is adapted here):
the release's ``SHA256SUMS`` must list the archive's checksum, the archive unpacks into ``Data/update/new``, and
the new app runs as a helper (``PokeUDS.exe --apply-update``) that waits for the old app to quit, swaps the app in
and opens it, putting the old app back on any failure. It differs where PokeUDS keeps the player's files beside
the app: only what a release ships (``PokeUDS.exe``, ``_internal``, ``LICENSE.txt``) is replaced. ``Bank`` (and the
GTS's ``Bank/.gts``), ``Data`` and ``received``, and anything else in the folder, are never moved or touched.

PokeUDS's releases are pre-releases (``v0.*``) and are offered; a draft never is. The Mac app is offered as a
download only (its bundle is replaced whole by hand), and an app run from source is never updated.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from .. import __version__

REPO = "Gr3nSkyDragon/PokeUDS"
RELEASES = os.environ.get("POKEUDS_UPDATE_URL") or f"https://api.github.com/repos/{REPO}/releases?per_page=20"
NAME = "PokeUDS"
WINDOWS_ASSET = "PokeUDS-windows.zip"
KEEP = frozenset({"Bank", "Data", "received"})     # the player's folders beside the app: never touched
EXECUTABLE = "PokeUDS.exe"
TIMEOUT = 8.0
MAX_JSON = 2 << 20
MAX_ARCHIVE = 600 << 20
MAX_FILES = 20000
EXIT_WAIT = 120.0          # s the helper waits for the old app to quit
RENAME_WAIT = 30.0         # s it keeps trying to move a file Windows still holds (an antivirus scan, a closing window)
OLD = ".update-old"        # where the replaced files wait, beside the app, until the new one has started


class Cancelled(Exception):
    pass


@dataclass
class Release:
    version: str               # "0.1.4"
    tag: str                   # "v0.1.4"
    page: str                  # the release's page on GitHub
    notes: str
    download: str              # the archive for this computer, or ""
    checksums: str             # SHA256SUMS's address, or ""

    @property
    def installable(self) -> bool:
        return bool(self.download and self.checksums)


def parse(version: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (version or "").strip())
    return tuple(int(x) for x in match.groups()) if match else None


def asset_name(system: str = sys.platform) -> str | None:
    """The release file this computer installs: only the Windows app updates itself."""
    return WINDOWS_ASSET if system == "win32" else None


def _allowed(address: str) -> bool:
    parts = urllib.parse.urlsplit(address)
    return (parts.scheme == "https" and bool(parts.hostname) and not parts.username and not parts.password
            or parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost"))


def _context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError):
        return ssl.create_default_context()


def _open(url: str, timeout: float = 30.0):
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not _allowed(newurl):
                raise OSError("the download redirected to an unsafe address")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    if not _allowed(url):
        raise OSError("not an HTTPS address")
    request = urllib.request.Request(url, headers={"User-Agent": "PokeUDS-updater",
                                                   "Accept": "application/vnd.github+json"})
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_context()), Redirect())
    return opener.open(request, timeout=timeout)


def release_asset(page: str, url: str, name: str) -> bool:
    """Whether ``url`` is ``name`` of the release at ``page``: PokeUDS's own GitHub release (or the tests' local
    server), never an address a reply could point anywhere."""
    origin = urllib.parse.urlsplit(page or "")
    if origin.scheme == "http" and origin.hostname in ("127.0.0.1", "localhost"):
        asset = urllib.parse.urlsplit(url or "")
        return asset.scheme == "http" and asset.netloc == origin.netloc and asset.path.endswith("/" + name)
    prefix = f"https://github.com/{REPO}/releases/tag/"
    if not (page or "").startswith(prefix):
        return False
    tag = page[len(prefix):]
    return parse(tag) is not None and url == f"https://github.com/{REPO}/releases/download/{tag}/{name}"


def newest(replies: list, current: str = __version__, system: str = sys.platform) -> Release | None:
    """The newest release above ``current`` in GitHub's list: drafts and odd tags skipped, pre-releases offered."""
    best: tuple[tuple[int, ...], dict] | None = None
    for reply in replies if isinstance(replies, list) else []:
        if not isinstance(reply, dict) or reply.get("draft"):
            continue
        version = parse(str(reply.get("tag_name", "")))
        if version is None or not isinstance(reply.get("html_url"), str):
            continue
        if best is None or version > best[0]:
            best = (version, reply)
    if best is None or parse(current) is None or best[0] <= parse(current):
        return None
    reply = best[1]
    assets = {a.get("name"): a.get("browser_download_url") for a in reply.get("assets") or [] if isinstance(a, dict)}
    name = asset_name(system)
    download = assets.get(name, "") if name else ""
    checksums = assets.get("SHA256SUMS", "")
    page = reply["html_url"]
    if download and not release_asset(page, download, name):
        download = ""
    if checksums and not release_asset(page, checksums, "SHA256SUMS"):
        checksums = ""
    return Release(".".join(map(str, best[0])), reply["tag_name"], page, str(reply.get("body") or "")[:4000],
                   download or "", checksums or "")


def check(url: str = RELEASES, current: str = __version__) -> Release | None:
    """Ask GitHub; -> the newer release, or None. OSError when GitHub does not answer."""
    with _open(url, TIMEOUT) as reply:
        data = reply.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise OSError("GitHub's answer is too large")
    try:
        return newest(json.loads(data.decode("utf-8")), current)
    except ValueError as exc:
        raise OSError(f"GitHub's answer is not JSON: {exc}") from exc


# -- where an update happens ----------------------------------------------------------------------

def install_root(executable: str = sys.executable, frozen: bool | None = None, system: str = sys.platform) -> Path | None:
    """The packaged Windows app's folder (where PokeUDS.exe is); None from source and on other systems."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not frozen or system != "win32":
        return None
    return Path(executable).resolve().parent


def work_folder(root: Path) -> Path:
    return root / "Data" / "update"


def blocker(root: Path | None) -> str:
    """Why this app cannot update itself in place ("" when it can)."""
    if root is None:
        return "only the packaged Windows app updates itself; download the new version from the release page"
    try:
        probe = root / ".update-test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return f"PokeUDS cannot write its own folder ({root}): move it somewhere writable, or download by hand"
    return ""


def checksums(text: str) -> dict[str, str]:
    found = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](\S.*)", line.strip())
        if match:
            found[match.group(2)] = match.group(1).lower()
    return found


def fetch(url: str, dest: Path, progress: Callable[[int, int], None] = lambda done, total: None,
          cancelled: Callable[[], bool] = lambda: False) -> str:
    """Download ``url`` to ``dest``; -> its SHA-256. A partial file is removed."""
    digest, done = hashlib.sha256(), 0
    try:
        with _open(url) as reply, dest.open("wb") as out:
            total = int(reply.headers.get("Content-Length") or 0)
            if total > MAX_ARCHIVE:
                raise OSError(f"the download is {total} bytes")
            while chunk := reply.read(1 << 16):
                if cancelled():
                    raise Cancelled
                done += len(chunk)
                if done > MAX_ARCHIVE:
                    raise OSError("the download is too large")
                digest.update(chunk)
                out.write(chunk)
                progress(done, total)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def unpack(archive: Path, folder: Path) -> Path:
    """Unpack the release zip into an empty ``folder``; -> the app inside (``folder/PokeUDS``). Every name must lie
    under ``PokeUDS/``, none may climb out, and the app must not ship a folder the player keeps."""
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as z:
            members = z.infolist()
            if len(members) > MAX_FILES:
                raise OSError("the archive holds too many files")
            for m in members:
                path = PurePosixPath(m.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in m.filename or not path.parts \
                        or path.parts[0] != NAME:
                    raise OSError(f"the archive holds an unsafe name: {m.filename}")
                if len(path.parts) > 1 and path.parts[1] in KEEP:
                    raise OSError(f"the archive would replace the player's {path.parts[1]} folder")
            z.extractall(folder)
        app = folder / NAME
        if not (app / EXECUTABLE).is_file():
            raise OSError(f"the archive holds no {NAME}/{EXECUTABLE}")
        return app
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def prepare(release: Release, root: Path, progress: Callable[[int, int], None] = lambda done, total: None,
            cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Download, check and unpack the release into ``root/Data/update``; -> the new app, ready for start_swap."""
    if not release.installable:
        raise OSError("the release has no Windows app or no SHA256SUMS")
    work = work_folder(root)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    with _open(release.checksums) as reply:
        expected = checksums(reply.read(MAX_JSON).decode("utf-8", "replace")).get(WINDOWS_ASSET)
    if not expected:
        raise OSError(f"SHA256SUMS does not list {WINDOWS_ASSET}")
    archive = work / WINDOWS_ASSET
    if fetch(release.download, archive, progress, cancelled) != expected:
        archive.unlink(missing_ok=True)
        raise OSError(f"{WINDOWS_ASSET} does not match its SHA-256 in SHA256SUMS")
    try:
        return unpack(archive, work / "new")
    finally:
        archive.unlink(missing_ok=True)


def _spawn(args: list[str], cwd: Path, out=subprocess.DEVNULL) -> None:
    """Start a program that outlives this one, with no console (DETACHED_PROCESS: nothing flashes open)."""
    detached = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    subprocess.Popen(args, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=out, stderr=out, close_fds=True,
                     creationflags=detached, start_new_session=sys.platform != "win32")


def start_swap(new: Path, root: Path, version: str) -> None:
    """Start the new app as the helper that replaces the app at ``root`` once this process exits."""
    args = [str(new / EXECUTABLE), "--apply-update", str(new), str(root), str(os.getpid()), version]
    with (work_folder(root) / "helper.log").open("ab") as log:
        # Started from Explorer, this app's working folder is root: inherited, Windows would refuse the moves.
        _spawn(args, new.parent, log)


# -- the helper -----------------------------------------------------------------------------------

def wait_for_exit(pid: int, timeout: float) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
        if not handle:
            return True
        try:
            return kernel.WaitForSingleObject(handle, int(timeout * 1000)) == 0
        finally:
            kernel.CloseHandle(handle)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        time.sleep(0.2)
    return False


def running_from(paths: list[Path]) -> list[int]:
    """Windows: the processes whose executable lies at or under one of ``paths`` (the old app's window, its PKHeX
    helper, a trade it started); each keeps its files from being moved. Elsewhere a running file never blocks."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    kernel, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    pids = (wintypes.DWORD * 4096)()
    size = wintypes.DWORD()
    if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(size)):
        return []
    files = {os.path.normcase(os.path.abspath(p)) for p in paths}
    folders = tuple(f.rstrip(os.sep) + os.sep for f in files)
    found = []
    for pid in pids[:size.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = kernel.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            name, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            if kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)) and pid != os.getpid():
                image = os.path.normcase(name.value)
                if image in files or image.startswith(folders):
                    found.append(pid)
        finally:
            kernel.CloseHandle(handle)
    return found


def wait_for(condition: Callable[[], bool], timeout: float) -> bool:
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > end:
            return False
        time.sleep(0.1)
    return True


def end_processes(paths: list[Path], timeout: float) -> None:
    """Wait for the old app's processes to end, then end the rest."""
    if wait_for(lambda: not running_from(paths), timeout):
        return
    for pid in running_from(paths):
        try:
            os.kill(pid, 9)                                    # TerminateProcess on Windows
        except OSError:
            pass
    wait_for(lambda: not running_from(paths), 5.0)


def _move(source: Path, dest: Path, timeout: float = RENAME_WAIT) -> None:
    end = time.monotonic() + timeout
    while True:
        try:
            os.rename(source, dest)
            return
        except OSError:
            if time.monotonic() > end:
                raise
            time.sleep(0.5)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def launch(root: Path) -> None:
    _spawn([str(root / EXECUTABLE)], root)


def apply(new: Path, root: Path, pid: int, version: str, relaunch: Callable[[Path], None] | None = None,
          helper: int | None = None, exit_wait: float = EXIT_WAIT) -> bool:
    """The helper: wait for the old app (``pid``) to quit, replace what the release ships in ``root`` with ``new``'s,
    and open the app again. The player's folders (KEEP) and anything else the release does not ship stay as they
    are. Any failure puts the old files back. ``root/Data/update/outcome.json`` tells the app that opens."""
    relaunch = relaunch or launch
    outcome = work_folder(root) / "outcome.json"

    def record(error: str = "") -> None:
        outcome.parent.mkdir(parents=True, exist_ok=True)
        outcome.write_text(json.dumps({"version": version, "error": error, "helper": helper}), encoding="utf-8")

    shipped = sorted(p.name for p in new.iterdir()) if new.is_dir() else []
    if EXECUTABLE not in shipped or KEEP & set(shipped) or OLD in shipped:
        record(f"the update in {new} is not a PokeUDS app; nothing was changed")
        return False
    if not wait_for_exit(pid, exit_wait):
        record("the old app did not quit; nothing was changed")
        return False
    end_processes([root / EXECUTABLE, root / "_internal"], 15.0)
    old = root / OLD
    shutil.rmtree(old, ignore_errors=True)
    old.mkdir()
    moved: list[str] = []
    copied: list[str] = []
    try:
        for name in shipped:
            if (root / name).exists():
                _move(root / name, old / name)
                moved.append(name)
        for name in shipped:
            source = new / name
            if source.is_dir():
                shutil.copytree(source, root / name)
            else:
                shutil.copy2(source, root / name)
            copied.append(name)
        if not (root / EXECUTABLE).is_file():
            raise OSError("the copied app has no PokeUDS.exe")
    except OSError as error:
        for name in copied:
            _remove(root / name)
        for name in moved:
            try:
                _move(old / name, root / name, 10.0)
            except OSError:
                pass
        record(f"could not replace the app ({error}); the old one is back")
        relaunch(root)
        return False
    record()
    relaunch(root)
    return True


def finish(root: Path | None) -> dict | None:
    """At start, from the installed app: the last update's outcome (once), and its leftovers removed: the replaced
    files and, once the helper has exited, the unpacked release."""
    if root is None:
        return None
    work = work_folder(root)
    outcome = work / "outcome.json"
    try:
        found = json.loads(outcome.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    outcome.unlink(missing_ok=True)
    found = found if isinstance(found, dict) else None
    if found and not found.get("error"):
        shutil.rmtree(root / OLD, ignore_errors=True)
    helper = found.get("helper") if found else None

    def tidy() -> None:
        if isinstance(helper, int):
            wait_for_exit(helper, 30.0)
        end = time.monotonic() + 120.0
        while True:                       # Windows keeps a running exe's folder a moment after it exits
            shutil.rmtree(work / "new", ignore_errors=True)
            if not (work / "new").exists() or time.monotonic() > end:
                return
            time.sleep(1.0)

    threading.Thread(target=tidy, daemon=True, name="update-tidy").start()
    return found
