"""Reading ReplayMod / ServerReplay recordings (``.mcpr``).

An ``.mcpr`` file is a zip archive containing:

* ``metaData.json`` -- e.g. ::

      {"singleplayer": false, "serverName": "My Server", "duration": 3605123,
       "date": 1714593303000, "mcversion": "1.21.1", "fileFormat": "MCPR",
       "fileFormatVersion": 14, "protocol": 767, "generator": "ServerReplay v1.2",
       "selfId": -1, "players": ["<uuid>", ...]}

  ``date`` is the **recording start** as a Unix time in milliseconds (UTC)
  and ``duration`` its length in milliseconds. These two numbers are what
  let us line the replay up with the server log.
* ``markers.json`` (optional) -- markers placed while recording/editing ::

      [{"realTimestamp": 125000, "value": {"name": "Boss fight", "position": {...}}}]

  ``realTimestamp`` is milliseconds from the start of the recording.
* ``recording.tmcpr`` -- the raw packet stream (not parsed here).

ServerReplay (the server-side Fabric mod) stores recordings as
``recordings/players/<player>/<timestamp>.mcpr`` and
``recordings/chunks/<area>/<timestamp>.mcpr``. The client-side ReplayMod
uses ``replay_recordings/``. We simply search the whole server folder for
``*.mcpr`` so custom locations work too.
"""

from __future__ import annotations

import json
import os
import zipfile
from typing import Any

from . import models as m
from .timeutil import from_epoch_ms

_SKIP_DIRS = {"logs", "libraries", "versions", "cache", ".fabric", "crash-reports", "region", "entities", "poi"}


def find_replay_files(root: str) -> list[str]:
    found = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if f.lower().endswith(".mcpr"):
                found.append(os.path.join(current, f))
    return sorted(found)


def _recorded_player(rel_path: str) -> str | None:
    """ServerReplay puts player recordings in ``.../players/<name>/x.mcpr``."""
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[-3] == "players":
        return parts[-2]
    return None


def read_replay(path: str, root: str, uuid_to_name: dict[str, str] | None = None) -> m.Replay:
    """Read the metadata and markers of one ``.mcpr`` file."""
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    uuid_to_name = uuid_to_name or {}
    meta: dict[str, Any] = {}
    markers: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        if "metaData.json" in names:
            meta = json.loads(zf.read("metaData.json").decode("utf-8", "replace"))
        if "markers.json" in names:
            try:
                for mk in json.loads(zf.read("markers.json").decode("utf-8", "replace")):
                    value = mk.get("value") or {}
                    markers.append({"offset_ms": int(mk.get("realTimestamp", 0)),
                                    "name": value.get("name") or "Marker",
                                    "position": value.get("position")})
            except (ValueError, TypeError, AttributeError):
                pass

    start = from_epoch_ms(meta["date"]) if isinstance(meta.get("date"), (int, float)) else None
    players = [str(p).lower() for p in meta.get("players", []) if p]
    return m.Replay(
        path=rel,
        start=start,
        duration_ms=int(meta.get("duration") or 0),
        server_name=meta.get("customServerName") or meta.get("serverName") or "",
        mc_version=meta.get("mcversion", ""),
        generator=meta.get("generator", ""),
        players=players,
        player_names=[uuid_to_name.get(p, p) for p in players],
        recorded_player=_recorded_player(rel),
        markers=sorted(markers, key=lambda mk: mk["offset_ms"]),
        size_bytes=os.path.getsize(path),
        raw_meta={k: v for k, v in meta.items() if k != "players"},
    )


def read_replays(root: str, uuid_to_name: dict[str, str] | None = None,
                 extra_paths: list[str] | None = None) -> tuple[list[m.Replay], list[str]]:
    """Read all replays under *root* (plus *extra_paths*). Returns ``(replays, warnings)``."""
    replays, warnings = [], []
    paths = find_replay_files(root) + [os.path.abspath(p) for p in (extra_paths or [])]
    seen = set()
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        try:
            replays.append(read_replay(p, root, uuid_to_name))
        except (zipfile.BadZipFile, OSError, ValueError, KeyError) as exc:
            warnings.append(f"could not read replay {p}: {exc}")
    replays.sort(key=lambda r: (r.start is None, r.start))
    return replays, warnings
