# The Windows installer

This page covers **installing** the app (for users) and **building** the installer (for
developers).

## For users: installing

1. Get `MinecraftServerMaster-Setup-<version>.exe`:
   - **From a release:** the repository's *Releases* page (the installer is attached automatically
     when a release is published, see *Releasing a version* below).
   - **From the latest build:** the repository's *Actions* tab → *Build installer* → the
     newest successful run → *Artifacts* → **MinecraftServerMaster-Setup** (a zip containing
     the `.exe`; you must be signed in to GitHub to download artifacts).
2. Run it. Windows will probably show **"Windows protected your PC"** (SmartScreen),
   because the installer isn't code-signed (signing certificates cost money). Click
   **More info → Run anyway**.
3. Follow the wizard. The choices:
   - **Install for me only** (default, no admin password) or **for all users**.
   - **Create a desktop shortcut** (ticked by default).
   - **Add the 'mcsm' command to PATH** (unticked): only needed if you want to use the
     command-line tool from a terminal (see the README).
4. Start **Minecraft Server Master** from the Start menu or desktop.

Nothing else is needed: Python is bundled inside the app.

**Uninstall:** *Settings → Apps → Minecraft Server Master → Uninstall* (or the
*Uninstall* shortcut in the Start menu). This also removes the PATH entry if you added it.
Reports and markers you saved elsewhere are left alone.

**Upgrade:** run a newer installer; it replaces the old version in place.

## Using the app

1. **Server** – click **Zip file…** (or **Folder…**) and pick your PebbleHost download. Select
   **several zips** (or use **+ Add backup…**) to merge backups into one history
   ([BACKUPS.md](BACKUPS.md)). It
   loads automatically; the grey line underneath shows how many players, events and
   replays were found, and which time zone was used. You can also drag a zip onto the app's
   desktop icon.
   - *Server time zone*: leave blank (automatic) unless the summary warns you. Examples:
     `Europe/London`, `America/New_York`, `+02:00`.
   - *latest.log date*: leave blank unless markers are off by a day (see
     [VIDEO_SYNC.md](VIDEO_SYNC.md#troubleshooting)).
2. **Statistics report** – **Create report and open it…** saves the HTML dashboard and opens
   it in your browser.
3. **Video & music sync** – choose the replay you rendered (or *Other footage* and type the
   real start time), fill in where the render starts and its frame rate, tick the events you
   want, optionally pick a song and alignment, then **Create markers**. The output folder
   opens when it's done. What each file is and how to import it: [VIDEO_SYNC.md](VIDEO_SYNC.md)
   and [MUSIC_SYNC.md](MUSIC_SYNC.md).
   - *BPM (blank = detect)* – leave blank to detect from the song file.
   - *Song time … lands on …* – e.g. `1:02.5` lands on `#12` (marker 12 in the CSV) or on
     a video time such as `3:40`.
   - **Detect BPM only** works without loading a server.
   - MP3/OGG/FLAC songs need [ffmpeg](https://ffmpeg.org) on PATH; WAV always works.
4. **Export data** – SQLite / JSON / CSV ([DATA_MODEL.md](DATA_MODEL.md)).
5. **Flashback** – pick a Flashback replay and **Copy replay with markers**, or fill in an area
   and **Create timelapse** (3:00 by default). Ticking *Install* puts the copy straight into
   Flashback. **Decode positions from all replays** reads where each recorded player went
   (used by the timelapse). Guides: [FLASHBACK.md](FLASHBACK.md), [TIMELAPSE.md](TIMELAPSE.md),
   [TRACKS.md](TRACKS.md).
6. **Live map** – type your server's address, check the RCON port/password (filled in from
   your download), and click **Start live map**. **Try demo** works without a server. Keep the
   app open while watching. Setup and security: [LIVE_MAP.md](LIVE_MAP.md).

Everything the app does is printed in the **Output** box at the bottom. Copy it from there
if you need to report a problem.

The window runs exactly the same code as the `mcsm` command line: every button builds the
same options you'd type and calls the matching command (see `mcsm/gui.py`).

## For developers: how the installer is built

```
mcsm/ (Python package)
   │  PyInstaller  (installer/mcsm.spec)
   ▼
dist/MinecraftServerMaster/
   ├─ Minecraft Server Master.exe   windowed app   (entry: installer/launch_gui.py → mcsm.gui)
   ├─ mcsm.exe                      command line   (entry: installer/launch_cli.py → mcsm.cli)
   └─ _internal/                    bundled Python, tkinter, tzdata, app icon
   │  Inno Setup   (installer/mcsm.iss)
   ▼
dist/installer/MinecraftServerMaster-Setup-<version>.exe
```

| File | Purpose |
|---|---|
| `installer/mcsm.spec` | PyInstaller recipe: two executables sharing one runtime ("onedir" mode – starts faster than a single-file exe and triggers fewer antivirus false positives). Bundles `tzdata`, because Windows Python has no time-zone database. |
| `installer/launch_gui.py`, `launch_cli.py` | Tiny entry scripts for the two executables. |
| `installer/version_info.txt` | Windows "Details" tab version resource; version numbers rewritten by the build script. |
| `installer/mcsm.iss` | Inno Setup script: per-user install by default, Start-menu/desktop shortcuts, optional PATH entry, uninstaller. **Never change `AppId`** – Windows uses it to recognise upgrades. |
| `installer/build_windows.ps1` | One command that does everything: venv → tests → PyInstaller → smoke test → Inno Setup. |
| `installer/make_icon.py` | Regenerates `installer/icon.ico` and `mcsm/assets/icon.png` (pure Python). |
| `.github/workflows/build-installer.yml` | CI: tests on Linux (Python 3.10 and 3.13); builds the installer on Windows, smoke-tests the bundle on a sample server, does a silent install → run → uninstall check, uploads the installer; publishes a Release for `v*` tags. |

### Building on your own Windows PC

1. Install Python 3.10+ from python.org (tick *Add python.exe to PATH*).
2. Install [Inno Setup 6.3+](https://jrsoftware.org/isinfo.php).
3. In PowerShell, from the repository folder:

   ```powershell
   powershell -ExecutionPolicy Bypass -File installer\build_windows.ps1
   ```

   Options: `-SkipTests`, `-SkipInstaller` (only build `dist\MinecraftServerMaster`),
   `-Python py` (choose the Python launcher).

The installer appears in `dist\installer\`.

### Releasing a version

1. Bump `__version__` in `mcsm/__init__.py` and `version` in `pyproject.toml`; note it in
   `PROJECT_STATUS.md`; push.
2. On GitHub: **Releases → Draft a new release** → *Choose a tag*: type `v0.2.0` and pick
   *Create new tag on publish* → *Target*: the branch with the code → **Publish release**.
3. The *Build installer* workflow runs for the release and attaches
   `MinecraftServerMaster-Setup-<version>.exe` to it within a few minutes (watch the Actions tab).

The permanent download link is then
https://github.com/MEZTKING/Minecraft-Server-Master/releases/latest.

### macOS and Linux

There is no packaged installer for these yet. Use the Python install from the README
(`pip install .`), which gives both the `mcsm` command and the window (`mcsm-gui`). On
Linux the window needs tkinter (`sudo apt install python3-tk`). PyInstaller can also bundle
the app on these systems (`pyinstaller installer/mcsm.spec`), but the result isn't signed
or notarised, so macOS will block it by default.

### Code signing (optional, later)

To remove the SmartScreen warning, sign `dist\MinecraftServerMaster\*.exe` and the setup
`.exe` with a code-signing certificate (`signtool sign /fd sha256 /tr <timestamp-url> /td
sha256 <file>`) after the PyInstaller step and after the Inno Setup step. Inno Setup can do
the second part via its `SignTool` directive.
