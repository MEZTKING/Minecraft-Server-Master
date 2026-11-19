"""Server-level files: ``server.properties``, ``level.dat``, mods, world size.

None of these produce events; they fill in the "overview" part of the report.
"""

from __future__ import annotations

import json
import os
import zipfile
from typing import Any

from . import nbt

# Dimension folder layout inside a world folder:
#   Vanilla/Fabric/Forge : <world>/region, <world>/DIM-1/region, <world>/DIM1/region
#   26.1+               : <world>/dimensions/minecraft/{overworld,the_nether,the_end}/region
#   custom dims         : <world>/dimensions/<namespace>/<name>/region
#   Paper/Spigot        : <world>/region, <world>_nether/DIM-1/region, <world>_the_end/DIM1/region
_DIMENSIONS = {
    "overworld": ["{w}/dimensions/minecraft/overworld/region", "{w}/region"],
    "the_nether": ["{w}/dimensions/minecraft/the_nether/region", "{w}/DIM-1/region", "{w}_nether/DIM-1/region"],
    "the_end": ["{w}/dimensions/minecraft/the_end/region", "{w}/DIM1/region", "{w}_the_end/DIM1/region"],
}


def read_properties(path: str, mask_secrets: bool = True) -> dict[str, str]:
    """Parse a Java ``.properties`` file (good enough for server.properties).

    Secrets (the RCON password) are replaced by ``********`` unless
    *mask_secrets* is False -- only the live map asks for them, to log in.
    """
    props: dict[str, str] = {}
    if not os.path.isfile(path):
        return props
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line[0] in "#!":
                continue
            sep = min((i for i in (line.find("="), line.find(":")) if i >= 0), default=-1)
            if sep < 0:
                props[line] = ""
                continue
            key, value = line[:sep].strip(), line[sep + 1:].strip()
            # Undo Java escaping of ':' '=' and unicode escapes.
            value = value.replace("\\:", ":").replace("\\=", "=")
            if "\\u" in value:
                try:
                    value = value.encode("latin-1").decode("unicode_escape")
                except UnicodeError:
                    pass
            props[key] = value
    # Never keep secrets around (they would end up in the HTML report).
    for secret in ("rcon.password", "management-server-secret"):
        if mask_secrets and secret in props and props[secret]:
            props[secret] = "********"
    return props


def world_dir(root: str, props: dict[str, str]) -> str:
    """Path of the main world folder (``level-name`` in server.properties)."""
    name = props.get("level-name") or "world"
    candidate = os.path.join(root, name)
    if os.path.isdir(candidate):
        return candidate
    # Fall back to any folder containing level.dat.
    for entry in sorted(os.listdir(root)):
        if os.path.isfile(os.path.join(root, entry, "level.dat")):
            return os.path.join(root, entry)
    return candidate


def _find_key(obj: Any, key: str, depth: int = 4) -> Any:
    """First value stored under *key* anywhere in nested dicts (shallow search)."""
    if depth < 0 or not isinstance(obj, dict):
        return None
    if key in obj:
        return obj[key]
    for v in obj.values():
        found = _find_key(v, key, depth - 1)
        if found is not None:
            return found
    return None


def _load_data_file(world: str, name: str) -> dict[str, Any]:
    """Read ``<world>/data/minecraft/<name>.dat`` (26.1+ moved several level.dat parts there)."""
    path = os.path.join(world, "data", "minecraft", name + ".dat")
    if not os.path.isfile(path):
        return {}
    try:
        return nbt.load(path)
    except (nbt.NBTError, OSError, EOFError):
        return {}


def read_level_dat(world: str) -> dict[str, Any]:
    """Summarise ``level.dat``: name, seed, version, game time, spawn, rules.

    Handles both layouts:

    * up to 1.21: everything in ``level.dat`` (``SpawnX/Y/Z``, ``Difficulty`` int,
      ``GameRules``, ``WorldGenSettings.seed``, ``raining``...);
    * 26.1+: ``spawn: {pos: [x,y,z], dimension, yaw, pitch}``,
      ``difficulty_settings: {difficulty: "hard", locked}``, and game rules / world-gen
      settings / weather moved to ``data/minecraft/{game_rules,world_gen_settings,weather}.dat``.
    """
    path = os.path.join(world, "level.dat")
    if not os.path.isfile(path):
        return {}
    try:
        data = nbt.load(path).get("Data", {})
    except (nbt.NBTError, OSError, EOFError) as exc:
        return {"error": f"could not read level.dat: {exc}"}

    gen = _load_data_file(world, "world_gen_settings")
    rules = _load_data_file(world, "game_rules")
    weather = _load_data_file(world, "weather")
    seed = data.get("WorldGenSettings", {}).get("seed", data.get("RandomSeed"))
    if seed is None:
        seed = _find_key(gen, "seed")
    version = data.get("Version", {})
    game_time = data.get("Time")                      # total ticks the world has run
    day_time = data.get("DayTime")  # 26.1 moved it; new location not verified, so left unknown there
    spawn = [data.get("SpawnX"), data.get("SpawnY"), data.get("SpawnZ")]
    if spawn[0] is None and isinstance(data.get("spawn"), dict):
        pos = data["spawn"].get("pos")
        if isinstance(pos, list) and len(pos) == 3:
            spawn = pos
    difficulty = {0: "peaceful", 1: "easy", 2: "normal", 3: "hard"}.get(data.get("Difficulty"))
    if difficulty is None and isinstance(data.get("difficulty_settings"), dict):
        difficulty = data["difficulty_settings"].get("difficulty")
    game_rules = data.get("GameRules")
    if not game_rules and rules:
        inner = rules.get("data", rules)
        game_rules = {k: v for k, v in inner.items() if not isinstance(v, (dict, list))} if isinstance(inner, dict) else {}
    raining = data.get("raining")
    if raining is None:
        raining = _find_key(weather, "raining")
    thundering = data.get("thundering")
    if thundering is None:
        thundering = _find_key(weather, "thundering")
    out: dict[str, Any] = {
        "level_name": data.get("LevelName"),
        "seed": seed,
        "version_name": version.get("Name"),
        "data_version": data.get("DataVersion") or version.get("Id"),
        "game_time_ticks": game_time,
        "game_time_hours": round(game_time / 20 / 3600, 2) if isinstance(game_time, int) else None,
        "in_game_days": (day_time // 24000) if isinstance(day_time, int) else None,
        "spawn": spawn,
        "hardcore": bool(data.get("hardcore", 0)),
        "difficulty": difficulty,
        "game_type": {0: "survival", 1: "creative", 2: "adventure", 3: "spectator"}.get(data.get("GameType")),
        "raining": bool(raining or 0),
        "thundering": bool(thundering or 0),
        "last_played_ms": data.get("LastPlayed"),
        "game_rules": game_rules or {},
        "datapacks_enabled": data.get("DataPacks", {}).get("Enabled", []),
        "brand": data.get("ServerBrands", []),
    }
    dragon = data.get("DragonFight") or data.get("DimensionData", {}).get("1", {}).get("DragonFight")
    if isinstance(dragon, dict):
        out["dragon_killed"] = bool(dragon.get("DragonKilled", 0))
    return out


def dir_size(path: str) -> tuple[int, int]:
    """Return ``(total_bytes, file_count)`` for everything below *path*."""
    total = count = 0
    for current, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(current, f))
                count += 1
            except OSError:
                pass
    return total, count


def region_summary(root: str, world: str) -> dict[str, dict[str, int]]:
    """Size and region-file count per dimension (a cheap 'how explored' metric).

    Each ``r.X.Z.mca`` region file covers 512x512 blocks, so the count is a
    rough measure of explored area.
    """
    w = os.path.relpath(world, root)
    out: dict[str, dict[str, int]] = {}
    for dim, patterns in _DIMENSIONS.items():
        for pat in patterns:
            folder = os.path.join(root, pat.format(w=w))
            if os.path.isdir(folder):
                files = [f for f in os.listdir(folder) if f.endswith(".mca")]
                size = sum(os.path.getsize(os.path.join(folder, f)) for f in files)
                out[dim] = {"region_files": len(files), "bytes": size,
                            "approx_area_km2": round(len(files) * 512 * 512 / 1e6, 2)}
                break
    custom = os.path.join(world, "dimensions")
    if os.path.isdir(custom):
        for current, dirs, _files in os.walk(custom):
            if os.path.basename(current) == "region":
                rel = os.path.relpath(os.path.dirname(current), custom).replace(os.sep, ":", 1)
                if rel in ("minecraft:overworld", "minecraft:the_nether", "minecraft:the_end"):
                    dirs[:] = []
                    continue
                files = [f for f in os.listdir(current) if f.endswith(".mca")]
                out[rel] = {"region_files": len(files),
                            "bytes": sum(os.path.getsize(os.path.join(current, f)) for f in files),
                            "approx_area_km2": round(len(files) * 512 * 512 / 1e6, 2)}
                dirs[:] = []
    return out


def read_json_list(path: str) -> list[dict[str, Any]]:
    """Read ops.json / whitelist.json / banned-*.json (missing file -> [])."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def list_mods(root: str) -> list[dict[str, str]]:
    """List mods (``mods/*.jar``) and plugins (``plugins/*.jar``) with versions.

    Reads Fabric (``fabric.mod.json``), Quilt (``quilt.mod.json``), Forge/NeoForge
    (``META-INF/mods.toml`` / ``neoforge.mods.toml``) and Bukkit (``plugin.yml``)
    metadata where present; otherwise falls back to the file name.
    """
    out = []
    for kind in ("mods", "plugins"):
        folder = os.path.join(root, kind)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".jar"):
                continue
            info = {"kind": kind[:-1], "file": name, "id": "", "name": name[:-4], "version": ""}
            try:
                with zipfile.ZipFile(os.path.join(folder, name)) as zf:
                    names = set(zf.namelist())
                    if "fabric.mod.json" in names:
                        meta = json.loads(zf.read("fabric.mod.json").decode("utf-8", "replace"), strict=False)
                        info.update(id=meta.get("id", ""), name=meta.get("name") or meta.get("id", ""),
                                    version=str(meta.get("version", "")), loader="fabric")
                    elif "quilt.mod.json" in names:
                        meta = json.loads(zf.read("quilt.mod.json").decode("utf-8", "replace")).get("quilt_loader", {})
                        info.update(id=meta.get("id", ""), name=meta.get("metadata", {}).get("name", meta.get("id", "")),
                                    version=str(meta.get("version", "")), loader="quilt")
                    elif "plugin.yml" in names or "paper-plugin.yml" in names:
                        text = zf.read("paper-plugin.yml" if "paper-plugin.yml" in names else "plugin.yml").decode("utf-8", "replace")
                        for line in text.splitlines():
                            if line.startswith("name:"):
                                info["name"] = info["id"] = line.split(":", 1)[1].strip().strip("'\"")
                            elif line.startswith("version:"):
                                info["version"] = line.split(":", 1)[1].strip().strip("'\"")
                        info["loader"] = "bukkit"
                    else:
                        for toml in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                            if toml in names:
                                text = zf.read(toml).decode("utf-8", "replace")
                                for line in text.splitlines():
                                    line = line.strip()
                                    for key in ("modId", "displayName", "version"):
                                        if line.startswith(key) and "=" in line and not info.get("_" + key):
                                            val = line.split("=", 1)[1].strip().strip("'\"")
                                            info[{"modId": "id", "displayName": "name", "version": "version"}[key]] = val
                                            info["_" + key] = "1"
                                info["loader"] = "neoforge" if "neoforge" in toml else "forge"
                                break
            except (zipfile.BadZipFile, OSError, ValueError, KeyError):
                pass
            out.append({k: v for k, v in info.items() if not k.startswith("_")})
    return out
