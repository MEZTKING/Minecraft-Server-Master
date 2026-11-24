"""Banners with their patterns, from a world's block entities (for the history viewer's real blocks).

* A banner's block entity: ``patterns`` (1.20.5+: ``[{pattern: "minecraft:stripe_top", color: "red"}]``; older:
  ``Patterns`` ``[{Pattern: "ts", Color: 14}]``), under its block (``<colour>_banner[rotation=0-15]`` standing,
  ``<colour>_wall_banner[facing=...]`` on a wall). The base colour is the block's.
* The page paints each flag from the game's own pattern pictures (``entity/banner/<pattern>.png``, white masks
  tinted with the dye colour) and hangs it at its exact angle; the block builder draws only the pole and bar.

Read-only.
"""
from __future__ import annotations

import os

from . import nbt

DYES = ["white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray", "light_gray", "cyan",
        "purple", "blue", "brown", "green", "red", "black"]
# pre-1.20.5 pattern codes
_OLD = {"b": "base", "bs": "stripe_bottom", "ts": "stripe_top", "ls": "stripe_left", "rs": "stripe_right",
        "cs": "stripe_center", "ms": "stripe_middle", "drs": "stripe_downright", "dls": "stripe_downleft",
        "ss": "small_stripes", "cr": "cross", "sc": "straight_cross", "bt": "triangle_bottom", "tt": "triangle_top",
        "bts": "triangles_bottom", "tts": "triangles_top", "ld": "diagonal_left", "rd": "diagonal_up_right",
        "lud": "diagonal_up_left", "rud": "diagonal_right", "mc": "circle", "mr": "rhombus", "vh": "half_vertical",
        "hh": "half_horizontal", "vhr": "half_vertical_right", "hhb": "half_horizontal_bottom", "bl": "square_bottom_left",
        "br": "square_bottom_right", "tl": "square_top_left", "tr": "square_top_right", "bo": "border",
        "cbo": "curly_border", "gra": "gradient", "gru": "gradient_up", "bri": "bricks", "glb": "globe",
        "cre": "creeper", "sku": "skull", "flo": "flower", "moj": "mojang", "pig": "piglin"}


def patterns_of(be: dict) -> list[list[str]]:
    """``[[pattern, colour], ...]`` of a banner block entity, bottom layer first."""
    out = []
    for p in be.get("patterns") or []:
        if not isinstance(p, dict):
            continue
        pat = p.get("pattern")
        if isinstance(pat, dict):                       # written out in full (a datapack's own): its asset
            pat = pat.get("asset_id") or ""
        col = p.get("color")
        if isinstance(pat, str) and isinstance(col, str):
            out.append([pat.split(":")[-1], col])
    for p in be.get("Patterns") or []:
        if isinstance(p, dict) and p.get("Pattern") in _OLD and isinstance(p.get("Color"), int):
            out.append([_OLD[p["Pattern"]], DYES[p["Color"] % 16]])
    return out


def banners_in_tile(root: str, world: str, dim: str, tx: int, tz: int, size: int = 128) -> list[dict]:
    """Every banner in the tile ``[tx*size, (tx+1)*size)`` x ``[tz*size, ...)``: ``{x, y, z, base, wall, facing,
    rotation, patterns}`` (wall banners: facing north/south/east/west; standing ones: rotation 0-15)."""
    from . import blockmesh as bm
    from .containers import _chunk, _region
    out = []
    c0x, c0z, n = tx * size >> 4, tz * size >> 4, max(1, size >> 4)
    for cx in range(c0x, c0x + n):
        for cz in range(c0z, c0z + n):
            p = _region(root, world, dim, cx << 4, cz << 4)
            raw = _chunk(p, cx, cz) if p else None
            if raw is None or b"banner" not in raw:
                continue
            d = nbt.loads(raw)
            bes = [e for e in d.get("block_entities") or (d.get("Level") or {}).get("TileEntities") or []
                   if isinstance(e, dict) and str(e.get("id", "")).endswith("banner")]
            if not bes:
                continue
            ch = bm.Chunk(raw)
            for e in bes:
                x, y, z = e.get("x"), e.get("y"), e.get("z")
                if not all(isinstance(v, int) for v in (x, y, z)):
                    continue
                state = ch.state(x & 15, y, z & 15) or ""
                name = state.split("[")[0].replace("minecraft:", "")
                if not name.endswith("banner"):
                    continue                            # gone from the block (a stale block entity)
                props = dict(kv.split("=", 1) for kv in state[state.index("[") + 1:-1].split(",")) if "[" in state else {}
                wall = name.endswith("_wall_banner")
                out.append({"x": x, "y": y, "z": z, "base": name[:-len("_wall_banner")] if wall else name[:-len("_banner")],
                            "wall": wall, "facing": props.get("facing", "north"),
                            "rotation": int(props.get("rotation", "0") or 0), "patterns": patterns_of(e)})
    return out
