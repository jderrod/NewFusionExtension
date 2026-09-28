# Gannomat hinge-hole drilling

What the add-in writes for the Gannomat drill: every **door** and **stile** gets
a list of hole records, and all components of an order are written to **one**
file `{order_id}.json`. This reproduces the old pipeline exactly
(`parameter_exporter.py` `export_drilling_coordinates_paired` +
`order_processor.py`); `tests/test_drilling.py` runs the old code next to
the new code on every input combination and on real old data.

* Code: `addin/NewFusionExtension/nfe/drilling.py`
* Settings: config section `drilling` (defaults = `DRILLING_DEFAULTS`). Changing
  them changes the output; the defaults give the old behaviour.
* Tables 1-7 are **generated from the code and the defaults**. After changing
  the defaults run
  `cd addin/NewFusionExtension && python -m nfe.drilling ../../docs/tables/DRILLING.md`
  (the test suite fails when these tables are out of date).
* Section 8 lists differences between the old code, the old docs, the old
  spreadsheets and old real output. **Please confirm or correct each one.**

Terms: **RH/LH** = door hinging right/left (`*_hinging_right` = 1/0),
**O/S / I/S** = swinging out/in (`*_swinging_out` = 1/0). For a stile, **LD** is
the door on the stile's left side and **RD** the door on its right side.

<!-- BEGIN GENERATED: python -m nfe.drilling docs/tables/DRILLING.md -->

## 1. WorkStop selection

hinging_right / swinging_out come from the order: `door_hinging_right` + `door_swinging_out` for doors; `LD_hinging_right` + `LD_swinging_out` (left side) and `RD_hinging_right` + `RD_swinging_out` (right side) for stiles. Series is normalised first (2nd digit is a generation digit: 3186 -> 3086; only 3xxx codes are recognised). Config: `drilling.workstops` (`<type>_<series>` overrides `<type>`).

| Component | Series | Hinging | Swing | hinging_right vs swinging_out | WorkStop |
|---|---|---|---|---|---|
| door | any series | LH | I/S | same | 1 |
| door | any series | LH | O/S | different | 4 |
| door | any series | RH | I/S | different | 4 |
| door | any series | RH | O/S | same | 1 |
| stile | any series except 3086 | LH | I/S | same | 4 |
| stile | any series except 3086 | LH | O/S | different | 1 |
| stile | any series except 3086 | RH | I/S | different | 1 |
| stile | any series except 3086 | RH | O/S | same | 4 |
| stile | 3086 (e.g. 3186) | LH | I/S | same | 1 |
| stile | 3086 (e.g. 3186) | LH | O/S | different | 4 |
| stile | 3086 (e.g. 3186) | RH | I/S | different | 4 |
| stile | 3086 (e.g. 3186) | RH | O/S | same | 1 |

## 2. Which stile sides are drilled

A side is drilled only when a door is on that side AND the door hinges onto the stile. If neither side is drilled the component gets the fallback record pair (table 4). When the component has no order parameters at all, the old fallback applies: left drilled if `left_interior_drilling` or `left_exterior_drilling` > 0, right likewise.

| Side | Door present | Door hinging | Drilled | Hole params |
|---|---|---|---|---|
| left | `left_side_door`=0 | * | no | LD_stile_* |
| left | `left_side_door`=1 | `LD_hinging_right`=0 (LH) | no | LD_stile_* |
| left | `left_side_door`=1 | `LD_hinging_right`=1 (RH) | yes | LD_stile_* |
| right | `right_side_door`=0 | * | no | RD_stile_* |
| right | `right_side_door`=1 | `RD_hinging_right`=0 (LH) | yes | RD_stile_* |
| right | `right_side_door`=1 | `RD_hinging_right`=1 (RH) | no | RD_stile_* |

## 3. FlipRotate

Doors: always 1. Stiles: from the model's computed flags `left_interior_drilling`, `left_exterior_drilling`, `right_interior_drilling`, `right_exterior_drilling` (non-zero = on). The value only matters for sides that are actually drilled (table 2).

| LI | LE | RI | RE | Left holes FlipRotate | Right holes FlipRotate |
|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | 1 | 1 |
| 0 | 0 | 0 | 1 | 1 | 1 |
| 0 | 0 | 1 | 0 | 1 | 1 |
| 0 | 0 | 1 | 1 | 3 | 4 |
| 0 | 1 | 0 | 0 | 1 | 1 |
| 0 | 1 | 0 | 1 | 1 | 1 |
| 0 | 1 | 1 | 0 | 3 | 4 |
| 0 | 1 | 1 | 1 | 3 | 4 |
| 1 | 0 | 0 | 0 | 1 | 1 |
| 1 | 0 | 0 | 1 | 4 | 3 |
| 1 | 0 | 1 | 0 | 1 | 1 |
| 1 | 0 | 1 | 1 | 4 | 4 |
| 1 | 1 | 0 | 0 | 4 | 3 |
| 1 | 1 | 0 | 1 | 4 | 3 |
| 1 | 1 | 1 | 0 | 4 | 4 |
| 1 | 1 | 1 | 1 | 4 | 4 |

## 4. Air drilling

Air drilling = a dummy record at a safe position so the Gannomat cycles both workstops. Config: `drilling.air_drill`. Air records always have PositionZ null, Function 4, ToolDM 1.

| When | Records added | PositionX | PositionY | DrillName | FlipRotate | Component description |
|---|---|---|---|---|---|---|
| No hinge-hole parameters found (door, or drilled stile side) | 2 records: WorkStop 1 then 4 | 121 | 0.197 | NO DRILLING REQUIRED | 1 | `Paired drilling coordinates (X, Y)` |
| Real holes all on ONE workstop and no record has FlipRotate in [2, 3] | 1 record appended on the other workstop (1<->4) | 121 | 0.197 | NO DRILLING REQUIRED | copied from the first hole | `Paired drilling coordinates (X, Y)` |
| Real holes all on one workstop but FlipRotate 3 present (mixed int/ext stile) | none |  |  |  |  |  |
| Holes on both workstops | none |  |  |  |  |  |
| Stile with no side drilled (table 2), or no `hinge_hole_x_dist` in the model, or any error | 2 records: WorkStop 1 then 4 (fallback) | 121 | 0.197 | NO DRILLING REQUIRED | 1 | `No drilling required` |

## 5. Holes per component

One record per hole parameter found in the model, in MODEL PARAMETER ORDER (user parameters first, then the other model parameters) - not in the order listed here. PositionX = the `_y_value` parameter in inches rounded to 3 dp; for stiles of a series in `drilling.x_offset_by_series` the offset is then added and the result rounded to 3 dp again. PositionY = `hinge_hole_x_dist` in inches rounded to 3 dp (same for every hole of the component). Missing / non-numeric hole parameters are skipped.

| Component | When | PositionX from | PositionY from | X offset |
|---|---|---|---|---|
| door | always | `top_hinge_hole_1_y_value` | `hinge_hole_x_dist` | none |
| door | always | `top_hinge_hole_2_y_value` | `hinge_hole_x_dist` | none |
| door | always | `mid_top_hinge_hole_1_y_value` | `hinge_hole_x_dist` | none |
| door | always | `mid_top_hinge_hole_2_y_value` | `hinge_hole_x_dist` | none |
| door | always | `mid_bottom_hinge_hole_1_y_value` | `hinge_hole_x_dist` | none |
| door | always | `mid_bottom_hinge_hole_2_y_value` | `hinge_hole_x_dist` | none |
| door | always | `bottom_hinge_hole_1_y_value` | `hinge_hole_x_dist` | none |
| door | always | `bottom_hinge_hole_2_y_value` | `hinge_hole_x_dist` | none |
| stile | left side drilled | `LD_stile_top_hinge_hole_1_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | left side drilled | `LD_stile_top_hinge_hole_2_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | left side drilled | `LD_stile_mid_top_hinge_hole_1_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | left side drilled | `LD_stile_mid_top_hinge_hole_2_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | left side drilled | `LD_stile_mid_bottom_hinge_hole_1_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | left side drilled | `LD_stile_mid_bottom_hinge_hole_2_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | left side drilled | `LD_stile_bottom_hinge_hole_1_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | left side drilled | `LD_stile_bottom_hinge_hole_2_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | right side drilled | `RD_stile_top_hinge_hole_1_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | right side drilled | `RD_stile_top_hinge_hole_2_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | right side drilled | `RD_stile_mid_top_hinge_hole_1_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | right side drilled | `RD_stile_mid_top_hinge_hole_2_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | right side drilled | `RD_stile_mid_bottom_hinge_hole_1_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | right side drilled | `RD_stile_mid_bottom_hinge_hole_2_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | right side drilled | `RD_stile_bottom_hinge_hole_1_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |
| stile | right side drilled | `RD_stile_bottom_hinge_hole_2_y_value` | `hinge_hole_x_dist` | 3086: -1.4375 in |

## 6. Record fields (in this key order)

| Field | Type | Value |
|---|---|---|
| ProgramName | string | `1-{component_id}-{order_id}` |
| WorkStop | int | table 1 (air records: table 4) |
| PositionX | number (in) | table 5 (air: 121) |
| PositionY | number (in) | `hinge_hole_x_dist` (air: 0.197) |
| PositionZ | null | always null |
| Function | int | `drilling.function` = 4 |
| ToolDM | int | `drilling.tool_dm` = 1 |
| FlipRotate | int | table 3 |
| SSMA_TimeStamp | null | always null (filled in by the machine side) |
| Fusion_TimeStamp | string | processing time `%Y-%m-%d %H:%M:%S` (local time) |
| DrillName | string | order `program_name` (trimmed) if not blank; else door: `{series_id} {RH\|LH} {O/S\|I/S} DOOR`; stile: `{series_id} {segment}[ \| {segment}]` with one segment per side that HAS a door: `{RH\|LH} {O/S\|I/S} {HINGE\|KEEPING}` (HINGE = drilled side). series_id is the raw order value (not normalised). Air records: `NO DRILLING REQUIRED` |

## 7. Compiled order file

One file per order named `{order_id}.json` (config `naming.drilling`). Components are listed under their type in processing order; types always appear in the order doors, panels, stiles (panels stays empty - panels are never drilled). Each entry keeps only `component_id` + `coordinates`. No file is written when no component produced drilling data. Written with `json.dump(..., indent=2)`.

```json
{
  "IBUS123456": {
    "unit": "in",
    "description": "Compiled paired drilling coordinates for all components organized by type",
    "doors": [
      {
        "component_id": "D1",
        "coordinates": [
          "..."
        ]
      }
    ],
    "panels": [],
    "stiles": [
      {
        "component_id": "S1",
        "coordinates": [
          "..."
        ]
      }
    ]
  }
}
```

<!-- END GENERATED -->

## 8. Differences to confirm

### 8a. Old code vs the old spreadsheet ("Manufacturing Workflow v3 2026_04_21.xlsx", sheet "Gannomat stiles")

| # | Spreadsheet says | Old code (= new code) does | Status |
|---|---|---|---|
| 1 | 3X82, 2 doors hinging, opposite swings, left I/S + right O/S: **WS1 & Flip & WS1** | left RH I/S -> WS1, right LH O/S -> WS1 | matches |
| 2 | 3X82, left O/S + right I/S: **WS4 & Flip & WS4** | both sides WS4 | matches |
| 3 | 3X86, left I/S + right O/S: **WS4 & Flip & WS4**; left O/S + right I/S: **WS1 & Flip & WS1** | 3086 table is the 3082 table inverted -> WS4/WS4 and WS1/WS1 | matches |
| 4 | 2 doors hinging, same swings: **WS1 & Rotate 180 & WS4** | one side WS1, the other WS4 (both series) | matches |
| 5 | 0 or 1 sides hinging: **WS1 & Rotate 180 & WS4** | 1 side: real holes on one workstop + air drill record on the other; 0 sides: air drill on WS1 and WS4 | matches |
| 6 | "Flip" in the opposite-swing cases | The code has no notion of "flip" from the swings: it relies on FlipRotate 3/4, which comes from the MODEL's `*_interior_drilling` / `*_exterior_drilling` flags (table 3). Only when those flags are mixed (interior + exterior) do the records carry 3/4 and the extra air drill record is suppressed. If the model flags are not mixed, an opposite-swing stile gets FlipRotate 1 on both sides AND an air drill record on the other workstop (looks like "rotate"). | **confirm** the model always sets mixed flags for opposite swings |
| 7 | Legend (Toolpath_Configuration_Matrix.xlsx): FlipRotate 3 = exterior face, processed FIRST; 4 = interior face, processed SECOND | Records are NOT ordered by FlipRotate or workstop - they follow the model's parameter order. The machine side must sort. | **confirm** |
| 8 | Flow chart: 3X86 stiles start at **workstop 4** (sticker at bottom) | No sequencing information in the file (see 7). | **confirm** |
| 9 | Flow chart: 3X81 stiles have **no Gannomat step** | A 3X81 stile with a door hinging onto it IS drilled (uses the 3082 workstop table) | **confirm** |
| 10 | Flow chart: 3X86 Gannomat sequence numbered "1, 2" (3X82: "5, 6") | - | looks like a spreadsheet typo |
| 11 | "label abuts the listed workstop", "leveling bar holes always towards middle" | Not represented in the output (handled on the machine / by the model's hole positions) | info |
| 12 | Nothing about the 3086 X offset (-1.4375 in) or the air drill position (X 121, Y 0.197) | Both hard-coded in the old code (now `drilling.x_offset_by_series`, `drilling.air_drill`) | **confirm** values |

### 8b. Old code vs the old doc (`DRILLING_COORDINATES.md`) - the doc is outdated

| # | Old doc says | Old code does |
|---|---|---|
| 1 | One file per component `1-{ComponentID}-{OrderID}-drilling.json` with a `holes` object, plus `{OrderID}-all-drilling.json` with a `components` array | No per-component files; ONE `{order_id}.json` with `doors` / `panels` / `stiles` lists of paired records (table 7) |
| 2 | Stile sides chosen by the 4 model flags `left/right_interior/exterior_drilling`; all 4 = 0 -> no file | Sides chosen from the ORDER (door present + hinging onto the stile, table 2). The flags only set FlipRotate (and choose sides only when a component has no order parameters). No side drilled -> air drill records, never "no file" |
| 3 | Stile hole parameters `LD_top_hinge_hole_1_x_value` ... | `LD_stile_top_hinge_hole_1_y_value` ... (the `LD_top_...` parameters are the door's own holes and are not used) |
| 4 | `hinge_hole_x_dist` renamed to `door_hinge_hole_y_dist` / `stile_hinge_hole_y_dist` | Used as `PositionY` of every record |
| 5 | No WorkStop, FlipRotate, DrillName, air drilling, 3086 offset | All present (tables 1-6) |

### 8c. Old code vs old real output files

| File | Difference to the current (old) code | Status |
|---|---|---|
| `S2S File Test/one-time-drilling-output-compilation/IBUS123456.json` (5 doors, 2026-03-17) | Identical except: `component_id` is `"1-D1-IBUS123456"` (code: `"D1"`); `PositionZ` is `0` (code: `null`); the air drill record keeps the door's DrillName (code: `"NO DRILLING REQUIRED"`). WorkStops, X/Y, record order, FlipRotate, ProgramName, timestamps all match (regression test). | file predates the current code - **confirm** the Gannomat import accepts `null` PositionZ and the short component_id |
| `IBUS1234567.json` (repo root, early mock-up) | ProgramName = hole name, FlipRotate `"rotate"`, no timestamps, DrillName `"3082G.67P RH OS"`, no air drill records, and an **RH O/S door on WorkStop 4** (code: WorkStop 1; LH I/S -> 1 agrees) | mock-up; **confirm** RH O/S door = WS1 |
| `IBUS447889_log.txt` (2026-06-17) | All logged decisions (4 doors, 4 stiles: workstops, DrillNames, air drilling, 8 components compiled) match (regression test) | matches |

### 8d. Quirks of the old code that are reproduced on purpose

1. **Type**: old code decided "stile" by the component id starting with `S`; the new code uses the component's type (same result for normal `D1`/`S1` ids).
2. **Series** normalisation only knows 3xxx codes. `2082`/`2086` (2X8X family) normalise to nothing, so a 2X86 stile would get the 3082 workstop table and **no** X offset. **Confirm** whether 2X86 stiles exist.
3. Any 2nd digit is treated as a generation digit (`3186` -> `3086`, also `3986` -> `3086`).
4. Records come out in model parameter order, not top-to-bottom.
5. The appended air drill record copies the first hole's FlipRotate (can be 4); the "no holes" air pair always uses 1.
6. Only FlipRotate 2 or 3 suppresses the extra air drill record. When the flags are mixed but both drilled sides are interior (e.g. LI=1, RI=1, LE=1 -> FlipRotate 4 and 4) and all holes are on one workstop, an air drill record is still added. FlipRotate 2 is never produced any more.
7. If the model's drilling flags disagree with the order (e.g. only the left side hinges onto the stile but RE=1), a one-sided stile can get FlipRotate 3 and then **no** air drill record, so the machine would not cycle the second workstop.
8. With an empty `series_id` the computed DrillName starts with a space (`" RH O/S DOOR"`); without any order parameters DrillName is empty.
9. `program_name` (if not blank) replaces DrillName on every real hole of the component, both stile sides alike.
10. The 3086 offset is applied to the value already rounded to 3 dp, then rounded again (e.g. 7.0325 -> 7.032 -> 5.595).
11. A stile that needs no drilling gets description `No drilling required`; a component whose model has no hole parameters gets the same two air records but description `Paired drilling coordinates (X, Y)` (descriptions are not written to the compiled file).
12. The old log said "adding air drilling at workstop 1" for the fallback but wrote records for workstops 1 AND 4.
13. Old code took two separate timestamps in one component (could differ by a second); the new code uses one.
14. Model-flag fallback (component without order parameters) drills a side when a flag is `> 0`, while FlipRotate treats any non-zero flag as on.
