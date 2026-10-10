# NewFusionExtension

A Fusion add-in that watches a dropbox folder for order JSON files. For every
component in an order (D1, S2, P5, … any number of each) it:

1. opens the model chosen for the component's series (e.g. `3X8X_Door`)
2. applies the order's parameters and the tags (component id `1-D1-<order>`,
   order number, series)
3. **saves the model** and waits until the new cloud version is complete
4. computes the Gannomat **drilling** records (doors, stiles)
5. picks the component's **toolpaths** from the toolpath tables (3X8X doors,
   3X82/3X86 stiles, panels; one setup per side, panels one by orientation).
   If there are none it skips straight to the drawing; otherwise it switches
   to MANUFACTURE, generates, posts each setup with the Anderson Stratos post
   and **splices** them into one program `1-<component>-<order>.txt`
6. opens the drawing, presses the equivalent of the drawing's
   "update references" button, and checks the drawing now points at that
   exact new model version
7. exports the drawing to PDF (`1-<component>-<order>-drawing.pdf`)
8. closes the drawing and every other document the pipeline created (verified
   closed, not just "close() returned True")

Per order it creates a run folder `output/<order_id> <Mon D YYYY - HHMM>/`
with the G-code, drawings, the compiled drilling file `<order_id>.json` and
`<order_id>-report.json`. In **remote** mode orders come from
`\\ddc-mefs\iBob-Export\S2S_Export` and the harness copies results to the
Anderson / Gannomat / Fusion Folders shares (Fusion itself never writes to the
network).

A separate **harness** serves a web dashboard and supervises Fusion.

```
NewFusionExtension/
  addin/NewFusionExtension/   the Fusion add-in (junctioned into Fusion's AddIns folder)
    NewFusionExtension.py     stable entry point: custom event + ticker thread
    nfe/                      engine, Fusion adapter, control API, shared code
  harness/server.py           dashboard + supervisor (standard-library Python)
  harness/scenarios.py        validation-scenario sheets -> orders (Scenarios tab)
  harness/static/             dashboard page
  scenarios/                  validation-scenario sheets (CSV)
  drawing_test/<sheet>/       drawing PDFs of the scenario orders, one folder per sheet
  config/config.json          shared configuration (edit from the dashboard)
  dropbox/inbox               drop orders here
  dropbox/processing          order being worked on + checkpoint
  dropbox/done | failed       finished orders + <name>.report.json
  output/<order> <date>/      run folder: .txt G-code, -drawing.pdf, drilling .json, report
  machine_drops/              flat copies for the machines (synced to the shares)
  post/                       Anderson Stratos post processor, machine and tool files
  state/                      status.json + events.jsonl written by the add-in
  logs/                       addin.log, harness.log
  tests/                      unit tests (no Fusion needed) + tests/live (real Fusion)
  scripts/                    installer, dev helpers
```

## Documentation

| Document | Content |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, threads, tick/generator state machine, order lifecycle, checkpoints, rules, parameter formatting, garbage collection, health and supervisor, config reference, file formats, HTTP APIs (8766 and 8765), security |
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | Mermaid diagrams: order lifecycle, per-component steps, one-component sequence, supervisor restart decisions ([workflow.svg](docs/workflow.svg)) |
| [docs/INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md) | Processes, ports, threads, files and external systems; module dependencies ([infrastructure.svg](docs/infrastructure.svg)) |
| [docs/TESTING.md](docs/TESTING.md) | Every unit test, test infrastructure, fixtures, live test driver and live results |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Install, start, dashboard, local/remote mode, rules, orders, reports, states, troubleshooting, production checklist, open decisions |
| [docs/tables/SELECTION_LOGIC.md](docs/tables/SELECTION_LOGIC.md) | How toolpaths, workstops and drilling are chosen, in plain words |
| [docs/tables/TOOLPATH_SELECTION.md](docs/tables/TOOLPATH_SELECTION.md) | The toolpath decision tables (generated from the built-in tables) with review notes |
| [docs/tables/SPLICING.md](docs/tables/SPLICING.md) | How per-setup programs are spliced into one `.txt` |
| [docs/tables/DRILLING.md](docs/tables/DRILLING.md) | Workstops, FlipRotate, air drilling, hole records, compiled file |
| [docs/tables/DIRECTORIES.md](docs/tables/DIRECTORIES.md) | Every directory in local and remote mode, and the sync |

## Setup

1. `powershell -ExecutionPolicy Bypass -File scripts\install_addin.ps1`
   (links the add-in into `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns`).
2. Start Fusion. The add-in loads automatically (`runOnStartup`). The first
   time, or without restarting: Utilities › Add-Ins (Shift+S) › Add-Ins ›
   NewFusionExtension › Run.
3. Start the dashboard: `start_harness.bat`, then open http://127.0.0.1:8765.
   With `api.launch_harness_from_addin` enabled the add-in starts it for you.
4. Series & drawings tab: pick the family folders in the hub, press
   **Suggest rules from hub**, review, **Save changes**.

## How a component finds its model and drawing

Rules live in `config.json` → `rules`, edited on the **Series & drawings** tab.
A rule has a component type (door / stile / panel / any), and either a
**family** (`3X8X` matches series ids whose 1st digit is 3 and 3rd digit is 8,
e.g. `3082G.67P`, `3186`) or a **series pattern** (glob on `series_id`:
`3?82*`, `2?8[12]*`). The most specific enabled rule wins; "Check a series"
on that tab shows which rule a series id would use.

Current rules (from the hub's naming convention):

| Type | Match | Model / drawing |
|---|---|---|
| door | family 2X8X | 2X8X_Door |
| door | family 3X8X | 3X8X_Door |
| panel | family 3X8X | 3X8X_Panel_Standard |
| stile | `2?8[12]*` | 2X81/2_Stile |
| stile | `2?8[68]*` | 2X86/8_Stile |
| stile | `3?81*` / `3?82*` / `3?86*` | 3X81 / 3X82 / 3X86_Stile |

## Parameter handling

* `[value, "type", "description"]` triples and bare values are both accepted.
* Floats get the model parameter's unit (`88.5625 in`); bools become `1`/`0`;
  text parameters are quoted.
* `series_id` selects the model; it is only written if the model has it.
* The tags are filled from the order (`orders.derived_parameters`): order number
  = order id, `component_id` = `1-<component>-<order id>`, series = `series_id`.
* JSON `null` → written as 0 / empty by default (`processing.null_policy`:
  `zero` | `skip` | `fail`). Leaving the old value would leak the previous
  component's value, because models are reused.
* Order parameters the model does not have are listed as warnings
  (`unknown_parameter_policy: fail` turns them into errors).
* Every assignment is read back and compared, so a silently ignored
  assignment fails the component instead of producing a wrong PDF.

## Reliability design

* **Never blocks Fusion.** Work is a generator-based state machine advanced one
  step per tick from a CustomEvent on the UI thread. Fusion processes its own
  events between steps; the add-in never calls `adsk.doEvents()`.
* **Save before drawing.** The drawing is opened only after the model's new
  version exists and `DataFile.isComplete` is true.
* **Garbage collection after every component.** The drawing is closed, then
  every document the pipeline owns (opened by it, hidden reference copies, or
  copies of configured models) is closed. A close counts only when the document
  becomes invalid; failing that it is activated and closed again. Python `gc`
  runs afterwards. Documents that were open before the add-in started, or that
  someone modified by hand, are never touched.
* **Model cache** of 1 by default: consecutive components that share a model
  reuse it (orders are grouped by model), and a model left dirty by a failure
  is discarded, never reused.
* **Periodic recycle** closes everything every 25 components.
* **Health limits** (threads, memory, open documents) are checked at each
  component boundary; crossing one pauses the order and asks the harness to
  restart Fusion.
* **Checkpoints.** The order being processed sits in `dropbox/processing` with
  a `.state.json`; after any restart the order resumes, skipping finished
  components. A component that was in progress during two crashes is failed
  instead of looping.
* **Supervisor** (harness) restarts Fusion when the add-in asks, when its
  heartbeat goes stale, when one step blocks longer than
  `step_stuck_timeout_s`, or after a crash (not after a normal close), with a
  cooldown and at most 6 restarts per hour.

## Dashboard

* **Monitor**: the current component on its route (open model → … → clean up)
  with live step timings, the order's components, queue counts, Fusion
  threads / memory / open documents against their restart limits, recent
  activity, supervisor state; Pause / Resume / Restart Fusion.
* **Orders**: drop or pick JSON files (checked and previewed with the model
  each component will use before going into the inbox), browse inbox /
  processing / done / failed, open reports (parameters before/after, drawing
  reference versions, warnings, step timings, PDF links), run an order again.
* **Series & drawings**: rules table with model/drawing pickers filled from the
  hub, suggestions from the hub, series checker, model parameter viewer, hub
  browser to choose the family folders.
* **Logic tables**: the rules in plain words, the toolpath tables (with a
  tester and editor), splicing, drilling & workstops (with a tester), naming.
* **Settings**: Location (Local / Remote switch, directory reachability, sync
  status), processing, health and supervisor options.
* **Docs**: all documentation and diagrams.
* **Log**: all events from the add-in and harness.

## Tests

```
run_tests.bat                              # 182 unit tests, no Fusion needed
py -3 tests\live\run_order.py <order.json> # drives the real add-in end to end
py -3 scripts\fusion_exec.py -c "result = app.activeDocument.name"   # dev only
```

The unit tests run the whole engine against `FakeOps`, a simulated Fusion that
reproduces async cloud saves, hidden reference copies spawned by drawing
updates, `close()` calls that silently do nothing, stale model versions, and
CAM generation/posting. The toolpath, splicing and drilling modules are
compared exhaustively against ports of the old pipeline's code.
`tests/test_docs.py` fails when the docs fall behind the code (regenerate the
test catalogue with `py -3 scripts\gen_test_catalogue.py`, the SVG diagrams
with `py -3 scripts\gen_diagrams.py`).

## Development switches (config.json → api)

* `dev_exec`: enables `POST /api/dev/exec` (run Python inside Fusion). **Turn
  off on production machines.**
* `POST http://127.0.0.1:8766/api/dev/reload` reloads the add-in code in place.
