"""The PokeUDS app: ``python -m gui``.

``POKEUDS_GUI_WEB=<port>`` serves it to a browser instead of opening a window (for screenshots);
``POKEUDS_GUI_HOST`` chooses the address it listens on (default: this machine only).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flet as ft  # noqa: E402

from gui import theme as t  # noqa: E402
from gui.app import App  # noqa: E402
from gui.views.board import BoardView  # noqa: E402
from gui.views.builder import BuilderView  # noqa: E402
from gui.views.bank import BankView  # noqa: E402
from gui.views.debug import DebugView  # noqa: E402
from gui.views.gifts import GiftsView  # noqa: E402
from gui.views.radio import RadioView  # noqa: E402
from gui.views.settings import SettingsView  # noqa: E402
from gui.views.trade import TradeView  # noqa: E402

PAGES = (TradeView, BankView, BuilderView, GiftsView, RadioView, BoardView, SettingsView)


class Shell:
    def __init__(self, page: ft.Page, app: App) -> None:
        self.page, self.app = page, app
        self.views = {cls.key: cls(app) for cls in (*PAGES, DebugView)}
        self.current = "trade"
        self.content = ft.Container(expand=True, padding=ft.Padding(0, t.GAP, t.GAP, t.GAP))
        self.rail = ft.NavigationRail(on_change=self._rail, bgcolor=t.PANEL, min_width=84,
                                      label_type=ft.NavigationRailLabelType.ALL,
                                      leading=ft.Container(t.mark(), padding=ft.Padding(0, 18, 0, 18)))
        app.navigate = self.navigate
        app.views = self.views
        app.rebuild_shell = self.rebuild
        app.listeners.append(self._changed)

    def keys(self) -> list[str]:
        return [cls.key for cls in PAGES] + (["debug"] if self.app.settings.debug else [])

    def build(self) -> ft.Control:
        self.rail.destinations = [ft.NavigationRailDestination(icon=self.views[k].icon, label=self.views[k].label)
                                  for k in self.keys()]
        if self.current not in self.keys():
            self.current = "trade"
        self.rail.selected_index = self.keys().index(self.current)
        self.content.content = self.views[self.current].control()
        return ft.Row([ft.Container(self.rail, bgcolor=t.PANEL), self.content], spacing=t.GAP, expand=True,
                      vertical_alignment=ft.CrossAxisAlignment.STRETCH)

    def rebuild(self) -> None:
        self.page.controls = [self.build()]
        self.page.update()

    def navigate(self, key: str) -> None:
        self.current = key
        self.rail.selected_index = self.keys().index(key)
        self.content.content = self.views[key].control()
        self.page.update()

    def _rail(self, e) -> None:
        self.navigate(self.keys()[e.control.selected_index])

    def _changed(self) -> None:
        self.views[self.current].refresh()


def main(page: ft.Page) -> None:
    page.title = "PokeUDS"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = page.dark_theme = t.app_theme()
    page.bgcolor = t.BG
    page.padding = 0
    if not page.web:
        page.window.min_width, page.window.min_height = 900, 600
        page.window.width, page.window.height = 1280, 820
    app = App(page)
    shell = Shell(page, app)
    page.add(shell.build())

    def closing(e) -> None:
        app.stop()
    page.on_close = closing


def run() -> None:
    if port := os.environ.get("POKEUDS_GUI_WEB"):
        # POKEUDS_GUI_HOST=0.0.0.0 also serves other machines (a preview pane, a phone on the LAN).
        ft.run(main, view=ft.AppView.WEB_BROWSER, no_cdn=True, port=int(port), host=os.environ.get("POKEUDS_GUI_HOST") or None)
    else:
        ft.run(main)


if __name__ == "__main__":
    run()
