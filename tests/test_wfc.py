"""The DS Wi-Fi Mystery Gift stand-in (docs/ds_wifi.md): DNS, Nintendo's base64, NAS, DLS1 and the certificate.

The TLS test runs only when tlslite-ng is installed (it is the optional dependency the server needs for the DS's
SSL 3.0); the rest needs nothing but the standard library and pycryptodome.
"""

from __future__ import annotations

import socket
import threading
from pathlib import Path

import pytest
from Crypto.PublicKey import RSA

from pokeuds.wfc import certs, dls1, dns, http, nas
from pokeuds.wfc.server import Config, WFCServer


# -- Nintendo's base64 ---------------------------------------------------------------------------------------------

def test_base64_uses_the_ds_alphabet():
    value = b"\xfb\xff".decode("latin-1")                 # base64 "+/8=": both substituted characters
    encoded = nas.encode_value(value)
    assert "+" not in encoded and "/" not in encoded and "=" not in encoded
    assert encoded == ".-8*"
    assert nas.decode_value(encoded) == value


def test_base64_reads_the_altwfc_variants():
    # The same "+/8=" written with AltWFC's '>' for + and '?' for /.
    assert nas.decode_value(">-8*") == b"\xfb\xff".decode("latin-1")
    assert nas.decode_value(">?8*") == b"\xfb\xff".decode("latin-1")


def test_form_round_trip():
    values = {"action": "login", "userid": "1000000001", "locator": "gamespy.com", "challenge": "Ab3+/x=="}
    body = nas.encode_form(values)
    assert b"+" not in body and b"/" not in body
    assert nas.decode_form(body) == values


# -- DNS -----------------------------------------------------------------------------------------------------------

def test_dns_answers_only_the_nintendo_names():
    resolver = dns.Resolver("192.168.1.5")
    assert resolver.is_ours("nas.nintendowifi.net")
    assert resolver.is_ours("NINTENDOWIFI.NET.")
    assert not resolver.is_ours("example.com")
    reply = resolver.resolve(dns.query("dls1.nintendowifi.net"))
    assert dns.answer_address(reply) == "192.168.1.5"
    assert dns.answer_address(resolver.resolve(dns.query("nas.nintendowifi.net", dns.QTYPE_AAAA))) is None
    other = resolver.resolve(dns.query("example.com"))
    assert (int.from_bytes(other[2:4], "big") & 0xF) == dns.RCODE_NXDOMAIN


def test_dns_udp_server_answers():
    resolver = dns.Resolver("127.0.0.1")
    server = dns.Server(resolver, "127.0.0.1", 0)
    server.start()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(5)
        sock.sendto(dns.query("conntest.nintendowifi.net", txid=7), ("127.0.0.1", server.port))
        reply, _ = sock.recvfrom(2048)
        sock.close()
        assert reply[:2] == b"\x00\x07"
        assert dns.answer_address(reply) == "127.0.0.1"
    finally:
        server.close()


# -- NAS -----------------------------------------------------------------------------------------------------------

def _request(path: str, fields: dict, host: str = "nas.nintendowifi.net") -> http.Request:
    return http.Request("POST", path, {}, {"host": host}, nas.encode_form(fields), "127.0.0.1")


def test_nas_login_then_gen4_svcloc():
    sessions = nas.Sessions()
    server = nas.NAS(sessions)
    reply = nas.decode_form(server.handle(_request("/ac", {
        "action": "login", "userid": "1000000001", "gsbrcd": "ADAE", "devtime": "120101120000",
        "ingamesn": "RED"})).body)
    assert reply["returncd"] == "001" and reply["retry"] == "0" and reply["locator"] == "gamespy.com"
    assert len(reply["challenge"]) == 8 and reply["token"]
    assert len(reply["datetime"]) == 14          # AltWFC's Gen 4 format
    placed = nas.decode_form(server.handle(_request("/ac", {
        "action": "svcloc", "userid": "1000000001", "gamecd": "ADAE", "svc": "9000",
        "token": reply["token"]})).body)
    assert placed["returncd"] == "007" and placed["retry"] == "0"
    assert placed["svchost"] == "dls1.nintendowifi.net" and placed["token"]


def test_nas_gen5_svcloc_uses_entralinked_answer():
    sessions = nas.Sessions()
    server = nas.NAS(sessions)
    token = sessions.new({"devtime": "120101120000"})
    placed = nas.decode_form(server.handle(_request("/ac", {
        "action": "svcloc", "userid": "1000000001", "gamecd": "IRAO", "svc": "9000", "token": token})).body)
    assert placed["returncd"] == "001"
    assert "retry" not in placed
    assert placed["servicetoken"] and placed["svchost"] == "dls1.nintendowifi.net"
    assert len(placed["datetime"]) == 12          # Entralinked's Gen 5 format
    # DLS1 finds the console's clock through the svcloc token too, so the Gen 5 date choice is the login day.
    assert sessions.get(placed["servicetoken"]).devtime == "120101120000"


def test_nas_conntest_and_profanity():
    server = nas.NAS(nas.Sessions())
    get = http.Request("GET", "/", {}, {"host": "conntest.nintendowifi.net"}, b"", "127.0.0.1")
    reply = server.handle(get)
    assert reply.body == b"ok" and reply.status == 200
    words = nas.decode_form(server.handle(http.Request(
        "POST", "/pr", {}, {}, nas.encode_form({"words": "bad\tworse"}), "127.0.0.1")).body)
    assert words["returncd"] == "000" and words["prwords"] == "00" and words["prwordsE"] == "00"


# -- DLS1 ----------------------------------------------------------------------------------------------------------

def _gift_folder(tmp_path: Path, game: str, files: dict[str, bytes], listing: str = "") -> Path:
    folder = tmp_path / "gifts" / game
    folder.mkdir(parents=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)
    if listing:
        (folder / "_list.txt").write_bytes(listing.encode("latin-1"))
    return tmp_path / "gifts"


def _dls(path: str, fields: dict) -> http.Request:
    return http.Request("POST", path, {}, {"host": "dls1.nintendowifi.net"}, nas.encode_form(fields), "127.0.0.1")


def test_dls1_gen4_serves_one_gift_by_date(tmp_path):
    gifts = _gift_folder(tmp_path, "ADA", {"1.myg": b"a" * 936, "2.myg": b"b" * 936},
                         "1.myg\t\t\t\t\t936\r\n2.myg\t\t\t\t\t936\r\n")
    sessions = nas.Sessions()
    token = sessions.new({"devtime": "120101120000"})          # 1 January: the first file
    server = dls1.DLS1(gifts, sessions)
    count = server.handle(_dls("/download", {"action": "count", "gamecd": "ADAE", "token": token}))
    assert count.body == b"1"
    listing = server.handle(_dls("/download", {"action": "list", "gamecd": "ADAE", "token": token}))
    assert listing.body.startswith(b"1.myg\t")
    contents = server.handle(_dls("/download", {"action": "contents", "gamecd": "ADAE", "contents": "1.myg"}))
    assert contents.body == b"a" * 936
    assert contents.headers["Content-Type"] == "application/x-dsdl"
    assert contents.version == "1.0"


def test_dls1_gen5_filters_the_game_then_picks_by_date(tmp_path):
    gaps = {
        "G1.bin": b"1" * 720,
        "G2.bin": b"2" * 720,
    }
    listing = ("G1.bin\t\tMYSTERY_E\t100000\t\t720\r\n"
               "G2.bin\t\tMYSTERY_E\t200000\t\t720\r\n")
    gifts = _gift_folder(tmp_path, "IRA", gaps, listing)
    sessions = nas.Sessions()
    token = sessions.new({"devtime": "120101120000"})
    server = dls1.DLS1(gifts, sessions)
    # IRBE is Black: only 0x200000 may be received.
    count = server.handle(_dls("/download", {"action": "count", "gamecd": "IRAO", "rhgamecd": "IRBE",
                                             "attr1": "MYSTERY_E", "token": token}))
    assert count.body == b"1"
    listing_reply = server.handle(_dls("/download", {"action": "list", "gamecd": "IRAO", "rhgamecd": "IRBE",
                                                     "attr1": "MYSTERY_E", "token": token}))
    assert listing_reply.body.startswith(b"G2.bin\t")


def test_dls1_gen5_intersects_the_request_mask(tmp_path):
    gifts = _gift_folder(tmp_path, "IRA", {"G3.bin": b"3" * 720},
                         "G3.bin\t\tMYSTERY_E\t300000\t\t720\r\n")
    server = dls1.DLS1(gifts, nas.Sessions())
    # An "all Black and White" file reaches a White cartridge even when the request carries only White's bit.
    reply = server.handle(_dls("/download", {"action": "count", "gamecd": "IRAO", "rhgamecd": "IRAE",
                                              "attr1": "MYSTERY_E", "attr2": "100000"}))
    assert reply.body == b"1"


def test_dls1_forced_gift_ignores_the_date(tmp_path):
    gifts = _gift_folder(tmp_path, "ADA", {"1.myg": b"a" * 936, "2.myg": b"b" * 936})
    server = dls1.DLS1(gifts, nas.Sessions(), gift="2.myg")
    listing = server.handle(_dls("/download", {"action": "list", "gamecd": "ADAE"}))
    assert listing.body.startswith(b"2.myg\t")


def test_dls1_refuses_traversal_and_missing_files(tmp_path):
    gifts = _gift_folder(tmp_path, "ADA", {"1.myg": b"a" * 936})
    server = dls1.DLS1(gifts, nas.Sessions())
    for name in ("../server.pem", "/etc/passwd", "_list.txt", ""):
        reply = server.handle(_dls("/download", {"action": "contents", "gamecd": "ADAE", "contents": name}))
        assert reply.status in (403, 404), name


# -- certificate and HTTP ------------------------------------------------------------------------------------------

def _synthetic_pair(tmp_path: Path) -> tuple[certs.WiiPair, RSA.RsaKey]:
    """A stand-in Nintendo CA and client certificate, so the tests need no Wii dump."""
    ca_key = RSA.generate(1024)
    name = certs.seq(certs.tlv(0x31, certs.seq(certs.oid("2.5.4.3"), certs.tlv(0x0C, b"Nintendo CA"))))
    ca = certs.Certificate(b"", name, name, ca_key.publickey())
    client_der, client_key = certs.make_server_cert(ca, ca_key, "Wii NWC Prod 1")
    client = certs.Certificate.parse(client_der)
    cert_path, key_path = tmp_path / "clientca.pem", tmp_path / "clientcakey.pem"
    cert_path.write_text(certs.der_to_pem(client_der), encoding="ascii")
    key_path.write_bytes(client_key.export_key("PEM"))
    pair = certs.WiiPair(cert_path, key_path, client, client_key, False)
    return pair, ca_key


def test_server_certificate_chains_to_the_wii_key():
    ca_key = RSA.generate(1024)
    name = certs.seq(certs.tlv(0x31, certs.seq(certs.oid("2.5.4.3"), certs.tlv(0x0C, b"Nintendo CA"))))
    ca = certs.Certificate(b"", name, name, ca_key.publickey())
    der, key = certs.make_server_cert(ca, ca_key, "*.*.*")
    server = certs.Certificate.parse(der)
    assert server.issuer == ca.subject
    assert server.signed_by(ca)
    assert server.public_key == key.publickey()
    assert certs.Certificate.common_name(server.subject) == "*.*.*"


def test_http_server_serves_nas_and_dls(tmp_path):
    pair, _ = _synthetic_pair(tmp_path)
    gifts = _gift_folder(tmp_path, "ADA", {"1.myg": b"a" * 936})
    config = Config(answer_ip="127.0.0.1", dns_port=0, http_port=0, https_port=0, gifts=gifts,
                    cache=tmp_path / "cache", cert=pair.cert_path, key=pair.key_path, dns=False, https=False)
    server = WFCServer(config)
    bound = server.start()
    try:
        import urllib.request

        base = f"http://127.0.0.1:{bound.http}"
        assert urllib.request.urlopen(base + "/", timeout=5).read() == b"ok"
        request = urllib.request.Request(base + "/ac", data=nas.encode_form(
            {"action": "login", "gsbrcd": "ADAE", "devtime": "120101120000"}), method="POST")
        with urllib.request.urlopen(request, timeout=5) as reply:
            assert nas.decode_form(reply.read())["returncd"] == "001"
    finally:
        server.close()


def test_export_writes_the_dls1_layout(tmp_path):
    import base64

    from pokeuds.wfc import export

    class FakeGift:
        def __init__(self, data):
            self.wifi_data = data

    class FakeHelper:
        def gifts(self, game, all=False):
            if game == "dp":
                return [{"card_id": 10, "title": "Test Gift", "games": ["Diamond"],
                         "card_data": base64.b64encode(b"c" * 856).decode()}]
            return [{"card_id": 11, "title": "Black Gift", "games": ["Black"],
                     "card_data": base64.b64encode(b"g" * 204).decode(),
                     "restrict_language": 2, "restrict_version": 0}]

        def gift(self, game, data=None, trainer=None):
            return FakeGift(b"m" * 936 if game == "dp" else b"b" * 720)

    written = export.export(tmp_path, ["dp", "bw"], ["E"], helper=FakeHelper())
    assert written == {"ADA": 1, "IRA": 1}
    assert (tmp_path / "ADA" / "0010_test_gift.myg").stat().st_size == 936
    assert (tmp_path / "IRA" / "0011_black_gift_E.bin").stat().st_size == 720
    four = (tmp_path / "ADA" / "_list.txt").read_bytes()
    five = (tmp_path / "IRA" / "_list.txt").read_bytes()
    assert b"\r\r" not in four and b"\r\r" not in five
    assert four == b"0010_test_gift.myg\t\t\t\t\t936\r\n"
    assert five == b"0011_black_gift_E.bin\t\tMYSTERY_E\tF00000\t\t720\r\n"


# -- the DS's TLS, when tlslite-ng is installed ---------------------------------------------------------------

def test_tls_ssl3_rc4_handshake(tmp_path):
    pytest.importorskip("tlslite", reason="tlslite-ng is the optional DS TLS dependency")
    import pokeuds.wfc.tls as tls
    pair, _ = _synthetic_pair(tmp_path)
    identity = certs.server_identity(pair, tmp_path / "cache")
    assert identity.chain[0]

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    seen = {}

    def serve():
        conn, _ = listener.accept()
        wrapped = tls.Server(identity).wrap(conn)
        seen["version"] = wrapped.version
        seen["cipher"] = wrapped.session.cipherSuite
        request = http.read_request(wrapped, "127.0.0.1")
        http.write_response(wrapped, http.Response(200, b"ok"))
        seen["path"] = request.path
        wrapped.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    from tlslite import TLSConnection

    client = TLSConnection(socket.create_connection(("127.0.0.1", port), timeout=10))
    client.handshakeClientCert(settings=tls.settings())
    assert client.version == (3, 0)
    client.sendall(b"GET / HTTP/1.0\r\nHost: conntest.nintendowifi.net\r\n\r\n")
    data = b""
    while b"ok" not in data:
        chunk = client.recv(4096)
        if not chunk:
            break
        data += chunk
    client.close()
    thread.join(timeout=10)
    listener.close()
    assert seen["version"] == (3, 0)
    assert seen["path"] == "/"
    assert b"ok" in data
