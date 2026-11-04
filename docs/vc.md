# Virtual Console: Red, Blue, Yellow, Gold, Silver, Crystal

PokeUDS stands in for a whole 3DS running a Game Boy Virtual Console title: it hosts or joins
through the VC's own link menu (the host/join screen Azahar already handles), runs the Pia session
two VC instances use, and plays the Game Boy game's side of the cable itself. No ROM or emulator is
involved.

Status: **alpha**. Every layer is tested against the captured retail frames and against a model of
a retail 3DS; nothing has run on hardware yet. Joining a retail host is the role every capture
covers; hosting is experimental (below).

## Modes

| PokeUDS offers | the 3DS runs | mode | on the cable | PokeUDS saves |
|---|---|---|---|---|
| `.pk1` | Red, Blue, Yellow | Gen 1 | Gen 1 blocks | `.pk1` |
| `.pk2` | Gold, Silver, Crystal | Gen 2 Trade Center | Gen 2 blocks **and the mail block** | `.pk2`, plus `.mail` when the Pokemon holds mail |
| `.pk1` | Gold, Silver, Crystal in the Time Capsule | Time Capsule, PokeUDS as Gen 1 | Gen 1 blocks, no mail | `.pk1` |
| `.pk2` | Red, Blue, Yellow | Time Capsule, PokeUDS as Gen 2 | Gen 1 blocks, no mail | the `.pk1` that arrived and the `.pk2` a Gen 2 game makes of it |

When joining, the 3DS's generation comes from its beacon's comm id. When hosting, `--as` names the
title PokeUDS stands in for and `--partner` the 3DS's generation if it differs.

```bash
python -m pokeuds vc trade --radio esp32 --keys aes_keys.txt --offer my.pk1
```

```bash
python -m pokeuds vc trade --radio esp32 --keys aes_keys.txt --offer my.pk2 --trades 2
```

```bash
python -m pokeuds vc trade --role host --as gold --radio esp32 --keys aes_keys.txt --offer my.pk2
```

```bash
python -m pokeuds vc convert my.pk2
```

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

- **Measured** (retail captures, Gr3nSkyDragon): the comm ids of Red `0x00171010`, Gold `0x00172610` and
  Silver `0x00172710`; every frame and message format; the joiner's handshake; the unit layout; the
  block window.
- **Derived**: Blue, Yellow and Crystal's comm ids (title id + 0x10, unmeasured). Joining matches the
  generation's family (`0x00171xxx`, `0x00172xxx`), so a wrong guess only matters when hosting.
- **Experimental: hosting.** The host's messages, including the mesh state and system data replayed
  from the retail host's frames, are what a retail host sends, and PokeUDS-as-host trades with the
  model. But Azahar hosting a retail joiner never completed a trade, for a cause not visible on the wire,
  and the host's 16 bytes of beacon application data are copied in shape only (`08`, seven random
  bytes, `01`, zeros). Prefer joining.
- **Unknown**: whether a VC checks the application data; Gen 2 against a retail console over the air
  (Gen 2 was measured through Azahar's local bridge); Crystal; Japanese releases (6-byte names).

## Checking it on hardware

1. `python -m pokeuds scan` with a 3DS hosting from the VC link menu: its comm id should read
   `00171010` for Red.
2. `vc trade` joining that host; `-v` logs the session states and each phase of the cable.
3. An Azahar log of the same session: `python -m pokeuds analyze azahar_log.txt` prints both Game Boy
   byte streams from the channel-243 Pia frames and the parties found in them.
