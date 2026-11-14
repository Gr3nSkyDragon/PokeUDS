"""The offer source (pokeuds.games.offers): a Gen 6 and a Gen 7 session driven by a source that behaves as an online
partner does, against a second engine standing in for the console: PokeUDS offers only after the console has and
says yes only after both have; a partner taking its offer back is a decline; a partner gone ends the session."""

import os
import threading
import time

import pytest

from pokeuds import pkx
from pokeuds.games import get, offers, profile
from pokeuds.games.gen6 import session as gen6_session
from pokeuds.games.gen6 import wire as gen6_wire
from pokeuds.games.gen6.trade import Gen6Trade
from pokeuds.games.gen7 import session as gen7_session
from pokeuds.games.gen7 import wire as gen7_wire
from pokeuds.radio.sim import Air, SimRadio
from pokeuds.uds import Station
from tests.test_pkx import make_record

COMM7, PASS7 = 0x00164810, b"GNgBwMcVotYs\x00"


class Partner:
    """An online partner's side of the source, scripted: its record arrives ``delay`` s after the console offered,
    its yes ``delay`` s after the console's; ``take_back`` times it takes its offer back instead of saying yes;
    ``leave_after`` trades it is gone."""

    offers_first = False

    def __init__(self, records, delay=0.3, take_back=0, leave_after=None):
        self.records, self.delay, self.take_back, self.leave_after = list(records), delay, take_back, leave_after
        self.done = 0
        self.offered_at = self.accepted_at = None
        self.log = []

    def _late(self, since):
        return since is not None and time.monotonic() - since >= self.delay

    def offer(self):
        return self.records[self.done % len(self.records)] if self._late(self.offered_at) else None

    def ended(self):
        if self.leave_after is not None and self.done >= self.leave_after and self.offered_at is not None:
            return "the partner left"
        return "every trade is done" if self.done >= len(self.records) else ""

    def console_offers(self, record):
        self.log.append(("offer", pkx.summary(record).species))
        self.offered_at = time.monotonic()

    def console_withdrew(self):
        self.log.append(("withdrew",))
        self.accepted_at = None

    def console_accepts(self):
        self.log.append(("accepts",))
        self.accepted_at = time.monotonic()

    def partner_accepts(self):
        if not self._late(self.accepted_at):
            return False
        if self.take_back:
            self.take_back -= 1
            self.offered_at = self.accepted_at = None
            self.log.append(("partner took it back",))
            return None
        return True

    def traded(self, record):
        self.log.append(("traded", pkx.summary(record).species))
        self.done += 1
        self.offered_at = self.accepted_at = None


@pytest.fixture(autouse=True)
def quick_syncs(monkeypatch):
    monkeypatch.setattr(gen6_session, "SYNC_ASK_AFTER", 0.05)
    monkeypatch.setattr(gen6_session, "SCREEN_AFTER", 0.05)


def test_the_queue_is_what_pokeuds_always_did():
    q = offers.Queue([b"a", b"b"], trades=3)
    assert q.offers_first and q.partner_accepts() is True
    seen = []
    while not q.ended():
        seen.append(q.offer())
        q.traded(b"x")
    assert seen == [b"a", b"b", b"a"] and q.ended()
    with pytest.raises(ValueError):
        offers.Queue([])


def run_both(*jobs, timeout=60):
    errors = []

    def wrap(job):
        try:
            job()
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(repr(exc))

    threads = [threading.Thread(target=wrap, args=(job,), daemon=True) for job in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
    assert not errors and not any(t.is_alive() for t in threads), errors


def gen6_pair(air, tmp_path, ours_source, console_offers, console_trades):
    key = os.urandom(16)

    def trade(mac, name, record, folder, channels, **kw):
        station = Station(SimRadio(air), bytes.fromhex(mac), key, name=name)
        station.start(channels[0])
        me = gen6_wire.Trainer(name, 1234, 5678, os.urandom(8), os.urandom(4), gen6_wire.VERSION_CODES["X"], name)
        return Gen6Trade(station, get("xy"), profile.load("xy"), record, folder, me, channels=channels, **kw)

    ours = trade("02000000A601", "POKEUDS", b"", tmp_path / "ours", (6,), offers=ours_source)
    console = trade("02000000A602", "Serena", console_offers[0], tmp_path / "console", (6,),
                    queue=console_offers[1:], trades=console_trades)
    console.queue_party = [gen6_session.party_record(o) for o in console.queue]
    return ours, console


@pytest.mark.parametrize("pokeuds_role", ["join", "host"])
def test_gen6_offers_after_the_console_and_confirms_after_the_partner(tmp_path, pokeuds_role):
    air = Air()
    partners = make_record(0x11111111, species=658, nickname="Froakie")
    console_offer = make_record(0x22222222, species=653, nickname="Fennekin")
    source = Partner([partners])
    ours, console = gen6_pair(air, tmp_path, source, [console_offer], 1)
    try:
        run_both(lambda: ours.run(pokeuds_role, 20.0),
                 lambda: console.run("host" if pokeuds_role == "join" else "join", 20.0))
        assert source.log == [("offer", 653), ("accepts",), ("traded", 653)]
        assert [p.read_bytes() for p in console.saved] == [partners[:pkx.STORED_SIZE]]
        assert [p.read_bytes() for p in ours.saved] == [console_offer[:pkx.STORED_SIZE]]
    finally:
        ours.station.close()
        console.station.close()
        air.close()


def test_gen6_partner_taking_its_offer_back_is_a_decline(tmp_path):
    air = Air()
    partners = make_record(0x11111111, species=658, nickname="Froakie")
    console_offer = make_record(0x22222222, species=653, nickname="Fennekin")
    source = Partner([partners], take_back=1)
    ours, console = gen6_pair(air, tmp_path, source, [console_offer], 1)
    try:
        run_both(lambda: ours.run("join", 20.0), lambda: console.run("host", 20.0))
        assert source.log == [("offer", 653), ("accepts",), ("partner took it back",), ("withdrew",),
                              ("offer", 653), ("accepts",), ("traded", 653)]
        assert len(console.saved) == len(ours.saved) == 1
    finally:
        ours.station.close()
        console.station.close()
        air.close()


def test_gen6_a_partner_gone_ends_the_session(tmp_path):
    """The console stands ready for two trades; the partner leaves after one, and PokeUDS leaves the trade screen."""
    air = Air()
    a, b = make_record(0x11111111, species=658, nickname="Froakie"), make_record(0x33333333, species=650,
                                                                                    nickname="Chespin")
    console_offer = make_record(0x22222222, species=653, nickname="Fennekin")
    source = Partner([a, b], leave_after=1)
    ours, console = gen6_pair(air, tmp_path, source, [console_offer, console_offer], 2)
    try:
        run_both(lambda: ours.run("join", 20.0), lambda: console.run("host", 20.0))
        assert len(console.saved) == len(ours.saved) == 1
        assert source.log[-1] == ("offer", 653)       # the console offered again; nobody was there to take it
    finally:
        ours.station.close()
        console.station.close()
        air.close()


def pk7(ec, species, name):
    return pkx.fix_checksum(make_record(ec, species=species, nickname=name, version=31))


@pytest.mark.parametrize("pokeuds_hosts", [True, False])
def test_gen7_offers_after_the_console_and_says_yes_after_the_partner(pokeuds_hosts, monkeypatch):
    # The engine standing in for the console waits up to SCREEN_WAIT for PokeUDS's screen before it offers, and an
    # online PokeUDS shows nothing until the console has offered. Whether a Sun/Moon waits so is unmeasured.
    monkeypatch.setattr(gen7_session, "SCREEN_WAIT", 0.5)
    air, key = Air(), os.urandom(16)
    host_st = Station(SimRadio(air), bytes.fromhex("02000000B701"), key, name="HOST")
    join_st = Station(SimRadio(air), bytes.fromhex("02000000B702"), key, name="JOIN")
    host_st.start(1)
    join_st.start(1)
    partners = [pk7(0x1111, 19, "Rattata"), pk7(0x3333, 755, "Morelull")]
    console_offer = pk7(0x2222, 773, "Silvally")
    source = Partner(partners, take_back=1)
    results = {}

    def session(station, name, host):
        if host == pokeuds_hosts:
            return gen7_session.Gen7Session(station, gen7_wire.Trainer(name), offers=source)
        return gen7_session.Gen7Session(station, gen7_wire.Trainer(name), console_offer, trades=2, answer_first=True)

    def host():
        host_st.host(wlan_comm_id=COMM7, passphrase=PASS7, channel=1, max_nodes=4, id_=1)
        deadline = time.monotonic() + 10
        while not any(p.connected for p in host_st.peers.values()):
            assert time.monotonic() < deadline, "nobody joined"
            time.sleep(0.02)
        results["host"] = session(host_st, "Hosty", True).run()

    def join():
        found = join_st.scan(10, until=lambda b: b.info.wlan_comm_id == COMM7)
        join_st.connect(next(b for b in found if b.info.wlan_comm_id == COMM7), PASS7)
        results["join"] = session(join_st, "Joiny", False).run()

    try:
        run_both(host, join, timeout=90)
        ours, consoles = (results["host"], results["join"]) if pokeuds_hosts else (results["join"], results["host"])
        assert ours == [console_offer, console_offer]
        assert consoles == partners
        assert source.log == [("offer", 773), ("accepts",), ("partner took it back",), ("withdrew",),
                              ("offer", 773), ("accepts",), ("traded", 773),
                              ("offer", 773), ("accepts",), ("traded", 773)]
    finally:
        host_st.close()
        join_st.close()
        air.close()
