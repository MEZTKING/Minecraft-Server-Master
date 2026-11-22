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
  GET  /api/project         the saved texts of this video (or a first title)
  POST /api/project         {"items": [...], "match": i}  save them (done by the page as you edit)
  POST /api/make            {"target": "world"|"video", "items": [...], "match": i}  make all of them

Projects are kept in ``%LOCALAPPDATA%/mcsm/video-text/`` (one file per video), never next to the video.
"""

from __future__ import annotations

import hashlib
import json
import os
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
        if path == "/api/project":
            return self.load_project()
        if path == "/api/job":
            with self._lock:
                return dict(self.job, log=self.job["log"][-30:])
        return None

    def project_file(self) -> str:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
        key = hashlib.sha1(os.path.normcase(os.path.abspath(self.video)).encode()).hexdigest()[:16]
        return os.path.join(base, "mcsm", "video-text", key + ".json")

    def load_project(self) -> dict:
        try:
            with open(self.project_file(), encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data
        except (OSError, ValueError):
            pass
        return {"video": self.video, "match": 0, "items": [
            {"id": 1, "name": "Title", "kind": "world" if self.vinfo["matches"] else "screen",
             "lines": [["Welcome to", 3.0], ["Our First Expedition", 5.5]], "start": None, "until": None,
             "animation": "pop"}]}

    def save_project(self, body: dict) -> dict:
        data = {"video": self.video, "match": int(body.get("match", 0)), "items": list(body.get("items") or [])}
        p = self.project_file()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(p + ".tmp", p)
        return {"saved": True}

    def api_post(self, path: str, body: dict):
        if path == "/api/camera":
            return self.camera_path(int(body.get("match", 0)))
        if path == "/api/project":
            return self.save_project(body)
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
        items = list(b.get("items") or [])
        self.save_project(b)
        if b.get("target") == "world":
            match = self.vinfo["matches"][int(b.get("match", 0))]
            r = vt.make_items_in_world(match, items, self.video, progress=say)
            say(("Replaced" if r.get("replaced") else "Made") + f" '{r['name']}' in {r['flashback']}")
            return {"replay": r["replay"], "name": r["name"], "replaced": r.get("replaced", False)}
        m = self.vinfo["matches"][int(b.get("match", 0))] if self.vinfo["matches"] else {}
        default_start = (m.get("still_from") or 0.0) + 0.2
        out = vt.make_items_on_screen(self.video, items, self.vinfo["duration"], default_start, progress=say)
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
