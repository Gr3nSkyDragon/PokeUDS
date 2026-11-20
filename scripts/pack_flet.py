#!/usr/bin/env python3
"""Run Flet's packer while preserving the macOS window's file-picker permissions (pokeldn's scripts/pack_flet.py).

flet pack patches Flet's window (Flet.app: name, icon, version), signs it again ad hoc and packs it into
``flet-macos.tar.gz`` inside PokeUDS.app; Flet unpacks it into ``~/.flet/client`` on first start. Flet's
``macos_utils.assemble_app_bundle`` (1.0.5 to 1.1.2) signs it with ``codesign -s - --force --deep`` and no entitlements,
which drops the ones Flet's own build sealed in, and without ``com.apple.security.files.user-selected.read-write``
file_picker refuses every file dialog: PlatformException(ENTITLEMENT_NOT_FOUND, ...) (a Mac player, 2026-11-20). It is
inside the archive, so nothing after flet pack can fix it.

So, as pokeldn does, ``assemble_app_bundle`` is replaced: the window's own entitlements are read before it is signed
again and sealed back in, and the build stops if the file-picker one is not among them. pack.py imports the function
when it gets there, so replacing the module's attribute first is enough. Unlike pokeldn (which builds its own window and
packs it as xz), the archive stays Flet's: gzip, with fixed timestamps, so its fingerprint is stable. Ad hoc signing, as
before: these entitlements need no Developer ID. scripts/build_exe.py checks the built app carries them. Remove this
when Flet seals the entitlements itself.

    python scripts/pack_flet.py pack ...       # the arguments `flet pack` takes
"""

import gzip
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FILE_DIALOGS = "com.apple.security.files.user-selected.read-write"


def assemble_macos_view(app_path, tar_path):
    import tarfile

    from flet_cli.__pyinstaller.utils import normalize_tar_entry

    # Flet's assemble_app_bundle drops sealed entitlements; the file picker requires user-selected.read-write.
    result = subprocess.run(["codesign", "--display", "--entitlements", "-", "--xml", app_path],
                            capture_output=True, check=True)
    entitlements = plistlib.loads(result.stdout) if result.stdout else {}
    if not entitlements.get(FILE_DIALOGS):
        raise SystemExit(f"Flet's macOS window ({app_path}) lacks its file-picker entitlement ({FILE_DIALOGS}), so "
                         f"file dialogs would fail with ENTITLEMENT_NOT_FOUND. It has: {sorted(entitlements) or 'none'}")
    print(f"(pack_flet) signing Flet's window with its entitlements: {', '.join(sorted(entitlements))}", flush=True)
    with tempfile.TemporaryDirectory(prefix="pokeuds-sign-") as folder:
        path = Path(folder) / "entitlements.plist"
        path.write_bytes(plistlib.dumps(entitlements))
        subprocess.run(["codesign", "--force", "--sign", "-", "--entitlements", str(path), app_path], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", app_path], check=True)
    # Flet's own archive: gzip with fixed timestamps, so identical content gives an identical fingerprint.
    with (open(tar_path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz,
          tarfile.open(fileobj=gz, mode="w") as archive):
        archive.add(app_path, arcname=os.path.basename(app_path), filter=normalize_tar_entry)
    shutil.rmtree(app_path, ignore_errors=True)


if __name__ == "__main__":
    if sys.platform == "darwin":
        from flet_cli.__pyinstaller import macos_utils
        macos_utils.assemble_app_bundle = assemble_macos_view
    from flet_cli.cli import main
    sys.argv[0] = "flet"
    main()
