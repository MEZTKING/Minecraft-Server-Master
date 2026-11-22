"""Surface terrain from region files, for the 3D live map and the history viewer.

Region files (``r.<rx>.<rz>.mca``) each hold 32x32 chunks of 16x16 columns:

    header: 1024 x 4-byte locations (3-byte sector offset, 1-byte sector count)
            1024 x 4-byte timestamps
    chunk : int32 length, 1 byte compression (1 gzip, 2 zlib, 3 none, 4 LZ4), NBT

Instead of decoding whole chunks we only need, per sampled column, the
**surface height** and the **block at the surface**:

* Height: the chunk's ``MOTION_BLOCKING`` heightmap (top block that blocks
  movement *or* is a fluid -- so water and leaves count, flowers and grass
  tufts don't). Heightmaps are packed into longs, ``bits`` per value, values
  never spanning two longs (format since 1.16).
* Block: look up that Y in the section's block-state palette (bit-packed the
  same way, at least 4 bits per entry).

Supported chunk formats: 1.18+ (``sections``/``block_states``, Y from -64) and
1.16-1.17 (``Level``/``Sections``/``BlockStates``/``Palette``, Y from 0).
Older worlds have no usable heightmap and are skipped.

The Nether has a bedrock roof, so its heightmap is useless; only the Overworld
and the End get terrain. Players are still shown in every dimension.

The output is a list of tiles (one per region) with a height grid and colour
grid, sampled every ``step`` blocks to keep big worlds fast and light.
"""

from __future__ import annotations

import gzip
import math
import os
import struct
import zlib
from dataclasses import dataclass
from typing import Callable, Iterator

from . import nbt

# --- approximate top-down colours -------------------------------------------

_COLOURS = {
    "grass_block": (106, 153, 64), "dirt": (134, 96, 67), "coarse_dirt": (119, 85, 59), "podzol": (91, 63, 24),
    "rooted_dirt": (144, 103, 76), "mud": (60, 57, 60), "dirt_path": (148, 122, 65), "farmland": (110, 75, 45),
    "mycelium": (111, 99, 105), "moss_block": (89, 109, 45), "snow": (240, 251, 251), "snow_block": (240, 251, 251),
    "powder_snow": (248, 253, 253), "ice": (145, 183, 253), "packed_ice": (141, 180, 250), "blue_ice": (116, 167, 253),
    "water": (63, 118, 228), "lava": (207, 92, 20), "sand": (219, 207, 163), "red_sand": (190, 102, 33),
    "gravel": (131, 127, 126), "clay": (160, 166, 179), "stone": (125, 125, 125), "cobblestone": (127, 127, 127),
    "deepslate": (80, 80, 82), "andesite": (136, 136, 137), "diorite": (188, 188, 188), "granite": (149, 103, 85),
    "tuff": (108, 109, 102), "calcite": (223, 224, 220), "sandstone": (216, 203, 155), "red_sandstone": (186, 99, 29),
    "terracotta": (152, 94, 67), "bedrock": (85, 85, 85), "obsidian": (15, 10, 24), "netherrack": (97, 38, 38),
    "end_stone": (219, 222, 158), "purpur_block": (169, 125, 169), "bamboo": (93, 144, 19), "cactus": (88, 129, 39),
    "sugar_cane": (148, 192, 101), "pumpkin": (198, 118, 24), "melon": (111, 145, 30), "hay_block": (166, 136, 38),
    "glass": (200, 220, 230), "bricks": (150, 97, 83), "stone_bricks": (122, 121, 122), "mossy_cobblestone": (110, 118, 95),
    "torch": (255, 216, 99), "glowstone": (171, 131, 84), "sea_lantern": (172, 199, 190), "lily_pad": (32, 128, 48),
    "kelp": (87, 130, 52), "seagrass": (51, 108, 36), "magma_block": (142, 63, 31), "soul_sand": (81, 62, 50),
    "crimson_nylium": (130, 31, 31), "warped_nylium": (43, 114, 101), "chest": (163, 117, 47), "crafting_table": (130, 92, 55),
    "furnace": (110, 110, 110), "iron_block": (220, 220, 220), "gold_block": (246, 208, 61), "diamond_block": (98, 237, 228),
    "emerald_block": (42, 203, 87), "redstone_block": (175, 24, 5), "lapis_block": (30, 67, 140), "copper_block": (192, 107, 79),
    "quartz_block": (235, 229, 222), "prismarine": (99, 156, 151), "mangrove_roots": (74, 59, 38),
}
_SUFFIXES = [  # (suffix, colour) checked in order
    ("_leaves", (58, 110, 40)), ("_log", (102, 81, 51)), ("_wood", (102, 81, 51)), ("_planks", (162, 130, 78)),
    ("_stairs", (140, 120, 100)), ("_slab", (140, 120, 100)), ("_fence", (150, 120, 80)), ("_wall", (120, 120, 120)),
    ("_carpet", (200, 200, 200)), ("_bed", (160, 40, 40)), ("_door", (140, 110, 70)), ("_trapdoor", (140, 110, 70)),
    ("_ore", (125, 125, 125)), ("_glass", (190, 210, 220)), ("_glass_pane", (190, 210, 220)), ("_flower", (200, 80, 80)),
    ("_mushroom", (150, 110, 90)), ("_coral", (200, 80, 150)), ("_coral_block", (200, 80, 150)),
]
_DYE = {"white": (233, 236, 236), "orange": (240, 118, 19), "magenta": (189, 68, 179), "light_blue": (58, 175, 217),
        "yellow": (248, 197, 39), "lime": (112, 185, 25), "pink": (237, 141, 172), "gray": (62, 68, 71),
        "light_gray": (142, 142, 134), "cyan": (21, 137, 145), "purple": (121, 42, 172), "blue": (53, 57, 157),
        "brown": (114, 71, 40), "green": (84, 109, 27), "red": (160, 39, 34), "black": (20, 21, 25)}


def block_colour(name: str) -> tuple[int, int, int]:
    """Approximate top-down colour for a block id (``minecraft:`` prefix optional)."""
    key = name.split(":", 1)[-1]
    if key in _COLOURS:
        return _COLOURS[key]
    for dye, rgb in _DYE.items():
        if key.startswith(dye + "_") and key[len(dye) + 1:] in ("wool", "concrete", "concrete_powder", "terracotta",
                                                                "glazed_terracotta", "carpet", "stained_glass", "bed"):
            return rgb
    for suffix, rgb in _SUFFIXES:
        if key.endswith(suffix):
            return rgb
    # Deterministic muted colour for anything unknown (modded blocks etc.).
    h = zlib.crc32(key.encode()) & 0xFFFFFF
    return (90 + (h & 0x3F), 90 + ((h >> 8) & 0x3F), 90 + ((h >> 16) & 0x3F))


# --- region / chunk reading ------------------------------------------------

def iter_chunks(path: str) -> Iterator[tuple[int, int, dict]]:
    """Yield ``(local_cx, local_cz, chunk_nbt)`` for every chunk in a region file."""
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 8192:
        return
    for i in range(1024):
        off = int.from_bytes(data[i * 4:i * 4 + 3], "big")
        if off == 0:
            continue
        start = off * 4096
        if start + 5 > len(data):
            continue
        length = struct.unpack(">i", data[start:start + 4])[0]
        comp = data[start + 4]
        raw = data[start + 5:start + 4 + length]
        try:
            if comp == 2:
                raw = zlib.decompress(raw)
            elif comp == 1:
                raw = gzip.decompress(raw)
            elif comp != 3:
                continue  # 4 = LZ4 (optional in 1.20.5+), 128+ = external .mcc file: not supported
            yield i % 32, i // 32, nbt.loads(raw)
        except (zlib.error, OSError, EOFError, nbt.NBTError, struct.error):
            continue


def iter_raw_chunks(path: str) -> Iterator[tuple[int, int, bytes]]:
    """Like :func:`iter_chunks`, but yields the decompressed NBT bytes (not decoded)."""
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 8192:
        return
    for i in range(1024):
        off = int.from_bytes(data[i * 4:i * 4 + 3], "big")
        if off == 0:
            continue
        start = off * 4096
        if start + 5 > len(data):
            continue
        length = struct.unpack_from(">i", data, start)[0]
        comp = data[start + 4]
        raw = data[start + 5:start + 4 + length]
        try:
            if comp == 2:
                raw = zlib.decompress(raw)
            elif comp == 1:
                raw = gzip.decompress(raw)
            elif comp != 3:
                continue
        except (zlib.error, OSError, EOFError):
            continue
        yield i % 32, i // 32, raw


# --- fast partial NBT reading --------------------------------------------------
# Decoding a whole chunk into Python objects is ~90% of the time. The map needs only the
# heightmap and each section's palette + block data, so this reader walks the bytes, keeps
# those tags and skips everything else (entities, lighting, biomes...) without building objects.
# Specs: {tag name (bytes): "val" | "palette" | sub-spec dict | ("list", sub-spec)}.

_FIXED = {1: 1, 2: 2, 3: 4, 4: 8, 5: 4, 6: 8}
_ARRAY_ITEM = {7: 1, 11: 4, 12: 8}
_U16, _I32 = struct.Struct(">H"), struct.Struct(">i")

_SEC_SPEC = {b"Y": "val", b"block_states": {b"palette": "lazy", b"data": "lazy"}}
_HM_SPEC = {b"MOTION_BLOCKING": "val", b"WORLD_SURFACE": "val"}
_OLD_SEC_SPEC = {b"Y": "val", b"Palette": "lazy", b"BlockStates": "lazy"}
_ROOT_SPEC = {b"yPos": "val", b"Heightmaps": _HM_SPEC, b"sections": ("list", _SEC_SPEC),
              b"Level": {b"Heightmaps": _HM_SPEC, b"Sections": ("list", _OLD_SEC_SPEC)}}
_NAME_SPEC = {b"Name": "val", b"id": "val", b"": "val"}    # see _block_name for the formats


def _skip(b: bytes, t: int, pos: int) -> int:
    if t in _FIXED:
        return pos + _FIXED[t]
    if t in _ARRAY_ITEM:
        return pos + 4 + _I32.unpack_from(b, pos)[0] * _ARRAY_ITEM[t]
    if t == 8:
        return pos + 2 + _U16.unpack_from(b, pos)[0]
    if t == 9:
        et, n = b[pos], _I32.unpack_from(b, pos + 1)[0]
        pos += 5
        if n <= 0:
            return pos
        if et in _FIXED:
            return pos + n * _FIXED[et]
        for _ in range(n):
            pos = _skip(b, et, pos)
        return pos
    if t == 10:
        return _skip_compound(b, pos)
    raise ValueError(f"bad NBT tag {t}")


def _skip_compound(b: bytes, pos: int) -> int:
    # Hot path (block palettes are thousands of small compounds): handle the common tags
    # inline and only call out for nested lists/compounds/arrays.
    while True:
        ct = b[pos]
        if ct == 0:
            return pos + 1
        pos += 3 + (b[pos + 1] << 8 | b[pos + 2])          # tag byte + name
        if ct == 8:
            pos += 2 + (b[pos] << 8 | b[pos + 1])
        elif ct in _FIXED:
            pos += _FIXED[ct]
        elif ct == 10:
            pos = _skip_compound(b, pos)
        else:
            pos = _skip(b, ct, pos)


def _value(b: bytes, t: int, pos: int):
    if t == 1:
        return struct.unpack_from(">b", b, pos)[0], pos + 1
    if t == 2:
        return struct.unpack_from(">h", b, pos)[0], pos + 2
    if t == 3:
        return _I32.unpack_from(b, pos)[0], pos + 4
    if t == 4:
        return struct.unpack_from(">q", b, pos)[0], pos + 8
    if t == 8:
        n = _U16.unpack_from(b, pos)[0]
        return b[pos + 2:pos + 2 + n].decode("utf-8", "replace"), pos + 2 + n
    if t in (11, 12):
        n = _I32.unpack_from(b, pos)[0]
        fmt = "i" if t == 11 else "q"
        return list(struct.unpack_from(f">{n}{fmt}", b, pos + 4)), pos + 4 + n * _ARRAY_ITEM[t]
    return None, _skip(b, t, pos)


def _compound(b: bytes, pos: int, spec: dict) -> tuple[dict, int]:
    out: dict = {}
    while True:
        t = b[pos]
        pos += 1
        if t == 0:
            return out, pos
        nl = _U16.unpack_from(b, pos)[0]
        name = b[pos + 2:pos + 2 + nl]
        pos += 2 + nl
        sub = spec.get(name)
        if sub is None:
            pos = _skip(b, t, pos)
        elif sub == "lazy":                  # remember where it is; decode only if needed
            out[name] = (t, pos)
            pos = _skip(b, t, pos)
        elif sub == "val":
            out[name], pos = _value(b, t, pos)
        elif sub == "palette" and t == 9:
            et, n = b[pos], _I32.unpack_from(b, pos + 1)[0]
            pos += 5
            names = []
            for _ in range(max(0, n)):
                if et == 8:
                    v, pos = _value(b, 8, pos)
                    names.append(v)
                elif et == 10:
                    d, pos = _compound(b, pos, _NAME_SPEC)
                    names.append(d.get(b"Name") or d.get(b"id") or d.get(b"") or "minecraft:air")
                else:
                    pos = _skip(b, et, pos)
            out[name] = names
        elif isinstance(sub, dict) and t == 10:
            out[name], pos = _compound(b, pos, sub)
        elif isinstance(sub, tuple) and t == 9:
            et, n = b[pos], _I32.unpack_from(b, pos + 1)[0]
            pos += 5
            items = []
            for _ in range(max(0, n)):
                if et == 10:
                    d, pos = _compound(b, pos, sub[1])
                    items.append(d)
                else:
                    pos = _skip(b, et, pos)
            out[name] = items
        else:
            pos = _skip(b, t, pos)


def _compound_list_palette(b: bytes, pos: int) -> tuple[list[str], int]:
    et, n = b[pos], _I32.unpack_from(b, pos + 1)[0]
    pos += 5
    names = []
    for _ in range(max(0, n)):
        if et == 8:
            v, pos = _value(b, 8, pos)
            names.append(v)
        elif et == 10:
            d, pos = _compound(b, pos, _NAME_SPEC)
            names.append(d.get(b"Name") or d.get(b"id") or d.get(b"") or "minecraft:air")
        else:
            pos = _skip(b, et, pos)
    return names, pos


class _LazySections(dict):
    """``{section_y: (palette, data)}`` that decodes a section only when it is looked up."""

    def __init__(self, raw: bytes):
        super().__init__()
        self.raw = raw

    def get(self, key, default=None):
        v = super().get(key, default)
        if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], tuple):
            (pt, ppos), data = v
            pal = _compound_list_palette(self.raw, ppos)[0] if pt == 9 else []
            longs = _value(self.raw, data[0], data[1])[0] if data else []
            v = (pal, longs or [])
            if not pal:
                super().__delitem__(key)
                return default
            self[key] = v
        return v


def _fast_sections(raw: bytes) -> tuple[dict[int, tuple[list, list[int]]], int, dict]:
    """:func:`_sections` straight from chunk bytes (same result, much faster). Sections are
    decoded lazily, on first lookup."""
    if not raw or raw[0] != 10:
        raise ValueError("not an NBT compound")
    pos = 3 + _U16.unpack_from(raw, 1)[0]
    root, _ = _compound(raw, pos, _ROOT_SPEC)
    out = _LazySections(raw)
    if b"sections" in root:                                   # 1.18+
        secs = [(s.get(b"Y", 0), (s.get(b"block_states") or {}).get(b"palette"),
                 (s.get(b"block_states") or {}).get(b"data")) for s in root[b"sections"]]
        min_y = root.get(b"yPos", -4) * 16
        hm = root.get(b"Heightmaps") or {}
    else:                                                     # 1.16-1.17
        level = root.get(b"Level") or {}
        secs = [(s.get(b"Y", 0), s.get(b"Palette"), s.get(b"BlockStates")) for s in level.get(b"Sections") or []]
        min_y = 0
        hm = level.get(b"Heightmaps") or {}
    for y, pal, data in secs:
        if pal and pal[0] == 9 and _I32.unpack_from(raw, pal[1] + 1)[0] > 0:   # non-empty palette
            dict.__setitem__(out, y, (pal, data))
    return out, min_y, {k.decode(): v for k, v in hm.items()}


def _unpack(longs: list[int], bits: int, index: int) -> int:
    per = 64 // bits
    word = longs[index // per] & 0xFFFFFFFFFFFFFFFF
    return (word >> ((index % per) * bits)) & ((1 << bits) - 1)


def _block_name(p) -> str:
    """A palette entry. Seen in real worlds:

    * ``{"Name": ..., "Properties": {...}}`` (1.13 – 26.2);
    * 26.3 (data version 5023): a plain string when every entry is a plain name; otherwise a
      list of compounds where blocks with properties are ``{"id": ..., "properties": {...}}``
      and plain names are wrapped as ``{"": "minecraft:stone"}`` (NBT lists hold one type).
    """
    if isinstance(p, str):
        return p
    if isinstance(p, dict):
        return p.get("Name") or p.get("id") or p.get("") or "minecraft:air"
    return "minecraft:air"


def _sections(chunk: dict) -> tuple[dict[int, tuple[list, list[int]]], int, dict]:
    """Return ``({section_y: (palette_names, data)}, min_y, heightmaps)``."""
    level = chunk.get("Level", chunk)  # 1.16-1.17 wrap everything in "Level"
    out = {}
    if "sections" in chunk:  # 1.18+
        for s in chunk["sections"]:
            bs = s.get("block_states") or {}
            pal = [_block_name(p) for p in bs.get("palette", [])]
            if pal:
                out[s.get("Y", 0)] = (pal, bs.get("data") or [])
        min_y = chunk.get("yPos", -4) * 16
        heightmaps = chunk.get("Heightmaps", {})
    else:  # 1.16-1.17
        for s in level.get("Sections", []):
            pal = [_block_name(p) for p in s.get("Palette", [])]
            if pal:
                out[s.get("Y", 0)] = (pal, s.get("BlockStates") or [])
        min_y = 0
        heightmaps = level.get("Heightmaps", {})
    return out, min_y, heightmaps


def _block_at(sections: dict, x: int, y: int, z: int) -> str | None:
    sec = sections.get(y >> 4)
    if sec is None:
        return None
    pal, data = sec
    if len(pal) == 1 or not data:
        return pal[0]
    bits = max(4, math.ceil(math.log2(len(pal))))
    idx = _unpack(data, bits, ((y & 15) * 256) + z * 16 + x)
    return pal[idx] if idx < len(pal) else None


@dataclass
class Tile:
    """Surface of one region, sampled every ``step`` blocks."""

    rx: int
    rz: int
    step: int
    size: int                  # samples per side = 512 // step
    heights: list[int]         # size*size, row-major (z then x); -32768 = no data
    colours: bytes             # size*size*3 RGB

    NO_DATA = -32768


def read_region_surface(path: str, step: int = 4) -> Tile | None:
    name = os.path.basename(path)
    try:
        _, rx, rz, _ = name.split(".")
        rx, rz = int(rx), int(rz)
    except ValueError:
        return None
    size = 512 // step
    heights = [Tile.NO_DATA] * (size * size)
    colours = bytearray(size * size * 3)
    found = False
    offsets = range(0, 16, step) if step <= 16 else [0]
    colour_cache: dict[str, bytes] = {}
    for cx, cz, raw in iter_raw_chunks(path):
        if step > 16 and ((cx * 16) % step or (cz * 16) % step):
            continue
        try:
            sections, min_y, hm = _fast_sections(raw)
        except (ValueError, IndexError, struct.error):
            continue
        motion = hm.get("MOTION_BLOCKING") or hm.get("WORLD_SURFACE")
        if not motion or not sections:
            continue
        # 256 values packed floor(64/bits) per long: recover bits from the array length
        # (9 for 1.18+ worlds, which are 384 blocks tall).
        bits = next((b for b in range(1, 17) if math.ceil(256 / (64 // b)) == len(motion)), 9)
        for lz in offsets:
            for lx in offsets:
                top = _unpack(motion, bits, lz * 16 + lx) + min_y - 1
                if top < min_y:
                    continue
                block = _block_at(sections, lx, top, lz)
                if block is None or block.endswith(":air"):
                    continue
                sx, sz = (cx * 16 + lx) // step, (cz * 16 + lz) // step
                i = sz * size + sx
                heights[i] = top
                rgb = colour_cache.get(block)
                if rgb is None:
                    rgb = colour_cache[block] = bytes(block_colour(block))
                colours[i * 3:i * 3 + 3] = rgb
                found = True
    return Tile(rx, rz, step, size, heights, bytes(colours)) if found else None


# --- tile cache ------------------------------------------------------------------
# One small file per region and sampling step, keyed by the region file's name, size and
# modification time, so later runs (and the live map / history viewer) load terrain instantly.
# An empty file means "this region has no surface".

_TILE_HEAD = struct.Struct(">4sBiiHH")       # magic, version, rx, rz, step, size
_TILE_MAGIC, _TILE_VERSION = b"MCST", 1


def default_cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mcsm", "terrain")


def tile_key(path: str, step: int) -> str:
    """Identity of a region's tile: file name, size, modification time and step. The same
    region in two backups (unchanged between them) has the same key, so it is read once."""
    st = os.stat(path)
    key = f"{_TILE_VERSION}|{os.path.basename(path)}|{st.st_size}|{st.st_mtime_ns}|{step}"
    return f"{zlib.crc32(key.encode()):08x}-{os.path.basename(path)}-{step}"


def tile_cache_file(path: str, step: int, cache_dir: str) -> str:
    return os.path.join(cache_dir, tile_key(path, step) + ".tile")


def save_tile(file: str, tile: Tile | None) -> None:
    os.makedirs(os.path.dirname(file), exist_ok=True)
    tmp = file + ".tmp"
    with open(tmp, "wb") as fh:
        if tile is not None:
            fh.write(_TILE_HEAD.pack(_TILE_MAGIC, _TILE_VERSION, tile.rx, tile.rz, tile.step, tile.size))
            fh.write(struct.pack(f">{len(tile.heights)}h", *tile.heights))
            fh.write(tile.colours)
    os.replace(tmp, file)


def load_tile(file: str) -> tuple[bool, Tile | None]:
    """``(found, tile)``: found is False when there is no usable cache entry."""
    try:
        with open(file, "rb") as fh:
            data = fh.read()
    except OSError:
        return False, None
    if not data:
        return True, None
    try:
        magic, ver, rx, rz, step, size = _TILE_HEAD.unpack_from(data, 0)
        if magic != _TILE_MAGIC or ver != _TILE_VERSION:
            return False, None
        n = size * size
        heights = list(struct.unpack_from(f">{n}h", data, _TILE_HEAD.size))
        colours = data[_TILE_HEAD.size + 2 * n:_TILE_HEAD.size + 5 * n]
        if len(colours) != 3 * n:
            return False, None
        return True, Tile(rx, rz, step, size, heights, colours)
    except struct.error:
        return False, None


def read_region_cached(path: str, step: int, cache_dir: str | None) -> Tile | None:
    """:func:`read_region_surface` through the tile cache. Safe to run in a worker process."""
    if not cache_dir:
        return read_region_surface(path, step)
    try:
        cf = tile_cache_file(path, step, cache_dir)
    except OSError:
        return read_region_surface(path, step)
    found, tile = load_tile(cf)
    if found:
        return tile
    tile = read_region_surface(path, step)
    try:
        save_tile(cf, tile)
    except OSError:
        pass
    return tile


DIMENSION_DIRS = {  # 26.1+ layout first
    "minecraft:overworld": ["{w}/dimensions/minecraft/overworld/region", "{w}/region"],
    "minecraft:the_end": ["{w}/dimensions/minecraft/the_end/region", "{w}/DIM1/region", "{w}_the_end/DIM1/region"],
}


def region_files(root: str, world: str, dimension: str) -> list[str]:
    w = os.path.relpath(world, root)
    for pat in DIMENSION_DIRS.get(dimension, []):
        folder = os.path.join(root, pat.format(w=w))
        if os.path.isdir(folder):
            return sorted(os.path.join(folder, f) for f in os.listdir(folder)
                          if f.startswith("r.") and f.endswith(".mca"))
    return []


def auto_step(n_regions: int, budget: int = 3_000_000) -> int:
    """Pick the coarsest-needed sampling so the whole map stays under *budget* samples."""
    for step in (1, 2, 4, 8, 16, 32):
        if n_regions * (512 // step) ** 2 <= budget:
            return step
    return 32


def build_terrain(root: str, world: str, dimension: str = "minecraft:overworld", step: int | None = None,
                  progress: Callable[[int, int], None] | None = None) -> list[Tile]:
    files = region_files(root, world, dimension)
    step = step or auto_step(len(files))
    tiles = []
    for i, path in enumerate(files):
        t = read_region_surface(path, step)
        if t:
            tiles.append(t)
        if progress:
            progress(i + 1, len(files))
    return tiles


def write_region(path: str, chunks: dict[tuple[int, int], dict]) -> None:
    """Write a region file from ``{(local_cx, local_cz): chunk_nbt}`` (used by tests/demo)."""
    header = bytearray(8192)
    body = bytearray()
    sector = 2
    for (cx, cz), chunk in sorted(chunks.items()):
        payload = zlib.compress(nbt.dumps(chunk, compress=False))
        blob = struct.pack(">iB", len(payload) + 1, 2) + payload
        blob += b"\0" * (-len(blob) % 4096)
        n = len(blob) // 4096
        i = cz * 32 + cx
        header[i * 4:i * 4 + 4] = sector.to_bytes(3, "big") + bytes([n])
        body += blob
        sector += n
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(bytes(header) + bytes(body))


def make_chunk(cx: int, cz: int, height_fn: Callable[[int, int], int], block_fn: Callable[[int, int, int], str]) -> dict:
    """Build a 1.18+ chunk NBT with a correct heightmap (for tests and the demo world)."""
    min_y = -64
    sections: dict[int, list[str]] = {}
    tops = []
    for z in range(16):
        for x in range(16):
            wx, wz = cx * 16 + x, cz * 16 + z
            h = height_fn(wx, wz)
            tops.append(h)
    # Only store the top block of each column plus stone below it in that section (enough for the map).
    sec_blocks: dict[int, list[str | None]] = {}
    for z in range(16):
        for x in range(16):
            h = tops[z * 16 + x]
            sy = h >> 4
            arr = sec_blocks.setdefault(sy, [None] * 4096)
            arr[((h & 15) * 256) + z * 16 + x] = block_fn(cx * 16 + x, h, cz * 16 + z)
    out_sections = []
    for sy, arr in sorted(sec_blocks.items()):
        names = ["minecraft:air"] + sorted({b for b in arr if b})
        index = {n: i for i, n in enumerate(names)}
        bits = max(4, math.ceil(math.log2(len(names))))
        per = 64 // bits
        longs = [0] * math.ceil(4096 / per)
        for i, b in enumerate(arr):
            v = index[b] if b else 0
            longs[i // per] |= v << ((i % per) * bits)
        longs = [l - (1 << 64) if l >= 1 << 63 else l for l in longs]
        out_sections.append({"Y": nbt.Typed(nbt.TAG_BYTE, sy),
                             "block_states": {"palette": [{"Name": n} for n in names],
                                              "data": nbt.Typed(nbt.TAG_LONG_ARRAY, longs)}})
    bits = 9
    per = 64 // bits
    hm = [0] * math.ceil(256 / per)
    for i, h in enumerate(tops):
        hm[i // per] |= (h - min_y + 1) << ((i % per) * bits)
    hm = [l - (1 << 64) if l >= 1 << 63 else l for l in hm]
    return {"DataVersion": 3700, "xPos": cx, "zPos": cz, "yPos": -4, "Status": "minecraft:full",
            "sections": out_sections, "Heightmaps": {"MOTION_BLOCKING": nbt.Typed(nbt.TAG_LONG_ARRAY, hm)}}
