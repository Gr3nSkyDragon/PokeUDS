"""The Debug page (Settings, Debug tools): the demo radio, a network scan, capture analysis."""

from __future__ import annotations

import flet as ft

from .. import theme as t
from .common import View, log_box

DEMOS = [("", "Off"), ("red", "Red (Gen 1)"), ("yellow", "Yellow (Gen 1)"), ("gold", "Gold (Gen 2 Trade Center)"),
         ("gold-tc", "Gold in the Time Capsule"), ("crystal", "Crystal (Gen 2 Trade Center)")]


class DebugView(View):
    key, label, icon = "debug", "Debug", ft.Icons.BUG_REPORT_OUTLINED

    def __init__(self, app) -> None:
        super().__init__(app)
        self.seconds = 20

    def build(self) -> ft.Control:
        s, busy = self.app.settings, self.app.busy()
        demo = t.card(
            t.heading("Demo radio"),
            t.label("Trades with a simulated 3DS instead of a radio: the real UDS, Pia and Game Boy code against a "
                    "model console. For trying the app and for development; it says nothing about real hardware."),
            ft.Dropdown(label="Simulated 3DS", value=s.port if s.radio == "demo" else "", width=320, disabled=busy,
                        options=[ft.DropdownOption(key=k, text=v) for k, v in DEMOS], on_select=self._demo))
        scan = t.card(
            t.heading("Network scan"),
            t.label("Lists every 3DS local wireless network in range, any game: comm id, players, application data. "
                    "Uses the connection chosen on the Trade page."),
            ft.Row([ft.Dropdown(label="Seconds", value=str(self.seconds), width=120, disabled=busy,
                                options=[ft.DropdownOption(key=str(n), text=str(n)) for n in (10, 20, 30, 60)],
                                on_select=self._seconds),
                    t.primary("Scan", self._scan, ft.Icons.RADAR, disabled=busy or s.radio == "demo")], spacing=12))
        analyze = t.card(
            t.heading("Analyze a capture"),
            t.label("An Azahar log (azahar_uds) or a sniff transcript: the game messages, the passphrase and comm id, "
                    "the Pokemon inside, and for VC titles both Game Boy streams."),
            t.secondary("Choose a log", self._analyze, ft.Icons.DESCRIPTION_OUTLINED, disabled=busy))
        output = []
        if self.app.run_kind in ("scan", "analyze"):
            output = [t.card(ft.Row([t.heading("Output"), ft.Container(expand=True),
                                     *([t.secondary("Stop", lambda e: self.app.stop(), ft.Icons.STOP_ROUNDED)] if busy else [])]),
                             log_box(self.app.log, height=320, app=self.app))]
        return ft.Column([t.title("Debug"), demo, scan, analyze, *output], spacing=t.GAP, scroll=ft.ScrollMode.AUTO)

    def _demo(self, e) -> None:
        value = e.control.value or ""
        s = self.app.settings
        if value:
            s.radio, s.port = "demo", value
        elif s.radio == "demo":
            s.radio, s.port = "esp32", ""
        self.save()
        self.app.changed()

    def _seconds(self, e) -> None:
        self.seconds = int(e.control.value)

    def _scan(self, e) -> None:
        self.app.start("scan", ["scan", "--radio", self.app.settings.radio_spec(), "--seconds", str(self.seconds)])

    async def _analyze(self, e) -> None:
        files = await self.app.picker.pick_files(dialog_title="Choose a capture")
        if files and files[0].path:
            self.app.start("analyze", ["analyze", files[0].path])
