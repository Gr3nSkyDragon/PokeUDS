"""The Radio page: which radio carries the local wireless, and a check that it answers."""

from __future__ import annotations

import flet as ft

from .. import theme as t
from .common import View, log_box

ESPRESSIF = (0x303A, 0x1001)


def serial_ports() -> list[tuple[str, str]]:
    """(device, description), the ESP32-S3's native USB port first."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    ports = []
    for port in list_ports.comports():
        esp = (port.vid, port.pid) == ESPRESSIF
        ports.append((port.device, ("ESP32-S3 (USB Serial/JTAG)" if esp else port.description or "serial port"), esp))
    ports.sort(key=lambda p: not p[2])
    return [(d, desc) for d, desc, _ in ports]


class RadioView(View):
    key, label, icon = "radio", "Radio", ft.Icons.SETTINGS_INPUT_ANTENNA

    def build(self) -> ft.Control:
        s = self.app.settings
        demo = s.radio == "demo"
        kind = ft.SegmentedButton(
            segments=[ft.Segment("esp32", label=ft.Text("ESP32-S3")), ft.Segment("ldnd", label=ft.Text("USB Wi-Fi adapter"))],
            selected=[s.radio if s.radio in ("esp32", "ldnd") else "esp32"], on_change=self._kind, disabled=self.app.busy())
        controls: list[ft.Control] = [t.title("Radio")]
        if demo:
            controls.append(t.notice(f"The demo radio is on ({s.port or 'red'}): runs go to a simulated 3DS. "
                                     "Turn it off in Debug.", t.INFO))
        body: list[ft.Control] = [ft.Row([kind])]
        if s.radio == "ldnd":
            body += [
                t.label("A USB Wi-Fi adapter through ldnd.exe, Windows only. Use the legacy 0.0.5 build from "
                        "unlimitedcoder2's gist (not ldnrs), set up the adapter in it, and start it before a trade."),
                ft.TextField(label="Pipe", value=s.port or r"\\.\pipe\ldnd", on_blur=self._port_text, width=360,
                             disabled=self.app.busy()),
            ]
        else:
            ports = serial_ports()
            options = [ft.DropdownOption(key="", text="Find it automatically")] + [
                ft.DropdownOption(key=d, text=f"{d}  ·  {desc}") for d, desc in ports]
            body += [
                t.label("An ESP32-S3 flashed with esp32-uds-bridge, on its native USB port."),
                ft.Row([ft.Dropdown(label="Port", value=s.port if s.radio == "esp32" else "", options=options, width=420,
                                    on_select=self._port, disabled=self.app.busy()),
                        ft.IconButton(ft.Icons.REFRESH, tooltip="Look again", on_click=lambda e: self.refresh())],
                       spacing=8),
            ]
        busy = self.app.busy() and self.app.run_kind == "probe"
        body.append(ft.Row([t.primary("Check the radio", self._probe, ft.Icons.WIFI_TETHERING, disabled=self.app.busy()),
                            ft.ProgressRing(width=18, height=18, visible=busy)], spacing=12))
        if self.app.run_kind == "probe":
            body.append(log_box(self.app.log, height=160))
        controls.append(t.card(*body, spacing=14))
        return ft.Column(controls, spacing=t.GAP, scroll=ft.ScrollMode.AUTO)

    def _kind(self, e) -> None:
        self.app.settings.radio = next(iter(e.control.selected), "esp32")
        self.app.settings.port = ""
        self.save()

    def _port(self, e) -> None:
        self.app.settings.port = e.control.value or ""
        self.save()

    def _port_text(self, e) -> None:
        value = (e.control.value or "").strip()
        self.app.settings.port = "" if value == r"\\.\pipe\ldnd" else value
        self.save()

    def _probe(self, e) -> None:
        self.app.start("probe", ["probe", "--radio", self.app.settings.radio_spec()])
