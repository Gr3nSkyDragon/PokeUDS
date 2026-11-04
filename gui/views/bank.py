"""The Bank page: every Pokemon the app keeps, what the trades received included.

Received Pokemon are deposited from the output folder each time the page opens. A record can be
offered in a trade, checked by PKHeX, copied through the Time Capsule (Gen 1 <-> Gen 2), exported or
deleted; Import adds files from anywhere.
"""

from __future__ import annotations

import threading

import flet as ft

from pokeuds.app import bank, catalog

from .. import theme as t
from .common import View, open_folder

FILTERS = (("all", "All"), ("gb", "Game Boy"), ("6", "Gen 6"), ("7", "Gen 7"))
SOURCE_COLORS = {"received": t.GOOD, "imported": t.MUTED, "built": t.INFO, "converted": t.WARN}


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

        header = ft.Row([
            t.title("Bank"), t.label(f"{len(entries)} Pokemon"), ft.Container(expand=True),
            ft.IconButton(ft.Icons.REFRESH, tooltip="Look again", on_click=lambda e: self.refresh()),
            t.secondary("Import...", self._import, ft.Icons.FILE_UPLOAD_OUTLINED),
            t.secondary("Open folder", lambda e: open_folder(str(b.folder)), ft.Icons.FOLDER_OUTLINED),
        ], spacing=8)
        controls: list[ft.Control] = [header]
        choosing = self.app.bank_for
        if choosing:
            entry = catalog.BY_KEY[choosing]
            controls.append(ft.Row([
                t.notice(f"Choosing a Pokemon to offer in {entry.name}: showing what it takes "
                         f"({', '.join('.' + x for x in entry.extensions)}).", t.INFO),
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
            return self.app.bank_for in bank.games_for(e)
        return (self.filter == "all" or (self.filter == "gb" and e.generation <= 2)
                or self.filter == str(e.generation))

    def _row(self, e: bank.Entry) -> ft.Control:
        chosen = e.id == self.selected
        legal = {True: ft.Icon(ft.Icons.VERIFIED_OUTLINED, color=t.GOOD, size=16, tooltip="Legal (PKHeX)"),
                 False: ft.Icon(ft.Icons.REPORT_GMAILERRORRED, color=t.BAD, size=16, tooltip="Not legal (PKHeX)")}
        return ft.Container(ft.Row([
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
        if e.has_mail:
            facts.append(("Mail", "holds mail (sent with it in Gen 2 trades)"))
        if e.origin:
            facts.append(("Origin", e.origin))
        rows: list[ft.Control] = [ft.Row([t.heading(e.species), ft.Container(expand=True),
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
            rows.append(t.label("Build the PKHeX helper to check: dotnet build -c Release services/pkhex", size=12))

        rows.append(ft.Divider(color=t.LINE))
        games = bank.games_for(e)
        rows.append(t.label("Offer in a trade", color=t.TEXT))
        rows.append(ft.Column([t.primary(f"Offer in {catalog.BY_KEY[g].name}", lambda _, x=e, k=g: self._offer(x, k),
                                         ft.Icons.SWAP_HORIZ, disabled=busy) for g in games] or
                              [t.label("No game here takes this record.", size=12)], spacing=6, tight=True))
        actions = [t.secondary("Export...", lambda _, x=e: self._export(x), ft.Icons.FILE_DOWNLOAD_OUTLINED)]
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

    def _offer(self, entry: bank.Entry, game: str) -> None:
        self.app.settings.offers[game] = str(entry.path)
        self.app.bank_for = ""
        self.app.views["trade"].pick_game(game)      # loads the offer from the settings and saves them
        self.app.navigate("trade")
