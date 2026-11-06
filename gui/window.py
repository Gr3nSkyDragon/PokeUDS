"""Where the app's window opens: where it was last time (Settings' ``window``), else the middle of the screen.

The place and size are kept whenever the window is moved, resized, maximized or restored. A remembered place is
used only while it is still on a screen (a monitor unplugged since would leave the window out of sight); otherwise
the window opens centered at its remembered size.
"""

from __future__ import annotations

import sys
import threading

import flet as ft

MIN_WIDTH, MIN_HEIGHT = 900, 600
DEFAULT_WIDTH, DEFAULT_HEIGHT = 1280, 820
SAVE_AFTER = 0.5            # seconds of calm after a move or resize before the settings are written


def on_a_screen(left: float, top: float, width: float) -> bool:
    """The window's title bar would be on some monitor. Windows only; elsewhere the window manager keeps
    windows reachable, so the place is trusted."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        # The title bar's middle and its left end, a little inside: either on a monitor is enough to grab it.
        points = ((int(left + width / 2), int(top + 10)), (int(left + 60), int(top + 10)))
        return any(user32.MonitorFromPoint(wintypes.POINT(x, y), 0) for x, y in points)   # 0: MONITOR_DEFAULTTONULL
    except (AttributeError, OSError, ValueError):
        return True


def restore(page: ft.Page, saved: dict) -> None:
    """Size and place the window as ``saved`` says, then show it, once.

    The app starts with its window hidden (gui.main: ``AppView.FLET_APP_HIDDEN``): a window shown first and then
    resized, moved, centered and maximized step by step flashes on screen, which must never happen to someone with
    photosensitive epilepsy. Everything is set while it is hidden, and it appears already in place."""
    window = page.window
    window.min_width, window.min_height = MIN_WIDTH, MIN_HEIGHT
    width = max(MIN_WIDTH, _number(saved.get("width"), DEFAULT_WIDTH))
    height = max(MIN_HEIGHT, _number(saved.get("height"), DEFAULT_HEIGHT))
    window.width, window.height = width, height
    left, top = saved.get("left"), saved.get("top")
    placed = isinstance(left, (int, float)) and isinstance(top, (int, float)) and on_a_screen(left, top, width)
    if placed:
        window.left, window.top = left, top
    if saved.get("maximized"):
        window.maximized = True

    async def reveal() -> None:
        if not placed:
            await window.center()
        window.visible = True
        page.update()

    page.run_task(reveal)


def remember(page: ft.Page, settings, also=None) -> None:
    """Keep the window's place and size in ``settings`` as it changes; ``also(event)`` gets every window event."""
    window = page.window
    timer: list[threading.Timer | None] = [None]
    keep = {ft.WindowEventType.MOVED, ft.WindowEventType.RESIZED, ft.WindowEventType.MAXIMIZE,
            ft.WindowEventType.UNMAXIMIZE, ft.WindowEventType.RESTORE}

    def save() -> None:
        place = dict(settings.window or {})
        place["maximized"] = bool(window.maximized)
        if not window.maximized and not window.minimized:        # a maximized window keeps the size it returns to
            for key in ("left", "top", "width", "height"):
                value = getattr(window, key)
                if isinstance(value, (int, float)):
                    place[key] = round(value)
        if place != settings.window:
            settings.window = place
            try:
                settings.save()
            except OSError:
                pass

    def changed(e) -> None:
        if e.type in keep:
            if timer[0] is not None:
                timer[0].cancel()
            timer[0] = threading.Timer(SAVE_AFTER, save)
            timer[0].daemon = True
            timer[0].start()
        if also:
            also(e)

    window.on_event = changed


def _number(value, default: float) -> float:
    return value if isinstance(value, (int, float)) and value > 0 else default
