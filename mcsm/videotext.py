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
import zipfile
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


def find_scenes_for_video(duration: float, tolerance: float = 0.6, video_mtime: float | None = None) -> list[dict]:
    """Flashback scenes that could have produced a video of *duration* seconds, best first.

    ``[{state, uuid, scene, name, length, instance, match, start, end}]``. *match* is ``exact``
    (the scene's export range gives that length now) or ``probable``: the edit was saved after
    the video was made (*video_mtime*) and its export start fits, so the range was probably changed
    after exporting (e.g. lengthened); *start*/*end* are then the range that gives the video's
    length.
    """
    from .flashback import flashback_folders
    exact, probable = [], []
    for f in flashback_folders():
        d = os.path.join(f["path"], "editor_states")
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not name.endswith(".json"):
                continue
            path = os.path.join(d, name)
            try:
                scenes = load_scenes(path)
                mtime = os.path.getmtime(path)
                with open(path, encoding="utf-8") as fh:
                    made_by_mcsm = "mcsm_copy_of" in fh.read(4096) or False
            except (OSError, ValueError):
                continue
            for i, sc in enumerate(scenes):
                clock = scene_clock(sc)
                base = {"state": path, "uuid": name[:-5], "scene": i, "name": sc.get("name") or f"Scene {i + 1}",
                        "instance": f["name"], "saved": mtime, "copy": made_by_mcsm}
                if clock and abs(clock.duration - duration) <= tolerance:
                    exact.append({**base, "length": clock.duration, "match": "exact",
                                  "start": clock.start, "end": clock.end})
                    continue
                start = sc.get("exportStartTicks", -1)
                if video_mtime is None or start is None or start < 0 or mtime < video_mtime:
                    continue
                # the same start, with the end where the video's length runs out
                wide = dict(sc, exportEndTicks=start + int(duration * 20 * 50) + 20)
                wclock = scene_clock(wide)
                if not wclock:
                    continue
                end = int(round(wclock.replay_tick(duration)))
                check = scene_clock(dict(sc, exportEndTicks=end))
                if check and abs(check.duration - duration) <= tolerance:
                    probable.append({**base, "length": check.duration, "match": "probable", "start": start, "end": end})
    exact.sort(key=lambda x: abs(x["length"] - duration))
    probable.sort(key=lambda x: x["saved"] - (video_mtime or 0))      # saved soonest after the video
    found = exact + probable
    own = [x for x in found if not x["copy"]]                          # mcsm's own copies only as a last resort
    return own or found


def scene_for_match(match: dict) -> dict:
    """The scene of a :func:`find_scenes_for_video` result, with the export range that made the video."""
    sc = dict(load_scenes(match["state"])[match["scene"]])
    sc["exportStartTicks"], sc["exportEndTicks"] = match["start"], match["end"]
    return sc


def still_camera_at_end(scene: dict) -> tuple[float, float] | None:
    """``(from_tick, to_tick)`` of the part at the end of the export where the camera doesn't
    move (two CAMERA keyframes with the same position and angle around the export end)."""
    end = scene.get("exportEndTicks", -1)
    for t in scene.get("keyframeTracks") or []:
        if t.get("keyframeType") != "CAMERA" or not t.get("enabled", True):
            continue
        keys = sorted((int(k), v) for k, v in (t.get("keyframesByTick") or {}).items() if isinstance(v, dict))
        same = lambda a, b: (a.get("position") == b.get("position") and a.get("yaw") == b.get("yaw")  # noqa: E731
                             and a.get("pitch") == b.get("pitch"))
        for (t1, a), (t2, b) in zip(keys, keys[1:]):
            if t1 <= end and (t2 >= end or keys[-1][0] == t2) and same(a, b):
                return float(t1), float(min(end, t2))
        if keys and keys[-1][0] <= end:
            return float(keys[-1][0]), float(end)         # after the last keyframe the camera holds
    return None


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


# --- big titles and burning them into a copy of the video ---------------------------------

def minecraft_font() -> str:
    """'Minecraft Ten' (Minecraft's logo font) when it's installed, else Arial Black."""
    dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
            os.path.expanduser("~/.fonts"), os.path.expanduser("~/Library/Fonts")]
    for d in dirs:
        for name in os.listdir(d) if d and os.path.isdir(d) else []:
            if name.lower().replace(" ", "") in ("minecraftlogo.ttf", "minecraftten.ttf", "minecraft-ten.ttf"):
                return "Minecraft Ten"
    return "Arial Black"


def _bgr(colour: str) -> str:
    names = {"white": (255, 255, 255), "black": (0, 0, 0), "gold": (255, 170, 0), "yellow": (255, 255, 85),
             "red": (255, 85, 85), "aqua": (85, 255, 255), "green": (85, 255, 85), "gray": (170, 170, 170)}
    if colour.startswith("#") and len(colour) == 7:
        r, g, b = int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)
    else:
        r, g, b = names.get(colour, (255, 255, 255))
    return f"&H{b:02X}{g:02X}{r:02X}&"


@dataclass
class Title:
    text: str
    start: float
    end: float
    size: float = 0.12              # letter height as a share of the video height
    y: float = 0.25                 # vertical centre as a share of the video height (0 = top)
    colour: str = "white"
    x: float = 0.5                  # horizontal centre as a share of the video width
    style: str = "outline"          # outline: border (Minecraft look); 3d: extruded letters; plain
    fade_in: float = 0.7            # used by animation "fade"
    fade_out: float = 0.0
    animation: str = "fade"         # none, fade, pop, words, words-fade, words-pop
    anim: float = 0.4               # how long one pop/word fade takes (seconds)
    word_gap: float = 0.3           # words one by one: seconds between words
    border_colour: str = "black"
    font: str = ""                  # "" = the default (Minecraft Ten if installed)
    border: float = 1.0             # border thickness (1 = normal)
    shadow: bool = True
    box: str = ""                   # background box colour ("" = none)
    opacity: float = 1.0            # 0..1
    roll: float = 0.0               # turned, degrees clockwise
    tilt: float = 0.0               # tipped back/forward (degrees, 3D perspective)
    turn: float = 0.0               # turned left/right (degrees, 3D perspective)
    depth: float = 0.0              # 3D letters: thickness as a share of the letter height (0 = flat)
    italic: bool = False
    underline: bool = False
    org: tuple | None = None        # point it turns around (shares of width/height; None = its centre)


TITLE_ANIMATIONS = ("none", "fade", "pop", "words", "words-fade", "words-pop", "rise", "slam")


def _alpha(opacity: float) -> int:
    """ASS alpha (0 = solid, 255 = invisible) for an opacity 0..1."""
    return 255 - int(round(255 * max(0.0, min(1.0, opacity))))


def _word_tags(t: Title, words: list[str]) -> str:
    """One line whose words appear one by one (the layout stays fixed: each word is drawn from the
    start, invisible until its moment)."""
    bs = "\\"
    out = []
    on = f"{_alpha(t.opacity):02X}"
    for i, w in enumerate(words):
        a = int((i * t.word_gap) * 1000)
        d = max(1, int(t.anim * 1000))
        if t.animation == "words":
            tags = f"{bs}alpha&HFF&{bs}t({a},{a + 1},{bs}alpha&H{on}&)"
        elif t.animation == "words-fade":
            tags = f"{bs}alpha&HFF&{bs}t({a},{a + d},{bs}alpha&H{on}&)"
        else:                                           # words-pop: grows up from the line, snappy
            tags = (f"{bs}alpha&HFF&{bs}fscy0{bs}t({a},{a + 1},{bs}alpha&H{on}&)"
                    f"{bs}t({a},{a + d},0.6,{bs}fscy100)")
        out.append("{" + tags + "}" + _ass_escape(w))
    return " ".join(out)


def titles_ass(titles: list[Title], width: int, height: int, font: str | None = None) -> str:
    """Big titles as an .ass script: styles (border / 3D / plain, optional box) and animations
    (fade, pop, words one by one)."""
    font = font or minecraft_font()
    head = ["[Script Info]", "; written by mcsm", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
            "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding",
            f"Style: t,{font},100,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,2,0,1,0,0,5,0,0,0,1",
            f"Style: box,{font},100,&H00FFFFFF,&H00FFFFFF,&H00000000,&H60000000,0,0,0,0,100,100,2,0,3,0,0,5,0,0,0,1",
            "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    bs = "\\"
    lines, layer = [], 0
    for t in titles:
        px = max(10, int(t.size * height))
        cx, cy = int(t.x * width), int(t.y * height)
        span = f"{_ass_time(t.start)},{_ass_time(t.end)}"
        words = t.text.split()
        by_word = t.animation.startswith("words") and len(words) > 1
        anim = t.animation
        fade_in = {"fade": t.fade_in, "pop": t.anim * 0.5, "slam": t.anim * 0.5, "rise": t.anim}.get(anim, 0.0)
        fad = f"{bs}fad({int(fade_in * 1000)},{int(t.fade_out * 1000)})"
        pop = ""
        d = max(2, int(t.anim * 1000))
        if anim == "pop":
            pop = f"{bs}fscx0{bs}fscy0{bs}t(0,{d * 6 // 10},{bs}fscx118{bs}fscy118){bs}t({d * 6 // 10},{d},{bs}fscx100{bs}fscy100)"
        elif anim == "slam":
            pop = f"{bs}fscx260{bs}fscy260{bs}t(0,{d // 2},0.5,{bs}fscx100{bs}fscy100)"
        a = _alpha(t.opacity)
        alpha = f"{bs}alpha&H{a:02X}&" if a and not by_word else ""
        ox, oy = (int(t.org[0] * width), int(t.org[1] * height)) if t.org else (cx, cy)
        turn = ""
        if t.roll or t.tilt or t.turn:
            turn = f"{bs}org({ox},{oy})"
            turn += f"{bs}frz{-t.roll:g}" if t.roll else ""
            turn += f"{bs}frx{t.tilt:g}" if t.tilt else ""
            turn += f"{bs}fry{t.turn:g}" if t.turn else ""
        look = (f"{bs}i1" if t.italic else "") + (f"{bs}u1" if t.underline else "")

        def place(dx: int = 0, dy: int = 0) -> str:
            if anim == "rise":                       # glides up into place
                return f"{bs}move({cx + dx},{cy + dy + px * 6 // 10},{cx + dx},{cy + dy},0,{d})"
            return f"{bs}pos({cx + dx},{cy + dy})"

        body = _word_tags(t, words) if by_word else _ass_escape(t.text)
        if t.font:
            fad = f"{bs}fn{t.font}" + fad
        tags = fad + pop + alpha + turn + look
        if t.box:
            pad = max(4, px // 6)
            box_a = 0x60 + (0xFF - 0x60) * a // 255
            lines.append(f"Dialogue: {layer},{span},box,,0,0,0,,{{{fad}{pop}{turn}{bs}fs{px}{place()}{bs}1c&HFFFFFF&"
                         f"{bs}alpha&HFF&{bs}3c{_bgr(t.box)}{bs}3a&H{box_a:02X}&{bs}bord{pad}}}{_ass_escape(t.text)}")
            layer += 1
        depth = t.depth or (0.14 if t.style == "3d" else 0.0)
        if depth > 0:
            n = max(4, min(24, int(px * depth)))
            r_, g_, b_ = (int(_bgr(t.colour)[i:i + 2], 16) for i in (6, 4, 2))
            for i in range(n, 0, -1):
                f = 0.55 - 0.3 * i / n
                shade = f"&H{int(b_ * f):02X}{int(g_ * f):02X}{int(r_ * f):02X}&"
                lines.append(f"Dialogue: {layer},{span},t,,0,0,0,,{{{tags}{bs}fs{px}{place(i // 3, i)}"
                             f"{bs}1c{shade}{bs}bord{max(2, px // 45)}{bs}3c&H101010&}}{body}")
                layer += 1
            bord = max(2, px // 45) if t.style != "outline" else max(2, int(px / 12 * t.border))
            lines.append(f"Dialogue: {layer},{span},t,,0,0,0,,{{{tags}{bs}fs{px}{place()}{bs}1c{_bgr(t.colour)}"
                         f"{bs}bord{bord}{bs}3c{_bgr(t.border_colour) if t.style == 'outline' else '&H181818&'}}}{body}")
        else:
            bord = max(0, int(px / 12 * t.border)) if t.style == "outline" else 0
            shad = max(1, px // 25) if t.shadow else 0
            lines.append(f"Dialogue: {layer},{span},t,,0,0,0,,{{{tags}{bs}fs{px}{place()}{bs}1c{_bgr(t.colour)}"
                         f"{bs}bord{bord}{bs}3c{_bgr(t.border_colour)}{bs}shad{shad}{bs}4c&H000000&}}{body}")
        layer += 1
    return "\n".join(head + lines) + "\n"


def pick_encoder(ffmpeg: str) -> list[str]:
    """ffmpeg arguments for the fastest H.264 encoder that really works on this PC (a graphics
    card's encoder when there is one; listed encoders are tried, not trusted), else libx264."""
    for enc, args in (("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "18"]),
                      ("h264_amf", ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "18",
                                    "-qp_p", "20"]),
                      ("h264_qsv", ["-c:v", "h264_qsv", "-global_quality", "20"])):
        try:
            r = subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.1",
                                "-c:v", enc, "-f", "null", "-"], capture_output=True, timeout=30)
            if r.returncode == 0:
                return args
        except (OSError, subprocess.SubprocessError):
            pass
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "17"]


def image_filters(images: list[dict], width: int, height: int, first_input: int = 1,
                  base: str = "0:v") -> tuple[list[str], str]:
    """ffmpeg filter chains that lay *images* over the video ``[base]``: each ``{file, start, end,
    x, y (centre, shares), height (share of the video height), opacity, roll, animation (none, fade,
    rise, pop), anim, fade_out}``. Returns (chains, label of the result)."""
    chains, cur = [], base
    for k, im in enumerate(images, start=first_input):
        h = max(2, int(round(float(im.get("height", 0.25)) * height / 2)) * 2)
        s0 = float(im.get("start") or 0.0)
        e0 = float(im["end"]) if im.get("end") is not None else 1e9
        d = max(0.05, float(im.get("anim") or 0.4))
        f = [f"scale=-2:{h}", "format=rgba"]
        op = float(im.get("opacity", 1.0))
        if op < 1:
            f.append(f"colorchannelmixer=aa={max(0.0, op):.3f}")
        roll = float(im.get("roll") or 0.0)
        if roll:
            a = f"{roll:g}*PI/180"
            f.append(f"rotate=a={a}:c=none:ow=rotw({a}):oh=roth({a})")
        if im.get("animation", "fade") in ("fade", "rise", "pop", "slam"):
            f.append(f"fade=t=in:st={s0:.3f}:d={d:.3f}:alpha=1")
        fo = float(im.get("fade_out") or 0.0)
        if fo > 0 and e0 < 1e8:
            f.append(f"fade=t=out:st={max(0.0, e0 - fo):.3f}:d={fo:.3f}:alpha=1")
        chains.append(f"[{k}:v]{','.join(f)}[i{k}]")
        x = f"{float(im.get('x', 0.5)) * width:.1f}-overlay_w/2"
        y = f"{float(im.get('y', 0.5)) * height:.1f}-overlay_h/2"
        if im.get("animation") == "rise":
            y += f"+{0.6 * h:.1f}*max(0,1-(t-{s0:.3f})/{d:.3f})"
        chains.append(f"[{cur}][i{k}]overlay=x='{x}':y='{y}':enable='between(t,{s0:.3f},{e0:.3f})'"
                      f":eval=frame:shortest=1[v{k}]")
        cur = f"v{k}"
    return chains, cur


def has_audio(video: str) -> bool:
    """Whether *video* has a sound track (assumed yes without ffprobe)."""
    probe = find_tool("ffprobe")
    if not probe:
        return True
    try:
        out = subprocess.run([probe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of",
                              "csv=p=0", video], capture_output=True, text=True, timeout=60).stdout
        return bool(out.strip())
    except (OSError, subprocess.SubprocessError):
        return True


def burn_in(video: str, ass_text: str, out: str, progress=None, images: list[dict] | None = None,
            music: dict | None = None) -> str:
    """Make a copy of *video* with the texts (and *images*, see :func:`image_filters`) drawn in and
    optionally a song mixed in (*music*, see :func:`videomusic.audio_args`). The original is never
    changed."""
    import tempfile
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found (install it, or use the .ass/.srt files instead)")
    if os.path.abspath(out) == os.path.abspath(video):
        raise ValueError("refusing to overwrite the original video")
    if os.path.exists(out):
        raise FileExistsError(out)
    say = progress or (lambda msg: None)
    total = video_duration(video) or 0
    work = tempfile.mkdtemp(prefix="mcsm-text-")
    try:
        with open(os.path.join(work, "t.ass"), "w", encoding="utf-8") as fh:
            fh.write(ass_text)
        part = out + ".part.mp4"
        has_text = "Dialogue:" in ass_text
        inputs, chains, cur = [], [], "0:v"
        if images:
            size = video_size(video) or (1920, 1080)
            for im in images:
                gif = os.path.splitext(im["file"])[1].lower() == ".gif"
                inputs += (["-ignore_loop", "0"] if gif else ["-loop", "1"]) + ["-i", os.path.abspath(im["file"])]
            chains, cur = image_filters(images, size[0], size[1])
        audio_map, acodec, achains = ["-map", "0:a?"], ["-c:a", "copy"], []
        if music:
            from . import videomusic as vm
            ins, achains, alabel = vm.audio_args(music, 1 + len(images or []), has_audio(video))
            inputs += ins
            audio_map, acodec = ["-map", f"[{alabel}]"], ["-c:a", "aac", "-b:a", "256k"]
        if has_text and (images or music):
            chains.append(f"[{cur}]ass=t.ass[out]")
            cur = "out"
        chains += achains
        if chains:
            graph = ["-filter_complex", ";".join(chains), "-map", "0:v" if cur == "0:v" else f"[{cur}]", *audio_map]
        else:
            graph = ["-vf", "ass=t.ass"]
        if music and total:
            graph += ["-t", f"{total:.3f}"]          # the song alone mustn't make the video longer
        cmd = [ffmpeg, "-v", "error", "-y", "-i", os.path.abspath(video), *inputs, *graph,
               *pick_encoder(ffmpeg), "-pix_fmt", "yuv420p", *acodec, "-progress", "pipe:1", "-nostats",
               os.path.abspath(part)]
        proc = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        last = -1
        for line in proc.stdout:
            if line.startswith("out_time_us=") and total:
                try:
                    pct = int(int(line.split("=")[1]) / 1e6 / total * 100)
                except ValueError:
                    continue
                if pct >= last + 5:
                    last = pct
                    say(f"  {min(pct, 100)}%")
        err = proc.stderr.read()
        if proc.wait() != 0:
            if os.path.exists(part):
                os.remove(part)
            raise RuntimeError(f"ffmpeg failed: {err.strip()[-400:]}")
        os.replace(part, out)
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- one-stop: analyse a finished video, then make the text in the world and/or on screen -----

def _replay_meta(path: str) -> dict:
    try:
        with zipfile.ZipFile(path) as zf:
            return json.loads(zf.read("metadata.json").decode("utf-8", "replace"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}


def analyse_video(video: str, world_dir: str | None = None) -> dict:
    """Everything needed to put text on a finished Flashback video: its length and size, the
    matching Flashback edits (best first), the recording, and where the camera holds still at the end."""
    from .flashback import flashback_folders
    from .library import is_world_dir, recording_uuids
    dur = video_duration(video)
    if dur is None:
        raise RuntimeError("ffprobe (part of ffmpeg) is needed to read the video's length")
    size = video_size(video) or (1920, 1080)
    matches = find_scenes_for_video(dur, video_mtime=os.path.getmtime(video))
    out = {"video": video, "duration": dur, "width": size[0], "height": size[1], "matches": matches}
    roots, cache = [], None
    if world_dir and is_world_dir(world_dir):
        roots.append(os.path.join(world_dir, "recordings"))
        cache = os.path.join(world_dir, "recordings", ".uuid-index.json")
    roots += [os.path.join(f["path"], "replays") for f in flashback_folders()]
    by_uuid = recording_uuids(roots, cache) if matches else {}
    for mt in matches:
        mt["recording"] = by_uuid.get(mt["uuid"])
        # A replay the app made (text added) is never used as a source: go back to its original.
        meta = _replay_meta(mt["recording"]) if mt["recording"] else {}
        if meta.get("original_uuid_mcsm") or meta.get("mcsm_world_text"):
            mt["copy"] = True
            original = meta.get("original_uuid_mcsm")
            while original and original in by_uuid:
                omega = _replay_meta(by_uuid[original])
                if not (omega.get("original_uuid_mcsm") or omega.get("mcsm_world_text")):
                    break
                original = omega.get("original_uuid_mcsm")
            mt["recording"] = by_uuid.get(original) if original else None
    own = [mt for mt in matches if not mt.get("copy") and mt.get("recording")]
    if own:                                     # your own edits first; the app's copies only as a fallback
        matches[:] = own + [mt for mt in matches if mt not in own]
    for mt in matches:
        mt["still_from"] = None
        if True:                                    # needs only the edit, not the recording
            sc = scene_for_match(mt)
            clock = scene_clock(sc)
            still = still_camera_at_end(sc)
            mt["still_from"] = clock.video_time(still[0]) if (still and clock) else None
    return out


def fov_at(scene: dict, tick: float, default: float = 70.0) -> float:
    """The camera's field of view (vertical, degrees) at *tick* from the scene's FOV keyframes."""
    for t in scene.get("keyframeTracks") or []:
        if t.get("keyframeType") == "FOV" and t.get("enabled", True):
            keys = sorted((int(k), float(v.get("fov", default))) for k, v in (t.get("keyframesByTick") or {}).items()
                          if isinstance(v, dict))
            if not keys:
                return default
            if tick <= keys[0][0]:
                return keys[0][1]
            for (t1, a), (t2, b) in zip(keys, keys[1:]):
                if t1 <= tick <= t2:
                    return a + (b - a) * (tick - t1) / max(1, t2 - t1)
            return keys[-1][1]
    return default


def project(cam: list[float], yaw: float, pitch: float, fov: float, width: int, height: int,
            point: tuple[float, float, float]) -> tuple[float, float, float] | None:
    """Where a world point lands in the picture: ``(x px, y px, pixels per block there)``, or None
    when it's behind the camera. Minecraft angles: yaw 0 looks south (+z), pitch up is negative."""
    import math
    ry, rp = math.radians(yaw), math.radians(pitch)
    f = (-math.sin(ry) * math.cos(rp), -math.sin(rp), math.cos(ry) * math.cos(rp))
    r = (-math.cos(ry), 0.0, -math.sin(ry))
    u = (r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0])
    d = (point[0] - cam[0], point[1] - cam[1], point[2] - cam[2])
    z = sum(a * b for a, b in zip(d, f))
    if z <= 0.1:
        return None
    x = sum(a * b for a, b in zip(d, r))
    y = sum(a * b for a, b in zip(d, u))
    focal = (height / 2) / math.tan(math.radians(fov) / 2)
    return width / 2 + x / z * focal, height / 2 - y / z * focal, focal / z


def world_layout(match: dict, lines: list[tuple[str, float]], at_seconds: float | None, distance: float = 55.0,
                 right: float = 0.0, up: float = 0.0, size_scale: float = 1.0,
                 spot: list[float] | None = None) -> dict:
    """Where in-world *lines* go: in front of the camera at *at_seconds* (None: where the camera stops
    at the end), *distance* blocks away, moved *right*/*up* blocks (as seen from the camera), letters
    *size_scale* times bigger. Returns the camera, the tick and ``texts: [(text, size, x, y, z)]``."""
    from . import worldtext as wt
    import math
    sc = scene_for_match(match)
    clock = scene_clock(sc)
    secs = at_seconds if at_seconds is not None else (match["still_from"] if match.get("still_from") is not None else clock.duration) + 0.1
    tick = clock.replay_tick(min(secs, clock.duration))
    cam = wt.camera_at(sc, tick)
    if cam is None:
        raise ValueError("this edit has no camera keyframes to place the text by")
    pos, yaw, pitch = cam
    ry = math.radians(yaw)
    sizes = [size * size_scale for _t, size in lines]
    if spot:                    # standing on a spot in the world (e.g. a mountain): lines go up from it
        cx, cy, cz = spot
        top = cy + sum(sizes) + 0.25 * sum(sizes[:-1]) + up
    else:                       # in front of the camera, in the upper part of the picture, like a title
        cx, cy, cz = wt.in_front(pos, yaw, pitch, distance)
        top = cy + distance * 0.32 + up
    cx, cz = cx - math.cos(ry) * right, cz - math.sin(ry) * right          # the camera's right
    # A text display stands on its position (the letters go up from it), so each line stands its
    # own height plus a small gap below the previous one.
    texts = []
    for text, size in lines:
        size *= size_scale
        base = top - size
        texts.append((text, size, cx, base, cz))
        top = base - size * 0.25
    return {"scene": sc, "clock": clock, "seconds": secs, "tick": tick, "camera": pos, "yaw": yaw, "pitch": pitch,
            "fov": fov_at(sc, tick), "texts": texts}


def world_preview_titles(layout: dict, width: int, height: int, colour: str = "white",
                         border_colour: str = "black") -> list[Title]:
    """On-screen stand-ins drawn where the in-world text will appear (for a preview frame)."""
    out = []
    for text, size, x, y, z in layout["texts"]:
        pr = project(layout["camera"], layout["yaw"], layout["pitch"], layout["fov"], width, height,
                     (x, y + size * 0.5, z))
        if pr is None:
            continue
        sx, sy, ppb = pr
        out.append(Title(text, 0, 1e6, size=max(0.005, size * ppb / height), y=sy / height, x=sx / width,
                         colour=colour, style="outline", animation="none", border_colour=border_colour))
    return out


def make_in_world(match: dict, lines: list[tuple[str, float]], at_seconds: float | None, colour: str = "white",
                  outline: bool = True, distance: float = 55.0, flashback_dir: str | None = None,
                  progress=None, until_seconds: float | None = None, always: bool = False,
                  animation: str = "none", anim: float = 0.4, word_gap: float = 0.3, fade_out: float = 0.0,
                  border_colour: str = "black", glow: bool = True, shadow: bool = False,
                  box: str = "", right: float = 0.0, up: float = 0.0, size_scale: float = 1.0,
                  video: str | None = None) -> dict:
    """Put *lines* (text, letter height in blocks; first = top) into the world of a copy of the
    matched recording, in front of the camera at *at_seconds* of the video (None: where the camera
    stops at the end). The text appears at that moment and stays until *until_seconds* (None: to
    the end); with *always* it is there for the whole recording. The copy and a copy of the edit
    (with the export range of this video) go into the Flashback folder for the recording's
    Minecraft version."""
    texts, secs = world_texts(match, lines, at_seconds, colour, outline, distance, until_seconds, always, animation,
                              anim, word_gap, fade_out, border_colour, glow, shadow, box, right, up, size_scale)
    r = write_world_copy(match, texts, video, flashback_dir, progress)
    return {**r, "appears_at": None if always else secs, "until": until_seconds}


def world_texts(match: dict, lines: list[tuple[str, float]], at_seconds: float | None, colour: str = "white",
                outline: bool = True, distance: float = 55.0, until_seconds: float | None = None,
                always: bool = False, animation: str = "none", anim: float = 0.4, word_gap: float = 0.3,
                fade_out: float = 0.0, border_colour: str = "black", glow: bool = True, shadow: bool = False,
                box: str = "", right: float = 0.0, up: float = 0.0, size_scale: float = 1.0,
                spot: list[float] | None = None, follow: dict | None = None, *, opacity: float = 1.0,
                roll: float = 0.0, facing: str = "auto", turn: float = 0.0, tilt: float = 0.0,
                depth: float = 0.0, italic: bool = False, underline: bool = False):
    """The text displays for one text (one or more lines) and the video second it appears at.
    *follow* ``{"eid", "x", "y", "z"}``: the text rides on that player (above their head).
    *facing*: auto (turns to the camera; upright on a spot), camera, upright, fixed (stays as it
    was when it appeared, turned by *turn* and tipped by *tilt* degrees: -90 = lying face up).
    *roll*: turned in its own plane (degrees clockwise). *depth*: 3D letters (share of the height)."""
    from . import worldtext as wt
    lay = world_layout(match, lines, at_seconds, distance, right, up, size_scale,
                       [follow["x"], follow["y"], follow["z"]] if follow else spot)
    clock, secs, yaw = lay["clock"], lay["seconds"], lay["yaw"]
    appear = None if always else int(lay["tick"])
    gone = None if (always or until_seconds is None) else int(clock.replay_tick(min(until_seconds, clock.duration)))
    texts = []
    start = appear
    box_argb = 0
    if box:
        r_, g_, b_ = (int(_bgr(box)[i:i + 2], 16) for i in (6, 4, 2))
        box_argb = (0x90 << 24) | (r_ << 16) | (g_ << 8) | b_
        box_argb = box_argb - (1 << 32) if box_argb >= 1 << 31 else box_argb
    lift = 0.3 + up                                 # riding: above the head, plus "up"
    rows = lay["texts"]
    sizes = [sz for _t, sz, *_ in rows]
    op = 255 if opacity >= 1 else max(26, int(round(255 * opacity)))
    fixed = facing == "fixed" and not follow
    if follow:
        face = "vertical" if facing == "upright" else "center"
    elif fixed:
        face = "fixed"
    else:
        face = "vertical" if facing == "upright" or (facing == "auto" and spot) else "center"
    top_y = rows[0][3] + rows[0][1] if rows else 0.0
    foot = rows[-1] if rows else None
    for i, (text, size, x, y, z) in enumerate(rows):
        below = sum(sizes[i + 1:]) + 0.25 * sum(sizes[i + 1:])
        if follow:
            off = (right, lift + below, 0.0)
            piv = (right, lift + (sizes[0] + 1.25 * sum(sizes[1:])) / 2, 0.0)
        elif fixed:                                 # all lines on the bottom line's foot, stacked in its own space
            off = (0.0, y - foot[3], 0.0)
            piv = (0.0, (top_y - foot[3]) / 2, 0.0)
            x, y, z = foot[2], foot[3], foot[4]
        else:
            off = (0.0, 0.0, 0.0)
            piv = (0.0, (top_y + foot[3]) / 2 - y, 0.0)
        texts.append(wt.WorldText(text, x, y, z, size=size, colour=colour, bold=True, facing=face,
                                  yaw=(yaw + 180 + turn) % 360 if fixed else 0.0, pitch=tilt if fixed else 0.0,
                                  roll=roll, pivot=piv, depth=depth, italic=italic, underline=underline,
                                  local=fixed, opacity=op,
                                  ride=follow["eid"] if follow else None, offset=off,
                                  outline=border_colour if outline else "",
                                  camera_yaw=(yaw + turn) if fixed else yaw,
                                  appear_tick=start, disappear_tick=gone, animation=animation,
                                  anim_ticks=max(1, int(anim * 20)), word_gap_ticks=max(1, int(word_gap * 20)),
                                  fade_out_ticks=int(fade_out * 20), glow=glow, shadow=shadow, background=box_argb))
        if start is not None and animation != "none":   # the next line starts when this one has appeared
            n = len(text.split()) if animation.startswith("words") else 1
            start += (n - 1) * max(1, int(word_gap * 20)) + max(1, int(anim * 20))
    return texts, secs


def write_world_copy(match: dict, texts: list, video: str | None = None, flashback_dir: str | None = None,
                     progress=None, markers: dict | None = None, song: tuple[str, int] | None = None) -> dict:
    """Write the Flashback copy of the matched recording with all *texts* in its world (one copy per
    video: an earlier copy for *video* is replaced, its edit kept)."""
    from . import flashback as fbm, worldtext as wt
    say = progress or (lambda msg: None)
    src = match.get("recording")
    if not src:
        raise ValueError("the recording of this edit wasn't found (import it into the library first)")
    with zipfile.ZipFile(src) as zf:
        mc = json.loads(zf.read("metadata.json").decode("utf-8")).get("version_string")
    fb = flashback_dir or fbm.find_flashback_dir(None, mc)
    if not fb:
        raise ValueError("no Flashback folder found")
    # One copy per video: making it again replaces the earlier copy (same name and id, so Flashback
    # keeps any changes you made to its edit) instead of piling up gigabyte-sized copies.
    earlier = find_text_copy(fb, video) if video else None
    if earlier:
        dst, keep = earlier["path"], earlier["uuid"]
        say(f"Replacing the earlier copy {os.path.basename(dst)}…")
    else:
        keep = None
        base = os.path.splitext(os.path.basename(src))[0] + " (text)"
        dst = os.path.join(fb, "replays", base + ".zip")
        n = 2
        while os.path.exists(dst):
            dst = os.path.join(fb, "replays", f"{base} {n}.zip")
            n += 1
        say(f"Writing a copy of {os.path.basename(src)} with the text in its world…")
    meta = wt.write_world_text_copy(src, dst, texts, keep_uuid=keep, video=video, markers=markers)
    edit_path = os.path.join(fb, "editor_states", meta["uuid"] + ".json")
    if os.path.exists(edit_path):
        edit = edit_path                              # keep your edit of the earlier copy as it is
    else:
        edit = wt.copy_edit(match["state"], edit_path, match["uuid"], (match["start"], match["end"]))
    from . import videomusic as vm
    vm.set_audio_track(edit, song[0] if song else None, song[1] if song else 0)   # only the app's song track
    return {"replay": dst, "edit": edit, "name": meta["name"], "texts": texts, "flashback": fb,
            "replaced": bool(earlier)}


ITEM_DEFAULTS = {"kind": "world", "lines": [], "start": None, "until": None, "animation": "pop", "anim": 0.4,
                 "word_gap": 0.3, "fade_out": 0.0, "colour": "white", "border": True, "border_colour": "black",
                 "shadow": False, "glow": True, "box": False, "style": "outline", "font": "",
                 "right": 0.0, "up": 0.0, "distance": 55.0, "size": 1.0, "x": 0.5, "y": 0.16, "spot": None,
                 "follow": None, "id": None, "after": None, "length": None, "opacity": 1.0, "roll": 0.0,
                 "facing": "auto", "turn": 0.0, "tilt": 0.0, "depth": 0.0, "italic": False, "underline": False,
                 "file": "", "height": 0.25}
ITEM_KINDS = ("world", "screen", "image")


def _item(raw: dict) -> dict:
    it = {**ITEM_DEFAULTS, **{k: v for k, v in raw.items() if k in ITEM_DEFAULTS}}
    it["lines"] = [(str(t), float(sz)) for t, sz in it["lines"] if str(t).strip()]
    for key in ("start", "until", "length"):
        it[key] = None if it[key] in (None, "") else float(it[key])
    if it["style"] == "3d" and not it["depth"]:          # older projects: "3D" letters were a style
        it["style"], it["depth"] = "outline", 0.14
    return it


def appear_time(it: dict) -> float:
    """Seconds until all of an item's lines (and words) have appeared."""
    a = it.get("animation", "none")
    if a == "none":
        return 0.0
    total = 0.0
    for text, _sz in it.get("lines") or [("", 0)]:
        n = len(str(text).split()) if a.startswith("words") else 1
        total += max(0, n - 1) * float(it.get("word_gap", 0.3)) + float(it.get("anim", 0.4))
    return total


def resolve_times(items: list[dict], duration: float, default_start) -> list[dict]:
    """Items (normalised with :func:`_item`) with real ``start`` and ``until`` seconds: an empty start
    is *default_start(item)*, a ``length`` sets the end, and a follow-up (``after: {id, gap, from}``)
    starts *gap* seconds after the start or end of the item it follows (chains of any length; a
    loop or a missing item falls back to its own start)."""
    by_id = {it["id"]: it for it in items if it.get("id") is not None}
    memo: dict[int, tuple[float, float | None]] = {}

    def times(it: dict, seen: frozenset) -> tuple[float, float | None]:
        key = id(it)
        if key in memo:
            return memo[key]
        start = it["start"] if it["start"] is not None else default_start(it)
        after = it.get("after") if isinstance(it.get("after"), dict) else None
        parent = by_id.get(after.get("id")) if after else None
        if parent is not None and parent.get("id") not in seen and parent is not it:
            ps, pe = times(parent, seen | {it.get("id")})
            if after.get("from") == "start":
                base = ps
            else:
                base = pe if pe is not None else ps + appear_time(parent) + 1.0
            start = max(0.0, base + float(after.get("gap") or 0.0))
        end = it["until"]
        if it.get("length") is not None:
            end = start + float(it["length"])
        if end is not None and duration:
            end = min(end, duration + 1)
        memo[key] = (start, end)
        return start, end

    out = []
    for it in items:
        st, en = times(it, frozenset({it.get("id")}))
        out.append({**it, "start": round(st, 3), "until": None if en is None else round(en, 3)})
    return out


def make_items_in_world(match: dict, items: list[dict], video: str | None = None, flashback_dir: str | None = None,
                        progress=None, players: dict | None = None, music: dict | None = None,
                        music_dir: str | None = None) -> dict:
    """All 'in the world' texts of an editor project in one Flashback copy (each with its own time,
    place, animation and look). *players* ``{16-byte uuid: name}`` is needed for texts that follow a
    player."""
    from . import paths
    texts = []
    clock0 = scene_clock(scene_for_match(match))
    still = match.get("still_from")
    dur = clock0.duration if clock0 else 0.0

    def default(it):
        if still is not None:
            return still + (0.1 if it["kind"] == "world" else 0.2)
        return max(0.0, dur - 4)
    for it in resolve_times([_item(r) for r in items], dur, default):
        if it["kind"] != "world" or not it["lines"]:
            continue
        follow = None
        if it["follow"]:
            sc = scene_for_match(match)
            clock = scene_clock(sc)
            start = it["start"]
            tick = int(clock.replay_tick(min(start, clock.duration)))
            seen = paths.players_in_view(match["recording"], tick, players or {})
            follow = seen.get(it["follow"])
            if follow is None:
                raise ValueError(f"{it['follow']} isn't in view at {start:.1f} s, so the text can't follow them; "
                                 "pick another moment or player")
        t, _secs = world_texts(match, it["lines"], it["start"], it["colour"], it["border"], it["distance"],
                               it["until"], False, it["animation"], it["anim"], it["word_gap"], it["fade_out"],
                               it["border_colour"], it["glow"], it["shadow"], "black" if it["box"] else "",
                               it["right"], it["up"], it["size"], it["spot"] or None, follow,
                               opacity=float(it["opacity"]), roll=float(it["roll"]), facing=it["facing"],
                               turn=float(it["turn"]), tilt=float(it["tilt"]), depth=float(it["depth"]),
                               italic=bool(it["italic"]), underline=bool(it["underline"]))
        texts += t
    markers, song, warning = None, None, ""
    if music and music.get("file"):
        from . import videomusic as vm
        say = progress or (lambda m: None)
        say("Lining the song and its beats up with the edit...")
        mp = vm.plan(match, music, music_dir or os.path.dirname(os.path.abspath(music["file"])))
        markers, song, warning = mp["markers"], (mp["path"], mp["tick"]), mp["warning"]
        say(f"Song at replay tick {mp['tick']}, {len(markers)} beat markers"
            + (f" ({mp['bpm']:.1f} BPM)" if mp.get("bpm") else ""))
        if warning:
            say("Note: " + warning)
    if not texts and not song:
        raise ValueError("there are no 'in the world' texts and no song")
    r = write_world_copy(match, texts, video, flashback_dir, progress, markers=markers, song=song)
    return {**r, "music_warning": warning, "beats": len(markers or {})}


def make_items_on_screen(video: str, items: list[dict], duration: float, default_start: float = 0.0,
                         out: str | None = None, progress=None, image_dir: str | None = None,
                         still_from: float | None = None, music: dict | None = None) -> str:
    """All 'on the video' texts and images of an editor project drawn into one new copy of *video*.
    Image items name a file in *image_dir* (where the editor keeps the pictures you added)."""
    size = video_size(video) or (1920, 1080)
    titles, images = [], []

    def default(it):
        if still_from is not None:
            return still_from + (0.1 if it["kind"] == "world" else 0.2)
        return default_start
    for it in resolve_times([_item(r) for r in items], duration, default):
        end = it["until"] if it["until"] is not None else duration + 1
        if it["kind"] == "image" and it["file"]:
            name = os.path.basename(str(it["file"]))
            path = os.path.join(image_dir, name) if image_dir else str(it["file"])
            if not os.path.isfile(path):
                raise ValueError(f"the picture {name} isn't there any more: add it again")
            images.append({"file": path, "start": it["start"], "end": end, "x": it["x"], "y": it["y"],
                           "height": it["height"], "opacity": it["opacity"], "roll": it["roll"],
                           "animation": it["animation"], "anim": it["anim"], "fade_out": it["fade_out"]})
            continue
        if it["kind"] != "screen" or not it["lines"]:
            continue
        lines = [(t, sz * it["size"] / 40.0) for t, sz in it["lines"]]   # blocks -> share of the height
        for ti in screen_titles(lines, it["start"], end, it["style"], it["colour"], it["animation"], it["anim"],
                                it["word_gap"], it["fade_out"], it["border_colour"], it["shadow"],
                                "black" if it["box"] else "", it["x"], it["y"], opacity=float(it["opacity"]),
                                roll=float(it["roll"]), tilt=float(it["tilt"]), turn=float(it["turn"]),
                                depth=float(it["depth"]), italic=bool(it["italic"]),
                                underline=bool(it["underline"])):
            ti.font = it["font"]
            titles.append(ti)
    if not titles and not images and not music:
        raise ValueError("there are no 'on the video' texts, pictures or song")
    if out is None:
        stem, ext = os.path.splitext(video)
        out, n = f"{stem} (with text){ext}", 2
        while os.path.exists(out):
            out, n = f"{stem} (with text {n}){ext}", n + 1
    return burn_in(video, titles_ass(titles, size[0], size[1]), out, progress, images=images, music=music)


def find_text_copy(flashback_dir: str, video: str) -> dict | None:
    """The copy made earlier for *video* (``mcsm_video`` in its metadata), if any."""
    key = os.path.normcase(os.path.abspath(video))
    d = os.path.join(flashback_dir, "replays")
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not name.lower().endswith(".zip"):
            continue
        meta = _replay_meta(os.path.join(d, name))
        if meta.get("mcsm_video") == key and meta.get("uuid"):
            return {"path": os.path.join(d, name), "uuid": meta["uuid"]}
    return None


def screen_titles(lines: list[tuple[str, float]], start: float, end: float, style: str = "outline",
                  colour: str = "white", animation: str = "fade", anim: float = 0.4, word_gap: float = 0.3,
                  fade_out: float = 0.0, border_colour: str = "black", shadow: bool = True,
                  box: str = "", x: float = 0.5, top: float = 0.16, **look) -> list[Title]:
    """Titles for *lines* (text, share of the picture height), top to bottom from *top* (share of the
    height), centred on *x*; with an animation the next line starts once the previous one appeared.
    *look*: opacity, roll, tilt, turn, depth, italic, underline (see :class:`Title`); turning is
    about the middle of all the lines."""
    titles, y, t0 = [], top, start
    for text, h in lines:
        titles.append(Title(text, t0, end, size=h, y=y + h / 2, colour=colour, style=style, fade_in=anim,
                            fade_out=fade_out, animation=animation, anim=anim, word_gap=word_gap,
                            border_colour=border_colour, shadow=shadow, box=box, x=x, **look))
        y += h * 1.25
        if animation != "none":
            n = len(text.split()) if animation.startswith("words") else 1
            t0 += (n - 1) * word_gap + anim
    middle = (top + (y - (lines[-1][1] * 0.25 if lines else 0))) / 2
    for ti in titles:
        ti.org = (x, middle)
    return titles


def make_on_screen(video: str, lines: list[tuple[str, float]], start: float, end: float, style: str = "outline",
                   colour: str = "white", out: str | None = None, progress=None, **look) -> str:
    """A copy of *video* with *lines* (text, share of the picture height) as big titles from
    *start* to *end* seconds; *look*: see :func:`screen_titles`."""
    size = video_size(video) or (1920, 1080)
    titles = screen_titles(lines, start, end, style, colour, **look)
    if out is None:
        stem, ext = os.path.splitext(video)
        out, n = f"{stem} (with text){ext}", 2
        while os.path.exists(out):
            out, n = f"{stem} (with text {n}){ext}", n + 1
    return burn_in(video, titles_ass(titles, size[0], size[1]), out, progress)
