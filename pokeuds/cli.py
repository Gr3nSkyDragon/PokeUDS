"""Command line: ``python -m pokeuds <command>``.

    probe    check that the radio answers
    scan     list UDS networks on the air (any 3DS game), with their decrypted node lists
    sniff    decrypt a session between two other consoles into a transcript
    analyze  summarize an Azahar log or a sniff transcript, and find Pokemon inside
    pkx      show a .pk6/.pk7 (or .ek6/.ek7) file
    trade    trade with a retail console (xy, oras, sm, usum), joining or hosting
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from . import pkx
from .util import mac_str, parse_hex, parse_mac, random_mac


def _add_radio(p: argparse.ArgumentParser) -> None:
    p.add_argument("--radio", default="esp32",
                   help="esp32[:PORT] (ESP32-S3, found by USB id) or ldnd[:PIPE] (USB adapter via ldnd.exe 0.0.5)")
    p.add_argument("--mac", type=parse_mac, help="MAC address to use on the air (default: random, locally administered)")
    p.add_argument("--channel", type=int, default=11, help="starting channel")


def _add_keys(p: argparse.ArgumentParser, required: bool = True) -> None:
    p.add_argument("--keys", required=required, help="aes_keys.txt holding the keyslot 0x2D key")


def _uds_key(path: str | None) -> bytes | None:
    if not path:
        return None
    from .keys import KeyFile

    return KeyFile.load(path).uds_key()


def cmd_probe(args) -> int:
    from .radio import open_radio

    radio = open_radio(args.radio)
    try:
        radio.start(args.channel, args.mac or random_mac())
        print(f"OK: {radio.describe()}")
    finally:
        radio.close()
    return 0


def _print_beacon(b) -> None:
    info = b.info
    names = ", ".join(f"{n.network_node_id}:{n.name or '?'}" for n in (b.nodes or []) if not n.empty())
    print(f"{mac_str(b.host)} ch {b.channel:>2} rssi {b.rssi if b.rssi is not None else '?':>4}  "
          f"comm {info.wlan_comm_id:08X} id {info.id} net {info.network_id:08X}  "
          f"nodes {info.total_nodes}/{info.max_nodes} [{names or ('undecryptable' if b.nodes is None else '-')}]  "
          f"appdata {len(info.application_data)} B{'' if b.sha1_valid else ' (bad SHA-1)'}")
    if info.application_data:
        print(f"    appdata {info.application_data.hex()}")


def cmd_scan(args) -> int:
    from .radio import open_radio
    from .uds import Station

    station = Station(open_radio(args.radio), args.mac or random_mac(), None)
    seen: dict = {}

    def heard(b):
        key = (b.host, b.info.network_id, b.info.total_nodes, b.info.application_data)
        if key not in seen:
            seen[key] = b
            _print_beacon(b)

    station.on_beacon = heard
    station.start(args.channel)
    try:
        print(f"scanning {args.seconds:.0f} s on channels 1, 6, 11 ...")
        station.scan(args.seconds, wlan_comm_id=args.comm_id)
    except KeyboardInterrupt:
        pass
    finally:
        station.close()
    if args.json:
        Path(args.json).write_text(json.dumps([
            {"host": mac_str(b.host), "channel": b.channel, "wlan_comm_id": b.info.wlan_comm_id, "id": b.info.id,
             "network_id": b.info.network_id, "total_nodes": b.info.total_nodes, "max_nodes": b.info.max_nodes,
             "application_data": b.info.application_data.hex(),
             "nodes": [{"node": n.network_node_id, "name": n.name, "fcs": n.friend_code_seed} for n in (b.nodes or [])]}
            for b in seen.values()], indent=2))
    print(f"{len({k[0] for k in seen})} host(s) heard")
    return 0


def _profile_passphrase(args) -> tuple[bytes, int | None]:
    if args.passphrase:
        return parse_hex(args.passphrase), args.comm_id
    if args.game:
        from .games import profile

        prof = profile.load(args.game, args.profile)
        if not prof.passphrase:
            raise SystemExit(f"{prof.source} has no passphrase_hex yet; pass --passphrase (docs/captures.md)")
        return prof.passphrase, args.comm_id or prof.wlan_comm_id
    raise SystemExit("give --passphrase HEX or --game GAME")


def cmd_sniff(args) -> int:
    from .capture.transcript import Message
    from .radio import open_radio
    from .uds.sniffer import Sniffer

    passphrase, comm_id = _profile_passphrase(args)
    radio = open_radio(args.radio)
    radio.start(args.channel, args.mac or random_mac())
    sniffer = Sniffer(radio, _uds_key(args.keys), passphrase, wlan_comm_id=comm_id)
    out = open(args.out, "w", encoding="utf-8") if args.out else None
    count = 0

    def network(b):
        print(f"network: ", end="")
        _print_beacon(b)
        radio.set_channel(b.channel)
        radio.watch(b.host)

    def packet(p):
        nonlocal count
        if p.kind == "securedata" and p.packet and not p.packet.management:
            msg = Message(count, p.time, p.role, p.packet.channel, p.packet.sequence, p.packet.src_node,
                          p.packet.dest_node, p.packet.payload, "pokeuds-sniff")
            count += 1
            print(f"{p.role:>6} ch {msg.channel} {msg.src_node}->{msg.dest_node} seq {msg.sequence:5} "
                  f"{len(msg.payload):4} B  {msg.payload[:24].hex()}")
            if out:
                out.write(json.dumps({**msg.to_json(), "retry": p.retry, "pn": p.pn}) + "\n")
                out.flush()
        elif p.kind.startswith("eapol"):
            print(f"{p.role:>6} {p.kind} from {mac_str(p.transmitter)}")

    sniffer.on_network = network
    print("sniffing; start the trade on both consoles (Ctrl+C to stop)")
    try:
        sniffer.run(packet, args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        radio.close()
        if out:
            out.close()
    print(f"{count} game messages, {sniffer.ccmp_failures} CCMP failures")
    return 0


def cmd_analyze(args) -> int:
    from .capture import transcript

    messages, facts = transcript.load(args.capture)
    print(transcript.summarize(messages))
    if facts.passphrases:
        print("passphrase candidates (profiles/<game>.toml network.passphrase_hex):")
        for value in sorted(facts.passphrases):
            print(f"    {value}")
    if facts.wlan_comm_ids:
        print("wlan comm ids: " + ", ".join(f"0x{c:08X}" for c in sorted(facts.wlan_comm_ids)))
    for hosted in facts.hosting:
        print("hosted network: " + ", ".join(f"{k}={v}" for k, v in hosted.items()
                                             if k in ("wlanCommId", "id", "channel", "maxNodes", "applicationDataSize")))
    for record in transcript.find_pokemon(messages):
        s = record.summary
        m = messages[record.message]
        print(f"Pokemon in message {record.message} ({m.direction}, channel {m.channel}) at offset {record.offset}: "
              f"{record.size} B, species {s.species}, '{s.nickname}', OT '{s.ot_name}', PID {s.pid:08X}")
        if args.extract:
            folder = Path(args.extract)
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"msg{record.message}_{s.species:03d}_{s.pid:08X}.{pkx.guess_format(record.record).extension}"
            target.write_bytes(pkx.to_stored(record.record))
            print(f"    -> {target}")
    if args.out:
        transcript.write_jsonl(messages, args.out)
        print(f"transcript written to {args.out}")
    return 0


def cmd_pkx(args) -> int:
    record = pkx.load(args.file)
    s = pkx.summary(record)
    from .games import VERSION_NAMES

    version = VERSION_NAMES.get(s.version, f"version {s.version}")
    print(f"species {s.species}  nickname '{s.nickname}'  OT '{s.ot_name}' {s.tid:05d}/{s.sid:05d}  "
          f"PID {s.pid:08X}  EC {s.encryption_constant:08X}  language {s.language}  from {version}"
          f"{'  shiny' if s.shiny else ''}{'  egg' if s.is_egg else ''}")
    print(f"checksum valid, {len(record)} bytes")
    return 0


def cmd_trade(args) -> int:
    from .games import get, profile
    from .games.trade import LocalTrade, ProtocolNotMapped
    from .radio import open_radio
    from .uds import Station

    game = get(args.game)
    offer = pkx.load(args.offer)
    try:
        pkx.check_format(offer, game.record)
    except ValueError as exc:
        raise SystemExit(f"{args.offer}: {exc}; {game.name} cannot receive it")
    prof = profile.load(game.key, args.profile)
    if missing := prof.missing():
        raise SystemExit(f"{prof.source} is missing {', '.join(missing)}; these come from captures "
                         "(docs/captures.md)")
    station = Station(open_radio(args.radio), args.mac or random_mac(), _uds_key(args.keys), name=args.name)
    station.start(args.channel)
    trade = LocalTrade(station, game, prof, offer, Path(args.output))
    try:
        if args.role == "host":
            appdata = parse_hex(args.application_data) if args.application_data is not None else None
            trade.host(appdata)
            print(f"hosting {game.name} on channel {station.network.channel}; waiting {args.seconds:.0f} s "
                  "for the console to join")
            trade.wait_for_partner(args.seconds)
        else:
            trade.join(args.seconds)
        print(f"connected as {station.role.value}, node {station.node_id}")
        trade.run(args.listen)
    except ProtocolNotMapped as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 2
    except TimeoutError as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        station.close()
    return 0


GAME_KEYS = ["xy", "oras", "sm", "usum"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pokeuds", description="3DS local wireless (UDS) trading toolkit")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe", help="check that the radio answers")
    _add_radio(p)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("scan", help="list UDS networks on the air")
    _add_radio(p)
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--comm-id", type=lambda s: int(s, 0), help="only this game's networks")
    p.add_argument("--json", help="also write what was heard to this file")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("sniff", help="decrypt another pair of consoles' session")
    _add_radio(p)
    _add_keys(p)
    p.add_argument("--game", choices=GAME_KEYS, help="take the passphrase and comm id from the game profile")
    p.add_argument("--profile", help="profile file (default profiles/<game>[.local].toml)")
    p.add_argument("--passphrase", help="the game's UDS passphrase as hex")
    p.add_argument("--comm-id", type=lambda s: int(s, 0))
    p.add_argument("--seconds", type=float)
    p.add_argument("--out", help="write game messages as JSON lines (for `pokeuds analyze`)")
    p.set_defaults(func=cmd_sniff)

    p = sub.add_parser("analyze", help="summarize a capture and find Pokemon records in it")
    p.add_argument("capture", help="an Azahar log (azahar_uds) or a `pokeuds sniff --out` file")
    p.add_argument("--out", help="write the parsed transcript as JSON lines")
    p.add_argument("--extract", help="write every Pokemon found as .pk6/.pk7 into this folder")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("pkx", aliases=["pk6", "pk7"], help="show a .pk6/.pk7 (or .ek6/.ek7) file")
    p.add_argument("file")
    p.set_defaults(func=cmd_pkx)

    p = sub.add_parser("trade", help="trade with a retail console")
    p.add_argument("game", choices=GAME_KEYS)
    _add_radio(p)
    _add_keys(p)
    p.add_argument("--offer", required=True, help="the .pk6 (X/Y, ORAS) or .pk7 (SM, USUM) to send")
    p.add_argument("--output", default="received", help="folder for the Pokemon received (default: received/)")
    p.add_argument("--role", choices=["join", "host"], default="join",
                   help="join the console's network (default) or host one for it to join")
    p.add_argument("--application-data", metavar="HEX",
                   help="as host: beacon application data, overriding the profile's application_data_hex")
    p.add_argument("--name", default="PokeUDS", help="the Mii name other consoles see")
    p.add_argument("--profile", help="profile file (default profiles/<game>[.local].toml)")
    p.add_argument("--seconds", type=float, default=60.0,
                   help="how long to look for the console (join) or wait for it to join (host)")
    p.add_argument("--listen", type=float, default=20.0, help="how long to record the console once connected")
    p.set_defaults(func=cmd_trade)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING - 10 * min(args.verbose, 2),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
