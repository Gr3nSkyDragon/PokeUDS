# A board-hosted open access point for DS Wi-Fi Gifts

This is the design for the roadmap's way B: the ESP32 board is the access point the DS joins, and PokeUDS serves
the gift from the PC over USB. It records what the DS needs, why the network is open rather than WEP, and the
firmware and host changes that make it work.

Status: design only. Nothing here has been run on a DS or a board.

## Does the DS need WEP?

No. A Nintendo DS game card does not support WPA; Nintendo's own compatibility page says:

> Nintendo DS Game Cards are not compatible with WPA security. To use the online features of these games, you
> will need to use WEP security or **no security**.

Diamond, Pearl, Platinum, HeartGold, SoulSilver, Black, White, Black 2 and White 2 are DS-era titles; all run in DS
mode on a DS or DS Lite, and even the DSi-enhanced ones still have to serve a DS. An **open** network is valid for
every one of them. A DSi-enhanced (`TWL`) game can use WPA on a DSi or 3DS, but not on a DS/DS Lite, so open is the
one setting that works everywhere.

That matters because **the ESP32 cannot do a WEP access point**. ESP-IDF's Wi-Fi driver says:

> Auth mode of ESP AP; currently, ESP AP does not support AUTH_WEP.

Its AP modes are open, WPA/WPA2/WPA3 and WPA2-Enterprise. WEP would need a router or a PC access point. The
DS-facing AP here is therefore open, with a unique SSID and only while serving. WEP would not have protected it
either.

## Two ways to put PokeUDS behind that AP

### B1. Board as a NAT router (AP + STA)

The board runs the open SoftAP, its DHCP hands the DS an address and DNS = **the PC's LAN address**, and the
board's station joins the normal Wi-Fi. IPv4 NAPT forwards the DS's TCP 80/443 to the PC. `wfc serve` already
listens on every interface, so the **host** side needs no change. This is the least work, but it needs the player's
Wi-Fi credentials and a router both devices can reach. An `esp32_nat_router`-style mode is the model.

That "no host change" is not "no firmware change": **PokeUDS's current firmware is a raw UDS radio with no station
mode, no DHCP client and no NAPT.** B1 needs a new board firmware mode for AP+STA+NAT. `esp32_nat_router` is a
separate firmware, not PokeUDS plus a NAT add-on; it has no UDS radio, Game Boy wrapper or PokeUDS screen. It is
useful as a temporary B1 test because it already has the AP+STA+NAPT part, but it replaces the PokeUDS firmware
while it is installed.

```
DS 192.168.4.2 ──open 802.11b/g──> ESP32 AP 192.168.4.1
                                    ESP32 STA 192.168.1.50 ──WPA2──> router 192.168.1.1
                                                                       │ Ethernet
                                                                    PC 192.168.1.10
```

The ESP32 has **one radio and two virtual interfaces**: `WIFI_MODE_APSTA` gives it an AP netif and a station netif
at the same time. ESP-IDF time-slices them, and they must share a channel; the AP follows the station's channel
(if the router moves, the AP announces a channel switch). The DS talks only to the AP side; the board talks to the
router on the station side. Nothing is tunneled over USB in this mode — USB is just power and control.

The two sides are different subnets, so the board routes and NATs:

- DS: `192.168.4.2`, gateway/DNS `192.168.4.1`.
- Board AP: `192.168.4.1`; board STA: `192.168.1.50` from the router.
- PC: `192.168.1.10`, wired or wireless; it does not need Wi-Fi, only to be on the router's LAN.

When the DS connects to `192.168.1.10:443`, the board forwards the packet out of its STA interface and NAPT
rewrites the source to `192.168.1.50:port`. The PC replies to `192.168.1.50`, and the board maps it back to
`192.168.4.2`. So the PC's firewall sees the **board's** address, not the DS's; allow UDP/TCP 53 and TCP 80/443
from `192.168.1.50` (or from the board's DHCP reservation).

A wired PC is fine: the router bridges Ethernet and Wi-Fi, so `192.168.1.10` and `192.168.1.50` are on the same
LAN. What does not work is a PC on a different network from the router the board joins, a 5 GHz-only router (the
ESP32 is 2.4 GHz), or a router with wireless client/AP isolation that blocks the board from the PC. B1 also needs
the board's station credentials; if there is no Wi-Fi router at all, use B2.

If the PC is the DNS server, DHCP can hand the PC's address directly. The alternative is to point DHCP DNS at the
board and have the board answer Nintendo names itself, forwarding nothing; that saves the PC's UDP 53 firewall
rule, at the cost of a DNS responder in the firmware (which B2 needs anyway).

### Trying B1 now with `esp32_nat_router`

The PokeUDS firmware is a raw UDS radio and has no Wi-Fi client mode, so it cannot join a router. For a first B1
test, use [`esp32_nat_router`](https://github.com/martin-ger/esp32_nat_router) as a stand-in; it is a separate,
small ESP-IDF NAT-router firmware (AP+STA+DHCP+NAPT+web UI), not a PokeUDS build. It has no UDS radio, so it
cannot trade with a 3DS while installed.

1. **Flash it to the board.** Its [web installer](https://martin-ger.github.io/esp32_nat_router/) needs Chrome or
   Edge; pick the board's chip (ESP32-S3, C3, C6 or classic ESP32). This replaces PokeUDS's 2.0-uds firmware and
   can erase the board's NVS/keys. PokeUDS can put 2.0-uds back later with `python -m pokeuds board flash`.
2. **First boot.** The board offers an open Wi-Fi network named `ESP32_NAT_Router`. Join it from a phone or laptop.
3. **Open `http://192.168.4.1`.** On the Getting Started page:
   - Access Point SSID: leave it or set `PokeUDS-DS`;
   - Access Point password: **leave it blank**. A password of 8 or more characters makes a WPA2 AP, which the DS
     cannot join;
   - Uplink SSID/password: your router's **2.4 GHz** network (not a 5 GHz-only SSID). Tick “Open network” only if
     the router has no password.
4. **Save & Reboot.** The board joins the router and keeps the open AP. Reconnect to the board's AP if you changed
   its SSID.
5. **Give the DS the PC as DNS.** On the router's DHCP table, note the board's STA address (`192.168.1.50`, say).
   In the board's web UI, Configuration → Access Point → **DNS Server**, set the PC's LAN address
   (`192.168.1.10`), or from the serial console run `set_ap_dns 192.168.1.10`. Save and reboot. The DS's DHCP now
   points at the PC, so its WFC settings can use Auto-obtain DNS. A static Primary DNS = the PC's address in the DS
   menu also works.
6. **Start PokeUDS** on the PC:

   ```bash
   python -m pokeuds wfc serve --ip 192.168.1.10 --gifts dlc
   ```

   Allow UDP/TCP 53 and TCP 80/443 through the PC firewall. The DS's packets arrive from the board's STA address
   (`192.168.1.50`), not from `192.168.4.x`.
7. **On the DS:** WFC Settings → Connection Settings → search for `PokeUDS-DS` (or `ESP32_NAT_Router`), security
   **None**, Auto-obtain DNS **Yes** (or No with the PC's address). Run the connection test, then Mystery Gift.

If it does not work, the usual causes are: the DS joined a WPA2 AP (password was set), the router is 5 GHz-only,
client/AP isolation is on, the PC firewall blocks the board's address, or the board's AP DNS still points at the
router rather than the PC.

### B2. Board as an AP and a USB TCP tunnel

The board runs the open SoftAP, its DHCP hands the DS an address and DNS = **the board's own address**, and the
board tunnels TCP 80 and 443 over USB to PokeUDS. No router, no Wi-Fi credentials, and the DS cannot reach
anything except the board. This is the rest of this page.

```
DS ──open 802.11b/g──> ESP32 SoftAP 192.168.4.1
                          │  DHCP: 192.168.4.2, DNS 192.168.4.1
                          │  DNS: every A -> 192.168.4.1
                          │  TCP 80/443 accepted, framed over USB Serial/JTAG
                          ▼
                       PC: pokeduds wfc serve (HTTP + SSL 3.0/RC4)
```

The board never terminates TLS. The DS's SSL 3.0/RC4 connection is end to end with the PC; the board only moves
bytes. That is deliberate: mbedTLS 3 has neither SSL 3.0 nor RC4.

## Firmware

The firmware is VC-Adapter's `firmware/esp32-uds-bridge`. A new mode (`WFC`) is a sibling of its UDS radio mode and
its Switch LDN mode; the existing `uds_wire` framing and `send_frame()` are reused. It cannot run at the same time
as the UDS radio: one Wi-Fi mode, one radio. `WFC_START` stops the radio and switches to `WIFI_MODE_AP`.

### Access point

```c
esp_netif_create_default_wifi_ap();
wifi_config_t cfg = {
    .ap = {
        .ssid = "PokeUDS-DS", .ssid_len = 10, .channel = 6,
        .authmode = WIFI_AUTH_OPEN, .max_connection = 1,
        .beacon_interval = 100,
    },
};
esp_wifi_set_mode(WIFI_MODE_AP);
esp_wifi_set_config(WIFI_IF_AP, &cfg);
esp_wifi_set_protocol(WIFI_IF_AP, WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G);
esp_wifi_start();
```

- 802.11b/g only, channel 1/6/11, one client. The DS is a b/g client and a hidden or 11n-only AP is a needless
  risk.
- The default AP netif is `192.168.4.1/24` and runs the DHCP server. Its DHCP answer must carry option 6 (DNS) =
  `192.168.4.1`; ESP-IDF's AP DHCP server advertises itself by default, but set it explicitly so the DS never
  needs a typed DNS.
- Open. Do not set a password; a DS cannot use WPA.

### DNS

A small UDP (and TCP, for completeness) server on port 53:

- parse the question as `pokeuds/wfc/dns.py` does;
- answer every `A` under `nintendowifi.net` (or every name, which is simpler and safe on an isolated AP) with
  `192.168.4.1`;
- `AAAA` and other types: NOERROR with no answers;
- anything else: NOERROR empty, or forward if a upstream is configured (not needed for B2).

The DS resolves `conntest`, `nas` and `dls1` to the board, so all its HTTP and TLS traffic arrives at the board's
TCP listeners.

### TCP listeners and the tunnel

The board listens on TCP 80 and 443. On `accept()` it assigns a 16-bit connection id and sends a `WFC_OPEN` frame
to the host; the host connects to its local `wfc serve` port and answers `WFC_OPEN_RESULT`. Bytes are carried in
`WFC_DATA` frames, one in flight per connection, acknowledged by `WFC_ACK` (stop-and-wait). That is plenty for a
TLS handshake and a 720-byte gift, keeps the firmware small, and makes USB backpressure obvious. `WFC_CLOSE`
ends a connection in either direction; a board-side timeout closes a stalled one.

Suggested additions to `main/uds_wire.h` (same framing, new types):

| type | direction | payload |
|---|---|---|
| `UDS_CMD_WFC_START` `0x20` | host -> board | `{ssid_len:u8, ssid, channel:u8}` |
| `UDS_CMD_WFC_STOP` `0x21` | host -> board | `{}` |
| `UDS_EVT_WFC_STATE` `0xA0` | board -> host | `{state:u8, ip[4], clients:u8}` |
| `UDS_EVT_WFC_OPEN` `0xA1` | board -> host | `{conn:u16 LE, port:u16 LE, addr[4]}` |
| `UDS_CMD_WFC_OPEN_RESULT` `0xA2` | host -> board | `{conn:u16 LE, ok:u8}` |
| `UDS_CMD_WFC_DATA` `0xA3` | both | `{conn:u16 LE, seq:u16 LE, bytes...}` |
| `UDS_CMD_WFC_ACK` `0xA4` | both | `{conn:u16 LE, seq:u16 LE}` |
| `UDS_CMD_WFC_CLOSE` `0xA5` | both | `{conn:u16 LE, reason:u8}` |

`WFC_DATA` fits the existing 2432-byte payload: 4 bytes of header, up to ~2400 bytes of stream. The host treats
the payload as opaque and writes it to a socket; it never parses HTTP or TLS. A connection is bound to one local
port (80 or 443), chosen by the port the DS connected to.

### Firmware tasks

- `wfc_ap.c` / `wfc_ap.h`: AP start/stop, DNS, TCP accept and per-connection relay state.
- `main.c`: `WFC_START`/`WFC_STOP` cases, refuse the UDS radio commands while WFC runs (and the reverse), send
  `WFC_STATE`, route `WFC_*` frames to `wfc_ap`.
- `uds_wire.h`: the types above; the host decoder is already generic.
- `display.c`: a `WFC` scene (SSID, DS connected, bytes moved) so the board shows what it is doing.

Memory: AP + DHCP + DNS + two listeners + per-connection buffers is small. The S3 is the first target; a classic
ESP32 has enough RAM but its 921600-baud UART is the throughput question. C3/C6 are later.

## Host side

`pokeuds/wfc/tunnel.py` would own the serial link:

- open the board with the existing `pokeuds.radio.esp32.open_serial`/`find_port` logic (native USB first, then
  CP210x/CH340);
- send `HELLO`, wait for `HELLO_ACK`, then `WFC_START`;
- read frames with `uds_wire.Decoder`; on `WFC_OPEN` connect to `127.0.0.1:<http_port or https_port>`;
- relay `WFC_DATA` both ways, with the one-frame window; `WFC_CLOSE` on EOF;
- log `WFC_STATE` and board `LOG` frames.

`wfc serve --tunnel COM5` would start the tunnel in a thread next to the normal listeners, binding the WFC server
to loopback (the board is the only client). No `--ip`, no DNS typing: the board's DHCP and DNS do it. The same
server code, certificate and gift folders stay as they are.

The tunnel is optional; `wfc serve` without `--tunnel` is unchanged for the router/open-AP setup.

## Test plan

1. Board alone: AP visible, DS associates, gets `192.168.4.x`, DNS answers `nas.nintendowifi.net` with
   `192.168.4.1`.
2. Host: `wfc serve --tunnel` sees `WFC_STATE` and a `WFC_OPEN` for port 80; the DS's connection test returns `ok`.
3. A Gen 4 and a Gen 5 game: NAS login, DLS1 list and contents, gift received.
4. Pull the USB cable mid-handshake and reconnect: the board drops the connections, the host cleans up, a second
   attempt works.
5. Throughput: time a gift download and a TLS handshake on the S3 (native USB) and, if built, a classic ESP32
   (921600 UART).

## Open questions

- Does the DS associate cleanly with an ESP32 open AP at 802.11b/g, and how long does the first DHCP/DNS take?
- Does the DS's WFC client tolerate the board's TCP stack (window, retransmit) during the SSL 3.0 handshake?
- Is one-frame stop-and-wait fast enough for the largest DLS1 file (720 bytes here, but PWT files can be larger)?
- Does the board's AP stay up while the USB Serial/JTAG link is busy, on each chip?
- Is the open AP acceptable in practice, or should the mode also offer B1 so the DS never sees an isolated AP?

## Recommendation

If a router is available, **B1 (AP+STA NAT)** is much less work: the PC's existing `wfc serve` is the whole host
side, and the board only needs AP+STA+NAPT. **B2** is the right choice when the board must work without a router
or when the DS must be isolated; it is the design above, and it is a firmware project of its own.

## Sources

- Nintendo Support, [Game Card Wireless Security Compatibility](https://en-americas-support.nintendo.com/app/answers/detail/a_id/677)
  (“Nintendo DS Game Cards are not compatible with WPA security… WEP security or no security”).
- Espressif, [Wi-Fi Driver](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/wifi.html)
  (`WIFI_AUTH_WEP` is not an AP mode: “currently, ESP AP does not support AUTH_WEP”).
- [martin-ger/esp32_nat_router](https://github.com/martin-ger/esp32_nat_router) for the AP+STA+NAPT pattern.
- VC-Adapter's `firmware/esp32-uds-bridge` (`main.c`, `uds_wire.h`) for the framing this design extends.
