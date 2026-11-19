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

from . import __version__, cli, models as m
from .analyzer import analyze
from .source import open_server
from .sync import DEFAULT_SYNC_TYPES
from .timeutil import format_hms, parse_tz

APP_NAME = "Minecraft Server Master"

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
        return len(s)


def open_path(path: str) -> None:
    """Open a file or folder with the operating system's default program."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


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
        self.live_stack = contextlib.ExitStack()
        self.live_url: str | None = None
        root.title(f"{APP_NAME} {__version__}")
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
        self.after(100, self._poll)
        self.log(f"{APP_NAME} {__version__}\n"
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

        opts = ttk.Frame(box)
        opts.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Label(opts, text="Server time zone:").pack(side="left")
        self.tz_var = tk.StringVar()
        ttk.Entry(opts, textvariable=self.tz_var, width=18).pack(side="left", padx=4)
        ttk.Label(opts, text="(blank = automatic)", foreground="gray").pack(side="left")
        ttk.Label(opts, text="   latest.log date:").pack(side="left")
        self.latest_var = tk.StringVar()
        ttk.Entry(opts, textvariable=self.latest_var, width=12).pack(side="left", padx=4)
        self.load_btn = ttk.Button(opts, text="Load server", command=self._load)
        self.load_btn.pack(side="right")
        self.status_var = tk.StringVar(value="No server loaded")
        ttk.Label(box, textvariable=self.status_var, foreground="gray").grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

    def _build_tabs(self) -> None:
        nb = ttk.Notebook(self)
        nb.grid(row=1, column=0, sticky="nsew", pady=8)
        self.nb = nb
        nb.add(self._tab_report(nb), text="Statistics report")
        nb.add(self._tab_sync(nb), text="Video & music sync")
        nb.add(self._tab_export(nb), text="Export data")
        nb.add(self._tab_live(nb), text="Live map")

    def _button(self, parent, text, command, **grid) -> ttk.Button:
        b = ttk.Button(parent, text=text, command=command, state="disabled")
        self.action_buttons.append(b)
        if grid:
            b.grid(**grid)
        return b

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
        self.live_open_btn = ttk.Button(btns, text="Open map", command=lambda: self.live_url and webbrowser.open(self.live_url),
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
            args = cli.build_parser().parse_args(argv)
        except SystemExit:
            messagebox.showerror(APP_NAME, "Check the RCON port and update interval are numbers.")
            return
        server_path = self.server_var.get().strip()
        password = self.live_pw_var.get()
        stack = contextlib.ExitStack()

        def work(_):
            try:
                root = stack.enter_context(open_server(server_path)) if server_path else None
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
            webbrowser.open(url)

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

    def _tab_export(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb, padding=12)
        ttk.Label(f, text="Raw data for spreadsheets or your own tools (see docs/DATA_MODEL.md).",
                  wraplength=640).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        self._button(f, "SQLite database…", lambda: self._export("sqlite"), row=1, column=0, sticky="w")
        self._button(f, "JSON file…", lambda: self._export("json"), row=1, column=1, sticky="w", padx=6)
        self._button(f, "CSV files (folder)…", lambda: self._export("csv"), row=1, column=2, sticky="w")
        return f

    def _build_log(self) -> None:
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

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "done":
                    self._set_busy(False)
                    if callable(payload):
                        payload()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _run(self, title: str, work, after=None) -> None:
        """Run ``work(data)`` on a thread, streaming its printed output to the log."""
        if self.busy:
            return
        self._set_busy(True)
        self.log(f"\n--- {title} ---\n")
        writer = _QueueWriter(self.q)

        def target():
            try:
                with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                    result = work(self.data)
                self.q.put(("done", (lambda: after(result)) if after else None))
            except SystemExit as exc:  # cli helpers use SystemExit for user errors
                self.q.put(("log", f"Error: {exc}\n"))
                self.q.put(("done", None))
            except Exception as exc:  # noqa: BLE001 - show anything to the user
                self.q.put(("log", f"Error: {exc}\n"))
                self.q.put(("log", traceback.format_exc()))
                self.q.put(("done", lambda: messagebox.showerror(APP_NAME, str(exc))))

        threading.Thread(target=target, daemon=True).start()

    def _common_argv(self) -> list[str]:
        argv = ["server-already-loaded"]
        if self.tz_var.get().strip():
            argv += ["--tz", self.tz_var.get().strip()]
        return argv

    # ----------------------------------------------------------------- pickers
    def _pick_zip(self):
        p = filedialog.askopenfilename(title="Choose the server download",
                                       filetypes=[("Zip files", "*.zip"), ("All files", "*.*")])
        if p:
            self.server_var.set(p)
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
        if items:
            self.replay_combo.current(min(select, len(items) - 1))
            self.mode_var.set("replay")
        else:
            self.replay_combo.set("No replays found in this server")
            self.mode_var.set("manual")

    # ----------------------------------------------------------------- actions
    def _load(self):
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

        def work(_):
            with open_server(path) as root:
                print(f"Reading {path}")
                return analyze(root, tz=tz, latest_log_date=latest), cli.live_settings(root)

        def after(result):
            data, live_cfg = result
            self.data = data
            # Pre-fill the Live map tab from the download's server.properties.
            if live_cfg.get("port"):
                self.live_port_var.set(str(live_cfg["port"]))
            if live_cfg.get("password") and not self.live_pw_var.get():
                self.live_pw_var.set(live_cfg["password"])
            self.status_var.set(f"Loaded: {len(data.players)} players, {len(data.events):,} events, "
                                f"{len(data.replays)} replays.  Time zone: {data.timezone_info}")
            self._fill_replays()
            self._set_busy(False)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                cli.run_summary(data)
            self.log(buf.getvalue())

        self._run("Loading server", work, after)

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
        args = cli.build_parser().parse_args(argv)
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
        args = cli.build_parser().parse_args(["export", *self._common_argv(), f"--{kind}", out])
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
            args = cli.build_parser().parse_args(argv)
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
        args = cli.build_parser().parse_args(["music", song])

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
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):  # "Open with…" / drag onto the icon
        app.server_var.set(sys.argv[1])
        app.after(200, app._load)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
