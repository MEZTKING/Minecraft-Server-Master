# Text on finished videos (no editing skills needed)

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
