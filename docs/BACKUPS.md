# Combining several backups into one history

Give `mcsm` several backups (zips or folders, any Minecraft versions) and it merges them into
**one** history: every log event once, every replay, the newest player stats, and each
backup's stats kept as a snapshot so you can see progress over time.

```bash
mcsm report backup-2025-06.zip backup-2025-12.zip backup-2026-05.zip -o everything.html
```

Every analysis command accepts several backups: `summary`, `report`, `export`, `events`,
`replays`, `sync`, `flashback`, `timelapse`. **In the app:** *Zip file…* lets you select several
zips at once, and **+ Add backup…** adds more to the list. The status line then says
"N backups merged".

To keep many backups organised, import them into the **library** once
([LIBRARY.md](LIBRARY.md)) and load the world's folder instead: it stands for all its backups
plus all their recordings, and PebbleHost `.tgz` downloads are unpacked there once.

The order you give them in doesn't matter. They are sorted by their newest data (last log line
or the world's last-played time).

## What gets merged, and how

| Data | Rule |
|---|---|
| **Log events** | Combined; an event that appears in several backups (same time, type, player and text) is kept once. Sessions are rebuilt from the merged events. |
| **Time zone** | Learned from the advancement files of *all* backups, so every backup's log times are converted the same way. |
| **Player stats, advancements, inventory** | From the newest backup that has them (stats are lifetime totals). |
| **Progress over time** | Each backup's numbers per player (play time, blocks mined, deaths, mob kills, crafted, distance, advancements, jumps) → **Backups** tab chart. |
| **Replays** | Combined, de-duplicated by file name + start time. Paths start with the backup's name. |
| **World info, mods, server.properties** | From the newest backup. |
| **Worlds** | A different level name, or a different seed (when both are known), counts as a new world. The Backups tab numbers them, and you get a warning, because stats then come from the newest world. |

## Different Minecraft versions

Each backup is read with whatever layout it has: 1.13–1.21 (`world/stats`, …) or 26.1+
(`world/players/stats`, `world/dimensions/minecraft/…`, `data/minecraft/*.dat`). The Overview's
**Server versions** table lists every version the server ran, from the version logged at each
start. `level.dat` only records the *last* version that saved the world.

## Big backups

Zip backups are read **selectively**: logs, player files, world info, and only the small
metadata of each replay are extracted to a temporary folder. Region files (usually most of
the size) are never unpacked; world size and explored area are read from the zip's file
list. A full replay is extracted only when it's actually needed (Flashback copies,
timelapses). The temporary files are removed when the command ends or the app loads
something else / closes. The live map extracts the newest backup's region files for its
terrain. The history viewer extracts each backup's region files in turn (one backup at a
time) to show the world as it was at each backup; see HISTORY.md.

## Backups tab (report)

- One row per backup: when it was taken, world version, world number and name, size,
  players, events, logs, replays.
- **Progress across backups**: pick a player and a statistic to see it per backup, with a
  table of all values.
