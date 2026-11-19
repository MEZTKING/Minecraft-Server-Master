# Syncing server events with a replay video

This guide explains how `mcsm sync` turns server log events into markers and subtitles that
sit at the right moment in a video rendered from a replay.

## How it works

Three clocks are involved:

| Clock | Where it comes from | Precision |
|---|---|---|
| **Real time of each event** | Log line `[20:15:03]` + date from the log file name + server time zone → UTC | 1 second |
| **Replay start** | ReplayMod `.mcpr`: `metaData.json` `"date"` (Unix ms, UTC) and `"duration"` (ms). Flashback `.zip`: the real-time-clock entry inside `c0.flashback` (see below) and `total_ticks` | 1 ms (ReplayMod), ~50 ms (Flashback) |
| **Video time** | Where you started rendering in the replay, and the render speed | – |

The maths is:

```
video_0_00   = replay_start + render_start
video_second = (event_time − video_0_00) / speed
```

Events with `0 ≤ video_second ≤ video_length` become markers.

Because log lines have **one-second** precision, markers can be up to one second early
(the event happened somewhere in that second). In practice this is invisible for chat
subtitles; for frame-exact cuts, use the marker to find the moment and then fine-tune by eye.

## Step by step

### Supported replay formats

| Format | Made by | What `mcsm` finds |
|---|---|---|
| `.mcpr` | ReplayMod (client), ServerReplay (default `replay_mod` encoding) | Finished recordings anywhere in the download |
| `.zip` with `metadata.json` + `c0.flashback` | Flashback (client, saved in `.minecraft/flashback/replays/`), ServerReplay with `default_encoding: flashback` | Zips inside folders whose path mentions *record*, *replay* or *flashback* (so world backups aren't opened), or any zip given with `--replay` |
| Folder with `metaData.json` (`*.tmp`, `*.mcpr.tmp`) | An unfinished ReplayMod/ServerReplay recording (server stopped mid-recording) | Listed as **unfinished** |
| Folder with `metadata.json` + `cN.flashback` | An unfinished Flashback/ServerReplay recording | Listed as **unfinished** |

**Flashback start times.** Flashback's `metadata.json` has no start time, only the length in
ticks (20 per second). The recording itself contains real-clock entries, so `mcsm` reads the
first one from `c0.flashback` and subtracts the ticks recorded before it. That is accurate to
about one tick (50 ms). If a file has no clock entry (very old Flashback versions), the start is
taken from a `YYYY-MM-DD--HH-MM-SS` date in the file name (ServerReplay's naming, read in the
server's time zone). Failing that, it uses the file's modification time minus the length, with a
warning. `mcsm replays` shows `start from file-name` / `start from file-time` when a fallback was used.

Flashback marker times are stored as ticks and converted at 50 ms per tick. If you **paused**
a client-side Flashback recording, the paused time isn't in the recording, so markers after
the pause will be early by the pause length. ServerReplay recordings never pause.

**ServerReplay's Flashback files** (made by its "arcade" library) contain no real-time
clock. Instead they include `arcade_replay_meta.json` with the time the file was **saved** and
any paused time, so `mcsm` uses start = save time − length − paused time
(`start from save-time`). Some ServerReplay 3.4.1 files report a length of 1 tick. Then the
start comes from the file name and the length is estimated as save time − start; the report
marks these as estimates.

ServerReplay names player folders by UUID by default (`player_recording_name: "{uuid}"`);
`mcsm` shows the player's name instead, using `usercache.json`. Custom names such as
`Recordings for: {username} ({uuid})` (the colon becomes `_` on Windows) work too.

### 1. Find the replay

```bash
mcsm replays my-server.zip
```

```
[1] recordings/players/Steve/2024-05-03--18-00-00.mcpr  (ReplayMod)
     start 2024-05-03 20:00:00  length 10:00  players: Steve  events during recording: 5  markers: 1
```

You can refer to a replay by its **number** (`--replay 1`), **part of its path**
(`--replay 18-00-00`) or a **file path** (`--replay C:/Videos/thing.mcpr` or
`--replay C:/Users/me/AppData/Roaming/.minecraft/flashback/replays/Base_tour.zip` – also works
for replays that aren't inside the server folder, e.g. client-side recordings).

### 2. Note your render settings

In ReplayMod's render dialog, note:

- **Start time** of the render inside the replay → `--render-start 12:30` (Flashback shows
  times as ticks in some places: divide by 20 for seconds) (default: 0, the
  beginning). Accepts `90`, `1:30`, `1:02:03`, `1m30s`, or decimals like `12:30.5`.
- **Length** if you didn't render to the end → `--length 5:00` (default: rest of the replay).
- **Speed**, if you rendered a timelapse → `--speed 4` (4× faster).
- **Frame rate** → `--fps 60` (used for EDL timecodes; default 60).

> If you used **keyframes / camera paths**, ReplayMod renders from the first keyframe's
> time; use that as `--render-start`. Time-warp keyframes (speed changes inside the path)
> can't be represented by a single `--speed`; split the video into parts and sync each.

### 3. Generate markers

```bash
mcsm sync my-server.zip --replay 1 --render-start 12:30 --fps 60 -o markers/
```

Choose what to include:

| Option | Example | Default |
|---|---|---|
| `--types` | `death,advancement,chat` or `all` | join, leave, chat, server_chat, emote, death, advancement, replay_marker |
| `--players` | `Steve,Alex` | everyone |
| `--subtitle-seconds` | `3` | 4 |
| `--formats` | `srt,edl` | all formats |
| `--name` | `episode-3` | replay file name |

### 4. Output files

| File | What it is | How to use it |
|---|---|---|
| `NAME.srt` | Subtitles: chat lines, "Steve joined", "Steve: [Diamonds!]"… | Drag onto the timeline / import as captions in any editor. Overlapping subtitles are shortened so the next one starts cleanly. |
| `NAME.edl` | DaVinci Resolve marker list, coloured by event type | Resolve: Media Pool → right-click the timeline → **Timelines → Import → Timeline Markers from EDL…** |
| `NAME.csv` | Table: index, video time, type, player, label, real UTC time | Spreadsheets; the `index` column is what `--music-align "…=#N"` refers to. |
| `NAME.audacity.txt` | Label track | Audacity: **File → Import → Labels…** (great for planning music). |
| `NAME.chapters.txt` | YouTube chapters (≥10 s apart, starting at 0:00) | Paste into the video description. |
| `NAME.json` | All markers + metadata | Your own scripts. |

Marker colours (Resolve names): join = Green, leave = Cream, chat = Blue, server message /
`/me` = Cyan, death = Red, advancement = Yellow, command = Purple, server start/stop =
Fuchsia, crash = Rose, replay marker = Pink, beat = Sky, bar = Lavender.

**Resolve timeline start:** Resolve timelines start at `01:00:00:00` by default, so the EDL
does too. If yours starts at `00:00:00:00`, pass `--edl-start-hour 0`.

**Fractional frame rates** (23.976 / 29.97 / 59.94) use non-drop-frame timecode at the
rounded rate (24 / 30 / 60), which is what Resolve expects for NDF timelines.

## Footage that isn't from a replay (OBS etc.)

Give the real-world time at 0:00 of the video instead of a replay:

```bash
mcsm sync my-server.zip --video-start "2024-05-03 20:00:00" --length 45:00 -o markers/
```

The time is read in **your computer's** time zone unless you pass `--tz`, in which case it
is read in that zone. (OBS can put the start time in the file name: *Settings → Advanced →
Filename Formatting*.)

## Troubleshooting

**"No events fell inside the video"** or markers are off by whole hours
→ the server's time zone is wrong. Run `mcsm summary` and check the *Time zone* line. If
it says "assumed UTC", pass `--tz` with the server's zone (try `--tz UTC` first, then your
host's data-centre zone). The *Files* tab of the report shows the first/last time of each
log file, which helps spot this.

**Markers off by a whole day** → `latest.log` was dated wrongly; pass
`--latest-log-date YYYY-MM-DD` (the date of its *last* line).

**Markers consistently a few seconds off** → ServerReplay starts its recording when the
player is set up, and ReplayMod's timeline may start a moment later. Measure the offset once
on a clear event (a chat message appearing on screen) and add it to `--render-start`
(e.g. `--render-start 12:32` instead of `12:30`).

**Events missing** → only messages listed in [LOG_FORMAT.md](LOG_FORMAT.md) are recognised.
Plugin chat formats (e.g. EssentialsChat `[Rank] Steve: hi`) are not standard and may not be
logged as `<Steve> hi`.
