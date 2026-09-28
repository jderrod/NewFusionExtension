# NewFusionExtension operator guide

How to install, run, configure and troubleshoot NFE. Technical background:
[ARCHITECTURE.md](ARCHITECTURE.md). Diagrams: [WORKFLOW.md](WORKFLOW.md),
[INFRASTRUCTURE.md](INFRASTRUCTURE.md).

---

## 1. Requirements

* Windows workstation with Autodesk Fusion installed and a user that can sign
  in to the hub configured in `hub.name` (default `Nikorohmz`).
* Python 3 with the `py` launcher (the harness uses only the standard library).
  The add-in itself runs on Fusion's bundled Python.
* The project folder (this repository). Its location is the "project root";
  `config/`, `dropbox/`, `output/`, `state/` and `logs/` live in it.

---

## 2. Install

```
powershell -ExecutionPolicy Bypass -File scripts\install_addin.ps1
```

`scripts/install_addin.ps1`:

* creates a directory junction
  `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\NewFusionExtension` →
  `addin\NewFusionExtension` (no admin rights needed);
* writes `addin\NewFusionExtension\nfe_home.txt` with the project root (used if
  someone later copies the folder instead of linking it);
* prints `Already linked` if the junction exists, and stops with an error if a
  real folder of that name is in the way (move it away first).

Uninstall: `powershell -ExecutionPolicy Bypass -File scripts\install_addin.ps1 -Uninstall`
removes only the junction, never the project files.

First run (or to load without restarting Fusion): in Fusion, Utilities ›
Add-Ins (Shift+S) › Add-Ins tab › NewFusionExtension › Run. After that the
manifest's `runOnStartup: true` loads it every time Fusion starts.

---

## 3. Start and stop

### Start

1. **Start Fusion** and sign in. The add-in loads automatically, starts its
   control API on `127.0.0.1:8766` and, with `processing.autostart: true`
   (default), starts watching `dropbox/inbox`.
2. **Start the harness (dashboard + supervisor):**
   * `start_harness.bat`: runs `pyw -3 harness\server.py` in the background
     and opens http://127.0.0.1:8765/; or
   * `py -3 harness\server.py [--port 8765] [--host 127.0.0.1] [--no-supervisor]`
     in a console (`--no-supervisor` = dashboard only, never restarts Fusion); or
   * set `api.launch_harness_from_addin: true` and the add-in starts the
     harness itself when nothing is listening on the harness port (it needs
     `pyw`/`pythonw` on `PATH`, or `api.harness_python`).
3. Open **http://127.0.0.1:8765**.

The add-in and the harness are independent: orders are processed without the
harness, but then nobody restarts Fusion after a crash or a health request.

### Stop

* Close Fusion normally. The add-in writes state `stopped`; the supervisor
  treats that as a clean exit and does **not** restart Fusion. An order in
  progress stays in `dropbox/processing/` and resumes at the next start.
* Stop the harness by ending its `pyw`/`py` process (or Ctrl+C in a console).
* To stop processing without closing Fusion: **Pause** in the dashboard.

---

## 4. The dashboard

### 4.1 Header (all tabs)

| Element | Meaning |
|---|---|
| **Fusion** lamp | Green `running · N threads · M MB` when `Fusion360.exe` exists; red `not running`; red `harness unreachable` when the dashboard cannot reach the harness. |
| **Add-in** lamp | Green `online` when the add-in's heartbeat (from `state/status.json`, same pid as the Fusion process) is younger than `supervisor.heartbeat_stale_s` (60 s). Red `silent <time>` or `not loaded` when Fusion runs but the add-in does not answer; grey `offline` when Fusion is not running. |
| **State** lamp | The engine state, see section 6. Shows `Restarting Fusion…` while the harness restarts Fusion. |
| **Pause** / **Resume** | Pause stops at the next component boundary (the current component finishes). Resume also clears a pending restart request. Only one of the two is visible. |
| **Restart Fusion** / **Start Fusion** | Restart needs a second click within 4 s. It terminates every `Fusion360.exe` (unsaved work is lost), starts Fusion again and waits up to 600 s for the add-in. The order resumes from its checkpoint. When Fusion is not running the button reads **Start Fusion** and only launches it. |

### 4.2 Monitor

* **Order in progress**: order id and file name, current component (id, type,
  series, model / drawing), progress `done+failed / total`, and the route
  `Open model → Apply parameters → Save model → Open drawing → Update drawing →
  Export PDF → Close & clean up` (plus `Save drawing` when enabled) with the
  duration of each finished step and the running time of the current one. A
  line shows what the engine is waiting for (`Waiting for model cloud save…`),
  or `Fusion is busy inside <step> for <time>` when one Fusion call has blocked
  the UI thread for more than 5 s. Chips show each component's status.
* When idle: `Watching the inbox` or `Paused` (with the reason), and the last
  order with an **Open report** button.
* **Counters**: inbox, processing, done and failed order counts; components
  made and component errors in this add-in session.
* **Charts** (last 30 minutes, 5 s samples from the harness): Fusion threads,
  Fusion memory (MB), open documents, each with the configured restart limit
  as a line (or a note when the limit is off the scale).
* **Recent activity**: the last 14 non-debug events.
* **Supervisor**: Fusion pid and uptime, handle count, heartbeat age, models
  kept open (model cache), pending restart request and reason, the last three
  Fusion restarts with reason and result. Controls:
  * **Close leftover documents**: full garbage collection (closes cached
    models and every pipeline document). Refused while an order is running.
  * **Cancel current order**: the current component finishes; all remaining
    components are marked `cancelled`, and the order goes to `failed/`.
  * **Restart Fusion automatically**: writes
    `supervisor.auto_restart_fusion` to `config.json`.

### 4.3 Orders

* **Add an order**: drop or pick one or more JSON files. Each is checked
  (`POST /api/orders/preview`) and shown with the model and drawing each
  component will use; components without a matching rule are flagged (`no
  matching rule`, and a warning that they will fail). **Add to inbox** uploads
  the file.
* **Orders** list with **Inbox / Processing / Done / Failed**:
  * Inbox rows have **Remove** (deletes the file).
  * Done / Failed rows show the result status and `done/total`, and have
    **Run again** (moves the order back to the inbox and deletes its report).
  * Clicking a row opens the report (section 8).

### 4.4 Series & drawings

* **Rules table**: one row per rule with **On**, **Type** (door, stile,
  panel, any), **Family** (2X8X / 3X8X), **Series pattern** (glob; blank =
  whole family), **Model** and **Drawing** (pickers filled from the family
  folders, showing file versions), **Parameters** (opens the model in Fusion
  invisibly and lists its user parameters; not available while an order is
  processing) and **Delete**. Changes are local until **Save changes**
  (validated by the harness; errors are shown and nothing is saved).
* **Suggest rules from hub**: scans the family folders and proposes a rule for
  every model named `<d>X8<code>_<Door|Stile|Panel>...` that has no rule yet
  (e.g. `3X82_Stile` → stile `3?82*`, drawing `3X82_Stile Drawing`). Add them
  one by one or all, then **Save changes**.
* **Add rule**: a new door / 3X8X rule to fill in.
* **Refresh hub files**: re-reads the family folders (after new models or
  versions were uploaded).
* **Check a series**: enter a type and a `series_id` to see which model and
  drawing it would use and which rule matched, using the rules currently on
  screen (including unsaved edits). This check does not use the order-id
  family fallback that the engine applies when a series id has no four-digit
  code.
* **Family folders in the hub**: the folder configured for each family, and a
  hub browser (Hubs › project › folders). **Use for 2X8X** / **Use for 3X8X**
  on a folder saves it as that family's folder.

### 4.4a Logic tables

Shows exactly what the add-in executes (the same data it loads at every order):

| Section | What you see / can do |
|---|---|
| Rules in plain words | [tables/SELECTION_LOGIC.md](tables/SELECTION_LOGIC.md): which inputs lead to which toolpaths, workstops and drilling. |
| Toolpath selection | One table per profile (3X8X doors, 3X82 stiles, 3X86 stiles, 3X8X panels): variables and the parameters they read, the ordered decision rows with the setups each posts, operation rules, and **Review** notes where the old spreadsheets disagree. **Try it**: tick/enter values and **Evaluate** to see the matching row, the setups and the operation states. **Edit tables (JSON)** validates and saves to `config/toolpath_profiles.json`; **Reset to defaults** goes back to the built-in tables (the old file is kept as `.bak`). |
| Splicing | How the per-setup programs are joined: order, the `M0` stop block, FLIP vs ROTATE, renumbering, the numbered-`M30` setting. |
| Drilling & workstops | Workstop table, which stile sides are drilled, FlipRotate, air drilling (position 121 / 0.197), hole sources. **Try it** shows the exact Gannomat records for chosen inputs. |
| Naming | The file and folder name templates with an example. |

### 4.5 Settings

A form over the most used keys, saved with **Save settings** (validated). The
add-in re-reads the configuration at the start of every order.

| Field | Key |
|---|---|
| Start processing when Fusion starts | `processing.autostart` |
| Save the model to the hub after applying parameters | `processing.save_model` |
| Also save the refreshed drawing to the hub | `processing.save_drawing` |
| Process components that share a model back to back | `orders.group_by_model` |
| JSON null values | `processing.null_policy` (`zero` / `skip` / `fail`) |
| Order parameter missing from the model | `processing.unknown_parameter_policy` (`warn` / `fail`) |
| Rule without a drawing | `processing.missing_drawing_policy` (`fail` / `warn`) |
| Models kept open between components | `processing.model_cache_size` |
| Close every document after this many components | `processing.recycle_every_components` |
| Cloud save / drawing update / PDF export timeouts | `processing.save_timeout_s`, `drawing_update_timeout_s`, `export_timeout_s` |
| Pause after opening / updating a drawing | `processing.drawing_settle_s` |
| Restart Fusion when one step blocks longer than | `processing.step_stuck_timeout_s` |
| Restart Fusion above threads / memory / open documents | `health.max_threads`, `max_working_set_mb`, `max_open_documents` |
| Refresh Fusion after this many components | `health.restart_every_components` |
| Let the harness restart Fusion | `supervisor.auto_restart_fusion` |
| Treat the add-in as hung after | `supervisor.heartbeat_stale_s` |
| Minimum time between automatic restarts | `supervisor.restart_cooldown_s` |
| Dropbox folder / PDF output folder | `paths.dropbox`, `paths.output` |

Keys not on the form (edit `config/config.json` directly; the full list is in
ARCHITECTURE.md section 13): `hub.name`, `orders.type_by_key`,
`orders.type_by_prefix`, `orders.derived_parameters`,
`processing.open_visible`, `supervisor.fusion_launcher`, and everything under
`api`. Changes to `api.*` ports and `paths.*` take effect after restarting the
add-in (Fusion) and the harness.

### 4.6 Log

All events from the add-in (`state/events.jsonl`) and the harness (tagged
`harness`), newest first. Filters: **Everything** (includes per-step debug
events), **Info**, **Problems** (warnings and errors).

---

## 5. Configuring rules and family folders

1. Series & drawings › **Family folders in the hub**: browse to the folder
   holding the 2X8X models and press **Use for 2X8X**; the same for 3X8X.
   (Current setup: project `Bobrick - Component modeling`, folders
   `2X8X Components` and `3X8X Components`.)
2. **Suggest rules from hub** › **Add all** (or add individually).
3. Review each row: type, family or series pattern, model, drawing. Every
   enabled rule needs a model; a rule without a drawing fails its components
   unless `missing_drawing_policy` is `warn`.
4. **Save changes**.
5. Use **Check a series** with real series ids (e.g. door `3082G.67P`, stile
   `3081.67`) to confirm the right rule wins.
6. Use **Parameters** on each model to confirm the order JSON parameter names
   match the model's user parameter names exactly.

How a rule is chosen: rules must match the component type (or be "any") and
either the series pattern or the family. An exact type beats "any"; a series
pattern beats a family rule; a longer pattern beats a shorter one. Details in
ARCHITECTURE.md section 9.

---

## 6. States and lamps

| State (lamp text) | Lamp | Meaning | What to do |
|---|---|---|---|
| `idle` (Idle, watching inbox) | green | No order; new files in the inbox are picked up within a few seconds. | — |
| `processing` (Processing) | green | An order is being worked on. Also shown while a pause is pending during an order (the Resume button is visible; the order stops at the next component). | — |
| `paused` (Paused) | amber | No order is taken. Reasons: dashboard pause, `autostart disabled`, or `internal error: ...` (the order stays in `processing/`). | Read the reason on the Monitor tab, fix, press Resume. |
| `waiting_restart` (Waiting for Fusion restart) | amber | A health limit or the scheduled refresh asked for a Fusion restart; the order waits at a component boundary. | Normally the harness restarts Fusion within ~5 s (subject to cooldown). If auto-restart is off, press Restart Fusion, or Resume to continue without restarting. |
| `waiting_fusion` (Waiting for Fusion to sign in) | amber | Readiness gate: Fusion is starting, offline, not signed in, or the configured hub is not visible. | Sign in to Fusion; check `hub.name`; check the network. |
| `stopped` (Stopped) | red | The add-in was stopped (Fusion closed normally or the add-in was stopped in Utilities › Add-Ins). After 60 s without heartbeat the lamp turns to `No add-in`. | Start Fusion / run the add-in. |
| `offline` (No add-in) | grey | The dashboard has no fresh heartbeat from an add-in inside the running Fusion process. | See troubleshooting. |
| Restarting Fusion… | amber | The harness is terminating and relaunching Fusion. | Wait (up to ~10 min). |

---

## 7. Dropping orders

* Copy order files (`*.json`) into `dropbox/inbox/` (or the folder set in
  `paths.dropbox`), or use the Orders tab. Files whose name starts with `.` or
  `~`, and `*.state.json` / `*.report.json`, are ignored.
* Files modified less than 1 s ago are left alone until they settle. When
  writing from another program, write to a temporary name (e.g. `.tmp`) and
  rename to `.json` when complete.
* Orders are processed one at a time, oldest (by modification time) first. An
  order left in `processing/` by a crash or restart is always resumed before a
  new one is taken.
* Order format: see ARCHITECTURE.md section 14.1. Components can be listed
  under `doors`, `stiles`, `panels` (or any other key; the type then comes
  from the id prefix `D`/`S`/`P`). Parameter names must match the model's
  parameter names. `series_id` chooses the rule. `a_order_number` is filled
  with the order id automatically if the model has it.

---

## 7a. Local and remote mode

The **Location** card (Settings tab) and the LOCAL / REMOTE badge in the
header show and switch where orders come from:

| Mode | Orders read from | Results copied to (by the harness sync) |
|---|---|---|
| Local | `dropbox/inbox` | `network_mirror/` in the project folder |
| Remote | `\\ddc-mefs\iBob-Export\S2S_Export` | `\\ddc-mefs\Anderson` (G-code), `\\ddc-mefs\Gannomat\F360_output` (drilling), `\\ddc-mefs\Fusion\Fusion Folders\output`, `...\order_completed`, `...\order_failed` |

Fusion always writes to this computer first (`output/`, `machine_drops/`);
only the harness copies to the network, so a slow share never stalls an order.

Before switching to **Remote** (the button asks you to click twice):

1. Check the **Directories** table: every `\\ddc-mefs` path should say
   *reachable* and *writable*. Unreachable paths are reported within a few
   seconds and simply retried; nothing breaks, but nothing is delivered either.
2. Note that files already mirrored locally are **not** re-sent to the real
   shares after switching (only new files are). Use **Sync now** for new files;
   use the resync action only if the machines really need old files again.
3. The sync runs only while the harness (dashboard) is running.

Claiming an order in remote mode moves it off the iBob share (copy, then
delete) into `dropbox/processing`.

## 8. Reading results

For an order file `X.json` with `order_id` `ID`:

| Where | What |
|---|---|
| `dropbox/done/X.json` + `X.report.json` | All components succeeded (status `ok`). |
| `dropbox/failed/X.json` + `X.report.json` | At least one component failed or was cancelled (`partial` if some succeeded, `failed` if none), or the file was rejected (`rejected`). |
| `output/ID <Mon D YYYY - HHMM>/` | The run folder (one per run; a resumed order keeps its folder). |
| `…/1-<component>-ID.txt` | The component's G-code (all its setups spliced), only when toolpaths were needed. |
| `…/1-<component>-ID-drawing.pdf` | The component's drawing. |
| `…/ID.json` | Gannomat drilling records for all doors and stiles of the order. |
| `…/ID-report.json` | Same report as `X.report.json` (not written for rejected orders). |
| `machine_drops/anderson`, `machine_drops/gannomat` | Flat copies waiting for the sync service. |

Names are set in `config.json` → `naming` (see the Logic tables › Naming view).

In the dashboard report (Orders tab › click an order):

* header: order id, status, `N made, M failed, K warnings`, duration, PDF
  folder;
* per component: status, model, duration, a bar of step durations, a **PDF**
  link, warning count; expanded: error, warnings, series, rule, drawing,
  saved model version (e.g. `v1410`), step durations, drawing reference
  (`3X8X_Door: v679 → v1410`), and the parameter table (`Was`, `Now`, `ok` or
  the failure message), plus order parameters not present in the model.

Common warnings:

| Warning | Meaning |
|---|---|
| `null value(s) written as 0/empty: ...` | The order had `null` for these parameters; with `null_policy: zero` they were written as 0 / empty text. |
| `model X has no parameter(s): ...` | Order parameters the model does not have (ignored under `unknown_parameter_policy: warn`). |
| `model unchanged; no new version saved` | The parameters already had these values, so no new version was needed. |
| `timeline warning/error at <feature>: ...` | Fusion reported a feature problem after the parameter change; check the PDF. |
| `no drawing configured for rule ...` | Only with `missing_drawing_policy: warn`; no PDF was made. |
| `cleanup problem: ...` | Something failed while closing documents; the PDF is still valid. |
| `toolpath table ...: model/order has no ... (used defaults)` | A variable of the toolpath table was not found; its default (0) was used. Check the table and the model. |
| `... toolpath generated with warnings` | Fusion generated the toolpath with a warning; check the G-code. |

---

## 9. Troubleshooting

### 9.1 Add-in and harness

| Symptom | Cause / where to look | Action |
|---|---|---|
| Add-in lamp `not loaded`; Fusion shows "NewFusionExtension failed to start" | Start-up exception. Full traceback in `logs/addin_crash.log`. | Fix the cause (often a broken `config.json` edit or a missing folder); run the add-in again from Utilities › Add-Ins. |
| Add-in not listed in Fusion | Junction missing or wrong. | Run `scripts\install_addin.ps1`; check `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\NewFusionExtension`. |
| Event `Control API could not bind port 8766` | Another process uses the port (e.g. a second Fusion). The add-in still processes orders, but the dashboard cannot control it or browse the hub. | Free the port or change `api.addin_port` (then restart Fusion and the harness). |
| Dashboard shows `harness unreachable` | Harness not running. | `start_harness.bat`. A console start that prints `Harness could not bind 127.0.0.1:8765 ... already running?` means another harness already runs. |
| Event `Harness not started: no pythonw/pyw found` | `api.launch_harness_from_addin` is on but no interpreter was found. | Install the `py` launcher or set `api.harness_python`. |
| Hub browsing / Parameters: `Fusion did not service the request within Ns (it may be busy in a long step)` (HTTP 503) | The UI thread is inside a long Fusion call. | Retry when idle. |
| Parameters: `busy processing an order; try again when idle` (409) | Model inspection is refused during an order. | Retry when idle. |
| Proxy error `add-in not reachable (is Fusion running with NewFusionExtension?)` | Add-in offline. | Start Fusion / the add-in. |

### 9.2 Orders not moving

| Symptom | Cause | Action |
|---|---|---|
| File stays in inbox | Engine paused, waiting for restart, or waiting for Fusion; another order is in `processing/`; file not settled; name not `*.json` or starting with `.`/`~`. | Check the state lamp and Monitor tab. |
| Order stays in `processing/`, state `paused` with `internal error: ...` | An unexpected exception outside a component. Traceback in the event (`Internal error in order task`) and `logs/addin.log`. | Fix the cause, then Resume; or move the file out of `processing/` (delete its `.state.json` too) to skip it. |
| Order moved to `failed/` immediately, status `rejected` | The file could not be parsed. Error in the report and in the Log: `invalid JSON: ...`, `order contains no components`, `duplicate component id 'D1'`, `doors[0] has no id`, `component D1 has no parameters object`, `component D1 parameter P: unsupported parameter value ...`, `order JSON must be an object`, `order file is too large (N bytes)`. | Fix the JSON and drop it again. |

### 9.3 Component failures

| Error message (component `error`) | Cause | Action |
|---|---|---|
| `no series rule matches type=... series_id=... - add one in Series Configuration` | No enabled rule for this type and series. | Add or enable a rule; test with Check a series. |
| `rule has no model selected` | Rule without `model.id`. | Pick a model. |
| `cloud file not found or not accessible: NAME (id)` | The model/drawing was deleted, moved to an inaccessible project, or the hub is unreachable (after 6 retries 10 s apart). | Re-select the file in the rules table (Refresh hub files). |
| `documents.open() returned nothing for NAME` | Fusion could not open the file. | Open it manually in Fusion to see the error. |
| `NAME is not a Fusion design` | The rule's model is not a design. | Pick the `.f3d` model. |
| `bad parameter values: P: ...` | A value could not be formatted: `text values cannot contain single quotes`, `not a boolean: ...`, `empty value for a numeric parameter`, `not a finite number`, `value is null` (with `null_policy: fail`). | Fix the order or the policy. |
| `bad derived parameter template P: ...` | `orders.derived_parameters` uses an unknown placeholder. | Use `{order_id}`, `{component_id}`, `{type}`, `{series_id}`. |
| `model X has no parameter(s): ...` | Only with `unknown_parameter_policy: fail`. | Fix names or set the policy to `warn`. |
| `N parameter(s) failed: P (value did not change to E (now F)); ...` | Fusion did not accept or silently ignored an assignment (read-back mismatch), or `not found`, or `verify failed: ...`. | Check the parameter expression / unit in the model (Parameters button). |
| `timed out after 300s waiting for model cloud save` / `save() returned False for NAME` | The cloud save did not finish (network, hub, large model). The dirty model is closed without saving. | Check the network; raise `save_timeout_s` if saves are just slow. Re-run the order. |
| `no drawing configured for rule R` | Rule without drawing and `missing_drawing_policy: fail`. | Pick a drawing. |
| `drawing D does not reference model M (references: ...)` | The chosen drawing belongs to another model. | Fix the rule's drawing. |
| `timed out after 180s waiting for drawing to reference the new model version` | The drawing did not pick up the new model version. | Check the drawing in Fusion; raise `drawing_update_timeout_s`. |
| `D is not a drawing document` / `could not create PDF export options` / `PDF export returned False` / `timed out after 300s waiting for PDF file` | PDF export failed. | Check write access to `output/`, disk space, and export the drawing manually once. |
| `Fusion stopped during this component 2 times; giving up` | Fusion crashed or was restarted twice while this component ran (`MAX_ATTEMPTS = 2`). | Try the component alone; look at the model; check the Log around the crashes. |
| `order cancelled` | Cancel was pressed. | Run again if needed. |

Failed components never stop the order; the others continue. A component
that failed leaves its model closed without saving, so the next component
starts from the saved state in the hub.

### 9.3a Toolpaths, posting and drilling

| Message | Cause | What to do |
|---|---|---|
| `no row of toolpath table <id> matches` | No decision row applies (e.g. a panel with height and width 0). | Check the order values; extend the table on Logic tables if the case is real. |
| `setup(s) not found in the model: ...` | The model's CAM setup names differ from the table. | Compare the model's setups (Manufacture workspace) with the table; names are matched ignoring case, spacing, dashes and ` (2)` suffixes. |
| `model ... has no manufacturing setups` | A table matched a model without CAM data. | Fix the table's series/family match or add setups to the model. |
| `toolpaths not generated: ...` / timeout waiting for toolpath generation | Fusion could not generate an operation (geometry change broke it) or generation took longer than `cam.generate_timeout_s`. | Open the model, regenerate that operation by hand, fix the CAM setup. |
| `post processor not found` | `cam.post_processor` does not exist. | Check `post/Anderson Stratos 2.cps`. |
| `toolpath tables are invalid: ...` | `config/toolpath_profiles.json` has an error. | Fix it on Logic tables › Edit tables, or Reset to defaults. |
| `the hub keeps returning <model> vN but vM was saved` | Right after a save the hub still hands out an older version (seen live). The engine already reopened 4 times with increasing waits. | Wait a minute and **Run again**; if it persists, check the hub (someone else editing the model?). |
| `model is at vN but vM was already saved; refusing to save an out-of-date copy` | Same cause, caught before saving. | As above. |

### 9.3b Network sync

| Symptom | What to do |
|---|---|
| Location card shows a share *not reachable* | Network/VPN or permissions; files wait in `machine_drops/` / `output/` and are copied when it comes back. |
| A machine needs a file again | Settings › Location: resync the pair (resends everything in it, including files the machines already consumed). |
| Nothing is copied | The harness must be running; check `location.sync.enabled` and the sync table's *Problems* column. |

### 9.4 Restart loops and supervisor problems

| Symptom | Cause | Action |
|---|---|---|
| Fusion restarts repeatedly | Look at the Supervisor panel (last restarts with reason) and the Log. Reasons: `add-in requested restart: ...` (health limit), `add-in heartbeat stale ...` (Fusion hung), `Fusion UI thread blocked ... in step X`, `Fusion is not running (crashed ...)`. | Fix the underlying cause; adjust limits (section 10). |
| Event `Restart limit reached (6/hour); not restarting: ...` | Six restarts within an hour. The supervisor stops restarting until older restarts age out. | Investigate before restarting manually. The crash-loop protection (`MAX_ATTEMPTS`) fails a component that keeps crashing Fusion, so the order can progress. |
| State stays `waiting_restart` | `supervisor.auto_restart_fusion` is off, the harness is not running, the cooldown (`restart_cooldown_s`, 300 s) has not passed, or the hourly limit is reached. | Start the harness / turn on auto-restart, press Restart Fusion, or Resume to continue without a restart. |
| After Resume the engine asks for a restart again after the next component | The health limit is still exceeded, or the scheduled refresh (`health.restart_every_components`) counts components per add-in session and only resets when Fusion restarts. | Restart Fusion, or set `restart_every_components` to 0 when running without auto-restart. |
| Restart result `failed: FusionLauncher.exe not found (set supervisor.fusion_launcher)` | Auto-detection found no `FusionLauncher.exe` with a `FusionLauncher.exe.ini` under `%LOCALAPPDATA%\Autodesk\webdeploy\production\`. | Set `supervisor.fusion_launcher` to the launcher path. |
| Fusion shows "Configuration file ... is missing or incomplete" after a restart | `supervisor.fusion_launcher` points to a `FusionLauncher.exe` in a Fusion build folder (no `.ini` next to it). | Clear the setting (auto-detect) or point it to the webdeploy copy that has `FusionLauncher.exe.ini`. |
| Restart result `add-in did not come back within 600s` | Fusion started but the add-in did not answer (sign-in dialog, update prompt, add-in disabled, crash on start). | Look at the Fusion window and `logs/addin_crash.log`. |
| `Waiting for Fusion to sign in` never clears | Not signed in, offline, or `hub.name` does not match a visible hub. The event shows the reason (`Fusion is still starting up`, `Fusion is offline`, `not signed in to a hub yet`, `hub X not visible yet`). | Sign in; correct `hub.name`. |
| Open-documents chart rising, `Could not close ...` warnings | Fusion refuses to close some documents. | **Close leftover documents** when idle; a Fusion restart clears them for sure. |

---

## 10. Production checklist

Check `config/config.json` (the development copy has `api.dev_exec: true` and
`api.launch_harness_from_addin: false`):

| Item | Setting / action |
|---|---|
| Remote code execution off | `api.dev_exec: false`. With `true`, any local process (and potentially a web page in a local browser) can run Python inside Fusion via `POST /api/dev/exec`. |
| Dashboard only on this machine | `api.harness_bind: "127.0.0.1"` (the APIs have no authentication). |
| Processing starts with Fusion | `processing.autostart: true`. |
| Fusion starts at logon and signs in by itself | Required for unattended restarts: after a restart the add-in waits in `waiting_fusion` until sign-in completes. |
| Harness always running | `api.launch_harness_from_addin: true` (started by the add-in), or `start_harness.bat` in the Windows Startup folder / Task Scheduler. |
| Automatic restarts on | `supervisor.auto_restart_fusion: true`; `supervisor.fusion_launcher` blank (auto-detect) or a verified webdeploy path. Test once with **Restart Fusion**. |
| Restart timing | `supervisor.heartbeat_stale_s: 60`, `supervisor.restart_cooldown_s: 300`, `processing.step_stuck_timeout_s: 900` (must exceed the longest normal model open / PDF export). |
| Health limits | `health.max_threads: 4000`, `health.max_open_documents: 12`, `health.max_working_set_mb` below the machine's RAM (default 12000), `health.restart_every_components: 200` (the soak measured about 25 MB of growth per component, about 5 GB per 200). |
| Rules | Every enabled rule has a model and a drawing; spot-check with **Check a series**; preview a real order on the Orders tab. |
| Policies | `processing.null_policy: zero`, `unknown_parameter_policy: warn` (or `fail` for strict input), `missing_drawing_policy: fail`, `save_model: true`, `save_drawing: false` unless the updated drawing must be kept in the hub. |
| Verification after installing or updating | `run_tests.bat` (43 tests pass), then one live order: `py -3 tests\live\run_order.py tests\fixtures\20260206_115328_3X8X_DV_0622.json` must exit 0. |
| Housekeeping | `logs/` and `state/events.jsonl` rotate by themselves; `output/`, `dropbox/done/` and `dropbox/failed/` grow without limit; archive them periodically. |
| Dedicated machine | Do not use this Fusion session for manual work: a restart terminates Fusion without saving, and garbage collection closes copies of the configured models and drawings that are not modified. |

---

## 11. Files an operator looks at

| Path | Content |
|---|---|
| `config/config.json` | Configuration (also editable from the dashboard). |
| `dropbox/inbox`, `processing`, `done`, `failed` | Order queue and reports. |
| `output/<order_id> <date>/` | Run folder: G-code, drawings, drilling file, report. |
| `machine_drops/`, `network_mirror/` | Staging for the machines; local-mode copy of the shares. |
| `config/toolpath_profiles.json` | Edited toolpath tables (absent = built-in). |
| `state/status.json` | Live status (what the dashboard shows). |
| `state/events.jsonl` | Event log (the Log tab). |
| `logs/addin.log` | Add-in log (events mirrored, error details). |
| `logs/addin_crash.log` | Exceptions from the add-in's entry point (start, tick handler, ticker, reload). |
| `logs/harness.log` | Harness and supervisor log, including restarts. |

---

## 12. Open decisions

Points found while porting the old pipeline that need a decision from the
shop. Until then the add-in follows the **old code** (the last version that
ran in production).

| # | Topic | Current behaviour | Where to look |
|---|---|---|---|
| 1 | Door and stile toolpath choices where the old spreadsheets (InstructionSelectionLogic, Manufacturing Workflow v3) disagree with the old code | Old code | Logic tables › Toolpath selection (rows with **Review**), [tables/TOOLPATH_SELECTION.md](tables/TOOLPATH_SELECTION.md) |
| 2 | Each spliced section keeps its numbered `N### M30` before the `M0` stop (on a Fanuc-style control `M30` ends the program) | Kept (`cam.strip_numbered_m30: false`) | [tables/SPLICING.md](tables/SPLICING.md) |
| 3 | Drilling details not in any document (3086 X offset −1.4375", air drill at 121 / 0.197, record order) | As old code | [tables/DRILLING.md](tables/DRILLING.md) section 8 |
| 4 | 3X81 stiles: drilled with the 3X82 rules; no toolpaths (no CAM setups in the model) | As listed | [tables/SELECTION_LOGIC.md](tables/SELECTION_LOGIC.md) section 4 |
| 5 | 2X86 stiles would use the 3X82 drilling table | As old code | DRILLING.md |
| 6 | Voorwood cut-list output of the old pipeline is not ported | Not produced | — |
| 7 | Switching local → remote does not resend files already mirrored locally | Only new files | Section 7a |
| 8 | Panels with height and width 0 now fail (old code posted every setup) | Fail with a clear error | — |

