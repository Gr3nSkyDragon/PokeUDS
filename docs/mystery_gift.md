# Mystery Gift: how feasible?

A feasibility study, not a feature: what each game PokeUDS covers can receive as a Mystery Gift, over
which link, and whether PokeUDS could stand in for the distributor. Nothing here is implemented yet
except what the last section says already works.

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
- **The link.** All 3DS local wireless is UDS, so a local distribution is a UDS network; most likely the
  distributor hosts and the game scans and joins, as with the PSS and the Festival Plaza. Everything
  PokeUDS has for those (beacons, the node list, the join, CCMP, SecureData channels) would apply.
- **The games' side.** Receiving is a menu in each game's Mystery Gift: the game looks for a nearby
  distribution, lists what it finds, and saves the card it accepts. A Gen 6/7 game cannot distribute; only
  Nintendo's distribution software (official, event-only) does.

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
   and keep the log. The NWM calls answer step 1: the comm ID and scan filter the game uses, and, when it
   joins, the passphrase. No distributor is needed for this; the game shows what it is looking for.
2. **Host what the game wants.** With step 1's values in a profile (`profiles/<game>.local.toml`: comm ID,
   passphrase, application data), `pokeuds trade xy --role host` can already offer a matching network. Whatever the game sends after joining (sniffed and decrypted with
   `pokeuds sniff`, summarised with `pokeuds analyze`) starts step 2.
3. **The game's code.** From your own dumped game, the receive routine shows the message layout and
   any signature check (step 3), which a capture alone cannot prove.
4. **A real distribution**, if one is ever on the air again (a store or tournament event): `pokeuds
   sniff` with the game's passphrase records the whole exchange between the distributor and a console.

If step 3 finds no signature, a distributor in PokeUDS is a few hundred lines on top of the host mode:
beacon as a distribution, offer one `.wc6full` / `.wc7full` from the user's own files, send it to the
console that asks. If it finds one, local Mystery Gift is not feasible and the next section is the way.

PokeUDS will not use or point to leaked copies of Nintendo's distribution software.

## What already works: the gift Pokemon by trade

For Pokemon gifts, a trade delivers the same Pokemon today. PKHeX's encounter database includes the
event gifts, and the Builder already falls back to them when nothing else is legal (a shiny Rayquaza in
ORAS comes back as its event Pokemon, with the event's OT, ID and met data). What a trade cannot
give: items, Festival Coins, Poke Beans and BP, and the card in the game's Mystery Gift album.

A small next step: let the Builder take a `.wc6` / `.wc7` (or a full file) and turn it into the
Pokemon it gives with PKHeX (its own conversion, then the legality check), for the trainer the user
enters, so any archived event can be sent this way. In Gen 1 and 2 the same holds for event Pokemon such as
Mew, which only ever came from in-person events: built by PKHeX, sent by trade.
