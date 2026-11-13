"""A whole VC trade over the simulated air: UDS join, Pia session, the Game Boy exchange, saved files."""

import os
import threading
import time

import pytest

from pokeuds import gbpk
from pokeuds.games import vc as titles
from pokeuds.radio.sim import Air, SimRadio
from pokeuds.uds import BROADCAST_NODE, Station
from pokeuds.vc import gbtext, link, pia
from pokeuds.vc.session import Config, Role, Session, State
from pokeuds.vc.trade import VcTrade, choose_mode
from tests.test_vc_link import gen1_mon, gen2_mon, party
from pokeuds.radio.demo import SimulatedVcHost
from pokeuds.vc.model import Model3DS


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
    assert saved.suffix == ".pk1" and "_rby-en_trade1_150_MEWTWO_00001" in saved.name   # tagged with the title's language
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
    assert titles.generation_of(0x00171010) == 1 and titles.generation_of(0x00172610) == 2
    assert titles.TITLES["gold"].comm_id == 0x00172610 and titles.title_of(0x00171010).key == "red"


def test_every_vc_title_is_in_the_table():
    """The 39 titles; a title is matched by the table, never by a mask. Blue shares Red's comm id and Silver
    Gold's (retail English Blue and Silver hosts, 2026-11-13); every other title has its own."""
    assert len(titles.TITLES) == 39 and len({t.comm_id for t in titles.TITLES.values()}) == 39 - 6 - 7
    by_language = {}
    for t in titles.TITLES.values():
        by_language.setdefault(t.language, []).append(t.key)
        pair = {"blue": "red", "silver": "gold"}.get(t.key.split("-")[0])
        first = titles.TITLES[titles.title_key(pair, t.language)] if pair else t
        assert titles.title_of(t.comm_id) is first and titles.generation_of(t.comm_id) == t.generation
        assert t.comm_id == (first.title_id & 0xFFFFFFFF) + 0x10 and t.title_id >> 32 == 0x00040000
    assert {k: len(v) for k, v in by_language.items()} == {"jp": 7, "en": 6, "de": 6, "fr": 6, "es": 6, "it": 6, "kr": 2}
    assert sorted(by_language["kr"]) == ["gold-kr", "silver-kr"] and "green-jp" in by_language["jp"]
    assert [t.key for t in titles.TITLES.values() if t.measured] == ["red", "blue", "gold", "silver"]
    assert titles.TITLES["blue"].comm_id == 0x00171010 and titles.TITLES["silver"].comm_id == 0x00172610
    # What the family mask got wrong (comm ids from the title list):
    gold_jp, crystal_es, gold_kr, red_jp = (titles.title_of(i) for i in (0x00172310, 0x00173110, 0x00173510, 0x00170C10))
    assert gold_jp.key == "gold-jp" and gold_jp.family == "jp"            # was joined as international
    assert crystal_es.key == "crystal-es" and crystal_es.generation == 2  # 00173xxx: was missed
    assert gold_kr.family == "kr"                                          # was missed
    assert red_jp.generation == 1 and red_jp.family == "jp"                # 00170xxx: was ignored
    assert titles.title_of(0x00172010) is None and titles.generation_of(0x00171F10) is None    # in a family, not a title
    assert all(t.supported for t in titles.TITLES.values()) and set(titles.HOSTABLE) == set(titles.TITLES)


def test_a_german_host_is_joined_and_its_language_kept(air, tmp_path):
    mine, theirs = gen2_mon(dex=155, nick="CYNDA"), gen2_mon(dex=158, nick="KARNIMANI", ot="GOLD", ot_id=7)
    trade, console = trade_over_air(air, tmp_path, titles.TITLES["gold-de"], mine, party(theirs), link.GEN2)
    assert trade.partner_title.key == "gold-de" and console.model.received == [mine]
    assert "_gsc-de_trade1_158_" in trade.saved[0].name


def family_mon(generation, family, dex, nick, ot="LINK", ot_id=4321):
    """A record as a game of ``family`` holds it."""
    base = gen1_mon(dex=dex) if generation == 1 else gen2_mon(dex=dex)
    return gbpk.Record(generation, base.data, gbtext.encode(ot, family), gbtext.encode(nick, family), family)


def console_trade(air, tmp_path, key, offer, theirs, *, mail=(), languages=(), offer_mail=b""):
    title = titles.TITLES[key]
    mode = choose_mode(offer, title.generation)
    uds_key = os.urandom(16)
    family = title.family
    trainer = gbtext.trainer_name("RED" if family != "jp" else "レッド", title.language)
    model = Model3DS(mode, link.Party(trainer, 0x1234, [theirs], list(mail)), console_generation=title.generation,
                     family=family)
    console = SimulatedVcHost(air, uds_key, title, model)
    station = Station(SimRadio(air), bytes.fromhex("02000000BB02"), uds_key, name="POKEUDS")
    station.start(1)
    trade = VcTrade(station, offer, tmp_path, languages=list(languages), offer_mail=offer_mail)
    try:
        trade.join(seconds=3.0)
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


@pytest.mark.parametrize("key,generation", [("green-jp", 1), ("gold-jp", 2), ("crystal-jp", 2)])
def test_a_japanese_console_trades_in_its_own_layout(air, tmp_path, key, generation):
    """Japanese blocks: 6-byte names, so 359- and 385-byte party blocks. An English Pokemon goes over with its
    species' Japanese name (and a nickname the kana cannot show as ?), and what comes back is a Japanese file."""
    pikachu = gen1_mon(dex=25, nick="PIKACHU") if generation == 1 else gen2_mon(dex=25, nick="PIKACHU")
    theirs = family_mon(generation, "jp", 150 if generation == 1 else 249, "ミュウツー" if generation == 1 else "ルギア",
                        ot="サトシ", ot_id=1)
    trade, console = console_trade(air, tmp_path, key, pikachu, theirs)
    assert trade.engine.family == "jp" and trade.partner_title.key == key
    (got,) = console.model.received
    assert got.family == "jp" and got.data == pikachu.data and got.nickname_text() == "ピカチュウ"
    assert got.ot_text() == "?" * len(pikachu.ot_text())             # no Latin letters in Japanese games
    assert gbpk.decode_name(console.model.partner_parties[0].trainer_name, "jp") == "トレーナー"
    (saved,) = trade.saved
    assert f"_{'rby' if generation == 1 else 'gsc'}-jp_trade1_" in saved.name
    assert len(saved.read_bytes()) == (gbpk.PK1_SIZE_JP if generation == 1 else gbpk.PK2_SIZE_JP)
    back = gbpk.Record.from_file(saved.read_bytes())
    assert back == theirs and back.nickname_text() in ("ミュウツー", "ルギア") and back.ot_text() == "サトシ"


def test_a_korean_console_trades_and_its_mail_comes_and_goes(air, tmp_path):
    """Korean blocks: international sizes holding Hangul, and 65-byte mail messages after a $52 run."""
    mine = gen2_mon(dex=152, nick="LEAFY", item=0x9E)          # Flower Mail
    mail = bytes(0x80 + i % 26 for i in range(0x40)) + b"\xfe" + gbtext.encode("GOLD", "kr", 10) + b"\x00\x07\x98\x9e"
    theirs = family_mon(2, "kr", 25, "피카츄", ot="골드", ot_id=3)
    theirs = gbpk.Record(2, bytes([25, 0x9F]) + theirs.data[2:], theirs.ot_name, theirs.nickname, "kr")
    their_mail = bytes([0x80 + i % 26 for i in range(0x41)]) + bytes(14)
    trade, console = console_trade(air, tmp_path, "silver-kr", mine, theirs, mail=[their_mail],
                                   offer_mail=mail[:link.LAYOUTS["kr"].mail], languages=["kr"])
    assert trade.engine.family == "kr" and trade.engine.layout.mail_body == 577
    (got,) = console.model.received
    assert got.family == "kr" and got.nickname_text() == "LEAFY"            # Korean games show Latin letters
    assert console.model.partner_mail[0][0] == mail                      # a $FE in a message crosses as $21 and back
    pk2, mail_file = trade.saved
    assert "_gsc-kr_trade1_025_" in pk2.name and len(pk2.read_bytes()) == gbpk.PK2_SIZE
    assert gbpk.Record.from_file(pk2.read_bytes(), "kr").nickname_text() == "피카츄"
    assert mail_file.read_bytes() == their_mail


def test_a_japanese_pokemon_meets_an_english_console(air, tmp_path):
    """The other way: a Japanese record (its size says so) to Red. Its species name becomes the English one; a
    nickname of kana, which international games cannot show, becomes ?s; mail stays behind."""
    jp = family_mon(2, "jp", 155, "ヒノアラシ", ot="サトシ")
    nicked = family_mon(2, "jp", 25, "ピカ", ot="サトシ")
    theirs = gen2_mon(dex=133, nick="EEVEE")
    trade, console = console_trade(air, tmp_path, "gold", jp, theirs)
    (got,) = console.model.received
    assert got.family == "intl" and got.nickname_text() == "CYNDAQUIL" and got.ot_text() == "???"
    assert nicked.in_language("jp", "de").nickname_text() == "??"
    assert nicked.in_language("jp", "jp") is nicked


def test_korean_games_have_no_time_capsule(air, tmp_path):
    from pokeuds.vc.trade import UnsupportedTitle

    title = titles.TITLES["gold-kr"]
    uds_key = os.urandom(16)
    theirs = family_mon(2, "kr", 25, "피카츄")
    console = SimulatedVcHost(air, uds_key, title, Model3DS(link.GEN2, link.Party(gbtext.trainer_name("A", "kr"), 1,
                                                                                [theirs]), family="kr"))
    station = Station(SimRadio(air), bytes.fromhex("02000000BB02"), uds_key, name="POKEUDS")
    station.start(1)
    trade = VcTrade(station, gen1_mon(), tmp_path)
    try:
        started = time.monotonic()
        with pytest.raises(UnsupportedTitle, match="no Generation 1 game"):
            trade.join(seconds=30.0)
        assert time.monotonic() - started < 15 and trade.session is None
        with pytest.raises(ValueError, match="no Generation 1 game"):
            trade.host(title, 2, seconds=0.1)
    finally:
        station.close()
        console.close()


class SimulatedVcJoiner:
    """A retail 3DS joining from the VC link menu: UDS client, Pia joiner session, the model game."""

    def __init__(self, air, key, model):
        self.station = Station(SimRadio(air), bytes.fromhex("02000000CC03"), key, name="RETAIL")
        self.station.start(1)
        self.model = model
        self.heard = None                                # the host network it joined
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        target = self.station.scan(5.0, until=lambda b: titles.generation_of(b.info.wlan_comm_id) is not None)
        self.heard = target[0]
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
def test_pokeuds_hosting(air, tmp_path, title_key, offer_gen, monkeypatch):
    """Host mode against the model as a joining 3DS."""
    key = os.urandom(16)
    title = titles.TITLES[title_key]
    mode = link.GEN1 if offer_gen == 1 else link.GEN2
    mine = gen1_mon(dex=7) if offer_gen == 1 else gen2_mon(dex=158)
    theirs = gen1_mon(dex=1, nick="BULBA") if offer_gen == 1 else gen2_mon(dex=152, nick="CHIKO")
    station = Station(SimRadio(air), bytes.fromhex("02000000DD04"), key, name="POKEUDS")
    station.start(titles.CHANNEL)
    sent = []
    real_send = station.send

    def send(dest_node, channel, payload, *, broadcast=False):
        sent.append((dest_node, broadcast, payload))
        real_send(dest_node, channel, payload, broadcast=broadcast)

    monkeypatch.setattr(station, "send", send)
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
        # libctru's default attributes, as every retail network carries: with 0 no console joined PokeUDS's host
        assert console.heard.info.attributes == titles.NETWORK_ATTRIBUTES == 0x8000
        # id 1 and 8 bytes, 01, zeros, as retail Blue and Silver hosts: with id 0 no console joined
        assert console.heard.info.id == titles.NETWORK_ID == 1
        data = console.heard.info.application_data
        assert len(data) == 16 and data[8:] == b"\x01" + bytes(7)
        # As a retail VC host (sniff of two retail consoles): every frame a broadcast on the air, and the setup
        # messages (station info, profile, mesh state) for destination 0; a retail joiner ignored them sent to 2.
        assert sent and all(broadcast for _, broadcast, _ in sent)
        kinds = ((pia.PROTO_SETUP, 62), (pia.PROTO_SETUP, 80), (pia.PROTO_SYSTEM, 128))
        setup = [m for _, _, raw in sent if (f := pia.parse(raw)) and f.kind == pia.KIND_PIA for m in f.messages
                 if (m.protocol, len(m.payload)) in kinds]
        assert {(m.protocol, len(m.payload)) for m in setup} == set(kinds)
        assert all(m.destination == pia.ID_BROADCAST for m in setup)
    finally:
        console.close()
        station.close()

