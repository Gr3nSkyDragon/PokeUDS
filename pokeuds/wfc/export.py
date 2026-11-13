"""Make the gift folders the DLS1 server serves, from the official cards PKHeX.Core embeds (docs/ds_wifi.md).

PokeUDS ships no Nintendo files. This asks the PKHeX helper for each DS card's Wi-Fi file (``.myg`` for Gen 4,
``.bin`` for Gen 5; docs/mystery_gift.md, "DS Wi-Fi gift files") and writes them into the layout
:mod:`pokeuds.wfc.dls1` reads, with a ``_list.txt`` per folder.

Gen 4 cards go to the folder of every game they reach (Diamond and Pearl are separate folders, as AltWFC's are);
Gen 5 cards all go to ``IRA`` because every Black/White/Black 2/White 2 cartridge asks for that code and names the
real game separately. A Gen 5 card limited to one language becomes one file with ``MYSTERY_<letter>``; a card any
language may receive is written once per requested language, its text left in English because PKHeX has no
translated distribution text.
"""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

GAMES = ("dp", "pt", "hgss", "bw", "b2w2")
LANG_LETTERS = {1: "J", 2: "E", 3: "F", 4: "I", 5: "G", 6: "S", 7: "K"}
LETTER_IDS = {letter: language for language, letter in LANG_LETTERS.items()}
DEFAULT_LANGUAGES = ("E", "F", "I", "G", "S")
VERSION_FOLDER = {"Diamond": "ADA", "Pearl": "APA", "Platinum": "CPU", "HeartGold": "IPG", "SoulSilver": "IPK"}
GAME_FOLDERS = {"dp": ("ADA", "APA"), "pt": ("CPU",), "hgss": ("IPG", "IPK"), "bw": ("IRA",), "b2w2": ("IRA",)}
SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    out = SLUG.sub("_", (text or "").lower()).strip("_")
    return out[:40] or "gift"


def _line(name: str, description: str, attr1: str, attr2: str, attr3: str, size: int) -> str:
    clean = lambda value: str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")
    return "\t".join((clean(name), clean(description), clean(attr1), clean(attr2), clean(attr3), str(size)))


def export(dest: str | Path, games=GAMES, languages=DEFAULT_LANGUAGES, helper=None,
           progress=None) -> dict[str, int]:
    """Write the official DS gifts under ``dest``; -> {folder: files written}.

    ``languages`` are the DLS1 letters (E, F, I, G, S, J, K) given to a Gen 5 card that is not limited to one
    language. ``helper`` is a :class:`pokeuds.pkhex.Helper` (the shared one by default)."""
    from .. import pkhex

    helper = helper or pkhex.shared()
    games = tuple(games)
    unknown = [game for game in games if game not in GAMES]
    if unknown:
        raise ValueError(f"not a DS game: {', '.join(unknown)} (choose from {', '.join(GAMES)})")
    if isinstance(languages, str):
        languages = [part for part in languages.replace(" ", "").split(",") if part]
    letters = []
    for letter in languages:
        letter = str(letter).strip().upper()
        if letter not in LETTER_IDS:
            raise ValueError(f"not a DLS1 language letter: {letter} (E, F, I, G, S, J, K)")
        if letter not in letters:
            letters.append(letter)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    listings: dict[str, list[str]] = {}
    written: dict[str, int] = {}
    note = progress or (lambda message: log.info("%s", message))

    for game in games:
        cards = helper.gifts(game, all=True)
        note(f"{game}: {len(cards)} official cards")
        for number, card in enumerate(cards, 1):
            raw = card.get("card_data")
            if not raw or int(card.get("card_id") or 0) == 0:
                continue                      # 0000: PKHeX's simulated Pokemon Bank entries, not distributions
            try:
                _one_card(dest, game, card, base64.b64decode(raw), letters, helper, listings, written)
            except Exception as exc:          # one card PKHeX cannot make must not stop the other 500
                log.warning("%s card %s (%s): %s", game, card.get("card_id"), card.get("title", ""), exc)
            if progress is None and number % 100 == 0:
                log.info("%s: %d/%d cards", game, number, len(cards))
    for folder, lines in listings.items():
        # bytes, not text: Windows' text mode would turn each "\r\n" into "\r\r\n" and break DLS1's line lengths.
        (dest / folder / "_list.txt").write_bytes("".join(line + "\r\n" for line in lines).encode("latin-1"))
    return written


def _one_card(dest: Path, game: str, card: dict, data: bytes, letters: list[str], helper,
              listings: dict, written: dict) -> None:
    """Write one card's Wi-Fi file(s) and its listing line(s)."""
    if game in ("dp", "pt", "hgss"):
        gift = helper.gift(game, data=data)
        if not gift.wifi_data:
            return
        folders = [VERSION_FOLDER[name] for name in card.get("games", []) if name in VERSION_FOLDER]
        if not folders:
            folders = list(GAME_FOLDERS[game])
        for folder in folders:
            _write(dest, folder, f"{card['card_id']:04d}_{slug(card.get('title', ''))}.myg",
                   gift.wifi_data, 936, listings, written)
        return
    language = int(card.get("restrict_language") or 0)
    wanted = [language] if language in LANG_LETTERS else [LETTER_IDS[letter] for letter in letters]
    mask = int(card.get("restrict_version") or 0) << 20 or 0xF00000
    for language_id in wanted:
        letter = LANG_LETTERS[language_id]
        gift = helper.gift(game, data=data, trainer={"language": language_id})
        if not gift.wifi_data:
            continue
        name = f"{card['card_id']:04d}_{slug(card.get('title', ''))}_{letter}.bin"
        _write(dest, "IRA", name, gift.wifi_data, 720, listings, written,
               f"MYSTERY_{letter}", f"{mask:06X}")


def _write(dest: Path, folder: str, name: str, data: bytes, size: int, listings: dict, written: dict,
           attr1: str = "", attr2: str = "", attr3: str = "") -> None:
    if len(data) != size:
        log.warning("%s/%s is %d bytes, not %d; skipped", folder, name, len(data), size)
        return
    target = dest / folder / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    line = _line(name, "", attr1, attr2, attr3, size)
    entries = listings.setdefault(folder, [])
    if line not in entries:
        entries.append(line)
        written[folder] = written.get(folder, 0) + 1
