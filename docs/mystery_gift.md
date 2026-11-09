# Mystery Gift: how feasible?

What each game PokeUDS covers can receive as a Mystery Gift, over which link, and whether PokeUDS could
stand in for the distributor; how pokeldn does it for the Switch; and what PokeUDS does today: cards
read, made and checked (the Gifts page and `pokeuds gift`), their Pokemon sent by trade, and Gen 6/7 cards sent over local
wireless as a distributor sends them (`pokeuds gift distribute`; the Gen 7 beacon and the distributor's
OPSSR signature are decoded and reproduced).

## Summary

| games | Mystery Gift over | what it gives | PokeUDS could distribute? |
|---|---|---|---|
| Red / Blue / Yellow (VC) | (none) | Gen 1 has no Mystery Gift | no |
| Gold / Silver / Crystal (VC) | the 3DS's **infrared** port, between two consoles | items and decorations | no: infrared, not wireless |
| X / Y, Omega Ruby / Alpha Sapphire | **local wireless** from a distributor (store and event distributions), and the internet | Pokemon, items | possibly: the radio side is the same UDS PokeUDS speaks; the gift protocol is unknown |
| Sun / Moon, Ultra Sun / Ultra Moon | **local wireless** from a distributor (event distributions), and the internet | Pokemon, items, Festival Coins, Poke Beans, BP | yes: the beacon and the OPSSR signature are decoded; PokeUDS signs its Gen 7 cards (see below) |

The Switch games' local gifts run over LDN, which is pokeldn's territory; the 3DS games use UDS.

## Gen 2 (Virtual Console): infrared

On the Game Boy Color, Mystery Gift used the cartridge-to-cartridge infrared port. The 3DS Virtual
Console keeps it by routing it through the 3DS's own infrared transceiver (pret's pokecrystal builds the
VC version with patches around `Infrared_ExchangeMysteryGiftData`). Two 3DS consoles running Gold,
Silver or Crystal can exchange gifts face to face; there is no wireless path.

PokeUDS's radios (an ESP32-S3's Wi-Fi, a USB Wi-Fi adapter) cannot reach the infrared port. Doing it
would need infrared hardware on the PC side, the 3DS infrared framing the VC emulator uses, and Gen 2's
gift exchange on top. What it would give, items and decorations from a fixed table, is small. **Not
worth pursuing**; Gen 2 trades already deliver any Pokemon or held item.

## Gen 6 and 7: local wireless

### What is known

- **The containers.** A gift as a distributor sends it is a "full" file: **784 bytes (0x310)**, a
  **520-byte (0x208) distribution header** followed by the **264-byte (0x108) card** (PKHeX.Core's
  `WC6Full` / `WC7Full`, `GiftStart` = 0x208). The header is mapped below ("The full file"). The card is
  the familiar `.wc6` / `.wc7`: card ID, title, date, the Pokemon or item, and its flags (one per save, once
  a day, already received). Event archives keep most cards in both forms.
- **The link.** All 3DS local wireless is UDS, so a local distribution is UDS: either a session the game
  joins (as with the PSS and the Festival Plaza), or beacons alone, as Sword and Shield do it (next
  section). PokeUDS has both sides: beacons it sends on its own timer, and the join, CCMP and data channels.
- **The games' side.** Receiving is a menu in each game's Mystery Gift: the game looks for a nearby
  distribution, lists what it finds, and saves the card it accepts. A Gen 6/7 game cannot distribute; only
  Nintendo's distribution software (official, event-only) does.
- **What X looks for** (Pokemon X in Azahar, Mystery Gift > Get via Local Wireless, about 70 s with nothing
  in range, 2026-11-05): it starts UDS and only scans, never hosts, polling the beacons every 0.3 s for
  **comm id 0x00055D10**, the PSS's own (profiles/xy.toml), with **network id 255** (any) and no MAC
  filter. So a distribution is a network of the Gen 6 comm id, told apart from the PSS presence (id 2) and
  the trade network (id 1) by its beacon data (below, "The Gen 6 distribution, on the air"). Gen 7 not
  looked at yet.


### The full file

The header, as Project Pokemon mapped it from RAM dumps of local wireless events (theSLAYER, 2017), checked
against every full file PKHeX.Core embeds (422 Gen 6, 801 Gen 7):

| Offset | Size | Holds |
|---|---|---|
| 0x000 | 4 | the games that may receive it: bits 1 X / Sun, 2 Y / Moon, 4 Alpha Sapphire / Ultra Sun, 8 Omega Ruby / Ultra Moon (0x001-0x003 zero in all) |
| 0x004 | 0x1FA | the distribution text the receiving screen shows: UTF-16, `
` line breaks, NUL-terminated (up to 506 bytes seen) |
| 0x1FE | 1 | 1: a halo effect while receiving (7 Gen 6 cards, 17 Gen 7, Marshadow and Ash's Pikachu among them) |
| 0x1FF | 1 | the language it is limited to; 0 any (0 in all) |
| 0x200 | 1 | 1: receivable once a day (mostly, not always, the Gen 7 card's own once-a-day flag) |
| 0x201 | 1 | the card's place in a set |
| 0x202 | 2 | **CRC-16/CCITT-FALSE** (poly 0x1021, init 0xFFFF, gflnet's) of all 784 bytes with this field zero, little-endian: every one of the 1223 matches |
| 0x204 | 1 | the number of cards in a set (one more than the set holds: receivable once although repeatable) |
| 0x205 | 1 | Gen 6: 0x46; Gen 7: a weight for picking from a random set, mostly 0 |
| 0x206 | 2 | 0x46 0x46 |
| 0x208 | 0x108 | the card |

The game takes a distribution whole (up to 20 full files, 0x310 apart in its memory) and picks from it: at
random from a random set, else every card. The official distributors check the checksum.

PokeUDS writes the whole header (`services/pkhex/Gifts.cs`): an official card gets the header of PKHeX's own
full file of it (its real distribution text, in its language), byte for byte; a card opened from a full file
keeps that file's header; a new card gets "<what it gives> will be given to you as a gift!", the fixed bytes,
and the card's once-a-day flag. The card editor's **distribution text** and **halo** fields change it, and the
checksum is always computed anew. A card that several official files share (3 Gen 6, 13 Gen 7, with
different texts) takes the first one's header.

### How pokeldn does it, and what that suggests

pokeldn has local Mystery Gift working for Sword/Shield (and FireRed/LeafGreen's Mystery Gift over Friend
on the Switch's GBA releases), and simulated gifts for the Switch games that have none ([pokeldn's
docs](https://github.com/Decryptu/pokeldn): `swsh_gift.md`, `gift_simulated.md`, `gifts.md`):

- **Sword/Shield: the card travels in beacons.** The distributor joins nothing and opens no session. It
  advertises an LDN network whose 384 bytes of advertise data carry the card in fragments (a CRC-16/ARC
  framed body, a ten-byte header with the message length, fragment count and index and a CRC of the
  whole message, 300 bytes of card per fragment: a 720-byte WC8 is three). The receiving game only scans
  (`nn::ldn::Scan` about forty times a minute, never `Connect`), reassembles, checks the CRCs and the
  card's own fields, and stores it. **No signature**: CRCs only, so a card built by pokeldn is accepted.
  The beacon side lives in Game Freak's network library (`BeaconCommunication`).
- **Simulated gifts** for Let's Go, BDSP, Legends Arceus, Scarlet/Violet and Legends Z-A, which take no
  local card: the trade host offers a fresh legal copy of an event Pokemon from PKHeX.Core's embedded
  event database, made for the receiving trainer where the card leaves the OT open ("Your OT").
- **Custom gifts**: a form builds a WC8 (Pokemon, egg, items, clothing, BP, money) or an FRLG card, saved
  in pokeldn's `.pokegift` container or as the native file.

The 3DS games are by the same developer, on an earlier version of the same library family, so the same
design is a fair first guess for Gen 6/7: **cards in beacon application data, the receiver scanning
only.** A UDS beacon carries at most 200 (0xC8) bytes of application data, so a 784-byte full file would
take at least four fragments. The guess is cheap to test (next sections) and, if it holds, a distributor
in PokeUDS is mostly beacons, which both radios already send.

### The Gen 6 distribution, on the air

Read off a real local distribution of card 165 (an Omega Ruby / Alpha Sapphire card); the reassembled message
matched that event's `.wc6full` byte for byte. `pokeuds/games/gen6/delivery.py` has it, with the capture as its test.

- **The network.** The distributor hosts a UDS network of the Gen 6 comm id **0x00055D10** (X/Y and ORAS alike),
  network id **2**, room for 2, on channel 11, and never needs anyone to join: the game scans. The gift is in the
  beacons' **184 bytes of application data**, one fragment per beacon, the fragments taking turns.
- **A fragment:**

  | Offset | Size | Holds |
  |---|---|---|
  | 0x00 | 2 | CRC-16/CCITT-FALSE (init 0xFFFF) of bytes 0x02-0xB7, little-endian |
  | 0x02 | 6 | `07 01 18 01 02 41` (0x18: the header's size) |
  | 0x08 | 8 | a distribution id (meaning unknown; PokeUDS sends the one seen) |
  | 0x10 | 4 | 0x5A8 (meaning unknown; the same) |
  | 0x14 | 2 | the card's id |
  | 0x16 | 1 | the number of fragments |
  | 0x17 | 1 | this fragment's index |
  | 0x18 | 160 | this fragment's slice of the message, the last one padded with zeros |

- **The message:** u32 the full file's size (784), u32 the compressed size, then the `.wc6full` compressed with
  Nintendo's **LZ10** (0x10, a 24-bit size, flag bytes most significant bit first; a reference is a 4-bit length
  less 3 and a 12-bit distance less 1). A typical card compresses to about 300 bytes: two fragments.
- **No signature.** CRCs only, as on the Switch. Whether the game also checks the two unknown header words is
  for a live test to show. The gift data is not encrypted either: the UDS link has AES-CCMP (the keyslot
  0x2D key), but the beacon's application data is the plain LZ10 message, which is why PokeUDS can send a
  Gen 6 gift without any extra key.

`pokeuds gift distribute xy CARD` (or `oras`) sends a gift this way: a `.wc6full`, a `.wc6`, or a card from
`gift list`. On the 3DS: Mystery Gift, Receive Gift, Get via Local Wireless.

### The Gen 7 distribution, on the air

Read off a real Gen 7 distributor (title 00040000001A8900, "Gen 7 Local Distributor", a modified Sun/Moon)
in Azahar while it broadcast card 289, the 20th-anniversary Shaymin; two separate runs produced the same
bytes. `pokeuds/games/gen7/delivery.py` has it; the capture verified the format during development, and the
unit-test vector is synthetic (the Python signer reproduces the captured signature byte for byte).

- **The network.** The distributor hosts a UDS network of the Gen 7 comm id **0x00164810** (Sun/Moon's and
  USUM's, the Festival Plaza's own), network id **2**, room for **2**, on channel **11**, with **200 bytes**
  of application data. The receiving game scans; it never joins.
- **A fragment (200 bytes):**

  | Offset | Size | Holds |
  |---|---|---|
  | 0x00 | 2 | CRC-16/USB (reflected 0x8005, init 0xFFFF, xorout 0xFFFF) of bytes 0x02-0xC7 |
  | 0x02 | 6 | `78 01 02 1E 02 41` (constant) |
  | 0x08 | 8 | a distribution id (the captured distributor's; the same in both runs) |
  | 0x10 | 4 | 0xBA8 (constant in the capture; Gen 6's word was 0x5A8) |
  | 0x14 | 4 | the card's id (u16), the fragment count (u8), this fragment's index (u8) |
  | 0x18 | 160 | this fragment's slice of the message, the last one zero-padded |
  | 0xB8 | 16 | zero padding (part of the CRC) |

  The tag is the distributor's own checksum function (0x36D9CC; CRC-16/USB over 198 bytes) and both
  captured fragments matched it during development. PokeUDS computed a different CRC before this, which is
  the likely reason a retail 3DS did not list its distributions at all.

- **The message:** u32 the full file's size (784), u32 the compressed size, then the `.wc7full` compressed
  with LZ10 and **OPSSR-signed**. Two fragments carry a typical card.
- **The date, corrected by the GDB trace.** The extdata's `haishin/1` carries date `00 00 00`, but that is
  not what goes on the air: at split time the app stamps the **event's date** into the card. For card 289
  the runtime full file (captured at `0x41D2F0`) has date `23 c1 02` = **2018-05-15** and CRC `d2 56`,
  differing from the extdata file only at those five bytes. PokeUDS therefore keeps the card's own date and
  CRC by default; `gen7.delivery.sanitized` remains available for callers that want the date cleared.
- **Game restrictions still apply.** The extdata's Searing Groudon card is version byte `2` (Moon only);
  a Sun game will not show it, however the date is set. The Chinese Greninja is version `15` (all games)
  and dated 2016-06-06, which PokeUDS keeps.
- **PokeUDS's beacon:** `pokeuds gift distribute sm CARD` (or `usum`) hosts the Gen 7 network, signs the
  compressed message and cycles the fragments; the fragment header (0x02-0x17) matches the capture byte for
  byte.

**The signature, solved from the game source.** The source of the Gen 7 game (the `niji_project` in the
`git_program170525momiji` tree) contains `NetStatic/NetLib/source/Delivery/Wireless/`: the splitter
(`DeliveryWirelessBeaconSplitter.cpp`) and the receiver (`DeliveryWirelessBeaconBinder.cpp`). The splitter
signs the compressed stream with `mw::zorro::OpssrCmcSha1_Sign`, overhead 8, RSA-768, using the public key
`0x66B279` and the private key `0x66B2F7`; Nintendo's Zorro readme describes it as **OPSSR with an AES-CMC
keyed permutation and SHA-1**, and `extlib/ctr/CTR_MW-Zorro/tools/sign.pl` is the reference implementation.
The signer keeps the first `len(compressed) - 88` bytes clear and replaces the last 88 with a **96-byte RSA
block** that recovers them (the digest's first 8 bytes are part of the signed block), so the signed stream
is `compressed + 8` bytes. That is the "8-byte tail": not a checksum, but the end of the RSA block.

`gen7.delivery.opssr_sign` is a faithful Python port (AES-CMC, SHA-1, raw RSA-768). It reproduces the real
distributor's captured message **byte for byte**, including the tail `66 33 d7 72 f5 65 00 c5`, and
`opssr_verify` recovers the compressed stream. The retail game verifies/recoveres the block with the public
key before decompressing, so PokeUDS now signs every Gen 7 message.

**Where the private key lives.** The 126-byte public key is embedded in `gen7/keys.py` (it is public).
The RSA private exponent is **not** in the repository: it is the distributor app's own key, kept in a
gitignored local file — `rsa_d_key.txt` next to your `aes_keys.txt`, or the legacy
`pokeuds/games/gen7/delivery_key.py`. Without it the verifier still works, but `fragments` raises a clear
error instead of sending an unsigned message.

- **Easiest:** run `python scripts/extract_gen7_key.py "Gen 7 Local Distributor.cxi" --keys aes_keys.txt`.
  It writes `rsa_d_key.txt` next to `aes_keys.txt`, and PokeUDS reads it there. The GUI's Settings page has
  a **Gen 7 Mystery Gift Key** section below Console keys that asks for the distributor, runs the same
  extraction, and remembers the key file. Retail Sun/Moon/USUM (and their CIAs/CCIs) contain only the
  public key — point the script at the distributor (00040000001A8900), not at a retail game.
- **It is not in `aes_keys.txt`.** That file holds the console's UDS AES key (keyslot 0x2D) for the
  local-wireless encryption. The OPSSR key is data inside the distributor app.
- **In the app:** the private key is the 461-byte PKCS#1 `RSAPrivateKey` DER at VA `0x66B2F7` in the app's `.code`
  (00040000001A8900); the public key is the 126-byte DER at `0x66B279`. In the decrypted CXI, `.code`
  starts at file offset `0x3000` and maps at VA `0x100000`, so the private DER is at
  `0x3000 + (0x66B2F7 - 0x100000) = 0x56E2F7`.
- **With GodMode9:** dump the installed title's decrypted ExeFS/`code.bin` (title 00040000001A8900) and
  read `0x56B2F7..0x56B4C4`; GM9 has no named "extract RSA key" function. The public key is
  `0x56B279..0x56B2F7`. If you already have the decrypted CXI, reading those offsets is enough.
- **Install it:** the extractor writes `d=<hex>` to `rsa_d_key.txt`; PokeUDS loads that file
  automatically from beside `aes_keys.txt`, from the working directory, or from `POKEUDS_GEN7_KEY`. The
  legacy `delivery_key.py` (a module with `RSA_D`) still works if you prefer it.

**Verified on a retail 3DS** (Sun): the real distributor's captured Shaymin message, replayed through
PokeUDS's own beacons, completes and shows the card. PokeUDS's own message for the same card is now the
same 314 bytes, signature included.

### What is not known

1. Nothing about the signature: the source and the reference tool pin it down, and PokeUDS's output matches
   the real distributor's. The only open point is the game's own `bDoVerifySign` setting on a retail build
   (it verifies, since the retail receiver recovers the signature before decompressing).

PokeUDS will not use or point to leaked copies of Nintendo's distribution software.

## What works today

### Cards

The PKHeX helper reads, makes and checks Gen 6/7 cards (`services/pkhex/Gifts.cs`, docs/pkhex.md):

| request | what it does |
|---|---|
| `gifts` | the official cards PKHeX.Core embeds for a game (one per card, species and form; `all` for every card, items and revisions too, with their bytes) |
| `gift` | an official card (by key) or a `.wc6`/`.wc7`/full file, made into its Pokemon for the trainer and checked |
| `card` | a card from the editor's fields, and the Pokemon it gives |
| `card_fields` | any card (official or a file) as the editor's fields, which rebuild it byte for byte |

PKHeX.Core 26.10.18 carries 838 Gen 6 and 937 Gen 7 cards. A card that names its own OT gives a Pokemon
traded from that trainer; one that leaves it open makes the receiver the OT (in Gen 7 the card marks this
with OT gender 3; in Gen 6 only eggs do it). The card reaches the versions in `RestrictVersion`, a bit
mask: 1 X / Sun, 2 Y / Moon, 4 Alpha Sapphire / Ultra Sun, 8 Omega Ruby / Ultra Moon, 0 all.

### The gift Pokemon by trade (simulated gifts)

As pokeldn does for the Switch games without local gifts: the card's Pokemon, made as the builder's
trainer (Trade page, Build one) would receive it, goes into the bank and from there into a trade. The Gifts page has three ways
in: **Official events** (searchable), **Open a card** (`.wc6`/`.wc7`/`.wc6full`/`.wc7full`) and **Build your
own**; each shows what the card gives, PKHeX's verdict, Keep its Pokemon in the bank, Offer in a trade,
and Save the card (264 bytes) or the full file (784 bytes). From the command line:

```bash
python -m pokeuds gift list oras --search rayquaza
```

```bash
python -m pokeuds gift make oras 0547-384-0 --save-card rayquaza.wc6
```

```bash
python -m pokeuds gift show sm "Munchlax.wc7" --ot DANA --tid 2468 --sid 1357
```

What a trade cannot give: items, Festival Coins, Poke Beans and BP, and the card in the game's album.

### Custom cards

**Build your own** on the Gifts page edits every field a card holds: the title, the card text, the date, the
games, how often it can be received, and a Pokemon down to its nature, ability, ball, held item (and Gen 7's
second item), moves and relearn moves, IVs (or a number of perfect ones), EVs, ribbons, OT, met place,
language, PID and contest stats; or up to six items (Gen 7; one in Gen 6) with their amounts, Battle Points,
or Poke Beans. Preview shows the Pokemon it gives, with the stats the game will work out. **Customize** opens
any official card or card file in the editor. A gift is saved as a `.udsgift` (to open and change later) and
exported as the card itself (`.wc6`/`.wc7`, or the 784-byte full file). The whole format, byte by byte, and
what a card cannot hold (stats, a message of your own, a background) are in
[docs/wonder_cards.md](wonder_cards.md).

A custom card's Pokemon can go by trade today, but **PKHeX flags it** ("Unable to match to a Mystery Gift in
the database"): no such event existed, and a check such as the GTS's, or a future one, would refuse it.
Whether a game accepts a custom card over local wireless is the signature question above. If it does (as
Sword/Shield do), these files are what a PokeUDS distributor would send.

### Testing

`tests/test_gifts.py` runs a sample of each game's official cards through every path (card file, full
file, conversion, legality, the bank) whenever the helper is built. `scripts/check_gifts.py OUT_DIR` runs
all of them and writes each as a file, about a minute; it also rebuilds each from the editor's fields:

| game | cards | Pokemon and eggs legal | items | BP |
|---|---:|---:|---:|---:|
| X / Y | 704 | all | | |
| Omega Ruby / Alpha Sapphire | 771 | all | | |
| Sun / Moon | 668 | all | | |
| Ultra Sun / Ultra Moon | 484 | all | | |
| total | 2,627 | 2,132 | 454 | 41 |

Every card comes back byte for byte from its `.wc6`/`.wc7` and from its full file. Three things the run
found and PokeUDS now handles:

- PKHeX gives the unhatched eggs of card 142 (Japanese Mega Evolution eggs, 2016) a met date its own check
  refuses; an unhatched egg's met date is cleared.
- PKHeX stamps today's date into its embedded cards that carry none (355 of ORAS's): such a card lists with
  no date, and a card exported from PKHeX's database carries the stamped date, not the event's.
- PKHeX's "Simulated Pokemon Bank" entries (card 0000) are its own stand-ins for Pokemon Bank gifts, not
  distributions; they are listed last.

In Gen 1 and 2 the event Pokemon come from the builder (Build one), which falls back to PKHeX's event encounters
(`pokeuds build rby mew --level 5`: "Event Gift (Mew)", legal; Crystal's Celebi is the GS Ball's, in
Ilex Forest), and go by trade.
