"""Live 3D map: where players are right now.

Pieces
------
* **Sources** produce player snapshots:
    - :class:`RconSource` asks the real server over RCON, using only read-only
      commands::

          list                                  -> who is online
          data get entity <name> Pos            -> [x, y, z]
          data get entity <name> Rotation       -> [yaw, pitch]
          data get entity <name> Dimension      -> "minecraft:overworld"
          data get entity <name> Health | foodLevel | XpLevel | playerGameType
                                                  (every 5th poll; they change slowly)

      All commands of one poll are pipelined, so a poll costs about one network
      round trip however many players are online.
    - :class:`DemoSource` simulates a few players walking around, so the map
      can be tried without a server.
* :class:`LiveMap` polls the source on a background thread, keeps a short
  movement trail per player, detects joins/leaves, optionally records every
  snapshot to ``live-tracks/YYYY-MM-DD.jsonl``, and serves:

      /                         the 3D viewer (mcsm/assets/web/live.html, three.js)
      /api/info                 server name, spawn, terrain build progress
      /api/terrain/<dim>        tile list for a dimension
      /api/terrain/<dim>/<rx>/<rz>   one tile: int16 heights + RGB bytes
      /api/state                latest snapshot
      /api/stream               Server-Sent Events, one message per poll

  on ``http://127.0.0.1:<port>`` (this computer only).

Terrain comes from the **downloaded** world (see :mod:`mcsm.terrain`), so
newly built things only appear after your next download; players are live.
"""

from __future__ import annotations

import json
import math
import os
import queue
import random
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from . import terrain as terrain_mod
from .rcon import RconClient, RconError

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "web")
TRAIL_SECONDS = 300          # how much movement history the viewer shows
TRAIL_MIN_MOVE = 0.5         # blocks; smaller moves don't add trail points

_NUM = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_FMT = re.compile(r"§[0-9a-fk-orx]", re.IGNORECASE)


@dataclass
class PlayerState:
    name: str
    x: float | None = None
    y: float | None = None
    z: float | None = None
    yaw: float = 0.0
    pitch: float = 0.0
    dimension: str = "minecraft:overworld"
    health: float | None = None
    food: int | None = None
    xp_level: int | None = None
    game_mode: str | None = None


# --- parsing RCON replies ----------------------------------------------------

def parse_list(reply: str) -> list[str]:
    """Names from the reply to ``list``.

    Handles ``There are 2 of a max of 20 players online: Steve, Alex`` (vanilla,
    Paper) and ``There are 2/20 players online:\\nSteve, Alex`` (older Bukkit).
    """
    reply = _FMT.sub("", reply)
    m = re.search(r"online:?(.*)$", reply, re.DOTALL | re.IGNORECASE)
    if not m:
        return []
    names = [n.strip() for n in re.split(r"[,\n]", m.group(1))]
    # Some forks prefix groups ("default: Steve"); keep the last word of each item.
    return [n.split(":")[-1].strip() for n in names if n.strip()]


def entity_value(reply: str) -> str | None:
    """The SNBT value from ``... has the following entity data: <value>``, or None."""
    m = re.search(r"entity data:\s*(.*)$", reply, re.DOTALL)
    return m.group(1).strip() if m else None


def parse_numbers(value: str | None) -> list[float]:
    return [float(v) for v in re.findall(_NUM, value or "")]


def parse_string(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().strip('"').strip("'")


# --- sources ------------------------------------------------------------------

class RconSource:
    SLOW_EVERY = 5
    SLOW_FIELDS = ("Health", "foodLevel", "XpLevel", "playerGameType")

    def __init__(self, client: RconClient):
        self.client = client
        self._polls = 0
        self._slow: dict[str, dict[str, Any]] = {}
        self.server_label = f"{client.host}:{client.port}"

    def poll(self) -> list[PlayerState]:
        names = parse_list(self.client.command("list"))
        slow = self._polls % self.SLOW_EVERY == 0
        self._polls += 1
        fields = ["Pos", "Rotation", "Dimension"] + (list(self.SLOW_FIELDS) if slow else [])
        cmds = [f"data get entity {n} {f}" for n in names for f in fields]
        replies = self.client.commands(cmds) if cmds else []
        out = []
        for i, name in enumerate(names):
            got = dict(zip(fields, (entity_value(r) for r in replies[i * len(fields):(i + 1) * len(fields)])))
            pos = parse_numbers(got.get("Pos"))
            if len(pos) < 3:
                continue  # left between "list" and "data get"
            rot = parse_numbers(got.get("Rotation")) + [0.0, 0.0]
            st = PlayerState(name, pos[0], pos[1], pos[2], rot[0], rot[1],
                             parse_string(got.get("Dimension")) or "minecraft:overworld")
            if slow:
                h, f, xp, gm = (parse_numbers(got.get(k)) for k in self.SLOW_FIELDS)
                self._slow[name] = {"health": h[0] if h else None, "food": int(f[0]) if f else None,
                                    "xp_level": int(xp[0]) if xp else None,
                                    "game_mode": {0: "survival", 1: "creative", 2: "adventure", 3: "spectator"}
                                    .get(int(gm[0])) if gm else None}
            for k, v in self._slow.get(name, {}).items():
                setattr(st, k, v)
            out.append(st)
        return out

    def close(self) -> None:
        self.client.close()


class DemoSource:
    """Simulated players for trying the map without a server."""

    NAMES = ["Steve", "Alex", "Builder42", "CreeperQueen"]

    def __init__(self, height_at=None, centre=(0.0, 0.0), seed: int = 7):
        self.rng = random.Random(seed)
        self.height_at = height_at or (lambda x, z: None)
        self.server_label = "demo"
        cx, cz = centre
        self.centre = (cx, cz)
        self.radius = 150.0  # stay near spawn so the demo stays on the downloaded terrain
        self.players = []
        for i, n in enumerate(self.NAMES):
            a = i * math.pi / 2
            self.players.append({"name": n, "x": cx + 30 * math.cos(a), "z": cz + 30 * math.sin(a),
                                 "heading": self.rng.uniform(0, 2 * math.pi), "online": True})
        self.t = 0

    def poll(self) -> list[PlayerState]:
        self.t += 1
        out = []
        for p in self.players:
            if self.rng.random() < 0.01:  # occasionally leave / rejoin
                p["online"] = not p["online"]
            if not p["online"]:
                continue
            p["heading"] += self.rng.uniform(-0.35, 0.35)
            dx, dz = p["x"] - self.centre[0], p["z"] - self.centre[1]
            if math.hypot(dx, dz) > self.radius:  # turn back towards the centre
                back = math.atan2(-dz, -dx)
                diff = (back - p["heading"] + math.pi) % (2 * math.pi) - math.pi
                p["heading"] += diff * 0.5
            speed = 4.3
            p["x"] += math.cos(p["heading"]) * speed
            p["z"] += math.sin(p["heading"]) * speed
            h = self.height_at(p["x"], p["z"])
            y = (h + 1) if h is not None else 64.0
            yaw = (math.degrees(p["heading"]) - 90) % 360 - 180  # Minecraft yaw: 0 = south (+z)
            out.append(PlayerState(p["name"], round(p["x"], 2), y, round(p["z"], 2), round(yaw, 1), 0.0,
                                   "minecraft:overworld", 20.0, 20, 5 + self.t // 30, "survival"))
        return out

    def close(self) -> None:
        pass


# --- the map service ------------------------------------------------------------

@dataclass
class _Track:
    points: list[tuple[float, float, float, float]] = field(default_factory=list)  # (t, x, y, z)
    dimension: str = ""


class TerrainServer:
    """Terrain tiles from a downloaded world, built in the background (shared by the live map
    and the history viewer). Needs ``root``, ``world``, ``terrain_step`` and ``_stop``."""

    root: str | None
    world: str | None
    terrain_step: int | None

    def _init_terrain(self) -> None:
        self.terrain: dict[str, dict[tuple[int, int], terrain_mod.Tile]] = {}
        self.terrain_progress: dict[str, list[int]] = {}

    def terrain_focus(self) -> list[tuple[float, float]]:
        """Block (x, z) points whose regions are built first (big worlds take a long time)."""
        sp = getattr(self, "spawn", None)
        return [(sp[0], sp[2])] if sp else []

    def _ordered_regions(self, files: list[str]) -> list[str]:
        focus = self.terrain_focus()
        if not focus:
            return files

        def dist(path: str) -> float:
            mt = re.search(r"r\.(-?\d+)\.(-?\d+)\.mca$", path)
            if not mt:
                return float("inf")
            cx, cz = int(mt.group(1)) * 512 + 256, int(mt.group(2)) * 512 + 256
            return min(math.hypot(cx - x, cz - z) for x, z in focus)
        return sorted(files, key=dist)

    def _build_terrain(self) -> None:
        if not (self.root and self.world):
            return
        for dim in ("minecraft:overworld", "minecraft:the_end"):
            files = self._ordered_regions(terrain_mod.region_files(self.root, self.world, dim))
            if not files:
                continue
            self.terrain_progress[dim] = [0, len(files)]
            step = self.terrain_step or terrain_mod.auto_step(len(files))
            tiles: dict[tuple[int, int], terrain_mod.Tile] = {}
            self.terrain[dim] = tiles
            for i, path in enumerate(files):
                if self._stop.is_set():
                    return
                try:
                    t = terrain_mod.read_region_surface(path, step)
                except Exception:  # noqa: BLE001 -- one unreadable region must not stop the rest
                    t = None
                if t:
                    tiles[(t.rx, t.rz)] = t
                self.terrain_progress[dim][0] = i + 1

    def height_at(self, x: float, z: float, dim: str = "minecraft:overworld") -> int | None:
        tiles = self.terrain.get(dim, {})
        t = tiles.get((math.floor(x) >> 9, math.floor(z) >> 9))
        if not t:
            return None
        lx, lz = (math.floor(x) & 511) // t.step, (math.floor(z) & 511) // t.step
        h = t.heights[lz * t.size + lx]
        return None if h == t.NO_DATA else h

    def terrain_info(self) -> dict:
        return {d: {"done": p[0], "total": p[1], "tiles": len(self.terrain.get(d, {}))}
                for d, p in self.terrain_progress.items()}


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second program bind a port that is already in use, so a
    # busy port would never be detected; elsewhere it only allows quick restarts.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def serve(app, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """An HTTP server for *app* on the first free port from *port* (this computer only)."""
    handler = _make_handler(app)
    for p in range(port, port + 20):
        try:
            httpd = _Server((host, p), handler)
            httpd.daemon_threads = True
            return httpd
        except OSError:
            continue
    raise OSError(f"no free port between {port} and {port + 19}")


class LiveMap(TerrainServer):
    PAGE = "live.html"

    def __init__(self, source, root: str | None = None, world: str | None = None, interval: float = 1.0,
                 record_dir: str | None = None, terrain_step: int | None = None, title: str = "",
                 spawn: tuple[float, float, float] | None = None):
        self.source, self.root, self.world = source, root, world
        self.interval = max(0.2, float(interval))
        self.record_dir = record_dir
        self.terrain_step = terrain_step
        self.title = title or "Minecraft server"
        self.spawn = spawn
        self.players: dict[str, PlayerState] = {}
        self.tracks: dict[str, _Track] = {}
        self.events: list[dict] = []          # recent joins/leaves
        self.status = "starting"
        self.error: str | None = None
        self.last_poll: float | None = None
        self._init_terrain()
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._httpd: ThreadingHTTPServer | None = None
        self._threads: list[threading.Thread] = []

    # -- lifecycle ----------------------------------------------------------
    def start(self, port: int = 8765, host: str = "127.0.0.1") -> str:
        self._httpd = serve(self, port, host)
        for target in (self._httpd.serve_forever, self._poll_loop, self._build_terrain):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)
        return f"http://{host}:{self._httpd.server_address[1]}/"

    def stop(self) -> None:
        self._stop.set()
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        try:
            self.source.close()
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            for q in self._subs:
                q.put(None)

    # -- polling ------------------------------------------------------------
    def _poll_loop(self) -> None:
        backoff = self.interval
        while not self._stop.is_set():
            started = time.time()
            try:
                states = self.source.poll()
                self._apply(states, started)
                self.status, self.error, backoff = "live", None, self.interval
            except (RconError, OSError) as exc:
                self.status, self.error = "reconnecting", str(exc)
                self._broadcast()
                backoff = min(30.0, backoff * 2)
            wait = (self.interval if self.status == "live" else backoff) - (time.time() - started)
            self._stop.wait(max(0.05, wait))

    def _apply(self, states: list[PlayerState], now: float) -> None:
        with self._lock:
            new = {s.name: s for s in states}
            for name in new.keys() - self.players.keys():
                self.events.append({"t": now, "type": "join", "player": name})
            for name in self.players.keys() - new.keys():
                self.events.append({"t": now, "type": "leave", "player": name})
            self.events = self.events[-50:]
            for s in states:
                tr = self.tracks.setdefault(s.name, _Track())
                if tr.dimension != s.dimension:
                    tr.points, tr.dimension = [], s.dimension
                last = tr.points[-1] if tr.points else None
                if last is None or math.dist(last[1:], (s.x, s.y, s.z)) >= TRAIL_MIN_MOVE:
                    tr.points.append((now, s.x, s.y, s.z))
                tr.points = [p for p in tr.points if now - p[0] <= TRAIL_SECONDS]
            self.players = new
            self.last_poll = now
        self._record(states, now)
        self._broadcast()

    def _record(self, states: list[PlayerState], now: float) -> None:
        if not self.record_dir:
            return
        os.makedirs(self.record_dir, exist_ok=True)
        path = os.path.join(self.record_dir, datetime.fromtimestamp(now).strftime("%Y-%m-%d") + ".jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": int(now * 1000), "players": [asdict(s) for s in states]}) + "\n")

    # -- state for the browser ----------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": self.status, "error": self.error, "server": self.source.server_label,
                "time": int((self.last_poll or time.time()) * 1000), "interval": self.interval,
                "players": [asdict(p) for p in self.players.values()],
                "trails": {n: {"dimension": t.dimension, "points": [[round(x, 1), round(y, 1), round(z, 1)]
                                                                   for _, x, y, z in t.points]}
                           for n, t in self.tracks.items() if n in self.players},
                "events": [{"t": int(e["t"] * 1000), "type": e["type"], "player": e["player"]} for e in self.events[-10:]],
            }

    def info(self) -> dict:
        return {"title": self.title, "server": self.source.server_label, "spawn": self.spawn,
                "terrain": self.terrain_info()}

    def _broadcast(self) -> None:
        msg = json.dumps(self.snapshot())
        with self._lock:
            for q in list(self._subs):
                q.put(msg)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=50)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)


_STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".png": "image/png", ".txt": "text/plain; charset=utf-8"}


def _make_handler(live: LiveMap):
    class Handler(BaseHTTPRequestHandler):
        server_version = "mcsm-live"

        def log_message(self, *args):  # keep the console quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj):
            self._send(200, json.dumps(obj).encode(), "application/json")

        def do_GET(self):  # noqa: N802
            path = unquote(urlparse(self.path).path)
            try:
                if path in ("/", "/index.html"):
                    return self._static(live.PAGE)
                if path.startswith("/vendor/"):
                    return self._static(path.lstrip("/"))
                if path == "/api/info":
                    return self._json(live.info())
                if path.startswith("/api/terrain/"):
                    return self._terrain(path[len("/api/terrain/"):].split("/"))
                extra = getattr(live, "api_get", None)
                if extra:                     # other apps (the history viewer) add their own routes
                    obj = extra(path)
                    if obj is not None:
                        return self._json(obj)
                    return self._send(404, b"not found", "text/plain")
                if path == "/api/state":
                    return self._json(live.snapshot())
                if path == "/api/stream":
                    return self._stream()
                self._send(404, b"not found", "text/plain")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _static(self, rel: str):
            full = os.path.realpath(os.path.join(WEB_DIR, rel))
            if not full.startswith(os.path.realpath(WEB_DIR) + os.sep) or not os.path.isfile(full):
                return self._send(404, b"not found", "text/plain")
            with open(full, "rb") as fh:
                self._send(200, fh.read(), _STATIC_TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))

        def _terrain(self, parts: list[str]):
            dim = parts[0]
            tiles = live.terrain.get(dim, {})
            if len(parts) == 1:
                return self._json({"dimension": dim, "tiles": [{"rx": t.rx, "rz": t.rz, "step": t.step, "size": t.size}
                                                               for t in list(tiles.values())]})
            try:
                t = tiles[(int(parts[1]), int(parts[2]))]
            except (KeyError, ValueError, IndexError):
                return self._send(404, b"no tile", "text/plain")
            import array
            heights = array.array("h", t.heights)
            if heights.itemsize != 2:
                return self._send(500, b"bad int size", "text/plain")
            import sys
            if sys.byteorder != "little":
                heights.byteswap()
            self._send(200, heights.tobytes() + t.colours, "application/octet-stream")

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            q = live.subscribe()
            try:
                self.wfile.write(f"data: {json.dumps(live.snapshot())}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=15)
                    except queue.Empty:
                        msg = ""
                    if msg is None:
                        break
                    self.wfile.write((f"data: {msg}\n\n" if msg else ": keep-alive\n\n").encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                live.unsubscribe(q)

    return Handler
