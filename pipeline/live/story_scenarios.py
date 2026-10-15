#!/usr/bin/env python3
"""Turn the storyboard's news surges into ready-made Scenarios: this week's, this month's and this year's.

Run weekly by .github/workflows/scenarios-weekly.yml after the story feed; devs can run it locally:

    python3 -I pipeline/live/fetch_live.py --only story
    python3 -I pipeline/live/story_scenarios.py [--days 7] [--story PATH] [--out PATH]
    python3 -I pipeline/live/story_scenarios.py --check      # validate the file only (after hand review)
    python3 -I pipeline/live/story_scenarios.py --backfill-year   # one-off: seed the year list from GDELT 1.0

Reads site/data/live/story.json and keeps the surges that look like strikes or fighting
(headline keywords, or CAMEO 19/20-heavy when there is no headline) and sit near something
the Scenarios panel can break: refineries, oil ports, LNG terminals and field clusters from
energy_graph.json, power plants of 300 MW or more, cities of a million or more (their power,
roads and rail), cable landings, and the eight sea chokepoints. Each one becomes a zone
shock sized to cover what it matched, plus cable and chokepoint shocks, at three levels read
off the news (see levels()):

    de   de-escalation: only the nearest site is hit, lightly
    cur  as reported: the zone covers everything matched, damage from the report mix
    esc  escalation: the zone grows to take in the next ring of sites, damage rises, and a
         nearby strait closes fully if the story is about shipping

Each window is filled round-robin across world regions (region()), strongest surge first in
each, so one busy region can't take every slot.

Windows: week = the story week window plus month-window surges that ended in the last
--days; month = both windows; year = this run's month items merged with last run's year
items (read back from --out) younger than 365 days. The year list is built up week by
week, so it only reaches a full year after a year of runs.

Writes site/data/scenarios_weekly.json:
    {"updated": ISO, "source": ..., "items": [week items],
     "windows": {"week": {"items"}, "month": {"items"}, "year": {"items"}}}
    item: {"label", "why", "url", "shocks" (= levels.cur), "levels": {"de", "cur", "esc"},
           "lv": {"de", "cur", "esc"} (one line each), "trend", "likely", "at", "months",
           "place", "t", "z", "matched"}
"items" repeats the week list for older pages. The page shows at most 12 items a window and
ignores the reviewer keys (place, t, z, matched). Matching is keyword- and distance-based, so
expect some noise; the weekly PR's review drops it before the file is merged.

Standard library only.
"""
import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.environ.get("ATLAS_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
DATA = os.path.join(ROOT, "site", "data")
MAX_ITEMS = {"week": 12, "month": 20, "year": 30}  # per window; the page shows the same numbers
ENERGY_KM, POWER_KM, CABLE_KM, CHOKE_KM, CITY_KM = 75, 75, 50, 150, 30
POWER_MIN_MW = 300
CITY_MIN_POP = 1_000_000  # a big city alone qualifies: the zone still cuts its power, roads and rail
MERGE_KM = 60  # surges closer than this to a stronger one are the same story

CHOKES = {"malacca": ("Strait of Malacca", 101.0, 2.6), "hormuz": ("Strait of Hormuz", 56.4, 26.5),
          "suez": ("Suez Canal", 32.4, 30.3), "bab": ("Bab el-Mandeb", 43.4, 12.6),
          "danish": ("Danish Straits", 10.9, 55.6), "turkish": ("Turkish Straits", 29.1, 41.1),
          "panama": ("Panama Canal", -79.7, 9.1), "cape": ("Cape of Good Hope", 18.5, -34.4)}
KIND = {"R": "refinery", "X": "LNG export terminal", "I": "LNG import terminal", "P": "oil port", "F": "oil/gas field"}
KIND_ORDER = "RXIPF"

# (pattern, label verb). First match names the scenario; any match makes a headline count as a strike.
STRIKE = [(r"\bdrones?\b|\buavs?\b", "Drone strikes"), (r"\bmissiles?\b|\bprojectiles?\b|\brockets?\b", "Missile strikes"),
          (r"\bair ?strikes?\b|\bbomb(ing|ed|s)?\b", "Air strikes"),
          (r"\bshell(ing|ed)\b|\bartillery\b|\bstrikes?\b|\bstruck\b|\bhit(s)?\b", "Strikes"),
          (r"\bexplosions?\b|\bblasts?\b|\bsabotage\b", "Explosions"),
          (r"\bfighting\b|\bclash(es)?\b|\boffensive\b|\bassault\b|\bbattle\b|\bshootout\b", "Fighting"),
          (r"\battacks?\b|\battacked\b|\braids?\b|\bambush\b", "Attacks"),
          (r"\binvasion\b|\bmilitants?\b|\binsurgents?\b", "Fighting")]  # not "war": too many retrospectives
# headlines about crime, courts or politics; dropped unless they also match STRIKE
NOISE = r"\bschool\b|\bshooting\b|\bshooter\b|\bgunman\b|\bexecution\b|\btrial\b|\bcourt\b|\blawsuit\b|\bcharges?\b|\barrest(ed|s)?\b|\bprotests?\b|\bmurder\b|\bhostage\b|\bjournalist\b|\belection\b|\bsettlers?\b|\bfarmers?\b|\bsynagogue\b|\bchurch\b|\bmosque\b|\btemple\b"
# weapons news that isn't a strike on anything: tests, launches into the sea, drills, interceptions
NOT_STRIKE = r"\btests?\b|\btested\b|\btest-fires?\b|\bfires? (a )?\w* ?(missile|projectile|rocket)s? (toward|into)\b|\bdrills?\b|\bshoots? down\b|\bdowns?\b|\bintercept(s|ed|ion)?\b|\bairspace\b"
# a conflict surge only becomes a scenario if its headline is about infrastructure or names the place itself
INFRA = r"refiner|\boil\b|\bgas\b|fuel|\bports?\b|harbou?r|power|\bgrid\b|electric|\bplants?\b|substation|pipeline|terminal|depot|\bdams?\b|bridge|rail|airport|factor(y|ies)|\bcables?\b|tanker|\bships?\b|energy|infrastructure|blackout"
SEA = r"\bships?\b|\btankers?\b|\bvessels?\b|\bstrait\b|\bmaritime\b|\bhouthis?\b|\bnavy\b|\bnaval\b|\bshipping\b|\bport\b"


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] scenarios {msg}", file=sys.stderr, flush=True)


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 12742.0 * math.asin(min(1.0, math.sqrt(a)))


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


class Grid:
    """1° spatial hash of (lon, lat, payload); `within()` yields (km, payload) inside a radius."""

    def __init__(self, items):
        self.g = {}
        for lon, lat, p in items:
            self.g.setdefault((math.floor(lat), math.floor(lon)), []).append((lon, lat, p))

    def within(self, lon, lat, km):
        dy = int(math.ceil(km / 111.0))
        dx = int(math.ceil(km / (111.32 * max(math.cos(math.radians(min(abs(lat) + dy, 89.0))), 0.05))))
        out = []
        for iy in range(math.floor(lat) - dy, math.floor(lat) + dy + 1):
            for ix in range(math.floor(lon) - dx, math.floor(lon) + dx + 1):
                for plon, plat, p in self.g.get((iy, (ix + 180) % 360 - 180), ()):
                    d = haversine_km(lat, lon, plat, plon)
                    if d <= km:
                        out.append((d, p))
        return sorted(out, key=lambda x: x[0])


def facilities():
    """Spatial indexes of what a zone or a cable/choke shock can hit."""
    G = load("energy_graph.json")
    n, names = G["nodes"], G["names"]
    energy = []
    for i, t in enumerate(n["t"]):
        if t in KIND:
            energy.append((n["x"][i] / 100, n["y"][i] / 100, {"k": t, "n": names.get(str(i), KIND[t])}))
    power = []
    for f in load("power.geojson")["features"]:
        p = f["properties"]
        if (p.get("mw") or 0) >= POWER_MIN_MW and p.get("s", "operating") == "operating" and f["geometry"]:
            lon, lat = f["geometry"]["coordinates"][:2]
            power.append((lon, lat, {"k": "power", "n": p["n"], "mw": p["mw"], "f": p.get("f")}))
    cables = []
    for f in load("landings.geojson")["features"]:
        p = f["properties"]
        if p.get("id") and f["geometry"]:
            lon, lat = f["geometry"]["coordinates"][:2]
            cables.append((lon, lat, {"k": "cable", "n": p["n"], "id": p["id"]}))
    cities = [(c["x"], c["y"], {"k": "city", "n": c["n"], "pop": c["pop"]}) for c in load("labels.json")
              if c.get("t") == "p" and (c.get("pop") or 0) >= CITY_MIN_POP]
    log(f"{len(energy)} energy sites, {len(power)} power plants ≥{POWER_MIN_MW} MW, {len(cables)} cable landings, {len(cities)} cities ≥{CITY_MIN_POP:,}")
    return Grid(energy), Grid(power), Grid(cables), Grid(cities)


def classify(item):
    """(label verb, sea story?) for a surge, or None if it doesn't look like a strike or fighting."""
    title = (item.get("title") or "").lower()
    r = item.get("r") or [0, 0, 0]
    heavy = (r[1] + r[2]) / max(1, sum(r))
    if not title:
        return None  # no headline: nothing says what was hit
    verb = next((v for pat, v in STRIKE if re.search(pat, title)), None)
    if not verb:
        return None
    if not re.search(INFRA, title) and place(item).lower() not in title:
        return None  # e.g. a Kyiv headline geocoded to Sochi, or a strike story with no target named
    if re.search(NOT_STRIKE, title):
        return None  # "North Korea fires projectile toward Eastern sea", "NATO jets down drone"
    if re.search(NOISE, title) and verb in ("Attacks", "Fighting"):
        return None  # "shooting at school", "arrested amid protests over October attacks"
    return verb, bool(re.search(SEA, title))


def place(item):
    return (item.get("n") or "").split(",")[0].strip() or f"{item['lat']:.1f}°, {item['lon']:.1f}°"


def describe(sites):
    """'Kremenchuk refinery, Kremenchuk TPP and 2 more' from matched sites, most important first."""
    def rank(s):
        d, p = s
        return (KIND_ORDER.index(p["k"]) if p["k"] in KIND_ORDER else {"power": 5, "city": 6}.get(p["k"], 7), d)
    seen, parts = set(), []
    for d, p in sorted(sites, key=rank):
        nm = p["n"]
        if p["k"] == "power":
            nm = f"{nm} ({p['mw'] / 1000:.1f} GW{', ' + p['f'] if p.get('f') else ''})"
        elif p["k"] == "city":
            nm = f"{nm} (≈{p['pop'] / 1e6:.1f} M people)"
        elif p["k"] in KIND and KIND[p["k"]].split()[0].lower() not in nm.lower():
            nm = f"{nm} ({KIND[p['k']]})"
        if nm not in seen:
            seen.add(nm)
            parts.append(nm)
    if len(parts) > 3:
        return ", ".join(parts[:3]) + f" and {len(parts) - 3} more"
    return " and ".join([", ".join(parts[:-1]), parts[-1]]) if len(parts) > 1 else parts[0]


def ts(v):
    return datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def trend(it):
    """'rising', 'easing' or 'steady': reports in the window's last buckets against the surge bucket."""
    x = [v for v in (it.get("x") or []) if isinstance(v, (int, float))]
    if len(x) < 3:
        return "steady"
    peak, i = max(x), x.index(max(x))
    tail = sum(x[-2:]) / 2
    if i >= len(x) - 2 or tail >= 0.8 * peak:
        return "rising"
    return "easing" if tail < 0.25 * peak else "steady"


def round5(v):
    return int(5 * math.ceil(v / 5))


def levels(lon, lat, m, sites, km, sea, lands, chokes, grids):
    """Shocks and one-line descriptions for de-escalation, as reported and escalation."""
    energy, power = grids[0], grids[1]
    lv, txt = {}, {}
    zone = lambda r, mm: ["zone", [round(lon, 2), round(lat, 2), r], mm]
    pct = lambda v: f"{round(v * 100)}%"
    near = sorted(sites, key=lambda s: s[0])
    if sites:
        d0, p0 = near[0]
        km_de, m_de = min(km, max(10, round5(d0 + 5))), max(0.1, round(m * 0.4 * 20) / 20)
        lv["de"] = [zone(km_de, m_de)]
        txt["de"] = f"Calms down: only the nearest site, {p0['n']}, is hit, {pct(m_de)} damage within {km_de} km."
        lv["cur"] = [zone(km, m)]
        txt["cur"] = f"As reported: {len(sites)} site{'s' if len(sites) > 1 else ''} within {km} km, {pct(m)} damage."
        km_esc, m_esc = min(150, round5(km * 1.8)), min(1.0, round((m + 0.25) * 20) / 20)
        more = energy.within(lon, lat, km_esc) + power.within(lon, lat, km_esc)
        have = {p["n"] for _, p in sites}
        extra = [p["n"] for _, p in more if p["n"] not in have]
        lv["esc"] = [zone(km_esc, m_esc)]
        txt["esc"] = f"Spreads to {km_esc} km, {pct(m_esc)} damage" + (
            f", reaching {len(extra)} more site{'s' if len(extra) > 1 else ''} ({', '.join(extra[:2])}{' …' if len(extra) > 2 else ''})." if extra else ".")
    else:
        lv["de"], lv["cur"], lv["esc"] = [], [], []
        txt["de"] = txt["cur"] = txt["esc"] = ""
    if sea:
        cab = [["cable", p["id"], 1] for _, p in lands[:3]]
        if chokes:
            name = CHOKES[chokes[0]][0]
            lv["de"] += [["choke", chokes[0], 0.1]]
            lv["cur"] += cab + [["choke", chokes[0], 0.5]]
            lv["esc"] += cab + [["choke", chokes[0], 1]]
            txt["de"] = (txt["de"] + " " if txt["de"] else "") + f"Ships keep using {name}, a few divert."
            txt["cur"] = (txt["cur"] + " " if txt["cur"] else "") + f"Half the traffic avoids {name}" + (", cables at risk." if cab else ".")
            txt["esc"] = (txt["esc"] + " " if txt["esc"] else "") + f"{name} closes" + (" and cables are cut." if cab else ".")
        elif cab:
            lv["cur"] += cab
            lv["esc"] += cab
            txt["cur"] = (txt["cur"] + " " if txt["cur"] else "") + "Nearby cables cut."
            txt["esc"] = (txt["esc"] + " " if txt["esc"] else "") + "Nearby cables cut."
    if not lv["de"]:  # a cable-only story: de-escalation cuts nothing, so keep the smallest real shock
        lv["de"] = [s[:2] + [0.25] for s in lv["cur"][:1]]
        txt["de"] = "Damage is repaired quickly: a quarter of the capacity is out for a while."
    return lv, txt


REGIONS = ["Middle East", "Europe", "Russia & Central Asia", "Africa", "South Asia", "East Asia & Pacific", "Americas"]


def region(lon, lat):
    """A coarse world region, only for spreading the news lists."""
    if lon < -30:
        return "Americas"
    if 34 <= lon < 63 and 12 <= lat < 42:
        return "Middle East"
    if lat >= 35 and lon < 45:
        return "Europe"
    if lat >= 35 and lon < 90:
        return "Russia & Central Asia"
    if lon < 52 and lat < 35:
        return "Africa"
    if 60 <= lon < 92 and lat < 37:
        return "South Asia"
    return "East Asia & Pacific" if lon >= 92 or lat < 0 else "Russia & Central Asia"


def _rr(groups, n):
    """Round-robin over lists (each best first), lists in the order of their best item."""
    out, k = [], 0
    while len(out) < n and any(len(v) > k for v in groups):
        out += [v[k] for v in groups if len(v) > k][: n - len(out)]
        k += 1
    return out


def spread(items, n):
    """Up to n items, mixing kinds of news (conflict, quake, storm, outbreak, ...) and, within each, world regions."""
    cats = {}
    for x in items:
        cats.setdefault(x.get("cat", "conflict"), []).append(x)
    lists = []
    for v in cats.values():
        by = {}
        for x in v:
            by.setdefault(region(*x["at"]), []).append(x)
        lists.append(_rr(list(by.values()), len(v)))
    return _rr(lists, n)


def build(cand, grids, now):
    energy, power, cables, cities = grids
    cand = sorted(cand, key=lambda it: -(it.get("z") or 0))
    out, kept = [], []
    for it in cand:
        if not all(isinstance(it.get(k), (int, float)) for k in ("lat", "lon")):
            continue
        lat, lon = it["lat"], it["lon"]
        if any(haversine_km(lat, lon, a, b) < MERGE_KM for a, b in kept):
            continue
        c = classify(it)
        if not c:
            continue
        verb, sea = c
        sites = energy.within(lon, lat, ENERGY_KM) + power.within(lon, lat, POWER_KM) + cities.within(lon, lat, CITY_KM)
        lands = cables.within(lon, lat, CABLE_KM)
        chokes = [(haversine_km(lat, lon, y, x), s) for s, (_, x, y) in CHOKES.items()]
        chokes = [s for d, s in sorted(chokes) if d <= CHOKE_KM]
        if not sites and not (sea and (lands or chokes)):
            continue
        r = it.get("r") or [0, 0, 0]
        heavy = (r[1] + r[2]) / max(1, sum(r))
        m = round(min(1.0, 0.3 + 0.4 * heavy + (0.2 if r[2] else 0)) * 20) / 20  # 30-90% damage, more for CAMEO 19/20
        km = max(25, min(80, round5(max(d for d, _ in sites)))) if sites else 0
        lv, txt = levels(lon, lat, m, sites, km, sea, lands, chokes, grids)
        shocks = lv["cur"]
        if not shocks:
            continue
        tr = trend(it)
        name = place(it)
        what = describe(sites) if sites else ", ".join(p["n"] for _, p in lands[:3]) or CHOKES[chokes[0]][0]
        headline = (it.get("title") or "").strip()
        why = f"{what} within {km or CABLE_KM} km."
        if headline:
            why += f" In the news: {headline[:110] + '…' if len(headline) > 110 else headline + '.'}"
        urls = [u for u in (it.get("u") or []) if isinstance(u, str) and re.match(r"https?://", u)]
        out.append({"cat": "conflict", "label": f"{verb} near {name}", "why": why, "url": urls[0] if urls else None, "shocks": shocks,
                    "levels": lv, "lv": txt, "trend": tr, "likely": {"rising": "esc", "easing": "de"}.get(tr, "cur"),
                    "at": [round(lon, 2), round(lat, 2)], "months": [(it.get("t") or now.strftime("%Y-%m"))[:7]],
                    "place": it.get("n"), "t": it.get("t"), "z": it.get("z"),
                    "matched": [p["n"] for _, p in sites[:8]] + [p["n"] for _, p in lands[:3]] + [CHOKES[s][0] for s in chokes[:1]]})
        kept.append((lat, lon))
    return out


def windows(story, days, grids, prev, extra=()):
    """{"week", "month", "year"} item lists. prev: last run's file (for the year list) or None.
    extra: non-conflict items (news_topics.py), sorted into the windows by their date."""
    now = ts(story["updated"])
    W = story.get("windows", {})
    wk, mo = list(W.get("week", {}).get("items", [])), list(W.get("month", {}).get("items", []))
    recent = []
    for it in mo:
        try:
            if ts(it["t1"]) >= now - timedelta(days=days):
                recent.append(it)
        except (KeyError, ValueError):
            pass
    week = build(wk + recent, grids, now)
    month = build(wk + mo, grids, now)
    def within(x, d):
        try:
            return ts(x["t"]) >= now - timedelta(days=d)
        except (KeyError, ValueError, TypeError):
            return False
    week += sorted([x for x in extra if within(x, days)], key=lambda x: -(x.get("z") or 0))
    month += sorted([x for x in extra if within(x, 30)], key=lambda x: -(x.get("z") or 0))
    older = [x for x in extra if not within(x, 30) and within(x, 365)]
    # year: this month's items plus last year's list, one per place (the newest wins; the months it surged in add up)
    old = []
    for x in ((prev or {}).get("windows", {}).get("year", {}).get("items", []) if isinstance(prev, dict) else []):
        try:
            if isinstance(x, dict) and x.get("at") and x.get("levels") and ts(x["t"]) >= now - timedelta(days=365):
                old.append(x)
        except (KeyError, ValueError, TypeError):
            pass
    year = []
    for x in month + sorted(older + old, key=lambda x: x["t"], reverse=True):
        twin = next((y for y in year if y.get("cat", "conflict") == x.get("cat", "conflict")
                     and haversine_km(x["at"][1], x["at"][0], y["at"][1], y["at"][0]) < MERGE_KM), None)
        if twin:
            twin["months"] = sorted(set(twin["months"]) | set(x.get("months") or []))
        else:
            year.append(dict(x, months=list(x.get("months") or [])))
    year.sort(key=lambda x: (-len(x["months"]), -(x.get("z") or 0)))
    return {w: spread(v, MAX_ITEMS[w]) for w, v in (("week", week), ("month", month), ("year", year))}


SIM_KINDS = {"refinery", "field", "port", "lng", "pipeline", "choke", "zone", "power", "mine", "cable", "water", "fab", "chip", "cloud", "outbreak", "crop"}


def bad_shocks(sh):
    """The shocks in a list that the page would reject."""
    out = []
    for s in sh:
        ok = isinstance(s, list) and len(s) == 3 and s[0] in SIM_KINDS and isinstance(s[2], (int, float)) and 0 <= s[2] <= 1
        if ok and s[0] == "zone":
            z = s[1]
            ok = isinstance(z, list) and len(z) == 3 and all(isinstance(v, (int, float)) for v in z) \
                and -180 <= z[0] <= 180 and -90 <= z[1] <= 90 and 5 <= z[2] <= 200
        elif ok:
            ok = isinstance(s[1], str) and s[1] != ""
        if ok and s[0] == "choke":
            ok = s[1] in CHOKES
        if ok and s[0] == "outbreak":
            ph, *xy = s[1].split("_")
            ok = ph in ("flu", "cov", "sev") and len(xy) == 2 and all(re.match(r"^-?\d+(\.\d+)?$", v) for v in xy)
        if ok and s[0] == "crop":
            ok = bool(re.match(r"^(wheat|maize|rice|soy|banana|chicken)_[A-Z0-9]{3}$", s[1]))
        if not ok:
            out.append(s)
    return out


def check(doc):
    """Problems that would make the page drop an item (mirrors the loader in index.html), as strings."""
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        return ["top level must be an object with an items list"]
    wins = doc.get("windows") or {"week": {"items": doc["items"]}}
    if not isinstance(wins, dict):
        return ["windows must be an object"]
    bad = []
    for w, v in wins.items():
        if w not in ("week", "month", "year") or not isinstance(v, dict) or not isinstance(v.get("items"), list):
            bad.append(f"windows.{w}: must be week, month or year with an items list")
            continue
        if len(v["items"]) > MAX_ITEMS[w]:
            bad.append(f"{w}: {len(v['items'])} items; the page shows only the first {MAX_ITEMS[w]}")
        for i, x in enumerate(v["items"]):
            tag = f"{w} item {i} ({x.get('label') if isinstance(x, dict) else x!r})"
            if not isinstance(x, dict) or not isinstance(x.get("label"), str) or not x["label"].strip():
                bad.append(f"{tag}: needs a label")
                continue
            if x.get("url") is not None and not re.match(r"https?://", str(x["url"])):
                bad.append(f"{tag}: url must be http(s) or null")
            sh = x.get("shocks")
            if not isinstance(sh, list) or not sh:
                bad.append(f"{tag}: needs at least one shock")
                continue
            bad += [f"{tag}: bad shock {s!r}" for s in bad_shocks(sh)]
            lv = x.get("levels")
            if lv is None:
                continue
            if not isinstance(lv, dict) or set(lv) != {"de", "cur", "esc"}:
                bad.append(f"{tag}: levels must have de, cur and esc")
                continue
            for k, l in lv.items():
                if not isinstance(l, list) or not l:
                    bad.append(f"{tag}: level {k} needs at least one shock")
                else:
                    bad += [f"{tag}: level {k}: bad shock {s!r}" for s in bad_shocks(l)]
    return bad


def backfill_year(grids, until):
    """Year items from twelve 30-day story windows over GDELT 1.0 daily files (several GB to download once;
    the filtered days are cached in raw/live_cache/gdelt1_year). Uses fetch_live's own story code."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fetch_live as fl
    from concurrent.futures import ThreadPoolExecutor
    cache = os.path.join(ROOT, "raw", "live_cache", "gdelt1_year")
    os.makedirs(cache, exist_ok=True)
    end = until.replace(hour=0, minute=0, second=0, microsecond=0)
    items = []
    for k in range(1, 13):
        e = end - timedelta(days=30 * k)  # the month window already covers the latest 30 days
        b0 = e - timedelta(days=30)
        days = [(b0 + timedelta(days=i)).strftime("%Y%m%d") for i in range(30)]
        with ThreadPoolExecutor(max_workers=4) as ex:
            got = list(ex.map(lambda d: fl.gdelt1_day(d, cache), days))
        rows = [r for g in got if g for r in g]
        have = sum(1 for g in got if g is not None)
        if have < 20:
            log(f"backfill {days[0]}–{days[-1]}: only {have} daily files, skipped")
            continue
        bk = [(fl.iso(b0 + timedelta(days=3 * i)), fl.iso(b0 + timedelta(days=3 * i + 3))) for i in range(10)]

        def tri_of(r, b0=b0):
            d = datetime.strptime(r[10][:8], "%Y%m%d").replace(tzinfo=timezone.utc)
            if r[1] < (d - timedelta(days=3)).strftime("%Y%m%d"):
                return None
            i = (d - b0).days // 3
            return i if 0 <= i < 10 else None
        win = fl.story_window(rows, bk, tri_of, 60)  # more surges than the live month window, for a wider spread
        got_items = build(win["items"], grids, e)
        log(f"backfill {days[0]}–{days[-1]}: {len(rows)} events, {len(win['items'])} surges, {len(got_items)} scenario(s)")
        items += got_items
    return items


def news_items(grids, now, offline):
    """Earthquakes, storms, floods and topic news (news_topics.py), matched against the same site grids."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import news_topics as nt
    energy, power, cables, cities = grids
    def sites(lon, lat, km):
        hits = energy.within(lon, lat, km) + power.within(lon, lat, km) + cities.within(lon, lat, km)
        return [p["n"] + (" (city)" if p["k"] == "city" else "") for _, p in sorted(hits, key=lambda h: h[0])]
    opt = lambda f: load(f) if os.path.exists(os.path.join(DATA, f)) else None
    labels, totals = load("labels.json"), load("country_totals.json")
    G = nt.Gazetteer(labels, totals)
    pts = lambda fc, fn: [dict(fn(f["properties"]), x=f["geometry"]["coordinates"][0], y=f["geometry"]["coordinates"][1]) for f in (fc or {}).get("features", [])]
    def land_cc(p):
        tail = (p.get("n") or "").rsplit(", ", 1)[-1]
        return {"id": p["id"], "n": p["n"], "cc": dict(G.cnames).get(tail)}
    fabs_fc = opt("fabs.geojson") or {"features": []}
    data = {"food": (opt("domains.json") or {}).get("food") or {},
            "landings": pts(opt("landings.geojson"), land_cc),
            "cloud": pts(opt("cloud.geojson"), lambda p: {"id": p["id"], "n": f"{p.get('p', '')} {p.get('code', '')}".strip(), "cc": p.get("cc")}),
            "chipsites": [f["properties"] for f in (opt("chip_sites.geojson") or {}).get("features", [])],
            "fabs": [f["properties"] for f in fabs_fc["features"]]}
    F = nt.Fetch(os.path.join(ROOT, "raw", "live_cache", "news"), log, offline)
    out = nt.quakes(F, sites, fabs_fc["features"], now, log) + nt.storms(F, sites, fabs_fc["features"], now, log) + nt.topics(F, G, data, now, log)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=7, help="also take month-window surges that ended this many days back (default 7)")
    ap.add_argument("--story", default=os.path.join(DATA, "live", "story.json"))
    ap.add_argument("--out", default=os.path.join(DATA, "scenarios_weekly.json"))
    ap.add_argument("--check", action="store_true", help="only validate --out and exit 1 on problems")
    ap.add_argument("--no-topics", action="store_true", help="conflict surges only: skip earthquakes, storms and the GDELT topic searches")
    ap.add_argument("--offline-news", action="store_true", help="use the cached topic and disaster answers in raw/live_cache/news/")
    ap.add_argument("--backfill-year", action="store_true", help="also seed the year list from a year of GDELT 1.0 daily files (slow, one-off)")
    args = ap.parse_args()
    if args.check:
        with open(args.out, encoding="utf-8") as f:
            bad = check(json.load(f))
        for b in bad:
            log("problem: " + b)
        log(f"{os.path.basename(args.out)}: {'ok' if not bad else str(len(bad)) + ' problem(s)'}")
        sys.exit(1 if bad else 0)
    with open(args.story, encoding="utf-8") as f:
        story = json.load(f)
    prev = None
    if os.path.exists(args.out):
        try:
            with open(args.out, encoding="utf-8") as f:
                prev = json.load(f)
        except (OSError, ValueError):
            log("couldn't read the previous file; the year list starts again")
    grids = facilities()
    if args.backfill_year:
        old = backfill_year(grids, ts(story["updated"]))
        prev = {"windows": {"year": {"items": old + (((prev or {}).get("windows") or {}).get("year") or {}).get("items", [])}}}
    extra = [] if args.no_topics else news_items(grids, ts(story["updated"]), args.offline_news)
    wins = windows(story, args.days, grids, prev, extra)
    for w, items in wins.items():
        for x in items:
            log(f"{w}: {x['label']}: {x['trend']}, {len(x['months'])} month(s), z {x['z']}")
    items = wins["week"]
    doc = {"updated": story["updated"], "source": "GDELT storyboard surges matched to sites within "
           f"{ENERGY_KM} km (cables {CABLE_KM} km, chokepoints {CHOKE_KM} km); see pipeline/live/story_scenarios.py",
           "items": items, "windows": {w: {"items": v} for w, v in wins.items()}}
    bad = check(doc)
    if bad:
        raise SystemExit("generated file fails its own check: " + "; ".join(bad))
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)  # indented so the weekly PR diff is readable
        f.write("\n")
    os.replace(tmp, args.out)
    log(", ".join(f"{len(v)} {w}" for w, v in wins.items()) + f" scenario(s) -> {args.out}")


if __name__ == "__main__":
    main()
