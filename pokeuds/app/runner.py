"""Runs the command line as a child process for the app, one at a time.

The app never opens the radio itself: a trade is ``python -m pokeuds ...`` in a child, which the app
reads line by line (``pokeuds.status`` events and the plain log). Stop closes the child's stdin, which
the child turns into a KeyboardInterrupt and a clean shutdown; a child that does not end in a few
seconds is terminated, then killed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Callable

from .. import status
from ..util import NO_WINDOW

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def command(*args: str) -> list[str]:
    if getattr(sys, "frozen", False):          # the packaged app: its own exe is the command line (packaging/)
        return [sys.executable, "--cli", *args]
    return [sys.executable, "-u", "-m", "pokeuds", *args]


class Run:
    def __init__(self, args: list[str], *, on_line: Callable[[str], None], on_event: Callable[[dict], None],
                 on_exit: Callable[[int], None], env: dict | None = None) -> None:
        child_env = {**os.environ, **(env or {}), "POKEUDS_STATUS": "1", "POKEUDS_MANAGED": "1",
                     "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        self.args = args
        self.proc = subprocess.Popen(command(*args), cwd=ROOT, env=child_env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace", bufsize=1, **NO_WINDOW)
        self.on_line, self.on_event, self.on_exit = on_line, on_event, on_exit
        self.lines: list[str] = []
        self._reader = threading.Thread(target=self._pump, daemon=True, name="pokeuds-child")
        self._reader.start()

    def running(self) -> bool:
        return self.proc.poll() is None

    def _pump(self) -> None:
        for raw in self.proc.stdout:
            line = raw.rstrip("\n")
            self.lines.append(line)
            event = status.parse(line)
            if event is not None:
                self.on_event(event)
            else:
                self.on_line(line)
        self.on_exit(self.proc.wait())

    def stop(self, grace: float = 4.0) -> None:
        if not self.running():
            return
        try:
            self.proc.stdin.close()
        except OSError:
            pass

        def escalate() -> None:
            try:
                self.proc.wait(grace)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(2.0)
                except subprocess.TimeoutExpired:
                    self.proc.kill()

        threading.Thread(target=escalate, daemon=True).start()

    def wait(self, timeout: float | None = None) -> int:
        code = self.proc.wait(timeout)
        self._reader.join(timeout=2.0)
        return code
