import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDIN = os.path.join(ROOT, "addin", "NewFusionExtension")
if ADDIN not in sys.path:
    sys.path.insert(0, ADDIN)

from nfe import config as config_mod  # noqa: E402
from nfe.engine import Engine  # noqa: E402
from nfe.fake_ops import FakeFile, FakeOps  # noqa: E402
from nfe.params import ModelParam  # noqa: E402
from nfe.paths import Paths  # noqa: E402
from nfe.state import StateStore  # noqa: E402

EXAMPLE_ORDER = os.path.join(ROOT, "tests", "fixtures", "20260206_115328_3X8X_DV_0622.json")


class FakeClock(object):
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def door_params():
    return {
        "component_height": ModelParam("component_height", "in", False, "80 in"),
        "component_width": ModelParam("component_width", "in", False, "30 in"),
        "component_floor_clearance": ModelParam("component_floor_clearance", "in", False, "4 in"),
        "component_ceiling_clearance": ModelParam("component_ceiling_clearance", "in", False, "5 in"),
        "door_hinging_right": ModelParam("door_hinging_right", "", False, "0"),
        "door_swinging_out": ModelParam("door_swinging_out", "", False, "0"),
        "door_wall_post_hinging": ModelParam("door_wall_post_hinging", "", False, "0"),
        "door_wall_post_latching": ModelParam("door_wall_post_latching", "", False, "0"),
        "door_wall_keep_latching": ModelParam("door_wall_keep_latching", "", False, "0"),
        "door_perp_stile_keeper": ModelParam("door_perp_stile_keeper", "", False, "0"),
        "floor_to_ceiling": ModelParam("floor_to_ceiling", "", False, "0"),
    }


def stile_params():
    return {
        "component_height": ModelParam("component_height", "in", False, "80 in"),
        "component_width": ModelParam("component_width", "in", False, "3 in"),
    }


HOLES = ["top_hinge_hole_1", "top_hinge_hole_2", "mid_top_hinge_hole_1", "mid_top_hinge_hole_2",
         "mid_bottom_hinge_hole_1", "mid_bottom_hinge_hole_2", "bottom_hinge_hole_1", "bottom_hinge_hole_2"]
HOLE_X = [1.72, 3.22, 29.594, 31.094, 57.468, 58.968, 85.343, 86.843]


def _setup(name, ops, suppressed=True):
    return {"name": name, "suppressed": suppressed,
            "ops": [{"name": o, "suppressed": True} for o in ops]}


def door_cam():
    """Same names (incl. trailing spaces) and suppression as the live 3X8X_Door."""
    out = []
    for i, (side, face, g) in enumerate([("Left", "Interior", "G57"), ("Left", "Interior", "G59"),
                                         ("Left", "Exterior", "G57"), ("Left", "Exterior", "G59"),
                                         ("Right", "Interior", "G57"), ("Right", "Interior", "G59"),
                                         ("Right", "Exterior", "G57"), ("Right", "Exterior", "G59")]):
        out.append(_setup("Setup %d - %s" % (i + 1, g),
                          ["%s %s Rabbet" % (side, face), "%s Bottom Notch " % side, "%s Top Notch" % side],
                          suppressed=i not in (4, 5)))
    return out


def door_computed(v):
    """Tiny stand-in for the door model's derived parameters."""
    hr, so = bool(v.get("door_hinging_right")), bool(v.get("door_swinging_out"))
    out = {"left_interior_rabbeting": float(not hr and so), "left_exterior_rabbeting": float(not hr and not so),
           "right_interior_rabbeting": float(hr and so), "right_exterior_rabbeting": float(hr and not so),
           "bottom_left_notching": 0.0, "bottom_right_notching": 0.0,
           "top_left_notching": 0.0, "top_right_notching": 0.0, "hinge_hole_x_dist": 0.968}
    out.update({h + "_y_value": x for h, x in zip(HOLES, HOLE_X)})
    return out


def stile_cam():
    return [_setup("%s Rabbet - %s %s" % (side, sw, g), ["%s rabbet %s" % (side, sw.lower())], suppressed=False)
            for side in ("Left", "Right") for sw in ("Out", "In") for g in ("G57", "G59")]


def stile_computed(v):
    ld, rd = bool(v.get("left_side_door")), bool(v.get("right_side_door"))
    out = {"hinge_hole_x_dist": 0.968,
           "left_interior_drilling": float(ld and bool(v.get("LD_hinging_right")) and not v.get("LD_swinging_out")),
           "left_exterior_drilling": float(ld and bool(v.get("LD_hinging_right")) and bool(v.get("LD_swinging_out"))),
           "right_interior_drilling": float(rd and not v.get("RD_hinging_right") and not v.get("RD_swinging_out")),
           "right_exterior_drilling": float(rd and not v.get("RD_hinging_right") and bool(v.get("RD_swinging_out")))}
    for side in ("LD", "RD"):
        out.update({"%s_stile_%s_y_value" % (side, h): x for h, x in zip(HOLES, HOLE_X)})
    return out


def full_stile_params():
    p = stile_params()
    for name in ("left_side_door", "right_side_door", "LD_hinging_right", "LD_swinging_out",
                 "RD_hinging_right", "RD_swinging_out"):
        p[name] = ModelParam(name, "", False, "0")
    return p


def panel_params():
    p = {n: ModelParam(n, "in", False, "10 in") for n in ("component_height", "component_width")}
    p["cutout_A"] = ModelParam("cutout_A", "", True, "''")
    p["cutout_B"] = ModelParam("cutout_B", "", True, "''")
    return p


def panel_cam():
    ops = ["Front Bottom Notch ", "Front Top Notch", "Back bottom Notch", "Back Top Notch",
           "Cutout A", "Cutout B", "Adaptive1"]
    return [_setup("Setup Back Bottom Corner G58", ops),
            _setup("Setup Back Top Corner G58", [o.strip() + " (2)" for o in ops])]


def panel_computed(v):
    out = {"notching_front_edge_bottom": 0.0, "notching_front_edge_top": 0.0,
           "notching_back_edge_bottom": 0.0, "notching_back_edge_top": 0.0}
    for k in ("A", "B"):
        on = bool(v.get("cutout_" + k))
        out["cutout_%s_width" % k] = 4.0 if on else 0.0
        out["cutout_%s_height" % k] = 6.0 if on else 0.0
    return out


def standard_files():
    return [
        FakeFile("m-door-3x8x", "3X8X_Door", "f3d", door_params(), cam=door_cam(), computed=door_computed),
        FakeFile("d-door-3x8x", "3X8X_Door Drawing", "f2d"),
        FakeFile("m-stile-3x8x", "3X8X_Stile", "f3d", full_stile_params(), cam=stile_cam(), computed=stile_computed),
        FakeFile("d-stile-3x8x", "3X8X_Stile Drawing", "f2d"),
        FakeFile("m-panel-3x8x", "3X8X_Panel_Standard", "f3d", panel_params(), cam=panel_cam(), computed=panel_computed),
        FakeFile("d-panel-3x8x", "3X8X_Panel_Standard Drawing", "f2d"),
    ]


def standard_rules():
    return [
        {"id": "door-3x8x", "enabled": True, "type": "door", "family": "3X8X", "series": "",
         "model": {"id": "m-door-3x8x", "name": "3X8X_Door"},
         "drawing": {"id": "d-door-3x8x", "name": "3X8X_Door Drawing"}},
        {"id": "stile-3x8x", "enabled": True, "type": "stile", "family": "3X8X", "series": "",
         "model": {"id": "m-stile-3x8x", "name": "3X8X_Stile"},
         "drawing": {"id": "d-stile-3x8x", "name": "3X8X_Stile Drawing"}},
        {"id": "panel-3x8x", "enabled": True, "type": "panel", "family": "3X8X", "series": "",
         "model": {"id": "m-panel-3x8x", "name": "3X8X_Panel_Standard"},
         "drawing": {"id": "d-panel-3x8x", "name": "3X8X_Panel_Standard Drawing"}},
    ]


class Sandbox(object):
    """A throwaway project root with config + dropbox."""

    def __init__(self, rules=None, processing=None, health=None, extra=None):
        self.root = tempfile.mkdtemp(prefix="nfe_test_")
        self.paths = Paths(self.root).ensure()
        cfg = config_mod.deep_merge(config_mod.DEFAULTS, {
            "rules": standard_rules() if rules is None else rules,
            "processing": dict({"drawing_settle_s": 0.0}, **(processing or {})),
            "health": health or {},
            "api": {"addin_port": 59987},    # never the live add-in's port
            "cam": {"seed_counter_from": ""},  # never read the real machine's counter
        })
        cfg = config_mod.deep_merge(cfg, extra or {})
        config_mod.save(self.paths.config_file, cfg)
        self.clock = FakeClock()

    def make_engine(self, ops):
        state = StateStore(self.paths.status_file, self.paths.events_file, clock=self.clock)
        return Engine(ops, self.paths, state, lambda: config_mod.load(self.paths.config_file),
                      clock=self.clock, wall=self.clock)

    def drop(self, name, data):
        path = os.path.join(self.paths.inbox, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.utime(path, (self.clock.t - 60, self.clock.t - 60))   # "settled"
        return path

    def run(self, engine, max_ticks=5000, step=0.5):
        for _ in range(max_ticks):
            if not engine.has_work():
                return
            engine.tick()
            self.clock.t += step
        raise AssertionError("engine did not go idle")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def order(order_id="3X8X_TEST_0001", doors=1, stiles=0, series="3082G.67P", extra=None):
    def door(i):
        p = {"series_id": [series, "string", ""],
             "component_height": [88.5625, "float", ""], "component_width": [32.5625, "float", ""],
             "component_floor_clearance": [4.5, "float", ""],
             "component_ceiling_clearance": [5.5625, "float", ""],
             "door_hinging_right": [True, "bool", ""], "door_swinging_out": [False, "bool", ""],
             "door_wall_post_hinging": [False, "bool", ""], "door_wall_post_latching": [False, "bool", ""],
             "door_wall_keep_latching": [True, "bool", ""], "door_perp_stile_keeper": [False, "bool", ""],
             "floor_to_ceiling": [False, "bool", ""]}
        p.update(extra or {})
        return {"id": ["D%d" % i, "string", "door ID"], "parameters": p}

    def stile(i):
        return {"id": ["S%d" % i, "string", ""],
                "parameters": {"series_id": [series, "string", ""],
                               "component_height": [70.0, "float", ""],
                               "component_width": [3.0, "float", ""]}}
    data = {"order_id": [order_id, "string", ""]}
    if doors:
        data["doors"] = [door(i + 1) for i in range(doors)]
    if stiles:
        data["stiles"] = [stile(i + 1) for i in range(stiles)]
    return data
