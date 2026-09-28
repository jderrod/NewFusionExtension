# Toolpath (CAM setup) selection tables

For every component NFE decides **which CAM setups to post, in which order**,
and **which operations inside those setups are active**. The old add-in
(FusionExtensionVM) had this hard-coded in `order_processor.py`
(`_build_door/stile/panel_setup_sequence`) and `cam_manager.py`
(`suppress_door/panel_toolpaths`). NFE keeps the same decisions as editable
**tables** called *profiles*.

- Format and evaluation: `addin/NewFusionExtension/nfe/cam_rules.py`
- Condition language: `addin/NewFusionExtension/nfe/expr.py`
- Default tables: `addin/NewFusionExtension/nfe/cam_defaults.py`
- Tests: `tests/test_cam_rules.py` compare the default tables with copies of
  the old code for **every** combination of the on/off inputs (512 door
  cases, 256 stile cases for each of 3082 / 3081 / 3182 / 3086 / 3186, over
  3 000 panel cases), using the real setup and operation names from the
  Fusion models.

The tables below are the defaults. They follow the old **code**. Where the
old spreadsheets say something different, the row is marked ⚠ and the
difference is listed under *Conflicts with old documents — please confirm*
for that component. Nothing was changed to follow a spreadsheet: please tell
us which version is right.

---

## How a table is read

1. **Pick the profile.** Same matching as the model rules: component type,
   then the `series` pattern if there is one (e.g. `3?86*` = any 3X86
   series), otherwise the family (`3X8X`).
2. **Read the variables.** Each variable reads one Fusion model parameter,
   one order parameter, or "model, else order". Flags are 0/1; lengths are in
   inches; text parameters are text (the quotes Fusion puts around text
   values are removed). A parameter that cannot be found uses the default
   (0, or empty text) and is reported as *missing*.
3. **Derived values** are computed next, top to bottom.
4. **Setup selection:** the rows are checked top to bottom and the **first
   true row wins**. Its setups are posted in the listed order. An empty list
   means "nothing to machine": no G-code, CAM is skipped. If no row is true
   the component fails with an error.
5. **Operation activation** is applied to the operations inside the chosen
   setups. Operations that no rule names are left as they are, suppressed, or
   activated, as the profile says.
6. **Splicing:** the posted setups are joined into one program (see
   [SPLICING.md](SPLICING.md)). `g57_before_g59 = true` moves G59 sections
   after the others, exactly like the old post processor.

### Name matching

Setup and operation names are matched exactly first, then ignoring case,
extra spaces, dashes (`Left Rabbet - Out - G57` = `Left Rabbet - Out G57`)
and a trailing copy number (`Cutout A (2)` = `Cutout A`). A name is **never**
matched by a part of it, so `Setup 1` can never pick `Setup 10`. A setup the
model does not have is reported as missing.

### Condition language

Conditions use `and`, `or`, `not`, `== != < <= > >=` (also chained, e.g.
`0 < x <= 5`), `+ - * /` on numbers, parentheses, numbers, `'text'`, and
`true` / `false`. A variable that is 0, empty text or false counts as false.
Nothing else is allowed (no functions, no attribute access), so an edited
table can never run code.

---

<!-- BEGIN GENERATED: nfe.cam_rules.render_markdown(DEFAULT_PROFILES) -->

## 3X8X doors (`door-3x8x`)

Applies to: type `door`, family `3X8X`.

Each side (left / right) with any rabbet or notch gets one setup. The side's setup is the EXTERIOR-rabbet one when that side's exterior rabbeting is on, otherwise the INTERIOR-rabbet one (notch-only sides use the interior setup with the rabbet suppressed). Out-swing doors use G59 for the left side and G57 for the right; in-swing doors the opposite.

### Variables

| Variable | Parameter | Read from | Type | Default |
|---|---|---|---|---|
| `swing_out` | `door_swinging_out` | model, else order | bool | 0 |
| `L_int` | `left_interior_rabbeting` | model | bool | 0 |
| `L_ext` | `left_exterior_rabbeting` | model | bool | 0 |
| `L_bn` | `bottom_left_notching` | model | bool | 0 |
| `L_tn` | `top_left_notching` | model | bool | 0 |
| `R_int` | `right_interior_rabbeting` | model | bool | 0 |
| `R_ext` | `right_exterior_rabbeting` | model | bool | 0 |
| `R_bn` | `bottom_right_notching` | model | bool | 0 |
| `R_tn` | `top_right_notching` | model | bool | 0 |
| `L_any` | = `L_int or L_ext or L_bn or L_tn` | derived | | |
| `R_any` | = `R_int or R_ext or R_bn or R_tn` | derived | | |

### Setup selection (first matching row wins)

| Row | When (plain words) | Expression | Setups posted, in order |
|---|---|---|---|
| D1 | No rabbet or notch on either side: no G-code | `not L_any and not R_any` | *none (skip CAM)* |
| D2 ⚠ | Out-swing, left side only, exterior rabbet | `swing_out and L_any and not R_any and L_ext` | `Setup 4 - G59` |
| D3 ⚠ | Out-swing, left side only, interior rabbet or notches only | `swing_out and L_any and not R_any and not L_ext` | `Setup 2 - G59` |
| D4 ⚠ | Out-swing, right side only, exterior rabbet | `swing_out and R_any and not L_any and R_ext` | `Setup 7 - G57` |
| D5 ⚠ | Out-swing, right side only, interior rabbet or notches only | `swing_out and R_any and not L_any and not R_ext` | `Setup 5 - G57` |
| D6 ⚠ | Out-swing, both sides; left exterior, right exterior | `swing_out and L_any and R_any and L_ext and R_ext` | `Setup 4 - G59` → `Setup 7 - G57` |
| D7 ⚠ | Out-swing, both sides; left exterior, right interior/notches | `swing_out and L_any and R_any and L_ext and not R_ext` | `Setup 4 - G59` → `Setup 5 - G57` |
| D8 ⚠ | Out-swing, both sides; left interior/notches, right exterior | `swing_out and L_any and R_any and not L_ext and R_ext` | `Setup 2 - G59` → `Setup 7 - G57` |
| D9 ⚠ | Out-swing, both sides; left interior/notches, right interior/notches | `swing_out and L_any and R_any and not L_ext and not R_ext` | `Setup 2 - G59` → `Setup 5 - G57` |
| D10 ⚠ | In-swing, left side only, exterior rabbet | `not swing_out and L_any and not R_any and L_ext` | `Setup 3 - G57` |
| D11 ⚠ | In-swing, left side only, interior rabbet or notches only | `not swing_out and L_any and not R_any and not L_ext` | `Setup 1 - G57` |
| D12 ⚠ | In-swing, right side only, exterior rabbet | `not swing_out and R_any and not L_any and R_ext` | `Setup 8 - G59` |
| D13 ⚠ | In-swing, right side only, interior rabbet or notches only | `not swing_out and R_any and not L_any and not R_ext` | `Setup 6 - G59` |
| D14 | In-swing, both sides; left exterior, right exterior | `not swing_out and L_any and R_any and L_ext and R_ext` | `Setup 3 - G57` → `Setup 8 - G59` |
| D15 | In-swing, both sides; left exterior, right interior/notches | `not swing_out and L_any and R_any and L_ext and not R_ext` | `Setup 3 - G57` → `Setup 6 - G59` |
| D16 | In-swing, both sides; left interior/notches, right exterior | `not swing_out and L_any and R_any and not L_ext and R_ext` | `Setup 1 - G57` → `Setup 8 - G59` |
| D17 | In-swing, both sides; left interior/notches, right interior/notches | `not swing_out and L_any and R_any and not L_ext and not R_ext` | `Setup 1 - G57` → `Setup 6 - G59` |

Splice options: `g57_before_g59 = true`.

### Operation activation (inside the selected setups)

| Operation | Active when | Expression |
|---|---|---|
| Left Interior Rabbet | left_interior_rabbeting on | `L_int` |
| Left Exterior Rabbet | left_exterior_rabbeting on | `L_ext` |
| Left Bottom Notch | bottom_left_notching on | `L_bn` |
| Left Top Notch | top_left_notching on | `L_tn` |
| Right Interior Rabbet | right_interior_rabbeting on | `R_int` |
| Right Exterior Rabbet | right_exterior_rabbeting on | `R_ext` |
| Right Bottom Notch | bottom_right_notching on | `R_bn` |
| Right Top Notch | top_right_notching on | `R_tn` |

Operations not listed: **leave** (left as they are in the model).

### Conflicts with old documents — please confirm

- **D2, D3**: InstructionSelectionLogic.xlsx 'Door Toolpath Selection' scenario 4 says out-swing left side -> Setup 1 - G57 (interior) / Setup 3 - G57 (exterior), and 'Door Instructions' gives Flip-G57.pdf; the old code and Toolpath_Configuration_Matrix.xlsx use G59 (Setup 2 / Setup 4).
- **D4, D5**: InstructionSelectionLogic.xlsx 'Door Toolpath Selection' scenario 3 says out-swing right side -> Setup 6 - G59 (interior) / Setup 8 - G59 (exterior), and 'Door Instructions' gives Flip-Rotate-G59.pdf; the old code and Toolpath_Configuration_Matrix.xlsx use G57 (Setup 5 / Setup 7).
- **D6, D7, D8, D9**: InstructionSelectionLogic.xlsx 'Door Toolpath Selection' scenario 7 says out-swing both sides -> left Setup 1/3 - G57 + right Setup 6/8 - G59, G57 first; the old code and Toolpath_Configuration_Matrix.xlsx use left G59 (Setup 2/4) + right G57 (Setup 5/7). (Final G-code order is G57 first either way because of the splicer's G57-before-G59 sort.)
- **D10, D11**: InstructionSelectionLogic.xlsx 'Door Toolpath Selection' scenario 6 says in-swing left side -> Setup 2 - G59 (interior) / Setup 4 - G59 (exterior), and 'Door Instructions' gives Flip-Rotate-G59.pdf; the old code and Toolpath_Configuration_Matrix.xlsx use G57 (Setup 1 / Setup 3).
- **D12, D13**: InstructionSelectionLogic.xlsx 'Door Instructions' gives Flip-G57.pdf (a G57 setup) for in-swing right side only; the old code, Toolpath_Configuration_Matrix.xlsx and the 'Door Toolpath Selection' sheet use G59 (Setup 6 / Setup 8).
- **General**: When a side has BOTH interior and exterior rabbeting on, the old code picks the exterior setup, so the interior rabbet (which only exists in the interior setups) is never machined. No document covers this case.
- **General**: Out-swing, both sides (D6-D9): the rows list left G59 first, exactly like the old code, but the old post processor (and the splicer, g57_before_g59) always moves G57 sections before G59, so the right-side G57 setup is machined first.

## 3X82 stiles (`stile-3x82`)

Applies to: type `stile`, series pattern `3?82*`.

Setups are chosen from the doors hanging on the stile (left_side_door / right_side_door) and their swing (LD/RD_swinging_out). For opposite swings the two setups use the same workstop (part is flipped) and the drilling flags decide which side is machined first: the side with drilling first; with drilling on both sides the side whose drilling is exterior only (FlipRotate=3, processed first at the Gannomat) goes first. Each setup holds one rabbet operation, which is always made active. 3X81 stiles are deliberately not covered: the 3X81 model has no manufacturing setups, so they skip the manufacture step.

### Variables

| Variable | Parameter | Read from | Type | Default |
|---|---|---|---|---|
| `L_door` | `left_side_door` | model, else order | bool | 0 |
| `R_door` | `right_side_door` | model, else order | bool | 0 |
| `L_out` | `LD_swinging_out` | model, else order | bool | 0 |
| `R_out` | `RD_swinging_out` | model, else order | bool | 0 |
| `LI_drill` | `left_interior_drilling` | model | bool | 0 |
| `LE_drill` | `left_exterior_drilling` | model | bool | 0 |
| `RI_drill` | `right_interior_drilling` | model | bool | 0 |
| `RE_drill` | `right_exterior_drilling` | model | bool | 0 |
| `two_doors` | = `L_door and R_door` | derived | | |
| `L_drill` | = `LI_drill or LE_drill` | derived | | |
| `R_drill` | = `RI_drill or RE_drill` | derived | | |

### Setup selection (first matching row wins)

| Row | When (plain words) | Expression | Setups posted, in order |
|---|---|---|---|
| S1 | No doors on the stile: no G-code | `not L_door and not R_door` | *none (skip CAM)* |
| S2 | Left door only, out-swing (A: G57) | `L_door and not R_door and L_out` | `Left Rabbet - Out G57` |
| S3 | Left door only, in-swing (B: G59) | `L_door and not R_door and not L_out` | `Left Rabbet - In G59` |
| S4 | Right door only, out-swing (B: G59) | `R_door and not L_door and R_out` | `Right Rabbet - Out G59` |
| S5 | Right door only, in-swing (A: G57) | `R_door and not L_door and not R_out` | `Right Rabbet - In G57` |
| S6 ⚠ | Both doors in-swing: right G57, rotate 180, left G59 | `two_doors and not L_out and not R_out` | `Right Rabbet - In G57` → `Left Rabbet - In G59` |
| S7 ⚠ | Both doors out-swing: left G57, rotate 180, right G59 | `two_doors and L_out and R_out` | `Left Rabbet - Out G57` → `Right Rabbet - Out G59` |
| S8 ⚠ | Left in / right out (G59 flip G59), no drilling: left side first (3X82 default) | `two_doors and not L_out and R_out and not L_drill and not R_drill` | `Left Rabbet - In G59` → `Right Rabbet - Out G59` |
| S9 ⚠ | Left in / right out, drilling on left only: left side first | `two_doors and not L_out and R_out and L_drill and not R_drill` | `Left Rabbet - In G59` → `Right Rabbet - Out G59` |
| S10 ⚠ | Left in / right out, drilling on right only: right side first | `two_doors and not L_out and R_out and R_drill and not L_drill` | `Right Rabbet - Out G59` → `Left Rabbet - In G59` |
| S11 ⚠ | Left in / right out, drilling both sides, left is FlipRotate=3 (exterior only): left side first | `two_doors and not L_out and R_out and L_drill and R_drill and not LI_drill` | `Left Rabbet - In G59` → `Right Rabbet - Out G59` |
| S12 ⚠ | Left in / right out, drilling both sides, right is FlipRotate=3: right side first | `two_doors and not L_out and R_out and L_drill and R_drill and LI_drill and not RI_drill` | `Right Rabbet - Out G59` → `Left Rabbet - In G59` |
| S13 ⚠ | Left in / right out, drilling both sides, both FlipRotate=4: left side first | `two_doors and not L_out and R_out and L_drill and R_drill and LI_drill and RI_drill` | `Left Rabbet - In G59` → `Right Rabbet - Out G59` |
| S14 ⚠ | Left out / right in (G57 flip G57), no drilling: left side first (3X82 default) | `two_doors and L_out and not R_out and not L_drill and not R_drill` | `Left Rabbet - Out G57` → `Right Rabbet - In G57` |
| S15 ⚠ | Left out / right in, drilling on left only: left side first | `two_doors and L_out and not R_out and L_drill and not R_drill` | `Left Rabbet - Out G57` → `Right Rabbet - In G57` |
| S16 ⚠ | Left out / right in, drilling on right only: right side first | `two_doors and L_out and not R_out and R_drill and not L_drill` | `Right Rabbet - In G57` → `Left Rabbet - Out G57` |
| S17 ⚠ | Left out / right in, drilling both sides, left is FlipRotate=3 (exterior only): left side first | `two_doors and L_out and not R_out and L_drill and R_drill and not LI_drill` | `Left Rabbet - Out G57` → `Right Rabbet - In G57` |
| S18 ⚠ | Left out / right in, drilling both sides, right is FlipRotate=3: right side first | `two_doors and L_out and not R_out and L_drill and R_drill and LI_drill and not RI_drill` | `Right Rabbet - In G57` → `Left Rabbet - Out G57` |
| S19 ⚠ | Left out / right in, drilling both sides, both FlipRotate=4: left side first | `two_doors and L_out and not R_out and L_drill and R_drill and LI_drill and RI_drill` | `Left Rabbet - Out G57` → `Right Rabbet - In G57` |

Splice options: `g57_before_g59 = true`.

### Operation activation (inside the selected setups)

Operations not listed: **activate** (made active).

### Conflicts with old documents — please confirm

- **S6**: InstructionSelectionLogic.xlsx 'Stile Toolpath Selection' says both in-swing -> Left Rabbet - In G57 then Right Rabbet - In G59; the old code and Toolpath_Configuration_Matrix.xlsx use Right Rabbet - In G57 then Left Rabbet - In G59.
- **S7**: InstructionSelectionLogic.xlsx 'Stile Toolpath Selection' says both out-swing -> Right Rabbet - Out G57 then Left Rabbet - Out G57 (both G57, probably a typo); the old code and Toolpath_Configuration_Matrix.xlsx use Left Rabbet - Out G57 then Right Rabbet - Out G59.
- **S8, S9, S10, S11, S12**: InstructionSelectionLogic.xlsx 'Stile Toolpath Selection' always lists the left side first for opposite swings (no drilling or series rule). Manufacturing Workflow v3 2026_04_21.xlsx 'Anderson stiles' (3X82) says left I/S + right O/S -> A:G57 & Flip & A:G57; the old code, InstructionSelectionLogic.xlsx and Toolpath_Configuration_Matrix.xlsx use G59 for both sides (B:G59 & Flip & B:G59). 
- **S13**: InstructionSelectionLogic.xlsx 'Stile Toolpath Selection' always lists the left side first for opposite swings (no drilling or series rule). Manufacturing Workflow v3 2026_04_21.xlsx 'Anderson stiles' (3X82) says left I/S + right O/S -> A:G57 & Flip & A:G57; the old code, InstructionSelectionLogic.xlsx and Toolpath_Configuration_Matrix.xlsx use G59 for both sides (B:G59 & Flip & B:G59).  Not in any document: when BOTH sides have interior drilling (FlipRotate=4 on both) the old code defaults to left first. A side with interior AND exterior drilling counts as FlipRotate=4 (interior wins). Toolpath_Configuration_Matrix.xlsx only describes the 'left FR=3' / 'right FR=3' cases.
- **S14, S15, S16, S17, S18**: InstructionSelectionLogic.xlsx 'Stile Toolpath Selection' always lists the left side first for opposite swings (no drilling or series rule). Manufacturing Workflow v3 2026_04_21.xlsx 'Anderson stiles' (3X82) says left O/S + right I/S -> B:G59 & Flip & B:G59; the old code, InstructionSelectionLogic.xlsx and Toolpath_Configuration_Matrix.xlsx use G57 for both sides (A:G57 & Flip & A:G57). 
- **S19**: InstructionSelectionLogic.xlsx 'Stile Toolpath Selection' always lists the left side first for opposite swings (no drilling or series rule). Manufacturing Workflow v3 2026_04_21.xlsx 'Anderson stiles' (3X82) says left O/S + right I/S -> B:G59 & Flip & B:G59; the old code, InstructionSelectionLogic.xlsx and Toolpath_Configuration_Matrix.xlsx use G57 for both sides (A:G57 & Flip & A:G57).  Not in any document: when BOTH sides have interior drilling (FlipRotate=4 on both) the old code defaults to left first. A side with interior AND exterior drilling counts as FlipRotate=4 (interior wins). Toolpath_Configuration_Matrix.xlsx only describes the 'left FR=3' / 'right FR=3' cases.
- **General**: The old code ran 3X81 stiles through the 3X82 sequence logic; the 3X81 model has no manufacturing setups and Manufacturing Workflow v3 2026_04_21.xlsx flow chart 'Stile 3X81' has no Anderson station, so 3X81 is excluded here (series pattern 3?82*).

## 3X86 stiles (`stile-3x86`)

Applies to: type `stile`, series pattern `3?86*`.

Setups are chosen from the doors hanging on the stile (left_side_door / right_side_door) and their swing (LD/RD_swinging_out). For opposite swings the two setups use the same workstop (part is flipped) and the drilling flags decide which side is machined first: the side with drilling first; with drilling on both sides the side whose drilling is exterior only (FlipRotate=3, processed first at the Gannomat) goes first. Each setup holds one rabbet operation, which is always made active. Setup names are written as in the 3X82 model ('Left Rabbet - Out G57'); they also match the 3X86 model's 'Left Rabbet - Out - G57'.

### Variables

| Variable | Parameter | Read from | Type | Default |
|---|---|---|---|---|
| `L_door` | `left_side_door` | model, else order | bool | 0 |
| `R_door` | `right_side_door` | model, else order | bool | 0 |
| `L_out` | `LD_swinging_out` | model, else order | bool | 0 |
| `R_out` | `RD_swinging_out` | model, else order | bool | 0 |
| `LI_drill` | `left_interior_drilling` | model | bool | 0 |
| `LE_drill` | `left_exterior_drilling` | model | bool | 0 |
| `RI_drill` | `right_interior_drilling` | model | bool | 0 |
| `RE_drill` | `right_exterior_drilling` | model | bool | 0 |
| `two_doors` | = `L_door and R_door` | derived | | |
| `L_drill` | = `LI_drill or LE_drill` | derived | | |
| `R_drill` | = `RI_drill or RE_drill` | derived | | |

### Setup selection (first matching row wins)

| Row | When (plain words) | Expression | Setups posted, in order |
|---|---|---|---|
| S1 | No doors on the stile: no G-code | `not L_door and not R_door` | *none (skip CAM)* |
| S2 ⚠ | Left door only, out-swing (A: G57) | `L_door and not R_door and L_out` | `Left Rabbet - Out G57` |
| S3 ⚠ | Left door only, in-swing (B: G59) | `L_door and not R_door and not L_out` | `Left Rabbet - In G59` |
| S4 ⚠ | Right door only, out-swing (B: G59) | `R_door and not L_door and R_out` | `Right Rabbet - Out G59` |
| S5 ⚠ | Right door only, in-swing (A: G57) | `R_door and not L_door and not R_out` | `Right Rabbet - In G57` |
| S6 ⚠ | Both doors in-swing: right G57, rotate 180, left G59 | `two_doors and not L_out and not R_out` | `Right Rabbet - In G57` → `Left Rabbet - In G59` |
| S7 ⚠ | Both doors out-swing: left G57, rotate 180, right G59 | `two_doors and L_out and R_out` | `Left Rabbet - Out G57` → `Right Rabbet - Out G59` |
| S8 ⚠ | Left in / right out (G59 flip G59), no drilling: right side first (3X86 default) | `two_doors and not L_out and R_out and not L_drill and not R_drill` | `Right Rabbet - Out G59` → `Left Rabbet - In G59` |
| S9 ⚠ | Left in / right out, drilling on left only: left side first | `two_doors and not L_out and R_out and L_drill and not R_drill` | `Left Rabbet - In G59` → `Right Rabbet - Out G59` |
| S10 ⚠ | Left in / right out, drilling on right only: right side first | `two_doors and not L_out and R_out and R_drill and not L_drill` | `Right Rabbet - Out G59` → `Left Rabbet - In G59` |
| S11 ⚠ | Left in / right out, drilling both sides, left is FlipRotate=3 (exterior only): left side first | `two_doors and not L_out and R_out and L_drill and R_drill and not LI_drill` | `Left Rabbet - In G59` → `Right Rabbet - Out G59` |
| S12 ⚠ | Left in / right out, drilling both sides, right is FlipRotate=3: right side first | `two_doors and not L_out and R_out and L_drill and R_drill and LI_drill and not RI_drill` | `Right Rabbet - Out G59` → `Left Rabbet - In G59` |
| S13 ⚠ | Left in / right out, drilling both sides, both FlipRotate=4: left side first | `two_doors and not L_out and R_out and L_drill and R_drill and LI_drill and RI_drill` | `Left Rabbet - In G59` → `Right Rabbet - Out G59` |
| S14 ⚠ | Left out / right in (G57 flip G57), no drilling: right side first (3X86 default) | `two_doors and L_out and not R_out and not L_drill and not R_drill` | `Right Rabbet - In G57` → `Left Rabbet - Out G57` |
| S15 ⚠ | Left out / right in, drilling on left only: left side first | `two_doors and L_out and not R_out and L_drill and not R_drill` | `Left Rabbet - Out G57` → `Right Rabbet - In G57` |
| S16 ⚠ | Left out / right in, drilling on right only: right side first | `two_doors and L_out and not R_out and R_drill and not L_drill` | `Right Rabbet - In G57` → `Left Rabbet - Out G57` |
| S17 ⚠ | Left out / right in, drilling both sides, left is FlipRotate=3 (exterior only): left side first | `two_doors and L_out and not R_out and L_drill and R_drill and not LI_drill` | `Left Rabbet - Out G57` → `Right Rabbet - In G57` |
| S18 ⚠ | Left out / right in, drilling both sides, right is FlipRotate=3: right side first | `two_doors and L_out and not R_out and L_drill and R_drill and LI_drill and not RI_drill` | `Right Rabbet - In G57` → `Left Rabbet - Out G57` |
| S19 ⚠ | Left out / right in, drilling both sides, both FlipRotate=4: left side first | `two_doors and L_out and not R_out and L_drill and R_drill and LI_drill and RI_drill` | `Left Rabbet - Out G57` → `Right Rabbet - In G57` |

Splice options: `g57_before_g59 = true`.

### Operation activation (inside the selected setups)

Operations not listed: **activate** (made active).

### Conflicts with old documents — please confirm

- **S2, S3, S4, S5**: Manufacturing Workflow v3 2026_04_21.xlsx 'Anderson stiles' (3X86) gives a mirrored single-door mapping: A:G57 for right O/S or left I/S, B:G59 for the other cases (one cell reads 'None | O/S or None | I/S', probably a typo); the old code, InstructionSelectionLogic.xlsx and Toolpath_Configuration_Matrix.xlsx use the 3X82 mapping (Left Out G57, Left In G59, Right Out G59, Right In G57) for 3X86 too.
- **S6**: InstructionSelectionLogic.xlsx 'Stile Toolpath Selection' says both in-swing -> Left Rabbet - In G57 then Right Rabbet - In G59; the old code and Toolpath_Configuration_Matrix.xlsx use Right Rabbet - In G57 then Left Rabbet - In G59.
- **S7**: InstructionSelectionLogic.xlsx 'Stile Toolpath Selection' says both out-swing -> Right Rabbet - Out G57 then Left Rabbet - Out G57 (both G57, probably a typo); the old code and Toolpath_Configuration_Matrix.xlsx use Left Rabbet - Out G57 then Right Rabbet - Out G59.
- **S8, S9, S10, S11, S12, S14, S15, S16, S17, S18**: InstructionSelectionLogic.xlsx 'Stile Toolpath Selection' always lists the left side first for opposite swings (no drilling or series rule).
- **S13, S19**: InstructionSelectionLogic.xlsx 'Stile Toolpath Selection' always lists the left side first for opposite swings (no drilling or series rule). Not in any document: when BOTH sides have interior drilling (FlipRotate=4 on both) the old code defaults to left first. A side with interior AND exterior drilling counts as FlipRotate=4 (interior wins). Toolpath_Configuration_Matrix.xlsx only describes the 'left FR=3' / 'right FR=3' cases.

## 3X8X panels (`panel-3x8x`)

Applies to: type `panel`, family `3X8X`.

One setup, chosen by orientation: portrait (height >= width) posts the back-bottom corner setup, landscape the back-top corner setup. Both setups contain the same operations (the second setup's names carry a ' (2)' suffix).

### Variables

| Variable | Parameter | Read from | Type | Default |
|---|---|---|---|---|
| `height` | `component_height` | model | number | 0 |
| `width` | `component_width` | model | number | 0 |
| `notch_FB` | `notching_front_edge_bottom` | model | bool | 0 |
| `notch_FT` | `notching_front_edge_top` | model | bool | 0 |
| `notch_BB` | `notching_back_edge_bottom` | model | bool | 0 |
| `notch_BT` | `notching_back_edge_top` | model | bool | 0 |
| `cutA_w` | `cutout_A_width` | model | number | 0 |
| `cutA_h` | `cutout_A_height` | model | number | 0 |
| `cutB_w` | `cutout_B_width` | model | number | 0 |
| `cutB_h` | `cutout_B_height` | model | number | 0 |
| `cutA_text` | `cutout_A` | model | text | '' |
| `cutB_text` | `cutout_B` | model | text | '' |
| `cutA` | = `(cutA_w > 0 and cutA_h > 0) or cutA_text != ''` | derived | | |
| `cutB` | = `(cutB_w > 0 and cutB_h > 0) or cutB_text != ''` | derived | | |
| `notch_any` | = `notch_FB or notch_FT or notch_BB or notch_BT` | derived | | |
| `any_feature` | = `notch_any or cutA or cutB` | derived | | |
| `size_known` | = `not (height == 0 and width == 0)` | derived | | |

### Setup selection (first matching row wins)

| Row | When (plain words) | Expression | Setups posted, in order |
|---|---|---|---|
| P1 | No notches and no cutouts: no G-code | `size_known and not any_feature` | *none (skip CAM)* |
| P2 | Portrait (height >= width) | `size_known and height >= width` | `Setup Back Bottom Corner G58` |
| P3 | Landscape (width > height) | `size_known and height < width` | `Setup Back Top Corner G58` |

Splice options: `g57_before_g59 = true`.

### Operation activation (inside the selected setups)

| Operation | Active when | Expression |
|---|---|---|
| Front Bottom Notch | notching_front_edge_bottom on | `notch_FB` |
| Front Top Notch | notching_front_edge_top on | `notch_FT` |
| Back Bottom Notch | notching_back_edge_bottom on | `notch_BB` |
| Back Top Notch | notching_back_edge_top on | `notch_BT` |
| Cutout A | cutout_A width and height > 0, or cutout_A text set | `cutA` |
| Cutout B | cutout_B width and height > 0, or cutout_B text set | `cutB` |

Operations not listed: **suppress** (suppressed).

### Conflicts with old documents — please confirm

- **Cutout A**: Toolpath_Configuration_Matrix.xlsx 'Panel Configurations' activates a cutout only when its width AND height are > 0 (same for Cutout B and for the 'no features' row P1); the old code also treats a non-empty cutout_A / cutout_B text parameter as active (safety net when the model's derived sizes did not update).
- **General**: Toolpath_Configuration_Matrix.xlsx 'Legend & Summary' says panels have 2 setups x 6 toolpaths; the live model also has 'Adaptive1' / 'Adaptive1 (2)', which the old code suppresses as an unknown operation (kept: unknown operations are suppressed).
- **General**: Height and width both 0: the old code gave up and posted every setup; here no row matches, so the component fails with a clear error instead.

<!-- END GENERATED -->

---

## Regenerating this file

Everything between the `BEGIN GENERATED` / `END GENERATED` markers is
generated from `DEFAULT_PROFILES`; a test fails when it is out of date.

```
cd addin\NewFusionExtension
py -3.13 -m nfe.cam_rules ..\..\docs\tables\TOOLPATH_SELECTION.md
```
