"""The Received page: the Pokemon in the output folder."""

from __future__ import annotations

import time
from pathlib import Path

import flet as ft

from .. import theme as t
from .common import View, open_folder

EXTENSIONS = (".pk1", ".pk2", ".pk6", ".pk7")


def describe(path: Path) -> str:
    try:
        if path.suffix in (".pk1", ".pk2"):
            from pokeuds import gbpk

            return gbpk.load(path).describe()
        from pokeuds import pkx

        s = pkx.summary(pkx.load(path))
        return f"species {s.species} '{s.nickname}', OT {s.ot_name}, PID {s.pid:08X}"
    except (OSError, ValueError) as exc:
        return f"unreadable: {exc}"


def received_files(folder: str) -> list[Path]:
    root = Path(folder).expanduser()
    if not root.is_dir():
        return []
    return sorted((p for p in root.iterdir() if p.suffix.lower() in EXTENSIONS), key=lambda p: p.stat().st_mtime,
                  reverse=True)


class ReceivedView(View):
    key, label, icon = "received", "Received", ft.Icons.INVENTORY_2_OUTLINED

    def build(self) -> ft.Control:
        folder = self.app.settings.output
        files = received_files(folder)
        header = ft.Row([t.title("Received"), ft.Container(expand=True),
                         ft.IconButton(ft.Icons.REFRESH, tooltip="Look again", on_click=lambda e: self.refresh()),
                         t.secondary("Open folder", lambda e: open_folder(folder), ft.Icons.FOLDER_OUTLINED)])
        if not files:
            body = t.card(t.label(f"Nothing in {folder} yet. Pokemon the 3DS sends are saved here, one file per trade."))
        else:
            rows = []
            for path in files[:200]:
                stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
                rows.append(ft.Container(ft.Row([
                    t.pill(path.suffix[1:].upper(), t.ACCENT),
                    ft.Column([ft.Text(describe(path), color=t.TEXT, size=14), t.label(path.name, size=12)],
                              spacing=1, expand=True),
                    t.label(stamp, size=12)], spacing=12), padding=ft.Padding(4, 8, 4, 8),
                    border=ft.Border(bottom=ft.BorderSide(1, t.LINE))))
            body = t.card(*rows, spacing=0)
        return ft.Column([header, t.label(folder, size=12), body], spacing=12, scroll=ft.ScrollMode.AUTO)
