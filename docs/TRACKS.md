# Position tracks from replays

`mcsm tracks` reads **where the recording player went** out of every Flashback replay and
saves it as a *track*: a list of positions over time. ServerReplay records one replay per
player, so your recordings together tell you where everybody was whenever they were recorded.
You don't need the live map running while people play.

Tracks are used by:

- **the timelapse** ([TIMELAPSE.md](TIMELAPSE.md)): to know when someone was at your area. It
  does this by itself (see below), or you pass the tracks folder with `--tracks`;
- the planned "everything as one recording" 3D history map (`PROJECT_STATUS.md` backlog #0b).

## Run it

**App:** load the download → **Flashback** tab → **Decode positions from all replays**. The
tracks go to `Documents/mcsm-replay-tracks`, and the timelapse's *Position recordings* field is
set to that folder.

**Command line:**

```bash
mcsm tracks my-server.zip -o replay-tracks
```

```
  recordings/players/Recordings for_ Alex (…)/2026-05-03--20-05-30.zip: Alex  0:02:00  14 positions
  ...
Replays     : 762 (740 with positions, 0 already decoded earlier)
Samples     : 183214
Tracks      : C:\Users\you\replay-tracks
```

| Option | Meaning | Default |
|---|---|---|
| `-o DIR` | Output folder: one `.jsonl` file per replay | `replay-tracks` |
| `--replay N` | Only this replay (number from `mcsm replays`, path or part of its name) | all |
| `--since` / `--until` | Only replays in this time range (`YYYY-MM-DD [HH:MM:SS]`, your local time unless `--tz`) | all |
| `--force` | Decode again even if a track file already exists | off |

Decoding reads the whole replay, so it takes a while on hundreds of replays. Files already in
the output folder are **reused**: run it again after adding new backups and only the new
replays are decoded. With several backups (`mcsm tracks A.zip B.zip …`) every replay is read
once.

## Automatic use in the timelapse

Without `--tracks`, `mcsm timelapse` decodes the Flashback replays that **overlap the chosen
replay in time** (every player's recordings from then) and uses those positions. If none has
positions, it falls back to the log sessions ("anyone online"). The plan says which source it
used, e.g. `Activity : 2 periods from positions decoded from 3 replays (412 samples)`. Turn
this off with `--no-replay-tracks`.

## The track format

The same as the live map's recordings (`mcsm live --record`), one JSON object per line:

```json
{"t":1714759530050,"source":"replay","players":[{"name":"Alex","x":110.01,"y":70.0,"z":0.0,"yaw":90.0,"pitch":10.0,"dimension":"minecraft:overworld"}]}
```

`t` is UTC milliseconds. A position is written when the player moved at least 0.1 blocks
(at most one per tick), and at least every 30 seconds while they stand still.

## How it works and limits

- Only the **player who made the recording** is tracked. Other players and mobs in the replay
  have entity ids but no names in the version-independent part of the file. Their own
  recordings cover them.
- The player is found by matching the start position (`create_local_player`) with the first
  entity movement nearby (`move_entities`). A player who never moves during a whole 5-minute
  chunk is not found in that chunk.
- Times are the replay's start time + ticks. If the recording was paused, later positions drift
  by the paused time. Replays whose start comes only from the file time are less precise
  (`mcsm replays` shows the source).
- ReplayMod (`.mcpr`) replays are not decoded (their packets change with every Minecraft
  version).
- Not yet checked against a real ServerReplay recording. The byte layout was verified from the
  Flashback and ServerReplay (arcade) source code.
