"""What was in a chest, barrel, furnace... at a moment: the backup's contents, then the Ledger log's item moves.

* The recordings don't keep container screens (Flashback and ServerReplay leave out ``open_screen`` and the
  container contents), but they keep chests opening and closing (``block_event``: the lid's viewer count), and the
  Ledger mod logs every item put in or taken out (``item-insert`` / ``item-remove``: the container's block, the
  item and its count, who; not the slot).
* So the contents at T = the container's ``Items`` in the backup saved at or before T (the block entity in its
  region file; a double chest's other half too), then every Ledger move there from the backup's time to T. Taken
  items come off the stacks of that item (last slot first); put-in items top up a stack of that item or take the
  first empty slot. Furnaces' smelting isn't logged (only what players and hoppers put in or take out).

Read-only.
"""
from __future__ import annotations

import os
import re
import struct
import zlib
from datetime import datetime, timezone

from . import nbt, terrain

SIZES = {"chest": 27, "trapped_chest": 27, "barrel": 27, "shulker_box": 27, "furnace": 3, "blast_furnace": 3,
         "smoker": 3, "hopper": 5, "dispenser": 9, "dropper": 9, "brewing_stand": 5, "chiseled_bookshelf": 6,
         "crafter": 9, "decorated_pot": 1, "ender_chest": 27}
_SNBT_ID = re.compile(r'\bid:"?([a-z0-9_:./-]+)"?')
_SNBT_COUNT = re.compile(r'\bcount:(\d+)')


def kind_of(block_id: str) -> str:
    """``minecraft:red_shulker_box`` -> ``shulker_box``; the size table's key."""
    name = block_id.replace("minecraft:", "")
    return "shulker_box" if name.endswith("shulker_box") else name


def _chunk(path: str, cx: int, cz: int) -> bytes | None:
    """One chunk's decompressed NBT from a region file (local chunk coordinates)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192)
            i = (cz & 31) * 32 + (cx & 31)
            off = int.from_bytes(head[i * 4:i * 4 + 3], "big")
            if not off:
                return None
            fh.seek(off * 4096)
            length, comp = struct.unpack(">iB", fh.read(5))
            raw = fh.read(length - 1)
    except (OSError, struct.error):
        return None
    try:
        return zlib.decompress(raw) if comp == 2 else (raw if comp == 3 else None)
    except zlib.error:
        return None


def _region(root: str, world: str, dim: str, x: int, z: int) -> str | None:
    name = f"r.{x >> 9}.{z >> 9}.mca"
    return next((p for p in terrain.region_files(root, world, dim) if os.path.basename(p) == name), None)


def items(lst) -> dict[int, list]:
    """An ``Items`` list -> ``{slot: [item, count]}``."""
    out = {}
    for st in lst or []:
        if isinstance(st, dict) and "Slot" in st:
            out[int(st["Slot"]) & 255] = [str(st.get("id", "?")).replace("minecraft:", ""),
                                         int(st.get("count", st.get("Count", 1)) or 1)]
    return out


def saved(root: str, world: str, dim: str, x: int, y: int, z: int) -> dict | None:
    """The container at x, y, z in a backup: ``{block, state, slots, other: [x, y, z] | None}`` (a double chest's
    other half from its block state), or None."""
    from . import blockmesh as bm
    p = _region(root, world, dim, x, z)
    raw = _chunk(p, x >> 4, z >> 4) if p else None
    if raw is None:
        return None
    state = bm.Chunk(raw).state(x & 15, y, z & 15) or ""
    d = nbt.loads(raw)
    be = next((e for e in d.get("block_entities") or (d.get("Level") or {}).get("TileEntities") or []
               if isinstance(e, dict) and (e.get("x"), e.get("y"), e.get("z")) == (x, y, z)), None)
    if be is None and not state:
        return None
    other = None
    m = re.search(r"type=(left|right)", state)
    f = re.search(r"facing=(north|south|east|west)", state)
    if m and f:                                         # a double chest: the other half beside it
        # looking at the chest's front, "left" means the other half is to its right (the game's ChestType)
        dx, dz = {"north": (1, 0), "south": (-1, 0), "east": (0, 1), "west": (0, -1)}[f.group(1)]
        if m.group(1) == "right":
            dx, dz = -dx, -dz
        other = [x + dx, y, z + dz]
    return {"block": state.split("[")[0] or str((be or {}).get("id", "")), "state": state,
            "slots": items((be or {}).get("Items")), "other": other}


def _ms(text: str) -> int:
    return int(datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000) \
        + (int(text[20:23]) if len(text) >= 23 else 0)


def moves(lg, dim: str, spots: list, t0: int, t1: int) -> list[dict]:
    """Ledger's item moves at the given blocks between t0 and t1 (ms): ``{t, kind: in/out, item, count, who,
    source}``, oldest first."""
    from .ledger import _when
    if not lg or not spots:
        return []
    out = []
    try:
        con = lg._con()
        wid = lg.worlds.get(dim)
        for x, y, z in spots:
            q = ("select a.action_identifier, x.time, x.extra_data, o.identifier, s.name, p.player_name from actions x "
                 "join ActionIdentifiers a on a.id = x.action_id join ObjectIdentifiers o on o.id = x.object_id "
                 "join sources s on s.id = x.source left join players p on p.id = x.player_id "
                 "where x.x = ? and x.y = ? and x.z = ? and a.action_identifier in ('item-insert', 'item-remove') "
                 "and x.time > ? and x.time <= ?" + (" and x.world_id = ?" if wid is not None else "") + " order by x.id")
            args = [x, y, z, _when(t0), _when(t1)] + ([wid] if wid is not None else [])
            for act, when, extra, obj, src, who in con.execute(q, args):
                mi = _SNBT_ID.search(extra or "")
                mc = _SNBT_COUNT.search(extra or "")
                out.append({"t": _ms(when), "kind": "in" if act == "item-insert" else "out",
                            "item": (mi.group(1) if mi else obj).replace("minecraft:", ""),
                            "count": int(mc.group(1)) if mc else 1, "who": who, "source": src})
    except Exception:                                   # noqa: BLE001 -- an older or odd database: no moves
        return []
    return sorted(out, key=lambda m: m["t"])


def apply(slots: dict[int, list], size: int, mv: list[dict], forward: bool = True) -> dict[int, list]:
    """The slots after the moves (or before them, with *forward* False: a backup taken after the moment)."""
    s = {k: list(v) for k, v in slots.items()}
    for m in (mv if forward else reversed(mv)):
        put = (m["kind"] == "in") == forward
        n = m["count"]
        if put:
            for k in sorted(s):                          # top up a stack of it, then the first empty slot
                if s[k][0] == m["item"] and s[k][1] < 64 and n:
                    add = min(n, 64 - s[k][1]); s[k][1] += add; n -= add
            k = 0
            while n and k < size:
                if k not in s:
                    s[k] = [m["item"], min(n, 64)]; n -= s[k][1]
                k += 1
        else:
            for k in sorted(s, reverse=True):            # off the stacks of it, last slot first
                if s[k][0] == m["item"] and n:
                    take = min(n, s[k][1]); s[k][1] -= take; n -= take
                    if s[k][1] <= 0:
                        del s[k]
    return s
