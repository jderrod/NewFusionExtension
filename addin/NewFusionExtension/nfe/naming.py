"""Output naming conventions (config section "naming").

Defaults follow the old pipeline so downstream machines and people recognise
the files:

    order folder   {order_id} {run_stamp}            3X8X_DV_0622 Sep 28 2026 - 0520
    G-code         1-{component_id}-{order_id}.txt   1-D1-3X8X_DV_0622.txt
    drawing PDF    1-{component_id}-{order_id}-drawing.pdf
    drilling       {order_id}.json                   (one file per order, Gannomat)
    report         {order_id}-report.json

Every file of a component shares the stem ``1-{component_id}-{order_id}``, so
the G-code, drawing and drilling rows (ProgramName) of one part are linked by
name, and every file lives in the order's run folder.

Placeholders: {order_id} {component_id} {type} {series_id} {source} (order file
name without extension) {run_stamp} (e.g. "Sep 28 2026 - 0520", no colons -
they are illegal in Windows names) {date} (2026-09-28) {time} (0520).
"""

import os
import re
import time

NAMING_DEFAULTS = {
    "order_folder": "{order_id} {run_stamp}",
    "gcode": "1-{component_id}-{order_id}.txt",
    "drawing_pdf": "1-{component_id}-{order_id}-drawing.pdf",
    "drilling": "{order_id}.json",
    "report": "{order_id}-report.json",
}

PLACEHOLDERS = ("order_id", "component_id", "type", "series_id", "source",
                "run_stamp", "date", "time")

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class NamingError(ValueError):
    pass


def safe_filename(name, max_len=150):
    """Windows-safe file/folder name: illegal characters -> '_', no trailing dots/spaces."""
    s = _ILLEGAL.sub("_", str(name)).strip().rstrip(". ")
    return s[:max_len] or "_"


def run_stamp(ts=None):
    """'Sep 28 2026 - 0520' (the old pipeline's '%b %#d %Y - %H%M')."""
    t = time.localtime(ts if ts is not None else time.time())
    return "%s %d %d - %02d%02d" % (_MONTHS[t.tm_mon - 1], t.tm_mday, t.tm_year, t.tm_hour, t.tm_min)


def fields(order_id, component_id="", ctype="", series_id="", source="", ts=None):
    t = time.localtime(ts if ts is not None else time.time())
    return {
        "order_id": order_id, "component_id": component_id, "type": ctype,
        "series_id": series_id, "source": os.path.splitext(os.path.basename(source or ""))[0],
        "run_stamp": run_stamp(ts), "date": "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday),
        "time": "%02d%02d" % (t.tm_hour, t.tm_min),
    }


def render(template, values):
    try:
        text = str(template).format(**values)
    except (KeyError, IndexError, ValueError) as e:
        raise NamingError("bad name template %r: %s (placeholders: %s)"
                          % (template, e, ", ".join("{%s}" % p for p in PLACEHOLDERS)))
    return safe_filename(text)


def validate(naming):
    errors = []
    sample = fields("ORDER", "D1", "door", "3082G", "order.json", 0)
    for key in NAMING_DEFAULTS:
        tpl = (naming or {}).get(key, NAMING_DEFAULTS[key])
        try:
            out = render(tpl, sample)
        except NamingError as e:
            errors.append("naming.%s: %s" % (key, e))
            continue
        if key != "order_folder" and "{component_id}" not in tpl and key in ("gcode", "drawing_pdf"):
            errors.append("naming.%s must contain {component_id} (one file per component)" % key)
        if key == "gcode" and not out.lower().endswith(".txt"):
            errors.append("naming.gcode must end in .txt")
        if key == "drawing_pdf" and not out.lower().endswith(".pdf"):
            errors.append("naming.drawing_pdf must end in .pdf")
    return errors


def unique_dir(parent, name):
    """parent/name, or 'name (2)', 'name (3)' ... if it already exists."""
    path = os.path.join(parent, name)
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(parent, "%s (%d)" % (name, n))
    return path


class Namer(object):
    """Names for one order run."""

    def __init__(self, naming, order_id, source="", ts=None):
        self.templates = dict(NAMING_DEFAULTS)
        self.templates.update({k: v for k, v in (naming or {}).items() if v})
        self.order_id = order_id
        self.source = source
        self.ts = ts if ts is not None else time.time()

    def _f(self, component_id="", ctype="", series_id=""):
        return fields(self.order_id, component_id, ctype, series_id, self.source, self.ts)

    def order_folder(self):
        return render(self.templates["order_folder"], self._f())

    def gcode(self, component_id, ctype="", series_id=""):
        return render(self.templates["gcode"], self._f(component_id, ctype, series_id))

    def drawing_pdf(self, component_id, ctype="", series_id=""):
        return render(self.templates["drawing_pdf"], self._f(component_id, ctype, series_id))

    def drilling(self):
        return render(self.templates["drilling"], self._f())

    def report(self):
        return render(self.templates["report"], self._f())

    def program_name(self, component_id):
        """Stem shared by a component's files (also the drilling ProgramName)."""
        return os.path.splitext(self.gcode(component_id))[0]
