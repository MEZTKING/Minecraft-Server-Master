"""Highlights for videos: spans of server history marked in the history viewer.

A highlight is ``{"id", "title", "start", "end", "players", "note", "created"}`` with times in
epoch milliseconds (UTC). They are kept in one JSON file per world (see
:func:`default_file`), independent of which backups are loaded.

**Export** (:func:`export`):

* For every finished Flashback recording that overlaps a highlight (one per point of view), a
  copy of the replay (new uuid, name ``… (highlights)``) with a start marker (green) and an end
  marker (red) per highlight, and an editor timeline with **one scene per highlight whose
  export range is that highlight** (see :mod:`mcsm.flashback`). Installed into Flashback's
  ``replays/`` and ``editor_states/`` when its folder is known, else written next to the
  highlights file.
* A clip list ``<world> highlights.csv``: title, local start/end, length, players, and each
  covering recording with in/out offsets.
"""

from __future__ import annotations

import csv
import json
import os
import zipfile
import re
import threading
import time
import uuid as uuid_mod
from datetime import datetime, timezone
from typing import Callable

from . import flashback as fbm, models as m

START_COLOUR, END_COLOUR = 0x55FF55, 0xFF5555


def default_file(world_name: str) -> str:
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    base = docs if os.path.isdir(docs) else home
    return os.path.join(base, "mcsm-highlights", f"{_safe(world_name or 'world')}.json")


def _safe(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" .") or "world"


def _ms(dt: datetime | None) -> int | None:
    return int(dt.timestamp() * 1000) if dt else None


class Store:
    """Highlights in a JSON file; thread-safe (the web server handles requests in threads)."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.items: list[dict] = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.items = [h for h in doc.get("highlights", []) if isinstance(h, dict) and "start" in h and "end" in h]

    def _save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "highlights": self.items}, fh, indent=1)
        os.replace(tmp, self.path)

    def list(self) -> list[dict]:
        with self._lock:
            return sorted((dict(h) for h in self.items), key=lambda h: h["start"])

    def put(self, h: dict, players: list[str] | None = None) -> dict:
        """Create (no ``id``) or update a highlight. Returns the stored highlight."""
        try:
            start, end = int(h["start"]), int(h["end"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("a highlight needs a start and an end time")
        if end < start:
            start, end = end, start
        if end - start < 1000:
            raise ValueError("a highlight must be at least 1 second long")
        title = str(h.get("title") or "").strip()[:120] or "Highlight"
        note = str(h.get("note") or "").strip()[:2000]
        moment = str(h.get("moment") or "")[:40]
        with self._lock:
            old = next((x for x in self.items if x["id"] == h.get("id")), None) if h.get("id") else None
            if old is None and moment and not h.get("id"):      # keeping the same important moment again
                same = next((x for x in self.items if x.get("moment") == moment), None)
                if same is not None:
                    return dict(same)
            if old is None:
                old = {"id": uuid_mod.uuid4().hex[:12], "created": int(time.time() * 1000)}
                self.items.append(old)
            old.update({"title": title, "start": start, "end": end, "note": note})
            if moment:
                old["moment"] = moment
            if players is not None:
                old["players"] = players
            self._save()
            return dict(old)

    def delete(self, hid: str) -> bool:
        with self._lock:
            n = len(self.items)
            self.items = [h for h in self.items if h["id"] != hid]
            if len(self.items) != n:
                self._save()
                return True
            return False


def players_in(data: m.ServerData, start_ms: int, end_ms: int) -> list[str]:
    """Players online (from the logs) at some point in the span, by current name."""
    aliases = {n.lower(): p.name for p in data.players.values() for n in p.names_seen + [p.name]}
    out = set()
    for s in data.sessions:
        s0 = _ms(s.start)
        s1 = _ms(s.end) if s.end else s0 + 1
        if s0 is not None and s0 <= end_ms and s1 >= start_ms:
            out.add(aliases.get(s.player.lower(), s.player))
    return sorted(out)


def covering(data: m.ServerData, h: dict) -> list[m.Replay]:
    """Finished Flashback recordings (with a real timeline) that overlap the highlight."""
    out = []
    for r in data.replays:
        if r.format != "flashback" or r.unfinished or r.start is None or r.raw_meta.get("duration_note"):
            continue
        r0, r1 = _ms(r.start), _ms(r.end or r.start)
        if r0 <= h["end"] and r1 >= h["start"]:
            out.append(r)
    return out


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _hms(ms: int) -> str:
    s = max(0, round(ms / 1000))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def write_clip_list(path: str, data: m.ServerData, items: list[dict]) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:   # BOM: Excel opens UTF-8 correctly
        w = csv.writer(fh)
        w.writerow(["title", "start (local)", "end (local)", "length", "players", "note",
                    "recording", "recorded by", "in (offset)", "out (offset)"])
        for h in items:
            reps = covering(data, h) or [None]
            for r in reps:
                row = [h["title"], _local(h["start"]), _local(h["end"]), _hms(h["end"] - h["start"]),
                       ", ".join(h.get("players") or []), h.get("note", "")]
                if r is None:
                    row += ["(no recording covers this)", "", "", ""]
                else:
                    r0 = _ms(r.start)
                    row += [r.path, r.recorded_player or "", _hms(max(0, h["start"] - r0)), _hms(min(_ms(r.end) or r0, h["end"]) - r0)]
                w.writerow(row)
    return path


def export(data: m.ServerData, items: list[dict], replay_file: Callable[[m.Replay], str],
           out_dir: str, flashback_dir: str | None, world_name: str = "world",
           progress: Callable[[str], None] | None = None,
           pick_dir: Callable[[m.Replay], str | None] | None = None,
           copied: Callable[[int, int], None] | None = None) -> dict:
    """Write the Flashback copies and the clip list for *items*. Returns a summary.

    *pick_dir* chooses the Flashback folder per recording (the launcher instance with the
    recording's Minecraft version); without it, everything goes to *flashback_dir*.
    """
    say = progress or (lambda _msg: None)
    result: dict = {"clip_list": None, "replays": [], "skipped": [], "installed": bool(flashback_dir),
                    "folder": None}
    result["clip_list"] = write_clip_list(os.path.join(out_dir, f"{_safe(world_name)} highlights.csv"), data, items)
    by_replay: dict[int, tuple[m.Replay, list[dict]]] = {}
    for h in items:
        reps = covering(data, h)
        if not reps:
            result["skipped"].append(h["title"])
        for r in reps:
            by_replay.setdefault(id(r), (r, []))[1].append(h)
    folders = set()
    # each copy keeps only the 5-minute parts its highlights need (and half a minute either side): small and quick
    plans = []
    for r, hs in by_replay.values():
        r0 = _ms(r.start)
        total = int(r.raw_meta.get("total_ticks") or 0) or int(r.duration_ms / 50)
        a = max(0, round((min(h["start"] for h in hs) - r0) / 50) - 600)
        b = min(total, round((max(h["end"] for h in hs) - r0) / 50) + 600)
        try:
            size = fbm.copy_size(replay_file(r), (a, b))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            size = 0
        plans.append((r, hs, (a, b), size))
    grand = sum(p[3] for p in plans) or 1
    done = [0]

    def wrote(n: int) -> None:
        done[0] += n
        if copied:
            copied(done[0], grand)
    for r, hs, trim, _size in plans:
        fb, _installed = fbm.export_target(r, pick_dir, flashback_dir, out_dir)
        target = os.path.join(fb, "replays")
        folders.add(target)
        r0 = _ms(r.start)
        total = int(r.raw_meta.get("total_ticks") or 0) or int(r.duration_ms / 50)
        clamp = lambda ms: max(0, min(total, round((ms - r0) / 50)))  # noqa: E731
        markers, scenes = [], []
        for h in sorted(hs, key=lambda x: x["start"]):
            a, b = clamp(h["start"]), clamp(h["end"])
            markers.append(fbm.FlashbackMarker(a, f"▶ {h['title']}", START_COLOUR))
            markers.append(fbm.FlashbackMarker(b, f"■ {h['title']}", END_COLOUR))
            scenes.append({"name": h["title"][:60], "keyframeTracks": [], "exportStartTicks": a,
                           "exportEndTicks": max(a + 1, b), "history": {"entries": [], "position": 0}})
        who = r.recorded_player or "replay"
        title = hs[0]["title"] if len(hs) == 1 else f"{len(hs)} highlights"
        shown = f"{title} – {who}"                         # as Flashback lists it
        name = _safe(f"{title} - {who}")
        dst = os.path.join(target, name + ".zip")
        n = 2
        while os.path.exists(dst):
            dst = os.path.join(target, f"{name} {n}.zip")
            n += 1
        say(f"Copying {r.path} ({len(hs)} highlight{'s' if len(hs) != 1 else ''})…")
        try:
            meta = fbm.write_replay_copy(replay_file(r), dst, markers, " (highlights)", trim=trim,
                                         display_name=shown, progress=wrote)
        except (OSError, ValueError, KeyError) as exc:
            result["skipped"].append(f"{r.path}: {exc}")
            continue
        off = meta.get("_trim_offset") or 0                # the copy starts at that tick: the scenes move with it
        for sc in scenes:
            sc["exportStartTicks"] = max(0, sc["exportStartTicks"] - off)
            sc["exportEndTicks"] = max(sc["exportStartTicks"] + 1, sc["exportEndTicks"] - off)
        state = {"scenes": scenes, "sceneIndex": 0}
        state_file = fbm.install_editor_state(fb, meta["uuid"], state)
        result["replays"].append({"file": dst, "name": shown, "editor_state": state_file, "recorded_by": r.recorded_player,
                                  "minecraft": r.mc_version, "highlights": [h["title"] for h in hs],
                                  "flashback": fb, "installed": _installed})
    result["folder"] = sorted(folders)[0] if len(folders) == 1 else (sorted(folders) or None)
    result["installed"] = any(not f.startswith(os.path.abspath(out_dir)) for f in map(os.path.abspath, folders))
    result["not_installed"] = sorted({os.path.dirname(f) for f in map(os.path.abspath, folders)
                                      if f.startswith(os.path.abspath(out_dir))})
    return result
