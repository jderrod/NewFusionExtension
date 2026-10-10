import json
import os
import unittest

import helpers
from helpers import FakeFile, FakeOps, Sandbox, order
from nfe.engine import Sleep, StepTimeout, Task, WaitFor
from nfe.jsonio import read_json


def report_for(sb, folder, name):
    return read_json(os.path.join(getattr(sb.paths, folder), os.path.splitext(name)[0] + ".report.json"))


class TaskRunner(unittest.TestCase):
    def test_sleep_and_waitfor(self):
        """Drives a `Task` with a `FakeClock`: `Sleep(5)` does not resume after 4 s but
        does after 5 s; `WaitFor` resumes with the condition's truthy value once it
        holds; a `WaitFor` that never holds throws `StepTimeout("timed out after 3s
        waiting for never")` into the generator; a `WaitFor(...,
        raise_on_timeout=False)` resumes with `False`, which becomes `Task.result`."""
        clock = helpers.FakeClock()
        flag = {"v": False}
        seen = []

        def gen():
            seen.append("a")
            yield Sleep(5)
            seen.append("b")
            got = yield WaitFor(lambda: flag["v"], 10, "flag")
            seen.append(("c", got))
            try:
                yield WaitFor(lambda: False, 3, "never")
            except StepTimeout as e:
                seen.append(str(e))
            ok = yield WaitFor(lambda: False, 1, "soft", raise_on_timeout=False)
            return ok

        t = Task("t", gen(), clock)
        t.advance()
        self.assertEqual(seen, ["a"])
        clock.t += 4
        t.advance()
        self.assertEqual(seen, ["a"])
        clock.t += 1
        t.advance()
        self.assertEqual(seen, ["a", "b"])
        t.advance()
        flag["v"] = True
        t.advance()
        self.assertEqual(seen[-1], ("c", True))
        clock.t += 3
        t.advance()
        self.assertIn("timed out after 3s waiting for never", seen[-1])
        clock.t += 1
        t.advance()
        self.assertTrue(t.done)
        self.assertIs(t.result, False)

    def test_exception_in_condition_is_raised_inside_generator(self):
        """An exception raised by a `WaitFor` condition is thrown into the generator at the
        `yield`, where it can be caught (the task result is `"caught"`)."""
        clock = helpers.FakeClock()

        def boom():
            raise ValueError("bad")

        def gen():
            try:
                yield WaitFor(boom, 5, "x")
            except ValueError:
                return "caught"
        t = Task("t", gen(), clock)
        t.advance()
        t.advance()
        self.assertEqual(t.result, "caught")


class EngineFlow(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.cleanup()

    def test_example_order_end_to_end(self):
        """The fixture order goes inbox → done with status `ok`; `D1` is `done` with
        `model_version` 2 (one save); the PDF is `1-D1-3X8X_DV_0622-drawing.pdf` inside
        the run folder `3X8X_DV_0622 <Mon D YYYY - HHMM>`; applied values read
        `88.5625 in` and `1`; the steps are exactly open_model, apply_parameters,
        save_model, drilling, plan_toolpaths, manufacture_workspace, generate_toolpaths,
        post_process, splice_gcode, open_drawing, update_drawing, export_pdf, cleanup;
        `3X8X_DV_0622-report.json` exists; inbox and processing are empty afterwards."""
        with open(helpers.EXAMPLE_ORDER) as fh:
            self.sb.drop("example.json", json.load(fh))
        ops = FakeOps(helpers.standard_files())
        eng = self.sb.make_engine(ops)
        eng.set_inbox_hint(True)
        self.sb.run(eng)

        rep = report_for(self.sb, "done", "example.json")
        self.assertEqual(rep["summary"]["status"], "ok", rep)
        c = rep["components"][0]
        self.assertEqual(c["status"], "done")
        self.assertEqual(c["model_version"], 2)                # saved once
        pdf = os.path.join(self.sb.paths.output, c["pdf"])
        self.assertTrue(os.path.isfile(pdf))
        self.assertEqual(os.path.basename(pdf), "1-D1-3X8X_DV_0622-drawing.pdf")
        self.assertRegex(os.path.basename(os.path.dirname(pdf)), r"^3X8X_DV_0622 [A-Z][a-z]{2} \d{1,2} \d{4} - \d{4}$")
        applied = {p["name"]: p["after"] for p in c["parameters"]}
        self.assertEqual(applied["component_height"], "88.5625 in")
        self.assertEqual(applied["door_hinging_right"], "1")
        self.assertEqual([s["name"] for s in c["steps"]],
                         ["open_model", "apply_parameters", "save_model", "drilling", "plan_toolpaths",
                          "manufacture_workspace", "generate_toolpaths", "post_process", "splice_gcode",
                          "open_drawing", "update_drawing", "export_pdf", "cleanup"])
        # output report + nothing left in inbox/processing
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(pdf), "3X8X_DV_0622-report.json")))
        self.assertEqual(os.listdir(self.sb.paths.processing), [])
        self.assertEqual(os.listdir(self.sb.paths.inbox), [])

    def test_garbage_collection_keeps_document_count_flat(self):
        """Hidden reference copies + closes that silently fail must not accumulate."""
        self.sb.drop("big.json", order(doors=12, stiles=6))
        ops = FakeOps(helpers.standard_files(), spawn_hidden_copies=True, stubborn_closes=1)
        eng = self.sb.make_engine(ops)
        eng.set_inbox_hint(True)
        counts = []
        for _ in range(20000):
            if not eng.has_work():
                break
            eng.tick()
            self.sb.clock.t += 0.5
            if eng.state.get("component") and eng.state.get("component", {}).get("step") == "cleanup":
                counts.append(ops.document_count())
        rep = report_for(self.sb, "done", "big.json")
        self.assertEqual(rep["summary"]["done"], 18, rep["summary"])
        # after each order only the cached model may remain open
        self.assertLessEqual(ops.document_count(), 1)
        self.assertLessEqual(max(counts), 4)
        hidden_alive = [d for d in ops.docs if d.alive and not d.visible]
        self.assertEqual(hidden_alive, [])

    def test_grouping_minimises_model_opens(self):
        """With CAM off, components interleaved `D1 S1 D2 S2 D3 S3` (under a generic
        `components` key, typed by id prefix) are regrouped so each model is opened
        exactly once: model opens are `["3X8X_Door", "3X8X_Stile"]`. (With CAM on, a
        model that went through CAM is discarded, see test_cam_flow.)"""
        self.sb.cleanup()
        self.sb = Sandbox(extra={"cam": {"enabled": False}})
        data = order(doors=3, stiles=3)
        # interleave the JSON: D1 S1 D2 S2 ...
        mixed = {"order_id": data["order_id"], "components": []}
        for d, s in zip(data["doors"], data["stiles"]):
            mixed["components"] += [d, s]
        self.sb.drop("mixed.json", mixed)
        ops = FakeOps(helpers.standard_files())
        eng = self.sb.make_engine(ops)
        eng.set_inbox_hint(True)
        self.sb.run(eng)
        opens = [n for op, n in ops.log if op == "open" and not n.endswith("Drawing")]
        self.assertEqual(opens, ["3X8X_Door", "3X8X_Stile"])

    def test_component_failures_are_isolated(self):
        """In a 2-door order where `D2` has series `9999` (no rule), `D1` is still `done`,
        `D2` fails with `no series rule`, and the order lands in `failed/` with status
        `partial`."""
        data = order(doors=2)
        data["doors"][1]["parameters"]["series_id"] = ["9999", "string", ""]   # no rule
        self.sb.drop("partial.json", data)
        ops = FakeOps(helpers.standard_files())
        eng = self.sb.make_engine(ops)
        eng.set_inbox_hint(True)
        self.sb.run(eng)
        rep = report_for(self.sb, "failed", "partial.json")
        self.assertEqual(rep["summary"]["status"], "partial")
        st = {c["id"]: c for c in rep["components"]}
        self.assertEqual(st["D1"]["status"], "done")
        self.assertEqual(st["D2"]["status"], "failed")
        self.assertIn("no series rule", st["D2"]["error"])

    def test_invalid_json_is_rejected(self):
        """A file containing `{not json` is moved to `failed/` with a report whose status
        is `rejected`."""
        with open(os.path.join(self.sb.paths.inbox, "bad.json"), "w") as fh:
            fh.write("{not json")
        os.utime(os.path.join(self.sb.paths.inbox, "bad.json"), (0, 0))
        eng = self.sb.make_engine(FakeOps(helpers.standard_files()))
        eng.set_inbox_hint(True)
        self.sb.run(eng)
        rep = report_for(self.sb, "failed", "bad.json")
        self.assertEqual(rep["summary"]["status"], "rejected")

    def test_save_timeout_fails_component_and_discards_dirty_model(self):
        """With cloud saves that never complete (5 s per tick), both components fail with
        `timed out`, and no documents remain open: the dirty model is closed, not
        reused."""
        self.sb.drop("slow.json", order(doors=2))
        ops = FakeOps(helpers.standard_files(), save_ticks=10 ** 9)
        eng = self.sb.make_engine(ops)
        eng.set_inbox_hint(True)
        self.sb.run(eng, step=5.0)
        rep = report_for(self.sb, "failed", "slow.json")
        self.assertEqual(rep["summary"]["failed"], 2)
        self.assertIn("timed out", rep["components"][0]["error"])
        self.assertEqual(ops.document_count(), 0)      # dirty models closed, not reused

    def test_unknown_parameter_policy(self):
        """An order parameter `mystery` that the model lacks is listed in
        `unknown_parameters`, produces a warning, and (default policy `warn`) the order
        still finishes in `done/`."""
        self.sb.drop("extra.json", order(doors=1, extra={"mystery": [1, "int", ""]}))
        ops = FakeOps(helpers.standard_files())
        eng = self.sb.make_engine(ops)
        eng.set_inbox_hint(True)
        self.sb.run(eng)
        rep = report_for(self.sb, "done", "extra.json")
        self.assertEqual(rep["components"][0]["unknown_parameters"], ["mystery"])
        self.assertTrue(rep["components"][0]["warnings"])

    def test_resume_after_crash_skips_finished_components(self):
        """A 3-door order is interrupted while `D2` is in `save_model` (`Task.close()`
        simulates Fusion dying); a new engine with a fresh `FakeOps` on the same folders
        resumes it: all 3 done, `attempts` = `{D1: 1, D2: 2, D3: 1}`, and the new engine
        saves only twice (D1 is not redone)."""
        self.sb.drop("resume.json", order(doors=3))
        ops = FakeOps(helpers.standard_files())
        eng = self.sb.make_engine(ops)
        eng.set_inbox_hint(True)
        # run until D1 is done and D2 has started, then "crash"
        for _ in range(10000):
            eng.tick()
            self.sb.clock.t += 0.5
            cur = eng.state.get("component") or {}
            if cur.get("id") == "D2" and cur.get("step") == "save_model":
                break
        eng._task.close()     # Fusion died
        ops2 = FakeOps(helpers.standard_files())
        eng2 = self.sb.make_engine(ops2)
        self.sb.run(eng2)
        rep = report_for(self.sb, "done", "resume.json")
        self.assertEqual(rep["summary"]["done"], 3)
        att = {c["id"]: c["attempts"] for c in rep["components"]}
        self.assertEqual(att, {"D1": 1, "D2": 2, "D3": 1})
        saves = [n for op, n in ops2.log if op == "save"]
        self.assertEqual(len(saves), 2)        # D1 was not redone

    def test_crash_loop_protection(self):
        """An order placed in `processing/` with a checkpoint saying `D1` was `in_progress`
        with 2 attempts is not retried: the report in `failed/` has error `... giving
        up`."""
        path = self.sb.drop("loop.json", order(doors=1))
        proc = os.path.join(self.sb.paths.processing, "loop.json")
        os.replace(path, proc)
        with open(os.path.splitext(proc)[0] + ".state.json", "w") as fh:
            json.dump({"components": {"D1": {"status": "in_progress", "attempts": 2}}}, fh)
        eng = self.sb.make_engine(FakeOps(helpers.standard_files()))
        self.sb.run(eng)
        rep = report_for(self.sb, "failed", "loop.json")
        self.assertIn("giving up", rep["components"][0]["error"])

    def test_pause_stops_at_component_boundary(self):
        """Pausing after the first component started lets it finish, then holds: statuses
        `[done, pending, pending]` after 2000 more ticks; resume finishes all three."""
        self.sb.drop("p.json", order(doors=3))
        ops = FakeOps(helpers.standard_files())
        eng = self.sb.make_engine(ops)
        eng.set_inbox_hint(True)
        while not eng.state.get("component"):
            eng.tick()
            self.sb.clock.t += 0.5
        eng.pause("test")
        self.assertEqual(eng.state.get("state"), "paused")      # not "processing"
        for _ in range(2000):
            eng.tick()
            self.sb.clock.t += 0.5
        comps = eng.state.get("order")["components"]
        self.assertEqual([c["status"] for c in comps], ["done", "pending", "pending"])
        eng.resume()
        self.sb.run(eng)
        self.assertEqual(report_for(self.sb, "done", "p.json")["summary"]["done"], 3)

    def test_health_threshold_requests_restart(self):
        """With `health.max_threads = 290` (FakeOps reports 300 + 40 per open document),
        the engine sets `restart_requested` after the first component, publishes state
        `waiting_restart`, and holds `D2` as `pending`."""
        self.sb.cleanup()
        self.sb = Sandbox(health={"max_threads": 290})
        self.sb.drop("h.json", order(doors=3))
        ops = FakeOps(helpers.standard_files())
        eng = self.sb.make_engine(ops)
        eng.set_inbox_hint(True)
        for _ in range(3000):      # the order waits at the boundary for the restart
            eng.tick()
            self.sb.clock.t += 0.5
        self.assertTrue(eng.restart_requested)
        self.assertEqual(eng.state.get("state"), "waiting_restart")
        comps = eng.state.get("order")["components"]
        self.assertEqual(comps[0]["status"], "done")
        self.assertEqual(comps[1]["status"], "pending")    # held for the restart

    def test_waits_for_fusion_ready_and_retries_lookups(self):
        """Right after a Fusion restart the hub is not available yet."""
        self.sb.drop("boot.json", order(doors=2))
        ops = FakeOps(helpers.standard_files(), not_ready_polls=20, resolve_failures=3)
        eng = self.sb.make_engine(ops)
        eng.set_inbox_hint(True)
        seen_waiting = False
        for _ in range(5000):
            if not eng.has_work():
                break
            eng.tick()
            self.sb.clock.t += 0.5
            seen_waiting = seen_waiting or eng.state.get("state") == "waiting_fusion"
        self.assertTrue(seen_waiting)
        rep = report_for(self.sb, "done", "boot.json")
        self.assertEqual(rep["summary"]["done"], 2)

    def test_scheduled_refresh(self):
        """With `health.restart_every_components = 2`, a 4-door order stops after two
        components with `restart_reason` containing `scheduled refresh after 2` and
        statuses `[done, done, pending, pending]`."""
        self.sb.cleanup()
        self.sb = Sandbox(health={"restart_every_components": 2})
        self.sb.drop("r.json", order(doors=4))
        eng = self.sb.make_engine(FakeOps(helpers.standard_files()))
        eng.set_inbox_hint(True)
        for _ in range(3000):
            eng.tick()
            self.sb.clock.t += 0.5
        self.assertIn("scheduled refresh after 2", eng.restart_reason)
        self.assertEqual([c["status"] for c in eng.state.get("order")["components"]],
                         ["done", "done", "pending", "pending"])
        # operator declines the refresh (Resume): a new interval starts, no immediate re-ask
        eng.resume()
        for _ in range(3000):
            if eng.state.get("order")["components"][2]["status"] == "done":
                break
            eng.tick()
            self.sb.clock.t += 0.5
        self.assertFalse(eng.restart_requested)

    def test_submit_runs_on_tick_thread(self):
        """`Engine.submit` called from a thread named `http` returns the name of the thread
        that called `tick()` (the test's main thread), proving the function ran on the
        tick thread."""
        import threading
        eng = self.sb.make_engine(FakeOps(helpers.standard_files()))
        out = {}

        def caller():
            out["v"] = eng.submit(lambda: threading.current_thread().name, timeout=5)
        th = threading.Thread(target=caller, name="http")
        th.start()
        while th.is_alive():
            if eng.has_work():
                eng.tick()
        self.assertEqual(out["v"], threading.current_thread().name)



class ModelSettle(unittest.TestCase):
    def tearDown(self):
        self.sb.cleanup()

    def gap(self, settle):
        """Seconds between D1's save completing and D2's parameters being applied."""
        self.sb = Sandbox(processing={"model_settle_s": settle})
        self.sb.drop("two.json", order(doors=2))
        eng = self.sb.make_engine(FakeOps(helpers.standard_files()))
        eng.set_inbox_hint(True)
        self.sb.run(eng)
        rep = report_for(self.sb, "done", "two.json")
        self.assertEqual(rep["summary"]["status"], "ok", rep)
        d1, d2 = rep["components"]
        after_save = sum(s["duration_s"] for s in d1["steps"][3:])
        return after_save + d2["steps"][0]["duration_s"] + d2["steps"][1]["duration_s"]

    def test_same_model_is_not_changed_again_before_the_settle_time(self):
        """Two doors on one model with `processing.model_settle_s` = 60: D2's
        parameters are applied at least 60 s after D1's save completed (the wait
        is spent in D2's `apply_parameters` step). With 0 there is no wait."""
        self.assertGreaterEqual(self.gap(60), 60)
        self.sb.cleanup()
        self.assertLess(self.gap(0), 30)


if __name__ == "__main__":
    unittest.main()
