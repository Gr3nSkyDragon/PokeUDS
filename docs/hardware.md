# Hardware

## ESP32 boards

PokeUDS runs VC-Adapter's [esp32-uds-bridge](https://github.com/Gr3nSkyDragon/VC-Adapter/tree/main/firmware/esp32-uds-bridge)
unchanged, firmware **2.1-uds**, on five chips:

| chip | connect by | PokeUDS finds it by | state |
|---|---|---|---|
| ESP32-S3 | its **native** USB port (often marked USB, not UART) | Espressif USB Serial/JTAG, `303A:1001` | traded on with 1.8 and with 2.1-ldn (2.1-uds's twin with the Switch side); 2.1-uds itself not yet tried on a board |
| ESP32-C3, ESP32-C6 | its native USB port | `303A:1001` (the same id) | traded on (0.1.5): X/Y with the 3DS asking on both, and with PokeUDS asking (PokeUDS hosting) on the C6; other games not yet |
| ESP32 (classic) | its board's only USB port, a USB-UART bridge (CP210x `10C4:EA60`, CH340 `1A86:7523`...) | the bridge's id, after any native port | **alpha**, the same |
| ESP32-S2 (a Flipper Wi-Fi Developer Board, a Lolin S2 Mini...) | its only USB port, the chip's own | TinyUSB CDC, `303A:4001` (installed from its ROM's download mode, `303A:0002`) | traded on: Gen 6 on a Lolin S2 Mini with 2.1-uds (2026-11-20; no longer alpha after 0.2.2); a 2.0 build answered HELLO and scanned on a Flipper board; other games not yet tried |

Or pass `--radio esp32:COM4` / `--radio esp32:/dev/ttyACM0`.

```bash
python -m pokeuds probe --radio esp32
```

prints the firmware version. On Linux, add yourself to the `dialout` group if the port is refused.

**A classic ESP32** talks over its UART: PokeUDS opens the bridge at **921600** baud (the firmware's rate from boot;
native USB ignores the rate) and holds DTR and RTS low before it opens the port, since bridge boards wire them to
the chip's EN and GPIO0 (auto-reset): opening would otherwise reset the board. The boot ROM's 115200-baud banner
before the firmware starts is skipped as noise. At 921600 the link carries about 92 KB/s each way, enough for a
trade; whether it keeps up under a busy room is unmeasured. On an S3, C3 or C6 board with a second, UART port,
that port is not where the firmware listens: use the native USB one.

The firmware is a dumb radio: it captures, injects, replays the beacon template and forwards frames
for our MAC and for one watched peer. PokeUDS starts it with a twin hardware address (as azahar_uds
does), because the chip does not pass unicast data for its own MAC to the capture path. The cost is
that our unicast frames are not hardware-ACKed, so the console retransmits some of them; the UDS layer
drops the duplicates.

### ESP32-S2 (Flipper Wi-Fi Developer Board, S2 Mini)

Use a USB data cable. Hold BOOT, press and release RESET, then release BOOT. On the **Board** page,
choose **Look again** and then **Install 2.1-uds** on the ESP32-S2 download port (`303A:0002`).
After the installer finishes, press RESET if the board stays in download mode. Click **Look again**
and **Check** on the new runtime port. A successful check selects the runtime port for trades when
the app already uses an ESP32 connection.

This is a 3DS-only image. It boots straight into UDS and does not include VC-Adapter's Switch mode,
an OLED profile, or an on-board Game Boy wrapper. PokeUDS runs the Game Boy protocol on the computer. TinyUSB uses slave mode to work around
Espressif's [USB-105 DMA erratum](https://docs.espressif.com/projects/esp-chip-errata/en/latest/esp32s2/03-errata-description/esp32s2/usb-otg-abnormal-data-ahb-arbitration.html).
The S2 uses separate images for pokeldn and PokeUDS.

#### Build and verify

PokeUDS includes `esp32-uds-bridge-2.1-uds-esp32s2.bin`, pinned by its SHA-256 like the other chips'. From the
command line, with the board in download mode and its download port in place of `PORT`:

```sh
python -m pokeuds board flash --port PORT
```

The S2's firmware port does not take esptool's reset, so every install starts from download mode and ends with
RESET. Its USB serial number is TinyUSB's default, `123456`, on every S2, so PokeUDS does not use it to find the board
again (2.1-uds never restarts, so it has no need to).

With PokeUDS firmware installed and other serial users closed, run these from the PokeUDS checkout:

```sh
python -m pokeuds board list
python -m pokeuds board check --port PORT
python -m pokeuds scan --radio esp32:PORT --seconds 20
```

Replace `PORT` with the actual runtime port. On a Flipper Wi-Fi Developer Board (ESP32-S2
rev 0.0) with arm64 macOS, the contributor's standalone `2.0-uds` build answered USB HELLO before and
after a physical cable replug. Wi-Fi initialized and completed a 20-second scan on channels
1, 6, and 11. The driver counted 1,781 incoming Wi-Fi frames with no USB drops or malformed
USB frames. No 3DS hosts were heard, and the board answered a fresh HELLO after the scan.

Real 2DS/3DS discovery, encrypted communication, and retail trading remain untested.

#### Restore the previous firmware

Verify the checksum of your saved firmware, put the board back into download mode, and write
the saved merged pokeldn image at offset `0x0`. Replace `PORT` with the download port:

```sh
python -m esptool --chip esp32s2 --port PORT --before no-reset write-flash 0x0 /path/to/pokeldn-radio-s2.bin
```

A verified full stock flash backup can likewise be restored at `0x0`. Use the saved image for this
board and keep the backup outside source control. Press RESET after the write if needed.

### Which firmware

PokeUDS comes with the firmware it uses, as pokeldn does: `esp32-uds-bridge-2.1-uds-<chip>.bin` for `esp32s3`,
`esp32c3`, `esp32c6`, `esp32` and `esp32s2` (`pokeuds/app/data/firmware/`, each pinned by its SHA-256), built from VC-Adapter's
`firmware/esp32-uds-bridge`, where the source is for anyone who wants to build or study it. Nothing is downloaded:
a new PokeUDS (which updates itself) brings new images.

| firmware | what it is | with PokeUDS |
|---|---|---|
| 2.1-uds | the 3DS side alone, for PokeUDS and Azahar: the 3DS raw radio every generation trades over and the optional screen. It boots in 3DS mode, so connecting never restarts it | **included**: the one to install |
| 2.1-ldn, 2.0-uds (was 1.8, the GB-Link build) | the 3DS side and GB-Link Switch LDN's Switch side, for mGBA-LDN | work, restarting into 3DS mode when PokeUDS connects; the Board page offers 2.1-uds as optional (keep it if mGBA-LDN uses the board's Switch side) |
| 1.8, 1.7 and older | the earlier S3 builds | work; the Board page offers 2.1-uds |
| 2.1-aio, 2.0-aio (was 1.9, the standalone build) | VC-Adapter's standalone firmware, for that board on a console's link port | not for a board on a computer; the Board page offers 2.1-uds in its place |

**The names changed in 2.1**: 2.0's uds line had both sides and is called ldn from 2.1, and 2.1-uds is the 3DS side
alone. The Board page names a 2.0-uds board "uds line, 3DS and Switch sides (2.1 calls it ldn)".

Nothing in the firmware needs changing for PokeUDS: it only uses the raw radio (START with a twin
address, TX_FRAME, SET_BEACON, SET_WATCH, PING), never the board-side Game Boy wrapper (`GB_*`) or the
key store (`SET_KEY`), since CCMP runs on the PC with the key from your `aes_keys.txt`. 2.0 speaks the same
protocol as 1.8.

Two things PokeUDS handles since 1.8:

- **Two modes.** The firmware boots in Switch mode (GB-Link Switch LDN's bridge) until a HELLO from this protocol
  restarts it in 3DS mode, and remembers the mode in flash. The first connection after a Switch session
  therefore drops while the board restarts; PokeUDS closes the port, finds the board again by its USB serial
  number (the chip's MAC: macOS can bring the port back under another `/dev/cu.usbmodem...` name, even when the
  port was chosen by hand) and repeats HELLO for up to 25 s. It never moves to another board this way.
  2.1-uds has no Switch side and never restarts.
- **The features byte.** HELLO_ACK has a tenth byte from 1.8 on; bit 0 marks the standalone board's line
  (aio, 1.9 before it), and bit 2 (2.1 on) an image with no Switch side (uds and aio). `probe` and the Board
  page name the line.

**The screen.** A 128x64 SSD1306 I2C OLED (on the S3: SDA GPIO8, SCL GPIO9, 3V3, GND; connect it before powering
the board; the other chips' pins are in VC-Adapter's `FIRMWARE.md`) shows the firmware, whether a host has said
HELLO, and the raw radio's channel and frame counts while PokeUDS drives it, as it does for Azahar. PokeUDS sends
nothing for it. It dims after a minute idle and turns off after ten; BOOT wakes it.

### Installing the firmware

The app's **Board** page lists boards, reads their firmware and installs the included 2.1-uds. The same
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

`flash` asks the board which chip it is (esptool) and takes that chip's included image (checked against its
SHA-256; `--firmware FILE.bin` writes another image, for firmware development). It checks the image is a merged
image for that chip (its bootloader with the chip's id, at 0x0, or at 0x1000 inside a classic ESP32's image, and
esptool's checksum and digest) and writes it at `0x0` with esptool 5.6.0 (`pip install -r requirements-gui.txt`),
at 460800 baud. A chip esp32-uds-bridge has no build for (an ESP32-H2, for example) is refused by name. Writing at 0x0
erases the keys a board stores; PokeUDS stores none there, Azahar users store theirs again. If the board is not
detected, hold **BOOT** while plugging it in, then release it.

### Still to check on a board (classic ESP32; C3 and C6 beyond X/Y)

From VC-Adapter's `FIRMWARE.md`: that each chip's Wi-Fi library still lets the bridge's raw frames out, that the
twin-MAC receive trick works on the C6, the beacon's steadiness on a single core (C3, C6), the classic ESP32's UART
throughput during a trade, and then a scan, a Gen 6 and a Gen 7 trade on each.

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
