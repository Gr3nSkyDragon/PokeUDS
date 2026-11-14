"""Virtual Console trades with a player far away (pokeuds.vc.online): two modelled consoles, each answered by its own
online engine, the two engines joined by an in-memory partner; then the same over the simulated air and the relays,
and from the command line."""

import os
import threading
import time

import pytest

from pokeuds import gbpk
from pokeuds.radio.demo import sample_mon
from pokeuds.vc import link, online
from pokeuds.vc.model import Driver, Model3DS


class Pair:
    """Two remotes that see each other's sends, round by round."""

    def __init__(self):
        self.sides = [self.Side(self, 0), self.Side(self, 1)]

    class Side:
        def __init__(self, pair, index):
            self.pair, self.index = pair, index
            self.box, self.round, self.mode, self.refused, self.left = {}, 0, None, [], ""

        @property
        def other(self):
            return self.pair.sides[1 - self.index]

        def begin(self, mode):
            self.mode = mode

        def send(self, key, data):
            self.box[(self.round, key)] = bytes(data)

        def get(self, key):
            return self.other.box.get((self.round, key))

        def next_round(self):
            self.round += 1

        def refuse(self, reason):
            self.refused.append(reason)

        def gone(self):
            return self.left


class Desk:
    """A modelled 3DS and the online engine answering it; the 3DS bursts its blocks, as a real one does."""

    def __init__(self, model, generation, remote, family="intl"):
        self.model, self.driver = model, Driver(model)
        self.engine = online.OnlineEngine(generation, remote, family=family)
        self.inbox = self.driver.start()

    def pump(self, units: int = 64) -> bool:
        """Answer up to ``units`` of the console's units (a console waiting on a choice asks again and again)."""
        moved = False
        while self.inbox and not self.model.done and units:
            units -= 1
            reply = self.engine.respond(self.inbox[0], self.inbox[1:])
            if reply is None:
                break
            self.inbox.pop(0)
            self.inbox += self.driver.reply(reply)
            moved = True
        return moved


def run_desks(*desks, limit=200000):
    for _ in range(limit):
        if all(d.model.done for d in desks):
            return
        moved = [d.pump() for d in desks]          # every desk, every turn
        if not any(moved) and not all(d.model.done for d in desks):
            raise AssertionError(f"stuck: {[(d.engine.phase(), d.engine.waiting) for d in desks]}")
    raise AssertionError("no end")


def party(generation, *mons, name="ASH", trainer_id=1111, mail=()):
    return link.Party(gbpk.encode_name(name), trainer_id, list(mons), list(mail))


def test_two_gen1_consoles_trade_through_their_engines():
    pair = Pair()
    a_mon, b_mon = sample_mon(1, 25, 12, "SPARKY"), sample_mon(1, 7, 9, "SHELLY")
    a = Desk(Model3DS(link.GEN1, party(1, a_mon, sample_mon(1, 16, 5, "PIDGE")), burst=True), 1, pair.sides[0])
    b = Desk(Model3DS(link.GEN1, party(1, b_mon, name="MISTY"), burst=True, role="joiner"), 1, pair.sides[1])
    run_desks(a, b)
    assert a.model.received == [b_mon] and b.model.received == [a_mon]
    assert [t.sent for t in a.engine.trades] == [a_mon] and [t.received for t in a.engine.trades] == [b_mon]
    assert pair.sides[0].mode is link.GEN1 and online.room_name(link.GEN1, "intl") == "gb1"
    # Each console saw the other's real party, its trainer name included.
    assert gbpk.decode_name(a.model.partner_parties[0].trainer_name) == "MISTY"
    assert a.model.partner_parties[1].mons[-1] == a_mon      # the second round: Misty's party holds Sparky now


def test_two_gen2_consoles_trade_and_mail_goes_with_it():
    pair = Pair()
    letter = bytes(range(0x80, 0x80 + 33)) + gbpk.encode_name("MISTY")[:10] + b"\x12\x34" + bytes([7, 0xA0])
    a_mon = sample_mon(2, 155, 10, "CYNDA")
    b_mon = gbpk.Record(2, bytes([7, 0xA0]) + sample_mon(2, 7, 10, "SQUIRT").data[2:], gbpk.encode_name("MISTY"),
                        gbpk.encode_name("SQUIRT"))      # holds a Flower Mail
    a = Desk(Model3DS(link.GEN2, party(2, a_mon), burst=True), 2, pair.sides[0])
    b = Desk(Model3DS(link.GEN2, party(2, b_mon, name="MISTY", mail=[letter]), burst=True, role="joiner"), 2,
             pair.sides[1])
    run_desks(a, b)
    assert a.model.received == [b_mon] and b.model.received == [a_mon]
    assert a.model.partner_mail[0][0] == letter                          # Misty's letter reached Ash's console
    assert a.engine.trades[0].received_mail == letter and pair.sides[0].mode is link.GEN2


def test_a_gen2_console_in_the_time_capsule_meets_a_gen1_console():
    pair = Pair()
    a_mon, b_mon = sample_mon(1, 25, 12, "SPARKY"), sample_mon(1, 129, 5, "MAGI")
    a = Desk(Model3DS(link.GEN1, party(1, a_mon), burst=True), 1, pair.sides[0])
    b = Desk(Model3DS(link.TIME_CAPSULE, party(1, b_mon, name="GOLD"), console_generation=2, burst=True,
                      room_byte=0xD0, role="joiner"), 2, pair.sides[1])
    run_desks(a, b)
    assert pair.sides[1].mode is link.TIME_CAPSULE and online.room_name(link.TIME_CAPSULE, "intl") == "gb1"
    assert a.model.received == [b_mon] and b.model.received == [a_mon]


def test_a_decline_on_either_side_trades_nothing():
    pair = Pair()
    a = Desk(Model3DS(link.GEN1, party(1, sample_mon(1, 25, 12, "SPARKY")), burst=True), 1, pair.sides[0])
    b = Desk(Model3DS(link.GEN1, party(1, sample_mon(1, 7, 9, "SHELLY")), burst=True, accept=False,
                      role="joiner"), 1, pair.sides[1])
    for _ in range(20000):
        a.pump()
        b.pump()
        if b.model.done:
            break
    assert b.model.done and not a.engine.trades and not b.engine.trades
    assert any("declined" in line for line in a.engine.log)


def test_the_block_is_held_until_the_partner_has_sent_theirs_and_bad_blocks_are_refused():
    pair = Pair()
    a = Desk(Model3DS(link.GEN1, party(1, sample_mon(1, 25, 12, "SPARKY")), burst=True), 1, pair.sides[0])
    for _ in range(50):
        a.pump()
    assert a.engine.phase() == "wait-block" and "partner's party block" in a.engine.waiting
    assert (0, "party") in pair.sides[0].box                 # its own party went to the partner meanwhile
    bad = bytearray(pair.sides[0].box[(0, "party")])
    bad[6 + 11 + 1 + 7] = 0                                 # the first Pokemon's species index: none
    pair.sides[1].box[(0, "party")] = bytes(bad)
    a.pump()
    assert pair.sides[0].refused and "species" in pair.sides[0].refused[0] and a.engine.phase() == "wait-block"


def test_a_partner_gone_in_the_trade_menu_is_a_cancel():
    pair = Pair()
    a = Desk(Model3DS(link.GEN1, party(1, sample_mon(1, 25, 12, "SPARKY")), burst=True), 1, pair.sides[0])
    b = Desk(Model3DS(link.GEN1, party(1, sample_mon(1, 7, 9, "SHELLY")), burst=True, role="joiner"), 1,
             pair.sides[1])
    for _ in range(20000):
        a.pump()
        if a.engine.phase() == "select/pick" and a.engine.waiting:
            break
        b.pump()
    assert a.engine.waiting == "waiting for the partner to choose"
    pair.sides[0].left = "the partner left"
    for _ in range(2000):
        if a.model.done:
            break
        a.pump()
    assert a.model.done and not a.model.received                # Cancel: the console leaves the trade screen


def test_party_checks():
    good = party(1, sample_mon(1, 25, 12, "SPARKY"))
    assert online.sane_party(good, 1) == ""
    high = sample_mon(1, 25, 12, "SPARKY")
    high = gbpk.Record(1, high.data[:8] + bytes([200]) + high.data[9:], high.ot_name, high.nickname)
    assert "move" in online.sane_party(party(1, high), 1)
    endless = gbpk.Record(1, good.mons[0].data, good.mons[0].ot_name, b"\x80" * 11)
    assert "name" in online.sane_party(party(1, endless), 1)
    assert online.mode_for(1, 0xD0) is link.GEN1 and online.mode_for(2, 0xD1) is link.GEN2
    assert online.mode_for(2, 0xD0) is online.mode_for(2, 0xD4) is link.TIME_CAPSULE
    assert online.shared_sizes(link.GEN2, "intl") == {"party": 450, "patch": 200, "pick": 2, "confirm": 2,
                                                      "mail": link.LAYOUTS["intl"].mail_body}
    assert online.room_name(link.GEN2, "jp") == "gb2-jp"


# -- over the simulated air and the relays ---------------------------------------------------------

def test_two_vc_consoles_trade_over_the_air_and_the_relays(tmp_path):
    """Two desks, each a modelled 3DS hosting from the VC link menu and a PokeUDS joining it online; the two PokeUDS
    meet through an in-memory relay. Red meets Blue in gb1; each console ends with the other's Pokemon."""
    from pokeuds.games import vc as titles
    from pokeuds.online import link as partners
    from pokeuds.radio.demo import SimulatedVcHost
    from pokeuds.radio.sim import Air, SimRadio
    from pokeuds.uds import Station
    from pokeuds.vc.trade import VcTrade
    from tests.test_online_trade import Hub

    hub = Hub(live=True)
    made, desks = [], []
    for side, title_key, mon in (("a", "red", sample_mon(1, 25, 12, "SPARKY")),
                                 ("b", "blue", sample_mon(1, 7, 9, "SHELLY"))):
        air, key = Air(), os.urandom(16)
        title = titles.TITLES[title_key]
        console = SimulatedVcHost(air, key, title, Model3DS(link.GEN1, party(1, mon, name=side.upper()), burst=True))
        station = Station(SimRadio(air), bytes.fromhex("02000000BB0" + ("2" if side == "a" else "3")), key,
                          name="POKEUDS")
        station.start(1)

        def partner(room, sizes, side=side):
            p = partners.Partner(room, "desk", name=f"P{side}", pool_factory=hub.pool, shared_sizes=sizes)
            p.start()
            made.append(p)
            return p

        trade = VcTrade(station, None, tmp_path / side, online=online.PartnerRemote(partner_factory=partner))
        desks.append((air, console, station, trade, mon))
    try:
        for _, _, _, trade, _ in desks:
            trade.join(seconds=3.0)
        runners = [threading.Thread(target=d[3].run, kwargs={"seconds": 40.0}, daemon=True) for d in desks]
        for r in runners:
            r.start()
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and not all(d[1].model.done for d in desks):
            time.sleep(0.05)
        (_, ca, _, ta, mon_a), (_, cb, _, tb, mon_b) = desks
        assert ca.model.done and cb.model.done, [(d[3].engine.phase(), d[3].engine.waiting) for d in desks]
        assert ca.model.received == [mon_b] and cb.model.received == [mon_a]
        assert [p.room_name for p in made] == ["gb1", "gb1"]
        assert not list((tmp_path / "a").glob("*")) if (tmp_path / "a").exists() else True   # nothing written
    finally:
        hub.stopped.set()
        for p in made:
            p.close()
        for air, console, station, _, _ in desks:
            station.close()
            console.close()
            air.close()


def test_two_command_line_runs_trade_vc_online_through_a_relay(tmp_path):
    """`pokeuds vc trade --online-code desk` beside the demo radio's Red, and again beside its Blue, through a loopback
    relay: both trade, write nothing, and end when the demo consoles leave."""
    import subprocess
    import sys
    from pathlib import Path

    from tests.fake_relay import Store, serve

    root = Path(__file__).resolve().parents[1]
    url, stop = serve(Store())
    env = dict(os.environ, POKEUDS_RELAYS=url, POKEUDS_DATA=str(tmp_path / "data"))
    runs = [subprocess.Popen([sys.executable, "-m", "pokeuds", "vc", "trade", "--radio", f"demo:{title}",
                              "--online-code", "desk", "--output", str(tmp_path / title), "--run-seconds", "60"],
                             cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for title in ("red", "blue")]
    try:
        outputs = [run.communicate(timeout=120)[0] for run in runs]
        assert [run.returncode for run in runs] == [0, 0], outputs
        for text in outputs:
            assert "[online] looking for a partner: gb1, code desk" in text and "[done] trade 1 complete" in text
            assert "0 file(s) written" in text
    finally:
        for run in runs:
            if run.poll() is None:
                run.kill()
        stop()


@pytest.mark.parametrize("argv,why", [
    (["vc", "trade", "--radio", "demo:red"], "--offer is needed"),
    (["vc", "trade", "--radio", "demo:red", "--online", "--offer", "a.pk1"], "leave out --offer"),
])
def test_the_vc_command_line_says_what_an_online_trade_needs(argv, why, tmp_path, monkeypatch):
    from pokeuds import cli

    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path))
    with pytest.raises(SystemExit) as stop:
        cli.main(argv)
    assert why in str(stop.value)
