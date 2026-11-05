"""Radio backends. ``open_radio`` turns a command-line spec into a radio:

- ``esp32`` or ``esp32:COM4``: an ESP32-S3 running esp32-uds-bridge (found by USB ID if no port).
- ``ldnd`` or ``ldnd:\\\\.\\pipe\\name``: a USB Wi-Fi adapter through legacy ldnd.exe 0.0.5 (Windows).
- ``demo:red`` (any VC title; ``gold-tc`` for the Time Capsule): a simulated 3DS, no hardware.
"""

from __future__ import annotations

from .base import DISCOVERY_CHANNELS, Radio, RxFrame


def open_radio(spec: str) -> Radio:
    kind, _, arg = spec.partition(":")
    kind = kind.lower()
    if kind == "esp32":
        from .esp32 import Esp32Radio

        radio = Esp32Radio(arg or None)
        radio.open()
        return radio
    if kind == "ldnd":
        from .ldnd import DEFAULT_PIPE, LdndRadio

        return LdndRadio(arg or DEFAULT_PIPE)
    if kind == "demo":
        from .demo import DemoRadio

        return DemoRadio(arg)
    from .. import plugins

    if (radio := plugins.open_radio(kind, arg)) is not None:      # a plugin's radio (docs/plugins.md)
        return radio
    raise ValueError(f"unknown radio {spec!r}: use esp32[:PORT], ldnd[:PIPE], demo:TITLE or a plugin's radio")


__all__ = ["DISCOVERY_CHANNELS", "Radio", "RxFrame", "open_radio"]
