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
from datetime import datetime, timezone
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


def export_folder(flashback_dir: str) -> str | None:
    """The folder this Flashback saves exported videos to (its export screen remembers the last one, in
    ``config/flashback/flashback.json``), if set and present."""
    cfg = os.path.join(os.path.dirname(os.path.abspath(flashback_dir)), "config", "flashback", "flashback.json")
    try:
        with open(cfg, encoding="utf-8") as fh:
            path = (json.load(fh).get("internalExport") or {}).get("defaultExportPath")
    except (OSError, ValueError, AttributeError):
        return None
    return path if isinstance(path, str) and os.path.isdir(path) else None


def export_folders() -> list[dict]:
    """Every Flashback's export folder: ``[{"path", "name", "mc"}]``, the most recently changed first."""
    out, seen = [], set()
    for f in flashback_folders():
        p = export_folder(f["path"])
        if p and os.path.normcase(p) not in seen:
            seen.add(os.path.normcase(p))
            out.append({"path": p, "name": f["name"], "mc": f["mc"]})
    out.sort(key=lambda e: -os.path.getmtime(e["path"]))
    return out


def flashback_folders() -> list[dict]:
    """Every Flashback data folder on this computer: the default ``.minecraft`` one and those in
    launcher instances (PrismLauncher, MultiMC, Modrinth App, CurseForge).
    ``[{path, name, launcher, mc}]``; *mc* is the instance's Minecraft version when known."""
    out, seen = [], set()

    def add(path, name, launcher, mc, installed=False):
        key = os.path.normcase(os.path.abspath(path))
        if (installed or os.path.isdir(path)) and key not in seen:
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
                # Flashback makes its folder only when it first records or lists replays, so an instance with
                # the mod installed counts too (its folder is made when the app first copies a recording in)
                if os.path.isdir(os.path.join(game, "flashback")) or _has_flashback_mod(game):
                    add(os.path.join(game, "flashback"), name, launcher, _instance_version(inst, name), installed=True)
                    break
    return out


def _has_flashback_mod(game: str) -> bool:
    """Whether this game folder's ``mods`` has Flashback itself (``Flashback-<ver>-for-MC<mc>.jar``)."""
    d = os.path.join(game, "mods")
    try:
        names = os.listdir(d) if os.path.isdir(d) else []
    except OSError:
        return False
    return any(n.lower().startswith("flashback-") and n.lower().endswith(".jar") for n in names)


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


def version_targets(versions) -> list[dict]:
    """For each Minecraft version (newest first), the Flashback its recordings' copies go into:
    ``{"mc", "name", "path"}`` (name/path None: there's no Flashback of that version on this PC)."""
    found = flashback_folders()
    out = []

    def key(v):
        return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.-]", v))
    for v in sorted({v for v in versions if v}, key=key, reverse=True):
        path = find_flashback_dir(None, v, exact=True)
        name = next((f["name"] for f in found if f["path"] == path), None) if path else None
        out.append({"mc": v, "name": name, "path": path})
    return out


def describe_targets(targets: list[dict]) -> str:
    """"26.1.2 -> Flashback 26.1.2; 1.21.5 -> no Flashback yet" for status lines."""
    return "; ".join(f"{t['mc']} \u2192 {t['name'] or 'no Flashback of that version on this PC'}" for t in targets)


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


def in_replays(recording: str | None, flashback_dir: str) -> bool:
    """Whether *recording* is in this Flashback's own ``replays`` folder (so Flashback lists it)."""
    if not recording:
        return False
    rep = os.path.normcase(os.path.abspath(os.path.join(flashback_dir, "replays")))
    return os.path.normcase(os.path.abspath(os.path.dirname(recording))) == rep


# --- the app's own copies, and the app's folder inside each Flashback -------------------------------------
# ``<flashback>/mcsm/registry.json`` lists every recording the app made (text, highlight and timelapse copies):
# the Flashback add-on only ever changes those, never your originals. (A mark inside the copy's edit can't be
# relied on: Flashback rewrites edits from memory and drops what it doesn't know.)

def mcsm_dir(flashback_dir: str) -> str:
    return os.path.join(flashback_dir, "mcsm")


def registry(flashback_dir: str) -> dict:
    try:
        with open(os.path.join(mcsm_dir(flashback_dir), "registry.json"), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) and isinstance(d.get("copies"), dict) else {"copies": {}}
    except (OSError, ValueError):
        return {"copies": {}}


def register_copy(copy_path: str, uuid: str, original_uuid: str | None, kind: str, name: str = "") -> None:
    """Note a copy the app made in its Flashback's registry (when *copy_path* is in a Flashback replays folder)."""
    folder = os.path.dirname(os.path.abspath(copy_path))
    if os.path.basename(folder).lower() != "replays" or not uuid:
        return
    fb = os.path.dirname(folder)
    reg = registry(fb)
    reg["copies"][uuid] = {"original": original_uuid, "kind": kind, "name": name, "file": os.path.basename(copy_path),
                           "made": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    d = mcsm_dir(fb)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "registry.json")
    with open(p + ".part", "w", encoding="utf-8") as fh:
        json.dump(reg, fh, indent=1)
    os.replace(p + ".part", p)


def unregister_copy(copy_path: str) -> None:
    """Take a copy the app removed again (e.g. a half-made one) off its Flashback's registry; an empty registry
    leaves no file behind."""
    folder = os.path.dirname(os.path.abspath(copy_path))
    if os.path.basename(folder).lower() != "replays":
        return
    fb = os.path.dirname(folder)
    p = os.path.join(mcsm_dir(fb), "registry.json")
    if not os.path.isfile(p):
        return
    reg = registry(fb)
    name = os.path.basename(copy_path)
    reg["copies"] = {u: c for u, c in reg["copies"].items() if c.get("file") != name}
    if reg["copies"]:
        with open(p + ".part", "w", encoding="utf-8") as fh:
            json.dump(reg, fh, indent=1)
        os.replace(p + ".part", p)
    else:
        os.remove(p)
        try:
            os.rmdir(mcsm_dir(fb))
        except OSError:
            pass


def is_app_copy(flashback_dir: str, uuid: str) -> bool:
    return uuid in registry(flashback_dir)["copies"]


_COPY_FILE = re.compile(r" \((text|highlights|mcsm|timelapse \d+ of \d+|timelapse)\)( \d+)?\.zip$", re.IGNORECASE)


def register_existing(flashback_dir: str) -> int:
    """Add copies the app made before the registry existed: recognised by the app's mark in their metadata or,
    as Flashback drops that mark when you rename a recording, by their file name ("… (text).zip",
    "… (highlights).zip", "… (timelapse 2 of 9).zip", "… (mcsm).zip"). Returns how many were added."""
    import zipfile
    reg = registry(flashback_dir)["copies"]
    d = os.path.join(flashback_dir, "replays")
    added = 0
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not name.lower().endswith(".zip"):
            continue
        try:
            with zipfile.ZipFile(os.path.join(d, name)) as z:
                meta = json.loads(z.read("metadata.json"))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            continue
        uid = meta.get("uuid")
        m = _COPY_FILE.search(name)
        if not uid or uid in reg or not (m or meta.get("original_uuid_mcsm") or meta.get("mcsm_world_text")):
            continue
        kind = (m.group(1).split(" ")[0].lower() if m else "copy")
        register_copy(os.path.join(d, name), uid, meta.get("original_uuid_mcsm"), kind, meta.get("name", ""))
        reg = registry(flashback_dir)["copies"]
        added += 1
    return added


def replay_ids(flashback_dir: str) -> set[str]:
    """The ids of the recordings in this Flashback's ``replays`` folder (what its list shows)."""
    import zipfile
    out = set()
    d = os.path.join(flashback_dir, "replays")
    for name in os.listdir(d) if os.path.isdir(d) else []:
        if not name.lower().endswith(".zip"):
            continue
        try:
            with zipfile.ZipFile(os.path.join(d, name)) as z:
                uid = json.loads(z.read("metadata.json")).get("uuid")
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            continue
        if uid:
            out.add(uid)
    return out


def put_back(recording: str, flashback_dir: str) -> str:
    """Copy *recording* (e.g. from the library) into this Flashback's ``replays`` folder, so Flashback lists it
    again and opens it with its edit (Flashback finds an edit by the recording's id). The original stays where
    it is. A file of the same name that is a different recording gets " (2)" etc. Returns the copy's path."""
    import shutil
    import zipfile
    if not recording or not os.path.isfile(recording):
        raise ValueError("the recording isn't on this PC any more")
    if in_replays(recording, flashback_dir):
        return recording
    with zipfile.ZipFile(recording) as z:
        uid = json.loads(z.read("metadata.json")).get("uuid")
    target = os.path.join(flashback_dir, "replays")
    os.makedirs(target, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(recording))
    dst, n = os.path.join(target, stem + ext), 2
    while os.path.exists(dst):
        try:
            with zipfile.ZipFile(dst) as z:
                if json.loads(z.read("metadata.json")).get("uuid") == uid:
                    return dst                                  # already there under this name
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            pass
        dst, n = os.path.join(target, f"{stem} ({n}){ext}"), n + 1
    shutil.copyfile(recording, dst + ".part")
    try:
        with zipfile.ZipFile(dst + ".part") as z:
            if z.testzip() is not None or json.loads(z.read("metadata.json")).get("uuid") != uid:
                raise ValueError("the copy didn't come out right")
    except BaseException:
        os.remove(dst + ".part")
        raise
    os.replace(dst + ".part", dst)
    return dst


def copy_name(meta: dict, src: str, suffix: str, title: str | None = None) -> str:
    """The name Flashback lists a copy by. A recording that never got a name ("Unnamed", "") is named after
    its file (the date it was recorded), so the copy can be found in Flashback's list; *title* (the scene's
    name) goes in front."""
    name = str(meta.get("name") or "").strip()
    if not name or name.lower().startswith("unnamed") or name == "Replay":
        name = os.path.splitext(os.path.basename(str(src).rstrip("/\\")))[0]
    if title and title.strip() and title.strip().lower() not in name.lower():
        name = f"{title.strip()} – {name}"
    return name + suffix


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
        meta["name"] = copy_name(meta, src, name_suffix)
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
    if new_uuid:
        kind = name_suffix.strip(" ()").split(" ")[0] or "copy"
        register_copy(dst, meta["uuid"], meta.get("original_uuid_mcsm"), kind, meta.get("name", ""))
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
                 export_range: tuple[int, int] | None = None, camera_keys: dict[int, dict] | None = None) -> dict:
    """Build an editor-state document.

    *timelapse*: ``[(replay_tick, output_tick), ...]`` with both strictly increasing.
    *camera*: from :func:`camera_keyframe`, placed at the first timelapse tick (or 0); or *camera_keys*
    ``{replay_tick: keyframe}`` for a moving camera. Neither: no camera track (set it in Flashback).
    """
    tracks = []
    start = timelapse[0][0] if timelapse else 0
    if camera_keys:
        tracks.append({"keyframeType": "CAMERA", "enabled": True,
                       "keyframesByTick": {str(t): k for t, k in sorted(camera_keys.items())}})
    elif camera:
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
