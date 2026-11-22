# The library: all your backups and recordings in one place

Backups pile up everywhere: `.tgz` and `.zip` downloads from PebbleHost, folders you unpacked,
copies on other drives, and every backup contains the same recordings again. The **library**
keeps them in one tidy folder:

```
Documents\mcsm-library\
  Main_world\                      one folder per world (level name + seed)
    backups\
      2026-05-19 14-19\            one dated folder per backup (server files, no recordings)
      2026-11-20 08-16\
    recordings\                    every backup's recordings, each stored once
      recordings\players\<player>\<date>.zip
      imported\                    recordings you added yourself (e.g. Flashback client replays)
    highlights.json                history-viewer highlights for this world
```

Then **load the whole world with one click**: all its backups and all its recordings, in every
tab and command.

## In the app

Open the **Library** tab.

1. **Library folder**: `Documents\mcsm-library` by default. Choose another one with
   *Browse…*. For big collections, put it **on the same drive as your backups** (e.g.
   `F:\Game Backup\mcsm-library`): unpacking and de-duplicating stay on that drive.
2. **Adding files**:
   - **Link** (default): the library points at the same data on disk with hard links. It's
     instant and uses no extra space, and the originals are not changed. Afterwards you can
     delete the original folders and nothing is lost: the data stays in the library. Only
     works on the same drive; on another drive it copies.
   - **Copy**: the originals are only read, never touched. Only what isn't a duplicate is
     stored: a file identical to one already in the library (another backup, or the same
     recording) is stored once. Use this to keep your original backups exactly as they are.
   - **Move**: the files are moved into the library (instant on the same drive).
3. **Import a whole folder of backups…**: point it at the folder where you keep all your
   downloads, however messy (see [below](#importing-a-whole-folder)). It shows what it found
   and asks before importing anything. This always copies, so your originals are only read.
   **Import backup folder…** is for one unpacked backup; **Import backup file (.zip / .tgz)…**
   for one download. Archives are unpacked straight into the library once, so you never unpack them
   again.
4. **Add recordings…** adds single recordings to the selected world. **Add my Flashback
   replays** adds everything in your Flashback replay folder (`%APPDATA%\.minecraft\flashback\replays`).
5. Select a world and press **Load this world** (or double-click it).

## On the command line

```
mcsm library scan "F:\Game Backup\Minecraft Divers"          (shows the plan, changes nothing)
mcsm library import-all "F:\Game Backup\Minecraft Divers" --library "F:\Game Backup\mcsm-library"
mcsm library import "May19_2026\home" backup-2026-11-20.tgz
mcsm library add-recordings "%APPDATA%\.minecraft\flashback\replays" --world Main_world
mcsm library list
mcsm history "%USERPROFILE%\Documents\mcsm-library\Main_world"
```

A library world folder works as SERVER for **every** command (`summary`, `report`, `history`,
`flashback`, …). A library folder with only one world also works.

| Option | Meaning |
|---|---|
| `--library DIR` | library folder (default `Documents\mcsm-library`) |
| `--mode auto\|link\|copy\|move` | auto (default): hard links on the same drive, else copy |
| `--world NAME` | put the backup into this world folder (default: matched by level name and seed) |
| `--force` | import even if a backup from the same time is already there |

## Importing a whole folder

Point *Import a whole folder of backups…* (or `mcsm library scan` / `import-all`) at the folder
with all your downloads. It looks at **what is inside** each file and folder, not at file names or
which company made the backup, so it works with any host:

| What you have | Examples | How it's recognised |
|---|---|---|
| A full server backup as an archive | PebbleHost `.tgz` (server inside `home/servers/<id>/`), Pterodactyl-panel hosts (BisectHosting, Apex, Shockbyte, …) `.tar.gz`, `.zip` downloads | `.zip`: its file list; `.tgz` / `.tar.gz` / `.tar`: checked when unpacked |
| An unpacked server folder | anything you unpacked yourself | `server.properties` or `logs/` |
| Only the world | Aternos and Realms world downloads, singleplayer saves (`.minecraft\saves\<world>`) | `level.dat` + region/player folders |
| Recordings | ServerReplay `recordings/`, Flashback replays, ReplayMod `.mcpr` | their metadata files |

Before importing it shows a plan:

- **Backups to import**, oldest first.
- **Copies of a backup, skipped:** an archive next to the folder you unpacked it into
  (`A.tgz` + `A\…`), a `.tar` next to its `.tgz`, and byte-identical archives in different folders.
  The fastest copy is used: an unpacked folder, then `.tar`, then `.zip` / `.tgz`. If it can't
  tell that two copies are the same backup, it imports both, and the second is then skipped
  because a backup from the same moment is already there. Nothing is lost either way; at most a
  little time is spent unpacking.
- **Recordings outside the backups** (e.g. a folder where you collected all recordings): added
  to the world most backups went into; ones already in the library are skipped.
- **Can't be read:** `.7z` and `.rar`. Unpack them (e.g. with 7-Zip) and scan again.
- **Not backups, ignored:** videos, mods (`.jar`), pictures and everything else, with a total size.
- Nothing inside a server's own folder is treated as a separate backup (for example a backup
  mod's `backups\*.zip`).

World-only backups get a `server.properties` with just `level-name` written into their library
folder, so every tool (statistics, terrain, history) works on them. They have no logs, so they
add world, stats and inventories, not events.

## What happens on import

- **Which world:** a backup goes to the world with the same level name and seed. A new level
  name, or a different seed, makes a new world folder.
- **Which date:** the backup's folder is named after its newest data (the world's last-played
  time or `latest.log`). Importing a backup whose time is already in the library is skipped,
  so importing the same download twice does nothing.
- **Recordings** (ServerReplay `recordings/`, ReplayMod, Flashback) go into the world's
  `recordings` folder. One that's already there (same file name and identical content, even
  under a differently named player folder, since ServerReplay changed its folder names between
  versions) is not stored again. One with the same name but different content is kept as `name (2).zip`.
- **Files that didn't change** between backups (same path and identical content, e.g. old
  region files and log archives) are stored once and hard-linked into each backup.
- **Added recordings** (`add-recordings` / *Add recordings…*) keep their folder layout under
  `recordings\imported\<folder name>\`, so `players\<uuid>\` folders still say whose
  recording it is; ones already in the world are skipped.
- `.7z` archives aren't supported (Python has no 7-Zip reader): unpack them first and import
  the folder.
- Each backup folder has an `mcsm-backup.json` with where it came from, when it was imported,
  the Minecraft version and the counts.
- Archive safety: entries that would land outside the library, links and devices in a `.tgz`
  are never unpacked.

On the owner's PC: the May (14.5 GB) and November (39.2 GB) backup folders were imported in
15 seconds with **no extra disk space** (link mode). The result was 978 recordings in one place,
and 2,847 of November's files are shared with May. Loading the library world gives exactly
the same history as loading both original folders.

## Good to know

- With **link** mode the library and the original share the same data. Don't run a Minecraft
  server from the original folder afterwards (it would change the library's copy too). Delete
  the original whenever you like.
- Windows Explorer shows each backup at its full size, even though shared files are only
  stored once. *Properties* of the whole library folder therefore overstates the space used.
- Path and terrain caches stay in `%LOCALAPPDATA%\mcsm` and are reused: moving recordings
  into the library does not make them decode again.
- Singleplayer worlds (`.minecraft\saves\<world>`) can be imported like any backup (they
  count as world-only: no logs, so no events).
