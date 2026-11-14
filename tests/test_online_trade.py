"""Online trades: the partner channel (pokeuds.online.link), the live relay pool against a loopback relay, and whole
trades between two consoles, each beside its own PokeUDS, through the relays.

The channel's tests and ``Hub`` are ported from pokeldn's ``tests/test_online.py`` (Decryptu/pokeldn, AGPL-3.0)."""

import base64
import os
import queue
import random
import threading
import time

import pytest

from pokeuds import pkx
from pokeuds.games import get, profile
from pokeuds.games.gen6 import session as gen6_session
from pokeuds.games.gen6 import wire as gen6_wire
from pokeuds.games.gen6.trade import Gen6Trade
from pokeuds.games.gen7.trade import Gen7Trade
from pokeuds.games.gen7 import session as gen7_session
from pokeuds.games.gen7 import wire as gen7_wire
from pokeuds.online import link, relays
from pokeuds.online import trade as online
from pokeuds.radio.sim import Air, SimRadio
from pokeuds.uds import Station
from tests.fake_relay import Store, serve
from tests.test_pkx import make_record


class Hub:
    """Every relay at once: an event reaches each subscriber unless the draw loses it, out of order. ``live`` delivers
    from a thread of its own, for partners ticking on the real clock."""

    def __init__(self, loss=0.0, seed=1, live=False):
        self.queue, self.pools = [], []
        self.loss, self.rng = loss, random.Random(seed)
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        if live:
            threading.Thread(target=self._deliver_live, daemon=True).start()

    def pool(self, on_event):
        hub = self

        class Pool:
            def subscribe(self, *a):
                pass

            def start(self):
                pass

            def close(self):
                pass

            def connected(self):
                return 1

            def publish(self, event):
                with hub.lock:
                    for other in hub.pools:
                        if other is not self and hub.rng.random() >= hub.loss:
                            hub.queue.append((other, event))
                return 1

        made = Pool()
        made.on_event = on_event
        self.pools.append(made)
        return made

    def deliver(self):
        with self.lock:
            batch, self.queue = self.queue, []
        self.rng.shuffle(batch)
        for pool, event in batch:
            pool.on_event(event)

    def _deliver_live(self):
        while not self.stopped.wait(0.01):
            self.deliver()


def partners(hub, now, count, room="gen6", code="12345678", validate=None, **kw):
    made = [link.Partner(room, code, name=f"P{i}", clock=lambda: now[0], pool_factory=hub.pool, validate=validate,
                         **kw) for i in range(count)]
    for p in made:
        p.start(thread=False)
    return made


def run(hub, now, made, until, seconds=120.0, step=0.1, extra=lambda: None):
    end = now[0] + seconds
    while now[0] < end and not until():
        for p in made:
            p.tick()
        hub.deliver()
        extra()
        now[0] += step
    return until()


@pytest.mark.parametrize("count,loss", [(2, 0.0), (7, 0.0), (6, 0.4)])
def test_everyone_in_a_room_pairs_with_one_other(count, loss):
    hub, now = Hub(loss), [1000.0]
    made = partners(hub, now, count)
    assert run(hub, now, made, lambda: sum(p.paired for p in made) == count - count % 2)
    by_key = {p.me.public: p for p in made}
    for p in made:
        if p.paired:
            assert by_key[p.peer].peer == p.me.public


def test_a_different_code_or_room_never_meets():
    hub, now = Hub(), [1000.0]
    made = (partners(hub, now, 1, code="11111111") + partners(hub, now, 1, code="22222222")
            + partners(hub, now, 1, room="gen7", code="11111111"))
    assert not run(hub, now, made, lambda: any(p.paired for p in made), seconds=30)


def test_rooms_are_pokeuds_own():
    assert link.room_id("gen6", "") == link.room_id("gen6", "open") != link.room_id("gen7", "")
    import hashlib
    assert link.room_id("gen6", "42") == hashlib.sha256(b"pokeuds online 1|gen6|42").hexdigest()


def test_the_channel_carries_rounds_in_order_through_loss():
    hub, now = Hub(loss=0.4, seed=7), [1000.0]
    a, b = partners(hub, now, 2)
    assert run(hub, now, [a, b], lambda: a.paired and b.paired)
    b.offer(b"theirs")
    assert run(hub, now, [a, b], lambda: a.theirs().offer == b"theirs")
    a.offer(b"first")
    a.withdraw()
    a.offer(b"second")
    a.accept()
    assert run(hub, now, [a, b], lambda: b.theirs().accepted)
    assert b.theirs().offer == b"second" and b.theirs().withdrawn == 1
    a.done()
    b.done()
    a.offer(b"next")
    assert run(hub, now, [a, b], lambda: b.theirs().offer == b"next")
    assert b.round == 2 and b.rounds[1].done


def test_a_yes_to_an_offer_since_taken_back_counts_for_nothing():
    """The partner's console said yes to this console's first offer; this console has offered another since."""
    hub, now = Hub(), [1000.0]
    a, b = partners(hub, now, 2)
    assert run(hub, now, [a, b], lambda: a.paired and b.paired)
    a.offer(b"first")
    assert run(hub, now, [a, b], lambda: b.theirs().offer == b"first")
    b.accept()                                   # names b"first"
    a.offer(b"second")                           # before b's yes arrives
    assert run(hub, now, [a, b], lambda: b.theirs().offer == b"second")
    assert not a.theirs().accepted
    b.accept()
    assert run(hub, now, [a, b], lambda: a.theirs().accepted)


def test_a_refused_record_never_reaches_the_partner_side():
    hub, now = Hub(), [1000.0]
    a, b = partners(hub, now, 2, validate=lambda record: ("bad", "") if record == b"bad" else (None, "it"))
    assert run(hub, now, [a, b], lambda: a.paired and b.paired)
    a.offer(b"bad")
    assert run(hub, now, [a, b], lambda: a.theirs().refused == "bad")
    assert b.theirs().offer is None
    a.offer(b"good")                              # a new offer clears the refusal
    assert a.theirs().refused == ""
    assert run(hub, now, [a, b], lambda: b.theirs().offer == b"good")


def test_a_partner_silent_before_any_offer_is_replaced_and_after_one_is_lost():
    hub, now = Hub(), [1000.0]
    a, b = partners(hub, now, 2)
    assert run(hub, now, [a, b], lambda: a.paired and b.paired)
    assert run(hub, now, [a], lambda: a.state == "searching", seconds=link.LOST_AFTER + 5)
    assert run(hub, now, [a, b], lambda: a.paired and b.paired)
    a.offer(b"mon")
    assert run(hub, now, [a], lambda: a.state == "lost", seconds=link.LOST_AFTER + 5)


def test_a_partner_that_left_is_an_offer_taken_back_and_ends_the_offers():
    hub, now = Hub(), [1000.0]
    a, b = partners(hub, now, 2)
    offers = online.Offers(a)
    assert run(hub, now, [a, b], lambda: a.paired and b.paired)
    a.offer(b"mine")
    b.offer(b"theirs")
    b.accept(b"mine")
    assert run(hub, now, [a, b], lambda: a.theirs().accepted)
    assert offers.offer() == b"theirs" and offers.partner_accepts() is True and not offers.ended()
    b.close()
    assert run(hub, now, [a], lambda: a.state == "lost", seconds=5)
    assert a.theirs().offer is None and not a.theirs().accepted
    assert offers.offer() is None and offers.partner_accepts() is None and offers.ended().startswith("the partner left")


@pytest.mark.parametrize("damage", ["signature", "identity", "old", "future", "oversize", "tags"])
def test_untrusted_relay_events_cannot_pair(damage):
    hub, now = Hub(), [1000.0]
    a, b = sorted(partners(hub, now, 2), key=lambda p: p.me.public)
    a.state = b.state = "searching"
    key = a._pair_key(b.me.public, b.dh_public)
    a._direct(b.me.public, b.dh_public, key, {"t": "invite", "n": "attacker"})
    event = dict(hub.queue[-1][1])
    if damage == "signature":
        event["sig"] = "0" * 128
    elif damage == "identity":
        event["pubkey"] = "0" * 64
    elif damage == "old":
        event["created_at"] = 0
    elif damage == "future":
        event["created_at"] = 2000
    elif damage == "oversize":
        event["content"] = "x" * (link.EVENT_LIMIT + 1)
    else:
        event["tags"] = [["t", b.room], 1]
    b._on_event(event)
    assert not b.paired


def test_paired_identity_cannot_change_its_encryption_key():
    hub, now = Hub(), [1000.0]
    a, b = partners(hub, now, 2)
    assert run(hub, now, [a, b], lambda: a.paired and b.paired)
    impostor = link.Partner(a.room_name, a.code, clock=lambda: now[0], pool_factory=hub.pool)
    impostor.me = b.me
    impostor._direct(a.me.public, a.dh_public, impostor._pair_key(a.me.public, a.dh_public),
                     {"t": "offer", "r": 1, "s": a.in_next, "d": "Zm9yZ2Vk"})
    hub.deliver()
    assert a.theirs().offer is None and a.peer_x == b.dh_public


def test_sequence_and_round_floods_do_not_grow_state_or_ack_unsent_messages():
    hub, now = Hub(), [1000.0]
    a, b = partners(hub, now, 2)
    assert run(hub, now, [a, b], lambda: a.paired and b.paired)
    a.offer(b"mine")
    retained = dict(a.unacked)
    for i in range(1000):
        a._on_direct(b.me.public, b.dh_public, a.key, {"t": "accept", "s": a.in_next + 1000 + i,
                                                    "r": 1000 + i, "a": a.out_seq + 1000})
        a._deliver({"t": "accept", "r": 1000 + i})
    assert not a.early and set(a.rounds) <= {1} and a.unacked == retained


def test_record_and_validation_limits_protect_the_online_checker():
    checked = []
    hub, now = Hub(), [1000.0]
    a, b = partners(hub, now, 2, validate=lambda raw: (checked.append(raw) or None, "test"))
    assert run(hub, now, [a, b], lambda: a.paired and b.paired)
    a._deliver({"t": "offer", "r": 1, "d": base64.b64encode(b"x" * (link.RECORD_LIMIT + 1)).decode()})
    assert not checked
    for i in range(link.CHECKS_PER_MINUTE + 1):
        a._deliver({"t": "offer", "r": 1, "d": base64.b64encode(bytes([i])).decode()})
    assert len(checked) == link.CHECKS_PER_MINUTE and a.state == "lost"


def test_shared_blocks_have_known_names_and_exact_sizes():
    hub, now = Hub(), [1000.0]
    a, b = partners(hub, now, 2, shared_sizes={"party:0": 200})
    for name, data in [("arbitrary", b"x"), ("party:0", b"x" * 201), ("party:0", b"x" * 199)]:
        a._deliver({"t": "share", "r": 1, "k": name, "d": base64.b64encode(data).decode()})
    assert not a.theirs().shared
    a._deliver({"t": "share", "r": 1, "k": "party:0", "d": base64.b64encode(b"x" * 200).decode()})
    assert a.theirs().shared == {"party:0": b"x" * 200}
    (c,) = partners(hub, now, 1)                 # a trade that shares nothing takes nothing
    c._deliver({"t": "share", "r": 1, "k": "party:0", "d": base64.b64encode(b"x" * 200).decode()})
    assert not c.theirs().shared


def test_long_online_sessions_prune_completed_rounds_and_stale_peers():
    hub, now = Hub(), [1000.0]
    a, b = partners(hub, now, 2)
    for _ in range(100):
        a.theirs()
        a.done()
    assert len(a.rounds) <= 1
    a.state = "searching"
    for i in range(link.PEERS_LIMIT + 20):
        a._on_announce(f"{i:064x}", {"t": "here", "v": 1, "x": b.dh_public, "busy": True})
    assert len(a.peers) == link.PEERS_LIMIT
    now[0] += link.PEER_STALE + 1
    a.tick()
    assert not a.peers


def test_rejected_replacement_offer_clears_the_previous_confirmation():
    hub, now = Hub(), [1000.0]
    a, b = partners(hub, now, 2)
    a.theirs().offer, a.theirs().accepted = b"previous", True
    a._deliver({"t": "offer", "r": 1, "d": "!invalid!"})
    assert a.theirs().offer is None and not a.theirs().accepted


def test_outgoing_messages_are_bounded_when_the_partner_never_acknowledges():
    hub, now = Hub(), [1000.0]
    a, b = partners(hub, now, 2)
    assert run(hub, now, [a, b], lambda: a.paired and b.paired)
    a.traded = True
    for _ in range(link.WINDOW + 10):
        a.accept(b"x")
    assert a.state == "lost" and len(a.unacked) <= link.WINDOW


def test_the_checker_refuses_what_is_not_a_pokemon_without_asking_pkhex(monkeypatch):
    import pokeuds.pkhex as pkhex

    monkeypatch.setattr(pkhex, "shared", lambda: pytest.fail("PKHeX asked about a record that is not one"))
    validate = online.checker("xy")
    assert validate(b"\0" * 100)[0] and validate(bytes(pkx.STORED_SIZE - 1) + b"\1")[0]


def test_the_checker_passes_pkhex_findings_on(monkeypatch):
    import pokeuds.pkhex as pkhex

    record = pkx.fix_checksum(make_record(0x1234, species=658, nickname="Froakie"))
    answers = {}

    class Helper:
        def check(self, game, data):
            answers["game"] = game
            return pkhex.Built(answers["legal"], answers["report"], "", "pk6", data, {})

    monkeypatch.setattr(pkhex, "shared", lambda: Helper())
    validate = online.checker("oras")
    answers.update(legal=True, report="")
    reason, what = validate(record)
    assert reason is None and "Froakie" in what and "[species 658" in what and answers["game"] == "oras"
    answers.update(legal=False, report="Held item is unreleased.")
    assert "never released" in validate(record)[0]
    answers.update(legal=False, report="Encounter not matched.")
    reason, what = validate(record)
    assert reason is None and "PKHeX flags it: Encounter not matched." in what


# -- the live pool against a relay ------------------------------------------------------------------

def test_two_partners_meet_through_a_websocket_relay(monkeypatch):
    """The real pool: one thread per relay, a standing subscription, ephemeral events passed on as they come; a relay
    that refuses connections is retried without holding the others up."""
    monkeypatch.setattr(relays, "RECONNECT_MIN", 0.2)
    store = Store()
    url, stop = serve(store, fragment=True)
    dead = __import__("socket").socket()
    dead.bind(("127.0.0.1", 0))
    urls = [url, f"ws://127.0.0.1:{dead.getsockname()[1]}"]
    made = [link.Partner("gen6", "desk", name=n, pool_factory=lambda on_event: relays.LivePool(urls, on_event))
            for n in ("Ash", "Misty")]
    try:
        for p in made:
            p.start()
        deadline = time.monotonic() + 20
        while not all(p.paired for p in made):
            assert time.monotonic() < deadline, [p.state for p in made]
            time.sleep(0.05)
        a, b = made
        assert {a.peer_name, b.peer_name} == {"Ash", "Misty"} and a.pool.connected() == 1
        a.offer(b"a record")
        while b.theirs().offer != b"a record":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert not store.events                    # ephemeral: passed on, never kept
    finally:
        for p in made:
            p.close()
        stop()
        dead.close()


# -- whole trades: console, PokeUDS, the relays, PokeUDS, console -----------------------------------

@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(gen6_session, "SYNC_ASK_AFTER", 0.05)
    monkeypatch.setattr(gen6_session, "SCREEN_AFTER", 0.05)
    monkeypatch.setattr(gen7_session, "SCREEN_WAIT", 0.5)   # see tests/test_offers.py


def live_partners(hub, room="gen6"):
    made = [link.Partner(room, "desk", name=n, pool_factory=hub.pool, validate=lambda r: (None, "a Pokemon"))
            for n in ("Ash", "Misty")]
    for p in made:
        p.start()
    return made


def run_all(jobs, timeout=90):
    errors = queue.Queue()

    def wrap(name, job):
        try:
            job()
        except Exception as exc:  # pragma: no cover - reported below
            errors.put(f"{name}: {exc!r}")

    threads = [threading.Thread(target=wrap, args=item, daemon=True) for item in jobs.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
    assert errors.empty() and not any(t.is_alive() for t in threads), list(errors.queue)


def test_two_gen6_consoles_trade_through_the_relays(tmp_path, quick):
    """Two desks, each a console (a second engine with its own queue) beside a PokeUDS that offers its partner's
    console's Pokemon. Two trades: each console ends with the other's Pokemon, each PokeUDS with its own console's."""
    hub = Hub(live=True)
    pa, pb = live_partners(hub)
    a_mons = [make_record(0x11111111, species=658, nickname="Froakie"),
              make_record(0x33333333, species=650, nickname="Chespin")]
    b_mons = [make_record(0x22222222, species=653, nickname="Fennekin"),
              make_record(0x44444444, species=661, nickname="Fletchling")]
    desks, stations = {}, []
    for side, partner, mons in (("a", pa, a_mons), ("b", pb, b_mons)):
        air, key = Air(), os.urandom(16)

        def trade(mac, name, record, folder, **kw):
            station = Station(SimRadio(air), bytes.fromhex(mac), key, name=name)
            station.start(6)
            stations.append(station)
            me = gen6_wire.Trainer(name, 1234, 5678, os.urandom(8), os.urandom(4), gen6_wire.VERSION_CODES["X"], name)
            return Gen6Trade(station, get("xy"), profile.load("xy"), record, folder, me, channels=(6,), **kw)

        ours = trade("02000000A601", "POKEUDS", b"", tmp_path / side / "pokeuds", offers=online.Offers(partner))
        console = trade("02000000A602", f"Player{side}", mons[0], tmp_path / side / "console", queue=mons[1:],
                        trades=2)
        console.queue_party = [gen6_session.party_record(o) for o in console.queue]
        desks[side] = (ours, console, air)
    try:
        run_all({"a pokeuds": lambda: desks["a"][0].run("join", 30.0),
                 "a console": lambda: desks["a"][1].run("host", 30.0),
                 "b pokeuds": lambda: desks["b"][0].run("host", 30.0),
                 "b console": lambda: desks["b"][1].run("join", 30.0)})
        stored = lambda mons: [m[:pkx.STORED_SIZE] for m in mons]
        assert [p.read_bytes() for p in desks["a"][1].saved] == stored(b_mons)
        assert [p.read_bytes() for p in desks["b"][1].saved] == stored(a_mons)
        assert desks["a"][0].saved == desks["b"][0].saved == []      # online, PokeUDS keeps nothing
        assert pa.round == pb.round == 3
    finally:
        hub.stopped.set()
        for p in (pa, pb):
            p.close()
        for s in stations:
            s.close()
        for _, _, air in desks.values():
            air.close()


def test_two_gen7_consoles_trade_through_the_relays(quick):
    hub = Hub(live=True)
    pa, pb = live_partners(hub, "gen7")
    pk7 = lambda ec, sp, name: pkx.fix_checksum(make_record(ec, species=sp, nickname=name, version=31))
    a_mon, b_mon = pk7(0x1111, 19, "Rattata"), pk7(0x2222, 773, "Silvally")
    results, stations, airs, jobs = {}, [], [], {}
    comm, pass_ = 0x00164810, b"GNgBwMcVotYs\x00"
    for side, partner, mon, pokeuds_hosts in (("a", pa, a_mon, True), ("b", pb, b_mon, False)):
        air, key = Air(), os.urandom(16)
        airs.append(air)
        host_st = Station(SimRadio(air), bytes.fromhex("02000000B701"), key, name="HOST")
        join_st = Station(SimRadio(air), bytes.fromhex("02000000B702"), key, name="JOIN")
        host_st.start(1)
        join_st.start(1)
        stations += [host_st, join_st]

        def session(station, name, pokeuds, partner=partner, mon=mon):
            if pokeuds:
                return gen7_session.Gen7Session(station, gen7_wire.Trainer(name), offers=online.Offers(partner))
            return gen7_session.Gen7Session(station, gen7_wire.Trainer(name), mon, trades=1, answer_first=True)

        def host(side=side, host_st=host_st, pokeuds=pokeuds_hosts, session=session):
            host_st.host(wlan_comm_id=comm, passphrase=pass_, channel=1, max_nodes=4, id_=1)
            deadline = time.monotonic() + 10
            while not any(p.connected for p in host_st.peers.values()):
                assert time.monotonic() < deadline, "nobody joined"
                time.sleep(0.02)
            results[(side, "pokeuds" if pokeuds else "console")] = session(host_st, "Hosty", pokeuds).run()

        def join(side=side, join_st=join_st, pokeuds=not pokeuds_hosts, session=session):
            found = join_st.scan(10, until=lambda b: b.info.wlan_comm_id == comm)
            join_st.connect(next(b for b in found if b.info.wlan_comm_id == comm), pass_)
            results[(side, "pokeuds" if pokeuds else "console")] = session(join_st, "Joiny", pokeuds).run()

        jobs[f"{side} host"], jobs[f"{side} join"] = host, join
    try:
        run_all(jobs)
        assert results[("a", "console")] == [b_mon] and results[("b", "console")] == [a_mon]
        assert results[("a", "pokeuds")] == [a_mon] and results[("b", "pokeuds")] == [b_mon]
    finally:
        hub.stopped.set()
        for p in (pa, pb):
            p.close()
        for s in stations:
            s.close()
        for air in airs:
            air.close()


# -- the command line ------------------------------------------------------------------------------

@pytest.mark.parametrize("argv,why", [
    (["trade", "xy", "--radio", "demo:x"], "--offer is needed"),
    (["trade", "xy", "--radio", "demo:x", "--online", "--offer", "a.pk6"], "leave out --offer"),
])
def test_the_command_line_says_what_an_online_trade_needs(argv, why, tmp_path, monkeypatch):
    from pokeuds import cli

    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    with pytest.raises(SystemExit) as stop:
        cli.main(argv)
    assert why in str(stop.value)


def test_two_command_line_runs_trade_online_through_a_relay(tmp_path):
    """Two `pokeuds trade xy --online-code desk` processes, each beside the demo radio's X console (PokeUDS's own
    engine playing the console), meet through a loopback relay; PKHeX checks each Pikachu; both trade and exit."""
    import subprocess
    import sys
    from pathlib import Path

    from pokeuds import pkhex

    if pkhex.helper_path() is None:
        pytest.skip("the PKHeX helper is not built")
    root = Path(__file__).resolve().parents[1]
    store = Store()
    url, stop = serve(store)
    env = dict(os.environ, POKEUDS_RELAYS=url, POKEUDS_DATA=str(tmp_path / "data"))
    runs = [subprocess.Popen([sys.executable, "-m", "pokeuds", "trade", "xy", "--radio", "demo:x", "--online-code",
                              "desk", "--trades", "1", "--output", str(tmp_path / side), "--seconds", "60"],
                             cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for side in ("a", "b")]
    try:
        outputs = [run.communicate(timeout=180)[0] for run in runs]
        assert [run.returncode for run in runs] == [0, 0], outputs
        for side, text in zip("ab", outputs):
            assert "[online] paired with" in text and "[done] trade 1 complete" in text
            assert "[done] trade 1 complete: species 25 'Pikachu' for species 25 'Pikachu'" in text
            assert not (tmp_path / side).exists() or not list((tmp_path / side).iterdir())
    finally:
        for run in runs:
            if run.poll() is None:
                run.kill()
        stop()


def test_two_demo_sun_moon_consoles_trade_through_the_relays(tmp_path, quick):
    """The one-console test's other half, twice: a PokeUDS beside the demo radio's Sun and one beside its Moon, each
    online, meet through the relays; each demo console ends with the other's Rowlet, and neither PokeUDS writes."""
    from pokeuds.radio.demo import DEMO_KEY, DemoRadio

    hub = Hub(live=True)
    pa, pb = live_partners(hub, "gen7")
    radios, stations, trades = [], [], []
    for title, partner, mac in (("sun", pa, "02000000B731"), ("moon", pb, "02000000B732")):
        radio = DemoRadio(title)
        station = Station(radio, bytes.fromhex(mac), DEMO_KEY, name="POKEUDS")
        station.start(6)
        radios.append(radio)
        stations.append(station)
        trades.append(Gen7Trade(station, get("sm"), profile.load("sm"), b"", tmp_path / title, gen7_wire.Trainer("Ash"),
                                offers=online.Offers(partner)))
    try:
        run_all({"sun": lambda: trades[0].run("join", 30.0), "moon": lambda: trades[1].run("join", 30.0)})
        got = [r.console.trade.saved for r in radios]
        assert [len(g) for g in got] == [1, 1] and all("_722_Rowlet_" in g[0].name for g in got)
        assert [t.saved for t in trades] == [[], []]
    finally:
        hub.stopped.set()
        for p in (pa, pb):
            p.close()
        for s in stations:
            s.close()
        for r in radios:
            r.close()
