# mcsm-link: the Flashback companion mod

A small Fabric client mod that lets the Minecraft Server Master video editor show the **real Minecraft
picture** from a running Flashback, instead of the app's own map.

- Starts a tiny HTTP server inside the game on **127.0.0.1 only** (first free port from 8796).
- Writes the port and a random **token** to `<instance>/minecraft/config/mcsm-link.json`; every request
  must carry the token (`X-MCSM-Token`), so only programs that can read your Minecraft folder can use it.
- `GET /status`: is a Flashback replay open, its id and name, the current tick, its length, the export
  range. `POST /view`: pause, go to a tick and put the camera at a position/angle/FOV/roll (the way
  Flashback's own camera keyframes do: the local spectator player). `GET /frame?width=N`: the picture the
  game draws right now, as PNG.
- Flashback has no API for other mods, so it is used through reflection with the names in its own code
  (`Flashback.getReplayServer`, `ReplayServer.goToReplayTick/getReplayTick/replayPaused`,
  `EditorStateManager.getCurrent().replayVisuals.setFov/overrideRoll`), checked against Flashback 0.40
  (Minecraft 26.1) and its current source. If Flashback renames something, requests answer with an
  error instead of crashing the game.

Build (Java 25, Gradle 9.8): `gradle build` → `build/libs/mcsm-link-<version>.jar`. CI builds it and
the installer bundles it as `mcsm/assets/mod/`; the app's *Install the Flashback link…* button (or
`mcsm flashback-link install --instance NAME`) copies it into the instance's `mods` folder.

Targets Minecraft 26.1.x with Fabric Loader 0.17+ (the owner's Flashback instance). Minecraft 26.1 isn't
obfuscated, so no mappings are needed.
