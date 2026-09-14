#!/usr/bin/env python3
"""Generate PWA icons for the chat UI into ``frontend/public/``.

One-off tool, no third-party deps (Pillow is not installed in the project venv):
PNG is written directly with ``zlib``/``struct``. Shapes are signed distance
fields rasterised with a 1px-wide antialiased edge, so the same code renders
crisply from 32px to 512px.

The mark is the site brand: the accent green of ``static/css/style.css`` with a
light geometric "ai" wordmark. Flip ``BG``/``FG`` below to invert the palettes.

    python3 scripts/generate_pwa_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "public"

# Site palette: `--accent` and `--chat-bg` from frontend/src/index.css.
BG = (15, 122, 95)  # #0f7a5f
FG = (243, 246, 244)  # #f3f6f4

# Mark geometry, in fractions of the icon size.
X_HEIGHT = 0.36  # height of the lowercase letters
STROKE = 0.09  # stem thickness
GAP = 0.2 * X_HEIGHT  # between "a" and "i"
DOT_RATIO = 0.55  # dot radius relative to the stroke
DOT_GAP = 0.06 * X_HEIGHT  # between the dot and the x-height
CORNER = 0.22  # corner radius of the rounded "any" icons


def sdf_rounded_rect(
    px: float, py: float, cx: float, cy: float, hw: float, hh: float, r: float
) -> float:
    """Signed distance to a rounded rectangle (capsule when r == hw)."""
    qx = abs(px - cx) - (hw - r)
    qy = abs(py - cy) - (hh - r)
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def sdf_ring(px: float, py: float, cx: float, cy: float, radius: float, t: float) -> float:
    """Signed distance to a stroked circle, i.e. the bowl of a geometric "a"."""
    return abs(math.hypot(px - cx, py - cy) - radius) - t / 2


def sdf_circle(px: float, py: float, cx: float, cy: float, radius: float) -> float:
    """Signed distance to a filled circle, i.e. the dot of the "i"."""
    return math.hypot(px - cx, py - cy) - radius


def mark_sdf(px: float, py: float) -> float:
    """Signed distance to the "ai" wordmark, centred in the unit square."""
    radius = (X_HEIGHT - STROKE) / 2
    dot_radius = DOT_RATIO * STROKE
    width = X_HEIGHT + GAP + STROKE
    # Centre the whole wordmark, dot included, not just the x-height band.
    cy = 0.5 + (DOT_GAP + 2 * dot_radius) / 2
    a_cx = 0.5 - width / 2 + X_HEIGHT / 2
    i_cx = a_cx + X_HEIGHT / 2 + GAP + STROKE / 2
    dot_y = cy - X_HEIGHT / 2 - DOT_GAP - dot_radius

    shapes = (
        # "a": bowl plus the right stem, as in a geometric sans.
        sdf_ring(px, py, a_cx, cy, radius, STROKE),
        sdf_rounded_rect(
            px, py, a_cx + X_HEIGHT / 2 - STROKE / 2, cy, STROKE / 2, X_HEIGHT / 2, STROKE / 2
        ),
        # "i": stem plus dot.
        sdf_rounded_rect(px, py, i_cx, cy, STROKE / 2, X_HEIGHT / 2, STROKE / 2),
        sdf_circle(px, py, i_cx, dot_y, dot_radius),
    )
    return min(shapes)


def background_sdf(px: float, py: float, corner: float) -> float:
    """Signed distance to the icon plate; corner=0 for a full-bleed square."""
    if corner <= 0:
        return max(abs(px - 0.5), abs(py - 0.5)) - 0.5
    return sdf_rounded_rect(px, py, 0.5, 0.5, 0.5, 0.5, corner)


def render(size: int, corner: float) -> bytes:
    """Render one RGBA icon; `corner` is the plate radius as a fraction of size."""
    rows = bytearray()
    for y in range(size):
        rows.append(0)  # PNG filter: none
        for x in range(size):
            # Pixel centres, in unit coordinates.
            px = (x + 0.5) / size
            py = (y + 0.5) / size
            plate = background_sdf(px, py, corner) * size
            coverage = min(1.0, max(0.0, 0.5 - plate))
            if coverage <= 0:
                rows.extend((0, 0, 0, 0))
                continue
            # The mark is drawn only inside the plate: clip it by the plate sdf,
            # otherwise its edge would bleed over a rounded corner.
            mark = max(mark_sdf(px, py) * size, plate)
            alpha = min(1.0, max(0.0, 0.5 - mark))
            rows.extend(
                (
                    round(BG[0] + (FG[0] - BG[0]) * alpha),
                    round(BG[1] + (FG[1] - BG[1]) * alpha),
                    round(BG[2] + (FG[2] - BG[2]) * alpha),
                    round(255 * coverage),
                )
            )
    return bytes(rows)


def write_png(path: Path, size: int, raw: bytes) -> None:
    """Write an 8-bit RGBA PNG (colour type 6)."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def main() -> None:
    # "any" icons are rounded; maskable/apple/favicon are full-bleed so that
    # Android (safe zone) and iOS (own mask) can crop them themselves.
    icons = (
        ("icon-192.png", 192, CORNER),
        ("icon-512.png", 512, CORNER),
        ("icon-maskable-512.png", 512, 0.0),
        ("apple-touch-icon.png", 180, 0.0),
        ("favicon-32.png", 32, 0.0),
    )
    for name, size, corner in icons:
        path = OUT_DIR / name
        write_png(path, size, render(size, corner))
        print(f"{path.relative_to(OUT_DIR.parent.parent)}: {size}x{size}")


if __name__ == "__main__":
    main()
