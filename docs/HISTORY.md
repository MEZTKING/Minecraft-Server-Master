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
**Videos** tab → **Join videos…** (or `mcsm timelapse-join PART1.mp4 PART2.mp4 … -o timelapse.mp4`). It opens in
the folder your Flashback saves videos to; Flashback names them by date and time, so export the parts in
order and they sort right (they're put in "(timelapse n of N)" order when their names have it, and **Up** /
**Down** change the order). Choose a **transition** for every cut at once or for each cut:

| Transition | What you see |
|---|---|
| Cut | straight from one clip to the next (no re-encoding when the clips match: fast, no quality loss) |
| Cross-fade, Dissolve | the clips blend into each other |
| Dip to black / white | the picture fades to black (or white) and back |
| Whip blur | a quick sideways blur, like a fast camera turn (always 0.3 s) |
| Block wipe | the picture breaks into big Minecraft-style pixels and comes back as the next clip |
| Wipe / Slide (left, right, up), Zoom in, Circle open | the next clip wipes, slides, zooms or opens in |

**Length** sets how long a transition takes (0.6 s at first). A transition overlaps the two clips by its
length, so the joined video is that much shorter; the clips' own sound cross-fades with it. With any
transition the video is re-encoded, at the first clip's size and frame rate. A **song** (optional, from any
second of it) replaces the sound, loops if it's shorter than the video and fades out over the last 2
seconds. On the command line: `--transition fade` (every cut), `--transitions fade,cut,blocks` (one per cut),
`--transition-length 0.8`, `--music SONG --music-start 12`. It needs ffmpeg on this PC.

Your recordings are not changed. The players' positions must have been read first (the status line says
when).

**Loading.** While the page is still reading things in the background, a card at the top shows each
part with a bar: the newest backup's map, the older backups' maps, player positions from the recordings,
changes between backups, and the building/exploring moments. You can look around meanwhile; **Hide**
puts the card away.

**Celebrations and sad moments.** Moments now have a mood, shown as a badge in the list, with two kinds in the
filter (*celebrations*, *sad moments*):

- **Celebrations:** defeating the Ender Dragon or the Wither; the first time anyone on the server reached a milestone
  (the Nether, the End, diamonds, a stronghold, an elytra, a fortress, a raid won…); a player's first pet ("Best
  Friends Forever", and every wolf or cat kind, or every animal bred); hard (challenge) advancements; several
  players getting the same advancement within 30 minutes; the server's 100th day and birthdays; a player's 100th
  session and their 100th, 250th, 500th and 1000th hour.
- **Biomes found** (*biomes found* in the filter): the first player on the server to find each biome, and each
  player's first time in a rare one (Mushroom Fields, Cherry Grove, Deep Dark, Ice Spikes, jungles, badlands, the
  Nether's forests and more). The times come from the dates the game keeps for *Adventuring Time* and *Hot
  Tourist Destinations*, so they're exact to the second. Biomes found in the server's first 3 days count only if
  they're rare (everyone starts around spawn). A moment where someone died and that outweighs the good part isn't
  called a celebration.
- **Sad moments:** dying after 10+ hours of play since the last death ("dies after 16 hours alive"); deaths where the
  items are probably gone (lava, the void, burning); three deaths in ten minutes ("dies again and again"); leaving
  within a minute of dying (and not coming back within five); a regular player's last time on the server, when that
  was 60+ days before the logs end.
- **From the statistics in the backups** (*from the statistics* in the filter; marked *between backups*, as they happened
  somewhere between two backups and so have no exact time; not played in the story): a player's first villager trade,
  enchantment, raid won, totem, elytra flight, dragon, wither, diamonds, player kill, animals bred, fish; records
  (the most blocks mined, flown, walked, mobs killed or items crafted in one stretch, per hour played; the server's
  record says so); play binges (far more hours a day than usual).
- Chat only lifts the mood of something already happening (or a lively chat of 3+ messages from 2+ players): whole
  words like *gg*, *lets go*, *rip*, *noo*, plus *!!!*, laughter and *:D* / *:(*; no single message labels anyone.

**Real blocks close up.** With **Real blocks** ticked (next to *Terrain*, on at first), zooming in shows the
real blocks of the backup on the map, with Minecraft's own textures and shapes (from the game installed on this PC,
of that backup's Minecraft version). Every block you could see from outside is there, not just the top layer:
inside houses (through doors and windows), under roofs and overhangs, cliffs to their full height, and caves near
the surface (down to 40 blocks under the ground). Blocks have their real shape and direction: stairs, slabs,
fences, walls, doors, trapdoors, torches, lanterns, rails, carpets, flowers, beds, chests (with their texture;
a double chest joins up), signs, banners, shulker boxes, skulls and mob heads, decorated pots. Blocks standing in
water (waterlogged stairs, slabs, fences...) have their water. Grass, leaves, vines and water take the colour of
their biome, from the game's own biome files: swamps are murky, badlands and savannas dry, jungles bright. Like the
game, the colour is blended where biomes meet. Turned stairs, fences and walls keep their texture lined up as in
the game. Not drawn yet: copper golem statues and dragon heads.

The pieces load 128 x 128 blocks at a time around where you look and around every player shown, nearest first
(about a second each the first time, then saved in `%LOCALAPPDATA%\mcsm\build-view\`), and follow the time slider
to each backup's world. Further away the map stays the smooth overview. When a backup's Minecraft version isn't
installed on this PC, the status line says so and another version's textures are used.

**Light.** The real blocks are lit the way the game lights them: each face gets the sky light and the light from
torches, lanterns, glowstone and so on that the backup stores for that spot, with Minecraft's soft shadows in
corners. Open **Light** (under *Terrain*) to change it; your settings are remembered:

- **Time of day**: noon is full daylight. At night the sky light fades and only torches and lamps light things
  (warmly); sunrise and sunset tint the sun.
- **Brightness**: like the game's *Brightness* option (Moody to Bright).
- **Torch light**: how strongly torches and lamps light their surroundings.
- **Exposure**: everything lighter or darker.
- **Sun shading**: 0 uses Minecraft's fixed shading of block sides; higher shades them by where the sun is.
- **Soft shadows**: the corner shadows (Minecraft's smooth lighting); 0 turns them off.
- **Graphics:** how much is drawn. Your choice is remembered.
  - **Off:** Minecraft's own flat look, as without shaders.
  - **Fancy:** shader-like looks, drawn in the page itself, as real shader packs only run inside the game:
    - the sun casts shadows from blocks and players;
    - a real sky with sunrise and sunset colours, the sun and moon, stars at night and grey rain clouds;
    - haze in the distance;
    - a soft glow around torches, lanterns, glowstone, fire and other light sources (soul ones glow blue),
      strongest at night;
    - water that reflects the sky, more at a glancing angle, with the sun's glint and small moving waves;
    - film-like colour.

    It's about as bright as Off overall.
  - **Ultra:** the same with sharper shadows reaching further, at full screen resolution. It's slower.
- **Reset** puts everything back.

**Exact playback.** All recordings play together on the world's own clock. Every recording contains the game
time the server sends each player every second, and that clock is the same in every recording. So recordings
made at the same time line up to a twentieth of a second, even through server lag. A paused recording shows up
as a jump in the clock, so the pause is placed exactly. Real dates come from the save times, with one shared
setting for each stretch of play, so players who were together stay together.

Zoomed in at **1×** or **10×**, or paused, the viewer uses every movement the recordings hold (about ten a
second, as the server sent them): the exact path, each step and jump, which way each player faced, where their
head turned and whether they looked up or down. Each player's own recording is used first; other players'
recordings of them fill the times it doesn't cover. Faster speeds and the zoomed-out view use the lighter
overview paths, about one point a second. The detail for a recording is worked out the first time you watch it
(a few seconds) and then kept.

Movement between recorded positions is smoothed like the game smooths other players: a player holds still
until shortly before the next recorded position, then glides into it, so steps up and jumps don't snap. Legs
swing with the recorded speed. In that view the players also sneak (the game's pose: bent from the neck, head
and arms lower, legs back), sprint, swim and fly with elytra (lying forward), and swing their arms
when they did. Mobs, pets, animals, boats and minecarts within 48 blocks of a recorded player are shown, a
couple of times a second, with the game's own models and textures: creepers, skeletons, zombies, spiders, fish,
squid, bats, farm animals, villagers, horses, boats and the rest. They walk (legs and arms swing with their
speed), zombies hold their arms out, fish swish their tails, squid move their tentacles and bats flap. The
models are read from the newest Minecraft 26.x game installed on this PC (older versions' games can't be read);
without one, mobs show as boxes sized and coloured by kind. What the recordings don't say is picked once per mob:
a horse's coat, a cat's or parrot's colours, a tropical fish's colour, and farm animals are their temperate kind.
Babies show adult-sized. Dropped items show as the item's icon, turning and bobbing like in the game (wheat
and seeds around a farm, a stack thrown to a friend); they disappear when picked up or gone. Mobs disappear the
moment they're killed or leave the recording's view. Arrows and other things that fly past aren't shown. The first time you watch a stretch, the status line says the recordings are being read in full;
that can take up to a minute, and later views are quick.

What the recordings say about each player and mob is shown too:

- **Sleeping:** lying on their back.
- **Dying:** falling over sideways; mobs disappear after their death.
- **Using an item:** the arm raised (eating, drawing a bow, holding up a shield).
- **Hurt:** a short red flash.
- **Burning:** an orange flicker.

The pose comes from the game's own pose value when the recording has one, otherwise from the flags.

**Time and weather from the recording.** With **Time and weather from the recording** ticked in **Light** (on
at first), close-up playback takes the time of day from the recording. When someone sleeps through the night,
the morning comes as it did in the game. Rain and thunder darken the sky as in the game, and rain falls around
the camera. Untick it to set the time yourself.

The first time a stretch is watched, its recordings are read in full in the background, three at a time. The
viewer shows what's ready straight away and fills in the rest as it arrives; the status line says how many are
still being read.

Players sit (legs forward) while riding: boats and chest boats, horses, mules, minecarts, even a nautilus, and
the invisible seats your sit datapack uses for chairs and stairs. Getting on and off is shown when it happened.
A pose, riding or flag keeps its last value from however long before, so someone who got in a boat ten minutes
earlier is still sitting.

Players wear their armour and hold what they held, as the recordings saw it: helmets, chestplates, leggings
and boots in every material (leather in its dye colour; trims aren't drawn), and the item in each hand as a
flat icon pointing forward from the fist, like Minecraft's own third-person view. A block held shows its
texture. Items from the Hero's Journey datapack show as the plain item they're based on (their own pictures
come from the server's resource pack, which isn't on this PC). The recorded player's own gear is only listed
when it changes or every few minutes; until the first listing in a recording, the first one seen is assumed.

**Blocks as at that moment.** With **As at that moment** ticked (next to *Real blocks*, on at first), the blocks
near you during exact playback are the world as the recordings saw it at that time, not as the next backup has
it: a build half done, a hole just dug, a house not yet there. Every recording keeps the chunks its player's game
received and every block placed or broken after that; the app puts together what all the recordings running at
that moment saw (the latest change wins). The nine pieces nearest you are rebuilt every 30 seconds of playback
and when you jump; the label next to the switch says which moment they show. In between, blocks placed or
broken appear as they happen: the chunk (16 x 16 blocks) where it happened is rebuilt on its own, usually within
a second. Further away, and where no
recording had the chunks loaded, the backup's blocks stay. Building a piece takes a few seconds (the first time
a recording is used, a little longer while the app notes where its chunks are kept). Light where a block was
changed is estimated from around it, as Flashback keeps no light updates.

**Shot for Flashback.** Plan a camera shot here and let Flashback film it at full game quality:

1. Put the camera where you want it (**Free cam**, **Spectate**, or orbiting) at the right moment, and press
   **Add keyframe** (key **K**). Add more at later moments. Each keyframe stores the time, where the camera is
   and where it looks. **Go** looks from a keyframe again; **×** removes it.
2. **Preview** flies the shot in the viewer (**Smooth**, like Flashback's camera, or **Linear**). **Esc**
   stops it.
3. The list below suggests the recordings that cover the whole shot. The best is first: the one whose player
   was nearest the camera. Pick another if you like.
4. **Send to Flashback** copies that recording into the Flashback of its Minecraft version (never the original)
   as "… (shot) title". The copy has your camera keyframes, the export range and a marker. Each keyframe's time
   becomes that recording's tick through the world clock, pauses included. Open it in Flashback and Export.

The keyframes are kept in the browser per world.

**Prepare ahead.** Close-up viewing reads recordings in full and builds real blocks the first time. **Prepare
ahead** does that in advance for the day, week or month shown, or everything, optionally for one player only.
**Estimate** shows how many recordings and block pieces, the time and the disk space first, with a warning when
it's a lot. **Start** runs it in the background with progress; **Cancel** stops it and keeps what's done. See
[WORKFLOW.md](WORKFLOW.md) for how much each takes.

**Show.** Open **Show** (under *Terrain*) to turn things on or off. Your choice is remembered:

- **Players**, their **Names** and their **Colour rings**
- **Trails** (the line behind each player)
- **Mobs and pets**
- **Chat**: what players said (from the server log) in a bubble over their head, for 4 to 12 seconds depending
  on the length, the last two messages at most. Not over your own head when watching through a player's eyes.
- the smooth **Map** and the **Grid**

**Real blocks**, **All paths** and **Event pins** keep their own switches.

**Free camera.** Press **F** or click **Free cam** to fly like Flashback's free camera:

- **Look around:** hold a mouse button and drag.
- **Move:** **W A S D**.
- **Up and down:** **Space** and **Shift**.
- **Faster:** hold **Ctrl**.
- **Speed:** the scroll wheel.

Press **F** or **Esc** to go back to orbiting. While the free camera is on, use **P** or the Play button to play
and pause, because Space moves you up.

**Spectate.** Pick a player in **Spectate** to watch them:

- **First person:** through their eyes, with their recorded head turn and look; lower when they sneak.
- **Behind:** a few blocks behind them.

Spectating switches to exact playback (1×). **Esc** stops.

**Dev mode** (press the **`** key, or open the page with `?dev=1`) shows a panel with:

- the exact time (and its number) and the camera's position, direction and mode;
- the backup shown and how many real-block pieces have loaded (or failed);
- whether exact playback is on, with its players, mobs and how long the last fetch took;
- the frame rate, draw calls and triangles;
- each player's position, where it comes from (exact recording, overview path, last seen) and their pose flags;
- the block under the mouse, read from the backup: its full state, light and biome.

Its buttons:

- **Copy view link:** a link that opens the viewer at this moment, with this camera and spectate. Send it
  along with a problem so it can be looked at in exactly the same place.
- **Copy debug info:** all of the above as text.
- **Reload blocks** and **Reload exact:** load them again.
- **Go to:** type `x y z` or a date and time such as `2026-05-24 20:31:09`.

**Players** are shown as Minecraft figures with their own skins, including the skin's outer layer (hat, jacket,
sleeves, trousers) and slim arms for players with the slim model. Each has a ring in their colour at their feet,
faces the way they move, and swings their arms and legs as they walk.

Each player wears the skin they had at the time you're looking at. Every recording keeps the skins the game sent,
so the figure uses the skin from the player's last recording before that moment; before their first recording,
it uses that first one's. When a player changed skins, the figure changes too as the time slider passes that
recording. The recordings are read once (a few seconds), and the result is kept.

The skin pictures come from Mojang's texture server, the same place the game gets them. Players who have no
recordings get their current skin: for those, the app sends only the player's UUID to Mojang. Skins are kept in
`%LOCALAPPDATA%\mcsm\skins\`, and current skins are checked again after a week. Untick
**Real skins** (next to *Event pins*) to use the game's default skins instead (Steve, Alex and the others, always
the same one for a player). Default skins are also used when a player has no skin or there's no internet.

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

**Minecraft versions.** Flashback opens a recording only in the Minecraft version it was made with. The
History tab's status line and the *Export to Flashback* button list where each version's copies go. On the timeline,
a **version band** above the players shows which Minecraft version the server ran when (from the backups and the
recordings; a change is placed at the first backup or recording in the new version), striped where this PC has no
Flashback of that version. Hover it for the dates and the Flashback; click a version to list only its moments (click
again for all). The status line lists the Flashback per version (e.g.
"26.1.2 → Flashback 26.1.2; 1.21.11 → 1.21.11; 1.21.5 → no Flashback of that version on this PC"). Each
moment shows its version, the list can be filtered by version, and copies (highlights and timelapses) go
only into a Flashback of exactly that version. If there is none on this PC, they go into a folder
"Flashback for Minecraft <version>" next to the highlights file (the timelapse summary says so first): add a
Minecraft instance of that version with Fabric and Flashback (e.g. in Prism Launcher), then move that folder's
`replays` and `editor_states` files into its `flashback` folder (make it if it isn't there yet). An instance counts
as soon as Flashback's jar is in its `mods` folder, before Flashback has made its own `flashback` folder.

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
