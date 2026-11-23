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

import math
import os
import threading
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


APP_COPIES = ("(highlights)", "(text)", "(timelapse", "(mcsm)")


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
        for f in files:                       # each file is read once
            if f not in self._loaded:
                self._loaded[f] = paths_mod.read_tracks([f])
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
        if path.startswith("/api/blocktile/"):       # real blocks close up: /api/blocktile/<layer>/<dim>/<tx>/<tz>
            try:
                layer, dim, tx, tz = path[len("/api/blocktile/"):].split("/")
                bw = self.layer_world(int(layer))
                if bw is None:
                    return {"faces": 0, "message": "that backup's world isn't readable"}
                return bw.block_tile(int(tx), int(tz), "minecraft:" + dim, f"/blocktile/{int(layer)}/")
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

    def file_for(self, path: str) -> str | None:
        """Files of the build's 3D world: /tl3d/<id>/tile/<rx>/<rz>/<step>, blocks-<key>.bin, tex/<name>.png;
        and the real blocks close up: /blocktile/<layer>/<key>.bin, /blocktex/<name>.png."""
        if path.startswith("/blocktile/") and path.endswith(".bin"):
            try:
                layer, key = path[len("/blocktile/"):-4].split("/")
                bw = self.layer_world(int(layer))
            except ValueError:
                return None
            return bw.block_tile_file(key) if bw else None
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
                    found = mo.find_moments(self.data, tracks=tracks or None) if tracks else None
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
