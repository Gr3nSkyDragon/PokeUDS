"""Plugin packages: reading, checking, signing, installing and removing them, and the sources that list them.

A ``.udsplugin`` is a zip with ``plugin.toml`` at its top or in one top folder (docs/plugins.md). Reading one
runs nothing: it checks the paths (no escaping, no links), the sizes, the manifest and the signature, and lists
what the Python files do for the install screen.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import io
import json
import os
import re
import shutil
import stat
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import plugins
from .settings import data_dir

EXTENSION = ".udsplugin"
MAX_FILES, MAX_BYTES, MAX_FILE = 2000, 50 << 20, 20 << 20
SIGNATURE = "plugin.sig"
SOURCE_FILE = "pokeuds-source.json"


@dataclass
class Package:
    id: str
    name: str
    version: str
    api: str
    author: str
    description: str
    homepage: str
    files: dict                      # relative path -> bytes (plugin.sig excluded)
    key: str = ""                    # the signer's public key, hex; "" when unsigned
    findings: list = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(len(v) for v in self.files.values())


# -- reading ----------------------------------------------------------------------------------------

def read(path: str | Path) -> Package:
    """A package from a .udsplugin (or any zip of a plugin folder), checked; ValueError says why not."""
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{path.name} is larger than {MAX_BYTES >> 20} MB")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError(f"{path.name} is not a plugin package (not a zip)") from None
    with archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            raise ValueError(f"{path.name} holds more than {MAX_FILES} files")
        names = [i.filename for i in infos]
        prefix = _prefix(names)
        files, total = {}, 0
        for info in infos:
            name = info.filename
            if (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
                raise ValueError(f"{path.name}: {name} is a link")
            rel = name[len(prefix):]
            parts = rel.replace("\\", "/").split("/")
            if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name) or ".." in parts or not rel:
                raise ValueError(f"{path.name}: the path {name!r} leaves the plugin's folder")
            if info.file_size > MAX_FILE:
                raise ValueError(f"{path.name}: {rel} is larger than {MAX_FILE >> 20} MB")
            total += info.file_size
            if total > MAX_BYTES:
                raise ValueError(f"{path.name} unpacks to more than {MAX_BYTES >> 20} MB")
            files["/".join(parts)] = archive.read(info)
    return _package(files, path.name)


def _prefix(names: list[str]) -> str:
    if "plugin.toml" in names:
        return ""
    tops = {n.split("/", 1)[0] for n in names}
    if len(tops) == 1 and f"{next(iter(tops))}/plugin.toml" in names:
        return next(iter(tops)) + "/"
    raise ValueError("no plugin.toml at the package's top or in its one top folder")


def _package(files: dict, where: str) -> Package:
    manifest = plugins.read_manifest(files["plugin.toml"], f"{where}: plugin.toml")
    module = str(manifest.get("module", "plugin"))
    if f"{module}.py" not in files:
        raise ValueError(f"{where}: no {module}.py (the module holding register(api))")
    sig = files.pop(SIGNATURE, None)
    package = Package(manifest["id"], manifest["name"], manifest["version"], str(manifest["api"]),
                      str(manifest.get("author", "")), str(manifest.get("description", "")),
                      str(manifest.get("homepage", "")), files)
    if sig is not None:
        package.key = verify(package, sig)
    package.findings = scan(files)
    return package


# -- what the code does -----------------------------------------------------------------------------

NETWORK = {"socket", "ssl", "http", "urllib", "requests", "httpx", "aiohttp", "websockets", "ftplib", "smtplib",
           "telnetlib", "xmlrpc", "asyncio"}
PROGRAMS = {"subprocess", "pty", "multiprocessing"}
NATIVE = {"ctypes", "cffi", "_ctypes"}


def scan(files: dict) -> list[str]:
    """What the Python files do, in the words the install screen shows; a hint, not a review."""
    found: dict[str, None] = {}
    for name, data in files.items():
        if name.endswith((".pyc", ".pyd", ".so", ".dll", ".dylib")):
            found["Ships compiled files it cannot show (they could do anything)"] = None
            continue
        if not name.endswith(".py"):
            continue
        text = data.decode("utf-8", "replace")
        if re.search(r"aes_keys|slot0x2[Dd]|bootrom|\.keys\b", text):
            found["Names your console keys (aes_keys.txt)"] = None
        try:
            tree = ast.parse(text, filename=name)
        except SyntaxError:
            found[f"Has Python PokeUDS cannot read ({name})"] = None
            continue
        for node in ast.walk(tree):
            tops = []
            if isinstance(node, ast.Import):
                tops = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                tops = [node.module.split(".")[0]]
            for top in tops:
                if top in NETWORK:
                    found["Uses the internet or the network"] = None
                if top in PROGRAMS:
                    found["Starts other programs"] = None
                if top in NATIVE:
                    found["Runs native code (ctypes, cffi)"] = None
                if top == "shutil":
                    found["May delete or move files (shutil)"] = None
            if isinstance(node, ast.Attribute):
                if node.attr in ("remove", "unlink", "rmdir", "rmtree", "truncate"):
                    found["May delete files"] = None
                if node.attr in ("system", "popen", "spawnv", "execv", "startfile"):
                    found["Starts other programs"] = None
            if isinstance(node, ast.Name) and node.id in ("exec", "eval", "compile", "__import__"):
                found["Runs code it builds while running (exec, eval, compile)"] = None
            if isinstance(node, ast.Name) and node.id == "marshal":
                found["Runs code it builds while running (marshal)"] = None
    return list(found)


# -- signing ----------------------------------------------------------------------------------------

def _manifest(package_id: str, version: str, files: dict) -> bytes:
    lines = [package_id, version] + [f"{hashlib.sha256(files[n]).hexdigest()}  {n}" for n in sorted(files)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def verify(package: Package, sig: bytes) -> str:
    """The signer's key (hex) when ``plugin.sig`` matches the package; ValueError when it does not."""
    from Crypto.Signature import eddsa

    try:
        body = json.loads(sig.decode("utf-8"))
        key = bytes.fromhex(body["key"])
        signature = base64.b64decode(body["signature"])
        public = eddsa.import_public_key(key)
        eddsa.new(public, "rfc8032").verify(_manifest(package.id, package.version, package.files), signature)
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"{package.id}: the signature does not match the files ({exc}); the package was changed "
                         "after it was signed, or the signature is broken") from None
    return key.hex()


def fingerprint(key_hex: str) -> str:
    """A short name for a signing key, as ABCD-EFGH-IJKL-MNOP."""
    digest = base64.b32encode(hashlib.sha256(bytes.fromhex(key_hex)).digest()).decode()[:16]
    return "-".join(digest[i:i + 4] for i in range(0, 16, 4))


def signing_key(path: str | Path, create: bool = False):
    from Crypto.PublicKey import ECC

    path = Path(path).expanduser()
    if not path.exists():
        if not create:
            raise ValueError(f"no signing key at {path}")
        key = ECC.generate(curve="ed25519")
        path.write_text(key.export_key(format="PEM"), encoding="utf-8")
        os.chmod(path, 0o600)
        return key
    return ECC.import_key(path.read_text(encoding="utf-8"))


def sign(key, package_id: str, version: str, files: dict) -> bytes:
    from Crypto.Signature import eddsa

    signature = eddsa.new(key, "rfc8032").sign(_manifest(package_id, version, files))
    public = key.public_key().export_key(format="raw").hex()
    return json.dumps({"key": public, "signature": base64.b64encode(signature).decode()}).encode()


def _keys_path() -> Path:
    return plugins.plugins_dir() / ".keys.json"


def known_key(plugin_id: str) -> str:
    try:
        return json.loads(_keys_path().read_text(encoding="utf-8")).get(plugin_id, "")
    except (OSError, ValueError, AttributeError):
        return ""


def _remember(plugin_id: str, key: str) -> None:
    try:
        keys = json.loads(_keys_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        keys = {}
    keys[plugin_id] = key
    plugins.plugins_dir().mkdir(parents=True, exist_ok=True)
    _keys_path().write_text(json.dumps(keys, indent=1, sort_keys=True), encoding="utf-8")


def trust(package: Package) -> str:
    """same (the key it was signed with before), new (signed, first seen), changed (another key: warn),
    unsigned-after-signed (warn), unsigned."""
    known = known_key(package.id)
    if package.key:
        return "same" if package.key == known else ("changed" if known else "new")
    return "unsigned-after-signed" if known else "unsigned"


TRUST_TEXT = {
    "same": "Signed by the same author as before",
    "new": "Signed; PokeUDS will remember this author's key",
    "changed": "Signed by a DIFFERENT key than before: it may not come from the same author",
    "unsigned-after-signed": "NOT signed, though earlier versions were: it may not come from the same author",
    "unsigned": "Not signed",
}


# -- installing -------------------------------------------------------------------------------------

def installed(plugin_id: str) -> plugins.Plugin | None:
    return next((p for p in plugins.discover() if p.id == plugin_id and not p.development), None)


def compare(package: Package, current: plugins.Plugin | None) -> str:
    """install, update, reinstall or downgrade."""
    if current is None:
        return "install"
    a, b = _version(package.version), _version(current.version)
    return "update" if a > b else ("reinstall" if a == b else "downgrade")


def _version(text: str) -> tuple:
    return tuple(int(x) if x.isdigit() else x for x in re.split(r"[.\-+]", text))


def install(package: Package, origin: str = "") -> Path:
    """Write the package into the plugins folder (an update replaces the folder, keeps the data), accept it,
    turn it on and remember its signing key. Call after the player accepted the install screen."""
    root = plugins.plugins_dir()
    root.mkdir(parents=True, exist_ok=True)
    target = root / package.id
    staging = root / f".{package.id}.new"
    if staging.exists():
        shutil.rmtree(staging)
    for name, data in package.files.items():
        out = staging / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    if package.key:
        _remember(package.id, package.key)
    value = plugins.state()
    value.setdefault("consent", {})[package.id] = package.version
    value["enabled"] = [i for i in value.get("enabled", []) if i != package.id] + [package.id]
    if origin:
        value.setdefault("origin", {})[package.id] = origin
    plugins.save_state(value)
    plugins.reload()
    return target


def accept(plugin: plugins.Plugin) -> None:
    """A plugin copied in by hand: the player accepted the install screen; turn it on."""
    plugins.consent(plugin.id, plugin.version)
    plugins.set_enabled(plugin.id, True)


def describe(plugin: plugins.Plugin) -> Package:
    """An installed plugin as a package, for its install screen."""
    files = {}
    for path in plugin.folder.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            files[path.relative_to(plugin.folder).as_posix()] = path.read_bytes()
    return _package(files, plugin.folder.name)


def remove(plugin_id: str, data: bool = False) -> None:
    target = plugins.plugins_dir() / plugin_id
    if target.exists():
        shutil.rmtree(target)
    if data:
        shutil.rmtree(data_dir() / "plugin-data" / plugin_id, ignore_errors=True)
    value = plugins.state()
    value["enabled"] = [i for i in value.get("enabled", []) if i != plugin_id]
    value.get("consent", {}).pop(plugin_id, None)
    plugins.save_state(value)
    plugins.reload()


def kept_data(plugin_id: str) -> int:
    folder = data_dir() / "plugin-data" / plugin_id
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else 0


def pack(folder: str | Path, out: str | Path | None = None, key=None) -> Path:
    """Zip a plugin folder into a .udsplugin (hidden files, __pycache__ and the manifest's ``exclude`` left out),
    sign it with ``key`` when given, and read it back as the app would."""
    folder = Path(folder)
    manifest = plugins.read_manifest((folder / "plugin.toml").read_bytes())
    excluded = [str(x) for x in manifest.get("exclude", [])]
    files = {}
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if not path.is_file() or any(p.startswith(".") or p == "__pycache__" for p in rel.parts):
            continue
        if any(rel.as_posix() == x or rel.as_posix().startswith(x.rstrip("/") + "/") for x in excluded):
            continue
        if rel.as_posix() == SIGNATURE:
            continue
        files[rel.as_posix()] = path.read_bytes()
    out = Path(out) if out else folder.parent / f"{manifest['id']}-{manifest['version']}{EXTENSION}"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        if key is not None:
            archive.writestr(SIGNATURE, sign(key, manifest["id"], manifest["version"], files))
    out.write_bytes(buffer.getvalue())
    read(out)
    return out


def size_text(size: int) -> str:
    return f"{size} bytes" if size < 1024 else f"{size / 1024:.0f} KB" if size < 1 << 20 else f"{size / (1 << 20):.1f} MB"


# -- sources ----------------------------------------------------------------------------------------

def _sources_path() -> Path:
    return plugins.plugins_dir() / ".sources.json"


def sources() -> list[str]:
    try:
        value = json.loads(_sources_path().read_text(encoding="utf-8"))
        return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def listing_url(text: str) -> str:
    """Where a followed source's listing is: OWNER/REPO, a GitHub page, a .json, or a folder address."""
    text = text.strip().rstrip("/")
    if match := re.fullmatch(r"(?:https://github\.com/)?([\w.-]+)/([\w.-]+)", text):
        return f"https://raw.githubusercontent.com/{match[1]}/{match[2]}/HEAD/{SOURCE_FILE}"
    if not _allowed(text):
        raise ValueError("a source is OWNER/REPO or an https:// address")
    return text if text.endswith(".json") else f"{text}/{SOURCE_FILE}"


def _allowed(url: str) -> bool:
    return url.startswith("https://") or bool(re.match(r"http://(127\.0\.0\.1|localhost)(:\d+)?/", url))


def follow(text: str) -> str:
    url = listing_url(text)
    value = sources()
    if text not in value:
        value.append(text.strip())
        plugins.plugins_dir().mkdir(parents=True, exist_ok=True)
        _sources_path().write_text(json.dumps(value, indent=1), encoding="utf-8")
    return url


def unfollow(text: str) -> None:
    value = [s for s in sources() if s != text]
    plugins.plugins_dir().mkdir(parents=True, exist_ok=True)
    _sources_path().write_text(json.dumps(value, indent=1), encoding="utf-8")


def _get(url: str, limit: int) -> bytes:
    if not _allowed(url):
        raise ValueError(f"{url}: only https:// (or http://localhost) addresses")
    request = urllib.request.Request(url, headers={"User-Agent": "PokeUDS"})
    with urllib.request.urlopen(request, timeout=30) as reply:     # noqa: S310 - checked above
        data = reply.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{url} is larger than expected")
    return data


@dataclass
class Listed:
    id: str
    name: str
    version: str
    api: str
    description: str
    author: str
    download: str
    sha256: str
    size: int
    homepage: str
    source: str


def parse_listing(raw: bytes, source: str) -> tuple[str, list[Listed]]:
    """(the source's name, its plugins); an entry lacking id, version, an https download or sha256, or
    repeating an id, is left out."""
    body = json.loads(raw.decode("utf-8"))
    if not isinstance(body, dict) or body.get("schema") != 1:
        raise ValueError(f"{source}: not a PokeUDS plugin listing (schema 1)")
    seen, out = set(), []
    for entry in body.get("plugins", []):
        if not isinstance(entry, dict):
            continue
        pid, version, url, digest = (str(entry.get(k, "")) for k in ("id", "version", "download", "sha256"))
        if not plugins.ID_RE.match(pid) or not version or not _allowed(url) or not re.fullmatch(r"[0-9a-f]{64}", digest) \
                or pid in seen:
            continue
        seen.add(pid)
        out.append(Listed(pid, str(entry.get("name") or pid), version, str(entry.get("api", "1.0")),
                          str(entry.get("description", "")), str(entry.get("author", "")), url, digest,
                          int(entry.get("size", 0) or 0), str(entry.get("homepage", "")), source))
    return str(body.get("name") or source), out


def browse() -> list[tuple[str, str, list[Listed] | str]]:
    """Each followed source: (what was typed, its name, its plugins or why it could not be read)."""
    out = []
    for text in sources():
        try:
            name, listed = parse_listing(_get(listing_url(text), 2 << 20), text)
            out.append((text, name, listed))
        except Exception as exc:                  # a source that is down or wrong is shown, not fatal
            out.append((text, text, f"{type(exc).__name__}: {exc}"))
    return out


def download(entry: Listed, folder: Path | None = None) -> tuple[Path, Package]:
    """Fetch a listed plugin, check its SHA-256 before reading it, and read it; its id must be the entry's."""
    data = _get(entry.download, MAX_BYTES)
    if hashlib.sha256(data).hexdigest() != entry.sha256:
        raise ValueError(f"{entry.id}: the download does not match the source's SHA-256; nothing was read")
    folder = folder or (plugins.plugins_dir() / ".downloads")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{entry.id}-{entry.version}{EXTENSION}"
    path.write_bytes(data)
    package = read(path)
    if package.id != entry.id:
        raise ValueError(f"the source lists {entry.id} but the package is {package.id}")
    return path, package


def listing_entry(path: str | Path, download: str) -> dict:
    """A source listing's entry for a package file (scripts/plugin_source.py)."""
    package = read(path)
    data = Path(path).read_bytes()
    return {"id": package.id, "name": package.name, "version": package.version, "api": package.api,
            "description": package.description, "author": package.author, "homepage": package.homepage,
            "download": download.format(id=package.id, version=package.version, file=Path(path).name),
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
