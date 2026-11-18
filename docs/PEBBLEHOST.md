# Getting your server files (PebbleHost and others)

`mcsm` works on a **copy** of your server files on your own computer. It never connects to
the server and never changes anything.

## Option A – download a zip (simplest)

1. Log in to the PebbleHost game panel and open your server.
2. **Stop the server** first if you can. A running server keeps writing to `latest.log`
   and the player files, so a copy taken while it runs may be a few minutes stale (harmless,
   but the newest events may be missing).
3. Open **File Manager**.
4. Select the folders/files listed below, choose **Compress / Archive** to create a zip, then
   download the zip. (Panels change; if you don't see a compress option, use SFTP below.)
5. Run `mcsm report your-download.zip`. You don't need to unzip it – `mcsm` finds the
   server folder inside the zip automatically, even if it's wrapped in an extra folder.

## Option B – SFTP (best for regular, incremental downloads)

PebbleHost shows SFTP login details in the panel (usually under *Settings / SFTP*). Connect
with a client such as **FileZilla** or **WinSCP** and download the folders below. Next time,
only new files are copied (choose "skip if identical" / "overwrite if newer").

## What to download

| Path | Size | Why |
|---|---|---|
| `logs/` | small | **Essential.** Every event and session comes from here. |
| `world/stats/` | tiny | Lifetime statistics per player |
| `world/advancements/` | tiny | Advancement dates; also how the server time zone is detected |
| `world/playerdata/` | small | Position, inventory, health, XP |
| `world/level.dat` | tiny | World info (seed, version, age) |
| `usercache.json`, `ops.json`, `whitelist.json`, `banned-players.json` | tiny | Names and roles |
| `server.properties` | tiny | Tells `mcsm` the world folder name |
| `recordings/` | **large** | ServerReplay recordings – only the ones you want to edit |
| `mods/` or `plugins/` | medium | Optional: mod/plugin list in the report |
| `crash-reports/` | small | Optional: crashes on the timeline |
| `world/region/` | **very large** | Optional: only used for the "explored area" estimate |

If your world is not called `world`, use the folder named by `level-name=` in
`server.properties`. On Paper/Spigot the Nether and End are separate folders
(`world_nether`, `world_the_end`) – only needed for the explored-area estimate.

## Keeping the full history

Minecraft rolls `latest.log` into `logs/YYYY-MM-DD-N.log.gz` every day and on every restart.
Some hosts eventually delete old logs or you may wipe them to save space. To keep a complete
history, download `logs/` regularly into the **same** local folder – archive file names are
unique, so new ones just add to the collection. `latest.log` is overwritten each time, which is
fine: it is renamed into an archive at the next restart and you'll get it then.

## Dating `latest.log`

Archive names contain the date, but `latest.log` doesn't. `mcsm` uses the file's
**modification time** as the date of its last line. Most download methods keep that time; if
yours doesn't (the report's *Files* tab shows how each file was dated), pass
`--latest-log-date YYYY-MM-DD`.

## Other hosts

Nothing here is PebbleHost-specific: Apex, Shockbyte, BisectHosting, Aternos (via world
download + logs), a home server, or a single-player world with a `logs/` folder all have
the same layout.
