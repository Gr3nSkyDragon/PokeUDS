"""Write or update a plugin source listing (pokeuds-source.json) for packages (docs/plugins.md, Publishing a source).

    python scripts/plugin_source.py hello-1.0.0.udsplugin -o pokeuds-source.json --name "My plugins" \\
        --download "https://github.com/me/my-plugins/releases/download/{id}-{version}/{file}"

An entry already listed at a higher version is kept.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeuds.app import plugin_store as store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("packages", nargs="+")
    parser.add_argument("-o", "--out", default="pokeuds-source.json")
    parser.add_argument("--name", default="PokeUDS plugins")
    parser.add_argument("--homepage", default="")
    parser.add_argument("--download", required=True, help="the address, with {id}, {version} and {file}")
    args = parser.parse_args()
    out = Path(args.out)
    body = json.loads(out.read_text()) if out.exists() else {"schema": 1, "plugins": []}
    body.update(schema=1, name=args.name, **({"homepage": args.homepage} if args.homepage else {}))
    entries = {e["id"]: e for e in body.get("plugins", [])}
    for path in args.packages:
        entry = store.listing_entry(path, args.download)
        old = entries.get(entry["id"])
        if old and store._version(old["version"]) > store._version(entry["version"]):
            print(f"kept {old['id']} {old['version']} (newer than {entry['version']})")
            continue
        entries[entry["id"]] = entry
        print(f"listed {entry['id']} {entry['version']}")
    body["plugins"] = list(entries.values())
    out.write_text(json.dumps(body, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
