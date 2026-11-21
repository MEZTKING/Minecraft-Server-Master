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
CACHE_VERSION = 1

_U32 = struct.Struct(">I")
_POS = struct.Struct(">dddfff")


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


def decode_points(zip_path: str) -> Iterator[tuple[int, str, float, float, float, float, float]]:
    """Yield ``(tick, dimension, x, y, z, yaw, pitch)`` for the recorded player, sampled.

    Raises ``ValueError`` for files that aren't Flashback replays.
    """
    with zipfile.ZipFile(zip_path) as z:
        tick = 0
        me: int | None = None
        local: tuple[float, float, float] | None = None
        first_seen: dict[int, tuple[float, float, float]] = {}
        cur: dict | None = None
        last_out: tuple[int, float, float, float, str] | None = None
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
            i += 4 + snap
            nt = kinds.index("next_tick") if "next_tick" in kinds else -1
            mv = kinds.index("move_entities") if "move_entities" in kinds else -1
            cl = kinds.index("create_local_player") if "create_local_player" in kinds else -1
            end = len(b)
            while i + 5 <= end:
                aid, i = _varint(b, i)
                size = _U32.unpack_from(b, i)[0]
                i += 4
                if aid == nt:
                    tick += 1
                    if cur is not None and tick % SAMPLE_TICKS == 0:
                        moved = (last_out is None or last_out[4] != cur["dim"]
                                 or abs(cur["x"] - last_out[1]) + abs(cur["y"] - last_out[2])
                                 + abs(cur["z"] - last_out[3]) >= MIN_MOVE)
                        if moved or tick - last_out[0] >= KEEPALIVE_TICKS:
                            last_out = (tick, cur["x"], cur["y"], cur["z"], cur["dim"])
                            yield tick, cur["dim"], cur["x"], cur["y"], cur["z"], cur["yaw"], cur["pitch"]
                elif aid == cl and size >= 40:
                    local = struct.unpack_from(">ddd", b, i + 16)
                    me = None            # re-match after a respawn / dimension change
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
                            if eid == me:
                                cur = {"dim": dim, "x": x, "y": y, "z": zz, "yaw": yaw, "pitch": pitch}
                i += size


def _cache_key(path: str) -> str:
    st = os.stat(path)
    h = hashlib.sha1(f"{CACHE_VERSION}|{os.path.basename(path)}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]


def replay_track(replay: m.Replay, zip_path: str, cache_dir: str | None) -> str | None:
    """Write (or reuse) the track file for one replay. Returns its path, or None if empty."""
    if replay.start is None or not replay.recorded_player:
        return None
    cache_dir = cache_dir or default_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, _cache_key(zip_path) + ".jsonl")
    if os.path.exists(out):
        return out if os.path.getsize(out) else None
    start_ms = int(replay.start.timestamp() * 1000)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for tick, dim, x, y, z, yaw, pitch in decode_points(zip_path):
            fh.write(json.dumps({"t": start_ms + tick * 50, "source": "replay",
                                 "players": [{"name": replay.recorded_player, "x": round(x, 2), "y": round(y, 2),
                                              "z": round(z, 2), "yaw": round(yaw, 1), "pitch": round(pitch, 1),
                                              "dimension": dim}]}) + "\n")
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
    for n, r in enumerate(todo, 1):
        if progress:
            progress(n, len(todo), r)
        try:
            t = replay_track(r, replay_file(r), cache_dir)
        except (OSError, ValueError, zipfile.BadZipFile, struct.error, IndexError) as exc:
            warns.append(f"{r.path}: positions not readable ({exc})")
            continue
        if t:
            files.append(t)
            if on_file:
                on_file(t)
    return files, warns


def read_tracks(files: list[str]) -> dict[str, list[list]]:
    """``{player: [[t_ms, x, y, z, dimension], ...]}``, time-sorted, from track files."""
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
                    out.setdefault(p["name"], []).append(
                        [row["t"], p["x"], p["y"], p["z"], p.get("dimension") or "minecraft:overworld"])
    for pts in out.values():
        pts.sort(key=lambda q: q[0])
    return out


def epoch_ms(dt: datetime | None) -> int | None:
    return int(dt.timestamp() * 1000) if dt else None
