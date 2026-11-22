"""Quiet but busy: long stretches of building, map art or exploring, found from players' movement.

The logs only show events (deaths, chat...), so hours spent building a base, flattening ground for map art
or searching for something look empty. The recordings know where every player was about once a second;
this module cuts each player's path into minutes and finds:

* **building** (and **map art**): 45 minutes or more in one area, while really moving around in it
  (standing still = AFK, ignored). The area may grow as the work goes on (row by row for map art); short
  trips away (up to ``gap_min``) don't end it. A large area whose ground level hardly changes is map art.
* **exploring / searching / journey**: 30 minutes or more of going into ground the player had never been
  to before (64-block squares, over the whole history: running around the base all evening isn't
  exploring); zig-zagging over an area is searching, going far in one direction a journey.

Design by Fable (2026-11-22), fixed after its review (excursions, growing areas, memory, duplicates and
teleports). Thresholds are starting values (``LIMITS``).
"""

from __future__ import annotations

import math

LIMITS = {
    "minute_ms": 60_000,
    "build_reach": 32,             # a minute belongs to a build if it's this close to the area so far ...
    "build_max": 192,              # ... and the area stays at most this wide
    "build_min_min": 45,           # minutes
    "gap_min": 10,                 # time away (no data, elsewhere, other dimension) that doesn't end a stretch
    "active_move": 3.0,            # blocks moved in a minute to count as active (not AFK)
    "active_share": 0.5,           # at least this share of a building stretch's minutes active
    "travel_move": 20.0,           # blocks moved in a minute to count as travelling
    "explore_min_min": 30,
    "explore_gap_min": 5,
    "cell": 64,                    # new ground = new squares of this size
    "explore_min_cells": 20,
    "mapart_size": 96,             # a building area at least this wide ...
    "mapart_flat": 4,              # ... where the usual ground level varies at most this much = map art
    "teleport": 60.0,              # a jump farther than this between two positions isn't movement
}


def _minutes(points: list[list], minute: int, teleport: float, cell: int) -> list[dict]:
    """Per minute: mean position, dimension, y range, distance moved, bounding box, 64-block squares."""
    out: list[dict] = []
    cur = None
    prev = None
    last_second = None
    for p in points:
        t, x, y, z, dim = p[0], p[1], p[2], p[3], p[4]
        if last_second == (t // 1000, dim):           # the same second seen twice (two recordings)
            continue
        last_second = (t // 1000, dim)
        k = t // minute
        if cur is None or k != cur["k"] or dim != cur["dim"]:
            cur = {"k": k, "t": k * minute, "dim": dim, "n": 0, "sx": 0.0, "sz": 0.0, "sy": 0.0, "ymin": y,
                   "ymax": y, "move": 0.0, "x0": x, "x1": x, "z0": z, "z1": z, "cells": set()}
            out.append(cur)
        if prev is not None and prev[4] == dim and t - prev[0] < 2 * minute:
            d = math.dist((prev[1], prev[2], prev[3]), (x, y, z))
            if d <= teleport:
                cur["move"] += d
        cur["n"] += 1
        cur["sx"] += x
        cur["sz"] += z
        cur["sy"] += y
        cur["ymin"], cur["ymax"] = min(cur["ymin"], y), max(cur["ymax"], y)
        cur["x0"], cur["x1"] = min(cur["x0"], x), max(cur["x1"], x)
        cur["z0"], cur["z1"] = min(cur["z0"], z), max(cur["z1"], z)
        cur["cells"].add((math.floor(x / cell), math.floor(z / cell)))
        prev = p
    for c in out:
        c["x"], c["z"], c["y"] = c["sx"] / c["n"], c["sz"] / c["n"], c["sy"] / c["n"]
    return out


def find_stretches(tracks: dict[str, list[list]], limits: dict | None = None) -> list[dict]:
    """Building / map art / exploring stretches of every player in *tracks* (``{player: [[t_ms, x, y, z,
    dimension, ...], ...]}``): ``{player, kind, start, end, minutes, active_minutes, dim, centre [x, y, z],
    area [x0, z0, x1, z1], y_range, km, new_cells, clip [start, end]}``."""
    L = {**LIMITS, **(limits or {})}
    out = []
    for player, pts in tracks.items():
        if not pts:
            continue
        mins = _minutes(sorted(pts, key=lambda q: q[0]), L["minute_ms"], L["teleport"], L["cell"])
        out += _building(player, mins, L)
        out += _exploring(player, mins, L, set())
    out.sort(key=lambda s: s["start"])
    return out


def _stretch(player, kind, run, L, **extra) -> dict:
    n = sum(c["n"] for c in run)
    ys = sorted(c["y"] for c in run)
    start, end = run[0]["t"], run[-1]["t"] + L["minute_ms"]
    return {"player": player, "kind": kind, "start": start, "end": end, "minutes": len(run),
            "active_minutes": sum(1 for c in run if c["move"] >= L["active_move"]), "dim": run[0]["dim"],
            "centre": [round(sum(c["sx"] for c in run) / n, 1), round(ys[len(ys) // 2], 1),
                       round(sum(c["sz"] for c in run) / n, 1)],
            "area": [math.floor(min(c["x0"] for c in run)), math.floor(min(c["z0"] for c in run)),
                     math.ceil(max(c["x1"] for c in run)), math.ceil(max(c["z1"] for c in run))],
            "y_range": [round(min(c["ymin"] for c in run)), round(max(c["ymax"] for c in run))],
            # the usual ground level: the middle 80 % of the minutes' mean heights
            "y_usual": [round(ys[len(ys) // 10]), round(ys[(len(ys) * 9) // 10])],
            "km": round(sum(c["move"] for c in run) / 1000, 2), **extra}


def _building(player: str, mins: list[dict], L: dict) -> list[dict]:
    out, run = [], []
    box = [0.0, 0.0, 0.0, 0.0]                        # x0, z0, x1, z1 of the work so far
    reach, gap = L["build_reach"], L["gap_min"] * L["minute_ms"]

    def close():
        while run and run[0]["move"] < L["active_move"]:      # AFK before or after the work isn't part of it
            run.pop(0)
        while run and run[-1]["move"] < L["active_move"]:
            run.pop()
        if len(run) >= L["build_min_min"]:
            active = sum(1 for c in run if c["move"] >= L["active_move"])
            if active >= L["active_share"] * len(run):
                s = _stretch(player, "building", run, L)
                w, h = s["area"][2] - s["area"][0], s["area"][3] - s["area"][1]
                if min(w, h) >= L["mapart_size"] and s["y_usual"][1] - s["y_usual"][0] <= L["mapart_flat"]:
                    s["kind"] = "map_art"
                # film the end: the finished work (last 3 minutes)
                s["clip"] = [max(s["start"], s["end"] - 3 * L["minute_ms"]), s["end"]]
                out.append(s)

    for c in mins:
        if run:
            inside = (c["dim"] == run[0]["dim"] and box[0] - reach <= c["x"] <= box[2] + reach
                      and box[1] - reach <= c["z"] <= box[3] + reach
                      and max(box[2], c["x"]) - min(box[0], c["x"]) <= L["build_max"]
                      and max(box[3], c["z"]) - min(box[1], c["z"]) <= L["build_max"])
            if inside and c["t"] - run[-1]["t"] <= gap:
                run.append(c)
                box = [min(box[0], c["x"]), min(box[1], c["z"]), max(box[2], c["x"]), max(box[3], c["z"])]
                continue
            if not inside and c["t"] - run[-1]["t"] <= gap:
                continue                              # a short trip away: the build goes on when they're back
        close()
        run = [c]
        box = [c["x"], c["z"], c["x"], c["z"]]
    close()
    return out


def _exploring(player: str, mins: list[dict], L: dict, ever: set) -> list[dict]:
    """Runs of minutes that reach ground the player had never been to (*ever*: every square so far)."""
    out, run = [], []
    for c in mins:                                    # what each minute adds that's new for this player, ever
        c["new"] = c["cells"] - ever
        ever |= c["cells"]

    def close():
        if not run or (run[-1]["t"] - run[0]["t"]) < (L["explore_min_min"] - 1) * L["minute_ms"]:
            return
        cells = set().union(*(c["new"] for c in run))
        if len(cells) < L["explore_min_cells"]:
            return
        path = sum(c["move"] for c in run)
        net = math.hypot(run[-1]["x"] - run[0]["x"], run[-1]["z"] - run[0]["z"])
        straight = net / max(1.0, path)
        kind = "journey" if straight > 0.6 else "searching" if straight < 0.3 else "exploring"
        # film the busiest 3 minutes (most new ground)
        news = [len(c["new"]) for c in run]
        i = max(range(len(run)), key=lambda k: sum(news[k:k + 3]))
        out.append(_stretch(player, kind, run, L, new_cells=len(cells), straightness=round(straight, 2),
                            clip=[run[i]["t"], run[i]["t"] + 3 * L["minute_ms"]]))

    gap = L["explore_gap_min"] * L["minute_ms"]
    for c in mins:
        if not c["new"]:
            continue                                  # nothing new this minute: may still be inside a run's gap
        if run and c["dim"] == run[-1]["dim"] and c["t"] - run[-1]["t"] <= gap:
            run.append(c)
        else:
            close()
            run = [c]
    close()
    return out
