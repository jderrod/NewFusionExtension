"""Localhost control API served by the add-in (127.0.0.1 only).

Handlers run on HTTP worker threads; anything that touches Fusion is marshalled
onto the UI thread with ``engine.submit()``.

    GET  /api/ping
    GET  /api/status
    GET  /api/events?since=<seq>
    GET  /api/hub/hubs
    GET  /api/hub/projects?hub=<id or name>
    GET  /api/hub/folder?id=<folder id>
    GET  /api/model/params?id=<file id>
    POST /api/control         {"action": "pause|resume|cancel|reload_config|gc"}
    POST /api/dev/exec        {"code": "..."}      (only when api.dev_exec is true)
    POST /api/dev/reload                           (reload add-in code)
"""

import json
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import VERSION

MAX_BODY = 2 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, status, message):
        Exception.__init__(self, message)
        self.status = status


def _check_same_site(headers):
    """Refuse requests a web page in a local browser could forge.

    Browsers only send application/json cross-origin after a CORS preflight,
    which this server never grants, and they always label cross-site requests
    with an Origin header."""
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise ApiError(415, "Content-Type must be application/json")
    origin = headers.get("Origin")
    if origin and not (origin.startswith("http://127.0.0.1:") or origin.startswith("http://localhost:")):
        raise ApiError(403, "cross-site request refused")


class ApiServer(object):
    def __init__(self, engine, ops, state, get_config, port, dev_exec=None, reload_cb=None,
                 extra_status=None):
        self.engine = engine
        self.ops = ops
        self.state = state
        self.get_config = get_config
        self.port = port
        self.dev_exec = dev_exec
        self.reload_cb = reload_cb
        self.extra_status = extra_status
        self.started_at = time.time()
        self._httpd = None
        self._thread = None

    # ------------------------------------------------------------------
    def start(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):   # keep Fusion's console quiet
                pass

            def do_GET(self):
                self._dispatch("GET")

            def do_POST(self):
                self._dispatch("POST")

            def _dispatch(self, method):
                try:
                    url = urlparse(self.path)
                    q = {k: v[-1] for k, v in parse_qs(url.query).items()}
                    body = None
                    if method == "POST":
                        n = int(self.headers.get("Content-Length") or 0)
                        if n > MAX_BODY:
                            raise ApiError(413, "body too large")
                        raw = self.rfile.read(n) if n else b""
                        _check_same_site(self.headers)   # after reading: a clean 415/403, not a reset
                        body = json.loads(raw.decode("utf-8")) if raw else {}
                    status, payload = api.route(method, url.path, q, body)
                except ApiError as e:
                    status, payload = e.status, {"error": str(e)}
                except TimeoutError as e:
                    status, payload = 503, {"error": str(e)}
                except Exception as e:
                    status, payload = 500, {"error": str(e), "traceback": traceback.format_exc()}
                data = json.dumps(payload, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

        ThreadingHTTPServer.allow_reuse_address = False
        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="nfe-api", daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd is not None:
            t = threading.Thread(target=self._httpd.shutdown, daemon=True)
            t.start()
            t.join(3.0)
            try:
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None

    # ------------------------------------------------------------------
    def route(self, method, path, q, body):
        eng = self.engine
        if method == "GET":
            if path == "/api/ping":
                return 200, {"ok": True, "version": VERSION, "started_at": self.started_at}
            if path == "/api/status":
                snap = self.state.snapshot()
                if self.extra_status:
                    snap.update(self.extra_status())
                return 200, snap
            if path == "/api/events":
                return 200, {"events": self.state.recent_events(int(q.get("since", 0) or 0))}
            if path == "/api/hub/hubs":
                return 200, {"hubs": eng.submit(lambda: self.ops.hub_list("hubs"), timeout=60)}
            if path == "/api/hub/projects":
                hub = q.get("hub") or ""
                return 200, {"projects": eng.submit(lambda: self.ops.hub_list("projects", hub), timeout=90)}
            if path == "/api/hub/folder":
                fid = q.get("id") or ""
                if not fid:
                    raise ApiError(400, "id is required")
                return 200, eng.submit(lambda: self.ops.hub_list("folder", fid), timeout=90)
            if path == "/api/model/params":
                fid = q.get("id") or ""
                if not fid:
                    raise ApiError(400, "id is required")
                if eng.busy():
                    raise ApiError(409, "busy processing an order; try again when idle")
                return 200, eng.submit(lambda: self.ops.inspect_model({"id": fid}), timeout=300)
            raise ApiError(404, "no such endpoint")

        if path == "/api/control":
            action = (body or {}).get("action")
            if action == "pause":
                eng.submit(lambda: eng.pause((body or {}).get("reason") or "paused from dashboard"), 30)
            elif action == "resume":
                eng.submit(eng.resume, 30)
            elif action == "cancel":
                return 200, {"ok": eng.submit(eng.cancel_order, 30)}
            elif action == "reload_config":
                eng.submit(eng.reload_config, 30)
            elif action == "gc":
                if eng.busy():
                    raise ApiError(409, "busy processing; leftover documents are closed after every component")
                eng.submit(eng.request_garbage_collection, 30)
            else:
                raise ApiError(400, "unknown action %r" % action)
            return 200, {"ok": True}
        if path == "/api/dev/exec":
            if not self.get_config().get("api", {}).get("dev_exec") or self.dev_exec is None:
                raise ApiError(403, "dev exec is disabled (api.dev_exec in config.json)")
            code = (body or {}).get("code") or ""
            timeout = float((body or {}).get("timeout") or 300)
            return 200, eng.submit(lambda: self.dev_exec(code), timeout=timeout)
        if path == "/api/dev/reload":
            if not self.get_config().get("api", {}).get("dev_exec"):
                raise ApiError(403, "reload is a development action (api.dev_exec in config.json)")
            if self.reload_cb is None:
                raise ApiError(501, "reload not available")
            self.reload_cb()
            return 202, {"ok": True, "message": "reload scheduled"}
        raise ApiError(404, "no such endpoint")
