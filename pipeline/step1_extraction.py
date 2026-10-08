
import os
ROOT = os.environ.get("ATLAS_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import json, glob, math, warnings, re, os
import pandas as pd, numpy as np, geopandas as gpd
from shapely import wkt
from shapely.geometry import mapping, shape
warnings.filterwarnings("ignore")

W = ROOT
OUT = f"{W}/site/data"; os.makedirs(OUT, exist_ok=True)
GEM = f"{W}/raw/gem"
UP = f"{W}/raw/maus2022"

def r(x, n=4):
    return round(float(x), n)

def num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except Exception:
        return None

def clean(v):
    if v is None: return None
    if isinstance(v, float) and math.isnan(v): return None
    s = str(v).strip()
    return s or None

def roundcoords(geom, n=4):
    g = mapping(geom)
    def rc(c):
        if isinstance(c[0], (list, tuple)): return [rc(x) for x in c]
        return [round(c[0], n), round(c[1], n)]
    return {"type": g["type"], "coordinates": rc(g["coordinates"])}

def dump(name, feats):
    p = f"{OUT}/{name}.geojson"
    json.dump({"type": "FeatureCollection", "features": feats}, open(p, "w"), separators=(",", ":"))
    print(name, len(feats), round(os.path.getsize(p) / 1e6, 2), "MB")

GAS_M3_TO_MMBOE = 35.3147 / 6000  # million m3 -> million boe (6,000 scf/boe)

# ---------- Oil & gas ----------
f = f"{GEM}/Global-Oil-and-Gas-Extraction-Tracker-March-2026.xlsx"
m = pd.read_excel(f, "Field-level main data")
res = pd.read_excel(f, "Field-level reserves data")
prod = pd.read_excel(f, "Field-level production data")

res["cls"] = res["Reserves classification"].astype(str).str.lower().str.strip()
pref = [("remaining", 0), ("2p", 1), ("proven and probable", 1), ("p50", 2), ("1p", 3), ("proved", 3), ("proven", 3), ("reserves", 4)]
def rank(c):
    if any(k in c for k in ["in place", "inplace", "eur", "ultimate", "contingent", "3p", "resource", "cumulative", "produced"]): return None
    for k, v in pref:
        if k in c: return v
    return None
res["rank"] = res["cls"].map(rank)
res = res[res["rank"].notna() & res["Quantity (converted)"].notna()]
def to_mmboe(row):
    u = str(row["Units (converted)"]); q = float(row["Quantity (converted)"])
    if "bbl" in u: return q
    if "m³" in u or "m3" in u: return q * GAS_M3_TO_MMBOE
    return None
res["mmboe"] = res.apply(to_mmboe, axis=1)
res["fd"] = res["Fuel description"].astype(str).str.lower()
reserves = {}
for uid, g in res.groupby("Unit ID"):
    best = g["rank"].min(); g = g[g["rank"] == best]
    g = g[g["Data Year"] == g["Data Year"].max()] if g["Data Year"].notna().any() else g
    liq = g[~g["fd"].str.contains("gas") | g["fd"].str.contains("condensate")]
    gas = g[g["fd"].str.contains("gas") & ~g["fd"].str.contains("condensate")]
    lv = liq.groupby("fd")["mmboe"].max().sum() if len(liq) else 0
    gv = gas["mmboe"].max() if len(gas) else 0
    oil_bbl = liq.groupby("fd")["mmboe"].max().sum() if len(liq) else None
    gas_m3 = gas["Quantity (converted)"].max() if len(gas) else None
    reserves[uid] = dict(total=lv + gv, oil=oil_bbl, gas=gas_m3, cls=g["Reserves classification"].iloc[0], yr=num(g["Data Year"].iloc[0]))

prod["fd"] = prod["Fuel description"].astype(str).str.lower()
production = {}
for uid, g in prod.groupby("Unit ID"):
    g = g[g["Quantity (converted)"].notna()]
    if not len(g): continue
    if g["Data Year"].notna().any():
        g = g[g["Data Year"] == g["Data Year"].max()]
    units = g["Units (converted)"].astype(str)
    liq = g[units.str.contains("bbl")]; gas = g[units.str.contains("m³|m3")]
    if len(liq):
        lq = liq.groupby("fd")["Quantity (converted)"].max()
        if "oil and condensate" in lq.index:
            lv = lq["oil and condensate"] + lq.get("ngl", 0)
        else:
            lv = lq.sum()
    else: lv = None
    gv = gas["Quantity (converted)"].max() if len(gas) else None
    production[uid] = dict(oil=lv, gas=gv, yr=num(g["Data Year"].iloc[0]),
                           total=(lv or 0) + (gv or 0) * GAS_M3_TO_MMBOE)

og_pts, og_poly = [], []
for _, row in m.iterrows():
    lat, lon = num(row["Latitude"]), num(row["Longitude"])
    uid = row["Unit ID"]
    rv = reserves.get(uid, {}); pv = production.get(uid, {})
    props = {
        "n": clean(row["Unit Name"]), "c": clean(row["Country/Area"]), "f": clean(row["Fuel type"]),
        "s": clean(row["Status"]), "o": clean(row["Operator"]), "b": clean(row["Basin"]),
        "sh": clean(row["Onshore/Offshore"]), "dy": num(row["Discovery year"]), "py": num(row["Production start year"]),
        "acc": clean(row["Location accuracy"]), "w": clean(row["Wiki URL (field)"]),
        "rt": r(rv["total"], 1) if rv.get("total") else None, "rc": rv.get("cls"), "ry": rv.get("yr"),
        "ro": r(rv["oil"], 1) if rv.get("oil") else None, "rg": r(rv["gas"], 0) if rv.get("gas") else None,
        "pt": r(pv["total"], 2) if pv.get("total") else None, "po": r(pv["oil"], 2) if pv.get("oil") else None,
        "pg": r(pv["gas"], 1) if pv.get("gas") else None, "pyr": pv.get("yr"),
    }
    props = {k: v for k, v in props.items() if v is not None}
    if lat is not None and lon is not None:
        og_pts.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r(lon), r(lat)]}, "properties": props})
    w = row["Field outline (WKT)"]
    if isinstance(w, str) and w.strip():
        try:
            g = wkt.loads(w).simplify(0.002, preserve_topology=True)
            if not g.is_empty:
                og_poly.append({"type": "Feature", "geometry": roundcoords(g), "properties": {"n": props.get("n"), "f": props.get("f")}})
        except Exception as e:
            pass
dump("oilgas", og_pts); dump("oilgas_outlines", og_poly)

# ---------- Coal ----------
c = pd.read_excel(f"{GEM}/Global Coal Mine Tracker, August 2026.xlsx", "Non-closed mines")
coal = []
for _, row in c.iterrows():
    lat, lon = num(row["Latitude"]), num(row["Longitude"])
    if lat is None or lon is None: continue
    props = {
        "n": clean(row["Mine Name"]), "c": clean(row["Country / Area"]), "s": clean(row["Status"]),
        "ct": clean(row["Coal Type"]), "cg": clean(row["Coal Grade"]), "mt": clean(row["Mine Type"]),
        "o": clean(row["Parent Company"]) or clean(row["Owners"]), "cf": clean(row["Coalfield"]),
        "cap": num(row["Capacity (Mtpa)"]), "pr": num(row["Production (Mtpa)"]), "pry": num(row["Year of Production"]),
        "rs": num(row["Total Reserves (Proven and Probable, Mt)"]), "rsc": num(row["Total Resource (Inferred, Indicated, Measured)"]),
        "acc": clean(row["Location Accuracy"]), "w": clean(row["GEM Wiki Page (ENG)"]),
    }
    props = {k: (r(v, 2) if isinstance(v, float) else v) for k, v in props.items() if v is not None}
    coal.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r(lon), r(lat)]}, "properties": props})
dump("coal", coal)

cb = []
for p in glob.glob(f"{W}/raw/gem/coal_boundaries/*.geojson"):
    gj = json.load(open(p))
    name = os.path.basename(p).rsplit(",", 2)[0]
    for ft in gj["features"]:
        cat = str(ft["properties"].get("mine feature category", "")).lower()
        if "boundary" not in cat or ft["geometry"] is None: continue
        g = shape(ft["geometry"]).simplify(0.0005, preserve_topology=True)
        cb.append({"type": "Feature", "geometry": roundcoords(g, 5), "properties": {"n": name}})
dump("coal_boundaries", cb)

# ---------- Iron ore ----------
i = pd.read_excel(f"{GEM}/Global Iron Ore Mines Tracker - September 2026 - Standard Copy (V1).xlsx", "Main Data")
iron = []
for _, row in i.iterrows():
    try:
        lat, lon = [float(x) for x in str(row["Coordinates"]).split(",")[:2]]
    except Exception:
        continue
    pr = None
    for y in ["2025", "2024", "2023", "2022"]:
        v = num(row[f"Production {y} (ttpa)"])
        if v is not None: pr, pry = v, int(y); break
    props = {
        "n": clean(row["Asset name (English)"]), "c": clean(row["Country/area"]), "s": clean(row["Operating status"]),
        "o": clean(row["Parent (English)"]) or clean(row["Owner (English)"]),
        "pr": r(pr / 1000, 2) if pr else None, "pry": pry if pr else None,
        "cap": r(num(row["Design capacity (ttpa)"]) / 1000, 2) if num(row["Design capacity (ttpa)"]) else None,
        "rs": r(num(row["Total reserves (proven and probable, thousand metric tonnes)"]) / 1000, 1) if num(row["Total reserves (proven and probable, thousand metric tonnes)"]) else None,
        "acc": clean(row["Coordinate accuracy"]), "w": clean(row["GEM wiki page"]),
    }
    props = {k: v for k, v in props.items() if v is not None}
    iron.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r(lon), r(lat)]}, "properties": props})
dump("iron", iron)

# ---------- Mining footprint (Maus et al. 2022) ----------
mp = gpd.read_file(glob.glob(f"{UP}/*global_mining_polygons_v2.gpkg")[0]).to_crs(4326)
print(mp.columns.tolist(), len(mp))
mp["geometry"] = mp.geometry.simplify(0.0003, preserve_topology=True)
feats, cents = [], []
for _, row in mp.iterrows():
    g = row.geometry
    if g is None or g.is_empty: continue
    a = r(row["AREA"], 3)
    feats.append({"type": "Feature", "geometry": roundcoords(g, 4), "properties": {"a": a, "c": row["COUNTRY_NAME"]}})
    pt = g.representative_point()
    cents.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r(pt.x, 3), r(pt.y, 3)]}, "properties": {"a": a}})
dump("mining_polygons", feats); dump("mining_points", cents)

# ---------- Basemap ----------
for name, src, tol in [("land", "ne_50m_land", 0.02), ("borders", "ne_50m_admin_0_boundary_lines_land", 0.02)]:
    b = gpd.read_file(f"{W}/raw/naturalearth/{src}.geojson")
    b["geometry"] = b.geometry.simplify(tol)
    dump(name, [{"type": "Feature", "geometry": roundcoords(g, 3), "properties": {}} for g in b.geometry if g is not None and not g.is_empty])
