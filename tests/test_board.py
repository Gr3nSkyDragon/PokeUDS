"""Boards and firmware images: release picking, download checks, image checks, update choice."""

import hashlib
import json

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


def test_image_names():
    assert board.image_info("esp32-uds-bridge-fw1.8-esp32s3-gblink.bin").build == "gblink"
    assert board.image_info("esp32-uds-bridge-fw1.7-esp32s3.bin").version == "1.7"
    assert board.image_info("pokeldn-esp32s3.bin") is None


def test_recommended_and_update(tmp_path):
    for name in ("esp32-uds-bridge-fw1.7-esp32s3.bin", "esp32-uds-bridge-fw1.8-esp32s3-gblink.bin",
                 "esp32-uds-bridge-fw1.9-esp32s3-vcadapter.bin"):
        (tmp_path / name).write_bytes(b"x")
    assert board.recommended(tmp_path).name.endswith("fw1.8-esp32s3-gblink.bin")   # not the newer link-port build
    old = board.Identity("COM4", (1, 7), "1.7", "", "")
    assert board.update_for(old, tmp_path).version == "1.8"
    assert board.update_for(board.Identity("COM4", (1, 8), "1.8", "", "gblink"), tmp_path) is None
    assert board.update_for(board.Identity("COM4", (1, 8), "1.8", "", "vcadapter"), tmp_path).version == "1.9"


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


def _release(tag, names, draft=False):
    return {"tag_name": tag, "draft": draft, "assets": [
        {"name": n, "browser_download_url": f"https://github.com/Gr3nSkyDragon/VC-Adapter/releases/download/{tag}/{n}"}
        for n in names]}


def test_download_checks_sums(tmp_path, monkeypatch):
    image = s3_image()
    name = "esp32-uds-bridge-fw1.8-esp32s3-gblink.bin"
    releases = [_release("v2.0", [name], draft=True), _release("v1.8", [name, "SHA256SUMS", "uds-esp32-setup.exe"])]
    files = {"SHA256SUMS": f"{hashlib.sha256(image).hexdigest()}  {name}\n".encode(), name: image}

    def get(url, limit):
        return json.dumps(releases).encode() if url == board.RELEASES else files[url.rsplit("/", 1)[1]]

    monkeypatch.setattr(board, "_get", get)
    written = board.download(say=lambda _: None, folder=tmp_path)
    assert [p.name for p in written] == [name] and (tmp_path / name).read_bytes() == image
    files[name] = image[:-1] + b"\x00"
    (tmp_path / name).unlink()
    with pytest.raises(OSError, match="SHA256SUMS"):
        board.download(say=lambda _: None, folder=tmp_path)
    assert not (tmp_path / name).exists()


def test_no_release_with_images():
    with pytest.raises(OSError, match="no published release"):
        board.pick_release([_release("v1", ["README.md"])])
