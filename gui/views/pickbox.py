"""A pick box you can type in: a dropdown whose list filters to what is typed (Flet's editable, filtering Dropdown).
A click on an entry picks it; leaving the box (Tab, a click elsewhere) picks what was typed, the first match, the
entry the filtered list shows on top. Used by the builder and the GTS page.

The builder's boxes hold thousands of entries (four move lists, the items, the species), and three things made it
lag (2026-11-23; pokeldn met the same, its docs/gui.md, Performance):

- The window keeps every entry of every box built, and rebuilds a box's whole list whenever the box changes: its
  focus, its value, a key typed. Seven long boxes held about 340 MB in the window (476 MB with the builder open, 140
  without) and made each click and pick slow. A box of ``LAZY_FROM`` entries or more holds its own entry alone until
  the pointer first comes over it (or Tab focuses it), and keeps its whole list from then on: only the boxes used are
  in the window in full (148 MB with the builder opened). It never gives the list back: the menu its arrow opens
  leaves the box unfocused and the pointer off it, so a box that looked unused had its open menu closed under the
  player (2026-11-23). pokeldn keeps its long boxes in a closed panel to the same end.
- Flet ends an event handler that calls no ``update()`` with an update of the whole page, a diff of every entry in
  Python: 50 to 190 ms each. Every box reports its text when it reaches the window, so opening the builder queued
  about a second of them before a click was handled, and each pick or key added one. The box's handlers call
  ``quiet()``: they update what they change themselves.
- Leaving a box redrew the whole page. It redraws only when the box shows text that is not its entry's name (a part
  of a name typed), so the name shows again.
"""

from __future__ import annotations

import dataclasses
from typing import Callable

import flet as ft

from ..translate import translate

WIDTH = 220
LAZY_FROM = 100     # a list this long is in the window once its box is first used (pokeldn's t.LAZY_FROM)


def quiet() -> None:
    """In an event handler that updates what it changes itself, or changes nothing on screen: no update of the whole
    page after it (Flet's automatic update)."""
    ft.context.disable_auto_update()


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


def _attached(control: ft.Control) -> bool:
    """Whether every parent up to the page still holds its child. Flet 1.0 keeps the parent link of a control replaced
    in a one-control property (a container's content, as a redraw replaces a page), and an update() of it patches a
    control the window has dropped (pokeldn's PixelActivity._attached)."""
    child, parent = control, control.parent
    while parent is not None:
        if not any(value is child or isinstance(value, list) and any(x is child for x in value)
                   for value in (getattr(parent, f.name, None) for f in dataclasses.fields(parent))):
            return False
        child, parent = parent, parent.parent
    return isinstance(child, ft.Page)


def _own(box: ft.Dropdown) -> list[ft.DropdownOption]:
    """A long box's list before it is first used: its own entry alone."""
    return [o for o in box.every if o.key == box.value][:1]


def fill(box: ft.Dropdown) -> None:
    """Give a long box its whole list, when it is on the page."""
    every = getattr(box, "every", None)
    if every is None or box.options is every or not _attached(box):
        return
    box.options = every
    try:
        box.update()
    except RuntimeError:            # it left the page meanwhile
        pass


def dropdown(control: ft.Control) -> ft.Dropdown:
    """The Dropdown of what pick_box returned: a long box comes in its hover wrapper."""
    return getattr(control, "dropdown", control)


def set_value(control: ft.Control, key: str | None) -> None:
    """Change a box's entry from the app (the builder empties its move boxes for a new species); the caller updates
    it. A long box not used yet keeps holding its own entry alone."""
    box = dropdown(control)
    box.value = key
    if getattr(box, "every", None) is not None and box.options is not box.every:
        box.options = _own(box)


def pick_box(label: str, value, options: list[tuple], on_select: Callable, *, width: int = WIDTH,
             disabled: bool = False, redraw: Callable[[], None] | None = None) -> ft.Control:
    """The box. ``options`` are (key, text); ``on_select`` gets the change event, its ``control.value`` the key, and
    updates what it changes itself (no update of the whole page follows it). ``redraw`` (the view's refresh) runs
    when the box is left showing typed text that picks nothing new, so it shows the name of the entry it keeps; a
    typed pick of another entry updates the box alone.

    A long box (``LAZY_FROM`` entries or more) comes in a wrapper (``dropdown()`` reaches the box) that fills it when
    the pointer comes over it, before the click: a list that arrives on the click itself makes the window rebuild the
    field and drop the keys typed right after it (2026-11-23, as pokeldn found). Focus by Tab fills it too."""
    choices = [(str(k), str(v)) for k, v in options]
    names = dict(choices)
    typed: dict[str, str | None] = {"text": None}
    current = None if value is None else str(value)
    every = [ft.DropdownOption(key=k, text=v) for k, v in choices]
    lazy = len(every) >= LAZY_FROM

    def selected(e) -> None:
        quiet()
        typed["text"] = None
        on_select(e)

    def text_changed(e) -> None:
        quiet()                     # nothing changes on screen: the box shows what was typed already
        typed["text"] = e.data if isinstance(e.data, str) else e.control.text

    def focused(e) -> None:
        quiet()
        fill(box)

    def hovered(e) -> None:
        quiet()
        if e.data is True:          # the pointer came over it (False: it left)
            fill(box)

    def left(e) -> None:
        quiet()
        text, typed["text"] = typed["text"], None
        if text is not None:
            key = pick(text, choices)
            if key is not None and key != e.control.value:
                e.control.value = key
                on_select(e)
                try:
                    e.control.update()  # the box shows the entry's name in place of what was typed
                except RuntimeError:
                    pass
            elif redraw is not None and _fold(text) != _fold(names.get(str(e.control.value), "")):
                redraw()                # typed text that picks nothing new: show the entry it keeps

    box = ft.Dropdown(label=label, value=current, width=width, dense=True, disabled=disabled, menu_height=360,
                      options=every, on_select=selected, on_text_change=text_changed, on_blur=left,
                      on_focus=focused if lazy else None, editable=True, enable_filter=True, enable_search=True)
    translate(box)                  # "None", "Any" and the like, in every entry the box may show
    if not lazy:
        return box
    box.every = every
    box.options = _own(box)
    wrapper = ft.Container(box, on_hover=hovered)
    wrapper.dropdown = box
    return wrapper
