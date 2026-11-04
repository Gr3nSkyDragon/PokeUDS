# The UDS protocol

What NWM puts on the air, as implemented in `pokeuds/uds/`. Everything here comes from Citra/Azahar's
NWM emulation and the retail captures made for azahar_uds; the code cites the same structures.

## Beacon

Every 100 TU (102.4 ms) the host broadcasts a beacon from its MAC (also the BSSID):

| element | content |
|---|---|
| fixed | timestamp, interval 100, capabilities `0x0431` |
| SSID | eight zero bytes |
| rates | `96 98 A4 B0 C8 E0 EC` |
| DS | the channel |
| TIM, country, ERP | `00 01 01 00`, `JP 00 01 0D 14`, `00` |
| vendor 00:1F:32 type 20 | `0A 00 00` |
| vendor 00:1F:32 type 21 | network info (0x1F bytes), SHA-1, application data size, application data |
| vendor 00:1F:32 type 24 (+25) | node list, AES-CTR encrypted |

Network info, big endian: comm id (u32), id (u8), update counter (u8, +1 per join or leave),
attributes (u16), network id (u32), total nodes, max nodes, then padding. The SHA-1 covers the tag
from the OUI to the end of the application data, with the hash field zeroed.

The node list is an MD5, a u16 bitmask, then one 0x1E-byte slot per possible node (friend code
seed u64, ten UTF-16 name units, node id u16, all big endian). It is AES-CTR encrypted with the fixed
NWM key `26E4423F4B3F69F2EE94195B49FF6959`; the counter is host MAC, comm id (LE), id, a zero byte and
network id (LE). Type 24 holds the first 0xFA bytes, type 25 the rest.

A scanner sends the probe request with SSID `Nintendo_3DS_continuous_scan_000`; retail hosts answer
it.

## Keys

    CCMP key = AES-CTR(key = keyslot 0x2D normal key,
                       counter = MD5(comm id LE, network id LE, host MAC, id u16 LE),
                       data = MD5(passphrase))

The passphrase is a constant the game passes to NWM (VC Gen 1/2: `TRL_NETWORK` plus its NUL). The
keyslot 0x2D key is a console key, read from `aes_keys.txt`.

## Joining

| step | frame |
|---|---|
| client -> host | authentication, open system, seq 1 |
| host -> client | authentication seq 2; azahar_uds also sends the association response here |
| client -> host | association request: caps `0x0431`, listen 1, SSID = network id as 8 hex digits, rates `82 84 8B 0C 12 96 18 24`, ext `30 48 60 6C` |
| host -> client | association response: status 0, AID `0xC000 \| 1`, the same SSID, rates |
| client -> host | EAPoL-Start (CCMP, ToDS) |
| host | assigns the next free node id and updates its beacon **before** answering |
| host -> client | EAPoL-Logoff (CCMP, FromDS) |

EAPoL-Start (0x30 bytes after LLC): `02 01`, AID, connection type (1 client, 2 spectator), `01 00`,
node info (friend code seed, name, 4 zero bytes, node id), trailer `15 00 04 E9 13 00`.

EAPoL-Logoff (0x298 bytes): `02 02`, 2 zero bytes, assigned node id, client MAC, 6 zero bytes, nodes
connected, max nodes, 4 zero bytes, sixteen 0x28-byte node slots.

A retail console reads the next beacon right after its Logoff and leaves (deauthentication reason 3)
if the beacon still shows only the host.

## Data

Every data frame is CCMP protected (PN starting at 1, key id 0). Addressing: host to client FromDS
(A1 client, A2 host, A3 host), client to host ToDS (A1 host, A2 client, A3 destination), broadcast
NoDS (A1 broadcast, A2 sender, A3 host). The AAD masks the frame control with `0xC78F`, so a
retransmission with the retry bit still authenticates.

The plaintext is LLC/SNAP `AA AA 03 00 00 00` + EtherType: `876D` SecureData or `888E` EAPoL.

SecureData, big endian: container size and packet count (u16 each; count 0 makes a retail console
drop the frame; hosts aggregate several packets per frame), then per packet: size (counts itself),
management flag, data channel, sequence, destination node, source node, payload (at most 0x5C6 bytes).
Node 1 is the host, `0xFFFF` everyone.

A client sends a management packet on channel 3 with payload `00` every 1.1 s; the host mirrors it
back with its own sequence number. Neither is shown to the game.
