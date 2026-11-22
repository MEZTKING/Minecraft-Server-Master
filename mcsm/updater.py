"""Checking for and installing new versions of the Windows app.

Two channels, both public downloads from GitHub (nothing personal is sent; the request carries a
plain ``mcsm/<version>`` user agent):

* **test** -- every successful build of the working branch: the *Build installer* workflow uploads the
  installer and ``build.json`` (version, build number, commit, SHA-256, recent changes) to the
  pre-release ``latest-build``.
* **stable** -- published GitHub Releases (``vX.Y.Z``); the installer's SHA-256 is GitHub's own
  asset digest.

The app checks at startup at most once a day (setting ``auto_check``). Updating downloads the
installer to a temporary folder, verifies its size and SHA-256, then runs it silently with
``/relaunch=1`` (the installer reopens the app) after the app has closed. Nothing is installed
without the user's click.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

from . import BUILD, __version__

REPO = "MEZTKING/Minecraft-Server-Master"
TEST_INFO_URL = f"https://github.com/{REPO}/releases/download/latest-build/build.json"
TEST_FILE_URL = f"https://github.com/{REPO}/releases/download/latest-build/{{file}}"
STABLE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
CHANNELS = ("test", "stable", "off")
CHECK_EVERY = 20 * 3600


def settings_path() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "mcsm", "settings.json")


def load_settings() -> dict:
    try:
        with open(settings_path(), encoding="utf-8") as fh:
            s = json.load(fh)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(s: dict) -> None:
    p = settings_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=1)
    os.replace(tmp, p)


def channel(settings: dict | None = None) -> str:
    c = (settings if settings is not None else load_settings()).get("update_channel", "test")
    return c if c in CHANNELS else "test"


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def current() -> dict:
    """This app's version and build number (0 when running from source)."""
    try:
        number = int(BUILD.get("number") or 0)
    except ValueError:
        number = 0
    return {"version": __version__, "number": number, "installed": bool(BUILD)}


def is_newer(candidate: dict, cur: dict | None = None) -> bool:
    cur = cur or current()
    a = (_vtuple(candidate.get("version", "")), int(candidate.get("number") or 0))
    b = (_vtuple(cur["version"]), int(cur.get("number") or 0))
    return a > b


def _get(url: str, timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"mcsm/{__version__}",
                                               "Accept": "application/vnd.github+json, application/json, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def check(which: str | None = None, timeout: float = 15) -> dict | None:
    """The newest available version on the channel, or None. ``{version, number, url, sha256,
    size, notes, file, channel}``. Raises OSError/ValueError when GitHub can't be reached."""
    which = which or channel()
    if which == "off":
        return None
    if which == "test":
        info = json.loads(_get(TEST_INFO_URL, timeout))
        return {"channel": "test", "version": str(info.get("version", "")), "number": int(info.get("number") or 0),
                "url": TEST_FILE_URL.format(file=info["file"]), "sha256": str(info.get("sha256", "")).lower(),
                "size": int(info.get("size") or 0), "notes": str(info.get("notes", "")), "file": info["file"],
                "commit": str(info.get("commit", ""))}
    rel = json.loads(_get(STABLE_API, timeout))
    asset = next((a for a in rel.get("assets") or [] if str(a.get("name", "")).lower().endswith(".exe")), None)
    if not asset:
        return None
    digest = str(asset.get("digest") or "")
    return {"channel": "stable", "version": str(rel.get("tag_name", "")).lstrip("vV"), "number": 0,
            "url": asset["browser_download_url"], "sha256": digest.split(":", 1)[1].lower() if ":" in digest else "",
            "size": int(asset.get("size") or 0), "notes": str(rel.get("body") or ""), "file": asset["name"]}


def check_due(settings: dict | None = None) -> bool:
    s = settings if settings is not None else load_settings()
    if channel(s) == "off" or s.get("auto_check") is False:
        return False
    return time.time() - float(s.get("last_check") or 0) > CHECK_EVERY


def download(info: dict, progress=None, dest_dir: str | None = None) -> str:
    """Download the installer and verify it (size and SHA-256). Returns its path."""
    say = progress or (lambda pct: None)
    if not info.get("sha256"):
        raise ValueError("no SHA-256 published for this installer: not installed")
    dest_dir = dest_dir or tempfile.mkdtemp(prefix="mcsm-update-")
    path = os.path.join(dest_dir, os.path.basename(info.get("file") or "MinecraftServerMaster-Setup.exe"))
    req = urllib.request.Request(info["url"], headers={"User-Agent": f"mcsm/{__version__}"})
    h, got = hashlib.sha256(), 0
    with urllib.request.urlopen(req, timeout=60) as r, open(path + ".part", "wb") as out:
        total = int(r.headers.get("Content-Length") or info.get("size") or 0)
        last = -1
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
            got += len(chunk)
            pct = int(got * 100 / total) if total else 0
            if pct >= last + 5:
                last = pct
                say(pct)
    if info.get("size") and got != int(info["size"]):
        os.remove(path + ".part")
        raise ValueError(f"download incomplete ({got} of {info['size']} bytes)")
    if info.get("sha256") and h.hexdigest() != info["sha256"]:
        os.remove(path + ".part")
        raise ValueError("the downloaded installer doesn't match its published SHA-256: not installed")
    os.replace(path + ".part", path)
    return path


def run_installer(path: str) -> None:
    """Start the installer silently (it waits for the app to close, installs over it and reopens it)."""
    if sys.platform != "win32":
        raise OSError("automatic updates are only for the Windows app")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/relaunch=1"],
                     close_fds=True, creationflags=flags)
