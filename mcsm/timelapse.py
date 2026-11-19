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
