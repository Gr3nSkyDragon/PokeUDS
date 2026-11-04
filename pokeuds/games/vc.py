"""The 3DS Virtual Console Game Boy Pokemon games.

Each title hosts or joins from its own link menu (the VC's host/join screen), on UDS with the
passphrase ``TRL_NETWORK`` + NUL and a comm id that is the title id + 0x10 (measured for Red, Gold
and Silver; the others follow the pattern and are unmeasured). Two networks of the same generation
share the family (``0x00171xxx`` Gen 1, ``0x00172xxx`` Gen 2), which is what joining matches, so a
region's own title ids work too.
"""

from __future__ import annotations

from dataclasses import dataclass

PASSPHRASE = b"TRL_NETWORK\x00"
CHANNEL = 6                    # the retail host in every capture
FAMILY_MASK = 0xFFFFF000
FAMILIES = {1: 0x00171000, 2: 0x00172000}


@dataclass(frozen=True)
class VcTitle:
    key: str
    name: str
    generation: int
    title_id: int
    measured: bool             # comm id seen on the air, not derived

    @property
    def comm_id(self) -> int:
        return (self.title_id & 0xFFFFFFFF) + 0x10


TITLES = {t.key: t for t in (
    VcTitle("red", "Pokemon Red (VC)", 1, 0x0004000000171000, True),
    VcTitle("blue", "Pokemon Blue (VC)", 1, 0x0004000000171100, False),
    VcTitle("yellow", "Pokemon Yellow (VC)", 1, 0x0004000000171200, False),
    VcTitle("gold", "Pokemon Gold (VC)", 2, 0x0004000000172600, True),
    VcTitle("silver", "Pokemon Silver (VC)", 2, 0x0004000000172700, True),
    VcTitle("crystal", "Pokemon Crystal (VC)", 2, 0x0004000000172800, False),
)}


def generation_of(comm_id: int) -> int | None:
    for gen, family in FAMILIES.items():
        if comm_id & FAMILY_MASK == family:
            return gen
    return None


def title_of(comm_id: int) -> VcTitle | None:
    return next((t for t in TITLES.values() if t.comm_id == comm_id), None)
