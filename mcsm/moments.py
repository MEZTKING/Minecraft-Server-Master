"""Find the important moments in a server's history, for picking what to edit.

Events from the logs (deaths, advancements, first joins, chat, markers placed in replays) that happen
close together are joined into **moments** (at most :data:`MAX_MS` long). Each moment gets a score from
the weights in :data:`WEIGHTS` (one place, so they're easy to tune), raised when more players were
online and when more players' recordings show it, and is listed best first. A moment no recording
shows is still listed but can't be exported. Picking a moment turns it into a highlight
(:mod:`mcsm.highlights`), which makes Flashback copies with markers and one ready scene.

Approach and weights advised by Fable (2026-11-22); its review then led to: a length cap, the killer
taken from the log parser (not a name search of the text), rarity among regular players only,
the app's own replay copies left out, no "first join" on the first day of the logs.
"""

from __future__ import annotations

import bisect
import hashlib
import re
from datetime import datetime

from . import models as m
from . import moods

WEIGHTS = {
    "death_by_player": 6.0,      # killed by another player
    "death": 3.0,                # any other death
    "advancement_max": 5.0,      # an advancement scores 1 / share of regular players who got it, at most this
    "first_join": 5.0,           # a player's first time on the server
    "chat": 0.3,                 # per message (and /me)
    "chat_max": 4.0,             # chat in one moment counts at most this
    "marker": 4.0,               # a marker someone placed in a replay on purpose
    "together": 0.5,             # x (1 + this per extra player online), ...
    "together_max": 3,           # ... counting at most this many extra players
    "per_view": 0.25,            # x (1 + this per extra player whose recording shows it), ...
    "views_max": 4,              # ... counting at most this many extra points of view
    "repeat": 0.5,               # the same player's same kind again in one moment counts this much less each time
    "work": 4.0,                 # building/exploring stretches: this x log2(1 + hours), so 3 hours ~ 8
}
WORK_KINDS = ("building", "map_art", "exploring", "searching", "journey")
WORK_LABEL = {"building": "building", "map_art": "map art / flattening", "exploring": "exploring",
              "searching": "searching", "journey": "a journey"}
GAP_MS = 3 * 60 * 1000           # events closer than this belong to one moment ...
MAX_MS = 10 * 60 * 1000          # ... as long as the moment stays this short
PAD_BEFORE_MS = 30 * 1000
PAD_AFTER_MS = 15 * 1000
REGULAR_S = 3600                 # players with at least this much playtime count for advancement rarity
APP_COPIES = ("(highlights)", "(text)", "(timelapse")   # replay copies the app made: not more recordings, not more markers
KINDS = ("death", "advancement", "first_join", "chat", "marker")


def _ms(dt: datetime | None) -> int | None:
    return int(dt.timestamp() * 1000) if dt else None


def _advancement_name(e: m.Event) -> str:
    title = (e.data or {}).get("title")
    if title:
        return str(title).strip()
    found = re.search(r"\[(.+?)\]", e.text or "")
    return (found.group(1) if found else e.text or "").strip()


class _Spans:
    """Intervals ``(start ms, end ms, item)`` with a quick "which overlap this span"."""

    def __init__(self, items: list[tuple[int, int, object]]):
        self.items = sorted(items, key=lambda x: x[0])
        self.starts = [x[0] for x in self.items]
        self.longest = max((b - a for a, b, _ in self.items), default=0)

    def overlapping(self, start: int, end: int) -> list:
        lo = bisect.bisect_left(self.starts, start - self.longest)
        hi = bisect.bisect_right(self.starts, end)
        return [x[2] for x in self.items[lo:hi] if x[1] >= start]


def find_moments(data: m.ServerData, weights: dict | None = None, tracks: dict | None = None) -> list[dict]:
    """The moments of *data*, best first: ``{id, start, end (ms, padded), score, title, kinds {kind: n},
    players, events [{t, kind, player, text, points}], online, recordings [{path, player}], why [text]}``.
    With *tracks* (players' positions from the recordings, see :mod:`mcsm.activity`) long stretches of
    building, map art and exploring are moments too (``mode``, ``area``, ``clip``: the best 3 minutes)."""
    w = {**WEIGHTS, **(weights or {})}
    names = {n.lower(): p.name for p in data.players.values() for n in p.names_seen + [p.name]}
    canon = lambda n: names.get((n or "").lower(), n)           # noqa: E731
    regulars = {p.name for p in data.players.values() if p.session_seconds >= REGULAR_S} \
        or {p.name for p in data.players.values()} or {"?"}

    # how rare each advancement is: share of the regular players who got it (from the logs)
    holders: dict[str, set] = {}
    for e in data.events:
        if e.type == m.EV_ADVANCEMENT and e.player:
            holders.setdefault(_advancement_name(e), set()).add(canon(e.player))

    # a first join only counts after the logs' first day (before that, everyone "joins for the first time")
    log_start = min((_ms(f.first_time) for f in data.log_files if f.first_time), default=None)
    first_join: dict[str, datetime] = {}
    for e in sorted((e for e in data.events if e.type == m.EV_JOIN and e.player), key=lambda e: e.time):
        first_join.setdefault(canon(e.player), e.time)

    scored = []
    for e in sorted(data.events, key=lambda e: e.time):
        t = _ms(e.time)
        who = canon(e.player)
        d = e.data or {}
        if e.type == m.EV_DEATH:
            killer = canon(d.get("killer")) if d.get("killer") else None
            by_player = bool(killer and killer != who and (d.get("killer") or "").lower() in names)
            kind, pts = "death", w["death_by_player"] if by_player else w["death"]
            if by_player:
                d = {**d, "killer_player": killer}
        elif e.type == m.EV_ADVANCEMENT:
            got = holders.get(_advancement_name(e), {who}) & regulars or {who}
            share = len(got) / max(1, len(regulars))
            kind, pts = "advancement", min(w["advancement_max"], 1.0 / max(share, 1e-6))
        elif (e.type == m.EV_JOIN and who and first_join.get(who) == e.time and not d.get("formerly")
              and (log_start is None or t - log_start > 24 * 3600 * 1000)):
            kind, pts = "first_join", w["first_join"]
        elif e.type in (m.EV_CHAT, m.EV_ME):
            kind, pts = "chat", w["chat"]
        elif e.type == m.EV_REPLAY_MARKER and not any(c in (e.source or "") for c in APP_COPIES):
            kind, pts = "marker", w["marker"]
        else:
            continue
        scored.append({"t": t, "kind": kind, "player": who, "text": (e.text or "")[:200], "points": round(pts, 2),
                       "killer": d.get("killer_player")})
        if kind == "chat":
            scored[-1]["chat_mood"] = moods.chat_mood(e.text or "")

    # celebrations and sad moments: tags on these events, plus farewells and anniversaries (mcsm.moods)
    scored += moods.tag_events(data, scored, canon, _advancement_name)
    scored.sort(key=lambda e: e["t"])

    # join events close together into moments, none longer than MAX_MS
    groups: list[list[dict]] = []
    for ev in scored:
        if groups and ev["t"] - groups[-1][-1]["t"] < GAP_MS and ev["t"] - groups[-1][0]["t"] < MAX_MS:
            groups[-1].append(ev)
        else:
            groups.append([ev])

    sessions = _Spans([(_ms(s.start), _ms(s.end) if s.end else (_ms(s.start) + 1), canon(s.player))
                       for s in data.sessions if s.start])
    if data.sessions:                                  # a session still open at the end of the logs: to the end
        last = max((_ms(e.time) for e in data.events), default=0)
        sessions = _Spans([(a, b if b > a + 1 else max(b, last), n) for a, b, n in sessions.items])
    replays = _Spans([(_ms(r.start), _ms(r.end or r.start), r) for r in data.replays
                      if r.format == "flashback" and not r.unfinished and r.start is not None
                      and not r.raw_meta.get("duration_note") and not any(c in r.path for c in APP_COPIES)])

    out = []
    for g in groups:
        chat = sum(e["points"] for e in g if e["kind"] == "chat")
        seen: dict[tuple, int] = {}                    # repeats add less and less: 1, 1/2, 1/4, ...
        base = min(w["chat_max"], chat)
        for e in g:
            if e["kind"] == "chat":
                continue
            k = (e["player"], e["kind"])
            base += e["points"] * w["repeat"] ** seen.get(k, 0)
            seen[k] = seen.get(k, 0) + 1
        if base <= 0:
            continue
        start, end = g[0]["t"] - PAD_BEFORE_MS, g[-1]["t"] + PAD_AFTER_MS
        players = sorted({e["player"] for e in g if e["player"]} | {e["killer"] for e in g if e.get("killer")})
        online = sorted(set(sessions.overlapping(start, end)))
        extra = len(set(online) - set(players))
        together = 1 + w["together"] * min(w["together_max"], extra)
        recs = replays.overlapping(start, end)
        views = {r.recorded_player or r.path for r in recs}
        cover = 1 + w["per_view"] * min(w["views_max"], max(0, len(views) - 1))
        score = base * together * cover
        kinds: dict[str, int] = {}
        for e in g:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        why = [f"{k.replace('_', ' ')} x{n}" for k, n in kinds.items()]
        tags = sorted({t for e in g for t in e.get("tags", [])})
        if tags:
            why.append(", ".join(t.replace("_", " ") for t in tags))
        if extra:
            why.append(f"{extra} more player{'s' if extra > 1 else ''} online (x{together:g})")
        why.append(f"{len(recs)} recording{'s' if len(recs) != 1 else ''}"
                   + (f" from {len(views)} points of view (x{cover:g})" if cover > 1 else ""))
        out.append({"id": hashlib.sha1(f"{g[0]['t']}|{g[0]['kind']}|{g[0]['player']}".encode()).hexdigest()[:12],
                    "start": start, "end": end, "score": round(score, 1), "title": _title(g), "kinds": kinds,
                    "players": players, "events": [{k: v for k, v in e.items() if k not in ("killer", "_adv", "chat_mood", "label")} for e in g],
                    "mood": moods.group_mood(g), "tags": tags,
                    "online": online, "recordings": [{"path": r.path, "player": r.recorded_player,
                                                      "mc": r.mc_version} for r in recs],
                    "mc": _version(recs), "why": why})
    if tracks:
        out += _work_moments(data, tracks, w, sessions, replays, canon)
    out += moods.stat_moments(data, canon)              # records, firsts, binges between backups
    out.sort(key=lambda x: -x["score"])
    return out


def _work_moments(data: m.ServerData, tracks: dict, w: dict, sessions: "_Spans", replays: "_Spans", canon) -> list[dict]:
    """Building / map art / exploring stretches as moments; players working together in one area (or
    travelling at the same time) are one moment."""
    import math
    from . import activity
    stretches = activity.find_stretches({canon(n): p for n, p in tracks.items()})
    groups: list[list[dict]] = []
    for s in stretches:                                   # sorted by start
        family = "build" if s["kind"] in ("building", "map_art") else "travel"
        for g in groups:
            h = g[0]
            hf = "build" if h["kind"] in ("building", "map_art") else "travel"
            near = math.hypot(h["centre"][0] - s["centre"][0], h["centre"][2] - s["centre"][2]) < (96 if family == "build" else 512)
            if hf == family and h["dim"] == s["dim"] and s["start"] <= max(x["end"] for x in g) and near:
                g.append(s)
                break
        else:
            groups.append([s])
    out = []
    for g in groups:
        start, end = min(s["start"] for s in g), max(s["end"] for s in g)
        players = sorted({s["player"] for s in g})
        lead = max(g, key=lambda s: s["minutes"])
        kind = "map_art" if any(s["kind"] == "map_art" for s in g) else lead["kind"]
        hours = (end - start) / 3_600_000
        active_h = max(s["active_minutes"] for s in g) / 60    # time really working (AFK doesn't count)
        online = sorted(set(sessions.overlapping(start, end)))
        extra = 0                                         # over hours everyone is online at some point: count the workers
        together = 1 + w["together"] * min(w["together_max"], len(players) - 1)
        recs = replays.overlapping(start, end)
        views = {r.recorded_player or r.path for r in replays.overlapping(*lead["clip"])}   # who films the best part
        cover = 1 + w["per_view"] * min(w["views_max"], max(0, len(views) - 1))
        score = w["work"] * math.log2(1 + active_h) * together * cover
        area = [min(s["area"][0] for s in g), min(s["area"][1] for s in g), max(s["area"][2] for s in g),
                max(s["area"][3] for s in g)]
        length = f"{int(hours)} h {round((hours % 1) * 60)} min" if hours >= 1 else f"{round(hours * 60)} min"
        where = f"at {round(lead['centre'][0])}, {round(lead['centre'][2])}" if kind in ("building", "map_art") \
            else f"{sum(s['km'] for s in g):.1f} km"
        stats = _stats_note(data, players, start)
        why = [f"{WORK_LABEL[kind]} for {length}", f"{round(active_h * 60)} min of it active",
               f"{len(players)} player{'s' if len(players) > 1 else ''}"]
        if stats:
            why.append(stats)
        if extra:
            why.append(f"{extra} more online")
        why.append(f"{len(recs)} recording{'s' if len(recs) != 1 else ''}"
                   + (f" from {len(views)} points of view (x{cover:g})" if cover > 1 else ""))
        out.append({"id": hashlib.sha1(f"work|{lead['kind']}|{lead['player']}|{lead['start'] // 600_000}".encode()).hexdigest()[:12],
                    "start": start, "end": end, "score": round(score, 1),
                    "title": f"{', '.join(players[:3])}{' and others' if len(players) > 3 else ''}: "
                             f"{WORK_LABEL[kind]} {where} ({length})",
                    "kinds": {kind: len(g)}, "players": players, "events": [], "online": online,
                    "recordings": [{"path": r.path, "player": r.recorded_player, "mc": r.mc_version} for r in recs],
                    "mc": _version(recs), "why": why,
                    "mode": kind, "area": area, "dim": lead["dim"], "y": lead["centre"][1], "clip": lead["clip"]})
    return out


def _stats_note(data: m.ServerData, players: list[str], t_ms: int) -> str:
    """What the players' statistics say about the backup interval around *t_ms* (blocks placed, mined, flown)."""
    bits = []
    for name in players:
        hist = sorted((h for h in data.player_history.get(name, []) if hasattr(h.get("time"), "timestamp")),
                      key=lambda h: h["time"])
        after = next((i for i, h in enumerate(hist) if _ms(h["time"]) >= t_ms), None)
        if not after:                                    # no backup before and after it
            continue
        a, b = hist[after - 1], hist[after]
        used = (b.get("used") or 0) - (a.get("used") or 0)
        mined = (b.get("blocks_mined") or 0) - (a.get("blocks_mined") or 0)
        fly = ((b.get("fly_cm") or 0) - (a.get("fly_cm") or 0)) / 100_000
        top = ""
        before = a.get("used_by_item") or {}
        if b.get("used_by_item") and before:
            # only items listed in both backups (the lists keep each player's 12 most used)
            diff = {k: v - before[k] for k, v in b["used_by_item"].items() if k in before and not _tool(k)}
            if diff:
                k = max(diff, key=diff.get)
                if diff[k] > 0.3 * max(1, used):
                    top = f", mostly {k.split(':')[-1].replace('_', ' ')}"
        parts = []
        if used > 1000:
            parts.append(f"items used {used / 1000:.0f}k{top}")
        if mined > 1000:
            parts.append(f"mined {mined / 1000:.0f}k")
        if fly > 5:
            parts.append(f"flew {fly:.0f} km")
        if parts:
            bits.append(f"{name} between backups: " + ", ".join(parts))
    return "; ".join(bits)


_TOOLS = ("pickaxe", "_axe", "shovel", "_hoe", "sword", "bow", "trident", "shears", "flint_and_steel",
          "fishing_rod", "firework_rocket", "elytra", "spyglass", "brush", "mace", "ender_pearl")


def _tool(item: str) -> bool:
    """Tools and weapons also count as "used": they don't say what was built."""
    return any(t in item for t in _TOOLS)


def _version(recs) -> str | None:
    """The Minecraft version most of the recordings were made in."""
    counts: dict[str, int] = {}
    for r in recs:
        if r.mc_version:
            counts[r.mc_version] = counts.get(r.mc_version, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _title(g: list[dict]) -> str:
    """A short title from the strongest event of a moment."""
    top = max((e for e in g if e["kind"] != "chat"), key=lambda e: e["points"], default=None)
    if top is not None and top.get("label"):
        return top["label"]
    if top is None:
        talkers = sorted({e["player"] for e in g if e["player"]})
        return f"Chat: {', '.join(talkers[:3])}{' and others' if len(talkers) > 3 else ''} ({len(g)} messages)"
    text = top["text"]
    if top["kind"] == "advancement":
        found = re.search(r"\[(.+?)\]", text)
        return f"{top['player']}: {found.group(1) if found else text}"
    if top["kind"] == "first_join":
        return f"{top['player']} joins for the first time"
    if top["kind"] == "marker":
        return f"Marker: {text or top['player'] or ''}".strip()
    return text or top["kind"]
