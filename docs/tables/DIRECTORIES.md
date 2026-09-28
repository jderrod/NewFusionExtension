# Directories: local and remote mode

Every folder NFE reads or writes, per location mode, and which process touches
it. Fusion (the add-in) only writes to **local** disk. The one exception is
claiming an order from a network inbox in remote mode, which removes the order
file from the share. The **harness sync service** (`nfe/sync.py`, a background
thread of `harness/server.py`) copies results from local disk to the shares.

* Config: section `location` in `config/config.json` (`mode` = `local` | `remote`).
  The mode is read again before every sync pass and on every add-in tick, so
  switching mode needs no restart.
* `<root>` = the project folder (`NFE_HOME`, e.g.
  `C:\Users\jderr\OneDrive\Desktop\NewFusionExtension`). Relative paths in the
  config are relative to `<root>`.
* Code: `nfe/paths.py` (local layout, inbox by mode), `nfe/dropbox.py`
  (inbox -> processing -> done | failed), `nfe/engine.py` (outputs, drop copies),
  `nfe/sync.py` (mirroring). Tests: `tests/test_sync.py`.

**The old VM base.** The old pipeline used `C:\FusionPipeline` as the local
base on the VM (`DEFAULT_VM_BASE` in the old `src/config.py`, overridable with
`FUSION_PIPELINE_VM_BASE`). It used a separate sandbox base for LOCAL mode.
NFE uses one base for both modes: the project root, which is configurable
(`NFE_HOME`; `paths.dropbox` / `paths.output` move those trees). Only the
destinations change with the mode. See section 4 for how the old folders map
to the new ones.

---

## 1. Local mode (`location.mode = "local"`)

Nothing leaves this computer. The "shares" are folders under
`<root>/<location.local.mirror_root>` (default `network_mirror`). This way a
dev machine exercises the same sync code as production.

| Directory | Written by | Read by | Contents |
|---|---|---|---|
| `<root>/dropbox/inbox` (or `location.local.inbox`) | user, dashboard upload (harness) | add-in (polls, claims) | order `*.json` waiting to run |
| `<root>/dropbox/processing` | add-in | add-in, harness (status) | the order being worked on + `<name>.state.json` checkpoint |
| `<root>/dropbox/done` | add-in | harness (lists), **sync** | finished orders + `<name>.report.json` |
| `<root>/dropbox/failed` | add-in | harness (lists), **sync** | orders with a failed component or a rejected file + report |
| `<root>/output/<order_id> <run_stamp>/` | add-in | harness (`/files/output`), **sync** | `1-<cid>-<oid>-drawing.pdf`, `1-<cid>-<oid>.txt` (G-code), `<oid>.json` (drilling), `<oid>-report.json`; temporary `_partial_*.pdf`, `.tmp_*`, `_nc/` (not synced) |
| `<root>/machine_drops/anderson` (`drops.gcode`) | add-in (flat copy of each G-code) | **sync** (`*.txt`) | flat staging for the Anderson router |
| `<root>/machine_drops/gannomat` (`drops.drilling`) | add-in (flat copy of `<oid>.json`) | **sync** (`*.json`) | flat staging for the Gannomat drill |
| `<root>/network_mirror/gcode` | **sync** | you (testing) | mirror of `machine_drops/anderson` |
| `<root>/network_mirror/drilling` | **sync** | you | mirror of `machine_drops/gannomat` |
| `<root>/network_mirror/output` | **sync** | you | mirror of the whole `output/` tree |
| `<root>/network_mirror/order_completed` | **sync** | you | mirror of `dropbox/done` |
| `<root>/network_mirror/order_failed` | **sync** | you | mirror of `dropbox/failed` |
| `<root>/state/` | add-in (`status.json`, `events.jsonl`), harness sync (`sync_state.json`, `sync_state.json.lock`) | harness, dashboard | live state; the record of what was already copied |
| `<root>/logs/` | add-in (`addin.log`), harness (`harness.log`, which includes sync messages) | you | text logs, not synced |
| `<root>/config/` | dashboard (harness) | add-in, harness | `config.json`, toolpath profiles |

## 2. Remote mode (`location.mode = "remote"`)

Orders come from the iBob export share. Every result is still written locally
first; the harness copies it to the ddc-mefs shares.

| Directory | Written by | Read by | Contents |
|---|---|---|---|
| `\\ddc-mefs\iBob-Export\S2S_Export` (`location.remote.inbox`) | iBob export | add-in (polls; a claim copies the order to local `processing` and then deletes it from the share) | order `*.json` waiting to run |
| `<root>/dropbox/processing` | add-in | add-in, harness | always local, same as local mode |
| `<root>/dropbox/done`, `<root>/dropbox/failed` | add-in | harness, **sync** | always local, same as local mode |
| `<root>/output/<order_id> <run_stamp>/` | add-in | harness, **sync** | always local, same as local mode |
| `<root>/machine_drops/anderson`, `.../gannomat` | add-in | **sync** | always local, same as local mode |
| `\\ddc-mefs\Anderson` (`shares.gcode`) | **sync** (flat, `*.txt`) | Anderson router (consumes files) | G-code programs |
| `\\ddc-mefs\Gannomat\F360_output` (`shares.drilling`) | **sync** (flat, `*.json`) | Gannomat drill (consumes files) | `<order_id>.json` drilling files |
| `\\ddc-mefs\Fusion\Fusion Folders\output` (`shares.output`) | **sync** (recursive) | people | copy of the `output/` tree |
| `\\ddc-mefs\Fusion\Fusion Folders\order_completed` (`shares.order_completed`) | **sync** (recursive) | people | copy of `dropbox/done` |
| `\\ddc-mefs\Fusion\Fusion Folders\order_failed` (`shares.order_failed`) | **sync** (recursive) | people | copy of `dropbox/failed` |
| `<root>/state/`, `<root>/logs/`, `<root>/config/` | same as local mode | | not synced |

**Disabling a pair.** Setting a share to `""` turns that pair off in **both**
modes, so a local run syncs exactly the pairs that production would.

**Drawings drop.** Setting `drops.drawings` (off by default) makes the add-in
copy PDFs to a local drop folder. No sync pair reads that folder.

**Checking paths.** The dashboard can call `nfe.sync.check_paths()`. It
returns, for the inbox and each share or mirror, whether the path exists and
whether it is writable (a `.nfe_probe_*.syncpart` file is created and removed
at once). Each check runs in a worker thread with a short timeout, so a dead
UNC host, which can block for about 20 s, cannot hang the page. Results are
cached for 10 s.

## 3. Sync pairs and guarantees

| Pair | Local source | Layout | Files synced |
|---|---|---|---|
| `gcode` | `machine_drops/anderson` | flat | `*.txt` |
| `drilling` | `machine_drops/gannomat` | flat | `*.json` |
| `output` | `output/` | recursive | everything except the exclusions below |
| `order_completed` | `dropbox/done` | recursive | everything except the exclusions below |
| `order_failed` | `dropbox/failed` | recursive | everything except the exclusions below |

Some names are never synced, in any pair:

* files or folders starting with `_partial_`, `.tmp_` or `.nfe_probe_`
* files ending in `.syncpart` or `.claiming`
* folders named `_nc` (the per-setup post output)

Each pass runs every `location.sync.interval_s` seconds (default 15). It
follows these rules:

1. **Local files are never modified or deleted.** Local disk is the archive
   of record.
2. **Settle time.** A file is copied only after it has been unmodified for
   `location.sync.settle_s` seconds (default 5). A file that is still being
   written is never picked up.
3. **Atomic copy.** The file is copied to `<name>.syncpart` in the destination
   folder, then renamed into place with `os.replace`. A machine sees the
   complete file or nothing. If the target is open, the rename is retried
   briefly; if it still fails, the next pass tries again.
4. **Sent once.** `state/sync_state.json` records the size and mtime of every
   copied file, keyed by `<pair>|<relative path>`. A file is copied again
   only if it changes locally. Whether it still exists at the destination is
   **deliberately not checked**: the machines consume (delete) their files,
   so re-copying missing files would resend every consumed program forever.
5. **Pruning.** A pair's state entries are dropped when their local file is
   gone. Pruning is skipped for a pass in which the source could not be
   listed, and never touches pairs that are disabled.
6. **Unreachable shares are isolated.** A pair checks its share root
   (`\\server\share`) or drive root first, with a timeout (5 s). If the check
   fails, that pair reports `reachable: false` and its files stay pending and
   are retried next pass; the other pairs carry on. Subfolders are created
   only when the share root exists, so a missing share is never recreated
   somewhere local. If a share disappears in the middle of a pass, the pair
   stops at the first failed copy.
7. **Mode switch.** The destinations change on the next pass. Files already
   recorded as sent (in either mode) are **not** re-sent to the new
   destination; only new or changed files go there. To push the backlog, use
   resync.
8. **Resync.** `SyncService.resync(pair)` forgets the record for one pair, or
   for all pairs. Every local file of that pair is sent again, **including
   files the machines already consumed**. Use it only after rebuilding a
   share.
9. **Single instance.** A lock on `sync_state.json.lock` stops a second sync
   service from running against the same state file.
10. **Crash safety.** The state file is saved atomically after every pair. A
    crash therefore resends at most the files of the pair that was
    interrupted.

## 4. Old pipeline -> NFE

| Old (`C:\FusionPipeline` on the VM) | NFE (`<root>`) | Old share |
|---|---|---|
| `order_dropbox` (VM: `\\ddc-mefs\iBob-Export\S2S_Export`) | `dropbox/inbox` / `location.remote.inbox` | same inbox share |
| `order_processing` | `dropbox/processing` | not synced |
| `order_completed` | `dropbox/done` | `\\ddc-mefs\Fusion\Fusion Folders\order_completed` |
| `order_failed` | `dropbox/failed` | `\\ddc-mefs\Fusion\Fusion Folders\order_failed` |
| `output/{models,gcode,parameters,logs}` | `output/<order_id> <run_stamp>/` (one folder per run) | `\\ddc-mefs\Fusion\Fusion Folders\output` |
| `machine_drops/anderson` | `machine_drops/anderson` | `\\ddc-mefs\Anderson` |
| `machine_drops/gannomat` | `machine_drops/gannomat` | `\\ddc-mefs\Gannomat\F360_output` |
| `machine_drops/voorwood` | not ported: NFE writes no cut list | `\\ddc-mefs\Voorwood` |
| `sync_state.json`, `file_sync.lock` | `state/sync_state.json`, `state/sync_state.json.lock` | |
| `sync_logs/file_sync_<date>.log` | `logs/harness.log` | |
| `input/{door,panel,stile}` | none (models come from the hub) | |
