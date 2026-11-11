"""The entry point of the packaged app (PokeUDS.exe, scripts/build_exe.py).

With no arguments it is the app, run in the window unpacked beside it (``_internal/flet_view``).
``PokeUDS.exe --cli <command> ...`` is the command line: the app runs each trade that way as a child
(pokeuds/app/runner.py), since a packaged app has no ``python -m pokeuds`` to call.
"""

import os
import sys
import tempfile
from pathlib import Path

INTERNAL = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def keep_temp_out_of_sight() -> None:
    """Python falls back to the current folder (the app's, when PokeUDS.exe is double-clicked) for its temporary
    files when %TEMP% cannot be written, and Flet keeps the window's process id in one while the app runs: a file
    with a random name in the app's folder. Use a folder inside _internal for them instead."""
    here = {Path.cwd().resolve(), Path(sys.executable).resolve().parent}
    if Path(tempfile.gettempdir()).resolve() in here:
        private = INTERNAL / "temp"
        try:
            private.mkdir(exist_ok=True)
        except OSError:
            return
        tempfile.tempdir = str(private)


keep_temp_out_of_sight()

if len(sys.argv) > 1 and sys.argv[1] == "--apply-update":
    # The new version, unpacked in the old one's Data/update/new, replacing the old one's program files once it has
    # quit (pokeuds/app/update.py). Nothing else is loaded: no window, no settings, no bank.
    from pokeuds.app import update

    new, root, pid, version = sys.argv[2:6]
    # POKEUDS_UPDATE_NO_RELAUNCH: a test of the packaged helper swaps without opening the app afterwards.
    relaunch = (lambda folder: None) if os.environ.get("POKEUDS_UPDATE_NO_RELAUNCH") else None
    sys.exit(0 if update.apply(Path(new), Path(root), int(pid), version, relaunch=relaunch, helper=os.getpid()) else 1)

if len(sys.argv) > 1 and sys.argv[1] == "--cli":
    del sys.argv[1]
    # The app reads the child line by line: UTF-8 and a flush per line, whatever the console's code page.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    from pokeuds.app.settings import AppFolderNotWritable
    from pokeuds.cli import main

    try:
        sys.exit(main())
    except AppFolderNotWritable as exc:
        print(f"PokeUDS: {exc}", file=sys.stderr)
        sys.exit(2)

# The window ships unpacked beside the app (scripts/build_exe.py), so Flet runs it from here instead of unpacking
# a copy into %USERPROFILE%\.flet on first start: nothing to unpack, nothing written outside the app's folder.
view = INTERNAL / "flet_view" / "flet"
if (view / "flet.exe").is_file():
    os.environ["FLET_VIEW_PATH"] = str(view)

from gui.main import run  # noqa: E402

run()
