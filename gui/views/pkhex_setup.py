"""Getting the PKHeX helper, from any page that needs it (Build one, Gifts, the bank's check) and from Settings.

Get it downloads the self-contained helper a PokeUDS release carries for this computer: nothing to install.
Build it runs ``dotnet build`` when a .NET 10 SDK is here. One download or build at a time, shared by every page;
when it ends every page is drawn again, so whatever was waiting for the helper opens (docs/pkhex.md).
"""

from __future__ import annotations

import threading

import flet as ft

from pokeuds import pkhex

from .. import theme as t

_state = {"working": "", "message": "", "color": t.INFO, "sdks": None}


def ready() -> bool:
    return pkhex.helper_path() is not None


def _sdks() -> list[str]:
    if _state["sdks"] is None:
        _state["sdks"] = pkhex.dotnet_sdks()
    return _state["sdks"]


def card(app, head: list[ft.Control] | None = None, compact: bool = False) -> ft.Control:
    """The helper's state and what to do about it; ``compact`` for Settings, where it is one row of many."""
    working = _state["working"]
    path = pkhex.helper_path()
    rows: list[ft.Control] = list(head or [])
    if path is not None and compact:
        rows.append(ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE, color=t.GOOD, size=18),
                            t.label(pkhex.status(), color=t.TEXT, expand=True)], spacing=8))
    elif path is None:
        rows.append(t.label("Building Pokemon, checking legality and Mystery Gift cards use PKHeX.Core through a small "
                            "helper program. " + pkhex.status(), color=t.TEXT))
    sdks = _sdks()
    can_build = any(v.split(".")[0].isdigit() and int(v.split(".")[0]) >= 10 for v in sdks)
    buttons = [t.primary("Get the PKHeX helper" if path is None else "Get it again", lambda e: _run(app, "get"),
                         ft.Icons.DOWNLOAD, disabled=bool(working))]
    if can_build:
        buttons.append(t.secondary("Build it with .NET", lambda e: _run(app, "build"), ft.Icons.BUILD, disabled=bool(working)))
    buttons.append(ft.ProgressRing(width=18, height=18, visible=bool(working)))
    if working:
        buttons.append(t.label("Downloading..." if working == "get" else "Building (a minute or two the first time)...",
                               size=12))
    rows.append(ft.Row(buttons, spacing=10, wrap=True))
    if path is None:
        rows.append(t.label(
            "Get it downloads the helper for this computer from PokeUDS's releases (about 20 MB, checked against the "
            "release's SHA256SUMS); nothing else to install. "
            + (f"Build it uses the .NET SDK here ({', '.join(sdks)})." if can_build else
               f"The .NET SDK here ({', '.join(sdks)}) is older than the .NET 10 the build needs." if sdks else
               "Building instead needs the .NET 10 SDK: dotnet build -c Release services/pkhex."), size=12))
    if _state["message"]:
        rows.append(t.notice(_state["message"], _state["color"]))
    return ft.Column(rows, spacing=10, tight=True) if compact else t.card(*rows, spacing=10)


def _run(app, what: str) -> None:
    if _state["working"]:
        return
    _state.update(working=what, message="")
    app.changed()

    def work() -> None:
        try:
            if what == "get":
                path = pkhex.download_helper(say=lambda _: None)
                _state.update(message=f"The PKHeX helper is ready: {path}", color=t.GOOD)
            else:
                done = pkhex.build_helper()
                if done.returncode == 0 and pkhex.helper_path():
                    _state.update(message="The PKHeX helper is built.", color=t.GOOD)
                else:
                    _state.update(message="dotnet build failed:\n" + pkhex.build_problem(done), color=t.BAD)
        except Exception as exc:                  # no release yet, no network, an old SDK, a failed check
            _state.update(message=str(exc), color=t.BAD)
        _state["working"] = ""
        _state["sdks"] = None
        rebuild = getattr(app, "rebuild_shell", None)
        app.ui(rebuild if rebuild else app.changed)

    threading.Thread(target=work, daemon=True, name=f"pkhex-{what}").start()
