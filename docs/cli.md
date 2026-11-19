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
`requirements-gui.txt` adds the app and the board flasher (`esptool`).

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
esp32-uds-bridge 2.1-uds for the chip it finds (`board flash`). `probe` checks that the radio answers; `scan` lists
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

Online, with a player far away who runs the same command beside their own 3DS ([online.md](online.md)):

```bash
python -m pokeuds trade xy --radio esp32 --online-code 1234
```

`--online` (or `--online-code`, which implies it) offers the partner's 3DS's Pokemon to this 3DS, so there is no
`--offer`; each side says yes only once both 3DSs have. Both players type the same code; with `--online` and no code,
PokeUDS meets anyone else trading the same games online without one. X/Y meets ORAS, Sun/Moon meets USUM. PKHeX
checks what arrives first, so it must be set up. PokeUDS stays on the trade screen until the partner leaves (or
`--trades` trades). `POKEUDS_RELAYS=wss://a,wss://b` replaces the relays. Not yet tried with two real 3DSs.

The Virtual Console games trade online the same way:

```bash
python -m pokeuds vc trade --radio esp32 --online-code 1234
```

PokeUDS plays the partner's console to yours: the room your 3DS chooses (the Trade Center, or a Gen 2 game's Time
Capsule) decides where it meets the partner, Red/Blue/Yellow and the Time Capsule together, Gold/Silver/Crystal's
Trade Center apart. Each 3DS sees the other's real party, pick and answer. PKHeX is not needed.

**Touch Trade** (experimental), for trade evolutions (Kadabra, Machoke, Graveler, Haunter, and the held-item ones):

```bash
python -m pokeuds trade xy --radio esp32 --touch-trade
```

```bash
python -m pokeuds vc trade --radio esp32 --touch-trade
```

PokeUDS offers back whatever the 3DS offers, in the same trade: the 3DS receives its own Pokemon, as from a trade,
and evolves it. On the Virtual Console the 3DS is shown its own party and PokeUDS picks the slot it picks. There is
no `--offer`, PokeUDS keeps nothing, and the trade is one swap, so the Pokemon is never away from the 3DS. Karrablast
and Shelmet evolve only when traded for each other, so not this way. It goes on until the 3DS leaves the trade
screen, or `--trades` trades.

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

Gen 4 and Gen 5, over DS local wireless (no console keys; the game is `dp`, `pt`, `hgss`, `bw` or `b2w2`):

```bash
python -m pokeuds gift list bw --search Darkrai
```

```bash
python -m pokeuds gift distribute bw KEY --radio esp32 --language ENG
```

The source is an official card's key (`gift list` shows them, items and Gen 4's key items too), a `.pcd`/`.wc4`/`.pgf`,
or a gift file (`.myg`, 936 bytes; `.bin` or `.wc5full`, 720).
`--language` is the receiving cartridge's: `ENG`, `JPN`, `FRE`, `ITA`, `GER`, `SPA` or `KOR` (default: a Gen 5 card's
own, else `ENG`). It sends on channel 7 from `02:00:00:00:00:01`; `--mac` does not apply. On the DS: Mystery Gift,
Receive Gift, the wireless option.


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
