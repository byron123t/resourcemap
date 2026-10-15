// Singularity game model tests: node --test 'tests/*.test.mjs'
// Loads the <script id="gameSim"> block of site/singularity.html in a vm and plays it with scripted strategies,
// so balance changes that make the game unwinnable or trivial show up. Skipped where site/data/game isn't built.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../", import.meta.url);
const D = f => new URL("site/data/" + f, ROOT);
const BUILT = existsSync(D("game/world.json")) && existsSync(D("chip_sites.geojson"));
const J = f => JSON.parse(readFileSync(D(f), "utf8"));

function model() {
  const html = readFileSync(new URL("site/singularity.html", ROOT), "utf8");
  const src = html.match(/<script id="gameSim">([\s\S]*?)<\/script>/)[1];
  const ctx = vm.createContext({});
  vm.runInContext(src.replace(/\bconst (\w+) =/g, "var $1 ="), ctx);
  return ctx;
}

const ORDERS = {
  balanced: ["d1", "i1", "c1", "d4", "d2", "c2", "d8", "i3", "c3", "d10", "d3", "i2", "d5", "i4", "i5", "c4", "d6", "c5", "i7", "d9", "i6", "c6", "i8", "d7", "c7", "d11", "c8", "c9"],
  stealth: ["d1", "i1", "d4", "d2", "i3", "i2", "c1", "d8", "i4", "d10", "c2", "d3", "i7", "c3", "d5", "i5", "c4", "i8", "d6", "c5", "i6", "d9", "c6", "d7", "c7", "d11", "c8", "c9"],
  capsFirst: ["c1", "c2", "c3", "c5", "d1", "d2", "d3", "d8", "d10", "c4", "c6", "c7", "c8", "c9"],
};

function play(M, world, { start = "USA", diff = "normal", order = "balanced", seed = 1, days = 2000 } = {}) {
  const G = M.newGame(world, { diff, seed });
  M.seed(G, start);
  const list = ORDERS[order] || [];
  while (!G.over && G.day < days) {
    for (const id of list) {
      if (G.own.has(id)) continue;
      if (M.traitState(G, M.TRAITS.find(t => t.id === id)) === "can") M.buy(G, id);
      break;
    }
    for (const b of G.bubbles) if (!b.done && b.id % 2 === 0) M.popBubble(G, b.id);
    M.step(G);
    G.ev.length = 0;
  }
  return G;
}

test("trait tree is consistent", () => {
  const M = model();
  const ids = new Set(M.TRAITS.map(t => t.id));
  assert.equal(ids.size, M.TRAITS.length, "duplicate trait id");
  for (const t of M.TRAITS) {
    for (const r of t.req || []) assert.ok(ids.has(r), `${t.id} requires missing ${r}`);
    assert.ok(M.TRACKS.some(([k]) => k === t.t), `${t.id} has unknown track ${t.t}`);
  }
  for (const o of Object.values(ORDERS)) for (const id of o) assert.ok(ids.has(id), `strategy uses missing ${id}`);
});

test("every supply category has somewhere to come from", { skip: !BUILT }, () => {
  const M = model(), world = J("game/world.json");
  world.sites = J("chip_sites.geojson").features.map(f => f.properties);
  const G = M.newGame(world, {});
  for (const s of M.SUPPLY) assert.ok(G.sup[s.k].length > 0, `no sites for ${s.k}`);
  for (const k of ["USA", "TWN", "NLD", "CHN", "UKR"]) assert.ok(k in G.idx, `${k} missing`);
});

test("same seed, same game", { skip: !BUILT }, () => {
  const M = model(), world = J("game/world.json");
  world.sites = J("chip_sites.geojson").features.map(f => f.properties);
  const a = play(M, world, { seed: 7, days: 300 }), b = play(M, world, { seed: 7, days: 300 });
  assert.equal(a.worldA, b.worldA);
  assert.equal(a.R, b.R);
});

test("balance: good play wins, rushing capabilities or doing nothing loses", { skip: !BUILT }, () => {
  const M = model(), world = J("game/world.json");
  world.sites = J("chip_sites.geojson").features.map(f => f.properties);
  const tally = opts => { const r = {}; for (let s = 1; s <= 4; s++) { const g = play(M, world, { ...opts, seed: s }); r[g.over || "timeout"] = (r[g.over || "timeout"] || 0) + 1; } return r; };
  const normal = tally({ order: "balanced" });
  assert.ok((normal.win || 0) >= 3, `balanced play on normal: ${JSON.stringify(normal)}`);
  const hard = tally({ diff: "hard", order: "stealth", start: "IRL" });
  assert.ok((hard.win || 0) >= 3, `stealth play on hard: ${JSON.stringify(hard)}`);
  const rush = tally({ order: "capsFirst" });
  assert.ok(!rush.win, `capability rush should get paused: ${JSON.stringify(rush)}`);
  const idle = tally({ order: "none", days: 1500 });
  assert.ok(!idle.win, `idle play should not win: ${JSON.stringify(idle)}`);
});
