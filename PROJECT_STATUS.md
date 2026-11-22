# Project status and roadmap

**This is the hand-off document.** Any session (human or Claude) picking up this project
should read it first and must **update it after every completed change** (see
`CLAUDE.md`). Last updated: 2026-11-22 (library safety fixes after review; versions/mods/datapacks over time; optional tidy).

## What this project is

`mcsm` (Minecraft Server Master) is a desktop app plus command-line tool for the owner of a
small Minecraft server for friends, hosted on PebbleHost. It:

1. parses a downloaded server (folder or `.zip`) into statistics and an event timeline;
2. syncs those events with replay videos (ServerReplay / ReplayMod / Flashback) as markers
   and subtitles for video editing, including syncing music to the beat;
3. shows a live 3D map of players in real time.

The owner edits videos (DaVinci Resolve etc.) on **Windows** and wants an easy installer.
More features "like these" are planned, listed under **Backlog** below.

## Downloads

- **Latest release (installer):** https://github.com/MEZTKING/Minecraft-Server-Master/releases/latest
  — **no release published yet.** Claude sessions can push only the working branch (tag pushes
  are rejected), so the owner publishes releases on GitHub: *Releases → Draft a new release →
  tag `v0.1.0` (create on publish), target `claude/exciting-cannon-d0r8dm` → Publish*. The
  workflow then builds the installer and attaches it (check the Actions tab).
- Every push also builds an installer artifact under Actions → Build installer (sign-in required,
  kept 90 days).

## How to work on it

- Branch: `claude/exciting-cannon-d0r8dm` (push here; no PRs unless asked).
- Tests: `python -m pytest tests`. GUI tests need tkinter and a display:
  `xvfb-run -a python3 -m unittest tests.test_mcsm`.
- Python 3.10+, **standard library only** at runtime. three.js is vendored in
  `mcsm/assets/web/vendor/`.
- Releases: bump `__version__` (`mcsm/__init__.py`) and `version` (`pyproject.toml`), update the
  changelog here, push; then the owner publishes a GitHub Release (see Downloads) and the
  workflow attaches the installer. Released so far: none. Current version: **0.2.0** (2026-11-22);
  builds are stamped `0.2.0 (build N, commit, date)` (see backlog #0l).
- Windows installer: built by GitHub Actions on every push (`.github/workflows/build-installer.yml`,
  see `docs/INSTALLER.md`). Check the run after pushing.
- Docs live in `README.md` and `docs/`. Code layout: `docs/ARCHITECTURE.md`.

## Feature status

| Feature | Status | Where | Docs |
|---|---|---|---|
| Log parsing (Vanilla/Fabric/Paper/Forge), sessions, deaths, chat, advancements | ✅ Done | `mcsm/logs.py` | `docs/LOG_FORMAT.md` |
| Stats, advancements, playerdata/level.dat NBT, mods, world size | ✅ Done | `mcsm/players.py`, `world.py`, `nbt.py` | `docs/DATA_MODEL.md` |
| Time-zone detection from advancement files | ✅ Done | `mcsm/timeutil.py` | README "Time zones" |
| HTML statistics dashboard | ✅ Done | `mcsm/exporters/report.py` | README |
| JSON / SQLite / CSV export | ✅ Done | `mcsm/exporters/data.py` | `docs/DATA_MODEL.md` |
| Replay ↔ log sync: SRT, Resolve EDL, CSV, Audacity, YouTube chapters | ✅ Done | `mcsm/sync.py`, `exporters/markers.py` | `docs/VIDEO_SYNC.md` |
| ReplayMod `.mcpr` (+ unfinished `.tmp` folders) | ✅ Done | `mcsm/replays.py` | `docs/VIDEO_SYNC.md` |
| Flashback `.zip` (+ unfinished folders), start time from the real-time-clock action | ✅ Done | `mcsm/replays.py` | `docs/VIDEO_SYNC.md` |
| Music sync: beat detection, beat grid, alignment, snapping | ✅ Done | `mcsm/music.py` | `docs/MUSIC_SYNC.md` |
| Desktop app (tkinter) | ✅ Done | `mcsm/gui.py` | `docs/INSTALLER.md` |
| Windows installer (PyInstaller + Inno Setup, CI build + install test) | ✅ Done | `installer/`, `.github/workflows/` | `docs/INSTALLER.md` |
| Live 3D map over RCON (terrain from download, trails, follow, demo mode, track recording) | ✅ Done, not yet tried on a real server | `mcsm/live.py`, `rcon.py`, `terrain.py`, `assets/web/live.html` | `docs/LIVE_MAP.md` |
| Flashback markers: replay copy with server events in its timeline | ✅ Done, not yet tried in a real Flashback | `mcsm/flashback.py`, `mcsm flashback`, app Flashback tab | `docs/FLASHBACK.md` |
| Activity-aware area timelapse in Flashback (Phase A): fixed camera + timelapse keyframes, 3:00 target or fixed speed, date/time SRT, chapters | ✅ Done, not yet tried in a real Flashback | `mcsm/timelapse.py`, `mcsm timelapse`, app Flashback tab | `docs/TIMELAPSE.md` |
| Merge many backups (any versions) into one history; Backups tab with progress over time; server-version timeline | ✅ Done, not yet tried on the owner's backups | `mcsm/archive.py`, all commands, app *+ Add backup* | `docs/BACKUPS.md` |
| Selective zip reading (no region unpacking; replay stubs) | ✅ Done | `mcsm/source.py` | `docs/BACKUPS.md` |
| "Everything as one recording" 3D time-machine map (Phase 2a): time slider, recorded player's path from Flashback recordings (cached), log-in positions, events pinned in 3D, sessions + replay coverage timeline, playback, app History tab | ✅ Done, checked on the owner's real backup (216 recordings) | `mcsm/paths.py`, `history.py`, `assets/web/history.html`, `mcsm history`, app History tab | `docs/HISTORY.md` |
| History viewer terrain per backup: the map shows the world as it was at the time on the slider (next backup saved at or after it), or one pinned backup; unchanged regions shared | ✅ Done, checked on the owner's May + November backups | `mcsm/live.py` (`TerrainServer` layers), `history.py`, `assets/web/history.html`, `--terrain`, app checkbox | `docs/HISTORY.md` |
| Highlights for videos: mark spans in the history viewer; export Flashback copies with start/end markers + one export-range scene per highlight, and a CSV clip list | ✅ Done, not yet tried in a real Flashback | `mcsm/highlights.py`, `history.py`, `assets/web/history.html` | `docs/HISTORY.md` |
| Library: one folder per world for all backups (folders, .zip, PebbleHost .tgz) and recordings (each stored once); link/copy/move; unchanged files hard-linked; a world folder works as SERVER everywhere; app Library tab | ✅ Done, checked on the owner's May + November backups and the May .tgz | `mcsm/library.py`, `archive.py` (recordings), `mcsm library`, app Library tab | `docs/LIBRARY.md` |
| Import a whole folder of backups from any host: scan + plan (one copy per backup, recordings, ignored/unsupported files), world-only backups (Aternos/Realms/singleplayer) | ✅ Done, scan checked on the owner's 600 GB backup folder | `mcsm/library.py` (`scan`, `import_all`), `mcsm library scan/import-all`, app *Import a whole folder…* | `docs/LIBRARY.md` |
| Minecraft versions, mods, plugins and datapacks over time: what changed between backups (`mcsm changes`, report Backups tab, app button, history viewer "Server at this moment"); each recording's Minecraft version | ✅ Done, checked on the owner's May + November backups | `mcsm/archive.py` (`changes`), `cli.py`, `report.py`, `history.html` | `docs/BACKUPS.md`, `docs/HISTORY.md` |
| Optional tidy of the original backup folder: duplicates moved aside (never deleted), optional dated names, undo | ✅ Done (tested on fixtures; not run on the owner's folder) | `mcsm/library.py` (`tidy_*`), `mcsm library tidy`, app *Tidy the original folder…* | `docs/LIBRARY.md` |
| Flashback in launcher instances (PrismLauncher, MultiMC, Modrinth App, CurseForge) with version matching; video projects in the library (recordings + a copy of the Flashback edits) | ✅ Done, detection checked on the owner's PC | `mcsm/flashback.py`, `mcsm/library.py` (`add_projects`), `mcsm library add-projects/projects`, app *Add video projects…* | `docs/LIBRARY.md`, `docs/FLASHBACK.md`, `docs/HISTORY.md` |
| Text on finished videos (Phase 1): Flashback scene found from the video length, video time -> server time (timelapse included), suggested texts, styled .ass + .srt | ✅ Done, checked on the owner's "Thanks for playing" intro (length match 0.03 s; advancement timing confirmed on screen) | `mcsm/videotext.py`, `mcsm video-text` | `docs/VIDEO_TEXT.md` |
| Real in-world text in a copy of a Flashback recording (text display entities in every chunk snapshot; place at X,Y,Z or in front of the edit's camera); on-screen 3D titles shown as examples | ✅ Done, packets byte-identical to the game's own (26.1.2/26.2/26.3); works in the owner's Flashback 26.1.2 (in the world, hidden by players walking past) | `mcsm/worldtext.py`, `mcsm world-text` | `docs/VIDEO_TEXT.md` |
| App Videos tab: pick a finished video -> edit (also when changed after export) + recording + still ending found; big/small title in the world (Flashback copy + edit with the video's export range) or burned onto a copy (Minecraft Ten font, black border or 3D; hardware encoder when it really works); preview | ✅ Done; checked on the owner's welcome clip (edit found 0.2 s, camera still from 57.65 s) | `mcsm/videotext.py` (`analyse_video`, `make_in_world`, `make_on_screen`), `worldtext.py` (border), `library.recording_uuids`, `gui.py` | `docs/VIDEO_TEXT.md` |
| Animated text: fade, pop, words one by one (instant/fade/pop), fade out; border colour, shadow, glow, background box; in the world and on the video | ✅ Done; on-screen demos on the owner's clip; in-world animations not yet seen in Flashback | `mcsm/worldtext.py`, `videotext.py`, Videos tab | `docs/VIDEO_TEXT.md` |
| Live text editor page for finished videos (play, scrub, change text/timing/animation/look/place live; make from the page) | ✅ Done, tried on the owner's welcome clip | `mcsm/videoeditor.py`, `assets/web/videotext.html`, `mcsm video-editor`, Videos tab | `docs/VIDEO_TEXT.md` |
| In-app updater: test builds (public `latest-build` pre-release) or stable releases; startup check once a day; SHA-256-verified download; silent install that reopens the app | ✅ Done; first end-to-end update not yet tried | `mcsm/updater.py`, `gui.py`, `.github/workflows/build-installer.yml`, `installer/mcsm.iss` | `docs/INSTALLER.md` |
| Timelapse Phase B (block-by-block from world snapshots) / Phase C (from replay packets) | 📝 Designed | n/a | Backlog #1 |

## Backlog (feature requests and ideas, in priority order)

### 0. Real-server findings (owner's report, 2026-11-19) — fix first

The owner ran the report on their real server (**Minecraft 26.1.2, Fabric, ServerReplay 3.4.1,
11 players, 266 logs, 762 Flashback replays, 14.7 GB world**). Logs parsed well (7,595 events).
Problems found:

1. ✅ *Fixed (needs owner re-check)* — **Stats, advancements and playerdata all empty.** Minecraft 26.1 moved these out of
   `<world>/stats|advancements|playerdata` (dimensions also moved to
   `<world>/dimensions/minecraft/<dim>/region`). Fix: auto-discover these folders anywhere in the
   world (uuid-named files), so future layout changes keep working. Same for terrain region dirs.
2. ✅ *Fixed via 1 (needs owner re-check)* — **Time zone unknown** (follows from 1: offsets come from advancement files). Also consider
   other offset sources.
3. 🟡 *Partly fixed* — **All Flashback replays: duration 50 ms, start only from the file name, no markers.**
   Research: ServerReplay writes Flashback via the arcade library (CasualChampionships/arcade
   `io/writer/flashback`). It writes **no real-time clock**, but adds `arcade_replay_meta.json`
   with `epoch_time_ms` (save time) and `accumulated_paused_time`. Now:
   start = save − length − paused (`start_source: save-time`). If the length is implausible
   (≤ 1 tick), the start comes from the file name and the length is estimated as save − start.
   The arcade source looks correct, so **why** the length is 1 tick is still unknown: a sample
   is needed.
   ServerReplay 3.4.1's Flashback output differs from what Flashback master writes. Needs a sample
   replay (`metadata.json` + `c0.flashback`) from the owner → fix the reader.
   **2026-11-21, checked on the real backup:** all 216 replays there come from ServerReplay
   **3.5.2+26.2 (40) and 3.6.0+26.3 (176)** and are healthy: `total_ticks` = sum of chunk
   durations = number of `next_tick` actions, no 1-tick lengths, and start (save − length −
   paused) lands 3–17 s after the file-name time (the file name is UTC on this server). So the
   50 ms problem belonged to **3.4.1** recordings (the old report); newer ServerReplay versions
   don't have it.
   **Root cause found (2026-11-21, `May19_2026` backup, 762 replays from 3.4.0+26.1 / 3.4.1+26.1):**
   every file has `total_ticks` 1 and one chunk. Its `c0.flashback` holds only `game_packet` (and
   `create_local_player`) actions: **no `next_tick`, no `move_entities`, an empty snapshot, no
   chunk cache**. ServerReplay 3.4.x never wrote the timeline, so the data can't be recovered:
   these recordings have no timing and won't play properly in Flashback. All 762 total 39 MB (a
   10-minute recording is 36 KiB; a 7-minute 3.6.0 one is 45 MB). mcsm's estimate (save − file
   name) is the best available and matches the server log exactly (e.g. 10:25 for
   `2026-05-19--09-57-42`). `mcsm flashback` / `timelapse` now warn when given such a recording.
   ✅ Closed.
4. ✅ *Fixed* — Player folders are named `Recordings for_ NAME (uuid)` (custom `player_recording_name`):
   extract the uuid/name.
5. ✅ *Mostly fixed* — `level.dat` in 26.1: spawn (`spawn.pos`), difficulty (`difficulty_settings`),
   seed/game rules/weather (`data/minecraft/*.dat`) now read. Day time's new location is not
   verified (left unknown).
6. ✅ *Fixed (2026-11-21, checked on the owner's real backup)*: the two players without uuid
   (`5ou1s`, `InsidiousCrow`) were **old names** of Vakirath and Crow_Shroom. The logs'
   `UUID of player NAME is …` lines prove it. Each person was split in two: stats under the new name,
   the old name's sessions/chat under a second row with 0:00 play time. Fix: join events carry
   the uuid; every logged name maps to the uuid's current name (usercache, else newest name
   seen; `formerly known as` also links names). Multi-backup merges also follow a uuid to its
   newer name. Result on the real backup: Vakirath 289 → 407 sessions, Crow_Shroom 134 → 156.
7. ✅ *Fixed* — Whole-zip extraction does not scale (14.7 GB world): zips are now read selectively
   (no region files; replays as metadata stubs, full replay extracted on demand).
8. Owner asked about versions ("I have multiple server jars"): the logs show **1.21.5 (Apr–Nov
   2025) → 1.21.10 → 1.21.11 → 26.1.2 (Apr 2026)**. `level.dat` only has the last one. The report
   now shows a **Server versions** table from the logs, and both folder layouts are read.

### 0b. Unified multi-backup archive + recordings map (requested 2026-11-19)

**The ask (owner's words):** "make a system so that i can see and combine multiple minecraft
backups with different version with their recording to make one unified glorious section where
i can easily view edit and anaylze anything. also how are you meant to view recordings etc, and
make a live map using the recording files, to do the same thing as above so i can see everything
in my backup file as if its one recording"

**Proposed design:**
- ✅ **Phase 1 done** (`mcsm/archive.py`, docs/BACKUPS.md): pass several backups to any command or
  select several in the app; Backups tab with progress per player. Original design notes:
- **Phase 1: merge backups into one dataset/report** (`mcsm report BACKUP1 BACKUP2 ...`, and an
  app list of backups):
  - read each backup (zip or folder, any version) *selectively*: logs, player files, level.dat,
    replay metadata only; never unpack region files;
  - logs: union, de-duplicated by (time, type, player, text), since the same archive appears in
    several backups and an old `latest.log` reappears later as a dated archive;
  - per-player stats/advancements/inventory: newest backup wins, and **each backup is kept as a
    snapshot**, giving progress over time (play time, blocks mined… per backup date);
  - replays: union, de-duplicated by replay uuid / file name;
  - a **Backups tab**: timeline of backups with game version, world size, seed and level name (a
    new world = new section), plus version changes.
- **Research for Phase 2 (2026-11-19):** Flashback chunk files have a version-independent
  `flashback:action/move_entities` action (per dimension: entity id, x/y/z doubles, yaw/pitch/
  head-yaw floats, on-ground; only *changed* entities per tick, so carry values forward).
  ServerReplay's writer (arcade `FlashbackWriter`) emits `create_local_player` (uuid, x/y/z,
  rotation, velocity, game profile, game mode: no entity id) and move_entities, but **no
  real-time clock**. Mapping entity id → player needs either the version-specific add-entity
  game packet, or a heuristic (the local player is the entity whose first position equals the
  create_local_player position). The arcade `ExactEntityPosition.write` layout is not yet
  verified. **A real replay sample from the owner is needed** to confirm before building.
- ✅ **Phase 2a done (2026-11-21)**: `mcsm history` / app History tab (docs/HISTORY.md).
  Recorded player's path decoded from every Flashback recording (`move_entities`; entity found
  by matching `create_local_player`), cached as `.jsonl` in the live-map track format; time
  slider + playback (skip quiet time, prev/next log-in); markers solid when recorded, faded at
  last known position (path or log-in) while online; trails; event pins at the player's
  position; "Recordings at this moment" with the offset to seek to in Flashback; timeline with
  online/recording rows, deaths, backups. Terrain from the newest backup, built near spawn and
  log-in spots first. On the real backup: 216 recordings in a few minutes, ~100k points.
  **Phase 2b, other players: done 2026-11-21.** Players in view of each recording are
  tracked via the add-entity game packet (id 1 in 26.2/26.3; matched by player UUID, so no
  entity-type table) incl. the chunk snapshot, and remove-entities (77 in 26.2, 78 in 26.3;
  unknown versions: stop 1 min after the last move). On the real backup: ~50,000 extra points
  for 6 players across 216 recordings (3.5 min); vs. the same players' own recordings at
  47,626 moments the median gap is 1.2 blocks, 99.9% within 20 (rest = teleports).
  **Terrain per backup: done 2026-11-22** (one terrain layer per backup; see changelog).
  **Still to do (older list):** terrain per backup (see the world change); terrain speed
  for big worlds (1,569 regions take well over an hour; cache tiles or a faster reader);
  "edit" (the owner's open question); open Flashback at the moment directly; Nether surface.
  (Terrain speed: done 2026-11-21, 1,569 regions in ~30 s, ~4 s cached.)
- **Phase 2: "everything as one recording" 3D time-machine viewer**: a time slider across
  the whole history. Player paths, events pinned in 3D, sessions, replays as coverage bars. Click
  to open or sync the replay at that moment. Terrain from the nearest backup (switching backups
  shows the world changing). Position sources, in order:
  - live-map track recordings (exact, exists);
  - **positions decoded from the replay recordings** (needs packet decoding; per-protocol,
    research);
  - coarse points from logs (join positions, death locations) and playerdata per backup.
- **How to view recordings** (answer given): the replays themselves play inside Flashback /
  ReplayMod in game; `mcsm` lines them up with logs, adds markers, and (Phase 2) shows where and
  when they happened on the map.
- Open questions: what "edit" should mean in the unified view (notes and annotations? marking
  highlights for video? merging/renaming players?); how many backups and what size.

### 0c. Highlights for videos in the history viewer (requested 2026-11-21)

**The ask:** the owner's answer to "what should *edit* mean in the unified view": **"Highlights
for videos"**.

**Design:**
- In the history viewer: **Mark start** / **Mark end** (keys `I` / `O`, as in video editors)
  at the current time, a title, **Save**. The players online in that span are filled in. Saved
  highlights are listed (jump to, play just that span, rename, delete) and drawn as bands on
  the timeline.
- Stored as JSON per world in `Documents\mcsm-highlights\<world>.json` (`--highlights FILE`),
  so they survive restarts, can be backed up, and don't depend on which backups are loaded.
- **Export to Flashback:** for every Flashback recording that covers a highlight (one per
  player's point of view), one copy of the replay (new uuid, `… (highlights)`) with a green start
  and a red end marker per highlight, plus an editor timeline with **one scene per highlight
  whose export range is that highlight**. In Flashback: open the copy, pick the scene, Export.
  Installed into Flashback's `replays/` and `editor_states/` when the folder is found;
  otherwise written to the highlights folder.
- Also a clip list (`<world> highlights.csv`): title, local start/end, length, players, and
  each covering recording with the in/out offsets. Works with any editor or a ReplayMod workflow.
- Safety: the page can only change highlights through POST requests that carry a custom
  header (so other websites can't); originals are never modified; editor states are never
  overwritten.

✅ **Done 2026-11-21** as designed (`mcsm/highlights.py`, viewer panel, `--highlights`,
`--flashback-dir`). Tried on the real backup: a 3-minute highlight at the base on 18 Nov
listed its 5 players and 5 recordings; export wrote 5 valid replay copies (same entries and
data, start/end markers 3,600 ticks apart, one scene with that export range each) and the clip
list. Writing it also uncovered the 26.3 `System chat:` log prefix (see changelog).

**Open questions:** does Flashback accept a scene with no keyframe tracks (only an export
range)? (Source suggests yes; to be confirmed in-game.) Should highlights also become markers
in `mcsm sync` video exports (SRT/EDL)?

### 0d. One library for all saves and recordings (requested 2026-11-22)

**The ask:** "i have other saves too and more recordings, they should be saved in one place to
make it more organized".

**Design:**
- A **library folder** (default `Documents\mcsm-library`, `--library DIR`), one folder per world
  (level name + seed), each backup in its own dated folder, every recording stored **once**:
  ```
  mcsm-library/
    <World>/
      world.json
      backups/2026-05-19 21-04/   server files (without recordings) + mcsm-backup.json
      recordings/recordings/players/<uuid>/<date>.zip   union of all backups' recordings
      recordings/imported/…       loose recordings added by hand (e.g. Flashback client replays)
      highlights.json
  ```
- `mcsm library import SOURCE…` takes a folder, `.zip` or **PebbleHost `.tgz`**. Archives are
  unpacked once, straight into the library. Modes: **link** (default on the same drive:
  hard links, so it's instant and uses no extra space, and the originals can be deleted
  afterwards), **copy**, **move**. A file identical to one in another backup of the same world
  is hard-linked, not stored twice (May and November shared 595 region files). Importing the
  same backup again is skipped.
- `mcsm library add-recordings FILE_OR_DIR… --world NAME`, `mcsm library list`.
- Every command accepts a library world folder as SERVER: it loads all of that world's
  backups plus its recordings. Highlights default to `<World>/highlights.json`.
- App: a **Library** tab (folder, import buttons, mode, list of worlds and backups, **Load this
  world**).

✅ **Done 2026-11-22** as designed. Checked on the owner's backups: May (14.5 GB) and November
(39.2 GB) folders imported in 15 s with no extra space (link mode), 978 recordings in one place,
2,847 files shared; loading the library world gives the same history as both originals
(10,583 events, 978 replays), and the history viewer reuses the path cache. The real May `.tgz`
(13.7 GB) imports directly in about 40 s with the same 762 recordings and logs as the owner's
unpacked folder (see changelog for the `Recordings for:` colon fix).

**Open questions:** do "other saves" include singleplayer worlds (`.minecraft/saves`)? They
have no logs, so they would only add terrain. Should Flashback's own replay folder be imported
automatically?

### 0e. Import a whole folder of backups, from any host (requested 2026-11-22)

**The ask:** "You should make a feature so importing it like this so other users can do the same,
i dont know if you need to consider taking a backup from which hosting company".

**Design:**
- `mcsm library scan FOLDER` shows a plan without changing anything; `mcsm library import-all
  FOLDER` runs it; app: **Import a whole folder…** (scan, show the plan, ask, import).
- Found by **content, not host name**: a server root (`server.properties`, `logs/`, …), a
  world-only folder (`level.dat` + `region/`: Aternos/Realms downloads, singleplayer saves), and
  archives (`.zip` listed cheaply; `.tgz`/`.tar.gz`/`.tar` by name, checked when unpacked).
- **One copy per backup:** an unpacked folder next to (or around) its archive, a `.tar` next to
  its `.tgz`, and identical archives (same size + same first/last MiB) are one backup; the fastest
  copy is used (folder, then `.tar`, then compressed). Importing still skips a backup whose time
  is already in the library, as a safety net.
- Recording folders that aren't inside a backup are added as recordings; videos, mods and other
  files are ignored and counted; `.7z`/`.rar` are listed as "unpack first".
- World-only backups go into `backups/<date>/<world>/` with a generated `server.properties`
  (`level-name`) so every tool works on them.

✅ **Done 2026-11-22** as designed. On the owner's `F:\Game Backup\Minecraft Divers` (about
600 GB) the scan found 15 backups, skipped 5 copies (3 archives next to their unpacked folder,
June 9's `.tar` next to its `.tgz`, and an identical July 4 `.tgz` in a second folder), found
1,980 loose recordings, ignored 4 videos + 4 `.jar` (35 GB), and flagged the `.7z`.

### 0f. Minecraft versions, mods and datapacks over time (raised 2026-11-22)

**The ask:** "remember that a few different versions of Minecraft was used aswell as a few
different mods and some datapacks, each were removed updated changed at random times, so these
can affect our work".

**How it affects the tools:**
- File layouts moved between versions (26.1 moved players and dimensions): the parsers handle
  every layout; each backup is read on its own.
- ServerReplay versions differ: 3.4.0/3.4.1 recorded no movement (no paths for those weeks).
- Flashback can only open a recording in the Minecraft version it was made with, so a
  highlight from May needs that version's Minecraft + Flashback.
- Mods change log lines (26.3 `System chat:`), add blocks the terrain colours don't know (drawn
  grey) and add stats; datapacks add advancements and death messages.

**Design:**
- Keep each backup's mod list (id, name, version, loader) and datapacks (enabled list from
  `level.dat` plus the `datapacks/` folder) in `ServerData.backups`.
- `archive.changes()`: compare backups in order and list what changed between each pair:
  Minecraft version, loader, mods added/removed/updated, datapacks added/removed. A change is
  dated "between backup A and backup B" (the exact day is not in the files).
- Show it: a "What changed" table in the report's Backups tab, `mcsm changes SERVER…` in the
  terminal, and in the history viewer the change list at each backup line plus the Minecraft
  version of each recording.

✅ **Done 2026-11-22** as designed. May → November on the owner's server: Minecraft 26.1.2 → 26.3,
ServerReplay 3.4.1 → 3.6.0 (explains May's recordings without movement), 9 mods removed, 3 added,
4 updated, one datapack swapped.

### 0g. Optional tidy of the original backup folder (requested 2026-11-22)

**The ask:** "With an option to fix the messy files first with a warning but mainly we dont want
to touch original files".

**Design:**
- Off by default. `mcsm library tidy FOLDER` shows the plan; `--apply` carries it out after a
  warning; the app asks with a warning dialog (No is the default).
- It never deletes: copies of a backup that the scan skipped (archive next to its unpacked
  folder, `.tar` next to `.tgz`, identical files) are **moved** into
  `FOLDER\_duplicates (check, then delete)\…` keeping their paths. Same drive: instant.
- Optional `--rename`: backup folders get a sortable date in front
  (`2026-06-09 June09_2026_PostEXP2`).
- Every move is written to `FOLDER\mcsm-tidy-undo.json`; `mcsm library tidy FOLDER --undo`
  (app: *Undo tidy*) puts everything back.

✅ **Done 2026-11-22** as designed (tested: tidy + undo restores the folder exactly). Not run on the
owner's folder: their originals stay untouched unless they choose to.

### 0h. Video projects and Flashback in launcher instances (requested 2026-11-22)

**The ask:** "edits are made on them so its nearly a finished project to upload, im trying to make
a system where i can easily make those, while having the history of the entire server all at one
place" and "the Edited Files used for videos was just an easy way to find where the original
recordings were taken from, since i used flashback editor and its per person recording".

**Found on the owner's PC:** Flashback runs in PrismLauncher instances (`Flashback 26.1.2`,
`Main Minecraft Multiplayer`), not in `%APPDATA%\.minecraft`; edits are Flashback editor states
(`flashback/editor_states/<replay uuid>.json`), separate from the recordings. mcsm only looked in
`.minecraft`, so highlight exports would have gone to the wrong place.

**Design:**
- Find Flashback in launcher instances (PrismLauncher, MultiMC, Modrinth App, CurseForge, plus
  `.minecraft`), with each instance's Minecraft version; when exporting a recording, use the
  instance whose version matches the recording (Flashback only opens a recording in its version).
- `mcsm library add-projects FOLDER`: each subfolder is a video project; its recordings are added
  (de-duplicated), and each recording's editor state is copied (read-only snapshot) from whichever
  Flashback folder has it into `<World>/projects/<project>/editor_states/`. `project.json` records
  the project, its recordings, whose perspective (sub-folder name, e.g. `Daragh`, `Me`) and where
  the edit came from. Launcher files are never changed.
- Later: list projects in the history viewer (their spans on the timeline), "open in Flashback"
  (copy recording + editor state into the right instance, never overwriting), notes/upload status.

✅ **Done 2026-11-22** (first part): launcher instances are found with their Minecraft version and
highlight exports / `--install` go to the matching instance; `mcsm library add-projects` /
`projects` and the app's *Add video projects…*. Checked on the owner's PC: both PrismLauncher
instances found (26.1.2 and 26.3), and all three recordings in `Edited Files used for videos` have
their Flashback edits found in `Flashback 26.1.2`. The later part (projects on the timeline, open in
Flashback, status) is still to do.

### 0i. Discord chat on the timeline (raised 2026-11-22, for later)

**The ask:** "later on we can add a discord integration so we can line up what happens just like
using the chat system".

**Proposed design:**
- Read a Discord channel export (DiscordChatExporter JSON/CSV, no token needed) first; a bot that
  reads chosen channels can come later (needs a bot token, kept out of files and exports).
- Each message becomes an event (`discord`, with author, channel, time, text; attachments as links)
  on the same timeline as in-game chat: report, history viewer pins/"Happening now", video
  markers/subtitles. Discord names are mapped to Minecraft names (a small editable table; linked
  accounts like DiscordSRV when present).
- Privacy: only channels the owner picks; no user IDs or avatars stored; easy to exclude people.
- **Open questions:** which channels; a bot or exports; is DiscordSRV (or similar) on the server.

### 0j. Server management log and guide (requested 2026-11-22)

**The ask:** "when managing servers there should be a history or guide so its easy to manage
everyone, add/remove mods or datapacks and understand what to do or when".

**Proposed design (in this app: it already has the whole history):**
- **Server log:** the changes between backups (`mcsm changes`) as a log, with the owner's own notes
  per change ("why Distant Horizons was removed"), stored in the library world (`server-log.json`),
  shown in the report and the history viewer. Planned changes can be added before they happen.
- **Checks on the newest backup:** mods built for a different Minecraft version than the server
  (version strings such as `+26.1.2` on a 26.3 server), two versions of the same mod, enabled
  datapacks whose file is missing, ServerReplay versions known to record no movement (3.4.0/3.4.1),
  very large mod data folders (Distant Horizons), client-only mods on the server.
- **Guides** with the server's own data filled in: updating Minecraft (back up, check each mod's
  version, ServerReplay/Flashback versions, test), adding or removing a mod or datapack (what
  happens to its blocks/items/data), and a "what changed since the last backup" summary to share
  with the players.
- Note from the owner: Flashback (Prism) shows warnings at start, likely from the HerosJourney
  datapack, that don't affect anything; a check could recognise datapack/mod warnings like this.
- **Open questions:** is a shared view for the other admins needed (a page in the library, or a
  Discord post together with 0i)?

### 0k. Text on finished videos without editing skills (requested 2026-11-22)

**The ask:** "what about the cinematics which are the finished raw version, the only thing to add
to that is some ingame texts which needed powerfull editing skills through something like
davinci, which i dont know how to do, can we make a system for that if possible".

**What we know:** the owner's cinematics are Flashback exports (`F:\...\Cinematics\*.mp4`, up to
30 GB). Each comes from a scene in a Flashback editor state, which stores the export range
(replay ticks) and TIMELAPSE keyframes (speed-ups). With the recording's start time, every second
of the video can be mapped to a real moment on the server, so the server's history can supply the
text. ffmpeg is already installed on the owner's PC (`C:\Users\Rakib\ffmpeg`).

**Design:**
- **Match the video:** pick a video and its project scene (suggested from the video length and
  the project's export ranges, or chosen by hand). Map video time -> replay tick -> real time,
  including timelapse sections (exact Flashback rule being researched from its source).
- **Suggested texts** from the history in that span: chat messages (Minecraft chat look,
  bottom-left, fading like the real chat), joins/leaves, deaths, advancements ("X has made the
  advancement …"), date and time stamps, player name cards when someone first appears, area names,
  plus the owner's own titles and subtitles.
- **A simple editor page** (like the history viewer): the video plays with the texts drawn over it
  live; a timeline shows each text as a block; tick on/off, edit the words, drag to move or
  stretch, pick a style (chat, title, subtitle, name card, date). Nothing to learn beyond that.
- **Outputs:** (1) a finished MP4 with the text burned in, made with ffmpeg (found on the PC, never
  bundled without asking), keeping the original untouched; (2) an `.ass` subtitle file with the
  same styles (VLC, mpv, ffmpeg); (3) `.srt` for YouTube captions; (4) a DaVinci Resolve-ready file
  (subtitle track + markers) for anyone who does want to edit further.
✅ **Phase 1 done 2026-11-22:** `mcsm video-text` (scene found by length, exact time mapping,
suggested texts, `.ass`/`.srt`/`.texts.json`). Checked on the owner's intro video: scene "Meeting
Room" found (337.6 s vs 337.57 s), date + 3 chat lines + an advancement placed; the advancement
("Is It a Bird?") appears exactly while the player is among birds. Phase 2: the editor page and
one-button burn-in.

- **Open questions for the owner:** OK to use the ffmpeg already on the PC for burning in? Which
  texts matter most (chat, titles, names, dates)? A Minecraft-style font (a free one such as
  Monocraft would be bundled) or a normal font?

### 0l. In-app updater (requested 2026-11-22, for later)

**The ask:** "make an in app updater later on which checks at startup and also add versioning".

**Versioning (done 2026-11-22):** `MAJOR.MINOR.PATCH` in `mcsm/__init__.py` (single source: the
installer, `pyproject.toml` and the .exe version resource follow it; a test keeps them equal).
Every installer build is stamped with its build number, commit and date (`mcsm/_build.py`, written
by `installer/build_windows.ps1`); the app title and `mcsm --version` show e.g.
`0.2.0 (build 57, 3913aae, 2026-11-22)`. Raise MINOR for new features, PATCH for fixes; publish a
GitHub Release (tag `vX.Y.Z`) to ship one.

**Updater design:**
- At startup, in the background, the app asks GitHub for the latest release of this (public)
  repository (`api.github.com/repos/MEZTKING/Minecraft-Server-Master/releases/latest`; no personal
  data, a plain `mcsm/<version>` user agent; at most once a day; off with a setting).
- If it is newer than the running version: a small bar "Version X.Y.Z is available — What's new /
  Update / Later". *What's new* shows the release notes.
- *Update* downloads the release's `MinecraftServerMaster-Setup-X.Y.Z.exe` into a temporary folder,
  checks its size and SHA-256 against the release's published digest, then (after the user confirms)
  closes the app and runs the installer, which reopens the app. Nothing is installed without a click.
- Never interrupts running work: the update waits until no import/export is running.
- Needs published releases: until the first one (v0.2.0) exists, there is nothing to update to.
- **Open questions:** also offer the latest *test* build (from GitHub Actions, needs sign-in), or only
  releases?

✅ **Done 2026-11-22** (owner asked for it now): `mcsm/updater.py` + *Updates* line in the app (channel
test/stable/off, Check now, What's new / Update / Later). Test builds: the workflow's new `latest-build`
job uploads `MinecraftServerMaster-Setup-latest.exe` + `build.json` (version, build number, commit,
SHA-256, size, last 8 commit messages) to the public pre-release `latest-build`, so no sign-in is needed.
The installer reopens the app after a silent update (`/relaunch=1`). Not yet tried end to end on the
owner's PC (needs two published builds).

### 0m. Animated text (requested 2026-11-22)

**The ask:** "add an option to animate the text and also make the words appear one by one fading /
instant with enthusiasm. All these should be available options as well as the necessary all needed
to make a good high quality in app text."

**Design:**
- Animations, in the world and on the video: **none**, **fade in**, **pop** (springs from nothing to a
  little too big and settles: "enthusiasm"), and **word by word** (instant / fading / popping, with a
  gap between words); optional **fade out** at the end.
- In the world: display entities interpolate scale changes smoothly (data 8 = start delay,
  9 = duration, 12 = scale), so a pop is a few packets; opacity (data 26) is stepped every tick for
  fades. Word by word = one text display per word, placed along the camera's right using Minecraft's
  font widths so the line keeps its layout.
- Look options: colour, border on/off + colour + thickness, shadow, glow (always bright) or lit by
  the world, background box (colour + opacity), letter size, distance, height.
- On the video (.ass): fade = alpha animation, pop = scale animation, word by word = per-word alpha
  animation inside one line (layout stays fixed).

✅ **Done 2026-11-22** as designed (Videos tab "Animation" row + look options; animation packets checked
byte for byte against Minecraft 26.1.2's codec). Demos made on the owner's welcome clip. Not yet seen
in Flashback: the in-world animations.

### 0n. Music beats over the landscape (requested 2026-11-22, for later)

**The ask:** "a music beats system that can hover over the mountains etc and is in sync with the
beats of whatever music i add".

**Proposed design:** use the existing beat detection (`mcsm/music.py`, beat grid of a song) to put
in-world elements over the landscape (text, glowing block/item displays, shapes) that pulse on each
beat: scale up and back (display interpolation), flash brightness, or appear in a rhythm. The beat
times are mapped to replay ticks through the edit's time mapping (timelapse included), so after
exporting in Flashback the pulses fall exactly on the beats; the app then adds the song to the video
with ffmpeg at the matching offset. Options: which song, where (in front of the camera / over a spot
on the map), what pulses, how strong, every beat or every bar.

### 1. Activity-aware timelapse of an area (requested 2026-11-19)

**The ask:** keep a fixed camera on an area (a base or town) and make a timelapse that shows
buildings going up. Players aren't always there, so the timelapse should automatically
**play only when someone is in the area** and skip the empty time. It should show the real
date/time, or ideally show the build **block by block**. The result should give easy,
structured timestamps.

**Proposed design, in phases (each phase is useful on its own):**

- **Phase A: smart cut list for a fixed-camera render (recommended first).**
  1. The owner defines an *area* once: a name, a dimension, and a box (x1,z1 to x2,z2, optional
     y range). Add an "areas" config (`mcsm-areas.json` next to the download, or entered in the
     app). Ideally pick the box by dragging on the 3D live map.
  2. Work out the *activity windows*: when was at least one player inside the area? Sources,
     best first:
     - live-map track recordings (`--record`, 1 position per second): exact;
     - (later) player positions decoded from the replay itself (Phase C);
     - logs only: joins/leaves give "someone was online", too coarse alone, but useful
       to rule out empty time.
     Merge windows closer than N minutes and pad each by a few seconds.
  3. The owner renders the area once with a fixed camera from a **ServerReplay chunk
     recording** (`recordings/chunks/<area>/`, which records an area continuously whether or not
     players are near), or from any replay covering it.
  4. `mcsm timelapse` outputs, for that render:
     - an **EDL cut list** that keeps only the active windows (DaVinci Resolve can import an
       EDL as a timeline, giving the cuts automatically), each speed-ramped to fit a target
       length;
     - an **SRT timestamp overlay** (e.g. "Day 12 · Sat 4 May · 20:31"), plus title-card markers
       at gaps ("3 days later");
     - **chapter/structured timestamps**: one per session in the area, labelled with who was
       there.
  - Reuses: `sync.VideoTimeline`, `exporters/markers.py`, the live track `.jsonl` format.
  - Open question for the owner: target video length, or a fixed speed (e.g. 60×)?
- **Phase B: build progress from world snapshots (true "block by block" at snapshot level).**
  Compare the area's blocks between successive world downloads or host backups (PebbleHost
  makes backups) using `mcsm/terrain.py`'s region reader extended to full block decoding. Then
  render the build growing in the 3D viewer: added blocks appear in order of snapshot, and
  within a snapshot by height (bottom-up), so it reads like a build animation. Export it as
  a browser-rendered video (MediaRecorder) or an image sequence. Accuracy is limited to the
  snapshot interval (e.g. daily).
- **Phase C (research): real block-by-block order from replays.** Decode block-change packets
  (and player positions) from `recording.tmcpr` / `cN.flashback`. This gives the exact time
  every block was placed, so the timelapse can play per block and Phase A no longer needs
  live tracks. Risk: the packet format changes with every Minecraft protocol version, so it
  needs per-version packet ID tables. Start with the server's current version only.

**Recommendation given to the owner:** start with Phase A (reliable, works with tools you
already use, low risk), then Phase B for the block-by-block look. Treat Phase C as
research. **Waiting for the owner to confirm** the order and answer the open question.

**Owner decisions 2026-11-19:** go **Flashback-first**; target length **3 minutes as an option**
(fixed speed also offered). **Phase A is done** (see `docs/TIMELAPSE.md`): areas, activity windows
from live-map tracks (fallback: log sessions), target-length/fixed-speed plan, Flashback copy with
activity markers + editor timeline (camera HOLD keyframe + TIMELAPSE track, export range), real
date/time SRT and chapters. Next: owner tests in Flashback; then Phase B.

**Update 2026-11-19: the owner likes Flashback's in-game editor.** They asked whether the
tool uses it. Answer given: not yet. `mcsm` only *reads* Flashback replays and writes files for
external editors (SRT/EDL/CSV). Revised recommendation: make the timelapse **Flashback-first**
(Phase A output goes into Flashback), keeping the Resolve EDL as a fallback. See backlog #2.

### 2. Flashback editor integration (raised 2026-11-19)

**The ask (owner's words):** "I know that Flashback, you can edit stuff in Flashback, and I
really like that editor. Is it using that method?"

**Proposed design:**
**Status:** 2a ✅ done (`mcsm flashback`); 2b ✅ done as part of the timelapse (editor-state
format fully researched; see `docs/FLASHBACK.md`). Both await a real-Flashback test by the owner.

- **2a. Write markers into a copy of a Flashback replay.** The `metadata.json`
  marker format is known (see `mcsm/replays.py` docstring): `"markers": {"<tick>": {"colour":
  int RGB, "position": {"position": [x,y,z], "dimension": "minecraft:..."}, "description": str}}`,
  with tick = (event time - replay start) / 50 ms. Add e.g. `mcsm sync --flashback-markers OUT.zip`,
  which copies the replay zip with merged markers (deaths, chat, advancements, joins, and later
  "entered area X" / activity start/end). **Never modify the original file.** Colour per event
  type, matching `sync.TYPE_COLOURS`. Verify that Flashback opens the copy and shows the markers
  (owner test).
- **2b. Generate a Flashback editor timelapse setup.** A fixed camera keyframe
  over the area, plus speed/time keyframes that skip idle periods and slow down during activity.
  **Unknown:** how and where Flashback stores editor state (keyframes, tracks). Next step: read
  the Flashback source (editor state / keyframe classes and serialization) before promising it.
- Open questions for the owner: Flashback and Minecraft versions in use (formats verified against
  Flashback master, Nov 2026); a test replay to confirm the markers/timeline load.

### 3. Other ideas (not yet requested in detail; confirm before building)

- **Ledger as a build-history source** (spotted 2026-11-20): the owner's server runs the Ledger
  block-logging mod, whose database records block changes with player and time. That would give
  true block-by-block timelapses (backlog #1 Phase B/C) and "who built/broke what" analysis
  without decoding replay packets. Needs the database file from the owner; note that selective zip
  reading skips `*.sqlite` today.

- Play back recorded tracks in the 3D viewer (time slider, speed control), e.g. "show
  last Saturday's session".
- Heatmaps: where people spend time (from tracks) and where they die (death messages + the
  `LastDeathLocation` in playerdata), overlaid on the 3D map and the HTML report.
- "Entered area X" events from tracks, as extra video markers in `mcsm sync`.
- Share the live map with friends on the same network (`--share`, bind to the LAN) or via a
  tunnel. Needs a security review first.
- More editor formats: Premiere Pro / Final Cut XML markers; DaVinci Resolve subtitle
  styling.
- macOS app bundle; code signing for the Windows installer (removes the SmartScreen warning).

## Decisions log

| Date | Decision | Why |
|---|---|---|
| 2026-11-18 | Python stdlib only, no runtime dependencies | Runs anywhere; easy to bundle into an installer |
| 2026-11-18 | All times stored as UTC; log local time converted via offsets learned from advancement files | Log lines have no time zone; advancement timestamps do |
| 2026-11-18 | HTML report is one self-contained file | Works offline and can be shared by sending one file |
| 2026-11-18 | "Music sync" interpreted as beat grid + alignment + snapping | Owner's message was brief; confirm if they meant something else |
| 2026-11-18 | Windows-first installer (Inno Setup), built in CI | Owner is on Windows; no Windows machine in the dev container |
| 2026-11-19 | Live map uses RCON + downloaded terrain instead of a server plugin | No mods needed; fits the app. BlueMap documented as the polished alternative |
| 2026-11-19 | Flashback start time decoded from the first `real_time_clock_optional` action | Flashback `metadata.json` has no start time |
| 2026-11-19 | Timelapse targets Flashback's editor first (owner confirmed) | Owner prefers editing in Flashback |
| 2026-11-19 | Flashback outputs are always a *copy* with a new replay uuid; never overwrite an editor_states file | Protects the owner's original replay and existing edits |
| 2026-11-19 | Timelapse default: fit 3:00, each idle gap 0.5 s, never slower than real time | Owner asked for "3 minutes as an option" |
| 2026-11-20 | Several backups → one history: events de-duplicated by (time, type, player, text); newest backup's stats; every backup kept as a snapshot | Owner's backups overlap and span versions |
| 2026-11-20 | Zip backups are read selectively; replays become metadata stubs until needed | Real backups are 15+ GB, mostly region files |
| 2026-11-19 | Player/dimension folders are found by known paths first, then by searching the world folder | Survives future layout changes (26.1 moved everything) |
| 2026-11-19 | Releases are published from GitHub's web UI (`release: published` trigger) | Session git access can't push tags |
| 2026-11-21 | History viewer positions come from Flashback's version-independent `move_entities` action, not version-specific game packets; only the recorded player is tracked for now | Works for every Minecraft version without packet decoding; identifying other entities needs per-protocol work |
| 2026-11-21 | Decoded paths are cached as `.jsonl` in the live-map track format (`%LOCALAPPDATA%\mcsm\paths`, keyed by file name + size + mtime) | Instant later runs; the same files feed `mcsm timelapse --tracks` |
| 2026-11-21 | Terrain: partial NBT scanner + process pool (≤ 8 workers) + per-region tile cache in `%LOCALAPPDATA%\mcsm\terrain` | Full decoding was ~90% of the time; 16 cores were idle; repeat runs should be instant. Output verified identical to the full decoder on real regions |
| 2026-11-21 | "Edit" in the unified view = highlights for videos (owner's choice); export = per-recording Flashback copies with markers + one export-range scene per highlight, plus a CSV clip list | Flashback-first (owner's editor); every point of view is offered; the CSV covers other editors |
| 2026-11-21 | Highlights live in `Documents\mcsm-highlights\<world>.json`, not in a cache | They are the owner's work: visible, backed up, independent of loaded backups |
| 2026-11-21 | The local server accepts POSTs only with an `X-MCSM` header and a same-origin (or no) Origin | Stops other websites from changing highlights through the browser |
| 2026-11-21 | Strip the `System chat: ` prefix (26.3) before classifying log lines | One change fixes joins, deaths, advancements and commands |
| 2026-11-21 | Other players in recordings are recognised by UUID in the add-entity packet, with per-protocol packet ids found in real files | Only two version-specific numbers instead of a full packet decoder; a wrong guess can't mislabel anyone because the UUID must match |
| 2026-11-21 | A player's own recording wins over "seen in someone else's" within 2 s | Their own is exact; mixing both made trails zigzag |
| 2026-11-21 | Region seams are stitched in the browser, not by resampling on the server | Tiles stay independent and cacheable per region; the page joins neighbours as they arrive |
| 2026-11-21 | Web servers don't set SO_REUSEADDR on Windows | There it lets a second server silently take a busy port |
| 2026-11-21 | Players are one person per uuid: old names (from `UUID of player` log lines / `formerly known as`) fold into the current name; `names_seen` keeps the old ones | A renamed player showed up twice on the real server |
| 2026-11-22 | Terrain per backup: "follow the time slider" shows the first backup saved **at or after** the slider time (after the last: the newest) | It contains everything built up to that moment; "the last backup before" showed the May world for almost all of May–November on the owner's server |
| 2026-11-22 | Terrain layers share tiles by key (file name + size + mtime + step); older backups are built after the newest, one at a time | Unchanged regions are read once (595 of May's 1,158 on the real backups); zip backups are unpacked one at a time to limit disk use |
| 2026-11-22 | Library import defaults to hard links on the same drive (else copy); recordings move out of the backup folders into one per-world `recordings` folder | No extra space and instant; the originals stay intact and can be deleted later; each recording stored once |
| 2026-11-22 | Library backups are named and de-duplicated by snapshot time (last-played / latest.log); worlds matched by level name + seed | Importing the same download twice does nothing; a reset world gets its own folder |
| 2026-11-22 | Archive member names are made Windows-safe (`:` → `_`) when unpacking `.tgz`/`.zip` | ServerReplay's `Recordings for: NAME` folders can't be created on Windows |
| 2026-11-22 | Backups are recognised by content, not by hosting company | Hosts change formats and many share the same panel; what matters is server root vs world-only vs archive |
| 2026-11-22 | Two backups within 2 s of each other count as the same | `.zip` stores times to 2 seconds; a zip and a folder of the same world differed by 0.3 s |
| 2026-11-22 | Library safety rules after an independent review: backups are built in `.partial-*` and renamed when complete; copies go through `*.part`; walks raise on unreadable folders; long-path prefixes on Windows; a same-time copy is skipped only if the stored one is at least as complete | A failed or partial import must never look like a finished backup, and nothing may be silently left out of 600 GB |
| 2026-11-22 | "Import a whole folder" and added recordings always copy (Move only when chosen and confirmed for recordings) | The owner's originals must never change; Flashback's live replay folder shouldn't share data with the library |
| 2026-11-22 | Tidy never deletes: duplicates are moved aside on the same drive with an undo log | The owner wants originals untouched by default; deleting stays a human decision |
| 2026-11-22 | Mod changes are matched by mod id (else file name without version); dated "between backup A and B" | The files don't record the day a mod changed |
| 2026-11-22 | The Flashback folder for a recording is the launcher instance with the recording's Minecraft version (else the most recently used) | Flashback opens a recording only in its own version; the owner keeps one instance per version |
| 2026-11-22 | The library remembers each imported archive's path, size and date | Re-scanning a 600 GB folder must not unpack everything again to find the one new backup |
| 2026-11-22 | A finished video is matched to its Flashback scene by length; time mapping follows Flashback's TIMELAPSE rule from its source (pairs with rising `ticks` only, else normal speed) | Matched the owner's two exported videos to 0.05 s; no need to ask which edit made which video |
| 2026-11-22 | In-world text = Minecraft text display entities written into the snapshot of every chunk file of a replay copy; ids/layouts taken from the version's own server jar (data reports + the game's packet codecs) | Real 3D text with lighting and occlusion; byte-exact with the game, no guessing per version |
| 2026-11-22 | A video's edit may have changed after exporting: edits saved after the video whose export start fits are offered as "probable", with the range that gives the video's length | The owner lengthened the welcome clip's edit after exporting it |
| 2026-11-22 | Text borders in the world are black text displays just behind the text | Text displays have only a drop shadow; the owner wanted white blocky text with black borders |
| 2026-11-22 | Test builds for the updater go to a public pre-release `latest-build` (fixed file name + build.json with SHA-256) | Actions artifacts need a GitHub sign-in to download; the app must update without one |
| 2026-11-19 | Options that take negative values (`--area`, `--camera`, `--music-start`...) are rewritten to `--opt=value` before argparse | `--area -100,...` was mistaken for an option |

## Known gaps and unverified assumptions

- **Library link mode shares data with the original folder.** Running a server from the
  original afterwards would change the library's copy too (documented). Explorer shows every
  backup at full size although shared files are stored once.
- **Copies of one backup are only recognised by name** (archive next to a folder named like it,
  `.tar` next to `.tgz`) or by identical bytes; other copies are unpacked and then skipped by
  snapshot time, which costs time but loses nothing.
- **Identical events in the same second count once when backups are merged** (also when one
  library backup is loaded): the May backup has one chat message sent twice in one second.

- **Flashback files not yet opened in a real Flashback.** The marker and editor-state formats
  were verified by reading Flashback's source (master, Nov 2026), not by running it. If a
  timeline doesn't load, Flashback logs `Error loading editor state`.
- Terrain per backup is coarse (one height/colour sample every 8–16 blocks on big worlds), so
  single new blocks don't show; whole builds, cleared land and newly explored areas do. With
  backups far apart, the world jumps at each backup rather than changing gradually.
- Timelapse over many days fast-forwards idle time at very high tick rates; Flashback export of
  such spans may be slow (not measured).
- Timelapse needs a Flashback replay; ReplayMod timelapses not supported yet.
- Terrain at automatic detail samples every 16 blocks on worlds this size, so hills look
  blocky and path lines can dip under the surface.
- History viewer: path time = recording start + ticks × 50 ms, so server lag (slow ticks) and
  paused time inside a recording shift later points slightly. ReplayMod `.mcpr` recordings
  give no paths yet. Other players need a known UUID (usercache/player files) and, for a
  future Minecraft version, the add/remove packet ids in `paths.ADD_ENTITY` /
  `REMOVE_ENTITIES` (find them as described in `paths.py`).

- **Not yet tested by the owner on real data:** log formats from their server, the
  installer's GUI wizard on a real Windows desktop, the live map against the real server
  (PebbleHost may need an extra port allocated for RCON).
- Flashback: the meaning of the delta form of the real-time-clock action wasn't verified
  (only the first full value is used). Paused client recordings shift later markers.
- ServerReplay's exact date-time file-name pattern and time zone are inferred from its README
  example (`2024-05-11--19-19-55`), not from source.
- Terrain: Minecraft 1.16+ chunk formats only; LZ4-compressed chunks (an optional 1.20.5+
  setting) and the Nether surface aren't drawn.
- Log times have 1-second resolution.

## Changelog

- **2026-11-22:** Editor: **text at a spot in the world**: click on the video and the camera ray is
  followed through the terrain of the backup saved right after the recording (`mcsm/worldpick.py`,
  every block's surface from the region files) to stand the text there (upright, facing the camera).
  The still ending is found from the edit alone; the app remembers the library folder (settings), and
  the tests keep out of the real app settings.
- **2026-11-22:** **Full text editor** (owner: "more texts at different times or throughout the video",
  "any other feature to make it a full usable editor"): any number of texts per video, each in the world
  or on the video with its own lines, timing, animation, look and place; timeline with draggable bars;
  drag texts on the video to move them (world texts in blocks); presets (title, subtitle, corner label);
  duplicate/delete, undo/redo, keyboard shortcuts; fonts for on-screen text; projects saved per video
  (`%LOCALAPPDATA%\mcsmideo-text`); one Flashback copy / one video for all texts.
- **2026-11-22:** **In-app updater**: Updates line in the app (test builds / stable releases / off),
  startup check once a day, What's new / Update / Later; SHA-256-verified download; the installer
  reopens the app after a silent update. CI publishes every successful build of the working branch to
  the public pre-release `latest-build`.
- **2026-11-22:** **Live text editor** (owner's request): `mcsm video-editor` / Videos tab *Open live
  editor*: the video plays in the browser with the text drawn live (in the world: projected with the
  edit's camera path; on the video: the title layout) while text, timing, animation, look and place
  change; make buttons on the page. The shared web server now streams files with HTTP Range (seeking).
  Fix: lines of in-world text overlapped (each line now stands its own height below the previous one),
  seen in the owner's Flashback screenshot and the editor.
- **2026-11-22:** "Put it in the world" again for the same video **replaces** its copy (same name and
  id, edit kept) instead of adding another ~1 GB copy; copies remember their video (`mcsm_video`).
  Fix: a camera that is still from 0 s was treated as "not found".
- **2026-11-22:** **Choosing where in-world text goes:** Videos tab "Place in the world" (right, up,
  distance, size) and **Preview world position**: the in-world text is projected into a frame of the
  video using the edit's camera and FOV keyframes (matched the owner's Flashback screenshot within ~1.5%
  of the picture height). Fix: the still-ending time was only worked out for one edit.
- **2026-11-22:** Fix (found by the owner in Flashback): an in-world text copy could be made **from an
  earlier text copy**, so the old text stayed in the world. The app's own copies are now recognised by
  their recording (`original_uuid_mcsm` / `mcsm_world_text` in its metadata; a mark in the edit file is
  lost when Flashback re-saves it) and the original recording is always used; adding text to a copy
  is refused; every copy gets its own entity ids.
- **2026-11-22:** **Animated text** in the world and on the video: fades in, pops in (springs out and
  settles), words one by one (instant / fading / popping) and fade out, with speed and word-gap
  settings; plus border colour, shadow, always-bright and a background box. Backlog: music beats over
  the landscape (#0n).
- **2026-11-22:** In-world text **tried in the owner's Flashback: it works** (real text in the world,
  hidden by a player walking in front). New: it can **appear and disappear at chosen moments**: added
  at that tick of the recording (into the right chunk file, after the right number of tick markers)
  and removed with a remove-entities packet; later chunk files get it in their snapshot. App Videos
  tab: "When" sets when it appears (default: when the camera stops), new "until" field;
  `world-text --appear-at/--until/--border`. Known: the black border is built for one viewing
  direction.
- **2026-11-22:** Every button in the app shows a short description when the mouse rests on it
  (`gui.BUTTON_TIPS`; a test fails if a new button has none). Owner's request.
- **2026-11-22: version 0.2.0** (first version bump; everything since 0.1.0 in the entries below:
  multi-backup history viewer, library, highlights, video text, in-world text, ...). Builds now carry
  a build stamp shown in the app title and `mcsm --version`.
- **2026-11-22:** **Videos tab (text on finished videos for everyone):** pick a video; the app finds
  its Flashback edit (also when changed after exporting), the recording (by its id, indexed once
  in the library) and the still ending; puts a big + small title in the world (Flashback copy with
  the edit, black border) or on a new copy of the video (Minecraft Ten font, border or 3D; NVIDIA/
  AMD/Intel encoder only when it really works). Made for the owner's welcome clip: in-world copy
  "(welcome text)" in their Flashback 26.1.2 instance, and the on-screen version.
- **2026-11-22:** **Real in-world text:** `mcsm world-text` adds a Minecraft text display to a copy
  of a Flashback recording (at X,Y,Z or in front of the edit's camera at a video second); the copy
  gets the original edit. Packet data for 26.1.2/26.2/26.3 from the game's own data reports and
  codecs, verified byte for byte in tests. Owner's note: Prism Flashback shows harmless warnings at
  start, likely from the HerosJourney datapack.
- **2026-11-22:** **Text on finished videos, phase 1:** `mcsm video-text` lines a Flashback export up
  with the server's history and writes styled `.ass` (Minecraft-like chat, date, titles) and `.srt`
  captions. Fix: recordings on a different drive than the server folder couldn't be read
  (`--replay F:\...` with a server on D:).
- **2026-11-22:** **Flashback in launcher instances; video projects; future backups.** Flashback
  folders are found in PrismLauncher/MultiMC/Modrinth/CurseForge instances (before: only
  `.minecraft`, so the owner's Prism setup was missed) and the instance matching a recording's
  Minecraft version is used for highlight exports, `flashback --install` and timelapses.
  `mcsm library add-projects` / `projects` and *Add video projects…* keep video projects (their
  recordings + a copy of the Flashback edits). Re-scans list backups already imported and don't
  unpack them again. Recordings in project folders are recognised by content.
- **2026-11-22:** **Library fixes from an independent code review** (no path could change the
  originals; these were about the library's own correctness): the app's *Import a whole folder*
  always copies (it used the Move/Link choice); recordings added by hand copy by default;
  interrupted imports leave no half backup (built in `.partial-*`, `*.part` files, stale leftovers
  cleaned); unreadable folders and paths over 260 characters no longer drop files silently; a
  grown unfinished recording is kept whole as `name (2)` (files were mixed into one folder) and a
  changed recording is stored once (was once per backup); a more complete copy of the same backup
  is no longer skipped; the scan no longer trusts an incomplete unpacked folder over its archive,
  treats any `logs` folder or game launcher folder as a server, or pairs `world.tar` with a
  different `world.zip`; tar hard links are kept; name collisions on Windows are kept as
  `(2)`; a library inside the source (or vice versa) is refused; two players' recordings with the
  same file name and start are both kept; faster world lookups and de-duplication index.
- **2026-11-22:** **Versions, mods and datapacks over time.** `mcsm changes`, a "What changed
  between backups" table in the report, *Show changes* in the app, and "Server at this moment" in
  the history viewer; each recording shows its Minecraft version.
- **2026-11-22:** **Optional tidy of the original folder** (`mcsm library tidy`, app *Tidy the
  original folder…* / *Undo tidy…*): duplicate copies moved aside, optional dated folder names,
  never deletes, fully undoable.
- **2026-11-22:** **Import a whole folder of backups, from any host.** `mcsm library scan FOLDER`
  (plan only) and `import-all`, app *Import a whole folder of backups…* (plan, ask, import; always
  copies). Detects server folders, world-only folders (Aternos/Realms/singleplayer, imported
  with a generated `server.properties`), `.zip`/`.tgz`/`.tar` archives and loose recordings;
  skips copies of the same backup; lists `.7z`/`.rar` and ignores other files. Fixes: a `.tar`
  ending in a recording was taken for a `.zip`; recording folders already in the library were
  added again; backups with neither LastPlayed nor logs are dated by `level.dat`. Tests: Tk
  windows are created with retries (intermittent init.tcl errors on the Windows CI runner).
- **2026-11-22:** Library: recordings are de-duplicated by file name + content anywhere in the
  world (ServerReplay renamed its player folders between versions); `add-recordings` keeps the
  folder layout (`players/<uuid>/` still names the player); docs recommend copy mode to leave
  originals untouched and a library on the backups' drive.
- **2026-11-22:** **Library: all backups and recordings in one place.** `mcsm library import`
  (folders, `.zip`, PebbleHost `.tgz` unpacked once), `add-recordings`, `list`; one folder per
  world, a dated folder per backup, recordings stored once, unchanged files hard-linked between
  backups, re-imports skipped. Every command and the app's Load accept a library world folder
  (all its backups + recordings); highlights live in the world folder. New app **Library** tab
  (import, *Add my Flashback replays*, world list, *Load this world*). Archive names with `:`
  (ServerReplay `Recordings for: NAME`) are unpacked as `_` on Windows. Merged backups now also
  read `--replay` extra files (were ignored when several backups were given).
- **2026-11-22:** **History viewer: terrain from every backup.** Each loaded backup becomes a
  terrain layer; the map follows the time slider (the first backup saved at or after that time)
  or a backup chosen in the new **Terrain** menu. Regions unchanged between backups are read
  once, and switching only re-downloads changed regions (seams rebuilt around them). New
  `--terrain all|newest` option and app checkbox. Tested on the owner's May and November
  backups: both layers built in 35 s from an empty cache. 454 of May's regions differ from
  November; in May the far north-west is still unexplored. `/api/terrain/<dim>?layer=N`.

- **2026-11-21:** **Highlights for videos** in the history viewer (Start/End with `I`/`O`, list,
  play, edit, delete; timeline bands) and **Export to Flashback** (replay copies with
  start/end markers and one export-range scene per highlight, plus a CSV clip list).
  **Log fix:** Minecraft 26.3 logs system messages as `System chat: …`, so since late October
  2026 every join, leave, death, advancement and command on the owner's server was missed
  (269 joins, 24 deaths, 6 advancements, 36 commands; sessions and last-seen were wrong). The
  prefix is now removed. Duplicate "Stopping (the) server" lines count as one shutdown (103
  double-counted since 2025).

- **2026-11-21:** History viewer shows **other players** seen in each recording ("seen by
  NAME"), not just the recorder: add/remove-entity packets matched by player UUID, read from
  the chunk snapshot too. Track files gain `recorded_by`/`seen` (also used by the timelapse's
  area detection). Path cache version 2 (re-decoded once).

- **2026-11-21:** The owner tried the installed app: the History tab works and terrain loads in
  parallel (worker processes OK in the frozen build). Fixed the dark grid lines between
  regions: the pages now stitch neighbouring regions' edges and corners (live map and history
  viewer). The local web server keeps connections open (HTTP/1.1); 1,000+ tile requests had
  run out of Windows sockets (`ERR_NO_BUFFER_SPACE`), and failed tiles are now retried.

- **2026-11-21:** Terrain speed: the owner's 1,569-region world went from 12+ minutes (over an
  hour while recordings were decoding) to ~33 s, and ~4 s on later runs (tile cache). Partial NBT
  scanner (2.5× per region, identical output), process pool, tile cache, 8 parallel tile
  downloads in the pages. Fixed 26.3 mixed palettes (`{"": name}` / `{id, properties}`) being
  read as air. History viewer opens on the nearest recorded spot when nobody is online.
  `python -m mcsm` and the installer launchers are now safe to re-import (worker processes).

- **2026-11-21:** History viewer (`mcsm history`, app History tab, docs/HISTORY.md): the whole
  history on a 3D map with a time slider; player paths decoded from Flashback recordings
  (`mcsm/paths.py`, cached). Terrain: 26.3 chunks (palette entries as plain strings) no longer
  crash the terrain builder (live map too); one bad region is skipped; regions near spawn and
  log-ins are built first. Local web servers no longer share a busy port on Windows.

- **2026-11-21:** First run against the owner's real backup (local, 26.3, 438 logs, 10,109
  events, 216 replays): stats, advancements and time zone now load (backlog #0.1/#0.2 confirmed).
  Renamed players are merged into one (`5ou1s` → Vakirath, `InsidiousCrow` → Crow_Shroom); join
  events gain `uuid`. Test `TestRenamedPlayer`. Real replays (ServerReplay 3.5.2/3.6.0) checked:
  lengths, starts and the Flashback chunk layout (action names, `next_tick` counts,
  `move_entities`) are as the reader expects. The 50 ms lengths were a 3.4.x problem: those
  files contain no ticks at all (checked on 762 real files), so their lengths stay estimated.
  `mcsm flashback` and `mcsm timelapse` now warn when given such a recording.

- **2026-11-20:** `HANDOFF.md` written: the work moves to a new session/account. Ledger idea added
  to the backlog.
- **2026-11-20:** Merge several backups into one history (`mcsm report A.zip B.zip …`, app
  *+ Add backup*), Backups tab with per-player progress, Server versions table, selective zip
  reading (sizes from the zip listing), lenient world identity.
- **2026-11-19:** Minecraft 26.1 world layout (players/, dimensions/minecraft/, new level.dat and
  data/minecraft/*.dat); ServerReplay Flashback replays: start from arcade save time, length
  estimate, "Recordings for_ NAME (uuid)" folders.
- **2026-11-19:** Release workflow now triggers on *publishing* a GitHub Release (tag pushes are
  blocked for Claude sessions). v0.1.0 ready to publish.
- **2026-11-19:** `mcsm flashback` (markers into a Flashback replay copy) and `mcsm timelapse`
  (activity-aware area timelapse set up in Flashback's editor; 3:00 target or fixed speed; date/time
  overlay; chapters); app Flashback tab; negative-value option fix; docs FLASHBACK.md, TIMELAPSE.md.
- **2026-11-19:** Backlog: Flashback editor integration (#2); timelapse revised to Flashback-first.
- **2026-11-19:** Live 3D map (`mcsm live`, app tab, demo mode, track recording); Flashback
  replays; unfinished ReplayMod/Flashback recordings; UUID-named ServerReplay folders show
  player names; this status document and `CLAUDE.md`.
- **2026-11-18:** Desktop app and Windows installer with CI; initial release: parsers, HTML
  report, exports, video sync, music sync.
