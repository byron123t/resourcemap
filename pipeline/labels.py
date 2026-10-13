"""Label points for the HTML label layer: countries, populated places, seas, regions. Needs only Natural Earth."""
import os
ROOT = os.environ.get("ATLAS_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json, warnings
import pandas as pd, geopandas as gpd
warnings.filterwarnings("ignore")

D = f"{ROOT}/site/data"; NE = f"{ROOT}/raw/naturalearth"

labels = []
C = gpd.read_file(f"{NE}/ne_50m_admin_0_countries.geojson")
for _, r in C.iterrows():
    labels.append({"t": "c", "n": r["NAME"], "x": round(r["LABEL_X"], 3), "y": round(r["LABEL_Y"], 3), "z": float(r["MIN_LABEL"]), "k": r["ADM0_A3"]})

# Populated places: r = 0 national capital, 1 admin-1 capital or 1 M+ people, 2 other.
# z is Natural Earth's min_zoom, floored so cities don't crowd out country names at world scale.
PP = gpd.read_file(f"{NE}/ne_10m_populated_places_simple.geojson")
PP = PP[~PP["featurecla"].astype(str).isin(["Historic place", "Meteorological Station"])]
for _, r in PP.iterrows():
    fc, pop = str(r["featurecla"]), int(r["pop_max"]) if r["pop_max"] and r["pop_max"] > 0 else 0
    rank = 0 if fc.startswith("Admin-0 capital") else 1 if fc.startswith("Admin-1") or pop >= 1_000_000 else 2
    l = {"t": "p", "n": " ".join(str(r["name"]).split()), "x": round(r.geometry.x, 3), "y": round(r.geometry.y, 3), "z": round(max(2.6 if rank < 2 else 3.6, float(r["min_zoom"])), 1), "r": rank}
    if pop: l["pop"] = pop
    if isinstance(r["adm1name"], str) and r["adm1name"] and r["adm1name"] != r["name"]: l["a"] = r["adm1name"]
    if isinstance(r["adm0_a3"], str): l["k"] = r["adm0_a3"]
    labels.append(l)

MP = gpd.read_file(f"{NE}/ne_10m_geography_marine_polys.geojson")
for _, r in MP.iterrows():
    if not isinstance(r["name"], str) or r.geometry is None: continue
    p = r.geometry.representative_point()
    z = float(r["min_label"]) if "min_label" in MP.columns and not pd.isna(r["min_label"]) else 2 + float(r["scalerank"])
    labels.append({"t": "m", "n": " ".join(r["name"].split()), "x": round(p.x, 3), "y": round(p.y, 3), "z": z})
GR = gpd.read_file(f"{NE}/ne_10m_geography_regions_polys.geojson"); GR.columns = [c.lower() if c != "geometry" else c for c in GR.columns]; GR = GR.set_geometry("geometry")
for _, r in GR.iterrows():
    if not isinstance(r["name"], str) or r.geometry is None: continue
    if str(r["featurecla"]) in ("Continent", "Island group", "Island", "Peninsula", "Pen/cape", "Isthmus", "Coast", "Delta", "Wetlands", "Gorge"): continue
    p = r.geometry.representative_point()
    z = float(r["min_label"]) if "min_label" in GR.columns and not pd.isna(r["min_label"]) else 2 + float(r["scalerank"])
    labels.append({"t": "g", "n": " ".join(r["name"].split()), "x": round(p.x, 3), "y": round(p.y, 3), "z": z, "f": str(r["featurecla"])})
json.dump(labels, open(f"{D}/labels.json", "w"), separators=(",", ":"), ensure_ascii=False)
print("labels", len(labels), {t: sum(l["t"] == t for l in labels) for t in "cpmg"}, f"{os.path.getsize(f'{D}/labels.json')/1e6:.2f} MB")
