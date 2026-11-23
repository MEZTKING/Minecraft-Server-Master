"""Versioned backups of your Flashback edits (``flashback/editor_states/<recording id>.json``).

An edit is hours of camera work, and Flashback rewrites the file as you edit. The app keeps versions of every
edit in ``%LOCALAPPDATA%/mcsm/edit-backups/<Flashback>/<recording id>/<time> <why>.json``:

- when the app starts (and when you press *Back up my Flashback edits*): every edit that changed since its last
  version;
- right before the app itself writes to an edit (camera keyframes, the song track), so anything the app does
  can be undone.

Only changed edits get a new version. The last :data:`KEEP_LAST` versions of each edit are kept, plus the last
version of every older day. :func:`restore` puts an earlier version back, after saving the current one first
(so a restore can be undone too); do it while that recording is closed in Flashback, as Flashback writes the edit
it has open back from memory.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import time

KEEP_LAST = 30


def backup_root() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mcsm", "edit-backups")


def _folder_key(flashback_dir: str) -> str:
    """A readable, unique folder name for one Flashback (its instance name + a short hash of its path)."""
    game = os.path.dirname(os.path.abspath(flashback_dir))
    inst = os.path.basename(os.path.dirname(game)) if os.path.basename(game).lower() in ("minecraft", ".minecraft") else os.path.basename(game)
    safe = re.sub(r"[^\w .()-]+", "_", inst).strip() or "Flashback"
    return f"{safe} {hashlib.sha1(os.path.normcase(os.path.abspath(flashback_dir)).encode()).hexdigest()[:6]}"


def versions_dir(state_path: str, root: str | None = None) -> str:
    fb = os.path.dirname(os.path.dirname(os.path.abspath(state_path)))
    uid = os.path.splitext(os.path.basename(state_path))[0]
    return os.path.join(root or backup_root(), _folder_key(fb), uid)


def _digest(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()


def versions(state_path: str, root: str | None = None) -> list[dict]:
    """Saved versions of one edit, newest first: ``{path, time (epoch s), why, keyframes, scenes}``."""
    out = []
    for p in glob.glob(os.path.join(versions_dir(state_path, root), "*.json")):
        name = os.path.splitext(os.path.basename(p))[0]
        m = re.match(r"(\d{8}-\d{6})(?:-(\d+))?(?: (.*))?$", name)
        if not m:
            continue
        t = time.mktime(time.strptime(m.group(1), "%Y%m%d-%H%M%S"))
        info = {"path": p, "time": t, "why": m.group(3) or "", "keyframes": None, "scenes": []}
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            sc = d.get("scenes") or []
            info["scenes"] = [s.get("name") or "Scene" for s in sc]
            info["keyframes"] = sum(len(t_.get("keyframesByTick") or {}) for s in sc for t_ in s.get("keyframeTracks") or [])
        except (OSError, ValueError, AttributeError):
            pass
        out.append(info)
    out.sort(key=lambda v: (v["time"], v["path"]), reverse=True)
    return out


def snapshot(state_path: str, why: str = "", root: str | None = None) -> str | None:
    """Save a version of the edit *state_path* if it changed since its last version. Returns the new file."""
    if not os.path.isfile(state_path):
        return None
    d = versions_dir(state_path, root)
    have = versions(state_path, root)
    try:
        if have and _digest(have[0]["path"]) == _digest(state_path):
            return None                                      # nothing changed
    except OSError:
        pass
    os.makedirs(d, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(os.path.getmtime(state_path)))
    clean = re.sub(r"[^\w ,.()-]+", "", why).strip()[:60]
    dst, n = os.path.join(d, f"{stamp}{' ' + clean if clean else ''}.json"), 2
    while os.path.exists(dst):
        dst, n = os.path.join(d, f"{stamp}-{n}{' ' + clean if clean else ''}.json"), n + 1
    shutil.copyfile(state_path, dst + ".part")
    os.replace(dst + ".part", dst)
    prune(state_path, root)
    return dst


def prune(state_path: str, root: str | None = None) -> int:
    """Keep the last :data:`KEEP_LAST` versions and, before those, the last version of each day."""
    vs = versions(state_path, root)
    keep = {v["path"] for v in vs[:KEEP_LAST]}
    seen_days = set()
    for v in vs[KEEP_LAST:]:
        day = time.strftime("%Y%m%d", time.localtime(v["time"]))
        if day not in seen_days:
            seen_days.add(day)
            keep.add(v["path"])
    gone = 0
    for v in vs:
        if v["path"] not in keep:
            try:
                os.remove(v["path"])
                gone += 1
            except OSError:
                pass
    return gone


def snapshot_all(flashback_dirs: list[str] | None = None, why: str = "", root: str | None = None) -> dict:
    """Save a version of every edit that changed, in every Flashback (default: all found on this PC)."""
    if flashback_dirs is None:
        from .flashback import flashback_folders
        flashback_dirs = [f["path"] for f in flashback_folders()]
    out = {"checked": 0, "saved": 0}
    for fb in flashback_dirs:
        for p in glob.glob(os.path.join(fb, "editor_states", "*.json")):
            out["checked"] += 1
            try:
                if snapshot(p, why, root):
                    out["saved"] += 1
            except OSError:
                continue
    return out


def restore(state_path: str, version_path: str, root: str | None = None) -> str:
    """Put an earlier version back as the edit (the current one is saved first). Returns the edit's path."""
    if os.path.abspath(os.path.dirname(version_path)) != os.path.abspath(versions_dir(state_path, root)):
        raise ValueError("that version belongs to another edit")
    with open(version_path, encoding="utf-8") as fh:
        json.load(fh)                                            # a readable edit
    snapshot(state_path, "before restoring", root)
    shutil.copyfile(version_path, state_path + ".part")
    os.replace(state_path + ".part", state_path)
    return state_path
