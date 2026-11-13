# Command-line instructions

Everything the app does can also be run from a terminal, along with the tools for captures and development. The
command line runs from source on any OS (Python 3.11 or newer); it shares the app's Settings, bank and plugins.

## Setting up

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

On macOS or Linux, activate with `. .venv/bin/activate`. `requirements.txt` is enough for the command line;
`requirements-gui.txt` adds the app, the board flasher (`esptool`) and the DS Wi-Fi server's SSL (`tlslite-ng`).

```bash
python -m pokeuds --help
```

lists every command; `python -m pokeuds COMMAND --help` explains one.

## Options most commands share

| option | meaning |
|---|---|
| `--radio esp32[:PORT]` | an ESP32 board, found by its USB id unless PORT is given (the default) |
| `--radio ldnd[:PIPE]` | a USB Wi-Fi adapter through ldnd.exe 0.0.5 (Windows; [hardware.md](hardware.md)) |
| `--radio demo` | a simulated console, for trying a command without hardware |
| `--keys aes_keys.txt` | the keyslot 0x2D key; defaults to the file chosen in the app's Settings |
| `--offer FILE` | the Pokemon to send; repeat it to queue more, traded in order |
| `--output FOLDER` | where received Pokemon go (default: `received/` in the current folder) |
| `-v` | before the command (`python -m pokeuds -v trade ...`): show what happens |

## Checking the radio

```bash
python -m pokeuds board list
```

```bash
python -m pokeuds board flash
```

```bash
python -m pokeuds probe --radio esp32
```

```bash
python -m pokeuds scan --radio esp32 --seconds 30
```

`board` lists attached ESP32 boards, reads their firmware (`board check`) and installs the included
esp32-uds-bridge 2.0-uds for the chip it finds (`board flash`). `probe` checks that the radio answers; `scan` lists
every 3DS network in range.

## Trading

X/Y, ORAS, Sun/Moon and USUM:

```bash
python -m pokeuds trade sm --radio esp32 --offer my.pk7
```

```bash
python -m pokeuds trade xy --role host --radio esp32 --offer my.pk6 --partner Serena
```

By default PokeUDS joins the network the 3DS opens (the 3DS asks for the trade). `--role host` has PokeUDS ask:
`--partner` names the trainer to ask in the PSS or Festival Plaza (default: the first one heard). `--trainer`,
`--tid` and `--sid` set what the 3DS sees; `--trades` sets how many trades before PokeUDS leaves.

The Virtual Console games (Red, Blue, Yellow, Gold, Silver, Crystal):

```bash
python -m pokeuds vc trade --radio esp32 --offer my.pk1
```

```bash
python -m pokeuds vc trade --role host --as gold --radio esp32 --offer my.pk2
```

Up to six Pokemon queue with `--offer`; a `.pk2` may have a `.mail` beside it. With `--role host`,
`--as` names the title PokeUDS stands in for, `red-de` or `gold-jp` for another language ([vc.md](vc.md)).
`vc show` reads a `.pk1`/`.pk2`; `vc convert` converts between them as the Time Capsule does.

## Building Pokemon

```bash
python -m pokeuds build sm pikachu --level 50 --shiny --bank
```

Builds a legal Pokemon with PKHeX for `rby`, `gsc`, `xy`, `oras`, `sm` or `usum`: `--nature`, `--gender`,
`--ability`, `--item`, `--ball`, `--moves`, `--nickname`, `--language`, and the trainer (`--ot`, `--tid`, `--sid`,
`--female`). `--out` writes the file, `--bank` keeps it in the bank, `--fix` takes the closest legal Pokemon when the
one asked for is not ([pkhex.md](pkhex.md)). `pkx` (or `pk6`, `pk7`) shows a `.pk6`/`.pk7` file.

## Mystery Gift

Gen 6 and Gen 7, over local wireless:

```bash
python -m pokeuds gift list sm --search Magearna
```

```bash
python -m pokeuds gift distribute sm my.wc7 --radio esp32
```

`gift list` lists PKHeX's official cards; `show` describes one; `make` makes its Pokemon (into the bank by default);
`edit` turns a card into a `.udsgift` to change; `card` builds a custom card; `distribute` sends a card, a
`.wc6`/`.wc7`, a full file or an official card's key, as an event distribution did. Gen 7 needs the signing key:
`--gen7-key` points at `rsa_d_key.txt` when it is not beside `aes_keys.txt`. To extract that key from a terminal
instead of Settings:

```bash
python scripts/extract_gen7_key.py DISTRIBUTOR.cxi --keys aes_keys.txt
```

(a `.cci`/`.3ds` or the distributor's `code.bin` work too; [mystery_gift.md](mystery_gift.md)).

Gen 4 and Gen 5, over the DS's Wi-Fi (pre-alpha; [ds_wifi.md](ds_wifi.md)):

```bash
python -m pokeuds wfc check
```

```bash
python -m pokeuds wfc serve
```

`wfc check` reads your Wii certificate pair and makes the server certificate; `wfc serve` answers the DS's DNS,
connection test, login and gift download until Ctrl+C; `wfc gifts` writes the official Gen 4/5 cards as the files
it serves.

## Captures and analysis

```bash
python -m pokeuds sniff --radio esp32 --game xy --out session.jsonl
```

```bash
python -m pokeuds analyze session.jsonl --extract found
```

`sniff` decrypts another pair of consoles' session; `analyze` reads that or an Azahar log (azahar_uds), and
`--extract` writes every Pokemon it finds ([captures.md](captures.md)).

## Plugins and PKHeX

```bash
python -m pokeuds plugins list
```

```bash
python -m pokeuds plugin run TOOL -- ARGS
```

`plugins` lists, installs (`install FILE.udsplugin`, after showing what it does), removes, enables and disables
plugins; `plugin run` runs a plugin's tool ([plugins.md](plugins.md)). From source, `pkhex status` says where
PKHeX is and `pkhex build` builds it (needs the .NET 10 SDK; [pkhex.md](pkhex.md)).

## Protocols

Each game's protocol is in [vc.md](vc.md), [gen6.md](gen6.md) and [gen7.md](gen7.md); the code's layout and the tests
are in [architecture.md](architecture.md).
