"""Activity-aware timelapse of an area.

Goal: a fixed camera over an area (a base, a town) that **only plays while someone is
there**, fast-forwards the empty time, and fits into a target length (3 minutes by
default) or runs at a fixed speed.

Steps
-----
1. **Area**: a box ``x1,z1 .. x2,z2`` (optional ``y1..y2``) in one dimension.
2. **Activity windows** -- when was at least one player inside the area?
   - Best: live-map track recordings (``mcsm live --record DIR``), one position per
     player per second. A window = consecutive samples with someone inside; windows
     closer than ``merge_gap`` seconds are joined, then padded by ``pad`` seconds.
   - Fallback (no tracks): play sessions from the logs (anyone online). Much coarser:
     someone online anywhere counts as activity. Flagged in the result.
3. **Schedule**: each active window plays at one speed; each idle gap between windows
   becomes a quick ``gap_seconds`` fast-forward. With ``target_seconds`` the active
   speed is chosen so everything adds up to the target (never slower than real time);
   with ``speed`` it is fixed.
4. **Output** (see :mod:`mcsm.flashback`): a TIMELAPSE track ``[(replay_tick,
   output_tick), ...]``, a fixed camera keyframe looking at the area, markers at each
   window ("Steve, Alex building"), and structured timestamps (chapters / SRT) in
   output-video time.
"""

from __future__ import annotations

import glob
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import models as m
from .timeutil import from_epoch_ms


@dataclass
class Area:
    name: str
    x1: float
    z1: float
    x2: float
    z2: float
    y1: float | None = None
    y2: float | None = None
    dimension: str = "minecraft:overworld"

    def __post_init__(self):
        self.x1, self.x2 = sorted((self.x1, self.x2))
        self.z1, self.z2 = sorted((self.z1, self.z2))
        if self.y1 is not None and self.y2 is not None:
            self.y1, self.y2 = sorted((self.y1, self.y2))

    @classmethod
    def parse(cls, spec: str, name: str = "Area", dimension: str = "minecraft:overworld") -> "Area":
        """``x1,z1,x2,z2`` or ``x1,z1,x2,z2,y1,y2``."""
        try:
            nums = [float(v) for v in spec.replace(" ", "").split(",")]
        except ValueError as exc:
            raise ValueError(f"area must be numbers like 100,-50,180,20 (got {spec!r})") from exc
        if len(nums) not in (4, 6):
            raise ValueError("area needs 4 numbers (x1,z1,x2,z2) or 6 (x1,z1,x2,z2,y1,y2)")
        return cls(name, *nums[:4], *(nums[4:6] if len(nums) == 6 else (None, None)), dimension=dimension)

    def contains(self, x, y, z, dimension: str) -> bool:
        if dimension != self.dimension or x is None or z is None:
            return False
        if not (self.x1 <= x <= self.x2 and self.z1 <= z <= self.z2):
            return False
        if self.y1 is not None and y is not None and not (self.y1 <= y <= self.y2):
            return False
        return True

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2, (self.z1 + self.z2) / 2


@dataclass
class Window:
    start: datetime
    end: datetime
    players: set[str] = field(default_factory=set)

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


def load_tracks(paths: list[str]) -> list[tuple[datetime, list[dict]]]:
    """Read ``.jsonl`` track files (files or folders). Returns time-sorted samples."""
    files = []
    for p in paths:
        files += sorted(glob.glob(os.path.join(p, "*.jsonl"))) if os.path.isdir(p) else [p]
    samples = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                    samples.append((from_epoch_ms(row["t"]), row.get("players") or []))
                except (ValueError, KeyError, TypeError):
                    continue
    samples.sort(key=lambda s: s[0])
    return samples


def windows_from_tracks(samples, area: Area, start: datetime, end: datetime, merge_gap: float = 120.0,
                        pad: float = 5.0) -> list[Window]:
    """Activity windows inside [start, end] from track samples."""
    out: list[Window] = []
    for t, players in samples:
        if t < start - timedelta(seconds=pad) or t > end + timedelta(seconds=pad):
            continue
        inside = {p["name"] for p in players if area.contains(p.get("x"), p.get("y"), p.get("z"),
                                                               p.get("dimension", "minecraft:overworld"))}
        if not inside:
            continue
        if out and (t - out[-1].end).total_seconds() <= merge_gap:
            out[-1].end = t
            out[-1].players |= inside
        else:
            out.append(Window(t, t, set(inside)))
    return _pad_clip(out, start, end, pad)


def windows_from_sessions(sessions: list[m.Session], start: datetime, end: datetime, merge_gap: float = 120.0,
                          pad: float = 5.0) -> list[Window]:
    """Coarse fallback: anyone online (from the logs) counts as activity."""
    raw = sorted(((s.start, s.end or end, s.player) for s in sessions if (s.end or end) > start and s.start < end),
                 key=lambda r: r[0])
    out: list[Window] = []
    for a, b, who in raw:
        if out and (a - out[-1].end).total_seconds() <= merge_gap:
            out[-1].end = max(out[-1].end, b)
            out[-1].players.add(who)
        else:
            out.append(Window(a, b, {who}))
    return _pad_clip(out, start, end, pad)


def _pad_clip(windows: list[Window], start: datetime, end: datetime, pad: float) -> list[Window]:
    res: list[Window] = []
    for w in windows:
        a = max(start, w.start - timedelta(seconds=pad))
        b = min(end, w.end + timedelta(seconds=pad))
        if b <= a:
            continue
        if res and a <= res[-1].end:
            res[-1].end = max(res[-1].end, b)
            res[-1].players |= w.players
        else:
            res.append(Window(a, b, set(w.players)))
    return res


@dataclass
class Segment:
    """One piece of the output video."""

    kind: str              # "active" or "gap"
    real_start: datetime
    real_end: datetime
    out_start: float       # seconds in the output video
    out_end: float
    players: set[str] = field(default_factory=set)

    @property
    def speed(self) -> float:
        dur = self.out_end - self.out_start
        return (self.real_end - self.real_start).total_seconds() / dur if dur > 0 else math.inf


@dataclass
class Plan:
    segments: list[Segment]
    active_speed: float
    total_seconds: float
    source: str            # "tracks" or "sessions"


def plan(windows: list[Window], target_seconds: float | None = 180.0, speed: float | None = None,
         gap_seconds: float = 0.5, min_speed: float = 1.0) -> Plan:
    """Turn activity windows into output segments (see module docstring)."""
    if not windows:
        raise ValueError("no activity in the area during this replay")
    active_total = sum(w.seconds for w in windows)
    gaps = len(windows) - 1
    if speed:
        k = max(min_speed, float(speed))
    else:
        budget = (target_seconds or 180.0) - gaps * gap_seconds
        if budget <= 0:
            raise ValueError("target length too short for this many activity periods; lower gap_seconds "
                             "or raise the target")
        k = max(min_speed, active_total / budget)
    segs, t = [], 0.0
    for i, w in enumerate(windows):
        if i:
            prev = windows[i - 1]
            segs.append(Segment("gap", prev.end, w.start, t, t + gap_seconds))
            t += gap_seconds
        d = w.seconds / k
        segs.append(Segment("active", w.start, w.end, t, t + d, set(w.players)))
        t += d
    return Plan(segs, k, t, "")


def timelapse_keyframes(p: Plan, replay_start: datetime) -> list[tuple[int, int]]:
    """``[(replay_tick, output_tick)]`` for a Flashback TIMELAPSE track.

    Segments are contiguous (each starts where the previous ended, in both real and
    output time), so the keyframes are the first segment's start plus every segment's
    end. Points that would not strictly increase after rounding to ticks are dropped.
    """
    def tick(real: datetime) -> int:
        return int(round((real - replay_start).total_seconds() * 20))

    points = [(tick(p.segments[0].real_start), int(round(p.segments[0].out_start * 20)))]
    points += [(tick(s.real_end), int(round(s.out_end * 20))) for s in p.segments]
    out = [points[0]]
    for rt, ot in points[1:]:
        if rt > out[-1][0] and ot > out[-1][1]:
            out.append((rt, ot))
    return out


def auto_camera(area: Area, ground_y: float | None = None) -> tuple[float, float, float, float, float]:
    """A camera south of the area looking north and down at its centre: ``(x, y, z, yaw, pitch)``.

    Minecraft yaw: 0 = looking south (+z), 180 = north (-z). Positive pitch looks down.
    """
    cx, cz = area.centre
    size = max(area.x2 - area.x1, area.z2 - area.z1, 16.0)
    base = ground_y if ground_y is not None else (area.y1 if area.y1 is not None else 64.0)
    dist = size * 0.9
    height = size * 0.55
    x, y, z = cx, base + height, area.z2 + dist * 0.35
    # Look from the camera down to the centre of the area (a few blocks above ground).
    pitch = math.degrees(math.atan2(y - (base + 4.0), z - cz))
    return round(x, 2), round(y, 2), round(z, 2), 180.0, round(max(10.0, min(70.0, pitch)), 1)


def describe(p: Plan, tz=None) -> list[str]:
    """Human-readable plan lines."""
    lines = []
    for s in p.segments:
        when = s.real_start.astimezone(tz).strftime("%a %d %b %H:%M")
        if s.kind == "active":
            lines.append(f"{_fmt(s.out_start)}-{_fmt(s.out_end)}  {when}  {', '.join(sorted(s.players)) or 'activity'}"
                         f"  ({(s.real_end - s.real_start).total_seconds() / 60:.0f} min at {s.speed:.0f}x)")
        else:
            gap = (s.real_end - s.real_start).total_seconds()
            span = f"{gap / 3600:.1f} h" if gap >= 5400 else f"{gap / 60:.0f} min"
            lines.append(f"{_fmt(s.out_start)}         ... {span} with nobody there (fast-forward)")
    return lines


def _fmt(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 60}:{sec % 60:02d}"


# --- a timelapse of a build found by the moments finder (see mcsm.moments._work_moments) -------------
# Design by Fable (2026-11-22): the builder's own recordings in time order (the fixed camera needs the
# chunks around the build, which only a recording made there has), activity windows from the positions
# read from the recordings (only the moment's players count), one speed across all files so the parts
# join seamlessly, the same camera in every part, one Flashback copy per file ("2 of 5").

MAX_CAMERA_SIZE = 160          # huge map-art areas: keep the camera within render distance
MIN_ACTIVE_S = 20              # a file with less activity than this inside the area is skipped


def samples_from_tracks(tracks: dict[str, list[list]], players: list[str], start_ms: int, end_ms: int,
                        idle_move: float = 3.0):
    """Positions read from recordings (``{player: [[t_ms, x, y, z, dimension, ...]]}``) in the shape
    :func:`windows_from_tracks` takes (``[(datetime, [{name, x, y, z, dimension}])]``), one per second.
    Minutes in which a player moved less than *idle_move* blocks (AFK) are left out, so a timelapse
    fast-forwards them like time with nobody there."""
    import math
    from .timeutil import from_epoch_ms
    by_second: dict[int, dict[str, dict]] = {}
    for name in players:
        pts = [p for p in tracks.get(name) or [] if start_ms <= p[0] <= end_ms]
        moved: dict[int, float] = {}
        for a, b in zip(pts, pts[1:]):
            if a[4] == b[4] and b[0] - a[0] < 120_000:
                d = math.dist(a[1:4], b[1:4])
                if d <= 60:
                    moved[b[0] // 60_000] = moved.get(b[0] // 60_000, 0.0) + d
        for p in pts:
            if moved.get(p[0] // 60_000, 0.0) < idle_move:
                continue
            by_second.setdefault(p[0] // 1000, {})[name] = {"name": name, "x": p[1], "y": p[2], "z": p[3],
                                                            "dimension": p[4]}
    return [(from_epoch_ms(sec * 1000), list(v.values())) for sec, v in sorted(by_second.items())]


def clip_windows(windows: list[Window], start: datetime, end: datetime) -> list[Window]:
    out = []
    for w in windows:
        a, b = max(w.start, start), min(w.end, end)
        if b > a:
            out.append(Window(a, b, set(w.players)))
    return out


def moment_area(moment: dict, grow: float = 8.0) -> Area:
    x0, z0, x1, z1 = moment["area"]
    return Area("the build", x0 - grow, z0 - grow, x1 + grow, z1 + grow,
                dimension=moment.get("dim") or "minecraft:overworld")


def moment_camera(moment: dict, area: Area) -> tuple[float, float, float, float, float]:
    """A fixed camera over the build: south of it looking down (map art: steeply down)."""
    cx, cz = area.centre
    half = min(MAX_CAMERA_SIZE, max(area.x2 - area.x1, area.z2 - area.z1)) / 2
    capped = Area(area.name, cx - half, cz - half, cx + half, cz + half, dimension=area.dimension)
    ground = float(moment.get("y") if moment.get("y") is not None else 64.0)
    x, y, z, yaw, pitch = auto_camera(capped, ground_y=ground)
    if moment.get("mode") == "map_art":
        size = 2 * half
        x, y, z, pitch = cx, ground + size * 0.9 + 10, cz + size * 0.12, 80.0
    return x, y, z, yaw, pitch


def plan_moment(data: m.ServerData, moment: dict, tracks: dict[str, list[list]],
                target_seconds: float = 180.0, gap_seconds: float = 0.5) -> dict:
    """What a timelapse of a build moment would be (nothing is written): the point of view, the files in
    order with their own plan at one shared speed, the camera, total length and size."""
    import bisect
    area = moment_area(moment)
    # player names as the moments use them (renames and letter case don't hide anyone's positions)
    canon = {n.lower(): p.name for p in data.players.values() for n in p.names_seen + [p.name]}
    by_name: dict[str, list] = {}
    for n, pts in tracks.items():
        by_name.setdefault(canon.get(n.lower(), n).lower(), []).extend(pts)
    for pts in by_name.values():
        pts.sort(key=lambda q: q[0])
    tracks = {n: pts for n, pts in by_name.items()}
    wanted = {r["path"] for r in moment.get("recordings") or []}
    reps, seen_starts = [], set()
    for r in sorted(data.replays, key=lambda r: (r.start or 0, r.path)):
        if (r.path in wanted and r.format == "flashback" and r.start is not None and not r.unfinished
                and r.recorded_player and not r.raw_meta.get("duration_note")
                and not any(c in r.path for c in ("(timelapse", "(highlights)", "(text)"))
                and (r.recorded_player.lower(), r.start) not in seen_starts):     # the same file twice
            seen_starts.add((r.recorded_player.lower(), r.start))
            reps.append(r)
    if not reps:
        raise ValueError("no recording with a timeline covers this build")
    start, end = from_ms(moment["start"]), from_ms(moment["end"])
    samples = samples_from_tracks(tracks, [p.lower() for p in moment.get("players") or []],
                                  moment["start"], moment["end"])
    windows = windows_from_tracks(samples, area, start, end, merge_gap=120.0, pad=5.0)
    if not windows:
        raise ValueError("the players' positions don't show them in the area (are the positions read yet?)")
    # the point of view: the player whose own files cover the most activity
    by_pov: dict[str, list] = {}
    for r in reps:
        by_pov.setdefault(r.recorded_player or "?", []).append(r)
    players = set(moment.get("players") or [])

    near = Area(area.name, area.x1 - 96, area.z1 - 96, area.x2 + 96, area.z2 + 96, dimension=area.dimension)

    def covered(who, rs):
        """Seconds of activity filmed from close by: the recorder's own position near the build."""
        own = tracks.get(who.lower()) or []
        times = [p[0] for p in own]
        secs = 0
        for r in rs:
            for w in clip_windows(windows, r.start, r.end):
                a = bisect.bisect_left(times, int(w.start.timestamp() * 1000))
                b = bisect.bisect_right(times, int(w.end.timestamp() * 1000))
                secs += len({own[i][0] // 1000 for i in range(a, b) if near.contains(own[i][1], own[i][2], own[i][3], own[i][4])})
        return secs
    scores = {k: covered(k, v) for k, v in by_pov.items()}
    pov = max(by_pov, key=lambda k: (scores[k], k in players, -len(by_pov[k])))
    if scores[pov] <= 0:
        raise ValueError("nobody who recorded was near the build while it was worked on")
    files = []
    for r in sorted(by_pov[pov], key=lambda r: r.start):
        ws = clip_windows(windows, r.start, r.end)
        if sum(w.seconds for w in ws) >= MIN_ACTIVE_S:
            files.append((r, ws))
    if not files:
        raise ValueError("the recordings show too little of the build")
    active = sum(w.seconds for _r, ws in files for w in ws)
    gaps = sum(len(ws) - 1 for _r, ws in files)            # breaks inside a file (the files are cut apart)
    budget = target_seconds - gaps * gap_seconds
    if budget < target_seconds / 3:                    # many breaks: make it longer rather than fail
        budget = target_seconds / 3
    speed = max(1.0, active / budget)
    cam = moment_camera(moment, area)
    parts = []
    for i, (r, ws) in enumerate(files, 1):
        pl = plan(ws, speed=speed, gap_seconds=gap_seconds)
        parts.append({"replay": r, "plan": pl, "n": i, "seconds": round(pl.total_seconds, 1)})
    return {"pov": pov, "area": area, "camera": cam, "speed": round(speed, 1), "parts": parts,
            "bytes": sum(p["replay"].size_bytes or 0 for p in parts),
            "seconds": round(sum(p["seconds"] for p in parts), 1), "active_seconds": round(active),
            "windows": len(windows)}


def from_ms(ms: int) -> datetime:
    from .timeutil import from_epoch_ms
    return from_epoch_ms(ms)


def export_moment(data: m.ServerData, moment: dict, tracks: dict[str, list[list]],
                  replay_file, flashback_dir: str | None, out_dir: str, pick_dir=None,
                  target_seconds: float = 180.0, progress=None) -> dict:
    """Write the timelapse of a build moment: one Flashback copy per recording of the chosen point of
    view, each with the fixed camera, the timelapse and one scene, plus an order list."""
    from . import flashback as fbm
    say = progress or (lambda _m: None)
    pm = plan_moment(data, moment, tracks, target_seconds)
    title = moment.get("title") or "Build"
    total = len(pm["parts"])
    written, folders, made = [], set(), []
    try:
        _write_parts(pm, title, total, replay_file, flashback_dir, out_dir, pick_dir, say, written, folders, made)
    except BaseException:
        for f in made:                                   # leave nothing half-made behind
            try:
                os.remove(f)
            except OSError:
                pass
        raise
    os.makedirs(out_dir, exist_ok=True)
    return _write_order(pm, title, written, out_dir, folders)


def target_folder(r, flashback_dir, out_dir, pick_dir=None) -> str:
    fb = (pick_dir(r) if pick_dir else None) or flashback_dir
    return os.path.join(fb, "replays") if fb else os.path.join(out_dir, "flashback")


def _write_parts(pm, title, total, replay_file, flashback_dir, out_dir, pick_dir, say, written, folders, made):
    from . import flashback as fbm
    for part in pm["parts"]:
        r, pl = part["replay"], part["plan"]
        fb = (pick_dir(r) if pick_dir else None) or flashback_dir
        target = os.path.join(fb, "replays") if fb else os.path.join(out_dir, "flashback")
        folders.add(target)
        keys = timelapse_keyframes(pl, r.start)
        markers = []
        for seg in pl.segments:
            if seg.kind == "active":
                markers.append(fbm.FlashbackMarker(fbm.tick_of((seg.real_start - r.start).total_seconds()),
                                                   f"{', '.join(sorted(seg.players)) or 'Someone'} building",
                                                   fbm.MARKER_COLOURS["activity_start"]))
        suffix = f" (timelapse {part['n']} of {total})"
        base = os.path.splitext(os.path.basename(r.path.rstrip("/")))[0]
        dst = os.path.join(target, f"{base}{suffix}.zip")
        k = 2
        while os.path.exists(dst):
            dst = os.path.join(target, f"{base}{suffix} {k}.zip")
            k += 1
        say(f"Copying part {part['n']} of {total}: {r.path}…")
        made.append(dst)
        meta = fbm.write_replay_copy(replay_file(r), dst, markers, suffix)
        state = fbm.editor_state(f"Timelapse {part['n']}/{total}: {title}"[:60], fbm.camera_keyframe(*pm["camera"]),
                                 keys, (keys[0][0], keys[-1][0]))
        if fb:
            state_file = fbm.install_editor_state(fb, meta["uuid"], state)
        else:
            state_file = os.path.join(os.path.dirname(dst), f"{meta['uuid']}.json")
            with open(state_file, "w", encoding="utf-8") as fh:
                json.dump(state, fh, separators=(",", ":"))
        made.append(state_file)
        written.append({"n": part["n"], "file": dst, "editor_state": state_file, "seconds": part["seconds"],
                        "recorded_by": r.recorded_player})


def _write_order(pm, title, written, out_dir, folders) -> dict:
    order = os.path.join(out_dir, f"timelapse order - {''.join(c for c in title if c.isalnum() or c in ' -_')[:60].strip()}.txt")
    with open(order, "w", encoding="utf-8") as fh:
        fh.write(f"Timelapse: {title}\nFilmed from {pm['pov']}'s recordings, {pm['speed']:g}x while building, "
                 f"about {_fmt(pm['seconds'])} in all.\n\nIn Flashback, open each copy in this order and Export "
                 "its scene; then put the videos one after another.\n\n")
        for w in written:
            fh.write(f"{w['n']}. {os.path.basename(w['file'])}  ({_fmt(w['seconds'])})\n")
        fh.write("\nJoining the exported videos with ffmpeg: list them in a text file as lines  file 'part1.mp4'  "
                 "and run:  ffmpeg -f concat -safe 0 -i list.txt -c copy timelapse.mp4\n")
    return {"parts": written, "order": order, "pov": pm["pov"], "speed": pm["speed"], "seconds": pm["seconds"],
            "folder": sorted(folders)[0] if len(folders) == 1 else sorted(folders)}
