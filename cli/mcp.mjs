#!/usr/bin/env node
// MCP server for the World Extraction Atlas, over stdio (JSON-RPC 2.0, one message per line).
// Standard library only. stdout carries protocol messages and nothing else; logs go to stderr.
//   claude mcp add atlas -- node /path/to/repo/cli/mcp.mjs [--data DIR | --remote [URL]] [--site URL]
import { createInterface } from "node:readline";
import * as api from "./lib/api.mjs";
import { AtlasError, configure } from "./lib/api.mjs";
import { parseLonLat, parseBbox } from "./lib/data.mjs";

const PROTOCOL = "2025-06-18", SUPPORTED = [PROTOCOL, "2025-03-26", "2024-11-05"];
const MAX_TEXT = 60000;   // characters of JSON per tool result, so an agent's context isn't flooded
const log = (...a) => process.stderr.write("[atlas-mcp] " + a.join(" ") + "\n");

// ---------- options ----------
let site;
{
  const argv = process.argv.slice(2), o = {};
  for (let i = 0; i < argv.length; i++) {
    const [k, v] = argv[i].replace(/^--/, "").split(/=(.*)/s);
    if (k === "remote") o.remote = v || (/^https?:/.test(argv[i + 1] || "") ? argv[++i] : true);
    else if (k === "data" || k === "site") o[k] = v ?? argv[++i];
  }
  configure({ data: o.data, remote: o.remote });
  site = o.site;
}

// ---------- tools ----------
const num = (d, extra = {}) => ({ type: "number", description: d, ...extra });
const str = (d, extra = {}) => ({ type: "string", description: d, ...extra });
const obj = (properties, required = []) => ({ type: "object", properties, required, additionalProperties: false });
const cap = (v, d, max) => Math.max(0, Math.min(max, v == null ? d : Math.floor(Number(v)) || 0));
const LAYER_HINT = "Layer key from atlas_layers, e.g. power, oilgas, coal, iron, crit, refinery, lng, steel, cement, chem, pipelines, ports, chokepoints, bases, airports, dc, cables, landings, cloud, fabs, chipsites, biolabs, lakes, water_risk; live: fires, events, ships, aircraft, jets, airliners, navy.";
const WHERE_HINT = "Property filters, each 'field OP value' with OP one of = != > >= < <= ~ (substring). = and ~ ignore case and accents. Examples: 'q>1', 'f=gas', 's=operating', 'cc=TWN', 'm~lithium'. Property keys come from atlas_fields.";

const TOOLS = [
  { name: "atlas_layers", title: "List atlas layers",
    description: "List every data layer and table in the World Extraction Atlas with key, label, type (static, live, base, table), geometry kind, feature count, unit of the size field q, and file. Call this first to learn layer keys for the other tools.",
    inputSchema: obj({ include_tables: { type: "boolean", description: "Also list non-map tables (country_totals, domains, country_energy, labels, scenarios_weekly, live_manifest, story). Default true." } }),
    run: async a => api.layers({ tables: a.include_tables !== false }) },
  { name: "atlas_fields", title: "Describe a layer's properties",
    description: "Property keys of one layer or table, with their meaning (from the README), how many records have each, an example value and the numeric range. Use before atlas_query to know what to filter and sort on (keys are short, e.g. q = size, s = status, k = ISO3 country, f = fuel).",
    inputSchema: obj({ layer: str(LAYER_HINT + " Tables also work: country_totals, domains, country_energy, labels, scenarios_weekly, live_manifest, story.") }, ["layer"]),
    run: async a => api.fields(a.layer) },
  { name: "atlas_query", title: "Query a layer",
    description: "Filter one layer by distance, bounding box, country and property conditions, then sort and page. Returns matching features (id, name, lon, lat, distance in km when near is given, properties). Default sort: nearest first with near, otherwise largest q first. Default 20 results, at most 200; use offset to page.",
    inputSchema: obj({
      layer: str(LAYER_HINT),
      near: str("Centre point as 'lon,lat' (longitude first), e.g. '139.69,35.69' for Tokyo."),
      km: num("Radius in km around near. Default 50; 0 means no radius (just nearest first).", { minimum: 0 }),
      bbox: str("Bounding box 'west,south,east,north' in degrees."),
      country: str("Country as ISO3 code (JPN) or name (Japan)."),
      where: { type: "array", items: { type: "string" }, description: WHERE_HINT },
      sort: str("Property to sort by; prefix with - for descending, e.g. '-q' or 'km'."),
      limit: num("Max results (default 20, max 200).", { minimum: 1, maximum: 200 }),
      offset: num("Skip this many results (for paging).", { minimum: 0 }),
      fields: { type: "array", items: { type: "string" }, description: "Only return these properties (default: all)." }
    }, ["layer"]),
    run: async a => {
      const r = await api.query(a.layer, { near: a.near ? parseLonLat(a.near) : undefined, km: a.km, bbox: a.bbox ? parseBbox(a.bbox) : undefined, country: a.country,
        where: a.where || [], sort: a.sort, limit: cap(a.limit, 20, 200), offset: a.offset, fields: a.fields });
      return { layer: r.layer, label: r.label, unit: r.unit, matched: r.matched, shown: r.shown, sort: r.sort, country: r.country, near: r.near,
        rows: r.rows.map(x => api.slim(x, r.fields)), ...(r.matched > r.shown ? { note: `${r.matched - r.shown} more match; raise limit or use offset` } : {}) };
    } },
  { name: "atlas_get", title: "Get one feature",
    description: "One feature with all its properties and what each means. Identify it by id from atlas_query/atlas_search (e.g. r3143912, tsmc-fab-18, an ICAO code, a Wikidata QID, '#123' for the 123rd feature), or by name (exact, else the largest feature whose name contains the text).",
    inputSchema: obj({ layer: str(LAYER_HINT), id: str("Feature id, '#index', or name.") }, ["layer", "id"]),
    run: async a => api.get(a.layer, a.id) },
  { name: "atlas_search", title: "Search places and features",
    description: "Search by name: countries, cities/seas/regions (Natural Earth labels), and named features across all layers (power plants, mines, fields, refineries, ports, airports, bases, fabs, cables, conflict-report places …). Case and accents are ignored. Use to find coordinates and ids before atlas_near / atlas_get.",
    inputSchema: obj({ text: str("Name or part of a name, e.g. 'Kharkiv', 'Jamnagar', 'TSMC'."), layers: { type: "array", items: { type: "string" }, description: "Restrict feature search to these layer keys." },
      limit: num("Max results per section (default 10, max 50).", { minimum: 1, maximum: 50 }) }, ["text"]),
    run: async a => api.search(a.text, { layers: a.layers, limit: cap(a.limit, 10, 50) }) },
  { name: "atlas_country", title: "Country profile",
    description: "Profile of one country: totals of operating sites (oil & gas, coal, iron, power by fuel, steel, cement, LNG, ports, water stress, population) with world rank; internet use, internet censorship, broadband, hospital beds, doctors, chip exports/imports, cereal import share, mineral rents; EIA oil and gas balance (production, consumption, imports); its largest sites per main layer; and site counts per layer.",
    inputSchema: obj({ country: str("ISO3 code (JPN) or name (Japan)."), top: num("Largest sites to list per layer (default 5, max 20).", { minimum: 0, maximum: 20 }) }, ["country"]),
    run: async a => api.country(a.country, { top: cap(a.top, 5, 20) }) },
  { name: "atlas_near", title: "What is near a point",
    description: "Everything within a radius of a point across all point layers (static sites and live feeds such as fires, conflict reports, ships, aircraft), grouped by layer with counts and the nearest few of each, plus context: the country, the river basin's water stress, drought and groundwater risk, and the people living within the radius. Then the nearest cities.",
    inputSchema: obj({ lon: num("Longitude in degrees.", { minimum: -180, maximum: 180 }), lat: num("Latitude in degrees.", { minimum: -90, maximum: 90 }),
      km: num("Radius in km (default 50, max 500).", { minimum: 1, maximum: 500 }), limit: num("Nearest items per layer (default 5, max 25).", { minimum: 1, maximum: 25 }),
      layers: { type: "array", items: { type: "string" }, description: "Only these layer keys (lines and polygons are allowed when named)." } }, ["lon", "lat"]),
    run: async a => api.near(a.lon, a.lat, { km: cap(a.km, 50, 500) || 50, limit: cap(a.limit, 5, 25), layers: a.layers }) },
  { name: "atlas_views", title: "List map views",
    description: "The atlas page's preset views (each answers one question: oil & gas flows, internet & cloud, water stress, conflict & military …) with group, label, layers switched on, background shading, note, a cross-domain connection, a go-to place, the page topic it sits under, and the page URL to open it. Also returns `rules`: how stories and scenarios pick their view (first match wins).",
    inputSchema: obj({ key: str("Optional view key for just one view.") }),
    run: async a => a.key ? api.view(a.key, { site }) : api.views({ site }) },
  { name: "atlas_shading_rank", title: "Rank countries by a shading field",
    description: "Without field: list the background-shading options (country choropleth fields with units, and per-basin water fields). With field: rank countries by it, e.g. net (% online), cens (internet censorship 0-100), og (oil & gas M boe/yr), pw_gw (power GW), pw_nuclear, chipX/chipM (chip trade US$ bn), beds, docs, fdep (share of cereals imported, 0-1), minr (mineral rents % GDP), bws (water stress), kpop, kdens, lng_imp, steel, cement.",
    inputSchema: obj({ field: str("Field key, e.g. 'cens' or 'c:cens'. Omit to list options."), top: num("How many countries (default 15, max 100).", { minimum: 1, maximum: 100 }),
      ascending: { type: "boolean", description: "Lowest first instead of highest." }, min_pop: num("Leave out countries with fewer people (default 1000000; 0 = all).") }),
    run: async a => a.field ? api.shadingRank(a.field, { top: cap(a.top, 15, 100), asc: !!a.ascending, minPop: a.min_pop ?? 1e6 }) : api.shading() },
  { name: "atlas_trade", title: "Food trade flows",
    description: "Who sells a food to whom (FAOSTAT detailed trade matrix, importer-reported, kt, latest full year). Items: wheat, maize, rice, soy, banana, chicken. Filter by exporter (from) and/or importer (to) as ISO3 or name. Each flow gives its share of world trade and of the buyer's own supply, so it shows dependence (e.g. from=RUS lists who relies on Russian wheat).",
    inputSchema: obj({ item: str("wheat, maize, rice, soy, banana or chicken"), from: str("Exporter ISO3 or name (optional)"), to: str("Importer ISO3 or name (optional)"),
      top: num("How many flows (default 15, max 200).", { minimum: 1, maximum: 200 }) }, ["item"]),
    run: async a => api.trade(a.item, { from: a.from, to: a.to, top: cap(a.top, 15, 200) }) },
  { name: "atlas_combine", title: "Rank countries on several fields",
    description: "Rank countries on several country fields at once: each field's percentile among countries that have it (prefix '-' to flip, so low values count as worse), averaged into a 0-1 score. Good for exposure questions, e.g. 'fdep,-beds,-docs' (food import dependence with few beds and doctors) or 'chipM,-net,cens'.",
    inputSchema: obj({ fields: str("Comma-separated fields, '-' prefix to flip, e.g. 'fdep,-beds,-docs'"), min_pop: num("Leave out countries with fewer people (default 1000000; 0 = all)."),
      top: num("How many countries (default 15, max 100).", { minimum: 1, maximum: 100 }) }, ["fields"]),
    run: async a => api.combine(a.fields, { minPop: a.min_pop ?? 1e6, top: cap(a.top, 15, 100) }) },
  { name: "atlas_correlate", title: "Correlate two country fields",
    description: "Spearman rank correlation between two country fields across countries (any shading field: net % online, cens censorship, beds, docs, chipX/chipM, fdep cereal import share, minr mineral rents, og, pw_gw, bws water stress, kpop, kdens, steel...). Returns rho, a plain reading, and the countries that break the pattern most. Countries under min_pop people are left out (default 1 million).",
    inputSchema: obj({ a: str("First field"), b: str("Second field"), min_pop: num("Leave out countries with fewer people (default 1000000; 0 = all)."),
      top: num("How many outliers (default 8).", { minimum: 1, maximum: 50 }) }, ["a", "b"]),
    run: async a => api.correlate(a.a, a.b, { minPop: a.min_pop ?? 1e6, top: cap(a.top, 8, 50) }) },
  { name: "atlas_sources", title: "Data sources and licences",
    description: "Every data source behind the atlas with URL, what it provides, licence and date. Use when citing data or checking reuse terms (e.g. cables are non-commercial, mining polygons CC BY-SA).",
    inputSchema: obj({}),
    run: async () => api.sources() },
  { name: "atlas_live", title: "Live feeds",
    description: "Without feed: the live snapshot manifest (feeds, update time, counts, sources). With feed: the top items of fires (by fire power), events (GDELT conflict reports, by report count), ships, aircraft, jets, airliners, navy (US Navy positions, approximate), or story (the storyboard: conflict surges plus consequential news of other kinds — unrest, trade curbs, industrial accidents, energy, shipping, health, food & farms, cyber & internet, disasters; window day|week|month|year (year = monthly surges over twelve months); filter with kind and min_tier; each item has a severity tier: story < news < incident < conflict < disaster). Live data exists only if the snapshot was built. Story items carry view (the map view that best shows them) and map (a page link opening that view there).",
    inputSchema: obj({ feed: str("fires, events, ships, aircraft, jets, airliners, navy or story."), limit: num("Max items (default 10, max 100).", { minimum: 1, maximum: 100 }),
      near: str("'lon,lat' to limit to a radius."), km: num("Radius in km with near (default 50)."), where: { type: "array", items: { type: "string" }, description: WHERE_HINT },
      sort: str("Sort property, '-' for descending."), kind: str("story only: Conflict, Unrest, Trade, Industry & energy, Shipping, Health, Food & farms, Cyber & internet or Disasters (prefix is enough)."), window: str("story only: day, week, month or year (default week).", { enum: ["day", "week", "month", "year"] }),
      min_tier: str("story only: lowest severity to include: story, news, incident, conflict or disaster (or 1-5). Items come worst first.") }),
    run: async a => {
      const r = await api.live(a.feed, { limit: cap(a.limit, 10, 100), near: a.near ? parseLonLat(a.near) : undefined, km: a.km, where: a.where || [], sort: a.sort, window: a.window, kind: a.kind, minTier: a.min_tier });
      return r.rows ? { feed: a.feed, manifest: r.manifest, matched: r.matched, shown: r.shown, sort: r.sort, rows: r.rows.map(x => api.slim(x)) } : r;
    } },
  { name: "atlas_scenarios", title: "Ready-made scenarios",
    description: "Ready-made what-if scenarios from the page (sea routes closed, refineries or fields offline, conflict zones, droughts, grid, cables, chips, pandemics, food): group, title, what it tests, shocks as [kind, target, share], a shocks string for atlas_run_scenario and the page URL.",
    inputSchema: obj({ group: str("Optional group key: routes, energy, conflict, water, power, net, cyber, chips, bio, food.") }),
    run: async a => api.scenarios({ group: a.group }) },
  { name: "atlas_news", title: "News scenarios",
    description: "Scenarios built from recent news (conflict surges near infrastructure, earthquakes, storms, floods, topic searches) for the week, month or year: label, why, article URL, category, trend, likely level, the map view that best shows it (view), and the shocks and one-line text for each level (de = de-escalate, cur = as reported, esc = escalate).",
    inputSchema: obj({ window: str("week, month or year (default week).", { enum: ["week", "month", "year"] }), limit: num("Max items (default 15, max 50).", { minimum: 1, maximum: 50 }) }),
    run: async a => api.news({ window: a.window || "week", limit: cap(a.limit, 15, 50) }) },
  { name: "atlas_run_scenario", title: "Run a scenario",
    description: "Run a what-if scenario through the atlas's own model (oil and gas re-routing, power, cables, mines, people in a zone, roads and rail, rivers, chips, cloud, outbreaks, food) and return its chain of consequences (steps), headline figures, world oil/gas totals before→after, chokepoint flows, the most affected countries, detail sentences the map view that best shows it (view) and the page URL, which opens in that view. The first run takes 1-2 s. Give a preset title (atlas_scenarios), a news label (atlas_news; pick level), or a shocks string: kind.target.percent joined by ';'. Syntax: choke.<key>.percent (hormuz, malacca, suez, bab, danish, turkish, panama, cape); refinery.<id>.percent (ids from atlas_query refinery, e.g. r3143912); zone.<lon>_<lat>_<km>.percent (e.g. zone.36.23_49.99_50.70 = 50 km around 36.23E 49.99N, 70% damage); outbreak.<flu|cov|sev>_<lon>_<lat>.percent (percent = share of natural spread left); crop.<item>_<ISO3>.percent (wheat, maize, rice, soy, banana, chicken); fab.<fab id or cc-ISO3>.percent. Other kinds: field, port, lng, pipeline, power, mine, cable, water, chip, cloud (see atlas_kinds).",
    inputSchema: obj({ scenario: str("Preset title, news label, or shocks string."), level: str("For news items: de, cur (default) or esc.", { enum: ["de", "cur", "esc"] }) }, ["scenario"]),
    run: async a => api.runScenario(a.scenario, { level: a.level || "cur", site }) },
  { name: "atlas_kinds", title: "Scenario shock kinds",
    description: "The shock kinds a scenario can use, with chokepoint keys, outbreak pathogens and crop items, plus the shocks-string syntax for atlas_run_scenario and atlas_map_url.",
    inputSchema: obj({}),
    run: async () => api.kinds() },
  { name: "atlas_map_url", title: "Link to the atlas page",
    description: "Build a link to the atlas web page that opens a view (#view=key) and/or a scenario (#sim=shocks). lon/lat/zoom open the map at that spot (#at=lon,lat,zoom).",
    inputSchema: obj({ view: str("View key from atlas_views, e.g. 'internet'."), shocks: str("Scenario shocks string, e.g. 'choke.hormuz.100;choke.bab.100'."),
      lon: num("Longitude for an OpenStreetMap link."), lat: num("Latitude for an OpenStreetMap link."), zoom: num("Zoom for the OpenStreetMap link (default 8).") }),
    run: async a => api.mapUrl({ view: a.view, shocks: a.shocks, lon: a.lon, lat: a.lat, zoom: a.zoom, site }) }
];
const BY_NAME = Object.fromEntries(TOOLS.map(t => [t.name, t]));

// shrink the largest arrays until the JSON fits MAX_TEXT, and say what was cut
function fit(result) {
  let text = JSON.stringify(result);
  if (text.length <= MAX_TEXT) return { result, text };
  const r = structuredClone(result), notes = [];
  for (let pass = 0; pass < 30 && text.length > MAX_TEXT; pass++) {
    let best = null;
    const walk = (o, path) => {
      if (Array.isArray(o)) { if (o.length > 1 && (!best || o.length > best.arr.length)) best = { arr: o, path }; o.forEach((x, i) => walk(x, path + "[" + i + "]")); }
      else if (o && typeof o === "object") for (const [k, v] of Object.entries(o)) walk(v, path ? path + "." + k : k);
    };
    walk(r, "");
    if (!best) break;
    const before = best.arr.length;
    best.arr.length = Math.max(1, Math.floor(before * 0.6));
    notes.push(`${best.path}: ${before - best.arr.length} of ${before} items dropped`);
    text = JSON.stringify(r);
  }
  if (text.length > MAX_TEXT) { return { result: null, text: text.slice(0, MAX_TEXT) + "…", truncated: true }; }
  r.truncated = `result shortened to fit ${MAX_TEXT} characters (${notes.join("; ")}); narrow the query or lower limit`;
  return { result: r, text: JSON.stringify(r) };
}

async function callTool(params) {
  const t = BY_NAME[params?.name];
  if (!t) throw Object.assign(new Error(`Unknown tool: ${params?.name}`), { rpc: -32602 });
  const args = params.arguments || {};
  if (typeof args !== "object" || Array.isArray(args)) throw Object.assign(new Error("arguments must be an object"), { rpc: -32602 });
  for (const k of t.inputSchema.required || []) if (args[k] == null || args[k] === "") return toolError(`missing required argument "${k}"`);
  try {
    const raw = await t.run(args);
    const { result, text } = fit(raw);
    const structured = result && typeof result === "object" && !Array.isArray(result) ? result : undefined;
    return { content: [{ type: "text", text }], ...(structured ? { structuredContent: structured } : {}) };
  } catch (e) {
    if (!(e instanceof AtlasError)) log(`${t.name} failed:`, e.stack || e.message);
    return toolError(e.message);
  }
}
const toolError = msg => ({ content: [{ type: "text", text: msg }], isError: true });

// ---------- JSON-RPC ----------
const send = msg => process.stdout.write(JSON.stringify(msg) + "\n");
const reply = (id, result) => send({ jsonrpc: "2.0", id, result });
const fail = (id, code, message, data) => send({ jsonrpc: "2.0", id, error: { code, message, ...(data !== undefined ? { data } : {}) } });

async function handle(msg) {
  if (!msg || typeof msg !== "object" || Array.isArray(msg) || msg.jsonrpc !== "2.0" || typeof msg.method !== "string") {
    if (msg && (msg.result !== undefined || msg.error !== undefined)) return;   // a response to us; we send no requests
    return fail(msg?.id ?? null, -32600, "Invalid Request");
  }
  const { id, method, params } = msg, isNote = id === undefined;
  try {
    let result;
    switch (method) {
      case "initialize": {
        const want = params?.protocolVersion;
        result = { protocolVersion: SUPPORTED.includes(want) ? want : PROTOCOL, capabilities: { tools: {} },
          serverInfo: { name: "atlas", title: "World Extraction Atlas", version: "1.0.0" },
          instructions: "World Extraction Atlas: site-level data on oil & gas, mines, power, industry, pipelines, ports, cables, data centres, chips, military sites, water, plus live fires, conflict reports, ships and aircraft, and a what-if scenario model. Start with atlas_search or atlas_layers; coordinates are lon,lat; property keys are short (see atlas_fields)." };
        break;
      }
      case "notifications/initialized": case "notifications/cancelled": case "initialized": return;
      case "ping": result = {}; break;
      case "tools/list": result = { tools: TOOLS.map(({ name, title, description, inputSchema }) => ({ name, title, description, inputSchema })) }; break;
      case "tools/call": result = await callTool(params); break;
      default:
        if (isNote) return;
        return fail(id, -32601, `Method not found: ${method}`);
    }
    if (!isNote) reply(id, result);
  } catch (e) {
    if (isNote) return log(method, e.message);
    fail(id, e.rpc || -32603, e.rpc ? e.message : "Internal error", e.rpc ? undefined : e.message);
  }
}

const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", line => {
  if (!line.trim()) return;
  let msg;
  try { msg = JSON.parse(line); } catch { return fail(null, -32700, "Parse error"); }
  handle(msg).catch(e => log("unhandled:", e.stack || e.message));
});
// on stdin close, pending calls finish and the process ends by itself once stdout has drained
// (process.exit here could cut off a large reply still being written to a pipe)
process.stdout.on("error", e => { if (e.code === "EPIPE") process.exit(0); });
log(`ready (data: ${api.dataSource().base})`);
