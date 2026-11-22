# History viewer: everything as one recording

`mcsm history` opens a 3D map in your browser with a **time slider over your server's whole
history**. Drag it to any moment to see who was online, where they walked, and what happened
(joins, deaths, chat, advancements). You can also see which recordings cover that moment.

```
mcsm history SERVER [MORE BACKUPS...]
```

In the app: load your backup(s), open the **History** tab, press **Open history viewer**.

## What you see

- **Players** walk along their paths as time moves. A solid marker means the position comes
  from a recording at that moment: their own, or someone else's they were in view of. A faded marker means the player was online (from the logs)
  and that is their last known position this session: a path point or where they logged in.
- **Trail**: where each player went in the last 1 min / 10 min / 1 hour / 1 day.
- **All paths**: every recorded path in the current dimension, faint. Shows where people
  spend their time.
- **Event pins**: deaths, chat, advancements and joins in the trail window, pinned at the
  player's position at that moment (when it is known).
- **Happening now**: those events as a list. Click one to fly to it.
- **Recordings at this moment**: each Flashback/ReplayMod recording covering the current time,
  and how far into it you are (e.g. `12:40`). In Flashback, open that replay and go to that
  time to see the moment in full. Click to copy the replay's path. Each one shows the
  **Minecraft version** it was recorded in: Flashback opens a recording only in that version.
- **Server at this moment** (with several backups): the Minecraft version, number of mods and
  datapacks from the backup that covers this time, and what changed since the backup before
  (mods added/removed/updated, datapacks, Minecraft version). Hover for the datapack names.

## The timeline

- One row per player who is active in the visible window. A **faded bar** means online (from
  the logs). A **solid bar** in the same colour means a recording. Recordings with no
  timeline inside (ServerReplay 3.4.x, see [FLASHBACK.md](FLASHBACK.md)) are drawn paler.
- Red ticks are deaths and crashes; grey ticks are other events; yellow lines are backups
  (when several are loaded).
- Click or drag to jump. **View** zooms the strip (everything, 30 days … 2 hours).
- **Play** moves time at the chosen speed (1×, 10×, 1 min/s, 10 min/s, 1 h/s).
  **Skip quiet time** jumps over periods when nobody was online. **Prev / Next** jump between
  log-ins. Keyboard: Space plays/pauses, ← → move 1 minute (with Shift: 1 hour), `I` / `O`
  mark a highlight's start / end.
- The page remembers the last time you looked at (in this browser only).

## Highlights for videos

Mark the moments you want in a video while you browse the history, then send them to
Flashback ready to export.

1. Move to where the moment starts and press **Start** (or the `I` key). Move to where it
   ends and press **End** (`O`). The span shows on the timeline.
2. Type a title and press **Save** (or Enter). The players online in that span are filled in.
3. Saved highlights are listed in the panel and shown as green bands on the timeline:
   **▶ Play** plays just that span, **Edit** loads it back for a new title or new Start/End,
   **Delete** removes it. Each one says how many recordings cover it, and whose.
4. **Export to Flashback** handles all highlights at once:
   - For every Flashback recording that covers a highlight (one per player's point of view),
     a **copy** appears in Flashback's replay list, named `NAME DATE (highlights)`. It has a
     green **▶ title** marker at each highlight's start and a red **■ title** marker at its
     end. Its editor has **one scene per highlight whose export range is already set**: open
     the copy, choose the scene, press Export.
   - A clip list, `<world> highlights.csv`, next to the highlights file: title, local
     start/end, length, players, and for each recording the in/out time to seek to. Use it
     for any video editor.
   - Copying takes a while for long recordings, which can be hundreds of MB each. Your original
     recordings are never changed, and existing Flashback timelines are never overwritten.

Highlights are saved in `Documents\mcsm-highlights\<world>.json`, or in the world's folder
(`highlights.json`) when you load a world from the [library](LIBRARY.md) (`--highlights FILE` to
change), so they're kept between sessions and whichever backups you load. Flashback's folder
is found automatically (`%APPDATA%\.minecraft\flashback`); give `--flashback-dir` for a
launcher with its own folder. If it isn't found, the copies go into a `flashback` folder
next to the highlights file: copy them into Flashback's `replays` folder, and the `.json`
editor timelines into its `editor_states` folder.

Not yet tried in a real Flashback: scenes that only set an export range (no camera
keyframes) are allowed by Flashback's code, but nobody has opened one in-game yet.

## Where positions come from

1. **Flashback recordings** (ServerReplay with `flashback` encoding, or the Flashback mod).
   `mcsm` reads the recorded player's movement straight from the replay files. No Minecraft
   version-specific decoding is needed. The first run reads every recording (about 1 second
   per 30 MB of replay data; the owner's 22 GB backup took a few minutes). Results are cached in
   `%LOCALAPPDATA%\mcsm\paths` (`--cache DIR` to change), so later runs are instant. The page
   fills in while this runs.
   - Besides the **recorded player**, every **other player in view** is tracked too. They
     are recognised by their UUID from the server files. In the player list their position
     says *seen by NAME* (whose recording it comes from). When a player was recording
     themselves at the same time, their own recording is used. Checked on the owner's
     backup: "seen" positions match the players' own recordings to about a block.
   - Mobs and other entities are not shown. Bedrock (Geyser) players without a UUID in the
     server files can't be recognised.
   - ServerReplay **3.4.0/3.4.1** recordings contain no movement, so they give no paths.
2. **Live-map recordings** (`mcsm live --record DIR`): `mcsm history SERVER --tracks DIR`.
3. **Log-in positions** from the logs (every join), used as "last known position".

The decoded paths use the same file format as live-map recordings, so they also work for
timelapses: `mcsm timelapse … --tracks "%LOCALAPPDATA%\mcsm\paths"`.

## Terrain: the world as it was

When you load several backups, **each backup's world is a terrain layer**. With **Terrain:
Follow the time slider** (the default) the map shows the newest backup taken at or before the
time on the slider (before the first backup: the first one). Drag back to May and the land
is as it was in May. Builds disappear and the explored area shrinks. Play forward and it
switches over at each backup (the yellow lines on the timeline). The **Terrain** menu can
also pin one backup, e.g. today's world under last spring's paths.

- The newest backup is built first (it's also what you see while the rest load), then the
  older ones, newest to oldest. The status line says how far it is.
- Regions that didn't change between backups (same file, size and time) are read **once** and
  shared, and switching backups only re-downloads the regions that differ. On the owner's
  May and November backups, 595 of May's 1,158 regions were unchanged.
- Regions are read in parallel (one worker process per CPU core, up to 8), nearest to spawn
  and log-in spots first, and each finished region is cached in `%LOCALAPPDATA%\mcsm\terrain`.
  The owner's 1,569-region world takes about 30 seconds the first time and about 4 seconds
  after that.
- Zip backups are unpacked (region files only) one at a time into a temporary folder that is
  deleted once their terrain is built.
- Each sample is one height and colour (`--terrain-detail`), so you see new buildings, cleared
  land and new paths, not single blocks.
- `--terrain newest` (or untick **Terrain from every backup** in the app) builds only the newest.
- The Nether surface is not drawn.

## Options

| Option | Meaning |
|---|---|
| `--tracks DIR_OR_FILE` | also show live-map recordings (repeatable) |
| `--no-replays` | don't read positions from recordings |
| `--highlights FILE` | where highlights are kept (default `Documents\mcsm-highlights\<world>.json`) |
| `--flashback-dir DIR` | Flashback data folder for exporting highlights (default: found automatically) |
| `--cache DIR` | where decoded paths are kept (default `%LOCALAPPDATA%\mcsm\paths`) |
| `--http-port N` | local port (default 8766; the next free one is used if busy) |
| `--terrain-detail auto\|1\|2\|4\|8\|16` | sample every N blocks |
| `--terrain all\|newest` | terrain from every backup (default; changes with the time slider) or only the newest |
| `--no-browser` | don't open the browser |
| `--tz`, `--latest-log-date` | as for every command |

The page is served on `http://127.0.0.1` only: it is not reachable from other computers.
