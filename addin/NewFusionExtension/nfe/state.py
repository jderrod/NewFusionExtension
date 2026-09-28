"""Live status + event log shared with the harness through files.

* ``state/status.json``  - snapshot, rewritten atomically (throttled)
* ``state/events.jsonl`` - append-only, one JSON object per line, rotated

Both are written from the Fusion UI thread and from the add-in's heartbeat
thread, so everything goes through one lock.
"""

import collections
import copy
import json
import os
import threading
import time

from .jsonio import write_json_atomic

MAX_EVENTS_BYTES = 5 * 1024 * 1024


class StateStore(object):
    def __init__(self, status_file, events_file, clock=time.time, ring_size=500):
        self.status_file = status_file
        self.events_file = events_file
        self._clock = clock
        self._lock = threading.RLock()
        self._status = {}
        self._events = collections.deque(maxlen=ring_size)
        self._seq = self._last_seq_on_disk()
        self._last_write = 0.0
        self._dirty = False

    # -- status ---------------------------------------------------------
    def update(self, flush=True, **fields):
        with self._lock:
            self._status.update(fields)
            self._dirty = True
            if flush:
                self._write_locked()

    def get(self, key, default=None):
        with self._lock:
            return self._status.get(key, default)

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._status)

    def flush(self, min_interval=0.0):
        with self._lock:
            if self._dirty and self._clock() - self._last_write >= min_interval:
                self._write_locked()

    def _write_locked(self):
        self._status["written_at"] = self._clock()
        try:
            write_json_atomic(self.status_file, self._status)
            self._dirty = False
            self._last_write = self._clock()
        except OSError:
            pass  # a reader holding the file; next flush retries

    # -- events ---------------------------------------------------------
    def event(self, level, message, **data):
        with self._lock:
            self._seq += 1
            ev = {"seq": self._seq, "ts": self._clock(), "level": level, "msg": message}
            if data:
                ev["data"] = data
            self._events.append(ev)
            try:
                self._rotate_if_needed()
                with open(self.events_file, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(ev, default=str) + "\n")
            except OSError:
                pass
            return ev

    def recent_events(self, since_seq=0, limit=200):
        with self._lock:
            out = [e for e in self._events if e["seq"] > since_seq]
        return out[-limit:]

    def _rotate_if_needed(self):
        try:
            if os.path.getsize(self.events_file) > MAX_EVENTS_BYTES:
                old = self.events_file + ".1"
                if os.path.exists(old):
                    os.remove(old)
                os.replace(self.events_file, old)
        except OSError:
            pass

    def _last_seq_on_disk(self):
        """Continue numbering across add-in restarts so readers never go backwards."""
        try:
            with open(self.events_file, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 8192))
                tail = fh.read().decode("utf-8", "replace").strip().splitlines()
            for line in reversed(tail):
                try:
                    return int(json.loads(line)["seq"])
                except (ValueError, KeyError, TypeError):
                    continue
        except OSError:
            pass
        return 0


def read_events_file(path, since_seq=0, limit=300):
    """Tail-read events from disk (used by the harness)."""
    out = []
    for p in (path + ".1", path):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("seq", 0) > since_seq:
                        out.append(ev)
        except OSError:
            continue
    return out[-limit:]
