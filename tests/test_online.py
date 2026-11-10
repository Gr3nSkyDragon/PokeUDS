"""Online: secp256k1 and BIP-340, Nostr events, and the relay client against a loopback relay."""

import json
import socket

import pytest

from pokeuds.online import relays, schnorr
from tests.fake_relay import Store, serve

# BIP-340's test vectors 0 and 1: secret key, public key, aux randomness, message, signature.
VECTORS = (
    ("0000000000000000000000000000000000000000000000000000000000000003",
     "F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9", "00" * 32, "00" * 32,
     "E907831F80848D1069A5371B402410364BDF1C5F8307B0084C55F1CE2DCA8215"
     "25F66A4A85EA8B71E482A74F382D2CE5EBEEE8FDB2172F477DF4900D310536C0"),
    ("B7E151628AED2A6ABF7158809CF4F3C762E7160F38B4DA56A784D9045190CFEF",
     "DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659", "00" * 31 + "01",
     "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89",
     "6896BD60EEAE296DB48A229FF71DFE071BDE413E6D43F917DC8DCF8C78DE3341"
     "8906D11AC976ABCCB20B091292BFF4EA897EFCB639EA871CFA95F6DE339E4B0A"),
)


@pytest.mark.parametrize("secret,public,aux,message,signature", VECTORS)
def test_bip340_vectors(secret, public, aux, message, signature):
    secret, public, aux, message, signature = (bytes.fromhex(x) for x in (secret, public, aux, message, signature))
    assert schnorr.public_key(secret) == public
    assert schnorr.sign(secret, message, aux) == signature and schnorr.verify(public, message, signature)
    assert not schnorr.verify(public, message, signature[:-1] + bytes([signature[-1] ^ 1]))
    assert not schnorr.verify(public, bytes(32 - 1) + b"\x01", signature)
    assert not schnorr.verify(public[:-1], message, signature)


def test_two_keys_share_one_secret():
    a, b, c = schnorr.new_secret(), schnorr.new_secret(), schnorr.new_secret()
    shared = schnorr.shared_x(a, schnorr.public_key(b))
    assert shared == schnorr.shared_x(b, schnorr.public_key(a)) and len(shared) == 32
    assert shared != schnorr.shared_x(c, schnorr.public_key(a))
    with pytest.raises(ValueError):
        schnorr.public_key(bytes(32))                       # zero is no key
    with pytest.raises(ValueError):
        schnorr.shared_x(a, b"\xff" * 32)                   # not on the curve


def test_events_are_signed_and_checked():
    secret = schnorr.new_secret()
    event = relays.sign_event(secret, 30402, "héllo", [["d", "abc"], ["t", "pokeuds-gts"], ["t", "x"]], created_at=1000)
    assert relays.valid(event) and event["pubkey"] == schnorr.public_key(secret).hex()
    assert relays.tag(event, "d") == "abc" and relays.tags(event, "t") == ["pokeuds-gts", "x"] and relays.tag(event, "p") is None
    for change in ({"content": "hello"}, {"created_at": 1001}, {"kind": 1}, {"tags": [["d", "abd"]]},
                   {"sig": "00" * 64}, {"pubkey": schnorr.public_key(schnorr.new_secret()).hex()}):
        assert not relays.valid({**event, **change})
    assert not relays.valid({**event, "tags": [["d", 5]]}) and not relays.valid("event") and not relays.valid({})
    assert relays.matches(event, {"kinds": [30402], "#t": ["x", "y"], "authors": [event["pubkey"]], "since": 1000})
    assert not relays.matches(event, {"#t": ["y"]}) and not relays.matches(event, {"until": 999})
    assert not relays.matches(event, {"kinds": [1]}) and not relays.matches(event, {"ids": ["00"]})


def test_the_store_replaces_deletes_and_expires():
    now = [5000.0]
    store = Store(clock=lambda: now[0])
    a, b = schnorr.new_secret(), schnorr.new_secret()
    first = relays.sign_event(a, 30402, "one", [["d", "x"]], created_at=100)
    second = relays.sign_event(a, 30402, "two", [["d", "x"]], created_at=200)
    other = relays.sign_event(a, 30402, "other", [["d", "y"], ["expiration", "6000"]], created_at=150)
    assert all(store.add(e) for e in (first, second, other))
    assert not store.add({**first, "content": "forged"})
    assert [e["content"] for e in store.query({"kinds": [30402]})] == ["two", "other"]      # the newest per d
    assert store.add(first) and [e["content"] for e in store.query({"#d": ["x"]})] == ["two"]   # an older one never wins
    store.add(relays.sign_event(b, 5, "", [["e", second["id"]]], created_at=300))           # not b's to delete
    assert len(store.query({"#d": ["x"]})) == 1
    store.add(relays.sign_event(a, 5, "", [["e", second["id"]]], created_at=300))
    assert store.query({"#d": ["x"]}) == []
    now[0] = 6000.0
    assert store.query({"#d": ["y"]}) == []                                                 # expired (NIP-40)
    store.add(relays.sign_event(a, 21059, "passing", [], created_at=10))
    assert store.query({"kinds": [21059]}) == []                                            # ephemeral: not stored


@pytest.mark.parametrize("fragment", [False, True])
def test_the_pool_against_a_websocket_relay(fragment):
    """The real client over loopback: the upgrade, a ping, masked frames, fragmented replies, two relays and a dead one."""
    one, two = Store(), Store()
    url_one, stop_one = serve(one, fragment)
    url_two, stop_two = serve(two, fragment)
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))                             # bound, never listening: connections are refused
    secret = schnorr.new_secret()
    listing = relays.sign_event(secret, 30402, json.dumps({"big": "x" * 3000}), [["d", "l1"], ["t", "pokeuds-gts"]])
    wrap = relays.sign_event(secret, 1059, "sealed", [["p", "ab" * 32]])
    try:
        with relays.Pool([url_one, url_two, f"ws://127.0.0.1:{dead.getsockname()[1]}"]) as pool:
            assert pool.publish(listing) == 2 and pool.publish(wrap) == 2
            assert pool.publish({**listing, "content": "forged"}) == 0         # the relays check signatures
            assert pool.reached() == 2 and len(pool.problems()) == 1
            two.add(relays.sign_event(secret, 1059, "only on two", [["p", "ab" * 32]], created_at=wrap["created_at"] + 5))
            found = pool.fetch({"kinds": [1059], "#p": ["ab" * 32]}, timeout=3)
            assert [e["content"] for e in found] == ["sealed", "only on two"]      # once each, oldest first
            assert pool.fetch({"kinds": [30402], "#t": ["pokeuds-gts"]}, timeout=3) == [listing]
            assert pool.fetch({"kinds": [30402], "#t": ["other"]}, timeout=3) == []
        assert pool.reached() == 0
    finally:
        stop_one()
        stop_two()
        dead.close()


def test_a_relay_that_lies_is_not_believed():
    """Events with a bad signature, or that the filter did not ask for, are dropped on the way in."""
    store = Store()
    secret = schnorr.new_secret()
    good = relays.sign_event(secret, 1059, "good", [["p", "cd" * 32]])
    store.events["forged"] = {**good, "id": "forged", "content": "forged"}        # past the store's own check
    store.events["offtopic"] = relays.sign_event(secret, 1, "a note", [])
    store.add(good)
    store.query = lambda flt: list(store.events.values())                          # a relay ignoring the filter
    url, stop = serve(store)
    try:
        with relays.Pool([url]) as pool:
            assert pool.fetch({"kinds": [1059], "#p": ["cd" * 32]}, timeout=3) == [good]
    finally:
        stop()


def test_relay_addresses(monkeypatch):
    assert all(u.startswith("wss://") for u in relays.relay_urls()) and len(relays.relay_urls()) >= 5
    monkeypatch.setenv("POKEUDS_GTS_RELAYS", " wss://a.example , ws://127.0.0.1:7000 ,")
    assert relays.relay_urls() == ("wss://a.example", "ws://127.0.0.1:7000")
    with pytest.raises(ValueError):
        relays.WebSocket("https://relay.example")
