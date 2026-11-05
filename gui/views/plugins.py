"""The Plugins page (above Board): installed plugins, adding one from a file or a source, and the install screen.

As pokeldn's: a card per plugin turns it on and off, updates it from a file, shows its folder and removes it
(optionally with its saved data); every install and update shows the risk screen first (docs/plugins.md).
"""

from __future__ import annotations

import threading
from pathlib import Path

import flet as ft

from pokeuds import plugins
from pokeuds.app import plugin_store as store

from .. import theme as t
from .common import View, open_folder

WARN_TRUST = ("changed", "unsigned-after-signed")


class PluginsView(View):
    key, label, icon = "plugins", "Plugins", ft.Icons.EXTENSION_OUTLINED

    def __init__(self, app) -> None:
        super().__init__(app)
        self.message, self.message_color = "", t.INFO
        self.listings: list | None = None
        self.browsing = False
        self.source_text = ""

    # -- layout ---------------------------------------------------------------------------------

    def build(self) -> ft.Control:
        reg = plugins.registry()
        controls: list[ft.Control] = [
            ft.Row([t.title("Plugins"), ft.Container(expand=True),
                    t.primary("Add a plugin...", self._add, ft.Icons.ADD),
                    t.secondary("Open the folder", lambda e: open_folder(str(plugins.plugins_dir())), ft.Icons.FOLDER_OPEN)],
                   spacing=8),
            t.notice("A plugin runs as Python inside PokeUDS with full access to your files, console keys, the network "
                     "and the radio. Install only plugins you trust; PokeUDS does not review them. The install screen "
                     "says what the code does, and guardrails stop it deleting files outside its own folders, but "
                     "they are not a sandbox.", t.WARN, ft.Icons.SHIELD_OUTLINED),
        ]
        if self.message:
            controls.append(t.notice(self.message, self.message_color))
        cards = [self._card(p) for p in reg.plugins]
        controls.append(t.card(t.heading("Installed"), *(cards or [t.label(
            f"No plugins yet. Add a .udsplugin file, or browse a source below. Plugins live in {plugins.plugins_dir()}.")]),
            spacing=12))
        controls.append(self._sources())
        return ft.Column(controls, spacing=t.GAP, scroll=ft.ScrollMode.AUTO)

    def _card(self, p: plugins.Plugin) -> ft.Control:
        if p.error and not (p.enabled and not p.consented):
            state, color = "Needs attention", t.BAD
        elif p.enabled and p.consented:
            state, color = "On", t.GOOD
        else:
            state, color = "Off", t.MUTED
        rows: list[ft.Control] = [
            ft.Row([ft.Icon(ft.Icons.EXTENSION, color=t.ACCENT), t.heading(p.name), t.label(p.version),
                    *([t.pill("POKEUDS_PLUGINS", t.INFO)] if p.development else []),
                    ft.Container(expand=True), t.pill(state, color),
                    ft.Switch(value=p.enabled and p.consented, disabled=p.development,
                              on_change=lambda e, x=p: self._toggle(x, e.control.value))]),
            t.label(" · ".join(x for x in (f"by {p.author}" if p.author else "", p.description) if x)),
        ]
        if p.adds():
            rows.append(t.label("Adds " + "; ".join(p.adds()), color=t.TEXT, size=12))
        if p.error:
            rows.append(t.label(p.error, color=t.BAD if state == "Needs attention" else t.WARN, size=12, selectable=True))
        if not p.development:
            rows.append(ft.Row([
                t.secondary("Update from a file...", self._add, ft.Icons.UPGRADE),
                t.secondary("Folder", lambda e, x=p: open_folder(str(x.folder)), ft.Icons.FOLDER_OUTLINED),
                *([ft.TextButton("Page", on_click=lambda e, x=p: self.app.page.launch_url(x.homepage))] if p.homepage else []),
                ft.TextButton("Remove", icon=ft.Icons.DELETE_OUTLINE, on_click=lambda e, x=p: self._confirm_remove(x),
                              style=ft.ButtonStyle(color=t.BAD)),
            ], spacing=8, wrap=True))
        return ft.Container(ft.Column(rows, spacing=6, tight=True), bgcolor=t.RAISED, border_radius=10, padding=12)

    def _sources(self) -> ft.Control:
        rows: list[ft.Control] = [
            t.heading("Sources"),
            t.label("A source is a published list of plugins (a pokeuds-source.json). Follow one by its GitHub OWNER/REPO "
                    "or an https:// address; Browse lists what it offers.", size=12),
            ft.Row([ft.TextField(label="OWNER/REPO or https://...", value=self.source_text, width=420, dense=True,
                                 on_change=lambda e: setattr(self, "source_text", e.control.value or "")),
                    t.secondary("Follow", self._follow, ft.Icons.ADD_LINK),
                    t.primary("Browse", self._browse, ft.Icons.TRAVEL_EXPLORE, disabled=self.browsing),
                    ft.ProgressRing(width=18, height=18, visible=self.browsing)], spacing=8, wrap=True),
        ]
        followed = store.sources()
        if not followed:
            rows.append(t.label("Not following any source.", size=12))
        for text in followed:
            rows.append(ft.Row([ft.Icon(ft.Icons.LINK, size=16, color=t.MUTED), t.label(text, color=t.TEXT),
                                ft.IconButton(ft.Icons.CLOSE, icon_size=16, tooltip="Stop following",
                                              on_click=lambda e, x=text: self._unfollow(x))], spacing=6))
        for text, name, listed in self.listings or []:
            rows.append(ft.Divider(color=t.LINE))
            rows.append(t.label(name, color=t.TEXT))
            if isinstance(listed, str):
                rows.append(t.label(f"Could not read it: {listed}", color=t.BAD, size=12))
                continue
            if not listed:
                rows.append(t.label("It lists no plugins.", size=12))
            for entry in listed:
                current = store.installed(entry.id)
                action = "Install" if current is None else ("Update" if store._version(entry.version) >
                                                             store._version(current.version) else "Reinstall")
                rows.append(ft.Row([
                    ft.Column([ft.Text(f"{entry.name} {entry.version}", color=t.TEXT, size=14),
                               t.label(" · ".join(x for x in (entry.author, entry.description) if x), size=12)],
                              spacing=1, expand=True),
                    t.secondary(action, lambda e, x=entry: self._install_listed(x), ft.Icons.DOWNLOAD)], spacing=8))
        return t.card(*rows, spacing=8)

    # -- actions --------------------------------------------------------------------------------

    def _say(self, text: str, color: str = t.INFO) -> None:
        self.message, self.message_color = text, color

    def _changed(self) -> None:
        """Plugins changed: every page that lists their tools, radios or games is rebuilt."""
        plugins.reload()
        rebuild = getattr(self.app, "rebuild_shell", None)
        if rebuild:
            rebuild()
        else:
            self.refresh()

    async def _add(self, e) -> None:
        files = await self.app.picker.pick_files(dialog_title="Add a plugin", file_type=ft.FilePickerFileType.CUSTOM,
                                                 allowed_extensions=[store.EXTENSION.lstrip("."), "zip"])
        if not (files and files[0].path):
            return
        try:
            package = store.read(files[0].path)
        except (OSError, ValueError) as exc:
            self._say(str(exc), t.BAD)
            self.refresh()
            return
        self._risk(package, lambda: self._install(package, files[0].path))

    def _install(self, package: store.Package, origin: str) -> None:
        try:
            store.install(package, origin=origin)
        except OSError as exc:
            self._say(f"Could not install {package.name}: {exc}", t.BAD)
        else:
            self._say(f"Installed {package.name} {package.version}. Its tools are on the Trade page.", t.GOOD)
        self._changed()

    def _toggle(self, p: plugins.Plugin, on: bool) -> None:
        if on and not p.consented:
            try:
                package = store.describe(p)
            except (OSError, ValueError) as exc:
                self._say(f"{p.name} cannot be read: {exc}", t.BAD)
                self.refresh()
                return
            self._risk(package, lambda: (store.accept(p), self._say(f"{p.name} is on.", t.GOOD), self._changed()),
                       on_cancel=self.refresh)
            return
        plugins.set_enabled(p.id, on)
        self._say(f"{p.name} is {'on' if on else 'off'}.", t.INFO)
        self._changed()

    def _risk(self, package: store.Package, on_accept, on_cancel=None) -> None:
        """The install screen: what the package is, what its code does, who signed it; then accept or not."""
        page = self.app.page
        current = store.installed(package.id)
        how = store.compare(package, current)
        trust = store.trust(package)
        warn = trust in WARN_TRUST
        first = ft.Checkbox(label="I understand it runs with full access to my files, keys, network and radio")
        second = ft.Checkbox(label="I still want this package although its signer changed", visible=warn)
        findings = package.findings or ["Nothing the scan flags (it is a hint, not a review)"]
        go = ft.FilledButton({"install": "Install", "update": "Update", "reinstall": "Reinstall",
                              "downgrade": "Downgrade"}[how], disabled=True,
                             style=ft.ButtonStyle(bgcolor=t.BAD if warn else t.ACCENT, color="#FFFFFF"))

        def checked(e) -> None:
            go.disabled = not (first.value and (second.value or not warn))
            go.update()

        first.on_change = second.on_change = checked

        def close(e=None) -> None:
            page.pop_dialog()
            if on_cancel:
                on_cancel()

        def accept(e) -> None:
            page.pop_dialog()
            on_accept()

        go.on_click = accept
        body = ft.Column([
            t.label(f"{package.name} {package.version}" + (f" by {package.author}" if package.author else "")
                    + f" · {store.size_text(package.size)} · plugin API {package.api}", color=t.TEXT),
            *([t.label(f"Installed now: {current.version}. This {how}s it; its saved data is kept.", size=12)] if current else []),
            t.label(package.description, size=12),
            ft.Row([ft.Icon(ft.Icons.VERIFIED_USER if trust in ("same", "new") else ft.Icons.GPP_MAYBE,
                            color=t.BAD if warn else (t.GOOD if package.key else t.WARN), size=18),
                    t.label(store.TRUST_TEXT[trust] + (f" (key {store.fingerprint(package.key)})" if package.key else ""),
                            color=t.BAD if warn else t.TEXT)], spacing=8),
            t.label("What its code does:", color=t.TEXT),
            *[t.label(f"· {f}", size=12) for f in findings],
            t.label(f"Its files go in {plugins.plugins_dir() / package.id}; it may change files only in its own folders, "
                    "the received folder, the bank and temporary folders.", size=12),
            first, second,
        ], spacing=8, tight=True, scroll=ft.ScrollMode.AUTO, width=560, height=440)
        page.show_dialog(ft.AlertDialog(title=ft.Text(f"{how.title()} {package.name}?"), content=body,
                                        actions=[ft.TextButton("Cancel", on_click=close), go]))

    def _confirm_remove(self, p: plugins.Plugin) -> None:
        page = self.app.page
        size = store.kept_data(p.id)
        data = ft.Checkbox(label=f"Also delete its saved data ({store.size_text(size)})", value=False, visible=size > 0)

        def remove(e) -> None:
            page.pop_dialog()
            store.remove(p.id, data=bool(data.value))
            self._say(f"Removed {p.name}.", t.WARN)
            self._changed()

        page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Remove {p.name}?"),
            content=ft.Column([t.label("Its tools, radios and offer edits go with it."), data], tight=True),
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                     ft.FilledButton("Remove", on_click=remove, style=ft.ButtonStyle(bgcolor=t.BAD, color="#FFFFFF"))]))

    def _follow(self, e) -> None:
        try:
            store.follow(self.source_text)
        except ValueError as exc:
            self._say(str(exc), t.BAD)
        else:
            self._say(f"Following {self.source_text}. Browse to see what it offers.", t.GOOD)
            self.source_text = ""
        self.refresh()

    def _unfollow(self, text: str) -> None:
        store.unfollow(text)
        self.listings = [x for x in (self.listings or []) if x[0] != text]
        self.refresh()

    def _browse(self, e) -> None:
        self.browsing = True
        self.refresh()

        def work() -> None:
            self.listings = store.browse()
            self.browsing = False
            self.app.ui(self.refresh)

        threading.Thread(target=work, daemon=True, name="plugin-sources").start()

    def _install_listed(self, entry: store.Listed) -> None:
        self._say(f"Downloading {entry.name} {entry.version}...", t.INFO)
        self.refresh()

        def work() -> None:
            try:
                path, package = store.download(entry)
            except Exception as exc:
                self._say(f"Could not get {entry.name}: {exc}", t.BAD)
                self.app.ui(self.refresh)
                return
            self.message = ""
            self.app.ui(lambda: self._risk(package, lambda: self._install(package, entry.source), on_cancel=self.refresh))

        threading.Thread(target=work, daemon=True, name="plugin-download").start()
