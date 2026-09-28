"""Series rules: pick the Fusion model + drawing for a component.

A rule looks like::

    {"id": "3x8x-door", "enabled": true, "type": "door",
     "family": "3X8X", "series": "",          # or e.g. "3?82*"
     "model":   {"id": "urn:adsk.wipprod:dm.lineage:...", "name": "3X8X_Door", "path": "..."},
     "drawing": {"id": "urn:...", "name": "3X8X_Door Drawing", "path": "..."}}

``series`` is a case-insensitive glob matched against the component's
``series_id``.  When it is blank the ``family`` is used instead: family
``3X8X`` means "first series digit 3, third digit 8" (e.g. 3082G.67P, 3186).
The most specific matching rule wins (exact type beats ``*``, then the
number of literal characters in the pattern).
"""

import fnmatch
import re

_SERIES_CODE = re.compile(r"(\d)(\d)(\d)(\d)")


def family_of(series_id):
    """'3082G.67P' -> '3X8X', 'S2186' -> '2X8X'; '' when unrecognised."""
    m = _SERIES_CODE.search(str(series_id or ""))
    if not m:
        return ""
    return "%sX%sX" % (m.group(1), m.group(3))


def family_from_order_id(order_id):
    m = re.match(r"(\dX\dX)", str(order_id or "").upper())
    return m.group(1) if m else ""


def _literal_count(pattern):
    return sum(1 for ch in pattern if ch not in "*?[]")


def rule_matches(rule, ctype, series_id, fallback_family=""):
    if not rule.get("enabled", True):
        return None
    rtype = rule.get("type", "*")
    if rtype not in ("*", ctype):
        return None
    pattern = (rule.get("series") or "").strip()
    if pattern:
        if not series_id or not fnmatch.fnmatchcase(series_id.upper(), pattern.upper()):
            return None
        literal = 100 + _literal_count(pattern)
    else:
        family = (rule.get("family") or "").upper()
        if not family:
            return None
        actual = family_of(series_id) or fallback_family.upper()
        if actual != family:
            return None
        literal = 50
    return (1 if rtype == ctype else 0, literal)


def resolve(rules, ctype, series_id, fallback_family=""):
    """Return the best rule for a component or None."""
    best, best_score = None, None
    for rule in rules or []:
        score = rule_matches(rule, ctype, series_id, fallback_family)
        if score is not None and (best_score is None or score > best_score):
            best, best_score = rule, score
    return best


def explain(rules, order):
    """Resolve every component of an order (used by the dashboard preview)."""
    fam = family_from_order_id(order.order_id)
    out = []
    for c in order.components:
        rule = resolve(rules, c.type, c.series_id, fam)
        out.append({
            "component_id": c.id, "type": c.type, "series_id": c.series_id,
            "family": family_of(c.series_id) or fam,
            "rule_id": rule.get("id") if rule else None,
            "model": (rule.get("model") or {}).get("name") if rule else None,
            "drawing": (rule.get("drawing") or {}).get("name") if rule else None,
        })
    return out


# ----------------------------------------------------------------------
# Rule suggestions from hub folder listings (dashboard "Suggest from hub")
# ----------------------------------------------------------------------
_MODEL_NAME = re.compile(r"^(\d)X8([0-9X]+(?:/[0-9]+)*)_(door|stile|panel)(?![a-z])", re.I)


def series_pattern_for(first_digit, sub):
    """('3','X') -> family; ('3','2') -> '3?82*'; ('2','1/2') -> '2?8[12]*'."""
    if sub.upper() == "X":
        return ""
    digits = sub.split("/")
    if len(digits) == 1:
        return "%s?8%s*" % (first_digit, digits[0])
    return "%s?8[%s]*" % (first_digit, "".join(digits))


def suggest_rules(files, existing=()):
    """files: [{"id","name","ext","folder_id","path"}] from the family folders.

    Returns new rules for models that follow the "<code>_<Type>" naming
    convention and are not already used by an existing rule."""
    used = {((r.get("model") or {}).get("id")) for r in existing}
    drawings = {f["name"].lower(): f for f in files if f.get("ext") == "f2d"}
    out = []
    for f in files:
        if f.get("ext") != "f3d" or f["id"] in used:
            continue
        m = _MODEL_NAME.match(f["name"])
        if not m:
            continue
        digit, sub, ctype = m.group(1), m.group(2), m.group(3).lower()
        pattern = series_pattern_for(digit, sub)
        family = "%sX8X" % digit
        drawing = drawings.get((f["name"] + " drawing").lower())
        rid = re.sub(r"[^a-z0-9]+", "-", f["name"].lower()).strip("-")
        out.append({
            "id": rid, "enabled": True, "type": ctype, "family": family, "series": pattern,
            "model": {"id": f["id"], "name": f["name"], "folder_id": f.get("folder_id"),
                      "path": f.get("path", "")},
            "drawing": ({"id": drawing["id"], "name": drawing["name"],
                         "folder_id": drawing.get("folder_id"), "path": drawing.get("path", "")}
                        if drawing else {}),
        })
    return out
