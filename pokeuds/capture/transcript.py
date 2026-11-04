"""Trade transcripts: the game-level messages of a session, from any capture source.

Sources:

- An Azahar (azahar_uds) log. Azahar runs the game's UDS service itself, so its log holds every
  payload the game sent (``UDS DATA TRACE TX GAME``) and received (``UDS DATA TRACE RX GAME``) in
  plaintext, plus the network parameters and the game's passphrase
  (``ConnectToNetwork key-derivation input``) when the emulated game joins.
- An azahar_uds passive sniff log (``AZAHAR_UDS_SNIFF=1``): ``UDS SNIFF DATA`` lines.
- A JSON-lines transcript written by ``pokeuds sniff``.

Each becomes a list of ``Message``; ``analyze`` summarizes them and finds Pokemon records inside.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import pk6
from ..util import parse_hex

_PAIRS = re.compile(r"(\w+)=([^,]*)")
_TIME = re.compile(r"^\[\s*([0-9.]+)\]")


@dataclass
class Message:
    index: int
    time: float | None
    direction: str          # "tx"/"rx" from the emulated console's view; "host"/"client" when sniffed
    channel: int
    sequence: int
    src_node: int
    dest_node: int
    payload: bytes
    source: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        d["payload"] = self.payload.hex()
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Message":
        return cls(**{**d, "payload": bytes.fromhex(d["payload"])})


@dataclass
class SessionFacts:
    """Network parameters a log reveals; what a game profile needs."""

    passphrases: set[str] = field(default_factory=set)
    wlan_comm_ids: set[int] = field(default_factory=set)
    network_ids: set[int] = field(default_factory=set)
    hosting: list[dict] = field(default_factory=list)


def _fields(line: str) -> dict[str, str]:
    return {k: v.strip() for k, v in _PAIRS.findall(line)}


def _int(value: str) -> int:
    return int(value, 0)


def parse_azahar_log(path: str | Path) -> tuple[list[Message], SessionFacts]:
    messages: list[Message] = []
    facts = SessionFacts()
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        stamp_match = _TIME.match(raw)
        stamp = float(stamp_match.group(1)) if stamp_match else None
        if "UDS DATA TRACE TX GAME:" in raw or "UDS DATA TRACE RX GAME:" in raw:
            f = _fields(raw.split("GAME:", 1)[1])
            messages.append(Message(len(messages), stamp, "tx" if "TX GAME" in raw else "rx", int(f["channel"]),
                                    int(f["secureSequence"]), int(f["sourceNode"]), int(f["destinationNode"]),
                                    parse_hex(f.get("payload", "")), "azahar"))
        elif "UDS SNIFF DATA:" in raw:
            f = _fields(raw.split("DATA:", 1)[1])
            if f.get("management") in ("1", "true"):
                continue
            messages.append(Message(len(messages), stamp, f["role"], int(f["channel"]), int(f["secureSequence"]),
                                    int(f["sourceNode"]), int(f["destinationNode"]),
                                    parse_hex(f.get("payload", "")), "azahar-sniff"))
        elif "ConnectToNetwork key-derivation input" in raw:
            f = _fields(raw)
            if "inputBytes" in f:
                facts.passphrases.add(parse_hex(f["inputBytes"]).hex())
            if "wlanCommId" in f:
                facts.wlan_comm_ids.add(_int(f["wlanCommId"]))
            if "networkId" in f:
                facts.network_ids.add(_int(f["networkId"]))
        elif "BeginHostingNetwork #" in raw:
            f = _fields(raw)
            facts.hosting.append(f)
            if "wlanCommId" in f:
                facts.wlan_comm_ids.add(_int(f["wlanCommId"]))
    return messages, facts


def load_jsonl(path: str | Path) -> list[Message]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if "payload" in record and "channel" in record:
                out.append(Message.from_json(record))
    return out


def load(path: str | Path) -> tuple[list[Message], SessionFacts]:
    text_head = Path(path).read_text(encoding="utf-8", errors="replace")[:1]
    if text_head == "{":
        return load_jsonl(path), SessionFacts()
    return parse_azahar_log(path)


def write_jsonl(messages: list[Message], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as out:
        for message in messages:
            out.write(json.dumps(message.to_json()) + "\n")


@dataclass
class FoundRecord:
    message: int
    offset: int
    size: int
    summary: pk6.Summary
    record: bytes


def find_pokemon(messages: list[Message]) -> list[FoundRecord]:
    """Every Gen 6 record inside any message, also across consecutive fragments of one channel."""
    found = []
    for message in messages:
        for offset, size, record in pk6.find_records(message.payload):
            found.append(FoundRecord(message.index, offset, size, pk6.summary(record), record))
    if found:
        return found
    # A record may be split across messages: try each direction/channel stream concatenated.
    streams: dict[tuple[str, int], list[Message]] = defaultdict(list)
    for message in messages:
        streams[(message.direction, message.channel)].append(message)
    for stream in streams.values():
        joined, starts = b"", []
        for message in stream:
            starts.append((len(joined), message.index))
            joined += message.payload
        for offset, size, record in pk6.find_records(joined):
            first = max((s for s in starts if s[0] <= offset), default=(0, stream[0].index))
            found.append(FoundRecord(first[1], offset - first[0], size, pk6.summary(record), record))
    return found


def summarize(messages: list[Message]) -> str:
    lines = [f"{len(messages)} game messages"]
    by_key = Counter((m.direction, m.channel) for m in messages)
    for (direction, channel), count in sorted(by_key.items()):
        sizes = Counter(len(m.payload) for m in messages if m.direction == direction and m.channel == channel)
        common = ", ".join(f"{size} B x{n}" for size, n in sizes.most_common(6))
        lines.append(f"  {direction:>6} channel {channel}: {count} messages ({common})")
    heads = Counter((m.direction, m.payload[:4].hex()) for m in messages if m.payload)
    if heads:
        lines.append("  most common leading bytes:")
        for (direction, head), count in heads.most_common(12):
            lines.append(f"    {direction:>6} {head:<8} x{count}")
    return "\n".join(lines)
