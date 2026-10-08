"""Second pass: quantity field `q`, country keys, water + geography + infrastructure layers, country totals."""
import os
ROOT = os.environ.get("ATLAS_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json, os, glob, math, warnings
import pandas as pd, geopandas as gpd, numpy as np
from shapely.geometry import mapping, Point
warnings.filterwarnings("ignore")

W = ROOT; D = f"{W}/site/data"; NE = f"{W}/raw/naturalearth"; GIS = f"{W}/raw/gem/gis"
EA = "EPSG:6933"  # equal-area for km²

def rc(geom, n):
    g = mapping(geom)
    def f(c):
        if isinstance(c[0], (list, tuple)): return [f(x) for x in c]
        return [round(c[0], n), round(c[1], n)]
    return {"type": g["type"], "coordinates": f(g["coordinates"])}

def dump(name, feats):
    p = f"{D}/{name}.geojson"
    for ft in feats:
        pr = ft["properties"]
        for k in list(pr):
            v = pr[k]
            if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) or (isinstance(v, str) and v.lower() in ("nan", "none", "")):
                del pr[k]
            elif isinstance(v, (np.integer,)): pr[k] = int(v)
            elif isinstance(v, (np.floating,)): pr[k] = float(v)
    json.dump({"type": "FeatureCollection", "features": feats}, open(p, "w"), separators=(",", ":"), allow_nan=False)
    print(f"{name:22s} {len(feats):6d}  {os.path.getsize(p)/1e6:5.2f} MB")

def nz(v):
    try:
        f = float(str(v).replace(",", ""))
        return None if math.isnan(f) or f <= 0 else f
    except Exception:
        return None

# ---------- countries ----------
C = gpd.read_file(f"{NE}/ne_50m_admin_0_countries.geojson")
C["key"] = C["ADM0_A3"]
C = C[["key", "NAME", "NAME_LONG", "LABEL_X", "LABEL_Y", "MIN_LABEL", "POP_EST", "geometry"]]
Cp = C.to_crs(EA)

def assign(gdf):
    """country key for points; offshore points snap to nearest country within 400 km"""
    g = gdf.to_crs(EA)
    j = gpd.sjoin_nearest(g, Cp[["key", "geometry"]], how="left", max_distance=400_000)
    j = j[~j.index.duplicated()]
    return j["key"].reindex(gdf.index)

totals = {k: {} for k in C["key"]}
def add(key, field, v):
    if key is None or (isinstance(key, float)) or v is None: return
    t = totals.setdefault(key, {}); t[field] = t.get(field, 0) + v

def addcount(key, field):
    if key is None or isinstance(key, float): return
    t = totals.setdefault(key, {}); t[field] = t.get(field, 0) + 1

# ---------- point layers: add q + country key ----------
def pts(name, qfn, tfield, cfield):
    fc = json.load(open(f"{D}/{name}.geojson"))
    feats = fc["features"]
    g = gpd.GeoDataFrame(geometry=[Point(f["geometry"]["coordinates"]) for f in feats], crs=4326)
    keys = assign(g)
    for f, k in zip(feats, keys):
        q = qfn(f["properties"])
        if q: f["properties"]["q"] = round(q, 3)
        else: f["properties"].pop("q", None)
        if isinstance(k, str): f["properties"]["k"] = k
        live = str(f["properties"].get("s", "")).lower() == "operating"
        if live: add(k, tfield, q)
        addcount(k, cfield)
    dump(name, feats)

pts("oilgas", lambda p: p.get("pt"), "og", "og_n")
pts("coal", lambda p: p.get("cap") or p.get("pr"), "coal", "coal_n")
pts("iron", lambda p: p.get("pr") or p.get("cap"), "iron", "iron_n")

# mining footprint (area, all statuses — it's observed land)
arr = json.load(open(f"{D}/mining_points.json"))
g = gpd.GeoDataFrame({"a": [a[2] for a in arr]}, geometry=[Point(a[0], a[1]) for a in arr], crs=4326)
keys = assign(g)
for a, k in zip(arr, keys):
    add(k, "mine_km2", a[2])
# keep compact array: [lon, lat, area]

# ---------- LNG terminals ----------
L = gpd.read_file(glob.glob(f"{GIS}/*LNG*.gpkg")[0])
L = L[L["Status"].isin(["operating", "construction", "proposed"])]
L = L[L.geometry.notna() & ~L.geometry.is_empty]
L["k"] = assign(L)
lng = []
for _, r in L.iterrows():
    q = nz(r["CapacityinMtpa"])
    pt = r.geometry if r.geometry.geom_type == "Point" else r.geometry.representative_point()
    p = {"n": r["TerminalName"] + (f" — {r['UnitName']}" if isinstance(r["UnitName"], str) and r["UnitName"] and r["UnitName"] != r["TerminalName"] else ""),
         "c": r["Country/Area"], "s": r["Status"], "ft": r["FacilityType"], "o": r["Owner"] if isinstance(r["Owner"], str) else None,
         "fl": "yes" if str(r["Floating"]).lower() in ("yes", "true", "1") else None,
         "sy": nz(r["ActualStartYear"]) or nz(r["LatestPlannedStartYear"]), "w": r["Wiki"] if isinstance(r["Wiki"], str) else None,
         "k": r["k"] if isinstance(r["k"], str) else None}
    if q: p["q"] = round(q, 2)
    p = {k: (int(v) if k == "sy" else v) for k, v in p.items() if v not in (None, "")}
    lng.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(pt.x, 4), round(pt.y, 4)]}, "properties": p})
    if r["Status"] == "operating":
        add(p.get("k"), "lng_exp" if r["FacilityType"] == "export" else "lng_imp", q)
dump("lng", lng)

# ---------- pipelines ----------
pipes = []
for f, kind in [(glob.glob(f"{GIS}/*Oil-NGL*.gpkg")[0], "oil"), (glob.glob(f"{GIS}/*Gas-Pipelines*.gpkg")[0], "gas")]:
    P = gpd.read_file(f)
    P = P[P["Status"].isin(["operating", "construction", "proposed"]) & P.geometry.notna()]
    P = P[~P.geometry.is_empty]
    P["geometry"] = P.geometry.simplify(0.02, preserve_topology=False)
    for _, r in P.iterrows():
        if r.geometry is None or r.geometry.is_empty: continue
        boed = nz(r["CapacityBOEd"])
        countries = str(r["CountriesOrAreas"] or "")
        p = {"n": r["PipelineName"], "sg": r["SegmentName"] if isinstance(r["SegmentName"], str) and r["SegmentName"] not in ("", "--") else None,
             "f": kind, "s": r["Status"], "cs": countries, "x": 1 if ("," in countries or ";" in countries) else 0,
             "km": nz(r["LengthMergedKm"]), "o": r["Owner"] if isinstance(r["Owner"], str) else None,
             "w": r["Wiki"] if isinstance(r["Wiki"], str) else None}
        if kind == "gas":
            b = nz(r["CapacityBcm/y"]); p["cap"] = round(b, 2) if b else None
        if boed: p["q"] = round(boed / 1e6, 4)  # million boe/d
        p = {k: (round(v) if k == "km" else v) for k, v in p.items() if v not in (None, "")}
        pipes.append({"type": "Feature", "geometry": rc(r.geometry, 3), "properties": p})
dump("pipelines", pipes)

# ---------- chokepoints (EIA, updated Mar 3 2026; 1H 2025 values) ----------
choke = [
    ("Strait of Malacca", 101.0, 2.6, 23.2, 9.2), ("Strait of Hormuz", 56.4, 26.5, 20.9, 11.4),
    ("Suez Canal & SUMED", 32.4, 30.3, 4.9, 0.9), ("Bab el-Mandeb", 43.4, 12.6, 4.2, 0.0),
    ("Danish Straits", 10.9, 55.6, 4.9, 1.6), ("Turkish Straits", 29.1, 41.1, 3.7, 0.6),
    ("Panama Canal", -79.7, 9.1, 2.3, None), ("Cape of Good Hope", 18.5, -34.4, 9.1, 5.7),
]
peak = {"Strait of Malacca": (24.0, 2023), "Strait of Hormuz": (21.9, 2022), "Suez Canal & SUMED": (8.8, 2023), "Bab el-Mandeb": (9.3, 2023),
        "Danish Straits": (5.0, 2023), "Turkish Straits": (3.7, "1H25"), "Panama Canal": (2.3, "1H25"), "Cape of Good Hope": (9.3, 2024)}
dump("chokepoints", [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]},
      "properties": {k: v for k, v in {"n": n, "q": q, "lng": l, "pk": peak[n][0], "pky": str(peak[n][1])}.items() if v is not None}} for n, x, y, q, l in choke])

# ---------- fresh water ----------
R = gpd.read_file(f"{NE}/ne_10m_rivers_lake_centerlines_scale_rank.geojson")
R = R[R["featurecla"].astype(str).str.contains("River|Lake Centerline", regex=True)]
R["geometry"] = R.geometry.simplify(0.01)
dump("rivers", [{"type": "Feature", "geometry": rc(r.geometry, 3), "properties": {k: v for k, v in {"n": r["name"] if isinstance(r["name"], str) else None, "sr": int(r["scalerank"])}.items() if v is not None}}
                 for _, r in R.iterrows() if r.geometry is not None and not r.geometry.is_empty])

Lk = gpd.read_file(f"{NE}/ne_10m_lakes.geojson")
Lk["km2"] = Lk.to_crs(EA).area / 1e6
saline = {"Caspian Sea", "Great Salt Lake", "Dead Sea", "Lake Urmia", "Lake Eyre", "Aral Sea", "Lake Turkana", "Qinghai Hu", "Lake Van"}
Lk["sal"] = Lk["name"].isin(saline).astype(int)
Lk["res"] = Lk["featurecla"].astype(str).str.contains("Reservoir").astype(int)
# per-country freshwater lake + reservoir area (equal-area intersection), saline lakes excluded
fresh = Lk[Lk["sal"] == 0].to_crs(EA)
inter = gpd.overlay(fresh[["res", "geometry"]], Cp[["key", "geometry"]], how="intersection", keep_geom_type=True)
inter["km2"] = inter.area / 1e6
for (k, res), v in inter.groupby(["key", "res"])["km2"].sum().items():
    add(k, "res_km2" if res else "lake_km2", float(v))
Lk["geometry"] = Lk.geometry.simplify(0.005)
dump("lakes", [{"type": "Feature", "geometry": rc(r.geometry, 3), "properties": {k: v for k, v in {"n": r["name"] if isinstance(r["name"], str) else None,
                 "km2": round(r["km2"]), "res": int(r["res"]) or None, "sal": int(r["sal"]) or None, "sr": int(r["scalerank"])}.items() if v is not None}}
                for _, r in Lk.iterrows() if r.geometry is not None and not r.geometry.is_empty])

G = gpd.read_file(f"{NE}/ne_10m_glaciated_areas.geojson")
gi = gpd.overlay(G.to_crs(EA)[["geometry"]], Cp[["key", "geometry"]], how="intersection", keep_geom_type=True)
gi["km2"] = gi.area / 1e6
for k, v in gi.groupby("key")["km2"].sum().items(): add(k, "ice_km2", float(v))
G["geometry"] = G.geometry.simplify(0.02)
dump("glaciers", [{"type": "Feature", "geometry": rc(g, 3), "properties": {}} for g in G.geometry if g is not None and not g.is_empty])

# ---------- geography / geopolitics ----------
Cs = C.copy(); Cs["geometry"] = Cs.geometry.simplify(0.01)
dump("countries", [{"type": "Feature", "id": i, "geometry": rc(r.geometry, 3), "properties": {"k": r["key"], "n": r["NAME"]}} for i, (_, r) in enumerate(Cs.iterrows())])
key_to_id = {r["key"]: i for i, (_, r) in enumerate(Cs.iterrows())}

BL = gpd.read_file(f"{NE}/ne_10m_admin_0_boundary_lines_land.geojson"); BL["geometry"] = BL.geometry.simplify(0.01)
dump("borders", [{"type": "Feature", "geometry": rc(g, 3), "properties": {}} for g in BL.geometry if g is not None and not g.is_empty])
os.remove(f"{D}/land.geojson") if os.path.exists(f"{D}/land.geojson") else None
LD = gpd.read_file(f"{NE}/ne_50m_land.geojson"); LD["geometry"] = LD.geometry.simplify(0.01)
dump("land", [{"type": "Feature", "geometry": rc(g, 3), "properties": {}} for g in LD.geometry if g is not None and not g.is_empty])

DA = gpd.read_file(f"{NE}/ne_10m_admin_0_disputed_areas.geojson"); DA["geometry"] = DA.geometry.simplify(0.005)
namecol = "NAME_LONG" if "NAME_LONG" in DA.columns else "NAME"
dump("disputed", [{"type": "Feature", "geometry": rc(r.geometry, 3), "properties": {"n": r[namecol], "nt": r.get("NOTE_BRK") if isinstance(r.get("NOTE_BRK"), str) else ""}}
                  for _, r in DA.iterrows() if r.geometry is not None and not r.geometry.is_empty])
DL = gpd.read_file(f"{NE}/ne_10m_admin_0_boundary_lines_disputed_areas.geojson"); DL["geometry"] = DL.geometry.simplify(0.005)
dump("disputed_lines", [{"type": "Feature", "geometry": rc(g, 3), "properties": {}} for g in DL.geometry if g is not None and not g.is_empty])
MI = gpd.read_file(f"{NE}/ne_10m_admin_0_boundary_lines_maritime_indicator.geojson"); MI["geometry"] = MI.geometry.simplify(0.01)
dump("maritime", [{"type": "Feature", "geometry": rc(g, 3), "properties": {}} for g in MI.geometry if g is not None and not g.is_empty])

# ---------- label points (rendered as HTML, no glyph server) ----------
labels = []
for _, r in C.iterrows():
    labels.append({"t": "c", "n": r["NAME"], "x": round(r["LABEL_X"], 3), "y": round(r["LABEL_Y"], 3), "z": float(r["MIN_LABEL"]), "k": r["key"]})
PP = gpd.read_file(f"{NE}/ne_10m_populated_places_simple.geojson")
PP = PP[PP["featurecla"].astype(str).str.contains("Admin-0 capital")]
for _, r in PP.iterrows():
    labels.append({"t": "p", "n": r["name"], "x": round(r.geometry.x, 3), "y": round(r.geometry.y, 3), "z": max(3.0, float(r["min_zoom"]))})
MP = gpd.read_file(f"{NE}/ne_10m_geography_marine_polys.geojson")
for _, r in MP.iterrows():
    if not isinstance(r["name"], str) or r.geometry is None: continue
    p = r.geometry.representative_point()
    z = float(r["min_label"]) if "min_label" in MP.columns and not pd.isna(r["min_label"]) else 2 + float(r["scalerank"])
    labels.append({"t": "m", "n": r["name"], "x": round(p.x, 3), "y": round(p.y, 3), "z": z})
GR = gpd.read_file(f"{NE}/ne_10m_geography_regions_polys.geojson"); GR.columns = [c.lower() if c != "geometry" else c for c in GR.columns]; GR = GR.set_geometry("geometry")
for _, r in GR.iterrows():
    if not isinstance(r["name"], str) or r.geometry is None: continue
    if str(r["featurecla"]) in ("Continent", "Island group", "Island", "Peninsula", "Pen/cape", "Isthmus", "Coast", "Delta", "Wetlands", "Gorge"): continue
    p = r.geometry.representative_point()
    z = float(r["min_label"]) if "min_label" in GR.columns and not pd.isna(r["min_label"]) else 2 + float(r["scalerank"])
    labels.append({"t": "g", "n": r["name"], "x": round(p.x, 3), "y": round(p.y, 3), "z": z, "f": str(r["featurecla"])})
json.dump(labels, open(f"{D}/labels.json", "w"), separators=(",", ":"), ensure_ascii=False)
print("labels", len(labels), {t: sum(l["t"] == t for l in labels) for t in "cpmg"})

# ---------- country totals ----------
out = {}
for _, r in C.iterrows():
    t = totals.get(r["key"], {})
    out[r["key"]] = {"n": r["NAME_LONG"], "pop": int(r["POP_EST"]) if not pd.isna(r["POP_EST"]) else None, "id": key_to_id[r["key"]],
                     **{k: round(v, 2) for k, v in t.items()}}
json.dump(out, open(f"{D}/country_totals.json", "w"), separators=(",", ":"), ensure_ascii=False)
unk = {k: v for k, v in totals.items() if k not in out and v}
print("country totals", len(out), "unmatched keys", list(unk)[:10])
for f in ["og", "coal", "iron", "mine_km2", "lng_exp", "lake_km2", "ice_km2"]:
    top = sorted(((v.get(f, 0), v["n"]) for v in out.values()), reverse=True)[:5]
    print(f, [(n, round(x)) for x, n in top])
