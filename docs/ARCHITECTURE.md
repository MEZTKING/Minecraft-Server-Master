# Architecture

## Design goals

- **Zero dependencies** – Python standard library only, so it runs anywhere Python 3.10+ does.
- **Read-only** – never modifies the server files; zips are extracted to a temporary folder
  that is deleted afterwards.
- **Everything in UTC internally** – convert as early as possible, display as late as possible.
- **One parse, many outputs** – every parser fills one `ServerData`; every exporter reads it.
- **Private** – nothing is uploaded; IP addresses are dropped; secrets in
  `server.properties` are masked.

## Layout

```
mcsm/
  __init__.py        version
  __main__.py        `python -m mcsm`
  cli.py             argument parsing and the commands (run_* functions take loaded data)
  gui.py             tkinter desktop window; calls the same run_* functions as the CLI
  assets/icon.png    window icon
  assets/web/        live.html (live 3D map), history.html (history viewer) + vendored three.js r160 (MIT)
  source.py          open a folder or .zip (selective "lite" extraction, replay stubs, ensure_full)
  archive.py         merge several backups (any versions) into one ServerData (+ library recordings)
  library.py         library folder: import folders/.zip/.tgz per world, recordings stored once,
                     unchanged files hard-linked; expand_sources() turns a world folder into its backups
  analyzer.py        runs all parsers, merges results into ServerData
  models.py          dataclasses: ServerData, Event, Session, PlayerProfile, Replay, ...
  timeutil.py        time zones, LogClock (local log time -> UTC), duration parsing
  nbt.py             NBT binary format reader (+ small writer for tests)
  logs.py            log files -> events; events -> sessions; crash reports
  players.py         usercache, stats, advancements, playerdata
  world.py           server.properties, level.dat, region sizes, mods/plugins, json lists
  replays.py         ReplayMod .mcpr + Flashback .zip (+ unfinished folders): start, length, markers
  rcon.py            RCON client (pipelined commands)
  live.py            live map: RCON/demo sources, polling, trails, recording, local web server + SSE;
                     TerrainServer and serve() shared with the history viewer
  paths.py           player paths decoded from Flashback chunk files (move_entities), cached as .jsonl tracks
  history.py         history viewer: paths + events + sessions + replays as JSON, local web server
                     (GET + header-protected POST for highlights)
  highlights.py      highlights store (JSON per world), covering recordings, Flashback export, clip list
  terrain.py         region files -> surface height/colour tiles for the 3D map: partial NBT scanner
                     (heightmap + lazily decoded section palettes only), tile cache; read in a
                     process pool by live.TerrainServer
  videotext.py       finished Flashback video <-> server time (export range + timelapse), timed texts, .ass/.srt
  flashback.py       write Flashback replay copies (markers, new uuid) and editor states (camera/timelapse)
  timelapse.py       areas, activity windows (tracks/sessions), target-length plan, timelapse keyframes
  sync.py            VideoTimeline and event -> Marker conversion
  music.py           BeatGrid, beat detection, snapping, alignment
  exporters/
    report.py        self-contained HTML dashboard
    data.py          JSON, SQLite, CSV
    markers.py       SRT, Resolve EDL, CSV, Audacity labels, YouTube chapters, JSON
tests/
  fixtures.py        builds a complete fake server (and a click-track WAV, Flashback replays)
  fake_rcon.py       fake RCON server speaking the real protocol (also runnable by hand)
  test_mcsm.py       unit + end-to-end tests
docs/                user and developer documentation
installer/           Windows packaging: PyInstaller spec, Inno Setup script, build script, icon
.github/workflows/   CI: tests + Windows installer build/release
```

## Data flow

```
 folder / zip ──source.open_server──► server root
                                         │
           analyzer.analyze(root) ───────┤
             1 world.read_properties      → where is the world folder?
             2 players.read_usercache     → uuid → name
               players.read_stats/advancements/playerdata
             3 LogClock(samples from advancement timestamps)
             4 logs.parse_logs(clock)     → events, log file info
             5 crash reports, replays     → more events
               logs.build_sessions        → sessions
             6 _merge_players             → PlayerProfile per name
                                         ▼
                                    ServerData
          ┌──────────────┬───────────────┼─────────────────────┐
   exporters.report  exporters.data   sync.build_markers   cli summary/events
   (HTML)            (JSON/SQL/CSV)   + music.BeatGrid
                                      → exporters.markers (SRT/EDL/...)
```

### Live map data flow

```
server ──RCON (list, data get entity …, pipelined)──► RconSource.poll()
                                                          │ every --interval s
download world/region/*.mca ──terrain.read_region_surface──► tiles (height + colour)
                                                          ▼
                       LiveMap (trails, join/leave, optional .jsonl recording)
                          │ http://127.0.0.1:<port>  (ThreadingHTTPServer)
                          ├─ /api/terrain/...  binary tiles
                          ├─ /api/stream       Server-Sent Events, one snapshot per poll
                          ▼
                 live.html (three.js): terrain meshes, markers, trails, labels
```

### History viewer data flow

```
ServerData (one or many backups) ──history_payload──► /api/history  events, sessions, replays, backups
Flashback replays ──paths.decode_points (move_entities of the recorded player)──►
    <cache>/<hash>.jsonl  (live-map track format; reused on later runs)
live-map recordings (--tracks) ─┐
                                ├─ HistoryMap.paths (merged per current name, thinned) ──► /api/paths
each backup's regions ──TerrainServer layers (newest first; unchanged regions shared by tile key)──►
    /api/terrain/<dim>?layer=N  (page swaps changed regions when the slider crosses a backup)
                                ▼
                 history.html (three.js): time slider, markers at time T, trails, event pins,
                 timeline canvas (sessions, recordings, backups, events)
```

## Key decisions

**Log dates are anchored to the last line.** Log4j names archives after the period they
cover, which is the date of the final line. Counting midnight roll-overs backwards from it
dates every line, including files that span midnight. See [LOG_FORMAT.md](LOG_FORMAT.md).

**Time zone comes from advancement files.** They are the only server file storing local
time *with* its UTC offset. `LogClock` keeps one offset per date and uses the nearest date,
which follows daylight-saving changes. `--tz` overrides it.

**Death detection needs a known player name.** Death messages have no fixed prefix, so a
line is only a death when it starts with a known player followed by a vanilla death phrase.

**The HTML report is one file with embedded JSON.** No server, no CDN, works offline and can
be shared by sending one file. All aggregation that depends on the viewer's time zone
(per-day totals, the heatmap) runs in the browser. Embedded data has `</` escaped so chat
text can never break out of the `<script>` tag.

**The GUI is a thin shell over the CLI.** Each button builds the argument list the
command line would receive, parses it with the same `argparse` parser, and calls the
matching `cli.run_*` function with the already-loaded `ServerData`, streaming its printed
output into the window. New CLI options are therefore available to the GUI just by adding a
field, and the two can never disagree.

**Markers are format-agnostic.** `sync.Marker` holds position/label/type; each output format
is a small function in `exporters/markers.py`.

## Extending

### A new log message

1. Add a regex and a branch in `logs.classify()` returning `(type, player, text, data)`.
2. If it's a new event type, add a constant in `models.py` (and `ALL_EVENT_TYPES`), a label
   in the report's `TYPE_LABEL`, and a colour in `sync.TYPE_COLOURS`.
3. Add a test line to `TestLogLines`, and document it in `docs/LOG_FORMAT.md`.

### A new data source

Write a reader returning plain dicts, call it from `analyzer.analyze()`, store the result on
`ServerData` (add a field in `models.py`), and render it in `exporters/report.py`. Add a file
to `tests/fixtures.py` and a test.

### A new marker format

Add `write_xxx(markers, path)` to `exporters/markers.py`, register it in `ALL_FORMATS` and
`write_all()`, and document the import steps in `docs/VIDEO_SYNC.md`.

## Ideas not yet implemented

See `PROJECT_STATUS.md` → Backlog for the maintained list with designs. Older notes:

- Reading chat/positions directly from the replay packet stream (`recording.tmcpr`) – would
  give sub-second timing but depends on the protocol version of each Minecraft release.
- Parsing chunk NBT in region files (block counts, builds, explored-area map image).
- Premiere Pro / Final Cut XML marker export.
- Per-player movement heatmaps from login positions and playerdata history.
