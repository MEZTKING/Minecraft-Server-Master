"""Join the videos exported from a timelapse's Flashback copies into one, optionally with music.

A build that spans several recording files becomes several Flashback copies ("... (timelapse 2 of 9)"),
each exported on its own. :func:`join` puts the exported videos one after another with ffmpeg (found on
this PC, never downloaded):

- order: by the "(timelapse n of N)" in the file names when every file has one, otherwise in the order
  given (the app's file picker sorts them by name, so oldest-first names work too);
- videos with the same size, frame rate and codec are joined without re-encoding (fast, no quality
  loss); otherwise they are re-encoded to the first video's size and frame rate;
- music (optional) replaces the sound: it starts at *music_start* seconds into the song, loops if the
  song is shorter than the video, and fades out over the last 2 seconds.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile

from .videotext import find_tool, video_duration

FADE_S = 2.0
_PART = re.compile(r"\(timelapse (\d+) of (\d+)\)", re.IGNORECASE)


def ordered(paths: list[str]) -> list[str]:
    """The videos in timelapse order (see the module docstring)."""
    nums = [_PART.search(os.path.basename(p)) for p in paths]
    if paths and all(nums):
        return [p for _n, p in sorted(zip((int(n.group(1)) for n in nums), paths), key=lambda q: q[0])]
    return list(paths)


def _streams(probe: str, path: str) -> dict:
    out = subprocess.run([probe, "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate",
                          "-of", "json", path], capture_output=True, text=True, timeout=60, check=True).stdout
    info = {"audio": False}
    for st in json.loads(out).get("streams", []):
        if st.get("codec_type") == "video" and "video" not in info:
            info["video"] = (st.get("codec_name"), st.get("width"), st.get("height"), st.get("r_frame_rate"))
        elif st.get("codec_type") == "audio":
            info["audio"] = True
    return info


def plan(paths: list[str]) -> dict:
    """What :func:`join` would do: ``{"order", "copy", "seconds", "audio"}`` (needs ffprobe)."""
    probe = find_tool("ffprobe")
    if not probe:
        raise ValueError("ffprobe (part of ffmpeg) was not found on this PC: install ffmpeg first")
    if not paths:
        raise ValueError("no videos chosen")
    order = ordered(paths)
    infos = [_streams(probe, p) for p in order]
    if any("video" not in i for i in infos):
        bad = [os.path.basename(p) for p, i in zip(order, infos) if "video" not in i]
        raise ValueError(f"not a video: {', '.join(bad)}")
    durations = [video_duration(p) or 0.0 for p in order]
    return {"order": order, "copy": len({i["video"] for i in infos}) == 1, "seconds": sum(durations),
            "audio": all(i["audio"] for i in infos), "size": infos[0]["video"][1:3], "fps": infos[0]["video"][3]}


def command(p: dict, out: str, music: str | None = None, music_start: float = 0.0, list_file: str = "") -> list[str]:
    """The ffmpeg command for plan *p* (the concat list is written to *list_file* by :func:`join`)."""
    ff = find_tool("ffmpeg") or "ffmpeg"
    total = p["seconds"]
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error"]
    if p["copy"]:
        cmd += ["-f", "concat", "-safe", "0", "-i", list_file]
    else:
        for v in p["order"]:
            cmd += ["-i", v]
    n = 1 if p["copy"] else len(p["order"])
    if music:
        cmd += ["-stream_loop", "-1", "-ss", f"{max(0.0, music_start):g}", "-i", music]
    if p["copy"]:
        cmd += ["-map", "0:v"]
        vcodec = ["-c:v", "copy"]
    else:
        w, h = p["size"]
        fps = p["fps"] or "30"
        chains = "".join(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                         f"fps={fps},setsar=1[v{i}];" for i in range(n))
        cmd += ["-filter_complex", chains + "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]", "-map", "[v]"]
        vcodec = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
    if music:
        fade = max(0.0, total - FADE_S)
        cmd += ["-map", f"{n}:a", "-af", f"afade=t=out:st={fade:.3f}:d={FADE_S:g}", "-c:a", "aac", "-b:a", "192k"]
    elif p["copy"] and p["audio"]:
        cmd += ["-map", "0:a", "-c:a", "copy"]
    else:
        cmd += ["-an"]
    if total:
        cmd += ["-t", f"{total:.3f}"]
    return cmd + vcodec + ["-movflags", "+faststart", out]


def join(paths: list[str], out: str, music: str | None = None, music_start: float = 0.0, say=print) -> dict:
    """Join *paths* into *out*; returns the plan."""
    p = plan(paths)
    if not find_tool("ffmpeg"):
        raise ValueError("ffmpeg was not found on this PC: install ffmpeg first")
    if os.path.abspath(out) in {os.path.abspath(v) for v in p["order"]}:
        raise ValueError("the joined video would overwrite one of the parts: choose another name")
    say(f"Joining {len(p['order'])} videos ({'without re-encoding' if p['copy'] else 're-encoding: they differ in size or frame rate'})"
        + (f" with music from {os.path.basename(music)}" if music else "") + "…")
    for i, v in enumerate(p["order"], 1):
        say(f"  {i}. {os.path.basename(v)}")
    fd, lst = tempfile.mkstemp(suffix=".txt", prefix="mcsm-join-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for v in p["order"]:
                fh.write("file '" + os.path.abspath(v).replace("\\", "/").replace("'", "'\\''") + "'\n")
        res = subprocess.run(command(p, out, music, music_start, lst), capture_output=True, text=True)
        if res.returncode != 0:
            raise ValueError("ffmpeg failed: " + (res.stderr.strip().splitlines() or ["unknown error"])[-1])
    finally:
        try:
            os.remove(lst)
        except OSError:
            pass
    say(f"Done: {out}")
    return p
