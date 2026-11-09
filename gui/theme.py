"""Colours, spacing and the few building blocks every view uses."""

from __future__ import annotations

from pathlib import Path

import flet as ft

ASSETS = Path(__file__).resolve().parent / "assets"     # bundled beside the gui package (scripts/build_exe.py)
ICON = ASSETS / "icon.ico"          # the exe's, the taskbar's and the window's icon
LOGO = ASSETS / "icon.png"          # the rail's mark

BG = "#101318"
PANEL = "#181C23"
CARD = "#1F242D"
RAISED = "#272D38"
LINE = "#2E3542"
TEXT = "#E8EAF0"
MUTED = "#9AA3B2"
ACCENT = "#E8534A"          # placeholder until the logo sets the colours
ACCENT_SOFT = "#3A2427"
GOOD = "#4CC38A"
WARN = "#E5B454"
BAD = "#E8534A"
INFO = "#5AA9E6"
GAP = 16
RADIUS = 12

STATE_COLORS = {"idle": MUTED, "running": INFO, "done": GOOD, "stopped": WARN, "error": BAD}


def app_theme() -> ft.Theme:
    return ft.Theme(color_scheme_seed=ACCENT, use_material3=True)


def title(text: str) -> ft.Text:
    return ft.Text(text, size=22, weight=ft.FontWeight.W_600, color=TEXT)


def heading(text: str) -> ft.Text:
    return ft.Text(text, size=15, weight=ft.FontWeight.W_600, color=TEXT)


def label(text: str, color: str = MUTED, size: int = 13, **kw) -> ft.Text:
    return ft.Text(text, size=size, color=color, **kw)


def card(*controls: ft.Control, padding: int = GAP, expand: bool | int = False, spacing: int = 10) -> ft.Container:
    return ft.Container(ft.Column(list(controls), spacing=spacing, tight=True), bgcolor=CARD, padding=padding,
                        border_radius=RADIUS, border=ft.Border.all(1, LINE), expand=expand)


def pill(text: str, color: str) -> ft.Container:
    return ft.Container(ft.Text(text, size=12, weight=ft.FontWeight.W_600, color=color),
                        bgcolor=RAISED, border_radius=20, padding=ft.Padding(10, 4, 10, 4))


def primary(text: str, on_click, icon=None, disabled: bool = False) -> ft.FilledButton:
    return ft.FilledButton(text, icon=icon, on_click=on_click, disabled=disabled,
                           style=ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF",
                                                shape=ft.RoundedRectangleBorder(radius=10)))


def secondary(text: str, on_click, icon=None, disabled: bool = False) -> ft.OutlinedButton:
    return ft.OutlinedButton(text, icon=icon, on_click=on_click, disabled=disabled,
                             style=ft.ButtonStyle(color=TEXT, shape=ft.RoundedRectangleBorder(radius=10),
                                                  side=ft.BorderSide(1, LINE)))


def notice(text: str, color: str = WARN, icon=ft.Icons.INFO_OUTLINE) -> ft.Container:
    return ft.Container(ft.Row([ft.Icon(icon, color=color, size=18),
                                ft.Text(text, color=TEXT, size=13, expand=True)], spacing=10),
                        bgcolor=RAISED, border_radius=10, padding=ft.Padding(12, 10, 12, 10))


def mark() -> ft.Container:
    """The rail's mark: the PokeUDS logo, or the old "UDS" tile if the logo file is missing."""
    if LOGO.is_file():
        return ft.Container(ft.Image(LOGO.read_bytes(), width=40, height=40, fit=ft.BoxFit.COVER),
                            width=40, height=40, border_radius=12, clip_behavior=ft.ClipBehavior.ANTI_ALIAS)
    return ft.Container(ft.Text("UDS", size=12, weight=ft.FontWeight.W_700, color="#FFFFFF"),
                        width=40, height=40, bgcolor=ACCENT, border_radius=12, alignment=ft.Alignment.CENTER)
