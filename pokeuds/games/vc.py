"""The 3DS Virtual Console Game Boy Pokemon games: every title, in every language (docs/vc.md).

Each title hosts or joins from its own link menu (the VC's host/join screen), on UDS with the
passphrase ``TRL_NETWORK`` + NUL and a comm id that is the title id's low 32 bits + 0x10. That rule is
measured for English Red, Gold and Silver; every other title's comm id follows it and is unmeasured.

Titles are matched by an explicit table, never by a mask over the comm id: the title ids of one
generation do not share a prefix (Japanese Gen 1 is ``0017 0Cxx``, Korean Gold ``0017 35xx``), and two
titles trade only when their **language family** agrees too. The family is how a game lays out names
in the blocks it exchanges:

| family | titles | names |
|---|---|---|
| ``intl`` | English, German, French, Spanish, Italian | 11 bytes (10 characters and a terminator) |
| ``jp`` | Japanese | 6 bytes (5 characters and a terminator), a kana character map; shorter blocks and mail |
| ``kr`` | Korean (Gold and Silver only) | 11 bytes holding 2-byte Hangul; longer mail |

Joining a Japanese game with international blocks corrupts the trade, so PokeUDS speaks each family's own
blocks (pokeuds.vc.link, Languages) and joins or hosts any title in the table. Only English Red, Gold and Silver
have been seen on the air; every other title is derived, and the Japanese and Korean blocks come from the
cartridges (Lorenzooone's PokemonGB_Online_Trades), unmeasured on the Virtual Console.
"""

from __future__ import annotations

from dataclasses import dataclass

PASSPHRASE = b"TRL_NETWORK\x00"
CHANNEL = 6                    # the retail host in every capture

LANGUAGES = {"jp": "Japanese", "en": "English", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian",
             "kr": "Korean"}
FAMILIES = {"jp": "jp", "kr": "kr"}             # every other language is international
FAMILY_NAMES = {"intl": "international", "jp": "Japanese", "kr": "Korean"}
SUPPORTED = ("intl", "jp", "kr")       # the families whose blocks PokeUDS builds (docs/vc.md, Languages)
# PKHeX's LanguageID of each language, which Poke Transporter keeps into Gen 7 (services/pkhex/Migrate.cs).
PKHEX_LANGUAGE = {"jp": 1, "en": 2, "fr": 3, "it": 4, "de": 5, "es": 7, "kr": 8}


def family_of(language: str) -> str:
    return FAMILIES.get(language, "intl")


@dataclass(frozen=True)
class VcTitle:
    key: str                   # "red", "gold-jp", "silver-kr": English titles keep the bare game name
    name: str
    generation: int
    title_id: int
    language: str              # a key of LANGUAGES
    measured: bool             # comm id seen on the air, not derived

    @property
    def comm_id(self) -> int:
        return (self.title_id & 0xFFFFFFFF) + 0x10

    @property
    def family(self) -> str:
        return family_of(self.language)

    @property
    def supported(self) -> bool:
        return self.family in SUPPORTED


# The middle of each title id, 0004000000xxxx00 (Bl4ckSh4rk's 3DS title list). Green is Japan's alone; Korea got
# Gold and Silver only (22 September 2017). Japan's Gen 1 titles also went out in Hong Kong and Taiwan, in Japanese.
_GAMES = (
    ("red", "Red", 1, {"jp": 0x170C, "en": 0x1710, "de": 0x1713, "fr": 0x1716, "es": 0x1719, "it": 0x171C}),
    ("green", "Green", 1, {"jp": 0x170D}),
    ("blue", "Blue", 1, {"jp": 0x170E, "en": 0x1711, "de": 0x1714, "fr": 0x1717, "es": 0x171A, "it": 0x171D}),
    ("yellow", "Yellow", 1, {"jp": 0x170F, "en": 0x1712, "de": 0x1715, "fr": 0x1718, "es": 0x171B, "it": 0x171E}),
    ("gold", "Gold", 2, {"jp": 0x1723, "en": 0x1726, "de": 0x1729, "fr": 0x172C, "es": 0x172F, "it": 0x1732,
                         "kr": 0x1735}),
    ("silver", "Silver", 2, {"jp": 0x1724, "en": 0x1727, "de": 0x172A, "fr": 0x172D, "es": 0x1730, "it": 0x1733,
                             "kr": 0x1736}),
    ("crystal", "Crystal", 2, {"jp": 0x1725, "en": 0x1728, "de": 0x172B, "fr": 0x172E, "es": 0x1731, "it": 0x1734}),
)
_MEASURED = {"red", "gold", "silver"}           # English only


def title_key(game: str, language: str) -> str:
    return game if language == "en" else f"{game}-{language}"


def _titles():
    for game, label, generation, ids in _GAMES:
        for language, middle in ids.items():
            key = title_key(game, language)
            name = f"Pokemon {label} (VC)" if language == "en" else f"Pokemon {label} (VC, {LANGUAGES[language]})"
            yield VcTitle(key, name, generation, 0x0004000000000000 | (middle << 8), language, key in _MEASURED)


TITLES = {t.key: t for t in _titles()}
_BY_COMM_ID = {t.comm_id: t for t in TITLES.values()}
HOSTABLE = tuple(k for k, t in TITLES.items() if t.supported)       # what `--as` takes


def title_of(comm_id: int) -> VcTitle | None:
    return _BY_COMM_ID.get(comm_id)


def generation_of(comm_id: int) -> int | None:
    title = title_of(comm_id)
    return title.generation if title else None


def titles_of(generation: int, family: str = "intl") -> list[VcTitle]:
    return [t for t in TITLES.values() if t.generation == generation and t.family == family]


def why_not(title: VcTitle) -> str:
    """Why PokeUDS does not trade with ``title``, in a sentence (for a family outside ``SUPPORTED``)."""
    detail = {"jp": "Japanese games hold names of 5 characters in a kana character map, so their trade blocks are "
                    "shorter than the international ones",
              "kr": "Korean games store names as 2-byte Hangul and hold longer mail"}[title.family]
    return (f"{title.name} is heard, but PokeUDS does not trade with {FAMILY_NAMES[title.family]} Virtual Console "
            f"games yet: {detail}. Joining with the wrong layout would corrupt the trade.")
