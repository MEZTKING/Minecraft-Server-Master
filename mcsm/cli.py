"""Command-line interface: ``mcsm <command> ...`` (or ``python -m mcsm``).

Commands
--------
summary   quick overview in the terminal
report    build the HTML statistics dashboard
export    write JSON / SQLite / CSV files
events    search events from the terminal
replays   list replay recordings found on the server
changes   Minecraft version, mods and datapacks changed between backups
video-text timed in-game texts for a finished Flashback video
world-text real 3D text in the world of a copy of a Flashback recording
sync      make video markers/subtitles for a replay (+ music beat sync)
flashback copy a Flashback replay with server events as markers in its timeline
timelapse activity-aware timelapse of an area, set up in Flashback
live      live 3D map of players (RCON)
history   3D time machine over all backups and recordings
library   keep all backups and recordings in one library folder
music     detect the tempo (BPM) of a song

Run ``mcsm <command> --help`` for the options of each command, and see
``README.md`` / ``docs/`` for worked examples.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import os
import sys
import tarfile
import json
import shutil
import zipfile
from datetime import date, datetime, timedelta  # noqa: F401

from . import __version__, models as m, version_string
from .analyzer import analyze
from .players import play_time_ticks
from .source import open_server
from .sync import DEFAULT_SYNC_TYPES
from .timeutil import UTC, format_hms, iso, parse_duration, parse_tz


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

# Backups opened during a command stay extracted until main() finishes, so later steps
# (copying a replay, building terrain) can still read them.
_OPEN = ExitStack()


def _load(args) -> m.ServerData:
    """Analyse one server/backup, or several merged into one (see mcsm.archive)."""
    tz = parse_tz(args.tz) if getattr(args, "tz", None) else None
    latest = date.fromisoformat(args.latest_log_date) if getattr(args, "latest_log_date", None) else None
    extra = []
    rp = getattr(args, "replay", None)
    if rp and os.path.isfile(rp):
        extra.append(rp)
    from .library import expand_sources
    servers, recordings, world = expand_sources(args.server if isinstance(args.server, list) else [args.server])
    if len(servers) == 1 and not recordings:
        root = _OPEN.enter_context(open_server(servers[0]))
        return analyze(root, tz=tz, latest_log_date=latest, extra_replays=extra)
    from .archive import analyze_many
    data = analyze_many(servers, _OPEN, tz=tz, latest_log_date=latest, extra_replays=extra,
                        progress=lambda msg: print(msg, file=sys.stderr), recordings=recordings)
    data.library_world = world
    return data


def _print_warnings(data: m.ServerData) -> None:
    for w in data.warnings:
        print(f"  ! {w}", file=sys.stderr)


def _parse_when(text: str, tz_spec: str | None) -> datetime:
    """Parse a user-given date/time. Naive values use --tz, else this computer's zone."""
    text = text.strip().replace("T", " ")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise SystemExit(f"cannot parse date/time {text!r}; use e.g. '2024-05-01 20:15:00'")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=parse_tz(tz_spec)) if tz_spec else dt.astimezone()
    return dt.astimezone(UTC)


def _local(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S") if dt else "-"


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("server", nargs="+", help="server folder or .zip backup; give several to merge them "
                                            "into one history (any versions, oldest to newest)")
    p.add_argument("--tz", help="server time zone for log times, e.g. Europe/London or +01:00 "
                                "(default: learned from advancement files)")
    p.add_argument("--latest-log-date", metavar="YYYY-MM-DD",
                   help="date of the last line in logs/latest.log (default: file modification date)")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_summary(args) -> int:
    return run_summary(_load(args), args)


def run_summary(data: m.ServerData, args=None) -> int:
    w = data.world
    print(f"Server root : {data.root}")
    print(f"World       : {w.get('level_name') or w.get('folder')}  ({w.get('version_name') or '?'})")
    print(f"Time zone   : {data.timezone_info}")
    if data.events:
        print(f"Logs cover  : {_local(data.events[0].time)} -> {_local(data.events[-1].time)}  "
              f"({len(data.log_files)} files, {len(data.events)} events)")
    counts: dict[str, int] = {}
    for e in data.events:
        counts[e.type] = counts.get(e.type, 0) + 1
    if counts:
        print("Events      : " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
    print(f"Replays     : {len(data.replays)}")
    print(f"Mods/plugins: {len(data.mods)}")
    if getattr(data, "changes", None):
        print(f"Changes     : {len(data.changes)} version/mod/datapack changes between backups (mcsm changes)")
    print()
    print(f"{'Player':<20}{'Play time':>12}{'Sessions':>10}{'Deaths':>8}{'Chat':>8}  Last seen")
    for p in sorted(data.players.values(), key=lambda p: -play_time_ticks(p.stats)):
        deaths = p.stats.get("custom", {}).get("deaths", p.deaths_logged)
        print(f"{p.name[:19]:<20}{format_hms(play_time_ticks(p.stats) / 20):>12}{p.session_count:>10}"
              f"{deaths:>8}{p.chat_messages:>8}  {_local(p.last_seen)}")
    _print_warnings(data)
    return 0


def cmd_report(args) -> int:
    return run_report(_load(args), args)


def run_report(data: m.ServerData, args) -> int:
    from .exporters.report import write_report
    out = args.output or "server-report.html"
    write_report(data, out, args.title)
    _print_warnings(data)
    print(f"Report written to {os.path.abspath(out)} -- open it in your browser.")
    return 0


def cmd_export(args) -> int:
    from .exporters import data as dx
    if not (args.json or args.sqlite or args.csv):
        args.sqlite = "server.sqlite"
    return run_export(_load(args), args)


def run_export(data: m.ServerData, args) -> int:
    from .exporters import data as dx
    if args.json:
        dx.write_json(data, args.json); print(f"JSON   -> {args.json}")
    if args.sqlite:
        dx.write_sqlite(data, args.sqlite); print(f"SQLite -> {args.sqlite}")
    if args.csv:
        for p in dx.write_csv_dir(data, args.csv):
            print(f"CSV    -> {p}")
    _print_warnings(data)
    return 0


def cmd_events(args) -> int:
    data = _load(args)
    since = _parse_when(args.since, args.tz) if args.since else None
    until = _parse_when(args.until, args.tz) if args.until else None
    types = set(args.type or [])
    players = {p.lower() for p in (args.player or [])}
    grep = (args.grep or "").lower()
    rows = [e for e in data.events
            if (not types or e.type in types)
            and (not players or (e.player or "").lower() in players)
            and (since is None or e.time >= since) and (until is None or e.time <= until)
            and (not grep or grep in e.text.lower() or grep in (e.player or "").lower())]
    if args.limit:
        rows = rows[-args.limit:]
    for e in rows:
        print(f"{_local(e.time)}  {e.type:<13} {(e.player or ''):<16} {e.text}")
    print(f"({len(rows)} events)", file=sys.stderr)
    return 0


def cmd_changes(args) -> int:
    data = _load(args)
    return run_changes(data, args)


def run_changes(data: m.ServerData, args=None) -> int:
    """Minecraft version, mods, plugins and datapacks changed between backups."""
    kinds = set(getattr(args, "kind", None) or [])
    if len(data.backups) < 2:
        print("Give two or more backups (or a library world) to see what changed between them.")
        return 0
    for b in data.backups:
        print(f"{_local(b.get('time'))}  {b.get('label')}: Minecraft {b.get('version') or '?'}, "
              f"{len(b.get('mod_list') or [])} mods/plugins, {len(b.get('datapacks') or [])} datapacks")
    print()
    last = None
    sign = {"added": "+", "removed": "-", "updated": "^", "changed": "!"}
    for c in data.changes:
        if kinds and c["kind"] not in kinds:
            continue
        span = (c.get("after"), c.get("time"))
        if span != last:
            print(f"Between {_local(span[0])} and {_local(span[1])}:")
            last = span
        v = f"{c['from']} -> {c['to']}" if c["action"] == "updated" else (c["to"] or c["from"])
        print(f"  {sign.get(c['action'], '*')} {c['kind']:<9} {c['name']}  {v}".rstrip())
    if not data.changes:
        print("No changes found between the backups.")
    return 0


def cmd_replays(args) -> int:
    data = _load(args)
    if not data.replays:
        print("No replays found (.mcpr from ReplayMod/ServerReplay, or Flashback .zip; ServerReplay stores them in recordings/).")
        return 0
    for i, r in enumerate(data.replays, start=1):
        inside = [e for e in data.events if r.start and r.start <= e.time <= r.end]
        who = r.recorded_player or ", ".join(r.player_names[:4])
        fmt = {"replaymod": "ReplayMod", "flashback": "Flashback"}.get(r.format, r.format)
        flags = f"{fmt}" + (", UNFINISHED" if r.unfinished else "") + \
            ("" if r.start_source in ("metadata", "real-time-clock") else f", start from {r.start_source}")
        print(f"[{i}] {r.path}  ({flags})\n     start {_local(r.start)}  length {format_hms(r.duration_ms / 1000)}  "
              f"players: {who or '-'}  events during recording: {len(inside)}  markers: {len(r.markers)}")
    return 0


def _resolve_replay(data: m.ServerData, spec: str) -> m.Replay:
    if os.path.isfile(spec):
        target = os.path.abspath(spec)
        for r in data.replays:
            if os.path.abspath(os.path.join(data.root, r.path)) == target or r.path.endswith(os.path.basename(spec)):
                return r
    if spec.isdigit() and 1 <= int(spec) <= len(data.replays):
        return data.replays[int(spec) - 1]
    matches = [r for r in data.replays if spec.lower() in r.path.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"no replay matches {spec!r}; run 'mcsm replays SERVER' to list them")
    raise SystemExit(f"{spec!r} matches {len(matches)} replays; be more specific:\n  " +
                     "\n  ".join(r.path for r in matches))


def cmd_sync(args) -> int:
    return run_sync(_load(args), args)


def run_sync(data: m.ServerData, args) -> int:
    """The body of ``mcsm sync``, usable with already-loaded data (the GUI calls this)."""
    from . import music, sync
    from .exporters import markers as mx

    speed = args.speed
    render_start = parse_duration(args.render_start) if args.render_start else 0.0
    length = parse_duration(args.length) if args.length else None

    if args.replay:
        replay = _resolve_replay(data, args.replay)
        timeline = sync.timeline_from_replay(replay, render_start, length, speed)
        basename = args.name or os.path.splitext(os.path.basename(replay.path))[0]
        replay_path = replay.path
        print(f"Replay      : {replay.path}")
        print(f"Recorded    : {_local(replay.start)} -> {_local(replay.end)}  ({format_hms(replay.duration_ms / 1000)})")
    elif args.video_start:
        timeline = sync.VideoTimeline(_parse_when(args.video_start, args.tz) + timedelta(seconds=render_start),
                                      length, speed, label="manual")
        basename = args.name or "video"
        replay_path = None
    else:
        raise SystemExit("give --replay FILE/NUMBER or --video-start 'YYYY-MM-DD HH:MM:SS'")

    print(f"Video 0:00  = {_local(timeline.t0)} (your local time)"
          + (f", length {format_hms(timeline.length)}" if timeline.length else "")
          + (f", speed x{speed}" if speed != 1 else ""))

    types = args.types.split(",") if args.types else None
    if types == ["all"]:
        types = m.ALL_EVENT_TYPES
    players = args.players.split(",") if args.players else None
    events = sync.build_markers(data.events, timeline, types, players, args.subtitle_seconds, replay_path)
    print(f"Events      : {len(events)} inside the video")

    # --- music ------------------------------------------------------------
    grid = None
    anchor_idx = None  # marker used for --music-align; never snapped away
    if args.music or args.bpm:
        if args.bpm:
            grid = music.BeatGrid(bpm=args.bpm, first_beat=parse_duration(args.beat_offset) if args.beat_offset else 0.0)
        else:
            print(f"Analysing   : {args.music} ...")
            grid = music.detect_beats(args.music)
            if args.beat_offset:
                grid.first_beat = parse_duration(args.beat_offset)
            print(f"Detected    : {grid.bpm} BPM, first beat at {grid.first_beat:.3f}s "
                  f"(confidence {grid.confidence}) -- override with --bpm/--beat-offset if wrong")
        grid.beats_per_bar = args.beats_per_bar
        if args.music_start:
            grid.music_start = _signed_duration(args.music_start)
        if args.music_align:
            song_part, _, video_part = args.music_align.partition("=")
            if not video_part:
                raise SystemExit("--music-align needs SONG_TIME=VIDEO_TIME (e.g. 1:02.5=#12 or 1:02.5=3:40)")
            song_t = parse_duration(song_part)
            if video_part.startswith("#"):
                idx = int(video_part[1:])
                if not 1 <= idx <= len(events):
                    raise SystemExit(f"marker #{idx} does not exist (there are {len(events)})")
                video_t = events[idx - 1].seconds
                anchor_idx = idx - 1
                print(f"Aligning    : song {song_part} -> marker #{idx} '{events[idx - 1].label}' at {format_hms(video_t)}")
            else:
                video_t = parse_duration(video_part)
            grid.music_start = music.music_start_for_alignment(song_t, video_t)
        print(f"Music starts: {grid.music_start:+.3f}s on the video timeline")
        if args.snap != "none":
            before = events
            events = music.snap_markers(events, grid, args.snap,
                                        parse_duration(args.snap_tolerance) if args.snap_tolerance else None,
                                        timeline.length)
            if anchor_idx is not None:
                events[anchor_idx] = before[anchor_idx]
            moved = sum(1 for a, b in zip(before, events) if b.snapped_from is not None)
            print(f"Snapped     : {moved} markers to the nearest {args.snap}")

    beat_markers = []
    if grid and args.beat_markers != "none":
        beat_markers = grid.markers(timeline.length, bars_only=args.beat_markers == "bar")
    all_markers = sorted(events + beat_markers, key=lambda mk: mk.seconds)

    formats = args.formats.split(",") if args.formats else mx.ALL_FORMATS
    meta = {"timeline_start_utc": iso(timeline.t0), "length_seconds": timeline.length, "speed": speed,
            "replay": replay_path, "fps": args.fps,
            "music": None if not grid else {"bpm": grid.bpm, "first_beat": grid.first_beat,
                                             "music_start": grid.music_start, "beats_per_bar": grid.beats_per_bar}}
    written = mx.write_all(all_markers, args.out, basename, args.fps, formats, args.edl_start_hour, meta,
                           subtitle_markers=events)
    if beat_markers:
        written += mx.write_all(beat_markers, args.out, basename + ".beats", args.fps,
                                [f for f in formats if f in ("edl", "csv", "audacity")], args.edl_start_hour, meta)
    for p in written:
        print(f"  -> {p}")
    if not events:
        print("No events fell inside the video. Check --tz (log time zone) and --render-start.", file=sys.stderr)
    _print_warnings(data)
    return 0


def _signed_duration(text: str) -> float:
    text = text.strip()
    return -parse_duration(text[1:]) if text.startswith("-") else parse_duration(text.lstrip("+"))


def live_settings(root: str | None) -> dict:
    """RCON port/password, title and spawn from a downloaded server (all optional)."""
    from . import world as world_mod
    out: dict = {}
    if not root:
        return out
    props = world_mod.read_properties(os.path.join(root, "server.properties"), mask_secrets=False)
    if props.get("rcon.port", "").isdigit():
        out["port"] = int(props["rcon.port"])
    if props.get("rcon.password"):
        out["password"] = props["rcon.password"]
    if props.get("enable-rcon", "").lower() == "false":
        out["rcon_disabled"] = True
    out["world"] = world_mod.world_dir(root, props)
    level = world_mod.read_level_dat(out["world"])
    out["title"] = level.get("level_name") or props.get("motd") or "Minecraft server"
    sp = level.get("spawn")
    if sp and None not in sp:
        out["spawn"] = tuple(float(v) for v in sp)
    return out


def start_live(args, root: str | None, password: str | None = None):
    """Create and start a LiveMap for CLI/GUI. Returns ``(live, url)``."""
    from .live import DemoSource, LiveMap, RconSource
    from .rcon import RconClient
    cfg = live_settings(root)
    step = None if args.terrain_detail == "auto" else int(args.terrain_detail)
    record = args.record
    if args.demo:
        live = LiveMap(None, root, cfg.get("world"), args.interval, record, step,
                       (cfg.get("title") or "Demo world") + " (demo)", cfg.get("spawn"))
        live.source = DemoSource(lambda x, z: live.height_at(x, z), centre=(cfg.get("spawn") or (0, 0, 0))[0::2])
    else:
        if not args.host:
            raise SystemExit("give --host (your server's address, e.g. 123.45.67.89 or play.example.com), or --demo")
        password = password or os.environ.get("MCSM_RCON_PASSWORD") or cfg.get("password")
        if not password:
            import getpass
            password = getpass.getpass("RCON password: ")
        port = args.rcon_port or cfg.get("port") or 25575
        client = RconClient(args.host, port, password)
        client.connect()  # fail fast with a clear message
        live = LiveMap(RconSource(client), root, cfg.get("world"), args.interval, record, step,
                       cfg.get("title") or args.host, cfg.get("spawn"))
    url = live.start(args.http_port)
    return live, url


def cmd_live(args) -> int:
    import time as _time
    import webbrowser
    from contextlib import ExitStack
    with ExitStack() as stack:
        root = stack.enter_context(open_server(args.server, include_regions=True)) if args.server else None
        live, url = start_live(args, root)
        print(f"Live map running at {url}  (Ctrl+C to stop)")
        if args.record:
            print(f"Recording positions to {os.path.abspath(args.record)}")
        if not args.no_browser:
            webbrowser.open(url)
        try:
            while True:
                _time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            live.stop()
    return 0


def _track_files(specs: list[str] | None) -> list[str]:
    import glob
    out = []
    for p in specs or []:
        out += sorted(glob.glob(os.path.join(p, "*.jsonl"))) if os.path.isdir(p) else [p]
    return out


def start_history(args, data: m.ServerData, stack: ExitStack | None = None, terrain_source: str | None = None):
    """Create and start the history viewer for CLI/GUI. Returns ``(history, url)``.

    Terrain comes from the newest backup (*terrain_source*, else the newest one loaded); a zip
    that was read selectively has no region files, so it is opened again with them (kept open
    on *stack*, default: until the command ends). With ``--terrain all`` (default) every older
    backup becomes a terrain layer too, built afterwards, so the page can show the world as it
    was at the time on the slider.
    """
    from .history import HistoryMap
    newest = terrain_source or (data.backups[-1]["path"] if data.backups else
                                (args.server[0] if isinstance(args.server, list) else args.server))
    root = data.root
    if newest and os.path.isfile(newest):
        root = (stack or _OPEN).enter_context(open_server(newest, include_regions=True))
    cfg = live_settings(root)
    step = None if args.terrain_detail == "auto" else int(args.terrain_detail)
    from .flashback import find_flashback_dir
    from .paths import epoch_ms

    def when(b):
        t = b.get("time")
        return epoch_ms(t) if hasattr(t, "timestamp") else None
    norm = lambda p: os.path.normcase(os.path.abspath(p)) if p else p  # noqa: E731
    top = next((b for b in data.backups if norm(b.get("path")) == norm(newest)), None)
    older = []
    if getattr(args, "terrain", "all") == "all" and len(data.backups) > 1:
        older = [{"label": b.get("label") or os.path.basename(b["path"]), "t": when(b), "path": b["path"]}
                 for b in data.backups if b is not top and b.get("path") and os.path.exists(b["path"])]
        older.sort(key=lambda b: b["t"] or 0, reverse=True)
    hm = HistoryMap(data, lambda r: _replay_file(data, r), root, cfg.get("world"), step,
                    args.cache, _track_files(args.tracks), cfg.get("title") or "", cfg.get("spawn"),
                    decode_replays=not args.no_replays, highlights_file=args.highlights,
                    flashback_dir=find_flashback_dir(args.flashback_dir), terrain_backups=older,
                    flashback_pick=None if args.flashback_dir else (lambda r: find_flashback_dir(None, r.mc_version)),
                    terrain_label=(top or {}).get("label") or "", terrain_time=when(top) if top else None)
    return hm, hm.start(args.http_port)


def cmd_history(args) -> int:
    import time as _time
    import webbrowser
    from .paths import default_cache_dir
    data = _load(args)
    hm, url = start_history(args, data)
    n = sum(1 for r in data.replays if r.format == "flashback" and r.start and not r.unfinished)
    print(f"History viewer running at {url}  (Ctrl+C to stop)", flush=True)
    print(f"History     : {len(data.events)} events, {len(data.sessions)} sessions, {len(data.replays)} replays")
    print(f"Highlights  : {hm.highlights.path}"
          + (f"  (export to Flashback: {hm.flashback_dir})" if hm.flashback_dir else
             "  (Flashback folder not found: exports go next to that file; see --flashback-dir)"))
    if not args.no_replays and n:
        print(f"Paths       : reading player positions from {n} Flashback recordings in the background "
              f"(cached in {args.cache or default_cache_dir()})")
    if not args.no_browser:
        webbrowser.open(url)
    _print_warnings(data)
    last = None
    try:
        while True:
            _time.sleep(1)
            done, total = hm.decode_progress
            if total and (done, total) != last and (done == total or done % 10 == 0):
                print(f"Paths       : {done}/{total} recordings read", file=sys.stderr)
                last = (done, total)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        hm.stop()
    return 0


def _flashback_replay(data: m.ServerData, spec: str) -> m.Replay:
    r = _resolve_replay(data, spec)
    if r.format != "flashback":
        raise SystemExit(f"{r.path} is a ReplayMod replay; this needs a Flashback replay (.zip). "
                         "ReplayMod users: use 'mcsm sync' for editor markers.")
    if r.unfinished:
        raise SystemExit(f"{r.path} is an unfinished recording folder; Flashback can only open finished .zip replays")
    if r.start is None:
        raise SystemExit(f"{r.path} has no usable start time")
    return r


def _replay_file(data: m.ServerData, r: m.Replay) -> str:
    """The replay's file on disk (complete, even if the backup was read selectively)."""
    from .source import ensure_full
    if os.path.isabs(r.path):
        p = r.path
    elif data.sources and "/" in r.path and r.path.split("/", 1)[0] in data.sources:
        label, rel = r.path.split("/", 1)
        p = os.path.join(data.sources[label], rel)
    else:
        p = os.path.join(data.root, r.path)
    return ensure_full(os.path.normpath(p))


def _safe_name(text: str) -> str:
    return "".join(c if c.isalnum() or c in " -_()" else "_" for c in text).strip() or "replay"


def _flashback_output(args, r: m.Replay, suffix: str) -> tuple[str, str | None]:
    """(output zip path, flashback dir or None)."""
    from . import flashback as fbm
    fb_dir = fbm.find_flashback_dir(args.flashback_dir, r.mc_version)
    if args.install and not fb_dir:
        raise SystemExit("Flashback folder not found. Pass --flashback-dir (e.g. "
                         "C:/Users/<you>/AppData/Roaming/.minecraft/flashback, or your launcher instance's .minecraft/flashback)")
    base = _safe_name(os.path.splitext(os.path.basename(r.path.rstrip("/")))[0] + suffix) + ".zip"
    if args.output:
        out = args.output
    elif args.install:
        out = os.path.join(fb_dir, "replays", base)
    else:
        out = base
    if os.path.exists(out):
        raise SystemExit(f"{out} already exists; choose another --output")
    return out, fb_dir


def _warn_if_empty_timeline(r: m.Replay) -> None:
    """ServerReplay 3.4.0/3.4.1 saved recordings without a timeline (no ticks, no movement); the
    length mcsm shows is an estimate, and Flashback has nothing to play."""
    if r.raw_meta.get("duration_note"):
        print(f"  ! This recording has no timeline inside (total_ticks {r.raw_meta.get('total_ticks', 1)}; "
              "typical of ServerReplay 3.4.0/3.4.1). Its length is estimated from the save time, and "
              "Flashback will likely show an empty replay. Recordings from ServerReplay 3.5+ are fine.")


def cmd_flashback(args) -> int:
    return run_flashback(_load(args), args)


def run_flashback(data: m.ServerData, args) -> int:
    from . import flashback as fbm, sync
    r = _flashback_replay(data, args.replay)
    _warn_if_empty_timeline(r)
    out, fb_dir = _flashback_output(args, r, " (mcsm)")
    types = args.types.split(",") if args.types else None
    if types == ["all"]:
        types = [t for t in m.ALL_EVENT_TYPES if t != m.EV_REPLAY_MARKER]
    types = [t for t in (types or DEFAULT_SYNC_TYPES) if t != m.EV_REPLAY_MARKER]  # the replay's own markers are kept anyway
    players = args.players.split(",") if args.players else None
    tl = sync.timeline_from_replay(r)
    events = sync.build_markers(data.events, tl, types, players)
    markers = [fbm.FlashbackMarker(fbm.tick_of(mk.seconds), mk.label, fbm.MARKER_COLOURS.get(mk.type, 0xFF5555))
               for mk in events]
    meta = fbm.write_replay_copy(_replay_file(data, r), out, markers, " (mcsm)")
    print(f"Replay      : {r.path}  (start {_local(r.start)}, via {r.start_source})")
    print(f"Markers     : {meta['_markers_added']} added")
    print(f"Copy        : {os.path.abspath(out)}")
    if args.install and fb_dir and fbm.copy_editor_state(fb_dir, meta.get("original_uuid_mcsm"), meta["uuid"]):
        print("Editor      : your existing Flashback timeline for this replay was copied over")
    if not args.install:
        print("Next        : put the copy in Flashback's replays folder (or re-run with --install), open it in Flashback")
    else:
        print("Next        : open Flashback -> Replays and choose the copy (name ends with '(mcsm)')")
    _print_warnings(data)
    return 0


def cmd_timelapse(args) -> int:
    return run_timelapse(_load(args), args)


def run_timelapse(data: m.ServerData, args) -> int:
    import json as _json
    from . import flashback as fbm, timelapse as tlm
    r = _flashback_replay(data, args.replay)
    _warn_if_empty_timeline(r)
    area = tlm.Area.parse(args.area, args.area_name, args.dimension)
    start, end = r.start, r.end
    merge_gap = parse_duration(args.merge_gap)
    if args.tracks:
        samples = tlm.load_tracks(args.tracks)
        windows = tlm.windows_from_tracks(samples, area, start, end, merge_gap, args.pad)
        source = f"{len(samples)} track samples"
    else:
        windows = tlm.windows_from_sessions(data.sessions, start, end, merge_gap, args.pad)
        source = "log sessions (anyone online counts -- record tracks with 'mcsm live --record' for real area detection)"
    if not windows:
        raise SystemExit(f"Nobody was in {area.name} during this replay (source: {source}).")
    plan = tlm.plan(windows, None if args.speed else parse_duration(args.length), args.speed, args.gap_seconds)
    print(f"Replay      : {r.path}  ({_local(start)} -> {_local(end)})")
    print(f"Area        : {area.name} x {area.x1:g}..{area.x2:g}, z {area.z1:g}..{area.z2:g} ({area.dimension})")
    print(f"Activity    : {len(windows)} period{'s' if len(windows) != 1 else ''} from {source}")
    print(f"Output      : {format_hms(plan.total_seconds)} at {plan.active_speed:.0f}x while active")
    for line in tlm.describe(plan):
        print("   " + line)
    if args.dry_run:
        return 0

    keys = tlm.timelapse_keyframes(plan, start)
    if args.camera:
        try:
            cam = [float(v) for v in args.camera.split(",")]
            assert len(cam) == 5
        except (ValueError, AssertionError):
            raise SystemExit("--camera needs X,Y,Z,YAW,PITCH (e.g. 120,110,260,180,35)")
    else:
        cam = list(tlm.auto_camera(area))
    out, fb_dir = _flashback_output(args, r, " (timelapse)")
    markers = []
    for s in plan.segments:
        if s.kind == "active":
            who = ", ".join(sorted(s.players)) or "Someone"
            markers.append(fbm.FlashbackMarker(fbm.tick_of((s.real_start - start).total_seconds()),
                                               f"{who} at {area.name}", fbm.MARKER_COLOURS["activity_start"]))
            markers.append(fbm.FlashbackMarker(fbm.tick_of((s.real_end - start).total_seconds()),
                                               f"{area.name}: activity ends", fbm.MARKER_COLOURS["activity_end"]))
    meta = fbm.write_replay_copy(_replay_file(data, r), out, markers, " (timelapse)")
    state = fbm.editor_state(f"Timelapse: {area.name}", fbm.camera_keyframe(*cam), keys, (keys[0][0], keys[-1][0]))
    if args.install:
        state_path = fbm.install_editor_state(fb_dir, meta["uuid"], state)
    else:
        state_path = os.path.join(os.path.dirname(os.path.abspath(out)), f"{meta['uuid']}.json")
        with open(state_path, "w", encoding="utf-8") as fh:
            _json.dump(state, fh, separators=(",", ":"))
    # Overlay/chapters go next to the output -- or, when installing, in the current folder
    # (Flashback's replays folder should only hold replays).
    stem = os.path.splitext(os.path.basename(out))[0]
    if args.extras_dir:
        os.makedirs(args.extras_dir, exist_ok=True)
        base = os.path.join(args.extras_dir, stem)
    else:
        base = stem if args.install and not args.output else os.path.splitext(out)[0]
    _write_timelapse_overlays(plan, area, base)
    print(f"Copy        : {os.path.abspath(out)}")
    print(f"Timeline    : {state_path}")
    print(f"Overlay     : {os.path.abspath(base)}.time.srt  (real date/time, for your editor)")
    print(f"Chapters    : {os.path.abspath(base)}.chapters.txt")
    if args.install:
        print("Next        : open Flashback -> Replays -> the '(timelapse)' copy; the camera and timelapse are on the timeline. "
              "Export the scene as a video.")
    else:
        print(f"Next        : copy the .zip to <.minecraft>/flashback/replays/ and {os.path.basename(state_path)} to "
              "<.minecraft>/flashback/editor_states/ (or re-run with --install), then open it in Flashback.")
    _print_warnings(data)
    return 0


def _write_timelapse_overlays(plan, area, base: str) -> None:
    """Real-date/time subtitle (updates every output second) and YouTube-style chapters."""
    from .exporters.markers import _srt_time
    lines, n = [], 0
    for s in plan.segments:
        if s.kind == "gap":
            hours = (s.real_end - s.real_start).total_seconds() / 3600
            mins = round(hours * 60)
            text = (f"{hours:.0f} hours later" if hours >= 1.5
                    else "1 minute later" if mins <= 1 else f"{mins} minutes later")
            n += 1
            lines.append(f"{n}\n{_srt_time(s.out_start)} --> {_srt_time(s.out_end)}\n{text}\n")
            continue
        t = s.out_start
        while t < s.out_end - 1e-6:
            nxt = min(s.out_end, t + 1.0)
            real = s.real_start + timedelta(seconds=(t - s.out_start) * s.speed)
            n += 1
            lines.append(f"{n}\n{_srt_time(t)} --> {_srt_time(nxt)}\n"
                         f"{real.astimezone().strftime('%a %d %b %Y  %H:%M')}\n")
            t = nxt
    with open(base + ".time.srt", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    chapters, last = [], -999.0
    for s in plan.segments:
        if s.kind != "active":
            continue
        at = 0.0 if not chapters else s.out_start
        if at - last < 10 and chapters:
            continue
        who = ", ".join(sorted(s.players)) or "activity"
        chapters.append(f"{format_hms(at)} {s.real_start.astimezone().strftime('%a %d %b %H:%M')} - {who}")
        last = at
    with open(base + ".chapters.txt", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(chapters) + "\n")


def cmd_library(args) -> int:
    from . import library as lib
    say = lambda msg: print(msg, flush=True)  # noqa: E731
    library = args.library or lib.default_library_dir()
    if args.action == "import":
        if not args.paths:
            print("give one or more backups (folders, .zip or .tgz) to import", file=sys.stderr)
            return 2
        failed = 0
        for p in args.paths:
            try:
                r = lib.import_backup(p, library, args.mode, args.world, say, force=args.force)
            except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
                print(f"Could not import {p}: {exc}", file=sys.stderr)
                failed += 1
                continue
            if r.get("skipped"):
                continue
            print(f"Imported into {r['backup']}\n"
                  f"  {r['files']:,} files, {lib.human_bytes(r['bytes'])}; "
                  f"{r['deduplicated']:,} shared with other backups, "
                  f"{lib.human_bytes(r['bytes_written'])} new on disk ({r['mode']})\n"
                  f"  recordings: {r['recordings_new']} new, {r['recordings_known']} already in the library")
        return 1 if failed else 0
    if args.action in ("scan", "import-all"):
        if len(args.paths) != 1:
            print("give the one folder that holds your backups", file=sys.stderr)
            return 2
        plan = lib.scan(args.paths[0], library)
        print(lib.describe_plan(plan), flush=True)
        if args.action == "scan":
            print("\nNothing was changed. Run `mcsm library import-all FOLDER` to import this.")
            return 0
        mode = "copy" if args.mode == "auto" else args.mode
        if mode != "copy":
            print(f"Note: --mode {mode} " + ("MOVES the files out of your folders." if mode == "move" else
                  "hard-links the files: the library shares data with your folders."), flush=True)
        print(f"\nImporting into {library} ({mode}) ...", flush=True)
        r = lib.import_all(plan, library, mode, args.world, say)
        print(f"\nDone: {len(r['imported'])} imported, {len(r['skipped'])} already there, "
              f"{len(r['failed'])} failed" + "".join(f"\n  failed: {f['path']}: {f['error']}" for f in r["failed"]))
        return 1 if r["failed"] else 0
    if args.action == "tidy":
        if len(args.paths) != 1:
            print("give the one folder that holds your backups", file=sys.stderr)
            return 2
        if args.undo:
            r = lib.tidy_undo(args.paths[0], say)
            print(f"{r['put_back']} moves undone")
            return 0
        plan = lib.scan(args.paths[0], library)
        moves = lib.tidy_plan(plan, rename=args.rename)
        print(lib.describe_tidy(plan, moves), flush=True)
        if not args.apply or not moves:
            if moves:
                print("Nothing was changed. WARNING: --apply moves files in your original folder "
                      "(never deletes; undo with --undo). Import into the library first.")
            return 0
        r = lib.tidy_apply(plan, moves, say)
        print(f"{r['moved']} moved. To put everything back: mcsm library tidy FOLDER --undo "
              f"(log: {r['undo_file']})")
        return 0
    if args.action in ("add-projects", "projects"):
        worlds = lib.list_worlds(library, count_recordings=False)
        w = next((x for x in worlds if args.world and x["name"].lower() == args.world.lower()), None)
        if w is None and len(worlds) == 1 and not args.world:
            w = worlds[0]
        if w is None:
            print(f"choose the world with --world ({', '.join(x['name'] for x in worlds) or 'import a backup first'})",
                  file=sys.stderr)
            return 2
        if args.action == "add-projects":
            if len(args.paths) != 1:
                print("give the folder whose subfolders are your video projects", file=sys.stderr)
                return 2
            r = lib.add_projects(args.paths[0], w["path"], say)
            print(f"{len(r['projects'])} projects, {r['editor_states']} Flashback edits copied, "
                  f"{r['recordings_added']} recordings new to the library")
            for m_ in r["missing_edits"]:
                print(f"  no Flashback edits found for {m_}")
            return 0
        for pr in lib.list_projects(w["path"]):
            print(f"{pr['name']}  ({pr.get('status', '')}, {len(pr.get('recordings') or [])} recordings)")
            for rec in pr.get("recordings") or []:
                who = f"{rec['perspective']}: " if rec.get("perspective") not in (None, "", ".") else ""
                print(f"  {who}{rec.get('file') or rec.get('original')}"
                      + ("  + edits" if rec.get("editor_state") else ""))
        return 0
    if args.action == "add-recordings":
        worlds = lib.list_worlds(library)
        w = next((x for x in worlds if args.world and x["name"].lower() == args.world.lower()), None)
        if w is None and len(worlds) == 1 and not args.world:
            w = worlds[0]
        if w is None:
            print(f"choose the world with --world ({', '.join(x['name'] for x in worlds) or 'import a backup first'})",
                  file=sys.stderr)
            return 2
        r = lib.add_recordings(args.paths, w["path"], args.mode, say)
        print(f"{r['added']} recordings added to {w['name']}, {r['known']} already there")
        return 0
    worlds = lib.list_worlds(library)
    print(f"Library: {library}")
    if not worlds:
        print("  (empty: mcsm library import BACKUP)")
    for w in worlds:
        print(f"\n{w['name']}  (seed {w.get('seed')}, {w['recordings']} recordings)  {w['path']}")
        for b in w["backups"]:
            print(f"  {b['name']:<20} {b.get('version') or '?':<10} {b.get('files', 0):>8,} files  "
                  f"{lib.human_bytes(b.get('bytes')):>9}  from {b.get('source', '?')}")
    return 0


def cmd_video_text(args) -> int:
    data = _load(args)
    return run_video_text(data, args)


def run_video_text(data: m.ServerData, args) -> int:
    """Timed texts (chat, joins, deaths, advancements, date) for a finished Flashback video."""
    from . import videotext as vt
    video = args.video
    if not os.path.isfile(video):
        raise FileNotFoundError(video)
    if args.state:
        scenes = vt.load_scenes(args.state)
        uuid = os.path.splitext(os.path.basename(args.state))[0]
        idx = args.scene if args.scene is not None else 0
        if not 0 <= idx < len(scenes):
            raise ValueError(f"{args.state} has {len(scenes)} scenes; --scene counts from 0")
        match = {"state": args.state, "uuid": uuid, "scene": idx, "name": scenes[idx].get("name") or ""}
    else:
        length = vt.video_duration(video)
        if length is None:
            raise SystemExit("Can't read the video's length (ffprobe not found). Give --state EDITOR_STATE.json "
                             "(and --scene N) to say which Flashback scene the video was exported from.")
        found = vt.find_scenes_for_video(length)
        if not found:
            raise SystemExit(f"No Flashback scene with a length of {length:.1f} s was found (the edits may have "
                             "changed since the export). Give --state and --scene.")
        match = found[0]
        print(f"Video       : {os.path.basename(video)} ({length:.1f} s)")
        print(f"From        : scene '{match['name']}' of {match['uuid']} ({match.get('instance', '')}, "
              f"{match['length']:.1f} s)")
        if len(found) > 1:
            print(f"              ({len(found) - 1} other scene(s) have the same length; pick one with --state/--scene)")
    replay = vt._replay_for(data, match["uuid"])
    if replay is None:
        raise SystemExit(f"The recording {match['uuid']} isn't among the loaded recordings. Load the backup or "
                         "library world that has it (or add it with --replay FILE).")
    scene = vt.load_scenes(match["state"])[match["scene"]]
    clock = vt.scene_clock(scene, int(replay.raw_meta.get("total_ticks") or 0) or None)
    if clock is None:
        raise SystemExit("That scene has no export range.")
    kinds = tuple(args.kind or ("chat", "join", "leave", "death", "advancement", "date"))
    texts = vt.suggest_texts(data, replay, clock, kinds)
    base = os.path.splitext(os.path.join(args.output_dir or os.path.dirname(os.path.abspath(video)),
                                         os.path.basename(video)))[0]
    os.makedirs(os.path.dirname(base), exist_ok=True)
    vt.save_texts(texts, base + ".texts.json", {"video": os.path.abspath(video), "state": match["state"],
                                                "scene": match["scene"], "recording": replay.path})
    w, h = vt.video_size(video) or (1920, 1080)
    vt.write_ass(texts, base + ".ass", w, h, font=args.font)
    vt.write_srt(texts, base + ".srt")
    by = {}
    for t in texts:
        by[t.kind] = by.get(t.kind, 0) + 1
    print(f"Recording   : {replay.path} ({replay.recorded_player or '?'}, starts {_local(replay.start)})")
    print(f"Texts       : {len(texts)} (" + ", ".join(f"{k} {v}" for k, v in sorted(by.items())) + ")")
    print(f"Wrote       : {base}.ass  (styled; VLC/mpv play it next to the video)")
    print(f"              {base}.srt  (plain captions, e.g. YouTube)")
    print(f"              {base}.texts.json  (the list, to edit)")
    return 0


def cmd_world_text(args) -> int:
    """Put real in-world text (Minecraft text display entities) into a copy of a Flashback recording."""
    from . import flashback as fbm, videotext as vt, worldtext as wt
    src = args.replay
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    with zipfile.ZipFile(src) as zf:
        meta = json.loads(zf.read("metadata.json").decode("utf-8"))
    uid = str(meta.get("uuid") or "")
    yaw = args.yaw
    clock = scene = None
    if args.appear_at is not None or args.until is not None or not args.at:
        state = args.state or fbm.find_editor_state(uid)
        if state:
            scene = vt.load_scenes(state)[args.scene if args.scene is not None else 0]
            clock = vt.scene_clock(scene, int(meta.get("total_ticks") or 0) or None)
    if (args.appear_at is not None or args.until is not None) and clock is None:
        raise SystemExit("--appear-at/--until count seconds of your exported video, which needs the Flashback edit "
                         "(not found; give --state FILE)")
    if args.at:
        try:
            x, y, z = (float(v) for v in args.at.split(","))
        except ValueError:
            raise SystemExit("--at needs X,Y,Z (e.g. --at 120,80,-560)")
    else:
        if scene is None:
            raise SystemExit("No Flashback edit found for this recording: give --at X,Y,Z instead.")
        tick = clock.replay_tick(args.in_front_at) if clock else args.in_front_at * 20
        cam = wt.eye_at(scene, tick)
        if cam is None:
            raise SystemExit("That scene has no camera keyframes: give --at X,Y,Z instead.")
        pos, cyaw, cpitch = cam
        x, y, z = wt.in_front(pos, cyaw, min(cpitch, 20), args.distance)
        if yaw is None:
            yaw = (cyaw + 180) % 360            # a fixed text faces back at that camera
        print(f"Camera at {args.in_front_at:g} s of '{scene.get('name')}': ({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f}), "
              f"looking {cyaw:.0f}deg -> text at ({x:.1f}, {y:.1f}, {z:.1f})")
    colour = args.colour.lower()
    if colour not in wt.COLOURS and not (colour.startswith("#") and len(colour) == 7):
        raise SystemExit(f"--colour: one of {', '.join(wt.COLOURS)} or #RRGGBB")
    appear = int(clock.replay_tick(args.appear_at)) if args.appear_at is not None else None
    gone = int(clock.replay_tick(args.until)) if args.until is not None else None
    text = wt.WorldText(args.text, x, y, z, size=args.size, colour=colour, bold=args.bold, facing=args.facing,
                        yaw=yaw or 0.0, shadow=not args.no_shadow, glow=not args.no_glow,
                        outline="black" if args.border else "", camera_yaw=yaw,
                        appear_tick=appear, disappear_tick=gone)
    fb_dir = None
    if args.output:
        dst = args.output
    else:
        fb_dir = fbm.find_flashback_dir(args.flashback_dir, str(meta.get("version_string") or "") or None)
        if not fb_dir:
            raise SystemExit("Flashback folder not found: give -o FILE or --flashback-dir")
        base = os.path.splitext(os.path.basename(src))[0] + " (text)"
        dst = os.path.join(fb_dir, "replays", base + ".zip")
        n = 2
        while os.path.exists(dst):
            dst = os.path.join(fb_dir, "replays", f"{base} {n}.zip")
            n += 1
    new = wt.write_world_text_copy(src, dst, [text])
    print(f"Wrote       : {dst}  ({new['_chunks']} chunk files, original unchanged)")
    if fb_dir:
        old_state = fbm.find_editor_state(uid)
        if old_state:
            target = os.path.join(fb_dir, "editor_states", f"{new['uuid']}.json")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if not os.path.exists(target):
                shutil.copy2(old_state, target)
                print(f"Your edit   : copied to the copy ({target})")
        print("In Flashback: open the replay named '" + new["name"] + "', check the text, then export as usual.")
    return 0


def cmd_video_editor(args) -> int:
    """Live text editor for a finished Flashback video, in the browser."""
    import time as _time
    import webbrowser
    from . import library as lib
    from .videoeditor import VideoEditor
    world = args.world
    if not world and args.library:
        worlds = lib.list_worlds(args.library, count_recordings=False)
        world = worlds[0]["path"] if len(worlds) == 1 else None
    if args.list_edits:
        from . import videotext as vt
        for e in vt.list_edits(world):
            length = f"{e['length']:.1f} s" if e["length"] is not None else "no export range"
            print(f"{e['saved_text']}  {e['scene_name']:<22} {e['recording_name']:<40} {length:>16}  "
                  f"{e['keyframes']:>4} keys{'  copy' if e['copy'] else ''}")
            print(f"    --edit \"{e['state']}\" --scene {e['scene'] + 1}")
        return 0
    if not args.video and not args.edit:
        print("Give --video (a finished video) or --edit (a Flashback edit .json)", file=sys.stderr)
        return 2
    print("Finding the Flashback edit and recording…", flush=True)
    ed = VideoEditor(args.video, world, edit=args.edit, scene=None if args.scene is None else args.scene - 1)
    url = ed.start(args.http_port)
    print(f"Live editor running at {url}  (Ctrl+C to stop)", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        while True:
            _time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        ed.stop()
    return 0


def cmd_flashback_link(args) -> int:
    """The Flashback companion mod: is it running, which replay is open; or install it."""
    from . import flashlink
    if args.action == "install":
        insts = [i for i in flashlink.instances() if os.path.isdir(i["minecraft"])]
        if args.instance:
            insts = [i for i in insts if i["name"] == args.instance]
        if len(insts) != 1:
            names = ", ".join(i["name"] for i in flashlink.instances()) or "none found"
            print(f"Choose the instance with --instance NAME (instances with Flashback: {names})", file=sys.stderr)
            return 2
        print("Installed", flashlink.install_mod(insts[0]["mods"]))
        return 0
    link, why = flashlink.find_link()
    if not link:
        print("Not connected:", why)
        return 1
    st = link.status()
    rep = st.get("replay")
    print(f"Connected to '{link.instance.get('name')}' (Minecraft {st.get('minecraft')}, Flashback "
          f"{st.get('flashback')}, link {st.get('mod')})")
    print(f"Open replay: {rep['name']} ({rep['uuid']}), tick {rep['tick']} of {rep['total']}" if rep else
          "No replay open" + (f" ({st['error']})" if st.get("error") else ""))
    return 0


def cmd_music(args) -> int:
    from .music import detect_beats
    grid = detect_beats(args.file)
    print(f"Tempo       : {grid.bpm} BPM")
    print(f"First beat  : {grid.first_beat:.3f}s into the song")
    print(f"Length      : {format_hms(grid.song_length or 0)}")
    print(f"Confidence  : {grid.confidence}  (below ~0.3: check by ear, set --bpm manually)")
    return 0


# ---------------------------------------------------------------------------
# argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mcsm", description="Parse a downloaded Minecraft server: statistics, "
                                 "event timeline, and video/replay/music sync.")
    ap.add_argument("--version", action="version", version=f"mcsm {version_string()}")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("summary", help="quick overview in the terminal"); _add_common(p)
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("report", help="build the HTML statistics dashboard"); _add_common(p)
    p.add_argument("-o", "--output", help="output file (default: server-report.html)")
    p.add_argument("--title", help="title shown at the top of the report")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("export", help="write JSON / SQLite / CSV files"); _add_common(p)
    p.add_argument("--json", metavar="FILE"); p.add_argument("--sqlite", metavar="FILE")
    p.add_argument("--csv", metavar="DIR", help="folder for events/sessions/players/stats .csv")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("events", help="search events in the terminal"); _add_common(p)
    p.add_argument("--type", action="append", choices=m.ALL_EVENT_TYPES, help="repeatable")
    p.add_argument("--player", action="append", help="repeatable")
    p.add_argument("--since", help="'YYYY-MM-DD [HH:MM:SS]' (your local time unless --tz)")
    p.add_argument("--until")
    p.add_argument("--grep", help="text to search for")
    p.add_argument("--limit", type=int, help="only the last N matches")
    p.set_defaults(func=cmd_events)

    p = sub.add_parser("changes", help="Minecraft version, mods and datapacks changed between backups")
    _add_common(p)
    p.add_argument("--kind", action="append", choices=["minecraft", "mod", "plugin", "datapack", "world"],
                   help="only this kind of change (repeatable)")
    p.set_defaults(func=cmd_changes)

    p = sub.add_parser("replays", help="list ReplayMod (.mcpr) and Flashback (.zip) recordings"); _add_common(p)
    p.set_defaults(func=cmd_replays)

    p = sub.add_parser("sync", help="video markers/subtitles for a replay (+ music sync)",
                       description="Create markers/subtitles that line server events up with a video "
                                   "rendered from a replay (or any recording with a known start time). "
                                   "See docs/VIDEO_SYNC.md and docs/MUSIC_SYNC.md.")
    _add_common(p)
    g = p.add_argument_group("which video")
    g.add_argument("--replay", help="replay (.mcpr or Flashback .zip) path, its number from 'mcsm replays', or part of its file name")
    g.add_argument("--video-start", help="instead of a replay: real time at video 0:00, 'YYYY-MM-DD HH:MM:SS'")
    g.add_argument("--render-start", help="where in the replay your render starts, e.g. 12:30 (default 0)")
    g.add_argument("--length", help="video length (default: rest of the replay)")
    g.add_argument("--speed", type=float, default=1.0, help="render speed, e.g. 2 for a 2x timelapse")
    g = p.add_argument_group("what to mark")
    g.add_argument("--types", help=f"comma list or 'all' (default: {','.join(DEFAULT_SYNC_TYPES)})")
    g.add_argument("--players", help="comma list of players to include (default: everyone)")
    g.add_argument("--subtitle-seconds", type=float, default=4.0, help="how long each subtitle stays (default 4)")
    g = p.add_argument_group("output")
    g.add_argument("-o", "--out", default="sync-output", help="output folder (default: sync-output)")
    g.add_argument("--name", help="base file name (default: replay file name)")
    g.add_argument("--formats", help="comma list of srt,edl,csv,audacity,chapters,json (default: all)")
    g.add_argument("--fps", type=float, default=60.0, help="video frame rate for EDL timecode (default 60)")
    g.add_argument("--edl-start-hour", type=int, default=1, help="timeline start hour in Resolve (default 1 = 01:00:00:00)")
    g = p.add_argument_group("music sync (docs/MUSIC_SYNC.md)")
    g.add_argument("--music", metavar="AUDIO", help="song file to detect the beat from (WAV; others need ffmpeg)")
    g.add_argument("--bpm", type=float, help="tempo, if you know it (skips detection)")
    g.add_argument("--beat-offset", help="time of the first downbeat inside the song, e.g. 0.42")
    g.add_argument("--beats-per-bar", type=int, default=4)
    g.add_argument("--music-start", help="where the song starts on the video timeline (may be negative), e.g. 0:05")
    g.add_argument("--music-align", metavar="SONG=VIDEO",
                   help="place the song so SONG time hits VIDEO time; VIDEO may be '#N' = marker N, e.g. 1:02.5=#12")
    g.add_argument("--snap", choices=["none", "beat", "bar"], default="none",
                   help="move event markers to the nearest beat/bar")
    g.add_argument("--snap-tolerance", help="only snap markers within this many seconds (default: half a beat/bar)")
    g.add_argument("--beat-markers", choices=["beat", "bar", "none"], default="bar",
                   help="also write beat or bar markers (default: bar)")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("flashback", help="copy a Flashback replay with server events as markers",
                       description="Writes a COPY of a Flashback replay whose timeline shows server events "
                                   "(deaths, chat, joins, advancements) as markers. The original is never changed. "
                                   "See docs/FLASHBACK.md.")
    _add_common(p)
    p.add_argument("--replay", required=True, help="Flashback replay: number from 'mcsm replays', file path, or part of its name")
    p.add_argument("-o", "--output", help="output .zip (default: '<name> (mcsm).zip' in the current folder)")
    p.add_argument("--install", action="store_true", help="put the copy straight into Flashback's replays folder")
    p.add_argument("--flashback-dir", help="Flashback data folder (default: <.minecraft>/flashback; set this for Prism/Modrinth/CurseForge instances)")
    p.add_argument("--types", help=f"comma list or 'all' (default: {','.join(DEFAULT_SYNC_TYPES)})")
    p.add_argument("--players", help="comma list of players to include (default: everyone)")
    p.set_defaults(func=cmd_flashback)

    p = sub.add_parser("timelapse", help="activity-aware timelapse of an area, set up in Flashback",
                       description="Fixed camera over an area that only plays while players are there, fits a "
                                   "target length (3 min default) or speed. Writes a Flashback replay copy + editor "
                                   "timeline (camera + timelapse keyframes), a date/time overlay (.srt) and chapters. "
                                   "See docs/TIMELAPSE.md.")
    _add_common(p)
    p.add_argument("--replay", required=True, help="Flashback replay covering the area (number, path or name part)")
    p.add_argument("--area", required=True, metavar="X1,Z1,X2,Z2[,Y1,Y2]", help="the box to watch, in block coordinates")
    p.add_argument("--area-name", default="Base", help="name used in markers/chapters (default: Base)")
    p.add_argument("--dimension", default="minecraft:overworld", choices=["minecraft:overworld", "minecraft:the_nether", "minecraft:the_end"])
    p.add_argument("--tracks", action="append", metavar="DIR_OR_FILE",
                   help="live-map recordings (mcsm live --record DIR); repeatable. Without it, log sessions are used (coarse)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--length", default="3:00", help="target video length (default 3:00)")
    g.add_argument("--speed", type=float, help="fixed speed instead of a target length, e.g. 60")
    p.add_argument("--gap-seconds", type=float, default=0.5, help="output time for each idle gap (default 0.5 s)")
    p.add_argument("--merge-gap", default="2:00", help="join activity closer together than this (default 2:00)")
    p.add_argument("--pad", type=float, default=5.0, help="seconds kept before/after each activity period (default 5)")
    p.add_argument("--camera", metavar="X,Y,Z,YAW,PITCH", help="camera position/angle (default: automatic, south of the area looking north)")
    p.add_argument("-o", "--output", help="output .zip (default: '<name> (timelapse).zip' in the current folder)")
    p.add_argument("--install", action="store_true", help="install the replay copy and its editor timeline into Flashback")
    p.add_argument("--flashback-dir", help="Flashback data folder (default: <.minecraft>/flashback)")
    p.add_argument("--extras-dir", help="folder for the date/time overlay and chapters (default: next to the output; "
                                        "with --install: the current folder)")
    p.add_argument("--dry-run", action="store_true", help="only print the plan")
    p.set_defaults(func=cmd_timelapse)

    p = sub.add_parser("live", help="live 3D map of where players are right now (RCON)",
                       description="Show players moving in real time on a 3D map. Positions come from the running "
                                   "server over RCON; terrain from your downloaded world. See docs/LIVE_MAP.md.")
    p.add_argument("server", nargs="?", help="downloaded server folder or .zip (for terrain and RCON settings)")
    p.add_argument("--host", help="server address for RCON (the IP/hostname of your server)")
    p.add_argument("--rcon-port", type=int, help="RCON port (default: rcon.port from server.properties, else 25575)")
    p.add_argument("--interval", type=float, default=1.0, help="seconds between position updates (default 1)")
    p.add_argument("--http-port", type=int, default=8765, help="local port for the map page (default 8765)")
    p.add_argument("--terrain-detail", default="auto", choices=["auto", "1", "2", "4", "8", "16"],
                   help="sample every N blocks (1 = full detail; auto keeps big worlds fast)")
    p.add_argument("--record", metavar="DIR", help="also save every position update to DIR/YYYY-MM-DD.jsonl")
    p.add_argument("--demo", action="store_true", help="simulated players; no server needed")
    p.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    p.set_defaults(func=cmd_live)
    p.epilog = "The RCON password is read from server.properties in SERVER, the MCSM_RCON_PASSWORD environment variable, or asked for."

    p = sub.add_parser("history", help="3D time machine: all backups and recordings as one timeline",
                       description="A 3D map with a time slider over the whole history: player paths decoded "
                                   "from Flashback recordings, joins, deaths and chat, sessions and replay "
                                   "coverage. Terrain from each backup (the world as it was at that time). "
                                   "See docs/HISTORY.md.")
    _add_common(p)
    p.add_argument("--tracks", action="append", metavar="DIR_OR_FILE",
                   help="also show live-map recordings (mcsm live --record DIR); repeatable")
    p.add_argument("--no-replays", action="store_true", help="don't read positions from Flashback recordings")
    p.add_argument("--cache", metavar="DIR", help="where decoded paths are kept (default: %%LOCALAPPDATA%%/mcsm/paths)")
    p.add_argument("--highlights", metavar="FILE",
                   help="where highlights are kept (default: Documents/mcsm-highlights/<world>.json)")
    p.add_argument("--flashback-dir", help="Flashback data folder for exporting highlights "
                                           "(default: <.minecraft>/flashback if it exists)")
    p.add_argument("--http-port", type=int, default=8766, help="local port for the page (default 8766)")
    p.add_argument("--terrain-detail", default="auto", choices=["auto", "1", "2", "4", "8", "16"],
                   help="sample every N blocks (1 = full detail; auto keeps big worlds fast)")
    p.add_argument("--terrain", default="all", choices=["all", "newest"],
                   help="all (default): terrain from every backup, so the map changes with the time "
                        "slider; newest: only the newest backup's")
    p.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("library", help="keep all backups and recordings in one library folder",
                       description="One folder per world; each backup in a dated folder; every recording stored "
                                   "once. Give a library world folder as SERVER to any command to load all of it.")
    p.add_argument("action", choices=["list", "import", "scan", "import-all", "add-recordings", "add-projects",
                                      "projects", "tidy"])
    p.add_argument("paths", nargs="*", help="import: backup folders, .zip or .tgz files; scan / import-all: "
                                            "a folder full of backups (any host); add-recordings: recording "
                                            "files or folders")
    p.add_argument("--library", metavar="DIR", help="library folder (default Documents/mcsm-library)")
    p.add_argument("--mode", choices=["auto", "link", "copy", "move"], default="auto",
                   help="auto (default): hard links on the same drive (no extra space), else copy "
                        "(import-all: copy, so originals are only read); move: move the files in")
    p.add_argument("--world", help="world folder name in the library (default: from the backup's level name)")
    p.add_argument("--force", action="store_true", help="import even if a backup from the same time is there")
    p.add_argument("--apply", action="store_true",
                   help="tidy: really move the files (without it, tidy only shows the plan)")
    p.add_argument("--rename", action="store_true", help="tidy: also put the date in front of backup folder names")
    p.add_argument("--undo", action="store_true", help="tidy: put back everything the last tidy moved")
    p.set_defaults(func=cmd_library)

    p = sub.add_parser("video-text", help="timed in-game texts (chat, deaths, titles...) for a finished Flashback video",
                       description="Lines a video exported from Flashback up with the server's history (the Flashback "
                                   "scene is found from the video's length) and writes styled subtitles.")
    _add_common(p)
    p.add_argument("--video", required=True, help="the finished video (.mp4) exported from Flashback")
    p.add_argument("--state", metavar="FILE", help="the Flashback editor state it was exported from (default: found)")
    p.add_argument("--scene", type=int, help="scene number in that editor state, from 0")
    p.add_argument("--replay", metavar="FILE", help="the recording, if it isn't in the loaded backups")
    p.add_argument("--kind", action="append", choices=["chat", "join", "leave", "death", "advancement", "date"],
                   help="only these texts (repeatable; default all)")
    p.add_argument("--font", default="Arial", help="font for the styled subtitles (default Arial)")
    p.add_argument("--output-dir", "-o", metavar="DIR", help="where to write (default: next to the video)")
    p.set_defaults(func=cmd_video_text)

    p = sub.add_parser("world-text", help="put real 3D text into the world of a copy of a Flashback recording",
                       description="Adds a Minecraft text display (real in-world text: lit by the world, hidden behind "
                                   "blocks) to a copy of a recording. Open the copy in Flashback (your edit is copied to "
                                   "it) and export again.")
    p.add_argument("--replay", required=True, help="the Flashback recording (.zip)")
    p.add_argument("--text", required=True)
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--at", metavar="X,Y,Z", help="world position of the text")
    where.add_argument("--in-front-at", type=float, metavar="SECONDS",
                       help="place it in front of the camera at this second of the exported video (from your edit)")
    p.add_argument("--distance", type=float, default=30.0, help="blocks in front of the camera (default 30)")
    p.add_argument("--state", metavar="FILE", help="Flashback editor state (default: found)")
    p.add_argument("--scene", type=int, help="scene number in the edit, from 0 (default 0)")
    p.add_argument("--size", type=float, default=4.0, help="letter height in blocks (default 4)")
    p.add_argument("--colour", "--color", default="white", help="Minecraft colour name (gold, aqua...) or #RRGGBB")
    p.add_argument("--bold", action="store_true")
    p.add_argument("--facing", choices=["center", "vertical", "fixed"], default="center",
                   help="center: always faces the camera (default); vertical: turns but stays upright; fixed: like a sign")
    p.add_argument("--yaw", type=float, help="for --facing fixed: direction it faces (degrees)")
    p.add_argument("--no-shadow", action="store_true")
    p.add_argument("--no-glow", action="store_true", help="lit by the world instead of always bright")
    p.add_argument("--border", action="store_true", help="black border around the letters")
    p.add_argument("--appear-at", type=float, metavar="SECONDS",
                   help="appear at this second of your exported video (default: there all the time)")
    p.add_argument("--until", type=float, metavar="SECONDS", help="disappear at this second of the video")
    p.add_argument("--flashback-dir", help="Flashback folder to put the copy in (default: the one for its version)")
    p.add_argument("-o", "--output", metavar="FILE", help="write the copy here instead of into Flashback")
    p.set_defaults(func=cmd_world_text)

    p = sub.add_parser("video-editor", help="live editor for text on a finished Flashback video (in the browser)")
    p.add_argument("--video", help="the finished video exported from Flashback")
    p.add_argument("--edit", metavar="STATE.json", help="a Flashback edit (editor_states/<id>.json) that hasn't "
                   "been exported: the page shows the app's 3D map from its camera instead of a video")
    p.add_argument("--scene", type=int, help="with --edit: scene number (1 = first; default: the one open in Flashback)")
    p.add_argument("--list-edits", action="store_true", help="list your Flashback edits (scene, recording, length) "
                   "with the --edit/--scene to open each")
    p.add_argument("--world", metavar="DIR", help="library world folder (to find the recording)")
    p.add_argument("--library", metavar="DIR", help="library folder with one world (alternative to --world)")
    p.add_argument("--http-port", type=int, default=8790)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_video_editor)

    p = sub.add_parser("flashback-link", help="the Flashback companion mod: status, or install it into an instance")
    p.add_argument("action", choices=["status", "install"])
    p.add_argument("--instance", help="install: the Minecraft instance (as listed by status/the app)")
    p.set_defaults(func=cmd_flashback_link)

    p = sub.add_parser("music", help="detect the tempo (BPM) and first beat of a song")
    p.add_argument("file")
    p.set_defaults(func=cmd_music)
    return ap


# Options whose values often start with "-" (negative coordinates / times). argparse
# would otherwise mistake "--area -100,20,..." for a new option.
_NEGATIVE_OK = ("--area", "--camera", "--music-start", "--music-align", "--video-start")


def normalise_argv(argv: list[str]) -> list[str]:
    """Turn ``--area -100,5,...`` into ``--area=-100,5,...`` for the options above."""
    out, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in _NEGATIVE_OK and i + 1 < len(argv) and argv[i + 1].startswith("-") and argv[i + 1] != "--":
            out.append(f"{a}={argv[i + 1]}")
            i += 2
            continue
        out.append(a)
        i += 1
    return out


def parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(normalise_argv(list(argv)))


def main(argv: list[str] | None = None) -> int:
    from .rcon import RconError
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        with _OPEN:
            return args.func(args)
    except (FileNotFoundError, ValueError, RuntimeError, OSError, RconError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
