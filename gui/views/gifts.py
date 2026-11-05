"""The Gifts page: Gen 6/7 Mystery Gift cards (docs/mystery_gift.md).

Three ways in, one result panel:

- Official events: the cards PKHeX.Core carries for the game, searchable.
- Open a card: a .wc6/.wc7, or a 784-byte .wc6full/.wc7full distribution file.
- Build your own: a custom card from a form, saved as a card file.

A card's Pokemon is made as the Builder's trainer would receive it, checked by PKHeX, and can be kept in the
bank and offered in a trade: the gift Pokemon by trade, until local distribution is understood. A custom
card's Pokemon matches no real event, so PKHeX flags it; the page says so.
"""

from __future__ import annotations

import threading
from pathlib import Path

import flet as ft

from pokeuds import pkx
from pokeuds.app import bank, catalog, giftfile
from pokeuds.app.builder import Trainer

from .. import theme as t
from .common import View
from .gift_editor import GiftEditor

GAMES = {"xy": "X / Y", "oras": "Omega Ruby / Alpha Sapphire", "sm": "Sun / Moon", "usum": "Ultra Sun / Ultra Moon"}
MODES = (("official", "Official events"), ("file", "Open a card"), ("custom", "Build your own"))
SHOWN = 150


def _helper_ready() -> bool:
    from pokeuds import pkhex

    return pkhex.helper_path() is not None


class GiftsView(View):
    key, label, icon = "gifts", "Gifts", ft.Icons.CARD_GIFTCARD

    def __init__(self, app) -> None:
        super().__init__(app)
        self.game = "oras"
        self.mode = "official"
        self.search = ""
        self.cards: dict[str, list[dict]] = {}
        self.loading = False
        self.selected = ""
        self.gift = None                    # pkhex.Gift on show
        self.saved: bank.Entry | None = None
        self.working = False
        self.message, self.message_color = "", t.INFO
        self.editor = GiftEditor(app, self.game, self.trainer, self._editor_result)

    @property
    def generation(self) -> int:
        return 6 if self.game in ("xy", "oras") else 7

    def trainer(self) -> dict:
        tr = Trainer.from_dict(self.app.settings.trainer, self.app.settings.trainer_name or "POKEUDS")
        return {"ot": tr.ot, "tid": tr.tid, "sid": tr.sid, "gender": 1 if tr.female else 0}

    # -- layout ---------------------------------------------------------------------------------

    def build(self) -> ft.Control:
        controls: list[ft.Control] = [
            t.title("Gifts"),
            t.label("Mystery Gift cards for X/Y, ORAS, Sun/Moon and Ultra Sun/Ultra Moon. PokeUDS cannot hand a card to "
                    "a game yet (how local distribution works is still unknown), but a card's Pokemon can go by trade."),
        ]
        if not _helper_ready():
            controls.append(t.notice("Gifts need the PKHeX helper: dotnet build -c Release services/pkhex "
                                     "(or Build it now under Build one on the Trade page).", t.WARN))
            return ft.Column(controls, spacing=t.GAP, scroll=ft.ScrollMode.AUTO)
        controls.append(ft.Row([
            ft.Dropdown(label="Game", value=self.game, width=280, dense=True, on_select=self._game,
                        options=[ft.DropdownOption(key=k, text=v) for k, v in GAMES.items()]),
            ft.SegmentedButton(segments=[ft.Segment(k, label=ft.Text(v)) for k, v in MODES], selected=[self.mode],
                               on_change=self._mode),
        ], spacing=12, wrap=True))
        if self.message:
            controls.append(t.notice(self.message, self.message_color))
        left = {"official": self._official, "file": self._file, "custom": self._custom}[self.mode]()
        controls.append(ft.Row([ft.Container(left, expand=True), ft.Container(self._result(), width=400)],
                               vertical_alignment=ft.CrossAxisAlignment.START, spacing=t.GAP))
        return ft.Column(controls, spacing=t.GAP, scroll=ft.ScrollMode.AUTO)

    def _official(self) -> ft.Control:
        cards = self.cards.get(self.game)
        if cards is None:
            self._load(self.game)
            return t.card(ft.Row([ft.ProgressRing(width=18, height=18),
                                  t.label("Reading PKHeX's event cards...")], spacing=12))
        needle = self.search.lower()
        shown = [c for c in cards if not needle or needle in " ".join(
            str(c.get(k, "")) for k in ("title", "species_name", "ot", "card_id", "held_item")).lower()]
        rows: list[ft.Control] = [
            ft.TextField(hint_text="Search title, species, OT or card number", value=self.search, dense=True,
                         prefix_icon=ft.Icons.SEARCH, on_submit=self._search, on_blur=self._search),
            t.label(f"{len(shown)} of {len(cards)} Pokemon cards" + (f", first {SHOWN} shown" if len(shown) > SHOWN else "")
                    + ". A card with its own OT gives a Pokemon traded from that trainer; the others make you its OT.",
                    size=12),
        ]
        for c in shown[:SHOWN]:
            chosen = c["key"] == self.selected
            rows.append(ft.Container(ft.Row([
                t.pill(f"{c['card_id']:04d}", t.ACCENT),
                ft.Column([ft.Text(c["title"] or "(untitled)", size=14, color=t.TEXT),
                           t.label(" · ".join(x for x in (
                               f"{c['species_name']} Lv {c['level']}" + (" egg" if c["kind"] == "egg" else ""),
                               {"always": "shiny", "may be": "may be shiny"}.get(c["shiny"], ""),
                               f"OT {c['ot']}" if c["ot"] else "your OT", c.get("date", "")) if x), size=12)],
                          spacing=1, expand=True),
            ], spacing=12), padding=8, border_radius=8, bgcolor=t.RAISED if chosen else None, ink=True,
                on_click=lambda e, k=c["key"]: self._pick(k)))
        return t.card(*rows, spacing=4, padding=12)

    def _file(self) -> ft.Control:
        return t.card(
            t.heading("Open a card"),
            t.label(f"A .wc{self.generation} (264 bytes, as PKHeX and the event galleries keep them) or a "
                    f".wc{self.generation}full (784 bytes: the distribution header, then the card). The game above "
                    "decides how it is read."),
            t.primary("Choose a card file...", self._open, ft.Icons.FILE_OPEN, disabled=self.working),
            spacing=12)

    def _custom(self) -> ft.Control:
        self.editor.set_game(self.game)
        return self.editor.control()

    def _editor_result(self, gift) -> None:
        """The editor previewed or exported a card: show it in the result panel."""
        self.gift, self.saved = gift, None
        self.app.ui(self.refresh)

    def follow_editor(self, game: str) -> None:
        """A .udsgift for another game was opened in the editor."""
        self.game, self.mode = game, "custom"

    def _customize(self, e) -> None:
        """Open the card on show in the editor, to change anything on it."""
        g = self.gift
        if g is None:
            return
        from pokeuds import pkhex

        try:
            fields = pkhex.shared().card_fields(self.game, data=g.card_data)
        except ValueError as exc:
            self._say(str(exc), t.BAD)
            self.refresh()
            return
        if fields.get("kind") not in ("pokemon", "item", "bp", "beans"):
            self._say("This card's kind cannot be edited here.", t.WARN)
            self.refresh()
            return
        self.editor.load_fields(self.game, giftfile.unreceived(fields), name=g.card.get("title", ""))
        self.mode = "custom"
        self.refresh()

    def _result(self) -> ft.Control:
        g = self.gift
        if self.working and g is None:
            return t.card(ft.Row([ft.ProgressRing(width=18, height=18), t.label("Making it...")], spacing=12))
        if g is None:
            return t.card(t.heading("The gift"), t.label("Choose a card, open one or make one."))
        c = g.card
        rows: list[ft.Control] = [
            ft.Row([t.heading(c["title"] or "(untitled)"), ft.Container(expand=True),
                    *([t.pill("Custom", t.WARN)] if g.custom else [])]),
            t.label(f"Card {c['card_id']:04d} · for {', '.join(c['games']) or 'no game here'}"
                    + (f" · {c['date']}" if c.get("date") else ""), size=12),
        ]
        p = g.pokemon
        if p is None:
            what = c.get("item") or c["kind"]
            rows.append(t.label(f"Gives {what} x{c.get('quantity', 1)}. Only a Mystery Gift can deliver this, not a trade.",
                                color=t.TEXT))
        else:
            s = p.summary
            facts = [("Pokemon", f"{s['species_name']} Lv {s['level']}" + (" (shiny)" if s["shiny"] else "")),
                     ("OT", f"{s['ot_name']} {s.get('shown_id', s['tid'])}" + ("" if c.get("ot") else " (you)")),
                     ("Moves", ", ".join(m for m in s["moves"] if m) or "-")]
            if s.get("stats"):
                facts.append(("Stats", " / ".join(str(v) for v in s["stats"])))
                facts.append(("IVs", " / ".join(str(v) for v in s.get("ivs", []))))
                if any(s.get("evs") or []):
                    facts.append(("EVs", " / ".join(str(v) for v in s["evs"])))
            for key, name in (("nature", "Nature"), ("ability", "Ability"), ("held_item", "Item"), ("ball", "Ball")):
                if s.get(key) and s[key] not in ("None", "(None)"):
                    facts.append((name, s[key]))
            rows += [ft.Row([t.label(k, size=12), ft.Text(v, size=13, color=t.TEXT, selectable=True, expand=True)],
                            spacing=8, vertical_alignment=ft.CrossAxisAlignment.START) for k, v in facts]
            rows.append(t.pill("Legal" if p.legal else "Not legal", t.GOOD if p.legal else t.BAD))
            if not p.legal:
                rows.append(ft.Text(p.report, size=12, color=t.MUTED, selectable=True))
            rows.append(ft.Divider(color=t.LINE))
            if self.saved:
                rows.append(t.label("In the bank.", color=t.GOOD))
                rows += [t.primary(f"Offer in {catalog.BY_KEY[k].name}", lambda e, k=k: self._offer(k), ft.Icons.SWAP_HORIZ,
                                   disabled=self.app.busy()) for k in bank.games_for(self.saved)]
            else:
                rows.append(t.primary("Keep its Pokemon in the bank", self._keep, ft.Icons.INVENTORY_2_OUTLINED))
        rows.append(ft.Divider(color=t.LINE))
        rows.append(ft.Row([t.secondary(f"Save .{g.extension}", lambda e: self._save(False), ft.Icons.SAVE_ALT),
                            t.secondary(f"Save .{g.extension}full", lambda e: self._save(True), ft.Icons.SAVE_ALT),
                            *([t.secondary("Customize", self._customize, ft.Icons.EDIT)] if self.mode != "custom" else [])],
                           wrap=True, spacing=8))
        return t.card(*rows, spacing=8)

    # -- work on the helper ---------------------------------------------------------------------

    def _say(self, text: str, color: str = t.INFO) -> None:
        self.message, self.message_color = text, color

    def _thread(self, name: str, work) -> None:
        def run() -> None:
            try:
                work()
            except ValueError as exc:           # the helper refused: say why
                self._say(str(exc), t.BAD)
            except Exception as exc:
                self._say(f"The PKHeX helper failed: {exc}", t.BAD)
            self.working = self.loading = False
            self.app.ui(self.refresh)

        threading.Thread(target=run, daemon=True, name=name).start()

    def _load(self, game: str) -> None:
        if self.loading:
            return
        self.loading = True

        def work() -> None:
            from pokeuds import pkhex

            self.cards[game] = pkhex.shared().gifts(game)

        self._thread("pkhex-gifts", work)

    def _show(self, make) -> None:
        self.working, self.gift, self.saved, self.message = True, None, None, ""
        self.refresh()

        def work() -> None:
            self.gift = make()

        self._thread("pkhex-gift", work)

    def _pick(self, key: str) -> None:
        self.selected = key
        game, trainer = self.game, self.trainer()
        from pokeuds import pkhex

        self._show(lambda: pkhex.shared().gift(game, key=key, trainer=trainer))

    async def _open(self, e) -> None:
        ext = f"wc{self.generation}"
        files = await self.app.picker.pick_files(dialog_title="Open a Mystery Gift card", file_type=ft.FilePickerFileType.CUSTOM,
                                                 allowed_extensions=[ext, ext + "full"])
        if not (files and files[0].path):
            return
        try:
            data = Path(files[0].path).read_bytes()
        except OSError as exc:
            self._say(str(exc), t.BAD)
            self.refresh()
            return
        game, trainer = self.game, self.trainer()
        from pokeuds import pkhex

        self._show(lambda: pkhex.shared().gift(game, data=data, trainer=trainer))

    # -- inputs and results ---------------------------------------------------------------------

    def _game(self, e) -> None:
        self.game = e.control.value
        self.selected, self.gift, self.saved, self.message = "", None, None, ""
        self.refresh()

    def _mode(self, e) -> None:
        self.mode = next(iter(e.control.selected), "official")
        self.message = ""
        self.refresh()

    def _search(self, e) -> None:
        value = (e.control.value or "").strip()
        if value != self.search:
            self.search = value
            self.refresh()

    def _keep(self, e) -> None:
        g = self.gift
        if not (g and g.pokemon):
            return
        p = g.pokemon
        b = bank.Bank(self.app.settings.bank or None)
        entry, new = b.deposit(pkx.to_stored(p.data), p.extension, source="gift", note=g.card["title"],
                               extra={"card_id": g.card["card_id"], "custom_card": g.custom})
        self.saved = b.set_legality(entry, p.legal, p.report, p.encounter)
        self._say(f"Kept {self.saved.describe()} in the bank." if new else "That Pokemon is already in the bank.", t.GOOD)
        self.refresh()

    def _offer(self, game: str) -> None:
        if self.saved:
            self.app.settings.offers[game] = str(self.saved.path)
            self.app.views["trade"].pick_game(game)
            self.app.navigate("trade")

    async def _save(self, full: bool) -> None:
        g = self.gift
        if g is None:
            return
        ext = g.extension + ("full" if full else "")
        data = g.full_data if full else g.card_data
        name = "".join(ch for ch in (g.card["title"] or "gift") if ch.isalnum() or ch in " -_").strip()[:40] or "gift"
        path = await self.app.picker.save_file(dialog_title="Save the card", file_name=f"{g.card['card_id']:04d} {name}.{ext}",
                                               file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=[ext],
                                               src_bytes=data)
        if path:
            target = Path(path if path.lower().endswith("." + ext) else f"{path}.{ext}")
            target.write_bytes(data)
            self._say(f"Saved {target} ({len(data)} bytes).", t.GOOD)
            self.refresh()
