"""Runs every parser and merges the results into one :class:`ServerData`.

Order matters:

1. ``server.properties`` -> where the world folder is.
2. ``usercache.json`` / stats / advancements / playerdata, keyed by UUID.
3. Advancement timestamps -> the server's UTC offset (:class:`LogClock`).
4. Logs -> events, then sessions.
5. Crash reports and replays -> more events.
6. Everything is merged per player (keyed by lower-case name).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, tzinfo

from . import logs, models as m, players, replays as replay_mod, world as world_mod
from .timeutil import UTC, LogClock


def analyze(root: str, tz: tzinfo | None = None, latest_log_date: date | None = None,
            extra_replays: list[str] | None = None) -> m.ServerData:
    """Parse the server at *root* (a directory; see :func:`mcsm.source.open_server`)."""
    data = m.ServerData(root=os.path.abspath(root), generated_at=datetime.now(UTC))

    # 1. server-level info
    props = world_mod.read_properties(os.path.join(root, "server.properties"))
    data.server_properties = props
    wdir = world_mod.world_dir(root, props)
    data.world = world_mod.read_level_dat(wdir)
    data.world["folder"] = os.path.relpath(wdir, root)
    data.world["dimensions"] = world_mod.region_summary(root, wdir) if os.path.isdir(wdir) else {}
    if os.path.isdir(wdir):
        data.world["size_bytes"], data.world["file_count"] = world_mod.dir_size(wdir)
    data.mods = world_mod.list_mods(root)
    if not os.path.isfile(os.path.join(root, "server.properties")):
        data.warnings.append("server.properties not found -- is this the right folder?")
    if not os.path.isdir(wdir):
        data.warnings.append(f"world folder '{data.world['folder']}' not found; stats/advancements unavailable")

    # 2. per-player files
    uuid_to_name = players.read_usercache(root)
    stats = players.read_stats(wdir)
    advs, offset_samples = players.read_advancements(wdir)
    pdata = players.read_playerdata(wdir)

    # 3. time zone
    clock = LogClock(explicit=tz, samples=offset_samples)
    data.timezone_info = clock.describe()
    if clock.source == "assumed-utc":
        data.warnings.append(data.timezone_info)

    # 4. logs
    data.events, data.log_files = logs.parse_logs(root, clock, uuid_to_name.values(), latest_log_date)
    if not data.log_files:
        data.warnings.append("no log files found in logs/")
    if any(f.date_source == "mtime" for f in data.log_files):
        data.warnings.append("latest.log was dated using its file modification time; "
                             "if that is wrong pass --latest-log-date YYYY-MM-DD")

    # 5. crash reports + replays
    for local, rel, desc in logs.iter_crash_reports(root):
        data.events.append(m.Event(clock.to_utc(local), m.EV_CRASH, None, desc, rel, None, {}))
    data.replays, warns = replay_mod.read_replays(root, uuid_to_name, extra_replays, clock.to_utc)
    data.warnings.extend(warns)
    for r in data.replays:
        if r.start is None:
            continue
        for mk in r.markers:
            data.events.append(m.Event(r.start + timedelta(milliseconds=mk["offset_ms"]), m.EV_REPLAY_MARKER,
                                       r.recorded_player, mk["name"], r.path, None,
                                       {"offset_ms": mk["offset_ms"]}))
    data.events.sort(key=lambda e: e.time)
    data.sessions = logs.build_sessions(data.events)

    # 6. merge per player
    _merge_players(data, root, uuid_to_name, stats, advs, pdata)
    return data


def _merge_players(data: m.ServerData, root: str, uuid_to_name: dict[str, str],
                   stats: dict, advs: dict, pdata: dict) -> None:
    def get(uuid: str | None, name: str | None) -> m.PlayerProfile:
        name = name or uuid_to_name.get(uuid or "", None) or (uuid or "unknown")
        key = name.lower()
        prof = data.players.get(key)
        if prof is None:
            prof = m.PlayerProfile(uuid=uuid, name=name)
            data.players[key] = prof
        if uuid and not prof.uuid:
            prof.uuid = uuid
        return prof

    for uuid in set(uuid_to_name) | set(stats) | set(advs) | set(pdata):
        prof = get(uuid, uuid_to_name.get(uuid))
        prof.stats = stats.get(uuid, {})
        prof.advancements = advs.get(uuid, {})
        prof.playerdata = pdata.get(uuid, {})

    def uuids_in(fname: str) -> set[str]:
        return {str(e.get("uuid", "")).lower() for e in world_mod.read_json_list(os.path.join(root, fname))}

    ops, white, banned = uuids_in("ops.json"), uuids_in("whitelist.json"), uuids_in("banned-players.json")

    for ev in data.events:
        if not ev.player:
            continue
        prof = get(None, ev.player)
        if ev.player not in prof.names_seen:
            prof.names_seen.append(ev.player)
        prof.first_seen = ev.time if prof.first_seen is None else min(prof.first_seen, ev.time)
        prof.last_seen = ev.time if prof.last_seen is None else max(prof.last_seen, ev.time)
        if ev.type == m.EV_CHAT:
            prof.chat_messages += 1
        elif ev.type == m.EV_DEATH:
            prof.deaths_logged += 1

    for s in data.sessions:
        prof = get(None, s.player)
        prof.session_count += 1
        prof.session_seconds += s.seconds

    for prof in data.players.values():
        u = (prof.uuid or "").lower()
        prof.is_op, prof.is_whitelisted, prof.is_banned = u in ops, u in white, u in banned
