"""Mirror local output folders to the network shares (runs in the harness).

Port of the old pipeline's ``file_sync_service.py``.  Fusion (the add-in)
only ever writes to local disk; this module, running on a background thread
of the harness process, copies the results to the ddc-mefs shares.  In
"local" location mode the "shares" are folders under
``<root>/<mirror_root>`` (default ``network_mirror/``), so a dev machine
exercises exactly the same code without touching the network.

Guarantees (kept from the old service):

* **Local files are never modified or deleted.**  Local disk is the archive
  of record.
* **Settle time**: a file is copied only once it has been unmodified for
  ``settle_s`` seconds, so a file still being written is never picked up.
* **Atomic copy**: each file is copied to ``<name>.syncpart`` in the
  destination folder and then renamed into place, so a machine controller
  polling a share sees a file complete or not at all ("*.txt" does not
  match "x.txt.syncpart").
* **Sent once**: a persistent state file records (size, mtime) of every file
  already copied.  A file is re-copied only when it changes locally.  Whether
  the file still exists at the destination is deliberately NOT checked: the
  machines consume (delete) files from their drop folders, so "missing at the
  destination" is the normal end state - re-copying on absence would resend
  every consumed program forever.  ``resync()`` forgets the record when a
  share really has to be rebuilt.
* **Pruning**: state entries for local files that no longer exist are
  dropped, so the state file does not grow forever.
* **Isolation**: an unreachable share only affects its own pair; its files
  stay unsynced and are retried next pass.  Nothing here raises for a
  missing or unreachable destination.
* **Single instance**: a lock file next to the state file stops two sync
  services (e.g. two harnesses) from fighting over the same state file.

Only the standard library is used; nothing here imports Fusion.
"""

import fnmatch
import os
import shutil
import tempfile
import threading
import time

from .jsonio import read_json, write_json_atomic

TEMP_SUFFIX = ".syncpart"
DEFAULT_INTERVAL_S = 15
DEFAULT_SETTLE_S = 5
DEFAULT_PROBE_TIMEOUT_S = 5.0
DEFAULT_MIRROR_ROOT = "network_mirror"

# File names never synced (temporary files of the add-in and of this module).
EXCLUDED_PREFIXES = ("_partial_", ".tmp_", ".nfe_probe_")
EXCLUDED_SUFFIXES = (TEMP_SUFFIX, ".claiming")
# Folder names never descended into (per-setup .nc scratch of the post step).
EXCLUDED_DIRS = ("_nc",)

# (share key, local source relative to the project root, recursive, pattern)
LOCAL_SOURCES = (
    ("gcode", "machine_drops/anderson", False, "*.txt"),
    ("drilling", "machine_drops/gannomat", False, "*.json"),
    ("output", "output", True, None),
    ("order_completed", "dropbox/done", True, None),
    ("order_failed", "dropbox/failed", True, None),
)
SHARE_KEYS = tuple(s[0] for s in LOCAL_SOURCES)

DEFAULT_REMOTE_SHARES = {
    "gcode": "\\\\ddc-mefs\\Anderson",
    "drilling": "\\\\ddc-mefs\\Gannomat\\F360_output",
    "output": "\\\\ddc-mefs\\Fusion\\Fusion Folders\\output",
    "order_completed": "\\\\ddc-mefs\\Fusion\\Fusion Folders\\order_completed",
    "order_failed": "\\\\ddc-mefs\\Fusion\\Fusion Folders\\order_failed",
}


# ---------------------------------------------------------------------------
# configuration -> pairs

def _mode(location_cfg):
    mode = str((location_cfg or {}).get("mode") or "local").strip().lower()
    return "remote" if mode == "remote" else "local"


def _abs(root, path):
    path = (path or "").strip()
    if not path:
        return ""
    if _is_unc(path) or os.path.isabs(path):
        return os.path.normpath(path) if not _is_unc(path) else path.rstrip("\\/")
    return os.path.normpath(os.path.join(root, path))


def _is_unc(path):
    return path.startswith("\\\\") or path.startswith("//")


def _share_setting(location_cfg, key):
    """The configured share for ``key``; None = not configured, "" = disabled."""
    shares = ((location_cfg or {}).get("remote") or {}).get("shares")
    if not isinstance(shares, dict):
        return DEFAULT_REMOTE_SHARES.get(key)
    if key not in shares:
        return DEFAULT_REMOTE_SHARES.get(key)
    return (shares.get(key) or "").strip()


def build_pairs(root, location_cfg, sources=None):
    """Return the local -> destination pairs for the configured location mode.

    Each pair is ``{"name", "source", "dest", "recursive", "pattern"}``.
    Sources are always local (relative to ``root``; ``sources`` may override
    them per share key).  In "local" mode each destination is
    ``<root>/<mirror_root>/<share key>``; in "remote" mode it is the share
    path.  A share set to "" disables that pair in both modes, so a local run
    syncs exactly the pairs production would.
    """
    root = os.path.abspath(root)
    loc = location_cfg or {}
    mode = _mode(loc)
    mirror_root = _abs(root, ((loc.get("local") or {}).get("mirror_root") or DEFAULT_MIRROR_ROOT))
    pairs = []
    for key, rel_source, recursive, pattern in LOCAL_SOURCES:
        share = _share_setting(loc, key)
        if share == "":
            continue                                  # disabled
        if mode == "remote":
            if not share:
                continue
            dest = _abs(root, share)
        else:
            dest = os.path.join(mirror_root, key)
        source = _abs(root, (sources or {}).get(key) or rel_source)
        pairs.append({"name": key, "source": source, "dest": dest,
                      "recursive": recursive, "pattern": pattern})
    return pairs


def disabled_pairs(location_cfg):
    return [k for k in SHARE_KEYS if _share_setting(location_cfg, k) == ""]


def resolve_inbox(root, location_cfg):
    """The inbox of the active mode ("" = the project's dropbox/inbox)."""
    loc = location_cfg or {}
    inbox = ((loc.get(_mode(loc)) or {}).get("inbox") or "").strip()
    if not inbox:
        return os.path.join(os.path.abspath(root), "dropbox", "inbox")
    return _abs(os.path.abspath(root), inbox)


# ---------------------------------------------------------------------------
# persistent record of what has been copied

class SyncState(object):
    """What has already been copied, keyed by ``"<pair>|<relative/path>"``.

    An entry stores the source file's size and mtime_ns: a file that is later
    rewritten is copied again, an untouched one never is.  Thread-safe.
    """

    VERSION = 1

    def __init__(self, path, log=None):
        self.path = path
        self._log = log or (lambda level, msg: None)
        self._lock = threading.RLock()
        self.entries = {}
        self.dirty = False
        self.load_error = None
        self.load()

    @staticmethod
    def key(pair, rel):
        return "%s|%s" % (pair, rel.replace("\\", "/"))

    def load(self):
        with self._lock:
            self.entries, self.dirty, self.load_error = {}, False, None
            if not self.path or not os.path.exists(self.path):
                return self
            data = read_json(self.path)
            if not isinstance(data, dict):
                # A corrupt state file must not wedge the service.  Worst case
                # files are copied again, which is harmless for the archive
                # shares (the machine drops may see a program twice).
                self.load_error = "unreadable sync state %s; starting fresh" % self.path
                self._log("warn", self.load_error)
                return self
            entries = data.get("entries") if "entries" in data else data   # old flat format
            if isinstance(entries, dict):
                self.entries = dict((k, v) for k, v in entries.items()
                                    if isinstance(v, dict) and "|" in k)
            return self

    def save(self):
        with self._lock:
            if not self.dirty or not self.path:
                return True
            try:
                write_json_atomic(self.path, {"version": self.VERSION, "entries": self.entries},
                                  indent=1)
                self.dirty = False
                return True
            except (OSError, ValueError) as e:
                self._log("warn", "Could not save sync state %s: %s" % (self.path, e))
                return False

    def is_synced(self, pair, rel, st):
        with self._lock:
            e = self.entries.get(self.key(pair, rel))
            return (e is not None and e.get("size") == st.st_size
                    and e.get("mtime_ns") == st.st_mtime_ns)

    def mark(self, pair, rel, st, dest=None):
        with self._lock:
            self.entries[self.key(pair, rel)] = {
                "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "dest": dest or ""}
            self.dirty = True

    def prune(self, pair, live_rels):
        """Drop ``pair``'s entries whose local file no longer exists."""
        prefix = pair + "|"
        live = set(self.key(pair, r) for r in live_rels)
        with self._lock:
            stale = [k for k in self.entries if k.startswith(prefix) and k not in live]
            for k in stale:
                del self.entries[k]
            if stale:
                self.dirty = True
            return len(stale)

    def clear(self, pair=None):
        """Forget what was copied (all pairs, or one).  Returns entries removed."""
        with self._lock:
            if pair is None:
                n = len(self.entries)
                self.entries = {}
            else:
                keys = [k for k in self.entries if k.startswith(pair + "|")]
                for k in keys:
                    del self.entries[k]
                n = len(keys)
            if n:
                self.dirty = True
            return n

    def count(self, pair=None):
        with self._lock:
            if pair is None:
                return len(self.entries)
            prefix = pair + "|"
            return sum(1 for k in self.entries if k.startswith(prefix))


# ---------------------------------------------------------------------------
# copying

def _replace_with_retry(src, dst, attempts=10, delay=0.1):
    # On Windows os.replace fails while a reader (a machine controller) has
    # the target open; retry briefly, then let the caller retry next pass.
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


def copy_atomic(src, dest):
    """Copy ``src`` to ``dest`` via ``dest + ".syncpart"`` and an atomic rename."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + TEMP_SUFFIX
    try:
        shutil.copy2(src, tmp)
        _replace_with_retry(tmp, dest)
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def _excluded_name(name):
    low = name.lower()
    return low.startswith(EXCLUDED_PREFIXES) or low.endswith(EXCLUDED_SUFFIXES)


def _matches(name, pattern):
    if not pattern:
        return True
    pats = pattern if isinstance(pattern, (list, tuple)) else str(pattern).split(";")
    return any(fnmatch.fnmatch(name, p.strip()) for p in pats if p.strip())


def iter_source_files(source, recursive, pattern=None):
    """Yield ``(full_path, rel_path, stat)`` of candidate files.

    Raises OSError when the source exists but cannot be listed (so the caller
    does not prune state on a transient error).  A missing source yields
    nothing.
    """
    if not os.path.isdir(source):
        return
    errors = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(source, onerror=errors.append):
            dirnames[:] = sorted(d for d in dirnames
                                 if d.lower() not in EXCLUDED_DIRS and not _excluded_name(d))
            for name in sorted(filenames):
                if _excluded_name(name) or not _matches(name, pattern):
                    continue
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                yield full, os.path.relpath(full, source).replace("\\", "/"), st
    else:
        with os.scandir(source) as it:
            entries = sorted(it, key=lambda e: e.name)
        for e in entries:
            if _excluded_name(e.name) or not _matches(e.name, pattern):
                continue
            try:
                if not e.is_file():
                    continue
                st = os.stat(e.path)
            except OSError:
                continue
            yield e.path, e.name, st
    if errors:
        raise errors[0]


# ---------------------------------------------------------------------------
# bounded filesystem probes (an unreachable UNC path can block for ~20 s)

class _Probe(object):
    def __init__(self):
        self.done = threading.Event()
        self.result = None


_inflight = {}
_inflight_lock = threading.Lock()


def _start_probe(key, fn):
    """Run ``fn`` in a daemon thread.  A probe still running for the same key
    (a hung UNC lookup) is reused instead of starting another thread, so a
    dead share never piles up threads."""
    with _inflight_lock:
        p = _inflight.get(key)
        if p is not None:
            return p
        p = _Probe()
        _inflight[key] = p

    def run():
        try:
            p.result = fn()
        except Exception as e:                       # noqa: BLE001 - reported as result
            p.result = e
        finally:
            with _inflight_lock:
                if _inflight.get(key) is p:
                    del _inflight[key]
            p.done.set()

    threading.Thread(target=run, name="nfe-sync-probe", daemon=True).start()
    return p


def _call_with_timeout(key, fn, timeout):
    """(finished, result) - result may be an exception instance."""
    p = _start_probe(key, fn)
    if p.done.wait(max(0.0, timeout)):
        return True, p.result
    return False, None


def _anchor(path):
    """``\\\\server\\share`` for a UNC path, ``X:\\`` for a drive path, else None."""
    drive, _ = os.path.splitdrive(path)
    if _is_unc(path):
        return drive if drive and len(drive.strip("\\/").replace("/", "\\").split("\\")) >= 2 else ""
    if drive:
        return drive + os.sep
    return None


def _probe_dir(path):
    """Does ``path`` exist as a directory, and can a file be created in it?

    The write probe is a ``.nfe_probe_*.syncpart`` file that is removed at
    once; no machine pattern matches it.
    """
    if not os.path.isdir(path):
        return {"exists": False, "writable": False}
    try:
        fd, tmp = tempfile.mkstemp(prefix=".nfe_probe_", suffix=TEMP_SUFFIX, dir=path)
        os.close(fd)
        os.remove(tmp)
        return {"exists": True, "writable": True}
    except OSError as e:
        return {"exists": True, "writable": False, "error": str(e)}


def _dest_problem(dest, probe_timeout, anchors):
    """None if ``dest`` is usable (created if needed), else a reason string.

    For a UNC path the share root must already exist: creating the
    subfolders of a missing share must never "succeed" somewhere else.
    ``anchors`` caches anchor results for one pass.
    """
    anchor = _anchor(dest)
    if anchor == "":
        return "invalid network path %s (expected \\\\server\\share\\...)" % dest
    if anchor:
        if anchor not in anchors:
            done, res = _call_with_timeout(("isdir", anchor), lambda: os.path.isdir(anchor),
                                           probe_timeout)
            if not done:
                anchors[anchor] = "%s did not respond within %.0f s" % (anchor, probe_timeout)
            elif res is True:
                anchors[anchor] = None
            elif isinstance(res, Exception):
                anchors[anchor] = "%s: %s" % (anchor, res)
            else:
                anchors[anchor] = "%s not found" % anchor
        if anchors[anchor]:
            return anchors[anchor]
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as e:
        return "cannot create %s: %s" % (dest, e)
    return None


def _inside(child, parent):
    child = os.path.normcase(os.path.abspath(child))
    parent = os.path.normcase(os.path.abspath(parent))
    return child == parent or child.startswith(parent.rstrip("\\/") + os.sep)


# ---------------------------------------------------------------------------
# one pass

def sync_pair(pair, state, settle_s, now, probe_timeout=DEFAULT_PROBE_TIMEOUT_S,
              should_stop=None, anchors=None):
    """Copy every new, settled file of one pair.  Never raises for I/O."""
    name, source, dest = pair["name"], pair["source"], pair["dest"]
    res = {"name": name, "source": source, "dest": dest, "reachable": False,
           "copied": [], "pending": 0, "errors": []}
    anchors = {} if anchors is None else anchors
    if not _is_unc(dest) and _inside(dest, source):
        res["errors"].append("destination %s is inside the source %s" % (dest, source))
        return res

    todo, live = [], []
    try:
        for full, rel, st in iter_source_files(source, pair.get("recursive"), pair.get("pattern")):
            live.append(rel)
            if state.is_synced(name, rel, st):
                continue
            if now - st.st_mtime < settle_s:
                res["pending"] += 1          # still being written; next pass
                continue
            todo.append((full, rel, st))
        state.prune(name, live)
    except OSError as e:
        res["errors"].append("cannot list %s: %s" % (source, e))

    problem = _dest_problem(dest, probe_timeout, anchors)
    if problem:
        res["errors"].append(problem)
        res["pending"] += len(todo)          # retried next pass
        return res
    res["reachable"] = True

    for i, (full, rel, st) in enumerate(todo):
        if should_stop and should_stop():
            res["pending"] += len(todo) - i
            break
        try:
            copy_atomic(full, os.path.join(dest, rel.replace("/", os.sep)))
            state.mark(name, rel, st, dest)
            res["copied"].append(rel)
        except OSError as e:
            res["errors"].append("%s: %s" % (rel, e))
            # Did the whole share just go away?  Then stop hammering it.
            anchors.pop(_anchor(dest) or "", None)
            gone = _dest_problem(dest, probe_timeout, anchors)
            if gone:
                res["reachable"] = False
                res["errors"].append(gone)
                res["pending"] += len(todo) - i
                break
            res["pending"] += 1
    state.save()
    return res


def sync_pass(pairs, state, settle_s, now=None, probe_timeout=DEFAULT_PROBE_TIMEOUT_S,
              should_stop=None):
    """One sweep over all pairs; returns one result dict per pair::

        {"name", "source", "dest", "reachable", "copied": [rel paths],
         "pending": n, "errors": [str]}

    ``pending`` counts files not copied yet (still settling, or waiting for
    an unreachable destination).
    """
    now = time.time() if now is None else now
    anchors = {}
    results = []
    for pair in pairs:
        if should_stop and should_stop():
            break
        try:
            results.append(sync_pair(pair, state, settle_s, now, probe_timeout,
                                     should_stop, anchors))
        except Exception as e:                       # noqa: BLE001 - one pair never kills a pass
            results.append({"name": pair.get("name"), "source": pair.get("source"),
                            "dest": pair.get("dest"), "reachable": False, "copied": [],
                            "pending": 0, "errors": ["unexpected: %r" % (e,)]})
    state.save()
    return results


# ---------------------------------------------------------------------------
# directory reachability for the dashboard

_check_cache = {}
_check_cache_lock = threading.Lock()


def check_paths(root, location_cfg, timeout=2.0, cache_s=10.0):
    """Reachability of the inbox and every share / mirror of the active mode.

    Returns ``[{"role", "path", "exists", "writable", ...}]``.  All checks run
    in parallel worker threads and the call returns within about ``timeout``
    seconds even when a UNC host is down (``"timed_out": True``).  Results
    are cached for ``cache_s`` seconds so frequent dashboard polls do not
    write probe files to the shares constantly.
    """
    loc = location_cfg or {}
    mode = _mode(loc)
    items = [{"role": "inbox", "path": resolve_inbox(root, loc)}]
    dests = dict((p["name"], p["dest"]) for p in build_pairs(root, loc))
    for key in SHARE_KEYS:
        items.append({"role": key, "path": dests.get(key, "")})

    now = time.time()
    waiting = []
    for it in items:
        it.update({"mode": mode, "exists": False, "writable": False})
        path = it["path"]
        if not path:
            it["disabled"] = True
            continue
        with _check_cache_lock:
            hit = _check_cache.get(path)
        if hit and cache_s > 0 and now - hit[0] < cache_s:
            it.update(hit[1])
            continue
        waiting.append((it, _start_probe(("probe", path), lambda p=path, f=_probe_dir: f(p))))

    deadline = time.time() + timeout
    for it, probe in waiting:
        if not probe.done.wait(max(0.0, deadline - time.time())):
            it["timed_out"] = True
            it["error"] = "no answer within %.1f s" % timeout
            continue
        r = probe.result
        if isinstance(r, Exception):
            r = {"exists": False, "writable": False, "error": str(r)}
        it.update(r)
        with _check_cache_lock:
            _check_cache[it["path"]] = (time.time(), dict(r))
    return items


# ---------------------------------------------------------------------------
# the background service

def _adapt_log(log):
    if log is None:
        return lambda level, msg: None
    if hasattr(log, "warning") and hasattr(log, "info"):          # a logging.Logger
        return lambda level, msg: {"warn": log.warning, "error": log.error}.get(level, log.info)(msg)
    return log


class _InstanceLock(object):
    """Cross-process (and cross-handle) exclusive lock on a lock file."""

    def __init__(self, path):
        self.path = path
        self._fh = None

    def acquire(self):
        if self._fh is not None:
            return True
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            fh = open(self.path, "a+")
        except OSError:
            return True          # cannot even create it: do not block syncing
        try:
            fh.seek(0)
            try:
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except ImportError:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self):
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            fh.seek(0)
            try:
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except ImportError:
                pass
        except OSError:
            pass
        fh.close()


class SyncService(object):
    """Runs sync passes on a daemon thread every ``location.sync.interval_s``.

    ``get_location_cfg()`` is called before every pass, so switching the
    location mode (or a share path, or disabling sync) applies on the next
    pass without a restart.  All public methods return quickly and are safe
    to call from the harness's HTTP threads; only ``run_once`` does I/O on
    the caller's thread (use ``trigger`` from request handlers).
    """

    RECENT = 20      # copied names / errors kept per pair in status()

    def __init__(self, root, get_location_cfg, state_path, log=None,
                 probe_timeout=DEFAULT_PROBE_TIMEOUT_S):
        self.root = os.path.abspath(root)
        self.get_location_cfg = get_location_cfg
        self.state_path = state_path
        self.probe_timeout = probe_timeout
        self._log = _adapt_log(log)
        self.state = SyncState(state_path, self._log)
        self._lock = threading.Lock()           # guards _status
        self._pass_lock = threading.Lock()      # one pass at a time
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._instance = _InstanceLock(state_path + ".lock")
        self._last_logged = {}
        self._status = {
            "running": False, "enabled": None, "mode": None, "interval_s": None,
            "settle_s": None, "in_pass": False, "passes": 0, "last_pass": None,
            "last_pass_duration_s": None, "pairs": [], "disabled_pairs": [],
            "totals": {"copied": 0, "errors": 0, "pending": 0, "unreachable": 0,
                       "synced_files": self.state.count()},
            "error": self.state.load_error, "state_file": state_path,
        }

    # -- lifecycle --------------------------------------------------------
    def start(self):
        """Start the background thread.  False if another service holds the lock."""
        if self._thread is not None and self._thread.is_alive():
            return True
        if not self._instance.acquire():
            self._set(error="another sync service is already running (lock %s)"
                      % self._instance.path, running=False)
            self._log("error", "File sync not started: another sync service holds %s"
                      % self._instance.path)
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nfe-sync", daemon=True)
        self._thread.start()
        self._set(running=True, error=None)
        return True

    def stop(self, timeout=10.0):
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)
        self._thread = None
        self._instance.release()
        self._set(running=False)

    def trigger(self):
        """Ask the background thread for a pass now (non-blocking)."""
        self._wake.set()

    def run_once(self):
        """One pass on the caller's thread.  Returns the per-pair results,
        or None when sync is disabled / another instance holds the lock."""
        if not self._instance.acquire():
            self._set(error="another sync service is already running (lock %s)"
                      % self._instance.path)
            return None
        return self._pass(self._read_cfg())

    def resync(self, pair_name=None):
        """Forget what was copied for one pair (or all): every local file of
        it is sent again on the next pass - including files the machines
        already consumed.  Returns the number of entries cleared."""
        n = self.state.clear(pair_name)
        self.state.save()
        self._log("warn", "Sync state cleared for %s (%d entries); files will be re-sent"
                  % (pair_name or "all pairs", n))
        with self._lock:
            self._status["totals"]["synced_files"] = self.state.count()
            for p in self._status["pairs"]:
                if pair_name is None or p["name"] == pair_name:
                    p["synced_files"] = self.state.count(p["name"])
        self.trigger()
        return n

    def status(self):
        with self._lock:
            s = dict(self._status)
            s["pairs"] = [dict(p, copied=list(p["copied"]), errors=list(p["errors"]))
                          for p in self._status["pairs"]]
            s["totals"] = dict(self._status["totals"])
            s["disabled_pairs"] = list(self._status["disabled_pairs"])
        s["running"] = bool(self._thread is not None and self._thread.is_alive())
        return s

    # -- internals --------------------------------------------------------
    def _set(self, **kw):
        with self._lock:
            self._status.update(kw)

    def _read_cfg(self):
        try:
            return self.get_location_cfg() or {}
        except Exception as e:                       # noqa: BLE001
            self._log("warn", "Sync: cannot read location config: %s" % e)
            return {}

    def _loop(self):
        while not self._stop.is_set():
            cfg = self._read_cfg()
            interval = DEFAULT_INTERVAL_S
            try:
                interval = float((cfg.get("sync") or {}).get("interval_s") or DEFAULT_INTERVAL_S)
            except (TypeError, ValueError):
                pass
            try:
                self._pass(cfg)
            except Exception as e:                   # noqa: BLE001 - never let one pass kill the thread
                self._set(error="sync pass failed: %r" % (e,))
                self._log("error", "Sync pass failed: %r" % (e,))
            self._wake.wait(max(1.0, interval))
            self._wake.clear()

    def _pass(self, cfg):
        sync_cfg = cfg.get("sync") or {}
        enabled = bool(sync_cfg.get("enabled", True))
        try:
            settle = float(sync_cfg.get("settle_s", DEFAULT_SETTLE_S))
        except (TypeError, ValueError):
            settle = DEFAULT_SETTLE_S
        mode = _mode(cfg)
        self._set(enabled=enabled, mode=mode, settle_s=settle,
                  interval_s=sync_cfg.get("interval_s", DEFAULT_INTERVAL_S),
                  disabled_pairs=disabled_pairs(cfg))
        if not enabled:
            return None
        with self._pass_lock:
            self._set(in_pass=True)
            t0 = time.time()
            try:
                pairs = build_pairs(self.root, cfg)
                results = sync_pass(pairs, self.state, settle, probe_timeout=self.probe_timeout,
                                    should_stop=self._stop.is_set)
            finally:
                self._set(in_pass=False)
            self._record(results, mode, t0)
            return results

    def _record(self, results, mode, t0):
        now = time.time()
        with self._lock:
            old = dict((p["name"], p) for p in self._status["pairs"])
            pairs = []
            totals = self._status["totals"]
            for r in results:
                prev = old.get(r["name"]) or {}
                same_dest = prev.get("dest") == r["dest"]
                copied = (list(prev.get("copied", [])) if same_dest else []) + r["copied"]
                p = {"name": r["name"], "source": r["source"], "dest": r["dest"],
                     "reachable": r["reachable"], "pending": r["pending"],
                     "copied": copied[-self.RECENT:], "errors": r["errors"][-self.RECENT:],
                     "copied_last_pass": len(r["copied"]),
                     "copied_total": (prev.get("copied_total", 0) if same_dest else 0)
                     + len(r["copied"]),
                     "last_copy": now if r["copied"] else prev.get("last_copy") if same_dest else None,
                     "synced_files": self.state.count(r["name"])}
                pairs.append(p)
                totals["copied"] += len(r["copied"])
                totals["errors"] += len(r["errors"])
            totals["pending"] = sum(r["pending"] for r in results)
            totals["unreachable"] = sum(1 for r in results if not r["reachable"])
            totals["synced_files"] = self.state.count()
            self._status.update(pairs=pairs, passes=self._status["passes"] + 1,
                                last_pass=now, last_pass_duration_s=round(now - t0, 3),
                                mode=mode, error=None)
        for r in results:
            if r["copied"]:
                self._log("info", "Sync %s: copied %d file(s) to %s"
                          % (r["name"], len(r["copied"]), r["dest"]))
            # Log problems when they change, not every 15 s.
            sig = (r["dest"], r["reachable"], r["errors"][-1] if r["errors"] else None)
            if sig != self._last_logged.get(r["name"]):
                if r["errors"]:
                    self._log("warn", "Sync %s -> %s: %s" % (r["name"], r["dest"], r["errors"][-1]))
                elif self._last_logged.get(r["name"], (None, None, None))[2]:
                    self._log("info", "Sync %s -> %s: OK again" % (r["name"], r["dest"]))
                self._last_logged[r["name"]] = sig
