#!/usr/bin/env python3
"""Fetch near-real-time snapshots into site/data/live/.

Run by a scheduled GitHub Action every few hours; devs can run it locally:

    python3 -I pipeline/live/fetch_live.py [--only events,fires,ships,aircraft,jets,airliners,navy,story]
                                           [--days 14] [--ais-seconds 240]
                                           [--archive DIR [--archive-budget-mb 1000] [--archive-long-days 1826]]

Feeds (each one independent; a failure leaves that feed's previous output):
  events   GDELT 2.0 export files, CAMEO roots 18-20, city-level geo -> events.geojson
  fires    NASA FIRMS VIIRS 24 h CSVs (NOAA-20, NOAA-21, Suomi NPP)  -> fires.json
  ships    aisstream.io WebSocket snapshot (needs AISSTREAM_API_KEY) -> ships.geojson
  aircraft adsb.lol /v2/mil                                          -> aircraft.geojson
  jets     adsb.lol /v2/type, business-jet types, LADD/PIA dropped  -> jets.geojson
  airliners adsb.lol /v2/type, airliner and regional types           -> airliners.geojson
  navy     USNI News Fleet and Marine Tracker (latest weekly post)   -> navy.geojson
  story    GDELT 1.0 dailies + 2.0 slots, regional surges per
           day / week / month window                                 -> story.json
plus manifest.json describing all of them. --archive also files this run into a
rolling, size-capped history (pipeline/live/archive.py).

Standard library only, except `websockets` (imported lazily for ships).
Raw downloads are cached under raw/live_cache/ so reruns are incremental.
"""
import argparse
import csv
import gzip
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

ROOT = os.environ.get("ATLAS_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
DATA = os.path.join(ROOT, "site", "data")
OUT = os.path.join(DATA, "live")
CACHE = os.path.join(ROOT, "raw", "live_cache")
UA = "resourcemap-live/1.0 (+https://github.com/byron123t/resourcemap)"
# events before fires (fires use events for cf) and before story (story reuses the GDELT slot cache)
FEEDS = ["events", "fires", "ships", "aircraft", "jets", "airliners", "navy", "story"]

SOURCES = {
    "fires": {"source": "NASA FIRMS VIIRS 375 m (NOAA-20, NOAA-21, Suomi NPP), last 24 h",
              "url": "https://firms.modaps.eosdis.nasa.gov/"},
    "events": {"source": "GDELT 2.0 Event Database (CAMEO 18–20, city-level)",
               "url": "https://www.gdeltproject.org/"},
    "ships": {"source": "aisstream.io AIS (terrestrial), WebSocket snapshot",
              "url": "https://aisstream.io/"},
    "aircraft": {"source": "adsb.lol /v2/mil", "url": "https://adsb.lol/"},
    "jets": {"source": "adsb.lol /v2/type (business-jet types; LADD/PIA-flagged aircraft omitted)",
             "url": "https://adsb.lol/"},
    "airliners": {"source": "adsb.lol /v2/type (airliner, regional and large freighter types)", "url": "https://adsb.lol/"},
    "navy": {"source": "USNI News Fleet and Marine Tracker (weekly; positions approximate)",
             "url": "https://news.usni.org/category/fleet-tracker"},
    "story": {"source": "GDELT 1.0 daily + 2.0 15-minute event files (CAMEO 18–20), ranked by surge",
              "url": "https://www.gdeltproject.org/"},
}


class Skip(Exception):
    """Feed intentionally not run (e.g. missing API key)."""


# ---------------------------------------------------------------- helpers

def log(feed, msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {feed:8s} {msg}", file=sys.stderr, flush=True)


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get(url, timeout=60, retries=3, not_found_ok=False):
    """GET url -> bytes. Returns None on 404 if not_found_ok. Retries other errors."""
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if r.headers.get("Content-Encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as e:
            if e.code == 404 and not_found_ok:
                return None
            last = e
            if 400 <= e.code < 500 and e.code not in (408, 429):
                break
        except Exception as e:  # URLError, timeout, connection reset, ...
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def write_json(path, obj):
    """Atomic compact JSON write; returns size in bytes."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, path)
    return os.path.getsize(path)


def dumps_len(obj):
    return len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def mb(n):
    return f"{n / 1e6:.2f} MB"


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 12742.0 * math.asin(min(1.0, math.sqrt(a)))


class PointIndex:
    """Spatial hash of (lat, lon) points in `cell`-degree cells; `near()` tests a radius in km."""

    def __init__(self, points, cell):
        self.cell = cell
        self.grid = {}
        for lat, lon in points:
            self.grid.setdefault((int(math.floor(lat / cell)), int(math.floor(lon / cell))), []).append((lat, lon))
        self.n = len(points)

    def near(self, lat, lon, km):
        c = self.cell
        dy = int(math.ceil(km / (111.0 * c)))
        coslat = max(math.cos(math.radians(min(abs(lat) + dy * c, 89.9))), 0.01)
        dx = min(int(math.ceil(km / (111.32 * coslat * c))), int(360 / c))
        cy, cx = int(math.floor(lat / c)), int(math.floor(lon / c))
        ncol = int(round(360 / c))
        for iy in range(cy - dy, cy + dy + 1):
            for ix in range(cx - dx, cx + dx + 1):
                # wrap across the antimeridian
                key = (iy, (ix + ncol // 2) % ncol - ncol // 2)
                for plat, plon in self.grid.get(key, ()):
                    if haversine_km(lat, lon, plat, plon) <= km:
                        return True
        return False


# ---------------------------------------------------------------- B. GDELT events

GDELT = "https://data.gdeltproject.org/gdeltv2/"
# GDELT 2.0 event export: 61 tab-separated columns (verified against a live file, 2026-10-08)
C_ID, C_SQLDATE, C_ROOT, C_GOLD, C_NART = 0, 1, 28, 30, 33
C_GTYPE, C_GNAME, C_GCC, C_GLAT, C_GLON, C_GFID, C_ADDED, C_URL = 51, 52, 53, 56, 57, 58, 59, 60
GDELT_NCOLS = 61
GDELT_ROOTS = {"18", "19", "20"}
GDELT_GEO_TYPES = {"3", "4"}  # US city / world city; 1/2/5 are country or state centroids


def gdelt_filter(raw_zip):
    """Zip bytes -> list of compact filtered rows."""
    z = zipfile.ZipFile(io.BytesIO(raw_zip))
    text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    rows = []
    for line in text.splitlines():
        c = line.split("\t")
        if len(c) != GDELT_NCOLS:
            continue
        if c[C_ROOT] not in GDELT_ROOTS or c[C_GTYPE] not in GDELT_GEO_TYPES:
            continue
        try:
            lat, lon = float(c[C_GLAT]), float(c[C_GLON])
            gold = float(c[C_GOLD]) if c[C_GOLD] else 0.0
            nart = int(c[C_NART] or 0)
        except ValueError:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
            continue
        # [id, sqldate, root, goldstein, articles, featureid, name, cc, lat, lon, dateadded, url]
        rows.append([c[C_ID], c[C_SQLDATE], int(c[C_ROOT]), gold, nart, c[C_GFID], c[C_GNAME],
                     c[C_GCC], lat, lon, c[C_ADDED], c[C_URL]])
    return rows


def gdelt_slot(slot, cache_dir, now):
    """Return (rows or None-if-missing, from_cache). Raises on network failure."""
    path = os.path.join(cache_dir, slot + ".json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            return d.get("rows"), True
        except Exception:
            pass  # corrupt cache -> refetch
    body = http_get(f"{GDELT}{slot}.export.CSV.zip", timeout=60, retries=3, not_found_ok=True)
    slot_dt = datetime.strptime(slot, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    if body is None:
        # Missing slots are permanent once they are old; a very recent one may still appear.
        if now - slot_dt > timedelta(hours=1):
            write_json(path, {"rows": None})
        return None, False
    rows = gdelt_filter(body)
    write_json(path, {"rows": rows})
    return rows, False


def run_events(args, ctx):
    feed = "events"
    now = utcnow()
    cache_dir = os.path.join(CACHE, "gdelt")
    os.makedirs(cache_dir, exist_ok=True)

    last = http_get(GDELT + "lastupdate.txt", timeout=30).decode("utf-8", "replace")
    m = re.search(r"/(\d{14})\.export\.CSV\.zip", last)
    if not m:
        raise RuntimeError("could not parse lastupdate.txt")
    latest = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    start = latest - timedelta(days=args.days)
    slots = []
    t = latest
    while t > start:
        slots.append(t.strftime("%Y%m%d%H%M%S"))
        t -= timedelta(minutes=15)
    log(feed, f"{len(slots)} slots {start:%Y-%m-%d %H:%M} .. {latest:%Y-%m-%d %H:%M} UTC")

    # prune cache files outside the window (+1 day slack)
    keep_after = (start - timedelta(days=1)).strftime("%Y%m%d%H%M%S")
    for fn in os.listdir(cache_dir):
        if fn.endswith(".json") and fn[:14] < keep_after:
            try:
                os.remove(os.path.join(cache_dir, fn))
            except OSError:
                pass

    all_rows, missing, failed, cached = [], 0, 0, 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(gdelt_slot, s, cache_dir, now): s for s in slots}
        for i, fu in enumerate(as_completed(futs), 1):
            try:
                rows, hit = fu.result()
                cached += hit
                if rows is None:
                    missing += 1
                else:
                    all_rows.extend(rows)
            except Exception as e:
                failed += 1
                if failed <= 5:
                    log(feed, f"warning: slot {futs[fu]}: {e}")
            if i % 200 == 0:
                log(feed, f"  {i}/{len(slots)} slots")
    log(feed, f"slots: {cached} cached, {len(slots) - cached - missing - failed} downloaded, "
              f"{missing} missing (404), {failed} failed")
    if failed > 0.2 * len(slots):
        raise RuntimeError(f"{failed}/{len(slots)} GDELT slots failed")

    # Dedupe by GlobalEventID; drop rows whose event date (SQLDATE) is well before the window:
    # those are articles about old events (anniversaries, court cases) re-coded as new.
    min_sqldate = (start - timedelta(days=1)).strftime("%Y%m%d")
    seen, rows, old = set(), [], 0
    for r in all_rows:
        if r[0] in seen:
            continue
        seen.add(r[0])
        if r[1] < min_sqldate:
            old += 1
            continue
        rows.append(r)
    log(feed, f"{len(rows)} events (roots 18-20, city-level); {old} dropped with SQLDATE before {min_sqldate}")

    cut72 = (now - timedelta(hours=72)).strftime("%Y%m%d%H%M%S")
    locs = {}
    for r in rows:
        _id, _sd, root, gold, nart, fid, name, cc, lat, lon, added, url = r
        key = fid or f"{lat:.2f},{lon:.2f}"
        L = locs.get(key)
        if L is None:
            L = locs[key] = {"n": name, "cc": cc, "lat": lat, "lon": lon, "e": 0, "a": 0,
                             "r18": 0, "r19": 0, "r20": 0, "d": added, "d0": added,
                             "gs": 0.0, "e3": 0, "urls": []}
        L["e"] += 1
        L["a"] += nart
        L["r%d" % root] += 1
        L["gs"] += gold
        if added > L["d"]:
            L["d"] = added
        if added < L["d0"]:
            L["d0"] = added
        if added >= cut72:
            L["e3"] += 1
        if url:
            L["urls"].append((added[:8], nart, added, url))

    def fmt(s):
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}"

    feats_all = []
    for L in locs.values():
        urls, seen_u = [], set()
        # most recent day first, then most-cited, then latest timestamp
        for _day, _n, _t, u in sorted(L["urls"], reverse=True):
            if u not in seen_u:
                seen_u.add(u)
                urls.append(u)
                if len(urls) == 4:
                    break
        props = {"n": L["n"], "cc": L["cc"], "e": L["e"], "a": L["a"], "r18": L["r18"],
                 "r19": L["r19"], "r20": L["r20"], "d": fmt(L["d"]), "d0": fmt(L["d0"]),
                 "g": round(L["gs"] / L["e"], 1), "u": urls, "q": L["a"], "e3": L["e3"]}
        feats_all.append({"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [round(L["lon"], 4), round(L["lat"], 4)]},
                          "properties": props})
    feats_all.sort(key=lambda f: -f["properties"]["a"])

    floor = 3
    while True:
        feats = [f for f in feats_all if f["properties"]["a"] >= floor]
        fc = {"type": "FeatureCollection", "updated": iso(now), "window_days": args.days, "features": feats}
        size = dumps_len(fc)
        if size < 4e6 or not feats:
            break
        floor = floor + 1 if floor < 10 else int(floor * 1.25)
    log(feed, f"{len(feats_all)} locations; dropped {len(feats_all) - len(feats)} with < {floor} articles")
    size = write_json(os.path.join(OUT, "events.geojson"), fc)
    log(feed, f"wrote events.geojson: {len(feats)} locations, {sum(f['properties']['e'] for f in feats)} events, {mb(size)}")
    ctx["event_points"] = [(f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0])
                           for f in feats if f["properties"]["e3"] > 0]
    return {"updated": iso(now), "count": len(feats), "events": sum(f["properties"]["e"] for f in feats),
            "window_days": args.days, "min_articles": floor, "latest_slot": iso(latest)}


# ---------------------------------------------------------------- A. FIRMS fires

FIRMS_URLS = {
    "J1": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_24h.csv",
    "J2": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_24h.csv",
    "NPP": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Global_24h.csv",
}
FIRE_GRID = 0.004          # dedupe snap, degrees (~450 m)
IND_KM, CF_KM = 2.5, 30.0
HOT_FUELS = {"coal", "gas", "oil", "bioenergy"}
DEAD_STATUS = {"proposed", "cancelled", "shelved", "discovered", "exploration", "abandoned",
               "announced", "pre-construction", "retired"}


def fetch_firms(name, url):
    cache = os.path.join(CACHE, "firms", os.path.basename(url))
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    try:
        body = http_get(url, timeout=180, retries=3)
        with open(cache + ".tmp", "wb") as f:
            f.write(body)
        os.replace(cache + ".tmp", cache)
        return body, False
    except Exception:
        # a recent cached copy is better than nothing
        if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 12 * 3600:
            with open(cache, "rb") as f:
                return f.read(), True
        raise


def industrial_points():
    pts = []
    for fn in ["oilgas", "steel", "power", "cement", "lng", "chemicals"]:
        path = os.path.join(DATA, fn + ".geojson")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        for ft in d.get("features", []):
            g, p = ft.get("geometry"), ft.get("properties") or {}
            if not g or g.get("type") != "Point":
                continue
            if fn == "power" and p.get("f") not in HOT_FUELS:
                continue
            if (p.get("s") or "").lower() in DEAD_STATUS:
                continue
            lon, lat = g["coordinates"][:2]
            pts.append((lat, lon))
    return pts


def load_event_points(ctx):
    if ctx.get("event_points") is not None:
        return ctx["event_points"], "this run"
    path = os.path.join(OUT, "events.geojson")
    if not os.path.exists(path):
        return [], "none"
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        pts = [(ft["geometry"]["coordinates"][1], ft["geometry"]["coordinates"][0])
               for ft in d.get("features", []) if (ft.get("properties") or {}).get("e3", 0) > 0]
        return pts, "existing events.geojson (" + str(d.get("updated", "?")) + ")"
    except Exception:
        return [], "none"


def run_fires(args, ctx):
    feed = "fires"
    now = utcnow()
    bodies = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(fetch_firms, k, u): k for k, u in FIRMS_URLS.items()}
        for fu in as_completed(futs):
            k = futs[fu]
            try:
                body, stale = fu.result()
                bodies[k] = body
                log(feed, f"{k}: {mb(len(body))}" + (" (from cache)" if stale else ""))
            except Exception as e:
                log(feed, f"warning: {k}: {e}")
    if not bodies:
        raise RuntimeError("all FIRMS downloads failed")

    cells, total, low = {}, 0, 0
    for k, body in bodies.items():
        rd = csv.DictReader(io.StringIO(body.decode("utf-8", "replace")))
        for r in rd:
            total += 1
            if (r.get("confidence") or "").strip().lower() in ("l", "low"):
                low += 1
                continue
            try:
                lat, lon = float(r["latitude"]), float(r["longitude"])
                frp = float(r.get("frp") or 0)
                ti4 = float(r.get("bright_ti4") or 0)
            except (ValueError, KeyError):
                continue
            key = (round(lat / FIRE_GRID), round(lon / FIRE_GRID))
            c = cells.get(key)
            if c is None:
                cells[key] = [lat, lon, frp, ti4, r.get("acq_date", ""), r.get("acq_time", ""),
                              (r.get("daynight") or "").strip().upper()[:1], 1]
            else:
                c[7] += 1
                if frp > c[2]:
                    c[:7] = [lat, lon, frp, ti4, r.get("acq_date", ""), r.get("acq_time", ""),
                             (r.get("daynight") or "").strip().upper()[:1]]
    log(feed, f"{total} detections from {len(bodies)} satellites, {low} low-confidence dropped, "
              f"{len(cells)} after dedupe")

    ind_idx = PointIndex(industrial_points(), 0.05)
    ev_pts, ev_src = load_event_points(ctx)
    ev_idx = PointIndex(ev_pts, 0.5)
    log(feed, f"{ind_idx.n} industrial heat sources; {ev_idx.n} conflict locations (last 72 h) from {ev_src}")

    rows = []
    for lat, lon, frp, ti4, d, tm, dn, nd in cells.values():
        tm = (tm or "").strip().zfill(4)
        t = f"{d}T{tm[:2]}:{tm[2:4]}Z" if d else ""
        ind = 1 if ind_idx.n and ind_idx.near(lat, lon, IND_KM) else 0
        cf = 1 if ev_idx.n and ev_idx.near(lat, lon, CF_KM) else 0
        rows.append([round(lon, 3), round(lat, 3), round(frp, 1), int(round(ti4)), t, dn, nd, ind, cf])
    rows.sort(key=lambda r: -r[2])

    def doc(rs):
        return {"updated": iso(now), "cols": ["lon", "lat", "frp", "ti4", "t", "dn", "nd", "ind", "cf"], "rows": rs}

    limit, notes = 14e6, []  # GitHub Pages gzips JSON about 4:1, so this is ~3.5 MB on the wire
    size = dumps_len(doc(rows))
    if size >= limit:
        before = len(rows)
        rows = [r for r in rows if not (r[2] < 1.0 and r[7] == 1)]
        notes.append(f"dropped {before - len(rows)} industrial rows with frp < 1.0")
        size = dumps_len(doc(rows))
        floor, before = 0.0, len(rows)
        while size >= limit and rows:
            floor = round(floor + 0.5, 1)
            rows = [r for r in rows if r[2] >= floor]
            size = dumps_len(doc(rows))
        if floor:
            notes.append(f"frp floor {floor} MW: dropped {before - len(rows)} more")
        for n in notes:
            log(feed, "size trim: " + n)
    size = write_json(os.path.join(OUT, "fires.json"), doc(rows))
    n_ind = sum(r[7] for r in rows)
    n_cf = sum(r[8] for r in rows)
    log(feed, f"wrote fires.json: {len(rows)} rows ({n_ind} ind, {n_cf} cf), {mb(size)}")
    out = {"updated": iso(now), "count": len(rows), "industrial": n_ind, "conflict": n_cf,
           "satellites": sorted(bodies)}
    if notes:
        out["trimmed"] = "; ".join(notes)
    return out


# ---------------------------------------------------------------- C. AIS ships

AIS_URL = "wss://stream.aisstream.io/v0/stream"
NAVY_STRONG = ("USS|USNS|HMS|HMAS|HMCS|HMNZS|INS|ROKS|FGS|ESPS|HNLMS|TCG|PNS|KRI|BRP|RFS|"
               "HDMS|KNM|HSWMS|ORP|NRP|ROCS")
NAVY_WEAK = "JS|HS|FS|SAS|ITS|CNS|ENS|SPS|ARA|BAP"  # also common merchant prefixes
RE_NAVY_STRONG = re.compile(r"^(%s)\b" % NAVY_STRONG)
RE_NAVY_WEAK = re.compile(r"^(%s)\b" % NAVY_WEAK)
RE_HULL = re.compile(r"\b(CVN|CVH|CV|DDG|DDH|DD|FFG|FFH|FF|LHD|LHA|LPD|LPH|LSD|LCS|CG|SSN|SSBN|SSK|"
                     r"AOE|AOR|AO|T ?AO|T ?AKE|T ?EPF|[DFLAPKRS] ?\d{2,4})\b")
CARRIERS = ["NIMITZ", "DWIGHT D EISENHOWER", "EISENHOWER", "CARL VINSON", "THEODORE ROOSEVELT",
            "ABRAHAM LINCOLN", "GEORGE WASHINGTON", "JOHN C STENNIS", "HARRY S TRUMAN", "RONALD REAGAN",
            "GEORGE H W BUSH", "GEORGE HW BUSH", "GERALD R FORD", "JOHN F KENNEDY",
            "QUEEN ELIZABETH", "PRINCE OF WALES", "CHARLES DE GAULLE", "CAVOUR", "TRIESTE",
            "JUAN CARLOS I", "VIKRANT", "VIKRAMADITYA", "LIAONING", "SHANDONG", "FUJIAN", "IZUMO",
            "KAGA", "KUZNETSOV", "ADMIRAL KUZNETSOV", "SAO PAULO"]
RE_CARRIER = re.compile(r"\b(%s)\b" % "|".join(sorted(CARRIERS, key=len, reverse=True)))


def norm_name(s):
    s = (s or "").upper().replace("@", " ")
    s = re.sub(r"[.,'`\"()\-_/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def classify_ship(name, ty, length):
    """-> class string or None (drop)."""
    nn = norm_name(name)
    ty = ty or 0
    mil = ty == 35
    if not mil and nn:
        if RE_NAVY_STRONG.match(nn):
            mil = True
        elif RE_NAVY_WEAK.match(nn) and (ty == 0 or 51 <= ty <= 59 or RE_HULL.search(nn)):
            mil = True
    if mil:
        return "carrier" if RE_CARRIER.search(nn) else "military"
    if ty == 55:
        return "law"
    if 80 <= ty <= 89:
        return "tanker"
    if 70 <= ty <= 79:
        return "cargo"
    if 60 <= ty <= 69:
        return "passenger" if (length or 0) >= 150 else None
    if (length or 0) >= 200:
        return "other"
    return None


def parse_ais_time(s):
    # MetaData.time_utc looks like "2026-10-08 21:45:03.123456789 +0000 UTC"
    if not s:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", str(s))
    return f"{m.group(1)}T{m.group(2)}Z" if m else None


async def ais_listen(key, seconds, feed):
    import asyncio
    import websockets  # noqa: lazily imported; optional dependency

    pos, static = {}, {}
    n_msgs = 0
    deadline = time.monotonic() + seconds
    sub = {"APIKey": key, "BoundingBoxes": [[[-90, -180], [90, 180]]],
           "FilterMessageTypes": ["PositionReport", "ShipStaticData", "StandardClassBPositionReport"]}
    attempts = 0
    while time.monotonic() < deadline and attempts < 4:
        attempts += 1
        try:
            async with websockets.connect(AIS_URL, compression="deflate", max_size=None,
                                          open_timeout=20, ping_interval=20, ping_timeout=20,
                                          user_agent_header=UA) as ws:
                await ws.send(json.dumps(sub))  # must arrive within 3 s of connecting
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    try:
                        frame = await asyncio.wait_for(ws.recv(), timeout=min(left, 30))
                    except asyncio.TimeoutError:
                        if time.monotonic() >= deadline:
                            break
                        raise RuntimeError("no AIS messages for 30 s")
                    if isinstance(frame, bytes):
                        frame = frame.decode("utf-8", "replace")
                    try:
                        msg = json.loads(frame)
                    except ValueError:
                        continue
                    if "error" in msg:
                        raise RuntimeError("aisstream error: " + str(msg["error"]))
                    mt = msg.get("MessageType")
                    body = (msg.get("Message") or {}).get(mt) or {}
                    meta = msg.get("MetaData") or {}
                    if mt == "SubscriptionConfirmation":
                        log(feed, "subscription confirmed")
                        continue
                    mmsi = meta.get("MMSI") or body.get("UserID")
                    if not mmsi:
                        continue
                    n_msgs += 1
                    if mt == "ShipStaticData":
                        dim = body.get("Dimension") or {}
                        static[mmsi] = {"n": (body.get("Name") or "").replace("@", " ").strip(),
                                        "ty": body.get("Type") or 0,
                                        "len": (dim.get("A") or 0) + (dim.get("B") or 0),
                                        "dest": (body.get("Destination") or "").replace("@", " ").strip(),
                                        "imo": body.get("ImoNumber") or 0,
                                        "cs": (body.get("CallSign") or "").replace("@", " ").strip(),
                                        "seen": time.time()}
                    elif mt in ("PositionReport", "StandardClassBPositionReport"):
                        if body.get("Valid") is False:
                            continue
                        lat = body.get("Latitude", meta.get("latitude", meta.get("Latitude")))
                        lon = body.get("Longitude", meta.get("longitude", meta.get("Longitude")))
                        if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
                            continue
                        pos[mmsi] = {"lat": lat, "lon": lon, "sog": body.get("Sog"), "cog": body.get("Cog"),
                                     "hd": body.get("TrueHeading"), "nav": body.get("NavigationalStatus"),
                                     "mn": (meta.get("ShipName") or "").replace("@", " ").strip(),
                                     "t": parse_ais_time(meta.get("time_utc")) or iso(utcnow())}
        except Exception as e:
            log(feed, f"warning: connection {attempts}: {e}")
            if "aisstream error" in str(e):
                raise
            await asyncio.sleep(min(2 ** attempts, 10))
    return pos, static, n_msgs


def run_ships(args, ctx):
    feed = "ships"
    key = os.environ.get("AISSTREAM_API_KEY", "").strip()
    if not key:
        log(feed, "AISSTREAM_API_KEY not set; skipping ships")
        raise Skip("AISSTREAM_API_KEY not set")
    try:
        import websockets  # noqa: F401
    except ImportError:
        log(feed, "websockets package not installed; skipping ships")
        raise Skip("websockets package not installed")
    import asyncio

    now = utcnow()
    log(feed, f"listening {args.ais_seconds} s")
    pos, static, n_msgs = asyncio.run(ais_listen(key, args.ais_seconds, feed))
    log(feed, f"{n_msgs} messages, {len(pos)} positions, {len(static)} static records")
    if not pos:
        raise RuntimeError("no AIS positions received")

    # Static data is only broadcast every ~6 min, so keep a 30-day cache across runs.
    spath = os.path.join(CACHE, "ais_static.json")
    cached = {}
    try:
        with open(spath, encoding="utf-8") as f:
            cached = {int(k): v for k, v in json.load(f).items()}
    except Exception:
        pass
    cached.update(static)
    cutoff = time.time() - 30 * 86400
    cached = {k: v for k, v in cached.items() if v.get("seen", 0) > cutoff}
    write_json(spath, {str(k): v for k, v in cached.items()})

    feats, counts = [], {}
    for mmsi, p in pos.items():
        s = cached.get(mmsi, {})
        name = s.get("n") or p["mn"]
        ty, length = s.get("ty") or 0, s.get("len") or None
        cl = classify_ship(name, ty, length)
        if not cl:
            continue
        counts[cl] = counts.get(cl, 0) + 1
        sog, cog, hd = p["sog"], p["cog"], p["hd"]
        props = {"mmsi": mmsi, "n": name, "cl": cl, "ty": ty,
                 "sog": round(sog, 1) if sog is not None and sog < 102.3 else None,
                 "cog": round(cog, 1) if cog is not None and cog < 360 else None,
                 "hd": hd if hd is not None and hd < 360 else None,
                 "dest": s.get("dest") or None, "len": length,
                 "imo": s.get("imo") or None, "t": p["t"]}
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [round(p["lon"], 4), round(p["lat"], 4)]},
                      "properties": {k: v for k, v in props.items() if v is not None}})
    order = {"carrier": 0, "military": 1, "law": 2, "tanker": 3, "cargo": 4, "passenger": 5, "other": 6}
    feats.sort(key=lambda f: order[f["properties"]["cl"]])
    fc = {"type": "FeatureCollection", "updated": iso(now), "features": feats}
    size = write_json(os.path.join(OUT, "ships.geojson"), fc)
    log(feed, f"wrote ships.geojson: {len(feats)} ships {counts}, {mb(size)}")
    return {"updated": iso(now), "count": len(feats), "classes": counts, "listen_seconds": args.ais_seconds}


# ---------------------------------------------------------------- D. military aircraft

def aircraft_feature(a):
    """adsb.lol aircraft record -> GeoJSON point, or None without a recent position."""
    lat, lon = a.get("lat"), a.get("lon")
    if lat is None or lon is None:
        return None
    if (a.get("seen_pos") if a.get("seen_pos") is not None else 1e9) >= 300:
        return None
    alt = a.get("alt_baro")
    props = {"hex": a.get("hex"), "f": (a.get("flight") or "").strip() or None,
             "r": a.get("r"), "ty": a.get("t")}
    if alt == "ground":
        props["alt"], props["gnd"] = 0, 1
    elif isinstance(alt, (int, float)):
        props["alt"] = int(alt)
    props["gs"] = round(a["gs"], 1) if isinstance(a.get("gs"), (int, float)) else None
    props["trk"] = round(a["track"], 1) if isinstance(a.get("track"), (int, float)) else None
    props["sq"] = a.get("squawk")
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]},
            "properties": {k: v for k, v in props.items() if v is not None}}


def run_aircraft(args, ctx):
    feed = "aircraft"
    now = utcnow()
    d = json.loads(http_get("https://api.adsb.lol/v2/mil", timeout=60))
    ac = d.get("ac") or []
    feats = [f for f in map(aircraft_feature, ac) if f]
    if not feats and not ac:
        raise RuntimeError("adsb.lol returned no aircraft")
    fc = {"type": "FeatureCollection", "updated": iso(now), "features": feats}
    size = write_json(os.path.join(OUT, "aircraft.geojson"), fc)
    log(feed, f"wrote aircraft.geojson: {len(feats)} of {len(ac)} aircraft, {mb(size)}")
    return {"updated": iso(now), "count": len(feats)}


# ---------------------------------------------------------------- D2. business jets and airliners

# ICAO type designators for business jets, queried one by one (adsb.lol has no working
# global dump). Light jets (C510, C525, E50P, ...) are left out to keep a run near 3 min.
JET_TYPES = {
    "GLF4": "Gulfstream IV", "GLF5": "Gulfstream V", "GLF6": "Gulfstream G650", "GA5C": "Gulfstream G500",
    "GA6C": "Gulfstream G600", "GA7C": "Gulfstream G700", "GA8C": "Gulfstream G800", "GL5T": "Bombardier Global 5000/5500",
    "GL7T": "Bombardier Global 7500", "GLEX": "Bombardier Global Express/6000", "GL8T": "Bombardier Global 8000",
    "CL30": "Bombardier Challenger 300", "CL35": "Bombardier Challenger 350", "CL60": "Bombardier Challenger 600 series",
    "FA7X": "Dassault Falcon 7X", "FA8X": "Dassault Falcon 8X", "F900": "Dassault Falcon 900", "F2TH": "Dassault Falcon 2000",
    "FA50": "Dassault Falcon 50", "FA6X": "Dassault Falcon 6X", "FA10": "Dassault Falcon 10",
    "E35L": "Embraer Legacy 600/650", "E55P": "Embraer Phenom 300", "E545": "Embraer Praetor 500 / Legacy 450",
    "E550": "Embraer Praetor 600 / Legacy 500", "LJ45": "Learjet 45", "LJ60": "Learjet 60", "LJ75": "Learjet 75",
    "C56X": "Cessna Citation Excel/XLS", "C680": "Cessna Citation Sovereign", "C68A": "Cessna Citation Latitude",
    "C700": "Cessna Citation Longitude", "C750": "Cessna Citation X", "H25B": "Hawker 800", "PC24": "Pilatus PC-24",
}
# dbFlags bits: 1 military (already in aircraft.geojson), 4 PIA (privacy ICAO address),
# 8 LADD (owner asked the FAA not to have the aircraft displayed). We honour both opt-outs.
JET_DROP = 1 | 4 | 8


# Mainline, regional and large freighter types; together about 90% of airliner traffic.
AIRLINER_TYPES = {
    "A318": "Airbus A318", "A319": "Airbus A319", "A320": "Airbus A320", "A321": "Airbus A321",
    "A19N": "Airbus A319neo", "A20N": "Airbus A320neo", "A21N": "Airbus A321neo",
    "A332": "Airbus A330-200", "A333": "Airbus A330-300", "A338": "Airbus A330-800neo", "A339": "Airbus A330-900neo",
    "A343": "Airbus A340-300", "A346": "Airbus A340-600", "A359": "Airbus A350-900", "A35K": "Airbus A350-1000",
    "A388": "Airbus A380", "BCS1": "Airbus A220-100", "BCS3": "Airbus A220-300",
    "B712": "Boeing 717", "B733": "Boeing 737-300", "B734": "Boeing 737-400", "B735": "Boeing 737-500",
    "B736": "Boeing 737-600", "B737": "Boeing 737-700", "B738": "Boeing 737-800", "B739": "Boeing 737-900",
    "B37M": "Boeing 737 MAX 7", "B38M": "Boeing 737 MAX 8", "B39M": "Boeing 737 MAX 9", "B3XM": "Boeing 737 MAX 10",
    "B744": "Boeing 747-400", "B748": "Boeing 747-8", "B752": "Boeing 757-200", "B753": "Boeing 757-300",
    "B762": "Boeing 767-200", "B763": "Boeing 767-300", "B764": "Boeing 767-400",
    "B772": "Boeing 777-200", "B77L": "Boeing 777-200LR / 777F", "B773": "Boeing 777-300", "B77W": "Boeing 777-300ER",
    "B788": "Boeing 787-8", "B789": "Boeing 787-9", "B78X": "Boeing 787-10", "MD11": "McDonnell Douglas MD-11",
    "E170": "Embraer E170", "E75S": "Embraer E175", "E75L": "Embraer E175", "E190": "Embraer E190",
    "E195": "Embraer E195", "E290": "Embraer E190-E2", "E295": "Embraer E195-E2",
    "CRJ2": "Bombardier CRJ200", "CRJ7": "Bombardier CRJ700", "CRJ9": "Bombardier CRJ900", "CRJX": "Bombardier CRJ1000",
    "AT72": "ATR 72", "AT75": "ATR 72-500", "AT76": "ATR 72-600", "DH8D": "De Havilland Dash 8-400",
    "C919": "COMAC C919", "AJ27": "COMAC ARJ21", "SU95": "Sukhoi Superjet 100",
}


def fetch_types(feed, types, fname):
    """Query adsb.lol /v2/type for each type, drop opted-out and military aircraft, write fname."""
    now = utcnow()
    seen, feats, total, dropped, failed = set(), [], 0, 0, []
    for i, t in enumerate(types):
        if i:
            time.sleep(2)  # adsb.lol answers bursts with 429
        body = None
        for wait in (0, 10, 20, 40):  # back off on 429, give up on other errors
            time.sleep(wait)
            try:
                body = http_get(f"https://api.adsb.lol/v2/type/{t}", timeout=60, retries=1)
                break
            except RuntimeError as e:
                if "429" not in str(e):
                    break
        if body is None:
            failed.append(t)
            continue
        for a in json.loads(body).get("ac") or []:
            if a.get("hex") in seen:
                continue
            seen.add(a.get("hex"))
            total += 1
            if (a.get("dbFlags") or 0) & JET_DROP or str(a.get("hex", "")).startswith("~"):
                dropped += 1
                continue
            f = aircraft_feature(a)
            if f:
                f["properties"]["desc"] = types.get(a.get("t"), types[t])
                feats.append(f)
    if len(failed) > len(types) // 2:
        raise RuntimeError(f"adsb.lol type queries failed for {len(failed)} of {len(types)} types")
    fc = {"type": "FeatureCollection", "updated": iso(now), "features": feats}
    size = write_json(os.path.join(OUT, fname), fc)
    log(feed, f"wrote {fname}: {len(feats)} of {total} aircraft ({dropped} privacy/military flagged), "
              f"failed types: {failed or '-'}, {mb(size)}")
    return {"updated": iso(now), "count": len(feats), "types": len(types) - len(failed)}


def run_jets(args, ctx):
    return fetch_types("jets", JET_TYPES, "jets.geojson")


def run_airliners(args, ctx):
    return fetch_types("airliners", AIRLINER_TYPES, "airliners.geojson")


# ---------------------------------------------------------------- E. US Navy deployments (USNI Fleet Tracker)

USNI_API = "https://news.usni.org/wp-json/wp/v2/posts"
RE_USNI_SHIP = re.compile(r"\b(USS|USNS)\s+([A-Z][^()<>]{1,40}?)\s*\(((?:T-)?[A-Z]{2,4})[-‑ ]?(\d{1,3})\)")
NAVY_CLASS = {"CVN": "carrier", "CV": "carrier", "LHA": "amphib", "LHD": "amphib", "LPD": "amphib", "LSD": "amphib",
              "LCC": "amphib", "DDG": "surface", "CG": "surface", "FFG": "surface", "LCS": "surface", "DD": "surface",
              "SSN": "sub", "SSBN": "sub", "SSGN": "sub"}
# (pattern, lat, lon, label, kind). Earliest match in the text wins; ties go to the longer match.
# "port" entries are spread tightly, "sea" entries widely.
NAVY_GAZ = [(p, la, lo, lab or p.split("|")[0].replace("\\", ""), k) for p, la, lo, lab, k in [
    ("Sasebo", 33.16, 129.72, "", "port"), ("Yokosuka", 35.29, 139.67, "", "port"),
    ("White Beach|Okinawa", 26.30, 127.90, "Okinawa", "port"), ("Apra Harbor|Guam", 13.44, 144.65, "Guam", "port"),
    ("Pearl Harbor|Hawaii", 21.35, -157.95, "Pearl Harbor", "port"),
    ("North Island|San Diego", 32.69, -117.20, "San Diego", "port"), ("Norfolk", 36.95, -76.33, "", "port"),
    ("Mayport", 30.39, -81.40, "", "port"), ("Everett", 47.99, -122.22, "", "port"),
    ("Bremerton|Puget Sound", 47.56, -122.63, "Bremerton", "port"), ("Rota", 36.62, -6.33, "", "port"),
    ("Souda Bay|Crete", 35.49, 24.12, "Souda Bay", "port"), ("Naples", 40.84, 14.27, "", "port"),
    ("Bahrain|Manama", 26.21, 50.61, "Bahrain", "port"), ("Duqm", 19.60, 57.70, "", "port"),
    ("Djibouti", 11.60, 43.15, "", "port"), ("Phuket", 7.82, 98.40, "", "port"),
    ("Singapore|Changi", 1.32, 104.00, "Singapore", "port"), ("Busan", 35.10, 129.04, "", "port"),
    ("Subic Bay|Manila", 14.80, 120.27, "Subic Bay", "port"), ("Da Nang", 16.07, 108.22, "", "port"),
    ("Darwin", -12.46, 130.84, "", "port"), ("Sydney", -33.85, 151.25, "", "port"),
    ("Diego Garcia", -7.30, 72.40, "", "port"), ("Halifax", 44.65, -63.57, "", "port"),
    ("Portsmouth", 50.80, -1.10, "", "port"), ("Plymouth", 50.36, -4.16, "", "port"),
    ("Reykjavik", 64.15, -21.95, "", "port"), ("Kiel", 54.33, 10.15, "", "port"), ("Oslo", 59.90, 10.70, "", "port"),
    ("Bergen", 60.40, 5.30, "", "port"), ("Gdynia", 54.53, 18.55, "", "port"), ("Tallinn", 59.45, 24.75, "", "port"),
    ("Helsinki", 60.16, 24.95, "", "port"), ("Stockholm", 59.32, 18.08, "", "port"),
    ("Copenhagen", 55.69, 12.60, "", "port"), ("Constanta", 44.17, 28.65, "", "port"),
    ("Limassol|Cyprus", 34.67, 33.04, "Cyprus", "port"), ("Haifa", 32.82, 35.00, "", "port"),
    ("Jeddah", 21.50, 39.15, "", "port"), ("Mombasa", -4.05, 39.67, "", "port"),
    ("Guantanamo", 19.90, -75.15, "", "port"), ("San Juan|Puerto Rico", 18.46, -66.10, "Puerto Rico", "port"),
    ("Key West", 24.55, -81.80, "", "port"), ("Pensacola", 30.35, -87.27, "", "port"),
    ("Jacksonville", 30.33, -81.65, "", "port"), ("New York", 40.68, -74.03, "", "port"),
    ("Baltimore", 39.26, -76.58, "", "port"), ("Hong Kong", 22.30, 114.17, "", "port"),
    ("Colombo", 6.95, 79.84, "", "port"), ("Chennai", 13.10, 80.30, "", "port"), ("Goa", 15.40, 73.80, "", "port"),
    ("Mumbai", 18.93, 72.84, "", "port"), ("Karachi", 24.84, 66.97, "", "port"), ("Muscat", 23.63, 58.57, "", "port"),
    ("Jebel Ali|Dubai", 25.01, 55.06, "Jebel Ali", "port"), ("Salalah", 16.94, 54.00, "", "port"),
    ("Aqaba", 29.52, 35.00, "", "port"), ("Alexandria", 31.20, 29.88, "", "port"),
    ("Valletta|Malta", 35.90, 14.52, "Malta", "port"), ("Toulon", 43.10, 5.92, "", "port"),
    ("Marseille", 43.33, 5.35, "", "port"), ("Brest", 48.38, -4.49, "", "port"), ("Lisbon", 38.70, -9.15, "", "port"),
    ("Gibraltar", 36.14, -5.35, "", "port"), ("Dakar", 14.68, -17.43, "", "port"), ("Lagos", 6.43, 3.40, "", "port"),
    ("Rio de Janeiro", -22.89, -43.17, "", "port"), ("Cartagena", 10.40, -75.53, "", "port"),
    ("Panama", 8.95, -79.55, "", "port"), ("Esquimalt", 48.43, -123.43, "", "port"),
    ("Tokyo", 35.62, 139.78, "", "port"), ("Bahamas", 25.05, -77.35, "", "port"),
    ("off the coast of Virginia", 36.80, -75.00, "off Virginia", "sea"),
    ("off the coast of Southern California|Southern California", 32.50, -118.30, "off Southern California", "sea"),
    ("off the coast of Florida", 30.00, -80.30, "off Florida", "sea"),
    ("Philippine Sea", 20.0, 130.0, "", "sea"), ("South China Sea", 14.0, 114.0, "", "sea"),
    ("East China Sea", 29.0, 125.0, "", "sea"), ("Sea of Japan|East Sea", 40.0, 135.0, "Sea of Japan", "sea"),
    ("Yellow Sea", 36.0, 123.0, "", "sea"), ("Gulf of Oman", 24.5, 58.5, "", "sea"),
    ("Strait of Hormuz", 26.4, 56.6, "", "sea"), ("Persian Gulf|Arabian Gulf", 27.0, 51.5, "Persian Gulf", "sea"),
    ("North Arabian Sea", 21.0, 62.0, "", "sea"), ("Arabian Sea", 17.0, 63.0, "", "sea"),
    ("Gulf of Aden", 12.5, 47.5, "", "sea"), ("Bab el-Mandeb", 12.6, 43.4, "", "sea"),
    ("Red Sea", 20.0, 38.5, "", "sea"), ("Suez Canal", 30.5, 32.35, "", "sea"),
    ("Eastern Mediterranean", 34.0, 30.0, "", "sea"), ("Adriatic", 42.5, 16.0, "Adriatic Sea", "sea"),
    ("Aegean", 38.5, 25.0, "Aegean Sea", "sea"), ("Ionian Sea", 38.0, 19.0, "", "sea"),
    ("Tyrrhenian Sea", 40.0, 12.0, "", "sea"), ("Mediterranean", 35.5, 18.0, "Mediterranean Sea", "sea"),
    ("Black Sea", 43.3, 34.0, "", "sea"), ("Baltic", 56.5, 19.0, "Baltic Sea", "sea"),
    ("North Sea", 56.0, 3.0, "", "sea"), ("Norwegian Sea", 67.0, 5.0, "", "sea"),
    ("Barents Sea", 72.0, 35.0, "", "sea"), ("Arctic", 75.0, 0.0, "Arctic Ocean", "sea"),
    ("North Atlantic", 45.0, -35.0, "", "sea"), ("Western Atlantic", 34.0, -70.0, "", "sea"),
    ("Eastern Atlantic", 40.0, -18.0, "", "sea"), ("Atlantic", 35.0, -40.0, "Atlantic Ocean", "sea"),
    ("Central Caribbean", 15.0, -75.0, "", "sea"), ("Eastern Caribbean", 15.0, -64.0, "", "sea"),
    ("Southern Caribbean", 12.5, -70.0, "", "sea"), ("Caribbean", 15.0, -75.0, "Caribbean Sea", "sea"),
    ("Gulf of Mexico", 25.0, -90.0, "", "sea"), ("Gulf of Alaska", 57.0, -145.0, "", "sea"),
    ("Bering Sea", 58.0, -175.0, "", "sea"), ("Eastern Pacific", 25.0, -125.0, "", "sea"),
    ("Western Pacific|7th Fleet", 20.0, 140.0, "Western Pacific", "sea"),
    ("Pacific", 25.0, -145.0, "Pacific Ocean", "sea"), ("Bay of Bengal", 15.0, 88.0, "", "sea"),
    ("Andaman Sea", 10.0, 96.0, "", "sea"), ("Strait of Malacca", 3.5, 100.5, "", "sea"),
    ("Indian Ocean", 0.0, 75.0, "", "sea"), ("Coral Sea", -18.0, 155.0, "", "sea"),
    ("Tasman Sea", -38.0, 160.0, "", "sea"), ("Celebes Sea", 4.0, 122.0, "", "sea"),
    ("Sulu Sea", 9.0, 120.0, "", "sea"), ("Java Sea", -5.0, 111.0, "", "sea"),
    ("Gulf of Thailand|Thailand", 10.0, 101.5, "Gulf of Thailand", "sea"),
    ("Japan", 33.5, 135.5, "off Japan", "sea"), ("Spain", 36.62, -6.33, "Rota", "port"),
    ("Virginia", 36.95, -76.33, "Norfolk", "port"), ("California", 32.69, -117.20, "San Diego", "port"),
]]
NAVY_GAZ_RE = [(re.compile(r"\b(?:%s)\b" % p, re.I), la, lo, lab, k) for p, la, lo, lab, k in NAVY_GAZ]
# clauses that name a place the ship is NOT at right now
RE_NAVY_NOISE = [re.compile(p, re.I) for p in [
    r",?\s*(?:which is |is |are )?home-?ported (?:at|in) .*?(?=,\s*(?:is|are|was|were|which|and)\b|\.\s|\.$|$)",
    r",?\s*(?:is |are )?based (?:at|in) .*?(?=,\s*(?:is|are|was|were|which|and)\b|\.\s|\.$|$)",
    r"\bpreviously\b[^.]*", r"\bfollowing\b[^.]*", r"\ben route (?:home )?to\b[^.]*",
    r"\bfrom (?:Naval|Marine Corps) (?:Air )?Station\b[^.]*"]]


def html_text(s):
    import html as _html
    return _html.unescape(re.sub(r"<[^>]+>", "", s)).replace("\xa0", " ").strip()


def navy_clean(t):
    if re.search(r"\barrived\b", t):
        t = re.sub(r"\bdeparted\b.*?(?=\band arrived\b)", "", t, flags=re.I)
    else:  # "departed on deployment Sept. 28 from San Diego": it is no longer there
        # (a "." followed by a digit or letter, as in "Sept. 28", doesn't end the sentence)
        t = re.sub(r"\b(?:departed|left)\b(?:[^.]|\.(?=\s?\d|\w))*?\bfrom\b(?:[^.]|\.(?=\s?\d|\w))*", "", t, flags=re.I)
    for rx in RE_NAVY_NOISE:
        t = rx.sub("", t)
    return t


def navy_place(t):
    """Earliest gazetteer match in t -> (lat, lon, label, kind) or None."""
    best = None
    for rx, la, lo, lab, k in NAVY_GAZ_RE:
        m = rx.search(t)
        if m and (best is None or (m.start(), -len(m.group(0))) < best[0]):
            best = ((m.start(), -len(m.group(0))), (la, lo, lab, k))
    return best[1] if best else None


def parse_usni(content):
    """Fleet Tracker post HTML -> {hull: ship dict} with the best location found for each ship."""
    c = re.sub(r"(?s)<figure.*?</figure>|<blockquote.*?</blockquote>", "", content)
    ships, order, group_moves = {}, [], []
    section = intro = group = None
    RE_GRP_NARR = re.compile(r"\bThe ([A-Z][\w.\- ]+?) (Carrier Strike Group|Amphibious Ready Group)\b(?! (?:includes|is composed of))")

    def put(name, hull, place, prio, grp, ctx):
        s = ships.get(hull)
        if s is None:
            s = ships[hull] = {"n": name, "h": hull, "prio": -1, "place": None, "grp": None, "s": ""}
            order.append(hull)
        if grp and not s["grp"]:
            s["grp"] = grp
        if place and prio > s["prio"]:
            s.update(prio=prio, place=place, s=ctx[:260])

    for m in re.finditer(r"(?s)<(h[1-6]|p|li)\b[^>]*>(.*?)</\1>", c):
        tag, text = m.group(1), html_text(m.group(2))
        if not text:
            continue
        if tag in ("h2", "h1"):
            section, intro, group = navy_place(text), None, None
            continue
        if tag.startswith("h"):  # h3/h4: strike group / ready group roster
            intro = None
            group = text if re.search(r"Strike Group|Ready Group|Task Force", text) else None
            continue
        mentions = list(RE_USNI_SHIP.finditer(text))
        # narrative about a group ("The Abraham Lincoln Carrier Strike Group is transiting ..."):
        # applied to the flagship after parsing, since its roster may come later
        gm = RE_GRP_NARR.search(text)
        if gm and tag == "p":
            pl = navy_place(navy_clean(RE_USNI_SHIP.sub(" ", text[gm.end():].split(". ")[0])))
            if pl:
                group_moves.append((norm_name(gm.group(1)).replace(" ", ""), pl, text))
        if not mentions:
            if tag == "p":
                intro = navy_place(navy_clean(text))
                # a long narrative paragraph ends a roster; short ones are roster sub-headings
                if len(text) > 60 and "embarked" not in text:
                    group = None
            continue
        gm = re.search(r"\bThe ([A-Z][\w.\- ]+?) (Carrier Strike Group|Amphibious Ready Group) (?:includes|is composed of)", text)
        blk_grp = (gm.group(1) + (" CSG" if gm.group(2).startswith("Carrier") else " ARG")) if gm else None
        stripped = RE_USNI_SHIP.sub(" ", text)
        blk_place = navy_place(navy_clean(stripped))
        roster = tag == "li" or len(text) < 140
        if not roster:
            group = None
        if re.search(r"\bindependent(?:ly)? deploy", text, re.I):
            blk_grp = None
            group = None
        for i, mm in enumerate(mentions):
            name = re.sub(r"[\s,]+$", "", mm.group(2)).strip()
            hull = f"{mm.group(3)}-{mm.group(4)}"
            end = mentions[i + 1].start() if i + 1 < len(mentions) else len(text)
            span = navy_clean(text[mm.end():end])
            pl = navy_place(span)
            if pl:
                put(name, hull, pl, 3, blk_grp or group, text)
            elif tag == "p" and blk_place:
                put(name, hull, blk_place, 2, blk_grp or group, text)
            elif tag == "li" and intro:
                put(name, hull, intro, 2, blk_grp or group, text)
            else:
                put(name, hull, section, 1, blk_grp or group, text)
    for key, pl, text in group_moves:
        for s in ships.values():
            if norm_name(s["n"]).replace(" ", "") == key:
                put(s["n"], s["h"], pl, 2, None, text)
    return [ships[h] for h in order if ships[h]["place"]]


def run_navy(args, ctx):
    feed = "navy"
    now = utcnow()
    q = USNI_API + "?search=Fleet%20and%20Marine%20Tracker&per_page=10&_fields=id,date,title,link"
    posts = [p for p in json.loads(http_get(q, timeout=60))
             if "fleet and marine tracker" in html_text(p["title"]["rendered"]).lower()]
    if not posts:
        raise RuntimeError("no Fleet and Marine Tracker post found")
    post = max(posts, key=lambda p: p["date"])
    d = json.loads(http_get(f"{USNI_API}/{post['id']}?_fields=content,date,link", timeout=60))
    ships = parse_usni(d["content"]["rendered"])
    if len(ships) < 5:
        raise RuntimeError(f"only {len(ships)} ships parsed; tracker layout may have changed")

    # spread ships that share a place on a golden-angle spiral (carriers in the middle)
    rank = {"carrier": 0, "amphib": 1, "surface": 2, "sub": 3, "aux": 4}
    for s in ships:
        s["cl"] = NAVY_CLASS.get(s["h"].split("-")[0], "aux")
    ships.sort(key=lambda s: rank[s["cl"]])
    seen, feats = {}, []
    for s in ships:
        la, lo, lab, kind = s["place"]
        k = seen.get(lab, 0)
        seen[lab] = k + 1
        r = (0.035 if kind == "port" else 0.45) * math.sqrt(k)
        a = math.radians(k * 137.508)
        lat = la + r * math.sin(a)
        lon = lo + r * math.cos(a) / max(math.cos(math.radians(la)), 0.2)
        props = {"n": ("USNS " if s["h"].startswith("T-") or s["h"].startswith("ESB") else "USS ") + s["n"],
                 "h": s["h"], "cl": s["cl"], "loc": lab, "port": 1 if kind == "port" else None,
                 "grp": s["grp"], "s": s["s"], "d": post["date"][:10], "u": post["link"]}
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]},
                      "properties": {k2: v for k2, v in props.items() if v is not None}})
    counts = {c: sum(f["properties"]["cl"] == c for f in feats) for c in rank}
    fc = {"type": "FeatureCollection", "updated": iso(now), "as_of": post["date"][:10], "post": post["link"],
          "title": html_text(post["title"]["rendered"]), "features": feats}
    size = write_json(os.path.join(OUT, "navy.geojson"), fc)
    log(feed, f"wrote navy.geojson: {len(feats)} ships from {post['link']} {counts}, {mb(size)}")
    return {"updated": iso(now), "count": len(feats), "as_of": post["date"][:10], "post": post["link"],
            "classes": counts}


# ---------------------------------------------------------------- F. storyboard (surges in GDELT, day/week/month)

GDELT1 = "https://data.gdeltproject.org/events/"
# GDELT 1.0 daily export: 58 columns (verified 2026-10-08). ActionGeo_* starts at 49.
G1_A1, G1_A2, G1_ROOT, G1_NART = 6, 16, 28, 33
G1_GTYPE, G1_GNAME, G1_GCC, G1_GLAT, G1_GLON, G1_GFID, G1_ADDED, G1_URL = 49, 50, 51, 53, 54, 55, 56, 57
G1_NCOLS = 58


def gdelt1_day(day, cache_dir):
    """GDELT 1.0 daily file for YYYYMMDD -> filtered rows (cached). None if not published."""
    path = os.path.join(cache_dir, day + ".json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)["rows"]
        except Exception:
            pass
    body = http_get(f"{GDELT1}{day}.export.CSV.zip", timeout=120, retries=3, not_found_ok=True)
    if body is None:
        return None
    z = zipfile.ZipFile(io.BytesIO(body))
    text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    rows = []
    for line in text.splitlines():
        c = line.split("\t")
        if len(c) != G1_NCOLS or c[G1_ROOT] not in GDELT_ROOTS or c[G1_GTYPE] not in GDELT_GEO_TYPES:
            continue
        try:
            lat, lon, nart = float(c[G1_GLAT]), float(c[G1_GLON]), int(c[G1_NART] or 0)
        except ValueError:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
            continue
        # same layout as the GDELT 2.0 cache rows, plus actor names; dateadded is the file day
        rows.append([c[0], c[1], int(c[G1_ROOT]), 0.0, nart, c[G1_GFID], c[G1_GNAME], c[G1_GCC],
                     lat, lon, day + "000000", c[G1_URL], c[G1_A1], c[G1_A2]])
    write_json(path, {"rows": rows})
    return rows


def region_key(name, cc):
    """'Kharkiv, Kharkivs'ka Oblast', Ukraine' -> 'UP|Kharkivs'ka Oblast', Ukraine' (first-level region)."""
    parts = [p.strip() for p in (name or "").split(",")]
    return (cc or "?") + "|" + (", ".join(parts[1:]) if len(parts) >= 3 else ", ".join(parts))


RE_SLUG_JUNK = re.compile(r"^(?:\d+|[a-f0-9]{8,}|index|article|story|news|amp|html?|php|aspx?)$", re.I)


SLUG_UPPER = {"us", "uk", "un", "eu", "idf", "nato", "fbi", "cia", "isis", "rsf", "m23", "dprk", "uae", "drc", "irgc", "pkk", "sdf", "eu's", "un's", "us-led"}
SLUG_PROPER = """afghan african american arab armenian azerbaijani british burmese chinese colombian congolese european french german haitian
    hamas hezbollah houthi houthis indian iranian iraqi israeli israelis jewish kremlin kurdish lebanese libyan mexican muslim nigerian pakistani
    palestinian palestinians pentagon russian russians saudi somali sudanese syrian taliban turkish ukrainian ukrainians venezuelan yemeni
    christian christians catholic islamic wagner junta's al-shabaab boko haram""".split()
_proper = None


def proper_words():
    """Lower-case words that start proper nouns: country, capital, region and sea names from labels.json plus demonyms."""
    global _proper
    if _proper is None:
        _proper = set(SLUG_PROPER)
        try:
            for l in json.load(open(os.path.join(DATA, "labels.json"), encoding="utf-8")):
                if l["t"] in "cpg":
                    _proper.update(w.lower() for w in re.split(r"[\s,()/.]+", l["n"]) if len(w) > 2)
        except (OSError, ValueError):
            pass
        _proper -= {"the", "and", "new", "united", "south", "north", "east", "west", "central", "republic", "great", "gulf", "sea", "island", "islands"}
    return _proper


def slug_case(title, names=()):
    """Slugs are lower case: restore acronyms and the proper nouns we know (gazetteer, demonyms, the event's place names)."""
    proper = proper_words() | {w.lower() for n in names for w in re.split(r"[\s,()/]+", n or "") if len(w) > 2}
    out = []
    for i, w in enumerate(title.split()):
        lw = w.lower()
        if lw in SLUG_UPPER and lw != "us" or lw == "us" and i < 2:
            w = lw.upper() if not lw.endswith("'s") else lw[:-2].capitalize() + "'s"
        elif lw in proper or lw.removesuffix("s") in proper or lw.removesuffix("'s") in proper or i == 0:
            w = lw[:1].upper() + lw[1:]
        out.append(w)
    return " ".join(out)


def slug_title(url):
    """Best-effort headline from a news URL's path."""
    try:
        path = urllib.request.urlparse(url).path
    except Exception:
        return None
    best = ""
    for seg in path.split("/"):
        seg = re.sub(r"\.(s?html?|php|aspx?|cms)$", "", seg, flags=re.I)
        seg = re.sub(r"^\d+[.\-_]", "", seg)  # "26606982.saudi-backed-..." article-id prefixes
        words = [w for w in re.split(r"[-_+]+", seg)
                 if w and not RE_SLUG_JUNK.match(w) and not re.fullmatch(r"[\d.]+|\w*\d{4,}\w*", w)]
        cand = " ".join(words)
        if len(words) >= 4 and len(cand) > len(best):
            best = cand
    if not best:
        return None
    best = best[:110].rsplit(" ", 1)[0] if len(best) > 110 else best
    return best[0].upper() + best[1:]


# Storyboard headlines about courts, crime, protests or anniversaries are coverage spikes, not fighting: dropped unless
# they also say something was attacked or fought over
STORY_NOISE = re.compile(r"\b(trial|court|charges?|charged|sentenc\w*|verdict|lawsuit|arrest\w*|anniversar\w*|memorial|commemorat\w*|"
                         r"school|shooting|shooter|gunman|assailant|stab\w*|nobel|execution|livestream\w*|election|protest\w*|police|murder\w*|activist|shot dead|domestic)\b", re.I)
STORY_FIGHT = re.compile(r"\b(strikes?|struck|air ?strikes?|drones?|missiles?|rockets?|shell(ing|ed)|bomb\w*|fighting|clash\w*|offensive|"
                         r"troops|militants?|insurgents?|rebels?|raid\w*|ambush\w*|explosions?|blasts?|war)\b", re.I)


# always dropped, even with a fight word: courts ("Judge strikes down..."), crime and policing, celebrities, retrospectives, weapons tests
STORY_OFF = re.compile(r"\b(judge|court|trial|verdict|convict\w*|sentenc\w*|lawsuit|cops?|sheriff\w*|deputy|hunter|bludgeon\w*|"
                       r"actor|actress|singer|star|celebrity|anniversary|years on|years after|test[- ]?fire\w*|tests?|drill|prison|building fire|house fire|plane crash|possible|could|may|"
                       r"war of words|serial killer|crash(es|ed)? into|road accident|charred|who fought|barely reported|found dead|mystery|sought)\b|"
                       r"\bfires? (two |a |\w+ )?(ballistic )?(missiles?|rockets?) (off|into|toward)", re.I)
STORY_HARM = re.compile(r"\b(kill\w*|dead|deaths?|attack\w*|gunmen|violence|massacre\w*|wounded|injured|hostages?|siege|seized?)\b", re.I)

# Severity tiers for storyboard items, lowest to highest. Each item gets `tier` (1-5) from what it reports:
#   1 story     thinly reported, or a jump in conflict reports without a headline to explain it
#   2 news      widely reported but nobody hurt and nothing broken (export curbs, tariffs, chip rules)
#   3 incident  damage or a few deaths: an explosion, a cable cut, a cyberattack, deadly unrest, a storm or quake near people
#   4 conflict  armed fighting: strikes, clashes, offensives
#   5 disaster  mass harm: dozens or more dead, or a PAGER orange or red earthquake
STORY_TIERS = {1: "Story", 2: "News", 3: "Incident", 4: "Conflict", 5: "Disaster"}
STORY_MASS = re.compile(r"\b(dozens|scores|hundreds|thousands)\b[^.]{0,40}\b(killed|dead|die[sd]?|deaths|kill)\b|\b(massacre\w*|mass casualt\w*|famine)\b|"
                        r"\b(more than |at least |over )?([2-9]\d|\d{3,})\s+(people\s+|civilians\s+|soldiers\s+)?(killed|dead|die[sd]?)\b", re.I)
STORY_DAMAGE = re.compile(r"\b(kill\w*|dead|deaths?|dies|injur\w*|wounded|explosions?|blasts?|fires?|collaps\w*|cut|severed|damaged?|outages?|blackouts?|"
                          r"attack\w*|ransomware|hack\w*|breach\w*|outbreaks?|cases|culled?|cull\w*|spill\w*|sinks?|sank|seized?|curfew|martial law|coup|"
                          r"state of emergency|halt\w*|shut\w*|stopp\w*)\b", re.I)

STORY_ONE = re.compile(r"\b(journalist|man|woman|tourist|leader|official|commander|activist|priest|mayor|judge|son|daughter)\b[^.]{0,40}\b(killed|dies|dead|shot|injured)\b|"
                       r"\bkill(s|ed)? (a |an |the )?(\w+ )?(journalist|leader|official|mayor|commander)\b", re.I)


def story_tier(it):
    """Severity tier (1-5, see STORY_TIERS) of one storyboard item."""
    title = it.get("title") or ""
    if it.get("cat") == "quake":
        al = it.get("al") or (re.search(r"\((\w+) alert", title) or [None, None])[1]
        mag = it.get("mag") or float((re.search(r"M(\d+(\.\d+)?)", title) or [0, 0])[1])
        return 5 if al in ("orange", "red") or mag >= 7.5 else 3
    if it.get("cat") in ("storm", "flood", "fire", "volcano"):
        return 5 if STORY_MASS.search(title) or re.search(r"\bsuper typhoon|category [45]\b", title, re.I) else 3
    if STORY_MASS.search(title):
        return 5
    if it.get("k") == "surge":
        if not title:
            return 1
        if STORY_ONE.search(title):
            return 3   # one person killed: a journalist, an official
        return 4 if it.get("e", 0) >= 15 or it.get("z", 0) >= 60 or it.get("a", 0) >= 500 else 3
    if it.get("cat") == "unrest" and re.search(r"\b(coup|martial law|troops|army deployed|toppl\w*|ousted)\b", title, re.I):
        return 4
    if STORY_DAMAGE.search(title):
        return 3
    return 1 if it.get("d", 0) <= 2 and it.get("a", 0) <= 3 else 2


_COUNTRY_RE = None


def headline_countries(title):
    """Country names a headline mentions, matched as word prefixes so 'Russian' counts as Russia and 'Ukrainian' as Ukraine.
    Names come from site/data/labels.json; empty if it isn't there."""
    global _COUNTRY_RE
    if _COUNTRY_RE is None:
        names = {}
        try:
            with open(os.path.join(DATA, "labels.json"), encoding="utf-8") as f:
                for l in json.load(f):
                    if l.get("t") == "c" and len(l["n"]) >= 4:
                        names[l["n"]] = l["n"]
        except (OSError, ValueError):
            pass
        names.update({"US": "United States of America", "U.S.": "United States of America", "America": "United States of America", "UK": "United Kingdom"})
        stems = sorted(((n[:-1] if n.endswith("e") else n, full) for n, full in names.items()), key=lambda kv: -len(kv[0]))
        _COUNTRY_RE = [(re.compile(r"\b" + re.escape(st), re.I if len(st) > 3 else 0), full) for st, full in stems]
    return {full for rx, full in _COUNTRY_RE if rx.search(title)}


_CITIES = None


def headline_city(title, country):
    """A city of 300,000+ in `country` (a GDELT country name) that the headline names: (name, lon, lat), else None."""
    global _CITIES
    if _CITIES is None:
        _CITIES = {}
        try:
            with open(os.path.join(DATA, "labels.json"), encoding="utf-8") as f:
                L = json.load(f)
            iso = {l["n"]: l["k"] for l in L if l.get("t") == "c"}
            for l in L:
                if l.get("t") == "p" and (l.get("pop") or 0) >= 300_000 and l.get("k"):
                    _CITIES.setdefault(l["k"], []).append((l["n"], l["x"], l["y"]))
            _CITIES["#iso"] = iso
        except (OSError, ValueError):
            pass
    iso = _CITIES.get("#iso", {})
    k = iso.get(country) or next((v for n, v in iso.items() if n.startswith(country) or country.startswith(n)), None)
    for name, x, y in sorted(_CITIES.get(k, []), key=lambda c: -len(c[0])):
        if re.search(r"\b" + re.escape(name) + r"\b", title, re.I if len(name) >= 4 else 0):
            return name, x, y
    return None


def headline_foreign_city(title, country):
    """True if the headline names a city of 300,000+ outside `country` and none inside it."""
    if headline_city(title, country):
        return False
    iso = _CITIES.get("#iso", {})
    k = iso.get(country) or next((v for n, v in iso.items() if n.startswith(country) or country.startswith(n)), None)
    return any(re.search(r"\b" + re.escape(name) + r"\b", title, re.I) for kk, cs in _CITIES.items() if kk not in ("#iso", k) for name, _, _ in cs if len(name) >= 4)


def place_name(n):
    """GDELT replaces accented letters with '?' ('Riyadh, Ar Riya?, Saudi Arabia'); drop the parts that lost letters."""
    parts = [p.strip() for p in (n or "").split(",")]
    keep = [p for p in parts if p and "?" not in p]
    return ", ".join(keep) if keep else n


def story_window(rows, buckets, bucket_of, n_items, base_rows=None, base_buckets=0):
    """Rank (region, bucket) surges. rows: cache rows; bucket_of(row) -> bucket index or None.
    Baseline per region = mean over the window's other buckets and any extra baseline buckets."""
    B = len(buckets)
    reg = {}
    tot = [0] * B
    for r, b in ((r, bucket_of(r)) for r in rows):
        if b is None:
            continue
        k = region_key(r[6], r[7])
        R = reg.get(k)
        if R is None:
            R = reg[k] = {"x": [0] * B, "a": [0] * B, "rows": {}, "cc": r[7], "base": 0}
        w = 1 + min(r[4], 50) ** 0.5  # one event, weighted gently by coverage
        R["x"][b] += w
        R["a"][b] += r[4]
        R["rows"].setdefault(b, []).append(r)
        tot[b] += 1
    for r in base_rows or ():
        R = reg.get(region_key(r[6], r[7]))
        if R:
            R["base"] += 1 + min(r[4], 50) ** 0.5
    cand = []
    for k, R in reg.items():
        x = R["x"]
        s = sum(x)
        for b in range(B):
            if x[b] < 6:
                continue
            others = (s - x[b] + R["base"]) / max(1, B - 1 + base_buckets)
            z = (x[b] - others) / math.sqrt(others + 4.0)
            if z > 1.5:
                cand.append((z * math.log1p(x[b]), k, b, others))
    cand.sort(reverse=True)
    items, per_cc, used = [], {}, set()
    for score, k, b, base in cand:
        R = reg[k]
        if per_cc.get(R["cc"], 0) >= 3:
            continue
        # one story per region, or two if the peaks are at least a third of the window apart
        prev = [bb for (kk, bb) in used if kk == k]
        if len(prev) >= 2 or any(abs(bb - b) < max(2, B // 3) for bb in prev):
            continue
        rs = R["rows"][b]
        # place: most-reported city in the region during the peak
        city = {}
        for r in rs:
            c = city.setdefault(r[5] or r[6], [0, r])
            c[0] += 1 + r[4]
        top = max(city.values(), key=lambda c: c[0])[1]
        urls, seen_u = [], set()
        for r in sorted(rs, key=lambda r: -r[4]):
            if r[11] and r[11] not in seen_u:
                seen_u.add(r[11])
                urls.append(r[11])
            if len(urls) == 4:
                break
        title = next((t for t in (slug_title(u) for u in urls) if t), None)
        roots = [sum(1 for r in rs if r[2] == x) for x in (18, 19, 20)]
        fight = title and STORY_FIGHT.search(title)
        # GDELT miscodes plenty of articles as fighting ("medical students visit rural South Dakota"), so a headline has to say
        # itself that something was attacked, fought over or killed; one about courts, crime or anniversaries also needs a fight word
        if title and (STORY_OFF.search(title) or not (fight or (STORY_HARM.search(title) and not STORY_NOISE.search(title)))):
            continue
        # a headline that names countries, none of them the place's own, was geocoded to the wrong place
        # ("Pakistan says airstrikes kill militants" at Hangu, Tianjin, China)
        named = headline_countries(title) if title else set()
        home = place_name(top[6]).split(",")[-1].strip()
        if named and not any(c == home or c.startswith(home) or home.startswith(c) for c in named):
            continue
        used.add((k, b))
        per_cc[R["cc"]] = per_cc.get(R["cc"], 0) + 1
        actors = {}
        for r in rs:
            for a in r[12:14]:
                if a:
                    actors[a] = actors.get(a, 0) + 1
        # GDELT often places a story at the wrong town in the right country (a Voronezh strike at Borisoglebsk, Murmansk); when
        # the headline names a city there, more than 100 km away, the story moves to it
        pname, plat, plon = place_name(top[6]), top[8], top[9]
        if title and headline_foreign_city(title, pname.split(",")[-1].strip()):
            continue  # "Russian airstrikes choke key bridges in Kyiv" placed at Volgograd
        hc = title and headline_city(title, pname.split(",")[-1].strip())
        if hc and math.hypot((hc[1] - plon) * math.cos(math.radians(plat)), hc[2] - plat) * 111 > 100:
            pname, plon, plat = f"{hc[0]}, {pname.split(',')[-1].strip()}", hc[1], hc[2]
        it = {"k": "surge", "cat": "conflict", "label": "Conflict", "t": buckets[b][0], "t1": buckets[b][1], "n": pname, "cc": R["cc"],
              "lat": round(plat, 4), "lon": round(plon, 4), "e": len(rs), "a": R["a"][b],
              "r": roots, "z": round(score, 1), "base": round(base, 1),
              "x": [round(v) for v in R["x"]], "u": urls}
        if title:
            it["title"] = slug_case(title, {r[6] for r in rs})
        if actors:
            it["act"] = [a for a, _ in sorted(actors.items(), key=lambda kv: -kv[1])[:3]]
        items.append(it)
        if len(items) >= n_items:
            break
    items.sort(key=lambda it: it["t"])
    return {"buckets": [b[0] for b in buckets], "total": tot, "items": items}


def run_story(args, ctx):
    feed = "story"
    now = utcnow()
    # --- GDELT 2.0 slots (shared cache with the events feed): day and week windows
    cache2 = os.path.join(CACHE, "gdelt")
    os.makedirs(cache2, exist_ok=True)
    last = http_get(GDELT + "lastupdate.txt", timeout=30).decode("utf-8", "replace")
    m = re.search(r"/(\d{14})\.export\.CSV\.zip", last)
    if not m:
        raise RuntimeError("could not parse lastupdate.txt")
    latest = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    slots, t = [], latest
    while t > latest - timedelta(days=14):
        slots.append(t.strftime("%Y%m%d%H%M%S"))
        t -= timedelta(minutes=15)
    rows2, failed = [], 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for fu in as_completed([ex.submit(gdelt_slot, s, cache2, now) for s in slots]):
            try:
                rows, _hit = fu.result()
                rows2.extend(rows or [])
            except Exception:
                failed += 1
    if failed > 0.2 * len(slots):
        raise RuntimeError(f"{failed}/{len(slots)} GDELT slots failed")
    seen, uniq = set(), []
    for r in rows2:
        if r[0] not in seen:
            seen.add(r[0])
            uniq.append(r)
    rows2 = [r + ["", ""] for r in uniq]  # no actor columns in the 2.0 cache
    log(feed, f"GDELT 2.0: {len(rows2)} events over 14 days ({failed} slots failed)")

    def dt14(s):
        return datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)

    out = {"updated": iso(now), "windows": {}}
    # day: 24 hourly buckets ending at the latest slot; baseline = the previous 6 days
    end = latest.replace(minute=0, second=0) + timedelta(hours=1)
    hb = [(iso(end - timedelta(hours=24 - i)), iso(end - timedelta(hours=23 - i))) for i in range(24)]
    h0 = end - timedelta(hours=24)

    def hour_of(r):
        if r[1] < (h0 - timedelta(days=2)).strftime("%Y%m%d"):
            return None
        d = dt14(r[10])
        return int((d - h0).total_seconds() // 3600) if h0 <= d < end else None
    base_day = [r for r in rows2 if h0 - timedelta(days=6) <= dt14(r[10]) < h0]
    out["windows"]["day"] = story_window(rows2, hb, hour_of, 20, base_day, 6 * 24)
    # week: 7 daily buckets; baseline = the 7 days before
    d_end = (latest + timedelta(days=1)).replace(hour=0, minute=0, second=0)
    db = [(iso(d_end - timedelta(days=7 - i)), iso(d_end - timedelta(days=6 - i))) for i in range(7)]
    d0 = d_end - timedelta(days=7)

    def day_of(r):
        if r[1] < (d0 - timedelta(days=2)).strftime("%Y%m%d"):
            return None
        d = dt14(r[10])
        return (d - d0).days if d0 <= d < d_end else None
    base_week = [r for r in rows2 if d0 - timedelta(days=7) <= dt14(r[10]) < d0]
    out["windows"]["week"] = story_window(rows2, db, day_of, 24, base_week, 7)

    # --- GDELT 1.0 daily files: month window (30 days, 3-day buckets)
    cache1 = os.path.join(CACHE, "gdelt1")
    os.makedirs(cache1, exist_ok=True)
    days = [(now - timedelta(days=i)).strftime("%Y%m%d") for i in range(1, 33)]
    for fn in os.listdir(cache1):
        if fn.endswith(".json") and fn[:8] < days[-1]:
            os.remove(os.path.join(cache1, fn))
    rows1, have = [], []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(gdelt1_day, d, cache1): d for d in days}
        for fu in as_completed(futs):
            try:
                r = fu.result()
            except Exception as e:
                log(feed, f"warning: GDELT 1.0 {futs[fu]}: {e}")
                continue
            if r is not None:
                rows1.extend(r)
                have.append(futs[fu])
    if len(have) < 20:
        raise RuntimeError(f"only {len(have)} GDELT 1.0 daily files available")
    last_day = datetime.strptime(max(have), "%Y%m%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    mb0 = last_day - timedelta(days=30)
    mbk = [(iso(mb0 + timedelta(days=3 * i)), iso(mb0 + timedelta(days=3 * i + 3))) for i in range(10)]

    def tri_of(r):
        d = datetime.strptime(r[10][:8], "%Y%m%d").replace(tzinfo=timezone.utc)
        # drop events coded from articles about something older than a few days
        if r[1] < (d - timedelta(days=3)).strftime("%Y%m%d"):
            return None
        i = (d - mb0).days // 3
        return i if 0 <= i < 10 else None
    out["windows"]["month"] = story_window(rows1, mbk, tri_of, 30)
    log(feed, f"GDELT 1.0: {len(rows1)} events from {len(have)} daily files")
    # beyond fighting: unrest, trade curbs, industrial accidents, blackouts, shipping, disease, harvests, cables, cloud, cyber
    # and chips (GDELT article searches), plus earthquakes, storms and floods (USGS, NASA EONET); see news_topics.py
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import news_topics as nt
        with open(os.path.join(DATA, "labels.json"), encoding="utf-8") as f:
            labels = json.load(f)
        with open(os.path.join(DATA, "country_totals.json"), encoding="utf-8") as f:
            totals = json.load(f)
        F = nt.Fetch(os.path.join(CACHE, "news"), lambda m: log(feed, m))
        G = nt.Gazetteer(labels, totals)
        tops = nt.story_topics(F, G, log=lambda m: log(feed, m)) + nt.story_disasters(F, now, log=lambda m: log(feed, m), G=G)
        for w, days in (("day", 1), ("week", 7), ("month", 31)):
            since = iso(now - timedelta(days=days))
            win = out["windows"][w]
            win["items"] = sorted(win["items"] + [it for it in tops if it["t1"] >= since], key=lambda it: it["t"])
        log(feed, f"topics: {len(tops)} items")
    except Exception as e:  # noqa: BLE001 - the conflict storyboard stands on its own
        log(feed, f"warning: topic news skipped: {type(e).__name__}: {e}")
    for win in out["windows"].values():
        for it in win["items"]:
            it["tier"] = story_tier(it)
    out["tiers"] = STORY_TIERS
    size = write_json(os.path.join(OUT, "story.json"), out)
    counts = {k: len(v["items"]) for k, v in out["windows"].items()}
    log(feed, f"wrote story.json: {counts}, {mb(size)}")
    return {"updated": iso(now), "count": sum(counts.values()), "windows": counts}


# ---------------------------------------------------------------- main

RUNNERS = {"events": run_events, "fires": run_fires, "ships": run_ships, "aircraft": run_aircraft,
           "jets": run_jets, "airliners": run_airliners, "navy": run_navy, "story": run_story}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default=",".join(FEEDS),
                    help="comma-separated subset of: " + ",".join(FEEDS))
    ap.add_argument("--days", type=int, default=14, help="GDELT window in days (default 14)")
    ap.add_argument("--ais-seconds", type=int, default=240, help="AIS listen time (default 240)")
    ap.add_argument("--archive", metavar="DIR", help="also add this run to the rolling archive in DIR (see archive.py)")
    ap.add_argument("--archive-budget-mb", type=float, default=1000, help="archive size cap in MB (default 1000)")
    ap.add_argument("--archive-long-days", type=int, default=1826,
                    help="days of events, story and navy history to keep (default 1826, five years)")
    args = ap.parse_args()
    want = [s.strip() for s in args.only.split(",") if s.strip()]
    bad = [s for s in want if s not in RUNNERS]
    if bad:
        ap.error("unknown feed(s): " + ", ".join(bad))
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(CACHE, exist_ok=True)

    mpath = os.path.join(OUT, "manifest.json")
    try:
        with open(mpath, encoding="utf-8") as f:
            old = json.load(f).get("feeds", {})
    except Exception:
        old = {}
    feeds = {k: v for k, v in old.items()}  # feeds not run this time keep their entries

    ctx, ok, failed, t0 = {}, [], [], time.time()
    for name in FEEDS:  # fixed order: events before fires
        if name not in want:
            continue
        t1 = time.time()
        try:
            info = RUNNERS[name](args, ctx)
            feeds[name] = {**info, **SOURCES[name]}
            ok.append(name)
            log(name, f"done in {time.time() - t1:.0f} s")
        except Skip as e:
            msg = str(e)
            if name in old and "updated" in old[name]:
                feeds[name] = {**old[name], "stale": True, "error": msg}
            else:
                feeds[name] = {"status": ("disabled: no AISSTREAM_API_KEY" if "AISSTREAM" in msg
                                          else "disabled: " + msg), **SOURCES[name]}
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"[:300]
            log(name, "FAILED: " + msg)
            failed.append(name)
            base = old.get(name) or {"status": "error", **SOURCES[name]}
            feeds[name] = {**base, "stale": True, "error": msg}
    write_json(mpath, {"generated": iso(utcnow()), "feeds": feeds})
    if args.archive:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import archive
            archive.update(args.archive, OUT, os.path.join(CACHE, "gdelt"), ok, utcnow(),
                           args.archive_budget_mb, args.archive_long_days)
        except Exception as e:
            log("archive", f"FAILED: {type(e).__name__}: {e}")
    log("main", f"ok: {ok or '-'}; failed: {failed or '-'}; total {time.time() - t0:.0f} s")
    return 1 if failed and not ok else 0


if __name__ == "__main__":
    sys.exit(main())
