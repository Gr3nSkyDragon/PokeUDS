# Hardware

## ESP32-S3

PokeUDS runs VC-Adapter's [esp32-uds-bridge](https://github.com/Gr3nSkyDragon/VC-Adapter/tree/main/firmware/esp32-uds-bridge)
unchanged. Connect the board by its **native** USB port, which shows up as Espressif USB Serial/JTAG
(`303A:1001`); PokeUDS finds it by that id, or pass `--radio esp32:COM4` / `--radio esp32:/dev/ttyACM0`.

```bash
python -m pokeuds probe --radio esp32
```

prints the firmware version. On Linux, add yourself to the `dialout` group if the port is refused.

The firmware is a dumb radio: it captures, injects, replays the beacon template and forwards frames
for our MAC and for one watched peer. PokeUDS starts it with a twin hardware address (as azahar_uds
does), because the chip does not pass unicast data for its own MAC to the capture path. The cost is
that our unicast frames are not hardware-ACKed, so the console retransmits some of them; the UDS layer
drops the duplicates.

### Which firmware

PokeUDS comes with the firmware it uses, as pokeldn does: `esp32-uds-bridge-fw1.8-esp32s3-gblink.bin`
(`pokeuds/app/data/firmware/`, from VC-Adapter's release `POC_v2`, pinned by its SHA-256). Nothing is
downloaded.

| firmware | what it is | with PokeUDS |
|---|---|---|
| 1.8, GB-Link build | the build for a board on a computer (Azahar, mGBA, PokeUDS): the 3DS raw radio every generation trades over, the optional screen, and a Switch side PokeUDS does not use | **included**: the one to install |
| 1.7 and older | the 3DS side alone | works; the Board page offers 1.8 |
| 2.0 (and its 1.9 beta) | VC-Adapter's standalone firmware, for that board on a console's link port | not for a PC-hosted board; the Board page offers 1.8 in its place |

Nothing in the firmware needs changing for PokeUDS: it only uses the raw radio (START with a twin
address, TX_FRAME, SET_BEACON, SET_WATCH, PING), never the board-side Game Boy wrapper (`GB_*`) or the
key store (`SET_KEY`), since CCMP runs on the PC with the key from your `aes_keys.txt`.

Two things PokeUDS handles for 1.8:

- **Two modes.** 1.8 boots in Switch mode (GB-Link Switch LDN's bridge) until a HELLO from this protocol
  restarts it in 3DS mode, and remembers the mode in flash. The first connection after a Switch session
  therefore drops while the board restarts; PokeUDS closes the port, finds the board again (its port can
  come back under another name) and repeats HELLO for up to 25 s.
- **The features byte.** HELLO_ACK has a tenth byte from 1.8 on; bit 0 marks the VC-Adapter board build.
  `probe` and the Board page name the build.

**The screen.** A 128x64 SSD1306 I2C OLED (SDA GPIO8, SCL GPIO9, 3V3, GND; connect it before powering
the board) shows the firmware, whether a host has said HELLO, and the raw radio's channel and frame
counts while PokeUDS drives it, as it does for Azahar. PokeUDS sends nothing for it. It dims after a
minute idle and turns off after ten; BOOT wakes it.

### Installing the firmware

The app's **Board** page lists boards, reads their firmware and installs the included 1.8. The same
steps from the command line:

```bash
python -m pokeuds board list
```

```bash
python -m pokeuds board check
```

```bash
python -m pokeuds board flash --port COM4
```

`flash` takes the included 1.8 image (checked against its SHA-256; `--firmware FILE.bin` writes another image,
for firmware development), checks it is a merged
ESP32-S3 image (its bootloader at 0x0 with the S3's chip id, esptool's checksum and digest) and writes it
at `0x0` with esptool 5.6.0 (`pip install -r requirements-gui.txt`), at 460800 baud. Writing at 0x0
erases the keys a board stores; PokeUDS stores none there, Azahar users store theirs again. If the board
is not detected, hold **BOOT** while plugging it in, then release it. A USB-UART port (CP210x, CH340)
can be flashed through, but trades need the native USB port.

## USB Wi-Fi adapter (Windows)

Use the legacy **ldnd.exe 0.0.5** from unlimitedcoder2's gist and follow its adapter setup (it binds
the adapter to WinUSB and runs the Linux driver for it). The newer ldnrs build speaks a different
protocol and does not work. Start ldnd.exe, wait until it says it is ready, then:

```bash
python -m pokeuds probe --radio ldnd
```

PokeUDS removes any stale `udsmon0`/`udsap0` interface, creates an **active** monitor interface
`udsmon0` with its own MAC (so the adapter ACKs frames sent to us), brings it up and tunes it. If the
driver refuses active monitor mode it falls back to a passive monitor and logs a warning; in that case
the console will retransmit to us and may give up on joining. azahar_uds solves this on such adapters
with a companion AP interface used as an "ACK shell"; that is not ported yet and will be if active
monitor mode turns out not to be enough on the adapters we test.

azahar_uds was tested with a generic AC1300 adapter.

## Keys

`aes_keys.txt`, the file Azahar reads, with the keyslot 0x2D key. Keys are console secrets: keep the
file outside the repository (`.gitignore` already excludes `aes_keys.txt`).
