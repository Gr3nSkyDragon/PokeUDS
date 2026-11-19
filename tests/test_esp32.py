"""The ESP32 backend against a scripted serial port that answers like esp32-uds-bridge."""

import struct
import threading
import time

import pytest

from pokeuds.radio import esp32_wire as wire
from pokeuds.radio.esp32 import Esp32Radio


class FakeBoard:
    def __init__(self):
        self.out = bytearray()
        self.lock = threading.Lock()
        self.decoder = wire.Decoder()
        self.received: list[wire.Frame] = []

    def write(self, data):
        for frame in self.decoder.feed(data):
            self.received.append(frame)
            if frame.type == wire.Type.HELLO:
                self._reply(wire.Type.HELLO_ACK, frame.seq, bytes([1, 2, 3]) + bytes.fromhex("A0B1C2D3E4F5"))
            elif frame.type == wire.Type.PING:
                self._reply(wire.Type.PONG, frame.seq, frame.payload)
            else:  # Every other command, TX_FRAME included, gets a STATUS echoing its seq.
                self._reply(wire.Type.STATUS, frame.seq, bytes([frame.type]) + struct.pack("<i", 0))
                if frame.type == wire.Type.START:
                    self._reply(wire.Type.RX, 0, bytes([6, 0xC4, 0]) + bytes(30))

    def _reply(self, type_, seq, payload):
        with self.lock:
            self.out += wire.encode(type_, seq, 0, payload)

    def read(self, count):
        time.sleep(0.002)
        with self.lock:
            data = bytes(self.out[:count])
            del self.out[:count]
            return data

    def close(self):
        pass


@pytest.fixture
def board(monkeypatch):
    import serial

    fake = FakeBoard()
    monkeypatch.setattr(serial, "Serial", lambda *a, **k: fake)
    return fake


def test_session(board):
    radio = Esp32Radio("FAKE")
    radio.open()
    assert radio.firmware == "2.3 (protocol 1)" and radio.factory_mac == bytes.fromhex("A0B1C2D3E4F5")
    mac = bytes.fromhex("020000000001")
    radio.start(6, mac)
    radio.set_channel(11)
    radio.set_beacon(b"\x80\x00" + bytes(40))
    radio.send(b"\x08\x00" + bytes(2) + b"\xff" * 6 + bytes(20))  # NoDS broadcast data.
    rx = radio.recv(1.0)
    assert (rx.channel, rx.rssi, len(rx.mpdu)) == (6, -60, 30)
    radio.close()
    by_type = {f.type: f.payload for f in board.received}
    assert by_type[wire.Type.START] == bytes([6]) + mac + b"\x01"     # Channel, MAC, twin address.
    assert by_type[wire.Type.SET_CHANNEL] == bytes([11])
    assert by_type[wire.Type.TX_FRAME][:2] == bytes([wire.TX_NO_ACK, 22])  # Broadcast data at 11 Mbps.
    assert wire.Type.STOP in by_type
    assert radio._status_results == {} and radio._awaiting == set()


class SwitchModeBoard(FakeBoard):
    """Firmware 1.8 in Switch mode: the first HELLO restarts the board, so that port dies."""

    def __init__(self, ports):
        super().__init__()
        self.ports = ports
        self.dead = False

    def write(self, data):
        if self.dead:
            raise OSError("device disconnected")
        for frame in self.decoder.feed(data):
            if frame.type == wire.Type.HELLO:
                self.dead = True
                self.ports.append(ThreeDSModeBoard())   # comes back after the restart

    def read(self, count):
        time.sleep(0.002)
        if self.dead:
            raise OSError("device disconnected")
        return b""


class ThreeDSModeBoard(FakeBoard):
    def write(self, data):
        for frame in self.decoder.feed(data):
            self.received.append(frame)
            if frame.type == wire.Type.HELLO:   # 1.8: a features byte after the MAC (none set: gblink build)
                self._reply(wire.Type.HELLO_ACK, frame.seq, bytes([1, 1, 8]) + bytes.fromhex("A0B1C2D3E4F5") + b"\x00")
            else:
                self._reply(wire.Type.STATUS, frame.seq, bytes([frame.type]) + struct.pack("<i", 0))


def test_survives_the_switch_to_3ds_mode_restart(monkeypatch):
    import serial

    from pokeuds.radio import esp32

    monkeypatch.setattr(esp32, "REOPEN_DELAY", 0.05)
    opened = []
    ports = []
    ports.append(SwitchModeBoard(ports))

    def open_port(*a, **k):
        if not ports:
            raise serial.SerialException("could not open port")
        opened.append(ports.pop(0))
        return opened[-1]

    monkeypatch.setattr(serial, "Serial", open_port)
    radio = Esp32Radio("FAKE")
    radio.open()
    assert len(opened) == 2 and radio.version == (1, 8) and radio.features == 0
    assert radio.firmware == "1.8 (protocol 1)"
    radio.start(1, bytes.fromhex("020000000001"))
    radio.close()
    assert wire.Type.START in {f.type for f in opened[1].received}


def test_vcadapter_build_is_named(board, monkeypatch):
    def hello(self, data):
        for frame in self.decoder.feed(data):
            self._reply(wire.Type.HELLO_ACK, frame.seq, bytes([1, 2, 0]) + bytes(6) + bytes([wire.FEATURE_LINK_PORT]))
    monkeypatch.setattr(FakeBoard, "write", hello)
    radio = Esp32Radio("FAKE")
    radio.open()
    assert radio.firmware == "2.0 (protocol 1), VC-Adapter board build"
    radio._hello_event.clear()       # no STOP round trip on close
    radio.close()


@pytest.mark.parametrize("version, features, build", [
    ((2, 1), wire.FEATURE_NO_SWITCH, "uds"),                       # 2.1-uds: the 3DS side alone
    ((2, 1), 0, "ldn"),                                            # 2.1-ldn: both sides
    ((2, 0), 0, "ldn"),                                            # 2.0-uds had both sides: the ldn line
    ((2, 1), wire.FEATURE_LINK_PORT | wire.FEATURE_NO_SWITCH, "aio"),
])
def test_lines_are_named(board, monkeypatch, version, features, build):
    from pokeuds.app import board as boards

    def hello(self, data):
        for frame in self.decoder.feed(data):
            if frame.type == wire.Type.HELLO:
                self._reply(wire.Type.HELLO_ACK, frame.seq, bytes([1, *version]) + bytes(6) + bytes([features]))
            else:
                self._reply(wire.Type.STATUS, frame.seq, bytes([frame.type]) + struct.pack("<i", 0))
    monkeypatch.setattr(FakeBoard, "write", hello)
    assert boards.identify("FAKE").build == build


def _port(device, serial_number, vid=0x303A, pid=0x1001):
    from types import SimpleNamespace

    return SimpleNamespace(device=device, serial_number=serial_number, vid=vid, pid=pid)


def test_a_board_back_under_another_name_is_found_by_its_serial(monkeypatch):
    """macOS: the board restarts into its 3DS mode and its port comes back as another /dev/cu.usbmodem..."""
    import serial
    from serial.tools import list_ports

    from pokeuds.radio import esp32

    fake = FakeBoard()
    attached = [_port("/dev/cu.usbmodem1101", "F4:12:FA:00:00:01")]
    opened = []

    def open_port(name=None, *args, **kwargs):
        opened.append(name)
        if name != "/dev/cu.usbmodem1103":
            # The board is restarting: its old port is gone, and it comes back under the new name, beside another board.
            attached[:] = [_port("/dev/cu.usbmodem2201", "F4:12:FA:00:00:02"), _port("/dev/cu.usbmodem1103", "F4:12:FA:00:00:01")]
            raise serial.SerialException("[Errno 6] Device not configured")
        return fake

    monkeypatch.setattr(list_ports, "comports", lambda: list(attached))
    monkeypatch.setattr(serial, "Serial", open_port)
    monkeypatch.setattr(esp32, "REOPEN_DELAY", 0.01)
    radio = Esp32Radio("/dev/cu.usbmodem1101")
    radio.open()
    assert radio.port_name == "/dev/cu.usbmodem1103" and opened == ["/dev/cu.usbmodem1101", "/dev/cu.usbmodem1103"]
    radio._hello_event.clear()
    radio.close()


def test_find_again(monkeypatch):
    from serial.tools import list_ports

    from pokeuds.radio.esp32 import find_again

    attached = [_port("COM5", "AA"), _port("COM6", "BB")]
    monkeypatch.setattr(list_ports, "comports", lambda: list(attached))
    assert find_again("COM5", "AA") == "COM5"            # still there
    assert find_again("COM4", "BB") == "COM6"            # back under another name
    assert find_again("COM4", "") is None                # nothing to match it by: never another board
    assert find_again("COM4", "CC") is None              # not back yet
