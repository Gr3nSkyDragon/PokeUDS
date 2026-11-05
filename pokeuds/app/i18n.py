"""The app's language: English, or a language pack (a data-only plugin, docs/plugins.md, Language packs).

The app's pages are written in English. A pack's ``strings.toml`` maps English text to its translation:

    "Choose a Pokemon to offer: from the bank, Build one, or a file" = "Elige un Pokemon para ofrecer: ..."
    "Built {value0} and kept it in the bank." = "Se creo {value0} y se guardo en el banco."

A key with ``{name}`` fields is a template: it matches text whose literal parts are the same, the fields
standing for anything, and the translation keeps the same fields (they may move). The template of every string
the app shows is ``pokeuds/app/data/strings.en.toml`` (scripts/extract_strings.py). Text a pack does not cover
stays English; so do logs, file names and what the command line prints.
"""

from __future__ import annotations

import re
import string
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

TEMPLATE = Path(__file__).with_name("data") / "strings.en.toml"
MAX_STRINGS_BYTES = 4 << 20
FIELD = re.compile(r"\{(\w+)\}")


def fields(text: str) -> list[str]:
    """The named fields of a template; ValueError on bad format syntax."""
    names = []
    for literal, name, spec, conversion in string.Formatter().parse(text):
        if name is not None:
            if not re.fullmatch(r"\w+", name) or spec or conversion:
                raise ValueError(f"{text!r}: a field is a plain {{name}}")
            names.append(name)
    return names


@dataclass
class Catalog:
    locale: str = ""
    name: str = "English"
    exact: dict = field(default_factory=dict)
    templates: list = field(default_factory=list)      # (regex, translation), longest literal text first
    font_family: str = ""

    def __bool__(self) -> bool:
        return bool(self.exact or self.templates)

    def tr(self, text: str) -> str:
        if not text or not self:
            return text
        if (hit := self.exact.get(text)) is not None:
            return hit
        if "{" in text or len(text) > 4096:
            return text
        for pattern, translation in self.templates:
            if (m := pattern.fullmatch(text)) is not None:
                return translation.format(**{k: self.tr(v) for k, v in m.groupdict().items()})
        return text


def parse(raw: bytes, where: str = "strings.toml") -> dict[str, str]:
    """A pack's strings, checked: text values only, and each translation with exactly its key's fields."""
    if len(raw) > MAX_STRINGS_BYTES:
        raise ValueError(f"{where} is larger than {MAX_STRINGS_BYTES >> 20} MB")
    try:
        table = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{where}: {exc}") from None
    out = {}
    for key, value in table.items():
        if not isinstance(value, str):
            raise ValueError(f"{where}: {key!r} is not text")
        try:
            want, got = sorted(fields(key)), sorted(fields(value))
        except ValueError as exc:
            raise ValueError(f"{where}: {exc}") from None
        if want != got:
            raise ValueError(f"{where}: {key!r} has the fields {want or 'none'}, its translation {got or 'none'}")
        out[key] = value
    return out


def build(strings: dict[str, str], locale: str = "", name: str = "", font_family: str = "") -> Catalog:
    exact, templates = {}, []
    for key, value in strings.items():
        if not fields(key):
            exact[key] = value
            continue
        literal = FIELD.sub("", key)
        if sum(c.isalpha() for c in literal) < 3:
            continue                                   # all fields: it would match anything
        parts, last = [], 0
        for m in FIELD.finditer(key):
            parts.append(re.escape(key[last:m.start()]))
            group = m.group(1)
            parts.append(f"(?P<{group}>.+?)" if f"(?P<{group}>" not in "".join(parts) else f"(?P={group})")
            last = m.end()
        parts.append(re.escape(key[last:]))
        templates.append((len(literal), re.compile("".join(parts), re.DOTALL), value))
    templates.sort(key=lambda t: -t[0])
    return Catalog(locale, name or locale, exact, [(p, v) for _, p, v in templates], font_family)


# -- the packs and the choice -----------------------------------------------------------------------

@dataclass
class Pack:
    id: str
    name: str
    locale: str
    folder: Path
    strings: str = "strings.toml"
    font_family: str = ""


def packs() -> list[Pack]:
    """Language packs that are on and accepted (POKEUDS_PLUGINS folders included)."""
    from .. import plugins

    out = []
    for p in plugins.discover():
        if not (p.enabled and p.consented) or p.error:
            continue
        try:
            manifest = plugins.read_manifest((p.folder / "plugin.toml").read_bytes())
        except (OSError, ValueError):
            continue
        if manifest.get("locale"):
            out.append(Pack(p.id, p.name, str(manifest["locale"]), p.folder, str(manifest.get("strings", "strings.toml")),
                            str(manifest.get("font_family", ""))[:100]))
    return out


_current = Catalog()


def current() -> Catalog:
    return _current


def select(pack_id: str) -> Catalog:
    """Use a pack by its plugin id ("" for English); a pack that is gone or broken falls back to English."""
    global _current
    _current = Catalog()
    if pack_id:
        for pack in packs():
            if pack.id == pack_id:
                try:
                    strings = parse((pack.folder / pack.strings).read_bytes(), f"{pack.id}: {pack.strings}")
                except (OSError, ValueError):
                    break
                _current = build(strings, pack.locale, pack.name, pack.font_family)
                break
    return _current


def tr(text: str) -> str:
    return _current.tr(text)


def template() -> dict[str, str]:
    """Every string the app shows, English to English (the pack template)."""
    try:
        return parse(TEMPLATE.read_bytes(), str(TEMPLATE))
    except OSError:
        return {}
