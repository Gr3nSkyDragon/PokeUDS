"""The desktop shortcut: a banner above every page offers it once, and Settings keeps a button for it (docs/gui.md,
The launcher).

Players drag PokeUDS.exe out of its folder to have it on the desktop. The launcher copes, but a shortcut is what they
wanted: the banner offers one, and once used or closed stays closed (Settings, ``shortcut_banner``). It shows only in
the packaged Windows app behind its launcher, and only while the desktop has no ``PokeUDS.lnk``.
"""

from __future__ import annotations

import threading
from typing import Callable

import flet as ft

from pokeuds.app import home, i18n

from . import theme as t
from .translate import translate


def available() -> bool:
    """A shortcut can be made: the packaged Windows app, started through its launcher's folder."""
    return home.behind_launcher()


def wanted(app) -> bool:
    return bool(app.settings.shortcut_banner) and available() and not home.has_shortcut()


def create(app, done: Callable[[str], None]) -> None:
    """Make the shortcut in the background; ``done`` gets "" or what went wrong, on the page's loop."""
    def work() -> None:
        try:
            home.make_shortcut(home.install_folder())
            error = ""
        except OSError as exc:
            error = str(exc) or type(exc).__name__
        app.ui(lambda: done(error))

    threading.Thread(target=work, daemon=True, name="shortcut").start()


def banner(app) -> ft.Container:
    """The banner, or an empty, hidden container once closed, used, or not needed."""
    box = ft.Container(visible=wanted(app), padding=ft.Padding(0, t.GAP, t.GAP, 0))
    if not box.visible:
        return box

    def settle() -> None:
        app.settings.shortcut_banner = False
        app.save()

    def close(_) -> None:
        settle()
        box.visible = False
        box.update()

    status = ft.Text("", color=t.BAD, size=12, visible=False)
    make = t.primary("Create desktop shortcut", lambda e: start(), ft.Icons.ADD_TO_HOME_SCREEN)

    def finished(error: str) -> None:
        make.disabled = False
        if error:
            status.value = i18n.tr(f"Could not create the shortcut: {error}")
            status.visible = True
            box.update()
            return
        settle()
        box.content = _row(ft.Icons.CHECK_CIRCLE, t.GOOD, [ft.Text(i18n.tr("Shortcut created: PokeUDS is on your "
                                                                           "desktop."), color=t.TEXT, size=13,
                                                                   expand=True)],
                           ft.IconButton(ft.Icons.CLOSE, icon_size=18, tooltip=i18n.tr("Hide"), on_click=close))
        box.update()

    def start() -> None:
        make.disabled = True
        status.visible = False
        box.update()
        create(app, finished)

    box.content = _row(ft.Icons.DESKTOP_WINDOWS, t.INFO,
                       [ft.Column([ft.Text("Start PokeUDS from your desktop? A shortcut opens it while PokeUDS.exe "
                                           "stays in its folder, where it belongs.", color=t.TEXT, size=13),
                                   status], spacing=2, expand=True),
                        make],
                       ft.IconButton(ft.Icons.CLOSE, icon_size=18, tooltip="Hide", on_click=close))
    return translate(box)


def _row(icon, color: str, middle: list[ft.Control], close: ft.Control) -> ft.Control:
    return ft.Container(ft.Row([ft.Icon(icon, color=color, size=20), *middle, close], spacing=12,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor=t.RAISED, border_radius=10, padding=ft.Padding(12, 4, 8, 4))
