"""The GTS page: list a banked Pokemon against what you want, browse other players' listings and offer on one, and
see where your own listings and offers stand (pokeuds/app/gts.py, docs/online.md).

Nothing goes online until you list, offer, look or check. Listings and offers are settled when an app opens: this
page runs a round when it opens (and the app runs one at start when something is live), and "Check now" runs one.
"""

from __future__ import annotations

import threading

import flet as ft

from pokeuds import species
from pokeuds.app import bank, gts
from pokeuds.online import gts as wire
from pokeuds.online import relays

from .. import theme as t
from .common import View
from .pickbox import pick_box
from .sprite import sprite

FORMATS = tuple((f, f".{f} · {wire.FORMAT_NAMES[f]}") for f in wire.FORMATS)
ALL = "all"
FORMATS = ((ALL, "All formats"),) + FORMATS
LEVELS = [(n, str(n)) for n in range(1, 101)]


def no_relay(pool) -> str:
    """Why nothing reached the relays, in a sentence the player can act on: each relay's own error."""
    problems = pool.problems()[:3]
    return ("No relay answered" + (": " + "; ".join(problems) if problems else "")
            + ". If this computer is online, a firewall or antivirus may be blocking PokeUDS, or its clock may be "
              "wrong (secure connections need it). Check the relays shows each one.")
LAST_SPECIES = {"pk1": 151, "pk2": 251, "pk6": 721, "pk7": 807}
_options: dict[str, list] = {}


def species_options(fmt: str) -> list:
    if fmt == ALL:
        fmt = "pk7"                          # every format's species: Gen 7 has them all
    """(dex, name) for the species a format holds; the same list object each time (picker caches by id)."""
    if fmt not in _options:
        _options[fmt] = [(i, species.NAMES[i]) for i in range(1, LAST_SPECIES[fmt] + 1)]
    return _options[fmt]


class GtsView(View):
    key, label, icon = "gts", "GTS", ft.Icons.PUBLIC

    def __init__(self, app) -> None:
        super().__init__(app)
        self.pool_factory = relays.Pool         # the tests hand in a fake relay
        self.gts_root = None                    # the bank's .gts; the tests hand in their own
        self.message, self.message_color = "", t.INFO
        self.working = ""                       # what runs in the background: "round", "browse", "list", "offer"
        self.listing_entry = ""                 # the bank entry chosen to list
        self.wants: list[dict] = [{"species": 0, "min_level": 1, "max_level": 100, "shiny": False}]
        self.browse_format = ALL                 # every format: a listing of any generation shows
        self.browse_species = 0
        self.found: list[wire.Listing] | None = None
        self.relay_check: list[tuple[str, bool, str]] | None = None   # Check the relays' last answer
        self.offer_choice: dict[str, str] = {}  # listing id -> the bank entry chosen to offer
        self.opened = False

    # -- the objects behind the page -------------------------------------------------------------

    @property
    def bank(self) -> bank.Bank:
        return bank.Bank(self.app.settings.bank or None)

    @property
    def gts(self) -> gts.Gts:
        found = self.bank
        # The other copy's bank and the received folder tell the player's own listings from someone else's.
        near = gts.nearby(found.folder, self.app.settings.output) if self.gts_root is None else ()
        return gts.Gts(found, self.gts_root, near=near)

    def trainer(self) -> str:
        s = self.app.settings
        return str((s.trainer or {}).get("ot") or s.trainer_name or "POKEUDS")[:16]

    def choose(self, entry_id: str) -> None:
        """From the Bank page: list this one."""
        self.listing_entry = entry_id
        entry = self.bank.find(entry_id)
        if entry is not None:
            self.browse_format = entry.extension
        self.message = ""

    def settle(self) -> None:
        """Once a run, when something of ours is live: a round in the background (the app calls it at start)."""
        if self.opened:
            return
        self.opened = True
        try:
            live = self.gts.live()
        except OSError:
            live = False
        if live:
            self._background("round", self._round_work, redraw=False)

    # -- layout ---------------------------------------------------------------------------------

    def build(self) -> ft.Control:
        self.settle()
        busy = bool(self.working)
        header = ft.Row([
            t.title("GTS"), ft.Container(expand=True),
            *([ft.ProgressRing(width=16, height=16), t.label(self._working_text(), size=12)] if busy else []),
            t.secondary("Check the relays", lambda e: self._background("relays", self._check_relays), ft.Icons.WIFI_FIND,
                        disabled=busy),
            t.secondary("Check now", lambda e: self._background("round", self._round_work), ft.Icons.SYNC, disabled=busy),
        ], spacing=8)
        controls: list[ft.Control] = [header, t.notice(
            "Trade with anyone, any time: list a Pokemon against what you want, or offer on someone else's listing. "
            "A trade settles when the lister's app next opens. The other app is trusted to send the Pokemon it "
            "listed; a changed app could keep yours. Only Pokemon PKHeX finds legal go on the GTS.", t.INFO)]
        if self.message:
            controls.append(t.notice(self.message, self.message_color))
        if self.relay_check is not None:
            controls.append(self._relay_card())
        controls += [self._mine(), self._list_card(busy), self._browse_card(busy)]
        return ft.Column(controls, spacing=12, scroll=ft.ScrollMode.AUTO,
                         horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    def _working_text(self) -> str:
        return {"round": "Talking to the relays...", "browse": "Looking...", "list": "Checking and listing...",
                "offer": "Checking and offering...", "relays": "Checking each relay..."}.get(self.working, "Working...")

    def _check_relays(self) -> None:
        with self.pool_factory() as pool:
            self.relay_check = pool.check() if hasattr(pool, "check") else []

    def _relay_card(self) -> ft.Control:
        """Each relay, and whether it answered: what to look at when nothing does."""
        answered = sum(ok for _, ok, _ in self.relay_check)
        rows: list[ft.Control] = [ft.Row([t.heading(f"Relays: {answered} of {len(self.relay_check)} answered"),
                                          ft.Container(expand=True),
                                          ft.IconButton(ft.Icons.CLOSE, icon_size=18, tooltip="Close",
                                                        on_click=lambda e: self._close_relay_check())])]
        for url, ok, detail in self.relay_check:
            rows.append(ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR_OUTLINE,
                                        color=t.GOOD if ok else t.BAD, size=16),
                                ft.Text(url, size=13, color=t.TEXT, width=280, selectable=True),
                                t.label(detail, size=12, selectable=True, expand=True)], spacing=8))
        if not answered:
            rows.append(t.label("None answered. If this computer is online, a firewall or antivirus may be blocking "
                                "PokeUDS (allow PokeUDS.exe), or the computer's clock may be wrong: secure connections "
                                "need the right date and time.", size=12))
        return t.card(*rows, spacing=6)

    def _close_relay_check(self) -> None:
        self.relay_check = None
        self.refresh()

    def _mine(self) -> ft.Control:
        items = self.gts.items()
        if not items:
            return t.card(t.heading("Yours"), t.label("No listings or offers yet.", size=12))
        rows: list[ft.Control] = [t.heading("Yours")]
        for item in items[:50]:
            s = item.state
            pokemon = s.get("pokemon") or {}
            color = {"open": t.INFO, "waiting": t.INFO, "traded": t.GOOD, "failed": t.BAD}.get(item.status, t.MUTED)
            actions = ([t.secondary("Take down", lambda e, i=item: self._withdraw(i), ft.Icons.REMOVE_CIRCLE_OUTLINE,
                                    disabled=bool(self.working))]
                       if item.kind == "listing" and item.status == "open" else [])
            rows.append(ft.Row([
                sprite(self.app, int(pokemon.get("species") or 0), shiny=bool(pokemon.get("shiny")), size=40,
                       on_ready=self.refresh) if pokemon else ft.Icon(ft.Icons.SWAP_HORIZ, color=t.MUTED),
                t.pill("Listing" if item.kind == "listing" else "Offer", color),
                ft.Column([ft.Text(item.title(), color=t.TEXT, size=14), t.label(item.detail(), size=12)],
                          spacing=1, expand=True),
                *actions], spacing=12))
        return t.card(*rows, spacing=8)

    # listing

    def _list_card(self, busy: bool) -> ft.Control:
        entries = [e for e in self.bank.entries() if e.extension in wire.FORMATS and not e.has_mail]
        rows: list[ft.Control] = [t.heading("List a Pokemon")]
        if not entries:
            rows.append(t.label("The bank has nothing to list (a Pokemon holding mail cannot go).", size=12))
            return t.card(*rows)
        chosen = next((e for e in entries if e.id == self.listing_entry), None)
        rows.append(ft.Dropdown(
            label="From the bank", value=chosen.id if chosen else None, width=460, dense=True, disabled=busy,
            options=[ft.DropdownOption(key=e.id, text=f"{e.describe()} (.{e.extension})") for e in entries[:300]],
            on_select=self._pick_entry))
        if chosen is None:
            return t.card(*rows)
        options = species_options(chosen.extension)
        rows.append(t.label("Wanted in return: one to three Pokemon. Any of them will do.", size=12))
        for i, want in enumerate(self.wants):
            row = [pick_box(f"Want {i + 1}", want["species"] or None, options,
                            lambda e, i=i: self._want(i, "species", int(e.control.value or 0)), width=220,
                            disabled=busy, redraw=self.refresh),
                   self._level(i, "min_level", "From Lv", busy), self._level(i, "max_level", "To Lv", busy)]
            if chosen.extension != "pk1":           # Gen 1 has no shininess
                row.append(ft.Checkbox(label="Shiny only", value=want["shiny"], disabled=busy,
                                       on_change=lambda e, i=i: self._want(i, "shiny", bool(e.control.value))))
            if len(self.wants) > 1:
                row.append(ft.IconButton(ft.Icons.CLOSE, icon_size=18, tooltip="Remove", disabled=busy,
                                         on_click=lambda e, i=i: self._drop_want(i)))
            rows.append(ft.Row(row, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        buttons = [t.primary("List it", lambda e: self._list(chosen), ft.Icons.PUBLISH, disabled=busy)]
        if len(self.wants) < wire.MAX_WANTS:
            buttons.insert(0, t.secondary("Another want", lambda e: self._add_want(), ft.Icons.ADD, disabled=busy))
        rows += [ft.Row(buttons, spacing=8),
                 t.label(f"{chosen.describe()} leaves the bank while listed. Taking the listing down, or 30 days "
                         "without a trade, brings it back.", size=12)]
        return t.card(*rows)

    def _level(self, i: int, key: str, label: str, busy: bool) -> ft.Dropdown:
        return pick_box(label, self.wants[i][key], LEVELS, lambda e: self._want(i, key, e.control.value), width=110,
                        disabled=busy, redraw=self.refresh)

    def _pick_entry(self, e) -> None:
        self.listing_entry = e.control.value or ""
        self.message = ""
        self.refresh()

    def _want(self, i: int, key: str, value) -> None:
        if key in ("min_level", "max_level"):
            try:
                value = max(1, min(100, int(str(value).strip())))
            except ValueError:
                value = 1 if key == "min_level" else 100
        self.wants[i][key] = value
        if key != "species":
            self.refresh()

    def _add_want(self) -> None:
        self.wants.append({"species": 0, "min_level": 1, "max_level": 100, "shiny": False})
        self.refresh()

    def _drop_want(self, i: int) -> None:
        del self.wants[i]
        self.refresh()

    def _wants(self, fmt: str) -> list[wire.Want]:
        out = []
        for w in self.wants:
            if not w["species"]:
                continue
            low, high = sorted((w["min_level"], w["max_level"]))
            out.append(wire.Want(int(w["species"]), species.name(int(w["species"])), low, high,
                                 bool(w["shiny"]) and fmt != "pk1"))
        return out

    def _list(self, entry: bank.Entry) -> None:
        wants = self._wants(entry.extension)
        if not wants:
            self._say("Choose at least one Pokemon you want in return.", t.WARN)
            self.refresh()
            return

        def work() -> None:
            item = self.gts.list_pokemon(entry, wants, self.trainer())
            self.listing_entry = ""
            self.wants = [{"species": 0, "min_level": 1, "max_level": 100, "shiny": False}]
            reached = self._publish_round()
            self._say(f"Listed {item.title()}." + ("" if reached else " No relay answered: it goes up at the next "
                                                   "check."), t.GOOD if reached else t.WARN)

        self._background("list", work)

    # browsing

    def _browse_card(self, busy: bool) -> ft.Control:
        options = species_options(self.browse_format)
        rows: list[ft.Control] = [t.heading("Browse"), ft.Row([
            ft.Dropdown(label="Format", value=self.browse_format, width=400, dense=True, disabled=busy,
                        options=[ft.DropdownOption(key=k, text=v) for k, v in FORMATS], on_select=self._pick_format),
            pick_box("Offering", self.browse_species, [(0, "Any Pokemon")] + options, self._pick_browse_species,
                     width=220, disabled=busy, redraw=self.refresh),
            t.secondary("Look", lambda e: self._background("browse", self._browse_work), ft.Icons.SEARCH, disabled=busy),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)]
        if self.found is None:
            rows.append(t.label("Look to see the open listings.", size=12))
        elif not self.found:
            rows.append(t.label("No open listings match.", size=12))
        else:
            here = self.gts
            ours = here.ours()
            rows += [self._listing_row(listing, ours.whose(listing, here.root), busy) for listing in self.found[:100]]
        return t.card(*rows, spacing=8)

    def _listing_row(self, listing: wire.Listing, own: str, busy: bool) -> ft.Control:
        """``own``: why the listing is the player's own (``Ours.whose``), or ""."""
        p = listing.pokemon
        facts = " · ".join(str(x) for x in (p.get("nickname") and f"'{p['nickname']}'", p.get("nature"),
                                             p.get("ability"), p.get("held_item"), p.get("ot") and f"OT {p['ot']}")
                           if x)
        side: list[ft.Control]
        if own:
            side = [ft.Container(t.pill("Yours", t.MUTED), tooltip=own[0].upper() + own[1:])]
        else:
            fits = self.gts.fits(listing)
            if not fits:
                side = [t.label("Nothing in your bank fits", size=12)]
            else:
                pick = self.offer_choice.get(listing.id) or fits[0].id
                side = [ft.Dropdown(value=pick, width=260, dense=True, disabled=busy,
                                    options=[ft.DropdownOption(key=e.id, text=e.describe()) for e in fits[:50]],
                                    on_select=lambda e, k=listing.id: self.offer_choice.__setitem__(k, e.control.value)),
                        t.primary("Offer", lambda e, l=listing: self._offer(l), ft.Icons.SWAP_HORIZ, disabled=busy)]
        return ft.Container(ft.Row([
            sprite(self.app, int(p.get("species") or 0), shiny=bool(p.get("shiny")), size=44, on_ready=self.refresh),
            ft.Column([ft.Text(listing.title(), color=t.TEXT, size=14),
                       t.label(f"Wants {listing.asks()}", size=12),
                       *([t.label(facts, size=12)] if facts else []),
                       t.label(f"From {listing.trainer or 'someone'}", size=12)], spacing=1, expand=True),
            *side], spacing=12), padding=ft.Padding(4, 6, 4, 6))

    def _pick_format(self, e) -> None:
        self.browse_format = e.control.value or ALL
        self.browse_species, self.found = 0, None
        self.refresh()

    def _pick_browse_species(self, e) -> None:
        value = e.control.value
        self.browse_species = int(value) if value and str(value).isdigit() else 0

    def _browse_work(self) -> None:
        with self.pool_factory() as pool:
            fmt = None if self.browse_format == ALL else self.browse_format
            self.found = self.gts.browse(pool, fmt, has=self.browse_species or None)
            if not pool.reached():
                self._say(no_relay(pool), t.BAD)

    def _offer(self, listing: wire.Listing) -> None:
        fits = {e.id: e for e in self.gts.fits(listing)}
        entry = fits.get(self.offer_choice.get(listing.id, "")) or next(iter(fits.values()), None)
        if entry is None:
            return

        def work() -> None:
            item = self.gts.offer(entry, listing, self.trainer())
            reached = self._publish_round()
            self.found = [f for f in self.found or [] if f.id != listing.id]
            self._say(f"Offered {item.title()} for {listing.title()}. It is traded, or comes back, once "
                      f"{listing.trainer or 'the lister'}'s app opens." + ("" if reached else
                                                                           " No relay answered: it goes at the next check."),
                      t.GOOD if reached else t.WARN)

        self._background("offer", work)

    # settling

    def _withdraw(self, item: gts.Item) -> None:
        try:
            self.gts.withdraw(item)
        except ValueError as exc:
            self._say(str(exc), t.BAD)
            self.refresh()
            return
        self._say(f"Took {item.title()} down; it is back in the bank.", t.GOOD)
        self._background("round", self._round_work, keep_message=True)

    def _publish_round(self) -> bool:
        """Send what is ours now; -> whether any relay answered."""
        with self.pool_factory() as pool:
            said = self.gts.round(pool)
            if said:
                self._say(" ".join(said), t.GOOD)
            return bool(pool.reached())

    def _round_work(self) -> None:
        with self.pool_factory() as pool:
            said = self.gts.round(pool)
            if said:
                self._say(" ".join(said), t.GOOD)
            elif not pool.reached():
                self._say(no_relay(pool), t.BAD)

    def _background(self, kind: str, work, keep_message: bool = False, redraw: bool = True) -> None:
        if self.working:
            return
        self.working = kind
        if not keep_message:
            self.message = ""
        if redraw:
            self.refresh()

        def run() -> None:
            try:
                work()
            except Exception as exc:        # PKHeX's verdict, a full disk, a relay gone strange
                self._say(str(exc), t.BAD)
            self.working = ""
            self.app.ui(self.refresh)

        threading.Thread(target=run, daemon=True, name=f"gts-{kind}").start()

    def _say(self, text: str, color: str = t.INFO) -> None:
        self.message, self.message_color = text, color
