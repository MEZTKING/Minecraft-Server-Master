"""Builds a small but realistic fake server on disk for the tests.

Server time zone is UTC+02:00 (as if hosted in central Europe in summer),
which the advancement files reveal via their ``+0200`` suffix.

Timeline (server local time):

2024-05-01  (logs/2024-05-01-1.log.gz, vanilla format)
  19:59:50  server start 1.20.4
  20:00:10  Steve logs in / joins at (10.5, 64, -3.5)
  20:05:00  <Steve> hello world
  20:06:00  Alex joins
  20:10:00  Steve has made the advancement [Stone Age]   (matches advancement JSON 20:10:00 +0200)
  20:20:00  Alex was slain by Zombie
  20:30:00  Alex left the game
  23:59:50  <Steve> almost midnight
2024-05-02  (same file continues past midnight -> file named for 05-02)
  00:00:10  <Steve> past midnight
  00:05:00  Stopping server

logs/latest.log  (Fabric format, mtime 2024-05-03 22:00 local)
  2024-05-03 20:00:00  server start
  20:00:30  Steve joins
  20:01:00  Steve fell from a high place
  20:02:00  <Steve> gg
  20:03:00  Steve has reached the goal [Sky's the Limit]
  (no stop -> session ends at log end 20:03:00)

Replay recordings/players/Steve/2024-05-03.mcpr: starts 2024-05-03 18:00:00 UTC
(= 20:00:00 local), 10 minutes long, one marker at 90 s.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import struct
import time
import wave
import zipfile
from datetime import datetime, timezone

from mcsm import nbt
from mcsm.nbt import TAG_FLOAT, TAG_LONG, TAG_SHORT, Typed

STEVE = "069a79f4-44e9-4726-a5be-fca90e38aaf5"
ALEX = "853c80ef-3c37-49fd-aa49-938b674adae6"

LOG1 = """\
[19:59:50] [Server thread/INFO]: Starting minecraft server version 1.20.4
[19:59:55] [Server thread/INFO]: Done (4.512s)! For help, type "help"
[20:00:10] [Server thread/INFO]: Steve[/203.0.113.5:51234] logged in with entity id 12 at (10.5, 64.0, -3.5)
[20:00:10] [Server thread/INFO]: Steve joined the game
[20:05:00] [Server thread/INFO]: <Steve> hello world
[20:06:00] [Server thread/INFO]: Alex joined the game
[20:07:00] [Server thread/WARN]: Steve moved too quickly! 12.3,0.0,4.5
[20:08:00] [Server thread/INFO]: [Steve: Set the time to 1000]
[20:10:00] [Server thread/INFO]: Steve has made the advancement [Stone Age]
[20:20:00] [Server thread/INFO]: Alex was slain by Zombie
[20:25:00] [Server thread/INFO]: [Server] Restarting soon
[20:30:00] [Server thread/INFO]: Alex lost connection: Disconnected
[20:30:00] [Server thread/INFO]: Alex left the game
[23:59:50] [Server thread/INFO]: <Steve> almost midnight
[00:00:10] [Server thread/INFO]: <Steve> past midnight
[00:05:00] [Server thread/INFO]: Stopping server
"""

LATEST = """\
[20:00:00] [main/INFO] (Minecraft) Starting minecraft server version 1.20.4
[20:00:30] [Server thread/INFO] (Minecraft) Steve joined the game
[20:01:00] [Server thread/INFO] (Minecraft) Steve fell from a high place
[20:02:00] [Server thread/INFO] (Minecraft) [Not Secure] <Steve> gg
[20:03:00] [Server thread/INFO] (Minecraft) Steve has reached the goal [Sky's the Limit]
"""

REPLAY_START_MS = int(datetime(2024, 5, 3, 18, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)


def _write(path: str, text: str | bytes, mode: str = "w") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as fh:
        fh.write(text)


def build_server(root: str) -> str:
    """Create the fake server under *root* and return *root*."""
    _write(os.path.join(root, "server.properties"),
           "#Minecraft server properties\nlevel-name=world\nmotd=Friends SMP\nmax-players=20\n"
           "rcon.password=hunter2\ndifficulty=hard\n")
    _write(os.path.join(root, "eula.txt"), "eula=true\n")
    _write(os.path.join(root, "usercache.json"), json.dumps([
        {"name": "Steve", "uuid": STEVE, "expiresOn": "2024-06-01 00:00:00 +0200"},
        {"name": "Alex", "uuid": ALEX, "expiresOn": "2024-06-01 00:00:00 +0200"}]))
    _write(os.path.join(root, "ops.json"), json.dumps([{"uuid": STEVE, "name": "Steve", "level": 4}]))

    # logs
    _write(os.path.join(root, "logs", "2024-05-02-1.log.gz"), gzip.compress(LOG1.encode()), "wb")
    latest = os.path.join(root, "logs", "latest.log")
    _write(latest, LATEST)
    ts = time.mktime(datetime(2024, 5, 3, 22, 0, 0).timetuple())
    os.utime(latest, (ts, ts))
    _write(os.path.join(root, "logs", "debug.log"), "noise\n")

    # world
    w = os.path.join(root, "world")
    _write(os.path.join(w, "level.dat"), nbt.dumps({"Data": {
        "LevelName": "Friends SMP", "Time": Typed(TAG_LONG, 20 * 3600 * 5), "DayTime": Typed(TAG_LONG, 24000 * 7 + 100),
        "SpawnX": 0, "SpawnY": 64, "SpawnZ": 0, "Difficulty": Typed(1, 3), "GameType": 0, "hardcore": Typed(1, 0),
        "Version": {"Name": "1.20.4", "Id": 3700}, "DataVersion": 3700,
        "WorldGenSettings": {"seed": Typed(TAG_LONG, -123456789012345)},
        "GameRules": {"keepInventory": "true"},
    }}), "wb")
    _write(os.path.join(w, "region", "r.0.0.mca"), b"\0" * 8192, "wb")
    _write(os.path.join(w, "DIM-1", "region", "r.0.0.mca"), b"\0" * 4096, "wb")

    _write(os.path.join(w, "stats", f"{STEVE}.json"), json.dumps({"stats": {
        "minecraft:custom": {"minecraft:play_time": 72000 * 3, "minecraft:deaths": 1, "minecraft:jump": 321,
                             "minecraft:walk_one_cm": 1234567, "minecraft:mob_kills": 12},
        "minecraft:mined": {"minecraft:stone": 500, "minecraft:diamond_ore": 3}}, "DataVersion": 3700}))
    _write(os.path.join(w, "stats", f"{ALEX}.json"), json.dumps({  # legacy pre-1.13 format
        "stat.playOneMinute": 36000, "stat.deaths": 1, "stat.mineBlock.minecraft.stone": 40}))

    _write(os.path.join(w, "advancements", f"{STEVE}.json"), json.dumps({
        "minecraft:story/mine_stone": {"criteria": {"get_stone": "2024-05-01 20:10:00 +0200"}, "done": True},
        "minecraft:recipes/misc/torch": {"criteria": {"has_coal": "2024-05-01 20:09:00 +0200"}, "done": True},
        "minecraft:adventure/sleep_in_bed": {"criteria": {"slept": "2024-05-03 20:00:40 +0200"}, "done": True},
        "DataVersion": 3700}))

    _write(os.path.join(w, "playerdata", f"{STEVE}.dat"), nbt.dumps({
        "Pos": [12.25, 70.0, -8.75], "Dimension": "minecraft:overworld", "Health": Typed(TAG_FLOAT, 20.0),
        "foodLevel": 18, "XpLevel": 30, "playerGameType": 0,
        "Inventory": [{"id": "minecraft:diamond_pickaxe", "count": 1, "Slot": Typed(1, 0)},
                      {"id": "minecraft:torch", "count": 64, "Slot": Typed(1, 1)},
                      {"id": "minecraft:torch", "count": 10, "Slot": Typed(1, 2)}],
        "EnderItems": [{"id": "minecraft:elytra", "Count": Typed(1, 1), "Slot": Typed(1, 0)}],
        "LastDeathLocation": {"dimension": "minecraft:the_nether", "pos": Typed(11, [1, 2, 3])},
        "abilities": {"flying": Typed(1, 0)}, "Air": Typed(TAG_SHORT, 300),
    }), "wb")

    # replay
    rp = os.path.join(root, "recordings", "players", "Steve", "2024-05-03--18-00-00.mcpr")
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    with zipfile.ZipFile(rp, "w") as zf:
        zf.writestr("metaData.json", json.dumps({
            "singleplayer": False, "serverName": "Friends SMP", "duration": 600_000, "date": REPLAY_START_MS,
            "mcversion": "1.20.4", "fileFormat": "MCPR", "fileFormatVersion": 14, "protocol": 765,
            "generator": "ServerReplay v1.1.0", "selfId": -1, "players": [STEVE]}))
        zf.writestr("markers.json", json.dumps([{"realTimestamp": 90_000, "value": {"name": "Cool moment"}}]))
        zf.writestr("recording.tmcpr", b"")

    # crash report
    _write(os.path.join(root, "crash-reports", "crash-2024-05-02_00.10.00-server.txt"),
           "---- Minecraft Crash Report ----\nTime: whenever\nDescription: Ticking entity\n")
    return root


def write_click_track(path: str, bpm: float = 120.0, first_beat: float = 0.25, seconds: float = 12.0,
                      rate: int = 22050) -> None:
    """A mono WAV with a short decaying click on every beat (accent on the bar)."""
    n = int(seconds * rate)
    samples = [0.0] * n
    period = 60.0 / bpm
    k = 0
    while True:
        t = first_beat + k * period
        if t >= seconds:
            break
        start = int(t * rate)
        amp = 0.9 if k % 4 == 0 else 0.6
        for i in range(int(0.03 * rate)):
            if start + i < n:
                samples[start + i] += amp * math.exp(-i / (0.005 * rate)) * math.sin(2 * math.pi * 1000 * i / rate)
        k += 1
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in samples))


FLASHBACK_RTC_MS = int(datetime(2024, 5, 3, 18, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)


def add_extra_replays(root: str) -> None:
    """Flashback + unfinished replays (kept separate so older tests keep their counts).

    * recordings/players/<steve-uuid>/2024-05-03--20-05-00.zip   Flashback, real-time clock
      says 18:05:01 UTC after 20 ticks -> start 18:05:00 UTC; 3 minutes long; marker at tick 600.
    * recordings/players/<steve-uuid>/2024-05-03--21-00-00.zip   Flashback without a clock
      -> start from the file name, 21:00 server time (UTC+2) = 19:00 UTC.
    * recordings/players/<steve-uuid>/2024-05-03--22-00-00/       unfinished Flashback folder
    * recordings/chunks/base/2024-05-03--22-30-00.mcpr.tmp/       unfinished ReplayMod folder
    """
    from mcsm.replays import write_flashback_chunk
    folder = os.path.join(root, "recordings", "players", STEVE)
    os.makedirs(folder, exist_ok=True)

    def meta(ticks, markers=None):
        d = {"uuid": "x", "name": "Base tour", "version_string": "1.21.1", "world_name": "Friends SMP",
             "total_ticks": ticks, "chunks": {"c0.flashback": {"duration": ticks, "forcePlaySnapshot": False}}}
        if markers:
            d["markers"] = markers
        return json.dumps(d)

    import io as _io
    buf = _io.BytesIO()
    write_flashback_chunk(buf, FLASHBACK_RTC_MS + 1000, ticks_before=20, extra_ticks=5)
    with zipfile.ZipFile(os.path.join(folder, "2024-05-03--20-05-00.zip"), "w") as zf:
        zf.writestr("metadata.json", meta(3600, {"600": {"colour": 16711680, "description": "Dragon fight",
                                                         "position": {"position": [1.0, 70.0, 2.0],
                                                                      "dimension": "minecraft:the_end"}}}))
        zf.writestr("c0.flashback", buf.getvalue())
        zf.writestr("icon.png", b"")

    with zipfile.ZipFile(os.path.join(folder, "2024-05-03--21-00-00.zip"), "w") as zf:
        zf.writestr("metadata.json", meta(1200))
        zf.writestr("c0.flashback", b"\xd7\x80\xe8\x84\x00\x00\x00\x00\x00")  # header, no actions, no clock

    raw = os.path.join(folder, "2024-05-03--22-00-00")
    os.makedirs(raw, exist_ok=True)
    with open(os.path.join(raw, "metadata.json"), "w") as fh:
        fh.write(meta(400))
    with open(os.path.join(raw, "c0.flashback"), "wb") as fh:
        write_flashback_chunk(fh, int(datetime(2024, 5, 3, 20, 0, 0, tzinfo=timezone.utc).timestamp() * 1000))

    tmp = os.path.join(root, "recordings", "chunks", "base", "2024-05-03--22-30-00.mcpr.tmp")
    os.makedirs(tmp, exist_ok=True)
    with open(os.path.join(tmp, "metaData.json"), "w") as fh:
        json.dump({"date": int(datetime(2024, 5, 3, 20, 30, tzinfo=timezone.utc).timestamp() * 1000),
                   "duration": 60000, "serverName": "Friends SMP", "mcversion": "1.21.1", "players": []}, fh)


def build_server_26_1(root: str) -> str:
    """The same fake server, but in the Minecraft 26.1 world layout, with a ServerReplay
    3.4.1-style Flashback replay (arcade_replay_meta.json, no real-time clock, 1-tick length,
    folder "Recordings for_ Steve (<uuid>)")."""
    import shutil as _sh
    build_server(root)
    w = os.path.join(root, "world")
    os.makedirs(os.path.join(w, "players"))
    for old, new in (("stats", "players/stats"), ("advancements", "players/advancements"), ("playerdata", "players/data")):
        _sh.move(os.path.join(w, old), os.path.join(w, new))
    os.makedirs(os.path.join(w, "dimensions", "minecraft", "overworld"))
    _sh.move(os.path.join(w, "region"), os.path.join(w, "dimensions", "minecraft", "overworld", "region"))
    os.makedirs(os.path.join(w, "dimensions", "minecraft", "the_nether"))
    _sh.move(os.path.join(w, "DIM-1", "region"), os.path.join(w, "dimensions", "minecraft", "the_nether", "region"))
    _sh.rmtree(os.path.join(w, "DIM-1"))
    _write(os.path.join(w, "level.dat"), nbt.dumps({"Data": {
        "LevelName": "Friends SMP", "Time": Typed(TAG_LONG, 20 * 3600 * 5), "GameType": 0,
        "Version": {"Name": "26.1.2", "Id": 4790}, "DataVersion": 4790,
        "spawn": {"dimension": "minecraft:overworld", "pos": Typed(11, [8, 70, -16]), "yaw": Typed(TAG_FLOAT, 0.0),
                  "pitch": Typed(TAG_FLOAT, 0.0)},
        "difficulty_settings": {"difficulty": "hard", "locked": Typed(1, 0)}}}), "wb")
    _write(os.path.join(w, "data", "minecraft", "world_gen_settings.dat"),
           nbt.dumps({"data": {"seed": Typed(TAG_LONG, 4242), "generate_structures": Typed(1, 1)}, "DataVersion": 4790}), "wb")
    _write(os.path.join(w, "data", "minecraft", "game_rules.dat"),
           nbt.dumps({"data": {"minecraft:keep_inventory": Typed(1, 1)}, "DataVersion": 4790}), "wb")
    _write(os.path.join(w, "data", "minecraft", "weather.dat"),
           nbt.dumps({"data": {"raining": Typed(1, 1), "thundering": Typed(1, 0)}}), "wb")
    # ServerReplay 3.4.1 Flashback replay: saved 2024-05-03 18:10:00 UTC, file name 20:05:00 server time (UTC+2)
    folder = os.path.join(root, "recordings", "players", f"Recordings for_ Steve ({STEVE})")
    os.makedirs(folder, exist_ok=True)
    save_ms = int(datetime(2024, 5, 3, 18, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
    with zipfile.ZipFile(os.path.join(folder, "2024-05-03--20-05-00.zip"), "w") as zf:
        zf.writestr("metadata.json", json.dumps({"uuid": "5f1c0f7e-0000-4000-8000-000000000001", "name": "Unnamed",
                                                 "version_string": "26.1.2", "world_name": "Main_World",
                                                 "data_version": 4790, "protocol_version": 775, "total_ticks": 1,
                                                 "markers": {}, "chunks": {"c0.flashback": {"duration": 1, "forcePlaySnapshot": False}}}))
        zf.writestr("c0.flashback", b"\xd7\x80\xe8\x84\x00\x00\x00\x00\x00")
        zf.writestr("arcade_replay_meta.json", json.dumps({"name": "Steve", "epoch_time_ms": save_ms,
                                                           "accumulated_paused_time": "00:01:00"}))
    # A recording with a sane length: start = save - length - paused
    with zipfile.ZipFile(os.path.join(folder, "2024-05-03--20-30-00.zip"), "w") as zf:
        zf.writestr("metadata.json", json.dumps({"uuid": "5f1c0f7e-0000-4000-8000-000000000002", "name": "Unnamed",
                                                 "version_string": "26.1.2", "world_name": "Main_World", "total_ticks": 2400,
                                                 "markers": {}, "chunks": {"c0.flashback": {"duration": 2400, "forcePlaySnapshot": False}}}))
        zf.writestr("c0.flashback", b"\xd7\x80\xe8\x84\x00\x00\x00\x00\x00")
        zf.writestr("arcade_replay_meta.json", json.dumps({
            "name": "Steve", "epoch_time_ms": int(datetime(2024, 5, 3, 18, 33, 0, tzinfo=timezone.utc).timestamp() * 1000),
            "accumulated_paused_time": "00:01:00"}))
    return root


def add_position_replay(root: str) -> str:
    """A ServerReplay-style Flashback replay of Alex WITH positions (move_entities).

    recordings/players/<alex-uuid>/2024-05-03--20-05-30.zip: no clock, so the start comes from
    the file name: 20:05:30 server time (UTC+2) = 18:05:30 UTC; 2400 ticks (2 min).
    Alex (entity 7) is created at (110, 70, 0), walks east inside x 110..116 for ticks 1-600
    (= the first 30 s), then is at (400, 70, 0) from tick 601. A zombie (entity 99) moves
    nearby-ish at (500, 64, 500) and must not be mistaken for Alex.
    """
    from mcsm.paths import write_test_chunk
    import io as _io
    folder = os.path.join(root, "recordings", "players", ALEX)
    os.makedirs(folder, exist_ok=True)
    ticks = []
    for t in range(1, 2401):
        entries = [("minecraft:overworld", 99, 500.0, 64.0, 500.0 + t * 0.01)]
        if t <= 600:
            entries.append(("minecraft:overworld", 7, 110.0 + t * 0.01, 70.0, 0.0))
        elif t == 601:
            entries.append(("minecraft:overworld", 7, 400.0, 70.0, 0.0))
        ticks.append(entries)
    buf = _io.BytesIO()
    write_test_chunk(buf, ticks, local_player=(ALEX, 110.0, 70.0, 0.0))
    path = os.path.join(folder, "2024-05-03--20-05-30.zip")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("metadata.json", json.dumps({"uuid": "5f1c0f7e-0000-4000-8000-0000000000a1", "name": "Unnamed",
                                                 "version_string": "26.1.2", "world_name": "Main_World",
                                                 "total_ticks": 2400,
                                                 "chunks": {"c0.flashback": {"duration": 2400, "forcePlaySnapshot": False}}}))
        zf.writestr("c0.flashback", buf.getvalue())
    return path
