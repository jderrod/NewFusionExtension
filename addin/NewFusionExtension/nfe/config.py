"""Shared configuration (config/config.json).

The add-in re-reads the file at the start of every order, so edits made in the
dashboard take effect without restarting Fusion.
"""

import copy
import os

from .jsonio import read_json, write_json_atomic

COMPONENT_TYPES = ("door", "stile", "panel")

DEFAULTS = {
    "hub": {
        "name": "Nikorohmz",
        # Project + folders that hold the family models.  Filled in from the
        # dashboard's Series Configuration section.
        "project": "",
        "family_folders": {"2X8X": "", "3X8X": ""},
    },
    # Series rules: which Fusion model + drawing to use for a component.
    # Matched on component type + a glob against the component's series_id;
    # the most specific matching rule wins.  See nfe.rules.
    "rules": [],
    "orders": {
        # Top-level JSON key -> component type.  Any other list of objects with
        # "id" + "parameters" is accepted and typed by the id's letter prefix.
        "type_by_key": {"doors": "door", "stiles": "stile", "panels": "panel"},
        "type_by_prefix": {"D": "door", "S": "stile", "P": "panel"},
        # Process components that share a model back-to-back (fewer opens).
        "group_by_model": True,
        # Parameters filled from the order itself (applied only when the model
        # has them).  Placeholders: {order_id} {component_id} {type} {series_id}
        # A value that comes out empty is not written.  The 2X stile models
        # say "order_number" / "series" where the others have an "a_" prefix.
        "derived_parameters": {"a_order_number": "{order_id}", "order_number": "{order_id}",
                               "component_id": "1-{component_id}-{order_id}",
                               "a_series": "{series_id}", "series": "{series_id}"},
        # Tags every model is expected to carry: a warning is reported when a
        # model has none of the names in a group.
        "tag_parameters": [["component_id"], ["a_order_number", "order_number"], ["a_series", "series"]],
        # Order values reshaped before they are applied.  split_text: the panel
        # "cutout" value "B-354 & B-386" -> cutout_A='B-354', cutout_B='B-386'
        # (old pipeline behaviour; '+' accepted as separator too).
        "input_transforms": [
            {"type": "split_text", "component_types": ["panel"], "from": "cutout",
             "to": ["cutout_A", "cutout_B"], "separators": ["&", "+"]},
        ],
    },
    # Output file/folder names - see nfe/naming.py for placeholders.
    "naming": {
        "order_folder": "{order_id} {run_stamp}",
        "gcode": "1-{component_id}-{order_id}.txt",
        "drawing_pdf": "1-{component_id}-{order_id}-drawing.pdf",
        "drilling": "{order_id}.json",
        "report": "{order_id}-report.json",
    },
    # Manufacture (CAM) step: toolpath selection tables live in
    # config/toolpath_profiles.json (defaults: nfe/cam_defaults.py).
    "cam": {
        "enabled": True,
        "post_processor": "post/Anderson Stratos 2.cps",   # relative to the project root
        "program_counter_file": "state/nc_program_counter.txt",
        "program_counter_start": 1001,
        "seed_counter_from": "C:/Users/jderr/Fusion Extension/FusionExtensionVM/nc_program_counter.txt",
        "profiles_file": "config/toolpath_profiles.json",
        "generate_timeout_s": 300,
        "switch_workspace": True,       # activate MANUFACTURE for CAM, back to DESIGN after
        "keep_nc_sections": False,      # keep the per-setup .nc files next to the .txt
        # The old splicer kept each section's numbered "N### M30" before the next
        # M0 (bit-for-bit compatible default).  True removes them - confirm what
        # the Stratos control does with an M30 mid-program first.
        "strip_numbered_m30": False,
    },
    # Gannomat hinge-hole drilling output (one file per order); values merged
    # over nfe.drilling.DRILLING_DEFAULTS.
    "drilling": {},
    # Flat local staging copies for the machines (relative to the project
    # root; "" = off).  The harness sync service copies them to the shares.
    "drops": {"gcode": "machine_drops/anderson", "drilling": "machine_drops/gannomat", "drawings": ""},
    # Where orders come from and where results go.  "local": everything on this
    # computer (shares mirrored to network_mirror/).  "remote": orders are read
    # from the iBob export share and results are copied to the ddc-mefs shares
    # by the harness sync service - Fusion itself never writes to the network.
    "location": {
        "mode": "local",
        "local": {"inbox": "", "mirror_root": "network_mirror"},
        "remote": {
            "inbox": "\\\\ddc-mefs\\iBob-Export\\S2S_Export",
            "shares": {
                "gcode": "\\\\ddc-mefs\\Anderson",
                "drilling": "\\\\ddc-mefs\\Gannomat\\F360_output",
                "output": "\\\\ddc-mefs\\Fusion\\Fusion Folders\\output",
                "order_completed": "\\\\ddc-mefs\\Fusion\\Fusion Folders\\order_completed",
                "order_failed": "\\\\ddc-mefs\\Fusion\\Fusion Folders\\order_failed",
            },
        },
        "sync": {"enabled": True, "interval_s": 15, "settle_s": 5},
    },
    "processing": {
        "autostart": True,              # start working as soon as the add-in loads
        "save_model": True,
        "save_drawing": False,          # also save the refreshed drawing to the hub
        "open_visible": True,
        "model_cache_size": 1,          # models kept open between components
        "recycle_every_components": 25, # full document flush every N components (0 = never)
        "unknown_parameter_policy": "warn",   # warn | fail
        "null_policy": "zero",                # zero | skip | fail  (JSON null values)
        "missing_drawing_policy": "fail",     # fail | warn
        "open_timeout_s": 600,
        "save_timeout_s": 300,
        "drawing_update_timeout_s": 180,
        "drawing_settle_s": 2.0,
        # Minimum time between a model's cloud save completing and the next
        # change to that same model (0 = no wait).  See ARCHITECTURE.md 12.5.
        "model_settle_s": 20.0,
        "export_timeout_s": 300,
        "step_stuck_timeout_s": 900,    # harness treats a longer blocking step as a hang
    },
    "health": {
        "max_threads": 4000,
        "max_working_set_mb": 12000,
        "max_open_documents": 12,
        # Fusion grows slowly inside (~25 MB / component, not documents): refresh
        # it at a component boundary after this many components (0 = never).
        "restart_every_components": 200,
    },
    "supervisor": {
        "auto_restart_fusion": True,
        "heartbeat_stale_s": 60,
        "restart_cooldown_s": 300,
        "fusion_launcher": "",          # blank = auto-detect FusionLauncher.exe
        # Dialogs that freeze Fusion until somebody clicks.  The harness answers
        # the ones listed here: "close" = the X button, "click" = a button at
        # x, y given as fractions of the dialog's width and height.
        "dismiss_dialogs": True,
        "dialog_actions": [
            # cloud upload refused: "Open" (never "Save As", which creates a copy)
            {"title": "Upload Error", "action": "click", "x": 0.912, "y": 0.8125},
            # "Recovered Documents" after a restart with unsaved changes
            {"title": "Fusion360", "action": "close"},
        ],
    },
    "api": {
        "addin_port": 8766,
        "harness_port": 8765,
        "harness_bind": "127.0.0.1",
        "dev_exec": False,              # POST /api/dev/exec (run Python inside Fusion)
        "launch_harness_from_addin": True,
        "harness_python": "",           # blank = pyw/pythonw from PATH
    },
    "paths": {"dropbox": "", "output": ""},
}


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load(path):
    raw = read_json(path, default=None)
    if raw is None:
        return copy.deepcopy(DEFAULTS)
    return deep_merge(DEFAULTS, raw)


def save(path, cfg):
    errors = validate(cfg)
    if errors:
        raise ValueError("; ".join(errors))
    write_json_atomic(path, cfg)


def ensure_exists(path):
    if not os.path.isfile(path):
        write_json_atomic(path, DEFAULTS)


def validate(cfg):
    errors = []
    rules = cfg.get("rules", [])
    if not isinstance(rules, list):
        return ["rules must be a list"]
    seen = set()
    for i, r in enumerate(rules):
        label = "rule %d" % (i + 1)
        if not isinstance(r, dict):
            errors.append("%s is not an object" % label)
            continue
        rid = r.get("id")
        if not rid:
            errors.append("%s has no id" % label)
        elif rid in seen:
            errors.append("duplicate rule id %r" % rid)
        seen.add(rid)
        if r.get("type") not in COMPONENT_TYPES + ("*",):
            errors.append("%s: type must be one of %s or *" % (label, ", ".join(COMPONENT_TYPES)))
        if not r.get("series") and not r.get("family"):
            errors.append("%s: needs a series pattern or a family" % label)
        model = r.get("model") or {}
        if r.get("enabled", True) and not model.get("id"):
            errors.append("%s: no model selected" % label)
    from .naming import validate as validate_naming
    errors.extend(validate_naming(cfg.get("naming")))
    cam = cfg.get("cam", {})
    if not isinstance(cam.get("generate_timeout_s"), (int, float)) or cam.get("generate_timeout_s") <= 0:
        errors.append("cam.generate_timeout_s must be a positive number")
    loc = cfg.get("location", {})
    if loc.get("mode") not in ("local", "remote"):
        errors.append("location.mode must be local or remote")
    proc = cfg.get("processing", {})
    for key in ("open_timeout_s", "save_timeout_s", "drawing_update_timeout_s", "export_timeout_s"):
        v = proc.get(key)
        if not isinstance(v, (int, float)) or v <= 0:
            errors.append("processing.%s must be a positive number" % key)
    if proc.get("unknown_parameter_policy") not in ("warn", "fail"):
        errors.append("processing.unknown_parameter_policy must be warn or fail")
    if proc.get("null_policy") not in ("zero", "skip", "fail"):
        errors.append("processing.null_policy must be zero, skip or fail")
    if proc.get("missing_drawing_policy") not in ("warn", "fail"):
        errors.append("processing.missing_drawing_policy must be warn or fail")
    size = proc.get("model_cache_size")
    if not isinstance(size, int) or size < 0:
        errors.append("processing.model_cache_size must be an integer >= 0")
    return errors
