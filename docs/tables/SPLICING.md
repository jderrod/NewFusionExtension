# G-code splicing rules

One component (a door, a stile, ...) usually needs more than one CAM setup,
for example the left and the right rabbet. Fusion posts each setup to its own
file. NFE then **splices** those files into **one program per component**.
The code is in `addin/NewFusionExtension/nfe/gcode.py`. It is a faithful port
of the old add-in's `post_processor.py` (FusionExtensionVM), quirks included.
The tests in `tests/test_gcode.py` compare it with a copy of the old code.

---

## 1. Posting

| Step | What happens | Example |
|---|---|---|
| Choose setups | The plan lists setup names in order. The same name can appear more than once. Setups without a valid, unsuppressed toolpath are left out. | `["Setup 4 - G59", "Setup 7 - G57"]` |
| Program number | Each **individual post** takes the next number from the persistent counter. The first number is **1001**. The file stores the last number used. | 1041, 1042 |
| Post | Fusion runs the *Anderson Stratos 2* post and writes `<number>.nc`. | `1041.nc`, `1042.nc` |
| Post layout | `%`, `O####`, `(comment header)`, N-numbered body starting at `N10` in steps of 5, a numbered `N### M30`, `%` | see the example in section 7 |
| Counter file | NFE keeps its own counter file. It can be seeded from the old add-in's `C:\Users\jderr\Fusion Extension\FusionExtensionVM\nc_program_counter.txt`. The next number is then `max(own, old) + 1`, so no number is used twice. The old file is only read, never written. | old = 1285, so the next is 1286 |
| Final program number | The spliced file keeps the `O####` of whichever section comes **first after ordering**. The other sections' numbers are used up but do not appear in the file. | `O1042` |

## 2. Ordering

The sections are put in order **after** posting. The workstop is the `G54`..`G59`
in the setup name.

| Rule | When | Example input (plan order) | Resulting order |
|---|---|---|---|
| Stable "G59 last" | Explicit setup sequence (normal case) | `Setup 4 - G59`, `Setup 7 - G57` (door) | `Setup 7 - G57`, `Setup 4 - G59` |
| Stable "G59 last" | Explicit sequence, already G57 first | `Right Rabbet - In G57`, `Left Rabbet - In G59` (stile) | unchanged |
| Stable "G59 last" | Explicit sequence, same G | `Left Rabbet - In G59`, `Right Rabbet - Out G59` | unchanged |
| Stable "G59 last" | Repeats / mixed | `B G59`, `Z G57`, `A G59`, `Y`, `B G59`, `C G58` | `Z G57`, `Y`, `C G58`, `B G59`, `A G59`, `B G59` |
| Sort by (has G59, name) | No explicit sequence (every setup once, model order) | `B G59`, `Z G57`, `A G59`, `Y`, `C G58` | `C G58`, `Y`, `Z G57`, `A G59`, `B G59` |
| No reordering | `g57_before_g59 = False` | `Setup 4 - G59`, `Setup 7 - G57` | unchanged |

Only the literal, **upper-case** `G59` moves a section. Every other section
(G54 to G58, or no workstop) counts as "before". A setup named `... g59` in
lower case is **not** moved (old quirk).

## 3. Transition between sections

Before every section except the first, NFE inserts this block:

```
M0

(******************)
(NEW SETUP, <text>)
(******************)

```

| Previous workstop | Next workstop | `<text>` | Operator action |
|---|---|---|---|
| G57 | G57 | `FLIP PART` | Flip the part about Y |
| G59 | G59 | `FLIP PART` | Flip the part about Y |
| G57 | G59 | `ROTATE PART 180` | Rotate 180 deg about Z |
| G59 | G57 | `ROTATE PART 180` | Rotate 180 deg about Z |
| G54..G59, both known, different | | `ROTATE PART 180` | |
| none | any | `FLIP PART` | |
| any | none | `FLIP PART` | |

The comparison is always with the **directly previous** section, including a
section that has no workstop. The workstop is found case-insensitively; the
first `G5[4-9]` in the name wins.

## 4. Header and footer

| Section | Header (`%`, `O####`, comments before the first `N<digits><space>` line) | Footer |
|---|---|---|
| First | kept | removed |
| Every later one | removed. If the file has **no** N-numbered line at all, nothing is removed. | removed |

Removing the footer means:

1. Trailing lines that are blank or `%` are removed.
2. Then **one** line that starts with `M30` is removed, if it is the last line left.

The program ends with a bare `M30` and `%`.

Every line has its trailing whitespace stripped. Input files may use LF,
CRLF or CR line endings.

**Old quirk: numbered M30.** The Stratos post writes `N### M30`, which does
not start with `M30`. Rule 2 therefore does **not** remove it, so every section
keeps its own `N### M30` right before the next `M0`, and the file ends with
`N### M30`, `M30`, `%`. The real old outputs look exactly like this (for
example `tests/fixtures/gcode/1-S5-IBUS789123.txt`). NFE copies this by
default. `strip_numbered_m30=True` removes `N### M30` too.

## 5. Renumbering

After splicing, every line that starts with `N<digits><whitespace>` is
renumbered in one continuous series: **N10, N15, N20, ...** (start 10, step 5).
Comments, `M0`, the bare `M30`, `%` and `O####` get no number. A bare `N10`
with nothing after it is not renumbered.

## 6. Output file

| Item | Value |
|---|---|
| Name | `1-{component_id}-{order_id}.txt`, e.g. `1-D1-IBUS447837.txt` |
| Line endings | **CRLF**, with a trailing CRLF after the final `%` (same as the old `open(path, 'w')` on Windows) |
| Encoding | Bytes are copied through unchanged (latin-1 round trip) |
| Writing | Atomic: a temp file in the same folder, then `os.replace` |
| Individual `.nc` | Temporary. The old code deleted them after splicing. |

## 7. Worked example

The plan is `["Setup 4 - G59", "Setup 7 - G57"]`. G59 is posted first and gets
1041; G57 is posted second and gets 1042.

`1041.nc` (Setup 4 - G59):

```
%
O1041
(MACHINE)
(  VENDOR ANDERSON)
N10 G90 G94 G17 G49
N15 G20
(LEFT RABBET IN)
N20 G59
N25 G0 X-1. Y0.5
N30 G1 Z0.25 F200.
N35 M30
%
```

`1042.nc` (Setup 7 - G57):

```
%
O1042
(MACHINE)
(  VENDOR ANDERSON)
N10 G90 G94 G17 G49
N15 G20
(RIGHT RABBET IN)
N20 G57
N25 G0 X1. Y0.5
N30 G1 Z0.25 F200.
N35 M30
%
```

Ordering moves G57 first. The report then reads:

| # | Setup | Workstop | Transition before |
|---|---|---|---|
| 1 | Setup 7 - G57 | G57 | none |
| 2 | Setup 4 - G59 | G59 | ROTATE PART 180 |

The result, `1-D1-IBUS1.txt` (CRLF):

```
%
O1042
(MACHINE)
(  VENDOR ANDERSON)
N10 G90 G94 G17 G49
N15 G20
(RIGHT RABBET IN)
N20 G57
N25 G0 X1. Y0.5
N30 G1 Z0.25 F200.
N35 M30
M0

(******************)
(NEW SETUP, ROTATE PART 180)
(******************)

N40 G90 G94 G17 G49
N45 G20
(LEFT RABBET IN)
N50 G59
N55 G0 X-1. Y0.5
N60 G1 Z0.25 F200.
N65 M30
M30
%
```

With `strip_numbered_m30=True`, the lines `N35 M30` and `N65 M30` disappear
and the numbering closes up (N35..N55 for the second section).
