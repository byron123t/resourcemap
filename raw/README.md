# raw/ — source downloads (not committed)

`pipeline/fetch_open_sources.sh` fills `naturalearth/`. Everything else is a manual download: most of these sites put a form or login in front of the file. Put the files exactly where shown, because the scripts look them up by these paths.

```
raw/
  naturalearth/                        ← fetch_open_sources.sh
    ne_*.geojson                         Natural Earth (16 files)
    Shipping_Lanes_v1.geojson            github.com/newzealandpaul/Shipping-Lanes
    global_power_plant_database.csv      github.com/wri/global-power-plant-database
  gem/                                 ← globalenergymonitor.org/download-data (free form)
    Global-Oil-and-Gas-Extraction-Tracker-March-2026.xlsx
    Global Coal Mine Tracker, August 2026.xlsx
    Global Iron Ore Mines Tracker - September 2026 - Standard Copy (V1).xlsx
    Global-Coal-Plant-Tracker-July-2026.xlsx
    Global Oil and Gas Plant Tracker (GOGPT) - August 2026.xlsx
    Plant-level data - Global Iron and Steel Tracker - September 2026 (V1).xlsx
    Plant-level data - Global Cement and Concrete Tracker - July 2026 - Standard Copy V1.xlsx
    Plant-level data - Global Chemicals Inventory - November 2025 V2.xlsx
    coal_boundaries/*.geojson            unzip "Coal Mine Boundaries and Methane Sources - version 1.0.2 …zip" (geojson files only)
    gis/
      GEM-GOIT-Oil-NGL-Pipelines-2026-07-21.gpkg      from GEM-GOIT-Oil-NGL-Pipelines-2026-06-corrected.zip
      GEM-GGIT-Gas-Pipelines-2025-11.gpkg             from GEM-GGIT-Gas-Pipelines-2025-11.zip
      GEM-GGIT-LNG-Terminals-2025-09.gpkg             from GEM-GGIT-LNG-Terminals-2025-09-gis-files.zip
  maus2022/                            ← doi.org/10.1594/PANGAEA.942325 (free PANGAEA account)
    global_mining_polygons_v2.gpkg
  aqueduct/                            ← wri.org/data/aqueduct-global-maps-40-data — unzip the download here
    Aqueduct40_waterrisk_download_Y2023M07D05/GDB/Aq40_Y2023D07M05.gdb
  whymap/                              ← whymap.org (BGR) — GeoJSON exports
    whymap_11_gw_resources_recharge.geojson
    whymap_10_saline_gw.geojson
    whymap_1_gw_abstraction_mining.geojson
  usgs_critmin_global/                 ← doi.org/10.5066/F7GH9GQR — unzip PP1802_CritMin_Shapefiles.zip here
    PP1802_CritMin_pts.shp (+ .dbf .shx .prj …)
  usgs_critmin_us/                     ← doi.org/10.5066/P9K1HBNT — unzip critical_mineral_deposits_v3.zip here
    critical_mineral_deposits_v3/critical_mineral_deposits_v3.shp (+ sidecars)
```

The GEM file names change with each release. If you download a newer version, update the file names in the step scripts; search them for `.xlsx`.

## Suggested additions (not wired in yet)

- **GEM Global Integrated Power Tracker** (solar, wind and every other fuel; Sep 2026). Use it to replace the WRI 2021 plants in step 3. globalenergymonitor.org/download-data
- **Climate TRACE**: refineries and other manufacturing sites with coordinates and emissions. climatetrace.org/data
- **US DOE/NETL Global Oil & Gas Infrastructure**: refineries worldwide. doi.org/10.18141/1427300
- **Oxford Spatial Finance Initiative GeoAsset databases**: pulp & paper and ethylene plants. sustainablefinance.ox.ac.uk/geoasset-databases/
- **TransitionZero Solar Asset Mapper**: solar farms detected from satellite imagery. zenodo.org/records/11368204
