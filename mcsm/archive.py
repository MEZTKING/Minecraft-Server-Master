"""Merging several backups of a server into one history ("archive").

Typical input: PebbleHost backups taken over months, maybe across Minecraft versions
(e.g. 1.21 -> 26.1, which moved player files) and maybe world resets. Each backup is
analysed on its own with the normal parsers (which handle every supported layout), then:

* **Time zone** -- advancement timestamps from *all* backups teach the log clock, so a
  backup without advancement files still gets correct times.
* **Events** -- the same log archive is usually in several backups, and an old
  ``latest.log`` shows up again later as a dated ``.log.gz``. Events are de-duplicated by
  (time, type, player, text); sessions are rebuilt from the merged events.
* **Players** -- stats, advancements and inventory come from the newest backup that has
  them (stats are lifetime totals); every backup's numbers are kept in
  ``player_history`` so progress over time can be charted.
* **Replays** -- combined, de-duplicated by file name + start time. Paths are prefixed with
  the backup label (``ServerData.sources`` maps labels to extracted folders).
* **Backups** -- ``ServerData.backups`` lists each one: date, version, world, seed, size,
  counts. A different level name or seed marks a different world.

Backups are processed oldest-first, ordered by their newest log line / last-played time.
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from datetime import date, datetime, tzinfo
from typing import Callable

from . import logs, models as m, players as players_mod, replays as replay_mod, world as world_mod
from .analyzer import analyze
from .players import play_time_ticks
from .source import open_server
from .timeutil import UTC, LogClock


def _label(path: str, used: set[str]) -> str:
    base = os.path.basename(os.path.normpath(path))
    for ext in (".zip",):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
    base = base.replace("/", "_") or "backup"
    label, n = base, 2
    while label in used:
        label, n = f"{base} ({n})", n + 1
    used.add(label)
    return label


def _snapshot_time(d: m.ServerData) -> datetime | None:
    times = [f.last_time for f in d.log_files if f.last_time]
    lp = d.world.get("last_played_ms")
    if isinstance(lp, (int, float)) and lp > 0:
        times.append(datetime.fromtimestamp(lp / 1000, tz=UTC))
    return max(times) if times else None


def _offset_samples(root: str) -> list:
    props = world_mod.read_properties(os.path.join(root, "server.properties"))
    _advs, samples = players_mod.read_advancements(world_mod.world_dir(root, props))
    return samples


def analyze_many(paths: list[str], stack: ExitStack, tz: tzinfo | None = None,
                 latest_log_date: date | None = None, extra_replays: list[str] | None = None,
                 progress: Callable[[str], None] | None = None,
                 recordings: list[str] | None = None) -> m.ServerData:
    """Open (selectively), analyse and merge several backups. *stack* keeps them extracted.

    *recordings*: extra folders whose recordings belong to these backups (a library world's
    ``recordings`` folder, see :mod:`mcsm.library`).
    """
    say = progress or (lambda msg: None)
    used: set[str] = set()
    opened = []
    for p in paths:
        say(f"Opening {p} ...")
        root = stack.enter_context(open_server(p))
        opened.append((_label(p, used), p, root))

    # 1. one time-zone model for all backups
    samples = []
    for _label_, _p, root in opened:
        samples += _offset_samples(root)

    # 2. analyse each
    parts = []
    for label, p, root in opened:
        say(f"Analysing {label} ...")
        d = analyze(root, tz=tz, latest_log_date=latest_log_date, extra_offset_samples=samples)
        parts.append((label, p, root, d))
    parts.sort(key=lambda t: (_snapshot_time(t[3]) is None, _snapshot_time(t[3]) or datetime.min.replace(tzinfo=UTC)))
    if recordings:
        say("Reading recordings ...")
    return merge(parts, tz, samples, extra_replays, recordings)


_BUILTIN_PACKS = ("vanilla", "fabric", "bundle", "trade_rebalance", "minecart_improvements", "redstone_experiments")


def datapacks_of(world: dict) -> list[str]:
    """The world's own datapacks: enabled ``file/...`` packs plus the ``datapacks/`` folder.

    Built-in packs (vanilla, feature flags) and packs that mods provide are left out: those
    follow the Minecraft version and mod list, which are compared separately.
    """
    names = set()
    for p in world.get("datapacks_enabled") or []:
        p = str(p)
        if p.startswith("file/"):
            names.add(p[5:])
    for p in world.get("datapacks_folder") or []:
        names.add(str(p))
    return sorted(names, key=str.lower)


def _mod_key(x: dict) -> str:
    """Identify a mod across updates: its id, else its file name without the version part."""
    if x.get("id"):
        return x["id"].lower()
    import re
    base = (x.get("file") or x.get("name") or "").lower()
    return re.split(r"[-_+ ](?:v?\d)", base[:-4] if base.endswith(".jar") else base)[0]


def changes(backups: list[dict]) -> list[dict]:
    """What changed between consecutive backups (oldest first).

    Each entry: ``{time, after, before_label, label, kind, action, name, from, to}`` where
    *kind* is ``minecraft`` / ``mod`` / ``plugin`` / ``datapack`` / ``world`` and *action* is
    ``added`` / ``removed`` / ``updated`` / ``changed``. The change happened some time between
    the two backups' times (*after* .. *time*).
    """
    out = []
    for prev, cur in zip(backups, backups[1:]):
        base = {"time": cur.get("time"), "after": prev.get("time"),
                "before_label": prev.get("label"), "label": cur.get("label")}

        def add(kind, action, name, a="", b=""):
            out.append({**base, "kind": kind, "action": action, "name": name, "from": a, "to": b})
        if prev.get("version") and cur.get("version") and prev["version"] != cur["version"]:
            add("minecraft", "updated", "Minecraft", prev["version"], cur["version"])
        if prev.get("world_number") != cur.get("world_number"):
            add("world", "changed", cur.get("level_name") or "world", prev.get("level_name") or "", cur.get("level_name") or "")
        pm = {_mod_key(x): x for x in prev.get("mod_list") or []}
        cm = {_mod_key(x): x for x in cur.get("mod_list") or []}
        if prev.get("mod_list") is not None and cur.get("mod_list") is not None:
            for k in sorted(set(pm) | set(cm)):
                a, b = pm.get(k), cm.get(k)
                kind = (b or a).get("kind") or "mod"
                name = (b or a).get("name") or k
                if a and not b:
                    add(kind, "removed", name, a.get("version", ""), "")
                elif b and not a:
                    add(kind, "added", name, "", b.get("version", ""))
                elif (a.get("version") or a.get("file")) != (b.get("version") or b.get("file")):
                    add(kind, "updated", name, a.get("version") or a.get("file", ""), b.get("version") or b.get("file", ""))
        pd, cd = set(prev.get("datapacks") or []), set(cur.get("datapacks") or [])
        for n in sorted(cd - pd, key=str.lower):
            add("datapack", "added", n)
        for n in sorted(pd - cd, key=str.lower):
            add("datapack", "removed", n)
    return out


def merge(parts: list[tuple[str, str, str, m.ServerData]], tz=None, samples=None,
          extra_replays: list[str] | None = None, recordings: list[str] | None = None) -> m.ServerData:
    """Merge analysed backups (oldest first) into one ServerData."""
    newest = parts[-1][3]
    out = m.ServerData(root=newest.root, generated_at=datetime.now(UTC))
    out.server_properties = newest.server_properties
    out.world = dict(newest.world)
    out.mods = newest.mods
    out.timezone_info = LogClock(explicit=tz, samples=samples or []).describe()
    out.sources = {label: root for label, _p, root, _d in parts}

    seen_events = set()
    for label, _p, _root, d in parts:
        for ev in d.events:
            key = (ev.time, ev.type, ev.player, ev.text)
            if key in seen_events:
                continue
            seen_events.add(key)
            ev.source = f"{label}/{ev.source}" if ev.source else label
            out.events.append(ev)
    out.events.sort(key=lambda e: e.time)
    out.sessions = logs.build_sessions(out.events)

    seen_logs = set()
    for label, _p, _root, d in parts:
        for f in d.log_files:
            key = (os.path.basename(f.path), f.lines, f.first_time, f.last_time)
            if key in seen_logs:
                continue
            seen_logs.add(key)
            f.path = f"{label}/{f.path}"
            out.log_files.append(f)

    seen_replays = set()
    for label, _p, _root, d in parts:
        for r in d.replays:
            key = (os.path.basename(r.path.rstrip("/")), r.start, (r.recorded_player or "").lower())
            if key in seen_replays:
                continue
            seen_replays.add(key)
            if not os.path.isabs(r.path):
                r.path = f"{label}/{r.path}"
            out.replays.append(r)

    for label, _p, _root, d in parts:
        out.warnings += [f"[{label}] {w}" for w in d.warnings
                         if "time-zone" not in w or not samples]  # the merged clock replaces per-backup tz warnings
    if not samples and tz is None:
        out.warnings.insert(0, out.timezone_info)

    # Players: newest backup with data wins; keep per-backup history. The same uuid under a
    # different name in a newer backup is a rename: move the profile to the newer name.
    by_uuid: dict[str, str] = {}           # uuid -> key in out.players
    for label, _p, _root, d in parts:
        snap_time = _snapshot_time(d)
        for key, prof in d.players.items():
            u = (prof.uuid or "").lower()
            old_key = by_uuid.get(u) if u else None
            if old_key and old_key != key and old_key in out.players:
                moved = out.players.pop(old_key)
                moved.name = prof.name
                if key in out.players:     # already seen under the new name: keep that one
                    moved = out.players[key]
                out.players[key] = moved
            if u:
                by_uuid[u] = key
            target = out.players.get(key)
            if target is None:
                target = m.PlayerProfile(uuid=prof.uuid, name=prof.name)
                out.players[key] = target
            if prof.uuid and not target.uuid:
                target.uuid = prof.uuid
            if prof.stats:
                target.stats = prof.stats
            if prof.advancements:
                target.advancements = prof.advancements
            if prof.playerdata:
                target.playerdata = prof.playerdata
            target.is_op, target.is_whitelisted, target.is_banned = prof.is_op, prof.is_whitelisted, prof.is_banned
            if prof.stats:
                c = prof.stats.get("custom", {})
                out.player_history.setdefault(prof.name, []).append({
                    "backup": label, "time": snap_time,
                    "play_ticks": play_time_ticks(prof.stats), "deaths": c.get("deaths", 0),
                    "mob_kills": c.get("mob_kills", 0), "jumps": c.get("jump", 0),
                    "blocks_mined": sum(prof.stats.get("mined", {}).values()),
                    "items_crafted": sum(prof.stats.get("crafted", {}).values()),
                    "walk_cm": c.get("walk_one_cm", 0) + c.get("sprint_one_cm", 0),
                    "advancements": sum(1 for a in prof.advancements.values() if a.get("done")),
                })
    for prof in out.players.values():
        prof.first_seen = prof.last_seen = None
        prof.session_count, prof.session_seconds, prof.chat_messages, prof.deaths_logged = 0, 0.0, 0, 0
        prof.names_seen = []
    aliases = players_mod.name_aliases(
        out.events, {p.uuid.lower(): p.name for p in out.players.values() if p.uuid})

    def profile_for(name: str) -> m.PlayerProfile:
        cur, u = aliases.get(name.lower(), (name, None))
        prof = out.players.setdefault(cur.lower(), m.PlayerProfile(uuid=u, name=cur))
        if u and not prof.uuid:
            prof.uuid = u
        return prof

    for ev in out.events:
        if not ev.player:
            continue
        prof = profile_for(ev.player)
        if ev.player not in prof.names_seen:
            prof.names_seen.append(ev.player)
        prof.first_seen = ev.time if prof.first_seen is None else min(prof.first_seen, ev.time)
        prof.last_seen = ev.time if prof.last_seen is None else max(prof.last_seen, ev.time)
        prof.chat_messages += ev.type == m.EV_CHAT
        prof.deaths_logged += ev.type == m.EV_DEATH
    for s in out.sessions:
        prof = profile_for(s.player)
        prof.session_count += 1
        prof.session_seconds += s.seconds

    # Recordings kept outside the backups (library) and extra replay files, read with the
    # merged clock and player names; de-duplicated against the backups' own recordings.
    clock = LogClock(explicit=tz, samples=samples or [])
    uuid_to_name = {p.uuid.lower(): p.name for p in out.players.values() if p.uuid}
    extra_sets = [(rec, None) for rec in (recordings or [])]
    if extra_replays:
        extra_sets.append((newest.root, list(extra_replays)))
    for i, (rec, files) in enumerate(extra_sets):
        if files is None:
            label = "recordings" if i == 0 else f"recordings ({i + 1})"
            out.sources[label] = rec
            found, warns = replay_mod.read_replays(rec, uuid_to_name, None, clock.to_utc)
        else:
            label = None
            found, warns = [], []
            for f in files:
                try:
                    found.append(replay_mod.read_replay(f, rec, uuid_to_name, None, clock.to_utc))
                    found[-1].path = os.path.abspath(f)
                except Exception as exc:  # noqa: BLE001 -- a bad extra file is a warning, not a failure
                    warns.append(f"could not read replay {f}: {exc}")
        out.warnings += warns
        for r in found:
            key = (os.path.basename(r.path.rstrip("/")), r.start, (r.recorded_player or "").lower())
            if key in seen_replays:
                continue
            seen_replays.add(key)
            if label and not os.path.isabs(r.path):
                r.path = f"{label}/{r.path}"
            out.replays.append(r)
    out.replays.sort(key=lambda r: (r.start is None, r.start))

    # A new world = different level name, or a different seed when both seeds are known
    # (a backup whose seed couldn't be read is not a new world by itself).
    worlds_seen: list[list] = []          # [level_name, seed]
    def world_number(w) -> int:
        name, seed = w.get("level_name"), w.get("seed")
        for i, (n, sd) in enumerate(worlds_seen):
            if n == name and (seed is None or sd is None or seed == sd):
                if sd is None and seed is not None:
                    worlds_seen[i][1] = seed
                return i + 1
        worlds_seen.append([name, seed])
        return len(worlds_seen)
    for label, p, _root, d in parts:
        w = d.world
        out.backups.append({
            "label": label, "path": p, "time": _snapshot_time(d),
            "version": w.get("version_name"), "data_version": w.get("data_version"),
            "level_name": w.get("level_name"), "seed": w.get("seed"),
            "world_number": world_number(w),
            "size_bytes": w.get("size_bytes"), "game_time_hours": w.get("game_time_hours"),
            "players": len(d.players), "events": len(d.events), "log_files": len(d.log_files),
            "replays": len(d.replays), "mods": len(d.mods),
            "mod_list": [{k: x.get(k, "") for k in ("id", "name", "version", "loader", "kind", "file")} for x in d.mods],
            "datapacks": datapacks_of(d.world),
            "first_event": d.events[0].time if d.events else None,
            "last_event": d.events[-1].time if d.events else None,
        })
    out.changes = changes(out.backups)
    if len(worlds_seen) > 1:
        out.warnings.append(f"these backups contain {len(worlds_seen)} different worlds (level name/seed); "
                            "player stats shown are from the newest backup")
    return out
