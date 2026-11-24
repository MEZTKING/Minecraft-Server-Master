"""Paintings and item frames (with what's in them), from a world's entity files (for the history viewer).

* A painting: ``variant`` (1.21: ``"minecraft:kebab"``; older ``Motive``), ``facing`` (2D: 0 south, 1 west, 2 north,
  3 east; older ``Facing``), ``Pos`` its centre. Its size comes from its picture (``painting/<name>.png``, 16 pixels a
  block).
* An item frame: ``Facing`` (0 down, 1 up, 2 north, 3 south, 4 west, 5 east), ``ItemRotation`` (eighth turns),
  ``Item``, ``Invisible``, ``Pos`` its centre. Frames holding a map are drawn by the maps code instead.

Read-only.
"""
from __future__ import annotations

import os

from . import mapart


def _entities_of_tile(world: str, dim: str, tx: int, tz: int, size: int):
    rx, rz = (tx * size) >> 9, (tz * size) >> 9
    x0, z0 = tx * size, tz * size
    for p in mapart.entity_regions(world, dim):
        if os.path.basename(p) != f"r.{rx}.{rz}.mca":
            continue
        for e in mapart._region_entities(p):
            pos = e.get("Pos") or []
            if len(pos) == 3 and x0 <= pos[0] < x0 + size and z0 <= pos[2] < z0 + size:
                yield e


def hanging_in_tile(world: str, dim: str, tx: int, tz: int, size: int = 128) -> dict:
    """``{"paintings": [{x, y, z, facing, variant}], "frames": [{x, y, z, facing, rot, item, glow, invisible}]}`` in
    the tile ``[tx*size, ...)`` (x, y, z: the centre; painting facing 2D, frame facing 3D)."""
    paintings, frames = [], []
    for e in _entities_of_tile(world, dim, tx, tz, size):
        kind = str(e.get("id", "")).replace("minecraft:", "")
        x, y, z = (float(v) for v in e["Pos"][:3])
        if kind == "painting":
            var = e.get("variant", e.get("Motive"))
            if isinstance(var, dict):
                var = var.get("asset_id")
            if not isinstance(var, str):
                continue
            facing = e.get("facing", e.get("Facing", 0))
            paintings.append({"x": x, "y": y, "z": z, "facing": int(facing) & 3, "variant": var.split(":")[-1]})
        elif kind in ("item_frame", "glow_item_frame"):
            item = e.get("Item")
            if mapart._map_id(item) is not None:
                continue                                # a map: drawn by the maps code
            iid = str(item.get("id", "")).split(":")[-1] if isinstance(item, dict) else ""
            frames.append({"x": x, "y": y, "z": z, "facing": int(e.get("Facing", e.get("facing", 3))) % 6,
                           "rot": int(e.get("ItemRotation", 0)) % 8, "item": iid, "glow": kind == "glow_item_frame",
                           "invisible": bool(e.get("Invisible"))})
    return {"paintings": paintings, "frames": frames}
