"""Players' real skins, for the history viewer's player figures.

A skin is looked up by the player's UUID at Mojang's session server (the same place the game gets it from), then
the picture is downloaded from Mojang's texture server (``textures.minecraft.net``, nowhere else) and kept in
``%LOCALAPPDATA%/mcsm/skins/``. A kept skin is checked again after a week (players change skins); a player without
a skin (or when there's no internet) gets the game's default skins instead, and is asked about again after an hour.
Only UUIDs are sent; nothing else about the server or its players.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

PROFILE_URL = "https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"
TEXTURE_HOST = "textures.minecraft.net"
UA = {"User-Agent": "mcsm (Minecraft server history viewer)"}
REFRESH_S = 7 * 86400
RETRY_S = 3600
MAX_BYTES = 256 * 1024
_UUID = re.compile(r"^[0-9a-f]{32}$")
_lock = threading.Lock()
_failed: dict[str, float] = {}


def cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mcsm", "skins")


def clean_uuid(uuid: str | None) -> str | None:
    u = (uuid or "").replace("-", "").lower()
    return u if _UUID.match(u) else None


def _index_path(folder: str) -> str:
    return os.path.join(folder, "index.json")


def _load_index(folder: str) -> dict:
    try:
        with open(_index_path(folder), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_index(folder: str, index: dict) -> None:
    os.makedirs(folder, exist_ok=True)
    with open(_index_path(folder) + ".part", "w", encoding="utf-8") as fh:
        json.dump(index, fh)
    os.replace(_index_path(folder) + ".part", _index_path(folder))


def _get(url: str, limit: int) -> bytes | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10) as r:
            if r.status != 200:
                return None
            data = r.read(limit + 1)
            return data if len(data) <= limit else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def lookup(uuid: str, fetch=_get) -> dict | None:
    """``{"url", "slim"}`` of *uuid*'s skin at Mojang, or None (no such player, no skin, offline)."""
    raw = fetch(PROFILE_URL.format(uuid=uuid), 64 * 1024)
    if not raw:
        return None
    try:
        prof = json.loads(raw.decode("utf-8"))
        tex = next(p["value"] for p in prof.get("properties", []) if p.get("name") == "textures")
        skin = json.loads(base64.b64decode(tex).decode("utf-8"))["textures"]["SKIN"]
    except (ValueError, KeyError, StopIteration, TypeError):
        return None
    url = str(skin.get("url") or "")
    m = re.match(r"^https?://" + re.escape(TEXTURE_HOST) + r"/texture/([0-9a-f]+)$", url)
    if not m:
        return None                                    # only Mojang's own texture server
    return {"url": f"https://{TEXTURE_HOST}/texture/{m.group(1)}",
            "slim": ((skin.get("metadata") or {}).get("model") == "slim")}


def skin(uuid: str | None, folder: str | None = None, fetch=_get, now: float | None = None) -> dict | None:
    """The skin of the player *uuid*: ``{"file", "slim"}`` (downloaded if needed), or None (use a default skin)."""
    u = clean_uuid(uuid)
    if not u:
        return None
    folder = folder or cache_dir()
    now = time.time() if now is None else now
    with _lock:
        index = _load_index(folder)
        entry = index.get(u)
        file = os.path.join(folder, u + ".png")
        if entry and os.path.isfile(file) and now - entry.get("checked", 0) < REFRESH_S:
            return {"file": file, "slim": bool(entry.get("slim"))}
        if now - _failed.get(u, -1e18) < RETRY_S:
            return {"file": file, "slim": bool(entry.get("slim"))} if entry and os.path.isfile(file) else None
    info = lookup(u, fetch)
    data = fetch(info["url"], MAX_BYTES) if info else None
    with _lock:
        if not data or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            _failed[u] = now
            # keep using an older copy when Mojang can't be reached
            return {"file": file, "slim": bool(entry.get("slim"))} if entry and os.path.isfile(file) else None
        os.makedirs(folder, exist_ok=True)
        with open(file + ".part", "wb") as fh:
            fh.write(data)
        os.replace(file + ".part", file)
        index = _load_index(folder)
        index[u] = {"slim": info["slim"], "checked": now}
        _save_index(folder, index)
        return {"file": file, "slim": info["slim"]}


def skin_file(uuid: str, folder: str | None = None) -> str | None:
    """A skin already downloaded (for serving it), or None."""
    u = clean_uuid(uuid)
    if not u:
        return None
    f = os.path.join(folder or cache_dir(), u + ".png")
    return f if os.path.isfile(f) else None
