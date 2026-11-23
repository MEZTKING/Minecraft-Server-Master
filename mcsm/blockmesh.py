"""Real Minecraft blocks from a world backup, for the history viewer and the editor's map preview.

What is built: every block you could see from outside, not just the top layer. Starting from the open sky, the
air (and see-through blocks: glass, leaves, doors, slabs...) is followed in through doorways, windows, under
roofs and overhangs and into cave mouths, down to :data:`DEPTH` blocks under the surface; every block face that
air touches is kept. So houses are furnished inside, cliffs have their full height and nothing hangs in mid-air.

How a block looks comes from the game's own files in the Minecraft client jar already installed on this PC
(nothing is downloaded or bundled): the block's state (``facing``, ``half``, ``shape``...) picks its model in
the blockstate file (variants and multipart: fences, walls, panes), and every element of the model is turned
into textured quads (with the element's own rotation and the variant's x/y rotation). Block entities that have
no model in the jar get a simple stand-in: chests (with their real texture), beds, signs, shulker boxes.

Light is Minecraft's own: each face gets the sky light and block light (torches, lanterns...) stored in the
chunk for the space in front of it, and full cubes get the corner shading of smooth lighting (ambient
occlusion), so the page can light the world like the game does and let you change day/night and brightness.

Output (``build``): ``{"origin": [x0, z0], "infos": [...], "textures": [...], "faces": bytes}``. ``faces`` are
little-endian records ``int16 x - x0, y, z - z0, dir, info; uint16 extra``: dir 0-5 is one face of a full
block (up, down, north, south, west, east; ``extra`` = corner shading, 2 bits per corner, | block light << 8 |
sky light << 12), dir 7 a whole model (``extra`` = the sides hidden by neighbours, one bit per direction, |
light as before). ``infos[i]["quads"]``: ``[tex, cull, shade, x,y,z * 4, u,v * 4]`` in block units (0..1),
corners top-left, top-right, bottom-right, bottom-left as seen from outside.
"""

from __future__ import annotations

import glob
import json
import math
import os
import struct
import zipfile
from collections import deque

from . import terrain

VERSION = 4                     # bump when the output changes (cached tiles are built again)
DEPTH = 40                      # how far under a spot's surface open space is followed (caves, cellars)
MAX_OPEN = 1_500_000            # safety limit on the open cells followed per build
RECORD = struct.Struct("<5hHH")     # x, y, z, dir, look, light/shading, tint (RGB565)

AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:light", "minecraft:barrier",
       "minecraft:structure_void", "minecraft:moving_piston"}
DIRS = [(0, 1, 0), (0, -1, 0), (0, 0, -1), (0, 0, 1), (-1, 0, 0), (1, 0, 0)]
FACE_NAMES = ["up", "down", "north", "south", "west", "east"]
GRASS, FOLIAGE, WATER = (124, 189, 107), (89, 174, 48), (63, 118, 228)
# Tints now come from each spot's biome (``Biomes``) and are applied per face; kept for older callers.
TINTS: dict = {}
FIXED_TINTS = {"spruce_leaves": (97, 153, 97), "birch_leaves": (128, 167, 85), "mangrove_leaves": (146, 193, 98),
               "lily_pad": (32, 128, 48), "attached_melon_stem": (224, 199, 28), "attached_pumpkin_stem": (224, 199, 28),
               "melon_stem": (224, 199, 28), "pumpkin_stem": (224, 199, 28)}
GRASS_TINTED = {"grass_block", "short_grass", "grass", "tall_grass", "fern", "large_fern", "potted_fern", "sugar_cane",
                "bush"}


def tint_kind(name: str, props: dict) -> str | tuple | None:
    """What colours a block's tinted faces: "grass", "foliage", "water" (from the biome) or a fixed colour."""
    if name in ("water", "bubble_column"):
        return "water"
    if name in FIXED_TINTS:
        return FIXED_TINTS[name]
    if name == "redstone_wire":
        f = int(props.get("power", "0") or 0) / 15
        return (int(255 * (f * 0.6 + (0.4 if f > 0 else 0.3))), int(255 * max(0.0, f * f * 0.7 - 0.5)),
                int(255 * max(0.0, f * f * 0.6 - 0.7)))
    if "leaves" in name or name == "vine":
        return "foliage"
    return "grass"


def rgb565(c) -> int:
    return ((c[0] >> 3) << 11) | ((c[1] >> 2) << 5) | (c[2] >> 3)


def png_pixels(data: bytes) -> tuple[int, int, list[tuple[int, int, int]]]:
    """A small PNG reader (8-bit grey, RGB, RGBA or palette; not interlaced): ``(width, height, [rgb...])``."""
    import zlib
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG")
    pos, idat, plte = 8, bytearray(), b""
    w = h = ctype = 0
    while pos < len(data):
        n = struct.unpack_from(">I", data, pos)[0]
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, ctype, _c, _f, inter = struct.unpack(">IIBBBBB", body)
            if depth != 8 or inter:
                raise ValueError("unsupported PNG")
        elif kind == b"PLTE":
            plte = body
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(bytes(idat))
    stride = w * bpp
    prev = bytearray(stride)
    out = []
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], (prev[i - bpp] if i >= bpp else 0)
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        for x in range(w):
            px = line[x * bpp:(x + 1) * bpp]
            if ctype == 3:
                k = px[0] * 3
                out.append((plte[k], plte[k + 1], plte[k + 2]))
            elif ctype in (0, 4):
                out.append((px[0], px[0], px[0]))
            else:
                out.append((px[0], px[1], px[2]))
        prev = line
    return w, h, out


def _colour(v) -> tuple[int, int, int] | None:
    if isinstance(v, str) and v.startswith("#"):
        v = int(v[1:], 16)
    if isinstance(v, int):
        return ((v >> 16) & 255, (v >> 8) & 255, v & 255)
    return None


class Biomes:
    """Grass, foliage and water colours per biome, from the game's own biome files and colour maps (in the jar)."""

    def __init__(self, look: "Look"):
        self.look = look
        self._maps: dict[str, list | None] = {}
        self._cache: dict[tuple[str, str], tuple[int, int, int]] = {}

    def _map(self, name: str):
        if name not in self._maps:
            try:
                self._maps[name] = png_pixels(self.look.zf.read(f"assets/minecraft/textures/colormap/{name}.png"))[2]
            except (KeyError, ValueError, AttributeError, struct.error, IndexError):
                self._maps[name] = None
        return self._maps[name]

    def colour(self, biome: str | None, kind: str) -> tuple[int, int, int]:
        biome = (biome or "minecraft:plains").split(":", 1)[-1]
        key = (biome, kind)
        if key in self._cache:
            return self._cache[key]
        d = self.look._json(f"data/minecraft/worldgen/biome/{biome}.json") or {}
        eff = d.get("effects") or {}
        temp = max(0.0, min(1.0, float(d.get("temperature", 0.8))))
        down = max(0.0, min(1.0, float(d.get("downfall", 0.4)))) * temp

        def from_map(name, default):
            px = self._map(name)
            if not px:
                return default
            i, j = int((1 - temp) * 255), int((1 - down) * 255)
            k = j * 256 + i
            return px[k] if 0 <= k < len(px) else default
        if kind == "water":
            c = _colour(eff.get("water_color")) or WATER
        elif kind == "foliage":
            c = _colour(eff.get("foliage_color")) or from_map("foliage", FOLIAGE)
        else:
            c = _colour(eff.get("grass_color")) or from_map("grass", GRASS)
            mod = eff.get("grass_color_modifier")
            if mod == "swamp":
                c = (106, 112, 57)                         # the game's swamp grass (it also mixes in a darker one)
            elif mod == "dark_forest":
                c = tuple(((v & 0xFE) + b) >> 1 for v, b in zip(c, (0x28, 0x34, 0x0A)))
        self._cache[key] = c
        return c


# A face's corners (top-left, top-right, bottom-right, bottom-left seen from outside): per axis 0 = from, 1 = to
CORNERS = {"up": [(0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)], "down": [(0, 0, 1), (1, 0, 1), (1, 0, 0), (0, 0, 0)],
           "north": [(1, 1, 0), (0, 1, 0), (0, 0, 0), (1, 0, 0)], "south": [(0, 1, 1), (1, 1, 1), (1, 0, 1), (0, 0, 1)],
           "west": [(0, 1, 0), (0, 1, 1), (0, 0, 1), (0, 0, 0)], "east": [(1, 1, 1), (1, 1, 0), (1, 0, 0), (1, 0, 1)]}


def _default_uv(face: str, f, t) -> list[float]:
    x0, y0, z0 = f
    x1, y1, z1 = t
    return {"down": [x0, 16 - z1, x1, 16 - z0], "up": [x0, z0, x1, z1], "north": [16 - x1, 16 - y1, 16 - x0, 16 - y0],
            "south": [x0, 16 - y1, x1, 16 - y0], "west": [z0, 16 - y1, z1, 16 - y0],
            "east": [16 - z1, 16 - y1, 16 - z0, 16 - y0]}[face]


def _rotate(p, axis: str, deg: float, origin=(8.0, 8.0, 8.0), scale: float = 1.0):
    """Turn point *p* (block pixels) by *deg* about *axis* through *origin* (right-handed, like the game)."""
    a = math.radians(deg)
    c, s = round(math.cos(a), 9), round(math.sin(a), 9)
    x, y, z = p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]
    if axis == "x":
        y, z = y * c - z * s, y * s + z * c
        y, z = y * scale, z * scale
    elif axis == "y":
        x, z = x * c + z * s, -x * s + z * c
        x, z = x * scale, z * scale
    else:
        x, y = x * c - y * s, x * s + y * c
        x, y = x * scale, y * scale
    return (x + origin[0], y + origin[1], z + origin[2])


def _dir_of(v) -> int:
    """The nearest of the six directions to vector *v*."""
    best, bi = -2.0, 0
    for i, d in enumerate(DIRS):
        dot = v[0] * d[0] + v[1] * d[1] + v[2] * d[2]
        if dot > best:
            best, bi = dot, i
    return bi


def _full_face_dir(q: list) -> int | None:
    """The side of the block a quad covers completely (0-5), or None."""
    pts = [q[3 + k * 3: 6 + k * 3] for k in range(4)]
    for axis in range(3):
        vals = {round(p[axis], 4) for p in pts}
        if len(vals) != 1 or next(iter(vals)) not in (0.0, 1.0):
            continue
        others = [a for a in range(3) if a != axis]
        if all({round(p[a], 4) for p in pts} == {0.0, 1.0} for a in others):
            v = [0, 0, 0]
            v[axis] = 1 if next(iter(vals)) == 1.0 else -1
            return DIRS.index(tuple(v))
    return None


def _locked_uv(pts) -> list[tuple[float, float]] | None:
    """Texture corners lined up with the world for a turned, axis-aligned face (uvlock)."""
    ax, ay, az = (pts[1][i] - pts[0][i] for i in range(3))
    bx, by, bz = (pts[3][i] - pts[0][i] for i in range(3))
    n = (by * az - bz * ay, bz * ax - bx * az, bx * ay - by * ax)
    if abs(n[0]) + abs(n[1]) + abs(n[2]) < 1e-9:
        return None
    d = _dir_of(n)
    f = {0: lambda p: (p[0], p[2]), 1: lambda p: (p[0], 16 - p[2]), 2: lambda p: (16 - p[0], 16 - p[1]),
         3: lambda p: (p[0], 16 - p[1]), 4: lambda p: (p[2], 16 - p[1]), 5: lambda p: (16 - p[2], 16 - p[1])}[d]
    return [(f(p)[0] / 16, f(p)[1] / 16) for p in pts]


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


def parse_state(state: str) -> tuple[str, dict]:
    """``minecraft:oak_stairs[facing=east,half=top]`` -> ``("oak_stairs", {"facing": "east", "half": "top"})``."""
    name, _, rest = state.partition("[")
    props = {}
    if rest:
        for kv in rest.rstrip("]").split(","):
            k, _, v = kv.partition("=")
            if k:
                props[k] = v
    return name.split(":", 1)[-1], props


def _matches(when, props: dict) -> bool:
    if not when:
        return True
    if "OR" in when:
        return any(_matches(w, props) for w in when["OR"])
    if "AND" in when:
        return all(_matches(w, props) for w in when["AND"])
    for k, v in when.items():
        if str(props.get(k, "")) not in str(v).split("|"):
            return False
    return True


class Look:
    """How each block state looks (its quads, textures, kind), from a client jar."""

    def __init__(self, jar: str | None):
        self.zf = zipfile.ZipFile(jar) if jar else None
        self._models: dict[str, dict] = {}
        self.infos: list[dict] = []
        self.index: dict[str, int | None] = {}
        self.textures: list[str] = []
        self._tex_index: dict[str, int] = {}
        self._names = set(self.zf.namelist()) if self.zf else set()

    def _json(self, path: str) -> dict | None:
        if not self.zf:
            return None
        try:
            return json.loads(self.zf.read(path).decode("utf-8"))
        except (KeyError, ValueError):
            return None

    def has_texture(self, tex: str) -> bool:
        return f"assets/minecraft/textures/{tex}.png" in self._names

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

    # --- quads ---
    def _element_quads(self, els: list, textures: dict, x_rot: int = 0, y_rot: int = 0, tex_size=16,
                       overlay: bool = True, uvlock: bool = False) -> list[list]:
        """The quads of model elements, turned by the variant's x then y rotation. Each quad ends with its tint mode:
        0 none, 1 the whole face (grass, leaves, water), 2 only the overlay (a grass block's sides). With *uvlock*
        (stairs, fences, walls) a turned face keeps the texture lined up with the world, as in the game."""
        tw, th = (tex_size, tex_size) if isinstance(tex_size, (int, float)) else tex_size
        quads = []
        pairs = {}
        if overlay and len(els) == 2 and els[0].get("from") == els[1].get("from") and els[0].get("to") == els[1].get("to"):
            # grass block style: a tinted overlay drawn over the same box -> one texture "base+suffix"
            for f, fd in (els[1].get("faces") or {}).items():
                base = self._tex(textures, ((els[0].get("faces") or {}).get(f) or {}).get("texture"))
                over = self._tex(textures, fd.get("texture"))
                if base and over and over.startswith(base + "_"):
                    pairs[f] = f"{base}+{over[len(base) + 1:]}"
            els = els[:1]
        for e in els:
            f, t = e.get("from") or [0, 0, 0], e.get("to") or [16, 16, 16]
            rot = e.get("rotation") or {}
            for face, fd in (e.get("faces") or {}).items():
                if face not in CORNERS:
                    continue
                tex = pairs.get(face) or self._tex(textures, fd.get("texture")) or self._tex(textures, "#particle")
                if not tex:
                    continue
                uv = fd.get("uv") or _default_uv(face, f, t)
                pts = [tuple((t if c[i] else f)[i] for i in range(3)) for c in CORNERS[face]]
                if rot and rot.get("angle"):
                    ang = float(rot["angle"])
                    sc = 1.0
                    if rot.get("rescale"):
                        sc = 1.0 / math.cos(math.radians(abs(ang)))
                    pts = [_rotate(p, rot.get("axis", "y"), ang, tuple(rot.get("origin") or (8, 8, 8)), sc) for p in pts]
                if x_rot:
                    pts = [_rotate(p, "x", -x_rot) for p in pts]
                if y_rot:
                    pts = [_rotate(p, "y", -y_rot) for p in pts]
                cull = fd.get("cullface")
                cull_i = -1
                if cull in CORNERS:
                    v = DIRS[FACE_NAMES.index(cull)]
                    v = _rotate(v, "x", -x_rot, (0, 0, 0)) if x_rot else v
                    v = _rotate(v, "y", -y_rot, (0, 0, 0)) if y_rot else v
                    cull_i = _dir_of(v)
                u0, v0, u1, v1 = float(uv[0]) / tw, float(uv[1]) / th, float(uv[2]) / tw, float(uv[3]) / th
                corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
                r = (int(fd.get("rotation") or 0) // 90) % 4
                uvs = [corners[(k - r) % 4] for k in range(4)]
                if uvlock and (x_rot or y_rot) and not (rot and rot.get("angle")):
                    uvs = _locked_uv(pts) or uvs
                q = [self._tid(tex), cull_i, 0 if e.get("shade") is False else 1]
                for p in pts:
                    q += [round(p[0] / 16, 5), round(p[1] / 16, 5), round(p[2] / 16, 5)]
                for c in uvs:
                    q += [round(c[0], 5), round(c[1], 5)]
                q.append(2 if face in pairs else (1 if fd.get("tintindex") is not None else 0))
                quads.append(q)
        return quads

    def _special(self, name: str, props: dict) -> list | None:
        """Stand-ins for block entities whose look isn't a model in the jar:
        ``[elements, textures, (texture width, height), x rotation, y rotation]``."""
        facing_y = {"north": 0, "east": 90, "south": 180, "west": 270}.get(props.get("facing", "north"), 0)

        def box(f, t, faces, tex="#t"):
            return {"from": f, "to": t, "faces": {k: {"texture": tex, "uv": v} for k, v in faces.items()}}
        if name.endswith("_banner"):
            colour = name[: -len("_wall_banner")] if name.endswith("_wall_banner") else name[: -len("_banner")]
            wool = f"block/{colour}_wool" if self.has_texture(f"block/{colour}_wool") else "block/white_wool"
            six = {k: {"texture": "#w"} for k in FACE_NAMES}
            pole = {k: {"texture": "#p"} for k in FACE_NAMES}
            if "wall" in name:                         # hangs from the block behind it (model faces north)
                els = [{"from": [1, -14, 14.5], "to": [15, 14, 15.5], "faces": six},
                       {"from": [0, 13, 14], "to": [16, 15, 16], "faces": pole}]
            else:
                els = [{"from": [1, 2, 7.5], "to": [15, 30, 8.5], "faces": six},
                       {"from": [7, 0, 6.5], "to": [9, 30, 7.5], "faces": pole},
                       {"from": [0, 30, 6.5], "to": [16, 32, 8.5], "faces": pole}]
                rot = int(props.get("rotation", "0") or 0)
                facing_y = int(round(rot * 22.5 / 90.0)) * 90 % 360
            return [els, {"w": wool, "p": "block/oak_planks"}, (16, 16), 0, facing_y]
        if name.endswith("copper_chest"):
            stage = name.replace("waxed_", "").replace("copper_chest", "").rstrip("_")
            tex = "entity/chest/copper" + (f"_{stage}" if stage else "")
            if not self.has_texture(tex):
                tex = "entity/chest/copper"
            name = "chest"
        else:
            tex = None
        if name in ("chest", "trapped_chest", "ender_chest"):
            tex = tex or {"chest": "entity/chest/normal", "trapped_chest": "entity/chest/trapped",
                          "ender_chest": "entity/chest/ender"}[name]
            # The texture's regions (as the game's chest model maps them; the model is drawn mirrored, so a region the
            # model calls "east" shows on the west side): lid top, base bottom, front / back / sides of lid and base.
            # A double chest's halves use their own textures (15 wide, only the outer side drawn); left joins the
            # block to the east of a chest facing north.
            half = props.get("type", "single")
            if half in ("left", "right") and self.has_texture(f"{tex}_{half}"):
                tex = f"{tex}_{half}"
                if half == "left":
                    x0, x1, outer, ou, ob = 1, 16, "west", [29, 14, 43, 19], [29, 33, 43, 43]
                else:
                    x0, x1, outer, ou, ob = 0, 15, "east", [0, 14, 14, 19], [0, 33, 14, 43]
                lid = {"north": [14, 14, 29, 19], "south": [43, 14, 58, 19], "up": [29, 0, 44, 14], outer: ou}
                base = {"north": [14, 33, 29, 43], "south": [43, 33, 58, 43], "down": [14, 19, 29, 33], outer: ob}
                els = [box([x0, 0, 1], [x1, 10, 15], base), box([x0, 10, 1], [x1, 14, 15], lid)]
            else:
                els = [box([1, 0, 1], [15, 10, 15], {"north": [14, 33, 28, 43], "south": [42, 33, 56, 43],
                                                     "west": [28, 33, 42, 43], "east": [0, 33, 14, 43],
                                                     "down": [14, 19, 28, 33]}),
                       box([1, 10, 1], [15, 14, 15], {"north": [14, 14, 28, 19], "south": [42, 14, 56, 19],
                                                      "west": [28, 14, 42, 19], "east": [0, 14, 14, 19],
                                                      "up": [28, 0, 42, 14]}),
                       box([7, 7, 0], [9, 11, 1], {"north": [1, 1, 3, 5], "up": [1, 0, 3, 1], "west": [0, 1, 1, 5],
                                                   "east": [3, 1, 4, 5], "down": [3, 0, 5, 1]})]
            return [els, {"t": tex}, (64, 64), 0, facing_y]
        heads = {"skeleton": ("entity/skeleton/skeleton", 32), "wither_skeleton": ("entity/skeleton/wither_skeleton", 32),
                 "zombie": ("entity/zombie/zombie", 64), "creeper": ("entity/creeper/creeper", 32),
                 "piglin": ("entity/piglin/piglin", 64), "player": ("entity/player/wide/steve", 64)}
        kind = name.replace("_wall_skull", "").replace("_wall_head", "").replace("_skull", "").replace("_head", "")
        if kind in heads and name.endswith(("_skull", "_head")):
            tex, h = heads[kind]
            # the head's skin layout (like a player skin): front, back, sides, top, bottom; model faces south
            faces = {"south": [8, 8, 16, 16], "north": [24, 8, 32, 16], "west": [0, 8, 8, 16], "east": [16, 8, 24, 16],
                     "up": [8, 0, 16, 8], "down": [16, 0, 24, 8]}
            if "_wall_" in name:                        # against the block behind it: facing north = back to the south
                els = [box([4, 4, 8], [12, 12, 16], {**faces, "north": [8, 8, 16, 16], "south": [24, 8, 32, 16]})]
            else:
                els = [box([4, 0, 4], [12, 8, 12], faces)]
                rot = int(props.get("rotation", "0") or 0)
                facing_y = int(round(rot * 22.5 / 90.0)) * 90 % 360
            return [els, {"t": tex}, (64, h), 0, facing_y]
        if name == "decorated_pot":
            side = "entity/decorated_pot/decorated_pot_side"
            if not self.has_texture(side):
                return [[box([1, 0, 1], [15, 16, 15], {k: [0, 0, 16, 16] for k in FACE_NAMES})], {"t": "block/terracotta"},
                        (16, 16), 0, 0]
            body = {"from": [1, 0, 1], "to": [15, 16, 15], "faces": {k: {"texture": "#s", "uv": [1, 0, 15, 16]}
                                                                      for k in ("north", "south", "west", "east")}}
            body["faces"]["up"] = {"texture": "#b", "uv": [0, 0, 16, 16]}
            body["faces"]["down"] = {"texture": "#b", "uv": [0, 0, 16, 16]}
            return [[body], {"s": side, "b": "entity/decorated_pot/decorated_pot_base"}, (16, 16), 0, facing_y]
        if name.endswith("_bed"):
            colour = name[:-4]
            top = "block/white_wool" if props.get("part") == "head" else f"block/{colour}_wool"
            els = [{"from": [0, 3, 0], "to": [16, 9, 16], "faces": {
                "up": {"texture": "#top"}, "down": {"texture": "#bottom"}, "north": {"texture": "#side"},
                "south": {"texture": "#side"}, "west": {"texture": "#side"}, "east": {"texture": "#side"}}}]
            for lx, lz in ((0, 0), (13, 0), (0, 13), (13, 13)):
                els.append({"from": [lx, 0, lz], "to": [lx + 3, 3, lz + 3], "faces": {
                    k: {"texture": "#bottom"} for k in ("north", "south", "west", "east", "down")}})
            # the head's pillow end points the way the bed faces; model drawn facing north
            return [els, {"top": top, "side": f"block/{colour}_wool", "bottom": "block/oak_planks"}, (16, 16), 0, facing_y]
        if name.endswith(("_sign", "_hanging_sign")):
            wood = name
            for suf in ("_wall_hanging_sign", "_hanging_sign", "_wall_sign", "_sign"):
                if wood.endswith(suf):
                    wood = wood[: -len(suf)]
                    break
            planks = f"block/{wood}_planks" if self.has_texture(f"block/{wood}_planks") else "block/oak_planks"
            six = {k: {"texture": "#p"} for k in FACE_NAMES}
            if "hanging" in name:
                els = [{"from": [1, 0, 7], "to": [15, 10, 9], "faces": six}]
            elif "wall" in name:
                els = [{"from": [0, 4.5, 14], "to": [16, 12.5, 16], "faces": six}]
            else:
                els = [{"from": [0, 7, 7.25], "to": [16, 15, 8.75], "faces": six},
                       {"from": [7.25, 0, 7.25], "to": [8.75, 7, 8.75], "faces": six}]
                rot = int(props.get("rotation", "0") or 0)
                facing_y = int(round(rot * 22.5 / 90.0)) * 90 % 360
            return [els, {"p": planks}, (16, 16), 0, facing_y]
        if name.endswith("shulker_box"):
            tex = f"block/{name}" if self.has_texture(f"block/{name}") else "block/shulker_box"
            return [[{"from": [0, 0, 0], "to": [16, 16, 16], "faces": {k: {"texture": "#t"} for k in FACE_NAMES}}],
                    {"t": tex}, (16, 16), 0, 0]
        return None

    def info(self, state: str) -> int | None:
        """Index of the look of *state* (``minecraft:name[props]``) in :attr:`infos`; None = draw nothing."""
        if state in self.index:
            return self.index[state]
        name, props = parse_state(state)
        kind, quads = "model", []
        if name in ("water", "bubble_column", "lava"):
            tex = "block/lava_still" if name == "lava" else "block/water_still"
            tint = {} if name == "lava" else {"tintindex": 0}
            el = [{"from": [0, 0, 0], "to": [16, 14, 16], "faces": {k: {"texture": tex, "cullface": k, **tint}
                                                                   for k in FACE_NAMES}}]
            quads = self._element_quads(el, {})
            kind = "lava" if name == "lava" else "water"
        else:
            special = self._special(name, props)
            if special:
                els, texs, size, xr, yr = special
                quads = self._element_quads(els, texs, xr, yr, tex_size=size)
            else:
                bs = self._json(f"assets/minecraft/blockstates/{name}.json") or {}
                applies = []
                if bs.get("variants"):
                    for key, v in bs["variants"].items():
                        want = dict(kv.split("=", 1) for kv in key.split(",") if "=" in kv)
                        if all(props.get(k) == val for k, val in want.items()):
                            applies.append(v[0] if isinstance(v, list) else v)
                            break
                    if not applies:
                        v = next(iter(bs["variants"].values()))
                        applies.append(v[0] if isinstance(v, list) else v)
                elif bs.get("multipart"):
                    for part in bs["multipart"]:
                        if _matches(part.get("when"), props):
                            a = part.get("apply")
                            applies.append(a[0] if isinstance(a, list) else a or {})
                for a in applies:
                    m = self._model(a.get("model") or "")
                    if m["elements"]:
                        quads += self._element_quads(m["elements"], m["textures"], int(a.get("x") or 0),
                                                     int(a.get("y") or 0), uvlock=bool(a.get("uvlock")))
                if not applies and not bs:                    # no blockstate file (an unknown block): a plain cube
                    tex = f"block/{name}" if self.has_texture(f"block/{name}") else None
                    if tex:
                        quads = self._element_quads([{"from": [0, 0, 0], "to": [16, 16, 16], "faces": {
                            k: {"texture": tex, "cullface": k} for k in FACE_NAMES}}], {})
        if not quads:
            self.index[state] = None
            return None
        # a full cube: six whole faces, one on each side of the block (then culled by the neighbour on that side)
        dirs = [_full_face_dir(q) for q in quads] if len(quads) == 6 else []
        full = bool(dirs) and sorted(d for d in dirs if d is not None) == list(range(6))
        if full:
            for q, d in zip(quads, dirs):
                q[1] = d
        cutout = "leaves" in name or "glass" in name or name in (
            "ice", "slime_block", "honey_block", "spawner", "mangrove_roots", "cobweb", "trial_spawner", "vault")
        if kind in ("water", "lava") or (kind == "model" and full):
            kind = "cube" if kind == "model" else kind
            by_dir = [None] * 6
            for q in quads:
                by_dir[q[1]] = q
            quads = by_dir
        tinted = any(q and q[-1] for q in quads)
        info = {"name": name, "kind": kind, "quads": quads, "opaque": kind == "cube" and not cutout,
                "cutout": cutout, "water": props.get("waterlogged") == "true",
                "tint": tint_kind(name, props) if tinted else None}
        self.index[state] = len(self.infos)
        self.infos.append(info)
        return self.index[state]

    def texture_png(self, tex: str) -> bytes | None:
        if not self.zf:
            return None
        try:
            return self.zf.read(f"assets/minecraft/textures/{tex}.png")
        except KeyError:
            return None


# --- reading chunks with block states and light ---------------------------------------------------------

_U16, _I32 = struct.Struct(">H"), struct.Struct(">i")
_SEC = {b"Y": "val", b"block_states": {b"palette": "lazy", b"data": "lazy"}, b"BlockLight": "lazy", b"SkyLight": "lazy",
        b"biomes": {b"palette": "lazy", b"data": "lazy"}}
_OLD_SEC = {b"Y": "val", b"Palette": "lazy", b"BlockStates": "lazy", b"BlockLight": "lazy", b"SkyLight": "lazy"}
_ROOT = {b"yPos": "val", b"Heightmaps": terrain._HM_SPEC, b"sections": ("list", _SEC),
         b"Level": {b"Heightmaps": terrain._HM_SPEC, b"Sections": ("list", _OLD_SEC)}}


def _generic(b: bytes, pos: int) -> tuple[dict, int]:
    out = {}
    while True:
        t = b[pos]
        pos += 1
        if t == 0:
            return out, pos
        nl = _U16.unpack_from(b, pos)[0]
        name = b[pos + 2:pos + 2 + nl].decode("utf-8", "replace")
        pos += 2 + nl
        if t == 10:
            v, pos = _generic(b, pos)
        elif t in (1, 2, 3, 4, 8):
            v, pos = terrain._value(b, t, pos)
        else:
            v, pos = None, terrain._skip(b, t, pos)
        out[name] = v


def _state(entry) -> str:
    if isinstance(entry, str):
        return entry
    name = entry.get("Name") or entry.get("id") or entry.get("") or "minecraft:air"
    props = entry.get("Properties") or entry.get("properties") or {}
    if not isinstance(props, dict) or not props:
        return name
    return name + "[" + ",".join(f"{k}={props[k]}" for k in sorted(props)) + "]"


def _palette(b: bytes, t: int, pos: int) -> list[str]:
    if t != 9:
        return []
    et, n = b[pos], _I32.unpack_from(b, pos + 1)[0]
    pos += 5
    out = []
    for _ in range(max(0, n)):
        if et == 8:
            v, pos = terrain._value(b, 8, pos)
            out.append(v)
        elif et == 10:
            d, pos = _generic(b, pos)
            out.append(_state(d))
        else:
            pos = terrain._skip(b, et, pos)
    return out


def _byte_array(b: bytes, lazy) -> bytes | None:
    if not lazy or lazy[0] != 7:
        return None
    pos = lazy[1]
    n = _I32.unpack_from(b, pos)[0]
    return b[pos + 4:pos + 4 + n] if n == 2048 else None


class Chunk:
    """One chunk's block states (decoded a section at a time), heights and light."""

    def __init__(self, raw: bytes):
        if not raw or raw[0] != 10:
            raise ValueError("not an NBT compound")
        pos = 3 + _U16.unpack_from(raw, 1)[0]
        root, _ = terrain._compound(raw, pos, _ROOT)
        self.raw = raw
        if b"sections" in root:
            secs = [(s.get(b"Y", 0), (s.get(b"block_states") or {}).get(b"palette"),
                     (s.get(b"block_states") or {}).get(b"data"), s.get(b"BlockLight"), s.get(b"SkyLight"))
                    for s in root[b"sections"]]
            self._biome_lazy = {s.get(b"Y", 0): ((s.get(b"biomes") or {}).get(b"palette"), (s.get(b"biomes") or {}).get(b"data"))
                                for s in root[b"sections"] if s.get(b"biomes")}
            self.min_y = root.get(b"yPos", -4) * 16
            hm = root.get(b"Heightmaps") or {}
        else:
            level = root.get(b"Level") or {}
            secs = [(s.get(b"Y", 0), s.get(b"Palette"), s.get(b"BlockStates"), s.get(b"BlockLight"), s.get(b"SkyLight"))
                    for s in level.get(b"Sections") or []]
            self.min_y = 0
            hm = level.get(b"Heightmaps") or {}
            self._biome_lazy = {}
        self._lazy = {y: rest for y, *rest in secs}
        self._dec: dict[int, tuple | None] = {}
        self.lit = any(s[4] for s in secs)            # light worked out by the game (else: everything daylit)
        motion = hm.get(b"MOTION_BLOCKING") or hm.get(b"WORLD_SURFACE")
        if not motion:
            raise ValueError("no heightmap")
        bits = next((b for b in range(1, 17) if math.ceil(256 / (64 // b)) == len(motion)), 9)
        self.tops = [terrain._unpack(motion, bits, i) + self.min_y - 1 for i in range(256)]

    def section(self, sy: int):
        if sy in self._dec:
            return self._dec[sy]
        lz = self._lazy.get(sy)
        v = None
        if lz:
            pal_l, data_l, bl_l, sl_l = lz
            pal = _palette(self.raw, *pal_l) if pal_l else []
            longs = terrain._value(self.raw, data_l[0], data_l[1])[0] if data_l else []
            bits = max(4, math.ceil(math.log2(len(pal)))) if len(pal) > 1 else 0
            v = (pal, longs or [], bits, _byte_array(self.raw, bl_l), _byte_array(self.raw, sl_l))
        self._dec[sy] = v
        return v

    def biome(self, x: int, y: int, z: int) -> str | None:
        """The biome at x, y, z (chunk-local x/z; the game stores one per 4 x 4 x 4 blocks)."""
        sy = y >> 4
        dec = self._bio.get(sy) if hasattr(self, "_bio") else None
        if dec is None:
            if not hasattr(self, "_bio"):
                self._bio = {}
            lz = self._biome_lazy.get(sy)
            pal, longs = [], []
            if lz and lz[0]:
                pal = _palette(self.raw, *lz[0])
                longs = terrain._value(self.raw, lz[1][0], lz[1][1])[0] if lz[1] else []
            dec = self._bio[sy] = (pal, longs or [], math.ceil(math.log2(len(pal))) if len(pal) > 1 else 0)
        pal, longs, bits = dec
        if not pal:
            return None
        if len(pal) == 1 or not longs or not bits:
            return pal[0]
        idx = terrain._unpack(longs, bits, ((y >> 2) & 3) * 16 + (z >> 2) * 4 + (x >> 2))
        return pal[idx] if idx < len(pal) else pal[0]

    def state(self, x: int, y: int, z: int) -> str | None:
        s = self.section(y >> 4)
        if s is None or not s[0]:
            return None
        pal, longs, bits = s[0], s[1], s[2]
        if len(pal) == 1 or not longs:
            return pal[0]
        idx = terrain._unpack(longs, bits, ((y & 15) * 256) + z * 16 + x)
        return pal[idx] if idx < len(pal) else None

    def light(self, x: int, y: int, z: int) -> tuple[int, int]:
        """(sky, block) light of the space at x, y, z (chunk-local x/z)."""
        if not self.lit:
            return 15, 0
        s = self.section(y >> 4)
        i = ((y & 15) * 256) + z * 16 + x
        if s is None:
            return (15 if y > self.tops[z * 16 + x] else 0), 0
        bl, sl = s[3], s[4]
        blk = ((bl[i >> 1] >> (4 * (i & 1))) & 15) if bl else 0
        if sl:
            sky = (sl[i >> 1] >> (4 * (i & 1))) & 15
        else:
            sky = 15 if y > self.tops[z * 16 + x] else 0
        return sky, blk


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
          max_chunks: int = 900, dimension: str = "minecraft:overworld",
          only: set[tuple[int, int]] | None = None) -> dict:
    """The visible block faces within *radius* of the (x, z) *points* (the camera path). With *only* (a set of
    chunk coordinates), exactly the blocks of those chunks; the chunks around them are read too, so faces at the
    edge are hidden or shown correctly (pieces built side by side fit together)."""
    if only is not None:
        core = set(only)
        want = {(cx + dx, cz + dz) for cx, cz in only for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
        max_chunks = max(max_chunks, len(want))
    else:
        core = _chunks_near(points, radius)
        if len(core) > max_chunks:                 # a long flight: keep the chunks nearest to most points
            centre = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
            core = set(sorted(core, key=lambda c: (c[0] * 16 - centre[0]) ** 2 + (c[1] * 16 - centre[1]) ** 2)[:max_chunks])
        want = {(cx + dx, cz + dz) for cx, cz in core for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
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
    chunks: dict[tuple[int, int], Chunk] = {}
    for (rx, rz), locals_ in regions.items():
        p = files.get((rx, rz))
        if not p:
            continue
        for lx, lz, raw in terrain.iter_raw_chunks(p):
            if (lx, lz) not in locals_:
                continue
            try:
                chunks[(rx * 32 + lx, rz * 32 + lz)] = Chunk(raw)
            except (ValueError, IndexError, struct.error, KeyError, TypeError):
                continue

    infos = look.infos
    cache: dict[tuple[int, int, int], int | None] = {}

    def top(x: int, z: int) -> int | None:
        c = chunks.get((x >> 4, z >> 4))
        return None if c is None else c.tops[(z & 15) * 16 + (x & 15)]

    def block(x: int, y: int, z: int) -> int | None:
        """Look index of the block there (None: air, nothing to draw, or not loaded)."""
        key = (x, y, z)
        try:
            return cache[key]
        except KeyError:
            pass
        c = chunks.get((x >> 4, z >> 4))
        s = c.state(x & 15, y, z & 15) if c is not None and y >= c.min_y else None
        v = None if (s is None or s.partition("[")[0] in AIR) else look.info(s)
        cache[key] = v
        return v

    def opaque(x: int, y: int, z: int) -> bool:
        i = block(x, y, z)
        return i is not None and infos[i]["opaque"]

    def passable(x: int, y: int, z: int) -> bool:
        i = block(x, y, z)
        if i is None:
            return True
        info = infos[i]
        return not info["opaque"] and info["kind"] not in ("water", "lava")

    # 1. the open space you can see into: from the sky in through gaps, down to DEPTH under each spot's surface
    core_cols = [(cx * 16 + lx, cz * 16 + lz) for cx, cz in core if (cx, cz) in chunks for lx in range(16) for lz in range(16)]
    if only is None:
        near2 = (radius + 8) ** 2
        pts = points[:: max(1, len(points) // 400)]
        core_cols = [(x, z) for x, z in core_cols if any((x - px) ** 2 + (z - pz) ** 2 <= near2 for px, pz in pts)]
    xs = [c[0] for c in chunks] or [0]
    zs = [c[1] for c in chunks] or [0]
    bx0, bx1 = min(xs) * 16, (max(xs) + 1) * 16                # follow open space over all the chunks read
    bz0, bz1 = min(zs) * 16, (max(zs) + 1) * 16
    seen: set[tuple[int, int, int]] = set()
    todo = deque()
    ring_cols = [(cx * 16 + lx, cz * 16 + lz) for cx, cz in chunks for lx in range(16) for lz in range(16)]
    for x, z in ring_cols:
        h = top(x, z)
        nb = [top(x + 1, z), top(x - 1, z), top(x, z + 1), top(x, z - 1)]
        low = min([t for t in nb if t is not None] + [h])
        c = chunks[(x >> 4, z >> 4)]
        for y in range(max(c.min_y, low + 1), h + 1):         # next to open sky: the top, and walls above neighbours
            if passable(x, y, z) and (x, y, z) not in seen:
                seen.add((x, y, z))
                todo.append((x, y, z))
    while todo and len(seen) < MAX_OPEN:
        x, y, z = todo.popleft()
        for dx, dy, dz in DIRS:
            nx, ny, nz = x + dx, y + dy, z + dz
            if not (bx0 <= nx < bx1 and bz0 <= nz < bz1) or (nx, ny, nz) in seen:
                continue
            t = top(nx, nz)
            if t is None or ny > t or ny < t - DEPTH:
                continue
            c = chunks[(nx >> 4, nz >> 4)]
            if ny < c.min_y or not passable(nx, ny, nz):
                continue
            seen.add((nx, ny, nz))
            todo.append((nx, ny, nz))

    def visible(x: int, y: int, z: int) -> bool:
        t = top(x, z)
        return t is not None and (y > t or (x, y, z) in seen)

    biomes = Biomes(look)
    tint_cache: dict[tuple, int] = {}

    def biome_at(x: int, y: int, z: int) -> str | None:
        c = chunks.get((x >> 4, z >> 4))
        return c.biome(x & 15, y, z & 15) if c is not None and y >= c.min_y else None

    def tint(x: int, y: int, z: int, kind) -> int:
        """The colour (RGB565) of a tinted face at x, y, z: the biome's, blended over the cells around (like the game)."""
        if kind is None:
            return 0
        if isinstance(kind, tuple):
            return rgb565(kind)
        key = (x >> 2, y >> 2, z >> 2, kind)
        v = tint_cache.get(key)
        if v is None:
            acc = [0, 0, 0]
            n = 0
            for dx in (-4, 0, 4):
                for dz in (-4, 0, 4):
                    b = biome_at((x & ~3) + dx, y, (z & ~3) + dz)
                    if b is None and (dx or dz):
                        continue
                    c = biomes.colour(b, kind)
                    acc = [acc[0] + c[0], acc[1] + c[1], acc[2] + c[2]]
                    n += 1
            v = tint_cache[key] = rgb565(tuple(a // max(1, n) for a in acc))
        return v

    def light(x: int, y: int, z: int) -> int:
        c = chunks.get((x >> 4, z >> 4))
        if c is None:
            return 15 << 4
        sky, blk = c.light(x & 15, y, z & 15)
        return (sky << 4) | blk

    # 2. the blocks that open space touches, in the wanted area
    core_set = set(core_cols)
    cand: set[tuple[int, int, int]] = set()
    for x, z in core_cols:
        h = top(x, z)
        nb = [top(x + 1, z), top(x - 1, z), top(x, z + 1), top(x, z - 1)]
        low = min([t for t in nb if t is not None] + [h])
        c = chunks[(x >> 4, z >> 4)]
        for y in range(max(c.min_y, low), h + 3):
            cand.add((x, y, z))
    for x, y, z in seen:
        if (x, z) in core_set:
            cand.add((x, y, z))
        for dx, dy, dz in DIRS:
            if (x + dx, z + dz) in core_set:
                cand.add((x + dx, y + dy, z + dz))

    xs = [x for x, _ in core_cols] or [0]
    zs = [z for _, z in core_cols] or [0]
    x0, z0 = (min(xs) >> 4) * 16, (min(zs) >> 4) * 16
    faces = bytearray()
    pack = RECORD.pack
    for x, y, z in sorted(cand):
        i = block(x, y, z)
        if i is None:
            continue
        info = infos[i]
        kind = info["kind"]
        if kind == "model":
            if not visible(x, y, z):
                continue
            mask = 0
            for d, (dx, dy, dz) in enumerate(DIRS):
                if opaque(x + dx, y + dy, z + dz):
                    mask |= 1 << d
            lt = max(light(x, y, z), light(x, y + 1, z), key=lambda v: (v >> 4) + (v & 15))
            faces += pack(x - x0, y, z - z0, 7, i, mask | (lt << 8), tint(x, y, z, info["tint"]))
            if info["water"]:                               # waterlogged: the water around it too
                w = look.info("minecraft:water")
                for d, (dx, dy, dz) in enumerate(DIRS):
                    nx, ny, nz = x + dx, y + dy, z + dz
                    if not visible(nx, ny, nz):
                        continue
                    n = block(nx, ny, nz)
                    if n is not None and (infos[n]["opaque"] or infos[n]["kind"] == "water" or infos[n]["water"]):
                        continue
                    faces += pack(x - x0, y, z - z0, d, w, light(nx, ny, nz) << 8, tint(x, y, z, "water"))
            continue
        for d, (dx, dy, dz) in enumerate(DIRS):
            nx, ny, nz = x + dx, y + dy, z + dz
            if not visible(nx, ny, nz):
                continue
            n = block(nx, ny, nz)
            if n is not None:
                ninfo = infos[n]
                if ninfo["opaque"] or n == i or (kind in ("water", "lava") and ninfo["kind"] == kind) \
                        or (kind == "water" and ninfo["water"]):
                    continue
            if info["quads"][d] is None:
                continue
            ao = 0
            if kind == "cube" and not info["cutout"]:
                face = FACE_NAMES[d]
                axes = [a for a in range(3) if DIRS[d][a] == 0]
                for k, corner in enumerate(CORNERS[face]):
                    s = [0, 0, 0]
                    for a in axes:
                        s[a] = 1 if corner[a] else -1
                    s1 = [0, 0, 0]
                    s1[axes[0]] = s[axes[0]]
                    s2 = [0, 0, 0]
                    s2[axes[1]] = s[axes[1]]
                    o1 = opaque(nx + s1[0], ny + s1[1], nz + s1[2])
                    o2 = opaque(nx + s2[0], ny + s2[1], nz + s2[2])
                    oc = opaque(nx + s[0], ny + s[1], nz + s[2])
                    occ = 3 if (o1 and o2) else (o1 + o2 + oc)
                    ao |= occ << (2 * k)
            faces += pack(x - x0, y, z - z0, d, i, ao | (light(nx, ny, nz) << 8), tint(x, y, z, info["tint"]))
    return {"origin": [x0, z0], "infos": infos, "textures": look.textures, "faces": bytes(faces),
            "chunks": len(chunks), "open": len(seen)}


def client_infos(infos: list[dict]) -> list[dict]:
    """What the page needs of each look."""
    return [{"name": i["name"], "kind": i["kind"], "quads": i["quads"], "cutout": i["cutout"]} for i in infos]


def texture_sizes(look: "Look", textures: list[str]) -> dict:
    """Width and height of textures that aren't 16 x 16 square tiles (entity textures), for the page's atlas."""
    out = {}
    for t in textures:
        if t.startswith("entity/"):
            data = look.texture_png(t.split("+")[0])
            if data and len(data) > 24:
                out[t] = list(struct.unpack(">II", data[16:24]))
    return out
