"""Machine-readable progress for the GUI.

When ``POKEUDS_STATUS`` is set (the app sets it for the runs it starts), the command line also
prints one line per event:

    [status] {"event": "joined", "role": "client", "channel": 6}

The plain log and the ``[done] trade N complete: <path>`` lines are unchanged, so a terminal user
sees nothing new. Events: ``radio``, ``searching``, ``found``, ``hosting``, ``joined``,
``partner-joined``, ``session``, ``phase``, ``note``, ``received``, ``stopped``, ``error``.
"""

from __future__ import annotations

import json
import os
import sys

PREFIX = "[status] "


def enabled() -> bool:
    return bool(os.environ.get("POKEUDS_STATUS"))


def emit(event: str, **fields) -> None:
    if not enabled():
        return
    line = PREFIX + json.dumps({"event": event, **fields}, ensure_ascii=False, default=str)
    print(line, flush=True)


def parse(line: str) -> dict | None:
    """The event in a ``[status]`` line, or None for any other line."""
    if not line.startswith(PREFIX):
        return None
    try:
        event = json.loads(line[len(PREFIX):])
    except ValueError:
        return None
    return event if isinstance(event, dict) and "event" in event else None


def watch_stdin_for_stop() -> None:
    """A run the app manages ends when the app closes its stdin: raise KeyboardInterrupt in the main
    thread, as Ctrl+C would (a windowless child on Windows cannot be sent a signal)."""
    import _thread
    import threading

    def wait() -> None:
        try:
            sys.stdin.read()
        except Exception:
            pass
        _thread.interrupt_main()

    threading.Thread(target=wait, daemon=True, name="stop-on-stdin-close").start()
