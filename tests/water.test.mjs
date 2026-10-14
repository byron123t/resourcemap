// River-basin drought model tests: node --test 'tests/*.test.mjs'
// The model is the <script id="simWater"> block of site/index.html, run here in a vm context.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../", import.meta.url);
const HTML = readFileSync(new URL("site/index.html", ROOT), "utf8");
const SRC = HTML.match(/<script id="simWater">([\s\S]*?)<\/script>/)[1];
const ctx = vm.createContext({ Math, Map, Set, String, Float64Array, Uint8Array });
vm.runInContext(SRC, ctx);
const waterImpact = vm.runInContext("waterImpact", ctx), waterCat = vm.runInContext("waterCat", ctx), WATER = vm.runInContext("WATER", ctx);

const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b} (±${tol})`);

// A river: 0 and 1 are headwaters joining at 2, which flows to 3 and then 4 at the sea. 5 is a separate river with a city.
//   0 \
//      2 - 3 - 4        5
//   1 /
function river() {
  return {
    n: 6, countries: ["AAA", "BBB"],
    pf: [111, 112, 110, 100, 10, 900], dn: [2, 2, 3, 4, -1, -1], ua: [100, 100, 250, 400, 1000, 300],
    pop: [1e5, 2e5, 1e6, 3e6, 5e6, 2e6], k: [0, 0, 0, 1, 1, 1],
    cx: [0, 50, 25, 100, 200, 300].map(v => v * 1), cy: [0, 0, 0, 0, 0, 0],
    cities: [["Upstream", 10, 0, 500000, 0], ["Dryville", 100, 0, 900000, 3], ["Wetton", 300, 0, 2000000, 5], ["Faraway", 5000, 4000, 9000000, 5]]
  };
}
const W = { wr: [0.3, 0.05, 0.3, 0.5, 0.15, 0.05], ws: [2, 0, 2, 3, 1, 0] };

test("Aqueduct stress bands", () => {
  assert.deepEqual([0, 0.09, 0.1, 0.19, 0.2, 0.39, 0.4, 0.79, 0.8, 5].map(waterCat), [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]);
});

test("a dried basin takes its upstream catchment with it, and downstream loses a share", () => {
  const R = waterImpact(river(), W, [{ t: "110", m: 0.5 }]);
  const loss = Object.fromEntries(R.basins.map(b => [b.i, b.loss]));
  near(loss[0], 0.5, 1e-9, "headwater 0"); near(loss[1], 0.5, 1e-9, "headwater 1"); near(loss[2], 0.5, 1e-9, "the basin itself");
  near(loss[3], 0.5 * 250 / 400, 1e-9, "next basin down, by upstream area");
  near(loss[4], 0.5 * 250 / 1000, 1e-9, "river mouth");
  assert.equal(loss[5], undefined, "another river is untouched");
  near(R.people, 1e5 + 2e5 + 1e6 + 3e6 + 5e6, 1, "people in affected basins");
});

test("stress rises as water falls: wr / remaining share", () => {
  const R = waterImpact(river(), W, [{ t: "110", m: 0.5 }]);
  const b2 = R.basins.find(b => b.i === 2);
  near(b2.wr1, 0.6, 1e-9, "0.3 / 0.5");
  assert.equal(b2.c0, 2); assert.equal(b2.c1, 3);
  // newly high stress: basins 0 (0.3 → 0.6), 2 (0.3 → 0.6) and 3 (0.5 → 0.73 is already band 3, so not new)
  near(R.exposed, 1e5 + 1e6, 1, "people newly under high stress");
});

test("the effect fades out downstream below 1%", () => {
  const B = river(); B.ua[4] = 1e9;
  const R = waterImpact(B, W, [{ t: "110", m: 0.5 }]);
  assert.ok(!R.basins.some(b => b.i === 4), "a huge downstream basin barely notices");
});

test("people leave only highly stressed basins that lost enough water, towards cities with water to spare", () => {
  const R = waterImpact(river(), W, [{ t: "110", m: 0.8 }]);
  // 0, 1, 2 lose 80% (≥75%: 5% leave), but only 0 and 2 end highly stressed (1: 0.05 / 0.2 = 0.25, band 2);
  // 3 loses 50% (≥40%: 3%, 0.5 / 0.5 = 1, band 4); 4 loses 20% (≥15%: 1%) but 0.15 / 0.8 = 0.19 is band 1, so nobody
  const want = 0.05 * (1e5 + 1e6) + 0.03 * 3e6;
  near(R.moved, want, 1, "people who move");
  assert.ok(R.dest.length >= 1);
  assert.ok(R.dest.every(d => d.b === 5), "only the unaffected, unstressed river receives people");
  assert.equal(R.dest[0].n, "Wetton", "the near city outpulls the far one");
  near(R.placed, R.moved, 1, "everyone who moves is placed");
  near(R.dest.reduce((a, d) => a + d.people, 0), R.moved, 1, "destinations add up");
});

test("by country, unknown basins, zero magnitude", () => {
  const R = waterImpact(river(), W, [{ t: "110", m: 0.5 }]);
  near(R.countries.AAA.people, 1.3e6, 1, "AAA"); near(R.countries.BBB.people, 8e6, 1, "BBB");
  assert.equal(waterImpact(river(), W, [{ t: "999", m: 1 }]).notes.length, 1);
  assert.equal(waterImpact(river(), W, [{ t: "110", m: 0 }]).basins.length, 0);
});

test("two shocks on one river compound", () => {
  const R = waterImpact(river(), W, [{ t: "111", m: 0.5 }, { t: "110", m: 0.5 }]);
  near(R.basins.find(b => b.i === 0).loss, 0.75, 1e-9, "basin 0 hit twice");
});

const BJ = new URL("site/data/basins.json", ROOT), WRJ = new URL("site/data/water_risk.geojson", ROOT);
test("real data: Indus drought", { skip: !existsSync(BJ) || !existsSync(WRJ) }, () => {
  const B = JSON.parse(readFileSync(BJ, "utf8")), WR = JSON.parse(readFileSync(WRJ, "utf8")).features;
  assert.equal(B.n, WR.length, "basins.json lines up with water_risk.geojson");
  const Wr = { wr: WR.map(f => f.properties.wr ?? null), ws: WR.map(f => f.properties.ws) };
  const t0 = Date.now(), R = waterImpact(B, Wr, [{ t: "456100", m: 0.4 }]);
  assert.ok(Date.now() - t0 < 2000, "fast enough to run on every slider move");
  assert.ok(R.basins.length > 50, `the whole Indus catchment: ${R.basins.length} basins`);
  assert.ok(R.people > 1e8 && R.people < 4e8, `people in the Indus basin: ${R.people}`);
  const ks = Object.keys(R.countries);
  assert.ok(ks.includes("PAK") && ks.includes("IND"), "Pakistan and India both share the river");
  assert.ok(R.moved > 0 && R.dest.length > 0, "some people move somewhere");
});
