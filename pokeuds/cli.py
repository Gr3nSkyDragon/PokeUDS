"""Command line: ``python -m pokeuds <command>``.

    probe    check that the radio answers
    scan     list UDS networks on the air (any 3DS game), with their decrypted node lists
    sniff    decrypt a session between two other consoles into a transcript
    analyze  summarize an Azahar log or a sniff transcript, and find Pokemon inside
    pkx      show a .pk6/.pk7 (or .ek6/.ek7) file
    vc       Virtual Console Gen 1/2: trade, show or convert a .pk1/.pk2
    trade    trade with a retail console (xy, oras, sm, usum), joining or hosting
    board    ESP32-S3 boards: list, check, download or flash esp32-uds-bridge
    build    build a legal Pokemon with the PKHeX helper (docs/pkhex.md)
    gift     Gen 6/7 Mystery Gift cards: list PKHeX's, show a .wc6/.wc7, make its Pokemon, build a custom card
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from . import pkx, status
from .util import mac_str, parse_hex, parse_mac, random_mac


def _add_radio(p: argparse.ArgumentParser) -> None:
    p.add_argument("--radio", default="esp32",
                   help="esp32[:PORT] (an ESP32 board, found by USB id) or ldnd[:PIPE] (USB adapter via ldnd.exe 0.0.5)")
    p.add_argument("--mac", type=parse_mac, help="MAC address to use on the air (default: random, locally administered)")
    p.add_argument("--channel", type=int, default=11, help="starting channel")


def _add_keys(p: argparse.ArgumentParser, required: bool = True) -> None:
    p.add_argument("--keys", required=required, help="aes_keys.txt holding the keyslot 0x2D key (not needed with --radio demo)")


def _uds_key(path: str | None, radio: str = "") -> bytes | None:
    if radio.startswith("demo"):
        from .radio.demo import DEMO_KEY

        return DEMO_KEY            # the simulated console's; no console key is involved
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
    from .capture import vc as vccap

    streams = vccap.unit_streams(messages)
    for direction, stream in streams.items():
        print(f"VC game stream ({direction}): {len(stream)} Game Boy bytes, starting {bytes(stream[:16]).hex(' ')}")
    for found in vccap.find_parties(streams):
        print("    " + vccap.describe(found))
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
    status.emit("radio", description=station.radio.describe())
    trade = LocalTrade(station, game, prof, offer, Path(args.output))
    try:
        if args.role == "host":
            appdata = parse_hex(args.application_data) if args.application_data is not None else None
            trade.host(appdata)
            print(f"hosting {game.name} on channel {station.network.channel}; waiting {args.seconds:.0f} s "
                  "for the console to join")
            status.emit("hosting", game=game.key, channel=station.network.channel)
            trade.wait_for_partner(args.seconds)
            status.emit("partner-joined")
        else:
            status.emit("searching", game=game.key)
            trade.join(args.seconds)
            status.emit("joined", channel=station.radio.channel)
        print(f"connected as {station.role.value}, node {station.node_id}")
        status.emit("session", state="listening")
        trade.run(args.listen)
    except ProtocolNotMapped as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        status.emit("stopped", reason="protocol-not-mapped", message=str(exc))
        return 2
    except TimeoutError as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        status.emit("stopped", reason="timeout", message=str(exc))
        return 1
    except KeyboardInterrupt:
        status.emit("stopped", reason="user")
        return 130
    finally:
        station.close()
    return 0


def cmd_vc_show(args) -> int:
    from . import gbpk

    record = gbpk.load(args.file)
    print(f"Generation {record.generation}: {record.describe()}, moves {record.moves}"
          + (f", item {record.held_item:#04x}" if record.generation == 2 else ""))
    if record.generation == 2 and (problems := gbpk.gen1_compatible(record)):
        print("cannot go through the Time Capsule: " + "; ".join(problems))
    return 0


def cmd_vc_convert(args) -> int:
    from . import gbpk

    record = gbpk.load(args.file)
    out = gbpk.gen2_to_gen1(record) if record.generation == 2 else gbpk.gen1_to_gen2(record)
    target = Path(args.output) if args.output else Path(args.file).with_suffix("." + out.extension)
    target.write_bytes(out.to_file())
    print(f"{out.describe()} -> {target}")
    return 0


def cmd_vc_trade(args) -> int:
    from . import gbpk
    from .games import vc as titles
    from .radio import open_radio
    from .uds import Station
    from .vc.trade import VcTrade

    offer = gbpk.load(args.offer)
    mail_file = Path(args.offer).with_suffix(".mail")
    mail = mail_file.read_bytes()[:47] if offer.generation == 2 and mail_file.exists() else b""
    if args.role == "host" and not args.as_title:
        raise SystemExit("hosting needs --as TITLE (the VC title PokeUDS stands in for)")
    if not args.keys and not args.radio.startswith("demo"):
        raise SystemExit("--keys aes_keys.txt is needed (the keyslot 0x2D key)")
    station = Station(open_radio(args.radio), args.mac or random_mac(), _uds_key(args.keys, args.radio), name=args.name)
    station.start(titles.CHANNEL if args.role == "host" else args.channel)
    status.emit("radio", description=station.radio.describe())
    trade = VcTrade(station, offer, Path(args.output), trades=args.trades, trainer_name=args.name, offer_mail=mail)
    try:
        if args.role == "host":
            title = titles.TITLES[args.as_title]
            partner = {"gen1": 1, "gen2": 2}.get(args.partner, title.generation)
            print(f"hosting as {title.name} (experimental); choose Join in the 3DS's VC link menu")
            status.emit("hosting", game=title.key, channel=titles.CHANNEL)
            trade.host(title, partner, args.seconds)
            status.emit("partner-joined")
        else:
            partner = {"gen1": 1, "gen2": 2}.get(args.partner)
            print("looking for a VC host; choose Host in the 3DS's VC link menu")
            status.emit("searching", game="vc")
            trade.join(args.seconds, partner)
            status.emit("joined", channel=station.radio.channel)
        print(f"connected: {trade.mode.key} trade, offering {offer.describe()}; walk to the Trade Center table")
        status.emit("mode", mode=trade.mode.key)
        trade.run(args.run_seconds)
        status.emit("stopped", reason="session-ended")
    except TimeoutError as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        status.emit("stopped", reason="timeout", message=str(exc))
        return 1
    except KeyboardInterrupt:
        status.emit("stopped", reason="user")
        stopped = True
    else:
        stopped = False
    finally:
        if trade.session:
            trade.session.leave(0)
        station.close()
    print(f"{len(trade.saved)} file(s) written to {args.output}")
    return 130 if stopped else 0


def cmd_board(args) -> int:
    from .app import board

    if args.board_command == "list":
        found = board.ports()
        for port in found:
            print(port.label())
        status.emit("boards", ports=[{"device": p.device, "description": p.description, "native": p.native} for p in found])
        if not found:
            print("No ESP32 found (Espressif USB 303A:1001, or a CP210x/CH340 UART bridge).")
        return 0
    if args.board_command == "check":
        port = args.port or next((p.device for p in board.ports() if p.native), None)
        if not port:
            print("No ESP32 on its native USB port. Plug in the port marked USB (not UART), or pass --port.")
            return 1
        print(f"Asking the board on {port} for its firmware (1.8 restarts into its 3DS mode first)")
        try:
            found = board.identify(port)
        except RuntimeError as exc:
            print(exc)
            return 1
        update = board.update_for(found)
        build = {"gblink": "GB-Link build", "vcadapter": "VC-Adapter board build", "": "3DS only"}[found.build]
        print(f"esp32-uds-bridge {found.firmware} ({build}) on {found.port}, MAC {found.mac}")
        if update:
            print(f"Update available: {update.name}")
        status.emit("board", port=found.port, firmware=found.firmware, build=found.build, mac=found.mac,
                    update=update.name if update else "")
        return 0
    if args.board_command == "download":
        try:
            written = board.download()
        except OSError as exc:
            print(f"Download failed: {exc}")
            print(f"The images are also on {board.RELEASES_PAGE}; Install from a file takes one.")
            return 1
        for path in written:
            print(f"Saved {path}")
        status.emit("firmware", files=[str(p) for p in written])
        return 0
    if args.board_command == "flash":
        image = Path(args.firmware) if args.firmware else (board.recommended() or None)
        if image is None:
            print("No firmware downloaded: run `pokeuds board download` or pass --firmware FILE.bin")
            return 1
        image = getattr(image, "path", image)
        info = board.image_info(image)
        if info and info.build == "vcadapter":
            print("Note: this is the VC-Adapter board build (1.9 beta), meant for that board on a link port.")
        port = args.port or next((p.device for p in board.ports()), None)
        if not port:
            print("No board found. If it is plugged in, hold BOOT while plugging it in, then release it.")
            return 1
        try:
            board.flash(port, image)
        except Exception as exc:           # esptool.FatalError, a serial error, a bad image
            print(f"Flashing failed: {exc}")
            print("If the board is not detected, hold BOOT while plugging it in, then release it, and try again.")
            return 1
        return 0
    return 2


def cmd_build(args) -> int:
    from . import pkhex
    from .app import bank as bank_mod
    from .app import builder

    helper = pkhex.shared()
    lists = helper.lists(args.game)

    def pick(kind: str, value: str | None) -> int | None:
        """A number, or a name from the game's list (case and spacing ignored)."""
        if value is None:
            return None
        if value.isdigit():
            return int(value)
        key = value.replace(" ", "").replace("-", "").lower()
        for number, name in lists[kind]:
            if name.replace(" ", "").replace("-", "").lower() == key:
                return number
        raise SystemExit(f"no {kind[:-1] if kind != 'species' else kind} called {value!r} in {args.game}")

    wish = builder.Wish(args.game, pick("species", args.species), args.level, args.shiny, pick("natures", args.nature),
                        args.gender or "", args.ability or "", pick("items", args.item), pick("balls", args.ball),
                        [pick("moves", m) for m in args.moves or []], args.nickname or "")
    trainer = builder.Trainer(args.ot, args.tid, args.sid, args.female)
    try:
        built = builder.build(wish, trainer, helper)
    except ValueError as exc:
        print(f"PKHeX could not build it: {exc}")
        return 1
    s = built.summary
    print(f"{s['species_name']} Lv {s['level']}{' (shiny)' if s['shiny'] else ''}, {'legal' if built.legal else 'NOT legal'}"
          f", from {built.encounter or '?'}; moves {', '.join(m for m in s['moves'] if m)}")
    if args.out:
        out = Path(args.out)
        if out.is_dir() or not out.suffix:
            out = out / f"{s['species']:03d}_{s['species_name']}.{built.extension}"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(built.data if built.extension in ("pk1", "pk2") else pkx.to_stored(built.data))
        print(f"Saved {out}")
    if args.bank or not args.out:
        entry = builder.keep(built, bank_mod.Bank(), wish)
        print(f"Kept in the bank as {entry.id}")
    return 0 if built.legal else 1


def _gift_line(card: dict) -> str:
    what = (f"{card.get('species_name', '?')} Lv {card.get('level', '?')}, shiny {card.get('shiny', '?')}"
            + (f", OT {card['ot']}" if card.get("ot") else ", your OT") if card["kind"] in ("pokemon", "egg") else
            f"{card.get('item') or card['kind']} x{card.get('quantity', 1)}")
    return f"{card['key']:<14} {card.get('date') or '----------':<10} {card['kind']:<7} {card['title'][:36]:<36} {what}"


def cmd_gift(args) -> int:
    from . import pkhex
    from .app import bank as bank_mod

    helper = pkhex.shared()
    trainer = {"ot": args.ot, "tid": args.tid, "sid": args.sid} if hasattr(args, "ot") else {}
    if args.gift_command == "list":
        found = 0
        for card in helper.gifts(args.game):
            if args.search and args.search.lower() not in json.dumps(card, ensure_ascii=False).lower():
                continue
            print(_gift_line(card))
            found += 1
        print(f"{found} card(s)")
        return 0
    if args.gift_command == "edit":
        from .app import giftfile

        source = Path(args.source)
        fields = (helper.card_fields(args.game, data=source.read_bytes()) if source.exists()
                  else helper.card_fields(args.game, key=args.source))
        out = giftfile.save(args.save, giftfile.GiftProject(args.game, giftfile.unreceived(fields), fields.get("title", "")))
        print(f"Saved the card's fields as {out}: change them (in the app's Gifts page, or the JSON), then "
              f"`pokeuds gift card {args.game} --project {out.name} --out card.wc{giftfile.generation(args.game)}`")
        return 0
    if args.gift_command == "card":
        if args.project:
            from .app import giftfile

            project = giftfile.load(args.project)
            for problem in project.problems:
                print(f"note: {problem}")
            if project.game != args.game:
                print(f"{args.project} is a gift for {project.game}, not {args.game}")
                return 1
            gift = helper.card(args.game, trainer=trainer, **project.fields)
        else:
            gift = helper.card(args.game, trainer=trainer, kind=args.kind, card_id=args.card_id, title=args.title,
                               species=args.species, level=args.level, shiny=args.shiny, item=args.item,
                               quantity=args.quantity, versions=args.versions, nickname=args.nickname,
                               **({"ot": args.card_ot, "tid": args.tid, "sid": args.sid} if args.card_ot else {}))
    elif args.source.lower().endswith((".wc6", ".wc7", ".wc6full", ".wc7full")) or Path(args.source).exists():
        gift = helper.gift(args.game, data=Path(args.source).read_bytes(), trainer=trainer)
    else:
        gift = helper.gift(args.game, key=args.source, trainer=trainer)
    print(_gift_line(gift.card))
    print(f"for {', '.join(gift.card['games'])}" + (" (a custom card)" if gift.custom else ""))
    if args.gift_command == "card" or getattr(args, "save_card", None):
        target = Path(args.out if args.gift_command == "card" else args.save_card)
        full = target.suffix.lower().endswith("full")
        target.write_bytes(gift.full_data if full else gift.card_data)
        print(f"Saved the card as {target}" + (" (784-byte full file)" if full else ""))
    p = gift.pokemon
    if p is None:
        print("An item card: only a Mystery Gift can deliver it, not a trade.")
        return 0
    s = p.summary
    print(f"gives {s['species_name']} Lv {s['level']}{' (shiny)' if s['shiny'] else ''}, OT {s['ot_name']} {s.get('shown_id', s['tid'])}, "
          f"{'legal' if p.legal else 'NOT legal: ' + p.report.splitlines()[0]}")
    if args.gift_command == "show":
        return 0
    if getattr(args, "pokemon_out", None):
        path = Path(args.pokemon_out)
        path.write_bytes(pkx.to_stored(p.data))
        print(f"Saved its Pokemon as {path}")
    if getattr(args, "bank", False) or (args.gift_command == "make" and not getattr(args, "pokemon_out", None)):
        entry, _ = bank_mod.Bank().deposit(pkx.to_stored(p.data), p.extension, source="gift",
                                           note=gift.card["title"], extra={"card_id": gift.card["card_id"]})
        print(f"Kept in the bank as {entry.id}")
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

    vc = sub.add_parser("vc", help="Virtual Console Gen 1/2 (Red, Blue, Yellow, Gold, Silver, Crystal)")
    vsub = vc.add_subparsers(dest="vc_command", required=True)
    p = vsub.add_parser("trade", help="trade through the VC link menu (Gen 1, Gen 2, or the Time Capsule)")
    _add_radio(p)
    _add_keys(p, required=False)
    p.add_argument("--offer", required=True, help=".pk1 or .pk2 to send (a .pk2 may have a .mail beside it)")
    p.add_argument("--output", default="received", help="folder for the Pokemon received (default: received/)")
    p.add_argument("--role", choices=["join", "host"], default="join",
                   help="join the 3DS's network (default; the 3DS chooses Host) or host one (experimental)")
    p.add_argument("--as", dest="as_title", choices=["red", "blue", "yellow", "gold", "silver", "crystal"],
                   help="as host: the title PokeUDS stands in for (its comm id)")
    p.add_argument("--partner", choices=["gen1", "gen2"],
                   help="the 3DS's generation; join finds it from the beacon, host defaults to --as's")
    p.add_argument("--trades", type=int, default=1, help="how many trades before PokeUDS chooses Cancel")
    p.add_argument("--name", default="POKEUDS", help="trainer name the 3DS sees (Game Boy text, up to 7)")
    p.add_argument("--seconds", type=float, default=120.0, help="how long to wait for the 3DS")
    p.add_argument("--run-seconds", type=float, help="stop after this long (default: when the session ends)")
    p.set_defaults(func=cmd_vc_trade)
    p = vsub.add_parser("show", help="show a .pk1/.pk2 file")
    p.add_argument("file")
    p.set_defaults(func=cmd_vc_show)
    p = vsub.add_parser("convert", help="convert .pk1 <-> .pk2 as the Time Capsule does")
    p.add_argument("file")
    p.add_argument("--output")
    p.set_defaults(func=cmd_vc_convert)

    bd = sub.add_parser("board", help="ESP32 boards and their esp32-uds-bridge firmware")
    bsub = bd.add_subparsers(dest="board_command", required=True)
    bsub.add_parser("list", help="list attached boards").set_defaults(func=cmd_board)
    p = bsub.add_parser("check", help="read a board's firmware version and build")
    p.add_argument("--port")
    p.set_defaults(func=cmd_board)
    bsub.add_parser("download", help="fetch the newest esp32-uds-bridge images from VC-Adapter's releases").set_defaults(func=cmd_board)
    p = bsub.add_parser("flash", help="write a firmware image at 0x0 (default: the newest downloaded GB-Link build)")
    p.add_argument("--port")
    p.add_argument("--firmware", help="a merged esp32-uds-bridge .bin")
    p.set_defaults(func=cmd_board)

    p = sub.add_parser("build", help="build a legal Pokemon with the PKHeX helper")
    p.add_argument("game", choices=["rby", "gsc", "xy", "oras", "sm", "usum"])
    p.add_argument("species", help="National Dex number or name")
    p.add_argument("--level", type=int, default=50)
    p.add_argument("--shiny", action="store_true")
    p.add_argument("--nature", help="name or 0-24 (Gen 6/7)")
    p.add_argument("--gender", choices=["male", "female"])
    p.add_argument("--ability", choices=["1", "2", "hidden"])
    p.add_argument("--item", help="held item, name or number (Gen 2 on)")
    p.add_argument("--ball", help="name or number (Gen 6/7)")
    p.add_argument("--moves", nargs="+", help="up to four, names or numbers (default: what it knows at that level)")
    p.add_argument("--nickname")
    p.add_argument("--ot", default="POKEUDS")
    p.add_argument("--tid", type=int, default=12345)
    p.add_argument("--sid", type=int, default=54321)
    p.add_argument("--female", action="store_true", help="a female OT (Gen 2 on)")
    p.add_argument("--out", help="write the record to this file or folder")
    p.add_argument("--bank", action="store_true", help="also keep it in the bank (the default without --out)")
    p.set_defaults(func=cmd_build)

    gf = sub.add_parser("gift", help="Gen 6/7 Mystery Gift cards (docs/mystery_gift.md)")
    gsub = gf.add_subparsers(dest="gift_command", required=True)

    def _gift_trainer(p) -> None:
        p.add_argument("--ot", default="POKEUDS", help="the receiver: OT of a card that gives your own Pokemon")
        p.add_argument("--tid", type=int, default=12345)
        p.add_argument("--sid", type=int, default=54321)

    p = gsub.add_parser("list", help="the official cards PKHeX knows for a game")
    p.add_argument("game", choices=GAME_KEYS)
    p.add_argument("--search", help="only cards mentioning this (title, species, OT)")
    p.set_defaults(func=cmd_gift)
    for name, text in (("show", "show a card (official key, .wc6/.wc7 or full file) and what it gives"),
                       ("make", "make a card's Pokemon for the trainer, for a trade (default: into the bank)")):
        p = gsub.add_parser(name, help=text)
        p.add_argument("game", choices=GAME_KEYS)
        p.add_argument("source", help="a key from `gift list`, or a .wc6/.wc7/.wc6full/.wc7full file")
        _gift_trainer(p)
        p.add_argument("--save-card", help="write the card to this file (.wc6/.wc7, or .wc6full/.wc7full for 784 bytes)")
        if name == "make":
            p.add_argument("--pokemon-out", help="write the Pokemon to this .pk6/.pk7")
            p.add_argument("--bank", action="store_true", help="also keep it in the bank")
        p.set_defaults(func=cmd_gift)
    p = gsub.add_parser("edit", help="turn a card (official key or .wc6/.wc7/full file) into a .udsgift to change")
    p.add_argument("game", choices=GAME_KEYS)
    p.add_argument("source", help="a key from `gift list`, or a card file")
    p.add_argument("--save", required=True, help="the .udsgift to write")
    p.set_defaults(func=cmd_gift)
    p = gsub.add_parser("card", help="build a custom card (its Pokemon matches no real event: PKHeX flags it)")
    p.add_argument("game", choices=GAME_KEYS)
    p.add_argument("--out", required=True, help="the card file: .wc6/.wc7, or .wc6full/.wc7full")
    p.add_argument("--project", help="a .udsgift (from the app or `gift edit`): every field; the flags below are ignored")
    p.add_argument("--kind", choices=["pokemon", "egg", "item", "bp", "beans"], default="pokemon",
                   help="egg: a Pokemon egg, set as official event eggs (level 1, from a Pokemon Center)")
    p.add_argument("--card-id", type=int, default=2000)
    p.add_argument("--title", default="PokeUDS gift")
    p.add_argument("--species", type=int)
    p.add_argument("--level", type=int, default=10)
    p.add_argument("--shiny", choices=["random", "always", "never"], default="random")
    p.add_argument("--nickname")
    p.add_argument("--item", type=int, help="item id (item card), or the bean kind")
    p.add_argument("--quantity", type=int, default=1)
    p.add_argument("--versions", type=int, default=0, help="RestrictVersion bits: 1 X/Sun, 2 Y/Moon, 4 AS/US, 8 OR/UM; 0 all")
    p.add_argument("--card-ot", default="", help="the card's own OT (default: the receiver is the OT)")
    _gift_trainer(p)
    p.set_defaults(func=cmd_gift)

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
    if status.enabled() and os.environ.get("POKEUDS_MANAGED"):
        status.watch_stdin_for_stop()
    try:
        return args.func(args)
    except SystemExit as exc:
        if exc.code not in (None, 0) and not isinstance(exc.code, int):
            status.emit("error", message=str(exc.code))
        raise
    except KeyboardInterrupt:
        status.emit("stopped", reason="user")
        return 130
    except Exception as exc:
        status.emit("error", message=f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    sys.exit(main())
