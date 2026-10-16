#!/usr/bin/env node
// atlas: query the World Extraction Atlas data from a terminal. Standard library only.
// Run `node cli/atlas.mjs help` for commands. Exit codes: 0 ok, 1 usage error, 2 not found.
import * as api from "./lib/api.mjs";
import { AtlasError, configure } from "./lib/api.mjs";
import { parseLonLat, parseBbox } from "./lib/data.mjs";

// ---------- arguments ----------
const VALUE_FLAGS = new Set(["data", "site", "near", "km", "bbox", "country", "where", "sort", "limit", "offset", "fields", "format", "layers", "top", "group", "window", "level", "view", "at", "min-pop", "from", "to"]);
const BOOL_FLAGS = new Set(["json", "no-color", "url", "counts", "asc", "help", "geometry", "wide"]);
export function parseArgs(argv) {
  const pos = [], flags = { where: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--") { pos.push(...argv.slice(i + 1)); break; }
    if (a === "-h") { flags.help = true; continue; }
    if (!a.startsWith("--") || a === "--") { pos.push(a); continue; }
    let [name, val] = a.slice(2).split(/=(.*)/s);
    if (name === "remote") { if (val == null && /^https?:/.test(argv[i + 1] || "")) val = argv[++i]; flags.remote = val || true; continue; }
    if (BOOL_FLAGS.has(name)) { flags[name] = val == null ? true : !/^(0|false|no)$/i.test(val); continue; }
    if (!VALUE_FLAGS.has(name)) throw new AtlasError(`unknown option --${name}`);
    if (val == null) { if (i + 1 >= argv.length) throw new AtlasError(`--${name} needs a value`); val = argv[++i]; }
    if (name === "where") flags.where.push(val); else flags[name] = val;
  }
  return { pos, flags };
}

// ---------- output helpers ----------
let COLOR = false;
const sgr = (c, s) => COLOR ? `\x1b[${c}m${s}\x1b[0m` : String(s);
const bold = s => sgr("1", s), dim = s => sgr("2", s), cyan = s => sgr("36", s), yellow = s => sgr("33", s), red = s => sgr("31", s);
const out = (s = "") => process.stdout.write(s + "\n");
const fmtNum = v => {
  if (v == null || v === "") return "";
  if (typeof v !== "number") return String(v);
  const a = Math.abs(v), d = a >= 100 ? 0 : a >= 10 ? 1 : a >= 1 ? 2 : a === 0 ? 0 : 3;
  return v.toLocaleString("en-US", { maximumFractionDigits: d });
};
const cell = v => v == null ? "" : Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : typeof v === "number" ? fmtNum(v) : String(v);
const visLen = s => s.replace(/\x1b\[[0-9;]*m/g, "").length;
function table(rows, cols, { max = 44 } = {}) {
  // cols: [{key|get, head, num}]
  const vals = rows.map(r => cols.map(c => { const v = c.get ? c.get(r) : r[c.key]; let s = (c.raw ? String(v ?? "") : cell(v)).replace(/\s+/g, " "); if (s.length > (c.max || max)) s = s.slice(0, (c.max || max) - 1) + "…"; return s; }));
  const keep = cols.map((c, j) => c.always || vals.some(v => v[j] !== ""));
  const C = cols.filter((_, j) => keep[j]), V = vals.map(v => v.filter((_, j) => keep[j]));
  const w = C.map((c, j) => Math.max(c.head.length, ...V.map(v => v[j].length)));
  const line = (v, f = x => x) => v.map((s, j) => C[j].num ? s.padStart(w[j]) : s.padEnd(w[j])).map(f).join("  ").trimEnd();
  out(bold(line(C.map(c => c.head))));
  for (const v of V) out(line(v));
}
const kv = (pairs, indent = "  ") => {
  const w = Math.max(...pairs.map(([k]) => k.length));
  for (const [k, v] of pairs) out(indent + dim(k.padEnd(w)) + "  " + v);
};
const csvCell = v => { const s = v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
const title = s => out(bold(s));
const json = v => out(JSON.stringify(v, null, 2));

// ---------- help ----------
const HELP = {
  layers: ["atlas layers [--counts] [--json]", "List every data layer and table: key, label, feature count and file.\nCounts need each file loaded (about 1 s locally); in --remote mode they are skipped unless --counts."],
  fields: ["atlas fields <layer> [--json]", "Property keys of a layer with their meaning (from the README's Data files table), how many features have each, an example and the numeric range.\nWorks on tables too: country_totals, domains, country_energy, labels, scenarios_weekly, live_manifest, story."],
  query: ["atlas query <layer> [--near lon,lat] [--km 50] [--bbox w,s,e,n] [--country ISO3|name] [--where expr]... [--sort field|-field]\n            [--limit 20] [--offset 0] [--fields a,b] [--format table|json|geojson|csv]",
    "Filter one layer. --where takes field OP value with OP = != > >= < <= ~ (substring), repeatable, e.g. --where q>1 --where f=gas.\n= and ~ ignore case and accents. --near defaults to a 50 km radius (--km 0 = no limit, nearest first).\nDefault sort: distance with --near, else the layer's size field (q) descending.\nExamples:\n  atlas query power --near 139.69,35.69 --km 50 --where f=gas --sort -q\n  atlas query fabs --country TWN --fields o,nm,cap300 --limit 10\n  atlas query crit --where m~lithium --format csv"],
  get: ["atlas get <layer> <id|name|#index>", "One feature with every property and what it means. Matches an id (id, wpi, Wikidata QID, ICAO, IATA, MMSI, hull…),\nthen an exact name, then the largest feature whose name contains the text."],
  search: ["atlas search <text> [--layers a,b] [--limit 10]", "Find countries, cities, seas and regions (labels.json) and named features across layers. Case and accents are ignored."],
  country: ["atlas country <ISO3|name> [--top 5]", "Country profile: totals of operating sites with world rank, internet / health / chips / food / mineral-rent figures, EIA energy balance,\nthe largest sites per main layer, and site counts per layer."],
  near: ["atlas near <lon,lat> [--km 50] [--limit 5] [--layers a,b]", "Everything within a radius across point layers (static and live), grouped by layer and nearest first, plus the nearest cities."],
  views: ["atlas views [--json]", "The page's preset views: group, label, layers switched on, background shading, and note."],
  view: ["atlas view <key> [--url]", "One view in full and its page link (https://byron123t.github.io/resourcemap/#view=<key>; base URL from --site or ATLAS_SITE).\n--url prints only the link."],
  trade: ["atlas trade <item> [--from country] [--to country] [--top 15]", "Food trade flows (FAOSTAT detailed trade matrix, importer-reported, kt): wheat, maize, rice, soy, banana, chicken.\nWith --from: who buys from that exporter and how much of each buyer's supply it is. With --to: who sells to that importer.\nExample: atlas trade wheat --from RUS"],
  combine: ["atlas combine <field,-field,...> [--min-pop 1000000] [--top 15]", "Rank countries on several fields at once: each field's percentile (a leading - flips it, so low is worse), averaged.\nExample, who is exposed to a food and health shock: atlas combine fdep,-beds,-docs"],
  correlate: ["atlas correlate <fieldA> <fieldB> [--min-pop 1000000] [--top 8]", "Spearman rank correlation between two country fields (any shading field: net, cens, beds, docs, chipX, chipM, fdep, minr, og, pw_gw, bws, kpop, kdens...),\nwith the countries that break the pattern most. Example: atlas correlate net cens"],
  shading: ["atlas shading [field] [--top 15] [--asc] [--min-pop 1000000]", "Without a field: the background-shading options (country fields and their units, basin fields).\nWith a field (e.g. net, cens, og, pw_gw, chipX, fdep, minr): rank countries by it."],
  sources: ["atlas sources [--json]", "Every data source with its licence, as shown in the page's Sources panel."],
  live: ["atlas live [feed] [--limit 10] [--sort f] [--where expr] [--near lon,lat --km N] [--window day|week|month]",
    "Without a feed: the live manifest (feeds, update time, counts). With one (fires, events, ships, aircraft, jets, airliners, navy, story): its top items.\nLive files exist only after pipeline/live/fetch_live.py has run (or with --remote)."],
  scenarios: ["atlas scenarios [--group g]", "Ready-made scenarios (needs cli/lib/scenario.mjs): group, title, what it tests and its shocks."],
  news: ["atlas news [--window week|month|year]", "Scenarios built from this week's / month's / year's news, with their three levels (de-escalate, current, escalate)."],
  scenario: ["atlas scenario <preset title | news label | shocks> [--level de|cur|esc] [--limit N] [--json]",
    "Run a scenario (cli/lib/scenario.mjs runs the page's own model; the first run takes 1–2 s) and print its chain of consequences,\nworld and country tables, details and the page link. --level picks a news item's level; --limit = countries listed.\n" +
    "Shocks are kind.target.percent joined by ';' (the page's #sim= syntax):\n" +
    "  choke.hormuz.100                  close a chokepoint (keys: atlas kinds)\n" +
    "  refinery.r3143912.100             a refinery by id (atlas query refinery)\n" +
    "  zone.lon_lat_km.percent           a conflict/disaster zone, e.g. zone.36.23_49.99_50.70\n" +
    "  outbreak.<flu|cov|sev>_lon_lat.percent   percent = share of natural spread left\n" +
    "  crop.<item>_<ISO3>.percent        harvest lost: wheat, maize, rice, soy, banana, chicken\n" +
    "  fab.<fab id | cc-ISO3>.percent    one chip fab or every fab in a country\n" +
    "  also field, port, lng, pipeline, power, mine, cable, water, chip, cloud (atlas kinds)\n" +
    "Examples:\n  atlas scenario choke.hormuz.100\n  atlas scenario 'zone.36.23_49.99_50.70'\n  atlas scenario \"Red Sea closed\"\n  atlas scenario \"Attacks near Riyadh\" --level esc\n  atlas scenario 'crop.wheat_UKR.50;crop.wheat_RUS.30'"],
  kinds: ["atlas kinds", "Shock kinds for scenarios, the chokepoint keys, outbreak pathogens and crop items."],
  url: ["atlas url [--view key] [--at lon,lat[,zoom]] [shocks]", "Build a page link for a view, a map position and/or a scenario (#view=…&at=lon,lat,zoom&sim=…)."]
};
const GLOBAL = `Global options:
  --json          machine-readable JSON output (every command)
  --data DIR      data directory (default: $ATLAS_DATA or <repo>/site/data)
  --remote [URL]  read data over HTTP instead (default: $ATLAS_REMOTE or https://byron123t.github.io/resourcemap/data/)
  --site URL      page base URL for links (default: $ATLAS_SITE or https://byron123t.github.io/resourcemap/)
  --no-color      no ANSI colour (colour is only used when stdout is a terminal)
Exit codes: 0 ok, 1 usage error, 2 not found.`;
function help(cmd) {
  if (cmd && HELP[cmd]) { out(bold("usage: ") + HELP[cmd][0]); out(); out(HELP[cmd][1]); out(); out(GLOBAL); return; }
  out(bold("atlas") + " — query the World Extraction Atlas data (site/data) from a terminal\n");
  out("Commands:");
  const w = Math.max(...Object.keys(HELP).map(k => k.length));
  for (const [k, [, d]] of Object.entries(HELP)) out("  " + cyan(k.padEnd(w)) + "  " + d.split("\n")[0]);
  out("\nRun `atlas help <command>` for details.\n");
  out("Examples:\n  atlas layers\n  atlas query power --near 139.69,35.69 --km 50 --where q>1\n  atlas country Japan\n  atlas search Kharkiv\n  atlas shading cens --top 10\n  atlas view internet --url\n  atlas scenario choke.hormuz.100\n");
  out(GLOBAL);
}

// ---------- commands ----------
const need = (v, cmd, what) => { if (v == null || v === "") throw new AtlasError(`missing ${what}. usage: ${HELP[cmd][0]}`); return v; };

async function cmdLayers(a, f) {
  const r = await api.layers({ counts: f.counts || undefined });
  if (f.json) return json(r);
  table(r.layers, [{ key: "key", head: "key" }, { key: "label", head: "label", max: 40 }, { key: "type", head: "type" }, { key: "kind", head: "kind" },
    { get: x => x.missing ? "missing" : x.count, head: "count", num: true }, { key: "unit", head: "unit" }, { key: "file", head: "file" }]);
  out(dim(`\ndata: ${r.source}`));
}
async function cmdFields(a, f) {
  const r = await api.fields(need(a[0], "fields", "layer"));
  if (f.json) return json(r);
  title(`${r.label} (${r.layer}) — ${fmtNum(r.count)} records, ${r.file}`);
  table(r.fields, [{ key: "key", head: "key" }, { key: "meaning", head: "meaning", max: 60 }, { key: "present", head: "present", num: true },
    { get: x => x.min != null ? `${fmtNum(x.min)} – ${fmtNum(x.max)}` : "", head: "range" }, { get: x => cell(x.example), head: "example", max: 36 }]);
}
function queryOpts(f) {
  return { near: f.near ? parseLonLat(f.near) : undefined, km: f.km, bbox: f.bbox ? parseBbox(f.bbox) : undefined, country: f.country,
    where: f.where, sort: f.sort, limit: f.limit, offset: f.offset, fields: f.fields };
}
function printRecords(r, f) {
  const fmt = f.json ? "json" : f.format || "table", wanted = r.fields;
  if (fmt === "json") return json({ ...r, rows: r.rows.map(x => api.slim(x, wanted, { geometry: f.geometry })) });
  if (fmt === "geojson") return out(JSON.stringify({ type: "FeatureCollection", features: r.rows.map(x => ({ type: "Feature", id: x.id, geometry: x.geometry,
    properties: { ...(wanted ? Object.fromEntries(wanted.map(k => [k, x.props[k]])) : x.props), ...(x.km != null ? { _km: x.km } : {}) } })) }));
  if (fmt === "csv") {
    const keys = wanted || [...new Set(r.rows.flatMap(x => Object.keys(x.props)))];
    const head = ["id", "name", ...keys.filter(k => k !== "n"), ...(r.near ? ["km"] : []), "lon", "lat"];
    out(head.join(","));
    for (const x of r.rows) out(head.map(k => csvCell(k === "id" ? x.id : k === "name" ? x.name : k === "km" ? x.km : k === "lon" ? x.lon : k === "lat" ? x.lat : x.props[k])).join(","));
    return;
  }
  if (fmt !== "table") throw new AtlasError(`--format must be table, json, geojson or csv`);
  const unit = r.unit ? `q (${r.unit})` : "q";
  const extra = wanted || ["c", "q", ...r.keyFields, "s"].filter((k, i, A) => A.indexOf(k) === i);
  const cols = [{ key: "name", head: "name", max: 42, always: true }, ...extra.map(k => ({ get: x => k === "c" ? x.props.c ?? x.props.k ?? x.props.cc : x.props[k], head: k === "q" ? unit : k, num: typeof r.rows.find(x => x.props[k] != null)?.props[k] === "number", max: 30 })),
    ...(r.near ? [{ key: "km", head: "km", num: true }] : []), { key: "lon", head: "lon", num: true, raw: true }, { key: "lat", head: "lat", num: true, raw: true }, { key: "id", head: "id", max: 28 }];
  table(r.rows, cols);
  const bits = [`${fmtNum(r.matched)} matched`, `${r.shown} shown`];
  if (r.sort) bits.push(`sorted by ${r.sort}`);
  if (r.country) bits.push(`in ${r.country.n}`);
  if (r.near) bits.push(`within ${r.near.km ? r.near.km + " km" : "any distance"} of ${r.near.lon},${r.near.lat}`);
  out(dim("\n" + r.label + ": " + bits.join(" · ")));
}
async function cmdQuery(a, f) { printRecords(await api.query(need(a[0], "query", "layer"), queryOpts(f)), f); }
async function cmdGet(a, f) {
  const r = await api.get(need(a[0], "get", "layer"), need(a.slice(1).join(" "), "get", "id or name"));
  if (f.json) return json(r);
  title(`${r.name ?? r.id}`);
  out(dim(`${r.kind} · ${r.layer} · id ${r.id} · ${r.lat != null ? `${r.lon}, ${r.lat}` : ""} (${r.geometry})`));
  out();
  const w = Math.max(...r.properties.map(p => p.key.length)), vs = r.properties.map(p => cell(p.value));
  const vw = Math.min(60, Math.max(...vs.map(v => v.length)));
  r.properties.forEach((p, i) => out(`  ${cyan(p.key.padEnd(w))}  ${vs[i].length > vw ? vs[i] : vs[i].padEnd(vw)}${p.label ? dim("  " + p.label) : ""}`));
  if (r.links.osm) out(dim(`\n  ${r.links.osm}`));
}
async function cmdSearch(a, f) {
  const r = await api.search(need(a.join(" "), "search", "text"), { layers: f.layers, limit: f.limit });
  if (f.json) return json(r);
  if (r.countries.length) { title("Countries"); table(r.countries, [{ key: "k", head: "key" }, { key: "n", head: "name" }, { key: "lon", head: "lon", num: true, raw: true }, { key: "lat", head: "lat", num: true, raw: true }]); out(); }
  if (r.places.length) { title("Places"); table(r.places, [{ key: "n", head: "name" }, { key: "type", head: "type" }, { key: "admin", head: "admin-1" }, { key: "k", head: "country" }, { key: "pop", head: "population", num: true }, { key: "lon", head: "lon", num: true, raw: true }, { key: "lat", head: "lat", num: true, raw: true }]); out(); }
  if (r.features.length) {
    title(`Features (${fmtNum(r.featuresMatched)} matched)`);
    table(r.features, [{ key: "layer", head: "layer" }, { key: "name", head: "name", max: 48 }, { get: x => x.c ?? x.k ?? x.cc, head: "country" }, { key: "q", head: "q", num: true }, { key: "lon", head: "lon", num: true, raw: true }, { key: "lat", head: "lat", num: true, raw: true }, { key: "id", head: "id", max: 30 }]);
  }
  if (!r.countries.length && !r.places.length && !r.features.length) { out(`nothing matches "${r.query}"`); process.exitCode = 2; }
}
const rankTxt = x => x.rank ? dim(`  #${x.rank} of ${x.of}`) : "";
const val = (x) => x.unit === "share" || /^share of/.test(x.unit || "") ? `${fmtNum(x.value * 100)}%` + (x.unit === "share" ? "" : " " + x.unit.replace(/^share of /, "of ")) : `${fmtNum(x.value)} ${x.unit || ""}`.trim();
async function cmdCountry(a, f) {
  const r = await api.country(need(a.join(" "), "country", "country"), { top: f.top });
  if (f.json) return json(r);
  title(`${r.n} (${r.k})`);
  if (r.totals.length) { out("\n" + bold("Resources & industry") + dim(" (operating sites; rank among countries with any)")); kv(r.totals.map(x => [x.label, val(x) + rankTxt(x)])); }
  if (r.minerals) out(dim("  critical minerals: ") + Object.entries(r.minerals).map(([m, n]) => `${m} ${n}`).join(", "));
  if (r.domains.length) { out("\n" + bold("Internet, health, chips, food")); kv(r.domains.map(x => [x.label, val(x) + (x.year ? dim(` (${x.year})`) : "") + rankTxt(x)])); }
  if (r.energy.length) { out("\n" + bold("Energy balance") + dim(" (US EIA)")); kv(r.energy.map(x => [x.label, val(x) + (x.year ? dim(` (${x.year})`) : "")])); }
  for (const [k, list] of Object.entries(r.sites)) {
    if (!list.length) continue;
    out("\n" + bold(`Largest: ${k}`) + dim(` (${fmtNum(r.counts[k])} in country)`));
    for (const s of list) out(`  ${fmtNum(s.value).padStart(8)} ${dim((s.unit || "").padEnd(6))}  ${s.name}${s.fuel ? dim(" · " + s.fuel) : ""}${s.operator ? dim(" · " + s.operator) : ""}${s.status && s.status !== "operating" ? yellow(" · " + s.status) : ""}`);
  }
  const other = Object.entries(r.counts).filter(([k]) => !r.sites[k]?.length);
  if (other.length) out("\n" + bold("Other counts") + "\n  " + other.map(([k, n]) => `${k} ${fmtNum(n)}`).join(" · "));
}
async function cmdNear(a, f) {
  const [lon, lat] = parseLonLat(need(a.join(" "), "near", "lon,lat"));
  const r = await api.near(lon, lat, { km: f.km ?? 50, limit: f.limit, layers: f.layers });
  if (f.json) return json(r);
  title(`Within ${r.km} km of ${lon}, ${lat}`);
  const cx = r.context || {}, B = cx.basin || {};
  const ctx = [cx.country && `in ${cx.country.n}`, cx.people != null && `≈${fmtNum(cx.people)} people within ${r.km} km`,
    B.waterStress && `water stress ${B.waterStress}`, B.drought && `drought risk ${B.drought}`, B.groundwaterDecline && `groundwater decline ${B.groundwaterDecline}`].filter(Boolean);
  if (ctx.length) out(dim("  " + ctx.join(" · ")));
  if (!r.groups.length) out(dim("  no sites in range"));
  for (const g of r.groups) {
    out("\n" + bold(g.label) + dim(` (${g.layer}) ${fmtNum(g.count)}`));
    for (const x of g.items) {
      const extra = Object.entries(x).filter(([k]) => !["id", "name", "km"].includes(k)).map(([k, v]) => `${k}=${cell(v)}`).join(" ");
      out(`  ${fmtNum(x.km).padStart(6)} km  ${x.name ?? x.id}${extra ? dim("  " + extra) : ""}`);
    }
  }
  out("\n" + bold("Nearest cities"));
  for (const c of r.cities) out(`  ${fmtNum(c.km).padStart(6)} km  ${c.n}${c.k ? dim(" " + c.k) : ""}${c.pop ? dim(" · pop " + fmtNum(c.pop)) : ""}`);
}
async function cmdViews(a, f) {
  const r = api.views({ site: f.site });
  if (f.json) return json(r);
  let g = null;
  for (const v of r.views) {
    if (v.group !== g) { out((g ? "\n" : "") + bold(v.group)); g = v.group; }
    out(`  ${cyan(v.key.padEnd(13))} ${v.label}${v.shading ? dim(" · shading: " + v.shading.label) : ""}`);
    out(dim(`  ${"".padEnd(13)} layers: ${v.layers.join(", ")}`));
  }
  out(dim(`\nOpen one: atlas view <key> --url`));
}
async function cmdView(a, f) {
  const v = api.view(need(a[0], "view", "view key"), { site: f.site });
  if (f.url && !f.json) return out(v.url);
  if (f.json) return json(v);
  title(`${v.label} (${v.key})`);
  out(dim(v.group) + "\n");
  out(v.note);
  if (v.connection) out(dim("\nConnection: ") + v.connection);
  out();
  kv([["layers", v.layers.join(", ")], ...(v.shading ? [["shading", `${v.shading.label} (${v.shading.value})`]] : []),
    ...Object.entries(v.filters).map(([k, x]) => [k, String(x)]), ...(v.big ? [["largest-site cut-offs", JSON.stringify(v.big)]] : []),
    ...(v.go ? [["go to", v.go.label + (v.go.lon != null ? ` (${v.go.lon}, ${v.go.lat}, zoom ${v.go.zoom})` : "")]] : []), ["url", cyan(v.url)]]);
}
async function cmdShading(a, f) {
  if (!a[0]) {
    const r = api.shading();
    if (f.json) return json(r);
    let g = null;
    for (const o of r.options) {
      if (o.group !== g) { out((g != null ? "\n" : "") + bold(o.group || "Other")); g = o.group; }
      out(`  ${cyan(o.field.padEnd(13))} ${o.label}${dim(" · " + (o.unit || ""))}${o.rankable ? "" : dim(" (per basin)")}`);
    }
    out(dim("\nRank countries: atlas shading <field> [--top 15]"));
    return;
  }
  const r = await api.shadingRank(a[0], { top: f.top ?? f.limit, asc: f.asc, minPop: f["min-pop"] ?? 1e6 });
  if (f.json) return json(r);
  title(`${r.label} — ${r.unit || ""}`);
  const share = /share/.test(r.unit || "");
  table(r.rows, [{ key: "rank", head: "#", num: true }, { key: "k", head: "key" }, { key: "n", head: "country" }, { get: x => share ? fmtNum(x.value * 100) + "%" : x.value, head: "value", num: true }, { get: x => x.year == null ? "" : String(x.year), head: "year" },
    { get: x => x.pop ? fmtNum(x.pop / 1e6, 1) + " M" : "", head: "people", num: true }]);
  out(dim(`\n${r.countries} countries with data${r.minPop ? ` and ${fmtNum(r.minPop / 1e6, 1)} M+ people (--min-pop 0 for all)` : ""} · ${r.order}`));
}
async function cmdTrade(a, f) {
  const r = await api.trade(a[0], { from: f.from, to: f.to, top: f.top ?? f.limit });
  if (f.json) return json(r);
  title(`${r.label} trade, ${r.year} (kt, importer-reported)`);
  if (r.exporter) out(`${r.exporter.n}: produces ${fmtNum(r.exporter.productionKt)} kt, exports ${fmtNum(r.exporter.exportsKt)} kt`);
  if (r.importer) out(`${r.importer.n}: produces ${fmtNum(r.importer.productionKt)} kt, imports ${fmtNum(r.importer.importsKt)} kt, supply ${fmtNum(r.importer.supplyKt)} kt`);
  table(r.flows, [{ key: "fromName", head: "from" }, { key: "toName", head: "to" }, { key: "kt", head: "kt", num: true },
    { get: x => fmtNum(x.shareOfWorldTrade * 100, 1) + "%", head: "of world trade", num: true }, { get: x => x.shareOfBuyerSupply == null ? "" : fmtNum(x.shareOfBuyerSupply * 100) + "%", head: "of buyer's supply", num: true }]);
  out(dim(`\n${r.matched} flows matched · world trade ${fmtNum(r.worldTradeKt)} kt, world output ${fmtNum(r.worldProductionKt)} kt`));
}
async function cmdCombine(a, f) {
  const r = await api.combine(a.join(","), { minPop: f["min-pop"] ?? 1e6, top: f.top ?? f.limit });
  if (f.json) return json(r);
  title(`Countries ranked on ${r.labels.join(" + ")}`);
  table(r.rows, [{ key: "rank", head: "#", num: true }, { key: "n", head: "country" }, { get: x => fmtNum(x.score * 100) , head: "score", num: true },
    ...r.spec.map(s => s.replace(/^-/, "")).map(fl => ({ get: x => x.fields[fl] == null ? "–" : x.fields[fl], head: fl, num: true }))]);
  out(dim(`\n${r.countries} countries with at least ${r.need} of the fields${r.minPop ? ` and ${fmtNum(r.minPop / 1e6, 1)} M+ people` : ""}; score = mean percentile, 100 = most exposed`));
}
async function cmdCorrelate(a, f) {
  const r = await api.correlate(a[0], a[1], { minPop: f["min-pop"] ?? 1e6, top: f.top ?? f.limit });
  if (f.json) return json(r);
  title(`${r.labels[0]} vs ${r.labels[1]}`);
  out(`Spearman ρ = ${r.spearman}: ${r.reading}${r.minPop ? ` (countries of ${fmtNum(r.minPop / 1e6, 1)} M+ people)` : ""}.`);
  out(bold("\nBreaking the pattern most:"));
  table(r.outliers, [{ key: "n", head: "country" }, { key: r.fields[0], head: r.fields[0], num: true }, { key: r.fields[1], head: r.fields[1], num: true },
    { get: x => `${x.rankA} / ${x.rankB}`, head: "ranks (low→high)" }]);
}
async function cmdSources(a, f) {
  const r = api.sources();
  if (f.json) return json(r);
  table(r.sources, [{ key: "name", head: "source", max: 46 }, { key: "licence", head: "licence", max: 40 }, { key: "what", head: "what", max: 50 }, { key: "when", head: "when", max: 22 }]);
}
async function cmdLive(a, f) {
  const r = await api.live(a[0], { ...queryOpts(f), window: f.window });
  if (f.json) return json(r);
  if (!a[0]) {
    title(`Live snapshot generated ${r.generated} (${r.age} ago)`);
    table(r.feeds, [{ key: "feed", head: "feed" }, { key: "count", head: "count", num: true }, { key: "age", head: "age" }, { key: "source", head: "source", max: 70 }, { get: x => x.error || x.status, head: "status" }]);
    return;
  }
  if (a[0] === "story") {
    title(`Storyboard, ${r.window} window (updated ${r.updated})`);
    table(r.items, [{ key: "title", head: "headline", max: 50 }, { key: "place", head: "place", max: 30 }, { key: "bucket", head: "from", max: 16 }, { key: "reports", head: "reports", num: true }, { key: "surge", head: "surge", num: true }, { key: "lon", head: "lon", num: true, raw: true }, { key: "lat", head: "lat", num: true, raw: true }]);
    return;
  }
  if (r.manifest) out(dim(`${r.manifest.source} · updated ${r.manifest.updated} (${r.manifest.age} ago)\n`));
  printRecords(r, f);
}
async function cmdScenarios(a, f) {
  const r = await api.scenarios({ group: f.group || a[0] });
  if (f.json) return json(r);
  let g = null;
  for (const p of r.presets) {
    if (p.group !== g) { out((g ? "\n" : "") + bold(p.groupLabel || p.group)); g = p.group; }
    out(`  ${cyan(p.title)}  ${dim(p.shocksString)}`);
    if (p.desc) out(`    ${p.desc}`);
  }
}
async function cmdNews(a, f) {
  const r = await api.news({ window: f.window || a[0] || "week", limit: f.limit });
  if (f.json) return json(r);
  title(`${r.label}: ${r.count} news scenarios`);
  r.items.forEach((x, i) => {
    out(`\n${bold(`${i + 1}. ${x.label}`)}${dim(` [${x.cat || ""}${x.trend ? ", " + x.trend : ""}${x.likely ? ", likely " + x.likely : ""}]`)}`);
    if (x.why) out("   " + x.why);
    for (const l of ["de", "cur", "esc"]) if (x.lv?.[l]) out(dim(`   ${l.padEnd(3)} `) + x.lv[l]);
    if (x.url) out(dim("   " + x.url));
  });
}
async function cmdKinds(a, f) {
  const r = await api.kinds();
  if (f.json) return json(r);
  title("Shock kinds");
  kv(Object.entries(r.kinds).map(([k, v]) => [k, v]));
  for (const [name, o] of [["Chokepoints (choke.<key>)", r.chokepoints], ["Pathogens (outbreak.<key>_lon_lat)", r.pathogens], ["Foods (crop.<key>_ISO3)", r.foods]]) {
    if (!o) continue;
    out("\n" + bold(name));
    kv(Object.entries(o).map(([k, v]) => [k, typeof v === "object" ? v.n || JSON.stringify(v) : v]));
  }
  out("\n" + r.syntax);
}
const pair = (x, d = 0) => Array.isArray(x) ? `${fmtNum(x[0])} → ${fmtNum(x[1])}` : cell(x);
async function cmdScenario(a, f) {
  const r = await api.runScenario(need(a.join(" "), "scenario", "preset title, news label or shocks"), { level: f.level || "cur", site: f.site, countries: f.limit ? Number(f.limit) : undefined });
  if (f.json) return json(r);
  title(r.title || "Scenario");
  out(dim(`shocks: ${r.shocksString}${r.level ? ` · level ${r.level}` : ""}`));
  for (const s of r.shocks || []) if (s && s.name) out(dim(`  ${s.kind} ${s.name} at ${Math.round(s.share * 100)}%`));
  if (r.headline?.length) out("\n" + r.headline.map(h => yellow(h)).join(" · "));
  (r.steps || []).forEach((s, i) => {
    out(`\n${bold(`${i + 1}. ${s.title}`)}${s.big ? "  " + yellow(s.big) : ""}`);
    if (s.text) out("   " + s.text);
  });
  if (r.world) { out("\n" + bold("World") + dim(" (before → after)")); kv(Object.values(r.world).map(w => [w.label, `${pair([w.before, w.after])} ${w.unit || ""}`])); }
  if (r.chokepoints?.length) { out("\n" + bold("Chokepoints") + dim(" (oil kb/d, gas bcm/yr; before → after)")); kv(r.chokepoints.map(c => [c.name || c.k, `oil ${pair(c.oil)} · gas ${pair(c.gas)}`])); }
  if (r.countries?.length) {
    out("\n" + bold("Countries hit hardest") + dim(" (before → after)"));
    table(r.countries, [{ key: "k", head: "key" }, { key: "name", head: "country" }, { get: x => pair(x.diesel), head: "diesel kb/d", num: true }, { get: x => pair(x.runs), head: "refinery runs", num: true },
      { get: x => pair(x.gasShort), head: "gas short bcm/yr", num: true }, { get: x => pair(x.gasPowerGW), head: "gas power at risk GW", num: true }]);
  }
  if (r.facts?.length) { out("\n" + bold("Details")); for (const x of r.facts) out("  • " + x); }
  if (r.notes?.length) { out(""); for (const x of r.notes) out(dim("  " + x)); }
  out("\n" + cyan(r.url));
}
async function cmdUrl(a, f) {
  const [lon, lat, zoom] = f.at ? String(f.at).split(",").map(Number) : [];
  if (f.at && !(isFinite(lon) && isFinite(lat))) throw new AtlasError("--at takes lon,lat[,zoom]", 1);
  const r = api.mapUrl({ view: f.view || undefined, shocks: a.join(";") || undefined, lon, lat, zoom: isFinite(zoom) ? zoom : undefined, site: f.site });
  if (f.json) return json(r);
  out(r.url);
}

const COMMANDS = { layers: cmdLayers, fields: cmdFields, query: cmdQuery, get: cmdGet, search: cmdSearch, country: cmdCountry, near: cmdNear,
  views: cmdViews, view: cmdView, shading: cmdShading, sources: cmdSources, live: cmdLive, scenarios: cmdScenarios, news: cmdNews, scenario: cmdScenario, kinds: cmdKinds, url: cmdUrl, trade: cmdTrade, correlate: cmdCorrelate, combine: cmdCombine };

export async function main(argv) {
  let f = {};
  try {
    const parsed = parseArgs(argv);
    const pos = parsed.pos; f = parsed.flags;
    COLOR = !!process.stdout.isTTY && !f["no-color"] && !f.json && !process.env.NO_COLOR;
    configure({ data: f.data, remote: f.remote });
    const [cmd, ...rest] = pos;
    if (!cmd || cmd === "help" || (f.help && !COMMANDS[cmd])) { help(rest[0]); return 0; }
    if (f.help) { help(cmd); return 0; }
    if (!COMMANDS[cmd]) throw new AtlasError(`unknown command "${cmd}". Run: atlas help`);
    await COMMANDS[cmd](rest, f);
    return process.exitCode || 0;
  } catch (e) {
    const code = e instanceof AtlasError ? e.code : 1;
    if (f.json) process.stderr.write(JSON.stringify({ error: e.message, code }) + "\n");
    else process.stderr.write((process.stderr.isTTY ? red("atlas: ") : "atlas: ") + e.message + "\n");
    if (!(e instanceof AtlasError) && process.env.ATLAS_DEBUG) process.stderr.write(e.stack + "\n");
    return code;
  }
}

process.stdout.on("error", e => { if (e.code === "EPIPE") process.exit(0); throw e; });
const code = await main(process.argv.slice(2));
process.exitCode = code;
