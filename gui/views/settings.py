"""The Settings page."""

from __future__ import annotations

import flet as ft

from pokeuds import __version__

from .. import theme as t
from .common import View


def name_problem(name: str) -> str:
    from pokeuds import gbpk

    if not name:
        return "A trainer name is needed"
    if len(name) > 7:
        return "At most 7 characters (the Game Boy games' limit)"
    try:
        gbpk.encode_name(name)
    except ValueError as exc:
        return str(exc)
    return ""


class SettingsView(View):
    key, label, icon = "settings", "Settings", ft.Icons.SETTINGS_OUTLINED

    def build(self) -> ft.Control:
        s = self.app.settings
        keys_problem = s.keys_problem()
        keys = t.card(
            t.heading("Console keys"),
            t.label("aes_keys.txt with the keyslot 0x2D key (slot0x2DKeyN, or slot0x2DKeyX, slot0x2DKeyY and "
                    "generatorConstant): the same file Azahar uses. PokeUDS reads it and never stores it."),
            ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE if not keys_problem else ft.Icons.ERROR_OUTLINE,
                            color=t.GOOD if not keys_problem else t.WARN, size=18),
                    ft.Text(s.keys or "No file chosen", color=t.TEXT, size=14, expand=True),
                    t.secondary("Choose", self._pick_keys, ft.Icons.KEY)], spacing=10),
            *([t.label(keys_problem, color=t.WARN)] if keys_problem and s.keys else []))
        output = t.card(
            t.heading("Received Pokemon"),
            ft.Row([ft.Text(s.output, color=t.TEXT, size=14, expand=True),
                    t.secondary("Change", self._pick_output, ft.Icons.FOLDER_OPEN)], spacing=10))
        problem = name_problem(s.trainer_name)
        trainer = t.card(
            t.heading("Trainer"),
            ft.TextField(label="Name the 3DS sees", value=s.trainer_name, width=260, max_length=7,
                         on_blur=self._name, error=problem or None))
        from pokeuds.app import i18n

        packs = i18n.packs()
        chosen = s.language if any(p.id == s.language for p in packs) else ""
        language = t.card(
            t.heading("App language"),
            t.label("English, or a language pack installed from the Plugins page. Logs, file names and the command "
                    "line stay English."),
            ft.Dropdown(value=chosen, width=320, dense=True, on_select=self._language,
                        options=[ft.DropdownOption(key="", text="English")]
                        + [ft.DropdownOption(key=p.id, text=f"{p.name} ({p.locale})") for p in packs]),
            *([t.label("No language pack installed.", size=12)] if not packs else []))
        from . import pkhex_setup

        helper = t.card(t.heading("PKHeX helper"), pkhex_setup.card(self.app, compact=True))
        art = t.card(
            ft.Row([ft.Column([t.heading("Pokemon sprites"),
                               t.label("Previews in the builder, the bank and Mystery Gift show each Pokemon's sprite, "
                                       "downloaded once from PokeAPI's sprite collection and kept. Off: only the ones "
                                       "already downloaded show.")],
                              spacing=4, expand=True),
                    ft.Switch(value=s.sprites, on_change=self._sprites)]))
        debug = t.card(
            ft.Row([ft.Column([t.heading("Debug tools"),
                               t.label("Adds a Debug page: the demo radio, a network scan and capture analysis.")],
                              spacing=4, expand=True),
                    ft.Switch(value=s.debug, on_change=self._debug)]))
        return ft.Column([t.title("Settings"), keys, output, trainer, language, helper, art, debug, t.label(f"PokeUDS {__version__}", size=12)],
                         spacing=t.GAP, scroll=ft.ScrollMode.AUTO)

    async def _pick_keys(self, e) -> None:
        files = await self.app.picker.pick_files(dialog_title="Choose aes_keys.txt", file_type=ft.FilePickerFileType.CUSTOM,
                                                 allowed_extensions=["txt"])
        if files and files[0].path:
            self.app.settings.keys = files[0].path
            self.save()
            self.app.changed()

    async def _pick_output(self, e) -> None:
        folder = await self.app.picker.get_directory_path(dialog_title="Where received Pokemon go")
        if folder:
            self.app.settings.output = folder
            self.save()

    def _name(self, e) -> None:
        self.app.settings.trainer_name = (e.control.value or "").strip()
        self.save()

    def _language(self, e) -> None:
        from pokeuds.app import i18n

        self.app.settings.language = e.control.value or ""
        self.app.save()
        i18n.select(self.app.settings.language)
        self.app.rebuild_shell()                 # every page again, in the new language

    def _sprites(self, e) -> None:
        from pokeuds.app import sprites

        self.app.settings.sprites = bool(e.control.value)
        sprites.CACHE.online = self.app.settings.sprites
        self.app.save()

    def _debug(self, e) -> None:
        self.app.settings.debug = bool(e.control.value)
        if not self.app.settings.debug and self.app.settings.radio == "demo":
            self.app.settings.radio, self.app.settings.port = "esp32", ""
        self.app.save()
        self.app.rebuild_shell()
