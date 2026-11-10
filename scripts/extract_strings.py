"""Write the language-pack template: every string the app shows (docs/plugins.md, Language packs).

    python scripts/extract_strings.py            # writes pokeuds/app/data/strings.en.toml
    python scripts/extract_strings.py --check    # exits 1 when the file is not up to date (the tests run it)
    python scripts/extract_strings.py --pseudo examples/plugins/pseudo/strings.toml

Strings come from the GUI's text calls (t.label, ft.Text, buttons...), its text keywords (label=, tooltip=...),
its (key, label) tables, and the text of the app modules the pages show (pokeuds/app: games, steps, progress).
An f-string becomes a template: ``f"Built {entry.describe()} and kept it"`` is ``"Built {value0} and kept it"``.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI_FILES = sorted((ROOT / "gui").glob("*.py")) + sorted((ROOT / "gui" / "views").glob("*.py"))
APP_FILES = [ROOT / "pokeuds" / "app" / n for n in ("catalog.py", "progress.py", "bank.py", "builder.py", "giftfile.py",
                                                    "plugin_store.py", "settings.py", "gts.py")] + [ROOT / "pokeuds" / "pkhex.py"]
OUT = ROOT / "pokeuds" / "app" / "data" / "strings.en.toml"
CALLS = {"label", "title", "heading", "notice", "primary", "secondary", "pill", "Text", "TextButton", "FilledButton",
         "OutlinedButton", "ElevatedButton", "_say", "Checkbox", "Switch", "_dd", "_num", "_text", "name_field"}
KEYWORDS = {"label", "hint_text", "helper", "tooltip", "text", "empty", "error", "dialog_title"}
ATTRIBUTES = {"message", "headline", "detail", "note"}


def template(node) -> str | None:
    """The text an expression makes, its non-literal parts as {value0}, {value1}...; None when it makes none."""
    count = [0]

    def walk(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return n.value.replace("{", "{{").replace("}", "}}")
        if isinstance(n, ast.JoinedStr):
            out = ""
            for part in n.values:
                if isinstance(part, ast.Constant):
                    out += str(part.value).replace("{", "{{").replace("}", "}}")
                else:
                    out += f"{{value{count[0]}}}"
                    count[0] += 1
            return out
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
            left, right = walk(n.left), walk(n.right)
            if left is None and right is None:
                return None
            return (left if left is not None else hole()) + (right if right is not None else hole())
        return None

    def hole():
        count[0] += 1
        return f"{{value{count[0] - 1}}}"

    text = walk(node)
    if text is None:
        return None
    return text.replace("{{", "{").replace("}}", "}") if "{value" not in text else _unescape(text)


def _unescape(text: str) -> str:
    # Literal braces are rare in UI text; keep {valueN} fields and turn {{ }} back into single braces.
    return re.sub(r"\{\{|\}\}", lambda m: m.group(0)[0], text)


def wanted(text: str, shown: bool = False) -> bool:
    """Text to translate. ``shown``: an argument the app displays, where a lone lowercase word ("alpha") is text."""
    plain = re.sub(r"\{value\d+\}", "", text).strip()
    if sum(c.isalpha() for c in plain) < 2:
        return False
    if plain.startswith(("--", "#", "http", "%", "/")) or "\\" in plain:
        return False                       # flags, colours, addresses, paths
    if not shown and re.fullmatch(r"[a-z0-9_.\-:/]+", plain):
        return False                       # keys
    if re.fullmatch(r"\.[a-z0-9]{2,8}", plain) or (not shown and re.fullmatch(r"[a-z0-9]{2,6}", plain)):
        return False                       # file extensions
    return True


def expressions(node):
    """The text-making expressions inside an argument: both arms of an if, each of a list's items."""
    if isinstance(node, ast.IfExp):
        yield from expressions(node.body)
        yield from expressions(node.orelse)
    elif isinstance(node, ast.BoolOp):
        for v in node.values:
            yield from expressions(v)
    elif isinstance(node, (ast.List, ast.Tuple)):
        for v in node.elts:
            yield from expressions(v)
    else:
        yield node


def from_gui(tree) -> set[str]:
    found = set()

    def add(node, shown=True):
        for expr in expressions(node):
            if (text := template(expr)) and wanted(text, shown) and not re.fullmatch(r"[a-z0-9_]+", text.split("{")[0] or "x"):
                found.add(text)
            elif text and wanted(text, shown) and shown:
                found.add(text)

    def call_names(func):
        if isinstance(func, ast.IfExp):                    # (t.primary if on else t.secondary)("Build one", ...)
            return call_names(func.body) | call_names(func.orelse)
        return {func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")}

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.match(r"[A-Z][a-z]", node.value):
            add(node, shown=False)                          # sentence-like constants: "No Pokemon chosen", "Idle"
        if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant) \
                and re.match(r"[A-Z][a-z]", str(node.values[0].value)):
            add(node, shown=False)
        if isinstance(node, ast.Call):
            if call_names(node.func) & CALLS and node.args:
                add(node.args[0])
            for kw in node.keywords:
                if kw.arg in KEYWORDS:
                    add(kw.value)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr in ATTRIBUTES and node.value is not None:
                    add(node.value)
                if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    for t, v in zip(target.elts, node.value.elts):            # key, label, icon = ...
                        if isinstance(t, ast.Name) and t.id == "label":
                            add(v)
        if isinstance(node, ast.Tuple) and len(node.elts) == 2 and isinstance(node.elts[1], (ast.Constant, ast.JoinedStr)):
            first = node.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, (str, int)):
                add(node.elts[1])                                           # (key, label) tables
    return found


def from_app(tree) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue                                                        # docstrings
        if isinstance(node, (ast.Constant, ast.JoinedStr)) and not isinstance(getattr(node, "parent", None), ast.JoinedStr):
            text = template(node)
            if text and wanted(text) and (" " in text or text[:1].isupper()) and "\n" not in text:
                found.add(text)
    return found


def _docstrings(tree) -> set[str]:
    """Module, class and function docstrings: for whoever reads the code, never shown."""
    return {n.body[0].value.value for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)
            and isinstance(n.body[0].value.value, str)}


def collect() -> dict[str, str]:
    strings: set[str] = set()
    for path in GUI_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        strings |= from_gui(tree) - _docstrings(tree)
    for path in APP_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for parent in ast.walk(tree):                                       # f-string parts are not strings of their own
            for child in ast.iter_child_nodes(parent):
                child.parent = parent
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
                      and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        strings |= {s for s in from_app(tree)} - set()
        strings -= {n.value for n in ast.walk(tree) if id(n) in docstrings and isinstance(n, ast.Constant)}
    return {s: s for s in sorted(strings)}


def toml(strings: dict[str, str], header: str) -> str:
    lines = [header] + [f"{json.dumps(k, ensure_ascii=False)} = {json.dumps(v, ensure_ascii=False)}"
                        for k, v in strings.items()]
    return "\n".join(lines) + "\n"


HEADER = ("# Every string the PokeUDS app shows, English = English: the template for a language pack's strings.toml\n"
          "# (docs/plugins.md, Language packs). Written by scripts/extract_strings.py; do not edit by hand.")

PSEUDO = str.maketrans("aeiouAEIOUcnsyCNSY", "àéîõüÀÉÎÕÜçñšýÇÑŠÝ")


def pseudo(text: str) -> str:
    """A pseudo-translation: accented, bracketed, fields untouched; shows what a pack would cover."""
    parts = re.split(r"(\{value\d+\})", text)
    return "[!" + "".join(p if p.startswith("{value") else p.translate(PSEUDO) for p in parts) + "!]"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--pseudo", help="also write a pseudo-locale strings.toml here")
    args = parser.parse_args()
    strings = collect()
    text = toml(strings, HEADER)
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT.relative_to(ROOT)} is out of date: run python scripts/extract_strings.py")
            return 1
        print(f"{len(strings)} strings, up to date")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {len(strings)} strings to {OUT.relative_to(ROOT)}")
    if args.pseudo:
        Path(args.pseudo).write_text(toml({k: pseudo(k) for k in strings}, "# A pseudo-locale: every string accented and "
                                          "bracketed (scripts/extract_strings.py --pseudo)."), encoding="utf-8")
        print(f"wrote the pseudo pack's strings to {args.pseudo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
