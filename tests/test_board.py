"""Boards and firmware: the image PokeUDS carries, image checks, what to install on which board."""

import hashlib

import pytest

esptool = pytest.importorskip("esptool")

from pokeuds.app import board  # noqa: E402


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


def test_the_included_firmware_is_1_8_gblink():
    path = board.firmware()
    assert path.name == "esp32-uds-bridge-fw1.8-esp32s3-gblink.bin" and board.FIRMWARE == "1.8"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == board.FIRMWARE_SHA256
    board.check_image(path)                     # a real merged ESP32-S3 image


def test_a_changed_firmware_file_is_refused(tmp_path, monkeypatch):
    fake = tmp_path / board.FIRMWARE_NAME
    fake.write_bytes(s3_image())
    monkeypatch.setattr(board, "firmware_path", lambda: fake)
    with pytest.raises(OSError, match="SHA-256"):
        board.firmware()
    monkeypatch.setattr(board, "firmware_path", lambda: tmp_path / "gone.bin")
    with pytest.raises(FileNotFoundError):
        board.firmware()


def test_what_to_install():
    assert board.advice(board.Identity("COM4", (1, 8), "1.8", "", "gblink")) == ""
    assert "newer" in board.advice(board.Identity("COM4", (1, 7), "1.7", "", ""))
    for version in ((1, 9), (2, 0)):              # VC-Adapter's standalone firmware: PokeUDS offers 1.8 instead
        text = board.advice(board.Identity("COM4", version, f"{version[0]}.{version[1]}", "", "vcadapter"))
        assert "standalone" in text and "1.8" in text


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
