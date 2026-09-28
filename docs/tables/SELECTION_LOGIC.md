# How toolpaths, workstops and drilling are chosen

This page explains the selection logic **in general terms**: which order
inputs lead to which toolpaths (Anderson), which workstops and holes
(Gannomat), and in what order. It covers the 3X8X series (doors, 3X82 and 3X86
stiles, panels). The exact decision tables the add-in executes are in
[TOOLPATH_SELECTION.md](TOOLPATH_SELECTION.md), [SPLICING.md](SPLICING.md) and
[DRILLING.md](DRILLING.md), and on the dashboard's **Logic tables** tab.

Sources: the formulas inside the live Fusion models (3X8X_Door, 3X82_Stile,
3X86_Stile, 3X8X_Panel_Standard, read 2026-09-28), the engineering sheets
"3X8X Door inputs & outputs v10", "3X82 Stile … v15", "3X86 Stile … v07", the
old pipeline code (`order_processor.py`, `cam_manager.py`,
`parameter_exporter.py`) and "Manufacturing Workflow v3".

## The chain

```
order inputs  ──►  Fusion model formulas  ──►  feature flags  ──►  pipeline picks
(swing, hinge,      (in each model)            (rabbet / notch /     setups, operations,
 sides, clearances)                             drilling per side)    workstops, order
```

The pipeline never guesses geometry: it reads the flags the model computed
after the order's values were applied (`read_values` → `cam_rules` /
`drilling`). So the rules below are the model's rules plus the pipeline's
selection rules.

**One toolpath (setup) per side.** A door or stile gets at most one setup per
side: none, one (one side) or two (both sides, spliced into one program). A
panel gets exactly one setup, chosen by its orientation, or none.

Glossary: *in-swing* = `swinging_out = 0`; *out-swing* = `swinging_out = 1`.
*Interior / exterior* = the face of the part. *Hinge side* = the side the
hinges are on; *latch side* = the other side.

---

## 1. Doors (3X8X)

Inputs used: `door_hinging_right`, `door_swinging_out`, `door_wall_post_hinging`,
`door_wall_post_latching`, `door_wall_keep_latching`, `door_perp_stile_keeper`,
`floor_to_ceiling`, `component_floor_clearance`, `component_ceiling_clearance`.

### 1.1 What gets machined on each side

| Feature | Present on a side when | Face |
|---|---|---|
| **Rabbet** | Always on the **hinge side**. On the **latch side** unless the door latches on a **wall keep** (`door_wall_keep_latching`) or a **perpendicular stile keeper** (`door_perp_stile_keeper`). | **Interior** if the door swings **out**, **exterior** if it swings **in** |
| **Bottom notch** | Floor clearance **below 4.31"** (3.31" notch + 1") **and** the side is the hinge side not hinged on a wall post, or the latch side not latching on a wall keep or wall post. | — |
| **Top notch** | `floor_to_ceiling = 1` **and** ceiling clearance **below 4.5"** (4" shoe + 0.5") **and** the same side condition as the bottom notch. | — |

Because the hinge side is always rabbeted, **every 3X8X door gets at least one
toolpath**.

### 1.2 Which setup (toolpath) each side uses

One setup per side that has any feature. The side and swing fix the workstop
(G57/G59); the rabbet face picks interior vs exterior setup. A side with only
notches (no rabbet) uses the interior setup with its rabbet operation
suppressed.

| Swing | Left side (rabbet / notch-only) | Right side (rabbet / notch-only) |
|---|---|---|
| **In-swing** (exterior rabbets) | **Setup 3 - G57** / Setup 1 - G57 | **Setup 8 - G59** / Setup 6 - G59 |
| **Out-swing** (interior rabbets) | **Setup 2 - G59** / Setup 2 - G59 | **Setup 5 - G57** / Setup 5 - G57 |

So for doors: **left side = G57 when in-swing, G59 when out-swing; right side
= G59 when in-swing, G57 when out-swing.**

Inside the chosen setup each operation is switched on only if its feature is
present: the rabbet operation by that side's rabbet flag, "Bottom Notch" by the
bottom-notch flag, "Top Notch" by the top-notch flag.

### 1.3 Order when both sides are machined

Both sections are spliced into one program. The **G57 section always comes
first**, then `M0` + `(NEW SETUP, ROTATE PART 180)`, then the G59 section
(one side is always G57 and the other G59, so the part is rotated, never
flipped).

### 1.4 Drilling (Gannomat)

| Item | Rule |
|---|---|
| Which side | Hinge holes are drilled on the **hinge side only**: left side when `door_hinging_right = 0`, right side when `= 1`. |
| Face | Interior when in-swing, exterior when out-swing (model flags `left/right_interior/exterior_drilling`). |
| **Workstop** | **WS1** when `door_hinging_right = door_swinging_out` (left-hand in-swing, right-hand out-swing); **WS4** otherwise (left-hand out-swing, right-hand in-swing). |
| Air drilling | The unused workstop always gets one "NO DRILLING REQUIRED" record at X 121, Y 0.197 (doors only drill on one workstop). |
| FlipRotate | Always 1. |
| Holes | 4 hinges × 2 holes (1.5" apart). Top hole 1.72" from the door top (+ the top-notch depth when floor-to-ceiling and not on a wall post); bottom margin 1.72" (+ the bottom-notch depth when notched and not on a wall post); hinges spaced evenly between. With a 1" floor clearance (and not on a wall post) the mid-top hinge moves up 0.823" and the mid-bottom hinge down 1.646". PositionX = the hole's distance from the top, PositionY = 0.968" (`hinge_hole_x_dist`). |
| DrillName | The order's `program_name` if given, else "&lt;series&gt; RH or LH, O/S or I/S, DOOR", e.g. `3082G.67P RH I/S DOOR`. |

---

## 2. Stiles (3X82 and 3X86)

Inputs used, per side (`LD_…` = door on the stile's left side, `RD_…` = right
side): `left_side_door`, `right_side_door`, `LD/RD_hinging_right`,
`LD/RD_swinging_out`, `LD/RD_height`, `LD/RD_floor_clearance`
(3X86 also `LD/RD_ceiling_clearance`).

### 2.1 What gets machined on each side

| Feature | Present on a side when | Face |
|---|---|---|
| **Rabbet** | A door is on that side (`left_side_door` / `right_side_door`). | **Interior** if that door swings **in**, **exterior** if it swings **out** |
| **Drilling** | That side's door **hinges onto this stile**: left side when `LD_hinging_right = 1`, right side when `RD_hinging_right = 0`. Otherwise the door latches here ("KEEPING") and the side is only rabbeted. | Same face as the rabbet |

The rabbet runs out through the bottom of the stile when the door's floor
clearance is below 7.625" (through-rabbeting), and out through the top when
the rabbet would otherwise stop less than 2" below the stile top (3X82: rabbet
start 1.386" above the door) or the door's ceiling clearance is below 7.625"
(3X86). This changes the rabbet's length, not which toolpath is used.

### 2.2 Which setup (toolpath) each side uses

Each side with a door gets exactly one setup, fixed by side and face (the
stile models carry the same mapping in their `left_rabbet_tag` /
`right_rabbet_tag` parameters):

| Side | Door swings **in** (interior rabbet) | Door swings **out** (exterior rabbet) |
|---|---|---|
| Left | **Left Rabbet - In G59** | **Left Rabbet - Out G57** |
| Right | **Right Rabbet - In G57** | **Right Rabbet - Out G59** |

Same for 3X82 and 3X86 (the 3X86 model names the setups "Left Rabbet - Out - G57"
etc.; the pipeline matches both spellings). Each stile setup holds one rabbet
operation, which is always activated.

### 2.3 Order when both sides have doors

| Swings | Setups | Transition | Which goes first |
|---|---|---|---|
| **Same** (both in or both out) | one G57 + one G59 | ROTATE PART 180 | The **G57** section (both in: right side first; both out: left side first) |
| **Opposite**, left in + right out | both **G59** | FLIP PART | see rule below |
| **Opposite**, left out + right in | both **G57** | FLIP PART | see rule below |

First-side rule for opposite swings (both sides are on the same workstop, so
the drilling decides):

1. Only one side is drilled (only one door hinges onto the stile) → **the
   drilled side first**.
2. Both sides drilled → **the exterior-drilled side first** (the out-swing
   door's side; FlipRotate 3 at the Gannomat, see 2.4).
3. Neither side drilled (both doors latch here) → **3X82: left side first;
   3X86: right side first.**

### 2.4 Drilling (Gannomat)

| Item | Rule |
|---|---|
| Which sides | Only the sides whose door hinges onto the stile (2.1). |
| **Workstop, 3X82** | Left side: **WS1** if interior (door in-swing), **WS4** if exterior. Right side: **WS4** if interior, **WS1** if exterior. |
| **Workstop, 3X86** | The opposite of 3X82: left interior **WS4**, left exterior **WS1**, right interior **WS1**, right exterior **WS4**. |
| Two drilled sides, same swings | One side on WS1, the other on WS4 → the part is rotated 180° between them. FlipRotate 1 on all holes. |
| Two drilled sides, opposite swings | Both sides on the same workstop → the part is flipped. **FlipRotate 3 on the exterior side's holes (drilled first), 4 on the interior side's holes.** |
| Air drilling | When the holes use only one workstop (one drilled side, and not the flip case), one "NO DRILLING REQUIRED" record is added on the other workstop at X 121, Y 0.197. A stile with no drilled side gets one such record on WS1 **and** WS4. |
| Holes | Per drilled side 8 holes (4 hinges × 2): the door's hinge positions moved onto the stile by `stile height − (door height + door floor clearance − 1")`, plus +1/32" on the mid hinges and −5/16" on the bottom hinge. **3X86: minus 1.4375"** (leveling bar). PositionY = 1.268". |
| DrillName | The order's `program_name` if given, else the series followed by one segment per side with a door, e.g. `3082G.67P RH I/S HINGE \| LH O/S KEEPING` (HINGE = drilled side, KEEPING = latch side). |

These workstops match the "Gannomat stiles" decision tree in Manufacturing
Workflow v3: 3X82 same swings → WS1, rotate, WS4; left in + right out → WS1,
flip, WS1; left out + right in → WS4, flip, WS4 (3X86 mirrored).

---

## 3. Panels (3X8X_Panel_Standard)

Inputs used: `component_height`, `component_width`,
`component_floor_clearance`, `component_ceiling_clearance`, `panel_section`
(`WHOLE`, `TOP`, `BOTTOM`), `panel_abuts_inline_stile_front/back`,
`panel_front/back_inline_stile_floor_to_ceiling`, `cutout`.

### 3.1 What gets machined

| Feature | Present when |
|---|---|
| **Bottom notch, front / back edge** | The panel is not a `TOP` section **and** floor clearance **below 4.5"** (3.5" notch + 1") **and** the panel abuts an inline stile on that edge. |
| **Top notch, front / back edge** | The panel is not a `BOTTOM` section **and** ceiling clearance is **above 0 and below 4.5"** **and** the panel abuts an inline stile on that edge **that runs floor to ceiling**. |
| **Cutout A / Cutout B** | The order's `cutout` names one or two cutouts ("B-354 & B-386" → A = B-354, B = B-386; "+" also accepted). Known codes: B-354, B-386, B-4354, B-347/B-357, B-3471/B-34715/B-3571/B-35715. Only for `WHOLE` or `BOTTOM` sections. |

No feature → **no toolpath** (the manufacture step is skipped).

### 3.2 Which setup (the one toolpath)

| Orientation | Setup |
|---|---|
| **Height ≥ width** | **Setup Back Bottom Corner G58** |
| **Width > height** | **Setup Back Top Corner G58** |

Inside it, each notch and cutout operation is active only if its feature is
present; the extra "Adaptive1" operation is always suppressed. Panels are
not drilled.

---

## 4. Components without toolpaths or drilling rules

| Component | Toolpaths | Drilling |
|---|---|---|
| 3X81 stile | None: no toolpath table covers 3X81 (the model has no manufacturing setups), so the manufacture step is skipped. | Drilled with the 3X82 rules if a door hinges onto it (old code behaviour; the workflow sheet shows no drilling station for 3X81 — to be confirmed). |
| 2X8X doors / stiles | None (no toolpath table for 2X8X). | Doors and stiles are drilled with the same rules as 3X8X (workstops by the door rule; stiles with the 3X82 table). |

## 5. Things to confirm

See the "Review" notes in [TOOLPATH_SELECTION.md](TOOLPATH_SELECTION.md) and
section 8 of [DRILLING.md](DRILLING.md). The main open points are the old
spreadsheets that disagree with the code (door out-swing workstops, stile
both-in order, 3X86 single-door mapping), the numbered `M30` kept at the end
of each spliced section, and 3X81 drilling.
