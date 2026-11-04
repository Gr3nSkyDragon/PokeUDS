"""Every official Gen 6/7 Mystery Gift card PKHeX.Core embeds, end to end (about a minute and a half).

Each card is written as a .wc6/.wc7 into OUT_DIR, opened again through the PKHeX helper, written and opened as
a 784-byte full file, made into its Pokemon for an English trainer, checked by PKHeX and put in a scratch bank.
Prints counts and any failure; exits 1 on a failure.

    python scripts/check_gifts.py OUT_DIR
"""
import base64
import collections
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pokeuds import pkhex, pkx
from pokeuds.app import bank

out = Path(sys.argv[1])
h = pkhex.Helper()
b = bank.Bank(Path(tempfile.mkdtemp()))
tr = {"ot": "DANA", "tid": 2468, "sid": 1357}
stats = collections.Counter()
problems = []
t0 = time.time()
for game in ("xy", "oras", "sm", "usum"):
    cards = h.gifts(game, all=True)
    for c in cards:
        raw = base64.b64decode(c["card_data"])
        ext = "wc6" if game in ("xy", "oras") else "wc7"
        path = out / game / f"{c['card_id']:04d}_{c['kind']}_{c.get('species', 0):03d}_{stats[game]:04d}.{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        stats[game] += 1
        stats[c["kind"]] += 1
        opened = h.gift(game, data=path.read_bytes(), trainer=tr)
        if opened.card_data != raw:
            problems.append((game, path.name, "card bytes changed on the way through"))
        full = h.gift(game, data=opened.full_data, trainer=tr)
        if full.card_data[:] != raw or full.card["card_id"] != c["card_id"]:
            problems.append((game, path.name, "full file did not round-trip"))
        if opened.pokemon is None:
            continue
        p = opened.pokemon
        if p.legal:
            stats["legal"] += 1
        else:
            stats["illegal"] += 1
            problems.append((game, path.name, f"{c['title']!r} {c.get('species_name')}: " + "; ".join(
                l for l in p.report.splitlines() if l.startswith("Invalid"))[:200]))
        rec = p.data if p.extension in ("pk1", "pk2") else pkx.to_stored(p.data)
        entry, _ = b.deposit(rec, p.extension, source="gift")
        if entry.dex != c["species"] or (not c["kind"] == "egg" and entry.level is None):
            problems.append((game, path.name, "bank read it differently"))
h.close()
print(dict(stats), f"{time.time() - t0:.0f} s")
by = collections.Counter(pr[2].split(":")[-1].strip()[:90] for pr in problems)
for reason, n in by.most_common(15):
    print(n, reason)
for pr in problems[:12]:
    print(pr)
sys.exit(1 if problems else 0)
