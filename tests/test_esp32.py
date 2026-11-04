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
            self._reply(wire.Type.HELLO_ACK, frame.seq, bytes([1, 1, 9]) + bytes(6) + bytes([wire.FEATURE_LINK_PORT]))
    monkeypatch.setattr(FakeBoard, "write", hello)
    radio = Esp32Radio("FAKE")
    radio.open()
    assert radio.firmware == "1.9 (protocol 1), VC-Adapter board build"
    radio._hello_event.clear()       # no STOP round trip on close
    radio.close()
