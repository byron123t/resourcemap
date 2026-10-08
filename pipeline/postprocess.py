"""Post-processing steps that run between the build steps (see run_all.sh).

Usage: python postprocess.py <step>
  mining     after step1: split the mining-footprint polygons into 3 longitude bands
             (loaded lazily by the page) and write compact centroid points
  pipelines  after step2: drop tiny/duplicate pipeline parts, merge, simplify, round coords
  power      after step3: slim power-plant properties
  water      after step4: simplify Aqueduct basin polygons
  clean      last: strip NaN values and U+FFFD characters from every data file
"""
import json, math, os, re, sys, glob
ROOT = os.environ.get("ATLAS_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
D = f"{ROOT}/site/data"

def rc(c, n):
    if isinstance(c[0], (list, tuple)): return [rc(x, n) for x in c]
    return [round(c[0], n), round(c[1], n)]

def mining():
    mp = json.load(open(f"{D}/mining_polygons.geojson"))["features"]
    bands = {"w": [], "c": [], "e": []}
    for f in mp:
        c = f["geometry"]["coordinates"]
        while isinstance(c[0][0], list): c = c[0]
        x = c[0][0]
        f["properties"].pop("c", None)
        bands["w" if x < -30 else ("c" if x < 60 else "e")].append(f)
    for k, v in bands.items():
        json.dump({"type": "FeatureCollection", "features": v}, open(f"{D}/mining_polygons_{k}.geojson", "w"), separators=(",", ":"))
    pts = json.load(open(f"{D}/mining_points.geojson"))["features"]
    json.dump([[f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1], f["properties"]["a"]] for f in pts],
              open(f"{D}/mining_points.json", "w"), separators=(",", ":"))
    os.remove(f"{D}/mining_polygons.geojson"); os.remove(f"{D}/mining_points.geojson")

def pipelines():
    from shapely.geometry import shape, mapping
    from shapely.ops import linemerge
    d = json.load(open(f"{D}/pipelines.geojson")); out = []
    for f in d["features"]:
        g = shape(f["geometry"])
        parts = list(g.geoms) if g.geom_type.startswith("Multi") else [g]
        seen, keep = set(), []
        for p in parts:
            if p.length < 0.02: continue
            c = tuple(map(tuple, p.coords))
            if c in seen or c[::-1] in seen: continue
            seen.add(c); keep.append(p)
        if not keep: continue
        m = (linemerge(keep) if len(keep) > 1 else keep[0]).simplify(0.04)
        mm = mapping(m)
        f["geometry"] = {"type": mm["type"], "coordinates": rc(json.loads(json.dumps(mm["coordinates"])), 2)}
        out.append(f)
    d["features"] = out
    json.dump(d, open(f"{D}/pipelines.geojson", "w"), separators=(",", ":"))

def power():
    d = json.load(open(f"{D}/power.geojson"))
    for f in d["features"]:
        p = f["properties"]
        if p.pop("src", None): p["r"] = 1          # r=1 → came from the WRI 2021 database
        p.pop("u", None)
        if "o" in p and len(p["o"]) > 40: p["o"] = p["o"][:40] + "…"
        f["geometry"]["coordinates"] = [round(x, 3) for x in f["geometry"]["coordinates"]]
    json.dump(d, open(f"{D}/power.geojson", "w"), separators=(",", ":"), ensure_ascii=False)

def water():
    from shapely.geometry import shape, mapping, MultiPolygon
    d = json.load(open(f"{D}/water_risk.geojson")); out = []
    for f in d["features"]:
        g = shape(f["geometry"]).simplify(0.09, preserve_topology=True)
        parts = [p for p in (list(g.geoms) if g.geom_type == "MultiPolygon" else [g]) if p.geom_type == "Polygon" and p.area > 0.004]
        if not parts: continue
        g = parts[0] if len(parts) == 1 else MultiPolygon(parts)
        m = mapping(g); f["geometry"] = {"type": m["type"], "coordinates": rc(m["coordinates"], 2)}
        out.append(f)
    d["features"] = out
    json.dump(d, open(f"{D}/water_risk.geojson", "w"), separators=(",", ":"))

def clean():
    for f in glob.glob(f"{D}/*.json") + glob.glob(f"{D}/*.geojson"):
        s = open(f, encoding="utf-8").read()
        s2 = re.sub(r'"[A-Za-z_]+":NaN,?', "", s).replace(",}", "}").replace("�", "")
        if s2 != s: open(f, "w", encoding="utf-8").write(s2); print("cleaned", os.path.basename(f))

if __name__ == "__main__":
    {"mining": mining, "pipelines": pipelines, "power": power, "water": water, "clean": clean}[sys.argv[1]]()
