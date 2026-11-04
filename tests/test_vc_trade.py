"""A whole VC trade over the simulated air: UDS join, Pia session, the Game Boy exchange, saved files."""

import os
import threading
import time

import pytest

from pokeuds import gbpk
from pokeuds.games import vc as titles
from pokeuds.radio.sim import Air, SimRadio
from pokeuds.uds import BROADCAST_NODE, Station
from pokeuds.vc import link, pia
from pokeuds.vc.session import Config, Role, Session, State
from pokeuds.vc.trade import VcTrade, choose_mode
from tests.test_vc_link import gen1_mon, gen2_mon, party
from tests.vc_model import Model3DS


class SimulatedVcHost:
    """A retail 3DS hosting from the VC link menu: UDS host, Pia host session, the model game."""

    def __init__(self, air, key, title, model):
        self.station = Station(SimRadio(air), bytes.fromhex("02000000AA01"), key, name="RETAIL")
        self.station.start(titles.CHANNEL)
        self.appdata = b"\x08" + os.urandom(7) + b"\x01" + bytes(7)
        self.station.host(wlan_comm_id=title.comm_id, passphrase=titles.PASSPHRASE, channel=titles.CHANNEL,
                          max_nodes=2, application_data=self.appdata)
        self.model = model
        self.session = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop.is_set() and not any(p.connected for p in self.station.peers.values()):
            time.sleep(0.01)
        send = lambda raw, bc: self.station.send(BROADCAST_NODE if bc else 2, pia.UDS_CHANNEL, raw)
        self.session = s = Session(Role.HOST, send, Config(name="RETAIL", application_data=self.appdata))
        t0 = time.monotonic()
        now = lambda: int((time.monotonic() - t0) * 1000)
        s.start(now())
        gen = self.model.run()
        first = False
        while not self.stop.is_set():
            packet = self.station.recv(pia.UDS_CHANNEL, timeout=0.005)
            while packet is not None:
                s.receive(now(), packet.payload)
                packet = self.station.recv(pia.UDS_CHANNEL, timeout=0)
            s.poll(now())
            if s.state is State.JOINED and not first:
                first = s.queue_unit(next(gen))          # $EF
            for byte in s.pop_units():
                if not self.model.done:
                    s.queue_unit(gen.send(byte))
            s.flush(now())

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        self.station.close()


@pytest.fixture
def air():
    a = Air()
    yield a
    a.close()


def trade_over_air(air, tmp_path, title, offer, console_party, mode):
    key = os.urandom(16)
    console = SimulatedVcHost(air, key, title, Model3DS(mode, console_party, console_generation=title.generation))
    station = Station(SimRadio(air), bytes.fromhex("02000000BB02"), key, name="POKEUDS")
    station.start(1)
    trade = VcTrade(station, offer, tmp_path)
    try:
        trade.join(seconds=3.0)
        assert trade.mode is mode
        runner = threading.Thread(target=trade.run, kwargs={"seconds": 20.0}, daemon=True)
        runner.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not console.model.done:
            time.sleep(0.05)
        assert console.model.done, f"engine stuck in {trade.engine.phase()}"
        time.sleep(0.2)
        return trade, console
    finally:
        station.close()
        console.close()


def test_gen1_trade_over_the_air(air, tmp_path):
    mine, theirs = gen1_mon(dex=25, nick="SPARKY"), gen1_mon(dex=150, nick="MEWTWO", ot="RED", ot_id=1)
    trade, console = trade_over_air(air, tmp_path, titles.TITLES["red"], mine, party(theirs), link.GEN1)
    assert console.model.received == [mine]
    (saved,) = trade.saved
    assert saved.suffix == ".pk1" and "_rby_trade1_150_MEWTWO_00001" in saved.name
    assert gbpk.Record.from_file(saved.read_bytes()) == theirs


def test_gen2_trade_over_the_air(air, tmp_path):
    mine, theirs = gen2_mon(dex=155), gen2_mon(dex=249, nick="LUGIA", ot="SILVER", ot_id=2)
    trade, console = trade_over_air(air, tmp_path, titles.TITLES["silver"], mine, party(theirs), link.GEN2)
    assert console.model.received == [mine]
    assert gbpk.Record.from_file(trade.saved[0].read_bytes()) == theirs


def test_time_capsule_gen2_offer_to_red(air, tmp_path):
    mine = gen2_mon(dex=25, nick="PIKA", item=0x92)
    theirs = gen1_mon(dex=133, nick="EEVEE", catch_rate=0x19)
    trade, console = trade_over_air(air, tmp_path, titles.TITLES["red"], mine, party(theirs), link.TIME_CAPSULE)
    assert console.model.received == [gbpk.gen2_to_gen1(mine)]
    pk1, pk2 = trade.saved
    assert gbpk.Record.from_file(pk1.read_bytes()) == theirs
    as_gen2 = gbpk.Record.from_file(pk2.read_bytes())
    assert as_gen2.generation == 2 and as_gen2.dex == 133 and as_gen2.held_item == 0x92   # catch rate $19 -> Leftovers


def test_time_capsule_gen1_offer_to_gold(air, tmp_path):
    mine, theirs = gen1_mon(dex=1), gen1_mon(dex=4, nick="CHARMY")
    trade, console = trade_over_air(air, tmp_path, titles.TITLES["gold"], mine, party(theirs), link.TIME_CAPSULE)
    assert console.model.received == [mine]
    assert gbpk.Record.from_file(trade.saved[0].read_bytes()) == theirs


def test_mode_choice():
    assert choose_mode(gen1_mon(), 1) is link.GEN1
    assert choose_mode(gen2_mon(), 2) is link.GEN2
    assert choose_mode(gen1_mon(), 2) is choose_mode(gen2_mon(), 1) is link.TIME_CAPSULE
    assert titles.generation_of(0x00171010) == 1 and titles.generation_of(0x00172710) == 2
    assert titles.TITLES["gold"].comm_id == 0x00172610 and titles.title_of(0x00171010).key == "red"


class SimulatedVcJoiner:
    """A retail 3DS joining from the VC link menu: UDS client, Pia joiner session, the model game."""

    def __init__(self, air, key, model):
        self.station = Station(SimRadio(air), bytes.fromhex("02000000CC03"), key, name="RETAIL")
        self.station.start(1)
        self.model = model
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        target = self.station.scan(5.0, until=lambda b: titles.generation_of(b.info.wlan_comm_id) is not None)
        self.station.connect(target[0], titles.PASSPHRASE)
        send = lambda raw, bc: self.station.send(BROADCAST_NODE if bc else 1, pia.UDS_CHANNEL, raw)
        s = Session(Role.JOINER, send, Config(name="RETAIL", application_data=target[0].info.application_data))
        t0 = time.monotonic()
        now = lambda: int((time.monotonic() - t0) * 1000)
        s.start(now())
        gen, started = self.model.run(), False
        while not self.stop.is_set():
            packet = self.station.recv(pia.UDS_CHANNEL, timeout=0.005)
            while packet is not None:
                s.receive(now(), packet.payload)
                packet = self.station.recv(pia.UDS_CHANNEL, timeout=0)
            s.poll(now())
            for byte in s.pop_units():                   # the host's unit k is the pair of our unit k
                if not started:
                    s.queue_unit(next(gen))
                    started = True
                if not self.model.done:
                    s.queue_unit(gen.send(byte))
            s.flush(now())

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        self.station.close()


@pytest.mark.parametrize("title_key,offer_gen", [("red", 1), ("gold", 2)])
def test_pokeuds_hosting(air, tmp_path, title_key, offer_gen):
    """Host mode (experimental on hardware) against the model as a joining 3DS."""
    key = os.urandom(16)
    title = titles.TITLES[title_key]
    mode = link.GEN1 if offer_gen == 1 else link.GEN2
    mine = gen1_mon(dex=7) if offer_gen == 1 else gen2_mon(dex=158)
    theirs = gen1_mon(dex=1, nick="BULBA") if offer_gen == 1 else gen2_mon(dex=152, nick="CHIKO")
    station = Station(SimRadio(air), bytes.fromhex("02000000DD04"), key, name="POKEUDS")
    station.start(titles.CHANNEL)
    trade = VcTrade(station, mine, tmp_path)
    console = SimulatedVcJoiner(air, key, Model3DS(mode, party(theirs), role="joiner"))
    try:
        trade.host(title, partner_generation=offer_gen, seconds=10.0)
        runner = threading.Thread(target=trade.run, kwargs={"seconds": 20.0}, daemon=True)
        runner.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not console.model.done:
            time.sleep(0.05)
        assert console.model.done, f"engine stuck in {trade.engine.phase()}"
        time.sleep(0.2)
        assert console.model.received == [mine]
        assert gbpk.Record.from_file(trade.saved[0].read_bytes()) == theirs
    finally:
        console.close()
        station.close()
