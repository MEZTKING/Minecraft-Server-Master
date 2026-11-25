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

- **building**: with the server's Ledger log (see *Blocks as at that moment*), exactly who built what. A build is
  one player placing 200 blocks or more in one area within 10 minutes of each other, over at least 10 minutes.
  The moment says what they placed ("placed 1,703 blocks (mostly spruce planks, stripped spruce log)"), and
  players building in one place at the same time are one moment. Replanting a farm, saplings and buckets of
  water don't count. Without a Ledger log, building is guessed from movement: 45 minutes or more in one area
  (within about 64 blocks), really moving around in it, with standing still (AFK) ignored. A large flat area
  worked back and forth is **map art / flattening**.
- **exploring / searching / a journey**: 30 minutes or more travelling into new ground; zig-zagging over an
  area is searching, going far in one direction a journey.
- **builds damaged** (with a Ledger log): blocks a player placed, later lost to a creeper or other explosion
  ("A creeper blew up 31 of Crow_Shroom's blocks", with who it was chasing), to fire, to lava, or to another
  player ("D_HumanCrafter took down 80 of Crow_Shroom's blocks": often agreed, so it isn't called griefing).
  Only placed blocks count, so a creeper crater in the wild is nothing; harvesting crops isn't damage.
  Explosions, fire and lava are sad moments.
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
of that backup's Minecraft version). Textures that move in the game move here too, at the game's pace: water,
lava, fire, nether portals, sea lanterns, magma, kelp, prismarine and the rest, with the frames in the game's own
order and timing, smoothly blended where the game blends them. Clouds drift over the overworld as the game's
(Fancy) clouds do: blocky, 4 blocks thick, at height 192, in the game's own pattern, white by day, dark at night and
grey in rain. Every block you could see from outside is there, not just the top layer:
inside houses (through doors and windows), under roofs and overhangs, cliffs to their full height, and caves near
the surface (down to 40 blocks under the ground). Blocks have their real shape and direction: stairs, slabs,
fences, walls, doors, trapdoors, torches, lanterns, rails, carpets, flowers, beds, chests (with their texture;
a double chest joins up), signs (with their text, see below), banners (with their patterns, see below), shulker boxes, skulls and mob heads, copper golem statues (in their pose and their copper's colour; player heads in their owner's skin, from Mojang's texture server like the
players' own skins), decorated pots. Blocks standing in
water (waterlogged stairs, slabs, fences...) have their water. Grass, leaves, vines and water take the colour of
their biome, from the game's own biome files: swamps are murky, badlands and savannas dry, jungles bright. Like the
game, the colour is blended where biomes meet. Turned stairs, fences and walls keep their texture lined up as in
the game. Not drawn yet: copper golem statues and dragon heads.

The pieces load 128 x 128 blocks at a time around where you look and around every player shown, nearest first
(about a second each the first time, then saved in `%LOCALAPPDATA%\mcsm\build-view\`), and follow the time slider
to each backup's world. Further away the map stays the smooth overview. When a backup's Minecraft version isn't
installed on this PC, the status line says so and another version's textures are used.

**Sharp but calm textures.** Close up, blocks show their crisp pixels; far away and at shallow angles they use
smaller copies of each texture (mipmaps, like the game's *Mipmap Levels*), so distant grass and leaves don't
shimmer into stripes.

**Build-in effect.** With **Build-in effect** ticked (under *Real blocks*, on at first), the land still loading
close up is drawn as a glowing grid of points and lines with a scan rippling across it, instead of the smooth
map's big flat polygons; each piece of real blocks then rises up out of the ground column by column, its top edge
a glowing block grid, in about a second and a half. Pieces updated to another moment just change. Untick it to
have them simply appear.

**Banners** show their real patterns, as saved in the backup shown: each flag is painted from the game's own
pattern pictures in the game's dye colours, layer by layer (any number of layers, as with your Limitless Banners
mod), and hangs at its exact angle (standing banners turn in sixteenths of a circle, as in the game). Ominous
banners at pillager outposts too. Shields carry their patterns and base colour, banners held show as small flags,
and a banner worn on the head (pillager captains) stands on it, all from the recordings.

**Rendering** (under *Light*, remembered):

- **FPS cap:** the most frames a second (30 to 240, or the screen's own rate), and **Show FPS** for a counter.
- **Resolution:** 50% to 200% of the screen's sharpness.
- **Block distance:** how far real blocks load around you (Near, Normal, Far, Very far).
- **Field of view**, and **Haze distance** (Fancy and Ultra).
- **Colours:** *As in the game* (the game's own colours) or *Film-like* (richer contrast and colour in Fancy and
  Ultra; some colours, like badlands leaves, then look stronger than in the game).
- **Shadows**, **Mobs** and **Light glow** on or off.

**Signs.** Standing, wall and hanging signs are shaped as in the game, and their text is written on them as the
backup saved it: every line where it was written, front and back, in the sign's dye colour (darker unless it was
made to glow). Text that doesn't glow is lit like the sign; glowing text shines with an outline, as in the game.
In close-up playback, text someone wrote on a sign during a recording replaces the backup's from that moment on.

**Under water.** The sea floor, sunken things and everything under water are drawn, seen through the water as
in the game.

**Water's shape.** Water is shaped as the game shapes it: a still source a little under a block high, flowing
water lower the further it runs, its surface sloping between cells (each corner averaged from the cells around it,
as the game does), falling water full height, and the flowing texture on slopes and sides. Stained glass, ice,
slime and honey blocks are see-through in their colour, and glass panes no longer flicker where faces meet.

**Caves underground.** Fly the free camera below the ground (more than a block under the surface where you are)
and the caves around you load: every wall, floor and ceiling of open space the sky can't reach, in a 64-block
high slice around the camera (3 x 3 pieces of 128 blocks; the slice follows you up and down in 32-block steps).
Caves are lit a little everywhere (cave light 6 of 15) so they can be seen, plus their real torch and lava light.
They come from the backup shown (`tiles-caves` in the cache) and go away when you come back up.

**Light.** The real blocks are lit the way the game lights them: each face gets the sky light and the light from
torches, lanterns, glowstone and so on that the backup stores for that spot, with Minecraft's soft shadows in
corners. Like the game's **smooth lighting**, each corner of a block face takes the light of the four spaces
around it, so light fades evenly across floors and walls instead of in block-sized steps. Light from torches,
lanterns and other light blocks **placed after the backup** (during a recording, or from the Ledger log) is worked
out too: it spreads as in the game, one level less each block, around corners but not through solid blocks. A
light block taken away takes its light with it: the spaces it lit go dark, apart from light from others nearby.
Like the game's own light map, sky light and torch light are added together (not the brighter of the
two) and go through the game's brightness curve, so rooms lit by torches are as bright as in the game. With
graphics Fancy, torch light isn't darkened by the sun's shadows (a lit room under a roof stays lit). Open **Light** (under *Terrain*) to change it; your settings are remembered:

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
    - a real sky with sunrise and sunset colours, the sun, the moon in the game's picture for that night's phase
      (full to new and back, eight nights, from the recording's day number), stars at night and grey rain clouds;
    - haze in the distance;
    - a soft glow around torches, lanterns, glowstone, fire and other light sources (soul ones glow blue),
      strongest at night;
    - water in its own biome colour, a little deeper than with graphics Off, that reflects the sky only at a
      glancing angle, with the sun's glint and small moving waves;
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
when they did (in Minecraft 26.3 recordings too, which keep swings in a packet of their own). Mobs, pets, animals, boats and minecarts within 48 blocks of a recorded player are shown, a
couple of times a second, with the game's own models and textures: creepers, skeletons, zombies, spiders, fish,
squid, bats, farm animals, villagers, horses, boats and the rest. They walk (legs and arms swing with their
speed), zombies hold their arms out, fish swish their tails, squid move their tentacles and bats flap. Mobs turn
and tilt their heads as recorded (a mob turning by more than 15 degrees is kept even when it stands still).
Pillagers, vindicators and other illagers fold their arms when empty-handed and free them when holding
something, as in the game. An egg or snowball breaking doesn't count as a death.

What the recordings say about each mob's looks is used too: **babies** are drawn with the game's own baby models
(big heads, small bodies), **sheep** in their wool colour (and bare when sheared), **horses** in their coat and
markings, **copper golems** in their copper's age (new, exposed, weathered, oxidized) with glowing eyes, **tamed cats and wolves** with a collar in its dye colour (a tamed wolf's own look too), sitting when they were
told to sit, **cats, wolves, cows, pigs, chickens and frogs** in their own variant (a siamese cat, a cold cow with
its long coat, a warm pig...; the variant names come from the recording itself), **villagers** in their biome's
clothes with their job's outfit and level badge (zombie villagers too), **slimes** and magma cubes at their size, and mobs with a **name tag** show their name above them, as in
the game, when you're within 16 blocks (with *Names* shown under *Show*). When a mob dies it tips over the way the
game does it, red all the while, and is gone a second later.

**Picking up, falling blocks, TNT, totems.** Items and experience orbs fly into whoever picks them up, as in the
game. Experience orbs show (green and yellow, shimmering), falling sand, gravel and concrete powder fall as their
block, lit TNT flashes white as its fuse burns, and a totem of undying going off bursts into yellow and green
sparkles round the one it saved.

**Mining and breaking.** In close-up playback, a block someone is mining shows the game's cracks growing on it,
and a block broken (by anyone the recordings saw) bursts into small bits of its own picture that fly out and fall,
as in the game. Other effects the recordings keep show with the game's particles too: composters filling and bone
meal (green sparkles), potions splashing (in the potion's colour), fires put out, lava fizzing, smoke from
dispensers, spawners, wax put on or scraped off copper. Particles the server sends are drawn as well, with the
game's pictures: fishing (the bobber's wake, splashes, bubbles), sword sweeps, the hearts from a hit, block dust and
others. Particles a player's own game makes up by itself aren't in the recordings, so the viewer makes them up the
same way, from the blocks round the camera while the time slider moves: torch flames and smoke (soul and copper
torches in their colours, redstone torches' red dust), lit candles, furnaces, blast furnaces and smokers, fire,
campfire smoke rising in a column, lava popping, drips under blocks with water or lava on them, nether portals, end
rods and leaves falling (cherry petals, pale oak and the rest), and rain splashing on the ground as it rains. Each
block makes them as often as in the game (the nearest most often), so they look alike but not the same specks as the
player saw.

Mobs make their own particles too: blazes trail smoke and endermen and endermites trail purple specks, all the time;
bees carrying nectar now and then drip a drop; glow squids give off glowing specks. A sheep eating grass lowers its
head to the ground and nibbles for two seconds, as in the game (the recordings keep when it starts).
When the server tells the game something happened to a mob, the game's particles show it: the puff of smoke when a
mob's body vanishes after it dies, a mob appearing from a spawner, hearts when animals fall in love or a pet is
tamed (smoke when taming fails), and villagers' hearts, anger and happiness. In the nether, the air has the
biome's particles round the camera: ash in soul sand valleys and basalt deltas, red spores in crimson forests and
blue ones in warped forests (the biome is the backup's at the camera).

**Potion swirls.** Players and mobs under a potion effect give off swirls in the effect's colour, as the game shows
them: Speed light blue, Invisibility pale grey, and so on, mixed when they have several. They come from what the
recordings saw (each player's and mob's list of effect particles), so they start and stop when the effect did.
Faint ones (from a beacon) appear five times less often, and an invisible player or mob shows only an occasional
swirl, as in the game. Spiders spawned with effects on Hard difficulty show theirs too. Mobs are only shown near the
recorded player, so a spider with effects further away shows nothing.

**Saddles and armour on animals.** Horses, donkeys, mules, pigs, striders, camels and nautiluses wear their
saddles, horses their horse armour (leather in its dye), llamas their carpets, wolves their armour and happy ghasts
their harnesses, as the recordings saw them; it all moves with the animal.

**Pistons** push out and pull back over the game's two ticks when they did in the recordings, and the blocks they
push (or a sticky piston pulls) slide with them.

**Boats** sit on the water as in the game, and their paddles row when the player in them rowed.

**Leads.** Animals on a lead are tied as the recordings saw them: a rope sagging like the game's from the animal
to the player holding it, to a fence knot, or to a wandering trader leading his llamas.

**Armour stands** are shown where the recordings saw them, with their armour and held items, small or full size,
with or without arms and base plate, and posed as they were set. **Arrows** fly as the game's arrow model, turned
along their flight, and stay where they land; arrows stuck in a player (the game counts them) poke out of their
body, arms and head. The
models are read from the newest Minecraft 26.x game installed on this PC (older versions' games can't be read);
without one, mobs show as boxes sized and coloured by kind. What the recordings don't say is picked once per mob:
a horse's coat, a cat's or parrot's colours, a tropical fish's colour, and farm animals are their temperate kind.
Babies show adult-sized. Dropped items show as in the game, turning and bobbing: a block as a small block,
anything else as its picture made solid (wheat and seeds around a farm, a stack thrown to a friend); they
disappear when picked up or gone. Things thrown or shot fly as in the game: eggs, snowballs, ender pearls,
arrows, tridents, potions, fire charges, wind charges, fireworks and eyes of ender leave from where they were
thrown at the speed the recording gives, with the game's gravity and air drag, until they hit (arrows stay where
they land). Mobs disappear the
moment they're killed or leave the recording's view. Arrows and other things that fly past aren't shown. The first time you watch a stretch, the status line says the recordings are being read in full;
that can take up to a minute, and later views are quick.

What the recordings say about each player and mob is shown too:

- **Sleeping:** lying on their back.
- **Dying:** falling over sideways; mobs disappear after their death.
- **Holding and using items:** the game's own arm poses: an arm holding something a little lower and forward; a
  shield raised across the body; a spyglass to the eye; a goat horn to the mouth; a trident raised overhead; a
  brush held out; drawing a bow or crossbow with both arms along the look.
- **Turning:** the body follows the way they walk and turns after the head, never more than 75 degrees from it,
  as the game works it out (the recordings keep where they look, not the body).
- **Hurt:** a short red flash.
- **Burning:** the game's fire around them (and an orange flicker on mobs).
- **Invisible** (a potion of invisibility, or an invisible armour stand): only their armour and what they hold
  show, as in the game.
- **Spectator mode:** a player in spectator mode shows as the game shows them to other spectators: just their
  head, see-through.
- **Size:** players and mobs made bigger or smaller (the scale attribute) are drawn at their size.
- **Their hand, through their eyes:** reliving someone through their eyes (V), you see what they hold at the
  bottom right as the game shows it (or their bare arm), swinging when they swing; a map held with the other hand
  free is held up in both hands, showing its picture as it was then. Walking sways the view and the hand with it as
  the game's view bobbing does; the hand lags a little behind turns; a new item comes up from below; food and drink
  are raised to the mouth and bob while they eat or drink. Drawing a bow, loading a crossbow and raising a trident to
  throw show the game's poses (the bow and crossbow with their pulling pictures). Looking through a spyglass shows
  the scope over the screen and zooms in to a tenth, as the game does (the hand isn't drawn then).
- **Tridents, spyglasses, shields and spears** in a hand are drawn as the game draws them: the trident's and the
  shield's own models (the shield with its banner pattern), the spyglass's tube, a spear's long in-hand picture. A
  thrown trident flies as its model, prongs first. While one is being used it's held the game's way for that: a
  shield raised to block, a trident drawn back to throw (and through their eyes, the shield raised when it's in their
  main hand).
- **The view itself, through their eyes:** it widens while they sprint and narrows while they draw a bow (and
  under water), easing as in the game; it rolls when they're hit and tips over when they die. The screen shows what
  the game lays over it: flames along the bottom while they burn, the underwater tint and the water's fog, the
  carved pumpkin's view when they wear one, frost when they're freezing in powder snow, and the darkened edges (the
  vignette) that deepen the darker it is where they stand (from the backup's light there).
- **The other hand, through their eyes:** what they hold in their off hand shows at the bottom left as the game draws
  it (a shield, a torch, a totem...), swinging when that hand swings and a shield raised while they block with it. A
  map in one hand while the other holds something is held low in that hand over the arm, its picture as it was.
- **Mobs' own moves:** a creeper about to explode swells and flashes white (a charged one wears its blue swirl);
  an enderman carries the block it picked up, low in front of it with both arms forward, and opens its jaw when
  someone stares at it; an iron golem swings its arms when it attacks and holds out a poppy to villagers; a wolf
  tilts its head when it's interested, shakes itself dry after water or rain, and holds its tail higher when tamed
  or angry; horses lower their heads to eat grass and rear up; a ghast opens its mouth before it shoots; slimes and
  magma cubes squash when they land and stretch when they jump; bees beat their wings, phantoms flap; a shulker
  sits on the wall, floor or ceiling it's attached to, in its colour, and lifts its lid to peek out. A sheep's wool
  and other layers move with the body.
- **Angry wolves and bees:** a wild wolf angry at someone shows its angry face (bared teeth, red eyes) for as long as
  the recording said (tamed wolves keep their tame face, as in the game). Bees show their angry eyes when angry and
  their pollen when carrying nectar.
- **Villagers' hats:** a villager's job hat and its biome clothes' hat are shown as the game layers them (a farmer's
  or fisherman's straw hat hides a snowy or desert villager's own hat), from the game's own texture flags.
- **Capes:** a player's cape, as their recordings had it, hanging from the shoulders and swinging out as they move
  (not with an elytra on). Capes come from Mojang's texture server like the skins.

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
and boots in every material (leather in its dye colour, trims in their pattern and material, enchanted armour and
items with the game's moving glint), and the item in each hand as a
solid item held where Minecraft's own third-person view holds it. A block held shows its
texture. Items from the Hero's Journey datapack show as the plain item they're based on (their own pictures
come from the server's resource pack, which isn't on this PC). The recorded player's own gear is only listed
when it changes or every few minutes; until the first listing in a recording, the first one seen is assumed.
An elytra worn shows as folded wings on the back. Mobs hold and wear what they had too: a zombie's sword or
helmet, a skeleton's bow, a piglin's golden sword, a drowned's trident.

**Chests opening.** In close-up playback, chests open and shut when they did in the recordings (any chest a
recorded player could see, not only their own): the lid lifts on its hinge with the game's easing, takes half a
second, and shuts again the same way. Double chests, trapped chests, ender chests and copper chests too, and
shulker boxes (their lid rises and turns, as in the game). Barrels show open through their block.

**Paintings and item frames.** Paintings hang in their pictures at their real size, and item frames show their
birch border and what's in them (turned as it was), from the backup shown. Frames holding a map show the map (see
Maps). In close-up playback, framed maps show as they were at that moment (the backup's map with everything the
recordings saw drawn on it since), and the paintings and frames the recordings saw are shown instead: ones put up, taken
down, turned or given another item during a recording show that from the moment it happened.

**Chests, furnaces and other containers.** While you spectate or relive someone, when they open a chest (the
recordings keep chest lids opening) or put items into or take them out of anything (the Ledger log: barrels,
furnaces, hoppers, shulker boxes...), its screen shows as in the game: the container's slots (a large chest's 54,
a furnace's input, fuel and result) and their hotbar under it. The recordings don't keep what's inside, so the
contents are rebuilt: what the backup saved in it, with every item the Ledger log saw put in or taken out since,
up to that moment. The items and counts follow the log; which slot a moved item sits in is a best guess, and a
furnace's smelting (input turning into output) isn't logged.

**Health and food.** Above their hotbar: their hearts and food, from their own recording, as the game draws them:
the hearts blink white for a moment after a hit, shake when they're low (4 health or less), hop in turn with
Regeneration, and turn green when poisoned, black when withered and blue when freezing. Under water or short of
breath, their air bubbles show above the food and burst one by one.

**Effects.** At the top right, the icons of the effects they have (good ones on the top row, harmful ones below; a
beacon's in its paler frame), as the game shows them. Their own recording keeps their effects when it can; otherwise
the effect swirls round them tell which effects they have (each effect's swirls are its own colour), read from the
game's own list of effects. Effects whose swirls are hidden don't show then.

**The item's name.** Switching to another hotbar slot shows the item's name above the hotbar for two seconds, fading
out, as in the game.

The experience bar's progress isn't in the recordings (ServerReplay doesn't keep it); the level shown is from the
backup (or the recording when it has it).

**Text on their screen.** While you relive someone, what the server put on their screen shows where the game shows
it, from their own recording:
- **The action bar** just above their hotbar and armour ("You can sleep only at night", "Sleeping through this
  night", a plugin's or command's messages): three seconds, fading over the last one. A jukebox starting near them
  shows "Now Playing: ..." with the song's name there, its colour cycling through the rainbow as in the game.
- **Boss bars** at the top of the screen (the ender dragon, the wither, raids, a server's own bars): their colour,
  notches, how full and their name, as the server set them.
- **Titles and subtitles** big in the middle of the screen, fading in and out with the times the server set.
- **Messages the server sent them** in the chat at the bottom left, with the players' chat from the logs: deaths,
  joins and leaves, advancements, "Respawn point set", replies to their commands, in their colours.

The text is in the game's English (the game's own wording for each message), with its colours, bold and italic.

**Maps and map art.** Maps hang in the world where they hang in the game: every map in an item frame (or glow
item frame) is drawn on its wall, floor or ceiling, turned as it was, close up where the real blocks are shown.
**Maps** (next to *Inventory*) opens a gallery:

- **Walls of maps**, each put together as it hangs (a wall of 3 x 3 maps is one picture), biggest first. Click
  one to go and look at it.
- **Every map** the backup has, newest first, with its scale (1:1 to 1:16). Click one to look down on the area
  it shows.
- **On walls in the world** turns the framed maps in the world on and off.

The maps and frames come from the backup of the terrain shown (as the time slider moves, another backup's maps).
The pictures are the maps' own saved colours, in the game's map colours read from the Minecraft installed on
this PC. Finding the framed maps reads every entities file of the backup once (about half a minute; then kept).

**Inventory and hotbar.** **Inventory** (next to *Relive*, or the **E** key) shows what the player you
spectate (or the one nearest the view) carries. While you spectate or relive someone, their hotbar is also
shown above the timeline, with the slot they hold outlined:

- **The hotbar is exact** when the player's own recording covers the time: the recordings keep every hotbar
  slot when they start and whenever one changes, and which slot is held. Exact slots have a green dot. What
  they hold in their hand is then the selected slot's item too.
- Items look as in the game's hotbar (read from the game's item models: block items show their block).
  Shields show their plate (the game draws them with its own code); beds, chests and heads show their name.
- Blocks they hold are drawn as small blocks in their hand, as in the game; other things as solid items made
  from their picture (like the game's item models), and a shield as a shield with its banner pattern. Items sit in
  the hand where the game puts them (its third-person placement for each item, mirrored in the left hand); a bow
  or crossbow being drawn shows the game's pulling pictures and both arms held up.
- **Everything else comes from a backup**: the main inventory, armour, off hand and the **Ender chest** tab,
  from the player's save in the library backup taken at or before the time shown. The panel says which backup
  and how long before it was, and the health, food and level saved then.
- Other players' hotbars aren't in anyone's recording (the game only sends you your own); only what they hold
  and wear is seen.

**Go to a day and time.** The **Go to** box in the bottom bar (after the date; clicking the date jumps to it)
takes a day and an exact time, in your time zone, in the forms you'd naturally type:
- `24 May 2026 20:31:09`, `2026-05-24 20:31`, `24/05/2026 8:31pm` (day first), `May 24 20:31`, `24.05.26`;
- a time alone (`20:31`, `8pm`) on the day shown; `today` or `yesterday` with or without a time;
- a day alone goes to the first time anyone was online that day;
- a place (`x y z` or `x z`) moves the camera there.

The calendar button next to it opens your browser's date and time picker. Press Enter to go. A time outside
the history goes to its nearest end and says so.

**Activity heatmap.** With the server's Ledger log, the **Activity heatmap** box (under *Show*) colours the
map by how much players did there: pale yellow for a little, red for a lot, in squares of 16 x 16 blocks.
- **What:** everything, building (blocks placed), mining (broken), fighting (mobs killed), or chests and storage.
- **When:** the day of the time slider, its week, its month, or the whole log.
- The note under it says how many squares and where the busiest one is. Only what players did counts (not
  fire or decay).

**Blocks as at that moment.** With **As at that moment** ticked (next to *Real blocks*, on at first), the blocks
near you are the world as it was at that time, not as the next backup has it: a build half done, a hole just
dug, a house not yet there. Two sources are used:
- **The recordings,** during exact playback, wherever a recording had the chunks loaded: exact, including crops
  growing and doors opening.
- **The server's Ledger log** everywhere else, at any time it covers, with or without recordings. Ledger is the
  mod that logs every block placed or broken; your server has it since 14 November 2025. The app reads its
  database from the newest backup (`ledger.sqlite`), never changing it. It takes the backup saved after that
  moment and puts back what was there before each change Ledger logged since. Ledger logs what players,
  explosions, fire and the like do, not crops or redstone. Every recording keeps the chunks its player's game
received and every block placed or broken after that; the app puts together what all the recordings running at
that moment saw (the latest change wins). The nine pieces nearest you are rebuilt every 30 seconds of playback
and when you jump; the label next to the switch says which moment they show. In between, blocks placed or
broken appear as they happen: the chunk (16 x 16 blocks) where it happened is rebuilt on its own, usually within
a second. Further away, and where no
recording had the chunks loaded, the backup's blocks stay. Building a piece takes a few seconds (the first time
a recording is used, a little longer while the app notes where its chunks are kept). Light where a block was
changed is estimated from around it, as Flashback keeps no light updates.

**Year in review.** The **Year in review** button (top left) opens the server's year as cards in a new tab
(the address is `/review/<year>`):
- the year in numbers;
- players and hours by month;
- when the server was busiest (a weekday x hour heatmap, and the busiest day);
- how everyone died (the top causes and the oddest one);
- records: longest session, furthest travelled, most blocks mined and placed, most mobs killed;
- the biggest builds of the year (with a Ledger log): who, the main blocks, how many and where;
- disasters (with a Ledger log): how often a build was damaged, blocks lost, and the worst times;
- the rarest advancements and who got them first;
- a list of everyone who played.

Each name opens that player's year (`/review/<year>/<name>`):
- hours, days and rank;
- hours by month, and their favourite hour and day;
- firsts: first time online, first advancement, first death;
- how far they travelled and how (walking, flying, by boat, on horses...);
- **builds** (with a Ledger log): blocks placed in builds and their biggest projects (a project is one place
  built on over one or more evenings), with the main blocks, where, when, and who helped;
- blocks mined and placed, with their top three;
- mobs killed and what killed them;
- deaths: how many, the most common cause, the worst day;
- advancements earned, and the rarest.

Pick another year at the top.

**Sharing:**
- **A card as a picture:** hover a card and press **Picture** to save it as a 1080 x 1350 image (the shape
  phones and Instagram use), to post in your server's channel.
- **A page as a file:** **Export…** saves the page as one file that opens in any browser without the app:
  - the overview's file has the server's cards and everyone's hours;
  - a player's file has only their own year plus the server's totals;
  - chat is never quoted;
  - untick **Include deaths** to leave the deaths out.

Where the numbers come from:
- **Hours, days, deaths and chat** come from the server logs, so days with no log are missing (a player's card
  says so when the game's own play time disagrees).
- **Travel, blocks and mobs** come from Minecraft's statistics in the backups: the newest backup before the
  year minus the newest at its end. The card says which backups it spans.
- **Times** are in this PC's time zone, with summer time.
- **Ignored players** are left out. Renamed players count once, under their current name.

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

**Working on.** Anything that takes a while shows in the **Working on** card (bottom right) with what it's
doing and how far it is: recordings being read in full, real blocks being built, maps being found, highlights
being exported, and **Watch in Flashback** (finding the recording, making the short copy, starting Minecraft). It
goes away when nothing is running, and is hidden in Relive and with F1.

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

**Play** on a moment follows the player it's about (spectating them from behind or through their eyes, as
Spectate is set): the one its title names first, else the one whose recording covers it. **Esc** stops following.

**Watch in Flashback.** One button for the exact moment shown: it takes the recording of the player you follow
(or the nearest recorded player), copies the minute before and two minutes after into the Flashback of that
recording's Minecraft version, named "<player> at <date time>", with a green marker at this moment and a scene
starting there, and starts that Minecraft (Prism Launcher). In Minecraft: Flashback → Replays → the copy (Flashback
can't be told from outside to open a replay, so that one click is yours). The status line shows the copying.

**Hide UI and pictures.** **Hide UI** (or **F1**, as in the game) hides every panel and label for a clean view;
F1 or **Esc** brings them back. **Picture** (or **F2**) saves a PNG of the 3D view as it is now (into your
browser's downloads), named after the world and the time shown. Both work while reliving.

**Explosions.** Creepers, TNT, fireballs, withers, end crystals and beds blowing up show as a flash, a burst of
light and rising smoke, sized to the blast, with a short camera shake while reliving close by. They come from the
Ledger log (every block a blast broke, with its cause), because the recordings leave explosions out when
ServerReplay's `optimize_explosion_packets` is on.

**Fine scrubbing.** Hold **Shift** (or Ctrl) while dragging on the timeline: the time moves 20 times slower than
the mouse, to creep back and forth frame by frame. **,** and **.** step one game tick (1/20 s) back and on (with
Shift: a second).

**Relive a moment, like a memory.** **Relive** on a moment in the list (or the **Relive** button by
*Spectate*, for the next minute of the player you spectate or the one nearest the view) replays it as if you
were there again:

- The screen goes white with when, where and who was there, and a loading bar, while their recording is read
  and the real blocks around them are built (it says which it's waiting for; the first time can take a minute).
  A memory only ever shows real blocks: the 640 blocks round the player load first, and a haze ends the view
  inside them (no loading grid, no smooth map).
- The camera drifts down from above and behind into the player's eyes (with *First person*) or over their
  shoulder (with *Behind*). Their position and look are smoothed, so it plays like a video.
- **V** (or **F5**, as in the game) switches the view: their eyes, over the shoulder, or facing them. The camera
  swings round to the new view, and like the game's own camera it stops in front of a wall between it and the
  player (with no room at all, it looks out from their eyes).
- It opens with a memory look (a warm haze closing in, a soft vignette, warmer colours) that fades away a few
  seconds in, so the rest plays as it was; it comes back for the ending.
- No panels: a letterbox and the game's own HUD, from the game's own pictures at its GUI scale 2: their hotbar
  and selected slot, hearts, food, armour (from what they wear), the experience bar, a crosshair when seeing
  through their eyes, and chat in the bottom left as in the game. Only the names of people close by are shown.
- On a moment, time slows to a third for a few seconds around its key event (a death, an advancement, a first).
- At the end it fades back to white with the caption, then puts the viewer back as it was. **Esc** leaves any
  time.

It needs a recording of the time (moments without one have no Relive button).

**Dev mode** (press the **`** key: top left of the keyboard, under Esc, left of 1; or open the page with
`?dev=1`; it's remembered until you turn it off the same way) shows a panel with:

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
- **Search what happened:** type words and press Enter (or **Search**). Every line holding all the words (any
  case) is listed, newest first, from three places:
  - the server logs: joins, leaves, chat, commands, deaths, advancements, server starts and crashes;
  - what the recordings saw: messages and the action bar, titles and boss bars, deaths and hurts, blocks broken,
    what was held and worn, the hotbar, items dropped and picked up, signs written, chests opened, mobs (named,
    babies, variants, angry, tamed, hearts, villagers' moods, bodies vanishing), potion effects, particles, game
    modes, totems, leads, paintings and frames...;
  - the Ledger block log: blocks broken, placed or changed, items put in or taken from containers, picked up or
    dropped, mobs killed or changed, by whom or by what (a creeper, fire, lava...).

  For example `diamond_ore D_HumanCrafter`, `wolf angry`, `slain`, `Sleeping`, `creeper block-break`. Click a line to
  go to that moment: the camera goes to the block for lines with a place (shown after @), or spectates the player.
  Only recordings already read in full are searched (any version of the viewer); the line under the box says how
  many weren't, and **Read them all** reads those in the background (a few at a time: it can take a long while; search
  again afterwards). The first search after starting reads each recording's data once (it can take a minute); then it
  is kept and searches are quick. At most the newest 400 are listed (the count says how many there were).

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
     a **copy** appears in Flashback's replay list, named after the highlight and whose view it
     is (`jamie burns to death – vegitogod22`). It holds only the 5-minute parts of the recording
     around its highlights, with the chunk data they use (a 2-hour, 424 MB recording becomes a
     67 MB copy in about 10 seconds). It has a green **▶ title** marker at each highlight's start
     and a red **■ title** marker at its end. Its editor has **one scene per highlight whose
     export range is already set**: open the copy, choose the scene, press Export.
   - A **progress bar** shows how much is copied and about how long is left.
   - When it's done, the copies are listed under the button (and kept there): each with whose
     view it is, its Minecraft version, and **Open Flashback**, which starts the Minecraft (Prism
     Launcher instance) that has that Flashback. Then in Minecraft: Flashback → Replays → the copy.
   - **One replay of everyone's views:** Flashback can join copies itself. In its replay list,
     **Edit** on one copy → **Combine With Other Replay** → pick another copy (again for each
     more). The copies are short, so this is quick.
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
