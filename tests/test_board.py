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
    assert board.IMAGES[chip][2] == (chip == "ESP32")         # alpha: only the classic ESP32 (the S3, C3, C6 traded)


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


# -- a new board, an old port ----------------------------------------------------------------------
# A C3 plugged in while the settings still named the S3's COM9: the Board page found and checked the C3 on COM3, but
# the Trade page kept COM9, and its Check sat on that empty port for the whole 1.8 restart allowance.

@pytest.fixture
def c3_on_com3(monkeypatch):
    """One native-USB board attached, on COM3; opening any port fails, as an absent COM9 does."""
    import serial
    from serial.tools import list_ports

    monkeypatch.setattr(list_ports, "comports", lambda: [FakePort("COM3", 0x303A, 0x1001)])
    tries = []

    def absent(*a, **k):
        tries.append(a[0] if a else k.get("port"))
        raise serial.SerialException("could not open port 'COM9': FileNotFoundError")

    monkeypatch.setattr(serial, "Serial", absent)
    monkeypatch.setattr(esp32, "UNOPENED_TIMEOUT", 0.3)
    monkeypatch.setattr(esp32, "REOPEN_DELAY", 0.05)
    return tries


def test_a_port_with_no_board_fails_soon_and_names_the_board_that_is_there(c3_on_com3):
    import time

    began = time.monotonic()
    with pytest.raises(RuntimeError) as error:
        esp32.Esp32Radio("COM9").open()
    assert time.monotonic() - began < esp32.OPEN_TIMEOUT / 5         # not the 25 s a restarting 1.8 board gets
    assert "No board on COM9" in str(error.value) and "COM3" in str(error.value)
    assert c3_on_com3 and set(c3_on_com3) == {"COM9"}                  # never quietly trades on another board


def test_an_attached_port_that_will_not_open_is_called_busy(c3_on_com3):
    with pytest.raises(RuntimeError, match="another program"):
        esp32.Esp32Radio("COM3").open()


def test_probe_answers_with_the_reason_instead_of_a_traceback(c3_on_com3, capsys):
    from pokeuds import cli

    assert cli.main(["probe", "--radio", "esp32:COM9"]) == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("No board on COM9") and "COM3" in out[-1]


def test_checking_or_installing_a_board_points_trades_at_it():
    from pokeuds.app.settings import Settings

    s = Settings(radio="esp32", port="COM9")
    assert board.stale(s, ["COM3"]) and not board.stale(s, ["COM3", "COM9"])
    assert board.adopt(s, "COM3") and s.port == "COM3" and not board.stale(s, ["COM3"])
    assert not board.adopt(s, "COM3")                                   # already: nothing to save
    found = Settings(radio="esp32", port="")                            # "found by its USB id" names it too
    assert board.adopt(found, "COM3") and found.port == "COM3" and not board.stale(Settings(radio="esp32"), [])
    for radio, port in (("ldnd", r"\.\pipe\ldnd"), ("demo", "red")):   # another connection the user chose stays
        other = Settings(radio=radio, port=port)
        assert not board.adopt(other, "COM3") and other.port == port and not board.stale(other, [])


def test_the_board_page_hands_its_board_to_the_trade_page(tmp_path, monkeypatch):
    pytest.importorskip("flet")
    from gui.app import App
    from gui.views.board import BoardView
    from gui.views.trade import TradeView
    from pokeuds.app.settings import Settings

    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(esp32, "board_ports", lambda: ["COM3"])
    # A Game Boy game: the Gen 6/7 builder would fill the shared PKHeX lists other tests set themselves.
    app = App(None, Settings(radio="esp32", port="COM9", last_game="rby"))
    runs = []
    monkeypatch.setattr(app, "start", lambda kind, args, **kw: runs.append((args, kw)))
    view = BoardView(app)
    view._check("COM3")
    args, kw = runs[-1]
    assert args == ["board", "check", "--port", "COM3"]
    kw["on_event"]({"event": "board", "port": "COM3", "firmware": "2.0", "build": "uds", "mac": "88:56:A6:3A:88:F0",
                    "update": "", "advice": ""})
    kw["on_exit"](0)
    assert app.settings.port == "COM3" and Settings.load().port == "COM3"          # saved, so the next start has it
    TradeView(app)._probe(None)
    assert runs[-1][0] == ["probe", "--radio", "esp32:COM3"] and runs[-1][1]["timeout"]

    import gui.views.trade as trade_page

    monkeypatch.setattr(trade_page, "esp32_ports", lambda: ["COM3"])
    app.settings.port = "COM9"                                           # plugged in, Board page not visited
    trade = TradeView(app)
    shown = repr(trade.connection())
    assert "no board on it now" in shown and "Use COM3" in shown
    trade._use_port("COM3")
    assert app.settings.port == "COM3" and "no board on it now" not in repr(trade.connection())

    app.settings.port = "COM9"
    view._install("COM3")
    runs[-1][1]["on_exit"](1)                                            # a failed install hands nothing over
    assert app.settings.port == "COM9"
    view._install("COM3")
    runs[-1][1]["on_exit"](0)
    assert app.settings.port == "COM3"


def test_a_check_that_never_answers_is_stopped_with_a_reason(tmp_path, monkeypatch):
    """Whatever the child does, Check ends: past its time it is stopped, and the reason is the log's last word."""
    pytest.importorskip("flet")
    import threading

    import gui.app as gui_app

    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path / "data"))
    done = threading.Event()

    class Stuck:                                      # a child that never says anything until it is stopped
        def __init__(self, args, *, on_line, on_event, on_exit):
            self.on_exit, self.alive = on_exit, True

        def running(self):
            return self.alive

        def stop(self):
            self.alive = False
            self.on_exit(130)
            done.set()

    monkeypatch.setattr(gui_app, "Run", Stuck)
    app = gui_app.App(None, gui_app.Settings(radio="esp32", port="COM9"))
    app.start("probe", ["probe", "--radio", "esp32:COM9"], timeout=0.1, timeout_note="no answer in time")
    assert done.wait(5) and not app.busy()
    assert app.log[-2:] == ["no answer in time", "(exited with code 130)"]
