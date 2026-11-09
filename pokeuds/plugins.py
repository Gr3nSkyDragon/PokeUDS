"""Plugins: Python that adds tools, games, radios and offer edits to PokeUDS (docs/plugins.md).

A plugin is a folder with a ``plugin.toml`` and a module whose ``register(api)`` says what it adds, shipped as
one ``.udsplugin`` file (pokeuds.app.plugin_store). Installed plugins live in ``<data>/plugins/<id>``; their
lasting files in ``<data>/plugin-data/<id>``. ``POKEUDS_PLUGINS`` (folders, separated as PATH is) turns
plugins on for one process and the runs it starts, for development.

A plugin runs as Python inside PokeUDS with full access to files, keys, the network and the radio; the
guardrails (pokeuds.plugin_guard) stop obvious harm and are not a sandbox.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import runpy
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import plugin_guard
from .app.settings import data_dir

API_MAJOR, API_LEVEL = 1, 1                  # 1.1: language packs
VERSION = f"{API_MAJOR}.{API_LEVEL}"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MANIFEST_BYTES = 64 * 1024
BUILT_IN_GAMES = ("xy", "oras", "sm", "usum", "rby", "gsc")
FIELD_KINDS = ("text", "number", "choice", "switch", "file")
TOKENS = ("{ot}", "{tid}", "{sid}", "{radio}", "{keys}", "{output}", "{game}", "{stamp}")


# -- what a plugin adds ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Field:
    """One input on a tool's page, passed to its script as a command-line flag."""

    flag: str                        # "--name"
    label: str
    kind: str = "text"               # text, number, choice, switch, file
    default: str | int | bool = ""
    choices: tuple = ()              # ((value, label), ...) for a choice
    exts: tuple = ()                 # ("pk6",) for a file
    help: str = ""

    def __post_init__(self) -> None:
        if self.kind not in FIELD_KINDS:
            raise ValueError(f"field {self.flag}: kind {self.kind!r} is not one of {', '.join(FIELD_KINDS)}")
        if not self.flag.startswith("--"):
            raise ValueError(f"field {self.flag!r}: a flag starts with --")


@dataclass(frozen=True)
class Tool:
    """Something to run: the Trade page lists it under its game and runs ``script`` as a child process."""

    key: str                         # unique among every tool
    name: str
    script: str                      # a file in the plugin's folder
    summary: str
    steps: tuple = ()                # what the player does, a line each
    fields: tuple = ()               # Fields
    fixed: tuple = ()                # always passed; may carry the TOKENS

    def __post_init__(self) -> None:
        if not KEY_RE.match(self.key):
            raise ValueError(f"tool key {self.key!r}: lowercase letters, digits, - and _")


@dataclass(frozen=True)
class Game:
    """A game section of the plugin's own on the Trade page."""

    key: str
    name: str
    group: str = "Plugins"


@dataclass
class Plugin:
    id: str
    name: str
    version: str
    folder: Path
    author: str = ""
    description: str = ""
    homepage: str = ""
    api: str = "1.0"
    module: str = "plugin"
    icon: str = ""
    locale: str = ""                 # a language pack's language; it has no code
    development: bool = False        # from POKEUDS_PLUGINS: on, and trusted, for this process
    enabled: bool = False
    consented: bool = False
    error: str = ""
    tools: list = field(default_factory=list)          # (game, Tool)
    games: list = field(default_factory=list)          # Game
    radios: list = field(default_factory=list)         # (scheme, label)
    offer_edits: list = field(default_factory=list)    # (game, label)

    @property
    def data(self) -> Path:
        return data_folder(self.id)

    def adds(self) -> list[str]:
        if self.locale:
            return [f"the language {self.name} ({self.locale}) to Settings, App language"]
        out = [f"{t.name} ({g})" for g, t in self.tools] + [f"the game {g.name}" for g in self.games]
        out += [f"the radio {label or scheme}" for scheme, label in self.radios]
        out += [f"an offer edit for {g}" + (f" ({label})" if label else "") for g, label in self.offer_edits]
        return out


class Api:
    """What ``register(api)`` receives (API 1.0 and up): add_tool, add_game, add_radio, add_offer_edit, folder, data."""

    version, level = VERSION, API_LEVEL

    def __init__(self, plugin: Plugin, registry: "Registry") -> None:
        self._plugin, self._registry = plugin, registry
        self.folder: Path | None = plugin.folder
        self.data = data_folder(plugin.id)
        self._tools: list = []
        self._games: list = []
        self._radios: list = []
        self._edits: list = []

    def add_tool(self, game: str, tool: Tool) -> None:
        if not isinstance(tool, Tool):
            raise TypeError("add_tool takes a pokeuds.plugins.Tool")
        known = set(BUILT_IN_GAMES) | set(self._registry.games) | {g.key for g in self._games}
        if game not in known:
            raise ValueError(f"no game {game!r}: one of {', '.join(sorted(known))}, or one added with add_game")
        if tool.key in self._registry.tools or any(t.key == tool.key for _, t in self._tools):
            raise ValueError(f"the tool key {tool.key!r} is taken")
        if not (self._plugin.folder / tool.script).is_file():
            raise ValueError(f"tool {tool.key}: no script {tool.script} in the plugin's folder")
        for token in re.findall(r"\{\w+\}", " ".join(map(str, tool.fixed))):
            if token not in TOKENS:
                raise ValueError(f"tool {tool.key}: unknown token {token} (one of {', '.join(TOKENS)})")
        self._tools.append((game, tool))

    def add_game(self, game: Game) -> None:
        if not KEY_RE.match(game.key) or game.key in BUILT_IN_GAMES or game.key in self._registry.games:
            raise ValueError(f"the game key {game.key!r} is taken or not valid")
        self._games.append(game)

    def add_radio(self, scheme: str, factory: Callable, label: str = "") -> None:
        """``factory(argument)`` returns a pokeuds.radio.base.Radio; runs then take ``--radio scheme:argument``."""
        if not KEY_RE.match(scheme) or scheme in ("esp32", "ldnd", "demo") or scheme in self._registry.radios:
            raise ValueError(f"the radio {scheme!r} is taken or not valid")
        self._radios.append((scheme, factory, label))

    def add_offer_edit(self, game: str, edit: Callable[[bytes], bytes], label: str = "") -> None:
        """``edit(record) -> record`` on every Pokemon a trade of ``game`` offers, before PKHeX's check."""
        if game not in BUILT_IN_GAMES:
            raise ValueError(f"offer edits are for {', '.join(BUILT_IN_GAMES)}, not {game!r}")
        self._edits.append((game, edit, label))


@dataclass
class Registry:
    plugins: list = field(default_factory=list)
    tools: dict = field(default_factory=dict)           # key -> (plugin, game, Tool)
    games: dict = field(default_factory=dict)           # key -> (plugin, Game)
    radios: dict = field(default_factory=dict)          # scheme -> (plugin, factory, label)
    offer_edits: dict = field(default_factory=dict)     # game -> [(plugin, edit, label)]

    def tools_for(self, game: str) -> list:
        return [(p, t) for p, g, t in self.tools.values() if g == game]


# -- folders and state ----------------------------------------------------------------------------

def plugins_dir() -> Path:
    return data_dir() / "plugins"


def data_folder(plugin_id: str) -> Path:
    """A plugin's lasting files (kept across updates; deleted with the plugin only when asked)."""
    path = data_dir() / "plugin-data" / plugin_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path() -> Path:
    return plugins_dir() / ".state.json"


def state() -> dict:
    """{"enabled": [ids], "consent": {id: version}}: which plugins are on, and which install the player accepted."""
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(value: dict) -> None:
    plugins_dir().mkdir(parents=True, exist_ok=True)
    tmp = _state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(_state_path())


def set_enabled(plugin_id: str, on: bool) -> None:
    value = state()
    enabled = [i for i in value.get("enabled", []) if i != plugin_id] + ([plugin_id] if on else [])
    value["enabled"] = enabled
    save_state(value)
    reload()


def consent(plugin_id: str, version: str) -> None:
    value = state()
    value.setdefault("consent", {})[plugin_id] = version
    save_state(value)


# -- manifests --------------------------------------------------------------------------------------

def parse_api(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)(?:\.(\d+))?", str(text).strip())
    if not match:
        raise ValueError(f"api {text!r} is not a version like 1.0")
    return int(match[1]), int(match[2] or 0)


def read_manifest(raw: bytes, where: str = "plugin.toml") -> dict:
    """The manifest's fields, or ValueError saying what is wrong."""
    if len(raw) > MANIFEST_BYTES:
        raise ValueError(f"{where} is larger than {MANIFEST_BYTES // 1024} KB")
    try:
        manifest = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{where}: {exc}") from None
    plugin_id = manifest.get("id")
    if not isinstance(plugin_id, str) or not ID_RE.match(plugin_id):
        raise ValueError(f"{where}: id must be lowercase letters, digits, dots, dashes or underscores")
    for key in ("name", "version"):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            raise ValueError(f"{where}: {key} is required")
    if "api" not in manifest:
        raise ValueError(f"{where}: api (the lowest PokeUDS plugin API it needs, like \"1.0\") is required")
    major, level = parse_api(manifest["api"])
    if major != API_MAJOR:
        raise ValueError(f"{plugin_id} needs plugin API {major}.x; this PokeUDS has {VERSION}")
    if level > API_LEVEL:
        raise ValueError(f"{plugin_id} needs plugin API {major}.{level}; update PokeUDS (it has {VERSION})")
    module = str(manifest.get("module", "plugin"))
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", module):
        raise ValueError(f"{where}: module {module!r} is not a module name")
    if "locale" in manifest:                     # a language pack (1.1): data only
        locale = manifest["locale"]
        if not isinstance(locale, str) or not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", locale):
            raise ValueError(f"{where}: locale {locale!r} is not a language tag like es or zh-Hans")
        strings = str(manifest.get("strings", "strings.toml"))
        if ".." in Path(strings).parts or Path(strings).is_absolute():
            raise ValueError(f"{where}: strings must stay inside the pack")
        if len(str(manifest.get("font_family", ""))) > 100:
            raise ValueError(f"{where}: font_family is at most 100 characters")
        if parse_api(manifest["api"]) < (1, 1):
            raise ValueError(f"{where}: a language pack needs api = \"1.1\"")
    return manifest


def _plugin_from(folder: Path, development: bool = False) -> Plugin:
    manifest = read_manifest((folder / "plugin.toml").read_bytes(), str(folder / "plugin.toml"))
    return Plugin(manifest["id"], manifest["name"], manifest["version"], folder, str(manifest.get("author", "")),
                  str(manifest.get("description", "")), str(manifest.get("homepage", "")), str(manifest["api"]),
                  str(manifest.get("module", "plugin")), str(manifest.get("icon", "")), str(manifest.get("locale", "")),
                  development)


def discover() -> list[Plugin]:
    """Installed plugins and POKEUDS_PLUGINS folders, with their on/off and consent state; nothing is run."""
    found: dict[str, Plugin] = {}
    st = state()
    folders: list[tuple[Path, bool]] = []
    root = plugins_dir()
    if root.is_dir():
        folders += [(p, False) for p in sorted(root.iterdir()) if p.is_dir() and not p.name.startswith(".")]
    folders += [(Path(p), True) for p in os.environ.get("POKEUDS_PLUGINS", "").split(os.pathsep) if p]
    for folder, development in folders:
        try:
            plugin = _plugin_from(folder, development)
        except (OSError, ValueError) as exc:
            plugin = Plugin(folder.name, folder.name, "?", folder, development=development, error=str(exc))
        if plugin.id in found and not development:
            continue
        if not development and plugin.folder.name != plugin.id and not plugin.error:
            plugin.error = f"the folder is {plugin.folder.name} but the plugin is {plugin.id}"
        plugin.enabled = development or plugin.id in st.get("enabled", [])
        plugin.consented = development or st.get("consent", {}).get(plugin.id) == plugin.version
        found[plugin.id] = plugin
    return list(found.values())


# -- loading ----------------------------------------------------------------------------------------

_registry: Registry | None = None


def _guard_folders(plugins: list[Plugin]) -> tuple[list, list]:
    import tempfile

    from .app.bank import default_folder
    from .app.settings import Settings

    code = [p.folder for p in plugins]
    settings = Settings.load()
    free = code + [data_dir() / "plugin-data", Path(settings.output).expanduser(), Path(tempfile.gettempdir()),
                   Path(settings.bank).expanduser() if settings.bank else default_folder(), Path.cwd() / "received"]
    return code, free


def load() -> Registry:
    """Run every plugin that is on and accepted; one that fails adds nothing and keeps its error."""
    registry = Registry()
    plugins = discover()
    runnable = [p for p in plugins if p.enabled and p.consented and not p.error]
    code, free = _guard_folders(runnable)
    if runnable:
        plugin_guard.install(code, free)
    for plugin in plugins:
        registry.plugins.append(plugin)
        if plugin not in runnable:
            if plugin.enabled and not plugin.consented and not plugin.error:
                plugin.error = "not accepted yet: turn it on from the Plugins page to see what it does"
            continue
        if plugin.locale:                         # a language pack runs nothing; Settings, App language uses it
            continue
        api = Api(plugin, registry)
        try:
            module = _import(plugin)
            register = getattr(module, "register", None)
            if not callable(register):
                raise ValueError(f"{plugin.module}.py has no register(api)")
            register(api)
        except Exception as exc:                  # a plugin's own error: it adds nothing
            plugin.error = f"{type(exc).__name__}: {exc}"
            continue
        for game in api._games:
            registry.games[game.key] = (plugin, game)
            plugin.games.append(game)
        for game, tool in api._tools:
            registry.tools[tool.key] = (plugin, game, tool)
            plugin.tools.append((game, tool))
        for scheme, factory, label in api._radios:
            registry.radios[scheme] = (plugin, factory, label)
            plugin.radios.append((scheme, label))
        for game, edit, label in api._edits:
            registry.offer_edits.setdefault(game, []).append((plugin, edit, label))
            plugin.offer_edits.append((game, label))
    return registry


def _import(plugin: Plugin):
    path = plugin.folder / f"{plugin.module}.py"
    if not path.is_file():
        raise ValueError(f"no {plugin.module}.py in the plugin")
    if str(plugin.folder) not in sys.path:
        sys.path.insert(0, str(plugin.folder))
    name = f"pokeuds_plugin_{re.sub(r'[^A-Za-z0-9_]', '_', plugin.id)}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = load()
    return _registry


def reload() -> Registry:
    global _registry
    _registry = None
    return registry()


# -- using what plugins add -------------------------------------------------------------------------

def tool_command(key: str, values: dict, tokens: dict) -> list[str]:
    """The arguments for ``python -m pokeuds plugin run KEY -- ...``: the fields' values, then ``fixed``."""
    plugin, game, tool = registry().tools[key]
    args: list[str] = []
    for f in tool.fields:
        value = values.get(f.flag, f.default)
        if f.kind == "switch":
            if value:
                args.append(f.flag)
        elif value not in ("", None):
            args += [f.flag, str(value)]
    for part in tool.fixed:
        text = str(part)
        for token in TOKENS:
            text = text.replace(token, str(tokens.get(token.strip("{}"), "")))
        args.append(text)
    return ["plugin", "run", key, "--", *args]


def run_tool(key: str, argv: list[str]) -> int:
    """Run a tool's script as ``__main__`` with ``argv`` (the child process side of a plugin run)."""
    reg = registry()
    if key not in reg.tools:
        known = ", ".join(sorted(reg.tools)) or "none"
        raise SystemExit(f"no plugin tool {key!r} (on and loaded: {known})")
    plugin, game, tool = reg.tools[key]
    script = plugin.folder / tool.script
    sys.argv = [str(script), *argv]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    return 0


def open_radio(scheme: str, argument: str):
    reg = registry()
    if scheme not in reg.radios:
        return None
    plugin, factory, label = reg.radios[scheme]
    return factory(argument)


def edit_offer(game: str, record: bytes, check_game: str, extension: str, say: Callable[[str], None] = print) -> bytes:
    """A trade's offer after every plugin's offer edit for ``game``; when one changed it, PKHeX's legality check
    runs last and no plugin turns it off: an edit PKHeX refuses stops the trade with PKHeX's reason."""
    edited_record, edited = apply_offer_edits(game, record, say)
    if not edited:
        return record
    from . import pkhex

    if pkhex.helper_path() is None:
        raise ValueError(f"{', '.join(edited)} edited the offer, and an edited offer must pass PKHeX's check, and PKHeX "
                         f"is missing ({pkhex.BUILD_COMMAND}), so turn the plugin off or build PKHeX")
    result = pkhex.shared().check(check_game, edited_record)
    if not result.legal:
        raise ValueError(f"PKHeX refused the offer {', '.join(edited)} edited: " + "; ".join(
            line for line in result.report.splitlines() if line.startswith("Invalid"))[:400])
    say(f"PKHeX checked the edited offer: legal ({extension})")
    return edited_record


def apply_offer_edits(game: str, record: bytes, say: Callable[[str], None] = print) -> tuple[bytes, list[str]]:
    """Every plugin's offer edit for ``game``, in load order. -> (record, the labels that edited it)."""
    edited = []
    for plugin, edit, label in registry().offer_edits.get(game, []):
        name = label or plugin.name
        try:
            out = edit(bytes(record))
        except Exception as exc:
            raise ValueError(f"the plugin {name}'s offer edit failed: {type(exc).__name__}: {exc}") from None
        if not isinstance(out, (bytes, bytearray)) or not out:
            raise ValueError(f"the plugin {name}'s offer edit returned no record")
        if bytes(out) != bytes(record):
            say(f"the plugin {name} edited the offer")
            edited.append(name)
        record = bytes(out)
    return record, edited
