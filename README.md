# PokeUDS

PokeUDS trades with retail Pokemon games on Nintendo 3DS over local wireless (UDS), the way
[pokeldn](https://github.com/Decryptu/pokeldn) does for Switch: a computer with an ESP32-S3 board or a
USB Wi-Fi adapter stands in for a second console. Nothing is installed on the 3DS.

Status: **alpha**. The radio backends and the UDS layer (scanning, hosting, joining, CCMP,
SecureData) work and are tested against each other in simulation; nothing has run on hardware yet.
The Generation 6 trade messages that ride inside UDS are not mapped yet; they need captures
([docs/captures.md](docs/captures.md)).

| | X/Y | ORAS | Sun/Moon | USUM | VC Gen 1 | VC Gen 2 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Trading starts in | PSS | PSS | Festival Plaza | Festival Plaza | VC link menu, Trade Center | VC link menu, Trade Center or Time Capsule |
| Record | .pk6 | .pk6 | .pk7 | .pk7 | .pk1 | .pk2 (+ mail) |
| Trades with | ORAS | X/Y | USUM | Sun/Moon | Gen 2 through the Time Capsule | Gen 1 through the Time Capsule |
| UDS session, join | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| UDS session, host | ✓ | ✓ | ✓ | ✓ | ✓ experimental | ✓ experimental |
| Trade messages | needs captures | needs captures | needs captures | needs captures | ✓ | ✓ |

✓ works in simulation, untested on hardware. All four 3DS generations' games share one trade
session (`games/trade.py`); `games/gen6` and `games/gen7` declare the games, and each game reads its
constants from `profiles/<game>.toml`. Local trades never cross a generation.

The Virtual Console Gen 1 and 2 games are the first with the **whole trade implemented** (tested in simulation, not yet on hardware): their link protocol is
known (pret's disassemblies for the Game Boy side, Gr3nSkyDragon's captures for the VC's Pia session), so
PokeUDS trades a `.pk1`/`.pk2` from end to end, including mail between Gen 2 games and the Time Capsule in
both directions ([docs/vc.md](docs/vc.md)).

## Hardware

Either radio works; see [docs/hardware.md](docs/hardware.md).

- **ESP32-S3** flashed with [esp32-uds-bridge](https://github.com/Gr3nSkyDragon/VC-Adapter/tree/main/firmware/esp32-uds-bridge),
  connected by its native USB port. Any OS.
- **USB Wi-Fi adapter** through legacy **ldnd.exe 0.0.5** (unlimitedcoder2's gist), Windows only. Not the
  ldnrs revision.

You also need `aes_keys.txt` with the keyslot 0x2D key (`slot0x2DKeyN`, or `slot0x2DKeyX`,
`slot0x2DKeyY` and `generatorConstant`), the same file Azahar uses. Only scanning works without it.

## Use

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
```

### The app

```bash
pip install -r requirements-gui.txt
```

```bash
python -m gui
```

Pick a game, the Pokemon to offer and who hosts, then Start ([docs/gui.md](docs/gui.md)). Settings, Debug
tools adds a demo radio that trades with a simulated 3DS, a network scan and capture analysis.

### The command line

```bash
python -m pokeuds probe --radio esp32
```

```bash
python -m pokeuds scan --radio esp32 --seconds 30
```

`scan` lists every 3DS UDS network in range with its comm id, decrypted player list and application
data; open the PSS (X/Y, ORAS) or the Festival Plaza (Sun/Moon, USUM) on a console and it should appear. With a USB adapter use `--radio ldnd`.

```bash
python -m pokeuds analyze azahar_log.txt --extract captures/pokemon
```

```bash
python -m pokeuds sniff --radio esp32 --keys aes_keys.txt --game xy --out captures/xy_trade.jsonl
```

```bash
python -m pokeuds trade xy --radio esp32 --keys aes_keys.txt --offer my.pk6 --output received
```

```bash
python -m pokeuds trade oras --role host --radio esp32 --keys aes_keys.txt --offer my.pk6
```

```bash
python -m pokeuds trade usum --radio esp32 --keys aes_keys.txt --offer my.pk7
```

```bash
python -m pokeuds vc trade --radio esp32 --keys aes_keys.txt --offer my.pk1
```

`vc trade` joins a 3DS that chose Host in the VC link menu and trades through the Trade Center or the
Time Capsule; see [docs/vc.md](docs/vc.md) for the modes and hosting.

`trade` loads and checks the offer (a Gen 7 species is refused for a Gen 6 game), then either finds the console and joins its network
(`--role join`, the default) or opens a network and waits for the console to join (`--role host`,
advertising the profile's application data or `--application-data HEX`). Once connected it records
what the console sends for `--listen` seconds and saves any Pokemon found in it, then stops with an
explanation, since the trade messages are not mapped yet. Received Pokemon go to `--output` as
`<time>_<game>_trade<N>_<species>_<nickname>_<PID>.pk6` (`.pk7` for Sun/Moon and USUM).

Each game reads its constants from `profiles/<game>.toml` (or an untracked `<game>.local.toml`),
filled in from captures.

## Layout

| Path | Contents |
|---|---|
| `pokeuds/radio/` | ESP32 serial protocol, ldnd pipe + nl80211 monitor setup, radiotap, a simulated air |
| `pokeuds/uds/` | 802.11 frames, beacons, CCMP and key derivation, SecureData, EAPoL, the station (host/client), the sniffer |
| `pokeuds/pkx.py`, `received.py` | Generation 6/7 records (crypto, checksum, summary, PK6/PK7 formats) and the output folder |
| `pokeuds/capture/` | Azahar-log and sniff transcripts, Pokemon finder |
| `pokeuds/games/` | Game registry, profile loader, the trade session (join and host); `gen6/` X/Y and ORAS, `gen7/` Sun/Moon and USUM, `vc.py` the VC titles |
| `pokeuds/vc/` | Virtual Console: Pia frames and session, the Game Boy trade engine, the end-to-end trade |
| `pokeuds/gbpk.py` | .pk1/.pk2 records and the Time Capsule conversions |
| `profiles/` | Per-game constants filled in from captures |
| `gui/`, `pokeuds/app/` | The app (Flet) and what it shares with the command line: settings, catalog, child runs, progress |
| `docs/` | [The app](docs/gui.md), [architecture](docs/architecture.md), [UDS protocol](docs/uds.md), [Virtual Console](docs/vc.md), [hardware](docs/hardware.md), [captures](docs/captures.md) |

```bash
python -m pytest -q
```

## Credits and licensing

The UDS layer is a Python port of the physical-UDS work in
[azahar_uds](https://github.com/Gr3nSkyDragon/azahar_uds) (itself built on Citra/Azahar's NWM
emulation, GPLv2 or later), so this project must stay GPL-compatible; no license file has been chosen
yet. Generation 6 record handling follows [PKHeX](https://github.com/kwsch/PKHeX). The Virtual Console
support is built on Gr3nSkyDragon's VC link measurements ([mgba_LDN](https://github.com/Gr3nSkyDragon/mgba_LDN)
`wiki/vc_link.md` and `wiki/gb_link.md`, and the retail-session frames used as test fixtures) and on
[pret](https://github.com/pret)'s pokered and pokecrystal, from which `scripts/gen_gb_tables.py`
generates the species, stat and item tables. The overall shape
(radio backends, received-file handling, docs per protocol) follows pokeldn. ESP32 firmware:
Gr3nSkyDragon's VC-Adapter. ldnd: unlimitedcoder2.
