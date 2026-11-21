# Live 3D map

See where everyone is **right now** on a 3D map in your browser: a marker, name tag and
facing arrow for each player, 5-minute movement trails, health/food/XP, join/leave pop-ups,
click-to-follow, and Overworld / Nether / End switching.

- **Player positions** come live from the running server over **RCON**, Minecraft's built-in
  remote console. No mods or plugins are needed.
- **The landscape** is drawn from the world in your **last download** (`world/region/*.mca`). New
  builds appear after your next download; players are always live.

Code: `mcsm/live.py` (polling and web server), `mcsm/rcon.py` (RCON client),
`mcsm/terrain.py` (region files to terrain), `mcsm/assets/web/live.html` (three.js viewer).

## 1. Turn on RCON on the server

In `server.properties` on your host (PebbleHost: *File Manager → server.properties*), set:

```properties
enable-rcon=true
rcon.port=25575
rcon.password=<a long random password>
```

Restart the server. Then check your host allows connections to that port:

- Many hosts (PebbleHost included) only open the ports allocated to your server. If
  `mcsm` can't connect, look in the panel for **additional ports / port allocation**, add one,
  and set `rcon.port` to it. If your plan can't add a port, ask the host's support whether
  RCON is possible.
- Use the server's address **without** the game port, e.g. `123.45.67.89` (not
  `123.45.67.89:25565`). The app strips a game port if you paste one.

> **Security.** RCON is unencrypted and gives full operator control. Use a long random
> password (not your panel password), don't share it, and turn RCON off again if you stop
> using the live map. `mcsm` only ever sends read-only commands (`list` and
> `data get entity …`), but anyone with the password could send anything.

## 2. Start the map

**In the app:** load your server download first (this fills in the RCON port and password from
its `server.properties` and provides the terrain). Then go to the **Live map** tab, type the server
address, and click **Start live map**. Your browser opens the map. Keep the app open while you
watch; **Stop**, or closing the app, ends it.

**Command line:**

```bash
mcsm live my-server.zip --host 123.45.67.89
```

The password is taken from the download's `server.properties`, from the
`MCSM_RCON_PASSWORD` environment variable, or asked for. It's never accepted as a
command-line argument, because those are visible to other programs.

| Option | Meaning | Default |
|---|---|---|
| `--host` | Server address | required (unless `--demo`) |
| `--rcon-port` | RCON port | `rcon.port` from the download, else 25575 |
| `--interval` | Seconds between updates | 1 |
| `--terrain-detail` | Sample every N blocks (`1` = full detail) | `auto` (keeps big worlds smooth) |
| `--record DIR` | Also save every update to `DIR/YYYY-MM-DD.jsonl` | off |
| `--http-port` | Local port for the map page | 8765 (or the next free one) |
| `--demo` | Simulated players walking around, no server needed | off |
| `--no-browser` | Don't open the browser | off |

**Try it without a server:** `mcsm live my-server.zip --demo`, or **Try demo** in the app.

The map is served on `http://127.0.0.1:<port>/`, so it is only reachable from your own computer.

## 3. Using the map

| Action | How |
|---|---|
| Rotate / pan / zoom | Drag / right-drag (or two fingers) / scroll |
| Follow a player | Click their name in the list (click again to stop) |
| Fit everyone in view | **Show everyone** |
| Other dimensions | Dimension menu. Following a player switches automatically. |
| Hide trails | Untick **Trails** |

Markers get bigger as you zoom out so players stay visible. Between updates, markers glide
smoothly instead of jumping.

## How it works

Every `--interval` seconds `mcsm` sends, over one RCON connection:

```
list                                   -> who is online
data get entity <name> Pos             -> [x, y, z]
data get entity <name> Rotation        -> [yaw, pitch]
data get entity <name> Dimension       -> "minecraft:overworld"
data get entity <name> Health | foodLevel | XpLevel | playerGameType   (every 5th update)
```

All of an update's commands are sent at once (pipelined), so an update costs roughly one
network round trip whatever the number of players. If the connection drops, the map shows
*Reconnecting…* and retries with growing delays (up to 30 s).

Terrain: for each region file, the chunks' `MOTION_BLOCKING` heightmap gives the surface
height, and the block at that height (decoded from the section's palette) gives an approximate
colour. Supported worlds: Minecraft 1.16 and newer. Worlds bigger than ~12 regions are sampled
every 2–32 blocks automatically. The Nether isn't drawn because its bedrock roof hides the
surface, but Nether players are still shown, on a grid. Regions nearest spawn are built
first; big worlds (over a thousand regions) take a long time to finish, so far-away land
appears last. Minecraft 26.3's shorter palette format is supported, and a region that can't
be read is skipped.

## Recorded tracks

With `--record` (or the **Save positions** tick box), each update is appended to
`YYYY-MM-DD.jsonl`:

```json
{"t": 1714762800000, "players": [{"name": "Steve", "x": 12.5, "y": 64.0, "z": -3.2, "yaw": 90.0, "pitch": 0.0,
  "dimension": "minecraft:overworld", "health": 20.0, "food": 20, "xp_level": 30, "game_mode": "survival"}]}
```

`t` is Unix milliseconds (UTC). These files feed the **activity-aware timelapse**
([TIMELAPSE.md](TIMELAPSE.md)): keep recording on while people play so the timelapse knows
who was at your base, and when. They can also be shown in the history viewer
(`mcsm history SERVER --tracks DIR`, [HISTORY.md](HISTORY.md)).

## Troubleshooting

| Message | Fix |
|---|---|
| `cannot connect … Is enable-rcon=true and is the RCON port open?` | RCON is off, the port is wrong, or the host doesn't expose it (see step 1) |
| `RCON password rejected` | Password differs from `rcon.password` on the *running* server (restart after editing) |
| `RCON server did not answer in time` | Server lagging or overloaded; try `--interval 3` |
| No terrain | Load a download that includes `world/region/`; Minecraft 1.16+ |
| Players float or sink | Terrain is from an older download, or detail is reduced (`--terrain-detail 1`) |

## Alternative: BlueMap

If you'd rather have a polished, always-on web map with full 3D block rendering, look at
**BlueMap**, a free plugin/mod that runs on the server itself and also shows live players. It
needs to be installed on the server and a web port exposed. `mcsm live` needs neither, which
is why it's built in here.
