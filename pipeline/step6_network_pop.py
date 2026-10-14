"""Step 6: refineries, resource networks and population for the Scenarios mode.

Writes to site/data/:
  refineries.geojson   oil refineries with capacity   Climate TRACE v6 assets API (CC BY 4.0)
  country_energy.json  oil/gas/distillate balances    US EIA International bulk file (public domain)
  energy_graph.json    crude + gas flow network for the Scenarios solver, built from the layers above
  pop/*.bin            people per ~36 km² hexagon     Kontur Population 2023, H3 resolution 6 (CC BY 4.0)
  basins.json          river-basin links and people   HydroSHEDS HydroBASINS level 6 (free with attribution) + Kontur
  transport_graph.json road and rail networks with the cities on them, built from roads, rail and labels
Also adds kpop (Kontur people per country) to country_totals.json.

Merges its counts and sources into infra_meta.json (written by step 5).

The refinery, energy and transport sections need only the standard library; pop and water also need numpy and matplotlib
(for fast point-in-polygon tests).

Raw downloads are cached in raw/net/. The big ones (Kontur, HydroBASINS) are kept once downloaded; delete them to refresh.
Flags:
  --offline        use only the cache (fail if a required file is missing)
  --only=a,b       run only these sections (refineries, energy, pop, water, transport)
"""
import os
ROOT = os.environ.get("ATLAS_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import json, math, statistics, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

W = ROOT
OUT = f"{W}/site/data"; os.makedirs(OUT, exist_ok=True)
RAW = f"{W}/raw/net"; os.makedirs(RAW, exist_ok=True)
UA = "resourcemap-pipeline/1.0 (+https://github.com/byron123t/resourcemap)"
OFFLINE = "--offline" in sys.argv
ONLY = next((a.split("=", 1)[1].split(",") for a in sys.argv if a.startswith("--only=")), None)
FETCHLOG = f"{RAW}/_fetch.json"
fetchlog = json.load(open(FETCHLOG)) if os.path.exists(FETCHLOG) else {}
META = f"{OUT}/infra_meta.json"
meta = json.load(open(META)) if os.path.exists(META) else {}

def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------- HTTP with cache (same as step 5) ----------
def http(url, data=None, headers=None, timeout=120, tries=4, ua=UA):
    h = {"User-Agent": ua}; h.update(headers or {})
    body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429 or k == tries - 1: raise
            wait = int(e.headers.get("Retry-After", 0) or 0) or 5 * 2 ** k
            print(f"  HTTP {e.code}, retry in {wait}s"); time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if k == tries - 1: raise
            print(f"  {e}, retry in {5 * 2 ** k}s"); time.sleep(5 * 2 ** k)

def cached(name, url, ua=UA, **kw):
    """Return bytes of raw/net/<name>, downloading it unless --offline."""
    p = f"{RAW}/{name}"
    if not OFFLINE:
        print("fetch", url)
        b, hdr = http(url, ua=ua, **kw)
        open(p, "wb").write(b)
        fetchlog[name] = {"url": url, "retrieved": now_utc(),
                          "last_modified": hdr.get("Last-Modified") or hdr.get("last-modified")}
        json.dump(fetchlog, open(FETCHLOG, "w"), indent=1)
        time.sleep(1)
    if not os.path.exists(p): raise SystemExit(f"missing {p}; run without --offline")
    return open(p, "rb").read()

def fetch_big(name, url):
    """Path of raw/net/<name>, streamed to disk on first use and kept after that."""
    p = f"{RAW}/{name}"
    if os.path.exists(p): return p
    if OFFLINE: raise SystemExit(f"missing {p}; run without --offline")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    print("fetch", url)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r, open(p + ".part", "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b: break
            f.write(b)
        lm = r.headers.get("Last-Modified")
    os.replace(p + ".part", p)
    fetchlog[name] = {"url": url, "retrieved": now_utc(), "last_modified": lm}
    json.dump(fetchlog, open(FETCHLOG, "w"), indent=1)
    return p

def r4(x): return round(float(x), 4)

def pt(lon, lat, props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [r4(lon), r4(lat)]},
            "properties": {k: v for k, v in props.items() if v not in (None, "")}}

def dump(name, feats, src, **extra):
    p = f"{OUT}/{name}.geojson"
    json.dump({"type": "FeatureCollection", "features": feats}, open(p, "w", encoding="utf-8"),
              separators=(",", ":"), ensure_ascii=False)
    print(name, len(feats), round(os.path.getsize(p) / 1e6, 2), "MB")
    meta[f"{name}.geojson"] = {"count": len(feats), "bytes": os.path.getsize(p), "sources": src, **extra}

def srcinfo(fname, url=None, **kw):
    """Source entry for infra_meta.json. A file put in raw/net/ by hand has no fetch-log entry,
    so it falls back to the given URL and the file's modification time."""
    e = fetchlog.get(fname, {})
    p = f"{RAW}/{fname}"
    when = e.get("retrieved") or (os.path.exists(p) and
        datetime.fromtimestamp(os.path.getmtime(p), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")) or None
    d = {"url": e.get("url") or url, "retrieved": when}
    if e.get("last_modified"): d["version"] = e["last_modified"]
    d.update(kw)
    return d

def num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None

def countries():
    """ADM0_A3 key -> name, from the country layer step 1 wrote."""
    d = json.load(open(f"{OUT}/countries.geojson", encoding="utf-8"))
    return {f["properties"]["k"]: f["properties"]["n"] for f in d["features"]}

# Climate TRACE uses ISO 3166 alpha-3; the atlas keys are Natural Earth ADM0_A3. Overseas
# departments that Natural Earth folds into their parent country map to it.
ISO_FIX = {"MTQ": "FRA", "GLP": "FRA", "REU": "FRA", "GUF": "FRA", "SSD": "SDS", "XKX": "KOS", "PSE": "PSX"}

# =====================================================================
# 1. Oil refineries (Climate TRACE)
# =====================================================================
CT_URL = "https://api.climatetrace.org/v6/assets?subsectors=oil-and-gas-refining&limit=2000&year=2024"

def refinery_type(t):
    t = (t or "").lower()
    return "d" if t.startswith("deep") else "m" if t.startswith("medium") else "h" if t.startswith("hydroskim") else None

def refineries():
    raw = json.loads(cached("ct_refining.json", CT_URL, timeout=180))
    assets = raw["assets"] if isinstance(raw, dict) else raw
    names = countries()
    rows = []
    for a in assets:
        g = (a.get("Centroid") or {}).get("Geometry")
        if not g or len(g) < 2: continue
        k = a.get("Country"); k = ISO_FIX.get(k, k)
        es = a.get("EmissionsSummary") or [{}]
        bd = num(es[0].get("Capacity")) if (es[0].get("CapacityUnits") or "").lower().startswith("bbl") else None
        owner = next((o.get("CompanyName") for o in a.get("Owners") or [] if o.get("CompanyName")), None)
        rows.append({"id": f"r{a['Id']}", "n": (a.get("Name") or "").strip() or None, "k": k, "c": names.get(k),
                     "o": owner, "t": refinery_type(a.get("AssetType")), "bd": bd if bd and bd > 0 else None,
                     "x": g[0], "y": g[1]})
    # missing capacity: country median, else world median; flagged ci so the map can show it
    by_k = {}
    for r in rows:
        if r["bd"]: by_k.setdefault(r["k"], []).append(r["bd"])
    world = statistics.median(b for v in by_k.values() for b in v)
    imputed = 0
    for r in rows:
        if not r["bd"]:
            r["bd"] = statistics.median(by_k[r["k"]]) if r["k"] in by_k else world
            r["ci"] = 1; imputed += 1
    feats = [pt(r["x"], r["y"], {"n": r["n"], "c": r["c"], "k": r["k"], "id": r["id"], "o": r["o"], "s": "operating",
                                 "t": r["t"], "bd": round(r["bd"]), "q": round(r["bd"] / 1e6, 4), "ci": r.get("ci")})
             for r in rows]
    feats.sort(key=lambda f: -f["properties"]["q"])
    missing = sorted({r["k"] for r in rows if r["k"] not in names})
    if missing: print("  refineries: country keys not in countries.geojson:", missing)
    total = sum(f["properties"]["q"] for f in feats)
    print(f"  refineries: {len(feats)} sites, {total:.1f} Mb/d, {imputed} capacities imputed")
    dump("refineries", feats, [srcinfo("ct_refining.json", name="Climate TRACE v6 assets (oil-and-gas-refining)", licence="CC BY 4.0")],
         total_mbd=round(total, 2), imputed=imputed)

# =====================================================================
# 2. Energy: EIA country balances + crude/gas flow network for the Scenarios solver
# =====================================================================
EIA_URL = "https://www.eia.gov/opendata/bulk/INTL.zip"
# EIA International series (product-activity) -> short key; units in the comment
EIA_SERIES = {"57-1-TBPD": "cr",   # crude oil incl. lease condensate production, kb/d
              "55-1-TBPD": "lq",   # crude, NGPL and other liquids production, kb/d
              "54-2-TBPD": "pc",   # refined petroleum products consumption, kb/d
              "54-1-TBPD": "pp",   # refined petroleum products production, kb/d (series ends 2014)
              "65-2-TBPD": "dc",   # distillate fuel oil consumption, kb/d
              "65-1-TBPD": "dp",   # distillate fuel oil production, kb/d (series ends 2014)
              "26-1-BCM": "gp",    # dry natural gas production, bcm/yr
              "26-2-BCM": "gc",    # dry natural gas consumption, bcm/yr
              "26-3-BCM": "gi",    # dry natural gas imports, bcm/yr
              "26-4-BCM": "ge"}    # dry natural gas exports, bcm/yr
EIA_FIX = {"SSD": "SDS", "XKS": "KOS", "PSE": "PSX", **{k: "FRA" for k in ("MTQ", "GLP", "REU", "GUF")}}

def eia_balances():
    """ADM0_A3 -> {key: latest value since 2010, key+'y': its year}, from the EIA bulk file."""
    import io, zipfile
    z = zipfile.ZipFile(io.BytesIO(cached("eia_intl.zip", EIA_URL, timeout=300)))
    out = {}
    with z.open(z.namelist()[0]) as f:
        for line in io.TextIOWrapper(f, encoding="utf-8"):
            if '"series_id":"INTL.' not in line[:40]: continue
            d = json.loads(line); sid = d["series_id"]
            if not sid.endswith(".A"): continue
            a = sid[5:-2].split("-")  # product, activity, geography, unit
            if len(a) != 4 or len(a[2]) != 3: continue
            key = EIA_SERIES.get(f"{a[0]}-{a[1]}-{a[3]}")
            if not key: continue
            pts = sorted(((int(y), v) for y, v in d["data"] if isinstance(v, (int, float)) and y.isdigit() and int(y) >= 2010), reverse=True)
            if not pts: continue
            k = EIA_FIX.get(a[2], a[2]); c = out.setdefault(k, {})
            c[key] = round(c.get(key, 0) + pts[0][1], 3); c[key + "y"] = pts[0][0]
    return out

R_EARTH = 6371.0
def hav(x1, y1, x2, y2):
    p1, p2 = math.radians(y1), math.radians(y2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(x2 - x1) / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1, math.sqrt(a)))

class Grid:
    """Point bucket index in degrees, for near-neighbour searches."""
    def __init__(self, cell): self.c = cell; self.b = {}
    def add(self, x, y, v): self.b.setdefault((math.floor(x / self.c), math.floor(y / self.c)), []).append((x, y, v))
    def near(self, x, y, km):
        r = int(math.ceil(km / 111 / max(0.2, math.cos(math.radians(min(85, abs(y))))) / self.c)) + 1
        gx, gy = math.floor(x / self.c), math.floor(y / self.c)
        for i in range(gx - r, gx + r + 1):
            for j in range(gy - r, gy + r + 1):
                for px, py, v in self.b.get((i, j), ()):
                    d = hav(x, y, px, py)
                    if d <= km: yield d, v

def country_index():
    """Point-in-polygon lookup against countries.geojson, with bounding boxes."""
    polys = []
    for f in json.load(open(f"{OUT}/countries.geojson", encoding="utf-8"))["features"]:
        g = f["geometry"]
        for p in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]):
            xs = [c[0] for c in p[0]]; ys = [c[1] for c in p[0]]
            polys.append((min(xs), min(ys), max(xs), max(ys), p, f["properties"]["k"]))
    def inside(x, y, ring):
        n, j, c = len(ring), len(ring) - 1, False
        for i in range(n):
            xi, yi = ring[i]; xj, yj = ring[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi: c = not c
            j = i
        return c
    def lookup(x, y):
        for x0, y0, x1, y1, p, k in polys:
            if x0 <= x <= x1 and y0 <= y <= y1 and inside(x, y, p[0]) and not any(inside(x, y, h) for h in p[1:]): return k
        return None
    return lookup

def neighbours():
    """ADM0_A3 pairs that share a border vertex (to 0.01 degree)."""
    owner = {}
    adj = set()
    for f in json.load(open(f"{OUT}/countries.geojson", encoding="utf-8"))["features"]:
        k, g = f["properties"]["k"], f["geometry"]
        seen = set()
        for p in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]):
            for ring in p:
                for x, y in ring: seen.add((round(x, 2), round(y, 2)))
        for v in seen:
            o = owner.setdefault(v, k)
            if o != k: adj.add(tuple(sorted((o, k))))
    return adj

def seg_dist_km(px, py, ax, ay, bx, by):
    """Point to segment distance, equirectangular around the point (fine below a few hundred km)."""
    c = math.cos(math.radians(py))
    ax, bx, qx = (ax - px) * c, (bx - px) * c, 0
    ay, by = ay - py, by - py
    dx, dy = bx - ax, by - ay
    t = 0 if dx == dy == 0 else max(0, min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx, ay + t * dy) * 111.2

# Graph constants. Costs are in km-equivalents: a km of pipeline costs 1, a km at sea 0.15.
SNAP = 0.1              # degrees: pipeline ends this close are joined
SEA_SNAP = 0.6          # degrees: shipping-lane vertices this close are joined
GAS_BOE = 59.9          # bcm/yr per million boe/d
LNG_BCM = 1.36          # bcm of gas per Mt of LNG
PORT_KBD = {"L": 3000, "M": 1500, "S": 600, "V": 250}   # crude a port can load or land, kb/d, by WPI harbour size (illustrative)
XB_CAP = {"o": 100, "g": 2}                             # unmapped cross-border trade per land border: kb/d crude, bcm/yr gas
CHOKE_KM = 110          # lane segments this close to a chokepoint belong to it
CHOKE_SLUG = {"Strait of Malacca": "malacca", "Strait of Hormuz": "hormuz", "Suez Canal & SUMED": "suez", "Bab el-Mandeb": "bab",
              "Danish Straits": "danish", "Turkish Straits": "turkish", "Panama Canal": "panama", "Cape of Good Hope": "cape"}
# the shipping lanes stop short of the Black Sea, so its ports' links to the sea count as the Turkish Straits
CHOKE_BOX = {"turkish": (27.4, 40.9, 42.0, 47.5)}
# node kinds: J pipeline junction, F field cluster, R refinery, P oil port, X LNG export, I LNG import, S sea, H country hub, D gas demand
# edge kinds: p pipeline, l local link (gathering line, jetty), h domestic link via the country hub (stands in for unmapped pipes, rail, trucks),
#             b cross-border link between hubs, s sea lane, t port <-> sea, x liquefaction + loading, i unloading + regas

def energy():
    bal = eia_balances()
    names = countries()
    where = country_index()
    D = lambda n: json.load(open(f"{OUT}/{n}", encoding="utf-8"))["features"]
    nodes, edges, ids = [], [], {}
    def node(kind, x, y, k, **kw):
        nodes.append({"t": kind, "x": x, "y": y, "k": k, **kw}); return len(nodes) - 1
    def edge(a, b, kind, comm, km, cap=None, **kw):
        if a != b: edges.append({"a": a, "b": b, "t": kind, "c": comm, "km": max(1, round(km)), "cap": cap, **kw})

    # ---- pipelines: snap ends, split at T-junctions and facility taps ----
    pf = D("pipelines.geojson")
    parts = []   # (feature index, commodity, [[x,y],...])
    for i, f in enumerate(pf):
        p = f["properties"]
        if p.get("s") != "operating": continue
        g = f["geometry"]
        for line in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]):
            if len(line) >= 2: parts.append((i, "o" if p["f"] == "oil" else "g", line))
    # capacity per feature: oil kb/d, gas bcm/yr; missing -> median of the same pipeline name, else of the fuel
    def fcap(p):
        if p["f"] == "oil": return p["q"] * 1000 if p.get("q") else None
        return p.get("cap") or (p["q"] * GAS_BOE if p.get("q") else None)
    by_name, by_fuel = {}, {}
    for f in pf:
        p = f["properties"]
        if p.get("s") == "operating" and fcap(p):
            by_name.setdefault((p["f"], p["n"]), []).append(fcap(p)); by_fuel.setdefault(p["f"], []).append(fcap(p))
    caps, imputed_caps = {}, 0
    for i, f in enumerate(pf):
        p = f["properties"]
        if p.get("s") != "operating": continue
        c = fcap(p)
        if c: caps[i] = (c, 0)
        else:
            caps[i] = (statistics.median(by_name.get((p["f"], p["n"])) or by_fuel[p["f"]]), 1); imputed_caps += 1

    # every vertex, for T-snaps
    vgrid = Grid(SNAP)
    for pi_, (fi, comm, line) in enumerate(parts):
        for vi, (x, y) in enumerate(line): vgrid.add(x, y, (pi_, vi))
    splits = [set([0, len(line) - 1]) for _, _, line in parts]
    snap_km = SNAP * 111
    # an end with no other end nearby taps into the nearest vertex of another part of the same fuel
    endgrid = Grid(SNAP)
    for pi_, (fi, comm, line) in enumerate(parts):
        for vi in (0, len(line) - 1): endgrid.add(line[vi][0], line[vi][1], (pi_, vi))
    tsnaps = 0
    for pi_, (fi, comm, line) in enumerate(parts):
        for vi in (0, len(line) - 1):
            x, y = line[vi]
            if any(v[0] != pi_ and parts[v[0]][1] == comm for _, v in endgrid.near(x, y, snap_km)): continue
            best = min(((d, v) for d, v in vgrid.near(x, y, snap_km) if v[0] != pi_ and parts[v[0]][1] == comm), default=None)
            if best: splits[best[1][0]].add(best[1][1]); tsnaps += 1

    # facilities that tap a pipeline mid-line get a split there too (done below, before edges are cut)
    def tap(x, y, comm, km):
        best = min(((d, v) for d, v in vgrid.near(x, y, km) if parts[v[0]][1] == comm), default=None)
        if best: splits[best[1][0]].add(best[1][1]); return best[1], best[0]
        return None, None

    # ---- facilities ----
    # fields: clusters per country and 0.5 degree cell; supply scaled per country to EIA production
    fo, fg = {}, {}
    cl = {}
    for f in D("oilgas.geojson"):
        p = f["properties"]
        if p.get("s") not in ("operating", None) or not (p.get("po") or p.get("pg")): continue
        k = p.get("k"); x, y = f["geometry"]["coordinates"]
        key = (k, math.floor(x / 0.5), math.floor(y / 0.5))
        c = cl.setdefault(key, {"o": 0, "g": 0, "xs": 0, "ys": 0, "w": 0, "n": []})
        o, g = (p.get("po") or 0) * 1e6 / 365 / 1e3, (p.get("pg") or 0) / 1e3   # kb/d, bcm/yr
        w = o + g * 17.2 + 1e-6   # weight the cluster centre by output (1 bcm/yr ~ 17 kb/d oil equivalent)
        c["o"] += o; c["g"] += g; c["xs"] += x * w; c["ys"] += y * w; c["w"] += w; c["n"].append((o + g * 17.2, p.get("n")))
        fo[k] = fo.get(k, 0) + o; fg[k] = fg.get(k, 0) + g
    # per-country scale to EIA (kept when EIA has no figure)
    so = {k: (bal[k]["cr"] / v if bal.get(k, {}).get("cr") is not None and v > 0 else 1) for k, v in fo.items()}
    sg = {k: (bal[k]["gp"] / v if bal.get(k, {}).get("gp") is not None and v > 0 else 1) for k, v in fg.items()}
    taps = []   # (node, x, y, comm, km): facility nodes to tie to the pipeline network
    for (k, ix, iy), c in sorted(cl.items(), key=lambda kv: (str(kv[0][0]), kv[0][1], kv[0][2])):
        x, y = c["xs"] / c["w"], c["ys"] / c["w"]
        sup_o, sup_g = c["o"] * so.get(k, 1), c["g"] * sg.get(k, 1)
        if sup_o < 0.05 and sup_g < 0.001: continue
        top = max(c["n"], key=lambda t: t[0])[1]
        n = node("F", x, y, k, so=round(sup_o, 2) or None, sg=round(sup_g, 4) or None, n=(top or "") + (f" +{len(c['n']) - 1}" if len(c["n"]) > 1 else ""))
        ids[f"f:{k}:{ix}:{iy}"] = n
        if sup_o: taps.append((n, x, y, "o", 50))
        if sup_g: taps.append((n, x, y, "g", 50))

    # refineries: crude demand (nameplate kb/d; the solver scales it to world crude supply)
    for f in D("refineries.geojson"):
        p = f["properties"]; x, y = f["geometry"]["coordinates"]
        n = node("R", x, y, p.get("k"), dr=round(p["bd"] / 1000, 1), rt=p.get("t"), n=p.get("n"), id=p["id"])
        ids[p["id"]] = n; taps.append((n, x, y, "o", 50))

    # oil ports: WPI oil terminal flag or an oil terminal at least 9 m deep
    import csv
    hs_code = {"Large": "L", "Medium": "M", "Small": "S", "Very Small": "V"}
    for r in csv.DictReader(open(f"{W}/raw/infra/UpdatedPub150.csv", encoding="utf-8-sig")):
        depth = num(r["Oil Terminal Depth (m)"]) or 0
        if r["Facilities - Oil Terminal"] != "Yes" and depth < 9: continue
        x, y = num(r["Longitude"]), num(r["Latitude"])
        if x is None or y is None: continue
        k = where(x, y)
        if k is None:   # ports sit on the coast, so try a little inland
            k = next((where(x + dx, y + dy) for dx, dy in ((0.05, 0), (-0.05, 0), (0, 0.05), (0, -0.05)) if where(x + dx, y + dy)), None)
        hs = hs_code.get(r["Harbor Size"].strip(), "V")
        pid = f"p{int(num(r['World Port Index Number']))}"   # same number as ports_wpi.geojson's wpi
        n = node("P", r4(x), r4(y), k, hs=hs, cap=PORT_KBD[hs], n=r["Main Port Name"], id=pid)
        ids[pid] = n; taps.append((n, x, y, "o", 50))

    # LNG terminals (operating, with capacity)
    for i, f in enumerate(D("lng.geojson")):
        p = f["properties"]
        if p.get("s") != "operating" or not p.get("q") or p.get("ft") not in ("export", "import"): continue
        x, y = f["geometry"]["coordinates"]
        n = node("X" if p["ft"] == "export" else "I", x, y, p.get("k"), cap=round(p["q"] * LNG_BCM, 2), n=p.get("n"), id=f"l{i}")
        ids[f"l{i}"] = n; taps.append((n, x, y, "g", 50))

    # gas demand: EIA consumption, 65% spread by city population, 35% by operating gas power capacity (illustrative split)
    cities = [(l["x"], l["y"], l["k"], l["pop"]) for l in json.load(open(f"{OUT}/labels.json", encoding="utf-8"))
              if l["t"] == "p" and (l.get("pop") or 0) >= 100000 and l.get("k")]
    gasgw = [(f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1], f["properties"].get("k"), f["properties"].get("q") or 0)
             for f in D("power.geojson") if f["properties"].get("f") == "gas" and f["properties"].get("s") == "operating"]
    popk, gwk = {}, {}
    for x, y, k, w in cities: popk[k] = popk.get(k, 0) + w
    for x, y, k, w in gasgw: gwk[k] = gwk.get(k, 0) + w
    dcl = {}
    def dadd(x, y, k, share):
        if not k or share <= 0: return
        c = dcl.setdefault((k, math.floor(x / 2), math.floor(y / 2)), [0, 0, 0, 0])
        c[0] += share; c[1] += x * share; c[2] += y * share; c[3] += 1
    for x, y, k, w in cities:
        gc = bal.get(k, {}).get("gc") or 0
        if gc: dadd(x, y, k, gc * w / popk[k] * (0.65 if gwk.get(k) else 1))
    for x, y, k, w in gasgw:
        gc = bal.get(k, {}).get("gc") or 0
        if gc and gwk[k] > 0: dadd(x, y, k, gc * w / gwk[k] * (0.35 if popk.get(k) else 1))
    placed = {}
    for (k, ix, iy), (dg, xs, ys, cnt) in sorted(dcl.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        x, y = xs / dg, ys / dg
        n = node("D", r4(x), r4(y), k, dg=round(dg, 4))
        placed[k] = placed.get(k, 0) + dg; taps.append((n, x, y, "g", 100))

    # ---- country hubs (at the label point) ----
    hub = {}
    for l in json.load(open(f"{OUT}/labels.json", encoding="utf-8")):
        if l["t"] == "c" and l["k"] not in hub:
            k = l["k"]; b = bal.get(k, {})
            # production with no mapped field, and gas demand with no city or plant, sit at the hub
            ho = max(0, (b.get("cr") or 0) - fo.get(k, 0) * so.get(k, 1)) if k not in fo else 0
            hg = max(0, (b.get("gp") or 0) - fg.get(k, 0) * sg.get(k, 1)) if k not in fg else 0
            hd = (b.get("gc") or 0) if k not in placed else 0
            hub[k] = node("H", l["x"], l["y"], k, so=round(ho, 2) or None, sg=round(hg, 4) or None, dg=round(hd, 4) or None, n=names.get(k, k), id=f"h:{k}")
            ids[f"h:{k}"] = hub[k]

    # ---- taps: facilities onto the pipeline network (splits must be known before cutting edges) ----
    tapped = []
    for n, x, y, comm, km in taps:
        v, d = tap(x, y, comm, km)
        if v: tapped.append((n, v, d, comm))

    # ---- cut parts at splits into edges between snapped junctions ----
    # union-find on split vertices: ends within SNAP join; T-snapped ends join the vertex they tapped
    pts = []
    pid = {}
    for pi_, (fi, comm, line) in enumerate(parts):
        for vi in sorted(splits[pi_]):
            pid[(pi_, vi)] = len(pts); pts.append((line[vi][0], line[vi][1], comm))
    par = list(range(len(pts)))
    def find(a):
        while par[a] != a: par[a] = par[par[a]]; a = par[a]
        return a
    sg_ = Grid(SNAP)
    for i, (x, y, comm) in enumerate(pts): sg_.add(x, y, i)
    for i, (x, y, comm) in enumerate(pts):
        for d, j in sg_.near(x, y, snap_km):
            if j != i and pts[j][2] == comm: par[find(i)] = find(j)
    jnode = {}
    for i, (x, y, comm) in enumerate(pts):
        r = find(i)
        if r not in jnode:
            jnode[r] = node("J", r4(pts[r][0]), r4(pts[r][1]), None)
    J = lambda pi_, vi: jnode[find(pid[(pi_, vi)])]
    for pi_, (fi, comm, line) in enumerate(parts):
        cut = sorted(splits[pi_])
        for a, b in zip(cut, cut[1:]):
            km = sum(hav(*line[i], *line[i + 1]) for i in range(a, b))
            cap, ci = caps[fi]
            edge(J(pi_, a), J(pi_, b), "p", comm, km, round(cap, 3), ref=fi, ci=ci or None)
    for n, v, d, comm in tapped:
        edge(n, J(*v), "l", comm, d)
    # junction countries (for summaries and zone shocks)
    for nd in nodes:
        if nd["t"] == "J": nd["k"] = where(nd["x"], nd["y"])

    # local links between facilities of one fuel within 40 km (refinery next to its port or field, etc.)
    fgrid = Grid(0.5)
    for i, nd in enumerate(nodes):
        if nd["t"] in "FRPXID": fgrid.add(nd["x"], nd["y"], i)
    ok = {"o": set("FRP"), "g": set("FXID")}
    for i, nd in enumerate(nodes):
        if nd["t"] not in "FRPXID": continue
        for comm in "og":
            if nd["t"] not in ok[comm] or (nd["t"] == "F" and not nd.get("s" + comm)): continue
            near = sorted((d, j) for d, j in fgrid.near(nd["x"], nd["y"], 40) if j > i and nodes[j]["t"] in ok[comm]
                          and not (nodes[j]["t"] == "F" and not nodes[j].get("s" + comm)))
            for d, j in near[:4]: edge(i, j, "l", comm, d)

    # domestic links through the country hub, one way where the direction is obvious
    for i, nd in enumerate(nodes):
        k, t = nd["k"], nd["t"]
        if t in "JSH" or k not in hub: continue
        h = hub[k]; km = hav(nd["x"], nd["y"], nodes[h]["x"], nodes[h]["y"])
        if t == "F":
            if nd.get("so"): edge(i, h, "h", "o", km, dir=1)
            if nd.get("sg"): edge(i, h, "h", "g", km, dir=1)
        elif t == "R": edge(h, i, "h", "o", km, dir=1)
        elif t == "P": edge(h, i, "h", "o", km)
        elif t in "XD": edge(h, i, "h", "g", km, dir=1)
        elif t == "I": edge(i, h, "h", "g", km, dir=1)
    for a, b in sorted(neighbours()):
        if a in hub and b in hub:
            km = hav(nodes[hub[a]]["x"], nodes[hub[a]]["y"], nodes[hub[b]]["x"], nodes[hub[b]]["y"])
            for comm in "og": edge(hub[a], hub[b], "b", comm, km, XB_CAP[comm])

    # ---- sea: shipping lanes snapped at SEA_SNAP, joined across the antimeridian ----
    lanes = [f["geometry"]["coordinates"] for f in D("shipping.geojson")]
    lp = [(x, y) for line in lanes for x, y in line]
    par = list(range(len(lp)))
    lg = Grid(SEA_SNAP)
    for i, (x, y) in enumerate(lp): lg.add(x, y, i)
    for i, (x, y) in enumerate(lp):
        for d, j in lg.near(x, y, SEA_SNAP * 111):
            if j != i: par[find(j)] = find(i)
        if abs(x) >= 179.9:
            for d, j in lg.near(-x, y, SEA_SNAP * 111): par[find(j)] = find(i)
    snode = {}
    for i, (x, y) in enumerate(lp):
        r = find(i)
        if r not in snode: snode[r] = node("S", r4(lp[r][0]), r4(lp[r][1]), None)
    off = 0
    chokes = [(CHOKE_SLUG[f["properties"]["n"]], *f["geometry"]["coordinates"]) for f in D("chokepoints.geojson")]
    seen = set()
    for line in lanes:
        for i in range(len(line) - 1):
            a, b = snode[find(off + i)], snode[find(off + i + 1)]
            if a == b or (min(a, b), max(a, b)) in seen: continue
            seen.add((min(a, b), max(a, b)))
            (ax, ay), (bx, by) = line[i], line[i + 1]
            ch = next((s for s, cx, cy in chokes if abs(ax - bx) < 180 and seg_dist_km(cx, cy, ax, ay, bx, by) <= CHOKE_KM), None)
            edge(a, b, "s", "*", hav(ax, ay, bx, by), ch=ch)
        off += len(line)
    sea = [(i, nd["x"], nd["y"]) for i, nd in enumerate(nodes) if nd["t"] == "S"]
    sgrid = Grid(2)
    for i, x, y in sea: sgrid.add(x, y, i)
    def nearest_sea(x, y):
        for km in (300, 800, 2000):
            best = min(sgrid.near(x, y, km), default=None)
            if best: return best
        return None
    for i, nd in enumerate(nodes):
        if nd["t"] in "PXI":
            d, s = nearest_sea(nd["x"], nd["y"]) or (None, None)
            if s is None: continue
            ch = next((c for c, (x0, y0, x1, y1) in CHOKE_BOX.items() if x0 <= nd["x"] <= x1 and y0 <= nd["y"] <= y1), None)
            if nd["t"] == "P": edge(i, s, "t", "o", d, nd["cap"], ch=ch)
            elif nd["t"] == "X": edge(i, s, "x", "g", d, nd["cap"], dir=1, ch=ch)
            else: edge(s, i, "i", "g", d, nd["cap"], dir=1, ch=ch)

    # ---- self-check ----
    nF = sum(1 for n in nodes if n["t"] == "F"); nR = sum(1 for n in nodes if n["t"] == "R")
    tapF = len({n for n, v, d, c in tapped if nodes[n]["t"] == "F"}); tapR = len({n for n, v, d, c in tapped if nodes[n]["t"] == "R"})
    # facilities linked to anything besides their hub
    linked = set()
    for e in edges:
        if e["t"] in "lt": linked.add(e["a"]); linked.add(e["b"])
    linkR = sum(1 for i, n in enumerate(nodes) if n["t"] == "R" and i in linked)
    sup_o = sum(n.get("so") or 0 for n in nodes); sup_g = sum(n.get("sg") or 0 for n in nodes)
    dem_r = sum(n.get("dr") or 0 for n in nodes); dem_g = sum(n.get("dg") or 0 for n in nodes)
    print(f"  energy: {len(nodes)} nodes, {len(edges)} edges; {tsnaps} T-snaps; {imputed_caps} pipeline capacities imputed")
    print(f"  energy: fields {nF} ({tapF} on a pipeline), refineries {nR} ({tapR} on a pipeline, {linkR} on pipe/port/field)")
    print(f"  energy: crude supply {sup_o:,.0f} kb/d vs refinery nameplate {dem_r:,.0f}; gas supply {sup_g:,.0f} bcm vs demand {dem_g:,.0f}")
    if nR and linkR / nR < 0.8: raise SystemExit(f"only {linkR}/{nR} refineries reach a pipeline, port or field")

    # ---- compact: drop dangling pipeline spurs, merge pass-through junctions of one pipeline ----
    adj = {}
    for ei, e in enumerate(edges):
        adj.setdefault(e["a"], set()).add(ei); adj.setdefault(e["b"], set()).add(ei)
    dead = set()
    def other(ei, n): return edges[ei]["b"] if edges[ei]["a"] == n else edges[ei]["a"]
    stack = [i for i, n in enumerate(nodes) if n["t"] == "J" and len(adj.get(i, ())) <= 1]
    while stack:
        i = stack.pop()
        if i in dead or len(adj.get(i, ())) > 1: continue
        dead.add(i)
        for ei in list(adj.get(i, ())):
            j = other(ei, i); adj[j].discard(ei); adj[i].discard(ei); edges[ei] = None
            if nodes[j]["t"] == "J" and len(adj[j]) <= 1: stack.append(j)
    merged = 0
    for i, n in enumerate(nodes):
        if n["t"] != "J" or i in dead or len(adj.get(i, ())) != 2: continue
        e1, e2 = sorted(adj[i])
        A, B = edges[e1], edges[e2]
        if A["t"] != "p" or B["t"] != "p" or A["ref"] != B["ref"] or A["c"] != B["c"]: continue
        a, b = other(e1, i), other(e2, i)
        if a == b: continue
        A.update(a=a, b=b, km=A["km"] + B["km"], cap=min(A["cap"], B["cap"]))
        adj[b].discard(e2); adj[b].add(e1); edges[e2] = None; adj[i] = set(); dead.add(i); merged += 1
    keep = [i for i in range(len(nodes)) if i not in dead]
    remap = {o: n for n, o in enumerate(keep)}
    nodes = [nodes[i] for i in keep]
    edges = [dict(e, a=remap[e["a"]], b=remap[e["b"]]) for e in edges if e]
    ids = {k: remap[v] for k, v in ids.items() if v in remap}
    print(f"  energy: compacted to {len(nodes)} nodes, {len(edges)} edges ({len(dead) - merged} spur junctions dropped, {merged} merged)")

    # ---- write: columnar, coordinates x100; sparse columns as {index: value} ----
    ks = sorted({n["k"] for n in nodes if n["k"]})
    kix = {k: i for i, k in enumerate(ks)}
    g = {"v": 1, "generated": now_utc(), "countries": ks,
         "nodes": {"t": "".join(n["t"] for n in nodes), "x": [round(n["x"] * 100) for n in nodes], "y": [round(n["y"] * 100) for n in nodes],
                   "k": [kix[n["k"]] if n["k"] else -1 for n in nodes],
                   **{key: {i: round(n[key], r) for i, n in enumerate(nodes) if n.get(key)} for key, r in (("so", 2), ("sg", 4), ("dr", 1), ("dg", 4), ("cap", 2))}},
         "names": {i: n["n"] for i, n in enumerate(nodes) if n.get("n")},
         "rt": {i: n["rt"] for i, n in enumerate(nodes) if n.get("rt")},
         "ids": ids,
         "edges": {"a": [e["a"] for e in edges], "b": [e["b"] for e in edges], "t": "".join(e["t"] for e in edges), "c": "".join(e["c"] for e in edges),
                   "km": [e["km"] for e in edges], "cap": [e["cap"] or 0 for e in edges],
                   "dir": [i for i, e in enumerate(edges) if e.get("dir")], "ci": [i for i, e in enumerate(edges) if e.get("ci")],
                   "ref": {i: e["ref"] for i, e in enumerate(edges) if e.get("ref") is not None}},
         "choke": {s: [i for i, e in enumerate(edges) if e.get("ch") == s] for s in CHOKE_SLUG.values()},
         "consts": {"snap": SNAP, "seaSnap": SEA_SNAP, "portKbd": PORT_KBD, "xbCap": XB_CAP, "lngBcm": LNG_BCM, "gasBoe": GAS_BOE, "chokeKm": CHOKE_KM}}
    p = f"{OUT}/energy_graph.json"
    json.dump(g, open(p, "w"), separators=(",", ":"), ensure_ascii=False)
    print("energy_graph.json", round(os.path.getsize(p) / 1e6, 2), "MB")

    # country balances: EIA figures plus the field scale factors and distillate yield
    ce = {}
    for k in sorted((set(bal) | set(fo) | set(fg)) - {None}):
        b = dict(bal.get(k, {}))
        if k in so: b["fo"] = round(so[k], 3)
        if k in sg: b["fg"] = round(sg[k], 3)
        if b.get("pp") and b.get("dp"): b["dy"] = round(min(0.5, max(0.2, b["dp"] / b["pp"])), 3)
        if k in names or k in hub: ce[k] = b
    p2 = f"{OUT}/country_energy.json"
    json.dump({"generated": now_utc(), "units": {"cr lq pc pp dc dp": "thousand barrels per day", "gp gc gi ge": "billion cubic metres per year",
                                                 "fo fg": "EIA production / GEM field production", "dy": "distillate share of refinery output (2014)"},
               "countries": ce}, open(p2, "w"), separators=(",", ":"))
    print("country_energy.json", round(os.path.getsize(p2) / 1e6, 2), "MB")
    src = [srcinfo("eia_intl.zip", url="https://www.eia.gov/opendata/bulk/INTL.zip", name="US EIA International Energy Data (bulk INTL.zip)", licence="public domain")]
    meta["energy_graph.json"] = {"count": len(nodes), "edges": len(edges), "bytes": os.path.getsize(p), "sources": src + [
        {"name": "built from pipelines, oilgas, refineries, ports_wpi, lng, shipping, chokepoints, power, labels"}]}
    meta["country_energy.json"] = {"count": len(ce), "bytes": os.path.getsize(p2), "sources": src}

# =====================================================================
# 3. Population (Kontur H3 r6) and 4. river basins (HydroBASINS level 6)
# =====================================================================
KONTUR = ("kontur/kontur_population_20231101_r6.gpkg.gz",
          "https://geodata-eu-central-1-kontur-public.s3.eu-central-1.amazonaws.com/kontur_datasets/kontur_population_20231101_r6.gpkg.gz")
HYBAS = [(f"hydrobasins/hybas_{r}_lev06_v1c.zip", f"https://data.hydrosheds.org/file/hydrobasins/standard/hybas_{r}_lev06_v1c.zip")
         for r in ("af", "ar", "as", "au", "eu", "gr", "na", "sa", "si")]
POP_TILE = 10      # degrees per pop/*.bin file
POP_Q = 4400       # stored value = round(ln(1 + people) * POP_Q), so 16 bits cover 2.9 million people per hexagon

class PolyIndex:
    """Which polygon contains each of many points. Points are sorted by x so each polygon only tests the points under
    its bounding box; matplotlib's point-in-path test is even-odd, so holes and multi-part rings work as one path."""
    def __init__(self, xs, ys):
        import numpy as np
        self.np = np
        self.o = np.argsort(xs, kind="stable"); self.xs = xs[self.o]; self.ys = ys[self.o]
        self.hit = np.full(len(xs), -1, np.int32)
    def add(self, rings, v):
        from matplotlib.path import Path
        np = self.np
        rings = [np.asarray(r, float) for r in rings if len(r) >= 4]
        if not rings: return
        allv = np.concatenate(rings)
        (x0, y0), (x1, y1) = allv.min(0), allv.max(0)
        a, b = np.searchsorted(self.xs, x0, "left"), np.searchsorted(self.xs, x1, "right")
        if a == b: return
        sel = np.nonzero((self.ys[a:b] >= y0) & (self.ys[a:b] <= y1) & (self.hit[a:b] < 0))[0] + a
        if not len(sel): return
        codes = np.concatenate([[Path.MOVETO] + [Path.LINETO] * (len(r) - 1) for r in rings]).astype(np.uint8)
        inside = Path(allv, codes).contains_points(np.column_stack([self.xs[sel], self.ys[sel]]))
        self.hit[sel[inside]] = v
    def result(self):
        out = self.np.empty_like(self.hit); out[self.o] = self.hit
        return out

def ring_km2(r):
    """Area of a lon/lat ring on a sphere (km²)."""
    a = 0.0
    for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]):
        a += math.radians(x2 - x1) * (2 + math.sin(math.radians(y1)) + math.sin(math.radians(y2)))
    return abs(a) * 6371.0088 ** 2 / 2

def polys_of(g):
    """Polygon ring lists of a GeoJSON Polygon or MultiPolygon."""
    return g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]

def kontur_points():
    """(lon, lat, people) arrays: the centre of each H3 hexagon, read from the GeoPackage's R-tree (EPSG:3857 boxes)."""
    import gzip, shutil, sqlite3, tempfile
    import numpy as np
    gz = fetch_big(*KONTUR)
    # unpacked it is ~1 GB, so it goes to the temp dir (not raw/, which may sit in a synced folder)
    os.makedirs(f"{tempfile.gettempdir()}/atlas-kontur", exist_ok=True)
    gpkg = f"{tempfile.gettempdir()}/atlas-kontur/{os.path.basename(gz)[:-3]}"
    if not os.path.exists(gpkg) or os.path.getmtime(gpkg) < os.path.getmtime(gz):
        print("  unpacking", os.path.basename(gz))
        with gzip.open(gz, "rb") as f, open(gpkg + ".part", "wb") as o: shutil.copyfileobj(f, o, 1 << 22)
        os.replace(gpkg + ".part", gpkg)
    con = sqlite3.connect(f"file:{gpkg}?mode=ro", uri=True)
    (srs,) = con.execute("select srs_id from gpkg_contents where table_name='population'").fetchone()
    if srs != 3857: raise SystemExit(f"Kontur: expected EPSG:3857, got {srs}")
    rows = np.array(con.execute("select r.minx, r.maxx, r.miny, r.maxy, p.population from population p "
                                "join rtree_population_geom r on r.id = p.fid order by p.fid").fetchall(), float)
    con.close()
    R = 6378137.0
    wide = rows[:, 1] - rows[:, 0] > 1e6   # a hexagon cut by the antimeridian: its box spans the globe, so use the side with the most area
    mx = np.where(wide, np.where(np.abs(rows[:, 0]) > np.abs(rows[:, 1]), rows[:, 0], rows[:, 1]), (rows[:, 0] + rows[:, 1]) / 2)
    lon = np.degrees(mx / R)
    lat = np.degrees(np.arctan(np.sinh((rows[:, 2] + rows[:, 3]) / 2 / R)))
    print(f"  Kontur: {len(rows):,} hexagons, {rows[:, 4].sum() / 1e9:.3f} bn people, {int(wide.sum())} on the antimeridian")
    return lon, lat, rows[:, 4]

def pop():
    import numpy as np
    lon, lat, ppl = kontur_points()
    # ---- tiles: POP_TILE-degree files, 1-degree cells inside, columnar ----
    d = f"{OUT}/pop"; os.makedirs(d, exist_ok=True)
    for f in os.listdir(d):
        if f.endswith(".bin"): os.remove(f"{d}/{f}")
    fx, fy = np.floor(lon), np.floor(lat)
    ix, iy = np.floor(lon / POP_TILE).astype(int), np.floor(lat / POP_TILE).astype(int)
    cx, cy = (fx - ix * POP_TILE).astype(int), (fy - iy * POP_TILE).astype(int)
    cell = cy * POP_TILE + cx
    dx = np.minimum(255, ((lon - fx) * 256).astype(int)).astype(np.uint8)
    dy = np.minimum(255, ((lat - fy) * 256).astype(int)).astype(np.uint8)
    q = np.round(np.log1p(ppl) * POP_Q).astype(np.int64)
    if q.max() > 65535: raise SystemExit("pop: a hexagon has too many people for 16 bits; lower POP_Q")
    q = q.astype("<u2")
    order = np.lexsort((dy, dx, cell, iy, ix))
    tiles, nbytes = {}, 0
    keys = np.stack([ix[order], iy[order]], 1)
    cut = np.nonzero(np.any(np.diff(keys, axis=0) != 0, axis=1))[0] + 1
    for grp in np.split(order, cut):
        tx, ty = int(ix[grp[0]]), int(iy[grp[0]])
        counts = np.bincount(cell[grp], minlength=POP_TILE * POP_TILE).astype("<u4")
        name = f"{tx}_{ty}"
        with open(f"{d}/{name}.bin", "wb") as f:
            f.write(counts.tobytes()); f.write(dx[grp].tobytes()); f.write(dy[grp].tobytes()); f.write(q[grp].tobytes())
        tiles[name] = int(len(grp)); nbytes += os.path.getsize(f"{d}/{name}.bin")
    idx = {"v": 1, "generated": now_utc(), "tile": POP_TILE, "q": POP_Q, "total": int(round(ppl.sum())), "tiles": dict(sorted(tiles.items())),
           "layout": f"per file: uint32[{POP_TILE * POP_TILE}] hexagon counts per 1-degree cell (row = latitude), then uint8 dx[n], uint8 dy[n] "
                     "(1/256 degree from the cell's south-west corner), then little-endian uint16 people[n] = round(ln(1 + people) * q)"}
    json.dump(idx, open(f"{d}/index.json", "w"), separators=(",", ":"))
    print(f"  pop: {len(tiles)} tiles, {nbytes / 1e6:.1f} MB")

    # ---- people per country (kpop in country_totals.json) ----
    feats = json.load(open(f"{OUT}/countries.geojson", encoding="utf-8"))["features"]
    pi = PolyIndex(lon, lat)
    for i, f in enumerate(feats):
        for p in polys_of(f["geometry"]): pi.add(p, i)
    hit = pi.result()
    by = np.bincount(hit[hit >= 0], weights=ppl[hit >= 0], minlength=len(feats))
    tp = f"{OUT}/country_totals.json"
    totals = json.load(open(tp, encoding="utf-8"))
    off = []
    for i, f in enumerate(feats):
        k = f["properties"]["k"]
        if k not in totals or by[i] <= 0: continue
        totals[k]["kpop"] = int(round(by[i]))
        km2 = sum(ring_km2(p[0]) - sum(ring_km2(h) for h in p[1:]) for p in polys_of(f["geometry"]))
        if km2 > 0: totals[k]["kdens"] = round(by[i] / km2, 1)
        ne = totals[k].get("pop") or 0
        if ne > 1e6 and abs(by[i] / ne - 1) > 0.25: off.append(f"{k} {by[i] / ne:.2f}")
    json.dump(totals, open(tp, "w"), separators=(",", ":"), ensure_ascii=False)
    print(f"  pop: {ppl[hit >= 0].sum() / ppl.sum() * 100:.1f}% of people fall inside a country polygon")
    if off: print("  pop: Kontur / Natural Earth population differs by more than 25% in", ", ".join(off))
    src = [srcinfo(*KONTUR, name="Kontur Population 2023-11-01, H3 resolution 6", licence="CC BY 4.0")]
    meta["pop/index.json"] = {"count": len(lon), "tiles": len(tiles), "bytes": nbytes, "sources": src}
    return lon, lat, ppl

def read_shp(zpath):
    """Polygons and attributes of the one shapefile in a zip: [(rings, {field: value})]. Stdlib parser for shape type 5."""
    import struct, zipfile
    z = zipfile.ZipFile(zpath)
    names = z.namelist()
    shp = z.read(next(n for n in names if n.lower().endswith(".shp")))
    dbf = z.read(next(n for n in names if n.lower().endswith(".dbf")))
    # dBase: header, 32-byte field descriptors up to 0x0D, then fixed-width records
    nrec, hlen, rlen = struct.unpack("<IHH", dbf[4:12])
    fields, o = [], 32
    while dbf[o] != 0x0D:
        fields.append((dbf[o:o + 11].split(b"\0")[0].decode(), chr(dbf[o + 11]), dbf[o + 16])); o += 32
    attrs = []
    for r in range(nrec):
        rec, o, a = dbf[hlen + r * rlen: hlen + (r + 1) * rlen], 1, {}
        for name, t, ln in fields:
            s = rec[o:o + ln].decode("latin-1").strip(); o += ln
            a[name] = (float(s) if "." in s or "e" in s.lower() else int(s)) if t in "NF" and s else s
        attrs.append(a)
    out, o = [], 100
    while o < len(shp):
        clen = struct.unpack(">I", shp[o + 4:o + 8])[0] * 2
        body = shp[o + 8:o + 8 + clen]; o += 8 + clen
        if struct.unpack("<i", body[:4])[0] != 5: out.append([]); continue
        nparts, npts = struct.unpack("<ii", body[36:44])
        parts = list(struct.unpack(f"<{nparts}i", body[44:44 + 4 * nparts])) + [npts]
        xy = struct.unpack(f"<{2 * npts}d", body[44 + 4 * nparts: 44 + 4 * nparts + 16 * npts])
        out.append([[(xy[2 * i], xy[2 * i + 1]) for i in range(parts[j], parts[j + 1])] for j in range(nparts)])
    if len(out) != len(attrs): raise SystemExit(f"{zpath}: {len(out)} shapes but {len(attrs)} records")
    return list(zip(out, attrs))

def water(kontur=None):
    import numpy as np
    from collections import Counter
    lon, lat, ppl = kontur or kontur_points()
    wr = json.load(open(f"{OUT}/water_risk.geojson", encoding="utf-8"))["features"]
    nb = len(wr)
    # people, their centre and their main country per Aqueduct basin
    pi = PolyIndex(lon, lat)
    for i, f in enumerate(wr):
        for p in polys_of(f["geometry"]): pi.add(p, i)
    hb = pi.result(); m = hb >= 0
    bpop = np.bincount(hb[m], weights=ppl[m], minlength=nb)
    bx = np.bincount(hb[m], weights=ppl[m] * lon[m], minlength=nb); by_ = np.bincount(hb[m], weights=ppl[m] * lat[m], minlength=nb)
    feats = json.load(open(f"{OUT}/countries.geojson", encoding="utf-8"))["features"]
    ci = PolyIndex(lon, lat)
    for i, f in enumerate(feats):
        for p in polys_of(f["geometry"]): ci.add(p, i)
    hc = ci.result()
    main_k = {}
    acc = {}
    for b, c, w in zip(hb[m & (hc >= 0)], hc[m & (hc >= 0)], ppl[m & (hc >= 0)]): acc[(b, c)] = acc.get((b, c), 0) + w
    for (b, c), w in acc.items():
        if w > main_k.get(b, (None, 0))[1]: main_k[b] = (c, w)
    print(f"  water: {ppl[m].sum() / ppl.sum() * 100:.1f}% of people inside an Aqueduct basin")

    # HydroBASINS level 6 polygons and their downstream links
    hy = []
    for name, url in HYBAS: hy += read_shp(fetch_big(name, url))
    print(f"  water: {len(hy):,} HydroBASINS level-6 basins")
    # sample points inside each Aqueduct basin (a grid over each part, kept if inside), voted into HydroBASINS polygons
    from matplotlib.path import Path
    sx, sy, sb = [], [], []
    for i, f in enumerate(wr):
        for p in polys_of(f["geometry"]):
            rings = [np.asarray(r, float) for r in p if len(r) >= 4]
            if not rings: continue
            allv = np.concatenate(rings); (x0, y0), (x1, y1) = allv.min(0), allv.max(0)
            gx, gy = np.meshgrid(np.linspace(x0, x1, 10)[1:-1], np.linspace(y0, y1, 10)[1:-1])
            pts = np.column_stack([gx.ravel(), gy.ravel()])
            codes = np.concatenate([[Path.MOVETO] + [Path.LINETO] * (len(r) - 1) for r in rings]).astype(np.uint8)
            pts = pts[Path(allv, codes).contains_points(pts)]
            if not len(pts): pts = allv[:-1].mean(0, keepdims=True)
            sx.append(pts[:, 0]); sy.append(pts[:, 1]); sb.append(np.full(len(pts), i))
    sx, sy, sb = np.concatenate(sx), np.concatenate(sy), np.concatenate(sb)
    hi = PolyIndex(sx, sy)
    for j, (rings, a) in enumerate(hy): hi.add(rings, j)
    hh = hi.result()
    votes = {}
    for b, j in zip(sb[hh >= 0], hh[hh >= 0]): votes.setdefault(int(b), Counter())[int(j)] += 1
    pick = {b: c.most_common(1)[0][0] for b, c in votes.items()}
    print(f"  water: {len(pick):,} of {nb:,} Aqueduct basins matched to a HydroBASINS basin")

    # downstream Aqueduct basin: follow NEXT_DOWN until a HydroBASINS basin that some Aqueduct basin matched
    by_id = {a["HYBAS_ID"]: j for j, (_, a) in enumerate(hy)}
    first = {}
    for b in sorted(pick): first.setdefault(pick[b], b)
    def down(j):
        seen = 0
        while seen < 500:
            nd = hy[j][1]["NEXT_DOWN"]
            if not nd or nd not in by_id: return -1
            j = by_id[nd]; seen += 1
            if j in first: return first[j]
        return -1
    pf, dn, ua, kk, cx, cy = [], [], [], [], [], []
    for b in range(nb):
        j = pick.get(b)
        a = hy[j][1] if j is not None else {}
        pf.append(int(a["PFAF_ID"]) if a else 0)
        d = down(j) if j is not None else -1
        dn.append(d if d != b else -1)
        ua.append(round(a.get("UP_AREA") or 0))
        kk.append(int(main_k[b][0]) if b in main_k else -1)
        cx.append(int(round(bx[b] / bpop[b] * 100)) if bpop[b] > 0 else None); cy.append(int(round(by_[b] / bpop[b] * 100)) if bpop[b] > 0 else None)
    # basins with no people: centre of their sample points
    for b in range(nb):
        if cx[b] is None:
            s = sb == b
            cx[b] = int(round(float(sx[s].mean()) * 100)) if s.any() else 0; cy[b] = int(round(float(sy[s].mean()) * 100)) if s.any() else 0
    # cities of 100k+ (labels.json) and the basin each sits in: where people leaving a drying basin are sent
    towns = [l for l in json.load(open(f"{OUT}/labels.json", encoding="utf-8")) if l["t"] == "p" and (l.get("pop") or 0) >= 100000]
    ti = PolyIndex(np.array([l["x"] for l in towns], float), np.array([l["y"] for l in towns], float))
    for i, f in enumerate(wr):
        for p in polys_of(f["geometry"]): ti.add(p, i)
    tb = ti.result()
    cities = [[l["n"], int(round(l["x"] * 100)), int(round(l["y"] * 100)), int(l["pop"]), int(b)] for l, b in zip(towns, tb) if b >= 0]
    print(f"  water: {len(cities):,} of {len(towns):,} cities of 100k+ placed in a basin")
    ks = [feats[i]["properties"]["k"] for i in range(len(feats))]
    out = {"v": 1, "generated": now_utc(), "n": nb,
           # first ring's first vertex of each basin, rounded: lets the page check this file matches water_risk.geojson
           "check": round(float(sum(polys_of(f["geometry"])[0][0][0][0] for f in wr)), 2),
           "countries": ks, "pf": pf, "dn": dn, "ua": ua, "pop": [int(round(v)) for v in bpop], "k": kk, "cx": cx, "cy": cy, "cities": cities}
    p = f"{OUT}/basins.json"
    json.dump(out, open(p, "w"), separators=(",", ":"))
    links = sum(1 for d in dn if d >= 0)
    print(f"  water: {links:,} downstream links; basins.json {os.path.getsize(p) / 1e6:.2f} MB")
    meta["basins.json"] = {"count": nb, "bytes": os.path.getsize(p), "sources": [
        srcinfo(*HYBAS[0], name="HydroSHEDS HydroBASINS v1c, level 6 (9 regional files)", licence="HydroSHEDS licence: free with attribution"),
        srcinfo(*KONTUR, name="Kontur Population 2023-11-01, H3 resolution 6", licence="CC BY 4.0"),
        {"name": "basin polygons from water_risk.geojson (WRI Aqueduct 4.0)"}]}

def pop_and_water():
    """pop and water share the Kontur points, so they run together when both are wanted."""
    want = lambda s: not ONLY or s in ONLY
    k = pop() if want("pop") else None
    if want("water"): water(k)

# =====================================================================
# 5. Transport: road and rail networks for the zone scenarios
# =====================================================================
T_SNAP_KM = 3        # a loose line end this close to another line of the same network joins it there
T_CITY_KM = 25       # each city of T_CITY_POP+ joins the nearest line within this distance
T_CITY_POP = 100000
T_BIG_KM = 500       # cities prefer a line in a network at least this long over a nearer stub

def seg_proj(px, py, ax, ay, bx, by):
    """Distance in km from a point to a segment, and how far along the segment (0-1) the nearest point is."""
    c = math.cos(math.radians(py))
    ax, bx = (ax - px) * c, (bx - px) * c
    ay, by = ay - py, by - py
    dx, dy = bx - ax, by - ay
    t = 0 if dx == dy == 0 else max(0, min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx, ay + t * dy) * 111.2, t

def line_parts(name, flag=None):
    """Every part of every line in <name>.geojson, in file order (the page walks the file the same way),
    and the indices of the parts whose feature has the property `flag`."""
    out, flagged = [], []
    for f in json.load(open(f"{OUT}/{name}.geojson", encoding="utf-8"))["features"]:
        g = f["geometry"]
        ps = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        if flag and f["properties"].get(flag): flagged += range(len(out), len(out) + len(ps))
        out += ps
    return out, flagged

def transport_net(name, parts, cities):
    """Snap one network's lines into a graph. A stop is a point on a part, keyed by 100 x its fractional vertex index;
    stops that are the same place share a node, and the stretches of a part between consecutive stops are the edges."""
    C = 0.1
    grid = {}
    for pi, p in enumerate(parts):
        for j in range(1, len(p)):
            (ax, ay), (bx, by) = p[j - 1], p[j]
            for gx in range(math.floor(min(ax, bx) / C), math.floor(max(ax, bx) / C) + 1):
                for gy in range(math.floor(min(ay, by) / C), math.floor(max(ay, by) / C) + 1):
                    grid.setdefault((gx, gy), []).append((pi, j))
    def nearest(x, y, km, skip=None, ok=None):
        """(km, part, key) of the closest point on any line within km, or (km, None, None)."""
        rx = int(km / (111.2 * max(0.05, math.cos(math.radians(y)))) / C) + 1; ry = int(km / 111.2 / C) + 1
        gx, gy = math.floor(x / C), math.floor(y / C)
        best, seen = (km, None, None), set()
        for i in range(gx - rx, gx + rx + 1):
            for j in range(gy - ry, gy + ry + 1):
                for pi, s in grid.get((i, j), ()):
                    if pi == skip or (pi, s) in seen or (ok and not ok(pi)): continue
                    seen.add((pi, s))
                    p = parts[pi]
                    d, t = seg_proj(x, y, *p[s - 1], *p[s])
                    if d < best[0]:
                        k = round((s - 1 + t) * 100)
                        best = (d, pi, k)
        return best
    stops = [{} for _ in parts]   # part -> {key: stop id}
    par = []
    def stop(pi, key):
        s = stops[pi].get(key)
        if s is None: s = stops[pi][key] = len(par); par.append(s)
        return s
    def find(a):
        while par[a] != a: par[a] = par[par[a]]; a = par[a]
        return a
    # 1. every part end is a stop, and a vertex shared by two parts joins them
    at = {}
    for pi, p in enumerate(parts):
        for i, v in enumerate(p): at.setdefault(tuple(v), []).append((pi, i))
    for v in at.values():
        if len(v) == 1 and 0 < v[0][1] < len(parts[v[0][0]]) - 1: continue
        ids = [stop(pi, i * 100) for pi, i in v]
        for s in ids[1:]: par[find(s)] = find(ids[0])
    # 2. loose ends: the simplified lines rarely share interior vertices, so an end that isn't already a junction
    #    (a free end, or where one part simply continues another) joins the nearest other line
    loose = snapped = 0
    for pi, p in enumerate(parts):
        for i in (0, len(p) - 1):
            here = at[tuple(p[i])]
            if len(here) > 2 or any(0 < j < len(parts[q]) - 1 for q, j in here): continue
            loose += 1
            mine = {q for q, _ in here}
            d, pj, k = nearest(*p[i], T_SNAP_KM, ok=lambda q: q not in mine)
            if pj is None: continue
            par[find(stops[pi][i * 100])] = find(stop(pj, k)); snapped += 1
    # nodes: one per group of stops; components by part (a part's stops are all joined by its edges)
    node = {}
    for s in range(len(par)): node.setdefault(find(s), len(node))
    cpar = list(range(len(node)))
    def cfind(a):
        while cpar[a] != a: cpar[a] = cpar[cpar[a]]; a = cpar[a]
        return a
    pkm = []
    for pi, p in enumerate(parts):
        ns = [node[find(s)] for s in stops[pi].values()]
        for n in ns[1:]: cpar[cfind(n)] = cfind(ns[0])
        pkm.append(sum(hav(*p[j - 1], *p[j]) for j in range(1, len(p))))
    ckm = {}
    for pi in range(len(parts)):
        c = cfind(node[find(stops[pi][0])]); ckm[c] = ckm.get(c, 0) + pkm[pi]
    big = lambda pi: ckm[cfind(node[find(stops[pi][0])])] >= T_BIG_KM
    # 3. cities: a stop of their own on the nearest line, preferring a line in a long network
    at_city, ckm_city = [], []
    for x, y in cities:
        d, pi, k = nearest(x, y, T_CITY_KM, ok=big)
        if pi is None: d, pi, k = nearest(x, y, T_CITY_KM)
        if pi is None: at_city.append(-1); ckm_city.append(0); continue
        s = stop(pi, k)
        if find(s) not in node: node[find(s)] = len(node)
        at_city.append(node[find(s)]); ckm_city.append(round(d, 1))
    def pos(pi, k):
        p, i, t = parts[pi], k // 100, k % 100 / 100
        if t == 0: return p[i]
        return [p[i][0] + (p[i + 1][0] - p[i][0]) * t, p[i][1] + (p[i + 1][1] - p[i][1]) * t]
    def along(pi, k0, k1):
        """km along a part between two stops"""
        pts = [pos(pi, k0)] + parts[pi][k0 // 100 + 1:(k1 + 99) // 100] + [pos(pi, k1)]
        return sum(hav(*pts[j - 1], *pts[j]) for j in range(1, len(pts)))
    nx, ny = [None] * len(node), [None] * len(node)
    flat, ekm = [], []
    for pi in range(len(parts)):
        ks = sorted(stops[pi])
        row = []
        for j, k in enumerate(ks):
            n = node[find(stops[pi][k])]
            if nx[n] is None: nx[n], ny[n] = (round(c * 100) for c in pos(pi, k))
            if 0 < j: row.append(k); ekm.append(max(1, round(along(pi, ks[j - 1], k) * 10)))
            row.append(n)
        flat.append(row)
    edges = len(ekm)
    tot = sum(ckm.values()); top = sorted(ckm.values(), reverse=True)[:3]
    print(f"  transport {name}: {len(node):,} nodes, {edges:,} edges; {snapped:,} of {loose:,} loose ends joined a line within {T_SNAP_KM} km; "
          f"{len(ckm):,} networks, largest {', '.join(f'{v / tot * 100:.1f}%' for v in top)} of {tot:,.0f} km; "
          f"{sum(1 for a in at_city if a >= 0):,} of {len(cities):,} cities on it")
    return {"check": [len(parts), sum(len(p) for p in parts)], "x": nx, "y": ny, "parts": flat, "km": ekm, "city": at_city, "cityKm": ckm_city}

def transport():
    towns = [l for l in json.load(open(f"{OUT}/labels.json", encoding="utf-8")) if l["t"] == "p" and (l.get("pop") or 0) >= T_CITY_POP]
    xy = [(l["x"], l["y"]) for l in towns]
    g = {"v": 1, "generated": now_utc(), "snapKm": T_SNAP_KM, "cityKm": T_CITY_KM,
         "layout": "per network, parts[i] lists the nodes along the i-th line part of its geojson (features in order, MultiLineString parts in order): "
                   "[node, key, node, key, node, ...]. The first node is at the part's start; each later node sits at the key before it, "
                   "100 x its fractional vertex index along the part. Consecutive nodes are joined by an edge. "
                   "km[e] is the length of edge e (edges numbered in that same order) in units of 0.1 km; fast lists the parts that are expressways. "
                   "x, y are node coordinates x 100; city[i] is the node city i joins, -1 if none, cityKm[i] how far away it is; "
                   "check is [parts, vertices] of the geojson the graph was built from.",
         "cities": {"n": [l["n"] for l in towns], "x": [round(l["x"] * 100) for l in towns], "y": [round(l["y"] * 100) for l in towns],
                    "pop": [int(l["pop"]) for l in towns], "k": [l.get("k") or "" for l in towns]}}
    for key, name, flag in (("road", "roads", "x"), ("rail", "rail", None)):
        parts, fast = line_parts(name, flag)
        g[key] = transport_net(name, parts, xy)
        if flag: g[key]["fast"] = fast
    p = f"{OUT}/transport_graph.json"
    json.dump(g, open(p, "w", encoding="utf-8"), separators=(",", ":"), ensure_ascii=False)
    print("transport_graph.json", round(os.path.getsize(p) / 1e6, 2), "MB")
    meta["transport_graph.json"] = {"count": len(g["road"]["x"]) + len(g["rail"]["x"]), "bytes": os.path.getsize(p), "sources": [
        {"name": "built from roads, rail and labels (Natural Earth 10m)"}]}

SECTIONS = {"refineries": refineries, "energy": energy, "pop+water": pop_and_water, "transport": transport}

if __name__ == "__main__":
    print("step6", "(offline)" if OFFLINE else "")
    for name, fn in SECTIONS.items():
        if ONLY and not set(name.split("+")) & set(ONLY): continue
        fn(); json.dump(fetchlog, open(FETCHLOG, "w"), indent=1)
    meta["_generated"] = now_utc()
    json.dump(meta, open(META, "w"), indent=1, ensure_ascii=False)
    print("wrote infra_meta.json")
