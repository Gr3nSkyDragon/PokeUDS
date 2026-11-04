"""Virtual Console captures: the Game Boy byte streams inside channel-243 Pia payloads.

Azahar's ``UDS DATA TRACE`` lines for a VC title carry whole Pia frames. Splitting them into game
units and de-duplicating by stream index (the VC re-sends windows) gives each side's Game Boy serial
stream, which is then searched for party blocks the way the receiving ROM reads them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import gbpk
from ..vc import link, pia
from .transcript import Message


def unit_streams(messages: list[Message]) -> dict[str, list[int]]:
    """direction -> Game Boy bytes in stream order, from every Pia game unit in the messages."""
    by_direction: dict[str, dict[int, int]] = {}
    for message in messages:
        if message.channel != pia.UDS_CHANNEL:
            continue
        frame = pia.parse(message.payload)
        if frame is None or frame.kind != pia.KIND_PIA:
            continue
        for m in frame.messages:
            if m.protocol == pia.PROTO_GAME and (unit := pia.Unit.unpack(m.payload)):
                by_direction.setdefault(message.direction, {}).setdefault(unit.index, unit.byte)
    return {d: [units[i] for i in sorted(units)] for d, units in by_direction.items()}


@dataclass
class FoundParty:
    direction: str
    position: int
    generation: int
    party: link.Party


def find_parties(streams: dict[str, list[int]]) -> list[FoundParty]:
    found = []
    for direction, stream in streams.items():
        i = 0
        while i < len(stream):
            if stream[i:i + 6] != [link.PREAMBLE] * 6:
                i += 1
                continue
            for generation, size in ((2, 450), (1, 424)):
                window = stream[i:i + size]
                patch_at = next((j for j in range(i + size, min(len(stream), i + size + 20))
                                 if stream[j:j + 3] == [link.PREAMBLE] * 3), None)
                patches = stream[patch_at:patch_at + 200] if patch_at else []
                try:
                    party = link.parse_party(window, patches, generation)
                except Exception:
                    continue
                if 1 <= len(party.mons) <= 6 and all(m.dex and 0 < m.level <= 100 for m in party.mons):
                    found.append(FoundParty(direction, i, generation, party))
                    break
            while i < len(stream) and stream[i] == link.PREAMBLE:
                i += 1
    return found


def describe(found: FoundParty) -> str:
    names = ", ".join(m.describe() for m in found.party.mons)
    return (f"{found.direction} at {found.position}: Gen {found.generation} party of "
            f"{gbpk.decode_name(found.party.trainer_name)}: {names}")
