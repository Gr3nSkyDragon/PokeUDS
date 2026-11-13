# Architecture

PokeUDS replaces one 3DS in a local wireless session. A game on a 3DS never sees the radio: it asks
the NWM system module to scan, host or join, and then sends and receives SecureData packets on
numbered data channels. PokeUDS does NWM's job in Python, and then plays the game's part on top.

```
 game protocol      pokeuds/games/          trade messages, the offer, the received record
 ------------------------------------------------------------------------------------------
 UDS (NWM)          pokeuds/uds/station     beacons, auth/assoc, EAPoL join, node ids,
                    pokeuds/uds/{beacon,     CCMP, SecureData channels, keepalive
                    crypto,data,dot11}
 ------------------------------------------------------------------------------------------
 radio              pokeuds/radio/esp32      raw MPDUs in and out, channel, beacon timer
                    pokeuds/radio/ldnd
```

## Radio

`Radio` (`radio/base.py`) is deliberately dumb: start on a channel with a MAC, set the channel,
send an MPDU, set a periodic beacon, receive MPDUs. No UDS logic lives in a backend, so the ESP32
and the USB adapter behave the same, exactly as azahar_uds keeps them.

| | ESP32-S3 (`esp32`) | USB adapter (`ldnd`) |
|---|---|---|
| link | USB CDC, COBS frames with CRC-32 | `\\.\pipe\ldnd`, forwarded Linux socket calls |
| beacon | firmware timer, template set over USB | software timer in the I/O thread |
| unicast ACKs | twin hardware MAC: none for our frames, peers retransmit | active monitor interface carrying our MAC |
| capture filter | beacons, our MAC, and `watch()`ed MACs | everything on the channel |
| OS | any | Windows |

Data frames are sent at fixed rates measured with retail consoles (client 11 Mbps, host 24 Mbps);
group-addressed frames are sent without waiting for an ACK.

`radio/sim.py` connects any number of radios in one process for tests.

## UDS station

`uds/station.py` runs one engine thread per station: it reads the radio, handles management and
data frames, and runs the timers (join retries every 0.5 s, client keepalive every 1.1 s). The
public calls (`scan`, `host`, `connect`, `send`, `recv`) are thread safe. The protocol itself is in
[uds.md](uds.md).

Captured retransmissions are dropped (management by sequence number and the retry bit, data by
CCMP packet number), since a monitor sees every copy.

## Games

A game module gets a connected `Station` and speaks the game's messages through `send`/`recv`.
Per-game constants (comm id, passphrase, data channels, the application data a host advertises)
live in `profiles/<game>.toml`, filled in from captures, so no value in the code is a guess.

One `LocalTrade` session (`games/trade.py`) serves every 3DS game, in both roles. It is
parameterized by a `Game` entry and that game's profile:

| game | generation | trading starts in | record | trades with |
|---|---|---|---|---|
| `xy` | 6 | PSS | PK6 | `oras` |
| `oras` | 6 | PSS | PK6 | `xy` |
| `sm` | 7 | Festival Plaza (Link Trade) | PK7 | `usum` |
| `usum` | 7 | Festival Plaza (Link Trade) | PK7 | `sm` |

`games/gen6` and `games/gen7` only declare their games (titles, origin version bytes, partners).
PK6 and PK7 share their layout, encryption and checksum (PKHeX derives both from `G6PKM`), so
`pkx.py` handles both and a `Format` carries the differences: extension and species range. An
offer the game cannot hold, such as a Gen 7 species for X/Y, is refused before the radio opens.

A profile's `also_join_comm_ids` lets one game join a partner game's networks (ORAS with an X/Y host,
Sun/Moon with a USUM host) if captures show their comm ids differ. If the game protocols themselves
turn out to differ (Gen 7 may run on Pia's local mode; see below), the session will dispatch to a
per-generation message layer; the UDS part stays shared.

| role | the session |
|---|---|
| join | scan for the profile's comm ids, pick the strongest host, join it as a client |
| host | open a network with the profile's comm id and application data, wait for an EAPoL join |

### Generation 6, and what Gen 7 shares

X/Y and ORAS trade over NEX: PRUDP V1 (`pokeuds/nex/prudp.py`) behind Pia's local UDS header, Net-Z's
object setup, and GameFreak's gflnet messages; `games/gen6` runs the PSS, the trade network and the
trade itself ([gen6.md](gen6.md)). Sun/Moon and USUM link the same NEX transport with the same
checksum key (found in both games' code), so Gen 7 reuses `nex/prudp.py` and gflnet's framing and
needs only its own Festival Plaza signalling and trade commands, from one capture.

## Virtual Console

The VC titles are a different kind of game: the 3DS emulates the Game Boy link cable over UDS, so
there is no 3DS game protocol to learn, only the VC's Pia session and the original Game Boy one.

```
 Game Boy cable     pokeuds/vc/link.py       syncs, room choice, blocks, trade screen (pret)
 Pia session        pokeuds/vc/session.py    hello, setup, mesh, pings, reliable unit stream
 Pia frames         pokeuds/vc/pia.py        CRC prefix, messages, HMAC "PokemonSIO" tail
 UDS                pokeuds/uds              data channel 243, passphrase TRL_NETWORK
```

`vc/trade.py` runs them together. The engine answers each 3DS unit after seeing it (the VC paces
one unit per exchange); as host it answers the joiner's unit k with unit k+1, after its own `$EF`.
See [vc.md](vc.md).

## Received Pokemon

As in pokeldn, each received record is written the moment it arrives (`received.save`), decrypted, in
the 232-byte box format PKHeX opens, one file per trade.

## Code layout

| Path | Contents |
|---|---|
| `pokeuds/radio/` | The ESP32 serial protocol, the ldnd pipe and nl80211 monitor, radiotap, a simulated air |
| `pokeuds/uds/` | 802.11 frames, beacons, CCMP and key derivation, SecureData, EAPoL, the station (host and client), the sniffer |
| `pokeuds/nex/` | NEX's PRUDP V1 as Gen 6/7 run it over Pia's local UDS layer |
| `pokeuds/games/` | Game registry and profiles; `gen6/` the PSS and X/Y-ORAS trade, `gen7/` the Festival Plaza and Sun/Moon-USUM trade |
| `pokeuds/vc/` | Virtual Console: Pia frames and session, the Game Boy trade engine |
| `pokeuds/online/` | Nostr signing and relays, and the GTS's events ([online.md](online.md)) |
| `pokeuds/pkx.py`, `gbpk.py` | Gen 6/7 and Gen 1/2 records, the Time Capsule conversions |
| `pokeuds/capture/` | Azahar-log and sniff transcripts, the protocol decoders behind `analyze` |
| `pokeuds/games/gen6/delivery.py`, `gen7/delivery.py` | Local-wireless Mystery Gift: the beacon fragments, LZ10, Gen 7's OPSSR signing |
| `gui/`, `pokeuds/app/` | The app (Flet) and what it shares with the command line; `gui/assets/` holds the icon |
| `packaging/`, `scripts/build_exe.py` | The Windows exe's entry point, the icon artwork and the build ([release.md](release.md)) |
| `services/pkhex/` | PKHeX's side (a .NET program the app runs): building and checking Pokemon, Mystery Gift cards, migration |
| `profiles/` | Each game's network constants, from captures |
| `docs/` | [The app](gui.md), [command line](cli.md), [UDS](uds.md), [Gen 6](gen6.md), [Gen 7](gen7.md), [Virtual Console](vc.md), [hardware](hardware.md), [captures](captures.md), [PKHeX](pkhex.md), [online and the GTS](online.md), [Mystery Gift](mystery_gift.md), [Wonder Cards](wonder_cards.md), [plugins](plugins.md), [releases](release.md), [code signing](code_signing.md) |

The tests:

```bash
python -m pytest -q
```
