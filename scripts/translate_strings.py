"""Machine-translate the app's strings into its built-in languages (pokeuds/app/i18n.py, docs/release.md, Translations).

    python scripts/translate_strings.py              # every language in languages.toml: what is new, and drop what is gone
    python scripts/translate_strings.py ja fr        # some of them
    python scripts/translate_strings.py --check      # exit 1 when a language the app ships is behind (no API call)
    python scripts/translate_strings.py --dry-run    # what a run would translate
    python scripts/translate_strings.py --export DIR # write what each language lacks to DIR/<locale>.json, to translate
    python scripts/translate_strings.py --import DIR # take those files back, filled in, checked like any translation

The source is ``pokeuds/app/data/strings.en.toml`` (``scripts/extract_strings.py``); each language is
``pokeuds/app/data/lang/<locale>.toml``, named in ``languages.toml`` there. Only strings a language does not have yet
are sent, so a run after a release's changes costs a few dozen strings, and a translation already in the file is
never replaced: fix one by hand and it stays fixed. Strings the app no longer shows are dropped.

Translations come from the Claude API (``pip install -r requirements-translate.txt``; API credentials from
``ANTHROPIC_API_KEY`` or ``ant auth login``: a Claude Console account, billed apart from a Claude subscription), with
the official Pokemon terms in ``scripts/translation_glossary.toml``. Without API credentials, ``--export`` writes what
each language lacks, with the rules and the glossary, for a Claude Code session to translate; ``--import`` takes it
back. Either way each translation is checked as the app checks a language pack (the same ``{value0}`` fields); one
that fails is left out and stays English, and the run says which.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pokeuds.app import i18n  # noqa: E402

GLOSSARY = Path(__file__).with_name("translation_glossary.toml")
MODEL = "claude-opus-5-5"
BATCH = 60

SYSTEM = """You translate the user interface of PokeUDS, a desktop app that trades Pokemon with a Nintendo 3DS or DS \
over local wireless, keeps a bank of Pokemon, and sends Mystery Gift cards. Players read it; many are not technical.

You get a JSON list of English UI strings, each with an id. Return every one translated into {language}, same ids.

Rules:
- Keep each {{value0}}-style field exactly as written (same names, same braces); move it where the grammar needs it.
- Keep untranslated: PokeUDS, PKHeX, Azahar, mGBA, GitHub, ESP32 and chip names (S2, S3, C3, C6), 3DS, DS, GTS, USB, \
COM, file names and extensions (.wc6, .pk7...), commands, and game titles as they are written in English.
- Use the official term for every Pokemon word in the glossary below; for other Pokemon words, the term the games use \
in {language}.
- Match the English's length and tone: short, plain, direct. Buttons stay short. Keep the punctuation style (a \
trailing colon, "...", "·").
- A string that is only a name, a number or a symbol stays as it is.

Glossary (English: {language}):
{glossary}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}


def glossary(locale: str) -> str:
    try:
        table = tomllib.loads(GLOSSARY.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return "(none)"
    lines = [f"- {english}: {terms[locale]}" for english, terms in table.items()
             if isinstance(terms, dict) and isinstance(terms.get(locale), str)]
    return "\n".join(lines) or "(none)"


class Stop(Exception):
    """A failure no retry fixes (no API credentials, a refused key): the run stops and writes nothing more."""


def claude_translator(locale: str, language: str):
    """A function that translates a batch ({id: english} -> {id: text}) with Claude."""
    import anthropic

    client = anthropic.Anthropic()
    system = SYSTEM.format(language=f"{language} ({locale})", glossary=glossary(locale))

    def translate(batch: dict[int, str]) -> dict[int, str]:
        try:
            return ask(batch)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise Stop(f"the Claude API refused the credentials: {exc}") from None
        except TypeError as exc:                  # the SDK found no credentials at all
            if "authentication" not in str(exc):
                raise
            raise Stop("no Claude API credentials (ANTHROPIC_API_KEY, or ant auth login). A Claude subscription, "
                       "Claude Code's included, is not API access; without it, use --export and --import") from None

    def ask(batch: dict[int, str]) -> dict[int, str]:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=system,
            messages=[{"role": "user", "content": json.dumps(
                [{"id": i, "en": text} for i, text in batch.items()], ensure_ascii=False)}],
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"the model stopped ({response.stop_reason})")
        text = next(b.text for b in response.content if b.type == "text")
        return {int(t["id"]): t["text"] for t in json.loads(text)["translations"]}

    return translate


def fits(english: str, translation: str) -> bool:
    """Whether a translation is one the app would accept for this string: text, with exactly its fields."""
    if not isinstance(translation, str) or not translation.strip():
        return False
    try:
        return sorted(i18n.fields(english)) == sorted(i18n.fields(translation))
    except ValueError:
        return False


def translate_missing(missing: list[str], translate, batch_size: int = BATCH, say=print) -> tuple[dict, list]:
    """Translate ``missing`` in batches; a batch's strings that come back wrong are tried once more on their own.
    -> (translations, the strings left English)."""
    done, failed = {}, []
    for start in range(0, len(missing), batch_size):
        chunk = missing[start:start + batch_size]
        try:
            answer = translate(dict(enumerate(chunk)))
        except Stop:
            raise
        except Exception as exc:          # the API, or an answer that is not the JSON asked for
            say(f"  a batch failed ({exc}); trying its strings one at a time")
            answer = {}
        retry = []
        for i, english in enumerate(chunk):
            if fits(english, answer.get(i)):
                done[english] = answer[i]
            else:
                retry.append(english)
        for english in retry:
            try:
                text = translate({0: english}).get(0)
            except Stop:
                raise
            except Exception:
                text = None
            if fits(english, text):
                done[english] = text
            else:
                failed.append(english)
        say(f"  {min(start + batch_size, len(missing))} of {len(missing)}")
    return done, failed


def write(path: Path, strings: dict[str, str], locale: str, name: str) -> None:
    header = (f"# PokeUDS in {name} ({locale}): machine-translated from strings.en.toml by scripts/translate_strings.py.\n"
              "# A translation fixed by hand here stays fixed: the script only adds what is missing.\n")
    lines = [header] + [f"{json.dumps(k, ensure_ascii=False)} = {json.dumps(v, ensure_ascii=False)}"
                        for k, v in sorted(strings.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def read(path: Path) -> dict[str, str]:
    try:
        return i18n.parse(path.read_bytes(), str(path))
    except OSError:
        return {}


def export(folder: Path, locale: str, name: str, missing: list[str]) -> None:
    """What a language lacks, to be translated elsewhere: each English string with an empty translation."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{locale}.json").write_text(json.dumps({
        "language": f"{name} ({locale})",
        "rules": SYSTEM.format(language=f"{name} ({locale})", glossary=glossary(locale)),
        "translations": {english: "" for english in missing},
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def imported(folder: Path, locale: str) -> dict[str, str]:
    """A filled-in export: English to translation (empty ones dropped); {} when there is no file."""
    path = folder / f"{locale}.json"
    if not path.is_file():
        return {}
    table = json.loads(path.read_text(encoding="utf-8")).get("translations", {})
    return {k: v for k, v in table.items() if isinstance(k, str) and isinstance(v, str) and v.strip()}


def main(argv=None, translator=claude_translator) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("locales", nargs="*", help="languages to update (default: every one in languages.toml)")
    parser.add_argument("--check", action="store_true", help="exit 1 when a language lacks strings; translate nothing")
    parser.add_argument("--dry-run", action="store_true", help="say what would be translated; change nothing")
    parser.add_argument("--export", type=Path, metavar="DIR", help="write what each language lacks to DIR/<locale>.json")
    parser.add_argument("--import", dest="import_", type=Path, metavar="DIR", help="take DIR/<locale>.json back, checked")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):      # language names (日本語) on a Windows console's code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    template = i18n.template()
    if not template:
        print(f"{i18n.TEMPLATE} is missing: run scripts/extract_strings.py first")
        return 2
    languages = i18n.languages()
    unknown = [loc for loc in args.locales if loc not in languages]
    if unknown:
        print(f"not in {i18n.LANG / 'languages.toml'}: {', '.join(unknown)}")
        return 2
    stale = left_english = 0
    for locale in args.locales or list(languages):
        name = str(languages[locale].get("name", locale))
        path = i18n.LANG / f"{locale}.toml"
        have = read(path)
        missing = [k for k in template if k not in have]
        obsolete = [k for k in have if k not in template]
        if args.check and not path.is_file():
            print(f"{name} ({locale}): not made yet (the app does not offer it)")
            continue
        print(f"{name} ({locale}): {len(template) - len(missing)} of {len(template)} strings, {len(missing)} to "
              f"translate, {len(obsolete)} to drop")
        if args.check:
            stale += bool(missing or obsolete)
            continue
        if args.dry_run or not (missing or obsolete):
            continue
        if args.export:
            if missing:
                export(args.export, locale, name, missing)
                print(f"  wrote {args.export / (locale + '.json')}")
            continue
        if args.import_:
            given = imported(args.import_, locale)
            done = {k: v for k, v in given.items() if k in missing and fits(k, v)}
            failed = [k for k in given if k in missing and k not in done]
        else:
            try:
                done, failed = translate_missing(missing, translator(locale, name)) if missing else ({}, [])
            except Stop as exc:
                print(f"Stopped: {exc}")
                return 2
        left_english += len(failed)
        if done or obsolete:
            kept = {k: v for k, v in have.items() if k in template}
            write(path, {**kept, **done}, locale, name)
            print(f"  added {len(done)}, dropped {len(obsolete)}")
        else:
            print("  nothing to add: the file is left as it was")
        for english in failed[:20]:
            print(f"  left English (no usable translation): {english}")
        if len(failed) > 20:
            print(f"  ... and {len(failed) - 20} more")
    if args.check and stale:
        print("Run python scripts/translate_strings.py to bring them up to date.")
        return 1
    return 1 if left_english else 0


if __name__ == "__main__":
    raise SystemExit(main())
