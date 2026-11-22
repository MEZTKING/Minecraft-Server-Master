"""Find the spot in the world under a point of a video frame (for placing text on a mountain etc.).

The camera position, angles and field of view come from the Flashback edit; the ground comes from
the world as saved in the library backup taken right after the recording (the top block of every
column, read from the region files and cached). A ray from the camera through the clicked pixel is
followed until it meets the ground.
"""

from __future__ import annotations

import math
import os
import re
from datetime import datetime

from . import library as lib, terrain, world as world_mod


class GroundProbe:
    """Top-of-ground heights of one dimension of one backup, region by region on demand."""

    def __init__(self, root: str, world: str, dimension: str = "minecraft:overworld"):
        self.root, self.world, self.dimension = root, world, dimension
        self.files = {}
        for p in terrain.region_files(root, world, dimension):
            try:
                _r, rx, rz, _e = os.path.basename(p).split(".")
                self.files[(int(rx), int(rz))] = p
            except ValueError:
                continue
        self.tiles: dict[tuple[int, int], terrain.Tile | None] = {}

    def height(self, x: float, z: float) -> int | None:
        """Y of the top block at (x, z), or None (no data / outside the saved world)."""
        bx, bz = math.floor(x), math.floor(z)
        key = (bx >> 9, bz >> 9)
        if key not in self.tiles:
            p = self.files.get(key)
            self.tiles[key] = terrain.read_region_cached(p, 1, terrain.default_cache_dir()) if p else None
        tile = self.tiles[key]
        if tile is None:
            return None
        h = tile.heights[(bz & 511) * tile.size + (bx & 511)]
        return None if h == terrain.Tile.NO_DATA else h


def backup_for(world_dir: str, when: datetime | None) -> tuple[str, str] | None:
    """(server root, world folder) of the library backup saved at or right after *when*."""
    backups = lib.world_backups(world_dir)
    if not backups:
        return None
    pick = backups[-1]
    if when is not None:
        ms = when.timestamp() * 1000
        later = [b for b in backups if (b.get("snapshot_ms") or 0) >= ms]
        if later:
            pick = later[0]
    root = pick["path"]
    props = world_mod.read_properties(os.path.join(root, "server.properties"))
    return root, world_mod.world_dir(root, props)


def recording_date(path: str) -> datetime | None:
    """The date in a ServerReplay/Flashback recording's file name (2026-06-06--19-55-14.zip)."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})--(\d{2})-(\d{2})-(\d{2})", os.path.basename(path or ""))
    if not m:
        return None
    return datetime(*(int(v) for v in m.groups())).astimezone()


def world_of_recording(path: str) -> str | None:
    """The library world folder a recording is stored in, if any."""
    d = os.path.dirname(os.path.abspath(path or ""))
    while d and os.path.dirname(d) != d:
        if lib.is_world_dir(d):
            return d
        d = os.path.dirname(d)
    return None


def ray(cam: list[float], yaw: float, pitch: float, fov: float, width: int, height: int,
        sx: float, sy: float) -> tuple[float, float, float]:
    """Direction (unit vector) of the camera ray through pixel (sx, sy); inverse of videotext.project."""
    ry, rp = math.radians(yaw), math.radians(pitch)
    f = (-math.sin(ry) * math.cos(rp), -math.sin(rp), math.cos(ry) * math.cos(rp))
    r = (-math.cos(ry), 0.0, -math.sin(ry))
    u = (r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0])
    focal = (height / 2) / math.tan(math.radians(fov) / 2)
    a, b = (sx - width / 2) / focal, -(sy - height / 2) / focal
    d = tuple(f[i] + r[i] * a + u[i] * b for i in range(3))
    n = math.sqrt(sum(c * c for c in d))
    return d[0] / n, d[1] / n, d[2] / n


def hit_ground(probe: GroundProbe, cam: list[float], direction: tuple[float, float, float],
               max_dist: float = 1500.0, step: float = 0.5) -> tuple[float, float, float] | None:
    """First point along the ray where it meets the top of the ground (None: sky / no data)."""
    x, y, z = cam
    dx, dy, dz = direction
    t = 0.0
    while t < max_dist:
        t += step if t < 200 else step * 2
        px, py, pz = x + dx * t, y + dy * t, z + dz * t
        h = probe.height(px, pz)
        if h is not None and py <= h + 1:
            return px, h + 1.0, pz
    return None
