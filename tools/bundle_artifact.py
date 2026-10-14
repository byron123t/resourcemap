"""Make a single-file page for hosts that can't load local CSS or scripts (e.g. a claude.ai artifact).

Inlines vendor/maplibre-gl.css into site/index.html, loads MapLibre JS from jsDelivr (pinned to 4.7.1) and the fonts
from Google Fonts instead of vendor/, drops pmtiles.js (only the offline server needs it) and writes dist/index.html.
The data files stay separate: publish them alongside as data/<name>.

    python tools/bundle_artifact.py
"""
import os
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src = open(f"{ROOT}/site/index.html", encoding="utf-8").read()
css = open(f"{ROOT}/site/vendor/maplibre-gl.css", encoding="utf-8").read()
SWAPS = [
    ('<link rel="stylesheet" href="vendor/maplibre-gl.css">', f"<style>{css}</style>"),
    ('<link rel="stylesheet" href="vendor/fonts.css">', '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@75..100,500..800'
     '&family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">'),
    ('<script src="vendor/maplibre-gl.js"></script>', '<script src="https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>'),
    ('<script src="vendor/pmtiles.js"></script>\n', ""),
]
for a, b in SWAPS:
    assert a in src, f"not found: {a[:60]}"
    src = src.replace(a, b, 1)
os.makedirs(f"{ROOT}/dist", exist_ok=True)
open(f"{ROOT}/dist/index.html", "w", encoding="utf-8").write(src)
print("wrote dist/index.html")
