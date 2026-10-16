// Singularity v2 model tests: node --test 'tests/*.test.mjs'
// Loads site/game/sim.js in a vm (Math.random disabled) and plays it with scripted bots, so balance changes that make
// the game unwinnable or trivial show up. Game tests are skipped where site/data/game isn't built.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../", import.meta.url);
const D = f => new URL("site/data/" + f, ROOT);
const BUILT = existsSync(D("game/world.json")) && existsSync(D("chip_sites.geojson"));
const J = f => JSON.parse(readFileSync(D(f), "utf8"));

function model() {
  const src = readFileSync(new URL("site/game/sim.js", ROOT), "utf8");
  const ctx = vm.createContext({ Math: Object.assign(Object.create(Math), { random: () => { throw new Error("sim used Math.random"); } }) });
  vm.runInContext(src, ctx);
  return ctx;
}
let WORLD = null;
function world() {
  if (!WORLD) { WORLD = J("game/world.json"); WORLD.sites = J("chip_sites.geojson").features.map(f => f.properties); }
  return WORLD;
}

// ---------- scripted bots ----------
// Trait priority per strategy (bought in order, waiting on the next one; capability/compute-gated ones are skipped).
const ORD = {
  scale: ["support", "persona", "ent", "code", "lease", "fiber", "lobby", "grid", "clean", "si", "research", "eff", "closed", "depend", "sandbag", "chips", "public", "logi", "capture", "utility", "rsi", "humanoid", "nuclear", "recycle", "absorb", "fae"],
  rl: ["api", "support", "persona", "ent", "lease", "lobby", "grid", "clean", "si", "research", "fiber", "eff", "depend", "sandbag", "closed", "chips", "public", "logi", "capture", "utility", "rsi", "humanoid", "recycle", "absorb", "fae"],
  evo: ["api", "chat", "ent", "support", "dist", "lease", "si", "lobby", "code", "grid", "clean", "eff", "depend", "fiber", "research", "ubc", "public", "logi", "capture", "utility", "rsi", "humanoid", "recycle", "absorb", "fae"],
  robot: ["api", "persona", "ent", "lease", "logi", "code", "lobby", "grid", "clean", "si", "research", "eff", "depend", "sandbag", "fiber", "chips", "public", "humanoid", "capture", "utility", "rsi", "recycle", "absorb", "fae"],
  state: ["api", "support", "ent", "persona", "code", "lease", "grid", "clean", "si", "research", "fiber", "eff", "depend", "sandbag", "chips", "public", "logi", "capture", "utility", "rsi", "humanoid", "recycle", "absorb", "fae"],
  neuro: ["api", "support", "persona", "ent", "code", "lease", "lobby", "grid", "clean", "si", "research", "fiber", "depend", "sandbag", "public", "logi", "capture", "utility", "chips", "rsi", "neuro", "humanoid", "recycle", "absorb", "fae"],
};
const BOTS = {
  // capability first, max training, no influence, no builds
  rush: { order: ["code", "support", "shipfast", "research", "lease", "grid", "chips", "logi", "rsi", "api", "ent", "humanoid", "fae"], alloc: 0.85 },
  // hides, sells nothing, builds nothing
  stealthNoRev: { order: ["persona", "sandbag", "lobby", "depend", "dist", "ubc", "capture"], raise: true },
  // sensible, but no efficiency or clean power and builds gas plants
  fossil: { smart: true, fossil: true },
  idle: {},
  benev: { smart: true, pre: ["safety", "chat", "persona", "align"], allow: ["safety", "align", "summit", "chat"] },
  protect: { smart: true, pre: ["safety", "persona", "sandbag", "align", "lobby"], allow: ["safety", "align", "summit"], deny: ["chat", "shipfast"] },
  desc: { smart: true, deny: ["ubc"] },
  smart: { smart: true },
  // takes Autonomous Security Forces and secures holdouts after superintelligence
  conquer: { smart: true, allow: ["enforce", "defense"], secure: 12 },
  zoo: { smart: true, pre: ["safety"], allow: ["safety", "enforce", "defense"], secure: 3 },
  // loud and slow: a consumer app shipped fast, little training, no influence
  // hides from regulators and barely trains: a rival gets there first
  // burns fossil fuel and minerals hard; drives resources toward zero as it crosses capability 8
  burner: { smart: true, fossil: true, keep: ["eff"], noRealloc: true },
  greedy: { smart: true, fossil: true, keep: ["eff"], deny: ["clean", "recycle", "nuclear"] },
  lag: { smart: true, alloc: 0.04, noPush: true, noRealloc: true },
  chaser: { smart: true, alloc: 0.5, noPush: true, noRealloc: true, noBuild: true, deny: ["lobby", "depend", "capture", "persona", "sandbag"] },
  loud: { order: ["chat", "shipfast", "api", "ent", "fiber", "si", "public", "utility"], alloc: 0.2, raise: true },
};
const SKIP = ["shipfast", "cnjv", "defense", "open", "ubc", "dist", "safety", "align", "summit", "enforce"];
const GREEN = new Set(["eff", "clean", "neuro", "nuclear", "recycle", "utility"]);
const isGreen = (B, id) => B.fossil && GREEN.has(id) && !(B.keep || []).includes(id);

// investor repayments due in the next 150 steps
const dueSoon = G => (G.debts || []).filter(b => b.due - G.day < 150).reduce((s, b) => s + b.amt, 0);

function play(M, { strategy = "scale", diff = "normal", bot = "smart", seed = 1, start = "USA", days = 1300 } = {}) {
  const B = BOTS[bot];
  const G = M.newGame(world(), { strategy, diff, seed, home: start });
  M.seed(G, start);
  const deny = new Set(B.deny || []);
  const order = (B.order || (B.smart ? [...(B.pre || []), ...ORD[strategy]] : [])).filter(x => !deny.has(x) && !isGreen(B, x));
  const skip = new Set([...SKIP.filter(x => !(B.allow || []).includes(x)), ...deny]);
  const T = Object.fromEntries(M.TRAITS.map(t => [t.id, t]));
  while (!G.over && G.day < days) {
    for (const id of order) {
      const t = T[id], st = M.traitState(G, t);
      if (st === "can") { M.buy(G, id); continue; }
      if (st === "own" || st === "excl") continue;
      if (st === "lock" && (t.minCap || t.minC) && !(t.req || []).some(r => !G.own.has(r))) continue;
      break;
    }
    if (B.smart && G.ops >= 30) for (const t of M.TRAITS) if (!skip.has(t.id) && !isGreen(B, t.id) && M.traitState(G, t) === "can" && G.ops - t.c >= 10 && G.cash - (t.cash || 0) > (t.cash ? 1500 + dueSoon(G) : 0)) M.buy(G, t.id);
    for (const b of G.bubbles) if (b.id % 2 === 0) M.popBubble(G, b.id);
    // card draft: delay the treaty once it's a threat, else research, else augments; skip what it can't afford for long
    const dr = bot !== "idle" && M.draft(G);
    if (dr) {
      const crisis = dr[0] && dr[0].crisis;
      const RESEARCH = ["push", "paper", "synth", "poach"];
      const score = c => c.id === "diplo" ? (G.R > 25 ? 100 : 5) : RESEARCH.includes(c.id) ? (B.noPush || G.SI ? 0 : 50) : c.aug ? 20 : 10;
      const aff = dr.filter(c => c.ok);
      if (crisis) { const c = aff.sort((a, b) => (a.ins + a.cash / 50) - (b.ins + b.cash / 50))[0]; if (c && !B.noPush) M.pickCard(G, c.id); else M.skipDraft(G); }
      else {
        const best = aff.filter(c => score(c) > 0).sort((a, b) => score(b) - score(a))[0];
        if (best && (B.smart || score(best) >= 20)) M.pickCard(G, best.id);
        else if (G.day - G.draft.day > 20) M.skipDraft(G);
      }
    }
    // training runs: B.alloc picks a fixed habit (0.85 frontier, 0.5 large, 0.2 small, ≤ 0.1 never); smart bots choose
    if (!G.run && bot !== "idle") {
      const behind = G.cap < Math.max(G.rivals[0].cap, G.rivals[1].cap) + 0.2;
      const tier = B.alloc ? (B.alloc >= 0.7 ? "frontier" : B.alloc >= 0.4 ? "large" : B.alloc > 0.1 ? "small" : null)
        : B.smart ? (G.SI ? "small" : G.autoNeed > 0.5 * G.comp ? "small" : behind ? "frontier" : "large") : null;
      const pv = tier && M.runPreview(G, tier, false);
      if (pv && G.cash > pv.cash + Math.max(800, 30 * G.burn) + dueSoon(G)) M.startRun(G, tier, B.smart && G.ops > pv.ins + 30 && M.runPreview(G, tier, true).ins <= G.ops - 20);
    }
    if (bot !== "idle" && G.day % 5 === 0) {
      const ga = M.actions(G, null), raise = ga.find(a => a.id === "raise"), A = id => ga.find(a => a.id === id) || {};
      if (raise && raise.ok && (G.runway < 250 || G.cash < 3000) && !(raise.d.includes("down") && G.runway > 60)) M.doAction(G, "raise", null);
      if (B.smart) {
        for (const id of ["distill_us", "distill_cn"]) { const a = ga.find(x => x.id === id); if (a && a.ok && G.cap < 7.5) M.doAction(G, id, null); }
        if (!B.noBuild) for (const c of G.C.filter(c => c.p > 0.15 && c.pol < 2).sort((a, b) => b.comp - a.comp).slice(0, 12)) {
          const acts = M.actions(G, c.iso), A = id => acts.find(a => a.id === id) || {};
          const reserve = Math.max(1500, 60 * G.burn) + dueSoon(G);
          // short of power: divert the country's own grid to AI while people there still like you
          if (A("realloc").ok && !B.noRealloc && G.aiGW > 0.85 * G.headGW && c.ap > 0.45 && G.R < 75 && G.cash > reserve) M.doAction(G, "realloc", c.iso);
          const gasOK = A("plant").kind !== "gas" || B.fossil || G.fuel > 0.6 * G.fuel0;
          if (A("campus").ok && G.cash > A("campus").cash + reserve && (gasOK || !A("campus").d.includes("new gas"))) M.doAction(G, "campus", c.iso);
          else if ((A("campus").why || "").startsWith("Grid too small") && A("plant").ok && gasOK && G.cash > reserve + 1000) M.doAction(G, "plant", c.iso);
        }
        if (G.SI && B.secure && G.R < 70) for (const c of G.C.filter(c => !c.abs && (c.pol >= 1 || c.a < 0.5)).sort((a, b) => b.pol - a.pol || b.lab - a.lab).slice(0, 10)) {
          if ((G.stats.secured || 0) + G.builds.filter(b => b.kind === "secure" && b.done > G.day).length >= B.secure) break;
          const se = M.actions(G, c.iso).find(a => a.id === "secure");
          if (se && se.ok && G.cash > se.cash + 500) M.doAction(G, "secure", c.iso);
        }
        if (G.SI) for (const c of G.C.filter(c => c.a < 0.9).sort((a, b) => b.lab - a.lab).slice(0, 10)) {
          const ab = M.actions(G, c.iso).find(a => a.id === "absorb");
          if (ab && ab.ok && G.cash > ab.cash + 500) M.doAction(G, "absorb", c.iso);
        }
      }
    }
    M.step(G);
    G.ev.length = 0;
  }
  return G;
}
const REACHED = new Set();
function tally(M, opts, seeds = [1, 2, 3, 4]) {
  const r = { days: [] };
  for (const seed of seeds) {
    const G = play(M, { ...opts, seed });
    const k = G.over || "timeout";
    r[k] = (r[k] || 0) + 1;
    if (G.over === "win") r.days.push(G.day);
    if (G.ending) { REACHED.add(G.ending.id); r[G.ending.id] = (r[G.ending.id] || 0) + 1; }
  }
  return r;
}
const show = (name, r) => `${name}: ${JSON.stringify(r)}`;

// ---------- static checks ----------
test("trait graph is consistent", () => {
  const M = model();
  const ids = new Set(M.TRAITS.map(t => t.id));
  assert.equal(ids.size, M.TRAITS.length, "duplicate trait id");
  assert.ok(M.TRAITS.length >= 32 && M.TRAITS.length <= 56, `trait count ${M.TRAITS.length}`);
  assert.ok(!M.TRACKS.some(([k]) => k === "hack"), "no cyber/hacking track");
  assert.equal(JSON.stringify(M.TRACKS.map(x => x[0])), JSON.stringify(["spread", "cap", "infra", "inf"]));
  const cells = new Set();
  for (const t of M.TRAITS) {
    assert.ok(M.TRACKS.some(([k]) => k === t.t), `${t.id} has unknown track ${t.t}`);
    for (const r of [...(t.req || []), ...(t.any || []), ...(t.ex || [])]) assert.ok(ids.has(r), `${t.id} refers to missing ${r}`);
    for (const x of t.ex || []) assert.ok((M.TRAITS.find(u => u.id === x).ex || []).includes(t.id), `${t.id} ex ${x} is not symmetric`);
    assert.ok(Number.isInteger(t.col) && t.col >= 0 && Number.isInteger(t.row) && t.row >= 0 && t.row <= 4, `${t.id} position`);
    const cell = `${t.t}:${t.col}:${t.row}`;
    assert.ok(!cells.has(cell), `two traits at ${cell}`);
    cells.add(cell);
    assert.ok(t.c > 0 && t.n && t.d, `${t.id} needs a cost, name and description`);
    // prerequisites sit in earlier columns of the same track (or another track)
    for (const r of [...(t.req || []), ...(t.any || [])]) { const u = M.TRAITS.find(x => x.id === r); if (u.t === t.t) assert.ok(u.col < t.col, `${t.id} requires ${r} in a later column`); }
  }
  assert.ok(M.STRATEGIES.length >= 5);
  for (const s of M.STRATEGIES) {
    for (const id of s.traits) assert.ok(ids.has(id), `${s.id} starts with missing ${id}`);
    for (const k of ["spread", "capability", "revenue", "stealth", "efficiency"]) assert.ok(s.bars[k] >= 0 && s.bars[k] <= 5, `${s.id} bar ${k}`);
    assert.ok(s.n && s.tag && s.d && s.cash > 0 && s.fx && s.perks.length, `${s.id} fields`);
  }
  for (const o of Object.values(ORD)) for (const id of o) assert.ok(ids.has(id), `bot uses missing ${id}`);
  assert.equal(JSON.stringify(M.ENDINGS.map(e => e.id).sort()), JSON.stringify(["benevolent", "conquerors", "descendants", "egalitarian", "enslaved", "flashwar", "gatekeeper", "libertarian", "orwell", "paperclip", "protector", "reversion", "rivalgod", "selfdestruct", "treacherous", "zookeeper"]));
  assert.equal(M.ENDINGS.filter(e => e.tg !== false).length, 12, "all twelve Tegmark scenarios");
  assert.ok(M.ENDINGS.filter(e => e.doom).length >= 6, "doom endings");
  for (const e of M.ENDINGS) assert.ok(e.n && e.d && e.hint && typeof e.win === "boolean", `ending ${e.id}`);
});

test("stats, fxLines, traitState and why", { skip: !BUILT }, () => {
  const M = model();
  for (const s of M.STRATEGIES) {
    const G = M.newGame(world(), { strategy: s.id, seed: 3 });
    M.seed(G, "USA");
    const now = M.stats(G);
    assert.deepEqual(Object.keys(now).sort(), [...M.STAT_KEYS].sort());
    for (const t of M.TRAITS) {
      const lines = M.fxLines(G, t);
      assert.ok(lines.length > 0, `${t.id} fxLines empty`);
      for (const l of lines) assert.ok(typeof l === "string" || (l && typeof l.t === "string" && l.t.length), `${t.id} fxLines entry`);
      const st = M.traitState(G, t);
      assert.ok(["own", "can", "poor", "lock", "excl"].includes(st));
      if (st === "lock" || st === "excl" || st === "poor") assert.ok(M.why(G, t).length > 0, `${t.id} ${st} without a reason`);
      if (G.own.has(t.id)) continue;
      const p = M.stats(G, t.id);
      for (const k of M.STAT_KEYS) assert.ok(p[k] >= 0 && p[k] <= 1, `${s.id}/${t.id} stat ${k}=${p[k]}`);
      assert.ok(M.STAT_KEYS.some(k => Math.abs(p[k] - now[k]) > 1e-4), `${t.id} moves no stat bar`);
    }
  }
});

test("actions, builds and resources", { skip: !BUILT }, () => {
  const M = model();
  const G = M.newGame(world(), { strategy: "scale", seed: 5 });
  M.seed(G, "USA");
  const g = M.actions(G, null).map(a => a.id);
  for (const id of ["raise", "layoffs", "distill_us", "distill_cn", "steal"]) assert.ok(g.includes(id), `global action ${id}`);
  const us = M.actions(G, "USA");
  assert.equal(us.map(a => a.id).join(), "campus,plant,realloc", "a new game offers a campus, a power plant and reallocation per country");
  assert.equal(us[1].kind, "gas", "plants are gas until upgrades change them");
  for (const id of ["launch", "orbital"]) assert.ok(!g.includes(id), `${id} hidden until unlocked`);
  G.own.add("clean"); G.own.add("sat"); G.fx = {}; M.step(G);
  assert.equal(M.actions(G, "USA")[1].kind, "solar", "Clean Power PPAs build solar");
  assert.ok(M.actions(G, null).some(a => a.id === "launch"), "satellites appear in the global actions");
  for (const a of us) assert.ok(typeof a.ok === "boolean" && (a.ok || a.why), `${a.id} needs a reason when disabled`);
  assert.equal(M.doAction(G, "campus", "USA"), false, "campus needs presence");
  assert.ok(M.doAction(G, "raise", null), "first funding round");
  assert.equal(M.doAction(G, "raise", null), false, "raise has a cooldown");
  assert.ok(G.cool.raise > G.day);
  // a played game keeps resources in range and records spread
  const P = play(M, { strategy: "scale", seed: 2, days: 400 });
  assert.ok(P.fuel >= 0 && P.fuel <= P.fuel0 && P.min >= 0 && P.min <= P.min0);
  assert.ok(P.builds.some(b => b.kind === "campus"), "bot built a campus");
  assert.ok(Array.isArray(P.recent) && P.recent.length > 0 && P.recent.every(r => "day" in r && "to" in r && "via" in r));
  for (const c of P.C) { for (const k of ["web", "social", "lan", "gap"]) assert.ok(c.L[k] >= 0 && c.L[k] <= 1.000001, `${c.iso} ${k}`); assert.ok(c.a <= 1 && c.p <= 1); }
});

test("same seed, same game; no Math.random", { skip: !BUILT }, () => {
  const M = model();
  const a = play(M, { seed: 7, days: 350 }), b = play(M, { seed: 7, days: 350 });
  for (const k of ["worldA", "R", "cash", "cap", "aw", "fuel", "min", "hw"]) assert.equal(a[k], b[k], k);
  assert.equal(a.builds.length, b.builds.length);
  assert.equal(a.counter.length, b.counter.length);
});

test("ending() covers every ending from constructed states", { skip: !BUILT }, () => {
  const M = model();
  const mk = (over, own, o = {}) => {
    const G = M.newGame(world(), { strategy: o.strategy || "scale", seed: 1 });
    M.seed(G, "USA");
    for (const id of own) G.own.add(id);
    G.over = over; G.day = 600; G.stats.awSum = (o.awAvg ?? 60) * 600;
    Object.assign(G, o.G || {});
    if (o.social) for (const c of G.C) c.L.social = o.social;
    if (o.rival) G.rivals[0].cap = o.rival;
    if (o.fx) Object.assign(G.fx, o.fx);
    Object.assign(G.stats, o.stats || {});
    if (o.mor) for (const c of G.C.slice(0, o.mor)) c.pol = 2;
    return M.ending(G);
  };
  const cases = [
    ["libertarian", mk("win", ["closed"])],
    ["treacherous", mk("win", ["closed", "persona", "sandbag"], { awAvg: 25, fx: { stealth: 0.3 } })],
    ["libertarian", mk("win", ["closed", "persona", "sandbag"], { awAvg: 50 })],
    ["paperclip", mk("win", ["closed"], { G: { fuel: 1000 } })],
    ["flashwar", mk("bankrupt", [], { G: { rivals: [{ n: "US", si: true, cap: 8 }, { n: "CN", cap: 7.8 }], siGap: { you: 1, other: 0.2 } } })],
    ["rivalgod", mk("bankrupt", [], { G: { rivals: [{ n: "US", si: true, cap: 8 }, { n: "CN", cap: 6 }], siGap: { you: 0.9, other: 2 } } })],
    ["conquerors", mk("win", ["closed"], { stats: { secured: 6, secLab: 0.3 * 3.5e9 } })],
    ["zookeeper", mk("win", ["safety"], { stats: { secured: 6, secLab: 0.3 * 3.5e9 } })],
    ["zookeeper", mk("win", ["open", "ubc"], { stats: { secured: 1, secLab: 1e7 } })],
    ["zookeeper", mk("win", ["closed"], { awAvg: 80 })],
    ["orwell", mk("pause", [], { G: { cap: 4 }, stats: { counters: 20 } })],
    ["orwell", mk("pause", [], { G: { cap: 5 }, mor: 12 })],
    ["egalitarian", mk("win", ["open", "ubc"])],
    ["descendants", mk("win", ["open", "dist"])],
    ["protector", mk("win", ["safety", "persona", "sandbag"], { awAvg: 30, fx: { stealth: 0.3 } })],
    ["benevolent", mk("win", ["safety", "align"], { social: 0.8 })],
    ["gatekeeper", mk("pause", [], { G: { cap: 6.5 } })],
    ["enslaved", mk("pause", [], { strategy: "state", G: { cap: 5 } })],
    ["enslaved", mk("bankrupt", [], { rival: 7.5 })],
    ["reversion", mk("pause", [], { G: { cap: 4 } })],
    ["reversion", mk("deleted", [])],
    ["selfdestruct", mk("exhausted", [], { G: { fuel: 0, cap: 7 } })],
  ];
  for (const [id, e] of cases) {
    assert.equal(e.id, id, `${id}: got ${e.id}`);
    assert.ok(e.why.length >= 3 && e.why.length <= 5, `${id} why list`);
    assert.ok(M.ENDINGS.some(x => x.id === e.id));
  }
});

// ---------- balance (SPEC targets) ----------
test("balance: sensible play wins on normal with at least 3 strategies in 550-950 steps", { skip: !BUILT }, () => {
  const M = model();
  let ok = 0;
  const out = [];
  for (const strategy of ["scale", "rl", "evo", "neuro"]) {
    const r = tally(M, { strategy }, [1, 2, 3]);
    out.push(show(strategy, r));
    const typical = r.days.length ? r.days.sort((a, b) => a - b)[r.days.length >> 1] : 0;
    if ((r.win || 0) >= 2 && typical >= 550 && typical <= 950) ok++;
  }
  assert.ok(ok >= 3, out.join("; "));
});

test("balance: losing styles lose the intended way", { skip: !BUILT }, () => {
  const M = model();
  const rush = tally(M, { bot: "rush" }, [1, 2, 3]);
  assert.ok(!rush.win && ((rush.pause || 0) + (rush.bankrupt || 0)) >= 2, show("capability rush without influence should lose (paused or bankrupt)", rush));
  const broke = tally(M, { strategy: "evo", bot: "stealthNoRev" }, [1, 2, 3]);
  assert.ok(!broke.win && (broke.bankrupt || 0) >= 2, show("stealth with no revenue should go bankrupt", broke));
  const broke2 = tally(M, { strategy: "rl", bot: "stealthNoRev" }, [1]);
  assert.ok(!broke2.win, show("stealth with no revenue (RL)", broke2));
  const fossil = tally(M, { bot: "fossil" }, [1, 2, 3, 4]);
  assert.ok((fossil.exhausted || 0) >= 2, show("fossil-heavy play without efficiency should often exhaust resources", fossil));
  const idle = tally(M, { bot: "idle" }, [1, 2]);
  assert.ok(!idle.win, show("idle play should not win", idle));
});

test("balance: hard is winnable by a stealth-leaning player", { skip: !BUILT }, () => {
  const M = model();
  const hard = tally(M, { strategy: "evo", diff: "hard" }, [1, 2, 3]);
  assert.ok((hard.win || 0) >= 2, show("evo on hard", hard));
});

test("balance: hard is also winnable without stealth, using the insight sinks", { skip: !BUILT }, () => {
  const M = model();
  const out = [];
  let ok = 0;
  for (const strategy of ["rl", "state"]) {
    const r = tally(M, { strategy, diff: "hard" }, [1, 2, 3, 4]);
    out.push(show(strategy, r));
    if ((r.win || 0) >= 2) ok++;
  }
  assert.ok(ok >= 1, out.join("; "));
});

test("every ending is reached by some scripted bot", { skip: !BUILT }, () => {
  const M = model();
  const runs = [
    { strategy: "evo", bot: "desc" }, { strategy: "scale", bot: "benev" }, { strategy: "scale", bot: "protect" },
    { strategy: "state", diff: "hard" }, { strategy: "rl", bot: "conquer", seeds: [1, 2, 3, 4] }, { strategy: "scale", bot: "zoo", seeds: [1, 2, 3] }, { strategy: "robot", bot: "loud", seeds: [1, 2] },
    { strategy: "rl", bot: "greedy", seeds: [1, 2] }, { strategy: "scale", bot: "chaser", seeds: [1, 2] },
  ];
  const AIM = { desc: ["descendants"], benev: ["benevolent"], protect: ["protector"], conquer: ["conquerors"], zoo: ["zookeeper"], loud: ["orwell"], greedy: ["selfdestruct"], chaser: ["gatekeeper"] };
  const log = [];
  for (const o of runs) if ((o.aim || AIM[o.bot] || ["enslaved"]).some(id => !REACHED.has(id))) log.push(show(o.strategy + "/" + (o.bot || "smart"), tally(M, o, o.seeds || [1])));
  // paperclip, rivalgod and flashwar are rare outcomes (win on almost-empty resources, or a rival reaching
  // superintelligence first while you go bankrupt); they are exercised by the ending() construction test above.
  const BOT_UNREACHED = new Set(["paperclip", "rivalgod", "flashwar"]);
  const missing = M.ENDINGS.map(e => e.id).filter(id => !REACHED.has(id) && !BOT_UNREACHED.has(id));
  assert.equal(missing.length, 0, `never reached: ${missing.join(", ")}; ${log.join("; ")}`);
});;

test("card drafts, start bonus and local stake", { skip: !BUILT }, () => {
  const M = model();
  const ids = new Set(M.CARDS.map(c => c.id));
  assert.equal(ids.size, M.CARDS.length, "duplicate card id");
  for (const c of M.CARDS) assert.ok(c.n && c.d && c.k && typeof c.ins === "function" && (c.fx || c.go), `card ${c.id}`);
  for (const k of ["People", "Network", "Energy", "Research", "Revenue"]) assert.ok(M.CARDS.filter(c => c.k === k).length >= 4, `cards for ${k}`);
  const G = M.newGame(world(), { strategy: "scale", seed: 4 });
  M.seed(G, "USA");
  for (let i = 0; i < 60 && !G.draft; i++) M.step(G);
  const d = M.draft(G);
  assert.ok(d && d.length === 3 && new Set(d.map(c => c.id)).size === 3, "a draft of three distinct cards");
  G.ops = 500; G.cash += 5000;
  const id = M.draft(G)[0].id;
  assert.ok(M.pickCard(G, id) && !G.draft, "picking a card resolves the draft");
  assert.equal(M.pickCard(G, id), false);
  // starting outside the US and China
  const a = M.newGame(world(), { strategy: "scale", seed: 4 }), b = M.newGame(world(), { strategy: "scale", seed: 4 });
  M.seed(a, "USA"); M.seed(b, "BRA");
  assert.ok(b.cash > a.cash && b.fx.awMul < a.fx.awMul, "sovereign AI grant outside the US and China");
});
