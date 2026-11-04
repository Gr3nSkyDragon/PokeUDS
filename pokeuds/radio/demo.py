"""The demo radio: a simulated 3DS on a simulated air, for trying the app without hardware.

``--radio demo:red`` (or blue, yellow, gold, silver, crystal; ``gold-tc`` etc. for a Gen 2 console in
the Time Capsule) puts a modelled console hosting from the VC link menu next to PokeUDS. It runs the
same UDS, Pia and Game Boy code a real trade does, against ``pokeuds.vc.model``. It is a development
and demonstration aid: it proves nothing about a real 3DS.
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


class DemoRadio(SimRadio):
    def __init__(self, spec: str) -> None:
        from ..games import vc as titles
        from ..vc import link
        from ..vc.model import Model3DS

        key = (spec or "red").lower()
        time_capsule = key.endswith("-tc")
        key = key.removesuffix("-tc")
        if key not in titles.TITLES:
            raise ValueError(f"demo radio: unknown title {key!r}; use red, blue, yellow, gold, silver, crystal")
        self.title = titles.TITLES[key]
        gen = self.title.generation
        mode = link.TIME_CAPSULE if time_capsule or gen == 1 else link.GEN2
        wire_gen = mode.generation
        mons = ([sample_mon(1, 133, 25, "EEVEE"), sample_mon(1, 25, 18, "PIKACHU")] if wire_gen == 1
                else [sample_mon(2, 175, 5, "TOGEPI"), sample_mon(2, 152, 12, "CHIKORITA")])
        party = link.Party(gbpk.encode_name("DEMO"), 4242, mons)
        self.air = Air()
        super().__init__(self.air)
        self.model = Model3DS(mode, party, console_generation=gen)
        self.console = SimulatedVcHost(self.air, DEMO_KEY, self.title, self.model)

    def describe(self) -> str:
        return f"demo radio: a simulated 3DS running {self.title.name}"

    def close(self) -> None:
        super().close()
        self.console.close()
        self.air.close()
