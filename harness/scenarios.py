"""Validation-scenario sheets -> order JSON.

A sheet is the "ValidationScenarios" tab of an inputs & outputs workbook saved
as CSV.  The header row (found by its ``Scenario`` and ``Drawing required?``
cells; it is not always the first row) is followed by one scenario per row::

    Count | Scenario     | Drawing required? | component_height | ... |  | Series | ... | Gapless | ...
    7     | 2X8X_SV_0007 | Yes               | 110.3125         | ... |  | 2X86   | ... | G       | ...

The columns between ``Drawing required?`` and the first blank header are the
model parameters.  Each scenario becomes an order with one component:

* ``order_id``   ``<Series>-<number>`` (2X86-0007): the stile sheets reuse the
  same scenario numbers, the series keeps their orders apart
* ``series_id``  ``<Series>`` plus ``G`` when the Gapless column says so
* ``drawing_test``  the sheet title; the add-in copies the order's drawing
  PDFs into ``drawing_test/<title>/``
"""

import csv
import os
import re

KINDS = {"D": ("doors", "D1"), "S": ("stiles", "S1"), "P": ("panels", "P1")}
_KIND = re.compile(r"_([DSP])V_", re.I)
_NUMBER = re.compile(r"(\d+)\s*$")


class SheetError(ValueError):
    pass


def _clean(text):
    return re.sub(r"\s+", "", text or "")


def sheet_title(filename):
    """'2X8X Door inputs & outputs v10 2026_09_10(Validation...).csv' -> '2X8X Door'."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    return (re.split(r"\s+inputs\b", stem, maxsplit=1, flags=re.I)[0].strip() or stem)[:80]


def _value(text, is_bool):
    text = text.strip()
    if text == "":
        return None, "bool" if is_bool else "float"
    if is_bool:
        return text == "1", "bool"
    try:
        return float(text), "float"
    except ValueError:
        return text, "string"


def parse_sheet(text, filename=""):
    """Parse CSV text into ``{"title", "columns", "rows"}``.

    Every row is ``{"n", "scenario", "drawing", "series", "series_id", "label",
    "order_id", "kind", "values"}`` with values as ``name -> (value, type)``.
    """
    table = list(csv.reader(text.splitlines()))
    head = None
    for i, r in enumerate(table[:50]):
        cells = [c.strip().lower() for c in r]
        if "scenario" in cells and any(c.startswith("drawing required") for c in cells):
            head = i
            break
    if head is None:
        raise SheetError("no header row with 'Scenario' and 'Drawing required?' columns")
    header = [c.strip() for c in table[head]]
    lower = [c.lower() for c in header]
    c_scn = lower.index("scenario")
    c_draw = next(i for i, c in enumerate(lower) if c.startswith("drawing required"))
    cols = []
    for i in range(c_draw + 1, len(header)):
        if not header[i]:
            break
        cols.append((i, _clean(header[i])))
    if not cols:
        raise SheetError("no parameter columns after 'Drawing required?'")

    def find(name):
        return lower.index(name) if name in lower else None

    c_series, c_gap, c_label = find("series"), find("gapless"), find("human readable label")
    data = [r + [""] * (len(header) - len(r)) for r in table[head + 1:]
            if len(r) > c_scn and r[c_scn].strip()]
    bools = {i for i, _ in cols
             if all(r[i].strip() in ("0", "1", "") for r in data) and any(r[i].strip() for r in data)}
    rows, seen = [], set()
    for r in data:
        scenario = r[c_scn].strip()
        kind = _KIND.search(scenario)
        num = _NUMBER.search(scenario)
        if not kind or not num:
            raise SheetError("scenario %r is not like 2X8X_DV_0001 / 2X8X_SV_0001" % scenario)
        series = r[c_series].strip() if c_series is not None else ""
        gapless = c_gap is not None and r[c_gap].strip().upper() == "G"
        label = r[c_label].strip() if c_label is not None else ""
        if not label:      # the door sheet keeps the label in an unnamed column
            label = next((c.strip() for c in r if c.strip().startswith(scenario + ":")), "")
        label = re.sub(r"\s+", " ", label[len(scenario) + 1:] if label.startswith(scenario + ":") else label).strip()
        order_id = "%s-%s" % (series, num.group(1)) if series else scenario
        if order_id in seen:
            raise SheetError("order id %s appears twice" % order_id)
        seen.add(order_id)
        rows.append({
            "n": int(num.group(1)), "scenario": scenario,
            "drawing": r[c_draw].strip().lower() in ("yes", "y", "true", "1"),
            "series": series, "series_id": series + ("G" if gapless else ""),
            "label": label, "order_id": order_id, "kind": kind.group(1).upper(),
            "values": {name: _value(r[i], i in bools) for i, name in cols},
        })
    if not rows:
        raise SheetError("the sheet has no scenarios")
    return {"title": sheet_title(filename), "columns": [name for _, name in cols], "rows": rows}


def load_sheet(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        return parse_sheet(fh.read(), os.path.basename(path))


def build_order(sheet, row):
    key, cid = KINDS[row["kind"]]
    params = {}
    if row["series_id"]:
        params["series_id"] = [row["series_id"], "string", ""]
    for name, (value, dtype) in row["values"].items():
        params[name] = [value, dtype, ""]
    return {
        "order_id": [row["order_id"], "string", "validation scenario %s" % row["scenario"]],
        "drawing_test": sheet["title"],
        key: [{"id": [cid, "string", ""], "parameters": params}],
    }


def select_rows(sheet, mode, selection=""):
    """``mode`` "drawings": every row with Drawing required = Yes.
    ``mode`` "selection": numbers, ranges and ids, e.g. ``7, 120-135, 2X86-0400``."""
    rows = sheet["rows"]
    if mode == "drawings":
        return [r for r in rows if r["drawing"]]
    if mode != "selection":
        raise SheetError("mode must be drawings or selection")
    wanted, ids = set(), set()
    for part in re.split(r"[,;\s]+", str(selection or "").strip()):
        if not part:
            continue
        m = re.match(r"^(\d+)(?:-(\d+))?$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            if b < a or b - a > 100000:
                raise SheetError("bad range %r" % part)
            wanted.update(range(a, b + 1))
        else:
            ids.add(part.upper())
    out = [r for r in rows if r["n"] in wanted
           or r["scenario"].upper() in ids or r["order_id"].upper() in ids]
    missing = sorted(ids - {r["scenario"].upper() for r in out} - {r["order_id"].upper() for r in out})
    if missing:
        raise SheetError("not in this sheet: %s" % ", ".join(missing[:10]))
    if not out:
        raise SheetError("the selection matches no scenario")
    return out
