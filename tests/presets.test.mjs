// Scenario preset tests: node --test 'tests/*.test.mjs'
// Every SIM_PRESETS entry in site/index.html and every item in site/data/scenarios_weekly.json must point at
// something in the built data, or the button does nothing. Skipped where site/data isn't built.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../", import.meta.url);
const HTML = readFileSync(new URL("site/index.html", ROOT), "utf8");
const D = f => new URL("site/data/" + f, ROOT);
const REAL = ["energy_graph.json", "pipelines.geojson", "power.geojson", "coal.geojson", "iron.geojson", "landings.geojson", "basins.json"].every(f => existsSync(D(f)));
const J = f => JSON.parse(readFileSync(D(f), "utf8"));

// pull SIM_PRESETS (and the helpers it uses) out of the page script
function presets() {
  // the kinds, chokepoints, pathogens and foods live in the #simCore script; the presets and groups in the main one
  const core = HTML.match(/<script id="simCore">([\s\S]*?)<\/script>/)[1];
  const a = HTML.indexOf("// ready-made scenarios:"), b = HTML.indexOf("let simGroup", a);
  const ctx = vm.createContext({ Math, Map, Set });
  vm.runInContext(core.replace(/^const (\w+) =/gm, "var $1 =") + "\nvar fnv36 = cFnv36;\n" + HTML.slice(a, b).replace(/\bconst (\w+) =/g, "var $1 ="), ctx);
  return { list: ctx.SIM_PRESETS, kinds: ctx.SIM_KIND, chokes: ctx.SIM_CHOKE, groups: ctx.SIM_GROUPS.map(g => g[0]), news: Object.keys(ctx.SIM_NEWS), fnv36: ctx.fnv36,
    patho: ctx.SIM_PATHO, food: ctx.SIM_FOOD };
}

function resolver(fnv36) {
  const G = J("energy_graph.json");
  const pipes = new Set(J("pipelines.geojson").features.map(f => fnv36(f.properties.n)));
  const hashed = f => new Set(J(f).features.map(x => fnv36(x.properties.n + "|" + x.properties.k)));
  const power = hashed("power.geojson"), coal = hashed("coal.geojson"), iron = hashed("iron.geojson");
  const cables = new Set(J("landings.geojson").features.map(f => f.properties.id));
  const basins = new Set(J("basins.json").pf.map(String));
  const kinds = { refinery: "R", field: "F", port: "P", lng: "XI" };
  const ids = f => existsSync(D(f)) ? new Set(J(f).features.map(x => x.properties.id)) : new Set();
  const fabs = existsSync(D("fabs.geojson")) ? J("fabs.geojson").features : [], fabIds = new Set(fabs.map(f => f.properties.id));
  const chips = ids("chip_sites.geojson"), clouds = ids("cloud.geojson");
  const food = existsSync(D("domains.json")) ? (J("domains.json").food || {}).prod || {} : {};
  return (P, [k, t, m]) => {
    assert.ok(m > 0 && m <= 1, `share ${m}`);
    if (kinds[k]) return G.ids[t] != null && kinds[k].includes(G.nodes.t[G.ids[t]]);
    if (k === "pipeline") return pipes.has(t);
    if (k === "choke") return t in P.chokes && (G.choke[t] || []).length > 0;
    if (k === "zone") return Array.isArray(t) && t.length === 3 && Math.abs(t[0]) <= 180 && Math.abs(t[1]) <= 90 && t[2] >= 5 && t[2] <= 200;
    if (k === "power") return power.has(t);
    if (k === "mine") return (t[0] === "c" ? coal : iron).has(t.slice(2));
    if (k === "cable") return cables.has(t);
    if (k === "water") return basins.has(String(t));
    if (k === "fab") return t.startsWith("cc-") ? fabs.some(f => f.properties.cc === t.slice(3).toUpperCase()) : fabIds.has(t);
    if (k === "chip") return chips.has(t);
    if (k === "cloud") return clouds.has(t);
    if (k === "outbreak") { const [ph, x, y] = t.split("_"); return ph in P.patho && Math.abs(+x) <= 180 && Math.abs(+y) <= 90; }
    if (k === "crop") { const [it, cc] = t.split("_"); return it in P.food && !!(food[it] || {})[cc]; }
    return false;
  };
}

test("presets are well formed", () => {
  const P = presets();
  assert.ok(P.list.length >= 30, `${P.list.length} presets`);
  for (const [g, title, desc, shocks] of P.list) {
    assert.ok(P.groups.includes(g) && !P.news.includes(g), `${title}: group ${g}`);
    assert.ok(title && desc, `${title}: needs a title and description`);
    assert.ok(Array.isArray(shocks) && shocks.length, `${title}: no shocks`);
    for (const s of shocks) assert.ok(s[0] in P.kinds, `${title}: kind ${s[0]}`);
  }
  for (const g of P.groups) assert.ok(P.news.includes(g) || P.list.some(p => p[0] === g), `group ${g} is empty`);
});

test("every preset target exists in the built data", { skip: !REAL && "site/data not built" }, () => {
  const P = presets(), ok = resolver(P.fnv36);
  const bad = [];
  for (const [, title, , shocks] of P.list) for (const s of shocks) if (!ok(P, s)) bad.push(`${title}: ${JSON.stringify(s)}`);
  assert.deepEqual(bad, []);
});

test("news scenarios resolve", { skip: (!REAL || !existsSync(D("scenarios_weekly.json"))) && "no scenarios_weekly.json" }, () => {
  const P = presets(), ok = resolver(P.fnv36), W = J("scenarios_weekly.json");
  const MAX = { week: 12, month: 20, year: 30 };
  assert.ok(Array.isArray(W.items) && W.items.length <= MAX.week, "items: a list of at most 12");
  const wins = W.windows || { week: { items: W.items } };
  for (const w of Object.keys(wins)) assert.ok(P.news.includes(w), `window ${w} has no tab`);
  const bad = [], good = s => Array.isArray(s) && s.length === 3 && s[0] in P.kinds && ok(P, s);
  for (const [w, { items }] of Object.entries(wins)) {
    assert.ok(Array.isArray(items) && items.length <= MAX[w], `${w}: a list of at most ${MAX[w]}`);
    for (const x of items) {
      assert.ok(typeof x.label === "string" && x.label.trim(), "label");
      assert.ok(x.url == null || /^https?:\/\//.test(x.url), `${x.label}: url`);
      assert.ok(Array.isArray(x.shocks) && x.shocks.length, `${x.label}: shocks`);
      for (const s of x.shocks) if (!good(s)) bad.push(`${w} ${x.label}: ${JSON.stringify(s)}`);
      if (!x.levels) continue;
      for (const k of ["de", "cur", "esc"]) {
        assert.ok(Array.isArray(x.levels[k]) && x.levels[k].length, `${x.label}: level ${k}`);
        for (const s of x.levels[k]) if (!good(s)) bad.push(`${w} ${x.label} ${k}: ${JSON.stringify(s)}`);
      }
      // the levels should get worse in order: zone radius and damage never shrink from de to cur to esc
      const z = k => x.levels[k].find(s => s[0] === "zone");
      if (z("de") && z("cur") && z("esc")) {
        assert.ok(z("de")[1][2] <= z("cur")[1][2] && z("cur")[1][2] <= z("esc")[1][2], `${x.label}: radius order`);
        assert.ok(z("de")[2] <= z("cur")[2] && z("cur")[2] <= z("esc")[2], `${x.label}: damage order`);
      }
    }
  }
  assert.deepEqual(bad, []);
});
