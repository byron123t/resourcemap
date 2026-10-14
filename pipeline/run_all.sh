#!/usr/bin/env bash
# Rebuilds every file in site/data from the raw sources in raw/ (see raw/README.md).
# Each step reads site/data from the previous one, so run them in this order.
set -euo pipefail
cd "$(dirname "$0")"
PY="python3 -I"
$PY step1_extraction.py
$PY postprocess.py mining
$PY step2_water_geo_pipelines.py
$PY labels.py                 # place names; needs only raw/naturalearth
$PY postprocess.py pipelines
$PY step3_industry_power_transport.py
$PY postprocess.py power
$PY step4_water_risk_minerals.py
$PY postprocess.py water
$PY step5_defense_infra.py   # downloads into ../raw/infra; add --offline to reuse the cache
$PY step6_network_pop.py     # refineries + scenario networks; downloads into ../raw/net, --offline reuses it
$PY postprocess.py clean
echo "done → ../site/data"
