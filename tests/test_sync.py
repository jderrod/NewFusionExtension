import helpers  # noqa: F401  (puts the add-in package on sys.path)

import os
import shutil
import string
import tempfile
import threading
import time
import unittest
from unittest import mock

from nfe import sync

OLD = 120.0     # seconds: comfortably "settled"


def write(path, text="x", age=OLD):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    t = time.time() - age
    os.utime(path, (t, t))
    return path


def tree(root):
    out = []
    for d, _, files in os.walk(root):
        for f in files:
            out.append(os.path.relpath(os.path.join(d, f), root).replace("\\", "/"))
    return sorted(out)


def missing_drive():
    for letter in reversed(string.ascii_uppercase):
        if not os.path.exists(letter + ":\\"):
            return letter + ":"
    return None


def loc(mode="local", shares=None, mirror_root="network_mirror", enabled=True, interval=1, settle=0):
    return {"mode": mode,
            "local": {"inbox": "", "mirror_root": mirror_root},
            "remote": {"inbox": "\\\\ddc-mefs\\iBob-Export\\S2S_Export",
                       "shares": dict(sync.DEFAULT_REMOTE_SHARES, **(shares or {}))},
            "sync": {"enabled": enabled, "interval_s": interval, "settle_s": settle}}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nfe_test_sync_")
        self.state_path = os.path.join(self.root, "state", "sync_state.json")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    def pair(self, name="gcode"):
        return [p for p in sync.build_pairs(self.root, loc()) if p["name"] == name]

    def state(self):
        return sync.SyncState(self.state_path)


class BuildPairsTest(Base):
    def test_local_mode_mirrors_under_root(self):
        pairs = dict((p["name"], p) for p in sync.build_pairs(self.root, loc()))
        self.assertEqual(sorted(pairs), sorted(sync.SHARE_KEYS))
        self.assertEqual(pairs["gcode"]["source"], self.p("machine_drops", "anderson"))
        self.assertEqual(pairs["gcode"]["dest"], self.p("network_mirror", "gcode"))
        self.assertEqual(pairs["gcode"]["pattern"], "*.txt")
        self.assertFalse(pairs["gcode"]["recursive"])
        self.assertEqual(pairs["drilling"]["source"], self.p("machine_drops", "gannomat"))
        self.assertEqual(pairs["drilling"]["pattern"], "*.json")
        self.assertEqual(pairs["output"]["source"], self.p("output"))
        self.assertTrue(pairs["output"]["recursive"])
        self.assertEqual(pairs["order_completed"]["source"], self.p("dropbox", "done"))
        self.assertEqual(pairs["order_failed"]["source"], self.p("dropbox", "failed"))
        self.assertEqual(pairs["order_failed"]["dest"], self.p("network_mirror", "order_failed"))
        for p in pairs.values():
            self.assertEqual(set(p), {"name", "source", "dest", "recursive", "pattern"})

    def test_absolute_mirror_root(self):
        other = tempfile.mkdtemp(prefix="nfe_test_mirror_")
        try:
            pairs = sync.build_pairs(self.root, loc(mirror_root=other))
            self.assertEqual(pairs[0]["dest"], os.path.join(other, "gcode"))
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_remote_mode_uses_shares(self):
        pairs = dict((p["name"], p["dest"]) for p in sync.build_pairs(self.root, loc("remote")))
        self.assertEqual(pairs["gcode"], "\\\\ddc-mefs\\Anderson")
        self.assertEqual(pairs["drilling"], "\\\\ddc-mefs\\Gannomat\\F360_output")
        self.assertEqual(pairs["order_completed"], "\\\\ddc-mefs\\Fusion\\Fusion Folders\\order_completed")
        # sources stay local in remote mode
        src = dict((p["name"], p["source"]) for p in sync.build_pairs(self.root, loc("remote")))
        self.assertEqual(src["output"], self.p("output"))

    def test_blank_share_disables_pair_in_both_modes(self):
        cfg = loc(shares={"drilling": "", "order_failed": ""})
        for mode in ("local", "remote"):
            cfg["mode"] = mode
            names = [p["name"] for p in sync.build_pairs(self.root, cfg)]
            self.assertEqual(names, ["gcode", "output", "order_completed"], mode)
        self.assertEqual(sync.disabled_pairs(cfg), ["drilling", "order_failed"])

    def test_unknown_mode_is_local(self):
        pairs = sync.build_pairs(self.root, loc("banana"))
        self.assertTrue(pairs[0]["dest"].startswith(self.p("network_mirror")))


class CopyTest(Base):
    def test_atomic_copy_leaves_no_syncpart(self):
        src = write(self.p("a.txt"), "hello")
        dest = self.p("out", "sub", "a.txt")
        sync.copy_atomic(src, dest)
        with open(dest, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "hello")
        self.assertEqual(os.listdir(self.p("out", "sub")), ["a.txt"])
        self.assertEqual(os.stat(dest).st_mtime_ns, os.stat(src).st_mtime_ns)

    def test_partial_never_visible(self):
        src = write(self.p("a.txt"), "hello")
        dest = self.p("out", "a.txt")
        seen = {}
        real = shutil.copy2

        def spy(s, d):
            seen["tmp"] = d
            seen["dest_during_copy"] = os.path.exists(dest)
            return real(s, d)

        with mock.patch.object(sync.shutil, "copy2", spy):
            sync.copy_atomic(src, dest)
        self.assertTrue(seen["tmp"].endswith("a.txt" + sync.TEMP_SUFFIX))
        self.assertEqual(os.path.dirname(seen["tmp"]), os.path.dirname(dest))
        self.assertFalse(seen["dest_during_copy"])
        self.assertEqual(os.listdir(self.p("out")), ["a.txt"])

    def test_failed_copy_cleans_up(self):
        src = write(self.p("a.txt"), "hello")
        dest = self.p("out", "a.txt")

        def broken(s, d):
            with open(d, "w") as fh:
                fh.write("hal")
            raise OSError("network name no longer available")

        with mock.patch.object(sync.shutil, "copy2", broken):
            with self.assertRaises(OSError):
                sync.copy_atomic(src, dest)
        self.assertEqual(os.listdir(self.p("out")), [])


class SyncPassTest(Base):
    def test_settle_time(self):
        f = write(self.p("machine_drops", "anderson", "1-D1-X.txt"), age=0)
        mtime = os.stat(f).st_mtime
        st = self.state()
        r = sync.sync_pass(self.pair(), st, settle_s=5, now=mtime + 2)[0]
        self.assertEqual((r["copied"], r["pending"], r["reachable"]), ([], 1, True))
        self.assertFalse(os.path.exists(self.p("network_mirror", "gcode", "1-D1-X.txt")))
        r = sync.sync_pass(self.pair(), st, settle_s=5, now=mtime + 6)[0]
        self.assertEqual((r["copied"], r["pending"]), (["1-D1-X.txt"], 0))
        self.assertTrue(os.path.exists(self.p("network_mirror", "gcode", "1-D1-X.txt")))

    def test_state_prevents_resending_consumed_files(self):
        f = write(self.p("machine_drops", "anderson", "a.txt"), "G0")
        dest = self.p("network_mirror", "gcode", "a.txt")
        r = sync.sync_pass(self.pair(), self.state(), 0)[0]
        self.assertEqual(r["copied"], ["a.txt"])
        os.remove(dest)                            # the machine consumed it
        # a fresh process reloads the persisted state
        r = sync.sync_pass(self.pair(), self.state(), 0)[0]
        self.assertEqual((r["copied"], r["errors"]), ([], []))
        self.assertFalse(os.path.exists(dest))
        self.assertTrue(os.path.exists(self.state_path))
        # a local change is copied again
        write(f, "G0 G1 changed", age=OLD - 10)
        r = sync.sync_pass(self.pair(), self.state(), 0)[0]
        self.assertEqual(r["copied"], ["a.txt"])

    def test_prune(self):
        f = write(self.p("machine_drops", "anderson", "a.txt"))
        keep = write(self.p("machine_drops", "anderson", "b.txt"))
        st = self.state()
        sync.sync_pass(self.pair(), st, 0)
        st.mark("drilling", "other.json", os.stat(keep))   # another pair's entry
        self.assertEqual(st.count("gcode"), 2)
        os.remove(f)
        sync.sync_pass(self.pair(), st, 0)
        self.assertEqual(st.count("gcode"), 1)
        self.assertTrue(st.is_synced("gcode", "b.txt", os.stat(keep)))
        self.assertEqual(st.count("drilling"), 1, "pairs not in the pass are not pruned")
        self.assertEqual(sync.SyncState(self.state_path).count("gcode"), 1)
        # a file that re-appears later is new again
        write(f)
        r = sync.sync_pass(self.pair(), st, 0)[0]
        self.assertEqual(r["copied"], ["a.txt"])

    def test_recursive_tree_with_exclusions(self):
        run = self.p("output", "3X8X_DV_0622 Sep 28 2026 - 0520")
        write(os.path.join(run, "1-D1-3X8X_DV_0622-drawing.pdf"))
        write(os.path.join(run, "1-D1-3X8X_DV_0622.txt"))
        write(os.path.join(run, "3X8X_DV_0622-report.json"))
        write(os.path.join(run, "_partial_1-D2-3X8X_DV_0622-drawing.pdf"))
        write(os.path.join(run, ".tmp_abcd.json"))
        write(os.path.join(run, "_nc", "D2", "1001.nc"))
        write(os.path.join(run, "x.txt.syncpart"))
        write(self.p("output", "deep", "a", "b", "c.log"))
        r = sync.sync_pass(self.pair("output"), self.state(), 0)[0]
        expected = ["3X8X_DV_0622 Sep 28 2026 - 0520/1-D1-3X8X_DV_0622-drawing.pdf",
                    "3X8X_DV_0622 Sep 28 2026 - 0520/1-D1-3X8X_DV_0622.txt",
                    "3X8X_DV_0622 Sep 28 2026 - 0520/3X8X_DV_0622-report.json",
                    "deep/a/b/c.log"]
        self.assertEqual(sorted(r["copied"]), expected)
        self.assertEqual(tree(self.p("network_mirror", "output")), expected)

    def test_flat_pattern(self):
        d = self.p("machine_drops", "anderson")
        write(os.path.join(d, "a.txt"))
        write(os.path.join(d, "B.TXT"))
        write(os.path.join(d, "b.nc"))
        write(os.path.join(d, ".tmp_c.txt"))
        write(os.path.join(d, "sub", "d.txt"))
        write(self.p("machine_drops", "gannomat", "ORDER1.json"))
        write(self.p("machine_drops", "gannomat", "notes.txt"))
        res = sync.sync_pass(sync.build_pairs(self.root, loc()), self.state(), 0)
        by = dict((r["name"], r) for r in res)
        self.assertEqual(sorted(by["gcode"]["copied"]), ["B.TXT", "a.txt"])
        self.assertEqual(tree(self.p("network_mirror", "gcode")), ["B.TXT", "a.txt"])
        self.assertEqual(by["drilling"]["copied"], ["ORDER1.json"])
        # empty / missing sources are fine and their mirrors exist
        self.assertTrue(by["order_failed"]["reachable"])
        self.assertTrue(os.path.isdir(self.p("network_mirror", "order_failed")))

    def test_unreachable_destinations_do_not_raise_or_block_others(self):
        write(self.p("machine_drops", "anderson", "a.txt"))
        write(self.p("machine_drops", "gannomat", "o.json"))
        write(self.p("output", "run", "r.pdf"))
        pairs = sync.build_pairs(self.root, loc())
        by = dict((p["name"], p) for p in pairs)
        by["gcode"]["dest"] = "\\\\nonexistent-host-xyz\\share\\Anderson"
        drive = missing_drive()
        if drive:
            by["drilling"]["dest"] = drive + "\\Gannomat\\F360_output"
        st = self.state()
        t0 = time.time()
        res = dict((r["name"], r) for r in sync.sync_pass(pairs, st, 0, probe_timeout=3.0))
        self.assertLess(time.time() - t0, 15)
        g = res["gcode"]
        self.assertFalse(g["reachable"])
        self.assertEqual((g["copied"], g["pending"]), ([], 1))
        self.assertTrue(g["errors"])
        if drive:
            self.assertFalse(res["drilling"]["reachable"])
            self.assertEqual(res["drilling"]["pending"], 1)
            self.assertFalse(os.path.exists(drive + "\\"))
        self.assertTrue(res["output"]["reachable"])
        self.assertEqual(res["output"]["copied"], ["run/r.pdf"])
        self.assertEqual(st.count("gcode"), 0, "unsent files are not marked")
        # nothing got "created" locally for the missing share
        self.assertFalse(os.path.exists(self.p("nonexistent-host-xyz")))
        # share comes back (here: pointed somewhere reachable) -> copied next pass
        by["gcode"]["dest"] = self.p("back", "gcode")
        r = sync.sync_pass([by["gcode"]], st, 0)[0]
        self.assertEqual(r["copied"], ["a.txt"])

    def test_invalid_unc_path(self):
        write(self.p("machine_drops", "anderson", "a.txt"))
        pair = self.pair()
        pair[0]["dest"] = "\\\\justahost"
        r = sync.sync_pass(pair, self.state(), 0)[0]
        self.assertFalse(r["reachable"])
        self.assertTrue(r["errors"])

    def test_dest_inside_source_refused(self):
        write(self.p("output", "a.pdf"))
        pair = self.pair("output")
        pair[0]["dest"] = self.p("output", "mirror")
        r = sync.sync_pass(pair, self.state(), 0)[0]
        self.assertEqual(r["copied"], [])
        self.assertIn("inside the source", r["errors"][0])

    def test_corrupt_state_starts_fresh(self):
        os.makedirs(os.path.dirname(self.state_path))
        with open(self.state_path, "w") as fh:
            fh.write("{not json")
        st = self.state()
        self.assertEqual(st.count(), 0)
        self.assertTrue(st.load_error)
        write(self.p("machine_drops", "anderson", "a.txt"))
        self.assertEqual(sync.sync_pass(self.pair(), st, 0)[0]["copied"], ["a.txt"])
        self.assertEqual(sync.SyncState(self.state_path).count(), 1)


class ServiceTest(Base):
    def setUp(self):
        Base.setUp(self)
        self.cfg = loc()
        self.services = []

    def tearDown(self):
        for s in self.services:
            s.stop()
        Base.tearDown(self)

    def service(self):
        s = sync.SyncService(self.root, lambda: self.cfg, self.state_path)
        self.services.append(s)
        return s

    def test_mode_switch_changes_destinations(self):
        remote = tempfile.mkdtemp(prefix="nfe_test_remote_")
        self.addCleanup(shutil.rmtree, remote, True)
        self.cfg["remote"]["shares"] = dict((k, os.path.join(remote, k)) for k in sync.SHARE_KEYS)
        svc = self.service()
        write(self.p("machine_drops", "anderson", "local1.txt"))
        svc.run_once()
        self.assertEqual(tree(self.p("network_mirror", "gcode")), ["local1.txt"])
        self.assertEqual(svc.status()["mode"], "local")

        self.cfg["mode"] = "remote"                    # no restart
        write(self.p("machine_drops", "anderson", "remote1.txt"))
        res = dict((r["name"], r) for r in svc.run_once())
        self.assertEqual(res["gcode"]["dest"], os.path.join(remote, "gcode"))
        self.assertEqual(tree(os.path.join(remote, "gcode")), ["remote1.txt"],
                         "files already sent in the other mode are not resent")
        self.assertEqual(tree(self.p("network_mirror", "gcode")), ["local1.txt"])
        st = svc.status()
        self.assertEqual(st["mode"], "remote")
        self.assertEqual(dict((p["name"], p["dest"]) for p in st["pairs"])["gcode"],
                         os.path.join(remote, "gcode"))

        self.cfg["mode"] = "local"
        write(self.p("machine_drops", "anderson", "local2.txt"))
        svc.run_once()
        self.assertEqual(tree(self.p("network_mirror", "gcode")), ["local1.txt", "local2.txt"])

    def test_resync(self):
        svc = self.service()
        write(self.p("machine_drops", "anderson", "a.txt"))
        write(self.p("machine_drops", "gannomat", "o.json"))
        svc.run_once()
        os.remove(self.p("network_mirror", "gcode", "a.txt"))
        os.remove(self.p("network_mirror", "drilling", "o.json"))
        svc.run_once()
        self.assertFalse(os.path.exists(self.p("network_mirror", "gcode", "a.txt")))
        self.assertEqual(svc.resync("gcode"), 1)
        svc.run_once()
        self.assertTrue(os.path.exists(self.p("network_mirror", "gcode", "a.txt")))
        self.assertFalse(os.path.exists(self.p("network_mirror", "drilling", "o.json")))
        self.assertEqual(svc.resync(), 2)
        svc.run_once()
        self.assertTrue(os.path.exists(self.p("network_mirror", "drilling", "o.json")))

    def test_disabled(self):
        self.cfg["sync"]["enabled"] = False
        svc = self.service()
        write(self.p("machine_drops", "anderson", "a.txt"))
        self.assertIsNone(svc.run_once())
        self.assertFalse(os.path.exists(self.p("network_mirror")))
        self.assertFalse(svc.status()["enabled"])

    def test_background_thread_and_status(self):
        svc = self.service()
        self.assertTrue(svc.start())
        write(self.p("machine_drops", "anderson", "a.txt"))
        svc.trigger()
        deadline = time.time() + 10
        dest = self.p("network_mirror", "gcode", "a.txt")
        while time.time() < deadline and not os.path.exists(dest):
            time.sleep(0.05)
        self.assertTrue(os.path.exists(dest))
        deadline = time.time() + 5
        while time.time() < deadline and not svc.status()["totals"]["copied"]:
            time.sleep(0.05)
        st = svc.status()
        self.assertTrue(st["running"])
        self.assertEqual(st["mode"], "local")
        self.assertGreaterEqual(st["passes"], 1)
        self.assertIsNotNone(st["last_pass"])
        self.assertEqual(st["totals"]["copied"], 1)
        g = [p for p in st["pairs"] if p["name"] == "gcode"][0]
        self.assertEqual(g["copied"], ["a.txt"])
        self.assertTrue(g["reachable"])
        t0 = time.time()
        svc.stop()
        self.assertLess(time.time() - t0, 5)
        self.assertFalse(svc.status()["running"])

    def test_config_error_does_not_kill_thread(self):
        calls = []

        def bad():
            calls.append(1)
            raise RuntimeError("config broken")

        svc = sync.SyncService(self.root, bad, self.state_path)
        self.services.append(svc)
        self.assertIsNotNone(svc.run_once())      # empty config -> defaults (local mode)
        self.assertTrue(calls)

    def test_single_instance(self):
        a, b = self.service(), self.service()
        self.assertTrue(a.start())
        self.assertFalse(b.start())
        self.assertIn("already running", b.status()["error"])
        self.assertIsNone(b.run_once())
        a.stop()
        self.assertTrue(b.start())

    def test_status_is_fast_during_a_pass(self):
        svc = self.service()
        write(self.p("machine_drops", "anderson", "a.txt"))
        gate = threading.Event()
        real = sync.copy_atomic

        def slow(src, dest):
            gate.wait(5)
            return real(src, dest)

        with mock.patch.object(sync, "copy_atomic", slow):
            t = threading.Thread(target=svc.run_once)
            t.start()
            time.sleep(0.2)
            t0 = time.time()
            st = svc.status()
            svc.resync("drilling")
            self.assertLess(time.time() - t0, 1.0)
            self.assertTrue(st["in_pass"])
            gate.set()
            t.join(10)


class CheckPathsTest(Base):
    def test_shape_local(self):
        os.makedirs(self.p("network_mirror", "gcode"))
        os.makedirs(self.p("dropbox", "inbox"))
        cfg = loc(shares={"order_failed": ""})
        items = sync.check_paths(self.root, cfg, cache_s=0)
        self.assertEqual([i["role"] for i in items],
                         ["inbox", "gcode", "drilling", "output", "order_completed", "order_failed"])
        for i in items:
            self.assertTrue({"role", "path", "exists", "writable"} <= set(i))
        by = dict((i["role"], i) for i in items)
        self.assertEqual(by["inbox"]["path"], self.p("dropbox", "inbox"))
        self.assertTrue(by["inbox"]["exists"] and by["inbox"]["writable"])
        self.assertTrue(by["gcode"]["exists"] and by["gcode"]["writable"])
        self.assertEqual(os.listdir(self.p("network_mirror", "gcode")), [], "probe file removed")
        self.assertFalse(by["drilling"]["exists"])
        self.assertTrue(by["order_failed"]["disabled"])
        self.assertEqual(by["order_failed"]["path"], "")

    def test_remote_paths(self):
        seen = []

        def fake(path):                         # never touch the real shares from a test
            seen.append(path)
            return {"exists": False, "writable": False}

        with mock.patch.object(sync, "_probe_dir", fake):
            items = sync.check_paths(self.root, loc("remote"), timeout=2.0, cache_s=0)
        by = dict((i["role"], i) for i in items)
        self.assertEqual(len(seen), 6)
        self.assertEqual(by["output"]["mode"], "remote")
        self.assertEqual(by["inbox"]["path"], "\\\\ddc-mefs\\iBob-Export\\S2S_Export")
        self.assertEqual(by["gcode"]["path"], "\\\\ddc-mefs\\Anderson")

    def test_slow_check_times_out(self):
        slow_path = "\\\\slow-host-%d\\share" % os.getpid()
        cfg = loc("remote", shares={"gcode": slow_path})
        cfg["remote"]["inbox"] = self.p("inbox")
        os.makedirs(self.p("inbox"))
        real = sync._probe_dir
        release = threading.Event()

        def fake(path):
            if path == slow_path:
                release.wait(10)                    # a hung UNC lookup
                return {"exists": False, "writable": False}
            if path.startswith("\\\\"):
                return {"exists": False, "writable": False}
            return real(path)

        try:
            with mock.patch.object(sync, "_probe_dir", fake):
                t0 = time.time()
                items = sync.check_paths(self.root, cfg, timeout=0.5, cache_s=0)
                self.assertLess(time.time() - t0, 2.0)
                by = dict((i["role"], i) for i in items)
                self.assertTrue(by["gcode"].get("timed_out"))
                self.assertFalse(by["gcode"]["exists"])
                self.assertTrue(by["inbox"]["exists"])
                # a second call reuses the hung probe instead of starting another thread
                n = threading.active_count()
                items = sync.check_paths(self.root, cfg, timeout=0.2, cache_s=0)
                self.assertTrue(dict((i["role"], i) for i in items)["gcode"].get("timed_out"))
                self.assertLessEqual(threading.active_count(), n + 4)
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
