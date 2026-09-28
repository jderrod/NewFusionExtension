import helpers  # noqa: F401  (sets sys.path)

import itertools
import os
import random
import re
import shutil
import tempfile
import threading
import unittest

from nfe import gcode

FIXTURES = os.path.join(helpers.ROOT, "tests", "fixtures", "gcode")


# ---------------------------------------------------------------------------
# ORACLE: the old FusionExtensionVM post_processor.py functions, copied as-is
# (file based, text-mode I/O exactly like the old code) so the new module
# can be compared against them.

class Oracle(object):
    def _extract_workstop(self, setup_name):
        if not setup_name:
            return None
        match = re.search(r'G5[4-9]', setup_name.upper())
        return match.group(0) if match else None

    def _stitch_setup_files(self, temp_files):
        combined = []
        prev_workstop = None
        for file_idx, (setup_name, file_path) in enumerate(temp_files):
            with open(file_path, 'r') as f:
                lines = [line.rstrip() for line in f.readlines()]
            curr_workstop = self._extract_workstop(setup_name)
            if file_idx == 0:
                body = self._strip_footer(lines)
                combined.extend(body)
            else:
                if prev_workstop and curr_workstop and prev_workstop != curr_workstop:
                    transition = 'ROTATE PART 180'
                else:
                    transition = 'FLIP PART'
                combined.append('M0')
                combined.append('')
                combined.append('(******************)')
                combined.append(f'(NEW SETUP, {transition})')
                combined.append('(******************)')
                combined.append('')
                body = self._strip_header(lines)
                body = self._strip_footer(body)
                combined.extend(body)
            prev_workstop = curr_workstop
        combined.append('M30')
        combined.append('%')
        return combined

    def _strip_header(self, lines):
        first_n = None
        for i, line in enumerate(lines):
            if re.match(r'^N\d+\s', line):
                first_n = i
                break
        if first_n is None:
            return lines
        return lines[first_n:]

    def _strip_footer(self, lines):
        end = len(lines)
        while end > 0 and lines[end - 1].strip() in ('', '%'):
            end -= 1
        if end > 0 and lines[end - 1].strip().startswith('M30'):
            end -= 1
        return lines[:end]

    def _renumber_ncodes(self, lines, start=10, increment=5):
        n_pattern = re.compile(r'^(N)\d+(\s)')
        current = start
        result = []
        for line in lines:
            if n_pattern.match(line):
                line = n_pattern.sub(f'N{current}\\2', line, count=1)
                current += increment
            result.append(line)
        return result

    def program(self, temp_files, setup_sequence, output_path):
        """Phases 2-4 of post_process_all_setups (after posting)."""
        temp_files = list(temp_files)
        if setup_sequence:
            temp_files.sort(key=lambda x: 1 if 'G59' in x[0] else 0)
        else:
            temp_files.sort(key=lambda x: ('G59' in x[0], x[0]))
        combined_lines = self._stitch_setup_files(temp_files)
        combined_lines = self._renumber_ncodes(combined_lines)
        with open(output_path, 'w') as f:
            f.write('\n'.join(combined_lines) + '\n')
        with open(output_path, 'rb') as f:
            return f.read()


ORACLE = Oracle()


# ---------------------------------------------------------------------------
# Fixture helpers

def fake_post(prog, body, header_comments=2, lead_pct=True, trailing=("M30", "%"),
              number_m30=True):
    """A post shaped like Anderson Stratos 2 output (see onOpen / onClose)."""
    lines = []
    if lead_pct:
        lines.append("%")
    lines.append("O%04d" % prog)
    lines += ["(MACHINE)", "(  VENDOR ANDERSON)", "(  MODEL STRATOS)",
              "(  DESCRIPTION BLA CNC ROUTER)"][:header_comments]
    n = 10
    for b in body:
        if b == "" or b.startswith("("):
            lines.append(b)
        else:
            lines.append("N%d %s" % (n, b))
            n += 5
    for t in trailing:
        if t == "M30" and number_m30:
            lines.append("N%d M30" % n)
        else:
            lines.append(t)
    return lines


def unsplice(text):
    """Cut an old spliced program back into the per-setup posts it came from.

    The real outputs on disk were made by the old splicer; their sections all
    start with the same post header and end with the post's 'N### M30'."""
    lines = gcode.split_text(text)
    assert lines[-2:] == ["M30", "%"], lines[-2:]
    lines = lines[:-2]
    chunks, cur, i = [], [], 0
    while i < len(lines):
        if lines[i] == "M0" and lines[i + 1] == "" and lines[i + 3].startswith("(NEW SETUP"):
            chunks.append(cur)
            cur = []
            i += 6
        else:
            cur.append(lines[i])
            i += 1
    chunks.append(cur)
    first = chunks[0]
    header = gcode.strip_header(first)
    header = first[:len(first) - len(header)]
    prog = int(header[1][1:])
    posts = [first + ["%"]]
    for k, chunk in enumerate(chunks[1:], 1):
        hdr = list(header)
        hdr[1] = "O%04d" % (prog + k)
        posts.append(hdr + gcode.renumber(chunk) + ["%"])
    return posts


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nfe_test_gcode_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.tmp, *parts)


# ---------------------------------------------------------------------------

class WorkstopTests(unittest.TestCase):
    def test_workstop_of(self):
        self.assertEqual(gcode.workstop_of("Right Rabbet - In G57"), "G57")
        self.assertEqual(gcode.workstop_of("Setup 4 - G59"), "G59")
        self.assertEqual(gcode.workstop_of("g58 lower"), "G58")
        self.assertEqual(gcode.workstop_of("G54 then G59"), "G54")
        self.assertIsNone(gcode.workstop_of("Setup 1"))
        self.assertIsNone(gcode.workstop_of("G53"))
        self.assertIsNone(gcode.workstop_of(""))
        self.assertIsNone(gcode.workstop_of(None))
        for name in ["X G54", "G55", "aG56b", "G570", "Setup g59", "G5 9", "G60"]:
            self.assertEqual(gcode.workstop_of(name), ORACLE._extract_workstop(name), name)


class OrderTests(unittest.TestCase):
    def names(self, sections):
        return [s[0] for s in sections]

    def sec(self, names):
        return [(n, ["N10 G0"]) for n in names]

    def test_door_sequence_puts_g57_first(self):
        out = gcode.order_sections(self.sec(["Setup 4 - G59", "Setup 7 - G57"]))
        self.assertEqual(self.names(out), ["Setup 7 - G57", "Setup 4 - G59"])

    def test_stile_sequence_unchanged(self):
        seq = ["Right Rabbet - In G57", "Left Rabbet - In G59"]
        self.assertEqual(self.names(gcode.order_sections(self.sec(seq))), seq)

    def test_same_g_opposite_swing_unchanged(self):
        seq = ["Left Rabbet - In G59", "Right Rabbet - Out G59"]
        out = gcode.order_sections(self.sec(seq))
        self.assertEqual(self.names(out), seq)
        rep = gcode.splice_report(out)
        self.assertEqual(rep[1]["transition_before"], "FLIP PART")

    def test_stable_within_groups_and_repeats(self):
        seq = ["B G59", "Z G57", "A G59", "Y", "B G59", "C G58"]
        out = self.names(gcode.order_sections(self.sec(seq)))
        self.assertEqual(out, ["Z G57", "Y", "C G58", "B G59", "A G59", "B G59"])

    def test_default_sort_without_sequence(self):
        seq = ["B G59", "Z G57", "A G59", "Y", "C G58"]
        out = self.names(gcode.order_sections(self.sec(seq), explicit_sequence=False))
        self.assertEqual(out, ["C G58", "Y", "Z G57", "A G59", "B G59"])

    def test_no_reordering_when_disabled(self):
        seq = ["Setup 4 - G59", "Setup 7 - G57"]
        for explicit in (True, False):
            out = gcode.order_sections(self.sec(seq), g57_before_g59=False, explicit_sequence=explicit)
            self.assertEqual(self.names(out), seq)

    def test_lowercase_g59_is_not_sorted(self):
        # old quirk: the sort looks for literal 'G59'
        seq = ["x g59", "y G57"]
        self.assertEqual(self.names(gcode.order_sections(self.sec(seq))), seq)

    def test_input_not_mutated(self):
        secs = self.sec(["A G59", "B G57"])
        before = list(secs)
        gcode.order_sections(secs)
        self.assertEqual(secs, before)


class StripRenumberTests(unittest.TestCase):
    def test_strip_header(self):
        post = fake_post(1001, ["G90", "(C)", "G0 X1"])
        self.assertEqual(gcode.strip_header(post)[0], "N10 G90")
        self.assertEqual(gcode.strip_header(["%", "(A)", "G0"]), ["%", "(A)", "G0"])
        # 'N10' with nothing after it is not a first N line (needs whitespace)
        self.assertEqual(gcode.strip_header(["%", "N10", "N15 G0"]), ["N15 G0"])

    def test_strip_footer(self):
        self.assertEqual(gcode.strip_footer(["N10 G0", "M30", "%", "", "%", ""]), ["N10 G0"])
        self.assertEqual(gcode.strip_footer(["N10 G0", "M30 ", "%"]), ["N10 G0"])
        self.assertEqual(gcode.strip_footer(["N10 G0", "M30", "M30", "%"]), ["N10 G0", "M30"])
        self.assertEqual(gcode.strip_footer(["N10 G0", "%"]), ["N10 G0"])
        self.assertEqual(gcode.strip_footer(["", "%"]), [])
        self.assertEqual(gcode.strip_footer([]), [])
        # numbered M30 (what the Stratos post writes) is kept by the old rule...
        self.assertEqual(gcode.strip_footer(["N10 G0", "N15 M30", "%"]), ["N10 G0", "N15 M30"])
        # ...and only removed on request
        self.assertEqual(gcode.strip_footer(["N10 G0", "N15 M30", "%"], strip_numbered_m30=True),
                         ["N10 G0"])
        self.assertEqual(gcode.strip_footer(["N10 G0", "N15 M300", "%"], strip_numbered_m30=True),
                         ["N10 G0", "N15 M300"])

    def test_renumber(self):
        lines = ["%", "O1001", "N10 G0", "N10 G1", "", "(N5 C)", "N999\tX1", "N7", "NX G0", "N20 M30"]
        self.assertEqual(gcode.renumber(lines),
                         ["%", "O1001", "N10 G0", "N15 G1", "", "(N5 C)", "N20\tX1", "N7", "NX G0",
                          "N25 M30"])
        self.assertEqual(gcode.renumber(["N1 A", "N2 B"], start=100, step=10), ["N100 A", "N110 B"])
        self.assertEqual(gcode.renumber(lines), ORACLE._renumber_ncodes(lines))


class SpliceTests(TmpDirCase):
    def test_m0_block_text(self):
        a = fake_post(1001, ["G57", "G0 X1"])
        b = fake_post(1002, ["G57", "G0 X2"])
        out = gcode.splice([("A G57", a), ("B G57", b)])
        i = out.index("M0")
        self.assertEqual(out[i:i + 6], ["M0", "", "(******************)", "(NEW SETUP, FLIP PART)",
                                        "(******************)", ""])
        self.assertEqual(out[-2:], ["M30", "%"])
        self.assertEqual(out[:2], ["%", "O1001"])
        self.assertNotIn("O1002", out)

    def test_transitions(self):
        cases = [("A G57", "B G57", "FLIP PART"), ("A G57", "B G59", "ROTATE PART 180"),
                 ("A G59", "B G57", "ROTATE PART 180"), ("A G59", "B G59", "FLIP PART"),
                 ("A", "B G59", "FLIP PART"), ("A G57", "B", "FLIP PART"), ("A", "B", "FLIP PART"),
                 ("A G54", "B G58", "ROTATE PART 180")]
        post = fake_post(1001, ["G0 X1"])
        for prev, cur, expected in cases:
            out = gcode.splice([(prev, post), (cur, post)])
            self.assertIn("(NEW SETUP, %s)" % expected, out, (prev, cur))
            self.assertEqual(gcode.splice_report([(prev, post), (cur, post)])[1]["transition_before"],
                             expected)

    def test_transition_uses_previous_section_even_if_unknown(self):
        post = fake_post(1001, ["G0 X1"])
        out = gcode.splice([("A G57", post), ("B", post), ("C G59", post)])
        self.assertEqual([l for l in out if l.startswith("(NEW SETUP")],
                         ["(NEW SETUP, FLIP PART)", "(NEW SETUP, FLIP PART)"])

    def test_custom_texts(self):
        post = fake_post(1001, ["G0 X1"])
        out = gcode.splice([("A G57", post), ("B G59", post), ("C G59", post)],
                           flip_text="FLIP", rotate_text="TURN")
        self.assertEqual([l for l in out if l.startswith("(NEW SETUP")],
                         ["(NEW SETUP, TURN)", "(NEW SETUP, FLIP)"])

    def test_continuous_numbering(self):
        a = fake_post(1001, ["G90", "G0 X1"])
        b = fake_post(1002, ["G90", "G0 X2", "G0 X3"])
        out = gcode.splice([("A", a), ("B", b)])
        nums = [int(re.match(r"N(\d+)", l).group(1)) for l in out if re.match(r"N\d+\s", l)]
        self.assertEqual(nums, list(range(10, 10 + 5 * len(nums), 5)))
        self.assertEqual(len(nums), 3 + 4)    # two numbered M30s stay in (old behaviour)

    def test_splice_report(self):
        secs = [("Setup 7 - G57", []), ("Setup 7 - G57", []), ("Setup 4 - G59", []), ("X", [])]
        self.assertEqual(gcode.splice_report(secs), [
            {"setup": "Setup 7 - G57", "workstop": "G57", "transition_before": None},
            {"setup": "Setup 7 - G57", "workstop": "G57", "transition_before": "FLIP PART"},
            {"setup": "Setup 4 - G59", "workstop": "G59", "transition_before": "ROTATE PART 180"},
            {"setup": "X", "workstop": None, "transition_before": "FLIP PART"},
        ])
        self.assertEqual(gcode.splice_report([]), [])

    def test_build_program_crlf(self):
        a = fake_post(1001, ["G0 X1"])
        b = fake_post(1002, ["G0 X2"])
        text = gcode.build_program([("Setup 4 - G59", a), ("Setup 7 - G57", b)])
        self.assertTrue(text.endswith("M30\r\n%\r\n"))
        self.assertNotIn("\n", text.replace("\r\n", ""))
        self.assertTrue(text.startswith("%\r\nO1002\r\n"))    # G57 section moved first
        self.assertIn("(NEW SETUP, ROTATE PART 180)", text)
        path = gcode.write_program(self.p("out", gcode.output_name("D1", "IBUS1")), text)
        self.assertTrue(path.endswith("1-D1-IBUS1.txt"))
        self.assertEqual(read_bytes(path), text.encode("ascii"))
        self.assertEqual([f for f in os.listdir(self.p("out"))], ["1-D1-IBUS1.txt"])  # no temp left

    def test_build_program_empty(self):
        with self.assertRaises(ValueError):
            gcode.build_program([])

    def test_write_program_overwrites(self):
        path = self.p("x.txt")
        gcode.write_program(path, "a\r\n")
        gcode.write_program(path, "%\r\nM30\r\n%\r\n")
        self.assertEqual(read_bytes(path), b"%\r\nM30\r\n%\r\n")

    def test_read_nc_line_endings(self):
        for i, raw in enumerate([b"%\nO1001  \nN10 G0\t\n%\n", b"%\r\nO1001  \r\nN10 G0\t\r\n%\r\n",
                                 b"%\rO1001\rN10 G0\r%", b"\xef\xbb\xbf%\r\nO1001\r\nN10 G0\r\n%"]):
            path = self.p("f%d.nc" % i)
            with open(path, "wb") as fh:
                fh.write(raw)
            self.assertEqual(gcode.read_nc(path), ["%", "O1001", "N10 G0", "%"], raw)
        path = self.p("blank.nc")
        with open(path, "wb") as fh:
            fh.write(b"N10 G0\n\n\n")
        self.assertEqual(gcode.read_nc(path), ["N10 G0", "", ""])

    def test_non_ascii_round_trip(self):
        path = self.p("deg.nc")
        with open(path, "wb") as fh:
            fh.write(b"%\r\nO1001\r\n(ANGLE 90\xb0)\r\nN10 G0\r\nN15 M30\r\n%\r\n")
        text = gcode.build_program([("A", gcode.read_nc(path))])
        out = gcode.write_program(self.p("o.txt"), text)
        self.assertIn(b"(ANGLE 90\xb0)\r\n", read_bytes(out))


class OracleComparisonTests(TmpDirCase):
    NAMES = ["Setup 7 - G57", "Setup 4 - G59", "Top - G58", "Plain Setup",
             "Left Rabbet - In G59", "Right Rabbet - Out G57", "lower g59", "Edge G54"]

    def random_post(self, rng, prog):
        body = []
        for _ in range(rng.randint(0, 6)):
            body.append(rng.choice(["G90", "G0 X1.5 Y2.", "G1 Z-0.25 F200.", "(OP COMMENT)", "",
                                    "M92 (ALL SPINDLES UP)", "G8 P1", "G57", "G59"]))
        trailing = rng.choice([("M30", "%"), ("M30", "%", ""), ("M30",), ("%",), (), ("M30", "", "%", "%"),
                               ("M30", "%", "", "")])
        post = fake_post(prog, body, header_comments=rng.randint(0, 4), lead_pct=rng.random() < 0.8,
                         trailing=trailing, number_m30=rng.random() < 0.6)
        if rng.random() < 0.2:
            post = [l + rng.choice([" ", "\t", "  "]) for l in post]
        if rng.random() < 0.15:
            post = [""] + post
        return post

    def write_nc(self, rng, name, lines):
        path = self.p(name)
        eol = rng.choice(["\n", "\r\n"])
        with open(path, "wb") as fh:
            fh.write((eol.join(lines) + rng.choice([eol, ""])).encode("ascii"))
        return path

    def compare(self, rng, names, tag):
        paths = []
        for i, name in enumerate(names):
            paths.append((name, self.write_nc(rng, "%s_%d.nc" % (tag, i), self.random_post(rng, 1001 + i))))
        sections = [(n, gcode.read_nc(p)) for n, p in paths]
        for explicit in (True, False):
            expected = ORACLE.program(paths, ["x"] if explicit else None, self.p("%s_oracle.txt" % tag))
            text = gcode.build_program(sections, explicit_sequence=explicit)
            out = gcode.write_program(self.p("%s_new.txt" % tag), text)
            self.assertEqual(read_bytes(out), expected, (names, explicit))
            self.assertEqual(text.encode("ascii"), expected)
        return 1

    def test_all_combinations_up_to_three(self):
        rng = random.Random(1234)
        count = 0
        for n in (1, 2, 3):
            for combo in itertools.product(self.NAMES[:5] if n == 3 else self.NAMES, repeat=n):
                count += self.compare(rng, combo, "c%d" % count)
        self.assertGreater(count, 150)

    def test_random_longer_sequences(self):
        rng = random.Random(99)
        for k in range(60):
            names = [rng.choice(self.NAMES) for _ in range(rng.randint(1, 7))]
            self.compare(rng, names, "r%d" % k)

    def test_no_n_lines_and_no_footer(self):
        rng = random.Random(5)
        a = self.write_nc(rng, "a.nc", ["%", "O1001", "(ONLY COMMENTS)"])
        b = self.write_nc(rng, "b.nc", ["%", "O1002", "(NO N LINES)", "G0 X1"])
        paths = [("A G57", a), ("B G59", b)]
        expected = ORACLE.program(paths, ["x"], self.p("o.txt"))
        text = gcode.build_program([(n, gcode.read_nc(p)) for n, p in paths])
        self.assertEqual(text.encode("ascii"), expected)
        self.assertIn("O1002", text)     # old quirk: header kept when no N line


class RealFixtureTests(unittest.TestCase):
    """Real spliced programs written by the old add-in (G Code Arc Analysis)."""

    def load(self, name):
        path = os.path.join(FIXTURES, name)
        return path, read_bytes(path)

    def test_round_trip_byte_exact(self):
        for name in ["1-P1-IBUS447872.txt", "1-S5-IBUS789123.txt", "1-S7-IBUS789123.txt",
                     "1-D1-IBUS447837.txt"]:
            path, raw = self.load(name)
            self.assertIn(b"\r\n", raw)
            posts = unsplice(raw.decode("ascii"))
            # neutral names: these files predate the G57/G59 sort and the ROTATE rule
            sections = [("Section %d" % (i + 1), p) for i, p in enumerate(posts)]
            text = gcode.build_program(sections)
            self.assertEqual(text.encode("ascii"), raw, name)
            self.assertEqual(gcode.read_nc(path), gcode.split_text(text))

    def test_real_files_keep_numbered_m30(self):
        _, raw = self.load("1-S5-IBUS789123.txt")
        lines = gcode.split_text(raw.decode("ascii"))
        i = lines.index("M0")
        self.assertEqual(lines[i - 1], "N180 M30")      # post's M30 survives the old footer rule
        self.assertEqual(lines[-3:], ["N360 M30", "M30", "%"])
        posts = unsplice(raw.decode("ascii"))
        fixed = gcode.build_program([("A G57", posts[0]), ("B G57", posts[1])], strip_numbered_m30=True)
        fixed_lines = gcode.split_text(fixed)
        self.assertEqual(fixed_lines[fixed_lines.index("M0") - 1], "N175 G90")
        self.assertEqual(fixed_lines[-3:], ["N350 G90", "M30", "%"])
        self.assertEqual(sum(1 for l in fixed_lines if "M30" in l), 1)

    def test_s7_with_current_rules_rotates(self):
        # S7 is Left Rabbet Out (G57) then Right Rabbet Out (G59); the old file
        # says FLIP, the current rule (different workstops) says ROTATE.
        _, raw = self.load("1-S7-IBUS789123.txt")
        posts = unsplice(raw.decode("ascii"))
        text = gcode.build_program([("Left Rabbet - Out G57", posts[0]),
                                    ("Right Rabbet - Out G59", posts[1])])
        self.assertEqual(text.encode("ascii"),
                         raw.replace(b"(NEW SETUP, FLIP PART)", b"(NEW SETUP, ROTATE PART 180)"))

    def test_d1_with_current_rules_groups_g57(self):
        _, raw = self.load("1-D1-IBUS447837.txt")
        posts = unsplice(raw.decode("ascii"))
        self.assertEqual(len(posts), 8)
        ws = []
        for p in posts:
            ws.append([l.split()[1] for l in p if re.match(r"N\d+ G5[4-9]$", l)][0])
        self.assertEqual(ws, ["G57", "G59"] * 4)
        names = ["Setup %d - %s" % (i + 1, w) for i, w in enumerate(ws)]
        sections = list(zip(names, posts))
        ordered = gcode.order_sections(sections)
        self.assertEqual([s[0] for s in ordered],
                         ["Setup 1 - G57", "Setup 3 - G57", "Setup 5 - G57", "Setup 7 - G57",
                          "Setup 2 - G59", "Setup 4 - G59", "Setup 6 - G59", "Setup 8 - G59"])
        self.assertEqual([r["transition_before"] for r in gcode.splice_report(ordered)],
                         [None, "FLIP PART", "FLIP PART", "FLIP PART", "ROTATE PART 180",
                          "FLIP PART", "FLIP PART", "FLIP PART"])
        text = gcode.build_program(sections)
        lines = gcode.split_text(text)
        self.assertEqual(lines[1], "O2044")
        self.assertEqual(len(lines), len(gcode.split_text(raw.decode("ascii"))))


class ProgramCounterTests(TmpDirCase):
    def test_starts_at_1001_and_persists(self):
        path = self.p("state", "nc_program_counter.txt")
        c = gcode.ProgramCounter(path)
        self.assertEqual(c.current(), 1000)
        self.assertEqual([c.next(), c.next(), next(c)], [1001, 1002, 1003])
        with open(path, "r") as fh:
            self.assertEqual(fh.read(), "1003")
        self.assertEqual(gcode.ProgramCounter(path).next(), 1004)    # "restart"
        self.assertEqual(gcode.ProgramCounter(path).current(), 1004)

    def test_custom_start(self):
        c = gcode.ProgramCounter(self.p("c.txt"), start=5000)
        self.assertEqual(c.next(), 5000)
        self.assertEqual(c.next(), 5001)

    def test_corrupt_or_empty_file(self):
        for content in ["abc", "", "12.5", "\x00\x00"]:
            path = self.p("bad.txt")
            with open(path, "w") as fh:
                fh.write(content)
            self.assertEqual(gcode.ProgramCounter(path).next(), 1001, repr(content))

    def test_old_format_with_newline_and_bom(self):
        path = self.p("c.txt")
        with open(path, "wb") as fh:
            fh.write(b"\xef\xbb\xbf1285\r\n")
        self.assertEqual(gcode.ProgramCounter(path).next(), 1286)

    def test_seed_from_old_counter(self):
        old = self.p("old.txt")
        with open(old, "w") as fh:
            fh.write("1285")
        path = self.p("new.txt")
        c = gcode.ProgramCounter(path, seed_paths=[old, self.p("missing.txt")])
        self.assertEqual(c.current(), 1285)
        self.assertEqual(c.next(), 1286)
        self.assertEqual(c.next(), 1287)
        with open(old) as fh:
            self.assertEqual(fh.read(), "1285")      # the old file is never written
        # own counter ahead of the seed
        with open(path, "w") as fh:
            fh.write("1400")
        self.assertEqual(c.next(), 1401)
        # old add-in used more numbers meanwhile
        with open(old, "w") as fh:
            fh.write("1500")
        self.assertEqual(c.next(), 1501)
        self.assertEqual(gcode.ProgramCounter(self.p("x.txt"), seed_paths=old).next(), 1501)

    def test_threads(self):
        path = self.p("c.txt")
        counters = [gcode.ProgramCounter(path), gcode.ProgramCounter(path)]
        got, lock = [], threading.Lock()

        def work(c):
            mine = [c.next() for _ in range(40)]
            with lock:
                got.extend(mine)
        threads = [threading.Thread(target=work, args=(counters[i % 2],)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(got), list(range(1001, 1001 + 320)))
        with open(path) as fh:
            self.assertEqual(fh.read(), "1320")
        self.assertEqual(os.listdir(self.tmp), ["c.txt"])


if __name__ == "__main__":
    unittest.main()
