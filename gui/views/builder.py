"""The builder, on the Trade page ("Build one"): a legal Pokemon made by PKHeX for someone with no file to send.

It builds for the game the Trade page has chosen. Pick the species and the level, add any wishes, and
Build: PKHeX walks the species' encounters and returns the first record its legality check accepts with
every wish (docs/pkhex.md). The result goes into the bank and becomes the trade's offer.

Import paste (its own button beside Build one, ``paste_control``) reads a Showdown or Smogon set, or a whole
team, with PKHeX's own parser; Use this one fills the fields from the set picked and opens the builder. What the
game lacks is listed. Build and offer it queues the result for the trade; Build and save in Bank only keeps it.
"""

from __future__ import annotations

import threading
from typing import Callable

import flet as ft

from pokeuds.app import bank, builder
from pokeuds.app.builder import ABILITIES, GAMES, GENDERS, HIDDEN_POWER, STATS, Pasted, Trainer, Wish

from .. import theme as t
from ..translate import translate
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

    def __init__(self, app, game: str, on_built: Callable[[bank.Entry, str], None],
                 on_pasted: Callable[[], None] | None = None) -> None:
        super().__init__(app)
        self.on_built = on_built
        self.on_pasted = on_pasted          # a pasted set filled the fields: the Trade page opens the builder
        self.paste_root = ft.Container()    # Import paste is drawn apart from the builder (paste_control)
        self.wish = Wish(game=game if game in GAMES else "xy", species=25, level=30)
        self.lists: dict[str, dict] = {}
        self.loading = ""
        self.building = False
        self.compiling = False
        self.result = None                  # pkhex.Built
        self.saved: bank.Entry | None = None
        self.closest = None                 # a closest legal build waiting for Keep this one (services/pkhex/Fix.cs)
        self.closest_offer = True           # what its build button asked: offer it, or only save it
        self.message, self.message_color = "", t.INFO
        # Import paste: its text, a read in progress, and the sets read from it.
        self.paste_text = ""
        self.reading = False
        self.pasted: list[Pasted] | None = None
        self.form_name = ""                 # the form a pasted set chose, shown beside the species
        self.stats_open = False             # the IVs and EVs section (opened by a paste that brings them)

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
                        "It goes into the bank; Build and offer it also queues it for this trade.")]
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

    def refresh(self) -> None:
        super().refresh()
        self.paste_root.content = translate(self._paste_card())
        try:
            self.paste_root.page                 # raises when the paste is not on the page (Flet 1.0)
        except RuntimeError:
            return
        self.paste_root.update()

    # -- import paste ---------------------------------------------------------------------------

    def paste_control(self) -> ft.Container:
        """Import paste, for the Trade page to show under its own button."""
        self.paste_root.content = translate(self._paste_card())
        return self.paste_root

    def _paste_card(self) -> ft.Control:
        name = GAMES[self.wish.game][0]
        head: list[ft.Control] = [t.heading(f"Import a paste for {name}")]
        if not _helper_ready():
            return self._no_helper(head)
        busy = self.building or self.reading
        rows: list[ft.Control] = head + [
            t.label("Paste a Showdown or Smogon set, or a whole team, in any language PKHeX reads. Each set it finds "
                    "is listed; Use this one opens the builder filled with it, to check and build."),
            *([t.notice(self.message, self.message_color)] if self.message and self.pasted is None else []),
            ft.TextField(value=self.paste_text, multiline=True, min_lines=6, max_lines=14, dense=True, disabled=busy,
                         hint_text="Pikachu @ Light Ball\nAbility: Static\nTimid Nature\n- Thunderbolt",
                         on_change=self._paste_changed),
            ft.Row([t.primary("Read it", self._read_paste, ft.Icons.SEARCH, disabled=busy or not self.paste_text.strip()),
                    ft.ProgressRing(width=18, height=18, visible=self.reading)], spacing=12),
        ]
        if self.pasted is not None:
            if not self.pasted:
                rows.append(t.notice("PKHeX found no set in the paste.", t.WARN))
            for i, pasted in enumerate(self.pasted):
                rows.append(self._pasted_card(i, pasted))
        return t.card(*rows, spacing=12)

    def _pasted_card(self, index: int, pasted: Pasted) -> ft.Control:
        lines: list[ft.Control] = [
            ft.Row([ft.Column([t.label(pasted.title(), color=t.TEXT), t.label(pasted.summary(), size=12)],
                              spacing=2, expand=True),
                    t.secondary("Use this one", lambda e, i=index: self._use_paste(i), ft.Icons.CHECK,
                                disabled=not pasted.usable or self.building)],
                   spacing=12)]
        for error in pasted.errors:
            lines.append(t.label(f"Left out: {error}", color=t.BAD, size=12))
        for note in pasted.notes:
            lines.append(t.label(note, size=12))
        return ft.Container(ft.Column(lines, spacing=4, tight=True), bgcolor=t.RAISED, border_radius=10,
                            padding=ft.Padding(12, 10, 12, 10))

    def _paste_changed(self, e) -> None:
        had = bool(self.paste_text.strip())
        self.paste_text = e.control.value or ""
        if had != bool(self.paste_text.strip()):
            self.refresh()                   # Read it turns on or off

    def _read_paste(self, e) -> None:
        if self.reading or not self.paste_text.strip():
            return
        self.reading, self.pasted, self.message = True, None, ""
        self.refresh()
        game, text = self.wish.game, self.paste_text

        def work() -> None:
            try:
                self.pasted = builder.paste(game, text)
            except Exception as exc:              # PKHeX refused or failed
                self.pasted = None
                self.message, self.message_color = f"PKHeX could not read the paste: {exc}", t.BAD
            self.reading = False
            self.app.ui(self.refresh)

        threading.Thread(target=work, daemon=True, name="pkhex-paste").start()

    def _use_paste(self, index: int) -> None:
        if not self.pasted or not 0 <= index < len(self.pasted) or not self.pasted[index].usable:
            return
        pasted = self.pasted[index]
        self.closest = None                 # a new set: any closest build was for the old wishes
        self.wish = pasted.apply(self.wish)
        self.form_name = pasted.form_name
        self.stats_open = self.stats_open or self.wish.ivs is not None or self.wish.evs is not None
        self.pasted = None
        left = f" Left out: {'; '.join(pasted.errors)}" if pasted.errors else ""
        self.message = f"Filled in {pasted.title()} from the paste: check the fields, then build it.{left}"
        self.message_color = t.WARN if pasted.errors else t.GOOD
        if self.on_pasted:
            self.on_pasted()                 # the Trade page closes the paste and opens the builder
        else:
            self.refresh()

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
        form = [t.label(f"Form: {self.form_name}", size=12)] if w.form and self.form_name else []
        names = ft.Column([
            ft.Row([name_field("Species", w.species, lists["species"], self._pick_species, 200, disabled=busy),
                    ft.TextField(label="Level", value=str(w.level), width=100, dense=True, on_blur=self._level,
                                 disabled=busy, keyboard_type=ft.KeyboardType.NUMBER),
                    beside(ft.Switch(label="Shiny", value=w.shiny, on_change=self._shiny, disabled=busy or gen == 1))],
                   spacing=12, vertical_alignment=TOP),
            *form,
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
        rows += self._stats(gen, busy)
        return rows

    # -- IVs and EVs ----------------------------------------------------------------------------

    def _stats(self, gen: int, busy: bool) -> list[ft.Control]:
        w = self.wish
        set_any = w.ivs is not None or w.evs is not None
        gb = gen <= 2
        label = (("Hide the DVs and stat experience" if gb else "Hide the IVs and EVs") if self.stats_open
                 else ("Set DVs and stat experience" if gb else "Set IVs and EVs"))
        toggle = t.secondary(label, self._toggle_stats, ft.Icons.TUNE, disabled=busy)
        if not self.stats_open:
            set_note = t.label("DVs and stat experience are set." if gb else "IVs and EVs are set.", size=12)
            return [ft.Row([toggle] + ([set_note] if set_any else []), spacing=12)]
        ivs, evs = w.ivs or [-1] * 6, w.evs or [0] * 6
        # Gen 1/2 have one Special (kept in SpA and SpD alike) and work out the HP DV from the other four.
        shown = (1, 2, 3, 5) if gen <= 2 else range(6)
        names = {3: "Spc"} if gen <= 2 else {}

        def box(label: str, value, on_blur, width: int = 76) -> ft.TextField:
            return ft.TextField(label=label, value="" if value is None else str(value), width=width, dense=True,
                                disabled=busy, hint_text="any" if value is None else None, on_blur=on_blur,
                                keyboard_type=ft.KeyboardType.NUMBER)

        iv_row: list[ft.Control] = []
        if gen <= 2:
            iv_row.append(t.label(f"HP {builder.gb_hp_dv(ivs)}", size=12))
        iv_row += [box(names.get(i, STATS[i]), None if ivs[i] < 0 else ivs[i], lambda e, i=i: self._set_iv(i, e))
                   for i in shown]
        ev_shown = (0, 1, 2, 3, 5) if gen <= 2 else range(6)
        ev_row: list[ft.Control] = [box(names.get(i, STATS[i]), evs[i], lambda e, i=i: self._set_ev(i, e),
                                        92 if gen <= 2 else 76) for i in ev_shown]
        if gen >= 3:
            ev_row.append(t.label(f"{sum(evs)} / 510", size=12, color=t.BAD if sum(evs) > 510 else t.MUTED))
        rows: list[ft.Control] = [
            ft.Row([toggle, t.secondary("Best DVs" if gb else "Best IVs", self._best_ivs, disabled=busy),
                    t.secondary("Clear them", self._clear_stats, disabled=busy or not set_any)], spacing=12, wrap=True),
            t.label("DVs (0 to 15; empty: the encounter rolls it)" if gen <= 2
                    else "IVs (0 to 31; empty: the encounter rolls it)", size=12),
            ft.Row(iv_row, spacing=8, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            t.label("Stat experience (0 to 65535; a battle gives some to all five)" if gen <= 2
                    else "EVs (0 to 252, 510 in all)", size=12),
            ft.Row(ev_row, spacing=8, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ]
        if gen >= 2:
            kind = builder.hidden_power(w.ivs, gen)
            now = (f"Hidden Power: {HIDDEN_POWER[kind]}" if kind is not None
                   else "Hidden Power: set the Attack and Defense DVs to see its type" if gen == 2
                   else "Hidden Power: set every IV to see its type")
            rows.append(ft.Row([self._dd("Hidden Power type", ANY, [(ANY, "Keep the IVs" if gen >= 3 else "Keep the DVs")]
                                         + [(str(i), n) for i, n in enumerate(HIDDEN_POWER)], self._hidden_power, 220,
                                         disabled=busy),
                                t.label(now, size=12)], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        return rows

    def _toggle_stats(self, e) -> None:
        self.stats_open = not self.stats_open
        self.refresh()

    @staticmethod
    def _number(e) -> int | None:
        text = (e.control.value or "").strip()
        return int(text) if text.lstrip("-").isdigit() else None

    def _set_iv(self, index: int, e) -> None:
        gen = builder.generation(self.wish.game)
        ivs = list(self.wish.ivs or [-1] * 6)
        value = self._number(e)
        ivs[index] = -1 if value is None else value
        if gen <= 2 and index == 3:
            ivs[4] = ivs[3]                          # Special is one DV
        self.wish.ivs = ivs if any(v >= 0 for v in ivs) else None
        self._changed()

    def _set_ev(self, index: int, e) -> None:
        gen = builder.generation(self.wish.game)
        evs = list(self.wish.evs or [0] * 6)
        evs[index] = self._number(e) or 0
        if gen <= 2 and index == 3:
            evs[4] = evs[3]
        self.wish.evs = evs if any(evs) else None
        self._changed()

    def _best_ivs(self, e) -> None:
        best, _ = builder.stat_limits(builder.generation(self.wish.game))
        self.wish.ivs = [best] * 6
        self._changed()

    def _clear_stats(self, e) -> None:
        self.wish.ivs = self.wish.evs = None
        self._changed()

    def _hidden_power(self, e) -> None:
        if e.control.value in (None, ANY):
            return
        self.wish.ivs = builder.with_hidden_power(self.wish.ivs, int(e.control.value),
                                                  builder.generation(self.wish.game))
        self._changed()

    def _build_row(self) -> ft.Control:
        busy = self.building
        if self.closest is not None:
            keep = "Keep this one and offer it" if self.closest_offer else "Keep this one in the Bank"
            return ft.Row([t.primary(keep, lambda e: self._keep_closest(), ft.Icons.CHECK, disabled=busy or self.app.busy()),
                           t.secondary("Back to editing", lambda e: self._drop_closest(), ft.Icons.EDIT, disabled=busy),
                           t.label(self.wish.label())], spacing=12, wrap=True)
        return ft.Row([t.primary("Build and offer it", self._build, ft.Icons.AUTO_FIX_HIGH, disabled=busy or self.app.busy()),
                       t.secondary("Build and save in Bank", lambda e: self._build(e, offer=False),
                                   ft.Icons.INVENTORY_2_OUTLINED, disabled=busy),
                       ft.ProgressRing(width=18, height=18, visible=busy), t.label(self.wish.label())],
                      spacing=12, wrap=True)

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

    def _build(self, e, offer: bool = True) -> None:
        """Build and keep it in the bank; ``offer`` also hands it to the Trade page's queue."""
        wish, trainer = self.wish, self.trainer
        if problems := wish.problems():
            self.message, self.message_color = "; ".join(problems).capitalize() + ".", t.BAD
            self.refresh()
            return
        self.building, self.message = True, ""
        self.refresh()

        def work() -> None:
            try:
                self.result = builder.build(wish, trainer, fix=True)
                if self.result.changes:
                    # Nothing legal is exactly what was asked for: show the closest and let the player choose.
                    self.closest, self.closest_offer = self.result, offer
                    self.message = ("PKHeX cannot build exactly that, so here is the closest legal one. "
                                    + " ".join(self.result.changes) + self._fixes_text(self.result))
                    self.message_color = t.WARN
                    self.building = False
                    self.app.ui(self.refresh)
                    return
                self._keep(self.result, offer)
                return
            except ValueError as exc:              # PKHeX found nothing legal with these wishes
                self.result, self.saved = None, None
                self.message, self.message_color = f"PKHeX could not build it: {exc}", t.BAD
            except Exception as exc:
                self.result, self.saved = None, None
                self.message, self.message_color = f"PKHeX failed: {exc}", t.BAD
            self.building = False
            self.app.ui(self.refresh)

        threading.Thread(target=work, daemon=True, name="pkhex-build").start()

    @staticmethod
    def _fixes_text(built) -> str:
        return (" PKHeX also: " + " ".join(built.fixes)) if built.fixes else ""

    def _keep(self, built, offer: bool) -> None:
        """Keep a build in the bank; ``offer`` also hands it to the Trade page. Runs on the build's thread."""
        self.saved = builder.keep(built, bank.Bank(self.app.settings.bank or None), self.wish)
        self.message = ""
        verdict = "legal" if built.legal else "NOT legal"
        self.building = False
        fixes = self._fixes_text(built)
        if offer:
            saved, text = self.saved, f"Built {self.saved.describe()} ({verdict}), kept it in the bank and made it the offer.{fixes}"
            self.app.ui(lambda: self.on_built(saved, text))
            return
        # Saved only: the builder stays open for the next one; the Bank page has it to offer later.
        self.message = f"Built {self.saved.describe()} ({verdict}) and saved it in the bank.{fixes}"
        self.message_color = t.GOOD if built.legal else t.WARN
        self.app.ui(self.refresh)

    def _keep_closest(self) -> None:
        """Keep the closest build: the fields take what it has (level, moves, ball...), and it goes as the build did."""
        built, offer = self.closest, self.closest_offer
        if built is None:
            return
        builder.take_changes(self.wish, built)
        self.closest = None
        try:
            self._keep(built, offer)
        except (OSError, ValueError) as exc:
            self.message, self.message_color = f"Could not keep it: {exc}", t.BAD
            self.refresh()

    def _drop_closest(self) -> None:
        self.closest, self.result, self.message = None, None, ""
        self.refresh()

    # -- inputs ---------------------------------------------------------------------------------

    def _changed(self) -> None:
        self.message = ""
        self.closest = None                 # a field changed: the closest build was for the old wishes
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
        self.wish.form, self.form_name = 0, ""
        self.wish.ivs = self.wish.evs = None  # DVs and stat experience against IVs and EVs: not the same ranges
        self.pasted = None                   # read for the other game: read it again
        self.message = ""

    def _pick_species(self, value: int) -> None:
        if value:
            self.wish.species = value
            self.wish.moves = []
            self.wish.form, self.form_name = 0, ""
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
            self.wish.form, self.form_name = 0, ""
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
