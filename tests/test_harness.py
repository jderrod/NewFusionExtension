import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

import helpers
from helpers import Sandbox, order

sys.path.insert(0, os.path.join(helpers.ROOT, "harness"))
import server  # noqa: E402
from nfe.jsonio import write_json_atomic  # noqa: E402


class HarnessRoutes(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.launched, self.killed = [], []
        self.h = server.Harness(root=self.sb.root, fusion_exe="nfe_no_such_process.exe",
                                launch_fn=lambda: self.launched.append(time.time()),
                                kill_fn=lambda pid: self.killed.append(pid))
        self.h.addin_wait_s = 0.5
        self.httpd = server.serve(self.h, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.h.close()
        self.sb.cleanup()

    def raw(self, method, path, body, headers):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=body,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_cross_site_requests_refused(self):
        """A POST with `Content-Type: text/plain` (what a foreign web page can send without
        a CORS preflight) gets 415, and a JSON POST carrying a foreign `Origin` gets
        403; neither writes to the inbox nor launches Fusion. The same request from the
        dashboard's own origin succeeds."""
        body = json.dumps({"name": "x.json", "content": order(doors=1)}).encode()
        # a page on another site: form-style content type, or JSON with a foreign Origin
        self.assertEqual(self.raw("POST", "/api/orders/upload", body, {"Content-Type": "text/plain"}), 415)
        self.assertEqual(self.raw("POST", "/api/control", b'{"action":"restart_fusion"}',
                                  {"Content-Type": "application/json", "Origin": "http://evil.example"}), 403)
        self.assertEqual(os.listdir(self.sb.paths.inbox), [])
        self.assertEqual(self.launched, [])
        # the dashboard itself (same origin) is fine
        ok = self.raw("POST", "/api/orders/upload", body, {"Content-Type": "application/json",
                                                           "Origin": "http://127.0.0.1:%d" % self.port})
        self.assertEqual(ok, 200)

    def test_upload_name_collisions_get_unique_names(self):
        """Three uploads named `same.json` are stored as `same.json`, `same_2.json`,
        `same_3.json` (nothing is overwritten)."""
        data = order(doors=1)
        names = {self.call("POST", "/api/orders/upload", {"name": "same.json", "content": data})[1]["name"]
                 for _ in range(3)}
        self.assertEqual(names, {"same.json", "same_2.json", "same_3.json"})

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                ct = r.headers.get("Content-Type", "")
                raw = r.read()
                return r.status, (json.loads(raw) if "json" in ct else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_dashboard_and_overview(self):
        """`GET /` serves the dashboard (contains `NFE Console`); `GET /api/overview`
        reports Fusion not running, the add-in offline and an empty inbox."""
        st, page = self.call("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(b"NFE Console", page)
        st, ov = self.call("GET", "/api/overview")
        self.assertEqual(st, 200)
        self.assertFalse(ov["fusion"]["running"])
        self.assertFalse(ov["addin"]["online"])
        self.assertEqual(ov["queue"]["inbox"], 0)

    def test_upload_preview_and_listing(self):
        """`POST /api/orders/preview` of 2 doors + 1 stile returns the counts and resolves
        the models `3X8X_Door` and `3X8X_Stile`; `POST /api/orders/upload` with name
        `../../evil name.json` stores `evil_name.json` in the inbox (sanitised); the
        inbox listing shows it; invalid JSON and an order without components are refused
        with 400 (`no components`)."""
        data = order(order_id="3X8X_UP_1", doors=2, stiles=1)
        st, pv = self.call("POST", "/api/orders/preview", {"content": json.dumps(data)})
        self.assertEqual(st, 200)
        self.assertEqual(pv["counts"], {"door": 2, "stile": 1})
        self.assertEqual({c["model"] for c in pv["components"]}, {"3X8X_Door", "3X8X_Stile"})
        st, up = self.call("POST", "/api/orders/upload", {"name": "../../evil name.json", "content": data})
        self.assertEqual(st, 200)
        self.assertEqual(up["name"], "evil_name.json")
        self.assertTrue(os.path.isfile(os.path.join(self.sb.paths.inbox, "evil_name.json")))
        st, lst = self.call("GET", "/api/orders?folder=inbox")
        self.assertEqual([o["name"] for o in lst["orders"]], ["evil_name.json"])
        st, bad = self.call("POST", "/api/orders/upload", {"name": "x.json", "content": "{nope"})
        self.assertEqual(st, 400)
        st, bad = self.call("POST", "/api/orders/upload", {"name": "x.json", "content": {"order_id": "x"}})
        self.assertEqual(st, 400)
        self.assertIn("no components", bad["error"])

    def test_full_cycle_with_engine_then_requeue(self):
        """After the engine processes a 1-door order, `/api/orders?folder=done` shows
        summary `ok`, `/api/orders/report` returns the report, `/files/output/<pdf>`
        (URL-encoded: the run folder name contains spaces) serves bytes starting with
        `%PDF`, and `/api/orders/requeue` puts the file back into the inbox."""
        self.sb.drop("cyc.json", order(doors=1))
        eng = self.sb.make_engine(helpers.FakeOps(helpers.standard_files()))
        eng.set_inbox_hint(True)
        self.sb.run(eng)
        st, lst = self.call("GET", "/api/orders?folder=done")
        self.assertEqual(lst["orders"][0]["summary"]["status"], "ok")
        st, rep = self.call("GET", "/api/orders/report?folder=done&name=cyc.json")
        pdf = rep["components"][0]["pdf"]
        st, raw = self.call("GET", "/files/output/" + urllib.request.quote(pdf))
        self.assertEqual(st, 200)
        self.assertTrue(raw.startswith(b"%PDF"))
        st, _ = self.call("POST", "/api/orders/requeue", {"folder": "done", "name": "cyc.json"})
        self.assertEqual(st, 200)
        self.assertTrue(os.path.isfile(os.path.join(self.sb.paths.inbox, "cyc.json")))

    def test_output_path_traversal_blocked(self):
        """`..%2F`, plain `../`, `%2E%2E` sequences under `/files/output/` and
        `/static/../server.py` all return 404."""
        for p in ("/files/output/..%2Fconfig%2Fconfig.json", "/files/output/../config/config.json",
                  "/files/output/%2E%2E/%2E%2E/Windows/win.ini", "/static/../server.py"):
            st, _ = self.call("GET", p)
            self.assertEqual(st, 404, p)

    def test_config_roundtrip_and_validation(self):
        """`GET /api/config` + `PUT /api/config` with `save_drawing = true` persists the
        change; adding a rule of type `gate` is rejected with 400 (`type must be`)."""
        st, cfg = self.call("GET", "/api/config")
        cfg["processing"]["save_drawing"] = True
        st, saved = self.call("PUT", "/api/config", cfg)
        self.assertEqual(st, 200)
        self.assertTrue(saved["processing"]["save_drawing"])
        cfg["rules"].append({"id": "x", "type": "gate", "model": {}})
        st, err = self.call("PUT", "/api/config", cfg)
        self.assertEqual(st, 400)
        self.assertIn("type must be", err["error"])

    def test_addin_proxy_reports_offline(self):
        """`/api/hub/hubs` with no add-in listening returns 503 with `not reachable`."""
        st, err = self.call("GET", "/api/hub/hubs")
        self.assertEqual(st, 503)
        self.assertIn("not reachable", err["error"])


class SupervisorPolicy(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.launched = []
        self.h = server.Harness(root=self.sb.root, fusion_exe="nfe_no_such_process.exe",
                                launch_fn=lambda: self.launched.append(1), kill_fn=lambda pid: None)
        self.h.addin_wait_s = 0.2
        self.h.post_kill_sleep_s = 0
        self.h.ping_interval_s = 0.05
        self.h.cfg["supervisor"]["restart_cooldown_s"] = 0

    def tearDown(self):
        self.h.close()
        self.sb.cleanup()

    def status(self, **kw):
        base = {"addin_pid": 4242, "heartbeat_at": time.time(), "state": "processing"}
        base.update(kw)
        write_json_atomic(self.sb.paths.status_file, base)

    def wait_restart(self):
        for _ in range(400):
            if not self.h.restart_in_progress:
                return
            time.sleep(0.05)
        self.fail("restart worker did not finish")

    def test_healthy_no_action(self):
        """Fusion running with the add-in's pid and a fresh heartbeat: `check_policies`
        returns `None`."""
        self.h.fusion = {"running": True, "pid": 4242}
        self.status()
        self.assertIsNone(self.h.check_policies())

    def test_restart_requested(self):
        """`restart_requested` with reason `threads 5000 > 4000` triggers a restart whose
        reason contains that text; the worker calls `launch_fn` once and, with no add-in
        answering, records `add-in did not come back ...`."""
        self.h.fusion = {"running": True, "pid": 4242}
        self.status(restart_requested=True, restart_reason="threads 5000 > 4000")
        reason = self.h.check_policies()
        self.assertIn("threads 5000", reason)
        self.wait_restart()
        self.assertEqual(self.launched, [1])
        self.assertIn("did not come back", self.h.restarts[-1]["result"])

    def test_stale_heartbeat_and_blocked_ui(self):
        """A heartbeat 500 s old triggers `heartbeat stale`; after that restart,
        `ui_blocked_s = 5000` in step `open_drawing` triggers `blocked`."""
        self.h.fusion = {"running": True, "pid": 4242}
        self.status(heartbeat_at=time.time() - 500)
        self.assertIn("heartbeat stale", self.h.check_policies())
        self.wait_restart()
        self.status(ui_blocked_s=5000, component={"step": "open_drawing"})
        self.assertIn("blocked", self.h.check_policies())
        self.wait_restart()

    def test_crash_vs_clean_exit(self):
        """Fusion not running and never seen with the add-in: no action. After it was seen
        (`_seen_pid_with_addin`) and state is `processing`: restart with `crashed`. With
        state `stopped` (normal close): no action."""
        self.h.fusion = {"running": False}
        self.status(state="processing")
        self.assertIsNone(self.h.check_policies())       # never saw it running with the add-in
        self.h._seen_pid_with_addin = 4242
        self.assertIn("crashed", self.h.check_policies())
        self.wait_restart()
        self.h._seen_pid_with_addin = 4242
        self.status(state="stopped")                     # user closed Fusion normally
        self.assertIsNone(self.h.check_policies())

    def test_disabled_and_rate_limited(self):
        """`auto_restart_fusion = false` suppresses a requested restart; with 6 restarts
        already recorded in the last hour (`MAX_RESTARTS_PER_HOUR`) no restart happens
        either."""
        self.h.fusion = {"running": True, "pid": 4242}
        self.status(restart_requested=True)
        self.h.cfg["supervisor"]["auto_restart_fusion"] = False
        self.assertIsNone(self.h.check_policies())
        self.h.cfg["supervisor"]["auto_restart_fusion"] = True
        for _ in range(server.MAX_RESTARTS_PER_HOUR):
            self.h.restarts.append({"at": time.time(), "reason": "x", "result": "ok"})
        self.assertIsNone(self.h.check_policies())



class FusionDialogs(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.h = server.Harness(root=self.sb.root, fusion_exe="nfe_no_such_process.exe",
                                launch_fn=lambda: None, kill_fn=lambda pid: None)
        self.h.fusion = {"running": True, "pid": 4242}
        self.closed, self.clicked = [], []

    def tearDown(self):
        self.h.close()
        self.sb.cleanup()

    def check(self, dialog):
        return self.h.check_dialogs(find=lambda pid: dialog, close=self.closed.append,
                                    click=lambda hwnd, x, y: self.clicked.append((hwnd, round(x), round(y))))

    def addin_loaded(self, pid=4242):
        write_json_atomic(self.sb.paths.status_file, {"state": "processing", "addin_pid": pid,
                                                      "heartbeat_at": time.time()})

    def test_upload_error_is_answered_with_open(self):
        """With the add-in loaded in the Fusion process, an `Upload Error` dialog of
        500x176 is clicked at (456, 143) - the `Open` button, not `Save As` - and the
        overview reports `fusion.dialog`. The same dialog is answered at most three
        times; when it is gone the overview clears."""
        self.addin_loaded()
        dlg = {"hwnd": 77, "title": "Upload Error", "width": 500, "height": 176}
        self.assertEqual(self.check(dlg)["action"], "click")
        self.assertEqual(self.clicked, [(77, 456, 143)])
        self.assertEqual(self.h.overview()["fusion"]["dialog"], "Upload Error")
        for _ in range(5):
            self.check(dlg)
        self.assertEqual(len(self.clicked), 3)
        self.check(None)
        self.assertIsNone(self.h.overview()["fusion"]["dialog"])

    def test_recovered_documents_is_closed_and_unknown_dialogs_are_left(self):
        """The untitled-by-Fusion `Fusion360` dialog (Recovered Documents) gets the X
        button. A dialog with an unlisted title is only reported. Nothing is touched
        before the add-in has loaded in that Fusion process (sign-in, licence), or
        when `supervisor.dismiss_dialogs` is off."""
        dlg = {"hwnd": 5, "title": "Fusion360", "width": 580, "height": 420}
        self.assertIsNone(self.check(dlg))                 # add-in not loaded yet
        self.addin_loaded(pid=1)                           # loaded in an older Fusion process
        self.assertIsNone(self.check(dlg))
        self.assertEqual(self.closed, [])
        self.addin_loaded()
        self.assertEqual(self.check(dlg)["action"], "close")
        self.assertEqual(self.closed, [5])
        self.assertIsNone(self.check({"hwnd": 6, "title": "Something else", "width": 300, "height": 200}))
        self.assertEqual(self.h.overview()["fusion"]["dialog"], "Something else")
        self.h.cfg["supervisor"]["dismiss_dialogs"] = False
        self.assertIsNone(self.check({"hwnd": 8, "title": "Upload Error", "width": 500, "height": 176}))
        self.assertEqual((self.closed, self.clicked), ([5], []))


if __name__ == "__main__":
    unittest.main()
