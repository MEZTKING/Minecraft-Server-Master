"""History viewer: everything in the backups and recordings, as if it were one recording.

A 3D map (the live map's terrain and three.js page) with a time slider over the whole history.
Served on ``http://127.0.0.1:<port>`` (this computer only):

    /                      the viewer (mcsm/assets/web/history.html)
    /api/info              title, spawn, terrain progress, path-decoding progress
    /api/terrain/...       terrain tiles (same as the live map)
    /api/history           time range, players, events, sessions, replays, backups
    /api/paths             player paths decoded so far (grows while recordings are read)

Position sources: player paths decoded from Flashback recordings (:mod:`mcsm.paths`), live-map
track recordings (``mcsm live --record``), and join positions from the logs.
"""

from __future__ import annotations

import math
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
                        "path": r.path, "format": r.format, "estimated": bool(r.raw_meta.get("duration_note"))})
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
                     "version": b.get("version")} for b in data.backups],
        "spawn": data.world.get("spawn"),
    }


class HistoryMap(TerrainServer):
    PAGE = "history.html"

    def __init__(self, data: m.ServerData, replay_file: Callable[[m.Replay], str] | None,
                 root: str | None = None, world: str | None = None, terrain_step: int | None = None,
                 cache_dir: str | None = None, track_files: list[str] | None = None, title: str = "",
                 spawn=None, decode_replays: bool = True):
        self.data, self.replay_file = data, replay_file
        self.root, self.world, self.terrain_step = root, world, terrain_step
        self.cache_dir = cache_dir
        self.title, self.spawn = title, spawn
        self.decode_replays = decode_replays and replay_file is not None
        self._init_terrain()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._track_files = list(track_files or [])
        self._replay_tracks: list[str] = []
        self.decode_progress = [0, 0]
        self.warnings: list[str] = []
        self._payload = history_payload(data, title)
        self._paths_cache: tuple[int, dict] | None = None
        self._loaded: dict[str, dict[str, list[list]]] = {}
        self._httpd = None

    # -- lifecycle ----------------------------------------------------------
    def start(self, port: int = 8766, host: str = "127.0.0.1") -> str:
        self._httpd = serve(self, port, host)
        for target in (self._httpd.serve_forever, self._build_terrain, self._decode):
            threading.Thread(target=target, daemon=True).start()
        return f"http://{host}:{self._httpd.server_address[1]}/"

    def stop(self) -> None:
        self._stop.set()
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

    # -- data for the browser -------------------------------------------------
    def info(self) -> dict:
        return {"title": self._payload["title"], "spawn": self.spawn or self._payload.get("spawn"),
                "terrain": self.terrain_info(),
                "paths": {"done": self.decode_progress[0], "total": self.decode_progress[1],
                          "enabled": self.decode_replays},
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
        for f in files:
            for name, pts in self._loaded[f].items():
                merged.setdefault(aliases.get(name, name), []).extend(pts)
        out = {"players": {n: thin(prefer_own(sorted(p, key=lambda q: q[0]))) for n, p in merged.items()}}
        out["points"] = sum(len(p) for p in out["players"].values())
        self._paths_cache = (len(files), out)
        return out

    def api_get(self, path: str):
        if path == "/api/history":
            return self._payload
        if path == "/api/paths":
            return self.paths()
        return None
