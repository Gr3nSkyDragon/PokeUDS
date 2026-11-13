"""The Settings page."""

from __future__ import annotations

import asyncio
from pathlib import Path

import flet as ft

from pokeuds import __version__
from pokeuds.app import update
from pokeuds.app.settings import data_dir

from .. import theme as t
from .common import View

_gen7 = {"message": "", "color": t.INFO}


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
        gen7_problem = s.gen7_key_problem()
        gen7_path = s.gen7_key_path()
        gen7_ok = bool(gen7_path) and not gen7_problem
        gen7 = t.card(
            t.heading("Gen 7 Mystery Gift Key"),
            t.label("The Gen 7 distributor signs its gifts with an OPSSR key. PokeUDS can read it from your own "
                    "Gen 7 Local Distributor (CXI, CCI or code.bin); retail Sun/Moon/USUM only carry the public "
                    "key. You choose where to save rsa_d_key.txt, and PokeUDS remembers it."),
            ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE if gen7_ok else ft.Icons.ERROR_OUTLINE,
                            color=t.GOOD if gen7_ok else t.WARN, size=18),
                    ft.Text(gen7_path or "No key installed", color=t.TEXT, size=14, expand=True)], spacing=8),
            ft.Row([t.primary("Choose distributor..." if not gen7_path else "Replace distributor...",
                              self._pick_distributor, ft.Icons.KEY),
                    t.secondary("Choose key file...", self._pick_gen7_key, ft.Icons.FOLDER_OPEN)],
                   spacing=8, wrap=True),
            *([t.label(gen7_problem, color=t.WARN)] if gen7_problem and gen7_path else []),
            *([t.label(_gen7["message"], color=_gen7["color"])] if _gen7["message"] else []))
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

        # PKHeX is part of the app; only a source run without it built has anything to say here.
        helper = None if pkhex_setup.ready() else t.card(t.heading("PKHeX"), pkhex_setup.card(self.app, compact=True))
        art = t.card(
            ft.Row([ft.Column([t.heading("Pokemon sprites"),
                               t.label("Previews in the builder, the bank and Mystery Gift show each Pokemon's sprite, "
                                       "downloaded once from PokeAPI's sprite collection and kept. Off: only the ones "
                                       "already downloaded show.")],
                              spacing=4, expand=True),
                    ft.Switch(value=s.sprites, on_change=self._sprites)]))
        root = update.install_root()
        updates = t.card(
            ft.Row([ft.Column([t.heading("Updates"),
                               t.label("At start, PokeUDS asks GitHub whether a newer version is out; one adds a "
                                       "green Update entry to the rail. Updating replaces only PokeUDS's own program "
                                       "files: your Bank, GTS listings, settings and received Pokemon stay as they are."
                                       if root is not None else
                                       "At start, PokeUDS asks GitHub whether a newer version is out; one adds a "
                                       "green Update entry to the rail. Only the Windows app updates itself; here the "
                                       "entry opens the download page.")],
                              spacing=4, expand=True),
                    ft.Switch(value=s.updates, on_change=self._updates)]),
            ft.Row([t.secondary("Check now", lambda e: self.app.updates.check() if getattr(self.app, "updates", None)
                                else None, ft.Icons.SYSTEM_UPDATE_ALT),
                    t.label(f"This is PokeUDS {__version__}.", size=12)], spacing=12))
        debug = t.card(
            ft.Row([ft.Column([t.heading("Debug tools"),
                               t.label("Adds a Debug page: the demo radio, a network scan and capture analysis.")],
                              spacing=4, expand=True),
                    ft.Switch(value=s.debug, on_change=self._debug)]))
        return ft.Column([c for c in (t.title("Settings"), keys, gen7, output, trainer, language, helper, art, updates,
                                      debug)
                          if c is not None] + [
                          t.label(f"PokeUDS {__version__}", size=12)],
                         spacing=t.GAP, scroll=ft.ScrollMode.AUTO)

    async def _pick_keys(self, e) -> None:
        files = await self.app.picker.pick_files(dialog_title="Choose aes_keys.txt", file_type=ft.FilePickerFileType.CUSTOM,
                                                 allowed_extensions=["txt"])
        if files and files[0].path:
            self.app.settings.keys = files[0].path
            self.save()
            self.app.changed()

    async def _pick_distributor(self, e) -> None:
        """Let the user point at their own distributor, extract the key, and choose where to save it."""
        files = await self.app.picker.pick_files(
            dialog_title="Choose your Gen 7 Local Distributor (CXI, CCI or code.bin)",
            file_type=ft.FilePickerFileType.ANY)
        if not files or not files[0].path:
            return
        path = Path(files[0].path)
        _gen7.update(message="Reading the distributor...", color=t.INFO)
        self.app.changed()
        try:
            from pokeuds.games.gen7 import keyextract

            data = await asyncio.to_thread(path.read_bytes)
            offset, private = await asyncio.to_thread(keyextract.extract, data)
        except ValueError as exc:
            _gen7.update(message=str(exc), color=t.BAD)
            self.app.changed()
            return
        except OSError as exc:
            _gen7.update(message=f"Could not read {path}: {exc}", color=t.BAD)
            self.app.changed()
            return

        s = self.app.settings
        initial = (str(Path(s.gen7_key).parent)
                   if s.gen7_key and not s.gen7_key.endswith(".py") else str(data_dir()))
        out = await self.app.picker.save_file(
            dialog_title="Save the Gen 7 signing key",
            file_name="rsa_d_key.txt",
            initial_directory=initial,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["txt"])
        if not out:
            _gen7.update(message="Key not saved.", color=t.INFO)
            self.app.changed()
            return
        try:
            await asyncio.to_thread(keyextract.write_key, out, private, str(path), offset)
        except OSError as exc:
            if getattr(exc, "errno", None) == 13:
                _gen7.update(message=f"Permission denied writing to {Path(out).parent}. Choose a folder PokeUDS "
                                     "can write to.", color=t.BAD)
            else:
                _gen7.update(message=f"Could not write {out}: {exc}", color=t.BAD)
            self.app.changed()
            return
        s.gen7_key = out
        self.save()
        _gen7.update(message=f"Installed {Path(out).name} from {path.name}.", color=t.GOOD)
        self.app.changed()

    async def _pick_gen7_key(self, e) -> None:
        """Choose an already extracted rsa_d_key.txt."""
        files = await self.app.picker.pick_files(dialog_title="Choose rsa_d_key.txt",
                                                 file_type=ft.FilePickerFileType.CUSTOM,
                                                 allowed_extensions=["txt"])
        if not files or not files[0].path:
            return
        from pokeuds.games.gen7 import keyextract

        try:
            key = keyextract.read_text_key(files[0].path)
        except (OSError, ValueError) as exc:
            _gen7.update(message=str(exc), color=t.BAD)
            self.app.changed()
            return
        if key is None:
            _gen7.update(message=f"{Path(files[0].path).name} has no d= line", color=t.BAD)
        else:
            self.app.settings.gen7_key = files[0].path
            self.save()
            _gen7.update(message=f"Using {Path(files[0].path).name}.", color=t.GOOD)
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

    def _updates(self, e) -> None:
        self.app.settings.updates = bool(e.control.value)
        self.app.save()

    def _debug(self, e) -> None:
        self.app.settings.debug = bool(e.control.value)
        if not self.app.settings.debug and self.app.settings.radio == "demo":
            self.app.settings.radio, self.app.settings.port = "esp32", ""
        self.app.save()
        self.app.rebuild_shell()
