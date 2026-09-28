"""Ops implementation backed by the real Fusion API.

Only ever called on Fusion's UI thread (from the engine tick or a request the
engine services), never from a background thread.
"""

import os
import time

import adsk.core
import adsk.drawing
import adsk.fusion

from . import winproc
from .ops_base import Ops, OpsError
from .params import ModelParam


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


class FusionOps(Ops):
    def __init__(self, app, get_config):
        self.app = app
        self.get_config = get_config
        self._foreign = []      # documents open before we started: never touched
        self._ours = []         # documents we opened

    # ------------------------------------------------------------------
    # session
    # ------------------------------------------------------------------
    def begin_session(self):
        self._foreign = [d for d in self._all_docs()]

    def ready(self):
        app = self.app
        if not _safe(lambda: app.isStartupComplete, True):
            return False, "Fusion is still starting up"
        if _safe(lambda: app.isOffLine, False):
            return False, "Fusion is offline"
        data = _safe(lambda: app.data)
        hub = _safe(lambda: data.activeHub) if data is not None else None
        if hub is None:
            return False, "not signed in to a hub yet"
        wanted = (self.get_config() or {}).get("hub", {}).get("name")
        if wanted and _safe(lambda: hub.name) != wanted:
            for h in _safe(lambda: list(data.dataHubs), []) or []:
                if _safe(lambda: h.name) == wanted:
                    return True, ""
            return False, "hub %s not visible yet" % wanted
        return True, ""

    def _all_docs(self):
        docs = self.app.documents
        out = []
        for i in range(docs.count):
            d = _safe(lambda: docs.item(i))
            if d is not None:
                out.append(d)
        return out

    @staticmethod
    def _contains(seq, doc):
        for d in seq:
            try:
                if d == doc:
                    return True
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------
    # files
    # ------------------------------------------------------------------
    def resolve_file(self, ref):
        fid = (ref or {}).get("id")
        if not fid:
            raise OpsError("no file id configured")
        # Always look the file up again: a cached DataFile can keep reporting
        # an old "latest" version after we saved a new one.
        f = _safe(lambda: self.app.data.findFileById(fid))
        if f is None and ref.get("folder_id") and ref.get("name"):
            folder = _safe(lambda: self.app.data.findFolderById(ref["folder_id"]))
            if folder is not None:
                for df in folder.dataFiles:
                    if df.name == ref["name"]:
                        f = df
                        break
        if f is None:
            raise OpsError("cloud file not found or not accessible: %s (%s)"
                           % (ref.get("name") or "?", fid))
        latest = _safe(lambda: f.latestVersion)
        return latest or f

    def file_version(self, file):
        return file.versionNumber

    # ------------------------------------------------------------------
    # documents
    # ------------------------------------------------------------------
    def find_open(self, file):
        for d in self._all_docs():
            try:
                if not d.isVisible or self._contains(self._foreign, d):
                    continue          # hidden copy, or someone else's open document
                df = d.dataFile
                if df is not None and df.id == file.id:
                    if df.versionNumber < _safe(lambda: file.latestVersionNumber, df.versionNumber):
                        continue          # stale copy; the GC will retire it
                    if not self._contains(self._ours, d):
                        self._ours.append(d)
                    return d
            except Exception:
                continue
        return None

    def open_document(self, file, visible=True):
        doc = self.app.documents.open(file, visible)
        if doc is None:
            raise OpsError("documents.open() returned nothing for %s" % file.name)
        self._ours.append(doc)
        return doc

    def doc_alive(self, doc):
        return bool(_safe(lambda: doc.isValid, False))

    def doc_label(self, doc):
        name = _safe(lambda: doc.name, "?")
        if not _safe(lambda: doc.isVisible, True):
            name += " (hidden)"
        return name

    def doc_is_modified(self, doc):
        return bool(doc.isModified)

    def doc_version(self, doc):
        return _safe(lambda: doc.dataFile.versionNumber)

    def activate(self, doc):
        if _safe(lambda: doc.isVisible, False) and not _safe(lambda: doc.isActive, False):
            doc.activate()

    def close_document(self, doc):
        return doc.close(False)

    def _configured_ids(self):
        ids = set()
        for r in (self.get_config() or {}).get("rules", []):
            for k in ("model", "drawing"):
                fid = (r.get(k) or {}).get("id")
                if fid:
                    ids.add(fid)
        return ids

    def managed_documents(self, keep=()):
        self._ours = [d for d in self._ours if self.doc_alive(d)]
        configured = self._configured_ids()
        out = []
        for d in self._all_docs():
            if self._contains(keep, d) or self._contains(self._foreign, d):
                continue
            ours = self._contains(self._ours, d)
            hidden = not _safe(lambda: d.isVisible, True)
            lineage = _safe(lambda: d.dataFile.id)
            if ours or hidden or (lineage and lineage in configured):
                if _safe(lambda: d.isModified, False) and not ours and not hidden:
                    continue      # somebody's unsaved work: leave it alone
                out.append(d)
        return out

    def document_count(self):
        return self.app.documents.count

    # ------------------------------------------------------------------
    # parameters
    # ------------------------------------------------------------------
    def _design(self, doc):
        prod = doc.products.itemByProductType("DesignProductType")
        design = adsk.fusion.Design.cast(prod)
        if design is None:
            raise OpsError("%s is not a Fusion design" % doc.name)
        return design

    @staticmethod
    def _is_text(p):
        vt = _safe(lambda: p.valueType)
        if vt is not None:
            return vt == adsk.fusion.ParameterValueTypes.TextParameterValueType
        return _safe(lambda: p.unit, "") == "Text"

    def _param_map(self, design):
        out = {}
        for p in design.userParameters:
            out[p.name] = p
        for p in design.allParameters:
            if p.name and p.name not in out:
                out[p.name] = p
        return out

    def read_params(self, doc):
        design = self._design(doc)
        out = {}
        for name, p in self._param_map(design).items():
            text = self._is_text(p)
            out[name] = ModelParam(name, _safe(lambda: p.unit, ""), text,
                                   _safe(lambda: p.expression, ""),
                                   _safe(lambda: p.textValue if text else p.value))
        return out

    def apply_params(self, doc, assignments):
        design = self._design(doc)
        pmap = self._param_map(design)
        um = design.unitsManager
        results, targets, values = [], [], []
        for name, expr in assignments:
            p = pmap.get(name)
            if p is None:
                results.append({"name": name, "ok": False, "expression": expr, "message": "not found"})
                continue
            results.append({"name": name, "ok": None, "expression": expr, "before": p.expression})
            targets.append(p)
            values.append(adsk.core.ValueInput.createByString(expr))

        batch_error = None
        if targets:
            try:
                ok = design.modifyParameters(targets, values)
                if not ok:
                    batch_error = "modifyParameters returned False"
            except Exception as e:
                batch_error = str(e)
        if batch_error:
            # find the culprit(s) one at a time
            for r in results:
                if r["ok"] is None:
                    try:
                        pmap[r["name"]].expression = r["expression"]
                    except Exception as e:
                        r["ok"], r["message"] = False, str(e)

        for r in results:
            if r["ok"] is False:
                continue
            p = pmap[r["name"]]
            r["after"] = _safe(lambda: p.expression)
            try:
                if self._is_text(p):
                    want = r["expression"].strip("'")
                    r["ok"] = p.textValue == want
                else:
                    want = um.evaluateExpression(r["expression"], p.unit or "")
                    got = p.value
                    r["ok"] = abs(got - want) <= 1e-9 * max(1.0, abs(want))
                if not r["ok"]:
                    r["message"] = "value did not change to %s (now %s)" % (r["expression"], r["after"])
            except Exception as e:
                r["ok"], r["message"] = False, "verify failed: %s" % e
        return results

    _LENGTH_UNITS = ("in", "mm", "cm", "m", "ft")

    def read_values(self, doc):
        design = self._design(doc)
        um = design.unitsManager
        out = {}
        for name, p in self._param_map(design).items():
            try:
                if self._is_text(p):
                    out[name] = p.textValue
                    continue
                v = p.value
                if (p.unit or "") in self._LENGTH_UNITS:
                    v = um.convert(v, "cm", "in")     # internal length unit is cm
                out[name] = v
            except Exception:
                continue
        return out

    # ------------------------------------------------------------------
    # manufacture (CAM)
    # ------------------------------------------------------------------
    @staticmethod
    def _cam(doc):
        import adsk.cam
        prod = _safe(lambda: doc.products.itemByProductType("CAMProductType"))
        return adsk.cam.CAM.cast(prod) if prod is not None else None

    def cam_state(self, doc):
        cam = self._cam(doc)
        if cam is None:
            return None
        out = []
        for s in cam.setups:
            out.append({"name": s.name, "suppressed": bool(s.isSuppressed),
                        "ops": [{"name": o.name, "suppressed": bool(o.isSuppressed),
                                 "has_toolpath": bool(_safe(lambda: o.hasToolpath, False)),
                                 "valid": bool(_safe(lambda: o.isToolpathValid, False))}
                                for o in s.allOperations]})
        return out

    def activate_workspace(self, kind):
        ws_id = {"cam": "CAMEnvironment", "design": "FusionSolidEnvironment"}[kind]
        ws = self.app.userInterface.workspaces.itemById(ws_id)
        if ws is None:
            raise OpsError("workspace %s not available" % ws_id)
        if not _safe(lambda: ws.isActive, False):
            ws.activate()

    def _setups_by_name(self, doc):
        cam = self._cam(doc)
        if cam is None:
            raise OpsError("%s has no manufacturing (CAM) data" % doc.name)
        return cam, {s.name: s for s in cam.setups}

    def cam_prepare(self, doc, actions):
        cam, setups = self._setups_by_name(doc)
        changes = []
        for name in actions.get("unsuppress_setups", []):
            s = setups.get(name)
            if s is not None and s.isSuppressed:
                s.isSuppressed = False
                changes.append("unsuppressed setup %s" % name)
        for sname, op_states in (actions.get("ops") or {}).items():
            s = setups.get(sname)
            if s is None:
                continue
            for o in s.allOperations:
                if o.name in op_states:
                    want_suppressed = not op_states[o.name]
                    if bool(o.isSuppressed) != want_suppressed:
                        o.isSuppressed = want_suppressed
                        changes.append("%s %s / %s" % ("suppressed" if want_suppressed else "activated",
                                                      sname, o.name.strip()))
        return changes

    def cam_generate(self, doc, setup_names):
        cam, setups = self._setups_by_name(doc)
        coll = adsk.core.ObjectCollection.create()
        for name in setup_names:
            s = setups.get(name)
            if s is None:
                raise OpsError("setup %s not found" % name)
            for o in s.allOperations:
                if not o.isSuppressed:
                    coll.add(o)
        if coll.count == 0:
            return None
        return {"future": cam.generateToolpath(coll), "count": coll.count}

    def cam_generate_done(self, token):
        return token is None or bool(token["future"].isGenerationCompleted)

    def cam_problems(self, doc, setup_names):
        cam, setups = self._setups_by_name(doc)
        errors, warnings = [], []
        for name in setup_names:
            s = setups.get(name)
            if s is None:
                errors.append("setup %s not found" % name)
                continue
            for o in s.allOperations:
                if o.isSuppressed:
                    continue
                label = "%s / %s" % (name, o.name.strip())
                if _safe(lambda: o.hasError, False):
                    errors.append("%s: toolpath generation error" % label)
                elif not _safe(lambda: o.hasToolpath, False) or not _safe(lambda: o.isToolpathValid, False):
                    errors.append("%s: no valid toolpath" % label)
                elif _safe(lambda: o.hasWarning, False):
                    warnings.append("%s: toolpath generated with warnings" % label)
        return errors, warnings

    def cam_post(self, doc, setup_name, program_number, out_dir, post_path):
        import adsk.cam
        cam, setups = self._setups_by_name(doc)
        s = setups.get(setup_name)
        if s is None:
            raise OpsError("setup %s not found" % setup_name)
        if not os.path.isfile(post_path):
            raise OpsError("post processor not found: %s" % post_path)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "%d.nc" % int(program_number))
        if os.path.exists(path):
            os.remove(path)
        pi = adsk.cam.PostProcessInput.create(str(int(program_number)), post_path, out_dir,
                                              adsk.cam.PostOutputUnitOptions.DocumentUnitsOutput)
        pi.isOpenInEditor = False
        if not cam.postProcess(s, pi):
            raise OpsError("post processing %s returned False" % setup_name)
        if not os.path.isfile(path):
            raise OpsError("post processing %s produced no file (%s)" % (setup_name, path))
        return path

    def model_warnings(self, doc):
        design = self._design(doc)
        out = []
        if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
            return out
        tl = design.timeline
        for i in range(tl.count):
            item = _safe(lambda: tl.item(i))
            hs = _safe(lambda: item.healthState) if item is not None else None
            if hs in (adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState,
                      adsk.fusion.FeatureHealthStates.WarningFeatureHealthState):
                kind = "error" if hs == adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState else "warning"
                out.append("timeline %s at %s: %s" % (kind, _safe(lambda: item.name, "?"),
                                                    _safe(lambda: item.errorOrWarningMessage, "")))
                if len(out) >= 20:
                    break
        return out

    # ------------------------------------------------------------------
    # saving
    # ------------------------------------------------------------------
    def start_save(self, doc, message):
        if not doc.isModified:
            return None
        prev = _safe(lambda: doc.dataFile.versionNumber, 0) or 0
        if not doc.save(message):
            raise OpsError("save() returned False for %s" % doc.name)
        return {"prev": prev, "started": time.time()}

    def save_complete(self, doc, token):
        if doc.isModified:
            return False
        df = _safe(lambda: doc.dataFile)
        if df is None:
            return False
        ver = _safe(lambda: df.versionNumber, 0) or 0
        if ver <= token["prev"]:
            return False
        return bool(_safe(lambda: df.isComplete, True))

    # ------------------------------------------------------------------
    # drawings
    # ------------------------------------------------------------------
    def _refs(self, drawing):
        refs = drawing.documentReferences
        return [refs.item(i) for i in range(refs.count)]

    @staticmethod
    def _ref_info(ref):
        return {
            "name": _safe(lambda: ref.dataFile.name),
            "id": _safe(lambda: ref.dataFile.id),
            "version": _safe(lambda: ref.version),
            "file_version": _safe(lambda: ref.dataFile.versionNumber),
            "latest": _safe(lambda: ref.dataFile.latestVersionNumber),
            "out_of_date": _safe(lambda: ref.isOutOfDate),
        }

    def _model_ref(self, drawing, model_doc):
        lineage = model_doc.dataFile.id
        for ref in self._refs(drawing):
            if _safe(lambda: ref.dataFile.id) == lineage:
                return ref
        return None

    def update_drawing(self, drawing, model_doc):
        before = [self._ref_info(r) for r in self._refs(drawing)]
        if self._model_ref(drawing, model_doc) is None:
            raise OpsError("drawing %s does not reference model %s (references: %s)" % (
                drawing.name, model_doc.name, ", ".join(str(b["name"]) for b in before) or "none"))
        # The quick-access-toolbar "update / get latest" button.  Fusion raises
        # "Design xref is not out of date" when there is nothing to update
        # (model unchanged, or the drawing already bound to the open model).
        stale = any(b.get("out_of_date") for b in before) or not _safe(lambda: drawing.isUpToDate, True)
        pressed = False
        if stale:
            try:
                drawing.updateAllReferences()
                pressed = True
            except RuntimeError as e:
                if "not out of date" not in str(e):
                    raise
        for ref in self._refs(drawing):
            if _safe(lambda: ref.isOutOfDate, False):
                _safe(lambda: ref.getLatestVersion())
        after = [self._ref_info(r) for r in self._refs(drawing)]
        return {"before": before, "after": after, "updated": pressed}

    def drawing_up_to_date(self, drawing, model_doc):
        target = _safe(lambda: model_doc.dataFile.versionNumber)
        ref = self._model_ref(drawing, model_doc)
        if ref is None:
            return False
        if _safe(lambda: ref.isOutOfDate, False):
            _safe(lambda: ref.getLatestVersion())
            return False
        ver = _safe(lambda: ref.dataFile.versionNumber)
        if target is not None and ver is not None and ver < target:
            # the hub may not have reported the new version yet; nudge and poll again
            _safe(lambda: ref.getLatestVersion())
            return False
        return bool(_safe(lambda: drawing.isUpToDate, True))

    def export_pdf(self, drawing, path):
        dd = adsk.drawing.DrawingDocument.cast(drawing)
        if dd is None:
            raise OpsError("%s is not a drawing document" % drawing.name)
        em = dd.drawing.exportManager
        opts = em.createPDFExportOptions(path)
        if opts is None:
            raise OpsError("could not create PDF export options")
        opts.sheetsToExport = adsk.drawing.PDFSheetsExport.AllPDFSheetsExport
        opts.openPDF = False
        opts.useLineWeights = True
        if not em.execute(opts):
            raise OpsError("PDF export returned False")

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------
    def process_health(self):
        info = winproc.own_process_info() or {}
        return {"threads": info.get("threads"), "working_set_mb": info.get("working_set_mb"),
                "handles": info.get("handles"), "documents": self.document_count()}

    # ------------------------------------------------------------------
    # hub browsing for the dashboard
    # ------------------------------------------------------------------
    def hub_list(self, kind, id=None):
        data = self.app.data
        if kind == "hubs":
            active = _safe(lambda: data.activeHub.id)
            return [{"id": h.id, "name": h.name, "active": h.id == active,
                     "type": "personal" if h.hubType == adsk.core.HubTypes.PersonalHubType else "team"}
                    for h in data.dataHubs]
        if kind == "projects":
            hub = None
            for h in data.dataHubs:
                if h.id == id or h.name == id:
                    hub = h
                    break
            if hub is None:
                raise OpsError("hub not found: %s" % id)
            return [{"id": p.id, "name": p.name, "root_folder_id": p.rootFolder.id}
                    for p in hub.dataProjects]
        if kind == "folder":
            folder = data.findFolderById(id)
            if folder is None:
                raise OpsError("folder not found: %s" % id)
            files = []
            for f in folder.dataFiles:
                files.append({"id": f.id, "name": f.name, "ext": _safe(lambda: f.fileExtension, ""),
                              "version": _safe(lambda: f.versionNumber),
                              "folder_id": folder.id})
            return {"id": folder.id, "name": folder.name,
                    "parent_id": _safe(lambda: folder.parentFolder.id),
                    "project": _safe(lambda: folder.parentProject.name),
                    "folders": [{"id": s.id, "name": s.name} for s in folder.dataFolders],
                    "files": files}
        raise OpsError("unknown hub listing %r" % kind)

    def inspect_model(self, ref):
        """Open a model (invisibly if not already open) and list its parameters."""
        f = self.resolve_file(ref)
        doc = self.find_open(f)
        opened = False
        if doc is None:
            doc = self.app.documents.open(f, False)
            opened = True
            if doc is None:
                raise OpsError("could not open %s" % f.name)
        try:
            params = self.read_params(doc)
            user = set(p.name for p in self._design(doc).userParameters)
            return {"name": f.name, "version": f.versionNumber,
                    "parameters": [dict(p.to_dict(), user=(n in user))
                                   for n, p in sorted(params.items())]}
        finally:
            if opened:
                _safe(lambda: doc.close(False))
