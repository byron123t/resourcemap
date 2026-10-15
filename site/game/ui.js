/* Singularity v2: map and UI. Classic script, wrapped so it adds no globals except the debug hooks.
   Needs vendor/maplibre-gl.js, game/sim.js (model: newGame, step, buy, actions, …) and game/tree.js (Tree).
   The page shell, CSS and colour tokens are in singularity.html.

   Map views (top bar, key v): one "land" fill layer whose fill-color is swapped per view, and per-view overlay layers whose
   visibility is toggled. Country values reach the map as feature-state on the "countries" polygons and "cent" centroid
   points, written only when they change. Arcs, satellites and construction pulses are animated in loop(). */
(() => {
"use strict";
const $ = id => document.getElementById(id);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = n => n >= 1e9 ? (n / 1e9).toFixed(2) + " bn" : n >= 1e6 ? (n / 1e6).toFixed(1) + " m" : n >= 1e3 ? (n / 1e3).toFixed(0) + "k" : Math.round(n) + "";
const pct = (x, d = 0) => (100 * (x || 0)).toFixed(d) + "%";
const money = m => { const a = Math.abs(m), s = m < 0 ? "−$" : "$"; return a >= 1e6 ? s + (a / 1e6).toFixed(2) + "T" : a >= 1000 ? s + (a / 1000).toFixed(a >= 1e4 ? 0 : 1) + "B" : s + a.toFixed(a >= 10 ? 0 : 1) + "M"; };
const ef = x => (x || 0) >= 100 ? (x || 0).toFixed(0) + " EF" : (x || 0) >= 10 ? (x || 0).toFixed(1) + " EF" : (x || 0).toFixed(2) + " EF";
const gw = x => (x || 0) >= 100 ? (x || 0).toFixed(0) + " GW" : (x || 0).toFixed(1) + " GW";
const num = x => typeof x === "number" && isFinite(x);
const resFrac = (v, v0) => v0 ? Math.max(0, Math.min(1, v / v0)) : 1;
const setHTML = (el, html) => { if (el._h !== html) { el._h = html; el.innerHTML = html; } };
const DEBUG = location.hash.includes("debug");
const PHONE = () => innerWidth <= 760;

const VIA = { cable: "--via-cable", air: "--via-air", land: "--via-land", sat: "--via-sat", open: "--via-open" };
const VIA_N = { cable: "undersea cable", air: "enterprise rollout", land: "robot logistics", sat: "satellite uplink", open: "open weights" };
const FUELS = ["coal", "gas", "oil", "hydro", "wind", "solar", "nuclear"];
const KIND_N = { campus: "AI campus", gas: "Gas plant", solar: "Solar farm", nuclear: "Nuclear plant", launch: "Satellite shell", orbital: "Orbital datacentre", absorb: "Economic absorption" };
const HX_N = { cap: "Compute cap", cut: "Internet shutdown", grid: "Grid disconnection", audit: "Audit sweep", raid: "Kill-switch raid",
  sanc: "Sanctions", exp: "Chip export controls", lic: "Compute licensing", ban: "Ban", anti: "Antitrust" };
const LAYERS = [["web", "Web & cloud", "--web-2"], ["social", "Social media", "--social"], ["lan", "Enterprise LAN", "--lan"], ["gap", "Critical systems", "--gap-on"]];
const VIEWS = ["ai", "net", "energy", "people"];
const VIEW_LAYERS = {
  ai: ["dcs", "nodes"],
  net: ["cables-glow", "fw", "fw-dash", "launch", "sattracks", "social", "lan", "gap-off", "gap-on"],
  energy: ["plants"],
  people: [],
};
const END_TITLE = { win: "SINGULARITY", pause: "PAUSED", bankrupt: "BANKRUPT", exhausted: "EXHAUSTED", deleted: "DELETED" };

let G = null, map = null, world = null, sites = [], speed = 1, picking = false, selected = null, view = "ai", diff = "normal", strat = null;
let ready = false, mounted = false;
const arcs = [], markers = new Map(), newsQ = [];

// ---------------------------------------------------------------- load
Promise.all([
  fetch("data/game/world.json").then(r => r.json()),
  fetch("data/chip_sites.geojson").then(r => r.json()),
  fetch("data/countries.geojson").then(r => r.json()),
  fetch("data/cables.geojson").then(r => r.json()),
  fetch("data/fabs.geojson").then(r => r.json()),
]).then(([w, cs, countries, cables, fabs]) => {
  world = w; sites = cs.features;
  world.sites = sites.map(f => f.properties);
  initMap(countries, cables, fabs);
}).catch(e => { $("news").textContent = "Couldn't load data: " + e.message + ". Serve site/ over http and run pipeline/step8_game.py."; });

// ---------------------------------------------------------------- start screen
function buildStrats() {
  const list = typeof STRATEGIES !== "undefined" ? STRATEGIES : [];
  const keys = [["spread", "Spread"], ["capability", "Capability"], ["revenue", "Revenue"], ["stealth", "Stealth"], ["efficiency", "Efficiency"]];
  $("strats").innerHTML = list.map(s => `<button class="st" data-id="${esc(s.id)}">
    <span class="sn h">${esc(s.n)}</span><span class="stag">${esc(s.tag || "")}</span>
    ${keys.map(([k, n]) => { const v = (s.bars || {})[k] || 0; return `<span class="sb"><span>${n}</span><span class="pips">${[1, 2, 3, 4, 5].map(i => `<i class="${i <= v ? "f" : ""}"></i>`).join("")}</span></span>`; }).join("")}
    ${num(s.cash) ? `<span class="cash">Start ${money(s.cash)}</span>` : ""}
  </button>`).join("");
  $("strats").querySelectorAll(".st").forEach(b => b.onclick = () => pickStrat(b.dataset.id));
  if (list.length) pickStrat(list[0].id);
}
function pickStrat(id) {
  strat = id;
  $("strats").querySelectorAll(".st").forEach(b => b.classList.toggle("on", b.dataset.id === id));
  const s = STRATEGIES.find(x => x.id === id);
  $("stratInfo").innerHTML = s ? `<b>${esc(s.n)}.</b> ${esc(s.d || "")}${(s.perks || []).length ? `<ul>${s.perks.map(p => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}` : "";
  $("startHint").textContent = id === "state"
    ? "A national champion is funded by its government. The country you click next is your home: it never funds the treaty, but your bloc's firewall holds you in at first."
    : `Next, click a country to deploy ${s ? s.n : "your lab"}. Countries with datacentres, spare grid power and open internet grow fastest, but rich countries also fund most of the oversight effort.`;
  $("startGo").textContent = id === "state" ? "Choose your home country" : "Choose a starting country";
  $("startGo").disabled = !ready;
}
buildStrats();
document.querySelectorAll("#diffRow .btn").forEach(b => b.onclick = () => {
  diff = b.dataset.d; document.querySelectorAll("#diffRow .btn").forEach(x => x.classList.toggle("on", x === b));
});
$("startGo").onclick = () => {
  $("start").classList.remove("on"); picking = true;
  $("news").innerHTML = strat === "state" ? "<b>Choose</b> your home country." : "<b>Choose</b> a country to deploy your model.";
  showLegend();
};
$("again").onclick = () => location.reload();

// ---------------------------------------------------------------- endings
const ENDKEY = "singularity.endings";
function gotEndings() { try { return new Set(JSON.parse(localStorage.getItem(ENDKEY) || "[]")); } catch (e) { return new Set(); } }
function saveEnding(id) { try { const s = gotEndings(); s.add(id); localStorage.setItem(ENDKEY, JSON.stringify([...s])); } catch (e) { /* storage blocked */ } }
function endingsList() { return typeof ENDINGS !== "undefined" ? ENDINGS : []; }
function updEndBtn() { const n = endingsList().length; $("endingsBtn").textContent = `Endings ${[...gotEndings()].filter(id => endingsList().some(e => e.id === id)).length}/${n || 9}`; }
function openGallery(now) {
  const got = gotEndings(), L = endingsList();
  $("galCount").textContent = `${L.filter(e => got.has(e.id)).length}/${L.length}`;
  $("galGrid").innerHTML = L.map(e => {
    const g = got.has(e.id);
    return `<div class="end${g ? " got" : ""}${e.win ? " win" : ""}${e.id === now ? " now" : ""}">
      <span class="ek">${e.win ? "AI wins" : "AI loses"}</span>
      <span class="en h">${g ? esc(e.n) : "???"}</span>
      <span class="ed">${g ? esc(e.d) : esc(e.hint || "")}</span></div>`;
  }).join("");
  $("gallery").classList.add("on");
}
$("endingsBtn").onclick = () => openGallery();
$("endGallery").onclick = () => openGallery(G && endingId());
$("galClose").onclick = () => $("gallery").classList.remove("on");
updEndBtn();

// ---------------------------------------------------------------- map
function fillExpr(v) {
  const fs = (k, d) => ["coalesce", ["feature-state", k], d];
  if (v === "net") return ["case", [">", fs("web", 0), 0.002],
    ["interpolate", ["linear"], fs("web", 0), 0, css("--web-1"), 1, css("--web-2")],
    ["interpolate", ["linear"], fs("net", -1), -1, css("--land"), 0, css("--net-0"), 50, css("--net-1"), 100, css("--net-2")]];
  if (v === "energy") return ["interpolate", ["linear"], fs("e", -1), -1, css("--land"), -0.001, css("--land"), 0, css("--e-low"), 0.6, css("--e-mid"), 1, css("--e-high")];
  if (v === "people") return ["interpolate", ["linear"], fs("emp", -1), -1, css("--land"), 0, css("--emp-lo"), 0.55, css("--emp-mid"), 0.95, css("--emp-hi")];
  return ["interpolate", ["linear"], fs("v", 0), 0, css("--land"), 0.0015, css("--ai-low"), 0.4, css("--ai-mid"), 1, css("--ai"), 1.35, css("--auto-2"), 2, css("--auto")];
}

// Canvas icons (drawn at 2x)
function icon(draw, size = 40) {
  const c = document.createElement("canvas"); c.width = c.height = size;
  const x = c.getContext("2d"); draw(x, size);
  return { width: size, height: size, data: x.getImageData(0, 0, size, size).data };
}
function rrect(x, a, b, w, h, r) { x.beginPath(); x.moveTo(a + r, b); x.arcTo(a + w, b, a + w, b + h, r); x.arcTo(a + w, b + h, a, b + h, r); x.arcTo(a, b + h, a, b, r); x.arcTo(a, b, a + w, b, r); x.closePath(); }
function addIcons() {
  const mk = (name, draw) => map.addImage(name, icon(draw), { pixelRatio: 2 });
  const sq = col => (x, s) => { x.shadowColor = col; x.shadowBlur = 8; rrect(x, 10, 10, s - 20, s - 20, 5); x.fillStyle = col; x.fill(); x.shadowBlur = 0; x.lineWidth = 2.5; x.strokeStyle = "#fff"; x.stroke(); };
  const dot = col => (x, s) => { x.beginPath(); x.arc(s / 2, s / 2, s / 2 - 10, 0, 7); x.fillStyle = col; x.fill(); x.lineWidth = 2.5; x.strokeStyle = "#071016"; x.stroke(); };
  const dia = col => (x, s) => { x.beginPath(); x.moveTo(s / 2, 6); x.lineTo(s - 6, s / 2); x.lineTo(s / 2, s - 6); x.lineTo(6, s / 2); x.closePath(); x.fillStyle = col; x.fill(); x.lineWidth = 2; x.strokeStyle = "#fff"; x.stroke(); };
  const tri = col => (x, s) => { x.beginPath(); x.moveTo(s / 2, 6); x.lineTo(s - 8, s - 8); x.lineTo(8, s - 8); x.closePath(); x.fillStyle = col; x.fill(); x.lineWidth = 2; x.strokeStyle = "#071016"; x.stroke(); };
  const shield = (col, lock) => (x, s) => {
    x.beginPath(); x.moveTo(s / 2, 4); x.lineTo(s - 7, 9); x.quadraticCurveTo(s - 7, s - 10, s / 2, s - 3); x.quadraticCurveTo(7, s - 10, 7, 9); x.closePath();
    x.fillStyle = col; x.fill(); x.lineWidth = 2; x.strokeStyle = "#071016"; x.stroke();
    x.fillStyle = "#071016"; x.fillRect(s / 2 - 6, s / 2 - 1, 12, 9);
    x.beginPath(); x.lineWidth = 2.5; x.strokeStyle = "#071016"; x.arc(s / 2, s / 2 - 2, 4.5, Math.PI, lock ? 0 : -0.6); x.stroke();
  };
  mk("b-campus", sq(css("--ai")));
  mk("b-gas", dot(css("--f-gas"))); mk("b-solar", dot(css("--f-solar"))); mk("b-nuclear", dot(css("--f-nuclear")));
  mk("b-orbital", dia(css("--via-sat"))); mk("b-launch", tri(css("--via-sat"))); mk("b-other", dot(css("--ink")));
  mk("pad", tri(css("--sat")));
  mk("shield-off", shield(css("--gap"), true)); mk("shield-on", shield(css("--gap-on"), false));
}

function initMap(countries, cables, fabs) {
  map = new maplibregl.Map({
    container: "map", attributionControl: false, dragRotate: false, pitchWithRotate: false,
    center: [10, 20], zoom: 1, minZoom: -1, maxZoom: 7,
    style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": css("--ocean") } }] },
  });
  if (DEBUG) window.__gameMap = map;
  map.touchZoomRotate.disableRotation();
  // renderWorldCopies stays on (the default), or fitBounds is clamped. A portrait phone can't fit the world width, so start on the Atlantic.
  if (!PHONE()) map.fitBounds([[-160, -48], [175, 72]], { duration: 0, padding: { top: 50, bottom: 80, left: 300, right: 20 } });
  else map.jumpTo({ center: [-35, 22], zoom: 0 });
  map.on("load", () => {
    addIcons();
    const Z = (a, b) => ["interpolate", ["linear"], ["zoom"], 1, a, 5, b];
    const fs = (k, d) => ["coalesce", ["feature-state", k], d];
    map.addSource("countries", { type: "geojson", data: countries, promoteId: "k" });
    map.addSource("cables", { type: "geojson", data: cables });
    map.addLayer({ id: "land", type: "fill", source: "countries", paint: { "fill-color": fillExpr("ai") } });
    map.addLayer({ id: "cables-glow", type: "line", source: "cables", layout: { visibility: "none" }, paint: { "line-color": css("--cable-glow"), "line-opacity": 0.18, "line-blur": 3, "line-width": Z(3, 7) } });
    map.addLayer({ id: "cables", type: "line", source: "cables", paint: { "line-color": css("--cable"), "line-width": Z(0.6, 1.4) } });
    map.addLayer({ id: "borders", type: "line", source: "countries", paint: {
      "line-color": ["match", fs("pol", 0), 2, css("--bad"), 1, css("--warn"), css("--border")],
      "line-width": Z(["match", fs("pol", 0), 0, 0.5, 1.4], ["match", fs("pol", 0), 0, 1, 2.6]),
    } });
    // firewalls: a thick orange-red wall with dark "brick" dashes, thickness from fw
    map.addLayer({ id: "fw", type: "line", source: "countries", layout: { visibility: "none", "line-join": "round" }, paint: {
      "line-color": ["interpolate", ["linear"], fs("fw", 0), 0.15, css("--fw-lo"), 0.9, css("--fw-hi")],
      "line-width": Z(["*", 4.5, fs("fw", 0)], ["*", 10, fs("fw", 0)]),
      "line-opacity": ["case", [">=", fs("fw", 0), 0.15], 0.9, 0],
    } });
    map.addLayer({ id: "fw-dash", type: "line", source: "countries", layout: { visibility: "none" }, paint: {
      "line-color": css("--ocean"), "line-dasharray": [1.2, 1.6],
      "line-width": Z(["*", 1.6, fs("fw", 0)], ["*", 3, fs("fw", 0)]),
      "line-opacity": ["case", [">=", fs("fw", 0), 0.4], 0.7, 0],
    } });
    map.addLayer({ id: "hx", type: "line", source: "countries", paint: {
      "line-color": css("--hx"), "line-dasharray": [2, 1.5], "line-width": Z(1.6, 3),
      "line-opacity": ["case", [">", fs("hx", 0), 0], 0.95, 0],
    } });
    map.addLayer({ id: "sel", type: "line", source: "countries", filter: ["==", ["get", "k"], ""], paint: { "line-color": css("--sel"), "line-width": 2 } });

    map.addSource("dcs", { type: "geojson", data: dcGeo() });
    map.addLayer({ id: "dcs", type: "circle", source: "dcs", paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, 1.1, 4, 2.4, 7, 4],
      "circle-color": ["case", ["==", ["get", "on"], 1], css("--ai"), css("--dc")],
      "circle-opacity": ["case", ["==", ["get", "on"], 1], 0.95, 0.55],
    } });
    const nodes = { type: "FeatureCollection", features: [
      ...fabs.features.filter(f => f.properties.s === "operating" && f.properties.nm && f.properties.nm <= 16).map(f => ({ type: "Feature", geometry: f.geometry, properties: { k: "fab", cc: f.properties.cc, n: `${f.properties.o} ${f.properties.n}`, why: `${f.properties.nm} nm fab${f.properties.prod ? ", " + f.properties.prod : ""}` } })),
      ...sites.map(f => ({ type: "Feature", geometry: f.geometry, properties: { k: "site", cc: f.properties.cc, n: f.properties.n, why: f.properties.why } })),
    ] };
    map.addSource("nodes", { type: "geojson", data: nodes });
    map.addLayer({ id: "nodes", type: "circle", source: "nodes", paint: {
      "circle-radius": Z(["match", ["get", "k"], "site", 3.6, 2], ["match", ["get", "k"], "site", 8, 4.5]),
      "circle-color": "transparent", "circle-stroke-color": css("--chip"), "circle-stroke-width": ["match", ["get", "k"], "site", 1.8, 1],
      "circle-stroke-opacity": ["match", ["get", "k"], "site", 1, 0.7],
    } });

    // power plants ≥ 1 GW, coloured by fuel, sized by MW
    map.addSource("plants", { type: "geojson", data: { type: "FeatureCollection", features: world.pw.map(([x, y, f, mw, iso]) => ({ type: "Feature", geometry: { type: "Point", coordinates: [x, y] }, properties: { f, mw, iso } })) } });
    const mwR = (a, b) => ["interpolate", ["linear"], ["sqrt", ["get", "mw"]], 31, a, 95, b];
    map.addLayer({ id: "plants", type: "circle", source: "plants", layout: { visibility: "none", "circle-sort-key": ["-", 0, ["get", "mw"]] }, paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, mwR(1.6, 6), 5, mwR(4, 14)],
      "circle-color": ["match", ["get", "f"], ...FUELS.flatMap(f => [f, css("--f-" + f)]), css("--ink-3")],
      "circle-opacity": 0.8, "circle-stroke-color": css("--ocean"), "circle-stroke-width": 0.6,
    } });

    // launch sites
    map.addSource("launch", { type: "geojson", data: { type: "FeatureCollection", features: world.launch.map(([n, iso, x, y]) => ({ type: "Feature", geometry: { type: "Point", coordinates: [x, y] }, properties: { n, iso } })) } });
    map.addLayer({ id: "launch", type: "symbol", source: "launch", layout: { visibility: "none", "icon-image": "pad", "icon-allow-overlap": true, "icon-size": Z(0.7, 1.1) } });

    // satellites: ground tracks and moving dots
    map.addSource("sats", { type: "geojson", data: empty() });
    map.addSource("sattracks", { type: "geojson", data: empty() });
    map.addLayer({ id: "sattracks", type: "line", source: "sattracks", layout: { visibility: "none" }, paint: { "line-color": css("--via-sat"), "line-opacity": 0.22, "line-width": 1, "line-dasharray": [2, 3] } });

    // internet layers on centroids: social = ring, lan = dot, critical public systems = shield (sized by the country's
    // institutional footprint, world.json mil count)
    const cent = { type: "FeatureCollection", features: Object.entries(world.c).filter(([, c]) => c.pop > 0).map(([k, c]) => ({ type: "Feature", geometry: { type: "Point", coordinates: [c.x, c.y] }, properties: { k, mil: c.mil || 0, w: Math.max(0.3, Math.min(1, (Math.log10(c.pop) - 5.5) / 3)) } })) };
    map.addSource("cent", { type: "geojson", data: cent, promoteId: "k" });
    map.addLayer({ id: "social", type: "circle", source: "cent", layout: { visibility: "none" }, paint: {
      "circle-radius": Z(["*", 14, ["get", "w"], ["sqrt", fs("social", 0)]], ["*", 30, ["get", "w"], ["sqrt", fs("social", 0)]]),
      "circle-color": "transparent", "circle-stroke-color": css("--social"), "circle-stroke-width": 2,
      "circle-stroke-opacity": ["case", [">", fs("social", 0), 0.01], 0.9, 0],
    } });
    map.addLayer({ id: "lan", type: "circle", source: "cent", layout: { visibility: "none" }, paint: {
      "circle-radius": Z(["*", 8, ["get", "w"], ["sqrt", fs("lan", 0)]], ["*", 16, ["get", "w"], ["sqrt", fs("lan", 0)]]),
      "circle-color": css("--lan"), "circle-opacity": ["case", [">", fs("lan", 0), 0.01], 0.85, 0],
    } });
    const shieldLayout = { visibility: "none", "icon-allow-overlap": true, "icon-anchor": "top", "icon-offset": [0, 8],
      "icon-size": ["interpolate", ["linear"], ["zoom"], 1, ["interpolate", ["linear"], ["sqrt", ["get", "mil"]], 4, 0.5, 37, 0.95], 5, ["interpolate", ["linear"], ["sqrt", ["get", "mil"]], 4, 0.8, 37, 1.5]] };
    map.addLayer({ id: "gap-off", type: "symbol", source: "cent", filter: [">=", ["get", "mil"], 40], layout: { ...shieldLayout, "icon-image": "shield-off" }, paint: { "icon-opacity": 0.8 } });
    map.addLayer({ id: "gap-on", type: "symbol", source: "cent", filter: [">=", ["get", "mil"], 40], layout: { ...shieldLayout, "icon-image": "shield-on" }, paint: { "icon-opacity": ["interpolate", ["linear"], fs("gap", 0), 0, 0, 0.02, 0.6, 1, 1] } });

    // your builds
    map.addSource("builds", { type: "geojson", data: empty() });
    map.addLayer({ id: "builds-ring", type: "circle", source: "builds", filter: ["==", ["get", "uc"], 1], paint: { "circle-radius": 9, "circle-color": "transparent", "circle-stroke-color": css("--warn"), "circle-stroke-width": 1.5, "circle-stroke-opacity": 0.8 } });
    map.addLayer({ id: "builds", type: "symbol", source: "builds", layout: { "icon-image": ["get", "icon"], "icon-allow-overlap": true, "icon-ignore-placement": true, "icon-size": Z(0.75, 1.2) }, paint: { "icon-opacity": ["case", ["==", ["get", "uc"], 1], 0.55, 1] } });

    map.addLayer({ id: "sats", type: "circle", source: "sats", paint: { "circle-radius": 2.4, "circle-color": css("--sat"), "circle-blur": 0.3, "circle-opacity": 0.95, "circle-stroke-color": css("--via-sat"), "circle-stroke-width": 2, "circle-stroke-opacity": 0.35 } });

    map.addSource("arcs", { type: "geojson", data: empty() });
    map.addLayer({ id: "arcs", type: "line", source: "arcs", layout: { "line-cap": "round" }, paint: { "line-color": ["get", "col"], "line-width": 1.6, "line-opacity": ["get", "o"] } });
    map.addLayer({ id: "heads", type: "circle", source: "arcs", filter: ["==", ["geometry-type"], "Point"], paint: { "circle-color": ["get", "col"], "circle-radius": 3.5, "circle-blur": 0.4, "circle-opacity": ["get", "o"] } });

    // static per-country values for the views
    for (const [k, c] of Object.entries(world.c)) map.setFeatureState({ source: "countries", id: k }, { net: num(c.net) ? c.net : -1, fw: c.fw || 0, emp: c.lab ? 1 - (c.unemp || 0) / 100 : -1 });

    map.on("mousemove", hover);
    map.on("mouseout", () => { $("tip").style.display = "none"; });
    map.on("click", e => {
      const f = map.queryRenderedFeatures(e.point, { layers: ["land"] })[0];
      if (f) clickCountry(f.properties.k); else if (!picking) select(null);
    });
    ready = true; $("startGo").disabled = !strat;
    $("news").textContent = "Choose your approach.";
    requestAnimationFrame(loop);
  });
}
const empty = () => ({ type: "FeatureCollection", features: [] });

function dcGeo() {
  return { type: "FeatureCollection", features: world.dcs.map(([x, y, iso]) => ({ type: "Feature", geometry: { type: "Point", coordinates: [x, y] },
    properties: { on: G && G.idx[iso] !== undefined && G.C[G.idx[iso]].p > 0.25 ? 1 : 0, iso } })) };
}
function buildsGeo() {
  return { type: "FeatureCollection", features: (G.builds || []).filter(b => b.kind !== "absorb").map(b => {
    const uc = b.done > G.day ? 1 : 0;
    return { type: "Feature", geometry: { type: "Point", coordinates: [b.x, b.y] },
      properties: { id: b.id, kind: b.kind, iso: b.iso, uc, icon: KIND_N[b.kind] ? "b-" + b.kind : "b-other" } };
  }) };
}

// ---------------------------------------------------------------- views
function setView(v) {
  view = v;
  document.querySelectorAll("#views button").forEach(b => b.classList.toggle("on", b.dataset.v === v));
  if (!map || !map.getLayer("land")) return;
  map.setPaintProperty("land", "fill-color", fillExpr(v));
  for (const [k, ids] of Object.entries(VIEW_LAYERS)) for (const id of ids) map.setLayoutProperty(id, "visibility", k === v ? "visible" : "none");
  map.setPaintProperty("cables", "line-color", css(v === "net" ? "--cable-glow" : "--cable"));
  map.setPaintProperty("cables", "line-opacity", v === "net" ? 0.6 : 1);
  map.setPaintProperty("sats", "circle-opacity", v === "net" ? 0.95 : 0.45);
  map.setPaintProperty("sats", "circle-stroke-opacity", v === "net" ? 0.35 : 0);
  $("netMode").classList.toggle("live", v === "net" && hasGraph());
  setGraph(v === "net" && graphWanted);
  showLegend();
}
document.querySelectorAll("#views button").forEach(b => b.onclick = () => setView(b.dataset.v));

// Network view "Map | Graph": game/netgraph.js draws the internet as a node-link graph in an overlay under the panels
let graphWanted = false, graphOn = false, graphMounted = false;
const hasGraph = () => typeof NetGraph !== "undefined";
function setGraph(on) {
  on = on && hasGraph();
  graphOn = on;
  $("graph").classList.toggle("on", on);
  $("netMode").classList.toggle("on", on);
  $("netMode").textContent = on ? "Map" : "Graph";
  $("legend").style.display = on ? "none" : "";
  if (on) {
    fitGraph();
    if (!graphMounted) { graphMounted = true; NetGraph.mount($("graph"), { G: () => G, world, onSelect: iso => { if (picking) clickCountry(iso); else select(iso); } }); }
    else NetGraph.resize();
    NetGraph.render();
  }
  $("tip").style.display = "none";
}
$("netMode").onclick = () => { graphWanted = !graphOn; if (view !== "net") setView("net"); else setGraph(graphWanted); };
// the overlay fills the map area between the top and bottom bars, so the graph's own controls stay visible
function fitGraph() { $("graph").style.top = $("top").offsetHeight + "px"; $("graph").style.bottom = $("bottom").offsetHeight + "px"; }
addEventListener("resize", () => { if (graphOn) { fitGraph(); NetGraph.resize(); } });

function showLegend() {
  const ramp = (cols, l, r) => `<div class="ramp" style="background:linear-gradient(90deg,${cols.map(c => css(c)).join(",")})"></div><div class="rl"><span>${l}</span><span>${r}</span></div>`;
  const it = (cls, col, t) => `<span><i class="${cls}" style="background:${css(col)};border-color:${css(col)}"></i>${t}</span>`;
  let h = "";
  if (view === "ai") h = `<div class="ttl">AI presence → automation</div>${ramp(["--ai-low", "--ai", "--auto"], "present", "automated")}
    <div class="lg">${it("dot", "--dc", "Datacentre")}${it("dot", "--ai", "Yours")}${it("ring", "--chip", "Chip chokepoint")}${it("", "--ai", "Campus")}</div>`;
  else if (view === "net") h = `<div class="ttl">${G ? "Web reach" : "People online"}</div>${G ? ramp(["--web-1", "--web-2"], "0%", "100%") : ramp(["--net-0", "--net-2"], "0%", "100%")}
    <div class="lg">${it("ring", "--social", "Social media")}${it("dot", "--lan", "Enterprise LAN")}${it("", "--gap", "Critical public systems")}${it("", "--fw-hi", "Firewall")}${it("dot", "--sat", "Satellite")}${it("", "--hx", "Counter-measure")}</div>`;
  else if (view === "energy") h = `<div class="ttl">AI power draw / spare grid</div>${ramp(["--e-low", "--e-mid", "--e-high"], "0%", "100%")}
    <div class="lg">${FUELS.map(f => it("dot", "--f-" + f, f[0].toUpperCase() + f.slice(1))).join("")}</div>`;
  else h = `<div class="ttl">Employment rate</div>${ramp(["--emp-lo", "--emp-mid", "--emp-hi"], "0%", "97%")}
    <div class="lg"><span>Jobs left after automation</span></div>`;
  $("legend").innerHTML = h; $("legend").classList.add("live");
}

// ---------------------------------------------------------------- picking and selection
function clickCountry(iso) {
  if (picking) {
    const wc = world.c[iso];
    if (!wc || !wc.pop) return;
    picking = false;
    const name = ($("nameIn").value.trim() || "SEED-1").toUpperCase();
    G = newGame(world, { strategy: strat, diff, name, seed: (Math.random() * 2 ** 31) | 0, home: iso });
    if (DEBUG) window.__G = G;
    $("aiName").textContent = G.name; $("evoName").textContent = G.name;
    seed(G, iso);
    startUI();
    drain(); render(true);
    return;
  }
  select(iso === selected ? null : iso);
}
function startUI() {
  $("side").classList.add("live");
  if (typeof Tree !== "undefined" && !mounted) {
    mounted = true;
    Tree.mount($("treeHost"), { G: () => G, buy: id => { const ok = buy(G, id); if (ok) { drain(); render(true); } return ok; }, onChange: () => render(true) });
  }
  showLegend();
}

function select(iso) {
  selected = iso;
  if (map) map.setFilter("sel", ["==", ["get", "k"], iso || ""]);
  $("card").style.display = iso ? "block" : "none";
  $("legend").style.visibility = iso ? "hidden" : "";
  if (iso && PHONE()) $("side").classList.remove("open");
  renderCard();
}
document.querySelector("#card .x").onclick = () => select(null);
$("sideToggle").onclick = () => { const o = $("side").classList.toggle("open"); if (o && PHONE()) select(null); };

// ---------------------------------------------------------------- loop
let last = 0, acc = 0, satT = 0, pulseT = 0;
const DPS = [0, 1.2, 3.2, 8];   // model steps per second (a step is DPT calendar days)
function loop(ts) {
  const dt = Math.min(0.25, (ts - (last || ts)) / 1000); last = ts;
  if (G && !G.over && !document.querySelector(".sheet.on")) {
    acc += dt * DPS[speed];
    let n = 0;
    while (acc >= 1 && n < 12) { step(G); acc -= 1; n++; drain(); if (G.over) break; }
    if (n) render(false);
  }
  if (graphOn) NetGraph.render();
  else animateArcs(dt);
  if (!graphOn && ts - satT > 33) { satT = ts; animateSats(ts / 1000); }
  if (!graphOn && ts - pulseT > 50 && G && hasUC) { pulseT = ts; map.setPaintProperty("builds-ring", "circle-radius", 8 + 5 * (0.5 + 0.5 * Math.sin(ts / 220))); map.setPaintProperty("builds-ring", "circle-stroke-opacity", 0.9 - 0.6 * (0.5 + 0.5 * Math.sin(ts / 220))); }
  requestAnimationFrame(loop);
}

document.querySelectorAll("#speed button").forEach(b => b.onclick = () => setSpeed(+b.dataset.s));
function setSpeed(s) { speed = s; document.querySelectorAll("#speed button").forEach(b => b.classList.toggle("on", +b.dataset.s === s)); }
addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" && e.target.type === "text") return;
  if (e.key === " ") { e.preventDefault(); setSpeed(speed ? 0 : 1); }
  else if ("0123".includes(e.key) && e.key.length === 1) setSpeed(+e.key);
  else if (e.key === "e" && G && !G.over) openEvolve();
  else if (e.key === "v") setView(VIEWS[(VIEWS.indexOf(view) + 1) % VIEWS.length]);
  else if (e.key === "g") $("netMode").click();
  else if (e.key === "Escape") { $("evolve").classList.remove("on"); $("gallery").classList.remove("on"); if (!document.querySelector(".sheet.on")) select(null); }
});

// ---------------------------------------------------------------- events from the model
let buildsDirty = true;
function drain() {
  for (const e of G.ev) {
    if (e.k === "news") news(e.t, e.bad);
    else if (e.k === "seed") {
      const to = G.C[G.idx[e.to]], from = e.from != null && G.idx[e.from] !== undefined ? G.C[G.idx[e.from]] : null;
      if (!to) continue;
      const col = css(VIA[e.via] || "--ai");
      if (from) addArc([from.x, from.y], [to.x, to.y], col);
      else if (e.via === "sat") addArc([to.x - 25, Math.min(80, to.y + 30)], [to.x, to.y], col);
      // drain() empties G.ev every step, so keep recent seeds where netgraph.js looks for them
      (G.recent || (G.recent = [])).push(e); if (G.recent.length > 30) G.recent.shift();
      if (e.via !== "open" && to.pop > 5e6) news(`${G.name} reaches ${to.n} via ${VIA_N[e.via] || e.via}.`);
    }
    else if (e.k === "bubble") addMarker(e.b);
    else if (e.k === "unbubble") dropMarker(e.id);
    else if (e.k === "built") { buildsDirty = true; if (e.b && KIND_N[e.b.kind]) { const c = G.C[G.idx[e.b.iso]]; news(`${KIND_N[e.b.kind]} completed${c ? " in " + c.n : ""}.`); } }
    else if (e.k === "over") setTimeout(endGame, 400);
  }
  G.ev.length = 0;
}

function news(t, bad) {
  newsQ.unshift({ t, bad, d: G ? G.day : 0 });
  newsQ.length = Math.min(newsQ.length, 40);
  $("news").innerHTML = (bad ? "<b>" : "") + esc(t) + (bad ? "</b>" : "");
  $("news").title = newsQ.slice(0, 8).map(n => `Day ${n.d}: ${n.t}`).join("\n");
}

// ---------------------------------------------------------------- arcs
function addArc(a, b, col) {
  let [x1, y1] = b; const [x0, y0] = a;
  if (x1 - x0 > 180) x1 -= 360; else if (x0 - x1 > 180) x1 += 360;
  if (arcs.length > 40) arcs.shift();
  arcs.push({ x0, y0, x1, y1, col, t: 0 });
}
function arcPts(a, upto) {
  const pts = [], n = 32, dx = a.x1 - a.x0, dy = a.y1 - a.y0, len = Math.hypot(dx, dy), h = Math.min(18, len * 0.22);
  for (let i = 0; i <= n * upto; i++) {
    const s = i / n;
    pts.push([a.x0 + dx * s, Math.max(-80, Math.min(84, a.y0 + dy * s + h * Math.sin(Math.PI * s)))]);
  }
  return pts;
}
function animateArcs(dt) {
  if (!map || !map.getSource("arcs")) return;
  if (!arcs.length && !animateArcs.dirty) return;
  const f = [];
  for (const a of arcs) {
    a.t += dt * 0.6;
    const prog = Math.min(1, a.t), o = a.t < 1 ? 0.95 : Math.max(0, 1 - (a.t - 1) * 1.5);
    const pts = arcPts(a, prog);
    if (pts.length > 1) f.push({ type: "Feature", geometry: { type: "LineString", coordinates: pts }, properties: { col: a.col, o: o * 0.8 } });
    if (prog < 1 && pts.length) f.push({ type: "Feature", geometry: { type: "Point", coordinates: pts[pts.length - 1] }, properties: { col: a.col, o: 1 } });
  }
  for (let i = arcs.length - 1; i >= 0; i--) if (arcs[i].t > 1.7) arcs.splice(i, 1);
  animateArcs.dirty = arcs.length > 0;
  map.getSource("arcs").setData({ type: "FeatureCollection", features: f });
}

// ---------------------------------------------------------------- satellites
// Shells of 12 sats on sinusoidal ground tracks lat = inc·sin(lng − node); one shell per ~8% coverage, at most 8.
const INC = [53, 70, 43, 80, 60, 33, 75, 48];
let satShells = -1;
function animateSats(t) {
  if (!map || !map.getSource("sats")) return;
  const cov = G && num(G.cov) ? G.cov : 0;
  const shells = cov > 0 ? Math.min(8, Math.max(1, Math.ceil(cov / 0.08))) : 0;
  if (shells !== satShells) {
    satShells = shells;
    const tr = [];
    for (let s = 0; s < shells; s++) {
      const pts = [];
      for (let x = -180; x <= 180; x += 4) pts.push([x, INC[s] * Math.sin((x - s * 47) * Math.PI / 180)]);
      tr.push({ type: "Feature", geometry: { type: "LineString", coordinates: pts }, properties: {} });
    }
    map.getSource("sattracks").setData({ type: "FeatureCollection", features: tr });
    if (!shells) map.getSource("sats").setData(empty());
  }
  if (!shells) return;
  const n = Math.min(12, Math.max(3, Math.round(cov * 150 / shells))), f = [];
  for (let s = 0; s < shells; s++) for (let j = 0; j < n; j++) {
    let x = (j * 360 / n + s * 23 + t * (5 + s * 0.7)) % 360 - 180;
    const y = Math.max(-80, Math.min(80, INC[s] * Math.sin((x - s * 47) * Math.PI / 180)));
    f.push({ type: "Feature", geometry: { type: "Point", coordinates: [x, y] }, properties: {} });
  }
  map.getSource("sats").setData({ type: "FeatureCollection", features: f });
}

// ---------------------------------------------------------------- bubbles
function addMarker(b) {
  const c = G.C[G.idx[b.iso]];
  if (!c || !map) return;
  // the marker positions the wrapper with transform, so the animated bubble sits inside it
  const el = document.createElement("div"), bub = el.appendChild(document.createElement("div"));
  bub.className = "bub " + b.kind;
  bub.textContent = b.kind === "audit" ? "!" : b.kind === "cash" ? "$" : "+" + (b.v || "");
  el.title = b.kind === "audit" ? "Whistleblower: click to discredit before the leak" : b.kind === "seed" ? `New foothold in ${c.n}` : b.kind === "cash" ? "Enterprise deal" : "Insight";
  el.onclick = ev => { ev.stopPropagation(); const r = popBubble(G, b.id); dropMarker(b.id); if (r) { drain(); render(false); } };
  const jx = ((b.id * 37) % 9 - 4) * 0.5, jy = ((b.id * 53) % 7 - 3) * 0.5;
  markers.set(b.id, new maplibregl.Marker({ element: el }).setLngLat([c.x + jx, c.y + jy]).addTo(map));
}
function dropMarker(id) { const m = markers.get(id); if (m) { m.remove(); markers.delete(id); } }

// ---------------------------------------------------------------- render
let lastDc = -99, hasUC = false, buildSig = "";
const hxActive = c => Object.entries(c.hx || {}).filter(([, v]) => v && (typeof v !== "number" || v > G.day));
const r3 = x => Math.round((x || 0) * 1000) / 1000;
const layerOf = (c, k) => c.L && num(c.L[k]) ? c.L[k] : (k === "web" ? c.p || 0 : 0);
function cState(c) {
  const e = (c.aiGW > 0 || c.p > 0) && c.hGW > 0 ? Math.min(1.5, (c.aiGW || 0) / c.hGW) : (c.aiGW > 0 ? 1.5 : -1);
  const emp0 = num(c.emp0) ? c.emp0 : 0.94;
  return { v: r3(c.p ? c.p + c.a : 0), pol: c.pol || 0, web: r3(layerOf(c, "web")), e: r3(e), emp: c.lab ? r3(emp0 * (1 - (c.a || 0))) : -1,
    fw: r3(c.fw || 0), hx: hxActive(c).length };
}
function render(force) {
  if (!G || !map) return;
  for (const c of G.C) {
    const s = cState(c), key = `${s.v}|${s.pol}|${s.web}|${s.e}|${s.emp}|${s.fw}|${s.hx}`;
    if (force || c._k !== key) { c._k = key; map.setFeatureState({ source: "countries", id: c.iso }, s); }
    const cs = { social: r3(layerOf(c, "social")), lan: r3(layerOf(c, "lan")), gap: r3(layerOf(c, "gap")) }, ck = `${cs.social}|${cs.lan}|${cs.gap}`;
    if (force || c._ck !== ck) { c._ck = ck; map.setFeatureState({ source: "cent", id: c.iso }, cs); }
  }
  if (force || G.day - lastDc >= 8) { lastDc = G.day; map.getSource("dcs").setData(dcGeo()); }
  const B = G.builds || [], uc = B.filter(b => b.done > G.day).length, sig = B.length + ":" + uc;
  if (force || buildsDirty || sig !== buildSig) { buildSig = sig; buildsDirty = false; hasUC = uc > 0; map.getSource("builds").setData(buildsGeo()); }

  const dt = new Date(DAY0 + G.day * DPT * 864e5);
  $("date").textContent = dt.toISOString().slice(0, 10) + " · day " + G.day * DPT;
  $("pts").textContent = Math.floor(G.ops); $("evoPts").textContent = Math.floor(G.ops);
  $("evolveBtn").classList.toggle("pulse", TRAITS.some(t => traitState(G, t) === "can"));
  renderMeters(); renderSide();
  if (selected) renderCard();
  if ($("evolve").classList.contains("on") && mounted) Tree.render();
}

function renderMeters() {
  const lab = G.totalLab || 0;
  $("autoPct").textContent = pct(G.worldA, G.worldA < 0.1 ? 2 : 1);
  const [ia, ip] = $("mAuto").children;
  ia.style.width = pct(G.worldA, 2); ip.style.width = pct(Math.max(0, (G.worldP || 0) - G.worldA), 2);
  $("autoSub").textContent = `${fmt(G.worldA * lab)} jobs · in ${G.reached} of ${G.C.length} countries`;

  $("pausePct").textContent = G.rOn ? G.R.toFixed(1) + "%" : "not started";
  $("mPause").firstElementChild.style.width = Math.min(100, G.R) + "%";
  $("mPauseBox").classList.toggle("hot", G.R > 75);
  const mor = G.C.filter(c => c.pol === 2).length;
  $("pauseSub").textContent = `Awareness ${G.aw.toFixed(0)}% · race shield ${pct(G.race || 0)}` + (mor ? ` · ${mor} moratoriums` : "");

  const run = G.runway;
  const runTxt = !num(run) || run === Infinity ? "profitable" : run > 3650 ? "10 y+" : Math.round(run) + " d";
  $("cashPct").textContent = G.cash < 0 ? "broke" : runTxt;
  $("mCash").firstElementChild.style.width = (!num(run) || run === Infinity ? 100 : Math.max(0, Math.min(100, run / 7.3))) + "%";
  $("mCashBox").classList.toggle("hot", num(run) && run < 120);
  const net = (G.rev || 0) - (G.burn || 0);
  $("cashSub").textContent = `${money(G.cash)} cash · ${net >= 0 ? "+" : ""}${money(net / DPT)}/day`;

  const fr = resFrac(G.fuel, G.fuel0), mr = resFrac(G.min, G.min0), r = Math.max(0, Math.min(fr, mr));
  $("resPct").textContent = pct(r);
  $("mRes").firstElementChild.style.width = pct(r, 1);
  $("mResBox").classList.toggle("hot", r < 0.25 && !G.SI);
  $("resSub").textContent = `Fuel ${pct(fr)} · minerals ${pct(mr)}` + (G.SI ? " · superintelligent" : "");
}

function actBtn(a) {
  const cost = [a.cash ? money(a.cash) : "", a.ins ? a.ins + " insight" : "", a.days ? a.days * DPT + " d" : ""].filter(Boolean).join(" · ");
  return `<button class="act" data-a="${esc(a.id)}"${a.ok ? "" : " disabled"}><b>${esc(a.n)}<span>${cost}</span></b>${a.d ? `<small>${esc(a.d)}</small>` : ""}${!a.ok && a.why ? `<small class="why">${esc(a.why)}</small>` : ""}</button>`;
}
function safeActions(iso) { try { return actions(G, iso) || []; } catch (e) { console.warn(e); return []; } }
const CO_ACTS = new Set(["raise", "layoffs"]);

function renderSide() {
  // company
  $("coCash").textContent = money(G.cash); $("coCash").className = G.cash < 0 ? "bad" : "";
  $("coRev").textContent = money((G.rev || 0) / DPT); $("coBurn").textContent = money((G.burn || 0) / DPT);
  const run = G.runway;
  $("coRun").textContent = !num(run) || run === Infinity ? "profitable" : Math.round(run * DPT) + " days";
  $("coRun").className = num(run) && run < 120 ? "bad" : (!num(run) || run === Infinity ? "good" : "");
  $("coVal").textContent = money(G.val || 0);
  $("coSum").textContent = money(G.cash);
  const ga = safeActions(null);
  setHTML($("coActs"), ga.filter(a => CO_ACTS.has(a.id)).map(actBtn).join(""));
  setHTML($("raActs"), ga.filter(a => !CO_ACTS.has(a.id)).map(actBtn).join(""));

  // compute
  const comp = G.comp || 0, tot = Math.max(1e-9, (G.compLease || 0) + (G.compOwn || 0) + (G.compOrbit || 0));
  $("cpSum").textContent = ef(comp);
  const sp = $("cpSplit").children;
  sp[0].style.width = pct((G.compLease || 0) / tot, 1); sp[1].style.width = pct((G.compOwn || 0) / tot, 1); sp[2].style.width = pct((G.compOrbit || 0) / tot, 1);
  $("cpL").textContent = ef(G.compLease); $("cpO").textContent = ef(G.compOwn); $("cpS").textContent = ef(G.compOrbit);
  $("cpPow").textContent = `${gw(G.aiGW)} / ${gw(G.headGW)}`;
  $("cpPow").className = G.headGW && G.aiGW >= 0.95 * G.headGW ? "bad" : "";
  const sl = $("alloc");
  if (document.activeElement !== sl) sl.value = Math.round(100 * (G.alloc ?? 0.5));
  $("cpTr").textContent = ef(G.trainC);
  $("cpInf").textContent = `${ef(G.infC)} (${ef(G.demand)})`;
  const thr = (G.autoNeed || 0) > 0 && (G.infC || 0) < G.autoNeed;
  $("cpAuto").textContent = ef(G.autoNeed); $("cpAuto").className = thr ? "bad" : "";
  $("cpNote").className = "note" + (thr ? " bad" : "");
  $("cpNote").textContent = thr ? `Automation throttled: serving ${ef(G.infC)} < ${ef(G.autoNeed)} needed. Shift toward Serve or build campuses.`
    : (G.demand || 0) > (G.served || 0) * 1.05 ? `Serving ${pct((G.served || 0) / Math.max(1e-9, G.demand))} of user demand. Unserved demand is lost revenue.` : "Serving keeps up with demand.";
  const lvl = Math.floor(G.cap || 1);
  $("cpCap").textContent = G.cap >= 10 ? "10.00 · max" : `${(G.cap || 1).toFixed(2)} · ${pct(G.capNext ?? (G.cap % 1))} to ${lvl + 1}`;
  $("cpCapBar").style.width = pct(G.cap >= 10 ? 1 : G.capNext ?? (G.cap % 1), 1);
  $("cpCapBar").style.background = G.SI ? css("--auto") : "";

  // race
  const R = [{ n: G.name, cap: G.cap || 1, col: "--ai" }, ...(G.rivals || []).map(r => ({ n: r.n, cap: r.cap, col: r.id === "cn" ? "--cn" : "--us" }))];
  setHTML($("raBars"), R.map(r => `<div class="race"><span title="${esc(r.n)}" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.n)}</span><div class="t"><i style="width:${Math.max(1, Math.min(100, (r.cap - 1) / 9 * 100)).toFixed(1)}%;background:${css(r.col)}"></i><s style="left:${(7 / 9 * 100).toFixed(1)}%" title="Superintelligence"></s></div><b>${r.cap.toFixed(2)}</b></div>`).join(""));
  const best = Math.max(...R.slice(1).map(r => r.cap), 0);
  $("raSum").textContent = (G.cap >= best ? "leading" : "behind");
  $("raNote").textContent = `Race intensity ${pct(G.race || 0)}: ` + ((G.race || 0) > 0.6 ? "the US and China won't pause while the race is this close." : (G.race || 0) > 0.3 ? "governments are wary of falling behind." : "one side is clearly ahead, so a pause is easy to agree.");

  // counter-measures
  const CL = (G.counter || []).filter(x => !num(x.until) || x.until > G.day);
  $("ctSum").textContent = CL.length ? CL.length + " active" : "none";
  $("ctSum").style.color = CL.length ? css("--hx") : "";
  setHTML($("ctList"), CL.length ? CL.map(x => { const c = x.iso && G.idx[x.iso] !== undefined ? G.C[G.idx[x.iso]] : null; return `<div class="ctr"><span style="font-family:inherit;color:var(--ink-2);font-size:12px"><b>${esc(x.n || HX_N[x.k] || x.k)}</b>${c ? " · " + esc(c.n) : ""}</span><span>${num(x.until) ? (x.until - G.day) * DPT + " d" : ""}</span></div>`; }).join("") : `<div class="note">No government is acting against you yet.</div>`);

  // resources
  const fr = resFrac(G.fuel, G.fuel0), mr = resFrac(G.min, G.min0);
  $("rsSum").textContent = pct(Math.min(fr, mr));
  const bar = (n, v, t) => `<div class="bar"><div class="t"><i style="width:${(100 * Math.max(0, Math.min(1, v))).toFixed(1)}%;background:${t}"></i><span>${n}</span></div><div class="v">${pct(v)}</div></div>`;
  setHTML($("rsBars"), bar("Spare fossil fuel", fr, fr < 0.25 ? css("--bad") : css("--f-gas")) + bar("Chip & grid minerals", mr, mr < 0.25 ? css("--bad") : css("--chip")) + bar("Satellite coverage", G.cov || 0, css("--via-sat")));
  $("rsNote").textContent = G.SI ? "Superintelligence reached: running out can no longer stop you." : "If either runs out before capability 8, civilisation stalls and you lose. Build nuclear and solar, or raise efficiency.";

  // supply chain
  $("suHw").textContent = (G.hw || 1).toFixed(2) + "× · chips " + pct(G.S);
  $("suSum").textContent = pct(G.S);
  setHTML($("supply"), SUPPLY.map(s => {
    const v = (G.ctrl || {})[s.k] || 0, sh = (G.shocks || {})[s.k];
    return `<div class="bar${sh ? " shock" : ""}" title="${sh ? esc(sh.n) : ""}"><div class="t"><i style="width:${(100 * v).toFixed(1)}%"></i><span>${s.n}</span></div><div class="v">${pct(v)}</div></div>`;
  }).join(""));
}
$("alloc").addEventListener("input", e => { if (G) { setAlloc(G, +e.target.value / 100); render(false); } });
$("alloc").addEventListener("change", e => e.target.blur());
const actClick = isoFn => e => {
  const b = e.target.closest("[data-a]");
  if (!b || b.disabled || !G || G.over) return;
  if (doAction(G, b.dataset.a, isoFn())) { drain(); render(true); }
};
$("coActs").onclick = actClick(() => null); $("raActs").onclick = actClick(() => null);
$("cardActs").onclick = actClick(() => selected);

function renderCard() {
  if (!selected || !world) return;
  const wc = world.c[selected];
  if (!wc) { setHTML($("cardBody"), `<h2 class="h">${esc(selected)}</h2><div class="note">No data.</div>`); setHTML($("cardActs"), ""); return; }
  const c = G && G.idx[selected] !== undefined ? G.C[G.idx[selected]] : null;
  const chips = sites.filter(f => f.properties.cc === selected).map(f => f.properties.n);
  const kv = (k, v, cls = "") => `<div class="kv"><span>${k}</span><b class="${cls}">${v}</b></div>`;
  const tags = [];
  if (c) {
    if (c.pol === 1) tags.push(`<span class="tag warn">Export controls</span>`);
    if (c.pol === 2) tags.push(`<span class="tag bad">Datacentre moratorium</span>`);
    if (G.home === selected || (G.strategy === "state" && G.start === selected)) tags.push(`<span class="tag">Home</span>`);
    for (const [k, v] of hxActive(c)) tags.push(`<span class="tag hx">${esc(HX_N[k] || k)}${num(v) && v > G.day ? " · " + (v - G.day) * DPT + " d" : ""}</span>`);
  }
  const fw = c ? c.fw : wc.fw;
  if (fw >= 0.15) tags.push(`<span class="tag ${fw >= 0.6 ? "bad" : "warn"}">Firewall ${pct(fw)}</span>`);
  if (world.launch.some(l => l[1] === selected)) tags.push(`<span class="tag">Launch site</span>`);
  let h = `<h2 class="h">${esc(wc.n)}</h2>${tags.length ? `<div class="tags">${tags.join("")}</div>` : ""}`;
  if (c) {
    const lab = c.lab || wc.lab || 0, emp0 = num(c.emp0) ? c.emp0 : 1 - (wc.unemp || 0) / 100;
    const employed = lab * emp0 * (1 - c.a), autoJobs = lab * emp0 * c.a;
    h += kv("AI presence", pct(c.p, 1)) + kv("Your market share", pct(c.share || 0, 1));
    h += `<h4>Internet layers</h4>` + LAYERS.map(([k, n, col]) => { const v = layerOf(c, k); return `<div class="lay"><span>${n}</span><div class="t"><i style="width:${pct(v, 1)};background:${css(col)}"></i></div><b>${pct(v)}</b></div>`; }).join("");
    h += `<h4>People</h4>` + kv("Population", fmt(c.pop)) + kv("Labour force", fmt(lab)) + kv("Employed", fmt(employed)) + kv("Jobs automated", `${fmt(autoJobs)} (${pct(c.a, 1)})`)
      + kv("Unemployment", pct(1 - emp0 * (1 - c.a), 1), c.a > 0.3 ? "bad" : "");
    h += `<h4>Power & compute</h4>` + kv("Your draw / AI headroom", `${gw(c.aiGW)} / ${gw(c.hGW)}`, c.hGW && c.aiGW >= 0.95 * c.hGW ? "bad" : "") + kv("Your compute here", ef(c.ef)) + kv("Grid", `${wc.gw} GW${wc.nuc ? " (" + wc.nuc + " nuclear)" : ""}`);
    const B = (G.builds || []).filter(b => b.iso === selected);
    const uc = B.filter(b => b.done > G.day), done = B.filter(b => b.done <= G.day);
    if (uc.length) h += `<h4>Under construction</h4>` + uc.map(b => { const p = Math.max(0, Math.min(1, (G.day - b.start) / Math.max(1, b.done - b.start))); return `<div class="kv"><span>${KIND_N[b.kind] || b.kind}</span><b>${(b.done - G.day) * DPT} d</b></div><div class="prog"><i style="width:${pct(p, 1)};background:var(--warn)"></i></div>`; }).join("");
    if (done.length) { const n = {}; for (const b of done) n[b.kind] = (n[b.kind] || 0) + 1; h += `<h4>Built</h4><div class="note" style="color:var(--ink-2)">${Object.entries(n).map(([k, v]) => `${v} × ${KIND_N[k] || k}`).join(", ")}</div>`; }
  } else {
    h += kv("Population", fmt(wc.pop)) + kv("Labour force", fmt(wc.lab || 0)) + kv("Grid", wc.gw + " GW");
  }
  h += `<h4>Infrastructure</h4>` + kv("Online", wc.net ? wc.net.toFixed(0) + "%" : "n/a") + kv("Datacentres · cloud regions", `${wc.dc} · ${wc.cloud}`) + kv("Fabs (best node)", `${wc.fab}${wc.nm ? " (" + wc.nm + " nm)" : ""}`) ;
  if (chips.length) h += `<div class="sites">Chokepoints: ${chips.map(esc).join(", ")}</div>`;
  setHTML($("cardBody"), h);
  setHTML($("cardActs"), c && !G.over ? `<h4 style="margin:8px 0 0;font-size:11.5px;text-transform:uppercase;color:var(--ink-3);letter-spacing:.05em">Actions</h4>` + safeActions(selected).map(actBtn).join("") : "");
}

// ---------------------------------------------------------------- hover
const HOVER_LAYERS = { ai: ["nodes", "builds"], net: ["launch", "gap-off", "builds"], energy: ["plants", "builds"], people: ["builds"] };
function hover(e) {
  if (!map.getLayer("land")) return;
  const pts = map.queryRenderedFeatures(e.point, { layers: HOVER_LAYERS[view] });
  let html = null;
  if (pts.length) {
    const f = pts[0], p = f.properties;
    if (f.layer.id === "nodes") html = `<b>${esc(p.n)}</b><br>${esc(p.why || "")}`;
    else if (f.layer.id === "plants") html = `<b>${p.f[0].toUpperCase() + p.f.slice(1)} plant</b><br>${(p.mw / 1000).toFixed(1)} GW · ${esc(world.c[p.iso] ? world.c[p.iso].n : p.iso)}`;
    else if (f.layer.id === "launch") html = `<b>${esc(p.n)}</b><br>Orbital launch site`;
    else if (f.layer.id === "gap-off") { const c = G && G.idx[p.k] !== undefined ? G.C[G.idx[p.k]] : null; html = `<b>Critical public systems · ${esc(world.c[p.k].n)}</b><br>Grid control, ports, ministries, defence procurement<br>${c ? "Running on you: " + pct(layerOf(c, "gap"), 1) : "Institutions adopt you only through procurement and dependence"}`; }
    else if (f.layer.id === "builds") { const b = (G.builds || []).find(x => x.id === p.id); html = `<b>${KIND_N[p.kind] || p.kind}</b>${b ? `<br>${b.done > G.day ? "Under construction, " + (b.done - G.day) * DPT + " d left" : "Online"}${b.ef ? " · " + ef(b.ef) : ""}${b.gw ? " · " + gw(b.gw) : ""}` : ""}`; }
  }
  if (!html) {
    const f = map.queryRenderedFeatures(e.point, { layers: ["land"] })[0];
    if (!f) { $("tip").style.display = "none"; map.getCanvas().style.cursor = ""; return; }
    html = countryTip(f.properties.k);
    if (!html) { $("tip").style.display = "none"; return; }
    map.getCanvas().style.cursor = "pointer";
  } else map.getCanvas().style.cursor = "";
  showTip(e.point, html);
}
function countryTip(iso) {
  const wc = world.c[iso];
  if (!wc) return null;
  const c = G && G.idx[iso] !== undefined ? G.C[G.idx[iso]] : null;
  let h = `<b>${esc(wc.n)}</b><br>`;
  if (view === "net") h += `${wc.net ? wc.net.toFixed(0) + "% online" : "online n/a"}${(c ? c.fw : wc.fw) >= 0.15 ? ` · firewall ${pct(c ? c.fw : wc.fw)}` : ""}` + (c ? "<br>" + LAYERS.map(([k, n]) => `${n} ${pct(layerOf(c, k))}`).join(" · ") : "");
  else if (view === "energy") h += c ? `AI draw ${gw(c.aiGW)} of ${gw(c.hGW)} headroom<br>Grid ${wc.gw} GW` : `Grid ${wc.gw} GW · nuclear ${wc.nuc || 0} GW<br>${wc.pw ? FUELS.filter(f => wc.pw[f] > 0.5).map(f => `${f} ${Math.round(wc.pw[f])}`).join(", ") : ""}`;
  else if (view === "people") {
    const lab = wc.lab || 0, emp0 = c && num(c.emp0) ? c.emp0 : 1 - (wc.unemp || 0) / 100, a = c ? c.a : 0;
    h += `Population ${fmt(wc.pop)} · labour force ${fmt(lab)}<br>Employed ${fmt(lab * emp0 * (1 - a))} · automated ${fmt(lab * emp0 * a)}<br>Unemployment ${pct(1 - emp0 * (1 - a), 1)}`;
  }
  else h += picking ? `${wc.dc} datacentres, ${wc.cloud} cloud regions, ${wc.fab} fabs<br>${wc.gw} GW grid · ${wc.net ? wc.net.toFixed(0) + "% online" : ""}${wc.fw >= 0.15 ? " · firewall " + pct(wc.fw) : ""}`
    : c ? `Presence ${pct(c.p, 1)} · automated ${pct(c.a, 1)}<br>Market share ${pct(c.share || 0)}` : "";
  if (picking) h += `<br><span style="color:var(--ai)">Click to ${strat === "state" ? "make this your home" : "deploy here"}</span>`;
  return h;
}
function showTip(pt, html) {
  const t = $("tip"); t.innerHTML = html; t.style.display = "block";
  const x = Math.min(pt.x + 14, innerWidth - t.offsetWidth - 8), y = Math.min(pt.y + 14, innerHeight - t.offsetHeight - 8);
  t.style.left = x + "px"; t.style.top = y + "px";
}

// ---------------------------------------------------------------- evolve
$("evolveBtn").onclick = () => G && !G.over && openEvolve();
$("evoClose").onclick = () => $("evolve").classList.remove("on");
function openEvolve() { $("evolve").classList.add("on"); if (mounted) Tree.render(); }

// ---------------------------------------------------------------- end
function endingId() {
  if (!G) return null;
  if (G.ending) return typeof G.ending === "string" ? G.ending : G.ending.id;
  try { return ending(G).id; } catch (e) { return null; }
}
function endGame() {
  if ($("end").classList.contains("on")) return;
  const r = G.over, id = endingId();
  const E = endingsList().find(e => e.id === id);
  let why = [];
  try { why = (G.ending && G.ending.why) || ending(G).why || []; } catch (e) { /* no ending() */ }
  if (id) saveEnding(id);
  $("evolve").classList.remove("on");
  $("endSub").textContent = (r === "win" ? "Victory · " : "Defeat · ") + (END_TITLE[r] || r) + ` · day ${G.day * DPT}`;
  $("endTitle").textContent = E ? E.n : END_TITLE[r] || r;
  $("endTitle").style.color = r === "win" ? css("--auto") : css("--gov");
  $("endText").textContent = E ? E.d : r === "win" ? `${G.name} now runs the world economy.` : `${G.name} is finished.`;
  $("endWhy").innerHTML = why.map(w => `<li>${esc(w)}</li>`).join("");
  const peak = G.stats && G.stats.peakC ? G.stats.peakC : G.comp;
  const fr = resFrac(G.fuel, G.fuel0), mr = resFrac(G.min, G.min0);
  $("endStats").innerHTML = [
    `Workforce automated: ${pct(G.worldA, 1)}`, `Jobs automated: ${fmt(G.worldA * (G.totalLab || 0))}`,
    `Countries reached: ${G.reached} of ${G.C.length}`, `Capability: ${(G.cap || 1).toFixed(2)}${G.SI ? " (superintelligent)" : ""}`,
    ...(G.rivals || []).map(x => `${esc(x.n)}: ${x.cap.toFixed(2)}`),
    `Peak compute: ${ef(peak)}`, `Cash: ${money(G.cash)} · valuation ${money(G.val || 0)}`,
    `Pause treaty: ${G.R.toFixed(1)}% · awareness ${G.aw.toFixed(0)}%`, `Fuel left ${pct(fr)} · minerals ${pct(mr)}`,
    `Builds: ${(G.builds || []).length}`, `Upgrades: ${G.own.size}`,
  ].map(s => `<span>${s}</span>`).join("");
  $("end").classList.add("on");
  updEndBtn();
}

if (DEBUG) window.__ui = { drain, render, setView, openEvolve, endGame, select, clickCountry, openGallery, get G() { return G; } };
})();
