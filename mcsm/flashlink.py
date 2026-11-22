"""Talk to the companion mod ``mcsm-link`` inside a running Minecraft with Flashback.

The mod (``companion/`` in this repository; built into the app as ``assets/mod/mcsm-link-*.jar``)
listens on 127.0.0.1 and writes its port and a secret token to ``<instance>/minecraft/config/
mcsm-link.json``. The app reads that file, so only programs on this computer that can read your
Minecraft folder can drive the game. The mod moves the replay camera and time on request and sends
back the real picture the game draws (see ``McsmLink.java``).
"""

from __future__ import annotations

import glob
import http.client
import json
import os
import shutil

LINK_FILE = "mcsm-link.json"
MOD_PREFIX = "mcsm-link-"


def instances() -> list[dict]:
    """Minecraft instances that have Flashback: ``{name, mc, minecraft (folder), mods, link_file}``."""
    from .flashback import flashback_folders
    out = []
    for f in flashback_folders():
        mc_dir = os.path.dirname(f["path"])
        out.append({"name": f.get("name") or mc_dir, "mc": f.get("mc"), "minecraft": mc_dir,
                    "mods": os.path.join(mc_dir, "mods"),
                    "link_file": os.path.join(mc_dir, "config", LINK_FILE)})
    return out


class Link:
    """One running game with the mod."""

    def __init__(self, port: int, token: str, instance: dict | None = None):
        self.port, self.token, self.instance = int(port), str(token), instance or {}

    def request(self, method: str, path: str, body: dict | None = None, timeout: float = 12.0) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            data = json.dumps(body).encode() if body is not None else None
            conn.request(method, path, body=data, headers={"X-MCSM-Token": self.token,
                                                           "Host": f"127.0.0.1:{self.port}",
                                                           "Content-Type": "application/json"})
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def json(self, method: str, path: str, body: dict | None = None, timeout: float = 12.0) -> dict:
        code, data = self.request(method, path, body, timeout)
        try:
            out = json.loads(data.decode("utf-8") or "{}")
        except ValueError:
            out = {"error": data.decode("utf-8", "replace")[:200]}
        if code != 200 and "error" not in out:
            out["error"] = f"HTTP {code}"
        return out

    def status(self, timeout: float = 2.0) -> dict:
        return self.json("GET", "/status", timeout=timeout)

    def view(self, tick: int | None, pos=None, yaw=None, pitch=None, fov=None, roll=None) -> dict:
        body: dict = {"tick": tick}
        if pos is not None:
            body.update({"x": pos[0], "y": pos[1], "z": pos[2], "yaw": yaw or 0.0, "pitch": pitch or 0.0})
        if fov is not None:
            body["fov"] = fov
        if roll is not None:
            body["roll"] = roll
        return self.json("POST", "/view", body)

    def stream_url(self, width: int = 960, fps: int = 30) -> str:
        """A one-time link to the live picture (MJPEG) the page can show directly; the long-lived
        token never leaves the app."""
        t = self.json("POST", "/ticket", {})
        if not t.get("ticket"):
            raise RuntimeError(t.get("error") or "no ticket (is the link mod 0.2 or newer?)")
        return f"http://127.0.0.1:{self.port}/stream?k={t['ticket']}&w={int(width)}&fps={int(fps)}"

    def clock(self) -> dict:
        return self.json("GET", "/clock", timeout=3.0)

    def path(self, points: list[float]) -> dict:
        """The editor's camera path: flat ``[tick, x, y, z, yaw, pitch, fov, ...]`` (feet height)."""
        return self.json("POST", "/path", {"p": points})

    def play(self, tick: int) -> dict:
        return self.json("POST", "/play", {"tick": tick})

    def pause(self) -> dict:
        return self.json("POST", "/pause", {})

    def frame(self, width: int = 960) -> bytes:
        code, data = self.request("GET", f"/frame?width={int(width)}")
        if code != 200:
            raise RuntimeError(data.decode("utf-8", "replace")[:300])
        return data


def find_link(timeout: float = 0.6) -> tuple[Link | None, str]:
    """The running game with the mod, or ``(None, why not)``."""
    insts = instances()
    if not insts:
        return None, "no Minecraft instance with Flashback was found"
    seen = False
    for inst in insts:
        try:
            with open(inst["link_file"], encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            continue
        seen = True
        link = Link(cfg.get("port", 0), cfg.get("token", ""), inst)
        try:
            link.status(timeout=timeout)
            return link, ""
        except OSError:
            continue
    if not seen:
        return None, ("the companion mod isn't installed yet (Videos tab: 'Install the Flashback link…'), or "
                      "Minecraft hasn't been started with it")
    return None, "Minecraft with Flashback isn't running: start it and open the replay"


def bundled_mod() -> str | None:
    """The mod jar that came with the app (newest), if any."""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "mod")
    jars = sorted(glob.glob(os.path.join(here, MOD_PREFIX + "*.jar")))
    return jars[-1] if jars else None


def install_mod(mods_dir: str, jar: str | None = None) -> str:
    """Copy the mod into a Minecraft instance's ``mods`` folder (replacing an older copy of it only)."""
    jar = jar or bundled_mod()
    if not jar or not os.path.isfile(jar):
        raise ValueError("the companion mod isn't part of this build of the app")
    os.makedirs(mods_dir, exist_ok=True)
    for old in glob.glob(os.path.join(mods_dir, MOD_PREFIX + "*.jar")):
        if os.path.basename(old) != os.path.basename(jar):
            os.remove(old)
    dst = os.path.join(mods_dir, os.path.basename(jar))
    shutil.copyfile(jar, dst + ".part")
    os.replace(dst + ".part", dst)
    return dst
