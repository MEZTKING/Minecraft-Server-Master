"""Player paths decoded from Flashback recordings (ServerReplay or the Flashback mod).

A Flashback replay ``.zip`` holds ``c0.flashback``, ``c1.flashback``, ... Each chunk file is::

    u32 magic 0xD780E884
    VarInt N, then N action identifiers (VarInt length + UTF-8); the i-th has id i
    u32 snapshot size, snapshot bytes
    records until EOF: VarInt action id, u32 payload size, payload

Two version-independent actions give positions:

* ``flashback:action/create_local_player``: ``UUID, f64 x, f64 y, f64 z, ...`` (the recorded
  player; no entity id);
* ``flashback:action/move_entities``: ``VarInt levelCount``; per level a resource key
  (VarInt length + UTF-8) and ``VarInt count``; per entity ``VarInt id, f64 x, f64 y, f64 z,
  f32 yaw, f32 pitch, f32 headYaw, bool onGround``. Only entities that moved are written.

``flashback:action/next_tick`` marks each game tick (20 per second). The recorded player's
entity id is the one whose first ``move_entities`` position equals the ``create_local_player``
position (verified on real ServerReplay 3.5.2/3.6.0 files, 2026-11-21).

**Other players** in view come from two game packets (``flashback:action/game_packet``:
``VarInt packet id`` + the packet), whose ids depend on the protocol version:

* add entity (id 1 in 26.2/26.3): ``VarInt entity id, UUID, VarInt type, f64 x, f64 y, f64 z,
  ...``. An entity whose UUID is a known player's is that player. No entity-type table is
  needed. Players already in view when a chunk file starts are added in its snapshot, which
  holds records in the same format.
* remove entities (:data:`REMOVE_ENTITIES`): ``VarInt count, VarInt ids...``: the player left
  view. For unknown protocol versions, other players stop being tracked
  :data:`OTHER_TIMEOUT_TICKS` after they last moved.

**The world clock** (designed with Fable, 2026-11-23): every second the server sends each player the world's
game time (a set-time game packet starting with ``i64 gameTime``), and that counter is the same in every
recording. Its packet id differs per version, so it is found by behaviour: the packet whose first 8 bytes rise
by exactly the ticks passed. Each recording keeps ``[[tick, gameTime], ...]`` breakpoints (a jump means the
recording was paused there), so all recordings line up to the tick, whatever the lag; :func:`world_offsets`
turns game time into real time (one offset per stretch of overlapping recordings, from their save times).

Output is in the live-map track format (one JSON object per line)::

    {"t": <epoch ms>, "players": [{"name", "x", "y", "z", "yaw", "pitch", "dimension"}]}

so the files also work with ``mcsm timelapse --tracks``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import zipfile
from datetime import datetime
from typing import Callable, Iterator

from . import models as m

MAGIC = 0xD780E884
SAMPLE_TICKS = 20          # at most one point per second
KEEPALIVE_TICKS = 20 * 15  # ...and at least one every 15 s while the player stands still
MIN_MOVE = 0.25            # blocks
OTHER_TIMEOUT_TICKS = 20 * 60   # other players without a known remove packet: stop after 1 min still
CACHE_VERSION = 3          # 3: rows carry their tick ("k") and the file ends with the recording's clock

# Game packet ids by protocol version (found in the owner's real recordings, 2026-11-21:
# add entity carries a known player's UUID; remove entities is the only "[count, ids]" packet
# that ever lists a player's entity id).
ADD_ENTITY = {776: 1, 777: 1}          # 26.2, 26.3
REMOVE_ENTITIES = {776: 77, 777: 78}
DEFAULT_ADD_ENTITY = 1                 # unchanged since 1.20.2; safe to try: the UUID must match

_U32 = struct.Struct(">I")
_POS = struct.Struct(">dddfff")
_XYZ = struct.Struct(">ddd")


def default_cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mcsm", "paths")


def _varint(b, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        shift += 7
        if not c & 0x80:
            return n, i


def _chunk_names(z: zipfile.ZipFile) -> list[str]:
    names = [n for n in z.namelist() if re.fullmatch(r"c\d+\.flashback", n)]
    return sorted(names, key=lambda n: int(n[1:-len(".flashback")]))


def _protocol(z: zipfile.ZipFile) -> int | None:
    try:
        v = json.loads(z.read("metadata.json")).get("protocol_version")
        return int(v) if v is not None else None
    except (KeyError, ValueError, TypeError):
        return None


CLOCK_PROBE_TICKS = 600     # the world clock's packet is found in the first 30 s


# entities not worth following (things that fly past, decorations that never move, effects)
SKIP_ENTITIES = {"item", "arrow", "spectral_arrow", "trident", "snowball", "egg", "ender_pearl",
                 "experience_bottle", "potion", "splash_potion", "lingering_potion", "fishing_bobber",
                 "area_effect_cloud", "marker", "interaction", "lightning_bolt", "firework_rocket",
                 "eye_of_ender", "fireball", "small_fireball", "dragon_fireball", "wither_skull", "shulker_bullet",
                 "llama_spit", "evoker_fangs", "wind_charge", "breeze_wind_charge", "leash_knot", "painting",
                 "item_frame", "glow_item_frame", "ominous_item_spawner", "end_crystal"}
# things thrown or shot, followed every tick of their short flight (and shown as their item): entity -> item
PROJECTILES = {"egg": "egg", "snowball": "snowball", "ender_pearl": "ender_pearl", "arrow": "arrow",
               "spectral_arrow": "spectral_arrow", "trident": "trident", "experience_bottle": "experience_bottle",
               "splash_potion": "splash_potion", "lingering_potion": "lingering_potion", "potion": "splash_potion",
               "fireball": "fire_charge", "small_fireball": "fire_charge", "wind_charge": "wind_charge",
               "breeze_wind_charge": "wind_charge", "firework_rocket": "firework_rocket", "eye_of_ender": "ender_eye",
               "dragon_fireball": "dragon_breath", "wither_skull": "wither_skeleton_skull", "llama_spit": "",
               "shulker_bullet": "", "fishing_bobber": ""}
MOB_EVERY = 10                # mobs: at most 2 positions a second (players: every one)...
MOB_RADIUS = 48               # ...within this many blocks of the recorded player
MOB_MIN_MOVE = 0.3            # ...and only when they moved this much


def _varlong(b, i: int) -> tuple[int, int]:
    out = shift = 0
    while True:
        x = b[i]
        i += 1
        out |= (x & 0x7F) << shift
        if not x & 0x80:
            return out, i
        shift += 7


# Entity data (set_entity_data): entries (u8 index, VarInt serializer, value) until 0xFF. Serializer numbers differ
# between versions for most kinds, but these low ones have stayed put; the pose's is learned from index 6.
SER_BYTE, SER_VARINT, SER_VARLONG, SER_FLOAT, SER_BOOL, SER_OPT_BLOCK_POS = 0, 1, 2, 3, 8, 11


# particle kinds whose options are one int, a colour (ARGB): the rest of a living thing's effect particles have none
_COLOUR_PARTICLES = {"entity_effect", "flash", "tinted_leaves"}
SER_PARTICLES = 17                                      # a list of particles (a living thing's effect swirls)


def entity_data_all(b, k: int, end: int, holders: bool = False, items: list | None = None,
                    comps: list | None = None, parts: list | None = None) -> dict[int, tuple[int, object]]:
    """``{index: (serializer, value)}`` for every entry of an entity-data packet body we can read past (from *k*,
    after the entity id): bytes, VarInts, floats and booleans with their value, a text component (a mob's name)
    as its text, the rest None. Stops at the first unknown kind; with *holders* (mobs whose only unknown kinds are
    registry entries: cats, wolves, cows...), reads those as one VarInt each and goes on."""
    out: dict[int, tuple[int, object]] = {}
    last = -1
    while k < end:
        idx = b[k]
        if idx == 0xFF or idx <= last or idx > 60:
            break
        last = idx
        ser, k = _varint(b, k + 1)
        if ser == SER_BYTE:
            out[idx] = (ser, b[k])
            k += 1
        elif ser == SER_FLOAT:
            out[idx] = (ser, struct.unpack_from(">f", b, k)[0])
            k += 4
        elif ser == SER_BOOL:
            out[idx] = (ser, bool(b[k]))
            k += 1
        elif ser == SER_VARINT or idx == 6:               # index 6 is the pose (a VarInt) in every version
            v, k = _varint(b, k)
            out[idx] = (SER_VARINT, v)
        elif ser == SER_VARLONG:
            v, k = _varlong(b, k)
            out[idx] = (ser, v - (1 << 64) if v >= 1 << 63 else v)   # (signed: -1 is "none")
        elif ser == SER_OPT_BLOCK_POS:
            k += 9 if b[k] else 1
            out[idx] = (ser, None)
        elif ser == 4:                                    # a string: skipped
            n, k = _varint(b, k)
            k += n
            out[idx] = (ser, None)
        elif ser == 5:                                    # a text component (network NBT)
            txt, k = _text_of(b, k)
            out[idx] = (ser, txt)
        elif ser == 6:                                    # an optional text component (a mob's name, index 2)
            if b[k]:
                txt, k = _text_of(b, k + 1)
            else:
                txt, k = "", k + 1
            out[idx] = (ser, txt)
        elif ser == 9:                                    # rotations: three floats (degrees; armour stands' poses)
            out[idx] = (ser, tuple(round(x, 2) for x in struct.unpack_from(">fff", b, k)))
            k += 12
        elif ser == 10:                                   # a block position
            k += 8
            out[idx] = (ser, None)
        elif ser == SER_ITEM_STACK and items is not None:  # an item (an item frame's)
            st, k = item_stack(b, k, items, comps or [])
            out[idx] = (ser, st)
        elif ser in (12, 14, 15, 19, 20):                 # direction, block state, optional ones, pose: a VarInt
            v, k = _varint(b, k)
            out[idx] = (ser, v)
        elif ser == 13:                                   # an optional entity (its UUID): an owner
            k += 17 if b[k] else 1
            out[idx] = (ser, None)
        elif ser == SER_PARTICLES and parts is not None:  # effect particles: [[name, colour or None], ...]
            n, k = _varint(b, k)
            lst = []
            for _ in range(min(n, 64)):
                t, k = _varint(b, k)
                name = parts[t] if 0 <= t < len(parts) else ""
                col = None
                if name in _COLOUR_PARTICLES:
                    col = struct.unpack_from(">I", b, k)[0]
                    k += 4
                lst.append([name, col])
            out[idx] = (ser, lst)
        elif ser == 18:                                   # villager data: type, profession, level
            t, k = _varint(b, k)
            pr, k = _varint(b, k)
            lv, k = _varint(b, k)
            out[idx] = (ser, ("villager", t, pr, lv))
        else:                                             # a kind not known here: its first VarInt (a registry
            v, k2 = _varint(b, k)                         # entry's id for mob variants), then stop
            out[idx] = (ser, ("?", v))
            if not holders:
                break
            k = k2
    return out


HANGING = {"painting", "item_frame", "glow_item_frame"}
# entity events the game answers with particles round the mob: taming failed (smoke) 6, tamed (hearts) 7, a villager's
# hearts 12 / anger 13 / happiness 14, an animal in love 18, a mob appearing from a spawner 20, a body vanishing 60;
# and a sheep eating grass 10 (its head goes down)
ENTITY_FX = {6, 7, 10, 12, 13, 14, 18, 20, 60, 4, 8, 11, 34}   # and 4 an attack (iron golems, ravagers...),
# 8 a wolf shaking itself dry, 11 an iron golem holding out a poppy, 34 putting it away
BOSS_COLOURS = ["pink", "blue", "red", "green", "yellow", "purple", "white"]
# where a neutral mob's anger is in its entity data (1.21.x; 26.x one on): wolves 21, bees 18
ANGER_INDEX = {"wolf": 21, "bee": 18}


def mob_anim(kind: str, d: dict, new_ids: bool, block_names, tbl=None) -> list | None:
    """What a mob's entity data says about how it moves: a creeper swelling ("cs": +1 / -1) and charged ("cp"), an
    enderman's carried block ("eb": its name or None) and its stare ("ec"), a ghast about to shoot ("gc"), a wolf's
    head tilt ("wi"), a horse's flags ("hf": 0x10 eating, 0x20 rearing, 0x40 mouth open). Animals' own data is one
    index on in 26.x."""
    out = []
    sv = lambda v: v - (1 << 32) if isinstance(v, int) and v >= 1 << 31 else v
    if kind == "creeper":
        if d.get(16, (None,))[0] == SER_VARINT:
            out.append(["cs", sv(d[16][1])])
        if d.get(17, (None,))[0] == SER_BOOL:
            out.append(["cp", int(d[17][1])])
    elif kind == "enderman":
        e = d.get(16)
        if e and e[0] == 15:                              # an optional block state: 0 none, else its id
            if not e[1]:
                out.append(["eb", None])
            elif block_names is not None:
                nm = block_names[e[1]] if 0 <= e[1] < len(block_names) else ""
                out.append(["eb", nm.split("[")[0].replace("minecraft:", "") or None])
            else:
                return [["eb", "?"]]                      # (the caller reads the block names, then asks again)
        if d.get(17, (None,))[0] == SER_BOOL:
            out.append(["ec", int(d[17][1])])
    elif kind == "shulker":                             # the face it's attached to, how far it peeks (0-100), its dye
        if d.get(16, (None,))[0] == 12:
            out.append(["sha", d[16][1]])
        if d.get(17, (None,))[0] == SER_BYTE:
            out.append(["spk", d[17][1]])
        if d.get(18, (None,))[0] == SER_BYTE:
            out.append(["sco", d[18][1]])
    elif kind == "ghast":
        if d.get(16, (None,))[0] == SER_BOOL:
            out.append(["gc", int(d[16][1])])
    elif kind == "wolf":
        i = 20 if new_ids else 19
        if d.get(i, (None,))[0] == SER_BOOL:
            out.append(["wi", int(d[i][1])])
    elif kind in ("horse", "donkey", "mule", "skeleton_horse", "zombie_horse", "camel", "llama", "trader_llama"):
        i = 18 if new_ids else 17
        if d.get(i, (None,))[0] == SER_BYTE:
            out.append(["hf", d[i][1]])
    return out


def boss_event(b, k: int, end: int, bars: dict) -> bool:
    """Apply a boss_event body to *bars* (``{id: {"title", "health", "color", "notches", "flags"}}``): the bar's UUID,
    then the action (0 add: title, health, colour, notches, flags; 1 remove; 2 health; 3 title; 4 colour and
    notches; 5 flags). True when it changed something."""
    bid = bytes(b[k:k + 16]).hex()[:12]
    act, k = _varint(b, k + 16)
    if act == 1:
        return bars.pop(bid, None) is not None
    if act == 0:
        title, k = component_at(b, k)
        health = struct.unpack_from(">f", b, k)[0]
        col, k = _varint(b, k + 4)
        notch, k = _varint(b, k)
        bars[bid] = {"title": title, "health": round(health, 4), "color": BOSS_COLOURS[col] if 0 <= col < 7 else "white",
                     "notches": notch, "flags": b[k] if k < end else 0}
        return True
    bar = bars.get(bid)
    if bar is None:
        return False
    if act == 2:
        bar["health"] = round(struct.unpack_from(">f", b, k)[0], 4)
    elif act == 3:
        bar["title"] = component_at(b, k)[0]
    elif act == 4:
        col, k = _varint(b, k)
        bar["color"] = BOSS_COLOURS[col] if 0 <= col < 7 else "white"
        bar["notches"] = _varint(b, k)[0]
    elif act == 5:
        bar["flags"] = b[k]
    else:
        return False
    return True
# level events the game shows with particles (LevelEvent): bone meal 1505, composter 1500, lava fizz 1501, torch
# burnt out 1502, eye in a portal frame 1503, smoke 2000 / 2010, potion splash 2002 / 2007 (data: colour), ender eye
# breaking 2003, spawner 2004, dragon breath 2006, explosion 2008, steam 2009, wax on / off 3003 / 3004, scrape 3005,
# trial spawner 3011 / 3012 / 3013 ... (None: every one, for looking)
LEVEL_FX = {1009, 1500, 1501, 1502, 1503, 1505, 2000, 2002, 2003, 2004, 2006, 2007, 2008, 2009, 2010, 3003, 3004, 3005}
# Mobs that can be babies (index 16, a boolean: ageable animals and villagers, the zombie family)
BABY_KINDS = {"cow", "mooshroom", "pig", "sheep", "chicken", "horse", "donkey", "mule", "llama", "trader_llama",
              "skeleton_horse", "zombie_horse", "camel", "villager", "wolf", "cat", "ocelot", "fox", "rabbit", "panda",
              "polar_bear", "turtle", "goat", "bee", "hoglin", "zoglin", "axolotl", "frog", "sniffer", "armadillo",
              "strider", "dolphin", "squid", "glow_squid", "parrot", "zombie", "husk", "drowned", "zombie_villager",
              "zombified_piglin", "happy_ghast", "nautilus"}


# mobs whose variant is an entry of a registry the recording sends at its start (missing: the default, temperate /
# tabby / pale)
VARIANT_REG = {"cat": "cat_variant", "wolf": "wolf_variant", "frog": "frog_variant", "pig": "pig_variant",
               "chicken": "chicken_variant", "cow": "cow_variant", "zombie_nautilus": "zombie_nautilus_variant"}
# villagers' types and jobs: built-in registries, in the game's order
VILLAGER_TYPES = ["desert", "jungle", "plains", "savanna", "snow", "swamp", "taiga"]
PROFESSIONS = ["none", "armorer", "butcher", "cartographer", "cleric", "farmer", "fisherman", "fletcher",
               "leatherworker", "librarian", "mason", "nitwit", "shepherd", "toolsmith", "weaponsmith"]


def sign_written(body: bytes) -> list | None:
    """``[x, y, z, front, back]`` from a block_entity_data body (block position, VarInt type, network NBT) that
    holds a sign's text; None for anything else."""
    if len(body) < 10:
        return None
    pos = struct.unpack_from(">q", body, 0)[0]
    _t, k = _varint(body, 8)
    if k >= len(body) or body[k] != 10 or b"_text" not in body[k:]:
        return None
    try:
        from . import nbt as nbt_mod, signs as signs_mod
        from .moment import block_pos
        be = nbt_mod.loads(b"\x0a\x00\x00" + body[k + 1:])
        txt = signs_mod.text_of(be)
    except Exception:                                   # not a sign, or something unexpected: skipped
        return None
    if not txt["front"] and not txt["back"]:
        return None
    return list(block_pos(pos)) + [txt["front"], txt["back"]]


def particles_sent(body: bytes, names: list, blocks: list | None = None) -> list | None:
    """``[x, y, z, spread x, y, z, speed, count, particle, colour or None]`` from a level_particles body: two flags
    (override limiter, always show), the place (doubles), the spread and speed (floats), the count (int), then the
    particle type (and its options: a colour for dust and entity effects)."""
    try:
        if len(body) < 2 + 24 + 16 + 4 + 1:
            return None
        k = 2
        x, y, z = struct.unpack_from(">ddd", body, k)
        k += 24
        dx, dy, dz, sp = struct.unpack_from(">ffff", body, k)
        k += 16
        n = struct.unpack_from(">i", body, k)[0]
        k += 4
        t, k = _varint(body, k)
        name = names[t] if 0 <= t < len(names) else ""
        col = None
        if name in ("block", "falling_dust", "block_marker", "dust_pillar", "block_crumble") and blocks is not None:
            sid = _varint(body, k)[0]                    # which block (its state): the dust is its picture
            col = blocks[sid].split("[")[0].replace("minecraft:", "") if 0 <= sid < len(blocks) else None
        elif name in ("dust", "entity_effect", "tinted_leaves", "flash") and k + 4 <= len(body):
            col = struct.unpack_from(">i", body, k)[0] & 0xFFFFFF
        elif name == "dust_color_transition" and k + 4 <= len(body):
            col = struct.unpack_from(">i", body, k)[0] & 0xFFFFFF
        return [round(x, 3), round(y, 3), round(z, 3), round(dx, 3), round(dy, 3), round(dz, 3), round(sp, 4),
                max(0, min(n, 200)), name, col]
    except (IndexError, ValueError, struct.error):
        return None


def game_modes(body: bytes) -> list[tuple[bytes, str | None, int]]:
    """``[(UUID, name or None, game mode)]`` from a player_info_update body: an action set (one byte: add player,
    chat session, game mode, listed, latency, display name, list order, hat), then each player's UUID and the data of
    each action set. Only entries with a game mode; stops at anything it can't read."""
    out = []
    try:
        acts = body[0]
        n, k = _varint(body, 1)
        for _ in range(min(n, 500)):
            uuid = bytes(body[k:k + 16])
            k += 16
            name, mode = None, None
            if acts & 0x01:                                # add player: name, properties
                name, k = _string(body, k)
                cnt, k = _varint(body, k)
                for _ in range(cnt):
                    k = _string(body, k)[1]
                    k = _string(body, k)[1]
                    if body[k]:
                        k = _string(body, k + 1)[1]
                    else:
                        k += 1
            if acts & 0x02:                                # chat session (optional)
                if body[k]:
                    k += 1 + 16 + 8
                    ln, k = _varint(body, k)
                    k += ln
                    ln, k = _varint(body, k)
                    k += ln
                else:
                    k += 1
            if acts & 0x04:
                mode, k = _varint(body, k)
            if acts & 0x08:
                k += 1
            if acts & 0x10:
                k = _varint(body, k)[1]
            if acts & 0x20:
                k = _nbt_skip(body, k + 1) if body[k] else k + 1
            if acts & 0x40:
                k = _varint(body, k)[1]
            if acts & 0x80:
                k += 1
            if mode is not None:
                out.append((uuid, name, mode))
    except (IndexError, ValueError, struct.error):
        pass
    return out


def scale_of(body: bytes, attrs: list) -> tuple[int, float] | None:
    """``(entity id, scale)`` from an update_attributes body when it carries the scale attribute: its base value
    with its modifiers applied as the game does (add; add base x; multiply total)."""
    try:
        eid, k = _varint(body, 0)
        n, k = _varint(body, k)
        for _ in range(min(n, 64)):
            aid, k = _varint(body, k)
            base = struct.unpack_from(">d", body, k)[0]
            k += 8
            m, k = _varint(body, k)
            mods = []
            for _ in range(m):
                k = _string(body, k)[1]
                amt = struct.unpack_from(">d", body, k)[0]
                mods.append((amt, body[k + 8]))
                k += 9
            if 0 <= aid < len(attrs) and attrs[aid] == "scale":
                v = base + sum(a for a, op in mods if op == 0)
                v += base * sum(a for a, op in mods if op == 1)
                for a, op in mods:
                    if op == 2:
                        v *= 1 + a
                return eid, round(max(0.0625, min(16.0, v)), 4)
    except (IndexError, ValueError, struct.error):
        return None
    return None


def mob_details(kind: str, d: dict, new_ids: bool) -> list[list]:
    """What a mob's entity data says that changes how it looks: ``["baby", 0/1]``, ``["wool", byte]`` (a sheep:
    colour in the low 4 bits, 0x10 sheared), ``["variant", n]`` (a horse: coat + 256 x markings), ``["size", n]`` (slimes, magma cubes), ``["name", text]`` (a name
    tag; "" when taken off). *new_ids*: 26.x, where animals' own entries are one index on (17 -> 18)."""
    out = []
    if kind in BABY_KINDS and d.get(16, (None,))[0] == SER_BOOL:
        out.append(["baby", int(d[16][1])])
    if kind == "sheep":
        w = d.get(18 if new_ids else 17)
        if w and w[0] == SER_BYTE:
            out.append(["wool", w[1]])
    if kind == "horse":                                # coat (low byte) and markings (next byte)
        v = d.get(19 if new_ids else 18)
        if v and v[0] == SER_VARINT:
            out.append(["variant", v[1]])
    if kind in ("slime", "magma_cube") and d.get(16, (None,))[0] == SER_VARINT:
        out.append(["size", d[16][1]])
    if kind in VARIANT_REG:                            # the first registry entry in its data: its variant
        for idx in sorted(d):
            ser, v = d[idx]
            if idx >= 17 and isinstance(v, tuple) and v[0] == "?":
                names = REGISTRIES.get(VARIANT_REG[kind]) or []
                if 0 <= v[1] < len(names):
                    out.append(["variant", names[v[1]].split(":", 1)[-1]])
                break
    if kind.endswith(("_boat", "_raft")):               # rowing: the left paddle (11), the right one (12)
        for idx, key in ((11, "pl"), (12, "pr")):
            if d.get(idx, (None,))[0] == SER_BOOL:
                out.append([key, int(d[idx][1])])
    if kind == "copper_golem":                         # its copper's age: 0 new, 1 exposed, 2 weathered, 3 oxidized
        for idx in sorted(d):
            v = d[idx][1]
            if idx >= 16 and isinstance(v, tuple) and v[0] == "?":
                if 0 <= v[1] <= 3:
                    out.append(["weather", v[1]])
                break
    if kind in ("cat", "wolf"):                        # tamed (0x04; 0x01 sitting) and the collar's dye (default red)
        f = d.get(18 if new_ids else 17)
        if f and f[0] == SER_BYTE:
            out.append(["tame", f[1]])
        c = d.get((23 if new_ids else 22) if kind == "cat" else (21 if new_ids else 20))
        if c and c[0] == SER_VARINT:
            out.append(["collar", c[1]])
    for idx, (ser, v) in d.items():                     # villagers: their biome's clothes, job and level
        if isinstance(v, tuple) and v[0] == "villager":
            out.append(["vd", [VILLAGER_TYPES[v[1]] if v[1] < len(VILLAGER_TYPES) else "plains",
                               PROFESSIONS[v[2]] if v[2] < len(PROFESSIONS) else "none", v[3]]])
    if kind in ("arrow", "spectral_arrow", "trident") and d.get(10, (None,))[0] == SER_BOOL:
        out.append(["ig", int(d[10][1])])               # stuck in a block (or flying again)
    if kind == "armor_stand":                          # 0x01 small, 0x04 arms, 0x08 no base plate; poses in degrees
        if d.get(15, (None,))[0] == SER_BYTE:
            out.append(["asf", d[15][1]])
        poses = {str(i): list(d[i][1]) for i in range(16, 22) if d.get(i, (None, None))[0] == 9 and d[i][1]}
        if poses:
            out.append(["asp", poses])
    if d.get(2, (None,))[0] == 6 and isinstance(d[2][1], str):
        out.append(["name", d[2][1]])
    return out


def entity_data(b, k: int, end: int) -> dict[int, int | float]:
    """``{index: value}`` for the entries of an entity-data packet body we use for players and mobs alike: flags
    (0), pose (6), living flags / hand use (8, a byte), health (9, a float)."""
    out: dict[int, int | float] = {}
    for idx, (ser, v) in entity_data_all(b, k, end).items():
        if (idx == 0 and ser == SER_BYTE) or idx == 6 or (idx == 8 and ser == SER_BYTE) or (idx == 9 and ser == SER_FLOAT):
            out[idx] = v
    return out


def _text_of(b, k: int) -> tuple[str, int]:
    """A network-NBT text component's plain text (a string, or a compound's "text" and its "extra" parts' text)
    and where it ends."""
    from . import terrain
    t = b[k]
    if t == 8:
        return terrain._value(b, 8, k + 1)
    if t == 10:
        return _text_compound(b, k + 1)
    return "", _nbt_skip(b, k)


def nbt_any(b, t: int, pos: int, depth: int = 0):
    """Any network-NBT value of tag *t* at *pos* as plain Python (compounds as dicts, lists as lists) and where it
    ends."""
    from . import terrain
    if depth > 40:
        raise ValueError("NBT too deep")
    if t == 10:
        out = {}
        while b[pos] != 0:
            tt = b[pos]
            n = struct.unpack_from(">H", b, pos + 1)[0]
            name = b[pos + 3:pos + 3 + n].decode("utf-8", "replace")
            out[name], pos = nbt_any(b, tt, pos + 3 + n, depth + 1)
        return out, pos + 1
    if t == 9:
        et, n = b[pos], struct.unpack_from(">i", b, pos + 1)[0]
        pos += 5
        lst = []
        for _ in range(max(0, min(n, 10000))):
            v, pos = nbt_any(b, et, pos, depth + 1)
            lst.append(v)
        return lst, pos
    if t == 5:
        return round(struct.unpack_from(">f", b, pos)[0], 4), pos + 4
    if t == 6:
        return struct.unpack_from(">d", b, pos)[0], pos + 8
    return terrain._value(b, t, pos)


def _component_clean(c):
    """A text component as JSON text would have it: a compound list entry wrapped as {"": value} unwrapped, bytes as
    booleans for the style flags."""
    if isinstance(c, dict):
        if set(c) == {""}:
            return _component_clean(c[""])
        out = {}
        for k, v in c.items():
            if k in ("bold", "italic", "underlined", "strikethrough", "obfuscated") and isinstance(v, int):
                v = bool(v)
            out[k] = _component_clean(v) if isinstance(v, (dict, list)) else v
        return out
    if isinstance(c, list):
        return [_component_clean(x) for x in c]
    return c


def component_at(b, k: int):
    """A network-NBT text component (a string or a compound, with colours, translation keys and parts) and where it
    ends."""
    v, k = nbt_any(b, b[k], k + 1)
    return _component_clean(v), k


def _text_compound(b, pos: int) -> tuple[str, int]:
    """The text of a text component's compound payload (from just after its type byte) and where it ends."""
    from . import terrain
    head, tail = "", []
    while b[pos] != 0:
        tt = b[pos]
        n = struct.unpack_from(">H", b, pos + 1)[0]
        name = b[pos + 3:pos + 3 + n].decode("utf-8", "replace")
        pos += 3 + n
        if tt == 8 and name == "text":
            head, pos = terrain._value(b, 8, pos)
        elif tt == 9 and name == "extra":             # a list of components (or strings)
            et = b[pos]
            cnt = struct.unpack_from(">i", b, pos + 1)[0]
            pos += 5
            for _ in range(max(0, cnt)):
                if et == 8:
                    v, pos = terrain._value(b, 8, pos)
                elif et == 10:
                    v, pos = _text_compound(b, pos)
                else:
                    v, pos = "", terrain._skip(b, et, pos)
                tail.append(v)
        else:
            pos = terrain._skip(b, tt, pos)
    return head + "".join(tail), pos + 1


# Held items and armour (set_equipment): entity id, then (slot byte, 0x80 = another follows; item stack) entries.
# An item stack is VarInt count (0 = empty), VarInt item id, VarInt components added, VarInt removed, then each added
# component (VarInt type, value) and each removed type. Component values differ by kind; the common ones are read
# here by name (from the version's data_component_type registry). At an unknown kind the rest of that packet is
# skipped (the main hand comes first, so what's held is always read).
SLOTS = ("mainhand", "offhand", "feet", "legs", "chest", "head", "body", "saddle")
_VARINT_COMPONENTS = {"max_stack_size", "max_damage", "damage", "rarity", "repair_cost", "enchantable", "map_id",
                      "ominous_bottle_amplifier"}
DYES = ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray", "light_gray", "cyan", "purple",
        "blue", "brown", "green", "red", "black")
# the recording's own registries (from its configuration packets), for components that name entries by number
REGISTRIES: dict = {}


def registry_names(packet: bytes, registry: str) -> list[str] | None:
    """A registry's entry names in network id order, if *packet* (a configuration packet: id, then the registry's
    name and entries) is that registry's data; else None."""
    try:
        k = 1
        ln, k = _varint(packet, k)
        if packet[k:k + ln].decode("utf-8", "replace") != registry:
            return None
        k += ln
        n, k = _varint(packet, k)
        out = []
        for _ in range(n):
            ln, k = _varint(packet, k)
            out.append(packet[k:k + ln].decode("utf-8", "replace"))
            k += ln
            has = packet[k]
            k += 1
            if has:
                k = _nbt_skip(packet, k)
        return out
    except (IndexError, ValueError, struct.error):
        return None
_UNIT_COMPONENTS = {"unbreakable", "glider", "intangible_projectile", "creative_slot_lock"}


def _nbt_skip(b, k: int) -> int:
    """Skip a network NBT value (a type byte, then the payload without a name)."""
    from . import terrain
    t = b[k]
    return k + 1 if t == 0 else terrain._skip(b, t, k + 1)


def _string(b, k: int) -> tuple[str, int]:
    n, k = _varint(b, k)
    return b[k:k + n].decode("utf-8", "replace"), k + n


def _component(name: str, b, k: int) -> tuple[int, object]:
    """Skip one component value; returns (new position, something worth keeping or None). ValueError if unknown."""
    if name == "map_id":                                # a filled map's number (its picture)
        v, k = _varint(b, k)
        return k, ("map", v)
    if name in _VARINT_COMPONENTS:
        return _varint(b, k)[1], None
    if name in _UNIT_COMPONENTS:
        return k, None
    if name == "base_color":                            # a shield's (or banner item's) base dye
        v, k = _varint(b, k)
        return k, DYES[v % 16]
    if name == "banner_patterns":                       # layers: a pattern (registry entry, or 0 + written out), a dye
        names = REGISTRIES.get("banner_pattern") or []
        n, k = _varint(b, k)
        out = []
        for _ in range(min(n, 64)):
            h, k = _varint(b, k)
            if h == 0:
                asset, k = _string(b, k)
                k = _string(b, k)[1]                    # its translation key
                pat = asset
            else:
                pat = names[h - 1] if 0 <= h - 1 < len(names) else f"#{h - 1}"
            d, k = _varint(b, k)
            out.append([pat.split(":")[-1], DYES[d % 16]])
        return k, out
    if name == "enchantment_glint_override":
        return k + 1, ("glint", bool(b[k]))
    if name in ("dyed_color", "map_color"):
        return k + 4, struct.unpack_from(">i", b, k)[0] & 0xFFFFFF
    if name in ("item_model", "tooltip_style"):
        s, k = _string(b, k)
        return k, s
    if name in ("custom_name", "item_name", "custom_data"):
        return _nbt_skip(b, k), None
    if name == "lore":
        n, k = _varint(b, k)
        for _ in range(n):
            k = _nbt_skip(b, k)
        return k, None
    if name in ("enchantments", "stored_enchantments"):
        n, k = _varint(b, k)
        for _ in range(n):
            k = _varint(b, _varint(b, k)[1])[1]
        return k, ("glint", n > 0)
    if name == "custom_model_data":                     # floats, flags, strings, colours
        n, k = _varint(b, k)
        k += 4 * n
        n, k = _varint(b, k)
        k += n
        n, k = _varint(b, k)
        for _ in range(n):
            k = _string(b, k)[1]
        n, k = _varint(b, k)
        return k + 4 * n, None
    if name == "tooltip_display":
        n, k = _varint(b, k + 1)
        for _ in range(n):
            k = _varint(b, k)[1]
        return k, None
    if name == "trim":                                  # material and pattern: registry holders (id + 1; 0 = written out)
        got = []
        for reg in ("trim_material", "trim_pattern"):
            h, k = _varint(b, k)
            if h == 0:
                raise ValueError("inline trim")
            names = REGISTRIES.get(reg) or []
            got.append(names[h - 1].split(":", 1)[-1] if 0 <= h - 1 < len(names) else None)
        return k, ("trim", got if all(got) else None)
    raise ValueError(name)


def equipment(b, k: int, end: int, items: list, comps: list) -> list[list]:
    """``[[slot, item, item_model or "", dye colour or None], ...]`` from a set_equipment body (after the entity id);
    an empty slot has item "". Stops (keeping what was read) at a component it can't read."""
    out = []
    more = True
    while more and k < end:
        slot = b[k]
        more = bool(slot & 0x80)
        k += 1
        name = SLOTS[slot & 0x7F] if (slot & 0x7F) < len(SLOTS) else str(slot & 0x7F)
        got, k = item_stack(b, k, items, comps)
        out.append([name] + got[:3] + ([got[4]] if got[4] else []))   # + the banner look of a shield or banner
        if got[3]:                                     # a component it couldn't read: the rest is unknown
            break
    return out


def item_stack(b, k: int, items: list, comps: list) -> tuple[list, int]:
    """An item stack: ``([item ("" = empty), item_model or "", dye colour or None, stopped early, banner look or
    None], new position)``; the banner look (shields, banners) is ``{"base": dye, "patterns": [[pattern, dye]]}``."""
    count, k = _varint(b, k)
    if count <= 0:
        return ["", "", None, False, None], k
    iid, k = _varint(b, k)
    item = items[iid] if 0 <= iid < len(items) else f"#{iid}"
    add, k = _varint(b, k)
    rem, k = _varint(b, k)
    model, colour, look = "", None, None
    try:
        for _ in range(add):
            ct, k = _varint(b, k)
            cname = comps[ct] if 0 <= ct < len(comps) else ""
            k, val = _component(cname, b, k)
            if cname == "item_model" and val:
                model = val
            elif cname == "dyed_color":
                colour = val
            elif cname == "base_color":
                look = {**(look or {}), "base": val}
            elif cname == "banner_patterns" and val:
                look = {**(look or {}), "patterns": val}
            elif isinstance(val, tuple) and val[0] == "glint" and cname != "enchantment_glint_override":
                if val[1] and "glint" not in (look or {}):
                    look = {**(look or {}), "glint": True}
            elif isinstance(val, tuple) and val[0] == "glint":
                look = {**(look or {}), "glint": val[1]}
            elif isinstance(val, tuple) and val[0] == "map":
                look = {**(look or {}), "map": val[1]}
            elif isinstance(val, tuple) and val[0] == "trim" and val[1]:
                look = {**(look or {}), "trim": val[1]}          # [material, pattern]
        for _ in range(rem):
            k = _varint(b, k)[1]
    except (ValueError, IndexError, struct.error):
        return [item, model, colour, True, look], k
    return [item, model, colour, False, look], k


SER_OPT_COMPONENT, SER_ITEM_STACK = 6, 7


def dropped_item(b, k: int, end: int, items: list, comps: list) -> str | None:
    """The item of a dropped item from its entity data (index 8, an item stack), if this packet has it."""
    last = -1
    try:
        while k < end:
            idx = b[k]
            if idx == 0xFF or idx <= last or idx > 60:
                return None
            last = idx
            ser, k = _varint(b, k + 1)
            if idx == 8 and ser == SER_ITEM_STACK:
                return item_stack(b, k, items, comps)[0][0] or None
            if ser == SER_BYTE or ser == SER_BOOL:
                k += 1
            elif ser == SER_FLOAT:
                k += 4
            elif ser == SER_VARINT or idx == 6:
                k = _varint(b, k)[1]
            elif ser == SER_VARLONG:
                k = _varlong(b, k)[1]
            elif ser == SER_OPT_COMPONENT:
                k = _nbt_skip(b, k + 1) if b[k] else k + 1
            else:
                return None
    except (IndexError, struct.error, ValueError):
        return None
    return None


def _followed(kind: str) -> bool:
    if kind == "item" or kind in PROJECTILES:          # dropped items (their item: entity data), things thrown
        return True
    return bool(kind) and kind not in SKIP_ENTITIES and not kind.endswith(("_display", "_arrow", "_potion"))


def decode_entities(zip_path: str, players: dict[bytes, str] | None = None, every: int = SAMPLE_TICKS,
                    min_move: float = MIN_MOVE, clock: list | None = None, full: bool = False,
                    extras: dict | None = None) -> Iterator[tuple]:
    """Yield ``(tick, who, dimension, x, y, z, yaw, pitch)``, sampled about once a second (every *every* ticks,
    when moved at least *min_move* blocks; *full*: also ``head yaw, on ground``).

    ``who`` is None for the recorded player, else the name of another player in view (from
    *players*: ``{16-byte UUID: name}``). With *clock* (a list), the recording's world-clock breakpoints
    ``[tick, gameTime]`` are added to it (see the module docstring). With *extras* (a dict), mobs and pets in view
    are followed too (``who`` = ``"#<type>#<entity id>"``), and ``extras["events"]`` gets ``[tick, who, kind,
    value]``: ``"f"`` a player's flags (0x02 sneaking, 0x08 sprinting, 0x10 swimming, 0x80 elytra flight) and ``"s"``
    an arm swing. Packet ids come from :mod:`mcsm.protocol`. Raises ``ValueError`` for files that aren't
    Flashback replays.
    """
    from . import protocol as proto_mod
    players = players or {}
    probe: dict[int, list] = {}          # packet id -> [(tick, first i64)] while looking for the world clock
    time_pid: int | None = None

    def clock_point(t: int, gt: int) -> None:
        if not clock or abs((gt - t) - (clock[-1][1] - clock[-1][0])) > 2:
            clock.append([t, gt])            # the first, and wherever the recording was paused
        clock_last[:] = [t, gt]
    clock_last: list = []
    with zipfile.ZipFile(zip_path) as z:
        proto = _protocol(z)
        try:
            version = json.loads(z.read("metadata.json")).get("version_string")
        except (KeyError, ValueError):
            version = None
        tbl = proto_mod.table(proto, version) if (extras is not None or proto not in ADD_ENTITY) else None
        pk = (tbl or {}).get("packets") or {}
        types = (tbl or {}).get("entity_types") or []
        add_id = pk.get("add_entity", ADD_ENTITY.get(proto, DEFAULT_ADD_ENTITY))
        remove_id = pk.get("remove_entities", REMOVE_ENTITIES.get(proto))
        ex_on = extras is not None
        data_id = pk.get("set_entity_data") if ex_on else None
        anim_id = pk.get("animate") if ex_on else None
        hurt_ids = {pk.get("damage_event"), pk.get("hurt_animation")} - {None} if ex_on else set()
        event_id = pk.get("entity_event") if ex_on else None
        stime_id = pk.get("set_time") if ex_on else None
        game_id = pk.get("game_event") if ex_on else None
        pass_id = pk.get("set_passengers") if ex_on else None
        equip_id = pk.get("set_equipment") if ex_on and (tbl or {}).get("items") else None
        # the recorded player's own hotbar: every slot when the recording starts and when it changes, and which slot
        # is selected (server-side recordings keep these; other players' hotbars aren't sent to anyone)
        pinv_id = pk.get("set_player_inventory") if equip_id is not None else None
        held_id = pk.get("set_held_slot") if ex_on else None
        bev_id = pk.get("block_event") if ex_on else None
        hp_id = pk.get("set_health") if ex_on else None      # f32 health, varint food, f32 saturation
        swing_id = pk.get("swing_animation") if ex_on else None   # 26.3: entity id, hand (0 main, 1 off)
        link_id = pk.get("set_entity_link") if ex_on else None    # i32 tied entity, i32 holder (0: let go)
        bed_id = pk.get("block_entity_data") if ex_on else None   # block pos, type, NBT (signs' text)
        crack_id = pk.get("block_destruction") if ex_on else None   # breaker, block pos, stage 0-9 (else: stopped)
        take_id = pk.get("take_item_entity") if ex_on else None     # collected, collector, amount
        attr_id = pk.get("update_attributes") if ex_on and (tbl or {}).get("attributes") else None   # sizes
        info_id = pk.get("player_info_update") if ex_on else None    # game modes
        part_id = pk.get("level_particles") if ex_on and (tbl or {}).get("particles") else None   # particles
        lev_id = pk.get("level_event") if ex_on else None          # i32 event, block pos, i32 data, bool
        # text on the recorded player's screen: the action bar, titles, system messages (deaths, joins, commands)
        ab_id = pk.get("set_action_bar_text") if ex_on else None    # a text component
        sys_id = pk.get("system_chat") if ex_on else None           # a text component, overlay (the action bar)
        ti_id = pk.get("set_title_text") if ex_on else None
        st_id = pk.get("set_subtitle_text") if ex_on else None
        ta_id = pk.get("set_titles_animation") if ex_on else None   # i32 fade in, stay, fade out (ticks)
        tc_id = pk.get("clear_titles") if ex_on else None           # bool: reset the times too
        boss_id = pk.get("boss_event") if ex_on else None           # boss bars (the dragon, the wither, raids...)
        xp_id = pk.get("set_experience") if ex_on else None         # f32 bar, VarInt level, VarInt total
        eff_id = pk.get("update_mob_effect") if ex_on and (tbl or {}).get("mob_effects") else None
        reff_id = pk.get("remove_mob_effect") if ex_on and (tbl or {}).get("mob_effects") else None
        my_effects: dict = {}                                       # the recorded player's effects (for the HUD)
        bars: dict = {}
        game_now: tuple | None = None                               # (the world's game time, at which tick)
        block_names: list | None = None                           # block state ids -> names (blocks broken)
        knots: dict[int, tuple] = {}                              # leash knots on fences: where they are
        hangs: dict[int, str] = {}                                # paintings and item frames in view
        new_ids = bool(version) and not str(version).startswith("1.")   # 26.x: animals' own data one index on
        items_of, comps_of = (tbl or {}).get("items") or [], (tbl or {}).get("components") or []
        watch = {x for x in (add_id, remove_id, data_id, anim_id, event_id, stime_id, game_id, pass_id, equip_id,
                             pinv_id, held_id, bev_id, hp_id, swing_id, link_id, bed_id, crack_id, lev_id, take_id,
                             attr_id, info_id, part_id, ab_id, sys_id, ti_id, st_id, ta_id, tc_id, boss_id, xp_id, eff_id, reff_id)
                 if x is not None} | hurt_ids
        kinds_of: dict[int, str] = {}        # every entity's type (vehicles: boats, horses, the sit datapack's seat...)
        riders: dict[int, set] = {}          # vehicle -> who rides it
        if ex_on:
            extras.setdefault("events", [])
            extras.setdefault("mobs", {})
            extras.setdefault("world", [])
        tick = 0
        me: int | None = None
        local: tuple[float, float, float] | None = None
        first_seen: dict[int, tuple[float, float, float]] = {}
        others: dict[int, str] = {}          # entity id -> player name (currently in view)
        cur: dict[int | None, dict] = {}     # None = recorded player
        last_out: dict[int | None, tuple] = {}
        last_move: dict[int, int] = {}
        dim_now = "minecraft:overworld"
        for cn in _chunk_names(z):
            b = z.read(cn)
            if len(b) < 8 or _U32.unpack_from(b, 0)[0] != MAGIC:
                raise ValueError(f"{cn}: not a Flashback chunk")
            i = 4
            count, i = _varint(b, i)
            kinds = []
            for _ in range(count):
                ln, i = _varint(b, i)
                kinds.append(b[i:i + ln].decode("utf-8", "replace").rsplit("/", 1)[-1])
                i += ln
            snap = _U32.unpack_from(b, i)[0]
            i += 4
            nt = kinds.index("next_tick") if "next_tick" in kinds else -1
            mv = kinds.index("move_entities") if "move_entities" in kinds else -1
            cl = kinds.index("create_local_player") if "create_local_player" in kinds else -1
            gp = kinds.index("game_packet") if "game_packet" in kinds else -1
            cf = kinds.index("configuration_packet") if "configuration_packet" in kinds else -1
            # the snapshot (who/what is in view when this file starts) uses the same record format
            for start, end in ((i, i + snap), (i + snap, len(b))):
                i = start
                while i + 5 <= end:
                    aid, i = _varint(b, i)
                    size = _U32.unpack_from(b, i)[0]
                    i += 4
                    if aid == cf and ex_on and size > 20 and (b"banner_pattern" in b[i:i + 64] or b"_variant" in b[i:i + 64]
                                                               or b"trim_" in b[i:i + 64] or b"jukebox_song" in b[i:i + 64]):
                        try:                         # the registry's name: after the packet id, a string
                            ln, kk = _varint(b, i + 1)
                            reg = bytes(b[kk:kk + ln]).decode("utf-8", "replace")
                        except IndexError:
                            reg = ""
                        if reg.startswith("minecraft:") and (reg.endswith("_variant") or reg.endswith("banner_pattern")
                                                             or reg.endswith(("trim_material", "trim_pattern", "jukebox_song"))):
                            names = registry_names(bytes(b[i:i + size]), reg)
                            if names:
                                REGISTRIES[reg.split(":", 1)[1]] = names
                                if extras is not None:          # (a mob's variant is an id into these)
                                    extras.setdefault("registries", {})[reg.split(":", 1)[1]] = names
                    if clock is not None and aid == gp and size >= 9:
                        pid, kk = _varint(b, i) if b[i] & 0x80 else (b[i], i + 1)     # the id, then the packet
                        if pid == time_pid:
                            clock_point(tick, struct.unpack_from(">q", b, kk)[0])
                        elif time_pid is None:
                            lst = probe.setdefault(pid, [])
                            if len(lst) < 40 and kk + 8 <= i + size:
                                lst.append((tick, struct.unpack_from(">q", b, kk)[0]))
                    if aid == nt:
                        tick += 1
                        if clock is not None and time_pid is None and tick == CLOCK_PROBE_TICKS:
                            time_pid = _find_clock(probe, clock_point)
                        if tick % every == 0:
                            yield from _sample(tick, cur, last_out, last_move, others, remove_id, min_move, full)
                    elif aid == gp and size >= 2 and (players or extras is not None) and (b[i] in watch or b[i] & 0x80)                             and _varint(b, i)[0] in watch:
                        # (ids below 128 are one byte: most of the ~40 other packets per tick are skipped cheaply)
                        pid, k = _varint(b, i)
                        if pid == add_id and size >= 43:     # id, entity id, UUID, type, x, y, z
                            eid, k = _varint(b, k)
                            name = players.get(bytes(b[k:k + 16]))
                            if extras is not None and eid != me:
                                t_id, _k = _varint(b, k + 16)
                                kinds_of[eid] = types[t_id] if 0 <= t_id < len(types) else ""
                                if kinds_of[eid] in HANGING and _k + 24 <= i + size:   # a painting or item frame
                                    hx, hy, hz = _XYZ.unpack_from(b, _k)
                                    try:
                                        kv = lp_vec3(b, _k + 24)[1] + 3 if proto is None or proto >= 773 else _k + 27
                                        facing = _varint(b, kv)[0] % 6
                                    except (IndexError, struct.error):
                                        facing = 3
                                    hangs[eid] = f"#{kinds_of[eid]}#{eid}"
                                    extras["events"].append([tick, hangs[eid], "hg", [kinds_of[eid], round(hx, 4), round(hy, 4),
                                                                                       round(hz, 4), facing]])
                                if kinds_of[eid] == "leash_knot" and _k + 24 <= i + size:
                                    knots[eid] = tuple(round(v, 3) for v in struct.unpack_from(">ddd", b, _k))
                            if not name and extras is not None and eid != me:
                                t_id, _k = _varint(b, k + 16)
                                kind = types[t_id] if 0 <= t_id < len(types) else ""
                                if kind != "player" and _followed(kind):
                                    name = f"#{kind}#{eid}"
                                    extras["mobs"][name] = kind
                            if name and eid != me:
                                _t, k2 = _varint(b, k + 16)
                                x, y, zz = _XYZ.unpack_from(b, k2)
                                if name.startswith("#") and name.split("#")[1] in PROJECTILES:   # its launch speed
                                    try:
                                        vel = (lp_vec3(b, k2 + 24)[0] if proto is None or proto >= 773
                                               else _old_velocity(b, k2 + 24))
                                        extras["events"].append([tick, name, "v", [round(v, 4) for v in vel]])
                                    except (IndexError, struct.error):
                                        pass
                                if name.startswith("#falling_block#"):     # which block is falling (the data field)
                                    try:
                                        kv = lp_vec3(b, k2 + 24)[1] + 3 if proto is None or proto >= 773 else k2 + 27
                                        sid = _varint(b, kv)[0]
                                        if block_names is None:
                                            from . import protocol as proto_mod3
                                            block_names = proto_mod3.block_states(tbl) or []
                                        if 0 <= sid < len(block_names):
                                            extras["events"].append([tick, name, "fb", block_names[sid].split("[")[0].replace("minecraft:", "")])
                                    except (IndexError, struct.error):
                                        pass
                                others[eid] = name
                                cur[eid] = {"dim": dim_now, "x": x, "y": y, "z": zz, "yaw": 0.0, "pitch": 0.0, "name": name}
                                last_move[eid] = tick
                        elif pid == data_id and size > 4:     # entity id, then (index, serializer, value)...
                            eid, k = _varint(b, k)
                            if eid in hangs:                  # a painting's picture; an item frame's item and turn
                                # (26.x: the facing first, index 8; then the picture, or the item and its turn)
                                hd = entity_data_all(b, k, i + size, True, items_of, comps_of)
                                if hangs[eid].startswith("#painting#"):
                                    names = REGISTRIES.get("painting_variant") or []
                                    for idx in sorted(hd):
                                        v = hd[idx][1]
                                        if idx >= 8 and isinstance(v, tuple) and v[0] == "?":
                                            # (a holder that may be written out in full: id + 1, 0 = inline)
                                            if 1 <= v[1] <= len(names):
                                                extras["events"].append([tick, hangs[eid], "hv", names[v[1] - 1].split(":", 1)[-1]])
                                            break
                                else:
                                    it = next((i2 for i2 in sorted(hd) if hd[i2][0] == SER_ITEM_STACK), None)
                                    rot = hd.get(it + 1, (None, None)) if it is not None else (None, None)
                                    if it is not None:
                                        st = hd[it][1] or [""]
                                        extras["events"].append([tick, hangs[eid], "hi", [st[0], rot[1] if rot[0] == SER_VARINT else 0,
                                                                 ((st[4] if len(st) > 4 else None) or {}).get("map")]])
                                    elif any(hd[i2][0] == SER_VARINT for i2 in hd if i2 >= 9):   # only turned
                                        r2 = next(hd[i2][1] for i2 in sorted(hd) if i2 >= 9 and hd[i2][0] == SER_VARINT)
                                        extras["events"].append([tick, hangs[eid], "hi", [None, r2]])
                            elif eid in others and others[eid].startswith("#item#"):
                                it = dropped_item(b, k, i + size, items_of, comps_of)
                                if it:
                                    extras["events"].append([tick, others[eid], "it", it])   # which item
                            elif eid == me or eid in others:
                                who = None if eid == me else others[eid]
                                if who and who.startswith("#"):          # a mob: baby, wool, size, name tag
                                    mk = who.split("#")[1]
                                    md = entity_data_all(b, k, i + size, mk in VARIANT_REG or mk == "copper_golem")
                                    for ev in mob_details(mk, md, new_ids):
                                        extras["events"].append([tick, who] + ev)
                                    anim = mob_anim(mk, md, new_ids, block_names if block_names is not None else None, tbl)
                                    if anim and block_names is None and anim[0] == "eb":
                                        from . import protocol as proto_mod5
                                        block_names = proto_mod5.block_states(tbl) or []
                                        anim = mob_anim(mk, md, new_ids, block_names, tbl)
                                    for ev in anim or []:
                                        extras["events"].append([tick, who] + ev)
                                    if mk == "bee":            # bees' flags (0x08 carrying nectar; 0x04 has stung)
                                        bf = md.get(18 if new_ids else 17)
                                        if bf and bf[0] == SER_BYTE:
                                            extras["events"].append([tick, who, "bnf", bf[1]])
                                    if mk in ANGER_INDEX:      # angry: the game time it ends (-1 none) or ticks left
                                        an = md.get(ANGER_INDEX[mk] + (1 if new_ids else 0))
                                        left = None
                                        if an and an[0] == SER_VARLONG and isinstance(an[1], int):
                                            left = 0 if an[1] < 0 or game_now is None else max(0, an[1] - (game_now[0] + tick - game_now[1]))
                                        elif an and an[0] == SER_VARINT:
                                            left = max(0, an[1])
                                        if left is not None:
                                            extras["events"].append([tick, who, "ang", min(left, 1_000_000)])
                                da = entity_data_all(b, k, i + size, parts=tbl.get("particles"))
                                if da.get(10, (None,))[0] == SER_PARTICLES:   # potion swirls: which, and faint (beacon)?
                                    amb = da.get(11, (None, None))
                                    extras["events"].append([tick, who, "ef", [da[10][1], amb[1] if amb[0] == SER_BOOL else None]])
                                if da.get(12, (None,))[0] == SER_VARINT and not (who and any(
                                        x in who for x in ("boat", "raft", "minecart", "arrow", "trident"))):
                                    extras["events"].append([tick, who, "ar", da[12][1]])   # arrows stuck in them
                                if not (who and who.startswith("#")):    # players: air (1, of 300) and frozen ticks (7)
                                    if da.get(1, (None,))[0] == SER_VARINT:
                                        extras["events"].append([tick, who, "air", da[1][1]])
                                    if da.get(7, (None,))[0] == SER_VARINT:
                                        extras["events"].append([tick, who, "fz", da[7][1]])
                                d = entity_data(b, k, i + size)
                                if 0 in d:
                                    extras["events"].append([tick, who, "f", d[0]])    # sneak, sprint, swim, elytra, fire
                                if 6 in d:
                                    extras["events"].append([tick, who, "p", d[6]])    # pose: sleeping, dying, ...
                                if isinstance(d.get(8), int):
                                    extras["events"].append([tick, who, "h", d[8]])    # using an item (eat, bow, shield)
                                if isinstance(d.get(9), float) and d[9] <= 0:
                                    extras["events"].append([tick, who, "d", 1])       # health 0: dying
                        elif pid in hurt_ids and size > 2:    # damage_event / hurt_animation: entity id first
                            eid, k = _varint(b, k)
                            if eid == me or eid in others:
                                extras["events"].append([tick, None if eid == me else others[eid], "hurt", 1])
                        elif pid == event_id and size >= 6:   # entity_event: i32 entity id, status (3 = died)
                            eid = struct.unpack_from(">i", b, k)[0]
                            # (status 3 is also an egg or snowball breaking: only players and mobs die)
                            if b[k + 4] == 3 and (eid == me or (eid in others and not (others[eid].startswith("#")
                                                  and others[eid].split("#")[1] in PROJECTILES))):
                                extras["events"].append([tick, None if eid == me else others[eid], "d", 1])
                            elif b[k + 4] == 35 and (eid == me or eid in others):   # a totem of undying saved them
                                extras["events"].append([tick, None if eid == me else others[eid], "totem", 1])
                            elif b[k + 4] in ENTITY_FX and eid in others and others[eid].startswith("#"):
                                extras["events"].append([tick, others[eid], "ee", b[k + 4]])   # hearts, poofs...
                        elif pid == attr_id and size >= 4:   # attributes: the scale one (a bigger or smaller player or mob)
                            sc = scale_of(bytes(b[k:i + size]), tbl["attributes"])
                            if sc and (sc[0] == me or sc[0] in others):
                                extras["events"].append([tick, None if sc[0] == me else others[sc[0]], "sc", sc[1]])
                        elif pid == part_id and size >= 48:  # particles the server sent (hits, potions, commands...)
                            if block_names is None:
                                from . import protocol as proto_mod4
                                block_names = proto_mod4.block_states(tbl) or []
                            pt = particles_sent(bytes(b[k:i + size]), tbl["particles"], block_names)
                            if pt and pt[8]:
                                extras["events"].append([tick, None, "pt", pt])
                        elif pid in (ab_id, sys_id, ti_id, st_id) and size >= 2:   # text on their screen
                            try:
                                comp, kk = component_at(b, k)
                                kind = {ab_id: "ab", ti_id: "ti", st_id: "st"}.get(pid)
                                if pid == sys_id:
                                    kind = "ab" if kk < i + size and b[kk] else "sm"
                                if len(str(comp)) < 4000:
                                    extras["events"].append([tick, None, kind, comp])
                            except (IndexError, ValueError, struct.error):
                                pass
                        elif pid == boss_id and size >= 17:   # boss bars: every change, all the bars shown
                            try:
                                if boss_event(b, k, i + size, bars):
                                    extras["events"].append([tick, None, "bo", [dict(v, id=key) for key, v in bars.items()]])
                            except (IndexError, ValueError, struct.error):
                                pass
                        elif pid == xp_id and size >= 6:      # their experience bar and level
                            bar = struct.unpack_from(">f", b, k)[0]
                            lvl, kk = _varint(b, k + 4)
                            tot = _varint(b, kk)[0]
                            extras["events"].append([tick, None, "xp", [round(bar, 4), lvl, tot]])
                        elif pid in (eff_id, reff_id) and size >= 2:   # their effects: all of them after each change
                            try:
                                eid, kk = _varint(b, k)
                                if eid == me:
                                    effn, kk = _varint(b, kk)
                                    name = tbl["mob_effects"][effn] if 0 <= effn < len(tbl["mob_effects"]) else str(effn)
                                    if pid == eff_id:
                                        amp, kk = _varint(b, kk)
                                        dur, kk = _varint(b, kk)
                                        my_effects[name] = [name, amp, dur if dur < 2 ** 31 else -1, b[kk] if kk < i + size else 0, tick]
                                    else:
                                        my_effects.pop(name, None)
                                    extras["events"].append([tick, None, "eff", [list(v) for v in my_effects.values()]])
                            except (IndexError, struct.error):
                                pass
                        elif pid == ta_id and size >= 12:
                            extras["events"].append([tick, None, "ta", list(struct.unpack_from(">iii", b, k))])
                        elif pid == tc_id and size >= 1:
                            extras["events"].append([tick, None, "tc", bool(b[k])])
                        elif pid == info_id and size >= 3:   # the tab list: game modes (3 = spectator)
                            for uid, nm, mode in game_modes(bytes(b[k:i + size])):
                                who = players.get(uid) or nm
                                if who:
                                    extras["events"].append([tick, who, "gm", mode])
                        elif pid == take_id and size >= 3:   # picked up: the item (or orb), who by
                            got, kk = _varint(b, k)
                            by, kk = _varint(b, kk)
                            if got in others and others[got].startswith("#") and (by == me or by in others):
                                extras["events"].append([tick, others[got], "pk", None if by == me else others[by]])
                        elif pid == stime_id and size >= 9:   # the time of day (two layouts, see _day_time)
                            dt = _day_time(b, k, i + size)
                            game_now = (struct.unpack_from(">q", b, k)[0], tick)    # the game time comes first
                            if dt is not None:
                                extras["world"].append([tick, "time", dt[0], dt[1]])
                        elif pid == game_id and size >= 6:    # weather: 1 rain starts, 2 stops, 7 rain level, 8 thunder level
                            ev, val = b[k], struct.unpack_from(">f", b, k + 1)[0]
                            if ev in (1, 2):
                                extras["world"].append([tick, "rain", 1.0 if ev == 1 else 0.0, 0])
                            elif ev in (7, 8):
                                extras["world"].append([tick, "rain" if ev == 7 else "thunder", round(val, 3), 0])
                        elif pid == anim_id and size >= 3:    # entity id, action (0 main hand, 3 off hand swing)
                            eid, k = _varint(b, k)
                            if b[k] in (0, 3) and (eid == me or (eid in others and not others[eid].startswith("#"))):
                                extras["events"].append([tick, None if eid == me else others[eid], "s", b[k]])
                        elif pid == equip_id and size >= 3:   # held items and armour (players and mobs)
                            eid, k = _varint(b, k)
                            if eid == me or eid in others:
                                for slot in equipment(b, k, i + size, items_of, comps_of):
                                    extras["events"].append([tick, None if eid == me else others[eid], "eq", slot])
                        elif pid == pinv_id and size >= 2:    # the recorded player's inventory slot (hotbar 0-8)
                            slot, k = _varint(b, k)
                            if 0 <= slot < 9:
                                cnt = _varint(b, k)[0]
                                st, _ = item_stack(b, k, items_of, comps_of)
                                extras["events"].append([tick, None, "hb", [slot, st[0], st[1], st[2], max(0, cnt)]])
                        elif pid == bev_id and size >= 11:   # block pos, action, parameter, block: action 1 = viewers
                            pos = struct.unpack_from(">q", b, k)[0]
                            bid = _varint(b, k + 10)[0] if k + 10 < i + size else -1
                            blocks_tbl = (tbl or {}).get("blocks") or []
                            bname = blocks_tbl[bid][0] if 0 <= bid < len(blocks_tbl) else ""
                            from .moment import block_pos
                            if bname in ("piston", "sticky_piston"):     # 0 pushed out, 1 pulled back; direction
                                if b[k + 8] in (0, 1, 2):
                                    extras["events"].append([tick, None, "pi", list(block_pos(pos)) + [b[k + 8], b[k + 9] % 6,
                                                                                                    bname == "sticky_piston"]])
                            elif b[k + 8] == 1:
                                extras["events"].append([tick, None, "ce", list(block_pos(pos)) + [b[k + 9]]])
                        elif pid == link_id and size >= 9:    # a lead: the tied mob and who (or which knot) holds it
                            tied, holder = struct.unpack_from(">ii", b, k)
                            if tied in others and others[tied].startswith("#"):
                                if holder == me:
                                    val = ["p", None]
                                elif holder in others and not others[holder].startswith("#"):
                                    val = ["p", others[holder]]
                                elif holder in knots:
                                    val = ["k"] + list(knots[holder])
                                elif holder in others:
                                    val = ["m", others[holder]]
                                else:
                                    val = []
                                extras["events"].append([tick, others[tied], "lead", val])
                        elif pid == crack_id and size >= 11:  # a block being mined: its crack's stage
                            _e, kk = _varint(b, k)
                            from .moment import block_pos
                            st = b[kk + 8]
                            extras["events"].append([tick, None, "mc", list(block_pos(struct.unpack_from(">q", b, kk)[0]))
                                                     + [st if st < 10 else -1]])
                        elif pid == lev_id and size >= 17:    # 2001: a block broken (its particles), data: its state id
                            ev = struct.unpack_from(">i", b, k)[0]
                            if ev == 2001:
                                from .moment import block_pos
                                if block_names is None:
                                    from . import protocol as proto_mod2
                                    block_names = proto_mod2.block_states(tbl) or []
                                sid = struct.unpack_from(">i", b, k + 12)[0]
                                nm = block_names[sid] if 0 <= sid < len(block_names) else ""
                                if nm:
                                    extras["events"].append([tick, None, "bb", list(block_pos(struct.unpack_from(">q", b, k + 4)[0]))
                                                             + [nm.split("[")[0].replace("minecraft:", "")]])
                            elif ev in (1010, 1011):          # a jukebox starts (data: the song) or stops: "Now Playing"
                                from .moment import block_pos
                                songs = REGISTRIES.get("jukebox_song") or []
                                sid = struct.unpack_from(">i", b, k + 12)[0]
                                song = (songs[sid].split(":", 1)[-1] if 0 <= sid < len(songs) else None) if ev == 1010 else None
                                extras["events"].append([tick, None, "js", list(block_pos(struct.unpack_from(">q", b, k + 4)[0])) + [song]])
                            elif LEVEL_FX is None or ev in LEVEL_FX:   # other effects the game shows with particles
                                from .moment import block_pos
                                extras["events"].append([tick, None, "le", list(block_pos(struct.unpack_from(">q", b, k + 4)[0]))
                                                         + [ev, struct.unpack_from(">i", b, k + 12)[0]]])
                        elif pid == bed_id and size >= 12:    # a block entity's data: a sign's text written
                            sg = sign_written(bytes(b[k:i + size]))
                            if sg:
                                extras["events"].append([tick, None, "sg", sg])
                        elif pid == swing_id and size >= 3:   # 26.3's arm swing: like animate 0 / 3
                            eid, k = _varint(b, k)
                            if eid == me or (eid in others and not others[eid].startswith("#")):
                                extras["events"].append([tick, None if eid == me else others[eid], "s", 3 if b[k] == 1 else 0])
                        elif pid == hp_id and size >= 7:
                            hp = struct.unpack_from(">f", b, k)[0]
                            food = _varint(b, k + 4)[0]
                            extras["events"].append([tick, None, "hp", [round(hp, 1), food]])
                        elif pid == held_id:                  # which hotbar slot they hold (the smallest packet: 2 bytes)
                            slot = _varint(b, k)[0]
                            if 0 <= slot < 9:
                                extras["events"].append([tick, None, "hs", slot])
                        elif pid == pass_id and size >= 3:    # set_passengers: vehicle, count, riders
                            veh, k = _varint(b, k)
                            n, k = _varint(b, k)
                            now: set = set()
                            for _ in range(min(n, 64)):
                                p_id, k = _varint(b, k)
                                if p_id == me or (p_id in others and not others[p_id].startswith("#")):
                                    now.add(None if p_id == me else others[p_id])
                            before_r = riders.get(veh, set())
                            for who in now - before_r:            # sits down: in a boat, on a horse, on a seat
                                extras["events"].append([tick, who, "r", kinds_of.get(veh, "") or "seat"])
                            for who in before_r - now:            # gets off
                                extras["events"].append([tick, who, "r", ""])
                            riders[veh] = now
                        elif pid == remove_id and (others or riders):
                            n, k = _varint(b, k)
                            for _ in range(min(n, 10_000)):
                                eid, k = _varint(b, k)
                                if eid in hangs:              # out of sight or taken down
                                    extras["events"].append([tick, hangs.pop(eid), "hgx", 1])
                                for who in riders.pop(eid, ()) if extras is not None else ():
                                    extras["events"].append([tick, who, "r", ""])   # the vehicle went away
                                if eid in others:
                                    if extras is not None and others[eid].startswith("#"):
                                        extras["events"].append([tick, others[eid], "gone", 1])   # picked up, killed...
                                    del others[eid]
                                    cur.pop(eid, None)
                                    last_out.pop(eid, None)
                    elif aid == cl and size >= 40 and me is None:
                        local = _XYZ.unpack_from(b, i + 16)
                        first_seen.clear()
                    elif aid == mv:
                        p = b[i:i + size]
                        j = 0
                        levels, j = _varint(p, j)
                        for _ in range(levels):
                            ln, j = _varint(p, j)
                            dim = p[j:j + ln].decode("utf-8", "replace")
                            j += ln
                            n, j = _varint(p, j)
                            for _ in range(n):
                                eid, j = _varint(p, j)
                                x, y, zz, yaw, pitch, hy = _POS.unpack_from(p, j)
                                gr = p[j + _POS.size] if full else 0
                                j += _POS.size + 1
                                if me is None and local is not None and eid not in first_seen:
                                    first_seen[eid] = (x, y, zz)
                                    if abs(x - local[0]) < 0.01 and abs(y - local[1]) < 0.01 and abs(zz - local[2]) < 0.01:
                                        me = eid
                                        others.pop(eid, None)
                                        cur.pop(eid, None)
                                if eid == me:
                                    cur[None] = {"dim": dim, "x": x, "y": y, "z": zz, "yaw": yaw, "pitch": pitch,
                                                 "head": hy, "ground": gr}
                                    dim_now = dim
                                elif eid in others:
                                    cur[eid] = {"dim": dim, "x": x, "y": y, "z": zz, "yaw": yaw, "pitch": pitch,
                                                "head": hy, "ground": gr, "name": others[eid]}
                                    last_move[eid] = tick
                    i += size
        if clock is not None:
            if time_pid is None:
                _find_clock(probe, clock_point)
            if clock_last and clock and clock[-1][0] != clock_last[0]:
                clock.append([clock_last[0], clock_last[1]])     # the last reading: where the recording ends


def _find_clock(probe: dict, clock_point) -> int | None:
    """The packet id of the world clock: its first 8 bytes rise by the ticks passed (allowing a tick either way, as a
    packet can land on either side of a tick). Replays its readings into *clock_point*; None if there's none."""
    best, best_pid = 0.0, None
    for pid, v in probe.items():
        if len(v) < 5:
            continue
        steps = [(t1 - t0, g1 - g0) for (t0, g0), (t1, g1) in zip(v, v[1:]) if t1 > t0]
        if len(steps) < 4:
            continue
        ok = sum(1 for dt, dg in steps if abs(dg - dt) <= 1) / len(steps)
        if ok > best:
            best, best_pid = ok, pid
    if best_pid is None or best < 0.8:
        return None
    for t, g in probe[best_pid]:
        clock_point(t, g)
    return best_pid


def _day_time(b, k: int, end: int) -> tuple[int, float] | None:
    """``(day time in ticks, rate)`` from a set_time body, or None when it carries no day time. Up to 1.21.11: i64
    game time, i64 day time, bool ticking. From 26.1: i64 game time, then a list of world clocks (VarInt count; each
    VarInt clock id, VarLong total ticks, f32 partial tick, f32 rate); clock 0 is the overworld's day (seen in the
    owner's recordings: it jumps to a whole day when someone sleeps)."""
    n = end - k
    if n == 17:
        return struct.unpack_from(">q", b, k + 8)[0], 1.0 if b[k + 16] else 0.0
    if n >= 9:
        count, j = _varint(b, k + 8)
        for _ in range(min(count, 16)):
            if j >= end:
                break
            cid, j = _varint(b, j)
            ticks, j = _varlong(b, j)
            _partial, rate = struct.unpack_from(">ff", b, j)
            j += 8
            if cid == 0:
                return ticks, round(rate, 3)
    return None


def lp_vec3(b, k: int) -> tuple[tuple[float, float, float], int]:
    """A velocity in the compact form of 1.21.9+ (blocks a tick): one 0 byte for none; else 6 bytes (2 bits of
    scale, a flag for more scale as a VarInt, then three 15-bit values from -1 to 1), times the scale."""
    first = b[k]
    if first == 0:
        return (0.0, 0.0, 0.0), k + 1
    second = b[k + 1]
    rest = struct.unpack_from(">I", b, k + 2)[0]
    packed = (rest << 16) | (second << 8) | first
    scale = first & 3
    k += 6
    if first & 4:
        more, k = _varint(b, k)
        scale |= (more & 0xFFFFFFFF) << 2

    def unpack(v):
        return min(float(v & 32767), 32766.0) * 2.0 / 32766.0 - 1.0
    return (unpack(packed >> 3) * scale, unpack(packed >> 18) * scale, unpack(packed >> 33) * scale), k


def _old_velocity(b, k: int) -> tuple[float, float, float]:
    """Before 1.21.9: pitch, yaw, head yaw (bytes), data (VarInt), then the velocity as three shorts / 8000."""
    _d, k = _varint(b, k + 3)
    return tuple(v / 8000 for v in struct.unpack_from(">hhh", b, k))


def _sample(tick, cur, last_out, last_move, others, remove_id, min_move=MIN_MOVE, full=False):
    for key, c in sorted(cur.items(), key=lambda kv: kv[0] is not None):    # the recorded player first
        if key is not None:
            if key not in others:
                continue
            if others[key].startswith("#"):          # mobs: near the player, a couple of times a second
                me_c = cur.get(None)
                flying = others[key].split("#")[1] in PROJECTILES    # things thrown: every tick
                if (tick % MOB_EVERY and not flying) or me_c is None or me_c["dim"] != c["dim"]                         or abs(me_c["x"] - c["x"]) > MOB_RADIUS or abs(me_c["z"] - c["z"]) > MOB_RADIUS:
                    continue
                lo = last_out.get(key)
                turned = lo is not None and max(abs(((c["yaw"] - lo[5]) + 180) % 360 - 180),
                                                abs(((c.get("head", c["yaw"]) - (lo[7] if lo[7] is not None else lo[5])) + 180) % 360 - 180)) > 15
                if lo is not None and not turned and abs(c["x"] - lo[1]) + abs(c["y"] - lo[2]) + abs(c["z"] - lo[3]) < MOB_MIN_MOVE                         and tick - lo[0] < KEEPALIVE_TICKS:
                    continue                         # (a mob turning its body or head by more than 15 degrees: a row)
            if remove_id is None and tick - last_move.get(key, tick) > OTHER_TIMEOUT_TICKS:
                continue                     # unknown protocol: can't tell when they left view
        lo = last_out.get(key)
        moved = (lo is None or lo[4] != c["dim"]
                 or abs(c["x"] - lo[1]) + abs(c["y"] - lo[2]) + abs(c["z"] - lo[3]) >= min_move
                 or (full and (c["yaw"], c["pitch"], c.get("head")) != lo[5:8]))
        if moved or tick - lo[0] >= KEEPALIVE_TICKS:
            last_out[key] = (tick, c["x"], c["y"], c["z"], c["dim"], c["yaw"], c["pitch"], c.get("head"))
            if full:
                yield (tick, c.get("name"), c["dim"], c["x"], c["y"], c["z"], c["yaw"], c["pitch"],
                       c.get("head", c["yaw"]), c.get("ground", 0))
            else:
                yield tick, c.get("name"), c["dim"], c["x"], c["y"], c["z"], c["yaw"], c["pitch"]


def decode_points(zip_path: str) -> Iterator[tuple[int, str, float, float, float, float, float]]:
    """Yield ``(tick, dimension, x, y, z, yaw, pitch)`` for the recorded player only."""
    for tick, who, dim, x, y, z, yaw, pitch in decode_entities(zip_path):
        if who is None:
            yield tick, dim, x, y, z, yaw, pitch


def _cache_key(path: str, players: dict[bytes, str] | None = None) -> str:
    st = os.stat(path)
    who = ",".join(sorted(u.hex() for u in (players or {})))     # a new player means a re-read
    h = hashlib.sha1(f"{CACHE_VERSION}|{os.path.basename(path)}|{st.st_size}|{int(st.st_mtime)}|{who}".encode())
    return h.hexdigest()[:16]


def player_uuids(data: m.ServerData) -> dict[bytes, str]:
    """``{16-byte UUID: current name}`` for every player with a known UUID."""
    out = {}
    for p in data.players.values():
        try:
            if p.uuid:
                out[bytes.fromhex(p.uuid.replace("-", ""))] = p.name
        except ValueError:
            continue
    return out


def replay_track(replay: m.Replay, zip_path: str, cache_dir: str | None,
                 players: dict[bytes, str] | None = None) -> str | None:
    """Write (or reuse) the track file for one replay: the recorded player, plus every known
    player (*players*) who came into view. Returns its path, or None if empty."""
    if replay.start is None or not replay.recorded_player:
        return None
    cache_dir = cache_dir or default_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, _cache_key(zip_path, players) + ".jsonl")
    if os.path.exists(out):
        return out if os.path.getsize(out) else None
    start_ms = int(replay.start.timestamp() * 1000)
    tmp = out + ".tmp"

    def flush(fh, tick, rows):
        if rows:
            fh.write(json.dumps({"t": start_ms + tick * 50, "k": tick, "source": "replay",
                                 "recorded_by": replay.recorded_player, "players": rows}) + "\n")

    clock: list = []
    last_tick = 0
    with open(tmp, "w", encoding="utf-8") as fh:
        cur_tick, rows = None, []
        for tick, who, dim, x, y, z, yaw, pitch in decode_entities(zip_path, players, clock=clock):
            last_tick = tick
            if tick != cur_tick:
                flush(fh, cur_tick, rows)
                cur_tick, rows = tick, []
            row = {"name": who or replay.recorded_player, "x": round(x, 2), "y": round(y, 2), "z": round(z, 2),
                   "yaw": round(yaw, 1), "pitch": round(pitch, 1), "dimension": dim}
            if who:
                row["seen"] = True                # in view of the recorded player, not recording themselves
            rows.append(row)
        flush(fh, cur_tick, rows)
        arcade = (replay.raw_meta or {}).get("arcade") or {}
        save = arcade.get("epoch_time_ms") if isinstance(arcade.get("epoch_time_ms"), (int, float)) else None
        if rows or cur_tick is not None or clock:            # the recording's clock, for lining recordings up
            fh.write(json.dumps({"meta": {"clock": clock, "ticks": max(last_tick, replay.duration_ms // 50),
                                          "save_ms": save, "start_ms": start_ms,
                                          "recorded_by": replay.recorded_player}}) + "\n")
    os.replace(tmp, out)
    return out if os.path.getsize(out) else None


def tracks_from_replays(data: m.ServerData, replay_file: Callable[[m.Replay], str],
                        cache_dir: str | None = None,
                        progress: Callable[[int, int, m.Replay], None] | None = None,
                        on_file: Callable[[str], None] | None = None) -> tuple[list[str], list[str]]:
    """Track files for every finished Flashback replay with a start time.

    *replay_file* maps a replay to its file on disk (see ``cli._replay_file``). Returns
    ``(track files, warnings)``; *on_file* is also called with each file as soon as it is ready.
    Replays with no timeline (ServerReplay 3.4.x) give no points.
    """
    todo = [r for r in data.replays if r.format == "flashback" and not r.unfinished and r.start is not None
            and not r.raw_meta.get("duration_note")]
    files, warns = [], []
    players = player_uuids(data)
    for n, r in enumerate(todo, 1):
        if progress:
            progress(n, len(todo), r)
        try:
            t = replay_track(r, replay_file(r), cache_dir, players)
        except (OSError, ValueError, zipfile.BadZipFile, struct.error, IndexError) as exc:
            warns.append(f"{r.path}: positions not readable ({exc})")
            continue
        if t:
            files.append(t)
            if on_file:
                on_file(t)
    return files, warns


EXACT_SUFFIX = ".exact35.json.gz"    # 35: shulkers ("sha" face, "spk" peek, "sco" dye); 34: mobs' moves ("cs", "cp", "eb", "ec", "gc", "wi", "hf"; "ee" 4 / 8 / 11 / 34); 33: experience ("xp" [bar, level, total]), effects ("eff" [[name, amplifier, duration ticks (-1 for ever), flags, from tick], ...]); 32: players' air ("air", of 300) and frozen ticks ("fz"); 31: sheep eating grass (entity event 10 in "ee"); 30: bees' flags ("bnf"), bees' anger, jukebox songs ("js"); 29: boss bars ("bo", all shown), wolves' anger ("ang", ticks left); (28 also: entity events with particles, "ee"); 28: the action bar ("ab"), titles ("ti", "st", "ta", "tc"), system messages ("sm"); 27: potion swirls ("ef" [[particle, ARGB], ...], faint); 26: maps in recorded item frames (their number); 25: particles the server sent ("pt"); 24: game modes ("gm"), sizes ("sc"); 23: enchantment glint and armour trims in items' looks; 22: falling blocks ("fb"), TNT, experience orbs, pick-ups ("pk"), totems; 21: boats' paddles ("pl", "pr"); 20: copper golems' weathering ("weather"); 19: pistons ("pi"); 18: level events with particles ("le"); 17: mining cracks ("mc"), blocks broken ("bb"); 16: paintings and item frames ("hg", "hv", "hi", "hgx"); 15: leads ("lead"), signs written ("sg"); 14: tamed cats and wolves, collar colours; 13: mob variants (cat, wolf, cow...), villagers' type, job, level; 12: mobs' looks (baby, wool, horse variant, slime size, name); 11: 26.3 swings (swing_animation), named mobs read on, eggs breaking aren't deaths; 10: things thrown or shot, every tick; 9: shields' and banners' patterns in "eq"; 8: chests opening ("ce" [x, y, z, viewers]), health and food ("hp"); 7: mobs' held items and armour, the recorded player's hotbar ("hb", "hs"); 6: dropped items ("it", "gone"); 5: held items and armour; 4: riding


def exact_rows(zip_path: str, cache_dir: str | None, players: dict[bytes, str] | None = None,
               build: bool = True) -> dict | None:
    """Every recorded position of a recording, for exact playback: whenever someone moved or turned (up to 20 a
    second; at least every 15 s): ``{"who": [names, None = the recorded player, "#type#id" = a mob], "dims": [...],
    "rows": [[tick, who index, dim index, x, y, z, yaw, pitch, head yaw], ...], "events": [[tick, who, "f" flags |
    "s" swing / "p" pose / "h" hand use / "hurt" / "d" died, value]], "mobs": {"#type#id": type}, "world": [[tick,
    "time" | "rain" | "thunder", value, rate]]}``. Worked out once, kept as ``<key>`` + :data:`EXACT_SUFFIX`; with
    *build* False, None when it hasn't been worked out yet."""
    import gzip
    cache_dir = cache_dir or default_cache_dir()
    out = os.path.join(cache_dir, _cache_key(zip_path, players) + EXACT_SUFFIX)
    try:
        with gzip.open(out, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, EOFError):
        if not build:
            return None
    who: list = []
    dims: list = []
    rows = []
    extras: dict = {}
    for tick, w, dim, x, y, z, yaw, pitch, head, _ground in decode_entities(zip_path, players, every=1, min_move=0.01,
                                                                           full=True, extras=extras):
        if w not in who:
            who.append(w)
        if dim not in dims:
            dims.append(dim)
        rows.append([tick, who.index(w), dims.index(dim), round(x, 3), round(y, 3), round(z, 3),
                     round(yaw, 1), round(pitch, 1), round(head if head is not None else yaw, 1)])
    data = {"who": who, "dims": dims, "rows": rows, "events": extras.get("events", []), "mobs": extras.get("mobs", {}),
            "world": extras.get("world", [])}
    os.makedirs(cache_dir, exist_ok=True)
    with gzip.open(out + ".part", "wt", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.replace(out + ".part", out)
    return data


def read_meta(path: str) -> dict | None:
    """A recording track file's clock (its last line): ``{"clock", "ticks", "save_ms", "start_ms"}``, or None."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            n = fh.tell()
            fh.seek(max(0, n - 65536))
            last = fh.read().splitlines()[-1:]
        row = json.loads(last[0]) if last else {}
    except (OSError, ValueError, IndexError):
        return None
    return row.get("meta") if isinstance(row, dict) else None


def game_time(clock: list, tick: int) -> int:
    """The world's game time at *tick* of a recording, from its clock breakpoints."""
    import bisect
    i = bisect.bisect_right(clock, [tick, float("inf")]) - 1
    t0, g0 = clock[max(0, i)][:2]
    return g0 + (tick - t0)


def world_offsets(metas: dict[str, dict]) -> dict[str, int]:
    """``{file: offset_ms}`` so that real time = offset + game time x 50 ms, the same offset for every recording in a
    stretch of overlapping play (so they line up to the tick). Each recording suggests an offset from its save time
    (else its start); recordings close in game time whose suggestions agree within 30 s (no server restart between)
    share the median."""
    rows = []
    for f, m in metas.items():
        clock = (m or {}).get("clock") or []
        if not clock:
            continue
        g0 = clock[0][1] - clock[0][0]
        g1 = game_time(clock, int(m.get("ticks") or clock[-1][0]))
        if m.get("save_ms"):
            anchor, good = m["save_ms"] - g1 * 50, True
        elif m.get("start_ms"):
            anchor, good = m["start_ms"] - g0 * 50, False
        else:
            continue
        rows.append((g0, g1, anchor, good, f))
    rows.sort()
    out: dict[str, int] = {}
    group: list = []
    gmax = None

    def settle():
        if not group:
            return
        best = [a for _g0, _g1, a, good, _f in group if good] or [a for _g0, _g1, a, _good, _f in group]
        best.sort()
        off = best[len(best) // 2]
        for r in group:
            out[r[4]] = int(off)
    for r in rows:
        if group and r[0] <= gmax + 1200:
            med = sorted(x[2] for x in group)[len(group) // 2]
            if abs(r[2] - med) < 30_000:
                group.append(r)
                gmax = max(gmax, r[1])
                continue
        settle()
        group, gmax = [r], r[1]
    settle()
    return out


def read_tracks(files: list[str], timing: dict[str, tuple[int, list]] | None = None) -> dict[str, list[list]]:
    """``{player: [[t_ms, x, y, z, dimension(, seen_by)], ...]}``, time-sorted, from track files.

    ``seen_by`` (the recording's player) is present when the position comes from someone
    else's recording. With *timing* (``{file: (offset_ms, clock)}``), recording points get their time from the
    world clock (see :func:`world_offsets`) instead of the recording's estimated start."""
    out: dict[str, list[list]] = {}
    for f in files:
        off, clock = (timing or {}).get(f, (None, None))
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                t = row.get("t")
                if off is not None and clock and row.get("k") is not None:
                    t = off + game_time(clock, row["k"]) * 50
                for p in row.get("players") or []:
                    if p.get("x") is None:
                        continue
                    pt = [t, p["x"], p["y"], p["z"], p.get("dimension") or "minecraft:overworld"]
                    if p.get("seen") and row.get("recorded_by"):
                        pt.append(row["recorded_by"])
                    out.setdefault(p["name"], []).append(pt)
    for pts in out.values():
        pts.sort(key=lambda q: q[0])
    return out


def epoch_ms(dt: datetime | None) -> int | None:
    return int(dt.timestamp() * 1000) if dt else None


def players_in_view(zip_path: str, tick: int, players: dict[bytes, str]) -> dict[str, dict]:
    """Who is in view at replay *tick*: ``{name: {"eid", "x", "y", "z"}}``, the recorded player
    included (by the UUID of ``create_local_player``). Reads the recording only up to *tick*."""
    out: dict[str, dict] = {}
    with zipfile.ZipFile(zip_path) as z:
        proto = _protocol(z)
        add_id = ADD_ENTITY.get(proto, DEFAULT_ADD_ENTITY)
        remove_id = REMOVE_ENTITIES.get(proto)
        meta = json.loads(z.read("metadata.json").decode("utf-8", "replace"))
        chunks, start = [], 0
        for cn in _chunk_names(z):
            dur = int(((meta.get("chunks") or {}).get(cn) or {}).get("duration") or 0)
            chunks.append((cn, start))
            start += dur
        # only the chunk file that contains *tick* is needed: its snapshot lists who's in view
        cn, t0 = next(((c, s) for c, s in reversed(chunks) if s <= tick), chunks[0])
        b = z.read(cn)
        i = 4
        count, i = _varint(b, i)
        kinds = []
        for _ in range(count):
            ln, i = _varint(b, i)
            kinds.append(b[i:i + ln].decode("utf-8", "replace").rsplit("/", 1)[-1])
            i += ln
        snap = _U32.unpack_from(b, i)[0]
        i += 4
        nt = kinds.index("next_tick") if "next_tick" in kinds else -1
        mv = kinds.index("move_entities") if "move_entities" in kinds else -1
        cl = kinds.index("create_local_player") if "create_local_player" in kinds else -1
        gp = kinds.index("game_packet") if "game_packet" in kinds else -1
        by_eid: dict[int, str] = {}
        local, me, me_name = None, None, None
        t = t0
        end = len(b)
        while i + 5 <= end and t <= tick:
            aid, i = _varint(b, i)
            size = _U32.unpack_from(b, i)[0]
            i += 4
            if aid == nt:
                t += 1
            elif aid == gp and size > 2:
                pid, k = _varint(b, i)
                if pid == add_id and size >= 43:
                    eid, k = _varint(b, k)
                    name = players.get(bytes(b[k:k + 16]))
                    if name:
                        _t, k2 = _varint(b, k + 16)
                        x, y, zz = _XYZ.unpack_from(b, k2)
                        by_eid[eid] = name
                        out[name] = {"eid": eid, "x": x, "y": y, "z": zz}
                elif pid == remove_id:
                    n, k = _varint(b, k)
                    for _ in range(min(n, 10_000)):
                        eid, k = _varint(b, k)
                        name = by_eid.pop(eid, None)
                        if name and out.get(name, {}).get("eid") == eid:
                            out.pop(name, None)
            elif aid == cl and size >= 40:
                me_name = players.get(bytes(b[i:i + 16]))
                local = _XYZ.unpack_from(b, i + 16)
            elif aid == mv:
                p = b[i:i + size]
                j = 0
                levels, j = _varint(p, j)
                for _ in range(levels):
                    ln, j = _varint(p, j)
                    j += ln
                    n, j = _varint(p, j)
                    for _ in range(n):
                        eid, j = _varint(p, j)
                        x, y, zz, _yaw, _pitch, _hy = _POS.unpack_from(p, j)
                        j += _POS.size + 1
                        if me is None and local is not None and abs(x - local[0]) < 0.01 and \
                                abs(y - local[1]) < 0.01 and abs(zz - local[2]) < 0.01:
                            me = eid
                        if eid == me and me_name:
                            out[me_name] = {"eid": eid, "x": x, "y": y, "z": zz, "recorded": True}
                        elif eid in by_eid:
                            out[by_eid[eid]].update({"x": x, "y": y, "z": zz})
            i += size
    return out
