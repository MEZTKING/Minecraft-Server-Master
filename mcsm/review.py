"""The year in review: the server's year and each player's, as cards (see ``docs/HISTORY.md``).

Made from data the app already has (no recordings are opened):

* **sessions** (from the logs): hours, days, months, busiest hours, records. Missing log days lower these.
* **events** (from the logs): deaths and their causes, first joins.
* **statistics** saved with each backup (``ServerData.player_history[...]["stats"]``): travel, blocks mined and
  placed, mobs. Minecraft keeps lifetime totals, so a range's numbers are the newest backup at or before its
  end minus the newest at or before its start; nothing is interpolated and each card says which backups it
  spans. With no backup before the start, the baseline is zero only for a player who first joined within the
  range; otherwise the card is "partial". A total that went down (a reset) counts as 0 and is flagged.
* **advancements** (with their completion dates).

Hours, days and months are in one time zone (this PC's unless given), named in the result.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone, tzinfo

from . import models as m

CM_PER_KM = 100_000
TRAVEL = {                                   # statistic -> how the card names it
    "walk_one_cm": "walking", "sprint_one_cm": "sprinting", "crouch_one_cm": "sneaking",
    "swim_one_cm": "swimming", "walk_under_water_one_cm": "underwater", "walk_on_water_one_cm": "on water",
    "aviate_one_cm": "elytra", "fly_one_cm": "flying", "boat_one_cm": "by boat", "horse_one_cm": "on horses",
    "minecart_one_cm": "by minecart", "pig_one_cm": "on pigs", "strider_one_cm": "on striders",
    "happy_ghast_one_cm": "on happy ghasts", "nautilus_one_cm": "on nautili", "climb_one_cm": "climbing",
}                                            # (falling isn't travel: left out)


def local_tz(tz: tzinfo | None = None) -> tuple[tzinfo | None, str]:
    """The time zone for hours and days: *tz*, else None = this PC's (Python then applies summer time for each
    moment), and its name."""
    if tz is not None:
        return tz, str(tz)
    import time
    return None, " / ".join(dict.fromkeys(time.tzname)) or "this PC's time"


def year_range(year: int, tz: tzinfo | None = None) -> tuple[datetime, datetime]:
    """1 January to 1 January in *tz* (None: this PC's time zone), as UTC."""
    def at(y):
        d = datetime(y, 1, 1, tzinfo=tz) if tz is not None else datetime(y, 1, 1).astimezone()
        return d.astimezone(timezone.utc)
    return at(year), at(year + 1)


def _short(name: str) -> str:
    return name.split(":", 1)[-1].replace("_", " ")


def _local_date(ms: int, tz: tzinfo | None) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(tz).date().isoformat()


def death_cause(text: str, player: str | None) -> str:
    """'D_Human was slain by Zombie using [Sword]' -> 'slain by Zombie'."""
    t = text
    if player and t.startswith(player):
        t = t[len(player):]
    t = re.sub(r"\s+(using|with) \[.*$", "", t).strip()
    t = re.sub(r"^(was|got)\s+", "", t)
    return t or "died"


class _Names:
    """Every name a player used -> the name they have now (renamed players count once)."""

    def __init__(self, data: m.ServerData):
        self.to: dict[str, str] = {}
        for p in data.players.values():
            self.to[p.name.lower()] = p.name
            for n in p.names_seen:
                self.to[n.lower()] = p.name

    def __call__(self, name: str | None) -> str | None:
        return self.to.get(name.lower(), name) if name else None


def _sessions(data: m.ServerData, names: _Names, start: datetime, end: datetime) -> list[tuple[str, datetime, datetime]]:
    """(player, start, end) of every session overlapping the range, cut to it. A session with no end (the logs
    stop or the server crashed) ends at the next server start or the last log line."""
    starts = sorted(e.time for e in data.events if e.type in (m.EV_SERVER_START, m.EV_SERVER_STOP))
    last = max((e.time for e in data.events), default=end)
    out = []
    for s in data.sessions:
        if s.start is None:
            continue
        e = s.end
        if e is None:
            e = next((t for t in starts if t > s.start), last)
        a, b = max(s.start, start), min(e, end)
        if b > a:
            out.append((names(s.player), a, b))
    return out


def _split_local(a: datetime, b: datetime, tz: tzinfo | None):
    """Yield (local start, minutes) pieces of a span, cut at each hour."""
    t = a
    while t < b:
        loc = t.astimezone(tz)
        nxt = min(b, (loc.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).astimezone(timezone.utc))
        yield loc, (nxt - t).total_seconds() / 60
        t = nxt


def _snapshots(data: m.ServerData, name: str) -> list[dict]:
    hist = [h for h in data.player_history.get(name, []) if isinstance(h.get("time"), datetime) and h.get("stats")]
    return sorted(hist, key=lambda h: h["time"])


def _stats_delta(data: m.ServerData, name: str, start: datetime, end: datetime, first_join: datetime | None) -> dict:
    """The player's statistics gained within the range (see the module docstring)."""
    snaps = _snapshots(data, name)
    upto_end = [h for h in snaps if h["time"] <= end]
    if not upto_end:
        return {"available": False}
    last = upto_end[-1]
    before = [h for h in snaps if h["time"] <= start]
    partial = False
    if before:
        base = before[-1]
    elif first_join and first_join >= start:
        base = None                                      # new this year: everything counts
    else:                                                # played before the range, no backup then
        base, partial = upto_end[0], True
        if base is last:
            return {"available": False, "partial": True}
    out: dict = {"available": True, "partial": partial, "reset": False,
                 "from": base["time"].isoformat() if base else None, "to": last["time"].isoformat(),
                 "from_label": base.get("backup") if base else None, "to_label": last.get("backup")}
    for cat in ("custom", "mined", "used", "killed", "killed_by"):
        now = (last["stats"].get(cat) or {})
        was = (base["stats"].get(cat) or {}) if base else {}
        d = {}
        for k, v in now.items():
            diff = v - was.get(k, 0)
            if diff < 0:
                out["reset"] = True
                diff = 0
            if diff:
                d[k] = diff
        out[cat] = d
    return out


def _block_items() -> set[str]:
    """Names of items that are blocks (so "used" of them = placed), from the newest protocol table."""
    from . import protocol
    try:
        import json
        with open(protocol.BUNDLED, encoding="utf-8") as fh:
            tables = json.load(fh)
        t = tables[max(tables, key=int)]
        return {f"minecraft:{b[0]}" for b in t.get("blocks") or []} | {b[0] for b in t.get("blocks") or []}
    except (OSError, ValueError, KeyError):
        return set()


def _top(d: dict, n: int = 3) -> list[list]:
    return [[_short(k), v] for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:n]]


def projects(builds: list[dict], start_ms: int, end_ms: int, names) -> list[dict]:
    """Builds in the range grouped by place (overlapping areas in one dimension, any players, any days):
    ``{centre, area, dim, placed, players {name: placed}, first, last, sessions, top [[block, n], ...]}``,
    biggest first."""
    groups: list[dict] = []
    for b in sorted((b for b in builds if start_ms <= b["start"] < end_ms), key=lambda b: b["start"]):
        a = b["area"]
        for g in groups:
            ga = g["area"]
            if g["dim"] == b["dim"] and a[0] <= ga[2] + 8 and ga[0] <= a[2] + 8 and a[1] <= ga[3] + 8 and ga[1] <= a[3] + 8:
                g["area"] = [min(ga[0], a[0]), min(ga[1], a[1]), max(ga[2], a[2]), max(ga[3], a[3])]
                break
        else:
            g = {"dim": b["dim"], "area": list(a), "placed": 0, "players": Counter(), "first": b["start"],
                 "last": b["end"], "sessions": 0, "top": Counter(), "sx": 0.0, "sz": 0.0}
            groups.append(g)
        n = names(b["player"]) or b["player"]
        g["placed"] += b["placed"]
        g["players"][n] += b["placed"]
        g["last"] = max(g["last"], b["end"])
        g["sessions"] += 1
        g["sx"] += b["centre"][0] * b["placed"]
        g["sz"] += b["centre"][2] * b["placed"]
        for blk, k in b.get("top") or []:
            g["top"][blk] += k
    out = []
    for g in groups:
        out.append({"centre": [round(g["sx"] / g["placed"]), round(g["sz"] / g["placed"])], "area": g["area"],
                    "dim": g["dim"], "placed": g["placed"], "players": dict(g["players"].most_common()),
                    "first": g["first"], "last": g["last"], "sessions": g["sessions"],
                    "top": [[_short(k), v] for k, v in g["top"].most_common(3)]})
    out.sort(key=lambda g: -g["placed"])
    return out


def build(data: m.ServerData, start: datetime, end: datetime, tz: tzinfo | None = None,
          builds: list | None = None, incidents: list | None = None) -> dict:
    """The review of *start* to *end* (aware datetimes): ``{"range", "tz", "server": {...}, "players":
    {name: {...}}, "order": [names by hours]}``."""
    tz, tz_name = local_tz(tz)
    names = _Names(data)
    ignored = {n.lower() for n in (data.ignored or [])}
    sess = [s for s in _sessions(data, names, start, end) if s[0] and s[0].lower() not in ignored]
    # first join ever, per player (from every session, not only this range)
    first_ever: dict[str, datetime] = {}
    for s in data.sessions:
        n = names(s.player)
        if n and s.start and (n not in first_ever or s.start < first_ever[n]):
            first_ever[n] = s.start

    # ---- per player: minutes, days, months, hours
    minutes: Counter = Counter()
    days: dict[str, set] = defaultdict(set)
    months: dict[str, Counter] = defaultdict(Counter)
    hours: dict[str, Counter] = defaultdict(Counter)
    weekdays: dict[str, Counter] = defaultdict(Counter)
    heat: Counter = Counter()                            # (weekday, hour) -> player-minutes
    per_day: Counter = Counter()
    longest = None
    for n, a, b in sess:
        if longest is None or (b - a) > longest[2] - longest[1]:
            longest = (n, a, b)
        for loc, mins in _split_local(a, b, tz):
            minutes[n] += mins
            days[n].add(loc.date().isoformat())
            months[n][loc.strftime("%Y-%m")] += mins
            hours[n][loc.hour] += mins
            weekdays[n][loc.weekday()] += mins
            heat[(loc.weekday(), loc.hour)] += mins
            per_day[loc.date().isoformat()] += mins
    players = sorted(minutes, key=lambda n: -minutes[n])

    # ---- events in range
    in_range = [e for e in data.events if start <= e.time < end]
    deaths = [e for e in in_range if e.type == m.EV_DEATH and names(e.player) in minutes]
    causes = Counter(death_cause(e.text, e.player) for e in deaths)

    # ---- advancements (completion dates)
    adv_by: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    holders: Counter = Counter()
    for p in data.players.values():
        n = names(p.name)
        if not n or n.lower() in ignored:
            continue
        for aid, info in (p.advancements or {}).items():
            if not info.get("done") or "recipes/" in aid:
                continue
            holders[aid] += 1
            try:
                when = datetime.fromisoformat(str(info.get("completed")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if start <= when < end:
                adv_by[n].append((when, aid))
    first_to: dict[str, tuple[datetime, str]] = {}
    for n, lst in adv_by.items():
        for when, aid in lst:
            if aid not in first_to or when < first_to[aid][0]:
                first_to[aid] = (when, n)
    adv_shown = {aid for aid in first_to if not aid.split(":")[-1].startswith("recipes")}
    rarest = sorted(adv_shown, key=lambda a: (holders[a], first_to[a][0]))[:5]

    # ---- statistics between backups
    blocks = _block_items()
    stats = {n: _stats_delta(data, n, start, end, first_ever.get(n)) for n in players}

    def km(st, keys=None) -> float:
        c = st.get("custom") or {}
        return sum(v for k, v in c.items() if k in TRAVEL and (keys is None or k in keys)) / CM_PER_KM

    def placed(st) -> dict:
        return {k: v for k, v in (st.get("used") or {}).items() if k in blocks}

    def record(fn):
        best = max(((fn(st), n) for n, st in stats.items() if st.get("available")), default=(0, None))
        return {"player": best[1], "value": round(best[0], 1)} if best[1] and best[0] > 0 else None

    month_keys = []                                      # every month of the range (empty ones too)
    cur = start.astimezone(tz).replace(day=1, hour=12)
    while cur < end and len(month_keys) < 36:
        month_keys.append(cur.strftime("%Y-%m"))
        cur = (cur + timedelta(days=32)).replace(day=1)
    seen_before = {n for n, t in first_ever.items() if t < start}
    server = {
        "numbers": {"days": len(per_day), "players": len(players), "hours": round(sum(minutes.values()) / 60, 1),
                    "deaths": len(deaths), "advancements": sum(len(v) for v in adv_by.values()),
                    "chat_lines": sum(1 for e in in_range if e.type == m.EV_CHAT)},
        "months": [{"month": k, "players": sum(1 for n in players if months[n].get(k)),
                    "new": sum(1 for n in players if months[n].get(k) and n not in seen_before
                               and first_ever.get(n) and first_ever[n].astimezone(tz).strftime("%Y-%m") == k),
                    "hours": round(sum(months[n].get(k, 0) for n in players) / 60, 1)} for k in month_keys],
        "heatmap": [[round(heat.get((d, h), 0) / 60, 2) for h in range(24)] for d in range(7)],
        "busiest_day": max(per_day.items(), key=lambda kv: kv[1], default=(None, 0)),
        "deaths": {"top": causes.most_common(5),
                   "oddest": max((c for c, k in causes.items() if k == 1), key=len, default=None)},
        "records": {
            "longest_session": {"player": longest[0], "hours": round((longest[2] - longest[1]).total_seconds() / 3600, 1),
                                "date": longest[1].astimezone(tz).date().isoformat()} if longest else None,
            "furthest_km": record(km),
            "most_mined": record(lambda st: sum((st.get("mined") or {}).values())),
            "most_placed": record(lambda st: sum(placed(st).values())),
            "most_mobs": record(lambda st: sum((st.get("killed") or {}).values())),
        },
        "advancements": {"total": sum(len(v) for v in adv_by.values()),
                         "rarest": [{"id": a, "name": _short(a.split("/")[-1]), "holders": holders[a],
                                     "first": first_to[a][1], "date": first_to[a][0].astimezone(tz).date().isoformat()}
                                    for a in rarest]},
    }
    server["busiest_day"] = {"date": server["busiest_day"][0], "hours": round(server["busiest_day"][1] / 60, 1)}
    # builds from the server's Ledger log (who placed what, exactly), grouped by place into projects
    proj = projects(builds, int(start.timestamp() * 1000), int(end.timestamp() * 1000), names) if builds else []
    s_ms, e_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    hits = sorted((i for i in (incidents or []) if s_ms <= i["start"] < e_ms), key=lambda i: -i["blocks"])
    server["disasters"] = {"available": incidents is not None, "count": len(hits),
                           "blocks": sum(i["blocks"] for i in hits),
                           "worst": [{"kind": i["kind"], "cause": names(i["cause"]) if i["kind"] == "player" else i["cause"],
                                      "owners": [names(o) or o for o in i["owners"]], "blocks": i["blocks"],
                                      "date": _local_date(i["start"], tz),
                                      "at": [round(i["centre"][0]), round(i["centre"][2])]} for i in hits[:5]]}
    server["builds"] = {"available": builds is not None, "count": sum(p["sessions"] for p in proj),
                        "projects": [{**p, "first": _local_date(p["first"], tz), "last": _local_date(p["last"], tz)}
                                     for p in proj[:6]]}

    # ---- each player
    out_players = {}
    total = len(players)
    for rank, n in enumerate(players, 1):
        st = stats[n]
        mine = sorted(s for s in sess if s[0] == n)
        my_deaths = [e for e in deaths if names(e.player) == n]
        dd = Counter(e.time.astimezone(tz).date().isoformat() for e in my_deaths)
        play_stat = ((st.get("custom") or {}).get("play_time") or (st.get("custom") or {}).get("play_one_minute") or 0) / 20 / 3600
        log_h = minutes[n] / 60
        mined, plc = st.get("mined") or {}, placed(st)
        out_players[n] = {
            "headline": {"hours": round(log_h, 1), "days": len(days[n]), "rank": rank, "of": total,
                         "stats_hours": round(play_stat, 1) if st.get("available") else None,
                         "logs_incomplete": bool(st.get("available") and not st.get("partial") and play_stat
                                                 and abs(play_stat - log_h) > 0.15 * max(play_stat, log_h))},
            "months": [{"month": k, "hours": round(months[n].get(k, 0) / 60, 1)} for k in month_keys],
            "favourite_hour": max(hours[n], key=hours[n].get) if hours[n] else None,
            "favourite_weekday": max(weekdays[n], key=weekdays[n].get) if weekdays[n] else None,
            "firsts": {
                "join": mine[0][1].isoformat() if mine else None,
                "new_this_year": n not in seen_before,
                "advancement": (lambda x: {"name": _short(x[1].split("/")[-1]), "date": x[0].isoformat()} if x else None)(
                    min(adv_by.get(n, []), default=None)),
                "death": {"text": death_cause(my_deaths[0].text, my_deaths[0].player),
                          "date": my_deaths[0].time.isoformat()} if my_deaths else None,
            },
            "travel": {"available": bool(st.get("available")), "km": round(km(st), 1) if st.get("available") else None,
                       "by": sorted(([TRAVEL[k], round(v / CM_PER_KM, 1)] for k, v in (st.get("custom") or {}).items()
                                     if k in TRAVEL and v >= CM_PER_KM / 10), key=lambda x: -x[1])},
            "blocks": {"mined": sum(mined.values()), "mined_top": _top(mined),
                       "placed": sum(plc.values()), "placed_top": _top(plc)},
            "mobs": {"killed": sum((st.get("killed") or {}).values()), "top": _top(st.get("killed") or {}),
                     "killed_by": _top(st.get("killed_by") or {}, 1)},
            "deaths": {"count": len(my_deaths),
                       "top_cause": Counter(death_cause(e.text, e.player) for e in my_deaths).most_common(1),
                       "worst_day": max(dd.items(), key=lambda kv: kv[1]) if dd else None},
            "advancements": {"count": len(adv_by.get(n, [])),
                             "rarest": (lambda a: {"name": _short(a.split("/")[-1]), "holders": holders[a]} if a else None)(
                                 min((aid for _w, aid in adv_by.get(n, [])), key=lambda a: holders[a], default=None))},
            "stats_span": {k: st.get(k) for k in ("available", "partial", "reset", "from", "to", "from_label", "to_label")},
            "builds": {"available": builds is not None,
                       "placed": sum(p["players"].get(n, 0) for p in proj),
                       "projects": [{"centre": p["centre"], "dim": p["dim"], "placed": p["players"][n],
                                     "total": p["placed"], "with": [o for o in p["players"] if o != n][:3],
                                     "first": _local_date(p["first"], tz), "last": _local_date(p["last"], tz),
                                     "top": p["top"]} for p in proj if n in p["players"]][:4]},
        }
    uuids = {}
    for p in data.players.values():
        if p.uuid and names(p.name) in out_players:
            uuids[names(p.name)] = p.uuid
    return {"range": [start.isoformat(), end.isoformat()], "tz": tz_name, "server": server,
            "players": out_players, "order": players, "uuids": uuids,
            "title": data.world.get("level_name") or "Minecraft server"}
