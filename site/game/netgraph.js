// Singularity — internet graph view (Canvas 2D). Classic script; exposes the global `NetGraph`.
//
//   NetGraph.mount(el, { G: () => G | null, world, onSelect: iso => void, controls?: true })
//   NetGraph.render()          cheap; schedules one frame (call every tick while visible)
//   NetGraph.resize()          re-measure el (a ResizeObserver also does this)
//   NetGraph.setMode("force" | "geo")   animated switch between the force layout and pure geography
//   NetGraph.getMode()  NetGraph.select(iso | null)  NetGraph.repaint() (re-read CSS colours)  NetGraph.destroy()
//
// Nodes are countries sized by internet users (pop × net%); edges are world.cab (submarine cable links, width ∝ cable count)
// and world.nb (land fibre). The force layout is seeded and computed once per world, then cached. With a game running it
// draws each country's four layers G.C[i].L {web, social, lan, gap} as ring segments, firewall shells (thickness ∝ fw),
// a satellite orbit when G.cov > 0, counter-measures from G.C[i].hx (cut links dashed and dimmed), and animates new
// spread: it peeks at G.ev / G.recent seed events without draining them and falls back to diffing C[i].p.
(function () {
  "use strict";
  const LAYERS = [["web", "Web & cloud", "--web-2", "#2fd3c4"], ["social", "Social media", "--social", "#ff7ad9"],
    ["lan", "Enterprise LAN", "--lan", "#ffd166"], ["gap", "Critical systems", "--gap-on", "#ff3d8b"]];
  const VIA = { cable: "--via-cable", air: "--via-air", land: "--via-land", sat: "--via-sat", open: "--via-open" };
  const HX_N = { cap: "Compute cap", cut: "Internet shutdown", grid: "Grid disconnection", audit: "Audit sweep", raid: "Kill-switch raid",
    sanc: "Sanctions", exp: "Chip export controls", lic: "Compute licensing", ban: "Ban", anti: "Antitrust" };
  const SHORT = { USA: "USA", GBR: "UK", RUS: "Russia", KOR: "S. Korea", PRK: "N. Korea", ARE: "UAE", COD: "DR Congo", CAF: "CAR",
    BIH: "Bosnia", TTO: "Trinidad", PNG: "PNG", DOM: "Dominican Rep.", MKD: "N. Macedonia", SAU: "Saudi Arabia", ZAF: "S. Africa",
    CZE: "Czechia", NLD: "Netherlands", PHL: "Philippines", TWN: "Taiwan", HKG: "Hong Kong", VIR: "USVI", VGB: "BVI" };
  const HUBS = ["SGP", "GBR", "FRA", "USA", "JPN", "EGY", "NLD", "HKG", "ZAF", "PRT", "DJI"];   // always labelled cable hubs
  const CENTER_LNG = 10;          // map seam in the Pacific
  const LAYOUT_ITERS = 300;
  const TAU = Math.PI * 2;

  // ---------- small helpers ----------
  const clamp = (v, a, b) => v < a ? a : v > b ? b : v;
  const num = v => typeof v === "number" && isFinite(v);
  function mulberry32(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  function hash01(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return ((h >>> 0) % 100000) / 100000; }
  function hexRgb(c) {
    c = (c || "").trim();
    let m = /^#([0-9a-f]{3})$/i.exec(c);
    if (m) return m[1].split("").map(x => parseInt(x + x, 16));
    m = /^#([0-9a-f]{6})/i.exec(c);
    if (m) return [0, 2, 4].map(i => parseInt(m[1].slice(i, i + 2), 16));
    m = /rgba?\(([^)]+)\)/.exec(c);
    if (m) return m[1].split(",").slice(0, 3).map(x => parseFloat(x));
    return [128, 128, 128];
  }
  const rgba = (rgb, a) => `rgba(${rgb[0] | 0},${rgb[1] | 0},${rgb[2] | 0},${a})`;
  const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  const fmtUsers = u => u >= 1e9 ? (u / 1e9).toFixed(2) + " bn" : u >= 1e6 ? (u / 1e6).toFixed(u >= 1e8 ? 0 : 1) + " M" : u >= 1e3 ? Math.round(u / 1e3) + " k" : String(Math.round(u));
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  // ---------- graph + layout (computed once per world object) ----------
  const cache = new WeakMap();
  function geoXY(lng, lat) {
    let x = lng - CENTER_LNG; x = ((x + 540) % 360) - 180;
    const la = clamp(lat, -62, 74) * Math.PI / 180;
    const y = -Math.log(Math.tan(Math.PI / 4 + la / 2)) * 180 / Math.PI * 1.08;
    return [x, y];
  }
  function build(world) {
    if (cache.has(world)) return cache.get(world);
    const isos = Object.keys(world.c || {}).sort();
    const idx = {}; isos.forEach((k, i) => idx[k] = i);
    const nodes = isos.map(iso => {
      const c = world.c[iso];
      let net = num(c.net) ? c.net : 0;
      if (iso === "TWN" && net <= 0) net = 88;          // Taiwan has no ITU row
      const users = Math.max(0, (c.pop || 0) * net / 100);
      const g = geoXY(num(c.x) ? c.x : 0, num(c.y) ? c.y : 0);
      return { iso, n: c.n || iso, short: SHORT[iso] || (c.n && c.n.length <= 13 ? c.n : iso), users, net, fw: num(c.fw) ? c.fw : 0,
        gx: g[0], gy: g[1], r: 0, deg: 0, cabN: 0, nbN: 0, edges: [] };
    });
    const umax = Math.max(1, ...nodes.map(n => n.users));
    for (const n of nodes) n.r = 0.6 + 9 * Math.sqrt(n.users / umax);
    const edges = [];
    const seen = new Set();
    for (const e of world.cab || []) {
      const a = idx[e[0]], b = idx[e[1]]; if (a == null || b == null || a === b) continue;
      const key = a < b ? a + "," + b : b + "," + a; if (seen.has(key)) continue; seen.add(key);
      const k = Math.max(1, e[2] || 1);
      edges.push({ a, b, k, t: "cab" });
      nodes[a].deg += k; nodes[b].deg += k; nodes[a].cabN++; nodes[b].cabN++;
    }
    for (const e of world.nb || []) {
      const a = idx[e[0]], b = idx[e[1]]; if (a == null || b == null || a === b) continue;
      const key = a < b ? a + "," + b : b + "," + a;
      if (seen.has(key)) { nodes[a].nbN++; nodes[b].nbN++; continue; }  // already a cable link
      seen.add(key);
      edges.push({ a, b, k: 1, t: "nb" });
      nodes[a].nbN++; nodes[b].nbN++;
    }
    edges.forEach((e, i) => { nodes[e.a].edges.push(i); nodes[e.b].edges.push(i); });
    // bias spread curves the same way for every edge (deterministic)
    for (const e of edges) e.bend = (hash01(nodes[e.a].iso + nodes[e.b].iso) - 0.5) * 0.24;
    // two force layouts: landscape, and a portrait one with longitudes squeezed (for phones)
    forceLayout(nodes, edges, 1, 1, "wide");
    forceLayout(nodes, edges, 0.45, 1.2, "tall");
    // labels: biggest ~40 by users + the top cable hubs
    const byU = nodes.slice().sort((p, q) => q.users - p.users);
    const byD = nodes.slice().sort((p, q) => q.deg - p.deg);
    byU.forEach((n, i) => n.rankU = i);
    byD.forEach((n, i) => n.rankD = i);
    for (const n of nodes) n.lab = n.rankU < 40 || n.rankD < 12 || HUBS.includes(n.iso);
    const dmax = Math.max(1, ...nodes.map(n => n.deg));
    for (const n of nodes) n.hub = Math.sqrt(n.deg / dmax);
    const g = { nodes, edges, idx };
    cache.set(world, g);
    return g;
  }

  function forceLayout(nodes, edges, xs, ys, key) {
    const PAD = 6;
    const N = nodes.length, rnd = mulberry32(20221130);
    const x = new Float64Array(N), y = new Float64Array(N), r = new Float64Array(N);
    const ax = new Float64Array(N), ay = new Float64Array(N);
    for (let i = 0; i < N; i++) { ax[i] = nodes[i].gx * xs; ay[i] = nodes[i].gy * ys; x[i] = ax[i] + (rnd() - 0.5) * 0.3; y[i] = ay[i] + (rnd() - 0.5) * 0.3; r[i] = nodes[i].r; }
    const springs = [];
    for (const e of edges) {
      const A = nodes[e.a], B = nodes[e.b];
      const d = Math.hypot((A.gx - B.gx) * xs, (A.gy - B.gy) * ys);
      if (d > 45) continue;                                  // long-haul links don't pull continents together
      springs.push([e.a, e.b, d, e.t === "cab" ? Math.min(1, 0.35 + e.k / 6) : 0.5]);
    }
    for (let it = 0; it < LAYOUT_ITERS; it++) {
      const alpha = 1 - it / LAYOUT_ITERS;
      const dx = new Float64Array(N), dy = new Float64Array(N);
      // collision + short-range charge
      for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
        let vx = x[j] - x[i], vy = y[j] - y[i]; let d2 = vx * vx + vy * vy;
        if (d2 > 900) continue;
        if (d2 < 1e-6) { vx = rnd() - 0.5; vy = rnd() - 0.5; d2 = vx * vx + vy * vy; }
        const d = Math.sqrt(d2), minD = (r[i] + r[j]) * 1.3 + PAD;
        let f = 0;
        if (d < minD) f += (minD - d) * 0.5;
        f += 3 * alpha / (d2 + 4);
        const ux = vx / d * f, uy = vy / d * f;
        const wi = r[j] / (r[i] + r[j]), wj = 1 - wi;          // the small node moves more
        dx[i] -= ux * wi; dy[i] -= uy * wi; dx[j] += ux * wj; dy[j] += uy * wj;
      }
      // springs pull linked neighbours to a compact but readable distance
      for (const [a, b, gd, w] of springs) {
        const vx = x[b] - x[a], vy = y[b] - y[a], d = Math.hypot(vx, vy) || 1e-3;
        const rest = Math.max((r[a] + r[b]) * 1.3 + PAD + 0.5, gd * 0.9);
        const f = (d - rest) * 0.015 * w * alpha;
        const ux = vx / d * f, uy = vy / d * f;
        dx[a] += ux; dy[a] += uy; dx[b] -= ux; dy[b] -= uy;
      }
      // weak anchor to geography (a bit stronger at the end so the map stays recognisable)
      const ka = 0.004 + 0.01 * (1 - alpha);
      for (let i = 0; i < N; i++) {
        dx[i] += (ax[i] - x[i]) * ka; dy[i] += (ay[i] - y[i]) * ka;
        const m = Math.hypot(dx[i], dy[i]), cap = 3;
        if (m > cap) { dx[i] *= cap / m; dy[i] *= cap / m; }
        x[i] += dx[i]; y[i] += dy[i];
      }
    }
    for (let i = 0; i < N; i++) nodes[i][key] = [x[i], y[i]];
  }

  // ---------- instance state ----------
  let S = null;

  function readColors() {
    const cs = getComputedStyle(S.el);
    const v = (name, fb) => { const t = cs.getPropertyValue(name).trim(); return t || fb; };
    const C = {};
    const list = { bg: ["--ocean", "#071016"], ink: ["--ink", "#e4eef2"], ink2: ["--ink-2", "#9fb3bd"], ink3: ["--ink-3", "#657a85"],
      panel: ["--panel-solid", "#0c161d"], line: ["--panel-line", "#22333f"], cable: ["--cable-glow", "#2bd8ff"], vcable: ["--via-cable", "#3ee0ff"],
      land: ["--via-land", "#7dff9a"], net0: ["--net-0", "#0f1b24"], net1: ["--net-1", "#14324a"], net2: ["--net-2", "#1d5a85"],
      ai: ["--ai", "#2fd3c4"], auto: ["--auto", "#ff3d8b"], warn: ["--warn", "#ffb347"], fwlo: ["--fw-lo", "#ff9a3d"], fwhi: ["--fw-hi", "#ff2d2d"],
      sat: ["--sat", "#e0c8ff"], vsat: ["--via-sat", "#c08bff"], hx: ["--hx", "#ff6b3d"], sel: ["--sel", "#e4eef2"], open: ["--via-open", "#ff7ad9"],
      air: ["--via-air", "#ffb347"] };
    for (const k in list) { C[k + "S"] = v(list[k][0], list[k][1]); C[k] = hexRgb(C[k + "S"]); }
    C.layer = LAYERS.map(l => hexRgb(v(l[2], l[3])));
    C.via = {}; for (const k in VIA) C.via[k] = hexRgb(v(VIA[k], "#3ee0ff"));
    S.col = C;
  }

  function mount(el, opts) {
    if (S) destroy();
    opts = opts || {};
    const world = opts.world;
    if (!el || !world || !world.c) throw new Error("NetGraph.mount needs an element and world.json");
    const graph = build(world);
    if (getComputedStyle(el).position === "static") el.style.position = "relative";
    const cv = document.createElement("canvas");
    cv.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block;touch-action:none;cursor:grab";
    el.appendChild(cv);
    const tip = document.createElement("div");
    tip.className = "ng-tip";
    tip.style.cssText = "position:absolute;pointer-events:none;display:none;z-index:3;min-width:180px;max-width:250px;padding:7px 9px;border-radius:6px;" +
      "background:var(--panel,rgba(10,18,24,.94));border:1px solid var(--panel-line,#22333f);color:var(--ink,#e4eef2);font:12px/1.35 system-ui,sans-serif;box-shadow:0 4px 18px rgba(0,0,0,.45)";
    el.appendChild(tip);
    S = { el, cv, ctx: cv.getContext("2d"), tip, opts, world, g: graph, getG: typeof opts.G === "function" ? opts.G : () => opts.G || null,
      mode: "force", from: null, modeT0: 0, W: 0, H: 0, dpr: 1, view: { k: 1, cx: 0, cy: 0 }, userView: false,
      hover: -1, sel: -1, pointers: new Map(), drag: null, dirty: true, raf: 0, packets: [], pulses: [], prevP: null, prevG: null,
      seenEv: new WeakSet(), t: 0, pos: null, ctrl: null };
    S.pos = graph.nodes.map(n => n.wide.slice()); S.tall = false;
    readColors();
    if (opts.controls !== false) makeControls();
    bindInput();
    S.ro = new ResizeObserver(() => resize());
    S.ro.observe(el);
    if ("IntersectionObserver" in window) { S.io = new IntersectionObserver(() => schedule()); S.io.observe(el); }
    resize();
    return api;
  }

  function makeControls() {
    const box = document.createElement("div");
    box.style.cssText = "position:absolute;top:8px;right:8px;z-index:2;display:flex;gap:0;border:1px solid var(--panel-line,#22333f);border-radius:6px;overflow:hidden;" +
      "background:var(--panel,rgba(10,18,24,.9));font:600 11px system-ui,sans-serif";
    const mk = (m, label) => {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = label; b.dataset.mode = m;
      b.style.cssText = "border:0;padding:5px 10px;cursor:pointer;background:transparent;color:var(--ink-2,#9fb3bd);font:inherit";
      b.addEventListener("click", ev => { ev.stopPropagation(); setMode(m); });
      box.appendChild(b); return b;
    };
    S.ctrl = { box, force: mk("force", "Network"), geo: mk("geo", "Geographic") };
    S.el.appendChild(box);
    syncControls();
  }
  function syncControls() {
    if (!S || !S.ctrl) return;
    for (const m of ["force", "geo"]) {
      const b = S.ctrl[m], on = S.mode === m;
      b.style.background = on ? "var(--ai-low,#103d3e)" : "transparent";
      b.style.color = on ? "var(--ai,#2fd3c4)" : "var(--ink-2,#9fb3bd)";
    }
  }

  function destroy() {
    if (!S) return;
    cancelAnimationFrame(S.raf);
    if (S.ro) S.ro.disconnect(); if (S.io) S.io.disconnect();
    for (const [t, f, o] of S.listeners || []) t.removeEventListener(f.type, f.fn, o);
    S.cv.remove(); S.tip.remove(); if (S.ctrl) S.ctrl.box.remove();
    S = null;
  }

  // ---------- view transform ----------
  function target(i) { const n = S.g.nodes[i]; return S.mode === "geo" ? [n.gx, n.gy] : S.tall ? n.tall : n.wide; }
  // re-target (mode or aspect changed): animate nodes from where they are, refit unless the user moved the view
  function retarget(animate) {
    if (animate) { S.from = S.pos.map(p => p.slice()); S.modeT0 = performance.now(); }
    S.bb = bounds();
    if (!S.userView) fit();
  }
  function bounds() {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let i = 0; i < S.g.nodes.length; i++) { const n = S.g.nodes[i], [x, y] = target(i);
      if (n.users < 2e4 && n.cabN < 2) continue;        // uninhabited specks (Antarctica, S. Georgia…) don't stretch the fit
      if (x - n.r < x0) x0 = x - n.r; if (x + n.r > x1) x1 = x + n.r;
      if (y - n.r < y0) y0 = y - n.r; if (y + n.r > y1) y1 = y + n.r;
    }
    return [x0, y0, x1, y1];
  }
  function fitScale() {
    const [x0, y0, x1, y1] = S.bb || (S.bb = bounds());
    const pad = S.W < 600 ? 10 : 28;
    return Math.min((S.W - pad * 2) / (x1 - x0), (S.H - pad * 2 - (S.ctrl ? 26 : 0)) / (y1 - y0));
  }
  function fit() {
    const [x0, y0, x1, y1] = S.bb || (S.bb = bounds());
    S.view.k = 1; S.view.cx = (x0 + x1) / 2; S.view.cy = (y0 + y1) / 2 - (S.ctrl ? 13 / fitScale() : 0);
    S.userView = false;
  }
  const scale = () => fitScale() * S.view.k;
  function toScreen(x, y, s) { return [(x - S.view.cx) * s + S.W / 2, (y - S.view.cy) * s + S.H / 2]; }
  function toWorld(px, py) { const s = scale(); return [(px - S.W / 2) / s + S.view.cx, (py - S.H / 2) / s + S.view.cy]; }

  function resize() {
    if (!S) return;
    const r = S.el.getBoundingClientRect();
    const W = Math.round(r.width), H = Math.round(r.height), dpr = Math.min(window.devicePixelRatio || 1, 3);
    if (W !== S.W || H !== S.H || dpr !== S.dpr) {
      const was = S.W; S.W = W; S.H = H; S.dpr = dpr;
      S.cv.width = Math.max(1, Math.round(W * dpr)); S.cv.height = Math.max(1, Math.round(H * dpr));
      const tall = H > W * 1.15;
      if (tall !== S.tall) { S.tall = tall; retarget(was > 0 && S.mode === "force"); }
      else if (!S.userView) fit();
    }
    readColors();
    schedule();
  }

  function visible() { return S && S.el.isConnected && S.W > 0 && S.H > 0 && S.el.getClientRects().length > 0 && document.visibilityState !== "hidden"; }
  function schedule() { if (!S) return; S.dirty = true; if (!S.raf && visible()) S.raf = requestAnimationFrame(frame); }

  function setMode(m) {
    if (!S || (m !== "force" && m !== "geo") || m === S.mode) return;
    S.mode = m; retarget(true);
    syncControls(); schedule();
  }

  function select(iso) { if (!S) return; S.sel = iso != null && S.g.idx[iso] != null ? S.g.idx[iso] : -1; schedule(); }

  // ---------- input ----------
  function bindInput() {
    const L = S.listeners = [];
    const on = (t, type, fn, o) => { t.addEventListener(type, fn, o); L.push([t, { type, fn }, o]); };
    const cv = S.cv;
    const local = ev => { const r = cv.getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; };
    const zoomAt = (px, py, f) => {
      const k1 = clamp(S.view.k * f, 0.6, 14); if (k1 === S.view.k) return;
      const [wx, wy] = toWorld(px, py);
      S.view.k = k1;
      const s = scale();
      S.view.cx = wx - (px - S.W / 2) / s; S.view.cy = wy - (py - S.H / 2) / s;
      S.userView = true; schedule();
    };
    on(cv, "wheel", ev => { ev.preventDefault(); const [x, y] = local(ev); zoomAt(x, y, Math.exp(-ev.deltaY * (ev.deltaMode === 1 ? 0.05 : 0.0016))); }, { passive: false });
    on(cv, "dblclick", ev => { ev.preventDefault(); fit(); schedule(); });
    on(cv, "pointerdown", ev => {
      cv.setPointerCapture(ev.pointerId);
      const p = local(ev); S.pointers.set(ev.pointerId, p);
      if (S.pointers.size === 1) S.drag = { x: p[0], y: p[1], moved: 0, cx: S.view.cx, cy: S.view.cy };
      else if (S.pointers.size === 2) { const [a, b] = [...S.pointers.values()]; S.pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), mx: (a[0] + b[0]) / 2, my: (a[1] + b[1]) / 2 }; if (S.drag) S.drag.moved = 99; }
      cv.style.cursor = "grabbing";
    });
    on(cv, "pointermove", ev => {
      const p = local(ev);
      if (S.pointers.has(ev.pointerId)) {
        S.pointers.set(ev.pointerId, p);
        if (S.pointers.size >= 2 && S.pinch) {
          const [a, b] = [...S.pointers.values()];
          const d = Math.hypot(a[0] - b[0], a[1] - b[1]), mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
          const s = scale(); S.view.cx -= (mx - S.pinch.mx) / s; S.view.cy -= (my - S.pinch.my) / s;
          if (S.pinch.d > 0) zoomAt(mx, my, d / S.pinch.d);
          S.pinch = { d, mx, my }; S.userView = true; schedule();
          hideTip(); return;
        }
        if (S.drag) {
          const dx = p[0] - S.drag.x, dy = p[1] - S.drag.y;
          S.drag.moved = Math.max(S.drag.moved, Math.hypot(dx, dy));
          if (S.drag.moved > 3) { const s = scale(); S.view.cx = S.drag.cx - dx / s; S.view.cy = S.drag.cy - dy / s; S.userView = true; hideTip(); schedule(); return; }
        }
      }
      if (ev.pointerType === "mouse" || !S.pointers.size) hoverAt(p[0], p[1]);
    });
    const up = ev => {
      const p = local(ev);
      const wasDrag = S.drag && S.drag.moved > 3;
      const single = S.pointers.size === 1;
      S.pointers.delete(ev.pointerId);
      if (S.pointers.size < 2) S.pinch = null;
      if (!S.pointers.size) { cv.style.cursor = "grab"; if (single && !wasDrag && ev.type === "pointerup") click(p[0], p[1], ev.pointerType); S.drag = null; }
      else { const q = [...S.pointers.values()][0]; S.drag = { x: q[0], y: q[1], moved: 99, cx: S.view.cx, cy: S.view.cy }; }
    };
    on(cv, "pointerup", up); on(cv, "pointercancel", up);
    on(cv, "pointerleave", ev => { if (ev.pointerType === "mouse") { S.hover = -1; hideTip(); schedule(); } });
    on(document, "visibilitychange", () => schedule());
  }

  function hit(px, py) {
    const s = scale(); let best = -1, bd = Infinity;
    S.g.nodes.forEach((n, i) => {
      const [x, y] = toScreen(S.pos[i][0], S.pos[i][1], s);
      const rr = Math.max(nodeR(n, s) + 3, 7), d = Math.hypot(px - x, py - y);
      if (d < rr && d - rr < bd) { bd = d - rr; best = i; }
    });
    return best;
  }
  function hoverAt(px, py) {
    const i = hit(px, py);
    if (i !== S.hover) { S.hover = i; schedule(); }
    S.cv.style.cursor = i >= 0 ? "pointer" : "grab";
    if (i >= 0) showTip(i, px, py); else hideTip();
  }
  function click(px, py, ptype) {
    const i = hit(px, py);
    if (ptype !== "mouse") { S.hover = i; if (i >= 0) showTip(i, px, py); else hideTip(); }
    if (i < 0) return;
    S.sel = i; schedule();
    if (typeof S.opts.onSelect === "function") S.opts.onSelect(S.g.nodes[i].iso);
  }
  function hideTip() { if (S) S.tip.style.display = "none"; }

  function cOf(G, i) { if (!G || !G.C) return null; const j = G.idx ? G.idx[S.g.nodes[i].iso] : i; return j != null ? G.C[j] || null : null; }
  function hxList(G, c) {
    if (!c || !c.hx) return [];
    return Object.entries(c.hx).filter(([, v]) => v && (typeof v !== "number" || !G || v > G.day));
  }
  function layerVal(c, k) { if (!c) return 0; if (c.L && num(c.L[k])) return clamp(c.L[k], 0, 1); return k === "web" ? clamp(c.p || 0, 0, 1) : 0; }

  function showTip(i, px, py) {
    const n = S.g.nodes[i], G = S.getG(), c = cOf(G, i);
    const fw = c && num(c.fw) ? c.fw : n.fw;
    let h = `<div style="font-weight:700;font-size:13px;margin-bottom:3px">${esc(n.n)}</div>`;
    const kv = (k, v, col) => `<div style="display:flex;justify-content:space-between;gap:10px"><span style="color:var(--ink-2,#9fb3bd)">${k}</span><b style="${col ? "color:" + col : ""}">${v}</b></div>`;
    h += kv("Internet users", fmtUsers(n.users) + ` <span style="color:var(--ink-3,#657a85);font-weight:400">(${Math.round(n.net)}%)</span>`);
    h += kv("Cable links", n.cabN ? `${n.cabN} <span style="color:var(--ink-3,#657a85);font-weight:400">· ${n.deg} cables</span>` : "none");
    if (n.nbN) h += kv("Land borders", n.nbN);
    h += kv("Firewall", fw > 0.02 ? Math.round(fw * 100) + "%" : "open", fw >= 0.5 ? "var(--fw-hi,#ff2d2d)" : fw > 0.02 ? "var(--fw-lo,#ff9a3d)" : "");
    if (c) {
      h += `<div style="margin-top:5px">`;
      for (let l = 0; l < 4; l++) {
        const v = layerVal(c, LAYERS[l][0]);
        h += `<div style="display:flex;align-items:center;gap:6px;margin-top:2px"><span style="flex:0 0 104px;white-space:nowrap;color:var(--ink-2,#9fb3bd)">${LAYERS[l][1]}</span>` +
          `<span style="flex:1;height:5px;background:var(--track,#0f1d26);border-radius:3px;overflow:hidden"><i style="display:block;height:100%;width:${(v * 100).toFixed(1)}%;background:${rgba(S.col.layer[l], 1)}"></i></span>` +
          `<span style="flex:0 0 30px;text-align:right">${Math.round(v * 100)}%</span></div>`;
      }
      h += `</div>`;
      const hx = hxList(G, c);
      if (hx.length) h += `<div style="margin-top:5px;color:var(--hx,#ff6b3d)">${hx.map(([k, v]) => esc(HX_N[k] || k) + (num(v) && G && v > G.day ? ` · ${(v - G.day) * (typeof DPT !== "undefined" ? DPT : 1)} d` : "")).join("<br>")}</div>`;
    }
    S.tip.innerHTML = h;
    S.tip.style.display = "block";
    const tw = S.tip.offsetWidth, th = S.tip.offsetHeight;
    let x = px + 14, y = py + 14;
    if (x + tw > S.W - 4) x = px - tw - 14;
    if (y + th > S.H - 4) y = py - th - 14;
    S.tip.style.left = clamp(x, 4, Math.max(4, S.W - tw - 4)) + "px";
    S.tip.style.top = clamp(y, 4, Math.max(4, S.H - th - 4)) + "px";
  }

  // ---------- events → animations ----------
  function ingest(G, now) {
    const g = S.g;
    if (!G || !G.C) { S.prevP = null; S.prevG = null; return; }
    if (S.prevG !== G) { S.prevG = G; S.prevP = new Float64Array(g.nodes.length); g.nodes.forEach((n, i) => { const c = cOf(G, i); S.prevP[i] = c ? c.p || 0 : 0; }); S.packets = []; S.pulses = []; }
    const handled = new Set();
    const take = e => {
      if (!e || typeof e !== "object" || S.seenEv.has(e)) return;
      S.seenEv.add(e);
      if (e.k !== "seed" || g.idx[e.to] == null) return;
      const to = g.idx[e.to], from = e.from != null && g.idx[e.from] != null ? g.idx[e.from] : -1;
      handled.add(to);
      launch(from, to, e.via || "cable", now);
    };
    if (Array.isArray(G.recent)) for (const e of G.recent) take(e);
    if (Array.isArray(G.ev)) for (const e of G.ev) take(e);
    for (let i = 0; i < g.nodes.length; i++) {
      const c = cOf(G, i), p = c ? c.p || 0 : 0;
      if (S.prevP[i] < 0.002 && p >= 0.002 && !handled.has(i)) {
        // newly reached: find the reached neighbour it most likely came from
        let best = -1, bw = 0;
        for (const ei of g.nodes[i].edges) {
          const e = g.edges[ei], j = e.a === i ? e.b : e.a, cj = cOf(G, j), pj = cj ? cj.p || 0 : 0;
          const w = pj * (e.t === "cab" ? 1 + e.k * 0.2 : 0.8);
          if (S.prevP[j] >= 0.002 && w > bw) { bw = w; best = j; }
        }
        launch(best, i, best >= 0 ? (edgeBetween(best, i) || {}).t === "nb" ? "land" : "cable" : (G.cov > 0 ? "sat" : "air"), now);
      }
      S.prevP[i] = p;
    }
  }
  function edgeBetween(a, b) { for (const ei of S.g.nodes[a].edges) { const e = S.g.edges[ei]; if ((e.a === a && e.b === b) || (e.a === b && e.b === a)) return e; } return null; }
  function launch(from, to, via, now) {
    if (S.packets.length > 60) S.packets.shift();
    if (from >= 0 && from !== to) S.packets.push({ from, to, via, t0: now, dur: 1100 });
    else S.pulses.push({ i: to, via, t0: now });
    if (S.pulses.length > 80) S.pulses.shift();
  }

  // ---------- drawing ----------
  function nodeR(n, s) { return Math.max(1.7, n.r * s * 0.92); }

  function updatePositions(now) {
    const nodes = S.g.nodes;
    if (S.from) {
      const t = clamp((now - S.modeT0) / 650, 0, 1), e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      for (let i = 0; i < nodes.length; i++) {
        const [tx, ty] = target(i);
        S.pos[i][0] = S.from[i][0] + (tx - S.from[i][0]) * e; S.pos[i][1] = S.from[i][1] + (ty - S.from[i][1]) * e;
      }
      if (t >= 1) S.from = null;
      return true;
    }
    for (let i = 0; i < nodes.length; i++) { const t = target(i); S.pos[i][0] = t[0]; S.pos[i][1] = t[1]; }
    return false;
  }

  function frame(now) {
    S.raf = 0;
    if (!visible()) return;                       // hidden: stop; ResizeObserver/IntersectionObserver/render() restart us
    const G = S.getG();
    ingest(G, now);
    let anim = updatePositions(now);
    anim = draw(G, now) || anim;
    S.dirty = false;
    if (anim) S.raf = requestAnimationFrame(frame);
  }

  // control point of the (slightly bent) curve for an edge in screen space
  function ctrlPt(ax, ay, bx, by, bend) {
    const mx = (ax + bx) / 2, my = (ay + by) / 2, dx = bx - ax, dy = by - ay;
    return [mx - dy * bend, my + dx * bend];
  }

  function draw(G, now) {
    const { ctx, col, g } = S, W = S.W, H = S.H, s = scale(), dpr = S.dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = col.bgS; ctx.fillRect(0, 0, W, H);
    // faint radial glow in the middle so the graph floats
    const rg = ctx.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, Math.max(W, H) * 0.7);
    rg.addColorStop(0, rgba(col.net1, 0.35)); rg.addColorStop(1, rgba(col.bg, 0));
    ctx.fillStyle = rg; ctx.fillRect(0, 0, W, H);

    const nodes = g.nodes, N = nodes.length;
    const sx = new Float64Array(N), sy = new Float64Array(N), sr = new Float64Array(N);
    for (let i = 0; i < N; i++) { const p = toScreen(S.pos[i][0], S.pos[i][1], s); sx[i] = p[0]; sy[i] = p[1]; sr[i] = nodeR(nodes[i], s); }
    const halfW = 180 * s * (S.mode === "force" && S.tall ? 0.45 : 1);                        // longitude span/2 in px: links longer than this wrap round the Pacific
    const [bx0, , bx1] = S.bb;
    const leftX = toScreen(bx0 - 6, 0, s)[0], rightX = toScreen(bx1 + 6, 0, s)[0];

    // per-node game state
    const P = new Float64Array(N), cut = new Uint8Array(N), hxN = new Uint8Array(N), C = new Array(N);
    if (G && G.C) for (let i = 0; i < N; i++) {
      const c = C[i] = cOf(G, i); if (!c) continue;
      P[i] = c.p || 0;
      const hx = hxList(G, c); hxN[i] = hx.length;
      if (hx.some(([k]) => k === "cut" || k === "ban")) cut[i] = 1;
    }
    const z = clamp(s / 3.2, 0.45, 2.2);          // stroke/size scale with zoom
    const ze = clamp(s / 3.2, 0.45, 1.25);        // edges thicken less
    const hov = S.hover, sel = S.sel;

    // ---- edges ----
    const buckets = [[], [], [], [], []], ai = [], cutE = [], nbE = [], hiE = [], wrapE = [];
    for (let ei = 0; ei < g.edges.length; ei++) {
      const e = g.edges[ei];
      if (e.a === hov || e.b === hov || e.a === sel || e.b === sel) hiE.push(e);
      if (cut[e.a] || cut[e.b]) { cutE.push(e); continue; }
      if (e.t === "nb") { nbE.push(e); continue; }
      if (Math.abs(sx[e.a] - sx[e.b]) > halfW) { wrapE.push(e); continue; }
      if (P[e.a] > 0.02 && P[e.b] > 0.02) ai.push(e);
      buckets[e.k <= 1 ? 0 : e.k <= 2 ? 1 : e.k <= 4 ? 2 : e.k <= 8 ? 3 : 4].push(e);
    }
    const pathEdges = list => {
      ctx.beginPath();
      for (const e of list) {
        const ax = sx[e.a], ay = sy[e.a], bx = sx[e.b], by = sy[e.b];
        if (Math.abs(ax - bx) > halfW) {          // trans-Pacific: two stubs leaving the edges of the world
          const [l, r] = ax < bx ? [e.a, e.b] : [e.b, e.a];
          const ym = (sy[l] + sy[r]) / 2;
          ctx.moveTo(sx[l], sy[l]); ctx.quadraticCurveTo(sx[l] - 20 * ze, (sy[l] + ym) / 2, leftX, ym);
          ctx.moveTo(sx[r], sy[r]); ctx.quadraticCurveTo(sx[r] + 20 * ze, (sy[r] + ym) / 2, rightX, ym);
          continue;
        }
        if (e.t === "nb") { ctx.moveTo(ax, ay); ctx.lineTo(bx, by); continue; }
        const [cx, cy] = ctrlPt(ax, ay, bx, by, e.bend);
        ctx.moveTo(ax, ay); ctx.quadraticCurveTo(cx, cy, bx, by);
      }
    };
    ctx.lineCap = "round";
    // land fibre: thin, green-ish, under the cables
    ctx.setLineDash([]);
    pathEdges(nbE); ctx.strokeStyle = rgba(col.land, 0.22); ctx.lineWidth = 0.8 * ze; ctx.stroke();
    // cables: glow pass (additive) then core pass, width ∝ cable count
    ctx.globalCompositeOperation = "lighter";
    const BW = [0.4, 0.75, 1.15, 1.8, 2.7], BA = [0.11, 0.17, 0.26, 0.36, 0.5];
    if (wrapE.length) { pathEdges(wrapE); ctx.strokeStyle = rgba(col.vcable, 0.13); ctx.lineWidth = 0.9 * ze; ctx.stroke(); }
    for (let b = 0; b < 5; b++) {
      if (!buckets[b].length) continue;
      pathEdges(buckets[b]);
      ctx.globalCompositeOperation = "lighter";
      ctx.strokeStyle = rgba(col.cable, BA[b] * 0.12); ctx.lineWidth = BW[b] * ze * 4.5; ctx.stroke();
      ctx.globalCompositeOperation = "source-over";
      ctx.strokeStyle = rgba(col.vcable, BA[b] * 1.1); ctx.lineWidth = BW[b] * ze; ctx.stroke();
    }
    if (ai.length) {                                 // links between two countries you're in light up in AI teal
      pathEdges(ai); ctx.strokeStyle = rgba(col.ai, 0.16); ctx.lineWidth = 4 * ze; ctx.stroke();
      ctx.strokeStyle = rgba(col.ai, 0.55); ctx.lineWidth = 1.1 * ze; ctx.stroke();
    }
    ctx.globalCompositeOperation = "source-over";
    if (cutE.length) {                               // counter-measures: shut-down links dashed and dimmed
      pathEdges(cutE); ctx.setLineDash([4 * ze, 4 * ze]); ctx.strokeStyle = rgba(col.hx, 0.45); ctx.lineWidth = 1 * ze; ctx.stroke(); ctx.setLineDash([]);
    }
    if (hiE.length) { pathEdges(hiE); ctx.strokeStyle = rgba(col.sel, 0.7); ctx.lineWidth = 1.1 * ze; ctx.stroke(); }

    // ---- satellite orbit ----
    let anim = false;
    const cov = G && num(G.cov) ? clamp(G.cov, 0, 1) : 0;
    if (cov > 0) {
      anim = true;
      const ocx = W / 2 + (((S.bb[0] + S.bb[2]) / 2) - S.view.cx) * s, ocy = H / 2 + (((S.bb[1] + S.bb[3]) / 2) - S.view.cy) * s;
      const orx = (S.bb[2] - S.bb[0]) / 2 * s + 10, ory = (S.bb[3] - S.bb[1]) / 2 * s + 10;
      ctx.beginPath(); ctx.ellipse(ocx, ocy, orx, ory, 0, 0, TAU);
      ctx.setLineDash([2, 5]); ctx.strokeStyle = rgba(col.vsat, 0.25 + 0.35 * cov); ctx.lineWidth = 1 + 1.5 * cov; ctx.stroke(); ctx.setLineDash([]);
      const nS = Math.max(3, Math.round(4 + cov * 28)), sats = [];
      for (let k = 0; k < nS; k++) { const a = (k / nS) * TAU + now / 9000; sats.push([ocx + Math.cos(a) * orx, ocy + Math.sin(a) * ory]); }
      ctx.beginPath();
      for (let i = 0; i < N; i++) {
        const c = C[i]; const covered = c && num(c.sat) ? c.sat > 0 : hash01(nodes[i].iso + "sat") < cov;
        if (!covered) continue;
        let bd = Infinity, bs = null;
        for (const q of sats) { const d = (q[0] - sx[i]) ** 2 + (q[1] - sy[i]) ** 2; if (d < bd) { bd = d; bs = q; } }
        ctx.moveTo(sx[i], sy[i]); ctx.lineTo(bs[0], bs[1]);
      }
      ctx.strokeStyle = rgba(col.vsat, 0.16); ctx.lineWidth = 0.8; ctx.stroke();
      for (const q of sats) {
        ctx.beginPath(); ctx.arc(q[0], q[1], 4.5, 0, TAU); ctx.fillStyle = rgba(col.vsat, 0.25); ctx.fill();
        ctx.beginPath(); ctx.arc(q[0], q[1], 2, 0, TAU); ctx.fillStyle = col.satS; ctx.fill();
      }
    }

    // ---- nodes ----
    // hub halos (cable landing hubs glow), additive
    ctx.globalCompositeOperation = "lighter";
    const halo = haloSprite();
    for (let i = 0; i < N; i++) {
      const n = nodes[i]; if (n.hub < 0.2) continue;
      const R = sr[i] + (6 + 26 * n.hub) * z;
      ctx.globalAlpha = clamp(0.35 * n.hub, 0, 1);
      ctx.drawImage(halo, sx[i] - R, sy[i] - R, R * 2, R * 2);
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    const order = [...nodes.keys()].sort((a, b) => nodes[a].r - nodes[b].r).reverse();  // big first, small on top
    for (const i of order) {
      const n = nodes[i], x = sx[i], y = sy[i], r = sr[i], c = C[i];
      if (x < -60 || x > W + 60 || y < -60 || y > H + 60) continue;
      // body: blue by connectivity, mixing to AI teal by presence
      let fill = mix(col.net1, col.net2, clamp(n.net / 100, 0, 1));
      fill = mix(fill, col.ai, clamp(P[i] * 1.4, 0, 1) * 0.85);
      ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.fillStyle = rgba(fill, 1); ctx.fill();
      // automated share as a magenta core
      const a = c && num(c.a) ? clamp(c.a, 0, 1) : 0;
      if (a > 0.005) { ctx.beginPath(); ctx.arc(x, y, r * Math.sqrt(a) * 0.75, 0, TAU); ctx.fillStyle = rgba(col.auto, 0.8); ctx.fill(); }
      ctx.lineWidth = Math.max(0.6, 0.9 * z);
      ctx.strokeStyle = rgba(mix(col.vcable, col.ink, 0.3), 0.35 + 0.5 * n.hub);
      if (cut[i]) { ctx.setLineDash([3, 2]); ctx.strokeStyle = col.hxS; }
      ctx.stroke(); ctx.setLineDash([]);
      // four layer ring segments
      const w = clamp(r * 0.3, 1.6, 5.5);
      let R = r + 1.4 + w / 2;
      if (c) {
        const gapA = 0.22, seg = TAU / 4;
        for (let l = 0; l < 4; l++) {
          const v = layerVal(c, LAYERS[l][0]);
          const a0 = -Math.PI / 2 + l * seg + gapA / 2, span = seg - gapA;
          if (r >= 4) { ctx.beginPath(); ctx.arc(x, y, R, a0, a0 + span); ctx.strokeStyle = rgba(col.layer[l], 0.16); ctx.lineWidth = w; ctx.lineCap = "butt"; ctx.stroke(); }
          if (v > 0.003) { ctx.beginPath(); ctx.arc(x, y, R, a0, a0 + span * Math.max(v, 0.04)); ctx.strokeStyle = rgba(col.layer[l], 0.95); ctx.lineWidth = w; ctx.lineCap = "butt"; ctx.stroke(); }
        }
        R += w / 2;
      } else R = r;
      // firewall shell: brick-dashed wall, thickness ∝ fw
      const fw = c && num(c.fw) ? c.fw : n.fw;
      if (fw > 0.04) {
        const t = (0.8 + 6.5 * fw) * clamp(z, 0.6, 1.4), Rf = R + 1.5 + t / 2;
        const fc = mix(col.fwlo, col.fwhi, clamp(fw * 1.3 - 0.2, 0, 1));
        ctx.beginPath(); ctx.arc(x, y, Rf, 0, TAU);
        ctx.lineCap = "butt"; ctx.lineWidth = t; ctx.strokeStyle = rgba(fc, 0.18 + 0.25 * fw); ctx.stroke();
        const brick = Math.max(3, Rf * 0.42);
        ctx.setLineDash([brick, Math.max(1.2, brick * 0.18)]); ctx.strokeStyle = rgba(fc, 0.55 + 0.4 * fw); ctx.lineWidth = t * 0.75; ctx.stroke(); ctx.setLineDash([]);
        if (fw >= 0.5) { ctx.beginPath(); ctx.arc(x, y, Rf + t / 2 + 0.6, 0, TAU); ctx.lineWidth = 0.8; ctx.strokeStyle = rgba(fc, 0.8); ctx.stroke(); }
        R = Rf + t / 2;
      }
      // other active counter-measures: a small hazard pip
      if (hxN[i] && !cut[i]) {
        const px = x + R * 0.72, py = y - R * 0.72;
        ctx.beginPath(); ctx.arc(px, py, Math.max(2.4, 3 * z), 0, TAU); ctx.fillStyle = col.hxS; ctx.fill();
      }
      n._R = R;
      if (i === sel || i === hov) { ctx.beginPath(); ctx.arc(x, y, R + 3, 0, TAU); ctx.lineWidth = 1.5; ctx.strokeStyle = rgba(col.sel, i === sel ? 0.95 : 0.6); ctx.stroke(); }
    }

    // ---- packets and pulses ----
    ctx.globalCompositeOperation = "lighter";
    S.packets = S.packets.filter(pk => now - pk.t0 < pk.dur + 900);
    for (const pk of S.packets) {
      anim = true;
      const t = clamp((now - pk.t0) / pk.dur, 0, 1), e = edgeBetween(pk.from, pk.to);
      const c3 = col.via[pk.via] || col.ai;
      let ax = sx[pk.from], ay = sy[pk.from], bx = sx[pk.to], by = sy[pk.to];
      let cx, cy;
      const wrap = Math.abs(ax - bx) > halfW;
      if (wrap) { cx = (ax + bx) / 2; cy = Math.min(ay, by) - 60 * z; }
      else if (e && e.t === "cab") { const sg = e.a === pk.from ? 1 : -1; [cx, cy] = ctrlPt(ax, ay, bx, by, e.bend * sg); }
      else if (!e) { [cx, cy] = ctrlPt(ax, ay, bx, by, 0.25); }   // air / open: a higher arc
      else { cx = (ax + bx) / 2; cy = (ay + by) / 2; }
      const at = u => [(1 - u) * (1 - u) * ax + 2 * (1 - u) * u * cx + u * u * bx, (1 - u) * (1 - u) * ay + 2 * (1 - u) * u * cy + u * u * by];
      if (t < 1) {
        ctx.beginPath();
        const t0 = Math.max(0, t - 0.28); let q = at(t0); ctx.moveTo(q[0], q[1]);
        for (let u = t0; u <= t; u += 0.04) { q = at(u); ctx.lineTo(q[0], q[1]); }
        q = at(t); ctx.lineTo(q[0], q[1]);
        ctx.strokeStyle = rgba(c3, 0.55); ctx.lineWidth = 2.2 * z + 1; ctx.stroke();
        ctx.beginPath(); ctx.arc(q[0], q[1], 2.5 * z + 1.5, 0, TAU); ctx.fillStyle = rgba(mix(c3, col.ink, 0.5), 1); ctx.fill();
      } else {
        const u = (now - pk.t0 - pk.dur) / 900;
        ctx.beginPath(); ctx.arc(bx, by, sr[pk.to] + 3 + u * 26 * z, 0, TAU); ctx.strokeStyle = rgba(c3, 0.8 * (1 - u)); ctx.lineWidth = 2; ctx.stroke();
      }
    }
    S.pulses = S.pulses.filter(pu => now - pu.t0 < 1300);
    for (const pu of S.pulses) {
      anim = true;
      const u = (now - pu.t0) / 1300, c3 = col.via[pu.via] || col.ai;
      ctx.beginPath(); ctx.arc(sx[pu.i], sy[pu.i], sr[pu.i] + 3 + u * 30 * z, 0, TAU); ctx.strokeStyle = rgba(c3, 0.85 * (1 - u)); ctx.lineWidth = 2; ctx.stroke();
    }
    ctx.globalCompositeOperation = "source-over";

    // ---- labels: biggest by users + cable hubs, more as you zoom in; skip overlaps ----
    const placed = [];
    const fs = clamp(10 + 1.2 * (z - 1) * 2, 9, 13.5);
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const cand = [];
    for (let i = 0; i < N; i++) {
      const n = nodes[i];
      if (n.lab || sr[i] * 1 >= 7.5 || i === hov || i === sel || (S.view.k > 2.2 && sr[i] > 4)) cand.push(i);
    }
    cand.sort((a, b) => (b === sel || b === hov) - (a === sel || a === hov) || nodes[b].users - nodes[a].users);
    for (const i of cand) {
      const n = nodes[i], big = n.rankU < 12;
      const f = big ? fs + 1.5 : fs;
      ctx.font = `${big ? 700 : 600} ${f}px system-ui, -apple-system, Segoe UI, sans-serif`;
      const txt = n.short;
      const tw = ctx.measureText(txt).width;
      const ly = sy[i] + (n._R || sr[i]) + 2;
      const box = [sx[i] - tw / 2 - 2, ly - 1, sx[i] + tw / 2 + 2, ly + f + 1];
      if (box[2] < 0 || box[0] > W || box[3] < 0 || box[1] > H) continue;
      if (i !== hov && i !== sel && placed.some(b => !(box[2] < b[0] || box[0] > b[2] || box[3] < b[1] || box[1] > b[3]))) continue;
      placed.push(box);
      ctx.lineWidth = 3; ctx.strokeStyle = rgba(col.bg, 0.85); ctx.lineJoin = "round"; ctx.strokeText(txt, sx[i], ly);
      ctx.fillStyle = P[i] > 0.02 ? rgba(mix(col.ink, col.ai, 0.35), 1) : rgba(col.ink, big ? 0.95 : 0.78);
      ctx.fillText(txt, sx[i], ly);
    }

    // ---- legend (bottom-left) ----
    drawLegend(G, cov);
    return anim;
  }

  function drawLegend(G, cov) {
    const { ctx, col } = S, small = S.W < 520;
    const items = [["line", col.vcable, "Submarine cables"], ["line", col.land, "Land fibre"], ["wall", col.fwhi, "Firewall"]];
    if (G && G.C) {
      items.push(["seg", col.layer[0], "Web"], ["seg", col.layer[1], "Social"], ["seg", col.layer[2], "LAN"], ["seg", col.layer[3], "Critical"]);
      if (cov > 0) items.push(["dot", col.vsat, "Satellites"]);
      items.push(["dash", col.hx, "Cut"]);
    }
    ctx.font = `600 ${small ? 9.5 : 10.5}px system-ui, sans-serif`;
    ctx.textBaseline = "middle"; ctx.textAlign = "left";
    let x = 10, y = S.H - 12;
    const rowH = small ? 14 : 16;
    // wrap into rows that fit the width
    const rows = [[]]; let rw = 0;
    for (const it of items) { const w = 18 + ctx.measureText(it[2]).width + 12; if (rw + w > S.W - 20 && rows[rows.length - 1].length) { rows.push([]); rw = 0; } rows[rows.length - 1].push([it, w]); rw += w; }
    y = S.H - 10 - (rows.length - 1) * rowH;
    const maxW = Math.max(...rows.map(r => r.reduce((a, b) => a + b[1], 0)));
    ctx.fillStyle = rgba(col.bg, 0.72); ctx.fillRect(4, y - rowH / 2 - 3, maxW + 8, rows.length * rowH + 6);
    for (const row of rows) {
      x = 10;
      for (const [[k, c, t], w] of row) {
        ctx.beginPath();
        if (k === "line") { ctx.moveTo(x, y); ctx.lineTo(x + 13, y); ctx.strokeStyle = rgba(c, 0.9); ctx.lineWidth = 2; ctx.stroke(); }
        else if (k === "dash") { ctx.setLineDash([3, 2]); ctx.moveTo(x, y); ctx.lineTo(x + 13, y); ctx.strokeStyle = rgba(c, 0.9); ctx.lineWidth = 1.5; ctx.stroke(); ctx.setLineDash([]); }
        else if (k === "wall") { ctx.setLineDash([3, 1]); ctx.arc(x + 6, y, 5, 0, TAU); ctx.strokeStyle = rgba(c, 0.95); ctx.lineWidth = 2.5; ctx.stroke(); ctx.setLineDash([]); }
        else if (k === "seg") { ctx.arc(x + 6, y, 4.5, -Math.PI / 2, Math.PI * 0.7); ctx.strokeStyle = rgba(c, 1); ctx.lineWidth = 2.5; ctx.stroke(); }
        else { ctx.arc(x + 6, y, 3, 0, TAU); ctx.fillStyle = rgba(c, 1); ctx.fill(); }
        ctx.fillStyle = rgba(col.ink2, 1); ctx.fillText(t, x + 17, y + 0.5);
        x += w;
      }
      y += rowH;
    }
  }

  // one radial-gradient sprite for every hub halo (much cheaper than a gradient per node per frame)
  function haloSprite() {
    if (S.halo && S.haloCol === S.col.cableS) return S.halo;
    const c = document.createElement("canvas"); c.width = c.height = 128;
    const x = c.getContext("2d"), gr = x.createRadialGradient(64, 64, 6, 64, 64, 64);
    gr.addColorStop(0, rgba(S.col.cable, 0.9)); gr.addColorStop(0.35, rgba(S.col.cable, 0.35)); gr.addColorStop(1, rgba(S.col.cable, 0));
    x.fillStyle = gr; x.fillRect(0, 0, 128, 128);
    S.halo = c; S.haloCol = S.col.cableS; return c;
  }

  function render() { if (S) schedule(); }
  function repaint() { if (S) { readColors(); schedule(); } }

  const api = { mount, render, resize, setMode, getMode: () => S ? S.mode : "force", select, repaint, destroy,
    _state: () => S, _drawMs: () => { if (!S) return 0; const t = performance.now(); draw(S.getG(), t); return performance.now() - t; } };
  window.NetGraph = api;
})();
