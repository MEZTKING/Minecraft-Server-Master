"""Celebrations and sad moments (backlog 0x, designed with Fable 2026-11-23).

Three kinds of signal, all from data the app already has:

1. **Tags on log events** (:func:`tag_events`): a death or advancement keeps its kind, but gets tags and bonus points,
   so nothing is listed twice:
   - ``pet`` (a player's first tamed animal: Best Friends Forever; every wolf / cat kind; every animal bred),
   - ``boss`` (Free the End, Withering Heights, The End... Again...), ``server_first`` for the first time anyone on the
     server reached a milestone (the Nether, the End, diamonds, the sky limit...), ``challenge`` (purple advancements),
     ``together`` (3+ players got the same advancement within 30 minutes);
   - ``long_life`` (a death after 10+ hours of play since the player's last death), ``lossy`` (lava, the void, burning:
     the items are likely gone), ``death_streak`` (3+ deaths of one player in 10 minutes), ``rage_quit`` (left within a
     minute of dying, not back within 5 minutes).
2. **Derived moments** added to the log events: ``farewell`` (the end of a regular player's last session, if that was
   60+ days before the logs end), ``anniversary`` (the server's day 100, 365, every year; a player's 100th session and
   their 100th, 250th, 500th and 1000th hour of play).
3. **Between backups** (:func:`stat_moments`, from the statistics saved in each backup): ``record`` (a player's
   biggest stretch ever of mining, flying, mob kills, walking or crafting, per hour played), ``first`` (the first
   villager trade, enchantment, raid won, totem used, elytra flight, dragon, wither or diamond), ``binge`` (far more
   play per day than usual). These happened *somewhere between two backups*: shown as such, never as a moment in time.

Chat only lifts the mood of a moment that already has something in it (or 3+ messages from 2+ players within a
minute): whole words are matched (``gg``, ``lets go``, ``rip``, ``noo``...), repeated letters collapsed, plus signs any
language shares (``!!!``, laughter, ``:D``, ``:(``). No message on its own labels anyone.

Every moment gets ``mood``: ``"celebration"``, ``"sad"`` or ``None``.
"""

from __future__ import annotations

import bisect
import hashlib
import math
import re
from datetime import datetime, timedelta

from . import models as m

DAY_MS = 86_400_000
HOUR_S = 3600

BOSS = {"Free the End": "the Ender Dragon", "Withering Heights": "the Wither", "The End... Again...": "the Ender Dragon again"}
FIRSTS = {"We Need to Go Deeper": "the first trip to the Nether", "The End?": "the first trip to the End",
          "Diamonds!": "the first diamonds", "Sky's the Limit": "the first elytra", "Into Fire": "the first blaze rod",
          "Eye Spy": "the first stronghold", "A Terrible Fortress": "the first Nether fortress",
          "Hero of the Village": "the first raid won", "The Next Generation": "the first dragon egg",
          "Remote Getaway": "the first trip to the outer End islands", "Hidden in the Depths": "the first ancient debris"}
PETS = {"Best Friends Forever": "tames their first pet", "The Whole Pack": "has tamed every kind of wolf",
        "A Complete Catalogue": "has tamed every kind of cat", "Two by Two": "has bred every kind of animal"}
LOSSY = re.compile(r"lava|out of the world|burn|flames|void|fire|withered away", re.I)

POINTS = {"boss_first": 8.0, "boss": 5.0, "server_first": 5.0, "challenge": 2.0, "together": 4.0, "pet": 3.0,
          "long_life": 3.0, "long_life_max": 7.0, "lossy": 1.5, "death_streak": 3.0, "rage_quit": 2.0,
          "farewell": 5.0, "anniversary_server": 4.0, "anniversary_player": 3.0,
          "record": 3.0, "record_server": 5.0, "first": 3.0, "binge": 2.0, "mood_chat": 0.6, "cap": 8.0}

CHEER = {"gg", "ggs", "ggwp", "pog", "poggers", "yay", "yes", "nice", "wow", "lets go", "letsgo", "lfg", "woo", "woohoo",
         "hype", "les go", "lesgo", "congrats", "gz", "grats", "hurray", "hooray", "epic", "insane", "omg"}
MOURN = {"rip", "noo", "no", "f", "oof", "sad", "nooo", "why", "ugh", "damn", "dang", "rest in peace", "unlucky", "rip bozo"}
LAUGH = re.compile(r"\b(?:a?ha(?:ha)+h?|xd+|lo+l|lmao|lmfao|jaja+|kkk+|www+|ptdr|mdr)\b", re.I)
CELEBRATE_TAGS = {"boss", "server_first", "challenge", "together", "anniversary", "record", "first", "milestone", "pet"}
SAD_TAGS = {"long_life", "lossy", "death_streak", "rage_quit", "farewell"}


def _norm(text: str) -> str:
    """Lower case, repeated letters collapsed to two ("nooooo" -> "noo"), only letters/digits/spaces."""
    t = re.sub(r"(.)\1{2,}", r"\1\1", (text or "").lower())
    return " " + re.sub(r"[^\w ]+", " ", t).strip() + " "


def chat_mood(text: str) -> str | None:
    """``"cheer"``, ``"mourn"`` or None for one chat line (used only in bulk, never to label a person)."""
    t = _norm(text)
    raw = text or ""
    if any(f" {w} " in t for w in MOURN) or ":(" in raw or "T_T" in raw:
        return "mourn"
    if (any(f" {w} " in t for w in CHEER) or raw.count("!") >= 3 or LAUGH.search(raw) or ":D" in raw
            or (len(raw) >= 4 and raw.isupper() and any(c.isalpha() for c in raw))):
        return "cheer"
    return None


class _Played:
    """Seconds a player was online between two times (from the sessions)."""

    def __init__(self, data: m.ServerData, canon):
        self.by: dict[str, list[tuple[int, int]]] = {}
        for s in data.sessions:
            if s.start and s.end:
                self.by.setdefault(canon(s.player), []).append((_ms(s.start), _ms(s.end)))
        for v in self.by.values():
            v.sort()

    def seconds(self, who: str, t0: int, t1: int) -> float:
        return sum(max(0, min(b, t1) - max(a, t0)) for a, b in self.by.get(who, ())) / 1000


def _ms(dt: datetime | None) -> int | None:
    return int(dt.timestamp() * 1000) if dt else None


def tag_events(data: m.ServerData, scored: list[dict], canon, adv_name) -> list[dict]:
    """Add tags, bonus points and moods to the scored log events (in place) and return the derived events
    (farewells, anniversaries) to add before they're grouped into moments."""
    played = _Played(data, canon)
    cap = POINTS["cap"]

    def add(ev, tag, pts, mood):
        ev.setdefault("tags", []).append(tag)
        ev["points"] = round(min(cap, ev["points"] + pts), 2)
        ev["mood"] = ev.get("mood") or mood

    # -- advancements: bosses, server firsts, challenges, many players the same evening
    advs = [e for e in scored if e["kind"] == "advancement"]
    events_by_t = {(_ms(e.time), canon(e.player)): e for e in data.events if e.type == m.EV_ADVANCEMENT}
    seen_first: set[str] = set()
    seen_pet: set[tuple] = set()
    for ev in sorted(advs, key=lambda e: e["t"]):
        src = events_by_t.get((ev["t"], ev["player"]))
        name = adv_name(src) if src else ""
        kind = ((src.data or {}).get("kind") if src else "") or ""
        if name in BOSS:
            add(ev, "boss", POINTS["boss_first"] if name not in seen_first else POINTS["boss"] - ev["points"] / 2, "celebration")
            ev["label"] = f"{ev['player']} defeats {BOSS[name]}" + ("" if name not in seen_first else "")
        elif name in FIRSTS and name not in seen_first:
            add(ev, "server_first", POINTS["server_first"], "celebration")
            ev["label"] = f"{ev['player']}: {FIRSTS[name]} on the server"
        if name in PETS and (ev["player"], name) not in seen_pet:
            seen_pet.add((ev["player"], name))
            add(ev, "pet", POINTS["pet"], "celebration")
            ev["label"] = f"{ev['player']} {PETS[name]}"
        if kind == "challenge":
            add(ev, "challenge", POINTS["challenge"], "celebration")
        seen_first.add(name)
        ev["_adv"] = name
    by_name: dict[str, list[dict]] = {}
    for ev in advs:
        by_name.setdefault(ev.get("_adv", ""), []).append(ev)
    for name, evs in by_name.items():
        evs.sort(key=lambda e: e["t"])
        for i, ev in enumerate(evs):
            near = {e["player"] for e in evs if abs(e["t"] - ev["t"]) <= 30 * 60_000}
            if len(near) >= 3 and "together" not in ev.get("tags", []):
                for e in evs:
                    if abs(e["t"] - ev["t"]) <= 30 * 60_000 and "together" not in e.get("tags", []):
                        add(e, "together", POINTS["together"] / len(near), "celebration")

    # -- deaths: a long life lost, items likely lost, a run of deaths, leaving in a temper
    leaves = {}
    joins = {}
    for e in data.events:
        if e.type == m.EV_LEAVE and e.player:
            leaves.setdefault(canon(e.player), []).append(_ms(e.time))
        elif e.type == m.EV_JOIN and e.player:
            joins.setdefault(canon(e.player), []).append(_ms(e.time))
    for v in list(leaves.values()) + list(joins.values()):
        v.sort()
    log_start = min((_ms(f.first_time) for f in data.log_files if f.first_time), default=None)
    deaths: dict[str, list[dict]] = {}
    for ev in scored:
        if ev["kind"] == "death":
            deaths.setdefault(ev["player"], []).append(ev)
    cause_of = {(_ms(e.time), canon(e.player)): (e.data or {}).get("cause") or e.text
                for e in data.events if e.type == m.EV_DEATH}
    for who, evs in deaths.items():
        evs.sort(key=lambda e: e["t"])
        prev = log_start
        for i, ev in enumerate(evs):
            if prev is not None:
                hours = played.seconds(who, prev, ev["t"]) / HOUR_S
                if hours >= 10:
                    add(ev, "long_life", min(POINTS["long_life_max"], POINTS["long_life"] + math.log2(hours / 5)), "sad")
                    ev["label"] = f"{who} dies after {hours:.0f} hours alive"
            prev = ev["t"]
            if LOSSY.search(cause_of.get((ev["t"], who), "") or ""):
                add(ev, "lossy", POINTS["lossy"], "sad")
            if i >= 2 and ev["t"] - evs[i - 2]["t"] <= 10 * 60_000 and "death_streak" not in evs[i - 1].get("tags", []):
                add(ev, "death_streak", POINTS["death_streak"], "sad")
                ev["label"] = f"{who} dies again and again"
            lv = leaves.get(who, [])
            k = bisect.bisect_left(lv, ev["t"])
            if k < len(lv) and lv[k] - ev["t"] <= 60_000:
                jn = joins.get(who, [])
                j = bisect.bisect_right(jn, lv[k])
                if not (j < len(jn) and jn[j] - lv[k] <= 5 * 60_000):
                    add(ev, "rage_quit", POINTS["rage_quit"], "sad")

    # -- derived: farewells and anniversaries
    out = []
    logs_end = max((_ms(e.time) for e in data.events), default=None)
    if logs_end is not None:
        for p in data.players.values():
            ss = played.by.get(p.name) or []
            if not ss or sum(b - a for a, b in ss) < 5 * HOUR_S * 1000:
                continue
            last_end = ss[-1][1]
            if logs_end - last_end >= 60 * DAY_MS:
                out.append({"t": last_end, "kind": "farewell", "player": p.name, "points": POINTS["farewell"],
                            "text": f"{p.name}'s last time on the server", "tags": ["farewell"], "mood": "sad",
                            "label": f"{p.name}'s last time on the server"})
    if log_start is not None:
        first_day = datetime.fromtimestamp(log_start / 1000).astimezone().date()
        join_times = sorted(t for v in joins.values() for t in v)
        marks = [(100, "the server's 100th day")] + [(365 * y, f"the server's {_ordinal(y)} birthday") for y in range(1, 6)]
        for days, what in marks:
            day = first_day + timedelta(days=days)
            start = int(datetime(day.year, day.month, day.day).astimezone().timestamp() * 1000)
            k = bisect.bisect_left(join_times, start)
            if k < len(join_times) and join_times[k] < start + DAY_MS:
                out.append({"t": join_times[k], "kind": "anniversary", "player": None, "points": POINTS["anniversary_server"],
                            "text": what, "tags": ["anniversary"], "mood": "celebration", "label": what.capitalize()})
    for who, ss in played.by.items():
        if len(ss) >= 100:
            out.append({"t": ss[99][0], "kind": "anniversary", "player": who, "points": POINTS["anniversary_player"],
                        "text": f"{who}'s 100th time on the server", "tags": ["anniversary"], "mood": "celebration",
                        "label": f"{who}'s 100th time on the server"})
        total, goals = 0, [100, 250, 500, 1000]
        for a, b in ss:
            before = total
            total += (b - a) / 3_600_000
            for g in goals:
                if before < g <= total:
                    at = int(a + (g - before) * 3_600_000)
                    out.append({"t": at, "kind": "anniversary", "player": who, "points": POINTS["anniversary_player"],
                                "text": f"{who}'s {g}th hour on the server", "tags": ["milestone"], "mood": "celebration",
                                "label": f"{who} reaches {g} hours on the server"})
    return out


def _ordinal(n: int) -> str:
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}.get(n, f"{n}th")


def group_mood(group: list[dict]) -> str | None:
    """A moment's mood from its events' moods (by points) and, as a lift only, its chat."""
    score = {"celebration": 0.0, "sad": 0.0}
    for e in group:
        if e.get("mood") in score:
            score[e["mood"]] += e["points"]
    cheer = sum(1 for e in group if e.get("chat_mood") == "cheer")
    mourn = sum(1 for e in group if e.get("chat_mood") == "mourn")
    has_event = any(e["kind"] != "chat" for e in group)
    talkers = {e["player"] for e in group if e["kind"] == "chat"}
    lively = has_event or (len([e for e in group if e["kind"] == "chat"]) >= 3 and len(talkers) >= 2)
    if lively:
        score["celebration"] += POINTS["mood_chat"] * cheer
        score["sad"] += POINTS["mood_chat"] * mourn
    best = max(score, key=score.get)
    return best if score[best] >= 1.0 else None


# -- between backups: records, firsts, binges ------------------------------------------------------

RECORD_FIELDS = {"blocks_mined": ("blocks mined", 5000, 1), "fly_cm": ("km flown", 50_00000, 100_000),
                 "mob_kills": ("mobs killed", 500, 1), "walk_cm": ("km walked", 100_00000, 100_000),
                 "items_crafted": ("items crafted", 2000, 1)}
FIRST_FIELDS = {"traded": "first villager trade", "enchanted": "first enchantment", "raids_won": "first raid won",
                "totems": "first totem of undying used", "elytra": "first elytra flight", "dragons": "first dragon killed",
                "withers": "first wither killed", "diamonds": "first diamonds mined", "player_kills": "first player kill",
                "bred": "first animals bred", "fish": "first fish caught"}


def stat_moments(data: m.ServerData, canon) -> list[dict]:
    """Moments found in the statistics between consecutive backups (see the module docstring)."""
    hist: dict[str, list[dict]] = {}
    for name, rows in data.player_history.items():          # renamed players: one history
        for r in rows:
            if hasattr(r.get("time"), "timestamp"):
                hist.setdefault(canon(name), []).append(r)
    out = []
    server_best: dict[str, float] = {}
    candidates = []
    for who, rows in hist.items():
        rows.sort(key=lambda r: r["time"])
        best: dict[str, float] = {}
        rates = []
        for i in range(1, len(rows)):
            a, b = rows[i - 1], rows[i]
            hours = (b.get("play_ticks", 0) - a.get("play_ticks", 0)) / 72000
            days = max(1.0, (b["time"] - a["time"]).total_seconds() / 86400)
            t0, t1 = _ms(a["time"]), _ms(b["time"])
            if hours < 0:                                       # player data reset: skip this interval
                continue
            for f, (label, floor, unit) in RECORD_FIELDS.items():
                d = (b.get(f) or 0) - (a.get(f) or 0)
                if d < 0 or hours < 0.5:
                    continue
                rate = d / hours
                if i >= 4 and d >= floor and rate >= 1.5 * best.get(f, 0) > 0:
                    candidates.append((f, rate, who, t0, t1, f"{who}: most {label} ever between two backups "
                                       f"({d // unit:,} in {hours:.0f} h played)", b["backup"], a["backup"]))
                best[f] = max(best.get(f, 0), rate)
            for f, label in FIRST_FIELDS.items():
                if f in a and f in b and not (a.get(f) or 0) and (b.get(f) or 0) > 0:
                    out.append(_stat_moment(who, t0, t1, f"{who}: {label}", "first", POINTS["first"],
                                            a["backup"], b["backup"]))
            rates.append(hours / days)
            if len(rates) >= 4:
                med = sorted(rates[:-1])[len(rates[:-1]) // 2]
                if hours / days >= 3 * med and hours >= 10:
                    out.append(_stat_moment(who, t0, t1, f"{who} played {hours:.0f} hours ({hours / days:.1f} a day)",
                                            "binge", POINTS["binge"], a["backup"], b["backup"]))
    for f, rate, who, t0, t1, title, b_lab, a_lab in candidates:
        server_best[f] = max(server_best.get(f, 0), rate)
    for f, rate, who, t0, t1, title, b_lab, a_lab in candidates:
        top = rate >= server_best[f]
        out.append(_stat_moment(who, t0, t1, title + (" (the server's record)" if top else ""), "record",
                                POINTS["record_server"] if top else POINTS["record"], a_lab, b_lab))
    return out


def _stat_moment(who, t0, t1, title, tag, score, from_label, to_label) -> dict:
    return {"id": hashlib.sha1(f"stat|{tag}|{who}|{t1}|{title}".encode()).hexdigest()[:12],
            "start": t0, "end": t1, "score": round(score, 1), "title": title,
            "kinds": {"stats": 1}, "players": [who], "events": [], "online": [], "recordings": [], "mc": None,
            "why": [f"from the statistics saved in the backups {from_label} and {to_label}: it happened somewhere "
                    "between them"], "mode": "stats", "approx": True, "mood": "celebration", "tags": [tag]}
