"""Make a single-file page for hosts that can't load local CSS (e.g. a claude.ai artifact).

Inlines vendor/maplibre-gl.css into site/index.html and writes dist/index.html.
The data files stay separate: publish them alongside as data/<name>.
MapLibre JS is still loaded from jsDelivr (pinned to 4.7.1).

    python tools/bundle_artifact.py
"""
import os
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src = open(f"{ROOT}/site/index.html", encoding="utf-8").read()
css = open(f"{ROOT}/site/vendor/maplibre-gl.css", encoding="utf-8").read()
tag = '<link rel="stylesheet" href="vendor/maplibre-gl.css">'
assert tag in src, "stylesheet link not found"
os.makedirs(f"{ROOT}/dist", exist_ok=True)
open(f"{ROOT}/dist/index.html", "w", encoding="utf-8").write(src.replace(tag, f"<style>{css}</style>", 1))
print("wrote dist/index.html")
