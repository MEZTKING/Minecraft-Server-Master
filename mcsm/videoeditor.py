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
  POST /api/image           {"name": "logo.png", "data": base64}  keep a picture for the project
  GET  /image/<file>        a picture kept for the projects

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
        self.world_dir = world_dir
        self._ground: dict = {}                 # (root) -> GroundProbe
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
    IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

    def image_dir(self) -> str:
        return os.path.join(os.path.dirname(self.project_file()), "images")

    def file_for(self, path: str) -> str | None:
        if path == "/video":
            return self.video
        if path.startswith("/image/"):
            name = path[len("/image/"):]
            if name and name == os.path.basename(name) and os.path.splitext(name)[1].lower() in self.IMAGE_TYPES:
                full = os.path.join(self.image_dir(), name)
                return full if os.path.isfile(full) else None
        return None

    def add_image(self, body: dict) -> dict:
        """Keep a picture the page sent (copied into the projects folder; your file isn't touched).
        Same picture twice = one file. Returns its name and width/height."""
        import base64
        ext = os.path.splitext(str(body.get("name") or ""))[1].lower()
        if ext not in self.IMAGE_TYPES:
            raise ValueError("pictures can be PNG, JPG, GIF, WEBP or BMP")
        try:
            data = base64.b64decode(str(body.get("data") or ""), validate=True)
        except ValueError:
            raise ValueError("the picture didn't arrive in one piece")
        if not data:
            raise ValueError("empty picture")
        name = hashlib.sha1(data).hexdigest()[:16] + ext
        d = self.image_dir()
        os.makedirs(d, exist_ok=True)
        full = os.path.join(d, name)
        if not os.path.exists(full):
            with open(full + ".part", "wb") as fh:
                fh.write(data)
            os.replace(full + ".part", full)
        size = vt.video_size(full)
        return {"file": name, "width": size[0] if size else None, "height": size[1] if size else None}

    def api_get(self, path: str):
        if path == "/api/project":
            return self.load_project()
        if path == "/api/job":
            with self._lock:
                return dict(self.job, log=self.job["log"][-30:])
        return None

    # --- players (for texts that follow one) ----------------------------------------------------
    def players_map(self, index: int) -> dict:
        """{16-byte uuid: name} from the usercache of the library backup made after the recording."""
        import uuid as uuid_mod
        from . import players, worldpick as wp
        if not hasattr(self, "_players"):
            self._players = {}
        if index in self._players:
            return self._players[index]
        m = self.vinfo["matches"][index]
        out = {}
        world = self.world_dir or wp.world_of_recording(m.get("recording"))
        found = wp.backup_for(world, wp.recording_date(m.get("recording") or "")) if world else None
        if found:
            for u, name in players.read_usercache(found[0]).items():
                try:
                    out[uuid_mod.UUID(u).bytes] = name
                except ValueError:
                    pass
        self._players[index] = out
        return out

    def players_at(self, index: int, t: float) -> dict:
        from . import paths
        m = self.vinfo["matches"][index]
        if not m.get("recording"):
            raise ValueError("the recording of this edit wasn't found")
        clock = vt.scene_clock(vt.scene_for_match(m))
        tick = int(clock.replay_tick(min(t, clock.duration)))
        seen = paths.players_in_view(m["recording"], tick, self.players_map(index))
        return {"players": sorted(seen)}

    def track(self, index: int, name: str) -> dict:
        """Where *name* is through the video: ``[[video second, x, y, z], ...]`` (about every second)."""
        from . import paths, replays
        m = self.vinfo["matches"][index]
        if not m.get("recording"):
            return {"points": []}
        if not hasattr(self, "_tracks"):
            self._tracks = {}
        key = (index, name)
        if key in self._tracks:
            return {"points": self._tracks[key]}
        clock = vt.scene_clock(vt.scene_for_match(m))
        pmap = self.players_map(index)
        rec_player = replays._recorded_player(m["recording"].replace(os.sep, "/"))
        rec_name = next((n for u, n in pmap.items() if rec_player and str(__import__("uuid").UUID(bytes=u)) == rec_player),
                        rec_player)
        pts = []
        for tick, who, _dim, x, y, z, _yaw, _pitch in paths.decode_entities(m["recording"], pmap):
            if tick > clock.end + 40:
                break
            if tick < clock.start - 40:
                continue
            if who == name or (who is None and rec_name == name):
                v = clock.video_time(min(max(tick, clock.start), clock.end))
                if v is not None:
                    pts.append([round(v, 3), round(x, 3), round(y, 3), round(z, 3)])
        self._tracks[key] = pts
        return {"points": pts}

    def pick(self, index: int, t: float, sx: float, sy: float) -> dict:
        """The spot on the ground under (sx, sy) (shares of the picture) at video second *t*."""
        from . import worldpick as wp, worldtext as wt
        m = self.vinfo["matches"][index]
        world = self.world_dir or wp.world_of_recording(m.get("recording"))
        if not world:
            raise ValueError("the library world isn't known: open the editor from the app with your library set")
        found = wp.backup_for(world, wp.recording_date(m.get("recording") or ""))
        if not found:
            raise ValueError("no backup of this world in the library")
        root, wdir = found
        probe = self._ground.get(root) or wp.GroundProbe(root, wdir)
        self._ground[root] = probe
        sc = vt.scene_for_match(m)
        clock = vt.scene_clock(sc)
        tick = clock.replay_tick(min(t, clock.duration))
        cam = wt.camera_at(sc, tick)
        if cam is None:
            raise ValueError("this edit has no camera keyframes")
        pos, yaw, pitch = cam
        w, h = self.vinfo["width"], self.vinfo["height"]
        hit = wp.hit_ground(probe, pos, wp.ray(pos, yaw, pitch, vt.fov_at(sc, tick), w, h, sx * w, sy * h))
        if hit is None:
            return {"hit": None, "message": "That's sky (or outside the saved world): click on the ground."}
        dist = sum((a - b) ** 2 for a, b in zip(hit, pos)) ** 0.5
        return {"hit": [round(v, 2) for v in hit], "distance": round(dist, 1),
                "backup": os.path.basename(root)}

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
        if path == "/api/players":
            return self.players_at(int(body.get("match", 0)), float(body.get("t", 0)))
        if path == "/api/track":
            return self.track(int(body.get("match", 0)), str(body.get("name", "")))
        if path == "/api/pick":
            return self.pick(int(body.get("match", 0)), float(body["t"]), float(body["x"]), float(body["y"]))
        if path == "/api/make":
            return self._start(body)
        if path == "/api/image":
            return self.add_image(body)
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
            r = vt.make_items_in_world(match, items, self.video, progress=say,
                                       players=self.players_map(int(b.get("match", 0))))
            say(("Replaced" if r.get("replaced") else "Made") + f" '{r['name']}' in {r['flashback']}")
            return {"replay": r["replay"], "name": r["name"], "replaced": r.get("replaced", False)}
        m = self.vinfo["matches"][int(b.get("match", 0))] if self.vinfo["matches"] else {}
        out = vt.make_items_on_screen(self.video, items, self.vinfo["duration"],
                                      max(0.0, self.vinfo["duration"] - 4), progress=say,
                                      image_dir=self.image_dir(), still_from=m.get("still_from"))
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
