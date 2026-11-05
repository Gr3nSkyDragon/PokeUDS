# Captures

**Generation 6 is mapped** ([gen6.md](gen6.md)): five azahar_uds logs of Pokemon X trading with retail
consoles were enough to decode every layer, and PokeUDS replays all of them. What is still unseen for
Gen 6 is the bytes of a PSS beacon while a console *asks* for a trade (the request fields were read
off the profiles instead) and any ORAS session; one log of each settles them.

**Generation 7 needs one capture.** Sun/Moon and USUM link the same NEX PRUDP code as X/Y, with the
same 8-byte checksum key, Pia's local UDS layer, gflnet and the UDS passphrase string, so PokeUDS's
transport already speaks to them. What is missing is the Festival Plaza part: how a Link Trade
invitation travels (azahar_uds saw JoinFesta person data in the beacon, command byte 0x17 for an
invitation), the Net-Z setup the game uses, and the trade's gflnet commands and profile.

## Why Azahar captures are the easy path

azahar_uds already trades X/Y, ORAS and USUM with a retail 3DS. Because Azahar emulates the UDS service itself,
its log holds every game message in plaintext, both directions, with no decryption step:

| log line | content |
|---|---|
| `UDS DATA TRACE TX GAME` | each payload the emulated game sent: data channel, nodes, bytes |
| `UDS DATA TRACE RX GAME` | each payload the emulated game received |
| `ConnectToNetwork key-derivation input ... inputBytes=` | the game's passphrase (when the emulated game **joins**) |
| `BeginHostingNetwork #` / `ConnectToNetwork` | comm id, network id, max nodes, channel; `applicationData=` (builds from 2026-11-04 on) |
| `retail beacon payload` | a retail console's decrypted beacon, with `applicationData=` in full (same builds) |

`python -m pokeuds analyze azahar_log.txt` turns such a log into a transcript, prints the passphrase
and comm id, checks every PRUDP signature, finds every Pokemon record, and decodes PSS beacons;
`--flow` lists every Net-Z and gflnet message in order.

## For Generation 6, if a hardware test needs it

1. **Retail asks, Azahar answers.** Both in the PSS; on the retail console tap the Azahar player and
   ask for a trade. The log then holds a real request beacon.
2. **ORAS with ORAS** (or ORAS with X/Y), one trade: confirms ORAS's comm id and messages.

## The sessions to record: Generation 7 (Festival Plaza)

Trading starts in the Festival Plaza: Link Trade, then local communication.

8. **USUM with USUM**, both directions as in runs 1 and 2: the retail console starts the Link Trade and
   Azahar answers, then the reverse. Ultra Sun with Ultra Moon is what azahar_uds already completed, so
   this is the fastest Gen 7 capture. It gives `profiles/usum.toml`.
9. **A cancelled Link Trade**, backing out at the final confirmation.
10. **Sun/Moon with Sun/Moon**, both directions, for `profiles/sm.toml`.
11. **Sun/Moon with USUM**, once in each direction, for `also_join_comm_ids` if their comm ids differ.

Run the logs through `python -m pokeuds analyze --flow`: if Gen 7 really shares Gen 6's transport, every
PRUDP signature checks out and the gflnet commands, the Pokemon records and the Festival Plaza beacons are
listed in order, which is all the Gen 7 trade needs.

## Every run

For each run, note which side PokeUDS would have to play: the console that ends up **hosting** the
trade's network (Azahar logs `BeginHostingNetwork`) and the one that **joins** (`ConnectToNetwork`).
PokeUDS can take either role; a run where Azahar hosts gives the application data a host advertises
(`application_data_hex`), and a run where Azahar joins gives the passphrase.

Also helpful: `python -m pokeuds scan --radio esp32 --seconds 60 --json captures/pss.json` with a
retail console idling in the PSS or the Festival Plaza, which records its presence beacons and application data directly.

## Retail with retail

Once the passphrase is in the profile, two retail consoles can be recorded with no emulator:

```bash
python -m pokeuds sniff --radio esp32 --keys aes_keys.txt --game xy --out captures/xy_retail.jsonl
```

This also checks PokeUDS's UDS layer on real hardware (beacon parsing, key derivation, CCMP) before it
ever transmits. `analyze` reads the result the same way.

## What we will look for

- which side hosts after a trade request, and the application data that lists a player in the PSS or the
  Festival Plaza;
- the message types and order: request, accept, offer shown, confirm, swap, done, cancel;
- where the record sits in its message (232 or 260 bytes, encrypted or not) and what else rides
  with it (trainer data, checksums, a hash of the offered record);
- what the game checks before accepting (legality, version, a matching echo of the offer).
