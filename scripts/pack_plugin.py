"""Pack a plugin folder into a .udsplugin, optionally signed (docs/plugins.md, Packing and signing).

    python scripts/pack_plugin.py examples/plugins/hello [-o hello.udsplugin] [--key ~/plugin-signing.pem]

The package is read back as the app reads it. --key signs with an Ed25519 key, created on first use; keep it
private and backed up, and publish the fingerprint this prints.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeuds.app import plugin_store as store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder")
    parser.add_argument("-o", "--out")
    parser.add_argument("--key", help="an Ed25519 key in PEM (created if it does not exist)")
    args = parser.parse_args()
    key = store.signing_key(args.key, create=True) if args.key else None
    out = store.pack(args.folder, args.out, key)
    package = store.read(out)
    print(f"{out} ({store.size_text(out.stat().st_size)}): {package.id} {package.version}")
    if package.key:
        print(f"signed, key fingerprint {store.fingerprint(package.key)}")
    for finding in package.findings:
        print(f"  the install screen will say: {finding}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
