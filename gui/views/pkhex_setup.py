"""What a page that needs PKHeX (Build one, Gifts, the bank's check) shows when PKHeX is missing.

A packaged PokeUDS carries PKHeX inside (pokeuds/pkhex.py), so players never see this. Running from source without
it built, the notice says how to build it and, with the .NET 10 SDK here, offers Build it: one build at a time,
shared by every page; when it ends every page is drawn again, so whatever was waiting for PKHeX opens (docs/pkhex.md).
"""

from __future__ import annotations

import threading

import flet as ft

from pokeuds import pkhex

from .. import theme as t

_state = {"working": False, "message": "", "color": t.INFO}


def ready() -> bool:
    return pkhex.helper_path() is not None


def card(app, head: list[ft.Control] | None = None, compact: bool = False) -> ft.Control:
    """PKHeX is missing: why, and the build when it can run here; ``compact`` inside another card."""
    rows: list[ft.Control] = list(head or [])
    rows.append(t.label("Building Pokemon, checking legality and Mystery Gift cards use PKHeX. " + pkhex.status(),
                        color=t.TEXT))
    if pkhex.can_build():           # a source tree and the .NET 10 SDK; never in a packaged app
        working = _state["working"]
        rows.append(ft.Row([t.secondary("Build it with .NET", lambda e: _build(app), ft.Icons.BUILD, disabled=working),
                            ft.ProgressRing(width=18, height=18, visible=working),
                            *([t.label("Building (a minute or two the first time)...", size=12)] if working else [])],
                           spacing=10, wrap=True))
    if _state["message"]:
        rows.append(t.notice(_state["message"], _state["color"]))
    return ft.Column(rows, spacing=10, tight=True) if compact else t.card(*rows, spacing=10)


def _build(app) -> None:
    if _state["working"]:
        return
    _state.update(working=True, message="")
    app.changed()

    def work() -> None:
        try:
            done = pkhex.build_helper()
            if done.returncode == 0 and pkhex.helper_path():
                _state.update(message="PKHeX is built.", color=t.GOOD)
            else:
                _state.update(message="dotnet build failed:\n" + pkhex.build_problem(done), color=t.BAD)
        except Exception as exc:                  # no SDK, an old SDK, no source
            _state.update(message=str(exc), color=t.BAD)
        _state["working"] = False
        rebuild = getattr(app, "rebuild_shell", None)
        app.ui(rebuild if rebuild else app.changed)

    threading.Thread(target=work, daemon=True, name="pkhex-build").start()
