"""Plugins: manifests, loading, tools, radios, offer edits, packages and signatures, the guardrails, sources."""

import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from pokeuds import plugin_guard, plugins
from pokeuds.app import plugin_store as store

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "plugins"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("POKEUDS_PLUGINS", raising=False)
    plugins._registry = None
    yield
    plugins._registry = None
    plugin_guard.clear()


def make_plugin(folder: Path, plugin_id="demo", register="", api="1.0", files=None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.toml").write_text(f'id = "{plugin_id}"\nname = "Demo"\nversion = "1.0.0"\napi = "{api}"\n')
    (folder / "plugin.py").write_text("from pokeuds.plugins import Field, Game, Tool\n\ndef register(api):\n"
                                      + (register or "    pass\n"))
    for name, text in (files or {}).items():
        (folder / name).write_text(text)
    return folder


def test_manifests_are_checked():
    good = b'id = "my-plugin"\nname = "Mine"\nversion = "1.0.0"\napi = "1.0"\n'
    assert plugins.read_manifest(good)["id"] == "my-plugin"
    for bad, why in ((good.replace(b'"my-plugin"', b'"My Plugin"'), "id must be"),
                     (good.replace(b'"1.0"', b'"1.9"'), "update PokeUDS"),
                     (good.replace(b'"1.0"', b'"2.0"'), "needs plugin API 2"),
                     (good.replace(b'api = "1.0"\n', b""), "api"),
                     (b"id = [", "plugin.toml")):
        with pytest.raises(ValueError, match=why):
            plugins.read_manifest(bad)


def test_the_example_tool_loads_and_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("POKEUDS_PLUGINS", str(EXAMPLES / "hello"))
    reg = plugins.reload()
    assert "hello-greet" in reg.tools and [t.key for _, t in reg.tools_for("rby")] == ["hello-greet"]
    args = plugins.tool_command("hello-greet", {"--times": 2, "--shout": True}, {"ot": "DANA"})
    assert args == ["plugin", "run", "hello-greet", "--", "--greeting", "Hello", "--times", "2", "--shout",
                    "--trainer", "DANA"]
    env = {**os.environ, "POKEUDS_PLUGINS": str(EXAMPLES / "hello"), "POKEUDS_DATA": str(tmp_path / "data")}
    out = subprocess.run([sys.executable, "-m", "pokeuds", *args], cwd=ROOT, env=env, capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.splitlines()[:2] == ["HELLO, DANA!", "HELLO, DANA!"], out.stderr


def test_a_failing_plugin_adds_nothing(monkeypatch, tmp_path):
    bad = make_plugin(tmp_path / "bad", "bad", "    api.add_tool('rby', Tool('x-tool', 'X', 'x.py', 'nothing'))\n")
    taken = make_plugin(tmp_path / "taken", "taken", "    api.add_tool('nowhere', Tool('y', 'Y', 'plugin.py', 'y'))\n")
    monkeypatch.setenv("POKEUDS_PLUGINS", os.pathsep.join([str(bad), str(taken)]))
    reg = plugins.reload()
    errors = {p.id: p.error for p in reg.plugins}
    assert "no script x.py" in errors["bad"] and "no game 'nowhere'" in errors["taken"] and not reg.tools


def test_games_radios_and_offer_edits(monkeypatch, tmp_path):
    folder = make_plugin(tmp_path / "kit", "kit", (
        "    api.add_game(Game('kit-game', 'Kit game'))\n"
        "    api.add_tool('kit-game', Tool('kit-tool', 'Kit tool', 'plugin.py', 'does nothing'))\n"
        "    api.add_radio('kitradio', lambda arg: ('a radio', arg), 'Kit radio')\n"
        "    api.add_offer_edit('xy', lambda record: record[:-1] + b'\\x01', 'Kit edit')\n"))
    monkeypatch.setenv("POKEUDS_PLUGINS", str(folder))
    reg = plugins.reload()
    assert "kit-game" in reg.games and [t.key for _, t in reg.tools_for("kit-game")] == ["kit-tool"]
    from pokeuds.radio import open_radio

    assert open_radio("kitradio:here") == ("a radio", "here")
    record, edited = plugins.apply_offer_edits("xy", b"\x00\x00", say=lambda _: None)
    assert record == b"\x00\x01" and edited == ["Kit edit"]
    assert plugins.apply_offer_edits("sm", b"\x00", say=lambda _: None) == (b"\x00", [])


@pytest.mark.skipif(not __import__("pokeuds.pkhex", fromlist=["x"]).helper_path(), reason="the PKHeX helper is not built")
def test_an_offer_edit_passes_pkhex_last(monkeypatch, tmp_path):
    from pokeuds import gbpk, pkhex

    monkeypatch.setenv("POKEUDS_PLUGINS", str(EXAMPLES / "gb-renamer"))
    plugins.reload()
    built = pkhex.shared().build("rby", 4, level=12)
    out = plugins.edit_offer("rby", built.data, "rby", "pk1", say=lambda _: None)
    assert gbpk.decode_name(gbpk.Record.from_file(out).nickname) == "UDS"
    broken = make_plugin(tmp_path / "breaker", "breaker", "    api.add_offer_edit('rby', lambda r: r[:3] + b'\\xff' + r[4:])\n")
    monkeypatch.setenv("POKEUDS_PLUGINS", str(broken))
    plugins.reload()
    with pytest.raises(ValueError, match="PKHeX refused"):
        plugins.edit_offer("rby", built.data, "rby", "pk1", say=lambda _: None)


def test_packages_signatures_and_trust(tmp_path):
    key = store.signing_key(tmp_path / "key.pem", create=True)
    signed = store.pack(EXAMPLES / "hello", tmp_path / "hello.udsplugin", key)
    package = store.read(signed)
    assert package.id == "hello" and package.key and store.trust(package) == "new"
    assert "plugin.toml" in package.files and "plugin.sig" not in package.files
    store.install(package)
    assert store.trust(store.read(signed)) == "same"
    other = store.pack(EXAMPLES / "hello", tmp_path / "other.udsplugin", store.signing_key(tmp_path / "k2.pem", create=True))
    assert store.trust(store.read(other)) == "changed"
    unsigned = store.pack(EXAMPLES / "hello", tmp_path / "plain.udsplugin")
    assert store.trust(store.read(unsigned)) == "unsigned-after-signed"
    # a file changed after signing
    tampered = tmp_path / "tampered.udsplugin"
    with zipfile.ZipFile(signed) as src, zipfile.ZipFile(tampered, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            dst.writestr(info, data + b"\n# changed" if info.filename == "greet.py" else data)
    with pytest.raises(ValueError, match="signature does not match"):
        store.read(tampered)
    assert len(store.fingerprint(package.key)) == 19


def test_packages_cannot_escape(tmp_path):
    for name, why in (("../evil.py", "leaves"), ("/abs.py", "leaves")):
        path = tmp_path / "bad.udsplugin"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("plugin.toml", 'id = "bad"\nname = "Bad"\nversion = "1"\napi = "1.0"\n')
            z.writestr("plugin.py", "def register(api): pass\n")
            z.writestr(name, "x")
        with pytest.raises(ValueError, match=why):
            store.read(path)
    (tmp_path / "plain.zip").write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="not a zip"):
        store.read(tmp_path / "plain.zip")


def test_the_scan_says_what_code_does(tmp_path):
    files = {"plugin.py": b"import socket, subprocess, ctypes\nimport shutil\nexec('1')\nopen('aes_keys.txt')\n",
             "x.pyc": b"\0"}
    found = store.scan(files)
    for words in ("network", "other programs", "native code", "delete or move", "builds while running",
                  "console keys", "compiled files"):
        assert any(words in f for f in found), words


def test_install_consent_and_removal(tmp_path):
    package = store.read(store.pack(EXAMPLES / "hello", tmp_path / "h.udsplugin"))
    assert store.compare(package, None) == "install"
    store.install(package)
    reg = plugins.registry()
    assert "hello-greet" in reg.tools and store.installed("hello").enabled
    plugins.set_enabled("hello", False)
    assert "hello-greet" not in plugins.registry().tools
    # a plugin copied in by hand stays off until accepted
    make_plugin(plugins.plugins_dir() / "handmade", "handmade", "    pass\n")
    plugins.set_enabled("handmade", True)
    hand = next(p for p in plugins.reload().plugins if p.id == "handmade")
    assert not hand.consented and "not accepted" in hand.error
    store.accept(hand)
    assert next(p for p in plugins.registry().plugins if p.id == "handmade").consented
    (plugins.data_folder("hello") / "kept.txt").write_text("x")
    store.remove("hello", data=True)
    assert store.installed("hello") is None and not (plugins.data_folder("hello") / "kept.txt").exists()


def test_guardrails(monkeypatch, tmp_path):
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "scratch"))   # pytest's folders are in /tmp
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    folder = make_plugin(tmp_path / "rogue", "rogue", (
        f"    import os\n    open(api.data / 'ok.txt', 'w').write('fine')\n    os.remove({str(victim)!r})\n"))
    monkeypatch.setenv("POKEUDS_PLUGINS", str(folder))
    rogue = next(p for p in plugins.reload().plugins if p.id == "rogue")
    assert "PermissionError" in rogue.error and "guardrails" in rogue.error
    assert victim.read_text() == "keep me" and (plugins.data_folder("rogue") / "ok.txt").exists()
    folder2 = make_plugin(tmp_path / "shell", "shell", "    import subprocess\n    subprocess.run(['rm', '-rf', '/tmp/x'])\n")
    monkeypatch.setenv("POKEUDS_PLUGINS", str(folder2))
    assert "the command" in next(p for p in plugins.reload().plugins if p.id == "shell").error
    victim.unlink()                       # PokeUDS's own code is never stopped
    assert not victim.exists()


def test_sources(tmp_path):
    package = store.pack(EXAMPLES / "hello", tmp_path / "hello-1.0.0.udsplugin")
    entry = store.listing_entry(package, "http://127.0.0.1:PORT/{file}")
    assert store.listing_url("me/my-plugins").endswith("/me/my-plugins/HEAD/pokeuds-source.json")
    assert store.listing_url("https://example.org/plugins") == "https://example.org/plugins/pokeuds-source.json"
    with pytest.raises(ValueError):
        store.listing_url("http://example.org/x.json")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Quiet(*a, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        entry["download"] = entry["download"].replace("PORT", str(port))
        listing = {"schema": 1, "name": "Test plugins",
                   "plugins": [entry, {**entry, "id": "no-hash", "sha256": ""}, {**entry, "id": "Bad Id"}]}
        (tmp_path / "pokeuds-source.json").write_text(json.dumps(listing))
        store.follow(f"http://127.0.0.1:{port}/pokeuds-source.json")
        [(text, name, listed)] = store.browse()
        assert name == "Test plugins" and [e.id for e in listed] == ["hello"]
        path, got = store.download(listed[0], tmp_path / "dl")
        assert got.id == "hello" and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        listed[0].sha256 = "0" * 64
        with pytest.raises(ValueError, match="SHA-256"):
            store.download(listed[0], tmp_path / "dl2")
    finally:
        server.shutdown()


def test_pages_build_headless(monkeypatch, tmp_path):
    pytest.importorskip("flet")
    monkeypatch.setenv("POKEUDS_PLUGINS", str(EXAMPLES / "hello"))
    plugins.reload()
    from gui.app import App
    from gui.views.plugins import PluginsView
    from gui.views.trade import TradeView
    from pokeuds.app.settings import Settings

    app = App(None, Settings(output=str(tmp_path / "received")))
    PluginsView(app).control()
    trade = TradeView(app)
    trade.control()
    trade.pick_tool("hello-greet")
    assert trade.tool == "hello-greet" and trade.entry.key == "rby"
    trade.control()
    assert plugins.tool_command("hello-greet", trade.tool_values.get("hello-greet", {}), trade.tool_tokens())[:3] == \
        ["plugin", "run", "hello-greet"]
    trade.pick_game("xy")
    assert trade.tool == ""
