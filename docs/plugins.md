# Plugins

A plugin adds things to PokeUDS:

- **tools**: scripts listed on the Trade page under a game, with their own inputs and a Start button
- **games**: a section of their own on the Trade page
- **radios**: a Connection choice for other hardware
- **offer edits**: a change to every Pokemon a trade offers, checked by PKHeX afterwards
- **language packs**: the app's text in another language (no code)

A plugin is a folder of Python files with a `plugin.toml`, shipped as one `.udsplugin` file. Third-party
plugins are not distributed, reviewed or supported by PokeUDS.

- **Using plugins:** [Installing a plugin](#installing-a-plugin)
- **Writing one:** [Your first plugin](#your-first-plugin) takes about ten minutes, then
  [Making a plugin](#making-a-plugin) covers everything a plugin can add
- **Translating the app:** [Language packs](#language-packs-11)
- **Something not working:** [Troubleshooting](#troubleshooting)

## Installing a plugin

On **Plugins** (above Board in the side menu), press **Add a plugin...** and choose a `.udsplugin` file, or
install one from a [source](#sources) you follow. Read the install screen and accept it. The plugin's tools
then appear on the Trade page under their game, marked "plugin".

Each plugin has a card that turns it on and off, updates it from a file, opens its folder and homepage, and
removes it (its saved data too, if you tick that). Every install and update shows the install screen again. A
plugin copied into the plugins folder by hand stays off until you turn it on and accept its install screen.

The command line does the same:

```bash
python -m pokeuds plugins list
```

```bash
python -m pokeuds plugins install my-plugin-1.0.0.udsplugin --yes
```

`plugins remove ID [--data]`, `plugins enable ID` and `plugins disable ID` do the rest.

> **A plugin is not sandboxed.** It runs as Python inside PokeUDS, with full access to your files, console
> keys, the network and the radio. Install plugins only from people you trust. Nothing in a package runs
> until it is installed and turned on.

### The install screen

| it shows | where that comes from |
|---|---|
| name, version, author, size, the plugin API it needs, its description | `plugin.toml` |
| whether this is an install, update, reinstall or downgrade | the installed version |
| who signed it ([Signatures](#signatures)) | `plugin.sig` |
| what its code does: the network, other programs, deleting files, native code (`ctypes`, `cffi`), code built while running (`exec`, `eval`, `compile`, `marshal`), naming your console keys (`aes_keys.txt`, slot 0x2D), compiled files it cannot show | a scan of the Python files. It is a hint, not a review |
| where its files go and what it may change | the [guardrails](#guardrails) |

### Guardrails

Two checks stop obvious harm. Neither is a sandbox. Besides the install screen's scan, an audit hook
(`pokeuds/plugin_guard.py`) raises `PermissionError` whenever a plugin's code is on the call stack and tries
one of these:

| refused | allowed |
|---|---|
| deleting, emptying, moving or overwriting a file outside the plugin's folders | the same inside its own folder, its data folder, the received folder, the bank and temporary folders |
| starting `rm`, `del`, `rd`, `Remove-Item`, `shred`, `dd`, `format`, `mkfs`, `diskutil`, `diskpart`, `sudo`, `su` and the like, directly or through a shell, or any command with `-delete` or `--no-preserve-root` | any other program; creating files anywhere |

PokeUDS's own code is checked too when a plugin called it. Code with no plugin on the stack is never checked.

### Signatures

A plugin can be signed by its author ([Packing and signing](#packing-and-signing)). The app remembers the
first key it sees for each plugin id:

| the app sees | it |
|---|---|
| a signature that does not match the files | refuses the file |
| a signed plugin it has not seen before | shows the key's fingerprint, and remembers the key for that id |
| an update signed with the remembered key | shows "Signed by the same author as before" |
| a different key, or an unsigned update to a signed plugin | warns in red and asks you to tick a second box |
| an unsigned plugin that was never signed | shows "Not signed" |

Compare the fingerprint with the one the author publishes.

### Sources

A source is a published list of plugins (a `pokeuds-source.json`). Follow one under **Sources** on the Plugins
page, then **Browse** lists what it offers. No source is followed by default.

| you type | the list it reads |
|---|---|
| `OWNER/REPO` or `https://github.com/OWNER/REPO` | `https://raw.githubusercontent.com/OWNER/REPO/HEAD/pokeuds-source.json` |
| an https address ending in `.json` | that file |
| any other https address | `pokeuds-source.json` in that folder |

A download whose SHA-256 differs from the list's is refused before it is read, and a package whose id differs
from its entry is refused. The install screen and the signature check still run. To publish a source of your
own, see [Publishing a source](#publishing-a-source).

## Your first plugin

You need PokeUDS **from source** (README, "From source": Python 3.11 or newer). The packing scripts are in
`scripts/` and are not part of `PokeUDS.exe`. Run the commands below from the PokeUDS folder with the virtual
environment active.

**1. Copy the example.** `examples/plugins/hello` is the smallest plugin: one tool under Red/Blue/Yellow.
Copy it somewhere of your own and give it a new id.

```powershell
Copy-Item -Recurse examples\plugins\hello ~\my-plugin
```

```bash
cp -r examples/plugins/hello ~/my-plugin
```

In `~/my-plugin/plugin.toml`, change `id` to `my-plugin`, then change `name`, `author` and `description`. In
`plugin.py`, change the tool's `key` from `hello-greet` to something unique, such as `my-plugin-greet`.

**2. Try it without installing.** `POKEUDS_PLUGINS` turns plugins on for one process, with no install screen:

```powershell
$env:POKEUDS_PLUGINS = "$HOME\my-plugin"
python -m pokeuds plugins list
```

```bash
POKEUDS_PLUGINS=~/my-plugin python -m pokeuds plugins list
```

You should see `my-plugin 1.0.0 [on] (POKEUDS_PLUGINS): ...` followed by `adds Say hello (rby)`. If it says
`[error]`, the next line says why ([Troubleshooting](#troubleshooting)).

**3. Run its tool from the command line.** This is what the app's Start button does:

```bash
python -m pokeuds plugin run my-plugin-greet -- --greeting Hi --trainer Red
```

It prints `Hi, Red!`. In PowerShell, `$env:POKEUDS_PLUGINS` stays set until the window is closed. With bash,
put `POKEUDS_PLUGINS=~/my-plugin` in front of the command again.

**4. See it in the app.**

```powershell
$env:POKEUDS_PLUGINS = "$HOME\my-plugin"
python -m gui
```

```bash
POKEUDS_PLUGINS=~/my-plugin python -m gui
```

Open Trade, then Red/Blue/Yellow: "Say hello" is listed and marked "plugin". After an edit to `plugin.py`,
restart the app. An edit to the tool's script (`greet.py`) needs no restart, because every Start runs it again.

**5. Pack it.**

```bash
python scripts/pack_plugin.py ~/my-plugin --key ~/my-plugin-signing.pem
```

This writes `my-plugin-1.0.0.udsplugin` and creates the signing key if it does not exist yet. Add the file on
the Plugins page to see the install screen your users will see. To stop using `POKEUDS_PLUGINS` in PowerShell,
run `Remove-Item Env:POKEUDS_PLUGINS`.

The other examples: `examples/plugins/gb-renamer` is an offer edit that keeps a setting in its data folder, and
`examples/plugins/pseudo` is a language pack.

## Making a plugin

    my-plugin/
        plugin.toml        what the plugin is
        plugin.py          register(api): what it adds
        greet.py           a tool's script
        mylib_common.py    optional shared module (the folder is on sys.path, so give it a unique name)

```toml
id = "my-plugin"            # required: lowercase letters, digits, dots, dashes, underscores
name = "My plugin"          # required
version = "1.0.0"           # required; compared on update
api = "1.0"                 # required: the lowest plugin API it needs (see Versions)
author = "Your name"
description = "Shown on the install screen and the card."
homepage = "https://example.org/my-plugin"
module = "plugin"           # the module holding register(); plugin.py by default
exclude = ["tests"]         # left out by pack_plugin.py
```

PokeUDS calls `register(api)` when the plugin is on, in the app and in every run the app starts. It says what
the plugin adds and does nothing else:

```python
from pokeuds.plugins import Field, Tool


def register(api):
    api.add_tool("rby", Tool(
        key="my-plugin-greet",                  # unique among every plugin's tools
        name="Say hello",                       # its name on the Trade page
        script="greet.py",                      # its script, next to this file
        summary="Greets your trainer in the session log.",
        steps=("Nothing to do on the console.", "Press Start and read the log."),
        fields=(Field("--greeting", "Greeting", default="Hello"),),
        fixed=("--trainer", "{ot}"),            # always passed; {ot} is Settings' trainer name
    ))
```

The games are `xy`, `oras`, `sm`, `usum`, `rby` and `gsc`, or one a plugin adds ([A game of its
own](#a-game-of-its-own)). `api.add_tool` checks the tool at once: an unknown game, a key already taken, a
missing script or an unknown token raises, and the plugin's card shows the message.

### A tool's script

The Trade page lists a tool under its game. Start runs `python -m pokeuds plugin run KEY -- ARGS` as a child
process, the same way it runs a trade. The arguments are the fields' values, then `fixed`. Parse them with
`argparse`, and put the work under `if __name__ == "__main__":` (see `examples/plugins/hello/greet.py`).

| the script | the app |
|---|---|
| prints a line | shows it in the session log |
| calls `pokeuds.status.emit(event, **fields)` | updates the session panel, as a trade's events do (`note`, `stage`, `received`, ...) |
| exits 0 / exits non-zero or raises | shows done / failed, with the traceback |
| receives `KeyboardInterrupt` | Stop was pressed: finish within a few seconds or the process is ended |

Each `Field` is one input on the tool's page:

| `kind` | the input | passed as |
|---|---|---|
| `text` (default) | a text box | `--flag VALUE` |
| `number` | a number box | `--flag VALUE` |
| `choice` | a dropdown of `choices=((value, label), ...)` | `--flag VALUE` |
| `switch` | an on/off switch | `--flag` when on |
| `file` | a file chooser, `exts=("pk6",)` | `--flag PATH` |

`Field(flag, label, kind="text", default="", choices=(), exts=(), help="")`; `help` is the input's tooltip.

`fixed` may carry these tokens, filled in when Start is pressed:

| token | becomes |
|---|---|
| `{ot}` | Settings' trainer name |
| `{tid}`, `{sid}` | the builder's trainer ids |
| `{radio}` | the connection (a tool that uses it gets the Connection row) |
| `{keys}` | the path of Settings' `aes_keys.txt` |
| `{output}` | the received folder |
| `{game}` | the game the tool is listed under |
| `{stamp}` | a date-and-time stamp, for file names |

### A game of its own

```python
from pokeuds.plugins import Game

api.add_game(Game("my-game", "My game"))
api.add_tool("my-game", tool)
```

It is a section of the Trade page's list, under Plugins.

### A radio

`api.add_radio(scheme, factory, label)` adds a Connection choice. A run with `--radio scheme:argument` calls
`factory(argument)`, which returns a `pokeuds.radio.base.Radio` (`start`, `set_channel`, `send`, `set_beacon`,
`watch`, `recv`, `close`). `pokeuds/radio/esp32.py` and `ldnd.py` are the references. The Connection row asks
for the argument. `esp32`, `ldnd` and `demo` are taken.

### Offer edits

`api.add_offer_edit(game, edit, label)` runs `edit(record) -> record` on every Pokemon a trade of `game`
offers (`xy`, `oras`, `sm`, `usum`, `rby` or `gsc`). The record is the offer's bytes: a `.pk1`/`.pk2` file as
PokeUDS writes it, or the decrypted Gen 6/7 record. Return the record unchanged to leave it alone.

| step | |
|---|---|
| 1 | each plugin's offer edit, in load order; the run logs `the plugin LABEL edited the offer` |
| 2 | PKHeX's legality check, when any edit changed the record |

PKHeX's check is always last, and no plugin can turn it off. If PKHeX refuses the result, the trade stops with
PKHeX's reason. If an edit raises or returns nothing, the trade stops with the plugin's label. An edited offer
needs the PKHeX helper built ([docs/pkhex.md](pkhex.md)).

### What a plugin keeps

An update replaces the plugin's folder, so keep lasting files in `api.data` (`<data>/plugin-data/<id>`). It is
deleted when the plugin is removed only if the player asks. `examples/plugins/gb-renamer` reads its setting
from `api.data / "settings.json"`.

### Packing and signing

```bash
python scripts/pack_plugin.py my-plugin --key ~/my-plugin-signing.pem
```

`pack_plugin.py` writes `my-plugin-1.0.0.udsplugin` and reads it back as the app does, printing what the
install screen will say about the code. Hidden files, `__pycache__` and `exclude` are left out.

`--key` signs with an Ed25519 key, created on first use, and prints its fingerprint (`ABCD-EFGH-IJKL-MNOP`).
`plugin.sig` holds the public key and a signature over the id, the version and every file's SHA-256. Keep the
key private and backed up: an update signed with another key warns your users in red. Publish the fingerprint
where your users will look, such as your homepage.

Leave out `--key` for an unsigned package. Any zip works too, with `plugin.toml` at its top or in one top
folder, no path escaping the folder and no links.

### Publishing a source

`plugin_source.py` writes or updates a `pokeuds-source.json` for your packages. `{id}`, `{version}` and
`{file}` in `--download` are filled in for each one:

```bash
python scripts/plugin_source.py my-plugin-1.0.0.udsplugin -o pokeuds-source.json --name "My plugins" --download "https://github.com/me/my-plugins/releases/download/{id}-{version}/{file}"
```

Put the packages where `--download` says and `pokeuds-source.json` at the top of the repository (or at the
https address you give out). The file looks like this:

```json
{"schema": 1, "name": "My plugins", "homepage": "https://github.com/me/my-plugins",
 "plugins": [{"id": "my-plugin", "name": "My plugin", "version": "1.0.0", "api": "1.0",
   "description": "...", "author": "me", "download": "https://.../my-plugin-1.0.0.udsplugin",
   "sha256": "64 hex digits", "size": 46302, "homepage": "https://..."}]}
```

An entry lacking `id`, `version`, an https `download` or `sha256`, or repeating an id, is left out. A `schema`
other than 1 is refused. `http://` is accepted only for `127.0.0.1` and `localhost`, for testing.

## Troubleshooting

Start with `python -m pokeuds plugins list` (with `POKEUDS_PLUGINS` set, if you use it). It shows each plugin,
`[on]`, `[off]` or `[error]`, what it adds, and the error. The Plugins page shows the same error on the card.

| you see | it means |
|---|---|
| the tool is not on the Trade page | the plugin is off, it has an error, or the app was not restarted after an edit to `plugin.py` |
| `not accepted yet: turn it on from the Plugins page to see what it does` | it was copied into the plugins folder by hand; turn it on and accept the install screen |
| `the folder is X but the plugin is Y` | an installed plugin's folder must be named after its `id` |
| `needs plugin API 1.2; update PokeUDS` | `api` in `plugin.toml` is newer than this PokeUDS ([Versions](#versions)) |
| `the tool key 'x' is taken` | another tool, from any plugin, has that key; prefix keys with your plugin's id |
| `no game 'x': one of ...` | the game key is wrong, or `add_tool` came before the `add_game` it needs |
| `no script x.py in the plugin's folder` | `script` names a file that is not next to `plugin.py` |
| `plugin.py has no register(api)` | the module named by `module` has no `register` function |
| an import picks up another plugin's module | every plugin's folder is on `sys.path`; give helper modules unique names, such as `myplugin_common.py` |
| `PermissionError: PokeUDS's plugin guardrails stopped ...` | the code tried something the [guardrails](#guardrails) refuse; write inside `api.data` instead |
| `... edited the offer, and an edited offer must pass PKHeX's check` | build the PKHeX helper ([docs/pkhex.md](pkhex.md)) or turn the offer edit off |
| `PKHeX refused the offer ...` | the offer edit made an illegal Pokemon; the reason follows |

A plugin that raises in `register`, needs another API or reuses a key adds nothing at all, so one mistake
hides every tool it would have added.

## Language packs (1.1)

A language pack is a plugin with no code: a `locale` and the translations. Its `name` appears in Settings,
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

`strings.toml` maps the app's English text to its translation, each key quoted:

```toml
"Choose a Pokemon to offer: from the bank, Build one, or a file" = "Elige un Pokemon para ofrecer: del banco, Crear uno o un archivo"
"Built {value0} ({value1}), kept it in the bank and made it the offer." = "Se creó {value0} ({value1}), se guardó en el banco y es la oferta."
```

A key with `{value0}`-style fields is a template. It matches text whose fixed parts are the same, with the
fields standing for whatever the app filled in (a species, a count, a path). The translation keeps the same
fields, in any order. A field whose text is itself one of the strings is translated too. Bad TOML, non-text
values and a translation whose fields differ from its key are refused at install. Text a pack does not cover
stays English.

The app's pages are written in English and translated as they are drawn (`gui/translate.py`): every control's
text, label, hint, helper and tooltip, the side menu and dialogs, but never what the player types. Logs, file
names, PKHeX's legality reports and the command line stay English.

To start a pack:

1. Copy `pokeuds/app/data/strings.en.toml`, the template: every string the app shows, English = English.
2. Translate the right-hand sides, and write a `plugin.toml` like the one above.
3. Check it, then pack it as any plugin ([Packing and signing](#packing-and-signing)).

```bash
python scripts/check_language_pack.py PACK_FOLDER
```

`check_language_pack.py` lists what a pack does not translate and the keys the app no longer has, and exits 1
on a field difference. After an app update, run it again to see what is new.

`python scripts/extract_strings.py` rebuilds the template from the app's code (the tests check it is up to
date). `examples/plugins/pseudo` is a pseudo-locale covering every string (accented, in `[! !]`), made by
`extract_strings.py --pseudo`: turn it on to see which text a pack reaches.

## Versions

The plugin API is `major.level`. A plugin's `api` is the lowest it needs. An addition raises the level; a
plugin needing a higher level than the app has is refused with "update PokeUDS". A removal or change of
meaning raises the major.

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

Code: `pokeuds/plugins.py` (loader and API), `pokeuds/app/plugin_store.py` (packages, signatures, sources),
`pokeuds/plugin_guard.py` (guardrails), `pokeuds/app/i18n.py` and `gui/translate.py` (language packs),
`gui/views/plugins.py` (the page), `tests/test_plugins.py`.

## Compared with pokeldn

The design follows the plugins of [pokeldn](https://github.com/Decryptu/pokeldn), the Switch app PokeUDS is
modelled on (its `docs/plugins.md`), on PokeUDS's own API. PokeUDS plugins have no pop-ups, status cards,
libraries, battle servers or update checks from a plugin's own `updates` address yet; sources update what they
list. Language packs translate by English text rather than by keys, and have no messages, terms, help or
documents resources.
