"""Player heads and whose skin they wear, from a world's block entities (for the history viewer's real blocks).

* A player head's block entity (``minecraft:skull``): ``profile`` (1.20.5+: ``{name, id, properties: [{name:
  "textures", value: <base64 JSON>}]}``, or just a name) or ``SkullOwner`` (older: ``{Name, Id, Properties:
  {textures: [{Value: ...}]}}``). The base64 JSON has ``textures.SKIN.url`` (textures.minecraft.net/texture/<hash>).
* The page draws the head with that skin (``/skintex/<hash>.png``: the same skin store as the player figures, from
  Mojang's texture server only) over the block builder's plain head.

Read-only.
"""
from __future__ import annotations

import base64
import json
import re

from . import nbt

_HASH = re.compile(r"/texture/([0-9a-f]{16,128})$")


def skin_hash(be: dict) -> str | None:
    """The skin's texture hash of a player head block entity, if it has one."""
    prof = be.get("profile")
    props = None
    if isinstance(prof, dict):
        props = prof.get("properties")
    owner = be.get("SkullOwner")
    if props is None and isinstance(owner, dict):
        p = owner.get("Properties")
        if isinstance(p, dict):
            props = [{"name": "textures", "value": t.get("Value")} for t in p.get("textures") or [] if isinstance(t, dict)]
    for p in props or []:
        if not isinstance(p, dict) or p.get("name") != "textures" or not isinstance(p.get("value"), str):
            continue
        try:
            data = json.loads(base64.b64decode(p["value"] + "=" * (-len(p["value"]) % 4)).decode("utf-8", "replace"))
        except (ValueError, TypeError):
            continue
        url = ((data.get("textures") or {}).get("SKIN") or {}).get("url") or ""
        m = _HASH.search(url)
        if m:
            return m.group(1)
    return None


def statues_in_tile(root: str, world: str, dim: str, tx: int, tz: int, size: int = 128) -> list[dict]:
    """Every copper golem statue in the tile: ``{x, y, z, pose (standing, sitting, running, star), facing, weather
    (0 new .. 3 oxidized)}`` (the game draws them with the copper golem's own models; the block has none)."""
    from . import blockmesh as bm
    from .containers import _chunk, _region
    out = []
    c0x, c0z, n = tx * size >> 4, tz * size >> 4, max(1, size >> 4)
    for cx in range(c0x, c0x + n):
        for cz in range(c0z, c0z + n):
            p = _region(root, world, dim, cx << 4, cz << 4)
            raw = _chunk(p, cx, cz) if p else None
            if raw is None or b"copper_golem_statue" not in raw:
                continue
            ch = bm.Chunk(raw)
            d = nbt.loads(raw)
            for e in d.get("block_entities") or []:
                if not isinstance(e, dict) or "copper_golem_statue" not in str(e.get("id", "")):
                    continue
                x, y, z = e.get("x"), e.get("y"), e.get("z")
                if not all(isinstance(v, int) for v in (x, y, z)):
                    continue
                state = ch.state(x & 15, y, z & 15) or ""
                name = state.split("[")[0].replace("minecraft:", "").replace("waxed_", "")
                if not name.endswith("copper_golem_statue"):
                    continue
                props = dict(kv.split("=", 1) for kv in state[state.index("[") + 1:-1].split(",")) if "[" in state else {}
                weather = {"": 0, "exposed_": 1, "weathered_": 2, "oxidized_": 3}.get(name[:-len("copper_golem_statue")], 0)
                out.append({"x": x, "y": y, "z": z, "pose": props.get("copper_golem_pose", "standing"),
                            "facing": props.get("facing", "north"), "weather": weather})
    return out


def spawners_in_tile(root: str, world: str, dim: str, tx: int, tz: int, size: int = 128) -> list[dict]:
    """Every monster spawner in the tile and the mob it makes: ``{x, y, z, mob}`` (its ``SpawnData.entity.id``; the
    game draws that mob turning inside the cage)."""
    from .containers import _chunk, _region
    out = []
    c0x, c0z, n = tx * size >> 4, tz * size >> 4, max(1, size >> 4)
    for cx in range(c0x, c0x + n):
        for cz in range(c0z, c0z + n):
            p = _region(root, world, dim, cx << 4, cz << 4)
            raw = _chunk(p, cx, cz) if p else None
            if raw is None or b"mob_spawner" not in raw:
                continue
            d = nbt.loads(raw)
            for e in d.get("block_entities") or (d.get("Level") or {}).get("TileEntities") or []:
                if not isinstance(e, dict) or not str(e.get("id", "")).lower().endswith("mob_spawner"):
                    continue
                x, y, z = e.get("x"), e.get("y"), e.get("z")
                sd = e.get("SpawnData") if isinstance(e.get("SpawnData"), dict) else {}
                ent = sd.get("entity") if isinstance(sd.get("entity"), dict) else sd
                mob = str((ent or {}).get("id", "")).replace("minecraft:", "")
                if all(isinstance(v, int) for v in (x, y, z)) and mob:
                    out.append({"x": x, "y": y, "z": z, "mob": mob})
    return out


def heads_in_tile(root: str, world: str, dim: str, tx: int, tz: int, size: int = 128) -> list[dict]:
    """Every player head with a skin in the tile ``[tx*size, ...)``: ``{x, y, z, wall, facing, rotation, skin}``."""
    from . import blockmesh as bm
    from .containers import _chunk, _region
    out = []
    c0x, c0z, n = tx * size >> 4, tz * size >> 4, max(1, size >> 4)
    for cx in range(c0x, c0x + n):
        for cz in range(c0z, c0z + n):
            p = _region(root, world, dim, cx << 4, cz << 4)
            raw = _chunk(p, cx, cz) if p else None
            if raw is None or b"player_head" not in raw and b"player_wall_head" not in raw:
                continue
            d = nbt.loads(raw)
            bes = [e for e in d.get("block_entities") or (d.get("Level") or {}).get("TileEntities") or []
                   if isinstance(e, dict) and str(e.get("id", "")).lower().endswith("skull")]
            if not bes:
                continue
            ch = bm.Chunk(raw)
            for e in bes:
                x, y, z = e.get("x"), e.get("y"), e.get("z")
                if not all(isinstance(v, int) for v in (x, y, z)):
                    continue
                state = ch.state(x & 15, y, z & 15) or ""
                name = state.split("[")[0].replace("minecraft:", "")
                if name not in ("player_head", "player_wall_head"):
                    continue
                h = skin_hash(e)
                if not h:
                    continue
                props = dict(kv.split("=", 1) for kv in state[state.index("[") + 1:-1].split(",")) if "[" in state else {}
                out.append({"x": x, "y": y, "z": z, "wall": name == "player_wall_head", "skin": h,
                            "facing": props.get("facing", "north"), "rotation": int(props.get("rotation", "0") or 0)})
    return out
