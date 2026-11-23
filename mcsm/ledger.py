"""The Ledger mod's log of block changes (``<world>/ledger.sqlite``), for blocks as they were at any moment.

Ledger records every block broken, placed or changed on the server (by players, explosions, fire, decay,
fluids...) with its time (UTC), position, world, and the block before and after. With a backup saved at time
*B*, the world at an earlier time *T* is the backup with every change between *T* and *B* undone: at each
position the earliest change after *T* says what was there at *T* (its "old" block). For *T* after *B* the
changes from *B* to *T* are applied instead (the latest one at each position wins).

The database is only read (``mode=ro&immutable=1``), never changed. Rows rolled back with ``/ledger rollback``
are left out (the rollback put the old block back).
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timezone

BLOCK_ACTIONS = ("block-break", "block-place", "block-change")
_PROPS = re.compile(r'(\w+):"?([^",}]*)"?')


def find(root: str | None, world: str | None) -> str | None:
    """The Ledger database of a server folder, if it has one."""
    if not root:
        return None
    for p in ([os.path.join(root, world, "ledger.sqlite")] if world else []) + [os.path.join(root, "ledger.sqlite")]:
        if os.path.isfile(p):
            return p
    return None


def state(identifier: str, props: str | None) -> str:
    """``minecraft:oak_stairs`` + ``{facing:"east",half:"top"}`` -> ``minecraft:oak_stairs[facing=east,half=top]``."""
    if not props:
        return identifier
    pairs = _PROPS.findall(props)
    return identifier + ("[" + ",".join(f"{k}={v}" for k, v in pairs) + "]" if pairs else "")


def _when(ms: int) -> str:
    """Ledger's time text for epoch milliseconds (UTC, sortable as text)."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _ms(text: str) -> int:
    t = datetime.strptime(text[:23].ljust(23, "0") if "." in text else text + ".000", "%Y-%m-%d %H:%M:%S.%f")
    return int(t.replace(tzinfo=timezone.utc).timestamp() * 1000)


class Ledger:
    """One Ledger database, opened read-only (one connection per thread)."""

    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        con = self._con()
        self.actions = {name: i for i, name in con.execute("select id, action_identifier from ActionIdentifiers")}
        self.worlds = {name: i for i, name in con.execute("select id, identifier from worlds")}
        self.objects = dict(con.execute("select id, identifier from ObjectIdentifiers"))
        self.block_ids = tuple(self.actions[a] for a in BLOCK_ACTIONS if a in self.actions)
        r = con.execute("select min(time), max(time) from actions").fetchone()
        self.first, self.last = (_ms(r[0]), _ms(r[1])) if r and r[0] else (None, None)

    def _con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            uri = "file:" + self.path.replace("\\", "/").replace("?", "%3f").replace("#", "%23") + "?mode=ro&immutable=1"
            con = self._local.con = sqlite3.connect(uri, uri=True, check_same_thread=False)
        return con

    def changes(self, dim: str, x0: int, z0: int, x1: int, z1: int, t0: int, t1: int) -> list[tuple]:
        """Block changes in the box (x0..x1, z0..z1 inclusive) from *t0* to *t1* (ms, both included), in time order:
        ``(ms, x, y, z, new state, old state)``."""
        w = self.worlds.get(dim)
        if w is None or not self.block_ids:
            return []
        q = ("select time, x, y, z, object_id, block_state, old_object_id, old_block_state from actions "
             f"where world_id = ? and action_id in ({','.join('?' * len(self.block_ids))}) and rolled_back = 0 "
             "and x between ? and ? and z between ? and ? and time between ? and ? order by time, id")
        rows = self._con().execute(q, (w, *self.block_ids, x0, x1, z0, z1, _when(t0), _when(t1))).fetchall()
        obj = self.objects
        return [(_ms(t), x, y, z, state(obj.get(o, "minecraft:air"), s), state(obj.get(oo, "minecraft:air"), os_))
                for t, x, y, z, o, s, oo, os_ in rows]

    def patches(self, dim: str, chunks: set[tuple[int, int]], at: int, base: int) -> dict[tuple[int, int], dict]:
        """The blocks that differ from a backup saved at *base* (ms) at the moment *at*, per chunk:
        ``{(cx, cz): {(x & 15, y, z & 15): state}}``."""
        if not chunks:
            return {}
        xs, zs = [c[0] for c in chunks], [c[1] for c in chunks]
        lo, hi = (at + 1, base) if at < base else (base + 1, at)
        rows = self.changes(dim, min(xs) * 16, min(zs) * 16, max(xs) * 16 + 15, max(zs) * 16 + 15, lo, hi)
        found: dict[tuple[int, int, int], str] = {}
        if at < base:                                   # undo: the first change after *at* says what was there
            for _t, x, y, z, _new, old in rows:
                found.setdefault((x, y, z), old)
        else:                                           # replay: the last change up to *at* wins
            for _t, x, y, z, new, _old in rows:
                found[(x, y, z)] = new
        out: dict[tuple[int, int], dict] = {}
        for (x, y, z), s in found.items():
            c = (x >> 4, z >> 4)
            if c in chunks:
                out.setdefault(c, {})[(x & 15, y, z & 15)] = s
        return out


_OPEN: dict[str, Ledger | None] = {}


def open_ledger(path: str | None) -> Ledger | None:
    """A Ledger database, opened once (None when there is none or it can't be read)."""
    if not path:
        return None
    if path not in _OPEN:
        try:
            _OPEN[path] = Ledger(path)
        except (sqlite3.Error, OSError, ValueError):
            _OPEN[path] = None
    return _OPEN[path]


# ---------------------------------------------------------------- who built what
BUILD_LIMITS = {
    "gap_ms": 10 * 60_000,         # placements further apart than this end a build
    "reach": 32,                   # a placement belongs to the build if this close to its area so far ...
    "max_size": 192,               # ... and the area stays at most this wide
    "min_placed": 200,             # blocks placed for it to count as a build
    "min_minutes": 10,             # minutes with placements
    "mapart_size": 64,             # map art: at least this wide ...
    "mapart_flat": 0.7,            # ... with this share of the blocks within 3 heights of each other
}


# replanting a farm, saplings and buckets of water aren't building
FARMED = {"wheat", "carrots", "potatoes", "beetroots", "nether_wart", "sugar_cane", "cocoa", "kelp", "kelp_plant",
          "sweet_berry_bush", "pumpkin_stem", "melon_stem", "torchflower_crop", "pitcher_crop", "bamboo", "cactus",
          "water", "lava", "seagrass", "fire", "farmland", "cave_vines", "twisting_vines", "weeping_vines",
          "powder_snow"}


def _built(identifier: str) -> bool:
    n = identifier.split(":")[-1]
    return n not in FARMED and not n.endswith(("_sapling", "_propagule")) and n != "mangrove_propagule"


def find_builds(lg: Ledger, limits: dict | None = None, cache_file: str | None = None) -> list[dict]:
    """Every player's builds from the blocks they placed: ``{player, kind ("building"/"map_art"), start, end,
    minutes, active_minutes, dim, centre [x, y, z], area [x0, z0, x1, z1], y_range, y_usual, km, placed, broken,
    top [[block, n], ...], clip, source "ledger"}``, oldest first. Kept in *cache_file* (made again when the
    database changes)."""
    import json
    L = {**BUILD_LIMITS, **(limits or {})}
    stamp = None
    if cache_file:
        st = os.stat(lg.path)
        stamp = [st.st_size, int(st.st_mtime), sorted(L.items())]
        try:
            with open(cache_file, encoding="utf-8") as fh:
                d = json.load(fh)
            if d.get("stamp") == json.loads(json.dumps(stamp)):
                return d["builds"]
        except (OSError, ValueError, KeyError):
            pass
    place, brk = lg.actions.get("block-place"), lg.actions.get("block-break")
    names = dict(lg._con().execute("select id, player_name from players"))
    worlds = {i: n for n, i in lg.worlds.items()}
    rows = lg._con().execute(
        "select player_id, world_id, time, x, y, z, action_id, object_id from actions "
        "where player_id is not null and rolled_back = 0 and action_id in (?, ?) order by player_id, world_id, time",
        (place, brk))
    out: list[dict] = []
    run: list[tuple] = []
    box = [0, 0, 0, 0]
    key = None

    def close():
        placed = [r for r in run if r[3] == place and _built(lg.objects.get(r[5], ""))]
        if len(placed) < L["min_placed"]:
            return
        minutes = {r[0] // 60_000 for r in placed}
        if len(minutes) < L["min_minutes"]:
            return
        xs, ys, zs = sorted(r[1] for r in placed), sorted(r[2] for r in placed), sorted(r[4] for r in placed)
        cut = lambda v, q: v[min(len(v) - 1, int(q * len(v)))]
        area = [cut(xs, 0.02), cut(zs, 0.02), cut(xs, 0.98), cut(zs, 0.98)]
        y_mid = cut(ys, 0.5)
        flat = sum(1 for y in ys if abs(y - y_mid) <= 1) / len(ys)
        kind = "map_art" if min(area[2] - area[0], area[3] - area[1]) >= L["mapart_size"] and flat >= L["mapart_flat"] \
            else "building"
        top = {}
        for r in placed:
            top[r[5]] = top.get(r[5], 0) + 1
        start, end = run[0][0], run[-1][0] + 1000
        out.append({"player": names.get(key[0], "?"), "kind": kind, "start": start, "end": end,
                    "minutes": round((end - start) / 60_000), "active_minutes": len(minutes),
                    "dim": worlds.get(key[1], "minecraft:overworld"),
                    "centre": [round(sum(xs) / len(xs), 1), y_mid, round(sum(zs) / len(zs), 1)],
                    "area": area, "y_range": [ys[0], ys[-1]], "y_usual": [cut(ys, 0.1), cut(ys, 0.9)], "km": 0,
                    "placed": len(placed), "broken": len(run) - len(placed),
                    "top": [[lg.objects.get(o, "?").split(":")[-1], n] for o, n in
                            sorted(top.items(), key=lambda kv: -kv[1])[:3]],
                    "clip": [max(start, end - 3 * 60_000), end], "source": "ledger"})

    reach, size = L["reach"], L["max_size"]
    for pid, wid, t, x, y, z, aid, obj in rows:
        ms = _ms(t)
        if key != (pid, wid) or not run or ms - run[-1][0] > L["gap_ms"]:
            if run:
                close()
            run, key, box = [], (pid, wid), [x, z, x, z]
        inside = (box[0] - reach <= x <= box[2] + reach and box[1] - reach <= z <= box[3] + reach
                  and max(box[2], x) - min(box[0], x) <= size and max(box[3], z) - min(box[1], z) <= size)
        if not inside:
            if aid != place or not _built(lg.objects.get(obj, "")):   # mining or farming elsewhere: not part of it
                continue
            close()
            run, box = [], [x, z, x, z]
        run.append((ms, x, y, aid, z, obj))
        if aid == place and _built(lg.objects.get(obj, "")):
            box = [min(box[0], x), min(box[1], z), max(box[2], x), max(box[3], z)]
    if run:
        close()
    out.sort(key=lambda b: b["start"])
    if cache_file:
        try:
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            with open(cache_file + ".part", "w", encoding="utf-8") as fh:
                json.dump({"stamp": stamp, "builds": out}, fh, separators=(",", ":"))
            os.replace(cache_file + ".part", cache_file)
        except OSError:
            pass
    return out


# ---------------------------------------------------------------- griefing and accidents
EXPLOSIVE = {"creeper": "a creeper", "tnt": "TNT", "explosion": "an explosion", "fireball": "a fireball",
             "wither": "a wither", "end_crystal": "an end crystal", "bed": "a bed", "respawn_anchor": "a respawn anchor"}
INCIDENT_LIMITS = {
    "explosion": {"gap_ms": 20_000, "reach": 24, "min_blocks": 5},
    "fire": {"gap_ms": 180_000, "reach": 48, "min_blocks": 10},
    "fluid": {"gap_ms": 180_000, "reach": 48, "min_blocks": 10},
    "player": {"gap_ms": 600_000, "reach": 48, "min_blocks": 30},
}


def find_incidents(lg: Ledger, cache_file: str | None = None) -> list[dict]:
    """Players' builds damaged: blocks a player placed, later removed by an explosion, fire, a fluid or another
    player. Only placed blocks count (a creeper crater in the wild is nothing). ``{kind ("explosion" / "fire" /
    "fluid" / "player"), cause ("a creeper", "TNT", "fire", "Steve"...), near (the player a creeper chased or who
    lit the TNT, else None), start, end, dim, centre [x, y, z], area, blocks, owners {name: n}, top [[block, n]]}``,
    oldest first. Kept in *cache_file* (made again when the database changes)."""
    import json
    stamp = None
    if cache_file:
        st = os.stat(lg.path)
        stamp = [st.st_size, int(st.st_mtime), 2]
        try:
            with open(cache_file, encoding="utf-8") as fh:
                d = json.load(fh)
            if d.get("stamp") == stamp:
                return d["incidents"]
        except (OSError, ValueError, KeyError):
            pass
    place, brk = lg.actions.get("block-place"), lg.actions.get("block-break")
    con = lg._con()
    names = dict(con.execute("select id, player_name from players"))
    sources = dict(con.execute("select id, name from sources"))
    worlds = {i: n for n, i in lg.worlds.items()}
    player_src = next((i for i, n in sources.items() if n == "player"), None)
    owner: dict[tuple, tuple] = {}                     # (world, x, y, z) -> (placer, block) of placed blocks
    damage: list[tuple] = []                           # (kind, cause, near, ms, world, x, y, z, owner, block)
    rows = con.execute("select time, world_id, x, y, z, action_id, source, player_id, old_object_id, object_id "
                       "from actions where rolled_back = 0 and action_id in (?, ?) order by time, id", (place, brk))
    for t, w, x, y, z, aid, src, pid, old, obj in rows:
        k = (w, x, y, z)
        if aid == place:
            if src == player_src and pid is not None and _built(lg.objects.get(obj, "")):   # (not crops: harvest)
                owner[k] = (pid, obj)
            else:
                owner.pop(k, None)
            continue
        o = owner.pop(k, None)
        if o is None:
            continue
        s = sources.get(src, "")
        if s in EXPLOSIVE:
            damage.append(("explosion", EXPLOSIVE[s], names.get(pid), t, w, x, y, z, o[0], o[1]))
        elif s == "fire":
            damage.append(("fire", "fire", None, t, w, x, y, z, o[0], o[1]))
        elif s in ("fluid", "lava"):
            damage.append(("fluid", "lava or water", None, t, w, x, y, z, o[0], o[1]))
        elif s == "player" and pid is not None and pid != o[0]:
            damage.append(("player", names.get(pid, "?"), None, t, w, x, y, z, o[0], o[1]))
    # group into incidents: one cause, one place, one stretch of time
    out: list[dict] = []
    open_: dict[tuple, dict] = {}

    def finish(g):
        L = INCIDENT_LIMITS[g["kind"]]
        if g["blocks"] < L["min_blocks"]:
            return
        n = g.pop("n")
        g["centre"] = [round(g.pop("sx") / n, 1), round(g.pop("sy") / n, 1), round(g.pop("sz") / n, 1)]
        g["owners"] = {names.get(p, "?"): c for p, c in sorted(g["owners"].items(), key=lambda kv: -kv[1])}
        g["top"] = [[lg.objects.get(b, "?").split(":")[-1], c] for b, c in sorted(g["top"].items(), key=lambda kv: -kv[1])[:3]]
        out.append(g)

    for kind, cause, near, t, w, x, y, z, own, blk in damage:
        ms = _ms(t)
        if kind == "player" and names.get(own) == cause:
            continue
        key = (kind, cause, w)
        g = open_.get(key)
        L = INCIDENT_LIMITS[kind]
        if g is not None and (ms - g["end"] > L["gap_ms"] or abs(x - g["sx"] / g["n"]) > L["reach"]
                              or abs(z - g["sz"] / g["n"]) > L["reach"]):
            finish(open_.pop(key))
            g = None
        if g is None:
            g = open_[key] = {"kind": kind, "cause": cause, "near": near, "start": ms, "end": ms,
                              "dim": worlds.get(w, "minecraft:overworld"), "area": [x, z, x, z], "blocks": 0,
                              "owners": {}, "top": {}, "n": 0, "sx": 0.0, "sy": 0.0, "sz": 0.0}
        g["end"] = ms
        g["near"] = g["near"] or near
        g["area"] = [min(g["area"][0], x), min(g["area"][1], z), max(g["area"][2], x), max(g["area"][3], z)]
        g["blocks"] += 1
        g["owners"][own] = g["owners"].get(own, 0) + 1
        g["top"][blk] = g["top"].get(blk, 0) + 1
        g["n"] += 1
        g["sx"] += x
        g["sy"] += y
        g["sz"] += z
    for g in list(open_.values()):
        finish(g)
    out.sort(key=lambda g: g["start"])
    if cache_file:
        try:
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            with open(cache_file + ".part", "w", encoding="utf-8") as fh:
                json.dump({"stamp": stamp, "incidents": out}, fh, separators=(",", ":"))
            os.replace(cache_file + ".part", cache_file)
        except OSError:
            pass
    return out


# ---------------------------------------------------------------- where things happened (heatmap)
HEAT_KINDS = {                       # what each heatmap shows (Ledger action names)
    "all": ("block-place", "block-break", "block-change", "entity-kill", "item-insert", "item-remove"),
    "building": ("block-place",),
    "mining": ("block-break",),
    "fighting": ("entity-kill",),
    "storage": ("item-insert", "item-remove"),
}


def heat(lg: Ledger, dim: str, t0: int, t1: int, kind: str = "all", cell: int = 16, players_only: bool = True) -> list[list]:
    """How much happened where from *t0* to *t1* (ms): ``[[cell x, cell z, count, mean y], ...]`` for cells of
    *cell* blocks; by players only unless *players_only* is False (then fire, decay... too)."""
    w = lg.worlds.get(dim)
    ids = [lg.actions[a] for a in HEAT_KINDS.get(kind, HEAT_KINDS["all"]) if a in lg.actions]
    if w is None or not ids:
        return []
    shift = max(0, int(cell).bit_length() - 1)
    q = (f"select x >> {shift}, z >> {shift}, count(*), avg(y) from actions where world_id = ? and rolled_back = 0 "
         f"and action_id in ({','.join('?' * len(ids))}) and time between ? and ?"
         + (" and player_id is not null" if players_only else "") + " group by 1, 2")
    return [[cx, cz, n, round(y)] for cx, cz, n, y in lg._con().execute(q, (w, *ids, _when(t0), _when(t1)))]
