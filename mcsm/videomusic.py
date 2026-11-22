"""Music for a Flashback edit: the song on Flashback's own AUDIO track, plus beat markers.

Flashback (read from its source, master, 2026-11-22) stores an audio clip as one keyframe on an
``AUDIO`` track of the edit (editor state)::

    {"keyframeType": "AUDIO", "keyframesByTick": {"<tick>": {"path": "<absolute path>", "type": "audio",
     "interpolation_type": "LINEAR"}}, "enabled": true}

The clip starts at that replay tick (playing from later seeks into it), at a fixed volume, with its
pitch following the replay speed (so timelapse parts make it fast). Export records the game's sound,
so the song comes out in the exported video. Replay markers (``metadata.json`` → ``markers``:
``{"<tick>": {"colour": int RGB, "description": str}}``) are drawn as dots on Flashback's timeline;
clicking one jumps there.

So the app writes, into its own Flashback copy of the recording and that copy's edit (never your
original): the song (a copy kept in the app's folder, trimmed when it should start part-way) on an
AUDIO track named ``mcsm music``, and a marker on every beat or bar, lined up through the edit's
time mapping. Camera moves can then be put on the beat in Flashback's own editor.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess

TRACK_NAME = "mcsm music"
FLASHBACK_AUDIO = (".mp3", ".ogg", ".wav", ".flac", ".opus", ".aiff", ".au")
SONG_TYPES = FLASHBACK_AUDIO + (".m4a", ".aac", ".wma")
BAR_COLOUR, BEAT_COLOUR = 0xFFAA00, 0x55AAFF
MARK = "♪ "                                # descriptions of the app's beat markers start with this


def song_copy(song: str, offset: float, music_dir: str) -> str:
    """The file Flashback plays: a copy of *song* in *music_dir* (so moving or deleting the
    original doesn't break the edit), cut to start *offset* seconds into the song. Converted to
    .ogg with ffmpeg when Flashback can't play the format or it has to be cut."""
    from .videotext import find_tool
    if not os.path.isfile(song):
        raise ValueError(f"the song isn't there any more: {song}")
    ext = os.path.splitext(song)[1].lower()
    st = os.stat(song)
    key = hashlib.sha1(f"{os.path.abspath(song)}|{st.st_size}|{st.st_mtime}|{offset:.3f}".encode()).hexdigest()[:16]
    os.makedirs(music_dir, exist_ok=True)
    if offset <= 0 and ext in FLASHBACK_AUDIO:
        dst = os.path.join(music_dir, f"flashback-{key}{ext}")
        if not os.path.exists(dst):
            shutil.copyfile(song, dst + ".part")
            os.replace(dst + ".part", dst)
        return os.path.abspath(dst)
    ff = find_tool("ffmpeg")
    if not ff:
        raise ValueError("ffmpeg is needed to start the song part-way or to convert it for Flashback")
    dst = os.path.join(music_dir, f"flashback-{key}.ogg")
    if not os.path.exists(dst):
        part = dst + ".part.ogg"
        r = subprocess.run([ff, "-v", "error", "-y", "-ss", f"{max(0.0, offset):.3f}", "-i", song, "-vn",
                            "-c:a", "libvorbis", "-q:a", "6", part], capture_output=True, text=True)
        if r.returncode != 0:
            if os.path.exists(part):
                os.remove(part)
            raise RuntimeError(f"ffmpeg couldn't convert the song: {r.stderr.strip()[-300:]}")
        os.replace(part, dst)
    return os.path.abspath(dst)


def beat_grid(music: dict, song_path: str | None = None):
    """The song's beat grid: the tempo given in *music* (``bpm``, ``first_beat``) or detected."""
    from . import music as mus
    if music.get("bpm"):
        return mus.BeatGrid(bpm=float(music["bpm"]), first_beat=float(music.get("first_beat") or 0.0),
                            beats_per_bar=int(music.get("beats_per_bar") or 4))
    return mus.detect_beats(song_path or music["file"])


def plan(match: dict, music: dict, music_dir: str) -> dict:
    """Everything to put *music* into the Flashback copy of *match*'s edit.

    *music*: ``file`` (the song), ``start`` (second of the video where it starts), ``offset``
    (seconds into the song to start from), ``markers`` (``beats``, ``bars`` or ``none``), and
    optionally ``bpm`` / ``first_beat`` (else detected). Returns ``{path, tick, markers, warning}``."""
    from .videotext import scene_clock, scene_for_match
    clock = scene_clock(scene_for_match(match))
    if clock is None:
        raise ValueError("this edit has no export range to line the song up with")
    start = max(0.0, float(music.get("start") or 0.0))
    offset = max(0.0, float(music.get("offset") or 0.0))
    path = song_copy(music["file"], offset, music_dir)
    tick = int(round(clock.replay_tick(min(start, clock.duration))))
    markers: dict[str, dict] = {}
    mode = music.get("markers", "beats")
    grid = None
    if mode in ("beats", "bars"):
        grid = beat_grid(music, music["file"])
        grid.music_start = start - offset
        per_bar = grid.beats_per_bar
        for v, k in grid.beat_times(clock.duration):
            if v < start - 1e-6:
                continue
            bar = k % per_bar == 0
            if mode == "bars" and not bar:
                continue
            t = int(round(clock.replay_tick(v)))
            label = f"Bar {k // per_bar + 1}" if bar else f"Beat {k // per_bar + 1}.{k % per_bar + 1}"
            markers[str(t)] = {"colour": BAR_COLOUR if bar else BEAT_COLOUR, "description": MARK + label}
    # Flashback plays audio at the replay's speed: sped-up parts make the song fast.
    fast = [a for a, b, rate, t0 in clock.pieces if abs(rate / 20.0 - 1.0) > 0.01 and t0 + (b - a) / rate > start]
    warning = ("Part of the video after the song starts is sped up (timelapse): Flashback plays the song faster "
               "there. Use the song on the made video instead, or start it after the sped-up part.") if fast else ""
    return {"path": path, "tick": tick, "markers": markers, "warning": warning,
            "bpm": grid.bpm if grid else None}


def set_audio_track(edit_path: str, song_path: str | None, tick: int = 0) -> None:
    """Put the song on the edit's AUDIO track named ``mcsm music`` (replacing the app's earlier one;
    your own tracks are left alone). *song_path* None removes the app's track."""
    with open(edit_path, encoding="utf-8") as fh:
        state = json.load(fh)
    scenes = state.get("scenes") or []
    if not scenes:
        raise ValueError("the edit has no scenes")
    idx = int(state.get("sceneIndex") or 0)
    sc = scenes[idx if 0 <= idx < len(scenes) else 0]
    tracks = [t for t in (sc.get("keyframeTracks") or []) if t.get("customName") != TRACK_NAME]
    if not song_path and len(tracks) == len(sc.get("keyframeTracks") or []):
        return                                              # nothing of ours to remove: the file stays as it is
    if song_path:
        tracks.append({"keyframeType": "AUDIO",
                       "keyframesByTick": {str(int(tick)): {"path": os.path.abspath(song_path), "type": "audio",
                                                            "interpolation_type": "LINEAR"}},
                       "enabled": True, "customName": TRACK_NAME, "customColour": 0})
    sc["keyframeTracks"] = tracks
    tmp = edit_path + ".mcsm-part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, edit_path)


def audio_args(music: dict, first_input: int, has_audio: bool) -> tuple[list[str], list[str], str]:
    """ffmpeg pieces to add the song to a made video: extra inputs, filter chains and the label of
    the sound to map. The song starts at ``start`` seconds of the video, ``offset`` seconds into
    the song, at ``volume`` (1 = as is), mixed with the video's own sound (or alone)."""
    start = max(0.0, float(music.get("start") or 0.0))
    offset = max(0.0, float(music.get("offset") or 0.0))
    vol = max(0.0, float(music.get("volume", 1.0)))
    ms = int(round(start * 1000))
    k = first_input
    chain = (f"[{k}:a]atrim=start={offset:.3f},asetpts=PTS-STARTPTS,adelay={ms}|{ms},volume={vol:.3f}[song]")
    chains = [chain]
    if has_audio:
        chains.append("[0:a][song]amix=inputs=2:duration=first:normalize=0[mix]")
        return ["-i", os.path.abspath(music["file"])], chains, "mix"
    return ["-i", os.path.abspath(music["file"])], chains, "song"
