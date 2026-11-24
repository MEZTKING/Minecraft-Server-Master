# Project status and roadmap

**This is the hand-off document.** Any session (human or Claude) picking up this project
should read it first and must **update it after every completed change** (see
`CLAUDE.md`). Last updated: 2026-11-24 (maps and map art in the viewer; Relive a moment; build-in effect; inventory and hotbar; mob gear and elytra; no light lines between blocks; caves underground; deeper Fancy water; still figures when paused; go to a day and time; activity heatmap from Ledger; griefing and accident finder from Ledger; who built what from Ledger; blocks at any moment from Ledger; water colour fix; Ledger and one-recording ideas; flaky test fix; year in review v2: pictures and export; year in review v1; year-in-review design; chat bubbles; dropped items; live block changes; blocks at the moment; textured mobs; held items and armour; real blocks with real shapes, insides and Minecraft light; Light panel; player figures; biome finds).

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
| Editor on a Flashback edit before exporting (0p phase 1): the app's 3D map through the edit's camera (terrain from the backup after the recording, players as boxes), Camera/Map view, texts/song/beats, copy into the edit's Flashback folder | ✅ Done; tried on the owner's welcome-clip edit (composition matches the real export at 30 s) | `videotext.analyse_edit`, `videoeditor.py` (`tiles`, `tile_file`, `tracks_all`, `copy_key`), `videotext.html` (Clock, MAP3D), `mcsm video-editor --edit`, Videos tab | `docs/VIDEO_TEXT.md` |
| Server story (0s): Library tab "Watch the server story" / `mcsm story` loads the whole world and opens the history viewer at the beginning in story mode (moments in order at 10×, title cards, jump between moments, Day N of M, story feed, Beginning / Moment buttons) | ✅ Done (Fable's design); on the owner's library: 16 Apr 2025, 256 days, 121 good moments, played through the first ones | `history.html`, `cli.py` (`cmd_story`), `gui.py` (`_lib_story`) | `docs/HISTORY.md` |
| Important moments (0r): events of the whole history grouped into moments, scored (deaths, PvP, rare advancements, first joins, chat, markers; x players online; x recordings), ranked list in the history viewer with filters, ▶ Play, Keep → highlight → Flashback export | ✅ Done; on the owner's library 827 moments in 2.7 s, 272 with recordings; top: the PvP fight of 24 May with 5 recordings | `mcsm/moments.py`, `history.py` (`/api/moments`), `history.html` | `docs/HISTORY.md` |
| Real blocks in the editor's map (textured from the installed client jar): visible faces within ~80 blocks of the camera path, block outline boxes from the game's models, crossed plants, tints; smooth terrain beyond | ✅ Done; on the owner's edit 178 chunks, 89k faces in 1.1 s; camera view at 30 s matches the real export (blocks, torch, spire, build, player) | `mcsm/blockmesh.py`, `videoeditor.py` (`/api/blocks`, `/blocktex`), `videotext.html` (`loadBlocks`, atlas) | `docs/VIDEO_TEXT.md` |
| Flashback companion mod `mcsm-link` (Fabric, Minecraft 26.1.x): local token-protected link server in the game; status, seek + camera, real frames; editor view "Flashback (real)"; app/CLI install into an instance | ✅ Built (CI + locally) and smoke-tested in a test Minecraft 26.1.2 (link server, token check, real frame); not yet tried with Flashback and a replay open (owner test) | `companion/` (McsmLink.java), `mcsm/flashlink.py`, `videoeditor.py` (`/api/link*`, `/link/frame`), `videotext.html`, Videos tab, `mcsm flashback-link` | `docs/VIDEO_TEXT.md`, `companion/README.md` |
| Camera keyframes in the editor (0p phase 2): Flashback's interpolation (centripetal Catmull-Rom SMOOTH, eases, HOLD), keyframe row on the timeline, fly camera, add/move/delete/type keyframes, snap to beats, undo; written into the copy's edit only | ✅ Done; tried on the owner's edit (13 keyframes, added one by flying, undo/reset, original file untouched) | `worldtext.camera_at`/`apply_camera`/`set_camera_track`, `videotext.scene_for_match` (`camera_override`), `videoeditor.py` (`/api/camera` with keyframes), `videotext.html` | `docs/VIDEO_TEXT.md` |
| Music in the editor: song plays along, tempo detected, beats on the timeline; song on Flashback's AUDIO track of the copy's edit + a marker per beat/bar; mixed into the made video | ✅ Done; checked in the page on the owner's clip (song in sync) and in tests; not yet opened in Flashback | `mcsm/videomusic.py`, `videotext.py` (`make_items_in_world`, `burn_in`), `videoeditor.py` (`/api/music`, `/api/beats`), `videotext.html` | `docs/VIDEO_TEXT.md` |
| Editor extras: opacity, rotate, facing (camera / upright / fixed like a sign, turn, tilt), 3D depth, italic, underline, rise and slam animations, chained follow-ups with any gap, "lasts" seconds, pictures on the video (opacity, rotate, fade/rise); app Videos tab now only picks the video | ✅ Done; on-screen results checked frame by frame with ffmpeg; in-world rotation/facing/3D/rise not yet seen in Flashback | `worldtext.py` (`translation_of`, `quaternion_of`, `with_depth`, `transform_packet`), `videotext.py` (`resolve_times`, `image_filters`), `videoeditor.py` (`/api/image`), `videotext.html`, `gui.py` | `docs/VIDEO_TEXT.md` |
| In-app updater: test builds (public `latest-build` pre-release) or stable releases; startup check once a day; SHA-256-verified download; silent install that reopens the app | ✅ Done; first end-to-end update not yet tried | `mcsm/updater.py`, `gui.py`, `.github/workflows/build-installer.yml`, `installer/mcsm.iss` | `docs/INSTALLER.md` |
| Build timelapse view: the build's real world in 3D, look around, preview, camera automatic / own views (gliding) / set in Flashback; parts under 2 s left out; join exported parts with music | ✅ Done, the view and join not yet tried by the owner | `mcsm/world3d.py`, `mcsm/assets/web/map3d.js`, `mcsm/timelapse.py`, `mcsm/videojoin.py`, history viewer, `mcsm timelapse-join`, Videos tab | `docs/HISTORY.md` |
| Changes between backups: builds, map art, digging found by comparing each backup's ground with the one before; Changes toggle on the history map, change moments, building moments confirmed | ✅ Done, checked on the owner's 15 backups (26 change moments, 86 builds confirmed) | `mcsm/changes.py`, history viewer | `docs/HISTORY.md` |
| Loading bar with Cancel; loaded server/world remembered between runs and reopened at start; history page loading card | ✅ Done | `mcsm/progress.py`, `mcsm/loadcache.py`, app, history page | README |
| Ignored players (never supposed to join), left out everywhere | ✅ Done | `mcsm/ignore.py`, `mcsm ignore`, Ignored players… | README |
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

### 0o. Full editor controls and Flashback-native editing (requested 2026-11-22)

**The ask:** "add a opacity option for the texts, also the size of text in follow player can be huge.
not option to rotate, or any advanced options similar. no option to make it 3d. add everything you can
find. add options for follow up chained texts that appears one after another with any amount of seconds
etc. add options for all non texts like images too. i would like to remove the options for adding text
through there app since now we have a live editor." And: "is there a way to have it done through
flashback itself so i have the option to use the flashback editor and do everything at once, including
later on the music, which is an option already for flashback but there is not customising and its just
a drag and drop i believe. what can we do, if anything."

✅ **Editor part done 2026-11-22:** opacity; size down to 0.02 (follow-player starts at 0.12); rotate
(display left-rotation quaternion, data 13, serializer 40 in 26.1.x/26.2/26.3, about the middle of the
whole text); facing camera / upright / fixed like a sign with turn and tilt; 3D depth (darker layers
behind); italic and underline; rise and slam animations; chained follow-ups (gap after start or end,
chains of any length, moving the first moves the chain) and "lasts N seconds"; pictures on the video
(ffmpeg overlay with opacity, rotate, fade/rise, fade out); the app's Videos tab only picks the video.

**Doing it inside Flashback (researched in Flashback's source, master, 2026-11-22):**
- Flashback has **no add-on API**: no entrypoint or API package for other mods, the editor windows are a
  closed list, and its keyframe file format is hard-coded (an unknown keyframe type can't be saved or
  loaded). A companion mod adding a "Text" track inside Flashback's editor would have to patch Flashback
  internally (mixins), break with Flashback updates, and needs Java mod work: possible, but a big,
  fragile project.
- What works today: in-world texts are already *in* the replay copy, so after "Put world texts in
  Flashback" the owner keeps editing the camera, timing and speed in Flashback with the texts visible.
- **Music in Flashback (proposed next):** Flashback's AUDIO track stores only a file path per keyframe
  (`{"path": "...", "type": "audio"}`; the clip starts at that tick; fixed volume; pitch follows the
  replay speed), and Export records the game's sound, so the music ends up in the exported video. The
  app can write the song onto the AUDIO track of the replay copy's edit at the right tick, and add **beat
  markers** (the replay's `markers`, drawn as dots on Flashback's timeline; clicking one jumps there) from
  the beat detection, so camera moves can be lined up with the beat in Flashback itself. Open questions:
  which song file and where it starts; markers on every beat or every bar; with timelapse parts the
  song's pitch changes in Flashback, so the beat-synced setup is meant for normal-speed edits.
  ✅ **Done 2026-11-22** (owner: "Yes, build it"): song + start + "from song" + tempo (detected or typed)
  + markers every beat / every bar / none; the page plays it along and shows beats on the timeline; also
  mixable into the made video.

### 0p. Flashback editing inside the editor (raised 2026-11-22)

**The ask:** "or you can just make the flashback also part of the editor" (after the music question:
do everything in one place instead of export → editor → Flashback → export).

**What's possible:** the app can't draw Minecraft frames itself (only Minecraft/Flashback can), so the
editor's picture of the scene comes from either an exported video or the app's own 3D map (terrain from
the library backups, player paths from the recordings, as in the history viewer). The edit itself
(camera keyframes, speed/timelapse, audio, markers, export range) is plain JSON the app already reads
and writes.

**Proposed design:**
- **Open the editor on a Flashback edit, no export needed:** the page shows the 3D map with the camera
  path of the edit (its keyframes as points along a line, the view cone at the playhead) and the
  players' paths; texts, song and beats are placed on the same timeline in replay time. A low-quality
  "map preview" plays the camera through the map so timing can be judged before any export.
- **Edit the camera there:** add / move / delete camera keyframes on the map and the timeline, snap them
  to beats, set speed-ups; write them into the app's copy of the edit. Flashback still renders: open
  the copy, check, Export. After an export the editor switches to the real video picture again.
- **Phase 1** (smaller): open from an edit, map + camera path + timeline, texts/song/beats, write to
  Flashback. **Phase 2:** camera keyframe editing and snapping to beats.
✅ **Phase 1 done 2026-11-22** (owner: "Yes, start phase 1"): `--edit` / *Open a Flashback edit…*,
Camera view and Map view (camera path, camera marker, players), texts/song/beats on the edit's timeline,
the copy goes into the edit's own Flashback folder.
✅ **Phase 2 done 2026-11-22** (owner: "Yes, phase 2"): camera keyframes on the timeline, fly camera,
add/move/delete/type, snap to beats, Flashback's own interpolation for the preview and for text
placement; the camera goes into the app's copy of the edit only.
**Owner's question (2026-11-22):** "why are we not getting the true minecraft view but instead a map
view". Only Minecraft can draw the real picture (textures, lighting, shaders, entities). Options: (a) a
truer picture in the page: every block near the camera as a cube with Minecraft's own textures from the
installed game (no shaders or animated entities); (b) the real view from Flashback itself through a
companion mod that lets the editor drive a running Flashback (camera and time) — the true picture, but
Java mod work and patching Flashback internals; (c) export a quick low-quality preview in Flashback and
open it in the editor (works today).
**Owner chose (2026-11-22):** "Flashback mod", then "do both". ✅ (b) built: `companion/` mod
`mcsm-link` + editor view "Flashback (real)". ✅ (a) textured blocks done the same day.
- **Open questions for the owner:** is the map preview (blocky terrain, no textures) enough to judge
  camera moves, or is the goal only to avoid the round trip for texts and music? Which Flashback
  features matter most to have in the editor (camera keyframes, timelapse, orbit/follow cameras)?

**Fable's advice (2026-11-22) on 0q-0s:** order **0r → 0s (small) → 0q in stages**. 0r is the owner's actual
goal, pure Python on data already loaded, and lands in Flashback natively through 0c (copy with markers + one
scene per event), with no version risk. 0s mostly exists (needs its own entry, the whole library, play-through
with quiet-time skipping, "next important event" from 0r). 0q is the only part that can break with a Flashback
update: keep it thin (v1 a read-only Events window: list, filter, jump, "set export range to this event"; v2
live texts as client-side text displays re-added each tick; v3 snap keyframes to beat markers; never custom
keyframe types), with a sidecar `<replay uuid>.mcsm.json`, an allow-list of tested Flashback versions, a
`@Pseudo` mixin with `require = 0` and a try/finally around the ImGui window. Retire the real-view bridge
(stream, path/play, the 4 ms pump); freeze browser camera keyframing and the block view; keep the browser
editor for finished videos plus song/beat detection. Postpone block-diff "big builds" in 0r.
0r scoring: cluster events < 3 min apart into moments; weights killed by a player 6, other death 3,
advancement 1/share-of-holders (max 8), first-ever join 5, chat 0.3 per message (max 4); × (1 + 0.5 × extra
players in view, max 3); × coverage (no recording = greyed); pad 30 s before, 15 s after; weights in one dict
with tests. Presented as a ranked list by day in the history viewer (chips: kind, player, has recording;
score breakdown on hover); ticking turns a moment into a highlight → existing Flashback export.

### 0q. A native Flashback add-on instead of the bridge (raised 2026-11-22)

**Raised again 2026-11-23** after the owner tried *Flashback (real)*: "it just doesnt look great, its got some
syncing issue anyways, so lets just modify the flashback mod so we get everything we want, of course also add
backups for my original files / or dont work on the orignals just make a copy".

**Licence (checked 2026-11-23):** Flashback is "All rights reserved. Do not redistribute" (custom licence, no
API, no outside contributions). So **no fork or changed Flashback**: only our own separate mod, our code only,
working with Flashback while the game runs (as mcsm-link does now). Fine for the owner's own use; sharing such an
add-on publicly would be a grey area (not legal advice).

**Design (Fable, 2026-11-23)** (Flashback's class/method names to be checked against its jar first):
- **One mod**: mcsm-link grows into the add-on. One fail-safe hook (a Mixin at the end of Flashback's per-frame
  editor-UI drawing, `require=0`, errors disable the panel) draws an **"MCSM" ImGui panel inside Flashback's
  editor**; everything else by reflection, checked once at start into a capability list shown in the panel
  ("texts: ok, markers: unavailable"). Built against the owner's Flashback jar, never bundling it.
- **Data**: files are the truth: the app writes `flashback/mcsm/<replay-uuid>.json` (texts, music, beats, events,
  camera recipes); the add-on watches it and writes back atomically. The local HTTP stays for status/nudges; the
  picture stream is retired as the main view.
- **Live texts**: client-only text displays in the replay's world, placed every frame from the replay tick (seeks
  wipe entities), all our animations ported from `worldtext.py` (tested against shared example values); they show
  in the real picture and (to be checked) in Flashback's export. Baking into a copy stays as the way without the
  add-on.
- **Safety: never the originals.** "Edit a copy" makes "<name> (mcsm)" (new id, copied edit); a registry
  (`flashback/mcsm/registry.json`) lists the app's copies and the add-on only writes to those (read-only banner
  otherwise); versioned backups of every edit (`editor_states`) before any write (last 30 + one a day), with
  restore when the replay is closed; recordings are never changed in place, and the library copy is checked
  before making a working copy. Flashback rewrites an open edit from memory: never write an edit while it's open.
- **Order**: 1. events/moments list in the panel, click to jump, beat/event marks; 2. live texts with all
  animations; 3. snapping to beats/keyframes; 4. music (the app still writes the audio track and baked sound);
  5. camera recipes for transitions (orbit, whip, momentum, then foreground sweep), written into the copy's edit
  while the replay is closed.
- **Pitfalls**: Flashback updates (pin tested versions; unknown version = self-test, read-only), performance (no
  reflection lookups or file I/O per frame, cap text entities), testing without the game (animation/schedule
  code free of Minecraft classes, unit-tested; a self-test command logs which hooks work).
- **Recommended first stage**: app: copy registry, versioned edit backups + restore, the per-recording file; add-on:
  the fail-safe panel with the events/beats list, click to jump, and the read-only banner.

**The ask (after trying the real view):** "tried it its not great, lets just make a mod for flashback so"
… "instead of using a bridge for flashback is it possible to make it all native or what do you think."

**Why:** the browser editor + link mod streams pictures between two programs; the owner finds it
clunky and it costs resources. Inside Flashback everything is already there: the real picture, real
playback, its own camera tools and export.

**Proposed design:** the companion mod grows into a **Flashback add-on**: an extra "Minecraft Server
Master" window inside Flashback's own editor (Flashback draws its editor with ImGui; the add-on adds a
window in the same frame through a small mixin, since Flashback has no plug-in API).
- **Events**: the server's history for the open recording (chat, joins, deaths, advancements, the
  "important events" of 0r) as a list and as marks on Flashback's timeline; click = jump there.
- **Texts in the world, live**: add / move / edit the same texts as the app's editor (lines, size,
  colour, border, animation, place, follow a player) and see them in the real picture immediately (the
  mod spawns the text displays in the replay's world on the client); "Save" writes them into the
  recording copy the same way the app does now, so exports include them.
- **Music**: choose the song, see beats on Flashback's timeline (markers), snap camera keyframes to beats.
- The app keeps the library, the history, the event finder and the project files; the add-on reads a
  small per-recording file the app writes next to the replay (events, texts, song), so no live bridge.
- **Risks:** Flashback internals change between versions (the add-on must be checked against each
  Flashback update; it fails safe and only disables its window); ImGui-in-Flashback work is Java.
- **Open questions:** which first inside Flashback: events, texts or music? Keep the browser editor for
  on-screen texts/pictures on finished videos (it works without Minecraft)?

### 0r. Find the important events in all recordings (raised 2026-11-22)

✅ **First version done 2026-11-22** (owner: "Yes, event finder"), with Fable's weights; repeats of the same
player's same kind in one moment count half each time (a player dying 13 times to skeletons topped the
first run). "Together" uses players online (not yet who was in view); big builds from block changes postponed.

**The ask:** "is there a way to use the recordings to make any use of it. i want to edit all important
events, so organising that files so far were important."

**Proposed design:** an **event finder** over the whole library (3,177 recordings, 15 backups, the logs):
- Candidates, each with a time span and the players involved: deaths and what killed them, first joins
  and new players, advancements (rare ones ranked higher), boss fights, many players together in one
  place, long chat conversations, big builds appearing between two backups (block changes), first
  visits to the Nether/End, sessions with the most activity.
- Each event gets a score; the owner sees a ranked list with filters (player, kind, date) and ticks the
  ones to keep: they become **highlights** (0c), so they get Flashback copies with markers and one ready
  scene per event in every recording that covers it (each player's point of view).
- Organised in the library: `Events/<date> <title>/` with the clip list and links to the recordings.
- **Open questions:** which kinds matter most? How long before/after each event? One video per event or
  a compilation per day/week?

### 0s. The whole server history, start to finish (raised 2026-11-22)

✅ **Done 2026-11-22** (owner: "History viewer next") as Fable designed it: a story mode in the existing viewer,
not a second viewer.

**The ask:** "as the live map stands i guess you can make that a seperate thing where you can see
everything that happens sequentuilly form the beginning until the end".

**What exists:** the history viewer (*Open history viewer*) already shows all loaded backups as one
timeline with player paths, events and highlights, and each backup's world at its time.

**Proposed design:** make it its own window/entry ("Server history"), separate from the live map, that
loads the whole library by itself and can **play from the very beginning to the end**: speed control
(1 minute per day, skip quiet hours), an event feed alongside, follow a player, jump to the next important
event (0r), mark highlights while watching.
- **Open questions:** in the app window or the browser page as now? Should quiet times be skipped
  automatically?

### 0x. More kinds of moments: celebrations, sad moments… (raised 2026-11-23)

Owner, 2026-11-23: "also add player find certain biomes for the first time etc". ✅ Built the same day: `moods.biome_events`
(server firsts per biome, not in the first 3 days unless rare; personal firsts for rare biomes), from the
per-biome dates of Adventuring Time / Hot Tourist Destinations. On the owner's library: 185 finds, 38 server firsts.

✅ **Built 2026-11-23** (owner: "Yes, build them", "add more too", "even getting pets too"), Fable's design: `mcsm/moods.py`
(tags on deaths/advancements with bonus points and a mood; derived farewells and anniversaries; between-backup
records, firsts and binges from new per-backup statistics; chat mood words only as a lift), `mood`/`tags` on every
moment, filter and badges in the history viewer, story mode skips between-backup moments. On the owner's library:
103 celebrations, 91 sad moments (no farewells: every regular still plays). Left for later: griefing (needs build
ownership), gatherings from positions.

**The ask:** "for history also add any celebrations, sad moments etc find as much as you can using even player
statistics data to in parallel etc".

**Proposed design (to review with Fable before building):** new moment kinds found from the logs, the recordings'
positions and the statistics between backups (`player_history`):
- **Celebrations:** milestones (first diamonds, first Nether/End visit, killing the dragon or the wither, a full set
  of armour, an advancement many players get the same evening), birthdays of the server (first day, 100 days),
  a player's 100th hour, big group gatherings (many players in one place), "gg"/"lets go"/"yay" chat bursts.
- **Sad moments:** deaths that lost a lot (long time since the last death, deaths in lava/void where items are
  gone), a player's last session before leaving for good, many deaths in a row, griefed/destroyed builds (big "dug
  out" changes between backups next to someone else's build), "rip"/"noo" chat bursts.
- **From statistics (in parallel, per backup interval):** records broken (most blocks mined, furthest flown, most
  mobs killed in a stretch), sudden changes (play time jumps), the first time a statistic appears (first trade,
  first enchant, first elytra flight).
- **In the history:** a kind filter ("celebrations", "sad"), icons on the timeline, and the story mode picks them up.
- **Open questions:** which kinds matter most; is chat-based mood detection welcome (privacy)?

### 0y. Versions on the history timeline (Fable, 2026-11-23)

✅ **Version band built 2026-11-23** (owner: "Yes, version band"): `history.version_segments` (backups + recordings, the
app's copies and single stray recordings ignored), a band above the players (striped: no Flashback of that version),
hover = dates and Flashback, click = moments of that version. On the owner's library: 1.21.5, 1.21.10, 1.21.11, 26.1.2,
26.2, 26.3. Still open: the editor's warnings for recordings without a Flashback / in-world text support.

A version band on the timeline (1.21.5 | 1.21.10 | 1.21.11 | 26.1.2 | 26.2 | 26.3, from the backups and the
recordings), each part showing whether a Flashback of that version is on this PC; the version filter extended from
moments to recordings and highlights; warnings in the video editor when a recording's version has no Flashback or
no in-world-text support (only 26.1–26.3). One timeline (one world, one story), not separate views per version.
Still to check: decoding positions per protocol and terrain/changes across the 1.21 → 26 boundary (block renames
could look like changes).

### 0zf. Relive a moment, a loading effect, inventories (raised 2026-11-24; all ✅ built the same day)

The owner's asks: "make a system where you can replay the moment as if you were there again like a memory
scene"; "make the loading of terrain very cool instead of showing polygons, maybe like a vertices matrix";
"would it be possible to see what anyone has in their inventory/hotbar". Designs reviewed with Fable.

1. ✅ **Relive** (memory scene). A state machine in `history.html` (`relive`, `reliveTick`, `reliveStep`,
   `reliveExit`):
   - load: white, with the caption, until the exact recording and blocks are ready (at most 15 s);
   - in: fade from white;
   - play: 1x, a third of the speed within 1.8 s of the key event;
   - out: a freeze that fades to white with the caption, then everything is put back.
   - Camera: a 4 s push-in from 9 blocks behind and 4.5 above to over the shoulder (or the eyes), smoothed with a
     0.38 s time constant, slight sway, pitch softened.
   - Look: CSS letterbox, vignette and canvas filter (no post-processing pass); fog pulled to 26-90 blocks in warm
     white; trails, pins, rings, outlines and heat hidden; chat as subtitles; names only within 16 blocks.
   - Buttons: on moments with a recording (the key event: the first death, advancement or first, else the
     first event; 12 s before it to 20-60 s after), and **Relive** by Spectate (the next minute).
   - Fable's advice taken: over the shoulder reads more like a memory than the eyes (the recorded head turns are
     jittery), so *Behind* is offered; preload before fading in; restore everything on exit.
2. ✅ **Build-in effect** (`map3d.js` `revealIn`, the overview's `uLoad` rectangles):
   - each new real-block piece rises from its lowest block to its highest in 1.5 s, column by column (a hash per
     column), its edge a cyan block grid (`fwidth` lines);
   - the smooth map inside wanted-but-not-loaded pieces is drawn as a grid (every block, brighter every 8, dots
     at crossings) with a scan ripple; `LIGHT.uClock` runs always. Moment rebuilds and Relive don't animate.
   - Not done: the shadow pass doesn't follow the rise (shadows appear at once; 1.5 s).
3. ✅ **Inventories.**
   - **Checked first, on the owner's 24 May recording** (ServerReplay): the recordings keep
     `set_player_inventory` for the hotbar (all 9 slots at the start, then each change: 9 slots over 68 min) and
     `set_held_slot` (206 switches); no container contents. (Fable remembered Flashback's own
     `flashback:remote_*` payloads; these server-side recordings have none.)
   - `paths`: `"hb"` events `[slot, item, model, dye, count]` and `"hs"` (selected slot), recorded player only;
     `history.exact` turns them into `hb:<slot>` / `hs` states like `eq:`. The two packet ids were added to the
     bundled tables (`PACKETS`). The packet filter now admits 2-byte packets (set_held_slot is 2 bytes).
   - `/api/inventory/<name>/<t>`: the library backup at or before t (else the first), its playerdata read with
     `_inventory_of` (slots 0-35; armour and off hand from 1.21.5+ `equipment` or the old 100-103 / -106; ender
     chest; selected slot, health, food, level).
   - The page: a hotbar strip while spectating or reliving; the panel (**Inventory** / E) with armour, main,
     hotbar and an ender chest tab, each part saying where it's from. Other players: only held items and armour
     exist in any recording.

### 0zg. Better recordings and data from the server (raised 2026-11-24, advice)

The owner: "for the future is there a better way to record the players and the event, is there anything i can do
to improve on my side on the server so mapping data and more information is easier". Advice given (nothing built):
- Keep ServerReplay recording everyone all the time (it is what gives exact playback, hotbars, mobs and blocks at
  the moment); recordings only exist for players while they're recorded.
- Keep Ledger with no purge, and log item pick-ups and drops and container use if its config allows.
- Backups more often (e.g. every few hours while people play): inventories, stats and the map are only as fresh
  as the last backup.
- Keep `logs/` with every dated `.log.gz` (chat, deaths, advancements, joins).
- Keep the server clock right and in one time zone.
- Optional: a lightweight player-position logger (a datapack or plugin writing positions every few seconds) for
  times nobody's recording; a live map (BlueMap / squaremap) for a public view.

**The owner's setup (2026-11-24, their words, then checked in the 20 Nov backup's configs):**
1. "server replay is currently set to record people, whenever they join and stops recording when they leave".
   `config/server-replay/config.json`: `automatically_record: true`, `player_predicate: all`, Flashback format,
   `record_hotbar: true`, no size or length limit, never deletes (`delete_replays_after_duration: 0s`), chat and
   custom payloads kept, sounds, light updates and scoreboards left out; no chunk (area) recordings.
2. "ledger is also always storing any changes": `config/ledger.toml`: `autoPurgeDays = -1` (never purged), nothing
   blacklisted (all action types, worlds, objects, sources).
3. "i mostly make backups after an important event or after a month which comes first."
4. "logs is never touched."
5. "player are nearly always recorded unless i havent added the sever replay mod at that moment, it should be
   mentiond in the timeline." The timeline shows it: online rows without a recording row.

**Verdict: nothing needs changing for what the viewer uses.** Optional extras, in order of value:
- **A daily copy of `Main_world/players/` only** (stats, advancements, playerdata: a few MB). Inventories,
  armour, ender chests and statistics are only as fresh as the last backup (the 24 May inventory came from a
  5-day-old backup); a daily copy makes them exact to the day. A full backup monthly stays as it is.
- **A chunk (area) recording of spawn or the main base** (`/replay chunks start ...`): records that area whatever
  the players' view, for filming builds from any side. Bigger files.
- `record_voice_chat: true` (Simple Voice Chat is installed): voices in the recordings, only with everyone's
  agreement; the viewer doesn't play them yet.
- Keep `ignore_light_packets` and `ignore_sound_packets` as they are (the viewer takes light from the blocks and
  plays no sound).

**Found in the backups, not used yet (ideas):** ~~457 maps~~ (✅ shown in the viewer since 2026-11-24; not yet in
the year in review);
`Main_world/data/DistantHorizons.sqlite` (5 GB, last written April 2026: far terrain in detail);
`scoreboard.dat`.

### 0zd. Use the Ledger database (raised 2026-11-23)

**The ask:** "if you can use ledger to your advantage too and let me know when to test something".

**What there is.** The server runs the Ledger mod (`mods/ledger-*.jar`, `config/ledger.toml`). Every backup
since 2025-11-24 has `Main_world/ledger.sqlite`; the newest is 525 MB with 2,110,010 actions from
2025-11-14 to 2026-11-20. Its times appear to be UTC: the last action is at 02:03:13 on 20 November, when the
logs end.
- **Actions:** block-break 847k, block-place 564k, item-pick-up 326k, block-change 225k, entity-kill 62k,
  item-insert/remove 55k, item-drop 14k, entity-change 6k, mount/dismount 10k.
- **Each action has:** time, x/y/z, world, object (block/item/entity) and old object, block states, source
  (player, creeper, fire, gravity, decay...), player and extra data.
- **Per player:** Vakirath 406k, D_HumanCrafter 267k, Crow_Shroom 90k, FirstMirror7097 78k, Estproguy 62k,
  MEZTKING 57k, vegitogod22 39k, XDistroyelx 33k.

**What it unlocks.**
1. **Blocks at any moment, everywhere.** The backup after T, with Ledger's changes between T and that backup
   undone (old block states), gives the world at T even where no recording had the chunks. This works for
   every hour since November 2025, not only recorded stretches. It fills the gaps of `mcsm/moment.py`.
2. **Who built what, exactly.** Builds and map art come from each player's placed blocks (no more "near
   where you played"), for timelapse orders, the year in review (v3 card 9) and moments ("X finished the
   castle").
3. **Year in review, exact.** Blocks placed and broken per player per day (not only between backups), mobs
   killed with times, chests opened, and "played with" from building side by side.
4. **Griefing and accident finder.** Explosions (creeper), fire and lava damage near builds, with the blocks
   that went: moments and a "what happened here" panel in the viewer.
5. **A live heatmap.** Where activity happened on the map, by day.

**Design notes.**
- Read-only (`mode=ro&immutable=1`), never in place.
- The newest backup's database holds everything (it's cumulative); older ones only matter if it was ever
  reset.
- Index on time and position (a compact per-chunk change list in the cache) so "chunk X at time T" is fast.
- `rolled_back` rows (undone with /ledger rollback) must be honoured.

**Open:** which use first (proposed: 1, then 2).

✅ **Use 1 built 2026-11-23** (owner: "Blocks at any moment"). `mcsm/ledger.py`:
- `Ledger.changes()` (by world, box and time; block actions only; rolled-back rows left out).
- `Ledger.patches(dim, chunks, at, base)`: undo back to *at* from a backup saved at *base* (the first change
  after *at* gives the old block), or replay forward when the backup is older.
- `state()` turns Ledger's `{facing:"east"}` into `[facing=east]`.
- Ledger's times are UTC: checked where a recording and Ledger saw the same change (within a second).
- `blockmesh.PatchedChunk` puts the blocks over a backup chunk (tops follow; light estimated).
  `build(patches=...)`.
- Moment tiles and chunks: recordings first, Ledger for the other chunks, the backup's piece when nothing
  differs; the signature includes a hash of the patches.
- `/api/momentchanges` adds Ledger's changes (live updates for anyone's building); `/api/ledger` tells the
  page its span. The page uses moment tiles whenever Ledger covers the time, not only in exact playback.
- At most 8 moment builds queue (a dragged slider asks again).
- A whole-window query takes about 0.6 s, and a piece's patches about 0.6 s.
- Checked: Vakirath's ship at 3,886, 266 on 7 November 21:00 UTC (no recording near), with 2,806 blocks set
  back. Only the bottom of the hull shows, against the finished ship in the 20 November backup.

✅ **Use 2 built 2026-11-23** (owner: "Yes, who built what").
- `ledger.find_builds()` takes each player's block-place/break rows (in order, per world).
  - A build grows while placements stay within 32 blocks of its area (at most 192 wide) and less than
    10 minutes apart; 200 or more placed blocks over 10 or more minutes counts.
  - Map art: 64 or more wide, with 70% of the blocks within 3 heights.
  - Not building: crops, saplings, fluids, fire (`FARMED`).
  - About 4 s for 1.4 M rows; kept in `ledger/builds.json`.
- `moments.find_moments(builds=...)` replaces the movement-guessed building/map art with these (exact
  players, "placed N blocks (mostly ...)"). Timelapse orders follow moments.
- `review.projects()` groups builds by place into projects (overlapping areas, any players, any days).
  Server card **Biggest builds**; player card **Builds** (blocks in builds, their projects, with whom).
- On the owner's server: 166 builds by 8 players; 164 building moments. The biggest project is Vakirath's base
  at 4,732, 341 (23,005 blocks in 37 sessions, February–November). The top moment is D_HumanCrafter and
  Estproguy building at 1,270, -1,826.

✅ **Use 4 built 2026-11-23** (owner: "Griefing finder").
- `ledger.find_incidents()` goes through every block action in time order, remembering who placed each block
  (not crops). Placed blocks later removed by an explosion source (creeper, TNT, explosion, fireball,
  wither...), fire, a fluid or another player are damage.
  - Grouped by cause and place: explosions within 20 s and 24 blocks (at least 5 blocks); fire and fluids
    within 3 min and 48 blocks (10); another player within 10 min and 48 blocks (30).
  - Ledger's `player_id` on creeper rows is who the creeper chased; on TNT, who lit it.
  - About 3 s; kept in `ledger/incidents.json`. `/api/incidents`.
- Moments (`_damage_moments`, weight `damage` x log2(1 + blocks)): explosions, fire and lava are sad moments;
  another player's take-downs are worded neutrally (often agreed).
- The year in review has a **Disasters** card.
- On the owner's server: 13 incidents, 457 blocks. The worst was a fire that burned 145 blocks of
  Crow_Shroom's oak house at -2,007, 1,838 on 10 April (4 recordings running); creepers repeatedly hit
  Crow_Shroom's hut at -5,499, 3,116.

✅ **Use 5 built 2026-11-23** (owner: "Yes, the heatmap").
- `ledger.heat(dim, from, to, kind)` counts players' actions per 16 x 16 square (`x >> 4`) with their mean
  height. Kinds: all, building, mining, fighting, storage. About 0.9 s for the whole log, 0.2 s for a day.
- `/api/heatmap/<dim>/<from>/<to>/<kind>` (kept for 40 queries).
- The page's **Activity heatmap** box (shown only with a Ledger log) draws an instanced mesh of flat squares
  above the ground. Colour goes from yellow to red on a log scale; no depth test, no fog. The span (day, week,
  month, all) is around the time slider, in this PC's time zone.
- On the owner's server the busiest square of all is Vakirath's base at 4,744, 376 (20,954 actions); a far mob
  farm leads fighting.

### 0ze. One combined recording of everyone (raised 2026-11-23)

**The ask:** "is it not possible to just combine all the recordings as one and play that".

**Answer.** In the history viewer, that is what exact playback already does: every recording running at a
moment, on one world clock, everyone at once. A combined **Flashback** replay (so the game itself films
anywhere) is possible in principle:
- All recordings come from the same server, so entity ids, chunk data and block changes agree.
- With the world clock they line up.
- A merged replay would take one recording as the base and add the others' chunk loads, entities, block
  changes and players.
- Each recorded player is the "local player" in their own file, so in the merged one they become ordinary
  player entities.

**Hard parts:**
- rewriting packets per protocol version;
- duplicates (the same entity or chunk from two recordings);
- size (several recordings' chunk caches);
- the base player's inventory and HUD;
- Flashback's own render distance.

About one to two weeks with research into the replay format. Until then, Shot for Flashback picks the best
recording for a shot automatically.

### 0z. Recordings played back exactly, all at once (raised 2026-11-23)

Owner: "are player movements accurate?" then "make sure everything is exactly like the recordings, so everything can
be played at once accurately".

Design by Fable, 2026-11-23:

1. ✅ **World clock.** The set-time game packet's i64 game time is shared by all recordings. Its packet id is found
   by behaviour (111 / 113 / 115 in the owner's 773 / 775 / 777 recordings). Per recording, `[tick, gameTime]`
   breakpoints, with pauses as jumps. `paths.world_offsets` gives one offset per stretch of overlapping play,
   from the save times; ServerReplay 3.4.x recordings without the clock keep the old estimate.
2. ✅ **Exact playback.** `paths.exact_rows` holds every movement and turn of a recording (`.exact.json.gz`, built
   on first view) and `/api/exact/<from>/<to>` serves it. The page uses it zoomed in at 1x or 10x: position,
   body yaw, head yaw and pitch, with each player's own recording first.
3. ✅ **Built 2026-11-23** (owner: "Yes, build it").
   - `mcsm/protocol.py`: per-version packet ids and entity types from the game's own `--reports`, run from the
     client jar and Prism's libraries and Java on this PC. Bundled as `assets/protocols.json` for 773-777; other
     versions are generated on first use.
   - Player flags (sneaking, sprinting, swimming, elytra) from set-entity-data index 0, and arm swings from
     `animate`.
   - Mobs from add-entity types: within 48 blocks of the recorded player, every 10 ticks, when moved 0.3 blocks.
   - Shown in exact playback (`exact2` cache).
4. **Deferred.** Held items and armour (the item codec changes every version; built since: parity step 4), mob metadata, and block changes from
   recordings (the terrain stays the backup).

Measured before the change: computed starts were typically 1.5–3 s after the file-name time, 90% within 7 s,
independent of length.

Checked on the owner's library after the change: 1,632 of 1,643 recordings carry the clock, 2 have pauses, and
they form 717 stretches of play. Where one player appears in two recordings at the same moment (8,353 pairs),
the positions agree: median 0.00 blocks, 90% 0.00, 99% 0.24. With the old timing the median was 0.69 blocks,
90% 2.54 and 99% 40.

### 0za. Recording like Flashback, in the history viewer (raised 2026-11-23)

Owner: "add free roaming for the camera i guess, and all the feature to record like flashback", then "add player
spectate/first person mode view", "add a dev mode that makes your life easier to navigate and troubleshoot", and
"add the ability to manually record a moments if not already existed".

- ✅ **Built 2026-11-23:**
  - free camera (WASD, Space/Shift, drag to look, scroll speed);
  - spectate (first person from the recorded look, or behind);
  - dev mode (live state, block under the mouse via `/api/blockat`, view links, copy debug info, go to,
    reloads);
  - Show panel.
- **Manual moments** already exist as *Highlights for videos*: Start (I), End (O), a title and Save, then Export
  to Flashback. Open question: also list them among the moments?
- ✅ **Decided 2026-11-23:** plan in the viewer, render in Flashback (owner: "Yes, that plan").
  - Flashback is the real game: every block change, item, particle, resource pack and shader. The viewer is a
    reconstruction.
  - Built: `mcsm/shot.py` with the Shot panel (keyframes, preview, best recording by player distance, Send to
    Flashback through the world clock) and *Prepare ahead*, which estimates time and disk first; the owner
    asked for "give a warning or make it adjustable".
  - The estimates on the owner's library: a day is about 3 min and 120 MB; everything is about 3.6 h and
    8.3 GB.
  - `docs/WORKFLOW.md` written: the owner asked for "a short guide on how to approach everything the most
    optimal way and find ways to reduce it".
- **The earlier proposal for recording in the viewer itself** (kept for quick overview clips):
  - camera keyframes on a timeline in the viewer: position, look and FOV at a time, smooth or linear between
    them, as Flashback does; free-camera views or spectate can be saved as keyframes;
  - a preview of the path;
  - export as a video from the browser (canvas capture at a fixed frame rate, played frame by frame at exact
    times, so the result doesn't depend on the PC's speed; WebM, then ffmpeg to MP4 as the joins already do);
  - optionally, the same keyframes written into the Flashback copies of the recordings.

  Open questions: resolution and frame rate (1080p60?), and whether to add text and music from the video
  editor.

### 0zc. Shader-like looks in the history viewer (raised 2026-11-23)

✅ **Built 2026-11-23** (owner: "Shader-like looks"): *Graphics* Off / Fancy / Ultra in Light.

- In Fancy and Ultra, the blocks use a lit material (`MeshLambertMaterial`) with sun shadow maps that follow
  the focus: 2048 px over ±90 blocks, or 4096 px over ±150 in Ultra.
  - Our faces wind clockwise, so the normal isn't flipped per side, and the shadow map draws the engine's
    front (inner) side.
  - Plants and slanted quads are lit as if facing up.
  - The brightness gain (1.6) is measured so Fancy averages the same as Off at the village.
- Also built:
  - a sky dome shader (sun, moon, stars, sunrise and sunset glow, rain);
  - distance fog;
  - ACES tone mapping;
  - additive glow sprites at light sources (built with each block piece);
  - water with fresnel sky reflection, a sun glint and waves.
- Not done: screen-space AO, bloom (both need post-processing passes that aren't vendored), and reflections of
  the world itself.

Owner: "would it be possible to have shader support or anything for the history viewing".

Real shader packs (Iris and OptiFine) are written for the game's own renderer and can't run in a browser.
**Proposal:** a *Graphics* setting with effects that give much of that look:

- sun shadows (shadow maps over the real blocks);
- soft ambient occlusion on top of the game's corner shading;
- bloom on torches and lava;
- distance fog and an atmospheric sky with a sun and moon;
- water with reflections and moving waves;
- filmic colour.

Quality levels: Off, Fancy and Ultra (slower). For final videos, Flashback runs inside the real game, so a
shader mod in that instance applies (to check: Iris with Flashback).

### 0zb. Viewer parity with the recordings (Fable's plan, 2026-11-23)

Owner: "i assume you should finish the parity for the viewer first, im not sure ask fable".

Fable's order, most visible per effort first:

1. ✅ **Entity data.** Built 2026-11-23 (owner: "Parity, Fable's order").
   - `paths.entity_data` walks entries by index, with the stable low serializer ids (byte, varint, varlong,
     float, bool, optional block pos); index 6 is always the pose.
   - From the owner's 24 May recording: 169 mob deaths, 597 hurts, 96 player pose changes and 690 hand uses.

   Fable's original plan for this step: a generic entity-data reader (skip by serializer, learn serializer ids
   from the recording: the one at index 6 is Pose, etc.). Then:
   - pose: sleeping, dying;
   - hand use: eating, bow, shield;
   - hurt flash (`hurt_animation` / `damage_event`) and death fall-over (`entity_event` 3);
   - flags and pose for mobs too.
2. ✅ **Time and weather from the recording.** Built 2026-11-23.
   - 1.21.x: dayTime is the second i64 of `set_time`.
   - 26.1+: the packet carries a list of world clocks. Clock 0 is the overworld (VarInt id, VarLong ticks, f32
     partial, f32 rate); it jumps to a whole day when someone sleeps.
   - Rain and thunder come from `game_event` 1, 2, 7 and 8. Thunder counts as rain.
   - On 24 May the owner's recordings show a midday thunderstorm around 21:18 real time.
3. ✅ **Riding.** Built 2026-11-23.
   - `set_passengers` gives `"r"` events (the vehicle's type, or empty when getting off; also when the vehicle is
     removed). Riders are players only.
   - Every entity's type is remembered, so the sit datapack's armour-stand seats count too.
   - In the owner's recordings: 63 armour-stand seats, 80 boats, 16 horses/mules and 2 nautili.
   - The page shows a sitting pose.
   - States (flags, pose, hand use, riding) now come with their last value before the window, however long
     ago (`EXACT_SUFFIX` `.exact4`).
4. ✅ **Held items and armour.** Built 2026-11-23.
   - `protocol.TABLE_VERSION` 2: tables also list `items` and `components` (from the reports' registries);
     an older cached table is made again.
   - `paths.equipment()` reads `set_equipment` (slot byte with 0x80 = more; item stack; components skipped by
     name). At a component it can't read it keeps what came before (the main hand is first).
   - `"eq"` events for the recorded player and other players; `history.exact()` turns them into one state per
     slot (`eq:mainhand`, `eq:head`, ...) with the value from before the window, or (when none) the first one
     after it. `EXACT_SUFFIX` `.exact5`.
   - The page draws armour from `entity/equipment/humanoid[_leggings]/<material>` on enlarged player boxes
     (leather tinted by its dye) and a held icon at the fist, slightly self-lit.
   - Seen in the owner's recordings on 1.21.10 to 26.3: diamond armour and tools, bows, shields, leads,
     fishing rods, and `hsj:` item models (shown as their base item: the server resource pack isn't on the PC).
   - Not drawn: armour trims, enchantment glint. Elytra (folded wings) and mobs' held items and armour were added
     2026-11-24 (mobs' `set_equipment` is now read too; `EXACT_SUFFIX` `.exact7`).
5. ✅ **Textured mobs.** Built 2026-11-23.
   - The models aren't hand-made. `mcsm/mobmodels.py` runs the game's own model code
     (`LayerDefinitions.createRoots()`), from the newest readable client jar (26.1 and later aren't
     obfuscated). It is a small JVM bytecode interpreter that knows the model builder classes (CubeListBuilder,
     PartPose, PartDefinition, MeshDefinition, LayerDefinition, CubeDeformation, MeshTransformer), string joins
     (`"tentacle" + i`) and static tables, and treats everything else as unknown.
   - On 26.3 it reads 262 layers in under a second; they are cached per jar as JSON (`mobs/` cache).
   - Guardian spikes are placed as the game's animation leaves them at rest.
   - Nothing of the game is bundled: models and textures (`/mobtex/`) come from the jar on the PC.
   - The page builds each mob from its layer:
     - shared geometry per cube and shared materials per texture (normal, hurt, burning);
     - variants picked per mob;
     - extra layers: sheep wool, drowned, stray and bogged outer layers, slime's see-through outside;
     - size scales for wither skeletons, ghasts, elder guardians and others.
   - Walk cycles from the speed, zombie arms, fish tails, squid tentacles, bat wings and spider legs.
   - Yaw now turns the short way between rows.
   - Dev: `__history.mobGallery()` lines up one of each kind in the sky.
   - Not done: babies (no flag recorded), variants from entity data, chests on chest minecarts, bats hanging.
6. ✅ **Blocks as they were at that moment** (first version, built 2026-11-23). `mcsm/moment.py`.
   - **Where the data is.** Flashback keeps chunk packets in `level_chunk_caches/<n>` (10,000 per member; a
     u32 length, then the packet), referred to by `level_chunk_cached` actions. Each `c<n>.flashback` part (5 min,
     length in `metadata.json` `chunks`) starts with a snapshot loading every chunk then loaded, so the world a
     recording saw at tick t = its part's snapshot + the stream to t. No light updates or chunk unloads are kept.
   - **Decoding.** `level_chunk_with_light` sections are paletted containers with global state ids (26.x: block
     and fluid counts; 1.21.x: one count). Light masks are longs or bytes, depending on the version: the
     decoder takes whichever reads to the packet's end. `block_update`, `section_blocks_update` and
     `respawn` are applied as well. Biome names come from the recording's own configuration packets.
   - **Block state names.** The protocol tables (`TABLE_VERSION` 3) carry every block with its properties. A
     block's states are every combination of its properties, sorted by name, the last changing fastest; this is
     checked against every id when the table is made.
   - **Where chunks are found.** `CacheIndex` notes where each chunk sits inside a recording's caches (once,
     ~1 s per member; `moment/*.chunkidx.json`).
   - **Merging recordings.** `world_at()` uses every recording running then: the most recently loaded copy of a
     chunk is the base, and all recordings' later changes are applied in world-clock order.
   - **Showing it.** `RecChunk` has `Chunk`'s interface, so `blockmesh.build(overrides=...)` meshes it.
     `world3d.moment_tile()` keeps the last 400 in `tiles-moment/`; `/api/momenttile/<layer>/<dim>/<tx>/<tz>/<t>`
     is built in the background (2 at a time, `pending` until ready); `/api/momentrecs/<t>` is for dev.
   - The page: during exact playback the 9 nearest pieces use moment tiles at T rounded to 30 s, swapped in
     when ready. Switch **As at that moment**.
   - Checked on the owner's 10 April build at 379, -2296: the village matches the backup and the
     not-yet-built parts differ (92 changes in the main piece).
   - ✅ **Live changes** (v2, built 2026-11-23). Between the 30 s steps every chunk with a change since the
     piece's moment is rebuilt alone, as it was a second after its latest change, and drawn over the piece.
     - `blockMeshes` groups faces by chunk; `userData.chunks` holds each chunk's index range, and the page
       hides a chunk by blanking its indices (kept to put back).
     - `/api/momentchanges/<dim>/<from>/<to>` lists every change in the exact window;
       `/api/momentchunk/<layer>/<dim>/<cx>/<cz>/<t>` builds one chunk.
     - Two at a time; about 0.8 s each once warm. Speed-ups that made it possible:
       - each recording part is read once (`_read_part`, then filtered by tick);
       - the chunk-cache index records each packet's chunk, so only wanted packets are read;
       - `build()` skips backup reads when the recordings cover every chunk it needs.
   - **Not yet:** light updates from neighbours only; block entities (sign text, banner patterns) not drawn;
     a change on a chunk's edge doesn't rebuild the neighbouring chunk (a face can show or hide a moment late).
7. ✅ **Dropped items.** Built 2026-11-23.
   - `item` entities are followed like mobs. `paths.dropped_item()` reads the stack from their entity data
     (index 8, serializer 7, with optional components (serializer 6) skipped before it); it shares
     `paths.item_stack()` with the equipment reader.
   - New events: `"it"` (which item; a state, so known from before the window) and `"gone"`
     (remove_entities: picked up, killed, despawned or out of view). Mobs use `"gone"` too and vanish at once
     instead of 16 s later. `EXACT_SUFFIX` `.exact6`.
   - The page draws an icon (`itemTexture`), turning and bobbing.
   - In the owner's 10 April recording: 572 items, mostly wheat, seeds, glow ink sacs and eggs.
8. ✅ **Chat bubbles** from the server log. Built 2026-11-23: the history's chat events per player
   (`chatBy`), an HTML bubble above the name tag; shown 4 s + 60 ms a character (at most 12 s), two messages at
   most; switch **Chat** in Show. The log's times are whole seconds.

**Not worth it:** particles, sounds, explosions beyond a flash, item-frame contents and maps, the block-break
crack animation.

The latent bug Fable found (packet ids of 128 and up) is fixed.

### 0w. End-of-year review for everyone and for each player (raised 2026-11-23, for the future)

**The ask:** "for future plans, i want to add a end of year progress for everyone, one that shows everyones
stuff and one for each individual player for more personal details. add this for the future."

**Design (reviewed with Fable 2026-11-23; not started).**

**Shape.** One builder, two outputs:
- `mcsm/review.py` makes one `review.json` per range: a server block plus one block per player. It is cached
  by range and a fingerprint of the library.
- **Live pages** for the owner: `/review/<year>` and `/review/<year>/<player>`, served by the history server
  with their own `review.html` (not inside history.html).
- **Export** for sharing: the same page with the JSON, skins and map images inlined. There is one file for the
  overview and one per player, and a player's file holds only their own block plus the server totals.
- **Share a card as a picture:** each card is drawn on a 1080 x 1350 canvas by its own draw function, then
  saved as a PNG (no DOM screenshots). Skins must come from the same origin or be inlined, or the canvas can't
  be saved.

**Cards.** "Log" = from the server logs (missing days lower it); "backup" = from Minecraft's statistics at
backup times (the card says "stats from <first backup> to <last backup>").

Server's year:
1. The year in numbers: days the server ran, players, total hours (sessions; log).
2. Players per month, new and returning (first join per player; log).
3. Busiest days and hours, a heatmap of session minutes in server-local time (log).
4. Top 5 moments (the moments finder's scores).
5. The world changed: 2 or 3 before/after map pairs where the surface changed most between the first and
   last backup in range (backup).
6. Deaths by cause: the top 5 and the oddest one (death events; log).
7. Records: longest session; furthest travelled, most mined, most placed (statistics deltas; backup).
8. Advancements: how many, the rarest, who got each first (dated in the advancement files).
9. Versions and mods over the year (changes between backups).
10. Month by month: hours and the top moment of each month.
11. Chat: how many lines, the busiest chat day, top words. No quotes unless switched on.

Each player:
1. Headline: hours, days played, rank ("2nd of 9"; sessions). The statistics' play time is a check: more than
   15% apart says "logs incomplete" and uses the statistics.
2. Hours by month, favourite hour and weekday.
3. Firsts in range: first join, advancement, death.
4. Travel: walked, sprinted, flown, by boat and horse (`*_one_cm` deltas; backup).
5. Blocks mined and placed (placed = `used` of block items), the top 3 each (backup).
6. Mobs killed (top 3) and what killed them most (backup).
7. Deaths: how many, the top cause, the worst day (log).
8. Advancements earned in range, and the rarest.
9. Builds and map art near where they played: the area on a map and a timelapse link ("near where you
   played": who built what isn't certain).
10. Played with most: minutes online together, top 3 (only on their own page).
11. Their best 3 moments.
12. Compared with everyone: ranks on 3 statistics (ranks, not percentiles, for a small server).

**Year boundary.**
- Default: the calendar year in the server's time zone. Options: the last 12 months, or a custom range.
- Statistics are never interpolated. Each boundary uses the nearest backup at or before it, and the card
  says which.
- No backup before the range: the baseline is zero only if the player first joined within the range;
  otherwise the card is marked "partial".
- A statistic that went down (a reset) is clamped to 0 and flagged.

**Privacy.**
- The owner's live view shows everything. Exports are filtered:
  - chat: totals only, no quotes;
  - "played with": only on that player's own page;
  - deaths: on (they are public in the game), with a per-player switch to hide them.
- An "exclude from the review" switch per player (reusing the ignore list).
- No IP addresses; UUIDs only for skins.

**Visuals.**
- 2D: SVG/canvas charts and top-down map pictures made on the server from the sampled surface.
- No embedded 3D: it would break the one-file export and can't go on a share card. Each build card instead
  gets an "open in the 3D viewer" link.
- A path heatmap over the map is a cheap later addition.

**Build order.**
1. ✅ `review.py` and the JSON; server cards 1, 2, 3, 6, 7, 8; player cards 1 to 8; live pages; boundary
   labels. Logs, statistics and advancements only: no recordings. **Built 2026-11-23** (owner: "Yes, build v1"
   with the suggested defaults).
   - `mcsm/review.py` `build()` makes the cards' JSON.
   - Each backup's snapshot in `player_history` now keeps the statistic groups (custom travel and counts,
     mined, used, killed, killed_by); `loadcache.CACHE_VERSION` is 3.
   - Placed = `used` of items that are blocks (the newest protocol table's block list).
   - Sessions with no end stop at the next server start or the last log line.
   - Pages: `/review[/<year>[/<player>]]` (`review.html`) and `/api/review/<year>` or `<from>_<to>`.
     There's a **Year in review** button in the viewer.
   - Checked on the library: 2026 has 7 players, 1,022 h, 186 days and 276 deaths. D_HumanCrafter's hours
     from the logs (481.2) match the game's own play time (481.2).
2. ✅ Share pictures (canvas) and the filtered export. **Built 2026-11-23** (owner: "Yes, version 2").
   - `review.html` describes each card once as data. The same data draws the HTML card and a 1080 x 1350
     canvas picture (no DOM screenshots); faces are drawn from the skins as 8 x 8 data URLs.
   - **Export…** fetches the page itself and adds `window.REVIEW_DATA` (filtered) and the faces, then saves one
     file. A player's file has only that player plus the server's block; the overview's has everyone's hours
     only. **Include deaths** (on by default) can drop the deaths cards and first deaths.
   - Checked: Vakirath's file is 28 KB and works offline. Pictures were checked for the headline, travel and
     blocks cards.
3. Moments, before/after maps, builds, played-with.
4. Heatmaps from recordings; a video version.

**Traps.**
- Merge renamed players by UUID before counting (show the latest name).
- Leave ignored players out before ranking.
- Bedrock players have no Mojang skin: use a default.
- Hour cards use one stated server time zone (summer time included).
- Split sessions at midnight and at the range's edges. A session with no leave (a crash) ends at the next
  server start.
- Never open recordings while building the review: use the cached indexes.

**Open questions for the owner.**
- Calendar year as the default (a December ritual), or the last 12 months?
- Are the privacy defaults right (no chat quotes in exports; "played with" only on a player's own page;
  deaths shown)?
- Which players get a page: everyone who played in the range, or a chosen list?
- A video version later (cards and music) for the server's channel?

### 0v. YouTube-style transitions and title animations (raised 2026-11-23)

**The ask:** "can we add animation for this just like on popular youtube videos etc.
https://www.youtube.com/watch?v=SAOm9_hY39I . Such as the foreground sweep, match cut, perspective shift,
momentum cut, and more" (video: "No One Will Teach You These Minecraft Transitions"; the screenshots show
big blocky 3D titles in the world, glowing, one colour each).

**Design (Fable, 2026-11-23).** A = camera keyframes the app writes into the Flashback edit (real in-game),
B = an ffmpeg effect where exported clips are joined, C = guidance only.

| Transition | How | Needs |
|---|---|---|
| Foreground sweep | A (+B) | a solid block 1-3 blocks from the camera path (real blocks from the backup): shot 1 ends with it filling the frame, shot 2 starts behind another; cut on the covered frame. No block: a wipe |
| Match cut | C + A | two shots with the same composition: onion-skin of shot 1's last frame over shot 2's first in the 3D view, "align camera" button; hard cut |
| Perspective shift | A | one shot: orbit/crane around a player or text (SMOOTH keys, maybe FOV) |
| Momentum cut | A (+B) | shot 1 ends and shot 2 starts moving the same way at the same speed (no stop at the cut) |
| Whip pan | A + B | fast turn over 4-6 ticks at the cut, optional directional blur |
| Zoom-through | A | push into a doorway/block/text, cut on the dark frame, pull out |
| Fade, dip to black/white, wipes, pixel "block wipe" | B | ffmpeg `xfade` |
| Speed ramp | A | TIMELAPSE keys around the cut |

- **Shots:** one shot = one scene's export range; a small "sequence" file beside the edit lists shots
  (edit, scene, range) and a transition per cut; "Split here" makes two scenes with adjacent ranges; the
  owner exports each scene; the join step joins them in order with the transitions.
- **Title animations missing:** per-letter animation (drop-in, wave, typewriter), swing/flip-in (rotation),
  bounce/overshoot and shake, exit animations, colour gradients/pulse, a glow halo (text displays have no
  bloom: bigger faint copies behind, or a bloom pass on the clip), later truly blocky letters from
  block_display entities.
- **Build order:** 1. transitions at the join (fade/dip/wipe/block wipe/whip blur, chosen per cut) ->
  2. per-letter animations + rotation + gradient + glow halo -> 3. sequence/shots with "Split here" ->
  4. camera recipes (whip, momentum, orbit, speed ramp, then foreground sweep and zoom-through) ->
  5. match-cut onion-skin -> 6. block letters.
- **Pitfalls:** `xfade` re-encodes and overlaps clips (shorter total, music beats shift; hard cuts don't);
  SMOOTH keys overshoot at abrupt moves (use LINEAR/guard keys for whips); under a timelapse a 5-tick whip
  can be one frame (work in video time); backup blocks may be stale (check in the real view); per-letter x
  3D layers x halo multiplies entities (cap it).
- ✅ **Step 1 done 2026-11-23** (owner: "Join transitions first"): `videojoin.TRANSITIONS` (13: cut,
  cross-fade, dip to black/white, whip blur, block wipe, wipes, slides, zoom, circle, dissolve) per cut,
  ffmpeg `xfade` chain with the sound cross-fading, lengths capped to the clips; the app's **Join videos…**
  window (order, every cut / each cut, length, song); `--transition(s)`, `--transition-length`.
- ✅ **Step 2 done 2026-11-23** (owner: "Yes, title animations"): letters one by one (typewriter, fade, pop,
  drop with bounce), bounce, flip-up, shake; leaving by fading, shrinking, falling or rising; colour gradient
  (per-letter colours in one text component in the world, per-letter `\1c` on the video); glow halo (faint
  bigger copies in the world, a blurred layer on the video); in the world, in the editor's preview and on the
  video (where per-letter movement shows as popping). Letter texts get at most 4 3D layers (entity count).
  Not yet seen in the real game (in-world packets checked by tests only). Next: step 3, shots and "Split here".

### 0u. Timelapse parts, own camera, ignored players (raised 2026-11-22)

✅ **Done 2026-11-22.** The ask, after the owner's first timelapse ("it worked so far, although i don't
understand why it makes 9 timelapses"): "fix it all, and also i want to manually be able to look around in
the world and show a timelapse as well as an option", then "also add the option to ignore permanently some
players, as they were not supposed to join ever".

Design (Fable reviewed): parts under 2 s of video are left out and the speed worked out again so it still
fits 3 minutes; a join step (ffmpeg concat, copy when the parts match, else re-encode; music replaces the
sound, loops, fades out); the Timelapse button opens a 3D view of the build (terrain + real blocks from the
backup after the build, the editor's MAP3D moved to a shared `map3d.js`) with a preview (player boxes at
timelapse speed) and three cameras: automatic, own views (one = fixed; more = linear glide spread over the
whole timelapse, across parts), or none ("I'll set it in Flashback"). Ignored players: one list for all
servers (name + UUID + other names), applied right after loading (players, events, sessions, recordings they
made, other recordings' player lists, positions in the history viewer).

Open: Flashback's FOV isn't set (the preview uses 70, Minecraft's default); whether Flashback exports a
moving camera smoothly with LINEAR keyframes in a timelapse is untested in the real game.

### 0t. Quiet but busy: building, map art, searching (raised 2026-11-22)

✅ **Steps 1-3 done 2026-11-22** (owner: "Yes, build it"): movement stretches (`mcsm/activity.py`) as moments in
the list, the story and on the map (area outlined), stats between backups in their description, Keep = best
3 minutes. ✅ Step 4 (owner: "Timelapse of builds"): the Timelapse button on building/map-art moments (Fable's
design). ✅ Step 3/5 (owner: "Backup changes on map"): the surface comparison between backups
(`mcsm/changes.py`, Fable reviewed) on the history map (Changes toggle + list) and as moments; it also confirms
building moments. 0t is complete.

**The ask:** "sometimes players can spend long hours looking like their doing nothing but they have lots
going on. like making a massive map on the ground or looking for something" (the moments finder only sees
log events).

**Design (Fable, 2026-11-22):** a new kind of moment, `work`, found from data the app already has:
1. **Movement from the recordings** (cheap, minute-precise; build first): per player, 1-minute cells of
   the decoded paths (own recording preferred). *Building/working* = 45 min or more (gaps under 10 min merged)
   within ~48 blocks, with real motion inside (path length well above net displacement); near-zero motion or a
   perfectly regular loop = AFK/farm, dropped. *Exploring/searching* = 30 min or more visiting many new
   64-block cells (zig-zag = searching, straight = journey). *Map art/terraform hint* = a large flat area
   (100+ blocks, height constant, rows back and forth). Only players seen in some recording are found.
2. **Stats between backups** (cheap, coarse): blocks placed/mined, walked/flown per player per backup
   interval (`player_history`), to title and boost the stretches ("placed 41k blocks, mostly concrete" = map
   art; "elytra 300 km" = exploring), or as a moment without a recording.
3. **Surface change between backups** (moderate, cached; confirmation): for the areas of long stays only,
   compare the surface tiles before/after (heights, colours): proof that something big appeared and its
   area. Colour change on flat ground = map art; height change = build/terraform.
- **Score** grows slowly with time (`w * log2(1 + hours)`, x stats/diff boost, x players, x points of view),
  so a 3-hour confirmed build ranks like a PvP fight; no 10-minute cap for this kind; its own filter.
- **Keep** offers a **timelapse** of the area and span (the existing timelapse planner, one per covering
  recording; default for building/map art) or the **best 2-5 minutes** (densest stretch or the final reveal;
  default for exploring).
- Build order: `mcsm/activity.py` with tests (incl. AFK) → into the moments list and the map (area drawn) →
  stats titles → Keep → timelapse → surface diff.

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
| 2026-11-22 | The real view is a live MJPEG stream from the mod, and playing is done by Flashback with the mod following the editor's camera path; scrubbing sends one view per change | Fable's advice after the owner found per-frame request/PNG round trips "extremely slow" (3-4 game frames per editor frame, PNG encode, a seek per frame) |
| 2026-11-22 | The camera is seen from 1.62 blocks above a CAMERA keyframe's position (`worldtext.eye_at`); keyframes stay as Flashback stores them | Flashback puts the spectator player at the keyframe (player.snapTo), so the picture is from its eyes; found when the block preview put the camera inside the ground |
| 2026-11-22 | Block textures are read from the Minecraft client jar installed on the PC, never bundled or downloaded | Mojang's assets can't be redistributed; every owner has the game |
| 2026-11-22 | The companion mod uses Flashback only through reflection and has no build dependency on it | Flashback has no API; this keeps CI building without Flashback's jar and survives Flashback updates that keep the names |
| 2026-11-22 | The mod's link server listens on 127.0.0.1 with a random token in the instance's config folder; the app proxies frames so the page never sees the token | Other programs/websites on the PC can't drive the game or read the screen |
| 2026-11-22 | The song goes on Flashback's AUDIO track as a copy kept in the app's folder (cut/converted to .ogg with ffmpeg when it should start part-way); beat markers are replay markers named "♪ …" | Flashback stores only a file path per audio keyframe; a copy keeps the edit working when the original song moves |
| 2026-11-22 | All text controls live in the live editor; the app's Videos tab only picks the video | Owner: "remove the options for adding text through the app since now we have a live editor" |
| 2026-11-22 | Turned texts in the world turn about the middle of the whole text: translation = P - R(P) + R(offset) | The display's own pivot is the text's foot; a multi-line title must turn as one |
| 2026-11-22 | No companion mod inside Flashback for now; music via Flashback's own AUDIO track + beat markers proposed instead | Flashback has no add-on API and a hard-coded keyframe format (from its source) |
| 2026-11-19 | Options that take negative values (`--area`, `--camera`, `--music-start`...) are rewritten to `--opt=value` before argparse | `--area -100,...` was mistaken for an option |
| 2026-11-23 | Mob models are read from the installed client jar's model code by a bytecode interpreter, not hand-made or bundled | Exact for every mob and version, no Mojang code or assets shipped; 26.1+ jars aren't obfuscated |
| 2026-11-23 | Blocks at a moment come from each recording part's own snapshot plus its stream, merged across recordings by world clock; backup elsewhere | Flashback re-snapshots every 5-minute part, so no year-long tracking is needed; an old snapshot is worse than the backup |

## Ready for the owner to test

Things built and checked here that need a look in the real game or by eye (newest first):
- **Maps**: the **Maps** button: the walls of framed maps (e.g. the 3 x 3 at Vakirath's base, 4730, 118, 356)
  and every map; click a wall to go there; framed maps on the walls in the world. Note: the 5 x 5 map floor at
  258, 69, -2505 is covered by blocks in the 8 June backup (is it under a floor on purpose?).
- **Relive** (reworked 2026-11-24): **Relive** on a moment, or the **Relive** button by Spectate (e.g. Vakirath,
  24 May 16:02). Press **V** (or F5) to switch views; the memory look should fade after a few seconds; the camera
  shouldn't go through him or into walls; the hotbar and what he holds should match.
- **Build-in effect**: zoom in somewhere new and watch the grid, then the blocks rising.
- **Inventory**: spectate someone and press **E** (or **Inventory**); the hotbar above the timeline. Try the
  ender chest tab. Do they match what you remember having?
- **White lines between blocks**: should be gone (in Firefox, the screenshot you sent).
- **Mobs' gear and elytra**: zombies and skeletons holding things and wearing armour; elytra on backs.
- **Caves underground**: **Free cam** (F), fly down under the ground (Shift) and wait a few seconds: the caves
  around you load. Also: pause during exact playback and the players should stand still; water in Fancy should
  be a little deeper blue.
- **Go to a day and time**: the **Go to** box in the bottom bar (try `24 May 2026 8:31pm`, `yesterday 9pm`, a day
  alone, or `x y z`), and the calendar button next to it.
- **Griefing and accidents (Ledger)**: the moments list has the damage moments (e.g. the fire at Crow_Shroom's
  house, 10 April); the year in review has **Disasters**. Are the "took down" ones agreed work or not?
- **Who built what (Ledger)**: the moments list's building moments now name exact builders and blocks; the
  year in review has **Biggest builds** and a **Builds** card per player. Do they match what you remember?
- **Blocks at any moment (Ledger)**: drag the time slider back to any day since 14 November 2025 and zoom in on a
  build; it should look as it was then (e.g. Vakirath's ship at 3886, 266 on 7 November 2026, 21:00 UTC: only
  the bottom of the hull).
- **Water in Fancy and Ultra** (fixed 2026-11-23): it should look like the game's water, not pale.
- **Year in review**: the **Year in review** button; your player page; **Picture** on a card; **Export…**
  then open the file.
- **Blocks as at that moment and live changes**: play a recording near a build (e.g. D_HumanCrafter, 10 April
  2026, 17:18 UTC at 379, -2296) and watch blocks appear.
- **Shot for Flashback**: plan a shot in the viewer, send it, render it in Flashback.
- **Title animations in the world** (backlog 0v step 2): not yet seen in the real game.

Confirmed by the owner:
- **Activity heatmap (Ledger)**: "looking goood" (2026-11-23, in Firefox, whole log, everything).

## Known gaps and unverified assumptions

- **New in-world title animations not yet seen in Minecraft**: the gradient's text component (``extra`` list),
  the flip's rotation interpolation and the halo copies are written as the game's network format says, checked
  by tests, not yet watched in Flashback.

- **Saved load results are pickles** of the app's own data in `%LOCALAPPDATA%\mcsm\loaded` (only ever read
  by the app that wrote them; a new app version reads everything again). Zips aren't remembered (they're
  unpacked to a temporary folder each time). The history viewer's own steps (positions, terrain, changes)
  have their own caches; building/exploring moments are still worked out each time (about a minute).

- **Changes between backups are estimates** from the map's top-down surface sampled every 4 blocks: work under
  a roof or underground is invisible, builds on newly explored land are guessed from man-made block colours
  (game villages look the same), and the "confirmed" note counts every changed cell in a building moment's
  area (permissive: 86 of the owner's ~105 builds). Who did it comes only from recorded positions.

- **Timelapse view and join not yet tried by the owner.** Camera views assume the owner's Flashback FOV is
  near 70.

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

- **Real blocks: what's still approximate:**
  - Weighted variants always use the first model.
  - Block entities without a model in the jar are stand-ins:
    - chests: the entity texture on boxes; each half of a double chest reaches the other, using the single-chest
      texture;
    - beds: wool and planks;
    - signs: a plank board with no text;
    - banners: a wool flag with no pattern;
    - shulker boxes: a cube;
    - skulls and heads: a box with the mob's head texture, the player head as Steve;
    - decorated pots: a box with the pot textures.
  - Not drawn: copper golem statues, dragon heads.
  - Swamp grass uses one of the game's two swamp colours (the game picks by noise).
  - Caves are followed only to 40 blocks below the surface.
  - Players' real skins come from Mojang (`mcsm/skins.py`; only UUIDs are sent). Without one, the default skin is
    picked by name, not by the game's UUID rule.
  - The world shown is the backup saved after that time.
- **Flashback link not yet tried with a replay open.** Status/seek/camera use Flashback's names (checked in
  its 0.40 jar and source); the captured picture is the game's main framebuffer, which per Flashback's
  source holds the game only (its windows are drawn on a separate layer), at the size of Flashback's game
  area. Jumping backwards in a replay is slow in Flashback itself.
- **Camera interpolation:** SMOOTH follows Flashback's centripetal Catmull-Rom; its extra rescaling of
  tangents by the time gaps (factors clamped 0.4-2.5) isn't copied, so a smooth curve between unevenly
  spaced keyframes can differ slightly from Flashback's. Angles use a uniform spline on unwrapped degrees.
- **Map preview of an edit is approximate:** terrain is the surface every 2 blocks (4 further away) from
  the backup saved after the recording (builds made later show, blocks broken during the recording
  don't), and players' positions come about once a second. Only CAMERA keyframes are followed (orbit and
  entity-tracking cameras aren't), as for texts.
- **The owner has no Flashback for Minecraft 1.21.11** (recordings from Feb-Apr 2026 are 1.21.x): those can only be
  opened after adding such an instance (Prism: "Flashback 26.2" exists without a flashback folder yet).
- **Song and beat markers not yet opened in Flashback.** The AUDIO keyframe and marker formats come from
  Flashback's source (adapters quoted) and the owner's own edit files; detection on a click track gives
  119.45 BPM for 120 (good enough to line up; type the BPM if it drifts over a long song).
- **In-world rotation, "one way, like a sign", 3D depth, rise/slam not yet watched in Flashback.** Built
  from the game's data formats (indices and serializers from its jars) and the renderer's maths; the
  direction of Rotate and the side a fixed sign faces are the parts most likely to need a sign flip.
  The editor page approximates fixed signs and Turn/Tilt as a squash.
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

- **2026-11-24:** Maps in the history viewer (owner: "yes show the map art in the viewer"): `mcsm/mapart.py` reads
  a backup's maps (`data/minecraft/maps/<id>.dat` or `data/map_<id>.dat`), colours them with the game's map colours
  (read from `MapColor`'s static initialiser in the installed 26.x jar: 62 colours x 4 shades) and finds maps in
  item frames in every entities region (26.x components or the older tag). `/api/maps/<layer>` (worked out in the
  background, kept per backup), `/maptex/<layer>/<id>.png`. The page draws framed maps on their walls where real
  blocks are shown, and a **Maps** gallery (walls put together as they hang; every map). On the 8 June backup: 203
  maps, 39 framed in 4 walls; on 20 Nov: 456 maps, 192 framed. There are no locked maps, so "map art" can't be told
  apart from explored maps: all are shown.

- **2026-11-24:** Relive, after the owner's first look ("how to switch perspective in memory ... camera angle is a
  bit off aswell as some inventory items and even what hes holding ... play more like a video then a
  reconstruction"; "make the memory effect fade after a few seconds"): a camera rig that smooths the eyes and look
  and places the camera by distance and angle (it swings round, never through the player); **V** / **F5** views
  (eyes, over the shoulder, facing); the camera stops at walls (a ray through the real blocks); the memory look
  fades between 3 and 6 s and comes back for the end; Relive waits until the blocks around the player are loaded;
  no loading grid in a memory; caves only with the free camera (a player under a tree was taken as underground).
  Held item = the selected hotbar slot when the hotbar is recorded. Item icons from the game's item models
  (`Look.item_texture`, `/itemtex/<layer>/<item>.png`). A moment piece that fails is asked again after 20 s and
  shows the backup's blocks meanwhile (it used to stay missing).

- **2026-11-24:** Relive a moment as a memory (owner: "replay the moment as if you were there again like a memory
  scene"); the build-in effect for real blocks and the loading grid (owner: "make the loading of terrain very cool
  instead of showing polygons, maybe like a vertices matrix"); inventory and hotbar (owner: "would it be possible to
  see what anyone has in their inventory/hotbar"): the recorded player's hotbar exactly from the recordings, the rest
  and the ender chest from the backups (`/api/inventory`). Parity: mobs' held items and armour, elytra. Fix: thin
  light lines between blocks (owner: "there is a white line between blocks"): antialiasing sampled just outside each
  face's texture in the atlas; the lookup is now clamped to the face's own cell.

- **2026-11-24:** Owner's three viewer issues: (1) "terrain underground is not generated": caves now load when
  the free camera goes under the ground (`blockmesh.build(slab=...)`, `world3d.cave_tile`,
  `/api/cavetile/<layer>/<dim>/<tx>/<tz>/<band>`, 3 x 3 pieces in 32-block bands, cave light 6); (2) "water is
  bright blue": Fancy water is a little deeper, with less sky reflection; (3) "players keep running in same
  position when paused": figures stand still when the time doesn't move.

- **2026-11-24:** Go to a day and time in the history viewer (owner: "you should be able to search by day and
  exact time etc"): a **Go to** box with flexible dates (day first, month names, ISO, a time alone, today,
  yesterday), a calendar picker, a day alone jumps to its first activity, and places (x y z).

- **2026-11-23:** Activity heatmap from the Ledger log (`ledger.heat`, `/api/heatmap`): where players built,
  mined, fought or used chests, by day, week, month or all.

- **2026-11-23:** Griefing and accident finder from the Ledger log (`ledger.find_incidents`): placed blocks lost to
  explosions, fire, lava or another player, as moments and a year-in-review **Disasters** card.

- **2026-11-23:** Who built what, from the Ledger log: builds per player (`ledger.find_builds`), building moments
  with exact builders and blocks, year-in-review **Biggest builds** and **Builds** cards.

- **2026-11-23:** Blocks at any moment from the server's Ledger log (`mcsm/ledger.py`): the backup with the
  logged changes undone or replayed, wherever no recording had the chunks; live changes include Ledger's.

- **2026-11-23:** Water in Fancy/Ultra no longer washed out (it took the lit blocks' brightness match and too
  much sky). Backlog: 0zd Ledger (2.1 million logged actions found in the backups), 0ze one combined
  recording; a "Ready for the owner to test" list.

- **2026-11-23:** Fixed a flaky library test on CI: test archives were stamped with the time they
  were built, so two test servers built a moment apart differed. They now use a fixed time.

- **2026-11-23:** Year in review v2: each card saved as a 1080 x 1350 picture; pages exported as one file
  (only that player's year, no chat quotes, deaths optional).

- **2026-11-23:** Year in review v1: the server's year and each player's as cards (`mcsm/review.py`,
  `/review`), statistics compared between backups, a **Year in review** button in the history viewer.

- **2026-11-23:** Year-in-review design written (backlog 0w, reviewed with Fable): cards, boundaries,
  privacy, build order and open questions.

- **2026-11-23:** Chat bubbles over players in the history viewer (parity step 8), switch **Chat** in Show.

- **2026-11-23:** Dropped items in exact playback (parity step 7): item icons from the recordings' entity
  data; entities vanish when removed (`"gone"`).

- **2026-11-23:** Live block changes in exact playback: changed chunks rebuilt on their own between the 30 s
  moment steps; `/api/momentchanges`, `/api/momentchunk`; faster moment reads.

- **2026-11-23:** Blocks as they were at the moment (parity step 6, first version): `mcsm/moment.py` decodes
  Flashback's chunk caches and block changes; protocol tables carry block states (`TABLE_VERSION` 3);
  moment tiles near you during exact playback; switch **As at that moment**.

- **2026-11-23:** Textured mobs (parity step 5): `mcsm/mobmodels.py` reads every mob model from the installed
  game's own code; `/api/mobmodels`, `/mobtex/`; animated models in exact playback, boxes as the fallback.

- **2026-11-23:** Held items and armour in exact playback (parity step 4): `set_equipment` decoding,
  per-version item and component lists in the protocol tables, armour shells and held icons on the figures.

- **2026-11-23:** Riding in exact playback (parity step 3): sitting players in boats, on horses and on the sit
  datapack's seats; states known from any time before; `paths.EXACT_SUFFIX`.

- **2026-11-23:** Graphics Off / Fancy / Ultra in the history viewer (0zc): shadows, sky, haze, glow, water,
  film colour; `map3d.setGraphics` / `applyGraphics`.

- **2026-11-23:** Plan in the viewer, render in Flashback.
  - `mcsm/shot.py`: picks the recording, converts real time to ticks through the world clock (pauses included),
    and writes a copy with camera keyframes (eye minus 1.62), the export range and a marker.
  - `/api/shot/check` and `/api/shot/send`, and the Shot panel (K, preview, recording choice).
  - Prepare ahead: `/api/prepare/estimate`, `start` and `cancel`; recordings read in full, three at a time,
    then real blocks along player paths; estimates measured on the owner's library.
  - `BuildWorld.tile_ready`.
  - `docs/WORKFLOW.md`.
  - Backlog 0zc (shader-like looks).

- **2026-11-23:** Viewer parity, steps 1 and 2 (Fable's plan).
  - Pose, hand use, hurt, death and burning for players and mobs.
  - The recording's time of day (both formats) and weather drive the light, with rain around the camera.
  - New packet ids in `protocols.json`: hurt_animation, damage_event, game_event.
  - The `exact3` cache.
  - Recordings for exact playback are read in the background (three at a time); the API returns `pending` and
    keeps the last 12 in memory.

- **2026-11-23:** History viewer, after the owner's test at spawn on 24 May.
  - New: Show panel, free camera, spectate (first person / behind), dev mode (`/api/blockat`, view links).
  - Exact playback now glides between recorded positions (no snapping on steps) and takes leg speed from the
    recording.
  - The game's sneaking pose.
  - Chest lids and bottoms use the right texture regions; double chests use the game's `_left` / `_right`
    textures (`blockmesh` v4).
  - Packet ids of 128 and up are read correctly.
  - Backlog 0za and 0zb added.

- **2026-11-23:** Poses, arm swings and mobs in exact playback (0z step 3).
  - `mcsm/protocol.py` and `assets/protocols.json`, generated from the game's reports on the owner's PC.
  - `paths.decode_entities(extras=...)`.
  - `/api/exact` returns flags, swings and mobs.
  - The page shows poses, swings and mob boxes; the player ring stays on the ground.

- **2026-11-23:** Exact playback (0z steps 1-2, Fable's design).
  - The world clock in every recording (`paths` `CACHE_VERSION` 3: rows keep their tick and the file ends with
    the clock; all paths are decoded again once, about 15 minutes for the owner's 2,446 recordings).
  - `world_offsets`.
  - Exact rows per recording and `/api/exact`.
  - The page plays them zoomed in at 1x and 10x, with body, head and look.

- **2026-11-23:** Real blocks rebuilt after the owner's screenshots (gaps, flat lighting, box players, empty
  interiors).
  - `blockmesh` v2:
    - reads block states and light;
    - follows open space from the sky to find every visible block;
    - turns every model element into quads with variant and element rotations, and multipart;
    - adds stand-ins for chests, beds, signs, banners and shulker boxes;
    - stores sky and block light plus corner shading per face, in 12-byte records.
  - The page lights blocks with Minecraft's light curve; the new Light panel sets time of day, brightness, torch
    light, exposure, sun shading and soft shadows.
  - Players are skinned figures that walk.
  - Real-block pieces now also load around every player.
  - Biome finds added as moments.
  - Players' skins as recorded, after the owner said "please use whatever the recording had": `skins.timeline`
    reads the profile textures from each recording's first 256 KB (2,446 recordings in about 3 s, then cached);
    at a time T the figure wears the skin from the player's last recording before T.
  - Players' real skins from Mojang, after the owner said "Yes, real skins": `mcsm/skins.py`, kept in
    `%LOCALAPPDATA%\mcsm\skins`, refreshed weekly, with slim arms, the outer layer and old 64x32 skins handled,
    and a *Real skins* switch.
  - A death that outweighs a celebration makes the moment neutral.
  - On the owner's world: a 128 x 128 piece builds in about 1.1 s, about 77,000 faces.
  - Then, blockmesh v3:
    - biome colours: biome palettes read from the chunks; colours from the jar's biome files and colour maps, with
      a stdlib PNG reader; blended over 3 x 3 biome cells; per face as RGB565 in a 14-byte record; grass block
      sides tint only the overlay;
    - water in waterlogged blocks;
    - uvlock;
    - double chests joined;
    - skulls, heads and decorated pots.

- **2026-11-23:** Owner's PC set up (not code): Prism instances "Flashback 1.21.5" and "Flashback 1.21.10" created
  (Fabric loader 0.20.1, Prism picks Java), and Flashback + Fabric API added to them and to "Flashback 26.2"
  (Flashback 0.39.10 / 0.39.10 / 0.43.6 from Modrinth, sha512 checked). Each counts for `version_targets` once it
  has its Flashback jar: `flashback_folders` now also counts an instance whose `mods` has `Flashback-*.jar` (Flashback
  makes its `flashback` folder only on the first recording), so every version on the band has a Flashback.

- **2026-11-23:** History timeline: Minecraft version band (0y), click to filter moments by version.

- **2026-11-23:** Celebrations and sad moments (0x): `mcsm/moods.py`, moments' `mood` and `tags`, per-backup statistics
  extended (dragons, withers, diamonds, trades, enchanting, fish, breeding, raids, player kills, sleep, damage, time
  since death, totems, elytra; `loadcache` version 2 so saved loads are read again), history filter/badges.

- **2026-11-23:** History viewer: **real blocks close up** (owner: "render a real world texture behaviour lighting etc
  … so it doesnt look like a rough sample always at least for the history"): `world3d.block_tile` builds 128 x 128-block
  pieces of the backup shown (`blockmesh.build(only=...)` reads the chunks around a piece so pieces fit), cached on
  disk; the page loads them around the camera target (up to 2 pieces out, nearest first), lit (Lambert, normals; shared
  `map3d.blockMeshes`/`buildAtlas`), and cuts the smooth map away under them (a shader clip list). Versions (Fable's
  review, owner: "the history should be separated by version"): exports were already per version (each recording to the
  Flashback of its version); the History tab's status line and the export button/confirm now list it per version
  (`flashback.version_targets`); real blocks use each backup's own version (client jar, `/blocktex/<layer>/`) and say when
  that version's game isn't installed; `--flashback-dir` warns that it overrides the per-version choice. On the
  owner's library: backups 1.21.5 to 26.3, Flashbacks for 1.21.11, 26.1.2, 26.3.

- **2026-11-23:** Add-on stage 1, app side (owner: "Stage 1", then "for now i want to be able to edit and fully finish
  off what i have stored so far": the in-Flashback panel is postponed; Flashback Extras exists, an "ultra" add-on is
  for later): `mcsm/editbackup.py` versioned backups of every Flashback edit (at app start, before the app writes an
  edit; last 30 + one a day; restore saves the current first), "Earlier versions of this edit…" in the edit picker,
  `mcsm flashback-edits`; `flashback.registry`/`register_copy`/`register_existing` (app copies, also by file name as
  Flashback drops the app's mark when a recording is renamed); `mcsm/fbpanel.py` (per-recording events file for the
  future panel, not used yet). On the owner's PC: 32 edits saved, 13 copies registered, no originals.

- **2026-11-23:** Checked the owner's report "Thank you for playing … its in both meeting room recordings": the text
  is not in either original (library or Flashback copy, searched byte for byte); only in the app's "(text)" copy,
  made 22 Nov 08:57 and not remade since (so it doesn't have the new letter-drop title yet). Backlog 0q: native
  add-on design by Fable, with the licence finding (Flashback is All Rights Reserved: no fork).

- **2026-11-23:** "Open a Flashback edit": recordings not in that Flashback's replays folder are marked "not in
  Flashback" (by recording id, `flashback.replay_ids`), and right-click → "Put the recording back in Flashback" copies
  them there (`flashback.put_back`: checked zip and id, a different recording with the same name gets " (2)", the
  original stays). Owner: "Yes, add it".

- **2026-11-23:** Owner: "wheres my original edits". Their edits were intact in Flashback 26.1.2's editor_states,
  but the recordings were only in the library, so Flashback didn't list them. Copied (owner: "Yes, copy them") the
  four with camera keyframes back into its replays folder (Meeting Room 2026-05-24--18-49-45, Fishinand chasing
  2026-05-24--19-45-40, the welcome intro 2026-06-06--19-55-14, D_HumanCrafter 2026-05-24--15-25-52; ~1.5 GB),
  checked (size, zip, uuid = edit). Library copies unchanged. Idea: a right-click "Put the recording back in
  Flashback" in the edit picker.

- **2026-11-23:** Copies get a findable name in Flashback's list (owner: "i cant find the meeting room recording in
  flashback"): a recording that never got a name was listed as "Unnamed (text)"; now `flashback.copy_name` uses the
  recording's file name (its date) and, for text copies, the scene's name: "Meeting Room – 2026-05-24--18-49-45 (text)".

- **2026-11-23:** "Install the Flashback link" window: instances older than Minecraft 26.1 are shown greyed out
  (the mod needs 26.1+; the owner's list offered 1.21.11 first), and each says if the mod is installed already
  (and which version); the first one still needing it is chosen.

- **2026-11-23:** Pages in their own window (owner: "or is it better as a stand alone app"; answer: keep the web
  pages, open them in an app window): `mcsm/appwindow.py` uses Edge/Chrome `--app` with a separate profile, or
  Firefox (the owner's browser; no Edge on their PC) with a separate profile whose userChrome.css hides tabs and
  toolbars; setting `app_window` (Videos tab checkbox); falls back to the default browser. Tried on the owner's PC:
  the window opens without tabs or toolbars; Firefox shows its terms-of-use screen once for the new profile
  (left to the owner to accept; the app doesn't bypass it).

- **2026-11-23:** Owner (after trying the title animations in Flashback: "its working"): "automatically line up
  texts with keyframes", "a zoom in for the keyframes tab", "no option for music such as volume adjuster,
  equaliser". Editor: texts snap to keyframes/beats/playhead/other texts and move with the keyframe they sit on
  (`start_key`/`end_key`), "Fit to this camera move"; timeline zoom (scroll, Shift+scroll, − Fit +, follows the
  playhead); song: stops at, volume to 2x, fade in/out, bass/middle/treble, even loudness (loudnorm), game sound
  level in the made video; previewed with Web Audio, baked into Flashback's song copy (`videomusic.song_filters`).

- **2026-11-23:** Title animations (0v step 2): letters one by one (typewriter, fading, popping, dropping in), bounce,
  flip up, shake, ways of leaving (fade, shrink, fall, rise), colour gradient, glow halo; in the world, the editor
  preview and on the video; saved with default looks too.

- **2026-11-23:** Ctrl + mouse wheel over a slider or number box moves it one step (Ctrl+Shift: ten) in the
  editor and the history viewer (owner: "make it work with the scroll if you hold control … fine tuning them is a
  pain").

- **2026-11-23:** Transitions when joining videos (0v step 1, owner: "Join transitions first"): 13 transitions per
  cut with ffmpeg `xfade` (sound cross-fades, lengths capped to the clips), the app's **Join videos…** window
  (order Up/Down, every cut or each cut, length, song from any second), CLI `--transition`, `--transitions`,
  `--transition-length`. Backlog 0w: end-of-year review for everyone and for each player (owner's future plan).

- **2026-11-23:** Default looks for new texts (owner: "make some settings the default so im not changing the text
  style constantly, maybe with the option per video"): per kind of text (the four + buttons), for this video
  (in its project file) or all videos (`%LOCALAPPDATA%/mcsm/text-styles.json`); "Use on all like it"; GET/POST
  `/api/styles`. Docs say where the editor saves things.

- **2026-11-23:** Owner: "a proper loading bar so i know its working", "a cancel job option", "save state of the
  app between opening and closing unless something has changed", "right click … show the source in folders",
  "i cant find where i kept my timelapses". `mcsm/progress.py` (jobs report progress; Cancel raises
  `Cancelled`, a BaseException, at the next report or printed line, between files); the app's loading bar with
  Cancel; the library list is read in the background at start (it opened all 2,446 recordings before the
  window showed); `mcsm/loadcache.py` saves the loaded data (pickle, keyed by a fingerprint of the files loading
  reads, the options and the app version; folders only) and the app reopens the last server at start
  (owner's world: 15 s first time, 1.1 s after); the history page's loading card; right-click in "Open a
  Flashback edit" shows the recording, timeline file, replays folder and Flashback's export folder
  (`flashback.export_folder` from its own settings); Join timelapse parts opens in that export folder. Fixed:
  a worker thread read tkinter values (crashed loading), and error message boxes lost their text.

- **2026-11-22:** Changes between backups (0t step 3, owner: "Backup changes on map"; Fable reviewed):
  `mcsm/changes.py` compares each pair of consecutive backups (coarse map tiles first, then every 4 blocks; natural
  changes such as trees, water/ice, snow and the End's island ignored; areas of 250+ blocks² close together;
  man-made blocks on newly explored land), cached per pair. History viewer: Changes toggle (outlines + list,
  who was there), up to 5 change moments per interval, building moments confirmed. On the owner's library: 14
  intervals in a few minutes, 26 change moments, 86 builds confirmed.

- **2026-11-22:** The video editor's 3D map now uses `mcsm/world3d.py` too (one copy of the terrain-tile, block and
  texture code; its cache moved to `%LOCALAPPDATA%/mcsm/build-view/`), checked on the owner's "Meeting Room"
  edit (325k block faces). The editor's player boxes and "who's in view" leave out ignored players.

- **2026-11-22:** The timelapse view's "Leave out parts shorter than … s of video" (owner: "make the cutoff
  adjustable"): 0-30 s, default 2, remembered in the browser; re-plans at once (`plan_moment(min_part_s=)`,
  `min_part_s` in /api/timelapse/plan|export).

- **2026-11-22:** Timelapse follow-up (owner: "why it makes 9 timelapses", "fix it all", "look around in the world
  and show a timelapse", "ignore permanently some players"): parts under 2 s left out and the length shared out
  again; the timelapse view in the history viewer (real world in 3D, look around, preview, camera automatic /
  own views gliding / set in Flashback); `mcsm timelapse-join` + Videos tab "Join timelapse parts…" (ffmpeg, with
  music); ignored players (`mcsm ignore`, "Ignored players…", `--show-ignored`). The editor's 3D map moved to
  `assets/web/map3d.js`, shared by both pages. Fable reviewed the design.

- **2026-11-22:** Moved the owner's 11 timelapse copies (1.21.11; two builds) and their timelines out of the 26.1.2
  Flashback into `F:\Game Backup\mcsm-library\Main_world\Flashback for Minecraft 1.21.11` (owner: "Yes, move them").

- **2026-11-22:** Owner's first timelapse: the 1.21.11 copies landed in the 26.1.2 Flashback, which can't open them
  ("make it sorted by version"). Now highlights/timelapse copies only go into a Flashback of exactly the
  recording's version (`find_flashback_dir(..., exact=True)`, `flashback.export_target`), else into
  "Flashback for Minecraft <version>" next to the highlights file with instructions; world-text copies refuse with
  a clear message; moments show their version and can be filtered by it; the timelapse summary warns first.

- **2026-11-22:** Build timelapse after Fable's review: the point of view is whoever was near the build (not the
  longest file), the app's own copies and duplicate files are left out, renamed players' positions found, the plan
  reads nothing (recorded sizes), free space checked per target folder, the export runs in the background with
  progress and removes half-made copies on failure. AFK (owner: "sometimes people will go afk"): idle minutes at
  a build's start/end are cut, builds score by active time, timelapses fast-forward AFK minutes. All 120 build
  moments on the owner's library can be planned.

- **2026-11-22:** **Timelapse of a build** (Fable's design): `timelapse.plan_moment` / `export_moment` film a building
  or map-art moment from the builder's own recordings (the point of view with the most activity), with activity
  windows from the positions read from the recordings (`samples_from_tracks`), one speed across all files
  (about 3 minutes in all), the same fixed camera (map art from above), one Flashback copy per file
  "(timelapse n of N)" with one scene, and an order list. History viewer: Timelapse button (plan with size and
  free space, then export). Owner's library: D_HumanCrafter's 4 h 11 min build = 9 parts, 81x, 2:56, 1.1 GB.

- **2026-11-22:** Quiet but busy, tuned on the owner's library (1.5 M positions from 8 players, read once in ~20 min,
  then cached): "exploring" now needs ground the player had never been to (running around the base all evening
  was 239 false "searching" stretches); after Fable's review: trips away of up to 10 min don't end a build, the
  area grows with the work (whole map arts), map art judged on the usual ground level, work found once in the
  background after all positions are read, travel grouped only when near, tools left out of "mostly", stable ids.
  Result: 109 builds, 14 map arts, 50 explorations/journeys; e.g. D_HumanCrafter building 4 h 11 min (35k items).

- **2026-11-22:** **Quiet but busy** (0t, Fable's design): `activity.find_stretches` finds building, map art
  and exploring/searching/journeys from the players' paths (1-minute cells; AFK ignored); `find_moments`
  turns them into moments (players in the same area together; score 4 x log2(1 + hours) x players x points
  of view; statistics between backups: items used/placed with the main one, mined, flown). The history
  viewer recomputes the moments once the positions are read, outlines the area, plays a stretch in ~30 s in
  the story, and Keep takes the best 3 minutes. Backups now also keep items used and elytra distance.

- **2026-11-22:** Backlog 0t: quiet but busy stretches (building, map art, searching), designed by Fable.

- **2026-11-22:** Story mode after Fable's review: a running viewer for another world is replaced; ▶ Play on one
  moment/highlight plays just that with Story on; single-backup worlds aren't reloaded; no lost clicks while busy;
  clearer message while the moments are still loading; the feed's current mark clears after a moment ends.

- **2026-11-22:** **The server story** (0s): `mcsm story` and the Library tab's *Watch the server story* load
  the whole world and open the history viewer at the beginning with Story on; Play goes through the
  important moments in order (10× during a moment, a 2 s title card, a jump to the next), with ⏮ Beginning,
  ◀ Moment / Moment ▶, "Day N of M" and a "Story so far" feed. The app's `_load` can chain the viewer.

- **2026-11-22:** Moments, after Fable's review: at most 10 minutes long; PvP from the log parser's killer
  (not a name inside the text, e.g. an item); rarity among regular players (1 h+), cap 5; no "first join" on
  the logs' first day or for renamed players; the app's replay copies left out; recordings counted per
  point of view; Keep remembers the moment (no duplicates); overlaps by bisect (2.7 s → 0.04 s).

- **2026-11-22:** **Important moments** (event finder, 0r): `moments.find_moments` groups the history's
  events into moments and scores them (weights in one dict, advised by Fable); the history viewer lists
  them with filters (kind, player, with a recording, best first / by date), ▶ Play and Keep (makes a
  highlight, so the existing Flashback export applies). Owner's library: 827 moments, 272 with recordings.

- **2026-11-22:** Owner tried the real view (link 0.2): "its not great". Logged their direction in the
  backlog: a native Flashback add-on instead of the bridge (0q), an event finder over all recordings
  (0r), the whole server history from start to finish as its own viewer (0s).

- **2026-11-22:** Edit picker: the scene selected inside an edit is marked "(current scene)" only when the
  edit has several scenes ("open in Flashback" was read as the replay open right now).

- **2026-11-22:** **Flashback link 0.2** (owner: real view "runs extremely slow frames"; advice from Fable): the
  mod streams the game picture as MJPEG (captured every 1/fps s on the game side, JPEG via LWJGL stb on a
  worker thread, newest frame only, one-time tickets), Flashback plays the replay itself while the mod
  moves the camera along the editor's path each frame, `/clock` keeps the editor's playhead with the game,
  and the mod keeps Minecraft from throttling while streaming. Test game: ~26 frames/s at 854 px. The
  editor sends a view only when the playhead or camera changes.

- **2026-11-22:** **Edit picker** (owner: "Yes, add it"): *Open a Flashback edit…* lists every scene of every
  Flashback edit (scene name, recording name and file, export length, camera keyframes, saved, which
  Flashback; app copies marked, also when Flashback dropped the mark) instead of a file dialog of ids;
  `mcsm video-editor --list-edits`; `videotext.list_edits`.

- **2026-11-22:** Flashback link, first owner test: connected, but the owner's Flashback had only the app's
  text copy of the welcome recording (its `original_uuid_mcsm` led to an older copy that's gone). The
  editor now also accepts a copy of the same recording by content (same length and chunk files).

- **2026-11-22:** Editor map: **real blocks with Minecraft's textures** near the camera path
  (`blockmesh.build`: reads the chunks, keeps visible faces, block looks from the client jar's
  blockstates/models with parents, `{"sprite": ...}` texture refs of 26.x, flat-plane models as plants;
  the page builds a texture atlas with tints and one mesh, and leaves the smooth terrain out under the
  blocks). **Fix: the camera is at eye height** (1.62 above Flashback's keyframe position) for text
  placement, spot picking, the preview, new keyframes (stored back at feet height) and the link mod.

- **2026-11-22:** **Flashback companion mod `mcsm-link`** (`companion/`, Fabric, Java 25, Minecraft 26.1.2,
  Loom 1.19.4): a link server in the game on 127.0.0.1 (port from 8796, token in
  `config/mcsm-link.json`) with `/status`, `/view` (pause, seek, camera like Flashback's keyframes, FOV,
  roll) and `/frame` (PNG of the game picture). The app finds it (`flashlink.find_link`), the editor's new
  view **Flashback (real)** drives it and shows the real picture with texts on top, and the Videos tab /
  `mcsm flashback-link install` copies the jar into an instance's mods folder (CI builds it and bundles it
  in the installer). Smoke-tested in a test Minecraft: link server up, token enforced, frame captured.

- **2026-11-22:** **Camera keyframes in the editor** (0p phase 2): `worldtext.camera_at` now moves the
  camera like Flashback (SMOOTH spline through the neighbouring keyframes, HOLD, LINEAR, eases; a segment
  uses its first keyframe's right side and the next one's left side), which also makes text placement
  follow curved camera moves. In an edit opened without a video: keyframe row on the timeline (drag,
  snap to beats), fly camera (drag to look, WASD/E/Q), add/delete/type keyframes, undo/redo, back to the
  edit's camera. Changes live in the project (`camera`) and as `camera_override` on the match
  (`scene_for_match` applies it), and are written only into the app's copy of the edit.

- **2026-11-22:** **Editor on a Flashback edit before exporting** (backlog 0p phase 1): `analyse_edit`
  uses the scene's export range as the timeline; the page plays a clock instead of a video and shows the
  app's 3D map (three.js, the history viewer's terrain tiles, stretched to close the seams between
  regions) through the edit's camera, with players as boxes and names; Map view shows the camera path.
  Texts, the song and beats work as with a video; the copy goes into the edit's Flashback folder, one per
  edit and scene (`copy_key`). `mcsm video-editor --edit/--scene`, Videos tab *Open a Flashback edit…*.

- **2026-11-22:** Editor **music**: choose a song (kept as a copy), it plays along in the page; tempo and
  first beat detected (`music.detect_beats`) or typed; bars/beats drawn on the timeline. "Put world texts
  & song in Flashback" puts the song on an AUDIO track named `mcsm music` in the copy's edit
  (`videomusic.set_audio_track`, only the app's track is replaced) and replay markers on every beat or
  bar, lined up through the edit's time mapping (`videomusic.plan`), with a warning for sped-up parts.
  "Make the video" can mix the song in (`videomusic.audio_args`, `amix`, never longer than the video).
  A copy can now be made with only a song (no texts). Owner's new idea logged as backlog 0p.

- **2026-11-22:** Editor: **opacity, rotate, facing, 3D letters, italic/underline, rise and slam,
  chained follow-ups, "lasts", pictures**. World: display translation + left-rotation quaternion about
  the text's middle, fixed facing (entity yaw = camera yaw + 180 + turn, pitch = tilt) with parts placed
  in the display's own space, 3D as darker copies behind, rise via interpolated translation. Video:
  `\alpha`, `\frz/\frx/\fry` about the group's middle, `\move` for rise, depth layers for any colour,
  pictures with ffmpeg overlay (scale, opacity, rotate, fade, rising y). `resolve_times` turns chains
  and "lasts" into real times (loops and missing items fall back safely). Pictures are copied into
  `%LOCALAPPDATA%\mcsm\video-text\images`. The Videos tab only picks the video now; the editor gets the
  library world (spot picking from the app works). Fixed: duplicating a text gave it no id.
  Flashback source research: AUDIO keyframes and markers documented (backlog 0o).

- **2026-11-22:** The owner's library is built (`F:\Game Backup\mcsm-library`, world `Main_world`):
  15 backups (April 2025 1.21.5 to November 2026 26.3, incl. January 2026 unpacked from the `.7z`),
  3,177 recordings stored once, 2 video projects with 3 Flashback edits copied. 301 GB on disk against
  ~650 GB of original folders (copy mode: originals only read). The import itself found and drove the
  library fixes listed below (review fixes, re-scan index, Windows names).
- **2026-11-22:** Editor: **text that follows a player**: the text displays ride the player
  (`set_passengers`, 107 in 26.1.2/26.2, 109 in 26.3, from the game's data reports), stacked and bordered
  with the display translation (camera-facing space); the player's entity id at that moment comes from
  the recording (`paths.players_in_view`); preview follows the decoded track. Owner's idea: words
  reacting with the game, not leaving the screen when the camera moves.
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
