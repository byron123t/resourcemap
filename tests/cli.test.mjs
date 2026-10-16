// CLI and MCP server (cli/): page-constant extraction, queries on the built data, and an MCP round-trip.
// node --test tests/cli.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DATA = path.join(ROOT, "site", "data");
const CLI = path.join(ROOT, "cli", "atlas.mjs"), MCP = path.join(ROOT, "cli", "mcp.mjs");
const HAS_ENGINE = existsSync(path.join(ROOT, "cli", "lib", "scenario.mjs"));
const page = await import("../cli/lib/page.mjs");
const api = await import("../cli/lib/api.mjs");
const TOKYO = [139.69, 35.69];

test("page constants extract from site/index.html", () => {
  const all = page.loadAll();
  for (const n of page.REQUIRED) assert.ok(all[n] && Object.keys(all[n]).length, `${n} is empty`);
  assert.ok(Object.keys(all.FILES).length > 20 && Object.keys(all.VIEWS).length > 10 && all.SOURCES.length > 20);
  for (const [k, f] of Object.entries(all.FILES)) assert.ok(existsSync(path.join(DATA, f)), `FILES.${k} → ${f} is missing from site/data`);
  for (const [k, v] of Object.entries(all.VIEWS)) {
    assert.ok(v.label && v.g, `view ${k} has a label and group`);
    for (const l of v.on || []) assert.ok(all.VIEW_KEYS.includes(l), `view ${k} switches on unknown layer ${l}`);
  }
  for (const s of all.SOURCES) assert.ok(s[0] && s[3], `source ${s[0]} has a licence`);
  assert.ok(page.baseOptions().some(o => o.value === "c:net"), "background-shading options parse");
  assert.throws(() => page.pageConst("NO_SUCH_CONSTANT_X"), /not found/);
});

test("query: power plants near Tokyo, filtered and sorted", async () => {
  const r = await api.query("power", { near: TOKYO, km: 50, where: ["q>1"], limit: 50 });
  assert.ok(r.matched > 3, "several plants over 1 GW within 50 km of Tokyo");
  assert.ok(r.rows.every(x => x.km <= 50 && x.props.q > 1));
  assert.ok(r.rows.every((x, i) => !i || r.rows[i - 1].km <= x.km), "nearest first");
  const g = await api.query("power", { near: TOKYO, km: 50, where: ["f=gas"], sort: "-q", limit: 5 });
  assert.ok(g.rows.length && g.rows.every(x => x.props.f === "gas"));
  assert.ok(g.rows.every((x, i) => !i || g.rows[i - 1].props.q >= x.props.q), "sorted by q descending");
  const tw = await api.query("fabs", { country: "Taiwan", where: ["o~tsmc"], limit: 100 });
  assert.ok(tw.matched > 5 && tw.rows.every(x => x.props.cc === "TWN"));
});

test("country profile: Japan", async () => {
  const c = await api.country("JPN");
  assert.equal(c.k, "JPN");
  assert.match(c.n, /Japan/);
  assert.ok(c.sites.power.length === 5 && c.sites.power[0].value > 5, "largest power plants listed");
  assert.ok(c.domains.some(d => d.field === "net"), "people online");
  assert.ok(c.energy.some(e => e.field === "gc"), "gas consumption from EIA");
  assert.ok(c.counts.refinery > 5);
  assert.equal((await api.country("japan")).k, "JPN");
});

test("search: Kharkiv", async () => {
  const r = await api.search("Kharkiv");
  const city = r.places.find(p => p.n === "Kharkiv");
  assert.ok(city && city.k === "UKR" && city.type === "city");
  assert.ok(r.features.some(f => /Kharkiv/.test(f.name)));
  assert.ok((await api.search("kharkiv")).places.length, "case-insensitive");
});

test("views, shading and map URLs", async () => {
  const v = api.view("internet");
  assert.equal(v.url, "https://byron123t.github.io/resourcemap/#view=internet");
  assert.ok(v.layers.includes("cables"));
  const s = await api.shadingRank("net", { top: 5 });
  assert.equal(s.rows.length, 5);
  assert.ok(s.rows[0].value >= s.rows[4].value);
  assert.equal(api.mapUrl({ view: "oil", shocks: "choke.hormuz.100" }).url, "https://byron123t.github.io/resourcemap/#view=oil&sim=choke.hormuz.100");
  assert.throws(() => api.mapUrl({ shocks: "nonsense" }), /bad shock/);
});

test("CLI exit codes and JSON output", () => {
  const run = (...a) => spawnSync(process.execPath, [CLI, ...a], { encoding: "utf8" });
  const ok = run("query", "power", "--near", TOKYO.join(","), "--km", "30", "--limit", "3", "--json");
  assert.equal(ok.status, 0, ok.stderr);
  const j = JSON.parse(ok.stdout);
  assert.equal(j.layer, "power");
  assert.ok(j.rows.length <= 3 && j.rows.every(x => x.km <= 30));
  assert.equal(run("query", "no_such_layer").status, 2);
  assert.equal(run("query").status, 1);
  assert.equal(run("frobnicate").status, 1);
  assert.equal(run("query", "power", "--where", "q>>").status, 1);
  const h = run("help", "query");
  assert.equal(h.status, 0);
  assert.match(h.stdout, /--near/);
});

test("MCP server: initialize, tools/list, tools/call", async () => {
  const child = spawn(process.execPath, [MCP], { stdio: ["pipe", "pipe", "pipe"] });
  const replies = new Map(), waiters = new Map();
  let buf = "";
  child.stdout.on("data", d => {
    buf += d;
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i); buf = buf.slice(i + 1);
      const msg = JSON.parse(line);   // anything on stdout must be a protocol message
      replies.set(msg.id, msg);
      waiters.get(msg.id)?.(msg);
    }
  });
  const call = (id, method, params) => new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error(`timeout waiting for ${method}`)), 30000);
    waiters.set(id, m => { clearTimeout(t); res(m); });
    child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, ...(params ? { params } : {}) }) + "\n");
  });
  try {
    const init = await call(1, "initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "test", version: "0" } });
    assert.equal(init.result.protocolVersion, "2025-06-18");
    assert.ok(init.result.capabilities.tools);
    child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");
    const list = await call(2, "tools/list");
    const names = list.result.tools.map(t => t.name);
    for (const n of ["atlas_layers", "atlas_query", "atlas_search", "atlas_country", "atlas_near", "atlas_run_scenario", "atlas_map_url"]) assert.ok(names.includes(n), n);
    assert.ok(list.result.tools.every(t => t.inputSchema?.type === "object" && t.description.length > 40));
    const s = await call(3, "tools/call", { name: "atlas_search", arguments: { text: "Kharkiv", limit: 3 } });
    assert.ok(!s.result.isError);
    const body = JSON.parse(s.result.content[0].text);
    assert.ok(body.places.some(p => p.n === "Kharkiv"));
    assert.deepEqual(s.result.structuredContent, body);
    const bad = await call(4, "tools/call", { name: "atlas_get", arguments: { layer: "power", id: "zzzz-no-such-plant-zzzz" } });
    assert.equal(bad.result.isError, true);
    const unknown = await call(5, "tools/call", { name: "no_such_tool", arguments: {} });
    assert.equal(unknown.error.code, -32602);
    const nf = await call(6, "no/such/method");
    assert.equal(nf.error.code, -32601);
    assert.deepEqual((await call(7, "ping")).result, {});
  } finally { child.stdin.end(); child.kill(); }
});

test("scenario: Hormuz closed", { skip: !HAS_ENGINE && "cli/lib/scenario.mjs not present" }, async () => {
  const p = await api.scenarios({ group: "routes" });
  assert.ok(p.presets.some(x => /Hormuz/.test(x.title)));
  const r = await api.runScenario("choke.hormuz.100");
  assert.ok(r.steps.length >= 2 && r.steps[0].title);
  assert.match(r.url, /#sim=choke\.hormuz\.100/);
  const byTitle = await api.runScenario("Red Sea closed");
  assert.equal(byTitle.shocksString, "choke.bab.100;choke.suez.100");
  const k = await api.kinds();
  assert.ok(k.kinds.zone && k.chokepoints.hormuz);
});

test("scenario CLI reports a missing engine or bad shocks clearly", () => {
  const r = spawnSync(process.execPath, [CLI, "scenario", "notakind.x.5"], { encoding: "utf8" });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, HAS_ENGINE ? /unknown kind/ : /scenario engine not available/);
});

test("analysis: trade, correlate, combine and place context", async () => {
  const t = await api.trade("wheat", { from: "RUS", top: 5 });
  assert.equal(t.exporter.k, "RUS");
  assert.ok(t.flows.length && t.flows.every(f => f.from === "RUS" && f.kt > 0 && f.shareOfWorldTrade > 0));
  await assert.rejects(api.trade("caviar"), /trade needs an item/);
  const c = await api.correlate("chipM", "net");
  assert.ok(c.countries > 50 && c.spearman > 0 && c.outliers.length);
  const s = await api.combine("fdep,-beds,-docs", { top: 5 });
  assert.equal(s.rows.length, 5);
  assert.ok(s.rows[0].score >= s.rows[4].score);
  const r = await api.shadingRank("fdep", { top: 5 });
  assert.ok(r.rows.every(x => x.pop >= 1e6), "population floor");
  const n = await api.near(120.61, 24.21, { km: 15 });
  assert.equal(n.context.country.k, "TWN");
  assert.ok(n.context.basin && n.context.basin.waterStress);
});
