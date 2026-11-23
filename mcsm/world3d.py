"""The real world in 3D from a backup: for the timelapse view (around a build) and the video editor's map
(along an edit's camera path).

Terrain (the same surface tiles as the history viewer) and, close by, real blocks with the game's own
textures (:mod:`mcsm.blockmesh`). The timelapse view uses the library backup saved at or right after the
build ended (so the finished build shows). Files are cached in ``%LOCALAPPDATA%/mcsm/build-view/``.
"""

from __future__ import annotations

import array
import hashlib
import os
import sys

from . import blockmesh as bm, terrain

MAX_TILES = 25
BLOCK_MARGIN = 40.0          # real blocks this far around the build's area


def cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mcsm", "build-view")


class BuildWorld:
    """*area* ``[x0, z0, x1, z1]`` (a build) is where tiles and blocks are taken by default; the editor gives
    points along its camera path instead (:meth:`tiles`/:meth:`blocks` with *points*)."""

    def __init__(self, root: str, world: str, area: list[float] | None = None, mc: str | None = None,
                 label: str = "", cache: str | None = None):
        self.root, self.world, self.mc, self.label = root, world, mc, label
        self.area = [float(v) for v in area] if area else None
        self.cache = cache or cache_dir()
        self.key = hashlib.sha1(f"{os.path.abspath(root)}|{self.area}".encode()).hexdigest()[:16]
        self.jar: str | None = None
        self._regions: dict[tuple[int, int], str] | None = None
        self._look = None
        self._blocks: dict | None = None

    def regions(self) -> dict[tuple[int, int], str]:
        if self._regions is None:
            have = {}
            for p in terrain.region_files(self.root, self.world, "minecraft:overworld"):
                try:
                    _r, rx, rz, _e = os.path.basename(p).split(".")
                    have[(int(rx), int(rz))] = p
                except ValueError:
                    continue
            self._regions = have
        return self._regions

    def tiles(self, points: list[tuple[float, float]] | None = None, max_tiles: int = MAX_TILES) -> dict:
        """The regions of the build or under *points* (every 2 blocks) and around them (every 4)."""
        if points is not None:
            near = {(int(x) >> 9, int(z) >> 9) for x, z in points}
        else:
            x0, z0, x1, z1 = self.area
            near = {(rx, rz) for rx in range(int(x0) >> 9, (int(x1) >> 9) + 1) for rz in range(int(z0) >> 9, (int(z1) >> 9) + 1)}
        around = {(rx + dx, rz + dz) for rx, rz in near for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
        have = self.regions()
        keys = sorted(near) + sorted(around - near)
        out = [{"rx": rx, "rz": rz, "step": 2 if (rx, rz) in near else 4} for rx, rz in keys if (rx, rz) in have]
        return {"tiles": out[:max_tiles], "backup": self.label or os.path.basename(self.root)}

    def tile_file(self, rx: int, rz: int, step: int) -> str | None:
        p = self.regions().get((rx, rz))
        if not p or step not in (1, 2, 4, 8, 16):
            return None
        key = hashlib.sha1(f"{os.path.abspath(self.root)}|{rx}|{rz}|{step}".encode()).hexdigest()[:20]
        out = os.path.join(self.cache, "tiles", key + ".bin")
        if os.path.exists(out):
            return out
        t = terrain.read_region_cached(p, step, terrain.default_cache_dir())
        if t is None:
            return None
        heights = array.array("h", t.heights)
        if sys.byteorder != "little":
            heights.byteswap()
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out + ".part", "wb") as fh:
            fh.write(heights.tobytes() + bytes(t.colours))
        os.replace(out + ".part", out)
        return out

    def look(self):
        if self._look is None:
            self.jar = bm.find_client_jar(self.mc)
            self._look = bm.Look(self.jar)
        return self._look

    def blocks(self, url_prefix: str, points: list[tuple[float, float]] | None = None,
               radius: float = BLOCK_MARGIN) -> dict:
        """Real blocks over the build (or within *radius* of *points*), written to a file the page fetches
        (``<url_prefix>`` + the file's name, see :meth:`blocks_file`)."""
        if self._blocks is not None:
            return self._blocks
        look = self.look()
        if not look.zf:
            return {"faces": 0, "message": "no Minecraft client jar found on this PC (for the block textures)"}
        if points is None:
            x0, z0, x1, z1 = self.area
            pts = [(x, z) for x in _steps(x0, x1, 16) for z in _steps(z0, z1, 16)]
        else:
            pts = list(points)
            self.key = hashlib.sha1(f"{os.path.abspath(self.root)}|{pts[:1]}|{pts[-1:]}|{len(pts)}|{radius}".encode()).hexdigest()[:16]
        r = bm.build(self.root, self.world, pts, look, radius=radius)
        os.makedirs(self.cache, exist_ok=True)
        with open(self.blocks_file() + ".part", "wb") as fh:
            fh.write(r["faces"])
        os.replace(self.blocks_file() + ".part", self.blocks_file())
        self._blocks = {"faces": len(r["faces"]) // 10, "origin": r["origin"],
                        "url": url_prefix + os.path.basename(self.blocks_file()),
                        "infos": [{k: i[k] for k in ("name", "kind", "box", "tex")} for i in r["infos"]],
                        "textures": r["textures"], "tints": {t: c for t, c in bm.TINTS.items() if t in r["textures"]},
                        "jar": os.path.basename(self.jar or "")}
        return self._blocks

    TILE = 128                                       # blocks per side of a block tile (the history viewer's pieces)
    TILE_VERSION = 1

    def block_tile(self, tx: int, tz: int, dim: str, url_prefix: str) -> dict:
        """Real blocks of one 128 x 128 piece of the world (tile *tx*, *tz*), for the history viewer: built once
        and kept on disk (``blocktile-<key>.bin`` + ``.json``). ``faces`` 0: nothing there (not explored)."""
        import json
        n = self.TILE // 16
        key = hashlib.sha1(f"{self.TILE_VERSION}|{os.path.abspath(self.root)}|{self.world}|{dim}|{tx}|{tz}|{self.TILE}|{self.mc}".encode()).hexdigest()[:20]
        base = os.path.join(self.cache, "tiles-blocks", key)
        try:
            with open(base + ".json", encoding="utf-8") as fh:
                info = json.load(fh)
            if not info["faces"] or os.path.isfile(base + ".bin"):
                return {**info, "url": url_prefix + key + ".bin"}
        except (OSError, ValueError, KeyError):
            pass
        look = self.look()
        if not look.zf:
            return {"faces": 0, "message": "no Minecraft client jar found on this PC (for the block textures)"}
        only = {(tx * n + i, tz * n + j) for i in range(n) for j in range(n)}
        r = bm.build(self.root, self.world, [], look, dimension=dim, only=only)
        os.makedirs(os.path.dirname(base), exist_ok=True)
        if r["faces"]:
            with open(base + ".bin.part", "wb") as fh:
                fh.write(r["faces"])
            os.replace(base + ".bin.part", base + ".bin")
        info = {"faces": len(r["faces"]) // 10, "origin": r["origin"], "tile": [tx, tz, self.TILE],
                "infos": [{k: i[k] for k in ("name", "kind", "box", "tex")} for i in r["infos"]],
                "textures": r["textures"], "tints": {t: c for t, c in bm.TINTS.items() if t in r["textures"]},
                "jar": os.path.basename(self.jar or ""), "mc": self.mc,
                "exact_jar": bool(self.jar and self.mc and self.mc in os.path.basename(self.jar))}
        with open(base + ".json.part", "w", encoding="utf-8") as fh:
            json.dump(info, fh, separators=(",", ":"))
        os.replace(base + ".json.part", base + ".json")
        return {**info, "url": url_prefix + key + ".bin"}

    def block_tile_file(self, key: str) -> str | None:
        if not all(c in "0123456789abcdef" for c in key) or len(key) != 20:
            return None
        p = os.path.join(self.cache, "tiles-blocks", key + ".bin")
        return p if os.path.isfile(p) else None

    def blocks_file(self) -> str:
        return os.path.join(self.cache, f"blocks-{self.key}.bin")

    def texture(self, name: str) -> str | None:
        if not name or ".." in name or not all(c.isalnum() or c in "_/-." for c in name):
            return None
        data = self.look().texture_png(name)
        if data is None:
            return None
        out = os.path.join(self.cache, "textures", os.path.splitext(os.path.basename(self.jar or "jar"))[0],
                           *name.split("/")) + ".png"
        if not os.path.exists(out):
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out + ".part", "wb") as fh:
                fh.write(data)
            os.replace(out + ".part", out)
        return out


def _steps(a: float, b: float, step: float) -> list[float]:
    out, v = [], a
    while v < b:
        out.append(v)
        v += step
    out.append(b)
    return out
