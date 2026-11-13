# DS Wi-Fi Mystery Gifts (Gen 4 and 5)

Diamond, Pearl, Platinum, HeartGold, SoulSilver, Black, White, Black 2 and White 2 download their Wi-Fi Mystery
Gifts from Nintendo's servers: `nas.nintendowifi.net` for the login and `dls1.nintendowifi.net` for the gift file.
Nintendo WFC closed in 2014. This page describes PokeUDS's stand-in for those two servers, so a retail DS can
receive an official event card again.

It is the same trick Wiimmfi, Kaeru WFC, AltWFC and PokeGateway use, and it changes nothing on the DS: the player
sets the console's DNS to this computer and the games resolve Nintendo's names here. No game or save is patched.

**Status: the servers are written and unit-tested; not yet run against a DS.** The values marked *unmeasured* below
are the ones only a console can settle. See "What has been measured" at the end before treating it as working.

## What the DS does

| Step | Host | Port | What | Answered by |
|---|---|---|---|---|
| 1 | `conntest.nintendowifi.net` | 80 | `GET /`; the WFC settings test and the game's own check | `ok` |
| 2 | `nas.nintendowifi.net` | 443, SSL 3.0 | `POST /ac`, `action=acctcreate` (an account), then `login` (a challenge and token) | `pokeuds.wfc.nas` |
| 3 | `nas.nintendowifi.net` | 443 | `POST /ac`, `action=svcloc`, `svc=9000`/`9001`: where the download service is | `pokeuds.wfc.nas` |
| 4 | `dls1.nintendowifi.net` | 443, SSL 3.0 | `POST /download`, `action=count`, `list`, `contents` | `pokeuds.wfc.dls1` |

All four names resolve to this computer, so no request leaves the LAN. The DNS server also forwards names it does
not own (or answers them NXDOMAIN), so the DS can keep it configured for everything else.

### DNS

A DS's WFC connection settings hold a Primary and a Secondary DNS. Set **both** to this computer: if the secondary
is a public resolver, the DS may use it for one of Nintendo's names and get the old, discontinued address instead of
PokeUDS. There is no DHCP option to set from PokeUDS's side; the player types it on the console.

The DNS server (`pokeuds.wfc.dns`) answers `A` queries for every name under `nintendowifi.net` and
`nintendo.co.jp` with `--ip` (this computer's LAN address by default). `AAAA` and other types get an empty
`NOERROR` so the console falls back to IPv4. Other names are forwarded to `--dns-upstream` when given, else
`NXDOMAIN`. `--dns-all` answers every name, which is simpler but takes over the console's whole DNS.

UDP is what the DS uses. TCP is served too because a DNS client retries over TCP after a truncated reply.

### HTTP

The NAS client rejects HTTP/1.0 as bad data (DWC error 25101), so NAS replies are HTTP/1.1; the DLS1 download
client was tested against HTTP/1.0, so its replies are 1.0. Every reply carries a Content-Length and closes the
connection; the DS sends one request per connection and never uses chunked encoding.

### NAS

Every value on the wire is Nintendo's flavour of base64, percent-encoded as a form:

| Standard base64 | Nintendo's |
|---|---|
| `+` | `.` |
| `/` | `-` |
| `=` | `*` |

(`pokeuds.wfc.nas.decode_value` also reads AltWFC's older `>` for `+` and `?` for `/`.) The reply starts with
`returncd`, then `datetime` (`yyMMddHHmmss`), then the action's fields, joined by `&` with no trailing newline.

| action | request fields (after decoding) | reply |
|---|---|---|
| `acctcreate` | `userid` (may be empty), `macadr`, `ingamesn`/`devname`, `gsbrcd` | `returncd=002`, `retry=0`, a numeric `userid` |
| `login` | `userid`, `macadr`, `ingamesn`/`devname`, `gsbrcd`, `devtime` | `returncd=001`, `locator=gamespy.com`, an 8-character `challenge`, a 16-character `token` |
| `svcloc` | `userid`, `gamecd`, `svc`, `token` | `svchost=dls1.nintendowifi.net`, and the credential |

Gen 4 and Gen 5 differ in the `svcloc` answer in the wild. PokeUDS follows the two servers known to work:

- **Gen 4** (`gamecd` `ADA*`, `APA*`, `CPU*`, `IPG*`, `IPK*`), as AltWFC answers: `returncd=007`, `retry=0`,
  `statusdata=Y`, and `token` for `svc=9000` or `servicetoken` for `svc=9001`.
- **Gen 5** (`gamecd` `IRA*`), as Entralinked answers: `returncd=001`, `statusdata=Y`, no `retry`, and the
  credential under `servicetoken`.

`/pr` is the profanity filter; every word is accepted (`returncd=000` and zeros). `GET /` is the conntest page,
answered `ok` with `X-Organization: Nintendo` and `Server: BigIP`, as AltWFC does.

The `token` is kept with the console's `devtime` (its clock, which the DS sends at login), because DLS1 uses the day
of the year to choose a gift. It is not persisted; a restart only loses the date until the next login.

### DLS1

`POST /download` with the same base64 form. `X-DLS-Host` is sent back; gift replies use
`Content-Type: application/x-dsdl` and `Content-Disposition: attachment`.

| action | what it does |
|---|---|
| `count` | Gen 4: `1` when the folder has a gift. Gen 5: the number of gifts this game may receive. Other downloads: the number matching the attributes |
| `list` | one tab-separated line per gift: `name`, description, `attr1`, `attr2`, `attr3`, size; lines end `\r\n`, and an empty list is a bare `\r\n` |
| `contents` | the named file's bytes |

A gift folder is a game-code folder under `--gifts`:

```
gifts/ADA/10dp.myg               Gen 4 Diamond/Pearl (also used for a region's ADA*)
gifts/APA/...                    Pearl, when its folder exists
gifts/CPU/17p.myg                Platinum
gifts/IPG/48gs.myg               HeartGold/SoulSilver
gifts/IRA/...
gifts/IRAO/G0028_darkrai_en.bin  Gen 5 Black/White/Black 2/White 2
```

A four-letter folder is used when it exists; otherwise the three-letter shared folder (`ADA`, `CPU`, `IPG`, `IRA`)
is. That lets one export serve every region of a game. The folder may contain a `_list.txt` of six tab-separated
fields per line; without one, every file in it is offered in name order with blank attributes.

**Gen 4** offers exactly one gift per connection, chosen by the DS's day of the year: `(day_of_year - 1) % gifts`.
Changing the DS clock chooses another. This is why a Gen 4 folder may hold every card; the game only sees one.
`--gift NAME` overrides the date and always serves that file.

**Gen 5** ignores which cartridge asked and always sends `gamecd=IRAO`, `IRAJ` or `IRAK`; the real game is in
`rhgamecd`, and its **third letter** is what matters: `A` White, `B` Black, `D` White 2, `E` Black 2. The
request's `attr1` is `MYSTERY_<language letter>` (`E`, `F`, `I`, `G`, `S`, `J`, `K`). PokeUDS:

1. keeps entries whose non-blank requested attributes match (`attr1`, `attr2`, `attr3`);
2. for `MYSTERY_*`, keeps those whose `attr2`, read as hex, has the game's bit: `0x100000` White, `0x200000`
   Black, `0x400000` White 2, `0x800000` Black 2 (`0xF00000` all four);
3. picks one by the DS's day of the year.

Other Gen 5 files (the PWT tournaments, `WBT_*`, C-Gear skins) use `num` and `offset` as ordinary DLC. PokeUDS
filters and slices them the same way AltWFC does.

### The gift files

PokeUDS ships no Nintendo files. `services/pkhex/Gifts.cs` builds each official Gen 4/5 card PKHeX.Core embeds into
the file the game downloads (docs/mystery_gift.md, "DS Wi-Fi gift files"): a 936-byte `.myg` for Gen 4, a 720-byte
`.bin` for Gen 5. `pokeuds wfc gifts` writes them into the layout above and writes `_list.txt` for each folder:

```bash
python -m pokeuds wfc gifts dlc dp pt hgss bw b2w2
```

It asks the PKHeX helper for every card, so build or download the helper first (`python -m pokeuds pkhex status`).
Gen 4 cards go to every game whose version they reach; Gen 5 cards all go to `IRA`. A Gen 5 card limited to one
language is written once with `MYSTERY_<that letter>`; a card any language may receive is written once per
`--languages` letter (default `E,F,I,G,S`), with the distribution text left in English because PKHeX carries no
translated text. Use `--languages E` to keep a folder small.

The Wiimmfi/AltWFC `dlc` collection can be copied straight in instead, `_list.txt` files included. Its Gen 5
`_list.txt` attributes and file names are already what DLS1 expects.

## The certificate

The DS verifies the server certificate against Nintendo's roots, but it does not check whether an intermediate
certificate is allowed to sign others. This is "nds-constrain't" (shutterbug2000, 2018): any certificate Nintendo
signed can act as a CA. The Wii's built-in SSL **client** certificate, "Wii NWC Prod 1" (issuer "Nintendo CA",
serial `0x1A`), is signed by Nintendo and is the same on every Wii. A server certificate signed with its private
key, sent with the Wii certificate in the chain, is trusted by the DS:

```
server certificate (CN *.*.*, RSA 1024, SHA-1)
  -> Wii NWC Prod 1 (the player's clientca.pem)
  -> Nintendo CA (a DS root)
```

What PokeUDS needs is the Wii's client certificate and its private key:

- **Dolphin 5.0-5179 or newer** extracts them itself: Tools > Perform Online System Update. They land in the `Wii`
  folder of Dolphin's user directory as `clientca.pem` and `clientcakey.pem`.
- Or decrypt IOS13 (title `00000001-0000000D`), content `00000011.app`, and run Dolphin's `extract_certs.exe` on
  it; or WiiCertDump on the IOS13 `.app` from a NAND dump.

PokeUDS checks the pair against the SHA-256 Dolphin checks by, so it can tell the real files from something else:

| file | SHA-256 |
|---|---|
| `clientca.pem` | `229ec678525e060588e8ea23e5459ec14af3c2ebb7b9e69ec46b0faf011730d9` |
| `clientcakey.pem` | `723be9b32c3afb83a4a3757adf352529e90c0ad6fad52509963ba8942ae625df` |

```bash
python -m pokeuds wfc check --cert clientca.pem --key clientcakey.pem
```

It prints the subject and issuer, whether the hashes match, and makes (once) a server certificate in the data
folder's `wfc/server.pem` + `server.key`. A different pair is used with a warning; only the real Wii certificate is
vouched for by Nintendo's root. The private key is written unencrypted: it is a key, so keep the data folder private.

The server certificate has CN `*.*.*` (a wildcard for every three-label name, which covers `nas`,
`conntest` and `dls1`; flewkey's write-up), RSA 1024 and SHA-1 (what the DS's SSL library accepts), and is valid
from 2000 so a DS with a dead clock battery is not told the certificate is from the future. `--cn
nas.nintendowifi.net` makes one for a single host instead.

`rootca.pem`, the Wii's own trust store, is not needed.

### SSL 3.0 and RC4

The DS speaks SSL 3.0 with RC4-SHA or RC4-MD5, and nothing newer. Python's `ssl` module (OpenSSL 3) refuses both,
so the TLS is done by **tlslite-ng** (pure Python, LGPL-2.1), which still offers SSL 3.0 and RC4 as opt-ins:

```bash
python -m pip install tlslite-ng
```

or `python -m pip install -r requirements-wfc.txt`. The rest of PokeUDS does not need it; `wfc serve` says so if it
is missing. `pokeuds.wfc.tls` sets `minVersion=maxVersion=(3, 0)`, `cipherNames=["rc4"]`,
`macNames=["sha", "md5"]` and RSA key exchange.

The DS does not send `close_notify` when it is done, so a connection that ends without one is normal; PokeUDS
answers the request and closes. SSL 3.0 has no BEAST countermeasure to get in the way (that is a TLS 1.0 concern),
and the DS's SSL library cannot handle an empty fragment, so none is sent.

## Running it

```bash
python -m pokeuds wfc serve --cert clientca.pem --key clientcakey.pem --gifts dlc
```

```
DS Wi-Fi Mystery Gift server running.
  DNS:        192.168.1.20:53
  conntest:   http://192.168.1.20:80/
  NAS/DLS1:   https://192.168.1.20:443/ (SSL 3.0, RC4)
  certificate: *.*.* (*.*.* -> Wii NWC Prod 1 -> Nintendo CA)
  gifts:      dlc

On the DS: Nintendo WFC Settings > Connection Settings > your connection > Auto-obtain DNS: No,
Primary DNS 192.168.1.20, Secondary DNS 192.168.1.20 (or a normal resolver).
...
```

On the DS:

1. Put the DS on an **open or WEP** network. A DS game accepts only those, even on a DSi or 3DS; Windows' Mobile
   Hotspot is WPA2 and will not work. An old router with WEP, or a phone that can open a hotspot, is the usual
   answer.
2. Nintendo WFC Settings > Connection Settings > the connection > Auto-obtain DNS: No, Primary DNS the address
   above (Secondary: the same, or a normal resolver). Run the connection test; it should succeed.
3. In the game: Mystery Gift > **Receive via Wi-Fi** (Gen 4) or **Receive via Nintendo WFC** (Gen 5), then have it
   look for a gift.

The PC and the DS must be on the same LAN, and the firewall must allow UDP and TCP 53 and TCP 80 and 443. Windows
normally asks the first time; allow PokeUDS on private networks. `--dns-port`, `--http-port` and `--https-port`
move the listeners if something already holds a port (Internet Connection Sharing and Hyper-V often hold 53).
`--no-dns` is for a network that already redirects the names (a router running a DNS rule, a Pi-hole), `--no-http`
for a separate conntest, `--no-tls` for trying NAS/DLS1 over plain HTTP on port 80 (not a DS setup).

### A local access point (the board)

A PC alone needs an existing open or WEP network for the DS. The other way is to let the ESP32 board be that
network, so the player only joins the board. [docs/ds_wifi_ap.md](ds_wifi_ap.md) has the full design; in short:

- **The AP has to be open, not WEP.** ESP-IDF's SoftAP does not implement WEP ("currently, ESP AP does not support
  AUTH_WEP", Wi-Fi Driver docs); its AP modes are open, WPA/WPA2/WPA3 and WPA2-Enterprise. The DS accepts open, so
  open works. If WEP is required, it has to come from a router or a PC access point, not the ESP32. Treat the open
  AP as a short-range, temporary network: use a unique SSID, run it only while serving, and remember WEP would not
  have protected it either.
- **NAT router mode (AP+STA), no USB data path.** The board runs the open SoftAP; its DHCP hands the DS an address
  and DNS = the PC's LAN address. The board's station joins the normal WPA2 Wi-Fi, and IPv4 NAPT forwards the DS's
  TCP 80/443 to the PC. `wfc serve` already listens on every interface, so nothing changes on the host. This needs
  AP+STA coexistence (they share one radio and channel), the player's Wi-Fi credentials, and a board firmware mode
  like `esp32_nat_router`. It is the least moving parts.
- **USB tunnel mode.** The board answers DNS with its own address and forwards TCP 80/443 over USB to PokeUDS.
  No Wi-Fi credentials or router needed, but the board cannot terminate TLS itself (mbedTLS 3 has neither SSL 3.0
  nor RC4), so it must tunnel the bytes to the PC. This is the roadmap's original way B and needs a small protocol
  plus a host-side listener.

Either way the DS's WFC settings can use **Auto-obtain DNS: Yes**, because the board's DHCP server supplies the DNS
address. A static Primary DNS also works, but it must point at an address the DS can reach: the PC in NAT mode, the
board in tunnel mode. The board cannot run this mode and the 3DS UDS trade mode at the same time.

## Troubleshooting

| what the DS shows | what to look at |
|---|---|
| connection test fails (no DNS) | wrong Primary DNS, DS not on the LAN, UDP 53 blocked, ICS/Hyper-V holding 53 |
| error 25101 or "bad data" at login | NAS must be HTTP/1.1; check the server log for the request. A proxy or antivirus that rewrites HTTP will break it |
| error 20110 / 20109 / 20100 | the DS did not reach NAS (firewall, wrong IP, certificate refused). The log shows whether the TLS handshake happened |
| login works, gift list is empty | the gift folder is empty or named for another game code; `--gift NAME` to test, and check the log's `gamecd`/`rhgamecd` |
| a gift arrives but is the wrong one | Gen 4 picks by day of year; change the DS date or use `--gift` |
| a Gen 5 gift is not offered | `attr1` is the game's language; `attr2` must have the game's bit; `rhgamecd` must name that game |
| TLS handshake fails | tlslite-ng not installed, or an antivirus intercepting 443; the log says which |

PokeUDS logs every DNS answer and every NAS/DLS1 request at INFO. `-v` adds the details.

## What has been measured

- **Built and tested here:** DNS answers and forwarding; the base64 codec; the NAS login, `acctcreate` and
  `svcloc` for both generations; DLS1 count/list/contents, the Gen 4 date choice, the Gen 5 game mask and language
  filter, `--gift`, and path-traversal refusal; certificate parsing, chain signing and the server timestamp. The
  tests are `tests/test_wfc.py`.
- **Verified against the player's own files:** `clientca.pem` / `clientcakey.pem` with Dolphin's two SHA-256s; the
  generated server certificate verifies against the Wii certificate's public key (RSA/SHA-1, PKCS#1 v1.5).
- **Not tried against a DS** (docs/mystery_gift.md and POKEUDS_ROADMAP.md mark these too): SSL 3.0 + RC4 through
  tlslite-ng; whether the DS accepts CN `*.*.*`; the NAS field order and HTTP versions; whether Gen 4 wants
  `returncd` 007 with `token` (AltWFC's) or 001 with `servicetoken` (Entralinked's); whether a Gen 5 game accepts
  the `servicetoken` reply; the exact `rhgamecd` for each Gen 5 game and region; the three zero bytes in the Gen 5
  `.bin`; and the games' own checks on the gift files.

The roadmap's other way to carry the network, a board running an open SoftAP and tunnelling TCP 80/443 over USB,
would remove the open/WEP router and the DNS typing. It is not implemented; the servers here are what it would
tunnel to.

## Why this is not "impersonating Nintendo"

Nothing here contacts Nintendo: the DNS answers point at the player's own computer, the certificate is made from a
certificate and key already in the player's Wii, and no Nintendo key or gift file is shipped with PokeUDS. It is the
same preservation work as Wiimmfi, Kaeru WFC and AltWFC, for games whose official service has been off since 2014.
Use it with your own console, your own dump and your own save. The code does not implement account creation on
Nintendo's servers, online play, or anything that would touch a live service.

Protocol facts come from [dwc_network_server_emulator](https://github.com/barronwaffles/dwc_network_server_emulator)
(AGPL-3.0), [nds-constraint](https://github.com/KaeruTeam/nds-constraint),
[PokeGateway](https://github.com/ScarlettHaze/PokeGateway) (no licence; read for facts, no code copied),
[Entralinked](https://github.com/kuroppoi/entralinked) and the [DLS1-Files](https://github.com/InternalLoss/DLS1-Files)
collection. The certificate hash check follows Dolphin's `Source/Core/Core/IOS/Network/SSL.cpp`.
