# Wonder Cards (WC6, WC7, and the DS games' PCD and PGF)

The Gen 6 and Gen 7 Mystery Gift card, byte by byte, as PokeUDS reads and writes it, and what the card editor
(Gifts page, Build your own; `pokeuds gift card`) can set. Mapped by setting each field PKHeX.Core 26.10.18
names and watching which bytes change, then checking every byte across PKHeX's 838 Gen 6 and 937 Gen 7 official
cards: a byte no field explains and no card varies is unused. The editor's schema round-trips all of them, byte
for byte (`scripts/check_gifts.py`); the one exception is a PKHeX-made stand-in whose 37-character title PKHeX
itself cannot write back.

## Files

| file | size | what it is |
|---|---|---|
| `.wc6` / `.wc7` | 264 (0x108) | the card, as PKHeX, the event galleries and a save's card album keep it |
| `.wc6full` / `.wc7full` | 784 (0x310) | a distribution: a 520-byte (0x208) header, then the card (the header: [mystery_gift.md](mystery_gift.md), "The full file") |
| `.udsgift` | JSON | PokeUDS's saved custom gift: the editor's fields (below), and the card they made |

## The card

Offsets in the 264-byte card; Gen 6 and Gen 7 share the layout but where marked.

| offset | size | field | editor field |
|---|---|---|---|
| 0x000 | 2 | card number | `card_id` |
| 0x002 | 0x4A | title, UTF-16, 37 characters with no terminator (official cards use at most 36) | `title` |
| 0x04C | 4 | date the card was made (Gen 7: 3 bytes) | `date` |
| 0x050 | 1 | **card text**: which of the game's own messages the card shows (below) | `text` |
| 0x051 | 1 | kind: 0 Pokemon, 1 item; Gen 7 also 2 Poke Beans, 3 Battle Points | `kind` |
| 0x052 | 1 | flags: bit 0 not repeatable (set on nearly every official card), bit 1 received (cards taken from a save; the editor clears it), Gen 7 bit 2 once a day | `card_flags`, `repeatable`, `once_per_day` |
| 0x053 | 1 | can be received more than once (Gen 7 cards also hold 2) | `multi_obtain` |
| 0x054 | 0x10 | zero on every card | |
| 0x064 | 1 | 1 only on PKHeX's demo stand-ins | `extra_64` |
| 0x066 | 1 | 0xFF on every distributed card, 0 on demo-version cards; meaning unknown | `extra_66` |
| 0x068 | 4 | Pokemon: TID, SID. Item: item id, quantity (Gen 7: up to six item/quantity pairs, 0x068 to 0x07F). BP: amount. Beans: kind, quantity | `tid`, `sid` / `items` / `quantity`, `bean` |
| 0x06C | 1 | origin game (0: the receiver's) | `origin_game` |
| 0x070 | 4 | encryption constant (0: random) | `encryption_constant` |
| 0x074 | 2 | ribbons (bits) | `ribbons` |
| 0x076 | 1 | ball | `ball` |
| 0x078 | 2 | held item | `held_item` |
| 0x07A | 8 | moves | `moves` |
| 0x082 | 2 | species | `species` |
| 0x084 | 1 | form | `form` |
| 0x085 | 1 | language (0: the receiver's) | `language` |
| 0x086 | 0x1A | nickname, UTF-16, 12 characters | `nickname` |
| 0x0A0 | 1 | nature, 0xFF random | `nature` |
| 0x0A1 | 1 | gender: 0, 1, 2 genderless, 3 random | `gender` |
| 0x0A2 | 1 | ability: 0, 1, 2 hidden fixed; 3 first or second; 4 any | `ability` |
| 0x0A3 | 1 | PID: 0 fixed, 1 random, 2 always shiny, 3 never shiny | `shiny` |
| 0x0A4 | 2 | egg location | `egg_location` |
| 0x0A6 | 2 | met location (40001 "a lovely place" on most cards; event places 40001 on) | `met_location` |
| 0x0A8 | 1 | met level (PKHeX names it in Gen 7 only) | `met_level` |
| 0x0A9 | 6 | contest stats: cool, beauty, cute, smart, tough, sheen | `contest` |
| 0x0AF | 6 | IVs, in the order HP, Atk, Def, Spe, SpA, SpD; 0xFF random; 0xFC, 0xFD, 0xFE **in the HP byte** = 1, 2, 3 perfect IVs, the others random | `ivs` (display order), `perfect_ivs` |
| 0x0B5 | 1 | OT gender; 3 = the receiver is the OT (Gen 7, and Gen 6 eggs) | `ot_gender` |
| 0x0B6 | 0x1A | OT name, UTF-16, 12 characters; empty: the receiver | `ot` |
| 0x0D0 | 1 | level | `level` |
| 0x0D1 | 1 | an egg | `is_egg` |
| 0x0D2 | 2 | Gen 7: a second item handed over with the Pokemon | `additional_item` |
| 0x0D4 | 4 | PID (with PID type 0) | `pid` |
| 0x0D8 | 8 | relearn moves | `relearn` |
| 0x0E0 | 5 | OT memory: intensity, memory, variable (2), feeling | `memory` |
| 0x0E5 | 6 | EVs: HP, Atk, Def, Spe, SpA, SpD | `evs` (display order) |
| 0x0EB | 0x1D | zero on every card | |

### What a card cannot hold

- **Stats.** The game computes them from species, level, nature, IVs and EVs, all on the card. The editor's
  Preview shows the stats the game will give (PKHeX's calculation).
- **A message of your own.** The text under the title is one of the game's messages, picked by the byte at
  0x050. PokeUDS cannot read the games' text, so the editor names each value by the official cards that use
  it (Gen 7 text 56: "Gift from the PGL"; 91: the Special Demo Version cards; 54: gifts for the downloadable
  version; Gen 6 text 55: online competition gifts). Only the title is free.
- **A background.** No byte selects one: the game draws the card from what it gives. Neither official nor
  custom backgrounds can be chosen through the card; a custom one would mean changing the game itself.

## The editor's fields

The same JSON the PKHeX helper's `card` takes and `card_fields` gives back (services/pkhex/CardEditor.cs); a
`.udsgift` stores it under `fields`. Six-stat lists are in display order: HP, Atk, Def, SpA, SpD, Spe; `-1`
means random. A blank card (`pokeuds.app.giftfile.new_fields`) holds what most official cards do: card text 43
(Gen 6) or 44 (Gen 7), met in "a lovely place", a Poke Ball, the Classic Ribbon, the receiver as OT.

```json
{"card_id": 2000, "title": "A shiny Mimikyu for you!", "text": 44, "date": "2026-11-05", "kind": "pokemon",
 "versions": 12, "multi_obtain": false,
 "species": 778, "form": 0, "level": 50, "met_level": 50, "is_egg": false, "met_location": 40001,
 "ball": 1, "held_item": 234, "additional_item": 50, "moves": [389, 421, 104, 0], "relearn": [0, 0, 0, 0],
 "nickname": "Mimi", "language": 0, "ot": "", "origin_game": 0, "shiny": "always", "nature": 3, "gender": 1,
 "ability": "1", "ivs": [31, 31, 31, -1, 31, 31], "perfect_ivs": 0, "evs": [252, 252, 0, 0, 0, 4],
 "contest": [0, 0, 0, 0, 0, 0], "ribbons": ["classic", "wishing"]}
```

`versions` is the RestrictVersion mask: 1 X / Sun, 2 Y / Moon, 4 Alpha Sapphire / Ultra Sun, 8 Omega Ruby /
Ultra Moon, 0 all. An item card holds `items: [{"item": 50, "quantity": 5}]` (one in Gen 6, up to six in Gen 7);
a Battle Points card `quantity`; a Poke Beans card `bean` and `quantity`. Two fields are the full file's, not the
card's: `distribution_text` (what the receiving screen says; `""` for the default, or the text of the full file
or official card it was opened from) and `halo` (its halo effect while receiving).

A Gen 6 card cannot say "the receiver is the OT" for a Pokemon (no official non-egg card does, and the game
copies the card's IDs), so a Gen 6 card for the receiver names the trainer the app uses (the Trade page's
builder trainer).

## Eggs

An egg card is a Pokemon card with the egg byte (0x0D1) set; the editor offers it as **Gives: Pokemon egg**
(`kind: "egg"` to the helper and `pokeuds gift card --kind egg`), set as PKHeX's official event eggs are:

| | Gen 6 (70 eggs in ORAS's table) | Gen 7 (12 in Sun/Moon's) |
|---|---|---|
| level, met level | 1 | 1 |
| egg from | a Pokemon Center (40010) | a Pokemon Center or a lovely place |
| met at | nowhere yet (0) for the editor; some Gen 6 cards hold 40001 | nowhere yet (0) |
| OT | the receiver | the receiver |
| ball, shiny | Poke Ball, never shiny | Cherish Ball, may be shiny |
| ability | hidden on most | hidden on half |
| card text | 7 | 43 or 8 |

It arrives named Egg and hatches at level 1 for whoever hatches it, with the card's nature, ability, IVs,
moves, ball and item. **Legality:** the official egg cards are legal, also opened with Customize and rebuilt
unchanged; a custom egg is flagged like any custom card ("Unable to match an event Egg encounter"), and one
that copies an official egg's species but not its moves is flagged for the moves (an ORAS Bagon egg is
expected to know Thrash and Rage, as card 142's does). An egg with a met place fails PKHeX's met-date check,
so the editor leaves it empty.

## The DS games' cards (PCD, PGF)

**Build your own** works for the Gifts page's Nintendo DS games too (`services/pkhex/CardEditorDs.cs`): a Gen 4
`.pcd` or Gen 5 `.pgf` giving a Pokemon or an item (Gen 5 also an egg), exported as the card or as the gift file
the DS beacons carry (`.myg`, `.bin`; docs/mystery_gift.md), and sent from the page with **Start sending**. Each
DS card gives one item, with no amount: across every official Gen 4 and Gen 5 item card in PKHeX, the item number is
the only thing that changes, so neither card has room for a count.

| | Gen 4 (`.pcd`, 856 bytes) | Gen 5 (`.pgf`, 204 bytes) |
|---|---|---|
| card text | free text written on the card (0x154, 192 characters, the game's encoding) | none; the `.bin`'s distribution text instead |
| games | the compatibility bits at 0x14C: 0x400 Diamond, 0x800 Pearl, 0x1000 Platinum, 0x80 HeartGold, 0x100 SoulSilver | `RestrictVersion`: 1 White, 2 Black, 4 White 2, 8 Black 2 |
| date | none | the card's date |
| shiny | never (PID 1: the game rolls a PID, and such a gift is never shiny; all 247 of PKHeX's Gen 4 Pokemon cards bear this out), always (PokeUDS picks a PID shiny for the card's OT, as the 45 official shiny Gen 4 events are fixed-PID cards), or a fixed PID | random, always or never |
| nature, gender, ability | follow the PID; with Always shiny, PokeUDS picks one that gives those chosen | set on the card, or random |
| IVs | rolled by the game (no official card sets them) | set on the card, or random; no EVs |
| OT | named on the card | named on the card; an egg's is the receiver's |
| names | OT 7 characters, nickname 10 | the same |

- **Gen 4 is written onto an official card.** A `.pcd` holds a PGT gift, a whole PK4 for a Pokemon, inside card
  information PKHeX names only in part. PokeUDS starts from PKHeX's first random-PID Pokemon card (or first item
  card) and sets everything the editor shows, so the bytes nobody has named (0x152, 0x348, 0x354, and the PK4's
  fateful flag and Cherish Ball) are a real card's. The card shows the Pokemon's species as its middle icon
  (0x34C), as official cards do; an item card shows none. A custom Gen 4 card rebuilds byte for byte from its
  fields; an official one opened with Customize is rebuilt onto that base card, so it is not the same bytes.
- **Gen 5's text fields** end with 0xFFFF and are filled with 0xFF, as on official cards (an unset nickname is all
  0xFF: the species' own name); an item card's nickname and OT stay zero, as official item cards'.
- **PKHeX.Core 26.10.18's `PGF.Level`** reads 0x5B but its setter writes 0x5C, the egg flag; PokeUDS writes 0x5B
  itself. Of PKHeX's 837 official Gen 5 cards, 828 rebuild byte for byte from their fields; the other 9 list no moves
  at all, and PokeUDS writes the species' level-up moves into them, which is what the game gives.
- **A Pokemon "for whoever receives it"** gets the trainer from the Builder's settings written in, as in Gen 6: no
  official Gen 4 card, and no Gen 5 card but eggs, leaves the OT to the receiver.

On a DS (2026-11-17): a custom Gen 4 item card from this builder was received on a retail DS. Shinygati has received
custom Gen 4 and Gen 5 cards made with their own tools; this builder's Gen 4 Pokemon cards and its Gen 5 cards are
not yet tried. Gen 4/5 cards carry CRCs and checksums but no signature, so nothing should stop a game taking one.

## Working with them

| | in the app (Gifts) | command line |
|---|---|---|
| change an official card | Official events, pick, Customize | `pokeuds gift edit oras 0547-384-0 --save ray.udsgift` |
| change a card file | Build your own, Open... (or Open a card, Customize) | `pokeuds gift edit oras card.wc6 --save card.udsgift` |
| start from nothing | Build your own | `pokeuds gift card usum --species 778 --level 25 --out m.wc7` |
| save to change later | Save gift... (`.udsgift`) | edit the `.udsgift`'s JSON |
| export the card | Export .wc6/.wc7, Export full | `pokeuds gift card oras --project ray.udsgift --out ray.wc6` |
| send its Pokemon by trade | Keep its Pokemon in the bank, Offer | `pokeuds gift make oras ray.wc6` |
| send a DS card to a DS | Nintendo DS, Build your own, Preview, Start sending | `pokeuds gift distribute pt card.myg` (an exported `.myg`/`.bin`) |

A custom card's Pokemon is flagged by PKHeX ("Unable to match to a Mystery Gift in the database"): no such
event existed. Handing a card to a game over local wireless is the open question in docs/mystery_gift.md.
