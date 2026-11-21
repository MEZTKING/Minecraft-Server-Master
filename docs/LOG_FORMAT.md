# Log parsing reference

Code: `mcsm/logs.py`. Tests: `TestLogLines` and `TestAnalyze` in `tests/test_mcsm.py`.

## Files read

- `logs/YYYY-MM-DD-N.log.gz` (and uncompressed `.log`), sorted by date then `N`.
- `logs/latest.log`, read last.
- **Ignored:** `debug.log`, `debug-N.log.gz` (duplicates at debug level), anything else.

## Line formats

| Server | Example |
|---|---|
| Vanilla | `[20:15:03] [Server thread/INFO]: Steve joined the game` |
| Fabric / Quilt | `[20:15:03] [Server thread/INFO] (Minecraft) Steve joined the game` |
| Paper / Spigot / Purpur / Folia | `[20:15:03 INFO]: Steve joined the game` |
| Forge / NeoForge | `[01May2024 20:15:03.123] [Server thread/INFO] [minecraft/MinecraftServer]: Steve joined the game` |

Lines that match none of these (stack traces, plugin banners) are skipped. Colour codes
(`§a`) and ANSI escapes are stripped before matching.

## Dating lines

A line only has a time of day. The date is worked out per file:

1. **Archives** – the date in the file name is the date of the file's **last** line.
   Minecraft's logging rolls the file at midnight and at each server start, naming the
   archive after the period it covers, so this holds in practice.
2. **`latest.log`** – its modification time (from the file system or the zip) gives the date
   of the last line. Override with `--latest-log-date`.
3. **Forge** lines contain their own date, which is used directly.

Walking the file, a time that jumps **backwards by more than one hour** counts as a midnight
roll-over. Lines before the last roll-over are dated one day earlier per roll-over. (Small
backwards jumps – clock corrections – are ignored.)

The resulting local time is converted to UTC with the server's time zone
(see README → Time zones; code: `LogClock` in `mcsm/timeutil.py`).

The *Files* tab of the HTML report lists every file, how it was dated, and its first/last time.

## Recognised messages

`NAME` = 1–32 chars of letters, digits, `_`, `.`, `*`, `-` (the last three cover
Geyser/Floodgate Bedrock prefixes).

| Event type | Pattern | Extra data |
|---|---|---|
| `server_start` | `Starting minecraft server version 1.20.4` | `version` |
| `server_ready` | `Done (4.512s)! For help, type "help"` | `startup_seconds` |
| `server_stop` | `Stopping server` / `Stopping the server` | |
| `join` | `NAME joined the game` / `NAME joined the game (formerly known as OLD)` | `formerly`, plus `position` `(x, y, z)` and `world` from the preceding `NAME[/ip:port] logged in with entity id N at (...)` line, and `uuid` from the `UUID of player NAME is <uuid>` line before that. The uuid links a player's old names to their current one (see DATA_MODEL.md, `names_seen`) |
| `leave` | `NAME left the game` | `reason` from the preceding `NAME lost connection: …` line |
| `chat` | `<NAME> text`, optionally prefixed `[Not Secure] ` | |
| `server_chat` | `[Server] text`, `[Rcon] text` (`/say` from console) | |
| `emote` | `* NAME text` (`/me`) – only for known players | |
| `advancement` | `NAME has made the advancement [T]` / `has completed the challenge [T]` / `has reached the goal [T]` | `title`, `kind` = task / challenge / goal |
| `command` | Paper: `NAME issued server command: /cmd` | `command` |
| `command` | Vanilla op feedback: `[NAME: Set the time to 1000]` – only for known players, `Server`, `Rcon` | `feedback` |
| `death` | `NAME <death phrase>…` – only for **known players** | `cause`, `killer`, `weapon` |
| `crash` | not from logs: one per `crash-reports/crash-YYYY-MM-DD_HH.MM.SS-server.txt` | description in `text` |
| `replay_marker` | not from logs: markers stored inside `.mcpr` files | `offset_ms` |

**`System chat: ` prefix (Minecraft 26.3):** system messages are logged as
`System chat: Steve joined the game`, `System chat: Steve was slain by Zombie`, and so on.
This covers joins, leaves, deaths, advancements, command feedback and "Stopping the server".
The prefix is removed before the patterns above are applied. Player chat (`<Steve> hi`) has
no prefix. Before this was handled, the owner's server lost every join, death and
advancement from late October 2026. One shutdown logs both "Stopping the server" and
"Stopping server"; stops within 10 seconds count once.

**Known players** are names from `usercache.json`/`ops.json`/`whitelist.json` plus anyone
seen joining or logging in earlier. This stops lines like `Nobody drowned` from a plugin
being counted as deaths.

**Death phrases** are the vanilla death-message verbs from 1.12 to 1.21 (`was slain by`,
`drowned`, `hit the ground too hard`, `fell from a high place`, `was blown up by`,
`tried to swim in lava`, `experienced kinetic energy`, …). The full list is
`DEATH_PHRASES` in `mcsm/logs.py`; add to it if a modded death message is missing.
`killer` is taken from `… by X`, `… while fighting X`, `… escaping X`, `… trying to hurt X`;
`weapon` from a trailing `using [Item]`.

## Sessions

Built by `build_sessions()` from the sorted event list:

- `join` opens a session. A second `join` for the same player without a `leave` closes the
  old one (`end_reason = rejoin`).
- `leave` closes it (`leave`).
- `server_stop` closes all open sessions (`server_stop`).
- `server_start` while sessions are still open means the server crashed or was killed:
  they are closed at the **last event before** the start (`server_restart`).
- Still open at the end of all logs: closed at the last event (`log_end`).

Session totals therefore depend on how complete your logs are. The game's own `play_time`
statistic (stats files) is the authoritative lifetime total; the report shows both.

## Privacy

The IP address in `logged in with entity id` lines is discarded during parsing and never
appears in any output.

## Known limitations

- Chat from plugins that rewrite the format (EssentialsChat, LuckPerms prefixes, Discord
  bridges) is only recognised if the log still contains `<NAME> text`.
- Whisper (`/msg`) contents are not logged by Minecraft.
- Log times have 1-second resolution.
- Logs in a language other than English (rare on servers) are not recognised.
