#!/usr/bin/env python3
"""Draw site/email/heart.png: the site's watchlist heart (same path as site/app.js `heart`) in white,
on the site's dark round button. Email can't use inline SVG, so the email links this image instead.
Run once after changing the design; the PNG is committed. Stdlib only."""
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "site" / "email" / "heart.png"
SIZE = 64          # drawn at 2x for sharp screens; the email shows it at 32x32
SCALE = SIZE / 32 * (16 / 24)   # the site: a 16px icon (24-unit viewBox) in a 32px button
SS = 4             # supersampling per axis

# The site's heart path, flattened from its cubic curves (all absolute): start, then (c1, c2, end) per segment.
START = (12, 21)
SEGS = [((12, 21), (4.5, 16.3), (2.4, 11.7)), ((0.9, 8.3), (3, 4.5), (6.7, 4.5)),
        ((8.7, 4.5), (10.3, 5.5), (12, 7.5)), ((13.7, 5.5), (15.3, 4.5), (17.3, 4.5)),
        ((21, 4.5), (23.1, 8.3), (21.6, 11.7)), ((19.5, 16.3), (12, 21), (12, 21))]


def flatten(n=40):
    pts, p0 = [START], START
    for c1, c2, p3 in SEGS:
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            pts.append((u**3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t**3 * p3[0],
                        u**3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t**3 * p3[1]))
        p0 = p3
    return pts


def dist_to_segment(px, py, a, b):
    ax, ay, bx, by = *a, *b
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0 if l2 == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / l2))
    return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5


def png_bytes(w, h, raw):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def inside(x, y, pts):
    """Even-odd point-in-polygon test."""
    hit = False
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def draw(filled, bg_rgb, bg_alpha):
    """RGBA rows: the heart (outline, or filled) in white on a round button of `bg_rgb` at `bg_alpha`."""
    pts = flatten()
    segs = list(zip(pts, pts[1:]))
    off = (SIZE - 24 * SCALE) / 2          # centre the 24-unit icon in the button
    rows = []
    for y in range(SIZE):
        row = bytearray([0])
        for x in range(SIZE):
            bg = ink = 0
            for sy in range(SS):
                for sx in range(SS):
                    fx, fy = x + (sx + .5) / SS, y + (sy + .5) / SS
                    if (fx - SIZE / 2) ** 2 + (fy - SIZE / 2) ** 2 <= (SIZE / 2) ** 2:
                        bg += 1
                        ux, uy = (fx - off) / SCALE, (fy - off) / SCALE
                        if (filled and inside(ux, uy, pts)) or min(dist_to_segment(ux, uy, a, b) for a, b in segs) <= 1.0:
                            ink += 1                                     # stroke-width 2, like the site
            n = SS * SS
            a_bg, a_ink = bg / n * bg_alpha, ink / n
            a = a_ink + a_bg * (1 - a_ink)
            if a == 0:
                row += bytes([0, 0, 0, 0])
                continue
            c = [round((255 * a_ink + v * a_bg * (1 - a_ink)) / a) for v in bg_rgb]
            row += bytes(c + [round(a * 255)])
        rows.append(bytes(row))
    return b"".join(rows)


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(png_bytes(SIZE, SIZE, draw(False, (10, 12, 16), 0.6)))         # rgba(10,12,16,.6) like the site's button
    (OUT.parent / "heart-on.png").write_bytes(png_bytes(SIZE, SIZE, draw(True, (163, 33, 58), 1)))  # on the watchlist: accent red
    print(f"Wrote {OUT} and heart-on.png")


if __name__ == "__main__":
    main()
