"""A pick box you can type in: a dropdown whose list filters to what is typed (Flet's editable, filtering Dropdown).
A click on an entry picks it; leaving the box (Tab, a click elsewhere) picks what was typed, the first match, the
entry the filtered list shows on top. Used by the builder and the GTS page.
"""

from __future__ import annotations

from typing import Callable

import flet as ft

WIDTH = 220


def _fold(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def pick(text: str, choices: list[tuple[str, str]]) -> str | None:
    """The entry typed text means: the one named exactly, else the first whose name starts with it, else the first
    that contains it (the list's own filter puts that one on top); None for no text or no match."""
    folded = _fold(text)
    if not folded:
        return None
    for test in (lambda name: name == folded, lambda name: name.startswith(folded), lambda name: folded in name):
        for key, name in choices:
            if test(_fold(name)):
                return key
    return None


def pick_box(label: str, value, options: list[tuple], on_select: Callable, *, width: int = WIDTH,
             disabled: bool = False, redraw: Callable[[], None] | None = None) -> ft.Dropdown:
    """The box. ``options`` are (key, text); ``on_select`` gets the change event, its ``control.value`` the key;
    ``redraw`` (the view's refresh) runs after a typed pick, so the box shows the entry picked or the one it had."""
    choices = [(str(k), str(v)) for k, v in options]
    typed: dict[str, str | None] = {"text": None}

    def selected(e) -> None:
        typed["text"] = None
        on_select(e)

    def text_changed(e) -> None:
        typed["text"] = e.data if isinstance(e.data, str) else e.control.text

    def left(e) -> None:
        text, typed["text"] = typed["text"], None
        if text is None:
            return
        key = pick(text, choices)
        if key is not None and key != e.control.value:
            e.control.value = key
            on_select(e)
        if redraw is not None:
            redraw()

    return ft.Dropdown(label=label, value=None if value is None else str(value), width=width, dense=True,
                       disabled=disabled, menu_height=360,
                       options=[ft.DropdownOption(key=k, text=v) for k, v in choices], on_select=selected,
                       on_text_change=text_changed, on_blur=left, editable=True, enable_filter=True, enable_search=True)
