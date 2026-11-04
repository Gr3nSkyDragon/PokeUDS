"""UDS cryptography: beacon node-list encryption, the network CCMP key, and CCMP itself.

Ported from azahar_uds ``uds_beacon.cpp`` and ``uds_data.cpp`` (Citra/Azahar, GPLv2+), whose
formulas were verified against retail consoles.

- Beacon node lists are AES-CTR encrypted with a fixed key from the NWM module; no console key is
  needed to read who is in a network.
- Data frames use standard 802.11 CCMP. The CCMP key is the MD5 of the game's passphrase,
  AES-CTR encrypted with the keyslot 0x2D normal key under a counter that is the MD5 of the
  network's identity (comm id, network id, host MAC, id).
"""

from __future__ import annotations

import hashlib
import struct

from Crypto.Cipher import AES

from . import dot11

# The NWM beacon data key, taken from the NWM module code (public; in Citra since 2017).
NWM_BEACON_KEY = bytes.fromhex("26E4423F4B3F69F2EE94195B49FF6959")

CCMP_MIC_SIZE = 8
CCMP_HEADER_SIZE = 8


def _ctr(key: bytes, counter: bytes):
    # A full 16-byte initial counter block, incremented as one 128-bit big-endian integer (Crypto++).
    return AES.new(key, AES.MODE_CTR, nonce=b"", initial_value=counter)


def beacon_counter(host_mac: bytes, wlan_comm_id: int, id_: int, network_id: int) -> bytes:
    return struct.pack("<6sIBxI", host_mac, wlan_comm_id, id_, network_id)


def beacon_crypt(data: bytes, host_mac: bytes, wlan_comm_id: int, id_: int, network_id: int) -> bytes:
    return _ctr(NWM_BEACON_KEY, beacon_counter(host_mac, wlan_comm_id, id_, network_id)).encrypt(data)


def ccmp_key(uds_key: bytes, passphrase: bytes, host_mac: bytes, wlan_comm_id: int, id_: int, network_id: int) -> bytes:
    counter = hashlib.md5(struct.pack("<II6sH", wlan_comm_id, network_id, host_mac, id_)).digest()
    return _ctr(uds_key, counter).encrypt(hashlib.md5(passphrase).digest())


def _aad(fc: int, transmitter: bytes, receiver: bytes, bssid: bytes, seq_ctrl: int) -> bytes:
    # IEEE 802.11-2007 8.3.3.3.2: the masked on-air header (retry, power management, more data and
    # the subtype's QoS bits cleared; sequence number cleared, fragment number kept).
    to_ds, from_ds = bool(fc & dot11.FC_TO_DS), bool(fc & dot11.FC_FROM_DS)
    if to_ds and from_ds:
        raise ValueError("four-address frames are not used by UDS")
    if from_ds:
        addresses = receiver + bssid + transmitter
    elif to_ds:
        addresses = bssid + transmitter + receiver
    else:
        addresses = receiver + transmitter + bssid
    return struct.pack("<H", fc & 0xC78F) + addresses + struct.pack("<H", seq_ctrl & 0x000F)


def _nonce(transmitter: bytes, pn: int) -> bytes:
    return b"\x00" + transmitter + pn.to_bytes(6, "big")


def ccmp_header(pn: int) -> bytes:
    # PN0, PN1, reserved, KeyID 0 | ExtIV, PN2..PN5.
    return bytes([pn & 0xFF, (pn >> 8) & 0xFF, 0, 0x20]) + (pn >> 16).to_bytes(4, "little")


def ccmp_pn(header: bytes) -> int:
    return header[0] | header[1] << 8 | int.from_bytes(header[4:8], "little") << 16


def ccmp_encrypt(key: bytes, plaintext: bytes, *, fc: int, transmitter: bytes, receiver: bytes,
                 bssid: bytes, seq_ctrl: int, pn: int) -> bytes:
    cipher = AES.new(key, AES.MODE_CCM, nonce=_nonce(transmitter, pn), mac_len=CCMP_MIC_SIZE)
    cipher.update(_aad(fc, transmitter, receiver, bssid, seq_ctrl))
    ciphertext, mic = cipher.encrypt_and_digest(plaintext)
    return ciphertext + mic


def ccmp_decrypt(key: bytes, ciphertext: bytes, *, fc: int, transmitter: bytes, receiver: bytes,
                 bssid: bytes, seq_ctrl: int, pn: int) -> bytes | None:
    if len(ciphertext) < CCMP_MIC_SIZE:
        return None
    cipher = AES.new(key, AES.MODE_CCM, nonce=_nonce(transmitter, pn), mac_len=CCMP_MIC_SIZE)
    cipher.update(_aad(fc, transmitter, receiver, bssid, seq_ctrl))
    try:
        return cipher.decrypt_and_verify(ciphertext[:-CCMP_MIC_SIZE], ciphertext[-CCMP_MIC_SIZE:])
    except ValueError:
        return None


def build_data_frame(key: bytes, payload: bytes, *, transmitter: bytes, destination: bytes, bssid: bytes,
                     from_ds: bool, pn: int, seq: int) -> bytes:
    ds, a1, a2, a3 = dot11.data_addresses(transmitter, destination, bssid, from_ds=from_ds)
    fc = dot11.FC_DATA | dot11.FC_PROTECTED | ds
    seq_ctrl = (seq & 0x0FFF) << 4
    sealed = ccmp_encrypt(key, payload, fc=fc, transmitter=transmitter, receiver=destination, bssid=bssid,
                          seq_ctrl=seq_ctrl, pn=pn)
    return dot11.header(fc, a1, a2, a3, seq) + ccmp_header(pn) + sealed


def open_data_frame(key: bytes, frame: dot11.Frame) -> bytes | None:
    """The plaintext of a protected UDS data frame, or None if it fails CCMP authentication."""
    body = frame.body
    if len(body) < CCMP_HEADER_SIZE + CCMP_MIC_SIZE or not body[3] & 0x20:
        return None
    return ccmp_decrypt(key, body[CCMP_HEADER_SIZE:], fc=frame.fc, transmitter=frame.transmitter,
                        receiver=frame.destination, bssid=frame.bssid, seq_ctrl=frame.seq_ctrl,
                        pn=ccmp_pn(body))
