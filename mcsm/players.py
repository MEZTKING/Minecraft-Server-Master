"""Per-player files.

* ``usercache.json``                 -- UUID <-> name mapping (most recent names)
* ``<world>/stats/<uuid>.json``      -- lifetime statistics
* ``<world>/advancements/<uuid>.json`` -- advancements with completion timestamps
* ``<world>/playerdata/<uuid>.dat``  -- NBT: position, health, XP, inventory...

Minecraft 26.1+ keeps the same files in ``<world>/players/stats``,
``players/advancements`` and ``players/data``; :func:`player_dir` finds either.
* ``ops.json``, ``whitelist.json``, ``banned-players.json``

Statistic formats
-----------------
1.13+ ::

    {"stats": {"minecraft:custom": {"minecraft:play_time": 72000, ...},
               "minecraft:mined":  {"minecraft:stone": 512, ...}, ...},
     "DataVersion": 3700}

Pre-1.13 (flat) ::

    {"stat.playOneMinute": 72000, "stat.mineBlock.minecraft.stone": 512, ...}

Both are normalised to ``{category: {key: value}}`` with the ``minecraft:``
prefix stripped (e.g. ``{"custom": {"play_time": 72000}}``).

Units: time stats are in **ticks** (20 per second); distance stats
(``*_one_cm``) are in **centimetres**; damage stats are in tenths of a heart
point x10 (i.e. divide by 10 for half-hearts).
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from typing import Any

from . import nbt
from .timeutil import parse_offset_timestamp

# Old (pre-1.13) stat names -> new names, for the most useful ones.
_LEGACY_CUSTOM = {
    "stat.playOneMinute": "play_time", "stat.deaths": "deaths", "stat.mobKills": "mob_kills",
    "stat.playerKills": "player_kills", "stat.jump": "jump", "stat.walkOneCm": "walk_one_cm",
    "stat.sprintOneCm": "sprint_one_cm", "stat.flyOneCm": "fly_one_cm", "stat.swimOneCm": "swim_one_cm",
    "stat.damageDealt": "damage_dealt", "stat.damageTaken": "damage_taken", "stat.leaveGame": "leave_game",
    "stat.timeSinceDeath": "time_since_death", "stat.sneakTime": "sneak_time",
    "stat.fishCaught": "fish_caught", "stat.animalsBred": "animals_bred", "stat.drop": "drop",
}
_LEGACY_CATEGORY = {
    "stat.mineBlock": "mined", "stat.craftItem": "crafted", "stat.useItem": "used",
    "stat.breakItem": "broken", "stat.pickup": "picked_up", "stat.drop": "dropped",
    "stat.killEntity": "killed", "stat.entityKilledBy": "killed_by",
}


# Where per-player files live, newest layout first. Minecraft 26.1 moved them to
# <world>/players/{stats,advancements,data}; before that <world>/{stats,advancements,playerdata}.
_PLAYER_DIRS = {
    "stats": ["players/stats", "stats"],
    "advancements": ["players/advancements", "advancements"],
    "playerdata": ["players/data", "playerdata"],
}
_UUID_FILE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\.(json|dat)$")


def player_dir(world: str, kind: str) -> str | None:
    """Folder holding ``kind`` (stats / advancements / playerdata) files for this world.

    Known locations are tried first; otherwise the world folder is searched (3 levels,
    skipping dimension/region data) for a folder of uuid-named files with the right name,
    so a future layout change keeps working.
    """
    for rel in _PLAYER_DIRS[kind]:
        d = os.path.join(world, rel)
        if os.path.isdir(d):
            return d
    names = {"stats": {"stats"}, "advancements": {"advancements"}, "playerdata": {"playerdata", "data"}}[kind]
    ext = ".dat" if kind == "playerdata" else ".json"
    skip = {"region", "entities", "poi", "dimensions", "DIM1", "DIM-1", "datapacks", "DistantHorizons"}
    base_depth = world.rstrip(os.sep).count(os.sep)
    for current, dirs, files in os.walk(world):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        if current.count(os.sep) - base_depth >= 3:
            dirs[:] = []
        if os.path.basename(current) in names and any(_UUID_FILE.match(f) and f.endswith(ext) for f in files):
            return current
    return None


def _strip(key: str) -> str:
    return key.split(":", 1)[1] if key.startswith("minecraft:") else key


def read_usercache(root: str) -> dict[str, str]:
    """Return ``{uuid: name}`` from usercache.json (and whitelist/ops as backup)."""
    mapping: dict[str, str] = {}
    for fname in ("whitelist.json", "ops.json", "usercache.json"):  # usercache last = wins
        try:
            with open(os.path.join(root, fname), encoding="utf-8") as fh:
                for entry in json.load(fh):
                    if isinstance(entry, dict) and entry.get("uuid") and entry.get("name"):
                        mapping[entry["uuid"].lower()] = entry["name"]
        except (OSError, ValueError, TypeError):
            continue
    return mapping


def normalise_stats(raw: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Convert either stats format into ``{category: {key: int}}``."""
    out: dict[str, dict[str, int]] = {}
    if isinstance(raw.get("stats"), dict):
        for cat, values in raw["stats"].items():
            if isinstance(values, dict):
                out[_strip(cat)] = {_strip(k): int(v) for k, v in values.items() if isinstance(v, (int, float))}
        return out
    for key, value in raw.items():
        if not isinstance(value, (int, float)):
            continue
        if key in _LEGACY_CUSTOM:
            out.setdefault("custom", {})[_LEGACY_CUSTOM[key]] = int(value)
            continue
        for prefix, cat in _LEGACY_CATEGORY.items():
            if key.startswith(prefix + "."):
                item = key[len(prefix) + 1:].replace("minecraft.", "")
                out.setdefault(cat, {})[item] = int(value)
                break
        else:
            if key.startswith("stat."):
                out.setdefault("custom", {})[key[5:]] = int(value)
    return out


def read_stats(world: str) -> dict[str, dict[str, dict[str, int]]]:
    """Return ``{uuid: normalised_stats}`` for every file in ``<world>/stats``."""
    folder = player_dir(world, "stats")
    out = {}
    if not folder:
        return out
    for name in os.listdir(folder):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                out[name[:-5].lower()] = normalise_stats(json.load(fh))
        except (OSError, ValueError):
            continue
    return out


def read_advancements(world: str) -> tuple[dict[str, dict[str, dict[str, Any]]], list[tuple[date, timedelta]]]:
    """Read advancement files.

    Returns ``(by_uuid, offset_samples)`` where ``by_uuid`` is
    ``{uuid: {advancement_id: {"done": bool, "completed": iso | None, "criteria": {...}}}}``
    and ``offset_samples`` are ``(local_date, utc_offset)`` pairs used to learn
    the server's time zone (see :class:`mcsm.timeutil.LogClock`).

    Recipe unlocks (``minecraft:recipes/...``) are skipped -- there are
    hundreds of them and they are not interesting.
    """
    folder = player_dir(world, "advancements")
    out: dict[str, dict[str, dict[str, Any]]] = {}
    samples: list[tuple[date, timedelta]] = []
    if not folder:
        return out, samples
    for name in os.listdir(folder):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            continue
        player: dict[str, dict[str, Any]] = {}
        for adv_id, info in raw.items():
            if not isinstance(info, dict):
                continue
            criteria = {}
            for crit, ts in (info.get("criteria") or {}).items():
                dt = parse_offset_timestamp(ts) if isinstance(ts, str) else None
                if dt is not None:
                    criteria[crit] = dt
                    samples.append((dt.replace(tzinfo=None).date(), dt.utcoffset()))
            if "recipes/" in adv_id:
                continue
            completed = max(criteria.values()) if criteria and info.get("done") else None
            player[_strip(adv_id)] = {"done": bool(info.get("done")), "completed": completed,
                                      "criteria": criteria}
        out[name[:-5].lower()] = player
    return out, samples


def summarise_playerdata(data: dict[str, Any]) -> dict[str, Any]:
    """Pick the interesting fields out of a playerdata NBT compound."""
    pos = data.get("Pos") or [None, None, None]
    dim = data.get("Dimension")
    if isinstance(dim, int):  # pre-1.16 numeric dimensions
        dim = {0: "overworld", -1: "the_nether", 1: "the_end"}.get(dim, str(dim))
    inv = data.get("Inventory") or []
    ender = data.get("EnderItems") or []
    last_death = data.get("LastDeathLocation") or {}
    abilities = data.get("abilities") or {}

    def item_id(stack: dict) -> str:
        return _strip(str(stack.get("id", "?")))

    def item_count(stack: dict) -> int:
        return int(stack.get("count", stack.get("Count", 1)) or 1)

    def stacks(lst):
        agg: dict[str, int] = {}
        for st in lst:
            if isinstance(st, dict):
                agg[item_id(st)] = agg.get(item_id(st), 0) + item_count(st)
        return dict(sorted(agg.items(), key=lambda kv: -kv[1]))

    return {
        "position": [round(p, 1) if isinstance(p, float) else p for p in pos[:3]],
        "dimension": _strip(str(dim)) if dim else None,
        "health": data.get("Health"),
        "food": data.get("foodLevel"),
        "xp_level": data.get("XpLevel"),
        "xp_total": data.get("XpTotal"),
        "score": data.get("Score"),
        "game_mode": {0: "survival", 1: "creative", 2: "adventure", 3: "spectator"}.get(data.get("playerGameType")),
        "flying": bool(abilities.get("flying", 0)),
        "spawn": [data.get("SpawnX"), data.get("SpawnY"), data.get("SpawnZ")] if "SpawnX" in data else None,
        "last_death": {"dimension": _strip(str(last_death.get("dimension", ""))),
                       "pos": last_death.get("pos")} if last_death else None,
        "inventory": stacks(inv),
        "ender_chest": stacks(ender),
        "inventory_slots_used": len(inv),
    }


def read_playerdata(world: str) -> dict[str, dict[str, Any]]:
    """Return ``{uuid: summary}`` for every ``<world>/playerdata/*.dat``."""
    folder = player_dir(world, "playerdata")
    out = {}
    if not folder:
        return out
    for name in os.listdir(folder):
        if not name.endswith(".dat") or name.endswith("_old.dat"):
            continue
        try:
            out[name[:-4].lower()] = summarise_playerdata(nbt.load(os.path.join(folder, name)))
        except (nbt.NBTError, OSError, EOFError, ValueError) as exc:
            out[name[:-4].lower()] = {"error": str(exc)}
    return out


def play_time_ticks(stats: dict[str, dict[str, int]]) -> int:
    """Total play time in ticks (``play_time`` since 1.17, ``play_one_minute`` before)."""
    custom = stats.get("custom", {})
    return int(custom.get("play_time", custom.get("play_one_minute", 0)))
