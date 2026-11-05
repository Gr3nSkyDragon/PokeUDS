"""The custom Mystery Gift editor (Gifts page, Build your own): every field of a Gen 6/7 card.

The state is the helper's card schema (services/pkhex/CardEditor.cs, docs/wonder_cards.md) as a dict, so what is
saved (.udsgift), exported (.wc6/.wc7, full file) and opened (any of those, or an official card via Customize)
is the same thing. Preview asks PKHeX for the card and the Pokemon it gives, stats included.
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Callable

import flet as ft

from pokeuds.app import giftfile
from pokeuds.app.giftfile import CONTEST, RIBBONS, STATS

from .. import theme as t
from .common import View
from .picker import name_field

KINDS = {6: (("pokemon", "Pokemon"), ("egg", "Pokemon egg"), ("item", "Item")),
         7: (("pokemon", "Pokemon"), ("egg", "Pokemon egg"), ("item", "Items"), ("bp", "Battle Points"),
             ("beans", "Poke Beans"))}
VERSION_BITS = {6: (("X", 1), ("Y", 2), ("Alpha Sapphire", 4), ("Omega Ruby", 8)),
                7: (("Sun", 1), ("Moon", 2), ("Ultra Sun", 4), ("Ultra Moon", 8))}
ORIGINS = {6: ((0, "The receiver's game"), (24, "X"), (25, "Y"), (26, "Alpha Sapphire"), (27, "Omega Ruby")),
           7: ((0, "The receiver's game"), (30, "Sun"), (31, "Moon"), (32, "Ultra Sun"), (33, "Ultra Moon"))}
SHINY = (("random", "May be shiny"), ("always", "Always shiny"), ("never", "Never shiny"), ("fixed", "Fixed PID"))
ABILITY = (("random12", "First or second"), ("random12h", "Any, hidden too"), ("1", "First"), ("2", "Second"),
           ("hidden", "Hidden"))
GENDER = ((-1, "Random"), (0, "Male"), (1, "Female"), (2, "Genderless"))
NONE = "-"


class GiftEditor(View):
    key = "gift-editor"

    def __init__(self, app, game: str, trainer: Callable[[], dict], on_result: Callable) -> None:
        super().__init__(app)
        self.game = game
        self.trainer = trainer
        self.on_result = on_result
        self.f = giftfile.new_fields(game)
        self.name = ""
        self.path: Path | None = None
        self.lists: dict[str, dict] = {}
        self.texts: dict[str, list] = {}
        self.loading = False
        self.working = False
        self.more = False
        self.last = None                    # the last preview (pkhex.Gift)
        self.message, self.message_color = "", t.INFO

    @property
    def gen(self) -> int:
        return giftfile.generation(self.game)

    def set_game(self, game: str) -> None:
        """Follow the page's game; ids of moves, items and texts are each game's own, so a kind or text the
        other generation lacks falls back to the default."""
        if game == self.game:
            return
        old_gen, self.game = self.gen, game
        if self.f["kind"] in ("bp", "beans") and self.gen == 6:
            self.f = giftfile.new_fields(game)
        if old_gen != self.gen:
            self.f["text"] = giftfile.new_fields(game)["text"]
            if self.f["kind"] == "item":
                self.f["items"] = self.f["items"][:1]
        self.last = None

    def load_fields(self, game: str, fields: dict, name: str = "", path: Path | None = None) -> None:
        self.game, self.f, self.name, self.path, self.last = game, copy.deepcopy(fields), name, path, None
        self.message = ""

    # -- layout ---------------------------------------------------------------------------------

    def build(self) -> ft.Control:
        lists, texts = self.lists.get(self.game), self.texts.get(self.game)
        if lists is None or texts is None:
            self._load()
            return t.card(ft.Row([ft.ProgressRing(width=18, height=18),
                                  t.label("Asking PKHeX for the game's species, moves, items, places and card texts...")],
                                 spacing=12))
        sections: list[ft.Control] = []
        if self.message:
            sections.append(t.notice(self.message, self.message_color))
        sections.append(self._card_section(texts))
        kind = self.f["kind"]
        if kind == "pokemon":
            sections += [self._pokemon_section(lists), self._moves_section(lists), self._stats_section(),
                         self._trainer_section(), self._ribbons_section(), self._more_section(lists)]
        elif kind == "item":
            sections.append(self._items_section(lists))
        else:
            sections.append(self._points_section())
        sections.append(self._actions())
        return ft.Column(sections, spacing=t.GAP)

    # small builders

    def _set(self, key: str, value) -> None:
        self.f[key] = value

    def _num(self, label: str, value, setter, width: int = 110, lo: int = 0, hi: int = 0xFFFF, blank=None,
             hint: str = "") -> ft.TextField:
        def commit(e) -> None:
            text = (e.control.value or "").strip()
            if text == "" and blank is not None:
                setter(blank)
                return
            try:
                number = int(text, 0)
            except ValueError:
                self._say(f"{label}: {text!r} is not a number", t.BAD)
                return
            if not lo <= number <= hi:
                self._say(f"{label} is {lo} to {hi}", t.BAD)
                return
            setter(number)
        shown = "" if (blank is not None and value == blank) else str(value)
        return ft.TextField(label=label, value=shown, width=width, dense=True, on_blur=commit, hint_text=hint,
                            keyboard_type=ft.KeyboardType.NUMBER)

    def _text(self, label: str, key: str, width: int, limit: int) -> ft.TextField:
        return ft.TextField(label=label, value=str(self.f.get(key) or ""), width=width, dense=True, max_length=limit,
                            on_blur=lambda e: self._set(key, (e.control.value or "")[:limit]))

    def _dd(self, label: str, value, options, setter, width: int = 220, search: bool = False) -> ft.Dropdown:
        keys = [str(k) for k, _ in options]
        shown = str(value) if str(value) in keys else (keys[0] if keys else None)
        return ft.Dropdown(label=label, value=shown, width=width, dense=True, menu_height=360, enable_filter=search,
                           editable=search, options=[ft.DropdownOption(key=str(k), text=v) for k, v in options],
                           on_select=lambda e: setter(e.control.value))

    @staticmethod
    def _named(pairs, none: str | None = None, sort: bool = False):
        items = sorted(pairs, key=lambda p: p[1]) if sort else list(pairs)
        return ([(NONE, none)] if none else []) + [(v, n) for v, n in items]

    # sections

    def _card_section(self, texts) -> ft.Control:
        f, gen = self.f, self.gen
        text_options = [(i, f"Text {i} · {n} official card{'s' if n != 1 else ''}" + (f", e.g. {ex[0]}" if ex else ""))
                        for i, n, ex in texts]
        if f["text"] not in [i for i, *_ in texts]:
            text_options.insert(0, (f["text"], f"Text {f['text']} (no official card uses it)"))
        rows: list[ft.Control] = [
            t.heading("The card"),
            ft.Row([self._text("Title", "title", 380, 36),
                    self._num("Card number", f["card_id"], lambda v: self._set("card_id", v), 130, 0, 0xFFFF),
                    ft.TextField(label="Date (YYYY-MM-DD)", value=f.get("date", ""), width=170, dense=True,
                                 on_blur=lambda e: self._set("date", (e.control.value or "").strip()))],
                   wrap=True, spacing=12),
            ft.Row([self._dd("Card text", f["text"], text_options, lambda v: self._set("text", int(v)), 560)],
                   spacing=12),
            t.label("The title is yours to write. The card's text is one of the game's own messages, chosen by number: "
                    "each is named by official cards that use it. Wonder Cards have no background to choose; the game "
                    "draws the card from what it gives.", size=12),
            ft.Row([self._dd("Gives", self._shown_kind(), KINDS[gen], self._kind, 200),
                    ft.Switch(label="Can be received more than once", value=bool(f.get("multi_obtain")),
                              on_change=lambda e: self._set("multi_obtain", bool(e.control.value)))]
                   + ([ft.Switch(label="Once a day", value=bool(f.get("once_per_day")),
                                 on_change=lambda e: self._set("once_per_day", bool(e.control.value)))] if gen == 7 else []),
                   wrap=True, spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            t.label("For these games (none ticked: all four)", size=12),
            ft.Row([ft.Checkbox(label=name, value=bool(int(f.get("versions", 0)) & bit),
                                on_change=lambda e, b=bit: self._version(b, e.control.value))
                    for name, bit in VERSION_BITS[gen]], spacing=16),
            t.heading("Local distribution (.wc%dfull)" % gen),
            ft.Row([ft.TextField(label="Distribution text", value=f.get("distribution_text", ""), width=420, dense=True,
                                 multiline=True, min_lines=2, max_lines=4, max_length=252,
                                 hint_text="Empty: \"<what it gives> will be given to you as a gift!\"",
                                 on_blur=lambda e: self._set("distribution_text", e.control.value or "")),
                    ft.Switch(label="Halo while receiving", value=bool(f.get("halo")),
                              on_change=lambda e: self._set("halo", bool(e.control.value)))],
                   wrap=True, spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            t.label("What the game shows while it receives the gift from a distributor. Only the full file carries it.",
                    size=12),
        ]
        return t.card(*rows, spacing=10)

    def _pokemon_section(self, lists) -> ft.Control:
        f, gen = self.f, self.gen
        languages = [(0, "The receiver's")] + [(v, n) for v, n in lists.get("languages", [])]
        egg = giftfile.is_egg(f)
        row1 = [name_field("Species", f["species"], lists["species"], lambda v: self._int("species", v or 25), 200),
                self._num("Form", f.get("form", 0), lambda v: self._set("form", v), 80, 0, 63)]
        if egg:
            row1.append(name_field("Egg from", f.get("egg_location", giftfile.EGG_PLACE), lists.get("egg_locations", []),
                                   lambda v: self._set("egg_location", v or giftfile.EGG_PLACE), 240))
        else:
            row1 += [self._num("Level", f["level"], lambda v: self._set("level", v), 90, 1, 100),
                     self._num("Met level", f.get("met_level", f["level"]), lambda v: self._set("met_level", v), 100, 0, 100),
                     self._text("Nickname", "nickname", 180, 12)]
        row2 = [self._dd("Shiny", f.get("shiny", "random"), SHINY, lambda v: self._set("shiny", v), 170)]
        if f.get("shiny") == "fixed":
            row2.append(self._num("PID (0x...)", f.get("pid", 0), lambda v: self._set("pid", v), 150, 0, 0xFFFFFFFF))
        row2 += [self._dd("Gender", f.get("gender", -1), GENDER, lambda v: self._int("gender", v), 140),
                 self._dd("Nature", f.get("nature", -1), [(-1, "Random")] + sorted(lists["natures"], key=lambda x: x[1]),
                          lambda v: self._int("nature", v), 150),
                 self._dd("Ability", f.get("ability", "random12"), ABILITY, lambda v: self._set("ability", v), 180)]
        row3 = [self._dd("Ball", f.get("ball", 4), lists["balls"], lambda v: self._int("ball", v), 180),
                name_field("Held item", f.get("held_item", 0), lists["items"], lambda v: self._set("held_item", v), 220,
                           empty="none")]
        if gen == 7:
            row3.append(name_field("Second item (bag)", f.get("additional_item", 0), lists["items"],
                                   lambda v: self._set("additional_item", v), 220, empty="none"))
        row3.append(self._dd("Language", f.get("language", 0), languages, lambda v: self._int("language", v), 200))
        rows: list[ft.Control] = [t.heading("The egg" if egg else "The Pokemon"), ft.Row(row1, wrap=True, spacing=12)]
        if egg:
            rows.append(t.label("It arrives as an Egg (the game names it so) and hatches at level 1 for whoever hatches "
                                "it, with the nature, ability, IVs, moves, ball and item set here. Official event eggs: "
                                "from a Pokemon Center, the receiver as OT; Gen 6 never shiny in a Poke Ball, Gen 7 in "
                                "a Cherish Ball.", size=12))
        rows += [ft.Row(row2, wrap=True, spacing=12),
                 ft.Row(row3, wrap=True, spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)]
        return t.card(*rows, spacing=10)

    def _moves_section(self, lists) -> ft.Control:
        def row(key: str, label: str) -> ft.Row:
            current = (list(self.f.get(key) or []) + [0, 0, 0, 0])[:4]
            return ft.Row([name_field(f"{label} {i + 1}", m, lists["moves"], lambda v, i=i: self._slot(key, i, v, 4), 190,
                                      empty="none") for i, m in enumerate(current)], wrap=True, spacing=12)

        return t.card(t.heading("Moves"),
                      t.label("Empty moves: what the species knows at its level. Relearn moves are the ones it can "
                              "always relearn (the event moves on official cards).", size=12),
                      row("moves", "Move"), row("relearn", "Relearn"), spacing=10)

    def _stats_section(self) -> ft.Control:
        f = self.f
        ivs = (list(f.get("ivs") or []) + [-1] * 6)[:6]
        evs = (list(f.get("evs") or []) + [0] * 6)[:6]
        rows: list[ft.Control] = [
            t.heading("Stats"),
            t.label("A card cannot hold stats: the game works them out from the species, level, nature, IVs and EVs. "
                    "Set those here; Preview shows the stats the game will give it.", size=12),
            t.label("IVs (0 to 31; empty: random)", color=t.TEXT, size=13),
            ft.Row([self._num(name, iv, lambda v, i=i: self._six("ivs", i, v, -1), 80, 0, 31, blank=-1, hint="random")
                    for i, (name, iv) in enumerate(zip(STATS, ivs))]
                   + [self._dd("Perfect IVs among the random", f.get("perfect_ivs", 0),
                               [(0, "None set"), (1, "1"), (2, "2"), (3, "3")], lambda v: self._int("perfect_ivs", v), 230)],
                   wrap=True, spacing=8),
            t.label(f"EVs (0 to 252, at most 510 in all; now {sum(evs)})", color=t.TEXT, size=13),
            ft.Row([self._num(name, ev, lambda v, i=i: self._six("evs", i, v, 0), 80, 0, 252)
                    for i, (name, ev) in enumerate(zip(STATS, evs))], wrap=True, spacing=8),
        ]
        p = self.last.pokemon if self.last else None
        if p is not None and p.summary.get("stats"):
            s = p.summary
            rows.append(t.label(f"From the last preview, Lv {s['level']} {s.get('nature') or ''}: "
                                + "  ".join(f"{n} {v}" for n, v in zip(STATS, s["stats"]))
                                + "   (IVs " + "/".join(str(v) for v in s["ivs"]) + ")", color=t.GOOD, size=13))
        return t.card(*rows, spacing=8)

    def _trainer_section(self) -> ft.Control:
        f, gen = self.f, self.gen
        named = bool(f.get("ot"))
        rows: list[ft.Control] = [
            t.heading("Original trainer"),
            ft.SegmentedButton(segments=[ft.Segment("receiver", label=ft.Text("Whoever receives it")),
                                         ft.Segment("named", label=ft.Text("Named on the card"))],
                               selected=["named" if named else "receiver"], on_change=self._ot_mode),
        ]
        if named:
            rows.append(ft.Row([
                self._text("OT name", "ot", 180, 12),
                self._num("TID", f.get("tid", 0), lambda v: self._set("tid", v), 100, 0, 65535),
                self._num("SID", f.get("sid", 0), lambda v: self._set("sid", v), 100, 0, 65535),
                ft.Switch(label="Female OT", value=f.get("ot_gender") == 1,
                          on_change=lambda e: self._set("ot_gender", 1 if e.control.value else 0)),
            ], wrap=True, spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER))
            rows.append(t.label("A Pokemon traded from that trainer, as most official events give.", size=12))
        else:
            rows.append(t.label("The receiving save becomes its OT." + (
                " (Gen 6 cards cannot say that for a Pokemon, so PokeUDS writes the trainer from the Builder's settings "
                "into the card.)" if gen == 6 else ""), size=12))
        rows.append(self._dd("Origin game", f.get("origin_game", 0), ORIGINS[gen], lambda v: self._int("origin_game", v), 240))
        return t.card(*rows, spacing=10)

    def _ribbons_section(self) -> ft.Control:
        chosen = set(self.f.get("ribbons") or [])
        boxes = [ft.Container(ft.Checkbox(label=label, value=key in chosen,
                                          on_change=lambda e, k=key: self._ribbon(k, e.control.value)), width=170)
                 for key, label in RIBBONS]
        rows = [ft.Row(boxes[i:i + 4], spacing=4) for i in range(0, len(boxes), 4)]   # Flet stacks wrapped checkboxes
        return t.card(t.heading("Ribbons"), *rows, spacing=2)

    def _more_section(self, lists) -> ft.Control:
        f = self.f
        rows: list[ft.Control] = [ft.Row([t.heading("More"), ft.Container(expand=True),
                                          ft.TextButton("Hide" if self.more else "Show", on_click=self._toggle_more)])]
        if self.more:
            contest = (list(f.get("contest") or []) + [0] * 6)[:6]
            memory = f.get("memory") or {}
            rows += [
                ft.Row([name_field("Met at", f.get("met_location", giftfile.LOVELY_PLACE), lists.get("locations", []),
                                   lambda v: self._set("met_location", v), 300,
                                   empty="none yet (an egg: set when it hatches)" if giftfile.is_egg(f) else ""),
                        *([] if giftfile.is_egg(f) else [
                            name_field("Egg from", f.get("egg_location", 0), lists.get("egg_locations", []),
                                       lambda v: self._set("egg_location", v), 300, empty="none")])],
                       wrap=True, spacing=12),
                t.label("Event places: a lovely place, a Pokemon Center, a Pokemon event, the PGL, an online Present...",
                        size=12),
                ft.Row([self._num("Encryption constant (0: random)", f.get("encryption_constant", 0),
                                  lambda v: self._set("encryption_constant", v), 260, 0, 0xFFFFFFFF)], spacing=12),
                t.label("Contest stats", color=t.TEXT, size=13),
                ft.Row([self._num(name, v, lambda x, i=i: self._six("contest", i, x, 0), 90, 0, 255)
                        for i, (name, v) in enumerate(zip(CONTEST, contest))], wrap=True, spacing=8),
                t.label("OT memory (numbers as the games store them)", color=t.TEXT, size=13),
                ft.Row([self._num(label, memory.get(key, 0), lambda v, k=key: self._memory(k, v), 110, 0, 0xFFFF)
                        for key, label in (("memory", "Memory"), ("intensity", "Intensity"), ("feeling", "Feeling"),
                                           ("variable", "Variable"))], wrap=True, spacing=8),
            ]
        return t.card(*rows, spacing=10)

    def _items_section(self, lists) -> ft.Control:
        items = list(self.f.get("items") or [{"item": 1, "quantity": 1}])
        limit = 1 if self.gen == 6 else 6
        rows: list[ft.Control] = [t.heading("Items" if limit > 1 else "Item")]
        for i, item in enumerate(items):
            rows.append(ft.Row([
                name_field("Item", item.get("item", 1), lists["items"], lambda v, i=i: self._item(i, "item", v or 1), 260),
                self._num("How many", item.get("quantity", 1), lambda v, i=i: self._item(i, "quantity", v), 110, 1, 999),
                *([ft.IconButton(ft.Icons.REMOVE_CIRCLE_OUTLINE, tooltip="Remove", on_click=lambda e, i=i: self._drop_item(i))]
                  if len(items) > 1 else []),
            ], spacing=12))
        if len(items) < limit:
            rows.append(t.secondary("Add an item", self._add_item, ft.Icons.ADD))
        return t.card(*rows, spacing=10)

    def _points_section(self) -> ft.Control:
        f = self.f
        rows: list[ft.Control] = [t.heading("Battle Points" if f["kind"] == "bp" else "Poke Beans")]
        if f["kind"] == "beans":
            rows.append(self._num("Bean kind (as the game numbers them)", f.get("bean", 0), lambda v: self._set("bean", v), 280, 0, 255))
        rows.append(self._num("How many", f.get("quantity", 1), lambda v: self._set("quantity", v), 140, 1, 9999))
        return t.card(*rows, spacing=10)

    def _actions(self) -> ft.Control:
        ext = f"wc{self.gen}"
        return t.card(
            ft.Row([t.primary("Preview", self._preview, ft.Icons.VISIBILITY, disabled=self.working),
                    ft.ProgressRing(width=18, height=18, visible=self.working),
                    t.secondary("Save gift...", self._save, ft.Icons.SAVE),
                    t.secondary("Open...", self._open, ft.Icons.FOLDER_OPEN),
                    t.secondary(f"Export .{ext}", lambda e: self._export(False), ft.Icons.FILE_DOWNLOAD_OUTLINED),
                    t.secondary(f"Export .{ext}full", lambda e: self._export(True), ft.Icons.FILE_DOWNLOAD_OUTLINED),
                    ft.TextButton("Start over", on_click=self._reset)], wrap=True, spacing=8),
            t.label("Save gift keeps everything you set in a .udsgift to open and change later. Export writes the card "
                    f"itself: .{ext} (264 bytes, as PKHeX and the event galleries keep them) or the 784-byte full file."
                    + (f"  Last saved: {self.path.name}" if self.path else ""), size=12),
            spacing=8)

    # -- edits ----------------------------------------------------------------------------------

    def _say(self, text: str, color: str = t.INFO) -> None:
        self.message, self.message_color = text, color
        self.refresh()

    def _int(self, key: str, value, none=None) -> None:
        if value in (None, "", NONE):
            self.f[key] = none if none is not None else 0
            return
        try:
            self.f[key] = int(value)
        except ValueError:
            return
        if key in ("species", "perfect_ivs", "nature"):
            self.refresh()

    def _slot(self, key: str, i: int, value, size: int) -> None:
        values = (list(self.f.get(key) or []) + [0] * size)[:size]
        values[i] = 0 if value in (None, "", NONE) else int(value)
        self.f[key] = values

    def _six(self, key: str, i: int, value: int, default: int) -> None:
        values = (list(self.f.get(key) or []) + [default] * 6)[:6]
        values[i] = value
        self.f[key] = values
        if key == "evs":
            self.refresh()

    def _memory(self, key: str, value: int) -> None:
        memory = dict(self.f.get("memory") or {})
        memory[key] = value
        self.f["memory"] = memory

    def _ribbon(self, key: str, on: bool) -> None:
        chosen = [r for r in (self.f.get("ribbons") or []) if r != key]
        self.f["ribbons"] = chosen + ([key] if on else [])

    def _version(self, bit: int, on: bool) -> None:
        versions = int(self.f.get("versions", 0))
        self.f["versions"] = versions | bit if on else versions & ~bit

    def _shown_kind(self) -> str:
        return "egg" if giftfile.is_egg(self.f) else self.f["kind"]

    def _kind(self, value) -> None:
        if value == self._shown_kind():
            return
        keep = {k: self.f[k] for k in ("card_id", "title", "text", "date", "versions", "multi_obtain", "distribution_text", "halo") if k in self.f}
        self.f = {**giftfile.new_fields(self.game, value), **keep}
        self.last = None
        self.refresh()

    def _ot_mode(self, e) -> None:
        mode = next(iter(e.control.selected), "receiver")
        for key in ("ot", "tid", "sid", "ot_gender"):
            self.f.pop(key, None)
        if mode == "named":
            tr = self.trainer()
            self.f.update(ot=tr.get("ot") or "POKEUDS", tid=tr.get("tid", 12345), sid=tr.get("sid", 54321),
                          ot_gender=tr.get("gender", 0))
        else:
            self.f["ot"] = ""
        self.refresh()

    def _item(self, i: int, key: str, value) -> None:
        items = [dict(x) for x in (self.f.get("items") or [{"item": 1, "quantity": 1}])]
        items[i][key] = int(value)
        self.f["items"] = items

    def _add_item(self, e) -> None:
        self.f["items"] = list(self.f.get("items") or []) + [{"item": 1, "quantity": 1}]
        self.refresh()

    def _drop_item(self, i: int) -> None:
        items = list(self.f.get("items") or [])
        del items[i]
        self.f["items"] = items
        self.refresh()

    def _toggle_more(self, e) -> None:
        self.more = not self.more
        self.refresh()

    def _reset(self, e) -> None:
        self.f, self.name, self.path, self.last, self.message = giftfile.new_fields(self.game), "", None, None, ""
        self.refresh()

    # -- the helper -----------------------------------------------------------------------------

    def _load(self) -> None:
        if self.loading:
            return
        self.loading = True
        game = self.game

        def work() -> None:
            from pokeuds import pkhex

            try:
                helper = pkhex.shared()
                self.lists[game] = helper.lists(game)
                self.texts[game] = giftfile.card_texts(helper.gifts(game, all=True))
            except Exception as exc:
                self.lists[game], self.texts[game] = {"species": [], "moves": [], "items": [], "balls": [], "natures": []}, []
                self.message, self.message_color = f"PKHeX did not answer: {exc}", t.BAD
            self.loading = False
            self.app.ui(self.refresh)

        threading.Thread(target=work, daemon=True, name="pkhex-editor-lists").start()

    def make(self):
        """The card for the current fields (blocking; the helper is one request)."""
        from pokeuds import pkhex

        fields = {k: v for k, v in self.f.items() if v is not None}
        return pkhex.shared().card(self.game, trainer=self.trainer(), **fields)

    def _preview(self, e=None) -> None:
        self.working = True
        self.message = ""
        self.refresh()

        def work() -> None:
            try:
                self.last = self.make()
                self.on_result(self.last)
            except ValueError as exc:
                self.message, self.message_color = f"The card cannot be made: {exc}", t.BAD
            except Exception as exc:
                self.message, self.message_color = f"The PKHeX helper failed: {exc}", t.BAD
            self.working = False
            self.app.ui(self.refresh)

        threading.Thread(target=work, daemon=True, name="pkhex-card").start()

    async def _save(self, e) -> None:
        name = "".join(ch for ch in (self.f.get("title") or "gift") if ch.isalnum() or ch in " -_").strip()[:40] or "gift"
        path = await self.app.picker.save_file(dialog_title="Save the gift", file_name=f"{name}{giftfile.EXTENSION}",
                                               file_type=ft.FilePickerFileType.CUSTOM,
                                               allowed_extensions=[giftfile.EXTENSION.lstrip(".")])
        if not path:
            return
        try:
            card = self.make().card_data
        except Exception as exc:              # still saved: the fields are what counts
            card = b""
            self.message = f"Saved, but the card cannot be made yet: {exc}"
        self.path = giftfile.save(path, giftfile.GiftProject(self.game, copy.deepcopy(self.f), self.name, card))
        if card:
            self.message, self.message_color = f"Saved {self.path}.", t.GOOD
        else:
            self.message_color = t.WARN
        self.refresh()

    async def _open(self, e) -> None:
        ext = f"wc{self.gen}"
        files = await self.app.picker.pick_files(dialog_title="Open a gift", file_type=ft.FilePickerFileType.CUSTOM,
                                                 allowed_extensions=[giftfile.EXTENSION.lstrip("."), ext, ext + "full"])
        if not (files and files[0].path):
            return
        path = Path(files[0].path)
        try:
            if path.suffix.lower() == giftfile.EXTENSION:
                project = giftfile.load(path)
                self.load_fields(project.game, project.fields, project.name, path)
                if project.problems:
                    self.message, self.message_color = "; ".join(project.problems), t.WARN
                self.app.views["gifts"].follow_editor(project.game)
            else:
                from pokeuds import pkhex

                fields = pkhex.shared().card_fields(self.game, data=path.read_bytes())
                self.load_fields(self.game, giftfile.unreceived(fields))
                self.message, self.message_color = f"Opened {path.name}: change anything, then Preview.", t.GOOD
        except (OSError, ValueError) as exc:
            self.message, self.message_color = str(exc), t.BAD
        self.refresh()

    async def _export(self, full: bool) -> None:
        try:
            gift = self.make()
        except Exception as exc:
            self._say(f"The card cannot be made: {exc}", t.BAD)
            return
        ext = gift.extension + ("full" if full else "")
        data = gift.full_data if full else gift.card_data
        name = "".join(ch for ch in (self.f.get("title") or "gift") if ch.isalnum() or ch in " -_").strip()[:40] or "gift"
        path = await self.app.picker.save_file(dialog_title="Export the card", file_name=f"{self.f['card_id']:04d} {name}.{ext}",
                                               file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=[ext], src_bytes=data)
        if path:
            target = Path(path if path.lower().endswith("." + ext) else f"{path}.{ext}")
            target.write_bytes(data)
            self.last = gift
            self.on_result(gift)
            self._say(f"Exported {target} ({len(data)} bytes).", t.GOOD)
