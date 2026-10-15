/* Singularity v2: the Evolve screen's upgrade graph. Classic script, no DOM work until mount.
   Needs the sim.js globals TRAITS, TRACKS, STAT_KEYS, stats(G, previewId), fxLines(G, trait), traitState(G, t), why(G, t).

   Tree.mount(container, { G: () => state, buy: id => bool, onChange: () => void })
   Tree.render()   cheap; call it every tick while the Evolve sheet is open.
   Tree.select(id) select a trait (switches to its tab).

   Layout: stat strip (current values), one tab per track, then a node-link graph (SVG; nodes at trait col/row) beside a
   detail panel (Plague-style: description, cost, requirements, effect lines, stat bars with a ghost segment for the change).
   The graph is built once per tab; render() only updates classes and text, so hover, focus and selection survive ticks. */
const Tree = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const W = 120, H = 62, CW = 152, RH = 88, PAD = 20;          // node size, column/row pitch, graph margin
  const STAT_N = { spread: "Spread", capability: "Capability", automation: "Automation", revenue: "Revenue", efficiency: "Efficiency", awareness: "Awareness" };
  const BAD_UP = new Set(["awareness"]);                         // stats where an increase hurts you
  const STATE_N = { own: "Owned", can: "Available", poor: "Can't afford", lock: "Locked", excl: "Excluded" };
  // Effect lines whose quantity is bad when it goes up (sign flips the colour)
  const BAD_WORDS = /awareness|burn|treaty|pause|rival|visib|scandal|risk|lawsuit|distrust|detect|fine|upkeep|cost|fuel|minerals? used|crackdown|per automated job|serving compute|draw|suspicion|oversight|audit|counter|lease price|build time|delay|damage/i;

  let root, api, tab = null, sel = null, hov = null, built = null, els = {}, cache = {}, flashT = 0;

  const byId = id => TRAITS.find(t => t.id === id);
  const trackN = k => (TRACKS.find(x => x[0] === k) || [k, k])[1];
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const money = m => m >= 1000 ? "$" + (m / 1000).toFixed(m >= 10000 ? 0 : 1) + "B" : "$" + Math.round(m) + "M";
  const st = t => { try { return traitState(api.G(), t); } catch (e) { return "lock"; } };
  const svg = (tag, attrs, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };
  const setHTML = (key, el, html) => { if (cache[key] !== html) { cache[key] = html; el.innerHTML = html; } };

  // ---------- CSS (one <style>, page tokens, tg- prefix)
  const CSS = `
.tg { display: flex; flex-direction: column; gap: 10px; min-height: 0; color: var(--ink); }
.tg-strip { display: grid; grid-template-columns: repeat(6, 1fr); gap: 4px 14px; padding: 8px 10px; background: #0a141a; border: 1px solid var(--panel-line); border-radius: 8px; }
.tg-strip .tg-bar { grid-template-columns: 1fr; gap: 3px; }
.tg-strip .tg-bl { display: flex; justify-content: space-between; }
.tg-strip .tg-bt { height: 7px; }
.tg-head { display: flex; align-items: flex-end; gap: 10px; border-bottom: 1px solid var(--panel-line); }
.tg-tabs { display: flex; gap: 2px; overflow-x: auto; scrollbar-width: none; flex: 1; min-width: 0; }
.tg-tabs::-webkit-scrollbar { display: none; }
.tg-tab { position: relative; background: transparent; border: 1px solid transparent; border-bottom: 0; border-radius: 7px 7px 0 0; padding: 7px 14px 8px; color: var(--ink-2); font: 700 13px/1 Archivo, "IBM Plex Sans", sans-serif; font-stretch: 85%; letter-spacing: .06em; text-transform: uppercase; white-space: nowrap; cursor: pointer; margin-bottom: -1px; }
.tg-tab:hover { color: var(--ink); }
.tg-tab.on { color: var(--ai); background: var(--panel-solid); border-color: var(--panel-line); }
.tg-tab .tg-n { display: inline-block; min-width: 17px; margin-left: 6px; padding: 2px 4px; border-radius: 9px; background: var(--ai-dim); color: var(--ink); font: 600 10px/1 "IBM Plex Mono", monospace; letter-spacing: 0; text-align: center; }
.tg-tab .tg-n:empty { display: none; }
.tg-wallet { font: 12px "IBM Plex Mono", monospace; color: var(--ink-2); white-space: nowrap; padding-bottom: 7px; }
.tg-wallet b { color: var(--warn); font-weight: 600; }
.tg-main { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 12px; min-height: 0; }
.tg-graph { position: relative; overflow: auto; background: radial-gradient(circle at 30% 20%, #0d1f27, #081117 70%); border: 1px solid var(--panel-line); border-radius: 8px; min-height: 240px; max-height: 500px; overscroll-behavior: contain; -webkit-overflow-scrolling: touch; }
.tg-graph svg { display: block; margin: 0 auto; touch-action: pan-x pan-y; }
.tg-legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 11px; color: var(--ink-3); padding: 0 2px; }
.tg-legend i { display: inline-block; width: 18px; height: 0; border-top: 2px solid var(--ink-3); vertical-align: 3px; margin-right: 4px; }
.tg-legend i.d { border-top-style: dashed; }
.tg-legend i.x { border-top: 2px dashed var(--bad); }
.tg-legend i.o { width: 10px; height: 10px; border: 2px solid var(--ai); background: var(--ai-dim); border-radius: 2px; vertical-align: -1px; }
.tg-legend i.c { width: 10px; height: 10px; border: 2px solid var(--ai); border-radius: 2px; vertical-align: -1px; }

/* edges */
.tg-e { fill: none; stroke: #2a3d49; stroke-width: 2; }
.tg-e.any { stroke-dasharray: 5 4; }
.tg-e.half { stroke: var(--ai-dim); }
.tg-e.lit { stroke: var(--ai); stroke-width: 2.5; }
.tg-e.dim { opacity: .45; }
.tg-x line { stroke: var(--bad); stroke-width: 1.6; stroke-dasharray: 3 4; opacity: .7; }
.tg-x circle { fill: #2a0e0c; stroke: var(--bad); stroke-width: 1.5; }
.tg-x text { fill: var(--bad); font: 700 12px "IBM Plex Mono", monospace; text-anchor: middle; dominant-baseline: central; }
.tg-x.hot line { opacity: 1; stroke-width: 2.2; }

/* nodes */
.tg-node { cursor: pointer; outline: none; }
.tg-hex { fill: #0f1d26; stroke: #3b5260; stroke-width: 1.6; transition: fill .2s, stroke .2s; }
.tg-ring { fill: none; stroke: transparent; stroke-width: 2; }
.tg-nm { fill: var(--ink); font: 600 12px/1 Archivo, "IBM Plex Sans", sans-serif; font-stretch: 85%; text-anchor: middle; pointer-events: none; }
.tg-cost { fill: var(--ink-2); font: 10.5px "IBM Plex Mono", monospace; text-anchor: middle; pointer-events: none; }
.tg-strike { stroke: var(--bad); stroke-width: 1.6; opacity: 0; }
.tg-badge circle { stroke-width: 1.3; }
.tg-badge text { font: 700 9px "IBM Plex Mono", monospace; text-anchor: middle; dominant-baseline: central; pointer-events: none; }
.tg-badge.req circle { fill: #10202a; stroke: var(--ink-3); }
.tg-badge.req text { fill: var(--ink-2); }
.tg-badge.req.ok circle { stroke: var(--ai); }
.tg-badge.req.ok text { fill: var(--ai); }
.tg-badge.ex circle { fill: #2a0e0c; stroke: var(--bad); }
.tg-badge.ex text { fill: var(--bad); }

.tg-node.own .tg-hex { fill: var(--ai-dim); stroke: var(--ai); }
.tg-node.own .tg-cost { fill: #bff5ef; }
.tg-node.can .tg-hex { fill: #0f2a2d; stroke: var(--ai); stroke-width: 2.2; }
.tg-node.can .tg-ring { stroke: var(--ai); animation: tg-pulse 1.6s ease-in-out infinite; }
.tg-node.can .tg-cost { fill: var(--warn); }
.tg-node.poor .tg-hex { stroke: #4d6674; }
.tg-node.poor .tg-cost { fill: #a98450; }
.tg-node.lock { opacity: .42; }
.tg-node.lock .tg-hex { stroke-dasharray: 4 3; }
.tg-node.excl { opacity: .55; }
.tg-node.excl .tg-hex { stroke: var(--bad); fill: #1a0f10; }
.tg-node.excl .tg-nm { fill: var(--ink-3); }
.tg-node.excl .tg-strike { opacity: .9; }
.tg-node:hover .tg-hex, .tg-node:focus-visible .tg-hex { stroke-width: 2.6; }
.tg-node:hover { opacity: 1; }
.tg-node.sel .tg-ring { stroke: var(--ink); stroke-width: 2.4; animation: none; opacity: 1; }
.tg-node.just .tg-hex { animation: tg-flash .7s ease-out; }
@keyframes tg-pulse { 0%, 100% { opacity: .15; stroke-width: 2; } 50% { opacity: .85; stroke-width: 5; } }
@keyframes tg-flash { 0% { fill: #fff; } 100% { fill: var(--ai-dim); } }

/* detail panel */
.tg-det { background: #0a141a; border: 1px solid var(--panel-line); border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; overflow: hidden; max-height: 500px; min-height: 0; }
.tg-scroll { flex: 1; min-height: 0; overflow: auto; display: flex; flex-direction: column; gap: 7px; }
.tg-dh { display: flex; flex-direction: column; gap: 3px; }
.tg-dt { font: 800 19px/1.1 Archivo, "IBM Plex Sans", sans-serif; font-stretch: 85%; letter-spacing: .01em; }
.tg-dk { font-size: 11px; color: var(--ink-3); text-transform: uppercase; letter-spacing: .08em; display: flex; gap: 8px; align-items: center; }
.tg-chip { padding: 1px 6px; border-radius: 3px; font-weight: 600; letter-spacing: .04em; }
.tg-chip.own { background: var(--ai-dim); color: var(--ink); }
.tg-chip.can { background: #0f2a2d; color: var(--ai); border: 1px solid var(--ai); }
.tg-chip.poor { background: #33260f; color: var(--warn); }
.tg-chip.lock { background: #1a252c; color: var(--ink-2); }
.tg-chip.excl { background: #3d1210; color: var(--bad); }
.tg-dd { font-size: 13px; color: var(--ink-2); margin: 0; }
.tg-cst { display: flex; gap: 12px; font: 13px "IBM Plex Mono", monospace; }
.tg-cst span { color: var(--warn); }
.tg-cst span.no { color: var(--bad); }
.tg-cst span.cash { color: var(--ink); }
.tg-cst span.cash.no { color: var(--bad); }
.tg-req { font-size: 12px; color: var(--ink-2); display: grid; gap: 2px; }
.tg-req .ok { color: var(--ai); }
.tg-req .no { color: var(--ink-3); }
.tg-req .x { color: var(--bad); }
.tg-why { font-size: 12px; color: var(--warn); }
.tg-why.x { color: var(--bad); }
.tg-sec { font: 700 11px Archivo, sans-serif; font-stretch: 85%; letter-spacing: .1em; text-transform: uppercase; color: var(--ink-3); margin-top: 2px; }
.tg-fx { list-style: none; margin: 0; padding: 0; display: grid; gap: 1px; font-size: 12.5px; line-height: 1.35; }
.tg-fx li { padding-left: 14px; position: relative; }
.tg-fx li::before { content: ""; position: absolute; left: 2px; top: 6px; width: 6px; height: 6px; border-radius: 1px; background: currentColor; }
.tg-fx .good { color: #6be6b0; }
.tg-fx .bad { color: var(--bad); }
.tg-fx .neu { color: var(--ink-2); }
.tg-bars { display: grid; gap: 4px; }
.tg-bar { display: grid; grid-template-columns: 76px 1fr 54px; gap: 8px; align-items: center; font-size: 11.5px; color: var(--ink-2); }
.tg-bt { position: relative; height: 9px; background: #13232d; border-radius: 5px; overflow: hidden; }
.tg-bt i { position: absolute; top: 0; bottom: 0; left: 0; width: 0; }
.tg-bt i.cur { background: var(--ai); border-radius: 5px 0 0 5px; }
.tg-bar.aw .tg-bt i.cur { background: var(--gov); }
.tg-bt i.gh { opacity: .9; }
.tg-bt i.gh.up { background: repeating-linear-gradient(-45deg, #7dffb8 0 3px, #39b98a 3px 6px); }
.tg-bt i.gh.down { background: repeating-linear-gradient(-45deg, var(--bad) 0 3px, #7a2620 3px 6px); }
.tg-bv { font: 11px "IBM Plex Mono", monospace; text-align: right; white-space: nowrap; color: var(--ink-3); }
.tg-bv b { font-weight: 600; }
.tg-bv b.up { color: #6be6b0; }
.tg-bv b.down { color: var(--bad); }
.tg-buy { margin-top: auto; flex: none; background: var(--ai-dim); border: 1px solid var(--ai); color: var(--ink); border-radius: 7px; padding: 10px 12px; font: 800 14px Archivo, sans-serif; font-stretch: 85%; letter-spacing: .1em; text-transform: uppercase; cursor: pointer; }
.tg-buy:hover:not(:disabled) { background: #1aa79e; }
.tg-buy:disabled { background: #13232d; border-color: var(--panel-line); color: var(--ink-3); cursor: default; letter-spacing: .02em; text-transform: none; font: 600 12px "IBM Plex Sans", sans-serif; }
.tg-empty { color: var(--ink-3); font-size: 13px; }

@media (max-width: 760px) {
  .tg { gap: 8px; }
  .tg-strip { grid-template-columns: repeat(3, 1fr); gap: 5px 10px; padding: 7px 8px; }
  .tg-main { grid-template-columns: 1fr; }
  .tg-graph { max-height: 52vh; }
  .tg-det { max-height: none; }
  .tg-tab { padding: 7px 10px 8px; font-size: 12px; }
  .tg-wallet { display: none; }
}`;

  // ---------- stat bars (persistent rows; update widths only)
  function makeBars(parent, ghost) {
    const rows = {};
    for (const k of STAT_KEYS) {
      const r = document.createElement("div");
      r.className = "tg-bar" + (BAD_UP.has(k) ? " aw" : "");
      r.innerHTML = `<span class="tg-bl">${STAT_N[k] || k}${ghost ? "" : `<span class="tg-bv"></span>`}</span><div class="tg-bt"><i class="cur"></i>${ghost ? `<i class="gh"></i>` : ""}</div>${ghost ? `<span class="tg-bv"></span>` : ""}`;
      parent.appendChild(r);
      rows[k] = { cur: r.querySelector(".cur"), gh: r.querySelector(".gh"), v: r.querySelector(".tg-bv") };
    }
    return rows;
  }
  const pct = v => Math.max(0, Math.min(1, +v || 0)) * 100;
  function setBars(rows, now, nxt) {
    for (const k of STAT_KEYS) {
      const r = rows[k], a = pct(now && now[k]);
      if (!r.gh) { r.cur.style.width = a + "%"; r.v.textContent = Math.round(a); continue; }
      const b = nxt ? pct(nxt[k]) : a, d = b - a;
      r.cur.style.width = Math.min(a, b) + "%";
      const good = BAD_UP.has(k) ? d < 0 : d > 0;
      r.gh.className = "gh " + (good ? "up" : "down");
      r.gh.style.left = Math.min(a, b) + "%";
      r.gh.style.width = Math.abs(d) + "%";
      const dr = Math.round(d);
      r.v.innerHTML = Math.abs(d) < 0.5 ? `${Math.round(a)}` : `${Math.round(a)} <b class="${good ? "up" : "down"}">${dr > 0 ? "+" : dr < 0 ? "−" : "±"}${Math.abs(dr) || "<1"}</b>`;
    }
  }

  // ---------- mount
  function mount(container, a) {
    api = a; root = container;
    if (!document.getElementById("tg-style")) {
      const s = document.createElement("style"); s.id = "tg-style"; s.textContent = CSS; document.head.appendChild(s);
    }
    root.innerHTML = `<div class="tg">
      <div class="tg-strip" aria-label="Model stats"></div>
      <div class="tg-head"><div class="tg-tabs" role="tablist"></div><div class="tg-wallet"></div></div>
      <div class="tg-main">
        <div class="tg-graph"></div>
        <div class="tg-det" aria-live="polite">
          <div class="tg-scroll">
            <div class="tg-dbody"></div>
            <div class="tg-sec">Effect on your model</div>
            <div class="tg-bars"></div>
          </div>
          <button class="tg-buy" type="button" disabled>Select an upgrade</button>
        </div>
      </div>
      <div class="tg-legend"><span><i class="o"></i>owned</span><span><i class="c"></i>available</span><span><i></i>requires</span><span><i class="d"></i>requires one of</span><span><i class="x"></i>excludes</span><span>Enter buys · arrows move</span></div>
    </div>`;
    els = {
      strip: root.querySelector(".tg-strip"), tabs: root.querySelector(".tg-tabs"), wallet: root.querySelector(".tg-wallet"),
      graph: root.querySelector(".tg-graph"), body: root.querySelector(".tg-dbody"), buy: root.querySelector(".tg-buy"),
    };
    els.stripBars = makeBars(els.strip, false);
    els.detBars = makeBars(root.querySelector(".tg-bars"), true);
    els.buy.onclick = () => doBuy(sel);
    built = null; cache = {};
    if (!mount.once) {
      mount.once = true;
      document.addEventListener("keydown", onKey);
      window.addEventListener("resize", () => { if (built) fit(); });
    }
    render();
  }

  function doBuy(id) {
    const t = id && byId(id);
    if (!t || st(t) !== "can") return false;
    if (!api.buy(id)) return false;
    const n = built && built.nodes.get(id);
    if (n) { n.g.classList.remove("just"); void n.g.getBBox(); n.g.classList.add("just"); clearTimeout(flashT); flashT = setTimeout(() => n.g.classList.remove("just"), 800); }
    if (api.onChange) api.onChange();
    render();
    return true;
  }

  // ---------- keyboard: Enter buys, arrows move between nodes of the tab, [ ] switch tabs
  function onKey(e) {
    if (!root || !root.isConnected || root.offsetParent === null) return;
    const tg = e.target;
    if (tg && (tg.tagName === "INPUT" || tg.tagName === "TEXTAREA" || tg.tagName === "SELECT" || (tg.tagName === "BUTTON" && !root.contains(tg)))) return;
    if (e.key === "Enter") {
      if (tg && tg.tagName === "BUTTON") return;               // the button's own click handles it
      if (sel) { doBuy(sel); e.preventDefault(); }
      return;
    }
    if (e.key === "[" || e.key === "]") {
      const ks = TRACKS.map(x => x[0]), i = ks.indexOf(tab);
      setTab(ks[(i + (e.key === "]" ? 1 : ks.length - 1)) % ks.length]); e.preventDefault(); return;
    }
    const dir = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
    if (!dir || !built) return;
    e.preventDefault();
    const list = TRAITS.filter(t => t.t === tab);
    const cur = byId(sel);
    if (!cur || cur.t !== tab) { pick(list[0] && list[0].id); return; }
    let best = null, bs = Infinity;
    for (const t of list) {
      if (t === cur) continue;
      const dx = cr(t)[0] - cr(cur)[0], dy = cr(t)[1] - cr(cur)[1];
      const along = dx * dir[0] + dy * dir[1];
      if (along <= 0) continue;
      const side = Math.abs(dir[0] ? dy : dx);
      const s = along + side * 2.5;
      if (s < bs) { bs = s; best = t; }
    }
    if (best) pick(best.id);
  }

  function pick(id, focus = true) {
    if (!id) return;
    sel = id; hov = null;
    render();
    const n = built && built.nodes.get(id);
    if (n && focus) {
      try { n.g.focus({ preventScroll: true }); } catch (e) { /* old browsers */ }
      // keep the node in view
      const gr = els.graph, r = n.g.getBoundingClientRect(), b = gr.getBoundingClientRect();
      if (r.left < b.left) gr.scrollLeft -= b.left - r.left + 16;
      else if (r.right > b.right) gr.scrollLeft += r.right - b.right + 16;
      if (r.top < b.top) gr.scrollTop -= b.top - r.top + 16;
      else if (r.bottom > b.bottom) gr.scrollTop += r.bottom - b.bottom + 16;
    }
  }

  function setTab(k) {
    if (k === tab) return;
    tab = k; hov = null;
    const cur = byId(sel);
    if (!cur || cur.t !== k) sel = bestStart(k);
    built = null; render();
  }
  // default selection in a tab: first available, else first not owned, else first
  function bestStart(k) {
    const list = TRAITS.filter(t => t.t === k).sort((a, b) => ((a.col || 0) - (b.col || 0)) || ((a.row || 0) - (b.row || 0)));
    return (list.find(t => st(t) === "can") || list.find(t => st(t) !== "own") || list[0] || {}).id || null;
  }

  // ---------- graph build (once per tab)
  function hexPts(w, h) {
    const a = w / 2, c = 14, b = h / 2;
    return `${-a},0 ${-a + c},${-b} ${a - c},${-b} ${a},0 ${a - c},${b} ${-a + c},${b}`;
  }
  function wrap(name) {
    const words = String(name).split(/\s+/), lines = [""];
    for (const w of words) {
      const l = lines[lines.length - 1];
      if (!l) lines[lines.length - 1] = w;
      else if ((l + " " + w).length <= 16) lines[lines.length - 1] = l + " " + w;
      else lines.push(w);
    }
    if (lines.length > 2) lines.splice(1, lines.length, lines.slice(1).join(" "));
    if (lines[1] && lines[1].length > 18) lines[1] = lines[1].slice(0, 17) + "…";
    if (lines[0].length > 18) lines[0] = lines[0].slice(0, 17) + "…";
    return lines;
  }
  // trait col/row from the sim; traits without them get col = depth along in-track prerequisites, row = next free slot
  let LAY = new Map();
  function layout(list) {
    LAY = new Map();
    const ids = new Set(list.map(t => t.id)), depth = new Map();
    const dep = (t, seen = new Set()) => {
      if (Number.isFinite(t.col)) return t.col;
      if (depth.has(t.id)) return depth.get(t.id);
      if (seen.has(t.id)) return 0;
      seen.add(t.id);
      const pre = [...(t.req || []), ...(t.any || [])].filter(r => ids.has(r)).map(byId);
      const d = pre.length ? 1 + Math.max(...pre.map(p => dep(p, seen))) : 0;
      depth.set(t.id, d);
      return d;
    };
    const used = new Set();
    for (const t of list) if (Number.isFinite(t.col) && Number.isFinite(t.row)) { LAY.set(t.id, [t.col, t.row]); used.add(t.col + "," + t.row); }
    for (const t of list) if (!LAY.has(t.id)) {
      const c = dep(t); let r = Number.isFinite(t.row) ? t.row : 0;
      while (used.has(c + "," + r)) r++;
      used.add(c + "," + r); LAY.set(t.id, [c, r]);
    }
  }
  const cr = t => LAY.get(t.id) || [t.col || 0, t.row || 0];
  const pos = t => { const [c, r] = cr(t); return [PAD + W / 2 + c * CW, PAD + H / 2 + 6 + r * RH]; };

  function build() {
    const list = TRAITS.filter(t => t.t === tab);
    const ids = new Set(list.map(t => t.id));
    layout(list);
    const maxC = Math.max(0, ...list.map(t => cr(t)[0])), maxR = Math.max(0, ...list.map(t => cr(t)[1]));
    const nw = PAD * 2 + W + maxC * CW, nh = PAD * 2 + H + 12 + maxR * RH;
    els.graph.innerHTML = "";
    const s = svg("svg", { viewBox: `0 0 ${nw} ${nh}`, role: "group", "aria-label": trackN(tab) + " upgrades" }, els.graph);
    const gE = svg("g", {}, s), gX = svg("g", {}, s), gN = svg("g", {}, s);
    const edges = [], xs = [], nodes = new Map();

    for (const t of list) {
      const [x2, y2] = pos(t);
      const add = (rid, kind) => {
        const f = byId(rid);
        if (!f || !ids.has(rid)) return;
        const [x1, y1] = pos(f);
        let d;
        if (cr(f)[0] < cr(t)[0]) {
          const sx = x1 + W / 2, ex = x2 - W / 2, m = (ex - sx) / 2;
          d = `M${sx},${y1} C${sx + m},${y1} ${ex - m},${y2} ${ex},${y2}`;
        } else {                                                 // same column (or backwards): go vertically
          const dn = y2 > y1, sy = y1 + (dn ? H / 2 : -H / 2), ey = y2 + (dn ? -H / 2 : H / 2);
          const bend = cr(f)[0] > cr(t)[0] ? -30 : 0;
          d = `M${x1},${sy} C${x1 + bend},${(sy + ey) / 2} ${x2 + bend},${(sy + ey) / 2} ${x2},${ey}`;
        }
        edges.push({ el: svg("path", { d, class: "tg-e" + (kind === "any" ? " any" : "") }, gE), from: rid, to: t.id });
      };
      (t.req || []).forEach(r => add(r, "req"));
      (t.any || []).forEach(r => add(r, "any"));
    }
    // exclusive pairs inside this track: red dashed link with an × in the middle (each pair once)
    const seen = new Set();
    for (const t of list) for (const o of t.ex || []) {
      const key = [t.id, o].sort().join("|");
      if (!ids.has(o) || seen.has(key)) continue;
      seen.add(key);
      const [x1, y1] = pos(t), [x2, y2] = pos(byId(o));
      const g = svg("g", { class: "tg-x" }, gX);
      const dx = x2 - x1, dy = y2 - y1, L = Math.hypot(dx, dy) || 1;
      const ux = dx / L, uy = dy / L, r1 = Math.abs(ux) > 0.5 ? W / 2 : H / 2;
      svg("line", { x1: x1 + ux * r1, y1: y1 + uy * r1, x2: x2 - ux * r1, y2: y2 - uy * r1 }, g);
      const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
      svg("circle", { cx: mx, cy: my, r: 8 }, g);
      svg("text", { x: mx, y: my + 0.5 }, g).textContent = "×";
      xs.push({ g, a: t.id, b: o });
    }

    for (const t of list) {
      const [x, y] = pos(t);
      const g = svg("g", { class: "tg-node", transform: `translate(${x},${y})`, tabindex: "0", role: "button", "data-id": t.id }, gN);
      svg("polygon", { points: hexPts(W + 10, H + 10), class: "tg-ring" }, g);
      svg("polygon", { points: hexPts(W, H), class: "tg-hex" }, g);
      const lines = wrap(t.n);
      const y0 = lines.length === 1 ? -4 : -11;
      lines.forEach((l, i) => { svg("text", { x: 0, y: y0 + i * 14, class: "tg-nm" }, g).textContent = l; });
      svg("line", { x1: -W / 2 + 18, y1: y0 - 4 + (lines.length - 1) * 7, x2: W / 2 - 18, y2: y0 - 4 + (lines.length - 1) * 7, class: "tg-strike" }, g);
      const cost = svg("text", { x: 0, y: 22, class: "tg-cost" }, g);
      // badges: requirements from other tracks (top-left), exclusions with other tracks (top-right)
      const xreq = [...(t.req || []), ...(t.any || [])].filter(r => !ids.has(r) && byId(r));
      let bReq = null;
      if (xreq.length) {
        bReq = svg("g", { class: "tg-badge req", transform: `translate(${-W / 2 + 16},${-H / 2})` }, g);
        svg("circle", { r: 8 }, bReq);
        svg("text", { y: 0.5 }, bReq).textContent = (trackN(byId(xreq[0]).t) || "?")[0].toUpperCase();
      }
      const xex = (t.ex || []).filter(o => !ids.has(o) && byId(o));
      if (xex.length) {
        const b = svg("g", { class: "tg-badge ex", transform: `translate(${W / 2 - 16},${-H / 2})` }, g);
        svg("circle", { r: 8 }, b);
        svg("text", { y: 0.5 }, b).textContent = "×";
      }
      const title = svg("title", {}, g);
      title.textContent = t.n + (xreq.length ? "\nNeeds " + xreq.map(r => byId(r).n).join(t.any && t.any.length ? " / " : ", ") : "") + (xex.length ? "\nExcludes " + xex.map(o => byId(o).n).join(", ") : "");
      g.addEventListener("pointerenter", e => { if (e.pointerType === "mouse") { hov = t.id; render(); } });
      g.addEventListener("pointerleave", e => { if (e.pointerType === "mouse" && hov === t.id) { hov = null; render(); } });
      g.addEventListener("click", () => { if (sel === t.id && e2.last === t.id && Date.now() - e2.at < 400) doBuy(t.id); e2.last = t.id; e2.at = Date.now(); sel = t.id; render(); });
      g.addEventListener("focus", () => { if (sel !== t.id) { sel = t.id; render(); } });
      nodes.set(t.id, { g, cost, bReq, xreq, t });
    }
    built = { tab, traits: TRAITS, n: TRAITS.length, nw, nh, s, edges, xs, nodes };
    fit();
  }
  const e2 = { last: null, at: 0 };                            // double-click/tap on a selected node buys it

  function fit() {
    const avail = els.graph.clientWidth - 2;
    if (avail <= 0) { built.fitW = 0; return; }
    // fill the box on desktop (up to 1.2×, limited by the graph's max height), never below a readable scale; else scroll
    const maxH = parseFloat(getComputedStyle(els.graph).maxHeight) || 500;
    const fitS = Math.min(avail / built.nw, (maxH - 4) / built.nh);
    const sc = Math.min(avail < 600 ? 1 : 1.2, Math.max(avail < 600 ? 0.8 : 0.66, fitS));
    built.s.setAttribute("width", Math.round(built.nw * sc));
    built.s.setAttribute("height", Math.round(built.nh * sc));
    built.fitW = avail;
  }

  // ---------- render (every tick)
  function render() {
    if (!root || !api) return;
    const G = api.G();
    if (!G || typeof TRAITS === "undefined") return;
    if (!tab || !TRACKS.some(x => x[0] === tab)) { tab = TRACKS[0][0]; sel = bestStart(tab); }

    // tabs (rebuilt only when the counts change)
    const counts = TRACKS.map(([k]) => TRAITS.filter(t => t.t === k && st(t) === "can").length);
    setHTML("tabs", els.tabs, TRACKS.map(([k, n], i) => `<button class="tg-tab${k === tab ? " on" : ""}" role="tab" aria-selected="${k === tab}" data-t="${k}">${esc(n)}<span class="tg-n">${counts[i] || ""}</span></button>`).join(""));
    const on = els.tabs.querySelector(".tg-tab.on");
    if (on && cache.tabOn !== tab) { cache.tabOn = tab; const tb = els.tabs, L = on.getBoundingClientRect().left - tb.getBoundingClientRect().left + tb.scrollLeft, R = L + on.offsetWidth; if (L < tb.scrollLeft || R > tb.scrollLeft + tb.clientWidth) tb.scrollLeft = L - 24; }
    if (!els.tabs.onclick) els.tabs.onclick = e => { const b = e.target.closest(".tg-tab"); if (b) setTab(b.dataset.t); };
    setHTML("wallet", els.wallet, `<b>◆ ${Math.floor(G.ops || 0)}</b> insight · ${money(G.cash || 0)}`);

    if (!built || built.tab !== tab || built.traits !== TRAITS || built.n !== TRAITS.length) build();
    else if (els.graph.clientWidth - 2 !== built.fitW) fit();

    // nodes
    const state = {};
    for (const [id, n] of built.nodes) {
      const s = state[id] = st(n.t);
      const cls = "tg-node " + s + (id === sel ? " sel" : "");
      if (n.cls !== cls) { const just = n.g.classList.contains("just"); n.g.setAttribute("class", cls + (just ? " just" : "")); n.cls = cls; }
      const t = n.t;
      const txt = s === "own" ? "OWNED" : `◆${t.c || 0}` + (t.cash ? ` · ${money(t.cash)}` : "");
      if (n.costT !== txt) { n.cost.textContent = txt; n.costT = txt; }
      if (n.bReq) {
        const ok = n.xreq.some(r => G.own && G.own.has(r));
        n.bReq.setAttribute("class", "tg-badge req" + (ok ? " ok" : ""));
      }
    }
    const owned = id => G.own && G.own.has(id);
    for (const e of built.edges) {
      const c = "tg-e" + (e.el.classList.contains("any") ? " any" : "") + (owned(e.from) && owned(e.to) ? " lit" : owned(e.from) ? " half" : "") + (state[e.to] === "excl" ? " dim" : "");
      if (e.c !== c) { e.el.setAttribute("class", c); e.c = c; }
    }
    const act = hov || sel;
    for (const x of built.xs) x.g.setAttribute("class", "tg-x" + (act === x.a || act === x.b ? " hot" : ""));

    // stats
    let now = null, nxt = null;
    try { now = stats(G); } catch (e) { /* sim not ready */ }
    setBars(els.stripBars, now, null);
    const t = act && byId(act);
    if (t) { try { nxt = st(t) === "own" ? now : stats(G, t.id); } catch (e) { nxt = now; } }
    setBars(els.detBars, now, nxt);
    detail(G, t, t && (state[t.id] || st(t)));
  }

  function lineClass(l) {
    if (l && typeof l === "object") return [l.t || l.text || "", l.bad ? "bad" : l.good === false ? "bad" : l.good ? "good" : "neu"];
    const s = String(l), m = s.match(/[+−-]\s*\$?\d|×\s*\d/);
    if (!m) return [s, /^(cannot|can.t|no |lose|loses|blocks|disables|excludes|costs |rivals )/i.test(s) ? "bad" : "good"];
    let up = m[0][0] === "+";
    if (m[0][0] === "×") { const v = parseFloat(s.slice(m.index + 1)); up = v >= 1; }
    const bad = BAD_WORDS.test(s) && !/slowdown of/i.test(s) ? up : !up;
    return [s, bad ? "bad" : "good"];
  }

  function detail(G, t, s) {
    const b = els.buy;
    if (!t) {
      setHTML("det", els.body, `<div class="tg-dh"><div class="tg-dt">${esc(trackN(tab))}</div></div><p class="tg-empty">Pick a node to see what it does. Bars show your model now; a striped segment previews the change.</p>`);
      b.disabled = true; b.textContent = "Select an upgrade"; b.dataset.id = "";
      return;
    }
    const own = G.own || new Set();
    const req = (t.req || []).map(r => byId(r)).filter(Boolean);
    const any = (t.any || []).map(r => byId(r)).filter(Boolean);
    const ex = (t.ex || []).map(r => byId(r)).filter(Boolean);
    const tick = r => own.has(r.id) ? `<span class="ok">✓ ${esc(r.n)}</span>` : `<span class="no">○ ${esc(r.n)}</span>`;
    const reqs = [];
    req.forEach(r => reqs.push(tick(r) + (r.t !== t.t ? ` <span class="no">· ${esc(trackN(r.t))}</span>` : "")));
    if (any.length) reqs.push(`<span>One of: ${any.map(tick).join(" ")}</span>`);
    if (t.minCap) reqs.push((G.cap || 0) >= t.minCap ? `<span class="ok">✓ Capability ${t.minCap}</span>` : `<span class="no">○ Capability ${t.minCap} (now ${(G.cap || 0).toFixed(1)})</span>`);
    if (t.minC) reqs.push((G.comp || 0) >= t.minC ? `<span class="ok">✓ ${t.minC} EF compute</span>` : `<span class="no">○ ${t.minC} EF compute (now ${Math.round(G.comp || 0)})</span>`);
    // conditions on the game state (presence, awareness, automation…) come from the sim's reqs()
    if (typeof window.reqs === "function") {
      try { window.reqs(G, t).filter(r => r.cond).forEach(r => reqs.push(`<span class="${r.ok ? "ok" : "no"}">${r.ok ? "✓" : "○"} ${esc(r.t)}</span>`)); } catch (e) { /* sim not ready */ }
    }
    ex.forEach(r => reqs.push(`<span class="x">× Excludes ${esc(r.n)}${own.has(r.id) ? " (owned)" : ""}</span>`));
    let lines = [];
    try { lines = fxLines(G, t) || []; } catch (e) { /* sim not ready */ }
    if (ex.length) lines = lines.filter(l => !/^excludes /i.test(typeof l === "string" ? l : (l && (l.t || l.text)) || ""));   // listed above
    let w = "";
    if (s !== "own" && s !== "can") { try { w = why(G, t) || ""; } catch (e) { /* */ } }
    // the requirement list already explains an owned exclusion or a missing prerequisite
    if ((s === "excl" && ex.some(r => own.has(r.id))) || (s === "lock" && reqs.some(h => h.includes('class="no"')))) w = "";
    const okIns = (G.ops || 0) >= (t.c || 0), okCash = !t.cash || (G.cash || 0) >= t.cash;
    setHTML("det", els.body, `<div class="tg-dh">
        <div class="tg-dk"><span>${esc(trackN(t.t))}</span><span class="tg-chip ${s}">${STATE_N[s] || s}</span></div>
        <div class="tg-dt">${esc(t.n)}</div></div>
      ${t.d ? `<p class="tg-dd">${esc(t.d)}</p>` : ""}
      ${s === "own" ? "" : `<div class="tg-cst"><span class="${okIns ? "" : "no"}">◆ ${t.c || 0} insight</span>${t.cash ? `<span class="cash${okCash ? "" : " no"}">${money(t.cash)}</span>` : ""}</div>`}
      ${w ? `<div class="tg-why${s === "excl" ? " x" : ""}">${esc(w)}</div>` : ""}
      ${reqs.length ? `<div class="tg-req">${reqs.join("")}</div>` : ""}
      ${lines.length ? `<div class="tg-sec">Effects</div><ul class="tg-fx">${lines.map(l => { const [tx, c] = lineClass(l); return `<li class="${c}">${esc(tx)}</li>`; }).join("")}</ul>` : ""}`);
    const can = s === "can";
    b.disabled = !can;
    b.textContent = can ? `Evolve · ◆${t.c || 0}${t.cash ? " " + money(t.cash) : ""}` : s === "own" ? "Owned" : (w || STATE_N[s] || "Unavailable");
    b.dataset.id = t.id;
    b.onclick = () => doBuy(t.id);
  }

  return { mount, render, select: id => { const t = byId(id); if (!t) return; if (t.t !== tab) { tab = t.t; built = null; } pick(id, false); } };
})();
