# History viewer: everything as one recording

`mcsm history` opens a 3D map in your browser with a **time slider over your server's whole
history**. Drag it to any moment to see who was online, where they walked, and what happened
(joins, deaths, chat, advancements). You can also see which recordings cover that moment.

```
mcsm history SERVER [MORE BACKUPS...]
```

In the app: load your backup(s), open the **History** tab, press **Open history viewer**.

## What you see

- **Players** walk along their paths as time moves. A solid marker means the position comes
  from a recording at that moment: their own, or someone else's they were in view of. A faded marker means the player was online (from the logs)
  and that is their last known position this session: a path point or where they logged in.
- **Trail**: where each player went in the last 1 min / 10 min / 1 hour / 1 day.
- **All paths**: every recorded path in the current dimension, faint. Shows where people
  spend their time.
- **Event pins**: deaths, chat, advancements and joins in the trail window, pinned at the
  player's position at that moment (when it is known).
- **Happening now**: those events as a list. Click one to fly to it.
- **Recordings at this moment**: each Flashback/ReplayMod recording covering the current time,
  and how far into it you are (e.g. `12:40`). In Flashback, open that replay and go to that
  time to see the moment in full. Click to copy the replay's path. Each one shows the
  **Minecraft version** it was recorded in: Flashback opens a recording only in that version.
- **Server at this moment** (with several backups): the Minecraft version, number of mods and
  datapacks from the backup that covers this time, and what changed since the backup before
  (mods added/removed/updated, datapacks, Minecraft version). Hover for the datapack names.

## The timeline

- One row per player who is active in the visible window. A **faded bar** means online (from
  the logs). A **solid bar** in the same colour means a recording. Recordings with no
  timeline inside (ServerReplay 3.4.x, see [FLASHBACK.md](FLASHBACK.md)) are drawn paler.
- Red ticks are deaths and crashes; grey ticks are other events; yellow lines are backups
  (when several are loaded).
- Click or drag to jump. **View** zooms the strip (everything, 30 days … 2 hours).
- **Play** moves time at the chosen speed (1×, 10×, 1 min/s, 10 min/s, 1 h/s).
  **Skip quiet time** jumps over periods when nobody was online. **Prev / Next** jump between
  log-ins. Keyboard: Space plays/pauses, ← → move 1 minute (with Shift: 1 hour), `I` / `O`
  mark a highlight's start / end.
- The page remembers the last time you looked at (in this browser only).

## The server story, start to finish

**Library** tab → select the world → **Watch the server story** (or `mcsm story`, which uses the only
world in your library, or `mcsm story <world folder>`). The app loads every backup and recording of that
world (about a minute) and opens the history viewer at the very beginning with **Story** ticked.

- Press **Play**: the important moments (below) play in time order, each at 10× real time; between them a
  title card shows the date, what happened and who was there, and time jumps to the next one. The map
  frames the players of each moment.
- **good moments / best moments / every moment** chooses how many are played.
- **⏮ Beginning** goes back to the start, **◀ Moment / Moment ▶** to the previous or next moment (also with
  Story off), and the indicator next to the clock shows "Day N of all days played · moment n/N".
- **Story so far** in the panel lists the moments reached, newest first, by day; click one to go there.
- Untick **Story** to play normally again (speed, Skip quiet time).

Player positions come from the recordings and are read in the background the first time (the status
line shows the progress), so early in a first session players may show only as events.

## Important moments

The panel lists the **important moments of the whole history**, found automatically from the logs:
deaths (killed by another player counts most; someone dying again and again counts less each time),
advancements (the rarer, the more: 1 divided by the share of regular players, with an hour or more of
playtime, who got it), a player's first join (not on the first day of the logs, when everyone seems
new), conversations and markers placed in replays (not the app's own copies). Events less than 3
minutes apart form one moment, at most 10 minutes long; each moment starts 30 s before its first event
and ends 15 s after its last. Its score grows with the number of other players online and with the
number of players whose recordings show it (points of view). Hover a moment to see why it scored and its events.

**Quiet but busy.** Hours of building, flattening ground for map art or searching for something show
nothing in the logs. Once the players' positions have been read from the recordings (the status line
shows the progress; the list updates by itself), such stretches are moments too:

- **building**: 45 minutes or more in one area (within about 64 blocks), really moving around in it;
  standing still (AFK) is ignored. A large flat area worked back and forth is **map art / flattening**.
- **exploring / searching / a journey**: 30 minutes or more travelling into new ground; zig-zagging over an
  area is searching, going far in one direction a journey.
- **AFK** doesn't count: idle minutes at the start or end of a build are cut off, and the score grows
  slowly with the time actually spent moving (3 active hours rank about like a PvP fight); the
  description says how much of it was active. Players working in the same area at the same time are
  one moment. Where the backups allow, the players' statistics between
  the backups before and after are shown ("used/placed 40k, mostly white concrete", "flew 300 km").
- Hovering or playing one outlines its area on the map; in the story it plays through in about 30 s.
  **Keep** keeps its best 3 minutes: the end of a build (the finished work), the stretch of an exploration
  that found the most new ground. Only players seen in some recording can be found.

**Timelapse of a build.** Building and map-art moments have a **Timelapse** button: a fixed camera over
the area (south of it looking down; straight down for map art) that plays the hours of work in about 3
minutes and skips the time nobody was there. It is filmed from the builder's own recordings (the camera
needs the chunks around the build, which only a recording made there has), all at one speed so the parts
join seamlessly. It is filmed from the recordings of whoever was actually near the build, and minutes when the
builders were AFK are fast-forwarded like time with nobody there.

The button opens the **timelapse view**: the real world around the build in 3D (terrain, and real blocks
with Minecraft's own textures from the game installed on this PC, from the backup saved right after the
build), with a summary of who filmed it, the speed, the length, each part, where the copies go and how
much space they take. In the view you can:

- **look around**: drag to turn, right-drag to move, scroll to zoom;
- **▶ Preview** the timelapse: the players move as boxes at the timelapse's speed (the clock shows the
  part and the real time); the slider jumps anywhere in it;
- choose the **camera**:
  - **Automatic**: a fixed camera over the build (south of it looking down; straight down for map art);
  - **My views**: press **Save this view** where you want the camera. One view = a fixed camera there;
    two or more = the camera glides from one to the next, evenly over the whole timelapse (all parts
    together), and the preview shows that glide. **Remove last** forgets the last view;
  - **I'll set it in Flashback**: the copies get no camera; place your own camera keyframes in each copy
    before exporting.

**Make the timelapse** then makes the copies in the background (progress under the button): one
Flashback copy per recording file, named "… (timelapse 1 of 4)", each with the camera, the timelapse and one
scene, and an order list next to the highlights file. **Why several parts?** A build that took several
play sessions is spread over several recording files (Flashback starts a new file each time someone joins
again), and a Flashback edit can only use one file. All parts run at one speed so they join seamlessly.
Very short parts are left out (a few seconds aren't worth a copy to open and export) and the others share
their time, so the total stays about 3 minutes; the order list names what was left out. **Leave out parts
shorter than … s of video** in the view sets how short (2 s at first; 0 keeps every part; at most 30 s): the
plan, the part count and the preview update at once, and the app remembers your choice.

In Flashback open each copy in order and Export its scene; then **join** the exported videos: the app's
**Videos** tab → **Join timelapse parts…** (or `mcsm timelapse-join PART1.mp4 PART2.mp4 … -o timelapse.mp4
[--music SONG.mp3] [--music-start SECONDS]`). Pick the exported videos: they are put in "(timelapse n of N)"
order when their names have it (otherwise by name), joined without re-encoding when they have the same size
and frame rate (else re-encoded to the first one's), and a song, if you choose one, replaces the sound,
loops if it's shorter than the video and fades out over the last 2 seconds. It needs ffmpeg on this PC.

Your recordings are not changed. The players' positions must have been read first (the status line says
when).

**Changes between backups.** With two or more backups, the viewer compares each backup's ground with the
one before (in the background, a few minutes the first time for a big library, then saved). Tick
**Changes** next to **Terrain**: the map outlines what changed between the backup shown and the one before
it (orange = built, purple = map art, brown = dug out or cleared, grey = changed), and a list under it gives
each change's size and who was recorded there; click one to fly to it. Move the time slider (or pick a
backup in **Terrain**) to see another interval.

- Natural changes are left out: growing trees, water and ice, snow, lava, water plants, and the End's
  island around 0, 0 (the dragon fight). Small scattered changes (a block here and there) don't count;
  an area needs about 250 blocks² of changed ground that sits close together.
- On land that didn't exist yet in the older backup (explored since), areas covered in clearly man-made
  blocks (planks, bricks, glass, wool, concrete…) count as built "on new land". A village the game made
  can look the same.
- Changes also become **moments** ("changes between backups" in the kind filter; untick *with a
  recording*, as most have none): "Unknown builder" when nobody was recorded there, otherwise who was
  there, at the last time they were seen there. Building moments the backups confirm get "the backups show
  about N blocks² changed there" and rank a little higher; such an area gets no moment of its own. At most
  5 change moments per backup interval.
- The map's colours and heights are sampled every few blocks, so sizes are estimates.

**Minecraft versions.** Flashback opens a recording only in the Minecraft version it was made with. Each
moment shows its version, the list can be filtered by version, and copies (highlights and timelapses) go
only into a Flashback of exactly that version. If there is none on this PC, they go into a folder
"Flashback for Minecraft <version>" next to the highlights file (the timelapse summary says so first): add a
Minecraft instance of that version with Fabric and Flashback (e.g. in Prism Launcher), start it once, then
move that folder's `replays` and `editor_states` files into its `flashback` folder.

- Filters: kind of event (also *building & map art* and *exploring & searching*), a Minecraft version, a player, **with a recording** (moments no recording shows can't become a
  video), order **best first** or **by date** (grouped by day).
- **▶ Play** watches it on the map. **Keep** makes it a highlight (below); then **Export to Flashback**
  makes the replay copies with markers and one ready scene per highlight, as for highlights you mark
  yourself.

On the owner's library (15 backups, 3,177 recordings, 10,599 events) it finds about 800 moments in a few
seconds; about a third are shown by at least one recording. Keeping the same moment twice gives one
highlight. The weights are in `mcsm/moments.py`
(`WEIGHTS`).

## Highlights for videos

Mark the moments you want in a video while you browse the history, then send them to
Flashback ready to export.

1. Move to where the moment starts and press **Start** (or the `I` key). Move to where it
   ends and press **End** (`O`). The span shows on the timeline.
2. Type a title and press **Save** (or Enter). The players online in that span are filled in.
3. Saved highlights are listed in the panel and shown as green bands on the timeline:
   **▶ Play** plays just that span, **Edit** loads it back for a new title or new Start/End,
   **Delete** removes it. Each one says how many recordings cover it, and whose.
4. **Export to Flashback** handles all highlights at once:
   - For every Flashback recording that covers a highlight (one per player's point of view),
     a **copy** appears in Flashback's replay list, named `NAME DATE (highlights)`. It has a
     green **▶ title** marker at each highlight's start and a red **■ title** marker at its
     end. Its editor has **one scene per highlight whose export range is already set**: open
     the copy, choose the scene, press Export.
   - A clip list, `<world> highlights.csv`, next to the highlights file: title, local
     start/end, length, players, and for each recording the in/out time to seek to. Use it
     for any video editor.
   - Copying takes a while for long recordings, which can be hundreds of MB each. Your original
     recordings are never changed, and existing Flashback timelines are never overwritten.

Highlights are saved in `Documents\mcsm-highlights\<world>.json`, or in the world's folder
(`highlights.json`) when you load a world from the [library](LIBRARY.md) (`--highlights FILE` to
change), so they're kept between sessions and whichever backups you load. Flashback's folder
is found automatically, also inside launcher instances (PrismLauncher, MultiMC, Modrinth App,
CurseForge). Each copy goes to the instance with **the recording's Minecraft version**, because
Flashback only opens a recording in the version it was made with (e.g. May's 26.1.2 recordings
into a 26.1.2 instance). `--flashback-dir` forces one folder. If it isn't found, the copies go into a `flashback` folder
next to the highlights file: copy them into Flashback's `replays` folder, and the `.json`
editor timelines into its `editor_states` folder.

Not yet tried in a real Flashback: scenes that only set an export range (no camera
keyframes) are allowed by Flashback's code, but nobody has opened one in-game yet.

## Where positions come from

1. **Flashback recordings** (ServerReplay with `flashback` encoding, or the Flashback mod).
   `mcsm` reads the recorded player's movement straight from the replay files. No Minecraft
   version-specific decoding is needed. The first run reads every recording (about 1 second
   per 30 MB of replay data; the owner's 22 GB backup took a few minutes). Results are cached in
   `%LOCALAPPDATA%\mcsm\paths` (`--cache DIR` to change), so later runs are instant. The page
   fills in while this runs.
   - Besides the **recorded player**, every **other player in view** is tracked too. They
     are recognised by their UUID from the server files. In the player list their position
     says *seen by NAME* (whose recording it comes from). When a player was recording
     themselves at the same time, their own recording is used. Checked on the owner's
     backup: "seen" positions match the players' own recordings to about a block.
   - Mobs and other entities are not shown. Bedrock (Geyser) players without a UUID in the
     server files can't be recognised.
   - ServerReplay **3.4.0/3.4.1** recordings contain no movement, so they give no paths.
2. **Live-map recordings** (`mcsm live --record DIR`): `mcsm history SERVER --tracks DIR`.
3. **Log-in positions** from the logs (every join), used as "last known position".

The decoded paths use the same file format as live-map recordings, so they also work for
timelapses: `mcsm timelapse … --tracks "%LOCALAPPDATA%\mcsm\paths"`.

## Terrain: the world as it was

When you load several backups, **each backup's world is a terrain layer**. With **Terrain:
Follow the time slider** (the default) the map shows the newest backup taken at or before the
time on the slider (before the first backup: the first one). Drag back to May and the land
is as it was in May. Builds disappear and the explored area shrinks. Play forward and it
switches over at each backup (the yellow lines on the timeline). The **Terrain** menu can
also pin one backup, e.g. today's world under last spring's paths.

- The newest backup is built first (it's also what you see while the rest load), then the
  older ones, newest to oldest. The status line says how far it is.
- Regions that didn't change between backups (same file, size and time) are read **once** and
  shared, and switching backups only re-downloads the regions that differ. On the owner's
  May and November backups, 595 of May's 1,158 regions were unchanged.
- Regions are read in parallel (one worker process per CPU core, up to 8), nearest to spawn
  and log-in spots first, and each finished region is cached in `%LOCALAPPDATA%\mcsm\terrain`.
  The owner's 1,569-region world takes about 30 seconds the first time and about 4 seconds
  after that.
- Zip backups are unpacked (region files only) one at a time into a temporary folder that is
  deleted once their terrain is built.
- Each sample is one height and colour (`--terrain-detail`), so you see new buildings, cleared
  land and new paths, not single blocks.
- `--terrain newest` (or untick **Terrain from every backup** in the app) builds only the newest.
- The Nether surface is not drawn.

## Options

| Option | Meaning |
|---|---|
| `--tracks DIR_OR_FILE` | also show live-map recordings (repeatable) |
| `--no-replays` | don't read positions from recordings |
| `--highlights FILE` | where highlights are kept (default `Documents\mcsm-highlights\<world>.json`) |
| `--flashback-dir DIR` | Flashback data folder for exporting highlights (default: found automatically) |
| `--cache DIR` | where decoded paths are kept (default `%LOCALAPPDATA%\mcsm\paths`) |
| `--http-port N` | local port (default 8766; the next free one is used if busy) |
| `--terrain-detail auto\|1\|2\|4\|8\|16` | sample every N blocks |
| `--terrain all\|newest` | terrain from every backup (default; changes with the time slider) or only the newest |
| `--no-browser` | don't open the browser |
| `--tz`, `--latest-log-date` | as for every command |

The page is served on `http://127.0.0.1` only: it is not reachable from other computers.
