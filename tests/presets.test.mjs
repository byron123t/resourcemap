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
  const a = HTML.indexOf("const SIM_KIND = "), b = HTML.indexOf("let simGroup", a);
  const fnv = HTML.match(/function fnv36\(s\) \{[^\n]*\}/)[0];
  const ctx = vm.createContext({});
  vm.runInContext(fnv + "\n" + HTML.slice(a, b).replace(/\bconst (\w+) =/g, "var $1 ="), ctx);
  return { list: ctx.SIM_PRESETS, kinds: ctx.SIM_KIND, chokes: ctx.SIM_CHOKE, groups: ctx.SIM_GROUPS.map(g => g[0]), fnv36: ctx.fnv36 };
}

function resolver(fnv36) {
  const G = J("energy_graph.json");
  const pipes = new Set(J("pipelines.geojson").features.map(f => fnv36(f.properties.n)));
  const hashed = f => new Set(J(f).features.map(x => fnv36(x.properties.n + "|" + x.properties.k)));
  const power = hashed("power.geojson"), coal = hashed("coal.geojson"), iron = hashed("iron.geojson");
  const cables = new Set(J("landings.geojson").features.map(f => f.properties.id));
  const basins = new Set(J("basins.json").pf.map(String));
  const kinds = { refinery: "R", field: "F", port: "P", lng: "XI" };
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
    return false;
  };
}

test("presets are well formed", () => {
  const P = presets();
  assert.ok(P.list.length >= 30, `${P.list.length} presets`);
  for (const [g, title, desc, shocks] of P.list) {
    assert.ok(P.groups.includes(g) && g !== "news", `${title}: group ${g}`);
    assert.ok(title && desc, `${title}: needs a title and description`);
    assert.ok(Array.isArray(shocks) && shocks.length, `${title}: no shocks`);
    for (const s of shocks) assert.ok(s[0] in P.kinds, `${title}: kind ${s[0]}`);
  }
  for (const g of P.groups) assert.ok(g === "news" || P.list.some(p => p[0] === g), `group ${g} is empty`);
});

test("every preset target exists in the built data", { skip: !REAL && "site/data not built" }, () => {
  const P = presets(), ok = resolver(P.fnv36);
  const bad = [];
  for (const [, title, , shocks] of P.list) for (const s of shocks) if (!ok(P, s)) bad.push(`${title}: ${JSON.stringify(s)}`);
  assert.deepEqual(bad, []);
});

test("weekly news scenarios resolve", { skip: (!REAL || !existsSync(D("scenarios_weekly.json"))) && "no scenarios_weekly.json" }, () => {
  const P = presets(), ok = resolver(P.fnv36), W = J("scenarios_weekly.json");
  assert.ok(Array.isArray(W.items) && W.items.length <= 12, "items: a list of at most 12");
  const bad = [];
  for (const x of W.items) {
    assert.ok(typeof x.label === "string" && x.label.trim(), "label");
    assert.ok(x.url == null || /^https?:\/\//.test(x.url), `${x.label}: url`);
    assert.ok(Array.isArray(x.shocks) && x.shocks.length, `${x.label}: shocks`);
    for (const s of x.shocks) if (!(Array.isArray(s) && s.length === 3 && s[0] in P.kinds && ok(P, s))) bad.push(`${x.label}: ${JSON.stringify(s)}`);
  }
  assert.deepEqual(bad, []);
});
