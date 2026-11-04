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
