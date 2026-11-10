"""secp256k1 for Nostr: x-only public keys, BIP-340 Schnorr signatures, and the shared secret of two keys.

Pure Python on the curve's affine coordinates: a signature or a check takes a few milliseconds, and the GTS makes a
handful per round. Secret and public keys are 32 bytes; a public key is the x coordinate of the point with even y
(BIP-340, "x-only").
"""

from __future__ import annotations

import hashlib
import os

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)
Point = tuple[int, int] | None          # None is the point at infinity


def _add(a: Point, b: Point) -> Point:
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0] and a[1] != b[1]:
        return None
    if a == b:
        slope = 3 * a[0] * a[0] * pow(2 * a[1], -1, P) % P
    else:
        slope = (b[1] - a[1]) * pow(b[0] - a[0], -1, P) % P
    x = (slope * slope - a[0] - b[0]) % P
    return x, (slope * (a[0] - x) - a[1]) % P


def _mul(point: Point, k: int) -> Point:
    result = None
    while k:
        if k & 1:
            result = _add(result, point)
        point = _add(point, point)
        k >>= 1
    return result


def _lift_x(x: int) -> Point:
    """The curve point with this x and an even y, or None when x is not on the curve."""
    if x >= P:
        return None
    y_sq = (pow(x, 3, P) + 7) % P
    y = pow(y_sq, (P + 1) // 4, P)
    if y * y % P != y_sq:
        return None
    return x, y if y % 2 == 0 else P - y


def _tagged(tag: str, data: bytes) -> bytes:
    digest = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(digest + digest + data).digest()


def _secret(secret: bytes) -> int:
    d = int.from_bytes(secret, "big")
    if len(secret) != 32 or not 1 <= d < N:
        raise ValueError("a secret key is 32 bytes, between 1 and the curve's order")
    return d


def new_secret() -> bytes:
    while True:
        secret = os.urandom(32)
        if 1 <= int.from_bytes(secret, "big") < N:
            return secret


def public_key(secret: bytes) -> bytes:
    """The x-only public key of a secret key."""
    return _mul(G, _secret(secret))[0].to_bytes(32, "big")


def sign(secret: bytes, message: bytes, aux: bytes | None = None) -> bytes:
    """BIP-340: the 64-byte signature of a 32-byte message. ``aux`` is the fresh randomness (default: random)."""
    if len(message) != 32:
        raise ValueError("the message to sign is 32 bytes")
    aux = os.urandom(32) if aux is None else aux
    d = _secret(secret)
    point = _mul(G, d)
    if point[1] % 2:
        d = N - d
    px = point[0].to_bytes(32, "big")
    t = (d ^ int.from_bytes(_tagged("BIP0340/aux", aux), "big")).to_bytes(32, "big")
    k = int.from_bytes(_tagged("BIP0340/nonce", t + px + message), "big") % N
    if k == 0:
        raise ValueError("no nonce: sign again")
    r = _mul(G, k)
    if r[1] % 2:
        k = N - k
    rx = r[0].to_bytes(32, "big")
    e = int.from_bytes(_tagged("BIP0340/challenge", rx + px + message), "big") % N
    signature = rx + ((k + e * d) % N).to_bytes(32, "big")
    if not verify(px, message, signature):
        raise ValueError("the signature made does not verify")
    return signature


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(message) != 32 or len(signature) != 64:
        return False
    point = _lift_x(int.from_bytes(public, "big"))
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if point is None or r >= P or s >= N:
        return False
    e = int.from_bytes(_tagged("BIP0340/challenge", signature[:32] + public + message), "big") % N
    found = _add(_mul(G, s), _mul(point, N - e))
    return found is not None and found[1] % 2 == 0 and found[0] == r


def shared_x(secret: bytes, public: bytes) -> bytes:
    """The x coordinate both sides get from their own secret and the other's public key (ECDH). The same whichever
    of a point's two y values each side lifts, so x-only keys are enough."""
    point = _lift_x(int.from_bytes(public, "big"))
    if point is None:
        raise ValueError("not a public key on the curve")
    return _mul(point, _secret(secret))[0].to_bytes(32, "big")
