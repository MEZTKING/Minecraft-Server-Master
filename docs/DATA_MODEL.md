# Data model and export formats

Code: `mcsm/models.py` (classes), `mcsm/exporters/data.py` (JSON / SQLite / CSV).

All times are **UTC**. In JSON/SQLite/CSV they are ISO-8601 strings ending in `Z`
(`2024-05-01T18:05:00Z`).

## `ServerData`

| Field | Type | Contents |
|---|---|---|
| `root` | str | Absolute path that was parsed |
| `server_properties` | dict | `server.properties` (secrets masked as `********`) |
| `world` | dict | `level.dat` summary – see below |
| `players` | dict name→`PlayerProfile` | Keyed by lower-case name |
| `events` | list[`Event`] | Sorted by time |
| `sessions` | list[`Session`] | Sorted by start |
| `replays` | list[`Replay`] | Sorted by start |
| `log_files` | list[`LogFileInfo`] | Which logs were read and how they were dated |
| `mods` | list[dict] | `kind` (mod/plugin), `file`, `id`, `name`, `version`, `loader` |
| `timezone_info` | str | How log times were converted |
| `warnings` | list[str] | Things the user should check |
| `backups` | list[dict] | Merged archives only: per backup `label`, `path`, `time`, `version`, `data_version`, `level_name`, `seed`, `world_number`, `size_bytes`, counts, `first_event`, `last_event` |
| `player_history` | dict name→list | Merged archives only: per backup `backup`, `time`, `play_ticks`, `deaths`, `mob_kills`, `jumps`, `blocks_mined`, `items_crafted`, `walk_cm`, `advancements` |
| `sources` | dict | Merged archives only: backup label → extracted folder (replay paths start with the label) |

### `world`

`level_name`, `folder`, `seed`, `version_name`, `data_version`, `game_time_ticks`,
`game_time_hours`, `in_game_days`, `spawn` [x,y,z], `hardcore`, `difficulty`, `game_type`,
`raining`, `thundering`, `last_played_ms`, `game_rules` {}, `datapacks_enabled` [],
`brand` [], `dragon_killed`, `size_bytes`, `file_count`,
`dimensions` {name: {`region_files`, `bytes`, `approx_area_km2`}}.

## `Event`

| Field | Meaning |
|---|---|
| `time` | When it happened (UTC) |
| `type` | See table below |
| `player` | Player name, or null |
| `text` | Human-readable text (chat message text, death message, …) |
| `source` | File it came from, relative to the server root |
| `line` | 1-based line number in that file (null for non-log sources) |
| `data` | Type-specific extras (see [LOG_FORMAT.md](LOG_FORMAT.md)) |

Event types: `server_start`, `server_ready`, `server_stop`, `crash`, `join`, `leave`, `chat`,
`server_chat`, `emote`, `death`, `advancement`, `command`, `replay_marker`.

## `Session`

`player`, `start`, `end` (null if unknown), `seconds`, `end_reason`
(`leave` / `rejoin` / `server_stop` / `server_restart` / `log_end`), `join_position`.

## `PlayerProfile`

| Field | Source |
|---|---|
| `uuid`, `name`, `names_seen` | usercache + logs |
| `stats` | `{category: {key: value}}` from `stats/<uuid>.json`, `minecraft:` prefix removed |
| `advancements` | `{id: {done, completed, criteria}}`; recipe unlocks are left out |
| `playerdata` | `position`, `dimension`, `health`, `food`, `xp_level`, `xp_total`, `score`, `game_mode`, `flying`, `spawn`, `last_death` {dimension,pos}, `inventory` {item: count}, `ender_chest` {item: count}, `inventory_slots_used` |
| `is_op`, `is_whitelisted`, `is_banned` | ops.json / whitelist.json / banned-players.json |
| `first_seen`, `last_seen`, `session_count`, `session_seconds`, `chat_messages`, `deaths_logged` | derived from logs |

### Stat categories and units

Categories: `custom`, `mined`, `crafted`, `used`, `broken`, `picked_up`, `dropped`, `killed`,
`killed_by`.

| Stat keys | Unit | Convert |
|---|---|---|
| `play_time` (1.17+), `play_one_minute` (older), `time_since_death`, `time_since_rest`, `sneak_time`, `total_world_time` | ticks | ÷ 20 = seconds, ÷ 72000 = hours |
| `*_one_cm` (`walk_one_cm`, `sprint_one_cm`, `fly_one_cm`, `boat_one_cm`, …) | centimetres | ÷ 100 = blocks/metres |
| `damage_dealt`, `damage_taken`, `damage_*` | tenths of half-hearts | ÷ 10 = half-hearts |
| everything else | count | |

Pre-1.13 stat files (`stat.playOneMinute`, `stat.mineBlock.minecraft.stone`, …) are
converted to the same layout.

## `Replay`

`path`, `start`, `end`, `duration_ms`, `server_name`, `mc_version`, `generator`,
`players` (UUIDs, ReplayMod only), `player_names`, `recorded_player` (from ServerReplay's
`players/<uuid-or-name>/` folder, shown as a name), `markers` [{`offset_ms`, `name`,
`position`, `dimension` (Flashback only)}], `size_bytes`, `raw_meta`,
`format` (`replaymod` / `flashback`), `start_source` (`metadata` / `real-time-clock` /
`save-time` / `file-name` / `file-time`), `unfinished` (true for in-progress/crashed recording folders).

## SQLite schema (`mcsm export --sqlite`)

```sql
meta(key, value)                     -- root, generated_at, timezone, server_properties (JSON), world (JSON), warnings (JSON)
players(name, uuid, first_seen, last_seen, session_count, session_seconds, play_time_ticks,
        chat_messages, deaths_logged, is_op, is_whitelisted, is_banned, playerdata_json)
events(id, time, epoch_ms, type, player, text, source, line, data_json)
sessions(id, player, start, end, seconds, end_reason)
stats(player, category, key, value)
advancements(player, advancement, done, completed)
replays(path, start, end, duration_ms, server_name, mc_version, generator, recorded_player, players, size_bytes)
log_files(path, date, date_source, lines, first_time, last_time)
```

Example questions:

```sql
-- Who died most, according to the logs?
SELECT player, COUNT(*) n FROM events WHERE type='death' GROUP BY player ORDER BY n DESC;

-- What killed people?
SELECT json_extract(data_json,'$.killer') killer, COUNT(*) FROM events
WHERE type='death' GROUP BY killer ORDER BY 2 DESC;

-- Diamonds mined per player
SELECT player, value FROM stats WHERE category='mined' AND key='diamond_ore' ORDER BY value DESC;

-- Hours online per day (UTC days)
SELECT substr(start,1,10) day, ROUND(SUM(seconds)/3600.0,1) hours FROM sessions GROUP BY day;
```

## CSV (`mcsm export --csv DIR`)

`events.csv`, `sessions.csv`, `players.csv`, `stats.csv` – UTF-8 with BOM so Excel shows
special characters correctly.

## JSON (`mcsm export --json`)

The whole `ServerData`, field for field as above.

## Sync marker CSV/JSON (`mcsm sync`)

`index`, `video_seconds`, `video_time` (`HH:MM:SS.mmm`), `type` (event type, `beat` or
`bar`), `player`, `label`, `real_time_utc`, `snapped_from_seconds`, `colour`. The JSON
version also has a `meta` block with the timeline start, length, speed, fps and music grid.
