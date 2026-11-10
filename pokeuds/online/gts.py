"""The GTS's events: a listing any app reads from the relays, and the sealed offer and answer that trade against it
(docs/online.md, The GTS). Nothing here touches the bank or a relay: records come in as bytes with what the bank
knows of them, and events go out for ``relays.Pool`` to carry. The bank's side is ``pokeuds.app.gts``.

| event | kind | signed by | content |
|---|---|---|---|
| listing | 30402 (NIP-99), ``d`` a random id | a key made for the listing | JSON: the Pokemon as shown, its record's SHA-256, what is wanted, status |
| offer | 1059, ``p`` the listing's key | a key made for the offer | sealed: the listing's ``d``, the record offered, the trainer's name |
| answer | 1059, ``p`` the offer's key | the listing's key | sealed: the offer's id, and the listed record or a refusal |
| deletion | 5 (NIP-09) | the key of the event it names | none |

A sealed body is AES-256-GCM under ``sha256("pokeuds gts|" + x)``, x being the secret the two keys share
(``schnorr.shared_x``): only the listing's and the offer's keys read it, and neither key is tied to a player.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field

from Crypto.Cipher import AES

from . import relays, schnorr

PROTOCOL = 1
LISTING = 30402        # NIP-99 classified listing: addressable, a relay keeps the newest per key and d
WRAP = 1059            # stored, and found by its p tag
DELETION = 5
TAG = "pokeuds-gts"
LIFETIME = 30 * 86400  # s a listing stays up
WRAP_GRACE = 7 * 86400 # s an offer or answer outlives its listing, for the app that has not opened yet
SKEW = 3600            # clocks disagree: an ended listing is believed ended this much later
MAX_WANTS = 3
FORMATS = ("pk1", "pk2", "pk6", "pk7")
FORMAT_NAMES = {"pk1": "Red / Blue / Yellow", "pk2": "Gold / Silver / Crystal", "pk6": "X / Y / Omega Ruby / Alpha Sapphire",
                "pk7": "Sun / Moon / Ultra Sun / Ultra Moon"}
RECORD_LIMIT = 1024    # bytes of a record: a .pk7 is 232
STATUSES = ("open", "traded", "withdrawn")
SHOWN = ("species", "species_name", "form", "level", "shiny", "nickname", "gender", "nature", "ability", "ball",
         "held_item", "moves", "ot", "hidden_power", "language")
LANGUAGES = ("", "en", "de", "fr", "es", "it", "jp", "kr")   # a .pk1/.pk2's game language (pokeuds/games/vc.py)
_HEX64 = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[0-9a-f]{16,64}")


def _text(value, limit: int) -> bool:
    return isinstance(value, str) and len(value) <= limit


def _number(value, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


# -- what a listing asks for ----------------------------------------------------------------------

@dataclass(frozen=True)
class Want:
    species: int
    name: str
    min_level: int = 1
    max_level: int = 100
    shiny: bool = False             # only a shiny answers

    def takes(self, species: int, level: int, shiny: bool) -> bool:
        return species == self.species and self.min_level <= level <= self.max_level and (shiny or not self.shiny)

    def describe(self) -> str:
        levels = (f"Lv {self.min_level}" if self.min_level == self.max_level else
                  "any level" if (self.min_level, self.max_level) == (1, 100) else f"Lv {self.min_level}-{self.max_level}")
        return f"{'a shiny ' if self.shiny else ''}{self.name} ({levels})"

    def to_dict(self) -> dict:
        out = {"species": self.species, "name": self.name, "min_level": self.min_level, "max_level": self.max_level}
        if self.shiny:
            out["shiny"] = True
        return out

    @classmethod
    def from_dict(cls, raw) -> "Want | None":
        if not isinstance(raw, dict) or not _number(raw.get("species"), 1, 2000) or not _text(raw.get("name"), 40):
            return None
        low, high = raw.get("min_level", 1), raw.get("max_level", 100)
        if not _number(low, 1, 100) or not _number(high, low, 100) or raw.get("shiny", False) not in (True, False):
            return None
        return cls(raw["species"], raw["name"], low, high, bool(raw.get("shiny", False)))


# -- sealing --------------------------------------------------------------------------------------

def _key(secret: bytes, public: bytes) -> bytes:
    return hashlib.sha256(b"pokeuds gts|" + schnorr.shared_x(secret, public)).digest()


def seal(secret: bytes, public: bytes, body: dict) -> str:
    nonce = os.urandom(12)
    cipher = AES.new(_key(secret, public), AES.MODE_GCM, nonce=nonce)
    sealed, mac = cipher.encrypt_and_digest(json.dumps(body, separators=(",", ":")).encode())
    return base64.b64encode(nonce + sealed + mac).decode()


def unseal(secret: bytes, public: bytes, text: str) -> dict | None:
    """The body another key sealed to this one, or None: not for this key, changed on the way, or not a body."""
    try:
        raw = base64.b64decode(text, validate=True)
        if len(raw) < 28:
            return None
        cipher = AES.new(_key(secret, public), AES.MODE_GCM, nonce=raw[:12])
        body = json.loads(cipher.decrypt_and_verify(raw[12:-16], raw[-16:]))
        return body if isinstance(body, dict) else None
    except (ValueError, KeyError):
        return None


def _record(value) -> bytes | None:
    try:
        raw = base64.b64decode(value, validate=True) if isinstance(value, str) else b""
    except ValueError:
        return None
    return raw if 0 < len(raw) <= RECORD_LIMIT else None


# -- the listing ----------------------------------------------------------------------------------

@dataclass
class Listing:
    id: str                         # the d tag
    pubkey: str
    format: str                     # "pk1", "pk2", "pk6", "pk7": what is traded, and what answers must be
    pokemon: dict                   # the listed Pokemon as shown (SHOWN)
    sha256: str                     # of its record: what an answer must hold
    wants: list[Want]
    trainer: str
    status: str
    winner: str                     # the offer event traded with, once traded
    created: int                    # published_at: when it was first listed
    expires: int
    event: dict = field(repr=False, default_factory=dict)

    def takes(self, species: int, level: int, shiny: bool) -> Want | None:
        return next((w for w in self.wants if w.takes(species, level, shiny)), None)

    def title(self) -> str:
        p = self.pokemon
        name = str(p.get("species_name") or f"#{p.get('species')}")
        return f"{'Shiny ' if p.get('shiny') else ''}{name} Lv {p.get('level')}"

    def asks(self) -> str:
        return " or ".join(w.describe() for w in self.wants)


def new_id() -> str:
    return os.urandom(8).hex()


def listing_event(secret: bytes, ident: str, fmt: str, pokemon: dict, record_sha256: str, wants: list[Want],
                  trainer: str, *, status: str = "open", winner: str = "", published_at: int | None = None,
                  now: float | None = None) -> dict:
    """The listing, signed. Published again with another ``status`` (and the same ``published_at``), it replaces the
    one before on every relay, which keep the newest per key and ``d``."""
    if fmt not in FORMATS or status not in STATUSES or not 1 <= len(wants) <= MAX_WANTS or not _ID.fullmatch(ident):
        raise ValueError("not a listing: a format, one to three wants and an id are needed")
    now = int(time.time() if now is None else now)
    published = now if published_at is None else int(published_at)
    shown = {k: pokemon[k] for k in SHOWN if pokemon.get(k) not in (None, "", [])}
    body = {"protocol": PROTOCOL, "format": fmt, "pokemon": shown, "sha256": record_sha256,
            "wants": [w.to_dict() for w in wants], "trainer": trainer[:16], "status": status, "winner": winner}
    listing = Listing(ident, "", fmt, shown, record_sha256, wants, trainer, status, winner, published, published + LIFETIME)
    species = int(shown.get("species") or 0)
    tags = [["d", ident], ["t", TAG], ["t", f"{TAG}-{fmt}"], ["t", has_tag(fmt, species)],
            *[["t", wants_tag(fmt, w.species)] for w in wants],
            ["title", f"{listing.title()} ({FORMAT_NAMES[fmt]})"], ["summary", f"Wants {listing.asks()}"],
            ["published_at", str(published)], ["status", "active" if status == "open" else "sold"],
            ["expiration", str(published + LIFETIME)]]
    return relays.sign_event(secret, LISTING, json.dumps(body, separators=(",", ":"), ensure_ascii=False), tags, now)


def has_tag(fmt: str, species: int) -> str:
    return f"{TAG}-has-{fmt}-{int(species)}"


def wants_tag(fmt: str, species: int) -> str:
    return f"{TAG}-wants-{fmt}-{int(species)}"


def browse_filter(fmt: str | None = None, has: int | None = None, wants: int | None = None, limit: int = 200) -> dict:
    """The filter for the open listings of a format, optionally those listing or asking for a species (which needs
    the format: a species number alone says nothing across formats)."""
    topic = TAG if fmt is None else has_tag(fmt, has) if has else wants_tag(fmt, wants) if wants else f"{TAG}-{fmt}"
    return {"kinds": [LISTING], "#t": [topic], "limit": limit}


def parse_listing(event: dict) -> Listing | None:
    """A listing read from a relay, checked field by field; None for anything that is not one of ours."""
    if not relays.valid(event) or event.get("kind") != LISTING or TAG not in relays.tags(event, "t"):
        return None
    ident, published = relays.tag(event, "d"), relays.tag(event, "published_at")
    try:
        body = json.loads(event["content"])
        created = int(published)
    except (TypeError, ValueError):
        return None
    if not isinstance(body, dict) or body.get("protocol") != PROTOCOL or not ident or not _ID.fullmatch(ident):
        return None
    fmt, pokemon, raw_wants = body.get("format"), body.get("pokemon"), body.get("wants")
    if fmt not in FORMATS or not isinstance(pokemon, dict) or not isinstance(raw_wants, list):
        return None
    if not _number(pokemon.get("species"), 1, 2000) or not _number(pokemon.get("level"), 1, 100):
        return None
    if not all(_text(pokemon.get(k, ""), 40) for k in ("species_name", "nickname", "ot", "nature", "ability", "ball",
                                                        "held_item", "gender", "hidden_power")):
        return None
    if pokemon.get("language", "") not in LANGUAGES:
        return None
    moves = pokemon.get("moves", [])
    if not isinstance(moves, list) or len(moves) > 4 or not all(_text(m, 40) for m in moves):
        return None
    wants = [Want.from_dict(w) for w in raw_wants]
    if not 1 <= len(wants) <= MAX_WANTS or None in wants:
        return None
    status, winner, digest = body.get("status"), body.get("winner", ""), body.get("sha256")
    if status not in STATUSES or not isinstance(digest, str) or not _HEX64.fullmatch(digest):
        return None
    if not _text(body.get("trainer", ""), 16) or not (winner == "" or (isinstance(winner, str) and _HEX64.fullmatch(winner))):
        return None
    if not 0 < created <= event["created_at"] + SKEW:
        return None
    shown = {k: pokemon[k] for k in SHOWN if k in pokemon}
    shown["shiny"] = pokemon.get("shiny") is True
    return Listing(ident, event["pubkey"], fmt, shown, digest, wants, body.get("trainer", ""), status, winner, created,
                   created + LIFETIME, event)


# -- offers and answers ---------------------------------------------------------------------------

def _wrap(secret: bytes, to: str, body: dict, expires: int, now: float | None) -> dict:
    sealed = seal(secret, bytes.fromhex(to), body)
    return relays.sign_event(secret, WRAP, sealed, [["p", to], ["expiration", str(int(expires))]],
                             None if now is None else int(now))


def offer_event(secret: bytes, listing: Listing, record: bytes, trainer: str, now: float | None = None,
                language: str = "") -> dict:
    """``language``: a .pk1/.pk2's game language, which its record does not hold (the bank keeps it beside)."""
    body = {"protocol": PROTOCOL, "listing": listing.id, "format": listing.format,
            "record": base64.b64encode(record).decode(), "trainer": trainer[:16]}
    if language:
        body["language"] = language
    return _wrap(secret, listing.pubkey, body, listing.expires + WRAP_GRACE, now)


def read_offer(secret: bytes, event: dict) -> dict | None:
    """An offer sealed to the listing's key: {"listing", "format", "record" (bytes), "trainer", "language"}, or None."""
    if not relays.valid(event) or event.get("kind") != WRAP:
        return None
    body = unseal(secret, bytes.fromhex(event["pubkey"]), event["content"])
    if body is None or body.get("protocol") != PROTOCOL or body.get("format") not in FORMATS:
        return None
    record = _record(body.get("record"))
    if record is None or not _text(body.get("listing"), 64) or not _text(body.get("trainer", ""), 16):
        return None
    if body.get("language", "") not in LANGUAGES:
        return None
    return {"listing": body["listing"], "format": body["format"], "record": record, "trainer": body.get("trainer", ""),
            "language": body.get("language", "")}


def answer_event(secret: bytes, offer: dict, expires: int, *, record: bytes | None = None, reason: str = "",
                 now: float | None = None) -> dict:
    """The lister's answer to an offer event: the listed record (a trade), or why not."""
    body = {"protocol": PROTOCOL, "offer": offer["id"], "ok": record is not None}
    if record is not None:
        body["record"] = base64.b64encode(record).decode()
    else:
        body["reason"] = reason[:200]
    return _wrap(secret, offer["pubkey"], body, expires + WRAP_GRACE, now)


def read_answer(secret: bytes, listing_pubkey: str, event: dict) -> dict | None:
    """The lister's answer, sealed to the offer's key: {"offer", "ok", "record" (bytes or None), "reason"}, or None.
    Only an event signed by the listing's own key is an answer."""
    if not relays.valid(event) or event.get("kind") != WRAP or event.get("pubkey") != listing_pubkey:
        return None
    body = unseal(secret, bytes.fromhex(listing_pubkey), event["content"])
    if body is None or body.get("protocol") != PROTOCOL or not isinstance(body.get("offer"), str):
        return None
    if body.get("ok") is True:
        record = _record(body.get("record"))
        return None if record is None else {"offer": body["offer"], "ok": True, "record": record, "reason": ""}
    if body.get("ok") is False and _text(body.get("reason", ""), 200):
        return {"offer": body["offer"], "ok": False, "record": None, "reason": body.get("reason", "")}
    return None


def deletion_event(secret: bytes, ident: str) -> dict:
    return relays.sign_event(secret, DELETION, "", [["e", ident]])
