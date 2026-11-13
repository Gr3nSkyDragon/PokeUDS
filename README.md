<img src="gui/assets/icon.png" alt="PokeUDS" width="96" align="right">

# PokeUDS

PokeUDS trades with retail Pokemon games on a Nintendo 3DS over local wireless (UDS), the way
[pokeldn](https://github.com/Decryptu/pokeldn) does for the Switch. A computer with an ESP32 board (S3, C3, C6; alpha: classic) or a USB Wi-Fi
adapter stands in for a second console: the 3DS sees another player and trades with it. Nothing is installed on the
3DS. It can also send Gen 6 and Gen 7 Mystery Gifts over local wireless, the way an event distribution did.

**Status: alpha.** Trades have run against retail 3DS consoles for the Virtual Console games, Pokemon X/Y and Sun/Moon,
and a Gen 7 local-wireless Mystery Gift has been received by a retail Sun.

| Game | Trades start in | Record | PokeUDS joins | PokeUDS hosts | Tested with a 3DS |
|---|---|---|:---:|:---:|---|
| Red / Blue / Yellow (VC) | VC link menu, Trade Center | `.pk1` (or `.pk2` through the Time Capsule) | ✓ | ✓ experimental | ✓ Gen 1 trades |
| Gold / Silver / Crystal (VC) | VC link menu, Trade Center or Time Capsule | `.pk2` with mail (or `.pk1`) | ✓ | ✓ experimental | ✓ Gen 2 trades |
| X / Y | PSS | `.pk6` | ✓ | ✓ | ✓ the 3DS asking |
| Omega Ruby / Alpha Sapphire | PSS | `.pk6` | ✓ | ✓ | uses X/Y's values |
| Sun / Moon | Festival Plaza | `.pk7` | ✓ | ✓ | ✓ both directions |
| Ultra Sun / Ultra Moon | Festival Plaza | `.pk7` | ✓ | ✓ | uses Sun/Moon's values |

The VC rows cover every release: English, German, French, Spanish, Italian, Japanese (Green too) and Korean Gold
and Silver, each with its own trade blocks, and a Pokemon from one language is offered to a console of another with
its names carried over ([docs/vc.md](docs/vc.md), Languages). Only the English games have been tried with a 3DS so
far; the others are built from the cartridges' layouts and wait for a console to confirm them.

Local trades never cross a generation (Sun/Moon and USUM trade with each other, as do X/Y and ORAS); Gen 1 and Gen 2
meet in the Time Capsule. To move a Pokemon forward, the Bank migrates Gen 1/2 and Gen 6 Pokemon to Gen 7 the way
Poke Transporter and Pokemon Bank do.

## What it does

- **Trade** with the 3DS asking PokeUDS or PokeUDS asking the 3DS, one Pokemon or a queue of them in one session
  (up to six for the Game Boy games, a box's worth and more for the 3DS games).
- **Bank** every Pokemon received, imported or built: legality checks with PKHeX (one or the whole bank), Time
  Capsule copies, migration to Gen 7, duplicates caught, sprites.
- **Build** a legal Pokemon to offer when you have no file (PKHeX.Core, built into the app), with the IVs and EVs
  (DVs and stat experience in Gen 1/2) and Hidden Power you want, or import a Showdown or Smogon set (a whole team
  too) and build from it.
- **Mystery Gift**: PKHeX's official Gen 6/7 cards, `.wc6`/`.wc7` and full files, and a card editor (every field,
  saved as `.udsgift`, exported as `.wc6`/`.wc7`). A card's Pokemon can go into the bank and a trade, or the card
  itself goes out over local wireless as an event distribution did:
  - **Gen 6** (X/Y, ORAS): no extra key; the gift is CRC-checked only.
  - **Gen 7** (Sun/Moon, USUM): the game checks the distributor's OPSSR signature, so PokeUDS signs each card with
    the distributor app's private key. That key is not shipped: the **Gen 7 Mystery Gift Key** card in Settings
    reads it from your own dump of the Gen 7 Local Distributor (00040000001A8900) and keeps it in `rsa_d_key.txt`
    beside `aes_keys.txt`. Retail game dumps hold only the public key.
- **DS Wi-Fi Mystery Gifts (Gen 4/5), pre-alpha**: a DNS and Nintendo WFC stand-in for Diamond, Pearl, Platinum,
  HeartGold, SoulSilver, Black, White, Black 2 and White 2. The player points the DS's DNS at PokeUDS; it answers
  the connection test, the NAS login and the DLS1 download over the DS's SSL 3.0, with a server certificate made
  from the player's own Wii certificate (the "nds-constrain't" flaw), and sends PKHeX's official cards. Needs a Wii
  certificate dump and either an open (or WEP) access point or the board's own open AP
  ([docs/ds_wifi_ap.md](docs/ds_wifi_ap.md)); `tlslite-ng` for the DS's SSL; **not yet tried on a DS**
  ([docs/ds_wifi.md](docs/ds_wifi.md)).
- **GTS**: list a banked Pokemon against what you want, or offer on someone else's listing, bank to bank over
  public Nostr relays; a trade settles when the lister's app next opens ([docs/online.md](docs/online.md)). New and
  not yet tried over the public relays.
- **Board**: install the ESP32 firmware (included, nothing to download) from the app.
- **Plugins and language packs**, with an install screen that shows what a plugin's code does.

## Getting it

Download the app for your computer from the [releases](https://github.com/Gr3nSkyDragon/PokeUDS/releases). The
apps carry everything, PKHeX included: nothing else to install or download.

**Windows:** download `PokeUDS-windows.zip`, unzip it anywhere you can write (Documents, the Desktop, a USB
drive) and run `PokeUDS/PokeUDS.exe`.

Everything the app keeps stays in its own folder: your Pokemon in `PokeUDS/Bank`, received files in
`PokeUDS/received`, and its settings, sprites and plugins in `PokeUDS/Data` (hidden). Nothing is written to your
user profile, and the app will not write outside its folder: if it cannot write there, it says so and asks you to
move the folder. An earlier version's files in `%APPDATA%\PokeUDS` are copied over on the first start and left
where they were. The zip holds none of these folders, so to update, unzip the new version over the old `PokeUDS`
folder: the app is replaced and your `Bank`, `Data` and `received` are left as they are. If you delete the old
folder instead, move them into the new one first. Two copies of the folder are two separate players (each with its
own bank, settings and GTS listings).

**Mac:** download `PokeUDS-macos-arm64.zip` for Apple Silicon (M1 or newer) or `PokeUDS-macos-x64.zip` for an Intel
Mac (Apple menu, About This Mac, says which), unzip it and move `PokeUDS.app` to Applications. The app is not
notarized by Apple, so the first time it is refused: open it once, then System Settings, Privacy & Security,
**Open Anyway**. Your Pokemon are kept in `~/Library/Application Support/PokeUDS/bank`, outside the app, so replacing
`PokeUDS.app` with a newer one leaves them alone (Settings can point the bank somewhere else).

**Linux:** there is no packaged app; run PokeUDS from source, below.

**From source** (any OS, Python 3.11 or newer):

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -r requirements-gui.txt
```

```bash
python -m gui
```

On macOS or Linux, activate with `. .venv/bin/activate`. `requirements.txt` alone is enough for the command line.

## What you need

- **A radio** ([docs/hardware.md](docs/hardware.md)):
  - an **ESP32-S3** with [esp32-uds-bridge](https://github.com/Gr3nSkyDragon/VC-Adapter/tree/main/firmware/esp32-uds-bridge)
    firmware 2.0-uds, on its native USB port (PokeUDS includes it: the Board page installs it), or an ESP32-C3 or
    C6 the same way; **alpha**: a classic ESP32 ([docs/hardware.md](docs/hardware.md)); any OS; or
  - a **USB Wi-Fi adapter** through legacy **ldnd.exe 0.0.5** (Windows; not the ldnrs revision).
- **`aes_keys.txt`** holding the keyslot 0x2D key (`slot0x2DKeyN`, or `slot0x2DKeyX`, `slot0x2DKeyY` and
  `generatorConstant`): the same file Azahar uses. Set it once in Settings. Only scanning works without it.
- **For Gen 7 Mystery Gift distribution only:** a decrypted dump of the Gen 7 Local Distributor, as a CXI, a CCI
  (`.cci`/`.3ds`) or its `code.bin`, from which Settings extracts the signing key once
  ([docs/mystery_gift.md](docs/mystery_gift.md)).

## Trading from the app

1. Settings: choose `aes_keys.txt`, and set your trainer (name, TID/SID) for Pokemon you build.
2. Trade: pick the game and the connection (ESP32 board or USB Wi-Fi adapter).
3. Add Pokemon to the trade queue: the **+** row picks from the bank, or Build one (or Import paste), or Add a file.
4. Pick who asks, then Start, and follow the steps the page shows on the 3DS:
   - **VC games**: choose Host (or Join) in the VC link menu, then sit at the Trade Center table.
   - **X/Y, ORAS**: open the PSS with local wireless on, then ask PokeUDS for a trade (or accept its request).
   - **Sun/Moon, USUM**: open the Festival Plaza, then invite PokeUDS to a Link Trade (or accept its invite).

What the 3DS sends lands in the bank. See [docs/gui.md](docs/gui.md) for every page.

## Sending a Mystery Gift

1. Gifts: pick an official card (PKHeX's list), open a `.wc6`/`.wc7`/full file, or make one in the editor.
2. For Sun/Moon or USUM, set up the Gen 7 Mystery Gift Key in Settings first (once).
3. Send over local wireless, then on the 3DS: Mystery Gift, Receive Gift, Get via Local Wireless.

## The command line

```bash
python -m pokeuds scan --radio esp32 --seconds 30
```

```bash
python -m pokeuds trade sm --radio esp32 --offer my.pk7
```

```bash
python -m pokeuds trade xy --role host --radio esp32 --offer my.pk6 --partner Serena
```

```bash
python -m pokeuds vc trade --radio esp32 --offer my.pk1
```

```bash
python -m pokeuds gift distribute sm my.wc7 --radio esp32
```

`--offer` repeats for a queue; `--keys` defaults to the app's Settings; `-v` (before the command) shows what happens.
`scan` lists every 3DS network in range, `analyze` decodes an Azahar log or a capture, `build` makes a Pokemon with
PKHeX, `gift` lists, edits, builds and distributes Mystery Gift cards (`--gen7-key` points at `rsa_d_key.txt` if it
is not beside `aes_keys.txt`), `board` manages the ESP32, `pkhex` builds PKHeX in a source tree, `plugins` manages plugins.
`python scripts/extract_gen7_key.py DISTRIBUTOR.cxi --keys aes_keys.txt` (or a `.cci`/`.3ds`, or `code.bin`) is the
Settings key extraction from a terminal. Each game's protocol is in
[docs/vc.md](docs/vc.md), [docs/gen6.md](docs/gen6.md) and [docs/gen7.md](docs/gen7.md).

## Layout

| Path | Contents |
|---|---|
| `pokeuds/radio/` | The ESP32 serial protocol, the ldnd pipe and nl80211 monitor, radiotap, a simulated air |
| `pokeuds/uds/` | 802.11 frames, beacons, CCMP and key derivation, SecureData, EAPoL, the station (host and client), the sniffer |
| `pokeuds/nex/` | NEX's PRUDP V1 as Gen 6/7 run it over Pia's local UDS layer |
| `pokeuds/games/` | Game registry and profiles; `gen6/` the PSS and X/Y-ORAS trade, `gen7/` the Festival Plaza and Sun/Moon-USUM trade |
| `pokeuds/vc/` | Virtual Console: Pia frames and session, the Game Boy trade engine |
| `pokeuds/online/` | Nostr signing and relays, and the GTS's events ([docs/online.md](docs/online.md)) |
| `pokeuds/pkx.py`, `gbpk.py` | Gen 6/7 and Gen 1/2 records, the Time Capsule conversions |
| `pokeuds/capture/` | Azahar-log and sniff transcripts, the protocol decoders behind `analyze` |
| `pokeuds/games/gen6/delivery.py`, `gen7/delivery.py` | Local-wireless Mystery Gift: the beacon fragments, LZ10, Gen 7's OPSSR signing |
| `gui/`, `pokeuds/app/` | The app (Flet) and what it shares with the command line; `gui/assets/` holds the icon |
| `packaging/`, `scripts/build_exe.py` | The Windows exe's entry point, the icon artwork and the build ([docs/release.md](docs/release.md)) |
| `services/pkhex/` | PKHeX's side (a .NET program the app runs): building and checking Pokemon, Mystery Gift cards, migration |
| `profiles/` | Each game's network constants, from captures |
| `docs/` | [The app](docs/gui.md), [architecture](docs/architecture.md), [UDS](docs/uds.md), [Gen 6](docs/gen6.md), [Gen 7](docs/gen7.md), [Virtual Console](docs/vc.md), [hardware](docs/hardware.md), [captures](docs/captures.md), [PKHeX](docs/pkhex.md), [online and the GTS](docs/online.md), [Mystery Gift](docs/mystery_gift.md), [Wonder Cards](docs/wonder_cards.md), [plugins](docs/plugins.md), [releases](docs/release.md), [code signing](docs/code_signing.md) |

```bash
python -m pytest -q
```

## Credits

PokeUDS follows [pokeldn](https://github.com/Decryptu/pokeldn) by [Decryptu](https://github.com/Decryptu), which
does the same for the Switch: the app's shape and design, its PKHeX integration and updater, and code taken from it
under the AGPL-3.0. The 3DS protocols were read off logs of retail trades; AI tools helped reverse engineer them and
write parts of the code.

Contributors:

- [Gr3nSkyDragon](https://github.com/Gr3nSkyDragon): PokeUDS, and its testing on retail hardware
- **DeepSeek Harness** (DeepSeek): the Gen 7 Mystery Gift support: the distributor's beacon and OPSSR signing,
  the key extraction and the Settings flow ([CONTRIBUTORS.md](CONTRIBUTORS.md))

Local wireless and the radio:

- [azahar_uds](https://github.com/Gr3nSkyDragon/azahar_uds): the physical-UDS work the UDS layer is a Python port
  of, built on [Azahar](https://github.com/azahar-emu/azahar)'s (and Citra's) NWM emulation (GPLv2 or later)
- [VC-Adapter](https://github.com/Gr3nSkyDragon/VC-Adapter): the
  [esp32-uds-bridge](https://github.com/Gr3nSkyDragon/VC-Adapter/tree/main/firmware/esp32-uds-bridge) firmware the
  Board page installs
- [unlimitedcoder2](https://github.com/unlimitedcoder2): ldnd, the Windows USB Wi-Fi adapter daemon
- [martin-ger/esp32_nat_router](https://github.com/martin-ger/esp32_nat_router): the AP+STA+NAPT pattern behind
  the board's DS Wi-Fi access point, and a stand-in for it

Pokemon and the games:

- [PKHeX](https://github.com/kwsch/PKHeX) by [kwsch](https://github.com/kwsch): Pokemon records, legality,
  Mystery Gift cards, migration and Showdown sets (GPL-3.0)
- [pret](https://github.com/pret): the [pokered](https://github.com/pret/pokered) and
  [pokecrystal](https://github.com/pret/pokecrystal) decompilations behind the Game Boy link and the Time Capsule
- [mgba_LDN](https://github.com/Gr3nSkyDragon/mgba_LDN): the Virtual Console link measurements
- [Lorenzooone/PokemonGB_Online_Trades](https://github.com/Lorenzooone/PokemonGB_Online_Trades): the Japanese and
  Korean Game Boy games' text tables and layouts (MIT)
- [Project Pokemon](https://github.com/projectpokemon): theSLAYER's map of the local wireless event header
- [PokeAPI/sprites](https://github.com/PokeAPI/sprites): the sprites the app downloads

DS Wi-Fi Mystery Gifts:

- [barronwaffles/dwc_network_server_emulator](https://github.com/barronwaffles/dwc_network_server_emulator): the
  Nintendo WFC connection test, NAS login and DLS1 protocols (AGPL-3.0)
- [KaeruTeam/nds-constraint](https://github.com/KaeruTeam/nds-constraint): the DS's certificate check and the
  "nds-constrain't" flaw
- [kuroppoi/entralinked](https://github.com/kuroppoi/entralinked): protocol facts for the Gen 5 games' services
- [ScarlettHaze/PokeGateway](https://github.com/ScarlettHaze/PokeGateway): protocol facts for the Gen 4/5 games'
  services (no license; read for facts, no code copied)
- [InternalLoss/DLS1-Files](https://github.com/InternalLoss/DLS1-Files): the collection of official DLS1 files
- [Dolphin](https://github.com/dolphin-emu/dolphin): the certificate hash check follows its `IOS/Network/SSL.cpp`

Online:

- [Nostr](https://github.com/nostr-protocol/nips): the protocol the GTS runs on, over public relays

Libraries: [Flet](https://github.com/flet-dev/flet) (the app), [esptool](https://github.com/espressif/esptool)
(flashing the board), [PyCryptodome](https://github.com/Legrandin/pycryptodome),
[pySerial](https://github.com/pyserial/pyserial) and [tlslite-ng](https://github.com/tlsfuzzer/tlslite-ng)
(the DS's SSL 3.0, LGPL-2.1).

## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by
[SignPath Foundation](https://signpath.org). What is signed, the team roles and the privacy policy are on the
[Code signing policy](docs/code_signing.md) page.

## License

PokeUDS is free software under the **GNU Affero General Public License v3.0** ([LICENSE](LICENSE)). The license
covers this repository's code and nothing else. Anyone who passes on PokeUDS, changed or not, or runs a changed copy
as a service for others, must offer its source under the same license. Code taken from pokeldn (AGPL-3.0) or from
MIT projects keeps its original notice; PKHeX (GPL-3.0) and the GPLv2-or-later Azahar work are compatible with it.

PokeUDS is an unofficial fan project. It is not affiliated with, endorsed by or sponsored by Nintendo, The Pokemon
Company, Game Freak or Creatures. Pokemon, Nintendo 3DS, Nintendo DS and the related names and characters are
trademarks of their owners. Under section 7(e) of the AGPL, no right to use those trademarks or any other Pokemon
intellectual property is granted.

The authors do not endorse using PokeUDS for commercial, promotional or branded events, including Mystery Gift
distributions. Anyone who does so is responsible for obtaining the rights it requires.
