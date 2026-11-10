"""The demo radio: a simulated 3DS on a simulated air, for trying the app without hardware.

``--radio demo:red`` (or blue, yellow, gold, silver, crystal; ``gold-tc`` etc. for a Gen 2 console in
the Time Capsule) puts a modelled console hosting from the VC link menu next to PokeUDS. It runs the
same UDS, Pia and Game Boy code a real trade does, against ``pokeuds.vc.model``.

``--radio demo:x`` (or y, or, as) puts a Generation 6 console in the PSS next to PokeUDS: PokeUDS's own
Gen 6 engine playing the console's part. It waits a few seconds to be asked for a trade, then asks
PokeUDS itself, and offers a Pikachu.

Both are development and demonstration aids: they prove nothing about a real 3DS.
"""

from __future__ import annotations

import logging
import struct
import threading
import time

from .. import gbpk
from ..vc import gbdata
from .sim import Air, SimRadio

log = logging.getLogger(__name__)

DEMO_KEY = b"PokeUDS demo key"      # stands in for the console key on the simulated air only


def sample_mon(generation: int, dex: int, level: int, nickname: str, ot: str = "DEMO", ot_id: int = 4242) -> gbpk.Record:
    """A plausible party Pokemon: stats from base stats at middling DVs, no stat experience."""
    hp, atk, de, spd, sat, sdf = gbdata.GEN2_BASE[dex][0]
    dv = 8
    stat = lambda base: gbpk.calc_stat(base, dv, 0, level)
    max_hp = (base_hp := ((hp + dv) * 2) * level // 100 + level + 10)
    exp = level ** 3
    if generation == 1:
        d = bytearray(gbpk.GEN1_STRUCT)
        d[0] = gbpk.dex_to_index(dex)
        struct.pack_into(">H", d, 1, max_hp)
        d[3] = level
        d[5], d[6] = gbdata.GEN2_BASE[dex][1]
        d[7] = 45
        d[8:12] = bytes([33, 45, 0, 0])                     # Tackle, Growl
        struct.pack_into(">H", d, 12, ot_id)
        d[14:17] = exp.to_bytes(3, "big")
        d[27:29] = bytes([0x88, 0x88])
        d[29:33] = bytes([35, 40, 0, 0])
        d[33] = level
        struct.pack_into(">HHHHH", d, 34, max_hp, stat(atk), stat(de), stat(spd), stat(gbdata.KANTO_SPECIAL[dex]))
        return gbpk.Record(1, bytes(d), gbpk.encode_name(ot), gbpk.encode_name(nickname))
    d = bytearray(gbpk.GEN2_STRUCT)
    d[0] = dex
    d[2:6] = bytes([33, 45, 0, 0])
    struct.pack_into(">H", d, 6, ot_id)
    d[8:11] = exp.to_bytes(3, "big")
    d[21:23] = bytes([0x88, 0x88])
    d[23:27] = bytes([35, 40, 0, 0])
    d[27] = 70
    d[31] = level
    struct.pack_into(">H", d, 34, max_hp)
    struct.pack_into(">HHHHHH", d, 36, max_hp, stat(atk), stat(de), stat(spd), stat(sat), stat(sdf))
    return gbpk.Record(2, bytes(d), gbpk.encode_name(ot), gbpk.encode_name(nickname))


class SimulatedVcHost:
    """A 3DS hosting from the VC link menu: UDS host, the Pia host session, the modelled game."""

    def __init__(self, air: Air, key: bytes, title, model) -> None:
        import os

        from ..games import vc as titles
        from ..uds import Station

        self.station = Station(SimRadio(air), bytes.fromhex("02000000AA01"), key, name="DEMO")
        self.station.start(titles.CHANNEL)
        self.appdata = b"\x08" + os.urandom(7) + b"\x01" + bytes(7)
        self.station.host(wlan_comm_id=title.comm_id, passphrase=titles.PASSPHRASE, channel=titles.CHANNEL,
                          max_nodes=2, application_data=self.appdata)
        self.model = model
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="demo-console")
        self.thread.start()

    def _run(self) -> None:
        from ..uds import BROADCAST_NODE
        from ..vc import pia
        from ..vc.session import Config, Role, Session, State

        while not self.stop.is_set() and not any(p.connected for p in self.station.peers.values()):
            time.sleep(0.01)
        send = lambda raw, bc: self.station.send(BROADCAST_NODE if bc else 2, pia.UDS_CHANNEL, raw)
        session = Session(Role.HOST, send, Config(name="DEMO", application_data=self.appdata))
        t0 = time.monotonic()
        now = lambda: int((time.monotonic() - t0) * 1000)
        session.start(now())
        game, first = self.model.run(), False
        while not self.stop.is_set():
            try:
                packet = self.station.recv(pia.UDS_CHANNEL, timeout=0.005)
                while packet is not None:
                    session.receive(now(), packet.payload)
                    packet = self.station.recv(pia.UDS_CHANNEL, timeout=0)
                session.poll(now())
                if session.state is State.JOINED and not first:
                    first = session.queue_unit(next(game))
                for byte in session.pop_units():
                    if not self.model.done:
                        session.queue_unit(game.send(byte))
                session.flush(now())
            except Exception:
                log.exception("demo console")
                return
            if self.model.done and not session.send_flushed > session.send_acked:
                time.sleep(1.0)
                session._emit(now(), pia.bye(), broadcast=True)   # the demo console leaves the room
                return

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=2)
        self.station.close()


class SimulatedGen6Console:
    """A Generation 6 console in the PSS on channel 6, played by PokeUDS's own engine."""

    VERSIONS = {"x": "X", "y": "Y", "or": "Omega Ruby", "as": "Alpha Sapphire"}

    def __init__(self, air: Air, key: bytes, version: str) -> None:
        import tempfile
        from pathlib import Path

        from ..games import get, profile
        from ..games.gen6 import trade, wire
        from ..uds import Station

        self.name = self.VERSIONS[version]
        game = get("xy" if version in ("x", "y") else "oras")
        self.station = Station(SimRadio(air), bytes.fromhex("02000000AA06"), key, name="DEMO")
        self.station.start(6)
        self.folder = Path(tempfile.mkdtemp(prefix="pokeuds-demo-"))
        me = wire.Trainer.new("DEMO", 4242, 2424, wire.VERSION_CODES[self.name], "DEMO")
        self.trade = trade.Gen6Trade(self.station, game, profile.load(game.key), demo_pk6(), self.folder, me,
                                     channels=(6,), announce=False)
        self.thread = threading.Thread(target=self._run, daemon=True, name="demo-gen6-console")
        self.thread.start()

    def _run(self) -> None:
        try:
            try:
                self.trade.wait_to_be_asked(4.0)
            except TimeoutError:
                self.trade.ask(120.0)
        except Exception as exc:
            log.info("demo console stopped: %s", exc)

    def close(self) -> None:
        self.station.close()
        self.thread.join(timeout=2)


def demo_pk6() -> bytes:
    """The Pikachu the demo Gen 6 console offers, party stats filled in by hand (no PKHeX needed)."""
    from .. import pkx

    d = bytearray(pkx.PARTY_SIZE)
    struct.pack_into("<I", d, 0x00, 0x1DEC0DE5)           # encryption constant
    struct.pack_into("<HH", d, 0x08, 25, 0)               # species, held item
    struct.pack_into("<HH", d, 0x0C, 4242, 2424)          # TID, SID
    struct.pack_into("<I", d, 0x10, 1000)                 # experience
    d[0x14] = 9                                           # ability: Static
    struct.pack_into("<I", d, 0x18, 0x12345678)           # PID
    d[0x40:0x5A] = "Pikachu".encode("utf-16-le").ljust(26, b"\0")
    struct.pack_into("<4H", d, 0x5A, 84, 39, 98, 0)       # moves: Thunder Shock, Tail Whip, Quick Attack
    d[0xB0:0xCA] = "DEMO".encode("utf-16-le").ljust(26, b"\0")
    d[0xDE] = 10                                          # met level
    d[0xDF] = 24                                          # origin: X
    d[0xE3] = 2                                           # language: English
    d[0xEC] = 10                                          # level
    struct.pack_into("<7H", d, 0xF0, 30, 30, 18, 17, 19, 20, 26)   # current HP, then HP, Atk, Def, Spe, SpA, SpD
    return pkx.fix_checksum(bytes(d))


class DemoRadio(SimRadio):
    def __init__(self, spec: str) -> None:
        from ..games import vc as titles
        from ..vc import gbtext, link
        from ..vc.model import Model3DS

        key = (spec or "red").lower()
        if key in SimulatedGen6Console.VERSIONS:
            self.air = Air()
            super().__init__(self.air)
            self.console = SimulatedGen6Console(self.air, DEMO_KEY, key)
            self.title = self.console
            return
        time_capsule = key.endswith("-tc")
        key = key.removesuffix("-tc")
        if key not in titles.TITLES:
            raise ValueError(f"demo radio: unknown title {key!r}; use red, blue, yellow, gold, silver, crystal, x, y, or, as")
        self.title = titles.TITLES[key]
        gen = self.title.generation
        if time_capsule and self.title.family == "kr":
            raise ValueError("demo radio: Korean Gold and Silver have no Time Capsule")
        mode = link.TIME_CAPSULE if time_capsule or gen == 1 else link.GEN2
        wire_gen = mode.generation
        mons = ([sample_mon(1, 133, 25, "EEVEE"), sample_mon(1, 25, 18, "PIKACHU")] if wire_gen == 1
                else [sample_mon(2, 175, 5, "TOGEPI"), sample_mon(2, 152, 12, "CHIKORITA")])
        language = self.title.language            # a Japanese or Korean demo holds its own language's Pokemon
        mons = [m.in_language("en", language) for m in mons]
        party = link.Party(gbtext.trainer_name("DEMO", language), 4242, mons)
        self.air = Air()
        super().__init__(self.air)
        self.model = Model3DS(mode, party, console_generation=gen, family=self.title.family)
        self.console = SimulatedVcHost(self.air, DEMO_KEY, self.title, self.model)

    def describe(self) -> str:
        return f"demo radio: a simulated 3DS running {self.title.name}"

    def close(self) -> None:
        super().close()
        self.console.close()
        self.air.close()
