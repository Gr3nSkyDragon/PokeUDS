"""Boards and firmware: the images PokeUDS carries (one per chip), image checks, what to install on which board, and
how a classic ESP32's USB-UART bridge is found and opened."""

import hashlib

import pytest

esptool = pytest.importorskip("esptool")

from pokeuds.app import board  # noqa: E402
from pokeuds.radio import esp32  # noqa: E402


def s3_image() -> bytes:
    """A tiny but well-formed ESP32-S3 image, as esptool writes one."""
    import os
    import tempfile

    from esptool.bin_image import ESP32S3FirmwareImage, ImageSegment

    img = ESP32S3FirmwareImage()
    img.entrypoint = 0x403C8000
    seg = ImageSegment(0x403C8000, b"\x00" * 64)
    seg.name = ".iram"
    img.segments = [seg]
    img.append_digest = True
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "image.bin")
        img.save(path)
        with open(path, "rb") as f:
            return f.read()


@pytest.mark.parametrize("chip", board.CHIPS)
def test_each_chip_has_its_2_0_uds_image(chip):
    from esptool.targets import CHIP_DEFS

    path = board.firmware(chip)
    assert path.name == f"esp32-uds-bridge-2.0-uds-{board.IMAGES[chip][0]}.bin" and board.FIRMWARE == "2.0-uds"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == board.IMAGES[chip][1]
    rom = next(c for c in CHIP_DEFS.values() if c.CHIP_NAME == chip)
    board.check_image(path, rom)                 # a real merged image for that chip (the classic's bootloader at 0x1000)
    other = next(c for c in CHIP_DEFS.values() if c.CHIP_NAME in board.CHIPS and c.CHIP_NAME != chip)
    with pytest.raises(esptool.FatalError):
        board.check_image(path, other)           # never another chip's
    assert board.IMAGES[chip][2] == (chip != "ESP32-S3")      # alpha everywhere but the S3


def test_a_changed_firmware_file_is_refused(tmp_path, monkeypatch):
    fake = tmp_path / board.image_name("ESP32-C3")
    fake.write_bytes(s3_image())
    monkeypatch.setattr(board, "firmware_path", lambda chip="ESP32-S3": fake)
    with pytest.raises(OSError, match="SHA-256"):
        board.firmware("ESP32-C3")
    monkeypatch.setattr(board, "firmware_path", lambda chip="ESP32-S3": tmp_path / "gone.bin")
    with pytest.raises(FileNotFoundError):
        board.firmware("ESP32-C3")
    with pytest.raises(OSError, match="no build for the ESP32-H2"):
        board.firmware("ESP32-H2")
    assert all(isinstance(v, str) for v in board.bundled().values())


def test_what_to_install():
    assert board.advice(board.Identity("COM4", (2, 0), "2.0", "", "uds")) == ""
    assert "newer" in board.advice(board.Identity("COM4", (1, 8), "1.8", "", "gblink"))
    assert "newer" in board.advice(board.Identity("COM4", (1, 7), "1.7", "", ""))
    for version, build in (((1, 9), "vcadapter"), ((2, 0), "aio")):    # the standalone board's line
        text = board.advice(board.Identity("COM4", version, f"{version[0]}.{version[1]}", "", build))
        assert "standalone" in text and "2.0-uds" in text


def test_image_check(tmp_path):
    good = tmp_path / "good.bin"
    good.write_bytes(s3_image())
    board.check_image(good)
    bad = bytearray(s3_image())
    bad[12] = 0                                 # the classic ESP32's chip id
    (tmp_path / "esp32.bin").write_bytes(bytes(bad))
    with pytest.raises(esptool.FatalError, match="not a merged ESP32-S3 image"):
        board.check_image(tmp_path / "esp32.bin")
    bad = bytearray(s3_image())
    bad[40] ^= 1
    (tmp_path / "corrupt.bin").write_bytes(bytes(bad))
    with pytest.raises(esptool.FatalError, match="checksum"):
        board.check_image(tmp_path / "corrupt.bin")


class FakePort:
    def __init__(self, device, vid, pid):
        self.device, self.vid, self.pid = device, vid, pid


def test_a_classic_esp32s_bridge_is_found_after_native_usb(monkeypatch):
    from serial.tools import list_ports

    cp210x = FakePort("COM7", 0x10C4, 0xEA60)
    native = FakePort("COM4", 0x303A, 0x1001)
    other = FakePort("COM1", 0x8086, 0x1234)
    monkeypatch.setattr(list_ports, "comports", lambda: [other, cp210x, native])
    assert esp32.find_port() == "COM4" and not esp32.is_bridge("COM4")
    monkeypatch.setattr(list_ports, "comports", lambda: [other, cp210x])
    assert esp32.find_port() == "COM7" and esp32.is_bridge("COM7")
    assert [p.device for p in board.ports()] == ["COM7"] and "classic ESP32" in board.ports()[0].description


def test_a_bridge_opens_at_921600_without_resetting_the_board(monkeypatch):
    """A bridge's DTR and RTS drive EN and GPIO0: both are low before the port opens; native USB opens as before."""
    import serial

    made = []

    class Port:
        def __init__(self, port=None, baudrate=9600, **kw):
            self.port, self.baudrate, self.kw = port, baudrate, kw
            self.dtr = self.rts = True
            self.opened_with = None
            made.append(self)

        def open(self):
            self.opened_with = (self.port, self.dtr, self.rts)

    monkeypatch.setattr(serial, "Serial", Port)
    bridge = esp32.open_serial("COM7", esp32.BRIDGE_BAUD, True)
    assert bridge.baudrate == 921600 and bridge.opened_with == ("COM7", False, False)
    native = esp32.open_serial("COM4", esp32.NATIVE_BAUD, False)
    assert native.port == "COM4" and native.opened_with is None             # opened by its constructor, as before
