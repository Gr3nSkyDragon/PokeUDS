"""The update banner above every page (pokeuds/app/update.py, docs/gui.md, Updates).

At start, unless Settings turns it off, the app asks GitHub for a newer release in the background. One shows as a
banner: Update now (the packaged Windows app: download, check, restart into the new version, the player's Bank,
Data and received folders untouched), What's new (the release page), Not now. The Mac app and a source run get
Download instead. After an update the banner says how it went, once.
"""

from __future__ import annotations

import threading
import webbrowser

import flet as ft

from pokeuds.app import i18n
from pokeuds.app import update as updating

from . import theme as t


class Updates:
    def __init__(self, app) -> None:
        self.app = app
        self.root = updating.install_root()
        self.banner = ft.Container(visible=False, padding=ft.Padding(0, 0, 0, 8))
        self.release: updating.Release | None = None
        self.state = ""                  # "", "available", "downloading", "restarting", "error", "done", "current"
        self.detail = ""
        self.progress = (0, 0)
        self.cancel = False

    # -- checking -------------------------------------------------------------------------------

    def at_start(self) -> None:
        """The last update's outcome, once; then a check in the background, when Settings allows."""
        outcome = updating.finish(self.root)
        if outcome:
            error = outcome.get("error")
            self._show("error" if error else "done",
                       f"The update to PokeUDS {outcome.get('version')} did not finish: {error}" if error
                       else f"Updated to PokeUDS {outcome.get('version')}.")
        if self.app.settings.updates:
            self.check(quiet=True)

    def check(self, quiet: bool = False) -> None:
        """Ask GitHub in the background; ``quiet`` says nothing unless there is a newer release."""
        def work() -> None:
            try:
                release = updating.check()
            except OSError as exc:
                if not quiet:
                    self.app.ui(lambda: self._show("error", f"GitHub did not answer: {exc}"))
                return
            if release is None:
                if not quiet:
                    self.app.ui(lambda: self._show("current", "PokeUDS is up to date."))
                return
            self.release = release
            self.app.ui(lambda: self._show("available"))

        threading.Thread(target=work, daemon=True, name="update-check").start()

    # -- the banner -----------------------------------------------------------------------------

    def _show(self, state: str, detail: str = "") -> None:
        self.state, self.detail = state, detail
        self.banner.content = self._content()
        self.banner.visible = self.banner.content is not None
        try:
            self.banner.update()
        except (RuntimeError, AssertionError):  # not on a page yet: drawn with it
            pass

    def _content(self) -> ft.Control | None:
        r = self.release
        close = ft.IconButton(ft.Icons.CLOSE, icon_size=18, tooltip=i18n.tr("Not now"), on_click=lambda e: self._show(""))
        if self.state == "available" and r is not None:
            blocked = updating.blocker(self.root)
            actions: list[ft.Control] = []
            if not blocked and r.installable:
                actions.append(t.primary(i18n.tr("Update now"), lambda e: self._confirm(), ft.Icons.SYSTEM_UPDATE_ALT))
            else:
                actions.append(t.primary(i18n.tr("Download"), lambda e: webbrowser.open(r.page), ft.Icons.OPEN_IN_NEW))
            actions.append(t.secondary(i18n.tr("What's new"), lambda e: webbrowser.open(r.page), ft.Icons.ARTICLE))
            text = i18n.tr(f"PokeUDS {r.version} is out (you have {updating.__version__}).")
            return self._row(ft.Icons.NEW_RELEASES, t.ACCENT, text, actions + [close])
        if self.state == "downloading" and r is not None:
            done, total = self.progress
            bar = ft.ProgressBar(value=done / total if total else None, width=220, color=t.ACCENT, bgcolor=t.LINE)
            text = i18n.tr(f"Downloading PokeUDS {r.version}: {done >> 20} of {total >> 20} MB" if total
                           else f"Downloading PokeUDS {r.version}...")
            return self._row(ft.Icons.DOWNLOAD, t.INFO, text,
                             [bar, t.secondary(i18n.tr("Cancel"), lambda e: self._cancel(), ft.Icons.CLOSE)])
        if self.state == "restarting":
            return self._row(ft.Icons.RESTART_ALT, t.INFO, i18n.tr("Restarting into the new version..."), [])
        if self.state in ("error", "done", "current"):
            color = {"error": t.BAD, "done": t.GOOD, "current": t.GOOD}[self.state]
            return self._row(ft.Icons.INFO_OUTLINE, color, self.detail, [close])
        return None

    @staticmethod
    def _row(icon, color: str, text: str, actions: list[ft.Control]) -> ft.Control:
        return ft.Container(ft.Row([ft.Icon(icon, color=color, size=20),
                                    ft.Text(text, color=t.TEXT, size=13, expand=True), *actions],
                                   spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            bgcolor=t.RAISED, border_radius=10, padding=ft.Padding(12, 8, 12, 8))

    # -- updating -------------------------------------------------------------------------------

    def _confirm(self) -> None:
        page = self.app.page
        if self.app.busy():
            self._show("error", i18n.tr("Finish or stop the run under way first, then update."))
            return

        def close(_=None) -> None:
            page.pop_dialog()

        def go(_) -> None:
            page.pop_dialog()
            self._install()

        page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Update to PokeUDS {self.release.version}?"),
            content=ft.Text("PokeUDS downloads the new version, checks it, closes, replaces its own program files "
                            "(PokeUDS.exe and _internal) and opens again. Your Bank (and your GTS listings in it), "
                            "your settings in Data and the received folder are not touched.", width=460),
            actions=[ft.TextButton("Not now", on_click=close), ft.FilledButton("Update now", on_click=go)]))

    def _cancel(self) -> None:
        self.cancel = True

    def _install(self) -> None:
        release, root = self.release, self.root
        self.cancel, self.progress = False, (0, 0)
        self._show("downloading")
        last = [0]

        def progress(done: int, total: int) -> None:
            self.progress = (done, total)
            if done - last[0] > (1 << 20) or done == total:       # a redraw per megabyte
                last[0] = done
                self.app.ui(lambda: self._show("downloading"))

        def work() -> None:
            try:
                new = updating.prepare(release, root, progress, lambda: self.cancel)
                updating.start_swap(new, root, release.version)
            except updating.Cancelled:
                self.app.ui(lambda: self._show("available"))
                return
            except OSError as exc:
                self.app.ui(lambda: self._show("error", i18n.tr(f"The update failed: {exc}")))
                return
            self.app.ui(self._restart)

        threading.Thread(target=work, daemon=True, name="update").start()

    def _restart(self) -> None:
        """The helper waits for this app to quit: stop any run and close."""
        self._show("restarting")
        self.app.stop()

        async def close() -> None:
            await self.app.page.window.destroy()

        try:
            self.app.page.run_task(close)
        except Exception:
            import os

            os._exit(0)
