"""The PokeLDN banner above every page (docs/gui.md, The PokeLDN banner).

PokeUDS is for the 3DS; pokeldn, the project it follows, trades with the Switch games. The banner points players
looking for those to it, and once closed stays closed (Settings, ``pokeldn_banner``).
"""

from __future__ import annotations

import webbrowser

import flet as ft

from . import theme as t
from .translate import translate

POKELDN = "https://github.com/Decryptu/pokeldn"


def banner(app) -> ft.Container:
    """The banner, or an empty, hidden container once the player has closed it."""
    box = ft.Container(visible=bool(app.settings.pokeldn_banner), padding=ft.Padding(0, t.GAP, t.GAP, 0))

    def close(_) -> None:
        app.settings.pokeldn_banner = False
        app.save()
        box.visible = False
        box.update()

    link = ft.TextButton("PokeLDN", icon=ft.Icons.OPEN_IN_NEW, tooltip=POKELDN,
                         on_click=lambda e: webbrowser.open(POKELDN),
                         style=ft.ButtonStyle(color=t.ACCENT, padding=ft.Padding(8, 0, 8, 0),
                                              shape=ft.RoundedRectangleBorder(radius=8)))
    box.content = ft.Container(
        ft.Row([ft.Icon(ft.Icons.VIDEOGAME_ASSET, color=t.INFO, size=20),
                ft.Text("Looking for Switch games? Check out", color=t.TEXT, size=13),
                link,
                ft.Text("on GitHub.", color=t.TEXT, size=13, expand=True),
                ft.IconButton(ft.Icons.CLOSE, icon_size=18, tooltip="Hide", on_click=close)],
               spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=t.RAISED, border_radius=10, padding=ft.Padding(12, 4, 8, 4))
    return translate(box)
