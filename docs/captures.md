# Captures needed for Generation 6 (X/Y, ORAS)

PokeUDS can already bring up the UDS session X/Y uses. What it cannot do yet is talk to the game:
there is no decompilation of the Generation 6 games (no pret project covers them), and nothing public
documents the PSS or trade messages. The messages have to be recorded from real sessions.

## Why Azahar captures are the easy path

azahar_uds already trades X/Y with a retail 3DS. Because Azahar emulates the UDS service itself,
its log holds every game message in plaintext, both directions, with no decryption step:

| log line | content |
|---|---|
| `UDS DATA TRACE TX GAME` | each payload the emulated game sent: data channel, nodes, bytes |
| `UDS DATA TRACE RX GAME` | each payload the emulated game received |
| `ConnectToNetwork key-derivation input ... inputBytes=` | the game's passphrase (when the emulated game **joins**) |
| `BeginHostingNetwork #` / `ConnectToNetwork` | comm id, network id, max nodes, channel |

`python -m pokeuds analyze azahar_log.txt` turns such a log into a transcript, prints the passphrase
and comm id for `profiles/xy.toml`, and finds every Pokemon record in the messages.

## The sessions to record

Use the default log level (Info includes `Service.NWM`). Before each run, export both Pokemon involved
with PKHeX or PKSM so the record inside the messages can be matched byte for byte. Keep one log per
run (Azahar's `log/azahar_log.txt`, copied after closing the game).

1. **Retail requests, Azahar accepts.** Enter the PSS on both, wait about 30 seconds, then on the retail
   console tap the Azahar player and request a trade. Complete one trade.
2. **Azahar requests, retail accepts.** The same with the roles swapped. Between runs 1 and 2 we should
   see both sides of hosting and joining, and at least one of them logs the passphrase.
3. **A cancelled trade.** Offer a Pokemon, then back out at the final confirmation.
4. **Two trades in one session**, if the game allows it without leaving the trade screen.
5. **X with Y**, if you have both, so a version check in the messages would show up.

Then the same for ORAS:

6. **ORAS with ORAS**, runs 1 and 2 above (retail requests, Azahar requests). This tells us ORAS's comm
   id and passphrase for `profiles/oras.toml`, and whether its messages differ from X/Y's.
7. **ORAS with X/Y**, once in each direction (an ORAS console requesting an X/Y one, and back). If ORAS
   and X/Y turn out to use different comm ids, this shows which network the mixed pair uses, and the
   other id goes into `also_join_comm_ids`.

For each run, note which side PokeUDS would have to play: the console that ends up **hosting** the
trade's network (Azahar logs `BeginHostingNetwork`) and the one that **joins** (`ConnectToNetwork`).
PokeUDS can take either role; a run where Azahar hosts gives the application data a host advertises
(`application_data_hex`), and a run where Azahar joins gives the passphrase.

Also helpful: `python -m pokeuds scan --radio esp32 --seconds 60 --json captures/pss.json` with a
retail console idling in the PSS, which records its presence beacons and application data directly.

## Retail with retail

Once the passphrase is in the profile, two retail consoles can be recorded with no emulator:

```bash
python -m pokeuds sniff --radio esp32 --keys aes_keys.txt --game xy --out captures/xy_retail.jsonl
```

This also checks PokeUDS's UDS layer on real hardware (beacon parsing, key derivation, CCMP) before it
ever transmits. `analyze` reads the result the same way.

## What we will look for

- which side hosts after a trade request, and the PSS application data that advertises a player;
- the message types and order: request, accept, offer shown, confirm, swap, done, cancel;
- where the record sits in its message (232 or 260 bytes, encrypted or not) and what else rides
  with it (trainer data, checksums, a hash of the offered record);
- what the game checks before accepting (legality, version, a matching echo of the offer).
