# Mystery Gift: how feasible?

What each game PokeUDS covers can receive as a Mystery Gift, over which link, and whether PokeUDS could
stand in for the distributor; how pokeldn does it for the Switch; and what PokeUDS does today: cards
read, made and checked (the Gifts page and `pokeuds gift`), their Pokemon sent by trade. Handing a card
to a game over local wireless is not done yet: its protocol is unknown.

## Summary

| games | Mystery Gift over | what it gives | PokeUDS could distribute? |
|---|---|---|---|
| Red / Blue / Yellow (VC) | (none) | Gen 1 has no Mystery Gift | no |
| Gold / Silver / Crystal (VC) | the 3DS's **infrared** port, between two consoles | items and decorations | no: infrared, not wireless |
| X / Y, Omega Ruby / Alpha Sapphire | **local wireless** from a distributor (store and event distributions), and the internet | Pokemon, items | possibly: the radio side is the same UDS PokeUDS speaks; the gift protocol is unknown |
| Sun / Moon, Ultra Sun / Ultra Moon | **local wireless** from a distributor (event distributions), and the internet | Pokemon, items, Festival Coins, Poke Beans, BP | possibly, as for Gen 6 |

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
  `WC6Full` / `WC7Full`, `GiftStart` = 0x208). The header carries the distribution's restrictions, among
  them the games (`RestrictVersion`, at 0x000) and languages (`RestrictLanguage`, at 0x1FF) it is offered
  to. The card is the familiar `.wc6` / `.wc7`: card ID, title, date, the Pokemon or item, and its flags
  (one per save, once a day, already received). Event archives keep most cards in both forms.
- **The link.** All 3DS local wireless is UDS, so a local distribution is UDS: either a session the game
  joins (as with the PSS and the Festival Plaza), or beacons alone, as Sword and Shield do it (next
  section). PokeUDS has both sides: beacons it sends on its own timer, and the join, CCMP and data channels.
- **The games' side.** Receiving is a menu in each game's Mystery Gift: the game looks for a nearby
  distribution, lists what it finds, and saves the card it accepts. A Gen 6/7 game cannot distribute; only
  Nintendo's distribution software (official, event-only) does.

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

### What is not known

None of this is documented, and no capture is known to be public:

1. **The network the game looks for:** its local communication ID and the beacon's application data
   (how it recognises a distribution), and the passphrase the CCMP key derives from.
2. **The exchange:** what the game sends once joined, how the distributor offers its gifts (a list,
   then one full file, or several), in what framing (a transport like the VC's Pia, or the game's own), and
   what acknowledges a received gift.
3. **The checks** the game makes on what it receives (beyond the header's restrictions), such as a
   signature. If distributions were signed, no stand-in could send a gift the game accepts. Nothing
   points either way yet; this is the question that decides feasibility.

### How to find out

In order of cost, each step reusing what PokeUDS already has:

1. **Azahar, receive mode.** Run X/Y (or Sun/Moon) in azahar_uds, open the local-wireless receive menu,
   and keep the log. No distributor is needed; the game shows what it is looking for:
   - every beacon scan is logged as `UDS Real: game beacon scan #N, filterMac=..., wlanCommId=0x........,
     id=..., queuedReplyCount=..., firstFrameBytes=..., outputCapacity=...` (`RecvBeaconBroadcastData`):
     the comm ID it filters on, and the buffer it reads beacons into;
   - a `ConnectToNetwork` (or `Initialize`/`Bind` for a data channel) on that screen means a session;
     none, while the scans go on, means the beacon design above.
2. **Host what the game wants.** With step 1's values in a profile (`profiles/<game>.local.toml`: comm ID,
   passphrase, application data), `pokeuds trade xy --role host` can already offer a matching network. Whatever the game sends after joining (sniffed and decrypted with
   `pokeuds sniff`, summarised with `pokeuds analyze`) starts step 2.
3. **The game's code.** From your own dumped game, the receive routine shows the message layout and
   any signature check (step 3), which a capture alone cannot prove.
4. **A real distribution**, if one is ever on the air again (a store or tournament event): `pokeuds
   sniff` with the game's passphrase records the whole exchange between the distributor and a console.

If step 3 finds no signature, a distributor in PokeUDS is a few hundred lines on top of the host mode:
beacon as a distribution, offer one `.wc6full` / `.wc7full` from the user's own files (or one the Gifts
page made), and, for a session design, send it to the console that asks. If it finds one, local Mystery
Gift is not feasible and the next section is the way.

PokeUDS will not use or point to leaked copies of Nintendo's distribution software.

## What works today

### Cards

The PKHeX helper reads, makes and checks Gen 6/7 cards (`services/pkhex/Gifts.cs`, docs/pkhex.md):

| request | what it does |
|---|---|
| `gifts` | the official cards PKHeX.Core embeds for a game (one per card, species and form; `all` for every card, items and revisions too, with their bytes) |
| `gift` | an official card (by key) or a `.wc6`/`.wc7`/full file, made into its Pokemon for the trainer and checked |
| `card` | a custom card from a form, and the Pokemon it gives |

PKHeX.Core 26.10.18 carries 838 Gen 6 and 937 Gen 7 cards. A card that names its own OT gives a Pokemon
traded from that trainer; one that leaves it open makes the receiver the OT (in Gen 7 the card marks this
with OT gender 3; in Gen 6 only eggs do it). The card reaches the versions in `RestrictVersion`, a bit
mask: 1 X / Sun, 2 Y / Moon, 4 Alpha Sapphire / Ultra Sun, 8 Omega Ruby / Ultra Moon, 0 all.

### The gift Pokemon by trade (simulated gifts)

As pokeldn does for the Switch games without local gifts: the card's Pokemon, made as the Builder's
trainer would receive it, goes into the bank and from there into a trade. The Gifts page has three ways
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

**Build your own** (or `pokeuds gift card`) writes a card PokeUDS made: a Pokemon (species, level,
shiny, nickname, the card's OT or the receiver, moves it knows at that level, Classic Ribbon, met in "a
lovely place", a fateful encounter as every card gives), an item and quantity, or Gen 7 Battle Points, for the
versions ticked. The file is a well-formed card that PKHeX opens. Its Pokemon can go by trade today, but
**PKHeX flags it** ("Unable to match to a Mystery Gift in the database"): no such event existed, and a
check such as the GTS's, or a future one, would refuse it. A Gen 6 card for "the receiver" names the
trainer entered, since Gen 6's games copy a card's IDs (no non-egg Gen 6 card leaves the OT open).

Whether a game accepts a custom card over local wireless is the signature question above. If it does
(as Sword/Shield do), these files are what a PokeUDS distributor would send.

### Testing

`tests/test_gifts.py` runs a sample of each game's official cards through every path (card file, full
file, conversion, legality, the bank) whenever the helper is built. `scripts/check_gifts.py OUT_DIR` runs
all of them and writes each as a file, about 80 s:

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

In Gen 1 and 2 the event Pokemon come from the Builder, which falls back to PKHeX's event encounters
(`pokeuds build rby mew --level 5`: "Event Gift (Mew)", legal; Crystal's Celebi is the GS Ball's, in
Ilex Forest), and go by trade.
