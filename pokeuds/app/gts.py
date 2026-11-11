"""The GTS against the bank: what is listed or offered leaves the bank, what a trade brings joins it, and every open
listing and waiting offer is settled when the app next opens (docs/online.md, The GTS).

Each listing and offer of ours is a folder in the bank's hidden ``.gts`` holding ``state.json`` (its secret key, the signed
events, the answers sent) and the record that left the bank. Nothing is trusted to memory or to a relay: a step is
written to the folder before its event is published, and every live event is sent again each round, so a crash or a
relay that forgot loses nothing. The folders live inside the bank because what they hold left that bank and goes
back to it: two copies of the app, each with its own bank, never settle each other's listings.

| step | what happens |
|---|---|
| list | the Pokemon must be legal (PKHeX); it leaves the bank and its listing is published |
| offer | legal, of the listing's format and matching one of its wants; it leaves the bank, sealed to the listing's key |
| the lister's app opens | stored offers are answered oldest first: the first valid one is traded, the rest refused |
| a trade | the answer carries the listed record; the listing is published again as traded; the offered record is banked |
| the offering app opens | the answer's record is checked against the listing's SHA-256 and banked; a refusal, a lost race or a withdrawn listing returns its own Pokemon |
| a listing ends | after 30 days, or withdrawn: its Pokemon returns; later offers are refused |

A modified app can keep an offered Pokemon, or list one it does not hold: the SHA-256 shows it only afterwards. A
fair exchange with no trusted third party is impossible; the GTS is for people willing to take that chance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from ..online import gts as wire
from ..online import relays, schnorr
from . import bank as banking

log = logging.getLogger(__name__)
KEPT = 60 * 86400               # s a finished folder is kept before it is deleted
GAME_OF = {"pk1": "rby", "pk2": "gsc", "pk6": "xy", "pk7": "sm"}       # PKHeX's context for a format
LIVE = ("open", "waiting")
STATUS_TEXT = {"open": "Listed, waiting for an offer", "waiting": "Offered, waiting for the lister's app",
               "traded": "Traded", "withdrawn": "Taken down", "expired": "Ended after 30 days",
               "returned": "Returned to the bank", "failed": "Lost"}


FOLDER = ".gts"             # inside the bank folder, hidden like its .info


def default_root(bank: banking.Bank) -> Path:
    return bank.folder / FOLDER


def pkhex_check(fmt: str, data: bytes) -> tuple[bool, str, dict]:
    """What PKHeX says of a record: (legal, its report, the Pokemon as PKHeX reads it)."""
    from .. import pkhex

    result = pkhex.shared().check(GAME_OF[fmt], data)
    return result.legal, result.report, result.summary


@dataclass
class Item:
    """One listing or offer of ours: its folder and state."""
    path: Path
    state: dict

    @property
    def id(self) -> str:
        return self.state["id"]

    @property
    def kind(self) -> str:
        return self.state["kind"]

    @property
    def status(self) -> str:
        return self.state["status"]

    @property
    def live(self) -> bool:
        return self.status in LIVE

    def title(self) -> str:
        return self.state.get("title", "")

    def detail(self) -> str:
        """One line on where it stands, for the page."""
        s = self.state
        text = STATUS_TEXT.get(self.status, self.status)
        if self.kind == "listing":
            asks = " or ".join(wire.Want.from_dict(w).describe() for w in s.get("wants", []))
            if self.status == "traded":
                return f"Traded for {s.get('got', 'a Pokemon')}, now in the bank"
            return f"{text}; wants {asks}"
        if self.status == "traded":
            return f"Traded for {s.get('got', 'the listed Pokemon')}, now in the bank"
        reason = f": {s['reason']}" if s.get("reason") else ""
        return f"{text}{reason}" if self.status != "waiting" else f"{text} (for {s['listing'].get('title', 'a listing')})"

    def save(self) -> None:
        tmp = self.path / "state.json.tmp"
        tmp.write_text(json.dumps(self.state, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path / "state.json")

    def record(self) -> bytes:
        return (self.path / self.state["record"]).read_bytes()


def _name(entry: banking.Entry) -> str:
    """A Pokemon in a few words: "Eevee 'Vee' Lv 30"."""
    return entry.describe().split(", OT ")[0]


def _shown(entry: banking.Entry, summary: dict) -> dict:
    """The Pokemon as the listing shows it: the bank's reading, with what PKHeX adds."""
    shown = {"species": entry.dex, "species_name": entry.species, "form": entry.form or None,
             "level": summary.get("level") or entry.level, "shiny": bool(entry.shiny), "nickname": entry.nickname,
             "ot": entry.ot}
    for key in ("gender", "nature", "ability", "ball", "held_item", "hidden_power"):
        if isinstance(summary.get(key), str) and summary[key] not in ("", "none", "None"):
            shown[key] = summary[key]
    moves = [m for m in summary.get("moves") or [] if isinstance(m, str) and m]
    if moves:
        shown["moves"] = moves[:4]
    if language := _language(entry):
        shown["language"] = language        # its game's: a .pk1/.pk2 does not say (pokeuds/games/vc.py)
    return shown


def _language(entry: banking.Entry) -> str:
    """A Game Boy record's language, as the bank keeps it; "" for Gen 6/7, whose record holds its own."""
    language = entry.meta.get("language", "") if entry.generation <= 2 else ""
    return language if language in wire.LANGUAGES else ""


class Gts:
    def __init__(self, bank: banking.Bank, root: str | Path | None = None, check=pkhex_check, clock=time.time) -> None:
        self.bank = bank
        self.root = Path(root) if root else default_root(bank)
        self.check = check
        self.clock = clock

    # -- what we hold ---------------------------------------------------------------------------

    def items(self) -> list[Item]:
        """Our listings and offers, newest first."""
        found = []
        if self.root.is_dir():
            for folder in self.root.iterdir():
                try:
                    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(state, dict) and state.get("kind") in ("listing", "offer"):
                    found.append(Item(folder, state))
        return sorted(found, key=lambda i: i.state.get("created", 0), reverse=True)

    def live(self) -> bool:
        return any(item.live for item in self.items())

    def mine(self) -> set[str]:
        """The keys of our own listings, so the page does not offer us our own Pokemon."""
        return {i.state["pubkey"] for i in self.items() if i.kind == "listing"}

    # -- leaving and joining the bank -----------------------------------------------------------

    def _legal(self, entry: banking.Entry) -> tuple[banking.Entry, dict]:
        """A bank entry that may go on the GTS, with PKHeX's verdict kept, and PKHeX's reading of it; ValueError says
        why it may not."""
        if entry.has_mail:
            raise ValueError("a Pokemon holding mail cannot go on the GTS: take its mail off first")
        legal, report, summary = self.check(entry.extension, entry.data())
        entry = self.bank.set_legality(entry, legal, report, str(summary.get("encounter") or ""))
        if not legal:
            lines = [line for line in report.splitlines() if line.startswith("Invalid")][:2]
            raise ValueError("only legal Pokemon go on the GTS, and PKHeX finds this one is not"
                             + (": " + "; ".join(lines) if lines else ""))
        return entry, summary

    def _take(self, entry: banking.Entry, state: dict) -> Item:
        """Move a bank entry into a new folder: the folder is written first, so a crash never loses the Pokemon."""
        folder = self.root / f"{state['kind']}-{state['id']}"
        if not self.root.is_dir():
            self.root.mkdir(parents=True, exist_ok=True)
            banking._hide(self.root)
        folder.mkdir(parents=True, exist_ok=True)
        state["record"] = f"record.{entry.extension}"
        (folder / state["record"]).write_bytes(entry.data())
        keep = {k: v for k, v in entry.meta.items() if k not in ("digest", "created", "migrated")}
        state["bank"] = {"source": entry.source, "origin": entry.origin, "note": entry.note, **keep}
        item = Item(folder, state)
        item.save()
        self.bank.remove(entry)
        return item

    def _give_back(self, item: Item) -> None:
        """Our own Pokemon returns to the bank as it left."""
        if item.state.get("returned"):
            return
        kept = dict(item.state.get("bank") or {})
        extra = {k: v for k, v in kept.items() if k not in ("source", "origin", "note")}
        self.bank.deposit(item.record(), item.state["format"], source=kept.get("source", "imported"),
                          origin=kept.get("origin", ""), note=kept.get("note", ""), extra=extra)
        item.state["returned"] = True

    def _bank_received(self, item: Item, record: bytes, note: str, language: str = "") -> str:
        """Bank what the trade brought; ``language`` is a Game Boy record's, kept as a received one's is."""
        entry, _ = self.bank.deposit(record, item.state["format"], source="gts", note=note,
                                     extra={"language": language} if language else None)
        try:
            legal, report, summary = self.check(item.state["format"], record)
            entry = self.bank.set_legality(entry, legal, report, str(summary.get("encounter") or ""))
        except Exception as exc:        # banked all the same; the Bank page can check it later
            log.debug("could not check the received record: %s", exc)
        item.state["received"] = entry.id
        item.state["got"] = _name(entry)
        return _name(entry)

    # -- listing --------------------------------------------------------------------------------

    def list_pokemon(self, entry: banking.Entry, wants: list[wire.Want], trainer: str) -> Item:
        """Put a banked Pokemon up against one to three wants. It leaves the bank; ``round`` publishes it."""
        if not 1 <= len(wants) <= wire.MAX_WANTS:
            raise ValueError("a listing asks for one to three Pokemon")
        if entry.extension == "pk1" and any(w.shiny for w in wants):
            raise ValueError("Generation 1 has no shiny Pokemon to ask for")
        entry, summary = self._legal(entry)
        secret = schnorr.new_secret()
        ident = wire.new_id()
        now = int(self.clock())
        shown = _shown(entry, summary)
        digest = hashlib.sha256(entry.data()).hexdigest()
        event = wire.listing_event(secret, ident, entry.extension, shown, digest, wants, trainer, now=now)
        listing = wire.parse_listing(event)
        state = {"kind": "listing", "id": ident, "secret": secret.hex(), "pubkey": event["pubkey"],
                 "format": entry.extension, "status": "open", "created": now, "expires": listing.expires,
                 "title": listing.title(), "pokemon": shown, "sha256": digest, "trainer": trainer,
                 "wants": [w.to_dict() for w in wants], "event": event, "answers": {}, "winner": ""}
        return self._take(entry, state)

    def withdraw(self, item: Item) -> None:
        """Take an open listing down: its Pokemon returns now, and offers still on their way are refused."""
        if item.kind != "listing" or item.status != "open":
            raise ValueError("only an open listing can be taken down")
        self._end(item, "withdrawn")

    def _end(self, item: Item, status: str) -> None:
        s = item.state
        s["status"] = status
        s["event"] = self._listing_event(s, "withdrawn")
        s["ended"] = int(self.clock())
        item.save()                     # no trade can follow once this is written
        self._give_back(item)
        item.save()

    def _listing_event(self, s: dict, status: str, winner: str = "") -> dict:
        wants = [wire.Want.from_dict(w) for w in s["wants"]]
        return wire.listing_event(bytes.fromhex(s["secret"]), s["id"], s["format"], s["pokemon"], s["sha256"], wants,
                                  s["trainer"], status=status, winner=winner, published_at=s["created"],
                                  now=max(self.clock(), s["event"]["created_at"] + 1))

    # -- browsing and offering ------------------------------------------------------------------

    def browse(self, pool, fmt: str | None = None, has: int | None = None, wants: int | None = None) -> list[wire.Listing]:
        """The open listings on the relays, newest first: the newest version of each, not ended."""
        now = self.clock()
        newest: dict[tuple[str, str], wire.Listing] = {}
        for event in pool.fetch(wire.browse_filter(fmt, has, wants)):
            listing = wire.parse_listing(event)
            if listing is None:
                continue
            key = (listing.pubkey, listing.id)
            if key not in newest or newest[key].event["created_at"] < event["created_at"]:
                newest[key] = listing
        found = [l for l in newest.values() if l.status == "open" and l.expires > now]
        if fmt is None and has:                 # every format: the relays index a species per format, so filter here
            found = [l for l in found if l.pokemon.get("species") == has]
        if fmt is None and wants:
            found = [l for l in found if any(w.species == wants for w in l.wants)]
        return sorted(found, key=lambda l: l.created, reverse=True)

    def fits(self, listing: wire.Listing) -> list[banking.Entry]:
        """The bank's Pokemon that could answer a listing: its format, and one of its wants by species, level and
        shininess. (Legality is checked when one is offered.)"""
        return [e for e in self.bank.entries()
                if e.extension == listing.format and not e.has_mail and listing.takes(e.dex, e.level or 0, e.shiny)]

    def offer(self, entry: banking.Entry, listing: wire.Listing, trainer: str) -> Item:
        """Answer a listing with a banked Pokemon. It leaves the bank until the lister's app trades or refuses."""
        if listing.pubkey in self.mine():
            raise ValueError("this is your own listing")
        if any(i.kind == "offer" and i.status == "waiting" and i.state["listing"]["id"] == listing.id
               and i.state["listing"]["pubkey"] == listing.pubkey for i in self.items()):
            raise ValueError("you already have an offer waiting on this listing")
        if entry.extension != listing.format:
            raise ValueError(f"this listing trades .{listing.format} Pokemon ({wire.FORMAT_NAMES[listing.format]})")
        entry, summary = self._legal(entry)
        level = summary.get("level") or entry.level or 0
        if listing.takes(entry.dex, level, entry.shiny) is None:
            raise ValueError(f"the listing asks for {listing.asks()}; {_name(entry)} is not that")
        secret = schnorr.new_secret()
        now = int(self.clock())
        event = wire.offer_event(secret, listing, entry.data(), trainer, now=now, language=_language(entry))
        state = {"kind": "offer", "id": wire.new_id(), "secret": secret.hex(), "pubkey": event["pubkey"],
                 "format": entry.extension, "status": "waiting", "created": now, "title": _name(entry),
                 "listing": {"id": listing.id, "pubkey": listing.pubkey, "sha256": listing.sha256,
                             "expires": listing.expires, "title": listing.title(), "trainer": listing.trainer,
                             "language": str(listing.pokemon.get("language") or "")},
                 "event": event, "reason": ""}
        return self._take(entry, state)

    # -- settling -------------------------------------------------------------------------------

    def round(self, pool) -> list[str]:
        """Send what is ours to send and settle what the relays hold for us. -> what happened, a line each."""
        said: list[str] = []
        for item in self.items():
            try:
                self._recover(item)
                if item.kind == "listing":
                    self._round_listing(item, pool, said)
                else:
                    self._round_offer(item, pool, said)
            except Exception as exc:    # one bad folder never stops the others
                log.warning("GTS %s %s: %s", item.kind, item.id, exc)
                said.append(f"{item.title()}: {exc}")
        self._clean()
        return said

    def _recover(self, item: Item) -> None:
        """Finish what a crash cut short: the state says what was decided; the bank may not have it yet."""
        s = item.state
        received = item.path / f"received.{s['format']}"
        if s["status"] == "traded" and not s.get("received") and received.exists():
            language = s.get("partner_language", "") if item.kind == "listing" else s["listing"].get("language", "")
            self._bank_received(item, received.read_bytes(), f"GTS trade for {item.title()}", language)
            item.save()
        if s["status"] in ("returned", "withdrawn", "expired") and not s.get("returned"):
            self._give_back(item)
            item.save()

    def _valid_offer(self, s: dict, body: dict | None) -> tuple[str, str]:
        """("", what it is) for an offer the listing takes, else (why not, "")."""
        if body is None or body["listing"] != s["id"]:
            return "not an offer for this listing", ""
        if body["format"] != s["format"]:
            return f"the listing trades .{s['format']} Pokemon", ""
        try:
            parsed = banking.parse(Path(f"x.{s['format']}"), body["record"])
        except ValueError as exc:
            return f"not a Pokemon record: {exc}", ""
        legal, report, summary = self.check(s["format"], body["record"])
        if not legal:
            return "PKHeX finds the Pokemon offered is not legal", ""
        level = summary.get("level") or parsed.level or 0
        if not any(wire.Want.from_dict(w).takes(parsed.dex, level, parsed.shiny) for w in s["wants"]):
            return "the Pokemon offered is not what the listing asks for", ""
        return "", f"{summary.get('species_name') or banking.species.name(parsed.dex)} Lv {level}"

    def _round_listing(self, item: Item, pool, said: list[str]) -> None:
        s = item.state
        secret = bytes.fromhex(s["secret"])
        now = self.clock()
        if s["status"] == "open" and now >= s["expires"]:
            self._end(item, "expired")
            said.append(f"{item.title()}: the listing ended after 30 days and the Pokemon is back in the bank")
        if now > s["expires"] + wire.WRAP_GRACE:
            return                      # nothing of it is on a relay any more
        pool.publish(s["event"])
        for event in pool.fetch({"kinds": [wire.WRAP], "#p": [s["pubkey"]]}):
            if event["id"] in s["answers"] or event["pubkey"] == s["pubkey"]:
                continue
            body = wire.read_offer(secret, event)
            if body is None:
                continue                # not sealed to this key: nothing to answer
            if s["status"] != "open":
                why = "the listing was already traded" if s["status"] == "traded" else "the listing was taken down"
                answer = wire.answer_event(secret, event, s["expires"], reason=why, now=now)
            else:
                why, what = self._valid_offer(s, body)
                if why:
                    answer = wire.answer_event(secret, event, s["expires"], reason=why, now=now)
                else:
                    # The trade: written down before anything is published, then banked.
                    answer = wire.answer_event(secret, event, s["expires"], record=item.record(), now=now)
                    s["answers"][event["id"]] = answer
                    s["status"], s["winner"], s["ended"] = "traded", event["id"], int(now)
                    s["event"] = self._listing_event(s, "traded", winner=event["id"])
                    s["partner"], s["partner_language"] = body["trainer"], body["language"]
                    (item.path / f"received.{s['format']}").write_bytes(body["record"])
                    item.save()
                    got = self._bank_received(item, body["record"], f"GTS trade for {item.title()}"
                                              + (f" with {body['trainer']}" if body["trainer"] else ""),
                                              body["language"])
                    item.save()
                    pool.publish(s["event"])
                    said.append(f"{item.title()} was traded for {got}, now in the bank")
            s["answers"][event["id"]] = answer
            item.save()
        for answer in s["answers"].values():
            pool.publish(answer)        # again each round: the offering app may open weeks later

    def _round_offer(self, item: Item, pool, said: list[str]) -> None:
        s = item.state
        if s["status"] != "waiting":
            return
        secret = bytes.fromhex(s["secret"])
        listing = s["listing"]
        pool.publish(s["event"])
        for event in pool.fetch({"kinds": [wire.WRAP], "authors": [listing["pubkey"]], "#p": [s["pubkey"]]}):
            answer = wire.read_answer(secret, listing["pubkey"], event)
            if answer is None or answer["offer"] != s["event"]["id"]:
                continue
            if answer["ok"]:
                if hashlib.sha256(answer["record"]).hexdigest() != listing["sha256"]:
                    s["status"], s["reason"] = "failed", "the lister's app sent a Pokemon that is not the one listed"
                    said.append(f"{item.title()}: {s['reason']}; your Pokemon was traded away")
                else:
                    (item.path / f"received.{s['format']}").write_bytes(answer["record"])
                    s["status"] = "traded"
                    item.save()
                    got = self._bank_received(item, answer["record"], f"GTS trade for {item.title()}"
                                              + (f" with {listing['trainer']}" if listing.get("trainer") else ""),
                                              listing.get("language", ""))
                    said.append(f"{item.title()} was traded for {got}, now in the bank")
            else:
                self._return_offer(item, answer["reason"] or "the lister's app refused it", said)
            s["ended"] = int(self.clock())
            item.save()
            pool.publish(wire.deletion_event(secret, s["event"]["id"]))
            return
        if not pool.reached():
            return                      # no relay answered: nothing is known
        # No answer yet: is the listing still open?
        current = None
        for event in pool.fetch({"kinds": [wire.LISTING], "authors": [listing["pubkey"]], "#d": [listing["id"]]}):
            parsed = wire.parse_listing(event)
            if parsed and (current is None or current.event["created_at"] < event["created_at"]):
                current = parsed
        now = self.clock()
        if current is not None and current.status == "withdrawn":
            self._return_offer(item, "the listing was taken down", said)
        elif current is not None and current.status == "traded" and current.winner != s["event"]["id"]:
            self._return_offer(item, "the listing was traded with someone else", said)
        elif now > listing["expires"] + wire.SKEW:
            self._return_offer(item, "the listing ended", said)
        else:
            return
        s["ended"] = int(now)
        item.save()
        pool.publish(wire.deletion_event(secret, s["event"]["id"]))

    def _return_offer(self, item: Item, reason: str, said: list[str]) -> None:
        item.state["status"], item.state["reason"] = "returned", reason
        item.save()
        self._give_back(item)
        said.append(f"{item.title()} is back in the bank: {reason}")

    def _clean(self) -> None:
        now = self.clock()
        for item in self.items():
            ended = item.state.get("ended")
            if not item.live and ended and now - ended > KEPT:
                shutil.rmtree(item.path, ignore_errors=True)
