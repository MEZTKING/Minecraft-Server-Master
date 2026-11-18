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
  cli.py             argument parsing and the commands
  source.py          open a folder or .zip, find the server root inside it
  analyzer.py        runs all parsers, merges results into ServerData
  models.py          dataclasses: ServerData, Event, Session, PlayerProfile, Replay, ...
  timeutil.py        time zones, LogClock (local log time -> UTC), duration parsing
  nbt.py             NBT binary format reader (+ small writer for tests)
  logs.py            log files -> events; events -> sessions; crash reports
  players.py         usercache, stats, advancements, playerdata
  world.py           server.properties, level.dat, region sizes, mods/plugins, json lists
  replays.py         .mcpr metadata + markers
  sync.py            VideoTimeline and event -> Marker conversion
  music.py           BeatGrid, beat detection, snapping, alignment
  exporters/
    report.py        self-contained HTML dashboard
    data.py          JSON, SQLite, CSV
    markers.py       SRT, Resolve EDL, CSV, Audacity labels, YouTube chapters, JSON
tests/
  fixtures.py        builds a complete fake server (and a click-track WAV)
  test_mcsm.py       unit + end-to-end tests
docs/                user and developer documentation
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

- Reading chat/positions directly from the replay packet stream (`recording.tmcpr`) – would
  give sub-second timing but depends on the protocol version of each Minecraft release.
- Parsing chunk NBT in region files (block counts, builds, explored-area map image).
- Premiere Pro / Final Cut XML marker export.
- Per-player movement heatmaps from login positions and playerdata history.
