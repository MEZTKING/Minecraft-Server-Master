# Working smart: from a year of recordings to finished videos

A short guide to the quickest way through, and how to keep the waiting and the disk use down. The numbers are
from the owner's library: 2,446 recordings (157 GB) and 15 backups.

## The order that wastes the least time

1. **Load the library world once.** The first load reads every backup and recording. After that the result is
   saved and opens in about a second, unless something changed.
2. **Find moments in the history viewer.** Use the moments list (celebrations, sad moments, biome finds,
   statistics), the version band and the timeline. Work at **1 min/s** or faster: that uses only the light
   overview paths, so nothing is read in full.
3. **Look closer only where it matters.** Zoom in at **1×** or **10×**, spectate a player, or fly the free
   camera. The first time a stretch is watched close up, its recordings are read in full in the background,
   usually a few seconds to a minute.
4. **Plan the shot here, film it in Flashback.**
   - In **Shot for Flashback**, add camera keyframes with **K** and check them with **Preview**.
   - **Send to Flashback** copies the best recording with your camera path and export range.
   - In Flashback, open the copy ("… (shot) …") and Export. Flashback renders the real game: every block,
     item, particle, resource pack and shader, so that's where final quality comes from.
5. **Edit and finish.** Add text and music in the video editor and join the clips. Every edit of yours is
   backed up before the app changes it.

## Keeping the waiting down

- **Prepare ahead, but only what you'll watch.** Prepare ahead shows an estimate before it starts:

  | Scope | Recordings | Block pieces | Time | Disk |
  |---|---|---|---|---|
  | One day | 23 | 98 | about 3 minutes | 120 MB |
  | One week | 75 | 303 | about 9 minutes | 330 MB |
  | Everything | 1,636 | 6,495 | about 3½ hours | 8 GB |

  Prepare the day or week you're about to work on, or one player, rather than everything.
- **Run big preparations when you're away.** The viewer stays usable meanwhile, and Cancel keeps what's done.
- **Stay zoomed out while searching.** Real blocks and full detail load only close up.
- **Turn off what you don't need.** Use **Show** to hide mobs, trails and names, and **All paths** to remove
  extra lines. Fewer things on screen means a faster frame rate; dev mode (`` ` ``) shows it.
- **Narrow the timeline.** Use **View 1 day** and **Skip quiet time** so play jumps over hours with nobody
  online.

## Keeping the disk use down

- **Full detail** takes about 2–3 % of the recordings' own size (about 4 GB for all of them). **Real blocks** take
  about 1 MB per 128 × 128 piece. Both are kept in `%LOCALAPPDATA%\mcsm\` and can be deleted at any time; they're
  rebuilt when needed.
- **Copies for Flashback** are full copies of a recording (tens to hundreds of MB). Send only the shots you'll
  use, and delete the copies after exporting. Your originals are never touched, and the app lists its copies
  apart from yours.
- **The library** uses hard links, so a backup that repeats files costs no extra space. Tidy moves duplicates
  aside instead of deleting them.

## When something looks wrong

Turn on dev mode (`` ` ``) and use **Copy view link**. The link opens the viewer at exactly that moment and
camera, which is the quickest way to get a problem looked at.
