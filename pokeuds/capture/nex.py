"""Decoding a captured Generation 6/7 session: PRUDP packets, their signatures, the gflnet flow.

X/Y and ORAS run NEX's PRUDP V1 over Pia's local UDS layer, and Sun/Moon and USUM link the same
code (the same checksum key and framing), so one decoder serves both generations' captures.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..games.gen6 import wire
from ..nex import prudp
from .transcript import Message


@dataclass
class Event:
    time: float | None
    node: int
    stream: str           # "netz", "game", "unreliable"
    text: str
    payload: bytes


@dataclass
class Decoded:
    packets: int = 0
    signed_ok: int = 0
    not_prudp: int = 0
    events: list[Event] | None = None


def _describe_game(raw: bytes) -> str:
    try:
        m = wire.gfl_decode(raw)
    except ValueError as exc:
        return f"gflnet ? ({exc}), {len(raw)} B"
    if not m.body:
        return ""
    parts = []
    for b in wire.parse_bodies(m.body):
        if b.kind == "sync":
            parts.append(f"sync {'03' if b.done else '02'} module {b.module} id {b.ident:02X}")
        elif b.kind == "command":
            parts.append(f"command module {b.module} cmd {b.ident:02X}, {len(b.data)} B: {b.data[:12].hex(' ')}")
        else:
            parts.append(f"{b.kind}, {len(b.data)} B: {b.data[:12].hex(' ')}")
    return " + ".join(parts)


def decode(messages: list[Message]) -> Decoded:
    out = Decoded(events=[])
    seen: set[tuple[int, int, int]] = set()
    fragments: dict[tuple[int, bool], list[bytes]] = {}
    for message in messages:
        try:
            src, dst, raw = prudp.unwrap(message.payload)
            p = prudp.decode(raw)
        except ValueError:
            out.not_prudp += 1
            continue
        out.packets += 1
        if dst in prudp.CONNECTION_SIGNATURE and p.signature == prudp.signature(p, prudp.CONNECTION_SIGNATURE[dst]):
            out.signed_ok += 1
        if p.type != prudp.DATA or p.flags & (prudp.F_ACK | prudp.F_MULTI_ACK):
            continue
        if not p.flags & prudp.F_RELIABLE:
            out.events.append(Event(message.time, src, "unreliable", f"unreliable {len(p.payload)} B", p.payload))
            continue
        key = (src, p.flags, p.seq)
        if key in seen:
            continue
        seen.add(key)
        game = bool(p.flags & prudp.F_GAME)
        parts = fragments.setdefault((src, game), [])
        parts.append(p.payload)
        if p.fragment:
            continue
        whole = b"".join(parts)
        parts.clear()
        if game:
            text = _describe_game(whole)
            if text:
                out.events.append(Event(message.time, src, "game", text, whole))
        else:
            out.events.append(Event(message.time, src, "netz", f"Net-Z type {wire.netz_kind(whole):02X}, {len(whole)} B",
                                    whole))
    return out


def summarize(decoded: Decoded, flow: bool = False) -> list[str]:
    lines = [f"PRUDP: {decoded.packets} packets, {decoded.signed_ok} with a valid signature"
             + (f", {decoded.not_prudp} other payloads" if decoded.not_prudp else "")]
    game = [e for e in decoded.events if e.stream == "game"]
    netz = [e for e in decoded.events if e.stream == "netz"]
    lines.append(f"    {len(netz)} Net-Z messages, {len(game)} gflnet messages with a body")
    if flow:
        t0 = next((e.time for e in decoded.events if e.time is not None), 0.0)
        for e in decoded.events:
            if e.stream != "unreliable":
                stamp = f"{e.time - t0:8.2f}" if e.time is not None else "       ?"
                lines.append(f"    {stamp} node {e.node} {e.text}")
    return lines
