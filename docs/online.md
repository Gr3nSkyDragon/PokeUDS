# Online: the GTS and online trades

PokeUDS trades over the internet in two ways:

| | what it is | state |
|---|---|---|
| **The GTS** | list a banked Pokemon against what you want; anyone can offer on it, at any time. Bank to bank, no console needed at either end. | working: a GTS trade run against the public relays (2026-11-14) |
| **Online trades** | two players trade live, each with their own 3DS beside their own PokeUDS. Each board hosts a local trade; the two apps carry the offers between them. | X/Y, ORAS, Sun/Moon, USUM and the Virtual Console games: in the command line (`--online`, `--online-code`) and on the Trade page (the Online switch); tested in simulation (two consoles, two PokeUDS, one relay); **never run on hardware** |

Both ride on [Nostr](https://github.com/nostr-protocol/nips) relays: public servers that keep and pass on signed
JSON events. Nobody runs a PokeUDS server, there is no account, and no relay has to be trusted with anything but
passing events along. This is pokeldn's design; PokeUDS uses its own tags and key prefixes, so the two apps never
see each other's listings or rooms.

Game Boy Pokemon of every language go on the GTS, with their language. Live online trades for the Virtual
Console meet within a language family (international, Japanese, Korean); across families they would also need the
cross-language conversion the local trades already do ([vc.md](vc.md), Languages).

## The pieces

| file | what it does |
|---|---|
| `pokeuds/online/schnorr.py` | secp256k1 keys, BIP-340 signatures, the shared secret of two keys. Pure Python; checked against BIP-340's vectors and `cryptography`'s ECDH. |
| `pokeuds/online/relays.py` | events (NIP-01) signed and checked; a small WebSocket client; `Pool`, which talks to several relays at once and believes only correctly signed events that match what it asked for; `LivePool`, relays kept open with standing subscriptions, for a live trade. |
| `pokeuds/online/gts.py` | the GTS's events: listing, offer, answer, deletion; sealing. Touches neither the bank nor a relay. |
| `pokeuds/app/gts.py` | the GTS against the bank: what leaves it, what joins it, and the round that settles everything. |
| `gui/views/gts.py` | the GTS page. |
| `pokeuds/online/link.py` | an online trade's partner: the room, meeting, the pair channel (ported from pokeldn). |
| `pokeuds/online/trade.py` | the partner as a trade's offer source (`Offers`), and the PKHeX check of a partner's record (`checker`). |
| `pokeuds/games/offers.py` | where a Gen 6/7 trade's offers come from: the local queue, or an online partner. |
| `tests/fake_relay.py` | a relay for the tests: the store, a pool over it, and the store behind a real loopback WebSocket that also passes new events to open subscriptions. |

Relays: seven public relays by default (`relays.DEFAULT_RELAYS`). `POKEUDS_GTS_RELAYS=wss://a,wss://b` replaces
them, for a private relay or a test. A relay that refuses a connection is left alone for a minute. A read-only
check from Windows reached six of the seven in under three seconds (one answered HTTP 503).

## The GTS

### Events

| event | kind | signed by | content |
|---|---|---|---|
| listing | 30402 (NIP-99 classified), `d` a random id | a key made for this listing | JSON: the Pokemon as shown (with a `.pk1`/`.pk2`'s game language), the SHA-256 of its record, one to three wants, status |
| offer | 1059, `p` the listing's key | a key made for this offer | sealed: the listing's id, the offered record (with a `.pk1`/`.pk2`'s game language), the trainer's name |
| answer | 1059, `p` the offer's key | the listing's key | sealed: the offer's id, and the listed record, or why not |
| deletion | 5 (NIP-09) | the key of the event it names | none |

A sealed body is AES-256-GCM under `sha256("pokeuds gts|" + x)`, where x is the shared secret of the two keys:
only those two keys read it. Every key is made for one listing or one offer, so nothing ties two of them to the
same player. Listings expire after 30 days (NIP-40 `expiration`); offers and answers a week after their listing.

A listing is addressable: published again with the same key and `d`, it replaces the one before on every relay.
Its status moves `open` → `traded` (naming the winning offer) or `withdrawn`. Tags let an app ask a relay for
just one format, or the listings that have or want one species: `pokeuds-gts-pk7`, `pokeuds-gts-has-pk7-25`,
`pokeuds-gts-wants-pk7-133`.

Formats are `.pk1`, `.pk2`, `.pk6`, `.pk7`: a listing trades one format, and only that format may answer it.
(A Gen 1 Pokemon for a Gen 7 one is a Transporter migration followed by a `.pk7` listing, done in the bank first.)

### A trade, step by step

| step | where | what happens |
|---|---|---|
| list | lister | PKHeX must find the Pokemon legal. It leaves the bank into the bank's hidden `.gts/listing-<id>/`; the listing is published. |
| browse | anyone | the newest version of each listing, open and not expired; the page shows which banked Pokemon fit. |
| offer | offerer | legal by PKHeX, of the listing's format and one of its wants (species, level range, shiny). It leaves the bank into `.gts/offer-<id>/`; the offer is sealed to the listing's key. One waiting offer per listing; never on your own listing, nor on one of another copy of PokeUDS on this computer (Two copies on one computer). |
| the lister's app opens | lister | the offers on the relays are answered oldest first. The first one that is valid (readable, the right listing and format, a real record, legal by PKHeX, what the listing asks for) is traded: the answer carries the listed record, the listing is republished as `traded`, the offered Pokemon joins the bank. Every other offer is refused, saying why. |
| the offering app opens | offerer | the answer's record must match the listing's SHA-256; it joins the bank. A refusal returns the offered Pokemon. With no answer, the listing itself is read: withdrawn, traded to someone else, or ended returns the Pokemon too. The offer is then deleted from the relays. |
| a listing ends | lister | taken down, or 30 days without a trade: the Pokemon returns to the bank, and later offers are refused. |

Rounds happen when the app starts (if anything is live), when the GTS page opens, after each list or offer,
and on "Check now". There is no background service: a trade waits until the lister's app next opens.

### Two copies on one computer

The packaged app keeps its bank beside PokeUDS.exe (`Bank`), an older or from-source copy in `%APPDATA%\PokeUDS\bank`,
and each settles only its own `.gts`. Neither may trade with the other, which would only move a Pokemon between your
own banks. The GTS page marks as **Yours** (its tooltip says why) and refuses an offer on:

* a listing in this copy's `.gts`, or in the other bank's (the packaged app finds `%APPDATA%\PokeUDS\bank`);
* a listing whose Pokemon (its SHA-256) is already a file in either bank, a GTS folder, or the received folder both
  copies write to, which is how a from-source copy recognises the packaged app's listings.

And a lister's app refuses an offer whose Pokemon already sits in a bank or GTS folder on its computer (an older copy
may still send one): it goes back to the offering copy at its next round.

### Nothing is lost

* Each listing and offer is a folder in the bank's hidden `.gts` (so two copies of the app, each with its own
  bank, never settle each other's listings) holding `state.json` (its key, its events, the answers sent) and the record
  that left the bank. A step is written to the folder before its event is published.
* Every live event is published again each round: a relay that forgot, or a round with no relay reachable,
  costs nothing but time.
* A crash after a trade was decided but before the Pokemon reached the bank is finished on the next round
  (the received record is kept in the folder); a returned Pokemon that never reached the bank is given back then.
* A Pokemon that left the bank keeps its note, origin, language and legality verdict, and returns with them.
* A `.pk1`/`.pk2` does not hold its game's language, so the listing and the offer carry it: what a trade
  brings is banked with the language of the game it came from (Japanese, Korean or one of the international
  five), as a received one is ([vc.md](vc.md), Languages).
* Finished folders are deleted 60 days after they end.

### What it cannot promise

A fair exchange with no trusted third party is impossible. A changed app can keep an offered Pokemon, or answer
with a Pokemon it never listed; the SHA-256 catches the second only afterwards, and the page says so ("Lost: the
lister's app sent a Pokemon that is not the one listed"). The GTS page says this before anyone lists. Only legal
Pokemon go on the GTS, checked by PKHeX at both ends, so a hacked Pokemon is refused by an honest app either way.

### Tested, and not

`tests/test_gts.py` runs four apps with separate banks over one relay store: the whole trade, what may not be
listed or offered, races (the first valid offer wins; late offers come back), offers that a checking app refuses,
withdrawal, expiry and clean-up, a lister that sends the wrong Pokemon, relays that are down or forget everything,
and crashes mid-trade; and the page headless. `tests/test_online.py` covers the signatures and the relay client
against loopback WebSocket relays.

A GTS trade has since run against the public relays and worked (2026-11-14). Still unmeasured: relay limits on
event size (a listing is about 1 KB, an offer about 0.5 KB), how long the relays keep kind 1059 events, and how
they treat NIP-40.

## Online trades

A live trade between two players, each with a 3DS and a board. **Each side is an ordinary local trade**: PokeUDS
hosts the 3DS's trade over its own board, exactly as today, and the two apps pass what each 3DS offers to the
other over the relays. Nothing changes on the 3DS side. This is how pokeldn trades online (its `docs/online.md`
and `pokeldn/online/link.py`, which `pokeuds/online/link.py` is ported from); pokeldn does it for its own games
(FireRed/LeafGreen, Let's Go, Sword/Shield, BDSP, Legends, Scarlet/Violet, Z-A), so the Gen 6/7 and VC parts
below are PokeUDS's own.

### Meeting (as pokeldn)

* A **room** is `sha256("pokeuds online 1|<room>|<code or 'open'>")`: `<room>` is `gen7` (Sun/Moon with
  USUM), `gen6` (X/Y with ORAS), `gb1` and `gb2` (the Virtual Console, below). No game has a link code, so a code typed in the
  app makes a private room, and none meets anyone else waiting without one.
* Every event is ephemeral kind 21059 (relays pass it on and keep nothing), tagged with the room, sealed under
  `sha256("pokeuds room key|" + room)`, signed by a key made for the session.
* Each app sends `here` every 2.5 s (its X25519 key, trainer name, version, busy). The lower signing key invites
  the longest-seen free app above it, one at a time for up to 6 s; `yes`/`no` answers. A refused invite skips
  that app for 10 s.
* Paired, the two talk on a **pair channel**: an inner AES-GCM under a key from both apps' X25519 keys
  (`pokeuds pair|` + room + both keys + their shared secret). Every message is numbered and acknowledged,
  resent every 2 s until acknowledged, with a ping every 4 s; a partner silent for 45 s is lost (and replaced, if
  nothing was offered yet).
* The relays are kept open (`relays.LivePool`, one thread per relay, reconnecting after 2 s and up to 30 s).
  By default the six relays pokeldn measured passing 8 of 8 ephemeral events (`relays.LIVE_RELAYS`);
  `POKEUDS_RELAYS=wss://a,wss://b` replaces them.

### Trading

| message | meaning |
|---|---|
| `offer` / `withdraw` | trade *r*: this player's 3DS put this record up, or took it back |
| `accept` / `unaccept` | trade *r*: this player's 3DS said yes, or changed its mind. `accept` carries `h`, the first 32 hex digits of the SHA-256 of the record the 3DS said yes to; a yes naming another record than the one now offered is dropped |
| `refused` | trade *r*: this app will not show the other's record to its 3DS, and why |
| `done` | trade *r* finished on this side; the next is *r* + 1 |

`h` is PokeUDS's addition to pokeldn's protocol: without it, a 3DS that took its offer back and offered again
before the relays carried the withdrawal could meet the partner's yes to the first offer, and one side's trade
would complete alone.

Each app checks the partner's record with PKHeX, as **its own 3DS's game** (`online.trade.checker`), before its 3DS
sees it: one that is not a Gen 6/7 record, that PKHeX cannot read, or with an item or move the game never released,
is refused (the sender is told why); any other legality flag reaches the receiver as a warning. So an Omega Ruby
Pokemon holding what X never had never reaches an X. Without PKHeX set up, nothing can be checked, so every record
is refused. Each app says yes to its own 3DS only once **both** 3DSs have said yes, so neither trade completes
alone.

### How a trade takes its partner

A Gen 6/7 session takes its offers from an **offer source** (`pokeuds/games/offers.py`). Every call returns at
once; while it waits on one, the session keeps its 3DS's connection alive (heartbeats, syncs), so the 3DS never
times out on PokeUDS.

| | the local queue (`offers.Queue`) | an online partner (`online.trade.Offers`) |
|---|---|---|
| PokeUDS's offer | the next queued record, offered first | the partner's 3DS's record, offered once this 3DS has offered (its offer goes to the partner) and the partner's has come |
| PokeUDS's yes | at once | once this 3DS said yes and the partner's 3DS said yes to this 3DS's offer |
| a decline (either side) | offer again | both sides offer afresh: the partner's standing offer is not shown again |
| PokeUDS leaves the trade screen | after `trades` trades | when the partner has left or been lost |

| | the partner's offer goes to the 3DS as | the 3DS's offer goes out from | the partner taking its offer back becomes |
|---|---|---|---|
| Sun / Moon, USUM | module 1 cmd 00 (shown, then chosen) with module 2 cmd 01, then module 3 cmd 00 (the offer), once the console's own offer has come | the console's module 3 cmd 00 | PokeUDS's "no" at "Trade?": module 1 cmd 00, not chosen (what a console sends for no) |
| X / Y, ORAS | module 1 cmd 01 (the 260-byte record) and cmd 06, after the console's cmd 01 | the console's cmd 01 | cmd 07 `00e2` (a value other than 01) |

After a Sun/Moon trade PokeUDS shows the Pokemon it received as its screen comes back, as a console does, before the
next offer is known. Online, PokeUDS writes nothing to the received folder: the Pokemon the 3DS sent is the
partner's now, and a copy in the bank would be a clone.

### The Virtual Console

A Gen 1 or Gen 2 link is not a trade of single records: both games exchange whole parties every round, then each
picks, then both accept. So for the VC PokeUDS does not take an offer at all: it **plays the partner's console to its
own** (`pokeuds/vc/online.py`, `OnlineEngine`), and the link data each 3DS sends crosses to the other.

| step | what the 3DS sends | what PokeUDS answers |
|---|---|---|
| "Please wait", the room, the round opening | syncs, `$Dx` | as a local trade does (`link.TradeEngine`). The room the 3DS chooses sets the mode and the relay room: `gb1` for the Gen 1 cable (Red, Blue, Yellow, and Gold, Silver, Crystal's Time Capsule: a Gen 2 game sends `$D0` or `$D4` there), `gb2` for the Gen 2 Trade Center (`$D1`) |
| random numbers | its 17 bytes | made here |
| party, patch lists, Gen 2 mail | each block in one burst, then it waits for the partner's | its block, read from the burst and sent to the partner (`share`: `party`, `patch`, `mail`); the partner's console's block once it has come and passed the checks. Until then the reply is held |
| pick, accept | `$60`/`$70` + slot (`$F` cancel), `$x2` accept / `$x1` decline | `$FE`, no choice yet, until the partner's console's choice has come (`share`: `pick`, `confirm`, with a step count), then that choice |
| after a trade | one sync (Gen 2: 1 if a Mew, 2 if a Celebi was traded) | worked out here, as both games do |

Each 3DS therefore sees the other's real party, pick and answer, and both trade, or neither, as over a cable. The
next round's blocks come fresh from the partner's console, so a trade evolution, a nickname or a new move learned
there are what the other 3DS sees. A partner gone answers Cancel at the pick and No at the accept. Nothing is
written: what the 3DS receives is the partner's own Pokemon, and what it sends is theirs now.

Before a partner's block reaches the 3DS it is checked (`sane_party`, after Lorenzooone's sanity checks): one to six
Pokemon, every species and move in the game's tables, levels 1 to 100, every name ended. A block that fails is
refused (the partner is told why) and never answered: the 3DS waits at the table until its player leaves. PKHeX is
not needed. The Japanese and Korean games meet in rooms of their own (`gb1-jp`, `gb2-jp`, `gb2-kr`): their blocks are
laid out differently, and the cross-language conversion of the local trades is not applied online yet.

Where this comes from:

- [pokeldn](https://github.com/Decryptu/pokeldn)'s FireRed/LeafGreen online trade: the party blocks relayed whole,
  each side holding its own until the partner's console has sent the same, and the pick crossing before the record.
  FireRed is a GBA link with a different shape; the Game Boy rounds are PokeUDS's own.
- [Lorenzooone's PokemonGB_Online_Trades](https://github.com/Lorenzooone/PokemonGB_Online_Trades) (MIT), which trades
  real Game Boys online: `$FE` as "no input yet" while a game waits on the partner's choice, the sanity checks, and
  its two modes. Its *synchronous* mode passes every byte (too slow over relays); its *buffered* mode exchanges
  whole parties beforehand, with a stand-in party for the first round. A 3DS bursts its blocks and then waits, which
  a cartridge cannot be made to do, so PokeUDS needs no stand-in round: it reads the burst, passes it on and answers
  late. No code is copied.
- [GB-Link's gb-pokemon-web](https://github.com/GB-Link/gb-pokemon-web) (GPL-3.0), the web port of Lorenzooone's
  client for the GB-Link adapter, read for the same design. [Celio-Link](https://github.com/Celio-Link) trades
  Generation 3 only for now, so nothing of it applies yet.

Measured on hardware: nothing. The engine relies on the 3DS sending each block in one burst (measured for local
trades, mgba_LDN `wiki/vc_link.md`) and on its waiting, with no timer, for the partner's block and choice: how long it
waits decides how far apart in time the two players may sit down at the table. If it gives up, the fallback is a
gate before the round opening, or Lorenzooone's stand-in round.


### Tested, and not

`tests/test_offers.py` drives a Gen 6 and a Gen 7 session from a scripted partner against a second engine standing
in for the console: offering after the console, the yes held for the partner, a partner taking its offer back, a
partner gone. `tests/test_online_trade.py` covers the channel (pokeldn's tests of it, ported: pairing among many
with loss, rooms and codes apart, rounds in order, refusals, a silent or departed partner, forged and flooding
events, bounded state; and a yes to an offer since replaced), the PKHeX check, two partners meeting through a
loopback WebSocket relay with `LivePool`, and **whole trades**: a console and a PokeUDS on each of two simulated
desks, their PokeUDS joined through the relays, two Gen 6 trades and one Gen 7 trade, each console ending with the
other's Pokemon.

`test_two_command_line_runs_trade_online_through_a_relay` runs two `pokeuds trade xy --online-code desk` processes,
each beside the demo radio's X, through a loopback relay, with PKHeX checking each Pokemon.

`tests/test_vc_online.py` plays two modelled 3DSs (`vc/model.py`, bursting their blocks as a 3DS does) through two
`OnlineEngine`s: Gen 1, Gen 2 with mail, the Time Capsule meeting Red, a decline, a block held until the partner's
comes and a bad one refused, a partner gone in the trade menu; then Red and Blue over the simulated air and the relays,
and two `pokeuds vc trade --online-code desk` processes beside the demo radio's Red and Blue.

### Using it

Command line: `pokeuds trade <xy|oras|sm|usum> --online-code CODE`, `pokeuds vc trade --online-code CODE` (no
`--offer`; [cli.md](cli.md), Trading). App: on the Trade page, turn on **Online** (beside who asks or hosts) and type
the room code your partner types (Settings' `online`, `online_code`). The queue gives way to the partner's
Pokemon; the session panel shows the partner (waiting, paired, gone) and what their 3DS offers, from the `online`
status events. PokeUDS stays on the trade screen until the partner leaves or the 3DS's player does.

**Not yet done:**

* VC trades across language families, and battles.
* Anything on hardware. Unmeasured until then:
  * how long each game waits at the offer and at "Trade?" while the partner decides;
  * whether a Sun/Moon offers before PokeUDS's screen shows a Pokemon (online, PokeUDS has none to show until the
    partner's has come; the engine standing in for a console waits up to `SCREEN_WAIT`, 20 s, then offers);
  * whether an X/Y takes PokeUDS offering second and confirming after it, and what it does with a cmd 07 `00e2`;
  * whether a Sun/Moon takes PokeUDS's "no" at "Trade?" as a console's;
  * how long a VC game waits at the block window and in the trade menu, and whether a Gen 2 Time Capsule sends
    `$D0` or `$D4`;
  * the whole path with two consoles, two boards and two app instances on one desk, with a code.

## Licence

PokeUDS is AGPL-3.0 ([LICENSE](../LICENSE)), as pokeldn is, so pokeldn's code may be used here with its notice kept.
`pokeuds/online/link.py` is ported from pokeldn's `pokeldn/online/link.py` (Decryptu/pokeldn, AGPL-3.0), and
`tests/test_online_trade.py` from its tests of it, each saying so. The rest of the online and GTS code was written
from the public specifications (NIP-01, -09, -40, -99, BIP-340, RFC 6455) and pokeldn's documented design, and the
VC's from the projects The Virtual Console names, none of whose code is copied.
