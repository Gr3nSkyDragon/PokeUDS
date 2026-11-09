# Generation 7: Sun / Moon

PokeUDS trades with Pokemon Sun and Moon through the Festival Plaza's local Link Trade, in both directions. Everything
below was read off two Azahar logs of Sun/Moon trades between a retail 3DS and an emulated Sun/Moon, one with each
console inviting. Ultra Sun/Ultra Moon has no capture of its own: it trades with Sun/Moon, which only knows its own
network, so `profiles/usum.toml` carries Sun/Moon's values and `trade usum` runs the same session.

```bash
python -m pokeuds trade sm --radio esp32 --keys aes_keys.txt --offer my.pk7
```

```bash
python -m pokeuds trade sm --role host --radio esp32 --keys aes_keys.txt --offer my.pk7 --partner Lillie
```

- `--role join` (the default, "3DS asks"): PokeUDS joins the Festival Plaza as a guest under the trainer name. On
  the 3DS, invite it to a Link Trade; PokeUDS accepts by hosting the trade network, and the 3DS joins it.
- `--role host` ("PokeUDS asks"): PokeUDS invites the first player it hears in the plaza (or `--partner`); accept
  on the 3DS, which hosts the trade network for PokeUDS to join.
- `--offer` repeats: the trade queue, one Pokemon per trade, in order. `--trades N` as for Gen 6.
- The trainer is `--trainer/--tid/--sid`, else the app's trainer, else the offer's original trainer. The plaza knows
  a player by a principal id and an 8-byte id, which PokeUDS makes once and keeps in the settings (`plaza`), so a
  console sees the same guest every time.

## The network

| | |
|---|---|
| comm id | `0x00164810` (Sun's title id `0x1648` shifted left 8, OR `0x10`; both consoles of the captures) |
| passphrase | `GNgBwMcVotYs` and a NUL, as Generation 6 (GameFreak's gflnet default) |
| presence network | UDS `id 2`, four nodes, 200 bytes of application data, on channel 1, 6 or 11 |
| trade network | UDS `id 1`, four nodes, hosted by the INVITED player with 184 bytes of data |
| data channel | 243 |

The roles are the other way round from Generation 6's PSS, where the requester hosts.

## Festival Plaza beacons

`pokeuds/games/gen7/plaza.py` has the layout. A player's presence data is the head of their person record (the
clock, name, principal id and 8-byte id) and a 48-byte record that changes every second or so; byte 22 says which:
7-11 are idle records, 3 an invite (`token | target principal id | target 8-byte id | 01 01`), 4 an accept (`token |
inviter principal id | inviter 8-byte id`). The token is the inviter's clock (seconds since 2000). The checksum is
CRC-16/USB over everything after it, padded with zeros to 200 bytes.

## The session

Below the game the transport is Generation 6's: Pia's local header, NEX PRUDP V1 with the same checksum key, Net-Z
with one object more (twelve host messages after the first three, seven answer pairs). gflnet2 frames messages as
gflnet does with two differences: the checksum is CRC-16/USB, and the first node-pair word is `01 02 ff ff`.
`pokeuds/games/gen7/wire.py` has the message table: the profile and person blocks (module 0 commands 91 and 93),
the trade screen (module 1), the cursor (module 2), and the offer, confirmation, swap and leaving (module 3).

The templates in `pokeuds/games/gen7/templates.py` are the retail console's Net-Z messages in each role and an
emulated player's profile, person and plaza data, renamed "Dummy".

## Verified

Both directions have traded with a retail 3DS running Sun/Moon over the ESP32 board: the 3DS inviting PokeUDS, and
PokeUDS inviting the 3DS. Getting there took matching the consoles on details the simulation could not show:

- a host answers every resent authentication and association request (the ESP32 does not acknowledge them);
- the EAPoL-Logoff carries the host status `0x06` at offset 0x16 (X/Y accept a zero there, Sun/Moon do not);
- heartbeats name Net-Z object 4, and an acknowledgment carries the sender's next message index;
- a gflnet message can hold several commands: the client's "yes" is its Pokemon (module 2) and module 3 cmd 05 in
  one message, and the host starts the trade only on that 05;
- PokeUDS offers once the console's trade screen is up, answers the console's "yes" rather than going first, and
  leaves once the console is back on its trade screen after the trade.

Not captured yet: a console declining, leaving the trade screen early, and several trades in a row. PokeUDS's handling
of those follows the captured flow.

## Mystery Gift distribution

A Gen 7 local distributor opens no trade session: it hosts the Festival Plaza's comm id (0x00164810), network id **2**,
room for two, on channel 11, and carries the gift in its 200-byte beacons. Sun/Moon's Mystery Gift (Receive Gift, Get
via Local Wireless) only scans them, as for Generation 6. The fragment layout and the LZ10 full-file message
are in [mystery_gift.md](mystery_gift.md), "The Gen 7 distribution, on the air"; the message is signed with
Nintendo's Zorro/OPSSR scheme (AES-CMC + SHA-1 + RSA-768, 8-byte overhead) using the distributor's own keys,
and `gen7.delivery.opssr_sign` reproduces the real distributor's bytes exactly. `pokeuds gift distribute sm
CARD` (or `usum`) sends it.
