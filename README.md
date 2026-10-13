# World Extraction Atlas

An interactive, site-level world map of where resources are extracted and processed — oil & gas fields, coal and iron-ore mines, critical-mineral deposits, satellite-mapped mining land, power plants, steel/cement/chemical plants, LNG terminals, pipelines, shipping lanes, ports, rail, roads, rivers, lakes, glaciers, water stress, groundwater, disputed borders and shipping chokepoints — with per-country totals and rankings. On top of that sit security and infrastructure layers (military bases, airfields, data centres, undersea internet cables), farmland and land cover, and live feeds: satellite imagery (true colour, thermal infrared, fire temperature), VIIRS fire detections, GDELT conflict reports and news, large ships, US Navy warship and carrier positions, military aircraft, business jets and airliners. A **Storyboard** mode lists the biggest surges in conflict reports over the past day, week or month in time order and flies the map to each one.

Live at **https://byron123t.github.io/resourcemap/**.

It's a single static page (`site/index.html`) on [MapLibre GL JS 4.7.1](https://maplibre.org/) that loads pre-processed GeoJSON from `site/data/`. No build step and no tile server: geometry is simplified ahead of time, and labels are rendered as HTML, so it needs no glyph server either.

```
site/
  index.html            the whole app: HTML + CSS + JS in one file (~1,500 lines of JS)
  vendor/               maplibre-gl.css and maplibre-gl.js 4.7.1 (the page loads the JS from jsDelivr; the local copy is for offline tests)
  data/                 processed layers, ~80 MB (see "Data files" below)
  data/live/            live snapshots written by pipeline/live/fetch_live.py (generated at deploy time, not committed)
  .nojekyll             tells GitHub Pages to serve the folder as-is
pipeline/               Python that turns the raw downloads into site/data
  run_all.sh            runs every step in order
  step1_extraction.py               oil & gas, coal mines, iron ore, Maus mining polygons, basemap
  step2_water_geo_pipelines.py      country keys + totals, LNG, pipelines, chokepoints, rivers/lakes/glaciers, borders
  labels.py                         labels.json: countries, 7k populated places, seas, regions (Natural Earth only)
  step3_industry_power_transport.py power plants, steel, cement, chemicals, shipping lanes, rail, roads
  step4_water_risk_minerals.py      Aqueduct water risk, WHYMAP groundwater, USGS critical minerals
  step5_defense_infra.py            military bases (Wikidata + OSM), airfields (OurAirports), ports (NGA WPI), data centres (OSM), submarine cables (TeleGeography)
  live/archive.py       rolling history of the live feeds under a byte budget (fetch_live.py --archive)
  live/fetch_live.py    near-real-time snapshots: FIRMS fires, GDELT events + storyboard, AIS ships, USNI navy tracker, adsb.lol aircraft, business jets, airliners
  postprocess.py        size/cleanup passes run between the steps
  fetch_open_sources.sh downloads the freely scriptable sources into raw/
raw/                    raw downloads (not committed; see raw/README.md)
.github/workflows/pages.yml  builds and deploys site/ to GitHub Pages, refreshing the live snapshots every 30 min
tools/
  screenshot.py         headless render at several zooms → tools/shots/*.png
  bundle_artifact.py    inlines the CSS into dist/index.html for single-file hosting
  live_refresh.py       keeps site/data/live fresh in dev: optional CI-artifact pull, then fetch_live.py, on a timer
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

Set `ATLAS_ROOT` to point the scripts at another checkout. The steps must run in order, because each one reads and extends files written by the one before. In particular, `step2` rewrites the point layers from `step1` to add a quantity field `q` and a country key `k`, and it creates `country_totals.json`, which steps 3 and 4 then extend. Step 5 is independent and downloads its own inputs into `raw/infra/` (`--offline` rebuilds from that cache).

### Refresh only the infrastructure or live layers

```bash
python3 -I pipeline/step5_defense_infra.py          # bases, airports, ports, data centres, cables (~5 min the first time, needs network)
python3 -I pipeline/live/fetch_live.py               # all live feeds into site/data/live/
python3 -I pipeline/live/fetch_live.py --only fires,aircraft,jets,airliners
python3 -I pipeline/live/fetch_live.py --only navy,story
AISSTREAM_API_KEY=… python3 -I pipeline/live/fetch_live.py --only ships   # needs `pip install websockets`
gh run download -n live-data -D site/data/live          # or copy the latest CI snapshots, global ships included (kept 3 days)
python3 -I pipeline/live/fetch_live.py --archive raw/archive   # also file this run into the rolling history
python3 tools/live_refresh.py --ci --every 30          # dev loop: pull newer CI feeds, then fetch; repeats until Ctrl+C
```

Each live feed is independent: if one fails, its previous output and manifest entry stay. `site/data/live/manifest.json` records each feed's `updated` time, count, source and any error; the sidebar reads it to show "· 2 h ago" and turns red when a feed is missing or more than 12 h old. Open tabs re-read the manifest every 10 minutes and reload any live layer that has a newer snapshot.

**Rolling archive.** With `--archive DIR`, `pipeline/live/archive.py` adds each run to a history capped at `--archive-budget-mb` (default 1000). It has two tiers. The long tier holds five years (`--archive-long-days 1826`) of conflict reports as daily per-place totals with the two most-cited links (about 85 KB/day gzipped, so about 155 MB for five years), one storyboard snapshot per day (about 20 KB, about 35 MB) and one USNI tracker per weekly post. The short tier holds fires per detection day (0.7–1 MB), plus the first ships, military aircraft, business jets and airliner snapshots of each UTC hour (about 230 KB per hour together). After each run the oldest short-tier days are deleted until the total fits, at about 6.5 MB a day the short tier covers about 5 months in the first year, falling to about 4 months once the long tier holds its full five years (about 190 MB). Everything is gzipped JSON listed in `DIR/index.json`. LADD/PIA aircraft never reach the archive, because it reads the already filtered outputs. The archive isn't published yet: Pages can't hold it, and it needs a CORS-enabled host (a Hugging Face dataset or a Cloudflare R2 bucket).

## Publish on GitHub Pages

`.github/workflows/pages.yml` deploys `site/` on every push to `main`, on demand, and every 30 minutes (`7,37 * * * *`). Each run takes about 14 minutes and fetches fresh live snapshots first. GitHub delays or drops scheduled runs when it is busy, so in practice the deployed fires, events, storyboard, ships and aircraft are usually under an hour old, sometimes a few hours. A 30-minute schedule uses about 20,000 Actions minutes a month: free for a public repository, but over the 2,000-minute allowance of a private one. The USNI navy tracker is published weekly, so that layer changes about once a week. Raw downloads and the last snapshots are kept in the Actions cache, so a failed fetch redeploys the previous data.

One-time setup:

1. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
2. **For global ships, add a free aisstream.io key** as the repository secret `AISSTREAM_API_KEY` (Settings → Secrets and variables → Actions, or `gh secret set AISSTREAM_API_KEY`). Without it, the ships layer falls back to Finland's open Digitraffic feed, which covers only the Baltic (polled live from the browser every minute). The US Navy warships layer needs no key and is global either way.

Everything else needs no key. The processed `site/data/*.geojson` files are committed; only `site/data/live/` is generated at deploy time.

## How the page works

All of this is in `site/index.html`, in the second `<script>` block.

**Layer registry.** `Q` lists every resource point layer: its label, colour token, unit and size constant `k`. `GROUPS` maps each sidebar checkbox to the map layers and data files behind it. `VIEWS` defines the six presets (Overview, Resources, Industry, Transport, Water, Security).

**Lazy loading.** `ensure(src)` fetches a data file the first time its checkbox is turned on; `FILES` maps sources to static files and `LOADERS` handles the live ones. `pushData(src)` applies the status filter (all active, operating, or planned), the fuel filter for power and the commodity filter for critical minerals, then sets the source data. Defence and live layers have no status field; their filters are in `FILTERS` (fire type, base type, airport size).

**True positions at every zoom.** There is no grid or clustering: every site is one circle at its own coordinates. Radius is `k·√q`, capped at 26 px by `RC(k)` and scaled by zoom through the `ZF` stops, so the world view shows each site small and the circles grow as you zoom in. Circles of operating sites are opaque; planned ones are faint. Circles are sorted so big sites draw under small ones.

**Icons.** Ships, aircraft, warships and military bases use canvas icons drawn in `ICONS` (outlines in `SHAPES`) and registered with `addImage` (`pixelRatio: 2`). Ships and aircraft rotate by course/track; carriers get a flat-deck icon. `repaint()` redraws them with `updateImage` when the theme changes.

**Lines.** Pipelines and cables are hit-tested with a small box around the pointer. Hovering one outlines the whole system (`hoverLine()`, `hover` source), because both are stored as many segments. Planned cables are a separate dashed layer (`cables-plan`), since `line-dasharray` can't be data-driven. `fitCable()` shifts the western pieces of a cable that crosses the antimeridian so "Show whole route" frames it tightly.

**Live layers.** `LIVE` lists the snapshot feeds (`fires`, `events`, `ships`, `aircraft`, `jets`, `airliners`, `navy`), fetched from `data/live/` with the manifest's `generated` time as a cache buster. Status lines under each checkbox come from `setLive()`/`shipStatus()` and refresh every minute. Ships use the build's AIS snapshot when the manifest has one, otherwise `digitraffic()` polls Fintraffic's open Baltic feed (`pollShips()`).

**Business jets and airliners.** `run_jets()` and `run_airliners()` share `fetch_types()`, which asks adsb.lol for each type in `JET_TYPES` or `AIRLINER_TYPES` (about 60 Airbus, Boeing, Embraer, CRJ, ATR, Dash 8 and COMAC designators, roughly 5,000–9,000 aircraft depending on the hour; `JET_TYPES` has about 35 Gulfstream, Bombardier, Dassault, Embraer, Learjet, Citation, Hawker and Pilatus PC-24 designators). It sends one request every 2 s because adsb.lol answers bursts with 429 and `/v2/all` isn't available, and drops military aircraft (already in `aircraft.geojson`), aircraft flagged LADD (the owner asked the FAA not to have it shown) or PIA (privacy ICAO address), and anonymous `~` addresses, and it stores no owner names. The page lists a jet in search by registration or callsign and links to globe.adsb.lol for its live track. It is a build-time snapshot rather than a browser feed because the open ADS-B APIs send no CORS headers, and OpenSky's terms need a written agreement for use in a live product.

**US Navy.** `navy.geojson` comes from the latest USNI News Fleet and Marine Tracker post. `run_navy()` parses each ship's sentence for a port or sea area, geocodes it from a built-in gazetteer and fans ships at the same place out on a small spiral. Positions are approximate by design.

**Storyboard.** The "Storyboard" mode button hides the layer controls (`#sheetBody`) and shows `#story`. `setMode()` saves the layer checkboxes, keeps only a few context layers, and restores them on exit. `story.json` has three windows (`day`: 24 hourly buckets, `week`: 7 days, `month`: ten 3-day buckets). Each item is a place whose CAMEO 18–20 reports in one bucket were far above that place's own average over the window (`story_window()` in `fetch_live.py`). `storyRender()` draws the summary, the total-per-bucket chart, numbered cards with per-place sparklines, numbered HTML markers and the `story-halo` circles. `storyGo(i)` highlights a card, flies there and opens a detail panel with the article links and recent news. Prev/Next step through the items, and Play advances every 6.5 s. The `day` window ends at the newest GDELT 2.0 15-minute file; `month` is built from GDELT 1.0 daily files, which appear about a day late, so its last bucket can start 2–4 days back.

**News.** Clicking a country or a conflict report calls `loadNews(place, topic)`, which asks the GDELT DOC API for articles from the last 14 days. GDELT allows one request per 5 s, so requests are queued (`newsNext`) and cached per place.

**Satellite imagery.** `IMAGERY` is the registry of raster overlays, grouped as in the picker: sharp mosaics (Esri, EOX Sentinel-2 cloudless), daily true colour (VIIRS, MODIS), false colour, 10 m Sentinel on demand, 30 m HLS, geostationary GeoColor (GOES, MTG, Himawari), thermal infrared, fire temperature, and night/thermal daily composites. `gb()` builds NASA GIBS entries and `eu()` EUMETSAT WMS entries. `applyImagery()` adds the chosen raster under the borders (`beforeId "disputed"`), sets its `maxzoom` to the source's native level, and writes resolution, image time and attribution into `#imgNote`. Daily layers get a date picker; geostationary layers (`kind: "now"`) refresh every 10 minutes with `setTiles`. GIBS's `default` time differs between CDN edges, so `gibsLatest()` reads the `layer-time-actual` header from tile `0/0/0` and pins that explicit time. Esri World Imagery is a patchwork of photos from different years, so at zoom 12 and above `esriWhen()` asks the Esri `identify` endpoint for the photo under the map centre and shows its capture date, resolution and provider.

**Sentinel and NAIP on demand.** The `pc()` entries (`src: "pc"`) pull Sentinel-2 L2A, Sentinel-1 RTC and USDA NAIP (60 cm US aerial photos, from zoom 11, newest photo per spot from the last ~4 years) scenes from Microsoft Planetary Computer, which needs no key and allows CORS. `applyImagery()` POSTs a CQL2 search to `/data/v1/mosaic/register`: Sentinel-2 scenes from the 14 days up to the "Up to" date with under 30% cloud, least cloudy first, or Sentinel-1 scenes from the last 12 days, newest first. It then requests tiles from `/mosaic/{searchid}/tiles/…` with the band recipe in `q`. Search ids are cached per page load (`pcReg`). The tiler renders nothing below zoom 9 for Sentinel-2 or zoom 8 for Sentinel-1, so `pcHint()` shows a "Zoom in" button. "Find scenes in this view" runs a STAC search over the visible bounds for the last 30 days and groups the results into passes (one satellite, one UTC day). Hovering over a pass outlines its footprint in the `hover` source. Picking a pass re-registers the mosaic with an `id in [...]` filter, so only that pass shows.

**Farmland & land cover.** `AGRI` holds three GIBS rasters: SEDAC croplands and pastures (share of each cell, around 2000) and MODIS IGBP land cover (yearly, 2001–2024). `applyAgri()` adds the chosen one under the borders, and above any satellite imagery, with its own opacity slider and legend.

**Sources panel.** `SOURCES` lists every dataset with its link, licence and retrieval date. Dates come from `infra_meta.json` (step 5) or the live manifest; the panel is rendered into the sidebar at startup. The map's attribution control also shows the active imagery's credit.

**Streets & buildings.** The "Streets & buildings" checkbox (on in the Transport view) shows OpenStreetMap from OpenFreeMap's free vector tiles (`osm` source, OpenMapTiles schema, zoom 0–14, overzoomed to the map's max of 17; the planet build is refreshed about weekly, so new roads appear within a week or two of being mapped). Nothing is downloaded until the layer is on. `osm-water` and `osm-bldg` (buildings from zoom 13) sit under the satellite imagery, so imagery shows real rooftops. `osm-case`, `osm-street`, `osm-rail` and `osm-names` sit above the imagery and borders and below every data layer, which gives a hybrid map. Street names are the only text drawn by MapLibre, using OpenFreeMap's Noto Sans glyphs (`glyphs` in the style). While OSM is on, `applyGroup()` caps the Natural Earth `roads` and `rail` layers at zoom 9, because they don't line up with OSM streets. Colours are the `--street*` and `--building*` tokens.

**Terrain.** The "Terrain shading" checkbox shows a `hillshade` layer from Mapterhorn's terrarium-encoded elevation tiles (`dem` source, 512 px WebP, zoom 0–12, overzoomed above that). It sits above the background shading, so choropleth colours show through, and its shadow, highlight and accent colours are the `--hs-*` tokens. The Resources and Water views turn it on.

**Background shading.** Only one fill shows at a time, chosen in the "Background shading" select and handled by `applyBase()`. `w:*` options colour Aqueduct basins by a category field, `g:recharge` shows WHYMAP groundwater, and `c:*` options are country choropleths with five equal-count classes, computed from `country_totals.json` and written into a `c` property on the country features.

**Labels.** `labels.json` (countries, about 7,300 cities and towns, seas, regions; built by `pipeline/labels.py`) is placed as absolutely positioned HTML in `placeLabels()`, with greedy box-collision against earlier labels and the chokepoint markers. Labels are tried in order of min zoom, then city rank (national capitals, then admin-1 capitals and 1 M+ cities, then the rest) and population, so big places win collisions. Capitals get a ringed dot and minor towns a smaller grey one.

**Search.** The search box matches countries, Natural Earth cities and features in loaded layers locally, then asks the Photon geocoder (komoot, OpenStreetMap data) for anything else, debounced to 450 ms and cached per query. Photon is used instead of Nominatim because Nominatim's usage policy forbids search-as-you-type. Typed coordinates (`36.95, -76.31`) jump straight there. The last result opens a Google Maps search for the same text, and every detail panel links to Google Maps and OpenStreetMap at that spot.

**Theming.** Every colour is a CSS custom property on `:root`, redefined for dark mode. `repaint()` copies the tokens into the MapLibre paint properties when the theme changes.

**Resize safety.** The map is created only once its container has a real size (`whenSized`). After that, `keepCanvasInSync()` calls `map.resize()` and `setPixelRatio` whenever the container or the device pixel ratio changes. This was added for a bug where the canvas rendered only in the top-left corner.

## Known issues / what to fix next

1. **Dense regions overlap at world zoom.** With no aggregation, the Permian, Ruhr or Pearl River Delta read as a solid blob below zoom 3. Per-country totals are in the country panel; a heat or density layer at low zoom would be the next step if that matters.
2. **Circles are capped at 26 px** (`RC`), so the very largest sites look the same size as merely big ones. Consider a log scale or a legend note.
3. **Data gaps:**
   - No oil refineries.
   - Solar, wind, hydro and nuclear come from the 2021 WRI database. Swap in the GEM Global Integrated Power Tracker (`raw/README.md` has the links).
   - The US critical-minerals set is far denser than the global one.
   - About two thirds of oil and gas fields report no production.
   - Military bases come from Wikidata plus OpenStreetMap `military=base|airfield|naval_base` (deduplicated within 3 km). Still incomplete: OSM mapping is uneven and few secret or foreign-operated sites are tagged.
   - Data centres are OSM only, so coverage is uneven. PeeringDB has better coverage but its AUP forbids republishing, so it is off by default (`--with-peeringdb`).
4. **Live feeds are snapshots, not streams.** Fires, events, ships and aircraft are as fresh as the last Pages build (every 30 min when GitHub runs the schedule on time). Global ships need the `AISSTREAM_API_KEY` secret and cover terrestrial AIS only, so open ocean is sparse and warships usually don't broadcast. Warship positions come from the weekly USNI tracker instead (US Navy only, place names rather than coordinates). Aircraft are the adsb.lol military filter plus about 35 business-jet and 60 airliner types, and only show transponders near volunteer receivers. Light jets, turboprops and piston planes aren't fetched, and opted-out (LADD/PIA) aircraft are left out on purpose.
5. **Storyboard headlines are guessed from article URLs.** GDELT gives no titles, so `slug_title()` rebuilds one from the URL path and `slug_case()` restores capitals for known places and actors. Some headlines stay lower case or describe a different event at the same place.
6. **Sentinel tiles need zoom 8–9 and are slow.** Planetary Computer renders each tile on request (about 1–2 s each), so a full screen takes a few seconds. It is a free public service with no uptime promise. Free sharper imagery is limited to Esri's mosaic (often several years old) and NAIP (US only, every 2–3 years). Fresh sub-metre imagery (Vantor/Maxar, Planet SkySat, Airbus Pléiades, or resellers such as SkyFi and UP42) is paid per km² and needs an API key, so it would need a small backend to hold the key and should be an explicit "order this view" button, never loaded on zoom.
7. **Farmland shares are from around 2000.** The SEDAC cropland and pasture layers are the most recent global ones on GIBS. For change over time, use the MODIS land-cover option with its year picker.
8. **Performance.** `power.geojson` (~8.5 MB, about 35k points) and `water_risk.geojson` (~7 MB) are the heaviest files. If load time matters, convert to vector tiles (PMTiles via tippecanoe), served statically with the `pmtiles` protocol.

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
| ports.geojson | 1,081 | `sr` Natural Earth scalerank (3 = largest); superseded on the map by ports_wpi |
| ports_wpi.geojson | 3,807 | NGA World Port Index: `hs` harbour size L/M/S/V, `q` 4/3/2/1 from size, `ht` harbour type, `cd` channel depth m, `od` oil terminal depth m, `mv` max vessel length m, `lo` UN/LOCODE, `wpi` index number, `c` country |
| bases.geojson | 11,741 | military installations: `t` air/naval/army/missile/other, `src` w=Wikidata / o=OSM, `op` operator, `c` country (Wikidata only), `w` Wikidata QID, `osm` element id (n/w/r + id); closed bases dropped |
| airports.geojson | 29,431 | OurAirports: `t` L/M/S (large/medium/small), `mil`=1 military or joint use, `rw` longest runway m, `p`=1 paved, `i` ICAO, `ia` IATA, `c` country; small fields without a runway record pruned |
| datacenters.geojson | 4,692 | OSM `telecom=data_center`: `o` operator, `osm` element id, `src` o=OSM / p=PeeringDB |
| cables.geojson | 733 | TeleGeography submarine cables, split at the antimeridian: `id`, `n`, `col`, `km`, `rfs` ready-for-service year, `own` owners, `sup` suppliers, `lp` landing point count, `pl`=1 planned, `url` |
| landings.geojson | 1,926 | cable landing stations: `id`, `n`, `k` cables landing, `cs` cable names, `tbd`=1 planned only |
| infra_meta.json | | per step-5 file: count, size, source URLs, retrieval time, licence |
| live/manifest.json | | `generated`, and per feed `updated`, `count`, `source`, `url`, `status`/`error` |
| live/fires.json | ~200k | columns `lon, lat, frp` (MW), `ti4` (K), `t` UTC, `dn` day/night, `nd`, `ind`=1 within 2.5 km of a known industrial heat source, `cf`=1 within 30 km of a conflict report from the last 72 h |
| live/events.geojson | ~6.6k | GDELT city-level CAMEO 18–20, 14 days: `e` events, `a` articles, `e3` events in last 72 h, `r18`/`r19`/`r20` by root code, `g` mean Goldstein, `d0`/`d` first/last seen, `u` sample article URLs |
| live/ships.geojson | | AIS: `cl` carrier/military/law/tanker/cargo/passenger/other, `mmsi`, `n`, `ty` AIS type, `sog`, `cog`, `hd`, `len` m, `dest`, `imo`, `t` |
| live/navy.geojson | ~40 | USNI tracker (`as_of`, `post` at top level): `n` ship, `h` hull number, `cl` carrier/amphib/surface/sub/aux, `loc` place, `port`=1 in port, `grp` strike group, `s` source sentence, `d` date, `u` post URL |
| live/story.json | | `{updated, windows: {day, week, month}}`; each window has `buckets` (ISO start times), `total` per bucket and `items`: `t`/`t1` bucket, `n` place, `cc`, `lat`, `lon`, `e` reports, `a` articles, `r` [18, 19, 20] counts, `z` score, `base` usual reports per bucket, `x` per-bucket series, `u` article URLs, `title`, `act` top actors |
| live/aircraft.geojson | ~200 | adsb.lol military: `hex`, `f` callsign, `r` registration, `ty` type, `alt` ft, `gnd`, `gs` kn, `trk`, `sq` squawk |
| live/jets.geojson | ~400 | adsb.lol business jets, same fields as aircraft plus `desc` type name; LADD/PIA and military aircraft dropped |
| live/airliners.geojson | ~5,000–9,000 | adsb.lol airliners and regional aircraft, same fields as jets.geojson (~1.2 MB, ~200 KB gzipped) |
| rail.geojson / roads.geojson | 24k / 21k lines | `sr` scalerank, `e` electrified / `x` expressway |
| chokepoints.geojson | 8 | `q` oil M b/d (1H 2025), `lng` Bcf/d, `pk`/`pky` recent peak (EIA, Mar 2026) |
| water_risk.geojson | ~16k basins | Aqueduct 4.0 categories 0–4 (−1 = arid, low use): `ws` water stress, `f30`/`f50`/`f80` future stress (business as usual), `p50`/`o50` pessimistic/optimistic 2050, `gw` groundwater decline, `dr` drought, `rf`/`cf` flood, `wr` raw stress ratio |
| groundwater.geojson | 2,966 | WHYMAP `r` recharge class 0–4, `t` m=major basin / c=complex / l=local |
| saline_gw.geojson, gw_mining.geojson | 102 / 55 | saline groundwater areas; intensive or fossil-water well fields (`x`=2 fossil) |
| rivers, lakes, glaciers, land, countries, borders, disputed, disputed_lines, maritime | | Natural Earth, simplified; lakes have `km2`, `res` reservoir, `sal` saline |
| labels.json | 8,330 | `{t: c/p/m/g, n, x, y, z: min zoom}`; cities add `r` rank 0 national capital / 1 admin-1 capital or 1 M+ / 2 other, `pop` metro population, `a` admin-1 name, `k` ADM0_A3; regions add `f` feature class |
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

Infrastructure (step 5):

- **Wikidata** military installations, https://query.wikidata.org/ (CC0).
- **OpenStreetMap** `military=base|airfield|naval_base` via Overpass, © OpenStreetMap contributors (ODbL 1.0).
- **TeleGeography Submarine Cable Map**, https://www.submarinecablemap.com/ (CC BY-NC-SA 3.0): cables and landing points.
- **OurAirports**, https://ourairports.com/data/ (public domain).
- **NGA World Port Index**, Pub 150, https://msi.nga.mil/Publications/WPI (public domain, US Government).
- **OpenStreetMap** `telecom=data_center` via Overpass, © OpenStreetMap contributors (ODbL 1.0).

Live feeds (fetched every 30 min by the Pages workflow, or live from the browser):

- **NASA FIRMS** VIIRS 375 m active fires (NOAA-20, NOAA-21, Suomi NPP), https://firms.modaps.eosdis.nasa.gov/. NASA data, no restrictions; credit NASA FIRMS.
- **GDELT Project** 1.0 and 2.0 Event Databases (conflict reports, storyboard) and DOC API (news), https://www.gdeltproject.org/. Free for any use with attribution.
- **USNI News Fleet and Marine Tracker**, https://news.usni.org/category/fleet-tracker (© U.S. Naval Institute). The layer summarises ship locations and links each one to the post.
- **adsb.lol** military aircraft, business jets and airliners, https://adsb.lol/ (ODbL). adsb.lol asks heavy or production users to get in touch; if it introduces API keys, add one as a secret.
- **aisstream.io** terrestrial AIS, https://aisstream.io/ (free API key; terms at aisstream.io).
- **Fintraffic Digitraffic** marine AIS, https://www.digitraffic.fi/ (CC BY 4.0). Baltic fallback when there is no AIS key.

Satellite imagery (tiles requested directly by the browser):

- **NASA EOSDIS GIBS**, https://www.earthdata.nasa.gov/gibs: VIIRS, MODIS, HLS (Landsat and Copernicus Sentinel-2), GOES (NOAA), Himawari (JMA) layers. NASA data, no restrictions; credit the source satellite.
- **EUMETSAT** View Service WMS, https://view.eumetsat.int/: MTG FCI and Meteosat IODC. Free with credit "© EUMETSAT".
- **EOX Sentinel-2 cloudless 2024**, https://s2maps.eu/ (CC BY-NC-SA 4.0; contains modified Copernicus Sentinel data).
- **Copernicus Sentinel-2 L2A**, https://planetarycomputer.microsoft.com/dataset/sentinel-2-l2a, via Microsoft Planetary Computer. Free under the Copernicus Sentinel data terms; credit "Contains modified Copernicus Sentinel data [year]".
- **USDA NAIP**, https://planetarycomputer.microsoft.com/dataset/naip, National Agriculture Imagery Program, USDA Farm Service Agency, via Microsoft Planetary Computer (public domain).
- **Copernicus Sentinel-1 RTC**, https://planetarycomputer.microsoft.com/dataset/sentinel-1-rtc, terrain-corrected by Catalyst, via Microsoft Planetary Computer (CC BY 4.0).
- **Ramankutty et al. 2010**, Global Agricultural Lands: Croplands 2000 (https://doi.org/10.7927/H4C8276G) and Pastures 2000 (https://doi.org/10.7927/H47H1GGR), NASA SEDAC, CC BY 4.0, via GIBS.
- **NASA MODIS Land Cover Type** MCD12Q1 v061 (https://doi.org/10.5067/MODIS/MCD12Q1.061), NASA open data, via GIBS.
- **Esri World Imagery**, © Esri, Maxar, Earthstar Geographics and the GIS User Community (Esri terms of use). Capture dates from the same service's `identify` endpoint.
- **OpenStreetMap**, © OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright), as vector tiles from **OpenFreeMap** (https://openfreemap.org/, free, no key) in the OpenMapTiles schema (© OpenMapTiles, CC BY 4.0 for the schema and styles). Fonts are Noto Sans (SIL OFL) from the same server. Used for streets, buildings, rail, water and street names.
- **Mapterhorn terrain tiles**, https://mapterhorn.com/ (credit "© Mapterhorn"; per-source list at https://mapterhorn.com/attribution). Copernicus GLO-30 DEM worldwide (Copernicus DEM licence, © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the European Union and ESA), plus about 150 national lidar and DEM datasets under CC BY 4.0, public domain, Licence Ouverte, Datenlizenz Deutschland and similar open licences. Used for the hillshade.

Search:

- **Photon geocoder** by komoot, https://photon.komoot.io/, on OpenStreetMap data, © OpenStreetMap contributors (ODbL 1.0). Free public instance with fair-use limits; queries go straight from the browser.

If you publish the page, keep the Sources panel and the attribution control. CC BY-SA on the Maus data means derived mining-polygon files must stay CC BY-SA, and ODbL on the OSM and adsb.lol layers means derived databases must stay ODbL. The TeleGeography cable data is CC BY-NC-SA, so the cables layer is for non-commercial use and derived cable files must keep that licence.
