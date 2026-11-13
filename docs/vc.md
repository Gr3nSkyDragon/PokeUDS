# Virtual Console: Red, Blue, Yellow, Gold, Silver, Crystal

PokeUDS stands in for a whole 3DS running a Game Boy Virtual Console title: it hosts or joins
through the VC's own link menu (the host/join screen Azahar already handles), runs the Pia session
two VC instances use, and plays the Game Boy game's side of the cable itself. No ROM or emulator is
involved.

Status: **alpha**. Every layer is tested against the captured retail frames and against a model of
a retail 3DS, and the English games have traded with a retail 3DS both ways: PokeUDS joining, and PokeUDS hosting
Gen 1 with Gen 1 and Gen 2 with Gen 2 (below). The Time Capsule with PokeUDS hosting and the other languages are not
yet confirmed on hardware.

## Modes

| PokeUDS offers | the 3DS runs | mode | on the cable | PokeUDS saves |
|---|---|---|---|---|
| `.pk1` | Red, Blue, Yellow | Gen 1 | Gen 1 blocks | `.pk1` |
| `.pk2` | Gold, Silver, Crystal | Gen 2 Trade Center | Gen 2 blocks **and the mail block** | `.pk2`, plus `.mail` when the Pokemon holds mail |
| `.pk1` | Gold, Silver, Crystal in the Time Capsule | Time Capsule, PokeUDS as Gen 1 | Gen 1 blocks, no mail | `.pk1` |
| `.pk2` | Red, Blue, Yellow | Time Capsule, PokeUDS as Gen 2 | Gen 1 blocks, no mail | the `.pk1` that arrived and the `.pk2` a Gen 2 game makes of it |

When joining, the 3DS's title (its generation and language) comes from its beacon's comm id. When hosting,
`--as` names the title PokeUDS stands in for (`red`, `gold`, or one in another language as `red-de`,
`gold-fr`, `crystal-it`) and `--partner` the 3DS's generation if it differs.

```bash
python -m pokeuds vc trade --radio esp32 --keys aes_keys.txt --offer my.pk1
```

```bash
python -m pokeuds vc trade --radio esp32 --keys aes_keys.txt --offer cyndaquil.pk2 --offer totodile.pk2
```

```bash
python -m pokeuds vc trade --role host --as gold --radio esp32 --keys aes_keys.txt --offer my.pk2
```

```bash
python -m pokeuds vc convert my.pk2
```

**The trade queue:** `--offer` repeats, up to six (a Game Boy party), all `.pk1` or all `.pk2`. The queue is
PokeUDS's party in the Trade Center, and each trade offers the next one in order: as in the ROM, the Pokemon sent
leaves the party and the one received joins its end, so the console sees the party a real game would hold.
PokeUDS chooses Cancel after the last. `--trades N` (default: one per `--offer`) above the queue offers the first
slot again, for testing.

On the 3DS: choose Host in the VC link menu (or Join when PokeUDS hosts), then the Pokemon Center
receptionist, then the Trade Center (or the Time Capsule) and the table. PokeUDS answers the link menu
for Trade Center straight away (a Gen 1 game takes the partner's A press when its own player has not
pressed anything), offers its Pokemon in slot 1, accepts, and after `--trades` trades picks Cancel.

Files are PKHeX's single-Pokemon list layout: `.pk1` 69 bytes (`01`, Gen 1 index, `FF`, 44-byte
struct, OT, nickname), `.pk2` 73 bytes (`01`, dex number, `FF`, 48-byte struct, OT, nickname),
international 11-byte names. A `.pk2` offer may have its mail beside it as `<name>.mail` (47 bytes:
the 33-byte message and the 14-byte author record).

## Layers

| layer | module | source |
|---|---|---|
| UDS: beacon, join, CCMP, data channel 243 | `pokeuds/uds` | as for every 3DS game; passphrase `TRL_NETWORK` + NUL |
| Pia frames: prefix CRC-16/ARC, HMAC-MD5 tail with key `PokemonSIO`, 20-byte messages | `vc/pia.py` | mgba_LDN `wiki/vc_link.md`; rebuilds all 18 frames of a retail capture byte for byte |
| Pia session: hello, station info, profile, join request, mesh state, pings, clock sync, system stream, the reliable game stream | `vc/session.py` | the same page; the joiner as the retail 2DS does it |
| Game Boy cable: syncs, room choice, the blocks, the trade screen | `vc/link.py` | pret pokered `engine/link/cable_club.asm`, pokecrystal `engine/link/link.asm` |
| Records and the Time Capsule conversions | `gbpk.py`, `vc/gbdata.py` | pokecrystal `Link_PrepPartyData_Gen1`, `Link_ConvertPartyStruct1to2`; tables generated from pret by `scripts/gen_gb_tables.py` |

The VC sends one unit per serial exchange and waits for the partner's unit of the same index, except
for its blocks, which it sends in one burst. PokeUDS answers each 3DS unit after it has seen it,
which a cartridge cannot do; that is why it needs none of VC-Adapter's cartridge-side buffering.
What it takes from the measurements is the session format and the block window: for a block whose
first `$FD` the 3DS sends at index T, the 3DS stores PokeUDS's units from T-1, so PokeUDS sends a
padding `$FD` at T-1 and the rest of its block from T.

## The rounds

| step | Gen 1 and the Time Capsule | Gen 2 Trade Center |
|---|---|---|
| first unit | the host's `$EF`, the joiner's `$00` | same |
| "Please wait" | sync `$60` | sync `$60` |
| room | link menu, `$D4` (A on Trade Center); the Time Capsule's room confirm is also `$D4` | `Link_EnsureSync`, `$D1` |
| round opening | sync, two `$00` | syncs `$75`, `$76` (`CheckLinkTimeout_Gen2`), two `$00` |
| blocks | random numbers 17, party 424, patch lists 200 | random numbers 17, party 450, patch lists 200, **mail 390** |
| pick, confirm | `$60` + slot (`$6F` cancel), `$62` accept / `$61` decline | the same in `$70` |
| after the trade | one sync, then the round repeats | the check byte (1 if a Mew, 2 if a Celebi was traded, else 0), then the round repeats |

Mail exists only between two Gen 2 games: the Time Capsule's blocks are Gen 1's and Gen 1 has no mail.
In the mail block, message bytes `$FE` travel as `$21` and author-record bytes `$FE` as `$FF` with a
patch set; the receiver skips the `$20` preamble run, so PokeUDS answers `$20` until the 3DS's own run
ends and starts its mail with the 3DS's mail data.

## Time Capsule conversions

Gen 2 to Gen 1 (what a Gen 2 game sends): Gen 1 species index, box level 0, Gen 2 types (Magnemite and
Magneton Electric/Electric), the held item in the catch-rate byte, moves through PP copied, Special
recomputed from Red/Blue's base Special. Refused for a species above 151 or a move above Struggle.

Gen 1 to Gen 2 (what a Gen 2 game makes of what it receives): the catch rate becomes the held item
(`$19` Leftovers, `$2D` Bitter Berry, `$32` Gold Berry, several others Berry, any other value the same
item number), Special Attack and Special Defense from Gen 2 base stats, friendship 70.

## Measured, derived, unknown

- **Measured** (retail captures, Gr3nSkyDragon): the comm ids of Red `0x00171010` and Gold `0x00172610`, which
  Blue and Silver share (a pair hosts on one comm id so its two versions can trade: `pokeuds scan` of retail
  English Blue and Silver hosts, 2026-11-13); every frame and message format; the joiner's handshake; the unit
  layout; the block window. A retail host's network: id `1`, two nodes, channel 6, and 16 bytes of application
  data, eight per-session bytes, `01`, seven zeros.
- **Derived**: every other title's comm id (title id + 0x10, Blue and Silver their pair's; unmeasured): English
  Yellow and Crystal, and every non-English title (Languages, below). Joining matches the comm id against the title table, so a wrong
  guess means that title is not found: `python -m pokeuds scan` shows the comm id a console really sends.
- **Hosting.** The host's messages, including the mesh state and system data replayed
  from the retail host's frames, are what a retail host sends, and PokeUDS-as-host trades with the
  model. Its network now matches a retail host's (id `1`, attributes `0x8000`, the application data's shape);
  with id `0` and attributes `0`, no console joined it. As in a sniff of two retail consoles (2026-10-04), every
  frame goes on the air as a broadcast and the setup messages (station info, profile, mesh state) are for
  destination `0`: a retail joiner answered PokeUDS's hello, took its acks (destination `0`) and ignored its
  station info and profile sent to `2`, then gave up. The host answers each joiner setup message in turn (its
  station info after the joiner's, its profile after the joiner's, the mesh state after the join request),
  re-sends its system data every 0.25 s until it is acked, and only then sends its Start: its first game unit,
  `EF`, with role flags `FE 00 00` (every later host unit `00 00 00`; all six captures). Host units carry a
  per-session 32-bit value. A retail joiner acked an `EF` flagged `00 00 00` and stayed in the link menu. Azahar hosting a retail joiner never completed a trade,
  for a cause not visible on the wire. PokeUDS hosting has since traded with a retail 3DS, Gen 1 with Gen 1 and
  Gen 2 with Gen 2; the Time Capsule as host is not yet confirmed.
- **Unknown**: whether a VC checks the application data; Gen 2 against a retail console over the air
  (Gen 2 was measured through Azahar's local bridge); Crystal; everything about the non-English titles on
  the air (Languages, below).

## Languages

The 39 Virtual Console titles (`pokeuds/games/vc.py`; title ids `0004000000xxxx00` from Bl4ckSh4rk's list, the
comm id being the low 32 bits + 0x10):

| | Japanese | English | German | French | Spanish | Italian | Korean |
|---|---|---|---|---|---|---|---|
| Red | `170C` | `1710` | `1713` | `1716` | `1719` | `171C` | none |
| Green | `170D` | none | none | none | none | none | none |
| Blue | `170E` | `1711` | `1714` | `1717` | `171A` | `171D` | none |
| Yellow | `170F` | `1712` | `1715` | `1718` | `171B` | `171E` | none |
| Gold | `1723` | `1726` | `1729` | `172C` | `172F` | `1732` | `1735` |
| Silver | `1724` | `1727` | `172A` | `172D` | `1730` | `1733` | `1736` |
| Crystal | `1725` | `1728` | `172B` | `172E` | `1731` | `1734` | none |

Two titles trade when their generation fits (or the Time Capsule joins them) **and their language family
agrees**: the family is how a game lays out the names and mail in its blocks. PokeUDS speaks all three
(`pokeuds/vc/link.py` `LAYOUTS`, `pokeuds/vc/gbtext.py`), so it joins, or stands in for, any title in the table:

| family | titles | names | party block (Gen 1 / Gen 2) | Gen 2 mail body |
|---|---|---|---|---|
| international | English, German, French, Spanish, Italian | 11 bytes: 10 characters and a terminator | 424 / 450 | `$20` run; 6 x 33-byte messages, 6 x 14-byte authors, the authors' patch list: 385 |
| Japanese | Japanese | 6 bytes: 5 kana and a terminator | 359 / 385 | `$20` run; 6 x (33-byte message, 9-byte author), one patch list over all of it: 285 |
| Korean | Korean Gold and Silver | 11 bytes: Hangul in 2 bytes, Latin letters in 1 | - / 450 | `$52` run; 6 x 65-byte messages, 6 x 14-byte authors, the authors' patch list: 577 |

The structs, species and move indexes, items and the party patch lists are the same in every family. The
layouts, the Japanese and Korean character maps and every language's species names come from Lorenzooone's
[PokemonGB_Online_Trades](https://github.com/Lorenzooone/PokemonGB_Online_Trades) (MIT, `f2030e7`):
`pokeuds/vc/gbtext_data.py` is generated from its `useful_data/text` files by `scripts/make_gbtext.py`, with its
licence kept at the top.

- **Why a table.** PokeUDS used to match a beacon by `comm id & 0xFFFFF000` against `0x00171000` and
  `0x00172000`. Against the real ids that took Japanese Gold, Silver and Crystal (`001723xx` to `001725xx`) for
  international Gen 2 and joined them with 11-byte names, which corrupts the trade; it missed Spanish Silver and
  Crystal, every Italian Gen 2 title and both Korean ones (`00173xxx`), and all of Japanese Gen 1 (`00170xxx`).
  The table fixes all of that, and each title now gets its own family's blocks.
- **Files.** A Japanese `.pk1`/`.pk2` has 6-byte names, so 59 and 63 bytes, as PKHeX writes them. A Korean `.pk2`
  is 73 bytes like an international one: what marks it Korean is the language the bank keeps beside it (from the
  received file's name, `_gsc-kr_trade1_...`). PKHeX reads both, names and all, and finds them legal.
- **Another language's Pokemon.** A queued Pokemon from a game of another language goes over as Lorenzooone's
  project sends it: the structs as they are; a Pokemon that still has its species' name gets the console's name
  for it (German GLUMANDA becomes French SALAMECHE or Japanese ヒトカゲ), so a trade evolution renames it as the game
  expects; any other name is carried character by character, a character the console's family cannot show
  becoming `?` (a Japanese game has no Latin letters, an international one no kana). Mail stays behind when the
  families differ. The trainer name the console sees is `--name` where its family can show it, else that game's
  own default (トレーナー for a Japanese game). What a trade brings is saved in the console's language and family.
- **Korean games have no Time Capsule partner**: there is no Korean Gen 1 game. A `.pk1` queue is refused with
  that reason before PokeUDS joins or hosts a Korean title.
- **PKHeX and Korean trainers.** A Korean record migrates to Gen 7 legally with a Hangul OT. One whose OT is in
  Latin letters (an international Pokemon traded into a Korean game) is found illegal there ("OT from Generation
  1/2 uses unavailable characters"): PKHeX expects a Korean game's OT in its own characters.
- **The language is kept.** A Pokemon received from a joined title is written as
  `<stamp>_<game>-<language>_trade...` (`gsc-de`), the bank keeps `language` with the record, the GTS carries it
  to whoever trades for it ([online.md](online.md)), and migrating it to Gen 7 hands that language to PKHeX's
  Transporter, which keeps it in the `.pk7` (a Pokemon from German Gold stays German). When PokeUDS hosts, the
  title it stands in for gives the language.
- **Unmeasured.** Only English Red, Gold and Silver have been seen on the air. Every other title's comm id is
  derived; the Japanese and Korean blocks are those of the cartridges as Lorenzooone's project trades with them,
  never yet seen from a 3DS. Still to check on hardware: the comm ids and the 16 bytes of host application data
  for a Japanese, a Korean and a non-English international title; whether the Japanese VC uses the same Pia
  framing and `PokemonSIO` key; the Japanese block window; the Korean mail run's length, and whether Korean
  mail replaces a `$FE` in a message with `$21` as the others do.

## Checking it on hardware

1. `python -m pokeuds scan` with a 3DS hosting from the VC link menu: its comm id should read
   `00171010` for Red.
2. `vc trade` joining that host; `-v` logs the session states and each phase of the cable.
3. An Azahar log of the same session: `python -m pokeuds analyze azahar_log.txt` prints both Game Boy
   byte streams from the channel-243 Pia frames and the parties found in them.
