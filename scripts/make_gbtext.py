"""Write pokeuds/vc/gbtext_data.py, the Game Boy games' text in every language, from the data files of Lorenzooone's
PokemonGB_Online_Trades (MIT): its character maps (international, Japanese, Korean) and the default species and
trainer names of each language.

    git clone https://github.com/Lorenzooone/PokemonGB_Online_Trades
    git -C PokemonGB_Online_Trades checkout f2030e7
    python scripts/make_gbtext.py PokemonGB_Online_Trades

Only that project's ``useful_data/text`` files are read, as data; the readers below follow its
``utilities/text_conversions.py`` (``get_table_pointer_PG3TGX``, ``parse_alphabet``, ``extract_name_from_gen3_data``,
the species conversions), so what PokeUDS ships is what that project trades with.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "pokeuds" / "vc" / "gbtext_data.py"

# Lorenzooone's language numbers (PokemonNamesForGen) -> PokeUDS's (pokeuds/games/vc.py).
LANGUAGES = {"jp": 1, "en": 2, "fr": 3, "it": 4, "de": 5, "kr": 6, "es": 7}
NAME_LANGUAGES = 4          # pokemon_names.bin holds four name sets; language_names_index.bin picks one per language
GEN12_EOL, GEN3_EOL = 0x50, 0xFF
QUESTION = 0xE6             # the Game Boy games' "?"
MR_MIME, UNOWN = 122, 201
MR_MIME_OLD_NAME, UNOWN_NAME = 446, 445     # where the names of the two sit in the Gen 3 name table


def table_pointer(table: bytes, entry: int) -> int | None:
    """``get_table_pointer_PG3TGX``: the offset of an entry in a packed table."""
    control = table[0]
    initial_size = (control & 1) + 1
    offset_size = ((control >> 1) & 1) + 1
    shift = (control >> 2) & 7
    initial_shift = control >> 5
    initial, at = table[1] << initial_shift, 2
    if initial_size == 2:
        initial, at = struct.unpack_from("<H", table, 2)[0] << initial_shift, 4
    entries = (initial - at) >> (offset_size - 1)
    if entry >= entries:
        return None
    offset = table[at + entry] << shift if offset_size == 1 else struct.unpack_from("<H", table, at + entry * 2)[0] << shift
    return initial + offset


def alphabet(path: Path) -> list:
    """``parse_alphabet``: a 16 x 16 table of characters, a cell ``<TABLE>`` naming a second table (``# TABLE n``)."""
    lines = path.read_text(encoding="utf-8").splitlines()

    def table(target: int | None) -> tuple[list, list[int]]:
        started, start = target is None, None
        for i, line in enumerate(lines):
            if not started:
                started = line.startswith(f"# TABLE {target}")
            elif not line.startswith("#") and line.strip():
                start = i
                break
        if start is None:
            return [""] * 256, []
        cells, nested = [], []
        for row in range(16):
            parts = lines[start + row].split("#")
            for col in range(16):
                if target is None and parts[col] == "<TABLE>":
                    cells.append([])
                    nested.append(row * 16 + col)
                else:
                    cells.append(parts[col])
        return cells, nested

    cells, nested = table(None)
    for index in nested:
        cells[index] = table(index)[0]
    return cells


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="a checkout of Lorenzooone/PokemonGB_Online_Trades")
    a = parser.parse_args(argv)
    text = a.source / "useful_data" / "text"
    read = lambda name: (text / name).read_bytes()
    licence = (a.source / "LICENSE").read_text(encoding="utf-8").strip()

    gen12_gen3 = {"intl": read("text_gen12_to_gen3_int.bin"), "jp": read("text_gen12_to_gen3_jp.bin")}
    gen3_chars = {"intl": alphabet(text / "text_gen3_to_general_int.txt"), "jp": alphabet(text / "text_gen3_to_general_jp.txt")}
    gen3_gen12 = {"intl": read("text_gen3_to_gen12_int.bin"), "jp": read("text_gen3_to_gen12_jp.bin")}

    def decode_table(family: str) -> list[str]:
        out = []
        for value in range(256):
            ch = gen3_chars[family][gen12_gen3[family][value]]
            if value == QUESTION:
                out.append("?")
                continue
            keep = value == QUESTION or ch not in ("?", "<?>", "") and not ch.startswith("<") and value != GEN12_EOL
            out.append(ch if keep and isinstance(ch, str) else "")
        return out

    korean = {}
    for first, item in enumerate(alphabet(text / "text_gen2_to_general_kor.txt")):
        if isinstance(item, list):
            second = {s: ch for s, ch in enumerate(item) if ch not in ("?", "<?>", "") and not ch.startswith("<")}
            if second:
                korean[first] = second
        elif item not in ("?", "<?>", "") and not item.startswith("<"):
            korean[first] = item

    names = read("pokemon_names.bin")
    index = read("language_names_index.bin")

    def gen3_name(offset: int, family: str) -> bytes:
        """``extract_name_from_gen3_data``: a Gen 3 name as the Game Boy games write it."""
        out = bytearray()
        for k in range(6 if family == "jp" else 11):
            value = names[offset + k]
            if value == GEN3_EOL:
                break
            out.append(gen3_gen12[family][value])
        return bytes(out)

    species = {}
    kr_names = read("pokemon_names_kr_gen2.bin")
    for language, number in LANGUAGES.items():
        family = "jp" if language == "jp" else "intl"
        found = {}
        for dex in range(1, 252):
            if language == "kr":
                raw = kr_names[10 * dex:10 * dex + 10]
                found[dex] = (raw.split(bytes([GEN12_EOL]))[0]).hex()
                continue
            name_index = MR_MIME_OLD_NAME if dex == MR_MIME else UNOWN_NAME if dex == UNOWN else dex
            offset = table_pointer(names, name_index * NAME_LANGUAGES + index[number])
            if offset is not None:
                found[dex] = gen3_name(offset, family).hex()
        species[language] = found

    trainers_table = read("trainer_names.bin")
    trainers = {}
    for language, number in LANGUAGES.items():
        if language == "kr":
            trainers[language] = read("trainer_name_kr_gen2.bin").split(bytes([GEN12_EOL]))[0].hex()
            continue
        offset = table_pointer(trainers_table, number)
        family = "jp" if language == "jp" else "intl"
        out = bytearray()
        for k in range(6 if family == "jp" else 11):
            value = trainers_table[offset + k]
            if value == GEN3_EOL:
                break
            out.append(gen3_gen12[family][value])
        trainers[language] = bytes(out).hex()

    lines = [
        '"""Game Boy text in every language: generated by scripts/make_gbtext.py from the data of Lorenzooone\'s',
        "PokemonGB_Online_Trades (useful_data/text), which is under the MIT licence below. Do not edit by hand.",
        "",
        *licence.splitlines(),
        '"""',
        "",
        "# The character each game byte shows, international and Japanese ('' for none).",
        f"INTL = {decode_table('intl')!r}",
        f"JP = {decode_table('jp')!r}",
        "# Korean Gold and Silver: a byte is a character, or the first of two (a dict of second bytes).",
        f"KR = {korean!r}",
        "# Each language's species names in game bytes, without the terminator: {language: {dex: hex}}. Gen 1 and Gen 2",
        "# name a species alike; Italian and Spanish keep the English names.",
        f"SPECIES = {species!r}",
        "# The name a game gives a trainer it knows nothing of, in game bytes.",
        f"TRAINER = {trainers!r}",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
