# The PKHeX helper

PokeUDS builds and checks Pokemon with [PKHeX.Core](https://github.com/kwsch/PKHeX) (26.10.18, GPL-3.0),
so a trade does not need a `.pk*` file made elsewhere: pick a species, a level and a few wishes, and the
helper returns a record PKHeX's legality checker accepts.

PKHeX.Core is a .NET library, so the helper is a small .NET program (`services/pkhex`) that Python runs
as one long-lived child process. Build it once (the .NET 10 SDK):

```bash
dotnet build -c Release services/pkhex
```

`pokeuds/pkhex.py` finds it in `services/pkhex/bin/Release/net10.0/` (or `services/pkhex/publish/`);
`POKEUDS_PKHEX` points at one elsewhere. Without it, everything else in PokeUDS works; only building
and legality checks are missing.

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
| `{"cmd":"card","game":"usum","species":778, ...}` | a custom card and its Pokemon (docs/mystery_gift.md, Custom cards) |

Wishes, all optional: `level`, `form`, `shiny`, `nature` (0 to 24), `gender` (`male`, `female`),
`ability` (`1`, `2`, `hidden`), `moves` (up to four move ids), `held_item`, `ball`, `nickname`, and
`trainer` `{ot, tid, sid, language, gender}` (the app fills it from Settings). Fields a generation does
not have (natures and balls before Gen 3, items in Gen 1) are ignored.

A built or checked record comes back as `legal`, `report` (PKHeX's legality report), `encounter` (the
encounter it came from), `extension`, `data` (base64, decrypted) and `summary` (species, nickname, level,
shiny, gender, OT, TID, move names, IVs, item, nature, ability, ball, origin).

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
