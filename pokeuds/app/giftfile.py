"""Custom Mystery Gifts kept between sessions: the ``.udsgift`` file, and what the card editor offers.

A ``.udsgift`` is UTF-8 JSON holding the editor's fields (the schema in services/pkhex/CardEditor.cs and
docs/wonder_cards.md) and, for reference, the card they made:

    {"format": "pokeuds.gift", "version": 1, "game": "usum", "name": "Shiny Mimikyu",
     "fields": {...}, "card": "<264 bytes, hex>", "sha256": "<of the card>"}

The fields are what counts: opening a file rebuilds the card from them. The card and its hash only show
what was exported when it was saved. A ``.wc6``/``.wc7`` is the card itself (export), and can be opened
into the editor too (``Helper.card_fields``).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

FORMAT = "pokeuds.gift"
VERSION = 1
EXTENSION = ".udsgift"
GAMES = ("xy", "oras", "sm", "usum")
MAX_BYTES = 256 * 1024
CARD_SIZE, FULL_SIZE = 0x108, 0x310

# The card's ribbons, in the order the editor shows them.
RIBBONS = (
    ("classic", "Classic"), ("wishing", "Wishing"), ("premier", "Premier"), ("event", "Event"), ("birthday", "Birthday"),
    ("special", "Special"), ("souvenir", "Souvenir"), ("world", "World"), ("earth", "Earth"), ("national", "National"),
    ("country", "Country"), ("champion_battle", "Battle Champion"), ("champion_regional", "Regional Champion"),
    ("champion_national", "National Champion"), ("champion_world", "World Champion"),
)
STATS = ("HP", "Atk", "Def", "SpA", "SpD", "Spe")
CONTEST = ("Cool", "Beauty", "Cute", "Smart", "Tough", "Sheen")
LOVELY_PLACE = 40001


def generation(game: str) -> int:
    return 6 if game in ("xy", "oras") else 7


def new_fields(game: str, kind: str = "pokemon") -> dict:
    """A blank card as the editor starts it: what most official cards hold."""
    gen = generation(game)
    # card_flags 1: bit 0 "not repeatable", set on nearly every official card; bit 1 would mark it received.
    fields = {"card_id": 2000, "title": "PokeUDS gift", "text": 43 if gen == 6 else 44, "date": date.today().isoformat(),
              "kind": kind, "versions": 0, "multi_obtain": False, "card_flags": 1}
    if kind == "pokemon":
        fields.update(species=25, form=0, level=10, met_level=10, is_egg=False, egg_location=0, met_location=LOVELY_PLACE,
                      ball=4, held_item=0, moves=[0, 0, 0, 0], relearn=[0, 0, 0, 0], nickname="", language=0, ot="",
                      origin_game=0, shiny="random", pid=0, encryption_constant=0, nature=-1, gender=-1,
                      ability="random12", ivs=[-1] * 6, perfect_ivs=0, evs=[0] * 6, contest=[0] * 6,
                      ribbons=["classic"])
    elif kind == "item":
        fields["items"] = [{"item": 1, "quantity": 1}]
    else:
        fields.update(quantity=1)
        if kind == "beans":
            fields["bean"] = 0
    return fields


@dataclass
class GiftProject:
    game: str
    fields: dict
    name: str = ""
    card: bytes = b""                # the card as exported when saved (reference only)
    problems: list[str] = field(default_factory=list)

    def title(self) -> str:
        return self.name or str(self.fields.get("title") or "Custom gift")


def save(path: str | Path, project: GiftProject) -> Path:
    path = Path(path)
    if path.suffix.lower() != EXTENSION:
        path = path.with_suffix(EXTENSION)
    body = {"format": FORMAT, "version": VERSION, "game": project.game, "name": project.title(),
            "fields": project.fields}
    if project.card:
        body["card"] = project.card.hex()
        body["sha256"] = hashlib.sha256(project.card).hexdigest()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return path


def load(path: str | Path) -> GiftProject:
    """Read a .udsgift, refusing what is not one (wrong format, a newer version, an unknown game, bad fields)."""
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{path.name} is larger than a gift file can be")
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name} is not a PokeUDS gift file ({exc})") from None
    if not isinstance(body, dict) or body.get("format") != FORMAT:
        raise ValueError(f"{path.name} is not a PokeUDS gift file")
    version = body.get("version")
    if not isinstance(version, int) or version < 1:
        raise ValueError(f"{path.name} has no valid version")
    if version > VERSION:
        raise ValueError(f"{path.name} was made by a newer PokeUDS (gift file version {version}); update to open it")
    game, fields = body.get("game"), body.get("fields")
    if game not in GAMES:
        raise ValueError(f"{path.name}: unknown game {game!r}")
    if not isinstance(fields, dict) or fields.get("kind") not in ("pokemon", "item", "bp", "beans"):
        raise ValueError(f"{path.name} holds no card fields")
    card, problems = b"", []
    if isinstance(body.get("card"), str):
        try:
            card = bytes.fromhex(body["card"])
        except ValueError:
            problems.append("the saved card is not hex; it is rebuilt from the fields")
        if card and hashlib.sha256(card).hexdigest() != body.get("sha256"):
            problems.append("the saved card does not match its hash; it is rebuilt from the fields")
            card = b""
    return GiftProject(game, fields, str(body.get("name") or ""), card, problems)


def unreceived(fields: dict) -> dict:
    """A card taken from a save can carry its "received" bit (0x52 bit 1); one to hand out must not."""
    fields = dict(fields)
    fields["card_flags"] = int(fields.get("card_flags", 1)) & ~0x02
    return fields


def card_texts(gifts: list[dict]) -> list[tuple[int, int, list[str]]]:
    """The card texts official cards use, most used first: (index, cards, a few example titles in Latin script).

    ``gifts`` is ``Helper.gifts(game, all=True)``. The words are the game's own; PokeUDS cannot read them, so
    the editor names each text by the official cards that carry it."""
    counts: Counter[int] = Counter()
    examples: dict[int, list[str]] = {}
    for card in gifts:
        if card.get("card_id", 0) <= 0:
            continue
        text = int(card.get("text", 0))
        counts[text] += 1
        title = (card.get("title") or "").strip()
        seen = examples.setdefault(text, [])
        if title and title.isascii() and title not in seen and len(seen) < 3:
            seen.append(title)
    return [(text, n, examples.get(text, [])) for text, n in counts.most_common()]
