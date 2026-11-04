"""USB Wi-Fi adapter backend through ldnd.exe (Windows).

Use the legacy ldnd 0.0.5 build (unlimitedcoder2's gist), as azahar_uds does: it forwards Linux
socket calls over the named pipe ``\\\\.\\pipe\\ldnd`` to the Linux driver it runs for the adapter.
The ldnrs revision speaks a different, LDN-level protocol and does not work here.

Pipe frame (both directions), all little endian::

    | op:u8 | socket_id:u32 | arg0:u32 | arg1:u32 | arg2:u32 | blob_length:u32 | blob |

Requests (SOCKET, BIND, SETSOCKOPT, GETSOCKNAME, START) get a REPLY with the error in arg0; SENDTO
and CLOSE get none. Received socket data arrives as DATA frames at any time, interleaved with
replies.

The radio is an *active* monitor interface carrying our MAC, so the adapter's firmware sends the
timing-critical ACKs for unicast frames addressed to us while every frame stays raw. azahar_uds
additionally runs a companion AP interface as an "ACK shell" on adapters whose active monitor mode
does not acknowledge; that is not ported yet (docs/hardware.md).
"""

from __future__ import annotations

import enum
import logging
import queue
import struct
import sys
import threading
import time
from concurrent.futures import Future
from typing import Callable, Protocol

from . import netlink as nl
from . import radiotap
from .base import Radio, RxFrame, channel_to_frequency, data_rate, is_group_addressed

log = logging.getLogger(__name__)

DEFAULT_PIPE = r"\\.\pipe\ldnd"
HEADER = struct.Struct("<BIIIII")
MAX_BLOB = 256 * 1024
MONITOR_NAME = "udsmon0"
STALE_INTERFACES = ("udsmon0", "udsap0")  # Ours, and azahar_uds's leftovers.
BEACON_INTERVAL = 0.1024


class Op(enum.IntEnum):
    SOCKET = 1
    BIND = 2
    SETSOCKOPT = 3
    GETSOCKNAME = 4
    START = 5
    SENDTO = 6
    CLOSE = 7
    REPLY = 8
    DATA = 9


class Stream(Protocol):
    def read_exact(self, count: int) -> bytes: ...
    def write(self, data: bytes) -> None: ...
    def available(self) -> int: ...
    def close(self) -> None: ...


class PipeStream:
    """A Windows named pipe opened as a client. ``available`` uses PeekNamedPipe so one thread can
    poll for input and write without a blocking read holding the synchronous handle."""

    def __init__(self, path: str = DEFAULT_PIPE) -> None:
        if sys.platform != "win32":
            raise RuntimeError("ldnd.exe is a Windows program; use the ESP32 radio on this platform")
        import ctypes
        import msvcrt
        from ctypes import wintypes

        self._file = open(path, "r+b", buffering=0)
        self._handle = msvcrt.get_osfhandle(self._file.fileno())
        self._peek = ctypes.windll.kernel32.PeekNamedPipe
        self._peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self._peek.restype = wintypes.BOOL
        self._dword = wintypes.DWORD

    def read_exact(self, count: int) -> bytes:
        out = bytearray()
        while len(out) < count:
            chunk = self._file.read(count - len(out))
            if not chunk:
                raise EOFError("ldnd closed the pipe")
            out += chunk
        return bytes(out)

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = self._file.write(view)
            if not written:
                raise EOFError("ldnd closed the pipe")
            view = view[written:]

    def available(self) -> int:
        import ctypes

        avail = self._dword(0)
        if not self._peek(self._handle, None, 0, None, ctypes.byref(avail), None):
            raise EOFError("ldnd pipe is gone")
        return avail.value

    def close(self) -> None:
        self._file.close()


class DaemonError(RuntimeError):
    pass


class LdndClient:
    """The socket calls ldnd forwards. Not thread safe: one thread owns the pipe."""

    def __init__(self, stream: Stream) -> None:
        self.stream = stream
        self._next_sid = 0
        self._pending: dict[int, list[bytes]] = {}

    def _write(self, op: int, sid: int, a0: int = 0, a1: int = 0, a2: int = 0, blob: bytes = b"") -> None:
        if len(blob) > MAX_BLOB:
            raise ValueError("ldnd frame blob exceeds 256 KiB")
        self.stream.write(HEADER.pack(op, sid, a0 & 0xFFFFFFFF, a1 & 0xFFFFFFFF, a2 & 0xFFFFFFFF, len(blob)) + blob)

    def read_frame(self) -> tuple[int, int, int, bytes]:
        op, sid, a0, _a1, _a2, length = HEADER.unpack(self.stream.read_exact(HEADER.size))
        if length > MAX_BLOB:
            raise ValueError("ldnd frame blob exceeds 256 KiB")
        return op, sid, a0, self.stream.read_exact(length) if length else b""

    def _request(self, op: int, sid: int, a0: int = 0, a1: int = 0, a2: int = 0, blob: bytes = b"", name: str = "") -> bytes:
        self._write(op, sid, a0, a1, a2, blob)
        while True:
            rop, rsid, ra0, rblob = self.read_frame()
            if rop == Op.DATA:
                self._pending.setdefault(rsid, []).append(rblob)
                continue
            if rop != Op.REPLY:
                raise DaemonError(f"expected a reply to {name}, got opcode {rop}")
            if rsid != sid:
                raise DaemonError(f"ldnd reply for socket {rsid}, expected {sid}")
            if ra0 != 0:
                raise DaemonError(f"ldnd {name} failed for socket {sid}: error {ra0}")
            return rblob

    def socket(self, domain: int, type_: int, protocol: int) -> int:
        self._next_sid = (self._next_sid + 1) & 0xFFFFFFFF or 1
        sid = self._next_sid
        self._request(Op.SOCKET, sid, domain, type_, protocol, name="socket")
        return sid

    def bind(self, sid: int, address: bytes) -> None:
        self._request(Op.BIND, sid, blob=address, name="bind")

    def getsockname(self, sid: int) -> bytes:
        return self._request(Op.GETSOCKNAME, sid, name="getsockname")

    def start(self, sid: int) -> None:
        self._request(Op.START, sid, name="start")

    def sendto(self, sid: int, data: bytes, flags: int = 0) -> None:
        self._write(Op.SENDTO, sid, flags, blob=data)

    def close_socket(self, sid: int) -> None:
        self._pending.pop(sid, None)
        self._write(Op.CLOSE, sid)  # ldnd sends no reply for CLOSE.

    def take_pending(self, sid: int) -> bytes | None:
        queue_ = self._pending.get(sid)
        return queue_.pop(0) if queue_ else None

    def receive(self, sid: int) -> bytes:
        if (data := self.take_pending(sid)) is not None:
            return data
        while True:
            op, rsid, _a0, blob = self.read_frame()
            if op == Op.DATA:
                if rsid == sid:
                    return blob
                self._pending.setdefault(rsid, []).append(blob)
            elif op == Op.REPLY:
                raise DaemonError("unexpected ldnd reply while waiting for socket data")


class Nl80211:
    """nl80211 and rtnetlink requests over ldnd."""

    def __init__(self, client: LdndClient) -> None:
        self.client = client
        self.seq = 0
        self.sid = client.socket(nl.AF_NETLINK, nl.SOCK_DGRAM, nl.NETLINK_GENERIC)
        client.bind(self.sid, nl.sockaddr_nl())
        self.port = nl.parse_sockaddr_nl_port(client.getsockname(self.sid))
        client.start(self.sid)
        self.family = self._resolve_family()

    def _next(self) -> int:
        self.seq += 1
        return self.seq

    def _messages(self, sid: int, seq: int):
        while True:
            for message in nl.parse_messages(self.client.receive(sid)):
                if message.seq == seq:
                    yield message

    def _ack(self, sid: int, seq: int, what: str) -> None:
        for message in self._messages(sid, seq):
            if message.type == nl.NLMSG_ERROR:
                if message.error_code() != 0:
                    raise DaemonError(f"{what} failed with netlink error {message.error_code()}")
                return

    def _resolve_family(self) -> int:
        seq = self._next()
        attrs = nl.attr_str(nl.CTRL_ATTR_FAMILY_NAME, "nl80211")
        self.client.sendto(self.sid, nl.genl_request(nl.GENL_ID_CTRL, 0, seq, self.port, nl.CTRL_CMD_GETFAMILY, attrs))
        for message in self._messages(self.sid, seq):
            if message.type == nl.NLMSG_ERROR:
                raise DaemonError(f"resolving nl80211 failed with netlink error {message.error_code()}")
            family = message.genl_attrs().get(nl.CTRL_ATTR_FAMILY_ID)
            if family and len(family) >= 2:
                return struct.unpack_from("<H", family)[0]
        raise DaemonError("nl80211 family not found")

    def command(self, cmd: int, attrs: bytes, what: str) -> None:
        seq = self._next()
        self.client.sendto(self.sid, nl.genl_request(self.family, nl.NLM_F_ACK, seq, self.port, cmd, attrs))
        self._ack(self.sid, seq, what)

    def interfaces(self) -> list[nl.Interface]:
        seq = self._next()
        self.client.sendto(self.sid, nl.genl_request(self.family, nl.NLM_F_DUMP, seq, self.port, nl.NL80211_CMD_GET_INTERFACE))
        found = []
        for message in self._messages(self.sid, seq):
            if message.type == nl.NLMSG_ERROR:
                raise DaemonError(f"listing interfaces failed with netlink error {message.error_code()}")
            if message.type == nl.NLMSG_DONE:
                return found
            found.append(nl.Interface.from_attrs(message.genl_attrs()))
            if not message.flags & nl.NLM_F_MULTI:
                return found
        return found

    def delete_interface(self, ifindex: int) -> None:
        self.command(nl.NL80211_CMD_DEL_INTERFACE, nl.attr_u32(nl.NL80211_ATTR_IFINDEX, ifindex), "delete interface")

    def create_monitor(self, wiphy: int, name: str, active: bool) -> None:
        flags = nl.attr(nl.NL80211_MNTR_FLAG_OTHER_BSS)
        if active:
            # mac80211 then uses the interface's MAC and ACKs unicast frames addressed to it.
            flags += nl.attr(nl.NL80211_MNTR_FLAG_ACTIVE)
        attrs = (nl.attr_u32(nl.NL80211_ATTR_WIPHY, wiphy) + nl.attr_str(nl.NL80211_ATTR_IFNAME, name)
                 + nl.attr_u32(nl.NL80211_ATTR_IFTYPE, nl.NL80211_IFTYPE_MONITOR)
                 + nl.attr(nl.NL80211_ATTR_MNTR_FLAGS | nl.NLA_F_NESTED, flags))
        self.command(nl.NL80211_CMD_NEW_INTERFACE, attrs, "create monitor interface")

    def set_channel(self, ifindex: int, channel: int) -> None:
        attrs = nl.attr_u32(nl.NL80211_ATTR_IFINDEX, ifindex) + nl.attr_u32(nl.NL80211_ATTR_WIPHY_FREQ, channel_to_frequency(channel))
        self.command(nl.NL80211_CMD_SET_CHANNEL, attrs, "set channel")

    def setlink(self, ifindex: int, *, up: bool | None = None, address: bytes | None = None) -> None:
        sid = self.client.socket(nl.AF_NETLINK, nl.SOCK_DGRAM, nl.NETLINK_ROUTE)
        try:
            self.client.bind(sid, nl.sockaddr_nl())
            port = nl.parse_sockaddr_nl_port(self.client.getsockname(sid))
            self.client.start(sid)
            self.client.sendto(sid, nl.rtnl_setlink(1, port, ifindex, up=up, address=address))
            self._ack(sid, 1, "set link")
        finally:
            self.client.close_socket(sid)


class LdndRadio(Radio):
    def __init__(self, pipe: str = DEFAULT_PIPE, stream_factory: Callable[[], Stream] | None = None) -> None:
        self.pipe = pipe
        self._stream_factory = stream_factory or (lambda: PipeStream(pipe))
        self.client: LdndClient | None = None
        self.nl: Nl80211 | None = None
        self.ifindex = 0
        self.packet_sid = 0
        self.active_monitor = False
        self._rx: queue.Queue[RxFrame] = queue.Queue(maxsize=4096)
        self._tx: queue.Queue[bytes] = queue.Queue()
        self._control: queue.Queue[tuple[Callable[[], object], Future]] = queue.Queue()
        self._beacon: bytes | None = None
        self._beacon_seq = 0
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._epoch = time.monotonic()

    def describe(self) -> str:
        mode = "active monitor" if self.active_monitor else "passive monitor (no hardware ACKs)"
        return f"ldnd on {self.pipe}, {MONITOR_NAME} ifindex {self.ifindex}, {mode}"

    def start(self, channel: int, mac: bytes) -> None:
        self.mac, self.channel = bytes(mac), channel
        self.client = LdndClient(self._stream_factory())
        self.nl = Nl80211(self.client)
        interfaces = self.nl.interfaces()
        for stale in [i for i in interfaces if i.name in STALE_INTERFACES]:
            log.info("removing stale %s (ifindex %d)", stale.name, stale.ifindex)
            self.nl.delete_interface(stale.ifindex)
        interfaces = self.nl.interfaces()
        physical = next((i for i in interfaces if i.iftype != nl.NL80211_IFTYPE_MONITOR), None)
        if physical is None:
            raise RuntimeError("ldnd reports no Wi-Fi adapter; is the adapter set up in ldnd?")
        try:
            self.nl.create_monitor(physical.wiphy, MONITOR_NAME, active=True)
            self.active_monitor = True
        except DaemonError as exc:
            log.warning("active monitor mode refused (%s); falling back to a passive monitor", exc)
            for leftover in [i for i in self.nl.interfaces() if i.name == MONITOR_NAME]:
                self.nl.delete_interface(leftover.ifindex)
            self.nl.create_monitor(physical.wiphy, MONITOR_NAME, active=False)
        monitor = next((i for i in self.nl.interfaces() if i.name == MONITOR_NAME), None)
        if monitor is None:
            raise RuntimeError(f"nl80211 accepted the monitor but {MONITOR_NAME} was not found")
        self.ifindex = monitor.ifindex
        if self.active_monitor:
            try:
                self.nl.setlink(self.ifindex, address=self.mac)
            except DaemonError as exc:
                log.warning("could not give %s our MAC (%s); unicast frames will not be ACKed", MONITOR_NAME, exc)
        self.nl.setlink(self.ifindex, up=True)
        self.nl.set_channel(self.ifindex, channel)
        # socket(2) takes the protocol in host order but Linux expects htons(ETH_P_ALL).
        self.packet_sid = self.client.socket(nl.AF_PACKET, nl.SOCK_RAW, 0x0300)
        self.client.bind(self.packet_sid, nl.sockaddr_ll(self.ifindex))
        self.client.start(self.packet_sid)
        self._worker = threading.Thread(target=self._run, name="ldnd-io", daemon=True)
        self._worker.start()
        log.info("%s on channel %d", self.describe(), channel)

    # The worker thread owns the pipe from here on; other threads hand it work.

    def _call(self, fn: Callable[[], object]) -> object:
        future: Future = Future()
        self._control.put((fn, future))
        return future.result(timeout=5.0)

    def _run(self) -> None:
        assert self.client is not None
        next_beacon = time.monotonic()
        try:
            while not self._stop.is_set():
                busy = False
                while True:
                    try:
                        fn, future = self._control.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        future.set_result(fn())
                    except Exception as exc:
                        future.set_exception(exc)
                for _ in range(8):
                    try:
                        mpdu = self._tx.get_nowait()
                    except queue.Empty:
                        break
                    self._inject(mpdu)
                    busy = True
                now = time.monotonic()
                if self._beacon is not None and now >= next_beacon:
                    self._inject(self._stamped_beacon(self._beacon))
                    next_beacon = max(next_beacon + BEACON_INTERVAL, now)
                while (pending := self.client.take_pending(self.packet_sid)) is not None:
                    self._deliver(pending)
                if self.client.stream.available() >= HEADER.size:
                    op, sid, _a0, blob = self.client.read_frame()
                    if op == Op.DATA and sid == self.packet_sid:
                        self._deliver(blob)
                    busy = True
                if not busy:
                    time.sleep(0.002)
        except Exception as exc:
            log.error("ldnd I/O stopped: %s", exc)

    def _inject(self, mpdu: bytes) -> None:
        self.client.sendto(self.packet_sid, radiotap.build(mpdu, rate=data_rate(mpdu), no_ack=is_group_addressed(mpdu)))

    def _stamped_beacon(self, mpdu: bytes) -> bytes:
        # A rising timestamp and sequence keep a retail scanner from treating repeats as stale.
        self._beacon_seq = (self._beacon_seq + 1) & 0x0FFF
        stamp = int((time.monotonic() - self._epoch) * 1_000_000)
        return mpdu[:22] + struct.pack("<H", self._beacon_seq << 4) + struct.pack("<Q", stamp) + mpdu[32:]

    def _deliver(self, packet: bytes) -> None:
        stripped = radiotap.strip(packet)
        if stripped is None:
            return
        mpdu, parsed = stripped
        if len(mpdu) < 10:
            return
        channel = radiotap.frequency_to_channel(parsed.channel_freq) or self.channel
        try:
            self._rx.put_nowait(RxFrame(mpdu, channel, parsed.signal))
        except queue.Full:
            log.warning("dropping a captured frame: receive queue full")

    def set_channel(self, channel: int) -> None:
        if channel == self.channel:
            return
        self._call(lambda: self.nl.set_channel(self.ifindex, channel))
        self.channel = channel

    def send(self, mpdu: bytes) -> None:
        self._tx.put(bytes(mpdu))

    def set_beacon(self, mpdu: bytes | None) -> None:
        self._beacon = bytes(mpdu) if mpdu else None

    def recv(self, timeout: float | None = None) -> RxFrame | None:
        try:
            return self._rx.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=1.0)
        if self.client is not None:
            try:
                if self.packet_sid:
                    self.client.close_socket(self.packet_sid)
                if self.nl and self.ifindex:
                    self.nl.delete_interface(self.ifindex)
            except Exception as exc:
                log.debug("ldnd cleanup: %s", exc)
            try:
                self.client.stream.close()
            except Exception:
                pass
            self.client = None
