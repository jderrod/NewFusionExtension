"""Filesystem layout of a NewFusionExtension project.

Layout (relative to the project root)::

    config/config.json      shared configuration (add-in + harness)
    dropbox/inbox           orders are dropped here
    dropbox/processing      order currently being worked on (+ checkpoint)
    dropbox/done            finished orders (+ report)
    dropbox/failed          orders with at least one failed component (+ report)
    output/<order_id>/      PDFs and report.json per order
    state/status.json       live status written by the add-in
    state/events.jsonl      append-only event log written by the add-in
    logs/                   text logs
"""

import os

# <root>/addin/NewFusionExtension/nfe/paths.py
ADDIN_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def find_project_root():
    env = os.environ.get("NFE_HOME")
    if env:
        return os.path.abspath(env)
    candidate = os.path.dirname(os.path.dirname(ADDIN_DIR))
    if os.path.isdir(os.path.join(candidate, "config")):
        return candidate
    # The add-in folder may have been copied (not junctioned) into Fusion's
    # AddIns directory; the installer leaves a pointer file behind.
    home_file = os.path.join(ADDIN_DIR, "nfe_home.txt")
    if os.path.isfile(home_file):
        with open(home_file, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    return candidate


class Paths(object):
    def __init__(self, root=None, dropbox=None, output=None):
        self.root = os.path.abspath(root or find_project_root())
        self.config_dir = os.path.join(self.root, "config")
        self.config_file = os.path.join(self.config_dir, "config.json")
        self.dropbox = os.path.abspath(dropbox) if dropbox else os.path.join(self.root, "dropbox")
        self.inbox = os.path.join(self.dropbox, "inbox")
        self.processing = os.path.join(self.dropbox, "processing")
        self.done = os.path.join(self.dropbox, "done")
        self.failed = os.path.join(self.dropbox, "failed")
        self.output = os.path.abspath(output) if output else os.path.join(self.root, "output")
        self.state = os.path.join(self.root, "state")
        self.status_file = os.path.join(self.state, "status.json")
        self.events_file = os.path.join(self.state, "events.jsonl")
        self.logs = os.path.join(self.root, "logs")

    @classmethod
    def from_config(cls, root, cfg):
        p = cfg.get("paths", {}) if cfg else {}
        inst = cls(root, dropbox=p.get("dropbox") or None, output=p.get("output") or None)
        inst.apply_location(cfg)
        return inst

    def apply_location(self, cfg):
        """Point the inbox at the location mode's inbox (in place, so every
        holder of this Paths object sees a mode switch).  Processing, done,
        failed and output always stay local."""
        loc = (cfg or {}).get("location") or {}
        self.mode = loc.get("mode", "local")
        inbox = ((loc.get(self.mode) or {}).get("inbox") or "").strip()
        default_inbox = os.path.join(self.dropbox, "inbox")
        if not inbox:
            self.inbox = default_inbox
        elif os.path.isabs(inbox) or inbox.startswith("\\\\"):
            self.inbox = inbox
        else:
            self.inbox = os.path.join(self.root, inbox)
        self.inbox_is_local = not self.inbox.startswith("\\\\")
        return self

    def ensure(self):
        if getattr(self, "inbox_is_local", True):
            os.makedirs(self.inbox, exist_ok=True)     # never try to create a network inbox
        for d in (self.config_dir, self.processing, self.done, self.failed,
                  self.output, self.state, self.logs):
            os.makedirs(d, exist_ok=True)
        return self
