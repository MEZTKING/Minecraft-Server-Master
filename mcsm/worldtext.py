"""Real in-world text in a Flashback recording (a copy of it).

Minecraft has *text display* entities: text that exists in the world, is lit by it and is hidden
behind blocks. This module writes such entities into the **snapshot** at the start of every
chunk file (``cN.flashback``) of a *copy* of a recording, so the text is there wherever you are in
the replay. Open the copy in Flashback (the original edit is copied to it) and export again: the
text is part of the world in the video.

Packet layouts were checked byte for byte against packets encoded by the game itself
(Minecraft 26.1.2 / 26.2 / 26.3 server jars, ``ClientboundAddEntityPacket`` and
``ClientboundSetEntityDataPacket`` stream codecs, 2026-11-22):

* add entity: ``VarInt id, UUID, VarInt type, f64 x, y, z, LpVec3 velocity (0 = one zero byte),
  angle pitch, angle yaw, angle head yaw, VarInt data``
* set entity data: ``VarInt id``, then ``(u8 index, VarInt serializer, value)`` ... ``0xFF``.
  Display: 12 scale (Vector3f, serializer 39), 15 billboard (byte), 16 brightness (VarInt),
  17 view range (float). Text display: 23 text (component as network NBT), 24 line width,
  25 background ARGB, 27 flags (1 shadow, 2 see-through, 4 default background, 8 left, 16 right).

Recording layout (see :mod:`mcsm.paths`): a chunk file is ``magic, VarInt n, n action names, u32
snapshot size, snapshot, records``; snapshot and records are ``VarInt action, u32 size, payload``;
a ``flashback:action/game_packet`` payload is ``VarInt packet id + packet``.
"""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import struct
import uuid as uuid_mod
import zipfile
from dataclasses import dataclass

# protocol -> (add_entity packet id, set_entity_data packet id, text_display entity type id)
# From the game's own data reports (java net.minecraft.data.Main --reports) for each version.
PROTOCOLS = {
    775: (1, 99, 131),     # 26.1 / 26.1.1 / 26.1.2
    776: (1, 99, 132),     # 26.2
    777: (1, 101, 135),    # 26.3
}
# remove_entities packet id per protocol (same data reports; also seen in real recordings)
REMOVE_ENTITIES = {775: 77, 776: 77, 777: 78}
GAME_PACKET = "flashback:action/game_packet"
NEXT_TICK = "flashback:action/next_tick"
FLASHBACK_MAGIC = 0xD780E884

SER_BYTE, SER_INT, SER_FLOAT, SER_COMPONENT, SER_VECTOR3 = 0, 1, 3, 5, 39
BILLBOARD = {"fixed": 0, "vertical": 1, "horizontal": 2, "center": 3}
COLOURS = ("black", "dark_blue", "dark_green", "dark_aqua", "dark_red", "dark_purple", "gold", "gray",
           "dark_gray", "blue", "green", "aqua", "red", "light_purple", "yellow", "white")
LINE_HEIGHT = 0.25          # blocks per text line at scale 1 (9 font pixels + spacing, ~1/40 block each)


@dataclass
class WorldText:
    text: str
    x: float
    y: float
    z: float
    size: float = 3.0               # letter height in blocks
    colour: str = "white"           # a Minecraft colour name or #RRGGBB
    bold: bool = False
    facing: str = "center"          # center (always faces the camera), vertical, fixed
    yaw: float = 0.0                # for "fixed": which way the text faces
    shadow: bool = True
    background: int = 0             # ARGB; 0 = none
    glow: bool = True               # full brightness (readable at night)
    view_range: float = 8.0         # x64 blocks
    line_width: int = 100000        # pixels before wrapping (large = never wrap)
    outline: str = ""               # border colour (e.g. "black"); empty = no border
    outline_width: float = 0.0      # border thickness in blocks (0 = automatic, about 1/14 of the size)
    camera_yaw: float | None = None  # where it's seen from (needed for the border)
    appear_tick: int | None = None   # replay tick it appears at (None: there from the start)
    disappear_tick: int | None = None  # replay tick it goes away at (None: stays)


# --- encoding -----------------------------------------------------------------------------

def _varint(v: int) -> bytes:
    v &= 0xFFFFFFFF
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        out.append(b | (0x80 if v else 0))
        if not v:
            return bytes(out)


def _read_varint(b: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        if not c & 0x80:
            return n, i
        shift += 7


def _nbt_str(s: str) -> bytes:
    raw = s.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def text_component(text: str, colour: str = "white", bold: bool = False) -> bytes:
    """A text component as network NBT (nameless root): a plain string tag when unstyled,
    else a compound — exactly as the game writes it."""
    if (not colour or colour == "white") and not bold:
        return b"\x08" + _nbt_str(text)
    out = b"\x0a"
    if colour and colour != "white":
        out += b"\x08" + _nbt_str("color") + _nbt_str(colour)
    out += b"\x08" + _nbt_str("text") + _nbt_str(text)
    if bold:
        out += b"\x01" + _nbt_str("bold") + b"\x01"
    return out + b"\x00"


def _angle(deg: float) -> bytes:
    return bytes([int(math.floor(deg * 256.0 / 360.0)) & 0xFF])


def add_entity_packet(entity_id: int, uid: uuid_mod.UUID, type_id: int, x: float, y: float, z: float,
                      yaw: float = 0.0, pitch: float = 0.0) -> bytes:
    return (_varint(entity_id) + uid.bytes + _varint(type_id) + struct.pack(">ddd", x, y, z)
            + b"\x00"                                   # velocity: LpVec3 zero
            + _angle(pitch) + _angle(yaw) + b"\x00" + _varint(0))


def text_data_packet(entity_id: int, t: WorldText) -> bytes:
    scale = max(0.01, t.size / LINE_HEIGHT)
    flags = 0x01 if t.shadow else 0
    out = _varint(entity_id)
    out += bytes([12]) + _varint(SER_VECTOR3) + struct.pack(">fff", scale, scale, scale)
    out += bytes([15]) + _varint(SER_BYTE) + bytes([BILLBOARD.get(t.facing, 3)])
    if t.glow:
        out += bytes([16]) + _varint(SER_INT) + _varint(15 << 4 | 15 << 20)
    out += bytes([17]) + _varint(SER_FLOAT) + struct.pack(">f", t.view_range)
    out += bytes([23]) + _varint(SER_COMPONENT) + text_component(t.text, t.colour, t.bold)
    out += bytes([24]) + _varint(SER_INT) + _varint(t.line_width)
    out += bytes([25]) + _varint(SER_INT) + _varint(t.background)
    out += bytes([27]) + _varint(SER_BYTE) + bytes([flags])
    return out + b"\xff"


# --- writing into a recording -------------------------------------------------------------

def _record(action: int, payload: bytes) -> bytes:
    return _varint(action) + struct.pack(">I", len(payload)) + payload


def _header(chunk: bytes) -> tuple[list[str], int]:
    """Action names and the offset of the snapshot size field of a ``cN.flashback`` file."""
    if struct.unpack(">I", chunk[:4])[0] != FLASHBACK_MAGIC:
        raise ValueError("not a Flashback chunk file")
    i = 4
    n, i = _read_varint(chunk, i)
    names = []
    for _ in range(n):
        ln, i = _read_varint(chunk, i)
        names.append(chunk[i:i + ln].decode("utf-8"))
        i += ln
    return names, i


def add_timed(chunk: bytes, snapshot: list[bytes], at_tick: dict[int, list[bytes]]) -> bytes:
    """Add game packets to one chunk file: *snapshot* ones to its snapshot (there when playback
    starts or jumps into this chunk), and *at_tick* ``{tick in this chunk: packets}`` into the
    record stream right after that many ``next_tick`` actions (so they happen at that moment)."""
    names, i = _header(chunk)
    if GAME_PACKET not in names:
        raise ValueError("this chunk has no game packets")
    action = names.index(GAME_PACKET)
    tick_action = names.index(NEXT_TICK) if NEXT_TICK in names else None
    size = struct.unpack(">I", chunk[i:i + 4])[0]
    snap_end = i + 4 + size
    extra = b"".join(_record(action, r) for r in snapshot)
    head = chunk[:i] + struct.pack(">I", size + len(extra)) + chunk[i + 4:snap_end] + extra
    if not at_tick:
        return head + chunk[snap_end:]
    if tick_action is None:
        raise ValueError("this chunk has no tick markers")
    pending = sorted(at_tick.items())
    out = [head]
    j, tick, start = snap_end, 0, snap_end
    while pending and pending[0][0] <= 0:                 # tick 0 = right after the snapshot
        out.append(b"".join(_record(action, r) for r in pending.pop(0)[1]))
    while j < len(chunk) and pending:
        a, k = _read_varint(chunk, j)
        ln = struct.unpack(">I", chunk[k:k + 4])[0]
        j = k + 4 + ln
        if a == tick_action:
            tick += 1
            while pending and pending[0][0] <= tick:
                out.append(chunk[start:j])
                start = j
                out.append(b"".join(_record(action, r) for r in pending.pop(0)[1]))
    out.append(chunk[start:])
    for _t, recs in pending:                              # past the last tick: at the very end
        out.append(b"".join(_record(action, r) for r in recs))
    return b"".join(out)


def add_to_chunk(chunk: bytes, records: list[bytes]) -> bytes:
    """Append game-packet records to the snapshot of one ``cN.flashback`` file."""
    if struct.unpack(">I", chunk[:4])[0] != FLASHBACK_MAGIC:
        raise ValueError("not a Flashback chunk file")
    i = 4
    n, i = _read_varint(chunk, i)
    names = []
    for _ in range(n):
        ln, i = _read_varint(chunk, i)
        names.append(chunk[i:i + ln].decode("utf-8"))
        i += ln
    if GAME_PACKET not in names:
        raise ValueError("this chunk has no game packets")
    action = names.index(GAME_PACKET)
    size = struct.unpack(">I", chunk[i:i + 4])[0]
    snap = chunk[i + 4:i + 4 + size]
    extra = b"".join(_record(action, r) for r in records)
    return chunk[:i] + struct.pack(">I", size + len(extra)) + snap + extra + chunk[i + 4 + size:]


def with_outline(t: WorldText) -> list[WorldText]:
    """A bordered text as several text displays: copies in the border colour, shifted a little up,
    down, left and right and set slightly further from the camera, then the text itself in front.
    (Minecraft text displays only have a drop shadow, no outline.) Best seen from about the
    direction *camera_yaw* looks from."""
    if not t.outline:
        return [t]
    w = t.outline_width or max(0.04, t.size / 14.0)
    yaw = math.radians(t.camera_yaw if t.camera_yaw is not None else 0.0)
    fx, fz = -math.sin(yaw), math.cos(yaw)              # the camera's forward direction
    rx, rz = -math.cos(yaw), -math.sin(yaw)             # its right
    back = max(0.05, w * 0.5)
    out = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7)):
        out.append(WorldText(t.text, t.x + rx * dx * w + fx * back, t.y + dy * w, t.z + rz * dx * w + fz * back,
                             size=t.size, colour=t.outline, bold=t.bold, facing=t.facing, yaw=t.yaw, shadow=False,
                             background=0, glow=t.glow, view_range=t.view_range, line_width=t.line_width))
    out.append(WorldText(**{**t.__dict__, "outline": "", "shadow": False}))
    return out


def write_world_text_copy(src: str, dst: str, texts: list[WorldText], name_suffix: str = " (text)") -> dict:
    """Copy a Flashback recording with *texts* placed in its world. The original isn't changed.
    Returns the copy's metadata (new uuid; ``original_uuid_mcsm`` = the original's)."""
    if os.path.abspath(src) == os.path.abspath(dst):
        raise ValueError("refusing to overwrite the original recording")
    with zipfile.ZipFile(src) as zin:
        meta = json.loads(zin.read("metadata.json").decode("utf-8"))
        proto = int(meta.get("protocol_version") or 0)
        if proto not in PROTOCOLS:
            known = ", ".join(str(p) for p in sorted(PROTOCOLS))
            raise ValueError(f"Minecraft {meta.get('version_string') or '?'} (protocol {proto}) isn't supported "
                             f"yet for in-world text (supported: {known} = 26.1.x, 26.2, 26.3)")
        add_id, data_id, type_id = PROTOCOLS[proto]
        texts_in = texts
        texts = [part for t in texts_in for part in with_outline(t)]
        spawns = []                                       # (appear, disappear, packets to add, entity id)
        for k, t in enumerate(texts):
            eid = 2_000_000_000 + k                      # far above anything the server hands out
            uid = uuid_mod.uuid4()
            pk = [_varint(add_id) + add_entity_packet(eid, uid, type_id, t.x, t.y, t.z, t.yaw, 0.0),
                  _varint(data_id) + text_data_packet(eid, t)]
            spawns.append((t.appear_tick, t.disappear_tick, pk, eid))
        remove_id = REMOVE_ENTITIES[proto]
        # where each chunk file starts (replay ticks), from the metadata's chunk list
        starts, pos = {}, 0
        for name, info_ in sorted((meta.get("chunks") or {}).items(),
                                  key=lambda kv: int("".join(ch for ch in kv[0] if ch.isdigit()) or 0)):
            starts[name] = pos
            pos += int((info_ or {}).get("duration") or 0)

        def plan_for(name: str, length: int) -> tuple[list[bytes], dict[int, list[bytes]]]:
            c0 = starts.get(name, 0)
            c1 = c0 + length
            snap, timed = [], {}
            for appear, gone, pk, eid in spawns:
                a = appear if appear is not None else -1
                g = gone if gone is not None else 1 << 60
                if a <= c0 < g:
                    snap += pk                            # already there when this chunk starts
                elif c0 < a < c1:
                    timed.setdefault(a - c0, []).extend(pk)
                if c0 < g < c1 and (a <= c0 or a < g):
                    timed.setdefault(g - c0, []).append(_varint(remove_id) + _varint(1) + _varint(eid))
            return snap, timed
        meta["original_uuid_mcsm"] = meta.get("uuid")
        meta["uuid"] = str(uuid_mod.uuid4())
        meta["name"] = (meta.get("name") or "Replay") + name_suffix
        meta["mcsm_world_text"] = [t.__dict__ for t in texts_in]
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        tmp = dst + ".part"
        chunks = 0
        try:
            with zipfile.ZipFile(tmp, "w") as zout:
                for info in zin.infolist():
                    if info.filename == "metadata.json":
                        zout.writestr(info, json.dumps(meta, indent=2), compress_type=zipfile.ZIP_DEFLATED)
                        continue
                    out_info = zipfile.ZipInfo(info.filename, info.date_time)
                    out_info.compress_type = info.compress_type
                    out_info.external_attr = info.external_attr
                    if info.filename.endswith(".flashback") and "/" not in info.filename:
                        length = int(((meta.get("chunks") or {}).get(info.filename) or {}).get("duration") or 0)
                        snap, timed = plan_for(info.filename, length)
                        zout.writestr(out_info, add_timed(zin.read(info), snap, timed))
                        chunks += 1
                        continue
                    with zin.open(info) as fin, zout.open(out_info, "w", force_zip64=True) as fout:
                        shutil.copyfileobj(fin, fout, 1 << 20)
            os.replace(tmp, dst)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
    meta["_chunks"] = chunks
    return meta


# --- placing text where the camera looks ---------------------------------------------------

def copy_edit(state_file: str, target: str, original_uuid: str, export: tuple[int, int] | None = None) -> str:
    """Copy a Flashback edit (editor state) for a replay copy, marked as made by mcsm; optionally
    with the export range that made a given video. Never overwrites."""
    if os.path.exists(target):
        raise FileExistsError(target)
    with open(state_file, encoding="utf-8") as fh:
        state = json.load(fh)
    if export:
        idx = int(state.get("sceneIndex") or 0)
        scenes = state.get("scenes") or []
        if scenes:
            sc = scenes[idx if 0 <= idx < len(scenes) else 0]
            sc["exportStartTicks"], sc["exportEndTicks"] = int(export[0]), int(export[1])
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump({"mcsm_copy_of": original_uuid, **state}, fh)
    return target


def camera_at(scene: dict, tick: float) -> tuple[list[float], float, float] | None:
    """Camera ``(position, yaw, pitch)`` at replay *tick* from the scene's CAMERA keyframes
    (linear between keyframes; orbit/entity-tracking cameras are not followed)."""
    for t in scene.get("keyframeTracks") or []:
        if t.get("keyframeType") == "CAMERA" and t.get("enabled", True):
            keys = sorted((int(k), v) for k, v in (t.get("keyframesByTick") or {}).items()
                          if isinstance(v, dict) and v.get("position"))
            if not keys:
                return None
            if tick <= keys[0][0]:
                k = keys[0][1]
                return list(k["position"]), float(k.get("yaw", 0)), float(k.get("pitch", 0))
            for (t1, a), (t2, b) in zip(keys, keys[1:]):
                if t1 <= tick <= t2:
                    f = (tick - t1) / max(1, t2 - t1)
                    pos = [pa + (pb - pa) * f for pa, pb in zip(a["position"], b["position"])]
                    dyaw = ((float(b.get("yaw", 0)) - float(a.get("yaw", 0)) + 180) % 360) - 180
                    return (pos, float(a.get("yaw", 0)) + dyaw * f,
                            float(a.get("pitch", 0)) + (float(b.get("pitch", 0)) - float(a.get("pitch", 0))) * f)
            k = keys[-1][1]
            return list(k["position"]), float(k.get("yaw", 0)), float(k.get("pitch", 0))
    return None


def in_front(position: list[float], yaw: float, pitch: float, distance: float) -> tuple[float, float, float]:
    """The point *distance* blocks in front of a camera (Minecraft yaw/pitch, degrees)."""
    ry, rp = math.radians(yaw), math.radians(pitch)
    dx, dy, dz = -math.sin(ry) * math.cos(rp), -math.sin(rp), math.cos(ry) * math.cos(rp)
    return position[0] + dx * distance, position[1] + dy * distance, position[2] + dz * distance
