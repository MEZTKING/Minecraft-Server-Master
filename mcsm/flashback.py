"""Writing *into* Flashback: markers in a replay copy, and editor setups (camera + timelapse).

Everything here was checked against the Flashback source (github.com/Moulberry/Flashback,
master, Nov 2026); see docs/FLASHBACK.md for the details and sources.

Markers
-------
Flashback shows markers from the replay's ``metadata.json``::

    "markers": {"<tick>": {"colour": 0xRRGGBB, "description": "text"}}

(``position`` is optional and left out: Flashback compares its ``dimension`` to an
internal ResourceKey string we can't reproduce reliably.) Ticks are replay ticks,
20 per second from the start of the recording.

We **never modify the original replay**. :func:`write_replay_copy` writes a copy
with the extra markers and a **new replay uuid** -- Flashback keys its editor
state by that uuid, so the copy gets its own editor timeline and can't overwrite
edits made on the original.

Editor state (camera + timelapse)
---------------------------------
Flashback keeps the editor timeline *outside* the replay, in
``<.minecraft>/flashback/editor_states/<uuid>.json`` (plain JSON, Gson). It is
loaded when the replay is opened in Flashback; a file that fails to load is
deleted and replaced by an empty timeline (no crash). The minimal valid form,
with the values Flashback would otherwise leave unset written explicitly::

    {"scenes": [{"name": "...",
                 "keyframeTracks": [
                    {"keyframeType": "CAMERA", "enabled": true,
                     "keyframesByTick": {"<tick>": {"type": "camera", "position": [x, y, z],
                                                    "yaw": f, "pitch": f, "roll": 0.0,
                                                    "interpolation_type": "HOLD"}}},
                    {"keyframeType": "TIMELAPSE", "enabled": true,
                     "keyframesByTick": {"<replay tick>": {"type": "timelapse", "ticks": <output tick>}, ...}}],
                 "exportStartTicks": -1, "exportEndTicks": -1,
                 "history": {"entries": [], "position": 0}}],
     "sceneIndex": 0}

A TIMELAPSE track maps replay time to output time: between two keyframes the
replay plays at ``(replay ticks between them) / (output ticks between them)`` x
normal speed. Output ticks must strictly increase.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import uuid as uuid_mod
import zipfile
from dataclasses import dataclass

# Marker colours (0xRRGGBB) per event type -- same meaning as the video-marker colours.
MARKER_COLOURS = {
    "join": 0x55FF55, "leave": 0xAAAAAA, "chat": 0x5599FF, "server_chat": 0x55FFFF, "emote": 0x55FFFF,
    "death": 0xFF5555, "advancement": 0xFFFF55, "command": 0xAA55FF, "server_start": 0xFF55FF,
    "server_ready": 0xFF55FF, "server_stop": 0xFF55FF, "crash": 0xFF5599,
    "activity_start": 0x00E0A0, "activity_end": 0x008060,
}


def default_flashback_dirs() -> list[str]:
    """Where Flashback keeps its data (``<.minecraft>/flashback``) on this computer."""
    out = []
    if os.environ.get("MCSM_FLASHBACK_DIR"):
        out.append(os.environ["MCSM_FLASHBACK_DIR"])
    home = os.path.expanduser("~")
    if sys.platform.startswith("win"):
        out.append(os.path.join(os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming")), ".minecraft", "flashback"))
    elif sys.platform == "darwin":
        out.append(os.path.join(home, "Library", "Application Support", "minecraft", "flashback"))
    else:
        out.append(os.path.join(home, ".minecraft", "flashback"))
    return out


_VERSION_IN_NAME = re.compile(r"(?<![\d.])(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)(?![\d.])")


def _json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            v = json.load(fh)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _instance_version(inst: str, name: str) -> str | None:
    """Minecraft version of a launcher instance, from its own files (else from its name)."""
    pack = _json(os.path.join(inst, "mmc-pack.json"))                     # PrismLauncher / MultiMC
    for c in pack.get("components") or []:
        if isinstance(c, dict) and c.get("uid") == "net.minecraft" and c.get("version"):
            return str(c["version"])
    cf = _json(os.path.join(inst, "minecraftinstance.json"))              # CurseForge
    if cf.get("gameVersion"):
        return str(cf["gameVersion"])
    prof = _json(os.path.join(inst, "profile.json"))                      # older Modrinth App
    if isinstance(prof.get("metadata"), dict) and prof["metadata"].get("game_version"):
        return str(prof["metadata"]["game_version"])
    m = _VERSION_IN_NAME.search(name)
    return m.group(1) if m else None


def flashback_folders() -> list[dict]:
    """Every Flashback data folder on this computer: the default ``.minecraft`` one and those in
    launcher instances (PrismLauncher, MultiMC, Modrinth App, CurseForge).
    ``[{path, name, launcher, mc}]``; *mc* is the instance's Minecraft version when known."""
    out, seen = [], set()

    def add(path, name, launcher, mc):
        key = os.path.normcase(os.path.abspath(path))
        if os.path.isdir(path) and key not in seen:
            seen.add(key)
            out.append({"path": path, "name": name, "launcher": launcher, "mc": mc})

    for d in default_flashback_dirs():
        add(d, "default .minecraft", "minecraft", None)
    home = os.path.expanduser("~")
    appdata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
    roots = [(os.path.join(appdata, "PrismLauncher", "instances"), "PrismLauncher"),
             (os.path.join(appdata, "MultiMC", "instances"), "MultiMC"),
             (os.path.join(home, ".local", "share", "PrismLauncher", "instances"), "PrismLauncher"),
             (os.path.join(home, "Library", "Application Support", "PrismLauncher", "instances"), "PrismLauncher"),
             (os.path.join(appdata, "ModrinthApp", "profiles"), "Modrinth App"),
             (os.path.join(appdata, "com.modrinth.theseus", "profiles"), "Modrinth App"),
             (os.path.join(home, "curseforge", "minecraft", "Instances"), "CurseForge")]
    for root, launcher in roots:
        for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            inst = os.path.join(root, name)
            for game in (os.path.join(inst, "minecraft"), os.path.join(inst, ".minecraft"), inst):
                if os.path.isdir(os.path.join(game, "flashback")):
                    add(os.path.join(game, "flashback"), name, launcher, _instance_version(inst, name))
                    break
    return out


def _activity(path: str) -> float:
    """When this Flashback folder was last used (newest replay or editor state)."""
    newest = 0.0
    for sub in ("replays", "editor_states"):
        d = os.path.join(path, sub)
        for name in os.listdir(d) if os.path.isdir(d) else []:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(d, name)))
            except OSError:
                pass
    return newest


def find_flashback_dir(explicit: str | None = None, mc_version: str | None = None,
                       exact: bool = False) -> str | None:
    """The Flashback data folder (contains ``replays/``), or None if not found.

    *explicit* wins. Otherwise, with *mc_version* (a recording's Minecraft version), an instance
    of exactly that version is preferred, since Flashback only opens a recording in the version
    it was made with; then (unless *exact*) the most recently used Flashback folder.
    """
    if explicit:
        return explicit if os.path.isdir(explicit) else None
    if os.environ.get("MCSM_FLASHBACK_DIR") and os.path.isdir(os.environ["MCSM_FLASHBACK_DIR"]):
        return os.environ["MCSM_FLASHBACK_DIR"]
    found = flashback_folders()
    if not found:
        return None
    if mc_version:
        same = [f for f in found if f["mc"] == mc_version]
        if same:
            same.sort(key=lambda f: ("flashback" not in f["name"].lower(), -_activity(f["path"])))
            return same[0]["path"]
        if exact:
            return None
    return max(found, key=lambda f: _activity(f["path"]))["path"]


def export_target(r, pick_dir, flashback_dir: str | None, out_dir: str) -> tuple[str, bool]:
    """Where a copy of recording *r* goes: ``(Flashback data folder, installed)``. With *pick_dir* (the
    Flashback of the recording's own Minecraft version) a recording whose version has no Flashback on
    this PC goes into ``<out_dir>/Flashback for Minecraft <version>`` instead of a Flashback that
    couldn't open it (owner's first timelapse: 1.21.11 copies landed in 26.1.2)."""
    if pick_dir is not None:
        fb = pick_dir(r)
        if fb:
            return fb, True
        return os.path.join(out_dir, f"Flashback for Minecraft {r.mc_version or 'unknown version'}"), False
    if flashback_dir:
        return flashback_dir, True
    return os.path.join(out_dir, "flashback"), False


def find_editor_state(uuid: str) -> str | None:
    """The editor state (Flashback timeline/edits) of the replay *uuid*, in any Flashback folder."""
    for f in flashback_folders():
        p = os.path.join(f["path"], "editor_states", f"{uuid}.json")
        if os.path.isfile(p):
            return p
    return None


def tick_of(seconds_from_start: float) -> int:
    return int(round(seconds_from_start * 20))


@dataclass
class FlashbackMarker:
    tick: int
    description: str
    colour: int = 0xFF5555


def write_replay_copy(src: str, dst: str, markers: list[FlashbackMarker], name_suffix: str = " (mcsm)",
                      new_uuid: bool = True) -> dict:
    """Copy a Flashback replay ``.zip`` adding *markers*. Returns the new metadata.

    Existing markers are kept; a new marker whose tick is taken is moved to the next
    free tick. Markers outside ``0..total_ticks`` are dropped.
    """
    if os.path.abspath(src) == os.path.abspath(dst):
        raise ValueError("refusing to overwrite the original replay; choose another output file")
    with zipfile.ZipFile(src) as zin:
        meta = json.loads(zin.read("metadata.json").decode("utf-8"))
        total = int(meta.get("total_ticks") or 0) or sum(
            int((c or {}).get("duration", 0)) for c in (meta.get("chunks") or {}).values() if isinstance(c, dict))
        existing = dict(meta.get("markers") or {})
        taken = {int(k) for k in existing if str(k).lstrip("-").isdigit()}
        added = 0
        for mk in sorted(markers, key=lambda m: m.tick):
            t = max(0, mk.tick)
            if total and t > total:
                continue
            while t in taken:
                t += 1
            taken.add(t)
            existing[str(t)] = {"colour": int(mk.colour) & 0xFFFFFF, "description": mk.description[:200]}
            added += 1
        meta["markers"] = dict(sorted(existing.items(), key=lambda kv: int(kv[0])))
        if new_uuid:
            meta["original_uuid_mcsm"] = meta.get("uuid")
            meta["uuid"] = str(uuid_mod.uuid4())
        meta["name"] = (meta.get("name") or "Replay") + name_suffix
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        tmp = dst + ".part"
        with zipfile.ZipFile(tmp, "w") as zout:
            for info in zin.infolist():
                if info.filename == "metadata.json":
                    zout.writestr(info, json.dumps(meta, indent=2), compress_type=zipfile.ZIP_DEFLATED)
                    continue
                out_info = zipfile.ZipInfo(info.filename, info.date_time)
                out_info.compress_type = info.compress_type
                out_info.external_attr = info.external_attr
                with zin.open(info) as fin, zout.open(out_info, "w", force_zip64=True) as fout:
                    shutil.copyfileobj(fin, fout, 1 << 20)
        os.replace(tmp, dst)
    meta["_markers_added"] = added
    return meta


def copy_editor_state(flashback_dir: str, old_uuid: str | None, new_uuid: str) -> bool:
    """Copy the original replay's editor timeline (if any) to the copy's uuid."""
    if not (flashback_dir and old_uuid):
        return False
    src = os.path.join(flashback_dir, "editor_states", f"{old_uuid}.json")
    if not os.path.isfile(src):
        return False
    dst_dir = os.path.join(flashback_dir, "editor_states")
    shutil.copy2(src, os.path.join(dst_dir, f"{new_uuid}.json"))
    return True


def camera_keyframe(x: float, y: float, z: float, yaw: float, pitch: float) -> dict:
    return {"type": "camera", "position": [float(x), float(y), float(z)], "yaw": float(yaw),
            "pitch": float(pitch), "roll": 0.0, "interpolation_type": "HOLD"}


def editor_state(scene_name: str, camera: dict | None, timelapse: list[tuple[int, int]],
                 export_range: tuple[int, int] | None = None) -> dict:
    """Build an editor-state document.

    *timelapse*: ``[(replay_tick, output_tick), ...]`` with both strictly increasing.
    *camera*: from :func:`camera_keyframe`, placed at the first timelapse tick (or 0).
    """
    tracks = []
    start = timelapse[0][0] if timelapse else 0
    if camera:
        tracks.append({"keyframeType": "CAMERA", "enabled": True, "keyframesByTick": {str(start): camera}})
    if timelapse:
        last_out = -1
        last_in = -1
        for t_in, t_out in timelapse:
            if t_in <= last_in or t_out <= last_out:
                raise ValueError("timelapse keyframes must strictly increase in both replay and output time")
            last_in, last_out = t_in, t_out
        tracks.append({"keyframeType": "TIMELAPSE", "enabled": True,
                       "keyframesByTick": {str(t_in): {"type": "timelapse", "ticks": int(t_out)}
                                           for t_in, t_out in timelapse}})
    return {"scenes": [{"name": scene_name, "keyframeTracks": tracks,
                        "exportStartTicks": export_range[0] if export_range else -1,
                        "exportEndTicks": export_range[1] if export_range else -1,
                        "history": {"entries": [], "position": 0}}],
            "sceneIndex": 0}


def install_editor_state(flashback_dir: str, replay_uuid: str, state: dict) -> str:
    """Write an editor state for *replay_uuid*. Never overwrites an existing one."""
    folder = os.path.join(flashback_dir, "editor_states")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{replay_uuid}.json")
    if os.path.exists(path):
        raise FileExistsError(f"{path} already exists (refusing to overwrite an editor timeline)")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, separators=(",", ":"))
    return path
