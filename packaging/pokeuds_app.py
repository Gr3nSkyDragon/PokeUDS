"""The entry point of the packaged app (PokeUDS.exe, scripts/build_exe.py).

With no arguments it is the app. ``PokeUDS.exe --cli <command> ...`` is the command line: the app runs each trade
that way as a child (pokeuds/app/runner.py), since a packaged app has no ``python -m pokeuds`` to call.
"""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "--cli":
    del sys.argv[1]
    # The app reads the child line by line: UTF-8 and a flush per line, whatever the console's code page.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    from pokeuds.cli import main

    sys.exit(main())

from gui.main import run

run()
