"""Make the app's icon files from the artwork: gui/assets/icon.ico (the Windows exe, taskbar and window),
gui/assets/icon.icns (the macOS app) and gui/assets/icon.png (the rail's mark).

    python scripts/make_icon.py [--art packaging/icon.jpg]

The artwork's pure red becomes the app's accent (gui/theme.py ACCENT, the colour of the Start and Broadcast
buttons), so the icon matches the buttons. Each pixel's share of red is measured by how far its red channel
stands above green and blue, so the black outlines' anti-aliased edges and the JPEG's noise shift with it and
the cream and blue parts stay as drawn. Needs Pillow (it comes with flet[all]).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ART = ROOT / "packaging" / "icon.jpg"
ASSETS = ROOT / "gui" / "assets"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
ICNS_SIZE = 1024            # Pillow writes the 16 to 1024 px images a Mac uses from this one
PNG_SIZE = 128


def accent() -> tuple[int, int, int]:
    from gui.theme import ACCENT
    h = ACCENT.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def recolour(image: Image.Image, target: tuple[int, int, int]) -> Image.Image:
    """Move the red share of every pixel from pure red to ``target``."""
    tr, tg, tb = target
    dr, dg, db = tr - 255, tg, tb           # target minus pure red

    def pixel(r: int, g: int, b: int) -> tuple[int, int, int]:
        share = max(0.0, min(1.0, (r - max(g, b)) / 255))
        return (round(min(255, max(0, r + share * dr))), round(min(255, max(0, g + share * dg))),
                round(min(255, max(0, b + share * db))))

    out = image.convert("RGB")
    pixels = getattr(out, "get_flattened_data", out.getdata)()      # getdata is deprecated from Pillow 13
    out.putdata([pixel(*p) for p in pixels])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--art", type=Path, default=ART)
    a = parser.parse_args(argv)
    # Work at 1024 px: the largest size a Mac shows, and still quick to recolour pixel by pixel.
    art = Image.open(a.art).convert("RGB").resize((ICNS_SIZE, ICNS_SIZE), Image.LANCZOS)
    icon = recolour(art, accent())
    ASSETS.mkdir(parents=True, exist_ok=True)
    icon.resize((256, 256), Image.LANCZOS).save(ASSETS / "icon.ico", sizes=[(s, s) for s in ICO_SIZES])
    icon.save(ASSETS / "icon.icns")
    icon.resize((PNG_SIZE, PNG_SIZE), Image.LANCZOS).save(ASSETS / "icon.png", optimize=True)
    print(f"wrote icon.ico, icon.icns and icon.png in {ASSETS} from {a.art}, red -> #{'%02X%02X%02X' % accent()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
