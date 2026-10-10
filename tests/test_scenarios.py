import json
import os
import sys
import unittest

import helpers
from helpers import FakeFile, FakeOps, Sandbox, order
from nfe.jsonio import read_json
from nfe.orders import parse_order
from nfe.params import ModelParam

sys.path.insert(0, os.path.join(helpers.ROOT, "harness"))
import scenarios  # noqa: E402
import server  # noqa: E402

# Cut-downs of the three real sheets: the header sits on a different row in each.
DOOR_CSV = (
    "Scenario,Drawing required?,component_height,component_width,door_gapping,\"door_wall_\nkeep_latching\","
    ",,Series,Hinging,Gapless,\n"
    "2X8X_DV_0001,Yes,71.75,34.875,1,1,TRUE,,2X8X,RH,G,2X8X_DV_0001:    2X8X   RH   OS\n"
    "2X8X_DV_0002,No,71.75,24.5625,0,0,,,2X8X,LH,,2X8X_DV_0002:    2X8X   LH   OS\n")
STILE_CSV = (
    ",,,,,,,,,,,\n"
    "Count,Scenario,Drawing required?,component_height,ceiling_hung,left_side_door,,Human Readable Label,Series,Gapless\n"
    "1,2X8X_SV_0001,No,79.25,1,0,,2X8X_SV_0001: 2X86 Standard,2X86,\n"
    "7,2X8X_SV_0007,Yes,110.3125,0,1,,2X8X_SV_0007: 2X88G Standard,2X88,G\n"
    "8,2X8X_SV_0008,No,102.9375,0,1,,2X8X_SV_0008: 2X86 Standard,2X86,\n"
    "9,2X8X_SV_0009,Yes,110.625,0,1,,2X8X_SV_0009: 2X86 Standard,2X86,\n")
NOTES_CSV = (
    "Some random variables are dependent on other random variables,Level 1 variable\n"
    ",\n"
    "Count,Scenario,Drawing required?,component_height,floor_anchored,,Human Readable Label,Series,Gapless\n"
    "1,2X8X_SV_0001,Yes,109.75,0,,2X8X_SV_0001: 2X82 Standard,2X82,\n")


class SheetParsing(unittest.TestCase):
    def test_header_row_is_found_wherever_it_is(self):
        """The header row is row 0 in the door sheet, row 1 in the 2X86/88 stile sheet
        and comes after free-text notes in the 2X81/82 sheet; all three parse, and the
        parameter columns stop at the first blank header (`Series`, `Hinging` and the
        label are not parameters). A header cell with a line break
        (`door_wall_\\nkeep_latching`) becomes one name."""
        door = scenarios.parse_sheet(DOOR_CSV, "2X8X Door inputs & outputs v10 2026_09_10(ValidationScenarios vFin ).csv")
        self.assertEqual(door["title"], "2X8X Door")
        self.assertEqual(door["columns"], ["component_height", "component_width", "door_gapping",
                                           "door_wall_keep_latching"])
        self.assertEqual(scenarios.parse_sheet(STILE_CSV)["columns"],
                         ["component_height", "ceiling_hung", "left_side_door"])
        self.assertEqual(scenarios.parse_sheet(NOTES_CSV)["columns"], ["component_height", "floor_anchored"])
        with self.assertRaises(scenarios.SheetError):
            scenarios.parse_sheet("a,b,c\n1,2,3\n")

    def test_order_ids_series_and_types(self):
        """Scenario `2X8X_SV_0007` with Series `2X88` and Gapless `G` becomes order
        `2X88-0007` with `series_id` `2X88G`; the door `2X8X_DV_0001` becomes
        `2X8X-0001` / `2X8XG`. Columns holding only 0/1 are booleans, the rest floats.
        The built order parses as one stile `S1` (or door `D1`) and names the sheet
        title as its `drawing_test` folder."""
        sheet = scenarios.parse_sheet(STILE_CSV, "2X86 and 2X88 Stile inputs & outputs v4.csv")
        row = sheet["rows"][1]
        self.assertEqual((row["order_id"], row["series_id"], row["drawing"], row["label"]),
                         ("2X88-0007", "2X88G", True, "2X88G Standard"))
        data = scenarios.build_order(sheet, row)
        self.assertEqual(data["stiles"][0]["parameters"]["left_side_door"], [True, "bool", ""])
        self.assertEqual(data["stiles"][0]["parameters"]["component_height"], [110.3125, "float", ""])
        parsed = parse_order(data)
        self.assertEqual((parsed.order_id, parsed.drawing_test), ("2X88-0007", "2X86 and 2X88 Stile"))
        self.assertEqual([(c.id, c.type, c.series_id) for c in parsed.components], [("S1", "stile", "2X88G")])

        door = scenarios.parse_sheet(DOOR_CSV, "2X8X Door inputs.csv")
        d = scenarios.build_order(door, door["rows"][0])
        self.assertEqual(d["order_id"][0], "2X8X-0001")
        self.assertEqual(d["doors"][0]["parameters"]["series_id"][0], "2X8XG")
        self.assertEqual(door["rows"][0]["label"], "2X8X RH OS")

    def test_selection(self):
        """`drawings` picks the rows marked Yes (7 and 9). `selection` accepts numbers,
        ranges and ids: `1, 8-9` gives 1, 8, 9; `2X88-0007` and `2x8x_sv_0008` match
        by order id and by scenario id. An unknown id or an empty match is an error."""
        sheet = scenarios.parse_sheet(STILE_CSV)
        pick = lambda mode, sel="": [r["n"] for r in scenarios.select_rows(sheet, mode, sel)]  # noqa: E731
        self.assertEqual(pick("drawings"), [7, 9])
        self.assertEqual(pick("selection", "1, 8-9"), [1, 8, 9])
        self.assertEqual(pick("selection", "2X88-0007 2x8x_sv_0008"), [7, 8])
        for bad in ("2X86-9999", "500-600", ""):
            with self.assertRaises(scenarios.SheetError):
                pick("selection", bad)


class HarnessScenarios(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.h = server.Harness(root=self.sb.root, fusion_exe="nfe_no_such_process.exe",
                                launch_fn=lambda: None, kill_fn=lambda pid: None)

    def tearDown(self):
        self.h.close()
        self.sb.cleanup()

    def test_upload_list_and_generate(self):
        """An uploaded sheet is saved under `scenarios/` and listed with its counts
        (4 scenarios, 2 drawings). Generating `drawings` writes `2X88-0007.json` and
        `2X86-0009.json` to the inbox; generating again skips both instead of
        queuing duplicates. A file that is not a scenario sheet is refused with 400."""
        res = self.h.scenario_upload("2X86 and 2X88 Stile inputs & outputs v4.csv", STILE_CSV)
        self.assertEqual(res["total"], 4)
        sheets = self.h.scenario_sheets()["sheets"]
        self.assertEqual([(s["title"], s["total"], s["drawings"]) for s in sheets],
                         [("2X86 and 2X88 Stile", 4, 2)])
        self.assertEqual(self.h.scenario_rows(res["name"])["rows"][1][:5],
                         [7, "2X8X_SV_0007", "2X88-0007", True, "2X88G"])
        out = self.h.scenario_generate(res["name"], "drawings")
        self.assertEqual(out["queued"], ["2X88-0007", "2X86-0009"])
        self.assertEqual(sorted(os.listdir(self.sb.paths.inbox)), ["2X86-0009.json", "2X88-0007.json"])
        again = self.h.scenario_generate(res["name"], "selection", "7-9")
        self.assertEqual((again["queued"], again["skipped"]), (["2X86-0008"], ["2X88-0007", "2X86-0009"]))
        with self.assertRaises(server.HttpError) as cm:
            self.h.scenario_upload("junk.csv", "a,b\n1,2\n")
        self.assertEqual(cm.exception.status, 400)


def tagged_door_params():
    p = helpers.door_params()
    for name in ("component_id", "a_order_number", "a_series"):
        p[name] = ModelParam(name, "", True, "'old'")
    return p


class TagsAndDrawingTest(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.cleanup()

    def run_order(self, name, data, params):
        files = helpers.standard_files()
        files[0] = FakeFile("m-door-3x8x", "3X8X_Door", "f3d", params, cam=helpers.door_cam(),
                            computed=helpers.door_computed)
        self.sb.drop(name, data)
        eng = self.sb.make_engine(FakeOps(files))
        eng.set_inbox_hint(True)
        self.sb.run(eng)
        return read_json(os.path.join(self.sb.paths.done, os.path.splitext(name)[0] + ".report.json"))

    def test_tag_parameters_are_written(self):
        """A model with `component_id`, `a_order_number` and `a_series` gets
        `'1-D1-IBUS12345'`, `'IBUS12345'` and the order's series_id `'3082G.67P'`,
        with no tag warning."""
        rep = self.run_order("t.json", order(order_id="IBUS12345", doors=1), tagged_door_params())
        c = rep["components"][0]
        applied = {p["name"]: p["after"] for p in c["parameters"]}
        self.assertEqual(applied["component_id"], "'1-D1-IBUS12345'")
        self.assertEqual(applied["a_order_number"], "'IBUS12345'")
        self.assertEqual(applied["a_series"], "'3082G.67P'")
        self.assertFalse([w for w in c["warnings"] if "tag parameter" in w])

    def test_missing_tag_parameter_is_reported(self):
        """A model without any of the tag parameters still finishes, with one warning
        per missing tag (`component_id`, `a_order_number / order_number`,
        `a_series / series`)."""
        rep = self.run_order("t.json", order(doors=1), helpers.door_params())
        warns = [w for w in rep["components"][0]["warnings"] if "tag parameter" in w]
        self.assertEqual(len(warns), 3, warns)
        self.assertIn("a_order_number / order_number", warns[1])

    def test_drawing_test_folder_collects_pdfs(self):
        """An order carrying `"drawing_test": "2X8X Door"` has its drawing PDF copied
        to `drawing_test/2X8X_Door/` next to the normal run folder, and the report
        lists the copy; an ordinary order creates no `drawing_test` folder."""
        self.run_order("plain.json", order(order_id="3X8X_PLAIN_1", doors=1), tagged_door_params())
        self.assertFalse(os.path.exists(os.path.join(self.sb.root, "drawing_test")))
        data = order(order_id="3X8X-0001", doors=1)
        data["drawing_test"] = "2X8X Door"
        rep = self.run_order("scn.json", data, tagged_door_params())
        dest = os.path.join(self.sb.root, "drawing_test", "2X8X_Door")
        self.assertEqual(os.listdir(dest), ["1-D1-3X8X-0001-drawing.pdf"])
        self.assertEqual([os.path.basename(p) for p in rep["outputs"]["drawing_test"]],
                         ["1-D1-3X8X-0001-drawing.pdf"])


if __name__ == "__main__":
    unittest.main()
