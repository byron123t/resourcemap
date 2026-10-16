// Every CLI command and MCP tool is one function here. Each returns plain JSON-able data;
// cli/atlas.mjs formats it for a terminal and cli/mcp.mjs hands it to an agent.
import { page, pageConst, stripHtml, baseOptions } from "./page.mjs";
import {
  AtlasError, loadJSON, loadLayer, layerRegistry, resolveLayer, layerCatalogue, fieldSummary, fieldDocs, record, nameOf,
  distanceKm, inBbox, haversine, parseWhere, compareBy, resolveCountry, countryTest, countries, mergedTotals, norm, isNum,
  siteUrl, dataSource, KEY_FIELDS, SIZE_FIELD, TABLES, tableRecords
} from "./data.mjs";

export { AtlasError, configure, dataSource } from "./data.mjs";

const RETIRED = new Set(["cancelled", "retired", "abandoned", "decommissioning", "closed", "mothballed", "shelved - inferred 2 y", "retired - inferred"]);
const clampLimit = (v, d, max = 1000) => { const n = v == null || v === "" ? d : Number(v); if (!isFinite(n) || n < 0) throw new AtlasError(`bad limit "${v}"`); return Math.min(Math.floor(n), max); };
const sizeField = key => SIZE_FIELD[key] || "q";
const toList = v => v == null || v === "" ? [] : Array.isArray(v) ? v.flatMap(toList) : String(v).split(",").map(s => s.trim()).filter(Boolean);

// ---------- layers & fields ----------
export async function layers({ counts, tables = true } = {}) {
  return { source: dataSource().base, layers: await layerCatalogue({ counts, tables }) };
}

async function describeKeys(r) {
  const docs = await fieldDocs(), d = docs.files[r.file] || docs.files[r.file.replace(/^live\//, "live/")] || {};
  return k => d.keys?.[k] || (["n", "s", "k", "c"].includes(k) ? docs.common[k] : k === "q" ? `${docs.common.q}${r.unit ? " (" + r.unit + ")" : ""}` : null);
}
export async function fields(layer) {
  const r = resolveLayer(layer, { tables: true }), desc = await describeKeys(r);
  let recs, count;
  if (r.type === "table") { const j = await loadJSON(r.file); recs = tableRecords(r.key, j).map(p => ({ properties: p })); count = recs.length; }
  else { const fc = await loadLayer(r); recs = fc.features; count = recs.length; }
  const sum = fieldSummary({ features: recs });
  const docs = await fieldDocs();
  return { layer: r.key, label: r.label, file: r.file, count, about: docs.files[r.file]?.desc || null,
    fields: sum.map(s => ({ key: s.key, meaning: desc(s.key), present: s.count, example: s.example, ...(s.min != null ? { min: s.min, max: s.max } : {}) })) };
}

// ---------- query ----------
// o: {near:[lon,lat], km, bbox:[w,s,e,n], country, where:[...], sort, limit, offset, fields:[...], status}
export async function query(layer, o = {}) {
  const r = resolveLayer(layer), fc = await loadLayer(r);
  const where = toList(o.where).length ? (Array.isArray(o.where) ? o.where : [o.where]).map(parseWhere) : [];
  const c = o.country ? await resolveCountry(o.country) : null, inC = c ? await countryTest(c) : null;
  const near = o.near, km = o.km == null || o.km === "" ? (near ? 50 : null) : Number(o.km);
  if (km != null && !isFinite(km)) throw new AtlasError(`bad --km "${o.km}"`);
  const out = [];
  fc.features.forEach((f, i) => {
    const p = f.properties || {};
    if (o.bbox && !inBbox(f.geometry, o.bbox)) return;
    if (inC && !inC(p, f.geometry)) return;
    if (!where.every(w => w.test(p))) return;
    let dist;
    if (near) { dist = distanceKm(f.geometry, near[0], near[1]); if (km > 0 && dist > km) return; }
    const rec = record(r.key, f, i);
    if (near) rec.km = +dist.toFixed(2);
    out.push(rec);
  });
  const sort = o.sort || (near ? "km" : out.some(x => isNum(x.props[sizeField(r.key)])) ? "-" + sizeField(r.key) : null);
  if (sort) out.sort(compareBy(sort));
  const limit = clampLimit(o.limit, 20, o.maxLimit || 100000), offset = clampLimit(o.offset, 0, 1e9);
  const rows = out.slice(offset, offset + limit);
  return { layer: r.key, label: r.label, unit: r.unit, matched: out.length, shown: rows.length, sort, country: c ? { k: c.k, n: c.n } : undefined,
    near: near ? { lon: near[0], lat: near[1], km: km || null } : undefined, keyFields: KEY_FIELDS[r.key] || [],
    fields: o.fields ? toList(o.fields) : undefined, rows };
}
// strip internal bits from a record for output; fields: keep only those properties
export function slim(rec, fieldsWanted, { geometry = false } = {}) {
  const props = fieldsWanted ? Object.fromEntries(fieldsWanted.map(f => [f, rec.props[f] ?? rec[f]])) : rec.props;
  return { id: rec.id, name: rec.name, ...(rec.km != null ? { km: rec.km } : {}), lon: rec.lon, lat: rec.lat, properties: props, ...(geometry ? { geometry: rec.geometry } : {}) };
}

// ---------- get ----------
export async function get(layer, idOrName) {
  const r = resolveLayer(layer), fc = await loadLayer(r);
  const q = String(idOrName ?? "").trim();
  if (!q) throw new AtlasError("get needs an id or a name");
  const feats = fc.features;
  let i = -1;
  if (/^#\d+$/.test(q)) i = +q.slice(1) < feats.length ? +q.slice(1) : -1;
  if (i < 0) i = feats.findIndex((f, j) => record(r.key, f, j).id === q);
  const nq = norm(q);
  if (i < 0) i = feats.findIndex(f => norm(nameOf(f.properties || {})) === nq);
  if (i < 0) {
    let best = -1, bestQ = -Infinity;
    feats.forEach((f, j) => { const n = norm(nameOf(f.properties || {})); if (n && n.includes(nq)) { const v = Number(f.properties[sizeField(r.key)]) || 0; if (best < 0 || v > bestQ) { best = j; bestQ = v; } } });
    i = best;
  }
  if (i < 0) throw new AtlasError(`nothing in ${r.key} matches "${q}"`, 2);
  const rec = record(r.key, feats[i], i), desc = await describeKeys(r);
  const props = Object.entries(rec.props).map(([k, v]) => ({ key: k, label: desc(k), value: v }));
  return { layer: r.key, kind: r.item || r.label, id: rec.id, name: rec.name, lon: rec.lon, lat: rec.lat, geometry: rec.geometry.type, properties: props,
    links: rec.lat != null ? { osm: `https://www.openstreetmap.org/?mlat=${rec.lat}&mlon=${rec.lon}#map=12/${rec.lat}/${rec.lon}` } : {} };
}

// ---------- search ----------
const UNNAMED = new Set(["rail", "roads", "rivers", "shipping", "water_risk", "glaciers", "groundwater", "foot", "oilgas_outlines", "coal_boundaries", "fires",
  "maritime", "disputed_lines", "ports_ne", "countries", "disputed", "saline_gw"]);
function score(name, q) {
  const n = norm(name);
  if (!n || !q) return 0;
  if (n === q) return 100;
  if (n.startsWith(q)) return 80;
  const i = n.indexOf(q);
  if (i < 0) return 0;
  return /[^a-z0-9]/.test(n[i - 1]) ? 60 : 30;
}
export async function search(text, { layers: want, limit } = {}) {
  const q = norm(text);
  if (!q) throw new AtlasError("search needs some text");
  const lim = clampLimit(limit, 10, 500);
  const { list } = await countries(), labels = (await loadJSON("labels.json", { optional: true })) || [];
  const cs = list.map(c => ({ c, s: Math.max(score(c.n, q), score(c.alt, q), c.k.toLowerCase() === q ? 100 : 0) })).filter(x => x.s).sort((a, b) => b.s - a.s).slice(0, lim)
    .map(({ c }) => ({ k: c.k, n: c.n, lon: c.x, lat: c.y }));
  const places = labels.filter(l => l.t !== "c").map(l => ({ l, s: score(l.n, q) })).filter(x => x.s)
    .sort((a, b) => b.s - a.s || (a.l.t === "p" ? 0 : 1) - (b.l.t === "p" ? 0 : 1) || (b.l.pop || 0) - (a.l.pop || 0)).slice(0, lim)
    .map(({ l }) => ({ n: l.n, type: { p: "city", m: "sea", g: "region" }[l.t] || l.t, k: l.k, admin: l.a, pop: l.pop, lon: l.x, lat: l.y }));
  const { reg } = layerRegistry();
  const keys = want && toList(want).length ? toList(want).map(k => resolveLayer(k).key) : Object.values(reg).filter(r => r.type !== "table" && !UNNAMED.has(r.key)).map(r => r.key);
  const feats = [];
  for (const k of keys) {
    let fc;
    try { fc = await loadLayer(k); } catch (e) { if (e.code === 2) continue; throw e; }
    fc.features.forEach((f, i) => {
      const p = f.properties || {}, s = Math.max(score(nameOf(p), q), score(p.o, q) * 0.5, score(p.ia, q), score(p.i, q), score(p.h, q));
      if (s) feats.push({ s, v: Number(p[sizeField(k)]) || 0, rec: record(k, f, i) });
    });
  }
  feats.sort((a, b) => b.s - a.s || b.v - a.v);
  return { query: text, countries: cs, places, features: feats.slice(0, lim).map(({ rec }) => ({ layer: rec.layer, id: rec.id, name: rec.name, lon: rec.lon, lat: rec.lat,
    ...Object.fromEntries((KEY_FIELDS[rec.layer] || []).slice(0, 2).concat(["q", "c", "k", "cc"]).filter(f => rec.props[f] != null).map(f => [f, rec.props[f]])) })), featuresMatched: feats.length };
}

// ---------- country profile ----------
const TOT_LABEL = { og: "Oil & gas production", og_n: "Oil & gas fields with output", coal: "Coal mine capacity", coal_n: "Coal mines", iron: "Iron ore production", iron_n: "Iron ore mines",
  mine_km2: "Mined land", crit_n: "Critical mineral deposits", pw_gw: "Power capacity", steel: "Steel capacity", cement: "Cement capacity", chem_n: "Chemical plants",
  ammonia_n: "Ammonia plants", lng_exp: "LNG export capacity", lng_imp: "LNG import capacity", ports: "Seaports", lake_km2: "Lakes", res_km2: "Reservoirs", fresh_km2: "Lakes & reservoirs",
  ice_km2: "Glaciers & ice", bws: "Average water stress", bws_hi: "Area at high water stress", gw_basin_km2: "Area over major aquifers", pop: "Population (Natural Earth)",
  kpop: "Population (Kontur 2023)", kdens: "Population density" };
const TOT_UNIT = { og_n: "fields", coal_n: "mines", iron_n: "mines", ammonia_n: "plants", lake_km2: "km²", res_km2: "km²", ice_km2: "km²", bws_hi: "% of area", pop: "people" };
const DOM = { net: ["People online", "% of people"], bb: ["Fixed broadband", "subscriptions per 100 people"], srv: ["Secure web servers", "per million people"],
  cens: ["Internet censorship", "0 open – 100 sealed"], beds: ["Hospital beds", "per 1,000 people"], docs: ["Doctors", "per 1,000 people"],
  chipX: ["Chip exports", "US$ bn a year"], chipM: ["Chip imports", "US$ bn a year"], minr: ["Mineral rents", "% of GDP"], fdep: ["Cereals imported", "share of wheat, maize and rice supply"] };
const ENERGY = { cr: ["Crude oil production", "kb/d"], lq: ["All liquids production", "kb/d"], pc: ["Oil products consumed", "kb/d"], pp: ["Oil products produced", "kb/d"],
  dc: ["Distillate (diesel) consumed", "kb/d"], dp: ["Distillate produced", "kb/d"], gp: ["Gas produced", "bcm/yr"], gc: ["Gas consumed", "bcm/yr"],
  gi: ["Gas imported", "bcm/yr"], ge: ["Gas exported", "bcm/yr"], dy: ["Distillate share of refinery output (2014)", "share"] };
export const PROFILE_LAYERS = ["oilgas", "coal", "iron", "power", "refinery", "lng", "steel", "cement", "fabs"];
const COUNT_LAYERS = ["crit", "chem", "ports", "bases", "airports", "dc", "cloud", "chipsites", "biolabs", "landings"];
function fieldLabel(f) {
  const o = baseOptions().find(b => b.value === "c:" + f);
  if (TOT_LABEL[f]) return TOT_LABEL[f];
  if (o) return o.label;
  if (/^pw_/.test(f)) return "Power capacity: " + f.slice(3);
  return DOM[f]?.[0] || f;
}
const fieldUnit = f => page.CHORO[f]?.[1] || TOT_UNIT[f] || (/^pw_/.test(f) ? "GW" : DOM[f]?.[1] || null);
function rankOf(T, k, f) {
  const vals = Object.entries(T).filter(([, c]) => isNum(c[f]) && c[f] > 0).sort((a, b) => b[1][f] - a[1][f]);
  const i = vals.findIndex(([kk]) => kk === k);
  return i < 0 ? null : { rank: i + 1, of: vals.length };
}
export async function country(q, { top = 5 } = {}) {
  const c = await resolveCountry(q), T = await mergedTotals(), t = T[c.k] || {};
  const dom = (await loadJSON("domains.json", { optional: true }))?.countries?.[c.k] || {};
  const en = await loadJSON("country_energy.json", { optional: true });
  const totals = [];
  for (const [f, v] of Object.entries(t)) {
    if (["n", "id", "crit_m"].includes(f) || DOM[f] || !isNum(v)) continue;
    totals.push({ field: f, label: fieldLabel(f), value: v, unit: fieldUnit(f), ...rankOf(T, c.k, f) });
  }
  const domains = Object.keys(DOM).filter(f => t[f] != null).map(f => ({ field: f, label: DOM[f][0], value: t[f], unit: DOM[f][1], year: dom[f + "Y"] ?? null, ...rankOf(T, c.k, f) }));
  const e = en?.countries?.[c.k];
  const energy = e ? Object.keys(ENERGY).filter(f => e[f] != null).map(f => ({ field: f, label: ENERGY[f][0], value: e[f], unit: ENERGY[f][1], year: e[f + "y"] ?? null })) : [];
  if (e && e.gc > 0) energy.push({ field: "gas_import_share", label: "Gas imports ÷ consumption", value: +((e.gi || 0) / e.gc).toFixed(3), unit: "share", year: e.giy ?? null });
  const inC = await countryTest(c), sites = {}, counts = {};
  const lim = clampLimit(top, 5, 50);
  for (const k of [...PROFILE_LAYERS, ...COUNT_LAYERS]) {
    let fc;
    try { fc = await loadLayer(k); } catch (err) { if (err.code === 2) continue; throw err; }
    const sf = sizeField(k), mine = [];
    fc.features.forEach((f, i) => { if (inC(f.properties || {}, f.geometry)) mine.push(record(k, f, i)); });
    counts[k] = mine.length;
    if (!PROFILE_LAYERS.includes(k)) continue;
    sites[k] = mine.filter(x => !RETIRED.has(String(x.props.s || "").toLowerCase()) && isNum(x.props[sf])).sort(compareBy("-" + sf)).slice(0, lim)
      .map(x => ({ id: x.id, name: x.name, value: x.props[sf], unit: k === "fabs" ? "wafers/mo"  : resolveLayer(k).unit, status: x.props.s ?? null,
        ...(k === "power" ? { fuel: x.props.f } : {}), ...(k === "fabs" ? { operator: x.props.o, node_nm: x.props.nm } : {}), lon: x.lon, lat: x.lat }));
  }
  return { k: c.k, n: c.n, minerals: t.crit_m || null, totals, domains, energy, energyYearNote: en?.units || null, sites, counts,
    shadingUrl: siteUrl() + "#view=overview" };
}

// ---------- near ----------
export async function near(lon, lat, { km = 50, limit = 5, layers: want } = {}) {
  const R = Number(km);
  if (!isFinite(R) || R <= 0) throw new AtlasError(`bad --km "${km}"`);
  const lim = clampLimit(limit, 5, 200), { reg } = layerRegistry();
  const keys = want && toList(want).length ? toList(want).map(k => resolveLayer(k).key) : Object.values(reg).filter(r => r.type === "static" || r.type === "live").map(r => r.key);
  const groups = [];
  for (const k of keys) {
    let fc;
    try { fc = await loadLayer(k); } catch (e) { if (e.code === 2) continue; throw e; }
    if (!want && fc.features[0]?.geometry?.type !== "Point") continue;
    const hits = [];
    fc.features.forEach((f, i) => {
      const g = f.geometry;
      if (g?.type === "Point" && (Math.abs(g.coordinates[1] - lat) > R / 100 || haversine(lon, lat, g.coordinates[0], g.coordinates[1]) > R)) return;
      const d = distanceKm(g, lon, lat);
      if (d <= R) { const rec = record(k, f, i); rec.km = +d.toFixed(2); hits.push(rec); }
    });
    if (!hits.length) continue;
    hits.sort((a, b) => a.km - b.km);
    const r = reg[k];
    groups.push({ layer: k, label: r.label, unit: r.unit, count: hits.length, items: hits.slice(0, lim).map(x => ({ id: x.id, name: x.name, km: x.km,
      ...Object.fromEntries(["q", ...(KEY_FIELDS[k] || []).slice(0, 2), "s"].filter(f => x.props[f] != null).map(f => [f, x.props[f]])) })) });
  }
  const labels = (await loadJSON("labels.json", { optional: true })) || [];
  const cities = labels.filter(l => l.t === "p").map(l => ({ n: l.n, k: l.k, pop: l.pop, km: +haversine(lon, lat, l.x, l.y).toFixed(1) })).sort((a, b) => a.km - b.km);
  const inR = cities.filter(c => c.km <= R);
  return { lon, lat, km: R, groups, cities: (inR.length ? inR : cities).slice(0, inR.length ? 10 : 5) };
}

// ---------- views ----------
const viewRow = (key, v, site) => {
  const base = v.base ? baseOptions().find(b => b.value === v.base) : null;
  return { key, group: v.g, label: v.label, layers: v.on || [], shading: v.base ? { value: v.base, label: base?.label || v.base } : null,
    filters: Object.fromEntries(["fuel", "mineral", "fire", "bases", "air", "status", "img", "agri"].filter(f => v[f] != null && v[f] !== page.VIEW_DEFAULT[f]).map(f => [f, v[f]])),
    big: v.big || null, note: stripHtml(v.note), connection: stripHtml(v.x), go: v.go ? { label: v.go.label, ...(v.go.c ? { lon: v.go.c[0], lat: v.go.c[1], zoom: v.go.z } : {}) } : null,
    url: siteUrl(site) + "#view=" + key };
};
export function views({ site } = {}) { return { views: Object.entries(page.VIEWS).map(([k, v]) => viewRow(k, v, site)) }; }
export function view(key, { site } = {}) {
  const v = page.VIEWS[key];
  if (!v) {
    const n = norm(key), hit = Object.entries(page.VIEWS).find(([, x]) => norm(x.label).includes(n));
    if (!hit) throw new AtlasError(`unknown view "${key}". Views: ${Object.keys(page.VIEWS).join(", ")}`, 2);
    return viewRow(hit[0], hit[1], site);
  }
  return viewRow(key, v, site);
}

// ---------- background shading ----------
export function shading() {
  const out = [];
  for (const o of baseOptions()) {
    const [kind, field] = o.value.split(":");
    out.push({ value: o.value, field, label: o.label, group: o.group, unit: kind === "c" ? page.CHORO[field]?.[1] || null : kind === "w" ? "Aqueduct category 0–4 per basin" : "WHYMAP recharge class",
      rankable: kind === "c" });
  }
  for (const f of Object.keys(page.CHORO)) if (!out.some(o => o.value === "c:" + f)) out.push({ value: "c:" + f, field: f, label: fieldLabel(f), group: "", unit: page.CHORO[f][1], rankable: true });
  return { options: out };
}
export async function shadingRank(field, { top = 15, asc = false } = {}) {
  let f = String(field ?? "").replace(/^c:/, "");
  const T = await mergedTotals();
  if (!f) throw new AtlasError("shading needs a field, e.g. net, cens, og, pw_gw");
  if (!page.CHORO[f] && !Object.values(T).some(c => isNum(c[f]))) {
    const hit = shading().options.find(o => o.rankable && norm(o.label).includes(norm(f)));
    if (!hit) throw new AtlasError(`unknown shading field "${field}". Try: ${Object.keys(page.CHORO).join(", ")}`, 2);
    f = hit.field;
  }
  const dom = (await loadJSON("domains.json", { optional: true }))?.countries || {};
  const rows = Object.entries(T).filter(([, c]) => isNum(c[f]) && (asc || c[f] > 0)).map(([k, c]) => ({ k, n: c.n, value: c[f], year: dom[k]?.[f + "Y"] ?? undefined }))
    .sort((a, b) => asc ? a.value - b.value : b.value - a.value);
  const lim = clampLimit(top, 15, 500);
  return { field: f, label: fieldLabel(f), unit: fieldUnit(f), countries: rows.length, order: asc ? "ascending" : "descending",
    rows: rows.slice(0, lim).map((r, i) => ({ rank: i + 1, ...r })), url: siteUrl() + "#view=" + (Object.entries(page.VIEWS).find(([, v]) => v.base === "c:" + f)?.[0] || "overview") };
}

// ---------- sources ----------
export function sources() {
  return { sources: page.SOURCES.map(([name, url, what, licence, when, file]) => ({ name, url, what, licence, when: when || null, file: file || null })) };
}

// ---------- live ----------
export const LIVE_SORT = { fires: "-frp", events: "-e", ships: "-len", aircraft: "-alt", jets: "-alt", airliners: "-alt", navy: null };
const age = t => { const ms = Date.now() - Date.parse(t); if (!isFinite(ms)) return null; const m = Math.round(ms / 60000); return m < 60 ? m + " min" : m < 2880 ? Math.round(m / 60) + " h" : Math.round(m / 1440) + " days"; };
export async function live(feed, o = {}) {
  const m = await loadJSON("live/manifest.json", { optional: true });
  if (!m) throw new AtlasError("no live data: site/data/live/manifest.json is missing. Run pipeline/live/fetch_live.py, or use --remote", 2);
  const feeds = Object.entries(m.feeds || {}).map(([k, v]) => ({ feed: k, updated: v.updated || null, age: v.updated ? age(v.updated) : null, count: v.count ?? null,
    source: v.source || null, ...(v.status ? { status: v.status } : {}), ...(v.error ? { error: v.error } : {}) }));
  if (!feed) return { generated: m.generated, age: age(m.generated), feeds };
  if (feed === "story") {
    const s = await loadJSON("live/story.json", { optional: true });
    if (!s) throw new AtlasError("live/story.json is not present", 2);
    const w = o.window || "week", W = s.windows?.[w];
    if (!W) throw new AtlasError(`story window "${w}" not found; windows: ${Object.keys(s.windows || {}).join(", ")}`, 2);
    const lim = clampLimit(o.limit, 10, 200);
    return { feed, window: w, updated: s.updated, buckets: W.buckets?.length, items: (W.items || []).slice().sort((a, b) => b.z - a.z).slice(0, lim)
      .map(x => ({ title: x.title || x.n, place: x.n, cc: x.cc, lon: x.lon, lat: x.lat, bucket: x.t, reports: x.e, articles: x.a, surge: x.z, usual: x.base, url: x.u?.[0] })) };
  }
  const meta = feeds.find(f => f.feed === feed);
  const res = await query(feed, { ...o, sort: o.sort || LIVE_SORT[feed] || undefined, limit: o.limit ?? 10 });
  return { ...res, manifest: meta || null };
}

// ---------- map URLs ----------
const SIM_TOKEN = /^[a-z]+\.[^;&\s]+\.\d+(\.\d+)?$/i;
export function simTokens(shocks) {
  if (typeof shocks === "string") {
    const toks = shocks.split(";").map(s => s.trim()).filter(Boolean);
    for (const t of toks) {
      const k = t.slice(0, t.indexOf("."));
      if (!SIM_TOKEN.test(t) || !page.SIM_KIND[k]) throw new AtlasError(`bad shock "${t}": use kind.target.percent, kinds: ${Object.keys(page.SIM_KIND).join(", ")}`);
    }
    return toks.join(";");
  }
  return shocks.map(([k, t, m]) => `${k}.${Array.isArray(t) ? t.join("_") : t}.${Math.round(m * 100)}`).join(";");
}
export function mapUrl({ view: v, shocks, lon, lat, zoom, site } = {}) {
  const toks = [], notes = [];
  if (v) { if (!page.VIEWS[v]) throw new AtlasError(`unknown view "${v}". Views: ${Object.keys(page.VIEWS).join(", ")}`, 2); toks.push("view=" + v); }
  if (shocks && (shocks.length || typeof shocks === "string")) toks.push("sim=" + simTokens(shocks));
  if (lon != null && lat != null) toks.push(`at=${+(+lon).toFixed(4)},${+(+lat).toFixed(4)},${+(zoom ?? 8)}`);
  const out = { url: siteUrl(site) + (toks.length ? "#" + toks.join("&") : "") };
  if (notes.length) out.notes = notes;
  return out;
}

// ---------- scenarios (engine in ./scenario.mjs, loaded only when needed) ----------
let engineDir = null;
export async function engine() {
  let E;
  try { E = await import(new URL("./scenario.mjs", import.meta.url).href); }
  catch (e) {
    const missing = e.code === "ERR_MODULE_NOT_FOUND" && /scenario\.mjs/.test(e.message);
    throw new AtlasError(missing ? "scenario engine not available: cli/lib/scenario.mjs is missing" : `scenario engine not available: ${e.message}`, 2);
  }
  const src = dataSource();
  if (src.remote) throw new AtlasError("scenarios need the data on disk: the scenario engine does not support --remote / ATLAS_REMOTE (use a checkout's site/data or --data DIR)", 1);
  if (E.setDataDir && engineDir !== src.base) { E.setDataDir(src.base); engineDir = src.base; }   // setDataDir drops the engine's caches
  return E;
}
export const SHOCK_SYNTAX = "Shocks are kind.target.percent joined by ';' (the page's #sim= syntax): choke.hormuz.100, refinery.r3143912.100, " +
  "zone.lon_lat_km.percent (zone.36.23_49.99_50.70 = 50 km round 36.23E 49.99N, 70% damage), outbreak.<flu|cov|sev>_lon_lat.percent " +
  "(percent = how much of its natural spread is left), crop.<item>_<ISO3>.percent (item: wheat, maize, rice, soy, banana, chicken), " +
  "fab.<fab id or cc-ISO3>.percent. Other kinds: field, port, lng, pipeline, power, mine, cable, water (HydroBASINS code), chip, cloud. See atlas kinds.";
export async function kinds() {
  const E = await engine(), k = E.listKinds();
  return { syntax: SHOCK_SYNTAX, ...k };
}
export async function scenarios({ group } = {}) {
  const E = await engine(), all = await E.listPresets();
  const groups = page.SIM_GROUPS.filter(([g]) => !page.SIM_NEWS[g]).map(([g, label]) => ({ group: g, label }));
  const rows = group ? all.filter(p => p.group === group || norm(p.groupLabel) === norm(group)) : all;
  if (group && !rows.length) throw new AtlasError(`no scenario group "${group}". Groups: ${groups.map(g => g.group).join(", ")}`, 2);
  return { groups, presets: rows.map(p => ({ ...p, shocksString: simTokens(p.shocks), url: mapUrl({ shocks: p.shocks }).url })) };
}
export async function news({ window = "week", limit } = {}) {
  if (!page.SIM_NEWS[window]) throw new AtlasError(`window must be one of ${Object.keys(page.SIM_NEWS).join(", ")}`);
  const E = await engine(), items = await E.listNews(window);
  const lim = clampLimit(limit, 50, 500);
  return { window, label: page.SIM_NEWS[window], count: items.length, items: items.slice(0, lim) };
}
export async function runScenario(input, { level = "cur", site, countries } = {}) {
  if (!["de", "cur", "esc"].includes(level)) throw new AtlasError("level must be de, cur or esc");
  const E = await engine(), s = String(input ?? "").trim();
  if (!s) throw new AtlasError("scenario needs a preset title, a news label or a shocks string like choke.hormuz.100");
  let shocks, title = null, from = "shocks";
  if (s.split(";").every(t => SIM_TOKEN.test(t.trim()) || !t.trim())) {
    try { shocks = E.parseShocks(s); } catch (e) { throw new AtlasError(e.message); }
  } else {
    const n = norm(s), presets = await E.listPresets();
    const p = presets.find(x => norm(x.title) === n) || presets.find(x => norm(x.title).includes(n));
    if (p) { shocks = p.shocks; title = p.title; from = "preset"; }
    else {
      for (const w of Object.keys(page.SIM_NEWS)) {
        const items = await E.listNews(w).catch(() => []);
        const it = items.find(x => norm(x.label) === n) || items.find(x => norm(x.label).includes(n));
        if (it) { shocks = it.levels?.[level] || it.shocks; title = it.label; from = `news:${w}`; break; }
      }
    }
    if (!shocks) throw new AtlasError(`no preset or news scenario matches "${s}" (list them with: atlas scenarios / atlas news)`, 2);
  }
  if (!shocks?.length) throw new AtlasError(`no shocks parsed from "${s}"`);
  const res = await E.runScenario(shocks, { countries });
  const shocksString = simTokens(shocks);
  return { from, level: from.startsWith("news") ? level : undefined, preset: title || undefined, ...res, title: title || res.title, shocksString,
    url: site || process.env.ATLAS_SITE ? mapUrl({ shocks, site }).url : res.url || mapUrl({ shocks }).url };
}

export { TABLES };
export const pageConstant = pageConst;
