"""Mob models (boxes, texture offsets, pivots) read from the installed Minecraft client jar.

Minecraft's entity models are Java code, not assets: ``LayerDefinitions.createRoots()`` builds every model layer
(``ZOMBIE``, ``CHICKEN``, ``COD``...) from ``CubeListBuilder.texOffs(u, v).addBox(x, y, z, w, h, d)`` and
``PartPose.offset(...)`` calls. Since 26.1 the client jar isn't obfuscated, so the class and method names are
readable. This module runs that code with a small JVM bytecode interpreter (standard library only) that knows the
model builder classes and treats everything else as unknown. Nothing of the game is bundled with the app: the
models are read from the jar on this PC and cached as JSON.

Output per layer: ``{"tw": texture width, "th": height, "scale": s, "root": part}``; a part is
``{"pose": [x, y, z, xRot, yRot, zRot, xScale, yScale, zScale], "cubes": [[u, v, x, y, z, w, h, d, grow_x, grow_y,
grow_z, mirror], ...], "children": {name: part}}`` in Minecraft's model space (pixels, y down, the front is -z).
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
import struct
import zipfile

VERSION = 3                      # bump when the output changes (3: static tables, guardian spikes)
_PKG = "net/minecraft/client/model/"
_ROOTS = "net/minecraft/client/model/geom/LayerDefinitions"


# ---------------------------------------------------------------- class files
class _Class:
    def __init__(self, data: bytes):
        self.cp: list = [None]
        k = 10
        n = struct.unpack_from(">H", data, 8)[0]
        i = 1
        while i < n:
            tag = data[k]
            k += 1
            if tag == 1:
                ln = struct.unpack_from(">H", data, k)[0]
                self.cp.append(data[k + 2:k + 2 + ln].decode("utf-8", "replace"))
                k += 2 + ln
            elif tag in (3, 4):
                raw = data[k:k + 4]
                self.cp.append(("I", struct.unpack(">i", raw)[0]) if tag == 3 else ("F", struct.unpack(">f", raw)[0]))
                k += 4
            elif tag in (5, 6):
                raw = data[k:k + 8]
                self.cp.append(("J", struct.unpack(">q", raw)[0]) if tag == 5 else ("D", struct.unpack(">d", raw)[0]))
                self.cp.append(None)
                k += 8
                i += 1
            elif tag in (7, 8, 16, 19, 20):
                self.cp.append((tag, struct.unpack_from(">H", data, k)[0]))
                k += 2
            elif tag in (9, 10, 11, 12, 17, 18):
                self.cp.append((tag,) + struct.unpack_from(">HH", data, k))
                k += 4
            elif tag == 15:
                self.cp.append((tag, data[k], struct.unpack_from(">H", data, k + 1)[0]))
                k += 3
            else:
                raise ValueError(f"constant tag {tag}")
            i += 1
        k += 2
        self.name = self._class_name(struct.unpack_from(">H", data, k)[0])
        sup = struct.unpack_from(">H", data, k + 2)[0]
        self.super = self._class_name(sup) if sup else None
        k += 4
        ni = struct.unpack_from(">H", data, k)[0]
        k += 2 + 2 * ni
        nf = struct.unpack_from(">H", data, k)[0]
        k += 2
        for _ in range(nf):
            k = self._skip_member(data, k)
        nm = struct.unpack_from(">H", data, k)[0]
        k += 2
        self.methods: dict[tuple[str, str], tuple[int, bytes]] = {}
        for _ in range(nm):
            _acc, name_i, desc_i, na = struct.unpack_from(">HHHH", data, k)
            k += 8
            for _a in range(na):
                an, ln = struct.unpack_from(">HI", data, k)
                if self.cp[an] == "Code":
                    max_locals = struct.unpack_from(">H", data, k + 8)[0]
                    clen = struct.unpack_from(">I", data, k + 10)[0]
                    self.methods[(self.cp[name_i], self.cp[desc_i])] = (max_locals, data[k + 14:k + 14 + clen])
                k += 6 + ln
        self.bootstrap: list[tuple[int, list[int]]] = []   # (method handle, static arguments) per invokedynamic site
        for _ in range(struct.unpack_from(">H", data, k)[0]):
            an, ln = struct.unpack_from(">HI", data, k + 2)
            if self.cp[an] == "BootstrapMethods":
                j = k + 8
                for _b in range(struct.unpack_from(">H", data, j)[0]):
                    ref, na = struct.unpack_from(">HH", data, j + 2)
                    self.bootstrap.append((ref, list(struct.unpack_from(f">{na}H", data, j + 6))))
                    j += 4 + 2 * na
            k += 6 + ln

    @staticmethod
    def _skip_member(data: bytes, k: int) -> int:
        na = struct.unpack_from(">H", data, k + 6)[0]
        k += 8
        for _ in range(na):
            k += 6 + struct.unpack_from(">I", data, k + 2)[0]
        return k

    def _class_name(self, i: int) -> str:
        return self.cp[self.cp[i][1]]

    def utf(self, i: int) -> str:
        return self.cp[i]

    def ref(self, i: int) -> tuple[str, str, str]:
        """Field/method ref -> (class, name, descriptor)."""
        e = self.cp[i]
        nt = self.cp[e[2]]
        return self._class_name(e[1]), self.cp[nt[1]], self.cp[nt[2]]

    def ldc(self, i: int):
        e = self.cp[i]
        if isinstance(e, tuple) and e[0] in ("I", "F", "J", "D"):
            return _Wide(e[1]) if e[0] in ("J", "D") else e[1]
        if isinstance(e, tuple) and e[0] == 8:
            return self.cp[e[1]]
        return _UNK


def _args(desc: str) -> tuple[list[str], str]:
    out, k = [], 1
    while desc[k] != ")":
        j = k
        while desc[j] == "[":
            j += 1
        if desc[j] == "L":
            j = desc.index(";", j)
        out.append(desc[k:j + 1])
        k = j + 1
    return out, desc[k + 1:]


# ---------------------------------------------------------------- values
class _Unknown:
    def __repr__(self):
        return "?"


_UNK = _Unknown()


class _Wide:                     # a long or double (two stack slots in the JVM)
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v


class _Sym:                      # a ModelLayers field: the layer's name
    def __init__(self, name):
        self.name = name


class CD:
    def __init__(self, x=0.0, y=None, z=None):
        self.g = [x, x if y is None else y, x if z is None else z]


class Pose:
    def __init__(self, v=None):
        self.v = list(v) if v else [0, 0, 0, 0, 0, 0, 1, 1, 1]


class Cubes:
    def __init__(self):
        self.u = self.v = 0
        self.mirror = False
        self.cubes: list[list] = []


class Part:
    def __init__(self, cubes=None, pose=None):
        self.cubes = list(cubes or [])
        self.pose = pose or Pose()
        self.children: dict[str, Part] = {}

    def json(self) -> dict:
        return {"pose": [round(x, 5) for x in self.pose.v], "cubes": self.cubes,
                "children": {k: c.json() for k, c in self.children.items()}}


class Mesh:
    def __init__(self):
        self.root = Part()


class Layer:
    def __init__(self, mesh, tw, th):
        self.mesh, self.tw, self.th, self.scale = mesh, tw, th, 1.0


class _MapBuilder:
    def __init__(self):
        self.items: dict = {}


class _Scaling:
    def __init__(self, f):
        self.f = f


def _num(x, default=0.0):
    if isinstance(x, _Wide):
        x = x.v
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else default


# ---------------------------------------------------------------- the interpreter
class _Stop(Exception):
    pass


class Interpreter:
    def __init__(self, jar: zipfile.ZipFile):
        self.jar = jar
        self.classes: dict[str, _Class | None] = {}
        self.budget = 5_000_000
        self.maps: list[_MapBuilder] = []             # every map built (the layers' one, even if the run stops)
        self.statics: dict[tuple[str, str], object] = {}   # model classes' static fields (their tables of angles)
        self.inited: set[str] = set()

    def cls(self, name: str) -> _Class | None:
        if name not in self.classes:
            try:
                self.classes[name] = _Class(self.jar.read(name + ".class"))
            except (KeyError, ValueError, struct.error, IndexError):
                self.classes[name] = None
        return self.classes[name]

    def find(self, cname: str, mname: str, desc: str):
        c = self.cls(cname)
        while c is not None:
            if (mname, desc) in c.methods:
                return c, c.methods[(mname, desc)]
            c = self.cls(c.super) if c.super and c.super.startswith("net/minecraft/") else None
        return None, None

    # -- calls the interpreter knows itself (the model builder classes, maths)
    def native(self, owner: str, name: str, desc: str, args: list, static: bool):
        simple = owner.rsplit("/", 1)[-1]
        this = None if static else args[0]
        a = args if static else args[1:]
        if simple in ("Mth", "Math", "StrictMath"):
            f = {"sin": math.sin, "cos": math.cos, "sqrt": math.sqrt, "abs": abs, "toRadians": math.radians,
                 "atan2": math.atan2, "floor": math.floor}.get(name)
            if f and all(isinstance(_num(x, None), (int, float)) for x in a):
                v = f(*[_num(x) for x in a])
                return _Wide(v) if desc.endswith(")D") or desc.endswith(")J") else v
            return _UNK
        if simple == "CubeListBuilder" and name == "create":
            return Cubes()
        if isinstance(this, Cubes):
            if name == "texOffs":
                this.u, this.v = int(_num(a[0])), int(_num(a[1]))
            elif name == "mirror":
                this.mirror = bool(a[0]) if a else True
            elif name == "addBox":
                types, _r = _args(desc)
                vals = list(a)
                u, v, mirror, g = this.u, this.v, this.mirror, [0.0, 0.0, 0.0]
                if types and types[0] == "Ljava/lang/String;":
                    types, vals = types[1:], vals[1:]
                nums = [_num(x) for t, x in zip(types, vals) if t in ("F", "I")]
                for t, x in zip(types, vals):
                    if t == "Z":
                        mirror = bool(x)
                    elif isinstance(x, CD):
                        g = list(x.g)
                if desc.startswith("(Ljava/lang/String;") and len(nums) >= 8:
                    u, v = int(nums[6]), int(nums[7])
                x, y, z, w, h, d = nums[:6]
                this.cubes.append([u, v, x, y, z, w, h, d, g[0], g[1], g[2], mirror])
            return this
        if simple == "PartPose":
            if name in ("offset", "rotation", "offsetAndRotation"):
                n = [_num(x) for x in a]
                if name == "offset":
                    return Pose(n + [0, 0, 0, 1, 1, 1])
                if name == "rotation":
                    return Pose([0, 0, 0] + n + [1, 1, 1])
                return Pose(n + [1, 1, 1])
            if name == "<init>" and isinstance(this, Pose):
                this.v = [_num(x) for x in a]
                return None
            if isinstance(this, Pose):
                v = list(this.v)
                if name == "translated":
                    v[0] += _num(a[0]); v[1] += _num(a[1]); v[2] += _num(a[2])
                elif name == "withScale":
                    v[6] = v[7] = v[8] = _num(a[0])
                elif name == "scaled":
                    s = [_num(x) for x in a] if len(a) == 3 else [_num(a[0])] * 3
                    v[6] *= s[0]; v[7] *= s[1]; v[8] *= s[2]
                else:
                    return _UNK
                return Pose(v)
            return _UNK
        if simple == "CubeDeformation":
            if name == "<init>" and isinstance(this, CD):
                this.g = [_num(a[0])] * 3 if len(a) == 1 else [_num(x) for x in a]
                return None
            if name == "extend" and isinstance(this, CD):
                e = [_num(a[0])] * 3 if len(a) == 1 else [_num(x) for x in a]
                return CD(*[p + q for p, q in zip(this.g, e)])
            return _UNK
        if isinstance(this, Part):
            if name == "addOrReplaceChild":
                if len(a) == 3:
                    cubes = a[1].cubes if isinstance(a[1], Cubes) else []
                    p = Part(cubes, a[2] if isinstance(a[2], Pose) else Pose())
                else:
                    p = a[1] if isinstance(a[1], Part) else Part()
                if isinstance(a[0], str):
                    this.children[a[0]] = p
                return p
            if name == "getChild":
                return this.children.get(a[0]) or Part()
            if name == "clearChild":
                if isinstance(a[0], str) and a[0] in this.children:
                    this.children[a[0]] = Part(pose=this.children[a[0]].pose)
                return this
            if name == "clearRecursively":
                def clear(p):
                    p.cubes = []
                    for c in p.children.values():
                        clear(c)
                clear(this)
                return this
            return this if _r_is(desc, "PartDefinition") else (_UNK if not desc.endswith(")V") else None)
        if simple == "MeshDefinition":
            if name == "<init>":
                return None
            if isinstance(this, Mesh):
                if name == "getRoot":
                    return this.root
                return this                                   # transformed / apply: babies and the like
            return _UNK
        if simple == "LayerDefinition":
            if name == "create" and isinstance(a[0], Mesh):
                return Layer(a[0], int(_num(a[1])), int(_num(a[2])))
            if name == "apply" and isinstance(this, Layer):
                if isinstance(a[0], _Scaling):
                    out = Layer(this.mesh, this.tw, this.th)
                    out.scale = this.scale * a[0].f
                    return out
                return this
            return _UNK
        if simple == "MeshTransformer" and name == "scaling":
            return _Scaling(_num(a[0], 1.0))
        if simple == "ImmutableMap" and name == "builder":
            self.maps.append(_MapBuilder())
            return self.maps[-1]
        if isinstance(this, _MapBuilder):
            if name == "put" and isinstance(a[0], _Sym):
                this.items[a[0].name] = a[1]
                return this
            if name in ("build", "buildOrThrow", "buildKeepingLast"):
                return this
            return this
        return NotImplemented

    def invoke(self, owner, name, desc, args, static, depth):
        r = self.native(owner, name, desc, args, static)
        if r is not NotImplemented:
            return r
        if static and owner.startswith(_PKG) and depth < 40:
            c, m = self.find(owner, name, desc)
            if m is not None:
                try:
                    return self.run(c, m, args, depth + 1)
                except _Stop:
                    return _UNK
        return None if desc.endswith(")V") else _UNK

    def getstatic(self, owner, name):
        simple = owner.rsplit("/", 1)[-1]
        if simple == "ModelLayers":                       # a layer's name, not its value
            return _Sym(name)
        if owner.startswith(_PKG) and owner not in self.inited:   # run the class's static set-up once
            self.inited.add(owner)
            c = self.cls(owner)
            m = c.methods.get(("<clinit>", "()V")) if c else None
            if m is not None:
                try:
                    self.run(c, m, [])
                except _Stop:
                    pass
        if (owner, name) in self.statics and not isinstance(self.statics[(owner, name)], _Unknown):
            return self.statics[(owner, name)]
        if simple == "CubeDeformation" and name == "NONE":
            return CD()
        if simple == "PartPose" and name == "ZERO":
            return Pose()
        if simple == "MeshTransformer" and name == "IDENTITY":
            return _Scaling(1.0)
        return _UNK

    def new(self, cname):
        simple = cname.rsplit("/", 1)[-1]
        return {"CubeDeformation": CD, "MeshDefinition": Mesh, "PartPose": Pose,
                "CubeListBuilder": Cubes}.get(simple, lambda: _UNK)()

    def run(self, c: _Class, method, args: list, depth: int = 0):
        max_locals, code = method
        loc: list = [None] * max(max_locals, len(args) * 2 + 2)
        j = 0
        for a in args:                                      # longs and doubles take two local slots
            loc[j] = a
            j += 2 if isinstance(a, _Wide) else 1
        st: list = []
        pc = 0
        n = len(code)
        while pc < n:
            self.budget -= 1
            if self.budget <= 0:
                raise _Stop()
            op = code[pc]
            if op == 0x00:                                  # nop
                pc += 1
            elif op == 0x01:
                st.append(None); pc += 1
            elif 0x02 <= op <= 0x08:
                st.append(op - 3); pc += 1
            elif op in (0x09, 0x0A):
                st.append(_Wide(op - 9)); pc += 1
            elif 0x0B <= op <= 0x0D:
                st.append(float(op - 0x0B)); pc += 1
            elif op in (0x0E, 0x0F):
                st.append(_Wide(float(op - 0x0E))); pc += 1
            elif op == 0x10:
                st.append(struct.unpack_from(">b", code, pc + 1)[0]); pc += 2
            elif op == 0x11:
                st.append(struct.unpack_from(">h", code, pc + 1)[0]); pc += 3
            elif op == 0x12:
                st.append(c.ldc(code[pc + 1])); pc += 2
            elif op in (0x13, 0x14):
                st.append(c.ldc(struct.unpack_from(">H", code, pc + 1)[0])); pc += 3
            elif 0x15 <= op <= 0x19:                        # xload index
                st.append(loc[code[pc + 1]]); pc += 2
            elif 0x1A <= op <= 0x2D:                        # xload_n
                st.append(loc[(op - 0x1A) % 4]); pc += 1
            elif 0x2E <= op <= 0x35:                        # xaload
                i, arr = st.pop(), st.pop()
                st.append(arr[i] if isinstance(arr, list) and isinstance(i, int) and 0 <= i < len(arr) else _UNK)
                pc += 1
            elif 0x36 <= op <= 0x3A:                        # xstore index
                loc[code[pc + 1]] = st.pop(); pc += 2
            elif 0x3B <= op <= 0x4E:                        # xstore_n
                loc[(op - 0x3B) % 4] = st.pop(); pc += 1
            elif 0x4F <= op <= 0x56:                        # xastore
                v, i, arr = st.pop(), st.pop(), st.pop()
                if isinstance(arr, list) and isinstance(i, int) and 0 <= i < len(arr):
                    arr[i] = v
                pc += 1
            elif op == 0x57:
                st.pop(); pc += 1
            elif op == 0x58:
                if isinstance(st[-1], _Wide):
                    st.pop()
                else:
                    st.pop(); st.pop()
                pc += 1
            elif op == 0x59:
                st.append(st[-1]); pc += 1
            elif op == 0x5A:
                st.insert(-2, st[-1]); pc += 1
            elif op == 0x5B:
                st.insert(-3, st[-1]); pc += 1
            elif op == 0x5C:
                if isinstance(st[-1], _Wide):
                    st.append(st[-1])
                else:
                    st.extend(st[-2:])
                pc += 1
            elif op in (0x5D, 0x5E):
                if not isinstance(st[-1], _Wide):
                    raise _Stop()                           # dup2_x1/x2 of two single values: not in model code
                st.insert(-2 if op == 0x5D else -3, st[-1])
                pc += 1
            elif op == 0x5F:
                st[-1], st[-2] = st[-2], st[-1]; pc += 1
            elif 0x60 <= op <= 0x83:                        # arithmetic
                kind = (op - 0x60) % 4                      # 0 int, 1 long, 2 float, 3 double
                group = (op - 0x60) // 4                    # add sub mul div rem neg shl shr ushr and or xor
                if group == 5:
                    x = st.pop()
                    xv = _num(x, None)
                    r = _UNK if xv is None else -xv
                else:
                    y, x = st.pop(), st.pop()
                    xv, yv = _num(x, None), _num(y, None)
                    if xv is None or yv is None:
                        r = _UNK
                    elif group <= 4:
                        try:
                            if group == 0:
                                r = xv + yv
                            elif group == 1:
                                r = xv - yv
                            elif group == 2:
                                r = xv * yv
                            elif group == 3:
                                r = (int(xv / yv) if kind in (0, 1) else xv / yv)
                            else:
                                r = math.fmod(xv, yv) if kind >= 2 else int(math.fmod(xv, yv))
                        except ZeroDivisionError:
                            r = _UNK
                    else:                                    # shifts and bit operations: ints only
                        xi, yi = int(xv), int(yv)
                        bits = 64 if kind == 1 else 32
                        r = {6: xi << (yi & (bits - 1)), 7: xi >> (yi & (bits - 1)),
                             8: (xi % (1 << bits)) >> (yi & (bits - 1)), 9: xi & yi, 10: xi | yi, 11: xi ^ yi}.get(group, _UNK)
                if kind in (0, 1) and isinstance(r, (int, float)) and not isinstance(r, bool):
                    bits = 64 if kind == 1 else 32
                    r = ((int(r) + (1 << (bits - 1))) % (1 << bits)) - (1 << (bits - 1))
                if kind == 2 and isinstance(r, (int, float)):
                    r = struct.unpack(">f", struct.pack(">f", r))[0] if math.isfinite(r) else r
                st.append(_Wide(r) if kind in (1, 3) and r is not _UNK else r)
                pc += 1
            elif op == 0x84:                                # iinc
                v = loc[code[pc + 1]]
                loc[code[pc + 1]] = (v + struct.unpack_from(">b", code, pc + 2)[0]) if isinstance(v, int) else _UNK
                pc += 3
            elif 0x85 <= op <= 0x93:                        # conversions
                x = _num(st.pop(), None)
                to = {0x85: "J", 0x86: "F", 0x87: "D", 0x88: "I", 0x89: "F", 0x8A: "D", 0x8B: "I", 0x8C: "J",
                      0x8D: "D", 0x8E: "I", 0x8F: "J", 0x90: "F", 0x91: "I", 0x92: "I", 0x93: "I"}[op]
                if x is None:
                    st.append(_UNK)
                else:
                    if to in ("I", "J"):
                        x = int(x) if math.isfinite(x) else 0
                    else:
                        x = float(x)
                    st.append(_Wide(x) if to in ("J", "D") else x)
                pc += 1
            elif 0x94 <= op <= 0x98:                        # lcmp fcmpl fcmpg dcmpl dcmpg
                y, x = _num(st.pop(), None), _num(st.pop(), None)
                st.append(0 if x is None or y is None else (x > y) - (x < y)); pc += 1
            elif 0x99 <= op <= 0xA6:                        # conditional branches
                off = struct.unpack_from(">h", code, pc + 1)[0]
                if op <= 0x9E:
                    x = st.pop()
                    xv = _num(x, 0) if not isinstance(x, _Unknown) else 0
                    t = [xv == 0, xv != 0, xv < 0, xv >= 0, xv > 0, xv <= 0][op - 0x99]
                elif op <= 0xA4:
                    y, x = _num(st.pop(), 0), _num(st.pop(), 0)
                    t = [x == y, x != y, x < y, x >= y, x > y, x <= y][op - 0x9F]
                else:
                    y, x = st.pop(), st.pop()
                    t = (x is y) if op == 0xA5 else (x is not y)
                pc = pc + off if t else pc + 3
            elif op == 0xA7:
                pc += struct.unpack_from(">h", code, pc + 1)[0]
            elif op == 0xC8:
                pc += struct.unpack_from(">i", code, pc + 1)[0]
            elif op == 0xC4:                                # wide: a local index above 255
                op2, idx = code[pc + 1], struct.unpack_from(">H", code, pc + 2)[0]
                if 0x15 <= op2 <= 0x19:
                    st.append(loc[idx]); pc += 4
                elif 0x36 <= op2 <= 0x3A:
                    loc[idx] = st.pop(); pc += 4
                elif op2 == 0x84:
                    v = loc[idx]
                    loc[idx] = (v + struct.unpack_from(">h", code, pc + 4)[0]) if isinstance(v, int) else _UNK
                    pc += 6
                else:
                    raise _Stop()
            elif op in (0xAA, 0xAB, 0xA8, 0xA9, 0xBF, 0xC2, 0xC3, 0xC5):
                raise _Stop()                               # switches, jsr, throw, monitors, wide: not in model code
            elif 0xAC <= op <= 0xB0:
                return st.pop()
            elif op == 0xB1:
                return None
            elif op == 0xB2:                                # getstatic
                st.append(self.getstatic(*c.ref(struct.unpack_from(">H", code, pc + 1)[0])[:2])); pc += 3
            elif op == 0xB3:                                # putstatic
                owner, fname, _d = c.ref(struct.unpack_from(">H", code, pc + 1)[0])
                self.statics[(owner, fname)] = st.pop(); pc += 3
            elif op == 0xB4:
                st.pop(); st.append(_UNK); pc += 3
            elif op == 0xB5:
                st.pop(); st.pop(); pc += 3
            elif 0xB6 <= op <= 0xB9:                        # invokes
                owner, name, desc = c.ref(struct.unpack_from(">H", code, pc + 1)[0])
                types, ret = _args(desc)
                static = op == 0xB8
                k = len(types) + (0 if static else 1)
                args = st[len(st) - k:] if k else []
                del st[len(st) - k:]
                r = self.invoke(owner, name, desc, args, static, depth)
                if ret != "V":
                    st.append(r if r is not None else _UNK)
                pc += 5 if op == 0xB9 else 3
            elif op == 0xBA:                                # invokedynamic: lambdas and string joins
                e = c.cp[struct.unpack_from(">H", code, pc + 1)[0]]
                desc = c.utf(c.cp[e[2]][2])
                types, ret = _args(desc)
                vals = st[len(st) - len(types):] if types else []
                if types:
                    del st[len(st) - len(types):]
                if ret != "V":
                    st.append(_concat(c, e[1], vals))
                pc += 5
            elif op == 0xBB:                                # new
                st.append(self.new(c._class_name(struct.unpack_from(">H", code, pc + 1)[0]))); pc += 3
            elif op == 0xBC:                                # newarray
                cnt = st.pop()
                st.append([0] * cnt if isinstance(cnt, int) and 0 <= cnt < 10_000 else _UNK); pc += 2
            elif op == 0xBD:
                cnt = st.pop()
                st.append([None] * cnt if isinstance(cnt, int) and 0 <= cnt < 10_000 else _UNK); pc += 3
            elif op == 0xBE:
                arr = st.pop()
                st.append(len(arr) if isinstance(arr, list) else 0); pc += 1
            elif op == 0xC0:
                pc += 3                                     # checkcast
            elif op == 0xC1:
                st.pop(); st.append(0); pc += 3
            elif op in (0xC6, 0xC7):
                x = st.pop()
                t = (x is None) if op == 0xC6 else (x is not None)
                pc = pc + struct.unpack_from(">h", code, pc + 1)[0] if t else pc + 3
            else:
                raise _Stop()
        return None


def _concat(c: _Class, bsm: int, vals: list):
    """``"tentacle" + i`` (StringConcatFactory.makeConcatWithConstants); anything else is unknown."""
    try:
        ref, sargs = c.bootstrap[bsm]
        owner, name, _d = c.ref(c.cp[ref][2])
        if name != "makeConcatWithConstants" or not sargs:
            return _UNK
        recipe = c.ldc(sargs[0])
        consts = [c.ldc(x) for x in sargs[1:]]
    except (IndexError, TypeError, KeyError):
        return _UNK
    if not isinstance(recipe, str):
        return _UNK
    out, vi, ci = [], 0, 0
    for ch in recipe:
        if ch == "":
            v = vals[vi] if vi < len(vals) else _UNK
            vi += 1
            if isinstance(v, _Wide):
                v = v.v
            if isinstance(v, (_Unknown, type(None))) or not isinstance(v, (str, int, float)):
                return _UNK
            out.append(str(v))
        elif ch == "":
            out.append(str(consts[ci]) if ci < len(consts) else "")
            ci += 1
        else:
            out.append(ch)
    return "".join(out)


def _r_is(desc: str, simple: str) -> bool:
    return desc.rsplit(")", 1)[-1].rstrip(";").endswith("/" + simple)


# ---------------------------------------------------------------- jars and the cache
def readable_jars() -> list[str]:
    """Client jars on this PC whose model code is readable (not obfuscated: 26.1 and later), newest first."""
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    cands = []
    for base in (os.path.join(appdata, "PrismLauncher"), os.path.join(appdata, "MultiMC"),
                 os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "PrismLauncher")):
        cands += glob.glob(os.path.join(base, "libraries", "com", "mojang", "minecraft", "*", "minecraft-*-client.jar"))
    cands += glob.glob(os.path.join(appdata, ".minecraft", "versions", "*", "*.jar"))
    out = []
    for c in sorted(set(cands), key=_version_key, reverse=True):
        try:
            with zipfile.ZipFile(c) as z:
                z.getinfo(_ROOTS + ".class")
            out.append(c)
        except (KeyError, OSError, zipfile.BadZipFile):
            continue
    return out


def _version_key(path: str):
    m = re.search(r"(\d+(?:\.\d+)*)", os.path.basename(path).replace("minecraft-", ""))
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def extract(jar_path: str) -> dict:
    """``{"version": ..., "layers": {"ZOMBIE": {...}, ...}}`` from a readable client jar."""
    with zipfile.ZipFile(jar_path) as z:
        it = Interpreter(z)
        c = it.cls(_ROOTS)
        if c is None:
            return {"layers": {}}
        m = c.methods.get(("createRoots", "()Ljava/util/Map;"))
        if m is None:
            return {"layers": {}}
        try:
            it.run(c, m, [])
        except _Stop:
            pass                                      # e.g. the check for missing layers at the end
        found = {name: lay for built in it.maps for name, lay in built.items.items() if isinstance(lay, Layer)}
        _pose_at_rest(it, found)
    layers = {name: {"tw": lay.tw, "th": lay.th, "scale": round(lay.scale, 5), "root": lay.mesh.root.json()}
              for name, lay in found.items()}
    return {"jar": os.path.basename(jar_path), "layers": layers}


# Parts the game only places in its animation code, set here as they are at rest.
_SPIKES = "net/minecraft/client/model/monster/guardian/GuardianModel"


def _pose_at_rest(it: Interpreter, found: dict) -> None:
    """Guardian spikes: positions from GuardianModel.getSpikeX/Y/Z(i, age 0, spikes out = 0.55)."""
    c = it.cls(_SPIKES)
    if c is None:
        return
    for name in ("GUARDIAN", "ELDER_GUARDIAN"):
        lay = found.get(name)
        head = lay.mesh.root.children.get("head") if isinstance(lay, Layer) else None
        if head is None:
            continue
        for i in range(12):
            spike = head.children.get(f"spike{i}")
            if spike is None:
                continue
            xyz = []
            for axis in "XYZ":
                m = c.methods.get((f"getSpike{axis}", "(IFF)F"))
                try:
                    v = it.run(c, m, [i, 0.0, 0.55]) if m else None
                except _Stop:
                    v = None
                xyz.append(v if isinstance(v, float) else None)
            if None not in xyz:
                spike.pose.v[0:3] = xyz


def models(cache_dir: str | None = None) -> dict:
    """The mob models from the newest readable jar on this PC (cached); ``{"layers": {}}`` when there is none."""
    jars = readable_jars()
    if not jars:
        return {"layers": {}, "jar": None}
    jar = jars[0]
    key = f"{os.path.basename(jar)}-{int(os.path.getmtime(jar))}-v{VERSION}"
    path = os.path.join(cache_dir, f"mobmodels-{key}.json") if cache_dir else None
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    out = extract(jar)
    out["jar_path"] = jar
    if path:
        os.makedirs(cache_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh, separators=(",", ":"))
        os.replace(tmp, path)
    return out


def default_cache_dir() -> str:
    from . import paths
    return os.path.join(os.path.dirname(paths.default_cache_dir()), "mobs")


_MODELS: dict = {}


def cached_models() -> dict:
    """``models()`` for the default cache, kept in memory."""
    if "m" not in _MODELS:
        _MODELS["m"] = models(default_cache_dir())
    return _MODELS["m"]


def texture(name: str) -> str | None:
    """A mob texture (``entity/zombie/zombie``) from the same jar as the models, as a cached file."""
    if not name.startswith("entity/") or ".." in name or not all(c.isalnum() or c in "_/-" for c in name):
        return None
    jar = cached_models().get("jar_path")
    if not jar:
        return None
    out = os.path.join(default_cache_dir(), "textures", os.path.splitext(os.path.basename(jar))[0], *name.split("/")) + ".png"
    if not os.path.isfile(out):
        try:
            with zipfile.ZipFile(jar) as z:
                data = z.read(f"assets/minecraft/textures/{name}.png")
        except (KeyError, OSError, zipfile.BadZipFile):
            return None
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out + ".part", "wb") as fh:
            fh.write(data)
        os.replace(out + ".part", out)
    return out
