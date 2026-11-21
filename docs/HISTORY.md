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
  from a recording at that moment. A faded marker means the player was online (from the logs)
  and that is their last known position this session: a path point or where they logged in.
- **Trail**: where each player went in the last 1 min / 10 min / 1 hour / 1 day.
- **All paths**: every recorded path in the current dimension, faint. Shows where people
  spend their time.
- **Event pins**: deaths, chat, advancements and joins in the trail window, pinned at the
  player's position at that moment (when it is known).
- **Happening now**: those events as a list. Click one to fly to it.
- **Recordings at this moment**: each Flashback/ReplayMod recording covering the current time,
  and how far into it you are (e.g. `12:40`). In Flashback, open that replay and go to that
  time to see the moment in full. Click to copy the replay's path.

## The timeline

- One row per player who is active in the visible window. A **faded bar** means online (from
  the logs). A **solid bar** in the same colour means a recording. Recordings with no
  timeline inside (ServerReplay 3.4.x, see [FLASHBACK.md](FLASHBACK.md)) are drawn paler.
- Red ticks are deaths and crashes; grey ticks are other events; yellow lines are backups
  (when several are loaded).
- Click or drag to jump. **View** zooms the strip (everything, 30 days … 2 hours).
- **Play** moves time at the chosen speed (1×, 10×, 1 min/s, 10 min/s, 1 h/s).
  **Skip quiet time** jumps over periods when nobody was online. **Prev / Next** jump between
  log-ins. Keyboard: Space plays/pauses, ← → move 1 minute (with Shift: 1 hour).
- The page remembers the last time you looked at (in this browser only).

## Where positions come from

1. **Flashback recordings** (ServerReplay with `flashback` encoding, or the Flashback mod).
   `mcsm` reads the recorded player's movement straight from the replay files. No Minecraft
   version-specific decoding is needed. The first run reads every recording (about 1 second
   per 30 MB of replay data; the owner's 22 GB backup took a few minutes). Results are cached in
   `%LOCALAPPDATA%\mcsm\paths` (`--cache DIR` to change), so later runs are instant. The page
   fills in while this runs.
   - Only the **recorded player** is tracked from each recording. Other players visible in it
     are not identified yet.
   - ServerReplay **3.4.0/3.4.1** recordings contain no movement, so they give no paths.
2. **Live-map recordings** (`mcsm live --record DIR`): `mcsm history SERVER --tracks DIR`.
3. **Log-in positions** from the logs (every join), used as "last known position".

The decoded paths use the same file format as live-map recordings, so they also work for
timelapses: `mcsm timelapse … --tracks "%LOCALAPPDATA%\mcsm\paths"`.

## Terrain

The landscape comes from the **newest** backup's world. Big worlds take a long time to read
(the owner's 1,569 regions take well over an hour), so regions near spawn and where people log
in are built first and the rest follows in the background. The Nether surface is not drawn.

## Options

| Option | Meaning |
|---|---|
| `--tracks DIR_OR_FILE` | also show live-map recordings (repeatable) |
| `--no-replays` | don't read positions from recordings |
| `--cache DIR` | where decoded paths are kept (default `%LOCALAPPDATA%\mcsm\paths`) |
| `--http-port N` | local port (default 8766; the next free one is used if busy) |
| `--terrain-detail auto\|1\|2\|4\|8\|16` | sample every N blocks |
| `--no-browser` | don't open the browser |
| `--tz`, `--latest-log-date` | as for every command |

The page is served on `http://127.0.0.1` only: it is not reachable from other computers.
