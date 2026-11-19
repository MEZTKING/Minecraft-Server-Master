"""Surface terrain from region files, for the 3D live map.

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


def _unpack(longs: list[int], bits: int, index: int) -> int:
    per = 64 // bits
    word = longs[index // per] & 0xFFFFFFFFFFFFFFFF
    return (word >> ((index % per) * bits)) & ((1 << bits) - 1)


def _sections(chunk: dict) -> tuple[dict[int, tuple[list, list[int]]], int, dict]:
    """Return ``({section_y: (palette_names, data)}, min_y, heightmaps)``."""
    level = chunk.get("Level", chunk)  # 1.16-1.17 wrap everything in "Level"
    out = {}
    if "sections" in chunk:  # 1.18+
        for s in chunk["sections"]:
            bs = s.get("block_states") or {}
            pal = [p.get("Name", "minecraft:air") for p in bs.get("palette", [])]
            if pal:
                out[s.get("Y", 0)] = (pal, bs.get("data") or [])
        min_y = chunk.get("yPos", -4) * 16
        heightmaps = chunk.get("Heightmaps", {})
    else:  # 1.16-1.17
        for s in level.get("Sections", []):
            pal = [p.get("Name", "minecraft:air") for p in s.get("Palette", [])]
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
    for cx, cz, chunk in iter_chunks(path):
        if step > 16 and ((cx * 16) % step or (cz * 16) % step):
            continue
        sections, min_y, hm = _sections(chunk)
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
                colours[i * 3:i * 3 + 3] = bytes(block_colour(block))
                found = True
    return Tile(rx, rz, step, size, heights, bytes(colours)) if found else None


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
