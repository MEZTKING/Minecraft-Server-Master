"""End-to-end and unit tests. Run with ``python -m unittest`` or ``pytest``."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixtures import ALEX, REPLAY_START_MS, STEVE, build_server, write_click_track  # noqa: E402

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
