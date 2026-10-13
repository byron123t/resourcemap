"""Rolling archive of the live feeds, kept under a fixed byte budget.

Called by fetch_live.py after the feeds have run (--archive DIR). Two tiers:

  long   events (daily per-location totals), story (one snapshot per day) and
         navy (one per USNI post), kept for --archive-long-days (default 5 years)
  short  fires (daily, by detection date) and hourly snapshots of ships,
         aircraft, jets and airliners; the oldest days are deleted until the
         whole archive fits in --archive-budget-mb (default 1000)

Every file is gzipped compact JSON, so a page can read it with
DecompressionStream("gzip"). index.json lists what is there:

  DIR/index.json
  DIR/long/events/YYYY/YYYYMMDD.json.gz
  DIR/long/story/YYYY/YYYYMMDD.json.gz
  DIR/long/navy/YYYY-MM-DD.json.gz
  DIR/short/fires/YYYYMMDD.json.gz
  DIR/short/{ships,aircraft,jets,airliners}/YYYYMMDD/HH.json.gz

Inputs are the feed outputs in site/data/live/ plus the GDELT 2.0 slot cache,
so jets/airliners arrive here with LADD/PIA-flagged aircraft already removed.
"""
import gzip
import json
import os
import sys
from datetime import datetime, timedelta, timezone

MOVERS = {"ships": "ships.geojson", "aircraft": "aircraft.geojson", "jets": "jets.geojson",
          "airliners": "airliners.geojson"}
EVENT_COLS = ["lon", "lat", "n", "cc", "e", "a", "r18", "r19", "r20", "g", "u"]
FIRE_COLS = ["lon", "lat", "frp", "ti4", "min", "night", "nd", "ind", "cf"]  # lon/lat x1000, frp x10, min = minute of day


def _log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] archive  {msg}", file=sys.stderr, flush=True)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _write_gz(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, path)
    return os.path.getsize(path)


# ---------------------------------------------------------------- long tier

def events_days(arch, slot_cache, now):
    """Write one file per complete UTC day found in the GDELT 2.0 slot cache. Returns days written."""
    if not os.path.isdir(slot_cache):
        return []
    by_day = {}
    for fn in os.listdir(slot_cache):
        if fn.endswith(".json"):
            by_day.setdefault(fn[:8], []).append(fn)
    today = now.strftime("%Y%m%d")
    written = []
    for day, files in sorted(by_day.items()):
        out = os.path.join(arch, "long", "events", day[:4], day + ".json.gz")
        # 96 slots a day; a few may be missing upstream (cached as rows: null)
        if day >= today or len(files) < 90 or os.path.exists(out):
            continue
        min_sql = (datetime.strptime(day, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        seen, locs = set(), {}
        for fn in files:
            try:
                rows = _read(os.path.join(slot_cache, fn)).get("rows") or []
            except Exception:
                continue
            for _id, sql, root, gold, nart, fid, name, cc, lat, lon, added, url in rows:
                if _id in seen or sql < min_sql or added[:8] != day:
                    continue
                seen.add(_id)
                key = fid or f"{lat:.2f},{lon:.2f}"
                L = locs.get(key)
                if L is None:
                    L = locs[key] = [round(lon, 4), round(lat, 4), name, cc, 0, 0, 0, 0, 0, 0.0, {}]
                L[4] += 1
                L[5] += nart
                L[6 + root - 18] += 1
                L[9] += gold
                if url:
                    L[10][url] = L[10].get(url, 0) + nart
        rows = []
        for L in locs.values():
            urls = sorted(L[10], key=lambda u: -L[10][u])[:2]
            rows.append(L[:9] + [round(L[9] / L[4], 1), urls])
        rows.sort(key=lambda r: -r[5])
        _write_gz(out, {"day": f"{day[:4]}-{day[4:6]}-{day[6:]}", "cols": EVENT_COLS, "rows": rows})
        written.append(day)
    return written


def story_day(arch, live, now):
    src = os.path.join(live, "story.json")
    if not os.path.exists(src):
        return None
    day = now.strftime("%Y%m%d")
    _write_gz(os.path.join(arch, "long", "story", day[:4], day + ".json.gz"), _read(src))  # last run of the day wins
    return day


def navy_post(arch, live):
    src = os.path.join(live, "navy.geojson")
    if not os.path.exists(src):
        return None
    fc = _read(src)
    as_of = fc.get("as_of")
    out = os.path.join(arch, "long", "navy", f"{as_of}.json.gz")
    if not as_of or os.path.exists(out):
        return None
    _write_gz(out, fc)
    return as_of


# ---------------------------------------------------------------- short tier

def fires_days(arch, live):
    """Merge this run's detections into per-day files keyed by acquisition date."""
    src = os.path.join(live, "fires.json")
    if not os.path.exists(src):
        return []
    doc = _read(src)
    ci = {c: i for i, c in enumerate(doc["cols"])}
    new = {}
    for r in doc["rows"]:
        t = r[ci["t"]]
        if len(t) < 16:
            continue
        day = t[:10].replace("-", "")
        new.setdefault(day, []).append([round(r[ci["lon"]] * 1000), round(r[ci["lat"]] * 1000), round(r[ci["frp"]] * 10),
                                        r[ci["ti4"]], int(t[11:13]) * 60 + int(t[14:16]), 1 if r[ci["dn"]] == "N" else 0,
                                        r[ci["nd"]], r[ci["ind"]], r[ci["cf"]]])
    for day, rows in new.items():
        out = os.path.join(arch, "short", "fires", day + ".json.gz")
        have = {}
        if os.path.exists(out):
            try:
                have = {(r[0], r[1], r[4]): r for r in _read_gz(out)["rows"]}
            except Exception:
                pass
        for r in rows:
            have.setdefault((r[0], r[1], r[4]), r)
        _write_gz(out, {"day": f"{day[:4]}-{day[4:6]}-{day[6:]}", "cols": FIRE_COLS, "rows": list(have.values())})
    return sorted(new)


def mover_hours(arch, live, feeds):
    """One columnar snapshot per feed per UTC hour (the first run in that hour)."""
    done = []
    for feed in feeds:
        src = os.path.join(live, MOVERS[feed])
        if not os.path.exists(src):
            continue
        fc = _read(src)
        t = datetime.strptime(fc["updated"], "%Y-%m-%dT%H:%M:%SZ")
        out = os.path.join(arch, "short", feed, t.strftime("%Y%m%d"), t.strftime("%H") + ".json.gz")
        if os.path.exists(out):
            continue
        keys = []
        for f in fc["features"]:
            for k in f["properties"]:
                if k not in keys and k != "desc":  # desc follows from the type code
                    keys.append(k)
        rows = [f["geometry"]["coordinates"][:2] + [f["properties"].get(k) for k in keys] for f in fc["features"]]
        _write_gz(out, {"updated": fc["updated"], "cols": ["lon", "lat"] + keys, "rows": rows})
        done.append(feed)
    return done


# ---------------------------------------------------------------- pruning and index

def _day_of(rel):
    """Archive-relative path -> YYYYMMDD it belongs to."""
    parts = rel.split("/")
    name = parts[-1].split(".")[0].replace("-", "")
    return parts[-2] if parts[1] in MOVERS else name


def _scan(arch):
    files = []
    for tier in ("long", "short"):
        for dp, _dn, fns in os.walk(os.path.join(arch, tier)):
            for fn in fns:
                if fn.endswith(".json.gz"):
                    p = os.path.join(dp, fn)
                    rel = os.path.relpath(p, arch).replace(os.sep, "/")
                    files.append((rel, _day_of(rel), os.path.getsize(p)))
    return files


def _remove(arch, rel):
    os.remove(os.path.join(arch, rel))
    d = os.path.dirname(os.path.join(arch, rel))
    while os.path.abspath(d) != os.path.abspath(arch) and not os.listdir(d):
        os.rmdir(d)
        d = os.path.dirname(d)


def prune(arch, now, budget, long_days):
    files = _scan(arch)
    cut = (now - timedelta(days=long_days)).strftime("%Y%m%d")
    removed = 0
    for rel, day, _size in [f for f in files if f[0].startswith("long/") and f[1] < cut]:
        _remove(arch, rel)
        removed += 1
    files = _scan(arch)
    long_bytes = sum(s for rel, _d, s in files if rel.startswith("long/"))
    short = sorted(f for f in files if f[0].startswith("short/"))
    by_day = {}
    for rel, day, size in short:
        by_day.setdefault(day, []).append((rel, size))
    short_bytes = sum(s for _r, _d, s in short)
    days = sorted(by_day)
    # always keep the newest day, even over budget
    while days[1:] and long_bytes + short_bytes > budget:
        for rel, size in by_day.pop(days.pop(0)):
            _remove(arch, rel)
            short_bytes -= size
            removed += 1
    return removed


def write_index(arch, now, budget, long_days):
    files = _scan(arch)
    feeds, sizes = {}, {}
    for rel, day, size in files:
        parts = rel.split("/")
        feed = parts[1]
        sizes[feed] = sizes.get(feed, 0) + size
        f = feeds.setdefault(feed, {})
        if feed in MOVERS:
            f.setdefault(day, []).append(int(parts[-1][:2]))
        else:
            f[day] = rel
    for f in feeds.values():
        for v in f.values():
            if isinstance(v, list):
                v.sort()
    short_days = sorted({d for rel, d, _s in files if rel.startswith("short/")})
    total = sum(sizes.values())
    idx = {"updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "budget_bytes": budget, "bytes": total,
           "long_days": long_days, "short_window": [short_days[0], short_days[-1]] if short_days else None,
           "bytes_by_feed": sizes,
           "feeds": {k: dict(sorted(v.items())) for k, v in sorted(feeds.items())}}
    tmp = os.path.join(arch, "index.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, separators=(",", ":"))
    os.replace(tmp, os.path.join(arch, "index.json"))
    return idx


def update(arch, live, slot_cache, ran, now, budget_mb=1000, long_days=1826):
    """Add this run's outputs to the archive, prune to budget, rewrite index.json."""
    os.makedirs(arch, exist_ok=True)
    budget = int(budget_mb * 1e6)
    notes = []
    if "events" in ran:
        d = events_days(arch, slot_cache, now)
        if d:
            notes.append(f"events {len(d)} day(s) {d[0]}..{d[-1]}")
    if "story" in ran and story_day(arch, live, now):
        notes.append("story")
    if "navy" in ran:
        p = navy_post(arch, live)
        if p:
            notes.append(f"navy {p}")
    if "fires" in ran:
        d = fires_days(arch, live)
        if d:
            notes.append(f"fires {','.join(d)}")
    m = mover_hours(arch, live, [f for f in MOVERS if f in ran])
    if m:
        notes.append("hourly " + ",".join(m))
    removed = prune(arch, now, budget, long_days)
    idx = write_index(arch, now, budget, long_days)
    _log(f"added: {'; '.join(notes) or 'nothing new'}; removed {removed} file(s); "
         f"{idx['bytes'] / 1e6:.1f} of {budget / 1e6:.0f} MB; short window {idx['short_window']}")
    return idx
