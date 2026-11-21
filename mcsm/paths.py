"""Player paths decoded from Flashback recordings ("where was the recorded player, when").

A Flashback chunk file (``cN.flashback``, layout in :mod:`mcsm.replays`) is a list of
named actions. Two of them are version-independent and enough to follow the player
who made the recording:

* ``flashback:action/create_local_player`` -- payload starts with the player's UUID
  (two int64) and position (three float64), then rotation, velocity, game profile and
  game mode (not needed here). It carries **no entity id**.
* ``flashback:action/move_entities`` -- ``VarInt levels``; per level: dimension
  (VarInt length + UTF-8 resource location, e.g. ``minecraft:overworld``), ``VarInt n``;
  per entity: ``VarInt id, f64 x, f64 y, f64 z, f32 yaw, f32 pitch, f32 head_yaw,
  bool on_ground``. Only entities that *moved* that tick are written, so the last
  value is carried forward. (Verified against Flashback master's reader/recorder and
  ServerReplay's arcade ``FlashbackWriter``/``ExactEntityPosition``, branch 26.3:
  byte-identical. arcade adds the recorded player to move_entities only once they move.)
* ``flashback:action/next_tick`` -- one per game tick (20 per second).

The recorded player's entity id is found with a heuristic: the entity whose first
position in ``move_entities`` is (nearly) the ``create_local_player`` position. Every
chunk starts with a snapshot that re-creates the local player, so a wrong guess can
only last one chunk.

Output: the same ``.jsonl`` track format as the live map (``mcsm live --record``)::

    {"t": epoch_ms, "players": [{"name", "x", "y", "z", "yaw", "pitch", "dimension"}],
     "source": "replay"}

so tracks from replays feed the timelapse (and later the 3D history map) exactly like
live recordings. Times are ``replay.start + tick x 50 ms``; time the recording was
paused is not known per tick, so a paused recording drifts by the paused time.
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
import zipfile
from dataclasses import dataclass, field
from datetime import timedelta
from typing import IO, Callable, Iterator

from . import models as m
from .replays import FLASHBACK_MAGIC, TICK_ACTION, _read_exact, _read_varint

MOVE_ACTION = "flashback:action/move_entities"
LOCAL_PLAYER_ACTION = "flashback:action/create_local_player"

MATCH_DISTANCE = 1.5      # blocks: how close the first move must be to the create position
MIN_MOVE = 0.1            # blocks: smaller moves are not written as a new sample
HEARTBEAT_TICKS = 600     # write a sample at least every 30 s while the player is known


@dataclass
class ReplayPath:
    """Decoded path of the recorded player."""

    replay: str
    player: str
    samples: list[dict] = field(default_factory=list)   # rows in the live-track format
    ticks: int = 0
    matched: bool = False      # the player's entity id was found in move_entities
    note: str = ""


# --- low-level ------------------------------------------------------------------

def _records(fh: IO[bytes]) -> Iterator[tuple[str, bytes | None, bool]]:
    """Yield ``(action_name, payload, in_snapshot)``; payload is None for actions we skip."""
    if struct.unpack(">I", _read_exact(fh, 4))[0] != FLASHBACK_MAGIC:
        raise ValueError("not a Flashback chunk")
    names = [_read_exact(fh, _read_varint(fh)).decode("utf-8", "replace") for _ in range(_read_varint(fh))]
    wanted = {MOVE_ACTION, LOCAL_PLAYER_ACTION}

    def body(stream: IO[bytes], snapshot: bool):
        while True:
            try:
                action = _read_varint(stream)
            except EOFError:
                return
            size = struct.unpack(">i", _read_exact(stream, 4))[0]
            if size < 0:
                raise ValueError("negative record size")
            name = names[action] if action < len(names) else ""
            if name in wanted:
                yield name, _read_exact(stream, size), snapshot
            else:
                _skip_bytes(stream, size)
                if name == TICK_ACTION:
                    yield name, None, snapshot

    import io
    snap_size = struct.unpack(">i", _read_exact(fh, 4))[0]
    yield from body(io.BytesIO(_read_exact(fh, snap_size)), True)
    yield from body(fh, False)


def _skip_bytes(fh: IO[bytes], n: int) -> None:
    while n > 0:
        chunk = fh.read(min(n, 1 << 20))
        if not chunk:
            raise EOFError
        n -= len(chunk)


def _varint_at(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 35:
            raise ValueError("VarInt too long")


def parse_move_entities(payload: bytes) -> list[tuple[str, int, float, float, float, float, float]]:
    """``[(dimension, entity_id, x, y, z, yaw, pitch)]`` from a move_entities payload."""
    out = []
    levels, pos = _varint_at(payload, 0)
    for _ in range(levels):
        n, pos = _varint_at(payload, pos)
        dim = payload[pos:pos + n].decode("utf-8", "replace")
        pos += n
        count, pos = _varint_at(payload, pos)
        for _ in range(count):
            eid, pos = _varint_at(payload, pos)
            x, y, z, yaw, pitch, _head = struct.unpack_from(">dddfff", payload, pos)
            pos += 24 + 12 + 1  # three doubles, three floats, on_ground
            out.append((dim, eid, x, y, z, yaw, pitch))
    if pos > len(payload):
        raise ValueError("move_entities payload too short")
    return out


def parse_local_player(payload: bytes) -> tuple[str, float, float, float]:
    """``(uuid, x, y, z)`` from a create_local_player payload."""
    hi, lo, x, y, z = struct.unpack_from(">qqddd", payload, 0)
    h = f"{(hi & (2**64 - 1)):016x}{(lo & (2**64 - 1)):016x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}", x, y, z


# --- decoding a replay ------------------------------------------------------------

def _chunk_names(names: list[str], meta: dict) -> list[str]:
    chunks = list((meta.get("chunks") or {}).keys()) or [n for n in names if n.endswith(".flashback")]
    chunks = [c for c in chunks if c in names]
    return sorted(chunks, key=lambda n: int(re.sub(r"\D", "", n) or 0))


def decode_path(path: str, replay: m.Replay, uuid_to_name: dict[str, str] | None = None,
                max_ticks: int | None = None) -> ReplayPath:
    """Decode the recorded player's path from a Flashback replay (``.zip`` or folder).

    *path* must be the complete replay (see :func:`mcsm.source.ensure_full`).
    """
    if os.path.isdir(path):
        names = os.listdir(path)

        def opener(name):
            return open(os.path.join(path, name), "rb")
        with open(os.path.join(path, "metadata.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
        return _decode(_chunk_names(names, meta), opener, replay, uuid_to_name or {}, max_ticks)
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        meta = json.loads(zf.read("metadata.json").decode("utf-8", "replace"))
        return _decode(_chunk_names(names, meta), zf.open, replay, uuid_to_name or {}, max_ticks)


def _decode(chunks: list[str], opener: Callable[[str], IO[bytes]], replay: m.Replay,
            uuid_to_name: dict[str, str], max_ticks: int | None) -> ReplayPath:
    name = replay.recorded_player or ""
    res = ReplayPath(replay=replay.path, player=name)
    start_ms = int(replay.start.timestamp() * 1000) if replay.start else 0
    tick = 0
    player_id: int | None = None
    candidate: tuple[float, float, float] | None = None
    cur: tuple[str, float, float, float, float, float] | None = None   # dim, x, y, z, yaw, pitch
    last_written: tuple[int, tuple] | None = None
    dirty = False

    def emit():
        nonlocal last_written, dirty
        dim, x, y, z, yaw, pitch = cur
        res.samples.append({"t": start_ms + tick * 50, "source": "replay", "players": [{
            "name": res.player or "?", "x": round(x, 2), "y": round(y, 2), "z": round(z, 2),
            "yaw": round(yaw, 1), "pitch": round(pitch, 1), "dimension": dim}]})
        last_written, dirty = (tick, (dim, x, y, z)), False

    for chunk in chunks:
        try:
            with opener(chunk) as fh:
                for action, payload, in_snapshot in _records(fh):
                    if action == TICK_ACTION:
                        if in_snapshot:
                            continue
                        tick += 1
                        if cur is not None and last_written is not None:
                            lw_tick, (lw_dim, *lw_pos) = last_written
                            moved = dirty and (cur[0] != lw_dim or math.dist(cur[1:4], lw_pos) >= MIN_MOVE)
                            if moved or tick - lw_tick >= HEARTBEAT_TICKS:
                                emit()
                        if max_ticks and tick >= max_ticks:
                            break
                    elif action == LOCAL_PLAYER_ACTION:
                        try:
                            uuid, x, y, z = parse_local_player(payload)
                        except struct.error:
                            continue
                        if not res.player:
                            res.player = uuid_to_name.get(uuid, uuid)
                        candidate, player_id = (x, y, z), None   # re-identify in this chunk
                    elif action == MOVE_ACTION:
                        try:
                            moves = parse_move_entities(payload)
                        except (ValueError, IndexError, struct.error):
                            res.note = "some position records could not be read"
                            continue
                        if player_id is None and candidate is not None and moves:
                            best = min(moves, key=lambda mv: math.dist(mv[2:5], candidate))
                            if math.dist(best[2:5], candidate) <= MATCH_DISTANCE:
                                player_id, res.matched = best[1], True
                        for dim, eid, x, y, z, yaw, pitch in moves:
                            if eid == player_id:
                                cur, dirty = (dim, x, y, z, yaw, pitch), True
                                if last_written is None:
                                    emit()
        except (EOFError, ValueError, struct.error, KeyError, OSError) as exc:
            res.note = f"{chunk}: stopped early ({exc.__class__.__name__})"
            break
        if max_ticks and tick >= max_ticks:
            break
    if cur is not None and (last_written is None or last_written[0] != tick):
        emit()
    res.ticks = tick
    if not res.matched and not res.note:
        res.note = "the recorded player's position was not found in this replay"
    return res


# --- many replays -------------------------------------------------------------------

def track_file_name(replay: m.Replay) -> str:
    stem = os.path.splitext(replay.path.rstrip("/").replace("\\", "/"))[0]
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in stem).strip("_")
    return (safe or "replay") + ".jsonl"


def write_track(path: str, rp: ReplayPath) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in rp.samples:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    os.replace(tmp, path)


def overlapping(replays: list[m.Replay], start, end) -> list[m.Replay]:
    """Flashback replays whose time span overlaps [start, end]."""
    out = []
    for r in replays:
        if r.format != "flashback" or r.start is None:
            continue
        r_end = r.start + timedelta(milliseconds=max(r.duration_ms, 0))
        if r_end >= start and r.start <= end:
            out.append(r)
    return out


def tracks_for_replays(replays: list[m.Replay], resolve: Callable[[m.Replay], str], out_dir: str | None = None,
                       uuid_to_name: dict[str, str] | None = None, force: bool = False,
                       progress: Callable[[str], None] | None = None) -> tuple[list[tuple[m.Replay, ReplayPath | None, str]], list[dict]]:
    """Decode paths from many Flashback replays.

    *resolve* returns the full replay file for a :class:`~mcsm.models.Replay`. With
    *out_dir*, each path is written to ``out_dir/<replay>.jsonl`` and an existing file is
    reused unless *force* (decoding is the slow part). Returns ``([(replay, path or None
    when reused, track file or "")], all samples)``.
    """
    results, samples = [], []
    for i, r in enumerate(replays, 1):
        target = os.path.join(out_dir, track_file_name(r)) if out_dir else ""
        if target and os.path.isfile(target) and not force:
            with open(target, encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
            samples += rows
            results.append((r, None, target))
            continue
        if progress:
            progress(f"Decoding positions {i}/{len(replays)}: {r.path}")
        try:
            rp = decode_path(resolve(r), r, uuid_to_name)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            rp = ReplayPath(replay=r.path, player=r.recorded_player or "", note=f"could not read: {exc}")
        if target:
            write_track(target, rp)
        samples += rp.samples
        results.append((r, rp, target))
    return results, samples


def write_test_chunk(fh, moves_per_tick: list[list[tuple]], local_player: tuple | None = None,
                     ticks_before: int = 0) -> None:
    """Write a ``cN.flashback`` with a local player and move_entities (used by tests).

    *local_player* is ``(uuid, x, y, z)``; each entry of *moves_per_tick* is a list of
    ``(dimension, entity_id, x, y, z)`` written before that tick's next_tick.
    """
    def varint(v):
        out = bytearray()
        while True:
            b = v & 0x7F
            v >>= 7
            out.append(b | (0x80 if v else 0))
            if not v:
                return bytes(out)

    def record(aid, payload):
        return varint(aid) + struct.pack(">i", len(payload)) + payload

    def move(entries):
        by_dim: dict[str, list] = {}
        for dim, eid, x, y, z in entries:
            by_dim.setdefault(dim, []).append((eid, x, y, z))
        p = varint(len(by_dim))
        for dim, ents in by_dim.items():
            p += varint(len(dim)) + dim.encode() + varint(len(ents))
            for eid, x, y, z in ents:
                p += varint(eid) + struct.pack(">dddfff?", x, y, z, 90.0, 10.0, 90.0, True)
        return p

    names = [TICK_ACTION, "flashback:action/game_packet", LOCAL_PLAYER_ACTION, MOVE_ACTION]
    fh.write(struct.pack(">I", FLASHBACK_MAGIC) + varint(len(names)))
    for n in names:
        fh.write(varint(len(n)) + n.encode())
    snapshot = record(1, b"abc")
    if local_player:
        import uuid as _uuid
        u = _uuid.UUID(local_player[0])
        snapshot += record(2, u.bytes + struct.pack(">ddd", *local_player[1:4]) + b"\x00" * 40)
    fh.write(struct.pack(">i", len(snapshot)) + snapshot)
    for _ in range(ticks_before):
        fh.write(record(0, b""))
    for entries in moves_per_tick:
        if entries:
            fh.write(record(3, move(entries)))
        fh.write(record(0, b""))
