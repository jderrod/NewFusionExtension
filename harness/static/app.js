/* NFE Console - dashboard for the NewFusionExtension harness. No build step, no deps. */
(function () {
  "use strict";

  // ------------------------------------------------------------------ utils
  const $ = (id) => document.getElementById(id);
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (k === "dataset") Object.assign(n.dataset, v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      n.appendChild(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
    }
    return n;
  }
  const svgNS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs) {
    const n = document.createElementNS(svgNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    return n;
  }
  async function api(method, path, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
    const r = await fetch(path, opt);
    let data = null;
    try { data = await r.json(); } catch (e) { /* non-JSON */ }
    if (!r.ok) throw new Error((data && data.error) || ("HTTP " + r.status));
    return data;
  }
  let toastTimer = null;
  function toast(msg, isErr) {
    const t = $("toast");
    t.textContent = msg;
    t.className = "toast" + (isErr ? " err" : "");
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, isErr ? 7000 : 3500);
  }
  const fmtInt = (v) => (v === null || v === undefined) ? "—" : Math.round(v).toLocaleString();
  function fmtDur(s) {
    if (s === null || s === undefined || isNaN(s)) return "";
    s = Math.max(0, s);
    if (s < 60) return s.toFixed(s < 10 ? 1 : 0) + "s";
    const m = Math.floor(s / 60), r = Math.round(s % 60);
    if (m < 60) return m + "m " + String(r).padStart(2, "0") + "s";
    return Math.floor(m / 60) + "h " + String(m % 60).padStart(2, "0") + "m";
  }
  function fmtAgo(ts) {
    if (!ts) return "never";
    return fmtDur(Date.now() / 1000 - ts) + " ago";
  }
  function fmtTime(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  }
  function fmtDate(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " +
      d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  function pill(status, label) { return el("span", { class: "pill", dataset: { s: status || "" }, text: label || status || "—" }); }

  // ------------------------------------------------------------------ state
  const S = {
    view: "monitor",
    overview: null,
    events: [],
    sinceA: 0, sinceH: 0,
    folder: "done",
    selectedOrder: null,
    cfg: null,
    rules: null,          // editable copy
    rulesDirty: false,
    hubFiles: null,       // [{folderName, files:[...]}]
    logLevel: "info",
    browsePath: [],       // [{kind, id, name}]
  };

  // ------------------------------------------------------------------ tabs
  document.querySelectorAll("nav.tabs button").forEach((b) => {
    b.addEventListener("click", () => showView(b.dataset.view));
  });
  function showView(v) {
    S.view = v;
    document.querySelectorAll("nav.tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === v)));
    document.querySelectorAll("section.view").forEach((s) => { s.hidden = s.id !== "view-" + v; });
    try { localStorage.setItem("nfe.view", v); } catch (e) { /* ignore */ }
    if (v === "orders") loadOrders();
    if (v === "series") loadSeries();
    if (v === "settings") loadSettings();
    if (v === "log") renderLog();
    if (v === "docs") loadDocs();
    if (v === "logic") loadLogic();
  }

  // ------------------------------------------------------------------ polling
  async function pollOverview() {
    try {
      S.overview = await api("GET", "/api/overview?history=360");
      renderHeader();
      if (S.view === "monitor") renderMonitor();
      if (S.view === "orders" && S.folder !== "done" && S.folder !== "failed") loadOrders(true);
    } catch (e) {
      $("lamp-fusion").dataset.s = "critical";
      $("fusion-text").textContent = "harness unreachable";
    }
  }
  async function pollEvents() {
    try {
      const r = await api("GET", "/api/events?since_addin=" + S.sinceA + "&since_harness=" + S.sinceH);
      if (S.boot && r.boot !== S.boot) { S.sinceH = 0; S.boot = r.boot; return pollEvents(); }  // harness restarted
      S.boot = r.boot;
      S.sinceA = r.last_addin; S.sinceH = r.last_harness;
      if (r.events.length) {
        S.events = S.events.concat(r.events).slice(-2000);
        if (S.view === "monitor") renderShortEvents();
        if (S.view === "log") renderLog();
        if (S.view === "orders" && r.events.some((e) => /finished|Rejected/.test(e.msg))) loadOrders(true);
      }
    } catch (e) { /* next poll */ }
  }

  // ------------------------------------------------------------------ header
  function renderHeader() {
    const o = S.overview, f = o.fusion || {}, a = o.addin || {}, st = a.status || {};
    const lf = $("lamp-fusion");
    if (f.running) {
      lf.dataset.s = "good";
      $("fusion-text").textContent = "running · " + fmtInt(f.threads) + " threads · " + fmtInt(f.working_set_mb) + " MB";
    } else {
      lf.dataset.s = "critical";
      $("fusion-text").textContent = "not running";
    }
    const la = $("lamp-addin");
    if (a.online) { la.dataset.s = "good"; $("addin-text").textContent = "online"; }
    else if (f.running) { la.dataset.s = "critical"; $("addin-text").textContent = a.heartbeat_age_s ? "silent " + fmtDur(a.heartbeat_age_s) : "not loaded"; }
    else { la.dataset.s = "off"; $("addin-text").textContent = "offline"; }

    let state = a.online ? (st.state || "unknown") : "offline";
    const ls = $("lamp-state");
    const map = { processing: "good", idle: "good", paused: "warning", waiting_restart: "warning", waiting_fusion: "warning", stopped: "critical", offline: "off" };
    ls.dataset.s = map[state] || "off";
    const labels = { processing: "Processing", idle: "Idle, watching inbox", paused: "Paused", waiting_restart: "Waiting for Fusion restart", waiting_fusion: "Waiting for Fusion to sign in", stopped: "Stopped", offline: "No add-in" };
    $("state-text").textContent = labels[state] || state;
    if (o.supervisor && o.supervisor.restart_in_progress) { ls.dataset.s = "warning"; $("state-text").textContent = "Restarting Fusion…"; }

    const paused = !!st.paused;
    $("btn-pause").hidden = paused || !a.online;
    $("btn-resume").hidden = !paused || !a.online;
    $("btn-restart").textContent = f.running ? "Restart Fusion" : "Start Fusion";
    const mode = (o.location || {}).mode || "local";
    const mb = $("mode-badge");
    mb.textContent = mode === "remote" ? "REMOTE" : "LOCAL";
    mb.dataset.m = mode;
    mb.title = "Orders are read from " + ((o.location || {}).inbox || "?");
  }

  // ------------------------------------------------------------------ monitor
  const STATIONS = [
    ["open_model", "Open model"], ["apply_parameters", "Apply parameters"], ["save_model", "Save model"],
    ["open_drawing", "Open drawing"], ["update_drawing", "Update drawing"], ["export_pdf", "Export PDF"],
    ["cleanup", "Close & clean up"],
  ];

  function renderMonitor() {
    renderTraveler();
    renderCounters();
    renderCharts();
    renderSupervisor();
  }

  function renderTraveler() {
    const box = $("traveler");
    const st = (S.overview.addin || {}).status || {};
    const online = (S.overview.addin || {}).online;
    const order = online ? st.order : null;
    box.replaceChildren();
    if (!order) {
      const last = st.last_order;
      const note = el("div", { class: "idle-note" },
        el("div", { class: "eyebrow", text: online ? "No order in progress" : "Add-in not reachable" }),
        el("div", { class: "order-id", text: online ? (st.paused ? "Paused" : "Watching the inbox") : "Waiting for Fusion" }),
        el("div", { class: "comp-line muted", text: online
          ? (st.paused ? (st.pause_reason || "Resume to pick up orders.") : "Drop an order JSON into the inbox or use the Orders tab.")
          : "Start Fusion (the add-in loads with it) or check Utilities › Add-Ins." }));
      if (last) {
        note.appendChild(el("div", { class: "row", style: "margin-top:12px" },
          el("span", { class: "muted", text: "Last order" }),
          el("b", { class: "mono", text: last.order_id || last.name }),
          pill(last.status),
          el("span", { class: "muted", text: (last.total ? last.done + "/" + last.total + " components · " : "") + fmtAgo(last.finished_at) }),
          last.folder ? el("button", { class: "btn small", type: "button", text: "Open report",
            onclick: () => { S.folder = last.folder; S.selectedOrder = last.name; showView("orders"); } }) : null));
      }
      box.appendChild(note);
      return;
    }
    const comp = st.component || null;
    const head = el("div", { class: "traveler-head" },
      el("div", null,
        el("div", { class: "eyebrow", text: "Order in progress · " + order.name }),
        el("div", { class: "order-id", text: order.order_id }),
        comp ? el("div", { class: "comp-line" },
          el("b", { text: comp.id }), " ", comp.type,
          comp.series_id ? " · series " + comp.series_id : "",
          comp.model ? " → " + comp.model : "",
          comp.drawing ? " / " + comp.drawing : "") : el("div", { class: "comp-line muted", text: "Between components" })),
      el("div", { class: "count" },
        el("div", { class: "big" }, String(order.done + order.failed), el("span", { text: " / " + order.total })),
        el("div", { class: "muted", text: (order.failed ? order.failed + " failed · " : "") + "started " + fmtAgo(order.started_at) })));
    box.appendChild(head);

    const steps = (comp && comp.steps) || [];
    const names = STATIONS.slice();
    if (steps.some((s) => s.name === "save_drawing")) names.splice(6, 0, ["save_drawing", "Save drawing"]);
    const activeIdx = comp ? names.findIndex((n) => n[0] === comp.step) : -1;
    const durations = {};
    steps.forEach((s) => { if (s.duration_s !== null && s.duration_s !== undefined) durations[s.name] = s.duration_s; });
    const ol = el("ol", { class: "stations", "aria-label": "Steps for the current component" });
    ol.style.gridTemplateColumns = "repeat(" + names.length + ", minmax(92px, 1fr))";
    names.forEach(([key, label], i) => {
      let cls = "station";
      let t = "";
      if (activeIdx >= 0 && i < activeIdx) {
        cls += " done";
        if (durations[key] !== undefined) t = fmtDur(durations[key]);
        else if (steps.length) t = "skipped";
      }
      if (i === activeIdx) { cls += " active"; t = fmtDur(Date.now() / 1000 - comp.step_started_at); }
      ol.appendChild(el("li", { class: cls, "aria-current": i === activeIdx ? "step" : null },
        el("span", { class: "dot", text: i < activeIdx ? "✓" : String(i + 1) }),
        el("div", { class: "name", text: label }),
        el("div", { class: "t", text: t })));
    });
    box.appendChild(ol);
    let waitText = "";
    if (comp && comp.waiting_for && comp.step !== "cleanup") waitText = "Waiting for " + comp.waiting_for + "…";
    if (st.ui_blocked_s > 5) waitText = "Fusion is busy inside " + (comp ? comp.step.replace(/_/g, " ") : "a step") + " for " + fmtDur(st.ui_blocked_s);
    box.appendChild(el("div", { class: "waiting", text: waitText }));
    const chips = el("div", { class: "chips", "aria-label": "Components in this order" });
    (order.components || []).forEach((c) => chips.appendChild(el("span", {
      class: "chip", dataset: { s: c.status }, title: c.type + " · " + c.status, text: c.id })));
    box.appendChild(chips);
  }

  function renderCounters() {
    const o = S.overview, q = o.queue || {}, c = ((o.addin || {}).status || {}).counters || {};
    const box = $("counters");
    box.replaceChildren(
      counter("Inbox", q.inbox, "waiting"),
      counter("Processing", q.processing),
      counter("Done", q.done, "orders"),
      counter("Failed", q.failed, "orders"),
      counter("Components", c.components_done, "made this session"),
      counter("Component errors", c.components_failed, "this session"));
  }
  function counter(k, v, small) {
    return el("div", { class: "counter" }, el("div", { class: "k", text: k }),
      el("div", { class: "v" }, fmtInt(v || 0), small ? el("small", { text: small }) : null));
  }

  function renderCharts() {
    const h = S.overview.history || [], th = S.overview.thresholds || {};
    chart($("chart-threads"), "Fusion threads", "", h.map((p) => [p.t, p.threads]), th.max_threads, fmtInt);
    chart($("chart-mem"), "Fusion memory", "MB", h.map((p) => [p.t, p.mb]), th.max_working_set_mb, fmtInt);
    chart($("chart-docs"), "Open documents", "", h.map((p) => [p.t, p.docs]), th.max_open_documents, fmtInt);
  }

  function chart(card, title, unit, raw, limit, fmt) {
    const pts = raw.filter((p) => p[1] !== null && p[1] !== undefined);
    const last = pts.length ? pts[pts.length - 1][1] : null;
    card.replaceChildren();
    card.appendChild(el("div", { class: "chart-head" },
      el("h2", { style: "margin:0", text: title }),
      el("div", { class: "now" }, last === null ? "—" : fmt(last), el("small", { text: unit ? " " + unit : "" }))));
    const wrap = el("div", { class: "chart" });
    card.appendChild(wrap);
    const note = el("div", { class: "muted", style: "font-size:12px" });
    card.appendChild(note);
    const W = Math.max(wrap.clientWidth || 300, 200), H = 140, L = 44, R = 8, T = 8, B = 20;
    const now = Date.now() / 1000, span = 30 * 60, t0 = now - span;
    const vis = pts.filter((p) => p[0] >= t0);
    const dmax = vis.length ? Math.max(...vis.map((p) => p[1])) : 1;
    let ymax = niceMax(Math.max(dmax * 1.2, 1));
    const limitShown = limit && limit <= ymax * 1.6;
    if (limitShown) ymax = niceMax(Math.max(ymax, limit * 1.08));
    note.textContent = limit ? (limitShown ? "" : "Restart limit " + fmt(limit) + (unit ? " " + unit : "") + " is off the scale") : "";
    const x = (t) => L + (t - t0) / span * (W - L - R);
    const y = (v) => T + (1 - v / ymax) * (H - T - B);
    const s = svg("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": title + " over the last 30 minutes" });
    const g = svg("g", { class: "grid" }), ax = svg("g", { class: "axis" });
    [0, .5, 1].forEach((f) => {
      const v = ymax * f;
      g.appendChild(svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v) }));
      const tx = svg("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end" }); tx.textContent = fmt(v); ax.appendChild(tx);
    });
    [[t0, "-30m"], [now - 15 * 60, "-15m"], [now, "now"]].forEach(([t, lab], i) => {
      const tx = svg("text", { x: x(t), y: H - 4, "text-anchor": i === 0 ? "start" : i === 2 ? "end" : "middle" });
      tx.textContent = lab; ax.appendChild(tx);
    });
    s.appendChild(g); s.appendChild(ax);
    if (limitShown) {
      s.appendChild(svg("line", { class: "limit", x1: L, x2: W - R, y1: y(limit), y2: y(limit) }));
      const lt = svg("text", { class: "limit-label", x: W - R, y: y(limit) - 5, "text-anchor": "end" });
      lt.textContent = "restart at " + fmt(limit); s.appendChild(lt);
    }
    if (vis.length > 1) {
      // break the line where samples are missing (> 3 intervals apart)
      let d = "", prev = null;
      vis.forEach((p) => {
        const gap = prev && p[0] - prev[0] > 20;
        d += (d === "" || gap ? "M" : "L") + x(p[0]).toFixed(1) + " " + y(p[1]).toFixed(1);
        prev = p;
      });
      s.appendChild(svg("path", { class: "line", d }));
    } else if (vis.length === 1) {
      s.appendChild(svg("circle", { class: "hover-dot", cx: x(vis[0][0]), cy: y(vis[0][1]), r: 4 }));
    }
    const xh = svg("line", { class: "xhair", y1: T, y2: H - B, visibility: "hidden" });
    const hd = svg("circle", { class: "hover-dot", r: 4, visibility: "hidden" });
    s.appendChild(xh); s.appendChild(hd);
    wrap.appendChild(s);
    const tip = el("div", { class: "tip", hidden: true });
    wrap.appendChild(tip);
    if (!vis.length) {
      wrap.appendChild(el("div", { class: "empty", style: "position:absolute;inset:40px 0 0 50px", text: "No samples yet" }));
      return;
    }
    function hover(ev) {
      const r = s.getBoundingClientRect();
      const px = (ev.clientX - r.left) / r.width * W;
      const t = t0 + (px - L) / (W - L - R) * span;
      let best = vis[0];
      for (const p of vis) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
      xh.setAttribute("x1", x(best[0])); xh.setAttribute("x2", x(best[0])); xh.setAttribute("visibility", "visible");
      hd.setAttribute("cx", x(best[0])); hd.setAttribute("cy", y(best[1])); hd.setAttribute("visibility", "visible");
      tip.replaceChildren(el("span", { class: "k" }), el("strong", { text: fmt(best[1]) + (unit ? " " + unit : "") }),
        el("span", { class: "muted", text: "  " + fmtTime(best[0]) }));
      tip.hidden = false;
      const left = x(best[0]) / W * r.width;
      tip.style.left = Math.min(Math.max(left - 60, 0), r.width - 150) + "px";
      tip.style.top = "-6px";
    }
    s.addEventListener("pointermove", hover);
    s.addEventListener("pointerleave", () => { tip.hidden = true; xh.setAttribute("visibility", "hidden"); hd.setAttribute("visibility", "hidden"); });
  }
  function niceMax(v) {
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }

  function renderSupervisor() {
    const o = S.overview, f = o.fusion || {}, sup = o.supervisor || {}, st = (o.addin || {}).status || {};
    const dl = $("supervisor");
    const rows = [
      ["Fusion process", f.running ? "pid " + f.pid + " · up " + fmtAgo(f.started_at).replace(" ago", "") : "not running"],
      ["Handles", fmtInt(f.handles)],
      ["Add-in heartbeat", (o.addin || {}).heartbeat_age_s !== null && (o.addin || {}).heartbeat_age_s !== undefined ? fmtDur(o.addin.heartbeat_age_s) + " ago" : "—"],
      ["Models kept open", st.cached_models !== undefined ? String(st.cached_models) : "—"],
      ["Restart request", st.restart_requested ? (st.restart_reason || "yes") : "none"],
    ];
    const rs = sup.restarts || [];
    rows.push(["Fusion restarts", rs.length ? rs.slice(-3).reverse().map((r) => fmtDate(r.at) + " — " + r.reason + " (" + r.result + ")").join("\n") : "none since the harness started"]);
    dl.replaceChildren(...rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v, style: "white-space:pre-line" })]));
    $("auto-restart").checked = !!sup.auto_restart;
  }

  // ------------------------------------------------------------------ events
  function eventRow(e) {
    return el("div", { class: "ev", dataset: { l: e.level } },
      el("span", { class: "ts", text: fmtTime(e.ts) }),
      el("span", { class: "lv", text: e.level }),
      el("span", { class: "ev-msg" }, e.src === "harness" ? el("span", { class: "src", text: "harness" }) : null, e.msg));
  }
  function renderShortEvents() {
    const box = $("events-short");
    const list = S.events.filter((e) => e.level !== "debug").slice(-14).reverse();
    box.replaceChildren(...(list.length ? list.map(eventRow) : [el("div", { class: "empty", text: "Nothing has happened yet." })]));
  }
  function renderLog() {
    const box = $("events-full");
    const order = { debug: 0, info: 1, warn: 2, error: 3 };
    const min = order[S.logLevel];
    const list = S.events.filter((e) => (order[e.level] || 0) >= min).slice(-800).reverse();
    box.replaceChildren(...(list.length ? list.map(eventRow) : [el("div", { class: "empty", text: "No events at this level." })]));
  }
  document.querySelectorAll("#level-seg button").forEach((b) => b.addEventListener("click", () => {
    S.logLevel = b.dataset.l;
    document.querySelectorAll("#level-seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    renderLog();
  }));

  // ------------------------------------------------------------------ controls
  async function control(action, extra) {
    try { await api("POST", "/api/control", Object.assign({ action }, extra || {})); pollOverview(); return true; }
    catch (e) { toast(e.message, true); return false; }
  }
  $("btn-pause").addEventListener("click", async () => { if (await control("pause")) toast("Paused. The current component finishes first."); });
  $("btn-resume").addEventListener("click", async () => { if (await control("resume")) toast("Resumed"); });
  let restartArmed = null;
  $("btn-restart").addEventListener("click", async (ev) => {
    const running = S.overview && S.overview.fusion && S.overview.fusion.running;
    if (!running) { if (await control("start_fusion")) toast("Starting Fusion…"); return; }
    const b = ev.currentTarget;
    if (!restartArmed) {           // two-step confirm instead of confirm()
      b.textContent = "Click again to restart";
      b.classList.add("danger");
      restartArmed = setTimeout(() => { restartArmed = null; b.classList.remove("danger"); renderHeader(); }, 4000);
      return;
    }
    clearTimeout(restartArmed); restartArmed = null; b.classList.remove("danger");
    if (await control("restart_fusion", { reason: "restart from dashboard" })) toast("Restarting Fusion. The current order resumes afterwards.");
  });
  $("btn-gc").addEventListener("click", async () => { if (await control("gc")) toast("Closing leftover documents"); });
  $("btn-cancel").addEventListener("click", async () => { if (await control("cancel")) toast("The order stops after the current component"); });
  $("auto-restart").addEventListener("change", async (e) => {
    if (await control("auto_restart", { enabled: e.target.checked })) toast(e.target.checked ? "Fusion restarts automatically" : "Automatic restarts off");
  });

  // ------------------------------------------------------------------ orders
  document.querySelectorAll("#folder-seg button").forEach((b) => b.addEventListener("click", () => {
    S.folder = b.dataset.f; S.selectedOrder = null; $("report").hidden = true; loadOrders();
  }));

  async function loadOrders(quiet) {
    document.querySelectorAll("#folder-seg button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.f === S.folder)));
    let data;
    try { data = await api("GET", "/api/orders?folder=" + S.folder); }
    catch (e) { if (!quiet) toast(e.message, true); return; }
    const t = $("orders-table");
    const rows = data.orders;
    if (!rows.length) {
      const msgs = { inbox: "The inbox is empty.", processing: "Nothing is being processed.", done: "No finished orders yet.", failed: "No failed orders." };
      t.replaceChildren(el("tbody", null, el("tr", null, el("td", { class: "empty", text: msgs[S.folder] }))));
      return;
    }
    const head = el("thead", null, el("tr", null, el("th", { text: "File" }), el("th", { text: "When" }),
      el("th", { text: S.folder === "inbox" ? "Size" : "Result" }), el("th", { text: "" })));
    const body = el("tbody");
    rows.forEach((o) => {
      const sm = o.summary || {};
      let result, action = null;
      if (S.folder === "inbox") {
        result = el("span", { class: "mono", text: (o.size / 1024).toFixed(1) + " KB" });
        action = el("button", { class: "btn small", type: "button", text: "Remove", onclick: async (ev) => {
          ev.stopPropagation();
          try { await api("POST", "/api/orders/remove", { name: o.name }); toast("Removed " + o.name); loadOrders(); } catch (e) { toast(e.message, true); }
        } });
      } else if (S.folder === "processing") {
        result = pill("processing", "in progress");
      } else {
        result = el("span", { class: "row" }, pill(sm.status), sm.total ? el("span", { class: "muted", text: sm.done + "/" + sm.total }) : null);
        action = el("button", { class: "btn small", type: "button", text: "Run again", onclick: async (ev) => {
          ev.stopPropagation();
          try { const r = await api("POST", "/api/orders/requeue", { folder: S.folder, name: o.name }); toast("Queued " + r.name); loadOrders(); } catch (e) { toast(e.message, true); }
        } });
      }
      const tr = el("tr", { class: "clickable" + (S.selectedOrder === o.name ? " sel" : ""), tabindex: "0" },
        el("td", { class: "mono", text: o.name }), el("td", { text: fmtDate(o.mtime) }), el("td", null, result), el("td", null, action));
      const open = () => { S.selectedOrder = o.name; loadOrders(true); showReport(S.folder, o.name); };
      if (S.folder !== "inbox") {
        tr.addEventListener("click", open);
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      }
      body.appendChild(tr);
    });
    t.replaceChildren(head, body);
    if (S.selectedOrder && !quiet && S.folder !== "inbox") showReport(S.folder, S.selectedOrder);
  }

  async function showReport(folder, name) {
    const box = $("report");
    let rep;
    try { rep = await api("GET", "/api/orders/report?folder=" + folder + "&name=" + encodeURIComponent(name)); }
    catch (e) { toast(e.message, true); return; }
    box.hidden = false;
    if (folder === "processing") {
      box.replaceChildren(el("h2", { text: name }), el("p", { class: "muted", text: "Being processed now. Progress is on the Monitor tab." }));
      return;
    }
    const sm = rep.summary || {};
    const head = el("div", { class: "row", style: "justify-content:space-between;margin-bottom:10px" },
      el("div", null,
        el("div", { class: "muted", text: rep.source_file || name }),
        el("div", { class: "row" }, el("span", { style: "font-family:var(--display);font-size:30px;font-weight:700", text: rep.order_id || name }), pill(sm.status))),
      el("dl", { class: "kv" },
        el("dt", { text: "Components" }), el("dd", { text: (sm.done || 0) + " made, " + (sm.failed || 0) + " failed, " + (sm.warnings || 0) + " warnings" }),
        el("dt", { text: "Took" }), el("dd", { text: fmtDur(rep.duration_s) + (rep.finished_at ? " · finished " + fmtDate(rep.finished_at) : "") }),
        el("dt", { text: "PDF folder" }), el("dd", { class: "mono", text: "output/" + (sm.output_dir || "") })));
    box.replaceChildren(head);
    if (rep.error) box.appendChild(el("div", { class: "errline", text: rep.error }));
    (rep.components || []).forEach((c) => box.appendChild(componentDetails(c)));
  }

  function componentDetails(c) {
    const total = (c.steps || []).reduce((a, s) => a + (s.duration_s || 0), 0) || 1;
    const bar = el("span", { class: "steps-bar", title: (c.steps || []).map((s) => s.name + " " + fmtDur(s.duration_s)).join(" · ") },
      ...(c.steps || []).map((s) => { const sp = el("span"); sp.style.flex = String(Math.max(s.duration_s || 0, total * 0.01)); return sp; }));
    const summary = el("summary", null,
      el("span", { class: "id", text: c.id }), el("span", { class: "muted", text: c.type }), pill(c.status),
      el("span", { text: c.model || "no model" }),
      el("span", { class: "muted", text: fmtDur(c.duration_s) }), bar,
      c.pdf ? el("a", { href: "/files/output/" + c.pdf.split("/").map(encodeURIComponent).join("/"), target: "_blank", rel: "noopener", text: "PDF", onclick: (e) => e.stopPropagation() }) : null,
      (c.warnings || []).length ? el("span", { class: "pill", dataset: { s: "warn" }, text: c.warnings.length + " warning" + (c.warnings.length > 1 ? "s" : "") }) : null);
    const body = el("div", { style: "padding:8px 0 4px 44px" });
    if (c.error) body.appendChild(el("div", { class: "errline", text: c.error }));
    if ((c.warnings || []).length) body.appendChild(el("ul", { class: "warnlist" }, ...c.warnings.map((w) => el("li", { text: w }))));
    const kv = el("dl", { class: "kv", style: "margin:8px 0" },
      el("dt", { text: "Series" }), el("dd", { class: "mono", text: c.series_id || "—" }),
      el("dt", { text: "Rule" }), el("dd", { class: "mono", text: c.rule_id || "—" }),
      el("dt", { text: "Drawing" }), el("dd", { text: c.drawing || "—" }),
      el("dt", { text: "Saved model version" }), el("dd", { class: "mono", text: c.model_version ? "v" + c.model_version : "—" }),
      el("dt", { text: "Steps" }), el("dd", { text: (c.steps || []).map((s) => s.name.replace(/_/g, " ") + " " + fmtDur(s.duration_s)).join(" · ") }));
    const refs = c.drawing_references;
    if (refs && refs.after) {
      kv.appendChild(el("dt", { text: "Drawing reference" }));
      kv.appendChild(el("dd", { class: "mono", text: refs.after.map((r, i) => r.name + ": v" + ((refs.before[i] || {}).file_version || "?") + " → v" + r.file_version).join(", ") }));
    }
    body.appendChild(kv);
    if ((c.parameters || []).length) {
      const t = el("table", { class: "params" }, el("thead", null, el("tr", null,
        el("th", { text: "Parameter" }), el("th", { text: "Was" }), el("th", { text: "Now" }), el("th", { text: "" }))));
      const tb = el("tbody");
      c.parameters.forEach((p) => tb.appendChild(el("tr", null,
        el("td", { class: "mono", text: p.name }), el("td", { class: "mono muted", text: p.before || "" }),
        el("td", { class: "mono", text: p.after || p.expression || "" }),
        el("td", null, p.ok ? el("span", { class: "muted", text: "ok" }) : el("span", { class: "errline", text: p.message || "failed" })))));
      t.appendChild(tb);
      body.appendChild(el("div", { class: "table-wrap" }, t));
    }
    if ((c.unknown_parameters || []).length) body.appendChild(el("p", { class: "muted", style: "font-size:13px", text: "Not in the model: " + c.unknown_parameters.join(", ") }));
    return el("details", { class: "comp" }, summary, body);
  }

  // upload
  const drop = $("drop");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); handleFiles(e.dataTransfer.files); });
  $("file").addEventListener("change", (e) => { handleFiles(e.target.files); e.target.value = ""; });
  async function handleFiles(files) {
    const box = $("preview");
    box.replaceChildren();
    for (const f of Array.from(files)) {
      const text = await f.text();
      const block = el("div", { class: "card", style: "padding:12px;margin-bottom:10px;background:var(--panel-2)" });
      box.appendChild(block);
      try {
        const p = await api("POST", "/api/orders/preview", { content: text });
        const missing = p.components.filter((c) => !c.rule_id).length;
        const t = el("table", { class: "params" }, el("thead", null, el("tr", null,
          el("th", { text: "ID" }), el("th", { text: "Series" }), el("th", { text: "Model" }), el("th", { text: "Drawing" }))));
        const tb = el("tbody");
        p.components.forEach((c) => tb.appendChild(el("tr", null,
          el("td", { class: "mono", text: c.component_id }), el("td", { class: "mono", text: c.series_id || "—" }),
          el("td", null, c.model ? c.model : el("span", { class: "errline", text: "no matching rule" })),
          el("td", { text: c.drawing || "—" }))));
        t.appendChild(tb);
        const add = el("button", { class: "btn primary", type: "button", text: "Add to inbox", onclick: async () => {
          try { const r = await api("POST", "/api/orders/upload", { name: f.name, content: text }); toast("Added " + r.name + " to the inbox"); add.disabled = true; add.textContent = "Added"; }
          catch (e) { toast(e.message, true); }
        } });
        block.append(
          el("div", { class: "row", style: "justify-content:space-between" },
            el("div", null, el("b", { class: "mono", text: p.order_id }), " ",
              el("span", { class: "muted", text: Object.entries(p.counts).map(([k, v]) => v + " " + k + (v > 1 ? "s" : "")).join(", ") })),
            add),
          missing ? el("div", { class: "errline", text: missing + " component(s) have no series rule and will fail. Add a rule under Series & drawings first." }) : null,
          el("div", { class: "table-wrap", style: "margin-top:8px" }, t));
      } catch (e) {
        block.append(el("b", { class: "mono", text: f.name }), el("div", { class: "errline", text: "Not accepted: " + e.message }));
      }
    }
  }

  // ------------------------------------------------------------------ series
  async function loadSeries(force) {
    try {
      if (!S.cfg || force || !S.rulesDirty) {
        S.cfg = await api("GET", "/api/config");
        S.rules = JSON.parse(JSON.stringify(S.cfg.rules || []));
        S.rulesDirty = false;
      }
    } catch (e) { toast(e.message, true); return; }
    renderRules();
    renderFamilyFolders();
    if (!S.hubFiles || force) loadHubFiles();
    if (!S.browsePath.length) browse([]);
  }

  async function loadHubFiles() {
    const folders = Object.entries((S.cfg.hub || {}).family_folders || {}).filter(([, v]) => v && (v.id || typeof v === "string"));
    const out = [];
    for (const [fam, v] of folders) {
      const id = typeof v === "string" ? v : v.id;
      try {
        const r = await api("GET", "/api/hub/folder?id=" + encodeURIComponent(id));
        out.push({ family: fam, folderName: r.name, files: r.files.map((f) => Object.assign(f, { path: (r.project || "") + "/" + r.name })) });
      } catch (e) {
        out.push({ family: fam, folderName: fam + " (unavailable: " + e.message + ")", files: [] });
      }
    }
    S.hubFiles = out;
    renderRules();
  }

  function fileOptions(ext, current) {
    const sel = [];
    const seen = new Set();
    (S.hubFiles || []).forEach((grp) => {
      const og = el("optgroup", { label: grp.folderName });
      grp.files.filter((f) => f.ext === ext).forEach((f) => {
        seen.add(f.id);
        og.appendChild(el("option", { value: f.id, text: f.name + " (v" + f.version + ")", selected: current && current.id === f.id }));
      });
      if (og.children.length) sel.push(og);
    });
    if (current && current.id && !seen.has(current.id)) {
      sel.unshift(el("option", { value: current.id, text: (current.name || current.id) + (S.hubFiles ? " (outside family folders)" : ""), selected: true }));
    }
    sel.unshift(el("option", { value: "", text: ext === "f3d" ? "Choose a model…" : "No drawing" , selected: !current || !current.id }));
    return sel;
  }
  function findFile(id) {
    for (const g of S.hubFiles || []) for (const f of g.files) if (f.id === id) return f;
    return null;
  }
  function markDirty() { S.rulesDirty = true; $("rules-dirty").textContent = "Unsaved changes"; resolve(); }

  function renderRules() {
    const t = $("rules-table");
    const head = el("thead", null, el("tr", null, ...["On", "Type", "Family", "Series pattern", "Model", "Drawing", ""].map((h) => el("th", { text: h }))));
    const body = el("tbody");
    if (!S.rules.length) body.appendChild(el("tr", null, el("td", { colspan: "7", class: "empty", text: "No rules yet. Use “Suggest rules from hub” to start from the models in the family folders." })));
    S.rules.forEach((r, i) => {
      const on = el("input", { type: "checkbox", checked: r.enabled !== false, "aria-label": "Rule enabled", onchange: (e) => { r.enabled = e.target.checked; markDirty(); renderRules(); } });
      const type = el("select", { "aria-label": "Component type", onchange: (e) => { r.type = e.target.value; markDirty(); } },
        ...["door", "stile", "panel", "*"].map((v) => el("option", { value: v, text: v === "*" ? "any" : v, selected: r.type === v })));
      const fam = el("select", { "aria-label": "Family", onchange: (e) => { r.family = e.target.value; markDirty(); } },
        ...["", "2X8X", "3X8X"].concat(r.family && !["2X8X", "3X8X"].includes(r.family) ? [r.family] : []).map((v) =>
          el("option", { value: v, text: v || "—", selected: (r.family || "") === v })));
      const series = el("input", { type: "text", value: r.series || "", placeholder: "family", "aria-label": "Series pattern",
        title: "Glob matched against series_id: ? = one character, * = anything, [12] = 1 or 2. Blank = whole family.",
        oninput: (e) => { r.series = e.target.value.trim(); markDirty(); } });
      const model = el("select", { "aria-label": "Model", onchange: (e) => {
        const f = findFile(e.target.value);
        r.model = f ? { id: f.id, name: f.name, folder_id: f.folder_id, path: f.path } : {};
        markDirty();
      } }, ...fileOptions("f3d", r.model));
      const drawing = el("select", { "aria-label": "Drawing", onchange: (e) => {
        const f = findFile(e.target.value);
        r.drawing = f ? { id: f.id, name: f.name, folder_id: f.folder_id, path: f.path } : {};
        markDirty();
      } }, ...fileOptions("f2d", r.drawing));
      const acts = el("div", { class: "row" },
        el("button", { class: "btn small", type: "button", text: "Parameters", disabled: !(r.model && r.model.id), onclick: () => inspectModel(r.model) }),
        el("button", { class: "btn small danger", type: "button", text: "Delete", onclick: () => { S.rules.splice(i, 1); markDirty(); renderRules(); } }));
      body.appendChild(el("tr", { class: r.enabled === false ? "disabled" : "" },
        el("td", null, on), el("td", null, type), el("td", null, fam), el("td", null, series),
        el("td", null, model), el("td", null, drawing), el("td", null, acts)));
    });
    t.replaceChildren(head, body);
    $("rules-dirty").textContent = S.rulesDirty ? "Unsaved changes" : "";
  }

  $("btn-add-rule").addEventListener("click", () => {
    S.rules.push({ id: "rule-" + Date.now().toString(36), enabled: true, type: "door", family: "3X8X", series: "", model: {}, drawing: {} });
    markDirty(); renderRules();
  });
  $("btn-refresh-files").addEventListener("click", () => { S.hubFiles = null; loadHubFiles(); toast("Reading hub folders…"); });
  $("btn-save-rules").addEventListener("click", async () => {
    const cfg = Object.assign({}, S.cfg, { rules: S.rules });
    try {
      S.cfg = await api("PUT", "/api/config", cfg);
      S.rules = JSON.parse(JSON.stringify(S.cfg.rules)); S.rulesDirty = false;
      renderRules(); toast("Saved. New orders use these rules.");
    } catch (e) { toast("Not saved: " + e.message, true); }
  });
  $("btn-suggest").addEventListener("click", async (ev) => {
    const b = ev.currentTarget; b.disabled = true; b.textContent = "Reading hub…";
    const box = $("suggestions");
    try {
      const r = await api("POST", "/api/config/suggest", {});
      box.replaceChildren();
      if (!r.suggestions.length) { box.appendChild(el("p", { class: "muted", text: "Every model in the family folders (" + r.scanned_files + " files) already has a rule." })); return; }
      const addAll = el("button", { class: "btn small primary", type: "button", text: "Add all " + r.suggestions.length, onclick: () => {
        r.suggestions.forEach((s) => S.rules.push(s)); markDirty(); renderRules(); box.replaceChildren();
      } });
      box.append(el("div", { class: "row" }, el("b", { text: "Suggested from the hub" }), addAll));
      r.suggestions.forEach((s) => box.appendChild(el("div", { class: "row", style: "padding:4px 0" },
        el("button", { class: "btn small", type: "button", text: "Add", onclick: (e) => { S.rules.push(s); markDirty(); renderRules(); e.target.closest(".row").remove(); } }),
        el("span", { class: "mono", text: s.type + " · " + (s.series || s.family) }), "→ " + s.model.name + " / " + (s.drawing.name || "no drawing"))));
    } catch (e) { toast(e.message, true); }
    finally { b.disabled = false; b.textContent = "Suggest rules from hub"; }
  });

  // resolver (mirrors nfe/rules.py)
  function globToRe(p) {
    let re = "";
    for (let i = 0; i < p.length; i++) {
      const ch = p[i];
      if (ch === "*") re += ".*";
      else if (ch === "?") re += ".";
      else if (ch === "[") { const j = p.indexOf("]", i + 1); if (j > i) { re += "[" + p.slice(i + 1, j).replace(/\\/g, "\\\\") + "]"; i = j; } else re += "\\["; }
      else re += ch.replace(/[.+^${}()|\\\]]/g, "\\$&");
    }
    return new RegExp("^" + re + "$");
  }
  function familyOf(s) { const m = /(\d)(\d)(\d)(\d)/.exec(s || ""); return m ? m[1] + "X" + m[3] + "X" : ""; }
  function resolveRule(type, series) {
    let best = null, bestScore = null;
    for (const r of S.rules || []) {
      if (r.enabled === false) continue;
      const rt = r.type || "*";
      if (rt !== "*" && rt !== type) continue;
      let lit;
      const pat = (r.series || "").trim();
      if (pat) {
        if (!series || !globToRe(pat.toUpperCase()).test(series.toUpperCase())) continue;
        lit = 100 + [...pat].filter((c) => !"*?[]".includes(c)).length;
      } else {
        if (!r.family || familyOf(series) !== r.family.toUpperCase()) continue;
        lit = 50;
      }
      const score = [rt === type ? 1 : 0, lit];
      if (!bestScore || score[0] > bestScore[0] || (score[0] === bestScore[0] && score[1] > bestScore[1])) { best = r; bestScore = score; }
    }
    return best;
  }
  function resolve() {
    const out = $("res-out");
    const series = $("res-series").value.trim();
    if (!series) { out.textContent = ""; return; }
    const r = resolveRule($("res-type").value, series);
    out.replaceChildren(r ? el("span", null, "uses ", el("b", { text: (r.model || {}).name || "(no model)" }), " + ", el("b", { text: (r.drawing || {}).name || "no drawing" }), el("span", { class: "muted", text: "  rule " + r.id }))
      : el("span", { class: "errline", text: "No rule matches. Components like this would fail." }));
  }
  $("res-series").addEventListener("input", resolve);
  $("res-type").addEventListener("change", resolve);

  async function inspectModel(model) {
    const box = $("model-params");
    box.replaceChildren(el("p", { class: "muted", text: "Opening " + model.name + " in Fusion to read its parameters…" }));
    try {
      const r = await api("GET", "/api/model/params?id=" + encodeURIComponent(model.id));
      const user = r.parameters.filter((p) => p.user);
      const t = el("table", { class: "params" }, el("thead", null, el("tr", null, el("th", { text: "Parameter" }), el("th", { text: "Unit" }), el("th", { text: "Expression" }))));
      const tb = el("tbody");
      user.forEach((p) => tb.appendChild(el("tr", null, el("td", { class: "mono", text: p.name }), el("td", { class: "muted", text: p.unit || (p.is_text ? "text" : "—") }), el("td", { class: "mono", text: p.expression }))));
      t.appendChild(tb);
      box.replaceChildren(el("div", { class: "row", style: "justify-content:space-between" },
        el("b", { text: r.name + " v" + r.version }), el("span", { class: "muted", text: user.length + " user parameters (order JSON keys must match these names)" })),
        el("div", { class: "drawer-params table-wrap" }, t));
    } catch (e) { box.replaceChildren(el("div", { class: "errline", text: e.message })); }
  }

  // family folders + hub browser
  function renderFamilyFolders() {
    const ff = (S.cfg.hub || {}).family_folders || {};
    const dl = $("family-folders");
    dl.replaceChildren(...Object.entries(ff).flatMap(([fam, v]) => [el("dt", { text: fam }),
      el("dd", { text: (v && (v.name || v.id)) ? (v.name || v.id) : "not set — pick a folder below" })]));
  }
  async function setFamilyFolder(fam, folder) {
    const cfg = JSON.parse(JSON.stringify(S.cfg));
    cfg.hub.family_folders = Object.assign({}, cfg.hub.family_folders, { [fam]: { id: folder.id, name: folder.name } });
    try {
      S.cfg = await api("PUT", "/api/config", Object.assign(cfg, { rules: S.rulesDirty ? S.cfg.rules : cfg.rules }));
      renderFamilyFolders(); toast(fam + " models will be listed from " + folder.name); S.hubFiles = null; loadHubFiles();
    } catch (e) { toast(e.message, true); }
  }
  async function browse(path) {
    S.browsePath = path;
    const box = $("browser"), crumbs = $("crumbs");
    crumbs.replaceChildren(el("button", { class: "btn small", type: "button", text: "Hubs", onclick: () => browse([]) }),
      ...path.map((p, i) => el("span", null, " › ", el("button", { class: "btn small", type: "button", text: p.name, onclick: () => browse(path.slice(0, i + 1)) }))));
    box.replaceChildren(el("div", { class: "empty", text: "Loading…" }));
    try {
      const last = path[path.length - 1];
      let items = [];
      if (!last) {
        const r = await api("GET", "/api/hub/hubs");
        items = r.hubs.map((h) => ({ name: h.name + (h.active ? " (active)" : ""), open: () => browse([{ kind: "hub", id: h.id, name: h.name }]) }));
      } else if (last.kind === "hub") {
        const r = await api("GET", "/api/hub/projects?hub=" + encodeURIComponent(last.id));
        items = r.projects.map((p) => ({ name: p.name, open: () => browse(path.concat([{ kind: "folder", id: p.root_folder_id, name: p.name }])) }));
      } else {
        const r = await api("GET", "/api/hub/folder?id=" + encodeURIComponent(last.id));
        items = r.folders.map((f) => ({ name: f.name + "/", folder: f, open: () => browse(path.concat([{ kind: "folder", id: f.id, name: f.name }])) }));
        const nf3d = r.files.filter((f) => f.ext === "f3d").length, nf2d = r.files.filter((f) => f.ext === "f2d").length;
        items.push({ info: nf3d + " models, " + nf2d + " drawings in this folder" });
      }
      box.replaceChildren(...(items.length ? items.map((it) => it.info ? el("div", { class: "item muted", text: it.info }) :
        el("div", { class: "item" }, el("button", { class: "link", type: "button", text: it.name, onclick: it.open }),
          it.folder ? el("span", { class: "row" },
            el("button", { class: "btn small", type: "button", text: "Use for 2X8X", onclick: () => setFamilyFolder("2X8X", it.folder) }),
            el("button", { class: "btn small", type: "button", text: "Use for 3X8X", onclick: () => setFamilyFolder("3X8X", it.folder) })) : null))
        : [el("div", { class: "empty", text: "Empty." })]));
    } catch (e) { box.replaceChildren(el("div", { class: "errline", style: "padding:10px", text: e.message })); }
  }

  // ------------------------------------------------------------------ settings
  const FIELDS = [
    ["processing.autostart", "bool", "Start processing when Fusion starts"],
    ["processing.save_model", "bool", "Save the model to the hub after applying parameters"],
    ["processing.save_drawing", "bool", "Also save the refreshed drawing to the hub"],
    ["orders.group_by_model", "bool", "Process components that share a model back to back"],
    ["orders.pdf_name", "text", "PDF file name ({order_id}, {component_id}, {type})"],
    ["processing.null_policy", ["zero", "skip", "fail"], "JSON null values: write 0 / keep model value / fail component"],
    ["processing.unknown_parameter_policy", ["warn", "fail"], "Order parameter missing from the model"],
    ["processing.missing_drawing_policy", ["fail", "warn"], "Rule without a drawing"],
    ["processing.model_cache_size", "int", "Models kept open between components (0 = close every time)"],
    ["processing.recycle_every_components", "int", "Close every document after this many components (0 = never)"],
    ["processing.save_timeout_s", "num", "Cloud save timeout (s)"],
    ["processing.drawing_update_timeout_s", "num", "Drawing update timeout (s)"],
    ["processing.export_timeout_s", "num", "PDF export timeout (s)"],
    ["processing.drawing_settle_s", "num", "Pause after opening / updating a drawing (s)"],
    ["processing.step_stuck_timeout_s", "num", "Restart Fusion when one step blocks longer than (s)"],
    ["health.max_threads", "int", "Restart Fusion above this many threads"],
    ["health.max_working_set_mb", "int", "Restart Fusion above this much memory (MB)"],
    ["health.max_open_documents", "int", "Restart Fusion above this many open documents"],
    ["health.restart_every_components", "int", "Refresh Fusion after this many components (0 = never)"],
    ["supervisor.auto_restart_fusion", "bool", "Let the harness restart Fusion when needed"],
    ["supervisor.heartbeat_stale_s", "num", "Treat the add-in as hung after this many silent seconds"],
    ["supervisor.restart_cooldown_s", "num", "Minimum time between automatic restarts (s)"],
    ["paths.dropbox", "text", "Dropbox folder (blank = project dropbox/)"],
    ["paths.output", "text", "PDF output folder (blank = project output/)"],
  ];
  const getPath = (o, p) => p.split(".").reduce((a, k) => (a || {})[k], o);
  function setPath(o, p, v) { const ks = p.split("."); let a = o; ks.slice(0, -1).forEach((k) => { a = a[k] = a[k] || {}; }); a[ks[ks.length - 1]] = v; }
  async function loadLocation() {
    const body = $("location-body");
    let L;
    try { L = await api("GET", "/api/location"); } catch (e) { body.replaceChildren(el("div", { class: "errline", text: e.message })); return; }
    document.querySelectorAll("#mode-seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.m === L.mode)));
    const yes = (ok, t, f) => el("span", { class: ok ? "ok-text" : "bad-text", text: ok ? t : f });
    const dirRows = (L.directories || []).map((d) => tr(d.role, el("span", { class: "mono", text: d.path }),
      yes(d.exists, "reachable", "not reachable"), d.exists ? yes(d.writable, "yes", "no") : ""));
    const parts = [el("dl", { class: "kv" }, el("dt", { text: "Orders are read from" }), el("dd", { class: "mono", text: L.inbox }),
        el("dt", { text: "Fusion writes" }), el("dd", { text: "only to this computer (output run folders, machine_drops); the sync service copies results out" })),
      el("h2", { style: "margin-top:14px", text: "Directories" }), tbl(["Role", "Path", "Status", "Writable"], dirRows)];
    const sy = L.sync;
    if (sy && !sy.error) {
      const pairs = sy.pairs || sy.results || [];
      parts.push(el("div", { class: "row", style: "justify-content:space-between;margin-top:14px" },
        el("h2", { style: "margin:0", text: "Sync to shares" }),
        el("div", { class: "row" }, el("span", { class: "muted", text: sy.last_pass ? "last pass " + fmtAgo(sy.last_pass) + (sy.in_pass ? " (running)" : "") : "no pass yet" }),
          el("button", { class: "btn small", type: "button", text: "Sync now", onclick: async () => {
            try { await api("POST", "/api/sync", { action: "run_once" }); toast("Sync pass started"); setTimeout(loadLocation, 2500); } catch (e) { toast(e.message, true); } } }))),
        tbl(["Copies", "To", "Reachable", "Copied last pass", "Waiting to settle", "Problems"],
          pairs.map((p) => tr(p.name, el("span", { class: "mono", text: p.dest || "" }), yes(p.reachable, "yes", "no"),
            (p.copied || []).length, p.pending || 0, (p.errors || []).join("; ")))));
    } else {
      parts.push(el("p", { class: "review", text: "File sync service is not running" + (sy && sy.error ? ": " + sy.error : ".") }));
    }
    body.replaceChildren(...parts);
  }
  let modeArmed = null;
  document.querySelectorAll("#mode-seg button").forEach((b) => b.addEventListener("click", async () => {
    if (b.getAttribute("aria-pressed") === "true") return;
    if (b.dataset.m === "remote" && modeArmed !== b) {      // two-step confirm for going live
      modeArmed = b; b.textContent = "Click again: switch to REMOTE";
      setTimeout(() => { if (modeArmed === b) { modeArmed = null; b.textContent = "Remote (network)"; } }, 4000);
      return;
    }
    modeArmed = null; if (b.dataset.m === "remote") b.textContent = "Remote (network)";
    try { await api("POST", "/api/location", { mode: b.dataset.m }); toast("Location: " + b.dataset.m.toUpperCase()); loadLocation(); pollOverview(); }
    catch (e) { toast(e.message, true); }
  }));

  async function loadSettings() {
    loadLocation();
    try { S.cfg = await api("GET", "/api/config"); } catch (e) { toast(e.message, true); return; }
    const form = $("settings-form");
    form.replaceChildren(...FIELDS.map(([path, kind, label]) => {
      const v = getPath(S.cfg, path);
      let input;
      if (kind === "bool") {
        input = el("input", { type: "checkbox", checked: !!v, dataset: { path, kind } });
        return el("label", { class: "field check" }, input, el("span", { text: label }));
      }
      if (Array.isArray(kind)) input = el("select", { dataset: { path, kind: "choice" } }, ...kind.map((k) => el("option", { value: k, text: k, selected: v === k })));
      else input = el("input", { type: kind === "text" ? "text" : "number", step: kind === "int" ? "1" : "any", value: v === undefined || v === null ? "" : String(v), dataset: { path, kind } });
      return el("label", { class: "field" }, el("span", { text: label }), input);
    }));
    $("inbox-path").textContent = "dropbox/inbox";
  }
  $("btn-save-settings").addEventListener("click", async () => {
    const cfg = JSON.parse(JSON.stringify(S.cfg));
    document.querySelectorAll("#settings-form [data-path]").forEach((inp) => {
      const k = inp.dataset.kind;
      let v = k === "bool" ? inp.checked : inp.value;
      if (k === "int") v = parseInt(v, 10);
      if (k === "num") v = parseFloat(v);
      setPath(cfg, inp.dataset.path, v);
    });
    try { S.cfg = await api("PUT", "/api/config", cfg); toast("Settings saved"); }
    catch (e) { toast("Not saved: " + e.message, true); }
  });

  // ------------------------------------------------------------------ docs
  // Documents are the project's own files in docs/ (trusted), rendered with
  // marked + mermaid from the CDN; without internet the raw text is shown.
  const libs = {};
  function loadScript(src) {
    if (!libs[src]) libs[src] = new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src; s.onload = res; s.onerror = () => rej(new Error("could not load " + src));
      document.head.appendChild(s);
    });
    return libs[src];
  }
  const MARKED = "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js";
  const MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js";
  const isDark = () => {
    const t = document.documentElement.getAttribute("data-theme");
    return t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  };

  async function loadDocs() {
    const list = $("docs-list");
    let docs;
    try { docs = (await api("GET", "/api/docs")).docs; }
    catch (e) { list.replaceChildren(el("div", { class: "errline", text: e.message })); return; }
    if (!docs.length) { list.replaceChildren(el("div", { class: "empty", text: "No documents in docs/ yet." })); return; }
    S.docs = docs;
    const groups = [["doc", "Documents"], ["table", "Logic tables"], ["diagram", "Diagrams"]];
    list.replaceChildren(...groups.flatMap(([kind, label]) => {
      const items = docs.filter((d) => d.kind === kind);
      if (!items.length) return [];
      return [el("div", { class: "group", text: label }),
        ...items.map((d) => el("button", { type: "button", dataset: { name: d.name }, text: d.title, onclick: () => openDoc(d.name) }))];
    }));
    const want = S.docOpen || (docs.find((d) => d.name === "OPERATIONS.md") || docs[0]).name;
    openDoc(want);
  }

  async function openDoc(name, anchor) {
    S.docOpen = name;
    document.querySelectorAll("#docs-list button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.name === name)));
    const body = $("doc-body");
    const url = "/docs/" + encodeURIComponent(name);
    if (/\.svg$/i.test(name)) {
      body.replaceChildren(el("h1", { text: (S.docs.find((d) => d.name === name) || {}).title || name }),
        el("p", null, el("a", { href: url, target: "_blank", rel: "noopener", text: "Open full size" })),
        el("div", { class: "diagram-full" }, el("img", { src: url, alt: name })));
      return;
    }
    body.replaceChildren(el("p", { class: "muted", text: "Loading " + name + "…" }));
    let text;
    try { const r = await fetch(url); if (!r.ok) throw new Error("HTTP " + r.status); text = await r.text(); }
    catch (e) { body.replaceChildren(el("div", { class: "errline", text: "Could not load " + name + ": " + e.message })); return; }
    try {
      await loadScript(MARKED);
      body.innerHTML = window.marked.parse(text);
    } catch (e) {
      body.replaceChildren(el("p", { class: "muted", text: "Formatting unavailable offline; showing plain text." }), el("pre", { text }));
      return;
    }
    // relative links/images point into docs/
    body.querySelectorAll("img").forEach((img) => {
      const src = img.getAttribute("src") || "";
      if (!/^(https?:|data:|\/)/.test(src)) img.src = "/docs/" + src.replace(/^(\.\/|docs\/)/, "");
    });
    body.querySelectorAll("a[href]").forEach((a) => {
      const href = a.getAttribute("href");
      const m = /^(?:\.\/|docs\/|\.\.\/)*((?:tables\/)?[\w.\-]+\.(?:md|svg))(#.*)?$/i.exec(href);
      if (m) {
        a.addEventListener("click", (e) => { e.preventDefault(); openDoc(m[1], m[2]); });
      } else if (href.startsWith("#")) {
        a.addEventListener("click", (e) => {
          e.preventDefault();
          const t = body.querySelector("[id='" + CSS.escape(href.slice(1)) + "']") || findHeading(body, href.slice(1));
          if (t) t.scrollIntoView({ behavior: "smooth" });
        });
      } else if (/^https?:/.test(href)) { a.target = "_blank"; a.rel = "noopener"; }
    });
    const blocks = [...body.querySelectorAll("pre > code.language-mermaid")];
    if (blocks.length) {
      const nodes = blocks.map((code) => {
        const d = el("div", { class: "mermaid" });
        d.textContent = code.textContent;
        code.parentElement.replaceWith(d);
        return d;
      });
      try {
        await loadScript(MERMAID);
        window.mermaid.initialize({ startOnLoad: false, theme: isDark() ? "dark" : "neutral", securityLevel: "strict",
          fontFamily: "Instrument Sans, Segoe UI, sans-serif",
          // natural size + horizontal scroll: shrinking big flowcharts makes them unreadable
          flowchart: { useMaxWidth: false }, sequence: { useMaxWidth: false }, state: { useMaxWidth: false } });
        await window.mermaid.run({ nodes });
      } catch (e) {
        nodes.forEach((n) => { if (!n.querySelector("svg")) { const pre = el("pre", { text: n.textContent }); n.replaceWith(pre); } });
      }
    }
    if (anchor) { const t = findHeading(body, anchor.slice(1)); if (t) t.scrollIntoView(); } else window.scrollTo(0, 0);
  }
  function findHeading(root, slug) {
    const norm = (s) => s.toLowerCase().trim().replace(/[^\w\- ]+/g, "").replace(/\s+/g, "-");
    return [...root.querySelectorAll("h1,h2,h3,h4")].find((h) => norm(h.textContent) === norm(slug.replace(/-/g, " ")) || norm(h.textContent) === slug.toLowerCase());
  }

  // ------------------------------------------------------------------ logic tables
  // Rendered from /api/logic: the same tables and settings the add-in uses.
  S.logicSection = "toolpaths";
  document.querySelectorAll("#logic-seg button").forEach((b) => b.addEventListener("click", () => {
    S.logicSection = b.dataset.l;
    document.querySelectorAll("#logic-seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    renderLogic();
  }));
  async function loadLogic() {
    try { S.logic = await api("GET", "/api/logic"); }
    catch (e) { $("logic-body").replaceChildren(el("div", { class: "card errline", text: e.message })); return; }
    renderLogic();
  }
  function tbl(headers, rows, cls) {
    return el("div", { class: "table-wrap" }, el("table", { class: "logic " + (cls || "") },
      el("thead", null, el("tr", null, ...headers.map((h) => el("th", { text: h })))),
      el("tbody", null, ...rows)));
  }
  const tr = (...cells) => el("tr", null, ...cells.map((c) => (c instanceof Node ? el("td", null, c) : el("td", { text: c === undefined || c === null ? "" : String(c) }))));
  function wsChip(name) {
    const m = /G5[4-9]/i.exec(name || "");
    return el("span", { class: "setup-chip" }, m ? el("b", { class: "mono", text: m[0].toUpperCase() }) : null, " " + name);
  }
  function docLink(file, label) {
    return el("button", { class: "btn small", type: "button", text: label || "Open full table document",
      onclick: () => { S.docOpen = file; showView("docs"); } });
  }
  function renderLogic() {
    const box = $("logic-body");
    if (!S.logic) { box.replaceChildren(el("div", { class: "card muted", text: "Loading…" })); return; }
    box.replaceChildren();
    ({ rules: renderRulesDoc, toolpaths: renderToolpaths, splicing: renderSplicing, drilling: renderDrilling, naming: renderNaming })[S.logicSection](box);
  }

  // -- rules in plain words (docs/tables/SELECTION_LOGIC.md) -----------------
  async function renderRulesDoc(box) {
    const body = el("article", { class: "card doc-body" }, el("p", { class: "muted", text: "Loading…" }));
    box.appendChild(body);
    try {
      const r = await fetch("/docs/" + encodeURIComponent("tables/SELECTION_LOGIC.md"));
      if (!r.ok) throw new Error("HTTP " + r.status);
      const text = await r.text();
      await loadScript(MARKED);
      body.innerHTML = window.marked.parse(text);      // project's own document (trusted)
      body.querySelectorAll("a[href]").forEach((a) => {
        const m = /^((?:tables\/)?[\w.\-]+\.md)$/i.exec(a.getAttribute("href"));
        if (m) a.addEventListener("click", (e) => { e.preventDefault(); S.docOpen = "tables/" + m[1].replace(/^tables\//, ""); showView("docs"); });
      });
    } catch (e) { body.replaceChildren(el("div", { class: "errline", text: "Could not load the rules document: " + e.message })); }
  }

  // -- toolpath selection -------------------------------------------------
  function varSpec(v) { return typeof v === "string" ? { param: v, source: "auto" } : Object.assign({ source: "auto" }, v); }
  function varKind(name, spec) {
    if (spec.type) return spec.type;
    return /height|width|dist|clearance|length/i.test(spec.param || name) ? "number" : "bool";
  }
  function renderToolpaths(box) {
    const L = S.logic, profiles = L.profiles || [];
    if (!S.logicProfile || !profiles.some((p) => p.id === S.logicProfile)) S.logicProfile = profiles.length ? profiles[0].id : null;
    const prof = profiles.find((p) => p.id === S.logicProfile);
    const picker = el("div", { class: "seg" }, ...profiles.map((p) => el("button", { type: "button", "aria-pressed": String(p.id === S.logicProfile),
      text: p.title || p.id, onclick: () => { S.logicProfile = p.id; S.logicResult = null; renderLogic(); } })));
    const head = el("div", { class: "card" },
      el("div", { class: "row", style: "justify-content:space-between" }, picker,
        el("div", { class: "row" },
          el("span", { class: "muted", text: "Source: " + L.profiles_source }),
          el("button", { class: "btn small", type: "button", text: "Edit tables (JSON)", onclick: () => editProfiles() }),
          L.profiles_source === "built-in defaults" ? null : el("button", { class: "btn small danger", type: "button", text: "Reset to defaults", onclick: resetProfiles }),
          docLink("tables/TOOLPATH_SELECTION.md"))));
    box.appendChild(head);
    if (!prof) return;
    const errs = (L.errors || {})[prof.id] || [];
    const res = S.logicResult && S.logicResult.profile === prof.id ? S.logicResult : null;
    const truth = {};
    if (res && res.rows) res.rows.forEach((r) => { truth[r[0]] = r[1]; });

    const info = el("div", { class: "card" },
      el("h2", { text: prof.title || prof.id }),
      errs.length ? el("div", { class: "errline", text: "This table is invalid and components using it will fail: " + errs.join("; ") }) : null,
      el("dl", { class: "kv" },
        el("dt", { text: "Used for" }), el("dd", { text: (prof.type || "any") + " components, " + (prof.series ? "series " + prof.series : "family " + (prof.family || "any")) + (prof.enabled === false ? " (disabled)" : "") }),
        el("dt", { text: "How it works" }), el("dd", { text: "Rows are checked top to bottom; the first row whose condition is true decides which setups are posted, in that order. No setups = no toolpaths: the manufacture workspace is skipped." }),
        el("dt", { text: "Operations not listed" }), el("dd", { text: ({ leave: "left as they are in the model", suppress: "suppressed", activate: "activated" })[prof.unknown_operations] || prof.unknown_operations || "left as they are" }),
        el("dt", { text: "Splicing" }), el("dd", { text: (prof.splice || {}).g57_before_g59 === false ? "sections spliced in table order" : "G57 sections are spliced before G59 sections (stable), as in the old post processor" })));
    (prof.notes || []).forEach((n) => info.appendChild(el("p", { class: "review", text: n })));
    box.appendChild(info);

    const vars = Object.entries(prof.variables || {});
    const inputs = {};
    const tester = el("div", { class: "card" }, el("h2", { text: "Try it" }),
      el("p", { class: "muted", style: "font-size:13px;margin-top:0", text: "Set the model/order values and see which row matches and what gets posted." }));
    const form = el("div", { class: "form-grid" });
    vars.forEach(([name, raw]) => {
      const spec = varSpec(raw), kind = varKind(name, spec);
      const prev = S.logicInputs && S.logicInputs[spec.param];
      let inp;
      if (kind === "bool") inp = el("input", { type: "checkbox", checked: !!prev });
      else inp = el("input", { type: kind === "number" ? "number" : "text", step: "any", value: prev === undefined ? (kind === "number" ? "" : "") : String(prev) });
      inputs[name] = { spec, kind, inp };
      form.appendChild(kind === "bool" ? el("label", { class: "field check" }, inp, el("span", { class: "mono", text: spec.param }))
        : el("label", { class: "field" }, el("span", { class: "mono", text: spec.param }), inp));
    });
    const out = el("div", { style: "margin-top:12px" });
    tester.append(form, el("div", { class: "row", style: "margin-top:10px" },
      el("button", { class: "btn primary", type: "button", text: "Evaluate", onclick: async () => {
        const values = {};
        Object.values(inputs).forEach(({ spec, kind, inp }) => {
          values[spec.param] = kind === "bool" ? (inp.checked ? 1 : 0) : kind === "number" ? (inp.value === "" ? 0 : parseFloat(inp.value)) : inp.value;
        });
        S.logicInputs = values;
        try { S.logicResult = Object.assign({ profile: prof.id }, await api("POST", "/api/logic/evaluate", { profile_id: prof.id, values })); renderLogic(); }
        catch (e) { toast(e.message, true); }
      } })), out);
    if (res) {
      if (!res.matched) out.appendChild(el("div", { class: "errline", text: "No row matches: " + (res.error || "") }));
      else {
        out.append(el("div", { class: "row" }, el("b", { text: "Row " + res.matched }), el("span", { class: "muted", text: res.note || "" })),
          el("div", { class: "row", style: "margin:8px 0" }, res.setups.length ? el("span", { text: "Posts:" }) : el("b", { text: "No toolpaths - manufacture step skipped" }),
            ...res.setups.map((s, i) => el("span", { class: "row" }, i ? "→" : null, wsChip(s)))),
          res.op_rules && res.op_rules.length ? tbl(["Operation (in the posted setups)", "State"],
            res.op_rules.map((o) => tr(o.op, o.active ? "active" : "suppressed"))) : null);
      }
      if (res.missing && res.missing.length) out.appendChild(el("p", { class: "review", text: "Not provided (default used): " + res.missing.join(", ") }));
    }

    box.appendChild(el("div", { class: "card" }, el("h2", { text: "Decision table" }),
      tbl(["#", "Row", "Condition", "Setups posted (in order)", "Note", res ? "Result" : ""],
        (prof.sequence_rules || []).map((r, i) => {
          const row = el("tr", { class: res && res.matched === r.id ? "hit" : "" },
            el("td", { class: "num", text: String(i + 1) }), el("td", { class: "mono", text: r.id }),
            el("td", null, el("code", { text: r.when })),
            el("td", null, r.setups && r.setups.length ? el("div", { class: "chips-inline" }, ...r.setups.map(wsChip)) : el("span", { class: "muted", text: "none - skip CAM" })),
            el("td", null, el("div", { text: r.note || "" }), r.review ? el("div", { class: "review", text: "Review: " + r.review }) : null),
            el("td", { text: res && r.id in truth ? (truth[r.id] ? "true" : "false") : "" }));
          return row;
        }))));

    box.appendChild(el("div", { class: "grid-2" },
      el("div", { class: "card" }, el("h2", { text: "Variables" }),
        tbl(["Name", "Reads parameter", "From", "Default"], vars.map(([n, raw]) => { const sp = varSpec(raw); return tr(el("code", { text: n }), el("span", { class: "mono", text: sp.param }), sp.source, sp.default === undefined ? 0 : sp.default); })),
        Object.keys(prof.derived || {}).length ? el("div", null, el("h2", { style: "margin-top:14px", text: "Derived" }),
          tbl(["Name", "Expression"], Object.entries(prof.derived).map(([n, e]) => tr(el("code", { text: n }), el("code", { text: e }))))) : null),
      el("div", { class: "card" }, el("h2", { text: "Operation rules" }),
        (prof.operation_rules || []).length ? tbl(["Operation", "Active when"], prof.operation_rules.map((o) => tr(o.op, el("code", { text: o.active }))))
          : el("p", { class: "muted", text: "No per-operation rules: operations in posted setups are " + (prof.unknown_operations || "left as they are") + "." }))));
    box.appendChild(tester);
  }
  function editProfiles() {
    const box = $("logic-body");
    const ta = el("textarea", { rows: "28", spellcheck: "false" });
    ta.value = JSON.stringify(S.logic.profiles, null, 2);
    box.replaceChildren(el("div", { class: "card" },
      el("h2", { text: "Edit toolpath tables" }),
      el("p", { class: "muted", style: "font-size:13px", text: "Conditions use: and / or / not, == != < <= > >=, + - * /, numbers, 'text', true/false and the variable names. Saved to " + S.logic.profiles_file + " and used from the next order." }),
      ta,
      el("div", { class: "row", style: "margin-top:10px" },
        el("button", { class: "btn primary", type: "button", text: "Validate and save", onclick: async () => {
          let profiles;
          try { profiles = JSON.parse(ta.value); } catch (e) { toast("Not valid JSON: " + e.message, true); return; }
          try { await api("PUT", "/api/logic/profiles", { profiles }); toast("Toolpath tables saved"); loadLogic(); }
          catch (e) { toast("Not saved: " + e.message, true); }
        } }),
        el("button", { class: "btn", type: "button", text: "Cancel", onclick: renderLogic }))));
  }
  let resetArmed = null;
  async function resetProfiles(ev) {
    const b = ev.currentTarget;
    if (!resetArmed) { b.textContent = "Click again to reset"; resetArmed = setTimeout(() => { resetArmed = null; b.textContent = "Reset to defaults"; }, 4000); return; }
    clearTimeout(resetArmed); resetArmed = null;
    try { await api("POST", "/api/logic/reset", {}); toast("Tables reset to the built-in defaults"); loadLogic(); } catch (e) { toast(e.message, true); }
  }

  // -- splicing ---------------------------------------------------------------
  function renderSplicing(box) {
    const sp = S.logic.splice || {};
    box.append(
      el("div", { class: "card" }, el("h2", { text: "How one G-code file is built per component" }),
        tbl(["Step", "What happens"], [
          tr("1. Post each setup", "Every setup chosen by the toolpath table is post-processed on its own with " + (sp.post_processor || "the post") + " (program numbers from a persistent counter starting at " + sp.counter_start + ")."),
          tr("2. Order sections", "Table order, then a stable sort that puts G57 sections before G59 sections (other sections keep their place)."),
          tr("3. First section", "Kept with its header (%, O-number, machine and tool comments); its footer (M30, %) is removed."),
          tr("4. Each next section", "Preceded by the stop block below; its header (everything before the first N line) and footer are removed."),
          tr("5. End", "M30 and % are added once at the end."),
          tr("6. Renumber", "All N numbers renumbered N10, N15, N20 … across the whole file."),
          tr("7. Save", "Written as .txt with CRLF line endings: " + (S.logic.naming.example.gcode || "")),
        ]), docLink("tables/SPLICING.md")),
      el("div", { class: "grid-2" },
        el("div", { class: "card" }, el("h2", { text: "Transition between sections" }),
          tbl(["Previous section", "Next section", "Comment in the stop block"], [
            tr("G57", "G59", sp.rotate_text), tr("G59", "G57", sp.rotate_text),
            tr("G57", "G57", sp.flip_text), tr("G59", "G59", sp.flip_text),
            tr("no G5x in setup name", "any", sp.flip_text)])),
        el("div", { class: "card" }, el("h2", { text: "Stop block inserted between sections" }),
          el("pre", { text: "M0\n\n(******************)\n(NEW SETUP, " + sp.rotate_text + " | " + sp.flip_text + ")\n(******************)\n" }),
          el("p", { class: sp.strip_numbered_m30 ? "muted" : "review",
            text: sp.strip_numbered_m30 ? "Each section's numbered N### M30 is removed (cam.strip_numbered_m30 = true)."
              : "As in the old pipeline, each section keeps its numbered N### M30 before the M0 (cam.strip_numbered_m30 = false). Confirm how the Stratos control treats an M30 mid-program." }))),
      el("div", { class: "card" }, el("h2", { text: "Examples" }),
        tbl(["Toolpath table posts", "Spliced order", "Transitions"], [
          tr("Setup 4 - G59, Setup 7 - G57 (out-swing door, both sides)", "Setup 7 - G57 → Setup 4 - G59", sp.rotate_text),
          tr("Right Rabbet - In G57, Left Rabbet - In G59 (stile, both in-swing)", "unchanged", sp.rotate_text),
          tr("Left Rabbet - In G59, Right Rabbet - Out G59 (stile, opposite swings)", "unchanged", sp.flip_text),
          tr("Setup Back Bottom Corner G58 (panel)", "single section", "none")])));
  }

  // -- drilling ------------------------------------------------------------------
  function renderDrilling(box) {
    const D = S.logic.drilling || {}, ws = D.workstops || {}, air = D.air_drill || {};
    const wsRow = (label, key) => tr(label, (ws[key] || {}).same, (ws[key] || {}).different);
    box.append(
      el("div", { class: "card" }, el("h2", { text: "Workstop selection" }),
        el("p", { class: "muted", style: "font-size:13px;margin-top:0", text: "\"Same\" = the door's hinging_right equals its swinging_out (door: door_hinging_right / door_swinging_out; stile: per side LD_ / RD_ hinging_right and swinging_out)." }),
        tbl(["Component", "Same → workstop", "Different → workstop"], [
          wsRow("Door (all series)", "door"), wsRow("Stile 3X82 / 3X81", "stile"), wsRow("Stile 3X86", "stile_3086")]),
        docLink("tables/DRILLING.md")),
      el("div", { class: "grid-2" },
        el("div", { class: "card" }, el("h2", { text: "Which stile sides are drilled" }),
          tbl(["Side", "Drilled when"], [
            tr("Left", "left_side_door = 1 and LD_hinging_right = 1 (door hinges onto the stile)"),
            tr("Right", "right_side_door = 1 and RD_hinging_right = 0 (door hinges onto the stile)")]),
          el("h2", { style: "margin-top:14px", text: "FlipRotate" }),
          tbl(["Case", "FlipRotate"], [
            tr("Door", "1"),
            tr("Stile, one side drilled or both sides the same face", "1"),
            tr("Stile, interior drilling on one side and exterior on the other", "3 for the exterior side, 4 for the interior side")])),
        el("div", { class: "card" }, el("h2", { text: "Air drilling" }),
          tbl(["When", "Records added"], [
            tr("Component has no holes (or no " + (D.y_param || "hinge_hole_x_dist") + ")", "One on workstop 1 and one on workstop 4"),
            tr("Holes only on one workstop, FlipRotate not 2 or 3", "One on the other workstop (copies the first hole's settings)"),
            tr("Holes on both workstops, or mixed FlipRotate 3/4", "none")]),
          tbl(["Air drill position", "Value"], [tr("PositionX", air.x), tr("PositionY", air.y), tr("DrillName", air.name)]))),
      el("div", { class: "card" }, el("h2", { text: "Hole positions" }),
        tbl(["Item", "Source"], [
          tr("PositionX", "hinge hole *_y_value parameters (door: top/mid_top/mid_bottom/bottom_hinge_hole_1/2_y_value; stile: LD_/RD_stile_…_y_value), inches"),
          tr("X offset", Object.entries(D.x_offset_by_series || {}).map(([k, v]) => k + " series: " + v + " in").join("; ") || "none"),
          tr("PositionY", D.y_param || "hinge_hole_x_dist"),
          tr("Function / ToolDM", (D.function) + " / " + (D.tool_dm)),
          tr("ProgramName", "same stem as the component's G-code, e.g. " + (S.logic.naming.example.gcode || "").replace(/\.txt$/i, "")),
          tr("File", "one per order: " + S.logic.naming.example.drilling + " (doors and stiles)")])));
    // tester
    const f = {};
    const chk = (k, label) => { f[k] = el("input", { type: "checkbox" }); return el("label", { class: "field check" }, f[k], el("span", { class: "mono", text: label || k })); };
    f.type = el("select", null, el("option", { text: "door" }), el("option", { text: "stile" }));
    f.series = el("input", { type: "text", value: "3082G.67P" });
    const out = el("div", { style: "margin-top:12px" });
    const holes = [1.72, 3.22, 29.594, 31.094, 57.468, 58.968, 85.343, 86.843];
    const names = ["top_hinge_hole_1", "top_hinge_hole_2", "mid_top_hinge_hole_1", "mid_top_hinge_hole_2", "mid_bottom_hinge_hole_1", "mid_bottom_hinge_hole_2", "bottom_hinge_hole_1", "bottom_hinge_hole_2"];
    box.appendChild(el("div", { class: "card" }, el("h2", { text: "Try it" }),
      el("div", { class: "form-grid" },
        el("label", { class: "field" }, el("span", { text: "Component type" }), f.type),
        el("label", { class: "field" }, el("span", { text: "series_id" }), f.series),
        chk("door_hinging_right"), chk("door_swinging_out"),
        chk("left_side_door"), chk("LD_hinging_right"), chk("LD_swinging_out"),
        chk("right_side_door"), chk("RD_hinging_right"), chk("RD_swinging_out"),
        chk("left_interior_drilling"), chk("left_exterior_drilling"), chk("right_interior_drilling"), chk("right_exterior_drilling")),
      el("div", { class: "row", style: "margin-top:10px" }, el("button", { class: "btn primary", type: "button", text: "Show drilling records", onclick: async () => {
        const ov = { series_id: f.series.value }, mv = { hinge_hole_x_dist: 0.968 };
        ["door_hinging_right", "door_swinging_out", "left_side_door", "LD_hinging_right", "LD_swinging_out", "right_side_door", "RD_hinging_right", "RD_swinging_out"].forEach((k) => { ov[k] = f[k].checked ? 1 : 0; });
        ["left_interior_drilling", "left_exterior_drilling", "right_interior_drilling", "right_exterior_drilling"].forEach((k) => { mv[k] = f[k].checked ? 1 : 0; });
        names.forEach((n, i) => { mv[n + "_y_value"] = holes[i]; mv["LD_stile_" + n + "_y_value"] = holes[i]; mv["RD_stile_" + n + "_y_value"] = holes[i] + 0.5; });
        try {
          const r = (await api("POST", "/api/logic/drilling", { type: f.type.value, component_id: f.type.value === "door" ? "D1" : "S1", order_id: "TEST", series_id: f.series.value, order_values: ov, model_values: mv })).result;
          if (!r) { out.replaceChildren(el("p", { class: "muted", text: "No drilling output for this component type." })); return; }
          out.replaceChildren(el("p", { class: "muted", text: r.description || "" }),
            tbl(["ProgramName", "WorkStop", "PositionX", "PositionY", "FlipRotate", "DrillName"],
              (r.coordinates || []).map((c) => tr(c.ProgramName, c.WorkStop, c.PositionX, c.PositionY, c.FlipRotate, c.DrillName))));
        } catch (e) { out.replaceChildren(el("div", { class: "errline", text: e.message })); }
      } })), out));
  }

  // -- naming ---------------------------------------------------------------------
  function renderNaming(box) {
    const N = S.logic.naming || {}, ex = N.example || {}, t = N.templates || {};
    const labels = { order_folder: "Order output folder", gcode: "G-code (one per component)", drawing_pdf: "Drawing PDF (one per component)", drilling: "Drilling file (one per order)", report: "Order report" };
    box.appendChild(el("div", { class: "card" }, el("h2", { text: "Output naming" }),
      el("p", { class: "muted", style: "font-size:13px;margin-top:0", text: "All of a component's files share the stem 1-{component_id}-{order_id}, and every file of an order goes into the order's run folder. Example for order 3X8X_DV_0622, component D1:" }),
      tbl(["Output", "Template", "Example"], Object.keys(labels).map((k) => tr(labels[k], el("code", { text: t[k] || "" }), el("span", { class: "mono", text: ex[k] || "" })))),
      el("p", { class: "muted", style: "font-size:13px", text: "Placeholders: " + (N.placeholders || []).map((p) => "{" + p + "}").join(" ") + ". {run_stamp} looks like \"Sep 28 2026 - 0520\" (no colons). Change templates in config/config.json → naming." })));
  }

  // ------------------------------------------------------------------ boot
  const VIEWS = ["monitor", "orders", "series", "logic", "settings", "log", "docs"];
  let initial = "monitor";
  try { initial = localStorage.getItem("nfe.view") || "monitor"; } catch (e) { /* ignore */ }
  if (VIEWS.includes(location.hash.slice(1))) initial = location.hash.slice(1);
  const deepDoc = /^#docs\/([\w.\-]+)$/.exec(location.hash);      // e.g. #docs/WORKFLOW.md
  if (deepDoc) { initial = "docs"; S.docOpen = deepDoc[1]; }
  const deepLogic = /^#logic\/(rules|toolpaths|splicing|drilling|naming)$/.exec(location.hash);   // e.g. #logic/drilling
  if (deepLogic) {
    initial = "logic"; S.logicSection = deepLogic[1];
    document.querySelectorAll("#logic-seg button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.l === deepLogic[1])));
  }
  window.addEventListener("hashchange", () => { const v = location.hash.slice(1); if (VIEWS.includes(v)) showView(v); });
  $("inbox-path").textContent = "dropbox/inbox";
  showView(initial);
  pollOverview();
  pollEvents();
  setInterval(pollOverview, 2000);
  setInterval(pollEvents, 2000);
  setInterval(() => { if (S.view === "monitor" && S.overview) renderTraveler(); }, 1000);
  window.addEventListener("resize", () => { if (S.view === "monitor" && S.overview) renderCharts(); });
})();
