# PokeUDS

PokeUDS trades with retail Pokemon games on Nintendo 3DS over local wireless (UDS), the way
[pokeldn](https://github.com/Decryptu/pokeldn) does for Switch: a computer with an ESP32-S3 board or a
USB Wi-Fi adapter stands in for a second console. Nothing is installed on the 3DS.

Status: **foundation**. The radio backends and the UDS layer (scanning, hosting, joining, CCMP,
SecureData) work and are tested against each other in simulation; nothing has run on hardware yet.
The Pokemon X/Y trade messages that ride inside UDS are not mapped yet; they need captures
([docs/captures.md](docs/captures.md)).

| | X/Y | ORAS | Sun/Moon | USUM | VC Gen 1 | VC Gen 2 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| UDS session | ✓ (untested on hardware) | planned | planned | planned | planned | planned |
| Trade | needs captures | - | - | - | - | - |

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

```bash
python -m pokeuds probe --radio esp32
```

```bash
python -m pokeuds scan --radio esp32 --seconds 30
```

`scan` lists every 3DS UDS network in range with its comm id, decrypted player list and application
data; start the PSS on a console and it should appear. With a USB adapter use `--radio ldnd`.

```bash
python -m pokeuds analyze azahar_log.txt --extract captures/pokemon
```

```bash
python -m pokeuds sniff --radio esp32 --keys aes_keys.txt --game xy --out captures/xy_trade.jsonl
```

```bash
python -m pokeuds trade xy --radio esp32 --keys aes_keys.txt --offer my.pk6 --output received
```

`trade` loads and checks the offer, finds the console, joins its network and then stops with an
explanation, since the X/Y messages are not mapped yet; whatever the console sends is logged.
Received Pokemon go to `--output` as `<time>_xy_trade<N>_<species>_<nickname>_<PID>.pk6`.

## Layout

| Path | Contents |
|---|---|
| `pokeuds/radio/` | ESP32 serial protocol, ldnd pipe + nl80211 monitor setup, radiotap, a simulated air |
| `pokeuds/uds/` | 802.11 frames, beacons, CCMP and key derivation, SecureData, EAPoL, the station (host/client), the sniffer |
| `pokeuds/pk6.py`, `received.py` | Generation 6 records (crypto, checksum, summary) and the output folder |
| `pokeuds/capture/` | Azahar-log and sniff transcripts, Pokemon finder |
| `pokeuds/games/xy/` | X/Y profile and trade session |
| `profiles/` | Per-game constants filled in from captures |
| `docs/` | [Architecture](docs/architecture.md), [UDS protocol](docs/uds.md), [hardware](docs/hardware.md), [captures](docs/captures.md) |

```bash
python -m pytest -q
```

## Credits and licensing

The UDS layer is a Python port of the physical-UDS work in
[azahar_uds](https://github.com/Gr3nSkyDragon/azahar_uds) (itself built on Citra/Azahar's NWM
emulation, GPLv2 or later), so this project must stay GPL-compatible; no license file has been chosen
yet. Generation 6 record handling follows [PKHeX](https://github.com/kwsch/PKHeX). The overall shape
(radio backends, received-file handling, docs per protocol) follows pokeldn. ESP32 firmware:
Gr3nSkyDragon's VC-Adapter. ldnd: unlimitedcoder2.
