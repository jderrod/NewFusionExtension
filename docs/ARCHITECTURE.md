# NewFusionExtension architecture

This document describes how NewFusionExtension (NFE) works, based on the code
in this repository. Paths are relative to the project root. Function and class
names are given as `module.Class.method`.

Related documents:

* [WORKFLOW.md](WORKFLOW.md): order and component flow diagrams
* [INFRASTRUCTURE.md](INFRASTRUCTURE.md): processes, ports, files, module dependencies
* [TESTING.md](TESTING.md): unit and live tests
* [OPERATIONS.md](OPERATIONS.md): operator guide
* [tables/SELECTION_LOGIC.md](tables/SELECTION_LOGIC.md): how toolpaths, workstops and drilling are chosen (plain words)
* [tables/TOOLPATH_SELECTION.md](tables/TOOLPATH_SELECTION.md), [tables/SPLICING.md](tables/SPLICING.md), [tables/DRILLING.md](tables/DRILLING.md), [tables/DIRECTORIES.md](tables/DIRECTORIES.md): the logic tables

---

## 1. Overview

NFE turns order JSON files into machine files and PDF drawings using
parametric Fusion models. For every component in an order it opens the model
chosen by a series rule, writes the order's parameters into the model, saves a
new cloud version, computes the Gannomat drilling records, selects the
component's toolpaths from the toolpath tables (if any: switches to
MANUFACTURE, generates, posts one program per setup with the Anderson post and
splices them into one `.txt`), opens the drawing, updates the drawing's
reference to the saved version, exports the drawing to PDF and closes every
document it created. Per order it writes a named run folder with all files, a
compiled drilling file and a report; the harness copies results to the machine
shares.

There are two programs:

| Program | Runs in | Entry point | Purpose |
|---|---|---|---|
| Add-in | Fusion process (`Fusion360.exe`) | `addin/NewFusionExtension/NewFusionExtension.py` | Watches the dropbox, drives Fusion, writes status/events, serves the control API on 127.0.0.1:8766 |
| Harness | Separate Python process | `harness/server.py` | Serves the dashboard on 127.0.0.1:8765, supervises and restarts Fusion, manages dropbox and config, proxies hub browsing to the add-in |

They share the project folder: `config/config.json` (both read it; the harness
writes it), `dropbox/`, `output/`, `state/` (the add-in writes, the harness
reads) and `logs/`.

Design rules visible throughout the code:

1. **Never block Fusion for long and never pump events.** All Fusion work runs
   on Fusion's UI thread in small steps driven by a CustomEvent. The add-in
   never calls `adsk.doEvents()` (`nfe/engine.py` module docstring).
2. **Save before drawing.** The drawing is opened only after the model's new
   cloud version exists and `DataFile.isComplete` is true.
3. **Trust only `isValid` when closing.** `Document.close()` can return `True`
   and do nothing, so a close counts only when the document becomes invalid
   (`Engine._close_verified`).
4. **Survive crashes.** The order being processed lives in
   `dropbox/processing/` with a checkpoint; after any restart it resumes.
5. **Fusion never writes to the network.** Everything is written locally; the
   harness sync service copies results to the shares (section 18).
6. **Decisions are data.** Toolpath selection, splicing, drilling and naming
   are pure-Python tables/functions shared by the add-in (to run them) and the
   dashboard (to show and test them), section 7A.

---

## 2. Components and responsibilities

### Add-in (`addin/NewFusionExtension/`)

| File | Responsibility |
|---|---|
| `NewFusionExtension.py` | Stable entry point loaded by Fusion (`run`/`stop`). `Host` registers the CustomEvent `NewFusionExtension_tick`, runs the `nfe-ticker` thread, forwards ticks to `nfe.addin_main.AddinApp`, and performs in-place code reload. Writes `logs/addin_crash.log` on unhandled exceptions. |
| `NewFusionExtension.manifest` | Fusion add-in manifest, `runOnStartup: true`, version 0.1.0. |
| `nfe_home.txt` | Project root pointer written by `scripts/install_addin.ps1`; used by `nfe.paths.find_project_root` if the add-in folder was copied instead of junctioned. |
| `nfe/__init__.py` | `VERSION = "0.1.0"`. |
| `nfe/addin_main.py` | `AddinApp`: wires config, paths, logging, `StateStore`, `FusionOps`, `Engine`, `ApiServer`; runs the `nfe-heartbeat` and `nfe-inbox` threads; implements `/api/dev/exec` (`_dev_exec`) and optional harness auto-launch (`_maybe_launch_harness`). |
| `nfe/engine.py` | `Engine`: the cooperative state machine (`Task`, `Sleep`, `WaitFor`), order and component workflow (incl. `_drilling`, `_manufacture`, `_finalize_outputs`), model cache with stale version guard, garbage collection, checkpoints, health checks, readiness gate, lookup retry, cross-thread request queue (`submit`). |
| `nfe/ops_base.py` | `Ops`: the interface between the engine and Fusion. `OpsError`. |
| `nfe/fusion_ops.py` | `FusionOps(Ops)`: implementation on the real Fusion API (documents, parameters, computed values, saves, drawing references, PDF export, MANUFACTURE workspace, CAM suppression/generation/posting, hub browsing, model inspection). |
| `nfe/cam_rules.py` | Toolpath tables engine: `validate_profile`, `collect_values`, `plan`, `resolve_actions` (tolerant setup/operation name matching), `explain`, `render_markdown`. |
| `nfe/cam_defaults.py` | `DEFAULT_PROFILES`: built-in toolpath tables for 3X8X doors, 3X82 stiles, 3X86 stiles and 3X8X panels (with review notes where old documents disagree). |
| `nfe/expr.py` | Safe expression language for table conditions (`compile_expr`, `evaluate`): and/or/not, comparisons, arithmetic, numbers, strings, names only. |
| `nfe/gcode.py` | Splicing per-setup posts into one program (`order_sections`, `splice`, `build_program`, `write_program`), `ProgramCounter` (persistent program numbers). |
| `nfe/drilling.py` | Gannomat drilling records per door/stile (`component_drilling`: holes, workstops, FlipRotate, air drilling) and the compiled order file (`compile_order`, `write_compiled`); `DRILLING_DEFAULTS`. |
| `nfe/naming.py` | Output naming (`Namer`, `NAMING_DEFAULTS`, `run_stamp`, `unique_dir`, `safe_filename`). |
| `nfe/sync.py` | `SyncService` (run by the harness): copies machine drops, output and finished orders to the shares / network_mirror; `check_paths`, `build_pairs`. |
| `nfe/fake_ops.py` | `FakeOps(Ops)`: in-memory simulated Fusion for tests (async saves, hidden reference copies, closes that do nothing, readiness delays, lookup failures). |
| `nfe/orders.py` | Order JSON parsing: `parse_order`, `load_order_file`, `unwrap`, `Component`, `Order`, `safe_name`. |
| `nfe/params.py` | Converting order values into Fusion expressions: `format_expression`, `plan_assignments`, `ModelParam`. |
| `nfe/rules.py` | Series rules: `resolve`, `rule_matches`, `family_of`, `family_from_order_id`, `explain` (dashboard preview), `suggest_rules` (rules from hub folder listings). |
| `nfe/config.py` | `DEFAULTS`, `load` (deep merge over defaults), `save`, `validate`, `ensure_exists`. |
| `nfe/dropbox.py` | `Dropbox`: inbox listing, claim (rename, or copy + delete across drives), finish, reject, requeue, checkpoint and report paths. |
| `nfe/state.py` | `StateStore`: `state/status.json` snapshot and `state/events.jsonl` event log; `read_events_file` for the harness. |
| `nfe/jsonio.py` | `write_json_atomic` (temp file + `os.replace` with retry), `read_json`. |
| `nfe/winproc.py` | ctypes Win32 helpers: process list, thread/memory/handle counts, terminate, wait for exit, `find_fusion_launcher`, `launch_detached`. |
| `nfe/api_server.py` | `ApiServer`: localhost control API on port 8766. |
| `nfe/paths.py` | `Paths`: project folder layout, `apply_location` (inbox for local/remote mode); `find_project_root`. |

Everything in `nfe/` except `fusion_ops.py` and `addin_main.py` is plain Python
without `adsk` imports, so it can be imported by the harness and by the unit
tests.

### Harness (`harness/`)

| File | Responsibility |
|---|---|
| `harness/server.py` | `Harness` class (supervisor, restart worker, dropbox and config operations, add-in proxy), `make_handler`/`route` (HTTP), `main` (CLI: `--port`, `--host`, `--no-supervisor`). Standard library only; imports the shared `nfe` modules from `addin/NewFusionExtension`. |
| `harness/scenarios.py` | Validation-scenario sheets (`scenarios/*.csv`) → order JSON: `parse_sheet` (finds the header row, types the parameter columns), `select_rows` (all drawing-required rows, or numbers / ranges / ids), `build_order` (one component per order, `order_id` `<Series>-<number>`, `series_id` `<Series>` + `G` when gapless, `drawing_test` = sheet title). |
| `harness/static/index.html`, `app.js`, `app.css` | The dashboard ("NFE Console"): Monitor, Orders, Scenarios, Series & drawings, Logic tables, Settings (incl. Location), Log, Docs tabs. No build step. |

### Other folders

| Path | Content |
|---|---|
| `config/config.json` | Shared configuration (section 13). |
| `dropbox/inbox`, `processing`, `done`, `failed` | Order queue (section 6). |
| `output/<order_id> <Mon D YYYY - HHMM>/` | One run folder per order: `1-<comp>-<order>.txt`, `1-<comp>-<order>-drawing.pdf`, `<order>.json` (drilling), `<order>-report.json`. |
| `machine_drops/anderson`, `machine_drops/gannomat` | Flat staging copies for the machines (synced to the shares). |
| `network_mirror/` | Local-mode stand-in for the shares. |
| `post/` | `Anderson Stratos 2.cps` (post processor), `Anderson Stratos.mch`, `F360MT - PartitionDoor.tools`. |
| `config/toolpath_profiles.json` | Edited toolpath tables (absent = built-in defaults). |
| `docs/tables/` | Logic tables and the plain-words selection rules. |
| `state/status.json`, `state/events.jsonl` | Live status and event log written by the add-in. |
| `logs/` | `addin.log`, `addin_crash.log`, `harness.log`. |
| `scripts/install_addin.ps1` | Junctions the add-in into Fusion's AddIns folder. |
| `scripts/fusion_exec.py` | Development client for `/api/dev/exec`. |
| `scripts/gen_test_catalogue.py` | Regenerates the test catalogue in `docs/TESTING.md`. |
| `scripts/gen_diagrams.py` | Draws `docs/workflow.svg` and `docs/infrastructure.svg` from data (`--check` reports stale files). |
| `start_harness.bat`, `run_tests.bat` | Start the dashboard; run the unit tests. |
| `tests/` | Unit tests, helpers, fixtures, `tests/live/run_order.py`. |

---

## 3. Processes and threads

| Process | Thread | Created by | What it does | May call the Fusion API |
|---|---|---|---|---|
| Fusion | UI thread | Fusion | Runs `_TickHandler.notify` → `Host.on_tick` → `AddinApp.tick` → `Engine.tick`. All engine work and all `FusionOps` calls happen here. | Yes (only this thread) |
| Fusion | `nfe-ticker` | `Host.start` | Every `TICK_INTERVAL_S` = 0.2 s: if `AddinApp.wants_tick()` (= `Engine.has_work()`) or a reload is pending, and no event is outstanding, calls `app.fireCustomEvent`. Re-fires after `REFIRE_AFTER_S` = 30 s if an event seems lost. | Only `fireCustomEvent` |
| Fusion | `nfe-heartbeat` | `AddinApp.start` | Every `HEARTBEAT_S` = 2 s: writes `heartbeat_at` and `ui_blocked_s` to status; every third beat (6 s) also writes threads / working set / handles via `winproc.own_process_info`. File I/O and Win32 only. | No |
| Fusion | `nfe-inbox` | `AddinApp.start` | Every 2 s: lists the inbox (`inbox_files(raise_errors=True)`) and sets the engine's inbox hint and `inbox_reachable` / `inbox_scan_s` / `inbox_error`. Separate from the heartbeat because listing an unreachable network inbox can hang ~20 s. | No |
| Fusion | `nfe-api` + one daemon thread per request | `ApiServer.start` | Serves port 8766. Anything that needs Fusion is queued with `Engine.submit` and executed on the UI thread. | No (marshalled) |
| Harness | main thread | `harness/server.py main` | `ThreadingHTTPServer.serve_forever` on port 8765. | n/a |
| Harness | `supervisor` | `Harness.start_supervisor` | Every `SAMPLE_S` = 5 s: `sample_once` (Fusion process metrics, history) then `check_policies` (restart decisions). Not started with `--no-supervisor`. | n/a |
| Harness | `restart` | `Harness.restart_fusion` | One-off worker: terminate Fusion, launch it, wait for the add-in. | n/a |
| Harness | sync | `Harness.start_sync` → `SyncService.start` | Every `location.sync.interval_s` (15 s): copies settled files to the shares or `network_mirror/` (section 18). | n/a |
| Harness | one daemon thread per request | `ThreadingHTTPServer` | Dashboard API. Calls to the add-in use `urllib` with per-endpoint timeouts. | n/a |
| Browser | — | user | Polls `/api/overview` and `/api/events` every 2 s, re-renders the current-component view every 1 s. | n/a |

Thread-safety points:

* `StateStore` guards status and events with one `threading.RLock`, because the
  UI thread and the heartbeat thread both write.
* `Engine.tick` takes `_tick_lock` non-blockingly; a re-entrant call returns
  immediately (should not happen because nothing pumps events).
* `Engine.submit(fn, timeout)` puts `(fn, box)` on a `queue.Queue` and waits on
  a `threading.Event`. `Engine._serve_requests` runs up to 5 queued functions at
  the start of every tick, on the UI thread. A caller that times out gets
  `TimeoutError`, which the API maps to HTTP 503.

---

## 4. The tick loop

```
nfe-ticker thread                      Fusion UI thread
-----------------                      ----------------
every 0.2 s:
  want = reload_requested or impl.wants_tick()
  if want and (not pending or fired > 30 s ago):
      pending = True
      app.fireCustomEvent(EVENT_ID)  ─────►  _TickHandler.notify
                                               Host.on_tick:
                                                 pending = False
                                                 reload? → stop old impl, reload nfe.*, start new
                                                 else AddinApp.tick() → Engine.tick()
```

At most one event is outstanding, so Fusion's event queue never floods. When
there is nothing to do (`Engine.has_work()` is false) no events are fired at
all.

`Engine.has_work()` returns true when a cross-thread request is queued, when a
task is running, or (if not paused and no restart is requested) when the inbox
hint is set or `dropbox/processing/` contains an order.

`Engine.tick()`:

1. `_serve_requests()` (up to 5 queued `submit` calls).
2. If a task exists: `task.advance()`; when it finishes, `_finish_task()`.
3. Otherwise, if not paused and no restart is requested: `_start_next_order()`.

`Engine.tick_started_at` holds the wall time while a tick runs. The heartbeat
thread publishes `ui_blocked_s = now - tick_started_at`; the harness uses it to
detect a step that blocks the UI thread too long (section 12).

### In-place reload

`POST /api/dev/reload` → `AddinApp._request_reload` sets
`Host.reload_requested`. On the next tick `Host.on_tick` calls `AddinApp.stop()`
on the old instance (engine shutdown, API stop, heartbeat stop), deletes every
`nfe` / `nfe.*` module from `sys.modules`, imports `nfe.addin_main` again and
starts a new `AddinApp`. The CustomEvent and ticker thread in
`NewFusionExtension.py` stay in place. An order in progress stays in
`dropbox/processing/` and resumes from its checkpoint.

---

## 5. Cooperative state machine: `Task`, `Sleep`, `WaitFor`

Work is written as Python generators. A `Task` (`nfe/engine.py`) owns one
generator and resumes it at most once per `advance()` call, which happens once
per tick. What the generator yields tells the task when to resume:

| Yielded value | Meaning | Resumed with |
|---|---|---|
| `None` | Continue on the next tick. Used to give Fusion one event-loop turn (for example after `apply_params`, "let Fusion finish the recompute"). | `None` |
| `Sleep(seconds)` | Resume after `seconds` of engine clock (`time.monotonic` in production). Earlier ticks return without resuming. | `None` |
| `WaitFor(cond, timeout, what, raise_on_timeout=True)` | Call `cond()` on every tick. When it returns a truthy value, resume. | The truthy value |
| (timeout reached, `raise_on_timeout=True`) | `StepTimeout("timed out after Ns waiting for <what>")` is thrown into the generator. | exception |
| (timeout reached, `raise_on_timeout=False`) | Resume with `False`. | `False` |
| (`cond()` raises) | The exception is thrown into the generator at the `yield`. | exception |
| any other value | Treated as `None`. | `None` |

When the generator returns, `Task.done` is true and `Task.result` holds the
return value; an uncaught exception sets `Task.error` and `Task.error_tb`.
`Task.close()` closes the generator (raising `GeneratorExit` inside it); this
is what `Engine.shutdown` does when the add-in stops.

Sub-steps compose with `yield from`, e.g. the order generator
`_order_gen` → `_run_component` → `_component_steps` → `_get_model` →
`_close_verified`.

Each `advance()` does one short piece of Fusion work. Some Fusion calls block by
nature (`documents.open`, `exportManager.execute`, `doc.save` starting the
upload); they run inside one tick, and the time is visible as `ui_blocked_s`.
Waiting for asynchronous results (cloud save, drawing reference update, PDF
file on disk) never blocks: it is a `WaitFor` polled once per tick while Fusion
keeps processing its own events.

`Engine._wait(...)` is a thin wrapper that also publishes the wait reason as
`component.waiting_for` in `status.json` before returning the `WaitFor`.

---

## 6. Order lifecycle through the dropbox

Folder layout (`nfe/paths.py`, overridable with `paths.dropbox` and
`paths.output` in config):

| Folder | Content |
|---|---|
| `dropbox/inbox/` | New orders (`*.json`). |
| `dropbox/processing/` | The order being worked on and its checkpoint `<stem>.state.json`. |
| `dropbox/done/` | Orders whose components all succeeded, plus `<stem>.report.json`. |
| `dropbox/failed/` | Orders with at least one failed/cancelled component, or rejected orders, plus `<stem>.report.json`. |
| `output/<order_id> <run stamp>/` | The order's run folder (section 7A). |

Order files are names ending in `.json` that do not start with `.` or `~` and
do not end with `.state.json` or `.report.json` (`dropbox._is_order_file`).

### Steps

1. **Detect.** The `nfe-inbox` thread calls `Dropbox.inbox_files()` every 2 s and
   sets the engine's inbox hint, which makes the ticker fire. `inbox_files`
   ignores files modified less than `SETTLE_SECONDS` = 1.0 s ago (still being
   written) and returns the rest oldest first (by mtime).
2. **Start.** `Engine._start_next_order` first looks in `processing/`: an order
   there is a resumed order and is taken first (sorted by name). Otherwise it
   claims the oldest inbox file with `Dropbox.claim`, which `os.replace`s it
   into `processing/` (a name collision gets a `_YYYYmmdd_HHMMSS[_n]` suffix via
   `_unique`). From a network inbox (other drive) it copies to a `.claiming`
   file, deletes the original and renames; if the original cannot be deleted the
   copy is dropped, so an order is never processed twice. A claim that fails (file locked or gone) is retried on a later
   tick. A new `Task("order", _order_gen(path))` is created.
3. **Parse.** `_order_gen` reloads config (`self.cfg = self.load_config()`), so
   dashboard edits apply from the next order on, and parses the file with
   `load_order_file`. On `OrderError` or `OSError` the order is **rejected**:
   `Dropbox.reject` moves it to `failed/` and writes a report
   `{"summary": {"status": "rejected", "error": ...}, "error": ...}`.
4. **Plan.** `_plan` resolves a rule for every component (section 9). With
   `orders.group_by_model` (default true) components are reordered so that all
   components using the same model run back to back, in order of first
   appearance, keeping the original order within a group. Components without a
   rule are grouped individually.
5. **Process components.** For each planned component (section 7), with a
   checkpoint write before and after it (section 8).
6. **Finalize.** Every component result (or a `skipped` placeholder) goes into
   the report. Status: `ok` if nothing failed, `failed` if nothing succeeded,
   otherwise `partial`. `_finalize_outputs` writes the compiled drilling file
   `<order_id>.json` (when any door/stile produced records) and copies the
   G-code and drilling files to `machine_drops/`; the report is written to
   `<run folder>/<order_id>-report.json` (with an `outputs` section);
   `Dropbox.finish` moves the order to `done/` (status `ok`) or `failed/`
   (`partial`/`failed`), writes `<stem>.report.json` next to it and deletes the
   checkpoint. `status.json` gets `last_order`.

Other transitions:

* **Requeue** (dashboard "Run again", `POST /api/orders/requeue`): moves a file
  from `done/` or `failed/` back to `inbox/` and deletes its report.
* **Remove** (`POST /api/orders/remove`): deletes a file from `inbox/`.
* **Upload** (`POST /api/orders/upload`): validates with `parse_order` and
  writes the order into `inbox/` via a `.uploading` temp file and `os.replace`.
* **Internal error** in the order task (an exception not caught per component)
  pauses the engine with reason `internal error: ...`; the order stays in
  `processing/` for inspection and resumes on Resume.

Output names come from `naming` (section 7A.4). Every run gets its own
folder, so re-running an order never overwrites an earlier run; a resumed
order reuses the folder recorded in its checkpoint (`output_folder`).

---

## 7. Per-component workflow

`Engine._run_component` runs `_component_steps` and then, whatever happened,
`_after_component` (cleanup). Each step is recorded by `_step(res, name)` with
start time and duration, and published as `status.component`.

| # | Step name | What happens (code) | Waits / timeouts (config key, default) | Fails the component when |
|---|---|---|---|---|
| 0 | — | `rule is None` check. | — | `no series rule matches type=... series_id=... - add one in Series Configuration` |
| 1 | `open_model` | `_get_model`: reuse a cached model if it is alive and not modified; otherwise close the stale one, evict least-recently-used models until there is room, `_resolve` the cloud file (fresh `findFileById(...).latestVersion` every time, no cache), reuse an open visible up-to-date unmodified copy of a document that was not open before the add-in started (`find_open`) or `open_document(visible=open_visible)`, then `Sleep(0.5)`. **Stale version guard:** if the opened version is older than the version the engine last saved for this model (`_saved_versions`), close it, wait `STALE_BACKOFF_S` × attempt (5 s), look up and reopen, up to `STALE_REOPENS` = 4 times. | `_resolve` retries lookups (6 attempts, 10 s apart) | `rule has no model selected`, `cloud file not found or not accessible`, `documents.open() returned nothing`, `the hub keeps returning <model> vN but vM was saved` |
| 2 | `apply_parameters` | `read_params`; `_apply_input_transforms` (`orders.input_transforms`, default: panel `cutout` "B-354 & B-386" → text `cutout_A`/`cutout_B`); add derived parameters (`orders.derived_parameters`: the order number, component id and series tags) not present in the order; warn for every `orders.tag_parameters` group the model lacks; `plan_assignments` (section 10); `apply_params` (one `design.modifyParameters` batch, per-parameter fallback, read-back verification); `yield None`; append `model_warnings` (timeline errors/warnings, max 20). | one tick | bad values, unknown parameters under `unknown_parameter_policy: fail`, any assignment whose read-back does not match |
| 3 | `save_model` (if `processing.save_model`, default true) | Refuse to save a copy older than the last saved version (`refusing to save an out-of-date copy`). `start_save(model, "NFE <order_id> <component_id>")`. Returns `None` when the document is not modified (warning `model unchanged; no new version saved`). Then wait for `save_complete`: document not modified, `dataFile.versionNumber` greater than before, `dataFile.isComplete`. Records `model_version`. | `WaitFor` "model cloud save", `save_timeout_s` = 300 | `save() returned False`, timeout |
| 3a | `drilling` (doors, stiles) | `read_values` (recomputed values, lengths in inches) once for 3a/3b; `_drilling`: `drilling.component_drilling` with `DRILLING_DEFAULTS` + `drilling` config; every record's `ProgramName` set to the component's file stem; stored as `drilling`. | — | — |
| 3b | `plan_toolpaths` | `_manufacture`: pick the toolpath table (`rules.resolve` over the profiles), `collect_values`, `plan` → setups (row id, note). No table → `toolpaths.result = "no toolpath table for this component"`; no setups → `"no toolpaths needed"`: steps 3c–3f are skipped (MANUFACTURE is never entered). | — | invalid tables, `no row of toolpath table ... matches` |
| 3c | `manufacture_workspace` | `activate(model)`, `activate_workspace("cam")` (if `cam.switch_workspace`), `cam_state`, `resolve_actions` (exact then normalized name matching), `cam_prepare`: unsuppress the chosen setups, set each operation's suppression. | — | `model ... has no manufacturing setups`, `setup(s) not found in the model: ...` |
| 3d | `generate_toolpaths` | `cam_generate` on the active operations of the chosen setups; wait for the future; `cam_problems` (errors fail, warnings are recorded). | `WaitFor` "toolpath generation", `cam.generate_timeout_s` = 300 | `toolpaths not generated: ...`, timeout |
| 3e | `post_process` | One `CAM.postProcess` per setup with `cam.post_processor` into `<run folder>/_nc/<component>/<program>.nc`; program numbers from `gcode.ProgramCounter` (`state/nc_program_counter.txt`, seeded from the old counter). `yield` between posts. | — | `post processor not found`, `post processing ... produced no file` |
| 3f | `splice_gcode` | `gcode.order_sections` (G57 before G59, stable), `gcode.build_program` (M0 + FLIP / ROTATE blocks, N renumbering, CRLF; numbered `M30` kept unless `cam.strip_numbered_m30`), `write_program` to `naming.gcode`; `_nc` removed unless `cam.keep_nc_sections`; back to DESIGN. Records `gcode`, `gcode_bytes`, `toolpaths.splice`. | — | — |
| 4 | `open_drawing` | Rule without drawing: fail (`missing_drawing_policy: fail`, default) or warn and finish the component without a PDF. Otherwise `_resolve` the drawing, reuse an open copy or open it, `Sleep(drawing_settle_s)`. | `drawing_settle_s` = 2.0 | `no drawing configured for rule ...` |
| 5 | `update_drawing` | `activate`; `update_drawing(drawing, model)`: checks the drawing references the model's lineage, presses `updateAllReferences()` only if a reference is out of date or `drawing.isUpToDate` is false, then `getLatestVersion()` on references still out of date. Stores `drawing_references` (`before`, `after`, `updated`). Waits for `drawing_up_to_date`: the model reference is not out of date, its version is at least the model document's version, and the drawing is up to date (nudging `getLatestVersion` while not). Then `Sleep(drawing_settle_s)`. | `WaitFor` "drawing to reference the new model version", `drawing_update_timeout_s` = 180 | `drawing ... does not reference model ...`, timeout |
| 6 | `export_pdf` | Name from `naming.drawing_pdf`. Delete an old `_partial_<name>.pdf`; `activate`; `export_pdf(drawing, partial)` (all sheets, `openPDF=False`, `useLineWeights=True`); wait until the file is at least 64 bytes and starts with `%PDF-`; `os.replace` to the final name; record `pdf` (relative to output) and `pdf_bytes`. | `WaitFor` "PDF file", `export_timeout_s` = 300 | `... is not a drawing document`, `PDF export returned False`, timeout |
| 7 | `save_drawing` (if `processing.save_drawing`, default false) | `start_save(drawing, ...)` and wait for `save_complete`. | `WaitFor` "drawing cloud save", `save_timeout_s` | timeout |
| 8 | `cleanup` | `_after_component` (section 11): back to the DESIGN workspace if still in MANUFACTURE; verified close of the drawing; discard the model if the component failed or the model is dirty or dead; close uncached models; periodic full recycle; `_collect_garbage`; `gc.collect()`; health sample stored as `health_after`. | 5 s per close attempt | never fails the component; problems become warnings `cleanup problem: ...` |

CAM always leaves the model modified, so after a manufacture step the model
is closed without saving (the next component reopens the clean saved version;
toolpath data never goes into cloud versions).

Exceptions in steps 0-7 set `status: "failed"` and `error`. `ComponentError`
and `StepTimeout` are expected failures; any other exception also stores a
`traceback` in the component result.

After the component, `_order_gen` writes the checkpoint, updates counters and
calls `_check_health` (section 12).

---

## 7A. Manufacture, drilling and naming

The rules in plain words are in
[tables/SELECTION_LOGIC.md](tables/SELECTION_LOGIC.md); the exact tables are on
the dashboard's Logic tables tab and in `docs/tables/`.

### 7A.1 Toolpath tables (`nfe/cam_rules.py`, `nfe/cam_defaults.py`)

A *profile* matches components like a series rule (`type`, `family`,
`series`) and holds:

* `variables`: name → model/order parameter (`source` `model`, `order` or
  `auto` = model first), with `default` and optional `type`;
* `derived`: expressions over the variables;
* `sequence_rules`: ordered rows `{id, when, setups, note, review}`; the first
  row whose `when` expression is true gives the setups to post, in order
  (`[]` = no toolpaths);
* `operation_rules`: `{op, active}` for operations inside the chosen setups;
  `unknown_operations` (`leave`, `suppress`, `activate`) for the rest;
* `splice.g57_before_g59`.

Expressions use `nfe/expr.py` (safe AST whitelist). Tables come from
`config/toolpath_profiles.json` (edited on the dashboard, validated before
saving) or the built-in `DEFAULT_PROFILES`; they are loaded at the start of
every order (`Engine._load_profiles`). Invalid tables fail the components that
use them. Built-in tables: `door-3x8x` (17 rows), `stile-3x82` (`3?82*`),
`stile-3x86` (`3?86*`), `panel-3x8x`. 3X81 and 2X8X components have no table.

### 7A.2 Splicing (`nfe/gcode.py`)

See [tables/SPLICING.md](tables/SPLICING.md): one post per setup; stable
G57-before-G59 order; first section keeps its header; later sections are
preceded by `M0`, blank, `(******************)`, `(NEW SETUP, ROTATE PART
180)` when the workstop changes or `FLIP PART` otherwise, and lose their
header and footer; `M30` and `%` at the end; N renumbered from 10 in steps of
5; CRLF.

### 7A.3 Drilling (`nfe/drilling.py`)

See [tables/DRILLING.md](tables/DRILLING.md): hole records (`ProgramName`,
`WorkStop`, `PositionX`, `PositionY`, `PositionZ`, `Function`, `ToolDM`,
`FlipRotate`, `SSMA_TimeStamp`, `Fusion_TimeStamp`, `DrillName`) per door and
stile; compiled per order into `{order_id: {unit, description, doors, panels,
stiles}}`; byte-compatible with the old pipeline.

### 7A.4 Naming (`nfe/naming.py`)

| Output | Default template | Example |
|---|---|---|
| Run folder | `{order_id} {run_stamp}` | `3X8X_DV_0622 Sep 28 2026 - 0539` (`(2)` suffix if it exists) |
| G-code | `1-{component_id}-{order_id}.txt` | `1-D1-3X8X_DV_0622.txt` |
| Drawing | `1-{component_id}-{order_id}-drawing.pdf` | `1-D1-3X8X_DV_0622-drawing.pdf` |
| Drilling | `{order_id}.json` | `3X8X_DV_0622.json` |
| Report | `{order_id}-report.json` | `3X8X_DV_0622-report.json` |

Placeholders: `{order_id}`, `{component_id}`, `{type}`, `{series_id}`,
`{source}`, `{run_stamp}`, `{date}`, `{time}`. Names are made Windows-safe
(`safe_filename`); `naming.validate` rejects templates without
`{component_id}` for per-component files or with the wrong extension.

---

## 8. Checkpoints, resume and crash-loop protection

The checkpoint `dropbox/processing/<stem>.state.json` is written with
`write_json_atomic`:

* before a component starts: `results[id] = {"status": "in_progress", "attempts": n}`
* after it ends: the full component result (with `attempts`)
* when a component is given up or cancelled

At the start of `_order_gen` the checkpoint is loaded and components with
status `done`, `failed` or `cancelled` are skipped. `started_at` is kept from
the checkpoint, so the report's duration covers the whole order across
restarts.

`attempts = previous attempts + 1`. If the previous status is `in_progress`
(the process died during that component) and `attempts > MAX_ATTEMPTS`
(`MAX_ATTEMPTS = 2`), the component is failed without running it:
`Fusion stopped during this component 2 times; giving up`. Example:

| Run | Checkpoint before | Result |
|---|---|---|
| 1 | — | attempts 1, Fusion crashes mid-component |
| 2 | `in_progress`, attempts 1 | attempts 2 (≤ 2), runs again, Fusion crashes again |
| 3 | `in_progress`, attempts 2 | attempts 3 > 2 → failed "giving up", order continues with the next component |

This prevents an order that reliably crashes Fusion from producing an endless
restart loop (the harness cap of 6 restarts per hour is the second line of
defence).

### Component boundaries

At every component boundary `_order_gen`:

1. sleeps in 1 s steps while `paused` or `restart_requested` is set (unless a
   cancel is pending);
2. runs the readiness gate `_wait_until_ready` (section 12.4);
3. if a cancel was requested (`Engine.cancel_order`), marks the component
   `cancelled` with error `order cancelled` (the remaining components are
   cancelled the same way, then the order is finalized);
4. applies the crash-loop check above.

Pause, cancel and restart requests therefore never interrupt a component in
the middle; the current component finishes first.

---

## 9. Rule resolution

A rule (`config.json` → `rules`, format documented in `nfe/rules.py`):

```json
{"id": "3x82-stile", "enabled": true, "type": "stile",
 "family": "3X8X", "series": "3?82*",
 "model":   {"id": "urn:adsk.wipprod:dm.lineage:...", "name": "3X82_Stile", "folder_id": "...", "path": "..."},
 "drawing": {"id": "urn:adsk.wipprod:dm.lineage:...", "name": "3X82_Stile Drawing", "folder_id": "...", "path": "..."}}
```

`rules.rule_matches(rule, ctype, series_id, fallback_family)`:

1. Disabled rules (`enabled: false`) never match.
2. `type` must equal the component type or be `*`.
3. If `series` is non-blank it is a glob (`fnmatch.fnmatchcase`, both sides
   upper-cased, so case-insensitive) matched against `series_id`. `?` = one
   character, `*` = anything, `[12]` = one of the listed characters. An empty
   `series_id` never matches a series rule. Specificity = `100 + number of
   literal characters` (characters other than `* ? [ ]`).
4. If `series` is blank, `family` is used: the component's family is
   `family_of(series_id)` — the first run of four digits `d1 d2 d3 d4` gives
   `"<d1>X<d3>X"` (`3082G.67P` → `3X8X`, `S2186` → `2X8X`). If the series id
   has no four-digit run, the fallback family from the order id is used
   (`family_from_order_id`: an order id starting with e.g. `3X8X_`). Specificity
   = 50. A rule with neither `series` nor `family` never matches.
5. Score = `(1 if type is exact else 0, specificity)`.

`rules.resolve` returns the rule with the highest score; ties go to the rule
that appears first. Consequences:

* An exact-type rule always beats a `*` rule, whatever the pattern.
* For the same type, any series pattern (≥ 100) beats a family rule (50), and a
  longer literal pattern beats a shorter one (`3?82*` = 103 beats `3*` = 101).

Examples with the current `config/config.json` rules:

| Component | series_id | Family | Winning rule |
|---|---|---|---|
| door | `3082G.67P` | 3X8X | `3x8x-door` (family) |
| stile | `3082G.67P` | 3X8X | `3x82-stile` (`3?82*`) |
| stile | `3081.67` | 3X8X | `3x81-stile` (`3?81*`) |
| stile | `2182G` | 2X8X | `2x81-2-stile` (`2?8[12]*`) |
| panel | `3082G.67P` | 3X8X | `3x8x-panel-standard` |
| panel | `2082` | 2X8X | none → component fails |

`rules.explain` resolves every component of an order for the dashboard preview.
The dashboard's "Check a series" box re-implements the same scoring in
JavaScript (`resolveRule` in `harness/static/app.js`), without the order-id
fallback family.

### Rule suggestions

`rules.suggest_rules(files, existing)` (dashboard "Suggest rules from hub",
`POST /api/config/suggest`) scans the files of the configured family folders.
A model (`.f3d`) whose name matches `^(\d)X8([0-9X]+(?:/[0-9]+)*)_(door|stile|panel)`
(case-insensitive) and is not already used by a rule becomes a rule:

| Model name | type | family | series pattern |
|---|---|---|---|
| `3X8X_Door` | door | 3X8X | (blank → family rule) |
| `3X82_Stile` | stile | 3X8X | `3?82*` |
| `2X81/2_Stile` | stile | 2X8X | `2?8[12]*` |
| `3X8X_Panel_Standard` | panel | 3X8X | (blank) |

The drawing is the `.f2d` named `<model name> Drawing` in the same scan, if
present; the rule id is the lower-cased model name with non-alphanumerics
replaced by `-`.

---

## 10. Orders and parameter formatting

### Order parsing (`nfe/orders.py`)

* The file must be a JSON object, at most `MAX_ORDER_BYTES` = 5 MiB, read as
  UTF-8 with optional BOM.
* `order_id`: the value (or first element of a typed triple); if missing or
  empty, the file name without extension.
* Every top-level key (except `order_id`) whose value is a non-empty list of
  objects, at least one of which has `parameters`, is a component list. Each
  item needs an `id` (unique across the order) and a `parameters` object.
* Component type: `orders.type_by_key[key]` (default `doors`→door,
  `stiles`→stile, `panels`→panel); else by the id's leading letters via
  `orders.type_by_prefix` (`D`, `S`, `P`); else the key without a trailing `s`.
* Parameter values are either typed triples `[value, "type", "description"]`
  (2 or 3 elements, type in `string/str/text`, `float/double/number/real`,
  `int/integer`, `bool/boolean`) or bare values (type inferred). Nested lists
  or objects are rejected.
* `Component.series_id` is the `series_id` parameter as a string (empty when
  missing or null).
* `Component.index` is the position in document order; it is used to keep the
  original order inside a model group.

### Mapping to model parameters (`params.plan_assignments`)

* Order parameters are matched by exact name to the model's parameters
  (`FusionOps._param_map`: user parameters first, then `design.allParameters`
  for names not already present).
* `series_id` is skipped when the model has no such parameter (it is used for
  rule selection).
* Derived parameter names (`orders.derived_parameters`) are silently skipped
  when the model lacks them, and not written when their value comes out empty
  (an order without `series_id` leaves the series tag alone).
* Tags (`orders.tag_parameters`): every model is expected to have a component
  id, an order number and a series text parameter.  A model with none of the
  names in a group gets the warning `model X has no tag parameter a / b`.
  The tags that are written go through the same read-back check as every
  other assignment.
* Other order parameters the model does not have are "unknown": a warning
  `model X has no parameter(s): ...` by default, or a component failure with
  `processing.unknown_parameter_policy: fail`.
* Values that cannot be formatted are collected as errors and fail the
  component (`bad parameter values: ...`).

### Formatting rules (`params.format_expression`)

| Model parameter | Order value | Expression written |
|---|---|---|
| text | `null`, policy `zero` | `''` |
| numeric | `null`, policy `zero` | `0 <unit>` (or `0` when unitless) |
| any | `null`, policy `skip` | not written (model value kept) |
| any | `null`, policy `fail` | error `value is null` |
| text | `true` / `false` | `'true'` / `'false'` |
| text | number | `'<number>'` (formatted as below) |
| text | string | `'<string>'`; a string containing `'` is an error |
| numeric | bool, or declared type `bool` | `1` / `0` with the parameter's unit appended if it has one; strings `true/yes/on/1` and `false/no/off/0` accepted; anything else is an error |
| numeric | int | `<int> <unit>` |
| numeric | float | `%.10f` with trailing zeros removed (`0.1+0.2` → `0.3`), `-0` → `0`, then ` <unit>`; NaN/infinity is an error |
| numeric | numeric string (`"12.5"`) | as a float |
| numeric | `"true"` / `"false"` | `1` / `0` (+ unit) |
| numeric | other non-empty string | passed through unchanged as a Fusion expression (e.g. `"96 in"`) |
| numeric | empty string | error |

Units are always appended so Fusion never falls back to the document's default
units (e.g. `88.5625 in`).

Why the default null policy is `zero`: models are reused between components
(model cache), so leaving the previous value would leak the previous
component's value into this component. A `null value(s) written as 0/empty`
warning lists the affected parameters.

### Applying (`FusionOps.apply_params`)

1. Build one `ValueInput.createByString(expression)` per assignment and call
   `design.modifyParameters(targets, values)` once (one recompute).
2. If that returns `False` or raises, set each parameter's `expression` one at
   a time to find the failing ones.
3. Read back every assignment: text parameters compare `textValue` with the
   expression stripped of quotes; numeric parameters compare `p.value` with
   `unitsManager.evaluateExpression(expression, p.unit)` within a relative
   tolerance of 1e-9. A mismatch sets `ok: false` and
   `value did not change to X (now Y)`.

Each result is `{name, ok, expression, before, after, message}` and is stored in
the component's `parameters` list in the report. Text-parameter detection uses
`Parameter.valueType == adsk.fusion.ParameterValueTypes.TextParameterValueType`
with a fallback to `unit == "Text"` (`FusionOps._is_text`).

---

## 11. Documents, model cache and garbage collection

### Which documents the pipeline owns

`FusionOps` tracks:

* `_foreign`: every document open when the add-in started
  (`begin_session`). These are never closed by garbage collection.
* `_ours`: documents the add-in opened or adopted via `find_open`.
* `_file_cache`: lineage id → `DataFile` (refreshed to `latestVersion` on
  every `resolve_file`).

`FusionOps.managed_documents(keep)` returns the open documents the garbage
collector may close: not in `keep`, not foreign, and at least one of

* opened/adopted by the pipeline (`_ours`),
* hidden (`isVisible == False`: the hidden reference copies Fusion creates
  when a drawing's references are updated),
* a copy of a model or drawing referenced by any configured rule.

A visible document that is modified and is neither ours nor hidden is skipped
("somebody's unsaved work: leave it alone").

`find_open` only returns visible documents whose version is the file's latest
version; a stale copy is ignored (and later closed by garbage collection).

### Verified close (`Engine._close_verified`)

1. If the document is already invalid, done.
2. `close_document(doc)` (`doc.close(False)`, no save), then
   `WaitFor(not doc_alive, 5 s, raise_on_timeout=False)`.
3. If it is still valid: `activate(doc)`, one tick, close again, wait 5 s.
4. If it is still valid: event `Could not close <label> <name>`, return
   `False`.

`doc_alive` is `doc.isValid`. The return value of `close()` is ignored, because
it can report success without closing (observed on hidden drawing-reference
copies in the previous project).

### Model cache

`Engine._model_cache` is an `OrderedDict` of model lineage id → document, used
as an LRU with capacity `processing.model_cache_size` (default 1). Combined
with grouping by model, an order with 10 doors opens the door model once. A
cached model is reused only if it is still valid and not modified. With
`model_cache_size: 0` models go into `_opened_models` and are closed after each
component.

### Cleanup after every component (`Engine._after_component`)

1. Close the drawing (verified).
2. If the component failed, or the model is dead or modified (for example a
   save that timed out), remove the model from the cache and close it
   (verified, without saving). A model in an unknown state is never reused.
3. Close every model in `_opened_models`.
4. Every `processing.recycle_every_components` components (default 25; 0 =
   never) run a **full** collection that also closes the cached models.
5. `_collect_garbage(full)`: close up to 20 `managed_documents(keep=cached
   models)` per pass, verified; `gc.collect()`; log
   `Garbage collection: closed N, M not closable, documents A -> B` when
   anything was closed, not closable, or the pass was full.
6. `_sample_health()`: `ops.process_health()` (threads, working set MB,
   handles, open document count) is published as `status.health` and stored
   in the component result as `health_after`.

The dashboard button "Close leftover documents" (`POST /api/control
{"action": "gc"}`) runs `Engine.request_garbage_collection`, a full collection
as its own task; it is refused while an order is running.

`Engine.shutdown` (add-in stop or reload) abandons the task and closes the
cached and opened models without verification.

---

## 12. Health, supervision and readiness

### 12.1 Add-in health limits (`Engine._check_health`)

After every component (after the checkpoint) the last health sample is
compared with the `health` config:

| Condition | Default |
|---|---|
| `threads > health.max_threads` | 4000 |
| `working_set_mb > health.max_working_set_mb` | 12000 |
| `documents > health.max_open_documents` | 12 |
| `components_done + components_failed >= health.restart_every_components` (0 = off) | 200 |

If any holds, the engine sets `restart_requested`, `restart_reason`
(e.g. `scheduled refresh after 200 components`), logs
`Fusion restart requested at component boundary: ...` and publishes state
`waiting_restart`. The order then waits at the next component boundary. The
harness sees `restart_requested` in `status.json` and restarts Fusion; after
the restart the order resumes from its checkpoint. Resume (dashboard) clears
the request without a restart.

The scheduled refresh exists because Fusion grows by itself: in the 22-component
soak test Fusion gained about 6 threads, 110 handles and 25 MB per component
while the open document count stayed at 1, and a full document flush did not
release it.

### 12.2 Heartbeat and UI-blocked time

`AddinApp._heartbeat_loop` writes `heartbeat_at` every 2 s and `ui_blocked_s`
(seconds the current tick has been running, 0 when idle). The thread does not
touch the Fusion API, so it keeps beating while the UI thread is inside a long
Fusion call.

### 12.3 Harness supervisor (`harness/server.py`)

`Harness.sample_once` (every 5 s) finds `Fusion360.exe` with `winproc.find_pids`,
reads threads, working set, handles and start time, reads `state/status.json`,
and appends a history point `{t, threads, mb, handles, docs, state}` (up to
`HISTORY_POINTS` = 1440 points, 2 hours). It remembers
`_seen_pid_with_addin` when the status file's `addin_pid` equals the Fusion pid
and the heartbeat is younger than 30 s.

`Harness.check_policies` decides whether to restart:

| Check (in order) | Result |
|---|---|
| `supervisor.auto_restart_fusion` is false, or a restart is in progress | no action |
| less than `supervisor.restart_cooldown_s` (300) since the last restart ended | no action |
| Fusion running, add-in pid matches, `restart_requested` | restart: `add-in requested restart: <reason>` |
| Fusion running, pid matches, state not `stopped`, heartbeat older than `supervisor.heartbeat_stale_s` (60) | restart: `add-in heartbeat stale for Ns (Fusion hung)` |
| Fusion running, pid matches, `ui_blocked_s > processing.step_stuck_timeout_s` (900) | restart: `Fusion UI thread blocked for Ns in step <step>` |
| Fusion not running, the harness had seen it running with the add-in, and state is not `stopped` | restart: `Fusion is not running (crashed while add-in state was ...)` |
| Fusion not running after a normal close (`AddinApp.stop` wrote state `stopped`) | no action |
| any restart reason, but `MAX_RESTARTS_PER_HOUR` = 6 restarts already in the last hour | no restart; one `Restart limit reached` error event |

`Harness.restart_fusion` records `{at, reason, result}` and starts
`_restart_worker`:

1. terminate every `Fusion360.exe` (`TerminateProcess`), wait up to 60 s for
   each to exit;
2. sleep `post_kill_sleep_s` (3 s);
3. launch Fusion: `supervisor.fusion_launcher` or
   `winproc.find_fusion_launcher()`;
4. poll `GET /api/ping` on the add-in every 5 s for up to `addin_wait_s`
   (600 s). Result `ok` (with `took_s`) or
   `add-in did not come back within 600s`, or `failed: <error>`;
5. clear `restart_in_progress` and set `last_restart_at` (the cooldown counts
   from the end of the restart).

A manual restart from the dashboard (`restart_fusion` action) uses the same
worker but skips the policy checks (cooldown and hourly cap); it is still
recorded and counts toward the cap for automatic restarts.

`winproc.find_fusion_launcher` looks in
`%LOCALAPPDATA%\Autodesk\webdeploy\production\*\` for a `FusionLauncher.exe`
that has a `FusionLauncher.exe.ini` next to it and takes the newest. Fusion's
build folders also contain a `FusionLauncher.exe` without the `.ini`; starting
that one shows "Configuration file ... is missing or incomplete".

### 12.4 Readiness gate and lookup retry

Right after Fusion starts it is not yet signed in, and cloud lookups fail.

`Engine._wait_until_ready` runs at every component boundary. It calls
`ops.ready()` every 3 s until it returns `(True, "")`. `FusionOps.ready`
checks, in order:

| Check | Reason reported |
|---|---|
| `app.isStartupComplete` | `Fusion is still starting up` |
| not `app.isOffLine` | `Fusion is offline` |
| `app.data.activeHub` exists | `not signed in to a hub yet` |
| active hub name equals `hub.name`, or a hub of that name is in `app.data.dataHubs` | `hub <name> not visible yet` |

While waiting, state is `waiting_fusion` and `fusion_wait_reason` holds the
reason; a `Waiting for Fusion: ...` event is logged once, and
`Fusion is ready; continuing` when it clears.

`Engine._resolve(ref)` wraps `ops.resolve_file` with up to 6 attempts, 10 s
apart, logging `Cloud lookup of X failed (...); retrying`; the last failure
fails the component.

---

### 12.5 Cloud reservations and blocking dialogs

Fusion reserves a file in the hub when it becomes modified and releases the
reservation after the new version is uploaded. Seen four times on 2026-10-07
and 2026-10-08, every time on `2X86/8_Stile` (after 17, 28, 48 and 47 saves of
that model in a row; never in 50 saves of `2X81/2_Stile` or 48 of
`2X8X_Door`): Fusion sent a second reserve request just before the upload
(`reserveLineage_reserveFileBeforeUpload` in Fusion's `AppLogFile*.log`), the
server answered `DM-403.RESERVATION_RESERVE_LINEAGE_ALREADY_RESERVED`, Fusion
aborted the upload (`[CFSUploader] Uploading aborted since file couldn't be
reserved`) and showed a modal **Upload Error ... insufficient permissions**
dialog. Why Fusion sends the second request is not known. The reservation is
held by the signed-in user's own account and stays on the server for about two
hours: restarting Fusion or closing the document does not release it, and
every save of that file fails the same way until it expires. Other files are
not affected.

What the pipeline does about it:

* `processing.model_settle_s` (default 20): before changing a model, the
  engine waits until that many seconds have passed since the same model's
  previous save completed. It was added on the theory that a late push
  notification caused the failure; the 2026-10-08 failure happened with the
  wait in place, so it does not prevent it.
* The harness watches the Fusion process for a modal dialog
  (`winproc.modal_dialog`: the main window is disabled and another window is
  enabled) every sample. The title is shown on the Fusion lamp and logged.
  Dialogs listed in `supervisor.dialog_actions` are answered (at most three
  times each): **Upload Error** gets a click on **Open** (never **Save As**,
  which would create a copy of the model), the **Recovered Documents** dialog
  (window title `Fusion360`) that appears after a restart with unsaved changes
  gets its X button. Nothing is touched before the add-in has loaded in that
  Fusion process, so sign-in and licence dialogs are left alone.
* The component whose upload was refused still fails on `save_timeout_s`.
  Its model cannot be saved until the reservation is gone: hold that model's
  orders back and continue with the others.

## 13. Configuration reference

`config/config.json` is deep-merged over `nfe.config.DEFAULTS`
(`config.load`), so missing keys take their defaults. The add-in re-reads it
at the start of every order and on `reload_config`. The harness writes it
through `PUT /api/config`, which deep-merges over the defaults, validates
(`config.validate`) and writes atomically.

### `hub`

| Key | Default | Meaning |
|---|---|---|
| `hub.name` | `"Nikorohmz"` | Hub that must be visible before cloud work (`FusionOps.ready`). |
| `hub.project` | `""` | Informational; the project holding the family folders. |
| `hub.family_folders` | `{"2X8X": "", "3X8X": ""}` | Folder per family, either a folder id string or `{"id", "name"}` (the dashboard writes the object form). Used by "Suggest rules from hub" and the rule model/drawing pickers. |

### `rules`

| Key | Default | Meaning |
|---|---|---|
| `rules` | `[]` | List of series rules (section 9). Validation: unique non-empty `id`; `type` in `door`, `stile`, `panel`, `*`; a `series` or `family`; enabled rules need `model.id`. |

### `orders`

| Key | Default | Meaning |
|---|---|---|
| `orders.type_by_key` | `{"doors": "door", "stiles": "stile", "panels": "panel"}` | Top-level JSON key → component type. |
| `orders.type_by_prefix` | `{"D": "door", "S": "stile", "P": "panel"}` | Component id letter prefix → type, for other keys. |
| `orders.group_by_model` | `true` | Reorder components so those sharing a model run back to back. |
| `orders.derived_parameters` | `a_order_number` and `order_number` = `{order_id}`; `component_id` = `1-{component_id}-{order_id}`; `a_series` and `series` = `{series_id}` | Parameters filled from the order when the component does not supply them and the model has them (the 2X stile models say `order_number` / `series`, the others `a_order_number` / `a_series`). Placeholders `{order_id}`, `{component_id}`, `{type}`, `{series_id}`. Written as text; an empty value is not written. |
| `orders.tag_parameters` | `[["component_id"], ["a_order_number", "order_number"], ["a_series", "series"]]` | Tag parameters every model should have. A component whose model has none of the names in a group gets a warning. |
| `orders.input_transforms` | panel `split_text`: `cutout` → `cutout_A`, `cutout_B` on `&` or `+` | Order values reshaped before they are applied; the targets are always written (empty when absent) so a previous panel's cutout never lingers. |

### `naming`

| Key | Default | Meaning |
|---|---|---|
| `naming.order_folder` | `"{order_id} {run_stamp}"` | Run folder under `output/`. |
| `naming.gcode` | `"1-{component_id}-{order_id}.txt"` | Must contain `{component_id}` and end in `.txt`. |
| `naming.drawing_pdf` | `"1-{component_id}-{order_id}-drawing.pdf"` | Must contain `{component_id}` and end in `.pdf`. |
| `naming.drilling` | `"{order_id}.json"` | Compiled drilling file. |
| `naming.report` | `"{order_id}-report.json"` | Order report in the run folder. |

### `cam`

| Key | Default | Meaning |
|---|---|---|
| `cam.enabled` | `true` | Run the manufacture step at all. |
| `cam.post_processor` | `"post/Anderson Stratos 2.cps"` | Post processor (relative to the project root). |
| `cam.program_counter_file` | `"state/nc_program_counter.txt"` | Persistent program number counter. |
| `cam.program_counter_start` | `1001` | First program number. |
| `cam.seed_counter_from` | old pipeline counter file | Continue from the higher number so numbers never repeat. |
| `cam.profiles_file` | `"config/toolpath_profiles.json"` | Edited toolpath tables (absent = built-in). |
| `cam.generate_timeout_s` | `300` | Toolpath generation timeout. |
| `cam.switch_workspace` | `true` | Activate MANUFACTURE for CAM and DESIGN afterwards. |
| `cam.keep_nc_sections` | `false` | Keep the per-setup `.nc` files in `_nc/`. |
| `cam.strip_numbered_m30` | `false` | Remove each section's numbered `N### M30` (old behaviour keeps it; to be confirmed with the Stratos control). |

### `drilling`

Merged over `nfe.drilling.DRILLING_DEFAULTS`: `enabled` (true), `types`
(`["door", "stile"]`), `air_drill` (`{x: 121, y: 0.197, name: "NO DRILLING
REQUIRED"}`), `function` (4), `tool_dm` (1), `x_offset_by_series`
(`{"3086": -1.4375}`), `workstops` (door same→1/different→4, stile 4/1,
stile_3086 1/4), `y_param` (`hinge_hole_x_dist`).

### `drops`

| Key | Default | Meaning |
|---|---|---|
| `drops.gcode` | `"machine_drops/anderson"` | Flat copy of every G-code file (relative to the root; `""` = off). |
| `drops.drilling` | `"machine_drops/gannomat"` | Flat copy of the compiled drilling file. |
| `drops.drawings` | `""` | Flat copy of PDFs (off). |

### `location`

| Key | Default | Meaning |
|---|---|---|
| `location.mode` | `"local"` | `local` or `remote` (section 18). |
| `location.local.inbox` | `""` | Blank = `dropbox/inbox`. |
| `location.local.mirror_root` | `"network_mirror"` | Where the sync copies in local mode. |
| `location.remote.inbox` | `\\ddc-mefs\iBob-Export\S2S_Export` | Order inbox in remote mode. |
| `location.remote.shares.gcode` | `\\ddc-mefs\Anderson` | G-code share. |
| `location.remote.shares.drilling` | `\\ddc-mefs\Gannomat\F360_output` | Drilling share. |
| `location.remote.shares.output` | `\\ddc-mefs\Fusion\Fusion Folders\output` | Output tree. |
| `location.remote.shares.order_completed` / `order_failed` | `...\Fusion Folders\order_completed` / `order_failed` | Finished orders. |
| `location.sync.enabled`, `interval_s`, `settle_s` | `true`, `15`, `5` | Sync service. |

### `processing`

| Key | Default | Meaning |
|---|---|---|
| `processing.autostart` | `true` | Start processing when the add-in loads; `false` starts paused (`autostart disabled`). |
| `processing.save_model` | `true` | Save the model (new cloud version) before the drawing step. |
| `processing.save_drawing` | `false` | Also save the updated drawing to the hub after export. |
| `processing.open_visible` | `true` | Open models and drawings as visible documents. |
| `processing.model_cache_size` | `1` | Models kept open between components (integer ≥ 0; 0 = close after every component). |
| `processing.recycle_every_components` | `25` | Full document flush (including cached models) every N components; 0 = never. |
| `processing.unknown_parameter_policy` | `"warn"` | `warn` or `fail` for order parameters the model does not have. |
| `processing.null_policy` | `"zero"` | JSON `null`: `zero` (write 0 / `''`), `skip` (keep model value), `fail` (component fails). |
| `processing.missing_drawing_policy` | `"fail"` | Rule without a drawing: `fail` the component or `warn` and skip PDF export. |
| `processing.open_timeout_s` | `600` | Validated as a positive number but not used by the engine (document opens are synchronous calls). |
| `processing.save_timeout_s` | `300` | Timeout for model and drawing cloud saves. |
| `processing.drawing_update_timeout_s` | `180` | Timeout for the drawing to reference the new model version. |
| `processing.drawing_settle_s` | `2.0` | Sleep after opening the drawing and after its references are up to date. |
| `processing.model_settle_s` | `20.0` | Minimum time between a model's save completing and the next change to the same model (section 12.5). `0` = no wait. |
| `processing.export_timeout_s` | `300` | Timeout for the PDF file to appear. |
| `processing.step_stuck_timeout_s` | `900` | Harness restarts Fusion when one tick (`ui_blocked_s`) blocks longer than this. |

### `health`

| Key | Default | Meaning |
|---|---|---|
| `health.max_threads` | `4000` | Fusion thread count that triggers a restart request. |
| `health.max_working_set_mb` | `12000` | Fusion working set (MB) that triggers a restart request. |
| `health.max_open_documents` | `12` | Open document count that triggers a restart request. |
| `health.restart_every_components` | `200` | Scheduled restart request after this many components in one add-in session; 0 = never. |

### `supervisor`

| Key | Default | Meaning |
|---|---|---|
| `supervisor.auto_restart_fusion` | `true` | Allow the harness to restart Fusion automatically. |
| `supervisor.heartbeat_stale_s` | `60` | Heartbeat age treated as a hang; also the dashboard's "add-in online" threshold. |
| `supervisor.restart_cooldown_s` | `300` | Minimum time between automatic restarts (from the end of the previous one). |
| `supervisor.dismiss_dialogs` | `true` | Answer the Fusion dialogs listed in `dialog_actions` (section 12.5). |
| `supervisor.dialog_actions` | `Upload Error` → click at 0.912, 0.8125 (the Open button); `Fusion360` → close | One entry per dialog window title: `{"title", "action": "click" | "close", "x", "y"}` with x, y as fractions of the dialog's width and height. |
| `supervisor.fusion_launcher` | `""` | Path to `FusionLauncher.exe`; blank = auto-detect. |

### `api`

| Key | Default | Meaning |
|---|---|---|
| `api.addin_port` | `8766` | Add-in control API port (always bound to 127.0.0.1). |
| `api.harness_port` | `8765` | Harness dashboard port. |
| `api.harness_bind` | `"127.0.0.1"` | Harness bind address. |
| `api.dev_exec` | `false` | Enables `POST /api/dev/exec` (arbitrary Python inside Fusion). |
| `api.launch_harness_from_addin` | `true` | When the add-in starts and nothing listens on the harness port, start `harness/server.py` detached with `pyw -3` / `pythonw`. |
| `api.harness_python` | `""` | Interpreter for the auto-launched harness; blank = `pyw`/`pythonw` from PATH. |

### `paths`

| Key | Default | Meaning |
|---|---|---|
| `paths.dropbox` | `""` | Dropbox root; blank = `<project>/dropbox`. |
| `paths.output` | `""` | Output root; blank = `<project>/output`. |

The project root itself comes from the `NFE_HOME` environment variable, else
two levels above the add-in folder if it has a `config/` folder, else
`nfe_home.txt` (`paths.find_project_root`).

---

## 14. File formats

### 14.1 Order JSON (input)

An optional top-level `"drawing_test": "<folder>"` (written by the Scenarios
tab) makes the add-in also copy the order's drawing PDFs into
`drawing_test/<folder>/` under the project root; the report lists the copies
under `outputs.drawing_test`.

```json
{
  "order_id": ["3X8X_DV_0622", "string", "identifier for the order"],
  "doors": [
    {"id": ["D1", "string", "door ID"],
     "parameters": {
       "series_id":        ["3082G.67P", "string", "series ID of the component"],
       "component_height": [88.5625, "float", "height of the component in inches"],
       "door_hinging_right": [true, "bool", "..."]
     }}
  ],
  "stiles": [ ... ],
  "panels": [ ... ]
}
```

Bare values (`"component_height": 88.5625`) and any number of components per
key are accepted. See section 10.

### 14.2 Order report (`<run folder>/<order_id>-report.json` and `dropbox/<done|failed>/<stem>.report.json`)

Both files have identical content:

```json
{
  "order_id": "3X8X_DV_0622", "source_file": "example.json",
  "started_at": 1770000000.0, "finished_at": 1770000030.0, "duration_s": 30.0,
  "summary": {"status": "ok|partial|failed", "total": 1, "done": 1, "failed": 0,
              "warnings": 0, "order_id": "3X8X_DV_0622", "output_dir": "3X8X_DV_0622"},
  "components": [ <component result>, ... ]
}
```

Component result (`Engine._blank_result` plus fields added while running):

| Field | Meaning |
|---|---|
| `id`, `type`, `series_id` | From the order. |
| `rule_id`, `model`, `drawing` | Chosen rule and model/drawing names. |
| `status` | `done`, `failed`, `cancelled`, or `skipped` (never reached). |
| `error` | Failure message, or null. |
| `traceback` | Only for unexpected exceptions. |
| `warnings` | List of strings (unknown parameters, nulls, timeline warnings, unchanged model, cleanup problems). |
| `parameters` | `apply_params` results `{name, ok, expression, before, after, message}`. |
| `unknown_parameters` | Order parameters the model does not have. |
| `model_version` | Model `dataFile.versionNumber` after the save. |
| `drawing_references` | `{before: [...], after: [...], updated: bool}`; each reference `{name, id, version, file_version, latest, out_of_date}`. |
| `pdf`, `pdf_bytes` | PDF path relative to `output/` (forward slashes) and size. |
| `gcode`, `gcode_bytes` | G-code path relative to `output/` and size (only when toolpaths were posted). |
| `toolpaths` | `{profile, row, note, setups, missing_variables, inputs, sequence, changes, unknown_operations, result, splice: [{setup, workstop, transition_before}]}`. |
| `drilling` | The component's drilling records `{component_id, order_id, unit, description, coordinates, summary}`. |
| `steps` | `[{name, started_at, duration_s}]`. |
| `started_at`, `finished_at`, `duration_s` | Timing (epoch seconds). |
| `attempts` | Number of times this component was started (section 8). |
| `health_after` | Health sample taken during cleanup `{threads, working_set_mb, handles, documents, at}`. |

The order report also has `outputs`: `{folder, drilling, drilling_components, dropped}`.

A rejected order's report is only
`{"summary": {"status": "rejected", "error": "<reason>"}, "error": "<reason>"}`.

### 14.3 Checkpoint (`dropbox/processing/<stem>.state.json`)

```json
{"order_id": "IBUS447891", "started_at": 1770000000.0,
 "output_folder": "IBUS447891 Sep 28 2026 - 0611",
 "components": {
   "P1": { <full component result>, "attempts": 1 },
   "P2": {"status": "in_progress", "attempts": 1}
 }}
```

Deleted by `Dropbox.finish` / `Dropbox.reject`.

### 14.4 Status snapshot (`state/status.json`)

Written atomically by `StateStore.update` on every change (heartbeat
included). Fields:

| Field | Written by | Meaning |
|---|---|---|
| `state` | engine | `idle`, `processing`, `paused`, `waiting_restart`, `waiting_fusion`, `stopped` |
| `paused`, `pause_reason` | engine | Pause flag and reason. While paused, `state` is `paused` even if an order is open (it continues after the current component finishes and then holds at the boundary). |
| `restart_requested`, `restart_reason` | engine | Health restart request. |
| `counters` | engine | `orders_done`, `orders_failed`, `components_done`, `components_failed` for this add-in session. |
| `cached_models` | engine | Size of the model cache. |
| `order` | engine | Current order: `name`, `order_id`, `started_at`, `total`, `done`, `failed`, `current`, `components` `[{id, type, status}]`; `null` when idle. |
| `component` | engine | Current component: `id`, `type`, `step`, `step_started_at`, `started_at`, `series_id`, `model`, `drawing`, `waiting_for`, `steps` `[{name, duration_s}]`. |
| `last_order` | engine | Last finished order: `name`, `order_id`, `status`, `done`, `total`, `finished_at`, `folder`. |
| `health` | engine + heartbeat | `threads`, `working_set_mb`, `handles`, `documents`, `at`. |
| `fusion_wait_reason` | engine | Last readiness-gate reason. |
| `addin_version`, `addin_pid`, `addin_started_at`, `project_root` | `AddinApp.start` | Identity of the running add-in. |
| `heartbeat_at`, `ui_blocked_s` | heartbeat thread | Liveness. |
| `inbox_reachable`, `inbox_scan_s`, `inbox_error` | inbox thread | Whether the (possibly network) inbox could be listed and how long it took. |
| `written_at` | `StateStore` | Time of the write. |

`GET /api/status` on the add-in returns the same snapshot plus `has_work` and
`api_port`.

### 14.5 Event log (`state/events.jsonl`)

One JSON object per line:

```json
{"seq": 1234, "ts": 1770000000.0, "level": "debug|info|warn|error", "msg": "text", "data": {...}}
```

`seq` continues across add-in restarts (`StateStore._last_seq_on_disk` reads
the tail of the file). When the file exceeds 5 MiB it is renamed to
`events.jsonl.1` (replacing an older one). The add-in keeps the last 500
events in memory for `GET /api/events`. `AddinApp` also mirrors every event
into `logs/addin.log` (rotating, 5 MiB × 3). Harness events live in memory
(500) with their own `seq` and `"src": "harness"`, and in `logs/harness.log`.

---

## 15. HTTP API reference

All responses are JSON with `Cache-Control: no-store` unless noted. Errors are
`{"error": "message"}` (500 responses also carry `traceback`).

### 15.1 Add-in control API (`nfe/api_server.py`, `127.0.0.1:8766`)

Bound to `127.0.0.1` only (hard-coded); the port is `api.addin_port`. POST
bodies are JSON, at most 2 MiB (413 otherwise). Calls marked "UI thread" are
executed through `Engine.submit` and return 503 if Fusion does not service
them within the timeout.

| Method | Path | Body / query | Response | Notes |
|---|---|---|---|---|
| GET | `/api/ping` | — | `{ok, version, started_at}` | Used by the harness restart worker. |
| GET | `/api/status` | — | status snapshot + `has_work`, `api_port` | |
| GET | `/api/events` | `since=<seq>` | `{events: [...]}` | From the in-memory ring, at most 200 newer than `since`. |
| GET | `/api/hub/hubs` | — | `{hubs: [{id, name, active, type}]}` | UI thread, 60 s. `type` is `personal` or `team`. |
| GET | `/api/hub/projects` | `hub=<id or name>` | `{projects: [{id, name, root_folder_id}]}` | UI thread, 90 s. |
| GET | `/api/hub/folder` | `id=<folder id>` (required, 400) | `{id, name, parent_id, project, folders: [{id, name}], files: [{id, name, ext, version, folder_id}]}` | UI thread, 90 s. |
| GET | `/api/model/params` | `id=<file lineage id>` (required) | `{name, version, parameters: [{name, unit, is_text, expression, value, user}]}` | 409 while an order is processing. Opens the model invisibly if not open and closes it afterwards. UI thread, 300 s. |
| POST | `/api/control` | `{"action": "pause", "reason"?}` | `{ok: true}` | `Engine.pause`. |
| POST | `/api/control` | `{"action": "resume"}` | `{ok: true}` | Clears pause and restart request. |
| POST | `/api/control` | `{"action": "cancel"}` | `{ok: bool}` | `false` when no task is running. |
| POST | `/api/control` | `{"action": "reload_config"}` | `{ok: true}` | Re-reads `config.json`. |
| POST | `/api/control` | `{"action": "gc"}` | `{ok: true}` | Full garbage collection task; 500 with `busy processing; ...` if an order runs. |
| POST | `/api/dev/exec` | `{"code": "...", "timeout"?: 300}` | `{stdout, result, error, elapsed_s}` | 403 unless `api.dev_exec` is true. Runs on the UI thread with globals `app, adsk, ui, engine, ops, state, cfg, paths, result`. |
| POST | `/api/dev/reload` | — | 202 `{ok, message: "reload scheduled"}` | In-place code reload. Requires `api.dev_exec` (403 otherwise). |

Other status codes: 400 unknown action / missing id, 404 unknown path.

### 15.2 Harness API (`harness/server.py`, default `127.0.0.1:8765`)

Bind address and port: `--host`/`--port`, else `api.harness_bind` /
`api.harness_port`. Request bodies (POST/PUT) are JSON, at most 6 MiB (413),
invalid JSON → 400.

Static and files:

| Method | Path | Response |
|---|---|---|
| GET | `/` | `harness/static/index.html` |
| GET | `/static/<file>` | File from `harness/static/` (404 outside it). |
| GET | `/files/output/<relative path>` | File under the output folder, served inline (used for PDF links). 404 for anything resolving outside it. |

Data:

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET | `/api/overview` | `history=<points>` (default 360) | `{now, harness_version, fusion: {running, pid, threads, working_set_mb, handles, started_at}, addin: {online, heartbeat_age_s, status}, queue: {inbox, processing, done, failed}, supervisor: {auto_restart, restart_in_progress, restarts (last 10)}, thresholds (the health config), history: [{t, threads, mb, handles, docs, state}]}` |
| GET | `/api/events` | `since_addin`, `since_harness` | `{events (merged, sorted by ts, max 300), last_addin, last_harness}`; add-in events are read from `events.jsonl(.1)`. |
| GET | `/api/orders` | `folder=inbox|processing|done|failed` (default `done`) | `{orders: [{name, folder, mtime, size, summary?}]}`. `inbox`: all files, oldest first (processing order). Other folders: newest first, max 300, with the report's `summary`. |
| GET | `/api/orders/report` | `folder=done|failed|processing`, `name` | The `<stem>.report.json`; for `processing`: `{checkpoint, status}`. 404 when there is no report. |
| GET | `/api/config` | — | Full merged config (re-read from disk). |
| PUT | `/api/config` | full config object | Merged, validated (400 with the validation messages), saved config. Then tells the add-in `reload_config` (ignored if offline). |
| POST | `/api/orders/preview` | `{content: <JSON text or object>}` | `{order_id, counts, components: [{component_id, type, series_id, family, rule_id, model, drawing}]}`; 400 for invalid orders. |
| POST | `/api/orders/upload` | `{name, content}` | `{name, components}`; the file name is sanitised with `safe_name`; an existing name gets a `_HHMMSS` suffix. 400 for invalid orders. |
| GET | `/api/scenarios` | — | `{sheets: [{name, title, total, drawings}], folder}`: the CSV sheets in `scenarios/`. |
| GET | `/api/scenarios/rows?sheet=<name>` | — | `{name, title, columns, rows: [[number, scenario, order_id, drawing_required, series_id, label]]}`. 404 for an unknown sheet. |
| POST | `/api/scenarios/upload` | `{name, content: <CSV text>}` | `{name, total}`; saved under `scenarios/`. 400 when it is not a scenario sheet. |
| POST | `/api/scenarios/generate` | `{sheet, mode: drawings|selection, selection: "7, 120-135"}` | `{queued, skipped, drawing_test}`: one order per scenario written to the inbox as `<order_id>.json`; a scenario already in the inbox or being processed is skipped. 409 in remote mode (the inbox is a network share). |
| POST | `/api/orders/requeue` | `{folder: done|failed, name}` | `{name}` (the inbox name). 404 if missing. |
| POST | `/api/orders/remove` | `{name}` | `{ok: true}`; deletes an inbox file. |
| POST | `/api/config/suggest` | — | `{suggestions: [rule...], scanned_files}`; reads the family folders through the add-in. |
| POST | `/api/control` | `{action: pause|resume|cancel|gc|reload_config}` | Forwarded to the add-in's `/api/control` (40 s). |
| POST | `/api/control` | `{action: "restart_fusion", reason?}` | `{restart: {at, reason, result}}`; 409 if a restart is already running. |
| POST | `/api/control` | `{action: "start_fusion"}` | `{ok: true}`; 409 if Fusion is running. |
| POST | `/api/control` | `{action: "auto_restart", enabled: bool}` | `{auto_restart}`; writes `supervisor.auto_restart_fusion` to config. |
| GET | `/api/logic` | — | `{profiles, profiles_source, profiles_file, errors, drilling, splice, naming: {templates, placeholders, example}}` — what the add-in will use. |
| POST | `/api/logic/evaluate` | `{profile_id, values: {param: value}}` | `{matched, note, setups, op_rules, unknown_operations, rows: [[row_id, bool]], env, missing}` |
| POST | `/api/logic/drilling` | `{type, series_id, order_values, model_values}` | `{result: <component drilling>}` |
| PUT | `/api/logic/profiles` | `{profiles: [...]}` | Validated (400 with messages) and saved to `cam.profiles_file`. |
| POST | `/api/logic/reset` | — | Moves the profiles file to `.bak` (built-in tables apply). |
| GET | `/api/location` | — | `{mode, location, inbox, directories: [{role, path, exists, writable, timed_out?}], sync: <SyncService.status()>}` |
| POST | `/api/location` | `{mode: local|remote}` | Saves `location.mode`, tells the add-in to reload config, returns `/api/location`. |
| POST | `/api/sync` | `{action: run_once|resync, pair?}` | Trigger a pass / clear the sync state (files are sent again). |
| GET | `/api/docs`, `/docs/<name>` | — | Document list and files (`docs/*.md|svg`, `docs/tables/*.md`, `README.md`). |

Proxied to the add-in (503 `add-in not reachable ...` when it is down; add-in
error codes are passed through):

| Method | Path | Add-in path | Timeout |
|---|---|---|---|
| GET | `/api/hub/hubs` | `/api/hub/hubs` | 90 s |
| GET | `/api/hub/projects?hub=` | `/api/hub/projects` | 120 s |
| GET | `/api/hub/folder?id=` | `/api/hub/folder` | 120 s |
| GET | `/api/model/params?id=` | `/api/model/params` | 320 s |

---

## 16. Security notes

* **Localhost binding.** The add-in API always binds `127.0.0.1`. The harness
  binds `api.harness_bind` (default `127.0.0.1`). Neither API has
  authentication; binding the harness to another interface exposes order
  upload, config editing and Fusion restart to that network.
* **Cross-site request protection.** Every POST/PUT to either server must
  carry `Content-Type: application/json` (otherwise 415) and, if the request
  has an `Origin` header, it must be the server's own origin (harness) or a
  `http://127.0.0.1:` / `http://localhost:` origin (add-in); otherwise 403.
  A web page on another site can only send JSON after a CORS preflight, which
  neither server grants, so it cannot upload orders, change config, restart
  Fusion or reach `dev_exec` (`_check_same_site` in `harness/server.py` and
  `nfe/api_server.py`; covered by `test_cross_site_requests_refused`).
* **`api.dev_exec` must be `false` in production.** When true,
  `POST /api/dev/exec` runs arbitrary Python inside Fusion with full access to
  the user's files and Autodesk session. The shipped `DEFAULTS` value is
  `false`; check `config/config.json` (the development machine's copy has it
  set to `true`).
* **`/api/dev/reload` requires `api.dev_exec`.** It only reloads code from
  disk, but it abandons the current task (the order resumes from its
  checkpoint), so it is a development action like `dev_exec`.
* **Path traversal guards.**
  * `/files/output/<path>`: `Harness.output_file` URL-decodes, resolves with
    `os.path.realpath` and requires the result to be the output folder or
    inside it (`base + os.sep` prefix).
  * `/static/<path>`: resolved with `realpath` and required to be inside the
    static folder (`folder + os.sep` prefix).
  * `/docs/<name>`: only the base name is used, only `.md`/`.svg`/`.png`
    files from `docs/` (plus the root `README.md`) are served.
  * Order names from the dashboard pass through `os.path.basename`
    (`report`, `requeue`, `remove_inbox`) or `safe_name` (`upload`, which
    replaces everything but word characters, `.` and `-`).
  * `report` and `orders` accept only the fixed folder names.
  * `tests/test_harness.py::test_output_path_traversal_blocked` checks
    encoded and plain `..` variants.
* **Body limits.** 2 MiB (add-in), 6 MiB (harness), order files 5 MiB.
* **Restart kills Fusion.** `restart_fusion` terminates every `Fusion360.exe`
  without saving; unsaved work in any Fusion window on that machine is lost.
* **Foreign documents.** Garbage collection never closes documents that were
  open before the add-in started, and skips modified visible documents it did
  not open.

---

## 17. Lessons carried over from the previous project

From `C:\Users\jderr\Fusion Extension\FusionExtensionVM`
(`docs/Fusion_Thread_Leak_Root_Cause_and_Fix_2026-07-16.pdf`):

| Previous finding | How NFE handles it |
|---|---|
| `Document.close()` can return `True` and do nothing on hidden drawing-reference copies, so they accumulate. | `_close_verified` trusts only `doc.isValid`, escalates to activate+close, and `managed_documents` explicitly includes hidden documents. The unit tests simulate this with `FakeOps(stubborn_closes=1, spawn_hidden_copies=True)`. |
| Long blocking work on the UI thread combined with `adsk.doEvents()` caused crashes. | Work is cut into ticks; waits are `WaitFor` polls between ticks; `adsk.doEvents()` is never called. |

Findings from live testing on this project that are now built into the code:

| Finding | Where it is handled |
|---|---|
| `Parameter.valueType` must be compared with `adsk.fusion.ParameterValueTypes.TextParameterValueType` to detect text parameters. | `FusionOps._is_text` |
| Orders contain JSON `null` values. | `processing.null_policy` (`params.format_expression`) |
| `updateAllReferences()` raises "Design xref is not out of date" when nothing is stale. | `FusionOps.update_drawing` presses it only when a reference is stale and tolerates that message. |
| Only the webdeploy copy of `FusionLauncher.exe` with `FusionLauncher.exe.ini` starts Fusion. | `winproc.find_fusion_launcher` |
| After a Fusion restart, cloud work fails until sign-in completes. | `Engine._wait_until_ready` + `FusionOps.ready`, `Engine._resolve` retry. |
| Fusion grows ~25 MB / 6 threads / 110 handles per component regardless of open documents. | Health limits and `health.restart_every_components`. |

---

## 18. Location modes and network sync

`location.mode` selects where orders come from; results are always written on
this computer first.

| | Local | Remote |
|---|---|---|
| Order inbox | `dropbox/inbox` (`location.local.inbox`) | `\\ddc-mefs\iBob-Export\S2S_Export` (`location.remote.inbox`) |
| Claim | rename | copy + delete (`Dropbox.claim`) |
| Processing, done, failed, output, machine_drops | project folder | project folder |
| Sync destination | `network_mirror/<share>` | the `\\ddc-mefs` shares |

`Paths.apply_location` re-points the inbox in place whenever config is
reloaded (engine at every order, add-in `reload_config`, harness), so the
dashboard's Local/Remote switch applies without restarting anything. The
harness `SyncService` (`nfe/sync.py`) copies, every 15 s, files that have not
changed for 5 s: `machine_drops/anderson` → G-code share (`*.txt`, flat),
`machine_drops/gannomat` → drilling share (`*.json`, flat), `output/` →
output share (recursive, skipping `_partial_*`, `.tmp_*`, `_nc`),
`dropbox/done` and `dropbox/failed` → order_completed / order_failed. Copies go
to `<name>.syncpart` and are renamed, so a machine never sees a partial file.
`state/sync_state.json` remembers what was sent (a file consumed and deleted
by a machine is not sent again; `POST /api/sync {"action": "resync"}` resends).
An unreachable share only affects its own pair and is retried next pass.
Details and every directory: [tables/DIRECTORIES.md](tables/DIRECTORIES.md).

