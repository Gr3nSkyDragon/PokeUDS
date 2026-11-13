"""State the pages share: the page, the settings, the file picker and the one run a radio allows."""

from __future__ import annotations

from typing import Callable

import flet as ft

from pokeuds.app.progress import Progress
from pokeuds.app.runner import Run
from pokeuds.app.settings import Settings


LOGS_KEPT = 30                 # run logs kept in <data>/logs, newest first


def logs_folder():
    """``<data>/logs`` (beside the packaged app: Data/logs), or None when there is no data folder."""
    from pathlib import Path

    from pokeuds.app.settings import data_dir

    try:
        return Path(data_dir()) / "logs"
    except Exception:                            # AppFolderNotWritable and the like: no logs, but the run goes on
        return None


def new_log(kind: str, args: list[str]):
    """An empty file in logs_folder() named for this run; the oldest beyond LOGS_KEPT go. -> the path, or None."""
    import time

    folder = logs_folder()
    if folder is None:
        return None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        what = next((a for a in args if not a.startswith("-")), kind)
        if len(args) > 1 and args[0] in ("trade", "vc", "gift") and not args[1].startswith("-"):
            what = f"{args[0]}-{args[1]}"
        now = time.time()
        path = folder / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{int(now * 1000) % 1000:03d}_{kind}_{what}.txt"
        path.write_text("", encoding="utf-8")
        for old in sorted(folder.glob("*.txt"), reverse=True)[LOGS_KEPT:]:
            old.unlink(missing_ok=True)
        return path
    except OSError:
        return None


def save_log(kind: str, args: list[str], lines: list[str]):
    """Write a run's log to ``<data>/logs``, so a hardware test can be looked at after the app is closed.
    -> the file, or None when it cannot be written."""
    path = new_log(kind, args)
    if path is None:
        return None
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
    except OSError:
        return None


class RunLog:
    """A run's log file, written a line at a time as the run goes: it holds every line (the page shows only the last
    ones) and is on disk even if the app is closed or the run is killed midway."""

    def __init__(self, kind: str, args: list[str]) -> None:
        import threading

        self._lock = threading.Lock()
        self.path = new_log(kind, args)
        self._file = None
        if self.path is not None:
            try:
                self._file = self.path.open("a", encoding="utf-8", newline="\n")
            except OSError:
                self.path = None

    def write(self, text: str) -> None:
        with self._lock:
            if self._file is None:
                return
            try:
                self._file.write(text + "\n")
                self._file.flush()
            except (OSError, ValueError):
                self._file = None

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                except OSError:
                    pass
                self._file = None


class App:
    def __init__(self, page: ft.Page | None, settings: Settings | None = None) -> None:
        self.page = page
        self.settings = settings or Settings.load()
        from pokeuds.app import sprites

        # Sprites download only in the running app (never in a test's page-less App), and when Settings allow it.
        sprites.CACHE.online = page is not None and self.settings.sprites
        self.picker = ft.FilePicker() if page is not None else None
        self.clipboard = ft.Clipboard() if page is not None else None
        if page is not None:                     # dialogs are translated too (gui/translate.py)
            from .translate import translate

            show = page.show_dialog
            page.show_dialog = lambda dialog: show(translate(dialog))
        self.run: Run | None = None
        self.run_kind = ""                       # "trade", "probe", "scan", "analyze", "board", "gift"
        self.progress = Progress()
        self.log: list[str] = []
        self.saved_log = None                    # the current or last run's log file (RunLog), every line of it
        self.listeners: list[Callable[[], None]] = []
        self.navigate: Callable[[str], None] = lambda key: None
        self.views: dict = {}                    # key -> view, set by the shell
        self.bank_for = ""                       # a game key while the Bank page picks an offer for it

    # -- threads --------------------------------------------------------------------------------

    def ui(self, fn: Callable[[], None]) -> None:
        """Run fn on the page's event loop: controls must not be updated from worker threads."""
        if self.page is None:
            fn()
            return

        async def call() -> None:
            try:
                fn()
            except RuntimeError as error:   # a view that was replaced while the worker ran
                if "must be added to the page" not in str(error):
                    raise
        self.page.run_task(call)

    def changed(self) -> None:
        for listener in list(self.listeners):
            listener()

    def save(self) -> None:
        self.settings.save()

    # -- the run --------------------------------------------------------------------------------

    def busy(self) -> bool:
        return self.run is not None and self.run.running()

    def start(self, kind: str, args: list[str], on_line: Callable[[str], None] | None = None,
              on_event: Callable[[dict], None] | None = None, on_exit: Callable[[int], None] | None = None,
              timeout: float | None = None, timeout_note: str = "") -> None:
        """Run ``pokeuds ARGS`` as the one child. With ``timeout`` (seconds) a run still going then is stopped, and
        ``timeout_note`` (or a plain "gave up") is logged as its last word, so a check always ends with an answer."""
        import threading

        if self.busy():
            raise RuntimeError("a run is already going")
        self.run_kind = kind
        self.log = [f"$ pokeuds {' '.join(args)}"]
        file = RunLog(kind, args)
        file.write(self.log[0])
        self.saved_log = file.path
        if kind == "trade":
            self.progress.start()

        def line(text: str) -> None:
            file.write(text)
            self.log.append(text)
            del self.log[:-2000]
            if on_line:
                on_line(text)
            self.ui(self.changed)

        def event(ev: dict) -> None:
            if kind == "trade":
                self.progress.take(ev)
            if on_event:
                on_event(ev)
            self.ui(self.changed)

        expired = threading.Event()
        timer: list[threading.Timer] = []

        def exited(code: int) -> None:
            for t in timer:
                t.cancel()
            if kind == "trade":
                self.progress.exited(code)
            if expired.is_set():                 # its last line, after anything it said while being stopped
                self.log.append(timeout_note or f"Gave up after {timeout:g} s without an answer.")
                file.write(self.log[-1])
            self.log.append(f"(exited with code {code})")
            file.write(self.log[-1])
            file.close()
            if on_exit:
                on_exit(code)
            self.ui(self.changed)

        self.run = run = Run(args, on_line=line, on_event=event, on_exit=exited)
        if timeout:
            def expire() -> None:
                if run.running():
                    expired.set()
                    run.stop()                   # stdin closed, then terminate, then kill: it does end
            timer.append(threading.Timer(timeout, expire))
            timer[0].daemon = True
            timer[0].start()
        self.changed()

    def stop(self) -> None:
        if self.run:
            self.run.stop()
