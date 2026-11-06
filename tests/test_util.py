"""Every program PokeUDS starts is started without a window (pokeuds.util.NO_WINDOW).

Started from the app, which has no console, a console program would flash a window open and shut on Windows: a
real hazard for anyone with photosensitive epilepsy, so the rule is checked over the whole source, not by hand.
"""

import ast
import subprocess
import sys
from pathlib import Path

from pokeuds.util import NO_WINDOW

ROOT = Path(__file__).resolve().parents[1]
LAUNCHES = {"run", "Popen", "call", "check_call", "check_output"}
# macOS and Linux file-manager openers (gui/views/common.py, inside their platform branches): no console there.
OTHER_PLATFORMS = {"open", "xdg-open"}


def launches(tree):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LAUNCHES
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
            yield node


def hidden(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg is None and isinstance(kw.value, ast.Name) and kw.value.id == "NO_WINDOW":
            return True                                   # **NO_WINDOW
        if kw.arg == "creationflags":
            return True
    first = call.args[0] if call.args else None
    return (isinstance(first, ast.List) and first.elts and isinstance(first.elts[0], ast.Constant)
            and first.elts[0].value in OTHER_PLATFORMS)


def test_every_launch_opens_no_window():
    found, bare = 0, []
    for folder in ("pokeuds", "gui"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for call in launches(tree):
                found += 1
                if not hidden(call):
                    bare.append(f"{path.relative_to(ROOT)}:{call.lineno}")
    assert found >= 5                                     # the scan sees the launches it is meant to check
    assert bare == [], f"started with a window allowed (pass **NO_WINDOW): {bare}"


def test_no_window_on_windows_only():
    assert NO_WINDOW == ({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {})
