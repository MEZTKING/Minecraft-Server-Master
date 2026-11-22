"""Players the owner never wants to see: people who were never supposed to join.

The list is kept in ``%LOCALAPPDATA%/mcsm/ignored_players.json`` (one list for every server, by name and
UUID, so a renamed player stays ignored). :func:`apply` removes them from a loaded server: the player
list, the events (joins, chat, deaths...), the play sessions, the backup history and recordings they
made, so the reports, moments, story, highlights and timelapses never show them. Their positions in
other people's recordings are left out by the history viewer (:func:`is_ignored`).

Nothing in the backups or recordings is changed; taking a player off the list brings them back the
next time the server is loaded.
"""

from __future__ import annotations

import json
import os

from . import models as m


def default_file() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mcsm", "ignored_players.json")


def load(path: str | None = None) -> list[dict]:
    """``[{"name": ..., "uuid": ... or None, "names": [...]}]``; empty when there's no list yet."""
    try:
        with open(path or default_file(), encoding="utf-8") as fh:
            items = json.load(fh).get("players") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [i for i in items if isinstance(i, dict) and i.get("name")]


def save(items: list[dict], path: str | None = None) -> None:
    path = path or default_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as fh:
        json.dump({"players": items}, fh, indent=1)
    os.replace(path + ".part", path)


def entry_for(data: m.ServerData | None, name: str) -> dict:
    """An entry for *name*, with the UUID and every name seen if the loaded server knows the player."""
    p = data.player(name) if data else None
    if p is None and data:
        p = next((q for q in data.players.values() if name.lower() in (n.lower() for n in q.names_seen)), None)
    if p is None:
        return {"name": name, "uuid": None, "names": [name]}
    return {"name": p.name, "uuid": p.uuid, "names": sorted({p.name, *p.names_seen, name})}


def add(names: list[str], data: m.ServerData | None = None, path: str | None = None) -> list[dict]:
    items = load(path)
    for n in names:
        n = n.strip()
        if not n:
            continue
        e = entry_for(data, n)
        old = next((i for i in items if (e["uuid"] and i.get("uuid") == e["uuid"])
                    or i["name"].lower() == e["name"].lower()), None)
        if old:
            old["names"] = sorted({*old.get("names", []), *e["names"]})
            old["uuid"] = old.get("uuid") or e["uuid"]
        else:
            items.append(e)
    save(items, path)
    return items


def remove(names: list[str], path: str | None = None) -> list[dict]:
    low = {n.strip().lower() for n in names}
    items = [i for i in load(path)
             if i["name"].lower() not in low and not low & {n.lower() for n in i.get("names", [])}]
    save(items, path)
    return items


def names_of(items: list[dict]) -> tuple[set[str], set[str]]:
    """(lower-case names, UUIDs) of the list."""
    names = {n.lower() for i in items for n in [i["name"], *i.get("names", [])]}
    uuids = {i["uuid"].replace("-", "").lower() for i in items if i.get("uuid")}
    return names, uuids


def apply(data: m.ServerData, items: list[dict] | None = None) -> list[str]:
    """Remove the ignored players from *data* (in place). Returns the names that were found."""
    stored = items is None
    items = load() if stored else items
    if not items:
        return []
    names, uuids = names_of(items)
    gone = [p for k, p in data.players.items()
            if k in names or (p.uuid and p.uuid.replace("-", "").lower() in uuids)
            or any(n.lower() in names for n in p.names_seen)]
    for p in gone:
        names.add(p.name.lower())
        names.update(n.lower() for n in p.names_seen)
        if p.uuid:
            uuids.add(p.uuid.replace("-", "").lower())
    data.players = {k: p for k, p in data.players.items() if p not in gone}
    data.events = [e for e in data.events if not (e.player and e.player.lower() in names)]
    data.sessions = [s for s in data.sessions if s.player.lower() not in names]
    data.player_history = {k: v for k, v in data.player_history.items() if k.lower() not in names}
    data.replays = [r for r in data.replays if not (r.recorded_player and r.recorded_player.lower() in names)]
    for r in data.replays:                        # other people's recordings: leave them off the lists
        r.players = [u for u in r.players if u.replace("-", "").lower() not in uuids]
        r.player_names = [n for n in r.player_names if n.lower() not in names]
    data.ignored = sorted(names)
    if stored and gone:                           # remember UUIDs and other names, so a rename stays ignored
        changed = False
        for i in items:
            low = {n.lower() for n in [i["name"], *i.get("names", [])]}
            p = next((p for p in gone if p.name.lower() in low or low & {n.lower() for n in p.names_seen}), None)
            if p is None:
                continue
            new_names = sorted({*i.get("names", []), p.name, *p.names_seen})
            if new_names != i.get("names") or (p.uuid and not i.get("uuid")):
                i["names"], i["uuid"] = new_names, i.get("uuid") or p.uuid
                changed = True
        if changed:
            try:
                save(items)
            except OSError:
                pass
    return sorted(p.name for p in gone)


def is_ignored(data: m.ServerData, name: str | None) -> bool:
    return bool(name) and name.lower() in set(getattr(data, "ignored", None) or [])
