// CLI scenario engine tests: node --test 'tests/*.test.mjs'
// cli/lib/scenario.mjs runs the page's own model scripts (#simWorker, #simWater, #simTransport, #simCore) in node:vm on
// site/data. These check that it parses shocks like the page's #sim= hash and that each kind of scenario gives the
// consequence steps the page shows. Skipped where site/data isn't built.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { parseShocks, runScenario, listPresets, listNews, scenarioUrl } from "../cli/lib/scenario.mjs";

const REAL = ["energy_graph.json", "country_energy.json", "fabs.geojson", "domains.json", "air.json", "basins.json"]
  .every(f => existsSync(new URL("../site/data/" + f, import.meta.url)));
const big = (r, k) => (r.steps.find(s => s.k === k) || {}).big || "";
const num = s => +String(s).replace(/[^\d.]/g, "");

test("shock strings round-trip like the page's #sim= hash", () => {
  const s = parseShocks("choke.hormuz.100;zone.36.23_49.99_50.70;crop.wheat_RUS.30;outbreak.cov_114.30_30.59.50");
  assert.deepEqual(s[0], ["choke", "hormuz", 1]);
  assert.deepEqual(s[1], ["zone", [36.23, 49.99, 50], 0.7]);
  assert.deepEqual(s[2], ["crop", "wheat_RUS", 0.3]);
  assert.deepEqual(s[3], ["outbreak", "cov_114.30_30.59", 0.5]);
  assert.match(scenarioUrl(s), /#sim=choke\.hormuz\.100;zone\.36\.23_49\.99_50\.70;crop\.wheat_RUS\.30;outbreak\.cov_114\.30_30\.59\.50$/);
  assert.throws(() => parseShocks("nonsense.x.10"), /unknown kind/);
});

test("presets and news load", { skip: !REAL && "site/data not built" }, async () => {
  const P = await listPresets();
  assert.ok(P.length >= 60 && P.every(p => p.title && p.shocks.length));
  assert.ok(Array.isArray(await listNews("year")));
});

test("Hormuz closed strands oil and cuts diesel", { skip: !REAL && "site/data not built" }, async () => {
  const r = await runScenario(parseShocks("choke.hormuz.100"));
  assert.ok(r.world && r.world.stock.after - r.world.stock.before > 10000, "over 10 Mb/d stuck");
  assert.ok(r.steps.some(s => s.k === "fuel") && r.steps.some(s => s.k === "flow"));
});

test("a zone counts people, power and road detours", { skip: !REAL && "site/data not built" }, async () => {
  const r = await runScenario(parseShocks("zone.36.23_49.99_50.70"));
  assert.ok(num(big(r, "people")) > 1, "people inside");
  assert.ok(r.steps.some(s => s.k === "road"), "road and rail step");
});

test("chips, outbreaks and food give their own steps", { skip: !REAL && "site/data not built" }, async () => {
  const tw = await runScenario(parseShocks("fab.cc-twn.100"));
  assert.match(big(tw, "chips"), /leading-edge/);
  const ep = await runScenario(parseShocks("outbreak.cov_114.30_30.59.100"));
  assert.ok(num(big(ep, "spread")) >= 5, "countries reached");
  const fd = await runScenario(parseShocks("crop.wheat_RUS.30;crop.wheat_UKR.30"));
  assert.ok(fd.steps.some(s => s.k === "short") && num(big(fd, "exports")) > 10);
});

test("unknown targets are skipped with a note", { skip: !REAL && "site/data not built" }, async () => {
  const r = await runScenario(parseShocks("fab.no-such-fab.100"));
  assert.equal(r.steps.length, 0);
  assert.match(r.notes.join(" "), /not in the data/);
});
