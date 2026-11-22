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
  POST /api/music           {"name": "song.mp3", "data": base64}  keep a song for the project
  POST /api/beats           {"file": name}  detect its tempo and first beat
  GET  /music/<file>        a song kept for the projects (played along in the page)
  GET  /api/tiles           (edit without a video) the terrain regions around the camera path
  GET  /tile/<rx>/<rz>/<step>  one region's surface: int16 heights + RGB (as the history viewer)
  POST /api/tracks          {"match": i}  every player's path through the video

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

    def __init__(self, video: str | None, world_dir: str | None = None, info: dict | None = None,
                 edit: str | None = None, scene: int | None = None):
        """*video*: a finished video; or *edit* (a Flashback editor state) and *scene* for an edit
        that hasn't been exported: the page then shows the app's 3D map from the edit's camera."""
        self.video = video
        if video:
            self.vinfo = info or vt.analyse_video(video, world_dir)
        else:
            if not edit and not (info and info.get("edit")):
                raise ValueError("give a video or a Flashback edit")
            self.vinfo = info or vt.analyse_edit(edit, scene, world_dir)
        self.edit = self.vinfo.get("edit")
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
        return {"video": i["video"], "edit": i.get("edit"), "duration": i["duration"], "width": i["width"],
                "height": i["height"],
                "font": vt.minecraft_font(),
                "matches": [{"name": m["name"], "match": m["match"], "still_from": m.get("still_from"),
                             "recording": m.get("recording"), "uuid": m["uuid"]} for m in i["matches"]],
                "camera": self.camera_path(0) if i["matches"] else {"samples": [], "step": 0.1}}

    # --- web routes --------------------------------------------------------------------------
    IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

    def image_dir(self) -> str:
        return os.path.join(os.path.dirname(self.project_file()), "images")

    def music_dir(self) -> str:
        return os.path.join(os.path.dirname(self.project_file()), "music")

    def song_path(self, name: str) -> str:
        name = os.path.basename(str(name or ""))
        full = os.path.join(self.music_dir(), name)
        if not name or not os.path.isfile(full):
            raise ValueError("the song isn't there any more: choose it again")
        return full

    def add_music(self, body: dict) -> dict:
        """Keep a song the page sent (a copy; your file isn't touched). Returns its name and length."""
        import base64
        from . import videomusic as vm
        ext = os.path.splitext(str(body.get("name") or ""))[1].lower()
        if ext not in vm.SONG_TYPES:
            raise ValueError("songs can be MP3, OGG, WAV, FLAC, OPUS, M4A or AAC")
        try:
            data = base64.b64decode(str(body.get("data") or ""), validate=True)
        except ValueError:
            raise ValueError("the song didn't arrive in one piece")
        if not data:
            raise ValueError("empty song")
        name = hashlib.sha1(data).hexdigest()[:16] + ext
        d = self.music_dir()
        os.makedirs(d, exist_ok=True)
        full = os.path.join(d, name)
        if not os.path.exists(full):
            with open(full + ".part", "wb") as fh:
                fh.write(data)
            os.replace(full + ".part", full)
        return {"file": name, "duration": vt.video_duration(full)}

    def beats(self, body: dict) -> dict:
        from . import music as mus
        g = mus.detect_beats(self.song_path(body.get("file")))
        return {"bpm": g.bpm, "first_beat": g.first_beat, "confidence": g.confidence, "length": g.song_length}

    def music_for(self, music: dict | None) -> dict | None:
        """The project's song settings with the song's real path (None: no song)."""
        if not music or not music.get("file"):
            return None
        return {**music, "file": self.song_path(music["file"])}

    def copy_key(self) -> str:
        """What the app's Flashback copy is remembered by: the video, or the edit and scene."""
        if self.video:
            return self.video
        m = self.vinfo["matches"][0]
        return f"{self.edit}#scene{m['scene']}"

    # --- the 3D map for an edit without a video ----------------------------------------------------
    def _terrain_source(self):
        from . import worldpick as wp
        if hasattr(self, "_tsrc"):
            return self._tsrc
        m = self.vinfo["matches"][0] if self.vinfo["matches"] else {}
        world = self.world_dir or wp.world_of_recording(m.get("recording"))
        found = wp.backup_for(world, wp.recording_date(m.get("recording") or "")) if world else None
        self._tsrc = found
        return found

    def tiles(self) -> dict:
        """Regions near the camera path: close ones every 2 blocks, the rest every 4 (at most 49)."""
        from . import terrain
        found = self._terrain_source()
        if not found:
            return {"tiles": [], "message": "no library backup of this world: the map has no terrain"}
        root, wdir = found
        have = {}
        for p in terrain.region_files(root, wdir, "minecraft:overworld"):
            try:
                _r, rx, rz, _e = os.path.basename(p).split(".")
                have[(int(rx), int(rz))] = p
            except ValueError:
                continue
        near, around = set(), set()
        for x, _y, z, *_ in self.camera_path(0)["samples"][::10]:
            rx, rz = int(x) >> 9, int(z) >> 9
            near.add((rx, rz))
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    around.add((rx + dx, rz + dz))
        keys = sorted(near) + sorted(around - near)
        out = [{"rx": rx, "rz": rz, "step": 2 if (rx, rz) in near else 4} for rx, rz in keys if (rx, rz) in have][:49]
        self._regions = have
        return {"tiles": out, "backup": os.path.basename(root)}

    def tile_file(self, rx: int, rz: int, step: int) -> str | None:
        """One region's surface as a file (int16 heights, little-endian, then RGB), cached."""
        import array
        import sys
        from . import terrain
        if not hasattr(self, "_regions"):
            self.tiles()
        p = getattr(self, "_regions", {}).get((rx, rz))
        if not p or step not in (1, 2, 4, 8, 16):
            return None
        src = self._terrain_source()
        key = hashlib.sha1(f"{src[0]}|{rx}|{rz}|{step}".encode()).hexdigest()[:20]
        out = os.path.join(os.path.dirname(self.project_file()), "tiles", key + ".bin")
        if os.path.exists(out):
            return out
        t = terrain.read_region_cached(p, step, terrain.default_cache_dir())
        if t is None:
            return None
        heights = array.array("h", t.heights)
        if sys.byteorder != "little":
            heights.byteswap()
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out + ".part", "wb") as fh:
            fh.write(heights.tobytes() + bytes(t.colours))
        os.replace(out + ".part", out)
        return out

    def tracks_all(self, index: int) -> dict:
        """Every player's path through the video: ``{name: [[video second, x, y, z], ...]}``."""
        from . import paths, replays
        import uuid as uuid_mod
        m = self.vinfo["matches"][index]
        if not m.get("recording"):
            return {"players": {}}
        if not hasattr(self, "_all_tracks"):
            self._all_tracks = {}
        if index in self._all_tracks:
            return {"players": self._all_tracks[index]}
        clock = vt.scene_clock(vt.scene_for_match(m))
        pmap = self.players_map(index)
        rec_player = replays._recorded_player(m["recording"].replace(os.sep, "/"))
        rec_name = next((n for u, n in pmap.items() if rec_player and str(uuid_mod.UUID(bytes=u)) == rec_player),
                        rec_player) or "recorded player"
        out: dict[str, list] = {}
        for tick, who, _dim, x, y, z, _yaw, _pitch in paths.decode_entities(m["recording"], pmap):
            if tick > clock.end + 40:
                break
            if tick < clock.start - 40:
                continue
            v = clock.video_time(min(max(tick, clock.start), clock.end))
            if v is not None:
                out.setdefault(who or rec_name, []).append([round(v, 3), round(x, 2), round(y, 2), round(z, 2)])
        self._all_tracks[index] = out
        return {"players": out}

    def file_for(self, path: str) -> str | None:
        if path == "/video":
            return self.video
        if path.startswith("/tile/"):
            try:
                rx, rz, step = (int(v) for v in path[len("/tile/"):].split("/"))
            except ValueError:
                return None
            return self.tile_file(rx, rz, step)
        if path.startswith("/music/"):
            from . import videomusic as vm
            name = path[len("/music/"):]
            if name and name == os.path.basename(name) and os.path.splitext(name)[1].lower() in vm.SONG_TYPES:
                full = os.path.join(self.music_dir(), name)
                return full if os.path.isfile(full) else None
            return None
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
        if path == "/api/tiles":
            return self.tiles()
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
        key = hashlib.sha1(os.path.normcase(os.path.abspath(self.copy_key())).encode()).hexdigest()[:16]
        return os.path.join(base, "mcsm", "video-text", key + ".json")

    def load_project(self) -> dict:
        try:
            with open(self.project_file(), encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data
        except (OSError, ValueError):
            pass
        return {"video": self.copy_key(), "match": 0, "items": [
            {"id": 1, "name": "Title", "kind": "world" if self.vinfo["matches"] else "screen",
             "lines": [["Welcome to", 3.0], ["Our First Expedition", 5.5]], "start": None, "until": None,
             "animation": "pop"}]}

    def save_project(self, body: dict) -> dict:
        data = {"video": self.copy_key(), "match": int(body.get("match", 0)), "items": list(body.get("items") or []),
                "music": body.get("music") if isinstance(body.get("music"), dict) else None}
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
        if path == "/api/music":
            return self.add_music(body)
        if path == "/api/beats":
            return self.beats(body)
        if path == "/api/tracks":
            return self.tracks_all(int(body.get("match", 0)))
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
            music = self.music_for(b.get("music"))
            # an edit opened directly: its copy goes into the same Flashback folder
            fb = os.path.dirname(os.path.dirname(os.path.abspath(self.edit))) if self.edit else None
            r = vt.make_items_in_world(match, items, self.copy_key(), fb, progress=say,
                                       players=self.players_map(int(b.get("match", 0))),
                                       music=music, music_dir=self.music_dir())
            say(("Replaced" if r.get("replaced") else "Made") + f" '{r['name']}' in {r['flashback']}")
            return {"replay": r["replay"], "name": r["name"], "replaced": r.get("replaced", False),
                    "beats": r.get("beats", 0), "music_warning": r.get("music_warning", "")}
        if not self.video:
            raise ValueError("there's no exported video yet: put the texts in Flashback and export there")
        m = self.vinfo["matches"][int(b.get("match", 0))] if self.vinfo["matches"] else {}
        out = vt.make_items_on_screen(self.video, items, self.vinfo["duration"],
                                      max(0.0, self.vinfo["duration"] - 4), progress=say,
                                      image_dir=self.image_dir(), still_from=m.get("still_from"),
                                      music=(lambda mu: mu if mu and mu.get("on_video") else None)(
                                          self.music_for(b.get("music"))))
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
