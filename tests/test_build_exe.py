"""scripts/build_exe.py's macOS finishing steps, run anywhere (codesign stood in for)."""

import importlib.util
import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location("build_exe", ROOT / "scripts" / "build_exe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_mac_bundle_is_a_background_app_signed_again(tmp_path, monkeypatch):
    """The bundle's own process never opens a window: LSUIElement keeps it out of the Dock (no second icon, no
    "Application Not Responding"), every other key stays, and the bundle is signed again and checked."""
    build = _script()
    app = tmp_path / "PokeUDS.app"
    (app / "Contents").mkdir(parents=True)
    with (app / "Contents" / "Info.plist").open("wb") as f:
        plistlib.dump({"CFBundleIdentifier": build.BUNDLE_ID, "CFBundleName": "PokeUDS"}, f)
    build.background_app(app)
    with (app / "Contents" / "Info.plist").open("rb") as f:
        plist = plistlib.load(f)
    assert plist == {"CFBundleIdentifier": build.BUNDLE_ID, "CFBundleName": "PokeUDS", "LSUIElement": True}

    runs = []
    monkeypatch.setattr(build.subprocess, "run", lambda args, **kw: runs.append(args))
    build.resign(app)
    assert runs == [["codesign", "--force", "--sign", "-", str(app)],
                    ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)]]
