"""NewFusionExtension - Fusion add-in entry point.

This file is intentionally tiny and stable.  It owns:

* the CustomEvent used to get onto Fusion's UI thread, and
* a background "ticker" thread that fires that event whenever the add-in has
  work (at most one event outstanding, so Fusion's queue never floods).

All real work lives in ``nfe.addin_main.AddinApp``, which can be reloaded in
place (POST /api/dev/reload) without restarting Fusion.
"""

import os
import sys
import threading
import time
import traceback
import uuid

import adsk.core

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

EVENT_PREFIX = "NewFusionExtension_tick_"
TICK_INTERVAL_S = 0.2
REFIRE_AFTER_S = 30.0      # if an event was lost, fire again after this long

_host = None


def _write_crash(text):
    try:
        root = os.path.dirname(os.path.dirname(HERE))
        logs = os.path.join(root, "logs")
        os.makedirs(logs, exist_ok=True)
        with open(os.path.join(logs, "addin_crash.log"), "a", encoding="utf-8") as fh:
            fh.write("%s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except Exception:
        pass


class _TickHandler(adsk.core.CustomEventHandler):
    def __init__(self, host):
        super().__init__()
        self.host = host

    def notify(self, args):
        try:
            self.host.on_tick()
        except Exception:
            _write_crash(traceback.format_exc())


class Host(object):
    def __init__(self):
        self.app = adsk.core.Application.get()
        self.impl = None
        self.event = None
        self.handler = None
        self.pending = False
        self.fired_at = 0.0
        self.reload_requested = False
        self.stop_event = threading.Event()
        self.thread = None
        # Unique per instance: two instances must never share (or unregister)
        # each other's event.
        self.event_id = EVENT_PREFIX + uuid.uuid4().hex[:12]

    def is_alive(self):
        return self.thread is not None and self.thread.is_alive() and not self.stop_event.is_set()

    def start(self):
        self.event = self.app.registerCustomEvent(self.event_id)
        self.handler = _TickHandler(self)
        self.event.add(self.handler)
        self._load_impl()
        self.thread = threading.Thread(target=self._ticker, name="nfe-ticker", daemon=True)
        self.thread.start()
        sys._nfe_active_host = self

    def _load_impl(self):
        for name in [m for m in sys.modules if m == "nfe" or m.startswith("nfe.")]:
            del sys.modules[name]
        from nfe import addin_main
        impl = addin_main.AddinApp(self.app, host=self)
        impl.start()
        self.impl = impl

    def _ticker(self):
        while not self.stop_event.wait(TICK_INTERVAL_S):
            try:
                impl = self.impl
                want = self.reload_requested or (impl is not None and impl.wants_tick())
                now = time.time()
                if want and (not self.pending or now - self.fired_at > REFIRE_AFTER_S):
                    self.pending = True
                    self.fired_at = now
                    self.app.fireCustomEvent(self.event_id, "")
            except Exception:
                _write_crash(traceback.format_exc())
                time.sleep(1.0)

    def on_tick(self):
        self.pending = False
        if self.reload_requested:
            self.reload_requested = False
            old, self.impl = self.impl, None
            if old is not None:
                try:
                    old.stop()
                except Exception:
                    _write_crash(traceback.format_exc())
            try:
                self._load_impl()
            except Exception:
                _write_crash("reload failed:\n" + traceback.format_exc())
            return
        if self.impl is not None:
            self.impl.tick()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(2.0)
        if self.impl is not None:
            try:
                self.impl.stop()
            except Exception:
                _write_crash(traceback.format_exc())
            self.impl = None
        try:
            if self.event is not None and self.handler is not None:
                self.event.remove(self.handler)
        except Exception:
            pass
        try:
            self.app.unregisterCustomEvent(self.event_id)
        except Exception:
            pass
        if getattr(sys, "_nfe_active_host", None) is self:
            sys._nfe_active_host = None


def run(context):
    global _host
    # Fusion can register the same add-in twice (e.g. through the AddIns
    # junction and through its real path).  Only one instance may run.
    active = getattr(sys, "_nfe_active_host", None)
    if active is not None and active.is_alive():
        _write_crash("second NewFusionExtension instance ignored (%s); one is already running" % __file__)
        return
    try:
        _host = Host()
        _host.start()
    except Exception:
        text = traceback.format_exc()
        _write_crash(text)
        try:
            adsk.core.Application.get().userInterface.messageBox(
                "NewFusionExtension failed to start:\n\n" + text)
        except Exception:
            pass


def stop(context):
    global _host
    try:
        if _host is not None:
            _host.stop()
    except Exception:
        _write_crash(traceback.format_exc())
    _host = None
