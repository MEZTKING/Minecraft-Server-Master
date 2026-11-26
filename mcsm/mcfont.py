"""The game's own lettering as a font file the browser can use: the client jar's ``textures/font/ascii.png`` (a 16 x 16
grid of glyphs, 8 font pixels each) and its character table (``font/include/default.json``) turned into a TrueType
font, each lit pixel a square (128 units a font pixel, 8 to the em), each glyph as wide as the game measures it (its
last column with any pixel, plus 1), a space 4, the baseline 7 pixels down and lines 9 apart, as the game draws text.
The history viewer's on-screen game text (chat, titles, the action bar, boss bars, toasts...) uses it."""
from __future__ import annotations

import json
import struct
import zipfile
import zlib

VERSION = 1                                          # bump when the font made changes (its cached file)
U = 128                                              # font units a font pixel
ASCENT, LINE = 7, 9


def _png_alpha(data: bytes) -> tuple[int, int, list[int]]:
    """``(width, height, [alpha...])`` of an 8-bit PNG (grey, RGB, RGBA, grey + alpha, palette with tRNS)."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG")
    pos, idat, trns = 8, bytearray(), b""
    w = h = ctype = 0
    while pos < len(data):
        n = struct.unpack_from(">I", data, pos)[0]
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, ctype, _c, _f, inter = struct.unpack(">IIBBBBB", body)
            if inter or depth not in (1, 2, 4, 8) or (depth < 8 and ctype not in (0, 3)):
                raise ValueError("unsupported PNG")
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]          # bytes a pixel (1 for fewer bits than 8)
    raw = zlib.decompress(bytes(idat))
    stride = w * bpp if depth == 8 else (w * depth + 7) // 8
    prev, out = bytearray(stride), []
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], (prev[i - bpp] if i >= bpp else 0)
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        if depth < 8:                                   # 1, 2 or 4 bits a pixel, high bits first
            for x in range(w):
                bit = x * depth
                v = (line[bit >> 3] >> (8 - depth - (bit & 7))) & ((1 << depth) - 1)
                if ctype == 3:
                    out.append(trns[v] if v < len(trns) else 255)
                else:
                    out.append(0 if (len(trns) >= 2 and v == struct.unpack(">H", trns[:2])[0]) or v == 0 else 255)
            prev = line
            continue
        for x in range(w):
            px = line[x * bpp:(x + 1) * bpp]
            if ctype == 6:
                out.append(px[3])
            elif ctype == 4:
                out.append(px[1])
            elif ctype == 3:
                out.append(trns[px[0]] if px[0] < len(trns) else 255)
            else:
                out.append(255 if any(px) else 0)
        prev = line
    return w, h, out


def glyphs_from(png: bytes, rows: list[str]) -> dict[int, tuple[int, list]]:
    """``{codepoint: (advance in font pixels, [(x0, x1, row), ...] lit runs in font pixels)}`` from a glyph sheet and
    its rows of characters (\\0: none)."""
    w, h, alpha = _png_alpha(png)
    cols = max(len(r) for r in rows)
    cw, ch = w // cols, h // len(rows)
    out: dict[int, tuple[int, list]] = {}
    for ry, row in enumerate(rows):
        for rx, chr_ in enumerate(row):
            code = ord(chr_)
            if code == 0 or code in out:
                continue
            runs, last = [], -1
            for y in range(ch):
                x = 0
                while x < cw:
                    if alpha[(ry * ch + y) * w + rx * cw + x] > 0:
                        x0 = x
                        while x < cw and alpha[(ry * ch + y) * w + rx * cw + x] > 0:
                            x += 1
                        runs.append((x0 * 8 / cw, x * 8 / cw, y * 8 / ch, (y + 1) * 8 / ch))
                        last = max(last, x - 1)
                    else:
                        x += 1
            adv = round((last + 1) * 8 / cw) + 1 if last >= 0 else 0
            out[code] = (adv, runs)
    out[32] = (4, [])
    return out


def _checksum(b: bytes) -> int:
    b = b + b"\0" * (-len(b) % 4)
    return sum(struct.unpack(f">{len(b) // 4}I", b)) & 0xFFFFFFFF


def build_ttf(glyphs: dict[int, tuple[int, list]], family: str = "mcsm game") -> bytes:
    """A TrueType font of *glyphs* (see :func:`glyphs_from`)."""
    codes = sorted(c for c in glyphs if 0 < c < 0xFFFF)
    order = [None] + codes                            # glyph 0: .notdef (empty)
    glyf, loca, hmtx = bytearray(), [], bytearray()
    max_pts = max_ctr = 0
    bbox = [0, 0, 0, 0]
    adv_max = 0
    for code in order:
        loca.append(len(glyf))
        adv, runs = glyphs[code] if code is not None else (6, [])
        aw = int(adv * U)
        adv_max = max(adv_max, aw)
        if not runs:
            hmtx += struct.pack(">Hh", aw, 0)
            continue
        pts, ends = [], []
        for x0, x1, y0, y1 in runs:                   # each run a square-cornered box, clockwise (y up)
            top, bot = int((ASCENT - y0) * U), int((ASCENT - y1) * U)
            pts += [(int(x0 * U), top), (int(x1 * U), top), (int(x1 * U), bot), (int(x0 * U), bot)]
            ends.append(len(pts) - 1)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        gb = [min(xs), min(ys), max(xs), max(ys)]
        bbox = [min(bbox[0], gb[0]), min(bbox[1], gb[1]), max(bbox[2], gb[2]), max(bbox[3], gb[3])]
        max_pts, max_ctr = max(max_pts, len(pts)), max(max_ctr, len(ends))
        g = struct.pack(">hhhhh", len(ends), *gb) + struct.pack(f">{len(ends)}H", *ends) + struct.pack(">H", 0)
        g += bytes([1]) * len(pts)                    # every point on the curve, coordinates as 2-byte deltas
        px = py = 0
        for x, _y in pts:
            g += struct.pack(">h", x - px); px = x
        for _x, y in pts:
            g += struct.pack(">h", y - py); py = y
        glyf += g + b"\0" * (-len(g) % 4)
        hmtx += struct.pack(">Hh", aw, gb[0])
    loca.append(len(glyf))
    n = len(order)
    head = struct.pack(">IIIIHHqqhhhhHHhhh", 0x00010000, 0x00010000, 0, 0x5F0F3CF5, 0x000B, 8 * U, 0, 0,
                       bbox[0], bbox[1], bbox[2], bbox[3], 0, 8, 2, 1, 0)
    hhea = struct.pack(">IhhhH" + "h" * 11 + "H", 0x00010000, ASCENT * U, -U, (LINE - 8) * U, adv_max,
                       0, 0, bbox[2], 1, 0, 0, 0, 0, 0, 0, 0, n)
    maxp = struct.pack(">IHHHHHHHHHHHHHH", 0x00010000, n, max_pts, max_ctr, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0)
    os2 = struct.pack(">HhHHH10hh10s4I4sHHHhhhHHIIhhHHH", 4, 6 * U, 400, 5, 0,
                      5 * U, 5 * U, 0, U, 5 * U, 5 * U, 0, 4 * U, U // 2, 3 * U, 0, b"\0" * 10, 1, 0, 0, 0, b"mcsm",
                      0x40, codes[0] if codes else 32, min(codes[-1], 0xFFFF) if codes else 32,
                      ASCENT * U, -U, U, ASCENT * U, U, 1, 0, 5 * U, 7 * U, 0, 32, 1)
    names = {1: family, 2: "Regular", 3: family + " Regular", 4: family, 6: family.replace(" ", "-")}
    recs, strs = b"", b""
    for nid, text in sorted(names.items()):
        e = text.encode("utf-16-be")
        recs += struct.pack(">HHHHHH", 3, 1, 0x409, nid, len(e), len(strs))
        strs += e
    name = struct.pack(">HHH", 0, len(names), 6 + len(recs)) + recs + strs
    post = struct.pack(">IIhhIIIII", 0x00030000, 0, -U, U, 0, 0, 0, 0, 0)
    # cmap format 4: runs of codes whose glyphs follow on, then the closing 0xFFFF segment
    segs, i = [], 0
    while i < len(codes):
        j = i
        while j + 1 < len(codes) and codes[j + 1] == codes[j] + 1:
            j += 1
        segs.append((codes[i], codes[j], (i + 1 - codes[i]) & 0xFFFF))
        i = j + 1
    segs.append((0xFFFF, 0xFFFF, 1))
    sc = len(segs)
    sr = 2 * (1 << (sc.bit_length() - 1))
    sub = struct.pack(">HHHHHHH", 4, 0, 0, 2 * sc, sr, (sr // 2).bit_length() - 1, 2 * sc - sr)
    sub += struct.pack(f">{sc}H", *[e for _s, e, _d in segs]) + struct.pack(">H", 0)
    sub += struct.pack(f">{sc}H", *[s for s, _e, _d in segs]) + struct.pack(f">{sc}H", *[d for _s, _e, d in segs])
    sub += struct.pack(f">{sc}H", *([0] * sc))
    sub = sub[:2] + struct.pack(">H", len(sub)) + sub[4:]
    cmap = struct.pack(">HHHHI", 0, 1, 3, 1, 12) + sub
    tables = {b"OS/2": os2, b"cmap": cmap, b"glyf": bytes(glyf), b"head": head, b"hhea": hhea, b"hmtx": bytes(hmtx),
              b"loca": struct.pack(f">{len(loca)}I", *loca), b"maxp": maxp, b"name": name, b"post": post}
    nt = len(tables)
    es = (1 << (nt.bit_length() - 1))
    out = struct.pack(">IHHHH", 0x00010000, nt, es * 16, es.bit_length() - 1, nt * 16 - es * 16)
    off = 12 + 16 * nt
    body = b""
    for tag in sorted(tables):
        t = tables[tag]
        out += struct.pack(">4sIII", tag, _checksum(t), off + len(body), len(t))
        body += t + b"\0" * (-len(t) % 4)
    font = bytearray(out + body)
    hpos = 12 + 16 * nt + sum(len(tables[t]) + (-len(tables[t]) % 4) for t in sorted(tables) if t < b"head")
    struct.pack_into(">I", font, hpos + 8, (0xB1B0AFBA - _checksum(bytes(font))) & 0xFFFFFFFF)
    return bytes(font)


def game_font(jar: str | None) -> bytes | None:
    """The jar's ASCII lettering as a TrueType font (None when it can't be read)."""
    if not jar:
        return None
    try:
        with zipfile.ZipFile(jar) as zf:
            fonts = json.loads(zf.read("assets/minecraft/font/include/default.json"))
            prov = next(p for p in fonts["providers"] if str(p.get("file", "")).endswith("font/ascii.png"))
            png = zf.read("assets/minecraft/textures/font/ascii.png")
        return build_ttf(glyphs_from(png, prov["chars"]))
    except (OSError, KeyError, ValueError, StopIteration, zipfile.BadZipFile, struct.error, zlib.error):
        return None
