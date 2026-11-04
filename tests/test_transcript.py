from pokeuds import pk6
from pokeuds.capture import transcript
from tests.test_pk6 import make_record


def colon_hex(data: bytes) -> str:
    return ":".join(f"{b:02X}" for b in data) if data else "<none>"


def test_azahar_log(tmp_path):
    record = make_record(0x76543210, species=658, nickname="Greninja")
    rx_payload = b"\x07\x00\x01\x00" + pk6.encrypt(record)
    log = tmp_path / "azahar_log.txt"
    log.write_text("\n".join([
        "[  10.000000] Service.NWM <Info> core/hle/service/nwm/nwm_uds.cpp:ConnectToNetworkHLE:2733: "
        "UDS Real: ConnectToNetwork key-derivation input bytes=4, fingerprint=0x01020304, inputBytes=41:42:43:00, "
        "wlanCommId=0x00055D00, networkId=0x1234ABCD, hostMac=40:D2:8A:00:00:01",
        "[  11.500000] Service.NWM <Info> core/hle/service/nwm/nwm_uds.cpp:SendToHLE:2530: "
        "UDS DATA TRACE TX GAME: channel=1, secureSequence=0, sourceNode=2, destinationNode=1, flags=0x00, "
        "payloadBytes=3, payloadFingerprint=0x11111111, payload=01:02:03",
        "[  11.600000] Service.NWM <Info> core/hle/service/nwm/nwm_uds.cpp:PullPacketHLE:2660: "
        f"UDS DATA TRACE RX GAME: channel=1, secureSequence=5, sourceNode=1, destinationNode=2, "
        f"payloadBytes={len(rx_payload)}, payloadFingerprint=0x22222222, payload={colon_hex(rx_payload)}, "
        "remainingQueueDepth=0",
        "[  12.000000] unrelated line, channel=9",
    ]))
    messages, facts = transcript.load(log)
    assert [(m.direction, m.channel, m.src_node, m.dest_node) for m in messages] == [("tx", 1, 2, 1), ("rx", 1, 1, 2)]
    assert messages[0].payload == b"\x01\x02\x03" and messages[1].time == 11.6
    assert facts.passphrases == {"41424300"} and facts.wlan_comm_ids == {0x00055D00}
    found = transcript.find_pokemon(messages)
    assert len(found) == 1 and (found[0].message, found[0].offset) == (1, 4)
    assert found[0].summary.species == 658 and found[0].record == record
    out = tmp_path / "t.jsonl"
    transcript.write_jsonl(messages, out)
    assert [m.payload for m in transcript.load(out)[0]] == [m.payload for m in messages]


def test_record_split_across_messages():
    record = pk6.encrypt(make_record(0x00ABCDEF))
    messages = [transcript.Message(i, None, "rx", 1, i, 1, 2, chunk)
                for i, chunk in enumerate([b"\x99" * 5 + record[:100], record[100:200], record[200:]])]
    found = transcript.find_pokemon(messages)
    assert len(found) == 1 and found[0].message == 0 and found[0].offset == 5
