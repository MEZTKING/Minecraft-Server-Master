"""Writing markers in formats video/audio editors can import.

=================  =====================================================
Format             Import it in...
=================  =====================================================
``.srt``           Any editor as a subtitle track (Resolve, Premiere,
                   CapCut, Shotcut, Vegas, Kdenlive). Shows chat/events
                   as captions at the right moment.
``.edl``           DaVinci Resolve: right-click timeline in Media Pool ->
                   *Timelines > Import > Timeline Markers from EDL...*
                   Coloured markers by event type.
``.csv``           Spreadsheets, scripts, anything.
``.audacity.txt``  Audacity: *File > Import > Labels...* (also useful
                   for lining up music).
``.chapters.txt``  Paste into a YouTube description.
``.json``          Your own scripts.
=================  =====================================================
"""

from __future__ import annotations

import csv
import json
import os

from ..sync import Marker, marker_rows


def timecode(seconds: float, fps: float, start_hour: int = 0) -> str:
    """Non-drop-frame SMPTE timecode ``HH:MM:SS:FF``.

    Fractional rates (23.976, 29.97, 59.94) are treated as their rounded
    integer rate, which is how non-drop-frame timecode works.
    """
    rate = max(1, int(round(fps)))
    frames = int(round(max(0.0, seconds) * fps)) + start_hour * 3600 * rate
    ff = frames % rate
    total_s = frames // rate
    return f"{total_s // 3600:02d}:{(total_s // 60) % 60:02d}:{total_s % 60:02d}:{ff:02d}"


def _srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(markers: list[Marker], path: str, min_duration: float = 0.5) -> None:
    """One subtitle per marker. Overlapping subtitles are shortened so the
    next one starts cleanly (editors such as Resolve dislike overlaps)."""
    items = sorted((mk for mk in markers if mk.type not in ("beat", "bar")), key=lambda mk: mk.seconds)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for i, mk in enumerate(items):
            start = mk.seconds
            end = start + (mk.duration or 3.0)
            if i + 1 < len(items):
                end = min(end, max(start + min_duration, items[i + 1].seconds - 0.001))
            fh.write(f"{i + 1}\n{_srt_time(start)} --> {_srt_time(end)}\n{mk.label}\n\n")


def write_edl(markers: list[Marker], path: str, fps: float = 60.0, title: str = "mcsm markers",
              start_hour: int = 1) -> None:
    """DaVinci Resolve marker EDL.

    *start_hour*: Resolve timelines start at 01:00:00:00 by default; set 0
    if your timeline starts at 00:00:00:00.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"TITLE: {title}\nFCM: NON-DROP FRAME\n\n")
        for i, mk in enumerate(sorted(markers, key=lambda mk: mk.seconds), start=1):
            tc_in = timecode(mk.seconds, fps, start_hour)
            tc_out = timecode(mk.seconds + 1 / fps, fps, start_hour)
            label = mk.label.replace("|", "/").replace("\n", " ")[:200]
            fh.write(f"{i:03d}  001      V     C        {tc_in} {tc_out} {tc_in} {tc_out}  \n")
            fh.write(f" |C:ResolveColor{mk.colour} |M:{label} |D:1\n\n")


def write_csv(markers: list[Marker], path: str) -> None:
    rows = marker_rows(markers)
    fields = list(rows[0].keys()) if rows else ["index", "video_seconds", "video_time", "type", "player",
                                                "label", "real_time_utc", "snapped_from_seconds", "colour"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:  # BOM so Excel reads UTF-8
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def write_audacity(markers: list[Marker], path: str) -> None:
    """Audacity label track: ``start<TAB>end<TAB>label`` per line."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for mk in sorted(markers, key=lambda mk: mk.seconds):
            fh.write(f"{mk.seconds:.6f}\t{mk.seconds:.6f}\t{mk.label}\n")


def write_chapters(markers: list[Marker], path: str, min_gap: float = 10.0,
                   intro_label: str = "Start") -> int:
    """YouTube chapters (first chapter must be 0:00; chapters >= 10 s apart).

    Only "big" events become chapters: advancements, deaths, joins and replay
    markers. Returns the number of chapters written.
    """
    from ..timeutil import format_hms
    keep = {"advancement", "death", "join", "replay_marker"}
    chapters = [(0.0, intro_label)]
    for mk in sorted(markers, key=lambda mk: mk.seconds):
        if mk.type not in keep:
            continue
        if mk.seconds - chapters[-1][0] >= min_gap:
            chapters.append((mk.seconds, mk.label))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for t, label in chapters:
            fh.write(f"{format_hms(t)} {label}\n")
        if len(chapters) < 3:
            fh.write("\n# Note: YouTube needs at least 3 chapters, each at least 10 seconds long.\n")
    return len(chapters)


def write_json(markers: list[Marker], path: str, meta: dict | None = None) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"meta": meta or {}, "markers": marker_rows(markers)}, fh, indent=2, ensure_ascii=False)


ALL_FORMATS = ("srt", "edl", "csv", "audacity", "chapters", "json")


def write_all(markers: list[Marker], out_dir: str, basename: str, fps: float, formats=ALL_FORMATS,
              edl_start_hour: int = 1, meta: dict | None = None,
              subtitle_markers: list[Marker] | None = None) -> list[str]:
    """Write every requested format; returns the paths written.

    *subtitle_markers*: markers to use for SRT/chapters (normally the event
    markers without beat markers). Defaults to *markers*.
    """
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, basename)
    subs = subtitle_markers if subtitle_markers is not None else markers
    written = []
    if "srt" in formats:
        write_srt(subs, base + ".srt"); written.append(base + ".srt")
    if "edl" in formats:
        write_edl(markers, base + ".edl", fps, title=basename, start_hour=edl_start_hour); written.append(base + ".edl")
    if "csv" in formats:
        write_csv(markers, base + ".csv"); written.append(base + ".csv")
    if "audacity" in formats:
        write_audacity(markers, base + ".audacity.txt"); written.append(base + ".audacity.txt")
    if "chapters" in formats:
        write_chapters(subs, base + ".chapters.txt"); written.append(base + ".chapters.txt")
    if "json" in formats:
        write_json(markers, base + ".json", meta); written.append(base + ".json")
    return written
