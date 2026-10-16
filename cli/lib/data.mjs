// Data access for the CLI and MCP server: where the files are, cached loads, the layer catalogue,
// and the geometry / filter / sort helpers every command shares.
import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { REPO, page, sidebarLabels } from "./page.mjs";

export const DEFAULT_REMOTE = "https://byron123t.github.io/resourcemap/data/";
export const DEFAULT_SITE = "https://byron123t.github.io/resourcemap/";

export class AtlasError extends Error {
  // code: 1 usage error, 2 not found
  constructor(message, code = 1) { super(message); this.code = code; }
}

// ---------- where the data lives ----------
const opts = { data: null, remote: null };
export function configure({ data, remote } = {}) {
  if (data !== undefined) opts.data = data || null;
  if (remote !== undefined) opts.remote = remote === true ? (process.env.ATLAS_REMOTE || DEFAULT_REMOTE) : remote || null;
  cache.clear();
}
export function dataSource() {
  const remote = opts.remote || (!opts.data && process.env.ATLAS_REMOTE) || null;
  if (remote) return { remote: true, base: remote.endsWith("/") ? remote : remote + "/" };
  return { remote: false, base: path.resolve(opts.data || process.env.ATLAS_DATA || path.join(REPO, "site", "data")) };
}
export const siteUrl = s => { const u = s || process.env.ATLAS_SITE || DEFAULT_SITE; return u.endsWith("/") ? u : u + "/"; };

const cache = new Map();
// Load a JSON file relative to the data dir. optional: resolve to null when missing.
export function loadJSON(rel, { optional = false } = {}) {
  const key = rel;
  if (!cache.has(key)) {
    const src = dataSource();
    const p = (async () => {
      let text;
      if (src.remote) {
        const r = await fetch(src.base + rel);
        if (r.status === 404) return null;
        if (!r.ok) throw new AtlasError(`fetching ${src.base + rel}: HTTP ${r.status}`, 2);
        text = await r.text();
      } else {
        try { text = await readFile(path.join(src.base, rel), "utf8"); }
        catch (e) { if (e.code === "ENOENT") return null; throw e; }
      }
      return JSON.parse(text);
    })();
    cache.set(key, p);
  }
  return cache.get(key).then(v => {
    if (v == null && !optional) throw new AtlasError(`data file not found: ${rel} (in ${dataSource().base})`, 2);
    return v;
  });
}
export async function exists(rel) {
  const src = dataSource();
  if (src.remote) return (await loadJSON(rel, { optional: true })) != null;
  try { await stat(path.join(src.base, rel)); return true; } catch { return false; }
}

// ---------- text, numbers, geometry ----------
export const norm = s => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[’']/g, "'").trim();
export const isNum = v => v !== null && v !== "" && typeof v !== "boolean" && isFinite(Number(v));

export function haversine(lon1, lat1, lon2, lat2) {
  const R = 6371.0088, r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r, dLon = (lon2 - lon1) * r;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}
// every vertex of a geometry as [lon, lat]
export function vertices(g) {
  if (!g) return [];
  const out = [], walk = c => { if (typeof c[0] === "number") out.push(c); else for (const x of c) walk(x); };
  if (g.type === "GeometryCollection") for (const x of g.geometries) out.push(...vertices(x));
  else walk(g.coordinates);
  return out;
}
export function bboxOf(g) {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (const [x, y] of vertices(g)) { if (x < w) w = x; if (x > e) e = x; if (y < s) s = y; if (y > n) n = y; }
  return [w, s, e, n];
}
function ringContains(ring, x, y) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
export function polygonContains(g, x, y) {
  if (!g) return false;
  const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
  return polys.some(rings => ringContains(rings[0], x, y) && !rings.slice(1).some(h => ringContains(h, x, y)));
}
// a representative [lon, lat]: the point itself, the middle vertex of a line, the bbox centre of a polygon
export function repPoint(g) {
  if (!g) return null;
  if (g.type === "Point") return g.coordinates;
  const v = vertices(g);
  if (!v.length) return null;
  if (/Polygon/.test(g.type)) { const [w, s, e, n] = bboxOf(g); const c = [(w + e) / 2, (s + n) / 2]; return polygonContains(g, ...c) ? c : v[0]; }
  return v[Math.floor(v.length / 2)];
}
// distance from a point to a feature in km: exact for points, nearest vertex for lines, 0 inside polygons
export function distanceKm(g, lon, lat) {
  if (!g) return Infinity;
  if (g.type === "Point") return haversine(lon, lat, g.coordinates[0], g.coordinates[1]);
  if (/Polygon/.test(g.type) && polygonContains(g, lon, lat)) return 0;
  let best = Infinity;
  for (const [x, y] of vertices(g)) { const d = haversine(lon, lat, x, y); if (d < best) best = d; }
  return best;
}
export function inBbox(g, [w, s, e, n]) {
  const wrap = w > e;  // crosses the antimeridian
  const inX = x => wrap ? x >= w || x <= e : x >= w && x <= e;
  if (!g) return false;
  if (g.type === "Point") { const [x, y] = g.coordinates; return inX(x) && y >= s && y <= n; }
  return vertices(g).some(([x, y]) => inX(x) && y >= s && y <= n);
}
export function parseLonLat(str) {
  const m = String(str ?? "").split(/[,\s]+/).filter(Boolean).map(Number);
  if (m.length !== 2 || !m.every(isFinite) || Math.abs(m[0]) > 180 || Math.abs(m[1]) > 90)
    throw new AtlasError(`expected lon,lat (e.g. 139.69,35.69), got "${str}"`);
  return m;
}
export function parseBbox(str) {
  const m = String(str ?? "").split(",").map(Number);
  if (m.length !== 4 || !m.every(isFinite)) throw new AtlasError(`expected --bbox w,s,e,n, got "${str}"`);
  return m;
}

// ---------- filters, sort, fields ----------
// "q>5", "s=operating", "cc=TWN", "m~lithium", "f!=coal"; = and != are case-insensitive, ~ is substring
const WHERE_RE = /^\s*([\w.]+)\s*(>=|<=|!=|=|>|<|~)\s*(.*?)\s*$/;
export function parseWhere(expr) {
  const m = WHERE_RE.exec(String(expr));
  if (!m) throw new AtlasError(`bad --where "${expr}": use field OP value with OP one of = != > >= < <= ~`);
  const [, field, op, raw] = m;
  if (/^[<>=!~]/.test(raw) || (!raw && op !== "=" && op !== "!=")) throw new AtlasError(`bad --where "${expr}": missing or malformed value`);
  const val = raw.replace(/^["']|["']$/g, "");
  return { field, op, value: val, test: whereTest(field, op, val) };
}
function whereTest(field, op, val) {
  const nv = isNum(val) ? Number(val) : null, lv = norm(val);
  const get = p => field.split(".").reduce((o, k) => o == null ? o : o[k], p);
  return p => {
    const v = get(p);
    switch (op) {
      case "~": return v != null && norm(Array.isArray(v) ? v.join("|") : v).includes(lv);
      case "=": return v == null ? lv === "" || lv === "null" : nv != null && isNum(v) ? Number(v) === nv : norm(v) === lv;
      case "!=": return v == null ? !(lv === "" || lv === "null") : nv != null && isNum(v) ? Number(v) !== nv : norm(v) !== lv;
      default: {
        if (v == null || v === "") return false;
        const a = isNum(v) && nv != null ? Number(v) : norm(v), b = nv != null && isNum(v) ? nv : lv;
        return op === ">" ? a > b : op === ">=" ? a >= b : op === "<" ? a < b : a <= b;
      }
    }
  };
}
export function compareBy(spec) {
  const desc = spec.startsWith("-"), f = spec.replace(/^[-+]/, "");
  return (a, b) => {
    const x = a[f] ?? a.props?.[f], y = b[f] ?? b.props?.[f];
    if (x == null || x === "") return y == null || y === "" ? 0 : 1;   // missing values last either way
    if (y == null || y === "") return -1;
    const c = isNum(x) && isNum(y) ? Number(x) - Number(y) : String(x).localeCompare(String(y));
    return desc ? -c : c;
  };
}
export const pick = (obj, fields) => fields ? Object.fromEntries(fields.map(f => [f, obj[f]])) : obj;

// ---------- README "Data files" table: per-file property meanings ----------
let readmeDocs = null;
export async function fieldDocs() {
  if (readmeDocs) return readmeDocs;
  readmeDocs = { files: {}, common: { n: "name", q: "quantity used for sizing", s: "status, lowercase", k: "Natural Earth ADM0_A3 country key", c: "country" } };
  let md = "";
  try { md = await readFile(path.join(REPO, "README.md"), "utf8"); } catch { return readmeDocs; }
  const sec = md.split(/^## Data files.*$/m)[1];
  if (!sec) return readmeDocs;
  for (const line of sec.split(/^## /m)[0].split("\n")) {
    const cells = line.split(/(?<!\\)\|/).map(c => c.trim());
    if (cells.length < 4 || !cells[1] || cells[1] === "file" || /^-+$/.test(cells[1])) continue;
    const keys = parseKeyCell(cells[3]);
    for (let f of cells[1].split(/\s*[\/,]\s*/)) {
      f = f.trim();
      if (!f) continue;
      if (!/\./.test(f)) f += ".geojson";
      readmeDocs.files[f] = { count: cells[2], desc: cells[3].replace(/`/g, ""), keys };
    }
    // "mining_polygons_{w,c,e}.geojson" and "rail.geojson / roads.geojson" are good enough as-is
  }
  return readmeDocs;
}
function parseKeyCell(text) {
  const parts = text.split(/`([^`]+)`/);   // odd indices are code spans
  const out = {}, pending = [];
  for (let i = 1; i < parts.length; i += 2) {
    const key = parts[i];
    let desc = parts[i + 1] || "";
    if (!/^[\w.]+$/.test(key)) continue;
    const joiner = /^\s*\/\s*$/.test(desc);   // `q`/`pt` production … → both keys share the text
    desc = desc.replace(/^\s*[\/:,]?\s*/, "").replace(/^=(?=[a-z])/i, "").replace(/[\s,;(]+$/, "").replace(/\\\|/g, "|").trim();
    pending.push(key);
    if (joiner) continue;
    for (const k of pending) if (!out[k]) out[k] = desc;
    pending.length = 0;
  }
  return out;
}

// ---------- the layer catalogue ----------
const LIVE_FILES = { fires: "live/fires.json", events: "live/events.geojson", ships: "live/ships.geojson", aircraft: "live/aircraft.geojson",
  jets: "live/jets.geojson", airliners: "live/airliners.geojson", navy: "live/navy.geojson" };
// geojson files the page draws without a FILES entry (always-on base layers)
const BASE_FILES = { countries: ["countries.geojson", "Countries"], disputed: ["disputed.geojson", "Disputed areas"], disputed_lines: ["disputed_lines.geojson", "Disputed lines"],
  maritime: ["maritime.geojson", "Maritime boundaries"], ports_ne: ["ports.geojson", "Ports (Natural Earth, superseded by ports)"] };
export const TABLES = {
  country_totals: ["country_totals.json", "Per-country totals of operating sites"], domains: ["domains.json", "Country internet, health, chip and food figures"],
  country_energy: ["country_energy.json", "EIA country oil and gas balances"], labels: ["labels.json", "Place names: countries, cities, seas, regions"],
  scenarios_weekly: ["scenarios_weekly.json", "News scenarios (week, month, year)"], infra_meta: ["infra_meta.json", "Step 5 file metadata"],
  crit_commodities: ["crit_commodities.json", "Critical-mineral commodity counts"], air: ["air.json", "Air routes by country pair"],
  live_manifest: ["live/manifest.json", "Live feed manifest"], story: ["live/story.json", "Storyboard: conflict-report surges"]
};
// columns worth showing in tables, per layer (besides name, country, q)
export const KEY_FIELDS = {
  oilgas: ["f", "o", "pt"], coal: ["ct", "mt", "o"], iron: ["rs", "o"], crit: ["m", "dt"], foot: [], power: ["f", "mw"], steel: ["ir", "eq"], cement: ["ck"],
  chem: ["pp", "fs"], lng: ["ft", "o"], refinery: ["bd", "t", "o"], pipelines: ["f", "km", "cs"], shipping: ["t"], ports: ["hs", "lo", "cd"], chokepoints: ["lng", "pk"],
  bases: ["t", "op"], airports: ["t", "ia", "i", "rw", "mil"], dc: ["o", "ci"], cables: ["km", "rfs", "own"], landings: ["k"], cloud: ["p", "code", "city"],
  fabs: ["o", "nm", "cap300"], chipsites: ["o", "cat"], biolabs: ["city", "y"], lakes: ["km2", "res"], water_risk: ["ws", "dr", "gw"],
  fires: ["frp", "t", "ind", "cf"], events: ["e", "a", "e3", "d"], ships: ["cl", "sog", "len", "dest"], aircraft: ["ty", "alt", "gs"], jets: ["ty", "desc", "alt"],
  airliners: ["ty", "alt", "gs"], navy: ["h", "cl", "loc", "grp"]
};
// main "size" figure per layer for ranking and the country profile
export const SIZE_FIELD = { fabs: "cap300", fires: "frp", events: "e", ships: "len", cables: "km", landings: "k", lakes: "km2", airports: "rw" };
// identifier properties tried by `get`, in order
const ID_FIELDS = ["id", "wpi", "w", "osm", "i", "ia", "lo", "mmsi", "hex", "h", "code"];

// layers the sidebar doesn't name on their own
const OWN_LABEL = { oilgas_outlines: "Oil & gas field outlines", coal_boundaries: "Coal mine boundaries", landings: "Cable landing stations",
  water_risk: "Water risk by river basin (Aqueduct)", groundwater: "Groundwater recharge (WHYMAP)", saline_gw: "Saline groundwater", gw_mining: "Intensive or fossil-water well fields" };
// the records inside a table file
export function tableRecords(key, j) {
  if (Array.isArray(j)) return j;
  if (key === "scenarios_weekly") return Object.values(j.windows || {}).flat();
  if (key === "live_manifest") return Object.entries(j.feeds || {}).map(([k, v]) => ({ feed: k, ...v }));
  if (key === "story") return Object.values(j.windows || {}).flatMap(w => w.items || []);
  if (key === "air") return j.pairs || [];
  if (j.countries) return Object.values(j.countries);
  return Object.values(j).filter(v => v && typeof v === "object" && !Array.isArray(v));
}

let catalogue = null;
export function layerRegistry() {
  if (catalogue) return catalogue;
  const { FILES, GROUPS, Q, EXTRA, LIVE } = page, side = sidebarLabels();
  const groupOf = src => Object.keys(GROUPS).find(g => (GROUPS[g].data || []).includes(src));
  const reg = {};
  const add = (key, file, extra) => {
    const g = groupOf(key), sl = g && GROUPS[g].data.length === 1 && side[g];   // a sidebar label names a file only when it has one file
    reg[key] = { key, file, label: (sl && sl.label) || OWN_LABEL[key] || Q[key]?.short || EXTRA[key]?.label || key, item: Q[key]?.label || EXTRA[key]?.label || null,
      unit: Q[key]?.unit || null, group: g || null, note: sl?.note || null, ...extra };
  };
  for (const [k, f] of Object.entries(FILES)) add(k, f, { type: "static" });
  for (const k of LIVE) if (LIVE_FILES[k]) add(k, LIVE_FILES[k], { type: "live" });
  for (const [k, [f, label]] of Object.entries(BASE_FILES)) add(k, f, { type: "base", label });
  for (const [k, [f, label]] of Object.entries(TABLES)) reg[k] = { key: k, file: f, label, type: "table", kind: "table" };
  // friendly aliases: file stems (power, refineries, ports_wpi, chip_sites…) and the sidebar group keys
  const alias = {};
  for (const r of Object.values(reg)) { alias[r.key] = r.key; alias[path.basename(r.file).replace(/\.(geo)?json$/, "")] ??= r.key; }
  for (const [g, def] of Object.entries(GROUPS)) if (def.data?.length === 1 && reg[def.data[0]]) alias[g] ??= def.data[0];
  alias.pipes ??= "pipelines"; alias.lanes ??= "shipping"; alias.choke ??= "chokepoints"; alias.datacenters ??= "dc"; alias.refineries ??= "refinery";
  return (catalogue = { reg, alias });
}
export function resolveLayer(name, { tables = false } = {}) {
  const { reg, alias } = layerRegistry();
  const k = alias[name] || alias[norm(name)];
  if (!k || (!tables && reg[k].type === "table")) {
    const keys = Object.values(reg).filter(r => tables || r.type !== "table").map(r => r.key);
    throw new AtlasError(`unknown layer "${name}". Layers: ${keys.join(", ")}`, 2);
  }
  return reg[k];
}

// a layer as a FeatureCollection (mining_points and fires are columnar on disk)
export async function loadLayer(name) {
  const r = typeof name === "string" ? resolveLayer(name) : name;
  const j = await loadJSON(r.file, { optional: r.type === "live" });
  if (j == null) throw new AtlasError(`${r.key}: ${r.file} is not present${r.type === "live" ? " (live feeds are generated by pipeline/live/fetch_live.py)" : ""}`, 2);
  if (r.key === "foot" && Array.isArray(j)) {
    if (!j._fc) Object.defineProperty(j, "_fc", { value: { type: "FeatureCollection", features: j.map(a => ({ type: "Feature", geometry: { type: "Point", coordinates: [a[0], a[1]] }, properties: { q: a[2], s: "operating" } })) } });
    return j._fc;
  }
  if (j.cols && j.rows) {
    if (!j._fc) {
      const ix = Object.fromEntries(j.cols.map((c, i) => [c, i]));
      const feats = j.rows.map(row => ({ type: "Feature", geometry: { type: "Point", coordinates: [row[ix.lon], row[ix.lat]] },
        properties: Object.fromEntries(j.cols.filter(c => c !== "lon" && c !== "lat").map(c => [c, row[ix[c]]])) }));
      Object.defineProperty(j, "_fc", { value: { type: "FeatureCollection", updated: j.updated, features: feats } });
    }
    return j._fc;
  }
  if (!Array.isArray(j.features)) throw new AtlasError(`${r.key}: ${r.file} is not a FeatureCollection`, 1);
  return j;
}
const KIND = { Point: "point", MultiPoint: "point", LineString: "line", MultiLineString: "line", Polygon: "polygon", MultiPolygon: "polygon" };
export function fieldSummary(fc) {
  const seen = new Map();
  for (const f of fc.features) for (const [k, v] of Object.entries(f.properties || {})) {
    let s = seen.get(k);
    if (!s) seen.set(k, s = { key: k, n: 0, example: undefined, numeric: true, min: Infinity, max: -Infinity });
    if (v == null || v === "") continue;
    s.n++;
    if (s.example === undefined) s.example = v;
    if (isNum(v) && typeof v !== "string") { s.min = Math.min(s.min, v); s.max = Math.max(s.max, v); } else s.numeric = false;
  }
  return [...seen.values()].map(s => ({ key: s.key, count: s.n, example: s.example, ...(s.numeric && s.n ? { min: s.min, max: s.max } : {}) }));
}

// one catalogue row per layer/table; counts need each file loaded (skipped in remote mode unless counts=true)
export async function layerCatalogue({ counts = !dataSource().remote, tables = true } = {}) {
  const { reg } = layerRegistry();
  const rows = [];
  for (const r of Object.values(reg)) {
    if (r.type === "table" && !tables) continue;
    const row = { key: r.key, label: r.label, type: r.type, kind: r.kind || null, count: null, file: r.file, unit: r.unit };
    if (counts || r.type === "table") {
      try {
        if (r.type === "table") {
          const j = await loadJSON(r.file, { optional: true });
          if (j == null) row.missing = true; else row.count = tableRecords(r.key, j).length;
        } else {
          const fc = await loadLayer(r);
          row.count = fc.features.length;
          row.kind = KIND[fc.features[0]?.geometry?.type] || null;
        }
      } catch (e) { if (e.code === 2) row.missing = true; else throw e; }
    }
    if (row.missing && r.type === "live") continue;
    rows.push(row);
  }
  return rows;
}

// ---------- features as flat records ----------
export const nameOf = p => p.n ?? p.title ?? p.f ?? p.r ?? p.hex ?? (p.code ? `${p.p || ""} ${p.code}`.trim() : null) ?? p.id ?? (p.mmsi != null ? "MMSI " + p.mmsi : null);
// GEM rows carry their wiki URL in `w`; only Wikidata QIDs there make a usable id
export const idOf = (p, i) => { for (const f of ID_FIELDS) if (p[f] != null && p[f] !== "" && (f !== "w" || /^Q\d+$/.test(p[f]))) return String(p[f]); return "#" + i; };
export function record(layer, f, i) {
  const p = f.properties || {}, pt = repPoint(f.geometry);
  return { layer, id: idOf(p, i), name: nameOf(p), lon: pt ? +pt[0].toFixed(4) : null, lat: pt ? +pt[1].toFixed(4) : null, props: p, geometry: f.geometry, index: i };
}

// ---------- countries ----------
let countryIdx = null;
export async function countries() {
  if (countryIdx) return countryIdx;
  const [totals, labels, polys] = await Promise.all([loadJSON("country_totals.json"), loadJSON("labels.json", { optional: true }), loadJSON("countries.geojson", { optional: true })]);
  const list = Object.entries(totals).map(([k, t]) => ({ k, n: t.n }));
  const byK = Object.fromEntries(list.map(c => [c.k, c]));
  for (const l of labels || []) if (l.t === "c" && l.k && byK[l.k]) { byK[l.k].x = l.x; byK[l.k].y = l.y; if (norm(l.n) !== norm(byK[l.k].n)) byK[l.k].alt = l.n; }
  const geom = {};
  for (const f of polys?.features || []) if (f.properties?.k) geom[f.properties.k] = { g: f.geometry, bb: bboxOf(f.geometry) };
  return (countryIdx = { list, byK, geom });
}
const ALIASES = { usa: "USA", us: "USA", "united states": "USA", america: "USA", uk: "GBR", "great britain": "GBR", britain: "GBR", england: "GBR",
  russia: "RUS", "south korea": "KOR", korea: "KOR", "north korea": "PRK", iran: "IRN", syria: "SYR", taiwan: "TWN", vietnam: "VNM", laos: "LAO",
  "czech republic": "CZE", czechia: "CZE", drc: "COD", "dr congo": "COD", "congo-kinshasa": "COD", "ivory coast": "CIV", uae: "ARE", turkey: "TUR", turkiye: "TUR",
  bolivia: "BOL", venezuela: "VEN", tanzania: "TZA", moldova: "MDA", "south sudan": "SSD", palestine: "PSX", kosovo: "KOS", burma: "MMR" };
export async function resolveCountry(q) {
  const { list, byK } = await countries();
  const s = String(q ?? "").trim(), up = s.toUpperCase(), n = norm(s);
  if (!s) throw new AtlasError("missing country (ISO3 code or name)");
  if (byK[up]) return byK[up];
  if (ALIASES[n] && byK[ALIASES[n]]) return byK[ALIASES[n]];
  const exact = list.find(c => norm(c.n) === n || norm(c.alt) === n);
  if (exact) return exact;
  const starts = list.filter(c => norm(c.n).startsWith(n) || norm(c.alt).startsWith(n));
  if (starts.length === 1) return starts[0];
  const inc = list.filter(c => norm(c.n).includes(n) || norm(c.alt).includes(n));
  if (inc.length === 1 || starts.length > 1) return (starts.length ? starts : inc).sort((a, b) => a.n.length - b.n.length)[0];
  if (inc.length) return inc.sort((a, b) => a.n.length - b.n.length)[0];
  throw new AtlasError(`unknown country "${q}"`, 2);
}
// does feature p/geometry belong to country c? uses the feature's own key when it has one, else point-in-polygon
export async function countryTest(c) {
  const { geom } = await countries();
  const G = geom[c.k], names = new Set([norm(c.n), norm(c.alt)].filter(Boolean));
  return (p, g) => {
    for (const f of ["k", "cc"]) if (typeof p[f] === "string" && p[f].length === 3) return p[f] === c.k;
    if (typeof p.c === "string" && p.c.length > 3) { if (names.has(norm(p.c))) return true; }
    // pipelines list the countries they cross in `cs` (landings use `cs` for cable names, so only trust a hit)
    if (typeof p.cs === "string" && p.cs.split(/[,;|]\s*/).some(x => names.has(norm(x)))) return true;
    if (!G || !g || (g.type !== "Point" && typeof p.cs === "string")) return false;
    const pt = g.type === "Point" ? g.coordinates : repPoint(g);
    if (!pt) return false;
    const [w, s, e, n] = G.bb;
    return pt[0] >= w && pt[0] <= e && pt[1] >= s && pt[1] <= n && polygonContains(G.g, pt[0], pt[1]);
  };
}

// country totals with domains merged in, plus derived fields (fresh_km2, fdep), exactly as the page does
let merged = null;
export async function mergedTotals() {
  if (merged) return merged;
  const [totals, dom] = await Promise.all([loadJSON("country_totals.json"), loadJSON("domains.json", { optional: true })]);
  const T = structuredClone(totals);
  for (const c of Object.values(T)) c.fresh_km2 = (c.lake_km2 || 0) + (c.res_km2 || 0) || undefined;
  if (dom) {
    for (const [k, c] of Object.entries(dom.countries || {})) if (T[k]) for (const f of ["net", "bb", "srv", "beds", "docs", "chipX", "chipM", "cens", "minr"]) if (c[f] != null) T[k][f] = c[f];
    const F = dom.food || {}, sum = {};
    for (const it of ["wheat", "maize", "rice"]) {
      for (const [k, v] of Object.entries((F.prod || {})[it] || {})) (sum[k] = sum[k] || { p: 0, i: 0, x: 0 }).p += v;
      for (const [a, b, v] of (F.trade || {})[it] || []) { (sum[a] = sum[a] || { p: 0, i: 0, x: 0 }).x += v; (sum[b] = sum[b] || { p: 0, i: 0, x: 0 }).i += v; }
    }
    for (const [k, o] of Object.entries(sum)) { const use = o.p + o.i - o.x; if (T[k] && use > 0) T[k].fdep = Math.max(0.001, Math.min(1, o.i / use)); }
  }
  return (merged = T);
}

// for tests / debugging
export function _resetCaches() { cache.clear(); catalogue = null; countryIdx = null; merged = null; readmeDocs = null; }
