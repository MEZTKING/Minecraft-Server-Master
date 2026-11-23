"""Shots planned in the history viewer, rendered in Flashback.

A shot is a list of camera keyframes the owner places in the viewer (free camera, spectate or orbit): a real time,
the eye position, yaw, pitch. Flashback renders it at full game quality, so the app:

1. picks the recording to film from: one covering the whole shot (on the world clock, :mod:`mcsm.paths`), whose
   player was nearest the camera (their recording has the most around the camera);
2. turns each keyframe's real time into that recording's tick through its world clock (pauses included);
3. copies the recording into the Flashback of its Minecraft version (never the original), with a marker at the
   start, and writes the camera keyframes and the export range into the copy's editor state.

Flashback puts the camera's eye 1.62 blocks above a camera keyframe's position, so the keyframe is the eye minus
that (see ``worldtext.EYE_HEIGHT``).
"""

from __future__ import annotations

import math
import os

from . import flashback as fbm
from . import paths as paths_mod

EYE_HEIGHT = 1.62
INTERPOLATIONS = ("SMOOTH", "LINEAR", "HOLD")


def tick_at(meta: dict | None, offset: int | None, start_ms: int, t_ms: float) -> int:
    """The recording's tick at real time *t_ms*: through its world clock (*meta* ``clock`` breakpoints and the
    real-time *offset*), else from its start."""
    clock = (meta or {}).get("clock")
    if offset is None or not clock:
        return max(0, int(round((t_ms - start_ms) / 50)))
    gt = (t_ms - offset) / 50
    best = None
    for i, (t0, g0) in enumerate(c[:2] for c in clock):
        nxt = clock[i + 1][0] if i + 1 < len(clock) else None
        tick = t0 + (gt - g0)
        if tick >= t0 and (nxt is None or tick < nxt):
            best = tick
        elif tick < t0 and best is None:              # during a pause (the world went on): where it resumed
            best = t0
    if best is None:
        best = clock[-1][0] + (gt - clock[-1][1])
    return max(0, int(round(best)))


def span(meta: dict | None, offset: int | None, start_ms: int, duration_ms: int) -> tuple[int, int]:
    """A recording's real start and end (ms) on the world clock (else its estimated start)."""
    clock = (meta or {}).get("clock")
    if offset is None or not clock:
        return start_ms, start_ms + duration_ms
    ticks = int((meta or {}).get("ticks") or duration_ms // 50)
    return offset + paths_mod.game_time(clock, 0) * 50, offset + paths_mod.game_time(clock, ticks) * 50


def keyframe(key: dict, interpolation: str = "SMOOTH") -> dict:
    """A Flashback camera keyframe from a viewer keyframe ``{eye: [x, y, z], yaw, pitch}``."""
    x, y, z = (float(v) for v in key["eye"])
    k = fbm.camera_keyframe(x, y - EYE_HEIGHT, z, float(key["yaw"]), float(key["pitch"]))
    k["interpolation_type"] = interpolation if interpolation in INTERPOLATIONS else "SMOOTH"
    return k


def candidates(recordings: list[dict], keys: list[dict], near) -> list[dict]:
    """The recordings covering the whole shot, best first. *recordings*: ``{replay, meta, offset, start_ms,
    duration_ms, player}``; *near(player, t)*: that player's position at *t* or None."""
    t0, t1 = min(k["t"] for k in keys), max(k["t"] for k in keys)
    mid = keys[len(keys) // 2]
    out = []
    for rec in recordings:
        a, b = span(rec["meta"], rec["offset"], rec["start_ms"], rec["duration_ms"])
        if a > t0 or b < t1:
            continue
        pos = near(rec["player"], mid["t"])
        dist = math.dist(pos, mid["eye"]) if pos else float("inf")
        out.append({**rec, "distance": dist, "from_ms": a, "to_ms": b})
    out.sort(key=lambda r: (r["distance"], -(r["to_ms"] - r["from_ms"])))
    return out


def send(rec: dict, keys: list[dict], title: str, replay_file, flashback_dir, out_dir: str, pick_dir=None,
         interpolation: str = "SMOOTH") -> dict:
    """Copy *rec*'s recording with the shot's camera keyframes into Flashback. Returns where it went."""
    r = rec["replay"]
    ticks = {}
    for k in sorted(keys, key=lambda k: k["t"]):
        tick = tick_at(rec["meta"], rec["offset"], rec["start_ms"], k["t"])
        while tick in ticks:                             # two keyframes on one tick: the later one a tick later
            tick += 1
        ticks[tick] = keyframe(k, interpolation)
    first, last = min(ticks), max(ticks)
    fb, installed = fbm.export_target(r, pick_dir, flashback_dir, out_dir)
    target = os.path.join(fb, "replays")
    base = os.path.splitext(os.path.basename(r.path.rstrip("/")))[0]
    clean = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:40] or "shot"
    dst = os.path.join(target, f"{base} (shot) {clean}.zip")
    n = 2
    while os.path.exists(dst):
        dst = os.path.join(target, f"{base} (shot) {clean} {n}.zip")
        n += 1
    marker = fbm.FlashbackMarker(first, f"Shot: {title}"[:80], fbm.MARKER_COLOURS.get("highlight", 0x40C040))
    meta = fbm.write_replay_copy(replay_file(r), dst, [marker], " (shot)")
    state = fbm.editor_state(f"Shot: {title}"[:60], None, [], (first, last), camera_keys=ticks)
    try:
        state_file = fbm.install_editor_state(fb, meta["uuid"], state)
    except Exception:
        fbm.unregister_copy(dst)
        os.remove(dst)
        raise
    return {"file": dst, "editor_state": state_file, "flashback": fb, "installed": installed,
            "recording": r.path, "recorded_by": r.recorded_player, "ticks": [first, last],
            "seconds": round((last - first) / 20, 1), "keyframes": len(ticks), "mc": r.mc_version}
