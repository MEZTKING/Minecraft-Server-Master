"""Data classes shared by every parser.

Everything a parser finds ends up in one :class:`ServerData` object,
which the exporters (HTML report, SQLite, JSON, video sync) consume.
See ``docs/DATA_MODEL.md`` for a field-by-field description.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

# Event types. Kept as plain strings so they serialise trivially.
EV_SERVER_START = "server_start"      # "Starting minecraft server version ..."
EV_SERVER_READY = "server_ready"      # "Done (12.3s)! For help, type ..."
EV_SERVER_STOP = "server_stop"        # "Stopping server"
EV_CRASH = "crash"                    # a crash-reports/ file
EV_JOIN = "join"                      # "<name> joined the game"
EV_LEAVE = "leave"                    # "<name> left the game"
EV_CHAT = "chat"                      # "<name> message"
EV_SERVER_CHAT = "server_chat"        # "[Server] message" (/say from console)
EV_ME = "emote"                       # "* name does something" (/me)
EV_DEATH = "death"                    # "<name> was slain by Zombie"
EV_ADVANCEMENT = "advancement"        # "<name> has made the advancement [X]"
EV_COMMAND = "command"                # "<name> issued server command: /x" or "[name: did x]"
EV_REPLAY_MARKER = "replay_marker"    # a marker placed inside a replay

ALL_EVENT_TYPES = [
    EV_SERVER_START, EV_SERVER_READY, EV_SERVER_STOP, EV_CRASH, EV_JOIN, EV_LEAVE,
    EV_CHAT, EV_SERVER_CHAT, EV_ME, EV_DEATH, EV_ADVANCEMENT, EV_COMMAND, EV_REPLAY_MARKER,
]


@dataclass
class Event:
    """One thing that happened at a point in time."""

    time: datetime                 # aware, UTC
    type: str                      # one of the EV_* constants
    player: str | None = None      # player name, if the event belongs to one
    text: str = ""                 # human-readable description
    source: str = ""               # file it came from (relative to the server root)
    line: int | None = None        # 1-based line number in that file
    data: dict[str, Any] = field(default_factory=dict)  # type-specific extras


@dataclass
class Session:
    """A continuous stretch of a player being online."""

    player: str
    start: datetime
    end: datetime | None           # None if still online when the logs end
    end_reason: str = "leave"      # leave | server_stop | server_restart | log_end
    join_position: tuple[float, float, float] | None = None

    @property
    def seconds(self) -> float:
        if self.end is None:
            return 0.0
        return max(0.0, (self.end - self.start).total_seconds())


@dataclass
class LogFileInfo:
    path: str                      # relative to the server root
    date: str                      # ISO date anchored to the last line
    date_source: str               # "filename" | "mtime" | "override"
    lines: int
    first_time: datetime | None
    last_time: datetime | None


@dataclass
class PlayerProfile:
    """Everything known about one player, merged from all sources."""

    uuid: str | None
    name: str
    names_seen: list[str] = field(default_factory=list)
    stats: dict[str, dict[str, int]] = field(default_factory=dict)   # category -> key -> value
    advancements: dict[str, dict[str, Any]] = field(default_factory=dict)
    playerdata: dict[str, Any] = field(default_factory=dict)        # summarised .dat contents
    is_op: bool = False
    is_whitelisted: bool = False
    is_banned: bool = False
    # Derived from logs:
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    session_count: int = 0
    session_seconds: float = 0.0
    chat_messages: int = 0
    deaths_logged: int = 0


@dataclass
class Replay:
    """A ReplayMod (``.mcpr``) or Flashback (``.zip``) recording -- see :mod:`mcsm.replays`."""

    path: str                      # relative to the server root
    start: datetime | None         # UTC, from metaData.json "date"
    duration_ms: int
    server_name: str = ""
    mc_version: str = ""
    generator: str = ""
    players: list[str] = field(default_factory=list)   # UUIDs listed in metadata
    player_names: list[str] = field(default_factory=list)
    recorded_player: str | None = None  # ServerReplay folder name, if any
    markers: list[dict[str, Any]] = field(default_factory=list)
    size_bytes: int = 0
    raw_meta: dict[str, Any] = field(default_factory=dict)
    format: str = "replaymod"          # "replaymod" (.mcpr) or "flashback" (.zip)
    start_source: str = "metadata"     # metadata | real-time-clock | file-name | file-time
    unfinished: bool = False           # an in-progress/crashed recording folder

    @property
    def end(self) -> datetime | None:
        from datetime import timedelta
        return None if self.start is None else self.start + timedelta(milliseconds=self.duration_ms)


@dataclass
class ServerData:
    """The complete parsed server."""

    root: str
    server_properties: dict[str, str] = field(default_factory=dict)
    world: dict[str, Any] = field(default_factory=dict)
    players: dict[str, PlayerProfile] = field(default_factory=dict)  # key: lower-case name
    events: list[Event] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)
    replays: list[Replay] = field(default_factory=list)
    log_files: list[LogFileInfo] = field(default_factory=list)
    mods: list[dict[str, str]] = field(default_factory=list)
    timezone_info: str = ""
    warnings: list[str] = field(default_factory=list)
    generated_at: datetime | None = None
    # Multi-backup archives (mcsm.archive) only:
    backups: list[dict[str, Any]] = field(default_factory=list)          # one entry per backup, oldest first
    player_history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # name -> per-backup snapshot
    sources: dict[str, str] = field(default_factory=dict)                # backup label -> extracted root
    changes: list[dict[str, Any]] = field(default_factory=list)          # mods/datapacks/version changes between backups
    library_world: str | None = None                                     # library world folder, if loaded from one

    def player(self, name: str) -> PlayerProfile | None:
        return self.players.get(name.lower())


def to_jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses/datetimes into JSON-friendly values."""
    from .timeutil import iso

    if isinstance(obj, datetime):
        return iso(obj)
    if hasattr(obj, "__dataclass_fields__"):
        out = {k: to_jsonable(v) for k, v in asdict(obj).items()}
        if isinstance(obj, Session):
            out["seconds"] = obj.seconds
        if isinstance(obj, Replay):
            out["end"] = iso(obj.end)
        return out
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj
