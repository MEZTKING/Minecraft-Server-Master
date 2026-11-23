"""What the Flashback add-on's panel shows for each recording: written by the app, read inside Flashback.

For every recording in a Flashback's ``replays`` folder that the loaded server knows (by its id; the app's copies
by their original's id, or the original's file name), the app writes ``<flashback>/mcsm/<recording id>.json``:

``{"version": 1, "replay": id, "name": ..., "copy": true/false, "events": [{"tick", "kind", "player", "text"}],
"moments": [{"tick", "end_tick", "title", "score"}]}``

Ticks count from the start of the recording (20 per second), like Flashback's timeline. The add-on lists the events
and moments; clicking one jumps there. Your recordings and edits aren't touched: only these files in the
app's own ``mcsm`` folder inside each Flashback.
"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timezone

from . import flashback as fbm
from . import models as m

KINDS = (m.EV_JOIN, m.EV_LEAVE, m.EV_CHAT, m.EV_DEATH, m.EV_ADVANCEMENT)
VERSION = 1


def _stem(name: str) -> str:
    """A recording's file name without the app's copy suffix: "2026-05-24--18-49-45 (text).zip" -> "2026-05-24--18-49-45"."""
    base = os.path.splitext(os.path.basename(name))[0]
    for _ in range(3):
        cut = fbm._COPY_FILE.search(base + ".zip")
        if not cut:
            break
        base = base[:cut.start()]
    return base.split(" (")[0] if " (" in base else base


def panel_data(data: m.ServerData, r: m.Replay, moments: list[dict] | None = None) -> dict:
    """Events and moments during recording *r*, in its ticks."""
    if r.start is None:
        return {"events": [], "moments": []}
    t0 = r.start
    t1 = r.end or t0
    events = []
    for e in data.events:
        if e.type in KINDS and t0 <= e.time <= t1:
            events.append({"tick": int(round((e.time - t0).total_seconds() * 20)), "kind": e.type,
                           "player": e.player or "", "text": (e.text or "")[:200]})
    ms0, ms1 = int(t0.timestamp() * 1000), int(t1.timestamp() * 1000)
    out_m = []
    for mo in moments or []:
        if mo["end"] < ms0 or mo["start"] > ms1:
            continue
        out_m.append({"tick": max(0, int((mo["start"] - ms0) / 50)), "end_tick": int((min(mo["end"], ms1) - ms0) / 50),
                      "title": mo.get("title", "")[:160], "score": mo.get("score", 0)})
    out_m.sort(key=lambda x: x["tick"])
    return {"events": events, "moments": out_m}


def write_all(data: m.ServerData, flashback_dirs: list[str] | None = None, say=print) -> dict:
    """Write the panel's file for every recording of *data* that is in a Flashback replays folder."""
    from . import moments as mo
    if flashback_dirs is None:
        flashback_dirs = [f["path"] for f in fbm.flashback_folders()]
    by_id = {str(r.raw_meta.get("uuid")): r for r in data.replays if r.raw_meta.get("uuid")}
    by_stem = {}
    for r in data.replays:
        by_stem.setdefault(_stem(r.path.replace("\\", "/").rstrip("/").split("/")[-1]), r)
    moments = mo.find_moments(data)
    out = {"written": 0, "unknown": 0}
    for fb in flashback_dirs:
        fbm.register_existing(fb)
        reg = fbm.registry(fb)["copies"]
        d = os.path.join(fb, "replays")
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not name.lower().endswith(".zip"):
                continue
            try:
                with zipfile.ZipFile(os.path.join(d, name)) as z:
                    meta = json.loads(z.read("metadata.json"))
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                continue
            uid = meta.get("uuid")
            if not uid:
                continue
            copy = reg.get(uid)
            r = by_id.get(uid) or (by_id.get(str(copy.get("original"))) if copy else None) or by_stem.get(_stem(name))
            if r is None:
                out["unknown"] += 1
                continue
            doc = {"version": VERSION, "replay": uid, "name": meta.get("name") or name, "file": name,
                   "copy": bool(copy), "made": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   **panel_data(data, r, moments)}
            p = os.path.join(fbm.mcsm_dir(fb), f"{uid}.json")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p + ".part", "w", encoding="utf-8") as fh:
                json.dump(doc, fh, separators=(",", ":"))
            os.replace(p + ".part", p)
            out["written"] += 1
    say(f"Flashback panel: events for {out['written']} recordings written"
        + (f" ({out['unknown']} recordings not part of the loaded server)" if out["unknown"] else ""))
    return out
