"""A Pokemon's sprite in a preview (pokeuds.app.sprites): shown at once when it is here, else fetched in the
background and the view refreshed when it arrives. With no sprite (offline, turned off, none for it) the
preview keeps a quiet placeholder of the same size, so nothing moves."""

from __future__ import annotations

import threading
from typing import Callable

import flet as ft

from pokeuds.app import sprites

from .. import theme as t

_pending: set[tuple[int | str, bool]] = set()
_scheduled: set[int] = set()       # views with a refresh already on its way
_lock = threading.Lock()
SETTLE = 0.3                       # sprites arriving within this share one refresh of their view


def sprite(app, species: int, *, form: int = 0, shiny: bool = False, size: int = 96,
           on_ready: Callable[[], None] | None = None) -> ft.Control:
    """The sprite of ``species`` (``form``, ``shiny``) in a ``size`` square; ``on_ready`` (the view's refresh)
    runs on the UI thread once a missing sprite has been fetched."""
    key = sprites.sprite_key(species, form)
    data = sprites.CACHE.cached(key, shiny) or (sprites.CACHE.cached(key) if shiny else None)
    if data is None and key is not None:
        _fetch(app, key, shiny, on_ready)
    if data is None:
        return ft.Container(ft.Icon(ft.Icons.CATCHING_POKEMON, color=t.LINE, size=size // 3), width=size, height=size,
                            alignment=ft.Alignment.CENTER)
    return ft.Container(ft.Image(src=data, width=size, height=size, fit=ft.BoxFit.CONTAIN,
                                 filter_quality=ft.FilterQuality.NONE, gapless_playback=True),
                        width=size, height=size, tooltip="Sprite: PokeAPI")


def _fetch(app, key: int | str, shiny: bool, on_ready: Callable[[], None] | None) -> None:
    with _lock:
        if (key, shiny) in _pending:
            return
        _pending.add((key, shiny))

    def work() -> None:
        try:
            got = sprites.CACHE.get(key, shiny) or (sprites.CACHE.get(key) if shiny else None)
        finally:
            with _lock:
                _pending.discard((key, shiny))
        if got is not None and on_ready is not None:
            _notify(app, on_ready)

    threading.Thread(target=work, daemon=True, name="sprite").start()


def _notify(app, on_ready: Callable[[], None]) -> None:
    """One refresh per view for a burst of arrivals (a page of 150 gift cards), so clicks are not lost to
    a page rebuilt over and over."""
    owner = id(getattr(on_ready, "__self__", on_ready))
    with _lock:
        if owner in _scheduled:
            return
        _scheduled.add(owner)

    def fire() -> None:
        with _lock:
            _scheduled.discard(owner)
        app.ui(on_ready)

    timer = threading.Timer(SETTLE, fire)
    timer.daemon = True
    timer.start()
