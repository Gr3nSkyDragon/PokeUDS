"""Finding a partner by room and code, and the trade channel to it (docs/online.md, Online trades).

Everyone running the same room with the same code publishes to one room on the relays. Each app announces itself
every few seconds; the lower signing key invites a higher one that is free, and an accepted invite pairs the two.
From then on they exchange numbered, acknowledged messages encrypted with an X25519 key only the pair holds. A trade
drives ``Partner`` from its own loop (through ``pokeuds.online.trade.Offers``): every method returns at once and
every answer is something it polls.

Ported from pokeldn's ``pokeldn/online/link.py`` (Decryptu/pokeldn, AGPL-3.0; PokeUDS is AGPL-3.0 too) with its
notice kept: the protocol is pokeldn's, the prefixes PokeUDS's own (``pokeuds online 1``, ``pokeuds room key|``,
``pokeuds pair|``), so the two apps never share a room. PokeUDS's changes: its own relay pool (``relays.LivePool``),
signing (``schnorr``), status events for the app, and shared blocks whose names and sizes the caller gives.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time

from Crypto.Cipher import AES
from Crypto.Protocol import DH
from Crypto.PublicKey import ECC

from .. import status
from . import relays, schnorr

log = logging.getLogger(__name__)

PROTOCOL = 1
KIND = 21059                 # ephemeral (NIP-01: 20000-29999): relays pass it on and keep nothing
ANNOUNCE_PERIOD = 2.5
PEER_STALE = 8.0             # a peer silent this long is no longer offered an invite
INVITE_TIMEOUT = 6.0
SKIP_AFTER_REFUSAL = 10.0
RESEND_PERIOD = 2.0
PING_PERIOD = 4.0
HELLO_TIMEOUT = 12.0         # paired, but nothing heard back: search again
LOST_AFTER = 45.0
TICK = 0.1
ACK_DELAY = 0.3              # one ack covers what arrives together; relays rate-limit bursts
EVENT_LIMIT = 8192
PEERS_LIMIT = 128
WINDOW = 64
ROUND_WINDOW = 2
CHECKS_PER_MINUTE = 30
RECORD_LIMIT = 768
NAME_LIMIT = 24
MESSAGES = {"invite", "yes", "no", "hello", "ping", "bye", "offer", "share", "withdraw", "accept", "unaccept",
            "refused", "done"}


def _hex(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _text(value, limit: int) -> bool:
    return isinstance(value, str) and len(value) <= limit and all(ord(c) >= 32 and ord(c) != 127 for c in value)


def _record(value, limit: int) -> bytes | None:
    if not isinstance(value, str) or len(value) > 4 * ((limit + 2) // 3):
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError:
        return None
    return raw if 0 < len(raw) <= limit else None


def digest(record: bytes) -> str:
    """What an ``accept`` names: the record accepted, so a yes to an offer since taken back counts for nothing."""
    return hashlib.sha256(record).hexdigest()[:32]


def room_id(room: str, code: str) -> str:
    """Everyone with this room (``gen6``, ``gen7``, ...) and code shares it; an empty code is the open room."""
    text = f"pokeuds online {PROTOCOL}|{room}|{code or 'open'}"
    return hashlib.sha256(text.encode()).hexdigest()


def _seal(key: bytes, payload: dict) -> str:
    nonce = os.urandom(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    body, tag = cipher.encrypt_and_digest(json.dumps(payload, separators=(",", ":")).encode())
    return base64.b64encode(nonce + body + tag).decode()


def _open(key: bytes, content: str) -> dict | None:
    if not isinstance(content, str) or len(content) > EVENT_LIMIT:
        return None
    try:
        raw = base64.b64decode(content, validate=True)
        cipher = AES.new(key, AES.MODE_GCM, nonce=raw[:12])
        payload = json.loads(cipher.decrypt_and_verify(raw[12:-16], raw[-16:]))
    except (ValueError, KeyError, TypeError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None


class Identity:
    """A throwaway signing key: one per session, so nothing links two sessions."""

    def __init__(self, secret: bytes | None = None) -> None:
        self.secret = secret or schnorr.new_secret()
        self.public = schnorr.public_key(self.secret).hex()

    def event(self, kind: int, tags: list, content: str, created_at: int | None = None) -> dict:
        return relays.sign_event(self.secret, kind, content, tags, created_at)


class Round:
    """What the partner said about one trade."""

    def __init__(self) -> None:
        self.offer: bytes | None = None
        self.what = ""                       # the offer as the checker read it, for the player
        self.accepted = False
        self.withdrawn = 0
        self.done = False
        self.refused = ""                    # why the partner's app refused our offer
        self.shared: dict[str, bytes] = {}   # what else their console sent, by name


class Partner:
    """One online partner for a trade. States: connecting, searching, paired, lost, closed.

    ``validate(record) -> (reason refused or None, what it is)`` checks each record the partner offers before the
    trade sees it (``pokeuds.online.trade.checker``). ``shared_sizes`` names the other blocks a trade may relay
    (``share``) and the exact size of each; a block of any other name or size is dropped."""

    def __init__(self, room: str, code: str = "", name: str = "", app: str = "", say=None, validate=None,
                 pool_factory=None, clock=time.time, shared_sizes: dict[str, int] | None = None) -> None:
        self.room_name, self.code, self.name, self.app = room, code or "", name[:NAME_LIMIT], app[:NAME_LIMIT]
        self.say = say or (lambda text: None)
        self.validate, self.clock = validate, clock
        self.shared_sizes = dict(shared_sizes or {})
        self.room = room_id(room, self.code)
        self.room_key = hashlib.sha256(b"pokeuds room key|" + bytes.fromhex(self.room)).digest()
        self.me = Identity()
        self.dh = ECC.generate(curve="curve25519")
        self.dh_public = self.dh.public_key().export_key(format="raw").hex()
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.state = "connecting"
        self.peers: dict[str, dict] = {}       # signing key -> {x, name, app, seen, first, busy}
        self.skip: dict[str, float] = {}
        self.invite = None                    # (peer, deadline, x)
        self.peer = self.peer_name = self.peer_x = None
        self.key = None
        self.paired_at = 0.0
        self.heard = 0.0
        self.out_seq = 0
        self.unacked: dict[int, dict] = {}    # seq -> message
        self.last_sent = 0.0
        self.in_next = 1
        self.early: dict[int, dict] = {}
        self.ack_due = 0.0
        self.last_announce = 0.0
        self.round = 1
        self.rounds: dict[int, Round] = {}
        self.checks: list[float] = []
        self.shared_checks: list[float] = []
        self.events: list[float] = []
        self.ended = ""                       # why the partner left
        self.traded = False                   # an offer went either way: a lost partner stays lost
        self.mine = {"offer": None, "accepted": False, "shared": {}}   # the local console, this round
        factory = pool_factory or (lambda on_event: relays.LivePool(relays.live_relay_urls(), on_event))
        self.pool = factory(self._on_event)
        self.thread = threading.Thread(target=self._run, name="online partner", daemon=True)

    def _news(self, text: str, **fields) -> None:
        """A line for the player and the log, and the same as a status event for the app."""
        log.info("online: %s", text)
        self.say(f"[online] {text}")
        status.emit("online", state=self.state, text=text, **fields)

    # -- the trade's side ---------------------------------------------------------------------------

    def start(self, thread: bool = True) -> None:
        """thread=False leaves the ticking to the caller's ``tick()``, for a test on a fake clock."""
        self.pool.subscribe("pokeuds", {"kinds": [KIND], "#t": [self.room], "since": int(self.clock()) - 10})
        self.pool.start()
        if thread:
            self.thread.start()
        where = f"code {self.code}" if self.code else "no code"
        self._news(f"looking for a partner: {self.room_name}, {where}", room=self.room_name, code=self.code)

    def close(self, reason: str = "closed") -> None:
        if self.closed.is_set():
            return
        with self.lock:
            if self.state == "paired":
                self._send({"t": "bye", "why": reason}, reliable=False)
            self.state = "closed"
        self.closed.set()
        # The relays' threads take a moment to see it; a trade's loop never waits on them.
        threading.Thread(target=self.pool.close, name="online close", daemon=True).start()

    @property
    def paired(self) -> bool:
        return self.state == "paired"

    def theirs(self) -> Round:
        with self.lock:
            return self.rounds.setdefault(self.round, Round())

    # What the local console did this round is kept, so a partner found later hears it.

    def offer(self, record: bytes) -> None:
        """The local console offers ``record`` in this round."""
        with self.lock:
            self.mine = {"offer": bytes(record), "accepted": False, "shared": self.mine["shared"]}
            theirs = self.theirs()
            theirs.refused, theirs.accepted = "", False      # whatever they said was about another offer
            self._send(self._offer_message())

    def withdraw(self) -> None:
        with self.lock:
            self.mine = {"offer": None, "accepted": False, "shared": self.mine["shared"]}
            self.theirs().accepted = False
            self._send({"t": "withdraw", "r": self.round})

    def share(self, key: str, data: bytes) -> None:
        """Something else the local console sent this round that the partner's trade replays (a Game Boy party
        block)."""
        with self.lock:
            self.mine["shared"][key] = bytes(data)
            self._send(self._share_message(key))

    def _share_message(self, key: str) -> dict:
        self.traded = self.traded or self.state == "paired"   # a shared party is the partner's own
        return {"t": "share", "r": self.round, "k": key, "d": base64.b64encode(self.mine["shared"][key]).decode()}

    def accept(self, record: bytes | None = None) -> None:
        """The local console said yes to ``record``, the partner's offer it was shown (default: the partner's offer
        now). The partner takes the yes only while that is still what it offers."""
        with self.lock:
            record = self.theirs().offer if record is None else record
            self.mine["accepted"] = digest(record) if record else True
            self._send(self._accept_message())

    def _accept_message(self) -> dict:
        accepted = self.mine["accepted"]
        return {"t": "accept", "r": self.round, **({"h": accepted} if isinstance(accepted, str) else {})}

    def unaccept(self) -> None:
        with self.lock:
            self.mine["accepted"] = False
            self._send({"t": "unaccept", "r": self.round})

    def _offer_message(self) -> dict:
        self.traded = self.traded or self.state == "paired"
        return {"t": "offer", "r": self.round, "d": base64.b64encode(self.mine["offer"]).decode()}

    def refuse(self, reason: str) -> None:
        self._send({"t": "refused", "r": self.round, "why": reason[:200]})

    def done(self) -> None:
        """This round's trade completed on the local console; the next round starts."""
        with self.lock:
            self._send({"t": "done", "r": self.round})
            self.round += 1
            self.rounds = {n: r for n, r in self.rounds.items() if n >= self.round - 1}
            self.mine = {"offer": None, "accepted": False, "shared": {}}

    # -- relay events, on a relay's thread -------------------------------------------------------------

    def _on_event(self, event) -> None:
        now = self.clock()
        if (not isinstance(event, dict) or not isinstance(event.get("content"), str)
                or len(event["content"]) > EVENT_LIMIT
                or type(event.get("created_at")) is not int
                or not now - LOST_AFTER <= event["created_at"] <= now + 10):
            return
        tags = event.get("tags")
        if (not isinstance(tags, list) or len(tags) > 8
                or any(not isinstance(t, list) or not 1 <= len(t) <= 4
                       or any(not isinstance(v, str) or len(v) > 128 for v in t) for t in tags)):
            return
        if event.get("pubkey") == self.me.public or event.get("kind") != KIND:
            return
        if ["t", self.room] not in tags:
            return
        with self.lock:
            self.events = [t for t in self.events if now - t < 1]
            if len(self.events) >= 32:
                return
            self.events.append(now)
        if not relays.valid(event):
            return
        outer = _open(self.room_key, event.get("content", ""))
        if outer is None:
            return
        sender = event["pubkey"]
        to = next((t[1] for t in tags if len(t) >= 2 and t[0] == "p"), None)
        with self.lock:
            if outer.get("t") == "here":
                self._on_announce(sender, outer)
            elif to == self.me.public and isinstance(outer.get("x"), str) and isinstance(outer.get("c"), str):
                if self.state == "paired" and (sender != self.peer or outer["x"] != self.peer_x):
                    return
                if self.invite and sender == self.invite[0] and outer["x"] != self.invite[2]:
                    return
                key = self._pair_key(sender, outer["x"])
                inner = _open(key, outer["c"]) if key else None
                if inner is not None:
                    self._on_direct(sender, outer["x"], key, inner)

    def _on_announce(self, sender: str, body: dict) -> None:
        if (body.get("v") != PROTOCOL or not _hex(body.get("x"))
                or not _text(body.get("n", ""), NAME_LIMIT) or not _text(body.get("a", ""), 64)
                or type(body.get("busy")) is not bool):
            return
        now = self.clock()
        known = self.peers.get(sender)
        if not known and len(self.peers) >= PEERS_LIMIT:
            return
        self.peers[sender] = {"x": body["x"], "name": str(body.get("n", ""))[:NAME_LIMIT],
                              "app": str(body.get("a", ""))[:NAME_LIMIT], "seen": now,
                              "first": known["first"] if known else now, "busy": bool(body.get("busy"))}

    def _pair_key(self, peer: str, x_hex: str) -> bytes | None:
        if not _hex(x_hex):
            return None
        try:
            shared = DH.key_agreement(static_priv=self.dh,
                                      static_pub=DH.import_x25519_public_key(bytes.fromhex(x_hex)),
                                      kdf=lambda z: z)
        except ValueError:
            return None
        ends = "|".join(sorted((f"{self.me.public}:{self.dh_public}", f"{peer}:{x_hex}")))
        return hashlib.sha256(b"pokeuds pair|" + self.room.encode() + b"|" + ends.encode() + shared).digest()

    def _direct(self, peer: str, x_hex: str, key: bytes, inner: dict) -> None:
        content = _seal(self.room_key, {"x": self.dh_public, "c": _seal(key, inner)})
        self.pool.publish(self.me.event(KIND, [["t", self.room], ["p", peer]], content, int(self.clock())))

    def _on_direct(self, sender: str, x_hex: str, key: bytes, msg: dict) -> None:
        kind = msg.get("t")
        now = self.clock()
        if not isinstance(kind, str) or kind not in MESSAGES:
            return
        if not _text(msg.get("n", ""), NAME_LIMIT) or not _text(msg.get("why", ""), 200):
            return
        if self.state == "paired" and (sender != self.peer or x_hex != self.peer_x or key != self.key):
            return
        if self.invite and sender == self.invite[0] and x_hex != self.invite[2]:
            return
        if kind == "invite":
            if self.state == "searching" and self.invite is None and sender < self.me.public:
                self._direct(sender, x_hex, key, {"t": "yes", "n": self.name})
                self._pair(sender, x_hex, key, str(msg.get("n", ""))[:NAME_LIMIT])
            else:
                self._direct(sender, x_hex, key, {"t": "no"})
            return
        if kind in ("yes", "no"):
            if self.invite and self.invite[0] == sender and self.state == "searching":
                if kind == "yes":
                    self._pair(sender, x_hex, key, str(msg.get("n", ""))[:NAME_LIMIT])
                else:
                    self.skip[sender] = now + SKIP_AFTER_REFUSAL
                    self.invite = None
            elif kind == "yes" and not (self.state == "paired" and sender == self.peer):
                self._direct(sender, x_hex, key, {"t": "bye", "why": "taken"})
            return
        if self.state == "searching" and self.invite and self.invite[0] == sender and isinstance(msg.get("s"), int):
            # Relays do not keep order: the partner's first message can overtake its yes.
            self._pair(sender, x_hex, key, self.peers.get(sender, {}).get("name", ""))
        if self.state != "paired" or sender != self.peer:
            if kind not in ("bye", "ping"):
                self._direct(sender, x_hex, key, {"t": "bye", "why": "not paired"})
            return
        self.heard = now
        ack = msg.get("a")
        if type(ack) is int and 0 <= ack <= self.out_seq:
            for seq in [s for s in self.unacked if s <= ack]:
                del self.unacked[seq]
        seq = msg.get("s")
        if kind == "bye":
            self._lose(f"the partner left ({msg.get('why', '')})".replace(" ()", ""))
            return
        if type(seq) is not int or not 1 <= seq < self.in_next + WINDOW:
            return
        if seq >= self.in_next:
            self.early[seq] = msg
        while self.in_next in self.early:
            self._deliver(self.early.pop(self.in_next))
            if self.state != "paired":
                return
            self.in_next += 1
        self.ack_due = self.ack_due or now + ACK_DELAY

    def _deliver(self, msg: dict) -> None:
        kind, number = msg.get("t"), msg.get("r")
        if kind == "hello":
            self._news(f"connected to {self.peer_name or 'a partner'}", partner=self.peer_name)
            return
        if type(number) is not int or not max(1, self.round - 1) <= number <= self.round + ROUND_WINDOW:
            return
        theirs = self.rounds.setdefault(number, Round())
        if kind == "offer":
            theirs.offer, theirs.accepted = None, False
            record = _record(msg.get("d"), RECORD_LIMIT)
            if record is None:
                return
            now = self.clock()
            self.checks = [t for t in self.checks if now - t < 60]
            if len(self.checks) >= CHECKS_PER_MINUTE:
                self._lose("the partner sent too many Pokemon to check")
                return
            self.checks.append(now)
            reason, what = self.validate(record) if self.validate else (None, "a Pokemon")
            if reason:
                self._news(f"refused the partner's Pokemon: {reason}", why=reason)
                self._send({"t": "refused", "r": number, "why": reason[:200]})
                return
            theirs.offer, theirs.what, theirs.accepted, theirs.refused = record, what, False, ""
            self.traded = True
            self._news(f"{self.peer_name} offers {what}", partner=self.peer_name, offer=what, round=number)
        elif kind == "share" and isinstance(msg.get("k"), str):
            size = self.shared_sizes.get(msg["k"])
            record = _record(msg.get("d"), size) if size else None
            if record is None or len(record) != size:
                return
            if theirs.shared.get(msg["k"]) == record:
                return
            now = self.clock()
            self.shared_checks = [t for t in self.shared_checks if now - t < 60]
            if len(self.shared_checks) >= CHECKS_PER_MINUTE * 3:
                self._lose("the partner sent too many blocks to check")
                return
            self.shared_checks.append(now)
            theirs.shared[msg["k"]] = record
        elif kind == "withdraw":
            theirs.offer, theirs.accepted = None, False
            theirs.withdrawn += 1
            self._news(f"{self.peer_name} took back their offer", partner=self.peer_name, offer="", round=number)
        elif kind == "accept":
            mine = self.mine["offer"] if number == self.round else None
            if mine is None or msg.get("h") not in (None, digest(mine)):
                return                          # a yes to an offer this console no longer makes
            theirs.accepted = True
            self._news(f"{self.peer_name} confirmed", partner=self.peer_name, round=number)
        elif kind == "unaccept":
            theirs.accepted = False
            self._news(f"{self.peer_name} took back their confirmation", partner=self.peer_name, round=number)
        elif kind == "refused":
            theirs.refused = str(msg.get("why", ""))[:200] or "refused"
            self._news(f"{self.peer_name}'s app refused your Pokemon: {theirs.refused}", partner=self.peer_name,
                       why=theirs.refused, round=number)
        elif kind == "done":
            theirs.done = True
            self._news(f"{self.peer_name}'s trade {number} is complete", partner=self.peer_name, offer="",
                       round=number)

    def _pair(self, peer: str, x_hex: str, key: bytes, name: str) -> None:
        self.state = "paired"
        self.peer, self.peer_name, self.key, self.peer_x = peer, name or "a partner", key, x_hex
        self.invite = None
        self.paired_at = self.heard = self.clock()
        self._news(f"paired with {self.peer_name}", partner=self.peer_name)
        self._announce(force=True)
        self._send({"t": "hello", "n": self.name, "a": self.app})
        for name in self.mine["shared"]:
            self._send(self._share_message(name))
        if self.mine["offer"] is not None:
            self._send(self._offer_message())
            if self.mine["accepted"]:
                self._send(self._accept_message())

    def _lose(self, why: str) -> None:
        if self.state != "paired":
            return
        if not self.traded:
            # Nothing was offered on either side yet: look for someone else.
            self._reset_search()
            self._news(f"{why}; searching again")
            return
        self.state = "lost"
        self.ended = why
        # What a gone partner offered cannot be traded: every trade withdraws it from its console.
        theirs = self.rounds.setdefault(self.round, Round())
        theirs.offer, theirs.accepted = None, False
        self._news(why, why=why)

    # -- sending ---------------------------------------------------------------------------------------

    def _send(self, msg: dict, reliable: bool = True) -> None:
        with self.lock:
            if self.state != "paired":
                return
            body = dict(msg, a=self.in_next - 1)
            if reliable:
                if len(self.unacked) >= WINDOW:
                    self._lose("the partner stopped acknowledging messages")
                    return
                self.out_seq += 1
                body["s"] = self.out_seq
                self.unacked[self.out_seq] = body
            self._direct(self.peer, self.peer_x, self.key, body)
            self.last_sent = self.clock()
            self.ack_due = 0.0

    def _announce(self, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self.last_announce < ANNOUNCE_PERIOD:
            return
        self.last_announce = now
        body = {"t": "here", "v": PROTOCOL, "x": self.dh_public, "n": self.name, "a": self.app,
                "busy": self.state != "searching"}
        self.pool.publish(self.me.event(KIND, [["t", self.room]], _seal(self.room_key, body), int(now)))

    # -- the ticker ------------------------------------------------------------------------------------

    def _run(self) -> None:
        while not self.closed.wait(TICK):
            self.tick()

    def tick(self) -> None:
        try:
            with self.lock:
                self._tick()
        except Exception:  # noqa: BLE001 - the ticker must outlive one bad turn
            log.exception("online partner")

    def _tick(self) -> None:
        now = self.clock()
        self.peers = {p: info for p, info in self.peers.items() if now - info["seen"] < PEER_STALE}
        self.skip = {p: deadline for p, deadline in self.skip.items() if deadline > now}
        if self.state == "connecting":
            if self.pool.connected():
                self.state = "searching"
                self._news("waiting for a partner")
            return
        if self.state == "searching":
            self._announce()
            if self.invite and now > self.invite[1]:
                self.skip[self.invite[0]] = now + SKIP_AFTER_REFUSAL
                self.invite = None
            if self.invite is None:
                free = [(info["first"], peer) for peer, info in self.peers.items()
                        if peer > self.me.public and not info["busy"]
                        and now - info["seen"] < PEER_STALE and self.skip.get(peer, 0) < now]
                if free:
                    peer = min(free)[1]
                    x_hex = self.peers[peer]["x"]
                    key = self._pair_key(peer, x_hex)
                    if key:
                        self.invite = (peer, now + INVITE_TIMEOUT, x_hex)
                        self._direct(peer, x_hex, key, {"t": "invite", "n": self.name})
            return
        if self.state != "paired":
            return
        self._announce()
        if self.in_next == 1 and now - self.paired_at > HELLO_TIMEOUT:
            # The invite's answer was lost on the other side; nothing came back.
            self._reset_search()
            self._news("the partner did not answer; searching again")
            return
        if now - self.heard > LOST_AFTER:
            self._lose("lost the partner")
            return
        if self.unacked and now - self.last_sent > RESEND_PERIOD:
            for body in list(self.unacked.values()):
                self._direct(self.peer, self.peer_x, self.key, dict(body, a=self.in_next - 1))
            self.last_sent = now
        elif now - self.last_sent > PING_PERIOD or (self.ack_due and now >= self.ack_due):
            self._send({"t": "ping"}, reliable=False)

    def _reset_search(self) -> None:
        self.skip[self.peer] = self.clock() + SKIP_AFTER_REFUSAL
        self.state = "searching"
        self.rounds, self.round = {}, 1
        self.peer = self.peer_name = self.key = self.peer_x = None
        self.out_seq, self.unacked, self.in_next, self.early, self.ack_due = 0, {}, 1, {}, 0.0
        self._announce(force=True)
