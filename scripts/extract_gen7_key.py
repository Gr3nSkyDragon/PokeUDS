#!/usr/bin/env python3
"""Extract the Gen 7 distributor's OPSSR signing key from your own distributor app.

The private key exists only in the **Gen 7 Local Distributor** (title 00040000001A8900), the modified
Sun/Moon build that broadcasts events.  Retail Sun/Moon/USUM carry only the **public** key (the games
verify with it), so pointing this at a retail CXI correctly reports that no private key is there.

Point it at the distributor's decrypted CXI or CCI (.cci/.3ds), its extracted `code.bin`, or the 461-byte PKCS#1
DER (the key is found wherever the container puts it):

    python scripts/extract_gen7_key.py "Gen 7 Local Distributor.cxi" --keys aes_keys.txt

It writes `rsa_d_key.txt` next to `aes_keys.txt` (or `--out`), which PokeUDS reads beside its UDS
keys.  The file is secret: it lets anyone sign Gen 7 local distributions, so do not commit it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pokeuds.games.gen7 import keyextract  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", help="the distributor's decrypted CXI or CCI (.cci/.3ds), a code.bin, or the PKCS#1 DER")
    p.add_argument("--keys", help="the aes_keys.txt whose folder should receive rsa_d_key.txt")
    p.add_argument("--out", help="output path (default: next to --keys, else ./rsa_d_key.txt)")
    args = p.parse_args()

    try:
        data = Path(args.source).read_bytes()
        offset, private = keyextract.extract(data)
    except (OSError, ValueError) as exc:
        print(f"{args.source}: {exc}", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else (
        Path(args.keys).with_name("rsa_d_key.txt") if args.keys else Path("rsa_d_key.txt"))
    try:
        keyextract.write_key(out, private, args.source, offset)
    except OSError as exc:
        print(f"could not write {out}: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {out} (private exponent at 0x{offset:X} in {args.source})")
    print("keep it secret: it is the distributor's signing key, not a public key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
