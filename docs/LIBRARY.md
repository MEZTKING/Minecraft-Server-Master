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

1. **Library folder**: `Documents\mcsm-library` by default. Choose another one (e.g. a big
   drive) with *Browse…*.
2. **Adding files**:
   - **Link** (default): the library points at the same data on disk with hard links. It's
     instant and uses no extra space, and the originals are not changed. Afterwards you can
     delete the original folders and nothing is lost: the data stays in the library. Only
     works on the same drive; on another drive it copies.
   - **Copy**: a full copy.
   - **Move**: the files are moved into the library (instant on the same drive).
3. **Import backup folder…** for an unpacked backup; **Import backup file (.zip / .tgz)…** for a
   download. Archives are unpacked straight into the library once, so you never unpack them
   again.
4. **Add recordings…** adds single recordings to the selected world. **Add my Flashback
   replays** adds everything in your Flashback replay folder (`%APPDATA%\.minecraft\flashback\replays`).
5. Select a world and press **Load this world** (or double-click it).

## On the command line

```
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

## What happens on import

- **Which world:** a backup goes to the world with the same level name and seed. A new level
  name, or a different seed, makes a new world folder.
- **Which date:** the backup's folder is named after its newest data (the world's last-played
  time or `latest.log`). Importing a backup whose time is already in the library is skipped,
  so importing the same download twice does nothing.
- **Recordings** (ServerReplay `recordings/`, ReplayMod, Flashback) go into the world's
  `recordings` folder. One that's already there (same path and same content) is not stored
  again. One with the same name but different content is kept as `name (2).zip`.
- **Files that didn't change** between backups (same path and identical content, e.g. old
  region files and log archives) are stored once and hard-linked into each backup.
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
- Singleplayer worlds (`.minecraft\saves`) are not imported yet: they have no logs.
