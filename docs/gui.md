# The app

```bash
pip install -r requirements-gui.txt
```

```bash
python -m gui
```

A desktop app (Flet 1.0.5, the toolkit pokeldn uses) over the same code as the command line.

![The Trade page after a demo trade](assets/gui_trade.png)

## Pages

| page | what it does |
|---|---|
| Trade | pick the game, who hosts, the Pokemon to offer, Start; the session panel follows the run: radio, 3DS connected, link up, Pokemon received, what happened, the log |
| Radio | ESP32-S3 (port found by its USB id, or chosen) or a USB Wi-Fi adapter through ldnd.exe 0.0.5 (Windows); "Check the radio" runs `probe` |
| Board | ESP32-S3 boards: their firmware and build, update, download the released esp32-uds-bridge images, install one or a chosen `.bin` (`board ...` children; docs/hardware.md) |
| Received | the Pokemon in the output folder, newest first |
| Settings | `aes_keys.txt`, the output folder, the trainer name the 3DS sees, Debug tools |
| Debug | only with Settings, Debug tools on: the demo radio, a network scan, capture analysis |

The Game Boy titles trade completely. X/Y, ORAS, Sun/Moon and Ultra Sun/Ultra Moon are marked alpha: a
run joins or hosts and records what the 3DS sends, and Start stays off until the game's profile has
the values captures provide (`profiles/<game>.toml`).

## How it runs a trade

As in pokeldn, the app never opens the radio itself. Start runs the command line as a child process
(`python -m pokeuds vc trade ...`, or `trade xy ...`) with `POKEUDS_STATUS=1`, and reads two kinds of
line from it: the plain log, and `[status] {...}` events (`pokeuds/status.py`) that drive the session
panel. Stop closes the child's stdin, which the child turns into a clean shutdown (it leaves the
session and closes the radio); a child that does not end is terminated after a few seconds. One run
at a time, since one radio serves one process.

| module | role |
|---|---|
| `pokeuds/app/settings.py` | settings in the platform data folder (`POKEUDS_DATA` overrides it) |
| `pokeuds/app/catalog.py` | the games the app lists, what each accepts, the command it runs, what blocks Start |
| `pokeuds/app/runner.py` | the child process |
| `pokeuds/app/progress.py` | events to headline, steps and notes |
| `pokeuds/app/board.py` | boards, firmware images, download and flashing |
| `gui/` | the Flet pages |

## The demo radio

Debug, Demo radio, picks a simulated 3DS (Red, Yellow, Gold, Gold in the Time Capsule, Crystal). Runs then
go to `--radio demo:<title>`: a modelled console hosting from the VC link menu, on a simulated air, through
the real UDS, Pia and Game Boy code. It is for trying the app and for development, and proves nothing
about a real console. It needs no console keys.

## Screenshots

`POKEUDS_GUI_WEB=8550 python -m gui` serves the app to a browser instead of opening a window.
