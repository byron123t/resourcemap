// Road and rail detour model tests: node --test 'tests/*.test.mjs'
// The model is the <script id="simTransport"> block of site/index.html, run here in a vm context.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../", import.meta.url);
const HTML = readFileSync(new URL("site/index.html", ROOT), "utf8");
const SRC = HTML.match(/<script id="simTransport">([\s\S]*?)<\/script>/)[1];
const ctx = vm.createContext({ Math, Map, Set, Infinity, Float64Array, Int32Array, Uint8Array });
vm.runInContext(SRC, ctx);
const fn = n => vm.runInContext(n, ctx);
const transportNet = fn("transportNet"), transportImpact = fn("transportImpact"), trDijkstra = fn("trDijkstra"), TRANSPORT = fn("TRANSPORT");

const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b} (±${tol})`);

// Cities W (west) and E (east) on the equator, 2° apart, joined by a straight road through M, and by a longer road
// bowing north through N. A third city S sits off on its own line.
//   W(0) --- M(1) --- E(2)
//     \              /
//      N3 ---------N4          S5 - S6
const D = 111.2;   // km per degree on the equator, as the model measures it
function toy() {
  const x = [0, 100, 200, 0, 200, 400, 500], y = [0, 0, 0, 100, 100, 0, 0];
  const km = (a, b) => Math.round(Math.hypot(x[a] - x[b], y[a] - y[b]) / 100 * D * 10);
  // parts: W-M-E as one part, W-N3-N4-E as another (keys are vertex indices x 100), S5-S6
  const parts = [[0, 100, 1, 200, 2], [0, 100, 3, 200, 4, 300, 2], [5, 100, 6]];
  const edges = [[0, 1], [1, 2], [0, 3], [3, 4], [4, 2], [5, 6]];
  return {
    cities: { n: ["W", "E", "S"], x: [0, 200, 500], y: [0, 0, 0], pop: [1e6, 1e6, 5e5], k: ["AAA", "AAA", "BBB"] },
    road: { x, y, parts, km: edges.map(([a, b]) => km(a, b)), city: [0, 2, 6], fast: [] }
  };
}

test("edges come out of the parts in order, with their length and speed", () => {
  const T = toy(), G = transportNet(T.road, "road");
  assert.equal(G.m, 6);
  assert.deepEqual([...G.ea], [0, 1, 0, 3, 4, 5]); assert.deepEqual([...G.eb], [1, 2, 3, 4, 2, 6]);
  assert.deepEqual([...G.k0], [0, 100, 0, 100, 200, 0]); assert.deepEqual([...G.k1], [100, 200, 100, 200, 300, 100]);
  near(G.h[0], D / 70, 0.01, "an hour per 70 km");
  const F = transportNet({ ...T.road, fast: [0] }, "road");
  near(F.h[0], D / 100, 0.01, "expressways are faster");
});

test("shortest times take the straight road", () => {
  const G = transportNet(toy().road, "road"), R = trDijkstra(G, 0, new Set([2]));
  near(R.dist[2], 2 * D / 70, 0.01, "W to E");
  assert.equal(R.dist[6], Infinity, "S is on another network");
});

test("a zone on the straight road sends traffic round the north", () => {
  const T = toy(), G = transportNet(T.road, "road");
  const r = transportImpact(T, G, [{ x: 1, y: 0, r: 20, m: 1 }], new Map());
  assert.equal(r.edges, 2, "both halves of the straight road touch the zone");
  assert.equal(r.cutW, 0, "nobody is cut off");
  assert.ok(r.slowW > 0 && r.through === r.slowW, "every W-E trip is slower");
  const p = r.pairs.find(p => p.i === 0 && p.j === 1);
  near(p.h1, (D + 2 * D + D) / 70, 0.05, "the detour: up, across, down");
  assert.ok(r.dvol.get(0) < 0 && r.dvol.get(3) > 0, "the straight road loses traffic, the northern road gains it");
});

test("partial damage slows the zone rather than closing it", () => {
  const T = toy(), G = transportNet(T.road, "road");
  const r = transportImpact(T, G, [{ x: 1, y: 0, r: 20, m: 0.5 }], new Map());
  const p = r.pairs.find(p => p.i === 0 && p.j === 1);
  near(p.h1, 4 * D / 70, 0.05, "twice as slow on 2 units is the same as the 4-unit detour");
  assert.equal(r.cutW, 0);
});

test("closing both roads cuts the cities off from each other", () => {
  const T = toy(), G = transportNet(T.road, "road");
  const r = transportImpact(T, G, [{ x: 1, y: 0, r: 20, m: 1 }, { x: 1, y: 1, r: 20, m: 1 }], new Map());
  assert.ok(r.cutW > 0 && r.cutW === r.through, "all W-E trips are lost");
  assert.equal(r.pairs.find(p => p.i === 0).h1, Infinity);
});

test("trips to a city inside the zone count as lost, not re-routed", () => {
  const T = toy(), G = transportNet(T.road, "road");
  const r = transportImpact(T, G, [{ x: 0, y: 0, r: 30, m: 1 }], new Map());
  assert.deepEqual([...r.inside], [0]);
  assert.ok(r.inZone > 0 && r.through === 0);
});

test("no zone, no change; the cache doesn't change answers", () => {
  const T = toy(), G = transportNet(T.road, "road"), cache = new Map();
  assert.equal(transportImpact(T, G, [], cache).edges, 0);
  const z = [{ x: 1, y: 0, r: 20, m: 1 }], a = transportImpact(T, G, z, cache), b = transportImpact(T, G, z, cache);
  assert.equal(a.slowW, b.slowW); assert.equal(a.dh, b.dh);
});

const TG = new URL("site/data/transport_graph.json", ROOT), RD = new URL("site/data/roads.geojson", ROOT);
test("real data: fighting near Kharkiv", { skip: !existsSync(TG) || !existsSync(RD) }, () => {
  const T = JSON.parse(readFileSync(TG, "utf8")), roads = JSON.parse(readFileSync(RD, "utf8"));
  const nParts = roads.features.reduce((a, f) => a + (f.geometry.type === "MultiLineString" ? f.geometry.coordinates.length : 1), 0);
  assert.equal(T.road.check[0], nParts, "transport_graph.json lines up with roads.geojson");
  const z = [{ x: 36.23, y: 49.99, r: 60, m: 1 }];
  for (const kind of ["road", "rail"]) {
    const G = transportNet(T[kind], kind), t0 = Date.now(), r = transportImpact(T, G, z, new Map());
    const ms = Date.now() - t0;
    assert.ok(ms < 8000, `${kind}: fast enough (${ms} ms)`);
    assert.ok(r.edges > 5, `${kind}: lines inside the zone (${r.edges})`);
    assert.ok(r.inside.some(i => T.cities.n[i] === "Kharkiv"), `${kind}: Kharkiv is inside`);
    assert.ok(r.through > 0 && r.slowW + r.cutW > 0, `${kind}: some trips pass through and are hit`);
    assert.ok(r.dvol.size > 0, `${kind}: some lines change`);
  }
});

test("the worker answers init and run with lines to draw and the worst pairs", async () => {
  const W = HTML.match(/<script type="text\/plain" id="simTransportWorker">([\s\S]*?)<\/script>/)[1];
  const sent = [], T = toy();
  T.road.check = [3, 7]; T.rail = { ...T.road }; T.snapKm = 3; T.cityKm = 25;
  const wctx = vm.createContext({ Math, Map, Set, Infinity, Float64Array, Int32Array, Uint8Array, Date, String,
    fetch: async () => ({ ok: true, json: async () => T }), postMessage: m => sent.push(m) });
  vm.runInContext(SRC + W, wctx);
  const send = d => vm.runInContext("onmessage", wctx)({ data: d });
  await send({ type: "init", url: "x" });
  assert.equal(sent[0].type, "ready"); assert.equal(sent[0].check.road, 3);
  await send({ type: "run", id: 4, zones: [{ x: 1, y: 0, r: 20, m: 1 }] });
  const r = sent[1];
  assert.equal(r.type, "result"); assert.equal(r.id, 4);
  const road = r.out.road;
  assert.ok(road.lines.length >= 4 && road.lines.every(l => l.length === 8), "part, keys, change, coordinates");
  assert.ok(road.lines.some(l => l[0] === 1 && l[3] > 0) && road.lines.some(l => l[0] === 0 && l[3] < 0));
  assert.equal(road.pairs[0].cut, false);
  near(road.km, 2 * D, 0.5, "both halves of the straight road");
  await send({ type: "run", id: 5, zones: [{ x: 1, y: 0, r: 20, m: 1 }, { x: 1, y: 1, r: 20, m: 1 }] });
  assert.equal(sent[2].out.road.pairs[0].cut, true);
});
