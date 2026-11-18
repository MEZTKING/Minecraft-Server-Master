"""Lining up server events with a video.

The idea
--------
A video made from a replay starts at a known real-world moment:

    video_t0 (UTC) = replay.start + render_start

where ``replay.start`` comes from the ``.mcpr`` metadata and
``render_start`` is where in the replay you started rendering (the
ReplayMod "render from" time). For any server event::

    video_seconds = (event.time - video_t0) / speed

``speed`` is the ReplayMod render speed (1.0 for normal, 2.0 for a 2x
timelapse...). Events outside ``0 <= video_seconds <= video_length`` are
dropped.

Instead of a replay you can also give an absolute start time (useful for
OBS recordings or any other footage): ``--video-start "2024-05-01 20:15:00"``.

The resulting :class:`Marker` list is written in several formats by
:mod:`mcsm.exporters.markers` (SRT, DaVinci Resolve EDL, CSV, Audacity
labels, YouTube chapters, JSON).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from . import models as m
from .timeutil import iso

# Default colours used for markers (names match DaVinci Resolve's marker colours).
TYPE_COLOURS = {
    m.EV_JOIN: "Green", m.EV_LEAVE: "Cream", m.EV_CHAT: "Blue", m.EV_SERVER_CHAT: "Cyan",
    m.EV_ME: "Cyan", m.EV_DEATH: "Red", m.EV_ADVANCEMENT: "Yellow", m.EV_COMMAND: "Purple",
    m.EV_SERVER_START: "Fuchsia", m.EV_SERVER_READY: "Fuchsia", m.EV_SERVER_STOP: "Fuchsia",
    m.EV_CRASH: "Rose", m.EV_REPLAY_MARKER: "Pink",
    "beat": "Sky", "bar": "Lavender",
}

# Events that make sense as video markers by default (commands and server
# lifecycle are usually noise in an edit).
DEFAULT_SYNC_TYPES = [m.EV_JOIN, m.EV_LEAVE, m.EV_CHAT, m.EV_SERVER_CHAT, m.EV_ME,
                      m.EV_DEATH, m.EV_ADVANCEMENT, m.EV_REPLAY_MARKER]


@dataclass
class Marker:
    """A point on the video timeline."""

    seconds: float                 # position in the video
    label: str                     # short text shown on the marker
    type: str                      # event type, or "beat" / "bar" for music
    player: str | None = None
    real_time: datetime | None = None
    duration: float = 0.0          # how long the marker/subtitle lasts (seconds)
    note: str = ""                 # longer description
    snapped_from: float | None = None  # original position if snapped to a beat
    extra: dict = field(default_factory=dict)

    @property
    def colour(self) -> str:
        return TYPE_COLOURS.get(self.type, "Blue")


@dataclass
class VideoTimeline:
    """Maps real-world time to video time."""

    t0: datetime                   # real UTC time shown at video second 0
    length: float | None = None    # video length in seconds (None = unbounded)
    speed: float = 1.0             # >1 means timelapse
    label: str = ""

    def to_video(self, t: datetime) -> float:
        return (t - self.t0).total_seconds() / self.speed

    def to_real(self, seconds: float) -> datetime:
        return self.t0 + timedelta(seconds=seconds * self.speed)

    def contains(self, seconds: float) -> bool:
        return seconds >= 0 and (self.length is None or seconds <= self.length)


def timeline_from_replay(replay: m.Replay, render_start: float = 0.0,
                         render_length: float | None = None, speed: float = 1.0) -> VideoTimeline:
    if replay.start is None:
        raise ValueError(f"replay {replay.path} has no start date in its metadata")
    replay_seconds = replay.duration_ms / 1000.0
    if render_length is None:
        render_length = max(0.0, replay_seconds - render_start) / speed
    return VideoTimeline(replay.start + timedelta(seconds=render_start), render_length, speed,
                         label=replay.path)


def describe_event(ev: m.Event) -> str:
    """Short, editor-friendly label for an event."""
    p = ev.player or ""
    if ev.type == m.EV_CHAT:
        return f"<{p}> {ev.text}"
    if ev.type == m.EV_JOIN:
        return f"{p} joined"
    if ev.type == m.EV_LEAVE:
        return f"{p} left"
    if ev.type == m.EV_ADVANCEMENT:
        return f"{p}: [{ev.data.get('title', '')}]"
    if ev.type == m.EV_REPLAY_MARKER:
        return f"Marker: {ev.text}"
    if ev.type == m.EV_SERVER_CHAT:
        return f"[Server] {ev.text}"
    if ev.type == m.EV_COMMAND and p:
        return f"{p}: {ev.text}"
    return ev.text


def build_markers(events: Iterable[m.Event], timeline: VideoTimeline,
                  types: Iterable[str] | None = None, players: Iterable[str] | None = None,
                  subtitle_seconds: float = 4.0, replay_path: str | None = None) -> list[Marker]:
    """Select events that fall inside the video and convert them to markers.

    *replay_path*: if given, replay markers from *other* replays are ignored
    (markers of the replay being rendered are kept).
    """
    wanted = set(types or DEFAULT_SYNC_TYPES)
    who = {p.lower() for p in players} if players else None
    out = []
    for ev in events:
        if ev.type not in wanted:
            continue
        if who is not None and (ev.player or "").lower() not in who:
            continue
        if ev.type == m.EV_REPLAY_MARKER and replay_path and ev.source != replay_path:
            continue
        secs = timeline.to_video(ev.time)
        if not timeline.contains(secs):
            continue
        out.append(Marker(seconds=secs, label=describe_event(ev), type=ev.type, player=ev.player,
                          real_time=ev.time, duration=subtitle_seconds, note=ev.text,
                          extra={"source": ev.source, "line": ev.line, **ev.data}))
    out.sort(key=lambda mk: mk.seconds)
    return out


def marker_rows(markers: list[Marker]) -> list[dict]:
    """Flat dicts used by CSV/JSON export."""
    return [{
        "index": i + 1,
        "video_seconds": round(mk.seconds, 3),
        "video_time": _hms_ms(mk.seconds),
        "type": mk.type,
        "player": mk.player or "",
        "label": mk.label,
        "real_time_utc": iso(mk.real_time) or "",
        "snapped_from_seconds": "" if mk.snapped_from is None else round(mk.snapped_from, 3),
        "colour": mk.colour,
    } for i, mk in enumerate(markers)]


def _hms_ms(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    mi, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{mi:02d}:{s:02d}.{ms:03d}"
