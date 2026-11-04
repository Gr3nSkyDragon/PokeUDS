"""The Game Boy trade engine against a model 3DS, the blocks and the Time Capsule conversions."""

import struct

import pytest

from pokeuds import gbpk
from pokeuds.vc import gbdata, link
from tests.vc_model import Model3DS, play


def gen1_mon(dex=25, level=12, nick="SPARKY", ot="ASH", ot_id=12345, catch_rate=0xBE, fe_inside=False):
    d = bytearray(gbpk.GEN1_STRUCT)
    d[0] = gbpk.dex_to_index(dex)
    struct.pack_into(">H", d, 1, 30)
    d[3] = level
    d[5], d[6] = gbdata.TYPES["ELECTRIC"], gbdata.TYPES["ELECTRIC"]
    d[7] = catch_rate
    d[8:12] = bytes([84, 45, 0xFE if fe_inside else 86, 0])     # Thundershock, Growl, ...
    struct.pack_into(">H", d, 12, ot_id)
    d[14:17] = (2000).to_bytes(3, "big")
    struct.pack_into(">HHHHH", d, 17, 100, 200, 300, 400, 500)
    d[27:29] = b"\xAB\xCD"
    d[29:33] = bytes([30, 40, 20, 0])
    d[33] = level
    struct.pack_into(">HHHHH", d, 34, 33, 20, 15, 30, 22)
    return gbpk.Record(1, bytes(d), gbpk.encode_name(ot), gbpk.encode_name(nick))


def gen2_mon(dex=155, level=9, nick="EMBER", ot="GOLD", ot_id=54321, item=0x00):
    d = bytearray(gbpk.GEN2_STRUCT)
    d[0], d[1] = dex, item
    d[2:6] = bytes([33, 43, 52, 0])
    struct.pack_into(">H", d, 6, ot_id)
    d[8:11] = (900).to_bytes(3, "big")
    struct.pack_into(">HHHHH", d, 11, 10, 20, 30, 40, 50)
    d[21:23] = b"\x9A\xBC"
    d[23:27] = bytes([35, 30, 25, 0])
    d[27] = 70
    d[31] = level
    struct.pack_into(">H", d, 34, 28)
    struct.pack_into(">HHHHHH", d, 36, 28, 15, 14, 18, 17, 16)
    return gbpk.Record(2, bytes(d), gbpk.encode_name(ot), gbpk.encode_name(nick))


def party(*mons, name="LINK", tid=0x1234, mail=()):
    return link.Party(gbpk.encode_name(name), tid, list(mons), list(mail))


# -- data -----------------------------------------------------------------------------------------

def test_species_indexes_match_the_captures():
    # Party bytes decoded from retail VC Red captures (wiki/vc_link.md): MEW 15, Oddish B9, Poliwrath 6F, Pikachu 54.
    assert [gbpk.dex_to_index(d) for d in (151, 43, 62, 25)] == [0x15, 0xB9, 0x6F, 0x54]
    assert gbpk.index_to_dex(0x1F) == 0          # a MissingNo slot
    with pytest.raises(ValueError):
        gbpk.dex_to_index(152)


def test_names_and_files(tmp_path):
    assert gbpk.encode_name("OG WEED") == bytes.fromhex("8e867f96848483") + b"\x50" + bytes(3)
    assert gbpk.decode_name(bytes.fromhex("8e867f9684848350")) == "OG WEED"
    mon = gen1_mon()
    raw = mon.to_file()
    assert len(raw) == gbpk.PK1_SIZE and raw[:3] == bytes([1, 0x54, 0xFF])
    assert gbpk.Record.from_file(raw) == mon
    two = gen2_mon()
    assert len(two.to_file()) == gbpk.PK2_SIZE and gbpk.Record.from_file(two.to_file()) == two


def test_stat_formula():
    # Mewtwo at 100 with max DVs and stat exp: Special 154 base -> 406 in Gen 1.
    assert gbpk.calc_stat(154, 15, 65535, 100) == 406
    assert gbpk.calc_stat(50, 0, 0, 5) == 10


# -- Time Capsule conversions ---------------------------------------------------------------------

def test_gen2_to_gen1_as_the_time_capsule_sends_it():
    mon = gen2_mon(dex=81, level=20, item=0x92)                   # Magnemite holding Leftovers
    one = gbpk.gen2_to_gen1(mon)
    d = one.data
    assert d[0] == gbpk.dex_to_index(81) and d[3] == 0            # box level 0
    assert d[5] == d[6] == gbdata.TYPES["ELECTRIC"]               # no Steel in Gen 1
    assert d[7] == 0x92                                           # catch rate <- held item
    assert d[8:12] == mon.data[2:6] and d[27:29] == mon.data[21:23]
    assert d[33] == 20
    special = gbpk.calc_stat(gbdata.KANTO_SPECIAL[81], 0xC, 50, 20)
    assert struct.unpack_from(">H", d, 42)[0] == special


def test_time_capsule_refuses_gen2_only():
    with pytest.raises(ValueError, match="Generation 1"):
        gbpk.gen2_to_gen1(gen2_mon(dex=155))
    mon = bytearray(gen2_mon(dex=25).data)
    mon[3] = 200                                                  # a Gen 2 move
    with pytest.raises(ValueError, match="move"):
        gbpk.gen2_to_gen1(gbpk.Record(2, bytes(mon), gbpk.encode_name("A"), gbpk.encode_name("B")))


def test_gen1_to_gen2_as_a_gen2_game_receives_it():
    two = gbpk.gen1_to_gen2(gen1_mon(catch_rate=0x19))
    d = two.data
    assert d[0] == 25 and d[1] == 0x92                            # catch rate $19 becomes Leftovers
    assert d[27] == 70 and d[28:31] == bytes(3)                   # base friendship, no Pokerus or caught data
    assert d[31] == 12 and d[2:6] == bytes([84, 45, 86, 0])
    base = gbdata.GEN2_BASE[25][0]
    assert struct.unpack_from(">HH", d, 44) == (gbpk.calc_stat(base[4], 0xD, 500, 12), gbpk.calc_stat(base[5], 0xD, 500, 12))
    assert gbpk.gen1_to_gen2(gen1_mon(catch_rate=0xA0)).data[1] == 0xA0     # not in the table: same number
    round_trip = gbpk.gen1_to_gen2(gbpk.gen2_to_gen1(gen2_mon(dex=25, item=0x92)))
    assert round_trip.data[:27] == gen2_mon(dex=25, item=0x92).data[:27]


# -- blocks ---------------------------------------------------------------------------------------

def test_patch_lists_restore_fe():
    p = party(gen1_mon(fe_inside=True))
    block, patch = link.party_blocks(p, 1)
    assert link.NO_DATA not in block[6:]
    back = link.parse_party(list(block), list(patch), 1)
    assert back.mons[0].data == p.mons[0].data and back.mons[0].nickname == p.mons[0].nickname


def test_mail_block_round_trip():
    mail = bytes(range(0x80, 0x80 + 33)).replace(b"\x85", b"\xfe") + bytes([0xFE] + [7] * 13)  # text bytes; $21 stands in for $FE
    block = link.mail_block(party(gen2_mon(item=0x9E), mail=[mail]))
    assert len(block) == 390 and block[:5] == b"\x20" * 5 and link.NO_DATA not in block
    assert link.parse_mail(list(block[5:]))[0] == mail


# -- whole trades against the model ---------------------------------------------------------------

def run_trade(mode, ours, theirs, **model_kw):
    trades = []
    engine = link.TradeEngine(mode, ours, on_trade=trades.append)
    model = Model3DS(mode, theirs, **model_kw)
    play(engine, model)
    return engine, model, trades


def test_gen1_trade():
    mine, theirs = gen1_mon(dex=25, nick="SPARKY"), gen1_mon(dex=43, nick="OG WEED", ot="LINK", ot_id=31693)
    engine, model, trades = run_trade(link.GEN1, party(mine, name="POKEUDS"), party(theirs))
    assert [t.received for t in trades] == [theirs]
    assert model.received == [mine]                               # the 3DS got ours intact
    assert gbpk.decode_name(model.partner_parties[0].trainer_name) == "POKEUDS"
    # Round 2: the 3DS sees our new party (its own Oddish), and both leave with Cancel.
    assert model.partner_parties[1].mons[0] == theirs


def test_gen2_trade_with_mail():
    mail = b"HELLO".ljust(33, b"\x7f") + b"POKEUDS\x50\x00\x00" + b"\x12\x34" + bytes([155, 0x9E])
    mine = gen2_mon(dex=155, item=0x9E)                          # Cyndaquil holding Flower Mail
    theirs = gen2_mon(dex=158, nick="CHOMP", ot="SILVER", ot_id=777, item=0x9E)
    their_mail = b"FROM SILVER".ljust(33, b"\x7f") + b"SILVER\x50\x00\x00\x00" + b"\x03\x09" + bytes([158, 0x9E])
    engine, model, trades = run_trade(link.GEN2, party(mine, mail=[mail]), party(theirs, tid=777, mail=[their_mail]))
    assert trades[0].received == theirs and trades[0].received_mail == their_mail
    assert model.received == [mine] and model.partner_mail[0][0] == mail
    assert model.partner_parties[0].trainer_id == 0x1234


def test_gen2_trade_without_mail_sends_empty_mail():
    engine, model, trades = run_trade(link.GEN2, party(gen2_mon(dex=152)), party(gen2_mon(dex=161)))
    assert trades[0].received.dex == 161 and trades[0].received_mail is None
    assert model.partner_mail[0] == [bytes(47)] * 6


@pytest.mark.parametrize("console_generation", [1, 2])
def test_time_capsule_both_directions(console_generation):
    # 3DS on Red with us as a Gen 2 game (offer converted), or the 3DS on Gold in the Time Capsule with us as Gen 1.
    if console_generation == 1:
        offer = gbpk.gen2_to_gen1(gen2_mon(dex=25, item=0x92))
    else:
        offer = gen1_mon(dex=25)
    theirs = gen1_mon(dex=133, nick="EEVEE", ot="RED")
    engine, model, trades = run_trade(link.TIME_CAPSULE, party(offer), party(theirs),
                                      console_generation=console_generation)
    assert trades[0].received == theirs and model.received == [offer]
    assert model.partner_mail == []                              # no mail in the Time Capsule


def test_declined_trade_saves_nothing():
    engine, model, trades = run_trade(link.GEN1, party(gen1_mon()), party(gen1_mon(dex=1)), accept=False)
    assert trades == [] and model.received == []
