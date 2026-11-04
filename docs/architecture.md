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

### Gen 7 and Pia

Sun/Moon and USUM run their online play on Nintendo's Pia library, which also has a local mode over
UDS. If the Festival Plaza's local trade uses it, the SecureData payloads are Pia packets, which can
carry their own encryption on top of UDS's. The record finder would then see nothing in an Azahar
log; that alone would tell us Pia is in play, and the decoding layer comes next.

## Received Pokemon

As in pokeldn, each received record is written the moment it arrives (`received.save`), decrypted, in
the 232-byte box format PKHeX opens, one file per trade.
