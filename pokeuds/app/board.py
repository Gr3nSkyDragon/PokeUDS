"""ESP32 boards: find them, read their firmware, install esp32-uds-bridge (``FIRMWARE``) for their chip.

PokeUDS runs VC-Adapter's ``firmware/esp32-uds-bridge`` unchanged and carries the images it uses
(``data/firmware/``, each pinned by its SHA-256), built from VC-Adapter (github.com/Gr3nSkyDragon/VC-Adapter,
``firmware/esp32-uds-bridge``, AGPL-3.0), where their source is. The bundled **2.1-uds** is the line for a board on a
computer whose host only speaks 3DS (Azahar, PokeUDS): the 3DS raw radio every generation's trade uses and the
optional SSD1306 screen, with no Switch side, so it boots in 3DS mode and a HELLO never restarts it (HELLO_ACK's
no-Switch bit, 0x04).

**2.1-ldn** is what 2.0 called uds: both sides, for mGBA-LDN. PokeUDS works with it (and with 2.0-uds and 1.8), but
such a board restarts into its 3DS mode on PokeUDS's first HELLO; the radio then finds its port again by its USB
serial number, since macOS may name it anew, and the Board page offers 2.1-uds as optional. ``identify`` calls a
2.0-uds board "ldn", for what it carries. One image per chip:

| chip | image | host link | state |
|---|---|---|---|
| ESP32-S3 | ``esp32-uds-bridge-2.1-uds-esp32s3.bin`` | native USB (``303A:1001``) | the chip 1.8 ran on |
| ESP32-C3, ESP32-C6 | ``...-esp32c3.bin``, ``...-esp32c6.bin`` | native USB (``303A:1001``) | traded on (0.1.5) |
| ESP32 (classic) | ``...-esp32.bin`` (bootloader at 0x1000 inside it) | its board's USB-UART bridge (CP210x, CH340...), 921600 baud | alpha |
| ESP32-S2 | ``...-esp32s2.bin`` (bootloader at 0x1000 inside it) | TinyUSB CDC (``303A:4001``); flashed from its ROM's download mode (``303A:0002``) | alpha |

The S3, C3 and C6 share one USB id, so flashing asks the chip (esptool) and writes that chip's image. VC-Adapter's
standalone-board line, **2.1-aio** (2.0-aio, 1.9 before it; HELLO marks it with the link-port feature), is not for a
board on a computer, and PokeUDS offers 2.1-uds in its place.

A release image is merged (bootloader, partition table, app) and written at 0x0, so flashing erases the keys the
board stores. PokeUDS never stores keys on the board (it keeps CCMP on the PC), so that costs nothing here; Azahar
and uds-esp32-setup users have to store theirs again.

Every step is also a command (``python -m pokeuds board ...``), which is what the app runs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

FIRMWARE_VERSION = (2, 1)
FIRMWARE = "2.1-uds"
# esptool's chip name -> (the image's chip part, its SHA-256, alpha: not yet seen working on that chip).
IMAGES = {
    "ESP32-S3": ("esp32s3", "88b1f15ba70bcd464b45be864b821b30560959e413d1a5c4fa3afd94e1ef5f5d", False),
    "ESP32-C3": ("esp32c3", "3283c5fa7edba9a3b1b03ca08f128cd24b6f89931ccc942efc41b2f64bdb6540", False),
    "ESP32-C6": ("esp32c6", "996ba07d5cc0ca2aa4ecea9fdbeca82e2c8fef51d568344414cc5b5c88b2223c", False),
    "ESP32": ("esp32", "4b2c7e4d240ab4f7baebe6cc478313da015e0ac05f3424057dbda5c235906375", True),
    "ESP32-S2": ("esp32s2", "6ca0194a0f07e70a1d3c181a0560553ef2a3e655df6781125268a15b22349640", True),
}
CHIPS = tuple(IMAGES)
IMAGE_BYTES = 8 << 20          # a merged image is well under the flash of the boards it targets
FLASH_BAUD = "460800"
BOOTLOADER_OFFSET = {"ESP32": 0x1000, "ESP32-S2": 0x1000}

# USB ids: the native USB Serial/JTAG of the S3, C3 and C6, the S2's TinyUSB port and download mode, and the USB-UART
# bridges boards put on a UART port (the classic ESP32's only way in).
from ..radio.esp32 import BRIDGES, S2_CDC_USB, S2_ROM_USB, is_native_radio_port, port_usb_id  # noqa: E402


def image_name(chip: str) -> str:
    return f"esp32-uds-bridge-{FIRMWARE}-{IMAGES[chip][0]}.bin"


# -- boards ---------------------------------------------------------------------------------------

@dataclass
class Port:
    device: str
    description: str
    native: bool            # an S3, C3, C6 or S2's own USB; else a USB-UART bridge (a classic ESP32's way in)
    rom: bool = False       # an S2 in its ROM's download mode: install only
    chip: str = ""          # the chip, when the USB id says (the S2)

    def label(self) -> str:
        return f"{self.device} ({self.description})"


def ports() -> list[Port]:
    from serial.tools import list_ports

    found = []
    for p in list_ports.comports():
        usb_id = (p.vid, p.pid)
        if usb_id == S2_ROM_USB:
            found.append(Port(p.device, "ESP32-S2 download mode", True, rom=True, chip="ESP32-S2"))
        elif is_native_radio_port(p):
            if usb_id == S2_CDC_USB:
                found.append(Port(p.device, "ESP32-S2 native USB CDC", True, chip="ESP32-S2"))
            else:
                found.append(Port(p.device, "ESP32 native USB (S3, C3 or C6)", True))
        elif (p.vid, p.pid) in BRIDGES:
            found.append(Port(p.device, f"{BRIDGES[(p.vid, p.pid)]} USB-UART (a classic ESP32's port)", False))
    return sorted(found, key=lambda p: (not p.native, p.device))


def adopt(settings, port: str) -> bool:
    """Point trades at the board on ``port``, after the Board page checked or installed it: the ESP32 connection
    (the Trade page's) then uses that board, not a port saved for another one. A USB Wi-Fi adapter, plugin or demo
    radio the user chose is left alone. -> whether the settings changed (the caller saves them)."""
    if settings.radio != "esp32" or not port or settings.port == port:
        return False
    settings.port = port
    return True


def stale(settings, attached: list[str]) -> bool:
    """Whether the ESP32 connection names a port no board is on now (``attached``: the boards' ports)."""
    return settings.radio == "esp32" and bool(settings.port) and settings.port not in attached


def fallback(settings, attached: list[str]) -> str:
    """The board to use when the saved one is not plugged in: the only ESP32 attached, as pokeldn does
    (gui/app.py ``radio_port``). "" when the saved board is there, when none is, or when several are: opening a port
    resets its board, so PokeUDS does not try them in turn."""
    return attached[0] if stale(settings, attached) and len(attached) == 1 else ""


def follow(settings, attached: list[str]) -> str:
    """Point the ESP32 connection at ``fallback``'s board -> its port, or "" when nothing changed (the caller saves)."""
    port = fallback(settings, attached)
    if port:
        settings.port = port
    return port


@dataclass
class Identity:
    port: str
    version: tuple[int, int]
    firmware: str
    mac: str
    build: str             # "uds", "ldn", "aio" (2.0 on; 2.0's uds is "ldn"); "gblink", "vcadapter" (1.8, 1.9); ""


STANDALONE = ("aio", "vcadapter")   # the standalone VC-Adapter board's lines (2.0 on, 1.9)

# What each build is, for the Board page and ``board check``.
BUILDS = {"uds": "uds line, 3DS side only", "ldn": "ldn line, 3DS and Switch sides",
          "aio": "aio line, VC-Adapter standalone board", "gblink": "GB-Link build, 3DS and Switch sides",
          "vcadapter": "VC-Adapter standalone build", "": "3DS only"}


def describe(identity: Identity) -> str:
    """The board's build in words: 2.0's both-sides line was called uds then, so it is named that way."""
    if identity.build == "ldn" and identity.version < (2, 1):
        return "uds line, 3DS and Switch sides (2.1 calls it ldn)"
    return BUILDS.get(identity.build, identity.build)


def has_switch(version: tuple[int, int], build: str) -> bool:
    """Whether firmware of this version and build carries the Switch side (and so restarts on PokeUDS's HELLO).
    ``build`` may also be a line's name as an image carries it: 2.0's "uds" had both sides, 2.1's has one."""
    return build in ("ldn", "gblink") or (build == "uds" and version < (2, 1))


def identify(port: str) -> Identity:
    """HELLO the board (restarting 1.8 into its 3DS mode if needed) and read what it is."""
    from ..radio.esp32 import Esp32Radio
    from ..radio import esp32_wire as wire

    usb_id = port_usb_id(port)
    if usb_id == S2_ROM_USB:
        raise RuntimeError("The ESP32-S2 is in download mode. Install firmware or press RESET to run the existing image.")
    radio = Esp32Radio(port)
    radio.open()
    try:
        build = ""
        board_line = bool(radio.features & wire.FEATURE_LINK_PORT)       # the standalone board's line
        if radio.version >= (2, 0):          # no Switch side: 2.1-uds; 2.0 never set that bit, its uds had both sides
            build = "aio" if board_line else "uds" if radio.features & wire.FEATURE_NO_SWITCH else "ldn"
        elif radio.version >= (1, 8):
            build = "vcadapter" if board_line else "gblink"
        return Identity(radio.port_name, radio.version, f"{radio.version[0]}.{radio.version[1]}",
                        ":".join(f"{b:02X}" for b in radio.factory_mac), build)
    finally:
        radio.close()


# -- the firmware PokeUDS carries ---------------------------------------------------------------

def firmware_path(chip: str = "ESP32-S3") -> Path:
    return Path(__file__).with_name("data") / "firmware" / image_name(chip)


def firmware(chip: str = "ESP32-S3") -> Path:
    """The bundled image (``FIRMWARE``) for ``chip`` (esptool's name), after checking it is the one PokeUDS shipped with."""
    if chip not in IMAGES:
        raise OSError(f"esp32-uds-bridge has no build for the {chip}: it runs on the {', '.join(CHIPS)}")
    digest = IMAGES[chip][1]
    if digest is None:
        raise FileNotFoundError(f"The bundled {FIRMWARE} image for {chip} is pending. Use a VC-Adapter release image "
                                "with `pokeuds board flash --firmware IMAGE`.")
    path = firmware_path(chip)
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing: reinstall PokeUDS")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise OSError(f"{path} is not the firmware PokeUDS shipped with (its SHA-256 differs): reinstall PokeUDS")
    return path


def bundled() -> dict[str, Path | str]:
    """Each chip's image, or why it cannot be used."""
    out: dict[str, Path | str] = {}
    for chip in CHIPS:
        try:
            out[chip] = firmware(chip)
        except OSError as error:
            out[chip] = str(error)
    return out


def advice(identity: Identity) -> str:
    """Why to install PokeUDS's firmware on this board, or "" when it already runs a line PokeUDS works with."""
    if identity.build in STANDALONE:
        return (f"This is VC-Adapter's standalone board firmware ({identity.firmware}). PokeUDS uses {FIRMWARE}, the "
                "line for a board on a computer; installing replaces it.")
    if identity.version < FIRMWARE_VERSION:
        return f"PokeUDS includes firmware {FIRMWARE}, newer than this board's {identity.firmware}."
    if has_switch(identity.version, identity.build) and not has_switch(FIRMWARE_VERSION, FIRMWARE.split("-")[1]):
        return (f"Optional: this board's firmware ({identity.firmware}, {describe(identity)}) works, but restarts into its "
                f"3DS mode each time PokeUDS connects. PokeUDS's {FIRMWARE} has the 3DS side only and never restarts. "
                "Keep this one if the board is also used for mGBA-LDN's Switch side.")
    return ""


# -- flashing -------------------------------------------------------------------------------------

def check_image(path: str | Path, chip=None) -> None:
    """Refuse anything but a merged image for ``chip`` (esptool's chip class, default the S3): its bootloader, at
    0x0 (or 0x1000 on the classic ESP32 and S2), with that chip's id, and esptool's own checksum and digest over it."""
    import esptool
    from esptool.bin_image import LoadFirmwareImage
    from esptool.targets import ESP32S3ROM

    chip = chip or ESP32S3ROM
    name = chip.CHIP_NAME
    data = Path(path).read_bytes()
    if len(data) > IMAGE_BYTES:
        raise esptool.FatalError(f"{path} is too large to be a firmware image")
    start = BOOTLOADER_OFFSET.get(name, 0)
    head = data[start:]
    if len(head) < 24 or head[0] != 0xE9 or int.from_bytes(head[12:14], "little") != chip.IMAGE_CHIP_ID:
        raise esptool.FatalError(f"{path} is not a merged {name} image (write a release .bin for this chip, not the "
                                 "app alone)")
    try:
        image = LoadFirmwareImage(name, head)
        image.verify()
    except Exception as error:
        raise esptool.FatalError(f"{path} is not a valid {name} image: {error}") from error
    if image.checksum != image.calculate_checksum() or (image.append_digest and image.stored_digest != image.calc_digest):
        raise esptool.FatalError(f"{path}: the image's checksum does not match")


def flash(port: str, image: str | Path | None = None, say=print) -> None:
    """Detect the chip, pick its image (or check ``image`` against it) and write it at 0x0 on one connection."""
    import esptool

    if image is not None and not Path(image).is_file():
        raise esptool.FatalError(f"{image} does not exist")
    usb_id = port_usb_id(port)
    if usb_id == S2_CDC_USB:
        raise esptool.FatalError("Put the ESP32-S2 in download mode: hold BOOT, press and release RESET, then release BOOT. "
                                 "Refresh the Board page and choose the download port.")
    options = {"connect_mode": "no-reset"} if usb_id == S2_ROM_USB else {}
    with esptool.detect_chip(port, **options) as chip:
        name = chip.CHIP_NAME
        if name not in IMAGES:
            raise esptool.FatalError(f"this is an {name}; esp32-uds-bridge runs on the {', '.join(CHIPS)}")
        if image is None:
            try:
                image = firmware(name)
            except OSError as error:
                raise esptool.FatalError(str(error)) from error
        image = Path(image)
        check_image(image, chip)
        alpha = " (alpha: not yet tried on this chip)" if IMAGES[name][2] else ""
        say(f"Writing {image.name} to the {name} on {port}{alpha} (this erases keys stored on the board)")
        esptool.main(["--baud", FLASH_BAUD, "--after", "hard-reset", "write-flash", "0x0", str(image.resolve())], esp=chip)
    if name == "ESP32-S2":
        say("Done. Press RESET on the S2 if it stays in download mode, then look again and check its new port.")
    else:
        say("Done. The board restarts into the new firmware.")
