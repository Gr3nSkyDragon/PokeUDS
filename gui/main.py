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
from gui import window  # noqa: E402
from gui.translate import translate  # noqa: E402
from pokeuds.app import i18n  # noqa: E402
from gui.app import App  # noqa: E402
from gui.views.board import BoardView  # noqa: E402
from gui.views.bank import BankView  # noqa: E402
from gui.views.debug import DebugView  # noqa: E402
from gui.views.gifts import GiftsView  # noqa: E402
from gui.views.plugins import PluginsView  # noqa: E402
from gui.views.settings import SettingsView  # noqa: E402
from gui.views.trade import TradeView  # noqa: E402

PAGES = (TradeView, BankView, GiftsView)          # the rail's top
BOTTOM = (PluginsView, BoardView, SettingsView)     # pinned to the rail's foot, Settings last; Debug before it when on


class Shell:
    def __init__(self, page: ft.Page, app: App) -> None:
        self.page, self.app = page, app
        self.views = {cls.key: cls(app) for cls in (*PAGES, *BOTTOM, DebugView)}
        self.current = "trade"
        self.content = ft.Container(expand=True, padding=ft.Padding(0, t.GAP, t.GAP, t.GAP))
        self.rail = ft.NavigationRail(on_change=self._rail, bgcolor=t.PANEL, min_width=84, expand=True,
                                      label_type=ft.NavigationRailLabelType.ALL,
                                      leading=ft.Container(t.mark(), padding=ft.Padding(0, 18, 0, 18)))
        self.foot = ft.NavigationRail(on_change=self._foot, bgcolor=t.PANEL, min_width=84,
                                      label_type=ft.NavigationRailLabelType.ALL)
        app.navigate = self.navigate
        app.rebuild_shell = self.rebuild
        app.views = self.views
        app.listeners.append(self._changed)

    def keys(self) -> list[str]:
        return [cls.key for cls in PAGES]

    def foot_keys(self) -> list[str]:
        """Plugins, Board, then Debug when Settings turns it on, then Settings at the very bottom."""
        return ["plugins", "board"] + (["debug"] if self.app.settings.debug else []) + ["settings"]

    def _destination(self, key: str) -> ft.NavigationRailDestination:
        return ft.NavigationRailDestination(icon=self.views[key].icon, label=i18n.tr(self.views[key].label))

    def _select(self) -> None:
        """Mark the current page on whichever rail holds it; the other shows nothing chosen."""
        top, foot = self.keys(), self.foot_keys()
        self.rail.selected_index = top.index(self.current) if self.current in top else None
        self.foot.selected_index = foot.index(self.current) if self.current in foot else None

    def build(self) -> ft.Control:
        self.rail.destinations = [self._destination(k) for k in self.keys()]
        self.foot.destinations = [self._destination(k) for k in self.foot_keys()]
        self.foot.height = 85 * len(self.foot_keys())
        if self.current not in self.keys() + self.foot_keys():
            self.current = "trade"
        self._select()
        self.content.content = self.views[self.current].control()
        rails = ft.Container(ft.Column([self.rail, self.foot], spacing=0), bgcolor=t.PANEL,
                             padding=ft.Padding(0, 0, 0, 12))
        return ft.Row([rails, self.content], spacing=t.GAP, expand=True,
                      vertical_alignment=ft.CrossAxisAlignment.STRETCH)

    def rebuild(self) -> None:
        self.page.controls = [self.build()]
        self.page.update()

    def navigate(self, key: str) -> None:
        self.current = key
        self._select()
        self.content.content = self.views[key].control()
        self.page.update()

    def _rail(self, e) -> None:
        self.navigate(self.keys()[e.control.selected_index])

    def _foot(self, e) -> None:
        self.navigate(self.foot_keys()[e.control.selected_index])

    def _changed(self) -> None:
        self.views[self.current].refresh()


def main(page: ft.Page) -> None:
    page.title = "PokeUDS"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = page.dark_theme = t.app_theme()
    page.bgcolor = t.BG
    page.padding = 0
    app = App(page)
    if not page.web:
        window.restore(page, app.settings.window)
        window.remember(page, app.settings)
    i18n.select(app.settings.language)          # a language pack, or English
    if i18n.current().font_family:
        page.theme.font_family = page.dark_theme.font_family = i18n.current().font_family
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
