"""The sprite cache: which file a species and form map to, the PNG check, fetching once and keeping it."""

import struct
import zlib

from pokeuds.app import sprites


def png(width=2, height=2, extra=b"") -> bytes:
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    rows = b"".join(b"\0" + b"\xff\x00\x00\xff" * width for _ in range(height))
    return (sprites.PNG + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + extra
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def text_chunk() -> bytes:
    body = b"Comment\0hello"
    return struct.pack(">I", len(body)) + b"tEXt" + body + struct.pack(">I", zlib.crc32(b"tEXt" + body))


def test_sprite_keys():
    assert sprites.sprite_key(25) == 25 and sprites.sprite_key(0) is None
    assert sprites.sprite_key(19, 1) == 10091                  # Alolan Rattata
    assert sprites.sprite_key(201, 25) == "201-z" and sprites.sprite_key(201, 26) == "201-exclamation"
    assert sprites.sprite_key(658, 2) == 658                   # a form with no picture of its own: the species


def test_only_plain_small_pngs_are_kept():
    plain = png()
    assert sprites.clean(plain) == plain
    assert sprites.clean(png(extra=text_chunk())) == plain   # the drawing chunks alone
    assert sprites.clean(b"GIF89a" + plain) is None
    assert sprites.clean(plain[:-5]) is None                 # cut short
    broken = bytearray(plain)
    broken[30] ^= 1
    assert sprites.clean(bytes(broken)) is None              # a CRC that fails
    assert sprites.clean(png(width=300)) is None             # larger than any sprite


def test_fetched_once_then_kept(tmp_path):
    source = tmp_path / "pokeapi"
    (source / "shiny").mkdir(parents=True)
    (source / "25.png").write_bytes(png(extra=text_chunk()))
    cache = sprites.SpriteCache(tmp_path / "cache", base=source.as_uri())
    assert cache.cached(25) is None
    assert cache.get(25) == png()
    (source / "25.png").unlink()                             # from here on it comes from the cache
    assert sprites.SpriteCache(tmp_path / "cache", base=source.as_uri()).cached(25) == png()
    assert cache.get(26) is None                             # not there: no sprite, no error
    offline = sprites.SpriteCache(tmp_path / "other", base=source.as_uri())
    offline.online = False
    (source / "25.png").write_bytes(png())
    assert offline.get(25) is None                           # downloads off: only the cache is read
    assert cache.clear() == 1 and cache.cached(25) is None
