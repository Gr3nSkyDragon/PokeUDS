"""State the pages share: the page, the settings, the file picker and the one run a radio allows."""

from __future__ import annotations

from typing import Callable

import flet as ft

from pokeuds.app.progress import Progress
from pokeuds.app.runner import Run
from pokeuds.app.settings import Settings


class App:
    def __init__(self, page: ft.Page | None, settings: Settings | None = None) -> None:
        self.page = page
        self.settings = settings or Settings.load()
        self.picker = ft.FilePicker() if page is not None else None
        self.run: Run | None = None
        self.run_kind = ""                       # "trade", "probe", "scan", "analyze", "board"
        self.progress = Progress()
        self.log: list[str] = []
        self.listeners: list[Callable[[], None]] = []
        self.navigate: Callable[[str], None] = lambda key: None

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
              on_event: Callable[[dict], None] | None = None, on_exit: Callable[[int], None] | None = None) -> None:
        if self.busy():
            raise RuntimeError("a run is already going")
        self.run_kind = kind
        self.log = [f"$ pokeuds {' '.join(args)}"]
        if kind == "trade":
            self.progress.start()

        def line(text: str) -> None:
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

        def exited(code: int) -> None:
            if kind == "trade":
                self.progress.exited(code)
            self.log.append(f"(exited with code {code})")
            if on_exit:
                on_exit(code)
            self.ui(self.changed)

        self.run = Run(args, on_line=line, on_event=event, on_exit=exited)
        self.changed()

    def stop(self) -> None:
        if self.run:
            self.run.stop()
