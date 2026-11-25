"""The game's potion effects as the newest readable client jar defines them: each one's colour (its swirls' colour)
and category (beneficial, harmful, neutral), read from ``net/minecraft/world/effect/MobEffects`` (its static
initialiser: ``register("speed", new MobEffect(MobEffectCategory.BENEFICIAL, 0x33EBFF))``...). The history viewer
uses them to tell which effects a player has from their effect swirls' colours (the recordings don't keep the
effects themselves) and where the game puts each icon."""
from __future__ import annotations

import struct
import zipfile

from .mobmodels import _Class

# bytes each JVM instruction takes after its opcode (the variable-length ones are handled apart)
_OPERANDS = {**{op: 0 for op in range(0x00, 0x10)}, 0x10: 1, 0x11: 2, 0x12: 1, 0x13: 2, 0x14: 2,
             **{op: 1 for op in range(0x15, 0x1a)}, **{op: 0 for op in range(0x1a, 0x36)},
             **{op: 1 for op in range(0x36, 0x3b)}, **{op: 0 for op in range(0x3b, 0x84)}, 0x84: 2,
             **{op: 0 for op in range(0x85, 0x99)}, **{op: 2 for op in range(0x99, 0xa9)}, 0xa9: 1,
             **{op: 0 for op in range(0xac, 0xb2)}, **{op: 2 for op in range(0xb2, 0xb9)}, 0xb9: 4, 0xba: 4,
             0xbb: 2, 0xbc: 1, 0xbd: 2, 0xbe: 0, 0xbf: 0, 0xc0: 2, 0xc1: 2, 0xc2: 0, 0xc3: 0, 0xc5: 3,
             0xc6: 2, 0xc7: 2, 0xc8: 4, 0xc9: 4}


def read_effects(jar: str) -> dict[str, dict]:
    """``{name: {"color": 0xRRGGBB, "category": "beneficial" | "harmful" | "neutral"}}``; {} when unreadable."""
    try:
        with zipfile.ZipFile(jar) as zf:
            data = zf.read("net/minecraft/world/effect/MobEffects.class")
    except (OSError, KeyError, zipfile.BadZipFile):
        return {}
    try:
        c = _Class(data)
    except (ValueError, IndexError, struct.error):
        return {}
    code = next((v[1] for (n, _d), v in c.methods.items() if n == "<clinit>"), None)
    if code is None:
        return {}
    out: dict[str, dict] = {}
    name = cat = color = None
    i = 0
    while i < len(code):
        op = code[i]
        if op in (0x12, 0x13):                          # ldc / ldc_w: a name or a colour
            idx = code[i + 1] if op == 0x12 else struct.unpack_from(">H", code, i + 1)[0]
            v = c.ldc(idx)
            if isinstance(v, str) and v.replace("_", "").isalnum() and v.islower():
                name, cat, color = v, None, None
            elif isinstance(v, int) and cat is not None and color is None:
                color = v
        elif op == 0x11 and cat is not None and color is None:     # sipush
            color = struct.unpack_from(">h", code, i + 1)[0]
        elif op == 0x10 and cat is not None and color is None:     # bipush
            color = struct.unpack_from(">b", code, i + 1)[0]
        elif op == 0xb2:                                # getstatic: the category
            try:
                cls, fname, _ = c.ref(struct.unpack_from(">H", code, i + 1)[0])
            except (IndexError, TypeError):
                cls, fname = "", ""
            if cls.endswith("MobEffectCategory"):
                cat = fname.lower()
        elif op == 0xb8:                                # invokestatic: register(...) ends one
            try:
                _cls, mname, _ = c.ref(struct.unpack_from(">H", code, i + 1)[0])
            except (IndexError, TypeError):
                mname = ""
            if mname == "register" and name and cat and color is not None:
                out[name] = {"color": color & 0xFFFFFF, "category": cat}
                name = cat = color = None
        if op == 0xaa:                                  # tableswitch / lookupswitch (not expected here)
            return out
        if op == 0xab:
            return out
        if op == 0xc4:                                  # wide
            i += 6 if code[i + 1] == 0x84 else 4
            continue
        n = _OPERANDS.get(op)
        if n is None:
            return out
        i += 1 + n
    return out
