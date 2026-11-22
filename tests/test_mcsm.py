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

# Caches (terrain tiles, decoded paths) default to %LOCALAPPDATA%\mcsm: keep tests out of the real one.
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="mcsm-test-appdata-")

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

    def test_flashback_tab_timelapse(self):
        import time
        import tkinter as tk
        from fixtures import add_extra_replays
        from mcsm import gui
        tmp = tempfile.mkdtemp()
        root_dir = build_server(os.path.join(tmp, "srv"))
        add_extra_replays(root_dir)
        fb = os.path.join(tmp, "flashback")
        os.makedirs(os.path.join(fb, "replays"))
        gui._documents_dir = lambda: tmp
        dialogs = []
        gui.messagebox.showinfo = gui.messagebox.showerror = lambda *a, **k: dialogs.append(a)  # never block
        tracks = os.path.join(tmp, "tracks")
        os.makedirs(tracks)
        t0 = datetime(2024, 5, 3, 18, 5, tzinfo=UTC)
        with open(os.path.join(tracks, "t.jsonl"), "w") as fh:
            for sec in range(40, 100):
                fh.write(json.dumps({"t": int((t0 + timedelta(seconds=sec)).timestamp() * 1000), "players": [
                    {"name": "Steve", "x": -50, "y": 70, "z": -60, "dimension": "minecraft:overworld"}]}) + "\n")
        root = tk.Tk()
        try:
            app = gui.App(root)
            app.server_var.set(root_dir)
            app._load()
            deadline = time.time() + 30
            while (app.data is None or app.busy) and time.time() < deadline:
                root.update()
                time.sleep(0.05)
            self.assertIn("Flashback", app.fb_combo.get())
            app.fb_dir_var.set(fb)
            app.fb_install_var.set(True)
            app.tl_area_var.set("-100,-100,0,0")   # negative coordinates must work
            app.tl_tracks_var.set(tracks)
            app.tl_length_var.set("0:30")
            app._fb_timelapse(dry=False)
            deadline = time.time() + 30
            while app.busy and time.time() < deadline:
                root.update()
                time.sleep(0.05)
            log = app.log_text.get("1.0", "end")
            states = os.listdir(os.path.join(fb, "editor_states")) if os.path.isdir(os.path.join(fb, "editor_states")) else []
            self.assertEqual(dialogs, [])
            self.assertEqual(len(states), 1, log[-2000:])
            assert_valid_flashback_editor_state(self, json.load(open(os.path.join(fb, "editor_states", states[0]))))
            self.assertTrue(any(f.endswith(".time.srt") for f in os.listdir(os.path.join(tmp, "mcsm-flashback"))))
        finally:
            root.destroy()
            del root
            import gc
            gc.collect()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_multi_backup_load(self):
        import time
        import tkinter as tk
        from mcsm import gui
        tmp = tempfile.mkdtemp()
        a = _zip_dir(build_server(os.path.join(tmp, "a")), os.path.join(tmp, "a.zip"))
        b = _zip_dir(build_server(os.path.join(tmp, "b")), os.path.join(tmp, "b.zip"))
        gui.messagebox.showinfo = gui.messagebox.showerror = lambda *x, **k: None
        root = tk.Tk()
        try:
            app = gui.App(root)
            app.server_var.set(a + gui.SEP + b)
            app._load()
            deadline = time.time() + 30
            while (app.data is None or app.busy) and time.time() < deadline:
                root.update()
                time.sleep(0.05)
            self.assertEqual(len(app.data.backups), 2, app.log_text.get("1.0", "end")[-1500:])
            self.assertIn("2 backups merged", app.status_var.get())
            # the replay is still extractable after loading (backups kept open)
            r = next(r for r in app.data.replays if r.path.endswith(".mcpr"))
            self.assertTrue(os.path.isfile(cli._replay_file(app.data, r)))
            app.server_stack.close()
        finally:
            root.destroy()
            del root
            import gc
            gc.collect()
            shutil.rmtree(tmp, ignore_errors=True)

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

    def test_history_tab(self):
        import time
        import tkinter as tk
        import urllib.request
        from mcsm import gui
        gui.webbrowser.open = lambda url: None
        tmp = tempfile.mkdtemp()
        root = tk.Tk()
        try:
            app = gui.App(root)
            app.server_var.set(build_server(os.path.join(tmp, "s")))
            app._load()
            deadline = time.time() + 30
            while (app.data is None or app.busy) and time.time() < deadline:
                root.update()
                time.sleep(0.05)
            app.hist_replays_var.set(False)
            app._history_start()
            while app.history is None and time.time() < deadline:
                root.update()
                time.sleep(0.05)
            self.assertIsNotNone(app.history, app.log_text.get("1.0", "end")[-1500:])
            h = json.loads(urllib.request.urlopen(app.history_url + "api/history", timeout=5).read())
            self.assertIn("Steve", h["players"])
            app._history_stop()
            self.assertIsNone(app.history)
            app.server_stack.close()
        finally:
            root.destroy()
            del root
            import gc
            gc.collect()
            shutil.rmtree(tmp, ignore_errors=True)


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

    def test_fast_reader_matches_full_decode(self):
        from mcsm import terrain
        h = lambda x, z: 60 + (x * 7 + z * 3) % 40  # noqa: E731  (crosses section borders)
        blocks = ["minecraft:stone", "minecraft:sand", "minecraft:oak_log", "minecraft:water", "minecraft:snow"]
        b = lambda x, y, z: blocks[(x + y + z) % len(blocks)]  # noqa: E731
        chunk = terrain.make_chunk(3, -2, h, b)
        chunk["block_entities"] = [{"id": "minecraft:chest", "Items": [{"id": "x", "Count": nbt.Typed(nbt.TAG_BYTE, 1)}]}]
        chunk["sections"][0]["biomes"] = {"palette": ["minecraft:plains"]}       # extra tags to skip
        # 26.3 palette formats (seen in the owner's world): all plain strings, or compounds
        # mixing {"": name} (wrapped plain name) and {"id": name, "properties": {...}}.
        pal0 = chunk["sections"][0]["block_states"]["palette"]
        chunk["sections"][0]["block_states"]["palette"] = [p["Name"] for p in pal0]
        pal1 = chunk["sections"][1]["block_states"]["palette"]
        chunk["sections"][1]["block_states"]["palette"] = (
            [{"": pal1[0]["Name"]}] + [{"id": p["Name"], "properties": {"axis": "y"}} for p in pal1[1:]])
        raw = nbt.dumps(chunk, compress=False)
        fast, slow = terrain._fast_sections(raw), terrain._sections(nbt.loads(raw))
        self.assertEqual((fast[1], fast[2]), (slow[1], slow[2]))
        for y in slow[0]:
            self.assertEqual(fast[0].get(y), slow[0][y])
        names = [n for y in slow[0] for n in slow[0][y][0]]
        self.assertTrue(set(names) <= {"minecraft:air", *blocks}, names)          # no format read as air/garbage
        self.assertGreater(len(set(names) & set(blocks)), 2)
        old = {"Level": {"Sections": [{"Y": nbt.Typed(nbt.TAG_BYTE, 4), "Palette": [{"Name": "minecraft:dirt"}],
                                       "BlockStates": nbt.Typed(nbt.TAG_LONG_ARRAY, [0] * 256)}],
                         "Heightmaps": {"MOTION_BLOCKING": nbt.Typed(nbt.TAG_LONG_ARRAY, [1] * 37)}}}
        raw = nbt.dumps(old, compress=False)                                         # 1.16-1.17 layout
        fast, slow = terrain._fast_sections(raw), terrain._sections(nbt.loads(raw))
        self.assertEqual((fast[0].get(4), fast[1], fast[2]), (slow[0][4], slow[1], slow[2]))

    def test_tile_cache_and_parallel_build(self):
        from mcsm import terrain
        from mcsm.live import LiveMap
        h = lambda x, z: 64 + (x // 8 + z // 8) % 5  # noqa: E731
        b = lambda x, y, z: "minecraft:grass_block"  # noqa: E731
        with tempfile.TemporaryDirectory() as d:
            world = os.path.join(d, "world")
            for rx in range(4):
                terrain.write_region(os.path.join(world, "region", f"r.{rx}.0.mca"),
                                     {(0, 0): terrain.make_chunk(rx * 32, 0, h, b)})
            cache = os.path.join(d, "cache")
            serial = LiveMap(None, d, world, terrain_step=4)
            serial.terrain_cache, serial.terrain_workers = "", 1
            serial._build_terrain()
            par = LiveMap(None, d, world, terrain_step=4)
            par.terrain_cache, par.terrain_workers = cache, 2
            par._build_terrain()
            want = {k: (t.heights, t.colours) for k, t in serial.terrain["minecraft:overworld"].items()}
            got = {k: (t.heights, t.colours) for k, t in par.terrain["minecraft:overworld"].items()}
            self.assertEqual(len(want), 4)
            self.assertEqual(got, want)
            self.assertEqual(par.terrain_progress["minecraft:overworld"], [4, 4])
            self.assertEqual(len([f for f in os.listdir(cache) if f.endswith(".tile")]), 4)
            again = LiveMap(None, d, world, terrain_step=4)                    # served from the cache
            again.terrain_cache = cache
            again._build_terrain()
            self.assertEqual({k: (t.heights, t.colours) for k, t in again.terrain["minecraft:overworld"].items()}, want)
            # a changed region file is read again
            f = os.path.join(world, "region", "r.0.0.mca")
            os.utime(f, ns=(os.stat(f).st_atime_ns, os.stat(f).st_mtime_ns + 5_000_000_000))
            self.assertFalse(terrain.load_tile(terrain.tile_cache_file(f, 4, cache))[0])


def assert_valid_flashback_editor_state(tc, state):
    """Checks every requirement found in Flashback's source (see docs/FLASHBACK.md)."""
    tc.assertIsInstance(state["scenes"], list)
    tc.assertGreater(len(state["scenes"]), 0)
    tc.assertLess(state.get("sceneIndex", 0), len(state["scenes"]))
    for scene in state["scenes"]:
        for key in ("name", "keyframeTracks", "exportStartTicks", "exportEndTicks"):
            tc.assertIn(key, scene)
        tc.assertEqual(scene["history"], {"entries": [], "position": 0})
        for track in scene["keyframeTracks"]:
            tc.assertIn(track["keyframeType"], ("CAMERA", "TIMELAPSE"))
            tc.assertIs(track["enabled"], True)
            ticks = sorted(int(k) for k in track["keyframesByTick"])
            tc.assertTrue(ticks)
            for k, kf in track["keyframesByTick"].items():
                if track["keyframeType"] == "CAMERA":
                    tc.assertEqual(kf["type"], "camera")
                    tc.assertEqual(len(kf["position"]), 3)
                    tc.assertEqual(kf["interpolation_type"], "HOLD")
                else:
                    tc.assertEqual(kf["type"], "timelapse")
                    tc.assertIsInstance(kf["ticks"], int)
            if track["keyframeType"] == "TIMELAPSE":
                outs = [track["keyframesByTick"][str(t)]["ticks"] for t in ticks]
                tc.assertGreaterEqual(len(outs), 2)
                tc.assertEqual(outs, sorted(set(outs)), "output ticks must strictly increase")


class TestFlashbackWriting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fixtures import add_extra_replays
        cls.tmp = tempfile.mkdtemp(prefix="mcsm-fbw-")
        cls.root = build_server(os.path.join(cls.tmp, "server"))
        add_extra_replays(cls.root)
        cls.fbdir = os.path.join(cls.tmp, "flashback")
        os.makedirs(os.path.join(cls.fbdir, "replays"))
        # Steve is in the base 30-60 s and 120-150 s into the Flashback replay (starts 18:05:00 UTC).
        tracks = os.path.join(cls.tmp, "tracks")
        os.makedirs(tracks)
        t0 = datetime(2024, 5, 3, 18, 5, tzinfo=UTC)
        with open(os.path.join(tracks, "2024-05-03.jsonl"), "w") as fh:
            for sec in range(180):
                inside = 30 <= sec <= 60 or 120 <= sec <= 150
                fh.write(json.dumps({"t": int((t0 + timedelta(seconds=sec)).timestamp() * 1000), "players": [
                    {"name": "Steve", "x": 110 if inside else 400, "y": 70, "z": 0, "dimension": "minecraft:overworld"},
                    {"name": "Alex", "x": 110, "y": 70, "z": 0, "dimension": "minecraft:the_nether"}]}) + "\n")
        cls.tracks = tracks

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def src(self):
        return os.path.join(self.root, "recordings", "players", STEVE, "2024-05-03--20-05-00.zip")

    def test_marker_copy(self):
        from mcsm import flashback as fbm
        out = os.path.join(self.tmp, "copy.zip")
        before = open(self.src(), "rb").read()
        meta = fbm.write_replay_copy(self.src(), out, [fbm.FlashbackMarker(600, "Clash", 0xFF0000),
                                                       fbm.FlashbackMarker(100, "Hi", 0x00FF00),
                                                       fbm.FlashbackMarker(999999, "after end")])
        self.assertEqual(open(self.src(), "rb").read(), before, "original must be untouched")
        with zipfile.ZipFile(out) as zf, zipfile.ZipFile(self.src()) as orig:
            m2 = json.loads(zf.read("metadata.json"))
            self.assertEqual(zf.read("c0.flashback"), orig.read("c0.flashback"))
        self.assertNotEqual(m2["uuid"], "x")
        self.assertEqual(m2["name"], "Base tour (mcsm)")
        self.assertEqual(m2["markers"]["600"]["description"], "Dragon fight")      # existing kept
        self.assertEqual(m2["markers"]["601"], {"colour": 0xFF0000, "description": "Clash"})  # moved to free tick
        self.assertEqual(m2["markers"]["100"]["colour"], 0x00FF00)
        self.assertNotIn("999999", m2["markers"])
        self.assertEqual(meta["_markers_added"], 2)
        from mcsm.replays import read_replay
        r = read_replay(out, self.tmp)
        self.assertEqual((r.start, r.start_source), (utc(2024, 5, 3, 18, 5), "real-time-clock"))
        with self.assertRaises(ValueError):
            fbm.write_replay_copy(self.src(), self.src(), [])

    def test_editor_state_shape(self):
        from mcsm import flashback as fbm
        st = fbm.editor_state("x", fbm.camera_keyframe(1, 2, 3, 180, 30), [(10, 0), (50, 4)], (10, 50))
        assert_valid_flashback_editor_state(self, st)
        with self.assertRaises(ValueError):
            fbm.editor_state("x", None, [(10, 0), (50, 0)])

    def test_windows_plan_keyframes(self):
        from mcsm import timelapse as tlm
        area = tlm.Area.parse("130,20,100,-20", "Base")
        self.assertEqual((area.x1, area.x2, area.z1, area.z2), (100, 130, -20, 20))
        samples = tlm.load_tracks([self.tracks])
        start, end = utc(2024, 5, 3, 18, 5), utc(2024, 5, 3, 18, 8)
        w = tlm.windows_from_tracks(samples, area, start, end, merge_gap=20, pad=5)
        self.assertEqual([(x.start, x.end, x.players) for x in w],
                         [(utc(2024, 5, 3, 18, 5, 25), utc(2024, 5, 3, 18, 6, 5), {"Steve"}),
                          (utc(2024, 5, 3, 18, 6, 55), utc(2024, 5, 3, 18, 7, 35), {"Steve"})])  # Alex is in the nether
        self.assertEqual(len(tlm.windows_from_tracks(samples, area, start, end, merge_gap=120, pad=5)), 1)
        p = tlm.plan(w, target_seconds=20, gap_seconds=0.5)
        self.assertAlmostEqual(p.total_seconds, 20.0)
        self.assertAlmostEqual(p.active_speed, 80 / 19.5)
        keys = tlm.timelapse_keyframes(p, start)
        self.assertEqual(keys, [(500, 0), (1300, 195), (2300, 205), (3100, 400)])
        self.assertEqual(tlm.plan(w, speed=2).active_speed, 2)
        self.assertEqual(tlm.plan(w, target_seconds=3600).active_speed, 1.0)   # never slower than real time
        cam = tlm.auto_camera(area)
        self.assertEqual(cam[3], 180.0)
        self.assertTrue(10 <= cam[4] <= 70)
        self.assertGreater(cam[2], area.z2)

    def test_cli_timelapse_and_flashback_install(self):
        from io import StringIO
        from contextlib import redirect_stdout
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            buf = StringIO()
            with redirect_stdout(buf):
                code = cli.main(["timelapse", self.root, "--replay", "20-05-00", "--area", "100,-20,130,20",
                                 "--tracks", self.tracks, "--length", "0:20", "--merge-gap", "0:20",
                                 "--install", "--flashback-dir", self.fbdir])
            self.assertEqual(code, 0, buf.getvalue())
            states = os.listdir(os.path.join(self.fbdir, "editor_states"))
            self.assertEqual(len(states), 1)
            st = json.load(open(os.path.join(self.fbdir, "editor_states", states[0])))
            assert_valid_flashback_editor_state(self, st)
            self.assertEqual(st["scenes"][0]["exportStartTicks"], 500)
            copy = os.path.join(self.fbdir, "replays", "2024-05-03--20-05-00 (timelapse).zip")
            meta = json.loads(zipfile.ZipFile(copy).read("metadata.json"))
            self.assertEqual(states[0], meta["uuid"] + ".json")
            self.assertEqual(meta["markers"]["500"]["description"], "Steve at Base")
            self.assertTrue(os.path.exists("2024-05-03--20-05-00 (timelapse).time.srt"))
            self.assertEqual(sorted(os.listdir(os.path.join(self.fbdir, "replays"))),
                             ["2024-05-03--20-05-00 (timelapse).zip"])        # nothing else dropped there
            buf = StringIO()
            with redirect_stdout(buf):
                code = cli.main(["flashback", self.root, "--replay", "20-05-00", "--install", "--flashback-dir", self.fbdir])
            self.assertEqual(code, 0, buf.getvalue())
            # A ReplayMod replay is refused with a clear message.
            with redirect_stdout(StringIO()), self.assertRaises(SystemExit):
                cli.main(["flashback", self.root, "--replay", "18-00-00.mcpr"])
        finally:
            os.chdir(cwd)


class TestArgv(unittest.TestCase):
    def test_negative_values(self):
        self.assertEqual(cli.normalise_argv(["timelapse", "s", "--area", "-100,-5,20,30", "--camera", "-1,2,3,4,5"]),
                         ["timelapse", "s", "--area=-100,-5,20,30", "--camera=-1,2,3,4,5"])
        args = cli.parse_args(["sync", "s", "--music-start", "-0:10", "--bpm", "120"])
        self.assertEqual(args.music_start, "-0:10")


class TestMinecraft26Layout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fixtures import build_server_26_1
        cls.tmp = tempfile.mkdtemp(prefix="mcsm-26-")
        cls.root = build_server_26_1(os.path.join(cls.tmp, "server"))
        cls.data = analyze(cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_player_files_found(self):
        steve = self.data.player("steve")
        self.assertEqual(steve.stats["custom"]["play_time"], 216000)
        self.assertIn("story/mine_stone", steve.advancements)
        self.assertEqual(steve.playerdata["inventory"]["torch"], 74)
        self.assertIn("UTC+02:00", self.data.timezone_info)       # learned from players/advancements

    def test_level_dat_and_data_files(self):
        w = self.data.world
        self.assertEqual((w["version_name"], w["difficulty"], w["seed"], w["spawn"]), ("26.1.2", "hard", 4242, [8, 70, -16]))
        self.assertEqual(w["game_rules"], {"minecraft:keep_inventory": 1})
        self.assertTrue(w["raining"])
        self.assertEqual(set(w["dimensions"]), {"overworld", "the_nether"})   # no duplicates from dimensions/

    def test_terrain_dirs(self):
        from mcsm import terrain
        self.assertEqual(len(terrain.region_files(self.root, os.path.join(self.root, "world"), "minecraft:overworld")), 1)

    def test_serverreplay_flashback(self):
        reps = {os.path.basename(r.path): r for r in self.data.replays if "Recordings for_" in r.path}
        bad = reps["2024-05-03--20-05-00.zip"]
        self.assertEqual(bad.recorded_player, "Steve")
        # file name 20:05 server time (UTC+2) -> 18:05 UTC; length = save 18:10 - 18:05 - 1 min paused = 4 min
        self.assertEqual((bad.start, bad.start_source, bad.duration_ms), (utc(2024, 5, 3, 18, 5), "file-name", 240_000))
        self.assertEqual(bad.raw_meta["duration_note"], "estimated from save time")
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli._warn_if_empty_timeline(bad)
        self.assertIn("no timeline inside", buf.getvalue())
        good = reps["2024-05-03--20-30-00.zip"]
        # save 18:33 - 2 min length - 1 min paused = 18:30
        self.assertEqual((good.start, good.start_source, good.duration_ms), (utc(2024, 5, 3, 18, 30), "save-time", 120_000))


class TestSystemChatPrefix(unittest.TestCase):
    """26.3 logs system messages as "System chat: ..." (seen on the owner's server since Oct 2026)."""

    def test_prefixed_lines(self):
        import gzip
        with tempfile.TemporaryDirectory() as d:
            root = build_server(os.path.join(d, "s"))
            with gzip.open(os.path.join(root, "logs", "2024-05-02-1.log.gz"), "wt", encoding="utf-8") as fh:
                fh.write("[19:00:00] [Server thread/INFO]: Starting minecraft server version 26.3\n"
                         "[19:59:59] [Server thread/INFO]: Steve[/203.0.113.5:5000] logged in with entity id 5 at (1.0, 64.0, 2.0)\n"
                         "[19:59:59] [Server thread/INFO]: System chat: Steve joined the game\n"
                         "[20:01:00] [Server thread/INFO]: <Steve> hi\n"
                         "[20:02:00] [Server thread/INFO]: System chat: Steve was slain by Zombie\n"
                         "[20:03:00] [Server thread/INFO]: System chat: Steve has made the advancement [Monster Hunter]\n"
                         "[20:04:00] [Server thread/INFO]: System chat: Steve left the game\n"
                         "[20:05:00] [Server thread/INFO]: System chat: Stopping the server\n"
                         "[20:05:00] [Server thread/INFO]: Stopping server\n")
            data = analyze(root)
            day = [e for e in data.events if e.source.endswith("2024-05-02-1.log.gz")]
            self.assertEqual([e.type for e in day], ["server_start", "join", "chat", "death", "advancement", "leave", "server_stop"])
            self.assertEqual(day[1].data["position"], (1.0, 64.0, 2.0))
            self.assertEqual(day[3].data["killer"], "Zombie")
            self.assertTrue(any(s.start == day[1].time and s.end == day[5].time for s in data.sessions))


class TestRenamedPlayer(unittest.TestCase):
    """Steve played as "Stevie" before a name change. The log's "UUID of player" line links the
    old name to Steve's uuid, so both names make one player (seen on the owner's real server)."""

    @classmethod
    def setUpClass(cls):
        import gzip
        cls.tmp = tempfile.mkdtemp(prefix="mcsm-rename-")
        cls.root = build_server(os.path.join(cls.tmp, "server"))
        with gzip.open(os.path.join(cls.root, "logs", "2024-04-30-1.log.gz"), "wt", encoding="utf-8") as fh:
            fh.write(f"[19:59:59] [User Authenticator #1/INFO]: UUID of player Stevie is {STEVE}\n"
                     "[20:00:00] [Server thread/INFO]: Stevie[/203.0.113.5:5000] logged in with entity id 3 at (1.0, 64.0, 2.0)\n"
                     "[20:00:00] [Server thread/INFO]: Stevie joined the game\n"
                     "[20:30:00] [Server thread/INFO]: <Stevie> my old name\n"
                     "[21:00:00] [Server thread/INFO]: Stevie left the game\n")
        cls.data = analyze(cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_one_player(self):
        self.assertIsNone(self.data.player("stevie"))
        steve = self.data.player("steve")
        self.assertEqual(steve.uuid, STEVE)
        self.assertIn("Stevie", steve.names_seen)
        self.assertGreater(steve.stats["custom"]["play_time"], 0)
        self.assertEqual(sum(1 for s in self.data.sessions if s.player == "Stevie"), 1)
        join = next(e for e in self.data.events if e.type == m.EV_JOIN and e.player == "Stevie")
        self.assertEqual((join.data["uuid"], join.data["position"]), (STEVE, (1.0, 64.0, 2.0)))

    def test_formerly_known_as(self):
        from mcsm.players import name_aliases
        ev = m.Event(utc(2024, 5, 1), m.EV_JOIN, "Newname", "", "", None, {"formerly": "Oldname"})
        self.assertEqual(name_aliases([ev], {})["oldname"], ("Newname", None))


def _fb_chunk(records: list[tuple[str, bytes]], snapshot: list[tuple[str, bytes]] = ()) -> bytes:
    """A Flashback chunk file with the given (action, payload) records (ServerReplay action order)."""
    import struct

    def vi(n):
        out = b""
        while True:
            b, n = n & 0x7F, n >> 7
            out += bytes([b | (0x80 if n else 0)])
            if not n:
                return out
    names = ["flashback:action/level_chunk_cached", "flashback:action/configuration_packet",
             "flashback:action/create_local_player", "flashback:action/game_packet",
             "flashback:action/move_entities", "flashback:action/next_tick"]
    body = struct.pack(">I", 0xD780E884) + vi(len(names))
    for n in names:
        body += vi(len(n)) + n.encode()

    def recs(items):
        return b"".join(vi(names.index("flashback:action/" + name)) + struct.pack(">I", len(p)) + p for name, p in items)
    snap = recs(snapshot)
    return body + struct.pack(">I", len(snap)) + snap + recs(records)


def _vi(n: int) -> bytes:
    out = b""
    while True:
        b, n = n & 0x7F, n >> 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _fb_add_player(eid: int, uuid_text: str, x: float, y: float, z: float, entity_type: int = 159) -> bytes:
    """26.3 add-entity game packet (id 1): entity id, UUID, type, x, y, z, then more we don't read."""
    import struct
    return (_vi(1) + _vi(eid) + bytes.fromhex(uuid_text.replace("-", "")) + _vi(entity_type)
            + struct.pack(">ddd", x, y, z) + b"\0" * 12)


def _fb_move(entities: list[tuple[int, float, float, float]], dim: str = "minecraft:overworld") -> bytes:
    import struct

    def vi(n):
        out = b""
        while True:
            b, n = n & 0x7F, n >> 7
            out += bytes([b | (0x80 if n else 0)])
            if not n:
                return out
    p = vi(1) + vi(len(dim)) + dim.encode() + vi(len(entities))
    for eid, x, y, z in entities:
        p += vi(eid) + struct.pack(">dddfff", x, y, z, 90.0, 0.0, 90.0) + b"\x01"
    return p


class TestHistory(unittest.TestCase):
    """Player paths from Flashback recordings, and the history viewer's data and web server."""

    @classmethod
    def setUpClass(cls):
        import struct
        from fixtures import build_server_26_1
        cls.tmp = tempfile.mkdtemp(prefix="mcsm-hist-")
        cls.root = build_server_26_1(os.path.join(cls.tmp, "server"))
        # Steve (entity 7) walks east 1 block per second for 2 minutes, over two chunk files.
        # Alex (entity 9, added in the snapshot = already in view) walks north 1 block per second
        # until a remove-entities packet at tick 600 (he left view). Entity 11 is a mob (unknown
        # UUID). Recording starts 18:30 UTC (see fixtures); protocol 777 = 26.3.
        local = struct.pack(">QQ", 1, 2) + struct.pack(">ddd", 10.0, 64.0, -3.0) + b"\0" * 40
        snapshot = [("game_packet", _fb_add_player(9, ALEX, 500.0, 70.0, 500.0)),
                    ("game_packet", _fb_add_player(11, "00000000-0000-4000-8000-0000000000ff", 0, 0, 0, 12))]
        remove = ("game_packet", _vi(78) + _vi(2) + _vi(11) + _vi(9))
        chunks = []
        for c in range(2):
            recs = [("create_local_player", local)] if c == 0 else []
            for t in range(1200):
                g = c * 1200 + t
                ents = [(7, 10.0 + g / 20, 64.0, -3.0), (11, 1.0, 1.0, float(g))]
                if g < 600:
                    ents.append((9, 500.0, 70.0, 500.0 - g / 20))
                recs.append(("move_entities", _fb_move(ents)))
                if g == 600:
                    recs.append(remove)
                recs.append(("next_tick", b""))
            chunks.append(_fb_chunk(recs, snapshot if c == 0 else ()))
        cls.replay = os.path.join(cls.root, "recordings", "players", f"Recordings for_ Steve ({STEVE})",
                                  "2024-05-03--20-30-00.zip")
        with zipfile.ZipFile(cls.replay) as zf:
            meta, arcade = zf.read("metadata.json"), zf.read("arcade_replay_meta.json")
        meta = json.dumps({**json.loads(meta), "protocol_version": 777})
        with zipfile.ZipFile(cls.replay, "w") as zf:
            zf.writestr("metadata.json", meta)
            zf.writestr("arcade_replay_meta.json", arcade)
            zf.writestr("c0.flashback", chunks[0])
            zf.writestr("c1.flashback", chunks[1])
        cls.data = analyze(cls.root)
        cls.cache = os.path.join(cls.tmp, "cache")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_decode_points(self):
        from mcsm import paths
        pts = list(paths.decode_points(self.replay))
        self.assertEqual(pts[0][:5], (20, "minecraft:overworld", 10.95, 64.0, -3.0))    # after 20 ticks: moved 19/20 block
        self.assertEqual(pts[-1][0], 2400)                                               # carried into c1
        self.assertAlmostEqual(pts[-1][2], 10.0 + 2399 / 20)
        self.assertEqual(len(pts), 120)                                                  # one per second
        self.assertTrue(all(p[2] < 500 for p in pts))                                   # never the other entity

    def test_other_players(self):
        from mcsm import paths
        players = paths.player_uuids(self.data)
        self.assertEqual(players[bytes.fromhex(ALEX.replace("-", ""))], "Alex")
        alex = [p for p in paths.decode_entities(self.replay, players) if p[1] == "Alex"]
        self.assertEqual(alex[0][:6], (20, "Alex", "minecraft:overworld", 500.0, 70.0, 500.0 - 19 / 20))
        self.assertEqual(alex[-1][0], 600)                          # removed at tick 600: no more points
        self.assertEqual(len(alex), 30)
        self.assertFalse(any(p[1] not in (None, "Alex") for p in paths.decode_entities(self.replay, players)))  # no mobs
        # Track file: Alex is marked as seen in Steve's recording.
        f = paths.replay_track(self.data.replays[[r.path for r in self.data.replays].index(
            next(r.path for r in self.data.replays if r.path.endswith("20-30-00.zip")))],
            self.replay, os.path.join(self.tmp, "cache-other"), players)
        pts = paths.read_tracks([f])
        self.assertEqual(pts["Alex"][0][5], "Steve")
        self.assertEqual(len(pts["Steve"][0]), 5)
        # In the viewer, Alex's own points (if any) win over "seen" points at the same moment.
        from mcsm.history import prefer_own
        mixed = [[1000, 0, 0, 0, "d"], [1500, 9, 9, 9, "d", "Steve"], [9000, 1, 1, 1, "d", "Steve"]]
        self.assertEqual([p[0] for p in prefer_own(mixed)], [1000, 9000])

    def test_tracks_and_cache(self):
        from mcsm import paths
        files, warns = paths.tracks_from_replays(self.data, lambda r: os.path.join(self.root, r.path), self.cache)
        self.assertEqual((len(files), warns), (1, []))     # the 3.4.1 file (no timeline) is skipped
        with open(files[0], encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh]
        self.assertEqual(rows[0]["t"], int(utc(2024, 5, 3, 18, 30, 1).timestamp() * 1000))
        self.assertEqual(rows[0]["players"][0]["name"], "Steve")
        mtime = os.path.getmtime(files[0])
        again, _ = paths.tracks_from_replays(self.data, lambda r: os.path.join(self.root, r.path), self.cache)
        self.assertEqual((again, os.path.getmtime(again[0])), (files, mtime))      # reused, not rewritten
        # Same format as live-map recordings, so the timelapse can use it.
        from mcsm import timelapse
        self.assertEqual(len(timelapse.load_tracks(files)), 120)

    def test_thin(self):
        from mcsm.history import thin
        pts = [[i * 1000, 0.0, 64.0, 0.0, "minecraft:overworld"] for i in range(100)]   # standing still
        self.assertEqual([p[0] for p in thin(pts)], [0, 30000, 60000, 90000, 99000])
        self.assertEqual(len(thin([[0, 0, 0, 0, "a"], [1000, 0.1, 0, 0, "b"]])), 2)     # dimension change kept

    def test_server(self):
        import time as _time
        import urllib.request
        from mcsm.history import HistoryMap
        hm = HistoryMap(self.data, lambda r: os.path.join(self.root, r.path), cache_dir=self.cache)
        url = hm.start(8791)
        try:
            get = lambda p: json.loads(urllib.request.urlopen(url + p, timeout=10).read())   # noqa: E731
            h = get("api/history")
            self.assertIn("Steve", h["players"])
            self.assertTrue(any(r["estimated"] for r in h["replays"]))                     # the 3.4.1 file
            join = next(e for e in h["events"] if e["type"] == "join" and e.get("pos"))
            self.assertEqual(join["dim"], "minecraft:overworld")
            for _ in range(100):
                info = get("api/info")
                if info["paths"]["done"] >= info["paths"]["total"] > 0:
                    break
                _time.sleep(0.05)
            p = get("api/paths")
            self.assertGreater(len(p["players"]["Steve"]), 50)
            page = urllib.request.urlopen(url, timeout=10).read().decode()
            self.assertIn("api/history", page)
        finally:
            hm.stop()

    def test_terrain_per_backup(self):
        import shutil
        import urllib.request
        from mcsm import terrain
        from mcsm.history import HistoryMap
        grass = lambda x, y, z: "minecraft:grass_block"  # noqa: E731
        base = os.path.join(self.tmp, "terrain-layers")

        def server(name, regions):
            root = os.path.join(base, name)
            os.makedirs(os.path.join(root, "world", "region"))
            with open(os.path.join(root, "server.properties"), "w") as fh:
                fh.write("level-name=world\n")
            for rx, h in regions.items():
                terrain.write_region(os.path.join(root, "world", "region", f"r.{rx}.0.mca"),
                                     {(0, 0): terrain.make_chunk(rx * 32, 0, lambda x, z, h=h: h, grass)})
            return root
        old = server("old", {0: 64, 1: 64})
        new = server("new", {1: 80, 2: 70})
        shutil.copy2(os.path.join(old, "world", "region", "r.0.0.mca"),       # unchanged region
                     os.path.join(new, "world", "region", "r.0.0.mca"))
        hm = HistoryMap(self.data, None, new, os.path.join(new, "world"), 4, cache_dir=self.cache,
                        terrain_backups=[{"label": "May", "t": 1000, "path": old}],
                        terrain_label="Nov", terrain_time=2000)
        hm.terrain_cache, hm.terrain_workers = "", 1
        hm._build_terrain()
        ow = "minecraft:overworld"
        newest, older = hm.layer_tiles(ow, 0), hm.layer_tiles(ow, 1)
        self.assertEqual(sorted(newest), [(0, 0), (1, 0), (2, 0)])
        self.assertEqual(sorted(older), [(0, 0), (1, 0)])
        self.assertIs(newest[(0, 0)][1], older[(0, 0)][1])                     # read once, shared
        self.assertNotEqual(newest[(1, 0)][0], older[(1, 0)][0])
        h = lambda t: t.heights[0]  # noqa: E731
        self.assertEqual((h(newest[(1, 0)][1]), h(older[(1, 0)][1])), (80, 64))
        self.assertEqual(sorted(hm.terrain[ow]), [(0, 0), (1, 0), (2, 0)])      # live-map view = newest
        info = hm.terrain_layers_info()
        self.assertEqual([(L["label"], L["t"], L["done"]) for L in info], [("Nov", 2000, True), ("May", 1000, True)])
        hm._httpd = None
        from mcsm.live import serve
        import threading
        hm._httpd = serve(hm, 8793)
        threading.Thread(target=hm._httpd.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{hm._httpd.server_address[1]}/"
            get = lambda p: json.loads(urllib.request.urlopen(url + p, timeout=10).read())   # noqa: E731
            lst = get("api/terrain/minecraft:overworld?layer=1")
            self.assertTrue(lst["done"])
            self.assertEqual(sorted((t["rx"], t["k"]) for t in lst["tiles"]),
                             sorted((rc[0], k) for rc, (k, _t) in older.items()))
            self.assertEqual(len(get("api/terrain/minecraft:overworld")["tiles"]), 3)
            raw = urllib.request.urlopen(url + "api/terrain/minecraft:overworld/1/0?layer=1", timeout=10).read()
            self.assertEqual(int.from_bytes(raw[:2], "little"), 64)                 # the old height
            self.assertEqual(len(get("api/info")["terrain_layers"]), 2)
        finally:
            hm.stop()
        from mcsm import cli
        self.assertEqual(cli.parse_args(["history", "x", "--terrain", "newest"]).terrain, "newest")
        self.assertEqual(cli.parse_args(["history", "x"]).terrain, "all")

    def test_highlights_store_and_export(self):
        import csv as _csv
        from mcsm import highlights as hl
        start = int(utc(2024, 5, 3, 18, 31).timestamp() * 1000)        # replay 20-30-00 covers 18:30-18:32
        store = hl.Store(os.path.join(self.tmp, "hl", "w.json"))
        with self.assertRaises(ValueError):
            store.put({"start": start, "end": start + 500})               # under 1 s
        h = store.put({"title": "  Big fight ", "start": start + 30000, "end": start}, ["Steve"])   # reversed: swapped
        self.assertEqual((h["title"], h["start"], h["end"]), ("Big fight", start, start + 30000))
        store.put({"id": h["id"], "title": "Bigger fight", "start": start, "end": start + 30000})
        self.assertEqual([x["title"] for x in hl.Store(store.path).list()], ["Bigger fight"])  # saved to disk, updated in place
        self.assertEqual(hl.Store(store.path).list()[0]["players"], ["Steve"])
        reps = hl.covering(self.data, store.list()[0])
        self.assertEqual([os.path.basename(r.path) for r in reps], ["2024-05-03--20-30-00.zip"])   # not the 3.4.1 one
        fb = os.path.join(self.tmp, "flashback")
        os.makedirs(os.path.join(fb, "replays"))
        res = hl.export(self.data, store.list(), lambda r: os.path.join(self.root, r.path),
                        os.path.dirname(store.path), fb, "Main_world")
        self.assertEqual(len(res["replays"]), 1)
        out = res["replays"][0]
        with zipfile.ZipFile(out["file"]) as zf:
            meta = json.loads(zf.read("metadata.json"))
        self.assertEqual(sorted(meta["markers"]), ["1200", "1800"])          # 60 s and 90 s into the recording
        self.assertEqual(meta["markers"]["1200"]["colour"], hl.START_COLOUR)
        with open(out["editor_state"], encoding="utf-8") as fh:
            state = json.load(fh)
        assert_valid_flashback_editor_state(self, {**state, "scenes": [
            {**s, "keyframeTracks": [{"keyframeType": "CAMERA", "enabled": True, "keyframesByTick": {"0": {
                "type": "camera", "position": [0, 0, 0], "interpolation_type": "HOLD"}}}]} for s in state["scenes"]]})
        self.assertEqual((state["scenes"][0]["exportStartTicks"], state["scenes"][0]["exportEndTicks"]), (1200, 1800))
        self.assertEqual(state["scenes"][0]["keyframeTracks"], [])
        with open(res["clip_list"], encoding="utf-8-sig") as fh:
            rows = list(_csv.DictReader(fh))
        self.assertEqual((rows[0]["title"], rows[0]["in (offset)"], rows[0]["out (offset)"]), ("Bigger fight", "0:01:00", "0:01:30"))
        self.assertTrue(store.delete(h["id"]))
        self.assertEqual(hl.Store(store.path).list(), [])

    def test_highlight_api_only_from_own_page(self):
        import urllib.error
        import urllib.request
        from mcsm.history import HistoryMap
        hm = HistoryMap(self.data, None, decode_replays=False, highlights_file=os.path.join(self.tmp, "hl2.json"))
        url = hm.start(8795)
        try:
            body = json.dumps({"title": "x", "start": 0, "end": 5000}).encode()

            def post(headers):
                req = urllib.request.Request(url + "api/highlights", body, {"Content-Type": "application/json", **headers})
                try:
                    return urllib.request.urlopen(req, timeout=10).status
                except urllib.error.HTTPError as exc:
                    return exc.code
            self.assertEqual(post({}), 403)                                                   # no custom header
            self.assertEqual(post({"X-MCSM": "1", "Origin": "https://example.com"}), 403)     # another website
            self.assertEqual(post({"X-MCSM": "1"}), 200)
            got = json.loads(urllib.request.urlopen(url + "api/highlights", timeout=10).read())
            self.assertEqual([h["title"] for h in got["highlights"]], ["x"])
        finally:
            hm.stop()

    def test_palette_strings_26_3(self):
        from mcsm.terrain import _sections
        chunk = {"sections": [{"Y": 4, "block_states": {"palette": ["minecraft:grass_block", {"Name": "minecraft:oak_log"}]}}]}
        self.assertEqual(_sections(chunk)[0][4][0], ["minecraft:grass_block", "minecraft:oak_log"])


def _zip_dir(src: str, dst: str, wrapper: str = "") -> str:
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for cur, _dirs, files in os.walk(src):
            for f in files:
                full = os.path.join(cur, f)
                zf.write(full, os.path.join(wrapper, os.path.relpath(full, src)))
    return dst


class TestLiteZipAndArchive(unittest.TestCase):
    """Two backups of one server across a version change: an old-layout 1.21 backup taken on
    2024-05-02 and a 26.1-layout backup taken later, both as zips (selective extraction)."""

    @classmethod
    def setUpClass(cls):
        from fixtures import build_server_26_1
        cls.tmp = tempfile.mkdtemp(prefix="mcsm-arch-")
        old = build_server(os.path.join(cls.tmp, "old"))
        # The old backup was taken before latest.log existed and before the 05-03 replay.
        os.remove(os.path.join(old, "logs", "latest.log"))
        shutil.rmtree(os.path.join(old, "recordings"))
        stats = os.path.join(old, "world", "stats", f"{STEVE}.json")
        d = json.load(open(stats))
        d["stats"]["minecraft:custom"]["minecraft:play_time"] = 72000
        json.dump(d, open(stats, "w"))
        # a big region file that lite extraction must skip
        with open(os.path.join(old, "world", "region", "r.5.5.mca"), "wb") as fh:
            fh.write(b"\0" * 300_000)
        new = build_server_26_1(os.path.join(cls.tmp, "new"))
        cls.old_zip = _zip_dir(old, os.path.join(cls.tmp, "backup-2024-05-02.zip"), "server")
        cls.new_zip = _zip_dir(new, os.path.join(cls.tmp, "backup-2024-05-04.zip"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_lite_extraction_and_ensure_full(self):
        from mcsm.source import ensure_full, is_stub, open_server
        with open_server(self.old_zip) as root:
            self.assertFalse(os.path.exists(os.path.join(root, "world", "region", "r.5.5.mca")))
            self.assertTrue(os.path.exists(os.path.join(root, "world", "level.dat")))
        with open_server(self.new_zip) as root:
            rp = os.path.join(root, "recordings", "players", "Steve", "2024-05-03--18-00-00.mcpr")
            self.assertTrue(is_stub(rp))
            data = analyze(root)
            r = next(r for r in data.replays if r.path.endswith("18-00-00.mcpr"))
            self.assertEqual(r.start, utc(2024, 5, 3, 18))            # metadata read from the stub
            self.assertGreater(r.size_bytes, 0)
            ensure_full(rp)
            self.assertFalse(is_stub(rp))
            self.assertIn("recording.tmcpr", zipfile.ZipFile(rp).namelist())
        with open_server(self.old_zip, include_regions=True) as root:
            self.assertTrue(os.path.exists(os.path.join(root, "world", "region", "r.5.5.mca")))

    def test_merge(self):
        from contextlib import ExitStack
        from mcsm.archive import analyze_many
        with ExitStack() as stack:
            d = analyze_many([self.new_zip, self.old_zip], stack)   # order given doesn't matter
            self.assertEqual([b["label"] for b in d.backups], ["backup-2024-05-02", "backup-2024-05-04"])
            self.assertEqual([b["version"] for b in d.backups], ["1.20.4", "26.1.2"])
            chats = [e for e in d.events if e.type == "chat"]
            self.assertEqual(len(chats), 4)                         # the shared log is not doubled
            self.assertEqual(len(d.sessions), 3)
            steve = d.player("steve")
            self.assertEqual(steve.stats["custom"]["play_time"], 216000)      # newest backup wins
            hist = d.player_history["Steve"]
            self.assertEqual([h["play_ticks"] for h in hist], [72000, 216000])  # progress over time
            self.assertIn("UTC+02:00", d.timezone_info)
            # replays from the new backup, with label-prefixed paths that still resolve
            r = next(r for r in d.replays if r.path.endswith("18-00-00.mcpr"))
            self.assertTrue(r.path.startswith("backup-2024-05-04/"))
            self.assertTrue(os.path.isfile(cli._replay_file(d, r)))
            from mcsm.exporters import report as rep
            html = rep.render(d)
            self.assertIn("backup-2024-05-02", html)

    def test_cli_multi(self):
        from io import StringIO
        from contextlib import redirect_stdout, redirect_stderr
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["summary", self.old_zip, self.new_zip])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("Steve", out.getvalue())


    def test_sizes_from_listing(self):
        from mcsm.source import open_server
        with open_server(self.old_zip) as root:
            d = analyze(root)
        self.assertGreater(d.world["size_bytes"], 300_000)                 # skipped region file still counted
        self.assertEqual(d.world["dimensions"]["overworld"]["region_files"], 2)
