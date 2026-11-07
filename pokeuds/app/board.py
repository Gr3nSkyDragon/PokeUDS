"""ESP32-S3 boards: find them, read their firmware, install esp32-uds-bridge.

PokeUDS runs VC-Adapter's ``firmware/esp32-uds-bridge`` unchanged, and carries the one image it uses, as
pokeldn carries its firmware: firmware 1.8, the GB-Link build (``data/firmware/``, VC-Adapter's
release ``POC_v2``, pinned by its SHA-256). That is the build for a board on a PC (Azahar, mGBA,
PokeUDS): the 3DS raw radio every generation's trade uses, the optional SSD1306 screen, and GB-Link
Switch LDN's Switch side (which PokeUDS does not use; the board restarts into its 3DS mode on
PokeUDS's first HELLO). PokeUDS fetches no firmware. VC-Adapter's own firmware for its standalone
board (2.0, and the 1.9 beta before it; HELLO marks it with the link-port feature) is not for a
PC-hosted board, and PokeUDS offers 1.8 in its place.

A release image is merged (bootloader, partition table, app) and written at 0x0, so flashing erases
the keys the board stores. PokeUDS never stores keys on the board (it keeps CCMP on the PC), so that
costs nothing here; Azahar and uds-esp32-setup users have to store theirs again.

Every step is also a command (``python -m pokeuds board ...``), which is what the app runs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

FIRMWARE_VERSION = (1, 8)
FIRMWARE = "1.8"
FIRMWARE_NAME = "esp32-uds-bridge-fw1.8-esp32s3-gblink.bin"
FIRMWARE_SHA256 = "cd61e804d56011f875c1c92af0aa1cb9ed420e374c0570779f81162287346fdc"
IMAGE_BYTES = 8 << 20          # an S3 merged image is well under the 8 MB flash of the boards it targets
FLASH_BAUD = "460800"

# USB ids: the S3's own USB Serial/JTAG, and the USB-UART bridges dev boards put on their UART port.
ESPRESSIF_VID, ESPRESSIF_JTAG_PID = 0x303A, 0x1001
BRIDGES = {(0x10C4, 0xEA60): "CP210x", (0x1A86, 0x7523): "CH340", (0x1A86, 0x55D3): "CH343",
           (0x0403, 0x6001): "FTDI", (0x0403, 0x6010): "FTDI"}


# -- boards ---------------------------------------------------------------------------------------

@dataclass
class Port:
    device: str
    description: str
    native: bool            # the S3's own USB (where esp32-uds-bridge speaks); a bridge is its UART

    def label(self) -> str:
        return f"{self.device} ({self.description})"


def ports() -> list[Port]:
    from serial.tools import list_ports

    found = []
    for p in list_ports.comports():
        if (p.vid, p.pid) == (ESPRESSIF_VID, ESPRESSIF_JTAG_PID):
            found.append(Port(p.device, "ESP32 native USB", True))
        elif (p.vid, p.pid) in BRIDGES:
            found.append(Port(p.device, f"{BRIDGES[(p.vid, p.pid)]} USB-UART; flash only", False))
    return sorted(found, key=lambda p: (not p.native, p.device))


@dataclass
class Identity:
    port: str
    version: tuple[int, int]
    firmware: str
    mac: str
    build: str             # "gblink", "vcadapter" or "" (1.7 and older carry no build)


def identify(port: str) -> Identity:
    """HELLO the board (restarting 1.8 into its 3DS mode if needed) and read what it is."""
    from ..radio.esp32 import Esp32Radio
    from ..radio import esp32_wire as wire

    radio = Esp32Radio(port)
    radio.open()
    try:
        build = ""
        if radio.version >= (1, 8):
            build = "vcadapter" if radio.features & wire.FEATURE_LINK_PORT else "gblink"
        return Identity(radio.port_name, radio.version, f"{radio.version[0]}.{radio.version[1]}",
                        ":".join(f"{b:02X}" for b in radio.factory_mac), build)
    finally:
        radio.close()


# -- the firmware PokeUDS carries ---------------------------------------------------------------

def firmware_path() -> Path:
    return Path(__file__).with_name("data") / "firmware" / FIRMWARE_NAME


def firmware() -> Path:
    """The bundled 1.8 image, after checking it is the one PokeUDS was released with."""
    path = firmware_path()
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing: reinstall PokeUDS")
    if hashlib.sha256(path.read_bytes()).hexdigest() != FIRMWARE_SHA256:
        raise OSError(f"{path} is not the firmware PokeUDS shipped with (its SHA-256 differs): reinstall PokeUDS")
    return path


def advice(identity: Identity) -> str:
    """Why to install PokeUDS's 1.8 on this board, or "" when it already runs a build PokeUDS is made for."""
    if identity.build == "vcadapter":
        return (f"This is VC-Adapter's standalone board firmware ({identity.firmware}). PokeUDS uses {FIRMWARE}, the "
                "build for a board on a computer; installing replaces it.")
    if identity.version < FIRMWARE_VERSION:
        return f"PokeUDS includes firmware {FIRMWARE} (GB-Link build), newer than this board's {identity.firmware}."
    return ""


# -- flashing -------------------------------------------------------------------------------------

def check_image(path: str | Path, chip=None) -> None:
    """Refuse anything but a merged ESP32-S3 image: its bootloader at 0x0 with the S3's chip id,
    and esptool's own checksum and digest over that bootloader."""
    import esptool
    from esptool.bin_image import LoadFirmwareImage
    from esptool.targets import ESP32S3ROM

    chip = chip or ESP32S3ROM
    data = Path(path).read_bytes()
    if len(data) > IMAGE_BYTES:
        raise esptool.FatalError(f"{path} is too large to be a firmware image")
    if len(data) < 24 or data[0] != 0xE9 or int.from_bytes(data[12:14], "little") != chip.IMAGE_CHIP_ID:
        raise esptool.FatalError(f"{path} is not a merged ESP32-S3 image (write a release .bin, not the app alone)")
    try:
        image = LoadFirmwareImage(chip.CHIP_NAME, data)
        image.verify()
    except Exception as error:
        raise esptool.FatalError(f"{path} is not a valid ESP32-S3 image: {error}") from error
    if image.checksum != image.calculate_checksum() or (image.append_digest and image.stored_digest != image.calc_digest):
        raise esptool.FatalError(f"{path}: the image's checksum does not match")


def flash(port: str, image: str | Path, say=print) -> None:
    """Detect the chip, check the image against it and write it at 0x0 on one connection."""
    import esptool

    image = Path(image)
    if not image.is_file():
        raise esptool.FatalError(f"{image} does not exist")
    with esptool.detect_chip(port) as chip:
        if chip.CHIP_NAME != "ESP32-S3":
            raise esptool.FatalError(f"this is an {chip.CHIP_NAME}; esp32-uds-bridge runs on an ESP32-S3")
        check_image(image, chip)
        say(f"Writing {image.name} to the {chip.CHIP_NAME} on {port} (this erases keys stored on the board)")
        esptool.main(["--baud", FLASH_BAUD, "--after", "hard-reset", "write-flash", "0x0", str(image.resolve())], esp=chip)
    say("Done. The board restarts into the new firmware.")
