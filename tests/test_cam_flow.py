"""Engine runs through the manufacture (CAM), drilling and naming steps
against FakeOps models that mirror the live setups (see helpers)."""

import glob
import json
import os
import unittest

import helpers
from helpers import FakeOps, Sandbox, order
from nfe.jsonio import read_json


def stile_order(order_id="3X8X_SV_0001", **flags):
    p = {"series_id": ["3082G.67P", "string", ""], "component_height": [70.0, "float", ""],
         "component_width": [3.0, "float", ""]}
    for k, v in flags.items():
        p[k] = [v, "bool", ""]
    return {"order_id": [order_id, "string", ""], "stiles": [{"id": ["S1", "string", ""], "parameters": p}]}


class CamFlow(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.cleanup()

    def run_order(self, name, data, ops=None):
        self.sb.drop(name, data)
        ops = ops or FakeOps(helpers.standard_files())
        eng = self.sb.make_engine(ops)
        eng.set_inbox_hint(True)
        self.sb.run(eng)
        stem = os.path.splitext(name)[0]
        rep = (read_json(os.path.join(self.sb.paths.done, stem + ".report.json"))
               or read_json(os.path.join(self.sb.paths.failed, stem + ".report.json")))
        return ops, rep

    def order_dir(self, rep):
        return os.path.join(self.sb.paths.output, rep["summary"]["output_dir"])

    def test_door_posts_selected_setup_and_names_files(self):
        """A right-hand in-swing door matches row D12: only `Setup 8 - G59` is
        unsuppressed, generated and posted; the G-code `1-D1-<order>.txt` holds the
        right exterior rabbet and no notch, ends `M30`/`%` with CRLF, sits next to
        `1-D1-<order>-drawing.pdf`; the workspace goes MANUFACTURE → DESIGN around
        generate/post; the `_nc` scratch folder is removed."""
        # RH in-swing door -> right exterior rabbet -> in-swing right-only row -> Setup 8 - G59
        ops, rep = self.run_order("door.json", order(order_id="3X8X_DV_0001", doors=1))
        self.assertEqual(rep["summary"]["status"], "ok", rep["components"][0].get("error"))
        c = rep["components"][0]
        tp = c["toolpaths"]
        self.assertEqual(tp["setups"], ["Setup 8 - G59"])
        self.assertEqual(tp["sequence"], ["Setup 8 - G59"])
        self.assertIn("unsuppressed setup Setup 8 - G59", tp["changes"])
        self.assertEqual(os.path.basename(c["gcode"]), "1-D1-3X8X_DV_0001.txt")
        self.assertEqual(os.path.basename(c["pdf"]), "1-D1-3X8X_DV_0001-drawing.pdf")
        self.assertEqual(os.path.dirname(c["gcode"]), os.path.dirname(c["pdf"]))
        with open(os.path.join(self.sb.paths.output, c["gcode"]), "rb") as fh:
            g = fh.read().decode("ascii")
        self.assertIn("(RIGHT EXTERIOR RABBET)", g)
        self.assertNotIn("NOTCH", g)                       # notch ops stayed suppressed
        self.assertTrue(g.endswith("M30\r\n%\r\n"))
        # workspace: manufacture for CAM, back to design; generated before posted
        kinds = [e for e in ops.log if e[0] in ("workspace", "generate", "post")]
        self.assertEqual(kinds, [("workspace", "cam"), ("generate", ("Setup 8 - G59",)),
                                 ("post", "Setup 8 - G59"), ("workspace", "design")])
        # the per-setup .nc files are cleaned up
        self.assertFalse(os.path.exists(os.path.join(self.order_dir(rep), "_nc")))

    def test_two_setups_are_spliced_in_order(self):
        """A stile with in-swing doors on both sides posts `Right Rabbet - In G57` then
        `Left Rabbet - In G59`, spliced with `(NEW SETUP, ROTATE PART 180)`, the
        right-side section first, and N numbers renumbered N10, N15, …; the report's
        splice list matches."""
        # both doors in-swing -> Right In G57 then Left In G59 (rotate between)
        ops, rep = self.run_order("stile.json", stile_order(
            left_side_door=True, right_side_door=True, LD_swinging_out=False, RD_swinging_out=False,
            LD_hinging_right=True, RD_hinging_right=False))
        c = rep["components"][0]
        self.assertEqual(c["status"], "done", c.get("error"))
        self.assertEqual(c["toolpaths"]["sequence"], ["Right Rabbet - In G57", "Left Rabbet - In G59"])
        with open(os.path.join(self.sb.paths.output, c["gcode"]), "rb") as fh:
            g = fh.read().decode("ascii")
        self.assertIn("(NEW SETUP, ROTATE PART 180)", g)
        self.assertLess(g.index("(RIGHT RABBET IN)"), g.index("(LEFT RABBET IN)"))
        nums = [int(l.split()[0][1:]) for l in g.split("\r\n") if l.startswith("N")]
        self.assertEqual(nums, list(range(10, 10 + 5 * len(nums), 5)))   # renumbered N10, N15, ...
        self.assertEqual([s["setup"] for s in c["toolpaths"]["splice"]],
                         ["Right Rabbet - In G57", "Left Rabbet - In G59"])

    def test_no_toolpaths_skips_manufacture_entirely(self):
        """A stile without doors needs no toolpaths: the result says 'no toolpaths needed',
        no G-code is written, the workspace is never switched and the step list goes
        from plan_toolpaths straight to open_drawing."""
        ops, rep = self.run_order("plain.json", stile_order())       # stile without doors
        c = rep["components"][0]
        self.assertEqual(c["status"], "done")
        self.assertEqual(c["toolpaths"]["result"], "no toolpaths needed")
        self.assertIsNone(c.get("gcode"))
        self.assertFalse([e for e in ops.log if e[0] in ("workspace", "generate", "post")])
        names = [s["name"] for s in c["steps"]]
        self.assertNotIn("manufacture_workspace", names)
        self.assertLess(names.index("plan_toolpaths"), names.index("open_drawing"))

    def test_drilling_file_links_to_component_files(self):
        """An order with two doors and a stile writes `<order>.json` with doors D1, D2 and
        stile S1; every record's ProgramName equals the component's G-code stem; a
        right-hand in-swing door drills on WS4 with an air-drill record on WS1; copies
        land in machine_drops/gannomat and machine_drops/anderson."""
        data = order(order_id="3X8X_MX_0001", doors=2)
        data.update({k: v for k, v in stile_order(
            left_side_door=True, LD_hinging_right=True, LD_swinging_out=False).items() if k == "stiles"})
        ops, rep = self.run_order("mix.json", data)
        self.assertEqual(rep["summary"]["status"], "ok", [c.get("error") for c in rep["components"]])
        path = os.path.join(self.order_dir(rep), "3X8X_MX_0001.json")
        compiled = read_json(path)
        body = compiled["3X8X_MX_0001"]
        self.assertEqual([d["component_id"] for d in body["doors"]], ["D1", "D2"])
        self.assertEqual([s["component_id"] for s in body["stiles"]], ["S1"])
        progs = {r["ProgramName"] for d in body["doors"] + body["stiles"] for r in d["coordinates"]}
        self.assertEqual(progs, {"1-D1-3X8X_MX_0001", "1-D2-3X8X_MX_0001", "1-S1-3X8X_MX_0001"})
        door_ws = {r["WorkStop"] for r in body["doors"][0]["coordinates"]}
        self.assertEqual(door_ws, {1, 4})       # RH in-swing door on WS4 + air drill on WS1
        self.assertEqual(rep["outputs"]["drilling_components"], 3)
        # flat machine staging copies for the sync service
        self.assertTrue(os.path.isfile(os.path.join(self.sb.root, "machine_drops", "gannomat", "3X8X_MX_0001.json")))
        self.assertTrue(glob.glob(os.path.join(self.sb.root, "machine_drops", "anderson", "1-D1-3X8X_MX_0001.txt")))

    def test_missing_setup_fails_component_with_clear_error(self):
        """When the model lacks the chosen setup the component fails with 'setup(s) not
        found in the model: Setup 8 - G59' and cleanup still returns to the DESIGN
        workspace."""
        files = helpers.standard_files()
        files[0].cam = [s for s in files[0].cam if s["name"] != "Setup 8 - G59"]
        ops, rep = self.run_order("miss.json", order(order_id="3X8X_DV_0002", doors=1), FakeOps(files))
        c = rep["components"][0]
        self.assertEqual(c["status"], "failed")
        self.assertIn("setup(s) not found in the model: Setup 8 - G59", c["error"])
        self.assertIn(("workspace", "design"), ops.log)     # cleanup always leaves MANUFACTURE

    def test_model_discarded_after_cam(self):
        """CAM leaves the model modified, so it is closed without saving after each
        component: two doors open the model twice and no document stays open."""
        ops, rep = self.run_order("two.json", order(order_id="3X8X_DV_0003", doors=2))
        opens = [n for op, n in ops.log if op == "open" and n == "3X8X_Door"]
        self.assertEqual(len(opens), 2)          # CAM left the model modified -> reopened clean
        self.assertEqual(ops.document_count(), 0)

    def test_panel_cutout_split_and_panel_setup(self):
        """A panel with `cutout` 'B-354 & B-386' gets cutout_A 'B-354' and cutout_B 'B-386'
        (not reported as unknown); height ≥ width posts `Setup Back Bottom Corner G58`
        with both cutouts active and `Adaptive1` suppressed."""
        data = {"order_id": ["3X8X_PV_0001", "string", ""], "panels": [
            {"id": ["P1", "string", ""], "parameters": {
                "series_id": ["3082G.67P", "string", ""], "component_height": [60.0, "float", ""],
                "component_width": [30.0, "float", ""], "cutout": ["B-354 & B-386", "string", ""]}}]}
        ops, rep = self.run_order("panel.json", data)
        c = rep["components"][0]
        self.assertEqual(c["status"], "done", c.get("error"))
        applied = {p["name"]: p["after"] for p in c["parameters"]}
        self.assertEqual(applied["cutout_A"], "'B-354'")
        self.assertEqual(applied["cutout_B"], "'B-386'")
        self.assertNotIn("cutout", c["unknown_parameters"])
        self.assertEqual(c["toolpaths"]["sequence"], ["Setup Back Bottom Corner G58"])   # height >= width
        with open(os.path.join(self.sb.paths.output, c["gcode"]), "rb") as fh:
            g = fh.read().decode("ascii")
        self.assertIn("(CUTOUT A)", g)
        self.assertIn("(CUTOUT B)", g)
        self.assertNotIn("ADAPTIVE1", g)          # unknown op suppressed

    def test_order_folder_naming_and_resume_reuses_folder(self):
        """The run folder is named `<order_id> <Mon D YYYY - HHMM>` and holds
        `<order_id>-report.json`."""
        ops, rep = self.run_order("n.json", order(order_id="3X8X_DV_0004", doors=1))
        folder = rep["summary"]["output_dir"]
        self.assertRegex(folder, r"^3X8X_DV_0004 [A-Z][a-z]{2} \d{1,2} \d{4} - \d{4}$")
        self.assertTrue(os.path.isfile(os.path.join(self.order_dir(rep), "3X8X_DV_0004-report.json")))

    def test_stale_model_version_is_reopened(self):
        """When the hub hands out the previous model version once (as seen live), the
        engine closes it and reopens: the order finishes ok with model versions 2 then 3
        and three door opens."""
        ops = FakeOps(helpers.standard_files())
        ops.stale_opens = 1                  # the 2nd door open returns the old version once
        ops, rep = self.run_order("stale.json", order(order_id="3X8X_DV_0010", doors=2), ops)
        self.assertEqual(rep["summary"]["status"], "ok", [c.get("error") for c in rep["components"]])
        opens = [n for op, n in ops.log if op == "open" and n == "3X8X_Door"]
        self.assertEqual(len(opens), 3)      # D1, stale D2, fresh D2
        self.assertEqual([c["model_version"] for c in rep["components"]], [2, 3])

    def test_stale_model_version_fails_fast(self):
        """When the hub keeps returning an old version, the component fails with 'keeps
        returning 3X8X_Door v1 but v2 was saved' in well under the 300 s save timeout."""
        ops = FakeOps(helpers.standard_files())
        ops.stale_opens = 100
        ops, rep = self.run_order("stale2.json", order(order_id="3X8X_DV_0011", doors=2), ops)
        c = rep["components"][1]
        self.assertEqual(c["status"], "failed")
        self.assertIn("keeps returning 3X8X_Door v1 but v2 was saved", c["error"])
        self.assertLess(c["duration_s"], 120)      # not the 300 s save timeout

    def test_program_numbers_persist(self):
        """Program numbers come from state/nc_program_counter.txt and continue across
        orders (1001, 1002)."""
        self.run_order("p1.json", order(order_id="3X8X_DV_0005", doors=1))
        self.run_order("p2.json", order(order_id="3X8X_DV_0006", doors=1))
        with open(os.path.join(self.sb.root, "state", "nc_program_counter.txt")) as fh:
            self.assertEqual(int(fh.read().strip()), 1002)   # 1001, 1002 used


if __name__ == "__main__":
    unittest.main()
