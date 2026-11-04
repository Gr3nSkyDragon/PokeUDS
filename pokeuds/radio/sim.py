"""An in-process radio for tests: every SimRadio on one Air hears the others on its channel."""

from __future__ import annotations

import queue
import threading

from .base import Radio, RxFrame


class Air:
    def __init__(self, beacon_interval: float = 0.05) -> None:
        self.radios: list[SimRadio] = []
        self.lock = threading.Lock()
        self.beacon_interval = beacon_interval
        self.log: list[tuple[bytes, int]] = []  # Every frame sent, for assertions.
        self._stop = threading.Event()
        self._ticker = threading.Thread(target=self._beacons, daemon=True)
        self._ticker.start()

    def transmit(self, sender: "SimRadio", mpdu: bytes) -> None:
        with self.lock:
            self.log.append((mpdu, sender.channel))
            targets = [r for r in self.radios if r is not sender and r.channel == sender.channel]
        for radio in targets:
            radio.inbox.put(RxFrame(mpdu, sender.channel, -40))

    def _beacons(self) -> None:
        while not self._stop.wait(self.beacon_interval):
            with self.lock:
                senders = [r for r in self.radios if r.beacon]
            for radio in senders:
                self.transmit(radio, radio.beacon)

    def close(self) -> None:
        self._stop.set()


class SimRadio(Radio):
    def __init__(self, air: Air) -> None:
        self.air = air
        self.inbox: queue.Queue[RxFrame] = queue.Queue()
        self.beacon: bytes | None = None

    def start(self, channel: int, mac: bytes) -> None:
        self.channel, self.mac = channel, bytes(mac)
        with self.air.lock:
            self.air.radios.append(self)

    def set_channel(self, channel: int) -> None:
        self.channel = channel

    def send(self, mpdu: bytes) -> None:
        self.air.transmit(self, bytes(mpdu))

    def set_beacon(self, mpdu: bytes | None) -> None:
        self.beacon = bytes(mpdu) if mpdu else None

    def recv(self, timeout: float | None = None) -> RxFrame | None:
        try:
            return self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        with self.air.lock:
            if self in self.air.radios:
                self.air.radios.remove(self)
