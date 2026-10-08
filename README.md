# World Extraction Atlas

An interactive, site-level world map of where resources are extracted and processed — oil & gas fields, coal and iron-ore mines, critical-mineral deposits, satellite-mapped mining land, power plants, steel/cement/chemical plants, LNG terminals, pipelines, shipping lanes, ports, rail, roads, rivers, lakes, glaciers, water stress, groundwater, disputed borders and shipping chokepoints — with per-country totals and rankings.

It's a single static page (`site/index.html`) on [MapLibre GL JS 4.7.1](https://maplibre.org/) that loads pre-processed GeoJSON from `site/data/`. No build step and no tile server: geometry is simplified ahead of time, and labels are rendered as HTML, so it needs no glyph server either.

```
site/
  index.html            the whole app: HTML + CSS + JS in one file (~1,000 lines of JS)
  vendor/               maplibre-gl.css and maplibre-gl.js 4.7.1 (the page loads the JS from jsDelivr; the local copy is for offline tests)
  data/                 processed layers, ~59 MB (see "Data files" below)
pipeline/               Python that turns the raw downloads into site/data
  run_all.sh            runs every step in order
  step1_extraction.py               oil & gas, coal mines, iron ore, Maus mining polygons, basemap
  step2_water_geo_pipelines.py      country keys + totals, LNG, pipelines, chokepoints, rivers/lakes/glaciers, borders, labels
  step3_industry_power_transport.py power plants, steel, cement, chemicals, ports, shipping lanes, rail, roads
  step4_water_risk_minerals.py      Aqueduct water risk, WHYMAP groundwater, USGS critical minerals
  postprocess.py        size/cleanup passes run between the steps
  fetch_open_sources.sh downloads the freely scriptable sources into raw/
raw/                    raw downloads (not committed; see raw/README.md)
tools/
  screenshot.py         headless render at several zooms → tools/shots/*.png
  bundle_artifact.py    inlines the CSS into dist/index.html for single-file hosting
```

## Run it

```bash
cd site
python -m http.server 8765
# open http://localhost:8765
```

It must be served over HTTP, not opened as `file://`, because the page `fetch()`es `data/*.geojson`.

## Rebuild the data

```bash
pip install -r pipeline/requirements.txt
bash pipeline/fetch_open_sources.sh      # Natural Earth, shipping lanes, WRI power plants
# then put the manual downloads in raw/ as described in raw/README.md
bash pipeline/run_all.sh                 # ~10–15 min; writes site/data/
```

Set `ATLAS_ROOT` to point the scripts at another checkout. The steps must run in order, because each one reads and extends files written by the one before. In particular, `step2` rewrites the point layers from `step1` to add a quantity field `q` and a country key `k`, and it creates `country_totals.json`, which steps 3 and 4 then extend.

## How the page works

All of this is in `site/index.html`, in the second `<script>` block.

**Layer registry.** `Q` lists every point layer: its label, colour token, unit, size constant `k` and slot in the grid glyph. `GROUPS` maps each sidebar checkbox to the map layers and data files behind it. `VIEWS` defines the five presets (Overview, Resources, Industry, Transport, Water).

**Lazy loading.** `ensure(src)` fetches a data file the first time its checkbox is turned on. `pushData(src)` applies the status filter (all active, operating, or planned), the fuel filter for power and the commodity filter for critical minerals, then sets the source data.

**Three zoom regimes for point layers.** This is where the current problems are; see Known issues.

1. **Below `AGG_MAX` (2.5), the grid summary.** `cells()` bins sites into a screen-space Web Mercator grid (`CW`×`CH` px) at a fixed zoom `bin` (rounded to 0.25) and sums `q` per cell. Each cell draws one circle per layer at a fixed offset (`circle-translate` from `Q[k].slot`), so the circles never overlap. A screen-fixed tile icon (`g-plates`, a canvas image from `setPlateImage`) sits behind cells that hold two or more layers. Circle radius is `k·√sum`. Per layer, the 90th-percentile cell fills the slot (`MAXR`); bigger cells are capped and get a dark ring. Clicking a tile opens `showCell()`, which lists every layer's total for that cell.
2. **From `AGG_MAX` to zoom 6, true-position clusters.** Each point source is a MapLibre clustered GeoJSON source (`clusterMaxZoom: 6`, `clusterRadius: 26`) with `clusterProperties` summing `q`. A cluster sits at its members' centroid, uses radius `RC(k)` (capped at 26 px) and has a dark edge. Clicking it zooms to its expansion zoom.
3. **Above zoom 6, individual sites.** Radius is `RC(k)` scaled by `ZF`; faint circles aren't operating yet.

**Background shading.** Only one fill shows at a time, chosen in the "Background shading" select and handled by `applyBase()`. `w:*` options colour Aqueduct basins by a category field, `g:recharge` shows WHYMAP groundwater, and `c:*` options are country choropleths with five equal-count classes, computed from `country_totals.json` and written into a `c` property on the country features.

**Labels.** `labels.json` (countries, capitals, seas, regions) is placed as absolutely positioned HTML in `placeLabels()`, with greedy box-collision against earlier labels and the chokepoint markers.

**Theming.** Every colour is a CSS custom property on `:root`, redefined for dark mode. `repaint()` copies the tokens into the MapLibre paint properties when the theme changes.

**Resize safety.** The map is created only once its container has a real size (`whenSized`). After that, `keepCanvasInSync()` calls `map.resize()` and `setPixelRatio` whenever the container or the device pixel ratio changes. This was added for a bug where the canvas rendered only in the top-left corner.

## Known issues / what to fix next

1. **The world-view grid still reads as a lattice.** The user wants positions to stay accurate at every zoom. Options:
   - **Drop the grid.** Set `AGG_MAX = 0` and let MapLibre clusters handle every zoom. This is accurate, but clusters of different layers overlap.
   - **Keep the per-resource slot glyph but place it at the weighted centroid** of each cell's sites instead of the cell centre, with collision avoidance.
   - **Hex bins in geographic space.** Draw real hexagon polygons (not screen icons) and render each layer as a small pie or bar inside the hex.
   - **Per-layer clusters with a pixel offset per layer at low zoom.** Reuse `Q[k].slot` as `circle-translate` on the cluster layers. Positions stay roughly right, and different layers don't stack.

   To try any of these, the relevant code is `cells()`, `scale()`, `aggFor()`, `refreshAgg()`, `drawPlates()`, `setPlateImage()` and the `g-*` layers in `onLoad()`.
2. **The switch at `AGG_MAX` is abrupt.** Grid circles disappear and clusters appear. A short cross-fade would help: animate `circle-opacity` over 2.3–2.7.
3. **Some cluster circles are capped at 26 px** (`RC`), so very large aggregates such as the Permian or Western Siberia look the same size as smaller ones. Consider a log scale or a legend note.
4. **Data gaps:**
   - No oil refineries.
   - Solar, wind, hydro and nuclear come from the 2021 WRI database. Swap in the GEM Global Integrated Power Tracker (`raw/README.md` has the links).
   - The US critical-minerals set is far denser than the global one.
   - About two thirds of oil and gas fields report no production.
5. **Performance.** `power.geojson` (~8.5 MB, about 35k points) and `water_risk.geojson` (~7 MB) are the heaviest files. If load time matters, convert to vector tiles (PMTiles via tippecanoe), served statically with the `pmtiles` protocol.

## Data files (site/data)

Short property keys keep the files small. Every point feature has `q` (the quantity used for sizing), `s` (status, lowercase), `k` (the Natural Earth ADM0_A3 country key) and `n` (name).

| file | features | key properties |
|---|---|---|
| oilgas.geojson | 7,055 fields | `q`/`pt` production M boe/yr, `po` oil M bbl/yr, `pg` gas M m³/yr, `rt` reserves M boe, `rc` reserve class, `f` fuel, `o` operator, `b` basin, `w` GEM wiki |
| oilgas_outlines.geojson | 1,081 | field polygons (GEM WKT) |
| coal.geojson | 5,390 mines | `q`=capacity or production Mt/yr, `cap`, `pr`, `rs` reserves Mt, `ct` coal type, `mt` mine type |
| coal_boundaries.geojson | 666 | GEM coal mine boundary polygons |
| iron.geojson | 980 mines | `q` production or capacity Mt/yr, `rs` reserves Mt |
| critical_minerals.geojson | 3,392 deposits | `m` commodities joined with "\|" (e.g. `lithium\|REE`), `dt` deposit type, `src` g=global 2017 / u=US 2026, `pr`/`rs` production/resource text (US only), `lk` USGS link |
| mining_points.json | 44,929 | compact `[lon, lat, km²]` centroids of Maus et al. polygons |
| mining_polygons_{w,c,e}.geojson | 44,929 total | polygons split by longitude (<-30, -30–60, >60); loaded only at zoom ≥ 6.5 |
| power.geojson | 35,160 plants | `q` GW, `mw`, `f` fuel group (coal/gas/oil/nuclear/hydro/solar/wind/geothermal/bioenergy/storage/other), `r`=1 if from WRI 2021 |
| steel.geojson | 1,476 | `q` crude steel Mt/yr, `ir` iron Mt/yr, `eq` equipment |
| cement.geojson | 3,690 | `q` cement Mt/yr, `ck` clinker |
| chemicals.geojson | 868 | `pp` primary products, `fs` feedstock (no capacity, so `q`=1) |
| lng.geojson | 742 terminals | `q` Mt/yr, `ft` import/export, `fl` floating |
| pipelines.geojson | 4,560 | `f` oil/gas, `q` M boe/d, `cap` bcm/yr, `x`=1 if it crosses borders, `cs` countries |
| shipping.geojson | 211 | `t` Major/Middle/Minor |
| ports.geojson | 1,081 | `sr` Natural Earth scalerank (3 = largest) |
| rail.geojson / roads.geojson | 24k / 21k lines | `sr` scalerank, `e` electrified / `x` expressway |
| chokepoints.geojson | 8 | `q` oil M b/d (1H 2025), `lng` Bcf/d, `pk`/`pky` recent peak (EIA, Mar 2026) |
| water_risk.geojson | ~16k basins | Aqueduct 4.0 categories 0–4 (−1 = arid, low use): `ws` water stress, `f30`/`f50`/`f80` future stress (business as usual), `p50`/`o50` pessimistic/optimistic 2050, `gw` groundwater decline, `dr` drought, `rf`/`cf` flood, `wr` raw stress ratio |
| groundwater.geojson | 2,966 | WHYMAP `r` recharge class 0–4, `t` m=major basin / c=complex / l=local |
| saline_gw.geojson, gw_mining.geojson | 102 / 55 | saline groundwater areas; intensive or fossil-water well fields (`x`=2 fossil) |
| rivers, lakes, glaciers, land, countries, borders, disputed, disputed_lines, maritime | | Natural Earth, simplified; lakes have `km2`, `res` reservoir, `sal` saline |
| labels.json | 1,210 | `{t: c/p/m/g, n, x, y, z: min zoom}` |
| country_totals.json | 242 keyed by ADM0_A3 | sums of **operating** sites: `og`, `coal`, `iron`, `mine_km2`, `crit_n`, `crit_m` {commodity: count}, `pw_gw` and `pw_<fuel>`, `steel`, `cement`, `chem_n`, `ammonia_n`, `lng_exp`, `lng_imp`, `ports`, `lake_km2`, `res_km2`, `ice_km2`, `bws` (area-weighted Aqueduct score 0–5), `bws_hi` (% of area at high stress or above), `gw_basin_km2`, `pop`, `id` (index into countries.geojson) |
| crit_commodities.json | | commodity → deposit count, used to fill the mineral filter |

Conversions: gas is converted at 6,000 cf per barrel of oil equivalent (million m³ × 35.3147 / 6000 = M boe). LNG 1 Mt ≈ 1.36 bcm. Offshore sites are assigned to the nearest country within 400 km in an equal-area projection (EPSG:6933).

## Sources and licences

- **Global Energy Monitor trackers** (CC BY 4.0), globalenergymonitor.org/download-data: oil & gas extraction (Mar 2026), coal mines (Aug 2026), coal mine boundaries v1.0.2, iron ore mines (Sep 2026), coal plants (Jul 2026), oil & gas plants (Aug 2026), iron & steel (Sep 2026), cement (Jul 2026), chemicals (Nov 2025), oil/NGL pipelines (Jul 2026), gas pipelines (Nov 2025), LNG terminals (Sep 2025).
- **Maus et al. 2022**, Global-scale mining polygons v2, https://doi.org/10.1594/PANGAEA.942325 (CC BY-SA 4.0).
- **WRI Aqueduct 4.0** water risk (2023), CC BY 4.0.
- **BGR/UNESCO WHYMAP** groundwater resources.
- **USGS:** global distribution of critical minerals (Labay et al. 2017, https://doi.org/10.5066/F7GH9GQR) and critical mineral deposits of the US v3.0 (2026, https://doi.org/10.5066/P9K1HBNT). Public domain.
- **WRI Global Power Plant Database** v1.3 (2021), CC BY 4.0.
- **Global Shipping Lanes**, Benden 2022, https://doi.org/10.5281/zenodo.6361763 (CC BY 4.0, excluding Statista).
- **US EIA**, World Oil Transit Chokepoints (updated Mar 2026).
- **Natural Earth** (public domain): land, countries, borders, disputed areas, maritime lines, rivers, lakes, glaciers, roads, railroads, ports, populated places, marine and region names.

If you publish the page, keep the attribution text in the sidebar notes. CC BY-SA on the Maus data means derived mining-polygon files must stay CC BY-SA.
