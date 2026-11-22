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
    border: float = 1.0             # border thickness (1 = normal)
    shadow: bool = True
    box: str = ""                   # background box colour ("" = none)


TITLE_ANIMATIONS = ("none", "fade", "pop", "words", "words-fade", "words-pop")


def _word_tags(t: Title, words: list[str]) -> str:
    """One line whose words appear one by one (the layout stays fixed: each word is drawn from the
    start, invisible until its moment)."""
    bs = "\\"
    out = []
    for i, w in enumerate(words):
        a = int((i * t.word_gap) * 1000)
        d = max(1, int(t.anim * 1000))
        if t.animation == "words":
            tags = f"{bs}alpha&HFF&{bs}t({a},{a + 1},{bs}alpha&H00&)"
        elif t.animation == "words-fade":
            tags = f"{bs}alpha&HFF&{bs}t({a},{a + d},{bs}alpha&H00&)"
        else:                                           # words-pop: grows up from the line, snappy
            tags = (f"{bs}alpha&HFF&{bs}fscy0{bs}t({a},{a + 1},{bs}alpha&H00&)"
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
        fade_in = t.fade_in if t.animation == "fade" else (t.anim * 0.5 if t.animation == "pop" else 0.0)
        fad = f"{bs}fad({int(fade_in * 1000)},{int(t.fade_out * 1000)})"
        pop = ""
        if t.animation == "pop":
            d = max(2, int(t.anim * 1000))
            pop = f"{bs}fscx0{bs}fscy0{bs}t(0,{d * 6 // 10},{bs}fscx118{bs}fscy118){bs}t({d * 6 // 10},{d},{bs}fscx100{bs}fscy100)"
        body = _word_tags(t, words) if by_word else _ass_escape(t.text)
        if t.box:
            pad = max(4, px // 6)
            lines.append(f"Dialogue: {layer},{span},box,,0,0,0,,{{{fad}{pop}{bs}fs{px}{bs}pos({cx},{cy}){bs}1c&HFFFFFF&"
                         f"{bs}alpha&HFF&{bs}3c{_bgr(t.box)}{bs}3a&H60&{bs}bord{pad}}}{_ass_escape(t.text)}")
            layer += 1
        if t.style == "3d":
            depth = max(4, px // 7)
            for i in range(depth, 0, -1):
                shade = int(150 - 90 * i / depth) // 2
                lines.append(f"Dialogue: {layer},{span},t,,0,0,0,,{{{fad}{pop}{bs}fs{px}{bs}pos({cx + i // 3},{cy + i})"
                             f"{bs}1c&H{shade:02X}{shade:02X}{shade:02X}&{bs}bord{max(2, px // 45)}"
                             f"{bs}3c&H101010&}}{body}")
                layer += 1
            lines.append(f"Dialogue: {layer},{span},t,,0,0,0,,{{{fad}{pop}{bs}fs{px}{bs}pos({cx},{cy}){bs}1c{_bgr(t.colour)}"
                         f"{bs}bord{max(2, px // 45)}{bs}3c&H181818&}}{body}")
        else:
            bord = max(0, int(px / 12 * t.border)) if t.style == "outline" else 0
            shad = max(1, px // 25) if t.shadow else 0
            lines.append(f"Dialogue: {layer},{span},t,,0,0,0,,{{{fad}{pop}{bs}fs{px}{bs}pos({cx},{cy}){bs}1c{_bgr(t.colour)}"
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


def burn_in(video: str, ass_text: str, out: str, progress=None) -> str:
    """Make a copy of *video* with the texts drawn in (the original is never changed)."""
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
        cmd = [ffmpeg, "-v", "error", "-y", "-i", os.path.abspath(video), "-vf", "ass=t.ass",
               *pick_encoder(ffmpeg), "-pix_fmt", "yuv420p", "-c:a", "copy", "-progress", "pipe:1", "-nostats",
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
        if mt["recording"]:
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
                 right: float = 0.0, up: float = 0.0, size_scale: float = 1.0) -> dict:
    """Where in-world *lines* go: in front of the camera at *at_seconds* (None: where the camera stops
    at the end), *distance* blocks away, moved *right*/*up* blocks (as seen from the camera), letters
    *size_scale* times bigger. Returns the camera, the tick and ``texts: [(text, size, x, y, z)]``."""
    from . import worldtext as wt
    import math
    sc = scene_for_match(match)
    clock = scene_clock(sc)
    secs = at_seconds if at_seconds is not None else (match.get("still_from") or clock.duration) + 0.1
    tick = clock.replay_tick(min(secs, clock.duration))
    cam = wt.camera_at(sc, tick)
    if cam is None:
        raise ValueError("this edit has no camera keyframes to place the text by")
    pos, yaw, pitch = cam
    cx, cy, cz = wt.in_front(pos, yaw, pitch, distance)
    ry = math.radians(yaw)
    cx, cz = cx - math.cos(ry) * right, cz - math.sin(ry) * right          # the camera's right
    texts, y = [], cy + distance * 0.32 + up       # upper part of the picture, like a title
    for text, size in lines:
        size *= size_scale
        y -= size * 1.35
        texts.append((text, size, cx, y + size * 1.35, cz))
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
                  box: str = "", right: float = 0.0, up: float = 0.0, size_scale: float = 1.0) -> dict:
    """Put *lines* (text, letter height in blocks; first = top) into the world of a copy of the
    matched recording, in front of the camera at *at_seconds* of the video (None: where the camera
    stops at the end). The text appears at that moment and stays until *until_seconds* (None: to
    the end); with *always* it is there for the whole recording. The copy and a copy of the edit
    (with the export range of this video) go into the Flashback folder for the recording's
    Minecraft version."""
    from . import flashback as fbm, worldtext as wt
    say = progress or (lambda msg: None)
    src = match.get("recording")
    if not src:
        raise ValueError("the recording of this edit wasn't found (import it into the library first)")
    lay = world_layout(match, lines, at_seconds, distance, right, up, size_scale)
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
    for text, size, x, y, z in lay["texts"]:
        texts.append(wt.WorldText(text, x, y, z, size=size, colour=colour, bold=True,
                                  outline=border_colour if outline else "", camera_yaw=yaw,
                                  appear_tick=start, disappear_tick=gone, animation=animation,
                                  anim_ticks=max(1, int(anim * 20)), word_gap_ticks=max(1, int(word_gap * 20)),
                                  fade_out_ticks=int(fade_out * 20), glow=glow, shadow=shadow, background=box_argb))
        if start is not None and animation != "none":   # the next line starts when this one has appeared
            n = len(text.split()) if animation.startswith("words") else 1
            start += (n - 1) * max(1, int(word_gap * 20)) + max(1, int(anim * 20))
    with zipfile.ZipFile(src) as zf:
        mc = json.loads(zf.read("metadata.json").decode("utf-8")).get("version_string")
    fb = flashback_dir or fbm.find_flashback_dir(None, mc)
    if not fb:
        raise ValueError("no Flashback folder found")
    base = os.path.splitext(os.path.basename(src))[0] + " (text)"
    dst = os.path.join(fb, "replays", base + ".zip")
    n = 2
    while os.path.exists(dst):
        dst = os.path.join(fb, "replays", f"{base} {n}.zip")
        n += 1
    say(f"Writing a copy of {os.path.basename(src)} with the text in its world…")
    meta = wt.write_world_text_copy(src, dst, texts)
    edit = wt.copy_edit(match["state"], os.path.join(fb, "editor_states", meta["uuid"] + ".json"), match["uuid"],
                        (match["start"], match["end"]))
    return {"replay": dst, "edit": edit, "name": meta["name"], "texts": texts, "flashback": fb,
            "appears_at": None if always else secs, "until": until_seconds}


def screen_titles(lines: list[tuple[str, float]], start: float, end: float, style: str = "outline",
                  colour: str = "white", animation: str = "fade", anim: float = 0.4, word_gap: float = 0.3,
                  fade_out: float = 0.0, border_colour: str = "black", shadow: bool = True,
                  box: str = "") -> list[Title]:
    """Titles for *lines* (text, share of the picture height), top to bottom; with an animation the
    next line starts once the previous one has appeared."""
    titles, y, t0 = [], 0.16, start
    for text, h in lines:
        titles.append(Title(text, t0, end, size=h, y=y + h / 2, colour=colour, style=style, fade_in=anim,
                            fade_out=fade_out, animation=animation, anim=anim, word_gap=word_gap,
                            border_colour=border_colour, shadow=shadow, box=box))
        y += h * 1.25
        if animation != "none":
            n = len(text.split()) if animation.startswith("words") else 1
            t0 += (n - 1) * word_gap + anim
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
