"""Order-processing engine.

The engine is a cooperative state machine driven by ``tick()``.  Inside Fusion,
``tick()`` is called from a CustomEvent handler on the UI thread a few times per
second; each call advances the current order by at most one step and returns,
so Fusion keeps processing its own events (cloud uploads, drawing regeneration,
UI) between steps.  We never call ``adsk.doEvents()``.

Order work is written as generators.  They yield:

* ``None``        - continue on the next tick
* ``Sleep(s)``    - resume after ``s`` seconds
* ``WaitFor(...)``- resume when a condition becomes true (or time out)

All Fusion access goes through an ``Ops`` object (see ``ops_base``).
"""

import collections
import gc
import os
import queue
import threading
import time
import traceback

from . import naming as naming_mod
from . import rules as rules_mod
from .dropbox import Dropbox
from .orders import OrderError, load_order_file, safe_name
from .orders import ParamValue
from .params import plan_assignments

MAX_ATTEMPTS = 2   # a component that was in progress during 2 crashes/restarts fails
STALE_REOPENS = 4  # reopen attempts when the hub returns an older model version
STALE_BACKOFF_S = 5.0


class Sleep(object):
    __slots__ = ("seconds",)

    def __init__(self, seconds):
        self.seconds = float(seconds)


class WaitFor(object):
    __slots__ = ("cond", "timeout", "what", "raise_on_timeout")

    def __init__(self, cond, timeout, what="condition", raise_on_timeout=True):
        self.cond = cond
        self.timeout = float(timeout)
        self.what = what
        self.raise_on_timeout = raise_on_timeout


class StepTimeout(Exception):
    pass


class ComponentError(Exception):
    pass


class Task(object):
    """Runs one generator, one resume per ``advance()``."""

    def __init__(self, name, gen, clock):
        self.name = name
        self._gen = gen
        self._clock = clock
        self._wait = None
        self._until = 0.0
        self.done = False
        self.error = None
        self.error_tb = None
        self.result = None

    def advance(self):
        if self.done:
            return
        now = self._clock()
        w = self._wait
        if w is None:
            return self._resume(None, None)
        if isinstance(w, Sleep):
            if now < self._until:
                return
            return self._resume(None, None)
        try:
            ok = w.cond()
        except Exception as e:   # condition blew up: raise it inside the generator
            return self._resume(None, e)
        if ok:
            return self._resume(ok, None)
        if now >= self._until:
            if w.raise_on_timeout:
                return self._resume(None, StepTimeout(
                    "timed out after %.0fs waiting for %s" % (w.timeout, w.what)))
            return self._resume(False, None)

    @property
    def waiting_for(self):
        w = self._wait
        if isinstance(w, WaitFor):
            return w.what
        if isinstance(w, Sleep):
            return "sleep"
        return None

    def _resume(self, value, exc):
        try:
            if exc is not None:
                y = self._gen.throw(exc)
            else:
                y = self._gen.send(value)
        except StopIteration as si:
            self.done, self.result, self._wait = True, si.value, None
            return
        except Exception as e:
            self.done, self.error, self._wait = True, e, None
            self.error_tb = traceback.format_exc()
            return
        self._wait = y
        if isinstance(y, Sleep):
            self._until = self._clock() + y.seconds
        elif isinstance(y, WaitFor):
            self._until = self._clock() + y.timeout
        elif y is not None:
            self._wait = None

    def close(self):
        try:
            self._gen.close()
        except Exception:
            pass
        self.done = True


class Engine(object):
    def __init__(self, ops, paths, state, load_config, clock=time.monotonic, wall=time.time):
        self.ops = ops
        self.paths = paths
        self.state = state
        self.load_config = load_config
        self.clock = clock
        self.wall = wall
        self.dropbox = Dropbox(paths, clock=wall)
        self.cfg = load_config()
        self._requests = queue.Queue()
        self._task = None
        self._model_cache = collections.OrderedDict()   # model lineage id -> doc
        self._opened_models = {}                       # id -> doc for cache size 0
        self._saved_versions = {}                      # model id -> last version we saved
        self._since_recycle = 0
        self._inbox_hint = False
        self._cancel_order = False
        self._tick_lock = threading.Lock()
        self.tick_started_at = None                    # wall time while a tick runs
        self.paused = not self.cfg.get("processing", {}).get("autostart", True)
        self.pause_reason = "autostart disabled" if self.paused else ""
        self.restart_requested = False
        self.restart_reason = ""
        self.counters = {"orders_done": 0, "orders_failed": 0,
                         "components_done": 0, "components_failed": 0}
        self._refresh_base = 0
        self._publish(state="paused" if self.paused else "idle", order=None, component=None)

    # ------------------------------------------------------------------
    # driving
    # ------------------------------------------------------------------
    def set_inbox_hint(self, has_files):
        self._inbox_hint = bool(has_files)

    def has_work(self):
        if not self._requests.empty() or self._task is not None:
            return True
        if self.paused or self.restart_requested:
            return False
        return self._inbox_hint or bool(self.dropbox.processing_files())

    def tick(self):
        if not self._tick_lock.acquire(False):
            return          # re-entrant call (should not happen; we never pump events)
        self.tick_started_at = self.wall()
        try:
            self._serve_requests()
            if self._task is not None:
                self._task.advance()
                if self._task.done:
                    self._finish_task()
                return
            if self.paused or self.restart_requested:
                return
            self._start_next_order()
        finally:
            self.tick_started_at = None
            self._tick_lock.release()

    def busy(self):
        return self._task is not None

    # ------------------------------------------------------------------
    # main-thread requests (hub browsing, dev exec) from other threads
    # ------------------------------------------------------------------
    def submit(self, fn, timeout=120.0):
        box = {"done": threading.Event(), "result": None, "error": None}
        self._requests.put((fn, box))
        if not box["done"].wait(timeout):
            raise TimeoutError("Fusion did not service the request within %.0fs "
                               "(it may be busy in a long step)" % timeout)
        if box["error"] is not None:
            raise box["error"]
        return box["result"]

    def _serve_requests(self, budget=5):
        for _ in range(budget):
            try:
                fn, box = self._requests.get_nowait()
            except queue.Empty:
                return
            try:
                box["result"] = fn()
            except Exception as e:
                box["error"] = e
            finally:
                box["done"].set()

    # ------------------------------------------------------------------
    # controls
    # ------------------------------------------------------------------
    def pause(self, reason="paused from dashboard"):
        self.paused = True
        self.pause_reason = reason
        self.state.event("info", "Paused: %s" % reason)
        self._publish(state=self._state_name())

    def resume(self):
        self.paused = False
        self.pause_reason = ""
        self.restart_requested = False
        self.restart_reason = ""
        # a declined scheduled refresh starts a new interval instead of re-asking
        self._refresh_base = self.counters["components_done"] + self.counters["components_failed"]
        self.state.event("info", "Resumed")
        self._publish(state=self._state_name())

    def cancel_order(self):
        if self._task is None:
            return False
        self._cancel_order = True
        self.state.event("warn", "Cancel requested: current order stops after this component")
        return True

    def reload_config(self):
        self.cfg = self.load_config()
        self.paths.apply_location(self.cfg)
        self.state.event("info", "Configuration reloaded")

    def shutdown(self):
        """Add-in is stopping: abandon the task (order stays in processing/)."""
        if self._task is not None:
            self._task.close()
            self._task = None
        for doc in list(self._model_cache.values()) + list(self._opened_models.values()):
            try:
                if self.ops.doc_alive(doc):
                    self.ops.close_document(doc)
            except Exception:
                pass
        self._model_cache.clear()
        self._opened_models.clear()
        self._publish(state="stopped")

    def request_garbage_collection(self):
        """Dashboard 'Collect garbage' button (only when idle)."""
        if self._task is not None:
            raise RuntimeError("busy processing; garbage collection runs after every component anyway")
        self._task = Task("gc", self._gc_task(full=True), self.clock)
        return True

    def _gc_task(self, full):
        closed = yield from self._collect_garbage(full=full)
        return {"closed": closed}

    # ------------------------------------------------------------------
    # orders
    # ------------------------------------------------------------------
    def _start_next_order(self):
        resumed = self.dropbox.processing_files()
        if resumed:
            path = os.path.join(self.paths.processing, resumed[0])
            self.state.event("info", "Resuming order %s" % resumed[0])
        else:
            names = self.dropbox.inbox_files()
            if not names:
                self._inbox_hint = False
                return
            path = self.dropbox.claim(names[0])
            if not path:
                return
            self.state.event("info", "Picked up order %s" % names[0])
        self._task = Task("order", self._order_gen(path), self.clock)
        self._publish(state="processing")

    def _finish_task(self):
        task, self._task = self._task, None
        if task.error is not None:
            self.state.event("error", "Internal error in %s task: %s" % (task.name, task.error),
                             traceback=task.error_tb)
            if task.name == "order":
                # Don't spin on a broken order; it stays in processing/ for inspection.
                self.pause("internal error: %s" % task.error)
        self._publish(state=self._state_name(), component=None)

    def _state_name(self):
        if self.restart_requested:
            return "waiting_restart"
        if self.paused:
            return "paused"
        return "processing" if self._task is not None else "idle"

    def _plan(self, order, cfg):
        fam = rules_mod.family_from_order_id(order.order_id)
        rules = cfg.get("rules", [])
        planned = [(c, rules_mod.resolve(rules, c.type, c.series_id, fam)) for c in order.components]
        if cfg.get("orders", {}).get("group_by_model", True):
            first_seen = {}
            for i, (c, r) in enumerate(planned):
                key = (r or {}).get("model", {}).get("id") or ("none", c.id)
                first_seen.setdefault(key, i)
            planned.sort(key=lambda cr: (
                first_seen[(cr[1] or {}).get("model", {}).get("id") or ("none", cr[0].id)],
                cr[0].index))
        return planned

    def _order_gen(self, path):
        cfg = self.cfg = self.load_config()
        self.paths.apply_location(cfg)
        ocfg = cfg.get("orders", {})
        name = os.path.basename(path)
        try:
            order = load_order_file(path, type_by_key=ocfg.get("type_by_key"),
                                    type_by_prefix=ocfg.get("type_by_prefix"))
        except (OrderError, OSError) as e:
            self.dropbox.reject(path, str(e))
            self.counters["orders_failed"] += 1
            self.state.event("error", "Rejected order %s: %s" % (name, e), order=name)
            self._publish(last_order={"name": name, "status": "rejected", "error": str(e),
                                      "finished_at": self.wall()})
            return

        ckpt = self.dropbox.load_checkpoint(path) or {}
        results = ckpt.get("components", {})
        started_at = ckpt.get("started_at") or self.wall()
        plan = self._plan(order, cfg)
        namer = naming_mod.Namer(cfg.get("naming"), order.order_id, name, ts=started_at)
        if ckpt.get("output_folder"):
            out_dir = os.path.join(self.paths.output, ckpt["output_folder"])   # resumed run
        else:
            out_dir = naming_mod.unique_dir(self.paths.output, namer.order_folder())
        os.makedirs(out_dir, exist_ok=True)
        self._order_ctx = {"namer": namer, "out_dir": out_dir, "counter": None}
        self._order_ctx.update(self._load_profiles(cfg))
        self._cancel_order = False

        def publish_order(current=None):
            self._publish(order={
                "name": name, "order_id": order.order_id, "started_at": started_at,
                "total": len(plan),
                "done": sum(1 for r in results.values() if r.get("status") == "done"),
                "failed": sum(1 for r in results.values() if r.get("status") in ("failed", "cancelled")),
                "current": current,
                "components": [{"id": c.id, "type": c.type,
                                "status": results.get(c.id, {}).get("status", "pending")}
                               for c, _ in plan]})

        def checkpoint():
            self.dropbox.save_checkpoint(path, {"order_id": order.order_id, "started_at": started_at,
                                                "output_folder": os.path.basename(out_dir),
                                                "components": results})

        self.state.event("info", "Order %s: %d component(s) %s" % (
            order.order_id, len(plan), order.counts()), order=name)
        publish_order()

        for comp, rule in plan:
            prev = results.get(comp.id, {})
            if prev.get("status") in ("done", "failed", "cancelled"):
                continue
            # component boundary: honour pause / restart / cancel
            while (self.paused or self.restart_requested) and not self._cancel_order:
                yield Sleep(1.0)
            yield from self._wait_until_ready()
            if self._cancel_order:
                results[comp.id] = self._blank_result(comp, rule, status="cancelled",
                                                      error="order cancelled")
                continue
            attempts = int(prev.get("attempts", 0)) + 1
            if prev.get("status") == "in_progress" and attempts > MAX_ATTEMPTS:
                res = self._blank_result(comp, rule, status="failed",
                                         error="Fusion stopped during this component %d times; giving up"
                                               % (attempts - 1))
                res["attempts"] = attempts - 1
                results[comp.id] = res
                self.counters["components_failed"] += 1
                self.state.event("error", "%s %s: %s" % (order.order_id, comp.id, res["error"]))
                checkpoint()
                continue
            results[comp.id] = {"status": "in_progress", "attempts": attempts}
            checkpoint()
            publish_order(current=comp.id)

            res = yield from self._run_component(order, comp, rule, cfg, out_dir)
            res["attempts"] = attempts
            results[comp.id] = res
            checkpoint()
            if res["status"] == "done":
                self.counters["components_done"] += 1
            else:
                self.counters["components_failed"] += 1
            publish_order()
            self._check_health(cfg)

        # finalize
        comps = [results.get(c.id) or self._blank_result(c, r, status="skipped") for c, r in plan]
        n_done = sum(1 for r in comps if r["status"] == "done")
        n_fail = len(comps) - n_done
        n_warn = sum(len(r.get("warnings") or []) for r in comps)
        status = "ok" if n_fail == 0 else ("failed" if n_done == 0 else "partial")
        finished = self.wall()
        report = {
            "order_id": order.order_id, "source_file": name,
            "started_at": started_at, "finished_at": finished,
            "duration_s": round(finished - started_at, 1),
            "summary": {"status": status, "total": len(comps), "done": n_done,
                        "failed": n_fail, "warnings": n_warn, "order_id": order.order_id,
                        "output_dir": os.path.relpath(out_dir, self.paths.output)},
            "components": comps,
        }
        report["outputs"] = self._finalize_outputs(order, plan, results, namer, out_dir, cfg)
        from .jsonio import write_json_atomic
        write_json_atomic(os.path.join(out_dir, namer.report()), report)
        final_path = self.dropbox.finish(path, status == "ok", report)
        if status == "ok":
            self.counters["orders_done"] += 1
        else:
            self.counters["orders_failed"] += 1
        level = "info" if status == "ok" else "error"
        self.state.event(level, "Order %s finished: %s (%d/%d components, %.0fs)" % (
            order.order_id, status, n_done, len(comps), finished - started_at),
            order=name, folder=os.path.basename(os.path.dirname(final_path)))
        self._publish(order=None, last_order={
            "name": os.path.basename(final_path), "order_id": order.order_id, "status": status,
            "done": n_done, "total": len(comps), "finished_at": finished,
            "folder": "done" if status == "ok" else "failed"})

    # ------------------------------------------------------------------
    # one component
    # ------------------------------------------------------------------
    def _blank_result(self, comp, rule, status="pending", error=None):
        return {
            "id": comp.id, "type": comp.type, "series_id": comp.series_id,
            "rule_id": (rule or {}).get("id"),
            "model": ((rule or {}).get("model") or {}).get("name"),
            "drawing": ((rule or {}).get("drawing") or {}).get("name"),
            "status": status, "error": error, "warnings": [], "parameters": [],
            "unknown_parameters": [], "model_version": None, "pdf": None, "pdf_bytes": None,
            "steps": [], "started_at": None, "finished_at": None, "duration_s": None,
        }

    def _step(self, res, name):
        now = self.wall()
        if res["steps"]:
            last = res["steps"][-1]
            last["duration_s"] = round(now - last["started_at"], 2)
        res["steps"].append({"name": name, "started_at": now})
        self._publish(component={"id": res["id"], "type": res["type"], "step": name,
                                 "step_started_at": now, "started_at": res["started_at"],
                                 "series_id": res["series_id"], "model": res["model"],
                                 "drawing": res["drawing"], "waiting_for": None,
                                 "steps": [{"name": s["name"], "duration_s": s.get("duration_s")}
                                           for s in res["steps"]]})
        self.state.event("debug", "%s: %s" % (res["id"], name))

    def _run_component(self, order, comp, rule, cfg, out_dir):
        res = self._blank_result(comp, rule, status="in_progress")
        res["started_at"] = self.wall()
        ctx = {"drawing": None, "model": None}
        self.state.event("info", "%s %s (%s, series %s) -> %s" % (
            order.order_id, comp.id, comp.type, comp.series_id or "-",
            res["model"] or "NO RULE"), order=order.order_id, component=comp.id)
        try:
            yield from self._component_steps(order, comp, rule, cfg, out_dir, res, ctx)
            res["status"] = "done"
        except GeneratorExit:
            raise
        except Exception as e:
            res["status"] = "failed"
            res["error"] = str(e) or e.__class__.__name__
            if not isinstance(e, (ComponentError, StepTimeout)):
                res["traceback"] = traceback.format_exc()
            self.state.event("error", "%s %s failed: %s" % (order.order_id, comp.id, res["error"]),
                             component=comp.id)
        try:
            yield from self._after_component(res, ctx, cfg)
        except GeneratorExit:
            raise
        except Exception as e:
            res["warnings"].append("cleanup problem: %s" % e)
        now = self.wall()
        if res["steps"] and "duration_s" not in res["steps"][-1]:
            res["steps"][-1]["duration_s"] = round(now - res["steps"][-1]["started_at"], 2)
        res["finished_at"] = now
        res["duration_s"] = round(now - res["started_at"], 1)
        if res["status"] == "done":
            self.state.event("info", "%s %s done in %.0fs%s" % (
                order.order_id, comp.id, res["duration_s"],
                " (%d warning(s))" % len(res["warnings"]) if res["warnings"] else ""),
                component=comp.id, pdf=res["pdf"])
        return res

    def _wait(self, cond, timeout, what, res, raise_on_timeout=True):
        comp = self.state.get("component") or {}
        comp = dict(comp, waiting_for=what)
        self._publish(component=comp)
        return WaitFor(cond, timeout, what, raise_on_timeout)

    def _component_steps(self, order, comp, rule, cfg, out_dir, res, ctx):
        P = cfg.get("processing", {})
        ops = self.ops
        if rule is None:
            raise ComponentError("no series rule matches type=%s series_id=%s - add one in "
                                 "Series Configuration" % (comp.type, comp.series_id or "(none)"))
        model_ref = rule.get("model") or {}
        drawing_ref = rule.get("drawing") or {}

        # 1. model
        self._step(res, "open_model")
        model = yield from self._get_model(model_ref, P)
        ctx["model"] = model

        # 2. parameters
        self._step(res, "apply_parameters")
        model_params = ops.read_params(model)
        values = dict(comp.params)
        transformed = _apply_input_transforms(values, comp.type, cfg.get("orders", {}).get("input_transforms"))
        derived = cfg.get("orders", {}).get("derived_parameters") or {}
        for pname, template in derived.items():
            if pname not in values:
                try:
                    text = str(template).format(order_id=order.order_id, component_id=comp.id,
                                                type=comp.type, series_id=comp.series_id)
                except (KeyError, IndexError, ValueError) as e:
                    raise ComponentError("bad derived parameter template %s: %s" % (pname, e))
                values[pname] = ParamValue(text, "string")
        assignments, unknown, errors = plan_assignments(values, model_params, optional=tuple(derived) + tuple(transformed),
                                                    null_policy=P.get("null_policy", "zero"))
        nulls = [n for n, _, pv in assignments if pv.value is None]
        if nulls:
            res["warnings"].append("null value(s) written as 0/empty: %s" % ", ".join(nulls))
        res["unknown_parameters"] = unknown
        if errors:
            raise ComponentError("bad parameter values: " + "; ".join("%s: %s" % e for e in errors))
        if unknown:
            msg = "model %s has no parameter(s): %s" % (model_ref.get("name"), ", ".join(unknown))
            if P.get("unknown_parameter_policy") == "fail":
                raise ComponentError(msg)
            res["warnings"].append(msg)
        applied = ops.apply_params(model, [(n, e) for n, e, _ in assignments])
        res["parameters"] = applied
        bad = [a for a in applied if not a.get("ok")]
        if bad:
            raise ComponentError("%d parameter(s) failed: %s" % (
                len(bad), "; ".join("%s (%s)" % (a["name"], a.get("message")) for a in bad[:5])))
        yield None                   # let Fusion finish the recompute
        res["warnings"].extend(ops.model_warnings(model))

        # 3. save the model (new cloud version)
        if P.get("save_model", True):
            self._step(res, "save_model")
            want = self._saved_versions.get(model_ref.get("id"))
            have = ops.doc_version(model)
            if want and have and have < want:
                raise ComponentError("model is at v%s but v%s was already saved; refusing to save an "
                                     "out-of-date copy" % (have, want))
            token = ops.start_save(model, "NFE %s %s" % (order.order_id, comp.id))
            if token is not None:
                yield self._wait(lambda: ops.save_complete(model, token),
                                 P.get("save_timeout_s", 300), "model cloud save", res)
            else:
                res["warnings"].append("model unchanged; no new version saved")
        res["model_version"] = ops.doc_version(model)
        if res["model_version"]:
            self._saved_versions[model_ref.get("id")] = max(
                res["model_version"], self._saved_versions.get(model_ref.get("id"), 0))

        # 4. drilling + manufacture read the recomputed model values
        order_values = {k: pv.value for k, pv in values.items()}
        model_values = ops.read_values(model)
        self._drilling(order, comp, cfg, model_values, order_values, res)
        yield from self._manufacture(order, comp, cfg, model, model_values, order_values, res, ctx)

        # 5. drawing
        if not drawing_ref.get("id"):
            msg = "no drawing configured for rule %s" % rule.get("id")
            if P.get("missing_drawing_policy", "fail") == "fail":
                raise ComponentError(msg)
            res["warnings"].append(msg)
            return
        self._step(res, "open_drawing")
        dfile = yield from self._resolve(drawing_ref)
        drawing = ops.find_open(dfile) or ops.open_document(dfile, visible=P.get("open_visible", True))
        ctx["drawing"] = drawing
        yield Sleep(P.get("drawing_settle_s", 2.0))

        self._step(res, "update_drawing")
        ops.activate(drawing)
        info = ops.update_drawing(drawing, model)
        res["drawing_references"] = info
        yield self._wait(lambda: ops.drawing_up_to_date(drawing, model),
                         P.get("drawing_update_timeout_s", 180), "drawing to reference the new model version", res)
        yield Sleep(P.get("drawing_settle_s", 2.0))

        # 5. PDF
        self._step(res, "export_pdf")
        final = os.path.join(out_dir, self._order_ctx["namer"].drawing_pdf(comp.id, comp.type, comp.series_id))
        partial = os.path.join(out_dir, "_partial_" + os.path.basename(final))
        if os.path.exists(partial):
            os.remove(partial)
        ops.activate(drawing)
        ops.export_pdf(drawing, partial)
        yield self._wait(lambda: _pdf_ready(partial), P.get("export_timeout_s", 300), "PDF file", res)
        os.replace(partial, final)
        res["pdf"] = os.path.relpath(final, self.paths.output).replace("\\", "/")
        res["pdf_bytes"] = os.path.getsize(final)

        # 6. optionally keep the refreshed drawing in the hub
        if P.get("save_drawing", False):
            self._step(res, "save_drawing")
            token = ops.start_save(drawing, "NFE %s %s" % (order.order_id, comp.id))
            if token is not None:
                yield self._wait(lambda: ops.save_complete(drawing, token),
                                 P.get("save_timeout_s", 300), "drawing cloud save", res)

    def _get_model(self, ref, P):
        ops = self.ops
        key = ref.get("id")
        if not key:
            raise ComponentError("rule has no model selected")
        size = int(P.get("model_cache_size", 1))
        doc = self._model_cache.get(key)
        if doc is not None:
            if ops.doc_alive(doc) and not ops.doc_is_modified(doc):
                self._model_cache.move_to_end(key)
                ops.activate(doc)
                return doc
            self._model_cache.pop(key, None)
            yield from self._close_verified(doc, "stale model")
        # make room
        while self._model_cache and len(self._model_cache) >= max(1, size):
            _, old = self._model_cache.popitem(last=False)
            yield from self._close_verified(old, "evicted model")
        mfile = yield from self._resolve(ref)
        doc = ops.find_open(mfile)
        if doc is None or ops.doc_is_modified(doc):
            doc = ops.open_document(mfile, visible=P.get("open_visible", True))
        yield Sleep(0.5)
        # Stale version guard: right after a save the hub can still hand out
        # the previous version.  Editing and saving that copy can never
        # complete, so reopen until we get at least the version we saved.
        want = self._saved_versions.get(key)
        attempt = 0
        while want and (ops.doc_version(doc) or 0) < want:
            attempt += 1
            if attempt > STALE_REOPENS:
                raise ComponentError("the hub keeps returning %s v%s but v%s was saved; try again later"
                                     % (ref.get("name"), ops.doc_version(doc), want))
            self.state.event("warn", "Opened %s v%s but v%s was saved; reopening (%d/%d)" % (
                ref.get("name"), ops.doc_version(doc), want, attempt, STALE_REOPENS))
            yield from self._close_verified(doc, "out-of-date model")
            yield Sleep(STALE_BACKOFF_S * attempt)
            mfile = yield from self._resolve(ref)
            doc = ops.open_document(mfile, visible=P.get("open_visible", True))
            yield Sleep(0.5)
        if size > 0:
            self._model_cache[key] = doc
        else:
            self._opened_models[key] = doc
        return doc

    # ------------------------------------------------------------------
    # drilling (Gannomat) + manufacture (CAM / Anderson)
    # ------------------------------------------------------------------
    def _load_profiles(self, cfg):
        """Toolpath tables: config/toolpath_profiles.json, else the defaults."""
        from . import cam_rules
        from .cam_defaults import DEFAULT_PROFILES
        from .jsonio import read_json
        rel = (cfg.get("cam") or {}).get("profiles_file") or ""
        path = os.path.join(self.paths.root, rel) if rel else ""
        data = read_json(path, default=None) if path else None
        source = path if data is not None else "built-in defaults"
        profiles = data.get("profiles") if isinstance(data, dict) else (data if isinstance(data, list) else None)
        if profiles is None:
            profiles = DEFAULT_PROFILES
        errors = []
        for p in profiles:
            errors.extend("%s: %s" % (p.get("id"), e) for e in cam_rules.validate_profile(p))
        if errors:
            self.state.event("error", "Toolpath tables in %s are invalid: %s" % (source, "; ".join(errors[:5])))
        return {"profiles": profiles, "profile_errors": errors, "profiles_source": source}

    def _drilling(self, order, comp, cfg, model_values, order_values, res):
        from . import config as config_mod
        from . import drilling
        D = config_mod.deep_merge(drilling.DRILLING_DEFAULTS, cfg.get("drilling") or {})
        if not D.get("enabled", True) or comp.type not in (D.get("types") or []):
            return
        self._step(res, "drilling")
        paired = drilling.component_drilling(comp.type, comp.id, order.order_id, comp.series_id,
                                             order_values, model_values, D)
        if paired is None:
            return
        prog = self._order_ctx["namer"].program_name(comp.id)
        for rec in paired.get("coordinates", []):
            rec["ProgramName"] = prog          # same stem as the component's G-code / PDF
        res["drilling"] = paired

    def _counter(self, cfg):
        from . import gcode
        if self._order_ctx.get("counter") is None:
            C = cfg.get("cam") or {}
            path = os.path.join(self.paths.root, C.get("program_counter_file") or "state/nc_program_counter.txt")
            self._order_ctx["counter"] = gcode.ProgramCounter(
                path, start=int(C.get("program_counter_start", 1001)),
                seed_paths=[C["seed_counter_from"]] if C.get("seed_counter_from") else ())
        return self._order_ctx["counter"]

    def _manufacture(self, order, comp, cfg, model, model_values, order_values, res, ctx):
        """Pick toolpaths from the tables; when there are none, skip the
        manufacture workspace entirely and go straight to the drawing."""
        from . import cam_rules, gcode
        C = cfg.get("cam") or {}
        if not C.get("enabled", True):
            return
        ops = self.ops
        fam = rules_mod.family_from_order_id(order.order_id)
        profile = rules_mod.resolve(self._order_ctx["profiles"], comp.type, comp.series_id, fam)
        if profile is None:
            res["toolpaths"] = {"profile": None, "result": "no toolpath table for this component"}
            return
        if self._order_ctx["profile_errors"]:
            raise ComponentError("toolpath tables are invalid: %s" % "; ".join(self._order_ctx["profile_errors"][:3]))
        self._step(res, "plan_toolpaths")
        env, missing = cam_rules.collect_values(profile, model_values, order_values)
        try:
            pl = cam_rules.plan(profile, env)
        except cam_rules.NoRuleMatched as e:
            raise ComponentError("no row of toolpath table %s matches: %s" % (profile.get("id"), e))
        tp = {"profile": profile.get("id"), "row": pl["row_id"], "note": pl["note"],
              "setups": list(pl["setups"]), "missing_variables": missing,
              "inputs": {k: v for k, v in env.items() if not isinstance(v, (dict, list))}}
        res["toolpaths"] = tp
        if missing:
            res["warnings"].append("toolpath table %s: model/order has no %s (used defaults)"
                                   % (profile.get("id"), ", ".join(missing)))
        if not pl["setups"]:
            tp["result"] = "no toolpaths needed"
            return

        self._step(res, "manufacture_workspace")
        ops.activate(model)
        if C.get("switch_workspace", True):
            ops.activate_workspace("cam")
            ctx["workspace"] = "cam"
        state = ops.cam_state(model)
        if not state:
            raise ComponentError("model %s has no manufacturing setups" % res.get("model"))
        actions = cam_rules.resolve_actions(pl, state)
        if actions["missing_setups"]:
            raise ComponentError("setup(s) not found in the model: %s" % ", ".join(actions["missing_setups"]))
        tp["sequence"] = list(actions["sequence"])
        tp["changes"] = ops.cam_prepare(model, actions)
        if actions.get("unknown_ops"):
            tp["unknown_operations"] = ["%s / %s" % (a, b) for a, b in actions["unknown_ops"]]

        self._step(res, "generate_toolpaths")
        token = ops.cam_generate(model, actions["sequence"])
        if token is not None:
            yield self._wait(lambda: ops.cam_generate_done(token), C.get("generate_timeout_s", 300),
                             "toolpath generation", res)
        errors, warns = ops.cam_problems(model, actions["sequence"])
        res["warnings"].extend(warns)
        if errors:
            raise ComponentError("toolpaths not generated: %s" % "; ".join(errors[:5]))

        self._step(res, "post_process")
        post = C.get("post_processor") or ""
        if not os.path.isabs(post):
            post = os.path.join(self.paths.root, post)
        out_dir = self._order_ctx["out_dir"]
        tmp = os.path.join(out_dir, "_nc", naming_mod.safe_filename(comp.id))
        sections = []
        counter = self._counter(cfg)
        for sname in actions["sequence"]:
            nc = ops.cam_post(model, sname, counter.next(), tmp, post)
            sections.append((sname, gcode.read_nc(nc)))
            yield None                    # let Fusion breathe between posts

        self._step(res, "splice_gcode")
        g57_first = (profile.get("splice") or {}).get("g57_before_g59", True)
        ordered = gcode.order_sections(sections, g57_before_g59=g57_first, explicit_sequence=True)
        text = gcode.build_program(ordered, g57_before_g59=False,
                                   strip_numbered_m30=bool(C.get("strip_numbered_m30", False)))
        final = os.path.join(out_dir, self._order_ctx["namer"].gcode(comp.id, comp.type, comp.series_id))
        gcode.write_program(final, text)
        tp["result"] = "%d setup(s) spliced" % len(ordered)
        tp["splice"] = gcode.splice_report(ordered)
        res["gcode"] = os.path.relpath(final, self.paths.output).replace("\\", "/")
        res["gcode_bytes"] = os.path.getsize(final)
        if not C.get("keep_nc_sections", False):
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                os.rmdir(os.path.dirname(tmp))
            except OSError:
                pass
        if ctx.get("workspace") == "cam":
            ops.activate_workspace("design")
            ctx["workspace"] = None

    def _finalize_outputs(self, order, plan, results, namer, out_dir, cfg):
        """Order-level outputs: the compiled drilling file + optional drop copies."""
        from . import config as config_mod
        from . import drilling
        outputs = {"folder": os.path.basename(out_dir)}
        D = config_mod.deep_merge(drilling.DRILLING_DEFAULTS, cfg.get("drilling") or {})
        drilled = [(c.type, results[c.id]["drilling"]) for c, _ in plan
                   if isinstance(results.get(c.id), dict) and results[c.id].get("drilling")]
        compiled = drilling.compile_order(order.order_id, drilled) if D.get("enabled", True) and drilled else None
        if compiled is not None:
            dpath = os.path.join(out_dir, namer.drilling())
            drilling.write_compiled(dpath, compiled)
            outputs["drilling"] = os.path.relpath(dpath, self.paths.output).replace("\\", "/")
            outputs["drilling_components"] = len(drilled)
        drops = {k: (v if not v or os.path.isabs(v) else os.path.join(self.paths.root, v))
                 for k, v in (cfg.get("drops") or {}).items()}
        copied = []
        import shutil
        for kind, key in (("gcode", "gcode"), ("drawings", "pdf")):
            dest = drops.get(kind)
            if not dest:
                continue
            for c, _ in plan:
                rel = (results.get(c.id) or {}).get(key)
                if rel:
                    try:
                        os.makedirs(dest, exist_ok=True)
                        shutil.copy2(os.path.join(self.paths.output, rel), dest)
                        copied.append(os.path.join(dest, os.path.basename(rel)))
                    except OSError as e:
                        self.state.event("warn", "Drop copy to %s failed: %s" % (dest, e))
        if drops.get("drilling") and outputs.get("drilling"):
            try:
                os.makedirs(drops["drilling"], exist_ok=True)
                shutil.copy2(os.path.join(self.paths.output, outputs["drilling"]), drops["drilling"])
                copied.append(os.path.join(drops["drilling"], namer.drilling()))
            except OSError as e:
                self.state.event("warn", "Drop copy to %s failed: %s" % (drops["drilling"], e))
        if copied:
            outputs["dropped"] = copied
        return outputs

    def _wait_until_ready(self):
        """Hold at a component boundary until Fusion is signed in and online."""
        announced = False
        while True:
            try:
                ok, why = self.ops.ready()
            except Exception as e:
                ok, why = False, str(e)
            if ok:
                if announced:
                    self.state.event("info", "Fusion is ready; continuing")
                    self._publish(state="processing", fusion_wait_reason=None)
                return
            if not announced:
                self.state.event("warn", "Waiting for Fusion: %s" % why)
                self._publish(state="waiting_fusion", fusion_wait_reason=why)
                announced = True
            yield Sleep(3.0)

    def _resolve(self, ref, attempts=6, pause=10.0):
        """Look up a cloud file, retrying while the hub is slow to answer."""
        for i in range(attempts):
            try:
                return self.ops.resolve_file(ref)
            except Exception as e:
                if i == attempts - 1:
                    raise
                self.state.event("warn", "Cloud lookup of %s failed (%s); retrying"
                                 % (ref.get("name") or ref.get("id"), e))
                yield Sleep(pause)

    # ------------------------------------------------------------------
    # garbage collection
    # ------------------------------------------------------------------
    def _close_verified(self, doc, label):
        """Close a document and confirm it is really gone.

        Fusion's close() can report success and do nothing (seen with hidden
        drawing-reference copies), so the only trusted signal is the document
        becoming invalid.  Escalates to activate+close once."""
        ops = self.ops
        try:
            if not ops.doc_alive(doc):
                return True
            name = ops.doc_label(doc)
        except Exception:
            return True
        for attempt in ("close", "activate+close"):
            try:
                if attempt == "activate+close":
                    ops.activate(doc)
                    yield None
                ops.close_document(doc)
            except Exception as e:
                self.state.event("warn", "close %s (%s) raised: %s" % (label, name, e))
            gone = yield WaitFor(lambda: not ops.doc_alive(doc), 5.0,
                                 "close of %s" % name, raise_on_timeout=False)
            if gone:
                return True
        self.state.event("warn", "Could not close %s %s" % (label, name))
        return False

    def _collect_garbage(self, full=False):
        """Close every pipeline document except the cached models.

        ``full`` also flushes the model cache.  Returns number closed."""
        ops = self.ops
        if full:
            while self._model_cache:
                _, d = self._model_cache.popitem(last=False)
                yield from self._close_verified(d, "cached model")
        keep = list(self._model_cache.values())
        before = ops.document_count()
        closed = 0
        failed = 0
        for doc in ops.managed_documents(keep=keep)[:20]:
            ok = yield from self._close_verified(doc, "leftover document")
            if ok:
                closed += 1
            else:
                failed += 1
        gc.collect()
        after = ops.document_count()
        if closed or failed or full:
            self.state.event("info" if not failed else "warn",
                             "Garbage collection: closed %d, %d not closable, documents %d -> %d"
                             % (closed, failed, before, after))
        return closed

    def _after_component(self, res, ctx, cfg):
        P = cfg.get("processing", {})
        self._step(res, "cleanup")
        if ctx.get("workspace") == "cam":
            try:
                self.ops.activate_workspace("design")
            except Exception as e:
                res["warnings"].append("could not return to the design workspace: %s" % e)
            ctx["workspace"] = None
        if ctx.get("drawing") is not None:
            yield from self._close_verified(ctx["drawing"], "drawing")
            ctx["drawing"] = None
        model = ctx.get("model")
        if model is not None:
            dirty = False
            try:
                dirty = (not self.ops.doc_alive(model)) or self.ops.doc_is_modified(model)
            except Exception:
                dirty = True
            if res["status"] != "done" or dirty:
                # never reuse a model left in an unknown state
                for k, d in list(self._model_cache.items()):
                    if d is model:
                        del self._model_cache[k]
                yield from self._close_verified(model, "model")
        for k, d in list(self._opened_models.items()):
            yield from self._close_verified(d, "model")
            del self._opened_models[k]
        self._since_recycle += 1
        every = int(P.get("recycle_every_components", 0) or 0)
        full = bool(every) and self._since_recycle >= every
        if full:
            self._since_recycle = 0
            self.state.event("info", "Recycling all documents (every %d components)" % every)
        yield from self._collect_garbage(full=full)
        res["health_after"] = self._sample_health()

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------
    def _sample_health(self):
        try:
            h = self.ops.process_health() or {}
        except Exception as e:
            h = {"error": str(e)}
        h["at"] = self.wall()
        self._publish(health=h)
        return h

    def _check_health(self, cfg):
        H = cfg.get("health", {})
        h = self.state.get("health") or {}
        reasons = []
        if h.get("threads") and h["threads"] > H.get("max_threads", 4000):
            reasons.append("threads %d > %d" % (h["threads"], H.get("max_threads")))
        if h.get("working_set_mb") and h["working_set_mb"] > H.get("max_working_set_mb", 12000):
            reasons.append("memory %.0f MB > %d MB" % (h["working_set_mb"], H.get("max_working_set_mb")))
        if h.get("documents") and h["documents"] > H.get("max_open_documents", 12):
            reasons.append("%d open documents > %d" % (h["documents"], H.get("max_open_documents")))
        every = int(H.get("restart_every_components", 0) or 0)
        made = self.counters["components_done"] + self.counters["components_failed"] - self._refresh_base
        if every and made >= every:
            reasons.append("scheduled refresh after %d components" % made)
        if reasons and not self.restart_requested:
            self.restart_requested = True
            self.restart_reason = ", ".join(reasons)
            self.state.event("warn", "Fusion restart requested at component boundary: %s" % self.restart_reason)
            self._publish(state="waiting_restart")

    # ------------------------------------------------------------------
    def _publish(self, **fields):
        fields.setdefault("paused", self.paused)
        fields["pause_reason"] = self.pause_reason
        fields["restart_requested"] = self.restart_requested
        fields["restart_reason"] = self.restart_reason
        fields["counters"] = dict(self.counters)
        fields["cached_models"] = len(self._model_cache)
        if "state" not in fields:
            fields["state"] = self._state_name()
        self.state.update(**fields)


def _apply_input_transforms(values, ctype, transforms):
    """Reshape order values in place (see config orders.input_transforms).
    Returns the names of parameters it produced."""
    produced = []
    for t in transforms or []:
        if t.get("type") != "split_text" or ctype not in (t.get("component_types") or []):
            continue
        src, targets = t.get("from"), list(t.get("to") or [])
        pv = values.pop(src, None)
        raw = "" if pv is None or pv.value is None else str(pv.value).strip()
        if raw.lower() == "null":
            raw = ""
        parts = [raw]
        for sep in t.get("separators") or []:
            if sep in raw:
                parts = raw.split(sep)
                break
        parts = [p.strip() for p in parts if p.strip()]
        for i, name in enumerate(targets):
            values[name] = ParamValue(parts[i] if i < len(parts) else "", "string")
            produced.append(name)
    return produced


def _pdf_ready(path):
    try:
        if os.path.getsize(path) < 64:
            return False
        with open(path, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False
