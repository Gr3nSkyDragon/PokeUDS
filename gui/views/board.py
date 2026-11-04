"""The Board page: ESP32-S3 boards, the esp32-uds-bridge firmware they run, and installing it.

Modelled on pokeldn's Boards page. Checks and installs are ``python -m pokeuds board ...`` children, like
every other run, so the page never holds a serial port the radio needs.
"""

from __future__ import annotations

import flet as ft

from pokeuds.app import board

from .. import theme as t
from .common import View, log_box, open_folder

BUILDS = {"gblink": "GB-Link build", "vcadapter": "VC-Adapter board build", "": "3DS only"}


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
        images = board.local_images()
        best = board.recommended()
        controls: list[ft.Control] = [
            ft.Row([t.title("Board"), ft.Container(expand=True),
                    t.secondary("Look again", lambda e: self.refresh(), ft.Icons.REFRESH)]),
        ]
        if self.app.settings.radio == "ldnd":
            controls.append(t.notice("Trades use the USB Wi-Fi adapter (Radio page). This page is for ESP32-S3 boards.", t.INFO))
        controls.append(t.card(t.heading("Boards"), *([self._board(p, best) for p in ports] or [self._none()]), spacing=12))
        controls.append(self._firmware(images, best))
        if self.app.run_kind == "board":
            controls.append(t.card(t.heading("Activity"), log_box(self.app.log, height=200)))
        return ft.Column(controls, spacing=t.GAP, scroll=ft.ScrollMode.AUTO)

    def _none(self) -> ft.Control:
        return ft.Column([
            t.label("No board found", color=t.TEXT, size=14),
            t.label("Plug the ESP32-S3 into this computer with a data cable, on the port marked USB (its native USB). "
                    "It shows up here; press Look again. A board with no firmware may need BOOT held while plugging it in."),
        ], spacing=4, tight=True)

    def _board(self, port: board.Port, best: board.Image | None) -> ft.Control:
        info = self.found.get(port.device)
        busy = self.app.busy()
        in_use = self.app.settings.radio == "esp32" and self.app.settings.port == port.device
        if self.checking == port.device and busy:
            state, color, detail = "Checking...", t.INFO, "Asking the board for its firmware. Firmware 1.8 restarts into its 3DS mode first."
        elif self.installing == port.device and busy:
            state, color, detail = "Installing...", t.INFO, "Keep the board plugged in. About a minute."
        elif info:
            if info.get("update"):
                state, color = "Update available", t.WARN
                detail = f"esp32-uds-bridge {info['firmware']} ({BUILDS.get(info['build'], info['build'])}). {info['update']} is downloaded."
            else:
                state, color = "Ready", t.GOOD
                detail = f"esp32-uds-bridge {info['firmware']} ({BUILDS.get(info['build'], info['build'])}), MAC {info['mac']}."
                if info["build"] == "vcadapter":
                    detail += " This build is meant for the VC-Adapter board; the GB-Link build suits a plain board."
        elif port.device in self.failed:
            state, color = "No answer", t.BAD
            detail = "No esp32-uds-bridge answered. Install the firmware below (it needs firmware 1.7 or later)."
        elif not port.native:
            state, color = "UART port", t.MUTED
            detail = "A USB-UART bridge: firmware can be installed here, but trades need the board's native USB port."
        else:
            state, color, detail = "Not checked", t.MUTED, "Check it to read its firmware."
        buttons = []
        if port.native:
            buttons.append(t.secondary("Check", lambda e, d=port.device: self._check(d), ft.Icons.SEARCH, disabled=busy))
        install_text = "Update" if info and info.get("update") else "Install firmware"
        buttons.append(t.primary(install_text, lambda e, d=port.device: self._install(d, best.path if best else None),
                                 ft.Icons.DOWNLOAD_FOR_OFFLINE, disabled=busy or best is None))
        if port.native and (info or port.device not in self.failed):
            buttons.append(t.secondary("In use for trades" if in_use else "Use for trades",
                                       lambda e, d=port.device: self._use(d), ft.Icons.CHECK_CIRCLE_OUTLINE,
                                       disabled=busy or in_use))
        return ft.Container(ft.Column([
            ft.Row([ft.Icon(ft.Icons.MEMORY, color=t.MUTED), t.label(port.label(), color=t.TEXT, size=14),
                    ft.Container(expand=True), t.pill(state, color)]),
            t.label(detail),
            ft.Row(buttons, spacing=8, wrap=True),
        ], spacing=8, tight=True), bgcolor=t.RAISED, border_radius=10, padding=12)

    def _firmware(self, images: list[board.Image], best: board.Image | None) -> ft.Control:
        busy = self.app.busy()
        rows: list[ft.Control] = [
            t.heading("Firmware"),
            t.label("PokeUDS uses VC-Adapter's esp32-uds-bridge unchanged. The GB-Link build (firmware 1.8) is the one to "
                    "install: the 3DS radio every generation trades over, and the optional SSD1306 screen, which shows "
                    "the radio's state on its own. Its Switch side is not used here; the board restarts into its 3DS mode "
                    "the first time PokeUDS connects."),
        ]
        if images:
            for image in images:
                tag = "recommended" if best and image.path == best.path else BUILDS.get(image.build, image.build)
                rows.append(ft.Row([ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED, color=t.MUTED, size=18),
                                    t.label(image.name, color=t.TEXT), t.pill(tag, t.GOOD if tag == "recommended" else t.MUTED)],
                                   spacing=8))
        else:
            rows.append(t.label("No firmware downloaded yet. Download the released images, or install a .bin you already have."))
        downloading = busy and self.app.run_kind == "board" and not self.checking and not self.installing
        rows += [
            ft.Row([t.primary("Download the firmware", self._download, ft.Icons.CLOUD_DOWNLOAD, disabled=busy),
                    t.secondary("Install from a file...", self._install_file, ft.Icons.FILE_OPEN, disabled=busy),
                    t.secondary("Open the folder", lambda e: open_folder(str(board.firmware_dir())), ft.Icons.FOLDER_OPEN),
                    ft.ProgressRing(width=18, height=18, visible=downloading)], spacing=8, wrap=True),
            t.notice("Installing writes a merged image at 0x0, which also erases keys stored on the board. PokeUDS keeps "
                     "your console key on this computer, so nothing is lost here; Azahar users store theirs again. "
                     "If the board is not detected, hold BOOT while plugging it in, then release it.", t.MUTED),
        ]
        return t.card(*rows, spacing=12)

    # -- actions --------------------------------------------------------------------------------

    def _run(self, args: list[str], *, check: str = "", install: str = "") -> None:
        self.checking, self.installing = check, install

        def event(ev: dict) -> None:
            if ev.get("event") == "board":
                self.found[ev["port"]] = ev
                self.failed.discard(ev["port"])

        def exited(code: int) -> None:
            if check and code != 0:
                self.failed.add(check)
                self.found.pop(check, None)
            if install:                       # the board runs something new: check it again
                self.found.pop(install, None)
                self.failed.discard(install)
            self.checking = self.installing = ""

        self.app.start("board", ["board", *args], on_event=event, on_exit=exited)

    def _check(self, port: str) -> None:
        self._run(["check", "--port", port], check=port)

    def _install(self, port: str, image) -> None:
        if image is not None:
            self._run(["flash", "--port", port, "--firmware", str(image)], install=port)

    async def _install_file(self, e) -> None:
        files = await self.app.picker.pick_files(dialog_title="Choose an esp32-uds-bridge .bin",
                                                 file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["bin"])
        if not (files and files[0].path):
            return
        ports = board.ports()
        if not ports:
            self.app.log = ["No board found. Plug it in (hold BOOT if it is not detected) and try again."]
            self.app.run_kind = "board"
            self.refresh()
            return
        self._install(self.app.settings.port if any(p.device == self.app.settings.port for p in ports) else ports[0].device,
                      files[0].path)

    def _download(self, e) -> None:
        self._run(["download"])

    def _use(self, port: str) -> None:
        self.app.settings.radio, self.app.settings.port = "esp32", port
        self.save()
