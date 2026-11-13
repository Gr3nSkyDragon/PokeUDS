"""Pieces several views share."""

from __future__ import annotations

import os
import subprocess
import sys

import flet as ft

from .. import theme as t
from ..translate import translate


def awaiting(coroutine_function, *args):
    """An event handler that awaits ``coroutine_function(*args)``. Flet awaits a handler only when it is itself async:
    ``lambda e: self._save(x)`` would make the coroutine and drop it, and the button would do nothing."""
    async def handler(e=None) -> None:
        await coroutine_function(*args)
    return handler


class View:
    """A page: ``build`` returns its controls; ``refresh`` rebuilds them in place."""

    key = ""
    label = ""
    icon = ft.Icons.CIRCLE

    def __init__(self, app) -> None:
        self.app = app
        self.root = ft.Container(expand=True)

    def control(self) -> ft.Container:
        self.root.content = translate(self.build())
        return self.root

    def build(self) -> ft.Control:
        raise NotImplementedError

    def refresh(self) -> None:
        self.root.content = translate(self.build())
        try:
            self.root.page                  # raises when the view is not on the page (Flet 1.0)
        except RuntimeError:
            return
        self.root.update()

    def save(self) -> None:
        self.app.save()
        self.refresh()


def full_log(app) -> str:
    """The whole log of the current or last run: its file (every line), else the lines the page keeps."""
    path = getattr(app, "saved_log", None)
    if path is not None:
        try:
            text = path.read_text(encoding="utf-8")
            if text.strip():
                return text
        except OSError:
            pass
    return "\n".join(app.log) + ("\n" if app.log else "")


def log_box(lines: list[str], height: int = 220, last: int = 300, app=None) -> ft.Control:
    shown = lines[-last:] or ["(nothing yet)"]
    box = ft.Container(
        ft.ListView([ft.Text(line, size=12, font_family="monospace", color=t.MUTED, selectable=True) for line in shown],
                    spacing=1, auto_scroll=True, expand=True),
        height=height, bgcolor=t.BG, border_radius=8, padding=10, border=ft.Border.all(1, t.LINE))
    if app is None:
        return box
    copy = ft.TextButton("Copy log", icon=ft.Icons.CONTENT_COPY, disabled=not app.log)

    async def copy_log(e=None) -> None:
        if app.clipboard is None:
            return
        await app.clipboard.set(full_log(app))
        copy.content = "Copied"
        translate(copy)
        copy.update()

    copy.on_click = copy_log

    def open_logs(e=None) -> None:
        from ..app import logs_folder

        folder = logs_folder()
        if folder is not None:
            open_folder(str(folder))

    saved = getattr(app, "saved_log", None)
    where = ft.Text(f"Saved as {saved.name}" if saved is not None else "This log could not be saved to a file",
                    size=11, color=t.MUTED, selectable=True, expand=True) if app.log else ft.Container(expand=True)
    tools = ft.Row([where, copy, ft.TextButton("Open logs folder", icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                                               on_click=open_logs)],
                   spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    return ft.Column([tools, box], spacing=4)


def open_folder(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - the user's own output folder
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
