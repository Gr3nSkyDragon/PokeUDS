"""Language packs: catalogs, templates, checks at install, the app's pages translated, the template up to date."""

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from pokeuds import plugin_guard, plugins
from pokeuds.app import i18n
from pokeuds.app import plugin_store as store

ROOT = Path(__file__).resolve().parents[1]
PSEUDO = ROOT / "examples" / "plugins" / "pseudo"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("POKEUDS_PLUGINS", raising=False)
    plugins._registry = None
    yield
    i18n.select("")
    plugins._registry = None
    plugin_guard.clear()


def test_exact_strings_and_templates():
    catalog = i18n.build({"Bank": "Banco", "Built {value0} and kept it in the bank.": "Se creó {value0} y está en el banco.",
                          "Card {value0} · for {value1}": "Tarjeta {value0} · para {value1}", "{value0}": "x"})
    assert catalog.tr("Bank") == "Banco" and catalog.tr("Bag") == "Bag"
    assert catalog.tr("Built Pikachu Lv 30 and kept it in the bank.") == "Se creó Pikachu Lv 30 y está en el banco."
    assert catalog.tr("Card 0547 · for Omega Ruby, Alpha Sapphire") == "Tarjeta 0547 · para Omega Ruby, Alpha Sapphire"
    assert catalog.tr("Anything") == "Anything"                    # an all-field template matches nothing
    nested = i18n.build({"Bank": "Banco", "Open {value0}": "Abrir {value0}"})
    assert nested.tr("Open Bank") == "Abrir Banco"                  # a field that is itself a string is translated


def test_strings_are_checked():
    assert i18n.parse('"Bank" = "Banco"\n'.encode()) == {"Bank": "Banco"}
    for bad, why in (('"Bank" = 3\n', "not text"), ('"Card {value0}" = "Tarjeta"\n', "fields"),
                     ('"Bank" = "Banco {value0}"\n', "fields"), ('"Bank" = \n', "strings.toml")):
        with pytest.raises(ValueError, match=why):
            i18n.parse(bad.encode())


def test_packs_are_data_only_plugins(tmp_path):
    package = store.read(store.pack(PSEUDO, tmp_path / "pseudo.udsplugin"))
    assert package.findings == [] and "plugin.py" not in package.files
    store.install(package)
    pseudo = next(p for p in plugins.registry().plugins if p.id == "pseudo")
    assert not pseudo.error and "Settings, App language" in pseudo.adds()[0]
    assert [p.locale for p in i18n.packs()] == ["qps-ploc"]
    assert i18n.select("pseudo").tr("Bank") == "[!Bàñk!]" and i18n.select("missing").tr("Bank") == "Bank"
    broken = tmp_path / "broken.udsplugin"
    with zipfile.ZipFile(broken, "w") as z:
        z.writestr("plugin.toml", 'id = "broken"\nname = "Broken"\nversion = "1"\napi = "1.1"\nlocale = "es"\n')
        z.writestr("strings.toml", '"Card {value0}" = "Tarjeta"\n')
    with pytest.raises(ValueError, match="fields"):
        store.read(broken)
    old = tmp_path / "old.udsplugin"
    with zipfile.ZipFile(old, "w") as z:
        z.writestr("plugin.toml", 'id = "old"\nname = "Old"\nversion = "1"\napi = "1.0"\nlocale = "es"\n')
        z.writestr("strings.toml", "")
    with pytest.raises(ValueError, match="1.1"):
        store.read(old)


def test_the_pages_are_translated(monkeypatch, tmp_path):
    flet = pytest.importorskip("flet")
    import dataclasses

    monkeypatch.setenv("POKEUDS_PLUGINS", str(PSEUDO))
    plugins.reload()
    i18n.select("pseudo")
    from pokeuds.app import board

    monkeypatch.setattr(board, "ports", lambda: [])   # a board plugged in shows its port name: data, not text
    from gui.app import App
    from gui.translate import TEXT_FIELDS
    from gui.views.bank import BankView
    from gui.views.board import BoardView
    from gui.views.gifts import GiftsView
    from gui.views.plugins import PluginsView
    from gui.views.settings import SettingsView
    from gui.views.trade import TradeView
    from pokeuds.app.settings import Settings

    settings = Settings(output=str(tmp_path / "received"))
    app = App(None, settings)
    seen, english = 0, []

    def walk(o, done):
        nonlocal seen
        if id(o) in done or not dataclasses.is_dataclass(o) or isinstance(o, type):
            return
        done.add(id(o))
        for f in dataclasses.fields(o):
            v = getattr(o, f.name, None)
            if isinstance(v, str) and (f.name in TEXT_FIELDS or (f.name == "value" and isinstance(o, flet.Text))):
                if any(c.isalpha() for c in v):
                    seen += 1
                    if not v.startswith("[!"):
                        english.append(v)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    walk(x, done)
            elif f.name not in ("parent", "page", "data") and dataclasses.is_dataclass(v):
                walk(v, done)

    for cls in (TradeView, BankView, GiftsView, BoardView, SettingsView, PluginsView):
        walk(cls(app).control(), set())
    data = {"Pseudo", "Pseudo (qps-ploc)", str(tmp_path / "received"), settings.gen7_key_path()}
    assert seen > 100 and [e for e in english if e not in data and not e.startswith("by PokeUDS")] == []


def test_the_template_is_up_to_date():
    out = subprocess.run([sys.executable, "scripts/extract_strings.py", "--check"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    pseudo = i18n.parse((PSEUDO / "strings.toml").read_bytes())
    assert set(pseudo) == set(i18n.template()), "regenerate: python scripts/extract_strings.py --pseudo examples/plugins/pseudo/strings.toml"
