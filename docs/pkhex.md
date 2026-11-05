# The PKHeX helper

PokeUDS builds and checks Pokemon with [PKHeX.Core](https://github.com/kwsch/PKHeX) (26.10.18, GPL-3.0),
so a trade does not need a `.pk*` file made elsewhere: pick a species, a level and a few wishes, and the
helper returns a record PKHeX's legality checker accepts.

PKHeX.Core is a .NET library, so the helper is a small .NET program (`services/pkhex`) that Python runs
as one long-lived child process. Without it, everything else in PokeUDS works; building Pokemon, legality
checks and Mystery Gift cards are what need it.

## Getting the helper

| way | needs | where it goes |
|---|---|---|
| **Get the PKHeX helper** (the app: Settings, or any page that needs it), or `python -m pokeuds pkhex get` | nothing: the self-contained executable for this computer, from PokeUDS's GitHub releases, checked against the release's `SHA256SUMS` and started once before it is kept | `<data>/pkhex/` |
| `python scripts/publish_pkhex.py` | the .NET 10 SDK, once | `services/pkhex/dist/` (self-contained, as the release's) |
| `dotnet build -c Release services/pkhex`, the app's **Build it with .NET**, or `python -m pokeuds pkhex build` | the .NET 10 SDK, to build and to run | `services/pkhex/bin/Release/net10.0/` |

The newest one found is used; `POKEUDS_PKHEX` points at one elsewhere and wins. `python -m pokeuds pkhex status`
says which one, or what is missing: no .NET SDK, or one older than .NET 10 (an older SDK fails with
`NETSDK1045: The current .NET SDK does not support targeting .NET 10.0`; the app says so before trying).

The self-contained helper is one file of 16 to 19 MB with the .NET runtime inside, as pokeldn ships its own:
published single-file, compressed and trimmed. PKHeX.Core, the C# runtime binder and the helper itself are kept
whole, since the card editor reaches card fields through `dynamic` and reflection that full trimming would cut
(`services/pkhex/pokeuds-pkhex.csproj`). The published file passes the whole test suite and every official card
(`scripts/check_gifts.py`) with no .NET installed.

### Releases

`.github/workflows/release.yml` publishes the helper for Windows (x64, arm64), Linux (x64, arm64) and macOS
(Apple Silicon, Intel) and attaches them, with `SHA256SUMS`, to a GitHub release: push a tag like `v0.2.0`, or
run the workflow from the Actions tab with a tag name. The macOS files are published on a Mac runner, where the
SDK signs them (Apple Silicon refuses unsigned programs). The app's Get finds the newest release carrying an
asset for this computer (`pokeuds-pkhex-<rid>`, e.g. `pokeuds-pkhex-win-x64.exe`). `.github/workflows/check.yml`
runs the tests, the PKHeX ones included, on every push.

## Games

| key | games | record | written as |
|---|---|---|---|
| `rby` | Red, Blue, Yellow (VC) | PK1 | `.pk1`, 69 bytes (PKHeX's list layout, as `pokeuds vc` reads) |
| `gsc` | Gold, Silver, Crystal (VC) | PK2 | `.pk2`, 73 bytes |
| `xy` | X, Y | PK6 | `.pk6`, 260 bytes decrypted (party) |
| `oras` | Omega Ruby, Alpha Sapphire | PK6 | `.pk6` |
| `sm` | Sun, Moon | PK7 | `.pk7` |
| `usum` | Ultra Sun, Ultra Moon | PK7 | `.pk7` |

## Protocol

One JSON object per line each way. Every reply has `ok`; a refused request has `error` instead.

| request | reply |
|---|---|
| `{"cmd":"ping"}` | `{"ok":true,"pkhex":"26.10.18.0"}` |
| `{"cmd":"lists","game":"xy"}` | the game's `species`, `moves`, `items`, `balls`, `natures`, `languages` as `[value, name]` lists |
| `{"cmd":"build","game":"xy","species":25, ...wishes}` | a built record (below) |
| `{"cmd":"check","game":"xy","data":"<base64>"}` | the same, for a record you have |
| `{"cmd":"gifts","game":"oras"}` | the official Mystery Gift cards PKHeX.Core embeds for the game (Gen 6/7; `"all":true` for every card with its bytes) |
| `{"cmd":"gift","game":"oras","key":"0547-384-0"}` or `"data":"<base64>"` | a card (official, or a `.wc6`/`.wc7`/full file) and the Pokemon it gives the trainer |
| `{"cmd":"card","game":"usum","species":778, ...}` | a card from the editor's fields and its Pokemon (docs/wonder_cards.md) |
| `{"cmd":"card_fields","game":"usum","data":"<base64>"}` | any card as the editor's fields, which `card` turns back into the same bytes |
| `{"cmd":"migrate","game":"sm","from":"rby","source":"red","data":"<base64>","trainer":{...}}` | a Gen 1/2/6 record moved to Gen 7 (below), described as `check` describes one |

Wishes, all optional: `level`, `form`, `shiny`, `nature` (0 to 24), `gender` (`male`, `female`),
`ability` (`1`, `2`, `hidden`), `moves` (up to four move ids), `held_item`, `ball`, `nickname`, and
`trainer` `{ot, tid, sid, language, gender}` (the app fills it from Settings). Fields a generation does
not have (natures and balls before Gen 3, items in Gen 1) are ignored.

A built or checked record comes back as `legal`, `report` (PKHeX's legality report), `encounter` (the
encounter it came from), `extension`, `data` (base64, decrypted) and `summary` (species, nickname, level,
shiny, gender, OT, TID, move names, IVs, item, nature, ability, ball, origin).

## Migrating to Gen 7

`migrate` uses PKHeX.Core's converters, which copy what Nintendo's apps do (`services/pkhex/Migrate.cs`):

- **Gen 1/2 (`from` rby/gsc) -> Gen 7: Poke Transporter.** Met in "Kanto"/"Johto" at its current level on
  today's date, in a Poke Ball; three perfect IVs (five for Mew and Celebi), the others random; the nature its
  EXP mod 25 gives; its Hidden Ability, except the species Transporter leaves on their first ability; EXP reset
  to its level's minimum; its moves (not Dizzy Punch) and nickname kept, its held item and mail left behind; a
  new PID that keeps it shiny or not. `source` is the Virtual Console game it comes from (red, blue, yellow,
  gold, silver, crystal; one with Crystal's met data is always Crystal's).
- **Gen 6 (`from` xy/oras) -> Gen 7: Pokemon Bank.** Markings, Super Training and geolocations reset, and the
  trainer who withdraws it becomes its current handler, even its own OT, as PKHeX's legality check expects. A
  Cosplay Pikachu is refused, as Bank refuses it.

`trainer` is who runs the app: the handling trainer, and their 3DS region. An Egg is refused, and `game` must be
Gen 7: nothing official reaches Gen 6 from the Game Boy.

## How a build works

1. PKHeX's encounter generator lists the species' encounters in the chosen games (wild slots, then
   static encounters, in-game trades, eggs, and event gifts last), each at or below the asked level.
2. Each encounter is turned into a record with the wishes as criteria (`EncounterCriteria`): shiny,
   nature, gender, ability. An evolved species keeps its pre-evolution's encounter and is evolved.
3. The level, moves (or, with none asked, the moves it would know at that level), item, ball and nickname
   are applied, and PKHeX's `LegalityAnalysis` checks the result.
4. The first record that is legal and still has every wish is returned. None: the request is refused
   with PKHeX's reason, or "the encounter could not meet every request". (A species the game does not
   have, Rowlet in X/Y, is refused by PokeUDS before PKHeX is asked.)

## Build one (the Trade page) and `pokeuds build`

On the Trade page, **Build one** opens the builder for the chosen game. It collects the wishes (species,
level, nickname, shiny, gender, nature, ability, held item, ball, up to four moves) and the original
trainer, which it keeps for the next build.
The trainer is entered as each generation shows it: a five-digit ID (and SID from Gen 3 on), or from Gen
7 the six-digit ID and four-digit SID. Build and offer it puts the result in the bank (source "built", with
PKHeX's verdict) and makes it the trade's offer. Without the helper the panel says how to build it, with
a button that runs `dotnet build` when the .NET SDK is installed.

The same from the command line; names or numbers are accepted, `--out` takes a file or a folder, and
without `--out` the result goes to the bank:

```bash
python -m pokeuds build xy "mr. mime" --level 20 --nature timid --moves confusion "magical leaf" --out mime.pk6
```

```bash
python -m pokeuds build gsc togepi --level 5 --shiny --ot KRIS --tid 2468 --female
```

Things to know:

- **Shiny-locked Pokemon** (ORAS's Rayquaza, the Tapus) come back as their official event gifts when
  shiny is asked for, since that is the only legal shiny; they keep the event's OT, ID and met data.
- **Gen 2 shininess** is a DV pattern; encounters that ignore the shiny wish (Gen 2 eggs, for one) are
  given shiny DVs and checked again.
- A wish PKHeX cannot meet is refused with its reason ("Invalid Move 1" for a move the species cannot
  know at that level); nothing is kept.
- Forms other than the base form are not offered yet.

## From Python

```python
from pokeuds import pkhex

helper = pkhex.Helper()
built = helper.build("xy", 25, level=30, shiny=True, nature=3, nickname="Sparky",
                     trainer={"ot": "ASH", "tid": 1234, "sid": 4321})
assert built.legal
open("Sparky.pk6", "wb").write(built.data)
```

`tests/test_pkhex.py` builds one of each game and loads it with PokeUDS's own readers; it is skipped
when the helper is not built.
