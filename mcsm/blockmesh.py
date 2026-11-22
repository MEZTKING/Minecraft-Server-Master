"""Real Minecraft blocks around a camera path, for the editor's map preview.

Reads every block near the surface within *radius* blocks of the camera path from a world backup
(region files), keeps only the faces you can see (next to air or something see-through), and
describes each block type by its look from the game's own files: the block's model (its outline
box, from the elements of its first model) and its textures, read from the Minecraft client jar
already installed on this PC (nothing is downloaded or bundled). Plants are crossed quads; grass,
leaves and water get their usual colours.

Output (``build``): ``{"origin": [x0, z0], "infos": [...], "textures": [...], "faces": bytes}``;
faces are little-endian int16 ``x - x0, y, z - z0, dir, info`` (dir 0 up, 1 down, 2 north -z,
3 south +z, 4 west -x, 5 east +x, 6 crossed plant).
"""

from __future__ import annotations

import glob
import json
import math
import os
import struct
import zipfile

from . import terrain

AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:light", "minecraft:barrier",
       "minecraft:structure_void"}
DIRS = [(0, 1, 0), (0, -1, 0), (0, 0, -1), (0, 0, 1), (-1, 0, 0), (1, 0, 0)]
FACE_NAMES = ["up", "down", "north", "south", "west", "east"]
GRASS, FOLIAGE, WATER = (124, 189, 107), (89, 174, 48), (63, 118, 228)
TINTS = {"block/grass_block_top": GRASS, "block/grass_block_side_overlay": GRASS, "block/short_grass": GRASS,
         "block/tall_grass_top": GRASS, "block/tall_grass_bottom": GRASS, "block/fern": GRASS,
         "block/large_fern_top": GRASS, "block/large_fern_bottom": GRASS, "block/sugar_cane": GRASS,
         "block/oak_leaves": FOLIAGE, "block/jungle_leaves": FOLIAGE, "block/acacia_leaves": FOLIAGE,
         "block/dark_oak_leaves": FOLIAGE, "block/mangrove_leaves": (146, 193, 98), "block/vine": FOLIAGE,
         "block/spruce_leaves": (97, 153, 97), "block/birch_leaves": (128, 167, 85), "block/lily_pad": (32, 128, 48),
         "block/water_still": WATER, "block/water_flow": WATER}


def find_client_jar(mc_version: str | None) -> str | None:
    """The installed Minecraft client jar for *mc_version* (Prism/MultiMC libraries or the official
    launcher), else the newest one found."""
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    cands = []
    for base in (os.path.join(appdata, "PrismLauncher"), os.path.join(appdata, "MultiMC"),
                 os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "PrismLauncher")):
        cands += glob.glob(os.path.join(base, "libraries", "com", "mojang", "minecraft", "*", "minecraft-*-client.jar"))
    cands += glob.glob(os.path.join(appdata, ".minecraft", "versions", "*", "*.jar"))
    if mc_version:
        exact = [c for c in cands if os.path.basename(c) in (f"minecraft-{mc_version}-client.jar", f"{mc_version}.jar")]
        if exact:
            return exact[0]
    cands.sort(key=os.path.getmtime)
    return cands[-1] if cands else None


class Look:
    """Block looks (outline box, face textures, kind) from a client jar."""

    def __init__(self, jar: str | None):
        self.zf = zipfile.ZipFile(jar) if jar else None
        self._models: dict[str, dict] = {}
        self.infos: list[dict] = []
        self.index: dict[str, int] = {}
        self.textures: list[str] = []
        self._tex_index: dict[str, int] = {}

    def _json(self, path: str) -> dict | None:
        if not self.zf:
            return None
        try:
            return json.loads(self.zf.read(path).decode("utf-8"))
        except (KeyError, ValueError):
            return None

    def _model(self, name: str) -> dict:
        """The model with its parents merged: textures (child wins), first elements found, parents."""
        name = name.replace("minecraft:", "")
        if name in self._models:
            return self._models[name]
        self._models[name] = {"textures": {}, "elements": None, "chain": [name]}     # guards against loops
        m = self._json(f"assets/minecraft/models/{name}.json") or {}
        merged = {"textures": {}, "elements": m.get("elements"), "chain": [name]}
        if m.get("parent"):
            p = self._model(m["parent"])
            merged["textures"] = dict(p["textures"])
            merged["chain"] += p["chain"]
            if merged["elements"] is None:
                merged["elements"] = p["elements"]
        merged["textures"].update(m.get("textures") or {})
        self._models[name] = merged
        return merged

    @staticmethod
    def _name(ref):
        """A texture reference: a name, or (newer versions) ``{"sprite": name, ...}``."""
        if isinstance(ref, dict):
            ref = ref.get("sprite") or ref.get("texture")
        return ref if isinstance(ref, str) else None

    def _tex(self, textures: dict, ref, depth: int = 0) -> str | None:
        ref = self._name(ref)
        while ref and ref.startswith("#") and depth < 8:
            ref = self._name(textures.get(ref[1:]))
            depth += 1
        if not ref or ref.startswith("#"):
            return None
        return ref.replace("minecraft:", "")

    def _tid(self, tex: str | None) -> int:
        tex = tex or "block/stone"
        if tex not in self._tex_index:
            self._tex_index[tex] = len(self.textures)
            self.textures.append(tex)
        return self._tex_index[tex]

    def info(self, block: str) -> int:
        """Index of the look of *block* (``minecraft:name``) in :attr:`infos`."""
        if block in self.index:
            return self.index[block]
        name = block.split(":", 1)[-1]
        kind, box, faces = "cube", [0, 0, 0, 16, 16, 16], ["block/" + name] * 6
        if name in ("water", "bubble_column"):
            kind, box, faces = "water", [0, 0, 0, 16, 14, 16], ["block/water_still"] * 6
        elif name == "lava":
            box, faces = [0, 0, 0, 16, 14, 16], ["block/lava_still"] * 6
        else:
            bs = self._json(f"assets/minecraft/blockstates/{name}.json") or {}
            model = None
            if bs.get("variants"):
                v = next(iter(bs["variants"].values()))
                model = (v[0] if isinstance(v, list) else v).get("model")
            elif bs.get("multipart"):
                a = bs["multipart"][0].get("apply")
                model = (a[0] if isinstance(a, list) else a or {}).get("model")
            if model:
                m = self._model(model)
                chain, tx = m["chain"], m["textures"]
                if any(c.endswith(("block/cross", "block/tinted_cross", "block/flower_pot_cross")) for c in chain) \
                        and "flower_pot" not in name:
                    kind = "cross"
                    faces = [self._tex(tx, "#cross") or self._tex(tx, "#plant") or "block/" + name] * 6
                else:
                    els = m["elements"] or []
                    if els and all(any(e["from"][a] == e["to"][a] for a in range(3)) for e in els):
                        kind = "cross"                      # only flat planes (seagrass, vines...): a plant
                        first = els[0].get("faces") or {}
                        faces = [self._tex(tx, next(iter(first.values()), {}).get("texture"))
                                 or self._tex(tx, "#particle") or "block/" + name] * 6
                        els = []
                    elif els:
                        lo = [min(e["from"][i] for e in els) for i in range(3)]
                        hi = [max(e["to"][i] for e in els) for i in range(3)]
                        box = [max(0, min(16, v)) for v in lo + hi]
                        first = els[0].get("faces") or {}
                        faces = [self._tex(tx, (first.get(f) or {}).get("texture")) or self._tex(tx, "#particle")
                                 for f in FACE_NAMES]
                    else:
                        faces = [self._tex(tx, "#" + f) or self._tex(tx, "#all") or self._tex(tx, "#particle")
                                 for f in FACE_NAMES]
                    if "leaves" in name or any(c.endswith("block/leaves") for c in chain):
                        kind = "cutout"
                    elif "glass" in name or name in ("ice", "slime_block", "honey_block", "spawner", "barrier"):
                        kind = "cutout"
            if name == "grass_block":                       # its side is dirt plus a tinted grass overlay
                faces[2:] = ["block/grass_block_side+overlay"] * 4
        full = box == [0, 0, 0, 16, 16, 16]
        if kind == "cube" and not full:
            kind = "part"
        info = {"name": name, "kind": kind, "box": box, "tex": [self._tid(f) for f in faces],
                "opaque": kind == "cube" and full}
        self.index[block] = len(self.infos)
        self.infos.append(info)
        return self.index[block]

    def texture_png(self, tex: str) -> bytes | None:
        if not self.zf:
            return None
        try:
            return self.zf.read(f"assets/minecraft/textures/{tex}.png")
        except KeyError:
            return None


def _chunks_near(points: list[tuple[float, float]], radius: float) -> set[tuple[int, int]]:
    out = set()
    r = int(math.ceil(radius / 16)) + 1
    for x, z in points:
        cx, cz = int(math.floor(x)) >> 4, int(math.floor(z)) >> 4
        for dx in range(-r, r + 1):
            for dz in range(-r, r + 1):
                if (dx * 16) ** 2 + (dz * 16) ** 2 <= (radius + 24) ** 2:
                    out.add((cx + dx, cz + dz))
    return out


def build(root: str, world: str, points: list[tuple[float, float]], look: Look, radius: float = 80.0,
          max_chunks: int = 900, dimension: str = "minecraft:overworld") -> dict:
    """The visible block faces within *radius* of the (x, z) *points* (the camera path)."""
    want = _chunks_near(points, radius)
    if len(want) > max_chunks:                     # a long flight: keep the chunks nearest to most points
        centre = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
        want = set(sorted(want, key=lambda c: (c[0] * 16 - centre[0]) ** 2 + (c[1] * 16 - centre[1]) ** 2)[:max_chunks])
    regions: dict[tuple[int, int], set] = {}
    for cx, cz in want:
        regions.setdefault((cx >> 5, cz >> 5), set()).add((cx & 31, cz & 31))
    files = {}
    for p in terrain.region_files(root, world, dimension):
        try:
            _r, rx, rz, _e = os.path.basename(p).split(".")
            files[(int(rx), int(rz))] = p
        except ValueError:
            continue
    chunks: dict[tuple[int, int], tuple] = {}
    for (rx, rz), locals_ in regions.items():
        p = files.get((rx, rz))
        if not p:
            continue
        for lx, lz, raw in terrain.iter_raw_chunks(p):
            if (lx, lz) not in locals_:
                continue
            try:
                sections, min_y, hm = terrain._fast_sections(raw)
            except (ValueError, IndexError, struct.error):
                continue
            motion = hm.get("MOTION_BLOCKING") or hm.get("WORLD_SURFACE")
            if not motion or not sections:
                continue
            bits = next((b for b in range(1, 17) if math.ceil(256 / (64 // b)) == len(motion)), 9)
            tops = [terrain._unpack(motion, bits, i) + min_y - 1 for i in range(256)]
            chunks[(rx * 32 + lx, rz * 32 + lz)] = (sections, min_y, tops)

    cache: dict[tuple[int, int, int], int | None] = {}

    def top(x: int, z: int) -> int | None:
        c = chunks.get((x >> 4, z >> 4))
        return None if c is None else c[2][(z & 15) * 16 + (x & 15)]

    def block(x: int, y: int, z: int) -> int | None:
        """Look index of the block there (None: air / not loaded)."""
        key = (x, y, z)
        if key in cache:
            return cache[key]
        c = chunks.get((x >> 4, z >> 4))
        b = terrain._block_at(c[0], x & 15, y, z & 15) if c and y >= c[1] else None
        v = None if (b is None or b in AIR) else look.info(b)
        cache[key] = v
        return v

    near2 = (radius + 8) ** 2
    pts = points[:: max(1, len(points) // 400)]
    faces = bytearray()
    xs = [cx * 16 for cx, _ in chunks] or [0]
    zs = [cz * 16 for _, cz in chunks] or [0]
    x0, z0 = min(xs), min(zs)
    pack = struct.Struct("<5h").pack
    for (cx, cz), (_secs, min_y, tops) in chunks.items():
        for lz in range(16):
            for lx in range(16):
                x, z = cx * 16 + lx, cz * 16 + lz
                if not any((x - px) ** 2 + (z - pz) ** 2 <= near2 for px, pz in pts):
                    continue
                h = tops[lz * 16 + lx]
                if h < min_y:
                    continue
                low = h
                for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    t = top(x + dx, z + dz)
                    if t is not None:
                        low = min(low, t)
                lo = max(min_y, max(h - 48, low - 1))
                for y in range(lo, h + 2):              # +1: plants and carpets above the heightmap
                    i = block(x, y, z)
                    if i is None:
                        continue
                    info = look.infos[i]
                    if info["kind"] == "cross":
                        faces += pack(x - x0, y, z - z0, 6, i)
                        continue
                    for d, (dx, dy, dz) in enumerate(DIRS):
                        nx, ny, nz = x + dx, y + dy, z + dz
                        nt = top(nx, nz)
                        if nt is None:
                            continue                     # the edge of what was read: hidden
                        if ny <= nt:
                            n = block(nx, ny, nz)
                            if n is not None:
                                ninfo = look.infos[n]
                                if ninfo["opaque"] or (n == i and info["kind"] in ("water", "cutout")):
                                    continue
                        faces += pack(x - x0, y, z - z0, d, i)
    return {"origin": [x0, z0], "infos": look.infos, "textures": look.textures, "faces": bytes(faces),
            "chunks": len(chunks)}
