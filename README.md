# Minecraft Server Master (`mcsm`)

Turn the files you download from your Minecraft server host (PebbleHost or any other)
into:

1. **A statistics dashboard** – one HTML file showing everything that happened over the
   server's life: who played when, play time, deaths, chat, advancements, inventories,
   world info, mods, and every log event, searchable.
2. **Video-editing markers** – line up the **ServerReplay / ReplayMod** recordings with the
   server logs, so chat, deaths, joins and advancements appear as **subtitles and coloured
   timeline markers** in DaVinci Resolve, Premiere, CapCut, Shotcut, Vegas, Kdenlive...
3. **Music sync** – detect a song's beat (or type in its BPM), place it on the video
   timeline, add beat/bar markers, and optionally **snap event markers to the beat** so
   your cuts land on the music.
4. **Raw data exports** – SQLite, JSON and CSV for your own spreadsheets or scripts.

It needs **only Python 3.10+**: no packages to install, no internet, nothing leaves your
computer.

---

## Contents

- [Quick start](#quick-start)
- [Getting the files from PebbleHost](#getting-the-files-from-pebblehost)
- [Commands](#commands)
- [Video sync in 60 seconds](#video-sync-in-60-seconds)
- [Music sync in 60 seconds](#music-sync-in-60-seconds)
- [What gets parsed](#what-gets-parsed)
- [Time zones (read this if markers are off by hours)](#time-zones)
- [Documentation](#documentation)
- [Development](#development)

---

## Quick start

```bash
git clone https://github.com/meztking/Minecraft-Server-Master.git
```

```bash
cd Minecraft-Server-Master
```

Install it as a command (optional – you can also run `python -m mcsm` from this folder):

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
| `world/stats/`, `world/advancements/`, `world/playerdata/`, `world/level.dat` | Lifetime stats, advancement dates, inventories, world info |
| `usercache.json`, `ops.json`, `whitelist.json`, `banned-players.json` | UUID→name, roles |
| `server.properties` | World folder name, settings |
| `recordings/` (ServerReplay) | Replays to sync with video |
| `mods/` or `plugins/` | Mod list (optional) |
| `crash-reports/` | Crash events (optional) |

You **don't** need the region files (`world/region/*.mca`) – they're large and only used
to estimate how much of the world has been explored.

> **Tip:** Minecraft deletes nothing from `logs/` by itself, but some hosts clean old logs
> up. Download `logs/` regularly and merge the folders (file names never clash) to keep the
> full history.

---

## Commands

Every command takes the server folder or zip as its first argument, plus these common options:

| Option | Meaning |
|---|---|
| `--tz Europe/London` / `--tz +02:00` | Time zone the **server** writes its logs in. Normally detected automatically – see [Time zones](#time-zones). |
| `--latest-log-date 2024-05-03` | Date of the last line in `logs/latest.log`, if the file's modification date is wrong. |

| Command | What it does |
|---|---|
| `mcsm summary SERVER` | Overview + player table in the terminal. |
| `mcsm report SERVER -o report.html` | The HTML dashboard. |
| `mcsm export SERVER --sqlite s.db --json s.json --csv out/` | Data exports ([schema](docs/DATA_MODEL.md)). |
| `mcsm events SERVER --type death --player Steve --since 2024-05-01` | Search events in the terminal. |
| `mcsm replays SERVER` | List `.mcpr` recordings with their time range and event counts. |
| `mcsm sync SERVER --replay 1 ...` | Make markers/subtitles for a video ([guide](docs/VIDEO_SYNC.md)). |
| `mcsm music SONG.wav` | Detect a song's BPM and first beat ([guide](docs/MUSIC_SYNC.md)). |

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
| `*.mcpr` anywhere (ServerReplay, ReplayMod) | Recording start time, length, players, in-replay markers |
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
server on the fly – see `tests/fixtures.py` – so no real server files are needed.
