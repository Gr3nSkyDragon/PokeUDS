"""The Trade page: choose the game and the Pokemon, start, and follow the session."""

from __future__ import annotations

from pathlib import Path

import flet as ft

from pokeuds.app import catalog
from pokeuds.app.progress import STEP_LABELS, STEPS

from .. import theme as t
from .common import View, log_box, open_folder


class TradeView(View):
    key, label, icon = "trade", "Trade", ft.Icons.SWAP_HORIZ

    def __init__(self, app) -> None:
        super().__init__(app)
        key = app.settings.last_game if app.settings.last_game in catalog.BY_KEY else "rby"
        self.entry = catalog.BY_KEY[key]
        self.role = "join"
        self.host_title = ""
        self.trades = 1
        self.offer: catalog.Offer | None = None
        self.show_log = False
        self._load_offer()

    # -- state ----------------------------------------------------------------------------------

    def _load_offer(self) -> None:
        path = self.app.settings.offers.get(self.entry.key, "")
        self.offer = catalog.describe_offer(self.entry, path) if path and Path(path).exists() else None

    def pick_game(self, key: str) -> None:
        if self.app.busy():
            return
        self.entry = catalog.BY_KEY[key]
        self.app.settings.last_game = key
        self.role = "join"
        self.host_title = ""
        self._load_offer()
        self.save()

    async def choose_offer(self, e=None) -> None:
        files = await self.app.picker.pick_files(dialog_title="Choose the Pokemon to offer",
                                                 file_type=ft.FilePickerFileType.CUSTOM,
                                                 allowed_extensions=list(self.entry.extensions))
        if files and files[0].path:
            self.app.settings.offers[self.entry.key] = files[0].path
            self.offer = catalog.describe_offer(self.entry, files[0].path)
            self.save()

    def problems(self) -> list[str]:
        return catalog.missing_for_start(self.entry, self.app.settings, self.offer)

    def command(self) -> list[str]:
        return catalog.command(self.entry, self.app.settings, offer=self.offer.path if self.offer else "",
                               role=self.role, host_title=self.host_title, trades=self.trades, verbose=True)

    def start(self, e=None) -> None:
        if self.app.busy() or self.problems():
            return
        self.app.start("trade", self.command())

    # -- layout ---------------------------------------------------------------------------------

    def build(self) -> ft.Control:
        return ft.Row([self.games(), ft.Column([self.setup(), self.session()], spacing=t.GAP, expand=True,
                                               scroll=ft.ScrollMode.AUTO)],
                      spacing=t.GAP, expand=True, vertical_alignment=ft.CrossAxisAlignment.START)

    def games(self) -> ft.Control:
        rows: list[ft.Control] = []
        group = None
        for entry in catalog.ENTRIES:
            if entry.group != group:
                group = entry.group
                rows.append(ft.Container(t.label(group.upper(), size=11, weight=ft.FontWeight.W_600),
                                         padding=ft.Padding(8, 14 if rows else 2, 8, 4)))
            chosen = entry is self.entry
            rows.append(ft.Container(
                ft.Row([ft.Text(entry.name, size=14, color=t.TEXT, weight=ft.FontWeight.W_600 if chosen else None,
                                expand=True),
                        t.pill("alpha", t.MUTED) if not entry.complete else ft.Container()], spacing=6),
                bgcolor=t.ACCENT_SOFT if chosen else None, border_radius=10, padding=ft.Padding(12, 10, 12, 10),
                on_click=lambda e, k=entry.key: self.pick_game(k), ink=True,
                disabled=self.app.busy() and not chosen))
        return ft.Container(ft.Column(rows, spacing=2, scroll=ft.ScrollMode.AUTO), width=270, bgcolor=t.PANEL,
                            border_radius=t.RADIUS, padding=8, border=ft.Border.all(1, t.LINE))

    def setup(self) -> ft.Control:
        entry, busy = self.entry, self.app.busy()
        role = ft.SegmentedButton(
            segments=[ft.Segment("join", label=ft.Text("3DS hosts")), ft.Segment("host", label=ft.Text("PokeUDS hosts"))],
            selected=[self.role], on_change=self._role_changed, disabled=busy)
        how = entry.join_steps if self.role == "join" else entry.host_steps
        controls: list[ft.Control] = [
            ft.Row([t.title(entry.name), t.label(f"with {entry.console}")], spacing=12,
                   vertical_alignment=ft.CrossAxisAlignment.END),
            ft.Row([role], wrap=True),
            t.label(how),
        ]
        if self.role == "host" and entry.host_titles:
            controls.append(ft.Row([
                ft.Dropdown(label="PokeUDS stands in for", value=self.host_title or entry.host_titles[0], width=260,
                            options=[ft.DropdownOption(key=k, text=k.title()) for k in entry.host_titles],
                            on_select=self._host_title_changed, disabled=busy),
                t.pill("experimental", t.WARN)], spacing=12))
        if not entry.complete:
            controls.append(t.notice(f"{entry.name} is session-only for now: PokeUDS joins or hosts and records what the "
                                     "3DS sends, but cannot offer its Pokemon until the trade messages are mapped from "
                                     "captures.", t.INFO))
        offer_text = self.offer.summary if self.offer and self.offer.summary else "No Pokemon chosen"
        controls.append(ft.Row([
            ft.Icon(ft.Icons.CATCHING_POKEMON, color=t.ACCENT if self.offer else t.MUTED),
            ft.Column([ft.Text(offer_text, size=14, color=t.TEXT),
                       t.label(Path(self.offer.path).name if self.offer else
                               "Accepts " + ", ".join(f".{x}" for x in entry.extensions))],
                      spacing=2, expand=True),
            t.secondary("Choose file", self.choose_offer, ft.Icons.FOLDER_OPEN, disabled=busy),
        ], spacing=12))
        if self.offer and not self.offer.problem:
            controls.append(t.label(catalog.trade_mode(entry, self.offer), color=t.TEXT))
        if entry.complete:
            controls.append(ft.Row([
                t.label("Trades before PokeUDS cancels"),
                ft.Dropdown(value=str(self.trades), width=90, disabled=busy,
                            options=[ft.DropdownOption(key=str(n), text=str(n)) for n in range(1, 7)],
                            on_select=self._trades_changed)], spacing=12))
        for problem in self.problems():
            controls.append(t.notice(problem, t.WARN, ft.Icons.WARNING_AMBER_ROUNDED))
        button = (t.secondary("Stop", lambda e: self.app.stop(), ft.Icons.STOP_ROUNDED) if busy and self.app.run_kind == "trade"
                  else t.primary("Start", self.start, ft.Icons.PLAY_ARROW_ROUNDED, disabled=bool(self.problems()) or busy))
        radio = self.app.settings.radio_spec()
        controls.append(ft.Row([button, t.label(f"Radio: {radio}" + ("  (Debug)" if radio.startswith("demo") else ""))],
                               spacing=16))
        return t.card(*controls, spacing=14)

    def session(self) -> ft.Control:
        p = self.app.progress
        color = t.STATE_COLORS.get(p.state, t.MUTED)
        state_word = {"idle": "Idle", "running": "Running", "done": "Done", "stopped": "Stopped", "error": "Error"}[p.state]
        steps = ft.Row([
            ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE if p.steps[s] else ft.Icons.RADIO_BUTTON_UNCHECKED,
                            color=t.GOOD if p.steps[s] else t.MUTED, size=18),
                    t.label(STEP_LABELS[s], color=t.TEXT if p.steps[s] else t.MUTED)], spacing=6)
            for s in STEPS], spacing=18, wrap=True)
        controls: list[ft.Control] = [
            ft.Row([t.heading("Session"), t.pill(state_word, color)], spacing=10),
            ft.Text(p.headline, size=18, color=t.TEXT, weight=ft.FontWeight.W_600),
        ]
        if p.detail:
            controls.append(t.label(p.detail))
        controls.append(steps)
        if p.received:
            controls.append(t.heading("Received"))
            for item in p.received:
                controls.append(ft.Row([ft.Icon(ft.Icons.DOWNLOAD_DONE, color=t.GOOD, size=18),
                                        ft.Column([ft.Text(item.get("summary", ""), color=t.TEXT, size=14),
                                                   t.label(Path(item.get("path", "")).name, size=12)], spacing=1, expand=True),
                                        ], spacing=10))
            controls.append(t.secondary("Open output folder", lambda e: open_folder(self.app.settings.output),
                                        ft.Icons.FOLDER_OUTLINED))
        if p.notes:
            controls.append(t.heading("What happened"))
            controls += [t.label("· " + n) for n in p.notes[-8:]]
        controls.append(ft.TextButton("Hide the log" if self.show_log else "Show the log", on_click=self._toggle_log))
        if self.show_log:
            controls.append(log_box(self.app.log))
        return t.card(*controls, spacing=12)

    # -- events ---------------------------------------------------------------------------------

    def _role_changed(self, e) -> None:
        self.role = next(iter(e.control.selected), "join")
        self.refresh()

    def _host_title_changed(self, e) -> None:
        self.host_title = e.control.value
        self.refresh()

    def _trades_changed(self, e) -> None:
        self.trades = int(e.control.value)

    def _toggle_log(self, e) -> None:
        self.show_log = not self.show_log
        self.refresh()
