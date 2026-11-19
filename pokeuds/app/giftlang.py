"""Which language a Mystery Gift card was distributed in, for the Gifts page's language filter.

In order, the first that knows:

1. **The event archive.** pokeuds/app/data/event_languages.json (scripts/event_languages.py): Project Pokemon's
   EventsGallery files every card in a folder per language, and its files are matched to PKHeX's cards by fingerprint.
   It places 3,035 of the 3,043 cards PKHeX lists (2026-11-19); the rest are PKHeX's own "Simulated Pokemon Bank"
   stand-ins. Every real event is filed under one language; a card filed under all of them is a placeholder.
2. **The card.** Services/pkhex/Gifts.cs ``StatedLanguage``: every Gen 5 card, a Gen 4 card through its Pokemon.
3. **The title.** Japanese, Korean and Chinese by their writing, the European languages by their words (against the
   956 cards whose language is known: 910 right, 29 not at all).

A card none of them places is listed only when no language is chosen; one filed under every language (PKHeX's
placeholders) is under each.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

# LanguageID, as the games and PKHeX number them.
NAMES = {1: "Japanese", 2: "English", 3: "French", 4: "Italian", 5: "German", 7: "Spanish", 8: "Korean",
         9: "Chinese (simplified)", 10: "Chinese (traditional)"}
_CODES = {"JPN": 1, "ENG": 2, "FRE": 3, "ITA": 4, "GER": 5, "SPA": 7, "KOR": 8, "CHN": 9, "CHS": 9, "CHT": 10}
# The languages each generation's events went out in: a card filed under all of them is for everyone.
EVERY = {4: frozenset({1, 2, 3, 4, 5, 7, 8}), 5: frozenset({1, 2, 3, 4, 5, 7, 8}), 6: frozenset({1, 2, 3, 4, 5, 7, 8}),
         7: frozenset({1, 2, 3, 4, 5, 7, 8, 9, 10})}
TABLE = Path(__file__).with_name("data") / "event_languages.json"

_WORDS = {
    "ENG": """the get your you for from of and with an is has arrived event gift special present here come meet trainer
        mystery receive celebrate it's s wow shiny powerful mysterious battle victory soar trusted colored virtuoso agile
        ascendant brilliant celestial dazzling deluging diving dynamic fiery got gasp first championships cap returns
        two stones new world championship tournament version download bank this dummy card demo edition festival
        challenge legendary mythical rare its egg celebration anniversary helping hand peculiar super tough boo getting
        spring hidden ability distribution unlock secret key member letter course forest path""".split(),
    "FRE": """le les du des une voici votre vos pour avec est arrivé arrive cadeau dresseur rencontrez faites au aux
        offert voilà waouh couleur différente exclusif spécial spéciale fabuleux rarissime virtuose combats combattez
        envolez vers victoire doré banque démo tornade foudroyant un d l légendaire œuf obtenez recevez lettre carte
        membre clé secrète chemin forêt jaune""".split(),
    "GER": """der die das den dem ein eine einen für dich dir mit von zum zur ist und hol wünsch geschätzter sieg nur
        hier kommt geheimgeschehen geheimgeschenk geschenk erhalte samt kappe spezial klassiker kennen lerne ashs des
        zwei glutgeschenk schockgeschenk downloadversion demoversion trio legendäre seltene andersfarbiges brief
        eingetroffen mitgliedskarte wartet öffner wald siegerstraße goldgelbe bringt aufwind gruseliges""".split(),
    "ITA": """il lo gli di del della per con è arrivato esperto allenatore dono ecco ti preparati esprimi arriva avanza
        verso vittoria vola speciale uno ascesa passo sicuro alla carica illusionista desiderio leggendario raro
        lettera scheda soci chiave segreta bosco giallo campioni via hai ricevuto cromatico""".split(),
    "SPA": """el los las del tu tus para ya está aquí conoce estimado entrenador consigue al y sorpresa electrizante
        llamativo torneo banco este es mi poder fragmento especial deseo singular trío legendario pídele diferente
        carta profesor carné socio llave secreta clásicos""".split(),
}
# Words more than one language uses: each counts a little for each of them.
_SHARED = {"regalo": ("ITA", "SPA"), "la": ("FRE", "ITA", "SPA"), "de": ("FRE", "SPA"), "in": ("ENG", "GER"),
           "a": ("ENG", "ITA", "SPA"), "color": ("ITA", "SPA"), "misterioso": ("ITA", "SPA")}
_CHARS = {"FRE": "çêëâîïôûœ", "GER": "äöüß", "ITA": "ìò", "SPA": "ñ¡¿íóú"}
_WORD = re.compile(r"[a-zà-ÿœß']+")


def from_title(title: str) -> int:
    """The language a card's title is written in (LanguageID), or 0 when it cannot be told."""
    t = (title or "").strip()
    if any(0xAC00 <= ord(c) <= 0xD7AF or 0x3130 <= ord(c) <= 0x318F for c in t):
        return 8
    if any(0x3040 <= ord(c) <= 0x30FF for c in t):
        return 1
    if any(0x4E00 <= ord(c) <= 0x9FFF for c in t):
        return 9
    low = t.lower()
    score: Counter[str] = Counter()
    for word in _WORD.findall(low):
        word = word.strip("'")
        for lang, words in _WORDS.items():
            if word in words:
                score[lang] += 2
        for lang in _SHARED.get(word, ()):
            score[lang] += 1
    for lang, chars in _CHARS.items():
        score[lang] += 2 * sum(low.count(c) for c in chars)
    if low.startswith(("¡", "¿")):
        score["SPA"] += 4
    if re.search(r"\s[!?:]", t):                 # French puts a space before ! ? :
        score["FRE"] += 3
    if "in regalo" in low:
        score["ITA"] += 3
    if not score:
        return 0
    best, n = score.most_common(1)[0]
    return _CODES[best] if list(score.values()).count(n) == 1 else 0


@lru_cache(maxsize=1)
def _table() -> dict[str, str]:
    try:
        return json.loads(TABLE.read_text(encoding="utf-8")).get("cards", {})
    except (OSError, ValueError):
        return {}


def of_card(card: dict, generation: int) -> frozenset[int]:
    """The languages a listed card (``Helper.gifts``' entries) went out in; empty when nothing tells."""
    filed = _table().get(f"{generation}:{card.get('fingerprint', '')}")
    if filed:
        return frozenset(_CODES[code] for code in filed.split("-") if code in _CODES)
    stated = int(card.get("card_language") or 0)
    if stated:
        return frozenset({stated})
    read = from_title(card.get("title", ""))
    return frozenset({read}) if read else frozenset()


def every(languages: frozenset[int], generation: int) -> bool:
    """Filed under every language the generation's events went out in (PKHeX's placeholders)."""
    return bool(languages) and languages >= EVERY.get(generation, frozenset())


def describe(languages: frozenset[int], generation: int) -> str:
    if every(languages, generation):
        return "Every language"
    return ", ".join(NAMES[n] for n in sorted(languages, key=lambda n: NAMES.get(n, "")) if n in NAMES)


def matches(languages: frozenset[int], generation: int, chosen: set[int]) -> bool:
    """Whether a card belongs under the chosen languages; none chosen: every card. A card whose language cannot be
    told is under none of them."""
    if not chosen:
        return True
    return bool(languages & chosen)
