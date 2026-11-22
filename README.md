# Minecraft Server Master (`mcsm`)

Turn the files you download from your Minecraft server host (PebbleHost or any other)
into:

1. **A statistics dashboard** – one HTML file showing everything that happened over the
   server's life: who played when, play time, deaths, chat, advancements, inventories,
   world info, mods, and every log event, searchable.
2. **Video-editing markers** – line up **ReplayMod** (`.mcpr`), **Flashback** (`.zip`) and
   **ServerReplay** recordings with the server logs, so chat, deaths, joins and advancements appear as **subtitles and coloured
   timeline markers** in DaVinci Resolve, Premiere, CapCut, Shotcut, Vegas, Kdenlive...
3. **Music sync** – detect a song's beat (or type in its BPM), place it on the video
   timeline, add beat/bar markers, and optionally **snap event markers to the beat** so
   your cuts land on the music.
4. **Flashback editor integration** – copy a Flashback replay with server events as timeline
   markers, or get a ready-made **activity-aware timelapse** of your base (fixed camera, plays
   only while someone's there, fits 3 minutes) set up in Flashback's editor.
5. **Live 3D map** – watch where everyone is right now, with terrain from your world, trails,
   and click-to-follow. Uses the server's built-in RCON; no mods needed.
6. **Many backups as one history** – give several backups (any versions, e.g. 1.21 and 26.1)
   and get one merged report: every event once, all replays, progress over time per player.
   The **library** keeps all your backups (folders, `.zip`, PebbleHost `.tgz`) and recordings in
   one folder per world, each recording stored once, and loads a whole world with one click.
7. **Raw data exports** – SQLite, JSON and CSV for your own spreadsheets or scripts.

It comes as a **Windows installer** with a normal app window (nothing else to install),
or as a Python package for the command line. Everything runs on your computer: no
internet, nothing is uploaded.

---

## Contents

- [Install on Windows (installer)](#install-on-windows)
- [Quick start (command line)](#quick-start)
- [Getting the files from PebbleHost](#getting-the-files-from-pebblehost)
- [Commands](#commands)
- [Video sync in 60 seconds](#video-sync-in-60-seconds)
- [Music sync in 60 seconds](#music-sync-in-60-seconds)
- [What gets parsed](#what-gets-parsed)
- [Time zones (read this if markers are off by hours)](#time-zones)
- [Documentation](#documentation)
- [Development](#development)

---

## Install on Windows

1. Download `MinecraftServerMaster-Setup-<version>.exe` from the
   **[latest release](https://github.com/MEZTKING/Minecraft-Server-Master/releases/latest)**
   once one is published, or the newest *Build installer* run in the **Actions** tab, under
   *Artifacts* (sign-in required, kept 90 days).
2. Run it. If Windows says *"Windows protected your PC"*, click **More info → Run anyway**:
   the installer works but isn't code-signed.
3. Open **Minecraft Server Master** from the Start menu, click **Zip file…**, and pick your
   server download.

The window has five tabs: **Statistics report**, **Video & music sync**, **Export
data**, **Flashback** and **Live map**. The full guide, including building the installer yourself, is in
**[docs/INSTALLER.md](docs/INSTALLER.md)**.

---

## Quick start

The command line works on Windows, macOS and Linux with Python 3.10+.

```bash
git clone https://github.com/meztking/Minecraft-Server-Master.git
```

```bash
cd Minecraft-Server-Master
```

Install it as a command (optional – you can also run `python -m mcsm` from this folder).
This also installs `mcsm-gui`, which opens the same window as the Windows app:

```bash
pip install .
```

On **Windows**, also install the time-zone database (Windows Python doesn't ship one):

```bash
pip install tzdata
```

Then point it at your downloaded server (a folder **or** the `.zip`):

```bash
mcsm summary path/to/my-server.zip
```

```bash
mcsm report path/to/my-server.zip -o report.html
```

Open `report.html` in your browser.

---

## Getting the files from PebbleHost

See [docs/PEBBLEHOST.md](docs/PEBBLEHOST.md) for step-by-step instructions. In short,
download at least these from the server's file manager / SFTP:

| Path | Needed for |
|---|---|
| `logs/` (all `.log.gz` + `latest.log`) | Timeline, sessions, chat, deaths – **the most important part** |
| `world/stats/`, `world/advancements/`, `world/playerdata/`, `world/level.dat` (Minecraft 26.1+: `world/players/` and `world/data/minecraft/`) | Lifetime stats, advancement dates, inventories, world info |
| `usercache.json`, `ops.json`, `whitelist.json`, `banned-players.json` | UUID→name, roles |
| `server.properties` | World folder name, settings |
| `recordings/` (ServerReplay), or your client's `replay_recordings/` / `flashback/replays/` | Replays to sync with video |
| `mods/` or `plugins/` | Mod list (optional) |
| `crash-reports/` | Crash events (optional) |

You **don't** need the region files (`world/region/*.mca`) – they're large and only used
to estimate how much of the world has been explored.

> **Tip:** Minecraft deletes nothing from `logs/` by itself, but some hosts clean old logs
> up. Download `logs/` regularly and merge the folders (file names never clash) to keep the
> full history.

---

## Commands

Every command takes the server folder or zip as its first argument (**several** to merge
backups into one history; see [docs/BACKUPS.md](docs/BACKUPS.md)), plus these common options:

| Option | Meaning |
|---|---|
| `--tz Europe/London` / `--tz +02:00` | Time zone the **server** writes its logs in. Normally detected automatically – see [Time zones](#time-zones). |
| `--latest-log-date 2024-05-03` | Date of the last line in `logs/latest.log`, if the file's modification date is wrong. |
| `--show-ignored` | Also show the players on the ignore list (see `mcsm ignore`). |

| Command | What it does |
|---|---|
| `mcsm summary SERVER` | Overview + player table in the terminal. |
| `mcsm report SERVER -o report.html` | The HTML dashboard. |
| `mcsm export SERVER --sqlite s.db --json s.json --csv out/` | Data exports ([schema](docs/DATA_MODEL.md)). |
| `mcsm events SERVER --type death --player Steve --since 2024-05-01` | Search events in the terminal. |
| `mcsm video-editor --video FILE.mp4 --world DIR` (or `--edit STATE.json` before exporting) | Live editor in the browser: the video plays with your texts and pictures on it while you change them: any number of texts in the world (in front of the camera, on a spot such as a mountain, or following a player) or on the video, chains of texts, timing, animation, opacity, rotation, 3D letters, pictures, and a song that plays along, goes onto Flashback's audio track with a marker on every beat ([guide](docs/VIDEO_TEXT.md)). |
| `mcsm video-text SERVER --video FILE.mp4` | Timed in-game texts (chat, joins, deaths, advancements, date) for a video exported from Flashback, lined up automatically from your Flashback edit; styled `.ass` + `.srt` ([guide](docs/VIDEO_TEXT.md)). |
| `mcsm world-text --replay REC.zip --text "..." --in-front-at SECONDS` | Real 3D text in the world (lit, hidden behind blocks) in a copy of a Flashback recording; export again in Flashback ([guide](docs/VIDEO_TEXT.md#text-in-the-world-real-3d-text)). |
| `mcsm changes SERVER MORE...` | Minecraft version, mods, plugins and datapacks changed between backups ([guide](docs/BACKUPS.md#versions-mods-and-datapacks-over-time)). |
| `mcsm replays SERVER` | List `.mcpr` recordings with their time range and event counts. |
| `mcsm sync SERVER --replay 1 ...` | Make markers/subtitles for a video ([guide](docs/VIDEO_SYNC.md)). |
| `mcsm story [WORLD]` | The whole server's story, start to finish: the history viewer plays the important moments of every backup and recording in order ([guide](docs/HISTORY.md#the-server-story-start-to-finish)). |
| `mcsm flashback-link status` / `install --instance NAME` | The Flashback companion mod: the editor's real Minecraft picture ([guide](docs/VIDEO_TEXT.md), [mod](companion/README.md)). |
| `mcsm music SONG.wav` | Detect a song's BPM and first beat ([guide](docs/MUSIC_SYNC.md)). |
| `mcsm ignore list` / `add NAME...` / `remove NAME...` | Players who were never supposed to join: left out everywhere (reports, events, moments, the story, highlights, timelapses, the viewers' maps). One list for all servers, kept in `%LOCALAPPDATA%\mcsm\ignored_players.json`, by name and UUID (renames stay ignored). Your backups and recordings are not changed; taking someone off the list brings them back the next time you load. In the app: **Ignored players…** next to **Load server**. |
| `mcsm timelapse-join PART1.mp4 PART2.mp4 ... -o OUT.mp4 [--music SONG]` | Join the videos exported from a build timelapse's parts into one, in order, with a song if you like ([guide](docs/HISTORY.md)). |
| `mcsm flashback SERVER --replay N --install` | Copy a Flashback replay with server events as markers ([guide](docs/FLASHBACK.md)). |
| `mcsm timelapse SERVER --replay N --area X1,Z1,X2,Z2 --tracks DIR --install` | Activity-aware area timelapse set up in Flashback ([guide](docs/TIMELAPSE.md)). |
| `mcsm live SERVER --host ADDRESS` | Live 3D map of players in your browser ([guide](docs/LIVE_MAP.md)). Add `--demo` to try it without a server. |
| `mcsm library import-all FOLDER` / `scan` / `import` / `list` / `add-recordings` | Keep all backups (any host, also world-only downloads and singleplayer saves) and recordings in one library folder, one folder per world, duplicates stored once; `import-all` sorts a whole messy folder after showing a plan; then give the world's folder as SERVER to any command ([guide](docs/LIBRARY.md)). |
| `mcsm history SERVER [MORE...]` | 3D time machine: a time slider over the whole history, with player paths from Flashback recordings, events, sessions and replay coverage. The map shows **each backup's world at its time**, so you can watch builds appear. Mark **highlights for videos** and export them to Flashback with markers and ready-made export ranges ([guide](docs/HISTORY.md)). |

Run `mcsm <command> --help` for every option.

---

## Video sync in 60 seconds

1. List the replays and pick one:

   ```bash
   mcsm replays my-server.zip
   ```

2. In ReplayMod, note where your render **starts** inside the replay (say `12:30`).

3. Generate the markers (60 fps video):

   ```bash
   mcsm sync my-server.zip --replay 1 --render-start 12:30 --fps 60 -o markers/
   ```

4. Import into your editor:
   - **DaVinci Resolve:** Media Pool → right-click your timeline → *Timelines → Import →
     Timeline Markers from EDL…* → pick `markers/<name>.edl`. Then drag
     `markers/<name>.srt` onto the timeline for chat subtitles.
   - **Premiere / CapCut / Shotcut / Vegas / Kdenlive:** import the `.srt` as captions.
   - **YouTube:** paste `<name>.chapters.txt` into the description.

Full explanation, including timelapses (`--speed`), OBS footage (`--video-start`) and
troubleshooting: **[docs/VIDEO_SYNC.md](docs/VIDEO_SYNC.md)**.

---

## Music sync in 60 seconds

```bash
mcsm sync my-server.zip --replay 1 --render-start 12:30 \
    --music song.wav --music-align "1:02.5=#12" --snap beat -o markers/
```

- `--music song.wav` detects the tempo and beat positions (WAV works out of the box; MP3/OGG/etc.
  need [ffmpeg](https://ffmpeg.org) installed – or skip detection with `--bpm 128 --beat-offset 0.42`).
- `--music-align "1:02.5=#12"` places the song so that **1:02.5 into the song** (say, the drop)
  plays exactly at **marker #12** (see the `index` column of the CSV). The tool prints where to
  put the song on your timeline.
- `--snap beat` moves every event marker to the nearest beat (use `bar` for bigger cuts).
- Bar markers are written alongside (`<name>.beats.edl` / `.csv` / `.audacity.txt`).

Full explanation: **[docs/MUSIC_SYNC.md](docs/MUSIC_SYNC.md)**.

---

## What gets parsed

| Source | Gives you |
|---|---|
| `logs/*.log.gz`, `logs/latest.log` (Vanilla, Fabric, Paper/Spigot/Purpur, Forge/NeoForge) | Server starts/stops, joins/leaves (+ join position), chat, `/say`, `/me`, deaths (with killer & weapon), advancements, commands, and **play sessions** built from them |
| `world/stats/*.json` (1.13+ and older format) | Every lifetime statistic – play time, blocks mined, items crafted, mobs killed, distance travelled… |
| `world/advancements/*.json` | Every advancement with its completion time (also used to detect the server time zone) |
| `world/playerdata/*.dat` (NBT) | Last position & dimension, health, food, XP, game mode, last death location, inventory, ender chest |
| `world/level.dat` (NBT) | World name, seed, version, world age, in-game days, difficulty, game rules, dragon killed |
| `world/region`, `DIM-1`, `DIM1`, `dimensions/` | Region file count / size per dimension (≈ explored area) |
| `usercache.json`, `ops.json`, `whitelist.json`, `banned-players.json` | Names, op / whitelist / ban status |
| `server.properties` | Settings (the RCON password is masked) |
| `*.mcpr` anywhere (ReplayMod, ServerReplay) | Recording start time, length, players, in-replay markers |
| Flashback `.zip` replays and unfinished recording folders (Flashback mod, ServerReplay with `flashback` encoding) | Start time (decoded from the recording's real-time clock), length, markers |
| `crash-reports/` | Crash events |
| `mods/*.jar`, `plugins/*.jar` | Mod/plugin names and versions |

Players' **IP addresses** in the logs are never stored or exported.

Exact patterns and limitations: [docs/LOG_FORMAT.md](docs/LOG_FORMAT.md).

---

## Time zones

Log lines only contain a clock time like `[20:15:03]`, in the **server's** time zone –
which is often not yours (PebbleHost machines usually run on UTC or the data-centre's local
time). Replays store exact UTC timestamps. If the two are not converted correctly, markers
end up hours off.

`mcsm` solves this automatically: advancement files store timestamps **with** the server's
UTC offset (`2024-05-01 20:15:03 +0200`), so it learns the offset per date (handling
daylight-saving changes). `mcsm summary` prints what it found, e.g.
`Time zone : learned from advancement files (UTC offsets seen: UTC+02:00)`.

If nobody has earned an advancement yet, logs are assumed to be in UTC and you'll see a
warning – pass `--tz` (e.g. `--tz Europe/Amsterdam` or `--tz +01:00`) to fix it.

The HTML report always shows times in **your** browser's time zone.

---

## Documentation

| Document | Contents |
|---|---|
| [docs/BACKUPS.md](docs/BACKUPS.md) | Merging several backups (any versions) into one history; big-backup handling |
| [docs/FLASHBACK.md](docs/FLASHBACK.md) | Markers into Flashback replays; the exact files written; launcher folders |
| [docs/TIMELAPSE.md](docs/TIMELAPSE.md) | Activity-aware timelapse: what you need, options, outputs |
| [docs/LIVE_MAP.md](docs/LIVE_MAP.md) | Live 3D map: enabling RCON, starting the map, security, troubleshooting |
| [docs/VIDEO_TEXT.md](docs/VIDEO_TEXT.md) | In-game texts on finished Flashback videos without editing skills |
| [docs/LIBRARY.md](docs/LIBRARY.md) | The library: importing backups (.tgz/.zip/folders) and recordings into one place, link/copy/move |
| [docs/HISTORY.md](docs/HISTORY.md) | History viewer: the whole history as one recording, timeline, where positions come from |
| [HANDOFF.md](HANDOFF.md) | Everything needed to continue development in a new session or account |
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | What's done, what's planned (incl. timelapse design), decisions – the hand-off doc |
| [docs/INSTALLER.md](docs/INSTALLER.md) | Installing and using the Windows app; how the installer is built and released |
| [docs/PEBBLEHOST.md](docs/PEBBLEHOST.md) | Downloading the right files from PebbleHost (and other hosts) |
| [docs/VIDEO_SYNC.md](docs/VIDEO_SYNC.md) | How replay ↔ log syncing works, all options, editor import steps, troubleshooting |
| [docs/MUSIC_SYNC.md](docs/MUSIC_SYNC.md) | Beat detection, beat grids, alignment, snapping, worked examples |
| [docs/LOG_FORMAT.md](docs/LOG_FORMAT.md) | Every log line pattern recognised, log dating rules, session rules, known limitations |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Event types, `ServerData` fields, SQLite schema, JSON/CSV layout, stat units |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Code layout, data flow, how to add a new parser or export format |

---

## Development

```bash
python -m pytest tests
```

(or `python -m unittest discover tests` without pytest). The tests build a complete fake
server on the fly – see `tests/fixtures.py` – so no real server files are needed. The GUI
test runs only where tkinter and a display are available.

Build the Windows installer with `installer\build_windows.ps1` (see
[docs/INSTALLER.md](docs/INSTALLER.md)); GitHub Actions does this automatically on every push.
