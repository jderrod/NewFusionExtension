# NewFusionExtension workflow diagrams

Mermaid diagrams of how orders and components move through the system. The
text reference for every step is [ARCHITECTURE.md](ARCHITECTURE.md); section
numbers below refer to it. The selection rules in plain words are in
[tables/SELECTION_LOGIC.md](tables/SELECTION_LOGIC.md). A pre-rendered SVG of
diagram (b) is in [workflow.svg](workflow.svg) for viewers without Mermaid
(drawn by `scripts/gen_diagrams.py`; edit that script, not the SVG).

---

## (a) Order lifecycle: inbox → processing → done / failed

Code: `nfe/dropbox.py` (`Dropbox`), `nfe/engine.py` (`Engine._start_next_order`,
`Engine._order_gen`, `Engine._finalize_outputs`), `nfe/naming.py`. See
ARCHITECTURE.md sections 6 and 8.

```mermaid
flowchart TD
    drop["Order JSON arrives in the inbox<br/>local: dropbox/inbox · remote: \\ddc-mefs\iBob-Export\S2S_Export<br/>(or dashboard upload via .uploading + rename)"]
    settle{"Modified more than<br/>1 s ago? (SETTLE_SECONDS)"}
    hint["nfe-inbox thread sets the inbox hint<br/>ticker fires CustomEvent"]
    start{"Engine._start_next_order:<br/>order already in processing/?"}
    resume["Resume that order<br/>(reuses its output folder from the checkpoint)"]
    claim["Dropbox.claim into processing/<br/>same drive: os.replace · network inbox: copy + delete"]
    claimfail["Claim failed (locked / gone)<br/>retry on a later tick"]
    parse{"load_order_file:<br/>valid order?"}
    reject["Dropbox.reject: move to failed/<br/>write &lt;stem&gt;.report.json status rejected"]
    folder["Create run folder output/&lt;order_id&gt; &lt;Mon D YYYY - HHMM&gt;<br/>load toolpath tables, checkpoint, plan (rules, group by model)"]
    loop{"Next planned component"}
    skip["Status done / failed / cancelled<br/>in checkpoint: skip"]
    gate["Component boundary:<br/>wait while paused or restart requested<br/>readiness gate _wait_until_ready"]
    cancel{"Cancel requested?"}
    cancelled["Mark component cancelled"]
    attempts{"Checkpoint says in_progress<br/>and attempts &gt; MAX_ATTEMPTS (2)?"}
    giveup["Mark failed:<br/>Fusion stopped during this component N times; giving up"]
    mark["Checkpoint: in_progress, attempts + 1"]
    run["_run_component<br/>(diagram b)"]
    save["Checkpoint: component result<br/>_check_health (may request restart)"]
    final["Finalize: status ok / partial / failed<br/>&lt;order_id&gt;.json compiled drilling · &lt;order_id&gt;-report.json<br/>copies to machine_drops/anderson + gannomat"]
    ok{"status == ok?"}
    done["Dropbox.finish: move to done/<br/>+ &lt;stem&gt;.report.json, delete checkpoint"]
    failed["Dropbox.finish: move to failed/<br/>+ &lt;stem&gt;.report.json, delete checkpoint"]
    sync["Harness sync service copies machine_drops, output,<br/>done, failed to the shares (diagram f)"]
    requeue["Dashboard: Run again<br/>(requeue to inbox, report deleted)"]
    crash(["Fusion crash / restart / add-in reload<br/>order stays in processing/"])

    drop --> settle
    settle -- no --> settle
    settle -- yes --> hint --> start
    start -- yes --> resume --> parse
    start -- no --> claim
    claim -- ok --> parse
    claim -- error --> claimfail --> start
    parse -- "no: OrderError / OSError" --> reject
    parse -- yes --> folder --> loop
    loop -- "all handled" --> final
    loop --> skip --> loop
    loop --> gate --> cancel
    cancel -- yes --> cancelled --> loop
    cancel -- no --> attempts
    attempts -- yes --> giveup --> loop
    attempts -- no --> mark --> run --> save --> loop
    final --> ok
    ok -- yes --> done
    ok -- "no (partial / failed)" --> failed
    done --> sync
    failed --> sync
    done --> requeue
    failed --> requeue
    requeue --> drop
    run -. "process dies" .-> crash
    crash -. "add-in starts again" .-> start
```

Notes:

* Rejected orders never create an output folder.
* A cancelled component counts as failed for the order status.
* Components that were never reached are reported as `skipped`.
* An exception outside the per-component handler pauses the engine
  (`internal error: ...`); the order stays in `processing/`.

---

## (b) Per-component step flow

Code: `Engine._run_component`, `Engine._component_steps`, `Engine._get_model`,
`Engine._drilling`, `Engine._manufacture`, `Engine._after_component`,
`FusionOps`. See ARCHITECTURE.md section 7. Hexagons are waits (with the
config key and default).

```mermaid
flowchart TD
    rule{"Rule found for<br/>type + series_id?"}
    m1["open_model<br/>cached model alive and unmodified? reuse<br/>else fresh file lookup + open"]
    m2{"Opened version older than<br/>the version we last saved?"}
    m3["Stale version guard: close, wait 5·n s,<br/>look up again, reopen (max 4)"]
    p1["apply_parameters<br/>input transforms (cutout → cutout_A/B), derived a_order_number<br/>modifyParameters batch + read-back verify"]
    s1{"save_model:<br/>document modified?"}
    s2{{"WaitFor model cloud save<br/>version &gt; previous, isComplete<br/>save_timeout_s = 300"}}
    v1["Read recomputed model values<br/>(lengths in inches)"]
    dr["drilling (doors, stiles)<br/>hole records, workstops, FlipRotate, air drill"]
    pt{"plan_toolpaths: toolpath table row<br/>for type + series → setups?"}
    mw["manufacture_workspace<br/>switch to MANUFACTURE<br/>unsuppress chosen setups, set operations"]
    gt{{"generate_toolpaths<br/>WaitFor generation, generate_timeout_s = 300"}}
    pp["post_process: one post per setup<br/>Anderson Stratos 2.cps, program counter"]
    sp["splice_gcode: G57 before G59, M0 + FLIP / ROTATE<br/>renumber N → 1-&lt;comp&gt;-&lt;order&gt;.txt · back to DESIGN"]
    dr0{"Rule has a drawing?"}
    d1["open_drawing + Sleep drawing_settle_s"]
    u1["update_drawing: updateAllReferences only if stale"]
    u2{{"WaitFor drawing references the saved version<br/>drawing_update_timeout_s = 180"}}
    e1["export_pdf → 1-&lt;comp&gt;-&lt;order&gt;-drawing.pdf"]
    e2{{"WaitFor PDF file, export_timeout_s = 300"}}
    okc["status = done"]
    fail["status = failed, error = message"]
    c0["cleanup: back to DESIGN if still in MANUFACTURE<br/>verified close of the drawing"]
    c2{"Failed, or model modified<br/>(CAM always leaves it modified)?"}
    c3["Close model without saving<br/>(next component reopens a clean copy)"]
    c5["Garbage collection + gc.collect()<br/>health sample"]
    ret(["Return result to _order_gen<br/>checkpoint + _check_health"])

    rule -- no --> fail
    rule -- yes --> m1 --> m2
    m2 -- yes --> m3 --> m2
    m3 -- "still stale after 4 tries" --> fail
    m2 -- no --> p1
    p1 -- "bad value / verify mismatch" --> fail
    p1 --> s1
    s1 -- "no: warning, no new version" --> v1
    s1 -- yes --> s2
    s2 -- complete --> v1
    s2 -- timeout --> fail
    v1 --> dr --> pt
    pt -- "no table, or no setups:<br/>skip MANUFACTURE" --> dr0
    pt -- "setups chosen" --> mw --> gt
    gt -- "setup missing / no valid toolpath / timeout" --> fail
    gt --> pp --> sp --> dr0
    dr0 -- "no (policy fail)" --> fail
    dr0 -- yes --> d1 --> u1 --> u2
    u2 -- timeout --> fail
    u2 --> e1 --> e2
    e2 -- ready --> okc
    e2 -- timeout --> fail
    okc --> c0
    fail --> c0
    c0 --> c2
    c2 -- yes --> c3 --> c5
    c2 -- "no: keep in cache" --> c5
    c5 --> ret
```

---

## (c) Sequence of one component

Participants: the dashboard and harness, the add-in's ticker thread, the
engine on Fusion's UI thread, the Fusion API, and the Autodesk cloud. Each
engine activation is one tick; between ticks Fusion processes its own events
(uploads, toolpath generation, regeneration, UI).

```mermaid
sequenceDiagram
    autonumber
    participant D as Dashboard + Harness
    participant T as Add-in ticker thread
    participant E as Engine (Fusion UI thread)
    participant F as Fusion API
    participant C as Autodesk cloud (hub)

    T->>E: fireCustomEvent (engine has work)
    E->>F: findFileById(model).latestVersion, documents.open
    F->>C: look up lineage
    E->>E: opened version >= last saved? (else reopen)
    E->>F: modifyParameters(batch), read back values
    E->>F: doc.save("NFE order component")
    F->>C: upload new version (asynchronous)
    loop each tick until complete or save_timeout_s
        T->>E: tick
        E->>F: isModified? versionNumber? isComplete?
    end
    E->>F: read recomputed parameter values
    E->>E: drilling records (workstops, air drill)
    E->>E: toolpath table → setups (e.g. Setup 8 - G59)
    alt setups chosen
        E->>F: activate MANUFACTURE workspace
        E->>F: unsuppress setups, set operation suppression
        E->>F: cam.generateToolpath(active operations)
        loop each tick until generated or generate_timeout_s
            T->>E: tick
            E->>F: future.isGenerationCompleted?
        end
        E->>F: cam.postProcess(setup, Anderson post) per setup
        F-->>E: output/…/_nc/<program>.nc
        E->>E: splice sections → 1-comp-order.txt (CRLF)
        E->>F: activate DESIGN workspace
    else no setups
        Note over E: manufacture step skipped
    end
    E->>F: documents.open(drawing), updateAllReferences() if stale
    loop each tick until drawing references the saved version
        T->>E: tick
    end
    E->>F: exportManager.execute(PDF, all sheets)
    E->>F: close drawing (verified), close CAM-modified model without saving
    E->>E: GC, health sample, checkpoint
    E-->>D: status.json + events.jsonl updated
    D->>D: sync service copies machine_drops/output to the shares
```

---

## (d) Supervisor restart decision flow

Code: `Harness._supervise`, `Harness.sample_once`, `Harness.check_policies`,
`Harness._restart_worker` in `harness/server.py`. Runs every 5 s
(`SAMPLE_S`). See ARCHITECTURE.md section 12.3.

```mermaid
flowchart TD
    tick(["Every 5 s: sample_once<br/>find Fusion360.exe, process metrics, read status.json"])
    seen["If status.addin_pid == Fusion pid and heartbeat &lt; 30 s old:<br/>remember _seen_pid_with_addin"]
    auto{"supervisor.auto_restart_fusion<br/>and no restart in progress?"}
    cool{"Last restart ended more than<br/>restart_cooldown_s (300) ago?"}
    running{"Fusion running?"}
    same{"status.addin_pid ==<br/>Fusion pid?"}
    req{"status.restart_requested?"}
    stale{"state != stopped and heartbeat age<br/>&gt; heartbeat_stale_s (60)?"}
    blocked{"ui_blocked_s &gt;<br/>step_stuck_timeout_s (900)?"}
    crashed{"Seen with add-in before<br/>and state != stopped?"}
    none(["No action"])
    cap{"6 restarts in the last hour?<br/>(MAX_RESTARTS_PER_HOUR)"}
    limit(["Log once: Restart limit reached; no restart"])
    worker["restart_fusion(reason): record, start worker thread"]
    kill["Terminate every Fusion360.exe<br/>wait up to 60 s each, sleep 3 s"]
    launch{"Launcher: supervisor.fusion_launcher<br/>or webdeploy FusionLauncher.exe with .ini found?"}
    lfail(["result: failed: FusionLauncher.exe not found"])
    ping{"GET add-in /api/ping every 5 s<br/>answered within 600 s?"}
    okr(["result ok, took_s; reset _seen_pid_with_addin<br/>order resumes from checkpoint"])
    nok(["result: add-in did not come back within 600s"])
    endw(["restart_in_progress = false<br/>last_restart_at = now (cooldown starts)"])

    tick --> seen --> auto
    auto -- no --> none
    auto -- yes --> cool
    cool -- no --> none
    cool -- yes --> running
    running -- yes --> same
    same -- no --> none
    same -- yes --> req
    req -- "yes: add-in requested restart" --> cap
    req -- no --> stale
    stale -- "yes: heartbeat stale (hung)" --> cap
    stale -- no --> blocked
    blocked -- "yes: UI thread blocked" --> cap
    blocked -- no --> none
    running -- no --> crashed
    crashed -- "yes: crashed" --> cap
    crashed -- "no (never seen, or clean close)" --> none
    cap -- yes --> limit
    cap -- no --> worker --> kill --> launch
    launch -- no --> lfail --> endw
    launch -- yes --> ping
    ping -- yes --> okr --> endw
    ping -- no --> nok --> endw
```

A dashboard "Restart Fusion" starts `restart_fusion` directly (no cooldown,
no hourly cap; 409 if one is already running). "Start Fusion" (shown when
Fusion is not running) only launches it.

---

## (e) Toolpath, workstop and drilling selection (per side)

Code: `nfe/cam_defaults.py` (tables), `nfe/cam_rules.py`, `nfe/drilling.py`.
Plain-words version: [tables/SELECTION_LOGIC.md](tables/SELECTION_LOGIC.md).
At most one setup per side; panels one setup by orientation.

```mermaid
flowchart TD
    comp{"Component type"}

    comp -- door --> dside["Each side: rabbet (hinge side always; latch side unless<br/>wall keep / perpendicular stile keeper), notches (clearances)"]
    dside --> dsw{"Swing"}
    dsw -- in --> din["Left side → Setup 3 - G57 (Setup 1 if notch only)<br/>Right side → Setup 8 - G59 (Setup 6 if notch only)"]
    dsw -- out --> dout["Left side → Setup 2 - G59<br/>Right side → Setup 5 - G57"]
    din --> dsplice["Both sides: G57 section, ROTATE PART 180, G59 section"]
    dout --> dsplice
    comp -- door --> ddrill["Drill hinge side only<br/>WS1 if hinging_right = swinging_out, else WS4<br/>+ air drill on the other workstop"]

    comp -- "stile 3X82 / 3X86" --> sside["Each side with a door: rabbet<br/>interior if that door swings in, exterior if out"]
    sside --> smap["Left interior → Left Rabbet - In G59 · Left exterior → Left Rabbet - Out G57<br/>Right interior → Right Rabbet - In G57 · Right exterior → Right Rabbet - Out G59"]
    smap --> sboth{"Doors on both sides?"}
    sboth -- "same swings" --> ssame["G57 side first, ROTATE PART 180"]
    sboth -- "opposite swings" --> sopp["Both same workstop, FLIP PART<br/>first: the drilled side · both drilled: exterior side<br/>neither: 3X82 left / 3X86 right"]
    comp -- "stile 3X82 / 3X86" --> sdrill["Drill a side only if its door hinges onto the stile<br/>3X82: left int WS1, left ext WS4, right int WS4, right ext WS1<br/>3X86: mirrored · opposite faces → FlipRotate 3 ext / 4 int"]

    comp -- panel --> pfeat{"Notches or cutouts?"}
    pfeat -- no --> pnone["No toolpath (MANUFACTURE skipped)"]
    pfeat -- yes --> por{"height ≥ width?"}
    por -- yes --> pbot["Setup Back Bottom Corner G58"]
    por -- no --> ptop["Setup Back Top Corner G58"]
```

---

## (f) Location modes and network sync

Code: `nfe/paths.py` (`Paths.apply_location`), `nfe/dropbox.py`
(`Dropbox.claim`), `nfe/sync.py` (`SyncService`), `harness/server.py`
(`Harness.location`, `set_location_mode`, `sync_action`). Fusion only ever
writes to this computer; the harness copies results out.

```mermaid
flowchart LR
    subgraph REMOTE["Remote mode (network)"]
        ibob["\\ddc-mefs\iBob-Export\S2S_Export<br/>(order inbox)"]
        anderson["\\ddc-mefs\Anderson<br/>G-code .txt"]
        gannomat["\\ddc-mefs\Gannomat\F360_output<br/>drilling .json"]
        fusionf["\\ddc-mefs\Fusion\Fusion Folders<br/>output · order_completed · order_failed"]
    end
    subgraph LOCAL["This computer (project folder)"]
        inbox["dropbox/inbox<br/>(local-mode inbox)"]
        proc["dropbox/processing → done / failed"]
        out["output/&lt;order&gt; &lt;date&gt;/<br/>.txt · -drawing.pdf · .json · -report.json"]
        drops["machine_drops/anderson · machine_drops/gannomat"]
        mirror["network_mirror/<br/>(local-mode stand-in for the shares)"]
    end
    fusion["Fusion + add-in<br/>(nfe-inbox thread, engine)"]
    harness["Harness sync service<br/>every 15 s, settle 5 s, .syncpart + rename"]

    ibob -- "remote: claim (copy + delete)" --> fusion
    inbox -- "local: claim (rename)" --> fusion
    fusion --> proc
    fusion --> out
    fusion --> drops
    drops --> harness
    out --> harness
    proc --> harness
    harness -- remote --> anderson
    harness -- remote --> gannomat
    harness -- remote --> fusionf
    harness -- local --> mirror
```
