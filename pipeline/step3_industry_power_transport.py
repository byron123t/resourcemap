"""Third pass: transport lines, ports, power plants, heavy industry; extend country totals."""
import os
ROOT = os.environ.get("ATLAS_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json, os, math, re, warnings
import pandas as pd, geopandas as gpd, numpy as np
from shapely.geometry import mapping, Point, shape
from shapely.ops import linemerge
warnings.filterwarnings("ignore")

W = ROOT; D = f"{W}/site/data"; NE = f"{W}/raw/naturalearth"; GEM = f"{W}/raw/gem"
EA = "EPSG:6933"

def rc(geom, n):
    g = mapping(geom)
    def f(c):
        if isinstance(c[0], (list, tuple)): return [f(x) for x in c]
        return [round(c[0], n), round(c[1], n)]
    return {"type": g["type"], "coordinates": f(g["coordinates"])}

def clean(v):
    if v is None: return None
    if isinstance(v, (float, np.floating)):
        v = float(v)
        if math.isnan(v) or math.isinf(v): return None
        return float(v)
    if isinstance(v, (np.integer, int)) and not isinstance(v, bool): return int(v)
    s = str(v).strip()
    return None if s.lower() in ("", "nan", "none", "unknown", "n/a", ">0") else s

def num(v):
    try:
        f = float(str(v).replace(",", "").strip())
        return None if math.isnan(f) else f
    except Exception:
        return None

def feat(x, y, props):
    p = {k: clean(v) for k, v in props.items()}
    p = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in p.items() if v is not None}
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(x, 4), round(y, 4)]}, "properties": p}

def dump(name, feats):
    p = f"{D}/{name}.geojson"
    json.dump({"type": "FeatureCollection", "features": feats}, open(p, "w"), separators=(",", ":"), allow_nan=False, ensure_ascii=False)
    print(f"{name:18s} {len(feats):6d}  {os.path.getsize(p)/1e6:5.2f} MB")

def latlon(s):
    try:
        a, b = [float(x) for x in str(s).split(",")[:2]]
        return a, b
    except Exception:
        return None, None

C = gpd.read_file(f"{NE}/ne_50m_admin_0_countries.geojson")[["ADM0_A3", "geometry"]].rename(columns={"ADM0_A3": "key"}).to_crs(EA)
def assign(feats):
    g = gpd.GeoDataFrame(geometry=[Point(f["geometry"]["coordinates"]) for f in feats], crs=4326).to_crs(EA)
    j = gpd.sjoin_nearest(g, C, how="left", max_distance=400_000)
    j = j[~j.index.duplicated()]
    return list(j["key"].reindex(g.index))

totals = json.load(open(f"{D}/country_totals.json"))
def add(k, f, v):
    if not isinstance(k, str) or k not in totals or v is None: return
    totals[k][f] = round(totals[k].get(f, 0) + v, 3)

def tag(feats, fields):
    """attach country key and add operating quantities to totals"""
    for f, k in zip(feats, assign(feats)):
        if isinstance(k, str): f["properties"]["k"] = k
        if f["properties"].get("s") == "operating":
            for field, fn in fields:
                v = fn(f["properties"])
                if v: add(k, field, v)

LIVE = {"operating": "operating", "construction": "construction", "permitted": "proposed", "pre-permit": "proposed", "announced": "proposed",
        "proposed": "proposed", "mothballed": "mothballed", "shelved": "shelved"}

# ---------------- power plants ----------------
power = []
def fuelgroup(f):
    f = str(f).lower()
    for k, v in [("coal", "coal"), ("petcoke", "coal"), ("lignite", "coal"), ("nuclear", "nuclear"), ("hydro", "hydro"), ("wave", "hydro"), ("solar", "solar"),
                 ("wind", "wind"), ("geotherm", "geothermal"), ("bio", "bioenergy"), ("waste", "bioenergy"), ("gas", "gas"), ("lng", "gas"), ("oil", "oil"),
                 ("diesel", "oil"), ("fuel", "oil"), ("storage", "storage")]:
        if k in f: return v
    return "other"

def plants_from_units(df, loc, name, country, fuel, cap, status, lat, lon, start, owner, wiki):
    df = df.copy()
    df["_s"] = df[status].astype(str).str.lower().map(LIVE)
    df = df[df["_s"].notna()]
    df["_cap"] = df[cap].map(num).fillna(0)
    out = []
    for (lid, s), g in df.groupby([loc, "_s"]):
        r = g.iloc[0]
        if num(r[lat]) is None or num(r[lon]) is None: continue
        mw = g["_cap"].sum()
        out.append(feat(float(r[lon]), float(r[lat]), {
            "n": r[name], "c": r[country], "s": s, "f": fuel(r), "q": round(mw / 1000, 4) if mw else None, "mw": round(mw),
            "u": len(g), "sy": num(g[start].min()) if start else None, "o": r[owner] if owner else None, "w": r[wiki] if wiki else None}))
    return out

cp = pd.read_excel(f"{GEM}/Global-Coal-Plant-Tracker-July-2026.xlsx", "Units")
power += plants_from_units(cp, "GEM location ID", "Plant name", "Country/Area", lambda r: "coal", "Capacity (MW)", "Status", "Latitude", "Longitude", "Start year", "Parent", "Wiki URL")
og = pd.read_excel(f"{GEM}/Global Oil and Gas Plant Tracker (GOGPT) - August 2026.xlsx", "Gas & Oil Units")
power += plants_from_units(og, "GEM location ID", "Plant name", "Country/Area", lambda r: fuelgroup(r["Fuel"]), "Capacity (MW)", "Status", "Latitude", "Longitude", "Start year", "Parent(s)", "Wiki URL")
wri = pd.read_csv(f"{NE}/global_power_plant_database.csv", low_memory=False)
wri = wri[~wri["primary_fuel"].isin(["Coal", "Gas", "Oil", "Petcoke", "Cogeneration"])]
for _, r in wri.iterrows():
    mw = num(r["capacity_mw"]) or 0
    power.append(feat(r["longitude"], r["latitude"], {"n": r["name"], "c": r["country_long"], "s": "operating", "f": fuelgroup(r["primary_fuel"]),
                 "q": round(mw / 1000, 4) if mw else None, "mw": round(mw, 1), "sy": num(r["commissioning_year"]), "o": r["owner"], "src": "WRI 2021"}))
power = [p for p in power if p["properties"].get("mw", 0) >= 1]
tag(power, [("pw_gw", lambda p: p.get("q")), *[(f"pw_{fg}", (lambda fg: lambda p: p.get("q") if p.get("f") == fg else None)(fg)) for fg in ["coal", "gas", "oil", "nuclear", "hydro", "solar", "wind"]]])
print("power by fuel", pd.Series([p["properties"]["f"] for p in power]).value_counts().to_dict())
dump("power", power)

# ---------------- steel ----------------
sp = pd.read_excel(f"{GEM}/Plant-level data - Global Iron and Steel Tracker - September 2026 (V1).xlsx", "Plant data")
sc = pd.read_excel(f"{GEM}/Plant-level data - Global Iron and Steel Tracker - September 2026 (V1).xlsx", "Plant capacities and status")
sc["_st"] = sc["Nominal crude steel capacity (ttpa)"].map(num).fillna(0)
sc["_ir"] = sc["Nominal iron capacity (ttpa)"].map(num).fillna(0)
sc["_s"] = sc["Status"].astype(str).str.lower().map(lambda s: "operating" if "operating" in s else ("construction" if "construction" in s else ("proposed" if ("announced" in s or "proposed" in s) else ("mothballed" if "mothball" in s or "idle" in s else None))))
steel = []
spi = sp.set_index("GEM plant ID")
for (pid, s), g in sc[sc["_s"].notna()].groupby(["GEM plant ID", "_s"]):
    if pid not in spi.index: continue
    r = spi.loc[pid]
    if isinstance(r, pd.DataFrame): r = r.iloc[0]
    lat, lon = latlon(r["Coordinates"])
    if lat is None: continue
    st, ir = g["_st"].sum(), g["_ir"].sum()
    steel.append(feat(lon, lat, {"n": r["Plant name (English)"], "c": r["Country/area"], "s": s, "q": round(st / 1000, 3) if st else None,
                 "ir": round(ir / 1000, 3) if ir else None, "eq": "; ".join(sorted(set(str(x) for x in g["Main production equipment"].dropna())))[:80],
                 "o": r["Parent (English)"] if clean(r["Parent (English)"]) else r["Owner"], "w": r["GEM wiki page"], "prod": r["Steel products"]}))
tag(steel, [("steel", lambda p: p.get("q"))])
dump("steel", steel)

# ---------------- cement ----------------
cm = pd.read_excel(f"{GEM}/Plant-level data - Global Cement and Concrete Tracker - July 2026 - Standard Copy V1.xlsx", "Final data")
cement = []
for _, r in cm.iterrows():
    lat, lon = latlon(r["Coordinates"])
    if lat is None: continue
    s = str(r["Operating status"]).lower()
    s = "operating" if "operating" in s else ("construction" if "construction" in s else ("proposed" if "announced" in s or "proposed" in s else ("mothballed" if "mothball" in s else None)))
    if not s: continue
    cement.append(feat(lon, lat, {"n": r["Plant name (English)"], "c": r["Country/area"], "s": s, "q": num(r["Cement capacity (million metric tonnes per annum)"]),
                  "ck": num(r["Clinker capacity (million metric tonnes per annum)"]), "pt": r["Plant type"], "o": r["Parent"], "w": r["GEM wiki page"]}))
tag(cement, [("cement", lambda p: p.get("q"))])
dump("cement", cement)

# ---------------- chemicals ----------------
ch = pd.read_excel(f"{GEM}/Plant-level data - Global Chemicals Inventory - November 2025 V2.xlsx", "Plant data")
chem = []
for _, r in ch.iterrows():
    lat, lon = latlon(r["Coordinates"])
    if lat is None: continue
    chem.append(feat(lon, lat, {"n": r["Plant name (English)"], "c": r["Country/area"], "s": "operating", "q": 1, "pp": r["Primary products"],
                 "sp": r["Secondary products"], "fs": r["Feedstock"], "o": r["Owner (English)"], "w": r["GEM wiki page"]}))
tag(chem, [("chem_n", lambda p: 1), ("ammonia_n", lambda p: 1 if "ammonia" in str(p.get("pp", "")) else None)])
dump("chemicals", chem)

# ---------------- ports ----------------
pt = gpd.read_file(f"{NE}/ne_10m_ports.geojson")
ports = [feat(g.x, g.y, {"n": r["name"], "sr": int(r["scalerank"])}) for (_, r), g in zip(pt.iterrows(), pt.geometry)]
tag([dict(f, properties=dict(f["properties"], s="operating")) for f in ports], [("ports", lambda p: 1)])
dump("ports", ports)

# ---------------- lines ----------------
def lines(gdf, tol, prec, props, minlen=0.0):
    out = []
    gdf = gdf[gdf.geometry.notna()]
    for _, r in gdf.iterrows():
        g = r.geometry.simplify(tol)
        if g.is_empty or g.length < minlen: continue
        out.append({"type": "Feature", "geometry": rc(g, prec), "properties": {k: v for k, v in props(r).items() if v is not None}})
    return out

sl = gpd.read_file(f"{NE}/Shipping_Lanes_v1.geojson")
lanes = []
for _, r in sl.iterrows():
    parts = list(r.geometry.geoms) if r.geometry.geom_type.startswith("Multi") else [r.geometry]
    for p in parts:
        g = p.simplify(0.05)
        if g.length < 0.2: continue
        lanes.append({"type": "Feature", "geometry": rc(g, 2), "properties": {"t": r["Type"]}})
dump("shipping", lanes)

rr = gpd.read_file(f"{NE}/ne_10m_railroads.geojson")
rr = rr[rr["featurecla"] == "Railroad"]
rail = lines(rr, 0.01, 3, lambda r: {"sr": int(r["scalerank"]), "e": 1 if r["electric"] == 1 else None}, 0.02)
dump("rail", rail)

rd = gpd.read_file(f"{NE}/ne_10m_roads.geojson")
rd = rd[(rd["featurecla"] == "Road") & ((rd["type"].isin(["Major Highway", "Beltway"])) | (rd["expressway"] == 1) | (rd["scalerank"] <= 5))]
roads = lines(rd, 0.01, 3, lambda r: {"sr": int(r["scalerank"]), "x": 1 if r["expressway"] == 1 or r["type"] == "Major Highway" else None}, 0.02)
dump("roads", roads)

json.dump(totals, open(f"{D}/country_totals.json", "w"), separators=(",", ":"), ensure_ascii=False)
for f in ["pw_gw", "pw_nuclear", "pw_hydro", "steel", "cement", "chem_n", "ammonia_n", "ports"]:
    top = sorted(((v.get(f, 0), v["n"]) for v in totals.values()), reverse=True)[:5]
    print(f, [(n, round(x, 1)) for x, n in top])
