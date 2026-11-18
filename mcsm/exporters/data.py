"""Exporting the parsed server as JSON, SQLite or CSV files.

The SQLite schema is documented in ``docs/DATA_MODEL.md``. It is meant for
ad-hoc questions, e.g.::

    sqlite3 server.sqlite "SELECT player, COUNT(*) FROM events WHERE type='death' GROUP BY player"
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3

from .. import models as m
from ..players import play_time_ticks
from ..timeutil import iso


def to_dict(data: m.ServerData) -> dict:
    return m.to_jsonable(data)


def write_json(data: m.ServerData, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(to_dict(data), fh, indent=1, ensure_ascii=False)


SCHEMA = """
CREATE TABLE meta      (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE players   (name TEXT PRIMARY KEY, uuid TEXT, first_seen TEXT, last_seen TEXT,
                        session_count INTEGER, session_seconds REAL, play_time_ticks INTEGER,
                        chat_messages INTEGER, deaths_logged INTEGER,
                        is_op INTEGER, is_whitelisted INTEGER, is_banned INTEGER, playerdata_json TEXT);
CREATE TABLE events    (id INTEGER PRIMARY KEY, time TEXT, epoch_ms INTEGER, type TEXT, player TEXT,
                        text TEXT, source TEXT, line INTEGER, data_json TEXT);
CREATE INDEX events_time   ON events(time);
CREATE INDEX events_type   ON events(type);
CREATE INDEX events_player ON events(player);
CREATE TABLE sessions  (id INTEGER PRIMARY KEY, player TEXT, start TEXT, end TEXT, seconds REAL, end_reason TEXT);
CREATE TABLE stats     (player TEXT, category TEXT, key TEXT, value INTEGER);
CREATE INDEX stats_lookup ON stats(category, key);
CREATE TABLE advancements (player TEXT, advancement TEXT, done INTEGER, completed TEXT);
CREATE TABLE replays   (path TEXT PRIMARY KEY, start TEXT, end TEXT, duration_ms INTEGER, server_name TEXT,
                        mc_version TEXT, generator TEXT, recorded_player TEXT, players TEXT, size_bytes INTEGER);
CREATE TABLE log_files (path TEXT PRIMARY KEY, date TEXT, date_source TEXT, lines INTEGER, first_time TEXT, last_time TEXT);
"""


def write_sqlite(data: m.ServerData, path: str) -> None:
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO meta VALUES (?,?)", [
            ("root", data.root), ("generated_at", iso(data.generated_at)), ("timezone", data.timezone_info),
            ("server_properties", json.dumps(data.server_properties)),
            ("world", json.dumps(m.to_jsonable(data.world))), ("warnings", json.dumps(data.warnings)),
        ])
        con.executemany("INSERT INTO players VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (p.name, p.uuid, iso(p.first_seen), iso(p.last_seen), p.session_count, p.session_seconds,
             play_time_ticks(p.stats), p.chat_messages, p.deaths_logged, int(p.is_op), int(p.is_whitelisted),
             int(p.is_banned), json.dumps(p.playerdata)) for p in data.players.values()])
        con.executemany("INSERT INTO events (time, epoch_ms, type, player, text, source, line, data_json) "
                        "VALUES (?,?,?,?,?,?,?,?)", [
            (iso(e.time), int(e.time.timestamp() * 1000), e.type, e.player, e.text, e.source, e.line,
             json.dumps(m.to_jsonable(e.data))) for e in data.events])
        con.executemany("INSERT INTO sessions (player, start, end, seconds, end_reason) VALUES (?,?,?,?,?)", [
            (s.player, iso(s.start), iso(s.end), s.seconds, s.end_reason) for s in data.sessions])
        con.executemany("INSERT INTO stats VALUES (?,?,?,?)", [
            (p.name, cat, key, val) for p in data.players.values()
            for cat, values in p.stats.items() for key, val in values.items()])
        con.executemany("INSERT INTO advancements VALUES (?,?,?,?)", [
            (p.name, adv, int(info["done"]), iso(info.get("completed")))
            for p in data.players.values() for adv, info in p.advancements.items()])
        con.executemany("INSERT INTO replays VALUES (?,?,?,?,?,?,?,?,?,?)", [
            (r.path, iso(r.start), iso(r.end), r.duration_ms, r.server_name, r.mc_version, r.generator,
             r.recorded_player, json.dumps(r.player_names), r.size_bytes) for r in data.replays])
        con.executemany("INSERT INTO log_files VALUES (?,?,?,?,?,?)", [
            (f.path, f.date, f.date_source, f.lines, iso(f.first_time), iso(f.last_time)) for f in data.log_files])
        con.commit()
    finally:
        con.close()


def write_csv_dir(data: m.ServerData, out_dir: str) -> list[str]:
    """events.csv, sessions.csv, players.csv, stats.csv -- for spreadsheets."""
    os.makedirs(out_dir, exist_ok=True)
    written = []

    def dump(name, header, rows):
        p = os.path.join(out_dir, name)
        with open(p, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
        written.append(p)

    dump("events.csv", ["time_utc", "type", "player", "text", "source", "line"],
         [(iso(e.time), e.type, e.player or "", e.text, e.source, e.line or "") for e in data.events])
    dump("sessions.csv", ["player", "start_utc", "end_utc", "minutes", "end_reason"],
         [(s.player, iso(s.start), iso(s.end) or "", round(s.seconds / 60, 1), s.end_reason) for s in data.sessions])
    dump("players.csv", ["name", "uuid", "first_seen", "last_seen", "sessions", "logged_hours",
                         "play_time_hours_stats", "chat_messages", "deaths_logged"],
         [(p.name, p.uuid or "", iso(p.first_seen) or "", iso(p.last_seen) or "", p.session_count,
           round(p.session_seconds / 3600, 2), round(play_time_ticks(p.stats) / 72000, 2),
           p.chat_messages, p.deaths_logged) for p in sorted(data.players.values(), key=lambda p: p.name.lower())])
    dump("stats.csv", ["player", "category", "key", "value"],
         [(p.name, c, k, v) for p in data.players.values() for c, vals in p.stats.items() for k, v in vals.items()])
    return written
