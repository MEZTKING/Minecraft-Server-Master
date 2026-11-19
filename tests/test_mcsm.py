"""End-to-end and unit tests. Run with ``python -m unittest`` or ``pytest``."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
import zipfile
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixtures import ALEX, REPLAY_START_MS, STEVE, build_server, write_click_track  # noqa: E402,F401

from mcsm import cli, logs, models as m, music, nbt, sync  # noqa: E402
from mcsm.analyzer import analyze  # noqa: E402
from mcsm.exporters import data as dx, markers as mx, report  # noqa: E402
from mcsm.timeutil import LogClock, parse_duration, parse_tz  # noqa: E402

UTC = timezone.utc


def utc(*a):
    return datetime(*a, tzinfo=UTC)


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mcsm-test-")
        cls.root = build_server(os.path.join(cls.tmp, "server"))
        cls.data = analyze(cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestNBT(unittest.TestCase):
    def test_roundtrip(self):
        src = {"a": 1, "b": "x", "c": [1.5, 2.5], "d": {"e": nbt.Typed(nbt.TAG_LONG, 2**40)},
               "f": nbt.Typed(nbt.TAG_INT_ARRAY, [1, -2, 3]), "g": []}
        for compress in (True, False):
            out = nbt.loads(nbt.dumps(src, compress=compress))
            self.assertEqual(out, {"a": 1, "b": "x", "c": [1.5, 2.5], "d": {"e": 2**40}, "f": [1, -2, 3], "g": []})

    def test_bad_data(self):
        with self.assertRaises(nbt.NBTError):
            nbt.loads(b"\x0a\x00")


class TestTime(unittest.TestCase):
    def test_parse_tz(self):
        self.assertEqual(parse_tz("+02:00").utcoffset(None), timedelta(hours=2))
        self.assertEqual(parse_tz("UTC-5").utcoffset(None), timedelta(hours=-5))
        self.assertEqual(parse_tz("UTC").utcoffset(None), timedelta(0))
        london = parse_tz("Europe/London")
        self.assertEqual(datetime(2024, 7, 1, tzinfo=london).utcoffset(), timedelta(hours=1))

    def test_clock_from_samples_handles_dst(self):
        clock = LogClock(samples=[(date(2024, 3, 20), timedelta(hours=1)), (date(2024, 4, 10), timedelta(hours=2))])
        self.assertEqual(clock.to_utc(datetime(2024, 3, 21, 12)), utc(2024, 3, 21, 11))
        self.assertEqual(clock.to_utc(datetime(2024, 4, 9, 12)), utc(2024, 4, 9, 10))

    def test_parse_duration(self):
        self.assertEqual(parse_duration("90"), 90)
        self.assertEqual(parse_duration("1:30"), 90)
        self.assertEqual(parse_duration("1:00:01.5"), 3601.5)
        self.assertEqual(parse_duration("2m5s"), 125)


class TestLogLines(unittest.TestCase):
    def classify(self, line, known=("steve",)):
        parts = logs._split_line(line)
        self.assertIsNotNone(parts, line)
        raw = logs.RawLine(datetime(2024, 1, 1), parts[2], parts[3], parts[4], 1)
        return logs.classify(raw, set(known))

    def test_formats(self):
        for line in ["[20:00:00] [Server thread/INFO]: Steve joined the game",
                     "[20:00:00] [Server thread/INFO] (Minecraft) Steve joined the game",
                     "[20:00:00 INFO]: Steve joined the game",
                     "[01May2024 20:00:00.123] [Server thread/INFO] [minecraft/MinecraftServer]: Steve joined the game"]:
            self.assertEqual(self.classify(line)[:2], ("join", "Steve"))

    def test_messages(self):
        c = self.classify
        self.assertEqual(c("[20:00:00 INFO]: <Steve> hi <3")[0:3], ("chat", "Steve", "hi <3"))
        self.assertEqual(c("[20:00:00 INFO]: Steve issued server command: /home base")[0], "command")
        d = c("[20:00:00 INFO]: Steve was shot by Skeleton using [Bow of Doom]")
        self.assertEqual((d[0], d[3]["killer"], d[3]["weapon"]), ("death", "Skeleton", "Bow of Doom"))
        self.assertEqual(c("[20:00:00 INFO]: Steve has completed the challenge [Monster Hunter]")[3]["kind"], "challenge")
        self.assertEqual(c("[20:00:00 INFO]: §eSteve left the game")[0], "leave")
        # Unknown names must not be taken as deaths ("Server drowned" is not a player).
        self.assertIsNone(c("[20:00:00 INFO]: Nobody drowned"))
        self.assertEqual(c("[20:00:00 INFO]: Steve drowned")[0], "death")


class TestAnalyze(ServerTestCase):
    def test_timezone_learned(self):
        self.assertIn("UTC+02:00", self.data.timezone_info)

    def test_log_dating_and_midnight(self):
        chats = [e for e in self.data.events if e.type == "chat"]
        self.assertEqual(chats[0].time, utc(2024, 5, 1, 18, 5))            # 20:05 local on 05-01
        self.assertEqual(chats[1].time, utc(2024, 5, 1, 21, 59, 50))       # 23:59:50 local
        self.assertEqual(chats[2].time, utc(2024, 5, 1, 22, 0, 10))        # 00:00:10 local 05-02
        self.assertEqual(chats[3].time, utc(2024, 5, 3, 18, 2))            # latest.log via mtime
        self.assertEqual({f.path for f in self.data.log_files}, {"logs/2024-05-02-1.log.gz", "logs/latest.log"})

    def test_advancement_log_matches_json(self):
        adv = next(e for e in self.data.events if e.type == "advancement" and e.data["title"] == "Stone Age")
        steve = self.data.player("steve")
        self.assertEqual(adv.time, steve.advancements["story/mine_stone"]["completed"])
        self.assertNotIn("recipes/misc/torch", steve.advancements)

    def test_join_position_and_no_ip(self):
        join = next(e for e in self.data.events if e.type == "join")
        self.assertEqual(join.data["position"], (10.5, 64.0, -3.5))
        self.assertNotIn("203.0.113.5", json.dumps(m.to_jsonable(self.data.events)))

    def test_sessions(self):
        s = [(x.player, x.end_reason, x.seconds) for x in self.data.sessions]
        self.assertIn(("Alex", "leave", 24 * 60), s)
        self.assertIn(("Steve", "server_stop", (4 * 3600 + 4 * 60 + 50)), s)
        self.assertIn(("Steve", "log_end", 150), s)

    def test_players(self):
        steve, alex = self.data.player("Steve"), self.data.player("alex")
        self.assertTrue(steve.is_op)
        self.assertEqual(steve.stats["custom"]["play_time"], 216000)
        self.assertEqual(alex.stats["custom"]["play_time"], 36000)          # legacy format converted
        self.assertEqual(alex.stats["mined"]["stone"], 40)
        self.assertEqual(alex.deaths_logged, 1)
        self.assertEqual(steve.playerdata["inventory"]["torch"], 74)
        self.assertEqual(steve.playerdata["ender_chest"], {"elytra": 1})
        self.assertEqual(steve.playerdata["last_death"]["dimension"], "the_nether")
        self.assertEqual(steve.chat_messages, 4)

    def test_world(self):
        w = self.data.world
        self.assertEqual((w["level_name"], w["seed"], w["difficulty"]), ("Friends SMP", -123456789012345, "hard"))
        self.assertEqual(w["dimensions"]["overworld"]["region_files"], 1)
        self.assertIn("the_nether", w["dimensions"])
        self.assertEqual(self.data.server_properties["rcon.password"], "********")

    def test_replay_and_crash(self):
        r = self.data.replays[0]
        self.assertEqual(r.recorded_player, "Steve")
        self.assertEqual(r.player_names, ["Steve"])
        self.assertEqual(r.start, utc(2024, 5, 3, 18))
        types = {e.type for e in self.data.events}
        self.assertTrue({"replay_marker", "crash", "server_ready", "server_chat", "command"} <= types)


class TestZipInput(ServerTestCase):
    def test_zip_with_wrapper_folder(self):
        zpath = os.path.join(self.tmp, "download.zip")
        with zipfile.ZipFile(zpath, "w") as zf:
            for cur, _d, files in os.walk(self.root):
                for f in files:
                    full = os.path.join(cur, f)
                    zf.write(full, os.path.join("wrapper", os.path.relpath(full, self.root)))
        from mcsm.source import open_server
        with open_server(zpath) as root:
            d = analyze(root)
        self.assertEqual(len(d.players), 2)
        self.assertEqual(len([e for e in d.events if e.type == "chat"]), 4)


class TestSync(ServerTestCase):
    def test_markers_from_replay(self):
        r = self.data.replays[0]
        tl = sync.timeline_from_replay(r, render_start=30)
        mk = sync.build_markers(self.data.events, tl, replay_path=r.path)
        got = [(round(x.seconds), x.type) for x in mk]
        # join 20:00:30 local = 30 s into replay -> 0 s into a render starting at 30 s
        self.assertEqual(got, [(0, "join"), (30, "death"), (60, "replay_marker"), (90, "chat"), (150, "advancement")])
        self.assertEqual(tl.length, 570)

    def test_speed(self):
        tl = sync.VideoTimeline(utc(2024, 5, 3, 18), None, speed=2.0)
        self.assertEqual(tl.to_video(utc(2024, 5, 3, 18, 1)), 30)

    def test_writers(self):
        out = os.path.join(self.tmp, "sync")
        tl = sync.timeline_from_replay(self.data.replays[0])
        mk = sync.build_markers(self.data.events, tl)
        files = mx.write_all(mk, out, "test", fps=30)
        self.assertEqual(len(files), 6)
        srt = open(os.path.join(out, "test.srt"), encoding="utf-8").read()
        self.assertIn("00:00:30,000 --> 00:00:34,000\nSteve joined", srt)
        edl = open(os.path.join(out, "test.edl"), encoding="utf-8").read()
        self.assertIn("01:00:30:00 01:00:30:01", edl)
        self.assertIn("|C:ResolveColorRed |M:Steve fell from a high place |D:1", edl)
        ch = open(os.path.join(out, "test.chapters.txt"), encoding="utf-8").read().splitlines()
        self.assertEqual(ch[0], "0:00 Start")

    def test_timecode(self):
        self.assertEqual(mx.timecode(61.5, 30), "00:01:01:15")
        self.assertEqual(mx.timecode(0, 60, start_hour=1), "01:00:00:00")


class TestMusic(unittest.TestCase):
    def test_grid(self):
        g = music.BeatGrid(bpm=120, first_beat=0.25, music_start=10, song_length=5)
        times = g.beat_times()
        self.assertEqual(times[0], (10.25, 0))
        self.assertEqual(len(times), 10)                       # 0.25 .. 4.75
        self.assertEqual([mk.type for mk in g.markers()][:5], ["bar", "beat", "beat", "beat", "bar"])

    def test_snap_and_align(self):
        g = music.BeatGrid(bpm=60)                             # beat every second
        mk = [sync.Marker(seconds=3.3, label="a", type="death"), sync.Marker(seconds=7.6, label="b", type="chat")]
        snapped = music.snap_markers(mk, g, "beat")
        self.assertEqual([(x.seconds, x.snapped_from) for x in snapped], [(3, 3.3), (8, 7.6)])
        self.assertEqual(music.snap_markers(mk, g, "beat", tolerance=0.35)[1].seconds, 7.6)
        self.assertEqual(music.music_start_for_alignment(62.5, 100), 37.5)

    def test_detect_beats(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "click.wav")
            write_click_track(p, bpm=120, first_beat=0.25)
            g = music.detect_beats(p)
        self.assertAlmostEqual(g.bpm, 120, delta=1.0)
        # first beat may be found at any beat of the grid; check phase only
        phase = (g.first_beat - 0.25) % 0.5
        self.assertLess(min(phase, 0.5 - phase), 0.04)


class TestExports(ServerTestCase):
    def test_sqlite_json_csv_report(self):
        db = os.path.join(self.tmp, "s.sqlite")
        dx.write_sqlite(self.data, db)
        con = sqlite3.connect(db)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM events WHERE type='death'").fetchone()[0], 2)
        self.assertEqual(con.execute("SELECT value FROM stats WHERE player='Steve' AND key='jump'").fetchone()[0], 321)
        con.close()
        js = os.path.join(self.tmp, "s.json")
        dx.write_json(self.data, js)
        self.assertEqual(len(json.load(open(js, encoding="utf-8"))["players"]), 2)
        self.assertEqual(len(dx.write_csv_dir(self.data, os.path.join(self.tmp, "csv"))), 4)
        html = report.render(self.data)
        self.assertIn("Friends SMP", html)
        self.assertNotIn("</script><", html.split('id="data"')[1].split("</script>")[0])

    def test_report_escapes_script_close(self):
        ev = m.Event(utc(2024, 1, 1), "chat", "Steve", "</script><b>x")
        d = m.ServerData(root="x", events=[ev])
        html = report.render(d)
        self.assertNotIn("</script><b>", html)


class TestCLI(ServerTestCase):
    def run_cli(self, *argv):
        from io import StringIO
        from contextlib import redirect_stdout, redirect_stderr
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_commands(self):
        self.assertEqual(self.run_cli("summary", self.root)[0], 0)
        code, out, _ = self.run_cli("events", self.root, "--type", "death")
        self.assertEqual(out.count("\n"), 2)
        code, out, _ = self.run_cli("replays", self.root)
        self.assertIn("[1] recordings/players/Steve", out)
        rep = os.path.join(self.tmp, "r.html")
        self.assertEqual(self.run_cli("report", self.root, "-o", rep)[0], 0)
        self.assertTrue(os.path.getsize(rep) > 1000)

    def test_sync_with_music(self):
        out = os.path.join(self.tmp, "cli-sync")
        code, text, err = self.run_cli("sync", self.root, "--replay", "1", "-o", out, "--bpm", "120",
                                       "--music-align", "0:10=#2", "--snap", "beat")
        self.assertEqual(code, 0, err)
        self.assertIn("Music starts: +50.000s", text)  # marker #2 (death) at 60s
        self.assertTrue(os.path.exists(os.path.join(out, "2024-05-03--18-00-00.beats.edl")))
        rows = json.load(open(os.path.join(out, "2024-05-03--18-00-00.json"), encoding="utf-8"))["markers"]
        anchor = next(r for r in rows if r["type"] == "death")
        self.assertEqual((anchor["video_seconds"], anchor["snapped_from_seconds"]), (60.0, ""))

    def test_explicit_tz_override(self):
        d = analyze(self.root, tz=parse_tz("UTC"))
        chat = next(e for e in d.events if e.type == "chat")
        self.assertEqual(chat.time, utc(2024, 5, 1, 20, 5))


if __name__ == "__main__":
    unittest.main()


def _have_display() -> bool:
    try:
        import tkinter
        r = tkinter.Tk()
        r.destroy()
        del r
        import gc
        gc.collect()  # collect Tk objects on the main thread (tkinter forbids it elsewhere)
        return True
    except Exception:  # noqa: BLE001 - no tkinter or no display
        return False


@unittest.skipUnless(_have_display(), "needs tkinter and a display")
class TestGUI(ServerTestCase):
    def test_load_and_sync(self):
        import time
        import tkinter as tk
        from mcsm import gui
        gui.open_path = lambda p: None
        root = tk.Tk()
        try:
            app = gui.App(root)
            app.server_var.set(self.root)
            app._load()
            deadline = time.time() + 30
            while (app.data is None or app.busy) and time.time() < deadline:
                root.update()
                time.sleep(0.05)
            self.assertIsNotNone(app.data)
            self.assertIn("Steve", app.replay_combo.get())
            out = os.path.join(self.tmp, "gui-sync")
            app.outdir_var.set(out)
            app.bpm_var.set("120")
            app._sync()
            deadline = time.time() + 30
            while app.busy and time.time() < deadline:
                root.update()
                time.sleep(0.05)
            self.assertTrue(os.path.exists(os.path.join(out, "2024-05-03--18-00-00.srt")),
                            app.log_text.get("1.0", "end")[-1500:])
        finally:
            root.destroy()
            del root
            import gc
            gc.collect()

    def test_live_tab_against_fake_rcon(self):
        import time
        import tkinter as tk
        import urllib.request
        from fake_rcon import FakeRconServer
        from mcsm import gui
        gui.webbrowser.open = lambda url: None
        srv = FakeRconServer(password="pw")
        root = tk.Tk()
        try:
            app = gui.App(root)
            app.live_host_var.set(f"127.0.0.1:{25565}")   # game port is stripped
            app.live_port_var.set(str(srv.port))
            app.live_pw_var.set("pw")
            app._live_start(demo=False)
            deadline = time.time() + 15
            while app.live is None and time.time() < deadline:
                root.update()
                time.sleep(0.05)
            self.assertIsNotNone(app.live, app.log_text.get("1.0", "end"))
            while app.live.status != "live" and time.time() < deadline:
                time.sleep(0.05)
            state = json.loads(urllib.request.urlopen(app.live_url + "api/state", timeout=5).read())
            self.assertEqual({p["name"] for p in state["players"]}, {"Steve", "Alex"})
            app._live_stop()
            self.assertIsNone(app.live)
        finally:
            srv.close()
            root.destroy()
            del root
            import gc
            gc.collect()


class TestFlashbackAndUnfinished(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fixtures import add_extra_replays
        cls.tmp = tempfile.mkdtemp(prefix="mcsm-fb-")
        cls.root = build_server(os.path.join(cls.tmp, "server"))
        add_extra_replays(cls.root)
        # A world backup zip outside the recordings folder must NOT be scanned as a replay.
        with zipfile.ZipFile(os.path.join(cls.root, "backup.zip"), "w") as zf:
            zf.writestr("metadata.json", "{}")
            zf.writestr("c0.flashback", b"")
        cls.data = analyze(cls.root)
        cls.by_name = {os.path.basename(r.path.rstrip("/")): r for r in cls.data.replays}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_all_found(self):
        self.assertEqual(set(self.by_name), {"2024-05-03--18-00-00.mcpr", "2024-05-03--20-05-00.zip",
                                             "2024-05-03--21-00-00.zip", "2024-05-03--22-00-00",
                                             "2024-05-03--22-30-00.mcpr.tmp"})

    def test_flashback_rtc_start(self):
        r = self.by_name["2024-05-03--20-05-00.zip"]
        self.assertEqual((r.format, r.start_source, r.unfinished), ("flashback", "real-time-clock", False))
        self.assertEqual(r.start, utc(2024, 5, 3, 18, 5))           # 18:05:01 minus 20 ticks
        self.assertEqual(r.duration_ms, 180_000)
        self.assertEqual(r.recorded_player, "Steve")                  # uuid folder -> name
        self.assertEqual(r.markers, [{"offset_ms": 30_000, "name": "Dragon fight", "position": [1.0, 70.0, 2.0],
                                      "dimension": "minecraft:the_end"}])
        mk = [e for e in self.data.events if e.type == "replay_marker" and e.text == "Dragon fight"]
        self.assertEqual(mk[0].time, utc(2024, 5, 3, 18, 5, 30))

    def test_flashback_name_fallback_uses_server_tz(self):
        r = self.by_name["2024-05-03--21-00-00.zip"]
        self.assertEqual((r.start_source, r.start), ("file-name", utc(2024, 5, 3, 19, 0)))

    def test_unfinished(self):
        fb = self.by_name["2024-05-03--22-00-00"]
        self.assertEqual((fb.format, fb.unfinished, fb.start), ("flashback", True, utc(2024, 5, 3, 20)))
        rm = self.by_name["2024-05-03--22-30-00.mcpr.tmp"]
        self.assertEqual((rm.format, rm.unfinished, rm.start), ("replaymod", True, utc(2024, 5, 3, 20, 30)))

    def test_sync_flashback(self):
        r = self.by_name["2024-05-03--20-05-00.zip"]
        tl = sync.timeline_from_replay(r)
        mk = sync.build_markers(self.data.events, tl, replay_path=r.path)
        self.assertEqual([(round(x.seconds), x.label) for x in mk], [(30, "Marker: Dragon fight")])


class TestRconAndLive(unittest.TestCase):
    def setUp(self):
        from fake_rcon import FakeRconServer
        self.srv = FakeRconServer(password="s3cret")

    def tearDown(self):
        self.srv.close()

    def test_wrong_password(self):
        from mcsm.rcon import RconClient, RconError
        with self.assertRaises(RconError):
            RconClient("127.0.0.1", self.srv.port, "nope").connect()

    def test_connection_refused_message(self):
        from mcsm.rcon import RconClient, RconError
        with self.assertRaisesRegex(RconError, "enable-rcon"):
            RconClient("127.0.0.1", 1, "x", timeout=1).connect()

    def test_pipelined_commands(self):
        from mcsm.rcon import RconClient
        with RconClient("127.0.0.1", self.srv.port, "s3cret") as c:
            replies = c.commands(["list", "data get entity Steve Pos", "data get entity Nobody Pos"])
        self.assertIn("Steve, Alex", replies[0])
        self.assertEqual(replies[1], "Steve has the following entity data: [12.5d, 64.0d, -3.25d]")
        self.assertEqual(replies[2], "No entity was found")

    def test_source_poll(self):
        from mcsm.live import RconSource
        from mcsm.rcon import RconClient
        src = RconSource(RconClient("127.0.0.1", self.srv.port, "s3cret"))
        states = {s.name: s for s in src.poll()}
        st = states["Steve"]
        self.assertEqual((st.x, st.y, st.z, st.yaw, st.pitch), (12.5, 64.0, -3.25, 90.0, 10.0))
        self.assertEqual((st.health, st.food, st.xp_level, st.game_mode), (18.5, 17, 30, "survival"))
        self.assertEqual((states["Alex"].dimension, states["Alex"].game_mode), ("minecraft:the_nether", "creative"))
        # Second poll skips the slow fields but keeps their last values.
        n = len(self.srv.commands)
        src.poll()
        self.assertEqual(len(self.srv.commands) - n, 1 + 2 * 3)
        # A player who leaves between "list" and "data get" is skipped, not an error.
        self.srv.vanish_after_list = {"Alex"}
        self.assertEqual([s.name for s in src.poll()], ["Steve"])
        src.close()

    def test_parsers(self):
        from mcsm.live import parse_list
        self.assertEqual(parse_list("There are 0 of a max of 20 players online: "), [])
        self.assertEqual(parse_list("There are 2/20 players online:\n§aSteve§r, Alex"), ["Steve", "Alex"])
        self.assertEqual(parse_list("There are 1 of a max of 5 players online: .BedrockGuy"), [".BedrockGuy"])

    def test_live_map_http_events_and_recording(self):
        import urllib.request
        from mcsm.live import LiveMap, RconSource
        from mcsm.rcon import RconClient
        rec = tempfile.mkdtemp()
        live = LiveMap(RconSource(RconClient("127.0.0.1", self.srv.port, "s3cret")), interval=0.2, record_dir=rec)
        url = live.start(port=18800)
        try:
            deadline = time.time() + 5
            while live.status != "live" and time.time() < deadline:
                time.sleep(0.05)
            self.srv.players["Steve"]["pos"] = [20.0, 64.0, -3.25]
            del self.srv.players["Alex"]
            time.sleep(0.6)
            state = json.loads(urllib.request.urlopen(url + "api/state", timeout=5).read())
            self.assertEqual([p["name"] for p in state["players"]], ["Steve"])
            self.assertEqual([(e["type"], e["player"]) for e in state["events"]][-1], ("leave", "Alex"))
            self.assertGreaterEqual(len(state["trails"]["Steve"]["points"]), 2)
            page = urllib.request.urlopen(url, timeout=5).read().decode()
            self.assertIn("three.module.min.js", page)
            self.assertEqual(urllib.request.urlopen(url + "vendor/three.module.min.js", timeout=5).status, 200)
            with self.assertRaises(Exception):
                urllib.request.urlopen(url + "vendor/../../live.py", timeout=5)
            files = os.listdir(rec)
            self.assertEqual(len(files), 1)
            first = json.loads(open(os.path.join(rec, files[0]), encoding="utf-8").readline())
            self.assertEqual({p["name"] for p in first["players"]}, {"Steve", "Alex"})
        finally:
            live.stop()
            shutil.rmtree(rec, ignore_errors=True)

    def test_cli_reads_password_from_server_properties(self):
        from mcsm.cli import build_parser, start_live
        tmp = tempfile.mkdtemp()
        try:
            root = build_server(os.path.join(tmp, "srv"))
            with open(os.path.join(root, "server.properties"), "a") as fh:
                fh.write(f"enable-rcon=true\nrcon.port={self.srv.port}\n")
            props = open(os.path.join(root, "server.properties")).read().replace("rcon.password=hunter2", "rcon.password=s3cret")
            open(os.path.join(root, "server.properties"), "w").write(props)
            args = build_parser().parse_args(["live", root, "--host", "127.0.0.1", "--http-port", "18820", "--no-browser"])
            live, url = start_live(args, root)
            try:
                self.assertTrue(url.startswith("http://127.0.0.1:"))
                self.assertEqual(live.title, "Friends SMP")
            finally:
                live.stop()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestTerrain(unittest.TestCase):
    def test_region_roundtrip_and_colours(self):
        import math as _m
        from mcsm import terrain
        h = lambda x, z: 62 + int(10 * _m.sin(x / 20) * _m.cos(z / 25))  # noqa: E731
        b = lambda x, y, z: "minecraft:water" if y < 63 else "minecraft:grass_block"  # noqa: E731
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.-1.2.mca")
            terrain.write_region(p, {(cx, cz): terrain.make_chunk(cx - 32, cz + 64, h, b)
                                     for cx in range(3) for cz in range(3)})
            for step in (1, 2, 4):
                t = terrain.read_region_surface(p, step)
                self.assertEqual((t.rx, t.rz), (-1, 2))
                for z in range(0, 48, step):
                    for x in range(0, 48, step):
                        self.assertEqual(t.heights[(z // step) * t.size + x // step], h(x - 512, z + 1024))
        self.assertEqual(terrain.block_colour("minecraft:red_concrete"), (160, 39, 34))
        self.assertEqual(terrain.block_colour("birch_log"), terrain.block_colour("oak_log"))
        self.assertEqual(terrain.auto_step(4), 1)
        self.assertEqual(terrain.auto_step(400), 8)
