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
from pokeuds.app import bank, i18n  # noqa: E402
from gui.app import App  # noqa: E402
from gui.updates import Updates  # noqa: E402
from gui.views.board import BoardView  # noqa: E402
from gui.views.bank import BankView  # noqa: E402
from gui.views.debug import DebugView  # noqa: E402
from gui.views.gifts import GiftsView  # noqa: E402
from gui.views.gts import GtsView  # noqa: E402
from gui.views.plugins import PluginsView  # noqa: E402
from gui.views.settings import SettingsView  # noqa: E402
from gui.views.trade import TradeView  # noqa: E402

PAGES = (TradeView, BankView, GtsView, GiftsView)          # the rail's top
BOTTOM = (PluginsView, BoardView, SettingsView)     # pinned to the rail's foot, Settings last; Debug before it when on


class Shell:
    def __init__(self, page: ft.Page, app: App) -> None:
        self.page, self.app = page, app
        self.views = {cls.key: cls(app) for cls in (*PAGES, *BOTTOM, DebugView)}
        self.current = "trade"
        self.content = ft.Container(expand=True, padding=ft.Padding(0, t.GAP, t.GAP, t.GAP))
        self.updates = Updates(app)
        self.updates.on_available = self._update_rail      # a newer release: the green Update entry appears
        app.updates = self.updates
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
        """Update (green, only while a newer release is out), Plugins, Board, then Debug when Settings turns it on,
        then Settings at the very bottom."""
        return ((["update"] if self.updates.available() else []) + ["plugins", "board"]
                + (["debug"] if self.app.settings.debug else []) + ["settings"])

    def _destination(self, key: str) -> ft.NavigationRailDestination:
        if key == "update":                 # not a page: it offers the update, as pokeldn's does
            return ft.NavigationRailDestination(icon=ft.Icon(ft.Icons.DOWNLOAD, color=t.GOOD),
                                                label=ft.Text(i18n.tr("Update"), color=t.GOOD))
        return ft.NavigationRailDestination(icon=self.views[key].icon, label=i18n.tr(self.views[key].label))

    def _update_rail(self) -> None:
        self.foot.destinations = [self._destination(k) for k in self.foot_keys()]
        self.foot.height = 85 * len(self.foot_keys())
        self._select()
        try:
            self.page.update()
        except (RuntimeError, AssertionError):
            pass

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
        # The update banner sits above every page (gui/updates.py); hidden until there is something to say.
        body = ft.Column([ft.Container(self.updates.banner, padding=ft.Padding(0, t.GAP, t.GAP, 0)), self.content],
                         spacing=0, expand=True)
        return ft.Row([rails, body], spacing=t.GAP, expand=True,
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
        key = self.foot_keys()[e.control.selected_index]
        if key == "update":
            self._select()                  # the page shown stays chosen
            self.page.update()
            self.updates.offer()
            return
        self.navigate(key)

    def _changed(self) -> None:
        self.views[self.current].refresh()


def main(page: ft.Page) -> None:
    page.title = "PokeUDS"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = page.dark_theme = t.app_theme()
    page.bgcolor = t.BG
    page.padding = 0
    app = App(page)
    try:
        bank.Bank(app.settings.bank or None)    # makes the bank folder and tidies an older one (pokeuds/app/bank.py)
    except OSError:
        pass
    i18n.select(app.settings.language)          # a language pack, or English
    if i18n.current().font_family:
        page.theme.font_family = page.dark_theme.font_family = i18n.current().font_family
    shell = Shell(page, app)
    page.add(shell.build())
    shell.views["gts"].settle()               # settles what is live on the GTS, in the background
    if not page.web:
        shell.updates.at_start()              # the last update's outcome; a newer release, in the background
    if not page.web:
        if t.ICON.is_file():
            page.window.icon = str(t.ICON)      # from source too; the packaged exe also carries it
        # The window starts hidden (run) and shows once, in place and drawn: no flashing (gui/window.py).
        window.restore(page, app.settings.window)
        window.remember(page, app.settings)

    def closing(e) -> None:
        app.stop()
    page.on_close = closing


def _report(message: str) -> None:
    """A fatal startup error with no window yet: a Windows message box, else stderr."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "PokeUDS", 0x10)
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def run() -> None:
    from pokeuds.app.settings import AppFolderNotWritable, data_dir

    try:
        data_dir()          # makes Data beside the exe (and adopts older data), or reports a read-only folder
    except AppFolderNotWritable as exc:
        _report(str(exc))
        return
    if port := os.environ.get("POKEUDS_GUI_WEB"):
        # POKEUDS_GUI_HOST=0.0.0.0 also serves other machines (a preview pane, a phone on the LAN).
        ft.run(main, view=ft.AppView.WEB_BROWSER, no_cdn=True, port=int(port), host=os.environ.get("POKEUDS_GUI_HOST") or None)
    else:
        ft.run(main, view=ft.AppView.FLET_APP_HIDDEN)      # gui/window.py shows it once it is in place


if __name__ == "__main__":
    run()
