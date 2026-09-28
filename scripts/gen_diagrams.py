"""Generate docs/workflow.svg and docs/infrastructure.svg.

The diagrams are described as data below so they are easy to keep in step
with the code (standing rule: every change updates the docs and diagrams).

    py -3 scripts/gen_diagrams.py          # rewrite both SVGs
    py -3 scripts/gen_diagrams.py --check  # exit 1 if they are out of date
"""

import os
import sys
from xml.sax.saxutils import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
RESULT = {}

FONT = "'Segoe UI', 'Helvetica Neue', Arial, sans-serif"

STYLE = """
      .h1 { font-size: 20px; font-weight: 700; fill: #1b2220; }
      .sub { font-size: 13px; fill: #4b5551; }
      .st { font-size: 14px; font-weight: 700; fill: #1b2220; }
      .tx { font-size: 12px; fill: #1b2220; }
      .mu { font-size: 12px; fill: #4b5551; }
      .wt { font-size: 12px; fill: #7a4a00; }
      .wb { font-size: 12px; font-weight: 700; fill: #7a4a00; }
      .fl { font-size: 11px; fill: #a61e1e; }
      .ft { font-size: 14px; font-weight: 700; fill: #a61e1e; }
      .fx { font-size: 12px; fill: #6b1515; }
      .ok { font-size: 14px; font-weight: 700; fill: #1f5e3d; }
      .gl { font-size: 11px; fill: #1f5e3d; }
      .ct { font-size: 13px; font-weight: 700; fill: #1b2220; }
      .sec { font-size: 12px; font-weight: 700; fill: #4b5551; letter-spacing: 1px; }
      .step { fill: #e8eefb; stroke: #2346a0; stroke-width: 1.5; }
      .cam { fill: #f1e9fb; stroke: #6b3fa0; stroke-width: 1.5; }
      .drill { fill: #e6f3f5; stroke: #1f6f7a; stroke-width: 1.5; }
      .wait { fill: #fff6e0; stroke: #b7791f; stroke-width: 1.5; stroke-dasharray: 6 4; }
      .fail { fill: #fdecec; stroke: #c53030; stroke-width: 1.5; }
      .done { fill: #e6f4e6; stroke: #2f855a; stroke-width: 1.5; }
      .clean { fill: #f1f3f2; stroke: #4b5551; stroke-width: 1.5; }
      .inner { fill: #ffffff; stroke: #9aa39e; stroke-width: 1; }
      .zone { fill: none; stroke: #9aa39e; stroke-width: 1.2; stroke-dasharray: 3 3; }
      .net { fill: #fbf3e8; stroke: #9c5b12; stroke-width: 1.5; }
      .disk { fill: #f4f4ef; stroke: #6b6b55; stroke-width: 1.3; }
      .flow { stroke: #4b5551; stroke-width: 1.6; fill: none; }
      .link { stroke: #b7791f; stroke-width: 1.2; stroke-dasharray: 4 3; fill: none; }
      .fpath { stroke: #c53030; stroke-width: 1.4; fill: none; }
      .gpath { stroke: #2f855a; stroke-width: 1.4; fill: none; stroke-dasharray: 5 4; }
      .data { stroke: #1f6f7a; stroke-width: 1.4; fill: none; }
      .npath { stroke: #9c5b12; stroke-width: 1.4; fill: none; stroke-dasharray: 6 4; }
"""


def marker(mid, color):
    return ('    <marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
            'markerHeight="8" orient="auto-start-reverse">\n'
            '      <path d="M0,0 L10,5 L0,10 z" fill="%s"/>\n    </marker>' % (mid, color))


class Svg(object):
    def __init__(self, w, h, title, desc):
        self.w, self.h = w, h
        self.out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
                    'font-family="%s">' % (w, h, w, h, FONT),
                    "  <title>%s</title>" % escape(title),
                    "  <desc>%s</desc>" % escape(desc),
                    "  <defs>",
                    marker("arr", "#4b5551"), marker("arrRed", "#c53030"),
                    marker("arrGreen", "#2f855a"), marker("arrTeal", "#1f6f7a"),
                    marker("arrBrown", "#9c5b12"),
                    "    <style>%s    </style>" % STYLE,
                    "  </defs>",
                    '  <rect x="0" y="0" width="%d" height="%d" fill="#fcfcfb"/>' % (w, h)]

    def raw(self, s):
        self.out.append("  " + s)

    def comment(self, s):
        self.out.append("\n  <!-- %s -->" % s)

    def rect(self, x, y, w, h, cls, rx=8):
        self.raw('<rect x="%g" y="%g" width="%g" height="%g" rx="%g" class="%s"/>' % (x, y, w, h, rx, cls))

    def text(self, x, y, s, cls="tx", anchor=None):
        a = ' text-anchor="%s"' % anchor if anchor else ""
        self.raw('<text x="%g" y="%g" class="%s"%s>%s</text>' % (x, y, cls, a, escape(s)))

    def lines(self, x, y, items, cls="tx", step=17, anchor=None):
        for i, s in enumerate(items):
            self.text(x, y + i * step, s, cls, anchor)

    def line(self, x1, y1, x2, y2, cls="flow", mk="arr"):
        m = ' marker-end="url(#%s)"' % mk if mk else ""
        self.raw('<line x1="%g" y1="%g" x2="%g" y2="%g" class="%s"%s/>' % (x1, y1, x2, y2, cls, m))

    def poly(self, pts, cls="flow", mk="arr"):
        m = ' marker-end="url(#%s)"' % mk if mk else ""
        self.raw('<polyline points="%s" class="%s"%s/>' % (" ".join("%g,%g" % p for p in pts), cls, m))

    def save(self, name):
        self.out.append("</svg>\n")
        RESULT[name] = "\n".join(self.out)


# ----------------------------------------------------------------------
# workflow.svg: one component, Engine._component_steps + _after_component
# ----------------------------------------------------------------------

# (number, name, box class, [lines], wait (bold, detail) or None, failure label lines or [])
STEPS = [
    ("1", "open_model", "step",
     ["Cached model still valid and unmodified: reuse it. Otherwise",
      "_resolve the cloud file (fresh lookup), reuse an open copy or open it.",
      "Stale version guard: if the hub hands out a version older than the",
      "one this add-in saved, close and reopen (Engine._get_model)."],
     ("Sleep 0.5 s", "lookup retry 6 x 10 s; stale: reopen", "up to 4 x, waiting 5 s x attempt"),
     ["file not found / open failed /", "hub keeps returning an old version"]),
    ("2", "apply_parameters", "step",
     ["orders.input_transforms; derived a_order_number = order id",
      "plan_assignments: units, bools 1/0, quoted text, null_policy",
      "design.modifyParameters (one batch), read back every value;",
      "timeline errors / warnings added as warnings"],
     ("yield None", "one tick for Fusion to recompute"),
     ["bad value, verify mismatch,", "unknown param (policy fail)"]),
    ("3", "save_model  (processing.save_model, default on)", "step",
     ["Refuses to save a copy older than the version already saved.",
      "doc.save(\"NFE <order_id> <component_id>\")",
      "Complete when: not modified, version > previous, isComplete.",
      "Then read_values: the recomputed model values (lengths in inches)."],
     ("WaitFor model cloud save", "save_timeout_s = 300 s"),
     ["save() False / timeout /", "out-of-date copy"]),
    ("4", "drilling  (doors and stiles; drilling.enabled)", "drill",
     ["drilling.component_drilling: hole records for the Gannomat.",
      "Door: hinge side, WS1 or WS4, air drill on the other workstop.",
      "Stile: only sides a door hinges onto; workstop per side and",
      "FlipRotate. Kept for the order's drilling file (no Fusion work)."],
     None, []),
    ("5", "plan_toolpaths  (cam.enabled)", "cam",
     ["Find the component's toolpath table (3X8X doors, 3X82 / 3X86",
      "stiles, 3X8X panels); read its variables from the model/order.",
      "First matching row gives the setups: one per side for doors and",
      "stiles, exactly one for panels (height >= width or not).",
      "No setups (or no table for the component): skip to 10."],
     None, ["no row matches /", "toolpath tables invalid"]),
    ("6", "manufacture_workspace", "cam",
     ["Activate the model, switch to MANUFACTURE (cam.switch_workspace).",
      "cam_prepare: unsuppress the chosen setups and operations,",
      "suppress the rest, apply the table's operation rules."],
     None, ["no CAM setups /", "setup not found in the model"]),
    ("7", "generate_toolpaths", "cam",
     ["cam.generateToolpath for the chosen setups; then check every",
      "operation: errors fail, warnings are recorded."],
     ("WaitFor toolpath generation", "cam.generate_timeout_s = 300 s"),
     ["not generated / timeout"]),
    ("8", "post_process", "cam",
     ["Each setup with post/Anderson Stratos 2.cps into output/.../_nc/,",
      "program number from state/nc_program_counter.txt (O1001 ...)."],
     ("yield None between posts", "keeps Fusion responsive"),
     ["post processor missing / error"]),
    ("9", "splice_gcode", "cam",
     ["G57 section before G59; M0 stop block with FLIP or ROTATE text;",
      "N renumbered, CRLF, one program 1-<component>-<order>.txt.",
      "Removes _nc/ (unless keep_nc_sections); back to DESIGN."],
     None, ["write error"]),
    ("10", "open_drawing", "step",
     ["Rule without a drawing: fail (default) or warn and finish",
      "without a PDF (missing_drawing_policy).",
      "Otherwise _resolve the drawing, reuse an open copy or open it."],
     ("Sleep drawing_settle_s", "default 2.0 s"),
     ["no drawing (policy fail)"]),
    ("11", "update_drawing", "step",
     ["updateAllReferences() only when a reference is stale;",
      "getLatestVersion() until the reference reaches the saved",
      "model version. Then settle 2 s."],
     ("WaitFor new version referenced", "drawing_update_timeout_s = 180 s"),
     ["not referenced / timeout"]),
    ("12", "export_pdf", "step",
     ["All sheets to _partial_<name>.pdf, then os.replace to",
      "1-<component>-<order>-drawing.pdf in the run folder."],
     ("WaitFor PDF file (%PDF-, 64+ bytes)", "export_timeout_s = 300 s"),
     ["export error / timeout"]),
    ("13", "save_drawing  (processing.save_drawing, default off)", "step",
     ["Save the updated drawing to the hub as a new version."],
     ("WaitFor drawing cloud save", "save_timeout_s = 300 s"),
     ["timeout"]),
]


def workflow():
    left, bw, wx, ww, fx, fw = 40, 470, 540, 270, 1030, 210
    fail_x = fx - 2
    sections = {"4": "DRILLING", "5": "MANUFACTURE  (only when the table picks setups)", "10": "DRAWING"}
    y = 100
    s = Svg(1280, 0, "NewFusionExtension per-component workflow",
            "Steps of Engine._component_steps and _after_component in nfe/engine.py: open model, "
            "apply parameters, save model, drilling, toolpath selection, manufacture workspace, generate, "
            "post, splice, open drawing, update drawing, export PDF, optional drawing save, then cleanup, "
            "with waits, timeouts and the failure path.")
    body = []
    s.out, head = body, s.out   # draw into body; header fixed at the end once the height is known
    s.text(40, 40, "NewFusionExtension: per-component workflow", "h1")
    s.text(40, 64, "Engine._component_steps (nfe/engine.py). Each step runs on Fusion's UI thread one tick at a "
                   "time; waits are polled once per tick and never block Fusion.", "sub")

    s.comment("row 0: series rule")
    s.rect(left, y, bw, 50, "step")
    s.text(left + 16, y + 21, "Series rule found for type + series_id?", "st")
    s.text(left + 16, y + 40, "nfe.rules.resolve, chosen when the order is planned", "mu")
    s.line(left + bw, y + 25, fail_x, y + 25, "fpath", "arrRed")
    s.text((wx + ww + fx) / 2 + 60, y + 19, "no matching rule", "fl", "middle")
    s.line(left + bw / 2, y + 50, left + bw / 2, y + 78)
    s.text(left + bw / 2 + 8, y + 70, "yes", "mu")
    y += 80

    pos = {}
    for num, name, cls, body_lines, wait, fail in STEPS:
        if num in sections:
            s.text(left + bw / 2 + 14, y - 8, sections[num], "sec")
        h = 34 + 17 * len(body_lines)
        pos[num] = (y, h)
        s.comment("step %s: %s" % (num, name.split()[0]))
        s.rect(left, y, bw, h, cls)
        s.text(left + 16, y + 22, "%s  %s" % (num, name), "st")
        s.lines(left + 16, y + 42, body_lines)
        mid = y + h / 2
        right = left + bw
        if wait:
            wh = 22 + 18 * len(wait)
            s.line(right, mid, wx, mid, "link", None)
            s.rect(wx, mid - wh / 2, ww, wh, "wait")
            s.text(wx + ww / 2, mid - wh / 2 + 22, wait[0], "wb", "middle")
            s.lines(wx + ww / 2, mid - wh / 2 + 40, wait[1:], "wt", 18, "middle")
            right = wx + ww
        if fail:
            s.line(right, mid, fail_x, mid, "fpath", "arrRed")
            cx = (right + fail_x) / 2 if wait else (wx + ww + fail_x) / 2
            s.lines(cx, mid - 6 - 13 * (len(fail) - 1), fail, "fl", 13, "middle")
        y += h
        s.line(left + bw / 2, y, left + bw / 2, y + 26)
        y += 28 + (0 if STEPS[-1][0] == num else 0)

    # no toolpaths needed: skip the manufacture workspace
    y5, h5 = pos["5"]
    y10, _ = pos["10"]
    s.comment("bypass: no setups chosen -> drawing, MANUFACTURE never opened")
    s.poly([(left, y5 + h5 - 14), (30, y5 + h5 - 14), (30, y10 + 16), (left - 2, y10 + 16)], "gpath", "arrGreen")
    s.text(left + 8, y10 - 8, "skip MANUFACTURE", "gl")

    s.comment("done")
    done_y = y
    s.rect(left + 115, done_y, 240, 44, "done")
    s.text(left + bw / 2, done_y + 27, "status = done", "ok", "middle")
    y4, h4 = pos["10"]
    s.poly([(left, y4 + h4 - 12), (22, y4 + h4 - 12), (22, done_y + 22), (left + 113, done_y + 22)], "gpath", "arrGreen")
    s.text(28, done_y + 14, "policy warn", "mu")

    s.comment("failure box")
    s.rect(fx, 100, fw, done_y + 44 - 100, "fail")
    cy = (100 + done_y + 44) / 2 - 80
    s.text(fx + fw / 2, cy, "Component failed", "ft", "middle")
    s.lines(fx + fw / 2, cy + 24, ["status = failed", "error = the message", "",
                                  "ComponentError and", "StepTimeout: message only",
                                  "other exceptions: also", "a traceback", "",
                                  "The order continues with", "the next component."], "fx", 18, "middle")

    y = done_y + 44
    s.line(left + bw / 2, y, left + bw / 2, y + 40)
    s.line(fx + fw / 2, y, fx + fw / 2, y + 40, "fpath", "arrRed")
    y += 42

    s.comment("cleanup")
    s.rect(40, y, 1200, 170, "clean")
    s.text(56, y + 23, "14  cleanup: always runs, after success or failure  (Engine._after_component)", "st")
    boxes = [
        ("Workspace + drawing", ["back to DESIGN if still", "in MANUFACTURE;",
                                 "_close_verified drawing:", "wait for isValid false"]),
        ("Model", ["failed, or dirty after", "CAM: close unsaved;", "else keep for the next",
                   "component (cache size 1)"]),
        ("Periodic recycle", ["every 25 components", "(recycle_every_components)",
                              "also closes the cached", "models"]),
        ("Garbage collection", ["close up to 20 managed", "docs: ours, hidden copies,",
                                "configured files (verified)", "then Python gc.collect()"]),
        ("Health sample", ["threads, MB, handles,", "open documents; stored", "as health_after and in",
                           "status.json"]),
    ]
    bx = 56
    for i, (t, ls) in enumerate(boxes):
        s.rect(bx, y + 37, 220, 118, "inner", 6)
        s.text(bx + 12, y + 57, t, "ct")
        s.lines(bx + 12, y + 77, ls)
        if i < len(boxes) - 1:
            s.line(bx + 221, y + 96, bx + 235, y + 96)
        bx += 237
    y += 170
    s.line(640, y, 640, y + 28)
    y += 30

    s.comment("back in the order")
    s.rect(200, y, 880, 68, "step")
    s.text(640, y + 21, "Back in Engine._order_gen", "st", "middle")
    s.text(640, y + 40, "checkpoint, counters, _check_health (may request a Fusion restart), next component",
           "tx", "middle")
    s.text(640, y + 58, "after the last one: _finalize_outputs: <order>.json drilling file, copies to machine_drops/, "
           "report", "tx", "middle")
    y += 68 + 36

    s.comment("legend")
    items = [("step", "Model / drawing step"), ("drill", "Drilling"), ("cam", "Manufacture (CAM)"),
             ("wait", "Wait: polled once per tick"), ("done", "Success")]
    lx = 40
    for cls, label in items:
        s.rect(lx, y, 18, 12, cls, 2)
        s.text(lx + 24, y + 11, label)
        lx += 40 + 7 * len(label)
    s.line(lx, y + 6, lx + 36, y + 6, "fpath", "arrRed")
    s.text(lx + 44, y + 11, "Failure path")
    s.line(lx + 140, y + 6, lx + 176, y + 6, "gpath", "arrGreen")
    s.text(lx + 184, y + 11, "Skip")
    y += 30
    s.text(40, y, "Pause, cancel and restart requests are honoured only between components; a component always "
                  "finishes its steps and cleanup first. Timeouts: config keys, defaults from nfe/config.py.", "mu")
    H = y + 26
    head[0] = head[0].replace('height="0"', 'height="%d"' % H).replace("0 0 1280 0", "0 0 1280 %d" % H)
    head[-1] = head[-1].replace('height="0"', 'height="%d"' % H)
    s.out = head + body
    s.save("workflow.svg")


# ----------------------------------------------------------------------
# infrastructure.svg
# ----------------------------------------------------------------------

def infrastructure():
    s = Svg(1280, 1000, "NewFusionExtension infrastructure",
            "Processes, threads, ports, files and external systems: the harness (dashboard, supervisor, "
            "network sync), Fusion 360 with the add-in (API server, inbox thread, ticker, engine), the "
            "Autodesk hub, the local project folders and the ddc-mefs network shares.")
    s.text(40, 40, "NewFusionExtension: software infrastructure", "h1")
    s.text(40, 64, "Two processes on the workstation. Fusion only writes locally; the harness copies results "
                   "to the network, so a slow share never stalls an order.", "sub")

    # browser
    s.comment("browser")
    s.rect(40, 90, 300, 70, "inner")
    s.text(56, 114, "Browser: dashboard", "st")
    s.lines(56, 134, ["http://127.0.0.1:8765  (Orders, Series, Logic", "tables, Settings + Location, Docs)"], "tx", 16)
    s.line(190, 160, 190, 206)
    s.text(198, 188, "HTTP, same-site POSTs only", "mu")

    # harness process
    s.comment("harness process")
    s.rect(40, 208, 560, 330, "zone")
    s.text(56, 230, "Harness process  (pythonw harness/server.py, port 8765)", "st")
    hb = [
        (56, 244, "HTTP server", ["static dashboard, /api/* ", "proxy to the add-in API,",
                                    "logic tables, docs, uploads"]),
        (326, 244, "Supervisor thread", ["polls the add-in heartbeat;", "starts / restarts Fusion",
                                         "(FusionLauncher + .ini)", "on hang or restart request"]),
        (56, 384, "Sync thread  (nfe.sync)", ["every 15 s: new files older", "than 5 s, copy as .syncpart",
                                             "then rename; state file;", "local: network_mirror/"]),
        (326, 384, "Location", ["config.json location.mode", "local / remote; checks", "every path reachable /",
                                "writable (non-blocking)"]),
    ]
    for x, y, t, ls in hb:
        s.rect(x, y, 258, 132, "step")
        s.text(x + 12, y + 22, t, "ct")
        s.lines(x + 12, y + 44, ls, "tx", 17)

    # fusion process
    s.comment("fusion process")
    s.rect(680, 90, 560, 448, "zone")
    s.text(696, 112, "Fusion 360 process  (add-in NewFusionExtension)", "st")
    fb = [
        (696, 126, 258, "API server thread", ["127.0.0.1:8766 (api.port)", "status, orders, rules, gc,",
                                               "hub browse; JSON + same-site"]),
        (966, 126, 258, "Inbox thread (nfe-inbox)", ["scans the inbox (local or", "\\\\ddc-mefs share), claims",
                                                      "orders into processing/"]),
        (696, 262, 258, "Ticker thread", ["fires one CustomEvent at a", "time (never adsk.doEvents)",
                                           "writes the heartbeat"]),
        (966, 262, 258, "UI thread: Engine", ["generator state machine:", "model, drilling, CAM,",
                                              "drawing, cleanup, GC"]),
        (696, 398, 528, "FusionOps (nfe/fusion_ops.py)",
         ["documents, parameters, save, drawing references, PDF export,",
          "CAM: workspace, setups, generateToolpath, postProcess",
          "(post/Anderson Stratos 2.cps)"]),
    ]
    for x, y, w, t, ls in fb:
        s.rect(x, y, w, 122, "cam" if "Engine" in t or "FusionOps" in t else "step")
        s.text(x + 12, y + 22, t, "ct")
        s.lines(x + 12, y + 44, ls, "tx", 17)
    s.line(825, 384, 825, 398)
    s.line(1095, 384, 1095, 398)
    s.line(954, 322, 966, 322)

    # harness <-> addin
    s.line(600, 290, 694, 180, "flow", "arr")
    s.text(676, 176, "HTTP :8766", "mu", "end")
    s.poly([(694, 330), (640, 330), (640, 320), (586, 320)], "link", "arr")
    s.text(600, 346, "heartbeat file", "wt")

    # hub
    s.comment("hub")
    s.rect(980, 570, 260, 86, "inner")
    s.text(996, 594, "Autodesk hub (cloud)", "st")
    s.lines(996, 614, ["2X8X / 3X8X models + drawings;", "save = new version (async)"], "tx", 17)
    s.line(1110, 520, 1110, 568)

    # local disk
    s.comment("local project folder")
    s.rect(40, 580, 900, 230, "zone")
    s.text(56, 602, "Project folder (this computer)", "st")
    disks = [
        (56, 616, "dropbox/", ["inbox (local mode)", "processing, done, failed", "*.report.json"]),
        (276, 616, "output/", ["<order> <Mon D YYYY - HHMM>/", "1-<comp>-<order>.txt",
                                "1-<comp>-<order>-drawing.pdf", "<order>.json, -report.json"]),
        (496, 616, "machine_drops/", ["anderson/ (G-code)", "gannomat/ (drilling)", "flat copies for the sync"]),
        (716, 616, "config/, state/, post/", ["config.json, rules, toolpath", "tables; status, checkpoint,",
                                               "NC counter; .cps .mch .tools"]),
        (56, 726, "network_mirror/", ["local-mode stand-in for", "the shares"]),
        (276, 726, "docs/, logs/", ["documentation + diagrams", "(served on the Docs tab)"]),
    ]
    for x, y, t, ls in disks:
        s.rect(x, y, 210, 100 if y == 616 else 72, "disk", 6)
        s.text(x + 10, y + 20, t, "ct")
        s.lines(x + 10, y + 39, ls, "tx", 16)
    s.line(820, 520, 820, 578, "data", "arrTeal")
    s.text(828, 556, "claims orders, writes run folder + drops", "mu")

    # sync -> network
    s.comment("network shares")
    s.rect(40, 850, 1200, 120, "net")
    s.text(56, 874, "Network shares  (remote mode; paths from the old pipeline)", "st")
    shares = [("\\\\ddc-mefs\\iBob-Export\\S2S_Export", "incoming orders (inbox)"),
              ("\\\\ddc-mefs\\Anderson", "G-code .txt"),
              ("\\\\ddc-mefs\\Gannomat\\F360_output", "drilling .json"),
              ("\\\\ddc-mefs\\Fusion\\Fusion Folders\\output", "run folders"),
              ("...\\order_completed, ...\\order_failed", "finished orders")]
    for i, (p, d) in enumerate(shares):
        x = 56 + (i % 3) * 395
        y = 900 + (i // 3) * 36
        s.text(x, y, p, "tx")
        s.text(x, y + 15, d, "mu")
    s.poly([(185, 516), (185, 540), (20, 540), (20, 840), (60, 840), (60, 848)], "npath", "arrBrown")
    s.text(26, 832, "sync copies", "wt")
    s.poly([(1100, 848), (1100, 840), (1256, 840), (1256, 190), (1226, 190)], "npath", "arrBrown")
    s.text(1244, 832, "inbox thread reads orders (remote)", "wt", "end")

    s.text(40, 990, "Arrows: grey = control / HTTP, teal = files written by Fusion, brown dashed = network. "
                    "Details in ARCHITECTURE.md and tables/DIRECTORIES.md.", "mu")
    s.save("infrastructure.svg")


def render():
    """{file name: SVG text} for every diagram."""
    RESULT.clear()
    workflow()
    infrastructure()
    return dict(RESULT)


def stale():
    """Names of the SVGs in docs/ that differ from what this script draws."""
    out = []
    for name, text in render().items():
        try:
            with open(os.path.join(DOCS, name), encoding="utf-8", newline="") as fh:
                current = fh.read().replace("\r\n", "\n")   # git may check out CRLF
        except OSError:
            current = None
        if current != text:
            out.append(name)
    return out


if __name__ == "__main__":
    if "--check" in sys.argv:
        bad = stale()
        print("out of date: %s" % ", ".join(bad) if bad else "diagrams are current")
        sys.exit(1 if bad else 0)
    for name, text in render().items():
        path = os.path.join(DOCS, name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("wrote", path)
