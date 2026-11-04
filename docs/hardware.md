# Hardware

## ESP32-S3

Flash [esp32-uds-bridge](https://github.com/Gr3nSkyDragon/VC-Adapter/tree/main/firmware/esp32-uds-bridge)
(the azahar_uds firmware flasher works, or esptool at address `0x0000`). Connect the board by its
**native** USB port, which shows up as Espressif USB Serial/JTAG (`303A:1001`); PokeUDS finds it by
that id, or pass `--radio esp32:COM4` / `--radio esp32:/dev/ttyACM0`.

```bash
python -m pokeuds probe --radio esp32
```

prints the firmware version. On Linux, add yourself to the `dialout` group if the port is refused.

The firmware is a dumb radio: it captures, injects, replays the beacon template and forwards frames
for our MAC and for one watched peer. PokeUDS starts it with a twin hardware address (as azahar_uds
does), because the chip does not pass unicast data for its own MAC to the capture path. The cost is
that our unicast frames are not hardware-ACKed, so the console retransmits some of them; the UDS layer
drops the duplicates.

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
