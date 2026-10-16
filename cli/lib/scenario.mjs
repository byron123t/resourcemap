// Scenario engine for the CLI and MCP server. It runs the page's own models, taken from site/index.html, in node:vm:
// #simWorker (oil and gas flows), #simWater (river basins), #simTransport (+ its worker; roads and rail) and #simCore
// (shock resolver, power, mines, cables, chips, cloud, outbreaks, food, and the chain of consequences). This file only
// supplies what the page's `simEnv` supplies in the browser: data from site/data, population tiles, and the two workers.
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import vm from "node:vm";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const SITE = process.env.ATLAS_SITE || "https://byron123t.github.io/resourcemap/";
let DATA_DIR = process.env.ATLAS_DATA || join(ROOT, "site", "data");
export function setDataDir(dir) { DATA_DIR = dir; cache.clear(); }

const HTML = () => (HTML.s = HTML.s || readFileSync(join(ROOT, "site", "index.html"), "utf8"));
const block = id => {
  const m = HTML().match(new RegExp(`<script(?: type="text/plain")? id="${id}">([\\s\\S]*?)</script>`));
  if (!m) throw new Error(`site/index.html has no #${id} script`);
  return m[1];
};
// a `const NAME = ...;` statement from the main script, evaluated (one line or a balanced literal)
function mainConst(name) {
  const h = HTML(), i = h.indexOf(`  const ${name} = `);
  if (i < 0) throw new Error(`site/index.html has no const ${name}`);
  let k = h.indexOf("=", i) + 1, depth = 0, q = null;
  for (; k < h.length; k++) {
    const c = h[k];
    if (q) { if (c === "\\") k++; else if (c === q) q = null; continue; }
    if (c === '"' || c === "'" || c === "`") q = c;
    else if ("{[(".includes(c)) depth++;
    else if ("}])".includes(c)) depth--;
    else if (c === ";" && depth === 0) break;
  }
  return h.slice(h.indexOf("=", i) + 1, k);
}

// the #simCore script plus the page's preset tables, in one context
let CORE = null;
function core() {
  if (CORE) return CORE;
  const ctx = vm.createContext({ Math, Map, Set, Number, String, Object, Array, JSON, Promise, isFinite, Infinity, console });
  vm.runInContext(block("simWater") + "\n" + block("simTransport") + "\n" + block("simCore").replace(/^const (\w+) =/gm, "var $1 ="), ctx);
  const h = HTML(), a = h.indexOf("  // ready-made scenarios:"), b = h.indexOf("  let simGroup", a);
  vm.runInContext("var fnv36 = cFnv36;\n" + h.slice(a, b).replace(/\bconst (\w+) =/g, "var $1 ="), ctx);
  for (const n of ["SIM_GRAPH", "SIM_WORLD", "GAS_KBD"]) vm.runInContext(`var ${n} = ${mainConst(n)};`, ctx);
  return (CORE = ctx);
}
const C = n => vm.runInContext(n, core());

const cache = new Map();
const FILE = { power: "power.geojson", landings: "landings.geojson", fabs: "fabs.geojson", chipsites: "chip_sites.geojson", cloud: "cloud.geojson",
  air: "air.json", pipelines: "pipelines.geojson", chokepoints: "chokepoints.geojson", coal: "coal.geojson", iron: "iron.geojson", water_risk: "water_risk.geojson" };
function readJSON(f) {
  if (!cache.has(f)) {
    const p = join(DATA_DIR, f);
    cache.set(f, existsSync(p) ? JSON.parse(readFileSync(p, "utf8")) : null);
  }
  return cache.get(f);
}

// country totals with the domains.json figures joined in, as mergeDomains() does on the page
function totals() {
  if (cache.has("#totals")) return cache.get("#totals");
  const T = structuredClone(readJSON("country_totals.json") || {}), D = readJSON("domains.json");
  Object.values(T).forEach(c => c.fresh_km2 = (c.lake_km2 || 0) + (c.res_km2 || 0) || undefined);
  if (D) {
    for (const [k, c] of Object.entries(D.countries || {})) if (T[k]) for (const [f, v] of Object.entries(c)) if (v != null && !/Y$/.test(f)) T[k][f] = v;
    const F = D.food || {}, sum = {};
    for (const it of ["wheat", "maize", "rice"]) {
      for (const [k, v] of Object.entries((F.prod || {})[it] || {})) (sum[k] = sum[k] || { p: 0, i: 0, x: 0 }).p += v;
      for (const [a, b, v] of (F.trade || {})[it] || []) { (sum[a] = sum[a] || { p: 0, i: 0, x: 0 }).x += v; (sum[b] = sum[b] || { p: 0, i: 0, x: 0 }).i += v; }
    }
    for (const [k, o] of Object.entries(sum)) { const use = o.p + o.i - o.x; if (T[k] && use > 0) T[k].fdep = Math.max(0.001, Math.min(1, o.i / use)); }
  }
  cache.set("#totals", T);
  return T;
}

// people within km of a point, from the Kontur tiles in data/pop (same layout and maths as popInRadius() on the page)
const popTiles = new Map();
function popInRadius(lon, lat, km) {
  const idx = readJSON("pop/index.json"); if (!idx) return null;
  const hav = C("cHav"), T = idx.tile, Q = idx.q, dlat = km / 111.32, dlon = Math.min(180, km / (111.32 * Math.max(0.02, Math.cos(Math.min(89, Math.abs(lat) + dlat) * Math.PI / 180))));
  let sum = 0;
  for (let tx = Math.floor((lon - dlon) / T); tx <= Math.floor((lon + dlon) / T); tx++)
    for (let ty = Math.floor(Math.max(-90, lat - dlat) / T); ty <= Math.floor(Math.min(89.99, lat + dlat) / T); ty++) {
      const w = ((tx % (360 / T)) + 540 / T) % (360 / T) - 180 / T, nm = w + "_" + ty;
      if (!idx.tiles[nm]) continue;
      if (!popTiles.has(nm)) {
        const p = join(DATA_DIR, "pop", nm + ".bin"); if (!existsSync(p)) continue;
        const b = readFileSync(p), buf = b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength), Cn = T * T;
        const cnt = new Uint32Array(buf, 0, Cn), n = cnt.reduce((a, v) => a + v, 0), off = new Uint32Array(Cn + 1);
        for (let i = 0; i < Cn; i++) off[i + 1] = off[i] + cnt[i];
        popTiles.set(nm, { off, dx: new Uint8Array(buf, 4 * Cn, n), dy: new Uint8Array(buf, 4 * Cn + n, n), q: new Uint16Array(buf.slice(4 * Cn + 2 * n, 4 * Cn + 4 * n)) });
      }
      const t = popTiles.get(nm), [bx, by] = nm.split("_").map(Number);
      for (let c = 0; c < T * T; c++) {
        if (t.off[c] === t.off[c + 1]) continue;
        const x0 = bx * T + c % T, y0 = by * T + Math.floor(c / T);
        if (hav(lon, lat, x0 + 0.5, y0 + 0.5) > km + 80) continue;
        for (let i = t.off[c]; i < t.off[c + 1]; i++) if (hav(lon, lat, x0 + (t.dx[i] + 0.5) / 256, y0 + (t.dy[i] + 0.5) / 256) <= km) sum += Math.expm1(t.q[i] / Q);
      }
    }
  return sum;
}

// the road and rail worker (#simTransport + #simTransportWorker) in a sandbox, with fetch reading the local file
let TR = null;
async function traffic(zones) {
  if (!TR) {
    const msgs = [], ctx = vm.createContext({ Math, Map, Set, Infinity, Float64Array, Int32Array, Uint8Array, Uint32Array, Float32Array, Date, Array, Object, Number, String, console,
      postMessage: m => msgs.push(m), fetch: async () => ({ ok: true, json: async () => readJSON("transport_graph.json") }) });
    if (!readJSON("transport_graph.json")) return { type: "error", message: "transport_graph.json not built" };
    vm.runInContext(block("simTransport") + "\n" + block("simTransportWorker"), ctx);
    await ctx.onmessage({ data: { type: "init", url: "transport_graph.json" } });
    const ready = msgs.pop();
    if (!ready || ready.type !== "ready") return { type: "error", message: (ready && ready.message) || "transport model didn't start" };
    TR = { ctx, msgs, names: ready.names };
  }
  await TR.ctx.onmessage({ data: { type: "run", id: 1, zones } });
  const r = TR.msgs.pop();
  if (r && r.type === "result") { r.names = TR.names; r.parts = { road: true, rail: true }; r.n = zones.length; }
  return r;
}

// the oil and gas solver (#simWorker): solved once for today, then per scenario
let EN = null;
function energy() {
  if (EN) return EN;
  const G = readJSON("energy_graph.json"), E = readJSON("country_energy.json"), P = readJSON("pipelines.geojson");
  if (!G || !E) return (EN = { error: "energy_graph.json or country_energy.json not built" });
  const ctx = vm.createContext({ console, Date, Math });
  vm.runInContext(block("simWorker"), ctx);
  const base = vm.runInContext("init", ctx)({ graph: G, energy: E, totals: totals(), pipes: P ? P.features.map(f => f.properties.n) : [] });
  return (EN = { G, E, base, run: vm.runInContext("runShocks", ctx) });
}

let BASINS = null;
function basins() {
  if (!BASINS) {
    const B = readJSON("basins.json"), WR = readJSON("water_risk.geojson");
    if (!B || !WR || WR.features.length !== B.n) throw new Error("basins.json doesn't match water_risk.geojson");
    const idx = C("simBasinIndex")(WR);
    BASINS = { B, WB: idx.WB, at: idx.at, box: idx.box };
  }
  return BASINS;
}

function env() {
  const T = totals(), en = energy();
  return {
    totals: T, labels: readJSON("labels.json") || [], countries: readJSON("countries.geojson"), energy: en.E && en.E.countries, graph: en.G, boxes: {},
    load: async n => n === "air" ? readJSON("air.json") : readJSON(FILE[n] || n), domains: async () => readJSON("domains.json"),
    pop: async (x, y, km) => popInRadius(x, y, km), country: k => (T[k] && T[k].n) || k, traffic, basins: async () => basins()
  };
}

// "kind.target.percent;…" as in the page's #sim= hash (zones: zone.lon_lat_km.percent)
export function parseShocks(str) {
  const KIND = C("SIM_KIND");
  return String(str || "").split(/[;\s]+/).filter(Boolean).map(tok => {
    const a = tok.indexOf("."), b = tok.lastIndexOf(".");
    if (a < 0 || b <= a) throw new Error(`bad shock "${tok}": expected kind.target.percent`);
    const k = tok.slice(0, a), t = tok.slice(a + 1, b), pct = +tok.slice(b + 1);
    if (!KIND[k]) throw new Error(`unknown kind "${k}" (one of ${Object.keys(KIND).join(", ")})`);
    if (!isFinite(pct)) throw new Error(`bad percent in "${tok}"`);
    if (k === "zone") { const z = t.split("_").map(Number); if (z.length !== 3 || !z.every(isFinite)) throw new Error(`bad zone "${t}": lon_lat_km`); return [k, z, pct / 100]; }
    return [k, t, Math.max(0, Math.min(100, pct)) / 100];
  });
}
const token = ([k, t, m]) => `${k}.${k === "zone" ? t.join("_") : t}.${Math.round(m * 100)}`;
export const scenarioUrl = shocks => SITE + "#sim=" + encodeURIComponent(shocks.map(token).join(";")).replace(/%2C/g, ",").replace(/%3B/g, ";").replace(/%2E/g, ".");

export async function listPresets() {
  const groups = Object.fromEntries(C("SIM_GROUPS"));
  return C("SIM_PRESETS").map(([group, title, desc, shocks]) => ({ group, groupLabel: groups[group] || group, title, desc, shocks }));
}
export function listKinds() { return { kinds: C("SIM_KIND"), chokepoints: C("SIM_CHOKE"), pathogens: C("SIM_PATHO"), foods: C("SIM_FOOD") }; }
export async function listNews(window = "week") {
  const W = readJSON("scenarios_weekly.json");
  if (!W) return [];
  const items = (W.windows && W.windows[window] && W.windows[window].items) || (window === "week" ? W.items : []) || [];
  return items.map(x => ({ label: x.label, why: x.why, url: x.url, cat: x.cat || "conflict", t: x.t, trend: x.trend, likely: x.likely, months: x.months, levels: x.levels, lv: x.lv, shocks: x.shocks }));
}

const text = h => String(h).replace(/<[^>]+>/g, "").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"');

// Run a scenario. shocks: [[kind, target, share]]. Returns the chain of consequences the page would show, plus the
// world and country oil-and-gas tables when the network moved, the detailed facts as plain sentences, and a link.
export async function runScenario(shocks, opts = {}) {
  const E = env(), resolveCore = C("simResolveCore"), localCore = C("simLocalCore"), chainCore = C("simChainCore");
  const notes = [], list = [];
  for (const [k, t, m] of shocks) {
    const r = await resolveCore(E, k, Array.isArray(t) ? t.slice() : t);
    if (!r) { notes.push(`skipped ${k} ${Array.isArray(t) ? t.join(",") : t}: not in the data`); continue; }
    list.push({ k, t, m, ...r });
  }
  if (!list.length) return { title: "Nothing to run", steps: [], world: null, countries: [], facts: [], url: scenarioUrl(shocks), notes };
  // oil and gas: only when a shock touches the network
  const GRAPH = C("SIM_GRAPH"), gs = list.filter(s => GRAPH.includes(s.k)).map(({ k, t, m }) => ({ k, t, m }));
  let r = null;
  if (gs.length) {
    const en = energy();
    if (en.error) notes.push(en.error);
    else {
      r = en.run(gs);
      // the page only offers a "re-route" step when it drew some moved flows; mark that the same way
      const GAS = C("GAS_KBD"), Ed = en.G.edges;
      let fm = 0;
      for (const [e, b, s, c] of r.moved || []) { const d = Math.abs((Math.abs(s) - Math.abs(b)) * (c === "g" ? GAS : 1)); if (d >= 5 && !(Ed.ref[e] == null && "hb".includes(Ed.t[e]))) fm = Math.max(fm, d); }
      if (fm) E.boxes.flow = [-180, -60, 180, 75], E.boxes.flowMax = fm;
    }
  }
  const facts = await localCore(E, list);
  if (facts.traffic && facts.traffic.type === "result") E.boxes.traffic = [-180, -60, 180, 75];
  if (facts.water && facts.water.basins.length) { E.boxes.water = [-180, -60, 180, 75]; if (facts.water.dest.length) E.boxes.move = E.boxes.water; }
  const steps = chainCore(E, r, facts, list).map(s => ({ k: s.k, title: s.title, big: s.big, text: s.text }));
  const W = C("SIM_WORLD");
  const out = {
    title: list.length === 1 ? list[0].n : `${list.length} changes together: ` + list.map(s => s.n).join("; "),
    shocks: list.map(s => ({ kind: s.k, target: s.t, share: s.m, name: s.n, lon: s.x, lat: s.y })),
    steps, headline: facts.head.map(([v, t]) => `${v} ${t}`),
    world: r ? Object.fromEntries(W.map(([label, key, unit]) => [key, { label, unit, before: r.world[key][0], after: r.world[key][1] }])) : null,
    countries: r ? r.rows.map(x => ({ k: x.k, name: E.country(x.k), diesel: x.dsl, runs: x.run, gasShort: x.gasShort, gasPowerGW: x.gw }))
      .filter(x => Math.abs(x.diesel[1] - x.diesel[0]) + Math.abs(x.gasShort[1] - x.gasShort[0]) * 17 > 1)
      .sort((a, b) => Math.abs(b.diesel[1] - b.diesel[0]) - Math.abs(a.diesel[1] - a.diesel[0])).slice(0, opts.countries || 10) : [],
    chokepoints: r ? Object.entries(r.chokes).filter(([, v]) => Math.abs(v.o[1] - v.o[0]) > 5 || Math.abs(v.g[1] - v.g[0]) > 0.2)
      .map(([k, v]) => ({ k, name: C("SIM_CHOKE")[k], oil: v.o, gas: v.g })) : [],
    facts: facts.map(text), notes: notes.concat(r ? r.notes || [] : []), url: scenarioUrl(shocks)
  };
  return out;
}
