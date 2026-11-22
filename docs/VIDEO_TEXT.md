# Text on finished videos (no editing skills needed)

## The live editor

In the **Videos** tab choose your video (the app finds the Flashback edit and recording it was made
from), then press **Open live editor** (or `mcsm video-editor --video FILE --world <library world>`).
A page opens in your browser where the video plays with **all your texts and pictures drawn on it while
you change them**. Everything is done there; the Videos tab only picks the video (and can still write
the chat and events as subtitles, see below).

### Before exporting: open the Flashback edit itself

You don't need an exported video. In the Videos tab press **Open a Flashback edit…** and choose the
edit (`flashback/editor_states/<replay id>.json` in your Flashback folder; the scene open in Flashback
is used), or `mcsm video-editor --edit STATE.json [--scene N] --world <library world>`.

- The picture is the **app's 3D map seen through your edit's camera**, from the library backup made
  right after the recording: **real Minecraft blocks with Minecraft's own textures** within about 80
  blocks of the camera's path (taken from the Minecraft you have installed; slabs, posts and carpets
  keep their shape, plants are crossed, grass, leaves and water get their colours), and smooth coloured
  terrain further away. No lighting, shaders or animated mobs: for the exact picture use
  **Flashback (real)** below. The players are coloured boxes
  with their names, moving along their recorded paths (sampled about once a second, so close-ups are
  approximate). The timeline is the edit's export range, with any timelapse taken into account.
- **Camera view / Map view** (next to the time): *Map view* looks at the map from above, with your
  camera's path as a yellow line and the camera as a red marker (drag to turn, scroll to zoom). Texts are
  shown in *Camera view*.
- **Camera keyframes** (the *Camera* panel): the timeline's top row shows your edit's camera keyframes as
  diamonds (yellow = smooth, white = straight, blue = eased, red = hold then cut). The preview moves the
  camera the way Flashback does (its smooth spline, eases and holds, read from Flashback's source).
  - **Fly**: drag on the picture to look around, <kbd>W</kbd> <kbd>A</kbd> <kbd>S</kbd> <kbd>D</kbd> to move,
    <kbd>E</kbd>/<kbd>Q</kbd> up/down, <kbd>Shift</kbd> faster, <kbd>Esc</kbd> to stop.
  - **+ Keyframe here** (<kbd>K</kbd>): a keyframe at the playhead from what you see (while flying: where you
    flew to). **Delete keyframe**, <kbd>[</kbd>/<kbd>]</kbd> previous/next keyframe.
  - Drag a diamond to move it in time; with **snap to beats** it lands on the song's beats, and **Snap all
    to beats** moves every keyframe to its nearest beat.
  - The selected keyframe's time, position, yaw/pitch, roll and **Movement** (smooth, straight, ease in/out,
    hold then cut) can be typed; **Set to current view** moves it to where you flew.
  - Undo/redo include camera changes; **Back to the edit's camera** forgets them. Your edit's own file is
    never changed: the changed camera goes into the app's copy (replacing that copy's camera) when you
    press *Put … in Flashback*.
- **Flashback (real)**, the third view: the **real Minecraft picture**, drawn by your running Flashback.
  The editor moves Flashback's time and camera to where its own camera is (or where you fly) and shows
  what the game draws, with your texts on top. It needs the small companion mod:
  1. Videos tab → **Install the Flashback link…** → choose the Minecraft instance that has Flashback →
     confirm (it copies one small file into that instance's `mods` folder; or `mcsm flashback-link install
     --instance NAME`). It needs Minecraft 26.1 or newer with Fabric.
  2. Start that Minecraft and open the replay of the edit in Flashback (the original, or the app's copy).
  3. In the editor choose **Flashback (real)**. If something is missing the picture says what (not
     running, other replay open). `mcsm flashback-link status` shows the connection too.

  The mod only listens on this computer (127.0.0.1) and needs a secret token from your Minecraft
  folder. The picture is fitted by height (the same vertical field of view as the camera the texts are
  drawn with); Flashback's game area can be wider or narrower than your video. Jumping backwards in time
  makes Flashback re-read the recording, so it can take a moment; playing updates as fast as the game
  answers. Texts aren't in the replay until you press *Put … in Flashback*: until then the page draws them
  over the picture.
- Everything else works as with a video: texts in the world, follow a player, chains, the song and
  beats. **Put world texts & song in Flashback** makes the copy in the same Flashback folder as the
  edit (one copy per edit and scene; making it again replaces it). Open it in Flashback, check, and
  export once. *Make the video* needs an exported video, so it's hidden here; open that video in the
  editor afterwards for on-screen texts and pictures (your texts are kept per video, so add the
  on-screen ones there).

### Adding things

- **+ Title in the world**: real Minecraft text in the scene (projected with your edit's camera, so it
  moves correctly as the camera moves). **+ Title on the video**: drawn on top. **+ Subtitle** (bottom,
  dark box, 4 s), **+ Corner label** (a place or a name, lower left).
- **+ Picture**: a logo, photo or watermark on the video (PNG, JPG, GIF, WEBP, BMP). The picture is
  copied into the editor's folder (your file isn't touched); **Replace…** swaps it and keeps its place
  and timing. Pictures can't be put *in* the world yet, only on the video.
- **+ Follow-up**: a new text that appears **after the selected one goes**, 0.5 s later by default.
  Change the **Gap** to any number of seconds (negative = they overlap), or choose *after it appears*
  instead of *after it goes*. Follow-ups can be followed too, so you can build a chain of any length;
  moving the first one on the timeline moves the whole chain. Any text or picture can follow any other
  with **Follows**; choose *nothing* to give it its own time again (it stays where it is).
- **Duplicate**, **Delete** (texts that followed the deleted one keep their current time), **Undo**,
  **Redo**.

### Each text

- **Lines**, each with its own letter size, and a name.
- **When:** *Appears at* (a second, *now* or *when the camera stops*), *Until* (a second, *now* or the
  end) or **Or lasts** a number of seconds, and **Follows** / **Gap** for chains.
- **Animation:** appears at once, fades in, pops in, words one by one (instantly, fading or popping),
  **rises into place**, **slams down** (comes in big and lands like a stamp); speed, time between words,
  fade out at the end.
- **Look:** **opacity** (see-through), colour, border and its colour, shadow, dark box, **3D depth**
  (3D letters: darker layers behind and below the letters, in the world and on the video), **italic**,
  **underline**, always bright (in the world), letters with border or plain and a font (on the video).
- **Turn:** **Rotate** turns the text in its own plane (degrees, clockwise; a text with several lines
  turns as a whole). On the video, **Turn** and **Tilt** add a 3D perspective. In the world, **Faces**
  chooses how it looks at you:
  - *the camera (automatic)*: turns to face the camera (upright on a spot);
  - *the camera, always*; *the camera, staying upright*;
  - *one way, like a sign*: it stays put facing where the camera looks when it appears, so a moving
    camera sees it from the side. **Turn** spins it, **Tilt** tips it (-90 lays it on the ground face up,
    like writing on the floor).
- **Place:** in the world *In front of the camera* (Right / Up / Distance in blocks), **At a spot…**
  (click on the video where it should stand, e.g. far out on a mountain: the app follows the camera ray
  through the real terrain from the library backup made right after the recording; far spots get bigger
  letters) or **Follow a player** (the text rides on the player and moves with them exactly in
  Flashback; Right/Up move it beside/above the head). On the video: *Across* / *Down*.
- **Size ×**: from 0.02 (name-tag size above a player) to 20 (huge, on a far mountain). Following a
  player starts small.
- **Pictures:** height (share of the video height), across/down, opacity, rotate, appears / fades in /
  rises into place, fade out, and the same timing and chains as texts.

### Working with it

- **Timeline** under the video: one bar per text (blue = in the world, purple = on the video, green =
  picture, light green = where the camera stands still; ↳ = a follow-up). Drag a bar to move it (a
  follow-up's drag changes its gap), drag its right edge to change how long it stays, click elsewhere to
  move the playhead.
- **Drag a text or picture on the video** to move it; texts in the world move left, right, up and down
  in the world (in blocks, as seen from the camera).
- **Keys:** <kbd>Space</kbd> play/pause, <kbd>←</kbd>/<kbd>→</kbd> one frame (with Shift: one second),
  <kbd>I</kbd>/<kbd>O</kbd> start/end of the selected text at the playhead, <kbd>Ctrl+Z</kbd> /
  <kbd>Ctrl+Y</kbd> undo/redo, <kbd>Ctrl+D</kbd> duplicate, <kbd>Delete</kbd>.
- **Saved automatically** for each video (in `%LOCALAPPDATA%\mcsm\video-text`, pictures in its
  `images` folder; never next to your video), so you can close the page and continue later.

### Music

**Choose a song…** (MP3, OGG, WAV, FLAC, OPUS, M4A, AAC; the app keeps a copy, your file isn't touched).
The song **plays along** in the page while you edit (untick *Play along* to edit in silence).

- **Starts at**: the second of the video where the song starts (*now* = the playhead). **From song**:
  seconds into the song to start from, e.g. to skip its intro.
- **Tempo**: detected when you choose the song (or press *detect*); type the BPM and the **first beat**
  (seconds into the song of the first strong beat) yourself if the detection is off. The timeline shows
  the song as an orange band with **orange ticks on every bar and blue ticks on every beat**, so you can
  drag texts onto the beat.
- **In Flashback**: *Put world texts & song in Flashback* puts the song on **Flashback's own audio track**
  of the copy (a track named `mcsm music`; it starts at the right replay tick and comes out in Flashback's
  export) and a **marker on every beat or bar** (*Beat markers*: every beat / every bar / none) on
  Flashback's timeline: orange dots for bars, blue for beats, named like "♪ Bar 3". Click one in Flashback
  to jump there and put camera moves on the beat. Making it again moves the song and markers; your own
  tracks and the recording's own markers are kept. Flashback plays audio at the replay's speed, so in a
  sped-up (timelapse) part the song plays fast: the app warns you, and for such videos use the song on
  the made video instead.
- **In made video**: tick it to mix the song into *Make the video…* (with the video's own sound, at the
  **volume** you set; the video doesn't get longer).

### Making it

- **Put world texts & song in Flashback** makes one copy of the recording with all in-world texts, plus a copy
  of your edit with the same export range, in the Flashback folder for its Minecraft version. Open it in
  Flashback and press Export: the video comes out with the text in the world (lit by it, hidden behind
  blocks and players). You can keep editing the camera there with the texts visible. Making it again for
  the same video **replaces** that copy (same name and id, swapped in when complete; close the replay in
  Flashback first), and changes you made to the copy's edit are kept.
- **Make the video with on-screen texts & pictures** makes `<video> (with text).mp4` next to the original
  with ffmpeg (the fastest encoder your PC really has: NVIDIA, AMD or Intel, else the normal one).

Your original video, recording and edit are never changed. The page draws with your browser's fonts;
the finished video uses Minecraft's own font in the world and your chosen font on the video. The page's
preview of *one way, like a sign* and of Turn/Tilt is an approximation; Flashback shows the real thing.

## Chat and events as subtitles

You exported a video from Flashback and want the in-game texts on it: chat, "X joined the game",
deaths, advancements, the date. Normally that means placing every text by hand in an editor such
as DaVinci Resolve. `mcsm` does it for you: it works out which real moment on the server each
second of the video shows, and writes every text at the right time.

```
mcsm video-text SERVER --video "Cinematics\Thanks_for_playing_intro.mp4"
```

SERVER is your backup, several backups, or a [library](LIBRARY.md) world folder: whatever holds
the logs and the recording the video was made from.

## How it lines up

1. **Which edit made the video:** Flashback keeps your edits (scenes, export range, timelapse
   speed-ups) in an *editor state* per recording. `mcsm` looks at every Flashback folder on the PC,
   including launcher instances (PrismLauncher etc.), and finds the scene whose export has the
   same length as your video. On the owner's videos the lengths matched to 0.05 s (337.6 s and
   1210.8 s), timelapse parts included. If several scenes have the same length, or you changed the
   edit after exporting, give it yourself: `--state <editor state .json> --scene N`.
2. **Which recording:** the editor state belongs to one recording (its uuid). It must be among
   the loaded recordings; otherwise add it with `--replay FILE`.
3. **Which time:** the recording's start time + the scene's export range + the timelapse
   keyframes give the real time of every video frame. Sped-up parts are handled: texts there are
   shown for a shorter time so they don't pile up.

Flashback's rule, read from its source: consecutive TIMELAPSE keyframes form a sped-up part only
when the second one's value is larger; elsewhere the video runs at normal speed.

## What you get

Next to the video (or `-o DIR`):

| File | Use |
|---|---|
| `<video>.ass` | Styled texts: Minecraft-like chat (bottom-left, dark box, yellow joins, green advancements), date (top-left), titles and name cards. VLC and mpv show it automatically when it sits next to the video with the same name. ffmpeg can burn it into a copy of the video. |
| `<video>.srt` | Plain captions, e.g. for YouTube (*Subtitles → Upload file*). |
| `<video>.texts.json` | The list of texts with their times, to edit. |

Options: `--kind chat --kind death …` (only some kinds; default chat, joins, leaves, deaths,
advancements and the date), `--font NAME` (default Arial).

Burning the texts into a finished copy of the video (the original is never changed):

```
ffmpeg -i video.mp4 -vf "ass=video.ass" -c:a copy video-with-text.mp4
```

## Notes

- Flashback renders without the game HUD, so the chat isn't in the video to compare with; the
  timing comes from the edit and the recording (checked: an advancement for spotting a parrot
  appears exactly while the player is among birds).

## Text in the world (real 3D text)

`mcsm world-text` puts **real Minecraft text into the world** of a *copy* of a recording: a text
display entity, the same thing `/summon text_display` makes in-game. It sits at a place in the
world, gets lit by it (or always bright), and is hidden behind blocks and trees like anything
else. Open the copy in Flashback (your edit is copied to it) and export again: the text is part
of the scene.

```
mcsm world-text --replay "2026-05-24--18-49-45.zip" --text "Thank you for playing" ^
    --in-front-at 20 --distance 35 --size 5 --colour gold --bold
```

- **Where:** `--at X,Y,Z` (e.g. coordinates from F3 in Flashback), or `--in-front-at SECONDS`:
  in front of where your edit's camera looks at that second of the exported video,
  `--distance` blocks away (follows CAMERA keyframes; orbit and entity-tracking cameras aren't
  followed, so use `--at` for those).
- **Look:** `--size` (letter height in blocks), `--colour` (gold, aqua, red… or `#RRGGBB`),
  `--bold`, `--no-shadow`, `--no-glow` (lit by the world instead of always bright); the app adds a
  black border (black copies just behind the text, best seen from the camera's direction),
  `--facing center` (always faces the camera, default) / `vertical` / `fixed` with `--yaw`.
- **Always from the original:** a replay the app already added text to is never used as the source (its
  old text would stay in the world); the app goes back to the original recording.
- **Output:** a new replay named `… (text)` with a new id, put into the Flashback folder that
  matches the recording's Minecraft version, plus a copy of your edit for it; or `-o FILE`.
  The original recording and edit are never changed.
- **When:** `--appear-at SECONDS` / `--until SECONDS` (seconds of your exported video, from your
  edit): the text is added to the world at that tick of the recording and removed at "until".
  Without them it's there the whole recording. `--border` adds the black border.
- **Rotation, facing, 3D letters (from the live editor):** a turned text uses the display's own
  rotation (a quaternion) about the middle of the whole text; *one way, like a sign* is a fixed display
  turned to where the camera looked; 3D letters are darker copies stacked behind the text. These were
  built from the game's data formats and checked in tests, not yet watched in Flashback: tell us if a
  text turns the wrong way.
- **Animations in the world:** pops use the game's own smooth size animation (display interpolation),
  rising texts glide up with the same interpolation,
  fades step the text's opacity every tick, and words one by one are one text per word, placed with
  Minecraft's font widths so the line looks the same. If you jump to a later moment in Flashback, the
  text is shown as it is at that moment (no replayed animation).
- **Versions:** recordings made with Minecraft 26.1.x, 26.2 and 26.3. The packets were checked
  byte for byte against what those Minecraft versions write themselves.
- The text is added at the start of every chunk file of the recording, so it's there wherever you
  jump to. If the recorded player changes dimension, the text appears again from the next chunk
  file on (and also exists in the other dimension at the same coordinates).
- Tried in the owner's Flashback (26.1.2, 2026-11-22): the text is in the world (a player walking
  past hides it). Seen from other directions the black border looks different (it is built for the
  camera's direction at the moment it appears).
