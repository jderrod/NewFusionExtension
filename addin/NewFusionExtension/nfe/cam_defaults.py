"""Default toolpath-selection profiles (see nfe.cam_rules for the format).

These are a faithful transcription of the old FusionExtensionVM code
(``order_processor._build_door/stile/panel_setup_sequence`` and
``cam_manager.suppress_door/panel_toolpaths``); tests/test_cam_rules.py
checks them exhaustively against ports of that code.  Where the old
spreadsheets disagree with the code, the code wins and the row carries a
``review`` note (rendered in docs/tables/TOOLPATH_SELECTION.md).

Every row is written out in full on purpose: the tables are meant to be read
and edited by people, not generated.
"""

# Old spreadsheet names, used in the review notes.
ISL = "InstructionSelectionLogic.xlsx"
TCM = "Toolpath_Configuration_Matrix.xlsx"
MWF = "Manufacturing Workflow v3 2026_04_21.xlsx"


def _row(rid, when, setups, note, review=""):
    return {"id": rid, "when": when, "setups": setups, "note": note, "review": review}


def _model(param, vtype="bool"):
    return {"param": param, "source": "model", "type": vtype}


def _auto(param, vtype="bool"):
    return {"param": param, "source": "auto", "type": vtype}


# ----------------------------------------------------------------------
# 3X8X doors
# ----------------------------------------------------------------------
_DOOR_OUT_LEFT = ("%s 'Door Toolpath Selection' scenario 4 says out-swing left side -> "
                  "Setup 1 - G57 (interior) / Setup 3 - G57 (exterior), and 'Door Instructions' "
                  "gives Flip-G57.pdf; the old code and %s use G59 (Setup 2 / Setup 4)." % (ISL, TCM))
_DOOR_OUT_RIGHT = ("%s 'Door Toolpath Selection' scenario 3 says out-swing right side -> "
                   "Setup 6 - G59 (interior) / Setup 8 - G59 (exterior), and 'Door Instructions' "
                   "gives Flip-Rotate-G59.pdf; the old code and %s use G57 (Setup 5 / Setup 7)."
                   % (ISL, TCM))
_DOOR_OUT_BOTH = ("%s 'Door Toolpath Selection' scenario 7 says out-swing both sides -> left "
                  "Setup 1/3 - G57 + right Setup 6/8 - G59, G57 first; the old code and %s use "
                  "left G59 (Setup 2/4) + right G57 (Setup 5/7). (Final G-code order is G57 first "
                  "either way because of the splicer's G57-before-G59 sort.)" % (ISL, TCM))
_DOOR_IN_LEFT = ("%s 'Door Toolpath Selection' scenario 6 says in-swing left side -> "
                 "Setup 2 - G59 (interior) / Setup 4 - G59 (exterior), and 'Door Instructions' "
                 "gives Flip-Rotate-G59.pdf; the old code and %s use G57 (Setup 1 / Setup 3)."
                 % (ISL, TCM))
_DOOR_IN_RIGHT = ("%s 'Door Instructions' gives Flip-G57.pdf (a G57 setup) for in-swing right "
                  "side only; the old code, %s and the 'Door Toolpath Selection' sheet use G59 "
                  "(Setup 6 / Setup 8)." % (ISL, TCM))

DOOR_3X8X = {
    "id": "door-3x8x", "title": "3X8X doors", "enabled": True,
    "type": "door", "family": "3X8X", "series": "",
    "description": (
        "Each side (left / right) with any rabbet or notch gets one setup. The side's setup is "
        "the EXTERIOR-rabbet one when that side's exterior rabbeting is on, otherwise the "
        "INTERIOR-rabbet one (notch-only sides use the interior setup with the rabbet "
        "suppressed). Out-swing doors use G59 for the left side and G57 for the right; "
        "in-swing doors the opposite."),
    "variables": {
        "swing_out": _auto("door_swinging_out"),
        "L_int": _model("left_interior_rabbeting"),
        "L_ext": _model("left_exterior_rabbeting"),
        "L_bn": _model("bottom_left_notching"),
        "L_tn": _model("top_left_notching"),
        "R_int": _model("right_interior_rabbeting"),
        "R_ext": _model("right_exterior_rabbeting"),
        "R_bn": _model("bottom_right_notching"),
        "R_tn": _model("top_right_notching"),
    },
    "derived": {
        "L_any": "L_int or L_ext or L_bn or L_tn",
        "R_any": "R_int or R_ext or R_bn or R_tn",
    },
    "sequence_rules": [
        _row("D1", "not L_any and not R_any", [],
             "No rabbet or notch on either side: no G-code"),
        # Out-swing: left side at G59, right side at G57.
        _row("D2", "swing_out and L_any and not R_any and L_ext", ["Setup 4 - G59"],
             "Out-swing, left side only, exterior rabbet", _DOOR_OUT_LEFT),
        _row("D3", "swing_out and L_any and not R_any and not L_ext", ["Setup 2 - G59"],
             "Out-swing, left side only, interior rabbet or notches only", _DOOR_OUT_LEFT),
        _row("D4", "swing_out and R_any and not L_any and R_ext", ["Setup 7 - G57"],
             "Out-swing, right side only, exterior rabbet", _DOOR_OUT_RIGHT),
        _row("D5", "swing_out and R_any and not L_any and not R_ext", ["Setup 5 - G57"],
             "Out-swing, right side only, interior rabbet or notches only", _DOOR_OUT_RIGHT),
        _row("D6", "swing_out and L_any and R_any and L_ext and R_ext",
             ["Setup 4 - G59", "Setup 7 - G57"],
             "Out-swing, both sides; left exterior, right exterior", _DOOR_OUT_BOTH),
        _row("D7", "swing_out and L_any and R_any and L_ext and not R_ext",
             ["Setup 4 - G59", "Setup 5 - G57"],
             "Out-swing, both sides; left exterior, right interior/notches", _DOOR_OUT_BOTH),
        _row("D8", "swing_out and L_any and R_any and not L_ext and R_ext",
             ["Setup 2 - G59", "Setup 7 - G57"],
             "Out-swing, both sides; left interior/notches, right exterior", _DOOR_OUT_BOTH),
        _row("D9", "swing_out and L_any and R_any and not L_ext and not R_ext",
             ["Setup 2 - G59", "Setup 5 - G57"],
             "Out-swing, both sides; left interior/notches, right interior/notches",
             _DOOR_OUT_BOTH),
        # In-swing: left side at G57, right side at G59.
        _row("D10", "not swing_out and L_any and not R_any and L_ext", ["Setup 3 - G57"],
             "In-swing, left side only, exterior rabbet", _DOOR_IN_LEFT),
        _row("D11", "not swing_out and L_any and not R_any and not L_ext", ["Setup 1 - G57"],
             "In-swing, left side only, interior rabbet or notches only", _DOOR_IN_LEFT),
        _row("D12", "not swing_out and R_any and not L_any and R_ext", ["Setup 8 - G59"],
             "In-swing, right side only, exterior rabbet", _DOOR_IN_RIGHT),
        _row("D13", "not swing_out and R_any and not L_any and not R_ext", ["Setup 6 - G59"],
             "In-swing, right side only, interior rabbet or notches only", _DOOR_IN_RIGHT),
        _row("D14", "not swing_out and L_any and R_any and L_ext and R_ext",
             ["Setup 3 - G57", "Setup 8 - G59"],
             "In-swing, both sides; left exterior, right exterior"),
        _row("D15", "not swing_out and L_any and R_any and L_ext and not R_ext",
             ["Setup 3 - G57", "Setup 6 - G59"],
             "In-swing, both sides; left exterior, right interior/notches"),
        _row("D16", "not swing_out and L_any and R_any and not L_ext and R_ext",
             ["Setup 1 - G57", "Setup 8 - G59"],
             "In-swing, both sides; left interior/notches, right exterior"),
        _row("D17", "not swing_out and L_any and R_any and not L_ext and not R_ext",
             ["Setup 1 - G57", "Setup 6 - G59"],
             "In-swing, both sides; left interior/notches, right interior/notches"),
    ],
    "operation_rules": [
        {"op": "Left Interior Rabbet", "active": "L_int", "note": "left_interior_rabbeting on"},
        {"op": "Left Exterior Rabbet", "active": "L_ext", "note": "left_exterior_rabbeting on"},
        {"op": "Left Bottom Notch", "active": "L_bn", "note": "bottom_left_notching on"},
        {"op": "Left Top Notch", "active": "L_tn", "note": "top_left_notching on"},
        {"op": "Right Interior Rabbet", "active": "R_int", "note": "right_interior_rabbeting on"},
        {"op": "Right Exterior Rabbet", "active": "R_ext", "note": "right_exterior_rabbeting on"},
        {"op": "Right Bottom Notch", "active": "R_bn", "note": "bottom_right_notching on"},
        {"op": "Right Top Notch", "active": "R_tn", "note": "top_right_notching on"},
    ],
    "unknown_operations": "leave",
    "splice": {"g57_before_g59": True},
    "review": [
        "When a side has BOTH interior and exterior rabbeting on, the old code picks the "
        "exterior setup, so the interior rabbet (which only exists in the interior setups) is "
        "never machined. No document covers this case.",
        "Out-swing, both sides (D6-D9): the rows list left G59 first, exactly like the old "
        "code, but the old post processor (and the splicer, g57_before_g59) always moves G57 "
        "sections before G59, so the right-side G57 setup is machined first.",
    ],
}


# ----------------------------------------------------------------------
# 3X82 / 3X86 stiles
# ----------------------------------------------------------------------
_STILE_BOTH_IN = ("%s 'Stile Toolpath Selection' says both in-swing -> Left Rabbet - In G57 "
                  "then Right Rabbet - In G59; the old code and %s use Right Rabbet - In G57 "
                  "then Left Rabbet - In G59." % (ISL, TCM))
_STILE_BOTH_OUT = ("%s 'Stile Toolpath Selection' says both out-swing -> Right Rabbet - Out G57 "
                   "then Left Rabbet - Out G57 (both G57, probably a typo); the old code and %s "
                   "use Left Rabbet - Out G57 then Right Rabbet - Out G59." % (ISL, TCM))
_STILE_ISL_OPP = ("%s 'Stile Toolpath Selection' always lists the left side first for opposite "
                  "swings (no drilling or series rule)." % ISL)
_STILE_82_LIN = ("%s 'Anderson stiles' (3X82) says left I/S + right O/S -> A:G57 & Flip & "
                 "A:G57; the old code, %s and %s use G59 for both sides (B:G59 & Flip & B:G59). "
                 % (MWF, ISL, TCM))
_STILE_82_LOUT = ("%s 'Anderson stiles' (3X82) says left O/S + right I/S -> B:G59 & Flip & "
                  "B:G59; the old code, %s and %s use G57 for both sides (A:G57 & Flip & A:G57). "
                  % (MWF, ISL, TCM))
_STILE_86_SINGLE = ("%s 'Anderson stiles' (3X86) gives a mirrored single-door mapping: A:G57 for "
                    "right O/S or left I/S, B:G59 for the other cases (one cell reads "
                    "'None | O/S or None | I/S', probably a typo); the old code, %s and %s use "
                    "the 3X82 mapping (Left Out G57, Left In G59, Right Out G59, Right In G57) "
                    "for 3X86 too." % (MWF, ISL, TCM))
_STILE_FR_BOTH4 = ("Not in any document: when BOTH sides have interior drilling (FlipRotate=4 on "
                   "both) the old code defaults to left first. A side with interior AND exterior "
                   "drilling counts as FlipRotate=4 (interior wins). %s only describes the "
                   "'left FR=3' / 'right FR=3' cases." % TCM)

_STILE_VARIABLES = {
    "L_door": _auto("left_side_door"),
    "R_door": _auto("right_side_door"),
    "L_out": _auto("LD_swinging_out"),
    "R_out": _auto("RD_swinging_out"),
    "LI_drill": _model("left_interior_drilling"),
    "LE_drill": _model("left_exterior_drilling"),
    "RI_drill": _model("right_interior_drilling"),
    "RE_drill": _model("right_exterior_drilling"),
}
_STILE_DERIVED = {
    "two_doors": "L_door and R_door",
    "L_drill": "LI_drill or LE_drill",
    "R_drill": "RI_drill or RE_drill",
}

_LI59 = "Left Rabbet - In G59"
_RO59 = "Right Rabbet - Out G59"
_LO57 = "Left Rabbet - Out G57"
_RI57 = "Right Rabbet - In G57"


def _stile_rows(series):
    """Rows for one stile series; only the no-drilling default differs."""
    is_86 = series == "3X86"
    opp1 = "two_doors and not L_out and R_out"      # left in, right out: both G59
    opp2 = "two_doors and L_out and not R_out"      # left out, right in: both G57
    lin = _STILE_ISL_OPP + ("" if is_86 else " " + _STILE_82_LIN)
    lout = _STILE_ISL_OPP + ("" if is_86 else " " + _STILE_82_LOUT)
    single = _STILE_86_SINGLE if is_86 else ""
    if is_86:
        nd1 = ([_RO59, _LI59], "right side first (3X86 default)")
        nd2 = ([_RI57, _LO57], "right side first (3X86 default)")
    else:
        nd1 = ([_LI59, _RO59], "left side first (3X82 default)")
        nd2 = ([_LO57, _RI57], "left side first (3X82 default)")
    return [
        _row("S1", "not L_door and not R_door", [], "No doors on the stile: no G-code"),
        _row("S2", "L_door and not R_door and L_out", [_LO57],
             "Left door only, out-swing (A: G57)", single),
        _row("S3", "L_door and not R_door and not L_out", ["Left Rabbet - In G59"],
             "Left door only, in-swing (B: G59)", single),
        _row("S4", "R_door and not L_door and R_out", ["Right Rabbet - Out G59"],
             "Right door only, out-swing (B: G59)", single),
        _row("S5", "R_door and not L_door and not R_out", [_RI57],
             "Right door only, in-swing (A: G57)", single),
        _row("S6", "two_doors and not L_out and not R_out", [_RI57, _LI59],
             "Both doors in-swing: right G57, rotate 180, left G59", _STILE_BOTH_IN),
        _row("S7", "two_doors and L_out and R_out", [_LO57, _RO59],
             "Both doors out-swing: left G57, rotate 180, right G59", _STILE_BOTH_OUT),
        # Left in + right out: both at G59, order from the drilling flags.
        _row("S8", opp1 + " and not L_drill and not R_drill", nd1[0],
             "Left in / right out (G59 flip G59), no drilling: " + nd1[1], lin),
        _row("S9", opp1 + " and L_drill and not R_drill", [_LI59, _RO59],
             "Left in / right out, drilling on left only: left side first", lin),
        _row("S10", opp1 + " and R_drill and not L_drill", [_RO59, _LI59],
             "Left in / right out, drilling on right only: right side first", lin),
        _row("S11", opp1 + " and L_drill and R_drill and not LI_drill", [_LI59, _RO59],
             "Left in / right out, drilling both sides, left is FlipRotate=3 (exterior only): "
             "left side first", lin),
        _row("S12", opp1 + " and L_drill and R_drill and LI_drill and not RI_drill",
             [_RO59, _LI59],
             "Left in / right out, drilling both sides, right is FlipRotate=3: right side first",
             lin),
        _row("S13", opp1 + " and L_drill and R_drill and LI_drill and RI_drill", [_LI59, _RO59],
             "Left in / right out, drilling both sides, both FlipRotate=4: left side first",
             (lin + " " + _STILE_FR_BOTH4).strip()),
        # Left out + right in: both at G57.
        _row("S14", opp2 + " and not L_drill and not R_drill", nd2[0],
             "Left out / right in (G57 flip G57), no drilling: " + nd2[1], lout),
        _row("S15", opp2 + " and L_drill and not R_drill", [_LO57, _RI57],
             "Left out / right in, drilling on left only: left side first", lout),
        _row("S16", opp2 + " and R_drill and not L_drill", [_RI57, _LO57],
             "Left out / right in, drilling on right only: right side first", lout),
        _row("S17", opp2 + " and L_drill and R_drill and not LI_drill", [_LO57, _RI57],
             "Left out / right in, drilling both sides, left is FlipRotate=3 (exterior only): "
             "left side first", lout),
        _row("S18", opp2 + " and L_drill and R_drill and LI_drill and not RI_drill",
             [_RI57, _LO57],
             "Left out / right in, drilling both sides, right is FlipRotate=3: right side first",
             lout),
        _row("S19", opp2 + " and L_drill and R_drill and LI_drill and RI_drill", [_LO57, _RI57],
             "Left out / right in, drilling both sides, both FlipRotate=4: left side first",
             (lout + " " + _STILE_FR_BOTH4).strip()),
    ]


_STILE_DESCRIPTION = (
    "Setups are chosen from the doors hanging on the stile (left_side_door / right_side_door) "
    "and their swing (LD/RD_swinging_out). For opposite swings the two setups use the same "
    "workstop (part is flipped) and the drilling flags decide which side is machined first: "
    "the side with drilling first; with drilling on both sides the side whose drilling is "
    "exterior only (FlipRotate=3, processed first at the Gannomat) goes first. Each setup "
    "holds one rabbet operation, which is always made active.")

STILE_3X82 = {
    "id": "stile-3x82", "title": "3X82 stiles", "enabled": True,
    "type": "stile", "family": "3X8X", "series": "3?82*",
    "description": _STILE_DESCRIPTION + (
        " 3X81 stiles are deliberately not covered: the 3X81 model has no manufacturing "
        "setups, so they skip the manufacture step."),
    "variables": dict(_STILE_VARIABLES),
    "derived": dict(_STILE_DERIVED),
    "sequence_rules": _stile_rows("3X82"),
    "operation_rules": [],
    "unknown_operations": "activate",
    "splice": {"g57_before_g59": True},
    "review": [
        "The old code ran 3X81 stiles through the 3X82 sequence logic; the 3X81 model has no "
        "manufacturing setups and %s flow chart 'Stile 3X81' has no Anderson station, so "
        "3X81 is excluded here (series pattern 3?82*)." % MWF,
    ],
}

STILE_3X86 = {
    "id": "stile-3x86", "title": "3X86 stiles", "enabled": True,
    "type": "stile", "family": "3X8X", "series": "3?86*",
    "description": _STILE_DESCRIPTION + (
        " Setup names are written as in the 3X82 model ('Left Rabbet - Out G57'); they also "
        "match the 3X86 model's 'Left Rabbet - Out - G57'."),
    "variables": dict(_STILE_VARIABLES),
    "derived": dict(_STILE_DERIVED),
    "sequence_rules": _stile_rows("3X86"),
    "operation_rules": [],
    "unknown_operations": "activate",
    "splice": {"g57_before_g59": True},
}


# ----------------------------------------------------------------------
# 3X8X panels
# ----------------------------------------------------------------------
_PANEL_TEXT = ("%s 'Panel Configurations' activates a cutout only when its width AND height "
               "are > 0 (same for Cutout B and for the 'no features' row P1); the old code "
               "also treats a non-empty cutout_A / cutout_B text "
               "parameter as active (safety net when the model's derived sizes did not "
               "update)." % TCM)

PANEL_3X8X = {
    "id": "panel-3x8x", "title": "3X8X panels", "enabled": True,
    "type": "panel", "family": "3X8X", "series": "",
    "description": (
        "One setup, chosen by orientation: portrait (height >= width) posts the back-bottom "
        "corner setup, landscape the back-top corner setup. Both setups contain the same "
        "operations (the second setup's names carry a ' (2)' suffix)."),
    "variables": {
        "height": _model("component_height", "number"),
        "width": _model("component_width", "number"),
        "notch_FB": _model("notching_front_edge_bottom"),
        "notch_FT": _model("notching_front_edge_top"),
        "notch_BB": _model("notching_back_edge_bottom"),
        "notch_BT": _model("notching_back_edge_top"),
        "cutA_w": _model("cutout_A_width", "number"),
        "cutA_h": _model("cutout_A_height", "number"),
        "cutB_w": _model("cutout_B_width", "number"),
        "cutB_h": _model("cutout_B_height", "number"),
        "cutA_text": _model("cutout_A", "text"),
        "cutB_text": _model("cutout_B", "text"),
    },
    "derived": {
        "cutA": "(cutA_w > 0 and cutA_h > 0) or cutA_text != ''",
        "cutB": "(cutB_w > 0 and cutB_h > 0) or cutB_text != ''",
        "notch_any": "notch_FB or notch_FT or notch_BB or notch_BT",
        "any_feature": "notch_any or cutA or cutB",
        "size_known": "not (height == 0 and width == 0)",
    },
    "sequence_rules": [
        _row("P1", "size_known and not any_feature", [],
             "No notches and no cutouts: no G-code"),
        _row("P2", "size_known and height >= width", ["Setup Back Bottom Corner G58"],
             "Portrait (height >= width)"),
        _row("P3", "size_known and height < width", ["Setup Back Top Corner G58"],
             "Landscape (width > height)"),
    ],
    "operation_rules": [
        {"op": "Front Bottom Notch", "active": "notch_FB", "note": "notching_front_edge_bottom on"},
        {"op": "Front Top Notch", "active": "notch_FT", "note": "notching_front_edge_top on"},
        {"op": "Back Bottom Notch", "active": "notch_BB", "note": "notching_back_edge_bottom on"},
        {"op": "Back Top Notch", "active": "notch_BT", "note": "notching_back_edge_top on"},
        {"op": "Cutout A", "active": "cutA",
         "note": "cutout_A width and height > 0, or cutout_A text set", "review": _PANEL_TEXT},
        {"op": "Cutout B", "active": "cutB",
         "note": "cutout_B width and height > 0, or cutout_B text set"},
    ],
    "unknown_operations": "suppress",
    "splice": {"g57_before_g59": True},
    "review": [
        "%s 'Legend & Summary' says panels have 2 setups x 6 toolpaths; the live model also has "
        "'Adaptive1' / 'Adaptive1 (2)', which the old code suppresses as an unknown operation "
        "(kept: unknown operations are suppressed)." % TCM,
        "Height and width both 0: the old code gave up and posted every setup; here no row "
        "matches, so the component fails with a clear error instead.",
    ],
}


DEFAULT_PROFILES = [DOOR_3X8X, STILE_3X82, STILE_3X86, PANEL_3X8X]
