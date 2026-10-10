# Validation run, 2026-10-07 and 2026-10-08

All scenarios marked "Drawing required = Yes" in the three validation sheets
were run through the pipeline: 48 doors (`2X8X_Door`), 50 stiles on
`2X81/2_Stile` and 50 stiles on `2X86/8_Stile`. The 2X86/2X88 stiles were run
again on 2026-10-08. The figures below come from the `*-report.json` file of
every order in `output/`.

| Sheet | Scenarios | Drawings produced | Scenarios with a model warning | Failed saves |
|---|---|---|---|---|
| 2X8X Door | 48 | 48 | 18 | 0 |
| 2X81 and 2X82 Stile | 50 | 50 | 25 | 0 |
| 2X86 and 2X88 Stile, 2026-10-07 | 50 | 50 (after retries) | 0 | 3 |
| 2X86 and 2X88 Stile, 2026-10-08 | 50 | 47 | 0 | 1 (3 orders not run) |

A "model warning" is a timeline feature that Fusion marks yellow or red after
the parameters are applied. The drawing is still produced, but the geometry of
that feature may be wrong. The PDFs were not checked by eye.

## 1. 2X81 and 2X82 stiles: `routing_right` loses its profile when gapless

Every scenario with `stile_gapping = 1` (series `2X81G` / `2X82G`) gave the
same warning, and no scenario with `stile_gapping = 0` did: 25 of 25 against
0 of 25. No other input separates the two groups.

> timeline warning at routing_right: Profile 1 missing. 1 Reference Failures.
> The profile reference is lost and this feature is using cached geometry.
> Edit this feature and select new profile references.

* Warned: `2X82-0055` to `2X82-0098` (13) and `2X81-0148` to `2X81-0194` (12).
* Clean: `2X82-0001` to `2X82-0050` (13) and `2X81-0102` to `2X81-0146` (12).
* It is not a state left behind by an earlier order: the run went
  2X82 clean, 2X82G warned, 2X81 clean, 2X81G warned on the same open model.

Meaning: with gapping on, the sketch profile that `routing_right` cuts
changes shape or disappears, the feature can no longer find it, and Fusion
reuses the geometry from the last time it worked. The right-hand routing in
those 25 drawings is therefore suspect. The left-hand feature gave no warning.

Example pair: `2X82-0050` (clean) and `2X82-0055` (warned).

## 2. 2X8X door: sketch `DG Corners&Holes` fails to solve in 18 scenarios

> timeline warning at DG Corners&Holes: ... Failed to solve. Please try
> revising dimensions or constraints. Compute Failed

Fusion lists the dimensions involved. Always the nine fixed 0.25 in
dimensions `d155`-`d162` and `d254`, and in some scenarios one or both of:

* `d255 : if(continuous_hardware; hinge_hole_7_y_value + 0.25 in; 0.25 in)`
* `d257 : if(not(occupancy_indicator_latch); latch_hole_y_value + 0.25 in; 0.25 in)`

| Dimensions listed | Scenarios |
|---|---|
| fixed ones only | 0004, 0015, 0017, 0025, 0031, 0033, 0047 |
| + `d255` | 0012 |
| + `d257` | 0019, 0020, 0022, 0035, 0036, 0040, 0043 |
| + `d255` and `d257` | 0023, 0044, 0045 |

What the inputs show:

* No single input, and no combination of two or three of the on/off inputs,
  separates the 18 warned scenarios from the 30 clean ones. Gapless or not
  makes no difference (13 of 37 gapless, 5 of 11 gapped).
* All seven "+ `d257`" scenarios have `occupancy_indicator_latch = 0`, the
  case in which `d257` follows `latch_hole_y_value`. 7 of the 16 scenarios
  with that setting failed this way.
* The two shortest doors both failed, with both dimensions listed:
  `2X8X-0044` (56.5 in) and `2X8X-0045` (57.5 in). The next shortest
  (60.0625, 66.125 and 70.125 in) were clean.
* `door_swinging_out = 0` fails more often: 10 of 18, against 8 of 30 when 1.
* The values of the previous order on the same open model do not explain it
  either.

So the cause is inside the sketch (probably the position of the latch and
hinge holes relative to the corners) and cannot be named from the inputs
alone. The sketch has to be opened on a failing scenario, for example
`2X8X-0044` or `2X8X-0019`, next to a clean one such as `2X8X-0018`.

## 3. 2X86 and 2X88 stiles: no model warnings, cloud saves refused

Neither run gave a timeline warning. The failures were all
`timed out after 300s waiting for model cloud save`:

* 2026-10-07: `2X86-0074` (twice), `2X86-0076`, `2X88-0171`.
* 2026-10-08: `2X88-0182`; `2X88-0184` and `2X88-0187` were held back.

Cause: Fusion's own reservation of `2X86/8_Stile` in the hub was refused
(`RESERVATION_RESERVE_LINEAGE_ALREADY_RESERVED`), shown as "Upload Error ...
insufficient permissions". It is unrelated to the parameters. Details are in
ARCHITECTURE section 12.5 and OPERATIONS section 9.3-1.

## 4. Other messages

* `model unchanged; no new version saved` on `2X8X-0001`, `2X82-0001` and
  `2X86-0007`: the same scenario was applied twice in a row. Harmless.
