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
| Trade | pick the game, who hosts, the Pokemon to offer (from the bank or a file), Start; the session panel follows the run: radio, 3DS connected, link up, Pokemon received, what happened, the log |
| Radio | ESP32-S3 (port found by its USB id, or chosen) or a USB Wi-Fi adapter through ldnd.exe 0.0.5 (Windows); "Check the radio" runs `probe` |
| Board | ESP32-S3 boards: their firmware and build, update, download the released esp32-uds-bridge images, install one or a chosen `.bin` (`board ...` children; docs/hardware.md) |
| Builder | a legal Pokemon built by PKHeX (game, species, level and wishes, the trainer), kept in the bank and offered in a trade (docs/pkhex.md) |
| Gifts | Gen 6/7 Mystery Gift cards: PKHeX's official events, a `.wc6`/`.wc7` (or full) file, or a custom card; the card's Pokemon into the bank and a trade, the card saved as a file (docs/mystery_gift.md) |
| Bank | every Pokemon the app keeps, received ones included: details, PKHeX legality, offer in a trade, Time Capsule copy, import, export, delete |
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
| `pokeuds/app/bank.py` | the bank folder |
| `pokeuds/app/builder.py` | the Builder's wishes and trainer |
| `gui/` | the Flet pages |

## The bank

The bank is a folder (`<data folder>/bank`, or Settings' `bank`) holding one file per Pokemon with a
`.json` beside it, as pokeldn keeps its bank (`pokeuds/app/bank.py`):

```
20261104-153012_025_Sparky_1a2b3c4d.pk6     the record: .pk1/.pk2 as PokeUDS writes them, .pk6/.pk7 decrypted and stored, as PKHeX opens them
20261104-153012_025_Sparky_1a2b3c4d.json    source (received, imported, built, converted), when, the file it came from, a note, PKHeX's verdict
20261104-153012_025_Sparky_1a2b3c4d.mail    a Gen 2 Pokemon's mail, when it holds one
```

- **Received Pokemon** still land in the output folder first (one file per trade, as before). Each time
  the Bank page opens, files there that it has not seen are deposited; one deleted from the bank is not
  brought back (`received.json` remembers what was seen).
- **One copy each:** a record whose content is already banked is not added again, whatever its file name.
- **Import** takes `.pk1`, `.pk2`, `.pk6`, `.pk7`, `.ek6` and `.ek7` (encrypted records are stored decrypted),
  with a `.pk2`'s `.mail` beside it.
- **Offer in a trade** lists the games that take the record (a `.pk1` or `.pk2` reaches both VC games,
  the Time Capsule converting it on the way; a `.pk6` X/Y and ORAS; a `.pk7` Sun/Moon and Ultra Sun/Ultra
  Moon) and sets it as that game's offer. On the Trade page, **From the bank** opens the bank showing only
  what the chosen game takes.
- **Time Capsule copy** banks the `.pk2` a Gen 1 Pokemon becomes in Gen 2, or the `.pk1` a Gen 2 one
  becomes in Gen 1 (a Gen 1 species with Gen 1 moves only; its item and mail stay behind).
- **Check** asks the PKHeX helper (docs/pkhex.md), when it is built, and keeps the answer with the record.

Levels of stored Gen 6/7 records (which carry no level byte) come from their experience and the species'
growth rate (`pokeuds/species.py`, taken from PKHeX.Core).

## The demo radio

Debug, Demo radio, picks a simulated 3DS (Red, Yellow, Gold, Gold in the Time Capsule, Crystal). Runs then
go to `--radio demo:<title>`: a modelled console hosting from the VC link menu, on a simulated air, through
the real UDS, Pia and Game Boy code. It is for trying the app and for development, and proves nothing
about a real console. It needs no console keys.

## Screenshots

`POKEUDS_GUI_WEB=8550 python -m gui` serves the app to a browser instead of opening a window.
