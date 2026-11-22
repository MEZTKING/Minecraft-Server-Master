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
CACHE_VERSION = 2

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


def decode_entities(zip_path: str, players: dict[bytes, str] | None = None
                    ) -> Iterator[tuple[int, str | None, str, float, float, float, float, float]]:
    """Yield ``(tick, who, dimension, x, y, z, yaw, pitch)``, sampled about once a second.

    ``who`` is None for the recorded player, else the name of another player in view (from
    *players*: ``{16-byte UUID: name}``). Raises ``ValueError`` for files that aren't
    Flashback replays.
    """
    players = players or {}
    with zipfile.ZipFile(zip_path) as z:
        proto = _protocol(z)
        add_id = ADD_ENTITY.get(proto, DEFAULT_ADD_ENTITY)
        remove_id = REMOVE_ENTITIES.get(proto)
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
            # the snapshot (who/what is in view when this file starts) uses the same record format
            for start, end in ((i, i + snap), (i + snap, len(b))):
                i = start
                while i + 5 <= end:
                    aid, i = _varint(b, i)
                    size = _U32.unpack_from(b, i)[0]
                    i += 4
                    if aid == nt:
                        tick += 1
                        if tick % SAMPLE_TICKS == 0:
                            yield from _sample(tick, cur, last_out, last_move, others, remove_id)
                    elif aid == gp and size > 2 and players and (b[i] == add_id or b[i] == remove_id):
                        # (ids below 128 are one byte; this skips the ~40 other packets per tick cheaply)
                        pid, k = _varint(b, i)
                        if pid == add_id and size >= 43:     # id, entity id, UUID, type, x, y, z
                            eid, k = _varint(b, k)
                            name = players.get(bytes(b[k:k + 16]))
                            if name and eid != me:
                                _t, k2 = _varint(b, k + 16)          # entity type: not needed
                                x, y, zz = _XYZ.unpack_from(b, k2)
                                others[eid] = name
                                cur[eid] = {"dim": dim_now, "x": x, "y": y, "z": zz, "yaw": 0.0, "pitch": 0.0, "name": name}
                                last_move[eid] = tick
                        elif pid == remove_id and others:
                            n, k = _varint(b, k)
                            for _ in range(min(n, 10_000)):
                                eid, k = _varint(b, k)
                                if eid in others:
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
                                x, y, zz, yaw, pitch, _hy = _POS.unpack_from(p, j)
                                j += _POS.size + 1
                                if me is None and local is not None and eid not in first_seen:
                                    first_seen[eid] = (x, y, zz)
                                    if abs(x - local[0]) < 0.01 and abs(y - local[1]) < 0.01 and abs(zz - local[2]) < 0.01:
                                        me = eid
                                        others.pop(eid, None)
                                        cur.pop(eid, None)
                                if eid == me:
                                    cur[None] = {"dim": dim, "x": x, "y": y, "z": zz, "yaw": yaw, "pitch": pitch}
                                    dim_now = dim
                                elif eid in others:
                                    cur[eid] = {"dim": dim, "x": x, "y": y, "z": zz, "yaw": yaw, "pitch": pitch,
                                                "name": others[eid]}
                                    last_move[eid] = tick
                    i += size


def _sample(tick, cur, last_out, last_move, others, remove_id):
    for key, c in sorted(cur.items(), key=lambda kv: kv[0] is not None):    # the recorded player first
        if key is not None:
            if key not in others:
                continue
            if remove_id is None and tick - last_move.get(key, tick) > OTHER_TIMEOUT_TICKS:
                continue                     # unknown protocol: can't tell when they left view
        lo = last_out.get(key)
        moved = (lo is None or lo[4] != c["dim"]
                 or abs(c["x"] - lo[1]) + abs(c["y"] - lo[2]) + abs(c["z"] - lo[3]) >= MIN_MOVE)
        if moved or tick - lo[0] >= KEEPALIVE_TICKS:
            last_out[key] = (tick, c["x"], c["y"], c["z"], c["dim"])
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
            fh.write(json.dumps({"t": start_ms + tick * 50, "source": "replay", "recorded_by": replay.recorded_player,
                                 "players": rows}) + "\n")

    with open(tmp, "w", encoding="utf-8") as fh:
        cur_tick, rows = None, []
        for tick, who, dim, x, y, z, yaw, pitch in decode_entities(zip_path, players):
            if tick != cur_tick:
                flush(fh, cur_tick, rows)
                cur_tick, rows = tick, []
            row = {"name": who or replay.recorded_player, "x": round(x, 2), "y": round(y, 2), "z": round(z, 2),
                   "yaw": round(yaw, 1), "pitch": round(pitch, 1), "dimension": dim}
            if who:
                row["seen"] = True                # in view of the recorded player, not recording themselves
            rows.append(row)
        flush(fh, cur_tick, rows)
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


def read_tracks(files: list[str]) -> dict[str, list[list]]:
    """``{player: [[t_ms, x, y, z, dimension(, seen_by)], ...]}``, time-sorted, from track files.

    ``seen_by`` (the recording's player) is present when the position comes from someone
    else's recording."""
    out: dict[str, list[list]] = {}
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                for p in row.get("players") or []:
                    if p.get("x") is None:
                        continue
                    pt = [row["t"], p["x"], p["y"], p["z"], p.get("dimension") or "minecraft:overworld"]
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
