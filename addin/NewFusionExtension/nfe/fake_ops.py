"""In-memory stand-in for Fusion, used by unit tests and simulation mode.

It reproduces the behaviours that matter for robustness:

* cloud saves complete asynchronously (after ``save_ticks`` polls)
* updating a drawing spawns a hidden copy of the referenced model
  (``spawn_hidden_copies``) - the source of the old pipeline's leak
* some documents ignore the first close() (``stubborn_closes``)
"""

import copy
import itertools
import os
import re

from .ops_base import Ops, OpsError
from .params import ModelParam

_ids = itertools.count(1)
CRLF = chr(13) + chr(10)


class FakeFile(object):
    def __init__(self, fid, name, ext, params=None, cam=None, computed=None):
        self.id = fid
        self.name = name
        self.ext = ext
        self.version = 1
        self.params = params or {}
        # [{"name", "suppressed", "ops": [{"name", "suppressed"}]}] like the real model
        self.cam = cam
        # computed (model-derived) values: fn(values) -> {name: value}
        self.computed = computed


class FakeDoc(object):
    def __init__(self, file, visible=True):
        self.uid = next(_ids)
        self.file = file
        self.visible = visible
        self.alive = True
        self.modified = False
        self.params = {k: ModelParam(v.name, v.unit, v.is_text, v.expression, v.value)
                       for k, v in file.params.items()}
        self.ref_version = None
        self.close_refusals = 0
        self.cam = copy.deepcopy(file.cam)

    def __repr__(self):
        return "<FakeDoc %s #%d>" % (self.file.name, self.uid)


class FakeOps(Ops):
    def __init__(self, files, save_ticks=2, spawn_hidden_copies=True, stubborn_closes=0,
                 fail_open=(), fail_export=False, not_ready_polls=0, resolve_failures=0):
        self.files = {f.id: f for f in files}
        self.docs = []
        self.save_ticks = save_ticks
        self.spawn_hidden_copies = spawn_hidden_copies
        self.stubborn_closes = stubborn_closes
        self.fail_open = set(fail_open)
        self.fail_export = fail_export
        self.foreign = []
        self.log = []
        self.active = None
        self._save_polls = {}
        self.not_ready_polls = not_ready_polls
        self.generate_polls = 2
        self.stale_opens = 0          # next N opens return the previous version (hub lag)
        self.workspace = "design"
        self.resolve_failures = resolve_failures

    def ready(self):
        if self.not_ready_polls > 0:
            self.not_ready_polls -= 1
            return False, "starting up"
        return True, ""

    # session
    def begin_session(self):
        self.foreign = [d for d in self.docs if d.alive]

    # files
    def resolve_file(self, ref):
        if self.resolve_failures > 0:
            self.resolve_failures -= 1
            raise OpsError("hub not answering")
        f = self.files.get(ref.get("id"))
        if f is None:
            raise OpsError("file not found in hub: %s" % (ref.get("name") or ref.get("id")))
        return f

    def file_version(self, file):
        return file.version

    # documents
    def find_open(self, file):
        for d in self.docs:
            if d.alive and d.visible and d.file is file:
                return d
        return None

    def open_document(self, file, visible=True):
        self.log.append(("open", file.name))
        if file.id in self.fail_open:
            raise OpsError("documents.open() returned None for %s" % file.name)
        d = FakeDoc(file, visible)
        if self.stale_opens > 0 and file.ext == "f3d" and file.version > 1:
            self.stale_opens -= 1
            d.version_override = file.version - 1
        if file.ext == "f2d":
            d.ref_version = 1
        d.close_refusals = self.stubborn_closes
        self.docs.append(d)
        self.active = d
        return d

    def doc_alive(self, doc):
        return doc.alive

    def doc_label(self, doc):
        return "%s%s" % (doc.file.name, "" if doc.visible else " (hidden)")

    def doc_is_modified(self, doc):
        return doc.modified

    def doc_version(self, doc):
        return getattr(doc, "version_override", None) or doc.file.version

    def activate(self, doc):
        self.active = doc

    def close_document(self, doc):
        self.log.append(("close", doc.file.name))
        if doc.close_refusals > 0:     # Fusion "succeeds" but nothing happens
            doc.close_refusals -= 1
            return True
        doc.alive = False
        return True

    def managed_documents(self, keep=()):
        keep_ids = {d.uid for d in keep}
        foreign = {d.uid for d in self.foreign}
        return [d for d in self.docs if d.alive and d.uid not in keep_ids and d.uid not in foreign]

    def document_count(self):
        return sum(1 for d in self.docs if d.alive)

    # parameters
    def read_params(self, doc):
        return dict(doc.params)

    def apply_params(self, doc, assignments):
        out = []
        for name, expr in assignments:
            p = doc.params.get(name)
            if p is None:
                out.append({"name": name, "ok": False, "message": "not found"})
                continue
            before = p.expression
            p.expression = expr
            doc.modified = True
            out.append({"name": name, "ok": True, "before": before, "after": expr, "expression": expr})
        return out

    # saving
    def start_save(self, doc, message):
        if not doc.modified:
            return None
        self.log.append(("save", doc.file.name))
        token = {"from": doc.file.version, "id": next(_ids)}
        self._save_polls[token["id"]] = 0
        return token

    def save_complete(self, doc, token):
        n = self._save_polls[token["id"]] = self._save_polls[token["id"]] + 1
        if n >= self.save_ticks:
            if doc.modified:
                doc.file.version += 1
                doc.modified = False
            return True
        return False

    # drawings
    def update_drawing(self, drawing, model_doc):
        drawing.ref_version = model_doc.file.version
        if self.spawn_hidden_copies:
            hidden = FakeDoc(model_doc.file, visible=False)
            hidden.close_refusals = self.stubborn_closes
            self.docs.append(hidden)
        return {"references": [{"name": model_doc.file.name, "version": drawing.ref_version}]}

    def drawing_up_to_date(self, drawing, model_doc):
        return drawing.ref_version == model_doc.file.version

    def export_pdf(self, drawing, path):
        if self.fail_export:
            raise OpsError("export failed")
        with open(path, "wb") as fh:
            fh.write(b"%PDF-1.7\n" + b"%fake drawing " + drawing.file.name.encode() + b"\n" * 80)

    def read_values(self, doc):
        out = {}
        for name, p in doc.params.items():
            expr = (p.expression or "").strip()
            if p.is_text:
                out[name] = expr.strip("'")
                continue
            m = re.match(r"^-?\d+(\.\d+)?", expr)
            out[name] = float(m.group(0)) if m else 0.0
        if doc.file.computed:
            out.update(doc.file.computed(dict(out)))
        return out

    # manufacture
    def cam_state(self, doc):
        if doc.cam is None:
            return None
        return [{"name": s["name"], "suppressed": s["suppressed"],
                 "ops": [{"name": o["name"], "suppressed": o["suppressed"],
                          "has_toolpath": o.get("has_toolpath", False), "valid": o.get("has_toolpath", False)}
                         for o in s["ops"]]} for s in doc.cam]

    def activate_workspace(self, kind):
        self.log.append(("workspace", kind))
        self.workspace = kind

    def cam_prepare(self, doc, actions):
        changes = []
        for s in doc.cam:
            if s["name"] in actions.get("unsuppress_setups", []) and s["suppressed"]:
                s["suppressed"] = False
                changes.append("unsuppressed setup %s" % s["name"])
            for o in s["ops"]:
                states = (actions.get("ops") or {}).get(s["name"], {})
                if o["name"] in states and o["suppressed"] == states[o["name"]]:
                    o["suppressed"] = not states[o["name"]]
                    changes.append("%s / %s" % (s["name"], o["name"]))
        doc.modified = True
        return changes

    def cam_generate(self, doc, setup_names):
        ops = [o for s in doc.cam if s["name"] in setup_names for o in s["ops"] if not o["suppressed"]]
        if not ops:
            return None
        self.log.append(("generate", tuple(setup_names)))
        return {"ops": ops, "polls": 0}

    def cam_generate_done(self, token):
        if token is None:
            return True
        token["polls"] += 1
        if token["polls"] >= self.generate_polls:
            for o in token["ops"]:
                o["has_toolpath"] = not o.get("fails", False)
            return True
        return False

    def cam_problems(self, doc, setup_names):
        errors = ["%s / %s: no valid toolpath" % (s["name"], o["name"])
                  for s in doc.cam if s["name"] in setup_names for o in s["ops"]
                  if not o["suppressed"] and not o.get("has_toolpath")]
        return errors, []

    def cam_post(self, doc, setup_name, program_number, out_dir, post_path):
        s = [x for x in doc.cam if x["name"] == setup_name][0]
        active = [o["name"].strip() for o in s["ops"] if not o["suppressed"]]
        ws = re.search(r"G5[4-9]", setup_name.upper())
        lines = ["%", "O%d" % program_number, "(MACHINE)", "(  VENDOR ANDERSON)",
                 "N10 G90 G94 G17 G49", "N15 G20", ""]
        n = 20
        for op in active:
            lines += ["(%s)" % op.upper(), "N%d %s" % (n, ws.group(0) if ws else "G54"),
                      "N%d G1 X1. Y1." % (n + 5)]
            n += 10
        lines += ["N%d G28 G91 Z0." % n, "N%d M30" % (n + 5), "%"]
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "%d.nc" % program_number)
        with open(path, "wb") as fh:
            fh.write((CRLF.join(lines) + CRLF).encode("ascii"))
        self.log.append(("post", setup_name))
        return path

    # health
    def process_health(self):
        n = self.document_count()
        return {"threads": 300 + 40 * n, "working_set_mb": 900.0 + 150 * n, "documents": n}

    def hub_list(self, kind, id=None):
        return []
