"""Generate the app icon (mcsm/assets/icon.png and installer/icon.ico).

Pure standard library: draws the icon with simple anti-aliased shapes, encodes
PNGs with zlib, and packs them into a multi-size .ico (PNG-compressed entries,
supported by Windows Vista and later). Re-run after changing the design:

    python installer/make_icon.py
"""

import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

BG_TOP, BG_BOTTOM = (57, 135, 229), (28, 92, 171)   # blue ramp
FG = (255, 255, 255)


def draw(size: int) -> bytes:
    """Return RGBA bytes for a size x size icon."""
    ss = 4  # supersampling for anti-aliasing
    n = size * ss
    radius = 0.2 * n
    bars = [(0.20, 0.30, 0.62), (0.425, 0.525, 0.40), (0.65, 0.75, 0.22)]  # (x0, x1, top) as fractions
    base = 0.80
    out = bytearray()
    for y in range(size):
        for x in range(size):
            cov = fg = 0
            for sy in range(ss):
                for sx in range(ss):
                    px, py = x * ss + sx + 0.5, y * ss + sy + 0.5
                    # rounded square
                    cx = min(max(px, radius), n - radius)
                    cy = min(max(py, radius), n - radius)
                    if (px - cx) ** 2 + (py - cy) ** 2 > radius ** 2:
                        continue
                    cov += 1
                    fx, fy = px / n, py / n
                    if any(x0 <= fx <= x1 and top <= fy <= base for x0, x1, top in bars):
                        fg += 1
            total = ss * ss
            if cov == 0:
                out += b"\0\0\0\0"
                continue
            t = y / max(1, size - 1)
            bg = [round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)]
            a = fg / cov
            rgb = [round(bg[i] * (1 - a) + FG[i] * a) for i in range(3)]
            out += bytes(rgb + [round(255 * cov / total)])
    return bytes(out)


def png(size: int) -> bytes:
    raw = draw(size)
    rows = b"".join(b"\0" + raw[y * size * 4:(y + 1) * size * 4] for y in range(size))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def ico(sizes) -> bytes:
    images = [png(s) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for s, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return header + entries + b"".join(images)


if __name__ == "__main__":
    with open(os.path.join(ROOT, "mcsm", "assets", "icon.png"), "wb") as fh:
        fh.write(png(64))
    with open(os.path.join(HERE, "icon.ico"), "wb") as fh:
        fh.write(ico([16, 24, 32, 48, 64, 128, 256]))
    print("wrote mcsm/assets/icon.png and installer/icon.ico")
