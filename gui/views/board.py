"""The Board page: ESP32 boards (the S3, C3, C6 and S2; alpha: the classic ESP32), the esp32-uds-bridge firmware they
run, and installing it.

Modelled on pokeldn's Boards page: the firmware comes with PokeUDS (2.1-uds, one image per chip, built from
VC-Adapter), nothing is downloaded. Installing asks the board's chip and writes that chip's image. The page says only
what to do next; the background is in docs/hardware.md.
Checks and installs are ``python -m pokeuds board ...`` children, like every other run, so the page never holds a
serial port the radio needs.
"""

from __future__ import annotations

import flet as ft

from pokeuds.app import board

from .. import theme as t
from .common import View, log_box



class BoardView(View):
    key, label, icon = "board", "Board", ft.Icons.MEMORY

    def __init__(self, app) -> None:
        super().__init__(app)
        self.found: dict[str, dict] = {}       # port -> the `board` event of its last check
        self.failed: set[str] = set()          # ports whose last check got no answer
        self.checking = ""
        self.installing = ""

    # -- layout ---------------------------------------------------------------------------------

    def build(self) -> ft.Control:
        try:
            ports = board.ports()
        except ImportError:
            ports = []
        images = board.bundled()
        image = next((v for v in images.values() if not isinstance(v, str)), None)     # any chip's to install
        problem = "; ".join(v for v in images.values() if isinstance(v, str))
        controls: list[ft.Control] = [
            ft.Row([t.title("Board"), ft.Container(expand=True),
                    t.secondary("Look again", lambda e: self.refresh(), ft.Icons.REFRESH)]),
        ]
        if self.app.settings.radio == "ldnd":
            controls.append(t.notice("Trades use the USB Wi-Fi adapter (the Trade page's Connection). This page is for ESP32 boards.", t.INFO))
        controls.append(t.card(t.heading("Boards"), *([self._board(p, images.get(p.chip, image)) for p in ports] or [self._none()]), spacing=12))
        controls.append(self._firmware(images))
        if self.app.run_kind == "board":
            controls.append(t.card(t.heading("Activity"), log_box(self.app.log, height=200, app=self.app)))
        return ft.Column(controls, spacing=t.GAP, scroll=ft.ScrollMode.AUTO)

    def _none(self) -> ft.Control:
        return ft.Column([
            t.label("No board found", color=t.TEXT, size=14),
            t.label("Plug it in with a data cable, by its native USB port if it has two, then press Look again."),
            t.label("Not showing up? Hold BOOT while plugging it in."),
        ], spacing=4, tight=True)

    def _board(self, port: board.Port, image) -> ft.Control:
        info = self.found.get(port.device)
        busy = self.app.busy()
        in_use = self.app.settings.radio == "esp32" and self.app.settings.port == port.device
        if self.checking == port.device and busy:
            state, color, detail = "Checking...", t.INFO, ""
        elif self.installing == port.device and busy:
            state, color, detail = "Installing...", t.INFO, "Keep it plugged in, about a minute."
        elif port.rom:
            state, color, detail = "Download mode", t.INFO, "After installing, press RESET and check the new port."
        elif info:
            described = info.get("described") or board.BUILDS.get(info["build"], info["build"])
            running = f"Firmware {info['firmware']} ({described}), MAC {info['mac']}."
            if info.get("update") and info.get("optional"):
                state, color = "Optional update", t.INFO
                detail = f"{running} {info.get('advice', '')}".strip()
            elif info.get("update"):
                state, color = ("Other firmware" if info["build"] in board.STANDALONE else "Update available"), t.WARN
                detail = f"{running} {info.get('advice', '')}".strip()
            else:
                state, color, detail = "Ready", t.GOOD, running
        elif port.device in self.failed:
            state, color = "No answer", t.BAD
            detail = f"No firmware answered. Install {board.FIRMWARE}."
            if not port.native and port.chip != "ESP32-S2":
                detail += " On an S3, C3 or C6, use the native USB port."
        elif not port.native:
            state, color = "Not checked", t.MUTED
            detail = "UART port: a classic ESP32 (alpha) trades here; an S3, C3 or C6 needs its native USB port."
        else:
            state, color, detail = "Not checked", t.MUTED, ""
        buttons = []
        if not port.rom:
            buttons.append(t.secondary("Check", lambda e, d=port.device: self._check(d), ft.Icons.SEARCH, disabled=busy))
        install_text = f"Install {board.FIRMWARE}" if not info or info.get("update") else f"Reinstall {board.FIRMWARE}"
        if port.chip != "ESP32-S2" or port.rom:
            buttons.append(t.primary(install_text, lambda e, d=port: self._install(d), ft.Icons.SYSTEM_UPDATE_ALT,
                                     disabled=busy or image is None or isinstance(image, str)))
        else:                                   # an S2 installs only from download mode
            buttons.append(t.label("To install: hold BOOT, tap RESET, release BOOT, Look again."))
        if not port.rom and (info or (port.native and port.device not in self.failed)):
            buttons.append(t.secondary("In use for trades" if in_use else "Use for trades",
                                       lambda e, d=port.device: self._use(d), ft.Icons.CHECK_CIRCLE_OUTLINE,
                                       disabled=busy or in_use))
        return ft.Container(ft.Column([
            ft.Row([ft.Icon(ft.Icons.MEMORY, color=t.MUTED), t.label(port.label(), color=t.TEXT, size=14),
                    ft.Container(expand=True), t.pill(state, color)]),
            *([t.label(detail)] if detail else []),
            ft.Row(buttons, spacing=8, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=8, tight=True), bgcolor=t.RAISED, border_radius=10, padding=12)

    def _firmware(self, images: dict) -> ft.Control:
        rows: list[ft.Control] = [t.heading("Firmware")]
        for chip, found in images.items():
            ok = not isinstance(found, str)
            _, digest, alpha = board.IMAGES[chip]
            pending = digest is None
            rows.append(ft.Row([ft.Icon(ft.Icons.MEMORY, color=t.MUTED, size=18),
                                t.label(f"esp32-uds-bridge {board.FIRMWARE} for the {chip}", color=t.TEXT),
                                t.pill(("included, alpha" if alpha else "included") if ok else ("pending" if pending else "missing"),
                                       (t.WARN if alpha else t.GOOD) if ok else (t.WARN if pending else t.BAD))], spacing=8))
        if any(alpha for _, _, alpha in board.IMAGES.values()):
            rows.append(t.label("Alpha: the classic ESP32 has not traded on a real board yet."))
        problems = [v for chip, v in images.items() if isinstance(v, str) and board.IMAGES[chip][1] is not None]
        if problems:
            rows.append(t.notice("; ".join(problems), t.BAD))
        rows += [
            t.notice("Installing erases keys stored on the board. PokeUDS keeps yours on this computer; Azahar users "
                     "store theirs again.", t.MUTED),
        ]
        return t.card(*rows, spacing=12)

    # -- actions --------------------------------------------------------------------------------

    def _run(self, args: list[str], *, check: str = "", install: str = "", adopt_install: bool = True) -> None:
        self.checking, self.installing = check, install

        def event(ev: dict) -> None:
            if ev.get("event") == "board":
                self.found[ev["port"]] = ev
                self.failed.discard(ev["port"])
                self._adopt(ev["port"])        # the board that answered is the one trades use

        def exited(code: int) -> None:
            if check and code != 0:
                self.failed.add(check)
                self.found.pop(check, None)
            if install:                       # the board runs something new: check it again
                self.found.pop(install, None)
                self.failed.discard(install)
                if code == 0 and adopt_install:
                    self._adopt(install)
            self.checking = self.installing = ""

        self.app.start("board", ["board", *args], on_event=event, on_exit=exited)

    def _check(self, port: str) -> None:
        self._run(["check", "--port", port], check=port)

    def _install(self, port: board.Port) -> None:
        self._run(["flash", "--port", port.device], install=port.device, adopt_install=not port.rom)

    def _adopt(self, port: str) -> None:
        """A board just checked or installed: the Trade page's ESP32 connection uses its port from now on. Runs on the
        child's reader thread; the refresh that follows the event or exit shows it."""
        if board.adopt(self.app.settings, port):
            self.app.save()

    def _use(self, port: str) -> None:
        self.app.settings.radio, self.app.settings.port = "esp32", port
        self.save()
