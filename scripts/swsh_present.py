"""Write pokeuds/app/data/swsh_present.json: the Gen 7 species and forms Sword/Shield have, as PKHeX.Core knows them.

The Bank's Escape Rope filter (pokeuds/app/bank.py ``reaches_swsh``) reads the table, so it works without the PKHeX
helper; the helper (services/pkhex, "present") is what the table is made from. Sword/Shield's Pokedex is final, so
run it again only if PKHeX.Core changes what it counts as present (tests/test_pkhex.py compares the two).

    python scripts/swsh_present.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pokeuds import pkhex  # noqa: E402

OUT = ROOT / "pokeuds" / "app" / "data" / "swsh_present.json"


def main() -> int:
    helper = pkhex.Helper()
    try:
        present = helper.request("present", game="swsh")["present"]
    finally:
        helper.close()
    table = {str(species): forms for species, forms in present}
    OUT.write_text(json.dumps(table, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(table)} species")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
