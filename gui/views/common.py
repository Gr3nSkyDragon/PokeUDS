"""Pieces several views share."""

from __future__ import annotations

import os
import subprocess
import sys

import flet as ft

from .. import theme as t


class View:
    """A page: ``build`` returns its controls; ``refresh`` rebuilds them in place."""

    key = ""
    label = ""
    icon = ft.Icons.CIRCLE

    def __init__(self, app) -> None:
        self.app = app
        self.root = ft.Container(expand=True)

    def control(self) -> ft.Container:
        self.root.content = self.build()
        return self.root

    def build(self) -> ft.Control:
        raise NotImplementedError

    def refresh(self) -> None:
        self.root.content = self.build()
        try:
            self.root.page                  # raises when the view is not on the page (Flet 1.0)
        except RuntimeError:
            return
        self.root.update()

    def save(self) -> None:
        self.app.save()
        self.refresh()


def log_box(lines: list[str], height: int = 220, last: int = 300) -> ft.Container:
    shown = lines[-last:] or ["(nothing yet)"]
    return ft.Container(
        ft.ListView([ft.Text(line, size=12, font_family="monospace", color=t.MUTED, selectable=True) for line in shown],
                    spacing=1, auto_scroll=True, expand=True),
        height=height, bgcolor=t.BG, border_radius=8, padding=10, border=ft.Border.all(1, t.LINE))


def open_folder(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - the user's own output folder
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
