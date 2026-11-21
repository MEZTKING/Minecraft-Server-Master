# Working in Flashback's editor

[Flashback](https://github.com/Moulberry/Flashback) is a replay mod with an in-game editor
(timeline, camera keyframes, timelapse, export). `mcsm` can feed it directly:

| What | Command / app | Result in Flashback |
|---|---|---|
| **Server events as markers** | `mcsm flashback` / Flashback tab → *Copy replay with markers* | Deaths, chat, joins and advancements appear as coloured markers on the replay's timeline |
| **Area timelapse** | `mcsm timelapse` / Flashback tab → *Create timelapse* | A fixed camera and timelapse keyframes, ready to export. See [TIMELAPSE.md](TIMELAPSE.md) |
| **Position tracks** | `mcsm tracks` / Flashback tab → *Decode positions from all replays* | Where each recorded player went, read from the replays. See [TRACKS.md](TRACKS.md) |

**Your original replay is never changed.** `mcsm` writes a **copy** whose name ends in
`(mcsm)` or `(timelapse)`, with a **new replay ID**. Flashback keeps editor timelines per replay
ID, so the copy has its own timeline and can't overwrite edits you made on the original.
(With `--install`, if the original already has an editor timeline, the markers copy gets a
copy of it.)

## Markers

```bash
mcsm flashback my-server.zip --replay 2 --install
```

- `--replay`: the number from `mcsm replays`, a file path (e.g. a replay in your own
  `.minecraft/flashback/replays/`), or part of its name. Only finished Flashback `.zip`
  replays work; for ReplayMod use `mcsm sync`.
- `--install`: put the copy straight into Flashback's replays folder. Without it, the copy is
  written to the current folder (or `-o FILE`) and you move it to
  `<.minecraft>/flashback/replays/` yourself.
- `--flashback-dir`: where Flashback keeps its data. The default is `<.minecraft>/flashback`:
  `%APPDATA%\.minecraft\flashback` on Windows, `~/Library/Application Support/minecraft/flashback`
  on macOS, `~/.minecraft/flashback` on Linux. **Launchers** (Prism, Modrinth, CurseForge,
  ATLauncher) keep a separate `.minecraft` per instance, so point this at the instance's
  `flashback` folder. You can also set the `MCSM_FLASHBACK_DIR` environment variable.
- `--types`, `--players`: same as `mcsm sync`.

Marker colours: death red, chat blue, join green, leave grey, advancement yellow, command
purple, server start/stop pink, timelapse activity teal. The replay's own markers are kept; a
new marker that lands on an occupied tick moves to the next tick.

Markers are placed using the replay's start time. For Flashback that is decoded from the
recording's real-time clock (see [VIDEO_SYNC.md](VIDEO_SYNC.md#supported-replay-formats)),
accurate to about 50 ms; the log side has 1-second resolution.

## What gets written (for developers)

Verified against Flashback's source (master, November 2026). If a future Flashback version
changes these formats, this is where to look.

**Replay copy** (`.zip`): every entry is copied byte for byte except `metadata.json`, which
gets a new `uuid`, a name suffix, `original_uuid_mcsm` (the original's uuid), and merged
`markers`:

```json
"markers": {"600": {"colour": 16733525, "description": "Steve was blown up by Creeper"}}
```

Keys are replay ticks (20 per second from the start). `colour` is `0xRRGGBB`. `position` is
optional and left out, because Flashback compares its `dimension` with an internal string
format.

**Editor timeline** (`<flashback>/editor_states/<uuid>.json`), loaded when the replay is
opened in Flashback. The minimal valid form, as written by `mcsm/flashback.py`:

```json
{"scenes": [{"name": "Timelapse: Base",
             "keyframeTracks": [
               {"keyframeType": "CAMERA", "enabled": true,
                "keyframesByTick": {"500": {"type": "camera", "position": [115.0, 86.0, 32.6],
                                            "yaw": 180.0, "pitch": 28.9, "roll": 0.0,
                                            "interpolation_type": "HOLD"}}},
               {"keyframeType": "TIMELAPSE", "enabled": true,
                "keyframesByTick": {"500": {"type": "timelapse", "ticks": 0},
                                    "1300": {"type": "timelapse", "ticks": 195}}}],
             "exportStartTicks": 500, "exportEndTicks": 3100,
             "history": {"entries": [], "position": 0}}],
 "sceneIndex": 0}
```

Why each part is there (from the source):

- Scenes and tracks are created by Gson **without running their constructors**. So
  `enabled: true` (otherwise the track is ignored), `keyframeTracks`/`keyframesByTick`
  (otherwise null), explicit `exportStartTicks`/`exportEndTicks` (otherwise 0/0) and an empty
  `history` (otherwise undo/redo or adding a keyframe crashes) must all be written.
- `keyframeType` ids are exactly `CAMERA` and `TIMELAPSE`. Interpolation is the enum name.
  `HOLD` keeps the camera fixed after its keyframe.
- Timelapse: between two keyframes, the replay plays at
  `(replay ticks between) / (output ticks between)` × normal speed. Output `ticks` must strictly
  increase.
- If the file can't be loaded, Flashback deletes it and starts an empty timeline (no crash).
  `mcsm` never overwrites an existing editor timeline file.
- Open the copy in Flashback only **after** the files are written. A replay that's already open
  keeps its in-memory timeline and saves over the file.

Source files: `state/EditorState.java`, `EditorScene.java`, `EditorSceneHistory.java`,
`KeyframeTrack.java`, `EditorStateManager.java`, `keyframe/impl/CameraKeyframe.java`,
`TimelapseKeyframe.java`, `keyframe/types/*KeyframeType.java`, `record/FlashbackMeta.java`,
`record/ReplayMarker.java`, `editor/ui/windows/TimelineWindow.java`.

## Not yet verified

Nobody has opened these files in a real Flashback yet. Everything is based on reading the
source. If Flashback shows an empty timeline for a timelapse copy, its log (`logs/latest.log`
in `.minecraft`) contains `Error loading editor state` with the reason. Please report it.
