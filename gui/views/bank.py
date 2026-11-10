"""The Bank page: every Pokemon the app keeps, what the trades received included.

Received Pokemon are deposited from the output folder each time the page opens. A record can be
offered in a trade, checked by PKHeX (or the whole bank at once), copied through the Time Capsule
(Gen 1 <-> Gen 2), migrated to Gen 7 as Poke Transporter or Pokemon Bank would (a copy; the original stays),
exported or deleted; Import adds files from anywhere.
"""

from __future__ import annotations

import threading
import time

import flet as ft

from pokeuds.app import bank, catalog
from pokeuds.games import vc as vc_titles

from .. import theme as t
from .common import View, awaiting, open_folder
from .sprite import sprite

FILTERS = (("all", "All"), ("gb", "Game Boy"), ("6", "Gen 6"), ("7", "Gen 7"), ("illegal", "Not legal"))
SOURCE_COLORS = {"received": t.GOOD, "imported": t.MUTED, "built": t.INFO, "converted": t.WARN, "gift": t.ACCENT,
                 "gts": t.GOOD}


def _pkhex_ready() -> bool:
    from pokeuds import pkhex

    return pkhex.helper_path() is not None


class BankView(View):
    key, label, icon = "bank", "Bank", ft.Icons.INVENTORY_2_OUTLINED

    def __init__(self, app) -> None:
        super().__init__(app)
        self.filter = "all"
        self.selected = ""
        self.message = ""
        self.message_color = t.INFO
        self.checking = False
        self.check_progress = ""         # "12 of 80" while the whole bank is checked
        self.stop_check = False
        self.migrating = False

    @property
    def bank(self) -> bank.Bank:
        return bank.Bank(self.app.settings.bank or None)

    # -- layout ---------------------------------------------------------------------------------

    def build(self) -> ft.Control:
        b = self.bank
        added = b.sync_received(self.app.settings.output)
        if added:
            self.message = f"{len(added)} new from the received folder." if len(added) > 1 else \
                f"{added[0].describe()} is new from the received folder."
            self.message_color = t.GOOD
        entries = b.entries()
        shown = [e for e in entries if self._keep(e)]
        selected = next((e for e in entries if e.id == self.selected), None)
        duplicates = len(b.duplicates())

        header = ft.Row([
            t.title("Bank"), t.label(f"{len(entries)} Pokemon"), ft.Container(expand=True),
            ft.IconButton(ft.Icons.REFRESH, tooltip="Look again", on_click=lambda e: self.refresh()),
            *self._check_all_controls(entries),
            *([t.secondary(f"Remove {duplicates} duplicate" + ("s" if duplicates > 1 else ""),
                           lambda e: self._confirm_dedupe(), ft.Icons.CLEANING_SERVICES_OUTLINED)] if duplicates else []),
            t.secondary("Import...", self._import, ft.Icons.FILE_UPLOAD_OUTLINED),
            t.secondary("Open folder", lambda e: open_folder(str(b.folder)), ft.Icons.FOLDER_OUTLINED),
        ], spacing=8)
        controls: list[ft.Control] = [header]
        choosing = self.app.bank_for
        if choosing:
            entry = catalog.BY_KEY[choosing]
            takes = (f"Adding to the {entry.name} trade queue: showing what it takes "
                     f"({', '.join('.' + x for x in entry.extensions)})")
            if "pk7" in entry.extensions:
                takes += ", and the Gen 1, 2 and 6 Pokemon PokeUDS can migrate to .pk7 first"
            controls.append(ft.Row([
                t.notice(takes + ". Double-click one to queue it and go back.", t.INFO),
                t.secondary("Show everything", self._stop_choosing, ft.Icons.CLOSE)], spacing=8))
        else:
            controls.append(ft.SegmentedButton(
                segments=[ft.Segment(k, label=ft.Text(v)) for k, v in FILTERS], selected=[self.filter],
                on_change=self._filter))
        if self.message:
            controls.append(t.notice(self.message, self.message_color))

        if not entries:
            listing = t.card(t.label("The bank is empty. Pokemon the 3DS sends land here after each trade; "
                                     "Import adds .pk1, .pk2, .pk6, .pk7 (or .ek6/.ek7) files you have."))
        elif not shown:
            listing = t.card(t.label("Nothing here matches."))
        else:
            listing = t.card(*[self._row(e) for e in shown[:300]], spacing=0, padding=8)
        body = ft.Row([ft.Container(listing, expand=True)], vertical_alignment=ft.CrossAxisAlignment.START, spacing=t.GAP)
        if selected:
            body.controls.append(ft.Container(self._detail(selected), width=380))
        controls.append(body)
        return ft.Column(controls, spacing=12, scroll=ft.ScrollMode.AUTO)

    def _keep(self, e: bank.Entry) -> bool:
        if self.app.bank_for:
            return self.app.bank_for in bank.games_for(e) or self.app.bank_for in bank.migrates_to(e)
        return (self.filter == "all" or (self.filter == "gb" and e.generation <= 2)
                or (self.filter == "illegal" and e.legal is False) or self.filter == str(e.generation))

    def _row(self, e: bank.Entry) -> ft.Control:
        row = self._row_box(e)
        game = self.app.bank_for
        if not game:
            return row
        # Choosing for the Trade page: a double click queues it there (migrating it first if it must) and goes back.
        if game in bank.games_for(e):
            add = lambda _, x=e: self._offer(x, game)
        else:
            add = lambda _, x=e: self._offer_migrated(x, game)
        return ft.GestureDetector(row, on_double_tap=add)

    def _row_box(self, e: bank.Entry) -> ft.Control:
        chosen = e.id == self.selected
        legal = {True: ft.Icon(ft.Icons.VERIFIED_OUTLINED, color=t.GOOD, size=16, tooltip="Legal (PKHeX)"),
                 False: ft.Icon(ft.Icons.REPORT_GMAILERRORRED, color=t.BAD, size=16, tooltip="Not legal (PKHeX)")}
        return ft.Container(ft.Row([
            sprite(self.app, e.dex, form=e.form, shiny=e.shiny, size=44, on_ready=self.refresh),
            t.pill(e.extension.upper(), t.ACCENT),
            ft.Column([ft.Text(e.describe(), color=t.TEXT, size=14),
                       t.label(" · ".join(x for x in (bank.SOURCES.get(e.source, e.source), e.when, e.note) if x), size=12)],
                      spacing=1, expand=True),
            *([ft.Icon(ft.Icons.AUTO_AWESOME, color=t.WARN, size=16, tooltip="Shiny")] if e.shiny else []),
            *([ft.Icon(ft.Icons.MAIL_OUTLINE, color=t.MUTED, size=16, tooltip="Holds mail")] if e.has_mail else []),
            *([legal[e.legal]] if e.legal is not None else []),
        ], spacing=12), padding=ft.Padding(8, 8, 8, 8), border_radius=8,
            bgcolor=t.RAISED if chosen else None, on_click=lambda _, i=e.id: self._select(i), ink=True)

    def _detail(self, e: bank.Entry) -> ft.Control:
        busy = self.app.busy()
        facts = [
            ("Species", f"{e.species} (#{e.dex})"), ("Nickname", e.nickname or "-"),
            ("Level", str(e.level) if e.level else "-"), ("Trainer", f"{e.ot} {e.shown_tid()}"),
            ("Format", f".{e.extension} (Generation {e.generation})"),
            ("From", bank.SOURCES.get(e.source, e.source) + f", {e.when}"),
        ]
        if e.generation <= 2 and e.meta.get("language") in vc_titles.LANGUAGES:
            facts.append(("Language", f"{vc_titles.LANGUAGES[e.meta['language']]} (the game it came from)"))
        if e.has_mail:
            facts.append(("Mail", "holds mail (sent with it in Gen 2 trades)"))
        if e.source == "migrated" and (original := self.bank.find(e.meta.get("migrated_from", ""))):
            facts.append(("Migrated from", f"{original.describe()} (.{original.extension})"))
        elif e.origin:
            facts.append(("Origin", e.origin))
        rows: list[ft.Control] = [ft.Row([sprite(self.app, e.dex, form=e.form, shiny=e.shiny, size=96, on_ready=self.refresh),
                                          t.heading(e.species), ft.Container(expand=True),
                                          *([t.pill("Shiny", t.WARN)] if e.shiny else []),
                                          ft.IconButton(ft.Icons.CLOSE, on_click=lambda _: self._select(""), icon_size=18)])]
        rows += [ft.Row([t.label(k, size=12), ft.Text(v, size=13, color=t.TEXT, selectable=True, expand=True)],
                        vertical_alignment=ft.CrossAxisAlignment.START, spacing=8)
                 for k, v in facts]
        rows.append(ft.TextField(label="Note", value=e.note, dense=True, on_blur=lambda ev, x=e: self._note(x, ev)))

        if e.legal is None:
            legality = t.label("Not checked yet.", size=12)
        else:
            legality = ft.Column([t.pill("Legal" if e.legal else "Not legal", t.GOOD if e.legal else t.BAD),
                                  *([t.label(e.meta.get("encounter", ""), size=12)] if e.meta.get("encounter") else []),
                                  ft.Text(e.report, size=12, color=t.MUTED, selectable=True)], spacing=6, tight=True)
        pkhex_ok = _pkhex_ready()
        rows += [ft.Divider(color=t.LINE), t.label("Legality (PKHeX)", color=t.TEXT), legality,
                 ft.Row([t.secondary("Check" if e.legal is None else "Check again", lambda _, x=e: self._check(x),
                                     ft.Icons.FACT_CHECK_OUTLINED, disabled=self.checking or not pkhex_ok),
                         ft.ProgressRing(width=16, height=16, visible=self.checking)], spacing=8)]
        if not pkhex_ok:
            from . import pkhex_setup

            rows.append(pkhex_setup.card(self.app, compact=True))

        rows.append(ft.Divider(color=t.LINE))
        games = bank.games_for(e)
        rows.append(t.label("Add to a trade queue", color=t.TEXT))
        target = bank.migration_target(e)
        offers = [t.primary(f"Offer in {catalog.BY_KEY[g].name}", lambda _, x=e, k=g: self._offer(x, k),
                            ft.Icons.SWAP_HORIZ, disabled=busy) for g in games]
        offers += [t.secondary(f"Offer in {catalog.BY_KEY[g].name} (migrates to .pk{target})",
                               lambda _, x=e, k=g: self._offer_migrated(x, k), ft.Icons.MOVE_UP,
                               disabled=busy or self.migrating or not pkhex_ok)
                   for g in bank.migrates_to(e)]
        rows.append(ft.Column(offers or [t.label("No game here takes this record.", size=12)], spacing=6, tight=True))
        if target:
            rows += [ft.Divider(color=t.LINE), *self._migration(e, target, pkhex_ok)]
        actions = [t.secondary("Export...", awaiting(self._export, e), ft.Icons.FILE_DOWNLOAD_OUTLINED)]
        if e.extension in ("pk1", "pk2", "pk6", "pk7") and not e.has_mail:
            actions.insert(0, t.secondary("List on the GTS", lambda _, x=e: self._to_gts(x), ft.Icons.PUBLIC))
        if e.generation <= 2:
            actions.insert(0, t.secondary(f"Time Capsule copy (.pk{3 - e.generation})", lambda _, x=e: self._convert(x),
                                          ft.Icons.HISTORY))
        actions.append(ft.TextButton("Delete", icon=ft.Icons.DELETE_OUTLINE, on_click=lambda _, x=e: self._confirm_delete(x),
                                     style=ft.ButtonStyle(color=t.BAD)))
        rows += [ft.Divider(color=t.LINE), ft.Row(actions, wrap=True, spacing=8)]
        return t.card(*rows, spacing=8)

    # -- actions --------------------------------------------------------------------------------

    def _say(self, text: str, color: str = t.INFO) -> None:
        self.message, self.message_color = text, color

    def _select(self, entry_id: str) -> None:
        self.selected = "" if entry_id == self.selected else entry_id
        self.message = ""
        self.refresh()

    def _filter(self, e) -> None:
        self.filter = next(iter(e.control.selected), "all")
        self.refresh()

    def _stop_choosing(self, e) -> None:
        self.app.bank_for = ""
        self.refresh()

    async def _import(self, e) -> None:
        files = await self.app.picker.pick_files(dialog_title="Add Pokemon files to the bank", allow_multiple=True,
                                                 file_type=ft.FilePickerFileType.CUSTOM,
                                                 allowed_extensions=[x.lstrip(".") for x in bank.IMPORTABLE])
        added, kept, failed = [], 0, []
        for f in files or []:
            if not f.path:
                continue
            try:
                entry, new = self.bank.import_file(f.path)
            except (OSError, ValueError) as exc:
                failed.append(str(exc))
                continue
            if new:
                added.append(entry)
            else:
                kept += 1
        if added or kept or failed:
            parts = [f"Added {len(added)}."] + ([f"{kept} already in the bank."] if kept else []) + failed[:3]
            self._say(" ".join(parts), t.BAD if failed and not added else t.GOOD)
            if len(added) == 1:
                self.selected = added[0].id
        self.refresh()

    def _note(self, entry: bank.Entry, ev) -> None:
        value = (ev.control.value or "").strip()
        if value != entry.note:
            self.bank.set_note(entry, value)
            self.refresh()

    def _check(self, entry: bank.Entry) -> None:
        self.checking = True
        self.refresh()

        def work() -> None:
            try:
                result = bank.check(self.bank, entry)
                self._say(f"PKHeX: {'legal' if result.legal else 'not legal'}.", t.GOOD if result.legal else t.BAD)
            except Exception as exc:        # the helper missing, refusing or exiting
                self._say(f"PKHeX could not check it: {exc}", t.BAD)
            self.checking = False
            self.app.ui(self.refresh)

        threading.Thread(target=work, daemon=True, name="pkhex-check").start()

    def _convert(self, entry: bank.Entry) -> None:
        try:
            copy, new = self.bank.convert(entry)
        except ValueError as exc:
            self._say(str(exc), t.BAD)
        else:
            self._say(f"Time Capsule copy: {copy.describe()} (.{copy.extension})." if new else
                      "That Time Capsule copy is already in the bank.", t.GOOD)
            self.selected = copy.id
        self.refresh()

    async def _export(self, entry: bank.Entry) -> None:
        folder = await self.app.picker.get_directory_path(dialog_title="Export to")
        if folder:
            target = self.bank.export(entry, folder)
            self._say(f"Exported to {target}.", t.GOOD)
            self.refresh()

    def _confirm_delete(self, entry: bank.Entry) -> None:
        page = self.app.page

        def close(_=None) -> None:
            page.pop_dialog()

        def delete(_) -> None:
            page.pop_dialog()
            self.bank.remove(entry)
            self.selected = ""
            self._say(f"Deleted {entry.describe()}.", t.WARN)
            self.refresh()

        page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Delete {entry.species}?"),
            content=ft.Text(f"{entry.describe()} leaves the bank for good. A file in the received folder stays there."),
            actions=[ft.TextButton("Cancel", on_click=close),
                     ft.FilledButton("Delete", on_click=delete, style=ft.ButtonStyle(bgcolor=t.BAD, color="#FFFFFF"))]))

    def _confirm_dedupe(self) -> None:
        page = self.app.page
        pairs = self.bank.duplicates()

        def close(_=None) -> None:
            page.pop_dialog()

        def remove(_) -> None:
            page.pop_dialog()
            removed = self.bank.remove_duplicates()
            kept = {str(d.path): str(k.path) for d, k in removed}
            s = self.app.settings
            for game in list(s.offers) + list(s.queued):  # an offer on a removed copy moves to the one kept
                queue = [kept.get(p, p) for p in s.offer_queue(game)]
                s.set_offer_queue(game, list(dict.fromkeys(queue)))
            self.app.settings.save()
            if self.selected in {d.id for d, _ in removed}:
                self.selected = ""
            self._say(f"Removed {len(removed)} duplicate" + ("s" if len(removed) > 1 else "") + ".", t.WARN)
            self.refresh()

        names = sorted({k.describe() for _, k in pairs})
        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Remove duplicates?"),
            content=ft.Text(f"{len(pairs)} entries hold a Pokemon the bank already has (the same Pokemon after a "
                            "trade there and back). The oldest copy of each stays: " + "; ".join(names[:5])
                            + ("..." if len(names) > 5 else "") + ". Files in the received folder stay there."),
            actions=[ft.TextButton("Cancel", on_click=close),
                     ft.FilledButton("Remove", on_click=remove, style=ft.ButtonStyle(bgcolor=t.BAD, color="#FFFFFF"))]))

    def _offer(self, entry: bank.Entry, game: str) -> None:
        """Add the record to the game's trade queue and show the Trade page."""
        trade = self.app.views["trade"]
        self.app.bank_for = ""
        trade.pick_game(game)                        # loads the game's queue from the settings
        trade._add_and_say([str(entry.path)])
        trade.save()
        self.app.navigate("trade")

    def _to_gts(self, entry: bank.Entry) -> None:
        self.app.views["gts"].choose(entry.id)
        self.app.navigate("gts")

    # -- the whole bank's legality --------------------------------------------------------------

    def _check_all_controls(self, entries: list[bank.Entry]) -> list[ft.Control]:
        if not entries:
            return []
        if self.check_progress:
            return [ft.ProgressRing(width=16, height=16), t.label(f"Checking {self.check_progress}", size=12),
                    t.secondary("Stop", lambda e: self._stop_checking(), ft.Icons.STOP_ROUNDED)]
        return [t.secondary("Check all", lambda e: self._check_all(), ft.Icons.FACT_CHECK_OUTLINED,
                            disabled=self.checking or not _pkhex_ready())]

    def _stop_checking(self) -> None:
        self.stop_check = True

    def _check_all(self) -> None:
        self.checking, self.stop_check = True, False
        self.check_progress = "..."
        self.refresh()
        last = [0.0]

        def progress(done: int, total: int, entry) -> None:
            self.check_progress = f"{done} of {total}"
            now = time.monotonic()
            if now - last[0] > 0.5:           # the page redrawn twice a second, not once per Pokemon
                last[0] = now
                self.app.ui(self.refresh)

        def work() -> None:
            try:
                result = bank.check_all(self.bank, progress=progress, stop=lambda: self.stop_check)
                done = len(result.legal) + len(result.illegal)
                parts = [f"PKHeX checked {done}: {len(result.legal)} legal, {len(result.illegal)} not legal."]
                if result.failed:
                    entry, why = result.failed[0]
                    parts.append(f"{len(result.failed)} could not be checked ({entry.describe()}: {why}).")
                if self.stop_check:
                    parts.append("Stopped before the end.")
                self._say(" ".join(parts), t.BAD if result.illegal or result.failed else t.GOOD)
                if result.illegal and not self.app.bank_for:
                    self.filter = "illegal"
            except Exception as exc:        # the helper missing or refusing to start
                self._say(f"PKHeX could not check the bank: {exc}", t.BAD)
            self.checking, self.check_progress = False, ""
            self.app.ui(self.refresh)

        threading.Thread(target=work, daemon=True, name="pkhex-check-all").start()

    # -- migrating to Gen 7 ---------------------------------------------------------------------

    def _migration(self, e: bank.Entry, target: int, pkhex_ok: bool) -> list[ft.Control]:
        app = bank.MIGRATION_APPS[e.extension]
        rows: list[ft.Control] = [t.label(f"Migrate to Generation {target} ({app})", color=t.TEXT)]
        copy = bank.migrated_copy(self.bank, e)
        if copy:
            rows.append(t.label(f"Migrated already: {copy.describe()} (.{copy.extension}). The copy is kept beside "
                                "this one; migrating again gives the same copy.", size=12))
            rows.append(ft.Row([t.secondary("Show the copy", lambda _, i=copy.id: self._select(i), ft.Icons.ARROW_FORWARD)]))
        else:
            rows.append(t.label(f"A .pk{target} copy made as {app} makes it. This .{e.extension} stays in the bank as "
                                f"it is; the copy can never come back to Generation {e.generation}.", size=12))
            rows.append(ft.Row([t.secondary(f"Migrate to .pk{target}...", lambda _, x=e: self._confirm_migrate(x),
                                            ft.Icons.MOVE_UP, disabled=self.migrating or not pkhex_ok),
                                ft.ProgressRing(width=16, height=16, visible=self.migrating)], spacing=8))
        return rows

    def _offer_migrated(self, entry: bank.Entry, game: str) -> None:
        copy = bank.migrated_copy(self.bank, entry)
        if copy:
            self._offer(copy, game)
        else:
            self._confirm_migrate(entry, offer_in=game)

    def _confirm_migrate(self, entry: bank.Entry, offer_in: str = "") -> None:
        page = self.app.page
        target = bank.migration_target(entry)
        app = bank.MIGRATION_APPS[entry.extension]
        choice = {"vc": bank.VC_GAMES[entry.generation][0][0] if entry.generation <= 2 else None}
        what = (f"{entry.describe()} is a .{entry.extension}. "
                + (f"To trade it in {catalog.BY_KEY[offer_in].name}, " if offer_in else "")
                + f"PokeUDS migrates it to Generation {target} the way {app} does. The original .{entry.extension} "
                f"stays in the bank; the .pk{target} copy can no longer go to Generation {entry.generation}.")
        if entry.generation <= 2:
            changes = ("As Transporter does it: met in the " + ("Kanto" if entry.generation == 1 else "Johto")
                       + " region today at its current level, in a Poke Ball; three perfect IVs (five for Mew and "
                       "Celebi) and the rest random; the nature its EXP gives; its Hidden Ability for most species; "
                       "EXP set to the start of its level. Its moves and nickname come along; a held item and mail "
                       "stay behind. You (the Builder's trainer) become its handling trainer.")
        else:
            changes = ("As Bank does it: little changes. Markings, Super Training and geolocation data are reset, and "
                       "you (the Builder's trainer) become its handling trainer. A Cosplay Pikachu cannot move.")
        controls: list[ft.Control] = [ft.Text(what), ft.Text(changes, size=13, color=t.MUTED)]
        if entry.generation <= 2:
            controls.append(ft.Dropdown(
                label="Transferred from", value=choice["vc"], width=220, dense=True,
                options=[ft.DropdownOption(key=k, text=v) for k, v in bank.VC_GAMES[entry.generation]],
                tooltip="The Virtual Console game Transporter reads it from; its record names that game as its origin",
                on_select=lambda ev: choice.__setitem__("vc", ev.control.value)))

        def close(_=None) -> None:
            page.pop_dialog()

        def go(_) -> None:
            page.pop_dialog()
            self._migrate(entry, choice["vc"], offer_in)

        page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Migrate {entry.species} to Generation {target}?"),
            content=ft.Column(controls, tight=True, spacing=12, width=460),
            actions=[ft.TextButton("Cancel", on_click=close),
                     ft.FilledButton("Migrate and offer" if offer_in else "Migrate", on_click=go)]))

    def _migrate(self, entry: bank.Entry, vc_game: str | None, offer_in: str) -> None:
        from pokeuds.app.builder import Trainer

        s = self.app.settings
        trainer = Trainer.from_dict(s.trainer, s.trainer_name or "POKEUDS").request(bank.migration_target(entry))
        self.migrating = True
        self.refresh()

        def work() -> None:
            copy = None
            try:
                copy, new = bank.migrate(self.bank, entry, vc_game=vc_game, trainer=trainer)
                legal = "legal" if copy.legal else "NOT legal"
                self._say(f"Migrated: {copy.describe()} (.{copy.extension}), {legal} by PKHeX." if new else
                          f"Already migrated: {copy.describe()}.", t.GOOD if copy.legal else t.WARN)
                self.selected = copy.id
            except Exception as exc:        # what PKHeX refuses (an Egg, a Cosplay Pikachu), or the helper gone
                self._say(f"Could not migrate {entry.species}: {exc}", t.BAD)
            self.migrating = False
            if copy is not None and offer_in:
                self.app.ui(lambda: self._offer(copy, offer_in))
            else:
                self.app.ui(self.refresh)

        threading.Thread(target=work, daemon=True, name="pkhex-migrate").start()
