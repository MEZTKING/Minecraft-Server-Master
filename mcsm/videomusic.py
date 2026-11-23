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


# How the song sounds (the editor's music panel): applied in the editor's preview, baked into the copy Flashback
# plays (Flashback has no volume or effects for a clip) and used when the song is mixed into a made video.
SOUND_DEFAULTS = {"volume": 1.0, "fade_in": 0.0, "fade_out": 0.0, "until": None, "bass": 0.0, "mid": 0.0,
                  "treble": 0.0, "even": False}


def sound_of(music: dict) -> dict:
    out = dict(SOUND_DEFAULTS)
    for k, v in SOUND_DEFAULTS.items():
        x = music.get(k, v)
        if k == "until":
            out[k] = None if x in (None, "") else float(x)
        elif k == "even":
            out[k] = bool(x)
        else:
            try:
                out[k] = float(x)
            except (TypeError, ValueError):
                out[k] = v
    out["volume"] = max(0.0, min(3.0, out["volume"]))
    for k in ("bass", "mid", "treble"):
        out[k] = max(-15.0, min(15.0, out[k]))
    return out


def play_length(music: dict) -> float | None:
    """Seconds the song plays (it stops at ``until``, a second of the video), or None: to its end."""
    snd = sound_of(music)
    if snd["until"] is None:
        return None
    return max(0.1, snd["until"] - max(0.0, float(music.get("start") or 0.0)))


def song_filters(music: dict) -> list[str]:
    """ffmpeg audio filters for the song from where it starts playing (time 0): equaliser (bass ~110 Hz,
    mid ~1 kHz, treble ~4 kHz, in dB), even loudness, volume, fade in, and the stop with its fade out."""
    snd, out = sound_of(music), []
    if snd["bass"]:
        out.append(f"bass=g={snd['bass']:g}:f=110")
    if snd["mid"]:
        out.append(f"equalizer=f=1000:t=q:w=1:g={snd['mid']:g}")
    if snd["treble"]:
        out.append(f"treble=g={snd['treble']:g}:f=4000")
    if snd["even"]:
        out.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    if abs(snd["volume"] - 1.0) > 1e-6:
        out.append(f"volume={snd['volume']:.3f}")
    if snd["fade_in"] > 0:
        out.append(f"afade=t=in:st=0:d={snd['fade_in']:.3f}")
    length = play_length(music)
    if length is not None:
        out.append(f"atrim=end={length:.3f}")
        if snd["fade_out"] > 0:
            out.append(f"afade=t=out:st={max(0.0, length - snd['fade_out']):.3f}:d={snd['fade_out']:.3f}")
    return out


def song_copy(song: str, offset: float, music_dir: str, music: dict | None = None) -> str:
    """The file Flashback plays: a copy of *song* in *music_dir* (so moving or deleting the
    original doesn't break the edit), cut to start *offset* seconds into the song, with the sound
    settings of *music* baked in. Converted to .ogg with ffmpeg when Flashback can't play the format
    or it has to be cut or changed."""
    from .videotext import find_tool
    if not os.path.isfile(song):
        raise ValueError(f"the song isn't there any more: {song}")
    ext = os.path.splitext(song)[1].lower()
    st = os.stat(song)
    filters = song_filters(music or {})
    key = hashlib.sha1(f"{os.path.abspath(song)}|{st.st_size}|{st.st_mtime}|{offset:.3f}|{filters}".encode()).hexdigest()[:16]
    os.makedirs(music_dir, exist_ok=True)
    if offset <= 0 and ext in FLASHBACK_AUDIO and not filters:
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
                            *(["-af", ",".join(filters)] if filters else []),
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
    path = song_copy(music["file"], offset, music_dir, music)
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
    from . import editbackup
    editbackup.snapshot(edit_path, "before the app set the song")
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
    the song, with its sound settings (:func:`song_filters`), mixed with the video's own sound at
    ``game_volume`` (or alone)."""
    start = max(0.0, float(music.get("start") or 0.0))
    offset = max(0.0, float(music.get("offset") or 0.0))
    ms = int(round(start * 1000))
    k = first_input
    sound = "".join("," + f for f in song_filters(music))
    chain = f"[{k}:a]atrim=start={offset:.3f},asetpts=PTS-STARTPTS{sound},adelay={ms}|{ms}[song]"
    chains = [chain]
    if has_audio:
        game = max(0.0, min(2.0, float(music.get("game_volume", 1.0) if music.get("game_volume") is not None else 1.0)))
        chains.append(f"[0:a]volume={game:.3f}[game]")
        chains.append("[game][song]amix=inputs=2:duration=first:normalize=0[mix]")
        return ["-i", os.path.abspath(music["file"])], chains, "mix"
    return ["-i", os.path.abspath(music["file"])], chains, "song"
