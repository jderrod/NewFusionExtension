"""The order dropbox: inbox -> processing -> done | failed.

Claiming an order moves it into ``processing/`` so a crash or Fusion restart
never loses it; a checkpoint file next to it records finished components so
work resumes where it stopped.
"""

import os
import shutil
import time

from .jsonio import read_json, write_json_atomic

ORDER_EXT = ".json"
SETTLE_SECONDS = 1.0     # ignore files modified more recently (still being written)


def _is_order_file(name):
    low = name.lower()
    return (low.endswith(ORDER_EXT) and not low.startswith((".", "~"))
            and not low.endswith((".state.json", ".report.json")))


def _unique(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    candidate = "%s_%s%s" % (base, stamp, ext)
    n = 1
    while os.path.exists(candidate):
        n += 1
        candidate = "%s_%s_%d%s" % (base, stamp, n, ext)
    return candidate


class Dropbox(object):
    def __init__(self, paths, clock=time.time):
        self.paths = paths
        self._clock = clock

    # -- listing ----------------------------------------------------------
    def inbox_files(self, settled_only=True, raise_errors=False):
        try:
            names = sorted(os.listdir(self.paths.inbox))
        except OSError:
            if raise_errors:
                raise
            return []
        out = []
        now = self._clock()
        for name in names:
            if not _is_order_file(name):
                continue
            full = os.path.join(self.paths.inbox, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if settled_only and now - st.st_mtime < SETTLE_SECONDS:
                continue
            out.append((st.st_mtime, name))
        out.sort()   # oldest first
        return [n for _, n in out]

    def processing_files(self):
        try:
            return sorted(n for n in os.listdir(self.paths.processing) if _is_order_file(n))
        except OSError:
            return []

    def listing(self, folder_key):
        folder = getattr(self.paths, folder_key)
        out = []
        try:
            names = os.listdir(folder)
        except OSError:
            return out
        for name in names:
            if not _is_order_file(name):
                continue
            full = os.path.join(folder, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            item = {"name": name, "folder": folder_key, "mtime": st.st_mtime, "size": st.st_size}
            report = read_json(self.report_path(folder, name))
            if report:
                item["summary"] = report.get("summary")
            out.append(item)
        out.sort(key=lambda x: x["mtime"], reverse=True)
        return out

    # -- transitions ------------------------------------------------------
    def claim(self, name):
        """Move an inbox file into processing/.  Returns the new path or None."""
        src = os.path.join(self.paths.inbox, name)
        dst = _unique(os.path.join(self.paths.processing, name))
        try:
            os.replace(src, dst)
            return dst
        except OSError as e:
            if getattr(e, "winerror", None) != 17 and e.errno != 18:     # 17 / EXDEV: other drive
                return None     # still locked by the writer, or already gone
        # Network inbox (remote mode): copy, then remove the original.  If the
        # original can't be removed the copy is dropped, so an order is never
        # processed twice.
        tmp = dst + ".claiming"
        try:
            shutil.copy2(src, tmp)
            os.remove(src)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return None
        os.replace(tmp, dst)
        return dst

    def finish(self, processing_path, ok, report):
        folder = self.paths.done if ok else self.paths.failed
        dst = _unique(os.path.join(folder, os.path.basename(processing_path)))
        shutil.move(processing_path, dst)
        write_json_atomic(self.report_path(folder, os.path.basename(dst)), report)
        self.clear_checkpoint(processing_path)
        return dst

    def reject(self, path, reason):
        """Move an unparseable order straight to failed/ with a report."""
        report = {"summary": {"status": "rejected", "error": reason}, "error": reason}
        dst = _unique(os.path.join(self.paths.failed, os.path.basename(path)))
        try:
            shutil.move(path, dst)
        except OSError:
            return None
        write_json_atomic(self.report_path(self.paths.failed, os.path.basename(dst)), report)
        self.clear_checkpoint(path)
        return dst

    def requeue(self, folder_key, name):
        src = os.path.join(getattr(self.paths, folder_key), name)
        if not os.path.isfile(src):
            raise FileNotFoundError(name)
        dst = _unique(os.path.join(self.paths.inbox, name))
        shutil.move(src, dst)
        rp = self.report_path(getattr(self.paths, folder_key), name)
        if os.path.exists(rp):
            os.remove(rp)
        return dst

    # -- checkpoints --------------------------------------------------------
    @staticmethod
    def report_path(folder, name):
        return os.path.join(folder, os.path.splitext(name)[0] + ".report.json")

    @staticmethod
    def checkpoint_path(processing_path):
        return os.path.splitext(processing_path)[0] + ".state.json"

    def load_checkpoint(self, processing_path):
        return read_json(self.checkpoint_path(processing_path), default=None)

    def save_checkpoint(self, processing_path, data):
        write_json_atomic(self.checkpoint_path(processing_path), data)

    def clear_checkpoint(self, processing_path):
        try:
            os.remove(self.checkpoint_path(processing_path))
        except OSError:
            pass
