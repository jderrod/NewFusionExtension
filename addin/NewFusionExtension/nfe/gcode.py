"""Splice per-setup G-code posts into one program per component.

Fusion posts every chosen CAM setup on its own (Anderson Stratos post,
Fanuc style) into ``<program number>.nc``.  The posts are then put in order
and joined into ``1-{component_id}-{order_id}.txt``:

* the first post keeps its header (``%``, ``O####``, comments) but loses
  its footer;
* every later post is preceded by an ``M0`` stop and a ``NEW SETUP`` comment
  telling the operator to FLIP the part (same workstop) or ROTATE it 180
  (different workstops), and loses its header and footer;
* the program ends with ``M30`` / ``%`` and the N-numbers are renumbered
  continuously (N10, N15, ...).
* the output has CRLF line endings.

This is a faithful port of ``post_processor.py`` from FusionExtensionVM
(``post_process_all_setups`` ordering, ``_stitch_setup_files``,
``_extract_workstop``, ``_strip_header``, ``_strip_footer``,
``_renumber_ncodes``, ``get_next_program_number``), quirks included; see
docs/tables/SPLICING.md.  The only change is ``strip_numbered_m30``, which is
off by default and leaves the old behaviour unchanged.
"""

import os
import re
import tempfile
import threading

from .jsonio import _replace_with_retry

WORKSTOPS = ("G54", "G55", "G56", "G57", "G58", "G59")
FLIP_TEXT = "FLIP PART"
ROTATE_TEXT = "ROTATE PART 180"
EOL = "\r\n"
ENCODING = "latin-1"        # byte-transparent: every byte survives a round trip

# The old counter file; seeding from it keeps program numbers unique across
# the switch from the old add-in to this one.
OLD_COUNTER_FILE = r"C:\Users\jderr\Fusion Extension\FusionExtensionVM\nc_program_counter.txt"

_WORKSTOP_RE = re.compile(r"G5[4-9]")
_FIRST_N_RE = re.compile(r"^N\d+\s")
_RENUMBER_RE = re.compile(r"^(N)\d+(\s)")
_NUMBERED_M30_RE = re.compile(r"^N\d+\s+M30(\s|$)")


def workstop_of(setup_name):
    """'Right Rabbet - In G57' -> 'G57'; None when the name has no G54..G59."""
    if not setup_name:
        return None
    m = _WORKSTOP_RE.search(setup_name.upper())
    return m.group(0) if m else None


def _is_g59(setup_name):
    # The old sort tests for the literal, case-sensitive 'G59'. (workstop_of
    # uppercases first, so the two can disagree on 'g59'.)
    return "G59" in setup_name


def order_sections(sections, g57_before_g59=True, explicit_sequence=True):
    """Reorder ``[(setup_name, lines), ...]`` (given in plan order).

    With an explicit sequence: stable sort moving G59 sections after all
    others, so the order inside each group is kept.  Without one: sort by
    ``('G59' in name, name)``.  ``g57_before_g59=False`` keeps the order."""
    sections = list(sections)
    if not g57_before_g59:
        return sections
    if explicit_sequence:
        sections.sort(key=lambda s: 1 if _is_g59(s[0]) else 0)
    else:
        sections.sort(key=lambda s: (_is_g59(s[0]), s[0]))
    return sections


def strip_header(lines):
    """Drop everything before the first N-numbered line; unchanged if none."""
    for i, line in enumerate(lines):
        if _FIRST_N_RE.match(line):
            return lines[i:]
    return lines


def strip_footer(lines, strip_numbered_m30=False):
    """Drop trailing blank / '%' lines, then one trailing M30 line.

    The old code only recognises an M30 line that *starts* with 'M30'.  The
    Stratos post writes a numbered 'N### M30', so that line stays in.
    ``strip_numbered_m30=True`` also removes 'N### M30' (not the old behaviour)."""
    end = len(lines)
    while end > 0 and lines[end - 1].strip() in ("", "%"):
        end -= 1
    if end > 0:
        last = lines[end - 1].strip()
        if last.startswith("M30") or (strip_numbered_m30 and _NUMBERED_M30_RE.match(last)):
            end -= 1
    return lines[:end]


def renumber(lines, start=10, step=5):
    """Renumber every line that starts with 'N<digits><whitespace>'."""
    current = start
    out = []
    for line in lines:
        if _RENUMBER_RE.match(line):
            line = _RENUMBER_RE.sub(lambda m: "N%d%s" % (current, m.group(2)), line, count=1)
            current += step
        out.append(line)
    return out


def transition(prev_workstop, curr_workstop, flip_text=FLIP_TEXT, rotate_text=ROTATE_TEXT):
    """ROTATE when both workstops are known and differ, FLIP otherwise."""
    if prev_workstop and curr_workstop and prev_workstop != curr_workstop:
        return rotate_text
    return flip_text


def setup_block(text):
    """The block inserted before every section after the first."""
    return ["M0", "", "(******************)", "(NEW SETUP, %s)" % text, "(******************)", ""]


def stitch(sections, flip_text=FLIP_TEXT, rotate_text=ROTATE_TEXT, strip_numbered_m30=False):
    """Join the sections without renumbering (old ``_stitch_setup_files``)."""
    combined = []
    prev = None
    for idx, (name, lines) in enumerate(sections):
        lines = [line.rstrip() for line in lines]
        curr = workstop_of(name)
        if idx == 0:
            combined.extend(strip_footer(lines, strip_numbered_m30))
        else:
            combined.extend(setup_block(transition(prev, curr, flip_text, rotate_text)))
            combined.extend(strip_footer(strip_header(lines), strip_numbered_m30))
        prev = curr
    combined.append("M30")
    combined.append("%")
    return combined


def splice(sections, flip_text=FLIP_TEXT, rotate_text=ROTATE_TEXT, start=10, step=5,
           strip_numbered_m30=False):
    """Stitch the (already ordered) sections and renumber them; returns lines."""
    return renumber(stitch(sections, flip_text, rotate_text, strip_numbered_m30), start, step)


def build_program(sections, g57_before_g59=True, explicit_sequence=True,
                  flip_text=FLIP_TEXT, rotate_text=ROTATE_TEXT, strip_numbered_m30=False):
    """Order + splice + join with CRLF (trailing CRLF included)."""
    if not sections:
        raise ValueError("no G-code sections to splice")
    ordered = order_sections(sections, g57_before_g59, explicit_sequence)
    lines = splice(ordered, flip_text, rotate_text, strip_numbered_m30=strip_numbered_m30)
    return EOL.join(lines) + EOL


def splice_report(sections):
    """One row per section (in final order) for the order report / dashboard."""
    rows = []
    prev = None
    for idx, section in enumerate(sections):
        name = section[0]
        ws = workstop_of(name)
        rows.append({"setup": name, "workstop": ws,
                     "transition_before": None if idx == 0 else transition(prev, ws)})
        prev = ws
    return rows


def split_text(text):
    """Split like Python's universal-newline ``readlines`` + ``rstrip``."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return [p.rstrip() for p in parts]


def read_nc(path, encoding=ENCODING):
    """Read a posted .nc file as rstripped lines (LF, CRLF or CR endings)."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return split_text(data.decode(encoding))


def write_program(path, text, encoding=ENCODING):
    """Atomically write ``text`` exactly as given (binary: CRLF preserved)."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".txt", dir=d)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode(encoding, "replace"))
        _replace_with_retry(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def output_name(component_id, order_id):
    return "1-%s-%s.txt" % (component_id, order_id)


# --------------------------------------------------------------------------
# Program numbers

_PATH_LOCKS = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for(path):
    key = os.path.normcase(os.path.abspath(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = _PATH_LOCKS[key] = threading.Lock()
        return lock


def _read_counter(path):
    """Last used number stored in ``path``; None if missing or corrupt."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


class ProgramCounter(object):
    """Persistent program-number counter (the old ``nc_program_counter.txt``).

    The file holds the last number handed out.  ``next()`` returns
    ``last + 1``, or ``start`` if the file is missing or corrupt.  Numbers
    stored in ``seed_paths`` (e.g. the old add-in's counter) count as used,
    so a number is never handed out twice.  Thread-safe (one lock per file
    path, shared by all instances in the process)."""

    def __init__(self, path, start=1001, seed_paths=()):
        self.path = path
        self.start = int(start)
        if isinstance(seed_paths, str):
            seed_paths = (seed_paths,)
        self.seed_paths = tuple(p for p in seed_paths if p)
        self._lock = _lock_for(path)

    def _last_used(self):
        values = [v for v in [_read_counter(self.path)] +
                  [_read_counter(p) for p in self.seed_paths] if v is not None]
        return max(values) if values else None

    def current(self):
        """Last number handed out (``start - 1`` if none yet)."""
        with self._lock:
            last = self._last_used()
            return self.start - 1 if last is None else last

    def next(self):
        with self._lock:
            last = self._last_used()
            value = self.start if last is None else last + 1
            d = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".txt", dir=d)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(str(value))
                _replace_with_retry(tmp, self.path)
            except Exception:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            return value

    __next__ = next

    def __iter__(self):
        return self
