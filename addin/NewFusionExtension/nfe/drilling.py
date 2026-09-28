"""Hinge-hole drilling records for the Gannomat drill.

Port of the old pipeline's ``ParameterExporter.export_drilling_coordinates_paired``
(parameter_exporter.py) plus the fallback / compiled-file code in
order_processor.py.  With ``DRILLING_DEFAULTS`` the output is identical to the
old pipeline (tests/test_drilling.py runs the old function as an oracle).

Per door / stile the model's hinge-hole parameters become one record each::

    {"ProgramName": "1-D1-IBUS123456", "WorkStop": 1, "PositionX": 1.72,
     "PositionY": 0.968, "PositionZ": null, "Function": 4, "ToolDM": 1,
     "FlipRotate": 1, "SSMA_TimeStamp": null,
     "Fusion_TimeStamp": "2026-03-17 10:30:29", "DrillName": "3082G.67P RH O/S DOOR"}

and every component of an order is compiled into ONE file ``{order_id}.json``.

Inputs:
  ``order_values``  order parameter values, already unwrapped (name -> value).
  ``model_values``  model parameters after compute, name -> value in INCHES for
                    lengths, 0/1 for flags.  Iteration order matters: records
                    come out in model parameter order (old code walked
                    ``design.userParameters`` then the rest of
                    ``design.allParameters``), so pass them in that order.

``docs/tables/DRILLING.md`` is (partly) generated from this module; run
``python -m nfe.drilling <path-to-DRILLING.md>`` after changing the defaults.
"""

import copy
import datetime
import json
import re

from .jsonio import write_json_atomic

DRILLING_DEFAULTS = {
    "enabled": True,
    "types": ["door", "stile"],
    # Record used when a component needs no (or only one-sided) drilling so the
    # machine still cycles both workstops.
    "air_drill": {"x": 121, "y": 0.197, "name": "NO DRILLING REQUIRED"},
    "function": 4,
    "tool_dm": 1,
    # Added to every stile hole X (after rounding) for these normalised series.
    "x_offset_by_series": {"3086": -1.4375},
    # WorkStop when hinging_right == swinging_out ("same") or not ("different").
    # "<type>_<normalised series>" overrides "<type>" (e.g. stile_3086).
    "workstops": {"door": {"same": 1, "different": 4},
                  "stile": {"same": 4, "different": 1},
                  "stile_3086": {"same": 1, "different": 4}},
    # Model parameter that gives PositionY (distance of the holes from the edge).
    "y_param": "hinge_hole_x_dist",
}

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
WORKSTOP_PAIR = (1, 4)            # the two Gannomat workstops
FLIP_ROTATE_ROTATE = 1            # doors, and stiles unless mixed int/ext drilling
FLIP_ROTATE_EXTERIOR = 3          # mixed stile: side drilled from the exterior face
FLIP_ROTATE_INTERIOR = 4          # mixed stile: side drilled from the interior face
NO_AIR_DRILL_FLIP_ROTATE = (2, 3)  # any of these on a record -> no extra air drill record
HINGES = ("top", "mid_top", "mid_bottom", "bottom")
STILE_FLAG_PARAMS = ("left_interior_drilling", "left_exterior_drilling",
                     "right_interior_drilling", "right_exterior_drilling")
PAIRED_DESCRIPTION = "Paired drilling coordinates (X, Y)"
FALLBACK_DESCRIPTION = "No drilling required"
COMPILED_DESCRIPTION = "Compiled paired drilling coordinates for all components organized by type"
COMPILED_KEYS = (("door", "doors"), ("panel", "panels"), ("stile", "stiles"))
RECORD_FIELDS = ("ProgramName", "WorkStop", "PositionX", "PositionY", "PositionZ", "Function",
                 "ToolDM", "FlipRotate", "SSMA_TimeStamp", "Fusion_TimeStamp", "DrillName")

_SERIES = re.compile(r"(3\d{3})")


# ----------------------------------------------------------------------
# Small helpers (also used by the doc-table generator)
# ----------------------------------------------------------------------
def settings(cfg=None):
    """DRILLING_DEFAULTS deep-merged with ``cfg`` (the "drilling" config section,
    or a whole config dict that contains one)."""
    if isinstance(cfg, dict) and isinstance(cfg.get("drilling"), dict):
        cfg = cfg["drilling"]
    return _merge(DRILLING_DEFAULTS, cfg or {})


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def normalize_series(series_id):
    """'3182G.67P' -> '3082', '3186' -> '3086' (2nd digit is a generation digit).

    Like the old code only 3xxx codes are recognised: '2082' -> ''."""
    m = _SERIES.search(str(series_id or ""))
    if not m:
        return ""
    code = m.group(1)
    return code[0] + "0" + code[2:]


def door_hole_params():
    return ["%s_hinge_hole_%d_y_value" % (h, i) for h in HINGES for i in (1, 2)]


def stile_hole_params(side):
    """side: 'LD' (door on the stile's left) or 'RD'."""
    return ["%s_stile_%s_hinge_hole_%d_y_value" % (side, h, i) for h in HINGES for i in (1, 2)]


def _value(values, key):
    """Old ``_extract_param``: [value, type, desc] lists are unwrapped."""
    data = values.get(key)
    if data is None:
        return None
    return data[0] if isinstance(data, list) else data


def _is_number(v):
    return isinstance(v, (int, float))


def workstop_for(ctype, normalized_series, hinging_right, swinging_out, cfg=None):
    """WorkStop for a door, or for one side of a stile."""
    ws = settings(cfg)["workstops"]
    table = ws.get("%s_%s" % (ctype, normalized_series)) if normalized_series else None
    table = table or ws[ctype]
    return table["same"] if bool(hinging_right) == bool(swinging_out) else table["different"]


def stile_sides(order_values):
    """-> (left_has_door, capture_left, right_has_door, capture_right).

    A side is drilled only when a door is there AND it hinges onto the stile:
    left door must hinge right (LD_hinging_right), right door must hinge left
    (not RD_hinging_right)."""
    left_has_door = bool(_value(order_values, "left_side_door"))
    right_has_door = bool(_value(order_values, "right_side_door"))
    capture_left = left_has_door and bool(_value(order_values, "LD_hinging_right"))
    capture_right = right_has_door and not bool(_value(order_values, "RD_hinging_right"))
    return left_has_door, capture_left, right_has_door, capture_right


def stile_sides_from_flags(model_values):
    """Old fallback when the component has no order parameters."""
    def pos(name):
        v = model_values.get(name)
        return _is_number(v) and v > 0
    return (pos("left_interior_drilling") or pos("left_exterior_drilling"),
            pos("right_interior_drilling") or pos("right_exterior_drilling"))


def stile_flags(model_values):
    """(LI, LE, RI, RE) as 0/1 from the model's computed drilling flags."""
    out = []
    for name in STILE_FLAG_PARAMS:
        v = model_values.get(name)
        out.append(1 if (_is_number(v) and v != 0) else 0)
    return tuple(out)


def flip_rotate_for(li, le, ri, re_):
    """-> (left FlipRotate, right FlipRotate) for a stile.

    Interior AND exterior drilling on the stile (on any sides) -> per side
    4 (interior) / 3 (exterior); otherwise 1 (rotate) for everything."""
    if (li or ri) and (le or re_):
        return (FLIP_ROTATE_INTERIOR if li else FLIP_ROTATE_EXTERIOR,
                FLIP_ROTATE_INTERIOR if ri else FLIP_ROTATE_EXTERIOR)
    return FLIP_ROTATE_ROTATE, FLIP_ROTATE_ROTATE


def door_drill_name(series_id, hinging_right, swinging_out):
    return "%s %s %s DOOR" % (series_id, "RH" if hinging_right else "LH",
                              "O/S" if swinging_out else "I/S")


def stile_side_segment(hinging_right, swinging_out, hinged_here):
    return "%s %s %s" % ("RH" if hinging_right else "LH", "O/S" if swinging_out else "I/S",
                         "HINGE" if hinged_here else "KEEPING")


def _timestamp(now):
    if now is None:
        now = datetime.datetime.now()
    elif callable(now) and not isinstance(now, (datetime.datetime, datetime.date)):
        now = now()
    if isinstance(now, (datetime.datetime, datetime.date)):
        return now.strftime(TIMESTAMP_FORMAT)
    return str(now)


def _record(program, ws, x, y, fr, ts, name, cfg):
    return {"ProgramName": program, "WorkStop": ws, "PositionX": x, "PositionY": y,
            "PositionZ": None, "Function": cfg["function"], "ToolDM": cfg["tool_dm"],
            "FlipRotate": fr, "SSMA_TimeStamp": None, "Fusion_TimeStamp": ts, "DrillName": name}


def _air_pair(program, ts, cfg):
    air = cfg["air_drill"]
    return [_record(program, ws, air["x"], air["y"], FLIP_ROTATE_ROTATE, ts, air["name"], cfg)
            for ws in WORKSTOP_PAIR]


def fallback_drilling(component_id, order_id, cfg=None, now=None, reason=""):
    """order_processor's structure when the paired export failed: air drilling
    on both workstops, description 'No drilling required'."""
    cfg = settings(cfg)
    ts = _timestamp(now)
    return {
        "component_id": component_id,
        "order_id": order_id,
        "unit": "in",
        "description": FALLBACK_DESCRIPTION,
        "coordinates": _air_pair("1-%s-%s" % (component_id, order_id), ts, cfg),
        "summary": {"result": "fallback", "reason": reason or "no drilling",
                    "hole_count": 0, "workstops": list(WORKSTOP_PAIR),
                    "flip_rotate": [FLIP_ROTATE_ROTATE], "air_drill_added": True,
                    "air_drill_reason": "fallback: air drilling on both workstops"},
    }


class _NoDrilling(Exception):
    """The old export returned (False, msg) -> order_processor fallback."""


# ----------------------------------------------------------------------
# Per component
# ----------------------------------------------------------------------
def component_drilling(ctype, component_id, order_id, series_id, order_values, model_values,
                       cfg=None, now=None):
    """Drilling records for one door/stile (None for other types / disabled).

    ``series_id`` is only used when ``order_values`` has no ``series_id`` (the old
    code always read it from the component's order parameters)."""
    cfg = settings(cfg)
    if ctype not in ("door", "stile") or not cfg.get("enabled", True) \
            or ctype not in (cfg.get("types") or ()):
        return None
    order_values = order_values or {}
    model_values = model_values or {}
    ts = _timestamp(now)
    try:
        return _compute(ctype, component_id, order_id, series_id, order_values, model_values,
                        cfg, ts)
    except _NoDrilling as e:
        return fallback_drilling(component_id, order_id, cfg, ts, str(e))
    except Exception as e:   # the old export caught everything -> fallback
        return fallback_drilling(component_id, order_id, cfg, ts,
                                 "paired drilling coordinate export failed: %s" % e)


def _compute(ctype, component_id, order_id, series_arg, ov, mv, cfg, ts):
    is_stile = ctype == "stile"
    has_order = bool(ov)
    summary = {"type": ctype, "source": "order parameters" if has_order else "model flags"}

    y_raw = mv.get(cfg["y_param"])
    if not _is_number(y_raw):
        raise _NoDrilling("No %s parameter found for %s" % (cfg["y_param"], component_id))
    y_dist = round(float(y_raw), 3)

    # Series: only from the order parameters (old behaviour); the argument is a
    # fallback for callers whose order_values lack series_id.
    if has_order:
        if "series_id" in ov:
            series = str(_value(ov, "series_id") or "")
        else:
            series = str(series_arg or "")
    else:
        series = ""
    norm = normalize_series(series)
    summary.update({"series_id": series, "series_normalized": norm})

    left_params, right_params, sides = [], [], {}
    if is_stile:
        if has_order:
            left_door, cap_l, right_door, cap_r = stile_sides(ov)
        else:
            left_door = right_door = None
            cap_l, cap_r = stile_sides_from_flags(mv)
        sides = {"left": {"door": left_door, "drilled": cap_l},
                 "right": {"door": right_door, "drilled": cap_r}}
        if not cap_l and not cap_r:
            raise _NoDrilling("No drilling required for %s (no door hinges onto stile)"
                              % component_id)
        if cap_l:
            left_params = stile_hole_params("LD")
        if cap_r:
            right_params = stile_hole_params("RD")
        wanted = left_params + right_params
    else:
        wanted = door_hole_params()

    workstop = left_ws = right_ws = 1
    drill_name = left_name = right_name = ""
    if has_order:
        program_name = str(_value(ov, "program_name") or "").strip()
        if is_stile:
            if left_door and cap_l:
                left_ws = workstop_for("stile", norm, _value(ov, "LD_hinging_right"),
                                       _value(ov, "LD_swinging_out"), cfg)
            if right_door and cap_r:
                right_ws = workstop_for("stile", norm, _value(ov, "RD_hinging_right"),
                                        _value(ov, "RD_swinging_out"), cfg)
            if program_name:
                left_name = right_name = program_name
            else:
                segs = []
                if left_door:
                    segs.append(stile_side_segment(bool(_value(ov, "LD_hinging_right")),
                                                   bool(_value(ov, "LD_swinging_out")), cap_l))
                if right_door:
                    segs.append(stile_side_segment(bool(_value(ov, "RD_hinging_right")),
                                                   bool(_value(ov, "RD_swinging_out")), cap_r))
                left_name = right_name = "%s %s" % (series, " | ".join(segs))
        else:
            hinging = bool(_value(ov, "door_hinging_right"))
            swinging = bool(_value(ov, "door_swinging_out"))
            workstop = workstop_for("door", norm, hinging, swinging, cfg)
            drill_name = program_name or door_drill_name(series, hinging, swinging)

    left_fr = right_fr = FLIP_ROTATE_ROTATE
    if is_stile:
        flags = stile_flags(mv)
        left_fr, right_fr = flip_rotate_for(*flags)
        summary["drilling_flags"] = dict(zip(STILE_FLAG_PARAMS, flags))
        sides["left"].update({"workstop": left_ws if cap_l else None,
                              "flip_rotate": left_fr if cap_l else None})
        sides["right"].update({"workstop": right_ws if cap_r else None,
                               "flip_rotate": right_fr if cap_r else None})
        summary["sides"] = sides

    offset = cfg["x_offset_by_series"].get(norm) if (is_stile and norm) else None
    summary["x_offset"] = offset or 0

    program = "1-%s-%s" % (component_id, order_id)
    wanted_set = set(wanted)
    left_set = set(left_params)
    right_set = set(right_params)
    holes = []
    for name, raw in mv.items():
        if name not in wanted_set or not _is_number(raw):
            continue
        x = round(float(raw), 3)
        if offset:
            x = round(x + offset, 3)
        if is_stile and name in left_set:
            ws, dn, fr = left_ws, left_name, left_fr
        elif is_stile and name in right_set:
            ws, dn, fr = right_ws, right_name, right_fr
        else:
            ws, dn, fr = workstop, drill_name, FLIP_ROTATE_ROTATE
        holes.append(_record(program, ws, x, y_dist, fr, ts, dn, cfg))
    summary["hole_count"] = len(holes)

    coords = list(holes)
    air = cfg["air_drill"]
    summary["air_drill_added"] = False
    if not coords:
        coords = _air_pair(program, ts, cfg)
        summary.update({"air_drill_added": True,
                        "air_drill_reason": "no hole parameters found: air drilling on both workstops"})
    frs = set(r["FlipRotate"] for r in coords)
    used = set(r["WorkStop"] for r in coords)
    if len(used) == 1 and not (frs & set(NO_AIR_DRILL_FLIP_ROTATE)):
        used_ws = next(iter(used))
        opposite = WORKSTOP_PAIR[1] if used_ws == WORKSTOP_PAIR[0] else WORKSTOP_PAIR[0]
        ref = coords[0]
        coords.append({"ProgramName": ref["ProgramName"], "WorkStop": opposite,
                       "PositionX": air["x"], "PositionY": air["y"], "PositionZ": None,
                       "Function": ref["Function"], "ToolDM": ref["ToolDM"],
                       "FlipRotate": ref["FlipRotate"], "SSMA_TimeStamp": ref["SSMA_TimeStamp"],
                       "Fusion_TimeStamp": ref["Fusion_TimeStamp"], "DrillName": air["name"]})
        summary.update({"air_drill_added": True,
                        "air_drill_reason": "only workstop %s drilled: air drill on workstop %s"
                                            % (used_ws, opposite)})
    elif len(used) == 1:
        summary["air_drill_reason"] = ("only workstop %s drilled but FlipRotate %s: no air drill"
                                       % (next(iter(used)), sorted(frs & set(NO_AIR_DRILL_FLIP_ROTATE))))

    summary.update({
        "result": "drilled" if holes else "no holes found",
        "position_y": y_dist,
        "workstops": sorted(set(r["WorkStop"] for r in coords)),
        "flip_rotate": sorted(set(r["FlipRotate"] for r in coords)),
        "drill_names": sorted(set(r["DrillName"] for r in coords)),
        "record_count": len(coords),
    })
    return {
        "component_id": component_id,
        "order_id": order_id,
        "unit": "in",
        "description": PAIRED_DESCRIPTION,
        "coordinates": coords,
        "summary": summary,
    }


# ----------------------------------------------------------------------
# Order file
# ----------------------------------------------------------------------
def compiled_filename(order_id):
    return "%s.json" % order_id


def compile_order(order_id, components):
    """components: [(ctype, paired_dict), ...] in processing order.

    Returns ``{order_id: {"unit", "description", "doors", "panels", "stiles"}}``
    with each entry stripped to component_id + coordinates, or None when no
    component produced drilling data (the old pipeline wrote no file then)."""
    by_type = dict((key, []) for _, key in COMPILED_KEYS)
    type_key = dict(COMPILED_KEYS)
    for ctype, data in components or ():
        if not data or ctype not in type_key:
            continue
        by_type[type_key[ctype]].append({"component_id": data.get("component_id"),
                                         "coordinates": data.get("coordinates", [])})
    if not any(by_type.values()):
        return None
    body = {"unit": "in", "description": COMPILED_DESCRIPTION}
    for _, key in COMPILED_KEYS:
        body[key] = by_type[key]
    return {order_id: body}


def write_compiled(path, compiled):
    """Atomic ``json.dump(compiled, indent=2)`` (old pipeline format)."""
    write_json_atomic(path, compiled, indent=2)
    return path


# ----------------------------------------------------------------------
# Documentation tables (docs/tables/DRILLING.md, generated section)
# ----------------------------------------------------------------------
GENERATED_BEGIN = "<!-- BEGIN GENERATED: python -m nfe.drilling docs/tables/DRILLING.md -->"
GENERATED_END = "<!-- END GENERATED -->"


def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(out)


def _yn(b):
    return "yes" if b else "no"


def render_tables(cfg=None):
    """Markdown for tables 1-7, computed by calling the functions above."""
    cfg = settings(cfg)
    ws_cfg = cfg["workstops"]
    air = cfg["air_drill"]
    parts = []

    # 1. Workstops
    overrides = [k.split("_", 1) for k in sorted(ws_cfg) if "_" in k]
    series_rows = []
    for ctype in ("door", "stile"):
        others = [code for t, code in overrides if t == ctype]
        series_rows.append((ctype, "", "any series" + (" except %s" % ", ".join(others) if others else "")))
        for code in others:
            series_rows.append((ctype, code, "%s (e.g. %s1%s)" % (code, code[0], code[2:])))
    rows = []
    for ctype, code, label in series_rows:
        for h in (False, True):
            for s in (False, True):
                ws = workstop_for(ctype, code, h, s, cfg)
                rows.append((ctype, label, "RH" if h else "LH", "O/S" if s else "I/S",
                             "same" if h == s else "different", ws))
    parts.append("## 1. WorkStop selection\n\n"
                 "hinging_right / swinging_out come from the order: `door_hinging_right` + "
                 "`door_swinging_out` for doors; `LD_hinging_right` + `LD_swinging_out` (left side) "
                 "and `RD_hinging_right` + `RD_swinging_out` (right side) for stiles. Series is "
                 "normalised first (2nd digit is a generation digit: 3186 -> 3086; only 3xxx codes "
                 "are recognised). Config: `drilling.workstops` (`<type>_<series>` overrides "
                 "`<type>`).\n\n"
                 + _table(("Component", "Series", "Hinging", "Swing", "hinging_right vs swinging_out",
                           "WorkStop"), rows))

    # 2. Stile sides
    rows = []
    for side, door_key, hinge_key in (("left", "left_side_door", "LD_hinging_right"),
                                      ("right", "right_side_door", "RD_hinging_right")):
        for door, hr in ((False, None), (True, False), (True, True)):
            sides = stile_sides({door_key: door, hinge_key: hr})
            drilled = sides[1] if side == "left" else sides[3]
            rows.append((side, "`%s`=%d" % (door_key, door),
                         ("`%s`=%d (%s)" % (hinge_key, hr, "RH" if hr else "LH")) if door else "*",
                         _yn(drilled), "LD_stile_*" if side == "left" else "RD_stile_*"))
    parts.append("## 2. Which stile sides are drilled\n\n"
                 "A side is drilled only when a door is on that side AND the door hinges onto the "
                 "stile. If neither side is drilled the component gets the fallback record pair "
                 "(table 4). When the component has no order parameters at all, the old fallback "
                 "applies: left drilled if `left_interior_drilling` or `left_exterior_drilling` > 0, "
                 "right likewise.\n\n"
                 + _table(("Side", "Door present", "Door hinging", "Drilled", "Hole params"), rows))

    # 3. FlipRotate
    rows = []
    for li in (0, 1):
        for le in (0, 1):
            for ri in (0, 1):
                for re_ in (0, 1):
                    lf, rf = flip_rotate_for(li, le, ri, re_)
                    rows.append((li, le, ri, re_, lf, rf))
    parts.append("## 3. FlipRotate\n\n"
                 "Doors: always %d. Stiles: from the model's computed flags `left_interior_drilling`, "
                 "`left_exterior_drilling`, `right_interior_drilling`, `right_exterior_drilling` "
                 "(non-zero = on). The value only matters for sides that are actually drilled "
                 "(table 2).\n\n" % FLIP_ROTATE_ROTATE
                 + _table(("LI", "LE", "RI", "RE", "Left holes FlipRotate", "Right holes FlipRotate"),
                          rows))

    # 4. Air drilling
    rows = [
        ("No hinge-hole parameters found (door, or drilled stile side)",
         "2 records: WorkStop %d then %d" % WORKSTOP_PAIR, air["x"], air["y"], air["name"],
         FLIP_ROTATE_ROTATE, "`%s`" % PAIRED_DESCRIPTION),
        ("Real holes all on ONE workstop and no record has FlipRotate in %s"
         % (list(NO_AIR_DRILL_FLIP_ROTATE),),
         "1 record appended on the other workstop (%d<->%d)" % WORKSTOP_PAIR, air["x"], air["y"],
         air["name"], "copied from the first hole", "`%s`" % PAIRED_DESCRIPTION),
        ("Real holes all on one workstop but FlipRotate 3 present (mixed int/ext stile)",
         "none", "", "", "", "", ""),
        ("Holes on both workstops", "none", "", "", "", "", ""),
        ("Stile with no side drilled (table 2), or no `%s` in the model, or any error"
         % cfg["y_param"],
         "2 records: WorkStop %d then %d (fallback)" % WORKSTOP_PAIR, air["x"], air["y"],
         air["name"], FLIP_ROTATE_ROTATE, "`%s`" % FALLBACK_DESCRIPTION),
    ]
    parts.append("## 4. Air drilling\n\n"
                 "Air drilling = a dummy record at a safe position so the Gannomat cycles both "
                 "workstops. Config: `drilling.air_drill`. Air records always have PositionZ null, "
                 "Function %s, ToolDM %s.\n\n" % (cfg["function"], cfg["tool_dm"])
                 + _table(("When", "Records added", "PositionX", "PositionY", "DrillName", "FlipRotate",
                           "Component description"), rows))

    # 5. Holes
    rows = []
    for name in door_hole_params():
        rows.append(("door", "always", "`%s`" % name, "`%s`" % cfg["y_param"], "none"))
    offs = ", ".join("%s: %+g in" % (k, v) for k, v in sorted(cfg["x_offset_by_series"].items()))
    for side, when in (("LD", "left side drilled"), ("RD", "right side drilled")):
        for name in stile_hole_params(side):
            rows.append(("stile", when, "`%s`" % name, "`%s`" % cfg["y_param"], offs or "none"))
    parts.append("## 5. Holes per component\n\n"
                 "One record per hole parameter found in the model, in MODEL PARAMETER ORDER (user "
                 "parameters first, then the other model parameters) - not in the order listed "
                 "here. PositionX = the `_y_value` parameter in inches rounded to 3 dp; for stiles "
                 "of a series in `drilling.x_offset_by_series` the offset is then added and the "
                 "result rounded to 3 dp again. PositionY = `%s` in inches rounded to 3 dp (same "
                 "for every hole of the component). Missing / non-numeric hole parameters are "
                 "skipped.\n\n" % cfg["y_param"]
                 + _table(("Component", "When", "PositionX from", "PositionY from", "X offset"), rows))

    # 6. Record fields
    rows = [
        ("ProgramName", "string", "`1-{component_id}-{order_id}`"),
        ("WorkStop", "int", "table 1 (air records: table 4)"),
        ("PositionX", "number (in)", "table 5 (air: %s)" % air["x"]),
        ("PositionY", "number (in)", "`%s` (air: %s)" % (cfg["y_param"], air["y"])),
        ("PositionZ", "null", "always null"),
        ("Function", "int", "`drilling.function` = %s" % cfg["function"]),
        ("ToolDM", "int", "`drilling.tool_dm` = %s" % cfg["tool_dm"]),
        ("FlipRotate", "int", "table 3"),
        ("SSMA_TimeStamp", "null", "always null (filled in by the machine side)"),
        ("Fusion_TimeStamp", "string", "processing time `%s` (local time)" % TIMESTAMP_FORMAT),
        ("DrillName", "string",
         "order `program_name` (trimmed) if not blank; else door: `{series_id} {RH|LH} {O/S|I/S} "
         "DOOR`; stile: `{series_id} {segment}[ | {segment}]` with one segment per side that HAS "
         "a door: `{RH|LH} {O/S|I/S} {HINGE|KEEPING}` (HINGE = drilled side). series_id is the "
         "raw order value (not normalised). Air records: `%s`" % air["name"]),
    ]
    parts.append("## 6. Record fields (in this key order)\n\n"
                 + _table(("Field", "Type", "Value"), rows))

    # 7. Compiled file
    sample = compile_order("IBUS123456", [
        ("door", {"component_id": "D1", "coordinates": ["..."]}),
        ("stile", {"component_id": "S1", "coordinates": ["..."]})])
    parts.append("## 7. Compiled order file\n\n"
                 "One file per order named `%s` (config `naming.drilling`). Components are listed "
                 "under their type in processing order; types always appear in the order doors, "
                 "panels, stiles (panels stays empty - panels are never drilled). Each entry keeps "
                 "only `component_id` + `coordinates`. No file is written when no component "
                 "produced drilling data. Written with `json.dump(..., indent=2)`.\n\n```json\n%s\n```"
                 % (compiled_filename("{order_id}"), json.dumps(sample, indent=2)))
    return "\n\n".join(parts) + "\n"


def update_doc(path, cfg=None):
    """Replace the generated section of docs/tables/DRILLING.md."""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    start = text.index(GENERATED_BEGIN) + len(GENERATED_BEGIN)
    end = text.index(GENERATED_END)
    new = text[:start] + "\n\n" + render_tables(cfg) + "\n" + text[end:]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    return new != text


if __name__ == "__main__":   # python -m nfe.drilling docs/tables/DRILLING.md
    import sys
    print("updated" if update_doc(sys.argv[1]) else "unchanged")
