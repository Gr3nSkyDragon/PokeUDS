# PKHeX

PokeUDS builds and checks Pokemon with [PKHeX.Core](https://github.com/kwsch/PKHeX) (26.10.18, GPL-3.0),
so a trade does not need a `.pk*` file made elsewhere: pick a species, a level and a few wishes, and PKHeX
returns a record its legality checker accepts. Building Pokemon, legality checks, Mystery Gift cards and
migration to Gen 7 use it; everything else in PokeUDS works without it.

PKHeX.Core is a .NET library, so it runs in a small .NET program (`services/pkhex`, `pokeuds-pkhex`) that Python
starts as one long-lived child process, as pokeldn runs its own. A crash in it fails one request, not the app.
Players never see it: there is nothing to download, install or set up.

## Where it comes from

| running | PKHeX | needs |
|---|---|---|
| the packaged app (Windows, Mac) | inside the app: `scripts/build_exe.py` publishes it self-contained into `pokeuds/app/data/pkhex/` before packing | nothing |
| from source | `dotnet build -c Release services/pkhex` (or `python -m pokeuds pkhex build`; the app offers **Build it with .NET** where PKHeX would be) into `services/pkhex/bin/Release/net10.0/` | the .NET 10 SDK, to build and to run |
| from source, self-contained | `python scripts/publish_pkhex.py` into `services/pkhex/dist/` | the .NET 10 SDK, once |

The packaged app always uses its own copy. On a Mac, should that copy have lost its executable bit inside the app
bundle, it is copied to `<data>/pkhex/` and made executable there, with no network. From source the newest build
found is used. `POKEUDS_PKHEX` points at one elsewhere and wins over everything. `python -m pokeuds pkhex status`
says which one, or what is missing: no .NET SDK, or one older than .NET 10 (an older SDK fails with
`NETSDK1045: The current .NET SDK does not support targeting .NET 10.0`; the app says so before trying).

The self-contained program is one file of 16 to 19 MB with the .NET runtime inside, as pokeldn ships its own:
published single-file, compressed and trimmed. PKHeX.Core, the C# runtime binder and the program itself are kept
whole, since the card editor reaches card fields through `dynamic` and reflection that full trimming would cut
(`services/pkhex/pokeuds-pkhex.csproj`). The published file passes the whole test suite and every official card
(`scripts/check_gifts.py`) with no .NET installed.

Why a child process and not PKHeX.Core inside Python: a native library compiled ahead of time cannot run the card
editor's `dynamic` code, and loading .NET into Python needs the .NET runtime installed (or 70 MB more in the app);
either way a crash in PKHeX would close the window. pokeldn makes the same choice on the desktop (its library build
is only for iOS, which starts no second process).

### Releases

`.github/workflows/release.yml` builds the Windows and Mac apps, each publishing PKHeX for its own computer into
itself (macOS on a Mac runner, where the SDK signs it: Apple Silicon refuses unsigned programs). A release carries
only the apps and `SHA256SUMS`. `.github/workflows/check.yml` builds PKHeX and runs the tests, the PKHeX ones
included, on every push.

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
| `{"cmd":"paste","game":"xy","text":"..."}` | `sets`: each Showdown or Smogon set in the text as the wishes `build` takes for this game, with `errors` and `notes` (Import paste, below) |
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
level, nickname, shiny, gender, nature, ability, held item, ball, up to four moves, and under **Set IVs and EVs**
the IVs, EVs and Hidden Power) and the original trainer, which it keeps for the next build.
The trainer is entered as each generation shows it: a five-digit ID (and SID from Gen 3 on), or from Gen
7 the six-digit ID and four-digit SID. Build and offer it puts the result in the bank (source "built", with
PKHeX's verdict) and queues it for the trade; Build and save in Bank only puts it in the bank, and the builder
stays open for the next one. Running from source without PKHeX built, the panel says how
to build it, with a button that runs `dotnet build` when the .NET SDK is installed.

**Import paste**, beside Build one, fills the builder from a Showdown or Smogon set, the text team builders export:

```
Sparky (Raichu-Alola) (M) @ Aloraichium Z
Ability: Surge Surfer
Level: 50
Shiny: Yes
Timid Nature
- Psychic
- Thunderbolt
```

PKHeX's own parser (`ShowdownParsing`) reads it in any language it knows, in the chosen game's context (Gen 2
numbers its items apart from the later games). A whole team can be pasted, a team export's `=== ... ===` line
included; each set is listed and **Use this one** opens the builder filled with it, to check and build as usual. A set
gives the species and form, nickname, level (Showdown's default is 100), shiny, gender, nature, ability (as the
species' first, second or hidden slot), held item, moves, IVs and EVs (below); it names no ball, so the builder's
stays.

| the set asks for | it is |
|---|---|
| a line the parser cannot read | an error, and the line is left out |
| a species, form, move or item the game lacks (an Alolan form or a Z-Crystal for X/Y) | an error, and it is left out; a species the game lacks cannot be used at all |
| an ability the species cannot have here | an error, and the ability is left open |
| a nature or ability in Gen 1/2, a shiny, gender or held item in Gen 1, Hidden Power in Gen 1 | a note: the generation has none |
| DVs for a shiny Gen 2 Pokemon | a note: its DVs are fixed, so the set's are left out |

IVs and EVs come as Showdown means them. From Gen 3 on, a set with no IVs line has 31s (any IVs are legal for
nearly every Gen 6/7 encounter) and its EVs as written. A Gen 1/2 set gives DVs only when it names some (an IVs line
or Hidden Power), since its DVs also fix gender and shininess; PKHeX turns its IVs into DVs (31 is 15) and its EVs
into stat experience (an EV squared, 252 being the most), and a stat it does not name gets the most stat experience,
as Showdown gives it. A Hidden Power type is already in the IVs: PKHeX's parser moves them to it.

## IVs, EVs, DVs and Hidden Power

The builder's **Set IVs and EVs** (Gen 1/2: **Set DVs and stat experience**) opens under the moves; a paste that
brings them opens it. Empty IVs are left to the encounter; **Best IVs** sets them all to the most, and **Clear them**
leaves everything to the encounter again.

| | Gen 1/2 | Gen 6/7 |
|---|---|---|
| IVs | DVs of 0 to 15 for Attack, Defense, Speed and Special; the HP DV is made of the other four's lowest bits (shown, not set) | 0 to 31 for each stat |
| EVs | stat experience of 0 to 65535 for HP, Attack, Defense, Speed and Special; every battle gives some to all five, so either every one is above 0 or all are 0 (PKHeX: `Cannot receive EVs`) | 0 to 252 each, 510 in all |
| Hidden Power | Gen 2: from the Attack and Defense DVs' two lowest bits; Gen 1 has none | from every IV's lowest bit |

**Hidden Power type** sets the IVs (or DVs) for a type, moving only their lowest bits, into the usual spread for
it (Showdown's, PKHeX's `HiddenPower.DefaultLowBits`: Ice lowers Attack and Defense to 30); the IV already at 0
stays. The section shows the type the IVs give once every IV it depends on is set, and a build reports it.

Requests carry `ivs` and `evs` as six numbers in Showdown's order (HP, Atk, Def, SpA, SpD, Spe); an IV of -1 is left
to the encounter, and Gen 1/2 read Special from SpA. From Gen 3 on the IVs also go into the encounter's criteria,
so an encounter that rolls IVs rolls them; one with fixed IVs (some gifts and events) fails the legality check and the
next encounter is tried. A shiny Gen 2 Pokemon has Defense, Speed and Special DVs of 10 and an Attack DV of 2, 3, 6, 7,
10, 11, 14 or 15: the builder refuses other DVs with a shiny before asking PKHeX.

A pasted form is built too: the builder sends `form`, and an evolved form takes its own ability in the slot the
encounter rolled (a Pikachu met in Alola evolves into an Alolan Raichu with Surge Surfer).

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
