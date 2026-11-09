"""Find, parse and write the Gen 7 distributor's OPSSR private exponent.

The private key exists only in the Gen 7 Local Distributor (title 00040000001A8900): a 461-byte
PKCS#1 `RSAPrivateKey` DER inside its `.code`.  Retail Sun/Moon/USUM carry only the public key, so
`extract` correctly refuses them.  The key file this writes (`rsa_d_key.txt`) is secret.
"""

from __future__ import annotations

from pathlib import Path

from .keys import RSA_E, RSA_N

# PKCS#1 RSAPrivateKey: SEQUENCE, version 0, modulus (0x61 bytes, leading 0x00), exponent 0x010001.
PRIVATE_HEADER = bytes.fromhex("308201c9020100026100")
DER_SIZE = 0x1CD                       # 461 bytes
CXI_OFFSET = 0x56E2F7                  # .code at 0x3000, maps at VA 0x100000, private DER VA 0x66B2F7
CODE_OFFSET = 0x56B2F7                 # an extracted ExeFS code.bin (no CXI header)


def read_text_key(path: str | Path) -> int | None:
    """The `d=<hex>` value from an `rsa_d_key.txt` (or the legacy `gen7_delivery_key.txt`)."""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith(("d=", "rsa_d=")):
            value = line.split("=", 1)[1].strip().replace("0x", "").replace(":", "").replace(" ", "")
            return int(value, 16)
    return None


def parse_pkcs1(der: bytes) -> int:
    """The RSA private exponent from a PKCS#1 RSAPrivateKey DER, checked against the distributor's modulus."""
    if len(der) < 10 or der[:4] != b"\x30\x82\x01\xc9":
        raise ValueError("not a PKCS#1 RSAPrivateKey")

    def integer(i: int) -> tuple[int, int]:
        if der[i] != 0x02:
            raise ValueError("expected a DER INTEGER")
        size = der[i + 1]
        i += 2
        if size & 0x80:
            width = size & 0x7F
            size = int.from_bytes(der[i:i + width], "big")
            i += width
        value = der[i:i + size]
        if len(value) != size:
            raise ValueError("truncated DER INTEGER")
        return int.from_bytes(value, "big"), i + size

    _, i = integer(4)                  # version
    modulus, i = integer(i)
    exponent, i = integer(i)
    private, i = integer(i)
    if modulus != RSA_N:
        raise ValueError("this key is not the Gen 7 distributor's (retail games hold only the public key)")
    if exponent != RSA_E:
        raise ValueError(f"unexpected RSA exponent {exponent}")
    return private


def extract(data: bytes) -> tuple[int, int]:
    """-> (file offset, private exponent) from a CXI, a code.bin, a DER, or any container holding the DER."""
    for offset in (0, CXI_OFFSET, CODE_OFFSET):
        if data[offset:offset + 4] == b"\x30\x82\x01\xc9":
            try:
                return offset, parse_pkcs1(data[offset:offset + DER_SIZE])
            except ValueError:
                pass
    # A CCI/NCCH may put the ExeFS at another partition offset; find the private key by its header.
    start = 0
    while True:
        offset = data.find(PRIVATE_HEADER, start)
        if offset < 0:
            break
        try:
            return offset, parse_pkcs1(data[offset:offset + DER_SIZE])
        except ValueError:
            start = offset + 1
    raise ValueError(
        "no Gen 7 distributor private key found. Retail Sun/Moon/USUM (and their CIAs/CCIs) contain "
        "only the public key; the private key is in the Gen 7 Local Distributor (00040000001A8900)."
    )


def write_key(path: str | Path, private: int, source: str, offset: int) -> None:
    """Write an `rsa_d_key.txt` for `delivery.use_key_file` / `--gen7-key`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# PokeUDS Gen 7 distributor OPSSR signing key\n"
        f"# Extracted from: {source} (0x{offset:X})\n"
        "# SECRET: this lets anyone sign Gen 7 local distributions. Do not commit or share it.\n"
        f"d={private:0192x}\n",
        encoding="utf-8",
    )
