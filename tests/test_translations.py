"""Built-in machine translations: which languages the app offers, how a pack lays over one, and
scripts/translate_strings.py (with a fake translator: no API call is ever made here)."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

from pokeuds import plugin_guard, plugins
from pokeuds.app import i18n

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("translate_strings", ROOT / "scripts" / "translate_strings.py")
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)

TEMPLATE = {"Bank": "Bank", "Trade": "Trade", "Built {value0} and kept it.": "Built {value0} and kept it."}


@pytest.fixture
def lang(tmp_path, monkeypatch):
    """A data/lang folder: Spanish made, German made but empty (every request of a run failed), French listed but
    not made yet; and a three-string template."""
    folder = tmp_path / "lang"
    folder.mkdir()
    (folder / "languages.toml").write_text(
        '[es]\nname = "Español"\ntag = "Traducción automática"\n\n'
        '[fr]\nname = "Français"\ntag = "Traduction automatique"\n\n'
        '[de]\nname = "Deutsch"\ntag = "Maschinelle Übersetzung"\n', encoding="utf-8")
    (folder / "es.toml").write_text('"Bank" = "Banco"\n"Trade" = "Intercambio"\n"Old text" = "Texto viejo"\n',
                                    encoding="utf-8")
    (folder / "de.toml").write_text("# made, but no strings in it\n", encoding="utf-8")
    monkeypatch.setattr(i18n, "LANG", folder)
    monkeypatch.setattr(i18n, "template", lambda: dict(TEMPLATE))
    monkeypatch.setattr(i18n, "OFFERED_SHARE", 0.5)          # Spanish has two of the three strings
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("POKEUDS_PLUGINS", raising=False)
    plugins._registry = None
    yield folder
    i18n.select("")
    plugins._registry = None
    plugin_guard.clear()


def test_only_made_languages_are_offered_with_their_tag(lang):
    offered = i18n.built_in()
    assert [b.locale for b in offered] == ["es"] and offered[0].id == "builtin:es"   # German's file has no strings
    assert offered[0].label() == "Español · Traducción automática"     # name and tag both in the language itself
    catalog = i18n.select("builtin:es")
    assert catalog.machine == "Traducción automática" and catalog.tr("Bank") == "Banco"
    assert catalog.tr("Gifts") == "Gifts"                             # a string it lacks stays English
    for gone in ("builtin:fr", "builtin:de", "builtin:xx"):            # not made, empty, not listed: English
        assert not i18n.select(gone)


def test_a_language_still_being_made_is_not_offered(lang, monkeypatch):
    monkeypatch.setattr(i18n, "OFFERED_SHARE", 0.9)          # the app's own bar: two of three is not enough
    assert i18n.built_in() == [] and not i18n.select("builtin:es")


def test_a_pack_lays_over_the_built_in_language(lang, tmp_path, monkeypatch):
    pack = tmp_path / "plugins" / "community-es-mx"
    pack.mkdir(parents=True)
    (pack / "plugin.toml").write_text('id = "community-es-mx"\nname = "Español (México)"\nversion = "1.0.0"\n'
                                      'api = "1.1"\nlocale = "es-MX"\n', encoding="utf-8")
    (pack / "strings.toml").write_text('"Bank" = "Banquito"\n', encoding="utf-8")
    monkeypatch.setenv("POKEUDS_PLUGINS", str(pack))
    plugins.reload()
    catalog = i18n.select("community-es-mx")
    assert catalog.tr("Bank") == "Banquito"                        # the pack wins
    assert catalog.tr("Trade") == "Intercambio"                    # what it leaves out: the built-in Spanish
    assert not catalog.machine
    assert i18n.built_in_for("es-MX").locale == "es" and i18n.built_in_for("pt-BR") is None


def test_batches_are_checked_and_retried():
    asked = []

    def translate(batch):
        asked.append(dict(batch))
        out = {i: f"[{text}]" for i, text in batch.items()}
        if len(batch) > 1:
            out[1] = "Hecho y guardado."           # the field is lost: refused, then asked again on its own
        if batch.get(0) == "Never right {value0}":
            out[0] = "Nunca"
        return out

    done, failed = ts.translate_missing(["Bank", "Built {value0} and kept it.", "Trade", "Never right {value0}"],
                                        translate, batch_size=3, say=lambda *a: None)
    assert done == {"Bank": "[Bank]", "Built {value0} and kept it.": "[Built {value0} and kept it.]", "Trade": "[Trade]"}
    assert failed == ["Never right {value0}"]                 # wrong twice: stays English, and the run says so
    assert asked[1] == {0: "Built {value0} and kept it."}


def test_a_run_adds_what_is_missing_keeps_hand_fixes_and_drops_the_rest(lang):
    calls = []

    def translator(locale, name):
        calls.append(locale)
        return lambda batch: {i: f"ES:{text}" for i, text in batch.items()}

    assert ts.main(["--check", "es"], translator) == 1        # Spanish lacks a string and has one the app dropped
    assert calls == []                                         # --check never translates
    assert ts.main(["es"], translator) == 0 and calls == ["es"]
    assert i18n.parse((lang / "es.toml").read_bytes()) == {
        "Bank": "Banco", "Trade": "Intercambio", "Built {value0} and kept it.": "ES:Built {value0} and kept it."}
    assert ts.main(["--check", "es", "fr"], translator) == 0  # French is not made yet: not offered, not checked
    assert ts.main(["xx"], translator) == 2


def test_no_api_credentials_stops_the_run_and_writes_nothing(lang, capsys):
    def no_credentials(locale, name):
        def translate(batch):
            raise ts.Stop("no Claude API credentials")
        return translate

    assert ts.main(["fr"], no_credentials) == 2
    assert not (lang / "fr.toml").exists() and "Stopped: no Claude API credentials" in capsys.readouterr().out
    unusable = lambda locale, name: (lambda batch: {})        # every answer unusable: nothing to add, so no file
    assert ts.main(["fr"], unusable) == 1 and not (lang / "fr.toml").exists()


def test_export_and_import_round_trip(lang, tmp_path):
    out = tmp_path / "out"
    assert ts.main(["es", "fr", "--export", str(out)]) == 0
    french = json.loads((out / "fr.json").read_text(encoding="utf-8"))
    assert list(french["translations"]) == list(TEMPLATE)
    assert "Français (fr)" in french["rules"] and "{value0}" in french["rules"]
    assert list(json.loads((out / "es.json").read_text(encoding="utf-8"))["translations"]) == ["Built {value0} and kept it."]
    french["translations"] = {"Bank": "Banque", "Trade": "", "Built {value0} and kept it.": "Créé et gardé."}
    (out / "fr.json").write_text(json.dumps(french, ensure_ascii=False), encoding="utf-8")
    assert ts.main(["fr", "--import", str(out)]) == 1         # one refused: its field is gone; one left blank
    assert i18n.parse((lang / "fr.toml").read_bytes()) == {"Bank": "Banque"}


@pytest.mark.skipif(not os.environ.get("POKEUDS_TRANSLATIONS_STRICT"),
                    reason="set POKEUDS_TRANSLATIONS_STRICT=1 (the release build does) to require up-to-date languages")
def test_every_shipped_language_covers_the_app():
    template = i18n.template()
    for language in i18n.built_in():
        strings = language.strings()
        missing = [k for k in template if k not in strings]
        assert not missing, f"{language.locale} lacks {len(missing)} strings: python scripts/translate_strings.py"


def test_settings_offers_the_language_and_tags_it(lang, tmp_path):
    pytest.importorskip("flet")
    import dataclasses

    from gui.app import App
    from gui.views.settings import SettingsView
    from pokeuds.app.settings import Settings

    def texts(o, done=None):
        done = set() if done is None else done
        if id(o) in done or not dataclasses.is_dataclass(o) or isinstance(o, type):
            return []
        done.add(id(o))
        out = []
        for f in dataclasses.fields(o):
            v = getattr(o, f.name, None)
            if isinstance(v, str) and f.name in ("text", "value", "content"):
                out.append(v)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    out += texts(x, done)
            elif f.name not in ("parent", "page", "data") and dataclasses.is_dataclass(v):
                out += texts(v, done)
        return out

    settings = Settings(output=str(tmp_path / "received"))
    english = texts(SettingsView(App(None, settings)).control())
    assert "Español · Traducción automática" in english and not any("Machine" in t for t in english)
    settings.language = "builtin:es"
    i18n.select(settings.language)
    spanish = texts(SettingsView(App(None, settings)).control())
    # The chosen entry carries the tag; it is shown once, in Spanish, and nothing else says it.
    assert [t for t in spanish if "Traducción automática" in t] == ["Español · Traducción automática"]
