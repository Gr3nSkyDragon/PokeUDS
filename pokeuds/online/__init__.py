"""Trading between players who are not in the same room (docs/online.md).

Nothing here talks to Nintendo. The apps meet on public Nostr relays, which only store and pass on signed events:

- ``schnorr``: secp256k1 keys, BIP-340 signatures and the shared secret two keys make.
- ``relays``: Nostr events and a small WebSocket client for a pool of relays.
- ``gts``: the GTS's events: a listing anyone can read, and the sealed offer and answer that trade against it.

The bank's side of the GTS (what leaves the bank, what comes back, settling when the app opens) is
``pokeuds.app.gts``. Written for PokeUDS from the public specifications (NIP-01, NIP-09, NIP-40, NIP-99, BIP-340) and
the shape of pokeldn's GTS; the events carry PokeUDS's own tags and key prefixes, so the two apps never share listings.
"""
