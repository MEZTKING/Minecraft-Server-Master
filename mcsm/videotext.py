"""Text on finished videos: line a Flashback export up with the server's history.

A finished video exported from Flashback comes from one *scene* of a replay's editor state:
``exportStartTicks`` .. ``exportEndTicks`` (replay ticks) plus optional TIMELAPSE keyframes that
speed parts up. Flashback's rule (from its source, ``state/KeyframeTrack.java`` and
``exporting/ExportJob.java``): consecutive TIMELAPSE keyframes ``(k1, t1) -> (k2, t2)`` form a
segment only when ``t2 > t1``; inside it the replay advances ``(k2 - k1) / (t2 - t1) * 20``
ticks per video second. Everywhere else it plays at normal speed (20 ticks per second). So the
length of the video is the sum over the export range of ``1 / rate`` per tick; on the owner's
videos this matched to 0.05 s (337.6 s and 1210.8 s).

With the recording's start time, every video second maps to a real moment, so server events
(chat, joins, deaths, advancements) become timed texts. Outputs: ``.ass`` (styled: Minecraft
chat look, titles, name cards; plays in VLC/mpv, burns in with ffmpeg) and ``.srt``.
"""

from __future__ import annotations

import bisect
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import models as m

TICK_MS = 50


# --- time mapping ------------------------------------------------------------------------

@dataclass
class VideoClock:
    """Replay ticks <-> video seconds for one exported scene."""
    start: int                                  # export start (replay tick)
    end: int                                    # export end (replay tick)
    # piecewise constant rate: (from_tick, to_tick, replay ticks per video second, video s at from_tick)
    pieces: list[tuple[int, int, float, float]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        if not self.pieces:
            return 0.0
        a, b, rate, t0 = self.pieces[-1]
        return t0 + (b - a) / rate

    def video_time(self, tick: float) -> float | None:
        """Video seconds at replay *tick*, or None if that tick isn't in the video."""
        if tick < self.start or tick > self.end:
            return None
        for a, b, rate, t0 in self.pieces:
            if a <= tick <= b:
                return t0 + (tick - a) / rate
        return None

    def speed_at(self, tick: float) -> float:
        """How many times faster than normal the video runs at replay *tick*."""
        for a, b, rate, _t0 in self.pieces:
            if a <= tick <= b:
                return rate / 20.0
        return 1.0

    def replay_tick(self, seconds: float) -> float:
        """Replay tick shown at video time *seconds* (clamped to the video)."""
        for a, b, rate, t0 in self.pieces:
            if seconds <= t0 + (b - a) / rate:
                return a + max(0.0, seconds - t0) * rate
        return float(self.end)


def _timelapse_keys(scene: dict) -> list[tuple[int, int]]:
    for t in scene.get("keyframeTracks") or []:
        if t.get("keyframeType") == "TIMELAPSE" and t.get("enabled", True):
            out = []
            for k, v in (t.get("keyframesByTick") or {}).items():
                try:
                    out.append((int(k), int((v or {}).get("ticks", 0))))
                except (TypeError, ValueError):
                    pass
            return sorted(out)
    return []


def scene_clock(scene: dict, replay_ticks: int | None = None) -> VideoClock | None:
    """The clock of an exported scene; None if the scene has no export range set."""
    a, b = scene.get("exportStartTicks", -1), scene.get("exportEndTicks", -1)
    if a is None or b is None or a < 0 or b <= a:
        if replay_ticks and a == -1 and b == -1:
            a, b = 0, replay_ticks          # no range set: Flashback exports the whole replay
        else:
            return None
    keys = _timelapse_keys(scene)
    cuts = sorted({a, b} | {k for k, _ in keys if a < k < b})
    pieces, t = [], 0.0
    key_ticks = [k for k, _ in keys]
    for lo, hi in zip(cuts, cuts[1:]):
        rate = 20.0
        i = bisect.bisect_right(key_ticks, lo) - 1          # floor keyframe at or before lo
        if 0 <= i < len(keys) - 1:
            (k1, t1), (k2, t2) = keys[i], keys[i + 1]
            if t2 > t1 and k2 > k1:
                rate = (k2 - k1) / (t2 - t1) * 20.0
        rate = max(rate, 0.001)
        pieces.append((lo, hi, rate, t))
        t += (hi - lo) / rate
    return VideoClock(a, b, pieces)


def load_scenes(state_file: str) -> list[dict]:
    with open(state_file, encoding="utf-8") as fh:
        state = json.load(fh)
    return [s for s in state.get("scenes") or [] if isinstance(s, dict)]


def find_scenes_for_video(duration: float, tolerance: float = 0.6) -> list[dict]:
    """Exported scenes (in any Flashback folder) whose length matches a video of *duration* s.
    ``[{state, uuid, scene, name, length}]``, best match first."""
    from .flashback import flashback_folders
    out = []
    for f in flashback_folders():
        d = os.path.join(f["path"], "editor_states")
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not name.endswith(".json"):
                continue
            path = os.path.join(d, name)
            try:
                scenes = load_scenes(path)
            except (OSError, ValueError):
                continue
            for i, sc in enumerate(scenes):
                clock = scene_clock(sc)
                if clock and abs(clock.duration - duration) <= tolerance:
                    out.append({"state": path, "uuid": name[:-5], "scene": i, "name": sc.get("name") or f"Scene {i + 1}",
                                "length": clock.duration, "instance": f["name"]})
    return sorted(out, key=lambda x: abs(x["length"] - duration))


def video_duration(video: str) -> float | None:
    """Length of a video in seconds, using ffprobe if it's installed."""
    probe = find_tool("ffprobe")
    if not probe:
        return None
    try:
        out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                             capture_output=True, text=True, timeout=60, check=True).stdout.strip()
        return float(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def video_size(video: str) -> tuple[int, int] | None:
    """(width, height) of a video, using ffprobe if it's installed."""
    probe = find_tool("ffprobe")
    if not probe:
        return None
    try:
        out = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                              "-of", "csv=p=0", video], capture_output=True, text=True, timeout=60, check=True).stdout
        w, h = out.strip().split(",")[:2]
        return int(w), int(h)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def find_tool(name: str) -> str | None:
    """ffmpeg / ffprobe on PATH or in the usual Windows spots (never downloaded)."""
    exe = name + (".exe" if os.name == "nt" else "")
    found = shutil.which(name)
    if found:
        return found
    home = os.path.expanduser("~")
    for d in (os.path.join(home, "ffmpeg", "bin"), r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin"):
        if os.path.isfile(os.path.join(d, exe)):
            return os.path.join(d, exe)
    return None


# --- texts -------------------------------------------------------------------------------

STYLES = ("chat", "title", "subtitle", "name", "date")


@dataclass
class VideoText:
    start: float                 # video seconds
    end: float
    text: str
    style: str = "chat"          # one of STYLES
    kind: str = ""               # where it came from: chat/join/death/advancement/date/custom
    enabled: bool = True
    colour: str = ""             # optional #RRGGBB for the player name / highlight

    def to_json(self) -> dict:
        return dict(self.__dict__)


def _replay_for(data: m.ServerData, uuid: str) -> m.Replay | None:
    for r in data.replays:
        if str(r.raw_meta.get("uuid") or "") == uuid:
            return r
    return None


def suggest_texts(data: m.ServerData, replay: m.Replay, clock: VideoClock,
                  kinds: tuple[str, ...] = ("chat", "join", "leave", "death", "advancement", "date")) -> list[VideoText]:
    """Texts for everything that happened during the video, timed to the video."""
    if replay.start is None:
        raise ValueError("this recording has no known start time")
    t0 = replay.start
    out: list[VideoText] = []

    def at(dt: datetime) -> float | None:
        tick = (dt - t0).total_seconds() * 20.0
        return clock.video_time(tick)

    if "date" in kinds:
        first = t0 + timedelta(milliseconds=clock.start * TICK_MS)
        local = first.astimezone()
        out.append(VideoText(0.0, min(5.0, clock.duration), local.strftime("%d %B %Y, %H:%M").lstrip("0"),
                             "date", "date"))
    for ev in data.events:
        if ev.type not in ("chat", "join", "leave", "death", "advancement") or ev.type not in kinds:
            continue
        v = at(ev.time)
        if v is None:
            continue
        speed = clock.speed_at((ev.time - t0).total_seconds() * 20.0)
        hold = 10.0 if ev.type == "chat" else 6.0        # Minecraft chat lines fade after 10 s
        if speed > 3:                                    # sped-up part: keep it short and readable
            hold = 3.0
        if ev.type == "chat":
            text = f"<{ev.player}> {ev.text}"
        elif ev.type == "join":
            text = f"{ev.player} joined the game"
        elif ev.type == "leave":
            text = f"{ev.player} left the game"
        else:
            text = ev.text if ev.text.startswith(ev.player or "\0") else f"{ev.player} {ev.text}"
        out.append(VideoText(round(v, 2), round(min(v + hold, clock.duration), 2), text, "chat", ev.type))
    out.sort(key=lambda x: x.start)
    return out


# --- writing ----------------------------------------------------------------------------

def _ass_time(s: float) -> str:
    s = max(0.0, s)
    h, rem = divmod(s, 3600)
    mnt, sec = divmod(rem, 60)
    return f"{int(h)}:{int(mnt):02d}:{sec:05.2f}"


def _srt_time(s: float) -> str:
    ms = int(round(max(0.0, s) * 1000))
    h, ms = divmod(ms, 3600_000)
    mnt, ms = divmod(ms, 60_000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{mnt:02d}:{sec:02d},{ms:03d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\u29f5").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def write_ass(texts: list[VideoText], path: str, width: int = 1920, height: int = 1080,
              font: str = "Arial") -> str:
    """Styled subtitles: Minecraft-like chat (bottom-left, dark box), centred titles and
    subtitles, name cards (lower third), date (top-left)."""
    fs = max(12, height // 22)
    lines = [
        "[Script Info]", "; written by mcsm (Minecraft Server Master)", "ScriptType: v4.00+",
        f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: chat,{font},{fs},&H00FFFFFF,&H00FFFFFF,&H80000000,&H80000000,0,0,0,0,100,100,0,0,3,4,0,1,"
        f"{width // 60},{width // 3},{height // 9},1",
        f"Style: title,{font},{fs * 3},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,3,2,5,"
        f"40,40,40,1",
        f"Style: subtitle,{font},{int(fs * 1.4)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,"
        f"40,40,{height // 12},1",
        f"Style: name,{font},{int(fs * 1.3)},&H0055FFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,3,6,0,1,"
        f"{width // 30},40,{height // 4},1",
        f"Style: date,{font},{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,7,"
        f"{width // 60},40,{height // 30},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for t in texts:
        if not t.enabled or t.end <= t.start:
            continue
        style = t.style if t.style in STYLES else "subtitle"
        body = _ass_escape(t.text)
        if t.kind in ("join", "leave"):
            body = "{\\c&H55FFFF&}" + body                 # yellow, as in Minecraft
        elif t.kind == "advancement":
            body = "{\\c&H55FF55&}" + body                 # green
        elif t.kind == "chat" and t.text.startswith("<") and "> " in t.text:
            name, rest = t.text.split("> ", 1)
            body = _ass_escape(name + ">") + " " + _ass_escape(rest)
        fade = "{\\fad(150,400)}" if style in ("chat", "date", "name") else "{\\fad(300,300)}"
        lines.append(f"Dialogue: 0,{_ass_time(t.start)},{_ass_time(t.end)},{style},,0,0,0,,{fade}{body}")
    with open(path, "w", encoding="utf-8-sig") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def write_srt(texts: list[VideoText], path: str) -> str:
    """Plain captions (e.g. for YouTube)."""
    n, out = 0, []
    for t in texts:
        if not t.enabled or t.end <= t.start:
            continue
        n += 1
        out += [str(n), f"{_srt_time(t.start)} --> {_srt_time(t.end)}", t.text, ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    return path


def load_texts(path: str) -> list[VideoText]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return [VideoText(**{k: v for k, v in x.items() if k in VideoText.__dataclass_fields__}) for x in raw.get("texts", [])]


def save_texts(texts: list[VideoText], path: str, meta: dict | None = None) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({**(meta or {}), "texts": [t.to_json() for t in texts]}, fh, indent=1, default=str)
    return path
