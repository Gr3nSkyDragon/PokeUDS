"""An offer edit (docs/plugins.md, Offer edits): rename every Game Boy Pokemon PokeUDS offers.

The record is the .pk1/.pk2 file's bytes (01 <species> FF, the party struct, OT name, nickname). PokeUDS runs
PKHeX's legality check on the result; a nickname is always legal, so the trade goes ahead.
"""

import json

from pokeuds import gbpk


def nickname(api) -> str:
    """The name kept in the plugin's data folder (settings.json), UDS when none."""
    try:
        return json.loads((api.data / "settings.json").read_text())["nickname"][:10]
    except (OSError, ValueError, KeyError):
        return "UDS"


def register(api):
    def rename(record: bytes) -> bytes:
        mon = gbpk.Record.from_file(record)
        mon.nickname = gbpk.encode_name(nickname(api))
        return mon.to_file()

    api.add_offer_edit("rby", rename, "Game Boy renamer")
    api.add_offer_edit("gsc", rename, "Game Boy renamer")
