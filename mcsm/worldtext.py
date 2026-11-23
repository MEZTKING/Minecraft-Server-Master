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
  25 background ARGB, 26 text opacity (byte), 27 flags (1 shadow, 2 see-through, 4 default
  background, 8 left, 16 right). Transformation: 8 interpolation start (VarInt), 9 interpolation
  duration (VarInt), 11 translation (Vector3f, serializer 39), 13 left rotation (Quaternionf x, y, z, w,
  serializer 40), all the same in 26.1.x, 26.2 and 26.3.

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
# set_passengers (vehicle VarInt, count VarInt, passenger VarInts): makes text ride a player
SET_PASSENGERS = {775: 107, 776: 107, 777: 109}
GAME_PACKET = "flashback:action/game_packet"
NEXT_TICK = "flashback:action/next_tick"
FLASHBACK_MAGIC = 0xD780E884

SER_BYTE, SER_INT, SER_FLOAT, SER_COMPONENT, SER_VECTOR3, SER_QUATERNION = 0, 1, 3, 5, 39, 40
BILLBOARD = {"fixed": 0, "vertical": 1, "horizontal": 2, "center": 3}
COLOURS = ("black", "dark_blue", "dark_green", "dark_aqua", "dark_red", "dark_purple", "gold", "gray",
           "dark_gray", "blue", "green", "aqua", "red", "light_purple", "yellow", "white")
LINE_HEIGHT = 0.25          # blocks per text line at scale 1 (9 font pixels + spacing, ~1/40 block each)
MC_RGB = {"black": (0, 0, 0), "dark_blue": (0, 0, 170), "dark_green": (0, 170, 0), "dark_aqua": (0, 170, 170),
          "dark_red": (170, 0, 0), "dark_purple": (170, 0, 170), "gold": (255, 170, 0), "gray": (170, 170, 170),
          "dark_gray": (85, 85, 85), "blue": (85, 85, 255), "green": (85, 255, 85), "aqua": (85, 255, 255),
          "red": (255, 85, 85), "light_purple": (255, 85, 255), "yellow": (255, 255, 85), "white": (255, 255, 255)}


def rgb_of(colour: str) -> tuple[int, int, int]:
    if colour and colour.startswith("#") and len(colour) == 7:
        return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)
    return MC_RGB.get(colour or "white", (255, 255, 255))


def shade(colour: str, f: float) -> str:
    """*colour* darkened to *f* (0..1) of its brightness, as #RRGGBB."""
    r, g, b = rgb_of(colour)
    return "#%02x%02x%02x" % (int(r * f), int(g * f), int(b * f))


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
    animation: str = "none"         # none, fade, pop, words, words-fade, words-pop (see ANIMATIONS)
    anim_ticks: int = 8             # how long one fade/pop takes (20 ticks = 1 s)
    word_gap_ticks: int = 6         # words one by one: time between words
    fade_out_ticks: int = 0         # fade out before disappearing (0 = vanish)
    outline_colour: str = ""        # set internally for border copies
    ride: int | None = None          # entity id of a player to ride on (follows them; None = stays put)
    offset: tuple = (0.0, 0.0, 0.0)  # translation in the text's own space: right, up, towards the camera
    opacity: int = 255              # 26..255 (text below ~26 isn't drawn by Minecraft)
    pitch: float = 0.0              # for "fixed": tilt (-90 = lying face up, 90 = face down)
    roll: float = 0.0               # turned in its own plane, degrees clockwise (as you look at it)
    pivot: tuple = (0.0, 0.0, 0.0)  # point it turns around, in its own space from its position
    depth: float = 0.0              # 3D letters: thickness as a share of the letter height (0 = flat)
    italic: bool = False
    underline: bool = False
    local: bool = False             # parts (border, words, 3D) placed with the offset, in its own space
    colour2: str = ""               # colour gradient: the last letter's colour (empty = one colour)
    halo: bool = False              # a soft glow around the letters (faint bigger copies behind)
    exit: str = "fade"              # how it leaves over fade_out_ticks: fade, shrink, drop, rise
    max_layers: int = 12            # most 3D layers (fewer for letter-by-letter texts: many entities)


ANIMATIONS = {
    "none": "appears at once",
    "fade": "fades in",
    "pop": "pops in: springs from nothing to a little too big, then settles",
    "words": "words appear one by one, instantly",
    "words-fade": "words fade in one by one",
    "words-pop": "words pop in one by one",
    "rise": "rises into place while fading in",
    "slam": "comes down from big, like a stamp",
    "letters": "letters appear one by one (typewriter)",
    "letters-fade": "letters fade in one by one",
    "letters-pop": "letters pop in one by one",
    "letters-drop": "letters drop in one by one and bounce",
    "bounce": "drops in from above and bounces",
    "flip": "flips up from lying flat",
    "shake": "appears and shakes",
}
EXITS = {"fade": "fades out", "shrink": "shrinks away", "drop": "falls and fades", "rise": "rises and fades"}
# a piece of a split text (one word or letter) animates like this
SINGLE = {"words": "none", "words-fade": "fade", "words-pop": "pop", "letters": "none", "letters-fade": "fade",
          "letters-pop": "pop", "letters-drop": "bounce"}

# Advance widths (pixels, 1/40 block at scale 1) of Minecraft's default font; others are 6.
_GLYPH = {" ": 4, "!": 2, '"': 4, "'": 2, "(": 4, ")": 4, "*": 4, ",": 2, ".": 2, ":": 2, ";": 2, "<": 5,
          ">": 5, "@": 7, "I": 4, "[": 4, "]": 4, "`": 3, "f": 5, "i": 2, "k": 5, "l": 3, "t": 4, "{": 4,
          "|": 2, "}": 4, "~": 7}


def text_width_px(text: str, bold: bool = False) -> int:
    """Width of *text* in Minecraft font pixels (bold adds one per character)."""
    return sum(_GLYPH.get(ch, 6) + (1 if bold and ch != " " else 0) for ch in text)


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


def gradient(colour: str, colour2: str, n: int) -> list[str]:
    """*n* colours from *colour* to *colour2* (#rrggbb)."""
    a, b = rgb_of(colour), rgb_of(colour2)
    return ["#%02x%02x%02x" % tuple(round(a[c] + (b[c] - a[c]) * (i / max(1, n - 1))) for c in range(3)) for i in range(n)]


def text_component(text: str, colour: str = "white", bold: bool = False, italic: bool = False,
                   underline: bool = False, colour2: str = "") -> bytes:
    """A text component as network NBT (nameless root): a plain string tag when unstyled,
    else a compound — exactly as the game writes it. With *colour2* each letter gets its own colour
    (a gradient), as children in ``extra``."""
    if colour2 and len(text.strip()) > 1:
        letters = [ch for ch in text]
        visible = [i for i, ch in enumerate(letters) if not ch.isspace()]
        cols = gradient(colour, colour2, len(visible))
        col_of = {i: cols[k] for k, i in enumerate(visible)}
        out = b"\x0a" + b"\x08" + _nbt_str("text") + _nbt_str("")
        if bold:
            out += b"\x01" + _nbt_str("bold") + b"\x01"
        if italic:
            out += b"\x01" + _nbt_str("italic") + b"\x01"
        if underline:
            out += b"\x01" + _nbt_str("underlined") + b"\x01"
        out += b"\x09" + _nbt_str("extra") + b"\x0a" + struct.pack(">i", len(letters))
        last = cols[0]
        for i, ch in enumerate(letters):
            last = col_of.get(i, last)
            out += b"\x08" + _nbt_str("color") + _nbt_str(last) + b"\x08" + _nbt_str("text") + _nbt_str(ch) + b"\x00"
        return out + b"\x00"
    if (not colour or colour == "white") and not (bold or italic or underline):
        return b"\x08" + _nbt_str(text)
    out = b"\x0a"
    if colour and colour != "white":
        out += b"\x08" + _nbt_str("color") + _nbt_str(colour)
    out += b"\x08" + _nbt_str("text") + _nbt_str(text)
    if bold:
        out += b"\x01" + _nbt_str("bold") + b"\x01"
    if italic:
        out += b"\x01" + _nbt_str("italic") + b"\x01"
    if underline:
        out += b"\x01" + _nbt_str("underlined") + b"\x01"
    return out + b"\x00"


def _angle(deg: float) -> bytes:
    return bytes([int(math.floor(deg * 256.0 / 360.0)) & 0xFF])


def add_entity_packet(entity_id: int, uid: uuid_mod.UUID, type_id: int, x: float, y: float, z: float,
                      yaw: float = 0.0, pitch: float = 0.0) -> bytes:
    return (_varint(entity_id) + uid.bytes + _varint(type_id) + struct.pack(">ddd", x, y, z)
            + b"\x00"                                   # velocity: LpVec3 zero
            + _angle(pitch) + _angle(yaw) + b"\x00" + _varint(0))


def _scale_of(t: WorldText) -> float:
    return max(0.01, t.size / LINE_HEIGHT)


def scale_packet(entity_id: int, scale: float, duration: int) -> bytes:
    """Change the size, animated smoothly by the game over *duration* ticks."""
    return (_varint(entity_id) + bytes([8]) + _varint(SER_INT) + _varint(0)
            + bytes([9]) + _varint(SER_INT) + _varint(max(0, duration))
            + bytes([12]) + _varint(SER_VECTOR3) + struct.pack(">fff", scale, scale, scale) + b"\xff")


def _rot(t: WorldText, v) -> tuple[float, float, float]:
    """*v* turned by the text's roll (about its own forward axis; clockwise as you look at it)."""
    a = math.radians(-t.roll)
    c, s_ = math.cos(a), math.sin(a)
    return v[0] * c - v[1] * s_, v[0] * s_ + v[1] * c, v[2]


def translation_of(t: WorldText, extra=(0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    """The display translation: the offset (plus *extra*), turned with the text around its pivot
    (T = P - R(P) + R(offset), so turning happens about the pivot, not the text's foot)."""
    o = tuple(a + b for a, b in zip(t.offset, extra))
    if not t.roll:
        return o
    p, rp, ro = t.pivot, _rot(t, t.pivot), _rot(t, o)
    return p[0] - rp[0] + ro[0], p[1] - rp[1] + ro[1], p[2] - rp[2] + ro[2]


def quaternion_of(t: WorldText, flip: float = 0.0) -> tuple[float, float, float, float]:
    """The text's turn (roll about its forward axis), after tipping it *flip* degrees about its own
    left-right axis (the flip-in animation starts at 90: lying flat)."""
    a = math.radians(-t.roll) / 2
    qz = (0.0, 0.0, math.sin(a), math.cos(a))
    if not flip:
        return qz
    b = math.radians(-flip) / 2
    qx = (math.sin(b), 0.0, 0.0, math.cos(b))
    x1, y1, z1, w1 = qz
    x2, y2, z2, w2 = qx
    return (w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2, w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2, w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2)


def transform_packet(entity_id: int, duration: int, translation=None, scale: float | None = None,
                     rotation=None) -> bytes:
    """Move, resize and/or turn, animated smoothly by the game over *duration* ticks."""
    out = (_varint(entity_id) + bytes([8]) + _varint(SER_INT) + _varint(0)
           + bytes([9]) + _varint(SER_INT) + _varint(max(0, duration)))
    if translation is not None:
        out += bytes([11]) + _varint(SER_VECTOR3) + struct.pack(">fff", *translation)
    if scale is not None:
        out += bytes([12]) + _varint(SER_VECTOR3) + struct.pack(">fff", scale, scale, scale)
    if rotation is not None:
        out += bytes([13]) + _varint(SER_QUATERNION) + struct.pack(">ffff", *rotation)
    return out + b"\xff"


def opacity_packet(entity_id: int, opacity: int) -> bytes:
    return _varint(entity_id) + bytes([26]) + _varint(SER_BYTE) + bytes([max(0, min(255, opacity)) & 0xFF]) + b"\xff"


def text_data_packet(entity_id: int, t: WorldText, scale: float | None = None, extra=(0.0, 0.0, 0.0),
                     flip: float = 0.0) -> bytes:
    scale = _scale_of(t) if scale is None else scale
    flags = 0x01 if t.shadow else 0
    out = _varint(entity_id)
    out += bytes([12]) + _varint(SER_VECTOR3) + struct.pack(">fff", scale, scale, scale)
    tr = translation_of(t, extra)
    if any(tr):
        out += bytes([11]) + _varint(SER_VECTOR3) + struct.pack(">fff", *tr)
    if t.roll or flip:
        out += bytes([13]) + _varint(SER_QUATERNION) + struct.pack(">ffff", *quaternion_of(t, flip))
    out += bytes([15]) + _varint(SER_BYTE) + bytes([BILLBOARD.get(t.facing, 3)])
    if t.glow:
        out += bytes([16]) + _varint(SER_INT) + _varint(15 << 4 | 15 << 20)
    out += bytes([17]) + _varint(SER_FLOAT) + struct.pack(">f", t.view_range)
    out += bytes([23]) + _varint(SER_COMPONENT) + text_component(t.text, t.colour, t.bold, t.italic, t.underline,
                                                                 t.colour2)
    out += bytes([24]) + _varint(SER_INT) + _varint(t.line_width)
    out += bytes([25]) + _varint(SER_INT) + _varint(t.background)
    if t.opacity != 255:
        out += bytes([26]) + _varint(SER_BYTE) + bytes([max(0, min(255, t.opacity)) & 0xFF])
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
    if t.ride is not None or t.local:
        # Riding: all parts sit on the same spot, so the border is made with the offset, which the
        # game applies in the text's own (camera-facing) space: right/up, the text a little nearer.
        ox, oy, oz = t.offset
        out = [WorldText(**{**t.__dict__, "offset": (ox + dx * w, oy + dy * w, oz), "colour": t.outline,
                            "shadow": False, "background": 0, "outline": ""})
               for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7))]
        out.append(WorldText(**{**t.__dict__, "offset": (ox, oy, oz + max(0.02, w * 0.3)), "outline": "",
                                "shadow": False}))
        return out
    yaw = math.radians(t.camera_yaw if t.camera_yaw is not None else 0.0)
    fx, fz = -math.sin(yaw), math.cos(yaw)              # the camera's forward direction
    rx, rz = -math.cos(yaw), -math.sin(yaw)             # its right
    back = max(0.05, w * 0.5)
    out = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7)):
        out.append(WorldText(**{**t.__dict__, "x": t.x + rx * dx * w + fx * back, "y": t.y + dy * w,
                                "z": t.z + rz * dx * w + fz * back, "colour": t.outline, "shadow": False,
                                "background": 0, "outline": ""}))
    out.append(WorldText(**{**t.__dict__, "outline": "", "shadow": False}))
    return out


def split_words(t: WorldText) -> list[WorldText]:
    """For word-by-word and letter-by-letter animations: one text per word (or letter), placed along
    the line (as seen from the camera direction *camera_yaw*) so the line looks the same as one
    text; piece i appears *word_gap_ticks* after piece i-1. A colour gradient runs across the whole
    line (each piece gets its part of it)."""
    by_letter = t.animation.startswith("letters")
    if not (t.animation.startswith("words") or by_letter):
        return [t]
    # (start index in the text, piece) for each word, or each letter that isn't a space
    if by_letter:
        pieces = [(i, ch) for i, ch in enumerate(t.text) if not ch.isspace()]
    else:
        pieces, i = [], 0
        for w in t.text.split():
            i = t.text.index(w, i)
            pieces.append((i, w))
            i += len(w)
    if len(pieces) < 2:
        return [WorldText(**{**t.__dict__, "animation": SINGLE[t.animation]})]
    px = 0.025 * _scale_of(t)                                       # blocks per font pixel
    total = text_width_px(t.text, t.bold)
    yaw = math.radians(t.camera_yaw if t.camera_yaw is not None else t.yaw)
    rx, rz = -math.cos(yaw), -math.sin(yaw)                          # the camera's right
    single = SINGLE[t.animation]
    visible = [i for i, ch in enumerate(t.text) if not ch.isspace()]
    cols = gradient(t.colour, t.colour2, len(visible)) if t.colour2 else None
    col_at = {i: k for k, i in enumerate(visible)}
    out = []
    for n, (start, w) in enumerate(pieces):
        left = text_width_px(t.text[:start], t.bold)
        wpx = text_width_px(w, t.bold)
        centre = (left + wpx / 2 - total / 2) * px
        appear = (t.appear_tick or 0) + n * t.word_gap_ticks if t.appear_tick is not None else None
        look = {"text": w, "animation": single, "appear_tick": appear}
        if cols:                                     # this piece's part of the gradient
            a, b = cols[col_at[start]], cols[col_at[start + len(w) - 1]]
            look.update({"colour": a, "colour2": b if b != a and len(w) > 1 else ""})
        if by_letter:
            look["max_layers"] = min(t.max_layers, 4)   # many letters x border x 3D layers: keep it light
        if t.ride is not None or t.local:            # riding / fixed: spread along the line with the offset
            ox, oy, oz = t.offset
            out.append(WorldText(**{**t.__dict__, **look, "offset": (ox + centre, oy, oz)}))
        else:
            px_, py_, pz_ = t.pivot                  # it still turns around the middle of the whole line
            out.append(WorldText(**{**t.__dict__, **look, "x": t.x + rx * centre, "z": t.z + rz * centre,
                                    "pivot": (px_ - centre, py_, pz_)}))
    return out


def with_halo(t: WorldText) -> list[WorldText]:
    """A soft glow: two faint, slightly bigger copies of the text behind it (text displays have no real
    glow), then the text. They grow around the letters' middle, so they're moved down by half the growth."""
    if not t.halo:
        return [t]
    out = []
    w = t.outline_width or max(0.04, t.size / 14.0)
    for grow, op in ((1.22, 50), (1.1, 90)):
        size = t.size * grow
        down = (size - t.size) / 2
        back = max(0.08, w * 0.6) * (2.2 if grow > 1.15 else 1.6) + (t.depth * t.size if t.depth else 0)
        look = {"size": size, "opacity": max(26, op * t.opacity // 255), "outline": "", "shadow": False, "depth": 0.0,
                "background": 0, "halo": False, "glow": True}
        if t.ride is not None or t.local:
            ox, oy, oz = t.offset
            out.append(WorldText(**{**t.__dict__, **look, "offset": (ox, oy - down, oz - back)}))
        else:
            yaw = math.radians(t.camera_yaw if t.camera_yaw is not None else 0.0)
            fx, fz = -math.sin(yaw), math.cos(yaw)
            out.append(WorldText(**{**t.__dict__, **look, "x": t.x + fx * back, "y": t.y - down, "z": t.z + fz * back}))
    out.append(WorldText(**{**t.__dict__, "halo": False}))
    return out


def with_depth(t: WorldText) -> list[WorldText]:
    """3D letters: darker copies stacked behind the text and a little lower, like the side of a
    block, then the text itself (which keeps its border). Behind = away from the camera."""
    if t.depth <= 0:
        return [t]
    n = max(2, min(t.max_layers, 12, round(t.depth * 24)))
    step = t.depth * t.size / n
    w = t.outline_width or max(0.04, t.size / 14.0)
    start = max(0.05, w * 0.5) * 1.5 if t.outline else 0.03      # behind the border copies
    out = []
    for k in range(n, 0, -1):
        back, down = start + k * step, k * step * 0.55
        look = {"colour": shade(t.colour, 0.55 - 0.3 * k / n), "outline": "", "shadow": False, "depth": 0.0,
                "background": 0}
        if t.ride is not None or t.local:
            ox, oy, oz = t.offset
            out.append(WorldText(**{**t.__dict__, **look, "offset": (ox, oy - down, oz - back)}))
        else:
            yaw = math.radians(t.camera_yaw if t.camera_yaw is not None else 0.0)
            fx, fz = -math.sin(yaw), math.cos(yaw)
            out.append(WorldText(**{**t.__dict__, **look, "x": t.x + fx * back, "y": t.y - down, "z": t.z + fz * back}))
    out.append(WorldText(**{**t.__dict__, "depth": 0.0}))
    return out


@dataclass
class _Entity:
    eid: int
    ride: int | None
    appear: int | None
    gone: int | None
    start: list[bytes]                       # sent when it appears (add + first look)
    final: list[bytes]                       # its settled look (for snapshots after the animation)
    updates: list[tuple[int, bytes, bytes]]  # (tick, animated packet, instant packet)
    settled: int                             # tick from which *final* is right


def _plan_entity(eid: int, t: WorldText, add_id: int, data_id: int, type_id: int) -> _Entity:
    uid = uuid_mod.uuid4()
    add = _varint(add_id) + add_entity_packet(eid, uid, type_id, t.x, t.y, t.z, t.yaw, t.pitch)
    final = [add, _varint(data_id) + text_data_packet(eid, t)]
    a = t.appear_tick
    updates: list[tuple[int, bytes, bytes]] = []
    start, settled = final, (a or 0)
    target, n = _scale_of(t), max(1, t.anim_ticks)
    if a is not None and t.animation == "pop":
        start = [add, _varint(data_id) + text_data_packet(eid, t, scale=target * 0.02)]
        up = max(1, round(n * 0.6))
        updates.append((a + 1, _varint(data_id) + scale_packet(eid, target * 1.18, up),
                        _varint(data_id) + scale_packet(eid, target * 1.18, 0)))
        updates.append((a + 1 + up, _varint(data_id) + scale_packet(eid, target, n - up + 2),
                        _varint(data_id) + scale_packet(eid, target, 0)))
        settled = a + 1 + n + 2
    elif a is not None and t.animation == "fade":
        lo = 26
        start = [add, _varint(data_id) + text_data_packet(eid, WorldText(**{**t.__dict__, "opacity": lo}))]
        for k in range(1, n + 1):
            op = lo + (t.opacity - lo) * k // n
            pk = _varint(data_id) + opacity_packet(eid, op)
            updates.append((a + k, pk, pk))
        settled = a + n + 1
    elif a is not None and t.animation in ("rise", "slam"):
        lo = 26
        faint = WorldText(**{**t.__dict__, "opacity": lo})
        if t.animation == "rise":                    # starts lower, glides up into place
            start = [add, _varint(data_id) + text_data_packet(eid, faint, extra=(0.0, -0.6 * t.size, 0.0))]
            move = transform_packet(eid, n, translation=translation_of(t))
            updates.append((a + 1, _varint(data_id) + move,
                            _varint(data_id) + transform_packet(eid, 0, translation=translation_of(t))))
            m = n
        else:                                        # starts big, comes down hard
            start = [add, _varint(data_id) + text_data_packet(eid, faint, scale=target * 2.6)]
            updates.append((a + 1, _varint(data_id) + scale_packet(eid, target, max(1, n // 2)),
                            _varint(data_id) + scale_packet(eid, target, 0)))
            m = max(1, n // 2)
        for k in range(1, m + 1):
            op = lo + (t.opacity - lo) * k // m
            pk = _varint(data_id) + opacity_packet(eid, op)
            updates.append((a + k, pk, pk))
        settled = a + n + 2
    elif a is not None and t.animation == "bounce":   # drops in from above, overshoots a little, settles
        lo = 26
        faint = WorldText(**{**t.__dict__, "opacity": lo})
        start = [add, _varint(data_id) + text_data_packet(eid, faint, extra=(0.0, 0.9 * t.size, 0.0))]
        fall = max(1, round(n * 0.65))
        below = translation_of(t, (0.0, -0.12 * t.size, 0.0))
        updates.append((a + 1, _varint(data_id) + transform_packet(eid, fall, translation=below),
                        _varint(data_id) + transform_packet(eid, 0, translation=below)))
        updates.append((a + 1 + fall, _varint(data_id) + transform_packet(eid, max(1, n - fall + 1), translation=translation_of(t)),
                        _varint(data_id) + transform_packet(eid, 0, translation=translation_of(t))))
        for k in range(1, fall + 1):
            op = lo + (t.opacity - lo) * k // fall
            pk = _varint(data_id) + opacity_packet(eid, op)
            updates.append((a + k, pk, pk))
        settled = a + n + 3
    elif a is not None and t.animation == "flip":     # lies flat, swings up to face you
        lo = 26
        faint = WorldText(**{**t.__dict__, "opacity": lo})
        start = [add, _varint(data_id) + text_data_packet(eid, faint, flip=90.0)]
        upright = quaternion_of(t)
        updates.append((a + 1, _varint(data_id) + transform_packet(eid, n, rotation=upright),
                        _varint(data_id) + transform_packet(eid, 0, rotation=upright)))
        m = max(1, n // 2)
        for k in range(1, m + 1):
            op = lo + (t.opacity - lo) * k // m
            pk = _varint(data_id) + opacity_packet(eid, op)
            updates.append((a + k, pk, pk))
        settled = a + n + 2
    elif a is not None and t.animation == "shake":    # there at once, then a short shake that calms down
        amp = 0.08 * t.size
        for k in range(n):
            side = amp * (1 - k / n) * (1 if k % 2 == 0 else -1)
            tr = translation_of(t, (side, 0.0, 0.0)) if k < n - 1 else translation_of(t)
            updates.append((a + 1 + k, _varint(data_id) + transform_packet(eid, 1, translation=tr),
                            _varint(data_id) + transform_packet(eid, 0, translation=tr)))
        settled = a + n + 2
    if t.disappear_tick is not None and t.fade_out_ticks > 0:
        m = t.fade_out_ticks
        go = t.disappear_tick - m
        if t.exit == "shrink":
            updates.append((go, _varint(data_id) + scale_packet(eid, target * 0.02, m),
                            _varint(data_id) + scale_packet(eid, target * 0.02, 0)))
        elif t.exit in ("drop", "rise"):
            away = translation_of(t, (0.0, (-0.9 if t.exit == "drop" else 0.9) * t.size, 0.0))
            updates.append((go, _varint(data_id) + transform_packet(eid, m, translation=away),
                            _varint(data_id) + transform_packet(eid, 0, translation=away)))
        if t.exit != "shrink":
            for k in range(1, m + 1):
                op = t.opacity - (t.opacity - 26) * k // m
                pk = _varint(data_id) + opacity_packet(eid, op)
                updates.append((go + k - 1, pk, pk))
    return _Entity(eid, t.ride, a, t.disappear_tick, start, final, updates, settled)


def write_world_text_copy(src: str, dst: str, texts: list[WorldText], name_suffix: str = " (text)",
                          keep_uuid: str | None = None, video: str | None = None,
                          markers: dict | None = None, title: str | None = None) -> dict:
    """Copy a Flashback recording with *texts* placed in its world. The original isn't changed.
    Returns the copy's metadata (new uuid; ``original_uuid_mcsm`` = the original's).

    *dst* may be an earlier copy that is being replaced: it is only swapped for the new file once
    that is complete. *keep_uuid* gives the copy that id (so Flashback keeps its edit for it);
    *video* is remembered in the copy (``mcsm_video``) to find it again. *markers* ``{tick: {colour,
    description}}`` are added to the copy's timeline markers (the recording's own ones are kept)."""
    if os.path.abspath(src) == os.path.abspath(dst):
        raise ValueError("refusing to overwrite the original recording")
    with zipfile.ZipFile(src) as zin:
        meta = json.loads(zin.read("metadata.json").decode("utf-8"))
        if meta.get("mcsm_world_text"):
            raise ValueError("this recording is a copy that already has text added; use the original recording "
                             f"(id {meta.get('original_uuid_mcsm') or '?'}) so old text doesn't stay in it")
        proto = int(meta.get("protocol_version") or 0)
        if proto not in PROTOCOLS:
            known = ", ".join(str(p) for p in sorted(PROTOCOLS))
            raise ValueError(f"Minecraft {meta.get('version_string') or '?'} (protocol {proto}) isn't supported "
                             f"yet for in-world text (supported: {known} = 26.1.x, 26.2, 26.3)")
        add_id, data_id, type_id = PROTOCOLS[proto]
        texts_in = texts
        texts = [part for t in texts_in for word in split_words(t) for glow in with_halo(word)
                 for layer in with_depth(glow)
                 for part in (with_outline(layer) if layer.depth == 0 and layer.outline else [layer])]
        # far above any entity id the server hands out, and different for every copy
        import random
        base = 1_500_000_000 + random.randrange(0, 600_000) * 1000
        ents = [_plan_entity(base + k, t, add_id, data_id, type_id) for k, t in enumerate(texts)]
        remove_id = REMOVE_ENTITIES[proto]
        pass_id = SET_PASSENGERS[proto]
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
            for e in ents:
                a = e.appear if e.appear is not None else -1
                g = e.gone if e.gone is not None else 1 << 60
                if a <= c0 < g:                           # already there when this chunk starts
                    if c0 >= e.settled:
                        snap += e.final
                    else:
                        snap += e.start
                    snap += [instant for t_, _anim, instant in e.updates if t_ <= c0 and (c0 < e.settled or
                                                                                          t_ >= e.settled)]
                elif c0 < a < c1:
                    timed.setdefault(a - c0, []).extend(e.start)
                for t_, anim, _instant in e.updates:
                    if c0 < t_ < c1 and t_ > a:
                        timed.setdefault(t_ - c0, []).append(anim)
                if c0 < g < c1 and a < g:
                    timed.setdefault(g - c0, []).append(_varint(remove_id) + _varint(1) + _varint(e.eid))
            # Riders: tell the game which texts ride each player, at the start of the file and whenever
            # one appears (the list replaces the player's passengers, so it always names all of them).
            riders: dict[int, list[_Entity]] = {}
            for e in ents:
                if e.ride is not None:
                    riders.setdefault(e.ride, []).append(e)

            def alive(es, t_):
                return [e.eid for e in es if (e.appear if e.appear is not None else -1) <= t_ <
                        (e.gone if e.gone is not None else 1 << 60)]
            for vehicle, es in riders.items():
                now = alive(es, c0)
                if now:
                    snap.append(_varint(pass_id) + _varint(vehicle) + _varint(len(now))
                                + b"".join(_varint(x) for x in now))
                for t_ in sorted({e.appear for e in es if e.appear is not None and c0 < e.appear < c1}):
                    now = alive(es, t_)
                    timed.setdefault(t_ - c0, []).append(_varint(pass_id) + _varint(vehicle) + _varint(len(now))
                                                         + b"".join(_varint(x) for x in now))
            return snap, timed
        meta["original_uuid_mcsm"] = meta.get("uuid")
        meta["uuid"] = keep_uuid or str(uuid_mod.uuid4())
        if video:
            meta["mcsm_video"] = os.path.normcase(os.path.abspath(video))
        from .flashback import copy_name
        meta["name"] = copy_name(meta, src, name_suffix, title)        # findable in Flashback's list
        meta["mcsm_world_text"] = [t.__dict__ for t in texts_in]
        if markers:
            merged = dict(meta.get("markers") or {})
            for k, v in markers.items():
                merged.setdefault(str(k), v)           # never replace a marker of the recording itself
            meta["markers"] = merged
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
            try:
                os.replace(tmp, dst)
            except PermissionError:
                raise PermissionError(f"{dst} is in use (open in Flashback?): close that replay and try again")
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


# Interpolation types of Flashback keyframes: (left side, right side), from its InterpolationType.
SIDES = {"SMOOTH": ("SMOOTH", "SMOOTH"), "LINEAR": ("LINEAR", "LINEAR"), "HOLD": ("HOLD", "HOLD"),
         "EASE_IN": ("EASE", "LINEAR"), "EASE_OUT": ("LINEAR", "EASE"), "EASE_IN_OUT": ("EASE", "EASE"),
         "HERMITE": ("SMOOTH", "SMOOTH")}


def _wrap(d: float) -> float:
    return (d + 180.0) % 360.0 - 180.0


def _ease(left: str, right: str, a: float) -> float:
    """Flashback's SidedInterpolationType.interpolate for the non-spline cases."""
    if left == "EASE" and right == "EASE":
        return 4 * a ** 3 if a < 0.5 else 1 - (-2 * a + 2) ** 3 / 2
    if left == "EASE":
        return a ** 3
    if right == "EASE":
        return 1 - (1 - a) ** 3
    return a


def _catmull(p0, p1, p2, p3, a: float, alpha: float = 0.5):
    """Centripetal Catmull-Rom between p1 and p2 (Barry-Goldman pyramid), *a* in 0..1."""
    def knot(t, pa, pb):
        d = sum((x - y) ** 2 for x, y in zip(pa, pb)) ** 0.5
        return t + max(d ** alpha, 1e-4)
    t0 = 0.0
    t1 = knot(t0, p0, p1)
    t2 = knot(t1, p1, p2)
    t3 = knot(t2, p2, p3)
    t = t1 + (t2 - t1) * a

    def lerp(pa, pb, ta, tb):
        w = (t - ta) / (tb - ta)
        return [x + (y - x) * w for x, y in zip(pa, pb)]
    a1, a2, a3 = lerp(p0, p1, t0, t1), lerp(p1, p2, t1, t2), lerp(p2, p3, t2, t3)
    b1, b2 = lerp(a1, a2, t0, t2), lerp(a2, a3, t1, t3)
    return lerp(b1, b2, t1, t2)


def _cr_uniform(p0: float, p1: float, p2: float, p3: float, a: float) -> float:
    return 0.5 * ((2 * p1) + (-p0 + p2) * a + (2 * p0 - 5 * p1 + 4 * p2 - p3) * a * a
                  + (-p0 + 3 * p1 - 3 * p2 + p3) * a ** 3)


def camera_keys(scene: dict) -> list[tuple[int, dict]]:
    """The scene's CAMERA keyframes ``[(tick, keyframe)]`` in order (first enabled CAMERA track)."""
    for t in scene.get("keyframeTracks") or []:
        if t.get("keyframeType") == "CAMERA" and t.get("enabled", True):
            return sorted((int(k), v) for k, v in (t.get("keyframesByTick") or {}).items()
                          if isinstance(v, dict) and v.get("position"))
    return []


def camera_at(scene: dict, tick: float) -> tuple[list[float], float, float] | None:
    """Camera ``(position, yaw, pitch)`` at replay *tick* from the scene's CAMERA keyframes, the way
    Flashback moves it: SMOOTH = centripetal Catmull-Rom through the neighbouring keyframes, HOLD,
    LINEAR and the eases (a segment uses its first keyframe's right side and its second keyframe's
    left side). Orbit/entity-tracking cameras are not followed."""
    keys = camera_keys(scene)
    if not keys:
        return None

    def out(k):
        return list(k["position"]), float(k.get("yaw", 0)), float(k.get("pitch", 0))
    if tick <= keys[0][0]:
        return out(keys[0][1])
    if tick >= keys[-1][0]:
        return out(keys[-1][1])
    i = max(j for j, (t_, _k) in enumerate(keys) if t_ <= tick)
    (t1, a), (t2, b) = keys[i], keys[i + 1]
    amount = (tick - t1) / max(1e-9, t2 - t1)
    left = SIDES.get(a.get("interpolation_type") or "SMOOTH", SIDES["SMOOTH"])[1]
    right = SIDES.get(b.get("interpolation_type") or "SMOOTH", SIDES["SMOOTH"])[0]
    if left == "HOLD":
        return out(a)
    ya, yb = float(a.get("yaw", 0)), float(b.get("yaw", 0))
    pa_, pb_ = float(a.get("pitch", 0)), float(b.get("pitch", 0))
    if left == "SMOOTH" or right == "SMOOTH":
        before = keys[i - 1][1] if i > 0 and keys[i - 1][1].get("interpolation_type") != "HOLD" else a
        after = keys[i + 2][1] if i + 2 < len(keys) and b.get("interpolation_type") != "HOLD" else b
        pos = _catmull(before["position"], a["position"], b["position"], after["position"], amount)

        def ang(key):
            v0, v1, v2, v3 = (float(k.get(key, 0)) for k in (before, a, b, after))
            u1 = v0 + _wrap(v1 - v0)
            u2 = u1 + _wrap(v2 - v1)
            u3 = u2 + _wrap(v3 - v2)
            return _cr_uniform(v0, u1, u2, u3, amount)
        return pos, ang("yaw"), ang("pitch")
    f = _ease(left, right, amount)
    pos = [x + (y - x) * f for x, y in zip(a["position"], b["position"])]
    return pos, ya + max(-180.0, min(180.0, _wrap(yb - ya) * f)), pa_ + (pb_ - pa_) * f


# Flashback puts the (spectator) player at a CAMERA keyframe's position (player.snapTo), so the picture
# is seen from the player's eyes, this much higher (a standing player's eye height).
EYE_HEIGHT = 1.62


def eye_at(scene: dict, tick: float) -> tuple[list[float], float, float] | None:
    """Where the picture is seen from at *tick*: :func:`camera_at` raised to eye height."""
    cam = camera_at(scene, tick)
    if cam is None:
        return None
    pos, yaw, pitch = cam
    return [pos[0], pos[1] + EYE_HEIGHT, pos[2]], yaw, pitch


def set_camera_track(edit_path: str, keyframes: list[dict]) -> None:
    """Replace the CAMERA keyframes of the edit's open scene with *keyframes* (``{tick, position,
    yaw, pitch, roll, interpolation_type}``), keeping the track itself and everything else."""
    with open(edit_path, encoding="utf-8") as fh:
        state = json.load(fh)
    scenes = state.get("scenes") or []
    idx = int(state.get("sceneIndex") or 0)
    sc = scenes[idx if 0 <= idx < len(scenes) else 0]
    apply_camera(sc, keyframes)
    tmp = edit_path + ".mcsm-part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, edit_path)


def apply_camera(scene: dict, keyframes: list[dict]) -> dict:
    """*scene* with its (first enabled) CAMERA track's keyframes replaced by *keyframes*."""
    by_tick = {}
    for k in keyframes:
        by_tick[str(int(k["tick"]))] = {"position": [float(v) for v in k["position"]],
                                        "yaw": float(k.get("yaw", 0)), "pitch": float(k.get("pitch", 0)),
                                        "roll": float(k.get("roll", 0)), "type": "camera",
                                        "interpolation_type": k.get("interpolation_type") or "SMOOTH"}
    tracks = scene.setdefault("keyframeTracks", [])
    for t in tracks:
        if t.get("keyframeType") == "CAMERA" and t.get("enabled", True):
            t["keyframesByTick"] = by_tick
            return scene
    tracks.insert(0, {"keyframeType": "CAMERA", "keyframesByTick": by_tick, "enabled": True, "customColour": 0})
    return scene


def in_front(position: list[float], yaw: float, pitch: float, distance: float) -> tuple[float, float, float]:
    """The point *distance* blocks in front of a camera (Minecraft yaw/pitch, degrees)."""
    ry, rp = math.radians(yaw), math.radians(pitch)
    dx, dy, dz = -math.sin(ry) * math.cos(rp), -math.sin(rp), math.cos(ry) * math.cos(rp)
    return position[0] + dx * distance, position[1] + dy * distance, position[2] + dz * distance
