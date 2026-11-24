"""Map items (map art, maps in item frames) from a world's saved maps.

* ``<world>/data/minecraft/maps/<id>.dat`` (26.x) or ``<world>/data/map_<id>.dat`` (older): NBT ``data`` with
  ``colors`` (128 x 128 bytes, row by row), ``xCenter``, ``zCenter``, ``scale``, ``dimension``, ``locked``.
* A colour byte is ``base * 4 + shade``: the game's map colour ``base`` (its ``MapColor`` table) at one of four
  brightnesses (180, 220, 255, 135 of 255). 0-3 are see-through.
* The colour table is read from the game installed on this PC (``MapColor``'s static initialiser in an
  unobfuscated 26.x client jar: ``new MapColor(id, rgb)``), so nothing of the game is bundled.
* Maps in item frames: ``item_frame`` / ``glow_item_frame`` entities in ``<world>/entities/r.X.Z.mca`` (or the
  dimension's own folder) holding a ``filled_map``; their block, the side they face and their turn.

Read-only: the backups are opened, never written.
"""
from __future__ import annotations

import glob
import json
import os
import re
import struct
import zipfile
import zlib

from . import nbt

SHADES = (180, 220, 255, 135)


# --- the colour table, from the game ---------------------------------------------------------------------------

def _op_len(code: bytes, i: int) -> int:
    """Length of the JVM instruction at *i* (only what a static initialiser needs to be walked)."""
    op = code[i]
    if op in (0x10, 0x12, 0x15, 0x16, 0x17, 0x18, 0x19, 0x36, 0x37, 0x38, 0x39, 0x3a, 0xa9, 0xbc):
        return 2
    if op in (0x11, 0x13, 0x14, 0x84, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xbb, 0xbd, 0xc0, 0xc1,
              0xc6, 0xc7) or 0x99 <= op <= 0xa8:
        return 3
    if op == 0xc5:
        return 4
    if op in (0xb9, 0xba, 0xc8, 0xc9):
        return 5
    if op == 0xc4:                                      # wide
        return 6 if code[i + 1] == 0x84 else 4
    if op in (0xaa, 0xab):                              # tableswitch / lookupswitch: padded to 4
        j = i + 1 + (-(i + 1) % 4)
        if op == 0xaa:
            lo, hi = struct.unpack_from(">ii", code, j + 4)
            return j + 12 + 4 * (hi - lo + 1) - i
        n = struct.unpack_from(">i", code, j + 4)[0]
        return j + 8 + 8 * n - i
    return 1


def palette_from_jar(jar: str) -> dict[int, int] | None:
    """``{base id: 0xRRGGBB}`` from the game's MapColor class, or None (an obfuscated or unreadable jar)."""
    from .mobmodels import _Class
    try:
        with zipfile.ZipFile(jar) as z:
            data = z.read("net/minecraft/world/level/material/MapColor.class")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    try:
        c = _Class(data)
    except (ValueError, struct.error, IndexError):
        return None
    code = c.methods.get(("<clinit>", "()V"), (0, b""))[1]
    ints: list[int] = []
    out: dict[int, int] = {}
    i = 0
    while i < len(code):
        op = code[i]
        if 0x02 <= op <= 0x08:                          # iconst_m1 .. iconst_5
            ints.append(op - 3)
        elif op == 0x10:
            ints.append(struct.unpack_from(">b", code, i + 1)[0])
        elif op == 0x11:
            ints.append(struct.unpack_from(">h", code, i + 1)[0])
        elif op in (0x12, 0x13):
            idx = code[i + 1] if op == 0x12 else struct.unpack_from(">H", code, i + 1)[0]
            v = c.cp[idx]
            if isinstance(v, tuple) and v[0] == "I":
                ints.append(v[1])
        elif op == 0xb7:                                # invokespecial MapColor.<init>(II)V: id, rgb
            try:
                cls, name, desc = c.ref(struct.unpack_from(">H", code, i + 1)[0])
            except (TypeError, IndexError, KeyError):
                cls, name, desc = "", "", ""
            if cls.endswith("MapColor") and name == "<init>" and desc == "(II)V" and len(ints) >= 2:
                out[ints[-2]] = ints[-1] & 0xFFFFFF
            ints.clear()
        i += _op_len(code, i)
    return out if len(out) > 30 else None


_PALETTE: dict[str, list] = {}


def palette(cache_dir: str | None = None) -> list | None:
    """The 256 map colours ``[r, g, b, a]`` (index = the colour byte), from the newest readable game; kept as JSON."""
    from .mobmodels import readable_jars
    if "*" in _PALETTE:                                  # found already (finding the game is slow)
        return _PALETTE["*"]
    for jar in readable_jars():
        key = os.path.basename(jar)
        if key in _PALETTE:
            return _PALETTE[key]
        f = os.path.join(cache_dir, f"mapcolours-{os.path.splitext(key)[0]}.json") if cache_dir else None
        if f and os.path.isfile(f):
            try:
                with open(f, encoding="utf-8") as fh:
                    _PALETTE[key] = _PALETTE["*"] = json.load(fh)
                return _PALETTE[key]
            except (OSError, ValueError):
                pass
        base = palette_from_jar(jar)
        if not base:
            continue
        table = []
        for b in range(64):
            rgb = base.get(b)
            for s in SHADES:
                if not rgb or b == 0:
                    table.append([0, 0, 0, 0])
                else:
                    table.append([((rgb >> 16) & 255) * s // 255, ((rgb >> 8) & 255) * s // 255, (rgb & 255) * s // 255, 255])
        table += [[0, 0, 0, 0]] * (256 - len(table))
        _PALETTE[key] = _PALETTE["*"] = table
        if f:
            os.makedirs(cache_dir, exist_ok=True)
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(table, fh)
        return table
    return None


# --- maps ------------------------------------------------------------------------------------------------------

def map_files(world: str) -> dict[int, str]:
    """``{map id: file}`` of a world's saved maps (either layout)."""
    out = {}
    for f in glob.glob(os.path.join(world, "data", "minecraft", "maps", "*.dat")):
        m = re.fullmatch(r"(\d+)\.dat", os.path.basename(f))
        if m:
            out[int(m.group(1))] = f
    for f in glob.glob(os.path.join(world, "data", "map_*.dat")):
        m = re.fullmatch(r"map_(\d+)\.dat", os.path.basename(f))
        if m:
            out.setdefault(int(m.group(1)), f)
    return out


def read_map(path: str) -> dict | None:
    """``{colors: bytes (16384), x, z, scale, dim, locked}`` or None."""
    try:
        d = (nbt.load(path) or {}).get("data") or {}
    except (OSError, ValueError, EOFError, struct.error):
        return None
    cols = d.get("colors")
    if cols is None or len(cols) != 16384:
        return None
    raw = bytes(cols) if isinstance(cols, (bytes, bytearray)) else bytes(v & 255 for v in cols)
    dim = d.get("dimension")
    if isinstance(dim, int):
        dim = {0: "minecraft:overworld", -1: "minecraft:the_nether", 1: "minecraft:the_end"}.get(dim, "minecraft:overworld")
    return {"colors": raw, "x": d.get("xCenter", 0), "z": d.get("zCenter", 0), "scale": d.get("scale", 0),
            "dim": dim or "minecraft:overworld", "locked": bool(d.get("locked", 0))}


def png(colors: bytes, table: list) -> bytes:
    """A 128 x 128 RGBA PNG of a map's colour bytes."""
    rows = bytearray()
    for y in range(128):
        rows.append(0)
        for b in colors[y * 128:(y + 1) * 128]:
            rows.extend(table[b])
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 128, 128, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b""))


# --- maps in item frames ---------------------------------------------------------------------------------------

def _region_entities(path: str):
    """Every entity compound in an entities region file."""
    from . import terrain
    for _cx, _cz, chunk in terrain.iter_chunks(path):
        for e in chunk.get("Entities") or []:
            if isinstance(e, dict):
                yield e


def _map_id(item: dict) -> int | None:
    if not isinstance(item, dict) or str(item.get("id", "")).replace("minecraft:", "") != "filled_map":
        return None
    comps = item.get("components") or {}
    v = comps.get("minecraft:map_id", (item.get("tag") or {}).get("map"))
    return int(v) if isinstance(v, int) else None


def frames_in_region(path: str) -> list[dict]:
    """The maps hanging in item frames in one entities region: ``{map, x, y, z, facing, rot, glow}`` (the frame's
    block; facing 0 down, 1 up, 2 north, 3 south, 4 west, 5 east; rot 0-7 eighth turns, maps use 0-3)."""
    out = []
    for e in _region_entities(path):
        kind = str(e.get("id", "")).replace("minecraft:", "")
        if kind not in ("item_frame", "glow_item_frame"):
            continue
        mid = _map_id(e.get("Item"))
        if mid is None:
            continue
        x, y, z = (e.get("TileX"), e.get("TileY"), e.get("TileZ"))
        if x is None:
            block = e.get("block_pos")
            if isinstance(block, (list, tuple)) and len(block) == 3:
                x, y, z = block
            else:
                pos = e.get("Pos") or [0, 0, 0]
                x, y, z = (int(v // 1) for v in pos[:3])
        facing = e.get("Facing", e.get("facing", 3))
        out.append({"map": mid, "x": int(x), "y": int(y), "z": int(z), "facing": int(facing) & 7,
                    "rot": int(e.get("ItemRotation", 0)) % 4, "glow": kind == "glow_item_frame"})
    return out


def entity_regions(world: str, dim: str) -> list[str]:
    """The entities region files of a dimension (26.x ``dimensions/<ns>/<name>/entities`` or the older folders)."""
    ns, _, name = dim.partition(":")
    cands = [os.path.join(world, "dimensions", ns, name, "entities")]
    cands.append(os.path.join(world, {"overworld": "", "the_nether": "DIM-1", "the_end": "DIM1"}.get(name, name), "entities"))
    for d in cands:
        files = glob.glob(os.path.join(d, "r.*.*.mca"))
        if files:
            return files
    return []


# --- everything about a world's maps, kept --------------------------------------------------------------------

def world_maps(world: str, cache_dir: str | None = None) -> dict:
    """``{"maps": [{id, x, z, scale, dim, locked}], "frames": [{map, x, y, z, facing, rot, glow, dim}]}`` for a
    world, newest id first. Kept as JSON (made again when a map or entity file changes)."""
    import hashlib
    files = map_files(world)
    regions = {d: entity_regions(world, d) for d in ("minecraft:overworld", "minecraft:the_nether", "minecraft:the_end")}
    stamp = hashlib.sha1(json.dumps([sorted((k, os.path.getmtime(f), os.path.getsize(f)) for k, f in files.items()),
                                     sorted((f, os.path.getmtime(f), os.path.getsize(f)) for fs in regions.values() for f in fs)],
                                    default=str).encode()).hexdigest()
    cf = os.path.join(cache_dir, hashlib.sha1(os.path.abspath(world).encode()).hexdigest()[:16] + ".json") if cache_dir else None
    if cf and os.path.isfile(cf):
        try:
            with open(cf, encoding="utf-8") as fh:
                d = json.load(fh)
            if d.get("stamp") == stamp:
                return d
        except (OSError, ValueError):
            pass
    maps = []
    for mid, f in sorted(files.items(), reverse=True):
        m = read_map(f)
        if m:
            maps.append({"id": mid, "x": m["x"], "z": m["z"], "scale": m["scale"], "dim": m["dim"], "locked": m["locked"]})
    frames = []
    for dim, fs in regions.items():
        for f in fs:
            try:
                frames += [{**fr, "dim": dim} for fr in frames_in_region(f)]
            except (OSError, ValueError, EOFError, struct.error, zlib.error):
                continue
    d = {"stamp": stamp, "maps": maps, "frames": frames}
    if cf:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cf + ".part", "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        os.replace(cf + ".part", cf)
    return d


_FILES: dict[str, dict[int, str]] = {}


def map_png_file(world: str, mid: int, cache_dir: str) -> str | None:
    """A map's picture as a PNG file in *cache_dir* (made again when the map changes)."""
    files = _FILES.get(world)
    if files is None or mid not in files:              # listed once per world (and again for a new map)
        files = _FILES[world] = map_files(world)
    f = files.get(mid)
    table = palette(cache_dir)
    if not f or not table:
        return None
    import hashlib
    key = hashlib.sha1(f"{os.path.abspath(f)}|{os.path.getmtime(f)}|{os.path.getsize(f)}".encode()).hexdigest()[:16]
    out = os.path.join(cache_dir, "png", f"{mid}-{key}.png")
    if not os.path.isfile(out):
        m = read_map(f)
        if not m:
            return None
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out + ".part", "wb") as fh:
            fh.write(png(m["colors"], table))
        os.replace(out + ".part", out)
    return out
