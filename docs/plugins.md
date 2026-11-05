# Plugins

A plugin adds tools, games, radios and offer edits to PokeUDS. It is a folder of Python files with a
`plugin.toml`, shipped as one `.udsplugin` file. The design follows pokeldn's plugins (its `docs/plugins.md`),
on PokeUDS's own API. Third-party plugins are not distributed, reviewed or supported by PokeUDS.

Code: `pokeuds/plugins.py` (loader and API), `pokeuds/app/plugin_store.py` (packages, signatures, sources),
`pokeuds/plugin_guard.py` (guardrails), `pokeuds/app/i18n.py` and `gui/translate.py` (language packs),
`gui/views/plugins.py` (the page). Examples: `examples/plugins/hello` (a tool), `examples/plugins/gb-renamer`
(an offer edit) and `examples/plugins/pseudo` (a language pack).

## Installing a plugin

On **Plugins** (above Board in the side menu), press **Add a plugin...** and choose a `.udsplugin`, or install
one a followed source lists; then accept the install screen. Its tools appear on the Trade page under their
game, marked "plugin". A card per plugin turns it on and off, updates it from a file, shows its folder and
page, and removes it (optionally with its saved data). Every install and update shows the install screen; a
plugin copied into the folder by hand stays off until it is turned on and its install screen accepted.

```bash
python -m pokeuds plugins list
```

```bash
python -m pokeuds plugins install my-plugin-1.0.0.udsplugin --yes
```

`plugins remove ID [--data]`, `plugins enable ID` and `plugins disable ID` do the rest.

A plugin runs as Python inside PokeUDS with full access to files, console keys, the network and the radio.
Nothing in a package runs before it is installed and turned on.

### The install screen

| it shows | from |
|---|---|
| name, version, author, size, the plugin API it needs, its description | `plugin.toml` |
| install, update, reinstall or downgrade, against what is installed | the versions |
| who signed it (below) | `plugin.sig` |
| what its code does: the network, other programs, deleting files, native code (`ctypes`, `cffi`), code built while running (`exec`, `eval`, `compile`, `marshal`), naming your console keys (`aes_keys.txt`, slot 0x2D), compiled files it cannot show | a scan of the Python files (a hint, not a review) |
| where its files go and what it may change | the guardrails |

### Guardrails

As pokeldn's, two checks stop obvious harm; neither is a sandbox. Beside the install screen's scan, an audit
hook (`pokeuds/plugin_guard.py`) raises `PermissionError` while a plugin's code is on the call stack:

| refused | allowed |
|---|---|
| deleting, emptying, moving or overwriting a file outside the plugin's folders | the same in its folder, its data folder, the received folder, the bank and temporary folders |
| starting `rm`, `del`, `rd`, `Remove-Item`, `shred`, `dd`, `format`, `mkfs`, `diskutil`, `diskpart`, `sudo`, `su` and the like, directly or in a shell, or a command with `-delete` or `--no-preserve-root` | any other program; creating files anywhere |

PokeUDS code a plugin calls is checked; code with no plugin on the stack never is.

### Signatures

`scripts/pack_plugin.py --key KEY.pem` signs with an Ed25519 key (created on first use) and prints its
fingerprint (`ABCD-EFGH-IJKL-MNOP`). `plugin.sig` holds the public key and a signature over the id, the version
and every file's SHA-256. Keep the key private and backed up, and publish the fingerprint.

| the app sees | it |
|---|---|
| a signature that does not match | refuses the file |
| a new signed plugin | shows the fingerprint, and remembers the key for that id |
| an update with the remembered key | shows "Signed by the same author as before" |
| another key, or unsigned after signed | warns in red and asks a second acknowledgement |
| unsigned, never signed | shows "Not signed" |

### Sources

A source is a published JSON listing of plugins; the Plugins page's **Sources** follows them and **Browse**
lists what they offer. No source is followed by default.

| typed | the listing read |
|---|---|
| `OWNER/REPO` or `https://github.com/OWNER/REPO` | `https://raw.githubusercontent.com/OWNER/REPO/HEAD/pokeuds-source.json` |
| an https address ending in `.json` | that file |
| any other https address | `pokeuds-source.json` in that folder |

```json
{"schema": 1, "name": "My plugins", "homepage": "https://github.com/me/my-plugins",
 "plugins": [{"id": "my-plugin", "name": "My plugin", "version": "1.0.0", "api": "1.0",
   "description": "...", "author": "me", "download": "https://.../my-plugin-1.0.0.udsplugin",
   "sha256": "64 hex digits", "size": 46302, "homepage": "https://..."}]}
```

An entry lacking `id`, `version`, an https `download` or `sha256`, or repeating an id, is left out; a `schema`
other than 1 is refused; a download whose SHA-256 differs is refused before it is read; a package whose id
differs from the entry's is refused. The install screen and signature check still run. `http://` is accepted
only for `127.0.0.1` and `localhost`.

```bash
python scripts/plugin_source.py my-plugin-1.0.0.udsplugin -o pokeuds-source.json --name "My plugins" --download "https://github.com/me/my-plugins/releases/download/{id}-{version}/{file}"
```

## Making a plugin

    my-plugin/
        plugin.toml        what the plugin is
        plugin.py          register(api): what it adds
        hello.py           a tool's script
        mylib_common.py    optional shared module (the folder is on sys.path: use a unique name)

```toml
id = "my-plugin"            # required: lowercase letters, digits, dots, dashes, underscores
name = "My plugin"          # required
version = "1.0.0"           # required; compared on update
api = "1.0"                 # required: the lowest plugin API it needs
author = "Your name"
description = "Shown on the install screen and the card."
homepage = "https://example.org/my-plugin"
module = "plugin"           # the module holding register()
exclude = ["tests"]         # left out by pack_plugin.py
```

`register(api)` runs when the plugin is on, in the app and in every run it starts:

```python
from pokeuds.plugins import Field, Tool

def register(api):
    api.add_tool("rby", Tool(
        "hello-greet", "Say hello", "greet.py", "Greets your trainer in the session log.",
        ("Nothing to do on the console.", "Press Start and read the log."),
        fields=(Field("--greeting", "Greeting", default="Hello"),),
        fixed=("--trainer", "{ot}"),
    ))
```

### A tool's script

The Trade page lists a tool under its game; Start runs `python -m pokeuds plugin run TOOL -- ARGS` as a child
process, like a trade, with its fields and `fixed` as the arguments. Work belongs under
`if __name__ == "__main__"`.

| the script | the app |
|---|---|
| prints a line | shows it in the session log |
| `pokeuds.status.emit(event, **fields)` | drives the session panel, as a trade's events do (`note`, `stage`, `received`, ...) |
| exits 0 / non-zero, or raises | done / failed with the traceback |
| receives `KeyboardInterrupt` | Stop was pressed: leave within a few seconds or the process is ended |

| `Field` `kind` | the input | passed as |
|---|---|---|
| `text` (default) | a text box | `--flag VALUE` |
| `number` | a number box | `--flag VALUE` |
| `choice` | a dropdown of `choices=((value, label), ...)` | `--flag VALUE` |
| `switch` | an on/off switch | `--flag` when on |
| `file` | a file chooser, `exts=("pk6",)` | `--flag PATH` |

`fixed` may carry `{ot}` (Settings' trainer name), `{tid}`, `{sid}` (the builder's trainer), `{radio}` (the
connection: a tool using it gets the Connection row), `{keys}` (Settings' `aes_keys.txt`), `{output}` (the
received folder), `{game}` and `{stamp}`.

### A game of its own

```python
from pokeuds.plugins import Game

api.add_game(Game("my-game", "My game"))
api.add_tool("my-game", tool)
```

It is a section of the Trade page's list, under Plugins.

### A radio

`api.add_radio(scheme, factory, label)` adds a Connection choice; a run with `--radio scheme:argument` calls
`factory(argument)`, which returns a `pokeuds.radio.base.Radio` (start, set_channel, send, set_beacon, watch,
recv, close; `pokeuds/radio/esp32.py` and `ldnd.py` are references). The Connection row asks for the argument.

### Offer edits

`api.add_offer_edit(game, edit, label)` runs `edit(record) -> record` on every Pokemon a trade of `game`
(`xy`, `oras`, `sm`, `usum`, `rby`, `gsc`) offers. The record is the offer's bytes: a `.pk1`/`.pk2` file as
PokeUDS writes it, or the decrypted Gen 6/7 record.

| step | |
|---|---|
| 1 | each plugin's offer edit, in load order; the run logs `the plugin LABEL edited the offer` |
| 2 | PKHeX's legality check, when any edit changed the record |

PKHeX's check is the last step, and no plugin turns it off: an edit whose result PKHeX refuses stops the
trade with PKHeX's reason, an edit that raises or returns nothing stops it with the plugin's label, and an
edited offer needs the PKHeX helper built (docs/pkhex.md).

### What a plugin keeps

An update replaces the folder. Lasting files go in `api.data` (`<data>/plugin-data/<id>`), deleted on removal
only if asked.

### Packing and testing

```bash
python scripts/pack_plugin.py my-plugin --key ~/my-plugin-signing.pem
```

```bash
POKEUDS_PLUGINS=~/my-plugin python -m pokeuds plugins list
```

```bash
POKEUDS_PLUGINS=~/my-plugin python -m gui
```

`pack_plugin.py` writes `my-plugin-1.0.0.udsplugin` and reads it back as the app does; hidden files,
`__pycache__` and `exclude` stay out. Any zip works with `plugin.toml` at its top or in one top folder, no
escaping path and no link. `POKEUDS_PLUGINS` (folders, separated as PATH is) turns plugins on for that process
and its runs, without the install screen. A plugin that raises in `register`, needs another API or reuses a
key adds nothing, and its card shows why.

## Language packs (1.1)

A language pack is a data-only plugin: no code, a `locale`, and the translations. Its `name` shows in Settings,
**App language**; choosing it redraws every page at once.

```toml
id = "community-es"
name = "Español"
version = "1.0.0"
api = "1.1"
locale = "es"                  # a language tag: es, pt-BR, zh-Hans...
strings = "strings.toml"       # optional, the default
font_family = "Noto Sans"      # optional, at most 100 characters
```

`strings.toml` maps the app's English text to its translation, a quoted key each:

```toml
"Choose a Pokemon to offer: from the bank, Build one, or a file" = "Elige un Pokemon para ofrecer: del banco, Crear uno o un archivo"
"Built {value0} ({value1}), kept it in the bank and made it the offer." = "Se creó {value0} ({value1}), se guardó en el banco y es la oferta."
```

A key with `{value0}`-style fields is a template: it matches text whose literal parts are the same, the
fields standing for whatever the app filled in (a species, a count, a path), and the translation keeps the
same fields, in any order. A field whose text is itself one of the strings is translated too. Bad TOML,
non-text values and a translation whose fields differ from its key are refused at install; text a pack does
not cover stays English.

The app's pages are written in English and translated as they are drawn (`gui/translate.py`): every control's
text, label, hint, helper and tooltip, the side menu and dialogs; never what the player types. Logs, file
names, PKHeX's legality reports and the command line stay English.

```bash
python scripts/extract_strings.py
```

```bash
python scripts/check_language_pack.py PACK_FOLDER
```

`extract_strings.py` writes the template, `pokeuds/app/data/strings.en.toml`: every string the app shows,
English = English (the tests check it is up to date). A pack starts as a translated copy of it.
`check_language_pack.py` lists what a pack does not translate and the keys the app no longer has, and exits 1
on a field difference. `examples/plugins/pseudo` is a pseudo-locale covering every string (accented, in
`[! !]`), made by `extract_strings.py --pseudo`: turn it on to see what a pack reaches.

## Versions

The API is `major.level`; a plugin's `api` is the lowest it needs. An addition raises the level (a higher need
is refused with "update PokeUDS"); a removal or change of meaning raises the major.

| API | added |
|---|---|
| 1.0 | `add_tool`, `add_game`, `add_radio`, `add_offer_edit`, `folder`, `data`, `version`, `level`; `Tool`, `Field`, `Game` |
| 1.1 | language packs: `locale`, `strings`, `font_family` ([Language packs](#language-packs-11)) |

## The API, version 1

| name | what it is |
|---|---|
| `api.add_tool(game, tool)` | a `Tool` under `xy`, `oras`, `sm`, `usum`, `rby`, `gsc` or a plugin's game |
| `api.add_game(game)` | a `Game` section on the Trade page |
| `api.add_radio(scheme, factory, label="")` | a radio |
| `api.add_offer_edit(game, edit, label="")` | an edit to every offer of a trade, before PKHeX's check |
| `api.folder`, `api.data` | the plugin's folder; its kept data folder |
| `api.version`, `api.level` | the app's plugin API, `"1.1"` and `1` |
| `pokeuds.plugins.Tool`, `Field`, `Game` | the types |
| `pokeuds.status.emit(event, **fields)` | session panel events from a tool's script |

Compared with pokeldn: no pop-ups, status cards, libraries, battle servers or update checks from a plugin's
own `updates` address yet (sources update what they list); language packs translate by English text rather
than by keys, and have no messages, terms, help or documents resources.
