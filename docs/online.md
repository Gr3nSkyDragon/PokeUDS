# Online: the GTS and online trades

PokeUDS trades over the internet in two ways:

| | what it is | state |
|---|---|---|
| **The GTS** | list a banked Pokemon against what you want; anyone can offer on it, at any time. Bank to bank, no console needed at either end. | written and tested against a fake relay; **never run against the public relays yet** |
| **Online trades** | two players trade live, each with their own 3DS beside their own PokeUDS. Each board hosts a local trade; the two apps carry the offers between them. | not set up yet; designed (below) after pokeldn; needs the offer-source refactor, then hardware |

Both ride on [Nostr](https://github.com/nostr-protocol/nips) relays: public servers that keep and pass on signed
JSON events. Nobody runs a PokeUDS server, there is no account, and no relay has to be trusted with anything but
passing events along. This is pokeldn's design; PokeUDS uses its own tags and key prefixes, so the two apps never
see each other's listings or rooms.

Game Boy Pokemon of every language go on the GTS, with their language. Live online trades for the Virtual
Console start with the international games; the Japanese and Korean releases trade locally now ([vc.md](vc.md),
Languages), and live online they would also need the cross-language conversion the local trades already do.

## The pieces

| file | what it does |
|---|---|
| `pokeuds/online/schnorr.py` | secp256k1 keys, BIP-340 signatures, the shared secret of two keys. Pure Python; checked against BIP-340's vectors and `cryptography`'s ECDH. |
| `pokeuds/online/relays.py` | events (NIP-01) signed and checked; a small WebSocket client; `Pool`, which talks to several relays at once and believes only correctly signed events that match what it asked for. |
| `pokeuds/online/gts.py` | the GTS's events: listing, offer, answer, deletion; sealing. Touches neither the bank nor a relay. |
| `pokeuds/app/gts.py` | the GTS against the bank: what leaves it, what joins it, and the round that settles everything. |
| `gui/views/gts.py` | the GTS page. |
| `tests/fake_relay.py` | a relay for the tests: the store, a pool over it, and the store behind a real loopback WebSocket. |

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
| offer | offerer | legal by PKHeX, of the listing's format and one of its wants (species, level range, shiny). It leaves the bank into `.gts/offer-<id>/`; the offer is sealed to the listing's key. One waiting offer per listing; never on your own listing. |
| the lister's app opens | lister | the offers on the relays are answered oldest first. The first one that is valid (readable, the right listing and format, a real record, legal by PKHeX, what the listing asks for) is traded: the answer carries the listed record, the listing is republished as `traded`, the offered Pokemon joins the bank. Every other offer is refused, saying why. |
| the offering app opens | offerer | the answer's record must match the listing's SHA-256; it joins the bank. A refusal returns the offered Pokemon. With no answer, the listing itself is read: withdrawn, traded to someone else, or ended returns the Pokemon too. The offer is then deleted from the relays. |
| a listing ends | lister | taken down, or 30 days without a trade: the Pokemon returns to the bank, and later offers are refused. |

Rounds happen when the app starts (if anything is live), when the GTS page opens, after each list or offer,
and on "Check now". There is no background service: a trade waits until the lister's app next opens.

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

**Not yet done:** a run against the public relays. Publishing there is outward-facing (the events are public and
may be kept), so it waits for a deliberate first listing. Unmeasured until then: relay limits on event size (a
listing is about 1 KB, an offer about 0.5 KB), how long the relays keep kind 1059 events, and how they treat NIP-40.

## Online trades (planned)

A live trade between two players, each with a 3DS and a board. **Each side is an ordinary local trade**: PokeUDS
hosts the 3DS's trade over its own board, exactly as today, and the two apps pass what each 3DS offers to the
other over the relays. Nothing changes on the 3DS side. This is how pokeldn trades online (its `docs/online.md`
and `pokeldn/online/link.py` at `d701560`, which PokeUDS may now take with its notice: both are AGPL-3.0); pokeldn
does it for its own games (FireRed/LeafGreen, Let's Go, Sword/Shield, BDSP, Legends, Scarlet/Violet, Z-A), so the
Gen 6/7 and VC parts below are PokeUDS's own.

### Meeting (as pokeldn)

* A **room** is `sha256("pokeuds online 1|<room>|<code or 'open'>")`: `<room>` is `gen7` (Sun/Moon with
  USUM), `gen6` (X/Y with ORAS), later `gb1` and `gb2`. The 3DS games have no link code, so a code typed in the
  app makes a private room, and none meets anyone else waiting without one.
* Every event is ephemeral kind 21059 (relays pass it on and keep nothing), tagged with the room, sealed under
  `sha256("pokeuds room key|" + room)`, signed by a key made for the session.
* Each app sends `here` every 2.5 s (its X25519 key, trainer name, version, busy). The lower signing key invites
  the longest-seen free app above it, one at a time for up to 6 s; `yes`/`no` answers. A refused invite skips
  that app for 10 s.
* Paired, the two talk on a **pair channel**: an inner AES-GCM under a key from both apps' X25519 keys
  (`pokeuds pair|` + room + both keys + their shared secret). Every message is numbered and acknowledged,
  resent every 2 s until acknowledged, with a ping every 4 s; a partner silent for 45 s is lost.

### Trading

| message | meaning |
|---|---|
| `offer` / `withdraw` | trade *r*: this player's 3DS put this record up, or took it back |
| `accept` / `unaccept` | trade *r*: this player's 3DS said yes, or changed its mind |
| `refused` | trade *r*: this app will not show the other's record to its 3DS, and why |
| `done` | trade *r* finished on this side; the next is *r* + 1 |

Each app checks the partner's record with PKHeX before its 3DS sees it: one PKHeX cannot read, or with an item or
move the game never released, is refused (the sender is told why); any other legality flag reaches the receiver
as a warning. Each app says yes to its own 3DS only once **both** 3DSs have said yes, so neither trade completes
alone.

### How a trade takes its partner (as pokeldn)

pokeldn's trade hosts hold a `Partner` and drive it from their own loop: every call returns at once, and the
partner's state is something the host polls (`theirs()`: their offer, whether they accepted, whether they are
lost). The host calls `offer`, `accept`, `withdraw`, `done` as its own console acts. PokeUDS's trades take their
offers up front instead (`games/gen6/trade.py` `offer` and `queue`, `games/gen7/session.py` `self._offers`,
`vc/trade.py` `link.Party`), so the first step is an **offer source** with that same polling shape: the local
queue becomes one source (offers known at once, the console's yes answered at once) and `Partner` the other.

| | the partner's offer goes to the 3DS as | the 3DS's offer goes out from | PokeUDS's yes waits for | the partner withdrawing becomes |
|---|---|---|---|---|
| Sun / Moon, USUM | the offer `_one_trade` makes once the console's trade screen is up (today after up to `SCREEN_WAIT`, 20 s): online, once the partner's record has arrived and passed PKHeX | the record the console offers (already parsed for the bank) | the console's yes (as today) **and** the partner's `accept` | a decline in `_confirm` |
| X / Y, ORAS | module 0 command 01 (the 260-byte record) | the console's command 01 | as Gen 7 | command 07 with a value other than 01 |
| Virtual Console | the partner's party block, patch lists and Gen 2 mail, relayed whole before anyone picks (as pokeldn's FireRed does); PokeUDS holds its own block at the block window until the partner's arrives | the console's blocks and its pick (`$6x`/`$7x` + slot) | `$62` accept held until the partner's console accepted | `$6F` cancel |

### What is set up today

* `pokeuds/online/schnorr.py` and `relays.py`: keys, signatures, the relay pool (shared with the GTS).
* Nothing of the rooms, the pair channel or the offer source yet: **Gen 6 and Gen 7 online trades are not set
  up.** Order: the offer source (no hardware needed; the local trades must behave exactly as before), then
  `pokeuds/online/link.py` (`Partner`, tested as two apps over the fake relay), then Gen 7 (the best understood
  trade, with the most patience for waiting), then Gen 6, then the VC (the least patient link).
* Hardware decides the rest: how long each game waits at the offer and at "Trade?" before it gives up (Gen 6/7),
  at the block window and at `CheckLinkTimeout_Gen2` (VC), and two consoles, two boards and two app instances on
  one desk with a code.

## Licence

PokeUDS is AGPL-3.0 ([LICENSE](../LICENSE)), as pokeldn is, so pokeldn's code may be used here with its notice kept.
The online and GTS code so far was written from the public specifications (NIP-01, -09, -40, -99, BIP-340,
RFC 6455) and pokeldn's documented design; none of it is copied from pokeldn's source.
