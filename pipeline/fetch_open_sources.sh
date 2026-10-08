#!/usr/bin/env bash
# Downloads the sources that are freely scriptable (Natural Earth, shipping lanes, WRI power plants).
# GEM, Maus et al., Aqueduct, WHYMAP and USGS files need manual download: see raw/README.md.
set -euo pipefail
cd "$(dirname "$0")/../raw"
mkdir -p naturalearth
NE=https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson
for f in ne_50m_land ne_50m_admin_0_countries ne_50m_admin_0_boundary_lines_land ne_10m_admin_0_boundary_lines_land \
         ne_10m_rivers_lake_centerlines_scale_rank ne_10m_lakes ne_10m_glaciated_areas ne_10m_admin_0_disputed_areas \
         ne_10m_admin_0_boundary_lines_maritime_indicator ne_10m_admin_0_boundary_lines_disputed_areas \
         ne_10m_geography_marine_polys ne_10m_geography_regions_polys ne_10m_populated_places_simple \
         ne_10m_roads ne_10m_railroads ne_10m_ports; do
  curl -fsSL -o naturalearth/$f.geojson $NE/$f.geojson && echo "ok $f"
done
curl -fsSL -o naturalearth/Shipping_Lanes_v1.geojson https://raw.githubusercontent.com/newzealandpaul/Shipping-Lanes/main/data/Shipping_Lanes_v1.geojson
curl -fsSL -o naturalearth/global_power_plant_database.csv https://raw.githubusercontent.com/wri/global-power-plant-database/master/output_database/global_power_plant_database.csv
echo "done"
