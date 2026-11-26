"""The game's keyframed mob animations, read from the client jar: ``net/minecraft/client/animation/definitions/*``
(SnifferAnimation, ArmadilloAnimation, CamelAnimation, BreezeAnimation, WardenAnimation, FrogAnimation...). Each
class's static set-up builds its animations with ``AnimationDefinition.Builder.withLength(seconds).looping()
.addAnimation(part, new AnimationChannel(target, new Keyframe(time, KeyframeAnimations.degreeVec / posVec /
scaleVec(x, y, z), Interpolations.LINEAR / CATMULLROM)...))``; it's run here by :class:`mcsm.mobmodels.Interpreter`
taught those calls, so the history viewer can play the same keyframes as the game (KeyframeAnimations.animate).

Output: ``{"SnifferAnimation": {"SNIFFER_DIG": {"length": 8.0, "loop": False, "bones": {"head": [["rotation",
[[time, [x, y, z], "linear" | "catmullrom"], ...]], ...]}}}}``: rotations in radians, positions in model pixels (y
already turned the model's way, as posVec does), scales as the amount added to 1 (as scaleVec does)."""
from __future__ import annotations

import math
import zipfile

from .mobmodels import Interpreter, _Stop, _num

PKG = "net/minecraft/client/animation/definitions/"


class _Def:
    def __init__(self, length):
        self.length, self.loop, self.bones = length, False, {}


class _Channel:
    def __init__(self):
        self.target, self.keys = None, []


class _Key:
    def __init__(self):
        self.t, self.v, self.interp = 0.0, [0.0, 0.0, 0.0], "linear"


class _Vec:
    def __init__(self, v):
        self.v = v


class _AnimInterpreter(Interpreter):
    def native(self, owner, name, desc, args, static):
        simple = owner.rsplit("/", 1)[-1]
        this = None if static else args[0]
        a = args if static else args[1:]
        if simple == "AnimationDefinition$Builder":
            if name == "withLength":
                return _Def(float(_num(a[0], 0.0)))
            if isinstance(this, _Def):
                if name == "looping":
                    this.loop = True
                elif name == "addAnimation" and isinstance(a[0], str) and isinstance(a[1], _Channel):
                    this.bones.setdefault(a[0], []).append(a[1])
                return this
        if simple == "KeyframeAnimations" and name in ("degreeVec", "posVec", "scaleVec") and len(a) == 3:
            x, y, z = (float(_num(v, 0.0)) for v in a)
            if name == "degreeVec":
                return _Vec([math.radians(x), math.radians(y), math.radians(z)])
            if name == "posVec":
                return _Vec([x, -y, z])
            return _Vec([x - 1.0, y - 1.0, z - 1.0])
        if simple == "AnimationChannel" and name == "<init>" and isinstance(this, _Channel):
            this.target = a[0] if isinstance(a[0], str) else None
            this.keys = [k for k in (a[1] if isinstance(a[1], list) else []) if isinstance(k, _Key)]
            return None
        if simple == "Keyframe" and name == "<init>" and isinstance(this, _Key):
            this.t = float(_num(a[0], 0.0))
            this.v = a[1].v if isinstance(a[1], _Vec) else [0.0, 0.0, 0.0]
            this.interp = a[2] if isinstance(a[2], str) else "linear"
            return None
        if simple == "Vector3f" and name == "<init>" and isinstance(this, _Vec):
            this.v = [float(_num(v, 0.0)) for v in a[:3]] if len(a) >= 3 else [0.0, 0.0, 0.0]
            return None
        return super().native(owner, name, desc, args, static)

    def new(self, cname):
        simple = cname.rsplit("/", 1)[-1]
        if simple == "AnimationChannel":
            return _Channel()
        if simple == "Keyframe":
            return _Key()
        if simple == "Vector3f":
            return _Vec([0.0, 0.0, 0.0])
        return super().new(cname)

    def getstatic(self, owner, name):
        simple = owner.rsplit("/", 1)[-1]
        if simple == "AnimationChannel$Interpolations":
            return name.lower()                         # "linear" / "catmullrom"
        if simple == "AnimationChannel$Targets":
            return name.lower()                         # "rotation" / "position" / "scale"
        return super().getstatic(owner, name)


def read_animations(jar: str | None) -> dict:
    """Every animation class's keyframed animations (see the module's notes); {} when unreadable."""
    if not jar:
        return {}
    try:
        zf = zipfile.ZipFile(jar)
    except (OSError, zipfile.BadZipFile):
        return {}
    out: dict = {}
    with zf:
        it = _AnimInterpreter(zf)
        for n in sorted(zf.namelist()):
            if not (n.startswith(PKG) and n.endswith(".class")) or "$" in n:
                continue
            owner = n[:-len(".class")]
            c = it.cls(owner)
            m = c.methods.get(("<clinit>", "()V")) if c else None
            if m is None:
                continue
            it.budget = 5_000_000
            try:
                it.run(c, m, [])
            except (_Stop, IndexError, TypeError, ValueError, KeyError, AttributeError):
                pass
            anims = {}
            for (o, fname), v in it.statics.items():
                if o == owner and isinstance(v, _Def) and v.bones:
                    anims[fname] = {"length": round(v.length, 4), "loop": v.loop,
                                    "bones": {b: [[ch.target, [[round(k.t, 4), [round(x, 5) for x in k.v], k.interp]
                                                               for k in ch.keys]]
                                                  for ch in chs if ch.target] for b, chs in v.bones.items()}}
            if anims:
                out[owner.rsplit("/", 1)[-1]] = anims
    return out
