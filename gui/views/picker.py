"""A name field for long lists (species, moves, items, places): type a name, get its id.

Flet builds every option of a Dropdown up front, so pages with several 700-to-900-entry dropdowns took over a
minute to appear. This field holds no options: it resolves what was typed (exact name, then a name that starts
with it, then one that contains it, ignoring case, spaces and punctuation; or a number) and shows the match
below it, or why nothing matched.
"""

from __future__ import annotations

import re
from typing import Callable

import flet as ft

from .. import theme as t

_FOLD = re.compile(r"[\s\-'’.:]+")
_index_cache: dict[int, tuple[dict[str, int], list[tuple[str, int, str]], dict[int, str]]] = {}


def _fold(text: str) -> str:
    return _FOLD.sub("", text).casefold().replace("é", "e")


def _index(options: list) -> tuple[dict[str, int], list[tuple[str, int, str]], dict[int, str]]:
    key = id(options)
    if key not in _index_cache:
        exact, names, by_id = {}, [], {}
        for value, name in options:
            folded = _fold(str(name))
            exact.setdefault(folded, int(value))
            names.append((folded, int(value), str(name)))
            by_id.setdefault(int(value), str(name))
        _index_cache[key] = (exact, sorted(names), by_id)
    return _index_cache[key]


def resolve(text: str, options: list) -> tuple[int | None, str]:
    """(id, name) for what was typed, or (None, why)."""
    text = (text or "").strip()
    if not text:
        return 0, ""
    exact, names, by_id = _index(options)
    if text.lstrip("#").isdigit():
        number = int(text.lstrip("#"))
        return (number, by_id[number]) if number in by_id else (None, f"no entry {number}")
    folded = _fold(text)
    if folded in exact:
        return exact[folded], by_id[exact[folded]]
    starts = [(v, n) for f, v, n in names if f.startswith(folded)]
    if len(starts) == 1:
        return starts[0]
    contains = starts or [(v, n) for f, v, n in names if folded in f]
    if len(contains) == 1:
        return contains[0]
    if contains:
        return None, "more than one: " + ", ".join(n for _, n in contains[:4]) + ("..." if len(contains) > 4 else "")
    return None, "nothing matches"


def name_field(label: str, value: int, options: list, on_pick: Callable[[int], None], width: int = 220,
               empty: str = "", disabled: bool = False) -> ft.TextField:
    """A field showing the name for ``value`` (``empty`` for 0); a new name calls ``on_pick`` with its id."""
    _, _, by_id = _index(options)
    shown = by_id.get(int(value or 0), "") if value else ""

    def commit(e) -> None:
        number, text = resolve(e.control.value, options)
        if number is None:
            e.control.error, e.control.helper = text, None
        else:
            e.control.error = None
            e.control.value = text if number else ""
            e.control.helper = f"#{number}" if number else (empty or None)
            on_pick(number)
        e.control.update()

    return ft.TextField(label=label, value=shown, width=width, dense=True, disabled=disabled, on_blur=commit,
                        on_submit=commit, hint_text=empty or "type a name", helper=f"#{value}" if value else None,
                        helper_style=ft.TextStyle(color=t.MUTED, size=11))
