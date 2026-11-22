"""End-to-end and unit tests. Run with ``python -m unittest`` or ``pytest``."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import struct
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
# App settings (%APPDATA%\mcsm\settings.json: library folder, update channel) too.
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="mcsm-test-roaming-")

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


def _new_tk():
    """tk.Tk(), retried: on GitHub's Windows runners Tcl now and then fails to find init.tcl
    when a test process creates several Tk windows one after another."""
    import tkinter as tk
    for attempt in range(5):
        try:
            return tk.Tk()
        except tk.TclError:
            if attempt == 4:
                raise
            import gc
            gc.collect()
            time.sleep(0.5)


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
        root = _new_tk()
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
        root = _new_tk()
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
        root = _new_tk()
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
        root = _new_tk()
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
        root = _new_tk()
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

    def test_changes_between_backups(self):
        from contextlib import ExitStack
        from mcsm.archive import analyze_many, changes, datapacks_of
        with ExitStack() as stack:
            d = analyze_many([self.old_zip, self.new_zip], stack)
        mc = [c for c in d.changes if c["kind"] == "minecraft"]
        self.assertEqual([(c["from"], c["to"]) for c in mc], [("1.20.4", "26.1.2")])
        self.assertEqual(mc[0]["time"], d.backups[1]["time"])
        self.assertEqual(datapacks_of({"datapacks_enabled": ["vanilla", "file/sit.zip", "fabric"],
                                       "datapacks_folder": ["sit.zip", "horse.zip"]}), ["horse.zip", "sit.zip"])
        b1 = {"label": "a", "time": 1, "version": "26.1", "world_number": 1, "datapacks": ["sit.zip", "horse.zip"],
              "mod_list": [{"id": "lithium", "name": "Lithium", "version": "0.24", "kind": "mod"},
                           {"id": "", "name": "", "file": "spark-1.10.172-fabric.jar", "kind": "mod", "version": ""},
                           {"id": "serverreplay", "name": "Server Replay", "version": "3.4.1", "kind": "mod"}]}
        b2 = {"label": "b", "time": 2, "version": "26.3", "world_number": 1, "datapacks": ["sit.zip", "hero.zip"],
              "mod_list": [{"id": "", "name": "", "file": "spark-1.10.180-fabric.jar", "kind": "mod", "version": ""},
                           {"id": "serverreplay", "name": "Server Replay", "version": "3.6.0", "kind": "mod"},
                           {"id": "vanish", "name": "Vanish", "version": "1.6", "kind": "mod"}]}
        got = {(c["kind"], c["action"], c["name"]) for c in changes([b1, b2])}
        self.assertEqual(got, {("minecraft", "updated", "Minecraft"), ("mod", "removed", "Lithium"),
                               ("mod", "updated", "spark"), ("mod", "updated", "Server Replay"),
                               ("mod", "added", "Vanish"), ("datapack", "added", "hero.zip"),
                               ("datapack", "removed", "horse.zip")})
        from io import StringIO
        from contextlib import redirect_stdout, redirect_stderr
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(StringIO()):
            self.assertEqual(cli.main(["changes", self.old_zip, self.new_zip]), 0)
        self.assertIn("1.20.4 -> 26.1.2", out.getvalue())

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


class TestLibrary(unittest.TestCase):
    """Two backups of one server into one library: the old one as a folder, the new one as a
    PebbleHost-style .tgz with extra recordings and one changed file."""

    @classmethod
    def setUpClass(cls):
        import tarfile
        from fixtures import add_extra_replays
        cls.tmp = tempfile.mkdtemp(prefix="mcsm-lib-")
        cls.old = build_server(os.path.join(cls.tmp, "src", "old"))
        cls.old_replays = len(analyze(cls.old).replays)
        new = os.path.join(cls.tmp, "src", "new", "home", "servers", "abc")
        shutil.copytree(cls.old, new)
        add_extra_replays(new)
        with open(os.path.join(new, "ops.json"), "w") as fh:        # changed between backups
            json.dump([], fh)
        old_t, new_t = time.time() - 30 * 86400, time.time()
        os.utime(os.path.join(cls.old, "logs", "latest.log"), (old_t, old_t))
        os.utime(os.path.join(new, "logs", "latest.log"), (new_t, new_t))
        cls.tgz = os.path.join(cls.tmp, "src", "backup.tgz")
        with tarfile.open(cls.tgz, "w:gz") as tf:
            tf.add(os.path.join(cls.tmp, "src", "new", "home"), arcname="home")
            evil = tarfile.TarInfo("../escaped.txt")              # must never be written
            evil.size = 2
            import io as _io
            tf.addfile(evil, _io.BytesIO(b"no"))
        cls.lib = os.path.join(cls.tmp, "lib")
        from mcsm import library as lib
        cls.r_old = lib.import_backup(cls.old, cls.lib, mode="copy")
        cls.r_new = lib.import_backup(cls.tgz, cls.lib)
        cls.world = cls.r_old["world"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_layout_and_dedup(self):
        from mcsm import library as lib
        worlds = lib.list_worlds(self.lib)
        self.assertEqual([w["name"] for w in worlds], ["Friends SMP"])
        self.assertEqual(self.r_new["world"], self.world)
        bs = worlds[0]["backups"]
        self.assertEqual(len(bs), 2)
        self.assertLess(bs[0]["snapshot_ms"], bs[1]["snapshot_ms"])
        self.assertTrue(bs[1]["source"].endswith("backup.tgz"))
        for b in bs:                                               # recordings live outside the backups
            self.assertFalse(os.path.exists(os.path.join(b["path"], "recordings")))
            self.assertTrue(os.path.isfile(os.path.join(b["path"], "server.properties")))
        # unchanged file shared, changed file not
        self.assertTrue(os.path.samefile(os.path.join(bs[0]["path"], "usercache.json"),
                                         os.path.join(bs[1]["path"], "usercache.json")))
        self.assertFalse(os.path.samefile(os.path.join(bs[0]["path"], "ops.json"),
                                          os.path.join(bs[1]["path"], "ops.json")))
        self.assertGreater(self.r_new["deduplicated"], 5)
        # each recording stored once
        self.assertEqual(self.r_old["recordings_new"], self.old_replays)
        self.assertEqual(self.r_new["recordings_known"], self.old_replays)
        self.assertEqual(self.r_new["recordings_new"], 4)
        self.assertEqual(worlds[0]["recordings"], self.old_replays + 4)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "lib", "escaped.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escaped.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.lib, ".incoming")))
        # the source folder is untouched by copy mode
        self.assertTrue(os.path.isdir(os.path.join(self.old, "recordings")))

    def test_windows_names(self):
        from mcsm.library import _local_name
        n = _local_name("home/servers/x/recordings/players/Recordings for: Steve (abc)/a.zip")
        self.assertEqual(n, "home/servers/x/recordings/players/Recordings for_ Steve (abc)/a.zip"
                         if os.name == "nt" else "home/servers/x/recordings/players/Recordings for: Steve (abc)/a.zip")

    def test_reimport_skipped(self):
        from mcsm import library as lib
        r = lib.import_backup(self.old, self.lib)
        self.assertTrue(r["skipped"])
        self.assertEqual(len(lib.world_backups(self.world)), 2)

    def test_load_world_as_server(self):
        from io import StringIO
        from contextlib import redirect_stdout, redirect_stderr
        for target in (self.world, self.lib):                      # a one-world library counts as that world
            with redirect_stderr(StringIO()):
                d = cli._load(cli.parse_args(["summary", target]))
            self.assertEqual(len(d.backups), 2)
            self.assertEqual(d.library_world, self.world)
            self.assertEqual(len(d.replays), self.old_replays + 4)
            r = next(r for r in d.replays if r.path.endswith("20-05-00.zip"))
            self.assertTrue(r.path.startswith("recordings/"))
            self.assertTrue(os.path.isfile(cli._replay_file(d, r)))
            self.assertEqual(r.recorded_player, "Steve")               # uuid folder still mapped to the name
            self.assertEqual(len([e for e in d.events if e.type == "chat"]), 4)   # shared logs not doubled
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(StringIO()):
            self.assertEqual(cli.main(["library", "list", "--library", self.lib]), 0)
        self.assertIn("Friends SMP", out.getvalue())

    def test_link_mode_and_add_recordings(self):
        from mcsm import library as lib
        lib2 = os.path.join(self.tmp, "lib2")
        r = lib.import_backup(self.old, lib2, mode="link")
        self.assertEqual(r["mode"], "link")
        self.assertTrue(os.path.samefile(os.path.join(self.old, "server.properties"),
                                         os.path.join(r["backup"], "server.properties")))
        self.assertEqual(r["bytes_written"], 0)
        # a loose Flashback client replay
        loose = os.path.join(self.tmp, "flashback", "replays")
        os.makedirs(loose, exist_ok=True)
        src = os.path.join(self.world, "recordings", "recordings", "players", STEVE, "2024-05-03--20-05-00.zip")
        shutil.copy2(src, os.path.join(loose, "Client clip.zip"))
        s = lib.add_recordings([loose], r["world"], mode="copy")
        self.assertEqual(s["added"], 1)
        self.assertEqual(lib.add_recordings([loose], r["world"], mode="copy")["known"], 1)
        self.assertTrue(os.path.isfile(os.path.join(r["world"], "recordings", "imported", "replays", "Client clip.zip")))
        # a folder of recordings the world already has (same names, same content) adds nothing
        again = os.path.join(self.tmp, "all-recordings")
        shutil.copytree(os.path.join(self.old, "recordings"), os.path.join(again, "recordings"))
        s2 = lib.add_recordings([again], r["world"], mode="copy")
        self.assertEqual(s2["added"], 0)
        self.assertEqual(s2["known"], self.old_replays)
        from io import StringIO
        from contextlib import redirect_stderr
        with redirect_stderr(StringIO()):
            d = cli._load(cli.parse_args(["summary", r["world"]]))
        self.assertTrue(any(x.path.endswith("Client clip.zip") for x in d.replays))

    def test_history_highlights_in_world(self):
        from io import StringIO
        from contextlib import redirect_stderr
        from mcsm.history import HistoryMap
        with redirect_stderr(StringIO()):
            d = cli._load(cli.parse_args(["summary", self.world]))
        hm = HistoryMap(d, lambda r: cli._replay_file(d, r), d.root, None, 16, os.path.join(self.tmp, "cache"),
                        [], "", None, decode_replays=False)
        self.assertEqual(hm.highlights.path, os.path.join(self.world, "highlights.json"))

    def test_gui_library_tab(self):
        """Import through the app's Library tab, then load the world with one click."""
        if not _have_display():
            self.skipTest("no display")
        import tkinter as tk
        from mcsm import gui
        root = _new_tk()
        try:
            app = gui.App(root)
            lib3 = os.path.join(self.tmp, "lib3")
            app.lib_dir_var.set(lib3)
            app.lib_mode_var.set("copy")
            orig = gui.filedialog.askdirectory
            gui.filedialog.askdirectory = lambda **kw: self.old
            self.addCleanup(setattr, gui.filedialog, "askdirectory", orig)
            app._lib_import(folder=True)
            deadline = time.time() + 30
            while app.busy and time.time() < deadline:
                root.update()
                time.sleep(0.05)
            worlds = app.lib_tree.get_children()
            self.assertEqual(len(worlds), 1, app.log_text.get("1.0", "end")[-1500:])
            self.assertEqual(len(app.lib_tree.get_children(worlds[0])), 1)
            app.lib_tree.selection_set(worlds[0])
            app._lib_load()
            deadline = time.time() + 30
            while (app.data is None or app.busy) and time.time() < deadline:
                root.update()
                time.sleep(0.05)
            self.assertIsNotNone(app.data)
            self.assertEqual(app.data.library_world, worlds[0])
            self.assertEqual(len(app.data.replays), self.old_replays)
        finally:
            root.destroy()
            del root
            import gc
            gc.collect()


class TestLibraryScan(unittest.TestCase):
    """A messy backup folder like the owner's: archive + its unpacked copy, an identical archive
    elsewhere, A.tar next to A.tgz, an Aternos-style world-only zip, a singleplayer save, videos,
    a .7z and loose recordings."""

    @classmethod
    def setUpClass(cls):
        import tarfile
        from fixtures import add_extra_replays
        cls.tmp = tempfile.mkdtemp(prefix="mcsm-scan-")
        src = os.path.join(cls.tmp, "build")
        old = build_server(os.path.join(src, "old"))
        new = os.path.join(src, "new", "home", "servers", "abc")
        shutil.copytree(old, new)
        add_extra_replays(new)
        t_old, t_new = time.time() - 60 * 86400, time.time() - 86400
        os.utime(os.path.join(old, "logs", "latest.log"), (t_old, t_old))
        os.utime(os.path.join(new, "logs", "latest.log"), (t_new, t_new))
        top = cls.top = os.path.join(cls.tmp, "Game Backup")
        a = os.path.join(top, "Nov01")
        os.makedirs(a)
        with tarfile.open(os.path.join(a, "QUJD.tgz"), "w:gz") as tf:
            tf.add(os.path.join(src, "new", "home"), arcname="home")
        shutil.copytree(os.path.join(src, "new", "home"), os.path.join(a, "QUJD", "home"))   # unpacked copy
        os.makedirs(os.path.join(top, "Nov01 again"))
        shutil.copy2(os.path.join(a, "QUJD.tgz"), os.path.join(top, "Nov01 again", "QUJD.tgz"))  # identical
        b = os.path.join(top, "Sep")
        os.makedirs(b)
        with tarfile.open(os.path.join(b, "OLD.tar"), "w") as tf:
            tf.add(old, arcname="srv")
        with tarfile.open(os.path.join(b, "OLD.tgz"), "w:gz") as tf:
            tf.add(old, arcname="srv")
        cls.world_zip = _zip_dir(os.path.join(old, "world"), os.path.join(top, "aternos-world.zip"), "world")
        shutil.copytree(os.path.join(old, "world"), os.path.join(top, "saves", "My Survival"))
        with open(os.path.join(top, "cinematic.mp4"), "wb") as fh:
            fh.write(b"\0" * 1000)
        with open(os.path.join(top, "old.7z"), "wb") as fh:
            fh.write(b"7z")
        shutil.copytree(os.path.join(new, "recordings"), os.path.join(top, "All recordings", "recordings"))
        cls.lib = os.path.join(cls.tmp, "lib")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_scan_and_import_all(self):
        from mcsm import library as lib
        plan = lib.scan(self.top, self.lib)
        rel = lambda p: os.path.relpath(p, self.top).replace(os.sep, "/")  # noqa: E731
        picked = sorted(rel(c["path"]) for c in plan["backups"])
        self.assertEqual(picked, ["Nov01/QUJD/home/servers/abc", "Sep/OLD.tar", "aternos-world.zip",
                                  "saves/My Survival"])
        same = {rel(c["path"]): c["why"] for c in plan["same"]}
        self.assertEqual(same["Nov01/QUJD.tgz"], "unpacked copy next to it")
        self.assertEqual(same["Nov01 again/QUJD.tgz"], "identical file")
        self.assertIn("compressed (the .tar is used)", same["Sep/OLD.tgz"])
        self.assertEqual([rel(p) for p in plan["unsupported"]], ["old.7z"])
        self.assertEqual(plan["ignored"].get(".mp4"), 1)
        self.assertEqual([rel(r["group"]) for r in plan["recordings"]], ["All recordings"])
        self.assertTrue(next(c for c in plan["backups"] if c["path"].endswith("My Survival"))["world_only"])
        text = lib.describe_plan(plan)
        self.assertIn("Copies of a backup above, skipped (3)", text)

        r = lib.import_all(plan, self.lib, "copy")
        self.assertEqual(r["failed"], [])
        worlds = lib.list_worlds(self.lib)
        self.assertEqual(len(worlds), 1)
        bs = worlds[0]["backups"]
        # the zip and the save hold the same world snapshot: imported once
        self.assertEqual(len(r["imported"]), 3)
        self.assertEqual(len(r["skipped"]), 1)
        wo = next(b for b in bs if b.get("world_only"))
        self.assertTrue(os.path.isfile(os.path.join(wo["path"], "server.properties")))
        props = open(os.path.join(wo["path"], "server.properties")).read()
        level = props.split("level-name=")[1].strip()
        self.assertTrue(os.path.isfile(os.path.join(wo["path"], level, "level.dat")))
        self.assertEqual(r["recordings"][0]["added"], 0)            # all already came with the backups
        # the whole world loads, world-only backup included
        from io import StringIO
        from contextlib import redirect_stderr
        with redirect_stderr(StringIO()):
            d = cli._load(cli.parse_args(["summary", worlds[0]["path"]]))
        self.assertEqual(len(d.backups), 3)
        # originals untouched
        self.assertTrue(os.path.isdir(os.path.join(self.top, "Nov01", "QUJD", "home", "servers", "abc", "recordings")))
        out = StringIO()
        from contextlib import redirect_stdout
        with redirect_stdout(out), redirect_stderr(StringIO()):
            self.assertEqual(cli.main(["library", "scan", self.top, "--library", self.lib]), 0)
        self.assertIn("Nothing was changed", out.getvalue())


class TestLibrarySafety(unittest.TestCase):
    """Regression tests for the review of mcsm/library.py (2026-11-22)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mcsm-libsafe-")
        self.lib = os.path.join(self.tmp, "lib")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _server(self, name, age_days):
        root = build_server(os.path.join(self.tmp, name))
        t = time.time() - age_days * 86400
        os.utime(os.path.join(root, "logs", "latest.log"), (t, t))
        return root

    def _unfinished(self, root, chunk: bytes):
        d = os.path.join(root, "recordings", "players", STEVE, "2024-05-03--22-00-00")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "metadata.json"), "w") as fh:
            json.dump({"name": "x", "total_ticks": len(chunk), "chunks": {}}, fh)
        with open(os.path.join(d, "c0.flashback"), "wb") as fh:
            fh.write(chunk)
        return d

    def test_grown_recording_folder_is_kept_whole(self):
        from mcsm import library as lib
        a, b = self._server("a", 10), self._server("b", 5)
        self._unfinished(a, b"\xd7\x80\xe8\x84" + b"a" * 10)
        self._unfinished(b, b"\xd7\x80\xe8\x84" + b"b" * 20)       # the same recording, grown
        c = self._server("c", 1)
        self._unfinished(c, b"\xd7\x80\xe8\x84" + b"b" * 20)
        for s in (a, b, c):
            lib.import_backup(s, self.lib, mode="copy")
        w = lib.list_worlds(self.lib)[0]["path"]
        base = os.path.join(w, "recordings", "recordings", "players", STEVE)
        dirs = sorted(d for d in os.listdir(base) if d.startswith("2024-05-03--22-00-00"))
        self.assertEqual(dirs, ["2024-05-03--22-00-00", "2024-05-03--22-00-00 (2)"])   # not (3)
        for d in dirs:
            self.assertEqual(sorted(os.listdir(os.path.join(base, d))), ["c0.flashback", "metadata.json"])

    def test_changed_recording_file_stored_once(self):
        from mcsm import library as lib
        servers = [self._server(n, age) for n, age in (("a", 10), ("b", 5), ("c", 1))]
        for s in servers[1:]:
            p = os.path.join(s, "recordings", "players", "Steve", "2024-05-03--18-00-00.mcpr")
            with zipfile.ZipFile(p, "a") as zf:
                zf.writestr("extra.txt", "changed")
        for s in servers:
            lib.import_backup(s, self.lib, mode="copy")
        w = lib.list_worlds(self.lib)[0]["path"]
        names = sorted(os.listdir(os.path.join(w, "recordings", "recordings", "players", "Steve")))
        self.assertEqual(names, ["2024-05-03--18-00-00 (2).mcpr", "2024-05-03--18-00-00.mcpr"])

    def test_more_complete_copy_is_not_dropped(self):
        from mcsm import library as lib
        full = self._server("full", 3)
        part = os.path.join(self.tmp, "part")
        shutil.copytree(full, part)
        shutil.rmtree(os.path.join(part, "world", "stats"))               # unpacked only partly
        r1 = lib.import_backup(part, self.lib, mode="copy")
        r2 = lib.import_backup(full, self.lib, mode="copy")
        self.assertFalse(r2["skipped"])
        self.assertEqual(r2["more_complete_than"], r1["backup"])
        r3 = lib.import_backup(full, self.lib, mode="copy")
        self.assertTrue(r3["skipped"])

    def test_failed_import_leaves_nothing(self):
        from mcsm import library as lib
        s = self._server("a", 1)
        real = lib._place
        calls = {"n": 0}

        def boom(*a, **k):
            calls["n"] += 1
            if calls["n"] == 5:
                raise OSError("disk full")
            return real(*a, **k)
        lib._place = boom
        try:
            with self.assertRaises(OSError):
                lib.import_backup(s, self.lib, mode="copy")
        finally:
            lib._place = real
        w = lib.list_worlds(self.lib)[0]["path"]
        self.assertEqual(lib.world_backups(w), [])
        self.assertEqual([n for n in os.listdir(os.path.join(w, "backups"))], [])
        self.assertEqual(lib.import_backup(s, self.lib, mode="copy")["skipped"], False)

    def test_scan_rules(self):
        from mcsm import library as lib
        top = os.path.join(self.tmp, "top")
        os.makedirs(os.path.join(top, "logs"))                    # a stray logs folder at the top
        s = self._server("srv", 2)
        launcher = os.path.join(top, "client")                    # a game launcher folder
        os.makedirs(os.path.join(launcher, "logs"))
        os.makedirs(os.path.join(launcher, "saves"))
        open(os.path.join(launcher, "options.txt"), "w").close()
        open(os.path.join(launcher, "usercache.json"), "w").write("[]")
        _zip_dir(s, os.path.join(top, "A.zip"))
        shutil.copytree(s, os.path.join(top, "A", "srv"))
        shutil.rmtree(os.path.join(top, "A", "srv", "world"))     # a cancelled unpack
        _zip_dir(self._server("third", 4), os.path.join(top, "world.zip"))
        import tarfile
        with tarfile.open(os.path.join(top, "world.tar"), "w") as tf:   # same stem, other content
            tf.add(self._server("other", 7), arcname="srv")
        plan = lib.scan(top, self.lib)
        picked = sorted(os.path.relpath(c["path"], top) for c in plan["backups"])
        self.assertEqual(picked, ["A.zip", "world.tar", "world.zip"])
        same = {os.path.relpath(c["path"], top): c["why"] for c in plan["same"]}
        self.assertIn("incomplete", same[os.path.join("A", "srv")])

    def test_tar_hard_links_and_overlap(self):
        import io as _io
        import tarfile
        from mcsm import library as lib
        s = self._server("a", 1)
        tgz = os.path.join(self.tmp, "b.tgz")
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(s, arcname="srv")
            link = tarfile.TarInfo("srv/ops-copy.json")
            link.type, link.linkname = tarfile.LNKTYPE, "srv/ops.json"
            tf.addfile(link)
        r = lib.import_backup(tgz, self.lib, mode="copy")
        self.assertTrue(os.path.isfile(os.path.join(r["backup"], "ops-copy.json")))
        with self.assertRaises(ValueError):
            lib.import_backup(self.tmp, self.lib)                 # the source contains the library

    def test_same_name_recordings_of_two_players_both_kept(self):
        from mcsm.archive import merge
        from mcsm import models as mm
        d1 = analyze(self._server("a", 2))
        r = d1.replays[0]
        twin = mm.Replay(**{**r.__dict__})
        twin.recorded_player = "Alex"
        d1.replays.append(twin)
        out = merge([("a", "a", d1.root, d1)])
        self.assertEqual(sum(1 for x in out.replays if os.path.basename(x.path) == os.path.basename(r.path)), 2)

    def test_tidy_moves_duplicates_and_undo(self):
        from mcsm import library as lib
        top = os.path.join(self.tmp, "Game Backup")
        s = self._server("srv", 2)
        os.makedirs(os.path.join(top, "June09"))
        _zip_dir(s, os.path.join(top, "June09", "A.zip"))
        shutil.copytree(s, os.path.join(top, "June09", "A", "srv"))      # complete unpacked copy
        os.makedirs(os.path.join(top, "2026-01-01 Already dated"))
        shutil.copytree(self._server("other", 9), os.path.join(top, "2026-01-01 Already dated", "srv"))
        before = sorted(os.path.relpath(os.path.join(c, f), top) for c, _d, fs in os.walk(top) for f in fs)
        plan = lib.scan(top, self.lib)
        moves = lib.tidy_plan(plan, rename=True)
        self.assertEqual([os.path.relpath(m["to"], top) for m in moves],
                         [os.path.join(lib.DUPLICATES_DIR, "June09", "A.zip"),
                          datetime.fromtimestamp(plan["backups"][-1]["when"]).strftime("%Y-%m-%d") + " June09"])
        lib.tidy_apply(plan, moves)
        day = os.path.basename(moves[1]["to"])
        self.assertTrue(os.path.isfile(os.path.join(top, lib.DUPLICATES_DIR, "June09", "A.zip")))
        self.assertTrue(os.path.isdir(os.path.join(top, day, "A", "srv")))
        self.assertTrue(os.path.isdir(os.path.join(top, "2026-01-01 Already dated")))   # left alone
        lib.tidy_undo(top)
        after = sorted(os.path.relpath(os.path.join(c, f), top) for c, _d, fs in os.walk(top) for f in fs)
        self.assertEqual(after, before)                                          # exactly as it was
        self.assertFalse(os.path.exists(os.path.join(top, lib.DUPLICATES_DIR)))

    def test_rescan_skips_imported_without_unpacking(self):
        import tarfile
        from mcsm import library as lib
        top = os.path.join(self.tmp, "top")
        os.makedirs(top)
        s = self._server("srv", 3)
        with tarfile.open(os.path.join(top, "A.tgz"), "w:gz") as tf:
            tf.add(s, arcname="srv")
        r = lib.import_all(lib.scan(top, self.lib), self.lib)
        self.assertEqual(len(r["imported"]), 1)
        plan = lib.scan(top, self.lib)
        self.assertTrue(plan["backups"][0]["in_library"])
        self.assertIn("0 new, 1 already in the library", lib.describe_plan(plan))
        calls = []
        real = lib.import_backup
        lib.import_backup = lambda *a, **k: calls.append(a) or real(*a, **k)
        try:
            r2 = lib.import_all(plan, self.lib)
        finally:
            lib.import_backup = real
        self.assertEqual(calls, [])                                   # nothing unpacked again
        self.assertEqual(len(r2["skipped"]), 1)

    def test_video_projects_copy_flashback_edits(self):
        from mcsm import library as lib, flashback as fbm
        s = self._server("srv", 2)
        lib.import_backup(s, self.lib, mode="copy")
        w = lib.list_worlds(self.lib)[0]["path"]
        # a Flashback replay + its edits in a PrismLauncher-style instance
        appdata = os.path.join(self.tmp, "appdata")
        inst = os.path.join(appdata, "PrismLauncher", "instances", "Flashback 26.1.2")
        states = os.path.join(inst, "minecraft", "flashback", "editor_states")
        os.makedirs(states)
        os.makedirs(os.path.join(inst, "minecraft", "flashback", "replays"))
        with open(os.path.join(inst, "mmc-pack.json"), "w") as fh:
            json.dump({"components": [{"uid": "net.minecraft", "version": "26.1.2"}]}, fh)
        uid = "882f9442-d18e-4763-ae18-d8f9672346c5"
        with open(os.path.join(states, f"{uid}.json"), "w") as fh:
            json.dump({"scenes": [{"name": "Fishing"}]}, fh)
        proj = os.path.join(self.tmp, "Edited", "EXP2 - Fishing", "Daragh")
        os.makedirs(proj)
        with zipfile.ZipFile(os.path.join(proj, "2026-05-24--18-57-25.zip"), "w") as zf:
            zf.writestr("metadata.json", json.dumps({"uuid": uid, "version_string": "26.1.2", "total_ticks": 10,
                                                     "chunks": {}}))
            zf.writestr("c0.flashback", b"\xd7\x80\xe8\x84\x00")
        old_env = os.environ.get("APPDATA")
        os.environ["APPDATA"] = appdata
        try:
            found = fbm.flashback_folders()
            self.assertIn(("Flashback 26.1.2", "26.1.2"), {(f["name"], f["mc"]) for f in found})
            self.assertTrue(fbm.find_flashback_dir(None, "26.1.2").startswith(inst))
            before = sorted(os.listdir(states))
            r = lib.add_projects(os.path.join(self.tmp, "Edited"), w)
            self.assertEqual(sorted(os.listdir(states)), before)       # launcher files untouched
        finally:
            if old_env is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = old_env
        self.assertEqual(r["editor_states"], 1)
        pr = lib.list_projects(w)
        self.assertEqual([p["name"] for p in pr], ["EXP2 - Fishing"])
        rec = pr[0]["recordings"][0]
        self.assertEqual(rec["perspective"], "Daragh")
        self.assertEqual(rec["uuid"], uid)
        self.assertTrue(os.path.isfile(os.path.join(w, rec["file"])))
        self.assertTrue(os.path.isfile(os.path.join(w, rec["editor_state"])))
        # adding it again stores nothing new
        r2 = lib.add_projects(os.path.join(self.tmp, "Edited"), w)
        self.assertEqual(r2["recordings_added"], 0)


class TestVideoText(unittest.TestCase):
    """Lining a finished Flashback video up with the server history (mcsm.videotext)."""

    SCENE = {"name": "Meeting Room", "exportStartTicks": 21105, "exportEndTicks": 40237,
             "keyframeTracks": [{"keyframeType": "TIMELAPSE", "enabled": True, "keyframesByTick": {
                 "22051": {"type": "timelapse", "ticks": 0}, "34309": {"type": "timelapse", "ticks": 600},
                 "37395": {"type": "timelapse", "ticks": 0}, "38398": {"type": "timelapse", "ticks": 280}}}]}

    def test_clock_matches_flashback(self):
        from mcsm import videotext as vt
        c = vt.scene_clock(self.SCENE)
        # the owner's real export of this scene is 337.57 s long
        self.assertAlmostEqual(c.duration, 337.6, delta=0.1)
        self.assertAlmostEqual(c.video_time(22051), (22051 - 21105) / 20, places=6)      # normal speed first
        self.assertAlmostEqual(c.video_time(34309) - c.video_time(22051), 30.0, places=6)  # 600 ticks = 30 s
        self.assertAlmostEqual(c.speed_at(30000), 12258 / 600, places=6)
        self.assertEqual(c.speed_at(35000), 1.0)                                         # backwards pair: normal
        self.assertIsNone(c.video_time(100))
        for t in (0, 12.5, 77, 200, 330):
            self.assertAlmostEqual(c.video_time(c.replay_tick(t)), t, places=6)
        self.assertIsNone(vt.scene_clock({"exportStartTicks": -1, "exportEndTicks": -1}))
        self.assertAlmostEqual(vt.scene_clock({"exportStartTicks": -1, "exportEndTicks": -1}, 2000).duration, 100.0)

    def test_texts_and_files(self):
        from mcsm import videotext as vt
        tmp = tempfile.mkdtemp()
        try:
            data = analyze(build_server(os.path.join(tmp, "srv")))
            ev = next(e for e in data.events if e.type == "chat")
            r = m.Replay(path="x.zip", format="flashback", duration_ms=200_000, start=ev.time - timedelta(seconds=60),
                         raw_meta={"uuid": "u1", "total_ticks": 4000})
            data.replays.append(r)
            self.assertIs(vt._replay_for(data, "u1"), r)
            clock = vt.scene_clock({"exportStartTicks": 400, "exportEndTicks": 4000, "keyframeTracks": []})
            texts = vt.suggest_texts(data, r, clock)
            chat = [t for t in texts if t.kind == "chat" and ev.text in t.text]
            self.assertEqual(len(chat), 1)
            self.assertAlmostEqual(chat[0].start, 40.0, places=1)     # 60 s into the recording, export from 20 s
            self.assertEqual(texts[0].kind, "date")
            ass = vt.write_ass(texts, os.path.join(tmp, "v.ass"), 3840, 2160)
            body = open(ass, encoding="utf-8-sig").read()
            self.assertIn("PlayResX: 3840", body)
            self.assertIn(f"> {ev.text}", body)
            srt = open(vt.write_srt(texts, os.path.join(tmp, "v.srt")), encoding="utf-8").read()
            self.assertIn("00:00:40,000 -->", srt)
            vt.save_texts(texts, os.path.join(tmp, "v.json"))
            self.assertEqual(len(vt.load_texts(os.path.join(tmp, "v.json"))), len(texts))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_replay_on_other_drive_is_read(self):
        from mcsm.replays import _read_any
        import mcsm.replays as rp
        real = os.path.relpath

        def other_drive(path, start=None):
            raise ValueError("path is on mount 'F:', start on mount 'D:'")
        tmp = tempfile.mkdtemp()
        try:
            srv = build_server(os.path.join(tmp, "srv"))
            mcpr = os.path.join(srv, "recordings", "players", "Steve", "2024-05-03--18-00-00.mcpr")
            rp.os.path.relpath = other_drive
            try:
                r = _read_any(mcpr, srv, {}, None, None)
            finally:
                rp.os.path.relpath = real
            self.assertTrue(r.path.endswith("2024-05-03--18-00-00.mcpr"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestWorldText(unittest.TestCase):
    """In-world text: packets must match what Minecraft itself writes (bytes from the 26.1.2 / 26.2 /
    26.3 server jars' own codecs, encoded 2026-11-22)."""

    ADD = {775: "c0c4070123456789abcdef0fedcba987654321830140592000000000004051900000000000c0691800000000000000400000",
           776: "c0c4070123456789abcdef0fedcba987654321840140592000000000004051900000000000c0691800000000000000400000",
           777: "c0c4070123456789abcdef0fedcba987654321870140592000000000004051900000000000c0691800000000000000400000"}
    DATA = ("c0c4070c274100000041000000410000000f000011034080000017050800155468616e6b20796f7520666f7220706c6179696e67"
            "1801e8071901001b0001ff")
    STYLED_TEXT = "0a080005636f6c6f720004676f6c64080004746578740004476f6c64010004626f6c640100"

    def test_packets_match_the_game(self):
        import uuid
        from mcsm import worldtext as wt
        uid = uuid.UUID("0123456789abcdef0fedcba987654321")
        for proto, golden in self.ADD.items():
            type_id = wt.PROTOCOLS[proto][2]
            self.assertEqual(wt.add_entity_packet(123456, uid, type_id, 100.5, 70.25, -200.75, yaw=90.0).hex(), golden)
        t = wt.WorldText("Thank you for playing", 0, 0, 0, size=2.0, facing="fixed", glow=False, view_range=4.0,
                         line_width=1000)
        self.assertEqual(wt.text_data_packet(123456, t).hex(), self.DATA)
        self.assertEqual(wt.text_component("Gold", "gold", True).hex(), self.STYLED_TEXT)

    def test_copy_with_text(self):
        import io as _io
        from mcsm import worldtext as wt
        from mcsm.replays import write_flashback_chunk
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "rec.zip")
            buf = _io.BytesIO()
            write_flashback_chunk(buf, 1_700_000_000_000, ticks_before=3)
            chunk = buf.getvalue()
            with zipfile.ZipFile(src, "w") as zf:
                zf.writestr("metadata.json", json.dumps({"uuid": "u-orig", "name": "Rec", "protocol_version": 777,
                                                         "version_string": "26.3", "total_ticks": 10}))
                zf.writestr("c0.flashback", chunk)
                zf.writestr("c1.flashback", chunk)
                zf.writestr("level_chunk_caches/0", b"x" * 100)
            dst = os.path.join(tmp, "rec (text).zip")
            meta = wt.write_world_text_copy(src, dst, [wt.WorldText("Our First Adventure", 1, 70, 2, colour="gold")])
            self.assertEqual(meta["_chunks"], 2)
            self.assertEqual(meta["original_uuid_mcsm"], "u-orig")
            self.assertNotEqual(meta["uuid"], "u-orig")
            with zipfile.ZipFile(dst) as zf:
                new = zf.read("c0.flashback")
                self.assertEqual(zf.read("level_chunk_caches/0"), b"x" * 100)
            self.assertIn(b"Our First Adventure", new)
            self.assertGreater(len(new), len(chunk))
            self.assertEqual(new[-(len(chunk) - (chunk.index(b"game_packet") + 0)):][-20:], chunk[-20:])  # records kept
            # the snapshot grew by exactly the two added records; everything after it is unchanged
            i = 4
            n, i = wt._read_varint(chunk, i)
            for _ in range(n):
                ln, i = wt._read_varint(chunk, i)
                i += ln
            old_size = struct.unpack(">I", chunk[i:i + 4])[0]
            new_size = struct.unpack(">I", new[i:i + 4])[0]
            self.assertEqual(new[i + 4 + new_size:], chunk[i + 4 + old_size:])
            with zipfile.ZipFile(src) as zf:
                self.assertEqual(zf.read("c0.flashback"), chunk)                         # original untouched
            with zipfile.ZipFile(src, "a") as zf:
                pass
            bad = os.path.join(tmp, "old.zip")
            with zipfile.ZipFile(bad, "w") as zf:
                zf.writestr("metadata.json", json.dumps({"protocol_version": 770, "version_string": "1.21.5"}))
            with self.assertRaises(ValueError):
                wt.write_world_text_copy(bad, os.path.join(tmp, "x.zip"), [wt.WorldText("a", 0, 0, 0)])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_in_front_of_camera(self):
        from mcsm import worldtext as wt
        scene = {"keyframeTracks": [{"keyframeType": "CAMERA", "enabled": True, "keyframesByTick": {
            "0": {"position": [0, 64, 0], "yaw": 0, "pitch": 0}, "100": {"position": [10, 64, 0], "yaw": 90, "pitch": 0}}}]}
        pos, yaw, pitch = wt.camera_at(scene, 50)
        self.assertEqual(pos, [5.0, 64.0, 0.0])
        self.assertAlmostEqual(yaw, 45.0)
        x, y, z = wt.in_front([0, 64, 0], 0, 0, 20)          # yaw 0 looks south (+z)
        self.assertAlmostEqual(z, 20.0)
        self.assertAlmostEqual(x, 0.0)
        x, y, z = wt.in_front([0, 64, 0], 90, 0, 20)         # yaw 90 looks west (-x)
        self.assertAlmostEqual(x, -20.0)


class TestVideoMaking(unittest.TestCase):
    """Finding the edit of a video whose edit changed later; titles; the app's Videos tab."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mcsm-vid-")
        self.fb = os.path.join(self.tmp, "flashback")
        os.makedirs(os.path.join(self.fb, "editor_states"))
        os.makedirs(os.path.join(self.fb, "replays"))
        self.old_env = os.environ.get("MCSM_FLASHBACK_DIR"), os.environ.get("APPDATA")
        os.environ["MCSM_FLASHBACK_DIR"] = self.fb
        os.environ["APPDATA"] = os.path.join(self.tmp, "appdata")
        # an edit whose export (55011..56533 = 76.1 s) was lengthened after a 62.4 s video was exported
        self.state = os.path.join(self.fb, "editor_states", "73f0f1f3-0000-0000-0000-000000000000.json")
        cam = lambda x: {"position": [x, 110.0, -93.0], "yaw": -4.4, "pitch": -5.6}   # noqa: E731
        with open(self.state, "w") as fh:
            json.dump({"scenes": [{"name": "Scene 1", "exportStartTicks": 55011, "exportEndTicks": 56533,
                                   "keyframeTracks": [{"keyframeType": "CAMERA", "enabled": True, "keyframesByTick": {
                                       "55048": cam(45.0), "56164": cam(32.7), "56281": cam(32.7)}}]}]}, fh)
        self.video_mtime = time.time() - 3600
        os.utime(self.state, (time.time(), time.time()))

    def tearDown(self):
        for key, val in zip(("MCSM_FLASHBACK_DIR", "APPDATA"), self.old_env):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_changed_edit_is_found_and_still_end(self):
        from mcsm import videotext as vt
        found = vt.find_scenes_for_video(62.4, video_mtime=self.video_mtime)
        self.assertEqual(len(found), 1)
        m_ = found[0]
        self.assertEqual((m_["match"], m_["start"], m_["end"]), ("probable", 55011, 56259))
        sc = vt.scene_for_match(m_)
        self.assertEqual(vt.still_camera_at_end(sc), (56164.0, 56259.0))
        self.assertAlmostEqual(vt.scene_clock(sc).video_time(56164), 57.65, places=2)
        # an edit saved before the video can't be a changed version of it
        self.assertEqual(vt.find_scenes_for_video(62.4, video_mtime=time.time() + 60), [])
        # a copy made by mcsm is only offered when nothing else matches
        from mcsm import worldtext as wt
        wt.copy_edit(self.state, os.path.join(self.fb, "editor_states", "copy.json"), "73f0f1f3", (55011, 56259))
        found = vt.find_scenes_for_video(62.4, video_mtime=self.video_mtime)
        self.assertEqual([f["uuid"] for f in found], ["73f0f1f3-0000-0000-0000-000000000000"])

    def test_titles_and_burn_in(self):
        from mcsm import videotext as vt
        ass = vt.titles_ass([vt.Title("Welcome to", 1, 3, size=0.07, y=0.16),
                             vt.Title("Our First Expedition", 1, 3, size=0.13, y=0.28, style="3d", colour="gold")],
                            1920, 1080, font="Arial Black")
        self.assertIn(r"\bord", ass)
        self.assertIn(r"\fad(700,0)", ass)
        self.assertNotIn("\x08", ass)                                    # no broken escape codes
        self.assertNotIn("\x0c", ass)
        self.assertGreater(ass.count("Our First Expedition"), 5)          # 3D = layered copies
        ff = vt.find_tool("ffmpeg")
        if not ff:
            self.skipTest("ffmpeg not installed")
        src = os.path.join(self.tmp, "clip.mp4")
        subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=3:r=10", "-pix_fmt",
                        "yuv420p", src], check=True)
        out = vt.make_on_screen(src, [("Hello", 0.2)], 0.5, 3)
        self.assertTrue(out.endswith("clip (with text).mp4") and os.path.getsize(out) > 0)
        with self.assertRaises(FileExistsError):
            vt.burn_in(src, ass, out)

    def test_gui_videos_tab(self):
        if not _have_display():
            self.skipTest("no display")
        from mcsm import gui
        root = _new_tk()
        try:
            app = gui.App(root)
            self.assertIn("Videos", [app.nb.tab(t, "text") for t in app.nb.tabs()])
            # the text controls live in the live editor now; the tab only picks the video
            self.assertFalse(hasattr(app, "vid_big_var") or hasattr(app, "_vid_lines"))
            self.assertEqual(app.vid_status_var.get(), "Choose a video.")
        finally:
            root.destroy()
            del root
            import gc
            gc.collect()


class TestVersioning(unittest.TestCase):
    def test_one_version_everywhere(self):
        import re
        import mcsm
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        py = open(os.path.join(root, "pyproject.toml"), encoding="utf-8").read()
        iss = open(os.path.join(root, "installer", "mcsm.iss"), encoding="utf-8").read()
        self.assertEqual(re.search(r'^version = "([^"]+)"', py, re.M).group(1), mcsm.__version__)
        self.assertEqual(re.search(r'#define MyAppVersion "([^"]+)"', iss).group(1), mcsm.__version__)
        self.assertRegex(mcsm.__version__, r"^\d+\.\d+\.\d+$")
        self.assertTrue(mcsm.version_string().startswith(mcsm.__version__))


class TestButtonTips(unittest.TestCase):
    def test_every_button_explains_itself(self):
        if not _have_display():
            self.skipTest("no display")
        from tkinter import ttk
        from mcsm import gui
        root = _new_tk()
        try:
            app = gui.App(root)
            self.assertEqual(app.missing_tips, [], "buttons without a description in gui.BUTTON_TIPS")
            btn = next(c for c in app.winfo_children()[0].winfo_children() if isinstance(c, ttk.Button))
            tip = gui._Tip(btn, "hello")
            tip._show()
            self.assertIsNotNone(tip.win)
            tip._hide()
            self.assertIsNone(tip.win)
        finally:
            root.destroy()
            del root
            import gc
            gc.collect()


class TestWorldTextTiming(unittest.TestCase):
    """Text that appears at one replay tick and goes away at another."""

    @staticmethod
    def _chunk(ticks: int) -> bytes:
        from mcsm import worldtext as wt
        names = [wt.GAME_PACKET, wt.NEXT_TICK]
        out = struct.pack(">I", wt.FLASHBACK_MAGIC) + wt._varint(len(names))
        for n in names:
            out += wt._varint(len(n)) + n.encode()
        out += struct.pack(">I", 0)                                       # empty snapshot
        for _ in range(ticks):
            out += wt._varint(1) + struct.pack(">I", 0)                   # next_tick
        return out

    @staticmethod
    def _events(chunk: bytes):
        """[(tick, game packet id, first payload bytes)] in the snapshot (tick -1) and records."""
        from mcsm import worldtext as wt
        names, i = wt._header(chunk)
        size = struct.unpack(">I", chunk[i:i + 4])[0]
        out, j = [], i + 4
        end = j + size
        tick = -1
        while j < len(chunk):
            if j == end:
                tick = 0
            a, k = wt._read_varint(chunk, j)
            ln = struct.unpack(">I", chunk[k:k + 4])[0]
            payload = chunk[k + 4:k + 4 + ln]
            j = k + 4 + ln
            if names[a] == wt.NEXT_TICK:
                tick = max(tick, 0) + 1
            else:
                pid, _ = wt._read_varint(payload, 0)
                out.append((tick, pid))
        return out

    def test_appear_and_disappear(self):
        from mcsm import worldtext as wt
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "rec.zip")
            with zipfile.ZipFile(src, "w") as zf:
                zf.writestr("metadata.json", json.dumps({
                    "uuid": "u", "protocol_version": 775, "total_ticks": 30,
                    "chunks": {"c0.flashback": {"duration": 10}, "c1.flashback": {"duration": 10},
                               "c2.flashback": {"duration": 10}}}))
                for n in range(3):
                    zf.writestr(f"c{n}.flashback", self._chunk(10))
            dst = os.path.join(tmp, "out.zip")
            # appears at replay tick 4 (in c0), goes away at tick 25 (in c2)
            wt.write_world_text_copy(src, dst, [wt.WorldText("Hi", 0, 70, 0, appear_tick=4, disappear_tick=25)])
            with zipfile.ZipFile(dst) as zf:
                c0, c1, c2 = (self._events(zf.read(f"c{n}.flashback")) for n in range(3))
            add, data, remove = 1, 99, 77
            self.assertEqual(c0, [(4, add), (4, data)])              # spawned 4 ticks into the first file
            self.assertEqual(c1, [(-1, add), (-1, data)])            # already there when jumping to c1
            self.assertEqual(c2, [(-1, add), (-1, data), (5, remove)])  # removed 5 ticks into c2 (tick 25)
            # no timing: in every snapshot, never removed
            dst2 = os.path.join(tmp, "out2.zip")
            wt.write_world_text_copy(src, dst2, [wt.WorldText("Hi", 0, 70, 0)])
            with zipfile.ZipFile(dst2) as zf:
                self.assertEqual(self._events(zf.read("c2.flashback")), [(-1, add), (-1, data)])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestTextAnimation(unittest.TestCase):
    """Animated text: in the world (game-checked packets, per-word layout, timing) and on screen."""

    def test_animation_packets_match_the_game(self):
        from mcsm import worldtext as wt
        # encoded by Minecraft 26.1.2's own codec (2026-11-22)
        self.assertEqual(wt.scale_packet(7, 2.0, 5).hex(), "070801000901050c27400000004000000040000000ff")
        self.assertEqual(wt.opacity_packet(7, 128).hex(), "071a0080ff")

    def test_words_are_laid_out_along_the_line(self):
        from mcsm import worldtext as wt
        t = wt.WorldText("Our First Expedition", 0, 100, 0, size=5.0, camera_yaw=0.0, appear_tick=100,
                         animation="words-pop", word_gap_ticks=6)
        parts = wt.split_words(t)
        self.assertEqual([p.text for p in parts], ["Our", "First", "Expedition"])
        self.assertEqual([p.appear_tick for p in parts], [100, 106, 112])
        self.assertEqual({p.animation for p in parts}, {"pop"})
        xs = [p.x for p in parts]                    # camera looks south: its right is -x
        self.assertTrue(xs[0] > xs[1] > xs[2])
        self.assertAlmostEqual(sum(p.z for p in parts), 0.0)
        self.assertEqual(wt.text_width_px("Our"), 18)
        self.assertEqual(wt.text_width_px("il"), 5)

    def test_pop_and_fade_timeline(self):
        from mcsm import worldtext as wt
        pop = wt._plan_entity(1, wt.WorldText("Hi", 0, 0, 0, appear_tick=50, animation="pop", anim_ticks=10), 1, 99, 131)
        self.assertEqual([u[0] for u in pop.updates], [51, 57])          # grow too big, then settle
        self.assertIn(b"\x09\x01\x06", pop.updates[0][1])               # interpolated over 6 ticks
        self.assertIn(b"\x09\x01\x00", pop.updates[0][2])               # instant copy for snapshots
        fade = wt._plan_entity(2, wt.WorldText("Hi", 0, 0, 0, appear_tick=50, disappear_tick=200, animation="fade",
                                               anim_ticks=4, fade_out_ticks=4), 1, 99, 131)
        self.assertEqual([u[0] for u in fade.updates], [51, 52, 53, 54, 196, 197, 198, 199])
        self.assertTrue(fade.updates[-1][1].endswith(bytes([26]) + b"\x00\x1a\xff"))  # ends faint, then removed

    def test_screen_animations(self):
        from mcsm import videotext as vt
        titles = vt.screen_titles([("Welcome to", 0.07), ("Our First Expedition", 0.13)], 10.0, 20.0,
                                  animation="words-pop", anim=0.4, word_gap=0.3)
        self.assertAlmostEqual(titles[1].start, 10.0 + 0.3 + 0.4)       # the big line follows the small one
        ass = vt.titles_ass(titles, 1920, 1080, font="Arial Black")
        self.assertIn(r"{\alpha&HFF&\fscy0\t(300,301,\alpha&H00&)\t(300,700,0.6,\fscy100)}First", ass)
        pop = vt.titles_ass(vt.screen_titles([("Hi", 0.1)], 0, 5, animation="pop", anim=0.5), 1920, 1080, "x")
        self.assertIn(r"\fscx0\fscy0\t(0,300,\fscx118\fscy118)\t(300,500,\fscx100\fscy100)", pop)
        boxed = vt.titles_ass(vt.screen_titles([("Hi", 0.1)], 0, 5, box="black"), 1920, 1080, "x")
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:05.00,box", boxed)


class TestNoTextResidue(unittest.TestCase):
    """A copy with text must never become the source of another copy (its old text would stay)."""

    def test_copy_with_text_is_refused_and_ids_differ(self):
        from mcsm import worldtext as wt
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "rec.zip")
            chunk = TestWorldTextTiming._chunk(5)
            with zipfile.ZipFile(src, "w") as zf:
                zf.writestr("metadata.json", json.dumps({"uuid": "orig", "protocol_version": 775,
                                                         "chunks": {"c0.flashback": {"duration": 5}}}))
                zf.writestr("c0.flashback", chunk)
            a, b = os.path.join(tmp, "a.zip"), os.path.join(tmp, "b.zip")
            wt.write_world_text_copy(src, a, [wt.WorldText("One", 0, 70, 0)])
            wt.write_world_text_copy(src, b, [wt.WorldText("Two", 0, 70, 0)])
            with self.assertRaises(ValueError):                       # text on top of text: refused
                wt.write_world_text_copy(a, os.path.join(tmp, "c.zip"), [wt.WorldText("Three", 0, 70, 0)])

            def first_id(path):
                with zipfile.ZipFile(path) as zf:
                    data = zf.read("c0.flashback")
                names, i = wt._header(data)
                size = struct.unpack(">I", data[i:i + 4])[0]
                snap = data[i + 4:i + 4 + size]
                _a, k = wt._read_varint(snap, 0)
                payload = snap[k + 4:]
                _pid, k = wt._read_varint(payload, 0)
                eid, _ = wt._read_varint(payload, k)
                return eid
            self.assertNotEqual(first_id(a), first_id(b))              # copies never share entity ids
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_analysis_goes_back_to_the_original(self):
        from mcsm import videotext as vt
        tmp = tempfile.mkdtemp()
        try:
            fb = os.path.join(tmp, "fb")
            os.makedirs(os.path.join(fb, "editor_states"))
            os.makedirs(os.path.join(fb, "replays"))
            scene = {"scenes": [{"name": "S", "exportStartTicks": 0, "exportEndTicks": 200, "keyframeTracks": []}]}
            for u in ("orig", "copy1"):
                with open(os.path.join(fb, "editor_states", u + ".json"), "w") as fh:
                    json.dump(scene, fh)                               # Flashback dropped any mark in the edit
            for name, meta in (("orig.zip", {"uuid": "orig"}),
                               ("copy1.zip", {"uuid": "copy1", "original_uuid_mcsm": "orig", "mcsm_world_text": [{}]})):
                with zipfile.ZipFile(os.path.join(fb, "replays", name), "w") as zf:
                    zf.writestr("metadata.json", json.dumps(meta))
            video = os.path.join(tmp, "v.mp4")
            open(video, "wb").close()
            old = os.environ.get("MCSM_FLASHBACK_DIR"), vt.video_duration, vt.video_size
            os.environ["MCSM_FLASHBACK_DIR"] = fb
            vt.video_duration, vt.video_size = (lambda v: 10.0), (lambda v: (1920, 1080))
            try:
                info = vt.analyse_video(video)
            finally:
                vt.video_duration, vt.video_size = old[1], old[2]
                if old[0] is None:
                    os.environ.pop("MCSM_FLASHBACK_DIR", None)
                else:
                    os.environ["MCSM_FLASHBACK_DIR"] = old[0]
            self.assertEqual(info["matches"][0]["uuid"], "orig")
            self.assertTrue(info["matches"][0]["recording"].endswith("orig.zip"))
            for m_ in info["matches"]:                                 # even the copy's edit points at the original
                self.assertTrue(m_["recording"].endswith("orig.zip"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestWorldPlacement(unittest.TestCase):
    def test_projection(self):
        from mcsm import videotext as vt
        cam = [0.0, 64.0, 0.0]
        x, y, ppb = vt.project(cam, 0.0, 0.0, 70.0, 1920, 1080, (0.0, 64.0, 50.0))   # straight ahead (south)
        self.assertAlmostEqual(x, 960)
        self.assertAlmostEqual(y, 540)
        right = vt.project(cam, 0.0, 0.0, 70.0, 1920, 1080, (-5.0, 64.0, 50.0))       # -x is the camera's right
        self.assertGreater(right[0], 960)
        up = vt.project(cam, 0.0, 0.0, 70.0, 1920, 1080, (0.0, 70.0, 50.0))
        self.assertLess(up[1], 540)
        self.assertIsNone(vt.project(cam, 0.0, 0.0, 70.0, 1920, 1080, (0.0, 64.0, -10.0)))   # behind
        self.assertAlmostEqual(vt.fov_at({"keyframeTracks": [{"keyframeType": "FOV", "keyframesByTick": {
            "0": {"fov": 30}, "100": {"fov": 70}}}]}, 50), 50.0)


class TestReplaceTextCopy(unittest.TestCase):
    def test_making_again_replaces_the_copy_and_keeps_its_edit(self):
        from mcsm import videotext as vt
        tmp = tempfile.mkdtemp()
        try:
            fb = os.path.join(tmp, "fb")
            os.makedirs(os.path.join(fb, "editor_states"))
            os.makedirs(os.path.join(fb, "replays"))
            state = os.path.join(fb, "editor_states", "orig.json")
            cam = {"position": [0, 64, 0], "yaw": 0, "pitch": 0}
            with open(state, "w") as fh:
                json.dump({"scenes": [{"name": "S", "exportStartTicks": 0, "exportEndTicks": 10, "keyframeTracks": [
                    {"keyframeType": "CAMERA", "keyframesByTick": {"0": cam, "10": cam}}]}]}, fh)
            rec = os.path.join(tmp, "orig.zip")
            with zipfile.ZipFile(rec, "w") as zf:
                zf.writestr("metadata.json", json.dumps({"uuid": "orig", "protocol_version": 775, "total_ticks": 10,
                                                         "version_string": "26.1.2",
                                                         "chunks": {"c0.flashback": {"duration": 10}}}))
                zf.writestr("c0.flashback", TestWorldTextTiming._chunk(10))
            match = {"recording": rec, "state": state, "scene": 0, "uuid": "orig", "start": 0, "end": 10,
                     "still_from": 0.0}
            video = os.path.join(tmp, "clip.mp4")
            r1 = vt.make_in_world(match, [("One", 3.0)], None, flashback_dir=fb, video=video)
            with open(r1["edit"], "a") as fh:
                fh.write(" ")                                  # you changed the copy's edit in Flashback
            mark = os.path.getsize(r1["edit"])
            r2 = vt.make_in_world(match, [("Two", 3.0)], None, flashback_dir=fb, video=video)
            self.assertTrue(r2["replaced"])
            self.assertEqual(r2["replay"], r1["replay"])
            self.assertEqual(len(os.listdir(os.path.join(fb, "replays"))), 1)      # no pile of copies
            self.assertEqual(os.path.getsize(r2["edit"]), mark)                    # your edit kept
            with zipfile.ZipFile(r2["replay"]) as zf:
                self.assertIn(b"Two", zf.read("c0.flashback"))
                self.assertNotIn(b"One", zf.read("c0.flashback"))
            r3 = vt.make_in_world(match, [("Other", 3.0)], None, flashback_dir=fb, video=os.path.join(tmp, "b.mp4"))
            self.assertFalse(r3["replaced"])                                          # another video: own copy
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestVideoEditor(unittest.TestCase):
    """The live editor page's server: video with seeking, camera path, making the world copy."""

    def test_server(self):
        import urllib.request
        from mcsm.videoeditor import VideoEditor
        tmp = tempfile.mkdtemp()
        try:
            video = os.path.join(tmp, "clip.mp4")
            with open(video, "wb") as fh:
                fh.write(bytes(range(256)) * 40)                         # 10240 bytes
            cam = {"position": [0, 64, 0], "yaw": 0, "pitch": 0}
            state = os.path.join(tmp, "s.json")
            with open(state, "w") as fh:
                json.dump({"scenes": [{"name": "S", "exportStartTicks": 0, "exportEndTicks": 40, "keyframeTracks": [
                    {"keyframeType": "CAMERA", "keyframesByTick": {"0": cam, "40": cam}}]}]}, fh)
            info = {"video": video, "duration": 2.0, "width": 1920, "height": 1080,
                    "matches": [{"name": "S", "match": "exact", "state": state, "scene": 0, "uuid": "u", "start": 0,
                                 "end": 40, "recording": None, "still_from": 0.5}]}
            os.environ["LOCALAPPDATA_BACKUP"] = os.environ.get("LOCALAPPDATA", "")
            os.environ["LOCALAPPDATA"] = tmp                                  # projects go to a temp folder
            self.addCleanup(lambda: os.environ.__setitem__("LOCALAPPDATA", os.environ.pop("LOCALAPPDATA_BACKUP")))
            ed = VideoEditor(video, info=info)
            url = ed.start(18790)
            try:
                page = urllib.request.urlopen(url).read()
                self.assertIn(b"Text on your video", page)
                got = json.loads(urllib.request.urlopen(url + "api/info").read())
                self.assertEqual(len(got["camera"]["samples"]), 41)          # every 0.05 s over 2 s
                self.assertEqual(got["camera"]["samples"][0][:3], [0, 65.62, 0])     # eye height above the keyframe
                req = urllib.request.Request(url + "video", headers={"Range": "bytes=100-199"})
                r = urllib.request.urlopen(req)
                self.assertEqual(r.status, 206)
                self.assertEqual(r.headers["Content-Range"], "bytes 100-199/10240")
                self.assertEqual(r.read(), (bytes(range(256)) * 40)[100:200])
                post = urllib.request.Request(url + "api/make", data=json.dumps({"target": "world", "items": [{"kind": "world", "lines": [["Hi", 3]]}]}).encode(),
                                              headers={"Content-Type": "application/json"}, method="POST")
                with self.assertRaises(urllib.error.HTTPError):               # no X-MCSM header: refused
                    urllib.request.urlopen(post)
                post.add_header("X-MCSM", "1")
                self.assertTrue(json.loads(urllib.request.urlopen(post).read())["started"])
                for _ in range(50):
                    job = json.loads(urllib.request.urlopen(url + "api/job").read())
                    if not job["running"]:
                        break
                    time.sleep(0.1)
                self.assertIn("recording", job["error"])                     # no recording in this test: told why
                saved = json.loads(urllib.request.urlopen(url + "api/project").read())
                self.assertEqual(saved["items"][0]["lines"], [["Hi", 3]])      # the texts were saved
            finally:
                ed.stop()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestUpdater(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self.tmp

    def tearDown(self):
        if self.old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_versions_and_settings(self):
        from mcsm import updater
        cur = {"version": "0.2.0", "number": 37}
        self.assertTrue(updater.is_newer({"version": "0.2.0", "number": 38}, cur))
        self.assertFalse(updater.is_newer({"version": "0.2.0", "number": 37}, cur))
        self.assertTrue(updater.is_newer({"version": "0.3.0", "number": 0}, cur))
        self.assertFalse(updater.is_newer({"version": "0.1.9", "number": 99}, cur))
        self.assertTrue(updater.check_due({}))
        self.assertFalse(updater.check_due({"update_channel": "off"}))
        self.assertFalse(updater.check_due({"last_check": time.time()}))
        updater.save_settings({"update_channel": "stable"})
        self.assertEqual(updater.channel(), "stable")

    def test_check_both_channels(self):
        from mcsm import updater
        real = updater._get
        try:
            updater._get = lambda url, timeout=15: json.dumps({
                "version": "0.2.0", "number": 40, "commit": "abc", "sha256": "AB" * 32, "size": 5,
                "file": "MinecraftServerMaster-Setup-latest.exe", "notes": "- New things"}).encode()
            t = updater.check("test")
            self.assertEqual((t["version"], t["number"], t["sha256"]), ("0.2.0", 40, "ab" * 32))
            self.assertTrue(t["url"].endswith("/releases/download/latest-build/MinecraftServerMaster-Setup-latest.exe"))
            updater._get = lambda url, timeout=15: json.dumps({"tag_name": "v0.3.0", "body": "Notes", "assets": [
                {"name": "MinecraftServerMaster-Setup-0.3.0.exe", "browser_download_url": "https://x/y.exe",
                 "size": 9, "digest": "sha256:" + "cd" * 32}]}).encode()
            st = updater.check("stable")
            self.assertEqual((st["version"], st["sha256"], st["size"]), ("0.3.0", "cd" * 32, 9))
            self.assertIsNone(updater.check("off"))
        finally:
            updater._get = real

    def test_download_is_verified(self):
        import hashlib
        import http.server
        import threading
        from mcsm import updater
        body = b"installer-bytes" * 100
        good = hashlib.sha256(body).hexdigest()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}/setup.exe"
            p = updater.download({"url": url, "sha256": good, "size": len(body), "file": "setup.exe"}, dest_dir=self.tmp)
            self.assertEqual(open(p, "rb").read(), body)
            with self.assertRaises(ValueError):                         # wrong fingerprint: not installed
                updater.download({"url": url, "sha256": "00" * 32, "size": len(body), "file": "bad.exe"}, dest_dir=self.tmp)
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "bad.exe")))
            with self.assertRaises(ValueError):                         # no fingerprint published: not installed
                updater.download({"url": url, "sha256": "", "size": len(body), "file": "none.exe"}, dest_dir=self.tmp)
        finally:
            srv.shutdown()


class TestManyTexts(unittest.TestCase):
    """An editor project with several texts at different times: all world texts in one Flashback copy,
    all on-screen texts in one video."""

    def test_items(self):
        from mcsm import videotext as vt
        tmp = tempfile.mkdtemp()
        try:
            fb = os.path.join(tmp, "fb")
            os.makedirs(os.path.join(fb, "editor_states"))
            os.makedirs(os.path.join(fb, "replays"))
            state = os.path.join(fb, "editor_states", "orig.json")
            cam = {"position": [0, 64, 0], "yaw": 0, "pitch": 0}
            with open(state, "w") as fh:
                json.dump({"scenes": [{"name": "S", "exportStartTicks": 0, "exportEndTicks": 200, "keyframeTracks": [
                    {"keyframeType": "CAMERA", "keyframesByTick": {"0": cam, "200": cam}}]}]}, fh)
            rec = os.path.join(tmp, "orig.zip")
            with zipfile.ZipFile(rec, "w") as zf:
                zf.writestr("metadata.json", json.dumps({"uuid": "orig", "protocol_version": 775, "total_ticks": 200,
                                                         "version_string": "26.1.2",
                                                         "chunks": {"c0.flashback": {"duration": 200}}}))
                zf.writestr("c0.flashback", TestWorldTextTiming._chunk(200))
            match = {"recording": rec, "state": state, "scene": 0, "uuid": "orig", "start": 0, "end": 200,
                     "still_from": 1.0}
            items = [{"kind": "world", "lines": [["Early", 3]], "start": 2.0, "until": 4.0, "animation": "pop"},
                     {"kind": "world", "lines": [["Late", 3]], "start": 7.0, "right": 10},
                     {"kind": "screen", "lines": [["On screen", 3]], "start": 1.0, "until": 2.0}]
            r = vt.make_items_in_world(match, items, os.path.join(tmp, "clip.mp4"), fb)
            with zipfile.ZipFile(r["replay"]) as zf:
                data = zf.read("c0.flashback")
            self.assertIn(b"Early", data)
            self.assertIn(b"Late", data)
            self.assertNotIn(b"On screen", data)                     # on-screen texts don't go into the world
            events = TestWorldTextTiming._events(data)
            adds = [t for t, pid in events if pid == 1]
            self.assertIn(40, adds)                                    # "Early" at 2 s = tick 40
            self.assertIn(140, adds)                                   # "Late" at 7 s = tick 140
            self.assertIn((80, 77), events)                            # "Early" removed at 4 s
            with self.assertRaises(ValueError):
                vt.make_items_in_world(match, [items[2]], None, fb)    # no world texts: told so
            ff = vt.find_tool("ffmpeg")
            if ff:
                src = os.path.join(tmp, "clip.mp4")
                subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=3:r=10",
                                "-pix_fmt", "yuv420p", src], check=True)
                out = vt.make_items_on_screen(src, items + [{"kind": "screen", "lines": [["Second", 3]], "start": 2.0,
                                                             "x": 0.2, "y": 0.8, "font": "Arial Black"}], 3.0)
                self.assertTrue(os.path.getsize(out) > 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestWorldPick(unittest.TestCase):
    def test_ray_hits_a_mountain(self):
        from mcsm import worldpick as wp, videotext as vt

        class FlatWithMountain:
            def height(self, x, z):                      # flat ground at 62, a mountain (top 150) from z=300
                return 150 if 300 <= z <= 340 and -20 <= x <= 20 else 62
        cam = [0.0, 80.0, 0.0]
        # the centre of the picture, camera looking south and level: straight into the mountain's side
        d = wp.ray(cam, 0.0, 0.0, 70.0, 1920, 1080, 960, 540)
        self.assertAlmostEqual(d[2], 1.0, places=6)
        hit = wp.hit_ground(FlatWithMountain(), cam, d)
        self.assertTrue(298 <= hit[2] <= 302 and hit[1] == 151.0)
        # the ray through where a point projects comes back to that point (inverse of project)
        sx, sy, _ = vt.project(cam, 30.0, -10.0, 70.0, 1920, 1080, (40.0, 120.0, 90.0))
        d2 = wp.ray(cam, 30.0, -10.0, 70.0, 1920, 1080, sx, sy)
        target = (40.0, 40.0, 90.0)
        n = sum(v * v for v in target) ** 0.5
        for a, b in zip(d2, target):
            self.assertAlmostEqual(a, b / n, places=6)
        # lower in the picture: the flat ground in front
        low = wp.hit_ground(FlatWithMountain(), cam, wp.ray(cam, 0.0, 0.0, 70.0, 1920, 1080, 960, 900))
        self.assertEqual(low[1], 63.0)
        self.assertIsNone(wp.hit_ground(FlatWithMountain(), cam, wp.ray(cam, 0.0, 0.0, 70.0, 1920, 1080, 960, 100)))
        self.assertEqual(wp.recording_date("x/2026-06-06--19-55-14.zip").day, 6)


class TestFollowPlayer(unittest.TestCase):
    def test_text_rides_on_the_player(self):
        import uuid
        from mcsm import videotext as vt, worldtext as wt, paths
        tmp = tempfile.mkdtemp()
        try:
            steve = uuid.UUID(STEVE)
            # a chunk whose snapshot adds Steve (entity 500) with the real add_entity layout, then 20 ticks
            names = [wt.GAME_PACKET, wt.NEXT_TICK]
            add = wt._varint(1) + wt.add_entity_packet(500, steve, 155, 10.0, 70.0, 20.0)
            snap = wt._varint(0) + struct.pack(">I", len(add)) + add
            chunk = struct.pack(">I", wt.FLASHBACK_MAGIC) + wt._varint(len(names))
            for n in names:
                chunk += wt._varint(len(n)) + n.encode()
            chunk += struct.pack(">I", len(snap)) + snap + (wt._varint(1) + struct.pack(">I", 0)) * 20
            rec = os.path.join(tmp, "rec.zip")
            with zipfile.ZipFile(rec, "w") as zf:
                zf.writestr("metadata.json", json.dumps({"uuid": "r", "protocol_version": 775, "total_ticks": 20,
                                                         "version_string": "26.1.2",
                                                         "chunks": {"c0.flashback": {"duration": 20}}}))
                zf.writestr("c0.flashback", chunk)
            players = {steve.bytes: "Steve"}
            seen = paths.players_in_view(rec, 5, players)
            self.assertEqual(seen["Steve"]["eid"], 500)
            self.assertEqual((seen["Steve"]["x"], seen["Steve"]["z"]), (10.0, 20.0))
            fb = os.path.join(tmp, "fb")
            os.makedirs(os.path.join(fb, "editor_states"))
            os.makedirs(os.path.join(fb, "replays"))
            state = os.path.join(fb, "editor_states", "r.json")
            cam = {"position": [0, 70, 0], "yaw": 0, "pitch": 0}
            with open(state, "w") as fh:
                json.dump({"scenes": [{"name": "S", "exportStartTicks": 0, "exportEndTicks": 20, "keyframeTracks": [
                    {"keyframeType": "CAMERA", "keyframesByTick": {"0": cam, "20": cam}}]}]}, fh)
            match = {"recording": rec, "state": state, "scene": 0, "uuid": "r", "start": 0, "end": 20, "still_from": 0.0}
            r = vt.make_items_in_world(match, [{"kind": "world", "lines": [["TACKY", 1.5]], "start": 0.2, "follow": "Steve"}],
                                       None, fb, players=players)
            with zipfile.ZipFile(r["replay"]) as zf:
                ev = TestWorldTextTiming._events(zf.read("c0.flashback"))
            self.assertIn((4, wt.SET_PASSENGERS[775]), ev)          # mounted on Steve when it appears
            with self.assertRaises(ValueError):
                vt.make_items_in_world(match, [{"kind": "world", "lines": [["X", 1]], "start": 0.2, "follow": "Alex"}],
                                       None, fb, players=players)    # not in view: told so
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestAdvancedText(unittest.TestCase):
    """Opacity, rotation, facing, 3D letters, rise/slam, chained texts and pictures."""

    def test_rotation_turns_about_the_pivot(self):
        import math
        import struct
        from mcsm import worldtext as wt
        t = wt.WorldText("Hi", 0, 64, 0, size=1.0, roll=90, pivot=(0.0, 0.5, 0.0))
        tx, ty, tz = wt.translation_of(t)
        # clockwise quarter turn about (0, 0.5): the text's foot (0, 0) goes to (-0.5, 0.5)
        self.assertAlmostEqual(tx, -0.5, places=5)
        self.assertAlmostEqual(ty, 0.5, places=5)
        qx, qy, qz, qw = wt.quaternion_of(t)
        self.assertAlmostEqual(qz, -math.sqrt(0.5), places=5)
        self.assertAlmostEqual(qw, math.sqrt(0.5), places=5)
        pk = wt.text_data_packet(5, t)
        self.assertIn(bytes([13, 40]) + struct.pack(">ffff", qx, qy, qz, qw), pk)   # left rotation, quaternion
        self.assertIn(bytes([11, 39]), pk)                                           # translation
        flat = wt.text_data_packet(5, wt.WorldText("Hi", 0, 64, 0, size=1.0))
        self.assertNotIn(bytes([13, 40]), flat)                                      # unchanged when not turned

    def test_opacity_italic_underline(self):
        from mcsm import worldtext as wt
        pk = wt.text_data_packet(5, wt.WorldText("Hi", 0, 0, 0, opacity=128, italic=True, underline=True))
        self.assertIn(bytes([26, 0, 128]), pk)
        self.assertIn(b"italic", pk)
        self.assertIn(b"underlined", pk)

    def test_depth_layers(self):
        from mcsm import worldtext as wt
        t = wt.WorldText("Big", 0, 64, 0, size=4.0, colour="gold", outline="black", camera_yaw=0.0, depth=0.25)
        parts = wt.with_depth(t)
        self.assertEqual(len(parts), 7)                          # 6 layers + the text
        self.assertEqual(parts[-1].colour, "gold")
        self.assertTrue(all(p.colour.startswith("#") and not p.outline for p in parts[:-1]))
        self.assertTrue(all(p.z > t.z and p.y < t.y for p in parts[:-1]))   # behind (camera looks +z) and lower
        self.assertGreater(parts[0].z, parts[-2].z)              # the first layer is the deepest
        self.assertEqual(wt.with_depth(wt.WorldText("Flat", 0, 0, 0)), [wt.WorldText("Flat", 0, 0, 0)])

    def test_rise_and_slam_plans(self):
        from mcsm import worldtext as wt
        for anim in ("rise", "slam"):
            e = wt._plan_entity(9, wt.WorldText("X", 0, 64, 0, size=1, appear_tick=100, animation=anim, anim_ticks=8),
                                1, 99, 131)
            self.assertIn(bytes([26, 0, 26]), e.start[1])        # starts faint
            self.assertTrue(e.updates and all(t_ > 100 for t_, *_ in e.updates))
            self.assertGreaterEqual(e.settled, 108)

    def test_fixed_facing_and_chains(self):
        from mcsm import videotext as vt
        cam = {"position": [0, 64, 0], "yaw": 30, "pitch": 0}
        match = {"still_from": 1.0}
        sc = {"name": "S", "exportStartTicks": 0, "exportEndTicks": 200, "keyframeTracks": [
            {"keyframeType": "CAMERA", "keyframesByTick": {"0": cam, "200": cam}}]}
        orig = vt.scene_for_match
        vt.scene_for_match = lambda m: sc
        try:
            texts, _ = vt.world_texts(match, [("Top", 2.0), ("Bottom", 3.0)], 2.0, facing="fixed", turn=10, tilt=-90,
                                      roll=5, opacity=0.5)
        finally:
            vt.scene_for_match = orig
        self.assertTrue(all(t.facing == "fixed" and t.local for t in texts))
        self.assertAlmostEqual(texts[0].yaw, 220.0)               # camera yaw + 180 + turn
        self.assertEqual(texts[0].pitch, -90)
        self.assertEqual((texts[0].x, texts[0].y, texts[0].z), (texts[1].x, texts[1].y, texts[1].z))
        self.assertAlmostEqual(texts[0].offset[1], 3.0 + 0.25 * 2.0)   # stacked in its own space
        self.assertEqual(texts[0].opacity, 128)
        items = [vt._item(i) for i in (
            {"id": 1, "kind": "screen", "lines": [["A", 3]], "start": 1.0, "length": 2.0},
            {"id": 2, "kind": "screen", "lines": [["B", 3]], "after": {"id": 1, "gap": 0.5, "from": "end"}, "length": 1},
            {"id": 3, "kind": "image", "file": "x.png", "after": {"id": 2, "gap": 1.0, "from": "start"}},
            {"id": 4, "kind": "screen", "lines": [["Loop", 3]], "start": 9.0, "after": {"id": 5, "gap": 1}},
            {"id": 5, "kind": "screen", "lines": [["Loop", 3]], "start": 8.0, "after": {"id": 4, "gap": 1}},
            {"id": 6, "kind": "screen", "lines": [["Lost", 3]], "after": {"id": 99, "gap": 1}},
            {"kind": "screen", "lines": [["Old", 3]], "style": "3d"})]
        r = vt.resolve_times(items, 20.0, lambda it: 0.2)
        self.assertEqual((r[0]["start"], r[0]["until"]), (1.0, 3.0))
        self.assertEqual((r[1]["start"], r[1]["until"]), (3.5, 4.5))
        self.assertEqual(r[2]["start"], 4.5)                      # 1 s after B appears
        self.assertIsInstance(r[3]["start"], float)               # a loop doesn't hang
        self.assertEqual(r[5]["start"], 0.2)                      # follows nothing that exists: its own time
        self.assertEqual((items[6]["style"], items[6]["depth"]), ("outline", 0.14))   # older "3D" style

    def test_screen_look_and_pictures(self):
        from mcsm import videotext as vt
        titles = vt.screen_titles([("One", 0.1), ("Two", 0.1)], 0, 5, animation="rise", opacity=0.5, roll=10,
                                  italic=True, depth=0.2)
        ass = vt.titles_ass(titles, 1920, 1080, "x")
        self.assertIn("\\frz-10", ass)
        self.assertIn("\\alpha&H7F&", ass)                      # half see-through
        self.assertIn("\\move(", ass)
        self.assertIn("\\i1", ass)
        self.assertEqual(len({t.org for t in titles}), 1)         # both lines turn about one point
        slam = vt.titles_ass(vt.screen_titles([("S", 0.1)], 0, 5, animation="slam"), 1920, 1080, "x")
        self.assertIn("\\fscx260", slam)
        chains, last = vt.image_filters([{"file": "a.png", "start": 1, "end": 4, "x": 0.8, "y": 0.2, "height": 0.2,
                                          "opacity": 0.5, "roll": 15, "animation": "rise", "fade_out": 0.5}], 1280, 720)
        g = ";".join(chains)
        self.assertEqual(last, "v1")
        for part in ("scale=-2:144", "colorchannelmixer=aa=0.500", "rotate=a=15*PI/180", "fade=t=in:st=1.000",
                     "fade=t=out:st=3.500", "between(t,1.000,4.000)", "shortest=1"):
            self.assertIn(part, g)
        ff = vt.find_tool("ffmpeg")
        if not ff:
            self.skipTest("ffmpeg not installed")
        tmp = tempfile.mkdtemp()
        try:
            src, pic = os.path.join(tmp, "clip.mp4"), os.path.join(tmp, "logo.png")
            subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2:r=10", "-pix_fmt",
                            "yuv420p", src], check=True)
            subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=c=yellow:s=40x20", "-frames:v", "1", pic],
                           check=True)
            out = vt.make_items_on_screen(src, [{"id": 1, "kind": "image", "file": "logo.png", "start": 0.2, "x": 0.5,
                                                 "y": 0.5, "height": 0.5, "animation": "none"}], 2.0, image_dir=tmp)
            frame = os.path.join(tmp, "f.rgb")
            subprocess.run([ff, "-v", "error", "-ss", "1", "-i", out, "-frames:v", "1", "-vf", "crop=2:2:159:89",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", frame], check=True)
            with open(frame, "rb") as fh:
                r_, g_, b_ = fh.read()[:3]
            self.assertTrue(r_ > 180 and g_ > 180 and b_ < 90, (r_, g_, b_))      # the yellow picture is drawn
            with self.assertRaises(ValueError):
                vt.make_items_on_screen(src, [{"kind": "image", "file": "gone.png"}], 2.0, image_dir=tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_editor_keeps_pictures(self):
        import base64
        from mcsm import videoeditor as ve
        tmp = tempfile.mkdtemp()
        old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = tmp
        try:
            ed = ve.VideoEditor(os.path.join(tmp, "v.mp4"), info={"video": os.path.join(tmp, "v.mp4"), "duration": 5,
                                                                    "width": 320, "height": 180, "matches": []})
            r = ed.add_image({"name": "Logo.PNG", "data": base64.b64encode(b"\x89PNG fake").decode()})
            self.assertTrue(r["file"].endswith(".png"))
            self.assertEqual(ed.file_for("/image/" + r["file"]), os.path.join(ed.image_dir(), r["file"]))
            self.assertIsNone(ed.file_for("/image/..\\..\\secret.png"))
            self.assertIsNone(ed.file_for("/image/missing.png"))
            with self.assertRaises(ValueError):
                ed.add_image({"name": "evil.exe", "data": "AAAA"})
        finally:
            if old is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old
            shutil.rmtree(tmp, ignore_errors=True)


class TestVideoMusic(unittest.TestCase):
    """The song on Flashback's AUDIO track of the copy's edit, beat markers, and the song in a made video."""

    def _setup(self, tmp):
        fb = os.path.join(tmp, "fb")
        os.makedirs(os.path.join(fb, "editor_states"))
        os.makedirs(os.path.join(fb, "replays"))
        state = os.path.join(fb, "editor_states", "orig.json")
        cam = {"position": [0, 64, 0], "yaw": 0, "pitch": 0, "type": "camera"}
        with open(state, "w") as fh:
            json.dump({"sceneIndex": 0, "scenes": [{"name": "S", "exportStartTicks": 100, "exportEndTicks": 300,
                                                    "keyframeTracks": [{"keyframeType": "CAMERA", "enabled": True,
                                                                        "keyframesByTick": {"100": cam, "300": cam}}]}]}, fh)
        rec = os.path.join(tmp, "orig.zip")
        with zipfile.ZipFile(rec, "w") as zf:
            zf.writestr("metadata.json", json.dumps({"uuid": "orig", "protocol_version": 775, "total_ticks": 300,
                                                     "version_string": "26.1.2", "markers": {"150": {"colour": 1,
                                                                                                     "description": "mine"}},
                                                     "chunks": {"c0.flashback": {"duration": 300}}}))
            zf.writestr("c0.flashback", TestWorldTextTiming._chunk(300))
        match = {"recording": rec, "state": state, "scene": 0, "uuid": "orig", "start": 100, "end": 300,
                 "still_from": None}
        return fb, match

    def test_song_and_beats_in_flashback(self):
        from mcsm import videotext as vt, videomusic as vm
        tmp = tempfile.mkdtemp()
        try:
            fb, match = self._setup(tmp)
            songf = os.path.join(tmp, "song.ogg")
            with open(songf, "wb") as fh:
                fh.write(b"OggS fake")
            music = {"file": songf, "start": 1.0, "offset": 0, "bpm": 120, "first_beat": 0.25, "markers": "beats"}
            r = vt.make_items_in_world(match, [], os.path.join(tmp, "clip.mp4"), fb, music=music,
                                       music_dir=os.path.join(tmp, "music"))
            with zipfile.ZipFile(r["replay"]) as zf:
                meta = json.loads(zf.read("metadata.json"))
            marks = meta["markers"]
            self.assertEqual(marks["150"]["description"], "mine")              # the recording's own marker kept
            ours = {int(k): v for k, v in marks.items() if v["description"].startswith(vm.MARK)}
            # video 1.25 s = tick 125, then every 0.5 s (10 ticks) until the end of the video (tick 300)
            self.assertEqual(sorted(ours)[:3], [125, 135, 145])
            self.assertEqual(ours[125]["colour"], vm.BAR_COLOUR)
            self.assertEqual(ours[135]["colour"], vm.BEAT_COLOUR)
            self.assertEqual(r["beats"], len(ours))
            with open(r["edit"], encoding="utf-8") as fh:
                edit = json.load(fh)
            audio = [t for t in edit["scenes"][0]["keyframeTracks"] if t["keyframeType"] == "AUDIO"]
            self.assertEqual(len(audio), 1)
            (tick, kf), = audio[0]["keyframesByTick"].items()
            self.assertEqual(tick, "120")                                       # video 1.0 s
            self.assertEqual((kf["type"], kf["interpolation_type"]), ("audio", "LINEAR"))
            self.assertTrue(os.path.isabs(kf["path"]) and os.path.isfile(kf["path"]))
            self.assertNotEqual(os.path.abspath(kf["path"]), os.path.abspath(songf))   # a copy the app keeps
            # making it again (another start) replaces the app's track, keeps the user's own tracks
            edit["scenes"][0]["keyframeTracks"].append({"keyframeType": "AUDIO", "enabled": True,
                                                        "keyframesByTick": {"200": {"path": "x.ogg", "type": "audio"}}})
            with open(r["edit"], "w", encoding="utf-8") as fh:
                json.dump(edit, fh)
            vt.make_items_in_world(match, [], os.path.join(tmp, "clip.mp4"), fb, music={**music, "start": 2.0},
                                   music_dir=os.path.join(tmp, "music"))
            with open(r["edit"], encoding="utf-8") as fh:
                tracks = json.load(fh)["scenes"][0]["keyframeTracks"]
            audio = [t for t in tracks if t["keyframeType"] == "AUDIO"]
            self.assertEqual(len(audio), 2)
            self.assertIn("140", [k for t in audio for k in t["keyframesByTick"]])
            with self.assertRaises(ValueError):                                 # nothing at all to make
                vt.make_items_in_world(match, [], None, fb)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_song_in_made_video(self):
        from mcsm import videotext as vt
        ff = vt.find_tool("ffmpeg")
        if not ff:
            self.skipTest("ffmpeg not installed")
        tmp = tempfile.mkdtemp()
        try:
            src, songf = os.path.join(tmp, "clip.mp4"), os.path.join(tmp, "song.wav")
            subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=3:r=10", "-pix_fmt",
                            "yuv420p", src], check=True)                       # no sound of its own
            subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=10", songf], check=True)
            out = vt.make_items_on_screen(src, [], 3.0, music={"file": songf, "start": 1.0, "offset": 2.0,
                                                                "volume": 0.8})
            probe = vt.find_tool("ffprobe")
            info = subprocess.run([probe, "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of",
                                   "json", out], capture_output=True, text=True).stdout
            data = json.loads(info)
            self.assertIn("audio", [st["codec_type"] for st in data["streams"]])
            self.assertLess(float(data["format"]["duration"]), 3.6)            # the song doesn't make it longer
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_editor_keeps_songs(self):
        import base64
        from mcsm import videoeditor as ve
        tmp = tempfile.mkdtemp()
        old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = tmp
        try:
            ed = ve.VideoEditor(os.path.join(tmp, "v.mp4"), info={"video": os.path.join(tmp, "v.mp4"), "duration": 5,
                                                                    "width": 320, "height": 180, "matches": []})
            r = ed.add_music({"name": "Song.MP3", "data": base64.b64encode(b"ID3 fake").decode()})
            self.assertTrue(r["file"].endswith(".mp3"))
            self.assertEqual(ed.file_for("/music/" + r["file"]), os.path.join(ed.music_dir(), r["file"]))
            self.assertIsNone(ed.file_for("/music/../x.mp3"))
            self.assertEqual(ed.music_for({"file": r["file"]})["file"], os.path.join(ed.music_dir(), r["file"]))
            self.assertIsNone(ed.music_for(None))
            with self.assertRaises(ValueError):
                ed.music_for({"file": "gone.mp3"})
            with self.assertRaises(ValueError):
                ed.add_music({"name": "x.exe", "data": "AAAA"})
            ed.save_project({"items": [], "match": 0, "music": {"file": r["file"], "start": 2}})
            self.assertEqual(ed.load_project()["music"]["start"], 2)
        finally:
            if old is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old
            shutil.rmtree(tmp, ignore_errors=True)


class TestEditorFromEdit(unittest.TestCase):
    """The live editor opened on a Flashback edit that hasn't been exported (no video)."""

    def test_edit_mode(self):
        from mcsm import videotext as vt, videoeditor as ve
        tmp = tempfile.mkdtemp()
        old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = tmp
        try:
            fb, match = TestVideoMusic()._setup(tmp)
            info = vt.analyse_edit(match["state"])
            self.assertIsNone(info["video"])
            self.assertEqual(info["edit"], match["state"])
            self.assertAlmostEqual(info["duration"], 10.0, places=1)          # ticks 100-300 = 10 s
            m = info["matches"][0]
            self.assertEqual((m["start"], m["end"], m["match"], m["uuid"]), (100, 300, "edit", "orig"))
            with self.assertRaises(ValueError):
                vt.analyse_edit(match["state"], scene=3)
            info["matches"][0]["recording"] = match["recording"]
            ed = ve.VideoEditor(None, info=info)
            self.assertEqual(ed.copy_key(), match["state"] + "#scene0")
            self.assertIsNone(ed.file_for("/video"))
            self.assertIsNone(ed.file_for("/tile/0/0/2"))                      # no library backup: no terrain
            self.assertEqual(ed.tiles()["tiles"], [])
            self.assertEqual(ed.page_info()["edit"], match["state"])
            self.assertIn("players", ed.tracks_all(0))
            # one Flashback copy per edit, found again by its key
            items = [{"kind": "world", "lines": [["Hi", 2]], "start": 1.0}]
            r1 = ed._make({"target": "world", "items": items, "match": 0}, lambda _m: None)
            r2 = ed._make({"target": "world", "items": items, "match": 0}, lambda _m: None)
            self.assertTrue(r2["replaced"])
            self.assertEqual(r1["replay"], r2["replay"])
            with self.assertRaises(ValueError):                                  # nothing exported to draw on
                ed._make({"target": "video", "items": items, "match": 0}, lambda _m: None)
            with self.assertRaises(ValueError):
                ve.VideoEditor(None)
        finally:
            if old is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old
            shutil.rmtree(tmp, ignore_errors=True)


class TestCameraKeyframes(unittest.TestCase):
    """Camera keyframes: Flashback's interpolation, and changing them in the editor (edit mode)."""

    @staticmethod
    def _scene(kind_a="SMOOTH", kind_b="SMOOTH", mid=True):
        def key(x, yaw, kind):
            return {"position": [x, 64.0, 0.0], "yaw": yaw, "pitch": 0.0, "roll": 0.0, "type": "camera",
                    "interpolation_type": kind}
        keys = {"0": key(0.0, 170.0, kind_a), "100": key(10.0, -170.0, kind_b)}
        if mid:
            keys["200"] = key(30.0, -150.0, "SMOOTH")
        return {"keyframeTracks": [{"keyframeType": "CAMERA", "enabled": True, "keyframesByTick": keys}]}

    def test_interpolation(self):
        from mcsm import worldtext as wt
        # LINEAR: halfway in position; yaw takes the short way across 180 (170 -> -170 = +20)
        pos, yaw, _ = wt.camera_at(self._scene("LINEAR", "LINEAR", mid=False), 50)
        self.assertAlmostEqual(pos[0], 5.0)
        self.assertAlmostEqual(wt._wrap(yaw), 180.0 if wt._wrap(yaw) > 0 else -180.0, places=4)
        # HOLD: stays at the first keyframe until the next one
        pos, yaw, _ = wt.camera_at(self._scene("HOLD", "SMOOTH"), 99)
        self.assertEqual(pos[0], 0.0)
        # EASE_IN on the first, EASE_OUT on the second keyframe's left side = ease both ways
        pos, _, _ = wt.camera_at(self._scene("EASE_IN_OUT", "EASE_IN_OUT", mid=False), 25)
        self.assertAlmostEqual(pos[0], 10 * 4 * 0.25 ** 3)
        # SMOOTH: passes through the keyframes and bends towards the next one (spline, not straight)
        sc = self._scene()
        self.assertAlmostEqual(wt.camera_at(sc, 100)[0][0], 10.0)
        smooth = wt.camera_at(sc, 50)[0][0]
        self.assertNotAlmostEqual(smooth, 5.0, places=2)
        self.assertTrue(0.0 < smooth < 10.0)
        prev = None                                   # no jumps along a smooth path
        for t in range(0, 201):
            p = wt.camera_at(sc, t)[0][0]
            if prev is not None:
                self.assertLess(abs(p - prev), 1.0)
            prev = p

    def test_editing_the_camera_goes_into_the_copy_only(self):
        from mcsm import videotext as vt, videoeditor as ve, worldtext as wt
        tmp = tempfile.mkdtemp()
        old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = tmp
        try:
            fb, match = TestVideoMusic()._setup(tmp)
            with open(match["state"], encoding="utf-8") as fh:
                original = fh.read()
            info = vt.analyse_edit(match["state"])
            info["matches"][0]["recording"] = match["recording"]
            ed = ve.VideoEditor(None, info=info)
            self.assertEqual(len(ed.page_info()["keyframes"]), 2)
            self.assertEqual(ed.page_info()["clock"]["start"], 100)
            keys = ed.keyframes(0) + [{"tick": 200, "position": [5, 70, 5], "yaw": 90, "pitch": 10,
                                       "interpolation_type": "LINEAR"}, {"tick": "bad"}]
            r = ed.api_post("/api/camera", {"match": 0, "keyframes": keys})
            self.assertEqual([k["tick"] for k in r["keyframes"]], [100, 200, 300])   # cleaned, sorted
            self.assertEqual(wt.camera_at(vt.scene_for_match(ed.vinfo["matches"][0]), 200)[0], [5.0, 70.0, 5.0])
            ed.save_project({"items": [], "match": 0, "camera": r["keyframes"]})
            # a new editor on the same edit picks the changed camera up again
            info2 = vt.analyse_edit(match["state"])
            info2["matches"][0]["recording"] = match["recording"]
            self.assertEqual(len(ve.VideoEditor(None, info=info2).keyframes(0)), 3)
            out = ed._make({"target": "world", "items": [{"kind": "world", "lines": [["Hi", 2]], "start": 1.0}],
                            "match": 0, "camera": r["keyframes"]}, lambda _m: None)
            copy_edit = os.path.join(fb, "editor_states", vt._replay_meta(out["replay"])["uuid"] + ".json")
            with open(copy_edit, encoding="utf-8") as fh:
                cam = [t for t in json.load(fh)["scenes"][0]["keyframeTracks"] if t["keyframeType"] == "CAMERA"][0]
            self.assertEqual(sorted(cam["keyframesByTick"]), ["100", "200", "300"])
            self.assertEqual(cam["keyframesByTick"]["200"]["type"], "camera")
            with open(match["state"], encoding="utf-8") as fh:
                self.assertEqual(fh.read(), original)                 # your own edit is never changed
            ed.api_post("/api/camera", {"match": 0, "keyframes": None})
            self.assertEqual(len(ed.keyframes(0)), 2)                 # back to the edit's camera
        finally:
            if old is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old
            shutil.rmtree(tmp, ignore_errors=True)


class TestFlashbackLink(unittest.TestCase):
    """The app's side of the companion mod, against a stand-in that speaks the mod's protocol."""

    def test_link(self):
        import http.server
        import threading
        from mcsm import flashlink, videotext as vt, videoeditor as ve
        seen = []

        class Fake(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.headers.get("X-MCSM-Token") != "secret":
                    return self._reply(403, b"forbidden", "text/plain")
                if self.path == "/status":
                    return self._reply(200, json.dumps({"mod": "0.1.0", "minecraft": "26.1.2", "flashback": "0.40.0",
                                                        "replay": {"uuid": "orig", "name": "Rec", "tick": 5,
                                                                   "total": 300, "paused": True}}).encode())
                if self.path.startswith("/frame"):
                    return self._reply(200, b"\x89PNG fake " + self.path.encode(), "image/png")
                self._reply(404, b"no")

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(body)
                self._reply(200, b'{"ok": true}')

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        tmp = tempfile.mkdtemp()
        old_inst, old_local = flashlink.instances, os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = tmp
        try:
            mc = os.path.join(tmp, "inst", "minecraft")
            inst = {"name": "Test", "mc": "26.1.2", "minecraft": mc, "mods": os.path.join(mc, "mods"),
                    "link_file": os.path.join(mc, "config", "mcsm-link.json")}
            flashlink.instances = lambda: [inst]
            link, why = flashlink.find_link()
            self.assertIsNone(link)
            self.assertIn("isn't installed", why)                       # no link file yet
            os.makedirs(os.path.dirname(inst["link_file"]))
            with open(inst["link_file"], "w") as fh:
                json.dump({"port": srv.server_address[1], "token": "secret"}, fh)
            link, why = flashlink.find_link()
            self.assertEqual(link.status()["replay"]["uuid"], "orig")
            self.assertEqual(link.view(120, [1, 2, 3], 90, 10, 70)["ok"], True)
            self.assertEqual(seen[-1], {"tick": 120, "x": 1, "y": 2, "z": 3, "yaw": 90, "pitch": 10, "fov": 70})
            self.assertIn(b"width=640", link.frame(640))
            self.assertEqual(flashlink.Link(srv.server_address[1], "wrong").status().get("error"), "forbidden")
            # the editor: is the right replay open, put Flashback there, the picture
            fb, match = TestVideoMusic()._setup(tmp)
            info = vt.analyse_edit(match["state"])
            ed = ve.VideoEditor(None, info=info)
            st = ed.api_get("/api/link")
            self.assertTrue(st["connected"] and st["matches"])
            self.assertEqual(ed.api_post("/api/link/view", {"tick": 150, "x": 0, "y": 64, "z": 0, "yaw": 0,
                                                            "pitch": 0, "fov": 70})["ok"], True)
            frame = ed.file_for("/link/frame/800/7.png")
            with open(frame, "rb") as fh:
                self.assertTrue(fh.read().startswith(b"\x89PNG"))
            info["matches"][0]["uuid"] = "another"
            self.assertFalse(ed.api_get("/api/link")["matches"])          # a different replay is open
            # installing: only the mod's own older jars are replaced
            jar = os.path.join(tmp, "mcsm-link-0.2.0.jar")
            with open(jar, "wb") as fh:
                fh.write(b"jar")
            os.makedirs(inst["mods"])
            for name in ("mcsm-link-0.1.0.jar", "flashback.jar"):
                with open(os.path.join(inst["mods"], name), "wb") as fh:
                    fh.write(b"x")
            flashlink.install_mod(inst["mods"], jar)
            self.assertEqual(sorted(os.listdir(inst["mods"])), ["flashback.jar", "mcsm-link-0.2.0.jar"])
        finally:
            flashlink.instances = old_inst
            srv.shutdown()
            if old_local is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old_local
            shutil.rmtree(tmp, ignore_errors=True)


class TestRealBlocks(unittest.TestCase):
    """Real blocks near the camera with textures from the client jar, and the eye height."""

    @staticmethod
    def _jar(path):
        cube_faces = {f: {"texture": "#" + f} for f in ("down", "up", "north", "south", "west", "east")}
        files = {
            "blockstates/stone.json": {"variants": {"": {"model": "minecraft:block/stone"}}},
            "blockstates/short_grass.json": {"variants": {"": {"model": "minecraft:block/short_grass"}}},
            "blockstates/oak_slab.json": {"variants": {"type=bottom": {"model": "minecraft:block/oak_slab"}}},
            "models/block/cube.json": {"elements": [{"from": [0, 0, 0], "to": [16, 16, 16], "faces": cube_faces}]},
            "models/block/cube_all.json": {"parent": "block/cube", "textures": {
                **{f: "#all" for f in ("down", "up", "north", "south", "west", "east")}, "particle": "#all"}},
            "models/block/stone.json": {"parent": "block/cube_all", "textures": {"all": {"sprite": "block/stone"}}},
            "models/block/tinted_cross.json": {"parent": "block/cross"},
            "models/block/cross.json": {"textures": {"particle": "#cross"}},
            "models/block/short_grass.json": {"parent": "minecraft:block/tinted_cross",
                                              "textures": {"cross": "minecraft:block/short_grass"}},
            "models/block/oak_slab.json": {"textures": {"all": "block/oak_planks"}, "elements": [
                {"from": [0, 0, 0], "to": [16, 8, 16], "faces": {f: {"texture": "#all"} for f in
                                                                  ("down", "up", "north", "south", "west", "east")}}]},
        }
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in files.items():
                zf.writestr("assets/minecraft/" + name, json.dumps(data))
            zf.writestr("assets/minecraft/textures/block/stone.png", b"\x89PNG stone")

    def test_blocks(self):
        from mcsm import blockmesh as bm, terrain
        tmp = tempfile.mkdtemp()
        try:
            jar = os.path.join(tmp, "client.jar")
            self._jar(jar)
            world = os.path.join(tmp, "srv", "world")
            # a step: 64 high in the west half, 70 in the east half; one tuft of grass, one slab
            h = lambda x, z: 64 if x < 8 else 70                      # noqa: E731
            b = lambda x, y, z: ("minecraft:short_grass" if (x, z) == (2, 2) else
                                 "minecraft:oak_slab" if (x, z) == (3, 3) else "minecraft:stone")   # noqa: E731
            terrain.write_region(os.path.join(world, "region", "r.0.0.mca"), {(0, 0): terrain.make_chunk(0, 0, h, b)})
            look = bm.Look(jar)
            r = bm.build(os.path.join(tmp, "srv"), world, [(8.0, 8.0)], look, radius=40)
            faces = [struct.unpack_from("<5h", r["faces"], i) for i in range(0, len(r["faces"]), 10)]
            names = {i["name"]: i for i in r["infos"]}
            self.assertEqual(names["stone"]["kind"], "cube")
            self.assertTrue(names["stone"]["tex"] is not None and "block/stone" in r["textures"])   # {"sprite": ...}
            self.assertEqual(names["short_grass"]["kind"], "cross")
            self.assertEqual((names["oak_slab"]["kind"], names["oak_slab"]["box"]), ("part", [0, 0, 0, 16, 8, 16]))
            ox, oz = r["origin"]
            at = {(x + ox, y, z + oz, d) for x, y, z, d, _i in faces}
            self.assertIn((10, 70, 5, 0), at)                          # top of the high part
            self.assertIn((8, 70, 5, 4), at)                           # the step's wall, facing west
            self.assertNotIn((10, 70, 5, 4), at)                       # between two blocks: hidden
            self.assertIn((2, 64, 2, 6), at)                           # the grass tuft, crossed quads
            self.assertEqual(look.texture_png("block/stone"), b"\x89PNG stone")
            self.assertIsNone(look.texture_png("block/nothing"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_eye_height(self):
        from mcsm import worldtext as wt
        sc = {"keyframeTracks": [{"keyframeType": "CAMERA", "enabled": True, "keyframesByTick": {
            "0": {"position": [1.0, 64.0, 2.0], "yaw": 10, "pitch": 5, "type": "camera"}}}]}
        self.assertEqual(wt.camera_at(sc, 0)[0], [1.0, 64.0, 2.0])     # the keyframe as Flashback stores it
        self.assertEqual(wt.eye_at(sc, 0), ([1.0, 64.0 + wt.EYE_HEIGHT, 2.0], 10.0, 5.0))   # what you see from
