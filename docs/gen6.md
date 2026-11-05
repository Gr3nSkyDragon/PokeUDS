# Generation 6: X, Y, Omega Ruby, Alpha Sapphire

PokeUDS trades from the PSS like a second console: it shows up in the PSS list, asks a console for a
trade or answers one, and runs the whole trade (offer, confirm, swap) by itself. Nothing is installed
on the 3DS.

Status: **ready for a first hardware test, X/Y**. Every layer below was decoded from Gr3nSkyDragon's
azahar_uds logs of Pokemon X trading with retail consoles (five sessions, both roles), and PokeUDS's
engine replays all five against the retail side's real messages to the end, in the role the emulated
game played (two trades as host, two as client, a cancelled trade, two sessions without a trade).
Untested on hardware. ORAS uses X/Y's network values by assumption (they share the PSS); no ORAS
session has been decoded.

```bash
python -m pokeuds trade xy --radio esp32 --keys aes_keys.txt --offer my.pk6
```

```bash
python -m pokeuds trade xy --role host --radio esp32 --keys aes_keys.txt --offer my.pk6 --partner Serena
```

```bash
python -m pokeuds trade xy --radio demo:x --keys none --offer my.pk6
```

- `--role join` (the default, "3DS asks"): PokeUDS appears in the PSS under the trainer name; on the
  3DS, tap it and ask for a trade. PokeUDS answers, joins the console's trade network and trades.
- `--role host` ("PokeUDS asks"): PokeUDS asks the first trainer it hears in the PSS (or `--partner`);
  accept on the 3DS.
- `--trades N`: trades before PokeUDS leaves the trade screen. PokeUDS offers as soon as the screen
  opens and confirms as soon as the console has offered; the player on the 3DS decides.
- The trainer shown is `--trainer/--tid/--sid`, else the app's trainer (Settings, also the Builder's),
  else the offered Pokemon's original trainer. A PSS id is made once and kept in the app settings.
- A box `.pk6` (232 bytes) is sent in party form with its battle stats filled in by the PKHeX helper
  when it is built (`dotnet build -c Release services/pkhex`); without it they stay zero.
- `--radio demo:x` (y, or, as) trades with a simulated console in the PSS, PokeUDS's own engine
  playing the other side.

## The layers

| layer | what it is | module |
|---|---|---|
| UDS | comm id `0x00055D10`, passphrase `GNgBwMcVotYs` + NUL, data channel 243; PSS presence networks `id=2`, the trade network `id=1` with four nodes | `uds`, `games/gen6/trade.py` |
| Pia local | 16-byte header: `02 01 src dst 01 00 len 00*8` | `nex/prudp.py` |
| PRUDP V1 | NEX's reliable UDP, both sides connecting to each other; signed with `MD5ChecksumWithKey` | `nex/prudp.py` |
| Net-Z | NEX's duplicated objects: a fixed setup per role, the client's clock heartbeats, the host's keepalives | `games/gen6/session.py`, `templates.py` |
| gflnet | GameFreak's framing: CRC-16/CCITT-FALSE, message index, received counts; sync points and commands | `games/gen6/wire.py` |
| the trade | profiles, the trade screen, offers, confirmation, swap, leaving | `games/gen6/session.py` |

### PRUDP signature

The game signs every packet with an MD5 keyed by 8 fixed bytes, `01 02 03 04 05 06 07 08`:

    MD5(header[4:12] | 00 00 00 00 | receiver's connection signature | options | payload | key)

SYN and SYN-ACK leave the connection signature out. A node's connection signature is a constant of
its node id (node 1 `29d8ab2b...`, node 2 `9dca2853...`); peers do not check it. Found in X's code:
its NEX setup copies the key into the checksum the P2P streams use (an `MD5ChecksumWithKey`, which is
`MD5(data | key)`), next to the HMAC key NEX keeps for connection signatures. Every packet of the five
sessions verifies.

Each side numbers its reliable packets in one sequence (CONNECT 1, data from 2) and its unreliable ones
from the value its CONNECT (client) or CONNECT-ACK (host) declares. Reliable data is acknowledged by
multi-acks, `[0][n][u16 everything up to][n x u16 more]`. Messages over 1300 bytes are fragmented.
Pings every few seconds. PRUDP flag 0x80 marks gflnet's stream.

### The trade

| step | messages (module, command or sync id) |
|---|---|
| host's first message | the two stations' object handles |
| start | sync 0x11 |
| profiles | module 0 command 06: 1616 bytes: TID/SID, version, PSS id and key, names, greetings, the partner's PSS id, the PSS player record |
| | sync 0x13, then sync 0x81 (the hub: 0x82, 0x83 ... after each trade) |
| trade screen | command 08 (state), command 09 (964 bytes) |
| offer | command 08 `00 00 01 00 ...` from the side offering first, command 01 (the 260-byte party record, encrypted), command 06 |
| confirm | command 07 `01` (another value: declined) |
| swap | command 0a; then the next hub sync, command 08, the host's command 0b, syncs 0xCB 0xCC 0xCD (module 1), and the screen opens again |
| leave | command 05, the next hub sync, command 00 (552 bytes: the PSS id, zeros), sync 0x0A (module 1), sync 0x16 |

Sync points: each side sends `02`, the host answers `03` once it has both. gflnet messages with a body
are followed, on both sides, by an empty one reporting the counts.

### The PSS

A console in the PSS keeps re-hosting a two-node `id=2` network (every 1-3 s, on channel 1, 6 or 11)
whose application data is its PSS beacon: gflnet's BeaconManager header (`CRC-16`, game, record count,
version, `02 02`, `0x41`, the PSS id) and one record, type 4, the player: name, greeting, PSS key and
id, version, region. A request rides in the record's bytes from beacon offset 0x84: the requester sets
a token, the target's key and PSS id and 0x94 = 1; the target answers with the token and the
requester's key and id. The requester then hosts the trade network and the target joins it. Those
fields are read off the consoles' profiles during captured trades, which copy the record; the bytes of
an actual request beacon have not been captured yet.

## What a first test can tell

1. `python -m pokeuds scan --radio esp32 --seconds 20` with the console in the PSS: an `id=2` network
   of comm id `00055D10` should appear, and `analyze` of an Azahar log decodes its beacon.
2. `trade xy` with `-v`: the log shows the PSS players heard, the request, the join, Net-Z, each
   phase of the trade.
3. If the console never lists PokeUDS, or a request is not recognised, an Azahar log of a console
   asking the emulator (current builds log the beacons in full) settles the beacon fields:
   `python -m pokeuds analyze azahar_log.txt --flow`.
