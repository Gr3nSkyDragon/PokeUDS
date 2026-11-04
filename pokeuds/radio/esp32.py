"""ESP32-S3 radio backend (VC-Adapter ``firmware/esp32-uds-bridge``) over its native USB port.

The board captures and injects raw MPDUs and replays a beacon template on its own timer, so a slow
USB round trip never shows up as a missing beacon. The protocol is in ``esp32_wire``; the
behaviour below follows azahar_uds ``nl80211_monitor.cpp`` (``Esp32Session`` / ``RunEsp32Body``).
"""

from __future__ import annotations

import logging
import queue
import struct
import threading
import time

from . import esp32_wire as wire
from .base import Radio, RxFrame, data_rate, is_group_addressed

log = logging.getLogger(__name__)

# Espressif USB Serial/JTAG, the S3's native USB port.
ESPRESSIF_VID = 0x303A
ESPRESSIF_JTAG_PID = 0x1001

PING_INTERVAL = 2.0
PONG_TIMEOUT = 8.0
COMMAND_TIMEOUT = 3.0


def find_port() -> str | None:
    """The serial port of an attached ESP32-S3 (native USB), or None."""
    from serial.tools import list_ports

    for port in list_ports.comports():
        if port.vid == ESPRESSIF_VID and port.pid == ESPRESSIF_JTAG_PID:
            return port.device
    return None


class Esp32Radio(Radio):
    def __init__(self, port: str | None = None, baud: int = 3_000_000) -> None:
        self.port_name = port
        self.baud = baud
        self.serial = None
        self.hello: bytes | None = None
        self.firmware = "?"
        self.factory_mac = b""
        self._decoder = wire.Decoder()
        self._rx: queue.Queue[RxFrame] = queue.Queue(maxsize=4096)
        self._write_lock = threading.Lock()
        self._status = threading.Condition()
        self._status_results: dict[int, int] = {}
        self._awaiting: set[int] = set()  # Only commands we wait on; TX_FRAME statuses are ignored.
        self._hello_event = threading.Event()
        self._seq = 1
        self._last_pong = time.monotonic()
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._beacon: bytes | None = None

    # -- link ---------------------------------------------------------------------------------

    def open(self) -> None:
        import serial

        name = self.port_name or find_port()
        if not name:
            raise RuntimeError(
                "No ESP32-S3 found. Connect the board's native USB port (Espressif USB "
                "Serial/JTAG, 303A:1001) or pass --port.")
        self.port_name = name
        self.serial = serial.Serial(name, self.baud, timeout=0.02, write_timeout=1.0)
        self._reader = threading.Thread(target=self._read_loop, name="esp32-rx", daemon=True)
        self._reader.start()
        # Opening the native USB port can reset the chip, so keep asking until it answers.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not self._hello_event.is_set():
            self._send(wire.Type.HELLO)
            self._hello_event.wait(0.5)
        if not self._hello_event.is_set():
            self.close()
            raise RuntimeError(
                f"Something is on {name} but it did not answer. Is the esp32-uds-bridge "
                "firmware flashed?")
        ack = self.hello or b""
        if len(ack) >= 3:
            self.firmware = f"{ack[1]}.{ack[2]} (protocol {ack[0]})"
        if len(ack) >= 9:
            self.factory_mac = ack[3:9]
        log.info("ESP32 on %s, firmware %s", name, self.firmware)

    def describe(self) -> str:
        return f"ESP32 on {self.port_name}, firmware {self.firmware}"

    def _send(self, type_: int, payload: bytes = b"", flags: int = 0, *, await_status: bool = False) -> int:
        if self.serial is None:
            raise RuntimeError("ESP32 is not open")
        with self._write_lock:
            seq = self._seq
            self._seq = (self._seq + 1) & 0xFF or 1
            if await_status:  # Registered before the write so a fast reply is not dropped.
                with self._status:
                    self._awaiting.add(seq)
            self.serial.write(wire.encode(type_, seq, flags, payload))
        return seq

    def _command(self, type_: int, payload: bytes = b"") -> int:
        seq = self._send(type_, payload, await_status=True)
        deadline = time.monotonic() + COMMAND_TIMEOUT
        with self._status:
            try:
                while seq not in self._status_results:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"ESP32 did not answer {wire.Type(type_).name}")
                    self._status.wait(remaining)
                result = self._status_results.pop(seq)
            finally:
                self._awaiting.discard(seq)
        if result != 0:
            log.warning("ESP32 %s returned %d", wire.Type(type_).name, result)
        return result

    def _read_loop(self) -> None:
        next_ping = time.monotonic()
        while not self._stop.is_set():
            try:
                data = self.serial.read(16384)
            except Exception as exc:  # Unplugged.
                log.error("ESP32 link lost: %s", exc)
                break
            if data:
                for frame in self._decoder.feed(data):
                    self._dispatch(frame)
            now = time.monotonic()
            if self._hello_event.is_set() and now >= next_ping:
                next_ping = now + PING_INTERVAL
                try:
                    self._send(wire.Type.PING, b"\x01\x02\x03\x04")
                except Exception:
                    break
                if now - self._last_pong > PONG_TIMEOUT:
                    log.error("ESP32 stopped answering pings")

    def _dispatch(self, frame: wire.Frame) -> None:
        t = frame.type
        if t == wire.Type.HELLO_ACK:
            self.hello = frame.payload
            self._hello_event.set()
        elif t == wire.Type.STATUS:
            if len(frame.payload) >= 5:
                (result,) = struct.unpack_from("<i", frame.payload, 1)
                with self._status:
                    if frame.seq in self._awaiting:
                        self._status_results[frame.seq] = result
                        self._status.notify_all()
        elif t == wire.Type.RX:
            p = frame.payload
            if len(p) < 3 + 24:
                return
            channel = p[0] if 1 <= p[0] <= 14 else self.channel
            rssi = struct.unpack_from("b", p, 1)[0]
            try:
                self._rx.put_nowait(RxFrame(p[3:], channel, rssi, bool(p[2] & wire.RX_TRUNCATED)))
            except queue.Full:
                log.warning("dropping a captured frame: receive queue full")
        elif t == wire.Type.PONG:
            self._last_pong = time.monotonic()
        elif t == wire.Type.LOG:
            log.debug("ESP32: %s", frame.payload.decode("utf-8", "replace").rstrip())
        elif t == wire.Type.STATS:
            stats = wire.DeviceStats.parse(frame.payload)
            if stats:
                log.debug("ESP32 stats %s, bad USB frames %d", stats, self._decoder.bad_frames)
        elif t == wire.Type.TX_DONE:
            if len(frame.payload) >= 3:
                log.debug("ESP32 unicast TX %s", "acked" if frame.payload[0] else "not acked")

    # -- Radio ----------------------------------------------------------------------------------

    def start(self, channel: int, mac: bytes) -> None:
        if self.serial is None:
            self.open()
        self.mac, self.channel = bytes(mac), channel
        # The trailing 1 asks for a twin hardware address. The chip does not pass unicast data
        # addressed to its own MAC up to the capture path, which starved the join handshake in
        # azahar_uds; with a twin, frames for our MAC arrive as sniffed traffic. The price is no
        # hardware ACK for them, so the peer retransmits a few frames.
        self._command(wire.Type.START, bytes([channel]) + self.mac + b"\x01")

    def set_channel(self, channel: int) -> None:
        if channel != self.channel:
            self._command(wire.Type.SET_CHANNEL, bytes([channel]))
            self.channel = channel

    def send(self, mpdu: bytes) -> None:
        flags = wire.TX_NO_ACK if is_group_addressed(mpdu) else 0
        self._send(wire.Type.TX_FRAME, bytes([flags, data_rate(mpdu)]) + mpdu)

    def set_beacon(self, mpdu: bytes | None) -> None:
        if mpdu == self._beacon:
            return
        self._beacon = mpdu
        self._command(wire.Type.SET_BEACON, mpdu or b"")

    def watch(self, mac: bytes) -> None:
        self._send(wire.Type.SET_WATCH, bytes(mac))

    def recv(self, timeout: float | None = None) -> RxFrame | None:
        try:
            return self._rx.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        if self.serial is None:
            return
        try:
            if self._hello_event.is_set():
                self._command(wire.Type.STOP)
        except Exception:
            pass
        self._stop.set()
        if self._reader:
            self._reader.join(timeout=1.0)
        try:
            self.serial.close()
        finally:
            self.serial = None
