import copy
import itertools
import os
import re
import unittest

import helpers  # noqa: F401  (sets sys.path)
from nfe import cam_rules, rules
from nfe.cam_defaults import DEFAULT_PROFILES
from nfe.cam_rules import (NoRuleMatched, collect_values, explain, expects_toolpath,
                           normalize_op_name, normalize_setup_name, plan, render_markdown,
                           resolve_actions, validate_profile)
from nfe.expr import ExprError, compile_expr, evaluate
from nfe.orders import ParamValue

PROFILES = {p["id"]: p for p in DEFAULT_PROFILES}
DOC = os.path.join(helpers.ROOT, "docs", "tables", "TOOLPATH_SELECTION.md")


# ======================================================================
# Live-model fixtures (names exactly as probed, quirks included)
# ======================================================================
def _setup(name, ops, suppressed=False, op_suppressed=()):
    return {"name": name, "suppressed": suppressed,
            "ops": [{"name": o, "suppressed": o in op_suppressed} for o in ops]}


def door_setups():
    out = []
    for n in range(1, 9):
        g = "G57" if n % 2 else "G59"
        side = "Left" if n <= 4 else "Right"
        kind = "Interior" if n in (1, 2, 5, 6) else "Exterior"
        if side == "Left":
            ops = ["Left %s Rabbet" % kind, "Left Bottom Notch ", "Left Top Notch"]
        else:
            ops = ["Right %s Rabbet" % kind, "Right Bottom Notch", "Right Top Notch "]
        # Realistic leftovers from the previous component: some setups and ops suppressed.
        out.append(_setup("Setup %d - %s" % (n, g), ops, suppressed=n in (3, 6),
                          op_suppressed=ops[1:2] if n in (2, 7) else ()))
    return out


def stile_setups(dash86=False):
    out = []
    for side in ("Left", "Right"):
        for swing in ("Out", "In"):
            for g in ("G57", "G59"):
                name = ("%s Rabbet - %s - %s" if dash86 else "%s Rabbet - %s %s") % (side, swing, g)
                op = "%s rabbet %s" % (side, swing.lower())
                out.append(_setup(name, [op], suppressed=(g == "G59" and swing == "In"),
                                  op_suppressed=(op,) if side == "Right" else ()))
    return out


PANEL_OPS_1 = ["Front Bottom Notch ", "Front Top Notch", "Back bottom Notch", "Back Top Notch",
               "Cutout A", "Cutout B", "Adaptive1"]
PANEL_OPS_2 = ["Front Bottom Notch  (2)", "Front Top Notch (2)", "Back bottom Notch (2)",
               "Back Top Notch (2)", "Cutout A (2)", "Cutout B (2)", "Adaptive1 (2)"]


def panel_setups():
    return [_setup("Setup Back Bottom Corner G58", PANEL_OPS_1, op_suppressed=("Cutout B",)),
            _setup("Setup Back Top Corner G58", PANEL_OPS_2, suppressed=True,
                   op_suppressed=("Adaptive1 (2)",))]


# ======================================================================
# Oracles: the old FusionExtensionVM logic with Fusion/logging stripped
# ======================================================================
def _to_bool(val):
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    return str(val).strip().lower() not in ("0", "false", "no", "none", "null", "")


def old_door_sequence(p, setup_names):
    """order_processor._build_door_setup_sequence"""
    v = lambda k: _to_bool(p.get(k, 0))      # noqa: E731
    swing_out = v("door_swinging_out")
    li, le = v("left_interior_rabbeting"), v("left_exterior_rabbeting")
    ri, re_ = v("right_interior_rabbeting"), v("right_exterior_rabbeting")
    bl, br = v("bottom_left_notching"), v("bottom_right_notching")
    tl, tr = v("top_left_notching"), v("top_right_notching")
    left = li or le or bl or tl
    right = ri or re_ or br or tr
    if not left and not right:
        return []

    def find(target):
        t = target.lower()
        for n in setup_names:
            if n.lower() == t:
                return n
        for n in setup_names:
            if t in n.lower():
                return n
        return None

    def left_setup(g):
        if le:
            return find("Setup 3 - G57") if g == "G57" else find("Setup 4 - G59")
        return find("Setup 1 - G57") if g == "G57" else find("Setup 2 - G59")

    def right_setup(g):
        if re_:
            return find("Setup 7 - G57") if g == "G57" else find("Setup 8 - G59")
        return find("Setup 5 - G57") if g == "G57" else find("Setup 6 - G59")

    if swing_out:
        if left and not right:
            seq = [left_setup("G59")]
        elif right and not left:
            seq = [right_setup("G57")]
        else:
            seq = [left_setup("G59"), right_setup("G57")]
    else:
        if left and not right:
            seq = [left_setup("G57")]
        elif right and not left:
            seq = [right_setup("G59")]
        else:
            seq = [left_setup("G57"), right_setup("G59")]
    if any(s is None for s in seq):
        return None
    return seq


def old_door_suppression(p, cam):
    """cam_manager.suppress_door_toolpaths -> {(setup, op): active} for ops it touches."""
    b = lambda k: p.get(k, 0) != 0          # noqa: E731
    li, le = b("left_interior_rabbeting"), b("left_exterior_rabbeting")
    ri, re_ = b("right_interior_rabbeting"), b("right_exterior_rabbeting")
    bl, br = b("bottom_left_notching"), b("bottom_right_notching")
    tl, tr = b("top_left_notching"), b("top_right_notching")
    op_active = {}
    for n, rab, flag in ((1, "Left Interior Rabbet", li), (2, "Left Interior Rabbet", li),
                         (3, "Left Exterior Rabbet", le), (4, "Left Exterior Rabbet", le)):
        s = "Setup %d - %s" % (n, "G57" if n % 2 else "G59")
        op_active[(s, rab)] = flag
        op_active[(s, "Left Bottom Notch")] = bl
        op_active[(s, "Left Top Notch")] = tl
    for n, rab, flag in ((5, "Right Interior Rabbet", ri), (6, "Right Interior Rabbet", ri),
                         (7, "Right Exterior Rabbet", re_), (8, "Right Exterior Rabbet", re_)):
        s = "Setup %d - %s" % (n, "G57" if n % 2 else "G59")
        op_active[(s, rab)] = flag
        op_active[(s, "Right Bottom Notch")] = br
        op_active[(s, "Right Top Notch")] = tr
    out = {}
    for setup in cam:
        for op in setup["ops"]:
            key = (setup["name"].strip(), op["name"].strip())
            if key in op_active:
                out[(setup["name"], op["name"])] = op_active[key]
    return out


def old_stile_sequence(p, setup_names):
    """order_processor._build_stile_setup_sequence"""
    val = lambda k, d=None: p.get(k, d)      # noqa: E731
    m = re.search(r"(3\d{3})", str(val("series_id", "")))
    code = (m.group(1)[0] + "0" + m.group(1)[2:]) if m else ""
    is_3082 = code in ("3082", "3081")
    is_3086 = code == "3086"
    if not is_3082 and not is_3086:
        return None
    left = _to_bool(val("left_side_door", 0))
    right = _to_bool(val("right_side_door", 0))
    if int(left) + int(right) == 0:
        return []
    ld_out = _to_bool(val("LD_swinging_out", 0)) if left else None
    rd_out = _to_bool(val("RD_swinging_out", 0)) if right else None

    def norm(s):
        return re.sub(r"\s*-\s*", " ", s).lower().strip()

    def find(target):
        tl, tn = target.lower(), norm(target)
        for n in setup_names:
            if n.lower() == tl:
                return n
        for n in setup_names:
            if norm(n) == tn:
                return n
        for n in setup_names:
            if tl in n.lower():
                return n
        return None

    if int(left) + int(right) == 1:
        if left:
            seq = [find("Left Rabbet - Out G57" if ld_out else "Left Rabbet - In G59")]
        else:
            seq = [find("Right Rabbet - Out G59" if rd_out else "Right Rabbet - In G57")]
    else:
        if ld_out:
            l57, l59 = find("Left Rabbet - Out G57"), find("Left Rabbet - Out G59")
        else:
            l57, l59 = find("Left Rabbet - In G57"), find("Left Rabbet - In G59")
        if rd_out:
            r57, r59 = find("Right Rabbet - Out G57"), find("Right Rabbet - Out G59")
        else:
            r57, r59 = find("Right Rabbet - In G57"), find("Right Rabbet - In G59")
        if ld_out == rd_out:
            if not ld_out:
                seq = [find("Right Rabbet - In G57"), find("Left Rabbet - In G59")]
            else:
                seq = [find("Left Rabbet - Out G57"), find("Right Rabbet - Out G59")]
        else:
            if not ld_out and rd_out:
                ls, rs = l59, r59
            else:
                ls, rs = l57, r57
            li = _to_bool(val("left_interior_drilling", 0))
            le = _to_bool(val("left_exterior_drilling", 0))
            ri = _to_bool(val("right_interior_drilling", 0))
            re_ = _to_bool(val("right_exterior_drilling", 0))
            ld, rd = li or le, ri or re_
            if not ld and not rd:
                seq = [rs, ls] if is_3086 else [ls, rs]
            elif ld and not rd:
                seq = [ls, rs]
            elif rd and not ld:
                seq = [rs, ls]
            else:
                lfr = 4 if li else 3
                rfr = 4 if ri else 3
                if lfr == 3:
                    left_first = True
                elif rfr == 3:
                    left_first = False
                else:
                    left_first = True
                seq = [ls, rs] if left_first else [rs, ls]
    if any(s is None for s in seq):
        return None
    return seq if seq else None


def _text_param(expr):
    expr = str(expr)
    return expr[1:-1].strip() if expr.startswith("'") and expr.endswith("'") else expr.strip()


def old_panel_sequence(p, setup_names):
    """order_processor._build_panel_setup_sequence (text fallback included)"""
    h, w = float(p.get("component_height", 0)), float(p.get("component_width", 0))
    if h == 0 and w == 0:
        return None
    notch = any(float(p.get(k, 0)) != 0 for k in (
        "notching_front_edge_bottom", "notching_back_edge_bottom",
        "notching_front_edge_top", "notching_back_edge_top"))
    ca = float(p.get("cutout_A_width", 0)) > 0 and float(p.get("cutout_A_height", 0)) > 0
    cb = float(p.get("cutout_B_width", 0)) > 0 and float(p.get("cutout_B_height", 0)) > 0
    if not ca and "cutout_A" in p and _text_param(p["cutout_A"]):
        ca = True
    if not cb and "cutout_B" in p and _text_param(p["cutout_B"]):
        cb = True
    if not notch and not ca and not cb:
        return []

    def find(target):
        t = target.lower()
        for n in setup_names:
            if n.lower() == t:
                return n
        for n in setup_names:
            if t in n.lower():
                return n
        return None
    found = find("Setup Back Bottom Corner G58" if h >= w else "Setup Back Top Corner G58")
    return None if found is None else [found]


def old_panel_suppression(p, cam):
    """cam_manager.suppress_panel_toolpaths -> {(setup, op): active} (every op)."""
    b = lambda k: p.get(k, 0) != 0          # noqa: E731
    ca = p.get("cutout_A_width", 0) > 0 and p.get("cutout_A_height", 0) > 0
    cb = p.get("cutout_B_width", 0) > 0 and p.get("cutout_B_height", 0) > 0
    if _text_param(p.get("cutout_A", "")) and not ca:
        ca = True
    if _text_param(p.get("cutout_B", "")) and not cb:
        cb = True
    by_name = {"front bottom notch": b("notching_front_edge_bottom"),
               "front top notch": b("notching_front_edge_top"),
               "back bottom notch": b("notching_back_edge_bottom"),
               "back top notch": b("notching_back_edge_top"),
               "cutout a": ca, "cutout b": cb}
    out = {}
    for setup in cam:
        for op in setup["ops"]:
            n = re.sub(r"\s+\(\d+\)$", "", op["name"].strip()).strip().lower()
            out[(setup["name"], op["name"])] = by_name.get(n, False)
    return out


# ======================================================================
# Helpers
# ======================================================================
def run_profile(pid, model, order, cam):
    profile = PROFILES[pid]
    env, missing = collect_values(profile, model, order)
    pl = plan(profile, env)
    return pl, resolve_actions(pl, cam), missing


def flat_ops(actions):
    return {(s, o): a for s, ops in actions["ops"].items() for o, a in ops.items()}


DOOR_FLAGS = ["door_swinging_out", "left_interior_rabbeting", "left_exterior_rabbeting",
              "bottom_left_notching", "top_left_notching", "right_interior_rabbeting",
              "right_exterior_rabbeting", "bottom_right_notching", "top_right_notching"]


# ======================================================================
# Expression language
# ======================================================================
class ExprTests(unittest.TestCase):
    def test_operators_and_precedence(self):
        env = {"a": 1, "b": 0, "x": 3, "y": 4.5}
        cases = [
            ("a and b", 0), ("a or b", 1), ("not b", True), ("not a or b", 0),
            ("a or b and b", 1),                 # and binds tighter than or
            ("not a and b", False),               # not binds tighter than and
            ("x + y * 2", 12.0), ("(x + y) * 2", 15.0), ("x - 1 - 1", 1),
            ("x / 2", 1.5), ("-x + +y", 1.5), ("- -x", 3),
            ("x == 3", True), ("x != 3", False), ("x < y", True), ("x <= 3", True),
            ("x > y", False), ("y >= 4.5", True),
            ("1 < x < y", True), ("1 < x > y", False), ("0 <= b == 0 < a", True),
            ("x * 2 >= y and not b", True),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(evaluate(text, env), expected)

    def test_strings_and_booleans(self):
        env = {"s": "B-354", "e": ""}
        self.assertTrue(evaluate("s != ''", env))
        self.assertTrue(evaluate("e == \"\"", env))
        self.assertTrue(evaluate("s == 'B-354' and e == ''", env))
        self.assertIs(evaluate("true", {}), True)
        self.assertIs(evaluate("false or False", {}), False)
        self.assertIs(evaluate("True and not false", {}), True)
        self.assertEqual(evaluate("true + 1", {}), 2)
        self.assertTrue(evaluate("e or s", env) == "B-354")      # Python truthiness

    def test_names(self):
        self.assertEqual(compile_expr("a and (b or c > 2) or true").names, {"a", "b", "c"})

    def test_unknown_variable(self):
        with self.assertRaises(ExprError) as cm:
            evaluate("a and zz", {"a": 1})
        self.assertIn("unknown variable 'zz'", str(cm.exception))
        self.assertEqual(evaluate("b and zz", {"b": 0}), 0)     # short circuit

    def test_runtime_errors_are_expr_errors(self):
        for text, env in (("x / 0", {"x": 1}), ("s < 1", {"s": "a"}), ("s * 3", {"s": "ab"}),
                          ("-s", {"s": "a"}), ("s + 'b'", {"s": "a"})):
            with self.subTest(text=text), self.assertRaises(ExprError):
                evaluate(text, env)

    def test_unsafe_or_unsupported_input_rejected(self):
        bad = ["__import__('os')", "__import__('os').system('dir')", "a.b", "a.__class__",
               "f(1)", "open('x')", "a[0]", "a[1:2]", "lambda: 1", "lambda x: x",
               "[x for x in a]", "{1: 2}", "(1, 2)", "[1]", "{1}", "a if b else c",
               "(a := 1)", "2 ** 8", "a % 2", "a // 2", "a << 1", "a | b", "~a", "a in b",
               "a is b", "a is not b", "None", "b'x'", "f'{a}'", "...", "a = 1", "import os",
               "", "   ", "1 +", "a and", "(a", "x = = 1", "'a' 'b' c"]
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ExprError):
                compile_expr(text)
        self.assertTrue(issubclass(ExprError, ValueError))

    def test_non_text_and_too_long(self):
        with self.assertRaises(ExprError):
            compile_expr(5)
        with self.assertRaises(ExprError):
            compile_expr("a or " * 1000 + "a")
        with self.assertRaises(ExprError):
            compile_expr("(" * 500 + "a" + ")" * 500)

    def test_cached(self):
        self.assertIs(compile_expr("x > 1 and y"), compile_expr("x > 1 and y"))

    def test_syntax_error_message(self):
        with self.assertRaises(ExprError) as cm:
            compile_expr("a and and b")
        self.assertIn("syntax error", str(cm.exception))
        with self.assertRaises(ExprError) as cm:
            compile_expr("len(a) > 1")
        self.assertIn("function calls", str(cm.exception))


# ======================================================================
# Names
# ======================================================================
class NameTests(unittest.TestCase):
    def test_normalize_setup_name(self):
        self.assertEqual(normalize_setup_name("Left Rabbet - Out - G57"), "left rabbet out g57")
        self.assertEqual(normalize_setup_name("Left Rabbet - Out G57"), "left rabbet out g57")
        self.assertEqual(normalize_setup_name(" left  rabbet-out   G57 "), "left rabbet out g57")
        self.assertEqual(normalize_setup_name("Setup 1 - G57 (2)"), "setup 1 g57")
        self.assertNotEqual(normalize_setup_name("Setup 1 - G57"),
                            normalize_setup_name("Setup 10 - G57"))

    def test_normalize_op_name(self):
        self.assertEqual(normalize_op_name("Left Bottom Notch "), "left bottom notch")
        self.assertEqual(normalize_op_name("Front Bottom Notch  (2)"), "front bottom notch")
        self.assertEqual(normalize_op_name("Back bottom Notch"), "back bottom notch")
        self.assertEqual(normalize_op_name("Cutout A (2)"), "cutout a")
        self.assertEqual(normalize_op_name("Adaptive1 (12)"), "adaptive1")

    def test_no_partial_setup_match(self):
        cam = [_setup("Setup 10 - G57", ["Left Interior Rabbet"]),
               _setup("Setup 1 - G57 extra", ["Left Interior Rabbet"])]
        acts = resolve_actions({"setups": ["Setup 1 - G57"], "op_rules": []}, cam)
        self.assertEqual(acts["sequence"], [])
        self.assertEqual(acts["missing_setups"], ["Setup 1 - G57"])

    def test_exact_name_preferred_over_normalized(self):
        cam = [_setup("left rabbet out g57", ["x"]), _setup("Left Rabbet - Out G57", ["y"])]
        acts = resolve_actions({"setups": ["Left Rabbet - Out G57"], "op_rules": []}, cam)
        self.assertEqual(acts["sequence"], ["Left Rabbet - Out G57"])


# ======================================================================
# Oracle comparisons (exhaustive)
# ======================================================================
class DoorOracle(unittest.TestCase):
    def test_every_flag_combination(self):
        cam = door_setups()
        names = [s["name"] for s in cam]
        by_name = {s["name"]: s for s in cam}
        for bits in itertools.product((0, 1), repeat=len(DOOR_FLAGS)):
            # Fusion hands back floats; also exercise 1.0 / 0.0.
            model = {k: float(b) for k, b in zip(DOOR_FLAGS, bits)}
            with self.subTest(**{k: b for k, b in zip(DOOR_FLAGS, bits)}):
                pl, acts, missing = run_profile("door-3x8x", model, {}, cam)
                self.assertEqual(missing, [])
                expected = old_door_sequence(model, names)
                self.assertEqual(acts["sequence"], expected)
                self.assertEqual(acts["missing_setups"], [])
                self.assertEqual(expects_toolpath(pl), any(bits[1:]))
                # Operation states inside the selected setups match the old suppression.
                old = old_door_suppression(model, cam)
                want = {k: v for k, v in old.items() if k[0] in expected}
                self.assertEqual(flat_ops(acts), want)
                self.assertEqual(acts["unknown_ops"], [])
                self.assertEqual(acts["unsuppress_setups"],
                                 [s for s in expected if by_name[s]["suppressed"]])

    def test_swing_from_order_when_model_lacks_it(self):
        cam = door_setups()
        model = {"left_interior_rabbeting": 1}
        pl, acts, missing = run_profile("door-3x8x", model,
                                        {"door_swinging_out": ParamValue(True, "bool")}, cam)
        self.assertEqual(acts["sequence"], ["Setup 2 - G59"])
        self.assertIn("R_int", missing)
        self.assertNotIn("swing_out", missing)
        # Model value wins over the order.
        pl, acts, _ = run_profile("door-3x8x", dict(model, door_swinging_out=0),
                                  {"door_swinging_out": [True, "bool", ""]}, cam)
        self.assertEqual(acts["sequence"], ["Setup 1 - G57"])

    def test_unknown_ops_left_alone(self):
        cam = door_setups()
        cam[0]["ops"].append({"name": "Chamfer", "suppressed": False})
        _, acts, _ = run_profile("door-3x8x", {"left_interior_rabbeting": 1}, {}, cam)
        self.assertEqual(acts["unknown_ops"], [("Setup 1 - G57", "Chamfer")])
        self.assertNotIn("Chamfer", acts["ops"]["Setup 1 - G57"])


class StileOracle(unittest.TestCase):
    ORDER_KEYS = ["left_side_door", "right_side_door", "LD_swinging_out", "RD_swinging_out"]
    DRILL_KEYS = ["left_interior_drilling", "left_exterior_drilling",
                  "right_interior_drilling", "right_exterior_drilling"]

    def check_series(self, series, pid, dash86):
        profile = PROFILES[pid]
        self.assertIs(cam_rules.select_profile(DEFAULT_PROFILES, "stile", series, "3X8X"), profile)
        cam = stile_setups(dash86)
        names = [s["name"] for s in cam]
        by_name = {s["name"]: s for s in cam}
        count = 0
        for obits in itertools.product((False, True), repeat=4):
            for dbits in itertools.product((0, 1), repeat=4):
                order = dict(zip(self.ORDER_KEYS, obits), series_id=series)
                model = {k: float(b) for k, b in zip(self.DRILL_KEYS, dbits)}
                with self.subTest(series=series, order=obits, drilling=dbits):
                    pl, acts, _ = run_profile(pid, model, order, cam)
                    expected = old_stile_sequence(dict(order, **model), names)
                    self.assertIsNotNone(expected)
                    self.assertEqual(acts["sequence"], expected)
                    self.assertEqual(acts["missing_setups"], [])
                    self.assertEqual(expects_toolpath(pl), bool(expected))
                    # The single rabbet op of every posted setup is made active.
                    self.assertEqual(flat_ops(acts),
                                     {(s, by_name[s]["ops"][0]["name"]): True for s in expected})
                    self.assertEqual(acts["unsuppress_setups"],
                                     [s for s in expected if by_name[s]["suppressed"]])
                    count += 1
        self.assertEqual(count, 256)

    def test_3082(self):
        self.check_series("3082G.67P", "stile-3x82", False)

    def test_3081_has_no_toolpath_table(self):
        # the 3X81 model has no manufacturing setups: no profile, CAM skipped
        for series in ("3081", "3181B", "3081.67"):
            self.assertIsNone(rules.resolve(DEFAULT_PROFILES, "stile", series, "3X8X"), series)

    def test_3182(self):
        self.check_series("3182", "stile-3x82", False)

    def test_3086_dash_variant(self):
        self.check_series("3086", "stile-3x86", True)
        self.check_series("3186G.67P", "stile-3x86", True)

    def test_3086_rows_also_match_3x82_style_names(self):
        self.check_series("3086", "stile-3x86", False)

    def test_order_flags_as_strings_and_ints(self):
        cam = stile_setups()
        order = {"left_side_door": "1", "right_side_door": "false",
                 "LD_swinging_out": "0", "RD_swinging_out": 1}
        _, acts, _ = run_profile("stile-3x82", {}, order, cam)
        self.assertEqual(acts["sequence"], ["Left Rabbet - In G59"])

    def test_missing_setup_reported(self):
        cam = [s for s in stile_setups() if s["name"] != "Left Rabbet - In G59"]
        _, acts, _ = run_profile("stile-3x82", {}, {"left_side_door": 1}, cam)
        self.assertEqual(acts["sequence"], [])
        self.assertEqual(acts["missing_setups"], ["Left Rabbet - In G59"])

    def test_other_series_not_matched(self):
        for series in ("3084", "3083X", "2082"):
            self.assertIsNone(cam_rules.select_profile(DEFAULT_PROFILES, "stile", series, "3X8X"))


class PanelOracle(unittest.TestCase):
    DIMS = [(80.0, 30.0), (30.0, 80.0), (40.0, 40.0), (0.0, 10.0), (10.0, 0.0), (0.0, 0.0)]
    CUTOUTS = [(0.0, 0.0, ""), (5.0, 6.0, ""), (5.0, 0.0, ""), (0.0, 6.0, "'B-354'"),
               (0.0, 0.0, "'B-354'"), (0.0, 0.0, "''"), (5.0, 6.0, "C-12"), (0.0, 0.0, "  ")]
    NOTCH = ["notching_front_edge_bottom", "notching_front_edge_top",
             "notching_back_edge_bottom", "notching_back_edge_top"]

    def test_combinations(self):
        cam = panel_setups()
        names = [s["name"] for s in cam]
        by_name = {s["name"]: s for s in cam}
        count = 0
        for (h, w), nbits, ca, cb in itertools.product(self.DIMS,
                                                       itertools.product((0, 1), repeat=4),
                                                       self.CUTOUTS, self.CUTOUTS[:4]):
            model = {"component_height": h, "component_width": w,
                     "cutout_A_width": ca[0], "cutout_A_height": ca[1], "cutout_A": ca[2],
                     "cutout_B_width": cb[0], "cutout_B_height": cb[1], "cutout_B": cb[2]}
            model.update({k: float(b) for k, b in zip(self.NOTCH, nbits)})
            with self.subTest(**{k: str(v) for k, v in model.items()}):
                profile = PROFILES["panel-3x8x"]
                env, _ = collect_values(profile, model, {})
                expected = old_panel_sequence(model, names)
                if expected is None:
                    with self.assertRaises(NoRuleMatched):
                        plan(profile, env)
                    continue
                pl = plan(profile, env)
                acts = resolve_actions(pl, cam)
                self.assertEqual(acts["sequence"], expected)
                self.assertEqual(expects_toolpath(pl), bool(expected))
                old = old_panel_suppression(model, cam)
                self.assertEqual(flat_ops(acts),
                                 {k: v for k, v in old.items() if k[0] in expected})
                if expected:
                    self.assertEqual(len(acts["unknown_ops"]), 1)       # Adaptive1
                    self.assertFalse(flat_ops(acts)[(expected[0], by_name[expected[0]]["ops"][-1]["name"])])
                self.assertEqual(acts["unsuppress_setups"],
                                 [s for s in expected if by_name[s]["suppressed"]])
                count += 1
        self.assertGreater(count, 2000)

    def test_second_setup_ops_with_suffix(self):
        model = {"component_height": 20, "component_width": 60, "notching_back_edge_bottom": 1,
                 "cutout_A": "'B-354'"}
        _, acts, _ = run_profile("panel-3x8x", model, {}, panel_setups())
        self.assertEqual(acts["sequence"], ["Setup Back Top Corner G58"])
        self.assertEqual(acts["unsuppress_setups"], ["Setup Back Top Corner G58"])
        self.assertEqual(acts["ops"]["Setup Back Top Corner G58"], {
            "Front Bottom Notch  (2)": False, "Front Top Notch (2)": False,
            "Back bottom Notch (2)": True, "Back Top Notch (2)": False,
            "Cutout A (2)": True, "Cutout B (2)": False, "Adaptive1 (2)": False})


# ======================================================================
# Values, planning, validation, docs
# ======================================================================
class ValuesAndPlanning(unittest.TestCase):
    def profile(self):
        return {
            "id": "t", "type": "door", "family": "3X8X",
            "variables": {"a": "pa", "b": {"param": "pb", "source": "order", "default": 7},
                          "c": {"param": "pc", "source": "model", "type": "text"},
                          "n": {"param": "pn", "type": "number"}},
            "derived": {"ab": "a + b", "big": "ab > 10"},
            "sequence_rules": [{"id": "R1", "when": "big", "setups": ["X"]},
                               {"id": "R2", "when": "c == 'hi'", "setups": []}],
            "operation_rules": [{"op": "Op A", "active": "a"}],
            "unknown_operations": "leave",
        }

    def test_collect_values(self):
        env, missing = collect_values(self.profile(), {"pa": True, "pc": "'hi'", "pb": 99},
                                      {"pn": "2.5"})
        self.assertEqual(env, {"a": 1, "b": 7, "c": "hi", "n": 2.5, "ab": 8, "big": False})
        self.assertEqual(missing, ["b"])          # source "order" ignores the model value
        env, missing = collect_values(self.profile(), None, {"pa": 3, "pb": [9, "int", ""],
                                                             "pn": "abc"})
        self.assertEqual((env["a"], env["b"], env["c"], env["n"]), (3, 9, "", 0))
        self.assertEqual(missing, ["c", "n"])

    def test_plan_and_explain(self):
        p = self.profile()
        env, _ = collect_values(p, {"pa": 5, "pc": "hi"}, {"pb": 6})
        pl = plan(p, env)
        self.assertEqual((pl["row_id"], pl["setups"], pl["op_rules"]), ("R1", ["X"], [("Op A", True)]))
        self.assertTrue(expects_toolpath(pl))
        self.assertEqual(explain(p, env), [("R1", True), ("R2", True)])
        env, _ = collect_values(p, {"pc": "hi"}, {})
        self.assertEqual(plan(p, env)["row_id"], "R2")
        self.assertFalse(expects_toolpath(plan(p, env)))
        env, _ = collect_values(p, {}, {})
        with self.assertRaises(NoRuleMatched):
            plan(p, env)

    def test_activate_policy(self):
        acts = resolve_actions({"setups": ["S"], "op_rules": [], "unknown_operations": "activate"},
                               [_setup("S", ["x", "y"])])
        self.assertEqual(acts["ops"], {"S": {"x": True, "y": True}})

    def test_repeated_setup(self):
        acts = resolve_actions({"setups": ["S", "S"], "op_rules": []},
                               [_setup("S", ["x"], suppressed=True)])
        self.assertEqual(acts["sequence"], ["S", "S"])
        self.assertEqual(acts["unsuppress_setups"], ["S"])

    def test_profile_selection(self):
        sel = lambda t, s: cam_rules.select_profile(DEFAULT_PROFILES, t, s, "3X8X")["id"]  # noqa
        self.assertEqual(sel("door", "3082G.67P"), "door-3x8x")
        self.assertEqual(sel("panel", "3186"), "panel-3x8x")
        self.assertEqual(sel("stile", "3082G.67P"), "stile-3x82")
        self.assertEqual(sel("stile", "3186"), "stile-3x86")


class Validation(unittest.TestCase):
    def test_defaults_are_valid(self):
        for p in DEFAULT_PROFILES:
            with self.subTest(profile=p["id"]):
                self.assertEqual(validate_profile(p), [])
        self.assertEqual([p["id"] for p in DEFAULT_PROFILES],
                         ["door-3x8x", "stile-3x82", "stile-3x86", "panel-3x8x"])

    def test_every_default_row_has_a_note(self):
        for p in DEFAULT_PROFILES:
            for row in p["sequence_rules"]:
                self.assertTrue(row.get("note"), (p["id"], row["id"]))

    def broken(self, mutate, needle):
        p = copy.deepcopy(PROFILES["door-3x8x"])
        mutate(p)
        errors = validate_profile(p)
        self.assertTrue(any(needle in e for e in errors), errors)

    def test_broken_profiles(self):
        self.broken(lambda p: p["sequence_rules"][1].update(when="L_any and"), "syntax error")
        self.broken(lambda p: p["sequence_rules"][1].update(when="L_any and nope"), "nope")
        self.broken(lambda p: p["sequence_rules"][2].update(id="D1"), "duplicate row id")
        self.broken(lambda p: p["sequence_rules"][2].pop("id"), "has no id")
        self.broken(lambda p: p["sequence_rules"][2].pop("when"), "has no 'when'")
        self.broken(lambda p: p["sequence_rules"][2].update(setups="Setup 1"), "'setups'")
        self.broken(lambda p: p.pop("sequence_rules"), "sequence_rules")
        self.broken(lambda p: p.pop("id"), "missing an 'id'")
        self.broken(lambda p: p.update(family="", series=""), "family")
        self.broken(lambda p: p.update(unknown_operations="ignore"), "unknown_operations")
        self.broken(lambda p: p["variables"].update(x={"param": "p", "source": "db"}), "source")
        self.broken(lambda p: p["variables"].update(x={"source": "model"}), "no parameter")
        self.broken(lambda p: p["variables"].update({"not": "p"}), "bad variable name")
        self.broken(lambda p: p["derived"].update(L_int="1"), "clashes")
        self.broken(lambda p: p.update(derived={"a": "b", "b": "1"}), "unknown variable")
        self.broken(lambda p: p["operation_rules"].append({"op": "left interior rabbet ",
                                                           "active": "1"}), "duplicate operation")
        self.broken(lambda p: p["operation_rules"].append({"op": "X", "active": "f(1)"}),
                    "function calls")
        self.broken(lambda p: p.update(type="window"), "unknown type")
        self.assertEqual(validate_profile([]), ["profile must be an object"])


class Docs(unittest.TestCase):
    def test_markdown_in_sync_with_defaults(self):
        with open(DOC, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(render_markdown(DEFAULT_PROFILES), text,
                      "regenerate: cd addin/NewFusionExtension && py -3.13 -m nfe.cam_rules "
                      "../../docs/tables/TOOLPATH_SELECTION.md")

    def test_markdown_lists_conflicts(self):
        md = render_markdown(DEFAULT_PROFILES)
        for pid in PROFILES:
            self.assertIn("(`%s`)" % pid, md)
        self.assertIn("Setup 4 - G59", md)
        self.assertIn("InstructionSelectionLogic.xlsx", md)


if __name__ == "__main__":
    unittest.main()
