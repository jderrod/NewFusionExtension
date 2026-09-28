import helpers  # noqa: F401  (sets sys.path)

import copy
import itertools
import json
import os
import random
import re as _re
import shutil
import tempfile
import unittest

from nfe import drilling

TS = "2026-03-17 10:30:29"
SERIES = ("3082G.67P", "3182G.67P", "3081G", "3086G.67P", "3186", "2082G", "")
OLD_REPO = r"C:\Users\jderr\Downloads\FusionExtensionVM-main\FusionExtensionVM-main"
DOC = os.path.join(helpers.ROOT, "docs", "tables", "DRILLING.md")


# ======================================================================
# ORACLE: the old pipeline, ported as literally as possible.
#   parameter_exporter.py  export_drilling_coordinates_paired (logging removed,
#                          timestamps injected, adsk import dropped)
#   order_processor.py     fallback when it returns False + compiled file
# ======================================================================
class FakeParam(object):
    def __init__(self, name, value, expression="", comment=""):
        self.name = name
        self.value = value          # Fusion internal units (cm) for lengths
        self.expression = expression
        self.comment = comment


class FakeDesign(object):
    def __init__(self, user, model):
        self.userParameters = list(user)
        self.allParameters = list(user) + list(model)


class OldExporter(object):
    def convert_to_display_units(self, value, unit):
        if unit == 'in':
            return value / 2.54
        return value

    def export_drilling_coordinates_paired(self, design, component_id, order_id, component_params=None,
                                           ts=TS):
        try:
            is_stile = component_id.upper().startswith('S')
            y_dist = None
            user_param_names_set = {p.name for p in design.userParameters}
            all_params = list(design.userParameters) + [p for p in design.allParameters if p.name not in user_param_names_set]
            for param in all_params:
                if param.name == 'hinge_hole_x_dist':
                    try:
                        raw_value = param.value
                        y_dist = round(self.convert_to_display_units(raw_value, 'in'), 3)
                        break
                    except Exception:
                        pass
            if y_dist is None:
                return False, f"No hinge_hole_x_dist parameter found for {component_id}", ""
            hole_pairs = []

            def _extract_param(params, key):
                data = params.get(key)
                if data is None:
                    return None
                return data[0] if isinstance(data, list) else data

            if is_stile:
                capture_left = False
                capture_right = False
                if component_params:
                    left_side_data = component_params.get('left_side_door')
                    if left_side_data is not None:
                        left_has_door = bool(left_side_data[0] if isinstance(left_side_data, list) else left_side_data)
                    else:
                        left_has_door = False
                    if left_has_door:
                        ld_hinging_right = bool(_extract_param(component_params, 'LD_hinging_right'))
                        if ld_hinging_right:
                            capture_left = True
                    right_side_data = component_params.get('right_side_door')
                    if right_side_data is not None:
                        right_has_door = bool(right_side_data[0] if isinstance(right_side_data, list) else right_side_data)
                    else:
                        right_has_door = False
                    if right_has_door:
                        rd_hinging_right = bool(_extract_param(component_params, 'RD_hinging_right'))
                        if not rd_hinging_right:
                            capture_right = True
                else:
                    for param in all_params:
                        if param.name in ['left_interior_drilling', 'left_exterior_drilling']:
                            if hasattr(param, 'value') and param.value > 0:
                                capture_left = True
                        elif param.name in ['right_interior_drilling', 'right_exterior_drilling']:
                            if hasattr(param, 'value') and param.value > 0:
                                capture_right = True
                if not capture_left and not capture_right:
                    return False, f"No drilling required for {component_id} (no door hinges onto stile)", ""
                early_series_id = str(_extract_param(component_params, 'series_id') or '') if component_params else ''
                _series_match = _re.search(r'(3\d{3})', early_series_id)
                _normalized = (_series_match.group(1)[0] + '0' + _series_match.group(1)[2:]) if _series_match else ''
                is_3086 = _normalized == '3086'
                suffix = '_y_value'
                left_drilling_params = []
                right_drilling_params = []
                if capture_left:
                    left_drilling_params = [
                        f'LD_stile_top_hinge_hole_1{suffix}', f'LD_stile_top_hinge_hole_2{suffix}',
                        f'LD_stile_mid_top_hinge_hole_1{suffix}', f'LD_stile_mid_top_hinge_hole_2{suffix}',
                        f'LD_stile_mid_bottom_hinge_hole_1{suffix}', f'LD_stile_mid_bottom_hinge_hole_2{suffix}',
                        f'LD_stile_bottom_hinge_hole_1{suffix}', f'LD_stile_bottom_hinge_hole_2{suffix}']
                if capture_right:
                    right_drilling_params = [
                        f'RD_stile_top_hinge_hole_1{suffix}', f'RD_stile_top_hinge_hole_2{suffix}',
                        f'RD_stile_mid_top_hinge_hole_1{suffix}', f'RD_stile_mid_top_hinge_hole_2{suffix}',
                        f'RD_stile_mid_bottom_hinge_hole_1{suffix}', f'RD_stile_mid_bottom_hinge_hole_2{suffix}',
                        f'RD_stile_bottom_hinge_hole_1{suffix}', f'RD_stile_bottom_hinge_hole_2{suffix}']
                drilling_param_names = left_drilling_params + right_drilling_params
            else:
                drilling_param_names = [
                    'top_hinge_hole_1_y_value', 'top_hinge_hole_2_y_value',
                    'mid_top_hinge_hole_1_y_value', 'mid_top_hinge_hole_2_y_value',
                    'mid_bottom_hinge_hole_1_y_value', 'mid_bottom_hinge_hole_2_y_value',
                    'bottom_hinge_hole_1_y_value', 'bottom_hinge_hole_2_y_value']

            def _compute_door_workstop(hinging_right, swinging_out):
                return 1 if bool(hinging_right) == bool(swinging_out) else 4

            def _compute_stile_workstop(hinging_right, swinging_out):
                return 4 if bool(hinging_right) == bool(swinging_out) else 1

            def _compute_3086_stile_workstop(hinging_right, swinging_out):
                return 1 if bool(hinging_right) == bool(swinging_out) else 4

            workstop = 1
            drill_name = ''
            left_workstop = 1
            left_drill_name = ''
            right_workstop = 1
            right_drill_name = ''
            series_id = ''
            if component_params:
                series_id = str(_extract_param(component_params, 'series_id') or '')
                _ws_match = _re.search(r'(3\d{3})', series_id)
                _ws_normalized = (_ws_match.group(1)[0] + '0' + _ws_match.group(1)[2:]) if _ws_match else ''
                json_program_name = str(_extract_param(component_params, 'program_name') or '').strip() if component_params else ''
                if is_stile:
                    is_3086 = _ws_normalized == '3086'
                    stile_ws_func = _compute_3086_stile_workstop if is_3086 else _compute_stile_workstop
                    left_side_data = component_params.get('left_side_door')
                    left_has_door = bool(left_side_data[0] if isinstance(left_side_data, list) else left_side_data) if left_side_data is not None else False
                    if left_has_door and capture_left:
                        ld_hinging = bool(_extract_param(component_params, 'LD_hinging_right'))
                        ld_swinging = bool(_extract_param(component_params, 'LD_swinging_out'))
                        left_workstop = stile_ws_func(ld_hinging, ld_swinging)
                    right_side_data = component_params.get('right_side_door')
                    right_has_door = bool(right_side_data[0] if isinstance(right_side_data, list) else right_side_data) if right_side_data is not None else False
                    if right_has_door and capture_right:
                        rd_hinging = bool(_extract_param(component_params, 'RD_hinging_right'))
                        rd_swinging = bool(_extract_param(component_params, 'RD_swinging_out'))
                        right_workstop = stile_ws_func(rd_hinging, rd_swinging)
                    if json_program_name:
                        left_drill_name = json_program_name
                        right_drill_name = json_program_name
                    else:
                        left_segment = ''
                        right_segment = ''
                        if left_has_door:
                            ld_hinging = bool(_extract_param(component_params, 'LD_hinging_right'))
                            ld_swinging = bool(_extract_param(component_params, 'LD_swinging_out'))
                            hinge_code = 'RH' if ld_hinging else 'LH'
                            swing_code = 'O/S' if ld_swinging else 'I/S'
                            attach_code = 'HINGE' if capture_left else 'KEEPING'
                            left_segment = f'{hinge_code} {swing_code} {attach_code}'
                        if right_has_door:
                            rd_hinging = bool(_extract_param(component_params, 'RD_hinging_right'))
                            rd_swinging = bool(_extract_param(component_params, 'RD_swinging_out'))
                            hinge_code = 'RH' if rd_hinging else 'LH'
                            swing_code = 'O/S' if rd_swinging else 'I/S'
                            attach_code = 'HINGE' if capture_right else 'KEEPING'
                            right_segment = f'{hinge_code} {swing_code} {attach_code}'
                        drill_segments = [s for s in [left_segment, right_segment] if s]
                        combined_drill_name = f'{series_id} {" | ".join(drill_segments)}'
                        left_drill_name = combined_drill_name
                        right_drill_name = combined_drill_name
                else:
                    hinging_right = bool(_extract_param(component_params, 'door_hinging_right'))
                    swinging_out = bool(_extract_param(component_params, 'door_swinging_out'))
                    workstop = _compute_door_workstop(hinging_right, swinging_out)
                    if json_program_name:
                        drill_name = json_program_name
                    else:
                        hinge_code = 'RH' if hinging_right else 'LH'
                        swing_code = 'O/S' if swinging_out else 'I/S'
                        drill_name = f'{series_id} {hinge_code} {swing_code} DOOR'

            left_flip_rotate = 1
            right_flip_rotate = 1
            if is_stile:
                all_params_dict = {p.name: p for p in all_params}
                li = all_params_dict.get('left_interior_drilling')
                le = all_params_dict.get('left_exterior_drilling')
                ri = all_params_dict.get('right_interior_drilling')
                re = all_params_dict.get('right_exterior_drilling')
                li_val = 1 if (li and li.value != 0) else 0
                le_val = 1 if (le and le.value != 0) else 0
                ri_val = 1 if (ri and ri.value != 0) else 0
                re_val = 1 if (re and re.value != 0) else 0
                has_any_interior = li_val or ri_val
                has_any_exterior = le_val or re_val
                mixed = has_any_interior and has_any_exterior
                if mixed:
                    left_flip_rotate = 4 if li_val else 3
                    right_flip_rotate = 4 if ri_val else 3
                else:
                    left_flip_rotate = 1
                    right_flip_rotate = 1
                flip_rotate = 1
            else:
                flip_rotate = 1

            process_timestamp = ts
            for param in all_params:
                if param.name in drilling_param_names:
                    try:
                        raw_value = param.value
                        x_value = round(self.convert_to_display_units(raw_value, 'in'), 3)
                        if is_stile and is_3086:
                            x_value = round(x_value - 1.4375, 3)
                        if is_stile and param.name in left_drilling_params:
                            hole_ws = left_workstop
                            hole_dn = left_drill_name
                            hole_fr = left_flip_rotate
                        elif is_stile and param.name in right_drilling_params:
                            hole_ws = right_workstop
                            hole_dn = right_drill_name
                            hole_fr = right_flip_rotate
                        else:
                            hole_ws = workstop
                            hole_dn = drill_name
                            hole_fr = flip_rotate
                        hole_pairs.append({
                            'ProgramName': f'1-{component_id}-{order_id}',
                            'WorkStop': hole_ws,
                            'PositionX': x_value,
                            'PositionY': y_dist,
                            'PositionZ': None,
                            'Function': 4,
                            'ToolDM': 1,
                            'FlipRotate': hole_fr,
                            'SSMA_TimeStamp': None,
                            'Fusion_TimeStamp': process_timestamp,
                            'DrillName': hole_dn
                        })
                    except Exception:
                        pass
            if len(hole_pairs) == 0:
                _ts = ts
                for ws in (1, 4):
                    hole_pairs.append({
                        'ProgramName': f'1-{component_id}-{order_id}',
                        'WorkStop': ws,
                        'PositionX': 121,
                        'PositionY': 0.197,
                        'PositionZ': None,
                        'Function': 4,
                        'ToolDM': 1,
                        'FlipRotate': 1,
                        'SSMA_TimeStamp': None,
                        'Fusion_TimeStamp': _ts,
                        'DrillName': 'NO DRILLING REQUIRED'
                    })
            AIR_DRILL_X = 121
            AIR_DRILL_Y = 0.197
            flip_rotate_values = set(h['FlipRotate'] for h in hole_pairs)
            skip_air_drill = bool(flip_rotate_values & {2, 3})
            workstop_values = set(h['WorkStop'] for h in hole_pairs)
            if len(workstop_values) == 1 and not skip_air_drill:
                used_ws = workstop_values.pop()
                opposite_ws = 4 if used_ws == 1 else 1
                ref = hole_pairs[0]
                hole_pairs.append({
                    'ProgramName': ref['ProgramName'],
                    'WorkStop': opposite_ws,
                    'PositionX': AIR_DRILL_X,
                    'PositionY': AIR_DRILL_Y,
                    'PositionZ': None,
                    'Function': ref['Function'],
                    'ToolDM': ref['ToolDM'],
                    'FlipRotate': ref['FlipRotate'],
                    'SSMA_TimeStamp': ref['SSMA_TimeStamp'],
                    'Fusion_TimeStamp': ref['Fusion_TimeStamp'],
                    'DrillName': 'NO DRILLING REQUIRED'
                })
            paired_data = {
                'component_id': component_id,
                'order_id': order_id,
                'unit': 'in',
                'description': 'Paired drilling coordinates (X, Y)',
                'coordinates': hole_pairs
            }
            return True, f"Computed {len(hole_pairs)} paired coordinates for {component_id}", paired_data
        except Exception as e:
            return False, f"Paired drilling coordinate export failed: {str(e)}", ""


def old_component(design, comp_id, order_id, parameters, ts=TS):
    """order_processor: paired export, or the air-drilling fallback."""
    paired_success, paired_msg, paired_data = OldExporter().export_drilling_coordinates_paired(
        design, comp_id, order_id, parameters, ts=ts)
    if paired_success:
        return paired_data
    _ts = ts
    return {
        'component_id': comp_id, 'order_id': order_id, 'unit': 'in',
        'description': 'No drilling required',
        'coordinates': [{
            'ProgramName': f'1-{comp_id}-{order_id}', 'WorkStop': 1, 'PositionX': 121,
            'PositionY': 0.197, 'PositionZ': None, 'Function': 4, 'ToolDM': 1, 'FlipRotate': 1,
            'SSMA_TimeStamp': None, 'Fusion_TimeStamp': _ts, 'DrillName': 'NO DRILLING REQUIRED'
        }, {
            'ProgramName': f'1-{comp_id}-{order_id}', 'WorkStop': 4, 'PositionX': 121,
            'PositionY': 0.197, 'PositionZ': None, 'Function': 4, 'ToolDM': 1, 'FlipRotate': 1,
            'SSMA_TimeStamp': None, 'Fusion_TimeStamp': _ts, 'DrillName': 'NO DRILLING REQUIRED'
        }]
    }


def old_compile(order_id, by_type):
    compiled_paired_data = {order_id: {
        'unit': 'in',
        'description': 'Compiled paired drilling coordinates for all components organized by type',
        'doors': [], 'panels': [], 'stiles': []}}
    for comp_type in ['doors', 'panels', 'stiles']:
        for comp_data in by_type.get(comp_type, []):
            compiled_paired_data[order_id][comp_type].append({
                'component_id': comp_data.get('component_id'),
                'coordinates': comp_data.get('coordinates', [])})
    return compiled_paired_data


# ======================================================================
# Case builder: one description -> (old design + raw params, new inputs)
# ======================================================================
class Model(object):
    """Ordered model parameters: (name, value, is_length, is_user)."""

    def __init__(self):
        self.items = []

    def add(self, name, value, length=True, user=True):
        self.items = [i for i in self.items if i[0] != name]
        self.items.append((name, value, length, user))
        return self

    def drop(self, *names):
        self.items = [i for i in self.items if i[0] not in names]
        return self

    def design(self):
        # value is stored in INCHES for lengths; Fusion holds cm
        user = [FakeParam(n, v * 2.54 if (ln and isinstance(v, (int, float))) else v)
                for n, v, ln, u in self.items if u]
        model = [FakeParam(n, v * 2.54 if (ln and isinstance(v, (int, float))) else v)
                 for n, v, ln, u in self.items if not u]
        return FakeDesign(user, model)

    def values(self):
        """What the new code receives: userParameters then the rest of
        allParameters, Fusion value (cm) / 2.54 for lengths."""
        d = self.design()
        lengths = {n for n, v, ln, u in self.items if ln}
        user_names = {p.name for p in d.userParameters}
        out = {}
        for p in list(d.userParameters) + [p for p in d.allParameters if p.name not in user_names]:
            if p.name in lengths and isinstance(p.value, (int, float)):
                out[p.name] = p.value / 2.54
            else:
                out[p.name] = p.value
        return out


def wrap(order_values):
    """Order values as the old pipeline saw them: [value, type, description]."""
    if order_values is None:
        return None
    out = {}
    for k, v in order_values.items():
        t = "bool" if isinstance(v, bool) else ("string" if isinstance(v, str) else "float")
        out[k] = [v, t, "desc"]
    return out


def hole_model(rng, names, y=0.968, model_only=()):
    m = Model()
    m.add("component_height", 88.5)
    m.add("hinge_hole_x_dist", y, user="hinge_hole_x_dist" not in model_only)
    tricky = [1.72, 3.22, 26.6475, 28.1465, 54.0435, 55.5425, 78.9705, 80.4695,
              5.0, 1.4375, 1.43749, 0.0005, 104.97, 106.47000000000001]
    for i, n in enumerate(names):
        v = tricky[i % len(tricky)] if rng.random() < 0.5 else rng.uniform(0.5, 110)
        m.add(n, v, user=n not in model_only)
    m.add("d101", rng.uniform(0, 5), user=False)
    return m


ALL_STILE_HOLES = drilling.stile_hole_params("LD") + drilling.stile_hole_params("RD")


def stile_model(rng, flags, variant="full"):
    names = list(ALL_STILE_HOLES)
    rng.shuffle(names)          # model order drives record order
    m = hole_model(rng, names, y=1.268)
    for n, v in zip(drilling.STILE_FLAG_PARAMS, flags):
        if v is not None:
            m.add(n, v, length=False, user=rng.random() < 0.7)
    return apply_variant(m, variant, names)


def door_model(rng, variant="full"):
    names = drilling.door_hole_params()
    m = hole_model(rng, names)
    return apply_variant(m, variant, names)


VARIANTS = ("full", "no_y", "y_model_only", "some_missing", "no_holes", "bad_hole", "bad_y",
            "holes_model_only")


def apply_variant(m, variant, names):
    if variant == "no_y":
        m.drop("hinge_hole_x_dist")
    elif variant == "y_model_only":
        m.add("hinge_hole_x_dist", 0.875, user=False)
    elif variant == "some_missing":
        m.drop(*[n for n in names if "mid_top" in n])
    elif variant == "no_holes":
        m.drop(*names)
    elif variant == "bad_hole":
        m.add(names[0], "12 in", length=False)
    elif variant == "bad_y":
        m.add("hinge_hole_x_dist", "oops", length=False)
    elif variant == "holes_model_only":
        for n in names:
            v = [i for i in m.items if i[0] == n]
            if v:
                m.add(n, v[0][1], user=False)
    return m


def run_both(ctype, cid, order_id, order_values, model, series_arg=None, old_params=None):
    old = old_component(model.design(), cid, order_id,
                        wrap(order_values) if old_params is None else old_params)
    if series_arg is None:
        series_arg = str((order_values or {}).get("series_id") or "")
    new = drilling.component_drilling(ctype, cid, order_id, series_arg, order_values,
                                      model.values(), now=TS)
    return old, new


def strip(new):
    out = dict(new)
    out.pop("summary", None)
    return out


class OracleMixin(object):
    def assertSame(self, old, new, ctx):
        self.assertIsNotNone(new, ctx)
        a = json.dumps(old, indent=2)
        b = json.dumps(strip(new), indent=2)
        if a != b:
            self.fail("mismatch for %s\nOLD: %s\nNEW: %s" % (ctx, a, b))


# ======================================================================
# Exhaustive oracle comparisons
# ======================================================================
class DoorOracle(OracleMixin, unittest.TestCase):
    def test_all_door_combinations(self):
        rng = random.Random(1)
        n = 0
        for series, hr, so, pn, variant in itertools.product(
                SERIES, (False, True), (False, True), (None, "", "   ", " PROG X ", "absent"), VARIANTS):
            ov = {"series_id": series, "door_hinging_right": hr, "door_swinging_out": so}
            if pn != "absent":
                ov["program_name"] = pn
            old, new = run_both("door", "D1", "IBUS1", ov, door_model(rng, variant))
            self.assertSame(old, new, (series, hr, so, pn, variant))
            n += 1
        self.assertEqual(n, 7 * 2 * 2 * 5 * len(VARIANTS))

    def test_door_value_representations(self):
        rng = random.Random(2)
        reps = (True, False, 1, 0, None, "missing")
        for series, hr, so in itertools.product(("3082G.67P", "3186", None, "missing"), reps, reps):
            ov = {}
            for k, v in (("series_id", series), ("door_hinging_right", hr), ("door_swinging_out", so)):
                if v != "missing":
                    ov[k] = v
            old, new = run_both("door", "D2", "O", ov, door_model(rng))
            self.assertSame(old, new, (series, hr, so))

    def test_door_without_order_params(self):
        rng = random.Random(3)
        for variant in VARIANTS:
            for ov in (None, {}):
                old, new = run_both("door", "D1", "O", ov, door_model(rng, variant))
                self.assertSame(old, new, (variant, ov))


class StileOracle(OracleMixin, unittest.TestCase):
    def test_all_stile_combinations(self):
        rng = random.Random(10)
        n = 0
        flag_combos = list(itertools.product((0, 1), repeat=4))
        for series, ls, rs, ldh, rdh, lds, rds, flags, pn in itertools.product(
                SERIES, (False, True), (False, True), (False, True), (False, True),
                (False, True), (False, True), flag_combos, (None, "STILE PROG")):
            ov = {"series_id": series, "left_side_door": ls, "right_side_door": rs,
                  "LD_hinging_right": ldh, "RD_hinging_right": rdh,
                  "LD_swinging_out": lds, "RD_swinging_out": rds, "program_name": pn}
            old, new = run_both("stile", "S1", "IBUS2", ov, stile_model(rng, flags))
            self.assertSame(old, new, ov)
            n += 1
        self.assertEqual(n, 7 * 2 ** 6 * 16 * 2)

    def test_stile_model_variants(self):
        rng = random.Random(11)
        for series, variant, ls, rs, ldh, rdh in itertools.product(
                ("3082G.67P", "3086G.67P", "3186"), VARIANTS, (False, True), (False, True),
                (False, True), (False, True)):
            ov = {"series_id": series, "left_side_door": ls, "right_side_door": rs,
                  "LD_hinging_right": ldh, "RD_hinging_right": rdh,
                  "LD_swinging_out": True, "RD_swinging_out": False}
            flags = (rng.randint(0, 1), rng.randint(0, 1), rng.randint(0, 1), rng.randint(0, 1))
            old, new = run_both("stile", "S3", "O", ov, stile_model(rng, flags, variant))
            self.assertSame(old, new, (series, variant, ls, rs, ldh, rdh, flags))

    def test_stile_value_representations_and_missing_params(self):
        rng = random.Random(12)
        reps = (True, 1, 0, None, "missing")
        keys = ("left_side_door", "right_side_door", "LD_hinging_right", "RD_hinging_right",
                "LD_swinging_out", "RD_swinging_out")
        for vals in itertools.product(reps, repeat=6):
            ov = {"series_id": "3086G"}
            for k, v in zip(keys, vals):
                if v != "missing":
                    ov[k] = v
            flags = (rng.randint(0, 1), None, rng.randint(0, 1), rng.choice((0, 1, None)))
            old, new = run_both("stile", "S2", "O", ov, stile_model(rng, flags))
            self.assertSame(old, new, (vals, flags))

    def test_stile_flag_values_and_model_flag_fallback(self):
        rng = random.Random(13)
        for flags in itertools.product((0, 1, 2, -1, 0.5, None), repeat=4):
            for ov in (None, {}, {"series_id": "3082", "left_side_door": True,
                                  "LD_hinging_right": True}):
                for variant in ("full", "no_holes", "some_missing"):
                    old, new = run_both("stile", "S1", "O", ov, stile_model(rng, flags, variant))
                    self.assertSame(old, new, (flags, ov, variant))

    def test_empty_list_value_falls_back_like_old(self):
        rng = random.Random(14)
        model = stile_model(rng, (1, 0, 0, 0))
        old = old_component(model.design(), "S1", "O", {"left_side_door": [], "series_id": ["3082"]})
        new = drilling.component_drilling("stile", "S1", "O", "3082", {"left_side_door": [],
                                          "series_id": ["3082"]}, model.values(), now=TS)
        self.assertSame(old, new, "empty list")
        self.assertEqual(new["description"], "No drilling required")


# ======================================================================
# Direct behaviour tests
# ======================================================================
def door_values(x_list=(1.72, 3.22, 26.647, 28.147, 54.043, 55.543, 78.97, 80.47), y=0.968):
    mv = {"hinge_hole_x_dist": y}
    mv.update(zip(drilling.door_hole_params(), x_list))
    return mv


class Behaviour(unittest.TestCase):
    def test_normalize_series(self):
        self.assertEqual(drilling.normalize_series("3182G.67P"), "3082")
        self.assertEqual(drilling.normalize_series("3186"), "3086")
        self.assertEqual(drilling.normalize_series("S3081"), "3081")
        self.assertEqual(drilling.normalize_series("2082G"), "")
        self.assertEqual(drilling.normalize_series(None), "")
        self.assertEqual(drilling.normalize_series(""), "")

    def test_door_record_and_air_drill(self):
        out = drilling.component_drilling("door", "D1", "IBUS9", "3082G.67P",
                                          {"series_id": "3082G.67P", "door_hinging_right": True,
                                           "door_swinging_out": True}, door_values(), now=TS)
        c = out["coordinates"]
        self.assertEqual(len(c), 9)
        self.assertEqual(list(c[0].keys()), list(drilling.RECORD_FIELDS))
        self.assertEqual(c[0], {"ProgramName": "1-D1-IBUS9", "WorkStop": 1, "PositionX": 1.72,
                                "PositionY": 0.968, "PositionZ": None, "Function": 4, "ToolDM": 1,
                                "FlipRotate": 1, "SSMA_TimeStamp": None, "Fusion_TimeStamp": TS,
                                "DrillName": "3082G.67P RH O/S DOOR"})
        self.assertEqual((c[-1]["WorkStop"], c[-1]["PositionX"], c[-1]["PositionY"], c[-1]["DrillName"]),
                         (4, 121, 0.197, "NO DRILLING REQUIRED"))
        self.assertTrue(out["summary"]["air_drill_added"])
        self.assertEqual(out["summary"]["workstops"], [1, 4])

    def test_other_types_and_disabled(self):
        self.assertIsNone(drilling.component_drilling("panel", "P1", "O", "", {}, door_values()))
        self.assertIsNone(drilling.component_drilling("door", "D1", "O", "", {}, door_values(),
                                                      cfg={"enabled": False}))
        self.assertIsNone(drilling.component_drilling("stile", "S1", "O", "", {}, door_values(),
                                                      cfg={"types": ["door"]}))

    def test_type_comes_from_ctype_not_id_prefix(self):
        # old code: is_stile = component_id.startswith('S'); new: ctype decides
        out = drilling.component_drilling("door", "SD1", "O", "3082", {"door_hinging_right": True},
                                          door_values(), now=TS)
        self.assertEqual(len(out["coordinates"]), 9)

    def test_config_changes_apply(self):
        cfg = {"air_drill": {"x": 99, "y": 1.5, "name": "AIR"}, "function": 7, "tool_dm": 2,
               "workstops": {"door": {"same": 4, "different": 1}}}
        out = drilling.component_drilling("door", "D1", "O", "3082", {"door_hinging_right": False,
                                          "door_swinging_out": False}, door_values(), cfg=cfg, now=TS)
        c = out["coordinates"]
        self.assertEqual(c[0]["WorkStop"], 4)
        self.assertEqual((c[0]["Function"], c[0]["ToolDM"]), (7, 2))
        self.assertEqual((c[-1]["WorkStop"], c[-1]["PositionX"], c[-1]["PositionY"], c[-1]["DrillName"]),
                         (1, 99, 1.5, "AIR"))
        # per-series override + offset
        cfg = {"workstops": {"door_3081": {"same": 4, "different": 4}},
               "x_offset_by_series": {"3082": 1.0}}
        ov = {"series_id": "3081G", "door_hinging_right": 0, "door_swinging_out": 0}
        self.assertEqual(drilling.component_drilling("door", "D1", "O", "", ov, door_values(),
                                                     cfg=cfg)["coordinates"][0]["WorkStop"], 4)
        mv = {"hinge_hole_x_dist": 1.268, "LD_stile_top_hinge_hole_1_y_value": 10.0}
        out = drilling.component_drilling("stile", "S1", "O", "", {"series_id": "3182", "left_side_door": 1,
                                          "LD_hinging_right": 1}, mv, cfg=cfg)
        self.assertEqual(out["coordinates"][0]["PositionX"], 11.0)
        # whole config dict with a "drilling" section is accepted too
        out = drilling.component_drilling("door", "D1", "O", "", {"door_hinging_right": 1},
                                          door_values(), cfg={"drilling": {"function": 9}})
        self.assertEqual(out["coordinates"][0]["Function"], 9)

    def test_3086_offset_and_workstops(self):
        mv = {"hinge_hole_x_dist": 1.268, "LD_stile_top_hinge_hole_1_y_value": 7.0325,
              "RD_stile_top_hinge_hole_1_y_value": 3.47,
              "left_interior_drilling": 1, "right_exterior_drilling": 1}
        ov = {"series_id": "3186G", "left_side_door": True, "right_side_door": True,
              "LD_hinging_right": True, "RD_hinging_right": False,
              "LD_swinging_out": False, "RD_swinging_out": True}
        out = drilling.component_drilling("stile", "S1", "O", "", ov, mv, now=TS)
        c = out["coordinates"]
        # rounded to 3 dp BEFORE the offset, then rounded again (7.0325 -> 7.032 -> 5.595)
        self.assertEqual([r["PositionX"] for r in c], [round(round(7.0325, 3) - 1.4375, 3),
                                                       round(3.47 - 1.4375, 3)])
        # left I/S + right O/S on a 3X86 -> WS4 & flip & WS4 (spreadsheet); 3X82 -> WS1 & WS1.
        # Mixed interior/exterior -> FlipRotate 4 / 3 and no air drill record.
        self.assertEqual([(r["WorkStop"], r["FlipRotate"]) for r in c], [(4, 4), (4, 3)])
        self.assertFalse(out["summary"]["air_drill_added"])
        ov["series_id"] = "3082G"
        c = drilling.component_drilling("stile", "S1", "O", "", ov, mv, now=TS)["coordinates"]
        self.assertEqual([r["WorkStop"] for r in c], [1, 1])
        self.assertEqual(c[0]["DrillName"], "3082G RH I/S HINGE | LH O/S HINGE")

    def test_timestamp_forms(self):
        import datetime
        ov = {"door_hinging_right": 1}
        d = datetime.datetime(2026, 1, 2, 3, 4, 5)
        for now in (d, lambda: d, "2026-01-02 03:04:05"):
            out = drilling.component_drilling("door", "D1", "O", "", ov, door_values(), now=now)
            self.assertEqual(out["coordinates"][0]["Fusion_TimeStamp"], "2026-01-02 03:04:05")
        out = drilling.component_drilling("door", "D1", "O", "", ov, door_values())
        self.assertRegex(out["coordinates"][0]["Fusion_TimeStamp"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")


class Compiled(unittest.TestCase):
    def test_matches_old_compile_and_writes(self):
        rng = random.Random(20)
        comps, by_type = [], {"doors": [], "panels": [], "stiles": []}
        seq = [("stile", "S1"), ("door", "D1"), ("stile", "S2"), ("door", "D2")]
        for ctype, cid in seq:
            if ctype == "door":
                ov = {"series_id": "3082G", "door_hinging_right": rng.random() < 0.5}
                m = door_model(rng)
            else:
                ov = {"series_id": "3086G", "left_side_door": True, "LD_hinging_right": True}
                m = stile_model(rng, (1, 0, 0, 1))
            old, new = run_both(ctype, cid, "IBUS7", ov, m)
            comps.append((ctype, new))
            by_type[ctype + "s"].append(old)
        comps.append(("panel", None))
        compiled = drilling.compile_order("IBUS7", comps)
        self.assertEqual(json.dumps(compiled, indent=2), json.dumps(old_compile("IBUS7", by_type), indent=2))
        self.assertEqual(list(compiled["IBUS7"].keys()), ["unit", "description", "doors", "panels", "stiles"])
        self.assertEqual([e["component_id"] for e in compiled["IBUS7"]["doors"]], ["D1", "D2"])
        self.assertIsNone(drilling.compile_order("X", []))
        self.assertIsNone(drilling.compile_order("X", [("panel", None), ("door", None)]))
        tmp = tempfile.mkdtemp(prefix="nfe_drill_")
        try:
            path = os.path.join(tmp, "sub", drilling.compiled_filename("IBUS7"))
            drilling.write_compiled(path, compiled)
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            self.assertEqual(text, json.dumps(compiled, indent=2))
            self.assertTrue(path.endswith("IBUS7.json"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================
# Real data
# ======================================================================
# S5 / S7 from "G Code Arc Analysis/New folder/S{5,7}_all_parameters.json"
# (order IBUS789123, series 3082G.67P) - model values in inches.
S5 = {"flags": (0.0, 0.0, 1.0, 0.0),
      "ov": {"series_id": "3082G.67P", "left_side_door": 1.0, "right_side_door": 1.0,
             "LD_hinging_right": 0.0, "LD_swinging_out": 1.0, "RD_hinging_right": 0.0,
             "RD_swinging_out": 0.0},
      "LD": (30.907500000000002, 32.407500000000006, 54.90741666666667, 56.40741666666667,
             81.34508333333335, 82.84508333333335, 104.97, 106.47000000000001),
      "RD": (4.6575000000000255, 6.1575000000000255, 35.66708333333335, 37.16708333333336,
             66.64541666666669, 68.14541666666669, 97.28000000000003, 98.78000000000003)}
S7 = {"flags": (0.0, 1.0, 0.0, 1.0),
      "ov": {"series_id": "3082G.67P", "left_side_door": 1.0, "right_side_door": 1.0,
             "LD_hinging_right": 1.0, "LD_swinging_out": 1.0, "RD_hinging_right": 0.0,
             "RD_swinging_out": 1.0},
      "LD": (7.032499999999991, 8.53249999999999, 31.740749999999995, 33.24074999999999,
             58.88674999999999, 60.38674999999999, 83.22, 84.72),
      "RD": (3.4699999999999975, 4.969999999999998, 28.625416666666666, 30.125416666666666,
             53.749583333333334, 55.249583333333334, 78.53, 80.03)}


def real_stile_model(s):
    m = Model()
    m.add("hinge_hole_x_dist", 1.268)
    for side in ("LD", "RD"):
        for n, v in zip(drilling.stile_hole_params(side), s[side]):
            m.add(n, v)
    for n, v in zip(drilling.STILE_FLAG_PARAMS, s["flags"]):
        m.add(n, v, length=False)
    return m


def from_all_parameters(path):
    with open(path, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    m = Model()
    for section, user in (("user_parameters", True), ("model_parameters", False)):
        for name, p in (d.get(section) or {}).items():
            m.add(name, p.get("value"), length=(p.get("unit") == "in"), user=user)
    return m, d


class RealData(OracleMixin, unittest.TestCase):
    def test_s5_s7_real_model_values(self):
        s5 = drilling.component_drilling("stile", "S5", "IBUS789123", "", S5["ov"],
                                         real_stile_model(S5).values(), now=TS)
        old = old_component(real_stile_model(S5).design(), "S5", "IBUS789123", wrap(S5["ov"]))
        self.assertSame(old, s5, "S5")
        c = s5["coordinates"]
        # right door LH I/S hinges onto the stile -> RD holes on WS4, air drill on WS1
        self.assertEqual([r["WorkStop"] for r in c], [4] * 8 + [1])
        self.assertEqual([r["PositionX"] for r in c[:3]], [4.658, 6.158, 35.667])
        self.assertEqual(c[0]["DrillName"], "3082G.67P LH O/S KEEPING | LH I/S HINGE")
        self.assertEqual(c[-1]["DrillName"], "NO DRILLING REQUIRED")

        s7 = drilling.component_drilling("stile", "S7", "IBUS789123", "", S7["ov"],
                                         real_stile_model(S7).values(), now=TS)
        old = old_component(real_stile_model(S7).design(), "S7", "IBUS789123", wrap(S7["ov"]))
        self.assertSame(old, s7, "S7")
        c = s7["coordinates"]
        # both O/S (same swings): left RH O/S -> WS4, right LH O/S -> WS1; both exterior -> FR 1
        self.assertEqual([r["WorkStop"] for r in c], [4] * 8 + [1] * 8)
        self.assertEqual(set(r["FlipRotate"] for r in c), {1})
        self.assertEqual(c[0]["DrillName"], "3082G.67P RH O/S HINGE | LH O/S HINGE")

    @unittest.skipUnless(os.path.isdir(os.path.join(OLD_REPO, "testing_suite", "actual_output")),
                         "old repository not available")
    def test_door_validation_suite_real_inputs_and_model_values(self):
        """Every 3X8X_DV order with an exported D1_all_parameters.json: old vs new."""
        base = os.path.join(OLD_REPO, "testing_suite")
        count = 0
        for name in sorted(os.listdir(os.path.join(base, "actual_output"))):
            params = os.path.join(base, "actual_output", name, "D1_all_parameters.json")
            order = os.path.join(base, "door_validation_tests", name + ".json")
            if not (os.path.isfile(params) and os.path.isfile(order)):
                continue
            with open(order, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            comp = raw["doors"][0]
            m, _ = from_all_parameters(params)
            ov = {k: (v[0] if isinstance(v, list) else v) for k, v in comp["parameters"].items()}
            old = old_component(m.design(), "D1", name, comp["parameters"])
            new = drilling.component_drilling("door", "D1", name, "", ov, m.values(), now=TS)
            self.assertSame(old, new, name)
            count += 1
        self.assertGreater(count, 100)

    @unittest.skipUnless(os.path.isdir(os.path.join(OLD_REPO, "G Code Arc Analysis", "New folder")),
                         "old repository not available")
    def test_real_stile_parameter_exports(self):
        for sid in ("S5", "S7"):
            path = os.path.join(OLD_REPO, "G Code Arc Analysis", "New folder", sid + "_all_parameters.json")
            m, d = from_all_parameters(path)
            up = d["user_parameters"]
            ov = {k: up[k]["value"] for k in ("left_side_door", "right_side_door", "LD_hinging_right",
                                              "RD_hinging_right", "LD_swinging_out", "RD_swinging_out")}
            ov["series_id"] = d["metadata"]["series_id"]
            old = old_component(m.design(), sid, "IBUS789123", wrap(ov))
            new = drilling.component_drilling("stile", sid, "IBUS789123", "", ov, m.values(), now=TS)
            self.assertSame(old, new, sid)
            embedded = S5 if sid == "S5" else S7
            again = drilling.component_drilling("stile", sid, "IBUS789123", "", embedded["ov"],
                                                real_stile_model(embedded).values(), now=TS)
            self.assertEqual(new["coordinates"], again["coordinates"])


# IBUS123456: real compiled output of the old pipeline (S2S File Test/
# one-time-drilling-output-compilation).  Inputs were not kept, so the hole
# positions + hinge/swing (from DrillName) are taken from the file itself; the
# test checks the record structure, workstops, ordering and air drilling.
IBUS123456 = [
    ("D1", True, True, "10:30:29", (1.72, 3.22, 26.647, 28.147, 54.043, 55.543, 78.97, 80.47)),
    ("D2", False, True, "10:33:26", (3.22, 4.72, 25.865, 27.365, 48.51, 50.01, 71.155, 72.655)),
    ("D3", False, True, "10:36:36", (5.22, 6.72, 29.168, 30.668, 55.585, 57.085, 79.532, 81.032)),
    ("D4", False, False, "10:38:49", (1.72, 3.22, 24.282, 25.782, 46.843, 48.343, 69.405, 70.905)),
    ("D5", True, True, "10:41:39", (3.72, 5.22, 29.751, 31.251, 58.251, 59.751, 84.282, 85.782)),
]


class RealOutputRegression(unittest.TestCase):
    def build_ibus123456(self):
        comps = []
        for cid, hr, so, t, xs in IBUS123456:
            out = drilling.component_drilling(
                "door", cid, "IBUS123456", "", {"series_id": "3082G.67P", "door_hinging_right": hr,
                                                "door_swinging_out": so, "program_name": None},
                door_values(xs), now="2026-03-17 " + t)
            comps.append(("door", out))
        return drilling.compile_order("IBUS123456", comps)

    def test_ibus123456_workstops_and_air(self):
        compiled = self.build_ibus123456()["IBUS123456"]
        expected_ws = {"D1": 1, "D2": 4, "D3": 4, "D4": 1, "D5": 1}
        for entry in compiled["doors"]:
            c = entry["coordinates"]
            ws = expected_ws[entry["component_id"]]
            self.assertEqual([r["WorkStop"] for r in c], [ws] * 8 + [5 - ws])
            self.assertEqual((c[-1]["PositionX"], c[-1]["PositionY"]), (121, 0.197))

    @unittest.skipUnless(os.path.isfile(os.path.join(OLD_REPO, "S2S File Test",
                                                     "one-time-drilling-output-compilation",
                                                     "IBUS123456.json")), "old repository not available")
    def test_ibus123456_matches_real_file_modulo_known_format_drift(self):
        with open(os.path.join(OLD_REPO, "S2S File Test", "one-time-drilling-output-compilation",
                               "IBUS123456.json"), "r", encoding="utf-8") as fh:
            real = json.load(fh)
        ours = copy.deepcopy(self.build_ibus123456())
        # That file predates the current code (see docs/tables/DRILLING.md section 8):
        #   component_id "1-D1-IBUS123456", PositionZ 0, air record keeps the door DrillName.
        for entry in ours["IBUS123456"]["doors"]:
            entry["component_id"] = "1-%s-IBUS123456" % entry["component_id"]
            name = entry["coordinates"][0]["DrillName"]
            for r in entry["coordinates"]:
                r["PositionZ"] = 0
                r["DrillName"] = name
        self.assertEqual(json.dumps(ours, indent=2), json.dumps(real, indent=2))

    def test_ibus447889_log(self):
        """Decisions logged by the old pipeline for order IBUS447889 (IBUS447889_log.txt)."""
        def door(hr, so):
            return {"series_id": "3082G", "door_hinging_right": hr, "door_swinging_out": so}

        def stile(ls, rs, ldh, rdh, lds, rds):
            return {"series_id": "3082G.67P", "program_name": "", "left_side_door": ls,
                    "right_side_door": rs, "LD_hinging_right": ldh, "RD_hinging_right": rdh,
                    "LD_swinging_out": lds, "RD_swinging_out": rds}

        smv = {"hinge_hole_x_dist": 1.268}
        for side in ("LD", "RD"):
            smv.update((n, 10.0 + i) for i, n in enumerate(drilling.stile_hole_params(side)))
        cases = [
            ("door", "D1", door(False, True), door_values(), 9, [4] * 8 + [1], "3082G LH O/S DOOR"),
            ("door", "D2", door(True, False), door_values(), 9, [4] * 8 + [1], "3082G RH I/S DOOR"),
            ("door", "D3", door(False, False), door_values(), 9, [1] * 8 + [4], "3082G LH I/S DOOR"),
            ("door", "D4", door(False, False), door_values(), 9, [1] * 8 + [4], "3082G LH I/S DOOR"),
            ("stile", "S1", stile(1, 1, 0, 1, 1, 0), dict(smv, left_interior_drilling=0), 2, [1, 4],
             "NO DRILLING REQUIRED"),
            ("stile", "S2", stile(1, 1, 1, 0, 0, 0), dict(smv, left_interior_drilling=1,
                                                         right_interior_drilling=1),
             16, [1] * 8 + [4] * 8, "3082G.67P RH I/S HINGE | LH I/S HINGE"),
            ("stile", "S3", stile(1, 1, 0, 0, 0, 0), dict(smv, right_interior_drilling=1), 9,
             [4] * 8 + [1], "3082G.67P LH I/S KEEPING | LH I/S HINGE"),
            ("stile", "S4", stile(1, 0, 0, 0, 0, 0), smv, 2, [1, 4], "NO DRILLING REQUIRED"),
        ]
        comps = []
        for ctype, cid, ov, mv, n, ws, name in cases:
            out = drilling.component_drilling(ctype, cid, "IBUS447889", "", ov, mv, now=TS)
            c = out["coordinates"]
            self.assertEqual(len(c), n, cid)
            self.assertEqual([r["WorkStop"] for r in c], ws, cid)
            self.assertEqual(c[0]["DrillName"], name, cid)
            self.assertEqual(set(r["FlipRotate"] for r in c), {1}, cid)
            comps.append((ctype, out))
        self.assertEqual(comps[4][1]["description"], "No drilling required")
        compiled = drilling.compile_order("IBUS447889", comps)["IBUS447889"]
        self.assertEqual(len(compiled["doors"]) + len(compiled["stiles"]), 8)   # "with 8 component(s)"


class Docs(unittest.TestCase):
    def test_doc_tables_are_current(self):
        with open(DOC, "r", encoding="utf-8") as fh:
            text = fh.read()
        start = text.index(drilling.GENERATED_BEGIN) + len(drilling.GENERATED_BEGIN)
        end = text.index(drilling.GENERATED_END)
        self.assertEqual(text[start:end].strip(), drilling.render_tables().strip(),
                         "run: cd addin/NewFusionExtension && python -m nfe.drilling "
                         "../../docs/tables/DRILLING.md")


if __name__ == "__main__":
    unittest.main()
