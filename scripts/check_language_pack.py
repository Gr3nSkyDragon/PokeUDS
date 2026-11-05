"""What a language pack lacks against this release (docs/plugins.md, Language packs).

    python scripts/check_language_pack.py PACK_FOLDER

Lists the app's strings the pack does not translate, keys the app no longer shows, and translations whose
fields differ from the English; exits 1 on a field difference (the app refuses such a pack).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeuds import plugins  # noqa: E402
from pokeuds.app import i18n  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    folder = Path(sys.argv[1])
    manifest = plugins.read_manifest((folder / "plugin.toml").read_bytes())
    if not manifest.get("locale"):
        print(f"{folder} is not a language pack (no locale in plugin.toml)")
        return 2
    path = folder / str(manifest.get("strings", "strings.toml"))
    try:
        strings = i18n.parse(path.read_bytes(), str(path))
    except ValueError as exc:
        print(f"refused: {exc}")
        return 1
    template = i18n.template()
    missing = [k for k in template if k not in strings]
    obsolete = [k for k in strings if k not in template]
    same = [k for k, v in strings.items() if k in template and v == k]
    print(f"{manifest['name']} ({manifest['locale']}): {len(template) - len(missing)} of {len(template)} strings "
          f"translated, {len(same)} left as the English")
    for title, keys in (("not translated", missing), ("no longer in the app", obsolete)):
        if keys:
            print(f"\n{title} ({len(keys)}):")
            for key in keys:
                print(f"  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
