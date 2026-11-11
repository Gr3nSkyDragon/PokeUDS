"""The GTS: two apps, each with its own bank, trading through one relay store (tests/fake_relay.py)."""

import hashlib
import struct
import time

import pytest

from pokeuds import pkx
from pokeuds.app import bank, gts
from pokeuds.online import gts as wire
from pokeuds.online import relays, schnorr
from pokeuds.radio.demo import sample_mon
from tests.fake_relay import FakePool, Store
from tests.test_pkx import make_record

DAY = 86400


def pk6(species=25, nickname="Sparky", exp=27000, ec=0x1234_5678):
    data = bytearray(make_record(ec, species, nickname=nickname, size=pkx.STORED_SIZE))
    struct.pack_into("<I", data, 0x10, exp)
    return pkx.fix_checksum(bytes(data))


def legal(fmt, data):
    return True, "Legal!", {}


def illegal(fmt, data):
    return False, "Invalid: Encounter type mismatch.\nValid: something else", {}


class World:
    """A relay store, a clock, and as many apps as the test asks for."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.now = [float(int(time.time()))]
        self.store = Store(clock=lambda: self.now[0])
        self.pool = FakePool(self.store)

    def app(self, name, check=legal):
        return gts.Gts(bank.Bank(self.tmp / name / "bank"), self.tmp / name / "gts", check=check,
                       clock=lambda: self.now[0])

    def later(self, seconds):
        self.now[0] += seconds


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def put(app, species=25, nickname="Sparky", ec=0x1234_5678, **extra):
    entry, _ = app.bank.deposit(pk6(species, nickname, ec=ec), "pk6", source="imported", **extra)
    return entry


EEVEE = wire.Want(133, "Eevee")


def held(app):
    return sorted(e.species for e in app.bank.entries())


def test_a_listing_is_offered_on_and_traded(world):
    ann, ben = world.app("ann"), world.app("ben")
    pikachu = put(ann, note="my first", extra={"language": "de"})
    eevee = put(ben, 133, "Vee", ec=0x55)
    pikachu_data, eevee_data = pikachu.data(), eevee.data()
    listed = ann.list_pokemon(pikachu, [EEVEE, wire.Want(1, "Bulbasaur", 5, 20)], "Ann")
    assert held(ann) == [] and listed.status == "open" and ann.live()
    assert (listed.path / "record.pk6").read_bytes() == pikachu_data
    assert listed.detail() == "Listed, waiting for an offer; wants Eevee (any level) or Bulbasaur (Lv 5-20)"
    assert ben.browse(world.pool) == []                                 # not published until a round
    assert ann.round(world.pool) == []

    found = ben.browse(world.pool, "pk6")
    assert [l.title() for l in found] == ["Pikachu Lv 30"] and found[0].asks() == "Eevee (any level) or Bulbasaur (Lv 5-20)"
    assert found[0].trainer == "Ann" and found[0].sha256 == hashlib.sha256(pikachu_data).hexdigest()
    assert ben.browse(world.pool, "pk6", wants=133) == found and ben.browse(world.pool, "pk6", has=25) == found
    assert ben.browse(world.pool, "pk7") == [] and ben.browse(world.pool, "pk6", has=133) == []
    assert [e.id for e in ben.fits(found[0])] == [eevee.id] and ann.browse(world.pool)[0].pubkey in ann.mine()

    offered = ben.offer(eevee, found[0], "Ben")
    assert held(ben) == [] and offered.status == "waiting"
    assert offered.detail() == "Offered, waiting for the lister's app (for Pikachu Lv 30)"
    assert ben.round(world.pool) == []                                  # sent; Ann's app has not opened
    world.later(3 * DAY)
    assert ann.round(world.pool) == ["Pikachu Lv 30 was traded for Eevee 'Vee' Lv 30, now in the bank"]
    got = ann.bank.entries()[0]
    assert held(ann) == ["Eevee"] and got.source == "gts" and got.legal is True and "with Ben" in got.note
    assert got.data() == eevee_data and ann.items()[0].status == "traded" and not ann.live()
    assert ben.browse(world.pool) == []                                 # the listing reads as traded now

    world.later(DAY)
    assert ben.round(world.pool) == ["Eevee 'Vee' Lv 30 was traded for Pikachu 'Sparky' Lv 30, now in the bank"]
    mine = ben.bank.entries()[0]
    assert held(ben) == ["Pikachu"] and mine.data() == pikachu_data and mine.source == "gts" and "with Ann" in mine.note
    assert ben.items()[0].status == "traded" and not ben.live()
    assert world.store.query({"ids": [offered.state["event"]["id"]]}) == []        # the offer is deleted
    # Further rounds change nothing, on either side.
    assert ann.round(world.pool) == [] and ben.round(world.pool) == [] and held(ann) == ["Eevee"] and held(ben) == ["Pikachu"]


def test_what_may_not_be_listed_or_offered(world):
    ann, ben = world.app("ann"), world.app("ben")
    pikachu = put(ann)
    with pytest.raises(ValueError, match="one to three"):
        ann.list_pokemon(pikachu, [], "Ann")
    with pytest.raises(ValueError, match="one to three"):
        ann.list_pokemon(pikachu, [EEVEE] * 4, "Ann")
    with pytest.raises(ValueError, match="only legal Pokemon.*Encounter type mismatch"):
        world.app("ann", illegal).list_pokemon(pikachu, [EEVEE], "Ann")
    assert ann.bank.entries()[0].legal is False and ann.items() == []   # the verdict is kept; nothing left the bank

    gold = ann.bank.deposit(sample_mon(2, 155, 10, "CYNDA").to_file(), "pk2", source="imported", mail=b"\x01" * 47)[0]
    with pytest.raises(ValueError, match="holding mail"):
        ann.list_pokemon(gold, [wire.Want(152, "Chikorita")], "Ann")
    red = ann.bank.deposit(sample_mon(1, 4, 10, "CHAR").to_file(), "pk1", source="imported")[0]
    with pytest.raises(ValueError, match="no shiny"):
        ann.list_pokemon(red, [wire.Want(1, "Bulbasaur", shiny=True)], "Ann")

    ann.list_pokemon(ann.bank.find(pikachu.id), [wire.Want(133, "Eevee", 40, 50)], "Ann")
    ann.round(world.pool)
    listing = ben.browse(world.pool)[0]
    with pytest.raises(ValueError, match="your own listing"):
        ann.offer(red, listing, "Ann")
    with pytest.raises(ValueError, match="trades .pk6 Pokemon"):
        ben.offer(ben.bank.deposit(sample_mon(1, 7, 10, "SQUIRT").to_file(), "pk1", source="imported")[0], listing, "Ben")
    with pytest.raises(ValueError, match=r"asks for Eevee \(Lv 40-50\)"):
        ben.offer(put(ben, 133, "Vee"), listing, "Ben")                 # an Eevee, but Lv 30
    assert ben.fits(listing) == [] and len(ben.bank.entries()) == 2 and ben.items() == []


def test_the_first_valid_offer_wins_and_the_rest_come_back(world):
    ann, ben, cat, dan = (world.app(n) for n in ("ann", "ben", "cat", "dan"))
    ann.list_pokemon(put(ann), [EEVEE], "Ann")
    ann.round(world.pool)
    listing = ben.browse(world.pool)[0]
    ben.offer(put(ben, 133, "First", ec=1), listing, "Ben")
    ben.round(world.pool)
    with pytest.raises(ValueError, match="already have an offer waiting"):
        ben.offer(put(ben, 133, "Again", ec=9), listing, "Ben")
    world.later(60)
    cat.offer(put(cat, 133, "Second", ec=2), listing, "Cat")
    cat.round(world.pool)
    world.later(60)
    said = ann.round(world.pool)
    assert len(said) == 1 and "Eevee 'First'" in said[0] and [e.nickname for e in ann.bank.entries()] == ["First"]
    assert cat.round(world.pool) == ["Eevee 'Second' Lv 30 is back in the bank: the listing was already traded"]
    assert [e.nickname for e in cat.bank.entries()] == ["Second"] and cat.items()[0].status == "returned"
    assert cat.items()[0].detail() == "Returned to the bank: the listing was already traded"
    assert "Pikachu" in ben.round(world.pool)[0] and held(ben) == ["Eevee", "Pikachu"]

    # Dan read the listing before it was traded and offers after: Ann's app is closed, the listing itself tells him.
    dan.offer(put(dan, 133, "Late", ec=3), listing, "Dan")
    assert dan.round(world.pool) == ["Eevee 'Late' Lv 30 is back in the bank: "
                                     "the listing was traded with someone else"]
    assert held(dan) == ["Eevee"]
    assert ann.round(world.pool) == [] and held(ann) == ["Eevee"]       # nothing of Dan's is left to answer


def test_the_listers_app_checks_what_is_offered(world):
    """An offer made by an app that checks nothing: the lister's app refuses it, and says why."""
    ann = world.app("ann")
    ann.list_pokemon(put(ann), [EEVEE], "Ann")
    ann.round(world.pool)
    listing = ann.browse(world.pool)[0]

    def send(data, **change):
        secret = schnorr.new_secret()
        event = wire.offer_event(secret, listing, data, "Mal", now=world.now[0])
        if change:      # a body the honest code would not write
            body = {"protocol": 1, "listing": listing.id, "format": "pk6", "record": "", "trainer": "", **change}
            event = relays.sign_event(secret, wire.WRAP, wire.seal(secret, bytes.fromhex(listing.pubkey), body),
                                      [["p", listing.pubkey]], int(world.now[0]))
        world.pool.publish(event)
        world.later(1)
        return secret, event

    def reason(sent):
        secret, event = sent
        found = world.pool.fetch({"kinds": [wire.WRAP], "#p": [event["pubkey"]]})
        answers = [wire.read_answer(secret, listing.pubkey, e) for e in found]
        return [a["reason"] if not a["ok"] else "traded" for a in answers if a]

    wrong = send(pk6(1, "Bulba", ec=7))
    garbage = send(bytes(232))
    unreadable = send(b"", record="!!", trainer="x")
    other = send(pk6(133, "Vee", ec=8), listing="ffffffffffffffff", record=wire.base64.b64encode(pk6(133, "Vee", ec=8)).decode())
    noise = relays.sign_event(schnorr.new_secret(), wire.WRAP, "not sealed", [["p", listing.pubkey]], int(world.now[0]))
    world.pool.publish(noise)
    assert ann.round(world.pool) == [] and ann.items()[0].status == "open"
    assert reason(wrong) == ["the Pokemon offered is not what the listing asks for"]
    assert reason(garbage)[0].startswith("not a Pokemon record")
    assert reason(unreadable) == [] and reason(other) == ["not an offer for this listing"]
    assert len(ann.items()[0].state["answers"]) == 3                    # each answered once, noise never

    strict = world.app("ann", illegal)                                  # the same app; PKHeX now finds fault
    hacked = send(pk6(133, "Hax", ec=9))
    assert strict.round(world.pool) == [] and reason(hacked) == ["PKHeX finds the Pokemon offered is not legal"]
    good = send(pk6(133, "Vee", ec=10))
    assert len(ann.round(world.pool)) == 1 and reason(good) == ["traded"] and held(ann) == ["Eevee"]
    late = send(pk6(133, "Late", ec=11))
    assert ann.round(world.pool) == [] and reason(late) == ["the listing was already traded"]


def test_a_listing_taken_down_returns_everything(world):
    ann, ben = world.app("ann"), world.app("ben")
    pikachu = put(ann, note="keep me", extra={"language": "fr"})
    pikachu_data = pikachu.data()
    item = ann.list_pokemon(pikachu, [EEVEE], "Ann")
    ann.round(world.pool)
    listing = ben.browse(world.pool)[0]
    with pytest.raises(ValueError, match="only an open listing"):
        ben.withdraw(ben.offer(put(ben, 133, "Vee"), listing, "Ben"))
    world.later(10)
    ann.withdraw(item)
    back = ann.bank.entries()[0]
    assert back.data() == pikachu_data and back.note == "keep me" and back.meta["language"] == "fr" and back.legal is True
    assert back.source == "imported" and ann.items()[0].status == "withdrawn" and not ann.live()
    assert ann.items()[0].detail() == "Taken down; wants Eevee (any level)"
    with pytest.raises(ValueError, match="only an open listing"):
        ann.withdraw(ann.items()[0])
    assert ben.round(world.pool) == []                                  # Ann's app has not said so yet
    assert ann.round(world.pool) == [] and ann.browse(world.pool) == [] and len(ann.bank.entries()) == 1
    assert ben.round(world.pool) == ["Eevee 'Vee' Lv 30 is back in the bank: the listing was taken down"]
    assert held(ben) == ["Eevee"] and ben.round(world.pool) == [] and held(ben) == ["Eevee"]


def test_a_listing_ends_after_thirty_days(world):
    ann, ben = world.app("ann"), world.app("ben")
    ann.list_pokemon(put(ann), [EEVEE], "Ann")
    ann.round(world.pool)
    listing = ben.browse(world.pool)[0]
    ben.offer(put(ben, 133, "Vee"), listing, "Ben")
    world.later(29 * DAY)
    assert ben.round(world.pool) == [] and len(ben.browse(world.pool)) == 1
    world.later(DAY + 2 * wire.SKEW)                                    # neither app opened in time
    assert ben.browse(world.pool) == []
    assert ben.round(world.pool) == ["Eevee 'Vee' Lv 30 is back in the bank: the listing ended"]
    assert ann.round(world.pool) == ["Pikachu Lv 30: the listing ended after 30 days and the Pokemon is back in the bank"]
    assert held(ann) == ["Pikachu"] and held(ben) == ["Eevee"] and ann.items()[0].status == "expired"
    assert ann.round(world.pool) == [] and len(ann.bank.entries()) == 1
    world.later(gts.KEPT + DAY)
    ann.round(world.pool)
    ben.round(world.pool)
    assert ann.items() == [] and ben.items() == []                      # finished folders are cleared in the end


def test_a_lister_that_sends_another_pokemon_is_caught(world):
    ann, ben = world.app("ann"), world.app("ben")
    ann.list_pokemon(put(ann), [EEVEE], "Ann")
    ann.round(world.pool)
    listing = ben.browse(world.pool)[0]
    offered = ben.offer(put(ben, 133, "Vee"), listing, "Ben")
    ben.round(world.pool)
    secret = bytes.fromhex(ann.items()[0].state["secret"])
    stranger = wire.answer_event(schnorr.new_secret(), offered.state["event"], listing.expires, record=pk6(19, "Ratty"))
    world.pool.publish(stranger)                                        # not the listing's key: no answer at all
    assert ben.round(world.pool) == [] and ben.items()[0].status == "waiting"
    world.pool.publish(wire.answer_event(secret, offered.state["event"], listing.expires, record=pk6(19, "Ratty")))
    said = ben.round(world.pool)
    assert len(said) == 1 and "not the one listed" in said[0] and held(ben) == [] and ben.items()[0].status == "failed"
    assert ben.items()[0].detail() == "Lost: the lister's app sent a Pokemon that is not the one listed"


def test_nothing_is_lost_to_a_crash_or_a_relay(world, monkeypatch):
    ann, ben = world.app("ann"), world.app("ben")
    ann.list_pokemon(put(ann), [EEVEE], "Ann")
    world.pool.down = True
    assert ann.round(world.pool) == [] and ben.browse(world.pool) == []
    world.pool.down = False
    ann.round(world.pool)
    listing = ben.browse(world.pool)[0]
    ben.offer(put(ben, 133, "Vee"), listing, "Ben")
    world.pool.down = True
    world.later(40 * DAY)
    assert ben.round(world.pool) == [] and ben.items()[0].status == "waiting"       # no relay: nothing is known
    world.later(-40 * DAY)
    world.pool.down = False
    ben.round(world.pool)

    # The relays forget everything: each app sends what is live again.
    world.store.events.clear()
    assert ben.browse(world.pool) == []
    ben.round(world.pool)
    real = bank.Bank.deposit
    calls = []

    def dies(self, *args, **kwargs):
        calls.append(1)
        raise OSError("the disk is full")

    monkeypatch.setattr(bank.Bank, "deposit", dies)
    said = ann.round(world.pool)                                        # the trade is decided, then the app dies
    assert calls and "the disk is full" in said[0] and held(ann) == [] and ann.items()[0].status == "traded"
    monkeypatch.setattr(bank.Bank, "deposit", real)
    assert ann.round(world.pool) == [] and held(ann) == ["Eevee"]       # banked when it next opens, and answered
    assert "Pikachu" in ben.round(world.pool)[0] and held(ben) == ["Pikachu"]

    # A returned offer whose Pokemon never reached the bank is given back the next time.
    cat = world.app("cat")
    cat.offer(put(cat, 133, "Late", ec=4), listing, "Cat")
    monkeypatch.setattr(bank.Bank, "deposit", dies)
    assert "the disk is full" in cat.round(world.pool)[0] and held(cat) == [] and cat.items()[0].status == "returned"
    monkeypatch.setattr(bank.Bank, "deposit", real)
    assert cat.round(world.pool) == [] and held(cat) == ["Eevee"]
    assert cat.round(world.pool) == [] and len(cat.bank.entries()) == 1


def test_listings_read_from_a_relay_are_checked(world):
    ann = world.app("ann")
    ann.list_pokemon(put(ann), [EEVEE], "Ann")
    ann.round(world.pool)
    event = ann.items()[0].state["event"]
    assert wire.parse_listing(event).title() == "Pikachu Lv 30"
    secret = schnorr.new_secret()
    body = __import__("json").loads(event["content"])

    def forged(**change):
        return relays.sign_event(secret, wire.LISTING, __import__("json").dumps({**body, **change}), event["tags"],
                                 event["created_at"])

    assert wire.parse_listing(forged()) is not None
    for change in ({"protocol": 2}, {"format": "pk8"}, {"wants": []}, {"wants": [{"species": 133}]}, {"sha256": "abc"},
                   {"status": "sold"}, {"pokemon": {"species": 25, "level": 101}}, {"winner": "me"},
                   {"pokemon": {**body["pokemon"], "nickname": "x" * 41}}, {"trainer": "x" * 17},
                   {"wants": [{"species": 133, "name": "Eevee", "min_level": 50, "max_level": 40}]}):
        assert wire.parse_listing(forged(**change)) is None, change
    assert wire.parse_listing({**event, "content": event["content"] + " "}) is None       # the signature fails
    other = relays.sign_event(secret, wire.LISTING, event["content"], [t for t in event["tags"] if t[1] != wire.TAG])
    assert wire.parse_listing(other) is None                            # another app's listing

    # A sealed body is read by the two keys it was sealed between, and by no other.
    a, b, c = schnorr.new_secret(), schnorr.new_secret(), schnorr.new_secret()
    sealed = wire.seal(a, schnorr.public_key(b), {"x": 1})
    assert wire.unseal(b, schnorr.public_key(a), sealed) == {"x": 1}
    assert wire.unseal(c, schnorr.public_key(a), sealed) is None and wire.unseal(b, schnorr.public_key(a), "AAAA") is None
    assert wire.unseal(b, schnorr.public_key(a), sealed[:-4] + "AAAA") is None


def test_the_gts_page(world, monkeypatch, tmp_path):
    """The page headless: list from the Bank page, browse and offer from another app, both settle."""
    pytest.importorskip("flet")
    from gui.app import App
    from gui.views.bank import BankView
    from gui.views.gts import GtsView
    from pokeuds.app.settings import Settings

    monkeypatch.setattr(gts, "pkhex_check", legal)
    monkeypatch.setattr(gts.Gts.__init__, "__defaults__", (None, legal, lambda: world.now[0], ()))

    def page(name):
        app = App(None, Settings(bank=str(tmp_path / name / "bank"), trainer_name=name.upper(),
                                 output=str(tmp_path / name / "received")))
        view = GtsView(app)
        view.pool_factory = lambda: world.pool
        view.gts_root = tmp_path / name / "gts"
        app.views = {"gts": view}
        app.navigate = lambda key: None
        return app, view

    def wait(view):
        for _ in range(200):
            if not view.working:
                return
            time.sleep(0.02)
        raise AssertionError("still working")

    ann_app, ann = page("ann")
    pikachu = put(ann.gts)
    ann.control()
    assert ann.opened and not ann.working                               # nothing live: no round at start
    BankView(ann_app)._to_gts(pikachu)
    assert ann.listing_entry == pikachu.id and ann.browse_format == "pk6"
    ann.control()
    ann._list(pikachu)
    assert "Choose at least one" in ann.message and held(ann.gts) == ["Pikachu"]
    ann._want(0, "species", 133)
    ann._want(0, "max_level", "40")
    ann._list(pikachu)
    wait(ann)
    assert ann.message == "Listed Pikachu Lv 30." and held(ann.gts) == [] and ann.wants[0]["species"] == 0
    ann.control()

    ben_app, ben = page("ben")
    put(ben.gts, 133, "Vee")
    ben.browse_format = "pk6"
    ben._background("browse", ben._browse_work)
    wait(ben)
    assert [l.title() for l in ben.found] == ["Pikachu Lv 30"]
    ben.control()                                                       # the listing row, with what fits
    ben._offer(ben.found[0])
    wait(ben)
    assert ben.message.startswith("Offered Eevee 'Vee' Lv 30 for Pikachu Lv 30") and ben.found == []
    assert ben.gts.items()[0].status == "waiting"

    _, ann_again = page("ann")
    ann_again.settle()                                                  # the app opens: the trade settles
    wait(ann_again)
    assert "traded for Eevee 'Vee' Lv 30" in ann_again.message and held(ann_again.gts) == ["Eevee"]
    ben._background("round", ben._round_work)
    wait(ben)
    assert "traded for Pikachu 'Sparky' Lv 30" in ben.message and held(ben.gts) == ["Pikachu"]
    ben.control()
    ann_again.control()


def test_a_game_boy_pokemons_language_goes_with_it(world):
    """A .pk1/.pk2 does not hold its game's language; the bank keeps it beside, and the GTS carries it both ways."""
    ann, ben = world.app("ann"), world.app("ben")
    char = ann.bank.deposit(sample_mon(1, 4, 10, "GLUMANDA").to_file(), "pk1", source="received",
                            extra={"language": "de"})[0]
    squirt = ben.bank.deposit(sample_mon(1, 7, 10, "CARAPUCE").to_file(), "pk1", source="received",
                              extra={"language": "fr"})[0]
    ann.list_pokemon(char, [wire.Want(7, "Squirtle")], "Ann")
    ann.round(world.pool)
    listing = ben.browse(world.pool, "pk1")[0]
    assert listing.pokemon["language"] == "de"
    ben.offer(squirt, listing, "Ben")
    ben.round(world.pool)
    ann.round(world.pool)
    ben.round(world.pool)
    assert [(e.species, e.meta.get("language")) for e in ann.bank.entries()] == [("Squirtle", "fr")]
    assert [(e.species, e.meta.get("language")) for e in ben.bank.entries()] == [("Charmander", "de")]
    # A language no game has is not a listing.
    event = ann.items()[0].state["event"]
    body = __import__("json").loads(event["content"])
    body["pokemon"]["language"] = "xx"
    forged = relays.sign_event(schnorr.new_secret(), wire.LISTING, __import__("json").dumps(body), event["tags"],
                               event["created_at"])
    assert wire.parse_listing(forged) is None


def test_the_gts_folder_lives_in_the_bank(tmp_path):
    """What is listed left this bank and goes back to it: two copies of the app, two banks, two GTS folders."""
    one, two = gts.Gts(bank.Bank(tmp_path / "one")), gts.Gts(bank.Bank(tmp_path / "two"))
    assert one.root == tmp_path / "one" / ".gts" and two.root == tmp_path / "two" / ".gts"
    one.check = legal
    item = one.list_pokemon(put(one), [EEVEE], "Ann")
    assert item.path.parent == one.root and two.items() == [] and one.bank.entries() == []


def test_browsing_every_format(world):
    """All formats is the page's default: a .pk6 listing and a .pk1 one both show, and a species filter still works
    (the relays index a species per format, so it is applied here)."""
    ann, ben = world.app("ann"), world.app("ben")
    ann.list_pokemon(put(ann), [EEVEE], "Ann")
    ann.list_pokemon(ann.bank.deposit(sample_mon(1, 4, 10, "CHAR").to_file(), "pk1", source="imported")[0],
                     [wire.Want(7, "Squirtle")], "Ann")
    ann.round(world.pool)
    assert sorted(l.format for l in ben.browse(world.pool)) == ["pk1", "pk6"]
    assert [l.format for l in ben.browse(world.pool, None, has=4)] == ["pk1"]
    assert [l.format for l in ben.browse(world.pool, None, wants=133)] == ["pk6"]
    assert ben.browse(world.pool, "pk7") == []


def test_two_copies_of_the_app_on_one_computer_never_trade_with_each_other(world, tmp_path, monkeypatch):
    """A from-source copy keeps its bank in %APPDATA%, the packaged one beside the exe, and both write to one received
    folder: neither may offer on the other's listing, and a lister refuses an offer that is already on its computer."""
    from pokeuds.app import settings

    monkeypatch.setattr(settings, "system_data_dir", lambda: tmp_path / "appdata")
    received = tmp_path / "received"
    for folder in (received, tmp_path / "appdata" / "bank", tmp_path / "app" / "Bank"):
        folder.mkdir(parents=True)
    source = gts.Gts(bank.Bank(tmp_path / "appdata" / "bank"), check=legal, clock=lambda: world.now[0])
    exe = gts.Gts(bank.Bank(tmp_path / "app" / "Bank"), check=legal, clock=lambda: world.now[0])
    source.near = gts.nearby(source.bank.folder, received)
    exe.near = gts.nearby(exe.bank.folder, received)
    assert source.near == [received] and exe.near == [tmp_path / "appdata" / "bank", received]
    friend = world.app("friend")

    # The exe lists a Pokemon it was traded: the source copy sees it as the player's, by its key (it finds the exe's
    # GTS folder only through the received folder's copy of the record).
    (received / "bunnelby.pk6").write_bytes(pk6(659, "UDS", ec=7))
    exe.list_pokemon(exe.bank.deposit((received / "bunnelby.pk6").read_bytes(), "pk6", source="received")[0],
                     [EEVEE], "Me")
    exe.round(world.pool)
    listing = friend.browse(world.pool)[0]
    assert friend.whose(listing) == ""
    with pytest.raises(ValueError, match="already on this computer"):
        source.offer(put(source, 133, "Vee", ec=8), listing, "Me")
    assert len(source.bank.entries()) == 1                          # nothing left the bank

    # The source copy lists: the exe finds that copy's GTS folder in %APPDATA% and knows its key.
    source.list_pokemon(put(source, 25, "Volt", ec=9), [EEVEE], "Me")
    source.round(world.pool)
    theirs = next(l for l in friend.browse(world.pool) if l.pokemon["species"] == 25)
    assert "another copy of PokeUDS" in exe.whose(theirs) and exe.whose(listing) == "this is your own listing"
    assert theirs.pubkey in exe.mine()

    # An older copy that still sends the offer is refused by the lister: its Eevee sits in a GTS folder here.
    old = gts.Gts(source.bank, check=legal, clock=lambda: world.now[0])
    old.whose = lambda listing: ""
    old.offer(put(old, 133, "Vee", ec=10), listing, "Me")
    old.round(world.pool)
    exe.round(world.pool)
    old.round(world.pool)
    back = [i for i in old.items() if i.kind == "offer"][0]
    assert back.status == "returned" and "cannot trade with yourself" in back.state["reason"]
    assert [i.status for i in exe.items()] == ["open"]

    # Someone else's Eevee is still traded.
    friend.offer(put(friend, 133, "Eve", ec=11), listing, "Friend")
    friend.round(world.pool)
    exe.round(world.pool)
    assert next(i for i in exe.items() if i.kind == "listing").status == "traded"
