"""Reading replay recordings: ReplayMod (``.mcpr``) and Flashback (``.zip``).

Both formats are produced client-side by their mods *and* server-side by
ServerReplay (config ``default_encoding``: ``replay_mod`` or ``flashback``),
which saves to ``recordings/players/<uuid>/`` and ``recordings/chunks/<name>/``.
We search the whole server folder, so other locations work too.

ReplayMod (``.mcpr``, a zip)
----------------------------
* ``metaData.json`` -- ``date`` = recording start (Unix ms, UTC), ``duration``
  (ms), ``serverName``, ``mcversion``, ``generator``, ``players`` (UUIDs)...
* ``markers.json`` (optional) -- ``[{"realTimestamp": ms_from_start, "value": {"name": ...}}]``
* ``recording.tmcpr`` -- packet stream (not parsed).

Unfinished ReplayMod recordings (server crashed while recording) are folders
named ``<name>.tmp`` / ``<name>.mcpr.tmp`` containing ``metaData.json``; they
are read the same way and flagged ``unfinished``.

Flashback (``.zip``)
--------------------
Source: github.com/Moulberry/Flashback (``record/FlashbackMeta.java``,
``io/ReplayWriter.java``, ``action/ActionRealTimeClock.java``).

* ``metadata.json`` -- ``name``, ``world_name``, ``version_string``,
  ``total_ticks`` (20 per second), ``markers`` (``{"<tick>": {"colour",
  "position": {"position": [x,y,z], "dimension"}, "description"}}``) and
  ``chunks`` (``{"c0.flashback": {"duration": ticks}}``).
  **It has no start time.**
* ``c0.flashback``, ``c1.flashback``... -- the recording, one file per ~5
  minutes. Binary layout (big-endian)::

      uint32 magic 0xD780E884
      VarInt N, then N action names (VarInt length + UTF-8); the i-th name has id i
      uint32 snapshot_size, then snapshot_size bytes
      records until EOF: VarInt action_id, uint32 payload_size, payload

  The ``flashback:action/real_time_clock_optional`` action carries the real
  time: payload ``0x00`` + int64 epoch milliseconds (a non-zero first byte is a
  small delta instead). ``flashback:action/next_tick`` marks each game tick.

So the start time of a Flashback recording is::

    start = first full real-time-clock value - (next_tick records before it) x 50 ms

ServerReplay's Flashback writer (the "arcade" library) writes **no** real-time clock;
instead it adds ``arcade_replay_meta.json`` with ``epoch_time_ms`` (when the file was
SAVED) and ``accumulated_paused_time``, so ``start = save - length - paused``
(``start_source = "save-time"``).

Further fallbacks, in order: a ``YYYY-MM-DD--HH-MM-SS`` date in the file name
(ServerReplay's naming, server local time), then the file's modification time
minus the duration. :attr:`Replay.start_source` says which one was used.

Unfinished Flashback recordings are folders with ``metadata.json`` and
``cN.flashback`` files (ServerReplay leaves them next to the finished
``.zip``); they are read too and flagged ``unfinished``.
"""

from __future__ import annotations

import io
import json
import os
import re
import struct
import zipfile
from datetime import datetime, timedelta
from typing import IO, Any, Callable

from . import models as m
from .timeutil import UTC, from_epoch_ms

_SKIP_DIRS = {"logs", "libraries", "versions", "cache", ".fabric", "crash-reports", "region", "entities", "poi",
              "playerdata", "stats", "advancements", "data", "mods", "plugins", "config", "level_chunk_caches"}

FLASHBACK_MAGIC = 0xD780E884
RTC_ACTION = "flashback:action/real_time_clock_optional"
TICK_ACTION = "flashback:action/next_tick"
_NAME_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})--(\d{2})-(\d{2})-(\d{2})")


# --- discovery ---------------------------------------------------------------

def _is_flashback_dir(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "metadata.json")) and any(
        f.endswith(".flashback") for f in os.listdir(path))


def _is_replaymod_dir(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "metaData.json"))


def _zip_kind(path: str) -> str | None:
    """'replaymod', 'flashback' or None, judged from the zip's entry names (cheap)."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return None
    if "metaData.json" in names:
        return "replaymod"
    if "metadata.json" in names and any(n.endswith(".flashback") for n in names):
        return "flashback"
    return None


def find_replay_sources(root: str) -> list[tuple[str, str]]:
    """Return ``[(kind, path)]`` for every replay under *root*.

    kind is ``replaymod`` / ``replaymod-dir`` / ``flashback`` / ``flashback-dir``.
    Zips other than ``.mcpr`` are only opened if they sit in a folder whose
    path mentions recordings/replays/flashback, so world backups aren't scanned.
    """
    found = []
    for current, dirs, files in os.walk(root):
        keep = []
        for d in dirs:
            full = os.path.join(current, d)
            if _is_flashback_dir(full):
                found.append(("flashback-dir", full))
            elif _is_replaymod_dir(full):
                found.append(("replaymod-dir", full))
            elif d not in _SKIP_DIRS and not d.startswith(".") and not d.endswith((".cache", ".mcpr.cache")):
                keep.append(d)
        dirs[:] = keep
        replay_area = re.search(r"record|replay|flashback", current.replace("\\", "/"), re.IGNORECASE)
        for f in files:
            full = os.path.join(current, f)
            low = f.lower()
            if low.endswith(".mcpr"):
                found.append(("replaymod", full))
            elif low.endswith(".zip") and replay_area:
                kind = _zip_kind(full)
                if kind:
                    found.append((kind, full))
    return sorted(found, key=lambda kv: kv[1])


def find_replay_files(root: str) -> list[str]:
    """Paths of all replays (kept for backwards compatibility)."""
    return [p for _, p in find_replay_sources(root)]


_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _recorded_player(rel_path: str) -> str | None:
    """ServerReplay puts player recordings in ``.../players/<folder>/x``.

    The folder comes from its ``player_recording_name`` setting: ``{uuid}`` (default),
    ``{username}``, or text such as ``Recordings for: {username} ({uuid})`` (the colon becomes
    ``_`` on Windows). Returns the uuid if the folder contains one (mapped to a name later),
    else the folder name.
    """
    parts = rel_path.replace("\\", "/").rstrip("/").split("/")
    if len(parts) >= 3 and parts[-3] == "players":
        folder = parts[-2]
        m = _UUID_RE.search(folder)
        if m:
            return m.group(0).lower()
        m = re.match(r"^Recordings for[:_]?\s*(\S+)", folder)
        return m.group(1) if m else folder
    return None


# --- Flashback binary ----------------------------------------------------------

def _read_exact(fh: IO[bytes], n: int) -> bytes:
    data = fh.read(n)
    if len(data) != n:
        raise EOFError
    return data


def _read_varint(fh: IO[bytes]) -> int:
    result = shift = 0
    while True:
        b = _read_exact(fh, 1)[0]
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result
        shift += 7
        if shift > 35:
            raise ValueError("VarInt too long")


def _skip(fh: IO[bytes], n: int) -> None:
    while n > 0:
        chunk = fh.read(min(n, 1 << 20))
        if not chunk:
            raise EOFError
        n -= len(chunk)


def flashback_first_rtc(fh: IO[bytes], max_records: int = 2_000_000) -> tuple[int, int] | None:
    """Scan a ``cN.flashback`` stream for the first full real-time clock.

    Returns ``(epoch_ms, ticks_before_it)`` or ``None`` if there is none.
    """
    try:
        magic = struct.unpack(">I", _read_exact(fh, 4))[0]
        if magic != FLASHBACK_MAGIC:
            return None
        names = []
        for _ in range(_read_varint(fh)):
            names.append(_read_exact(fh, _read_varint(fh)).decode("utf-8", "replace"))
        rtc_id = names.index(RTC_ACTION) if RTC_ACTION in names else None
        tick_id = names.index(TICK_ACTION) if TICK_ACTION in names else None
        if rtc_id is None:
            return None
        _skip(fh, struct.unpack(">i", _read_exact(fh, 4))[0])  # snapshot
        ticks = 0
        for _ in range(max_records):
            action = _read_varint(fh)
            size = struct.unpack(">i", _read_exact(fh, 4))[0]
            if action == rtc_id and size >= 9:
                payload = _read_exact(fh, size)
                if payload[0] == 0:
                    return struct.unpack(">q", payload[1:9])[0], ticks
                continue
            if action == tick_id:
                ticks += 1
            _skip(fh, size)
    except (EOFError, ValueError, struct.error, UnicodeError):
        return None
    return None


# --- readers ---------------------------------------------------------------------

def _start_from_name(path: str, local_to_utc: Callable[[datetime], datetime] | None) -> datetime | None:
    mt = _NAME_DATE.search(os.path.basename(path.rstrip("/\\")))
    if not mt:
        return None
    local = datetime(*(int(g) for g in mt.groups()))
    return local_to_utc(local) if local_to_utc else local.replace(tzinfo=UTC)


def _mtime_start(path: str, duration_ms: int) -> datetime:
    return datetime.fromtimestamp(os.path.getmtime(path), tz=UTC) - timedelta(milliseconds=duration_ms)


def _size(path: str) -> int:
    if os.path.isfile(path):
        from .source import stub_original_size
        orig = stub_original_size(path) if path.lower().endswith((".zip", ".mcpr")) else None
        return orig if orig is not None else os.path.getsize(path)
    return sum(os.path.getsize(os.path.join(c, f)) for c, _d, fs in os.walk(path) for f in fs)


def _read_replaymod(meta: dict, markers_raw: Any, rel: str, path: str, uuid_to_name: dict[str, str],
                    unfinished: bool) -> m.Replay:
    markers = []
    if isinstance(markers_raw, list):
        for mk in markers_raw:
            try:
                value = mk.get("value") or {}
                markers.append({"offset_ms": int(mk.get("realTimestamp", 0)), "name": value.get("name") or "Marker",
                                "position": value.get("position")})
            except (AttributeError, TypeError, ValueError):
                continue
    duration = int(meta.get("duration") or 0)
    if isinstance(meta.get("date"), (int, float)):
        start, source = from_epoch_ms(meta["date"]), "metadata"
    else:
        start, source = _mtime_start(path, duration), "file-time"
    players = [str(p).lower() for p in meta.get("players", []) if p]
    return m.Replay(
        path=rel, start=start, duration_ms=duration,
        server_name=meta.get("customServerName") or meta.get("serverName") or "",
        mc_version=meta.get("mcversion", ""), generator=meta.get("generator", ""),
        players=players, player_names=[uuid_to_name.get(p, p) for p in players],
        recorded_player=_recorded_player(rel), markers=sorted(markers, key=lambda x: x["offset_ms"]),
        size_bytes=_size(path), raw_meta={k: v for k, v in meta.items() if k != "players"},
        format="replaymod", start_source=source, unfinished=unfinished)


def _hms_ms(text) -> int:
    try:
        h, mi, sec = (int(x) for x in str(text).split(":"))
        return ((h * 60 + mi) * 60 + sec) * 1000
    except (ValueError, TypeError):
        return 0


def _read_flashback(meta: dict, open_chunk: Callable[[str], IO[bytes] | None], rel: str, path: str,
                    local_to_utc, unfinished: bool, arcade_meta: dict | None = None) -> m.Replay:
    chunks = meta.get("chunks") or {}
    duration_ticks = int(meta.get("total_ticks") or 0) or sum(
        int((c or {}).get("duration", 0)) for c in chunks.values() if isinstance(c, dict))
    duration_ms = duration_ticks * 50

    start, source = None, ""
    # ServerReplay (arcade library) also writes arcade_replay_meta.json with the SAVE time
    # ("epoch_time_ms") and "accumulated_paused_time" ("HH:MM:SS"); it writes no real-time clock.
    arcade = arcade_meta or {}
    end_ms = arcade.get("epoch_time_ms") if isinstance(arcade.get("epoch_time_ms"), (int, float)) else None
    paused_ms = _hms_ms(arcade.get("accumulated_paused_time"))
    first_chunk = min(chunks, key=lambda n: int(re.sub(r"\D", "", n) or 0)) if chunks else "c0.flashback"
    fh = open_chunk(first_chunk)
    if fh is not None:
        with fh:
            rtc = flashback_first_rtc(fh)
        if rtc:
            start, source = from_epoch_ms(rtc[0] - rtc[1] * 50), "real-time-clock"
    duration_note = ""
    if start is None and end_ms is not None:
        name_start = _start_from_name(path, local_to_utc)
        if duration_ticks > 1:
            start, source = from_epoch_ms(end_ms - duration_ms - paused_ms), "save-time"
        elif name_start is not None:
            # Metadata length is implausible (seen with ServerReplay 3.4.1): estimate it from
            # the save time and the start time in the file name.
            start, source = name_start, "file-name"
            est = int(end_ms - paused_ms - name_start.timestamp() * 1000)
            if est > 0:
                duration_ms, duration_note = est, "estimated from save time"
    if start is None:
        start = _start_from_name(path, local_to_utc)
        source = "file-name" if start else ""
    if start is None:
        start, source = _mtime_start(path, duration_ms), "file-time"

    markers = []
    for tick, mk in (meta.get("markers") or {}).items():
        try:
            mk = mk or {}
            pos = mk.get("position") or {}
            markers.append({"offset_ms": int(tick) * 50, "name": mk.get("description") or "Marker",
                            "position": pos.get("position"), "dimension": pos.get("dimension")})
        except (AttributeError, TypeError, ValueError):
            continue
    return m.Replay(
        path=rel, start=start, duration_ms=duration_ms,
        server_name=meta.get("world_name") or meta.get("name") or "",
        mc_version=meta.get("version_string", ""), generator="Flashback",
        recorded_player=_recorded_player(rel), markers=sorted(markers, key=lambda x: x["offset_ms"]),
        size_bytes=_size(path), raw_meta={**{k: v for k, v in meta.items() if k not in ("chunks", "markers")},
                                          **({"arcade": arcade} if arcade else {}),
                                          **({"duration_note": duration_note} if duration_note else {})},
        format="flashback", start_source=source, unfinished=unfinished)


def read_replay(path: str, root: str, uuid_to_name: dict[str, str] | None = None, kind: str | None = None,
                local_to_utc: Callable[[datetime], datetime] | None = None) -> m.Replay:
    """Read one replay of any supported kind (detected if *kind* is None)."""
    r = _read_any(path, root, uuid_to_name or {}, kind, local_to_utc)
    # ServerReplay names player folders by UUID by default ({uuid}); show the name.
    if r.recorded_player and _UUID_RE.fullmatch(r.recorded_player):
        name = (uuid_to_name or {}).get(r.recorded_player.lower())
        if not name:  # not in usercache: use a name written in the folder, if any
            parts = r.path.replace("\\", "/").rstrip("/").split("/")
            m = re.match(r"^Recordings for[:_]?\s*(\S+)", parts[-2]) if len(parts) >= 2 else None
            name = m.group(1) if m else None
        if name:
            r.recorded_player = name
    return r


def _read_any(path, root, uuid_to_name, kind, local_to_utc) -> m.Replay:
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    if kind is None:
        if os.path.isdir(path):
            kind = "flashback-dir" if _is_flashback_dir(path) else "replaymod-dir"
        else:
            kind = _zip_kind(path) or "replaymod"

    if kind == "replaymod":
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            meta = json.loads(zf.read("metaData.json").decode("utf-8", "replace")) if "metaData.json" in names else {}
            markers = json.loads(zf.read("markers.json").decode("utf-8", "replace")) if "markers.json" in names else None
        return _read_replaymod(meta, markers, rel, path, uuid_to_name, unfinished=False)

    if kind == "replaymod-dir":
        with open(os.path.join(path, "metaData.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
        markers = None
        if os.path.isfile(os.path.join(path, "markers.json")):
            with open(os.path.join(path, "markers.json"), encoding="utf-8") as fh:
                markers = json.load(fh)
        return _read_replaymod(meta, markers, rel, path, uuid_to_name, unfinished=True)

    if kind == "flashback":
        with zipfile.ZipFile(path) as zf:
            meta = json.loads(zf.read("metadata.json").decode("utf-8", "replace"))
            names = set(zf.namelist())

            def open_chunk(name):
                return zf.open(name) if name in names else None

            arcade = None
            if "arcade_replay_meta.json" in names:
                try:
                    arcade = json.loads(zf.read("arcade_replay_meta.json").decode("utf-8", "replace"))
                except ValueError:
                    arcade = None
            return _read_flashback(meta, open_chunk, rel, path, local_to_utc, unfinished=False, arcade_meta=arcade)

    if kind == "flashback-dir":
        meta_path = os.path.join(path, "metadata.json")
        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
        except ValueError:  # Flashback keeps a backup while rewriting
            with open(meta_path + ".old", encoding="utf-8") as fh:
                meta = json.load(fh)

        def open_chunk(name):
            p = os.path.join(path, name)
            return open(p, "rb") if os.path.isfile(p) else None

        arcade = None
        ap = os.path.join(path, "arcade_replay_meta.json")
        if os.path.isfile(ap):
            try:
                with open(ap, encoding="utf-8") as fh:
                    arcade = json.load(fh)
            except ValueError:
                arcade = None
        return _read_flashback(meta, open_chunk, rel, path, local_to_utc, unfinished=True, arcade_meta=arcade)

    raise ValueError(f"unknown replay kind {kind}")


def read_replays(root: str, uuid_to_name: dict[str, str] | None = None, extra_paths: list[str] | None = None,
                 local_to_utc: Callable[[datetime], datetime] | None = None) -> tuple[list[m.Replay], list[str]]:
    """Read all replays under *root* (plus *extra_paths*). Returns ``(replays, warnings)``."""
    replays, warnings = [], []
    sources = find_replay_sources(root) + [(None, os.path.abspath(p)) for p in (extra_paths or [])]
    seen = set()
    for kind, p in sources:
        if p in seen:
            continue
        seen.add(p)
        try:
            r = read_replay(p, root, uuid_to_name, kind, local_to_utc)
        except (zipfile.BadZipFile, OSError, ValueError, KeyError) as exc:
            warnings.append(f"could not read replay {p}: {exc}")
            continue
        if r.start_source == "file-time":
            warnings.append(f"replay {r.path}: start time guessed from the file's modification time -- "
                            f"markers may be off; check with a clear event")
        replays.append(r)
    replays.sort(key=lambda r: (r.start is None, r.start))
    return replays, warnings


def write_flashback_chunk(fh: io.BufferedIOBase | io.BytesIO, epoch_ms: int, ticks_before: int = 0,
                          extra_ticks: int = 0) -> None:
    """Write a minimal valid ``cN.flashback`` (used by tests to build fixtures)."""
    def varint(v):
        out = bytearray()
        while True:
            b = v & 0x7F
            v >>= 7
            out.append(b | (0x80 if v else 0))
            if not v:
                return bytes(out)
    names = [TICK_ACTION, "flashback:action/game_packet", RTC_ACTION]
    fh.write(struct.pack(">I", FLASHBACK_MAGIC) + varint(len(names)))
    for n in names:
        fh.write(varint(len(n)) + n.encode())
    snapshot = varint(1) + struct.pack(">i", 3) + b"abc"  # one dummy game_packet record
    fh.write(struct.pack(">i", len(snapshot)) + snapshot)
    for _ in range(ticks_before):
        fh.write(varint(0) + struct.pack(">i", 0))
    fh.write(varint(2) + struct.pack(">i", 9) + b"\x00" + struct.pack(">q", epoch_ms))
    for _ in range(extra_ticks):
        fh.write(varint(0) + struct.pack(">i", 0) + varint(2) + struct.pack(">i", 1) + b"\x01")
