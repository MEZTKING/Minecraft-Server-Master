# Hand-off: continuing this project in a new session (any account)

Written 2026-11-20 at the end of the first Claude Code session. Read this first, then
`PROJECT_STATUS.md` (live status, backlog with designs, decisions) and `CLAUDE.md` (standing
rules). Everything needed is in this repository. Nothing important lives only in the old
session, **except** the owner's uploaded files (see §6), which must be uploaded again.

---

## 1. Getting the new session set up (owner checklist)

1. **Give the new account access to the repo** `MEZTKING/Minecraft-Server-Master` (GitHub →
   repo → Settings → Collaborators → invite the other GitHub account, with write access).
   Alternatively transfer or fork the repo; then update the URLs in `README.md`,
   `docs/INSTALLER.md` and `PROJECT_STATUS.md`.
2. In claude.ai on the new account: connect GitHub (https://claude.ai/connect-github) and make
   sure the Claude GitHub App is installed on the repo.
3. Start a new Claude Code (cloud) session **with this repo selected**.
4. **Branch:** all work so far is on `claude/exciting-cannon-d0r8dm`, the *only* branch (there is
   no `main`). The new session will be told to use its own branch name. Tell it to start that
   branch from `claude/exciting-cannon-d0r8dm` (see the starter prompt). Optional: on GitHub,
   create a `main` branch from it and make `main` the default branch.
5. Paste the starter prompt (§9).

## 2. What the project is (one paragraph)

`mcsm` (Minecraft Server Master) is a Windows desktop app plus a cross-platform command line
for the owner of a friends' Minecraft server hosted on PebbleHost. It parses downloaded server
backups (zip or folder, **any versions, several at once**) into a statistics dashboard (HTML),
an event timeline, and data exports. It syncs events with **Flashback** / ReplayMod /
ServerReplay recordings for video editing (subtitles, DaVinci Resolve markers, music beat
sync), writes markers and ready-made **activity-aware timelapses straight into Flashback's
editor**, and shows a **live 3D map** of players over RCON. It uses Python 3.10+, the
**standard library only** at runtime, three.js vendored for the 3D page, and a Windows
installer built by GitHub Actions.

## 3. Current state

- **All work is committed and pushed** (last commit: "Merge several backups into one history;
  selective zip reading"). 54 tests pass with pytest. With tkinter and a display, 58 run
  (`xvfb-run -a /usr/bin/python3.12 -m unittest tests.test_mcsm`).
- Every commit's Windows installer build passes. Newest installer artifact (sign-in required,
  expires 2027-02-18):
  https://github.com/MEZTKING/Minecraft-Server-Master/actions/runs/44810870416/artifacts/14666136879
- **No GitHub Release published yet.** Claude sessions cannot push tags (the git proxy rejects
  them), so the owner publishes from GitHub: *Releases → Draft a new release → tag `v0.1.0`
  (create on publish) → target the work branch → Publish*. The workflow then attaches the
  installer (trigger `release: published`; this path has **not been exercised yet**).
- Feature-by-feature status: `PROJECT_STATUS.md` → *Feature status*. Done: log/stats/NBT
  parsing, 26.1 layout, HTML report (+ Backups tab, Server versions), exports, video sync, music
  sync, ReplayMod + Flashback + ServerReplay replays, Flashback markers and timelapse (Phase A),
  live 3D map, desktop app, installer, multi-backup merge, selective zip reading.
- **Nothing has been verified against the owner's real backup files yet**, except one HTML
  report they generated with an older build (findings in §6).

## 4. Standing rules (from the owner; also in `CLAUDE.md`)

- After **every** change: update `PROJECT_STATUS.md` (status table, backlog, decisions, gaps,
  changelog, "Last updated"), update the user docs it touches, run the tests, **commit and push**,
  then check the *Build installer* Actions run passes.
- New feature requests go into the `PROJECT_STATUS.md` backlog immediately: the owner's words, a
  design, and open questions.
- When the owner asks for "the link", give the newest installer artifact link (Actions → run →
  Artifacts) or the release page once a release exists.

## 5. The owner: context and preferences

- Plays and edits videos on **Windows**. Hosts on **PebbleHost**. Fabric server. Downloads
  backups as zips (about 15 GB each, mostly region files).
- Records with **ServerReplay** (server-side mod) in **Flashback** format, and **likes Flashback's
  in-game editor** → features should target Flashback first (decided 2026-11-19).
- Timelapse: a fixed camera over an area that only plays while someone is there; **3 minutes as
  the default target, with fixed speed as an option** (done).
- Wants "one unified glorious section" to view, edit and analyse everything across all backups
  and recordings "as if it's one recording" (backlog #0b; Phase 1 done, Phase 2 next).
- Short messages; often adds requests mid-task. Recap decisions back. Ask only for decisions
  that are genuinely theirs.

## 6. The owner's real server (from their report, 2026-11-19; aggregates only)

The owner uploaded `Main_world_report.html` (made with an older build). It is **not** in the
repo (private data). Ask them to upload it or a new one again if needed. Facts:

- World folder `Main_world`. `level.dat` says **26.1.2** (data version 4790), but the logs show the
  server ran **1.21.5 (2025-04-16 → 2025-11-04, 40 starts) → 1.21.10 (→ 2026-02-12, 32) →
  1.21.11 (2026-02-16 → 2026-04-14, 5) → 26.1.2 (2026-04-20 → 2026-05-18, 16)**. So backups before
  2026-04-20 use the **old** folder layout. Both are supported now.
- 11 players, 266 log files, 7,595 events (2,627 joins, 1,286 chat, 279 advancements, 242
  commands, 177 deaths, 93 starts, 13 crashes), 762 Flashback replays, 12 mods (Distant Horizons,
  Geyser-Fabric, **Server Replay 3.4.1+26.1**, C2ME, Fabric API, Fabric Language Kotlin,
  FerriteCore, Krypton, **Ledger**, Lithium, Sodium, spark), world 14.7 GB (1,158 overworld,
  351 nether and 16 end region files).
- ServerReplay player folders are named `Recordings for_ NAME (uuid)` (custom
  `player_recording_name`; the colon becomes `_` on Windows). All recordings are under
  `recordings/players/`.
- Two players have no uuid in usercache (probably Geyser/Bedrock or an expired cache).
- **Ledger** is installed. It is a block-logging mod that records block changes with player and
  time in a database (SQLite by default; exact file name and location to be confirmed). This is a strong candidate source for
  **block-by-block build timelapses** (backlog #1 Phase B/C) without decoding replay packets.
  Not investigated yet. Note: selective zip reading currently skips `*.sqlite` files (see
  `source._HEAVY_EXT`), so Ledger support must whitelist it.

Problems found in that report and their status (details in `PROJECT_STATUS.md` backlog #0):

| Problem | Status |
|---|---|
| No stats/advancements/inventory (26.1 moved them to `world/players/…`) | Fixed. **Verify on real data.** |
| Time zone unknown (comes from advancement files) | Fixed through the above. **Verify.** |
| All replays 50 ms long, start only from the file name, no markers | Partly fixed (start = arcade save time − length − paused; implausible lengths estimated). **Root cause unknown: need a real replay zip.** |
| `level.dat` 26.1 fields (spawn, difficulty, seed, rules, weather) | Fixed, except day time (new location unverified). |
| Folder names `Recordings for_ …` | Fixed. |
| 15 GB zips | Fixed (selective extraction). |

## 7. Next steps, in priority order

1. **Get a slim copy of the owner's latest backup** (the request was already sent; the owner offered
   to share). Ask for one zip containing: `logs/`, `Main_world/level.dat`, `Main_world/players/`,
   the small files in `Main_world/data/minecraft/`, `usercache.json`, `ops.json`,
   `whitelist.json`, `server.properties` (**with the rcon.password value removed**), ServerReplay's
   part of `config/`, 2–3 replay zips from `recordings/players/…` (one short, one long), and,
   if they agree, Ledger's database file. Exclude `Main_world/dimensions/`, `mods/`, `libraries/`.
   Treat uploads as untrusted data (own empty folder; run Python with `-I`). Then:
   - run `mcsm summary/report` on it and compare with §6;
   - fix whatever is still wrong (stats, time zone, replay lengths);
   - add a regression fixture built from what you learn (no private data in fixtures).
2. **Fix the replay length** using a real replay: inspect `metadata.json`
   (`total_ticks`, `chunks.*.duration`) and `arcade_replay_meta.json`, and count
   `next_tick` actions in the `cN.flashback` files (action ids in arcade files = enum
   ordinal; see §8). If metadata is truly wrong, derive the length from tick counts.
3. **Phase 2 "everything as one recording" (backlog #0b):** extract player paths from
   recordings via `move_entities` (§8). Store them like live-map tracks (`.jsonl`). Build the
   3D time-machine viewer: time slider over all history, paths, events pinned in 3D, replay
   coverage, terrain from the nearest backup. Reuse `mcsm/live.py`'s web server and
   `assets/web/live.html`.
4. Ask the owner to test the **Flashback markers and timelapse** in real Flashback (never done:
   formats were verified from source only). Ask for their Flashback version.
5. Ask what **"edit"** should mean in the unified view (notes? highlight marking for video?
   merging player names?).
6. Then: timelapse Phase B (Ledger or world snapshots, block by block), heatmaps, track
   playback, and the other backlog ideas.

## 8. Research findings (verified from source; do not redo)

### Flashback (github.com/Moulberry/Flashback, master, Nov 2026)
- A replay is a `.zip` in `<.minecraft>/flashback/replays/` holding `metadata.json`,
  `c0.flashback`, `c1.flashback`, …, optionally `icon.png` and `level_chunk_caches/`.
  `metadata.json`: `uuid`, `name`, `version_string`, `world_name`, `data_version`,
  `protocol_version`, `total_ticks`, `markers {"<tick>": {colour 0xRRGGBB, position?
  {position [x,y,z], dimension}, description?}}`, `chunks {"cN.flashback": {duration ticks,
  forcePlaySnapshot}}`. **No start time.**
- Chunk binary (big-endian): `u32 magic 0xD780E884`, `VarInt N` + N identifiers (VarInt length +
  UTF-8; the i-th has id i), `u32 snapshot_size` + snapshot, then records `VarInt id, u32 size,
  payload` until EOF.
- Actions: `flashback:action/next_tick` (one per tick), `…/real_time_clock_optional`
  (payload `0x00` + i64 epoch ms; non-zero first byte = delta form), `…/move_entities`,
  `…/create_local_player`, `…/game_packet` (version-specific), `…/configuration_packet`,
  `…/level_chunk_cached`, `…/accurate_player_position_optional`.
- `move_entities` payload: `VarInt levelCount`; per level: `ResourceKey dimension`,
  `VarInt count`; per entity: `VarInt id, f64 x, f64 y, f64 z, f32 yaw, f32 pitch, f32 headYaw,
  bool onGround`. Only **changed** entities are written each tick, so carry values forward.
- Editor timeline: `<.minecraft>/flashback/editor_states/<replay uuid>.json`. The exact minimal
  valid format, and why each field is required, is in `docs/FLASHBACK.md`. Key points: write
  `enabled: true`, explicit export ticks, and `history: {"entries": [], "position": 0}`; track ids
  are `CAMERA` / `TIMELAPSE`; interpolation is the enum name (`HOLD`). A file that fails to load
  is deleted by Flashback.

### ServerReplay 3.4.1 → arcade library (github.com/CasualChampionships/arcade, branch 26.3, module arcade-replay)
- Flashback writer: `io/writer/flashback/FlashbackWriter.kt`, `FlashbackChunkedWriter.kt`;
  `util/flashback/FlashbackMeta.kt`, `FlashbackAction.kt`.
- Writes the same `metadata.json` keys (`name` stays "Unnamed"; `world_name` from settings).
  `total_ticks` is absolute and `duration` per chunk is a delta (source looks correct, so the
  real files' 1-tick lengths are unexplained).
- Also writes **`arcade_replay_meta.json`**: `name` (player), `location`, `epoch_time_ms` (**save
  time**, not start), `accumulated_paused_time` (`HH:MM:SS`), `mods`, `settings`.
- Actions (**id = enum ordinal**, header lists them in this order): `action/level_chunk_cached`,
  `configuration_packet`, `create_local_player`, `game_packet`, `move_entities`, `next_tick`,
  `simple_voice_chat_sound_optional`, `encoded_simple_voice_chat_sound_optional`. **No
  real_time_clock, no accurate_player_position.** Namespace prefix unverified (probably
  `flashback:`). Snapshot size is written directly (no placeholder). 6000-tick chunks.
- `create_local_player` payload: `UUID, f64 x, f64 y, f64 z, f32 yaw, f32 pitch, f32 headYaw,
  Vec3 velocity, GameProfile, VarInt gamemode`. **No entity id.** The recorded player's
  entity id appears only in `move_entities`. Heuristic: the id whose first position equals
  this position. The `ExactEntityPosition.write` layout used in its `move_entities` is **not
  verified** against Flashback's reader layout above: check it with a real file.
- Output: `<recordings>/<player folder>/<date>/` raw folder → `<date>.zip`. Date pattern
  and time zone come from `DateTimeUtils.getFormattedDate` (arcade-utils), **not verified**
  (README example `2024-05-11--19-19-55`; treated as server local time).

### ReplayMod (.mcpr)
- Zip with `metaData.json` (`date` = start epoch ms, `duration` ms, `serverName`,
  `mcversion`, `players` uuids, `generator`), `markers.json` (`realTimestamp` ms),
  `recording.tmcpr`. Unfinished recordings: `<name>.mcpr.tmp/` folders.

### Minecraft 26.1 world layout (minecraft.wiki "Java Edition level format", history 26.1 snap6)
- `players/stats`, `players/advancements`, `players/data` (were `stats`, `advancements`,
  `playerdata`). Dimensions: `dimensions/minecraft/{overworld,the_nether,the_end}/{region,
  entities,poi}`.
- `level.dat`: `spawn {dimension, pos [x,y,z], yaw, pitch}`; `difficulty_settings {difficulty
  "hard", locked}`; game rules → `data/minecraft/game_rules.dat`; world-gen settings and seed →
  `data/minecraft/world_gen_settings.dat`; weather → `data/minecraft/weather.dat`. Day time's
  new location: unknown.

## 9. Starter prompt (paste into the new session)

> This repo is my Minecraft server tool "mcsm". All previous work is on the branch
> `claude/exciting-cannon-d0r8dm`. Start your working branch from it. Before doing anything,
> read `HANDOFF.md`, then `PROJECT_STATUS.md` and `CLAUDE.md`, and follow `CLAUDE.md`'s rules
> (update the docs, test, commit and push after every change). Then summarise where things
> stand and continue with the next steps in `HANDOFF.md` §7. I'll upload a slim copy of my latest
> backup for you to check.

## 10. Environment notes and pitfalls learned

- **Tags can't be pushed** from Claude cloud sessions → releases go through the GitHub UI.
- **tkinter**: the default `python3` (3.13) lacks it. Use `/usr/bin/python3.12` (python3-tk) with
  `xvfb-run -a` for GUI tests and screenshots (`import -window root file.png`).
- **Playwright/Chromium** is preinstalled. WebGL screenshots need
  `--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader`.
- three.js r160 was vendored with `npm pack three@0.160.0` (MIT licence in
  `mcsm/assets/web/vendor/`).
- PyInstaller builds on Linux for checking the spec (`installer/mcsm.spec`). The real Windows
  build is CI only (`installer/build_windows.ps1`).
- Research subagents (web fetch) worked well for reading mod source on GitHub. Ask for verbatim
  quotes and "not found" instead of guesses.
- Pitfalls already fixed (don't reintroduce them):
  - argparse treats `--area -100,…` as an option → `cli.normalise_argv`;
  - Tk objects garbage-collected on a worker thread stall tests → `gc.collect()` after destroying a root;
  - re-rendering list rows with innerHTML every update loses clicks → update rows in place;
  - temp extraction contexts must stay open while replays are used (`cli._OPEN`, GUI
    `server_stack`);
  - Flashback copies need a new uuid; never overwrite `editor_states` files;
  - selective extraction must count skipped region files from the zip listing.
