"""Write pokeuds/app/data/event_languages.json: which language each of PKHeX's Mystery Gift cards was distributed in.

Project Pokemon's EventsGallery (https://github.com/projectpokemon/EventsGallery) keeps every Gen 4-7 Wonder Card in a
folder per language (``Released/Gen 6/Wondercards/ENG/...``; a few files name several, ``(ENG-FRE-GER-ITA-SPA)``).
Each file is matched to PKHeX's own copy of the card by its fingerprint (services/pkhex/Gifts.cs ``Fingerprint``: the
card's bytes less what receiving it changes), and only the languages are kept, not the files. The events are past, so
the table does not go stale; run it again only when PKHeX.Core adds cards.

    git clone --filter=blob:none --no-checkout https://github.com/projectpokemon/EventsGallery
    cd EventsGallery
    git sparse-checkout set --no-cone "/Released/Gen 4/Wondercards/" "/Released/Gen 5/Wondercards/" \\
        "/Released/Gen 5/Wondercard Fulls/" "/Released/Gen 6/Wondercards/" "/Released/Gen 7/3DS/Wondercards/"
    python scripts/event_languages.py PATH/TO/EventsGallery

(On Git for Windows' bash, put MSYS_NO_PATHCONV=1 before git sparse-checkout, or the leading / becomes a path.)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pokeuds import pkhex  # noqa: E402

OUT = ROOT / "pokeuds" / "app" / "data" / "event_languages.json"
LANGUAGES = ("JPN", "ENG", "FRE", "ITA", "GER", "SPA", "KOR", "CHS", "CHT")
# A game of each generation, for the helper to read that generation's cards with.
GAME = {4: "pt", 5: "bw", 6: "oras", 7: "usum"}
GAMES = {4: ("dp", "pt", "hgss"), 5: ("bw", "b2w2"), 6: ("xy", "oras"), 7: ("sm", "usum")}
SIZES = {".wc4": 856, ".pcd": 856, ".pgf": 204, ".wc5full": 720, ".wc6": 264, ".wc7": 264}
TAG = re.compile(r"\(([A-Z]{3}(?:-[A-Z]{3})*)\)\.[a-z0-9]+$")


def card_bytes(path: Path) -> bytes | None:
    """The one card a gallery file holds (a full file's first, when it holds several)."""
    data = path.read_bytes()
    ext = path.suffix.lower()
    if ext in (".wc6full", ".wc7full"):
        return data[:784] if data and len(data) % 784 == 0 else None
    return data if SIZES.get(ext) == len(data) else None


def languages(path: Path, gallery: Path) -> set[str]:
    parts = path.relative_to(gallery).parts
    found = {p for p in parts if p in LANGUAGES}
    if m := TAG.search(path.name):
        found |= {code for code in m.group(1).split("-") if code in LANGUAGES}
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("gallery", help="a checkout of projectpokemon/EventsGallery (the card folders are enough)")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()
    gallery = Path(args.gallery)
    released = gallery / "Released"
    if not released.is_dir():
        print(f"error: no Released folder in {gallery}")
        return 1
    helper = pkhex.Helper()
    try:
        ours: dict[str, str] = {}                     # "gen:fingerprint" of every card PKHeX lists
        for gen, games in GAMES.items():
            for game in games:
                for card in helper.gifts(game, all=True):
                    if card.get("fingerprint"):
                        ours[f"{gen}:{card['fingerprint']}"] = card["title"]
        table: dict[str, set[str]] = {}
        read = unread = 0
        for path in sorted(released.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in (*SIZES, ".wc6full", ".wc7full"):
                continue
            gen = int(re.search(r"Gen (\d)", str(path.relative_to(gallery))).group(1))
            langs = languages(path, gallery)
            data = card_bytes(path)
            if gen not in GAME or not langs or data is None:
                continue
            try:
                key = f"{gen}:{helper.fingerprint(GAME[gen], data)}"
            except ValueError:
                unread += 1
                continue
            read += 1
            if key in ours:
                table.setdefault(key, set()).update(langs)
    finally:
        helper.close()
    commit = ""
    try:
        commit = subprocess.run(["git", "-C", str(gallery), "rev-parse", "--short", "HEAD"], capture_output=True,
                                text=True, timeout=30).stdout.strip()
    except OSError:
        pass
    body = {"source": f"projectpokemon/EventsGallery {commit}".strip(),
            "cards": {k: "-".join(sorted(v, key=LANGUAGES.index)) for k, v in sorted(table.items())}}
    Path(args.out).write_text(json.dumps(body, indent=0, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"read {read} gallery cards ({unread} unreadable); {len(table)} of PKHeX's {len(ours)} cards placed; "
          f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
