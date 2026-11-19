"""Command-line interface: ``mcsm <command> ...`` (or ``python -m mcsm``).

Commands
--------
summary   quick overview in the terminal
report    build the HTML statistics dashboard
export    write JSON / SQLite / CSV files
events    search events from the terminal
replays   list replay recordings found on the server
sync      make video markers/subtitles for a replay (+ music beat sync)
live      live 3D map of players (RCON)
music     detect the tempo (BPM) of a song

Run ``mcsm <command> --help`` for the options of each command, and see
``README.md`` / ``docs/`` for worked examples.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta

from . import __version__, models as m
from .analyzer import analyze
from .players import play_time_ticks
from .source import open_server
from .sync import DEFAULT_SYNC_TYPES
from .timeutil import UTC, format_hms, iso, parse_duration, parse_tz


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load(args) -> m.ServerData:
    tz = parse_tz(args.tz) if getattr(args, "tz", None) else None
    latest = date.fromisoformat(args.latest_log_date) if getattr(args, "latest_log_date", None) else None
    extra = []
    rp = getattr(args, "replay", None)
    if rp and os.path.isfile(rp):
        extra.append(rp)
    with open_server(args.server) as root:
        data = analyze(root, tz=tz, latest_log_date=latest, extra_replays=extra)
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
    p.add_argument("server", help="server folder or .zip download")
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
        root = stack.enter_context(open_server(args.server)) if args.server else None
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
    ap.add_argument("--version", action="version", version=f"mcsm {__version__}")
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

    p = sub.add_parser("music", help="detect the tempo (BPM) and first beat of a song")
    p.add_argument("file")
    p.set_defaults(func=cmd_music)
    return ap


def main(argv: list[str] | None = None) -> int:
    from .rcon import RconError
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, RuntimeError, OSError, RconError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
