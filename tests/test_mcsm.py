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
        self.addCleanup(setattr, gui.appwindow, "open_page", gui.appwindow.open_page)
        gui.appwindow.open_page = lambda url: "test"
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
        self.addCleanup(setattr, gui.appwindow, "open_page", gui.appwindow.open_page)
        gui.appwindow.open_page = lambda url: "test"
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


class TestWorldClock(unittest.TestCase):
    """Recordings lined up on the world's game time (the set-time packet), pauses found, real time per stretch."""

    @staticmethod
    def _rec(path, start_gt, ticks, pause_at=None, pause=0):
        import struct
        recs = [("create_local_player", struct.pack(">QQ", 1, 2) + struct.pack(">ddd", 0.0, 64.0, 0.0) + b"\0" * 40)]
        gt = start_gt
        for t in range(ticks):
            if t == pause_at:
                gt += pause                               # the recording was paused: the world went on
            recs.append(("move_entities", _fb_move([(7, t / 20, 64.0, 0.0)])))
            if t % 20 == 1:                               # set time (26.3: id 115): game time, day time, ticking
                recs.append(("game_packet", _vi(115) + struct.pack(">qq?", gt, gt % 24000, True)))
            recs.append(("next_tick", b""))
            gt += 1
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("metadata.json", json.dumps({"protocol_version": 777}))
            zf.writestr("c0.flashback", _fb_chunk(recs))

    def test_clock(self):
        from mcsm import paths
        tmp = tempfile.mkdtemp()
        try:
            a, b = os.path.join(tmp, "a.zip"), os.path.join(tmp, "b.zip")
            self._rec(a, 50_000, 1000, pause_at=500, pause=200)
            self._rec(b, 50_300, 600)
            ca, cb = [], []
            list(paths.decode_entities(a, clock=ca))
            list(paths.decode_entities(b, clock=cb))
            self.assertEqual(len(ca), 3)                                   # start, the pause, the last reading
            self.assertEqual(paths.game_time(ca, 100), 50_100)
            self.assertEqual(paths.game_time(ca, 700), 50_900)             # 200 ticks of pause before
            self.assertEqual(paths.game_time(cb, 0), 50_300)
            # real time: both end on the same clock; a third recording after a server restart (its save says so)
            metas = {"a": {"clock": ca, "ticks": 999, "save_ms": 1_000_000 + 51_199 * 50},
                     "b": {"clock": cb, "ticks": 599, "save_ms": 1_000_000 + 50_899 * 50 + 900},   # saved a bit late
                     "c": {"clock": [[0, 51_300]], "ticks": 100, "save_ms": 9_000_000 + 51_400 * 50}}
            off = paths.world_offsets(metas)
            self.assertEqual(off["a"], off["b"])                           # one stretch of play: one offset
            self.assertNotEqual(off["a"], off["c"])                        # after a restart: its own
            # the same moment in both recordings gets the same time, to the tick
            ta = off["a"] + paths.game_time(ca, 700) * 50
            tb = off["b"] + paths.game_time(cb, 600) * 50                  # game time 50,900 in both
            self.assertEqual(ta, tb)
            # every recorded movement (for exact playback), kept once
            ex = paths.exact_rows(b, os.path.join(tmp, "cache"))
            self.assertEqual(ex["who"], [None])
            self.assertGreater(len(ex["rows"]), 500)                       # every tick (it moves each tick)
            self.assertEqual(ex["rows"][10][:2], [11, 0])
            self.assertEqual(ex["rows"][10][6:9], [90.0, 0.0, 90.0])       # yaw, pitch, head yaw
            self.assertEqual(paths.exact_rows(b, os.path.join(tmp, "cache")), ex)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestShot(unittest.TestCase):
    """A shot planned in the viewer, written as camera keyframes into a Flashback copy of the best recording."""

    def test_shot(self):
        from datetime import timedelta
        from mcsm import shot, paths
        tmp = tempfile.mkdtemp()
        try:
            a = os.path.join(tmp, "2026-05-24--18-00-00.zip")
            TestWorldClock._rec(a, 50_000, 1000, pause_at=500, pause=200)
            with zipfile.ZipFile(a, "a") as zf:
                zf.writestr("arcade_replay_meta.json", "{}")
            with zipfile.ZipFile(a) as zf:
                body = {n: zf.read(n) for n in zf.namelist()}
            body["metadata.json"] = json.dumps({"protocol_version": 777, "uuid": "11111111-2222-3333-4444-555555555555",
                                                "name": "Unnamed", "total_ticks": 1000, "chunks": {}}).encode()
            with zipfile.ZipFile(a, "w") as zf:
                for n, b in body.items():
                    zf.writestr(n, b)
            clock = []
            list(paths.decode_entities(a, clock=clock))
            meta = {"clock": clock, "ticks": 999}
            off = 1_000_000
            t_of = lambda tick: off + paths.game_time(clock, tick) * 50                   # noqa: E731
            self.assertEqual(shot.tick_at(meta, off, 0, t_of(100)), 100)
            self.assertEqual(shot.tick_at(meta, off, 0, t_of(700)), 700)                    # after the pause
            self.assertEqual(shot.tick_at(None, None, 1000, 1000 + 2000), 40)                # no clock: from the start
            start = datetime(2026, 5, 24, 18, tzinfo=timezone.utc)
            rep = m.Replay(a, start, 50_000, format="flashback", recorded_player="Steve", mc_version="26.3")
            rec = {"replay": rep, "meta": meta, "offset": off, "start_ms": 0, "duration_ms": 50_000, "player": "Steve"}
            other = {**rec, "player": "Alex"}
            keys = [{"t": t_of(100), "eye": [10.0, 70.0, 5.0], "yaw": 90.0, "pitch": 10.0},
                    {"t": t_of(300), "eye": [20.0, 72.0, 5.0], "yaw": 45.0, "pitch": 0.0}]
            near = lambda p, t: [11.0, 69.0, 5.0] if p == "Steve" else [500.0, 64.0, 500.0]   # noqa: E731
            c = shot.candidates([other, rec], keys, near)
            self.assertEqual([x["player"] for x in c], ["Steve", "Alex"])                   # nearest first
            far = [{**keys[0], "t": t_of(0) - 600_000}] + keys[1:]                           # starts before the recording
            self.assertEqual(shot.candidates([rec], far, near), [])
            fb = os.path.join(tmp, "flashback")
            os.makedirs(os.path.join(fb, "replays"))
            res = shot.send(c[0], keys, "Thunderstorm", lambda r: r.path, fb, tmp)
            self.assertEqual((res["ticks"], res["keyframes"], res["installed"]), ([100, 300], 2, True))
            with open(res["editor_state"], encoding="utf-8") as fh:
                st = json.load(fh)
            sc = st["scenes"][0]
            cam = next(t for t in sc["keyframeTracks"] if t["keyframeType"] == "CAMERA")["keyframesByTick"]
            self.assertEqual(sorted(cam), ["100", "300"])
            self.assertEqual(cam["100"]["position"], [10.0, 70.0 - shot.EYE_HEIGHT, 5.0])    # Flashback adds the eye
            self.assertEqual((cam["100"]["yaw"], cam["100"]["interpolation_type"]), (90.0, "SMOOTH"))
            self.assertEqual((sc["exportStartTicks"], sc["exportEndTicks"]), (100, 300))
            with zipfile.ZipFile(res["file"]) as zf:
                copy = json.loads(zf.read("metadata.json"))
            self.assertNotEqual(copy["uuid"], "11111111-2222-3333-4444-555555555555")       # a copy, original untouched
            self.assertIn("(shot)", copy["name"])
            self.assertTrue(any("Shot: Thunderstorm" in mk.get("description", "") for mk in copy["markers"].values()))
            self.assertTrue(os.path.isfile(a))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestPosesAndMobs(unittest.TestCase):
    """Per-version packet ids from the game's reports; sneaking/swinging players and mobs from a recording."""

    def test_protocol_tables(self):
        from mcsm import protocol
        t = protocol.table(777)
        self.assertEqual(t["version"], "26.3")
        self.assertEqual((t["packets"]["add_entity"], t["packets"]["set_entity_data"], t["packets"]["remove_entities"],
                          t["packets"]["set_time"]), (1, 101, 78, 115))
        self.assertEqual(t["entity_types"][159], "player")
        self.assertEqual(protocol.table(773)["packets"]["set_time"], 111)                 # 1.21.10
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmp, "reports"))
            with open(os.path.join(tmp, "reports", "packets.json"), "w") as fh:
                json.dump({"play": {"clientbound": {"minecraft:add_entity": {"protocol_id": 1},
                                                    "minecraft:animate": {"protocol_id": 2}}}}, fh)
            with open(os.path.join(tmp, "reports", "registries.json"), "w") as fh:
                json.dump({"minecraft:entity_type": {"entries": {"minecraft:cow": {"protocol_id": 1},
                                                                 "minecraft:bat": {"protocol_id": 0}}}}, fh)
            r = protocol.from_reports(tmp, "9.9", 999)
            self.assertEqual((r["packets"], r["entity_types"]), ({"add_entity": 1, "animate": 2}, ["bat", "cow"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_flags_swings_mobs(self):
        import struct
        from mcsm import paths, protocol
        zombie = protocol.table(777)["entity_types"].index("zombie")
        recs = [("create_local_player", struct.pack(">QQ", 1, 2) + struct.pack(">ddd", 0.0, 64.0, 0.0) + b"\0" * 40)]
        for t in range(60):
            ents = [(7, t / 20, 64.0, 0.0)] + ([(20, 5.0 + t / 20, 64.0, 5.0)] if t > 5 else [])
            recs.append(("move_entities", _fb_move(ents)))
            if t == 5:                                    # a zombie comes into view
                recs.append(("game_packet", _fb_add_player(20, "00000000-0000-4000-8000-000000000020", 5.0, 64.0, 5.0, zombie)))
                recs.append(("game_packet", _fb_add_player(21, "00000000-0000-4000-8000-000000000021", 0, 0, 0,
                                                           protocol.table(777)["entity_types"].index("item"))))
            if t == 10:                                   # the player starts sneaking (flags 0x02) and swings
                recs.append(("game_packet", _vi(101) + _vi(7) + bytes([0]) + _vi(0) + bytes([2]) + b"\xff"))
                recs.append(("game_packet", _vi(2) + _vi(7) + bytes([0])))
            if t == 12:                                   # rides the zombie (set_passengers 109: vehicle, count, riders)
                recs.append(("game_packet", _vi(109) + _vi(20) + _vi(1) + _vi(7)))
            if t == 20:                                   # gets off
                recs.append(("game_packet", _vi(109) + _vi(20) + _vi(0)))
            if t == 7:                                    # the dropped item is wheat (entity data index 8, an item stack)
                wheat = protocol.table(777)["items"].index("wheat")
                recs.append(("game_packet", _vi(101) + _vi(21) + bytes([0]) + _vi(0) + bytes([0])
                             + bytes([2]) + _vi(6) + bytes([0]) + bytes([8]) + _vi(7) + _vi(3) + _vi(wheat) + _vi(0) + _vi(0)
                             + bytes([0xFF])))
            if t == 30:                                   # picked up
                recs.append(("game_packet", _vi(78) + _vi(1) + _vi(21)))
            recs.append(("next_tick", b""))
        tmp = tempfile.mkdtemp()
        try:
            z = os.path.join(tmp, "r.zip")
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("metadata.json", json.dumps({"protocol_version": 777, "version_string": "26.3"}))
                zf.writestr("c0.flashback", _fb_chunk(recs))
            ex = paths.exact_rows(z, os.path.join(tmp, "cache"))
            self.assertIn([10, None, "f", 2], ex["events"])          # sneaking, from tick 10
            self.assertIn([10, None, "s", 0], ex["events"])
            self.assertIn([12, None, "r", "zombie"], ex["events"])  # riding, and what
            self.assertIn([20, None, "r", ""], ex["events"])        # off again          # a main-hand swing
            self.assertEqual(ex["mobs"], {"#zombie#20": "zombie", "#item#21": "item"})   # dropped items too
            self.assertIn([7, "#item#21", "it", "wheat"], ex["events"])
            self.assertIn([30, "#item#21", "gone", 1], ex["events"])
            self.assertIn("#zombie#20", ex["who"])
            zrows = [r for r in ex["rows"] if ex["who"][r[1]] == "#zombie#20"]
            self.assertTrue(zrows and all(r[0] % paths.MOB_EVERY == 0 for r in zrows))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_entity_data_time_weather(self):
        """The entity-data walker (flags, pose, hand use, health), the two layouts of the time of day, weather,
        hurt and death events."""
        import struct
        from mcsm import paths
        # index 0 byte flags (sneaking), 1 varint air, 6 pose (serializer learned: 20 here) crouching, 8 hand use,
        # 9 health 0, then something unknown (stops there)
        body = (bytes([0]) + _vi(0) + bytes([2]) + bytes([1]) + _vi(1) + _vi(300) + bytes([6]) + _vi(20) + _vi(5)
                + bytes([8]) + _vi(0) + bytes([1]) + bytes([9]) + _vi(3) + struct.pack(">f", 0.0)
                + bytes([10]) + _vi(99) + b"junk" + b"\xff")
        self.assertEqual(paths.entity_data(body, 0, len(body)), {0: 2, 6: 5, 8: 1, 9: 0.0})
        old = struct.pack(">qq?", 1000, 18_000, True)                          # 1.21.x: game time, day time, ticking
        self.assertEqual(paths._day_time(old, 0, len(old)), (18_000, 1.0))
        new = struct.pack(">q", 1000) + _vi(2) + _vi(1) + _vi(5) + struct.pack(">ff", 0, 1) + _vi(0) + _vi(99_384_000) \
            + struct.pack(">ff", 0, 1)                                         # 26.x: world clocks, the overworld's is 0
        self.assertEqual(paths._day_time(new, 0, len(new)), (99_384_000, 1.0))
        self.assertIsNone(paths._day_time(struct.pack(">q", 1000) + _vi(0), 0, 9))   # no clocks in this one
        recs = [("create_local_player", struct.pack(">QQ", 1, 2) + struct.pack(">ddd", 0.0, 64.0, 0.0) + b"\0" * 40)]
        for t in range(30):
            recs.append(("move_entities", _fb_move([(7, t / 20, 64.0, 0.0)])))
            if t == 2:
                recs.append(("game_packet", _vi(115) + new))                    # set time (26.3)
                recs.append(("game_packet", _vi(39) + bytes([7]) + struct.pack(">f", 0.8)))   # rain level 0.8
            if t == 5:
                recs.append(("game_packet", _vi(25) + _vi(7) + _vi(1) + _vi(0) + _vi(0) + b"\0"))   # damage event
                recs.append(("game_packet", _vi(101) + _vi(7) + bytes([6]) + _vi(20) + _vi(7) + b"\xff"))   # pose: dying
                recs.append(("game_packet", _vi(34) + struct.pack(">i", 7) + bytes([3])))   # entity event 3: died
            recs.append(("next_tick", b""))
        tmp = tempfile.mkdtemp()
        try:
            z = os.path.join(tmp, "r.zip")
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("metadata.json", json.dumps({"protocol_version": 777, "version_string": "26.3"}))
                zf.writestr("c0.flashback", _fb_chunk(recs))
            ex = paths.exact_rows(z, os.path.join(tmp, "cache"))
            self.assertIn([2, "time", 99_384_000, 1.0], ex["world"])
            self.assertIn([2, "rain", 0.8, 0], ex["world"])
            self.assertIn([5, None, "hurt", 1], ex["events"])
            self.assertIn([5, None, "p", 7], ex["events"])
            self.assertIn([5, None, "d", 1], ex["events"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_equipment(self):
        """Held items and armour from set_equipment: slots, item names, the item_model and dye components,
        and a component it can't read keeps what came before."""
        import struct
        from mcsm import paths, protocol
        t = protocol.table(777)
        items, comps = t["items"], t["components"]
        it, ct = items.index, comps.index

        def stack(name, *components):
            return _vi(1) + _vi(it(name)) + _vi(len(components)) + _vi(0) + b"".join(components)

        model = _vi(ct("item_model")) + _vi(len(b"hsj:aquatic_eye")) + b"hsj:aquatic_eye"
        dye = _vi(ct("dyed_color")) + struct.pack(">i", 0x3366FF)
        ench = _vi(ct("enchantments")) + _vi(2) + _vi(3) + _vi(1) + _vi(9) + _vi(5)    # two enchantments
        body = (bytes([0x80]) + stack("diamond_pickaxe", ench, model)              # main hand, more follow
                + bytes([0x80 | 4]) + stack("leather_chestplate", dye)            # chest
                + bytes([0x80 | 1]) + _vi(0)                                       # off hand: empty
                + bytes([5]) + stack("iron_helmet"))                               # head, the last
        self.assertEqual(paths.equipment(body, 0, len(body), items, comps),
                         [["mainhand", "diamond_pickaxe", "hsj:aquatic_eye", None, {"glint": True}],   # enchanted
                          ["chest", "leather_chestplate", "", 0x3366FF],
                          ["offhand", "", "", None], ["head", "iron_helmet", "", None]])
        odd = bytes([0x80]) + stack("bow", _vi(ct("potion_contents")) + b"\x01\x02") + bytes([5]) + stack("iron_helmet")
        self.assertEqual(paths.equipment(odd, 0, len(odd), items, comps), [["mainhand", "bow", "", None]])
        # in a recording: the player's own equipment and another player's
        eq = t["packets"]["set_equipment"]
        recs = [("create_local_player", struct.pack(">QQ", 1, 2) + struct.pack(">ddd", 0.0, 64.0, 0.0) + b"\0" * 40)]
        for tick in range(10):
            recs.append(("move_entities", _fb_move([(7, 0.0, 64.0, 0.0), (9, 3.0, 64.0, 0.0)])))
            if tick == 1:
                recs.append(("game_packet", _fb_add_player(9, ALEX, 3.0, 64.0, 0.0)))
            if tick == 3:
                recs.append(("game_packet", _vi(eq) + _vi(7) + bytes([0]) + stack("diamond_axe")))
                recs.append(("game_packet", _vi(eq) + _vi(9) + bytes([2]) + stack("golden_boots")))
            recs.append(("next_tick", b""))
        tmp = tempfile.mkdtemp()
        try:
            z = os.path.join(tmp, "r.zip")
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("metadata.json", json.dumps({"protocol_version": 777, "version_string": "26.3"}))
                zf.writestr("c0.flashback", _fb_chunk(recs))
            ex = paths.exact_rows(z, os.path.join(tmp, "cache"), {bytes.fromhex(ALEX.replace("-", "")): "Alex"})
            self.assertIn([3, None, "eq", ["mainhand", "diamond_axe", "", None]], ex["events"])
            self.assertIn([3, "Alex", "eq", ["feet", "golden_boots", "", None]], ex["events"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_watch_in_flashback(self):
        """One button: the recording covering a moment (the followed player's), copied around it into Flashback with a
        marker at that time and a scene starting there; Minecraft is only started for a launcher's own Flashback."""
        import types
        from datetime import datetime, timedelta, timezone
        from mcsm import history
        tmp = tempfile.mkdtemp()
        try:
            start = datetime(2026, 5, 24, 18, 0, tzinfo=timezone.utc)

            def rec(name, player):
                p = os.path.join(tmp, name)
                with zipfile.ZipFile(p, "w") as z:
                    z.writestr("metadata.json", json.dumps({"uuid": name, "name": name, "total_ticks": 24000,
                                                            "chunks": {f"c{i}.flashback": {"duration": 6000} for i in range(4)}}))
                    for i in range(4):
                        z.writestr(f"c{i}.flashback", b"x" * 10)
                return types.SimpleNamespace(format="flashback", unfinished=False, start=start, end=start + timedelta(minutes=20),
                                             raw_meta={"total_ticks": 24000}, duration_ms=1_200_000, recorded_player=player,
                                             mc_version="26.1.2", path=p)
            reps = [rec("a.zip", "Alex"), rec("b.zip", "Steve")]
            fb = os.path.join(tmp, "flashback")
            fake = types.SimpleNamespace(data=types.SimpleNamespace(replays=reps), replay_file=lambda r: r.path,
                                         flashback_pick=lambda r: fb, flashback_dir=None,
                                         highlights=types.SimpleNamespace(path=os.path.join(tmp, "h.json")),
                                         what_happened=lambda t, w: "Steve was shot by Skeleton")
            t = int((start + timedelta(minutes=7)).timestamp() * 1000)     # tick 8400: in part 1
            job = {"done": 0, "total": 0}
            r = history.HistoryMap.watch_in_flashback(fake, t, "Steve", job)
            self.assertEqual((r["recorded_by"], r["installed"], r["started"]), ("Steve", True, None))
            with zipfile.ZipFile(r["file"]) as z:
                meta = json.loads(z.read("metadata.json"))
            self.assertEqual(sorted(meta["chunks"]), ["c0.flashback"])   # only part 1 (6 to 9 minutes in), renumbered
            self.assertEqual(list(meta["markers"]), [str(8400 - 6000)])              # the marker at that moment
            self.assertTrue(meta["name"].startswith("Steve at ") and meta["name"].endswith(": Steve was shot by Skeleton"))
            self.assertTrue(os.path.isfile(os.path.join(fb, "editor_states", f"{meta['uuid']}.json")))
            self.assertEqual(job["done"], job["total"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_trimmed_replay_copy(self):
        """A highlight's copy keeps only the 5-minute parts it needs, renumbered from c0, with its markers moved to
        match, its own name, and the bytes it writes counted."""
        from mcsm import flashback as fbm
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "src.zip")
            meta = {"uuid": "u1", "name": "orig", "total_ticks": 24000, "markers": {"6500": {"colour": 1, "description": "old"}},
                    "chunks": {f"c{i}.flashback": {"duration": 6000} for i in range(4)}}
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("metadata.json", json.dumps(meta))
                for i in range(4):
                    z.writestr(f"c{i}.flashback", f"part {i}" * 100)
                z.writestr("level_chunk_caches/0", b"cache")
            dst = os.path.join(tmp, "out", "copy.zip")
            got = []
            m = fbm.write_replay_copy(src, dst, [fbm.FlashbackMarker(7000, "start", 1)], trim=(7000, 13000),
                                      display_name="jamie burns – vegitogod22", progress=got.append, new_uuid=False)
            self.assertEqual(m["_trim_offset"], 6000)
            with zipfile.ZipFile(dst) as z:
                names = sorted(z.namelist())
                out = json.loads(z.read("metadata.json"))
                self.assertEqual(z.read("c0.flashback"), b"part 1" * 100)        # the part with tick 7000, renumbered
                self.assertEqual(z.read("c1.flashback"), b"part 2" * 100)
            self.assertEqual(names, ["c0.flashback", "c1.flashback", "level_chunk_caches/0", "metadata.json"])
            self.assertEqual((out["total_ticks"], sorted(out["chunks"]), out["name"]),
                             (12000, ["c0.flashback", "c1.flashback"], "jamie burns – vegitogod22"))
            self.assertEqual(sorted(int(k) for k in out["markers"]), [500, 1000])   # the old marker and the new one
            self.assertEqual(sum(got), fbm.copy_size(src, (7000, 13000)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_item_banner_patterns(self):
        """A shield's or banner's look in an item stack: base_color, and banner_patterns (registry entries named from
        the recording's own registry, or written out in full)."""
        from mcsm import paths, protocol
        t = protocol.table(777)
        items, comps = t["items"], t["components"]
        paths.REGISTRIES["banner_pattern"] = ["minecraft:base", "minecraft:border", "minecraft:creeper"]
        asset = b"minecraft:globe"
        bp = (_vi(comps.index("banner_patterns")) + _vi(2)
              + _vi(3) + _vi(14)                                          # entry 2 (creeper), red
              + _vi(0) + _vi(len(asset)) + asset + _vi(5) + b"x.y.z" + _vi(11))   # written out: globe, blue
        stack = _vi(1) + _vi(items.index("shield")) + _vi(2) + _vi(0) + _vi(comps.index("base_color")) + _vi(15) + bp
        got, _ = paths.item_stack(stack, 0, items, comps)
        self.assertEqual(got[0], "shield")
        self.assertEqual(got[4], {"base": "black", "patterns": [["creeper", "red"], ["globe", "blue"]]})
        reg = (_vi(7) + _vi(len(b"minecraft:banner_pattern")) + b"minecraft:banner_pattern" + _vi(2)
               + _vi(len(b"minecraft:base")) + b"minecraft:base" + bytes([0])
               + _vi(len(b"minecraft:bricks")) + b"minecraft:bricks" + bytes([0]))
        self.assertEqual(paths.registry_names(reg, "minecraft:banner_pattern"), ["minecraft:base", "minecraft:bricks"])
        self.assertIsNone(paths.registry_names(reg, "minecraft:worldgen/biome"))

    def test_hotbar_and_inventory(self):
        """The recorded player's hotbar (set_player_inventory, set_held_slot) from a recording, and a player's slots
        from a backup's playerdata (1.21.5+ equipment, or the older armour slots)."""
        import struct
        from mcsm import history, paths, protocol
        t = protocol.table(777)
        items, comps, pk = t["items"], t["components"], t["packets"]

        def stack(name, n=1):
            return _vi(n) + _vi(items.index(name)) + _vi(0) + _vi(0)
        recs = [("create_local_player", struct.pack(">QQ", 1, 2) + struct.pack(">ddd", 0.0, 64.0, 0.0) + b"\0" * 40)]
        for tick in range(6):
            recs.append(("move_entities", _fb_move([(7, 0.0, 64.0, 0.0)])))
            if tick == 1:
                recs.append(("game_packet", _vi(pk["set_player_inventory"]) + _vi(0) + stack("diamond_sword")))
                recs.append(("game_packet", _vi(pk["set_player_inventory"]) + _vi(8) + stack("cobblestone", 25)))
                recs.append(("game_packet", _vi(pk["set_player_inventory"]) + _vi(20) + stack("dirt")))   # not the hotbar
            if tick == 4:
                recs.append(("game_packet", _vi(pk["set_held_slot"]) + _vi(8)))
                recs.append(("game_packet", _vi(pk["set_player_inventory"]) + _vi(8) + _vi(0)))           # used up
            recs.append(("next_tick", b""))
        tmp = tempfile.mkdtemp()
        try:
            z = os.path.join(tmp, "r.zip")
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("metadata.json", json.dumps({"protocol_version": 777, "version_string": "26.3"}))
                zf.writestr("c0.flashback", _fb_chunk(recs))
            ev = [e for e in paths.exact_rows(z, os.path.join(tmp, "cache"), {})["events"] if e[2] in ("hb", "hs")]
            self.assertEqual(ev, [[1, None, "hb", [0, "diamond_sword", "", None, 1]],
                                  [1, None, "hb", [8, "cobblestone", "", None, 25]],
                                  [4, None, "hs", 8], [4, None, "hb", [8, "", "", None, 0]]])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        new = history._inventory_of({"Inventory": [{"Slot": 0, "id": "minecraft:bow", "count": 1},
                                                   {"Slot": 9, "id": "minecraft:torch", "count": 64}],
                                     "equipment": {"head": {"id": "minecraft:iron_helmet", "count": 1}},
                                     "EnderItems": [{"Slot": 3, "id": "minecraft:elytra", "count": 1}],
                                     "SelectedItemSlot": 0, "Health": 20.0})
        self.assertEqual(new["slots"], {"0": ["bow", 1], "9": ["torch", 64], "head": ["iron_helmet", 1]})
        self.assertEqual((new["ender"], new["selected"]), ({"3": ["elytra", 1]}, 0))
        old = history._inventory_of({"Inventory": [{"Slot": 103, "id": "minecraft:diamond_helmet", "Count": 1},
                                                   {"Slot": -106, "id": "minecraft:shield", "Count": 1}]})
        self.assertEqual(old["slots"], {"head": ["diamond_helmet", 1], "offhand": ["shield", 1]})


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
            # exact playback of a stretch: every recorded movement, with the events and world state keys
            rec = next(r for r in self.data.replays if "2024-05-03--20-30-00" in r.path)
            t0 = int(rec.start.timestamp() * 1000)
            for _ in range(200):                                                 # read in the background first
                ex = get(f"api/exact/{t0}/{t0 + 30_000}")
                if not ex["pending"]:
                    break
                _time.sleep(0.05)
            self.assertGreater(len(ex["players"]["Steve"]), 500)               # 20 a second (he walks each tick)
            self.assertEqual(set(ex) >= {"players", "events", "world", "mobs"}, True)
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
        changed = os.path.join(new, "world", "region", "r.1.0.mca")           # a later save (fast CI clocks
        os.utime(changed, (time.time() + 60, time.time() + 60))               # can give both the same time)
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
            while (app.busy or getattr(app, "lib_loading", False)) and time.time() < deadline:
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
            with zipfile.ZipFile(p, "a") as zf:                  # a fixed time: the same bytes in both backups
                zf.writestr(zipfile.ZipInfo("extra.txt", date_time=(2024, 5, 3, 18, 0, 0)), "changed")
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
            # a new instance with the mod but never recorded in (no flashback folder yet) counts too
            new = os.path.join(appdata, "PrismLauncher", "instances", "Flashback 1.21.5")
            os.makedirs(os.path.join(new, "minecraft", "mods"))
            open(os.path.join(new, "minecraft", "mods", "Flashback-0.39.10-for-MC1.21.5.jar"), "wb").close()
            with open(os.path.join(new, "mmc-pack.json"), "w") as fh:
                json.dump({"components": [{"uid": "net.minecraft", "version": "1.21.5"}]}, fh)
            self.assertEqual(fbm.find_flashback_dir(None, "1.21.5", exact=True),
                             os.path.join(new, "minecraft", "flashback"))
            self.assertIsNone(fbm.find_flashback_dir(None, "1.21.4", exact=True))
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
        cube_faces = {f: {"texture": "#" + f, "cullface": f} for f in ("down", "up", "north", "south", "west", "east")}
        six = {f: {"texture": "#all"} for f in ("down", "up", "north", "south", "west", "east")}
        files = {
            "blockstates/stone.json": {"variants": {"": {"model": "minecraft:block/stone"}}},
            "blockstates/glass.json": {"variants": {"": {"model": "minecraft:block/glass"}}},
            "blockstates/short_grass.json": {"variants": {"": {"model": "minecraft:block/short_grass"}}},
            "blockstates/oak_slab.json": {"variants": {"type=bottom": {"model": "minecraft:block/oak_slab"},
                                                       "type=top": {"model": "minecraft:block/oak_slab_top"}}},
            "blockstates/oak_stairs.json": {"variants": {"facing=north": {"model": "minecraft:block/oak_stairs"},
                                                         "facing=east": {"model": "minecraft:block/oak_stairs", "y": 90}}},
            "blockstates/oak_fence.json": {"multipart": [{"apply": {"model": "minecraft:block/fence_post"}},
                                                         {"when": {"north": "true"}, "apply": {"model": "minecraft:block/fence_side"}}]},
            "models/block/cube.json": {"elements": [{"from": [0, 0, 0], "to": [16, 16, 16], "faces": cube_faces}]},
            "models/block/cube_all.json": {"parent": "block/cube", "textures": {
                **{f: "#all" for f in ("down", "up", "north", "south", "west", "east")}, "particle": "#all"}},
            "models/block/stone.json": {"parent": "block/cube_all", "textures": {"all": {"sprite": "block/stone"}}},
            "models/block/glass.json": {"parent": "block/cube_all", "textures": {"all": "block/glass"}},
            "models/block/cross.json": {"textures": {"particle": "#cross"}, "elements": [
                {"from": [0.8, 0, 8], "to": [15.2, 16, 8], "rotation": {"origin": [8, 8, 8], "axis": "y", "angle": 45, "rescale": True},
                 "faces": {"north": {"texture": "#cross"}, "south": {"texture": "#cross"}}},
                {"from": [8, 0, 0.8], "to": [8, 16, 15.2], "rotation": {"origin": [8, 8, 8], "axis": "y", "angle": 45, "rescale": True},
                 "faces": {"west": {"texture": "#cross"}, "east": {"texture": "#cross"}}}]},
            "models/block/tinted_cross.json": {"parent": "block/cross"},
            "models/block/short_grass.json": {"parent": "minecraft:block/tinted_cross",
                                              "textures": {"cross": "minecraft:block/short_grass"}},
            "models/block/oak_slab.json": {"textures": {"all": "block/oak_planks"}, "elements": [
                {"from": [0, 0, 0], "to": [16, 8, 16], "faces": six}]},
            "models/block/oak_slab_top.json": {"textures": {"all": "block/oak_planks"}, "elements": [
                {"from": [0, 8, 0], "to": [16, 16, 16], "faces": six}]},
            # a stair as one step on the north half (enough to see the turn)
            "models/block/oak_stairs.json": {"textures": {"all": "block/oak_planks"}, "elements": [
                {"from": [0, 0, 0], "to": [16, 16, 8], "faces": six}]},
            "models/block/fence_post.json": {"textures": {"all": "block/oak_planks"}, "elements": [
                {"from": [6, 0, 6], "to": [10, 16, 10], "faces": six}]},
            "models/block/fence_side.json": {"textures": {"all": "block/oak_planks"}, "elements": [
                {"from": [7, 12, 0], "to": [9, 15, 9], "faces": six}]},
            "blockstates/grass_block.json": {"variants": {"snowy=false": {"model": "minecraft:block/grass_block"}}},
            "models/block/grass_block.json": {"textures": {"top": "block/grass_block_top", "side": "block/grass_block_side",
                                                           "overlay": "block/grass_block_side_overlay", "bottom": "block/dirt"},
                "elements": [{"from": [0, 0, 0], "to": [16, 16, 16], "faces": {
                    "down": {"texture": "#bottom", "cullface": "down"}, "up": {"texture": "#top", "cullface": "up", "tintindex": 0},
                    **{f: {"texture": "#side", "cullface": f} for f in ("north", "south", "west", "east")}}},
                    {"from": [0, 0, 0], "to": [16, 16, 16], "faces": {
                        f: {"texture": "#overlay", "cullface": f, "tintindex": 0} for f in ("north", "south", "west", "east")}}]},
            "data/minecraft/worldgen/biome/plains.json": {"temperature": 0.8, "downfall": 0.4, "effects": {"water_color": 4159204}},
            "data/minecraft/worldgen/biome/badlands.json": {"temperature": 2.0, "downfall": 0.0,
                                                           "effects": {"grass_color": "#90814d", "water_color": "#3f76e4"}},
            "blockstates/oak_stairs_uv.json": {"variants": {"facing=east": {"model": "minecraft:block/oak_stairs", "y": 90,
                                                                            "uvlock": True}}},
        }
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in files.items():
                zf.writestr(name if name.startswith("data/") else "assets/minecraft/" + name, json.dumps(data))
            zf.writestr("assets/minecraft/textures/block/stone.png", b"\x89PNG stone")
            zf.writestr("assets/minecraft/textures/entity/chest/normal.png", b"\x89PNG chest")
            for half in ("left", "right"):                # a double chest's halves have their own textures
                zf.writestr(f"assets/minecraft/textures/entity/chest/normal_{half}.png", b"\x89PNG chest")
            zf.writestr("assets/minecraft/textures/colormap/grass.png", TestRealBlocks._png(256, 256, (10, 200, 30)))

    @staticmethod
    def _faces(r):
        from mcsm import blockmesh as bm
        ox, oz = r["origin"]
        out = []
        for i in range(0, len(r["faces"]), bm.RECORD.size):
            x, y, z, d, k, ex, tint, _l0, _l1 = bm.RECORD.unpack_from(r["faces"], i)
            out.append((x + ox, y, z + oz, d, r["infos"][k]["name"], ex, tint))
        return out

    @staticmethod
    def _png(w, h, rgb):
        """A plain RGB PNG of one colour (for the colour maps in the test jar)."""
        import zlib

        def chunk(kind, body):
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
        raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

    def _world(self, tmp, block_fn, light_fn=None, biome_fn=None):
        from mcsm import terrain
        world = os.path.join(tmp, "srv", "world")
        terrain.write_region(os.path.join(world, "region", "r.0.0.mca"),
                             {(0, 0): terrain.make_chunk_blocks(0, 0, block_fn, 56, 80, light_fn, biome_fn)})
        return world

    def test_blocks(self):
        from mcsm import blockmesh as bm
        tmp = tempfile.mkdtemp()
        try:
            jar = os.path.join(tmp, "client.jar")
            self._jar(jar)
            # a step: ground to 64 in the west half, to 70 in the east half; a tuft of grass, a slab, a stair, a fence

            def b(x, y, z):
                h = 64 if x < 8 else 70
                if y <= h:
                    return "minecraft:stone"
                if (x, y, z) == (2, 65, 2):
                    return "minecraft:short_grass"
                if (x, y, z) == (3, 65, 3):
                    return "minecraft:oak_slab[type=bottom]"
                if (x, y, z) == (4, 65, 4):
                    return "minecraft:oak_stairs[facing=east,half=bottom]"
                if (x, y, z) == (5, 65, 5):
                    return "minecraft:oak_fence[east=false,north=true,south=false,west=false]"
                return None
            world = self._world(tmp, b)
            look = bm.Look(jar)
            r = bm.build(os.path.join(tmp, "srv"), world, [(8.0, 8.0)], look, radius=40)
            infos = {i["name"]: i for i in r["infos"]}
            self.assertEqual(infos["stone"]["kind"], "cube")
            self.assertTrue(infos["stone"]["opaque"] and "block/stone" in r["textures"])      # {"sprite": ...}
            self.assertEqual(infos["short_grass"]["kind"], "model")                            # crossed quads
            self.assertEqual(len(infos["short_grass"]["quads"]), 4)
            slab = infos["oak_slab"]
            self.assertEqual((slab["kind"], max(v for q in slab["quads"] for v in q[4:15:3])), ("model", 0.5))
            # the stair turned to face east: its step (north half when facing north) is now on the east half
            stair_x = [v for q in infos["oak_stairs"]["quads"] for v in q[3:15:3]]
            self.assertEqual(min(stair_x), 0.5)
            self.assertEqual(len(infos["oak_fence"]["quads"]), 12)                             # post + north arm
            at = {f[:4] for f in self._faces(r)}
            self.assertIn((10, 70, 5, 0), at)                          # top of the high part
            self.assertIn((8, 70, 5, 4), at)                           # the step's wall, facing west, its full height
            self.assertIn((8, 65, 5, 4), at)
            self.assertNotIn((10, 70, 5, 4), at)                       # between two blocks: hidden
            self.assertNotIn((10, 64, 5, 0), at)                       # buried: hidden
            for pos in ((2, 65, 2), (3, 65, 3), (4, 65, 4), (5, 65, 5)):
                self.assertIn(pos + (7,), at)                          # models: whole
            self.assertIn((3, 64, 3, 0), at)                           # the ground under the slab still shows
            # light: this chunk has none stored, so everything is daylit; corners next to the step are shaded
            top = [f for f in self._faces(r) if f[:4] == (7, 64, 5, 0)][0]
            self.assertEqual(top[5] >> 12, 15)
            self.assertNotEqual(top[5] & 255, 0)
            self.assertEqual(look.texture_png("block/stone"), b"\x89PNG stone")
            self.assertIsNone(look.texture_png("block/nothing"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_banners(self):
        """Banners: their patterns (1.20.5+ and the older codes), base colour from the block, wall or standing."""
        from mcsm import banners, terrain
        self.assertEqual(banners.patterns_of({"patterns": [{"pattern": "minecraft:stripe_top", "color": "red"},
                                                           {"pattern": {"asset_id": "minecraft:globe"}, "color": "blue"}]}),
                         [["stripe_top", "red"], ["globe", "blue"]])
        self.assertEqual(banners.patterns_of({"Patterns": [{"Pattern": "mr", "Color": 9}]}), [["rhombus", "cyan"]])
        tmp = tempfile.mkdtemp()
        try:
            srv = os.path.join(tmp, "srv")
            world = os.path.join(srv, "world")

            def b(x, y, z):
                if y < 64:
                    return "minecraft:stone"
                if (x, y, z) == (3, 64, 3):
                    return "minecraft:red_banner[rotation=4]"
                if (x, y, z) == (5, 65, 5):
                    return "minecraft:blue_wall_banner[facing=east]"
                return None
            chunk = terrain.make_chunk_blocks(0, 0, b, 56, 80)
            chunk["block_entities"] = [
                {"id": "minecraft:banner", "x": 3, "y": 64, "z": 3, "patterns": [{"pattern": "minecraft:cross", "color": "white"}]},
                {"id": "minecraft:banner", "x": 5, "y": 65, "z": 5},
                {"id": "minecraft:banner", "x": 9, "y": 64, "z": 9}]          # its block is gone: left out
            terrain.write_region(os.path.join(world, "region", "r.0.0.mca"), {(0, 0): chunk})
            found = sorted(banners.banners_in_tile(srv, world, "minecraft:overworld", 0, 0), key=lambda v: v["x"])
            self.assertEqual(found, [
                {"x": 3, "y": 64, "z": 3, "base": "red", "wall": False, "facing": "north", "rotation": 4, "patterns": [["cross", "white"]]},
                {"x": 5, "y": 65, "z": 5, "base": "blue", "wall": True, "facing": "east", "rotation": 0, "patterns": []}])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_container_moves(self):
        """A container's contents after Ledger's item moves: taken off the stacks of that item (last slot first), put
        on a stack of it or into the first empty slot; and undone, for a backup taken after the moment."""
        from mcsm import containers as ct
        slots = {0: ["dirt", 60], 3: ["dirt", 10], 5: ["torch", 1]}
        mv = [{"kind": "out", "item": "dirt", "count": 12}, {"kind": "in", "item": "stone", "count": 70},
              {"kind": "in", "item": "dirt", "count": 2}, {"kind": "out", "item": "torch", "count": 1}]
        after = ct.apply(slots, 27, mv)
        self.assertEqual(after, {0: ["dirt", 60], 1: ["stone", 64], 2: ["stone", 6]})
        back = ct.apply(after, 27, mv, forward=False)      # the same items again (slots are a guess: the log has none)
        totals = {}
        for item, n in back.values():
            totals[item] = totals.get(item, 0) + n
        self.assertEqual(totals, {"dirt": 70, "torch": 1})
        self.assertEqual((ct.kind_of("minecraft:red_shulker_box"), ct.SIZES["furnace"]), ("shulker_box", 3))
        self.assertEqual(ct._ms("2026-05-24 14:26:56.408"), 1779632816408)

    def test_entity_data_reads_past_a_name(self):
        """A named mob's data: the name (index 2, optional text component) is read past, so the pose and hand use
        after it still count; an unnamed one (absent name) the same."""
        from mcsm import paths
        name = bytes([8]) + b"\x00\x03Bob"                  # network NBT: a string tag
        for opt in (b"\x01" + name, b"\x00"):
            body = bytes([0, 0, 0x02]) + bytes([2, 6]) + opt + bytes([6, 1, 5]) + bytes([8, 0, 1]) + b"\xff"
            d = paths.entity_data(body, 0, len(body))
            self.assertEqual(d.get(0), 2)
            self.assertEqual(d.get(6), 5)
            self.assertEqual(d.get(8), 1)

    def test_effect_particles_in_entity_data(self):
        """A living thing's potion swirls (index 10, a particle list): each particle's kind and, for entity_effect,
        its ARGB colour; the faint flag (index 11, a beacon's effects) after it is still read."""
        from mcsm import paths
        parts = ["entity_effect", "infested"]
        body = bytes([10, paths.SER_PARTICLES, 2, 0]) + bytes.fromhex("ff33ebff") + bytes([1]) + bytes([11, paths.SER_BOOL, 1, 0xFF])
        d = paths.entity_data_all(body, 0, len(body), parts=parts)
        self.assertEqual(d[10], (paths.SER_PARTICLES, [["entity_effect", 0xFF33EBFF], ["infested", None]]))
        self.assertEqual(d[11], (paths.SER_BOOL, True))
        self.assertEqual(paths.entity_data_all(body, 0, len(body))[10][1], ("?", 2))      # without the names: stops

    def test_text_components(self):
        """Text on the player's screen (action bar, titles, system messages) is kept whole: a network-NBT component
        with its colour, flags (bytes as booleans), translation key and arguments, and list entries wrapped as
        {"": ...} unwrapped; a plain string tag is a string."""
        import struct as st
        from mcsm import paths

        def s(name, v):
            e = v.encode()
            return bytes([8]) + st.pack(">H", len(name)) + name.encode() + st.pack(">H", len(e)) + e
        comp = (bytes([10]) + s("translate", "sleep.skipping_night") + s("color", "red")
                + bytes([1]) + st.pack(">H", 4) + b"bold" + bytes([1])
                + bytes([9]) + st.pack(">H", 4) + b"with" + bytes([10]) + st.pack(">i", 1) + s("", "D_HumanCrafter") + bytes([0])
                + bytes([0]))
        c, k = paths.component_at(comp + b"rest", 0)
        self.assertEqual(c, {"translate": "sleep.skipping_night", "color": "red", "bold": True, "with": ["D_HumanCrafter"]})
        self.assertEqual(k, len(comp))
        plain = bytes([8]) + st.pack(">H", 2) + b"hi"
        self.assertEqual(paths.component_at(plain, 0), ("hi", 5))
        self.assertIn(60, paths.ENTITY_FX)                     # a body vanishing: the poof

    def test_boss_bars(self):
        """Boss bars from boss_event: added with title, health, colour, notches and flags; health, title, style and
        flags updated; removed. A long in entity data (a wolf's anger end) is read signed (-1: none)."""
        import struct as st
        from mcsm import paths
        uid = bytes(range(16))
        title = bytes([8]) + st.pack(">H", 11) + b"Ender Dragon"[:11]
        bars = {}
        add = uid + bytes([0]) + title + st.pack(">f", 1.0) + bytes([5, 0, 1])
        self.assertTrue(paths.boss_event(add, 0, len(add), bars))
        bid = uid.hex()[:12]
        self.assertEqual(bars[bid], {"title": "Ender Drago", "health": 1.0, "color": "purple", "notches": 0, "flags": 1})
        hp = uid + bytes([2]) + st.pack(">f", 0.25)
        self.assertTrue(paths.boss_event(hp, 0, len(hp), bars))
        style = uid + bytes([4, 2, 4])
        paths.boss_event(style, 0, len(style), bars)
        self.assertEqual((bars[bid]["health"], bars[bid]["color"], bars[bid]["notches"]), (0.25, "red", 4))
        rm = uid + bytes([1])
        self.assertTrue(paths.boss_event(rm, 0, len(rm), bars))
        self.assertEqual(bars, {})
        body = bytes([22, paths.SER_VARLONG]) + bytes([0xFF] * 9) + bytes([1]) + bytes([0xFF])
        self.assertEqual(paths.entity_data_all(body, 0, len(body))[22], (paths.SER_VARLONG, -1))

    def test_mob_details(self):
        """Mobs' looks from entity data: baby (16), sheep wool (17; 26.x 18), horse variant (18; 26.x 19), slime size,
        a name tag as a compound text component with extra parts."""
        from mcsm import paths
        B, V, BOOL = paths.SER_BYTE, paths.SER_VARINT, paths.SER_BOOL
        self.assertEqual(paths.mob_details("cow", {16: (BOOL, True)}, True), [["baby", 1]])
        self.assertEqual(paths.mob_details("creeper", {16: (BOOL, True)}, True), [])     # not a baby kind
        self.assertEqual(paths.mob_details("sheep", {17: (B, 0x1E)}, False), [["wool", 0x1E]])
        self.assertEqual(paths.mob_details("sheep", {18: (B, 6)}, True), [["wool", 6]])
        self.assertEqual(paths.mob_details("horse", {19: (V, 769)}, True), [["variant", 769]])
        self.assertEqual(paths.mob_details("slime", {16: (V, 4)}, True), [["size", 4]])
        # {"text": "Fe", "extra": ["l", {"text": "ix"}]}
        comp = (bytes([10]) + bytes([8]) + b"\x00\x04text" + b"\x00\x02Fe"
                + bytes([9]) + b"\x00\x05extra" + bytes([8]) + b"\x00\x00\x00\x01" + b"\x00\x01l" + bytes([0]))
        txt, end = paths._text_of(comp, 0)
        self.assertEqual((txt, end), ("Fel", len(comp)))
        body = bytes([2, 6, 1]) + comp + b"\xff"
        self.assertEqual(paths.mob_details("cat", paths.entity_data_all(body, 0, len(body)), True), [["name", "Fel"]])

    def test_mob_variants_villagers_stands(self):
        """A variant is the first registry entry in a mob's data (named from the recording's registries); villager
        data is type, job, level; armour stands' flags and poses; an arrow stuck in a block."""
        from mcsm import paths
        old = dict(paths.REGISTRIES)
        try:
            paths.REGISTRIES["cat_variant"] = ["minecraft:all_black", "minecraft:black", "minecraft:siamese"]
            # cat: 18 tame flags (byte), 19 owner (none), 20 variant (registry kind 21: id 2), 21 sound (id 0)
            body = bytes([18, 0, 5, 19, 13, 0, 20, 21, 2, 21, 22, 0, 0xFF])
            d = paths.entity_data_all(body, 0, len(body))
            self.assertEqual(paths.mob_details("cat", d, True), [["variant", "siamese"], ["tame", 5]])
            body = bytes([19, 18, 2, 5, 3, 0xFF])                      # villager data: plains, farmer, level 3
            d = paths.entity_data_all(body, 0, len(body))
            self.assertEqual(paths.mob_details("villager", d, True), [["vd", ["plains", "farmer", 3]]])
            rot = struct.pack(">fff", -15.0, 0.0, 10.0)
            body = bytes([15, 0, 0x0C, 19, 9]) + rot + b"\xff"
            d = paths.entity_data_all(body, 0, len(body))
            self.assertEqual(paths.mob_details("armor_stand", d, True), [["asf", 12], ["asp", {"19": [-15.0, 0.0, 10.0]}]])
            body = bytes([8, 0, 1, 10, 8, 1, 0xFF])
            self.assertEqual(paths.mob_details("arrow", paths.entity_data_all(body, 0, len(body)), True), [["ig", 1]])
        finally:
            paths.REGISTRIES.clear(); paths.REGISTRIES.update(old)

    def test_removed_light_goes_dark(self):
        """A torch in the backup taken away during a recording: the floor it lit goes dark (its stored light falls
        off from the torch), while light from another torch still there flows back in."""
        from mcsm import blockmesh as bm

        class Fake:                                       # stone up to y 64; the backup's light from two torches
            min_y, lit = 0, True

            def __init__(self, edits):
                self.edits = edits
                self.tops = [65 if (x, z) in ((8, 8), (2, 2)) else 64 for z in range(16) for x in range(16)]

            def original_state(self, x, y, z):
                if y <= 64:
                    return "minecraft:stone"
                return "minecraft:torch" if (x, y, z) in ((8, 65, 8), (2, 65, 2)) else None

            def state(self, x, y, z):
                return self.edits[(x, y, z)] if (x, y, z) in self.edits else self.original_state(x, y, z)

            def biome(self, x, y, z):
                return "minecraft:plains"

            def light(self, x, y, z):
                if y <= 64:
                    return 0, 0
                d1 = abs(x - 8) + abs(y - 65) + abs(z - 8)
                d2 = abs(x - 2) + abs(y - 65) + abs(z - 2)
                return 15, max(0, 14 - d1, 14 - d2)
        tmp = tempfile.mkdtemp()
        try:
            jar = os.path.join(tmp, "client.jar")
            self._jar(jar)
            look = bm.Look(jar)

            def floor(edits):
                r = bm.build(os.path.join(tmp, "nowhere"), os.path.join(tmp, "nowhere", "w"), [], look, only={(0, 0)},
                             overrides={(0, 0): Fake(edits)})
                out = {}
                for i in range(0, len(r["faces"]), bm.RECORD.size):
                    x, y, z, d, k, ex, tint, l0, l1 = bm.RECORD.unpack_from(r["faces"], i)
                    if d == 0 and y == 64:
                        out[(x + r["origin"][0], z + r["origin"][1])] = (ex >> 8) & 15
                return out
            before, after = floor({}), floor({(8, 65, 8): "minecraft:air"})
            self.assertEqual(before[(12, 8)], 10)
            self.assertEqual(after[(12, 8)], max(0, 14 - (10 + 6)))   # only the far torch's light: 14 - 16 -> 0
            self.assertEqual(after[(3, 2)], 13)                         # next to the torch still there: unchanged
            self.assertEqual(after[(6, 6)], 6)                          # between them: the remaining torch's 14 - 8
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_map_updates(self):
        """A map_item_data body: id, scale, locked, icons (read past), then a rectangle of colours; and a filled map
        item's number in its look."""
        from mcsm import moment, paths
        icon = bytes([1, 1]) + bytes([3, 10, 20, 4]) + bytes([0])            # icons: one, no name
        body = bytes([42, 0, 1]) + icon + bytes([2, 3, 5, 7]) + bytes([6]) + bytes([9, 9, 9, 8, 8, 8])
        self.assertEqual(moment._map_patch(body, 0, len(body)), (42, 5, 7, 2, 3, bytes([9, 9, 9, 8, 8, 8])))
        self.assertIsNone(moment._map_patch(bytes([42, 0, 0, 0, 0]), 0, 5))        # no colours: nothing to lay
        st, _k = paths.item_stack(bytes([1, 1, 1, 0, 0, 77]), 0, ["air", "filled_map"], ["map_id"])
        self.assertEqual(st[4], {"map": 77})

    def test_game_modes_and_scale(self):
        """player_info_update: each player's game mode (with the add-player and listed entries read past); and
        update_attributes: the scale attribute with its modifiers applied as the game does."""
        from mcsm import paths
        u1, u2 = bytes(range(16)), bytes(range(16, 32))
        name = b"\x05Steve" + b"\x01" + b"\x08textures" + b"\x03abc" + b"\x00"     # name, one property, no signature
        body = bytes([0x01 | 0x04 | 0x08]) + bytes([2]) + u1 + name + bytes([3, 1]) + u2 + b"\x04Alex\x00" + bytes([0, 1])
        self.assertEqual(paths.game_modes(body), [(u1, "Steve", 3), (u2, "Alex", 0)])
        attrs = ["armor", "scale", "max_health"]
        mod = b"\x0bmc:shrinker" + struct.pack(">d", -0.5) + bytes([2])           # multiply the total by 1 - 0.5
        body = bytes([7, 2]) + bytes([2]) + struct.pack(">d", 20.0) + bytes([0]) \
            + bytes([1]) + struct.pack(">d", 1.0) + bytes([1]) + mod
        self.assertEqual(paths.scale_of(body, attrs), (7, 0.5))
        self.assertIsNone(paths.scale_of(bytes([7, 1, 0]) + struct.pack(">d", 2.0) + bytes([0]), attrs))   # armour only

    def test_item_glint_and_trim(self):
        """An item's look: glint when it has enchantments (or the override says so), and its trim's material and
        pattern by name from the recording's registries (holders: id + 1)."""
        from mcsm import paths
        old = dict(paths.REGISTRIES)
        try:
            paths.REGISTRIES["trim_material"] = ["minecraft:amethyst", "minecraft:gold"]
            paths.REGISTRIES["trim_pattern"] = ["minecraft:bolt", "minecraft:coast", "minecraft:sentry"]
            items = ["air", "diamond_chestplate"]
            comps = ["enchantments", "trim", "enchantment_glint_override"]
            # count 1, item 1, 2 components added, 0 removed; enchantments: 1 entry (id 4, level 3); trim: gold, sentry
            body = bytes([1, 1, 2, 0, 0, 1, 4, 3, 1, 2, 3])
            st, _k = paths.item_stack(body, 0, items, comps)
            self.assertEqual(st[0], "diamond_chestplate")
            self.assertEqual(st[4], {"glint": True, "trim": ["gold", "sentry"]})
            body = bytes([1, 1, 1, 0, 2, 0])                    # the glint turned off
            self.assertEqual(paths.item_stack(body, 0, items, comps)[0][4], {"glint": False})
        finally:
            paths.REGISTRIES.clear(); paths.REGISTRIES.update(old)

    def test_frame_item_in_entity_data(self):
        """An item frame's entity data (26.x): facing (8, a direction), the item (9, an item stack) and its turn (10)."""
        from mcsm import paths
        body = bytes([8, 12, 4, 9, 7, 1, 3, 0, 0, 10, 1, 5, 0xFF])
        d = paths.entity_data_all(body, 0, len(body), True, ["air", "stone", "dirt", "clock"], [])
        self.assertEqual(d[8], (12, 4))
        self.assertEqual(d[9][1][0], "clock")
        self.assertEqual(d[10], (paths.SER_VARINT, 5))

    def test_sign_written_packet(self):
        """A block_entity_data packet with a sign's text (network NBT: no root name) gives its place and lines;
        other block entities give nothing."""
        from mcsm import nbt, paths
        x, y, z = 286, 77, -2409
        pos = ((x & 0x3FFFFFF) << 38) | ((z & 0x3FFFFFF) << 12) | (y & 0xFFF)
        body_nbt = nbt.dumps({"front_text": {"messages": ['""', '{"text":"Room 9"}', '""', '""'], "color": "black",
                                             "has_glowing_text": 0}, "is_waxed": 0}, compress=False)
        net = b"\x0a" + body_nbt[3:]                         # the root compound without its (empty) name
        body = struct.pack(">q", pos) + bytes([7]) + net
        self.assertEqual(paths.sign_written(body), [286, 77, -2409, {"lines": ["", "Room 9", "", ""], "color": "black",
                                                                     "glow": False}, None])
        other = nbt.dumps({"patterns": []}, compress=False)
        self.assertIsNone(paths.sign_written(struct.pack(">q", pos) + bytes([3]) + b"\x0a" + other[3:]))

    def test_hanging(self):
        """Paintings (variant, 2D facing, centre) and item frames (item, facing, turn, glow), within the tile; frames
        holding a map are left to the maps code."""
        from unittest import mock
        from mcsm import hanging, mapart
        ents = [{"id": "minecraft:painting", "Pos": [186.5, 170.0, -770.96875], "facing": 0, "variant": "minecraft:sunset"},
                {"id": "minecraft:glow_item_frame", "Pos": [204.97, 175.5, -772.5], "Facing": 4, "ItemRotation": 3,
                 "Item": {"id": "minecraft:clock", "count": 1}},
                {"id": "minecraft:item_frame", "Pos": [200.5, 70.5, -700.03], "Facing": 2,
                 "Item": {"id": "minecraft:filled_map", "components": {"minecraft:map_id": 4}}},
                {"id": "minecraft:painting", "Pos": [500.5, 70.0, -770.5], "facing": 1, "variant": "minecraft:kebab"}]
        with mock.patch.object(mapart, "entity_regions", return_value=["x/r.0.-2.mca"]), \
                mock.patch.object(mapart, "_region_entities", return_value=ents):
            r = hanging.hanging_in_tile("w", "minecraft:overworld", 1, -7)
        self.assertEqual(r["paintings"], [{"x": 186.5, "y": 170.0, "z": -770.96875, "facing": 0, "variant": "sunset"}])
        self.assertEqual(r["frames"], [{"x": 204.97, "y": 175.5, "z": -772.5, "facing": 4, "rot": 3, "item": "clock",
                                        "glow": True, "invisible": False}])

    def test_head_skin_hash(self):
        """A player head's skin: the texture hash from its profile (1.20.5+) or SkullOwner (older); none without."""
        import base64
        from mcsm import heads
        h = "a" * 64
        val = base64.b64encode(json.dumps({"textures": {"SKIN": {"url": f"http://textures.minecraft.net/texture/{h}"}}}).encode()).decode()
        self.assertEqual(heads.skin_hash({"profile": {"name": "x", "properties": [{"name": "textures", "value": val}]}}), h)
        self.assertEqual(heads.skin_hash({"SkullOwner": {"Name": "x", "Properties": {"textures": [{"Value": val.rstrip("=")}]}}}), h)
        self.assertIsNone(heads.skin_hash({"profile": {"name": "x"}}))

    def test_sign_text(self):
        """Sign lines from every layout: JSON text in strings (to 1.21.4), components as NBT (1.21.5+), the old
        Text1-4; empty signs give nothing."""
        from mcsm import signs
        be = {"front_text": {"messages": ['{"text":"Room 1"}', '""', '"x"', '{"text":"a","extra":["b",{"text":"c"}]}'],
                             "color": "red", "has_glowing_text": 1},
              "back_text": {"messages": ["", "", "", ""]}}
        t = signs.text_of(be)
        self.assertEqual(t["front"], {"lines": ["Room 1", "", "x", "abc"], "color": "red", "glow": True})
        self.assertIsNone(t["back"])
        t = signs.text_of({"front_text": {"messages": ["Bread", {"text": "fresh"}, [{"text": "to"}, "day"], ""]}})
        self.assertEqual(t["front"]["lines"], ["Bread", "fresh", "today", ""])
        t = signs.text_of({"Text1": '{"text":"old"}', "Text2": '""', "Text3": '""', "Text4": '""'})
        self.assertEqual(t["front"]["lines"][0], "old")

    def test_tamed_collars(self):
        """A tamed cat's or wolf's flags and collar dye (26.x: cat 18 / 23, wolf 18 / 21; 1.21: one index less)."""
        from mcsm import paths
        B, V = paths.SER_BYTE, paths.SER_VARINT
        self.assertEqual(paths.mob_details("cat", {18: (B, 5), 23: (V, 3)}, True), [["tame", 5], ["collar", 3]])
        self.assertEqual(paths.mob_details("wolf", {17: (B, 4), 20: (V, 11)}, False), [["tame", 4], ["collar", 11]])

    def test_placed_light_and_smooth_corners(self):
        """A torch placed after the backup (a patch) lights the floor around it, less a level a step; each corner of
        a face gets the game's smooth light (the 4 cells round it averaged), so a face near the torch is brighter
        on its torch side."""
        from mcsm import blockmesh as bm
        self.assertEqual((bm.emission("minecraft:torch"), bm.emission("minecraft:redstone_lamp[lit=false]"),
                          bm.emission("minecraft:candle[candles=3,lit=true]")), (14, 0, 9))
        tmp = tempfile.mkdtemp()
        try:
            jar = os.path.join(tmp, "client.jar")
            self._jar(jar)
            world = self._world(tmp, lambda x, y, z: "minecraft:stone" if y <= 64 else None)
            look = bm.Look(jar)
            r = bm.build(os.path.join(tmp, "srv"), world, [], look, only={(0, 0)},
                         patches={(0, 0): {(8, 65, 8): "minecraft:torch"}})
            ox, oz = r["origin"]
            top = {}
            for i in range(0, len(r["faces"]), bm.RECORD.size):
                x, y, z, d, k, ex, tint, l0, l1 = bm.RECORD.unpack_from(r["faces"], i)
                if d == 0 and y == 64:
                    sm = l0 | (l1 << 32)
                    top[(x + ox, z + oz)] = ((ex >> 8) & 15, [((sm >> (12 * c)) >> 6) & 63 for c in range(4)])
            self.assertEqual(top[(10, 8)][0], 12)                 # two steps from the torch: 14 - 2
            self.assertEqual(top[(15, 8)][0], 7)
            self.assertEqual(top[(3, 3)][0], 4)                   # 10 steps round (5 + 5)
            corners = top[(10, 8)][1]
            self.assertGreater(max(corners), min(corners))        # smooth: brighter towards the torch
            self.assertEqual(sorted(corners), [44, 44, 48, 48])   # away: 11 + 11 + 10 + 12; towards: 13 + 11 + 12 + 12
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_water_shape(self):
        """Water shaped as the game does: a source 8/9 high, flowing water lower by level, corners averaged over the
        four cells round them (cells of 0.8 or more counting ten times), full under more water; flowing texture."""
        from mcsm import blockmesh as bm
        tmp = tempfile.mkdtemp()
        try:
            jar = os.path.join(tmp, "client.jar")
            self._jar(jar)

            def b(x, y, z):
                if y < 64:
                    return "minecraft:stone"
                if y == 64 and z == 5 and 2 <= x <= 6:              # a channel: a source, then flowing levels 1..4
                    return f"minecraft:water[level={x - 2}]"
                if y in (64, 65) and (x, z) == (10, 10):         # two sources stacked: the lower one is full
                    return "minecraft:water[level=0]"
                return "minecraft:stone" if y == 64 else None
            world = self._world(tmp, b)
            look = bm.Look(jar)
            r = bm.build(os.path.join(tmp, "srv"), world, [(8.0, 8.0)], look, radius=40)
            # the top quad of each water cell: its four corner heights
            def heights(pos):
                ox, oz = r["origin"]
                rec = [i for i in range(0, len(r["faces"]), bm.RECORD.size)
                       if bm.RECORD.unpack_from(r["faces"], i)[:4] == (pos[0] - ox, pos[1], pos[2] - oz, 0)]
                info = r["infos"][bm.RECORD.unpack_from(r["faces"], rec[0])[4]]
                q = info["quads"][0]
                return sorted({round(q[4 + 3 * k], 3) for k in range(4)}), info
            src, _ = heights((2, 64, 5))
            far, far_info = heights((6, 64, 5))
            self.assertTrue(max(src) > max(far))                  # sloping down from the source
            self.assertLess(min(far), 0.6)                        # level 4: (8 - 4) / 9 = 0.44 at its own corners
            self.assertIn("block/water_flow", r["textures"])
            low, _ = heights((10, 65, 10))
            self.assertEqual(low, [0.688])                        # the upper source on its own, open air round it:
                                                                  # (8/9 x 10) / 13 = 0.68 at each corner (to 1/32)
            ox, oz = r["origin"]
            self.assertFalse(any(bm.RECORD.unpack_from(r["faces"], i)[:4] == (10 - ox, 64, 10 - oz, 0)
                                 for i in range(0, len(r["faces"]), bm.RECORD.size)))   # under water: no top drawn
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_under_water(self):
        """The sea floor is drawn under water (seen through it, as in the game), with the water's surface."""
        from mcsm import blockmesh as bm
        tmp = tempfile.mkdtemp()
        try:
            jar = os.path.join(tmp, "client.jar")
            self._jar(jar)
            world = self._world(tmp, lambda x, y, z: "minecraft:stone" if y <= 58 else "minecraft:water" if y <= 64 else None)
            r = bm.build(os.path.join(tmp, "srv"), world, [(8.0, 8.0)], bm.Look(jar), radius=40)
            at = {(f[0], f[1], f[2], f[3], f[4]) for f in self._faces(r)}
            self.assertIn((5, 58, 5, 0, "stone"), at)          # the floor, facing up into the water
            self.assertIn((5, 64, 5, 0, "water"), at)          # the surface
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_maps(self):
        """Maps: the colour bytes with the game's shades, a PNG, both file layouts, and maps in item frames
        (26.x components and the older tag) found in an entities region."""
        import zlib
        from mcsm import mapart, nbt, terrain
        tmp = tempfile.mkdtemp()
        try:
            world = os.path.join(tmp, "world")
            cols = bytes([0] * 100 + [1 * 4 + 2] * 16284)           # see-through, then grass at full shade
            os.makedirs(os.path.join(world, "data", "minecraft", "maps"))
            with open(os.path.join(world, "data", "minecraft", "maps", "7.dat"), "wb") as fh:
                fh.write(nbt.dumps({"data": {"colors": nbt.Typed(nbt.TAG_BYTE_ARRAY, list(cols)), "xCenter": 64, "zCenter": -64, "scale": 1,
                                             "dimension": "minecraft:overworld", "locked": 1}}))
            with open(os.path.join(world, "data", "map_3.dat"), "wb") as fh:
                fh.write(nbt.dumps({"data": {"colors": nbt.Typed(nbt.TAG_BYTE_ARRAY, list(cols)), "xCenter": 0, "zCenter": 0, "dimension": 0}}))
            self.assertEqual(sorted(mapart.map_files(world)), [3, 7])
            m = mapart.read_map(mapart.map_files(world)[7])
            self.assertEqual((m["x"], m["z"], m["scale"], m["locked"], m["colors"][100]), (64, -64, 1, True, 6))
            table = [[0, 0, 0, 0]] * 4 + [[0x7F * s // 255, 0xB2 * s // 255, 0x38 * s // 255, 255] for s in mapart.SHADES] + [[0, 0, 0, 0]] * 248
            png = mapart.png(m["colors"], table)
            self.assertTrue(png.startswith(b"\x89PNG") and b"IHDR" in png and b"IEND" in png)
            raw = zlib.decompress(png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8])
            self.assertEqual(raw[1 + 100 * 4:1 + 101 * 4], bytes([0x7F, 0xB2, 0x38, 255]))   # row 0, pixel 100: grass
            self.assertEqual(raw[1:5], bytes([0, 0, 0, 0]))                                     # see-through
            ent = os.path.join(world, "entities", "r.0.0.mca")
            terrain.write_region(ent, {(0, 0): {"Entities": [
                {"id": "minecraft:glow_item_frame", "TileX": 5, "TileY": 70, "TileZ": 6, "Facing": 3, "ItemRotation": 1,
                 "Item": {"id": "minecraft:filled_map", "count": 1, "components": {"minecraft:map_id": 7}}},
                {"id": "minecraft:item_frame", "TileX": 6, "TileY": 70, "TileZ": 6, "Facing": 3,
                 "Item": {"id": "minecraft:filled_map", "Count": 1, "tag": {"map": 3}}},
                {"id": "minecraft:item_frame", "TileX": 7, "TileY": 70, "TileZ": 6, "Facing": 3,
                 "Item": {"id": "minecraft:diamond", "count": 1}},
                {"id": "minecraft:cow", "Pos": [1.0, 64.0, 1.0]}]}})
            self.assertEqual(mapart.entity_regions(world, "minecraft:overworld"), [ent])
            self.assertEqual(mapart.frames_in_region(ent),
                             [{"map": 7, "x": 5, "y": 70, "z": 6, "facing": 3, "rot": 1, "glow": True},
                              {"map": 3, "x": 6, "y": 70, "z": 6, "facing": 3, "rot": 0, "glow": False}])
            d = mapart.world_maps(world, os.path.join(tmp, "cache"))
            self.assertEqual(([m["id"] for m in d["maps"]], len(d["frames"])), ([7, 3], 2))
            self.assertEqual(mapart.world_maps(world, os.path.join(tmp, "cache"))["stamp"], d["stamp"])   # kept
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_map_palette_from_class(self):
        """The map colours from MapColor's static initialiser: new MapColor(id, rgb) for each colour."""
        import struct as st
        from mcsm import mapart
        # a minimal class file: constants, then <clinit> doing new MapColor(1, 0x7FB238) ... for 31 colours
        def utf(s):
            b = s.encode()
            return b"\x01" + st.pack(">H", len(b)) + b
        cp = [utf("net/minecraft/world/level/material/MapColor"), b"\x07\x00\x01",            # 1, 2: class
              utf("<init>"), utf("(II)V"), b"\x0c\x00\x03\x00\x04",                          # 3, 4, 5: name and type
              b"\x0a\x00\x02\x00\x05", utf("<clinit>"), utf("()V"), utf("Code"),             # 6: method ref; 7, 8, 9
              b"\x03" + st.pack(">i", 0x7FB238), utf("java/lang/Object"), b"\x07\x00\x0b"]  # 10: int; 11, 12
        code = b""
        for i in range(1, 32):
            code += b"\xbb\x00\x02\x59" + b"\x10" + bytes([i]) + (b"\x12\x0a" if i == 1 else b"\x11" + st.pack(">h", i * 100)) + b"\xb7\x00\x06\x57"
        code += b"\xb1"
        attr = st.pack(">HHI", 2, 0, len(code)) + code + st.pack(">HH", 0, 0)
        data = (b"\xca\xfe\xba\xbe" + st.pack(">HHH", 0, 52, len(cp) + 1) + b"".join(cp)
                + st.pack(">HHHH", 0x21, 2, 12, 0) + st.pack(">H", 0)
                + st.pack(">H", 1) + st.pack(">HHHH", 8, 7, 8, 1) + st.pack(">HI", 9, len(attr)) + attr + st.pack(">H", 0))
        tmp = tempfile.mkdtemp()
        try:
            jar = os.path.join(tmp, "client.jar")
            with zipfile.ZipFile(jar, "w") as z:
                z.writestr("net/minecraft/world/level/material/MapColor.class", data)
            p = mapart.palette_from_jar(jar)
            self.assertEqual((len(p), p[1], p[2], p[31]), (31, 0x7FB238, 200, 3100))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_item_texture(self):
        """Hotbar pictures: the item's own picture, else its item definition's model (through a select to its
        fallback) and that model's texture; drawn-by-code items (special) have none."""
        from mcsm import blockmesh as bm
        tmp = tempfile.mkdtemp()
        try:
            jar = os.path.join(tmp, "client.jar")
            A = "assets/minecraft/"
            with zipfile.ZipFile(jar, "w") as z:
                for t in ("item/potion", "block/oak_log", "block/crafting_table_front", "item/compass_16"):
                    z.writestr(f"{A}textures/{t}.png", b"png")
                z.writestr(f"{A}items/oak_log.json", json.dumps({"model": {"type": "minecraft:model", "model": "minecraft:block/oak_log"}}))
                z.writestr(f"{A}models/block/oak_log.json", json.dumps({"textures": {"side": "minecraft:block/oak_log", "particle": "x"}}))
                z.writestr(f"{A}items/crafting_table.json", json.dumps({"model": {"type": "minecraft:model", "model": "block/crafting_table"}}))
                z.writestr(f"{A}models/block/crafting_table.json", json.dumps({"textures": {"front": "block/crafting_table_front"}}))
                z.writestr(f"{A}items/compass.json", json.dumps({"model": {"type": "minecraft:select", "cases": [],
                           "fallback": {"type": "minecraft:model", "model": "item/compass_16"}}}))
                z.writestr(f"{A}models/item/compass_16.json", json.dumps({"textures": {"layer0": "item/compass_16"}}))
                z.writestr(f"{A}items/shield.json", json.dumps({"model": {"type": "minecraft:special", "base": "item/shield"}}))
            look = bm.Look(jar)
            self.assertEqual([look.item_texture(i) for i in ("potion", "oak_log", "crafting_table", "compass", "shield", "nothing", "../x")],
                             ["item/potion", "block/oak_log", "block/crafting_table_front", "item/compass_16", None, None, None])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_caves(self):
        """build(slab=...): the walls of a sealed tunnel are drawn, the open surface above isn't."""
        from mcsm import blockmesh as bm
        tmp = tempfile.mkdtemp()
        try:
            jar = os.path.join(tmp, "client.jar")
            self._jar(jar)

            def b(x, y, z):
                if y == 60 and 2 <= x <= 12 and z == 5:          # a tunnel, sealed in stone
                    return None
                return "minecraft:stone" if y <= 70 else None
            world = self._world(tmp, b)
            r = bm.build(os.path.join(tmp, "srv"), world, [(8.0, 8.0)], bm.Look(jar), radius=40, slab=(56, 66))
            at = {f[:4] for f in self._faces(r)}
            self.assertIn((5, 59, 5, 0), at)                    # the tunnel's floor, facing up
            self.assertIn((5, 61, 5, 1), at)                    # its ceiling, facing down
            self.assertFalse(any(p[1] == 70 for p in at))       # the surface isn't part of the caves
            self.assertTrue(all(f[5] >> 12 >= bm.CAVE_LIGHT or (f[5] >> 8) & 15 >= bm.CAVE_LIGHT
                                for f in self._faces(r)))      # lit enough to see
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_particle_sources(self):
        """Blocks whose particles the player's own game makes up are listed with each tile: torches (their flame
        0.27 back towards the wall), candles, campfires, leaves over air; drips under a block with water on it."""
        from mcsm import blockmesh as bm
        self.assertEqual(bm.ambient_code("torch", {}), "torch,flame,0.5,0.7,0.5")
        self.assertEqual(bm.ambient_code("soul_wall_torch", {"facing": "east"}), "torch,soul_fire_flame,0.23,0.92,0.5")
        self.assertIsNone(bm.ambient_code("redstone_torch", {"lit": "false"}))
        self.assertEqual(bm.ambient_code("white_candle", {"lit": "true", "candles": "2"}), "candle,0.375,0.44,0.5,0.625,0.5,0.44")
        self.assertIsNone(bm.ambient_code("candle", {"lit": "false", "candles": "1"}))
        self.assertEqual(bm.ambient_code("campfire", {"lit": "true", "signal_fire": "true"}), "campfire,1")
        self.assertEqual(bm.ambient_code("cherry_leaves", {}), "leaves,cherry_leaves")
        self.assertIsNone(bm.ambient_code("stone", {}))
        tmp = tempfile.mkdtemp()
        try:
            jar = os.path.join(tmp, "client.jar")
            self._jar(jar)

            def b(x, y, z):
                if (x, z) == (3, 3):                      # a stone with water on it, over two blocks of air
                    return {65: "minecraft:water[level=0]", 64: "minecraft:stone"}.get(y, "minecraft:stone" if y < 62 else None)
                if (x, y, z) == (10, 65, 10):
                    return "minecraft:lava[level=0]"
                return "minecraft:stone" if y < 64 else "minecraft:grass_block[snowy=false]" if y == 64 else None
            world = self._world(tmp, b)
            r = bm.build(os.path.join(tmp, "srv"), world, [(8.0, 8.0)], bm.Look(jar), radius=40)
            fx = [tuple(r["fx"][k:k + 5]) for k in range(0, len(r["fx"]), 5)]
            got = {(x, y, z): (r["fxk"][c], e) for x, y, z, c, e in fx}
            self.assertEqual(got.get((3, 64, 3)), ("drip,water", 2))
            self.assertEqual(got.get((10, 65, 10)), ("lava", 0))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_biomes_water_and_stand_ins(self):
        """Grass takes its biome's colour (blended at the edge), waterlogged blocks have water round them, double
        chests reach each other, skulls are drawn, uvlock keeps a turned stair's texture lined up."""
        from mcsm import blockmesh as bm
        tmp = tempfile.mkdtemp()
        try:
            jar = os.path.join(tmp, "client.jar")
            self._jar(jar)

            def b(x, y, z):
                if y < 64:
                    return "minecraft:stone"
                if y == 64:
                    return "minecraft:grass_block[snowy=false]"
                if (x, y, z) == (3, 65, 3):
                    return "minecraft:oak_slab[type=bottom,waterlogged=true]"
                return None
            world = self._world(tmp, b, biome_fn=lambda x, y, z: "minecraft:badlands" if x >= 8 else "minecraft:plains")
            look = bm.Look(jar)
            r = bm.build(os.path.join(tmp, "srv"), world, [(8.0, 8.0)], look, radius=40)
            faces = self._faces(r)
            tops = {(f[0], f[2]): f[6] for f in faces if f[1] == 64 and f[3] == 0 and f[4] == "grass_block"}
            plains, badlands = bm.rgb565((10, 200, 30)), bm.rgb565((0x90, 0x81, 0x4d))     # colour map / the biome's own
            self.assertEqual(tops[(1, 1)], plains)
            self.assertEqual(tops[(14, 14)], badlands)
            self.assertNotIn(tops[(7, 7)], (plains, badlands))                            # blended where they meet
            grass = next(i for i in r["infos"] if i["name"] == "grass_block")
            self.assertEqual(grass["quads"][0][-1], 1)                                     # top: tinted
            self.assertEqual(grass["quads"][2][-1], 2)                                     # sides: only the overlay
            self.assertIn("block/grass_block_side+overlay", r["textures"])
            water = [f for f in faces if f[:3] == (3, 65, 3) and f[4] == "water"]
            self.assertTrue(any(f[3] == 0 for f in water))                                  # its water surface
            self.assertEqual(water[0][6], bm.rgb565((63, 118, 228)))                        # plains water colour
            # stand-ins and turns
            chest_l = look.infos[look.info("minecraft:chest[facing=north,type=left]")]
            chest_1 = look.infos[look.info("minecraft:chest[facing=north,type=single]")]
            self.assertEqual(max(v for q in chest_l["quads"] for v in q[3:15:3]), 1.0)       # reaches the other half
            self.assertLess(max(v for q in chest_1["quads"] for v in q[3:15:3]), 1.0)
            skull = look.info("minecraft:skeleton_skull[rotation=0]")
            self.assertIsNotNone(skull)
            self.assertIsNone(look.info("minecraft:dragon_head[rotation=0]"))               # not drawn yet
            stair = look.infos[look.info("minecraft:oak_stairs_uv[facing=east]")]
            top = next(q for q in stair["quads"] if all(abs(q[4 + 3 * k] - 1) < 1e-6 for k in range(4)))
            # uvlock: the top's texture corners follow the world x/z of its corners
            self.assertEqual([round(v, 3) for v in top[15:23:2]], [round(v, 3) for v in top[3:15:3]])
            w, h, px = bm.png_pixels(self._png(4, 2, (1, 2, 3)))
            self.assertEqual((w, h, px[0], len(px)), (4, 2, (1, 2, 3), 8))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_inside_and_light(self):
        """A room under a roof is drawn when you can see into it (a doorway), and not when it's sealed; faces get
        the light stored for the space in front of them."""
        from mcsm import blockmesh as bm
        tmp = tempfile.mkdtemp()
        try:
            jar = os.path.join(tmp, "client.jar")
            self._jar(jar)

            def house(door):
                def b(x, y, z):
                    if y <= 64:
                        return "minecraft:stone"
                    inside = 4 <= x <= 10 and 4 <= z <= 10
                    if not inside or y > 69:
                        return None
                    if y == 69:
                        return "minecraft:stone"                 # the roof
                    if x in (4, 10) or z in (4, 10):
                        if door and z == 4 and x == 7 and y in (65, 66):
                            return None                          # a doorway
                        return "minecraft:stone"
                    return None
                return b

            def light(x, y, z):
                inside = 5 <= x <= 9 and 5 <= z <= 9 and 65 <= y <= 68
                return (3, 12) if inside else (15, 0)
            for door, seen in ((True, True), (False, False)):
                sub = os.path.join(tmp, str(door))
                os.makedirs(sub)
                world = self._world(sub, house(door), light)
                r = bm.build(os.path.join(sub, "srv"), world, [(8.0, 8.0)], bm.Look(jar), radius=40)
                floor = [f for f in self._faces(r) if f[:4] == (7, 64, 7, 0)]
                self.assertEqual(bool(floor), seen, door)
                if floor:
                    self.assertEqual(((floor[0][5] >> 12) & 15, (floor[0][5] >> 8) & 15), (3, 12))   # sky 3, torch 12
            self.assertEqual(bm.parse_state("minecraft:oak_stairs[facing=east,half=top]"),
                             ("oak_stairs", {"facing": "east", "half": "top"}))
            self.assertEqual(bm.parse_state("minecraft:stone"), ("stone", {}))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_eye_height(self):
        from mcsm import worldtext as wt
        sc = {"keyframeTracks": [{"keyframeType": "CAMERA", "enabled": True, "keyframesByTick": {
            "0": {"position": [1.0, 64.0, 2.0], "yaw": 10, "pitch": 5, "type": "camera"}}}]}
        self.assertEqual(wt.camera_at(sc, 0)[0], [1.0, 64.0, 2.0])     # the keyframe as Flashback stores it
        self.assertEqual(wt.eye_at(sc, 0), ([1.0, 64.0 + wt.EYE_HEIGHT, 2.0], 10.0, 5.0))   # what you see from


class TestLinkKnowsCopies(unittest.TestCase):
    """Flashback may have the app's copy (with text) open instead of the original recording."""

    def test_copy_by_content(self):
        from mcsm.videoeditor import VideoEditor
        tmp = tempfile.mkdtemp()
        try:
            reps = os.path.join(tmp, "minecraft", "flashback", "replays")
            os.makedirs(reps)
            chunks = {"c0.flashback": {"duration": 6000}, "c1.flashback": {"duration": 120}}

            def rec(path, **meta):
                with zipfile.ZipFile(path, "w") as zf:
                    zf.writestr("metadata.json", json.dumps({"total_ticks": 6120, "chunks": chunks, **meta}))
            original = os.path.join(tmp, "orig.zip")
            rec(original, uuid="orig")
            # a copy of a copy whose middle link is gone: still the same recording by content
            rec(os.path.join(reps, "a (text).zip"), uuid="copy2", original_uuid_mcsm="copy1-gone")
            rec(os.path.join(reps, "b (text).zip"), uuid="other", original_uuid_mcsm="x", total_ticks=99)

            class Link:
                instance = {"minecraft": os.path.join(tmp, "minecraft")}
            self.assertTrue(VideoEditor._copy_of("copy2", "orig", Link, original))
            self.assertFalse(VideoEditor._copy_of("other", "orig", Link, original))
            self.assertFalse(VideoEditor._copy_of("copy2", "orig", Link, None))   # no recording to compare
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestEditPicker(unittest.TestCase):
    """Choosing a Flashback edit by scene name and recording."""

    def test_list_and_pick(self):
        from mcsm import flashback, videotext as vt
        tmp = tempfile.mkdtemp()
        old = flashback.flashback_folders
        try:
            fb = os.path.join(tmp, "fb")
            os.makedirs(os.path.join(fb, "editor_states"))
            os.makedirs(os.path.join(fb, "replays"))
            cam = {"position": [0, 64, 0], "yaw": 0, "pitch": 0, "type": "camera"}
            with open(os.path.join(fb, "editor_states", "aaa.json"), "w") as fh:
                json.dump({"sceneIndex": 1, "scenes": [
                    {"name": "Intro", "exportStartTicks": 0, "exportEndTicks": 200, "keyframeTracks": [
                        {"keyframeType": "CAMERA", "keyframesByTick": {"0": cam, "200": cam}}]},
                    {"name": "Empty", "keyframeTracks": []}]}, fh)
            with zipfile.ZipFile(os.path.join(fb, "replays", "2026-06-06--19-55-14.zip"), "w") as zf:
                zf.writestr("metadata.json", json.dumps({"uuid": "aaa", "name": "Our base"}))
            flashback.flashback_folders = lambda: [{"path": fb, "name": "Test FB", "mc": "26.1.2"}]
            edits = vt.list_edits()
            self.assertEqual([e["scene_name"] for e in edits], ["Intro", "Empty"])
            intro = edits[0]
            self.assertEqual((intro["length"], intro["keyframes"], intro["current"]), (10.0, 2, False))
            self.assertEqual(intro["recording_name"], "Our base · 2026-06-06--19-55-14")
            self.assertTrue(edits[1]["current"] and edits[1]["length"] is None)
            if not _have_display():
                return
            from mcsm import gui
            root = _new_tk()
            try:
                app = gui.App(root)
                app._vid_edit_pick(edits)
                win = [w for w in root.winfo_children() if isinstance(w, gui.tk.Toplevel)][-1]
                tree = next(w for w in win.winfo_children() if isinstance(w, gui.ttk.Treeview))
                self.assertEqual(len(tree.get_children()), 1)          # the empty scene is hidden
                self.assertIn("Intro", tree.item(tree.get_children()[0])["values"][0])
                win.destroy()
            finally:
                root.destroy()
        finally:
            flashback.flashback_folders = old
            shutil.rmtree(tmp, ignore_errors=True)


class TestLinkStream(unittest.TestCase):
    """The live picture and playing along: tickets, path, play/pause, clock (stand-in mod)."""

    def test_stream_and_play(self):
        import http.server
        import threading
        from mcsm import flashlink
        seen = []

        class Fake(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._reply({"tick": 1234, "paused": False})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                seen.append((self.path, body))
                self._reply({"ticket": "abc"} if self.path == "/ticket" else {"ok": True})

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            link = flashlink.Link(srv.server_address[1], "secret")
            url = link.stream_url(800, 24)
            self.assertEqual(url, f"http://127.0.0.1:{srv.server_address[1]}/stream?k=abc&w=800&fps=24")
            self.assertNotIn("secret", url)                       # the long-lived token never goes to the page
            link.path([100, 1, 64, 2, 90, 0, 70])
            link.play(150)
            link.pause()
            self.assertEqual(link.clock()["tick"], 1234)
            self.assertEqual([p for p, _ in seen], ["/ticket", "/path", "/play", "/pause"])
            self.assertEqual(seen[1][1], {"p": [100, 1, 64, 2, 90, 0, 70]})
            self.assertEqual(seen[2][1], {"tick": 150})
        finally:
            srv.shutdown()


class TestMoments(unittest.TestCase):
    """The important moments: grouping, scoring, rarity, repeats, recordings (with Fable's review cases)."""

    def test_find_moments(self):
        from datetime import datetime, timedelta, timezone
        from mcsm import models as m, moments as mo
        t0 = datetime(2026, 5, 24, 20, 0, tzinfo=timezone.utc)
        at = lambda minutes: t0 + timedelta(minutes=minutes)     # noqa: E731
        d = m.ServerData(root="x")
        for n, secs in (("Alex", 7200), ("Steve", 7200), ("Bob", 7200), ("Visitor", 10)):
            d.players[n.lower()] = m.PlayerProfile(uuid=None, name=n, names_seen=[n], session_count=1,
                                                   session_seconds=secs)
        d.log_files = [m.LogFileInfo("logs/first.log", "2026-05-20", "filename", 10, at(-4 * 24 * 60), at(-4 * 24 * 60 + 5))]
        ev = lambda minute, typ, who, text, **data: m.Event(at(minute), typ, who, text, data=data)   # noqa: E731
        d.events = [
            ev(-4 * 24 * 60 + 1, m.EV_JOIN, "Visitor", "Visitor joined the game"),     # the logs' first day: not a first join
            ev(0, m.EV_JOIN, "Bob", "Bob joined the game"),                             # first join (moment)
            ev(30, m.EV_DEATH, "Alex", "Alex was slain by Steve", killer="Steve"),       # PvP ...
            ev(31, m.EV_DEATH, "Alex", "Alex was slain by Steve", killer="Steve"),       # ... a repeat counts half
            ev(32, m.EV_CHAT, "Steve", "gg"),
            ev(60, m.EV_DEATH, "Alex", "Alex was slain by Zombie using [Steve's Sword]", killer="Zombie"),
            ev(90, m.EV_ADVANCEMENT, "Alex", "Alex has made the advancement [Rare One]", title="Rare One"),
            ev(120, m.EV_ADVANCEMENT, "Alex", "Alex has made the advancement [Common]", title="Common"),
            ev(121, m.EV_ADVANCEMENT, "Steve", "Steve has made the advancement [Common]", title="Common"),
            ev(122, m.EV_ADVANCEMENT, "Bob", "Bob has made the advancement [Common]", title="Common"),
            ev(300, m.EV_LEAVE, "Bob", "Bob left the game"),                            # not a moment
        ] + [ev(400 + 2 * k, m.EV_CHAT, "Alex", f"message {k}") for k in range(20)]    # 40 min of chat
        d.sessions = [m.Session(n, at(-10), at(500)) for n in ("Alex", "Steve", "Bob")]
        d.replays = [m.Replay(path="rec.zip", start=at(25), duration_ms=20 * 60 * 1000, format="flashback",
                              recorded_player="Steve"),
                     m.Replay(path="rec (highlights).zip", start=at(25), duration_ms=20 * 60 * 1000,
                              format="flashback", recorded_player="Steve")]       # the app's copy: not counted
        ms = mo.find_moments(d)
        pvp = ms[0]
        self.assertEqual(pvp["kinds"], {"death": 2, "chat": 1})
        self.assertEqual(pvp["players"], ["Alex", "Steve"])                     # the killer is in it
        self.assertEqual(pvp["score"], round((6 + 6 * 0.5 + 0.3) * (1 + 0.5 * 1), 1))   # Bob online too
        self.assertEqual(pvp["recordings"], [{"path": "rec.zip", "player": "Steve", "mc": ""}])
        self.assertEqual(pvp["start"], int(at(30).timestamp() * 1000) - mo.PAD_BEFORE_MS)
        zombie = next(x for x in ms if "Zombie" in x["title"])
        self.assertEqual(zombie["score"], round(3 * (1 + 0.5 * 2), 1))           # a name in an item isn't PvP
        rare = next(x for x in ms if "Rare One" in x["title"])
        self.assertEqual(rare["score"], round(3.0 * (1 + 0.5 * 2), 1))           # 1 / (1 of 3 regulars)
        common = next(x for x in ms if "Common" in x["title"])
        self.assertEqual(common["score"], 7.0)            # 1 each, all three in it, + 4 for all three together (mcsm.moods)
        self.assertEqual(common["mood"], "celebration")
        titles = [x["title"] for x in ms]
        self.assertIn("Bob joins for the first time", titles)
        self.assertNotIn("Visitor joins for the first time", titles)
        chats = [x for x in ms if x["title"].startswith("Chat:")]
        self.assertGreaterEqual(len(chats), 4)                                   # 40 minutes split up
        self.assertTrue(all(x["end"] - x["start"] <= mo.MAX_MS + mo.PAD_BEFORE_MS + mo.PAD_AFTER_MS for x in ms))
        self.assertEqual(len({x["id"] for x in ms}), len(ms))

    def test_keep_twice_is_one_highlight(self):
        from mcsm import highlights as hl
        tmp = tempfile.mkdtemp()
        try:
            store = hl.Store(os.path.join(tmp, "h.json"))
            a = store.put({"title": "Fight", "start": 1000, "end": 9000, "moment": "abc"})
            b = store.put({"title": "Fight", "start": 1000, "end": 9000, "moment": "abc"})
            self.assertEqual(a["id"], b["id"])
            self.assertEqual(len(store.list()), 1)
            self.assertEqual(store.list()[0]["moment"], "abc")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestStoryCommand(unittest.TestCase):
    """`mcsm story`: which library world, and the history viewer opened in story mode."""

    def test_world_choice(self):
        from mcsm import cli, library as lib
        seen = []
        old_list, old_hist = lib.list_worlds, cli.cmd_history
        tmp = tempfile.mkdtemp()
        try:
            cli.cmd_history = lambda a: seen.append(a) or 0
            for worlds, expect in (([], 2), ([{"path": "A"}, {"path": "B"}], 2), ([{"path": os.path.join(tmp, "W")}], 0)):
                lib.list_worlds = lambda *_a, _w=worlds, **_k: _w
                self.assertEqual(cli.main(["story", "--library", tmp, "--no-browser"]), expect)
            self.assertEqual(len(seen), 1)
            self.assertTrue(seen[0].story)
            self.assertEqual(seen[0].server, [os.path.join(tmp, "W")])
            self.assertEqual(cli.main(["story", os.path.join(tmp, "X"), "--no-browser"]), 0)   # a world given
            self.assertEqual(seen[1].server, [os.path.join(tmp, "X")])
        finally:
            lib.list_worlds, cli.cmd_history = old_list, old_hist
            shutil.rmtree(tmp, ignore_errors=True)


class TestQuietButBusy(unittest.TestCase):
    """Building, map art and exploring found from movement (and AFK ignored)."""

    @staticmethod
    def _path(t0, minutes, fn, step_s=2):
        out = []
        for k in range(int(minutes * 60 / step_s)):
            s = k * step_s
            x, y, z = fn(s)
            out.append([t0 + s * 1000, x, y, z, "minecraft:overworld"])
        return out

    def test_stretches(self):
        import math
        from mcsm import activity
        t0 = 1_700_000_000_000
        build = self._path(t0, 90, lambda s: (100 + 20 * math.sin(s / 7), 70 + (s % 40) / 10, 200 + 20 * math.cos(s / 11)))
        afk = self._path(t0, 120, lambda s: (500.0, 64.0, 500.0))
        mapart = self._path(t0, 60, lambda s: (1000 + (s * 3) % 100, 63.0, 1000 + (s // 34) * 2 % 100))
        search = self._path(t0, 50, lambda s: (2000 + 600 * math.sin(s / 90), 70, 2000 + s * 1.5 * (1 if (s // 300) % 2 else -1)))
        st = activity.find_stretches({"Builder": build, "Afk": afk, "Artist": mapart, "Seeker": search})
        kinds = {(s["player"], s["kind"]) for s in st}
        self.assertIn(("Builder", "building"), kinds)
        self.assertIn(("Artist", "map_art"), kinds)
        self.assertFalse([s for s in st if s["player"] == "Afk"])          # standing still isn't building
        seeker = [s for s in st if s["player"] == "Seeker"]
        self.assertTrue(seeker and seeker[0]["kind"] in ("searching", "exploring"))
        b = next(s for s in st if s["player"] == "Builder")
        self.assertGreaterEqual(b["minutes"], 85)
        self.assertEqual(b["clip"][1], b["end"])                           # film the finished work
        self.assertTrue(b["area"][0] < 100 < b["area"][2])

    def test_big_map_art_and_a_trip_away(self):
        import math
        from mcsm import activity
        t0 = 1_700_000_000_000

        def rows(s):                                   # 128 x 128, row by row, 2 blocks a second
            row, along = divmod(s * 2, 128)
            x = along if row % 2 == 0 else 128 - along
            return 3000 + x, 63.0, 3000 + (row * 2) % 128
        art = activity.find_stretches({"Artist": self._path(t0, 70, rows)})
        self.assertEqual([s["kind"] for s in art], ["map_art"])          # one stretch, not cut into pieces
        self.assertGreaterEqual(art[0]["area"][2] - art[0]["area"][0], 120)

        def with_trip(s):                              # building, 5 minutes away at minute 30, back again
            if 30 * 60 <= s < 35 * 60:
                return 900.0, 70.0, 900.0
            return 100 + 20 * math.sin(s / 7), 70, 200 + 20 * math.cos(s / 11)
        b = activity.find_stretches({"Builder": self._path(t0, 70, with_trip)})
        builds = [s for s in b if s["kind"] == "building"]
        self.assertEqual(len(builds), 1)
        self.assertGreaterEqual(builds[0]["end"] - builds[0]["start"], 69 * 60_000)

    def test_work_moments(self):
        import math
        from datetime import datetime, timezone
        from mcsm import models as m, moments as mo
        t0 = 1_700_000_000_000
        tracks = {"Builder": self._path(t0, 180, lambda s: (100 + 20 * math.sin(s / 7), 70, 200 + 20 * math.cos(s / 11))),
                  "Helper": self._path(t0, 120, lambda s: (110 + 15 * math.sin(s / 5), 70, 190 + 15 * math.cos(s / 9)))}
        d = m.ServerData(root="x")
        for n in ("Builder", "Helper"):
            d.players[n.lower()] = m.PlayerProfile(uuid=None, name=n, names_seen=[n], session_count=1, session_seconds=9000)
        before, after = datetime.fromtimestamp(t0 / 1000 - 86400, timezone.utc), datetime.fromtimestamp(t0 / 1000 + 86400 * 5, timezone.utc)
        d.player_history["Builder"] = [{"time": before, "used": 1000, "blocks_mined": 0, "fly_cm": 0, "used_by_item": {"minecraft:white_concrete": 10}},
                                       {"time": after, "used": 41000, "blocks_mined": 0, "fly_cm": 0, "used_by_item": {"minecraft:white_concrete": 30000}}]
        ms = mo.find_moments(d, tracks=tracks)
        work = [x for x in ms if x.get("mode")]
        self.assertEqual(len(work), 1)                                      # two players, one area: one moment
        w = work[0]
        self.assertEqual(w["players"], ["Builder", "Helper"])
        self.assertIn("3 h", w["title"])
        self.assertIn("items used 40k, mostly white concrete", " ".join(w["why"]))
        hours = (w["end"] - w["start"]) / 3_600_000
        self.assertAlmostEqual(w["score"], round(mo.WEIGHTS["work"] * math.log2(1 + hours) * (1 + 0.5), 1), places=1)
        self.assertEqual(mo.find_moments(d), [])                            # no positions: nothing to find


class TestBuildTimelapse(unittest.TestCase):
    """A timelapse of a build moment: the builder's own files, one speed, one camera, Flashback copies."""

    def test_plan_and_export(self):
        import math
        from datetime import datetime, timezone
        from mcsm import models as m, timelapse as tl
        tmp = tempfile.mkdtemp()
        try:
            t0 = datetime(2026, 4, 10, 16, 0, tzinfo=timezone.utc)
            ms0 = int(t0.timestamp() * 1000)

            def rec(name, minutes):
                path = os.path.join(tmp, name)
                ticks = minutes * 60 * 20
                with zipfile.ZipFile(path, "w") as zf:
                    zf.writestr("metadata.json", json.dumps({"uuid": name, "protocol_version": 775, "total_ticks": ticks,
                                                             "version_string": "26.1.2",
                                                             "chunks": {"c0.flashback": {"duration": ticks}}}))
                    zf.writestr("c0.flashback", TestWorldTextTiming._chunk(ticks))
                return path
            files = {"b1.zip": rec("b1.zip", 60), "b2.zip": rec("b2.zip", 60), "v.zip": rec("v.zip", 130)}
            d = m.ServerData(root=tmp)
            d.replays = [m.Replay("b1.zip", t0, 60 * 60_000, format="flashback", recorded_player="Builder"),
                         m.Replay("b2.zip", datetime.fromtimestamp((ms0 + 60 * 60_000) / 1000, timezone.utc), 60 * 60_000,
                                  format="flashback", recorded_player="Builder"),
                         m.Replay("v.zip", t0, 130 * 60_000, format="flashback", recorded_player="Visitor")]
            # Builder works in the area for 2 hours, with a 20-minute break in the middle
            pts = [[ms0 + s * 1000, 100 + 10 * math.sin(s / 9), 70, 200 + 10 * math.cos(s / 7), "minecraft:overworld"]
                   for s in range(0, 120 * 60, 2) if not 50 * 60 <= s < 70 * 60]
            moment = {"id": "x", "title": "Builder: building", "mode": "building", "start": ms0, "end": ms0 + 120 * 60_000,
                      "area": [88, 188, 112, 212], "dim": "minecraft:overworld", "y": 70, "players": ["Builder"],
                      "recordings": [{"path": p} for p in ("b1.zip", "b2.zip", "v.zip")]}
            pm = tl.plan_moment(d, moment, {"Builder": pts})
            self.assertEqual(pm["pov"], "Builder")                        # not the longer bystander file
            self.assertEqual([p["replay"].path for p in pm["parts"]], ["b1.zip", "b2.zip"])
            self.assertAlmostEqual(pm["seconds"], 180, delta=3)
            speeds = {round(p["plan"].active_speed, 6) for p in pm["parts"]}
            self.assertEqual(len(speeds), 1)                                  # one speed across the parts
            self.assertAlmostEqual(speeds.pop(), pm["speed"], delta=0.06)
            fb = os.path.join(tmp, "fb")
            r = tl.export_moment(d, moment, {"Builder": pts}, lambda rp: files[rp.path], fb, os.path.join(tmp, "out"))
            self.assertEqual([os.path.basename(p["file"]) for p in r["parts"]],
                             ["b1 (timelapse 1 of 2).zip", "b2 (timelapse 2 of 2).zip"])
            cams = []
            for p in r["parts"]:
                with open(p["editor_state"], encoding="utf-8") as fh:
                    st = json.load(fh)
                self.assertEqual(len(st["scenes"]), 1)
                tracks = {t["keyframeType"]: t["keyframesByTick"] for t in st["scenes"][0]["keyframeTracks"]}
                keys = sorted(int(k) for k in tracks["TIMELAPSE"])
                self.assertEqual((st["scenes"][0]["exportStartTicks"], st["scenes"][0]["exportEndTicks"]), (keys[0], keys[-1]))
                self.assertTrue(0 <= keys[0] and keys[-1] <= 60 * 60 * 20)
                cams.append(list(tracks["CAMERA"].values())[0]["position"])
            self.assertEqual(cams[0], cams[1])                                # the same camera in every part
            self.assertTrue(os.path.exists(r["order"]))
            with self.assertRaises(ValueError):                               # no positions: nothing to film
                tl.plan_moment(d, moment, {})
            # a bystander far away whose one file covers more time doesn't win: the builder was there
            far = [[ms0 + k * 2000, 3000.0, 70.0, 3000.0, "minecraft:overworld"] for k in range(0, 130 * 30)]
            self.assertEqual(tl.plan_moment(d, moment, {"Builder": pts, "Visitor": far})["pov"], "Builder")
            # a renamed player (positions under the old name, other letter case) is still found
            d.players["builder"] = m.PlayerProfile(uuid=None, name="Builder", names_seen=["OldName", "Builder"])
            self.assertEqual(tl.plan_moment(d, moment, {"oldname": pts})["pov"], "Builder")
            # a failure half-way leaves nothing behind
            fb2 = os.path.join(tmp, "fb2")
            calls = []

            def broken(rp):
                calls.append(rp.path)
                if len(calls) > 1:
                    raise OSError("disk unplugged")
                return files[rp.path]
            with self.assertRaises(OSError):
                tl.export_moment(d, moment, {"Builder": pts}, broken, fb2, os.path.join(tmp, "out2"))
            left = [f for _r, _d, fs in os.walk(fb2) for f in fs]
            self.assertEqual(left, [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestAfk(unittest.TestCase):
    """AFK: not part of a build, not filmed in a timelapse."""

    def test_afk(self):
        import math
        from mcsm import activity, timelapse as tl
        t0 = 1_700_000_000_000

        def path(minutes, fn):
            return [[t0 + s * 1000, *fn(s), "minecraft:overworld"] for s in range(0, minutes * 60, 2)]

        def work_then_afk(s):                          # 60 min building, then 40 min standing still
            if s < 60 * 60:
                return 100 + 15 * math.sin(s / 7), 70.0, 200 + 15 * math.cos(s / 9)
            return 100.0, 70.0, 215.0
        pts = path(100, work_then_afk)
        st = activity.find_stretches({"Builder": pts})
        b = next(s for s in st if s["kind"] == "building")
        self.assertLessEqual(b["end"] - t0, 61 * 60_000)              # the AFK tail isn't part of the build
        samples = tl.samples_from_tracks({"Builder": pts}, ["Builder"], t0, t0 + 100 * 60_000)
        self.assertTrue(samples and max(s[0].timestamp() for s in samples) * 1000 <= t0 + 61 * 60_000)


class TestExportByVersion(unittest.TestCase):
    """Copies only go into a Flashback of the recording's own Minecraft version."""

    def test_export_target(self):
        from mcsm import flashback as fbm, models as m
        r = m.Replay("a.zip", None, 0, format="flashback", mc_version="1.21.11")
        out = os.path.join("x", "out")
        self.assertEqual(fbm.export_target(r, lambda _r: None, "C:/fb26", out),
                         (os.path.join(out, "Flashback for Minecraft 1.21.11"), False))   # never the 26.x one
        self.assertEqual(fbm.export_target(r, lambda _r: "C:/fb1.21.11", "C:/fb26", out), ("C:/fb1.21.11", True))
        self.assertEqual(fbm.export_target(r, None, "C:/chosen", out), ("C:/chosen", True))     # chosen by hand
        old = fbm.flashback_folders
        try:
            fbm.flashback_folders = lambda: [{"path": "C:/fb26", "name": "Flashback 26.1.2", "mc": "26.1.2"}]
            self.assertIsNone(fbm.find_flashback_dir(None, "1.21.11", exact=True))
            self.assertEqual(fbm.find_flashback_dir(None, "26.1.2", exact=True), "C:/fb26")
        finally:
            fbm.flashback_folders = old


class TestTimelapseCamera(unittest.TestCase):
    """Tiny parts are left out; the camera: automatic, the owner's own views (gliding), or none."""

    def _setup(self, tiny_minutes=0.6):
        import math
        t0 = datetime(2026, 4, 10, 16, 0, tzinfo=timezone.utc)
        ms0 = int(t0.timestamp() * 1000)
        at = lambda minutes: datetime.fromtimestamp((ms0 + minutes * 60_000) / 1000, timezone.utc)   # noqa: E731
        d = m.ServerData(root="x")
        d.replays = [m.Replay("b1.zip", t0, 60 * 60_000, format="flashback", recorded_player="Builder"),
                     m.Replay("b2.zip", at(60), 5 * 60_000, format="flashback", recorded_player="Builder"),
                     m.Replay("b3.zip", at(65), 60 * 60_000, format="flashback", recorded_player="Builder")]
        # busy in b1 and b3; in b2 only for a little while (a short part)
        pts = [[ms0 + s * 1000, 100 + 10 * math.sin(s / 9), 70, 200 + 10 * math.cos(s / 7), "minecraft:overworld"]
               for s in range(0, 125 * 60, 2) if s < 60 * 60 or s >= 65 * 60 or s < 60 * 60 + tiny_minutes * 60]
        moment = {"id": "x", "title": "Builder: building", "mode": "building", "start": ms0, "end": ms0 + 125 * 60_000,
                  "area": [88, 188, 112, 212], "dim": "minecraft:overworld", "y": 70, "players": ["Builder"],
                  "recordings": [{"path": p} for p in ("b1.zip", "b2.zip", "b3.zip")]}
        return d, moment, {"Builder": pts}

    def test_tiny_part_left_out(self):
        from mcsm import timelapse as tl
        d, moment, tracks = self._setup()
        pm = tl.plan_moment(d, moment, tracks)
        self.assertEqual([p["replay"].path for p in pm["parts"]], ["b1.zip", "b3.zip"])
        self.assertEqual([p["n"] for p in pm["parts"]], [1, 2])
        self.assertEqual([s["file"] for s in pm["skipped"]], ["b2.zip"])
        self.assertAlmostEqual(pm["seconds"], 180, delta=3)                      # the length is shared out again
        self.assertTrue(all(p["seconds"] >= tl.MIN_PART_S for p in pm["parts"]))
        self.assertEqual(pm["min_part_s"], tl.MIN_PART_S)
        keep_all = tl.plan_moment(d, moment, tracks, min_part_s=0)              # the owner's cut-off: 0 keeps every part
        self.assertEqual(len(keep_all["parts"]), 3)
        self.assertEqual(keep_all["skipped"], [])
        self.assertEqual(tl.plan_moment(d, moment, tracks, min_part_s=999)["min_part_s"], tl.MAX_MIN_PART_S)
        line = tl.timeline(pm)
        self.assertEqual(line[0][2], 0)
        self.assertAlmostEqual(line[-1][3], pm["seconds"], delta=0.01)

    def test_cameras(self):
        from mcsm import flashback as fbm, timelapse as tl
        d, moment, tracks = self._setup()
        pm = tl.plan_moment(d, moment, tracks)
        p1, p2 = pm["parts"]
        fixed, moving = tl.part_camera(pm, p1, None)                             # automatic
        self.assertEqual(fixed["position"], [float(v) for v in pm["camera"][:3]])
        self.assertIsNone(moving)
        self.assertEqual(tl.part_camera(pm, p1, {"mode": "free"}), (None, None))
        st = fbm.editor_state("s", None, [(0, 0), (100, 10)])
        self.assertEqual([t["keyframeType"] for t in st["scenes"][0]["keyframeTracks"]], ["TIMELAPSE"])
        one = {"eye": [10.0, 81.62, 20.0], "yaw": 200.0, "pitch": 30.0}
        fixed, moving = tl.part_camera(pm, p1, {"mode": "views", "views": [one]})
        self.assertEqual(fixed["interpolation_type"], "HOLD")
        self.assertAlmostEqual(fixed["position"][1], 80.0, places=6)              # the eye 1.62 above the keyframe
        self.assertAlmostEqual(fixed["yaw"], -160.0, places=6)
        a = {"eye": [0.0, 101.62, 0.0], "yaw": 170.0, "pitch": 10.0}
        b = {"eye": [100.0, 101.62, 0.0], "yaw": -170.0, "pitch": 50.0}
        cam = {"mode": "views", "views": [a, b]}
        _f, k1 = tl.part_camera(pm, p1, cam)
        _f, k2 = tl.part_camera(pm, p2, cam)
        first, last = k1[min(k1)], k2[max(k2)]
        self.assertAlmostEqual(first["position"][0], 0.0, places=6)
        self.assertAlmostEqual(last["position"][0], 100.0, places=3)
        self.assertEqual(first["interpolation_type"], "LINEAR")
        # the glide carries on across parts: part 2 starts where part 1 ended
        self.assertAlmostEqual(k1[max(k1)]["position"][0], k2[min(k2)]["position"][0], delta=0.6)
        frac = p1["seconds"] / pm["seconds"]
        self.assertAlmostEqual(k1[max(k1)]["position"][0], 100 * frac, delta=0.6)
        self.assertAlmostEqual(k1[max(k1)]["yaw"] % 360, (170 + 20 * frac) % 360, delta=0.5)   # the short way round
        keys = tl.timelapse_keyframes(p1["plan"], p1["replay"].start)
        self.assertTrue(keys[0][0] <= min(k1) and max(k1) <= keys[-1][0])
        st = fbm.editor_state("s", None, keys, camera_keys=k1)
        self.assertEqual(len(st["scenes"][0]["keyframeTracks"][0]["keyframesByTick"]), len(k1))

    def test_export_free_camera(self):
        from mcsm import timelapse as tl
        d, moment, tracks = self._setup()
        tmp = tempfile.mkdtemp()
        try:
            files = {}
            for r in d.replays:
                path = os.path.join(tmp, r.path)
                ticks = r.duration_ms // 50
                with zipfile.ZipFile(path, "w") as zf:
                    zf.writestr("metadata.json", json.dumps({"uuid": r.path, "protocol_version": 775, "total_ticks": ticks,
                                                             "chunks": {"c0.flashback": {"duration": ticks}}}))
                    zf.writestr("c0.flashback", TestWorldTextTiming._chunk(ticks))
                files[r.path] = path
            r = tl.export_moment(d, moment, tracks, lambda rp: files[rp.path], os.path.join(tmp, "fb"),
                                 os.path.join(tmp, "out"), camera={"mode": "free"})
            self.assertEqual(len(r["parts"]), 2)
            with open(r["parts"][0]["editor_state"], encoding="utf-8") as fh:
                st = json.load(fh)
            self.assertNotIn("CAMERA", [t["keyframeType"] for t in st["scenes"][0]["keyframeTracks"]])
            with open(r["order"], encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("No camera was set", text)
            self.assertIn("Left out", text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestIgnorePlayers(unittest.TestCase):
    """Players who were never supposed to join are left out everywhere."""

    def test_list_and_apply(self):
        from mcsm import ignore
        tmp = tempfile.mkdtemp()
        try:
            f = os.path.join(tmp, "ignored.json")
            t = datetime(2026, 4, 1, tzinfo=timezone.utc)
            d = m.ServerData(root="x")
            d.players = {"steve": m.PlayerProfile("u-1", "Steve"),
                         "griefer": m.PlayerProfile("aaaa-bbbb", "Griefer", names_seen=["OldName"])}
            d.events = [m.Event(t, m.EV_JOIN, "Steve"), m.Event(t, m.EV_JOIN, "Griefer"), m.Event(t, m.EV_CHAT, "oldname")]
            d.sessions = [m.Session("Steve", t, t), m.Session("Griefer", t, t)]
            d.replays = [m.Replay("a.zip", t, 1000, players=["u-1", "aaaabbbb"], player_names=["Steve", "Griefer"]),
                         m.Replay("b.zip", t, 1000, recorded_player="Griefer")]
            items = ignore.add(["griefer"], d, path=f)
            self.assertEqual(items[0]["name"], "Griefer")
            self.assertEqual(items[0]["uuid"], "aaaa-bbbb")
            self.assertIn("OldName", items[0]["names"])
            self.assertEqual(ignore.apply(d, ignore.load(f)), ["Griefer"])
            self.assertEqual(list(d.players), ["steve"])
            self.assertEqual([e.player for e in d.events], ["Steve"])
            self.assertEqual([s.player for s in d.sessions], ["Steve"])
            self.assertEqual([r.path for r in d.replays], ["a.zip"])
            self.assertEqual((d.replays[0].players, d.replays[0].player_names), (["u-1"], ["Steve"]))
            self.assertTrue(ignore.is_ignored(d, "OLDNAME"))
            self.assertFalse(ignore.is_ignored(d, "Steve"))
            self.assertEqual(ignore.remove(["oldname"], path=f), [])                # any of their names takes them off
            self.assertEqual(ignore.apply(d, ignore.load(f)), [])
            old = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = tmp                                      # the command line
            try:
                self.assertEqual(cli.main(["ignore", "add", "Alex"]), 0)
                self.assertEqual([i["name"] for i in ignore.load()], ["Alex"])
                self.assertEqual(cli.main(["ignore", "remove", "Alex"]), 0)
                self.assertEqual(ignore.load(), [])
            finally:
                if old is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestJoinTimelapse(unittest.TestCase):
    """The exported parts of a timelapse joined into one video, in order, with music."""

    def test_order(self):
        from mcsm import videojoin as vj
        self.assertEqual(vj.ordered(["x (timelapse 10 of 10).mp4", "x (timelapse 2 of 10).mp4", "x (timelapse 1 of 10).mp4"]),
                         ["x (timelapse 1 of 10).mp4", "x (timelapse 2 of 10).mp4", "x (timelapse 10 of 10).mp4"])
        self.assertEqual(vj.ordered(["b.mp4", "a.mp4"]), ["b.mp4", "a.mp4"])            # no numbers: as given

    def test_join(self):
        from mcsm import videojoin as vj
        from mcsm.videotext import find_tool, video_duration, video_size
        ff = find_tool("ffmpeg")
        if not ff or not find_tool("ffprobe"):
            self.skipTest("ffmpeg not installed")
        tmp = tempfile.mkdtemp()
        try:
            def clip(name, secs, size="320x180"):
                out = os.path.join(tmp, name)
                subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30",
                                "-f", "lavfi", "-i", "sine=frequency=440", "-t", str(secs), "-c:v", "libx264",
                                "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out], check=True)
                return out
            a, b = clip("B (timelapse 2 of 2).mp4", 1), clip("B (timelapse 1 of 2).mp4", 2)
            song = os.path.join(tmp, "song.wav")
            subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220", "-t", "1", song], check=True)
            out = os.path.join(tmp, "joined.mp4")
            p = vj.join([a, b], out, music=song, say=lambda _m: None)
            self.assertTrue(p["copy"])
            self.assertEqual(p["order"], [b, a])
            self.assertAlmostEqual(video_duration(out), 3.0, delta=0.25)           # the 1 s song loops to the end
            c = clip("other.mp4", 1, size="640x360")                                 # another size: re-encoded
            out2 = os.path.join(tmp, "joined2.mp4")
            p2 = vj.join([b, c], out2, say=lambda _m: None)
            self.assertFalse(p2["copy"])
            self.assertEqual(video_size(out2), (320, 180))
            self.assertAlmostEqual(video_duration(out2), 3.0, delta=0.25)
            with self.assertRaises(ValueError):
                vj.join([a, b], a, say=lambda _m: None)                              # never over a part
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestBuildWorld3D(unittest.TestCase):
    """The world around a build for the timelapse view: terrain tiles and real blocks from a backup."""

    def test_tiles_and_blocks(self):
        from mcsm import blockmesh as bm, terrain, world3d
        tmp = tempfile.mkdtemp()
        try:
            world = os.path.join(tmp, "srv", "world")
            terrain.write_region(os.path.join(world, "region", "r.0.0.mca"),
                                 {(0, 0): terrain.make_chunk(0, 0, lambda x, z: 64, lambda x, y, z: "minecraft:stone")})
            jar = os.path.join(tmp, "client.jar")
            TestRealBlocks._jar(jar)
            bw = world3d.BuildWorld(os.path.join(tmp, "srv"), world, [0, 0, 15, 15], cache=os.path.join(tmp, "cache"))
            self.assertEqual(bw.tiles()["tiles"], [{"rx": 0, "rz": 0, "step": 2}])
            self.assertTrue(os.path.isfile(bw.tile_file(0, 0, 2)))
            self.assertIsNone(bw.tile_file(5, 5, 2))
            old = bm.find_client_jar
            bm.find_client_jar = lambda _v: jar
            try:
                info = bw.blocks("/tl3d/x/")
            finally:
                bm.find_client_jar = old
            self.assertGreater(info["faces"], 0)
            self.assertEqual(info["url"], "/tl3d/x/" + os.path.basename(bw.blocks_file()))
            self.assertTrue(os.path.isfile(bw.blocks_file()))
            self.assertTrue(os.path.isfile(bw.texture("block/stone")))
            self.assertIsNone(bw.texture("../evil"))
            # dev mode: one block of the backup
            at = bw.block_at(3, 64, 4)
            self.assertEqual((at["state"], at["top"], at["sky_light"]), ("minecraft:stone", 64, 15))
            self.assertIsNone(bw.block_at(5000, 64, 5000)["state"])                   # never explored
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestBackupChanges(unittest.TestCase):
    """What changed on the ground between two backups: builds, map art, new land; natural changes ignored."""

    @staticmethod
    def _server(base, name, h, b, chunks, extra=None):
        from mcsm import terrain
        root = os.path.join(base, name)
        os.makedirs(os.path.join(root, "world", "region"))
        with open(os.path.join(root, "server.properties"), "w") as fh:
            fh.write("level-name=world\n")
        terrain.write_region(os.path.join(root, "world", "region", "r.0.0.mca"),
                             {(cx, cz): terrain.make_chunk(cx, cz, h, b) for cx, cz in chunks})
        for (rx, rz), cks in (extra or {}).items():
            terrain.write_region(os.path.join(root, "world", "region", f"r.{rx}.{rz}.mca"),
                                 {(cx & 31, cz & 31): terrain.make_chunk(cx, cz, h, b) for cx, cz in cks})
        return root, os.path.join(root, "world")

    def test_compare(self):
        from mcsm import changes as ch
        tmp = tempfile.mkdtemp()
        try:
            chunks = [(cx, cz) for cx in range(8) for cz in range(4)]
            flat = lambda x, z: 64                                                  # noqa: E731
            grass = lambda x, y, z: "minecraft:grass_block"                         # noqa: E731
            old = self._server(tmp, "old", flat, grass, chunks)

            def h(x, z):
                if 8 <= x < 32 and 8 <= z < 32:
                    return 72                                                       # a house
                if 80 <= x < 84 and 40 <= z < 44:
                    return 70                                                       # a tree
                return 64

            def b(x, y, z):
                if 8 <= x < 32 and 8 <= z < 32:
                    return "minecraft:oak_planks"
                if 80 <= x < 84 and 40 <= z < 44:
                    return "minecraft:oak_leaves"
                if 48 <= x < 112 and 0 <= z < 16:
                    return "minecraft:white_concrete"                               # flat map art
                return "minecraft:grass_block"
            # a base on land that didn't exist yet (region 1, 0): stone brick walls and floors

            def base_h(x, z):
                return (64 + (x + z) % 7) if x >= 512 else h(x, z)

            def base_b(x, y, z):
                return "minecraft:stone_bricks" if x >= 512 else b(x, y, z)
            new = self._server(tmp, "new", base_h, base_b, chunks,
                               extra={(1, 0): [(cx, cz) for cx in range(34, 37) for cz in range(0, 3)]})
            res = ch.compare(old, new, coarse_step=16)
            kinds = sorted((a["kind"], a["new_land"]) for a in res["areas"])
            self.assertIn(("build", False), kinds)
            self.assertIn(("map_art", False), kinds)
            self.assertIn(("build", True), kinds)
            self.assertEqual(len(kinds), 3)                                         # the tree is not a build
            house = next(a for a in res["areas"] if a["kind"] == "build" and not a["new_land"])
            self.assertTrue(house["area"][0] <= 8 and house["area"][2] >= 32 and house["area"][1] <= 8)
            self.assertGreaterEqual(house["blocks2"], 400)
            self.assertGreater(len(res["cells"]["minecraft:overworld"]), 0)
            self.assertEqual(ch.compare(old, old, coarse_step=16)["areas"], [])
            cache = os.path.join(tmp, "cache")
            first = ch.compare_cached(old, new, cache=cache, coarse_step=16)
            self.assertEqual(ch.compare_cached(old, new, cache=cache, coarse_step=16), json.loads(json.dumps(first)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_moments(self):
        from mcsm import changes as ch
        t0, t1 = 1_000_000, 9_000_000
        area = {"kind": "build", "area": [0, 0, 32, 32], "cells": 64, "blocks2": 1024, "volume": 8000, "y": 72,
                "new_land": False, "dim": "minecraft:overworld"}
        far = {**area, "area": [500, 500, 532, 532]}
        pairs = [{"from": t0, "to": t1, "areas": [area, far],
                  "cells": {"minecraft:overworld": [[x, z] for x in range(0, 32, 4) for z in range(0, 32, 4)]}}]
        tracks = {"Steve": [[t0 + 1000 * k, 510.0, 70.0, 510.0, "minecraft:overworld"] for k in range(120)]}
        work = [{"id": "w", "mode": "building", "area": [-4, -4, 36, 36], "dim": "minecraft:overworld",
                 "start": t0 + 5000, "score": 10.0, "why": ["building"]}]
        out = ch.change_moments(pairs, tracks, work)
        self.assertEqual(work[0]["confirmed"], 64 * 16)                             # the backups confirm the build
        self.assertEqual(work[0]["score"], 12.0)
        self.assertEqual(len(out), 1)                                               # the confirmed one is no new moment
        m = out[0]
        self.assertEqual((m["players"], m["mode"], m["area"]), (["Steve"], "changed", [500, 500, 532, 532]))
        self.assertEqual(m["start"], t0 + 119_000)                                  # the last time Steve was there
        self.assertIn("Steve", m["title"])
        lonely = ch.change_moments([{**pairs[0], "areas": [far]}], {}, [])
        self.assertTrue(lonely[0]["title"].startswith("Unknown builder"))
        self.assertEqual(lonely[0]["start"], t1)


class TestProgressAndCancel(unittest.TestCase):
    """The loading bar's progress, and Cancel stopping a job between files."""

    def test_job(self):
        from mcsm import progress as jobs
        jobs.report(1, 2, "nobody listening")                                   # no job: nothing happens
        seen = []
        job = jobs.Job(lambda d, t, txt: seen.append((d, t, txt)), min_interval=0)
        jobs.set_job(job)
        try:
            jobs.report(1, 4, "reading")
            self.assertEqual(seen, [(1, 4, "reading")])
            job.cancel()
            with self.assertRaises(jobs.Cancelled):
                jobs.report(2, 4)
            with self.assertRaises(jobs.Cancelled):
                jobs.check()
            try:                                                                # "skip a bad file" handlers don't swallow it
                try:
                    jobs.check()
                except Exception:                                               # noqa: BLE001
                    self.fail("Cancelled was caught as an Exception")
            except jobs.Cancelled:
                pass
        finally:
            jobs.set_job(None)

    def test_reading_recordings_reports(self):
        from mcsm import progress as jobs, replays
        tmp = tempfile.mkdtemp()
        try:
            rec = os.path.join(tmp, "recordings")
            os.makedirs(rec)
            for i in range(3):
                with zipfile.ZipFile(os.path.join(rec, f"r{i}.zip"), "w") as zf:
                    zf.writestr("metadata.json", json.dumps({"uuid": str(i), "total_ticks": 20, "chunks": {"c0.flashback": {"duration": 20}}}))
                    zf.writestr("c0.flashback", b"")
            seen = []
            jobs.set_job(jobs.Job(lambda d, t, txt: seen.append((d, t)), min_interval=0))
            try:
                replays.read_replays(rec)
            finally:
                jobs.set_job(None)
            self.assertIn((0, 3), seen)
            self.assertIn((2, 3), seen)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_gui_cancel(self):
        if not _have_display():
            self.skipTest("no display")
        from mcsm import gui
        root = _new_tk()
        try:
            app = gui.App(root)
            state = {}

            def work(_d):
                for i in range(1000):
                    print(f"step {i}")                                           # each line: a chance to stop
                    time.sleep(0.01)
                state["finished"] = True
            app._run("A long job", work, after=lambda _r: state.setdefault("after", True),
                     cancelled=lambda: state.setdefault("cancelled", True))
            deadline = time.time() + 5
            while time.time() < deadline and "step 3" not in app.log_text.get("1.0", "end"):
                root.update()
                time.sleep(0.02)
            self.assertTrue(app.job_frame.winfo_ismapped() or app.job_frame.grid_info())   # the bar shows
            app._cancel_job()
            deadline = time.time() + 5
            while app.busy and time.time() < deadline:
                root.update()
                time.sleep(0.02)
            self.assertFalse(app.busy)
            self.assertEqual(state, {"cancelled": True})
            self.assertIn("A long job: cancelled.", app.log_text.get("1.0", "end"))
            self.assertFalse(app.job_frame.grid_info())                          # the bar is gone
        finally:
            root.destroy()


class TestLoadCache(unittest.TestCase):
    """A loaded server is remembered between runs, unless something changed."""

    def test_saved_result(self):
        from contextlib import ExitStack
        from mcsm import loadcache
        tmp = tempfile.mkdtemp()
        try:
            srv = build_server(os.path.join(tmp, "srv"))
            cache = os.path.join(tmp, "cache")
            with ExitStack() as st:
                d1 = loadcache.load([srv], st, say=lambda _m: None, cache=cache)
            self.assertFalse(d1.loaded_from_cache)
            with ExitStack() as st:
                d2 = loadcache.load([srv], st, say=lambda _m: None, cache=cache)
            self.assertTrue(d2.loaded_from_cache)
            self.assertEqual((len(d2.events), sorted(d2.players)), (len(d1.events), sorted(d1.players)))
            with ExitStack() as st:                                              # other options: read again
                self.assertFalse(loadcache.load([srv], st, tz_spec="+02:00", say=lambda _m: None, cache=cache).loaded_from_cache)
            log = os.path.join(srv, "logs", "latest.log")                       # a new log line: read again
            with open(log, "a", encoding="utf-8") as fh:
                fh.write("[23:59:59] [Server thread/INFO]: Done\\n")
            os.utime(log, (time.time() + 5, time.time() + 5))
            with ExitStack() as st:
                self.assertFalse(loadcache.load([srv], st, say=lambda _m: None, cache=cache).loaded_from_cache)
            region = os.path.join(srv, "world", "region")                        # world map files don't count
            if os.path.isdir(region):
                with open(os.path.join(region, "r.9.9.mca"), "wb") as fh:
                    fh.write(b"\\0" * 16)
            with ExitStack() as st:
                self.assertTrue(loadcache.load([srv], st, say=lambda _m: None, cache=cache).loaded_from_cache)
            z = _zip_dir(srv, os.path.join(tmp, "srv.zip"))                       # a zip is never remembered
            self.assertIsNone(loadcache.fingerprint([z]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestExportFolder(unittest.TestCase):
    """Where a Flashback saves exported videos (its own setting), for the join step and the right-click menu."""

    def test_export_folder(self):
        from mcsm import flashback as fbm
        tmp = tempfile.mkdtemp()
        try:
            game = os.path.join(tmp, "minecraft")
            fb = os.path.join(game, "flashback")
            os.makedirs(os.path.join(game, "config", "flashback"))
            os.makedirs(fb)
            self.assertIsNone(fbm.export_folder(fb))                                  # no setting yet
            videos = os.path.join(tmp, "Cinematics")
            os.makedirs(videos)
            with open(os.path.join(game, "config", "flashback", "flashback.json"), "w", encoding="utf-8") as fh:
                json.dump({"internalExport": {"defaultExportPath": videos}}, fh)
            self.assertEqual(fbm.export_folder(fb), videos)
            shutil.rmtree(videos)
            self.assertIsNone(fbm.export_folder(fb))                                  # gone: not offered
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestDefaultTextLooks(unittest.TestCase):
    """New texts start with the owner's default look: for all videos, or this video's own."""

    def test_styles(self):
        from mcsm.videoeditor import VideoEditor
        tmp = tempfile.mkdtemp()
        old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = tmp
        try:
            def editor(video):
                ed = VideoEditor.__new__(VideoEditor)
                ed.video, ed.vinfo = video, {"matches": []}
                return ed
            a, b = editor(os.path.join(tmp, "a.mp4")), editor(os.path.join(tmp, "b.mp4"))
            self.assertEqual(a.styles(), {"all": {}, "video": {}})
            look = {"colour": "gold", "depth": 0.3, "animation": "fade", "start": 5, "lines": [["x", 1]]}
            a.set_style({"scope": "all", "preset": "title", "style": look})
            self.assertEqual(b.styles()["all"]["title"], {"colour": "gold", "depth": 0.3, "animation": "fade"})   # only the look
            a.set_style({"scope": "video", "preset": "title", "style": {"colour": "aqua"}})
            self.assertEqual(a.styles()["video"], {"title": {"colour": "aqua"}})
            self.assertEqual(b.styles()["video"], {})                                    # another video: its own
            a.save_project({"items": [{"id": 1}], "match": 0})                           # saving texts keeps the look
            self.assertEqual(a.styles()["video"], {"title": {"colour": "aqua"}})
            a.set_style({"scope": "video", "preset": "title", "style": None})
            self.assertEqual(a.styles()["video"], {})
            with self.assertRaises(ValueError):
                a.set_style({"scope": "all", "preset": "nope", "style": look})
        finally:
            if old is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old
            shutil.rmtree(tmp, ignore_errors=True)


class TestJoinTransitions(unittest.TestCase):
    """A transition at each cut when joining clips (ffmpeg xfade): lengths, per-cut choice, the sound."""

    def test_cuts(self):
        from mcsm import videojoin as vj
        self.assertEqual(vj._cuts(3, None, 0.6), [("cut", None, 0.0), ("cut", None, 0.0)])
        self.assertEqual(vj._cuts(3, "fade", 0.8), [("fade", "fade", 0.8), ("fade", "fade", 0.8)])
        self.assertEqual(vj._cuts(3, ["blocks"], 0.5), [("blocks", "pixelize", 0.5), ("cut", None, 0.0)])
        self.assertEqual(vj._cuts(2, "whip", 1.0), [("whip", "hblur", 0.3)])            # a whip is always quick
        with self.assertRaises(ValueError):
            vj._cuts(2, "explode", 0.5)
        args = cli.parse_args(["timelapse-join", "a.mp4", "b.mp4", "-o", "c.mp4", "--transitions", "fade,cut"])
        self.assertEqual((args.transitions, args.transition), ("fade,cut", None))

    def test_join_with_transitions(self):
        from mcsm import videojoin as vj
        from mcsm.videotext import find_tool, video_duration, has_audio
        ff = find_tool("ffmpeg")
        if not ff or not find_tool("ffprobe"):
            self.skipTest("ffmpeg not installed")
        tmp = tempfile.mkdtemp()
        try:
            def clip(name, secs, size="320x180"):
                out = os.path.join(tmp, name)
                subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30",
                                "-f", "lavfi", "-i", "sine=frequency=440", "-t", str(secs), "-c:v", "libx264",
                                "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out], check=True)
                return out
            a, b, c = clip("a.mp4", 2), clip("b.mp4", 2), clip("c.mp4", 1, size="640x360")
            out = os.path.join(tmp, "joined.mp4")
            p = vj.join([a, b, c], out, transitions=["dip_black", "cut"], length=0.5, say=lambda _m: None)
            self.assertFalse(p["copy"])
            self.assertAlmostEqual(p["seconds"], 4.5, places=2)                         # 2 + 2 + 1 - 0.5 overlap
            self.assertAlmostEqual(video_duration(out), 4.5, delta=0.15)
            self.assertTrue(has_audio(out))                                              # their sounds join too
            short = os.path.join(tmp, "short.mp4")                                       # longer than a clip: shortened
            p2 = vj.join([a, c], short, transitions="fade", length=3.0, say=lambda _m: None)
            self.assertLess(p2["cuts"][0][2], 1.0)
            self.assertAlmostEqual(video_duration(short), p2["seconds"], delta=0.15)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestTitleAnimations(unittest.TestCase):
    """Letters one by one, bounce, flip, shake, ways of leaving, colour gradients and the glow halo."""

    @staticmethod
    def _nbt(b, i=0, tag=None):
        """A tiny reader for the network NBT the app writes (just enough for text components)."""
        def s(i):
            n = struct.unpack_from(">H", b, i)[0]
            return b[i + 2:i + 2 + n].decode("utf-8"), i + 2 + n

        def val(t, i):
            if t == 8:
                return s(i)
            if t == 1:
                return b[i], i + 1
            if t == 10:
                out = {}
                while b[i] != 0:
                    tt = b[i]
                    name, i = s(i + 1)
                    out[name], i = val(tt, i)
                return out, i + 1
            if t == 9:
                et, n = b[i], struct.unpack_from(">i", b, i + 1)[0]
                i += 5
                items = []
                for _ in range(n):
                    v, i = val(et, i)
                    items.append(v)
                return items, i
            raise ValueError(t)
        return val(b[0], 1)[0]

    def test_gradient_component(self):
        from mcsm import worldtext as wt
        comp = self._nbt(wt.text_component("AB C", "gold", True, colour2="aqua"))
        self.assertEqual(comp["text"], "")
        self.assertEqual("".join(e["text"] for e in comp["extra"]), "AB C")
        cols = [e["color"] for e in comp["extra"]]
        self.assertEqual((cols[0], cols[-1]), ("#ffaa00", "#55ffff"))                  # gold ... aqua
        self.assertEqual(self._nbt(wt.text_component("A", "gold", colour2="aqua")), {"color": "gold", "text": "A"})

    def test_letters_split(self):
        from mcsm import worldtext as wt
        t = wt.WorldText("AB CD", 0, 64, 0, colour="gold", colour2="aqua", animation="letters-drop", appear_tick=10,
                         word_gap_ticks=3, camera_yaw=0, depth=0.5)
        parts = wt.split_words(t)
        self.assertEqual([p.text for p in parts], ["A", "B", "C", "D"])
        self.assertEqual([p.appear_tick for p in parts], [10, 13, 16, 19])
        self.assertEqual({p.animation for p in parts}, {"bounce"})
        self.assertEqual((parts[0].colour, parts[-1].colour), ("#ffaa00", "#55ffff"))
        self.assertEqual(len(wt.with_depth(parts[0])), 5)                               # 4 layers at most, + the letter
        xs = [p.x for p in parts]
        self.assertEqual(xs, sorted(xs, reverse=True))                                  # left to right as seen (camera's right is -x)
        words = wt.split_words(wt.WorldText("Big Fat", 0, 64, 0, colour="gold", colour2="aqua", animation="words", appear_tick=0))
        self.assertEqual([(w.text, w.colour2 != "") for w in words], [("Big", True), ("Fat", True)])

    def test_entrances_and_exits(self):
        from mcsm import worldtext as wt
        base = dict(x=0, y=64, z=0, appear_tick=100, disappear_tick=200, anim_ticks=10, fade_out_ticks=8)
        for anim in ("bounce", "flip", "shake"):
            e = wt._plan_entity(5, wt.WorldText("Hi", animation=anim, **base), 1, 99, 131)
            self.assertTrue(e.updates, anim)
            self.assertTrue(all(100 <= tick <= 200 for tick, _a, _b in e.updates), anim)
            self.assertGreater(e.settled, 100)
        flip = wt._plan_entity(5, wt.WorldText("Hi", animation="flip", **base), 1, 99, 131)
        self.assertIn(bytes([13]), flip.start[1])                                        # starts turned (lying flat)
        for exit_ in ("fade", "shrink", "drop", "rise"):
            e = wt._plan_entity(5, wt.WorldText("Hi", exit=exit_, **base), 1, 99, 131)
            leave = [tick for tick, _a, _b in e.updates if tick >= 192]
            self.assertTrue(leave, exit_)
        self.assertEqual(len(wt.with_halo(wt.WorldText("Hi", 0, 64, 0, halo=True))), 3)
        self.assertEqual(len(wt.with_halo(wt.WorldText("Hi", 0, 64, 0))), 1)

    def test_on_the_video(self):
        from mcsm import videotext as vt
        self.assertEqual(vt.pieces_of("Big Fat", "letters-fade"), 6)
        self.assertEqual(vt.pieces_of("Big Fat", "words"), 2)
        self.assertEqual(vt.pieces_of("Big Fat", "flip"), 1)
        t = vt.Title("AB", 1.0, 5.0, colour="gold", colour2="aqua", animation="letters-pop", word_gap=0.2,
                     halo=True, exit="drop", fade_out=0.5)
        ass = vt.titles_ass([t], 1280, 720)
        self.assertIn("\\1c&H00AAFF&", ass)                                                 # gold (BGR) on the first letter
        self.assertIn("\\1c&HFFFF55&", ass)                                                 # aqua on the last
        self.assertIn("\\blur", ass)                                                        # the halo
        self.assertIn("0:00:04.50,0:00:05.00", ass)                                         # the leaving part on its own
        self.assertIn("\\move(", ass)
        flip = vt.titles_ass([vt.Title("Hi", 0, 3, animation="flip", anim=0.5)], 1280, 720)
        self.assertIn("\\frx-90", flip)
        items = vt.resolve_times([vt._item({"kind": "screen", "lines": [["Hello there", 2]], "start": 0,
                                            "animation": "letters", "word_gap": 0.1, "anim": 0.2})], 10, lambda it: 0)
        self.assertAlmostEqual(vt.appear_time(items[0]), 9 * 0.1 + 0.2)


class TestSongSound(unittest.TestCase):
    """The song's sound settings: equaliser, volume, fades and where it stops, for Flashback and the made video."""

    def test_filters(self):
        from mcsm import videomusic as vm
        self.assertEqual(vm.song_filters({"file": "s.mp3"}), [])                       # as it is
        f = vm.song_filters({"start": 10, "until": 40, "volume": 1.5, "fade_in": 2, "fade_out": 3,
                             "bass": 4, "mid": -2, "treble": 99, "even": True})
        self.assertEqual(f, ["bass=g=4:f=110", "equalizer=f=1000:t=q:w=1:g=-2", "treble=g=15:f=4000",
                             "loudnorm=I=-16:TP=-1.5:LRA=11", "volume=1.500", "afade=t=in:st=0:d=2.000",
                             "atrim=end=30.000", "afade=t=out:st=27.000:d=3.000"])
        self.assertIsNone(vm.play_length({"start": 5}))
        _ins, chains, label = vm.audio_args({"file": "s.mp3", "start": 2, "volume": 0.5, "game_volume": 0.3}, 1, True)
        self.assertEqual(label, "mix")
        self.assertIn("volume=0.500", chains[0])
        self.assertIn("[0:a]volume=0.300[game]", chains)

    def test_baked_copy(self):
        from mcsm import videomusic as vm
        from mcsm.videotext import find_tool, video_duration
        ff = find_tool("ffmpeg")
        if not ff or not find_tool("ffprobe"):
            self.skipTest("ffmpeg not installed")
        tmp = tempfile.mkdtemp()
        try:
            song = os.path.join(tmp, "song.wav")
            subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=330", "-t", "6", song], check=True)
            plain = vm.song_copy(song, 0, tmp)
            self.assertTrue(plain.endswith(".wav"))                                     # nothing to change: a plain copy
            baked = vm.song_copy(song, 1.0, tmp, {"start": 0, "until": 3, "fade_out": 1, "bass": 6})
            self.assertTrue(baked.endswith(".ogg"))
            self.assertAlmostEqual(video_duration(baked), 3.0, delta=0.15)              # stops where it should
            self.assertNotEqual(baked, vm.song_copy(song, 1.0, tmp, {"start": 0, "until": 3, "fade_out": 1, "bass": 3}))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestAppWindow(unittest.TestCase):
    """The app's pages open in an Edge/Chrome app window with their own profile, else in the browser."""

    def test_open(self):
        from mcsm import appwindow
        cmd = appwindow.command("http://127.0.0.1:8790/", "edge.exe")
        self.assertEqual(cmd[:2], ["edge.exe", "--app=http://127.0.0.1:8790/"])
        self.assertIn("--disable-extensions", cmd)
        self.assertTrue(any(c.startswith("--user-data-dir=") and c.endswith("app-window") for c in cmd))
        opened, launched = [], []
        old = (appwindow.find_browser, appwindow.webbrowser.open, appwindow.subprocess.Popen, appwindow.enabled)
        try:
            appwindow.webbrowser.open = opened.append
            appwindow.subprocess.Popen = lambda c, **kw: launched.append(c)
            appwindow.enabled = lambda: True
            appwindow.find_browser = lambda: None
            old_ff = appwindow.find_firefox
            appwindow.find_firefox = lambda: None
            self.assertEqual(appwindow.open_page("http://x/"), "browser")                 # no Edge/Chrome/Firefox
            appwindow.find_firefox = old_ff
            appwindow.find_browser = lambda: "chrome"
            self.assertEqual(appwindow.open_page("http://y/"), "window")
            appwindow.enabled = lambda: False
            self.assertEqual(appwindow.open_page("http://z/"), "browser")                 # switched off
            self.assertEqual((opened, [c[1] for c in launched]), (["http://x/", "http://z/"], ["--app=http://y/"]))
        finally:
            appwindow.find_browser, appwindow.webbrowser.open, appwindow.subprocess.Popen, appwindow.enabled = old


class TestLinkInstances(unittest.TestCase):
    """The link-mod window only offers instances it can run in, and says where it's installed already."""

    def test_supports_and_installed(self):
        from mcsm import flashlink
        self.assertIs(flashlink.supports("1.21.11"), False)
        self.assertIs(flashlink.supports("26.1.2"), True)
        self.assertIs(flashlink.supports("27.0"), True)
        self.assertIsNone(flashlink.supports(None))
        tmp = tempfile.mkdtemp()
        try:
            self.assertIsNone(flashlink.installed_version(tmp))
            open(os.path.join(tmp, "mcsm-link-0.2.0.jar"), "wb").close()
            self.assertEqual(flashlink.installed_version(tmp), "0.2.0")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestCopyNames(unittest.TestCase):
    """Copies are findable in Flashback's list even when the recording never got a name."""

    def test_names(self):
        from mcsm.flashback import copy_name
        self.assertEqual(copy_name({"name": "Unnamed"}, "C:/r/2026-05-24--18-49-45.zip", " (text)", "Meeting Room"),
                         "Meeting Room – 2026-05-24--18-49-45 (text)")
        self.assertEqual(copy_name({"name": "Big build"}, "x.zip", " (timelapse 1 of 9)"), "Big build (timelapse 1 of 9)")
        self.assertEqual(copy_name({}, "C:/r/2026-04-10--16-02-16.zip", " (mcsm)"), "2026-04-10--16-02-16 (mcsm)")
        self.assertEqual(copy_name({"name": "Meeting Room 2"}, "a.zip", " (text)", "meeting room"), "Meeting Room 2 (text)")


class TestPutBack(unittest.TestCase):
    """An edit's recording copied back into Flashback's replays folder (checked; the original stays)."""

    def test_put_back(self):
        from mcsm import flashback as fbm
        tmp = tempfile.mkdtemp()
        try:
            fb = os.path.join(tmp, "flashback")
            os.makedirs(os.path.join(fb, "replays"))
            lib = os.path.join(tmp, "library")
            os.makedirs(lib)

            def rec(path, uid):
                with zipfile.ZipFile(path, "w") as z:
                    z.writestr("metadata.json", json.dumps({"uuid": uid}))
                return path
            src = rec(os.path.join(lib, "2026-05-24--18-49-45.zip"), "aaa")
            self.assertFalse(fbm.in_replays(src, fb))
            dst = fbm.put_back(src, fb)
            self.assertEqual(dst, os.path.join(fb, "replays", "2026-05-24--18-49-45.zip"))
            self.assertTrue(fbm.in_replays(dst, fb) and os.path.isfile(src))           # the original stays
            self.assertEqual(fbm.put_back(src, fb), dst)                               # already there
            other = rec(os.path.join(tmp, "2026-05-24--18-49-45.zip"), "bbb")          # same name, another recording
            self.assertTrue(fbm.put_back(other, fb).endswith("2026-05-24--18-49-45 (2).zip"))
            with self.assertRaises(ValueError):
                fbm.put_back(os.path.join(lib, "gone.zip"), fb)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestEditBackups(unittest.TestCase):
    """Versions of Flashback edits: only when changed, kept, restorable (and the restore can be undone)."""

    def test_versions_and_restore(self):
        from mcsm import editbackup as eb
        tmp = tempfile.mkdtemp()
        try:
            fb = os.path.join(tmp, "inst", "minecraft", "flashback")
            os.makedirs(os.path.join(fb, "editor_states"))
            st = os.path.join(fb, "editor_states", "abc.json")
            root = os.path.join(tmp, "backups")

            def write(keys):
                with open(st, "w", encoding="utf-8") as fh:
                    json.dump({"scenes": [{"name": "Meeting Room", "keyframeTracks": [
                        {"keyframeType": "CAMERA", "keyframesByTick": {str(k): {} for k in range(keys)}}]}]}, fh)
            write(55)
            self.assertIsNotNone(eb.snapshot(st, "first", root))
            self.assertIsNone(eb.snapshot(st, "again", root))                          # unchanged: no new version
            write(3)                                                                    # oops: most keyframes gone
            os.utime(st, (time.time() + 5, time.time() + 5))
            self.assertIsNotNone(eb.snapshot(st, "after", root))
            vs = eb.versions(st, root)
            self.assertEqual([v["keyframes"] for v in vs], [3, 55])
            self.assertEqual(vs[0]["scenes"], ["Meeting Room"])
            eb.restore(st, vs[1]["path"], root)
            with open(st, encoding="utf-8") as fh:
                self.assertEqual(len(json.load(fh)["scenes"][0]["keyframeTracks"][0]["keyframesByTick"]), 55)
            self.assertEqual(eb.versions(st, root)[0]["keyframes"], 3)                 # the replaced one is kept
            with self.assertRaises(ValueError):
                eb.restore(st, os.path.join(tmp, "other.json"), root)
            r = eb.snapshot_all([fb], "start", root)
            self.assertEqual(r["checked"], 1)
            for k in range(eb.KEEP_LAST + 5):                                           # pruning keeps the last 30
                write(100 + k)
                os.utime(st, (time.time() + 10 + k, time.time() + 10 + k))
                eb.snapshot(st, "", root)
            self.assertLessEqual(len(eb.versions(st, root)), eb.KEEP_LAST + 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestCopyRegistry(unittest.TestCase):
    """The list of the app's own copies in each Flashback (the add-on will only change those)."""

    def test_registry(self):
        from mcsm import flashback as fbm, fbpanel
        tmp = tempfile.mkdtemp()
        try:
            fb = os.path.join(tmp, "flashback")
            os.makedirs(os.path.join(fb, "replays"))

            def rec(name, meta):
                with zipfile.ZipFile(os.path.join(fb, "replays", name), "w") as z:
                    z.writestr("metadata.json", json.dumps(meta))
            rec("2026-05-24--18-49-45.zip", {"uuid": "orig"})
            rec("2026-05-24--18-49-45 (text).zip", {"uuid": "copy1", "name": "Renamed in Flashback"})   # mark dropped
            rec("x (timelapse 2 of 9).zip", {"uuid": "tl2"})
            rec("y.zip", {"uuid": "copy3", "original_uuid_mcsm": "orig"})
            self.assertEqual(fbm.register_existing(fb), 3)
            reg = fbm.registry(fb)["copies"]
            self.assertEqual(set(reg), {"copy1", "tl2", "copy3"})
            self.assertFalse(fbm.is_app_copy(fb, "orig"))
            self.assertEqual(reg["tl2"]["kind"], "timelapse")
            self.assertEqual(fbm.register_existing(fb), 0)
            self.assertEqual(fbpanel._stem("2026-05-24--18-49-45 (text).zip"), "2026-05-24--18-49-45")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestRealBlockTiles(unittest.TestCase):
    """The history viewer's real blocks close up: 128 x 128 pieces, built once, fitting together."""

    def test_tiles(self):
        from mcsm import blockmesh as bm, terrain, world3d
        tmp = tempfile.mkdtemp()
        try:
            world = os.path.join(tmp, "srv", "world")
            chunks = {(cx, cz): terrain.make_chunk(cx, cz, lambda x, z: 64 + (x // 20) % 3, lambda x, y, z: "minecraft:stone")
                      for cx in range(10) for cz in range(2)}
            terrain.write_region(os.path.join(world, "region", "r.0.0.mca"), chunks)
            jar = os.path.join(tmp, "client.jar")
            TestRealBlocks._jar(jar)
            old = bm.find_client_jar
            bm.find_client_jar = lambda _v: jar
            try:
                bw = world3d.BuildWorld(os.path.join(tmp, "srv"), world, mc="26.1.2", cache=os.path.join(tmp, "cache"))
                a = bw.block_tile(0, 0, "minecraft:overworld", "/blocktile/0/")
                b = bw.block_tile(1, 0, "minecraft:overworld", "/blocktile/0/")
                empty = bw.block_tile(5, 5, "minecraft:overworld", "/blocktile/0/")
            finally:
                bm.find_client_jar = old
            self.assertGreater(a["faces"], 0)
            self.assertGreater(b["faces"], 0)
            self.assertEqual((a["origin"], b["origin"]), ([0, 0], [128, 0]))
            self.assertEqual(empty["faces"], 0)
            key = a["url"].rsplit("/", 1)[1][:-4]
            with open(bw.block_tile_file(key), "rb") as fh:
                faces = [bm.RECORD.unpack_from(fh.read(), 0)]
            self.assertTrue(faces)
            # pieces side by side: no wall where they meet (a block's west face at x = 128 would be hidden)
            with open(bw.block_tile_file(b["url"].rsplit("/", 1)[1][:-4]), "rb") as fh:
                data = fh.read()
            west_edge = [f for f in (bm.RECORD.unpack_from(data, i) for i in range(0, len(data), bm.RECORD.size))
                         if f[0] == 0 and f[3] == 4 and f[1] <= 63]
            self.assertEqual(west_edge, [])
            self.assertIsNone(bw.block_tile_file("../evil"))
            again = bw.block_tile(0, 0, "minecraft:overworld", "/blocktile/0/")      # from the cache
            self.assertEqual(again["faces"], a["faces"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_version_targets(self):
        from mcsm import flashback as fbm
        old = fbm.flashback_folders
        try:
            fbm.flashback_folders = lambda: [{"path": "C:/fb26", "name": "Flashback 26.1.2", "mc": "26.1.2"},
                                             {"path": "C:/fb121", "name": "1.21.11", "mc": "1.21.11"}]
            t = fbm.version_targets(["1.21.5", "26.1.2", "1.21.11", None, "26.1.2"])
            self.assertEqual([(x["mc"], x["name"]) for x in t], [("26.1.2", "Flashback 26.1.2"), ("1.21.11", "1.21.11"),
                                                                ("1.21.5", None)])
            self.assertIn("1.21.5 \u2192 no Flashback", fbm.describe_targets(t))
        finally:
            fbm.flashback_folders = old


class TestMoods(unittest.TestCase):
    """Celebrations and sad moments: tags on deaths and advancements, farewells, anniversaries, statistics."""

    def _data(self):
        from datetime import timedelta
        t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        h = lambda x: t0 + timedelta(hours=x)                                          # noqa: E731
        d = m.ServerData(root="x")
        for n in ("Steve", "Alex", "Bob", "Old"):
            d.players[n.lower()] = m.PlayerProfile(None, n)
        d.log_files = [m.LogFileInfo("a.log", "2026-01-01", "filename", 10, t0, h(24 * 150))]
        ev = []
        for day in range(12):                                                         # Steve plays 12 sessions of 1 hour
            d.sessions.append(m.Session("Steve", h(day * 24), h(day * 24 + 1)))
        d.sessions.append(m.Session("Old", h(0), h(6)))                                # Old: 6 hours, then gone for good
        d.sessions.append(m.Session("Alex", h(24 * 149), h(24 * 149 + 1)))
        ev.append(m.Event(h(11 * 24 + 0.5), m.EV_DEATH, "Steve", "Steve tried to swim in lava", data={"cause": "tried to swim in lava"}))
        ev.append(m.Event(h(11 * 24 + 0.5) + timedelta(seconds=20), m.EV_LEAVE, "Steve", "Steve left the game"))
        for k in range(3):                                                            # Bob dies 3 times in 6 minutes
            ev.append(m.Event(h(5) + timedelta(minutes=2 * k), m.EV_DEATH, "Bob", "Bob was slain by Zombie",
                              data={"cause": "was slain by Zombie", "killer": "Zombie"}))
        ev.append(m.Event(h(2), m.EV_ADVANCEMENT, "Alex", "Alex has made the advancement [Best Friends Forever]",
                          data={"title": "Best Friends Forever", "kind": "task"}))
        ev.append(m.Event(h(3), m.EV_ADVANCEMENT, "Alex", "Alex has made the advancement [We Need to Go Deeper]",
                          data={"title": "We Need to Go Deeper", "kind": "task"}))
        ev.append(m.Event(h(3) + timedelta(seconds=10), m.EV_CHAT, "Steve", "GG!!!"))
        ev.append(m.Event(h(24 * 149 + 0.5), m.EV_JOIN, "Alex", "Alex joined the game"))
        d.events = ev
        return d, h

    def test_tags(self):
        from mcsm import moments, moods
        d, h = self._data()
        ms = moments.find_moments(d)
        tags = {t for x in ms for t in (x.get("tags") or [])}
        self.assertTrue({"long_life", "lossy", "rage_quit", "death_streak", "pet", "server_first", "farewell"} <= tags, tags)
        steve = next(x for x in ms if "long_life" in (x.get("tags") or []))
        self.assertEqual(steve["mood"], "sad")
        self.assertIn("hours alive", steve["title"])
        nether = next(x for x in ms if "server_first" in (x.get("tags") or []))
        self.assertEqual(nether["mood"], "celebration")
        self.assertIn("first trip to the Nether", nether["title"])
        bye = next(x for x in ms if "farewell" in (x.get("tags") or []))
        self.assertEqual(bye["players"], ["Old"])
        self.assertEqual(moods.chat_mood("GG!!!"), "cheer")
        self.assertEqual(moods.chat_mood("noooooo"), "mourn")
        self.assertEqual(moods.chat_mood("rip bozo"), "mourn")
        self.assertIsNone(moods.chat_mood("going to the mine"))

    def test_statistics(self):
        from datetime import timedelta
        from mcsm import moods
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = []
        for i in range(6):
            rows.append({"backup": f"b{i}", "time": t0 + timedelta(days=30 * i), "play_ticks": 72000 * 10 * i,
                         "blocks_mined": 1000 * i + (20000 if i == 5 else 0), "traded": 0 if i < 3 else 4,
                         "fly_cm": 0, "mob_kills": 0, "walk_cm": 0, "items_crafted": 0})
        d = m.ServerData(root="x")
        d.player_history = {"Steve": rows}
        out = moods.stat_moments(d, lambda n: n)
        titles = [x["title"] for x in out]
        self.assertIn("Steve: first villager trade", titles)
        self.assertTrue(any("most blocks mined ever" in t and "record" in t for t in titles), titles)
        self.assertTrue(all(x["approx"] and not x["recordings"] for x in out))

    def test_biomes(self):
        """Biome finds from the dates the game keeps for Adventuring Time: server firsts (not in the opening days,
        unless rare) and each player's first time in a rare biome."""
        from datetime import timedelta
        from mcsm import moods
        t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        d = m.ServerData(root="x")
        for n in ("Steve", "Alex"):
            d.players[n.lower()] = m.PlayerProfile(None, n)
        d.players["steve"].advancements = {"adventure/adventuring_time": {"done": False, "completed": None, "criteria": {
            "minecraft:plains": t0, "minecraft:cherry_grove": t0 + timedelta(hours=1),
            "minecraft:taiga": t0 + timedelta(days=10)}}}
        d.players["alex"].advancements = {
            "adventure/adventuring_time": {"done": False, "completed": None, "criteria": {
                "minecraft:plains": t0 + timedelta(minutes=5), "minecraft:cherry_grove": t0 + timedelta(days=20)}},
            "nether/explore_nether": {"done": False, "completed": None, "criteria": {
                "minecraft:warped_forest": t0 + timedelta(days=30)}}}
        ev = moods.biome_events(d, lambda n: n)
        labels = [e["label"] for e in ev]
        self.assertEqual(labels, [
            "Steve is the first on the server to find Cherry Grove",          # rare: counts even on the first day
            "Steve is the first on the server to find Taiga",                 # after the opening days
            "Alex finds Cherry Grove for the first time",                    # rare: a personal first too
            "Alex is the first on the server to find Warped Forest"])
        self.assertTrue(all(e["mood"] == "celebration" and "biome" in e["tags"] for e in ev))   # plains: spawn, not listed
        self.assertEqual(moods.biome_name("minecraft:old_growth_birch_forest"), "Old Growth Birch Forest")

    def test_death_is_not_a_celebration(self):
        from mcsm import moods
        group = [{"kind": "death", "points": 5.0, "player": "Bob"},
                 {"kind": "biome", "points": 2.0, "player": "Bob", "mood": "celebration"}]
        self.assertIsNone(moods.group_mood(group))
        group[0]["points"] = 1.0
        self.assertEqual(moods.group_mood(group), "celebration")


def _class_file(name: str, methods: dict) -> bytes:
    """A minimal Java class file: *methods* maps (name, descriptor) to (max_locals, code built by a function that
    takes the constant-pool helper ``cp`` with .utf/.cls/.str/.flt/.mref/.fref and returns bytes)."""
    import struct
    pool, index = [], {}

    def add(key, data, slots=1):
        if key not in index:
            index[key] = len(pool) + 1 + sum(1 for k in pool if k[1] == 2)
            pool.append((data, slots))
        return index[key]

    class CP:
        def utf(self, v):
            b = v.encode()
            return add(("u", v), b"\x01" + struct.pack(">H", len(b)) + b)

        def cls(self, v):
            return add(("c", v), b"\x07" + struct.pack(">H", self.utf(v)))

        def str(self, v):
            return add(("s", v), b"\x08" + struct.pack(">H", self.utf(v)))

        def flt(self, v):
            return add(("f", v), b"\x04" + struct.pack(">f", v))

        def nat(self, n, d):
            return add(("n", n, d), b"\x0c" + struct.pack(">HH", self.utf(n), self.utf(d)))

        def mref(self, c, n, d):
            return add(("m", c, n, d), bytes([10]) + struct.pack(">HH", self.cls(c), self.nat(n, d)))

        def fref(self, c, n, d):
            return add(("F", c, n, d), b"\x09" + struct.pack(">HH", self.cls(c), self.nat(n, d)))

    cp = CP()
    this = cp.cls(name)
    sup = cp.cls("java/lang/Object")
    code_name = cp.utf("Code")
    body = b""
    for (mn, md), (locals_, build) in methods.items():
        code = build(cp)
        attr = struct.pack(">HHI", 10, locals_, len(code)) + code + struct.pack(">HH", 0, 0)
        body += struct.pack(">HHHH", 0x0009, cp.utf(mn), cp.utf(md), 1) + struct.pack(">HI", code_name, len(attr)) + attr
    head = b"\xca\xfe\xba\xbe" + struct.pack(">HHH", 0, 65, len(pool) + 1) + b"".join(d for d, _s in pool)
    return head + struct.pack(">HHHHH", 0x21, this, sup, 0, 0) + struct.pack(">H", len(methods)) + body + struct.pack(">H", 0)


def _chunk_packet(cx: int, cz: int, ids: dict, two_counts: bool = True, mask_bytes: bool = True) -> bytes:
    """A level_chunk_with_light packet (26.3 layout by default): stone up to y 63, grass at y 64, air above;
    one biome; sky light 15 in the grass section."""
    import struct
    sec_count = struct.pack(">hh", 4096, 0) if two_counts else struct.pack(">h", 4096)
    data = b""
    for s in range(24):
        if s < 8:
            data += sec_count + bytes([0]) + _vi(ids["stone"])
        elif s == 8:                               # palette [air, grass], 4 bits: grass on the bottom layer
            longs = [0x1111111111111111 if i < 16 else 0 for i in range(256)]
            data += sec_count + bytes([4]) + _vi(2) + _vi(ids["air"]) + _vi(ids["grass"]) + b"".join(struct.pack(">Q", v) for v in longs)
        else:
            data += sec_count + bytes([0]) + _vi(ids["air"])
        data += bytes([0]) + _vi(0)                # biomes: one
    top = 64 - (-64) + 1                           # heightmap value: the top block + 1, above the bottom
    hm = [sum(top << (9 * k) for k in range(7)) for _ in range(37)]
    p = _vi(46) + struct.pack(">ii", cx, cz) + _vi(1) + _vi(4) + _vi(37) + b"".join(struct.pack(">Q", v) for v in hm)
    p += _vi(len(data)) + data + _vi(0)            # no block entities
    sky_bit = 1 << (8 + 1)                         # section 8 (bit 0 is the one below the lowest)
    if mask_bytes:
        p += _vi(2) + sky_bit.to_bytes(2, "little") + _vi(0) + _vi(0) + _vi(0)
    else:
        p += _vi(1) + struct.pack(">Q", sky_bit) + _vi(0) + _vi(0) + _vi(0)
    p += _vi(1) + _vi(2048) + bytes([0xFF]) * 2048 + _vi(0)
    return p


class TestLedger(unittest.TestCase):
    """The Ledger mod's block log: the world at a moment from a backup with the changes undone or replayed."""

    def _db(self, path):
        con = sqlite3.connect(path)
        con.executescript("""
            create table ActionIdentifiers (id integer primary key, action_identifier text);
            create table worlds (id integer primary key, identifier text);
            create table ObjectIdentifiers (id integer primary key, identifier text);
            create table actions (id integer primary key, action_id int, time text, x int, y int, z int, world_id int,
              object_id int, old_object_id int, block_state text, old_block_state text, source int, player_id int,
              extra_data text, rolled_back boolean);
            insert into ActionIdentifiers values (1, 'block-break'), (2, 'block-place'), (3, 'block-change'), (8, 'entity-kill');
            insert into worlds values (1, 'minecraft:overworld');
            insert into ObjectIdentifiers values (1, 'minecraft:air'), (2, 'minecraft:oak_stairs'), (3, 'minecraft:stone');
        """)
        rows = [   # (action, time, x, y, z, object, old object, state, old state, rolled back)
            (2, "2026-03-01 10:00:00.000", 5, 70, 5, 2, 1, '{facing:"east",half:"bottom"}', None, 0),   # stairs placed
            (1, "2026-03-01 11:00:00.000", 5, 70, 5, 1, 2, None, '{facing:"east",half:"bottom"}', 0),   # broken again
            (2, "2026-03-01 10:30:00.000", 6, 64, 6, 3, 1, None, None, 0),                              # stone placed
            (2, "2026-03-01 10:40:00.000", 7, 64, 7, 3, 1, None, None, 1),                              # rolled back
            (8, "2026-03-01 10:45:00.000", 8, 64, 8, 3, 1, None, None, 0),                              # not a block
        ]
        con.executemany("insert into actions (action_id, time, x, y, z, world_id, object_id, old_object_id, block_state,"
                        " old_block_state, source, rolled_back) values (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 1, ?)", rows)
        con.commit()
        con.close()

    def test_search(self):
        """Dev mode's search: recorded events as text lines (messages translated, repeats within 10 s dropped, noisy
        kinds left out, block events with their place), every word must match, the Ledger searched by action, block,
        source and player."""
        from datetime import datetime, timezone
        from mcsm import ledger, search
        lang = {"sleep.skipping_night": "Sleeping through this night", "death.attack.player": "%1$s was slain by %2$s"}
        self.assertEqual(search.component_plain({"translate": "death.attack.player", "with": ["Alex", {"text": "Steve"}]}, lang),
                         "Alex was slain by Steve")
        self.assertEqual(search.component_plain({"text": "a", "extra": ["§cb", {"text": "c"}]}), "abc")
        evs = [[0, None, "ab", {"translate": "sleep.skipping_night"}], [20, None, "ab", {"translate": "sleep.skipping_night"}],
               [40, None, "f", 2], [60, "#wolf#5", "ang", 400], [80, None, "bb", [1, 64, -2, "diamond_ore"]],
               [90, None, ["eq", "mainhand"][0], ["mainhand", "diamond_axe", 1]], [100, "#zombie#9", "ee", 60]]
        rows = search.index_events(evs, lambda t: 1000 + t * 50, {}, "Alex", lang)
        self.assertEqual([r[3] for r in rows], ["action bar: Sleeping through this night", "angry (400 ticks)",
                                                "block broken: diamond_ore", "holds (mainhand) diamond_axe",
                                                "body vanished after dying (poof)"])
        self.assertEqual(rows[0][:3], [1000, "Alex", "ab"])
        self.assertEqual(rows[2][4:], [1, 64, -2])                       # the place of a block event
        self.assertEqual(search.who_text("#wolf#5"), "wolf #5")
        self.assertTrue(search.matches("wolf #5 angry (400 ticks)", search.terms_of("Wolf  ANGRY")))
        self.assertFalse(search.matches("wolf #5 calm", ["wolf", "angry"]))
        self.assertEqual(search.exact_version(".exact29.json.gz"), 29)
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "ledger.sqlite")
            self._db(p)
            con = sqlite3.connect(p)
            con.execute("create table sources (id integer primary key, name text)")
            con.execute("insert into sources values (1, 'player'), (2, 'creeper')")
            con.execute("create table players (id integer primary key, player_id blob, player_name text, first_join text, last_join text)")
            con.execute("insert into players values (1, x'00', 'Alex', '', '')")
            con.execute("update actions set player_id = 1 where x = 5")
            con.commit()
            con.close()
            lg = ledger.Ledger(p)
            hits = ledger_hits = search.ledger_search(lg, ["stairs", "alex"])
            self.assertEqual([(h["text"], h["who"], h["x"]) for h in hits],
                             [("block-break air (was oak_stairs)", "Alex", 5), ("block-place oak_stairs", "Alex", 5)])
            self.assertEqual(hits[0]["t"], int(datetime(2026, 3, 1, 11, tzinfo=timezone.utc).timestamp() * 1000))
            self.assertEqual(search.ledger_search(lg, ["stone", "alex"]), [])  # the stone wasn't Alex's
            self.assertEqual(search.ledger_search(lg, ["nothing_like_this"]), [])
            del ledger_hits
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_moment(self):
        from datetime import datetime, timezone
        from mcsm import ledger
        ms = lambda *a: int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmp, "world"))
            p = os.path.join(tmp, "world", "ledger.sqlite")
            self._db(p)
            self.assertEqual(ledger.find(tmp, "world"), p)
            lg = ledger.Ledger(p)
            self.assertEqual((lg.first, lg.last), (ms(2026, 3, 1, 10), ms(2026, 3, 1, 11)))
            rows = lg.changes("minecraft:overworld", 0, 0, 15, 15, ms(2026, 3, 1), ms(2026, 3, 2))
            self.assertEqual([r[1:4] for r in rows], [(5, 70, 5), (6, 64, 6), (5, 70, 5)])   # in time order, no rollback
            self.assertEqual(rows[0][4], "minecraft:oak_stairs[facing=east,half=bottom]")
            backup = ms(2026, 3, 2)                       # a backup after it all: stairs gone, stone there
            at_1015 = lg.patches("minecraft:overworld", {(0, 0)}, ms(2026, 3, 1, 10, 15), backup)
            self.assertEqual(at_1015, {(0, 0): {(5, 70, 5): "minecraft:oak_stairs[facing=east,half=bottom]",
                                                (6, 64, 6): "minecraft:air"}})
            at_0900 = lg.patches("minecraft:overworld", {(0, 0)}, ms(2026, 3, 1, 9), backup)
            self.assertEqual(at_0900[(0, 0)][(5, 70, 5)], "minecraft:air")   # before the stairs were placed
            old_backup = ms(2026, 2, 1)                   # a backup before it all: replayed forward
            fwd = lg.patches("minecraft:overworld", {(0, 0)}, ms(2026, 3, 1, 10, 15), old_backup)
            self.assertEqual(fwd[(0, 0)][(5, 70, 5)], "minecraft:oak_stairs[facing=east,half=bottom]")
            self.assertNotIn((6, 64, 6), fwd[(0, 0)])     # placed later
            self.assertEqual(lg.patches("minecraft:overworld", {(9, 9)}, ms(2026, 3, 1, 10, 15), backup), {})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_builds(self):
        """Who built what: each player's placed blocks grouped into builds; farming isn't building."""
        from datetime import datetime, timedelta, timezone
        from mcsm import ledger, review
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "ledger.sqlite")
            self._db(p)
            con = sqlite3.connect(p)
            con.execute("create table players (id integer primary key, player_id blob, player_name text, first_join text, last_join text)")
            con.execute("insert into players values (1, x'00', 'Alex', '', ''), (2, x'01', 'Steve', '', '')")
            con.execute("insert into ObjectIdentifiers values (4, 'minecraft:oak_planks'), (5, 'minecraft:wheat')")
            t0 = datetime(2026, 3, 2, 18, tzinfo=timezone.utc)
            rows = []
            for i in range(300):                         # Alex builds for 20 minutes at (100, 100)
                t = t0 + timedelta(seconds=i * 4)
                rows.append((2, t.strftime("%Y-%m-%d %H:%M:%S.000"), 100 + i % 10, 64 + i // 100, 100 + (i // 10) % 10, 4, 1, 1))
            for i in range(400):                         # Steve replants a wheat farm: not a build
                t = t0 + timedelta(seconds=i * 3)
                rows.append((2, t.strftime("%Y-%m-%d %H:%M:%S.000"), 500 + i % 20, 64, 500 + i // 20, 5, 1, 2))
            con.executemany("insert into actions (action_id, time, x, y, z, world_id, object_id, old_object_id, source,"
                            " player_id, rolled_back) values (?, ?, ?, ?, ?, 1, ?, ?, 1, ?, 0)", rows)
            con.commit()
            con.close()
            builds = ledger.find_builds(ledger.Ledger(p), cache_file=os.path.join(tmp, "builds.json"))
            self.assertEqual(len(builds), 1)
            b = builds[0]
            self.assertEqual((b["player"], b["kind"], b["placed"], b["top"]), ("Alex", "building", 300, [["oak_planks", 300]]))
            self.assertEqual(b["dim"], "minecraft:overworld")
            self.assertTrue(95 <= b["centre"][0] <= 110 and 95 <= b["centre"][2] <= 110)
            self.assertEqual(ledger.find_builds(ledger.Ledger(p), cache_file=os.path.join(tmp, "builds.json")), builds)
            # the year in review groups builds by place (two evenings on one house = one project)
            later = {**b, "start": b["start"] + 86_400_000, "end": b["end"] + 86_400_000, "player": "Steve"}
            proj = review.projects([b, later], 0, 10 ** 13, lambda n: n)
            self.assertEqual(len(proj), 1)
            self.assertEqual((proj[0]["placed"], proj[0]["players"], proj[0]["sessions"]), (600, {"Alex": 300, "Steve": 300}, 2))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_explosions(self):
        """Blasts from the blocks Ledger saw them break: one cause, within a second and 8 blocks, is one blast."""
        from datetime import datetime, timedelta, timezone
        from mcsm import ledger
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "ledger.sqlite")
            self._db(p)
            con = sqlite3.connect(p)
            con.execute("create table sources (id integer primary key, name text)")
            con.execute("insert into sources values (1, 'player'), (2, 'creeper'), (3, 'tnt')")
            con.execute("delete from actions")
            t0 = datetime(2026, 3, 2, 18, tzinfo=timezone.utc)
            ts = lambda s: (t0 + timedelta(seconds=s)).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            rows = [(1, ts(0.1 * i), 10 + i % 3, 64, 10 + i // 3, 2) for i in range(9)]     # a creeper: 9 blocks
            rows += [(1, ts(30), 100, 70, 100, 3), (1, ts(30.2), 102, 70, 101, 3)]            # TNT, half a minute on
            rows += [(1, ts(60), 50, 64, 50, 1)]                                              # a player: no blast
            con.executemany("insert into actions (action_id, time, x, y, z, world_id, object_id, old_object_id, source)"
                            " values (?, ?, ?, ?, ?, 1, 1, 3, ?)", rows)
            con.commit(); con.close()
            lg = ledger.Ledger(p)
            got = ledger.explosions(lg, "minecraft:overworld", int(t0.timestamp() * 1000) - 1000, int(t0.timestamp() * 1000) + 120_000)
            self.assertEqual([(e["cause"], e["blocks"], e["x"], e["z"]) for e in got], [("creeper", 9, 11.0, 11.0), ("tnt", 2, 101.0, 100.5)])
            self.assertEqual(got[0]["t"], int(t0.timestamp() * 1000))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_incidents(self):
        """Builds damaged: placed blocks lost to a creeper or another player; crops harvested don't count."""
        from datetime import datetime, timedelta, timezone
        from mcsm import ledger
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "ledger.sqlite")
            self._db(p)
            con = sqlite3.connect(p)
            con.execute("create table players (id integer primary key, player_id blob, player_name text, first_join text, last_join text)")
            con.execute("insert into players values (1, x'00', 'Alex', '', ''), (2, x'01', 'Steve', '', '')")
            con.execute("create table sources (id integer primary key, name text)")
            con.execute("insert into sources values (1, 'player'), (2, 'creeper')")
            con.execute("insert into ObjectIdentifiers values (4, 'minecraft:oak_planks'), (5, 'minecraft:wheat')")
            con.execute("delete from actions")
            t0 = datetime(2026, 3, 2, 18, tzinfo=timezone.utc)
            ts = lambda s: (t0 + timedelta(seconds=s)).strftime("%Y-%m-%d %H:%M:%S.000")
            rows = []                                      # (action, time, x, y, z, object, old, source, player)
            for i in range(10):                            # Alex builds a wall and plants wheat
                rows.append((2, ts(i), 10 + i, 64, 10, 4, 1, 1, 1))
                rows.append((2, ts(i), 50 + i, 64, 50, 5, 1, 1, 1))
            for i in range(6):                             # a creeper chasing Alex blows up six of the planks
                rows.append((1, ts(100 + i), 10 + i, 64, 10, 1, 4, 2, 1))
            for i in range(10):                            # Steve harvests Alex's wheat: not damage
                rows.append((1, ts(200 + i), 50 + i, 64, 50, 1, 5, 1, 2))
            con.executemany("insert into actions (action_id, time, x, y, z, world_id, object_id, old_object_id, source,"
                            " player_id, rolled_back) values (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 0)", rows)
            con.commit()
            con.close()
            inc = ledger.find_incidents(ledger.Ledger(p))
            self.assertEqual(len(inc), 1)
            i = inc[0]
            self.assertEqual((i["kind"], i["cause"], i["near"], i["blocks"], i["owners"]),
                             ("explosion", "a creeper", "Alex", 6, {"Alex": 6}))
            self.assertEqual(i["top"], [["oak_planks", 6]])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_heat(self):
        """Where things happened: actions counted per 16 x 16 square, by kind and time."""
        from datetime import datetime, timezone
        from mcsm import ledger
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "ledger.sqlite")
            self._db(p)
            con = sqlite3.connect(p)
            con.execute("update actions set player_id = 1")
            con.commit()
            con.close()
            lg = ledger.Ledger(p)
            ms = lambda *a: int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)
            day = (ms(2026, 3, 1), ms(2026, 3, 2))
            cells = {(c[0], c[1]): c[2] for c in ledger.heat(lg, "minecraft:overworld", *day, "all")}
            self.assertEqual(cells, {(0, 0): 4})            # five actions in square 0, 0, one of them rolled back
            build = ledger.heat(lg, "minecraft:overworld", *day, "building")
            self.assertEqual(build, [[0, 0, 2, 67]])        # two placements left (one rolled back), at y 70 and 64
            self.assertEqual(ledger.heat(lg, "minecraft:overworld", *day, "fighting"), [[0, 0, 1, 64]])
            self.assertEqual(ledger.heat(lg, "minecraft:overworld", ms(2026, 4, 1), ms(2026, 4, 2)), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_patched_chunk(self):
        from mcsm import blockmesh as bm

        class Base:
            min_y, lit = -64, False
            tops = [63] * 256

            def state(self, x, y, z):
                return "minecraft:stone" if y <= 63 else "minecraft:air"

            def biome(self, x, y, z):
                return "minecraft:plains"

            def light(self, x, y, z):
                return (15, 0) if y > 63 else (0, 0)
        c = bm.PatchedChunk(Base(), {(1, 70, 1): "minecraft:oak_log", (2, 63, 2): "minecraft:air"})
        self.assertEqual((c.state(1, 70, 1), c.state(1, 69, 1)), ("minecraft:oak_log", "minecraft:air"))
        self.assertEqual(c.tops[1 * 16 + 1], 70)          # a block above the ground: the column's top goes up
        self.assertEqual(c.tops[2 * 16 + 2], 62)          # a hole dug in the top: it goes down
        self.assertEqual(c.light(2, 63, 2)[0], 15)        # open to the sky now


class TestReview(unittest.TestCase):
    """The year in review: hours from sessions, statistics between backups, deaths, advancements."""

    def test_build(self):
        from datetime import datetime, timezone
        from mcsm import models as m, review
        U = timezone.utc
        d = m.ServerData(root=".")
        d.world = {"level_name": "Test"}
        alex = m.PlayerProfile(uuid="00000000-0000-4000-8000-000000000001", name="Alex", names_seen=["Alex", "OldAlex"])
        alex.advancements = {"minecraft:story/mine_stone": {"done": True, "completed": "2026-03-01T10:00:00Z"},
                             "minecraft:nether/distract_piglin": {"done": True, "completed": "2026-04-02T10:00:00Z"},
                             "minecraft:story/root": {"done": True, "completed": "2025-12-01T10:00:00Z"}}
        steve = m.PlayerProfile(uuid="00000000-0000-4000-8000-000000000002", name="Steve", names_seen=["Steve"])
        steve.advancements = {"minecraft:story/mine_stone": {"done": True, "completed": "2026-02-01T10:00:00Z"}}
        d.players = {"alex": alex, "steve": steve}
        d.sessions = [m.Session("OldAlex", datetime(2025, 12, 30, 20, tzinfo=U), datetime(2025, 12, 30, 21, tzinfo=U)),
                      m.Session("Alex", datetime(2026, 3, 1, 20, tzinfo=U), datetime(2026, 3, 1, 23, tzinfo=U)),
                      m.Session("Steve", datetime(2026, 3, 1, 21, tzinfo=U), datetime(2026, 3, 1, 22, tzinfo=U)),
                      m.Session("Steve", datetime(2026, 12, 31, 23, 30, tzinfo=U), datetime(2027, 1, 1, 0, 30, tzinfo=U))]
        d.events = [m.Event(datetime(2026, 3, 1, 22, tzinfo=U), m.EV_DEATH, "OldAlex", "OldAlex was shot by Skeleton"),
                    m.Event(datetime(2026, 3, 1, 22, 5, tzinfo=U), m.EV_DEATH, "Alex", "Alex was shot by Skeleton"),
                    m.Event(datetime(2026, 3, 1, 22, 6, tzinfo=U), m.EV_CHAT, "Steve", "hi")]

        def snap(t, walk, dirt, zombies, play):
            return {"backup": t.date().isoformat(), "time": t,
                    "stats": {"custom": {"walk_one_cm": walk, "fall_one_cm": 999_999_999, "play_time": play},
                              "mined": {"minecraft:stone": dirt}, "used": {"minecraft:dirt": dirt, "minecraft:diamond_pickaxe": 5},
                              "killed": {"minecraft:zombie": zombies}, "killed_by": {}}}
        d.player_history = {"Alex": [snap(datetime(2025, 12, 31, tzinfo=U), 100_000, 10, 1, 0),
                                     snap(datetime(2026, 6, 1, tzinfo=U), 600_000, 110, 4, 3 * 3600 * 20)],
                            "Steve": [snap(datetime(2026, 6, 1, tzinfo=U), 200_000, 50, 0, 3600 * 20)]}
        a, b = review.year_range(2026, U)
        r = review.build(d, a, b, U)
        self.assertEqual(r["order"], ["Alex", "Steve"])                   # OldAlex counts as Alex; 2025 left out
        A, S = r["players"]["Alex"], r["players"]["Steve"]
        self.assertEqual((A["headline"]["hours"], A["headline"]["days"], A["headline"]["rank"]), (3.0, 1, 1))
        self.assertEqual(S["headline"]["hours"], 1.5)                       # cut at the year's end
        self.assertEqual(A["travel"]["km"], 5.0)                            # walked 500,000 cm; falling isn't travel
        self.assertEqual(A["blocks"]["placed_top"], [["dirt", 100]])        # a pickaxe isn't a block
        self.assertEqual(A["mobs"]["top"], [["zombie", 3]])
        self.assertEqual(A["deaths"]["count"], 2)
        self.assertEqual(A["deaths"]["top_cause"], [("shot by Skeleton", 2)])
        self.assertEqual(A["firsts"]["death"]["text"], "shot by Skeleton")
        self.assertFalse(A["firsts"]["new_this_year"])
        self.assertEqual(A["advancements"]["count"], 2)                    # the 2025 one isn't this year's
        self.assertEqual(A["advancements"]["rarest"]["name"], "distract piglin")
        self.assertFalse(A["stats_span"]["partial"])
        self.assertTrue(S["firsts"]["new_this_year"])
        self.assertTrue(S["stats_span"]["available"])                      # new this year: from zero
        s = r["server"]
        self.assertEqual(s["numbers"]["players"], 2)
        self.assertEqual(s["numbers"]["chat_lines"], 1)
        self.assertEqual(s["records"]["longest_session"]["player"], "Alex")
        self.assertEqual(s["advancements"]["rarest"][0]["first"], "Alex")
        self.assertEqual(len(s["months"]), 12)
        self.assertEqual(r["uuids"]["Alex"], alex.uuid)
        self.assertEqual(review.death_cause("Bob was slain by Zombie using [Sword]", "Bob"), "slain by Zombie")


class TestMoment(unittest.TestCase):
    """Blocks as they were at a moment: chunk packets, block changes and the world from several recordings."""

    @classmethod
    def setUpClass(cls):
        from mcsm import protocol
        states = protocol.block_states(protocol.table(777))
        cls.states = states
        cls.ids = {"air": states.index("minecraft:air"), "stone": states.index("minecraft:stone"),
                   "grass": states.index("minecraft:grass_block[snowy=false]"), "glass": states.index("minecraft:glass")}

    def test_block_table(self):
        from mcsm import protocol
        self.assertEqual(protocol.table(777)["packets"]["level_chunk_with_light"], 46)
        self.assertEqual(self.states[0], "minecraft:air")
        tmp = tempfile.mkdtemp()
        try:                                       # properties are numbered sorted by name, the last fastest
            p = os.path.join(tmp, "blocks.json")
            with open(p, "w") as fh:
                json.dump({"minecraft:air": {"states": [{"id": 0}]},
                           "minecraft:lamp": {"properties": {"lit": ["true", "false"], "facing": ["up", "down"]},
                                              "states": [{"id": 1, "properties": {"facing": "up", "lit": "true"}},
                                                         {"id": 2, "properties": {"facing": "up", "lit": "false"}},
                                                         {"id": 3, "properties": {"facing": "down", "lit": "true"}},
                                                         {"id": 4, "properties": {"facing": "down", "lit": "false"}}]}}, fh)
            t = {"blocks": protocol._block_table(p)}
            self.assertEqual(protocol.block_states(t)[2], "minecraft:lamp[facing=up,lit=false]")
            with open(p, "w") as fh:                   # a report that doesn't follow the rule: refused
                json.dump({"minecraft:lamp": {"properties": {"lit": ["true", "false"]},
                                              "states": [{"id": 0, "properties": {"lit": "false"}},
                                                         {"id": 1, "properties": {"lit": "true"}}]}}, fh)
            with self.assertRaises(ValueError):
                protocol._block_table(p)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_chunk_packet(self):
        from mcsm import moment
        for two, as_bytes, proto in ((True, True, 777), (False, False, 773)):
            c = moment.RecChunk(_chunk_packet(2, -3, self.ids, two, as_bytes), proto, self.states,
                                ["minecraft:plains"], "minecraft:overworld")
            self.assertEqual((c.cx, c.cz, c.min_y, len(c.sections)), (2, -3, -64, 24))
            self.assertEqual(c.state(5, 64, 7), "minecraft:grass_block[snowy=false]")
            self.assertEqual(c.state(5, 10, 7), "minecraft:stone")
            self.assertEqual(c.state(5, 65, 7), "minecraft:air")
            self.assertEqual(c.tops[7 * 16 + 5], 64)
            self.assertEqual(c.biome(5, 64, 7), "minecraft:plains")
            self.assertEqual(c.light(5, 66, 7), (15, 0))
        c.apply(5, 70, 7, self.ids["glass"])               # a block placed: the column's top goes up
        self.assertEqual((c.state(5, 70, 7), c.tops[7 * 16 + 5]), ("minecraft:glass", 70))
        c.apply(5, 70, 7, self.ids["air"])                 # and broken again
        self.assertEqual(c.tops[7 * 16 + 5], 64)
        self.assertEqual(moment.block_pos(((5 & 0x3FFFFFF) << 38) | ((-7 & 0x3FFFFFF) << 12) | (70 & 0xFFF)), (5, 70, -7))
        self.assertEqual(moment.section_pos(((-1 & 0x3FFFFF) << 42) | ((2 & 0x3FFFFF) << 20) | (4 & 0xFFFFF)), (-1, 4, 2))

    def test_world_from_recordings(self):
        import struct
        from mcsm import moment
        g = self.ids["glass"]
        recs = []
        for t in range(20):
            recs.append(("move_entities", _fb_move([(7, 8.0, 65.0, 8.0)])))
            if t == 5:                                     # a block placed at (3, 70, 4)
                pos = ((3 & 0x3FFFFFF) << 38) | ((4 & 0x3FFFFFF) << 12) | (70 & 0xFFF)
                recs.append(("game_packet", _vi(8) + struct.pack(">q", pos) + _vi(g)))
            if t == 8:                                     # several in one section: (1, 69, 1)
                sec = (0 << 42) | (0 << 20) | 4
                recs.append(("game_packet", _vi(86) + struct.pack(">q", sec) + _vi(1)
                             + _vi((g << 12) | (1 << 8) | (1 << 4) | 5)))
            recs.append(("next_tick", b""))
        tmp = tempfile.mkdtemp()
        try:
            z = os.path.join(tmp, "r.zip")
            pkt = _chunk_packet(0, 0, self.ids)
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("metadata.json", json.dumps({"protocol_version": 777, "version_string": "26.3",
                                                         "chunks": {"c0.flashback": {"duration": 20}}}))
                zf.writestr("level_chunk_caches/0", struct.pack(">I", len(pkt)) + pkt)
                zf.writestr("c0.flashback", _fb_chunk(recs, [("level_chunk_cached", _vi(0))]))
            st = moment.recording_state(z, 6)
            self.assertEqual(st["loads"], [["minecraft:overworld", 0, 0]])
            self.assertEqual(st["edits"], [[5, "minecraft:overworld", 3, 70, 4, g]])
            world = moment.world_at([{"zip": z, "tick": 12, "when": lambda t: t * 50}], {(0, 0)},
                                    "minecraft:overworld", os.path.join(tmp, "cache"))
            c = world[(0, 0)]
            self.assertEqual((c.state(3, 70, 4), c.state(1, 69, 1)), ("minecraft:glass", "minecraft:glass"))
            self.assertEqual(c.tops[4 * 16 + 3], 70)
            self.assertEqual(moment.world_at([{"zip": z, "tick": 12, "when": lambda t: t * 50}], {(5, 5)},
                                             "minecraft:overworld", None), {})      # a chunk nobody saw: none
            # the changes over a stretch (for showing them as they happen), and where each cached chunk is
            self.assertEqual([e[0] for e in moment.edits_between(z, 0, 20)], [5, 8])
            self.assertEqual([e[0] for e in moment.edits_between(z, 6, 20)], [8])
            idx = moment.CacheIndex(z, os.path.join(tmp, "cache"))
            with zipfile.ZipFile(z) as zf:
                self.assertEqual(idx.where(zf, 0), (0, 0))
                self.assertIsNone(idx.where(zf, 1))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestMobModels(unittest.TestCase):
    """Mob models read from the client jar's model code by a small bytecode interpreter."""

    def test_layer_from_bytecode(self):
        import struct
        from mcsm import mobmodels
        G = "net/minecraft/client/model/geom/"
        B = G + "builders/"

        def roots(cp):
            def inv(op, c, n, d):
                return bytes([op]) + struct.pack(">H", cp.mref(c, n, d))
            ldcf = lambda v: b"\x13" + struct.pack(">H", cp.flt(v))     # ldc_w
            return b"".join([
                inv(0xB8, "com/google/common/collect/ImmutableMap", "builder", "()Lcom/google/common/collect/ImmutableMap$Builder;"),
                bytes([0x4B]),                                          # astore_0
                bytes([0xBB]) + struct.pack(">H", cp.cls(B + "MeshDefinition")), bytes([0x59]),   # new, dup
                inv(0xB7, B + "MeshDefinition", "<init>", "()V"), bytes([0x4C]),                  # astore_1
                bytes([0x2B]), inv(0xB6, B + "MeshDefinition", "getRoot", f"()L{B}PartDefinition;"),
                bytes([0x13]) + struct.pack(">H", cp.str("head")),
                inv(0xB8, B + "CubeListBuilder", "create", f"()L{B}CubeListBuilder;"),
                bytes([0x03, 0x10, 16]),                                # 0, 16
                inv(0xB6, B + "CubeListBuilder", "texOffs", f"(II)L{B}CubeListBuilder;"),
                ldcf(-4.0), ldcf(-8.0), ldcf(-4.0), ldcf(4.0), ldcf(2.0), bytes([0x6A]),   # 4 x 2 = 8 (fmul)
                ldcf(8.0), ldcf(8.0),
                inv(0xB6, B + "CubeListBuilder", "addBox", f"(FFFFFF)L{B}CubeListBuilder;"),
                bytes([0x0B]), ldcf(6.0), bytes([0x0B]),                # 0, 6, 0
                inv(0xB8, G + "PartPose", "offset", f"(FFF)L{G}PartPose;"),
                inv(0xB6, B + "PartDefinition", "addOrReplaceChild",
                    f"(Ljava/lang/String;L{B}CubeListBuilder;L{G}PartPose;)L{B}PartDefinition;"),
                bytes([0x57]),                                          # pop
                bytes([0x2A, 0xB2]) + struct.pack(">H", cp.fref(G + "ModelLayers", "TEST", f"L{G}ModelLayerLocation;")),
                bytes([0x2B, 0x10, 64, 0x10, 32]),                      # mesh, 64, 32
                inv(0xB8, B + "LayerDefinition", "create", f"(L{B}MeshDefinition;II)L{B}LayerDefinition;"),
                inv(0xB6, "com/google/common/collect/ImmutableMap$Builder", "put",
                    "(Ljava/lang/Object;Ljava/lang/Object;)Lcom/google/common/collect/ImmutableMap$Builder;"),
                bytes([0x57, 0x01, 0xB0]),                              # pop, null, areturn
            ])

        tmp = tempfile.mkdtemp()
        try:
            jar = os.path.join(tmp, "minecraft-99.1-client.jar")
            with zipfile.ZipFile(jar, "w") as z:
                z.writestr(mobmodels._ROOTS + ".class",
                           _class_file(mobmodels._ROOTS, {("createRoots", "()Ljava/util/Map;"): (4, roots)}))
            out = mobmodels.extract(jar)
            lay = out["layers"]["TEST"]
            self.assertEqual((lay["tw"], lay["th"], lay["scale"]), (64, 32, 1.0))
            head = lay["root"]["children"]["head"]
            self.assertEqual(head["pose"][:3], [0.0, 6.0, 0.0])
            self.assertEqual(head["cubes"], [[0, 16, -4.0, -8.0, -4.0, 8.0, 8.0, 8.0, 0.0, 0.0, 0.0, False]])
            with zipfile.ZipFile(os.path.join(tmp, "old.jar"), "w") as z:        # an obfuscated jar: not readable
                z.writestr("abc.class", b"")
            self.assertEqual(mobmodels.extract(os.path.join(tmp, "old.jar"))["layers"], {})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestSkins(unittest.TestCase):
    """Players' real skins: looked up by UUID at Mojang, only from Mojang's texture server, kept and refreshed."""

    def test_skin(self):
        import base64
        from mcsm import skins
        tmp = tempfile.mkdtemp()
        uuid = "c2e9a692-cc46-415a-bcbc-23639ba15d30"
        calls = []

        def prof(url, slim=True):
            tex = {"textures": {"SKIN": {"url": url, **({"metadata": {"model": "slim"}} if slim else {})}}}
            return json.dumps({"id": uuid.replace("-", ""), "properties": [
                {"name": "textures", "value": base64.b64encode(json.dumps(tex).encode()).decode()}]}).encode()
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 50

        def fetch(url, limit):
            calls.append(url)
            if "sessionserver" in url:
                return prof("http://textures.minecraft.net/texture/abc123")
            return png if url == "https://textures.minecraft.net/texture/abc123" else None
        try:
            s = skins.skin(uuid, tmp, fetch, now=1000.0)
            self.assertTrue(s["slim"])
            with open(s["file"], "rb") as fh:
                self.assertEqual(fh.read(), png)
            self.assertEqual(calls[0], skins.PROFILE_URL.format(uuid=uuid.replace("-", "")))   # only the UUID goes out
            n = len(calls)
            self.assertEqual(skins.skin(uuid, tmp, fetch, now=2000.0)["file"], s["file"])     # kept: no new lookup
            self.assertEqual(len(calls), n)
            skins.skin(uuid, tmp, fetch, now=1000.0 + skins.REFRESH_S + 1)                       # a week on: checked again
            self.assertGreater(len(calls), n)
            self.assertEqual(skins.skin_file(uuid, tmp), s["file"])
            self.assertIsNone(skins.skin_file("../evil", tmp))
            self.assertIsNone(skins.skin("not-a-uuid", tmp, fetch))
            # a skin hosted anywhere else is refused; no skin -> None (the default skin is used)
            other = "11111111222233334444555555555555"
            self.assertIsNone(skins.skin(other, tmp, lambda url, limit: prof("https://evil.example/skin.png")
                                         if "sessionserver" in url else png, now=1000.0))
            self.assertIsNone(skins.skin("22222222222233334444555555555555", tmp, lambda url, limit: None, now=1000.0))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_skins_from_recordings(self):
        """The skin a player had in each recording (from the profile the game sent), over time."""
        import base64
        from datetime import timedelta
        from mcsm import skins
        tmp = tempfile.mkdtemp()
        uuid = "c2e9a692cc46415abcbc23639ba15d30"

        def rec(name, skin_hash, slim):
            tex = {"timestamp": 1, "profileId": uuid, "profileName": "OldName",
                   "textures": {"SKIN": {"url": f"http://textures.minecraft.net/texture/{skin_hash}",
                                         **({"metadata": {"model": "slim"}} if slim else {})},
                                **({"CAPE": {"url": "http://textures.minecraft.net/texture/" + "cc33" * 8}} if slim else {})}}
            value = base64.b64encode(json.dumps(tex).encode())
            body = b"\xd7\x80\xe8\x84junk" + bytes([8]) + b"textures" + bytes([len(value) & 0x7f | 0x80, len(value) >> 7]) + value
            path = os.path.join(tmp, name)
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("c0.flashback", body)
            return path
        try:
            a, b, c = rec("a.zip", "aa11" * 8, False), rec("b.zip", "bb22" * 8, True), rec("c.zip", "bb22" * 8, True)
            self.assertEqual(skins.recording_skins(a), [{"uuid": uuid, "name": "OldName", "hash": "aa11" * 8, "slim": False,
                                                         "cape": None}])
            self.assertEqual(skins.recording_skins(b)[0]["cape"], "cc33" * 8)         # a cape: its texture
            t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
            reps = [m.Replay(p, t0 + timedelta(days=i), 1000, format="flashback") for i, p in enumerate((a, b, c))]
            cache = os.path.join(tmp, "cache")
            line = skins.timeline(reps, lambda r: r.path, {uuid: "Steve"}.get, cache)
            ms = int(t0.timestamp() * 1000)
            self.assertEqual(line, {"Steve": [[ms, "aa11" * 8, False, None],
                                              [ms + 86_400_000, "bb22" * 8, True, "cc33" * 8]]})   # same skin: once
            self.assertTrue(os.path.isfile(os.path.join(cache, "recordings.json")))
            self.assertEqual(skins.timeline(reps, lambda r: r.path, {uuid: "Steve"}.get, cache), line)       # from the cache
            png = b"\x89PNG\r\n\x1a\n" + b"y" * 10
            got = skins.texture("aa11" * 8, cache, lambda url, limit: png if url.endswith("aa11" * 8) else None)
            with open(got, "rb") as fh:
                self.assertEqual(fh.read(), png)
            self.assertIsNone(skins.texture("../x", cache))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestVersionBand(unittest.TestCase):
    """The Minecraft version over time, from the backups and the recordings (the history's version band)."""

    def test_segments(self):
        from datetime import timedelta
        from mcsm import history
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        d = m.ServerData(root="x")
        d.backups = [{"time": t0, "version": "1.21.5"}, {"time": t0 + timedelta(days=60), "version": "1.21.11"},
                     {"time": t0 + timedelta(days=120), "version": "26.1.2"}]
        d.replays = [m.Replay("a.zip", t0 + timedelta(days=30), 1000, mc_version="1.21.5"),
                     m.Replay("b.zip", t0 + timedelta(days=50), 1000, mc_version="1.21.11"),   # the change: by day 50
                     m.Replay("c.zip", t0 + timedelta(days=70), 1000, mc_version="26.1.2"),    # a stray test client
                     m.Replay("d.zip", t0 + timedelta(days=80), 1000, mc_version="1.21.11"),
                     m.Replay("e (text).zip", t0 + timedelta(days=90), 1000, mc_version="9.9")]  # the app's copy: ignored
        segs = history.version_segments(d)
        self.assertEqual([s["mc"] for s in segs], ["1.21.5", "1.21.11", "26.1.2"])
        ms = lambda days: int((t0 + timedelta(days=days)).timestamp() * 1000)          # noqa: E731
        self.assertEqual(segs[1]["start"], ms(50))
        self.assertEqual(segs[1]["end"], ms(120))
        self.assertEqual(segs[0]["end"], segs[1]["start"])
