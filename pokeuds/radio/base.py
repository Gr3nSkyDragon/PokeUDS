"""The radio interface every backend implements.

A radio is a dumb 802.11 device on one 2.4 GHz channel. It captures MPDUs and injects them; it
knows nothing about UDS. Everything above the MPDU (beacons, the join handshake, CCMP) lives in
``pokeuds.uds`` so that the ESP32 and the USB adapter behave the same.

MPDUs cross this interface without radiotap and without the trailing FCS.
"""

from __future__ import annotations

import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass

# 2.4 GHz channels a 3DS uses for UDS discovery; a retail host picks one of these.
DISCOVERY_CHANNELS = (1, 6, 11)

# Transmit rates in 500 kbps units for data frames (both measured against retail consoles in
# azahar_uds): a client's NoDS/ToDS data at 11 Mbps, a host's FromDS data at 24 Mbps. Management
# frames keep the radio's default rate (0).
CLIENT_DATA_RATE = 22
HOST_DATA_RATE = 48


@dataclass
class RxFrame:
    mpdu: bytes
    channel: int
    rssi: int | None = None
    truncated: bool = False


def frame_control(mpdu: bytes) -> int:
    return struct.unpack_from("<H", mpdu, 0)[0] if len(mpdu) >= 2 else 0


def is_group_addressed(mpdu: bytes) -> bool:
    """Address 1 is multicast/broadcast: nobody acknowledges the frame."""
    return len(mpdu) >= 10 and (mpdu[4] & 0x01) != 0


def data_rate(mpdu: bytes) -> int:
    fc = frame_control(mpdu)
    if (fc >> 2) & 0x3 != 2:
        return 0
    return HOST_DATA_RATE if fc & 0x0200 else CLIENT_DATA_RATE


def channel_to_frequency(channel: int) -> int:
    if 1 <= channel <= 13:
        return 2407 + 5 * channel
    if channel == 14:
        return 2484
    raise ValueError(f"unsupported 2.4 GHz channel {channel}")


class Radio(ABC):
    """One 802.11 radio. Methods may be called from one thread at a time."""

    mac: bytes = b"\0" * 6
    channel: int = 0

    @abstractmethod
    def start(self, channel: int, mac: bytes) -> None:
        """Bring the radio up on ``channel``; unicast frames to ``mac`` are ours."""

    @abstractmethod
    def set_channel(self, channel: int) -> None: ...

    @abstractmethod
    def send(self, mpdu: bytes) -> None:
        """Inject one MPDU. Group-addressed frames are sent without waiting for an ACK."""

    @abstractmethod
    def set_beacon(self, mpdu: bytes | None) -> None:
        """Transmit ``mpdu`` every beacon interval (102.4 ms) until replaced; None stops."""

    def watch(self, mac: bytes) -> None:
        """Also deliver frames involving ``mac`` (a peer). Backends that capture all do nothing."""

    @abstractmethod
    def recv(self, timeout: float | None = None) -> RxFrame | None:
        """The next captured frame, or None after ``timeout`` seconds."""

    @abstractmethod
    def close(self) -> None: ...

    def describe(self) -> str:
        return type(self).__name__

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
