"""Live text editor for a finished Flashback video (a page in the browser).

The page plays the video and draws the text over it *live* while you change it: where it will sit
in the world (projected with the edit's camera path and field of view, so it moves correctly as the
camera moves), its size, colour, border, animation and timing. Buttons on the page then make the
real thing: the Flashback copy with the text in the world, or a new video with the text drawn on.

Routes (this computer only, see :func:`mcsm.live.serve`):
  GET  /                    the page (assets/web/videotext.html)
  GET  /video               the video, with seeking (HTTP Range)
  GET  /api/info            video, edits found, camera path of the chosen edit, settings
  GET  /api/job             progress of the running job
  POST /api/camera          {"match": i}  camera path of another edit
  POST /api/make            {"target": "world"|"video", ...settings}  start making it
"""

from __future__ import annotations

import threading

from . import videotext as vt
from .live import serve


class VideoEditor:
    PAGE = "videotext.html"

    def __init__(self, video: str, world_dir: str | None = None, info: dict | None = None):
        self.video = video
        self.vinfo = info or vt.analyse_video(video, world_dir)
        self.job: dict = {"running": False, "log": [], "result": None, "error": None}
        self._lock = threading.Lock()
        self.httpd = None

    # --- data for the page -------------------------------------------------------------------
    def camera_path(self, index: int) -> dict:
        """The camera (position, angles, field of view) through the video, every 0.05-0.1 s."""
        from . import worldtext as wt
        matches = self.vinfo["matches"]
        if not (0 <= index < len(matches)):
            return {"samples": [], "step": 0.1}
        sc = vt.scene_for_match(matches[index])
        clock = vt.scene_clock(sc)
        if clock is None:
            return {"samples": [], "step": 0.1}
        dur = self.vinfo["duration"]
        step = 0.05 if dur <= 600 else 0.1
        out, n = [], int(dur / step) + 1
        for i in range(n):
            tick = clock.replay_tick(min(i * step, clock.duration))
            cam = wt.camera_at(sc, tick)
            if cam is None:
                return {"samples": [], "step": step}
            pos, yaw, pitch = cam
            out.append([round(pos[0], 3), round(pos[1], 3), round(pos[2], 3), round(yaw, 3), round(pitch, 3),
                        round(vt.fov_at(sc, tick), 2), round(clock.speed_at(tick), 3)])
        return {"samples": out, "step": step}

    def info(self) -> dict:                 # /api/info (answered by the shared server)
        return self.page_info()

    def page_info(self) -> dict:
        i = self.vinfo
        return {"video": i["video"], "duration": i["duration"], "width": i["width"], "height": i["height"],
                "font": vt.minecraft_font(),
                "matches": [{"name": m["name"], "match": m["match"], "still_from": m.get("still_from"),
                             "recording": m.get("recording"), "uuid": m["uuid"]} for m in i["matches"]],
                "camera": self.camera_path(0) if i["matches"] else {"samples": [], "step": 0.1}}

    # --- web routes --------------------------------------------------------------------------
    def file_for(self, path: str) -> str | None:
        return self.video if path == "/video" else None

    def api_get(self, path: str):
        if path == "/api/job":
            with self._lock:
                return dict(self.job, log=self.job["log"][-30:])
        return None

    def api_post(self, path: str, body: dict):
        if path == "/api/camera":
            return self.camera_path(int(body.get("match", 0)))
        if path == "/api/make":
            return self._start(body)
        return None

    def _start(self, body: dict) -> dict:
        with self._lock:
            if self.job["running"]:
                raise ValueError("already making it; wait for it to finish")
            self.job = {"running": True, "log": [], "result": None, "error": None, "target": body.get("target")}

        def say(msg):
            with self._lock:
                self.job["log"].append(str(msg))

        def work():
            try:
                result = self._make(body, say)
                with self._lock:
                    self.job["result"] = result
            except Exception as exc:  # noqa: BLE001 -- shown on the page
                with self._lock:
                    self.job["error"] = str(exc)
            finally:
                with self._lock:
                    self.job["running"] = False

        threading.Thread(target=work, daemon=True).start()
        return {"started": True}

    def _make(self, b: dict, say) -> dict:
        lines = [(str(t), float(s)) for t, s in b.get("lines", []) if str(t).strip()]
        if not lines:
            raise ValueError("type some text first")
        at = b.get("at")
        at = None if at in (None, "") else float(at)
        until = b.get("until")
        until = None if until in (None, "") else float(until)
        look = {"animation": b.get("animation", "none"), "anim": float(b.get("anim", 0.4)),
                "word_gap": float(b.get("word_gap", 0.3)), "fade_out": float(b.get("fade_out", 0)),
                "border_colour": b.get("border_colour", "black"), "shadow": bool(b.get("shadow", False)),
                "box": "black" if b.get("box") else ""}
        colour, border = b.get("colour", "white"), bool(b.get("border", True))
        if b.get("target") == "world":
            match = self.vinfo["matches"][int(b.get("match", 0))]
            r = vt.make_in_world(match, lines, at, colour, border, progress=say, until_seconds=until,
                                 glow=bool(b.get("glow", True)), right=float(b.get("right", 0)),
                                 up=float(b.get("up", 0)), distance=float(b.get("distance", 55)),
                                 size_scale=float(b.get("size", 1)), video=self.video, **look)
            say(("Replaced" if r.get("replaced") else "Made") + f" '{r['name']}' in {r['flashback']}")
            return {"replay": r["replay"], "name": r["name"], "replaced": r.get("replaced", False)}
        screen_lines = [(t, s / 40.0) for t, s in lines]          # blocks -> share of the picture height
        start = at if at is not None else ((self.vinfo["matches"][0].get("still_from") or 0) + 0.2
                                           if self.vinfo["matches"] else 0.0)
        out = vt.make_on_screen(self.video, screen_lines, start, until if until is not None else self.vinfo["duration"] + 1,
                                b.get("style", "outline"), colour, progress=say, **look)
        say(f"Made {out}")
        return {"video": out}

    # --- running -----------------------------------------------------------------------------
    def start(self, port: int = 8790) -> str:
        self.httpd = serve(self, port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        host, p = self.httpd.server_address[:2]
        return f"http://{host}:{p}/"

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
