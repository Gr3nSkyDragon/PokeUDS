"""A Pokemon's sprite in a preview (pokeuds.app.sprites): shown at once when it is here, else fetched in the
background and drawn into its placeholder when it arrives. With no sprite (offline, turned off, none for it) the
placeholder stays, a quiet one of the same size, so nothing moves.

A sprite that arrives fills its own placeholder in place: the page around it is not rebuilt, so a page of 150 gift
cards neither flickers nor jumps back to the top while their sprites come in. ``progress`` says how many of the
fetches under way are done, for a page that shows it (the Gifts page, the first time its sprites download).
"""

from __future__ import annotations

import threading
from typing import Callable

import flet as ft

from pokeuds.app import sprites

from .. import theme as t

_lock = threading.Lock()
_waiting: dict[tuple[int | str, bool], list[tuple[ft.Container, int]]] = {}   # placeholders per sprite fetched
_counts = {"done": 0, "total": 0}          # fetches since the last time none was under way


def _image(data: bytes, size: int) -> ft.Image:
    return ft.Image(src=data, width=size, height=size, fit=ft.BoxFit.CONTAIN, filter_quality=ft.FilterQuality.NONE,
                    gapless_playback=True)


def sprite(app, species: int, *, form: int = 0, shiny: bool = False, size: int = 96,
           on_ready: Callable[[], None] | None = None) -> ft.Control:
    """The sprite of ``species`` (``form``, ``shiny``) in a ``size`` square. A missing one is fetched and drawn into
    its placeholder when it comes (``on_ready``, kept for older callers, is no longer needed)."""
    key = sprites.sprite_key(species, form)
    data = sprites.CACHE.cached(key, shiny) or (sprites.CACHE.cached(key) if shiny else None)
    if data is not None:
        return ft.Container(_image(data, size), width=size, height=size, tooltip="Sprite: PokeAPI")
    holder = ft.Container(ft.Icon(ft.Icons.CATCHING_POKEMON, color=t.LINE, size=size // 3), width=size, height=size,
                          alignment=ft.Alignment.CENTER)
    if key is not None and sprites.CACHE.online:
        _fetch(app, key, shiny, holder, size)
    return holder


def _fetch(app, key: int | str, shiny: bool, holder: ft.Container, size: int) -> None:
    with _lock:
        waiting = _waiting.get((key, shiny))
        if waiting is not None:              # already on its way: this placeholder is filled with the others
            waiting.append((holder, size))
            return
        _waiting[(key, shiny)] = [(holder, size)]
        _counts["total"] += 1

    def work() -> None:
        got = None
        try:
            got = sprites.CACHE.get(key, shiny) or (sprites.CACHE.get(key) if shiny else None)
        finally:
            app.ui(lambda: _arrived(key, shiny, got))

    threading.Thread(target=work, daemon=True, name="sprite").start()


def _arrived(key: int | str, shiny: bool, data: bytes | None) -> None:
    """On the UI thread: draw the sprite into every placeholder waiting for it that is still on a page."""
    with _lock:
        holders = _waiting.pop((key, shiny), [])
        _counts["done"] += 1
        if not _waiting:
            _counts["done"] = _counts["total"] = 0
    if data is None:
        return
    for holder, size in holders:
        holder.content = _image(data, size)
        holder.tooltip = "Sprite: PokeAPI"
        try:
            holder.update()
        except (RuntimeError, AssertionError):   # its page was rebuilt or left meanwhile: the new one has it cached
            pass


def progress() -> tuple[int, int]:
    """(done, total) of the sprite fetches under way; (0, 0) when none is."""
    with _lock:
        return _counts["done"], _counts["total"]
