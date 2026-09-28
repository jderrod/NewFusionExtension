"""NewFusionExtension harness: web dashboard + Fusion supervisor.

Runs outside Fusion (standard library only):

    py -3 harness/server.py            # http://127.0.0.1:8765

* serves the dashboard (harness/static)
* samples the Fusion process (threads, memory, handles) and keeps history
* reads the add-in's status/events files and proxies hub browsing to it
* manages the dropbox (upload/preview/requeue orders) and the config
* restarts Fusion when the add-in asks for it (health thresholds), when the
  add-in's heartbeat goes stale, when a step blocks too long, or after a crash
"""

import argparse
import collections
import json
import logging
import logging.handlers
import mimetypes
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "addin", "NewFusionExtension"))

from nfe import VERSION, winproc  # noqa: E402
from nfe import config as config_mod  # noqa: E402
from nfe import rules as rules_mod  # noqa: E402
from nfe.dropbox import Dropbox  # noqa: E402
from nfe.jsonio import read_json, write_json_atomic  # noqa: E402
from nfe.orders import OrderError, parse_order, safe_name  # noqa: E402
from nfe.paths import Paths  # noqa: E402
from nfe.state import read_events_file  # noqa: E402

FUSION_EXE = "Fusion360.exe"
SAMPLE_S = 5.0
HISTORY_POINTS = 1440          # 2 hours at 5 s
MAX_BODY = 6 * 1024 * 1024
MAX_RESTARTS_PER_HOUR = 6


class HttpError(Exception):
    def __init__(self, status, message):
        Exception.__init__(self, message)
        self.status = status


class Harness(object):
    def __init__(self, root=ROOT, fusion_exe=FUSION_EXE, launch_fn=None, kill_fn=None):
        self.root = root
        self.base_paths = Paths(root).ensure()
        config_mod.ensure_exists(self.base_paths.config_file)
        self.cfg = config_mod.load(self.base_paths.config_file)
        self.paths = Paths.from_config(root, self.cfg).ensure()
        self.dropbox = Dropbox(self.paths)
        self.fusion_exe = fusion_exe
        self.launch_fn = launch_fn or self._launch_fusion
        self.kill_fn = kill_fn or winproc.terminate
        self.log = self._setup_log()
        self.history = collections.deque(maxlen=HISTORY_POINTS)
        self.events = collections.deque(maxlen=500)
        self._seq = 0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.fusion = {"running": False}
        self.restarts = collections.deque(maxlen=50)
        self.restart_in_progress = False
        self.last_restart_at = 0.0
        self._seen_pid_with_addin = None
        self._status_cache = {}
        self.boot_id = "%x" % int(time.time() * 1000)   # harness event numbering restarts with it
        self.addin_wait_s = 600         # how long a restart waits for the add-in to come back
        self.post_kill_sleep_s = 3.0
        self.ping_interval_s = 5.0

    # ------------------------------------------------------------------
    def _setup_log(self):
        log = logging.getLogger("nfe.harness")
        if not log.handlers:
            h = logging.handlers.RotatingFileHandler(os.path.join(self.paths.logs, "harness.log"),
                                                     maxBytes=5 * 1024 * 1024, backupCount=3,
                                                     encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            log.addHandler(h)
            log.setLevel(logging.INFO)
        return log

    def close(self):
        """Stop the supervisor and release the log file (tests)."""
        self.stop()
        for hd in list(self.log.handlers):
            self.log.removeHandler(hd)
            hd.close()

    def event(self, level, msg, **data):
        with self._lock:
            self._seq += 1
            ev = {"seq": self._seq, "ts": time.time(), "level": level, "msg": msg, "src": "harness"}
            if data:
                ev["data"] = data
            self.events.append(ev)
        self.log.log({"warn": logging.WARNING, "error": logging.ERROR}.get(level, logging.INFO), msg)
        return ev

    def reload_config(self):
        self.cfg = config_mod.load(self.base_paths.config_file)
        if getattr(self, "paths", None) is not None:
            self.paths.apply_location(self.cfg)
        return self.cfg

    # ------------------------------------------------------------------
    # add-in access
    # ------------------------------------------------------------------
    def addin_url(self, path):
        return "http://127.0.0.1:%d%s" % (int(self.cfg["api"]["addin_port"]), path)

    def addin(self, path, body=None, timeout=30):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.addin_url(path), data=data, method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error")
            except Exception:
                msg = str(e)
            raise HttpError(e.code, msg)
        except (urllib.error.URLError, OSError) as e:
            raise HttpError(503, "add-in not reachable (is Fusion running with NewFusionExtension?): %s"
                            % getattr(e, "reason", e))

    def addin_status(self):
        st = read_json(self.paths.status_file, default=None)
        if st is not None:
            self._status_cache = st
        return self._status_cache

    # ------------------------------------------------------------------
    # supervisor
    # ------------------------------------------------------------------
    def start_supervisor(self):
        t = threading.Thread(target=self._supervise, name="supervisor", daemon=True)
        t.start()
        self.start_sync()
        return t

    # ------------------------------------------------------------------
    # location (local / remote) + network sync
    # ------------------------------------------------------------------
    def start_sync(self):
        """Mirror local outputs to the shares (or network_mirror/ in local mode).
        Runs here, outside Fusion, so a slow share never stalls an order."""
        try:
            from nfe.sync import SyncService
            self.sync = SyncService(self.root, lambda: self.cfg.get("location") or {},
                                    os.path.join(self.paths.state, "sync_state.json"), log=self.log)
            self.sync.start()
        except Exception as e:
            self.sync = None
            self.event("error", "File sync service not started: %s" % e)

    def location(self):
        from nfe import sync as sync_mod
        self.reload_config()
        loc = self.cfg.get("location") or {}
        paths = Paths.from_config(self.root, self.cfg)
        dirs = [{"role": "processing (local)", "path": paths.processing, "exists": os.path.isdir(paths.processing), "writable": True},
                {"role": "done / failed (local)", "path": paths.done + " | " + paths.failed, "exists": os.path.isdir(paths.done), "writable": True},
                {"role": "output run folders (local)", "path": paths.output, "exists": os.path.isdir(paths.output), "writable": True}]
        try:
            dirs = sync_mod.check_paths(self.root, loc) + dirs
        except Exception as e:
            dirs.insert(0, {"role": "check failed", "path": str(e), "exists": False, "writable": False})
        status = None
        if getattr(self, "sync", None) is not None:
            try:
                status = self.sync.status()
            except Exception as e:
                status = {"error": str(e)}
        return {"mode": loc.get("mode", "local"), "location": loc, "inbox": paths.inbox,
                "directories": dirs, "sync": status}

    def set_location_mode(self, mode):
        if mode not in ("local", "remote"):
            raise HttpError(400, "mode must be local or remote")
        cfg = self.reload_config()
        cfg["location"]["mode"] = mode
        self.save_config(cfg)                  # also asks the add-in to reload its config
        self.event("warn" if mode == "remote" else "info",
                   "Location switched to %s: orders are read from %s" % (
                       mode.upper(), Paths.from_config(self.root, cfg).inbox))
        return self.location()

    def sync_action(self, action, pair=None):
        if getattr(self, "sync", None) is None:
            raise HttpError(503, "file sync service is not running")
        if action == "run_once":
            self.sync.trigger()
            return {"ok": True, "message": "sync pass started"}
        if action == "resync":
            self.sync.resync(pair or None)
            self.event("warn", "Sync state cleared for %s: files will be sent again" % (pair or "all shares"))
            return {"ok": True}
        raise HttpError(400, "unknown sync action %r" % action)

    def stop(self):
        self._stop.set()
        if getattr(self, "sync", None) is not None:
            try:
                self.sync.stop()
            except Exception:
                pass

    def _supervise(self):
        while not self._stop.wait(SAMPLE_S):
            try:
                self.sample_once()
                self.check_policies()
            except Exception:
                self.log.exception("supervisor")

    def sample_once(self):
        pids = winproc.find_pids(self.fusion_exe)
        info = None
        for pid in pids:
            info = winproc.process_info(pid)
            if info:
                break
        st = self.addin_status()
        now = time.time()
        hb = st.get("heartbeat_at") or 0
        prev_running = self.fusion.get("running")
        if info:
            self.fusion = dict(info, running=True)
        else:
            self.fusion = {"running": False, "pid": None}
        addin_pid_matches = bool(info) and st.get("addin_pid") == info["pid"]
        if addin_pid_matches and now - hb < 30:
            self._seen_pid_with_addin = info["pid"]
        point = {"t": round(now, 1),
                 "threads": info.get("threads") if info else None,
                 "mb": info.get("working_set_mb") if info else None,
                 "handles": info.get("handles") if info else None,
                 "docs": (st.get("health") or {}).get("documents") if addin_pid_matches else None,
                 "state": st.get("state") if addin_pid_matches else ("offline" if info else "no_fusion")}
        with self._lock:
            self.history.append(point)
        if prev_running and not info and not self.restart_in_progress:
            self.event("warn" if st.get("state") != "stopped" else "info",
                       "Fusion process exited (add-in state: %s)" % st.get("state"))
        return point

    def heartbeat_age(self):
        st = self.addin_status()
        hb = st.get("heartbeat_at")
        if not hb or not self.fusion.get("running") or st.get("addin_pid") != self.fusion.get("pid"):
            return None
        return time.time() - hb

    def check_policies(self):
        """Decide whether Fusion needs a (re)start.  Returns the reason or None."""
        sup = self.cfg.get("supervisor", {})
        if not sup.get("auto_restart_fusion", True) or self.restart_in_progress:
            return None
        if time.time() - self.last_restart_at < float(sup.get("restart_cooldown_s", 300)):
            return None
        st = self.addin_status()
        reason = None
        if self.fusion.get("running"):
            same = st.get("addin_pid") == self.fusion.get("pid")
            age = self.heartbeat_age()
            if same and st.get("restart_requested"):
                reason = "add-in requested restart: %s" % (st.get("restart_reason") or "health limits")
            elif same and st.get("state") not in ("stopped",) and age is not None \
                    and age > float(sup.get("heartbeat_stale_s", 60)):
                reason = "add-in heartbeat stale for %.0fs (Fusion hung)" % age
            elif same and (st.get("ui_blocked_s") or 0) > float(
                    self.cfg.get("processing", {}).get("step_stuck_timeout_s", 900)):
                reason = "Fusion UI thread blocked for %.0fs in step %s" % (
                    st.get("ui_blocked_s"), (st.get("component") or {}).get("step"))
        else:
            crashed = self._seen_pid_with_addin is not None and st.get("state") != "stopped"
            if crashed:
                reason = "Fusion is not running (crashed while add-in state was %s)" % st.get("state")
        if reason:
            recent = [r for r in self.restarts if time.time() - r["at"] < 3600]
            if len(recent) >= MAX_RESTARTS_PER_HOUR:
                if not getattr(self, "_restart_limit_warned", False):
                    self.event("error", "Restart limit reached (%d/hour); not restarting: %s"
                               % (MAX_RESTARTS_PER_HOUR, reason))
                    self._restart_limit_warned = True
                return None
            self._restart_limit_warned = False
            self.restart_fusion(reason)
        return reason

    def restart_fusion(self, reason, wait=False):
        if self.restart_in_progress:
            raise HttpError(409, "restart already in progress")
        self.restart_in_progress = True
        self.last_restart_at = time.time()
        rec = {"at": time.time(), "reason": reason, "result": "running"}
        self.restarts.append(rec)
        self.event("warn", "Restarting Fusion: %s" % reason)
        t = threading.Thread(target=self._restart_worker, args=(rec,), name="restart", daemon=True)
        t.start()
        if wait:
            t.join()
        return rec

    def _restart_worker(self, rec):
        try:
            for pid in winproc.find_pids(self.fusion_exe):
                self.event("info", "Terminating Fusion pid %d" % pid)
                self.kill_fn(pid)
                if not winproc.wait_for_exit(pid, 60):
                    raise RuntimeError("Fusion pid %d did not exit" % pid)
            time.sleep(self.post_kill_sleep_s)
            self.launch_fn()
            self.event("info", "Fusion launched; waiting for the add-in")
            deadline = time.time() + self.addin_wait_s
            while time.time() < deadline and not self._stop.is_set():
                try:
                    self.addin("/api/ping", timeout=3)
                    rec["result"] = "ok"
                    rec["took_s"] = round(time.time() - rec["at"], 1)
                    self.event("info", "Fusion restarted, add-in online after %.0fs" % rec["took_s"])
                    self._seen_pid_with_addin = None
                    return
                except HttpError:
                    time.sleep(self.ping_interval_s)
            rec["result"] = "add-in did not come back within %.0fs" % self.addin_wait_s
            self.event("error", "Fusion restart: %s" % rec["result"])
        except Exception as e:
            rec["result"] = "failed: %s" % e
            self.event("error", "Fusion restart failed: %s" % e)
        finally:
            self.restart_in_progress = False
            self.last_restart_at = time.time()

    def _launch_fusion(self):
        exe = self.cfg.get("supervisor", {}).get("fusion_launcher") or winproc.find_fusion_launcher()
        if not exe or not os.path.isfile(exe):
            raise RuntimeError("FusionLauncher.exe not found (set supervisor.fusion_launcher)")
        winproc.launch_detached([exe])

    # ------------------------------------------------------------------
    # views
    # ------------------------------------------------------------------
    def overview(self, history_points=360):
        st = self.addin_status()
        age = self.heartbeat_age()
        online = age is not None and age < float(self.cfg["supervisor"].get("heartbeat_stale_s", 60))
        with self._lock:
            hist = list(self.history)[-history_points:]
        counts = {k: len(self.dropbox.processing_files()) if k == "processing" else None
                  for k in ("processing",)}
        counts["inbox"] = len(self.dropbox.inbox_files(settled_only=False))
        counts["done"] = _count_orders(self.paths.done)
        counts["failed"] = _count_orders(self.paths.failed)
        return {
            "now": time.time(), "harness_version": VERSION,
            "fusion": self.fusion,
            "addin": {"online": online, "heartbeat_age_s": None if age is None else round(age, 1),
                      "status": st},
            "queue": counts,
            "supervisor": {"auto_restart": self.cfg["supervisor"].get("auto_restart_fusion", True),
                           "restart_in_progress": self.restart_in_progress,
                           "restarts": list(self.restarts)[-10:]},
            "thresholds": self.cfg.get("health", {}),
            "location": {"mode": (self.cfg.get("location") or {}).get("mode", "local"),
                         "inbox": self.paths.inbox},
            "history": hist,
        }

    def merged_events(self, since_addin, since_harness, limit=300):
        addin = read_events_file(self.paths.events_file, since_addin, limit)
        with self._lock:
            harness = [e for e in self.events if e["seq"] > since_harness]
        for e in addin:
            e["src"] = "addin"
        return {"boot": self.boot_id,
                "events": sorted(addin + harness, key=lambda e: e["ts"])[-limit:],
                "last_addin": addin[-1]["seq"] if addin else since_addin,
                "last_harness": harness[-1]["seq"] if harness else since_harness}

    def orders(self, folder):
        if folder == "inbox":
            out = []
            for n in self.dropbox.inbox_files(settled_only=False):
                p = os.path.join(self.paths.inbox, n)
                out.append({"name": n, "folder": "inbox", "mtime": os.path.getmtime(p),
                            "size": os.path.getsize(p)})
            return out
        if folder not in ("processing", "done", "failed"):
            raise HttpError(400, "bad folder")
        return self.dropbox.listing(folder)[:300]

    def report(self, folder, name):
        if folder not in ("done", "failed", "processing"):
            raise HttpError(400, "bad folder")
        name = os.path.basename(name)
        d = getattr(self.paths, folder)
        if folder == "processing":
            ck = read_json(os.path.join(d, os.path.splitext(name)[0] + ".state.json"))
            return {"checkpoint": ck, "status": self.addin_status().get("order")}
        rep = read_json(Dropbox.report_path(d, name))
        if rep is None:
            raise HttpError(404, "no report")
        return rep

    def preview(self, content):
        data = _parse_content(content)
        try:
            order = parse_order(data, "upload.json",
                                type_by_key=self.cfg["orders"].get("type_by_key"),
                                type_by_prefix=self.cfg["orders"].get("type_by_prefix"))
        except OrderError as e:
            raise HttpError(400, str(e))
        self.reload_config()
        return {"order_id": order.order_id, "counts": order.counts(),
                "components": rules_mod.explain(self.cfg.get("rules", []), order)}

    def upload(self, name, content):
        data = _parse_content(content)
        try:
            order = parse_order(data, name or "order.json",
                                type_by_key=self.cfg["orders"].get("type_by_key"),
                                type_by_prefix=self.cfg["orders"].get("type_by_prefix"))
        except OrderError as e:
            raise HttpError(400, str(e))
        stem = safe_name(os.path.splitext(os.path.basename(name or ""))[0] or order.order_id)
        with self._lock:
            dst = os.path.join(self.paths.inbox, stem + ".json")
            n = 1
            while os.path.exists(dst) or os.path.exists(dst + ".uploading"):
                n += 1
                dst = os.path.join(self.paths.inbox, "%s_%d.json" % (stem, n))
            tmp = dst + ".uploading"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp, dst)
        self.event("info", "Order uploaded to inbox: %s (%d components)" % (
            os.path.basename(dst), len(order.components)))
        return {"name": os.path.basename(dst), "components": len(order.components)}

    def requeue(self, folder, name):
        if folder not in ("done", "failed"):
            raise HttpError(400, "only done/failed orders can be re-queued")
        try:
            dst = self.dropbox.requeue(folder, os.path.basename(name))
        except FileNotFoundError:
            raise HttpError(404, "not found")
        self.event("info", "Re-queued %s from %s" % (os.path.basename(dst), folder))
        return {"name": os.path.basename(dst)}

    def remove_inbox(self, name):
        p = os.path.join(self.paths.inbox, os.path.basename(name))
        if not os.path.isfile(p):
            raise HttpError(404, "not found")
        os.remove(p)
        self.event("info", "Removed %s from inbox" % os.path.basename(name))
        return {"ok": True}

    def save_config(self, cfg):
        merged = config_mod.deep_merge(config_mod.DEFAULTS, cfg)
        errors = config_mod.validate(merged)
        if errors:
            raise HttpError(400, "; ".join(errors))
        write_json_atomic(self.base_paths.config_file, merged)
        self.reload_config()
        self.event("info", "Configuration saved (%d rules)" % len(merged.get("rules", [])))
        try:
            self.addin("/api/control", {"action": "reload_config"}, timeout=10)
        except HttpError:
            pass   # the add-in re-reads config at every order anyway
        return merged

    def suggest_rules(self):
        self.reload_config()
        files = []
        for fam, folder in (self.cfg["hub"].get("family_folders") or {}).items():
            fid = folder.get("id") if isinstance(folder, dict) else folder
            if not fid:
                continue
            lst = self.addin("/api/hub/folder?id=" + urllib.request.quote(fid, safe=""), timeout=120)
            for f in lst.get("files", []):
                f["path"] = "%s/%s" % (lst.get("project") or "", lst.get("name") or fam)
                files.append(f)
        return {"suggestions": rules_mod.suggest_rules(files, self.cfg.get("rules", [])),
                "scanned_files": len(files)}

    def docs(self):
        """Markdown documents and diagrams in <project>/docs for the Docs tab."""
        d = os.path.join(self.root, "docs")
        out = []
        try:
            names = sorted(os.listdir(d))
        except OSError:
            names = []
        order = ["README.md", "OPERATIONS.md", "ARCHITECTURE.md", "WORKFLOW.md", "INFRASTRUCTURE.md", "TESTING.md"]
        for n in names:
            low = n.lower()
            if not low.endswith((".md", ".svg")):
                continue
            title = os.path.splitext(n)[0].replace("_", " ").title()
            if low.endswith(".md"):
                try:
                    with open(os.path.join(d, n), encoding="utf-8") as fh:
                        for line in fh:
                            if line.startswith("# "):
                                title = line[2:].strip()
                                if title.lower().startswith("newfusionextension "):
                                    title = title[len("newfusionextension "):].strip().capitalize()
                                break
                except OSError:
                    pass
            out.append({"name": n, "title": title, "kind": "diagram" if low.endswith(".svg") else "doc",
                        "rank": order.index(n) if n in order else 50})
        try:
            tables = sorted(n for n in os.listdir(os.path.join(d, "tables")) if n.lower().endswith(".md"))
        except OSError:
            tables = []
        for n in tables:
            title = os.path.splitext(n)[0].replace("_", " ").title()
            try:
                with open(os.path.join(d, "tables", n), encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith("# "):
                            title = line[2:].strip()
                            break
            except OSError:
                pass
            out.append({"name": "tables/" + n, "title": title, "kind": "table", "rank": 60})
        readme = os.path.join(self.root, "README.md")
        if os.path.isfile(readme):
            out.append({"name": "README.md", "title": "Project overview (README)", "kind": "doc", "rank": -1})
        out.sort(key=lambda x: ({"doc": 0, "table": 1}.get(x["kind"], 2), x["rank"], x["name"]))
        return out

    def doc_file(self, rel):
        rel = unquote(rel).replace("\\", "/")
        name = os.path.basename(rel)
        if name == "README.md":
            full = os.path.join(self.root, "README.md")
        elif rel.startswith("tables/"):
            full = os.path.join(self.root, "docs", "tables", name)
        else:
            full = os.path.join(self.root, "docs", name)
        if not name.lower().endswith((".md", ".svg", ".png")) or not os.path.isfile(full):
            raise HttpError(404, "not found")
        return full

    # ------------------------------------------------------------------
    # logic tables (toolpath selection, splicing, drilling, naming)
    # ------------------------------------------------------------------
    def _profiles_path(self):
        rel = (self.cfg.get("cam") or {}).get("profiles_file") or "config/toolpath_profiles.json"
        return os.path.join(self.root, rel), rel

    def _profiles(self):
        from nfe.cam_defaults import DEFAULT_PROFILES
        path, rel = self._profiles_path()
        data = read_json(path, default=None)
        profiles = data.get("profiles") if isinstance(data, dict) else (data if isinstance(data, list) else None)
        if profiles is None:
            return DEFAULT_PROFILES, "built-in defaults", rel
        return profiles, rel, rel

    def logic(self):
        from nfe import cam_rules, drilling, gcode
        from nfe import naming as naming_mod
        self.reload_config()
        profiles, source, rel = self._profiles()
        errors = {p.get("id"): cam_rules.validate_profile(p) for p in profiles}
        D = config_mod.deep_merge(drilling.DRILLING_DEFAULTS, self.cfg.get("drilling") or {})
        namer = naming_mod.Namer(self.cfg.get("naming"), "3X8X_DV_0622", "20260206_115328_3X8X_DV_0622.json")
        cam = self.cfg.get("cam") or {}
        return {
            "profiles": profiles, "profiles_source": source, "profiles_file": rel, "errors": errors,
            "drilling": D,
            "splice": {"flip_text": gcode.FLIP_TEXT, "rotate_text": gcode.ROTATE_TEXT,
                       "strip_numbered_m30": bool(cam.get("strip_numbered_m30", False)),
                       "post_processor": cam.get("post_processor"),
                       "counter_start": cam.get("program_counter_start", 1001)},
            "naming": {"templates": namer.templates, "placeholders": list(naming_mod.PLACEHOLDERS),
                       "example": {"order_folder": namer.order_folder(), "gcode": namer.gcode("D1"),
                                   "drawing_pdf": namer.drawing_pdf("D1"), "drilling": namer.drilling(),
                                   "report": namer.report()}},
        }

    def logic_evaluate(self, profile_id, values):
        from nfe import cam_rules
        profiles, _, _ = self._profiles()
        prof = next((p for p in profiles if p.get("id") == profile_id), None)
        if prof is None:
            raise HttpError(404, "no toolpath table %r" % profile_id)
        env, missing = cam_rules.collect_values(prof, values or {}, values or {})
        rows = cam_rules.explain(prof, env)
        try:
            pl = cam_rules.plan(prof, env)
        except cam_rules.NoRuleMatched as e:
            return {"matched": None, "error": str(e), "rows": rows, "env": env, "missing": missing}
        return {"matched": pl["row_id"], "note": pl["note"], "setups": pl["setups"],
                "op_rules": [{"op": o, "active": bool(a)} for o, a in pl["op_rules"]],
                "unknown_operations": pl.get("unknown_operations"), "rows": rows,
                "env": {k: v for k, v in env.items() if isinstance(v, (int, float, str, bool))},
                "missing": missing}

    def logic_drilling(self, body):
        from nfe import drilling
        D = config_mod.deep_merge(drilling.DRILLING_DEFAULTS, self.cfg.get("drilling") or {})
        out = drilling.component_drilling(body.get("type") or "door", body.get("component_id") or "X1",
                                          body.get("order_id") or "TEST", body.get("series_id") or "",
                                          body.get("order_values") or {}, body.get("model_values") or {}, D)
        return {"result": out}

    def save_profiles(self, profiles):
        from nfe import cam_rules
        if not isinstance(profiles, list) or not profiles:
            raise HttpError(400, "expected a non-empty list of toolpath tables")
        errors = []
        for p in profiles:
            errors.extend("%s: %s" % (p.get("id"), e) for e in cam_rules.validate_profile(p))
        ids = [p.get("id") for p in profiles]
        if len(set(ids)) != len(ids):
            errors.append("duplicate table ids")
        if errors:
            raise HttpError(400, "; ".join(errors[:8]))
        path, rel = self._profiles_path()
        write_json_atomic(path, {"profiles": profiles})
        self.event("info", "Toolpath tables saved to %s (%d tables)" % (rel, len(profiles)))
        return {"ok": True, "file": rel}

    def reset_profiles(self):
        path, rel = self._profiles_path()
        if os.path.exists(path):
            os.replace(path, path + ".bak")
        self.event("info", "Toolpath tables reset to built-in defaults (previous file kept as %s.bak)" % rel)
        return {"ok": True}

    def output_file(self, rel):
        rel = unquote(rel).replace("\\", "/").lstrip("/")
        full = os.path.realpath(os.path.join(self.paths.output, rel))
        base = os.path.realpath(self.paths.output)
        if not (full == base or full.startswith(base + os.sep)) or not os.path.isfile(full):
            raise HttpError(404, "not found")
        return full


def _check_same_site(headers):
    """Only same-origin pages (the dashboard) and scripts may change things:
    JSON bodies need a CORS preflight that is never granted, and browsers mark
    cross-site requests with Origin."""
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HttpError(415, "Content-Type must be application/json")
    origin = headers.get("Origin")
    host = headers.get("Host") or ""
    if origin and origin not in ("http://" + host, "https://" + host):
        raise HttpError(403, "cross-site request refused")


def _count_orders(folder):
    try:
        return sum(1 for n in os.listdir(folder) if n.lower().endswith(".json")
                   and not n.lower().endswith((".report.json", ".state.json")))
    except OSError:
        return 0


def _parse_content(content):
    if isinstance(content, (dict, list)):
        return content
    try:
        return json.loads(content or "")
    except ValueError as e:
        raise HttpError(400, "invalid JSON: %s" % e)


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------
def make_handler(h):
    static_dir = os.path.join(HERE, "static")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "NFEHarness/" + VERSION

        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            self._run("GET")

        def do_POST(self):
            self._run("POST")

        def do_PUT(self):
            self._run("PUT")

        def _json(self, status, payload):
            data = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _file(self, path, download_name=None):
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            with open(path, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            if download_name:
                self.send_header("Content-Disposition", 'inline; filename="%s"' % download_name)
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise HttpError(413, "body too large")
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                raise HttpError(400, "body is not JSON")

        def _run(self, method):
            try:
                url = urlparse(self.path)
                q = {k: v[-1] for k, v in parse_qs(url.query).items()}
                path = url.path
                if method == "GET" and (path == "/" or path.startswith("/static/")):
                    rel = "index.html" if path == "/" else path[len("/static/"):]
                    full = os.path.realpath(os.path.join(static_dir, rel))
                    if not full.startswith(os.path.realpath(static_dir) + os.sep) or not os.path.isfile(full):
                        raise HttpError(404, "not found")
                    return self._file(full)
                if method == "GET" and path.startswith("/docs/"):
                    return self._file(h.doc_file(path[len("/docs/"):]))
                if method == "GET" and path.startswith("/files/output/"):
                    full = h.output_file(path[len("/files/output/"):])
                    return self._file(full, os.path.basename(full))
                body = self._body() if method in ("POST", "PUT") else None
                if method in ("POST", "PUT"):
                    _check_same_site(self.headers)     # after reading: a clean 415/403, not a reset
                return self._json(200, route(h, method, path, q, body))
            except HttpError as e:
                self._json(e.status, {"error": str(e)})
            except Exception as e:
                h.log.exception("request %s", self.path)
                self._json(500, {"error": str(e), "traceback": traceback.format_exc()})

    return Handler


def route(h, method, path, q, body):
    if method == "GET":
        if path == "/api/overview":
            return h.overview(int(q.get("history", 360)))
        if path == "/api/events":
            return h.merged_events(int(q.get("since_addin", 0) or 0), int(q.get("since_harness", 0) or 0))
        if path == "/api/orders":
            return {"orders": h.orders(q.get("folder", "done"))}
        if path == "/api/orders/report":
            return h.report(q.get("folder", ""), q.get("name", ""))
        if path == "/api/config":
            return h.reload_config()
        if path == "/api/docs":
            return {"docs": h.docs()}
        if path == "/api/logic":
            return h.logic()
        if path == "/api/location":
            return h.location()
        if path == "/api/hub/hubs":
            return h.addin("/api/hub/hubs", timeout=90)
        if path == "/api/hub/projects":
            return h.addin("/api/hub/projects?hub=" + urllib.request.quote(q.get("hub", ""), safe=""), timeout=120)
        if path == "/api/hub/folder":
            return h.addin("/api/hub/folder?id=" + urllib.request.quote(q.get("id", ""), safe=""), timeout=120)
        if path == "/api/model/params":
            return h.addin("/api/model/params?id=" + urllib.request.quote(q.get("id", ""), safe=""), timeout=320)
        raise HttpError(404, "no such endpoint")

    body = body or {}
    if method == "PUT" and path == "/api/config":
        return h.save_config(body)
    if method == "PUT" and path == "/api/logic/profiles":
        return h.save_profiles(body.get("profiles"))
    if method == "POST":
        if path == "/api/orders/preview":
            return h.preview(body.get("content"))
        if path == "/api/orders/upload":
            return h.upload(body.get("name"), body.get("content"))
        if path == "/api/orders/requeue":
            return h.requeue(body.get("folder"), body.get("name", ""))
        if path == "/api/orders/remove":
            return h.remove_inbox(body.get("name", ""))
        if path == "/api/config/suggest":
            return h.suggest_rules()
        if path == "/api/location":
            return h.set_location_mode(body.get("mode"))
        if path == "/api/sync":
            return h.sync_action(body.get("action"), body.get("pair"))
        if path == "/api/logic/evaluate":
            return h.logic_evaluate(body.get("profile_id"), body.get("values"))
        if path == "/api/logic/drilling":
            return h.logic_drilling(body)
        if path == "/api/logic/reset":
            return h.reset_profiles()
        if path == "/api/control":
            action = body.get("action")
            if action in ("pause", "resume", "cancel", "gc", "reload_config"):
                return h.addin("/api/control", {"action": action}, timeout=40)
            if action == "restart_fusion":
                return {"restart": h.restart_fusion(body.get("reason") or "restart from dashboard")}
            if action == "start_fusion":
                if h.fusion.get("running"):
                    raise HttpError(409, "Fusion is already running")
                h.launch_fn()
                h.event("info", "Fusion launched from dashboard")
                return {"ok": True}
            if action == "auto_restart":
                cfg = h.reload_config()
                cfg["supervisor"]["auto_restart_fusion"] = bool(body.get("enabled"))
                h.save_config(cfg)
                return {"auto_restart": cfg["supervisor"]["auto_restart_fusion"]}
            raise HttpError(400, "unknown action %r" % action)
    raise HttpError(404, "no such endpoint")


def serve(harness, host, port):
    httpd = ThreadingHTTPServer((host, port), make_handler(harness))
    httpd.daemon_threads = True
    return httpd


def main():
    ap = argparse.ArgumentParser(description="NewFusionExtension harness")
    ap.add_argument("--port", type=int)
    ap.add_argument("--host")
    ap.add_argument("--no-supervisor", action="store_true", help="dashboard only; never restart Fusion")
    a = ap.parse_args()
    h = Harness()
    port = a.port or int(h.cfg["api"].get("harness_port", 8765))
    host = a.host or h.cfg["api"].get("harness_bind", "127.0.0.1")
    try:
        httpd = serve(h, host, port)
    except OSError as e:
        print("Harness could not bind %s:%d (%s) - already running?" % (host, port, e))
        return 1
    h.sample_once()
    if not a.no_supervisor:
        h.start_supervisor()
    h.event("info", "Harness %s listening on http://%s:%d" % (VERSION, host, port))
    print("Dashboard: http://%s:%d" % (host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        h.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
