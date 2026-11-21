"""Parsing server log files (``logs/latest.log`` and ``logs/*.log.gz``).

How a log line looks depends on the server software:

    Vanilla : [20:15:03] [Server thread/INFO]: Steve joined the game
    Fabric  : [20:15:03] [Server thread/INFO] (Minecraft) Steve joined the game
    Paper   : [20:15:03 INFO]: Steve joined the game
    Forge   : [01May2024 20:15:03.123] [Server thread/INFO] [minecraft/MinecraftServer]: Steve joined the game

:data:`_PREFIXES` matches all of them and yields the time and the message.

Dating log lines
----------------
Lines only carry a time of day. The date comes from the file name
(``2024-05-01-1.log.gz``). Minecraft's log4j config rolls the log at
midnight *and* at every server start, and names the archive after the
period it belongs to, so the date in the name is the date of the **last**
line. We therefore walk the file, count midnight roll-overs (the clock
going backwards), and anchor the final line to the file-name date.
``latest.log`` has no date in its name, so its modification time is used
instead (override with ``--latest-log-date``).

All produced times are converted to UTC through a :class:`~mcsm.timeutil.LogClock`.
See ``docs/LOG_FORMAT.md`` for the full list of recognised messages.
"""

from __future__ import annotations

import gzip
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable, Iterator

from . import models as m
from .timeutil import LogClock

# --- line prefixes ---------------------------------------------------------

_PREFIXES = [
    # Forge: [01May2024 20:15:03.123] [Server thread/INFO] [minecraft/DedicatedServer]: msg
    re.compile(r"^\[(?P<fdate>\d{2}[A-Za-z]{3}\d{4}) (?P<t>\d{2}:\d{2}:\d{2})(?:\.\d+)?\] "
               r"\[(?P<thread>[^\]]*)/(?P<level>[A-Z]+)\] \[[^\]]*\]: (?P<msg>.*)$"),
    # Vanilla / Fabric: [20:15:03] [Server thread/INFO]: msg   |   ... [Server thread/INFO] (Minecraft) msg
    re.compile(r"^\[(?P<t>\d{2}:\d{2}:\d{2})(?:\.\d+)?\] \[(?P<thread>[^\]]*)/(?P<level>[A-Z]+)\]"
               r"(?:: | \([^)]*\) )(?P<msg>.*)$"),
    # Paper / Spigot / Purpur: [20:15:03 INFO]: msg   (optionally [20:15:03 INFO]: [Plugin] msg)
    re.compile(r"^\[(?P<t>\d{2}:\d{2}:\d{2})(?:\.\d+)? (?P<level>[A-Z]+)\]: (?P<msg>.*)$"),
]

# Minecraft colour/format codes (section sign) and ANSI escapes that Paper writes.
_SYSTEM_CHAT = "System chat: "
_FORMAT_CODES = re.compile(r"§[0-9a-fk-orx]|\x1b\[[0-9;]*m", re.IGNORECASE)

# --- message patterns -----------------------------------------------------

_NAME = r"[A-Za-z0-9_.*\-]{1,32}"   # allows Bedrock/Geyser prefixes such as ".Name" or "*Name"

RE_START = re.compile(r"^Starting minecraft server version (?P<version>\S+)")
RE_READY = re.compile(r'^Done \((?P<secs>[\d.,]+)s\)! For help, type "help"')
RE_STOP = re.compile(r"^Stopping (?:the )?server$")
RE_JOIN = re.compile(rf"^(?P<name>{_NAME}) joined the game(?: \(formerly known as (?P<old>{_NAME})\))?$")
RE_LEAVE = re.compile(rf"^(?P<name>{_NAME}) left the game$")
RE_LOGIN = re.compile(rf"^(?P<name>{_NAME})\[[^\]]*\] logged in with entity id \d+ at "
                      r"\((?:\[(?P<world>[^\]]+)\])?(?P<x>-?[\d.]+), (?P<y>-?[\d.]+), (?P<z>-?[\d.]+)\)")
RE_LOST = re.compile(rf"^(?P<name>{_NAME}) lost connection: (?P<reason>.*)$")
# Logged by the "User Authenticator" thread just before "logged in with entity id".
RE_UUID = re.compile(rf"^UUID of player (?P<name>{_NAME}) is (?P<uuid>[0-9a-fA-F]{{8}}-[0-9a-fA-F]{{4}}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$")
RE_CHAT = re.compile(rf"^(?:\[Not Secure\] )?<(?P<name>{_NAME})> (?P<text>.*)$")
RE_SERVER_SAY = re.compile(r"^\[(?:Server|Rcon)\] (?P<text>.*)$")
RE_EMOTE = re.compile(rf"^\* (?P<name>{_NAME}) (?P<text>.*)$")
RE_ADVANCEMENT = re.compile(
    rf"^(?P<name>{_NAME}) has (?P<kind>made the advancement|completed the challenge|reached the goal) "
    r"\[(?P<title>.+)\]$")
RE_PAPER_COMMAND = re.compile(rf"^(?P<name>{_NAME}) issued server command: (?P<cmd>/.*)$")
RE_VANILLA_COMMAND = re.compile(rf"^\[(?P<name>{_NAME}): (?P<text>.+)\]$")

# Death message *verbs*: the text right after the player's name in every
# vanilla death message (1.12 - 1.21). A line counts as a death only when it
# starts with a known player name followed by one of these.
DEATH_PHRASES = [
    "was slain by", "was shot by", "was killed", "was blown up by", "blew up",
    "was fireballed by", "was pummeled by", "was impaled", "was skewered by",
    "was stung to death", "was squashed by", "was squished", "was poked to death",
    "was pricked to death", "was struck by lightning", "was obliterated by",
    "was frozen to death", "was roasted in dragon's breath", "was doomed to fall",
    "was knocked into the void", "was smashed by", "was burnt to a crisp",
    "was burned to a crisp", "was speared by",
    "drowned", "died", "hit the ground too hard", "fell from a high place",
    "fell off", "fell out of the world", "fell while climbing", "fell too far",
    "fell into", "fell from", "burned to death", "went up in flames", "walked into fire",
    "walked into the danger zone", "walked into a cactus", "tried to swim in lava",
    "discovered the floor was lava", "suffocated in a wall", "was squashed",
    "starved to death", "withered away", "froze to death", "experienced kinetic energy",
    "didn't want to live", "left the confines of this world", "went off with a bang",
    "was killed by magic", "was killed by even more magic", "was killed while trying to hurt",
    "was blown from a high place", "was shot off", "was struck",
]
_DEATH_RE = re.compile(rf"^(?P<name>{_NAME}) (?P<rest>(?:{'|'.join(re.escape(p) for p in sorted(DEATH_PHRASES, key=len, reverse=True))})\b.*)$")

# Death messages that mention a killer: "... by <killer>" / "... while fighting <x>"
_KILLER_RE = re.compile(r"\b(?:by|fighting|escaping|trying to hurt) (?P<killer>.+?)(?: using \[(?P<weapon>.+)\])?$")

_LOG_NAME_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<n>\d+)\.log(?:\.gz)?$")


@dataclass
class RawLine:
    """One parsed log line before it becomes an :class:`~mcsm.models.Event`."""

    local: datetime        # naive, server local time
    level: str
    thread: str
    msg: str
    lineno: int


def _open_text(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def list_log_files(root: str) -> list[str]:
    """Return log files in chronological order (archives first, latest.log last)."""
    logs_dir = os.path.join(root, "logs")
    if not os.path.isdir(logs_dir):
        return []
    archives = []
    for name in os.listdir(logs_dir):
        mt = _LOG_NAME_RE.match(name)
        if mt:
            archives.append((mt.group("date"), int(mt.group("n")), os.path.join(logs_dir, name)))
    archives.sort()
    files = [p for _, _, p in archives]
    latest = os.path.join(logs_dir, "latest.log")
    if os.path.isfile(latest):
        files.append(latest)
    return files


def _split_line(line: str) -> tuple[str | None, str, str, str, str] | None:
    """Return (forge_date, hh:mm:ss, level, thread, message) or None if not a log line."""
    for rx in _PREFIXES:
        mt = rx.match(line)
        if mt:
            gd = mt.groupdict()
            msg = _FORMAT_CODES.sub("", gd["msg"]).rstrip()
            # Since Minecraft 26.3 (seen on the owner's server from late October 2026) system
            # messages -- joins, leaves, deaths, advancements, command feedback -- are logged as
            # "System chat: <message>". Player chat ("<name> text") is unchanged.
            if msg.startswith(_SYSTEM_CHAT):
                msg = msg[len(_SYSTEM_CHAT):]
            return gd.get("fdate"), gd["t"], gd.get("level") or "INFO", gd.get("thread") or "", msg
    return None


def read_raw_lines(path: str, anchor_date: date) -> list[RawLine]:
    """Read a log file and attach a local date to every line.

    *anchor_date* is the date of the **last** line (see module docstring).
    Forge logs carry their own date and ignore the anchor.
    """
    parsed: list[tuple[date | None, time, str, str, str, int]] = []
    with _open_text(path) as fh:
        for lineno, line in enumerate(fh, start=1):
            parts = _split_line(line.rstrip("\r\n"))
            if parts is None:
                continue
            fdate, t, level, thread, msg = parts
            own_date = None
            if fdate:
                try:
                    own_date = datetime.strptime(fdate, "%d%b%Y").date()
                except ValueError:
                    own_date = None
            hh, mm, ss = (int(x) for x in t.split(":"))
            parsed.append((own_date, time(hh, mm, ss), level, thread, msg, lineno))

    if not parsed:
        return []

    # Count midnight roll-overs: a time earlier than the previous one by
    # more than an hour means the day changed. (Small backwards jumps can
    # happen with clock corrections and are ignored.)
    day_index = [0]
    for i in range(1, len(parsed)):
        prev_t, cur_t = parsed[i - 1][1], parsed[i][1]
        prev_s = prev_t.hour * 3600 + prev_t.minute * 60 + prev_t.second
        cur_s = cur_t.hour * 3600 + cur_t.minute * 60 + cur_t.second
        day_index.append(day_index[-1] + (1 if prev_s - cur_s > 3600 else 0))
    last_day = day_index[-1]

    out = []
    for (own_date, t, level, thread, msg, lineno), di in zip(parsed, day_index):
        d = own_date or (anchor_date - timedelta(days=last_day - di))
        out.append(RawLine(datetime.combine(d, t), level, thread, msg, lineno))
    return out


def anchor_date_for(path: str, latest_override: date | None = None) -> tuple[date, str]:
    """Work out the date of the last line of *path* and how we know it."""
    mt = _LOG_NAME_RE.match(os.path.basename(path))
    if mt:
        return date.fromisoformat(mt.group("date")), "filename"
    if latest_override is not None:
        return latest_override, "override"
    return datetime.fromtimestamp(os.path.getmtime(path)).date(), "mtime"


def classify(raw: RawLine, known_players: set[str]) -> tuple[str, str | None, str, dict] | None:
    """Turn a message into ``(event_type, player, text, data)`` or ``None``.

    *known_players* (lower-case names) is used to tell death messages apart
    from arbitrary text: a death needs a known player name at the start.
    The set is updated as players join, so it works even without usercache.
    """
    msg = raw.msg

    if mt := RE_START.match(msg):
        return m.EV_SERVER_START, None, msg, {"version": mt.group("version")}
    if mt := RE_READY.match(msg):
        return m.EV_SERVER_READY, None, "Server ready", {"startup_seconds": float(mt.group("secs").replace(",", "."))}
    if RE_STOP.match(msg):
        return m.EV_SERVER_STOP, None, "Server stopping", {}
    if mt := RE_JOIN.match(msg):
        data = {"formerly": mt.group("old")} if mt.group("old") else {}
        return m.EV_JOIN, mt.group("name"), msg, data
    if mt := RE_LEAVE.match(msg):
        return m.EV_LEAVE, mt.group("name"), msg, {}
    if mt := RE_ADVANCEMENT.match(msg):
        kind = {"made the advancement": "task", "completed the challenge": "challenge",
                "reached the goal": "goal"}[mt.group("kind")]
        return m.EV_ADVANCEMENT, mt.group("name"), msg, {"title": mt.group("title"), "kind": kind}
    if mt := RE_CHAT.match(msg):
        return m.EV_CHAT, mt.group("name"), mt.group("text"), {}
    if mt := RE_SERVER_SAY.match(msg):
        return m.EV_SERVER_CHAT, None, mt.group("text"), {}
    if mt := RE_PAPER_COMMAND.match(msg):
        return m.EV_COMMAND, mt.group("name"), mt.group("cmd"), {"command": mt.group("cmd")}
    if mt := RE_VANILLA_COMMAND.match(msg):
        if mt.group("name").lower() in known_players or mt.group("name") in ("Server", "Rcon"):
            who = None if mt.group("name") in ("Server", "Rcon") else mt.group("name")
            return m.EV_COMMAND, who, mt.group("text"), {"feedback": mt.group("text")}
    if mt := RE_EMOTE.match(msg):
        if mt.group("name").lower() in known_players:
            return m.EV_ME, mt.group("name"), msg, {}
    if mt := _DEATH_RE.match(msg):
        if mt.group("name").lower() in known_players:
            data: dict = {"cause": mt.group("rest")}
            km = _KILLER_RE.search(mt.group("rest"))
            if km:
                data["killer"] = km.group("killer")
                if km.group("weapon"):
                    data["weapon"] = km.group("weapon")
            return m.EV_DEATH, mt.group("name"), msg, data
    return None


def parse_logs(root: str, clock: LogClock, known_players: Iterable[str] = (),
               latest_date: date | None = None) -> tuple[list[m.Event], list[m.LogFileInfo]]:
    """Parse every log file under ``root/logs`` into events.

    Returns ``(events, file_infos)``. Events are sorted by time.
    """
    known = {p.lower() for p in known_players}
    events: list[m.Event] = []
    infos: list[m.LogFileInfo] = []
    pending_login: dict[str, dict] = {}

    for path in list_log_files(root):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        anchor, how = anchor_date_for(path, latest_date)
        try:
            lines = read_raw_lines(path, anchor)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            infos.append(m.LogFileInfo(rel, anchor.isoformat(), f"unreadable: {exc}", 0, None, None))
            continue
        infos.append(m.LogFileInfo(
            rel, anchor.isoformat(), how, len(lines),
            clock.to_utc(lines[0].local) if lines else None,
            clock.to_utc(lines[-1].local) if lines else None))

        for raw in lines:
            # "UUID of player" and "logged in with entity id" precede "joined the game";
            # remember them and attach the uuid and spawn position to the join event.
            if mt := RE_UUID.match(raw.msg):
                pending_login[mt.group("name").lower()] = {"uuid": mt.group("uuid").lower()}
                continue
            if mt := RE_LOGIN.match(raw.msg):
                pos = (float(mt.group("x")), float(mt.group("y")), float(mt.group("z")))
                pending_login.setdefault(mt.group("name").lower(), {}).update(
                    {"position": pos, "world": mt.group("world")})
                known.add(mt.group("name").lower())
                continue
            if mt := RE_LOST.match(raw.msg):
                pending_login.setdefault("__lost__" + mt.group("name").lower(), {})["reason"] = mt.group("reason")
                continue
            result = classify(raw, known)
            if result is None:
                continue
            etype, player, text, data = result
            if player:
                known.add(player.lower())
            if etype == m.EV_JOIN and player:
                extra = pending_login.pop(player.lower(), None)
                if extra:
                    data.update({k: v for k, v in extra.items() if v is not None})
            if etype == m.EV_LEAVE and player:
                lost = pending_login.pop("__lost__" + player.lower(), None)
                if lost and lost.get("reason"):
                    data["reason"] = lost["reason"]
            when = clock.to_utc(raw.local)
            if etype == m.EV_SERVER_STOP and events and events[-1].type == m.EV_SERVER_STOP \
                    and abs((when - events[-1].time).total_seconds()) <= 10:
                continue   # "Stopping the server" and "Stopping server" are logged for one shutdown
            events.append(m.Event(when, etype, player, text, rel, raw.lineno, data))

    events.sort(key=lambda e: e.time)  # stable: keeps file order for equal times
    return events, infos


def iter_crash_reports(root: str) -> Iterator[tuple[datetime, str, str]]:
    """Yield ``(local_naive_time, relative_path, description)`` for crash reports."""
    folder = os.path.join(root, "crash-reports")
    if not os.path.isdir(folder):
        return
    rx = re.compile(r"^crash-(\d{4}-\d{2}-\d{2})_(\d{2})\.(\d{2})\.(\d{2})-(\w+)\.txt$")
    for name in sorted(os.listdir(folder)):
        mt = rx.match(name)
        if not mt:
            continue
        local = datetime.fromisoformat(f"{mt.group(1)}T{mt.group(2)}:{mt.group(3)}:{mt.group(4)}")
        desc = ""
        try:
            with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("Description:"):
                        desc = line.split(":", 1)[1].strip()
                        break
        except OSError:
            pass
        yield local, f"crash-reports/{name}", desc or "Server crashed"


def build_sessions(events: list[m.Event]) -> list[m.Session]:
    """Pair join/leave events into play sessions.

    Rules:
    * ``join`` opens a session (a second join without a leave closes the
      old one first, ending it at the new join).
    * ``leave`` closes it.
    * ``server_stop`` / ``server_start`` close every open session -- a
      ``server_start`` without a prior stop means the server crashed or was
      killed, and the session is ended at the last event seen before it.
    * Sessions still open at the end of all logs get ``end=None`` unless
      a later event exists, in which case they end at the last event.
    """
    sessions: list[m.Session] = []
    open_: dict[str, m.Session] = {}
    last_time = None

    def close_all(at, reason):
        for s in open_.values():
            s.end, s.end_reason = at, reason
            sessions.append(s)
        open_.clear()

    for ev in events:
        if ev.type == m.EV_JOIN and ev.player:
            key = ev.player.lower()
            if key in open_:
                old = open_.pop(key)
                old.end, old.end_reason = ev.time, "rejoin"
                sessions.append(old)
            pos = ev.data.get("position")
            open_[key] = m.Session(ev.player, ev.time, None, "leave", tuple(pos) if pos else None)
        elif ev.type == m.EV_LEAVE and ev.player:
            s = open_.pop(ev.player.lower(), None)
            if s:
                s.end, s.end_reason = ev.time, "leave"
                sessions.append(s)
        elif ev.type == m.EV_SERVER_STOP:
            close_all(ev.time, "server_stop")
        elif ev.type == m.EV_SERVER_START and open_:
            close_all(last_time or ev.time, "server_restart")
        if ev.type not in (m.EV_CRASH, m.EV_REPLAY_MARKER):
            last_time = ev.time

    for s in open_.values():
        if last_time and last_time > s.start:
            s.end, s.end_reason = last_time, "log_end"
        else:
            s.end_reason = "log_end"
        sessions.append(s)
    sessions.sort(key=lambda s: s.start)
    return sessions
