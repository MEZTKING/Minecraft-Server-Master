"""Desktop window for mcsm (``mcsm-gui``, or the installed "Minecraft Server Master" app).

Built with tkinter, which ships with Python, so the app still has no third-party
dependencies. The window is a thin front-end over the command-line code:

* "Load server" runs :func:`mcsm.analyzer.analyze` once and keeps the result.
* Every button builds the same arguments the command line would get and calls
  the matching ``run_*`` function from :mod:`mcsm.cli` with the loaded data,
  so the GUI and the CLI always behave identically.
* Work runs on a background thread; everything it prints is streamed into
  the log pane at the bottom of the window.

See docs/INSTALLER.md for how this becomes a Windows installer.
"""

from __future__ import annotations

import contextlib
import io
import os
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser
from datetime import date

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import progress as jobs
from . import __version__, cli, models as m, version_string
from . import appwindow
from .analyzer import analyze
from .source import open_server
from .sync import DEFAULT_SYNC_TYPES
from .timeutil import format_hms, parse_tz

APP_NAME = "Minecraft Server Master"
SEP = " | "  # separates several backups in the "Download" field

# Short description of every button, shown when the mouse rests on it. Keyed by the button's
# text ("…" and "..." are the same). A test checks that every button has one.
BUTTON_TIPS = {
    "Zip file…": "Choose a downloaded server backup (.zip). Select several to merge them into one history.",
    "Folder…": "Choose an unpacked server folder (or a library world folder) and load it.",
    "+ Add backup…": "Add more backups to the ones already chosen; they are merged into one history.",
    "Load server": "Read the chosen server/backups: logs, players, stats and recordings. Needed by most tabs.",
    "Join videos…": "Pick the videos (a timelapse's parts, exported clips): they are joined in order into one video, "
                    "with a transition at each cut and a song if you like (needs ffmpeg).",
    "Join…": "Choose where to save the joined video and make it.",
    "Up": "Move the selected video earlier.",
    "Down": "Move the selected video later.",
    "Cancel": "Stop the job that is running (it stops after the file it's on; nothing is left half-written).",
    "Ignored players…": "Players who were never supposed to join: leave them out everywhere (reports, moments, "
                        "story, timelapses, maps). Nothing in your backups is changed.",
    "Browse…": "Pick a folder or file for this field.",
    "Open": "Open this folder in Explorer.",
    "Import a whole folder of backups…": "Scan a folder full of backups (any host), show what was found and "
                                          "copy each backup into the library once. Your originals are only read.",
    "Import backup folder…": "Put one unpacked backup folder into the library.",
    "Import backup file (.zip / .tgz)…": "Put one downloaded backup (.zip, .tgz) into the library; it is unpacked once.",
    "Add recordings…": "Add single recordings (.zip/.mcpr) to the selected world in the library (copied).",
    "Add my Flashback replays": "Copy everything in your Flashback replay folder into the selected world.",
    "Add video projects…": "Add a folder of video projects: the recordings each video uses, plus a copy of your "
                           "Flashback edits for them.",
    "Watch the server story": "Load the selected world (every backup and recording) and open the history viewer at "
                              "the very beginning in story mode: press Play and the important moments play in order.",
    "Load this world (all its backups and recordings)": "Load the selected library world: every backup and recording "
                                                        "in it, as one history.",
    "Refresh": "Read the library folder again.",
    "Tidy the original folder…": "Optional: move duplicate copies of backups aside (never deletes) and date folder "
                                 "names. Shows the plan and asks first; can be undone.",
    "Undo tidy…": "Put back everything the last tidy moved.",
    "Create report and open it…": "Make the statistics web page (play time, deaths, chat, advancements...) and open it.",
    "Show summary": "Print a quick overview of the loaded server in the log below.",
    "Show changes": "List what changed between backups: Minecraft version, mods, plugins and datapacks.",
    "Other replay…": "Choose a recording that isn't in the loaded backups.",
    "Detect BPM only": "Find the tempo (beats per minute) and first beat of the chosen song.",
    "Create markers": "Write markers/subtitles for the chosen recording (and music beats) for your video editor.",
    "SQLite database…": "Save all data as one SQLite database file.",
    "JSON file…": "Save all data as one JSON file.",
    "CSV files (folder)…": "Save all data as spreadsheet-friendly CSV files in a folder.",
    "Copy replay with markers": "Make a copy of the Flashback replay with the server's events as markers on its "
                                "timeline (the original isn't changed).",
    "Preview plan": "Show what the timelapse would contain, without writing anything.",
    "Create timelapse": "Make a Flashback copy set up as a timelapse of the area, playing only while someone is there.",
    "Start live map": "Connect to the server (RCON) and show where everyone is on a 3D map in your browser.",
    "Try demo": "Show the live map with made-up players, no server needed.",
    "Open map": "Open the running map's page in your browser again.",
    "Stop": "Stop the running map / viewer.",
    "Open history viewer": "Open the 3D time machine: a time slider over the whole history, player paths, events "
                           "and highlights for videos.",
    "Open page": "Open the running viewer's page in your browser again.",
    "Open live editor": "Open a page in your browser where the video plays with your texts and pictures on it, "
                        "and changes (place, size, colour, rotation, animation, timing) show instantly. Make the "
                        "Flashback copy or the new video from there.",
    "Check now": "Ask GitHub whether a newer version of this app exists (nothing personal is sent).",
    "What's new": "Show the changes in the new version.",
    "Update": "Download the new version, check it's genuine (SHA-256), then close, update and reopen the app.",
    "Later": "Hide this version's notice (you'll be told about the next one).",
    "Choose…": "Pick the finished video (exported from Flashback). The app finds its edit and recording.",
    "Open a Flashback edit…": "Choose one of your Flashback edits by scene name and recording (newest first) and open "
                              "the live editor on it before exporting: the picture "
                              "is the app's 3D map seen through your edit's camera, with the players, so you can "
                              "place texts and the song first, then put them in Flashback and export once.",
    "Open": "Open the live editor on the chosen Flashback scene.",
    "Install the Flashback link…": "Copy the small companion mod into the Minecraft instance that has Flashback, so "
                                   "the editor can show the real Minecraft picture (you choose the instance and "
                                   "confirm first).",
    "Install": "Copy the companion mod into the chosen instance's mods folder.",
    "Chat & events as subtitles": "Write subtitle files with the chat, joins, deaths and advancements at the right "
                                  "moments of the video.",
}


class _Tip:
    """A small yellow box under a widget while the mouse rests on it."""

    def __init__(self, widget, text: str):
        self.widget, self.text, self.win, self.job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self.job = self.widget.after(450, self._show)

    def _cancel(self):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None

    def _show(self):
        if self.win or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.win = tk.Toplevel(self.widget)
        self.win.wm_overrideredirect(True)
        self.win.wm_geometry(f"+{x}+{y}")
        tk.Label(self.win, text=self.text, justify="left", wraplength=360, background="#ffffe0", foreground="#222",
                 relief="solid", borderwidth=1, padx=6, pady=4).pack()

    def _hide(self, _e=None):
        self._cancel()
        if self.win:
            self.win.destroy()
            self.win = None


def _tip_key(text: str) -> str:
    return text.replace("...", "…").strip()


def add_button_tips(root) -> list[str]:
    """Attach :data:`BUTTON_TIPS` to every button under *root*; returns texts that have none."""
    missing = []

    def walk(w):
        for c in w.winfo_children():
            if isinstance(c, ttk.Button):
                tip = BUTTON_TIPS.get(_tip_key(c.cget("text")))
                if tip:
                    _Tip(c, tip)
                elif c.cget("text") not in missing:
                    missing.append(c.cget("text"))
            walk(c)
    walk(root)
    return missing


EVENT_CHOICES = [  # (type, label) in display order
    (m.EV_CHAT, "Chat"), (m.EV_DEATH, "Deaths"), (m.EV_ADVANCEMENT, "Advancements"),
    (m.EV_JOIN, "Joins"), (m.EV_LEAVE, "Leaves"), (m.EV_SERVER_CHAT, "Server messages"),
    (m.EV_ME, "/me"), (m.EV_REPLAY_MARKER, "Replay markers"), (m.EV_COMMAND, "Commands"),
    (m.EV_SERVER_START, "Server start"), (m.EV_SERVER_STOP, "Server stop"), (m.EV_CRASH, "Crashes"),
]


class _QueueWriter(io.TextIOBase):
    """File-like object that forwards writes to the GUI log queue."""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, s: str) -> int:
        if s:
            self.q.put(("log", s))
            jobs.check()              # a cancelled job stops at its next printed line
        return len(s)


def open_path(path: str) -> None:
    """Open a file or folder with the operating system's default program."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def show_in_folder(path: str) -> None:
    """Open the folder holding *path* with the file selected (Explorer); a folder is opened itself."""
    path = os.path.normpath(path)
    if sys.platform.startswith("win") and os.path.isfile(path):
        subprocess.Popen(["explorer", "/select,", path])
    elif os.path.isdir(path):
        open_path(path)
    elif os.path.isdir(os.path.dirname(path)):
        open_path(os.path.dirname(path))


def _documents_dir() -> str:
    for cand in (os.path.join(os.path.expanduser("~"), "Documents"), os.path.expanduser("~")):
        if os.path.isdir(cand):
            return cand
    return os.getcwd()


class App(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=10)
        self.root = root
        self.data: m.ServerData | None = None
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.action_buttons: list[ttk.Button] = []
        self.live = None                      # running LiveMap, if any
        self.server_stack = contextlib.ExitStack()  # keeps loaded backups extracted
        self.live_stack = contextlib.ExitStack()
        self.live_url: str | None = None
        self.history = None                   # running HistoryMap, if any
        self.history_stack = contextlib.ExitStack()
        self.history_url: str | None = None
        root.title(f"{APP_NAME} {version_string()}")
        root.minsize(760, 560)
        root.geometry("900x760")
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)   # tabs get the spare height; the log stays compact
        self._build_source()
        self._build_tabs()
        self._build_log()
        self.missing_tips = add_button_tips(self)          # hover a button to see what it does
        self.after(100, self._poll)
        self.log(f"{APP_NAME} {version_string()}\n"
                 "1. Choose your downloaded server (.zip or folder) and press Load.\n"
                 "2. Use the tabs to make a statistics report, video markers, or exports.\n\n")

    # ------------------------------------------------------------------ layout
    def _build_source(self) -> None:
        box = ttk.LabelFrame(self, text="Server", padding=8)
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Download:").grid(row=0, column=0, sticky="w")
        self.server_var = tk.StringVar()
        ttk.Entry(box, textvariable=self.server_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(box, text="Zip file…", command=self._pick_zip).grid(row=0, column=2)
        ttk.Button(box, text="Folder…", command=self._pick_folder).grid(row=0, column=3, padx=(4, 0))
        ttk.Button(box, text="+ Add backup…", command=self._add_backup).grid(row=0, column=4, padx=(4, 0))

        opts = ttk.Frame(box)
        opts.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(6, 0))
        ttk.Label(opts, text="Server time zone:").pack(side="left")
        self.tz_var = tk.StringVar()
        ttk.Entry(opts, textvariable=self.tz_var, width=18).pack(side="left", padx=4)
        ttk.Label(opts, text="(blank = automatic)", foreground="gray").pack(side="left")
        ttk.Label(opts, text="   latest.log date:").pack(side="left")
        self.latest_var = tk.StringVar()
        ttk.Entry(opts, textvariable=self.latest_var, width=12).pack(side="left", padx=4)
        self.load_btn = ttk.Button(opts, text="Load server", command=self._load)
        self.load_btn.pack(side="right")
        ttk.Button(opts, text="Ignored players…", command=self._ignored_players).pack(side="right", padx=4)
        from . import updater
        self.reopen_var = tk.BooleanVar(value=updater.load_settings().get("reopen_last", True))
        ttk.Checkbutton(opts, text="Open it again next time", variable=self.reopen_var,
                        command=self._save_reopen).pack(side="right", padx=4)
        self.status_var = tk.StringVar(value="No server loaded")
        ttk.Label(box, textvariable=self.status_var, foreground="gray", wraplength=820).grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))
        self._build_updates(box)

    def _save_reopen(self) -> None:
        from . import updater
        s = updater.load_settings()
        s["reopen_last"] = bool(self.reopen_var.get())
        updater.save_settings(s)

    def _reopen_last(self) -> None:
        """At start: load the server or world loaded last time (quick when nothing changed)."""
        from . import updater
        last = updater.load_settings().get("last_loaded") or {}
        path = last.get("path") or ""
        parts = [p.strip() for p in path.split(SEP.strip()) if p.strip()]
        if not self.reopen_var.get() or not parts or not all(os.path.exists(p) for p in parts) or self.server_var.get():
            return
        self.server_var.set(path)
        self.tz_var.set(last.get("tz") or "")
        self.latest_var.set(last.get("latest") or "")
        self.log(f"Opening the last server again: {path}\n")
        self._load()

    def _ignored_players(self) -> None:
        """Players left out everywhere: people who were never supposed to join (mcsm.ignore)."""
        from . import ignore
        win = tk.Toplevel(self)
        win.title("Ignored players")
        win.transient(self)
        ttk.Label(win, text="These players are left out everywhere: reports, events, moments, the story, highlights, "
                            "timelapses and the viewers' maps. Your backups and recordings are not changed.",
                  wraplength=460).pack(anchor="w", padx=10, pady=(10, 6))
        lb = tk.Listbox(win, height=8, selectmode="extended")
        lb.pack(fill="both", expand=True, padx=10)

        def refresh():
            lb.delete(0, "end")
            for i in ignore.load():
                lb.insert("end", i["name"])
        refresh()
        row = ttk.Frame(win)
        row.pack(fill="x", padx=10, pady=6)
        known = sorted((p.name for p in self.data.players.values()), key=str.lower) if self.data else []
        name_var = tk.StringVar()
        ttk.Combobox(row, textvariable=name_var, values=known, width=24).pack(side="left")
        note = tk.StringVar()

        def add():
            n = name_var.get().strip()
            if not n:
                return
            ignore.add([n], self.data)
            name_var.set("")
            refresh()
            if self.data:
                gone = ignore.apply(self.data)
                note.set(f"Left out of the loaded server: {', '.join(gone)}. Pages that are already open "
                         "(history viewer, live editor) show it after they are started again." if gone else
                         f"{n} isn't in the loaded server (they will be left out wherever they turn up).")

        def remove():
            names = [lb.get(i) for i in lb.curselection()]
            if names:
                ignore.remove(names)
                refresh()
                note.set("Taken off the list: they come back the next time you load the server.")
        ttk.Button(row, text="Ignore", command=add).pack(side="left", padx=4)
        ttk.Button(row, text="Stop ignoring", command=remove).pack(side="left")
        ttk.Button(row, text="Close", command=win.destroy).pack(side="right")
        ttk.Label(win, textvariable=note, foreground="gray", wraplength=460).pack(anchor="w", padx=10, pady=(0, 10))

    # ------------------------------------------------------------------ updates
    UPDATE_CHANNELS = [("test", "every new test build"), ("stable", "stable releases only"), ("off", "off")]

    def _build_updates(self, box) -> None:
        """'Updates: <channel> [Check now]' and, when there is one, the new version with its buttons."""
        from . import updater
        row = ttk.Frame(box)
        row.grid(row=3, column=0, columnspan=5, sticky="ew", pady=(6, 0))
        ttk.Label(row, text="Updates:").pack(side="left")
        settings = updater.load_settings()
        self.upd_channel_var = tk.StringVar(value=dict(self.UPDATE_CHANNELS)[updater.channel(settings)])
        combo = ttk.Combobox(row, textvariable=self.upd_channel_var, width=20, state="readonly",
                             values=[label for _k, label in self.UPDATE_CHANNELS])
        combo.pack(side="left", padx=4)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._upd_set_channel())
        ttk.Button(row, text="Check now", command=lambda: self._upd_check(manual=True)).pack(side="left")
        self.upd_msg_var = tk.StringVar(value="")
        ttk.Label(row, textvariable=self.upd_msg_var, foreground="gray").pack(side="left", padx=8)
        self.upd_buttons = ttk.Frame(row)
        ttk.Button(self.upd_buttons, text="What's new", command=self._upd_notes).pack(side="left")
        ttk.Button(self.upd_buttons, text="Update", command=self._upd_install).pack(side="left", padx=4)
        ttk.Button(self.upd_buttons, text="Later", command=self._upd_later).pack(side="left")
        self.upd_available = None
        if updater.current()["installed"] and updater.check_due(settings):
            self.after(3000, lambda: self._upd_check(manual=False))

    def _upd_set_channel(self) -> None:
        from . import updater
        key = next(k for k, label in self.UPDATE_CHANNELS if label == self.upd_channel_var.get())
        s = updater.load_settings()
        s["update_channel"] = key
        updater.save_settings(s)
        if key != "off":
            self._upd_check(manual=True)
        else:
            self._upd_later()

    def _upd_check(self, manual: bool) -> None:
        """Ask GitHub in the background (no personal data); show the result in the Updates line."""
        import threading
        from . import updater
        cur = updater.current()
        if not cur["installed"]:
            if manual:
                self.upd_msg_var.set("Running from source: update with git instead.")
            return
        self.upd_msg_var.set("Checking…")

        def work():
            try:
                found = updater.check()
                err = None
            except Exception as exc:  # noqa: BLE001 -- offline etc.: just say so
                found, err = None, exc
            self.q.put(("done", lambda: self._upd_result(found, err, manual)))
        threading.Thread(target=work, daemon=True).start()

    def _upd_result(self, found, err, manual: bool) -> None:
        import time as _time
        from . import updater
        s = updater.load_settings()
        s["last_check"] = _time.time()
        updater.save_settings(s)
        if err is not None:
            self.upd_msg_var.set("Couldn't check for updates (offline?)" if manual else "")
            return
        if found and updater.is_newer(found) and str(found.get("number")) != str(s.get("skipped", "")):
            self.upd_available = found
            label = f"{found['version']}" + (f" (build {found['number']})" if found.get("number") else "")
            self.upd_msg_var.set(f"New version available: {label}")
            self.upd_buttons.pack(side="left")
        else:
            self.upd_available = None
            self.upd_buttons.pack_forget()
            self.upd_msg_var.set("You have the newest version." if manual else "")

    def _upd_notes(self) -> None:
        f = self.upd_available or {}
        messagebox.showinfo(APP_NAME, f"What's new in {f.get('version', '')}"
                            + (f" (build {f['number']})" if f.get("number") else "") + ":\n\n"
                            + (f.get("notes") or "No notes.")[:3000])

    def _upd_later(self) -> None:
        from . import updater
        if self.upd_available:
            s = updater.load_settings()
            s["skipped"] = str(self.upd_available.get("number") or self.upd_available.get("version"))
            updater.save_settings(s)
        self.upd_available = None
        self.upd_buttons.pack_forget()
        self.upd_msg_var.set("")

    def _upd_install(self) -> None:
        """Download, verify, then close the app and let the installer update and reopen it."""
        from . import updater
        f = self.upd_available
        if not f:
            return
        if self.busy or getattr(self, "history", None) or getattr(self, "live", None):
            messagebox.showinfo(APP_NAME, "Finish or stop what's running first (imports, exports, the history "
                                          "viewer or live map); then press Update again.")
            return
        if not messagebox.askyesno(APP_NAME, f"Download and install {f['version']}"
                                   + (f" (build {f['number']})" if f.get("number") else "")
                                   + "?\n\nThe app closes, updates and opens again. Your library, highlights "
                                     "and settings are kept."):
            return

        def work(_):
            print("Downloading the update…")
            path = updater.download(f, progress=lambda pct: print(f"  {pct}%"))
            print("Downloaded and checked (SHA-256 matches). Installing…")
            return path

        def after(path):
            updater.run_installer(path)
            self.root.after(500, self.root.event_generate, "<<mcsm-quit>>")

        self._run("Updating", work, after)

    def _build_tabs(self) -> None:
        nb = ttk.Notebook(self)
        nb.grid(row=1, column=0, sticky="nsew", pady=8)
        self.nb = nb
        nb.add(self._tab_library(nb), text="Library")
        nb.add(self._tab_report(nb), text="Statistics report")
        nb.add(self._tab_sync(nb), text="Video & music sync")
        nb.add(self._tab_export(nb), text="Export data")
        nb.add(self._tab_flashback(nb), text="Flashback")
        nb.add(self._tab_live(nb), text="Live map")
        nb.add(self._tab_history(nb), text="History")
        nb.add(self._tab_videos(nb), text="Videos")

    def _button(self, parent, text, command, **grid) -> ttk.Button:
        b = ttk.Button(parent, text=text, command=command, state="disabled")
        self.action_buttons.append(b)
        if grid:
            b.grid(**grid)
        return b

    def _tab_library(self, nb) -> ttk.Frame:
        from . import library as lib
        f = ttk.Frame(nb, padding=12)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(4, weight=1)
        ttk.Label(f, text="Keep all your backups and recordings in one place: one folder per world, each backup "
                          "in a dated folder, every recording stored once. Files that didn't change between "
                          "backups are stored once too. Then load a whole world with one click. "
                          "See docs/LIBRARY.md.", wraplength=660, justify="left").grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="Library folder:").grid(row=1, column=0, sticky="w", pady=(8, 3))
        self.lib_dir_var = tk.StringVar(value=lib.default_library_dir())
        self.lib_dir_var.trace_add("write", lambda *_: lib.is_library(self.lib_dir_var.get())
                                   and lib.remember_library(self.lib_dir_var.get()))
        ttk.Entry(f, textvariable=self.lib_dir_var).grid(row=1, column=1, sticky="ew", padx=4, pady=(8, 3))
        lb = ttk.Frame(f)
        lb.grid(row=1, column=2, sticky="w", pady=(8, 3))
        ttk.Button(lb, text="Browse…", command=lambda: (self._pick_dir(self.lib_dir_var), self._lib_refresh())).pack(side="left")
        ttk.Button(lb, text="Open", command=lambda: os.path.isdir(self.lib_dir_var.get()) and open_path(self.lib_dir_var.get())).pack(side="left", padx=(4, 0))
        modes = ttk.Frame(f)
        modes.grid(row=2, column=0, columnspan=3, sticky="w", pady=3)
        ttk.Label(modes, text="Adding files:").pack(side="left")
        self.lib_mode_var = tk.StringVar(value="auto")
        for value, text in (("auto", "Link (no extra space; copy if on another drive)"), ("copy", "Copy"),
                            ("move", "Move (originals go into the library)")):
            ttk.Radiobutton(modes, text=text, value=value, variable=self.lib_mode_var).pack(side="left", padx=(6, 0))
        btns = ttk.Frame(f)
        btns.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 6))
        ttk.Button(btns, text="Import a whole folder of backups…", command=self._lib_import_all).pack(side="left", padx=(0, 4))
        ttk.Button(btns, text="Import backup folder…", command=lambda: self._lib_import(folder=True)).pack(side="left")
        ttk.Button(btns, text="Import backup file (.zip / .tgz)…", command=lambda: self._lib_import(folder=False)).pack(side="left", padx=4)
        ttk.Button(btns, text="Add recordings…", command=self._lib_add_recordings).pack(side="left")
        ttk.Button(btns, text="Add my Flashback replays", command=lambda: self._lib_add_recordings(flashback=True)).pack(side="left", padx=4)
        ttk.Button(btns, text="Add video projects…", command=self._lib_add_projects).pack(side="left")
        cols = ("version", "files", "size", "source")
        tree = ttk.Treeview(f, columns=cols, height=8)
        tree.heading("#0", text="World / backup")
        for c, w, t in (("version", 80, "Version"), ("files", 80, "Files"), ("size", 80, "Size"), ("source", 260, "Imported from")):
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor="w")
        tree.column("#0", width=220)
        tree.grid(row=4, column=0, columnspan=3, sticky="nsew")
        tree.bind("<Double-1>", lambda _e: self._lib_load())
        self.lib_tree = tree
        bottom = ttk.Frame(f)
        bottom.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(bottom, text="Load this world (all its backups and recordings)", command=self._lib_load).pack(side="left")
        ttk.Button(bottom, text="Watch the server story", command=self._lib_story).pack(side="left", padx=4)
        ttk.Button(bottom, text="Refresh", command=self._lib_refresh).pack(side="left", padx=4)
        ttk.Button(bottom, text="Tidy the original folder…", command=self._lib_tidy).pack(side="left", padx=(16, 0))
        ttk.Button(bottom, text="Undo tidy…", command=self._lib_tidy_undo).pack(side="left", padx=4)
        self.after(50, self._lib_refresh)               # after the window is up
        return f

    def _lib_selected_world(self) -> str | None:
        from . import library as lib
        sel = self.lib_tree.selection()
        if sel:
            item = sel[0]
            parent = self.lib_tree.parent(item)
            return None if item == "__empty" else (parent or item)
        worlds = lib.list_worlds(self.lib_dir_var.get())
        return worlds[0]["path"] if len(worlds) == 1 else None

    def _lib_refresh(self) -> None:
        """Read the library in the background (with the loading bar) and fill the list."""
        from . import library as lib
        folder = self.lib_dir_var.get()
        self.lib_loading = True
        tree = self.lib_tree
        tree.delete(*tree.get_children())
        tree.insert("", "end", iid="__loading", text="(reading your library…)")

        def work(_d):
            try:
                return lib.list_worlds(folder)
            except OSError:
                return []
        self._run("Reading your library", work, self._lib_fill, quiet=True, exclusive=False,
                  cancelled=lambda: self._lib_fill(None))

    def _lib_fill(self, worlds) -> None:
        from . import library as lib
        self.lib_loading = False
        tree = self.lib_tree
        tree.delete(*tree.get_children())
        if worlds is None:
            tree.insert("", "end", iid="__cancelled", text="(not read: press Refresh)")
            return
        for w in worlds:
            bs = w["backups"]
            tree.insert("", "end", iid=w["path"], open=True,
                        text=f"{w['name']}  ({len(bs)} backups, {w['recordings']} recordings)",
                        values=(bs[-1].get("version") or "" if bs else "", "", "", ""))
            for b in bs:
                tree.insert(w["path"], "end", iid=b["path"], text=b["name"],
                            values=(b.get("version") or "?", f"{b.get('files', 0):,}", lib.human_bytes(b.get("bytes")),
                                    b.get("source", "")))
        if not worlds:
            tree.insert("", "end", iid="__empty", text="(empty: import a backup)")

    def _lib_import_all(self) -> None:
        """Scan a folder full of backups, show the plan, and import it after a yes."""
        from . import library as lib
        top = filedialog.askdirectory(title="Choose the folder that holds your backups (any host)")
        if not top:
            return
        library = self.lib_dir_var.get()
        mode = "copy"      # a whole folder is always copied: the originals are only read

        def scan(_):
            plan = lib.scan(top, library)
            print(lib.describe_plan(plan))
            return plan

        def confirm(plan):
            if not plan["backups"] and not plan["recordings"]:
                messagebox.showinfo(APP_NAME, "No backups or recordings found in that folder.")
                return
            words = "copied (the originals are only read, never changed)"
            if not messagebox.askyesno(APP_NAME, f"Import {len(plan['backups'])} backups and "
                                                 f"{sum(r['count'] for r in plan['recordings'])} loose recordings "
                                                 f"into {library}?\n\n{len(plan['same'])} duplicate copies are skipped. "
                                                 f"Files are {words}. The full plan is in the log below. "
                                                 "Big backups can take a long time."):
                return

            def work(_):
                r = lib.import_all(plan, library, mode, None, print)
                print(f"Done: {len(r['imported'])} imported, {len(r['skipped'])} already there, "
                      f"{len(r['failed'])} failed")

            self._run("Importing the folder", work, lambda _r: self._lib_refresh())

        self._run("Scanning for backups", scan, confirm)

    def _lib_tidy(self) -> None:
        """Optional: move duplicate copies aside (and date folder names) in the ORIGINAL folder."""
        from . import library as lib
        top = filedialog.askdirectory(title="Choose the folder that holds your original backups")
        if not top:
            return
        rename = messagebox.askyesno(APP_NAME, "Also put the date in front of backup folder names "
                                               "(e.g. '2026-06-09 June09_2026_PostEXP2') so they sort by date?",
                                     default="no")

        library = self.lib_dir_var.get()

        def scan(_):
            plan = lib.scan(top, library)
            moves = lib.tidy_plan(plan, rename=rename)
            print(lib.describe_tidy(plan, moves))
            return plan, moves

        def confirm(result):
            plan, moves = result
            if not moves:
                messagebox.showinfo(APP_NAME, "Nothing to tidy: no duplicate copies found.")
                return
            if not messagebox.askyesno(
                    APP_NAME, f"WARNING: this changes your ORIGINAL folder.\n\n{len(moves)} items will be MOVED "
                              f"(not deleted): duplicate copies go into '{lib.DUPLICATES_DIR}'"
                              f"{', and folders get their date in front' if rename else ''}. The list is in the "
                              "log below.\n\nImport into the library first. You can put everything back with "
                              "'Undo tidy…'.\n\nMove them now?", icon="warning", default="no"):
                return
            self._run("Tidying the original folder",
                      lambda _d: print(f"{lib.tidy_apply(plan, moves, print)['moved']} moved; undo with 'Undo tidy…'"))

        self._run("Planning the tidy", scan, confirm)

    def _lib_tidy_undo(self) -> None:
        from . import library as lib
        top = filedialog.askdirectory(title="Choose the folder you tidied")
        if not top:
            return
        if not messagebox.askyesno(APP_NAME, "Put back everything the last tidy moved in this folder?"):
            return
        self._run("Undoing the tidy", lambda _d: print(f"{lib.tidy_undo(top, print)['put_back']} items put back"))

    def _lib_import(self, folder: bool) -> None:
        from . import library as lib
        if folder:
            p = filedialog.askdirectory(title="Choose a backup folder to import")
            paths = [p] if p else []
        else:
            paths = list(filedialog.askopenfilenames(
                title="Choose backup file(s) to import",
                filetypes=[("Backups", "*.zip *.tgz *.tar.gz *.tar"), ("All files", "*.*")]))
        if not paths:
            return
        library, mode = self.lib_dir_var.get(), self.lib_mode_var.get()
        if mode == "move" and not messagebox.askyesno(
                APP_NAME, "Move the backup's files into the library? They will no longer be in their "
                          "current folder.", default="no"):
            return

        def work(_):
            for p in paths:
                r = lib.import_backup(p, library, mode if folder else "auto", None, print)
                if not r.get("skipped"):
                    print(f"Imported into {r['backup']}: {r['files']:,} files, {lib.human_bytes(r['bytes'])}; "
                          f"{r['deduplicated']:,} shared with other backups; recordings: {r['recordings_new']} "
                          f"new, {r['recordings_known']} already there")

        self._run("Importing into the library", work, lambda _r: self._lib_refresh())

    def _lib_add_recordings(self, flashback: bool = False) -> None:
        from . import library as lib
        w = self._lib_selected_world()
        if not w:
            messagebox.showinfo(APP_NAME, "Select the world the recordings belong to (import a backup first).")
            return
        if flashback:
            from .flashback import find_flashback_dir
            fb = find_flashback_dir(None)
            paths = [os.path.join(fb, "replays")] if fb and os.path.isdir(os.path.join(fb, "replays")) else []
            if not paths:
                messagebox.showinfo(APP_NAME, "Flashback's replay folder wasn't found; use Add recordings… instead.")
                return
        else:
            paths = list(filedialog.askopenfilenames(
                title="Choose recordings (.zip / .mcpr)", filetypes=[("Recordings", "*.zip *.mcpr"), ("All files", "*.*")]))
        if not paths:
            return
        # Recordings are copied unless Move is chosen (and confirmed): they often live in a game
        # folder (Flashback's replays) that shouldn't share data with the library.
        mode = "move" if self.lib_mode_var.get() == "move" and not flashback else "copy"
        if mode == "move" and not messagebox.askyesno(
                APP_NAME, "Move these recordings into the library? They will no longer be in their "
                          "current folder.", default="no"):
            return

        def work(_):
            r = lib.add_recordings(paths, w, mode, print)
            print(f"{r['added']} recordings added, {r['known']} already in the library")

        self._run("Adding recordings", work, lambda _r: self._lib_refresh())

    def _lib_add_projects(self) -> None:
        """A folder whose subfolders are video projects: their recordings + a copy of the Flashback edits."""
        from . import library as lib
        w = self._lib_selected_world()
        if not w:
            messagebox.showinfo(APP_NAME, "Select the world the projects belong to (import a backup first).")
            return
        folder = filedialog.askdirectory(title="Choose the folder whose subfolders are your video projects")
        if not folder:
            return

        def work(_):
            r = lib.add_projects(folder, w, print)
            print(f"{len(r['projects'])} projects, {r['editor_states']} Flashback edits copied (your launcher "
                  f"files are not changed), {r['recordings_added']} recordings new to the library")

        self._run("Adding video projects", work, lambda _r: self._lib_refresh())

    def _lib_load(self) -> None:
        w = self._lib_selected_world()
        if not w or w == "__empty":
            messagebox.showinfo(APP_NAME, "Import a backup first, then select its world.")
            return
        self.server_var.set(w)
        self._load()

    def _lib_story(self) -> None:
        """Load the selected library world (if it isn't loaded yet) and open its story in the history viewer."""
        if self.busy:
            messagebox.showinfo(APP_NAME, "Wait for the current task to finish, then try again.")
            return
        w = self._lib_selected_world()
        if not w or w == "__empty":
            messagebox.showinfo(APP_NAME, "Import a backup first, then select its world.")
            return
        if self.data is not None and os.path.normcase(str(self.data.library_world or "")) == os.path.normcase(w):
            self._history_start(story=True)
            return
        self.server_var.set(w)
        self.log("Loading the whole world for its story (every backup and recording; about a minute)…\n")

        def then():
            if self.history is not None:          # a viewer for the previously loaded server: replace it
                self._history_stop()
            self._history_start(story=True)
        self._load(then=then)

    def _tab_report(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb, padding=12)
        ttk.Label(f, text="A single web page with everything that happened on the server: play time, "
                          "activity charts, deaths, chat, advancements, inventories and every log event.",
                  wraplength=640, justify="left").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="Title (optional):").grid(row=1, column=0, sticky="w", pady=10)
        self.title_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.title_var, width=40).grid(row=1, column=1, sticky="w", padx=4)
        self._button(f, "Create report and open it…", self._report, row=2, column=0, columnspan=2, sticky="w")
        ttk.Separator(f).grid(row=3, column=0, columnspan=3, sticky="ew", pady=14)
        ttk.Label(f, text="Quick summary of the loaded server:").grid(row=4, column=0, sticky="w")
        self._button(f, "Show summary", lambda: self._run("Summary", lambda d: cli.run_summary(d)),
                     row=4, column=1, sticky="w", padx=4)
        ttk.Label(f, text="Minecraft version, mods and datapacks changed between backups:").grid(row=5, column=0, sticky="w", pady=(8, 0))
        self._button(f, "Show changes", lambda: self._run("Changes between backups", lambda d: cli.run_changes(d)),
                     row=5, column=1, sticky="w", padx=4, pady=(8, 0))
        return f

    def _scrollable(self, nb) -> tuple[ttk.Frame, ttk.Frame]:
        """A tab whose content scrolls vertically when the window is too short."""
        holder = ttk.Frame(nb)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        canvas = tk.Canvas(holder, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, padding=(12, 8))
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.grid(row=0, column=0, sticky="nsew")

        def on_inner(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            need = inner.winfo_reqheight() > canvas.winfo_height()
            if need:
                bar.grid(row=0, column=1, sticky="ns")
            else:
                bar.grid_remove()
                canvas.yview_moveto(0)

        def on_canvas(e):
            canvas.itemconfigure(win, width=e.width)
            on_inner()

        inner.bind("<Configure>", on_inner)
        canvas.bind("<Configure>", on_canvas)

        def wheel(e):
            if inner.winfo_reqheight() <= canvas.winfo_height():
                return
            step = -1 if (getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0) else 1
            canvas.yview_scroll(step * (1 if sys.platform == "darwin" else 3), "units")

        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            holder.bind_all(seq, lambda e: wheel(e) if str(e.widget).startswith(str(holder)) else None, add="+")
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            canvas.configure(background=bg)
        return holder, inner

    def _tab_sync(self, nb) -> ttk.Frame:
        holder, outer = self._scrollable(nb)
        outer.columnconfigure(0, weight=1)

        # -- which video
        vid = ttk.LabelFrame(outer, text="Which video", padding=8)
        vid.grid(row=0, column=0, sticky="ew")
        vid.columnconfigure(1, weight=1)
        self.mode_var = tk.StringVar(value="replay")
        ttk.Radiobutton(vid, text="Rendered from replay:", variable=self.mode_var, value="replay").grid(row=0, column=0, sticky="w")
        self.replay_combo = ttk.Combobox(vid, state="readonly")
        self.replay_combo.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(vid, text="Other replay…", command=self._pick_replay).grid(row=0, column=2)
        ttk.Radiobutton(vid, text="Other footage, starting at:", variable=self.mode_var, value="manual").grid(row=1, column=0, sticky="w")
        self.video_start_var = tk.StringVar()
        ttk.Entry(vid, textvariable=self.video_start_var).grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Label(vid, text="YYYY-MM-DD HH:MM:SS", foreground="gray").grid(row=1, column=2, sticky="w")

        row = ttk.Frame(vid)
        row.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.render_start_var, self.length_var = tk.StringVar(value="0:00"), tk.StringVar()
        self.speed_var, self.fps_var = tk.StringVar(value="1"), tk.StringVar(value="60")
        for label, var, w in (("Render starts at", self.render_start_var, 8), ("Length", self.length_var, 8),
                              ("Speed ×", self.speed_var, 5), ("FPS", self.fps_var, 6)):
            ttk.Label(row, text=label).pack(side="left", padx=(0, 4))
            ttk.Entry(row, textvariable=var, width=w).pack(side="left", padx=(0, 14))
        ttk.Label(vid, text="Times like 12:30 or 1:02:03. Leave Length empty to use the rest of the replay.",
                  foreground="gray").grid(row=3, column=0, columnspan=3, sticky="w")

        # -- events
        ev = ttk.LabelFrame(outer, text="Events to mark", padding=8)
        ev.grid(row=1, column=0, sticky="ew", pady=6)
        self.type_vars = {}
        for i, (t, label) in enumerate(EVENT_CHOICES):
            var = tk.BooleanVar(value=t in DEFAULT_SYNC_TYPES)
            self.type_vars[t] = var
            ttk.Checkbutton(ev, text=label, variable=var).grid(row=i // 6, column=i % 6, sticky="w", padx=(0, 10))
        ttk.Label(ev, text="Only players (comma separated, blank = all):").grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.players_var = tk.StringVar()
        ttk.Entry(ev, textvariable=self.players_var).grid(row=2, column=3, columnspan=3, sticky="ew", pady=(6, 0))

        # -- music
        mu = ttk.LabelFrame(outer, text="Music (optional)", padding=8)
        mu.grid(row=2, column=0, sticky="ew")
        mu.columnconfigure(1, weight=1)
        ttk.Label(mu, text="Song file:").grid(row=0, column=0, sticky="w")
        self.music_var = tk.StringVar()
        ttk.Entry(mu, textvariable=self.music_var).grid(row=0, column=1, columnspan=3, sticky="ew", padx=4)
        ttk.Button(mu, text="Browse…", command=self._pick_music).grid(row=0, column=4)
        r1 = ttk.Frame(mu)
        r1.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))
        self.bpm_var, self.offset_var = tk.StringVar(), tk.StringVar()
        self.align_song_var, self.align_target_var = tk.StringVar(), tk.StringVar()
        for label, var, w in (("BPM (blank = detect)", self.bpm_var, 7), ("First beat at", self.offset_var, 7),
                              ("Song time", self.align_song_var, 7), ("lands on (#marker or video time)", self.align_target_var, 7)):
            ttk.Label(r1, text=label).pack(side="left", padx=(0, 4))
            ttk.Entry(r1, textvariable=var, width=w).pack(side="left", padx=(0, 12))
        r2 = ttk.Frame(mu)
        r2.grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Label(r2, text="Snap event markers to:").pack(side="left")
        self.snap_var = tk.StringVar(value="none")
        ttk.Combobox(r2, textvariable=self.snap_var, values=["none", "beat", "bar"], width=6, state="readonly").pack(side="left", padx=(4, 16))
        ttk.Label(r2, text="Beat markers:").pack(side="left")
        self.beatmk_var = tk.StringVar(value="bar")
        ttk.Combobox(r2, textvariable=self.beatmk_var, values=["bar", "beat", "none"], width=6, state="readonly").pack(side="left", padx=4)
        self._button(r2, "Detect BPM only", self._detect_bpm).pack(side="left", padx=(16, 0))
        self.action_buttons[-1].configure(state="normal")  # works without a loaded server

        # -- output
        out = ttk.Frame(outer)
        out.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Save to folder:").grid(row=0, column=0, sticky="w")
        self.outdir_var = tk.StringVar(value=os.path.join(_documents_dir(), "mcsm-markers"))
        ttk.Entry(out, textvariable=self.outdir_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(out, text="Browse…", command=lambda: self._pick_dir(self.outdir_var)).grid(row=0, column=2)
        self._button(out, "Create markers", self._sync, row=0, column=3, padx=(8, 0))
        return holder

    def _tab_flashback(self, nb) -> ttk.Frame:
        from .flashback import find_flashback_dir
        holder, f = self._scrollable(nb)
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Work in Flashback's own editor. The app makes a COPY of a Flashback replay (your original is "
                          "never changed) with server events as markers, or with a ready-made fixed-camera timelapse "
                          "of an area. Details: docs/FLASHBACK.md and docs/TIMELAPSE.md.",
                  wraplength=660, justify="left").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="Flashback replay:").grid(row=1, column=0, sticky="w", pady=(8, 2))
        self.fb_combo = ttk.Combobox(f, state="readonly")
        self.fb_combo.grid(row=1, column=1, columnspan=2, sticky="ew", padx=4, pady=(8, 2))
        ttk.Label(f, text="Flashback folder:").grid(row=2, column=0, sticky="w")
        self.fb_dir_var = tk.StringVar(value=find_flashback_dir() or "")
        ttk.Entry(f, textvariable=self.fb_dir_var).grid(row=2, column=1, sticky="ew", padx=4)
        ttk.Button(f, text="Browse…", command=lambda: self._pick_dir(self.fb_dir_var)).grid(row=2, column=2)
        ttk.Label(f, text="<.minecraft>/flashback – for Prism/Modrinth/CurseForge pick the instance's .minecraft/flashback",
                  foreground="gray").grid(row=3, column=1, columnspan=2, sticky="w")
        self.fb_install_var = tk.BooleanVar(value=bool(self.fb_dir_var.get()))
        ttk.Checkbutton(f, text="Install the copy into Flashback (otherwise saved to Documents/mcsm-flashback)",
                        variable=self.fb_install_var).grid(row=4, column=0, columnspan=3, sticky="w", pady=(2, 6))

        mk = ttk.LabelFrame(f, text="1. Markers", padding=8)
        mk.grid(row=5, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Label(mk, text="Uses the event types and players ticked on the 'Video & music sync' tab.",
                  foreground="gray").pack(side="left")
        self._button(mk, "Copy replay with markers", self._fb_markers).pack(side="right")

        tl = ttk.LabelFrame(f, text="2. Timelapse of an area (plays only while someone is there)", padding=8)
        tl.grid(row=6, column=0, columnspan=3, sticky="ew", pady=4)
        tl.columnconfigure(1, weight=1)
        self.tl_name_var, self.tl_area_var = tk.StringVar(value="Base"), tk.StringVar()
        self.tl_dim_var = tk.StringVar(value="minecraft:overworld")
        self.tl_tracks_var = tk.StringVar(value=os.path.join(_documents_dir(), "mcsm-live-tracks"))
        self.tl_mode_var, self.tl_length_var, self.tl_speed_var = tk.StringVar(value="length"), tk.StringVar(value="3:00"), tk.StringVar(value="60")
        self.tl_camera_var = tk.StringVar()
        r = 0
        for label, var, hint in (("Area name", self.tl_name_var, ""),
                                 ("Area box", self.tl_area_var, "x1,z1,x2,z2  (optional ,y1,y2) – F3 coordinates of two opposite corners")):
            ttk.Label(tl, text=label + ":").grid(row=r, column=0, sticky="w", pady=2)
            ttk.Entry(tl, textvariable=var).grid(row=r, column=1, sticky="ew", padx=4)
            ttk.Label(tl, text=hint, foreground="gray").grid(row=r, column=2, sticky="w")
            r += 1
        ttk.Label(tl, text="Dimension:").grid(row=r, column=0, sticky="w", pady=2)
        ttk.Combobox(tl, textvariable=self.tl_dim_var, state="readonly", width=22,
                     values=["minecraft:overworld", "minecraft:the_nether", "minecraft:the_end"]).grid(row=r, column=1, sticky="w", padx=4)
        r += 1
        ttk.Label(tl, text="Position recordings:").grid(row=r, column=0, sticky="w", pady=2)
        ttk.Entry(tl, textvariable=self.tl_tracks_var).grid(row=r, column=1, sticky="ew", padx=4)
        ttk.Button(tl, text="Browse…", command=lambda: self._pick_dir(self.tl_tracks_var)).grid(row=r, column=2, sticky="w")
        r += 1
        ttk.Label(tl, text="Made by Live map → 'Save positions'. Empty/missing: uses log sessions (anyone online, coarse).",
                  foreground="gray", wraplength=520).grid(row=r, column=1, columnspan=2, sticky="w")
        r += 1
        lenrow = ttk.Frame(tl)
        lenrow.grid(row=r, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Radiobutton(lenrow, text="Fit into", variable=self.tl_mode_var, value="length").pack(side="left")
        ttk.Entry(lenrow, textvariable=self.tl_length_var, width=7).pack(side="left", padx=(4, 16))
        ttk.Radiobutton(lenrow, text="Fixed speed ×", variable=self.tl_mode_var, value="speed").pack(side="left")
        ttk.Entry(lenrow, textvariable=self.tl_speed_var, width=6).pack(side="left", padx=4)
        r += 1
        ttk.Label(tl, text="Camera (optional):").grid(row=r, column=0, sticky="w", pady=2)
        ttk.Entry(tl, textvariable=self.tl_camera_var).grid(row=r, column=1, sticky="ew", padx=4)
        ttk.Label(tl, text="x,y,z,yaw,pitch – blank = automatic", foreground="gray").grid(row=r, column=2, sticky="w")
        r += 1
        brow = ttk.Frame(tl)
        brow.grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._button(brow, "Preview plan", lambda: self._fb_timelapse(dry=True)).pack(side="left")
        self._button(brow, "Create timelapse", lambda: self._fb_timelapse(dry=False)).pack(side="left", padx=6)
        return holder

    def _fb_common(self, cmd: str) -> list[str] | None:
        idx = self.fb_combo.current()
        reps = getattr(self, "_fb_replays", [])
        if idx < 0 or idx >= len(reps):
            messagebox.showinfo(APP_NAME, "Load a server whose download contains Flashback replays, then pick one.")
            return None
        argv = [cmd, *self._common_argv(), "--replay", str(self.data.replays.index(reps[idx]) + 1)]
        fb_dir = self.fb_dir_var.get().strip()
        if fb_dir:
            argv += ["--flashback-dir", fb_dir]
        if self.fb_install_var.get():
            if not fb_dir:
                messagebox.showinfo(APP_NAME, "Choose the Flashback folder to install into, or untick 'Install'.")
                return None
            argv.append("--install")
        else:
            out_dir = os.path.join(_documents_dir(), "mcsm-flashback")
            os.makedirs(out_dir, exist_ok=True)
            suffix = " (mcsm)" if cmd == "flashback" else " (timelapse)"
            stem = os.path.splitext(os.path.basename(reps[idx].path.rstrip("/")))[0]
            out = os.path.join(out_dir, cli._safe_name(stem + suffix) + ".zip")
            n = 2
            while os.path.exists(out):
                out = os.path.join(out_dir, cli._safe_name(f"{stem}{suffix} {n}") + ".zip")
                n += 1
            argv += ["-o", out]
        return argv

    def _parse(self, argv: list[str]):
        """Parse like the command line; on a bad value show argparse's own message."""
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                return cli.parse_args(argv)
        except SystemExit:
            msg = buf.getvalue().strip().splitlines()
            messagebox.showerror(APP_NAME, (msg[-1] if msg else "Invalid value").split("error: ")[-1])
            return None

    def _fb_markers(self):
        argv = self._fb_common("flashback")
        if argv is None:
            return
        types = [t for t, v in self.type_vars.items() if v.get()]
        if types:
            argv += ["--types", ",".join(types)]
        if self.players_var.get().strip():
            argv += ["--players", self.players_var.get().strip()]
        args = self._parse(argv)
        if args is None:
            return
        self._run("Flashback markers", lambda d: cli.run_flashback(d, args))

    def _fb_timelapse(self, dry: bool):
        argv = self._fb_common("timelapse")
        if argv is None:
            return
        if not self.tl_area_var.get().strip():
            messagebox.showinfo(APP_NAME, "Type the area box: x1,z1,x2,z2 (two opposite corners, from F3).")
            return
        argv += ["--area", self.tl_area_var.get().strip(), "--area-name", self.tl_name_var.get().strip() or "Base",
                 "--dimension", self.tl_dim_var.get(),
                 "--extras-dir", os.path.join(_documents_dir(), "mcsm-flashback")]
        tracks = self.tl_tracks_var.get().strip()
        if tracks and os.path.exists(tracks):
            argv += ["--tracks", tracks]
        if self.tl_mode_var.get() == "speed":
            argv += ["--speed", self.tl_speed_var.get().strip() or "60"]
        else:
            argv += ["--length", self.tl_length_var.get().strip() or "3:00"]
        if self.tl_camera_var.get().strip():
            argv += ["--camera", self.tl_camera_var.get().strip()]
        if dry:
            argv.append("--dry-run")
        args = self._parse(argv)
        if args is None:
            return
        self._run("Timelapse plan" if dry else "Flashback timelapse", lambda d: cli.run_timelapse(d, args))

    def _tab_live(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb, padding=12)
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="See where everyone is right now on a 3D map in your browser. Player positions come "
                          "live from the server over RCON; the landscape comes from the world in your download. "
                          "Setup: docs/LIVE_MAP.md.", wraplength=660, justify="left").grid(row=0, column=0, columnspan=4, sticky="w")
        self.live_host_var, self.live_port_var = tk.StringVar(), tk.StringVar(value="25575")
        self.live_pw_var, self.live_interval_var = tk.StringVar(), tk.StringVar(value="1")
        rows = (("Server address", self.live_host_var, "e.g. 123.45.67.89 or play.myserver.net (no port)"),
                ("RCON port", self.live_port_var, "rcon.port in server.properties"),
                ("RCON password", self.live_pw_var, "rcon.password (filled in from your download if present)"),
                ("Update every", self.live_interval_var, "seconds"))
        for i, (label, var, hint) in enumerate(rows, start=1):
            ttk.Label(f, text=label + ":").grid(row=i, column=0, sticky="w", pady=3)
            ttk.Entry(f, textvariable=var, show="•" if "password" in label else "").grid(row=i, column=1, sticky="ew", padx=4)
            ttk.Label(f, text=hint, foreground="gray").grid(row=i, column=2, sticky="w")
        self.live_record_var = tk.BooleanVar(value=False)
        self.live_record_dir = tk.StringVar(value=os.path.join(_documents_dir(), "mcsm-live-tracks"))
        ttk.Checkbutton(f, text="Save positions to:", variable=self.live_record_var).grid(row=5, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self.live_record_dir).grid(row=5, column=1, sticky="ew", padx=4)
        ttk.Button(f, text="Browse…", command=lambda: self._pick_dir(self.live_record_dir)).grid(row=5, column=2, sticky="w")
        btns = ttk.Frame(f)
        btns.grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.live_start_btn = ttk.Button(btns, text="Start live map", command=lambda: self._live_start(demo=False))
        self.live_start_btn.pack(side="left")
        self.live_demo_btn = ttk.Button(btns, text="Try demo", command=lambda: self._live_start(demo=True))
        self.live_demo_btn.pack(side="left", padx=6)
        self.live_open_btn = ttk.Button(btns, text="Open map", command=lambda: self.live_url and appwindow.open_page(self.live_url),
                                        state="disabled")
        self.live_open_btn.pack(side="left")
        self.live_stop_btn = ttk.Button(btns, text="Stop", command=self._live_stop, state="disabled")
        self.live_stop_btn.pack(side="left", padx=6)
        self.live_status_var = tk.StringVar(value="Not running.")
        ttk.Label(f, textvariable=self.live_status_var, foreground="gray", wraplength=660).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(f, text="RCON is unencrypted and gives full control of the server: use a long random password "
                          "and don't share it.", foreground="gray", wraplength=660).grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))
        return f

    def _live_start(self, demo: bool) -> None:
        if self.live is not None or self.busy:
            return
        argv = ["live", "--no-browser", "--interval", self.live_interval_var.get().strip() or "1"]
        if demo:
            argv.append("--demo")
        else:
            host = self.live_host_var.get().strip()
            if not host:
                messagebox.showinfo(APP_NAME, "Type your server's address (the IP or hostname you join with, without the port).")
                return
            if ":" in host and host.count(":") == 1:  # "1.2.3.4:25565" -> drop the game port
                host = host.split(":")[0]
            argv += ["--host", host]
            if self.live_port_var.get().strip():
                argv += ["--rcon-port", self.live_port_var.get().strip()]
        if self.live_record_var.get():
            argv += ["--record", self.live_record_dir.get().strip()]
        try:
            args = cli.parse_args(argv)
        except SystemExit:
            messagebox.showerror(APP_NAME, "Check the RCON port and update interval are numbers.")
            return
        # Terrain comes from the newest (last) backup in the list.
        server_path = [p for p in self.server_var.get().split(SEP.strip()) if p.strip()][-1:] or [""]
        server_path = server_path[0].strip()
        password = self.live_pw_var.get()
        stack = contextlib.ExitStack()

        def work(_):
            try:
                root = stack.enter_context(open_server(server_path, include_regions=True)) if server_path else None
                if not demo and not password and not cli.live_settings(root).get("password"):
                    raise ValueError("Type the RCON password (rcon.password in server.properties).")
                print("Connecting…" if not demo else "Starting demo…")
                return cli.start_live(args, root, password or None)
            except BaseException:
                stack.close()  # remove any extracted temp copy if starting failed
                raise

        def after(result):
            live, url = result
            self.live, self.live_url, self.live_stack = live, url, stack
            self.live_status_var.set(f"Running at {url}  –  {'demo players' if demo else 'live from ' + args.host}. "
                                     "Keep this window open while you watch.")
            self.live_start_btn.configure(state="disabled")
            self.live_demo_btn.configure(state="disabled")
            self.live_stop_btn.configure(state="normal")
            self.live_open_btn.configure(state="normal")
            self.log(f"Live map running at {url}\n")
            appwindow.open_page(url)

        self._run("Live map", work, after)

    def _live_stop(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None
        self.live_stack.close()
        self.live_url = None
        self.live_status_var.set("Stopped.")
        self.live_start_btn.configure(state="normal")
        self.live_demo_btn.configure(state="normal")
        self.live_stop_btn.configure(state="disabled")
        self.live_open_btn.configure(state="disabled")

    # ------------------------------------------------------------------ videos
    def _tab_videos(self, nb) -> ttk.Frame:
        """Text and pictures on a finished Flashback video: everything happens in the live editor
        (a page in the browser); this tab only picks the video."""
        f = ttk.Frame(nb, padding=12)
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Add titles, subtitles and pictures to a video you exported from Flashback, without "
                          "editing skills. Choose the video, then open the live editor: the video plays with your "
                          "texts on it while you change them. Texts can stand in the Minecraft world itself (on a "
                          "mountain, following a player) or be drawn on the video. See docs/VIDEO_TEXT.md.",
                  wraplength=680, justify="left").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="Finished video:").grid(row=1, column=0, sticky="w", pady=(10, 3))
        self.vid_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.vid_var).grid(row=1, column=1, sticky="ew", padx=4, pady=(10, 3))
        ttk.Button(f, text="Choose…", command=self._vid_pick).grid(row=1, column=2, sticky="w", pady=(10, 3))
        btns = ttk.Frame(f)
        btns.grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Button(btns, text="Open live editor", command=self._vid_live).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Chat & events as subtitles", command=self._vid_subtitles).pack(side="left")
        ttk.Label(f, text="Not exported yet? Open the Flashback edit itself: the editor shows the app's 3D map through "
                          "your edit's camera, so you can place texts and the song before exporting.",
                  wraplength=680, justify="left").grid(row=4, column=0, columnspan=3, sticky="w", pady=(14, 3))
        ttk.Button(f, text="Open a Flashback edit…", command=self._vid_edit).grid(row=5, column=0, sticky="w")
        ttk.Label(f, text="Real Minecraft picture in the editor: install the small companion mod into your Flashback "
                          "instance once, start Minecraft and open the replay; the editor's 'Flashback (real)' view "
                          "then shows what the game draws.", wraplength=680, justify="left").grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(14, 3))
        ttk.Button(f, text="Install the Flashback link…", command=self._vid_install_link).grid(row=7, column=0, sticky="w")
        self.app_window_var = tk.BooleanVar(value=appwindow.enabled())
        ttk.Checkbutton(f, text="Open the editor, history viewer and live map in their own window (Edge or Chrome app "
                                "window: no tabs, no browser extensions); off = a tab in your browser",
                        variable=self.app_window_var,
                        command=lambda: appwindow.set_enabled(self.app_window_var.get())).grid(
            row=10, column=0, columnspan=3, sticky="w", pady=(14, 0))
        ttk.Label(f, text="A timelapse in several parts, or clips you exported? Join them here into one video, in "
                          "order, with a transition at each cut (cross-fade, dip to black, whip, block wipe…) and a "
                          "song if you like.",
                  wraplength=680, justify="left").grid(row=8, column=0, columnspan=3, sticky="w", pady=(14, 3))
        ttk.Button(f, text="Join videos…", command=self._vid_join).grid(row=9, column=0, sticky="w")
        self.vid_status_var = tk.StringVar(value="Choose a video.")
        ttk.Label(f, textvariable=self.vid_status_var, foreground="gray", wraplength=680, justify="left").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.vid_info = None
        return f

    def _vid_join(self) -> None:
        """Join videos (a timelapse's parts, highlight clips) into one, with a transition at each cut and a song
        (mcsm timelapse-join)."""
        from . import videojoin
        from . import flashback as fbm
        outs = fbm.export_folders()                      # where Flashback saves exported videos (newest first)
        start = outs[0]["path"] if outs else os.path.join(os.path.expanduser("~"), "Videos")
        vids = filedialog.askopenfilenames(title="The videos to join (Flashback names exports by date and time: "
                                                 "export them in order)",
                                           initialdir=start if os.path.isdir(start) else _documents_dir(),
                                           filetypes=[("Videos", "*.mp4 *.mov *.mkv *.webm"), ("All files", "*.*")])
        if not vids:
            return
        order = videojoin.ordered(sorted(vids, key=lambda p: os.path.basename(p).lower()))
        labels = dict(videojoin.transition_names())
        names_by_label = {v: k for k, v in labels.items()}
        win = tk.Toplevel(self.root)
        win.title("Join videos")
        win.transient(self.root)
        ttk.Label(win, text="The videos are joined in this order (Up / Down to change it), with a transition at "
                            "each cut. A transition overlaps the two clips a little, so the video gets that much "
                            "shorter.", wraplength=560, justify="left").pack(padx=10, pady=(10, 6), anchor="w")
        top = ttk.Frame(win)
        top.pack(fill="x", padx=10)
        lb = tk.Listbox(top, height=min(10, max(3, len(order))), width=70, exportselection=False)
        lb.pack(side="left", fill="x", expand=True)
        updown = ttk.Frame(top)
        updown.pack(side="left", padx=6)
        every = ttk.Frame(win)
        every.pack(fill="x", padx=10, pady=(8, 0))
        ttk.Label(every, text="Every cut:").pack(side="left")
        all_var = tk.StringVar(value=labels["cut"])
        all_box = ttk.Combobox(every, textvariable=all_var, values=list(labels.values()), state="readonly", width=32)
        all_box.pack(side="left", padx=4)
        ttk.Label(every, text="Length (s):").pack(side="left", padx=(10, 0))
        len_var = tk.StringVar(value=str(videojoin.DEFAULT_LENGTH))
        ttk.Spinbox(every, from_=0.1, to=3.0, increment=0.1, textvariable=len_var, width=5).pack(side="left", padx=4)
        cuts_frame = ttk.Frame(win)
        cuts_frame.pack(fill="x", padx=10, pady=(6, 0))
        cut_vars: list[tk.StringVar] = []

        def show():
            sel = lb.curselection()
            lb.delete(0, "end")
            for i, v in enumerate(order, 1):
                lb.insert("end", f"{i}. {os.path.basename(v)}")
            if sel:
                lb.selection_set(sel[0])
            for w in cuts_frame.winfo_children():
                w.destroy()
            old = [v.get() for v in cut_vars]
            cut_vars.clear()
            for i in range(len(order) - 1):
                var = tk.StringVar(value=old[i] if i < len(old) else all_var.get())
                cut_vars.append(var)
                row_ = ttk.Frame(cuts_frame)
                row_.pack(fill="x", pady=1)
                ttk.Label(row_, text=f"Between {i + 1} and {i + 2}:", width=16).pack(side="left")
                ttk.Combobox(row_, textvariable=var, values=list(labels.values()), state="readonly", width=32).pack(side="left")

        def move(d):
            sel = lb.curselection()
            if not sel:
                return
            i, j = sel[0], sel[0] + d
            if 0 <= j < len(order):
                order[i], order[j] = order[j], order[i]
                lb.selection_clear(0, "end")
                lb.selection_set(j)
                show()
                lb.selection_set(j)
        ttk.Button(updown, text="Up", command=lambda: move(-1)).pack(fill="x")
        ttk.Button(updown, text="Down", command=lambda: move(1)).pack(fill="x", pady=2)

        def set_all(_e=None):
            for v in cut_vars:
                v.set(all_var.get())
        all_box.bind("<<ComboboxSelected>>", set_all)
        show()
        mus = ttk.Frame(win)
        mus.pack(fill="x", padx=10, pady=(10, 0))
        ttk.Label(mus, text="Song (optional):").pack(side="left")
        music_var = tk.StringVar()
        ttk.Entry(mus, textvariable=music_var, width=40).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(mus, text="Choose…", command=lambda: music_var.set(filedialog.askopenfilename(
            parent=win, title="Song", filetypes=[("Music", "*.mp3 *.ogg *.wav *.m4a *.flac"), ("All files", "*.*")])
            or music_var.get())).pack(side="left")
        ttk.Label(mus, text="from (s):").pack(side="left", padx=(8, 0))
        mstart_var = tk.StringVar(value="0")
        ttk.Entry(mus, textvariable=mstart_var, width=5).pack(side="left", padx=4)
        ttk.Label(win, text="A song replaces the videos' own sound, loops if it's shorter and fades out at the end.",
                  foreground="gray").pack(padx=10, anchor="w")

        def go():
            first = os.path.basename(order[0])
            name = videojoin._PART.sub("", os.path.splitext(first)[0]).strip() or "joined"
            out = filedialog.asksaveasfilename(parent=win, title="Save the joined video as", defaultextension=".mp4",
                                               initialdir=os.path.dirname(order[0]), initialfile=f"{name} joined.mp4",
                                               filetypes=[("MP4 video", "*.mp4")])
            if not out:
                return
            trans = ",".join(names_by_label.get(v.get(), "cut") for v in cut_vars) or "cut"
            argv = ["timelapse-join", *order, "-o", out, "--transitions", trans, "--transition-length", len_var.get() or "0.6"]
            if music_var.get().strip():
                argv += ["--music", music_var.get().strip(), "--music-start", mstart_var.get().strip() or "0"]
            args = self._parse(argv)
            if args is None:
                return
            win.destroy()
            self.vid_status_var.set("Joining the videos… (see the log)")
            self._run("Join videos", lambda _d: cli.cmd_timelapse_join(args),
                      after=lambda rc: self.vid_status_var.set(f"Joined: {out}" if rc == 0 else "Joining failed (see the log)."))
        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=10, pady=10)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="Join…", command=go).pack(side="right", padx=6)

    def _vid_live(self) -> None:
        """The live editor page: the video plays with the text drawn on it while you change it."""
        from .videoeditor import VideoEditor
        if not self.vid_info:
            messagebox.showinfo(APP_NAME, "Choose a video first.")
            return
        if getattr(self, "vid_editor", None):
            self.vid_editor.stop()
        self.vid_editor = VideoEditor(self.vid_info["video"], world_dir=self._vid_world_dir(), info=self.vid_info)
        url = self.vid_editor.start()
        self.vid_status_var.set(f"Live editor open in your browser: {url}")
        appwindow.open_page(url)

    def _vid_edit(self) -> None:
        """Choose a Flashback edit by its scene name and recording, then open the live editor on it."""
        from . import videotext as vt
        world = self._vid_world_dir()
        self._run("Reading your Flashback edits", lambda _: vt.list_edits(world), lambda edits: self._vid_edit_pick(edits))

    def _vid_edit_pick(self, edits: list[dict]) -> None:
        if not edits:
            messagebox.showinfo(APP_NAME, "No Flashback edits were found (Flashback keeps them in "
                                          "flashback/editor_states of the Minecraft instance).")
            return
        win = tk.Toplevel(self.root)
        win.title("Open a Flashback edit")
        win.transient(self.root)
        win.geometry("900x460")
        ttk.Label(win, text="Your Flashback edits, newest first. Choose a scene and press Open (or double-click). "
                            "'copy' = a copy the app made (with text).", wraplength=860, justify="left").pack(
            padx=10, pady=(10, 4), anchor="w")
        cols = ("scene", "recording", "length", "keys", "saved", "where")
        tree = ttk.Treeview(win, columns=cols, show="headings", selectmode="browse")
        for c, label, w in (("scene", "Scene", 170), ("recording", "Recording", 300), ("length", "Length", 70),
                            ("keys", "Camera keyframes", 110), ("saved", "Saved", 120), ("where", "Flashback", 120)):
            tree.heading(c, text=label)
            tree.column(c, width=w, anchor="w")
        tree.pack(fill="both", expand=True, padx=10)
        show_empty = tk.BooleanVar(value=False)
        rows: dict[str, dict] = {}

        def fill():
            tree.delete(*tree.get_children())
            rows.clear()
            for e in edits:
                if not show_empty.get() and not e["keyframes"] and e["length"] is None:
                    continue
                length = f"{int(e['length'] // 60)}:{e['length'] % 60:04.1f}" if e["length"] is not None else "no export range"
                several = sum(1 for o in edits if o["state"] == e["state"]) > 1
                name = e["scene_name"] + (" (current scene)" if e["current"] and several else "") + (" · copy" if e["copy"] else "")
                iid = tree.insert("", "end", values=(name, e["recording_name"], length, e["keyframes"], e["saved_text"],
                                                     e["instance"]))
                rows[iid] = e
            kids = tree.get_children()
            if kids:
                tree.selection_set(kids[0])
        fill()
        row = ttk.Frame(win)
        row.pack(fill="x", padx=10, pady=8)
        ttk.Checkbutton(row, text="Show empty edits (no camera keyframes, no export range)", variable=show_empty,
                        command=fill).pack(side="left")

        def go(_event=None):
            sel = tree.selection()
            if not sel:
                return
            e = rows[sel[0]]
            if e["length"] is None:
                messagebox.showinfo(APP_NAME, "This scene has no export range yet: set the export start and end in "
                                              "Flashback first.", parent=win)
                return
            win.destroy()
            self._vid_edit_open(e["state"], e["scene"])
        tree.bind("<Double-1>", go)

        def menu(event):
            """Right-click: where this edit's files are (Flashback has no "show in folder")."""
            from . import flashback as fbm
            iid = tree.identify_row(event.y)
            if not iid:
                return
            tree.selection_set(iid)
            e = rows[iid]
            fb = os.path.dirname(os.path.dirname(e["state"]))          # .../flashback/editor_states/x.json
            m = tk.Menu(win, tearoff=0)
            rec = e.get("recording")
            m.add_command(label="Show the recording in its folder", state="normal" if rec and os.path.exists(rec) else "disabled",
                          command=lambda: show_in_folder(rec))
            m.add_command(label="Show the edit's timeline file (editor state)", command=lambda: show_in_folder(e["state"]))
            m.add_command(label="Open this Flashback's replays folder",
                          command=lambda: show_in_folder(os.path.join(fb, "replays")))
            out = fbm.export_folder(fb)
            m.add_command(label=f"Open the folder Flashback saves videos to{'' if out else ' (none set yet)'}",
                          state="normal" if out else "disabled", command=lambda: show_in_folder(out))
            m.tk_popup(event.x_root, event.y_root)
        tree.bind("<Button-3>", menu)
        ttk.Label(win, text="Right-click an edit to find its recording, its timeline file or the exported videos.",
                  foreground="gray").pack(padx=10, anchor="w")
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(row, text="Open", command=go).pack(side="right", padx=6)

    def _vid_edit_open(self, p: str, scene: int | None = None) -> None:
        from .videoeditor import VideoEditor
        world = self._vid_world_dir()

        def work(_):
            print("Reading the edit and finding its recording…")
            ed = VideoEditor(None, world, edit=p, scene=scene)
            return ed, ed.start()

        def after(r):
            ed, url = r
            if getattr(self, "vid_editor", None):
                self.vid_editor.stop()
            self.vid_editor = ed
            m = ed.vinfo["matches"][0]
            self.vid_status_var.set(f"Live editor for '{m['name']}' ({ed.vinfo['duration']:.1f} s) open in your browser: {url}")
            appwindow.open_page(url)

        self._run("Opening the Flashback edit", work, after)

    def _vid_install_link(self) -> None:
        """Copy the companion mod into the mods folder of a Minecraft instance that has Flashback."""
        from . import flashlink
        jar = flashlink.bundled_mod()
        if not jar:
            messagebox.showinfo(APP_NAME, "This copy of the app doesn't include the companion mod (it comes with the "
                                          "installer from GitHub).")
            return
        insts = [i for i in flashlink.instances() if os.path.isdir(i["minecraft"])]
        if not insts:
            messagebox.showinfo(APP_NAME, "No Minecraft instance with Flashback was found.")
            return
        win = tk.Toplevel(self.root)
        win.title("Install the Flashback link")
        win.transient(self.root)
        ttk.Label(win, text="Which Minecraft has Flashback? The mod is copied into its mods folder (nothing else "
                            "is changed). It needs Minecraft 26.1 or newer and Fabric.", wraplength=460,
                  justify="left").pack(padx=12, pady=(12, 6), anchor="w")
        choice = tk.StringVar(value=insts[0]["name"])
        for i in insts:
            ttk.Radiobutton(win, text=f"{i['name']} (Minecraft {i.get('mc') or '?'})", value=i["name"],
                            variable=choice).pack(padx=18, anchor="w")

        def go():
            inst = next(i for i in insts if i["name"] == choice.get())
            if not messagebox.askyesno(APP_NAME, f"Copy {os.path.basename(jar)} into\n{inst['mods']}?\n\n"
                                                 "Restart Minecraft afterwards.", parent=win):
                return
            try:
                dst = flashlink.install_mod(inst["mods"], jar)
            except (OSError, ValueError) as exc:
                messagebox.showerror(APP_NAME, str(exc), parent=win)
                return
            win.destroy()
            self.vid_status_var.set(f"Installed {os.path.basename(dst)} in '{inst['name']}'. Start Minecraft, open the "
                                    "replay in Flashback, then use 'Flashback (real)' in the editor.")

        row = ttk.Frame(win)
        row.pack(padx=12, pady=12, anchor="e")
        ttk.Button(row, text="Install", command=go).pack(side="left", padx=4)
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="left")

    def _vid_world_dir(self) -> str | None:
        from . import library as lib
        if self.data is not None and self.data.library_world:
            return self.data.library_world
        try:
            worlds = lib.list_worlds(self.lib_dir_var.get(), count_recordings=False)
        except OSError:
            worlds = []
        return worlds[0]["path"] if len(worlds) == 1 else None

    def _vid_pick(self) -> None:
        from . import videotext as vt
        p = filedialog.askopenfilename(title="Choose the finished video",
                                       filetypes=[("Videos", "*.mp4 *.mov *.mkv"), ("All files", "*.*")])
        if not p:
            return
        self.vid_var.set(p)
        world = self._vid_world_dir()

        def work(_):
            print("Finding the Flashback edit and recording (the first time reads every recording's id)…")
            return vt.analyse_video(p, world)

        def after(info):
            self.vid_info = info
            m = info["matches"][0] if info["matches"] else None
            still = m.get("still_from") if m else None
            found = (f"Made from '{m['name']}' of {os.path.basename(m['recording'])}." if m and m.get("recording") else
                     ("Its Flashback edit was found, but not the recording (import it into the library)." if m else
                      "No Flashback edit found: only 'on the video' texts and pictures."))
            self.vid_status_var.set(f"{info['duration']:.1f} s, {info['width']}x{info['height']}. {found} "
                                    + (f"The camera stops at {still:.1f} s. " if still is not None else "")
                                    + "Now open the live editor.")

        self._run("Looking at the video", work, after)

    def _vid_subtitles(self) -> None:
        if self.data is None:
            messagebox.showinfo(APP_NAME, "Load your server (or library world) first: the chat and events come "
                                          "from its logs.")
            return
        if not self.vid_var.get():
            messagebox.showinfo(APP_NAME, "Choose a video first.")
            return
        args = self._parse(["video-text"] + self._common_argv() + ["--video", self.vid_var.get()])
        if args is not None:
            self._run("Chat & events as subtitles", lambda d: cli.run_video_text(d, args))

    def _tab_history(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb, padding=12)
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Everything in your loaded backups as one recording: a 3D map with a time slider over "
                          "the whole history. Player paths come from your Flashback recordings; joins, deaths and "
                          "chat from the logs. Mark highlights for videos and export them to Flashback. "
                          "Load one or more backups first. See docs/HISTORY.md.",
                  wraplength=660, justify="left").grid(row=0, column=0, columnspan=3, sticky="w")
        self.hist_replays_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Read player positions from Flashback recordings (first time: a few minutes; "
                                "then cached)", variable=self.hist_replays_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 3))
        self.hist_tracks_var = tk.BooleanVar(value=False)
        self.hist_tracks_dir = tk.StringVar(value=os.path.join(_documents_dir(), "mcsm-live-tracks"))
        ttk.Checkbutton(f, text="Also live-map positions from:", variable=self.hist_tracks_var).grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self.hist_tracks_dir).grid(row=2, column=1, sticky="ew", padx=4)
        ttk.Button(f, text="Browse…", command=lambda: self._pick_dir(self.hist_tracks_dir)).grid(row=2, column=2, sticky="w")
        self.hist_all_terrain_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Terrain from every backup (the map shows the world as it was at the time on the "
                                "slider)", variable=self.hist_all_terrain_var).grid(row=3, column=0, columnspan=3, sticky="w", pady=3)
        btns = ttk.Frame(f)
        btns.grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.hist_start_btn = self._button(btns, "Open history viewer", self._history_start)
        self.hist_start_btn.pack(side="left")
        self.hist_open_btn = ttk.Button(btns, text="Open page", state="disabled",
                                        command=lambda: self.history_url and appwindow.open_page(self.history_url))
        self.hist_open_btn.pack(side="left", padx=6)
        self.hist_stop_btn = ttk.Button(btns, text="Stop", command=self._history_stop, state="disabled")
        self.hist_stop_btn.pack(side="left")
        self.hist_status_var = tk.StringVar(value="Not running.")
        ttk.Label(f, textvariable=self.hist_status_var, foreground="gray", wraplength=660).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))
        return f

    def _history_start(self, story: bool = False) -> None:
        if self.history is not None and self.history_url:
            appwindow.open_page(self.history_url + ("?story=1" if story else ""))      # already running
            return
        if self.data is None:
            messagebox.showinfo(APP_NAME, "Load your server or a library world first (Library tab: select the world, "
                                          "then 'Load this world' or 'Watch the server story').")
            return
        if self.busy:
            return
        argv = ["history", *self._common_argv(), "--no-browser"]
        if not self.hist_replays_var.get():
            argv.append("--no-replays")
        if self.hist_tracks_var.get() and self.hist_tracks_dir.get().strip():
            argv += ["--tracks", self.hist_tracks_dir.get().strip()]
        if not self.hist_all_terrain_var.get():
            argv += ["--terrain", "newest"]
        args = cli.parse_args(argv)
        paths = [p.strip() for p in self.server_var.get().split(SEP.strip()) if p.strip()]
        newest = self.data.backups[-1]["path"] if self.data.backups else (paths[-1] if paths else None)
        stack = contextlib.ExitStack()

        def work(data):
            try:
                print("Starting the history viewer…")
                return cli.start_history(args, data, stack, newest)
            except BaseException:
                stack.close()
                raise

        def after(result):
            hm, url = result
            self.history, self.history_url, self.history_stack = hm, url, stack
            self.hist_status_var.set(
                f"Running at {url}. Player paths and terrain keep loading in the background; the page fills "
                f"in as they arrive. Keep this window open.\nHighlights are saved in {hm.highlights.path}; "
                + (f"exports go into Flashback ({hm.flashback_dir})." if hm.flashback_dir
                   else "Flashback's folder wasn't found, so exports go next to that file."))
            self.hist_start_btn.configure(state="disabled")
            self.hist_stop_btn.configure(state="normal")
            self.hist_open_btn.configure(state="normal")
            self.log(f"History viewer running at {url}\n")
            appwindow.open_page(url + ("?story=1" if story else ""))

        self._run("History viewer", work, after)

    def _history_stop(self) -> None:
        if self.history is not None:
            self.history.stop()
            self.history = None
        self.history_stack.close()
        self.history_url = None
        self.hist_status_var.set("Stopped.")
        self.hist_start_btn.configure(state="normal" if self.data is not None else "disabled")
        self.hist_stop_btn.configure(state="disabled")
        self.hist_open_btn.configure(state="disabled")

    def _tab_export(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb, padding=12)
        ttk.Label(f, text="Raw data for spreadsheets or your own tools (see docs/DATA_MODEL.md).",
                  wraplength=640).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        self._button(f, "SQLite database…", lambda: self._export("sqlite"), row=1, column=0, sticky="w")
        self._button(f, "JSON file…", lambda: self._export("json"), row=1, column=1, sticky="w", padx=6)
        self._button(f, "CSV files (folder)…", lambda: self._export("csv"), row=1, column=2, sticky="w")
        return f

    def _build_log(self) -> None:
        # The loading bar: shown while a job runs, with what it's doing and a Cancel button.
        self.job_frame = ttk.Frame(self)
        self.job_frame.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.job_frame.columnconfigure(1, weight=1)
        self.job_text = tk.StringVar()
        ttk.Label(self.job_frame, textvariable=self.job_text, width=46, anchor="w").grid(row=0, column=0, sticky="w")
        self.job_bar = ttk.Progressbar(self.job_frame, mode="indeterminate")
        self.job_bar.grid(row=0, column=1, sticky="ew", padx=6)
        self.job_cancel = ttk.Button(self.job_frame, text="Cancel", command=self._cancel_job)
        self.job_cancel.grid(row=0, column=2)
        self.job_frame.grid_remove()
        self.job: jobs.Job | None = None
        ttk.Label(self, text="Output").grid(row=2, column=0, sticky="w")
        frame = ttk.Frame(self)
        frame.grid(row=3, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(frame, height=7, wrap="word", state="disabled",
                                font=("Consolas" if sys.platform.startswith("win") else "TkFixedFont", 9))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frame, command=self.log_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_text["yscrollcommand"] = sb.set

    # ----------------------------------------------------------------- helpers
    def log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.load_btn.configure(state="disabled" if busy else "normal")
        for b in self.action_buttons:
            needs_data = b.cget("text") != "Detect BPM only"
            b.configure(state="disabled" if busy or (needs_data and self.data is None) else "normal")
        self.root.configure(cursor="watch" if busy else "")

    def _show_job(self, title: str | None) -> None:
        """Show the loading bar for a job (*title*), or hide it (None)."""
        if title is None:
            self.job_bar.stop()
            self.job_frame.grid_remove()
            return
        self.job_text.set(title + "…")
        self.job_bar.configure(mode="indeterminate", value=0)
        self.job_bar.start(12)
        self.job_cancel.configure(state="normal", text="Cancel")
        self.job_frame.grid()

    def _job_progress(self, done, total, text) -> None:
        if text:
            self.job_text.set(text)
        if total:
            if str(self.job_bar.cget("mode")) != "determinate":
                self.job_bar.stop()
                self.job_bar.configure(mode="determinate")
            self.job_bar.configure(maximum=total, value=min(done or 0, total))
        elif str(self.job_bar.cget("mode")) != "indeterminate":
            self.job_bar.configure(mode="indeterminate", value=0)
            self.job_bar.start(12)

    def _cancel_job(self) -> None:
        if self.job is not None:
            self.job.cancel()
            self.job_text.set("Cancelling… (stops after the file it's on)")
            self.job_cancel.configure(state="disabled")

    def _poll(self) -> None:
        try:
            while True:
                item = self.q.get_nowait()
                kind, payload = item[0], item[1]
                if kind == "log":
                    self.log(payload)
                elif kind == "progress":
                    job, args = payload
                    if job is self.job:
                        self._job_progress(*args)
                elif kind == "done":
                    job, exclusive, then = payload if isinstance(payload, tuple) else (None, True, payload)
                    if job is None or job is self.job:
                        self.job = None
                        self._show_job(None)
                    if exclusive:
                        self._set_busy(False)
                    if callable(then):
                        then()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _run(self, title: str, work, after=None, cancelled=None, quiet: bool = False,
             exclusive: bool = True) -> None:
        """Run ``work(data)`` on a thread, streaming its printed output to the log, with the loading bar
        (progress from :mod:`mcsm.progress`) and its Cancel button. *cancelled*: called after a cancel.

        *exclusive* jobs are one at a time (the buttons wait); a non-exclusive one (reading the library
        list) runs beside them, doesn't print, and gives the bar up to any exclusive job that starts."""
        if exclusive and self.busy:
            return
        if exclusive:
            self._set_busy(True)
        if not quiet:
            self.log(f"\n--- {title} ---\n")
        writer = _QueueWriter(self.q)
        job = jobs.Job(lambda d, t, txt: self.q.put(("progress", (job, (d, t, txt)))))
        if exclusive or self.job is None:
            self.job = job
            self._show_job(title)

        def done(then):
            self.q.put(("done", (job, exclusive, then)))

        def target():
            jobs.set_job(job)
            try:
                if exclusive:
                    with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                        result = work(self.data)
                else:
                    result = work(self.data)
                done((lambda: after(result)) if after else None)
            except jobs.Cancelled:
                self.q.put(("log", f"{title}: cancelled.\n"))
                done(cancelled)
            except SystemExit as exc:  # cli helpers use SystemExit for user errors
                self.q.put(("log", f"Error: {exc}\n"))
                done(None)
            except Exception as exc:  # noqa: BLE001 - show anything to the user
                msg = str(exc)
                self.q.put(("log", f"Error: {msg}\n"))
                self.q.put(("log", traceback.format_exc()))
                done(lambda: messagebox.showerror(APP_NAME, msg))
            finally:
                jobs.set_job(None)

        threading.Thread(target=target, daemon=True).start()

    def _common_argv(self) -> list[str]:
        argv = ["server-already-loaded"]
        if self.tz_var.get().strip():
            argv += ["--tz", self.tz_var.get().strip()]
        return argv

    # ----------------------------------------------------------------- pickers
    def _pick_zip(self):
        ps = filedialog.askopenfilenames(title="Choose the server backup(s) – select several to merge them",
                                         filetypes=[("Zip files", "*.zip"), ("All files", "*.*")])
        if ps:
            self.server_var.set(SEP.join(ps))
            self._load()

    def _add_backup(self):
        ps = filedialog.askopenfilenames(title="Add backup(s) to merge", filetypes=[("Zip files", "*.zip"), ("All files", "*.*")])
        if ps:
            have = [p.strip() for p in self.server_var.get().split(SEP.strip()) if p.strip()]
            self.server_var.set(SEP.join(have + [p for p in ps if p not in have]))
            self._load()

    def _pick_folder(self):
        p = filedialog.askdirectory(title="Choose the server folder")
        if p:
            self.server_var.set(p)
            self._load()

    def _pick_dir(self, var):
        p = filedialog.askdirectory(title="Choose a folder", initialdir=var.get() or _documents_dir())
        if p:
            var.set(p)

    def _pick_music(self):
        p = filedialog.askopenfilename(title="Choose a song", filetypes=[
            ("Audio", "*.wav *.mp3 *.ogg *.flac *.m4a *.aac"), ("All files", "*.*")])
        if p:
            self.music_var.set(p)

    def _pick_replay(self):
        p = filedialog.askopenfilename(title="Choose a replay", filetypes=[
            ("Replays (ReplayMod .mcpr, Flashback .zip)", "*.mcpr *.zip"), ("All files", "*.*")])
        if not p or self.data is None:
            return
        from .replays import read_replay
        try:
            r = read_replay(p, self.data.root, {pl.uuid: pl.name for pl in self.data.players.values() if pl.uuid})
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"Could not read replay (is it a ReplayMod .mcpr or Flashback .zip?): {exc}")
            return
        r.path = os.path.abspath(p)
        self.data.replays.append(r)
        self._fill_replays(select=len(self.data.replays) - 1)

    def _fill_replays(self, select: int = 0):
        items = []
        for i, r in enumerate(self.data.replays if self.data else [], start=1):
            when = r.start.astimezone().strftime("%Y-%m-%d %H:%M") if r.start else "?"
            who = r.recorded_player or ", ".join(r.player_names[:3])
            kind = "Flashback" if r.format == "flashback" else "ReplayMod"
            if r.unfinished:
                kind += ", unfinished"
            items.append(f"{i}. {when}  ({format_hms(r.duration_ms / 1000)})  {who}  –  {kind}  –  "
                         f"{os.path.basename(r.path.rstrip('/'))}")
        self.replay_combo["values"] = items
        self._fb_replays = [r for r in (self.data.replays if self.data else [])
                            if r.format == "flashback" and not r.unfinished]
        fb_items = [items[self.data.replays.index(r)] for r in self._fb_replays]
        self.fb_combo["values"] = fb_items
        if fb_items:
            self.fb_combo.current(0)
        else:
            self.fb_combo.set("No finished Flashback replays in this download")
        if items:
            self.replay_combo.current(min(select, len(items) - 1))
            self.mode_var.set("replay")
        else:
            self.replay_combo.set("No replays found in this server")
            self.mode_var.set("manual")

    # ----------------------------------------------------------------- actions
    def _load(self, then=None):
        path = self.server_var.get().strip()
        if not path:
            messagebox.showinfo(APP_NAME, "Choose your server .zip or folder first.")
            return
        try:
            tz = parse_tz(self.tz_var.get()) if self.tz_var.get().strip() else None
            latest = date.fromisoformat(self.latest_var.get().strip()) if self.latest_var.get().strip() else None
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        self.status_var.set("Loading… (big servers can take a minute)")

        paths = [p.strip() for p in path.split(SEP.strip()) if p.strip()]
        stack = contextlib.ExitStack()
        tz_spec = self.tz_var.get().strip()             # read here: tkinter values only on the main thread

        def work(_):
            # Backups stay (selectively) extracted while loaded: Flashback copies and
            # timelapses read full replays from them later. Closed on the next load / exit.
            try:
                from . import loadcache                       # the saved result when nothing changed
                data = loadcache.load(paths, stack, tz=tz, tz_spec=tz_spec, latest=latest)
                from . import ignore
                left_out = ignore.apply(data)             # players who were never supposed to join
                if left_out:
                    print(f"Ignored players left out: {', '.join(left_out)}")
                return data, cli.live_settings(data.root)
            except BaseException:
                stack.close()
                raise

        def after(result):
            data, live_cfg = result
            self.server_stack.close()          # release the previously loaded backup(s)
            self.server_stack = stack
            self.data = data
            # Pre-fill the Live map tab from the download's server.properties.
            if live_cfg.get("port"):
                self.live_port_var.set(str(live_cfg["port"]))
            if live_cfg.get("password") and not self.live_pw_var.get():
                self.live_pw_var.set(live_cfg["password"])
            from . import updater
            s = updater.load_settings()                  # opened again when the app starts
            s["last_loaded"] = {"path": path, "tz": self.tz_var.get().strip(), "latest": self.latest_var.get().strip()}
            updater.save_settings(s)
            merged = f"{len(data.backups)} backups merged, " if data.backups else ""
            self.status_var.set(f"Loaded: {merged}{len(data.players)} players, {len(data.events):,} events, "
                                f"{len(data.replays)} replays.  Time zone: {data.timezone_info}"
                                + ("  (saved result: nothing changed since last time)"
                                   if getattr(data, "loaded_from_cache", False) else ""))
            self._fill_replays()
            self._set_busy(False)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                cli.run_summary(data)
            self.log(buf.getvalue())
            if then:
                then()

        self._run("Loading server", work, after,
                  cancelled=lambda: self.status_var.set("Loading cancelled." if self.data is None else
                                                        "Loading cancelled: the server loaded before is still loaded."))

    def _report(self):
        name = (self.data.world.get("level_name") or "server") if self.data else "server"
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in str(name)).strip() or "server"
        out = filedialog.asksaveasfilename(title="Save report as", defaultextension=".html",
                                           initialdir=_documents_dir(), initialfile=f"{safe} report.html",
                                           filetypes=[("Web page", "*.html")])
        if not out:
            return
        argv = ["report", *self._common_argv(), "-o", out]
        if self.title_var.get().strip():
            argv += ["--title", self.title_var.get().strip()]
        args = cli.parse_args(argv)
        self._run("Statistics report", lambda d: cli.run_report(d, args),
                  after=lambda _r: webbrowser.open("file:///" + os.path.abspath(out).replace(os.sep, "/").lstrip("/")))

    def _export(self, kind: str):
        if kind == "csv":
            out = filedialog.askdirectory(title="Folder for the CSV files", initialdir=_documents_dir())
        else:
            ext = ".sqlite" if kind == "sqlite" else ".json"
            out = filedialog.asksaveasfilename(title="Save as", defaultextension=ext, initialdir=_documents_dir(),
                                               initialfile="server" + ext)
        if not out:
            return
        args = cli.parse_args(["export", *self._common_argv(), f"--{kind}", out])
        self._run(f"Export {kind}", lambda d: cli.run_export(d, args),
                  after=lambda _r: open_path(out if kind == "csv" else os.path.dirname(out)))

    def _sync(self):
        argv = ["sync", *self._common_argv(), "-o", self.outdir_var.get().strip() or "mcsm-markers"]
        if self.mode_var.get() == "replay":
            idx = self.replay_combo.current()
            if idx < 0 or not self.data or not self.data.replays:
                messagebox.showinfo(APP_NAME, "Pick a replay, or choose 'Other footage' and type its start time.")
                return
            argv += ["--replay", str(idx + 1)]
        else:
            if not self.video_start_var.get().strip():
                messagebox.showinfo(APP_NAME, "Type the real date and time at 0:00 of your video.")
                return
            argv += ["--video-start", self.video_start_var.get().strip()]
        for flag, var in (("--render-start", self.render_start_var), ("--length", self.length_var),
                          ("--speed", self.speed_var), ("--fps", self.fps_var), ("--players", self.players_var),
                          ("--music", self.music_var), ("--bpm", self.bpm_var), ("--beat-offset", self.offset_var)):
            if var.get().strip():
                argv += [flag, var.get().strip()]
        types = [t for t, v in self.type_vars.items() if v.get()]
        if not types:
            messagebox.showinfo(APP_NAME, "Tick at least one kind of event.")
            return
        argv += ["--types", ",".join(types)]
        song, target = self.align_song_var.get().strip(), self.align_target_var.get().strip()
        if song or target:
            if not (song and target):
                messagebox.showinfo(APP_NAME, "For alignment fill in both the song time and where it should land "
                                              "(a marker like #12, or a video time like 3:40).")
                return
            argv += ["--music-align", f"{song}={target}"]
        argv += ["--snap", self.snap_var.get(), "--beat-markers", self.beatmk_var.get()]
        try:
            args = cli.parse_args(argv)
        except SystemExit:
            messagebox.showerror(APP_NAME, "One of the values is not valid (check speed and FPS are numbers).")
            return
        out_dir = args.out
        self._run("Video & music sync", lambda d: cli.run_sync(d, args), after=lambda _r: open_path(out_dir)
                  if os.path.isdir(out_dir) else None)

    def _detect_bpm(self):
        song = self.music_var.get().strip()
        if not song:
            self._pick_music()
            song = self.music_var.get().strip()
            if not song:
                return
        args = cli.parse_args(["music", song])

        self._run("Beat detection", lambda _d: cli.cmd_music(args))


def main() -> int:
    if sys.platform.startswith("win"):
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except tk.TclError:
        pass
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")
    if os.path.isfile(icon):
        try:
            root.iconphoto(True, tk.PhotoImage(file=icon))
        except tk.TclError:
            pass
    app = App(root)

    def on_close():
        app._live_stop()  # stop the live map server and remove temp files
        app._history_stop()
        app.server_stack.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.bind("<<mcsm-quit>>", lambda _e: on_close())         # the updater closes the app to install
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):  # "Open with…" / drag onto the icon
        app.server_var.set(sys.argv[1])
        app.after(200, app._load)
    else:
        app.after(300, app._reopen_last)                 # the last server, as it was
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
