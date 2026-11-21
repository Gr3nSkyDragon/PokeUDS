"""The bank: deposits, de-duplication, the received folder, Time Capsule copies, export, games."""

import struct
import sys
from dataclasses import replace

import pytest

from pokeuds import gbpk, pk8, pkhex, pkx, received
from pokeuds.app import bank
from pokeuds.radio.demo import sample_mon
from tests.test_pk8 import make_pk8
from tests.test_pkx import make_record


def pk6(species=25, nickname="Sparky", exp=27000, ec=0x1234_5678):
    data = bytearray(make_record(ec, species, nickname=nickname, size=pkx.STORED_SIZE))
    struct.pack_into("<I", data, 0x10, exp)
    return pkx.fix_checksum(bytes(data))


@pytest.fixture
def b(tmp_path):
    return bank.Bank(tmp_path / "bank")


def test_import_reads_and_keeps_once(b, tmp_path):
    src = tmp_path / "sparky.ek6"
    src.write_bytes(pkx.encrypt(pk6()))                # an encrypted file is banked decrypted
    entry, added = b.import_file(src)
    assert added and entry.extension == "pk6" and entry.species == "Pikachu" and entry.level == 30
    assert pkx.checksum_valid(entry.data()) and entry.source == "imported" and entry.origin == str(src)
    again, added = b.import_file(src)
    assert not added and again.id == entry.id and len(b.entries()) == 1
    assert entry.describe().startswith("Pikachu 'Sparky' Lv 30, OT Ash")


def test_refuses_what_is_not_a_pokemon(b, tmp_path):
    (tmp_path / "x.pk6").write_bytes(bytes(100))
    with pytest.raises(ValueError, match="not a Generation 6/7 record"):
        b.import_file(tmp_path / "x.pk6")
    (tmp_path / "x.pk6").write_bytes(pk6(species=722))
    with pytest.raises(ValueError, match="does not exist in Generation 6"):
        b.import_file(tmp_path / "x.pk6")
    (tmp_path / "x.txt").write_text("hi")
    with pytest.raises(ValueError, match="not a Pokemon file"):
        b.import_file(tmp_path / "x.txt")


def test_received_folder_is_deposited_once(b, tmp_path):
    out = tmp_path / "received"
    received.save(pk6(), out, game="xy", stamp="20261104-120000")
    gold = out / "cynda.pk2"
    gold.write_bytes(sample_mon(2, 155, 10, "CYNDA").to_file())
    gold.with_suffix(".mail").write_bytes(b"\x01" * 47)
    added = b.sync_received(out)
    assert sorted(e.extension for e in added) == ["pk2", "pk6"] and all(e.source == "received" for e in added)
    assert next(e for e in added if e.extension == "pk2").has_mail
    assert b.sync_received(out) == []
    b.remove(b.entries()[0])                           # removed from the bank: not brought back
    assert b.sync_received(out) == [] and len(b.entries()) == 1


def test_time_capsule_copies(b, tmp_path):
    entry, _ = b.deposit(sample_mon(1, 4, 12, "CHARMY").to_file(), "pk1", source="imported")
    copy, added = b.convert(entry)
    assert added and copy.extension == "pk2" and copy.dex == 4 and copy.source == "converted"
    back, _ = b.convert(copy)
    assert back.extension == "pk1" and back.dex == 4
    cynda, _ = b.deposit(sample_mon(2, 155, 10, "CYNDA").to_file(), "pk2", source="imported")
    with pytest.raises(ValueError, match="not a Generation 1 species"):
        b.convert(cynda)


def test_gen2_shiny_and_export(b, tmp_path):
    mon = sample_mon(2, 25, 20, "PIKA")
    data = bytearray(mon.data)
    data[21:23] = bytes([0xEA, 0xAA])                  # Attack 14, Defense/Speed/Special 10
    shiny = gbpk.Record(2, bytes(data), mon.ot_name, mon.nickname)
    entry, _ = b.deposit(shiny.to_file(), "pk2", source="built")
    assert entry.shiny and entry.level == 20
    first = b.export(entry, tmp_path / "out")
    second = b.export(entry, tmp_path / "out")
    assert first.name == "025_PIKA.pk2" and second.name == "025_PIKA_2.pk2"
    assert gbpk.load(first).dex == 25


def test_games_for(b):
    gen1, _ = b.deposit(sample_mon(1, 4, 12, "CHARMY").to_file(), "pk1", source="imported")
    xy, _ = b.deposit(pk6(), "pk6", source="imported")
    rowlet = pkx.fix_checksum(pk6(species=722)[:])
    sm, _ = b.deposit(rowlet, "pk7", source="imported")
    assert bank.games_for(gen1) == ["rby", "gsc"]
    assert bank.games_for(xy) == ["xy", "oras"] and bank.games_for(sm) == ["sm", "usum"]


def test_notes_and_legality_are_kept(b):
    entry, _ = b.deposit(pk6(), "pk6", source="imported")
    entry = b.set_note(entry, "for Dana")
    entry = b.set_legality(entry, True, "Legal!", "Wild")
    again = b.find(entry.id)
    assert again.note == "for Dana" and again.legal is True and again.report == "Legal!"


def traded(record: bytes, handler="Refresh", memory=3) -> bytes:
    """``record`` as a 3DS sends it back after a trade: its player as the handler, with memories."""
    d = bytearray(record)
    d[0x78:0x92] = handler.encode("utf-16-le").ljust(26, b"\0")
    d[0x93], d[0xA2], d[0xA4], d[0xA5], d[0xA6], d[0x95] = 1, 70, 1, 4, memory, 49
    return pkx.fix_checksum(bytes(d))


def test_the_same_pokemon_after_trades_is_kept_once(b, tmp_path):
    first, added = b.deposit(pk6(), "pk6", source="built")
    assert added
    out = tmp_path / "received"
    for n, memory in enumerate((3, 4, 14), 1):         # traded away and back three times
        received.save(traded(pk6(), memory=memory), out, game="xy", trade=n, stamp=f"20261105-21000{n}")
    assert b.sync_received(out) == [] and [e.id for e in b.entries()] == [first.id]
    again, added = b.deposit(pkx.encrypt(traded(pk6(), handler="Other")), "pk6", source="imported")
    assert not added and again.id == first.id
    grown, added = b.deposit(pk6(exp=30000), "pk6", source="received")      # it changed: a new entry
    assert added and grown.id != first.id
    gold = sample_mon(2, 155, 10, "CYNDA").to_file()
    b.deposit(gold, "pk2", source="built")
    happier = bytearray(gold)
    happier[3 + 27] = 200                              # a trade sets a Gen 2 Pokemon's friendship anew
    assert not b.deposit(bytes(happier), "pk2", source="received")[1]


def test_duplicates_banked_before_are_removed(b, tmp_path):
    first, _ = b.deposit(pk6(), "pk6", source="built")
    for n in range(2):                                 # as an older PokeUDS banked them, by exact content
        path = b.folder / f"20261105-21000{n}_025_Sparky_{n}.pk6"
        path.write_bytes(traded(pk6(), memory=n + 3))
        b._write_meta(path, {"source": "received", "created": first.created + n + 1})
    assert len(b.entries()) == 3
    pairs = b.duplicates()
    assert len(pairs) == 2 and all(kept.id == first.id for _, kept in pairs)
    assert [d for d, _ in b.remove_duplicates()] and [e.id for e in b.entries()] == [first.id]
    assert b.duplicates() == []


class FakeHelper:
    """Stands in for the PKHeX helper: checks by species, migrates to a fixed .pk7."""

    def __init__(self):
        self.migrated = []

    def check(self, game, data):
        legal = bank.parse(f"x.{ {'rby': 'pk1', 'gsc': 'pk2'}.get(game, 'pk6' if game in ('xy', 'oras') else 'pk7') }",
                           data).dex != 150
        return pkhex.Built(legal, "Legal!" if legal else "Invalid: no.", "Wild", "pk6", data, {})

    def migrate(self, game, data, source, vc_game=None, trainer=None, language=""):
        self.migrated.append((game, source, vc_game, trainer))
        self.languages = getattr(self, "languages", []) + [language]
        if game == "swsh":
            return pkhex.Built(True, "Legal!", "HOME transfer", "pk8", make_pk8(species=6, ec=len(self.migrated)), {})
        return pkhex.Built(True, "Legal!", "Virtual Console Transfer", "pk7", pk6(species=6, ec=len(self.migrated)), {})


def test_the_whole_bank_is_checked(b, monkeypatch):
    monkeypatch.setattr(pkhex, "shared", lambda: FakeHelper())
    b.deposit(pk6(), "pk6", source="imported")
    b.deposit(sample_mon(1, 150, 70, "MEWTWO").to_file(), "pk1", source="imported")
    seen = []
    result = bank.check_all(b, progress=lambda done, total, e: seen.append((done, total)))
    assert len(result.legal) == 1 and [e.species for e in result.illegal] == ["Mewtwo"] and not result.failed
    assert seen[-1] == (2, 2) and all(e.legal is not None for e in b.entries())
    assert bank.check_all(b, stop=lambda: True).legal == []


def test_migrating_keeps_the_original_and_makes_one_copy(b):
    helper = FakeHelper()
    gen1, _ = b.deposit(sample_mon(1, 4, 12, "CHARMY").to_file(), "pk1", source="imported")
    xy, _ = b.deposit(pk6(), "pk6", source="imported")
    assert bank.migration_target(gen1) == 7 and bank.migration_target(xy) == 7
    assert bank.migrates_to(gen1) == ["sm", "usum"] and bank.migrates_to(xy) == ["sm", "usum"]
    copy, made = bank.migrate(b, gen1, vc_game="blue", trainer={"ot": "ME"}, helper=helper)
    assert made and copy.extension == "pk7" and copy.source == "migrated" and copy.legal is True
    assert copy.meta["migrated_from"] == gen1.id and helper.migrated == [("sm", "rby", "blue", {"ot": "ME"})]
    assert b.find(gen1.id) is not None and bank.games_for(b.find(gen1.id)) == ["rby", "gsc"]   # the original stays
    assert bank.games_for(copy) == ["sm", "usum"] and bank.migration_target(copy) is None   # and the copy stays in Gen 7
    again, made = bank.migrate(b, b.find(gen1.id), helper=helper)
    assert not made and again.id == copy.id and len(helper.migrated) == 1
    with pytest.raises(ValueError):
        bank.migrate(b, copy, helper=helper)                       # a .pk7 goes nowhere
    with pytest.raises(ValueError):
        bank.migrate(b, replace(gen1, meta={}), vc_game="gold", helper=helper)   # Gold is not a Gen 1 game
    xy_copy, made = bank.migrate(b, xy, helper=helper)
    assert made and helper.migrated[-1][1] == "xy" and xy_copy.note.startswith("Pokemon Bank copy")


def test_pk8_files_are_banked(b, tmp_path):
    src = tmp_path / "zappy.ek8"
    src.write_bytes(pk8.encrypt(make_pk8(species=26, form=1, nickname="Zappy", version=36)))
    entry, added = b.import_file(src)
    assert added and entry.extension == "pk8" and entry.generation == 8 and entry.species == "Raichu" and entry.form == 1
    assert entry.level == 30 and pk8.checksum_valid(entry.data()) and len(entry.data()) == pk8.STORED_SIZE
    assert bank.games_for(entry) == [] and bank.migration_target(entry) is None and bank.pkhex_game(entry) == "swsh"
    assert bank.origin_mark(entry) == "Game Boy (Virtual Console)" and bank.reaches_swsh(entry)
    assert b.import_file(src)[1] is False
    (tmp_path / "grookey.pk8").write_bytes(make_pk8(species=810))
    with pytest.raises(ValueError, match="3DS games' species"):
        b.import_file(tmp_path / "grookey.pk8")


def test_what_reaches_sword_and_shield(b):
    pikachu = replace(b.deposit(pk6(species=25), "pk6", source="imported")[0], form=0)   # the test record's 0x1D
    typhlosion, _ = b.deposit(pk6(species=157, nickname="Typh"), "pk6", source="imported")
    gb, _ = b.deposit(sample_mon(1, 4, 12, "CHARMY").to_file(), "pk1", source="imported")
    assert bank.reaches_swsh(pikachu) and bank.reaches_swsh(gb) and not bank.reaches_swsh(typhlosion)
    assert not bank.reaches_swsh(replace(pikachu, form=1))         # a Cosplay Pikachu stays in Gen 6
    alolan = replace(pikachu, extension="pk7", generation=7, dex=26, form=1)
    assert bank.reaches_swsh(alolan) and bank.origin_mark(pikachu) == ""


def test_the_escape_rope_goes_through_gen_7_and_makes_one_copy(b):
    helper = FakeHelper()
    gen1, _ = b.deposit(sample_mon(1, 4, 12, "CHARMY").to_file(), "pk1", source="imported")
    copy, made = bank.escape_rope(b, gen1, vc_game="red", trainer={"ot": "ME"}, helper=helper)
    assert made and copy.extension == "pk8" and copy.source == "migrated" and copy.legal is True
    assert [m[:3] for m in helper.migrated] == [("sm", "rby", "red"), ("swsh", "sm", None)]
    gen7 = bank.migrated_copy(b, b.find(gen1.id))
    assert gen7 is not None and gen7.extension == "pk7" and copy.meta["migrated_from"] == gen7.id
    assert bank.swsh_copy(b, b.find(gen1.id)).id == copy.id == bank.swsh_copy(b, gen7).id
    again, made = bank.escape_rope(b, b.find(gen1.id), helper=helper)
    assert not made and again.id == copy.id and len(helper.migrated) == 2
    with pytest.raises(ValueError, match="already"):
        bank.escape_rope(b, copy, helper=helper)
    # A Gen 1 Pokemon migrated to Gen 7 before ropes out from that copy, not from a new one.
    gen1b, _ = b.deposit(sample_mon(1, 7, 12, "SQUIRT").to_file(), "pk1", source="imported")
    first, _ = bank.migrate(b, gen1b, vc_game="blue", helper=helper)
    roped, made = bank.escape_rope(b, b.find(gen1b.id), helper=helper)
    assert made and helper.migrated[-1][0] == "swsh" and roped.meta["migrated_from"] == first.id
    typhlosion, _ = b.deposit(pk6(species=157, nickname="Typh"), "pk6", source="imported")
    with pytest.raises(ValueError, match="cannot go"):
        bank.escape_rope(b, typhlosion, helper=helper)


def _packaged(monkeypatch, tmp_path):
    """Act as PokeUDS.exe unzipped into tmp_path/app, with the old data folder at tmp_path/appdata."""
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(app / "PokeUDS.exe"))
    monkeypatch.setattr(bank, "PORTABLE_ON", (sys.platform,))      # the Windows app's behaviour, on any test machine
    monkeypatch.delenv("POKEUDS_DATA", raising=False)
    # The old data folder: %APPDATA%\PokeUDS on Windows, elsewhere on Linux and macOS, so set it outright.
    monkeypatch.setattr(bank, "data_dir", lambda: tmp_path / "appdata" / "PokeUDS")
    monkeypatch.setattr(bank, "system_data_dir", lambda: tmp_path / "appdata" / "PokeUDS")
    monkeypatch.setattr(bank, "_portable", {})
    return app


def test_packaged_app_banks_beside_the_exe(monkeypatch, tmp_path):
    app = _packaged(monkeypatch, tmp_path)
    legacy = tmp_path / "appdata" / "PokeUDS" / "bank"
    legacy.mkdir(parents=True)
    (legacy / "old.pk7").write_bytes(b"x" * 232)
    (legacy / "received.json").write_text("{}", encoding="utf-8")
    (legacy / "Gen 6").mkdir()                              # an old bank a newer from-source copy sorted already
    (legacy / "Gen 6" / "sorted.pk6").write_bytes(b"x" * 232)
    assert bank.default_folder() == app / "Bank"
    assert sorted(p.name for p in (app / "Bank").iterdir()) == ["Gen 6", "old.pk7", "received.json"]
    assert (legacy / "old.pk7").is_file()                   # copied, not moved
    bank.Bank().entries()                                   # opening it tidies received.json into .info, old.pk7 into Gen 7
    assert sorted(p.name for p in (app / "Bank").iterdir()) == [".info", "Gen 6", "Gen 7"]
    assert (app / "Bank" / "Gen 7" / "old.pk7").is_file() and (app / "Bank" / "Gen 6" / "sorted.pk6").is_file()
    # Copied once: what the player removes from the new bank does not come back on the next start.
    (app / "Bank" / "Gen 7" / "old.pk7").unlink()
    monkeypatch.setattr(bank, "_portable", {})
    assert bank.default_folder() == app / "Bank" and not (app / "Bank" / "old.pk7").exists()
    assert not (app / "Bank" / "Gen 7" / "old.pk7").exists()


def test_packaged_app_falls_back_when_its_folder_is_read_only(monkeypatch, tmp_path):
    _packaged(monkeypatch, tmp_path)

    def refuse(*a, **k):
        raise PermissionError("read-only")
    monkeypatch.setattr(bank.Path, "mkdir", refuse)
    assert bank.default_folder() == tmp_path / "appdata" / "PokeUDS" / "bank"


def test_the_mac_app_banks_in_its_data_folder(monkeypatch, tmp_path):
    app = _packaged(monkeypatch, tmp_path)
    monkeypatch.setattr(bank, "PORTABLE_ON", ("win32",))
    monkeypatch.setattr("sys.platform", "darwin")
    assert bank.default_folder() == tmp_path / "appdata" / "PokeUDS" / "bank"
    assert not (app / "Bank").exists()


def test_from_source_the_bank_stays_in_the_data_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("POKEUDS_DATA", str(tmp_path / "data"))
    assert bank.default_folder() == tmp_path / "data" / "bank"


def test_the_bank_shows_only_pokemon(b, tmp_path):
    out = tmp_path / "received"
    received.save(pk6(), out, game="xy", stamp="20261104-120000")
    gold = out / "cynda.pk2"
    gold.write_bytes(sample_mon(2, 155, 10, "CYNDA").to_file())
    gold.with_suffix(".mail").write_bytes(b"" * 47)
    b.sync_received(out)
    entry = b.set_note(b.entries()[0], "keep")
    assert sorted(p.name for p in b.folder.iterdir()) == [".info", "Gen 2", "Gen 6"]
    # the mail stays beside its .pk2, where trades read it
    assert sorted(p.suffix for p in (b.folder / "Gen 2").iterdir()) == [".mail", ".pk2"]
    assert [p.suffix for p in (b.folder / "Gen 6").iterdir()] == [".pk6"]
    assert sorted(p.name for p in b.info.iterdir()) == sorted([f"{e.id}.json" for e in b.entries()] + ["received.json"])
    assert entry.note == "keep"
    b.remove(entry)
    assert not (b.info / f"{entry.id}.json").exists()


def test_an_older_bank_is_tidied_into_info(tmp_path):
    folder = tmp_path / "old"
    first = bank.Bank(folder)
    entry, _ = first.deposit(pk6(), "pk6", source="built", note="from before")
    # the layout before .info: the record at the top, the .json beside it, received.json at the top
    entry.path.replace(folder / entry.path.name)
    entry.path.parent.rmdir()
    (first.info / f"{entry.id}.json").replace(folder / f"{entry.id}.json")
    (folder / "received.json").write_text('{"x": ["a.pk6"]}', encoding="utf-8")
    first.info.rmdir()
    bank._tidied.clear()
    again = bank.Bank(folder)
    assert again.entries()[0].note == "from before" and again.entries()[0].source == "built"
    assert sorted(p.name for p in folder.iterdir()) == [".info", "Gen 6"]
    assert [p.name for p in (folder / "Gen 6").iterdir()] == [f"{entry.id}.pk6"]
    assert again._seen("x") == ["a.pk6"]


def test_a_received_vc_pokemon_keeps_its_titles_language(b, tmp_path):
    """The file name carries the language of the title it came from; the bank keeps it and migration hands it on."""
    from pokeuds.vc import trade as vc_trade

    assert bank.received_language("20261110-120000_gsc-de_trade1_155_CYNDA_00001.pk2") == "de"
    assert bank.received_language("20261110-120000_timecapsule-jp_trade2_025_x_00001.pk1") == "jp"
    assert bank.received_language("20261110-120000_rby_trade1_025_PIKA_00001.pk1") == ""        # an older file
    assert bank.received_language("20261104-120000_xy_trade1_653_Fennekin_12345678.pk6") == ""
    out = tmp_path / "received"
    german = vc_trade.save(sample_mon(2, 155, 10, "FEURIGEL"), out, game="gsc", trade=1, stamp="20261110-120000",
                           language="de")
    plain = vc_trade.save(sample_mon(1, 25, 12, "PIKA"), out, game="rby", trade=2, stamp="20261110-120000")
    assert "_gsc-de_trade1_" in german.name and "_rby_trade2_" in plain.name
    added = {e.extension: e for e in b.sync_received(out)}
    assert added["pk2"].meta["language"] == "de" and "language" not in added["pk1"].meta
    helper = FakeHelper()
    bank.migrate(b, added["pk2"], vc_game="gold", helper=helper)
    bank.migrate(b, added["pk1"], vc_game="red", helper=helper)
    assert helper.languages == ["de", ""]


def _flatten(b: bank.Bank) -> None:
    """The layout before the generation folders: every record (and mail) at the top of the bank."""
    for name in bank.FOLDERS.values():
        place = b.folder / name
        if place.is_dir():
            for path in place.iterdir():
                path.replace(b.folder / path.name)
            place.rmdir()
    bank._tidied.clear()


def test_new_records_go_in_their_generation_folder(b):
    made = [b.deposit(sample_mon(1, 4, 12, "CHARMY").to_file(), "pk1", source="imported")[0],
            b.deposit(sample_mon(2, 155, 10, "CYNDA").to_file(), "pk2", source="imported", mail=b"\x01" * 47)[0],
            b.deposit(pk6(), "pk6", source="imported")[0],
            b.deposit(make_pk8(species=26, form=1, nickname="Zappy", version=36), "pk8", source="migrated")[0]]
    assert [e.path.parent.name for e in made] == ["Gen 1", "Gen 2", "Gen 6", "Gen 8"]
    assert made[1].has_mail and made[1].path.with_suffix(".mail").parent.name == "Gen 2"
    assert all(b.find(e.id).path == e.path for e in made) and len(b.entries()) == 4


def test_a_flat_bank_is_sorted_into_generation_folders(tmp_path):
    """An older bank (a folder chosen in Settings here) moves into the generation folders once, when it is opened:
    each record keeps its .info, a .pk2 its mail, the GTS its listings, and a trade queue finds the moved files."""
    from pokeuds.app import gts
    from tests.test_gts import EEVEE, legal

    b = bank.Bank(tmp_path / "chosen")
    out = tmp_path / "received"
    received.save(pk6(species=133, nickname="Vee", ec=5), out, game="xy", stamp="20261104-120000")
    b.sync_received(out)
    red, _ = b.deposit(sample_mon(1, 4, 12, "CHARMY").to_file(), "pk1", source="imported", note="first")
    gold, _ = b.deposit(sample_mon(2, 155, 10, "CYNDA").to_file(), "pk2", source="imported", mail=b"\x01" * 47,
                        extra={"language": "de"})
    sparky, _ = b.deposit(pk6(), "pk6", source="built", note="keep")
    rope, _ = bank.escape_rope(b, red, vc_game="red", helper=FakeHelper())        # its .pk7 and .pk8 copies
    market = gts.Gts(b, check=legal)
    listing = market.list_pokemon(b.deposit(pk6(species=26, nickname="Volt", ec=9), "pk6", source="imported")[0],
                                  [EEVEE], "Me")
    before = {e.id: (e.extension, e.source, e.note, e.has_mail, e.legal, e.data()) for e in b.entries()}
    info = sorted(p.name for p in b.info.iterdir())
    _flatten(b)
    flat = sorted(p.name for p in b.folder.iterdir() if p.is_file())
    assert len(flat) == len(before) + 1 and f"{gold.id}.mail" in flat                     # the records and the mail
    queued = str(b.folder / sparky.path.name)                                              # a trade queue's old path

    again = bank.Bank(b.folder)                                                            # opening it sorts it
    assert sorted(p.name for p in again.folder.iterdir()) == [".gts", ".info", "Gen 1", "Gen 2", "Gen 6", "Gen 7", "Gen 8"]
    entries = again.entries()
    assert {e.id: (e.extension, e.source, e.note, e.has_mail, e.legal, e.data()) for e in entries} == before
    assert all(e.path.parent == again.folder / bank.FOLDERS[e.extension] for e in entries)
    assert (again.folder / "Gen 2" / f"{gold.id}.mail").read_bytes() == b"\x01" * 47
    assert sorted(p.name for p in again.info.iterdir()) == info                            # .info is left as it was
    assert bank.language_of(again.find(gold.id).path) == "de"
    assert bank.swsh_copy(again, again.find(red.id)).id == rope.id                         # the Escape Rope's links hold
    assert again.sync_received(out) == []                                                  # received.json still read
    assert bank.current_path(queued) == str(again.folder / "Gen 6" / sparky.path.name)
    assert bank.current_path(out / "x.pk6") == str(out / "x.pk6")                          # not a moved record

    # The GTS listing stayed in .gts, the bank's records in their folders count as held, and a withdrawn listing's
    # Pokemon comes back into its generation folder.
    market = gts.Gts(again, check=legal)
    [item] = market.items()
    assert item.path == listing.path and item.status == "open" and bank.parse("x.pk6", item.record()).dex == 26
    held = market.ours().held
    assert all(gts._digest(e.path) in held for e in entries)
    market.withdraw(item)
    back = next(e for e in again.entries() if e.dex == 26)
    assert back.path.parent == again.folder / "Gen 6"

    # Once is enough: a second opening finds nothing at the top to move.
    bank._tidied.clear()
    assert len(bank.Bank(again.folder).entries()) == len(before) + 1


def test_sorting_never_overwrites_and_tries_again(b, monkeypatch):
    import shutil

    one, _ = b.deposit(pk6(), "pk6", source="imported")
    two, _ = b.deposit(pk6(species=133, nickname="Vee", ec=7), "pk6", source="imported")
    three, _ = b.deposit(sample_mon(2, 155, 10, "CYNDA").to_file(), "pk2", source="imported", mail=b"\x01" * 47)
    shutil.copy2(one.path, b.folder / one.path.name)          # the same file at the top as well: that copy goes
    other = pk6(species=133, nickname="Other", ec=7)
    (b.folder / two.path.name).write_bytes(other)             # a different file of the same name: neither is replaced
    for path in (three.path, three.path.with_suffix(".mail")):
        path.replace(b.folder / path.name)                    # another program has this .pk2 open
    real = bank._move

    def busy(source, target):
        if source.suffix == ".pk2":
            raise PermissionError("in use")
        real(source, target)

    monkeypatch.setattr(bank, "_move", busy)
    bank._tidied.clear()
    again = bank.Bank(b.folder)
    assert not (b.folder / one.path.name).exists() and one.path.is_file()
    assert (b.folder / two.path.name).read_bytes() == other and two.path.read_bytes() == pk6(species=133, nickname="Vee", ec=7)
    stuck = again.find(three.id)
    assert stuck.path == b.folder / three.path.name and stuck.has_mail     # still listed, its mail kept beside it
    assert b.folder.resolve() not in bank._tidied                          # to be tried again
    monkeypatch.setattr(bank, "_move", real)
    moved = bank.Bank(b.folder).find(three.id)
    assert moved.path == three.path and moved.has_mail and not (b.folder / f"{three.id}.mail").exists()
