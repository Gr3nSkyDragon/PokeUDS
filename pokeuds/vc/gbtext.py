"""Game Boy text in the three language families the games use (docs/vc.md, Languages).

| family | games | a name field | characters |
|---|---|---|---|
| ``intl`` | English, German, French, Spanish, Italian | 11 bytes: 10 and a terminator | one byte each (pret's charmap) |
| ``jp`` | Japanese | 6 bytes: 5 and a terminator | one byte each, kana |
| ``kr`` | Korean Gold and Silver | 11 bytes: 10 and a terminator | Hangul in two bytes, Latin letters in one |

The Japanese and Korean maps, and every language's species and default trainer names, come from Lorenzooone's
PokemonGB_Online_Trades (MIT; ``gbtext_data.py``, made by scripts/make_gbtext.py).

A Pokemon moving between languages keeps its structs as they are; only its names change, as that project does it:
a Pokemon that still has its species' name gets the other language's name for it (so a trade evolution renames
it as the game expects), and any other name is carried character by character, a character the other family
cannot show becoming ``?``.
"""

from __future__ import annotations

from . import gbdata, gbtext_data as data

TERMINATOR = 0x50
QUESTION = 0xE6
NAME_LENGTH = {"intl": 11, "jp": 6, "kr": 11}
FAMILIES = {"jp": "jp", "kr": "kr"}             # every other language is international


def family_of(language: str) -> str:
    return FAMILIES.get(language or "en", "intl")


def _tables():
    intl_decode = {code: ch for ch, code in gbdata.CHARMAP.items()}
    for code, ch in enumerate(data.INTL):              # what pret's map leaves out (Á, Í, the battle letters)
        if ch and code not in intl_decode:
            intl_decode[code] = ch
    intl_encode = dict(gbdata.CHARMAP)
    for code, ch in sorted(intl_decode.items()):
        intl_encode.setdefault(ch, code)
    jp_decode = {code: ch for code, ch in enumerate(data.JP) if ch}
    jp_encode = {}
    for code, ch in sorted(jp_decode.items(), key=lambda item: (item[0] < 0x80, item[0])):
        jp_encode.setdefault(ch, code)          # a kana in both halves: the games write the one from $80 on
    kr_decode: dict[int, str | dict[int, str]] = dict(data.KR)
    kr_encode: dict[str, bytes] = {}
    for first, item in sorted(kr_decode.items()):
        if isinstance(item, str):
            kr_encode.setdefault(item, bytes([first]))
    for first, item in sorted(kr_decode.items()):
        if isinstance(item, dict):
            for second, ch in sorted(item.items()):
                kr_encode.setdefault(ch, bytes([first, second]))
    for ch, code in intl_encode.items():               # Korean games keep the Latin letters and digits
        if ch.isascii() and code not in kr_decode:
            kr_encode.setdefault(ch, bytes([code]))
            kr_decode.setdefault(code, ch)
    return ({"intl": intl_decode, "jp": jp_decode, "kr": kr_decode},
            {"intl": {k: bytes([v]) for k, v in intl_encode.items()}, "jp": {k: bytes([v]) for k, v in jp_encode.items()},
             "kr": kr_encode})


_DECODE, _ENCODE = _tables()


def decode(raw: bytes, family: str = "intl") -> str:
    """The text of a name field, up to its terminator."""
    table = _DECODE[family]
    out, i = [], 0
    while i < len(raw) and raw[i] != TERMINATOR:
        item = table.get(raw[i])
        if isinstance(item, dict):
            out.append(item.get(raw[i + 1], "?") if i + 1 < len(raw) else "?")
            i += 2
        else:
            out.append(item or "?")
            i += 1
    return "".join(out)


def encode(text: str, family: str = "intl", length: int | None = None, *, lenient: bool = False) -> bytes:
    """A name field of ``length`` bytes (the family's own by default). A character the family has no byte for, or
    a name too long, is a ValueError; ``lenient`` writes ``?`` for the first and cuts the second short."""
    length = NAME_LENGTH[family] if length is None else length
    table = _ENCODE[family]
    out = bytearray()
    for ch in text:
        code = table.get(ch)
        if code is None:
            if not lenient:
                raise ValueError(f"{ch!r} has no Game Boy character ({family})")
            code = bytes([QUESTION])
        if len(out) + len(code) > length - 1:
            if not lenient:
                raise ValueError(f"{text!r} is longer than {length - 1} bytes")
            break
        out += code
    out.append(TERMINATOR)
    return bytes(out).ljust(length, b"\x00")


def field(raw: bytes) -> bytes:
    """A name's bytes without the terminator and what follows."""
    end = raw.find(bytes([TERMINATOR]))
    return raw if end < 0 else raw[:end]


def species_name(dex: int, language: str) -> bytes:
    """The species' name as a game of ``language`` writes it (no terminator); b"" when unknown."""
    names = data.SPECIES.get(language or "en") or data.SPECIES["en"]
    return bytes.fromhex(names.get(dex, ""))


def is_species_name(raw: bytes, dex: int, language: str) -> bool:
    """Whether a nickname field holds the species' own name in ``language`` (it was never nicknamed)."""
    name = species_name(dex, language)
    family = family_of(language)
    return bool(name) and (field(raw) == name or decode(raw, family) == decode(name, family))


def convert_name(raw: bytes, source: str, target: str, *, dex: int | None = None) -> bytes:
    """A name field written by a game of ``source`` language, for a game of ``target`` language. With ``dex``, the
    field is a nickname: the species' name becomes the target's name for it."""
    src, dst = family_of(source), family_of(target)
    if dex is not None and is_species_name(raw, dex, source or "en"):
        name = species_name(dex, target or "en")
        if name:
            return (name[:NAME_LENGTH[dst] - 1] + bytes([TERMINATOR])).ljust(NAME_LENGTH[dst], b"\x00")
    if src == dst:
        return bytes(raw[:NAME_LENGTH[dst]]).ljust(NAME_LENGTH[dst], b"\x00")
    text = decode(raw, src)
    if not text and dex is None:
        text = decode(bytes.fromhex(data.TRAINER.get(target or "en", "")), dst)
    return encode(text, dst, lenient=True)


def trainer_name(text: str, language: str) -> bytes:
    """The trainer name PokeUDS shows a game of ``language``: ``text`` if the game can show it, else the name that
    game gives a trainer it does not know (Japanese games have no Latin letters)."""
    family = family_of(language)
    try:
        return encode(text, family)
    except ValueError:
        return (bytes.fromhex(data.TRAINER.get(language or "en", "")) + bytes([TERMINATOR])).ljust(NAME_LENGTH[family], b"\x00")
