"""Step 5: defence and strategic infrastructure points (stdlib only).

Writes to site/data/:
  bases.geojson        military bases          Wikidata SPARQL (CC0) + OSM military=* via Overpass (ODbL)
  airports.geojson     airports + runways      OurAirports (public domain)
  ports_wpi.geojson    World Port Index        NGA Pub 150 (US gov, public domain)
  datacenters.geojson  data centres            OSM telecom=data_center via Overpass (+ PeeringDB, opt-in)
  cables.geojson       submarine cables        TeleGeography Submarine Cable Map (CC BY-NC-SA 3.0)
  landings.geojson     cable landing points    same
  infra_meta.json      counts, sources, retrieval times

Raw downloads are cached in raw/infra/. Flags:
  --offline        use only the cache (fail if a required file is missing)
  --with-peeringdb add PeeringDB facilities. Off by default: the PeeringDB AUP forbids
                   republishing its data without permission, so don't deploy that build
  --no-osm         skip the Overpass requests (and leave OSM bases out)
"""
import os
ROOT = os.environ.get("ATLAS_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import csv, io, json, math, re, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

W = ROOT
OUT = f"{W}/site/data"; os.makedirs(OUT, exist_ok=True)
RAW = f"{W}/raw/infra"; os.makedirs(RAW, exist_ok=True)
UA = "resourcemap-pipeline/1.0 (+https://github.com/byron123t/resourcemap)"
OFFLINE = "--offline" in sys.argv
NO_PDB = "--with-peeringdb" not in sys.argv
NO_OSM = "--no-osm" in sys.argv
FETCHLOG = f"{RAW}/_fetch.json"
fetchlog = json.load(open(FETCHLOG)) if os.path.exists(FETCHLOG) else {}
meta = {}

def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------- HTTP with cache ----------
def http(url, data=None, headers=None, timeout=120, tries=4, ua=UA):
    h = {"User-Agent": ua}; h.update(headers or {})
    body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            # 4xx other than 429 won't improve with retries
            if e.code < 500 and e.code != 429 or k == tries - 1: raise
            wait = int(e.headers.get("Retry-After", 0) or 0) or 5 * 2 ** k
            print(f"  HTTP {e.code}, retry in {wait}s"); time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if k == tries - 1: raise
            print(f"  {e}, retry in {5 * 2 ** k}s"); time.sleep(5 * 2 ** k)

def cached(name, url, ua=UA, **kw):
    """Return bytes of raw/infra/<name>, downloading it unless --offline."""
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

def r4(x): return round(float(x), 4)

def pt(lon, lat, props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [r4(lon), r4(lat)]},
            "properties": {k: v for k, v in props.items() if v not in (None, "")}}

def dump(name, feats, src, **extra):
    p = f"{OUT}/{name}.geojson"
    json.dump({"type": "FeatureCollection", "features": feats}, open(p, "w", encoding="utf-8"),
              separators=(",", ":"), ensure_ascii=False)
    mb = round(os.path.getsize(p) / 1e6, 2)
    print(name, len(feats), mb, "MB")
    meta[f"{name}.geojson"] = {"count": len(feats), "bytes": os.path.getsize(p), "sources": src, **extra}
    return mb

def srcinfo(name, **kw):
    e = fetchlog.get(name, {})
    d = {"url": e.get("url"), "retrieved": e.get("retrieved")}
    if e.get("last_modified"): d["version"] = e["last_modified"]
    d.update(kw)
    return d

def num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None

# =====================================================================
# 1. Military bases (Wikidata)
# =====================================================================
SPARQL = "https://query.wikidata.org/sparql"
LANGS = "en,mul,fr,de,es,it,pt,nl,pl,ru,uk,ja,zh,ko,ar,tr,sv,cs,fi,no,da,el,he,fa,hi,id,vi,th"

def sparql(q, tag):
    """Run a query, cached as raw/infra/wd_<tag>.json."""
    url = SPARQL + "?" + urllib.parse.urlencode({"query": q})
    p = f"{RAW}/wd_{tag}.json"
    if not OFFLINE:
        b, _ = http(SPARQL, data={"query": q}, headers={"Accept": "application/sparql-results+json"}, timeout=75)
        d = json.loads(b)  # a timed-out query returns truncated JSON -> raises here
        open(p, "wb").write(b)
        fetchlog[f"wd_{tag}.json"] = {"url": SPARQL, "retrieved": now_utc()}
        time.sleep(1)
        return d["results"]["bindings"]
    if not os.path.exists(p): raise SystemExit(f"missing {p}; run without --offline")
    return json.load(open(p))["results"]["bindings"]

def qid(b, k):
    v = b.get(k, {}).get("value", "")
    return v.rsplit("/", 1)[-1] if v else None

def subtree(root, tag):
    return {qid(b, "c") for b in sparql(f"SELECT ?c WHERE {{ ?c wdt:P279* wd:{root} . }}", f"cls_{tag}")}

# Police/gendarmerie posts are typed as "barracks" in Wikidata and would swamp the map (3.6k
# French gendarmeries); historic/former classes are dropped too.
EXCLUDE_CLS = {"Q83554028", "Q5645932", "Q134318865", "Q116257904", "Q30559999", "Q116693136"}
FORMER_CLS = ["Q116257904", "Q30559999", "Q116693136", "Q839954", "Q109607", "Q19860854", "Q15893266", "Q56556915"]
CLOSED_STATE = ["Q11639308", "Q56651571", "Q104664889", "Q63065035", "Q111802839", "Q56556915", "Q30108381", "Q12377751"]

NAME_CAT = [("missile", re.compile(r"\b(missile|ICBM|launch (complex|facility|site))\b", re.I)),
            ("air", re.compile(r"\b(air\s*(force\s*)?base|airbase|air\s*station|airfield|air\s*field|RAF|NAS|AFB|base\s+a[ée]rea|fliegerhorst|aviation)\b", re.I)),
            ("naval", re.compile(r"\b(naval|navy|submarine|fleet|dockyard|marine\s*base|base\s+naval)\b", re.I)),
            ("army", re.compile(r"\b(fort|camp|barracks|kaserne|caserne|cuartel|quartel|garrison|army|cantonment|kazarma)\b", re.I))]
OP_CAT = [("air", re.compile(r"air\s*force|luftwaffe|armée de l'air|fuerza a[ée]rea|força a[ée]rea|aeronautica", re.I)),
          ("naval", re.compile(r"\bnavy\b|naval|marine nationale|marina|marinha|marine corps", re.I)),
          ("army", re.compile(r"\barmy\b|armée de terre|ej[ée]rcito|ex[ée]rcito|heer\b|esercito", re.I))]

def bases():
    air = subtree("Q695850", "air") | {"Q130008763", "Q1536137"}          # air base tree, RAF aerodrome, MOB
    naval = subtree("Q1324633", "naval") | {"Q7619063", "Q55624858", "Q7374522"}  # + shore establishment, naval station
    missile = {"Q853409", "Q21193688", "Q87715130"}                        # launch facility, missile base, launch site
    army = (subtree("Q131263", "barracks") | subtree("Q88556", "garrison")
            | {"Q1198413", "Q2604714", "Q5034013", "Q2543279", "Q57178953", "Q111212994", "Q136558797",
               "Q42058516", "Q65121211", "Q20502495", "Q97503720", "Q1321241", "Q4842222", "Q136644432"})
    allmil = subtree("Q245016", "base") | {"Q18691599"} | missile
    buckets = [("missile", missile), ("air", air), ("naval", naval), ("army", army)]
    other = allmil - air - naval - missile - army
    buckets.append(("other", other))

    items, cats = {}, {}
    def run(cat, tag, classes):
        cl = sorted(c for c in classes if c and c not in EXCLUDE_CLS)
        if not cl: return
        q = f"""SELECT ?i ?iLabel ?coord ?coLabel ?opLabel ?end ?end2 ?former ?state WHERE {{
 VALUES ?c {{ {' '.join('wd:' + c for c in cl)} }}
 ?i wdt:P31 ?c ; wdt:P625 ?coord .
 OPTIONAL {{ ?i wdt:P17 ?co }} OPTIONAL {{ ?i wdt:P137 ?op }}
 OPTIONAL {{ ?i wdt:P576 ?end }} OPTIONAL {{ ?i wdt:P582 ?end2 }}
 OPTIONAL {{ VALUES ?fc {{ {' '.join('wd:' + c for c in FORMER_CLS + sorted(EXCLUDE_CLS))} }} ?i wdt:P31 ?fc . BIND(1 AS ?former) }}
 OPTIONAL {{ VALUES ?st {{ {' '.join('wd:' + c for c in CLOSED_STATE)} }} ?i wdt:P5817 ?st . BIND(1 AS ?state) }}
 SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{LANGS}". }} }}"""
        try:
            rows = sparql(q, f"items_{tag}")
        except (urllib.error.HTTPError, json.JSONDecodeError, TimeoutError, urllib.error.URLError) as e:
            if len(cl) == 1: raise
            print(f"  {tag}: {type(e).__name__}, splitting {len(cl)} classes")
            h = len(cl) // 2
            run(cat, tag + "a", cl[:h]); run(cat, tag + "b", cl[h:]); return
        print(f"  {tag}: {len(rows)} rows ({len(cl)} classes)")
        for b in rows:
            i = qid(b, "i"); cats.setdefault(i, set()).add(cat)
            if i in items:
                it = items[i]
                for k in ("end", "end2", "former", "state"):
                    if k in b: it[k] = it.get(k) or b[k]["value"]
                if not it.get("op") and b.get("opLabel"): it["op"] = b["opLabel"]["value"]
                continue
            m = re.match(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", b["coord"]["value"])
            if not m: continue   # coordinates on other globes
            items[i] = {"lon": float(m[1]), "lat": float(m[2]), "n": b.get("iLabel", {}).get("value"),
                        "c": b.get("coLabel", {}).get("value"), "op": b.get("opLabel", {}).get("value"),
                        **{k: b[k]["value"] for k in ("end", "end2", "former", "state") if k in b}}
    for cat, classes in buckets:
        cl = sorted(c for c in classes if c and c not in EXCLUDE_CLS)
        # ~40 classes per query keeps each request well under the 60 s limit; run() splits on timeout
        for j in range(0, len(cl), 40):
            run(cat, f"{cat}{j // 40}", cl[j:j + 40])

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    order = ["missile", "air", "naval", "army", "other"]
    feats, dropped, unlabeled = [], 0, []
    for i, it in items.items():
        ended = any((it.get(k) or "9999")[:10] <= today for k in ("end", "end2") if it.get(k))
        if ended or it.get("former") or it.get("state"):
            dropped += 1; continue
        if not it["n"] or re.fullmatch(r"Q\d+", it["n"] or ""): unlabeled.append(i)
        t = next(c for c in order if c in cats[i])
        if t == "other":   # generic "military base" items: refine from the name, then the operator
            t = next((c for c, rx in NAME_CAT if rx.search(it["n"] or "")), None) \
                or next((c for c, rx in OP_CAT if rx.search(it["op"] or "")), "other")
        it["t"] = t; it["w"] = i
    # any-language fallback for items with no label in LANGS
    if unlabeled:
        for j in range(0, len(unlabeled), 300):
            chunk = unlabeled[j:j + 300]
            q = f"""SELECT ?i (SAMPLE(?l) AS ?lab) WHERE {{ VALUES ?i {{ {' '.join('wd:' + x for x in chunk)} }}
 ?i rdfs:label ?l . }} GROUP BY ?i"""
            for b in sparql(q, f"labels{j // 300}"):
                items[qid(b, "i")]["n"] = b["lab"]["value"]
    for i, it in items.items():
        if "t" not in it: continue
        n = it["n"] if it["n"] and not re.fullmatch(r"Q\d+", it["n"]) else "Military base"
        feats.append(pt(it["lon"], it["lat"], {"n": n, "t": it["t"], "c": it["c"], "op": it["op"], "w": i}))
    nwd = len(feats)
    srcs = [{"url": SPARQL, "retrieved": max((v["retrieved"] for k, v in fetchlog.items() if k.startswith("wd_")), default=None),
             "version": "live Wikidata query", "licence": "CC0 1.0"}]
    osm_stats = osm_bases(feats, srcs)
    feats.sort(key=lambda f: order.index(f["properties"]["t"]))
    counts = {c: sum(f["properties"]["t"] == c for f in feats) for c in order}
    print(f"bases: {len(items)} Wikidata items, dropped {dropped} closed/former; per t: {counts}")
    json.dump(fetchlog, open(FETCHLOG, "w"), indent=1)
    dump("bases", feats, srcs, per_type=counts, dropped_closed=dropped, wikidata=nwd, **osm_stats)


# OSM military=* areas complement Wikidata, which is thin outside Europe and North America.
OVQ_MIL = '[out:json][timeout:240];nwr["military"~"^(base|airfield|naval_base)$"];out center tags;'
OSM_SERVICE = {"navy": "naval", "coast_guard": "naval", "marines": "army", "army": "army", "national_guard": "army",
               "air_force": "air", "space_force": "other", "rocket_force": "missile", "strategic_missile_forces": "missile"}
OSM_DROP_SERVICE = re.compile(r"gendarmerie|police|border|customs|fc\b", re.I)
OSM_GONE = ("disused", "abandoned", "historic", "ruins", "demolished", "razed", "was:military", "end_date")

def overpass(q, fname, label):
    """Run an Overpass query, cached as raw/infra/<fname>; one retry, then fall back to the cache."""
    p, d = f"{RAW}/{fname}", None
    if not NO_OSM and not OFFLINE:
        for k in range(2):
            try:
                print("fetch", OVERPASS, f"({label})")
                b, _ = http(OVERPASS, data={"data": q}, timeout=300, tries=1)
                d = json.loads(b)
                open(p, "wb").write(b)
                fetchlog[fname] = {"url": OVERPASS, "retrieved": now_utc(),
                                   "last_modified": d.get("osm3s", {}).get("timestamp_osm_base")}
                break
            except Exception as e:
                print(f"  overpass failed: {e}")
                if k == 0: time.sleep(30)
    if d is None and not NO_OSM and os.path.exists(p):
        print(f"  using cached {fname}"); d = json.load(open(p))
    return d

def osm_bases(feats, srcs):
    """Append OSM bases that are not already in feats (Wikidata). Returns stats for infra_meta."""
    osm = overpass(OVQ_MIL, "osm_military.json", "military=base|airfield|naval_base")
    if not osm: return {"osm": 0}
    wd_ids = {f["properties"].get("w") for f in feats}
    grid = {}
    for f in feats:
        x, y = f["geometry"]["coordinates"]
        grid.setdefault((int(x * 20), int(y * 20)), []).append((x, y, f["properties"]["t"]))
    added = dup = gone = police = 0
    for e in osm.get("elements", []):
        tg = e.get("tags", {})
        c = e.get("center") or ({"lat": e["lat"], "lon": e["lon"]} if "lat" in e else None)
        if not c: continue
        if any(k in tg for k in OSM_GONE) or any(k.startswith(("disused:", "abandoned:")) for k in tg):
            gone += 1; continue
        svc = (tg.get("military_service") or "").split(";")[0].strip().lower()
        if OSM_DROP_SERVICE.search(svc) or OSM_DROP_SERVICE.search(tg.get("operator", "")):
            police += 1; continue
        if tg.get("wikidata") in wd_ids:
            dup += 1; continue
        name = (tg.get("name:en") or tg.get("name") or tg.get("official_name") or "").strip()
        mil = tg["military"]
        t = ("naval" if mil == "naval_base" else "air" if mil == "airfield" else OSM_SERVICE.get(svc)) \
            or next((k for k, rx in NAME_CAT if rx.search(name)), None) \
            or next((k for k, rx in OP_CAT if rx.search(tg.get("operator", ""))), "other")
        if t == "army" and mil == "airfield": t = "air"
        xy = (c["lon"], c["lat"]); gx, gy = int(xy[0] * 20), int(xy[1] * 20)
        # Wikidata points and OSM area centres of the same base can sit a few km apart
        near = any(dist_m(xy, (qx, qy)) < (3000 if qt == t else 1200)
                   for dx in (-1, 0, 1) for dy in (-1, 0, 1) for qx, qy, qt in grid.get((gx + dx, gy + dy), []))
        if near: dup += 1; continue
        grid.setdefault((gx, gy), []).append((xy[0], xy[1], t))
        feats.append(pt(xy[0], xy[1], {"n": name or ("Military airfield" if mil == "airfield" else "Military area"),
                                       "t": t, "op": tg.get("operator"), "src": "o", "osm": e["type"][0] + str(e["id"]),
                                       "w": tg.get("wikidata")}))
        added += 1
    srcs.append(srcinfo("osm_military.json", licence="ODbL 1.0, © OpenStreetMap contributors", query=OVQ_MIL))
    print(f"  OSM military: added {added}, {dup} already in Wikidata, dropped {gone} disused, {police} police/border")
    return {"osm": added, "osm_dup_wikidata": dup, "osm_dropped_disused": gone, "osm_dropped_police": police}


# =====================================================================
# 5. Submarine cables (TeleGeography Submarine Cable Map)
# =====================================================================
SCM = "https://www.submarinecablemap.com/api/v3"

def cables():
    ua = "Mozilla/5.0 (compatible; " + UA + ")"
    geo = json.loads(cached("cable-geo.json", f"{SCM}/cable/cable-geo.json", ua=ua))
    lps = json.loads(cached("landing-point-geo.json", f"{SCM}/landing-point/landing-point-geo.json", ua=ua))
    # per-cable details (owners, length, RFS year) are one request each; cache them for 30 days
    cdir = f"{RAW}/cables"; os.makedirs(cdir, exist_ok=True)
    ids = sorted({f["properties"]["id"] for f in geo["features"]})
    stale = [i for i in ids if not os.path.exists(f"{cdir}/{i}.json")
             or time.time() - os.path.getmtime(f"{cdir}/{i}.json") > 30 * 86400]
    if stale and not OFFLINE:
        from concurrent.futures import ThreadPoolExecutor
        print(f"fetch {len(stale)} cable detail files from {SCM}/cable/<id>.json")
        def one(i):
            try:
                b, _ = http(f"{SCM}/cable/{i}.json", ua=ua, timeout=30, tries=3)
                open(f"{cdir}/{i}.json", "wb").write(b)
            except Exception as e:
                print(f"  {i}: {e}")
        with ThreadPoolExecutor(4) as ex: list(ex.map(one, stale))
        fetchlog["cables/"] = {"url": f"{SCM}/cable/<id>.json", "retrieved": now_utc()}
    det = {}
    for i in ids:
        try: det[i] = json.load(open(f"{cdir}/{i}.json"))
        except Exception: pass
    year = datetime.now(timezone.utc).year
    feats, lp_cables = [], {}
    for f in geo["features"]:
        p = f["properties"]; d = det.get(p["id"], {})
        for lp in d.get("landing_points") or []:
            lp_cables.setdefault(lp["id"], []).append(p["name"])
        lines = [[[round(x, 3), round(y, 3)] for x, y in l] for l in f["geometry"]["coordinates"]]
        km = num(re.sub(r"[^\d.]", "", d.get("length") or "") or None)
        rfs = d.get("rfs_year")
        props = {"n": p["name"], "id": p["id"], "col": p.get("color"), "km": int(km) if km else None,
                 "rfs": rfs, "own": d.get("owners"), "sup": d.get("suppliers"), "url": d.get("url"),
                 "lp": len(d.get("landing_points") or []) or None,
                 "pl": 1 if d.get("is_planned") or (rfs and rfs > year) else None}
        feats.append({"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": lines},
                      "properties": {k: v for k, v in props.items() if v not in (None, "")}})
    feats.sort(key=lambda f: -(f["properties"].get("km") or 0))
    dump("cables", feats, [srcinfo("cable-geo.json", licence="CC BY-NC-SA 3.0, TeleGeography"),
                           srcinfo("landing-point-geo.json", licence="CC BY-NC-SA 3.0, TeleGeography")],
         planned=sum("pl" in f["properties"] for f in feats), with_details=len(det))
    lfeats = []
    for f in lps["features"]:
        p = f["properties"]; cs = lp_cables.get(p["id"], [])
        x, y = f["geometry"]["coordinates"]
        lfeats.append(pt(x, y, {"n": p["name"], "id": p["id"], "k": len(cs) or None,
                                "cs": ", ".join(sorted(set(cs))[:12]) or None, "tbd": 1 if p.get("is_tbd") else None}))
    lfeats.sort(key=lambda f: f["properties"].get("k") or 0)
    dump("landings", lfeats, meta["cables.geojson"]["sources"])

# =====================================================================
# 2. Airports (OurAirports)
# =====================================================================
OA = "https://davidmegginson.github.io/ourairports-data"
PAVED = re.compile(r"^\s*(ASP|CON|PEM|BIT|PAV|TAR|MAC|BLACKTOP|SEALED|ASFALTO|ASPHALT|CONCRETE|BETON|HORMIG)", re.I)
MIL = re.compile(r"\b(AB|AFB|NAS|AAF|RAF|ANGB|ARB|NAF|MCAS|MCAF|JRB)\b"
                 r"|(?i:\b(air\s*base|airbase|air\s*force|air\s*station|naval\s*air|army\s*air\s*field|army\s*airfield"
                 r"|military|base\s+a[ée]rea|a[ée]rea|luftwaffe|fliegerhorst|heliporto\s+militar|army\s+heliport"
                 r"|marine\s*corps\s*air|air\s*national\s*guard)\b)")

def airports():
    ap = list(csv.DictReader(io.StringIO(cached("airports.csv", f"{OA}/airports.csv").decode("utf-8"))))
    rw = list(csv.DictReader(io.StringIO(cached("runways.csv", f"{OA}/runways.csv").decode("utf-8"))))
    best = {}
    for r in rw:
        if r["closed"] != "0": continue
        L = num(r["length_ft"])
        if not L: continue
        a = r["airport_ref"]
        if a not in best or L > best[a][0]: best[a] = (L, r["surface"])
    T = {"large_airport": "L", "medium_airport": "M", "small_airport": "S"}
    feats = []
    for a in ap:
        t = T.get(a["type"])
        lat, lon = num(a["latitude_deg"]), num(a["longitude_deg"])
        if not t or lat is None or lon is None: continue
        b = best.get(a["id"])
        feats.append(pt(lon, lat, {
            "n": a["name"].strip(), "t": t, "i": a["icao_code"] or a["ident"], "ia": a["iata_code"],
            "c": a["iso_country"], "rw": round(b[0] * 0.3048) if b else None,
            "p": 1 if b and PAVED.match(b[1] or "") else None,
            "mil": 1 if MIL.search(a["name"]) else None}))
    feats.sort(key=lambda f: "LMS".index(f["properties"]["t"]))
    mb = dump("airports", feats, [srcinfo("airports.csv", licence="Public domain"), srcinfo("runways.csv", licence="Public domain")])
    pruned = 0
    if mb > 7:
        keep = [f for f in feats if not (f["properties"]["t"] == "S" and "rw" not in f["properties"] and "p" not in f["properties"])]
        pruned = len(feats) - len(keep)
        print(f"airports > 7 MB: dropping {pruned} small airports with no runway info and not paved")
        dump("airports", keep, meta["airports.geojson"]["sources"], pruned_small_no_runway=pruned)
    c = {k: sum(f["properties"]["t"] == k for f in (keep if pruned else feats)) for k in "LMS"}
    meta["airports.geojson"]["per_type"] = c
    meta["airports.geojson"]["military_flagged"] = sum("mil" in f["properties"] for f in (keep if pruned else feats))
    print("airports per t:", c, "mil:", meta["airports.geojson"]["military_flagged"])

# =====================================================================
# 3. NGA World Port Index (Pub 150)
# =====================================================================
WPI = "https://msi.nga.mil/api/publications/download?type=view&key=16920959/SFH00000/UpdatedPub150.csv"
HS = {"very small": "V", "small": "S", "medium": "M", "large": "L"}
HT = {"coastal (natural)": "CN", "coastal (breakwater)": "CB", "coastal (tide gates)": "CT", "river (natural)": "RN",
      "river (basins)": "RB", "river (tide gates)": "RT", "canal or lake": "LC", "open roadstead": "OR", "typhoon harbor": "TH"}

def ports():
    raw = cached("UpdatedPub150.csv", WPI, ua="Mozilla/5.0 (compatible; " + UA + ")").decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(raw)))
    col = lambda *names: next(k for k in rows[0] if k.strip().lower() in [n.lower() for n in names])
    K = {"n": col("Main Port Name"), "c": col("Country Code"), "lo": col("UN/LOCODE"), "wpi": col("World Port Index Number"),
         "hs": col("Harbor Size"), "ht": col("Harbor Type"), "mv": col("Maximum Vessel Length (m)"),
         "cd": col("Channel Depth (m)"), "od": col("Oil Terminal Depth (m)"), "lat": col("Latitude"), "lon": col("Longitude")}
    pos = lambda v: (round(num(v), 1) if num(v) and num(v) > 0 else None)
    feats = []
    for r in rows:
        lat, lon = num(r[K["lat"]]), num(r[K["lon"]])
        if lat is None or lon is None: continue
        hs = HS.get(r[K["hs"]].strip().lower())
        w = num(r[K["wpi"]])
        lo = re.sub(r"\s+", "", r[K["lo"]])
        feats.append(pt(lon, lat, {
            "n": r[K["n"]].strip(), "c": r[K["c"]].strip(), "lo": lo or None, "wpi": int(w) if w else None,
            "hs": hs, "ht": HT.get(r[K["ht"]].strip().lower()), "mv": pos(r[K["mv"]]),
            "cd": pos(r[K["cd"]]), "od": pos(r[K["od"]]), "q": {"L": 4, "M": 3, "S": 2, "V": 1}.get(hs, 1)}))
    feats.sort(key=lambda f: f["properties"]["q"])
    dump("ports_wpi", feats, [srcinfo("UpdatedPub150.csv", licence="Public domain (US Government work)", edition="Pub 150")],
         per_size={k: sum(f["properties"].get("hs") == k for f in feats) for k in "LMSV"})

# =====================================================================
# 4. Data centres (PeeringDB + OSM)
# =====================================================================
PDB = "https://www.peeringdb.com/api/fac?depth=0"
OVERPASS = "https://overpass-api.de/api/interpreter"
OVQ = '[out:json][timeout:180];nwr["telecom"="data_center"];out center tags;'

def dist_m(a, b):
    x = math.radians(b[0] - a[0]) * math.cos(math.radians((a[1] + b[1]) / 2))
    return 6371000 * math.hypot(x, math.radians(b[1] - a[1]))

def datacenters():
    feats, srcs = [], []
    if not NO_PDB:
        d = json.loads(cached("peeringdb_fac.json", PDB, timeout=180))
        assert "data" in d, d
        for f in d["data"]:
            if f.get("status") != "ok" or f.get("latitude") is None or f.get("longitude") is None: continue
            feats.append(pt(f["longitude"], f["latitude"], {
                "n": (f.get("name") or "Data center").strip(), "o": f.get("org_name"), "c": f.get("country"),
                "ci": f.get("city"), "net": f.get("net_count") or None, "ix": f.get("ix_count") or None,
                "id": f["id"], "q": max(1, f.get("net_count") or 0), "src": "p"}))
        gen = d.get("meta", {}).get("generated")
        srcs.append(srcinfo("peeringdb_fac.json", licence="PeeringDB AUP (https://www.peeringdb.com/aup)",
                            version=datetime.fromtimestamp(gen, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if gen else None))
    npdb = len(feats)
    osm = None
    p = f"{RAW}/osm_datacenters.json"
    if not NO_OSM:
        if not OFFLINE:
            for k in range(2):   # Overpass is often overloaded: one retry, then give up
                try:
                    print("fetch", OVERPASS, "(telecom=data_center)")
                    b, _ = http(OVERPASS, data={"data": OVQ}, timeout=240, tries=1)
                    osm = json.loads(b)
                    open(p, "wb").write(b)
                    fetchlog["osm_datacenters.json"] = {"url": OVERPASS, "retrieved": now_utc(),
                                                        "last_modified": osm.get("osm3s", {}).get("timestamp_osm_base")}
                    break
                except Exception as e:
                    print(f"  overpass failed: {e}")
                    if k == 0: time.sleep(30)
        if osm is None and os.path.exists(p):
            print("  using cached OSM data"); osm = json.load(open(p))
    nosm = skipped = 0
    if osm:
        # grid index of PeeringDB points for the 300 m proximity test
        grid = {}
        for f in feats:
            x, y = f["geometry"]["coordinates"]; grid.setdefault((int(x * 100), int(y * 100)), []).append((x, y))
        for e in osm.get("elements", []):
            c = e.get("center") or ({"lat": e["lat"], "lon": e["lon"]} if "lat" in e else None)
            if not c: continue
            xy = (c["lon"], c["lat"]); gx, gy = int(xy[0] * 100), int(xy[1] * 100)
            near = any(dist_m(xy, q) < 300 for dx in (-1, 0, 1) for dy in (-1, 0, 1) for q in grid.get((gx + dx, gy + dy), []))
            if near: skipped += 1; continue
            tg = e.get("tags", {})
            feats.append(pt(xy[0], xy[1], {"n": (tg.get("name") or tg.get("operator") or "Data center").strip(),
                                           "o": tg.get("operator"), "q": 1, "src": "o", "osm": e["type"][0] + str(e["id"])}))
            nosm += 1
        srcs.append(srcinfo("osm_datacenters.json", licence="ODbL 1.0, © OpenStreetMap contributors", query=OVQ))
    print(f"datacenters: PeeringDB {npdb}, OSM added {nosm} (skipped {skipped} within 300 m of PeeringDB)")
    json.dump(fetchlog, open(FETCHLOG, "w"), indent=1)
    feats.sort(key=lambda f: f["properties"]["q"])
    dump("datacenters", feats, srcs, peeringdb=npdb, osm=nosm, osm_skipped_near_peeringdb=skipped)

if __name__ == "__main__":
    print("step5", "(offline)" if OFFLINE else "")
    airports(); json.dump(fetchlog, open(FETCHLOG, "w"), indent=1)
    ports(); json.dump(fetchlog, open(FETCHLOG, "w"), indent=1)
    datacenters()
    cables(); json.dump(fetchlog, open(FETCHLOG, "w"), indent=1)
    bases()
    meta["_generated"] = now_utc()
    json.dump(meta, open(f"{OUT}/infra_meta.json", "w"), indent=1, ensure_ascii=False)
    print("wrote infra_meta.json")
