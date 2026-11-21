# Activity-aware timelapse of an area

A fixed camera over your base or town that **plays only while someone is there**. Empty
hours, nights and days are skipped with a quick fast-forward. The whole thing fits a
**target length** (3 minutes by default) or runs at a **fixed speed**. It is set up for you in
**Flashback's editor**, ready to export, together with a real date/time overlay and chapter
timestamps.

## What you need

1. **A Flashback replay that covers the area.** Either:
   - **ServerReplay chunk recording (best):** records an area continuously, whether or not
     players are near. In ServerReplay set `default_encoding` to `flashback`, then start a
     chunk recording around your base. It's saved in `recordings/chunks/<name>/`.
   - A player recording (ServerReplay or your own Flashback) where that player is at the area.
2. **Positions** saying who was where and when. Nothing to do if your players are recorded
   with ServerReplay in Flashback format: `mcsm` decodes the positions from every replay that
   overlaps the chosen one ([TRACKS.md](TRACKS.md)). Other sources: live-map recordings
   (`mcsm live … --record DIR`, or *Save positions* in the Live map tab) or a `mcsm tracks`
   folder, passed with `--tracks`. With no positions at all, `mcsm` falls back to the logs
   ("anyone online"). That is much coarser: it can't tell whether people were at *your* area.
3. **The area box:** stand at two opposite corners in game and note the X and Z from F3. Add a
   Y range if you want to ignore people flying over or mining far below.

## Run it

**App:** load the download → **Flashback** tab → pick the replay, fill in *Area box*, point
*Position recordings* at your recordings folder (or leave it empty, or click **Decode positions from all replays**) → **Preview plan** → **Create timelapse**.

**Command line:**

```bash
mcsm timelapse my-server.zip --replay 3 --area 100,-50,180,20 --area-name "Main base" \
    --length 3:00 --install
```

Use `--dry-run` first to see the plan:

```
Activity    : 6 periods from 241880 track samples
Output      : 3:00 at 92x while active
   0:00-0:41  Sat 04 May 19:02  Alex, Steve  (63 min at 92x)
   0:41         ... 20.1 h with nobody there (fast-forward)
   0:41-1:12  Sun 05 May 15:31  Steve  (47 min at 92x)
   ...
```

| Option | Meaning | Default |
|---|---|---|
| `--area X1,Z1,X2,Z2[,Y1,Y2]` | The box to watch (negative numbers are fine) | required |
| `--area-name` | Used in markers and chapters | Base |
| `--dimension` | `minecraft:overworld` / `the_nether` / `the_end` | overworld |
| `--tracks DIR` | Live-map recordings or a `mcsm tracks` folder (repeatable) | positions decoded from overlapping replays, else log sessions |
| `--no-replay-tracks` | Don't decode positions from replays | off |
| `--length 3:00` | Fit the whole timelapse into this length | 3:00 |
| `--speed 60` | Instead: a fixed speed while active | – |
| `--gap-seconds` | Output time for each skipped idle stretch | 0.5 |
| `--merge-gap 2:00` | Activity closer together than this counts as one period | 2:00 |
| `--pad 5` | Seconds kept before/after each period | 5 |
| `--camera X,Y,Z,YAW,PITCH` | Camera position and angle | automatic |
| `--install` / `--flashback-dir` | Put everything into Flashback (see [FLASHBACK.md](FLASHBACK.md)) | off |
| `--extras-dir` | Where the overlay and chapters go | next to the output (current folder with `--install`) |

With a target length, every active period plays at the same speed, chosen so active time plus
the gaps add up to the target. It never plays slower than real time, so short sessions give a
shorter video.

**Camera:** by default it sits south of the area, raised, looking north and down at the
centre. To use your own, fly to the spot in game and read `XYZ` and `Facing` (yaw/pitch) from
F3. Or adjust the camera keyframe in Flashback after opening; it's a normal keyframe.

## What you get

- **A replay copy** ending in `(timelapse)` with markers *"Steve, Alex at Main base"* /
  *"activity ends"* at each period.
- **Its editor timeline:** one scene, *Timelapse: Main base*, with a fixed camera keyframe and a
  timelapse track. The export range is already set to the first to the last activity. In
  Flashback, open the copy and export the scene as a video.
- **`… (timelapse).time.srt`:** a subtitle showing the real date and time, updated every second
  of the output, with "20 hours later" cards over the skips. Put it on top of the exported video
  in your editor.
- **`… (timelapse).chapters.txt`:** structured timestamps, one per period ("1:12 Sun 05 May
  15:31 – Steve"), for YouTube or your own notes.

## Limits and notes

- Accuracy depends on the positions: live recordings give one per player per second; replay
  positions are finer but only exist while a player was being recorded.
- Idle stretches are fast-forwarded through the replay at very high speed. Flashback still
  processes that recording time, so exporting a timelapse over many days of recording can take
  a while.
- "Block by block" building order (build progress between world snapshots, or decoding block
  placements from the replay) is planned. See `PROJECT_STATUS.md` backlog #1, phases B and C.
- ReplayMod replays aren't supported for timelapses yet (Flashback first, as decided).
