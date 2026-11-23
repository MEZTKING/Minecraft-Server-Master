"""History viewer: everything in the backups and recordings, as if it were one recording.

A 3D map (the live map's terrain and three.js page) with a time slider over the whole history.
Served on ``http://127.0.0.1:<port>`` (this computer only):

    /                      the viewer (mcsm/assets/web/history.html)
    /api/info              title, spawn, terrain progress, path-decoding progress
    /api/terrain/...       terrain tiles (same as the live map); ``?layer=N`` for an older backup
    /api/history           time range, players, events, sessions, replays, backups
    /api/paths             player paths decoded so far (grows while recordings are read)
    /api/changes           what changed on the ground between backups (:mod:`mcsm.changes`), as found so far
    /api/blocktile/<layer>/<dim>/<tx>/<tz>   real blocks of one 128 x 128 piece of a backup's world (the map
                           close up), with /blocktile/<layer>/<key>.bin and /blocktex/<name>.png

Position sources: player paths decoded from Flashback recordings (:mod:`mcsm.paths`), live-map
track recordings (``mcsm live --record``), and join positions from the logs.
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
import threading
import zipfile
from typing import Callable

from . import models as m, paths as paths_mod
from .live import TerrainServer, serve

# Events shown on the timeline (server_stop/ready and replay markers would only add noise).
HISTORY_TYPES = (m.EV_JOIN, m.EV_LEAVE, m.EV_CHAT, m.EV_DEATH, m.EV_ADVANCEMENT, m.EV_COMMAND,
                 m.EV_CRASH, m.EV_SERVER_START, m.EV_REPLAY_MARKER)
SERVE_MIN_MOVE = 1.5      # blocks; thinner paths for the browser
SERVE_MAX_GAP_MS = 30_000  # ...but keep a point at least every 30 s


def dimension_of(world: str | None) -> str:
    """Dimension id from the ``[world]`` part of a log-in line (``world_nether``, ``minecraft:the_end``...)."""
    w = (world or "").lower()
    if "nether" in w:
        return "minecraft:the_nether"
    if "end" in w:
        return "minecraft:the_end"
    return "minecraft:overworld"


def _ms(dt) -> int | None:
    return paths_mod.epoch_ms(dt)


def thin(points: list[list]) -> list[list]:
    """Drop points that barely moved (keeps dimension changes and one point per 30 s)."""
    out: list[list] = []
    for p in points:
        if out:
            q = out[-1]
            if (p[4] == q[4] and p[0] - q[0] < SERVE_MAX_GAP_MS
                    and math.dist(p[1:4], q[1:4]) < SERVE_MIN_MOVE):
                continue
        out.append(p)
    if points and out[-1] is not points[-1]:
        out.append(points[-1])
    return out


def prefer_own(points: list[list]) -> list[list]:
    """Drop points seen in someone else's recording (6th element = who recorded) within 2 s of
    the player's own recording or live-map track: their own is more exact."""
    import bisect
    own = [p[0] for p in points if len(p) < 6]
    if not own or len(own) == len(points):
        return points
    out = []
    for p in points:
        if len(p) >= 6:
            i = bisect.bisect_left(own, p[0] - 2000)
            if i < len(own) and own[i] <= p[0] + 2000:
                continue
        out.append(p)
    return out


def history_payload(data: m.ServerData, title: str = "") -> dict:
    """Everything except the paths, as JSON-friendly data (times in epoch ms)."""
    events = []
    for e in data.events:
        if e.type not in HISTORY_TYPES:
            continue
        row = {"t": _ms(e.time), "type": e.type, "player": e.player, "text": e.text[:200]}
        pos = (e.data or {}).get("position")
        if pos:
            row["pos"] = [round(v, 1) for v in pos]
            row["dim"] = dimension_of((e.data or {}).get("world"))
        events.append(row)
    replays = []
    for i, r in enumerate(data.replays):
        if r.start is None:
            continue
        end = r.end or r.start
        replays.append({"i": i, "start": _ms(r.start), "end": _ms(end), "player": r.recorded_player,
                        "path": r.path, "format": r.format, "estimated": bool(r.raw_meta.get("duration_note")),
                        "mc": r.mc_version or ""})
    sessions = [{"player": s.player, "start": _ms(s.start), "end": _ms(s.end)} for s in data.sessions if s.start]
    times = [x for x in [e["t"] for e in events] + [s["start"] for s in sessions] + [s["end"] for s in sessions]
             + [r["end"] for r in replays] if x]
    aliases = {}
    for p in data.players.values():
        for n in p.names_seen:
            if n != p.name:
                aliases[n] = p.name
    return {
        "title": title or data.world.get("level_name") or "Minecraft server",
        "range": [min(times), max(times)] if times else None,
        "players": sorted(p.name for p in data.players.values() if p.session_count or p.names_seen),
        "aliases": aliases,
        "uuids": {p.name: p.uuid for p in data.players.values() if p.uuid},   # for the players' real skins
        "events": events,
        "sessions": sessions,
        "replays": replays,
        "backups": [{"label": b.get("label"), "t": _ms(b.get("time")) if hasattr(b.get("time"), "timestamp") else None,
                     "version": b.get("version"), "mods": len(b.get("mod_list") or []),
                     "datapacks": b.get("datapacks") or []} for b in data.backups],
        "changes": [{"t": _ms(c.get("time")) if hasattr(c.get("time"), "timestamp") else None,
                     "after": _ms(c.get("after")) if hasattr(c.get("after"), "timestamp") else None,
                     "kind": c["kind"], "action": c["action"], "name": c["name"], "from": c["from"], "to": c["to"]}
                    for c in getattr(data, "changes", [])],
        "spawn": data.world.get("spawn"),
    }


APP_COPIES = ("(highlights)", "(text)", "(timelapse", "(mcsm)", "(shot)")


def version_segments(data: m.ServerData, end_ms: int | None = None) -> list[dict]:
    """The Minecraft version over time, from the backups (their version when saved) and the recordings (made in
    a known version): ``[{start, end, mc, sure}]``, oldest first. A change is placed at the first backup or recording
    in the new version (it happened somewhere before that); ``sure`` is False while only one source says so."""
    points = []
    for b in data.backups:
        if b.get("version") and hasattr(b.get("time"), "timestamp"):
            points.append((_ms(b["time"]), b["version"]))
    for r in data.replays:
        if r.mc_version and r.start is not None and not any(c in r.path for c in APP_COPIES):
            points.append((_ms(r.start), r.mc_version))
    points.sort()
    segs: list[dict] = []
    for t, v in points:
        if segs and segs[-1]["mc"] == v:
            segs[-1]["last"] = t
            segs[-1]["n"] += 1
            continue
        if segs:
            segs[-1]["end"] = t
        segs.append({"start": t, "end": None, "mc": v, "last": t, "n": 1})
    if segs:
        segs[-1]["end"] = max(end_ms or 0, segs[-1]["last"])
    # a single stray point (e.g. one recording from a test client) inside another version's stretch: drop it
    clean = []
    for s in segs:
        if s["n"] == 1 and clean and len(segs) > 2 and s is not segs[-1]:
            clean[-1]["end"] = s["end"]
            continue
        if clean and clean[-1]["mc"] == s["mc"]:
            clean[-1]["end"] = s["end"]
            continue
        clean.append(s)
    return [{"start": s["start"], "end": s["end"], "mc": s["mc"]} for s in clean]


class HistoryMap(TerrainServer):
    PAGE = "history.html"

    def __init__(self, data: m.ServerData, replay_file: Callable[[m.Replay], str] | None,
                 root: str | None = None, world: str | None = None, terrain_step: int | None = None,
                 cache_dir: str | None = None, track_files: list[str] | None = None, title: str = "",
                 spawn=None, decode_replays: bool = True, highlights_file: str | None = None,
                 flashback_dir: str | None = None, terrain_backups: list[dict] | None = None,
                 flashback_pick: Callable | None = None,
                 terrain_label: str = "", terrain_time: int | None = None):
        from . import highlights as hl
        self.data, self.replay_file = data, replay_file
        self._metas, self._offsets, self._loaded_off = {}, {}, {}
        self._exact_lock = threading.Lock()
        self._exact_jobs: set[str] = set()
        self._exact_slots = threading.Semaphore(3)
        self._exact_mem: dict[str, dict] = {}
        self._prep = {"running": False, "cancel": False, "done": 0, "total": 0, "phase": "", "errors": 0}
        self._skins, self._skin_lock = None, threading.Lock()
        if not highlights_file and data.library_world:      # a library world keeps its own highlights
            highlights_file = os.path.join(data.library_world, "highlights.json")
        self.highlights = hl.Store(highlights_file or hl.default_file(data.world.get("level_name") or title))
        self.flashback_dir = flashback_dir
        self.flashback_pick = flashback_pick      # per recording: the instance with its Minecraft version
        self._export_lock = threading.Lock()
        self.root, self.world, self.terrain_step = root, world, terrain_step
        self.cache_dir = cache_dir
        self.title, self.spawn = title, spawn
        self.decode_replays = decode_replays and replay_file is not None
        self._terrain_backups = list(terrain_backups or [])
        self.terrain_label, self.terrain_time = terrain_label, terrain_time
        self._init_terrain()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._track_files = list(track_files or [])
        self._replay_tracks: list[str] = []
        self.decode_progress = [0, 0]
        self.warnings: list[str] = []
        self._payload = history_payload(data, title)
        try:                                       # the version band: which Minecraft, and is its Flashback here
            from . import flashback as fbm
            segs = version_segments(data, (self._payload.get("range") or [None, None])[1])
            names = {t["mc"]: t["name"] for t in fbm.version_targets(s["mc"] for s in segs)}
            self._payload["versions"] = [{**s, "flashback": names.get(s["mc"])} for s in segs]
        except Exception:                          # noqa: BLE001 -- the band is a bonus
            self._payload["versions"] = []
        self._paths_cache: tuple[int, dict] | None = None
        self._loaded: dict[str, dict[str, list[list]]] = {}
        self._httpd = None

    # -- lifecycle ----------------------------------------------------------
    def start(self, port: int = 8766, host: str = "127.0.0.1") -> str:
        self._httpd = serve(self, port, host)
        for target in (self._httpd.serve_forever, self._build_terrain, self._decode, self._find_changes):
            threading.Thread(target=target, daemon=True).start()
        return f"http://{host}:{self._httpd.server_address[1]}/"

    def stop(self) -> None:
        self._stop.set()
        stack = getattr(self, "_layer_stack", None)
        if stack is not None:
            stack.close()
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()

    def _decode(self) -> None:
        if not self.decode_replays:
            return

        def progress(n, total, _r):
            self.decode_progress = [n - 1, total]
            if self._stop.is_set():
                raise KeyboardInterrupt

        def ready(f):
            with self._lock:
                self._replay_tracks.append(f)

        try:
            _files, warns = paths_mod.tracks_from_replays(self.data, self.replay_file, self.cache_dir,
                                                          progress, ready)
        except KeyboardInterrupt:
            return
        with self._lock:
            self.warnings += warns
        self.decode_progress = [self.decode_progress[1], self.decode_progress[1]]

    # -- changes between backups ----------------------------------------------
    def _snapshots(self) -> list[dict]:
        """Every backup with a time, oldest first: ``{"path", "t", "label"}`` (the newest is ``root``)."""
        snaps = [{"path": b["path"], "t": b.get("t"), "label": b.get("label") or ""} for b in self._terrain_backups]
        if self.root and self.world:
            snaps.append({"path": self.root, "world": self.world, "t": self.terrain_time, "label": self.terrain_label})
        return sorted((s for s in snaps if s.get("t") is not None), key=lambda s: s["t"])

    def _find_changes(self) -> None:
        """Compare each pair of consecutive backups (newest first) once the map's terrain is built."""
        import contextlib
        import time as time_mod
        from . import changes as ch, source, terrain as terrain_mod, world as world_mod
        self.changes_progress = [0, 0]
        self._change_pairs: list[dict] = []
        snaps = self._snapshots()
        if len(snaps) < 2:
            self.changes_progress = [0, 0]
            self._changes_done = True
            return
        self.changes_progress = [0, len(snaps) - 1]
        while not self._stop.is_set():            # wait for the terrain (it reads the same files)
            layers = list(self.terrain_layers)
            if len(layers) >= len(self._terrain_backups) + 1 and all(L["done"] for L in layers):
                break
            time_mod.sleep(2)
        cache = terrain_mod.default_cache_dir() if self.terrain_cache is None else (self.terrain_cache or None)
        try:
            n = len(terrain_mod.region_files(self.root, self.world, "minecraft:overworld")) if self.root else 0
            coarse = self.terrain_step or terrain_mod.auto_step(max(1, n))
            for i in range(len(snaps) - 1, 0, -1):
                if self._stop.is_set():
                    return
                with contextlib.ExitStack() as stack:
                    pair = []
                    for s in (snaps[i - 1], snaps[i]):
                        if s.get("world"):
                            pair.append((s["path"], s["world"]))
                        else:
                            root = stack.enter_context(source.open_server(s["path"], include_regions=True))
                            props = world_mod.read_properties(os.path.join(root, "server.properties"))
                            pair.append((root, world_mod.world_dir(root, props)))
                    try:
                        res = ch.compare_cached(pair[0], pair[1], coarse_step=coarse, tile_cache=cache, stop=self._stop)
                    except Exception as exc:          # noqa: BLE001 -- one unreadable backup mustn't stop the rest
                        self.warnings.append(f"changes between backups {snaps[i - 1]['label']} and {snaps[i]['label']}: {exc}")
                        res = {"areas": [], "cells": {}}
                with self._lock:
                    self._change_pairs.append({"from": snaps[i - 1]["t"], "to": snaps[i]["t"],
                                               "from_label": snaps[i - 1]["label"], "to_label": snaps[i]["label"], **res})
                self.changes_progress[0] += 1
        finally:
            self._changes_done = True

    def changes_info(self) -> dict:
        """The changes found so far, newest backup interval first (without the cell lists)."""
        from . import changes as ch
        _n, tracks = getattr(self, "_tracks", (0, {}))
        with self._lock:
            pairs = list(getattr(self, "_change_pairs", []))
        out = []
        for p in sorted(pairs, key=lambda p: -p["to"]):
            areas = []
            for ar in p["areas"]:
                who = ch.presence(ar["area"], ar["dim"], p["from"], p["to"], tracks) if tracks else {"players": []}
                areas.append({**ar, "label": ch.label(ar["kind"]), "players": who["players"][:3]})
            out.append({k: p[k] for k in ("from", "to", "from_label", "to_label")} | {"areas": areas})
        return {"pairs": out, "progress": list(getattr(self, "changes_progress", [0, 0])),
                "done": bool(getattr(self, "_changes_done", False))}

    def decode_now(self) -> None:
        """Decode synchronously (tests and ``--no-server`` runs)."""
        self._decode()

    def terrain_focus(self) -> list[tuple[float, float]]:
        """Spawn plus where people logged in (most frequent spots first), so terrain around the
        action is built before far-away regions."""
        from collections import Counter
        pts = Counter()
        for e in self._payload["events"]:
            if e.get("pos") and e.get("dim") == "minecraft:overworld":
                pts[(round(e["pos"][0] / 256) * 256, round(e["pos"][2] / 256) * 256)] += 1
        sp = self.spawn or self._payload.get("spawn")
        out = [(sp[0], sp[2])] if sp else []
        return out + [p for p, _n in pts.most_common(20)]

    def terrain_backups(self) -> list[dict]:
        return self._terrain_backups

    # -- data for the browser -------------------------------------------------
    def info(self) -> dict:
        return {"title": self._payload["title"], "spawn": self.spawn or self._payload.get("spawn"),
                "terrain": self.terrain_info(), "terrain_layers": self.terrain_layers_info(),
                "terrain_pending": len(self._terrain_backups) + 1 - len(self.terrain_layers),
                "paths": {"done": self.decode_progress[0], "total": self.decode_progress[1],
                          "enabled": self.decode_replays},
                "changes": {"done": list(getattr(self, "changes_progress", [0, 0]))[0],
                            "total": list(getattr(self, "changes_progress", [0, 0]))[1],
                            "finished": bool(getattr(self, "_changes_done", False))},
                "moments": {"finished": getattr(self, "_work_state", "") == "done"},
                "warnings": self.warnings[-20:]}

    def paths(self) -> dict:
        """Paths per player from replays and track recordings (re-read only when files change)."""
        with self._lock:
            files = self._track_files + self._replay_tracks
        if self._paths_cache and self._paths_cache[0] == len(files):
            return self._paths_cache[1]
        # line the recordings up on the world clock (one offset per stretch of overlapping play)
        for f in files:
            if f not in self._metas:
                self._metas[f] = paths_mod.read_meta(f)
        self._offsets = paths_mod.world_offsets({f: self._metas[f] for f in files if self._metas.get(f)})
        for f in files:                       # each file is read once (again if its offset changed)
            off = self._offsets.get(f)
            if f not in self._loaded or self._loaded_off.get(f) != off:
                timing = {f: (off, self._metas[f]["clock"])} if off is not None else None
                self._loaded[f] = paths_mod.read_tracks([f], timing)
                self._loaded_off[f] = off
        aliases = self._payload["aliases"]
        merged: dict[str, list[list]] = {}
        ignored = set(self.data.ignored or [])
        for f in files:
            for name, pts in self._loaded[f].items():
                if name.lower() in ignored or aliases.get(name, name).lower() in ignored:
                    continue                      # never supposed to be there (mcsm.ignore)
                merged.setdefault(aliases.get(name, name), []).extend(pts)
        own = {n: prefer_own(sorted(p, key=lambda q: q[0])) for n, p in merged.items()}
        self._tracks = (len(files), own)                  # full detail, for finding building/exploring
        out = {"players": {n: thin(p) for n, p in own.items()}}
        out["points"] = sum(len(p) for p in out["players"].values())
        self._paths_cache = (len(files), out)
        return out

    def api_get(self, path: str):
        if path == "/api/history":
            return self._payload
        if path == "/api/paths":
            return self.paths()
        if path == "/api/changes":
            return self.changes_info()
        if path == "/api/highlights":
            return self.highlights_info()
        if path == "/api/moments":
            return self.moments()
        if path.startswith("/api/exact/"):           # every recorded movement in a time window: /api/exact/<from>/<to>
            try:
                a, b = (int(v) for v in path[len("/api/exact/"):].split("/"))
            except ValueError:
                return {"players": {}}
            return self.exact(a, b)
        if path == "/api/prepare":
            return self._prep_view()
        if path == "/api/review" or path.startswith("/api/review/"):   # the year in review: /api/review[/<year>]
            return self.review(path[len("/api/review/"):] if path.startswith("/api/review/") else "")
        if path == "/api/mobmodels":                  # mob models read from the newest readable Minecraft jar here
            from . import mobmodels
            m = mobmodels.cached_models()
            return {"jar": m.get("jar"), "layers": m.get("layers", {})}
        if path == "/api/skintimeline":               # the skin each player had in each recording
            return self.skin_timeline()
        if path.startswith("/api/skin/"):            # a player's real skin from Mojang: /api/skin/<uuid>
            from . import skins
            u = skins.clean_uuid(path[len("/api/skin/"):])
            s = skins.skin(u) if u else None
            return {"found": True, "url": f"/skin/{u}.png", "slim": s["slim"]} if s else {"found": False}
        if path.startswith("/api/blockat/"):         # dev mode: one block of a backup, /api/blockat/<layer>/<dim>/<x>/<y>/<z>
            try:
                layer, dim, x, y, z = path[len("/api/blockat/"):].split("/")
                bw = self.layer_world(int(layer))
                return bw.block_at(int(x), int(y), int(z), "minecraft:" + dim) if bw else {"message": "no such backup"}
            except (ValueError, OSError) as exc:
                return {"message": str(exc)}
        if path.startswith("/api/blocktile/"):       # real blocks close up: /api/blocktile/<layer>/<dim>/<tx>/<tz>
            try:
                layer, dim, tx, tz = path[len("/api/blocktile/"):].split("/")
                bw = self.layer_world(int(layer))
                if bw is None:
                    return {"faces": 0, "message": "that backup's world isn't readable"}
                return bw.block_tile(int(tx), int(tz), "minecraft:" + dim, f"/blocktile/{int(layer)}/")
            except (ValueError, OSError) as exc:
                return {"faces": 0, "message": str(exc)}
        if path.startswith("/api/momentrecs/"):      # dev: the recordings running at a moment, /api/momentrecs/<t ms>
            try:
                self._moment_why = []
                try:
                    recs = self.recordings_at(int(path[len("/api/momentrecs/"):]))
                    return {"recordings": [{"zip": os.path.basename(r["zip"]), "tick": r["tick"]} for r in recs],
                            "not_running": self._moment_why}
                finally:
                    self._moment_why = None
            except ValueError:
                return {"recordings": []}
        if path.startswith("/api/heatmap/"):         # where things happened (Ledger): /api/heatmap/<dim>/<from>/<to>/<kind>
            from . import ledger as ledger_mod
            try:
                dim, a, b, kind = path[len("/api/heatmap/"):].split("/")
                a, b = int(a), int(b)
            except ValueError:
                return {"cells": []}
            lg = self.ledger()
            if lg is None:
                return {"cells": [], "available": False}
            cache = self.__dict__.setdefault("_heat", {})
            key = (dim, a, b, kind)
            if key not in cache:
                cache[key] = ledger_mod.heat(lg, "minecraft:" + dim, a, b, kind)
                while len(cache) > 40:
                    cache.pop(next(iter(cache)))
            return {"cells": cache[key], "cell": 16, "available": True}
        if path == "/api/incidents":                  # builds damaged (Ledger): explosions, fire, lava, others
            return {"incidents": self.incidents() or []}
        if path == "/api/ledger":                     # the Ledger mod's block log: blocks at any moment it covers
            lg = self.ledger()
            return {"available": bool(lg), "from": lg.first if lg else None, "to": lg.last if lg else None}
        if path.startswith("/api/momentchanges/"):   # block changes the recordings saw: /api/momentchanges/<dim>/<from>/<to>
            try:
                dim, a, b = path[len("/api/momentchanges/"):].split("/")
                return self.moment_changes("minecraft:" + dim, int(a), int(b))
            except ValueError:
                return {"changes": []}
        if path.startswith("/api/momentchunk/"):     # one chunk at a moment: /api/momentchunk/<layer>/<dim>/<cx>/<cz>/<t ms>
            try:
                layer, dim, cx, cz, t = path[len("/api/momentchunk/"):].split("/")
                return self.moment_tile(int(layer), "minecraft:" + dim, int(cx), int(cz), int(t), chunk=True)
            except (ValueError, OSError) as exc:
                return {"faces": 0, "message": str(exc)}
        if path.startswith("/api/momenttile/"):      # blocks at a moment: /api/momenttile/<layer>/<dim>/<tx>/<tz>/<t ms>
            try:
                layer, dim, tx, tz, t = path[len("/api/momenttile/"):].split("/")
                return self.moment_tile(int(layer), "minecraft:" + dim, int(tx), int(tz), int(t))
            except (ValueError, OSError) as exc:
                return {"faces": 0, "message": str(exc)}
        if path.startswith("/api/tl3d/"):            # the build's world in 3D: /api/tl3d/<id>/tiles|blocks
            parts = path[len("/api/tl3d/"):].split("/")
            if len(parts) == 2 and parts[1] in ("tiles", "blocks"):
                try:
                    bw = self.build_world(parts[0])
                    return bw.tiles() if parts[1] == "tiles" else bw.blocks(f"/tl3d/{parts[0]}/")
                except (ValueError, OSError) as exc:     # shown on the page; the view works without it
                    return {"tiles": [], "faces": 0, "message": str(exc)}
        if path == "/api/timelapse/job":
            job = getattr(self, "_tl_job", None) or {"running": False, "log": [], "result": None, "error": None}
            return {**job, "log": job["log"][-5:]}
        return None

    def build_world(self, moment_id: str):
        """The real world around a build moment (terrain + blocks) from the backup saved right after it."""
        from . import timelapse as tl, world3d, worldpick as wp
        cache = self.__dict__.setdefault("_build_worlds", {})
        if moment_id in cache:
            return cache[moment_id]
        mom = next((x for x in (getattr(self, "_moments", None) or []) if x["id"] == moment_id), None)
        if mom is None or not mom.get("area"):
            raise ValueError("that isn't a building moment (reload the page)")
        found, label = None, ""
        if self.data.library_world:
            found = wp.backup_for(self.data.library_world, tl.from_ms(mom["end"]))
            label = os.path.basename(found[0]) if found else ""
        if not found and self.root and self.world:
            found, label = (self.root, self.world), "the loaded backup"
        if not found:
            raise ValueError("no backup of this world to show the build from")
        a = tl.moment_area(mom)
        bw = world3d.BuildWorld(found[0], found[1], [a.x1, a.z1, a.x2, a.z2], mom.get("mc"), label)
        cache[moment_id] = bw
        return bw

    def layer_world(self, layer: int):
        """The world of terrain layer *layer* (0 = the newest backup, then the older ones as the map numbers
        them) for real blocks, opened once."""
        import contextlib
        from . import source, world3d, world as world_mod
        cache = self.__dict__.setdefault("_layer_worlds", {})
        if layer in cache:
            return cache[layer]
        with self.__dict__.setdefault("_layer_lock", threading.Lock()):
            if layer in cache:
                return cache[layer]
            if layer == 0:
                found = (self.root, self.world) if self.root and self.world else None
            elif 1 <= layer <= len(self._terrain_backups):
                stack = self.__dict__.setdefault("_layer_stack", contextlib.ExitStack())
                root = stack.enter_context(source.open_server(self._terrain_backups[layer - 1]["path"], include_regions=True))
                props = world_mod.read_properties(os.path.join(root, "server.properties"))
                found = (root, world_mod.world_dir(root, props))
            else:
                found = None
            mc = None
            if found:
                if layer == 0:
                    mc = getattr(self, "terrain_version", None)
                elif 1 <= layer <= len(self._terrain_backups):
                    mc = self._terrain_backups[layer - 1].get("version")
                if not mc:                                # unknown: the version most recordings have
                    versions = [r.mc_version for r in self.data.replays if r.mc_version]
                    mc = max(set(versions), key=versions.count) if versions else None
            cache[layer] = world3d.BuildWorld(found[0], found[1], mc=mc, label=str(layer)) if found else None
            return cache[layer]

    def exact(self, t0: int, t1: int) -> dict:
        """Every recorded position from *t0* to *t1* (ms, at most 10 minutes): ``{"players": {name: [[t, x, y, z,
        dimension, yaw, pitch, head yaw], ...]}}``, on the world clock. A player's own recording wins; others' views of
        them fill the time it doesn't cover."""
        t1 = min(t1, t0 + 600_000)
        self.paths()                                   # the world-clock offsets
        players = paths_mod.player_uuids(self.data)
        aliases = self._payload["aliases"]
        ignored = set(self.data.ignored or [])
        own: dict[str, list] = {}
        seen: dict[str, list] = {}
        pending = 0
        mobs: dict[str, dict] = {}
        events: dict[str, dict[str, dict]] = {}       # name -> kind -> {t: value} (de-duplicated across recordings)
        world: dict[str, dict] = {}                   # time / rain / thunder -> {t: [value, rate]}
        before: dict[str, tuple] = {}                 # the world's last state before t0
        state_before: dict[tuple, tuple] = {}         # (name, kind) -> the last state before t0, however long ago
        state_after: dict[tuple, tuple] = {}          # equipment: the next one after t1, when nothing earlier is known
        for r in self.data.replays:
            if r.format != "flashback" or r.unfinished or r.start is None or r.raw_meta.get("duration_note"):
                continue
            s_ms = int(r.start.timestamp() * 1000)
            if s_ms - 120_000 > t1 or s_ms + r.duration_ms + 120_000 < t0:
                continue
            try:
                zp = self.replay_file(r)
                track = os.path.join(self.cache_dir or paths_mod.default_cache_dir(), paths_mod._cache_key(zp, players) + ".jsonl")
                data = self._exact_mem.get(zp) or paths_mod.exact_rows(zp, self.cache_dir, players, build=False)
                if data is not None:                   # keep the last dozen in memory (a slider moves a lot)
                    self._exact_mem[zp] = self._exact_mem.pop(zp, data)
                    while len(self._exact_mem) > 12:
                        self._exact_mem.pop(next(iter(self._exact_mem)))
                if data is None:                       # not read in full yet: in the background, a few at a time
                    pending += 1
                    self._exact_build(zp, players)
                    continue
            except (OSError, ValueError, zipfile.BadZipFile, struct.error, IndexError):
                continue
            off = self._offsets.get(track)
            meta = self._metas.get(track) or {}
            clock = meta.get("clock")
            def when(tick):
                return off + paths_mod.game_time(clock, tick) * 50 if off is not None and clock else s_ms + tick * 50
            for tick, w, kind, value in data.get("events", []):
                t = when(tick)
                # states (flags, pose, hand use, riding): the last one before t0 however long ago, and changes;
                # moments (swing, hurt, death) only in the window
                name = w if w and w.startswith("#") else aliases.get(w or r.recorded_player, w or r.recorded_player)
                if kind == "eq":                          # one state per slot: "eq:mainhand", "eq:head", ...
                    kind, value = f"eq:{value[0]}", value[1:]
                if (kind in ("f", "p", "h", "r", "it") or kind.startswith("eq:")) and t < t0:
                    if (name, kind) not in state_before or state_before[(name, kind)][0] < t:
                        state_before[(name, kind)] = (t, value)
                elif kind.startswith("eq:") and t > t1:
                    if (name, kind) not in state_after or state_after[(name, kind)][0] > t:
                        state_after[(name, kind)] = (t, value)
                elif t0 <= t <= t1:
                    events.setdefault(name, {}).setdefault(kind, {})[t] = value
            for tick, kind, value, rate in data.get("world", []):
                t = when(tick)
                if t < t0:
                    if kind not in before or before[kind][0] < t:
                        before[kind] = (t, value, rate)
                elif t <= t1:
                    world.setdefault(kind, {})[t] = [value, rate]
            for row in data["rows"]:
                tick = row[0]
                t = when(tick)
                if t < t0 or t > t1:
                    continue
                w = data["who"][row[1]]
                if w and w.startswith("#"):              # a mob or pet: by its entity id (shared by all recordings)
                    mob = mobs.setdefault(w, {"type": data.get("mobs", {}).get(w, w.split("#")[1]), "rows": {}})
                    mob["rows"][t] = [t, row[3], row[4], row[5], data["dims"][row[2]], row[6]]
                    continue
                name = aliases.get(w or r.recorded_player, w or r.recorded_player)
                if not name or name.lower() in ignored:
                    continue
                (own if w is None else seen).setdefault(name, []).append(
                    [t, row[3], row[4], row[5], data["dims"][row[2]], row[6], row[7], row[8]])
        for (name, kind), (t, value) in state_before.items():
            events.setdefault(name, {}).setdefault(kind, {}).setdefault(t, value)
        # equipment seen only later in a recording (its own player's is listed every 5 minutes): assume it was worn
        # from the start of the window (armour and tools rarely change without a new listing)
        for (name, kind), (t, value) in state_after.items():
            if (name, kind) not in state_before and not events.get(name, {}).get(kind):
                events.setdefault(name, {}).setdefault(kind, {})[t0] = value
        out = {}
        for name in set(own) | set(seen):
            mine = sorted(own.get(name, []))
            others = sorted(seen.get(name, []))
            if mine:                                   # someone else's view only where their own recording has nothing
                import bisect
                ts = [p[0] for p in mine]
                keep = []
                for p in others:
                    i = bisect.bisect_left(ts, p[0])
                    near = [ts[k] for k in (i - 1, i) if 0 <= k < len(ts)]
                    if not near or min(abs(p[0] - v) for v in near) > 16_000:
                        keep.append(p)
                others = keep
            out[name] = sorted(mine + others)
        return {"players": out, "from": t0, "to": t1, "pending": pending,
                "events": {n: {k: sorted([t, v] for t, v in d.items()) for k, d in kinds.items()} for n, kinds in events.items()},
                "world": {k: sorted(([list(before[k])] if k in before else [])        # the state just before, then changes
                                    + [[t, v[0], v[1]] for t, v in world.get(k, {}).items()])
                          for k in set(world) | set(before)},
                "mobs": {k: {"type": v["type"], "rows": [v["rows"][t] for t in sorted(v["rows"])]} for k, v in mobs.items()}}

    # ---- the year in review (mcsm.review) ----
    def review(self, which: str) -> dict:
        """``which``: a year (default: the year of the newest data), or ``<from>_<to>`` (YYYY-MM-DD, local).
        Also lists the years there is data for."""
        from datetime import datetime as dt, timedelta
        from . import review as rv
        tz, _name = rv.local_tz()
        times = [s.start for s in self.data.sessions if s.start]
        years = sorted({t.astimezone(tz).year for t in times}) or [dt.now().year]
        try:
            if "_" in which:
                a, b = which.split("_")
                start = dt.fromisoformat(a).astimezone() if tz is None else dt.fromisoformat(a).replace(tzinfo=tz)
                end = (dt.fromisoformat(b) + timedelta(days=1))
                end = end.astimezone() if tz is None else end.replace(tzinfo=tz)
                key = which
            else:
                year = int(which) if which else years[-1]
                start, end = rv.year_range(year, tz)
                key = str(year)
        except ValueError:
            return {"error": f"not a year or range: {which}"}
        cache = self.__dict__.setdefault("_reviews", {})
        if key not in cache:
            cache[key] = rv.build(self.data, start.astimezone(rv.timezone.utc), end.astimezone(rv.timezone.utc), tz,
                                  builds=self.builds(), incidents=self.incidents())
        return {**cache[key], "years": years, "key": key}

    # ---- blocks as they were at a moment (from the recordings' chunk data; see mcsm.moment) ----
    def recordings_at(self, t: int) -> list[dict]:
        """Every recording running at *t* (ms, world clock): ``{"zip", "tick", "when"}`` for :mod:`mcsm.moment`."""
        from . import shot
        self.paths()                                   # the world-clock offsets
        players = paths_mod.player_uuids(self.data)
        out = []
        for r in self.data.replays:
            if r.format != "flashback" or r.unfinished or r.start is None or any(c in r.path for c in APP_COPIES):
                continue
            s_ms = int(r.start.timestamp() * 1000)
            if s_ms - 120_000 > t or s_ms + r.duration_ms + 120_000 < t:
                continue
            try:
                zp = self.replay_file(r)
            except (OSError, ValueError):
                continue
            track = os.path.join(self.cache_dir or paths_mod.default_cache_dir(), paths_mod._cache_key(zp, players) + ".jsonl")
            off, meta = self._offsets.get(track), self._metas.get(track) or {}
            a, b = shot.span(meta, off, s_ms, r.duration_ms)
            if not a <= t <= b:
                why = getattr(self, "_moment_why", None)        # dev: why a recording isn't used
                if why is not None:
                    why.append([os.path.basename(zp), a, b, off, bool(meta.get("clock"))])
                continue
            clock = meta.get("clock")

            def when(tick, off=off, clock=clock, s_ms=s_ms):
                return off + paths_mod.game_time(clock, tick) * 50 if off is not None and clock else s_ms + tick * 50
            out.append({"zip": zp, "tick": shot.tick_at(meta, off, s_ms, t), "when": when,
                        "tick_of": lambda ms, meta=meta, off=off, s_ms=s_ms: shot.tick_at(meta, off, s_ms, ms)})
        return out

    def moment_changes(self, dim: str, t0: int, t1: int) -> dict:
        """Every block change the recordings saw from *t0* to *t1* (ms, at most 10 minutes), in *dim*:
        ``{"changes": [[t, x, y, z], ...]}`` sorted by time (for showing them as they happen)."""
        from . import moment
        t1 = min(t1, t0 + 600_000)
        seen, out = set(), []
        for rec in self.recordings_at(t0) + self.recordings_at((t0 + t1) // 2) + self.recordings_at(t1):
            if rec["zip"] in seen:
                continue
            seen.add(rec["zip"])
            for tick, d, x, y, z, _sid in moment.edits_between(rec["zip"], rec["tick_of"](t0), rec["tick_of"](t1)):
                t = rec["when"](tick)
                if d == dim and t0 <= t <= t1:
                    out.append([int(t), x, y, z])
        lg = self.ledger()                             # and every change the Ledger log has (anyone, anywhere)
        if lg:
            far = 30_000_000
            out += [[ms, x, y, z] for ms, x, y, z, _new, _old in lg.changes(dim, -far, -far, far, far, t0, t1)]
        out.sort()
        return {"changes": out}

    def ledger(self):
        """The Ledger mod's block log of the newest backup (mcsm.ledger), or None."""
        from . import ledger as ledger_mod
        return ledger_mod.open_ledger(ledger_mod.find(self.root, self.world))

    def builds(self) -> list | None:
        """Every build from the Ledger log (mcsm.ledger.find_builds), worked out once and kept; None without it."""
        from . import ledger as ledger_mod
        lg = self.ledger()
        if lg is None:
            return None
        if getattr(self, "_builds", None) is None:
            cache = os.path.join(os.path.dirname(self.cache_dir or paths_mod.default_cache_dir()), "ledger", "builds.json")
            try:
                self._builds = ledger_mod.find_builds(lg, cache_file=cache)
            except Exception:                              # noqa: BLE001 -- builds are an extra
                self._builds = []
        return self._builds

    def incidents(self) -> list | None:
        """Builds damaged, from the Ledger log (mcsm.ledger.find_incidents), worked out once; None without it."""
        from . import ledger as ledger_mod
        lg = self.ledger()
        if lg is None:
            return None
        if getattr(self, "_incidents", None) is None:
            cache = os.path.join(os.path.dirname(self.cache_dir or paths_mod.default_cache_dir()), "ledger", "incidents.json")
            try:
                self._incidents = ledger_mod.find_incidents(lg, cache_file=cache)
            except Exception:                              # noqa: BLE001 -- an extra
                self._incidents = []
        return self._incidents

    def layer_time(self, layer: int) -> int | None:
        """When terrain layer *layer*'s backup was saved (ms)."""
        if layer == 0:
            return self.terrain_time
        return (self._terrain_backups[layer - 1].get("t") if 1 <= layer <= len(self._terrain_backups) else None)

    def moment_tile(self, layer: int, dim: str, tx: int, tz: int, t: int, chunk: bool = False) -> dict:
        """One piece of real blocks (or, with *chunk*, one chunk) as the recordings saw it at *t*: built in the
        background (``pending`` until then); ``moment`` False when no recording had any of its chunks loaded (the
        backup's piece)."""
        from . import moment
        key = (layer, dim, tx, tz, t, chunk)
        done = self.__dict__.setdefault("_moment_done", {})
        if key in done:
            return done[key]
        jobs = self.__dict__.setdefault("_moment_jobs", set())
        lock = self.__dict__.setdefault("_moment_lock", threading.Lock())
        slots = self.__dict__.setdefault("_moment_slots", threading.Semaphore(2))
        with lock:
            if key in jobs or len(jobs) >= 8:              # (a slider dragged fast: ask again later)
                return {"pending": True}
            jobs.add(key)

        def work():
            info: dict
            with slots:
                try:
                    bw = self.layer_world(layer)
                    if bw is None:
                        info = {"faces": 0, "message": "that backup's world isn't readable"}
                    else:
                        n = 1 if chunk else bw.TILE // 16
                        wanted = {(tx * n + i, tz * n + j) for i in range(-1, n + 1) for j in range(-1, n + 1)}
                        cache = os.path.join(os.path.dirname(self.cache_dir or paths_mod.default_cache_dir()), "moment")
                        # 1. chunks a recording had loaded then (exact, growth and all)
                        over = moment.world_at(self.recordings_at(t), wanted, dim, cache)
                        # 2. elsewhere: the backup with the Ledger log's changes undone back to t (or replayed to it)
                        lg, base_t = self.ledger(), self.layer_time(layer)
                        logged = bool(lg and base_t is not None and lg.first is not None and t >= lg.first)
                        patches = lg.patches(dim, wanted - set(over), t, base_t) if logged else {}
                        mine = lambda k: tx * n <= k[0] < tx * n + n and tz * n <= k[1] < tz * n + n
                        inside = [k for k in over if mine(k)]
                        patched = [k for k in patches if mine(k)]
                        sig = "|".join(f"{k}:{c.loaded_ms}:{len(c.edits)}:{getattr(c, 'last_ms', 0)}"
                                       for k, c in sorted(over.items()))
                        if patches:
                            sig += "|ledger:" + hashlib.sha1(repr(sorted((k, sorted(v.items())) for k, v in
                                                                      patches.items())).encode()).hexdigest()[:16]
                        url = f"/blocktile/{layer}/"
                        as_at = {"moment": bool(inside or patched or logged), "recorded_chunks": len(inside),
                                 "ledger": logged, "ledger_chunks": len(patched),
                                 "edits": sum(len(over[k].edits) for k in inside) + sum(len(patches[k]) for k in patched)}
                        if chunk:
                            info = {**bw.moment_chunk(tx, tz, dim, url, over, sig or "backup", patches), **as_at}
                        elif not inside and not patched:     # nothing differs from the backup's piece
                            info = {**bw.block_tile(tx, tz, dim, url), **as_at}
                        else:
                            info = {**bw.moment_tile(tx, tz, dim, url, over, sig, patches), **as_at}
                except (OSError, ValueError, zipfile.BadZipFile, struct.error, IndexError, KeyError) as exc:
                    info = {"faces": 0, "message": f"blocks at that moment: {exc}"}
            with lock:
                done[key] = info
                while len(done) > 200:
                    done.pop(next(iter(done)))
                jobs.discard(key)
        threading.Thread(target=work, daemon=True).start()
        return {"pending": True}

    def _exact_build(self, zp: str, players) -> None:
        """Read a recording in full for exact playback in the background (three at a time; each once)."""
        with self._exact_lock:
            if zp in self._exact_jobs:
                return
            self._exact_jobs.add(zp)

        def work():
            with self._exact_slots:
                try:
                    paths_mod.exact_rows(zp, self.cache_dir, players)
                except (OSError, ValueError, zipfile.BadZipFile, struct.error, IndexError):
                    pass
            with self._exact_lock:
                self._exact_jobs.discard(zp)
        threading.Thread(target=work, daemon=True).start()

    # ---- preparing ahead (owner: "give a warning or make it adjustable") ----
    PREP_MBPS = 30            # recordings read in full, per second (measured ~45 MB/s on one thread; disk-bound)
    PREP_TILE_S = 1.2         # one 128 x 128 piece of real blocks (measured on the owner's world)

    def _prep_view(self) -> dict:
        return {k: v for k, v in self._prep.items() if k != "items"}

    def _prep_items(self, body: dict) -> tuple[list, list, dict]:
        """What preparing *body* would do: (recordings to read in full, block pieces to build, estimate)."""
        import glob
        import statistics
        t0 = float(body.get("from") or 0) or -float("inf")
        t1 = float(body.get("to") or 0) or float("inf")
        who = body.get("player") or ""
        what = set(body.get("what") or ["exact", "blocks"])
        self.paths()
        players = paths_mod.player_uuids(self.data)
        cache = self.cache_dir or paths_mod.default_cache_dir()
        recs, size = [], 0
        if "exact" in what and self.replay_file is not None:
            for r in self.data.replays:
                if r.format != "flashback" or r.unfinished or r.start is None or r.raw_meta.get("duration_note") \
                        or any(c in r.path for c in APP_COPIES):
                    continue
                a = r.start.timestamp() * 1000
                if a > t1 or a + r.duration_ms < t0:
                    continue
                if who and self._payload["aliases"].get(r.recorded_player, r.recorded_player) != who:
                    continue
                try:
                    zp = self.replay_file(r)
                    if os.path.isfile(os.path.join(cache, paths_mod._cache_key(zp, players) + paths_mod.EXACT_SUFFIX)):
                        continue
                    size += os.path.getsize(zp)
                except (OSError, ValueError):
                    continue
                recs.append(zp)
        tiles = []
        if "blocks" in what:
            layers = sorted((L for L in self.terrain_layers if L.get("t") is not None), key=lambda L: L["t"])
            stamps = [L["t"] for L in layers]
            import bisect
            _n, tracks = getattr(self, "_tracks", (0, {}))
            seen = set()
            for name, pts in tracks.items():
                if who and name != who:
                    continue
                for q in pts:
                    if not t0 <= q[0] <= t1:
                        continue
                    i = bisect.bisect_left(stamps, q[0])            # the first backup saved at or after then
                    layer = (layers[i] if i < len(layers) else layers[-1])["id"] if layers else 0
                    key = (layer, q[4], int(q[1] // 128), int(q[3] // 128))
                    if key not in seen:
                        seen.add(key)
                        bw = self.layer_world(layer)
                        if bw is not None and not bw.tile_ready(key[2], key[3], key[1]):
                            tiles.append(key)
        # disk: as much as what's prepared already takes, per MB of recording / per piece
        ex = glob.glob(os.path.join(cache, "*" + paths_mod.EXACT_SUFFIX))
        ratio = 0.025
        tile_mb = 1.1
        try:
            tl = glob.glob(os.path.join(self.layer_world(0).cache, "tiles-blocks", "*.bin")) if self.layer_world(0) else []
            if tl:
                tile_mb = statistics.median(os.path.getsize(f) for f in tl[:200]) / 1e6 * 1.4
        except (OSError, AttributeError):
            pass
        est = {"recordings": len(recs), "recordings_mb": round(size / 1e6), "pieces": len(tiles),
               "minutes": round((size / 1e6 / self.PREP_MBPS + len(tiles) * self.PREP_TILE_S) / 60, 1),
               "disk_mb": round(size / 1e6 * ratio + len(tiles) * tile_mb), "already_prepared": len(ex)}
        return recs, tiles, est

    def prepare(self, body: dict, start: bool = False) -> dict:
        """Estimate (time, disk) what preparing ahead would do; with *start*, do it in the background."""
        if self._prep["running"]:
            return {**self._prep_view(), "message": "already preparing"}
        recs, tiles, est = self._prep_items(body)
        if not start:
            return {"estimate": est}
        players = paths_mod.player_uuids(self.data)
        self._prep.update({"running": True, "cancel": False, "done": 0, "total": len(recs) + len(tiles),
                           "phase": "recordings", "errors": 0, "estimate": est})

        def work():
            from concurrent.futures import ThreadPoolExecutor
            try:
                def one(zp):
                    if self._prep["cancel"]:
                        return
                    try:
                        paths_mod.exact_rows(zp, self.cache_dir, players)
                    except Exception:                    # noqa: BLE001 -- one bad file mustn't stop the rest
                        self._prep["errors"] += 1
                    self._prep["done"] += 1
                with ThreadPoolExecutor(3) as pool:
                    list(pool.map(one, recs))
                self._prep["phase"] = "real blocks"
                for layer, dim, tx, tz in tiles:
                    if self._prep["cancel"]:
                        break
                    try:
                        bw = self.layer_world(layer)
                        if bw is not None:
                            bw.block_tile(tx, tz, dim, f"/blocktile/{layer}/")
                    except Exception:                    # noqa: BLE001
                        self._prep["errors"] += 1
                    self._prep["done"] += 1
            finally:
                self._prep["running"] = False
                self._prep["phase"] = "cancelled" if self._prep["cancel"] else "done"
        threading.Thread(target=work, daemon=True).start()
        return self._prep_view()

    def shot(self, body: dict, send: bool = False) -> dict:
        """The recordings a shot can be filmed from (best first); with *send*, copy the chosen one (``recording``,
        else the best) into Flashback with the shot's camera keyframes (see :mod:`mcsm.shot`)."""
        import bisect
        from . import shot as shot_mod
        keys = []
        for k in body.get("keys") or []:
            try:
                keys.append({"t": float(k["t"]), "eye": [float(v) for v in k["eye"]][:3], "yaw": float(k["yaw"]),
                             "pitch": float(k["pitch"])})
            except (KeyError, TypeError, ValueError):
                raise ValueError("a keyframe needs t, eye [x, y, z], yaw and pitch")
        if len(keys) < 1:
            raise ValueError("add at least one camera keyframe")
        if self.replay_file is None:
            raise ValueError("recordings are not available in this session")
        self.paths()                                   # the world-clock offsets and positions
        players = paths_mod.player_uuids(self.data)
        _n, tracks = getattr(self, "_tracks", (0, {}))

        def near(player, t):
            pts = tracks.get(player) or []
            i = bisect.bisect_left([q[0] for q in pts], t)
            best = min((pts[j] for j in (i - 1, i) if 0 <= j < len(pts)), key=lambda q: abs(q[0] - t), default=None)
            return best[1:4] if best and abs(best[0] - t) < 60_000 else None
        recs = []
        for r in self.data.replays:
            if r.format != "flashback" or r.unfinished or r.start is None or r.raw_meta.get("duration_note")                     or any(c in r.path for c in APP_COPIES):
                continue
            try:
                zp = self.replay_file(r)
                track = os.path.join(self.cache_dir or paths_mod.default_cache_dir(), paths_mod._cache_key(zp, players) + ".jsonl")
            except (OSError, ValueError):
                continue
            recs.append({"replay": r, "meta": self._metas.get(track), "offset": self._offsets.get(track),
                         "start_ms": int(r.start.timestamp() * 1000), "duration_ms": r.duration_ms,
                         "player": self._payload["aliases"].get(r.recorded_player, r.recorded_player)})
        cands = shot_mod.candidates(recs, keys, near)
        view = [{"recording": c["replay"].path, "player": c["player"], "mc": c["replay"].mc_version,
                 "distance": None if c["distance"] == float("inf") else round(c["distance"]),
                 "from": c["from_ms"], "to": c["to_ms"]} for c in cands[:12]]
        if not send:
            return {"candidates": view}
        if not cands:
            raise ValueError("no recording covers the whole shot: shorten it, or move it to when someone was recording")
        pick = next((c for c in cands if c["replay"].path == body.get("recording")), cands[0])
        if not self._export_lock.acquire(blocking=False):
            raise ValueError("an export is already running")
        try:
            out = os.path.dirname(os.path.abspath(self.highlights.path))
            res = shot_mod.send(pick, keys, str(body.get("title") or "Shot")[:80], self.replay_file,
                                self.flashback_dir, out, self.flashback_pick, str(body.get("interpolation") or "SMOOTH"))
        finally:
            self._export_lock.release()
        return {**res, "candidates": view}

    def skin_timeline(self) -> dict:
        """``{name: [[start_ms, hash, slim], ...]}``: each player's skin from their recordings (worked out once)."""
        with self._skin_lock:
            if self._skins is None:
                from . import skins
                if self.replay_file is None:
                    self._skins = {}
                else:
                    by_uuid = {skins.clean_uuid(p.uuid): p.name for p in self.data.players.values() if p.uuid}
                    try:
                        self._skins = skins.timeline(self.data.replays, self.replay_file, by_uuid.get)
                    except OSError:
                        self._skins = {}
            return self._skins

    def file_for(self, path: str) -> str | None:
        """Files of the build's 3D world: /tl3d/<id>/tile/<rx>/<rz>/<step>, blocks-<key>.bin, tex/<name>.png;
        and the real blocks close up: /blocktile/<layer>/<key>.bin, /blocktex/<name>.png; the year in review's
        page: /review, /review/<year>, /review/<year>/<player>."""
        if path == "/review" or path.startswith("/review/"):
            return os.path.join(os.path.dirname(__file__), "assets", "web", "review.html")
        if path.startswith("/blocktile/") and path.endswith(".bin"):
            try:
                layer, key = path[len("/blocktile/"):-4].split("/")
                bw = self.layer_world(int(layer))
            except ValueError:
                return None
            return bw.block_tile_file(key) if bw else None
        if path.startswith("/mobtex/") and path.endswith(".png"):         # /mobtex/entity/<...>.png: from the models' jar
            from . import mobmodels
            return mobmodels.texture(path[len("/mobtex/"):-4])
        if path.startswith("/skintex/") and path.endswith(".png"):        # /skintex/<hash>.png: a skin from a recording
            from . import skins
            return skins.texture(path[len("/skintex/"):-4])
        if path.startswith("/skin/") and path.endswith(".png"):           # /skin/<uuid>.png (downloaded already)
            from . import skins
            return skins.skin_file(path[len("/skin/"):-4])
        if path.startswith("/blocktex/") and path.endswith(".png"):       # /blocktex/<layer>/<name>.png
            layer, _, name = path[len("/blocktex/"):-4].partition("/")
            try:
                bw = self.layer_world(int(layer))
            except ValueError:
                return None
            return bw.texture(name) if bw else None
        if not path.startswith("/tl3d/"):
            return None
        mid, _, rest = path[len("/tl3d/"):].partition("/")
        try:
            bw = self.build_world(mid)
        except ValueError:
            return None
        if rest.startswith("tile/"):
            try:
                rx, rz, step = (int(v) for v in rest[len("tile/"):].split("/"))
            except ValueError:
                return None
            return bw.tile_file(rx, rz, step)
        if rest == os.path.basename(bw.blocks_file()):
            f = bw.blocks_file()
            return f if os.path.isfile(f) else None
        if rest.startswith("tex/") and rest.endswith(".png"):
            return bw.texture(rest[len("tex/"):-4])
        return None

    def timelapse(self, moment_id: str, export: bool = False, camera: dict | None = None,
                  min_part_s: float | None = None) -> dict:
        """A timelapse of a build moment: the plan (nothing is read or written), or start making the
        Flashback copies in the background (progress: GET /api/timelapse/job)."""
        import shutil
        import threading
        from . import timelapse as tl
        mom = next((x for x in (getattr(self, "_moments", None) or []) if x["id"] == moment_id), None)
        if mom is None or mom.get("mode") not in ("building", "map_art"):
            raise ValueError("that isn't a building moment (reload the page)")
        if self.replay_file is None:
            raise ValueError("recordings are not available in this session")
        _n, tracks = getattr(self, "_tracks", (0, {}))
        if not tracks:
            raise ValueError("the players' positions haven't been read yet: wait until the status line says they are")
        pm = tl.plan_moment(self.data, mom, tracks, min_part_s=min_part_s)
        out_dir = os.path.dirname(os.path.abspath(self.highlights.path))
        # where each copy goes (each recording's Minecraft version can have its own Flashback), and space there
        need: dict[str, int] = {}
        for p in pm["parts"]:
            folder = tl.target_folder(p["replay"], self.flashback_dir, out_dir, self.flashback_pick)
            need[folder] = need.get(folder, 0) + (p["replay"].size_bytes or 0)
        short = []
        free_total = 0
        for folder, size in need.items():
            probe = folder
            while probe and not os.path.exists(probe):
                probe = os.path.dirname(probe)
            try:
                free = shutil.disk_usage(probe or out_dir).free
            except OSError:
                continue
            free_total += free
            if size > free * 0.9:
                short.append(f"{folder}: {free / 1e9:.1f} GB free, needs {size / 1e9:.1f} GB")
        cx, cy, cz, cyaw, cpitch = pm["camera"]
        summary = {"pov": pm["pov"], "speed": pm["speed"], "seconds": pm["seconds"], "bytes": pm["bytes"],
                   "free": free_total or None, "folders": sorted(need), "short": short,
                   "skipped": pm.get("skipped") or [], "min_part_s": pm["min_part_s"],
                   "max_min_part_s": tl.MAX_MIN_PART_S, "timeline": tl.timeline(pm),
                   "area": [pm["area"].x1, pm["area"].z1, pm["area"].x2, pm["area"].z2], "y": mom.get("y"),
                   "auto_view": {"eye": [cx, cy + tl.EYE_HEIGHT, cz], "yaw": cyaw, "pitch": cpitch},
                   "parts": [{"n": p["n"], "file": p["replay"].path.split("/")[-1], "seconds": p["seconds"]}
                             for p in pm["parts"]], "flashback_dir": self.flashback_dir}
        if not export:
            return summary
        if short:
            raise ValueError("not enough free space: " + "; ".join(short))
        if not self._export_lock.acquire(blocking=False):
            raise ValueError("an export is already running")
        self._tl_job = {"running": True, "log": [], "result": None, "error": None}

        def work():
            try:
                r = tl.export_moment(self.data, mom, tracks, self.replay_file, self.flashback_dir, out_dir,
                                     pick_dir=self.flashback_pick, progress=self._tl_job["log"].append,
                                     camera=camera, min_part_s=min_part_s)
                self._tl_job["result"] = {**summary, **{k: v for k, v in r.items() if k in ("parts", "order", "folder")}}
            except Exception as exc:                     # noqa: BLE001 -- shown on the page
                self._tl_job["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                self._tl_job["running"] = False
                self._export_lock.release()
        threading.Thread(target=work, daemon=True).start()
        return {"started": True, **summary}

    def moments(self) -> dict:
        """The important moments of the whole history, best first. The log events' moments come at once;
        building/exploring stretches are worked out once, in the background, when every recording's
        positions have been read (then the page asks again)."""
        from . import moments as mo
        import threading
        with self._lock:
            if getattr(self, "_moments", None) is None:
                self._moments = mo.find_moments(self.data)
                self._work_state = "waiting"
        done, total = self.decode_progress
        final = not self.decode_replays or bool(total and done >= total)
        if final and self._work_state == "waiting":
            self._work_state = "working"

            def work():
                try:
                    self.paths()                          # brings self._tracks up to date
                    _n, tracks = getattr(self, "_tracks", (0, {}))
                    builds = self.builds()                # exact builders, when the server has a Ledger log
                    incidents = self.incidents()          # and builds damaged (explosions, fire, others)
                    found = mo.find_moments(self.data, tracks=tracks or None, builds=builds, incidents=incidents)                         if tracks or builds or incidents else None
                    with self._lock:
                        if found is not None:
                            self._moments = found
                        self._work_state = "done"
                except Exception:                         # noqa: BLE001 -- keep the event moments
                    self._work_state = "done"
            threading.Thread(target=work, daemon=True).start()
        changes_done = bool(getattr(self, "_changes_done", False)) or not self._httpd
        if final and self._work_state == "done" and changes_done and not getattr(self, "_change_moments", False):
            from . import changes as ch                   # building moments the backups confirm, and the others
            self._change_moments = True
            _n, tracks = getattr(self, "_tracks", (0, {}))
            with self._lock:
                pairs = list(getattr(self, "_change_pairs", []))
                if pairs:
                    extra = ch.change_moments(pairs, tracks or {}, self._moments)
                    self._moments = sorted(self._moments + extra, key=lambda x: -x["score"])
        return {"moments": self._moments, "weights": mo.WEIGHTS,
                "positions_final": final and self._work_state == "done" and changes_done}

    # -- highlights ----------------------------------------------------------
    def highlights_info(self) -> dict:
        from . import highlights as hl
        items = self.highlights.list()
        for h in items:
            h["recordings"] = [{"path": r.path, "player": r.recorded_player,
                                "in_ms": max(0, h["start"] - _ms(r.start))} for r in hl.covering(self.data, h)]
        return {"file": self.highlights.path, "flashback_dir": self.flashback_dir, "highlights": items,
                "targets": self.export_targets(), "per_version": self.flashback_pick is not None}

    def export_targets(self) -> list[dict]:
        """Where copies go, per Minecraft version of the recordings (each version has its own Flashback)."""
        from . import flashback as fbm
        if getattr(self, "_targets", None) is None:
            self._targets = fbm.version_targets(r.mc_version for r in self.data.replays)
        return self._targets

    def api_post(self, path: str, body: dict):
        from . import highlights as hl
        if path == "/api/highlights":
            players = hl.players_in(self.data, int(body.get("start", 0)), int(body.get("end", 0)))
            self.highlights.put(body, players)
            return self.highlights_info()
        if path in ("/api/timelapse/plan", "/api/timelapse/export"):
            cut = body.get("min_part_s")
            return self.timelapse(str(body.get("id", "")), export=path.endswith("export"),
                                  camera=body.get("camera") if isinstance(body.get("camera"), dict) else None,
                                  min_part_s=float(cut) if isinstance(cut, (int, float)) else None)
        if path in ("/api/prepare/estimate", "/api/prepare/start"):   # preparing ahead, after an estimate
            return self.prepare(body, start=path.endswith("start"))
        if path == "/api/prepare/cancel":
            self._prep["cancel"] = True
            return self._prep_view()
        if path in ("/api/shot/check", "/api/shot/send"):   # a shot planned here, rendered in Flashback
            return self.shot(body, send=path.endswith("send"))
        if path == "/api/highlights/delete":
            self.highlights.delete(str(body.get("id", "")))
            return self.highlights_info()
        if path == "/api/highlights/export":
            ids = set(body.get("ids") or [])
            items = [h for h in self.highlights.list() if not ids or h["id"] in ids]
            if not items:
                raise ValueError("no highlights to export")
            if self.replay_file is None:
                raise ValueError("recordings are not available in this session")
            if not self._export_lock.acquire(blocking=False):
                raise ValueError("an export is already running")
            try:
                out = os.path.dirname(os.path.abspath(self.highlights.path))
                return hl.export(self.data, items, self.replay_file, out, self.flashback_dir,
                                 self.data.world.get("level_name") or self._payload["title"],
                                 pick_dir=self.flashback_pick)
            finally:
                self._export_lock.release()
        return None
