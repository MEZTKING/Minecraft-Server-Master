# Syncing music to the video

`mcsm sync` can place a song on the same timeline as your game-event markers, so you can
cut on the beat and make big moments land on the drop.

There are four building blocks; use any combination.

## 1. Beat grid – know where every beat is

A beat grid is defined by:

| Option | Meaning | Default |
|---|---|---|
| `--bpm 128` | Tempo in beats per minute | detected from `--music` |
| `--beat-offset 0.42` | Seconds into the song where the first **downbeat** (beat 1 of bar 1) is | detected / 0 |
| `--beats-per-bar 4` | Time signature numerator (3 for a waltz) | 4 |
| `--music-start 0:05` | Where the song starts on the **video** timeline (may be negative: `-0:10` means the song started 10 s before the video) | 0 |

Every beat then sits at `video_time = music_start + beat_offset + k × 60 / bpm`.

`--beat-markers bar` (the default) writes a marker on every bar; `beat` writes one on every beat;
`none` writes none. These go into the main EDL/CSV/Audacity files and also into separate
`NAME.beats.*` files, so you can import them on their own.

## 2. Beat detection – when you don't know the BPM

```bash
mcsm music song.wav
```

```
Tempo       : 128.0 BPM
First beat  : 0.387s into the song
Length      : 3:12
Confidence  : 0.93  (below ~0.3: check by ear, set --bpm manually)
```

Or directly inside `sync` with `--music song.wav`.

- **WAV** files work with nothing installed.
- **MP3, OGG, FLAC, M4A…** are converted with **ffmpeg** if it is on your PATH
  (download from https://ffmpeg.org). Without ffmpeg, convert the song to WAV in Audacity
  (*File → Export → WAV*) or give `--bpm` yourself.

How it works (all in `mcsm/music.py`):

1. The song is mixed to mono and split into ~23 ms frames; each frame's loudness is measured.
2. **Onset strength** = how much louder each frame is than the previous one (drum hits,
   note starts).
3. The tempo is the repetition period of the onset pattern (autocorrelation between 60 and
   200 BPM, gently preferring ~120 BPM to avoid choosing half or double the tempo).
4. Tempo (±2 BPM, 0.05 steps) and phase are fine-tuned by choosing the beat grid that lines
   up with the most onset strength.

Accuracy: tempo to ~0.1 BPM on steady music; first beat to within about one frame (~23 ms,
about one video frame at 60 fps). **Confidence** compares the chosen grid with a randomly
shifted one: near 1 = a clear steady beat, below ~0.3 = unreliable (live, rubato, ambient
music) – listen and set `--bpm` / `--beat-offset` manually.

Detection may land on beat 2, 3 or 4 instead of the true downbeat, or pick half/double
tempo for some genres. If bar markers look shifted by a beat, adjust `--beat-offset` by
one beat (`60 / bpm` seconds); if every other beat is missing, double `--bpm`.

## 3. Alignment – make a moment in the song hit a moment in the video

```bash
mcsm sync my-server.zip --replay 1 --music song.wav --music-align "1:02.5=#12"
```

means "**1:02.5 into the song** must play when **marker #12** happens". The marker number is
the `index` column of `NAME.csv` (run once without music to see the numbers). You can also
use a plain video time: `--music-align "1:02.5=3:40"`.

The tool prints where the song must start on your timeline:

```
Aligning    : song 1:02.5 -> marker #12 'Steve: [The End?]' at 3:40
Music starts: +157.500s on the video timeline
```

Put the song on your audio track at 2:37.5 (157.5 s) and the drop hits the moment. The
marker you aligned to is never moved by snapping.

## 4. Snapping – move event markers onto the beat

```bash
mcsm sync ... --snap beat            # every event marker → nearest beat
mcsm sync ... --snap bar             # → nearest bar (for slower, bigger cuts)
mcsm sync ... --snap beat --snap-tolerance 0.15   # only move markers within 150 ms
```

Snapped markers keep their original position in the CSV/JSON column
`snapped_from_seconds`, so you can see how far each moved. Subtitles (SRT) use the snapped
times too, so captions pop in on the beat.

Snapping is an editing aid: a marker moved to the beat may be up to half a beat (≈0.23 s at
128 BPM) away from the true in-game moment. Use a tolerance if that matters.

## Worked example

You rendered 5 minutes of a replay starting at 12:30, at 60 fps, want the song's drop
(0:48.2) on Steve's dragon kill, and want cuts on the bar:

```bash
mcsm sync my-server.zip --replay 1 --render-start 12:30 --length 5:00 --fps 60 \
    --types death,advancement,chat --music track.mp3 --snap bar \
    --music-align "0:48.2=#7" -o episode3/
```

Then in DaVinci Resolve:

1. Import the video; place it at the start of the timeline.
2. Import `episode3/<name>.edl` as timeline markers (event markers + bar markers).
3. Put `track.mp3` on an audio track at the printed *Music starts* position.
4. Drag `episode3/<name>.srt` onto the timeline for chat subtitles.
5. Cut on the markers.

In Audacity, import the song and then `episode3/<name>.audacity.txt` via *File → Import →
Labels…* to hear where events fall in the music (shift the label track by *Music starts*).
