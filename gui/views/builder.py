"""The builder, on the Trade page ("Build one"): a legal Pokemon made by PKHeX for someone with no file to send.

It builds for the game the Trade page has chosen. Pick the species and the level, add any wishes, and
Build: the PKHeX helper walks the species' encounters and returns the first record its legality check
accepts with every wish (docs/pkhex.md). The result goes into the bank and becomes the trade's offer.
"""

from __future__ import annotations

import threading
from typing import Callable

import flet as ft

from pokeuds.app import bank, builder
from pokeuds.app.builder import ABILITIES, GAMES, GENDERS, Trainer, Wish

from .. import theme as t
from .common import View
from .picker import name_field
from .sprite import sprite

ANY = "-"
TOP = ft.CrossAxisAlignment.START       # fields align by their boxes: a helper or counter line hangs below


def beside(control: ft.Control) -> ft.Control:
    """A switch at a dense field's height, so it sits level with the field's box rather than its helper line."""
    return ft.Container(control, height=48, alignment=ft.Alignment.CENTER_LEFT)


def _helper_ready() -> bool:
    from . import pkhex_setup

    return pkhex_setup.ready()


class BuilderPanel(View):
    """Not a page of its own: the Trade page embeds its ``control()`` and gets each built Pokemon."""

    key = "builder"

    def __init__(self, app, game: str, on_built: Callable[[bank.Entry, str], None]) -> None:
        super().__init__(app)
        self.on_built = on_built
        self.wish = Wish(game=game if game in GAMES else "xy", species=25, level=30)
        self.lists: dict[str, dict] = {}
        self.loading = ""
        self.building = False
        self.compiling = False
        self.result = None                  # pkhex.Built
        self.saved: bank.Entry | None = None
        self.message, self.message_color = "", t.INFO

    @property
    def trainer(self) -> Trainer:
        return Trainer.from_dict(self.app.settings.trainer, self.app.settings.trainer_name or "POKEUDS")

    def _save_trainer(self, trainer: Trainer) -> None:
        self.app.settings.trainer = trainer.to_dict()
        self.app.save()

    # -- layout ---------------------------------------------------------------------------------

    def set_game(self, game: str) -> None:
        """Follow the Trade page's game (ids of moves and items are each game's own)."""
        if game in GAMES and game != self.wish.game:
            self._apply_game(game)

    def build(self) -> ft.Control:
        name = GAMES[self.wish.game][0]
        head = [t.heading(f"Build a Pokemon for {name}"),
                t.label("No file to send? PKHeX builds a legal one from an encounter in this game and checks it. "
                        "It goes into the bank and becomes the offer.")]
        if not _helper_ready():
            return self._no_helper(head)
        lists = self.lists.get(self.wish.game)
        if lists is None:
            self._load_lists(self.wish.game)
            return t.card(*head, ft.Row([ft.ProgressRing(width=18, height=18),
                                         t.label("Asking PKHeX for the game's species, moves and items...")], spacing=12))
        rows = head + ([t.notice(self.message, self.message_color)] if self.message else [])
        rows += self._pokemon(lists) + [ft.Divider(color=t.LINE)] + self._trainer() + [self._build_row()]
        return t.card(*rows, spacing=12)

    def _no_helper(self, head: list[ft.Control]) -> ft.Control:
        from . import pkhex_setup

        return pkhex_setup.card(self.app, head)

    @staticmethod
    def _dd(label: str, value, options: list[tuple[str, str]], on_select, width: int = 220, search: bool = False,
            disabled: bool = False) -> ft.Dropdown:
        return ft.Dropdown(label=label, value=value, width=width, dense=True, disabled=disabled, menu_height=360,
                           options=[ft.DropdownOption(key=k, text=v) for k, v in options], on_select=on_select,
                           enable_filter=search, editable=search)

    def _pokemon(self, lists: dict) -> list[ft.Control]:
        w, gen = self.wish, builder.generation(self.wish.game)
        busy = self.building
        names = ft.Column([
            ft.Row([name_field("Species", w.species, lists["species"], self._pick_species, 200, disabled=busy),
                    ft.TextField(label="Level", value=str(w.level), width=100, dense=True, on_blur=self._level,
                                 disabled=busy, keyboard_type=ft.KeyboardType.NUMBER),
                    beside(ft.Switch(label="Shiny", value=w.shiny, on_change=self._shiny, disabled=busy or gen == 1))],
                   spacing=12, vertical_alignment=TOP),
            ft.TextField(label="Nickname", value=w.nickname, width=200, dense=True, disabled=busy,
                         max_length=builder.NAME_LIMITS[gen][0], on_blur=self._nickname),
        ], spacing=8)
        preview = sprite(self.app, w.species, shiny=w.shiny and gen >= 2, size=112, on_ready=self.refresh)
        rows: list[ft.Control] = [ft.Row([names, ft.Container(expand=True), preview], spacing=24, vertical_alignment=TOP)]
        wishes: list[ft.Control] = [self._dd("Gender", w.gender or ANY, [(k or ANY, v) for k, v in GENDERS.items()],
                                             self._gender, 140, disabled=busy)]
        if gen >= 3:
            wishes.append(self._dd("Nature", ANY if w.nature is None else str(w.nature),
                                   [(ANY, "Any")] + [(str(v), n) for v, n in sorted(lists["natures"], key=lambda x: x[1])],
                                   self._nature, 160, disabled=busy))
            wishes.append(self._dd("Ability", w.ability or ANY, [(k or ANY, v) for k, v in ABILITIES.items()],
                                   self._ability, 180, disabled=busy))
        rows.append(ft.Row(wishes, wrap=True, spacing=12, vertical_alignment=TOP))
        carried: list[ft.Control] = []
        if gen >= 3:
            carried.append(self._dd("Ball", str(w.ball) if w.ball else ANY,
                                    [(ANY, "Any")] + [(str(v), n) for v, n in lists["balls"]], self._ball, 180,
                                    disabled=busy))
        if gen >= 2:
            carried.append(name_field("Held item", w.held_item or 0, lists["items"], self._pick_item, 200, empty="none",
                                      disabled=busy))
        if carried:
            rows.append(ft.Row(carried, wrap=True, spacing=12, vertical_alignment=TOP))
        current = (w.moves + [0, 0, 0, 0])[:4]
        rows.append(t.label("Moves: leave them empty for the moves it would know at that level."))
        rows.append(ft.Row([name_field(f"Move {i + 1}", m, lists["moves"], lambda v, i=i: self._pick_move(i, v), 190,
                                       empty="none", disabled=busy)
                            for i, m in enumerate(current)], wrap=True, spacing=12, vertical_alignment=TOP))
        return rows

    def _build_row(self) -> ft.Control:
        busy = self.building
        return ft.Row([t.primary("Build and offer it", self._build, ft.Icons.AUTO_FIX_HIGH, disabled=busy or self.app.busy()),
                       ft.ProgressRing(width=18, height=18, visible=busy), t.label(self.wish.label())], spacing=12)

    def _trainer(self) -> list[ft.Control]:
        tr, gen = self.trainer, builder.generation(self.wish.game)
        busy = self.building
        fields: list[ft.Control] = [ft.TextField(label="OT name", value=tr.ot, width=180, dense=True, disabled=busy,
                                                 max_length=builder.NAME_LIMITS[gen][1],
                                                 on_blur=self._ot)]
        if gen >= 7:
            fields += [ft.TextField(label="TID (6 digits)", value=tr.shown_id(7), width=140, dense=True, disabled=busy,
                                    on_blur=lambda e: self._g7_id(e, None)),
                       ft.TextField(label="SID (4 digits)", value=str(tr.g7_sid()), width=130, dense=True, disabled=busy,
                                    on_blur=lambda e: self._g7_id(None, e))]
        else:
            fields.append(ft.TextField(label="TID", value=f"{tr.tid:05d}", width=110, dense=True, disabled=busy,
                                       on_blur=lambda e: self._id(e, "tid")))
            if gen >= 3:
                fields.append(ft.TextField(label="SID", value=f"{tr.sid:05d}", width=110, dense=True, disabled=busy,
                                           on_blur=lambda e: self._id(e, "sid")))
        if gen >= 2:
            fields.append(beside(ft.Switch(label="Female OT", value=tr.female, on_change=self._ot_gender, disabled=busy)))
        return [t.label("Original trainer: shown on its summary. Use your own to make it yours; kept for the next build.",
                        color=t.TEXT),
                ft.Row(fields, wrap=True, spacing=12, vertical_alignment=TOP)]

    # -- the helper -----------------------------------------------------------------------------

    def _load_lists(self, game: str) -> None:
        if self.loading == game:
            return
        self.loading = game

        def work() -> None:
            from pokeuds import pkhex

            try:
                self.lists[game] = pkhex.shared().lists(game)
            except Exception as exc:               # the helper failed to start or answer
                self.lists[game] = {"species": [], "moves": [], "items": [], "balls": [], "natures": []}
                self.message, self.message_color = f"PKHeX did not answer: {exc}", t.BAD
            self.loading = ""
            self.app.ui(self.refresh)

        threading.Thread(target=work, daemon=True, name="pkhex-lists").start()

    def _build(self, e) -> None:
        wish, trainer = self.wish, self.trainer
        if problems := wish.problems():
            self.message, self.message_color = "; ".join(problems).capitalize() + ".", t.BAD
            self.refresh()
            return
        self.building, self.message = True, ""
        self.refresh()

        def work() -> None:
            try:
                self.result = builder.build(wish, trainer)
                self.saved = builder.keep(self.result, bank.Bank(self.app.settings.bank or None), wish)
                self.message = ""
                verdict = "legal" if self.result.legal else "NOT legal"
                saved, text = self.saved, f"Built {self.saved.describe()} ({verdict}), kept it in the bank and made it the offer."
                self.building = False
                self.app.ui(lambda: self.on_built(saved, text))
                return
            except ValueError as exc:              # PKHeX found nothing legal with these wishes
                self.result, self.saved = None, None
                self.message, self.message_color = f"PKHeX could not build it: {exc}", t.BAD
            except Exception as exc:
                self.result, self.saved = None, None
                self.message, self.message_color = f"The PKHeX helper failed: {exc}", t.BAD
            self.building = False
            self.app.ui(self.refresh)

        threading.Thread(target=work, daemon=True, name="pkhex-build").start()

    # -- inputs ---------------------------------------------------------------------------------

    def _changed(self) -> None:
        self.message = ""
        self.refresh()

    def _apply_game(self, game: str) -> None:
        self.wish.game = game
        gen = builder.generation(self.wish.game)
        if self.wish.species > GAMES[self.wish.game][2]:
            self.wish.species = 25
        if gen < 3:
            self.wish.nature, self.wish.ability, self.wish.ball = None, "", None
        if gen < 2:
            self.wish.held_item, self.wish.shiny = None, False
        self.wish.moves = []                 # move and item ids are the game's own lists
        self.wish.held_item = None
        self.message = ""

    def _pick_species(self, value: int) -> None:
        if value:
            self.wish.species = value
            self.wish.moves = []
            self._changed()

    def _pick_item(self, value: int) -> None:
        self.wish.held_item = value or None

    def _pick_move(self, slot: int, value: int) -> None:
        moves = (self.wish.moves + [0, 0, 0, 0])[:4]
        moves[slot] = value
        self.wish.moves = [m for m in moves if m]

    def _species(self, e) -> None:
        if e.control.value and e.control.value.isdigit():
            self.wish.species = int(e.control.value)
            self.wish.moves = []
            self._changed()

    def _level(self, e) -> None:
        try:
            self.wish.level = max(1, min(100, int(e.control.value)))
        except (TypeError, ValueError):
            pass
        self._changed()

    def _nickname(self, e) -> None:
        self.wish.nickname = (e.control.value or "").strip()

    def _shiny(self, e) -> None:
        self.wish.shiny = bool(e.control.value)
        self.refresh()                       # the preview turns shiny

    def _gender(self, e) -> None:
        self.wish.gender = "" if e.control.value == ANY else e.control.value

    def _nature(self, e) -> None:
        self.wish.nature = None if e.control.value == ANY else int(e.control.value)

    def _ability(self, e) -> None:
        self.wish.ability = "" if e.control.value == ANY else e.control.value

    def _item(self, e) -> None:
        value = e.control.value
        self.wish.held_item = int(value) if value and value.isdigit() else None

    def _ball(self, e) -> None:
        self.wish.ball = None if e.control.value == ANY else int(e.control.value)

    def _move(self, slot: int, e) -> None:
        moves = (self.wish.moves + [0, 0, 0, 0])[:4]
        value = e.control.value
        moves[slot] = int(value) if value and value.isdigit() else 0
        self.wish.moves = [m for m in moves if m]

    def _ot(self, e) -> None:
        tr = self.trainer
        tr.ot = (e.control.value or "").strip() or tr.ot
        self._save_trainer(tr)

    def _ot_gender(self, e) -> None:
        tr = self.trainer
        tr.female = bool(e.control.value)
        self._save_trainer(tr)

    def _id(self, e, which: str) -> None:
        tr = self.trainer
        try:
            value = int(e.control.value)
            if not 0 <= value <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            self.message, self.message_color = "A TID or SID is 0 to 65535.", t.BAD
            self.refresh()
            return
        setattr(tr, which, value)
        self._save_trainer(tr)

    def _g7_id(self, id_event, sid_event) -> None:
        tr = self.trainer
        try:
            shown = int(id_event.control.value) if id_event else int(tr.shown_id(7))
            sid4 = int(sid_event.control.value) if sid_event else tr.g7_sid()
            tr.set_g7(shown, sid4)
        except (TypeError, ValueError) as exc:
            self.message, self.message_color = str(exc) if str(exc) else "That is not a number.", t.BAD
            self.refresh()
            return
        self._save_trainer(tr)
