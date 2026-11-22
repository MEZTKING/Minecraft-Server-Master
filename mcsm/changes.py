"""What changed on the ground between backups: builds, map art, digging (backlog 0t step 3).

For each pair of consecutive backups, the surfaces are compared (the same top-down heights and colours as the
map, :mod:`mcsm.terrain`):

1. regions whose file changed are checked quickly at the map's coarse step (tiles usually cached already);
2. regions that really differ there are read again every :data:`DETAIL_STEP` blocks and compared cell by cell
   (whole rows that are equal are skipped);
3. a cell has *changed* when its height moved by 2 or more, or its colour changed a lot. Natural changes are
   ignored: cells that are leaves, logs, water, ice, snow, lava or water plants on either side (trees grow,
   water freezes, snow falls), cells without data (new or pruned chunks), and the End's island around 0, 0
   (the dragon fight rebuilds it);
4. changed cells close together (2 cells) are joined into areas; an area counts when it has at least
   :data:`MIN_CELLS` changed cells, is at least 3 cells across both ways and :data:`MIN_DENSITY` of its box
   changed (a tree here and there doesn't add up to a build);
5. each area gets a kind: ``map_art`` (big and flat, colours changed but not heights), ``build`` (mostly
   higher), ``dug`` (mostly lower: digging, clearing) or ``changed``.

Land that didn't exist yet in the older backup (explored since) has nothing to compare with: there, cells
whose top block is clearly made by players (planks, bricks, glass, wool, concrete, stairs...) count instead,
so a base built far out still shows (``new_land``; a village the game made can look the same).

Results are cached per pair in ``%LOCALAPPDATA%/mcsm/changes/``. Who did it is worked out by the caller (the
history viewer) from recorded positions; the backups alone can't say.
"""

from __future__ import annotations

import array
import hashlib
import json
import math
import os
import statistics

from . import terrain

DETAIL_STEP = 4
MIN_CELLS = 16                  # x 16 blocks² at step 4 = 256 blocks²
MIN_DENSITY = 0.35
MIN_SIDE = 3                    # cells across, both ways
JOIN = 2                        # changed cells this close (in cells) are one area
DH = 2                          # blocks of height difference that count
DCOLOUR2 = 40 ** 2              # squared colour distance that counts
END_IGNORE = 200                # blocks around 0, 0 in the End (dragon fight)
MAX_PER_PAIR = 60               # most areas kept per pair (largest first)
CACHE_VERSION = 1
DIMS = ("minecraft:overworld", "minecraft:the_end")     # the Nether's surface is its bedrock roof

_NOISE_NAMES = ("oak_leaves", "oak_log", "water", "ice", "packed_ice", "blue_ice", "snow", "snow_block",
                "powder_snow", "lava", "lily_pad", "kelp", "seagrass", "sugar_cane", "bamboo", "cactus",
                "vine", "short_grass", "tall_grass", "fern")
NOISE = {bytes(terrain.block_colour(n)) for n in _NOISE_NAMES}
_MADE = ["oak_planks", "oak_stairs", "oak_slab", "oak_fence", "stone_brick_wall", "white_carpet", "red_bed", "oak_door",
         "oak_trapdoor", "glass", "white_stained_glass", "glass_pane", "bricks", "stone_bricks", "quartz_block",
         "iron_block", "gold_block", "diamond_block", "emerald_block", "redstone_block", "lapis_block", "glowstone",
         "sea_lantern", "torch", "chest", "crafting_table", "furnace", "hay_block", "copper_block"]
_MADE += [f"{d}_{k}" for d in ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray",
                               "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black")
          for k in ("wool", "concrete")]
MADE = {bytes(terrain.block_colour(n)) for n in _MADE} - NOISE


def cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mcsm", "changes")


def _regions(root: str, world: str, dim: str) -> dict[tuple[int, int], str]:
    out = {}
    for p in terrain.region_files(root, world, dim):
        try:
            _r, rx, rz, _e = os.path.basename(p).split(".")
            out[(int(rx), int(rz))] = p
        except ValueError:
            continue
    return out


def changed_cells(a: terrain.Tile | None, b: terrain.Tile, end: bool = False) -> list[tuple]:
    """``[(cell x, cell z, height change, colour changed, new height, new land)]`` in world cell coordinates
    (``block // step``). *a* None: the region didn't exist yet."""
    if a is None:
        a = terrain.Tile(b.rx, b.rz, b.step, b.size, [terrain.Tile.NO_DATA] * (b.size * b.size), bytes(b.size * b.size * 3))
    if a.step != b.step or a.size != b.size:
        return []
    n, step = a.size, a.step
    if a.heights == b.heights and a.colours == b.colours:
        return []
    ha, hb = array.array("h", a.heights), array.array("h", b.heights)
    ca, cb = memoryview(a.colours), memoryview(b.colours)
    no = terrain.Tile.NO_DATA
    x0, z0 = a.rx * 512 // step, a.rz * 512 // step
    out = []
    for z in range(n):
        r0, r1 = z * n, (z + 1) * n
        if ha[r0:r1] == hb[r0:r1] and ca[r0 * 3:r1 * 3] == cb[r0 * 3:r1 * 3]:
            continue
        for x in range(n):
            i = r0 + x
            h0, h1 = ha[i], hb[i]
            if h1 == no:
                continue
            if h0 == no:                              # new land: only clearly man-made blocks count
                c1 = bytes(cb[i * 3:i * 3 + 3])
                if c1 in MADE and not (end and math.hypot((x0 + x) * step, (z0 + z) * step) < END_IGNORE):
                    out.append((x0 + x, z0 + z, 0, True, h1, True))
                continue
            c0, c1 = bytes(ca[i * 3:i * 3 + 3]), bytes(cb[i * 3:i * 3 + 3])
            if c0 in NOISE or c1 in NOISE:
                continue
            dh = h1 - h0
            dc = (c0[0] - c1[0]) ** 2 + (c0[1] - c1[1]) ** 2 + (c0[2] - c1[2]) ** 2
            if abs(dh) >= DH or dc >= DCOLOUR2:
                gx, gz = x0 + x, z0 + z
                if end and math.hypot(gx * step, gz * step) < END_IGNORE:
                    continue
                out.append((gx, gz, dh, dc >= DCOLOUR2, h1, False))
    return out


def areas(cells: list[tuple], step: int) -> list[dict]:
    """Join changed cells into areas and keep the ones that look made by someone (see the module docstring)."""
    if not cells:
        return []
    at = {(c[0], c[1]): c for c in cells}
    parent = {k: k for k in at}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    for (x, z) in at:
        for dx in range(-JOIN, JOIN + 1):
            for dz in range(-JOIN, JOIN + 1):
                o = (x + dx, z + dz)
                if o in at and o != (x, z):
                    ra, rb = find((x, z)), find(o)
                    if ra != rb:
                        parent[ra] = rb
    groups: dict = {}
    for k in at:
        groups.setdefault(find(k), []).append(at[k])
    out = []
    for g in groups.values():
        xs, zs = [c[0] for c in g], [c[1] for c in g]
        w, d = max(xs) - min(xs) + 1, max(zs) - min(zs) + 1
        if len(g) < MIN_CELLS or w < MIN_SIDE or d < MIN_SIDE or len(g) / (w * d) < MIN_DENSITY:
            continue
        up = sum(1 for c in g if c[2] >= DH)
        down = sum(1 for c in g if c[2] <= -DH)
        flat_colour = sum(1 for c in g if abs(c[2]) < DH and c[3] and not c[5])
        new = sum(1 for c in g if c[5])
        n = len(g)
        if new >= 0.5 * n:
            flat = statistics.pstdev([c[4] for c in g]) <= 1.0
            kind = "map_art" if flat and max(w, d) * step >= 48 else "build"
        elif flat_colour >= 0.7 * n and max(w, d) * step >= 48:
            kind = "map_art"
        elif up >= 0.5 * n:
            kind = "build"
        elif down >= 0.5 * n:
            kind = "dug"
        else:
            kind = "changed"
        out.append({"kind": kind, "area": [min(xs) * step, min(zs) * step, (max(xs) + 1) * step, (max(zs) + 1) * step],
                    "cells": n, "blocks2": n * step * step, "new_land": new >= 0.5 * n,
                    "volume": int(round(sum(abs(c[2]) for c in g) * step * step, -2)),   # an estimate
                    "y": int(statistics.median(c[4] for c in g))})
    out.sort(key=lambda a: -a["cells"])
    return out[:MAX_PER_PAIR]


def compare(old: tuple[str, str], new: tuple[str, str], coarse_step: int | None = None,
            tile_cache: str | None = None, stop=None) -> dict:
    """What changed from backup *old* to *new* (each ``(server root, world folder)``): ``{"areas": [...],
    "cells": {dim: [[x, z], ...]}}`` (every changed cell, also the scattered ones that make no area, in
    blocks; used to confirm builds the recordings show)."""
    out: list[dict] = []
    every: dict[str, list] = {}
    for dim in DIMS:
        a, b = _regions(*old, dim), _regions(*new, dim)
        if not a or not b:
            continue
        step0 = coarse_step or terrain.auto_step(len(b))
        cells: list[tuple] = []
        end = dim.endswith("the_end")
        for k in sorted(b):
            if stop is not None and stop.is_set():
                return {"areas": out, "cells": every}
            try:
                if k in a and terrain.tile_key(a[k], step0) == terrain.tile_key(b[k], step0):
                    continue
            except OSError:
                continue
            if step0 != DETAIL_STEP:          # a quick look at the map's own tiles first
                ta = terrain.read_region_cached(a[k], step0, tile_cache) if k in a else None
                tb = terrain.read_region_cached(b[k], step0, tile_cache)
                if not tb or (k in a and not ta) or not changed_cells(ta, tb, end):
                    continue
            ta = terrain.read_region_cached(a[k], DETAIL_STEP, tile_cache) if k in a else None
            tb = terrain.read_region_cached(b[k], DETAIL_STEP, tile_cache)
            if tb and (ta or k not in a):
                cells += changed_cells(ta, tb, end)
        for ar in areas(cells, DETAIL_STEP):
            ar["dim"] = dim
            out.append(ar)
        if cells:
            every[dim] = [[c[0] * DETAIL_STEP, c[1] * DETAIL_STEP] for c in cells]
    return {"areas": out, "cells": every}


def _fingerprint(root: str, world: str) -> str:
    """Changes when the backup's regions change (a live download that was replaced)."""
    h = hashlib.sha1()
    for dim in DIMS:
        for p in sorted(terrain.region_files(root, world, dim)):
            try:
                st = os.stat(p)
            except OSError:
                continue
            h.update(f"{os.path.basename(p)}|{st.st_size}|{st.st_mtime_ns};".encode())
    return h.hexdigest()[:16]


def compare_cached(old: tuple[str, str], new: tuple[str, str], cache: str | None = None, **kw) -> dict:
    cache = cache or cache_dir()
    key = hashlib.sha1(f"{CACHE_VERSION}|{_fingerprint(*old)}|{_fingerprint(*new)}|{DETAIL_STEP}".encode()).hexdigest()[:20]
    path = os.path.join(cache, key + ".json")
    try:
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh)
        if "areas" in got and "cells" in got:
            return got
    except (OSError, ValueError, KeyError):
        pass
    res = compare(old, new, **kw)
    stop = kw.get("stop")
    if stop is None or not stop.is_set():
        os.makedirs(cache, exist_ok=True)
        with open(path + ".part", "w", encoding="utf-8") as fh:
            json.dump(res, fh, separators=(",", ":"))
        os.replace(path + ".part", path)
    return res


def label(kind: str) -> str:
    return {"map_art": "Map art", "build": "Built", "dug": "Dug out / cleared", "changed": "Changed"}.get(kind, "Changed")


# --- who and when (from the players' recorded positions) ------------------------------------------------

NEAR = 16                       # blocks around an area that still count as "there"
MOMENTS_PER_PAIR = 5            # most change moments per backup interval (a terraformed world would flood the list)


def presence(area: list[float], dim: str, t0: int, t1: int, tracks: dict[str, list[list]]) -> dict:
    """Who was at *area* between *t0* and *t1* (ms): ``{"players": [names, most time first], "seconds": {...},
    "last": ms or None}`` from position tracks ``{name: [[ms, x, y, z, dim], ...]}`` (sorted by time)."""
    import bisect
    x0, z0, x1, z1 = area[0] - NEAR, area[1] - NEAR, area[2] + NEAR, area[3] + NEAR
    secs: dict[str, int] = {}
    last = None
    for name, pts in tracks.items():
        lo = bisect.bisect_left(pts, [t0])                 # [t0] sorts before every point at t0
        seen = set()
        for p in pts[lo:]:
            if p[0] > t1:
                break
            if p[4] == dim and x0 <= p[1] <= x1 and z0 <= p[3] <= z1:
                seen.add(p[0] // 1000)
                last = p[0] if last is None or p[0] > last else last
        if seen:
            secs[name] = len(seen)
    return {"players": sorted(secs, key=lambda n: -secs[n]), "seconds": secs, "last": last}


def _fmt_day(ms: int) -> str:
    from .timeutil import from_epoch_ms
    return from_epoch_ms(ms).astimezone().strftime("%d %b %Y").lstrip("0")


def change_moments(pairs: list[dict], tracks: dict[str, list[list]], work: list[dict]) -> list[dict]:
    """Moments for the changes between backups, and notes on the building moments they confirm.

    *pairs*: ``[{"from": ms, "to": ms, "areas": [...], "cells": {...}}]``; *work*: the building/map-art
    moments (changed in place: ``confirmed`` blocks², a line in ``why`` and a score x1.2 when the backups show
    the work). An area that a building moment already covers gets no moment of its own."""
    import hashlib
    for wm in work:
        if wm.get("mode") not in ("building", "map_art") or not wm.get("area"):
            continue
        for p in pairs:
            if not p["from"] <= wm["start"] <= p["to"]:
                continue
            x0, z0, x1, z1 = wm["area"]
            n = sum(1 for x, z in p["cells"].get(wm.get("dim") or "minecraft:overworld", [])
                    if x0 <= x <= x1 and z0 <= z <= z1)
            if n >= MIN_CELLS:
                wm["confirmed"] = n * DETAIL_STEP * DETAIL_STEP
                wm["why"] = list(wm.get("why") or []) + [f"the backups show about {wm['confirmed']:,} blocks² changed there"]
                wm["score"] = round(wm["score"] * 1.2, 1)
            break
    out = []
    for p in pairs:
        made = 0
        for ar in p["areas"]:
            if made >= MOMENTS_PER_PAIR:
                break
            ax0, az0, ax1, az1 = ar["area"]
            if any(wm.get("area") and (wm.get("dim") or "minecraft:overworld") == ar["dim"]
                   and p["from"] <= wm["start"] <= p["to"] and wm.get("mode") in ("building", "map_art")
                   and not (wm["area"][2] < ax0 or wm["area"][0] > ax1 or wm["area"][3] < az0 or wm["area"][1] > az1)
                   for wm in work):
                continue
            who = presence(ar["area"], ar["dim"], p["from"], p["to"], tracks)
            at = who["last"] or p["to"]
            cx, cz = round((ax0 + ax1) / 2), round((az0 + az1) / 2)
            what = label(ar["kind"]) + (" on new land" if ar.get("new_land") else "")
            names = who["players"][:3]
            score = 3.0 * math.log2(1 + ar["blocks2"] / 256) * (1.0 if names else 0.8)
            out.append({"id": hashlib.sha1(f"change|{p['to']}|{ax0}|{az0}".encode()).hexdigest()[:12],
                        "start": at, "end": at, "score": round(score, 1),
                        "title": f"{', '.join(names) if names else 'Unknown builder'}: {what.lower()} "
                                 f"at {cx}, {cz} (between the backups of {_fmt_day(p['from'])} and {_fmt_day(p['to'])})",
                        "kinds": {"change": 1}, "players": names, "events": [], "online": [], "recordings": [], "mc": None,
                        "why": [f"{what}: about {ar['blocks2']:,} blocks² of ground changed between two backups",
                                ("seen there: " + ", ".join(f"{n} ({round(who['seconds'][n] / 60)} min)" for n in names)
                                 if names else "nobody was recorded there: the backups alone can't say who")],
                        "mode": "changed", "change": ar["kind"], "area": ar["area"], "dim": ar["dim"], "y": ar["y"]})
            made += 1
    return out
