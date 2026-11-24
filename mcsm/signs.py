"""Signs and their text, from a world's block entities (for the history viewer's real blocks).

* A sign's block entity (1.20+): ``front_text`` and ``back_text``, each ``{messages: [4 lines], color: "black",
  has_glowing_text: 0/1}``; a line is a text component: JSON text in a string (up to 1.21.4) or the component itself
  as NBT (1.21.5+: a string, a compound with "text" / "extra", or a list). Older worlds: ``Text1``..``Text4``,
  ``Color``, ``GlowingText`` (front only).
* The block says which sign it is: standing (``*_sign[rotation=0-15]``), on a wall (``*_wall_sign[facing=...]``),
  hanging (``*_hanging_sign[rotation=...]``) or hanging from a wall (``*_wall_hanging_sign[facing=...]``).
* The page writes the lines on the board where the game does; the block builder draws the board itself.

Read-only.
"""
from __future__ import annotations

import json

from . import nbt


def plain_text(c) -> str:
    """The plain text of a text component: a string (JSON text or plain), a compound ("text", "translate",
    "extra") or a list of components."""
    if isinstance(c, (bytes, bytearray)):
        c = c.decode("utf-8", "replace")
    if isinstance(c, str):
        s = c.strip()
        if s[:1] in ("{", "[", '"'):
            try:
                return plain_text(json.loads(s)) if not s.startswith('"') else json.loads(s)
            except ValueError:
                return c
        return c
    if isinstance(c, list):
        return "".join(plain_text(x) for x in c)
    if isinstance(c, dict):
        head = c.get("text")
        if head is None:
            head = c.get("") if isinstance(c.get(""), str) else ""
        if not head and isinstance(c.get("translate"), str):
            head = c.get("fallback") or ""
        return str(head) + "".join(plain_text(x) for x in c.get("extra") or [])
    return "" if c is None else str(c)


def _side(t) -> dict | None:
    if not isinstance(t, dict):
        return None
    lines = [plain_text(m) for m in (t.get("messages") or [])][:4]
    lines += [""] * (4 - len(lines))
    if not any(x.strip() for x in lines):
        return None
    return {"lines": lines, "color": str(t.get("color") or "black").split(":")[-1],
            "glow": bool(t.get("has_glowing_text"))}


def text_of(be: dict) -> dict:
    """``{"front": side | None, "back": side | None}`` of a sign block entity; a side is ``{lines, color, glow}``."""
    if "front_text" in be or "back_text" in be:
        return {"front": _side(be.get("front_text")), "back": _side(be.get("back_text"))}
    old = {"messages": [be.get(f"Text{i}", "") for i in range(1, 5)], "color": be.get("Color", "black"),
           "has_glowing_text": be.get("GlowingText", 0)}
    return {"front": _side(old), "back": None}


def signs_in_tile(root: str, world: str, dim: str, tx: int, tz: int, size: int = 128) -> list[dict]:
    """Every sign with text in the tile ``[tx*size, (tx+1)*size)`` x ``[tz*size, ...)``: ``{x, y, z, kind
    ("standing", "wall", "hanging", "wall_hanging"), wood, facing, rotation, front, back}``."""
    from . import blockmesh as bm
    from .containers import _chunk, _region
    out = []
    c0x, c0z, n = tx * size >> 4, tz * size >> 4, max(1, size >> 4)
    for cx in range(c0x, c0x + n):
        for cz in range(c0z, c0z + n):
            p = _region(root, world, dim, cx << 4, cz << 4)
            raw = _chunk(p, cx, cz) if p else None
            if raw is None or b"sign" not in raw:
                continue
            d = nbt.loads(raw)
            bes = [e for e in d.get("block_entities") or (d.get("Level") or {}).get("TileEntities") or []
                   if isinstance(e, dict) and str(e.get("id", "")).lower().endswith("sign")]
            if not bes:
                continue
            ch = bm.Chunk(raw)
            for e in bes:
                x, y, z = e.get("x"), e.get("y"), e.get("z")
                if not all(isinstance(v, int) for v in (x, y, z)):
                    continue
                state = ch.state(x & 15, y, z & 15) or ""
                name = state.split("[")[0].replace("minecraft:", "")
                if not name.endswith("sign"):
                    continue                            # gone from the block (a stale block entity)
                txt = text_of(e)
                if not txt["front"] and not txt["back"]:
                    continue
                props = dict(kv.split("=", 1) for kv in state[state.index("[") + 1:-1].split(",")) if "[" in state else {}
                for suffix, kind in (("_wall_hanging_sign", "wall_hanging"), ("_hanging_sign", "hanging"),
                                     ("_wall_sign", "wall"), ("_sign", "standing")):
                    if name.endswith(suffix):
                        wood = name[:-len(suffix)]
                        break
                out.append({"x": x, "y": y, "z": z, "kind": kind, "wood": wood,
                            "facing": props.get("facing", "north"), "rotation": int(props.get("rotation", "0") or 0),
                            **txt})
    return out
