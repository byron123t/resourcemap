#!/usr/bin/env bash
# Rebuilds every file in site/data from the raw sources in raw/ (see raw/README.md).
# Each step reads site/data from the previous one, so run them in this order.
set -euo pipefail
cd "$(dirname "$0")"
PY="python3 -I"
$PY step1_extraction.py
$PY postprocess.py mining
$PY step2_water_geo_pipelines.py
$PY postprocess.py pipelines
$PY step3_industry_power_transport.py
$PY postprocess.py power
$PY step4_water_risk_minerals.py
$PY postprocess.py water
$PY postprocess.py clean
echo "done → ../site/data"
