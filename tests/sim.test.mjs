// Scenario solver tests: node --test tests/
// The solver lives in site/index.html as <script type="text/plain" id="simWorker">; it is pulled out and run in a vm context,
// first on small hand-made graphs, then (if the built data is there) on the real energy network.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../", import.meta.url);
const HTML = readFileSync(new URL("site/index.html", ROOT), "utf8");
const SRC = HTML.match(/<script type="text\/plain" id="simWorker">([\s\S]*?)<\/script>/)[1];

// a fresh solver per graph: init() keeps the graph in module state
function solver(d) {
  const ctx = vm.createContext({ console, Date, Math });
  vm.runInContext(SRC, ctx);
  const base = vm.runInContext("init", ctx)(d);
  return { base, run: vm.runInContext("runShocks", ctx), fnv36: vm.runInContext("fnv36", ctx), ctx };
}

// toy graph builder. Nodes: [type, lon, lat, extra]; edges: [a, b, type, commodity, km, cap, extra]
function graph(nodes, edges, more = {}) {
  const N = { t: "", x: [], y: [], k: [], so: {}, sg: {}, dr: {}, dg: {}, cap: {} };
  const ids = {}, rt = {};
  nodes.forEach(([t, x, y, o = {}], i) => {
    N.t += t; N.x.push(Math.round(x * 100)); N.y.push(Math.round(y * 100)); N.k.push(o.k ?? 0);
    for (const f of ["so", "sg", "dr", "dg", "cap"]) if (o[f] != null) N[f][i] = o[f];
    if (o.id) ids[o.id] = i;
    if (o.rt) rt[i] = o.rt;
  });
  const E = { a: [], b: [], t: "", c: "", km: [], cap: [], dir: [], ci: [], ref: {} };
  edges.forEach(([a, b, t, c, km, cap, o = {}], i) => {
    E.a.push(a); E.b.push(b); E.t += t; E.c += c; E.km.push(km); E.cap.push(cap ?? 0);
    if (o.dir) E.dir.push(i);
    if (o.ref != null) E.ref[i] = o.ref;
  });
  return { v: 1, countries: ["AAA", "BBB"], nodes: N, names: {}, rt, ids, edges: E, choke: more.choke || {}, consts: {} };
}

const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b} (±${tol})`);
const flowOn = (r, e, c = "o") => { const m = r.moved.find(x => x[0] === e && x[3] === c); return m ? [m[1], m[2]] : null; };

test("a binding pipeline leaves crude in the ground", () => {
  const G = graph([["F", 0, 0, { so: 100 }], ["R", 1, 0, { dr: 100, rt: "m", id: "r1" }]], [[0, 1, "p", "o", 100, 50]]);
  const { base, run } = solver({ graph: G, energy: { countries: {} }, totals: {}, pipes: [] });
  near(base.world.run[0], 50, 0.2, "refinery run limited by the pipe");
  const r = run([]);
  near(r.world.stock[0], 50, 0.2, "the rest stays in the ground");
});

test("closing the cheap pipeline moves the flow to the dearer one", () => {
  const G = graph([["F", 0, 0, { so: 100 }], ["R", 1, 0, { dr: 200, id: "r1" }]],
    [[0, 1, "p", "o", 100, 500, { ref: 0 }], [0, 1, "p", "o", 300, 500, { ref: 1 }]]);
  const { base, run, fnv36 } = solver({ graph: G, energy: { countries: {} }, totals: {}, pipes: ["Cheap", "Dear"] });
  const r = run([{ k: "pipeline", t: fnv36("Cheap"), m: 1 }]);
  assert.deepEqual(flowOn(r, 0), [100, 0]);
  assert.deepEqual(flowOn(r, 1), [0, 100]);
  near(r.world.run[1], base.world.run[0], 0.01, "nothing lost overall");
});

test("above 70% of capacity flow spills onto a parallel pipe", () => {
  // demand is 95 (refinery at the 95% ceiling); the short pipe takes 70 at 100/km, then 3x costs more than the 200 km pipe
  const G = graph([["F", 0, 0, { so: 200 }], ["R", 1, 0, { dr: 100 }]],
    [[0, 1, "p", "o", 100, 100, { ref: 0 }], [0, 1, "p", "o", 200, 100, { ref: 1 }]]);
  const { base, run, fnv36 } = solver({ graph: G, energy: { countries: {} }, totals: {}, pipes: ["Short", "Long"] });
  near(base.world.run[0], 95, 0.2, "refinery at the ceiling");
  // closing the long one shows its baseline share
  const r = run([{ k: "pipeline", t: fnv36("Long"), m: 1 }]);
  const [b] = flowOn(r, 1);
  near(b, 25, 0.2, "long pipe carried the spill");
  const [b0, s0] = flowOn(r, 0);
  near(b0, 70, 0.2, "short pipe filled to 70%");
  near(s0, 95, 0.2, "short pipe runs hot once it is the only route");
});

test("a closed chokepoint sends tankers the long way round", () => {
  const G = graph([
    ["F", 0, 0, { so: 100 }], ["S", 1, 0], ["S", 5, 0], ["S", 3, -20], ["R", 6, 0, { dr: 100 }],
  ], [
    [0, 1, "l", "o", 10], [1, 2, "s", "*", 450], [1, 3, "s", "*", 2500], [3, 2, "s", "*", 2500], [2, 4, "l", "o", 10],
  ], { choke: { hormuz: [1] } });
  const { base, run } = solver({ graph: G, energy: { countries: {} }, totals: {}, pipes: [] });
  const r = run([{ k: "choke", t: "hormuz", m: 1 }]);
  near(r.chokes.hormuz.o[0], 95, 0.2, "strait carried everything");
  near(r.chokes.hormuz.o[1], 0, 0.01, "strait empty when closed");
  near(r.world.run[1], base.world.run[0], 0.01, "the detour still delivers");
  assert.deepEqual(flowOn(r, 2), [0, 95]);
});

test("neighbouring refineries absorb an outage up to their spare capacity", () => {
  // 100 of crude for 200 of nameplate: both run at 50, and either can go to 95
  const G = graph([["F", 0, 0, { so: 100 }], ["R", 1, 0, { dr: 100, id: "r1" }], ["R", 0, 1, { dr: 100, id: "r2" }]],
    [[0, 1, "p", "o", 100, 500], [0, 2, "p", "o", 100, 500]]);
  const { base, run } = solver({ graph: G, energy: { countries: {} }, totals: {}, pipes: [] });
  near(base.u0, 0.5, 1e-9, "world run rate");
  near(base.world.run[0], 100, 0.2, "all crude refined");
  const r = run([{ k: "refinery", t: "r1", m: 1 }]);
  near(r.world.run[1], 95, 0.2, "the other refinery runs at its ceiling");
  near(r.world.stock[1], 5, 0.2, "the rest is stored");
  // half an outage: the shocked plant keeps half its demand
  const h = run([{ k: "refinery", t: "r1", m: 0.5 }]);
  near(h.world.run[1], 100, 0.2, "half outage still refines everything");
});

test("diesel follows the country yield, else the refinery type", () => {
  const G = graph([["F", 0, 0, { so: 100 }], ["R", 1, 0, { dr: 100, rt: "h", k: 0 }], ["R", 0, 1, { dr: 100, rt: "d", k: 1 }]],
    [[0, 1, "p", "o", 100, 500], [0, 2, "p", "o", 100, 500]]);
  const { run } = solver({ graph: G, energy: { countries: { AAA: { dy: 0.45 } } }, totals: {}, pipes: [] });
  const r = run([{ k: "zone", t: [0, 1, 20], m: 1 }]);   // knocks out the BBB plant only
  const row = k => r.rows.find(x => x.k === k);
  near(row("AAA").dsl[0], 50 * 0.45, 0.1, "AAA uses its EIA yield");
  near(row("BBB").dsl[0], 50 * 0.38, 0.1, "BBB falls back to the complex-plant yield");
  near(row("AAA").dsl[1], 95 * 0.45, 0.1, "AAA picks up the slack");
  near(row("BBB").dsl[1], 0, 0.01, "zone shuts BBB");
});

test("a gas shortfall derates gas-fired power", () => {
  const G = graph([["F", 0, 0, { sg: 10 }], ["J", 0.5, 0], ["D", 1, 0, { dg: 10 }]],
    [[0, 1, "l", "g", 10], [1, 2, "p", "g", 100, 10, { ref: 0 }]]);   // the line runs full
  const d = { graph: G, energy: { countries: { AAA: { gc: 10 } } }, totals: { AAA: { pw_gas: 20 } }, pipes: ["Line"] };
  const { base, run, fnv36 } = solver(d);
  near(base.world.gasShort[0], 0, 0.01, "no shortfall today");
  const r = run([{ k: "pipeline", t: fnv36("Line"), m: 1 }]);
  near(r.world.gasShort[1], 10, 0.01, "all gas cut off");
  near(r.world.gw[1], 20, 0.01, "whole gas fleet at risk");
  near(r.world.strand[1], 10, 0.01, "the gas is stranded");
  const h = run([{ k: "pipeline", t: fnv36("Line"), m: 0.5 }]);
  near(h.world.gw[1], 10, 0.01, "half the gas, half the fleet");
});

test("a shortage is shared out instead of landing on the furthest buyer", () => {
  const G = graph([["F", 0, 0, { sg: 10 }], ["D", 1, 0, { dg: 10, k: 0 }], ["D", 9, 0, { dg: 10, k: 1 }]],
    [[0, 1, "p", "g", 100, 100], [0, 2, "p", "g", 900, 100]]);
  const { base, ctx } = solver({ graph: G, energy: { countries: {} }, totals: {}, pipes: [] });
  near(base.world.gas[0], 10, 0.01, "all supply delivered");
  // the first half of each demand is worth more than the second half anywhere, so each buyer gets 5
  const c = vm.runInContext("BASE.c", ctx);
  near(c.AAA.gas, 5, 0.01, "near buyer");
  near(c.BBB.gas, 5, 0.01, "far buyer");
});

test("unknown ids come back as notes, not errors", () => {
  const G = graph([["F", 0, 0, { so: 10 }], ["R", 1, 0, { dr: 10 }]], [[0, 1, "p", "o", 100, 50]]);
  const { run } = solver({ graph: G, energy: { countries: {} }, totals: {}, pipes: [] });
  const r = run([{ k: "refinery", t: "nope", m: 1 }, { k: "pipeline", t: "zzz", m: 1 }]);
  assert.equal(r.notes.length, 2);
  assert.equal(r.rows.length, 0);
});

// ---- the real network (skipped when the pipeline hasn't been run) ----
const D = f => new URL("site/data/" + f, ROOT);
const REAL = ["energy_graph.json", "country_energy.json", "country_totals.json", "pipelines.geojson"].every(f => existsSync(D(f)));

test("real network", { skip: !REAL && "site/data not built" }, async t => {
  const J = f => JSON.parse(readFileSync(D(f), "utf8"));
  const G = J("energy_graph.json"), E = J("country_energy.json");
  const pipes = J("pipelines.geojson").features.map(f => f.properties.n);
  const t0 = Date.now();
  const { base, run, fnv36 } = solver({ graph: G, energy: E, totals: J("country_totals.json"), pipes });
  const initMs = Date.now() - t0;

  await t.test("baseline solves fast and refines most crude", () => {
    assert.ok(base.ms < 3000, `baseline ${base.ms} ms`);
    assert.ok(initMs < 10000, `init ${initMs} ms`);
    const r = run([]);
    assert.ok(r.world.stock[0] < 0.1 * r.world.crude[0], `stock ${r.world.stock[0]} of ${r.world.crude[0]}`);
    assert.equal(r.moved.length, 0);
  });

  await t.test("Jamnagar offline cuts India's diesel by about its own output", () => {
    const i = G.ids.r3143912, cap = G.nodes.dr[i], dy = E.countries.IND.dy;
    const own = cap * base.u0 * dy;
    const r = run([{ k: "refinery", t: "r3143912", m: 1 }]);
    const ind = r.rows.find(x => x.k === "IND");
    const drop = ind.dsl[0] - ind.dsl[1];
    assert.ok(drop >= 0.5 * own && drop <= 1.0 * own, `India diesel −${drop.toFixed(0)} vs plant ${own.toFixed(0)} kb/d`);
    assert.ok(r.ms < 3000, `${r.ms} ms`);
  });

  await t.test("Hormuz closed: at least 10 Mb/d rerouted or unmet", () => {
    const r = run([{ k: "choke", t: "hormuz", m: 1 }]);
    const h = r.chokes.hormuz.o;
    assert.ok(h[0] >= 10000, `baseline through Hormuz ${h[0]}`);
    assert.ok(h[1] < 1, `still through Hormuz ${h[1]}`);
    const lost = r.world.run[0] - r.world.run[1];
    const rerouted = ["suez", "bab", "cape"].reduce((a, c) => a + Math.max(0, r.chokes[c].o[1] - r.chokes[c].o[0]), 0);
    assert.ok(lost + rerouted >= 10000, `lost ${lost} + rerouted ${rerouted}`);
    assert.ok(r.world.gw[1] > 0, "Gulf LNG loss puts some gas power at risk");
  });

  await t.test("pipeline ids match between the page and the solver", () => {
    const r = run([{ k: "pipeline", t: fnv36("Druzhba Oil Pipeline"), m: 1 }]);
    assert.equal(r.notes.length, 0, r.notes.join("; "));
    assert.ok(r.moved.length > 0);
  });
});
