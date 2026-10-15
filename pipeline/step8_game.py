"""Step 8: world file for the Singularity game (site/singularity.html). Stdlib only, no downloads.

Reads layers the earlier steps wrote to site/data/ and condenses them into one per-country table
plus the links the AI spreads along:
  countries.geojson, country_totals.json (population, power), domains.json (internet, servers, chip trade),
  datacenters.geojson, cloud.geojson, fabs.geojson, chip_sites.geojson, cables.geojson, air.json

Writes site/data/game/world.json:
  c      {iso: {n, x, y, pop, net, srv, gw, dc, cloud, fab, cap, nm, chip:[site ids], crit}}
  nb     land neighbours  [[a, b], ...]   (countries whose outlines share border vertices)
  cab    cable links      [[a, b, cable count], ...]
  air    air links        [[a, b, routes], ...]   (OpenFlights 2014, same as air.json)
  dcs    datacentre points [[lng, lat, iso], ...]
"""
import json, math, os
from collections import defaultdict

ROOT = os.environ.get("ATLAS_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
D = f"{ROOT}/site/data"
OUT = f"{D}/game"; os.makedirs(OUT, exist_ok=True)

def load(name):
    with open(f"{D}/{name}", encoding="utf-8") as f:
        return json.load(f)

def polys(geom):
    return [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]

def ring_area_centroid(ring):
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        k = x0 * y1 - x1 * y0
        a += k; cx += (x0 + x1) * k; cy += (y0 + y1) * k
    if abs(a) < 1e-12:
        return 0.0, ring[0][0], ring[0][1]
    return abs(a) / 2, cx / (3 * a), cy / (3 * a)

def in_ring(x, y, ring):
    inside = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside

countries = load("countries.geojson")["features"]
totals = load("country_totals.json")
dom = load("domains.json")["countries"]

shapes = []   # (iso, bbox, [polygon rings])
C = {}
for f in countries:
    iso = f["properties"]["k"]
    ps = polys(f["geometry"])
    best = max((ring_area_centroid(p[0]) for p in ps), key=lambda t: t[0])
    xs = [pt[0] for p in ps for pt in p[0]]; ys = [pt[1] for p in ps for pt in p[0]]
    shapes.append((iso, (min(xs), min(ys), max(xs), max(ys)), ps))
    t = totals.get(iso, {}); d = dom.get(iso, {})
    C[iso] = {
        "n": f["properties"]["n"], "x": round(best[1], 2), "y": round(best[2], 2),
        "pop": t.get("pop") or 0, "net": d.get("net", 0), "srv": d.get("srv", 0),
        "gw": round(t.get("pw_gw") or 0, 1), "nuc": round(t.get("pw_nuclear") or 0, 1),
        "dc": 0, "cloud": 0, "fab": 0, "cap": 0, "nm": 0, "chip": [],
        "crit": sum((t.get("crit_m") or {}).get(m, 0) for m in ("gallium", "germanium", "REE", "rare earths", "tungsten", "PGE", "tantalum", "cobalt", "lithium")),
        "chipX": d.get("chipX", 0),
    }

def country_at(x, y):
    for iso, (x0, y0, x1, y1), ps in shapes:
        if x0 <= x <= x1 and y0 <= y <= y1 and any(in_ring(x, y, p[0]) for p in ps):
            return iso
    return None

def nearest_country(x, y, maxd=3.0):
    iso = country_at(x, y)
    if iso:
        return iso
    best, bd = None, maxd * maxd
    for iso, (x0, y0, x1, y1), ps in shapes:
        if x < x0 - maxd or x > x1 + maxd or y < y0 - maxd or y > y1 + maxd:
            continue
        for p in ps:
            for px, py in p[0][::4]:
                dd = (px - x) ** 2 + (py - y) ** 2
                if dd < bd:
                    best, bd = iso, dd
    return best

# land neighbours: outlines that share vertices on a 0.05° grid
cells = defaultdict(set)
for iso, _, ps in shapes:
    for p in ps:
        for x, y in p[0]:
            cells[(round(x * 20), round(y * 20))].add(iso)
pairs = defaultdict(int)
for s in cells.values():
    if 1 < len(s) < 5:
        s = sorted(s)
        for i in range(len(s)):
            for j in range(i + 1, len(s)):
                pairs[(s[i], s[j])] += 1
nb = sorted([a, b] for (a, b), k in pairs.items() if k >= 3)

# datacentres and cloud regions
dcs = []
for f in load("datacenters.geojson")["features"]:
    x, y = f["geometry"]["coordinates"][:2]
    iso = country_at(x, y)
    if iso:
        C[iso]["dc"] += 1
        dcs.append([round(x, 3), round(y, 3), iso])
for f in load("cloud.geojson")["features"]:
    cc = f["properties"].get("cc")
    if cc in C:
        C[cc]["cloud"] += 1

# fabs: operating capacity in 300 mm wafer equivalents per month, best node
for f in load("fabs.geojson")["features"]:
    p = f["properties"]; cc = p.get("cc")
    if cc not in C or p.get("s") not in ("operating", None):
        continue
    C[cc]["fab"] += 1
    C[cc]["cap"] += p.get("cap300") or 0
    nm = p.get("nm")
    if nm and (not C[cc]["nm"] or nm < C[cc]["nm"]):
        C[cc]["nm"] = nm
for f in load("chip_sites.geojson")["features"]:
    p = f["properties"]
    if p["cc"] in C:
        C[p["cc"]]["chip"].append(p["id"])

# cables: countries at the ends of each cable part
cab = defaultdict(int)
for f in load("cables.geojson")["features"]:
    g = f["geometry"]
    parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
    ends = set()
    for part in parts:
        for x, y in (part[0], part[-1]):
            iso = nearest_country(x, y)
            if iso:
                ends.add(iso)
    ends = sorted(ends)
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            cab[(ends[i], ends[j])] += 1
cab = sorted([a, b, k] for (a, b), k in cab.items())

air = [p for p in load("air.json")["pairs"] if p[0] in C and p[1] in C and p[0] != p[1]]

out = {"c": C, "nb": nb, "cab": cab, "air": air, "dcs": dcs}
with open(f"{OUT}/world.json", "w", encoding="utf-8") as f:
    json.dump(out, f, separators=(",", ":"))
print(f"world.json: {len(C)} countries, {len(nb)} borders, {len(cab)} cable links, {len(air)} air links, {len(dcs)} datacentres")
