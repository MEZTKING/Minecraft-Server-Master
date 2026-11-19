# Project status and roadmap

**This is the hand-off document.** Any session (human or Claude) picking up this project
should read it first and must **update it after every completed change** (see
`CLAUDE.md`). Last updated: 2026-11-19 (first release, v0.1.0).

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
  (v0.1.0 published 2026-11-19 from the working branch).
- Every push also builds an installer artifact under Actions → Build installer (sign-in required,
  kept 90 days).

## How to work on it

- Branch: `claude/exciting-cannon-d0r8dm` (push here; no PRs unless asked).
- Tests: `python -m pytest tests`. GUI tests need tkinter and a display:
  `xvfb-run -a python3 -m unittest tests.test_mcsm`.
- Python 3.10+, **standard library only** at runtime. three.js is vendored in
  `mcsm/assets/web/vendor/`.
- Releases: bump `__version__` (`mcsm/__init__.py`) and `version` (`pyproject.toml`), update the
  changelog here, push, then push a tag `vX.Y.Z`; the workflow attaches the installer to a
  GitHub Release. Released so far: v0.1.0.
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
| Timelapse Phase B (block-by-block from world snapshots) / Phase C (from replay packets) | 📝 Designed | n/a | Backlog #1 |

## Backlog (feature requests and ideas, in priority order)

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
| 2026-11-19 | Options that take negative values (`--area`, `--camera`, `--music-start`...) are rewritten to `--opt=value` before argparse | `--area -100,...` was mistaken for an option |

## Known gaps and unverified assumptions

- **Flashback files not yet opened in a real Flashback.** The marker and editor-state formats
  were verified by reading Flashback's source (master, Nov 2026), not by running it. If a
  timeline doesn't load, Flashback logs `Error loading editor state`.
- Timelapse over many days fast-forwards idle time at very high tick rates; Flashback export of
  such spans may be slow (not measured).
- Timelapse needs a Flashback replay; ReplayMod timelapses not supported yet.

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

- **2026-11-19 – v0.1.0 released** (GitHub Release with the Windows installer): everything listed
  below.
- **2026-11-19:** `mcsm flashback` (markers into a Flashback replay copy) and `mcsm timelapse`
  (activity-aware area timelapse set up in Flashback's editor; 3:00 target or fixed speed; date/time
  overlay; chapters); app Flashback tab; negative-value option fix; docs FLASHBACK.md, TIMELAPSE.md.
- **2026-11-19:** Backlog: Flashback editor integration (#2); timelapse revised to Flashback-first.
- **2026-11-19:** Live 3D map (`mcsm live`, app tab, demo mode, track recording); Flashback
  replays; unfinished ReplayMod/Flashback recordings; UUID-named ServerReplay folders show
  player names; this status document and `CLAUDE.md`.
- **2026-11-18:** Desktop app and Windows installer with CI; initial release: parsers, HTML
  report, exports, video sync, music sync.
