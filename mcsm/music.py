"""Syncing music to the video: beat grids, beat detection and snapping.

Three things are offered:

1. **Beat grid** -- given a tempo (BPM), the time of the first beat inside
   the song, and where the song starts on the video timeline, generate a
   marker for every beat and every bar. Drop these next to the game-event
   markers and you can see at a glance which cuts land on the beat.

2. **Beat detection** -- if you do not know the BPM, :func:`detect_beats`
   estimates tempo and first-beat offset from a WAV file (pure Python, no
   dependencies). Other formats (MP3, OGG, FLAC, M4A...) are converted with
   ``ffmpeg`` if it is installed.

   Algorithm (classic "onset envelope + comb filter"):
     a. mix to mono, compute log-energy in ~23 ms frames;
     b. onset strength = positive change in energy between frames;
     c. coarse tempo = autocorrelation peak of the onset envelope between
        60 and 200 BPM, weighted towards ~120 BPM to avoid half/double
        tempo mistakes;
     d. fine tempo + phase = the (bpm, offset) pair whose beat grid
        collects the most onset strength, searched +-2 BPM around (c).

   It works well on music with a steady beat (most edit music). For
   live/rubato music, give ``--bpm`` and ``--beat-offset`` yourself.

3. **Snapping** -- move each event marker to the nearest beat (or bar) if
   it is within a tolerance, so cuts placed on those markers hit the music.
   The original position is kept in ``snapped_from``.

4. **Alignment** -- "I want 1:02.5 into the song (the drop) to land on
   marker #12": :func:`music_start_for_alignment` tells you where to place
   the song on the timeline.

Timeline maths: a beat at song time ``s`` sits at video time
``music_start + s``. ``music_start`` may be negative (song starts before
the video does).
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from array import array
from dataclasses import dataclass

from .sync import Marker


@dataclass
class BeatGrid:
    bpm: float
    first_beat: float = 0.0        # seconds into the song of the first (down)beat
    beats_per_bar: int = 4
    music_start: float = 0.0       # where the song starts on the video timeline (seconds)
    song_length: float | None = None
    confidence: float | None = None  # only set by detection (0..1, higher = steadier)

    @property
    def period(self) -> float:
        return 60.0 / self.bpm

    def beat_times(self, video_length: float | None = None) -> list[tuple[float, int]]:
        """Return ``[(video_seconds, beat_index)]``; index 0 is the first downbeat.

        Beats before the first downbeat (pickup) get negative indices.
        """
        if self.bpm <= 0:
            raise ValueError("bpm must be positive")
        song_end = self.song_length if self.song_length is not None else math.inf
        out = []
        k = -int(self.first_beat // self.period)
        while True:
            song_t = self.first_beat + k * self.period
            if song_t > song_end + 1e-9:
                break
            video_t = self.music_start + song_t
            if video_length is not None and video_t > video_length + 1e-9:
                break
            if song_t >= -1e-9 and video_t >= -1e-9:
                out.append((video_t, k))
            k += 1
            if self.song_length is None and video_length is None and len(out) > 100_000:
                break
        return out

    def markers(self, video_length: float | None = None, bars_only: bool = False) -> list[Marker]:
        out = []
        for t, k in self.beat_times(video_length):
            is_bar = k % self.beats_per_bar == 0
            if bars_only and not is_bar:
                continue
            bar_no = k // self.beats_per_bar + 1
            beat_in_bar = k % self.beats_per_bar + 1
            out.append(Marker(seconds=t, type="bar" if is_bar else "beat",
                              label=f"Bar {bar_no}" if is_bar else f"Beat {bar_no}.{beat_in_bar}",
                              extra={"beat_index": k}))
        return out


def snap_markers(markers: list[Marker], grid: BeatGrid, to: str = "beat",
                 tolerance: float | None = None, video_length: float | None = None) -> list[Marker]:
    """Move markers to the nearest beat/bar within *tolerance* seconds.

    Default tolerance is half a beat (or half a bar), i.e. always snap.
    Returns new Marker objects; the input list is untouched.
    """
    beats = [t for t, k in grid.beat_times(video_length) if to == "beat" or k % grid.beats_per_bar == 0]
    if not beats:
        return list(markers)
    step = grid.period * (1 if to == "beat" else grid.beats_per_bar)
    tol = tolerance if tolerance is not None else step / 2
    import bisect
    out = []
    for mk in markers:
        i = bisect.bisect_left(beats, mk.seconds)
        cands = [beats[j] for j in (i - 1, i) if 0 <= j < len(beats)]
        best = min(cands, key=lambda b: abs(b - mk.seconds))
        if abs(best - mk.seconds) <= tol + 1e-9:
            new = Marker(**{**mk.__dict__})
            new.snapped_from, new.seconds = mk.seconds, best
            out.append(new)
        else:
            out.append(mk)
    return out


def music_start_for_alignment(song_time: float, video_time: float) -> float:
    """Where the song must start so *song_time* plays at *video_time*."""
    return video_time - song_time


# ---------------------------------------------------------------------------
# Audio loading
# ---------------------------------------------------------------------------

TARGET_RATE = 22050


def _ffmpeg_to_wav(path: str) -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError(
            f"{os.path.basename(path)} is not a WAV file and ffmpeg is not installed. "
            "Either install ffmpeg (https://ffmpeg.org), convert the song to WAV, "
            "or pass --bpm (and --beat-offset) instead of --music.")
    fd, out = tempfile.mkstemp(suffix=".wav", prefix="mcsm-")
    os.close(fd)
    # CREATE_NO_WINDOW stops a console window flashing up when run from the desktop app.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run([exe, "-v", "error", "-y", "-i", path, "-ac", "1", "-ar", str(TARGET_RATE),
                    "-sample_fmt", "s16", out], check=True, creationflags=flags)
    return out


def load_mono(path: str) -> tuple[array, int]:
    """Load audio as mono float samples. Returns ``(samples, sample_rate)``."""
    tmp = None
    try:
        try:
            wf = wave.open(path, "rb")
        except (wave.Error, EOFError):
            tmp = _ffmpeg_to_wav(path)
            wf = wave.open(tmp, "rb")
        with wf:
            ch, width, rate, n = wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()
            raw = wf.readframes(n)
    finally:
        if tmp:
            os.unlink(tmp)

    if width == 2:
        ints = array("h")
        ints.frombytes(raw)
        if struct.pack("=h", 1) != struct.pack("<h", 1):
            ints.byteswap()
        scale = 32768.0
    elif width == 1:
        ints = array("h", (b - 128 for b in raw))
        scale = 128.0
    elif width in (3, 4):
        if width == 3:
            ints = array("i", (int.from_bytes(raw[i:i + 3], "little", signed=True) for i in range(0, len(raw), 3)))
            scale = 8388608.0
        else:
            ints = array("i")
            ints.frombytes(raw)
            scale = 2147483648.0
    else:
        raise ValueError(f"unsupported WAV sample width {width}")

    # Mix down + decimate to ~TARGET_RATE (plain averaging is fine for energy analysis).
    step = max(1, rate // TARGET_RATE)
    frame_stride = ch * step
    mono = array("f")
    for i in range(0, len(ints) - ch + 1, frame_stride):
        mono.append(sum(ints[i:i + ch]) / (ch * scale))
    return mono, rate // step


def onset_envelope(samples: array, rate: int, hop: int = 512) -> tuple[list[float], float]:
    """Return ``(onset_strength_per_frame, frames_per_second)``."""
    energies = []
    for i in range(0, len(samples) - hop + 1, hop):
        chunk = samples[i:i + hop]
        e = sum(x * x for x in chunk) / hop
        energies.append(math.log10(e + 1e-10))
    env = [0.0] + [max(0.0, energies[i] - energies[i - 1]) for i in range(1, len(energies))]
    # Remove slow trends: subtract a local mean (~0.5 s window).
    w = max(1, int(rate / hop / 2))
    prefix = [0.0]
    for v in env:
        prefix.append(prefix[-1] + v)
    out = []
    for i, v in enumerate(env):
        a, b = max(0, i - w), min(len(env), i + w + 1)
        out.append(max(0.0, v - (prefix[b] - prefix[a]) / (b - a)))
    return out, rate / hop


def _grid_score(env: list[float], fps: float, bpm: float, phase: float) -> float:
    period = 60.0 * fps / bpm
    total, pos, n = 0.0, phase, len(env)
    count = 0
    while pos < n - 1:
        i = int(pos)
        frac = pos - i
        total += env[i] * (1 - frac) + env[i + 1] * frac
        pos += period
        count += 1
    return total / max(count, 1)


def detect_beats(path: str, min_bpm: float = 60, max_bpm: float = 200) -> BeatGrid:
    """Estimate the tempo and first-beat offset of an audio file."""
    samples, rate = load_mono(path)
    if len(samples) < rate * 3:
        raise ValueError("audio is too short for beat detection (need at least 3 seconds)")
    env, fps = onset_envelope(samples, rate)
    n = len(env)
    mean = sum(env) / n
    centred = [v - mean for v in env]

    # (c) coarse tempo via weighted autocorrelation
    best_lag, best_val = None, -math.inf
    lo, hi = int(60 * fps / max_bpm), int(math.ceil(60 * fps / min_bpm))
    for lag in range(max(1, lo), min(hi, n - 1) + 1):
        ac = sum(centred[i] * centred[i + lag] for i in range(n - lag)) / (n - lag)
        bpm = 60 * fps / lag
        weight = math.exp(-0.5 * (math.log2(bpm / 120.0) / 0.9) ** 2)  # prefer ~120 BPM
        if ac * weight > best_val:
            best_val, best_lag = ac * weight, lag
    coarse = 60 * fps / best_lag

    # (d) fine tempo + phase
    best = (-math.inf, coarse, 0.0)
    bpm = max(min_bpm, coarse - 2.0)
    while bpm <= min(max_bpm, coarse + 2.0):
        period = 60 * fps / bpm
        steps = max(8, int(period * 2))
        for s in range(steps):
            phase = period * s / steps
            sc = _grid_score(env, fps, bpm, phase)
            if sc > best[0]:
                best = (sc, bpm, phase)
        bpm += 0.05
    score, bpm, phase = best
    # Confidence: how much better the chosen phase is than the average phase
    # at the same tempo. A steady beat gives a sharp peak (close to 1); music
    # without a clear pulse scores near 0.
    period = 60 * fps / bpm
    phases = [_grid_score(env, fps, bpm, period * s / 16) for s in range(16)]
    avg = sum(phases) / len(phases)
    confidence = 0.0 if score <= 0 else max(0.0, 1.0 - avg / score)
    first = phase / fps
    return BeatGrid(bpm=round(bpm, 2), first_beat=round(first, 3),
                    song_length=len(samples) / rate, confidence=round(confidence, 2))
