"""ESP32-S3 boards: find them, read their firmware, fetch and flash esp32-uds-bridge.

PokeUDS runs VC-Adapter's ``firmware/esp32-uds-bridge`` unchanged. The image it recommends is the
GB-Link build (``esp32-uds-bridge-fw<version>-esp32s3-gblink.bin``, firmware 1.8): the 3DS raw radio
that every generation's trade uses, the optional SSD1306 screen, and GB-Link Switch LDN's Switch side
(which PokeUDS does not use; the board restarts into its 3DS mode on PokeUDS's first HELLO). The
VC-Adapter board build (``-vcadapter``, 1.9 beta) is for that board on a console's link port; 1.7
(``-esp32s3.bin``) is the 3DS side alone and works too.

A release image is merged (bootloader, partition table, app) and written at 0x0, so flashing erases
the keys the board stores. PokeUDS never stores keys on the board (it keeps CCMP on the PC), so that
costs nothing here; Azahar and uds-esp32-setup users have to store theirs again.

Every step is also a command (``python -m pokeuds board ...``), which is what the app runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .settings import data_dir

RELEASES = os.environ.get("POKEUDS_FIRMWARE_RELEASES", "https://api.github.com/repos/Gr3nSkyDragon/VC-Adapter/releases")
RELEASES_PAGE = "https://github.com/Gr3nSkyDragon/VC-Adapter/releases"
IMAGE_RE = re.compile(r"^esp32-uds-bridge-fw(?P<version>\d+\.\d+)-esp32s3(?:-(?P<build>[a-z]+))?\.bin$")
RECOMMENDED_BUILD = "gblink"
IMAGE_BYTES = 8 << 20          # an S3 merged image is well under the 8 MB flash of the boards it targets
FLASH_BAUD = "460800"

# USB ids: the S3's own USB Serial/JTAG, and the USB-UART bridges dev boards put on their UART port.
ESPRESSIF_VID, ESPRESSIF_JTAG_PID = 0x303A, 0x1001
BRIDGES = {(0x10C4, 0xEA60): "CP210x", (0x1A86, 0x7523): "CH340", (0x1A86, 0x55D3): "CH343",
           (0x0403, 0x6001): "FTDI", (0x0403, 0x6010): "FTDI"}


def firmware_dir() -> Path:
    return data_dir() / "firmware"


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
            found.append(Port(p.device, "ESP32-S3 native USB", True))
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


# -- firmware images ------------------------------------------------------------------------------

@dataclass
class Image:
    path: Path
    version: str
    build: str

    @property
    def name(self) -> str:
        return self.path.name


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", version))


def image_info(path: str | Path) -> Image | None:
    m = IMAGE_RE.match(Path(path).name)
    return Image(Path(path), m["version"], m["build"] or "") if m else None


def local_images(folder: Path | None = None) -> list[Image]:
    """Downloaded images, newest first, the recommended build first among equals."""
    folder = folder or firmware_dir()
    images = [i for p in (folder.glob("*.bin") if folder.is_dir() else []) if (i := image_info(p))]
    return sorted(images, key=lambda i: (version_key(i.version), i.build == RECOMMENDED_BUILD), reverse=True)


def recommended(folder: Path | None = None) -> Image | None:
    """The newest downloaded GB-Link build, else the newest 3DS-only image (1.7 and older)."""
    images = local_images(folder)
    return (next((i for i in images if i.build == RECOMMENDED_BUILD), None)
            or next((i for i in images if not i.build), None))


def update_for(identity: Identity, folder: Path | None = None) -> Image | None:
    """A downloaded image newer than the board's, of the board's own build (or the recommended one)."""
    best = recommended(folder)
    if identity.build == "vcadapter":
        best = next((i for i in local_images(folder) if i.build == "vcadapter"), None)
    if best and version_key(best.version) > identity.version:
        return best
    return None


def _get(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "PokeUDS", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as reply:      # noqa: S310 - fixed https URLs
        data = reply.read(limit + 1)
    if len(data) > limit:
        raise OSError(f"{url} is larger than expected")
    return data


def pick_release(releases: list) -> tuple[dict, dict[str, str]]:
    """The newest published release carrying an esp32-uds-bridge image, and its image assets."""
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") or not isinstance(release.get("assets"), list):
            continue
        files = {a["name"]: a.get("browser_download_url", "") for a in release["assets"]
                 if isinstance(a, dict) and isinstance(a.get("name"), str)}
        images = {n: u for n, u in files.items() if IMAGE_RE.match(n) and u.startswith("https://github.com/")}
        if images:
            sums = files.get("SHA256SUMS", "")
            return release, {**images, **({"SHA256SUMS": sums} if sums.startswith("https://github.com/") else {})}
    raise OSError(f"no published release carries an esp32-uds-bridge image ({RELEASES_PAGE})")


def download(say=print, folder: Path | None = None) -> list[Path]:
    """Fetch the S3 images of the newest VC-Adapter release into the firmware folder.

    When the release has a SHA256SUMS, every image must match it before anything is written."""
    folder = folder or firmware_dir()
    releases = json.loads(_get(RELEASES, 2_000_000))
    if not isinstance(releases, list):
        raise OSError("the release listing holds no releases")
    release, assets = pick_release(releases)
    sums = {}
    if "SHA256SUMS" in assets:
        for line in _get(assets.pop("SHA256SUMS"), 100_000).decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) == 2:
                sums[parts[1].lstrip("*")] = parts[0].lower()
    images = {}
    for name, url in assets.items():
        say(f"Downloading {name} from {release.get('tag_name', '?')}")
        images[name] = _get(url, IMAGE_BYTES)
        if sums and hashlib.sha256(images[name]).hexdigest() != sums.get(name):
            raise OSError(f"{name} does not match the release's SHA256SUMS; nothing was written")
    if not sums:
        say("The release has no SHA256SUMS; the images are checked as ESP32-S3 images before flashing.")
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in images.items():
        path = folder / name
        part = folder / (name + ".part")
        part.write_bytes(data)
        part.replace(path)
        written.append(path)
    return written


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


def flash(port: str, firmware: str | Path, say=print) -> None:
    """Detect the chip, check the image against it and write it at 0x0 on one connection."""
    import esptool

    firmware = Path(firmware)
    if not firmware.is_file():
        raise esptool.FatalError(f"{firmware} does not exist")
    with esptool.detect_chip(port) as chip:
        if chip.CHIP_NAME != "ESP32-S3":
            raise esptool.FatalError(f"this is an {chip.CHIP_NAME}; esp32-uds-bridge runs on an ESP32-S3")
        check_image(firmware, chip)
        say(f"Writing {firmware.name} to the {chip.CHIP_NAME} on {port} (this erases keys stored on the board)")
        esptool.main(["--baud", FLASH_BAUD, "--after", "hard-reset", "write-flash", "0x0", str(firmware.resolve())], esp=chip)
    say("Done. The board restarts into the new firmware.")
