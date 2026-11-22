# Text on finished videos (no editing skills needed)

## The easiest way: the live editor

In the **Videos** tab choose your video, then press **Open live editor** (or
`mcsm video-editor --video FILE --world <library world>`). A page opens in your browser where the video
plays with **all your texts drawn on it while you change them**.

- **Any number of texts**, each **in the world** (real Minecraft text in the scene, projected with your
  edit's camera, so it moves correctly as the camera moves) or **on the video** (drawn on top).
  Buttons: **+ Title in the world**, **+ Title on the video**, **+ Subtitle** (bottom, dark box, 4 s),
  **+ Corner label** (a place or name, lower left), **Duplicate**, **Delete**.
- **Where in the world:** *In front of the camera* (a title, placed relative to the camera when it
  appears; it stays at that spot in the world, so a moving camera leaves it behind) or **At a spot in the
  world…**: click on the video where the text should stand, e.g. far out on a mountain. The app follows
  the camera ray through the real terrain (read from the library backup made right after the recording)
  and stands the text there, upright and turning to face the camera, so it looks like part of the
  landscape; far spots get bigger letters automatically. Far spots are exact; right in front of a low
  camera it can be a block off.
- **Timeline** under the video: one bar per text (blue = in the world, purple = on the video, green = where
  the camera stands still). Drag a bar to move it in time, drag its right edge to change how long it
  stays, click elsewhere to move the playhead.
- **Drag a text on the video** to move it: on-screen texts move freely; texts in the world move left,
  right, up and down in the world (in blocks, as seen from the camera).
- **Each text:** a name, one or more lines (each with its own letter size), when it appears (a second,
  *now*, or *when the camera stops*) and until when, animation (appears / fades / pops / words one by one,
  speed, gap between words, fade out), colour, border and its colour, shadow, dark box, always bright (in
  the world) or 3D letters and a font (on the video), and its place and size.
- **Keys:** <kbd>Space</kbd> play/pause, <kbd>←</kbd>/<kbd>→</kbd> one frame (with Shift: one second),
  <kbd>I</kbd>/<kbd>O</kbd> start/end of the selected text at the playhead, <kbd>Ctrl+Z</kbd> /
  <kbd>Ctrl+Y</kbd> undo/redo, <kbd>Ctrl+D</kbd> duplicate, <kbd>Delete</kbd>.
- **Saved automatically** for each video (in `%LOCALAPPDATA%\mcsmideo-text`, never next to your
  video), so you can close the page and continue later.
- **Make it:** **Put world texts in Flashback** makes one Flashback copy with all in-world texts (making
  it again replaces it); **Make the video with on-screen texts** makes one new video with all on-screen
  texts drawn on.

The drawing in the page uses the fonts your browser has; the finished video uses Minecraft's own font in
the world and your chosen font on the video.

## The app's Videos tab

1. **Finished video → Choose…**: pick the video you exported from Flashback. The app finds the
   Flashback edit it came from (even if you changed the edit afterwards, e.g. made it longer), the
   recording, and where the camera stops moving at the end.
2. **Big text** and an optional **small text above** (e.g. "Welcome to" / "Our First Expedition").
3. **When:** where the camera stops at the end (good for titles), or at a second you choose, and
   optionally **until second** (empty = to the end of the video). In the world the text then really
   *appears* at that moment of the recording (and disappears at "until"), so it's not visible
   earlier in the shot.
4. **Look:** colour (white by default), **black border**, and for on-screen text optionally
   **3D letters**. On-screen text uses Minecraft's own logo font ("Minecraft Ten") when it's
   installed, else Arial Black.
5. **Animation:** appears at once, **fades in**, **pops in** (springs out a little too big, then
   settles), or **words one by one** (instantly, fading or popping), with the **speed**, the time
   **between words** and an optional **fade out** at the end. With two lines, the big line starts once
   the small one has appeared. More look options: **border colour**, **shadow**, **always bright**
   (in the world; off = lit by the world, dark at night) and a **dark box** behind the text.
6. **Place in the world (blocks):** move the text **right** / **up** (negative = left / down), set its
   **distance** from the camera and make it bigger or smaller (**size ×**). **Preview world position**
   shows a frame of your video with the text drawn where it will be in the world, worked out from your
   edit's camera position, angle and zoom (checked against the real result in Flashback). Adjust and
   preview again until it sits right, then put it in the world.
7. Then either:
   - **Put it in the world (Flashback)**: real Minecraft text in the scene (lit by the world,
     hidden behind blocks). A copy of the recording named `… (text)` and a copy of your edit (with
     the same export range as your video) go into the Flashback folder for its Minecraft version.
     Open it in Flashback and press Export: the video comes out with the text in the world.
     Pressing it again for the same video **replaces** that copy (same name and id, made as a new file
     and swapped in when complete; close the replay in Flashback first). Changes you made to the copy's
     edit in Flashback are kept. Other videos get their own copy.
   - **Put it on the video (new file)**: `<video> (with text).mp4` next to the original, made with
     ffmpeg (the fastest encoder your PC really has: NVIDIA, AMD or Intel, else the normal one).
   - **Preview**: one frame with the on-screen text, to check size and place first.
   - **Chat & events as subtitles**: the `video-text` files below (load your server first).

Your original video, recording and edit are never changed.

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

## Not yet

- A page where you watch the video with the texts on it, switch texts on/off, change words and
  drag them on a timeline, add your own titles, and press one button to make the finished video.
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
- **Animations in the world:** pops use the game's own smooth size animation (display interpolation),
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
