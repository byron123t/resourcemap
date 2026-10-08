"""Fourth pass: Aqueduct 4.0 water risk, WHYMAP groundwater, USGS critical minerals; extend country totals."""
import os
ROOT = os.environ.get("ATLAS_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json, os, math, re, warnings, glob
import pandas as pd, geopandas as gpd, numpy as np
from shapely.geometry import mapping, Point
warnings.filterwarnings("ignore")

W = ROOT; D = f"{W}/site/data"; N = f"{W}/raw"; NE = f"{W}/raw/naturalearth"
UP = f"{W}/raw/whymap"
EA = "EPSG:6933"

def rc(geom, n):
    g = mapping(geom)
    def f(c):
        if isinstance(c[0], (list, tuple)): return [f(x) for x in c]
        return [round(c[0], n), round(c[1], n)]
    return {"type": g["type"], "coordinates": f(g["coordinates"])}

def ok(v):
    if v is None: return False
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): return False
    return str(v).strip() not in ("", "nan", "None")

def dump(name, feats):
    p = f"{D}/{name}.geojson"
    json.dump({"type": "FeatureCollection", "features": feats}, open(p, "w"), separators=(",", ":"), allow_nan=False, ensure_ascii=False)
    print(f"{name:18s} {len(feats):6d}  {os.path.getsize(p)/1e6:5.2f} MB")

totals = json.load(open(f"{D}/country_totals.json"))
C = gpd.read_file(f"{NE}/ne_50m_admin_0_countries.geojson")[["ADM0_A3", "geometry"]].rename(columns={"ADM0_A3": "key"}).to_crs(EA)

# ================= Aqueduct 4.0 =================
gdb = glob.glob(f"{N}/aqueduct/*/GDB/*.gdb")[0]
cols = ["pfaf_id", "gid_0", "area_km2", "bws_raw", "bws_score", "bws_cat", "bwd_cat", "iav_cat", "sev_cat", "gtd_cat", "gtd_raw", "rfr_cat", "cfr_cat", "drr_cat", "w_awr_def_tot_cat", "w_awr_def_tot_score"]
ba = gpd.read_file(gdb, layer="baseline_annual", columns=cols, ignore_geometry=True)
ba = pd.DataFrame(ba)
print("baseline rows", len(ba), ba["bws_cat"].value_counts().to_dict())

# country water stress: area-weighted mean of the 0–5 score over valid sub-basins (arid & low water use counted as 5? no — kept out)
v = ba[(ba["bws_score"] >= 0) & (ba["area_km2"] > 0)]
for g0, g in v.groupby("gid_0"):
    s = float((g["bws_score"] * g["area_km2"]).sum() / g["area_km2"].sum())
    hi = float(g.loc[g["bws_cat"] >= 3, "area_km2"].sum() / g["area_km2"].sum())
    # Aqueduct uses GADM ISO3; Natural Earth ADM0_A3 matches for nearly all
    if g0 in totals:
        totals[g0]["bws"] = round(s, 2)
        totals[g0]["bws_hi"] = round(hi * 100, 1)

# per-basin attributes (one row per pfaf_id: the largest piece)
ba = ba.sort_values("area_km2", ascending=False).drop_duplicates("pfaf_id").set_index("pfaf_id")
fu = gpd.read_file(gdb, layer="future_annual", columns=["pfaf_id", "bau30_ws_x_c", "bau50_ws_x_c", "bau80_ws_x_c", "pes50_ws_x_c", "opt50_ws_x_c"])
fu = fu[fu.geometry.notna()]
fu["geometry"] = fu.geometry.simplify(0.04, preserve_topology=True)
basins = []
for _, r in fu.iterrows():
    if r.geometry is None or r.geometry.is_empty: continue
    a = ba.loc[r["pfaf_id"]] if r["pfaf_id"] in ba.index else None
    p = {}
    if a is not None:
        for k_out, k_in in [("ws", "bws_cat"), ("gw", "gtd_cat"), ("dr", "drr_cat"), ("rf", "rfr_cat"), ("iv", "iav_cat"), ("sv", "sev_cat"), ("tot", "w_awr_def_tot_cat")]:
            if ok(a[k_in]): p[k_out] = int(a[k_in])
        if ok(a["bws_raw"]) and a["bws_raw"] >= 0: p["wr"] = round(float(a["bws_raw"]), 3)
    for k_out, k_in in [("f30", "bau30_ws_x_c"), ("f50", "bau50_ws_x_c"), ("f80", "bau80_ws_x_c"), ("p50", "pes50_ws_x_c"), ("o50", "opt50_ws_x_c")]:
        if ok(r[k_in]): p[k_out] = int(r[k_in])
    if not p: continue
    basins.append({"type": "Feature", "geometry": rc(r.geometry, 2), "properties": p})
dump("water_risk", basins)

# ================= WHYMAP groundwater =================
gw = gpd.read_file(f"{UP}/whymap_11_gw_resources_recharge.geojson")
RCH = {"very low (< 2)": 0, "low (2 - 20)": 1, "low - very low (< 20)": 1, "medium (20 - 100)": 2, "medium - very low (< 100)": 2,
       "high (100 - 300)": 3, "very high - high (> 100)": 3, "very high (> 300)": 4}
TYP = {"major groundwater basin": "m", "complex hydrogeological structures": "c", "local and shallow aquifers": "l"}
gw = gw[gw["ICE"] == 0]  # drop ice / no-data polygons
gw["geometry"] = gw.geometry.simplify(0.03, preserve_topology=True)
gwf = []
for _, r in gw.iterrows():
    if r.geometry is None or r.geometry.is_empty: continue
    gwf.append({"type": "Feature", "geometry": rc(r.geometry, 2), "properties": {"r": RCH.get(r["recharge"], 2), "t": TYP.get(r["aquif_type"], "l"), "rl": r["recharge"]}})
dump("groundwater", gwf)
# share of each country's land over a major groundwater basin
mj = gw[gw["aquif_type"] == "major groundwater basin"].to_crs(EA)
inter = gpd.overlay(mj[["geometry"]], C, how="intersection", keep_geom_type=True)
inter["km2"] = inter.area / 1e6
for k, s in inter.groupby("key")["km2"].sum().items():
    if k in totals: totals[k]["gw_basin_km2"] = round(float(s))

sal = gpd.read_file(f"{UP}/whymap_10_saline_gw.geojson")
sal["geometry"] = sal.geometry.simplify(0.03)
dump("saline_gw", [{"type": "Feature", "geometry": rc(g, 2), "properties": {}} for g in sal.geometry if g is not None and not g.is_empty])
ab = gpd.read_file(f"{UP}/whymap_1_gw_abstraction_mining.geojson")
dump("gw_mining", [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(g.x, 3), round(g.y, 3)]},
       "properties": {k: v for k, v in {"n": (r["name"] or "").strip() or None, "x": int(r["exploitati"]), "src": r["source"]}.items() if v}} for (_, r), g in zip(ab.iterrows(), ab.geometry)])

# ================= critical minerals =================
def canon(x):
    x = x.strip().lower()
    if not x: return []
    m = {"platinum-group elements": ["PGE"], "platinum": ["PGE"], "palladium": ["PGE"], "rhodium": ["PGE"], "iridium": ["PGE"], "pge": ["PGE"],
         "rare-earth elements": ["REE"], "ree": ["REE"], "scandium": ["REE"], "yttrium": ["REE"],
         "niobium and tantalum": ["niobium", "tantalum"], "zirconium and hafnium": ["zirconium", "hafnium"],
         "fluorite": ["fluorspar"], "palladium rhenium": ["PGE", "rhenium"], "tantalum tin": ["tantalum", "tin"], "aluminum": ["bauxite/aluminium"]}
    return m.get(x, [x])

crit = []
pp = gpd.read_file(f"{N}/usgs_critmin_global/PP1802_CritMin_pts.shp")
for _, r in pp.iterrows():
    ms = sorted({c for x in str(r["CRITICAL_M"]).split(";") for c in canon(x)})
    crit.append({"x": r.geometry.x, "y": r.geometry.y, "p": {"n": r["DEPOSIT_NA"], "c": r["LOCATION"], "cd": r["LOC_DETAIL"] if ok(r["LOC_DETAIL"]) else None,
                 "m": "|".join(ms), "dt": r["DEPOSIT_TY"], "src": "g"}})
us = gpd.read_file(f"{N}/usgs_critmin_us/critical_mineral_deposits_v3/critical_mineral_deposits_v3.shp")
def join(r, base):
    s = " ".join(str(r[c]) for c in [f"{base}{i}" for i in range(1, 5)] if c in r and ok(r[c]))
    return s[:600] if s else None
for _, r in us.iterrows():
    ms = sorted({c for x in str(r["CritMin"]).replace(";", ",").split(",") for c in canon(x)})
    crit.append({"x": r["Long_WGS84"], "y": r["Lat_WGS84"], "p": {"n": r["Deposit_na"], "c": "United States", "cd": r["State"], "m": "|".join(ms),
                 "dt": r["DepType"], "ms": r["MinSystem"], "cat": r["DepCat"], "rk": int(r["Rank"]) if ok(r["Rank"]) else None,
                 "pr": join(r, "Prduction"), "rs": join(r, "Resources"), "lk": r["Links"] if ok(r["Links"]) else None, "src": "u"}})
cf = []
for c in crit:
    p = {k: v for k, v in c["p"].items() if ok(v)}
    p["s"] = "operating"; p["q"] = 1
    cf.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(float(c["x"]), 4), round(float(c["y"]), 4)]}, "properties": p})
# country keys + totals
g = gpd.GeoDataFrame(geometry=[Point(f["geometry"]["coordinates"]) for f in cf], crs=4326).to_crs(EA)
j = gpd.sjoin_nearest(g, C, how="left", max_distance=400_000); j = j[~j.index.duplicated()]
for f, k in zip(cf, j["key"].reindex(g.index)):
    if isinstance(k, str):
        f["properties"]["k"] = k
        if k in totals:
            totals[k]["crit_n"] = totals[k].get("crit_n", 0) + 1
            ms = totals[k].setdefault("crit_m", {})
            for m in f["properties"]["m"].split("|"):
                if m: ms[m] = ms.get(m, 0) + 1
dump("critical_minerals", cf)
from collections import Counter
cnt = Counter(m for f in cf for m in f["properties"]["m"].split("|") if m)
print("commodities", cnt.most_common(40))
json.dump(dict(cnt.most_common()), open(f"{D}/crit_commodities.json", "w"))

json.dump(totals, open(f"{D}/country_totals.json", "w"), separators=(",", ":"), ensure_ascii=False)
for f in ["bws", "bws_hi", "gw_basin_km2", "crit_n"]:
    top = sorted(((v.get(f, 0), v["n"]) for v in totals.values()), reverse=True)[:6]
    print(f, [(n, round(x, 1)) for x, n in top])
