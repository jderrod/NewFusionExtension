"""Everything the add-in runs inside Fusion, wired together.

The stable entry file (NewFusionExtension.py) owns the CustomEvent and the
ticker thread and calls ``AddinApp.tick()`` on the UI thread; this module can be
reloaded without restarting Fusion.
"""

import contextlib
import gc
import io
import logging
import logging.handlers
import os
import shutil
import socket
import sys
import threading
import time
import traceback

import adsk.core

from . import VERSION, winproc
from . import config as config_mod
from .api_server import ApiServer
from .dropbox import Dropbox
from .engine import Engine
from .fusion_ops import FusionOps
from .paths import Paths, find_project_root
from .state import StateStore

HEARTBEAT_S = 2.0


def _setup_logging(logs_dir):
    log = logging.getLogger("nfe")
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    os.makedirs(logs_dir, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(os.path.join(logs_dir, "addin.log"),
                                             maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False
    return log


class _LoggingStateStore(StateStore):
    """Mirror events into logs/addin.log."""

    def __init__(self, log, *a, **kw):
        StateStore.__init__(self, *a, **kw)
        self._log = log

    def event(self, level, message, **data):
        lvl = {"debug": logging.DEBUG, "info": logging.INFO, "warn": logging.WARNING,
               "error": logging.ERROR}.get(level, logging.INFO)
        self._log.log(lvl, message + (" | %s" % data if data and level == "error" else ""))
        return StateStore.event(self, level, message, **data)


class AddinApp(object):
    def __init__(self, app, host=None):
        self.app = app
        self.host = host
        root = find_project_root()
        base = Paths(root)
        base.ensure()
        config_mod.ensure_exists(base.config_file)
        self._config_file = base.config_file
        self.cfg = config_mod.load(base.config_file)
        self.paths = Paths.from_config(root, self.cfg).ensure()
        self.log = _setup_logging(self.paths.logs)
        self.state = _LoggingStateStore(self.log, self.paths.status_file, self.paths.events_file)
        self.ops = FusionOps(app, lambda: self.cfg)
        self.engine = Engine(self.ops, self.paths, self.state, self._load_config)
        self.dropbox = Dropbox(self.paths)
        self.api = ApiServer(self.engine, self.ops, self.state, lambda: self.cfg,
                             int(self.cfg["api"]["addin_port"]), dev_exec=self._dev_exec,
                             reload_cb=self._request_reload, extra_status=self._extra_status)
        self._stop = threading.Event()
        self._hb_thread = None
        self._last_health = {}

    def _load_config(self):
        self.cfg = config_mod.load(self._config_file)
        if getattr(self, "paths", None) is not None:
            self.paths.apply_location(self.cfg)
        return self.cfg

    # ------------------------------------------------------------------
    def start(self):
        self.ops.begin_session()
        try:
            self.api.start()
        except OSError as e:
            self.state.event("error", "Control API could not bind port %s: %s"
                             % (self.cfg["api"]["addin_port"], e))
        self.state.update(addin_version=VERSION, addin_pid=os.getpid(),
                          addin_started_at=time.time(), project_root=self.paths.root,
                          heartbeat_at=time.time())
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, name="nfe-heartbeat", daemon=True)
        self._hb_thread.start()
        # Inbox scanning has its own thread: listing an unreachable network
        # inbox (remote mode) can hang for ~20 s and must never delay the heartbeat.
        self._inbox_thread = threading.Thread(target=self._inbox_loop, name="nfe-inbox", daemon=True)
        self._inbox_thread.start()
        self.state.event("info", "Add-in %s started in Fusion (pid %d)%s" % (
            VERSION, os.getpid(), "" if not self.engine.paused else " - paused (autostart off)"))
        self._maybe_launch_harness()

    def stop(self):
        self._stop.set()
        try:
            self.engine.shutdown()
        except Exception:
            self.log.exception("engine shutdown")
        self.api.stop()
        if self._hb_thread is not None:
            self._hb_thread.join(3.0)
        self.state.update(state="stopped", heartbeat_at=time.time())
        self.state.event("info", "Add-in stopped")
        for h in list(self.log.handlers):
            self.log.removeHandler(h)
            h.close()

    def wants_tick(self):
        return self.engine.has_work()

    def tick(self):
        self.engine.tick()

    # ------------------------------------------------------------------
    def _heartbeat_loop(self):
        """Background thread: file I/O + Win32 only, never the Fusion API."""
        n = 0
        while not self._stop.wait(HEARTBEAT_S):
            n += 1
            try:
                fields = {"heartbeat_at": time.time()}
                started = self.engine.tick_started_at
                fields["ui_blocked_s"] = round(time.time() - started, 1) if started else 0
                if n % 3 == 0:
                    info = winproc.own_process_info() or {}
                    h = dict(self.state.get("health") or {})
                    h.update(threads=info.get("threads"), working_set_mb=info.get("working_set_mb"),
                             handles=info.get("handles"), at=time.time())
                    fields["health"] = h
                self.state.update(**fields)
            except Exception:
                self.log.exception("heartbeat")

    def _inbox_loop(self):
        """Background thread: file I/O only."""
        while not self._stop.wait(HEARTBEAT_S):
            try:
                t0 = time.time()
                has = bool(self.dropbox.inbox_files(raise_errors=True))
                self.engine.set_inbox_hint(has)
                self.state.update(flush=False, inbox_reachable=True, inbox_scan_s=round(time.time() - t0, 2))
            except Exception as e:
                self.state.update(flush=False, inbox_reachable=False, inbox_error=str(e))

    def _extra_status(self):
        return {"has_work": self.engine.has_work(), "api_port": self.api.port}

    def _request_reload(self):
        if self.host is not None:
            self.host.reload_requested = True

    def _dev_exec(self, code):
        """Run Python on the UI thread (development only; see api.dev_exec)."""
        import adsk.cam
        import adsk.drawing
        import adsk.fusion
        g = {"app": self.app, "adsk": adsk, "ui": self.app.userInterface, "engine": self.engine,
             "ops": self.ops, "state": self.state, "cfg": self.cfg, "paths": self.paths, "result": None}
        buf = io.StringIO()
        t0 = time.time()
        try:
            with contextlib.redirect_stdout(buf):
                exec(compile(code, "<dev-exec>", "exec"), g)
            err = None
        except Exception:
            err = traceback.format_exc()
        out = {"stdout": buf.getvalue()[-200000:], "result": g.get("result"), "error": err,
               "elapsed_s": round(time.time() - t0, 2)}
        try:
            import json
            json.dumps(out["result"])
        except Exception:
            out["result"] = repr(out["result"])
        return out

    def _maybe_launch_harness(self):
        api = self.cfg.get("api", {})
        if not api.get("launch_harness_from_addin"):
            return
        port = int(api.get("harness_port", 8765))
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return          # already running
        except OSError:
            pass
        server = os.path.join(self.paths.root, "harness", "server.py")
        py = api.get("harness_python") or shutil.which("pyw") or shutil.which("pythonw")
        if not py or not os.path.isfile(server):
            self.state.event("warn", "Harness not started: no pythonw/pyw found (set api.harness_python)")
            return
        args = [py]
        if os.path.basename(py).lower().startswith("pyw"):
            args.append("-3")
        args.append(server)
        try:
            winproc.launch_detached(args, cwd=self.paths.root)
            self.state.event("info", "Started harness dashboard on port %d" % port)
        except Exception as e:
            self.state.event("warn", "Could not start harness: %s" % e)
