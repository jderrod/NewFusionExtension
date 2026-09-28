"""Data-driven toolpath (CAM setup) selection.

Which CAM setups get posted, in which order, and which operations inside
them are active used to be hard-coded (FusionExtensionVM
``_build_*_setup_sequence`` / ``suppress_*_toolpaths``).  Here the same
decisions are editable tables, one *profile* per model family::

    {"id": "door-3x8x", "title": "3X8X doors", "enabled": true,
     "type": "door", "family": "3X8X", "series": "",     # matched like nfe.rules
     "variables": {"swing_out": "door_swinging_out",     # var -> param name, or
                   "L_int": {"param": "left_interior_rabbeting",
                             "source": "model", "type": "bool", "default": 0}},
     "derived": {"L_any": "L_int or L_ext or L_bn or L_tn"},
     "sequence_rules": [{"id": "D1", "when": "not L_any and not R_any",
                         "setups": [], "note": "...", "review": ""}, ...],
     "operation_rules": [{"op": "Left Interior Rabbet", "active": "L_int"}, ...],
     "unknown_operations": "leave",       # | "suppress" | "activate"
     "splice": {"g57_before_g59": true}}

Variables read a model parameter (``source`` "model"), an order parameter
("order") or the model first and the order as fallback ("auto", the default).
Optional ``type`` coerces the value: "bool" -> 1/0 (``"0"``, ``"false"`` and
``""`` are false, like the old ``_to_bool``), "number" -> float, "text" ->
str with wrapping quotes stripped.  Missing values fall back to ``default``
(0, or '' for text) and are reported.  ``derived`` values are computed in
order and may use earlier ones.

The FIRST sequence row whose ``when`` is true decides the setups to post
(``[]`` = nothing to machine, skip CAM).  Operation rules are then applied
to the operations inside the selected setups; operations no rule covers
follow ``unknown_operations``.  Expressions use nfe.expr (safe subset).

Setup and operation names are matched exactly first, then after
normalisation (case, whitespace, dashes, a trailing " (2)" copy suffix);
never by substring, so "Setup 1" can not pick "Setup 10".
"""

import keyword
import re

from . import rules as rules_mod
from .expr import ExprError, compile_expr

SOURCES = ("model", "order", "auto")
TYPES = ("", "bool", "number", "text")
UNKNOWN_POLICIES = ("leave", "suppress", "activate")


class NoRuleMatched(ValueError):
    pass


# ----------------------------------------------------------------------
# Names
# ----------------------------------------------------------------------
_COPY_SUFFIX = re.compile(r"\s*\(\d+\)$")
_DASHES = re.compile(r"\s*-\s*")
_SPACES = re.compile(r"\s+")


def normalize_setup_name(s):
    """'Left Rabbet - Out - G57' / 'left rabbet-out g57 (2)' -> 'left rabbet out g57'."""
    s = _SPACES.sub(" ", str(s or "")).strip()
    s = _COPY_SUFFIX.sub("", s)
    s = _DASHES.sub(" ", s)
    return _SPACES.sub(" ", s).strip().lower()


def normalize_op_name(s):
    """'Front Bottom Notch  (2)' -> 'front bottom notch'; 'Back bottom Notch' too."""
    s = _COPY_SUFFIX.sub("", str(s or "").strip())
    return _SPACES.sub(" ", s).strip().lower()


def _var_spec(spec):
    """Normalise a variable spec to (param, source, type, default)."""
    if isinstance(spec, str):
        return spec, "auto", "", None
    return (spec.get("param"), spec.get("source", "auto"), spec.get("type", "") or "",
            spec.get("default"))


def _valid_name(name):
    return (isinstance(name, str) and name.isidentifier() and not keyword.iskeyword(name)
            and name not in ("true", "false"))


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------
def validate_profile(profile):
    """Return a list of human readable problems ([] when the profile is fine)."""
    errors = []
    if not isinstance(profile, dict):
        return ["profile must be an object"]
    pid = profile.get("id")
    label = "profile %r" % (pid,)
    if not pid or not isinstance(pid, str):
        errors.append("profile is missing an 'id'")
    if profile.get("type", "*") not in ("*", "door", "stile", "panel"):
        errors.append("%s: unknown type %r" % (label, profile.get("type")))
    if not (profile.get("series") or "").strip() and not (profile.get("family") or "").strip():
        errors.append("%s: needs a 'family' or a 'series' pattern" % label)

    known = set()
    variables = profile.get("variables")
    if not isinstance(variables, dict):
        errors.append("%s: 'variables' must be an object" % label)
        variables = {}
    for name, spec in variables.items():
        if not _valid_name(name):
            errors.append("%s: bad variable name %r" % (label, name))
        if not isinstance(spec, (str, dict)):
            errors.append("%s: variable %r must be a parameter name or an object" % (label, name))
            continue
        param, source, vtype, default = _var_spec(spec)
        if not param or not isinstance(param, str):
            errors.append("%s: variable %r has no parameter name" % (label, name))
        if source not in SOURCES:
            errors.append("%s: variable %r has bad source %r (use %s)"
                          % (label, name, source, "/".join(SOURCES)))
        if vtype not in TYPES:
            errors.append("%s: variable %r has bad type %r" % (label, name, vtype))
        if default is not None and not isinstance(default, (int, float, str)):
            errors.append("%s: variable %r has a bad default %r" % (label, name, default))
        known.add(name)

    def check(text, where, allowed):
        try:
            ex = compile_expr(text)
        except ExprError as e:
            errors.append("%s: %s: %s" % (label, where, e))
            return
        unknown = sorted(ex.names - allowed)
        if unknown:
            errors.append("%s: %s uses unknown variable(s) %s"
                          % (label, where, ", ".join(unknown)))

    derived = profile.get("derived") or {}
    if not isinstance(derived, dict):
        errors.append("%s: 'derived' must be an object" % label)
        derived = {}
    for name, text in derived.items():
        if not _valid_name(name):
            errors.append("%s: bad derived name %r" % (label, name))
        if name in known:
            errors.append("%s: derived %r clashes with a variable or earlier derived value"
                          % (label, name))
        check(text, "derived %r" % name, known)
        known.add(name)

    rows = profile.get("sequence_rules")
    if not isinstance(rows, list) or not rows:
        errors.append("%s: 'sequence_rules' must be a non-empty list" % label)
        rows = []
    seen = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append("%s: sequence row %d must be an object" % (label, i + 1))
            continue
        rid = row.get("id")
        where = "row %r" % (rid if rid else i + 1)
        if not rid:
            errors.append("%s: sequence row %d has no id" % (label, i + 1))
        elif rid in seen:
            errors.append("%s: duplicate row id %r" % (label, rid))
        seen.add(rid)
        if "when" not in row:
            errors.append("%s: %s has no 'when'" % (label, where))
        else:
            check(row["when"], where, known)
        setups = row.get("setups")
        if not isinstance(setups, list) or not all(isinstance(s, str) and s.strip() for s in setups):
            errors.append("%s: %s 'setups' must be a list of setup names" % (label, where))

    ops = profile.get("operation_rules") or []
    if not isinstance(ops, list):
        errors.append("%s: 'operation_rules' must be a list" % label)
        ops = []
    seen_ops = set()
    for i, rule in enumerate(ops):
        if not isinstance(rule, dict) or not isinstance(rule.get("op"), str) or not rule["op"].strip():
            errors.append("%s: operation rule %d needs an 'op' name" % (label, i + 1))
            continue
        key = normalize_op_name(rule["op"])
        if key in seen_ops:
            errors.append("%s: duplicate operation rule %r" % (label, rule["op"]))
        seen_ops.add(key)
        if "active" not in rule:
            errors.append("%s: operation %r has no 'active'" % (label, rule["op"]))
        else:
            check(rule["active"], "operation %r" % rule["op"], known)

    policy = profile.get("unknown_operations", "leave")
    if policy not in UNKNOWN_POLICIES:
        errors.append("%s: unknown_operations must be one of %s, not %r"
                      % (label, "/".join(UNKNOWN_POLICIES), policy))
    if not isinstance(profile.get("splice", {}), dict):
        errors.append("%s: 'splice' must be an object" % label)
    return errors


# ----------------------------------------------------------------------
# Values
# ----------------------------------------------------------------------
_FALSE_TEXT = ("0", "false", "no", "off", "none", "null", "")


def _unwrap(v):
    # Accept [value, "type", "description"] and ParamValue-like objects too.
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    if hasattr(v, "value") and hasattr(v, "dtype"):
        return v.value
    return v


def _text(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    if len(s) >= 2 and s[0] == s[-1] == "'":      # Fusion text expression 'B-354'
        s = s[1:-1].strip()
    return s


def _coerce(v, vtype):
    """Return the env value, or raise ValueError when it can't be used."""
    if vtype == "text":
        return _text(v)
    if vtype == "bool":
        if isinstance(v, (bool, int, float)):
            return 1 if v else 0
        return 0 if str(v).strip().lower() in _FALSE_TEXT else 1
    if isinstance(v, bool):
        return 1 if v else 0
    if vtype == "number":
        if isinstance(v, (int, float)):
            return v
        return float(str(v).strip())
    return v


def collect_values(profile, model_values, order_values):
    """Build the evaluation env; returns ``(env, missing_variable_names)``."""
    model_values = model_values or {}
    order_values = order_values or {}
    env, missing = {}, []
    for name, spec in (profile.get("variables") or {}).items():
        param, source, vtype, default = _var_spec(spec)
        value = None
        if source in ("model", "auto"):
            value = _unwrap(model_values.get(param))
        if value is None and source in ("order", "auto"):
            value = _unwrap(order_values.get(param))
        if value is not None:
            try:
                env[name] = _coerce(value, vtype)
                continue
            except ValueError:
                pass
        if default is None:
            default = "" if vtype == "text" else 0
        env[name] = default
        missing.append(name)
    for name, text in (profile.get("derived") or {}).items():
        env[name] = compile_expr(text).eval(env)
    return env, missing


# ----------------------------------------------------------------------
# Planning
# ----------------------------------------------------------------------
def select_profile(profiles, ctype, series_id, fallback_family=""):
    """Best matching profile (same matching rules as nfe.rules) or None."""
    return rules_mod.resolve(profiles, ctype, series_id, fallback_family)


def explain(profile, env):
    """[(row_id, matched)] for every sequence row (dashboard view)."""
    return [(row.get("id"), bool(compile_expr(row["when"]).eval(env)))
            for row in profile.get("sequence_rules") or []]


def plan(profile, env):
    for row in profile.get("sequence_rules") or []:
        if compile_expr(row["when"]).eval(env):
            break
    else:
        raise NoRuleMatched("profile %r: no sequence row matched" % profile.get("id"))
    op_rules = [(r["op"], bool(compile_expr(r["active"]).eval(env)))
                for r in profile.get("operation_rules") or []]
    return {
        "profile_id": profile.get("id"),
        "row_id": row.get("id"),
        "note": row.get("note", ""),
        "setups": list(row.get("setups") or []),
        "op_rules": op_rules,
        "unknown_operations": profile.get("unknown_operations", "leave"),
        "splice": dict(profile.get("splice") or {}),
        "env": env,
    }


def expects_toolpath(plan_dict):
    return bool(plan_dict.get("setups"))


def _find(name, candidates, key, normalize):
    """Exact match first, then normalised equality; never substrings."""
    for c in candidates:
        if key(c) == name:
            return c
    target = normalize(name)
    for c in candidates:
        if normalize(key(c)) == target:
            return c
    return None


def resolve_actions(plan_dict, cam_setups):
    """Map a plan onto the live CAM tree.

    ``cam_setups``: ``[{"name", "suppressed", "ops": [{"name", "suppressed"}]}]``.
    Returns the actual setup names to post (plan order), selected setups that
    are currently suppressed, the desired active state of every operation the
    plan decides (``{setup: {op: bool}}``), plan setups not found and
    operations no rule covers."""
    policy = plan_dict.get("unknown_operations", "leave")
    rules_by_exact = {}
    rules_by_norm = {}
    for op_name, active in plan_dict.get("op_rules") or []:
        rules_by_exact.setdefault(op_name, active)
        rules_by_norm.setdefault(normalize_op_name(op_name), active)

    out = {"sequence": [], "unsuppress_setups": [], "ops": {},
           "missing_setups": [], "unknown_ops": []}
    for wanted in plan_dict.get("setups") or []:
        setup = _find(wanted, cam_setups, lambda s: s["name"], normalize_setup_name)
        if setup is None:
            out["missing_setups"].append(wanted)
            continue
        sname = setup["name"]
        out["sequence"].append(sname)
        if setup.get("suppressed") and sname not in out["unsuppress_setups"]:
            out["unsuppress_setups"].append(sname)
        if sname in out["ops"]:
            continue                                # posted twice: decided already
        states = out["ops"][sname] = {}
        for op in setup.get("ops") or []:
            oname = op["name"]
            if oname in rules_by_exact:
                states[oname] = rules_by_exact[oname]
                continue
            norm = normalize_op_name(oname)
            if norm in rules_by_norm:
                states[oname] = rules_by_norm[norm]
                continue
            out["unknown_ops"].append((sname, oname))
            if policy == "suppress":
                states[oname] = False
            elif policy == "activate":
                states[oname] = True
    return out


# ----------------------------------------------------------------------
# Markdown (docs/tables/TOOLPATH_SELECTION.md is generated from this)
# ----------------------------------------------------------------------
BEGIN_MARK = "<!-- BEGIN GENERATED: nfe.cam_rules.render_markdown(DEFAULT_PROFILES) -->"
END_MARK = "<!-- END GENERATED -->"


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def _code(text):
    return "`%s`" % _cell(text) if text != "" else ""


def render_markdown(profiles):
    out = []
    for p in profiles:
        out.append("## %s (`%s`)" % (p.get("title") or p.get("id"), p.get("id")))
        out.append("")
        match = []
        if p.get("type"):
            match.append("type `%s`" % p["type"])
        if (p.get("series") or "").strip():
            match.append("series pattern `%s`" % p["series"])
        elif p.get("family"):
            match.append("family `%s`" % p["family"])
        if not p.get("enabled", True):
            match.append("**disabled**")
        out.append("Applies to: %s." % ", ".join(match))
        if p.get("description"):
            out.append("")
            out.append(p["description"])
        out.append("")

        out.append("### Variables")
        out.append("")
        out.append("| Variable | Parameter | Read from | Type | Default |")
        out.append("|---|---|---|---|---|")
        for name, spec in (p.get("variables") or {}).items():
            param, source, vtype, default = _var_spec(spec)
            src = {"model": "model", "order": "order",
                   "auto": "model, else order"}.get(source, source)
            if default is None:
                default = "''" if vtype == "text" else 0
            out.append("| `%s` | `%s` | %s | %s | %s |"
                       % (name, param, src, vtype or "as is", _cell(default)))
        for name, text in (p.get("derived") or {}).items():
            out.append("| `%s` | = %s | derived | | |" % (name, _code(text)))
        out.append("")

        out.append("### Setup selection (first matching row wins)")
        out.append("")
        out.append("| Row | When (plain words) | Expression | Setups posted, in order |")
        out.append("|---|---|---|---|")
        for row in p.get("sequence_rules") or []:
            setups = row.get("setups") or []
            posted = " → ".join("`%s`" % _cell(s) for s in setups) if setups else "*none (skip CAM)*"
            flag = " ⚠" if row.get("review") else ""
            out.append("| %s%s | %s | %s | %s |" % (row.get("id"), flag, _cell(row.get("note", "")),
                                                    _code(row.get("when", "")), posted))
        out.append("")
        splice = p.get("splice") or {}
        if splice:
            out.append("Splice options: %s." % ", ".join("`%s = %s`" % (k, str(v).lower())
                                                        for k, v in splice.items()))
            out.append("")

        out.append("### Operation activation (inside the selected setups)")
        out.append("")
        ops = p.get("operation_rules") or []
        if ops:
            out.append("| Operation | Active when | Expression |")
            out.append("|---|---|---|")
            for r in ops:
                out.append("| %s | %s | %s |" % (_cell(r["op"]), _cell(r.get("note", "")),
                                                _code(r.get("active", ""))))
            out.append("")
        policy = p.get("unknown_operations", "leave")
        out.append("Operations not listed: **%s** (%s)." % (policy, {
            "leave": "left as they are in the model",
            "suppress": "suppressed",
            "activate": "made active"}.get(policy, "?")))
        out.append("")

        out.append("### Conflicts with old documents — please confirm")
        out.append("")
        reviews = [(row.get("id"), row["review"]) for row in p.get("sequence_rules") or []
                   if row.get("review")]
        reviews += [(r["op"], r["review"]) for r in ops if r.get("review")]
        reviews += [("General", text) for text in p.get("review") or []]
        grouped = {}                                    # same text -> one bullet
        for rid, text in reviews:
            grouped.setdefault(text, []).append(rid)
        if grouped:
            for text, ids in grouped.items():
                out.append("- **%s**: %s" % (", ".join(_cell(i) for i in ids), text))
        else:
            out.append("- none found")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def write_markdown(path, profiles):
    """Replace the generated block between the markers in ``path``."""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    start = text.index(BEGIN_MARK) + len(BEGIN_MARK)
    end = text.index(END_MARK, start)
    new = text[:start] + "\n\n" + render_markdown(profiles) + "\n" + text[end:]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    return new


if __name__ == "__main__":      # py -3.13 -m nfe.cam_rules <doc path>
    import sys
    from .cam_defaults import DEFAULT_PROFILES
    write_markdown(sys.argv[1], DEFAULT_PROFILES)
