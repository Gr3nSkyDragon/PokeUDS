"""The Gen 7 distributor's public OPSSR key and the constants of its message format.

The public key is public (every copy of the game carries it); the private exponent is the distributor
app's and is never in this repository — see `keyextract` and docs/mystery_gift.md.
"""

PUBLIC_KEY = bytes.fromhex(
    "307c300d06092a864886f70d0101010500036b003068026100b756e1dcd8cece78e148107b1bac115fdb17de843453"
    "cab7d4e6df8dd21f5a3d17b4477a8a531d97d57eb558f0d58a4af5bfaddda4a0bc1dc22ff87576c7268b942819d4c8"
    "3f78e1ee92d406662f4e68471e4de833e5126c32eb63a868345d1d0203010001")
RSA_N = int.from_bytes(bytes.fromhex(
    "b756e1dcd8cece78e148107b1bac115fdb17de843453cab7d4e6df8dd21f5a3d17b4477a8a531d97d57eb558f0d5"
    "8a4af5bfaddda4a0bc1dc22ff87576c7268b942819d4c83f78e1ee92d406662f4e68471e4de833e5126c32eb63a8"
    "68345d1d"), "big")
RSA_E = 65537
MODULUS_SIZE = 96               # RSA-768
OVERHEAD = 8                    # OPSSR's digest bytes kept in the signature
SIGNATURE_SIZE = MODULUS_SIZE   # the signed block replacing the compressed stream's last 88 bytes
