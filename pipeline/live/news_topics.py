"""Non-conflict news for the Scenarios panel: disasters, disease, harvests, internet, cloud and chips.

Used by story_scenarios.py, which merges these items with the conflict surges. Sources:

  USGS earthquakes (public domain)   M6.5+ in the last year, with the PAGER impact alert
  NASA EONET (public domain)         severe storms (tracks with wind speed) and floods, last year
  GDELT DOC 2.0 (free, with credit)  article searches by topic over the last 3 months: bird flu,
                                     crop disease, failed harvests, human outbreaks, cable cuts,
                                     cloud outages, cyberattacks on infrastructure, chip export curbs

Every item has the same shape as a conflict item (label, why, url, shocks, levels, lv, trend,
likely, at, months, place, t, z, matched) plus "cat". Places in GDELT headlines are found by
matching city and country names from labels.json, so a story that names no place is dropped.
GDELT allows one request every 5 s; requests here are 8 s apart and a failure keeps the last
cached answer (raw/live_cache/news/), so a rate-limited run still produces a file.
Standard library only.
"""
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

UA = "resourcemap-news/1.0 (+https://github.com/byron123t/resourcemap)"
GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"
EONET = "https://eonet.gsfc.nasa.gov/api/v3/events"


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class Fetch:
    """JSON fetches cached under cache_dir; on failure the cached copy is used (None if there is none)."""

    def __init__(self, cache_dir, log, offline=False):
        self.dir, self.log, self.offline, self.last = cache_dir, log, offline, 0.0
        os.makedirs(cache_dir, exist_ok=True)

    def json(self, name, url, gap=0.0, tries=3):
        path = os.path.join(self.dir, name)
        if not self.offline:
            for k in range(tries):
                wait = self.last + gap - time.time()
                if wait > 0:
                    time.sleep(wait)
                try:
                    body = _get(url)
                    self.last = time.time()
                    d = json.loads(body)  # GDELT answers a rate limit with plain text, which raises here
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(d, f)
                    return d
                except Exception as e:  # noqa: BLE001 - any failure falls back to the cache
                    self.last = time.time()
                    self.log(f"{name}: {type(e).__name__} ({k + 1}/{tries})")
                    time.sleep(gap * (k + 1))
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return None


def _hav(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742.0 * math.asin(min(1.0, math.sqrt(a)))


def _r5(v):
    return int(5 * math.ceil(v / 5))


def _item(cat, label, why, url, lv, txt, lon, lat, t, z, place, matched):
    return {"cat": cat, "label": label, "why": why, "url": url, "shocks": lv["cur"], "levels": lv, "lv": txt,
            "trend": "steady", "likely": "cur", "at": [round(lon, 2), round(lat, 2)], "months": [t[:7]],
            "place": place, "t": t, "z": z, "matched": matched}


class Gazetteer:
    """Finds the first place named in a headline: a city of 300,000+ or a country (labels.json)."""

    def __init__(self, labels, totals):
        self.cities = sorted([l for l in labels if l.get("t") == "p" and (l.get("pop") or 0) >= 300_000],
                             key=lambda l: -len(l["n"]))
        self.countries = {l["k"]: l for l in labels if l.get("t") == "c"}
        names = {}
        for k, c in totals.items():
            if c.get("n"):
                names[c["n"]] = k
        for l in self.countries.values():
            names.setdefault(l["n"], l["k"])
        names.update({"US": "USA", "U.S.": "USA", "America": "USA", "UK": "GBR", "Britain": "GBR", "Russia": "RUS", "China": "CHN",
                      "Taiwan": "TWN", "South Korea": "KOR", "North Korea": "PRK", "Iran": "IRN", "Vietnam": "VNM", "Congo": "COD",
                      "DR Congo": "COD", "Democratic Republic of Congo": "COD", "Ivory Coast": "CIV", "Philippines": "PHL",
                      "Netherlands": "NLD", "Czech Republic": "CZE", "Syria": "SYR", "Laos": "LAO", "Bolivia": "BOL",
                      "Tanzania": "TZA", "Venezuela": "VEN", "Moldova": "MDA"})
        self.cnames = sorted(names.items(), key=lambda kv: -len(kv[0]))
        self.biggest = {}
        for l in labels:
            if l.get("t") == "p" and l.get("pop") and l.get("k") and l["pop"] > self.biggest.get(l["k"], {}).get("pop", 0):
                self.biggest[l["k"]] = l

    def find(self, title):
        """(ISO3, lon, lat, place name, city?) for the place named first in the headline, or None."""
        best = None  # (start, -length, result)
        for l in self.cities:
            m = re.search(r"\b" + re.escape(l["n"]) + r"\b", title)
            if m and (best is None or (m.start(), -len(l["n"])) < best[:2]):
                best = (m.start(), -len(l["n"]), (l.get("k"), l["x"], l["y"], l["n"], True))
        for n, k in self.cnames:
            m = re.search(r"\b" + re.escape(n) + r"\b", title)
            c = m and (self.biggest.get(k) or self.countries.get(k))
            if c and (best is None or (m.start(), -len(n)) < best[:2]):
                best = (m.start(), -len(n), (k, c["x"], c["y"], n, False))
        return best[2] if best else None


# --- earthquakes and storms: zones over the place, plus chip fabs inside them ---------------------------

def _fab_shocks(fabs, lon, lat, km, m):
    hit = [f for f in fabs if f["properties"].get("s") == "operating"
           and _hav(lat, lon, f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]) <= km]
    hit.sort(key=lambda f: -(f["properties"].get("cap300") or 0))
    return [["fab", f["properties"]["id"], m] for f in hit[:6]], [f["properties"]["n"] for f in hit[:6]]


def _zone_levels(lon, lat, km, m, fabs, what):
    """de/cur/esc for a disaster zone: smaller and lighter, as reported, wider and heavier."""
    lv, txt = {}, {}
    for k, kk, mm in (("de", 0.6, 0.5), ("cur", 1.0, 1.0), ("esc", 1.5, None)):
        r = max(10, min(200, _r5(km * kk)))
        dm = round(min(1.0, m + 0.25 if mm is None else m * mm) * 20) / 20
        fs, names = _fab_shocks(fabs, lon, lat, r, dm)
        lv[k] = [["zone", [round(lon, 2), round(lat, 2), r], max(0.05, dm)]] + fs
        txt[k] = {"de": f"Lighter than feared: {r} km around the {what}, {round(dm * 100)}% damage, repaired fast.",
                  "cur": f"As reported: {r} km around the {what}, {round(dm * 100)}% damage.",
                  "esc": f"Worse: aftershocks, landslides or flooding spread it to {r} km at {round(dm * 100)}% damage."}[k] \
            + (f" Chip fabs inside: {', '.join(names[:3])}." if names else "")
    return lv, txt


def quakes(F, sites, fabs, now, log):
    url = USGS + "?" + urllib.parse.urlencode({"format": "geojson", "starttime": (now - timedelta(days=365)).strftime("%Y-%m-%d"),
                                                 "minmagnitude": "6.0", "orderby": "magnitude"})
    d = F.json("usgs_quakes.json", url)
    out = []
    alert_m = {"green": 0.15, "yellow": 0.3, "orange": 0.5, "red": 0.7}
    for f in (d or {}).get("features", []):
        p, (lon, lat) = f["properties"], f["geometry"]["coordinates"][:2]
        mag = p.get("mag") or 0
        km = 25 if mag < 6.5 else 40 if mag < 7 else 70 if mag < 7.5 else 120 if mag < 8 else 180
        near = sites(lon, lat, km)
        fs, _ = _fab_shocks(fabs, lon, lat, km, 0.5)
        if not near and not fs:
            continue
        m = alert_m.get(p.get("alert"), 0.2)
        lv, txt = _zone_levels(lon, lat, km, m, fabs, "epicentre")
        t = datetime.fromtimestamp(p["time"] / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        place = re.sub(r"^\d+ km [NSEW]+ of |^The \d{4} |\s*Earthquake$", "", p.get("place") or "").replace(" Region,", ",")
        alert = f", {p['alert']} alert for damage" if p.get("alert") else ""
        out.append(_item("quake", f"M{mag:.1f} earthquake near {place.split(',')[0]}",
                         f"{', '.join(n for n in near[:3])}{' and more' if len(near) > 3 else ''} within {km} km (USGS{alert}).",
                         p.get("url"), lv, txt, lon, lat, t, mag * 10 + {"red": 40, "orange": 30, "yellow": 15}.get(p.get("alert"), 0), place, near[:8]))
    log(f"USGS: {len(out)} earthquake scenario(s)")
    return out


def storms(F, sites, fabs, now, log):
    url = EONET + "?" + urllib.parse.urlencode({"status": "all", "days": "365", "category": "severeStorms,floods"})
    d = F.json("eonet.json", url)
    out = []
    for e in (d or {}).get("events", []):
        cat = e["categories"][0]["id"] if e.get("categories") else ""
        pts = [g for g in e.get("geometry", []) if g.get("type") == "Point"]
        if not pts:
            continue
        if cat == "severeStorms":
            # the point on the track with the most at stake within 150 km, weighted by wind there
            best = max(pts, key=lambda g: len(sites(*g["coordinates"], 150)) * (g.get("magnitudeValue") or 30))
            kts, km = best.get("magnitudeValue") or max((g.get("magnitudeValue") or 0) for g in pts), 150
            m = 0.2 if kts < 64 else 0.35 if kts < 96 else 0.5 if kts < 113 else 0.7
            what, kind = "storm's landfall", "storm"
        elif cat == "floods":
            best, km, m, what, kind, kts = pts[-1], 80, 0.4, "flood", "flood", 0
        else:
            continue
        lon, lat = best["coordinates"]
        near = sites(lon, lat, km)
        if not near:
            continue
        lv, txt = _zone_levels(lon, lat, km, m, fabs, what)
        src = next((s.get("url") for s in e.get("sources", []) if s.get("url")), None)
        title = re.sub(r"\s+\d{5,}$", "", e["title"]).strip()
        out.append(_item(kind, title, f"{', '.join(near[:3])}{' and more' if len(near) > 3 else ''} within {km} km"
                         + (f"; winds up to {kts:.0f} knots" if kts else "") + " (NASA EONET).",
                         src, lv, txt, lon, lat, best["date"], kts or 40, title, near[:8]))
    # large wildfires (not prescribed burns) within 30 km of a city, power plant or energy site, newest first
    url = EONET + "?" + urllib.parse.urlencode({"status": "all", "days": "365", "category": "wildfires"})
    d = F.json("eonet_fires.json", url)
    fires = []
    for e in (d or {}).get("events", []):
        g = (e.get("geometry") or [None])[-1]
        if not g or g.get("type") != "Point" or re.search(r"prescribed|\bRX\b", e.get("title", ""), re.I):
            continue
        size = (g.get("magnitudeValue") or 0) * (0.405 if (g.get("magnitudeUnit") or "").startswith("acre") else 1)  # hectares
        if size < 2000:
            continue
        lon, lat = g["coordinates"]
        near = sites(lon, lat, 30)
        if not near:
            continue
        km = 15 if size < 10000 else 25 if size < 50000 else 40
        lv, txt = _zone_levels(lon, lat, km, 0.3, fabs, "fire")
        title = re.sub(r"\s+\d{5,}$", "", e["title"]).strip()
        src = next((s.get("url") for s in e.get("sources", []) if s.get("url")), None)
        fires.append(_item("fire", title, f"{', '.join(near[:3])}{' and more' if len(near) > 3 else ''} within 30 km; about {size:,.0f} hectares burnt (NASA EONET).",
                           src, lv, txt, lon, lat, g["date"], min(100, size / 1000), title, near[:8]))
    fires.sort(key=lambda x: x["t"], reverse=True)
    out += fires[:40]
    log(f"EONET: {len(out)} storm/flood/fire scenario(s)")
    return out


# --- GDELT topic searches ------------------------------------------------------------------------------

def _crop_item(title):
    t = title.lower()
    for k, pat in (("banana", r"banana|tr4|panama disease"), ("wheat", r"wheat|rust"), ("rice", r"\brice\b|paddy"),
                   ("maize", r"maize|\bcorn\b|armyworm"), ("soy", r"soy")):
        if re.search(pat, t):
            return k
    return None


# (category, query, how to turn a headline + place into shocks). Queries are English-language only.
TOPICS = [
    ("animal", '("bird flu" OR "avian influenza" OR H5N1) (poultry OR farm OR culled OR cull OR flock) sourcelang:english'),
    ("harvest", '(drought OR heatwave OR frost OR flooding OR locust OR "wheat rust" OR "fall armyworm" OR "Panama disease") (wheat OR corn OR maize OR rice OR soybean OR banana) (harvest OR crop OR yields) sourcelang:english'),
    ("outbreak", '(Ebola OR Marburg OR Nipah OR mpox OR "novel virus" OR "mystery illness" OR "unknown disease") outbreak sourcelang:english'),
    ("cable", '("undersea cable" OR "subsea cable" OR "submarine cable") (cut OR damaged OR severed OR outage OR sabotage) sourcelang:english'),
    ("cloud", '(AWS OR "Amazon Web Services" OR Azure OR "Google Cloud" OR Cloudflare OR "data center") (outage OR fire OR "went down") sourcelang:english'),
    ("cyber", '(ransomware OR cyberattack OR "cyber attack" OR hackers) (port OR pipeline OR "power grid" OR refinery OR airport OR hospital OR water) sourcelang:english'),
    ("chips", '(semiconductor OR chip OR gallium OR germanium OR "rare earth") ("export controls" OR "export ban" OR shortage OR "fab fire" OR "plant shut") sourcelang:english'),
]
# a headline must also say something happened, not just mention the topic
MUST = {"animal": r"cull|outbreak|detect|confirm|infect|spread|farm",
        "harvest": r"drought|frost|flood|pest|rust|locust|armyworm|fail|loss|lost|damage|slump|plunge|fall|shortfall|disease",
        "outbreak": r"outbreak|case|confirm|spread|death|died|kill",
        "cable": r"\bcut\b|damag|sever|outage|sabotag|disrupt",
        "cloud": r"outage|\bdown\b|went down|fire|disrupt|fail",
        "cyber": r"attack|ransomware|hack|breach|shut",
        "chips": r"\bban\b|banned|curb|control|restrict|shortage|halt|\bfire\b|shut|outage|quake|earthquake|suspend"}
NOT_NEWS = r"\b(drill|exercise|simulation|anniversary|lessons|years after|stock|shares|earnings|opinion|explainer)\b"


def topics(F, G, data, now, log, timespan="3months"):
    """Items from GDELT article searches, one per (topic, place), strongest = most articles."""
    out = []
    for cat, q in TOPICS:
        url = GDELT_DOC + "?" + urllib.parse.urlencode({"query": q, "mode": "artlist", "format": "json", "maxrecords": "250",
                                                         "timespan": timespan, "sort": "datedesc"})
        d = F.json(f"gdelt_{cat}.json", url, gap=8)
        arts = (d or {}).get("articles", [])
        groups = {}
        for a in arts:
            title = (a.get("title") or "").strip()
            if not title or re.search(NOT_NEWS, title, re.I) or not re.search(MUST[cat], title, re.I):
                continue
            hit = G.find(title)
            if not hit:
                continue
            key = (hit[0], hit[3])
            groups.setdefault(key, []).append((a, hit))
        n0 = len(out)
        for (cc, name), lst in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:6]:
            a, (cc, lon, lat, name, is_city) = lst[0]  # newest first
            it = _topic_item(cat, a, cc, lon, lat, name, len(lst), data, G)
            if it:
                out.append(it)
        log(f"GDELT {cat}: {len(arts)} articles, {len(out) - n0} scenario(s)")
    return out


def _seen(a):
    s = a.get("seendate") or ""
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _topic_item(cat, a, cc, lon, lat, name, n, data, G):
    title, url, t = a["title"], a.get("url"), _seen(a)
    head = f" In the news: {title[:110] + '…' if len(title) > 110 else title + '.'}"
    pct = lambda v: f"{round(v * 100)}%"
    lv, txt, label, why, matched = None, None, None, None, []
    prod = (data.get("food") or {}).get("prod") or {}
    if cat in ("animal", "harvest"):
        item = "chicken" if cat == "animal" else _crop_item(title)
        if not item or not cc or not (prod.get(item) or {}).get(cc):
            return None
        ms = (0.05, 0.15, 0.35) if cat == "animal" else (0.1, 0.25, 0.45)
        tok = f"{item}_{cc}"
        lv = {k: [["crop", tok, m]] for k, m in zip(("de", "cur", "esc"), ms)}
        what = "flocks culled" if cat == "animal" else "of the crop lost"
        txt = {"de": f"Contained quickly: {pct(ms[0])} {what}.", "cur": f"As reported: about {pct(ms[1])} {what}.", "esc": f"Spreads across the country: {pct(ms[2])} {what}."}
        label = ("Bird flu in " if cat == "animal" else {"banana": "Banana disease in ", "wheat": "Wheat losses in ", "rice": "Rice losses in ",
                                                          "maize": "Maize losses in ", "soy": "Soy losses in "}[item]) + name
        why = f"{n} recent article{'s' if n > 1 else ''} about {'bird flu on farms' if cat == 'animal' else item + ' harvests'} in {name}."
    elif cat == "outbreak":
        sev = re.search(r"ebola|marburg|nipah", title, re.I)
        ph = "sev" if sev else "cov"
        tok = f"{ph}_{lon:.2f}_{lat:.2f}"
        ms = (0.3, 0.55, 1.0)
        lv = {k: [["outbreak", tok, m]] for k, m in zip(("de", "cur", "esc"), ms)}
        txt = {"de": "Contained: tracing and isolation keep it mostly local.", "cur": "Partly controlled: it spreads, but slower than it could.",
               "esc": "Uncontrolled: it spreads as fast as the virus allows."}
        label, why = f"Outbreak in {name}", f"{n} recent article{'s' if n > 1 else ''} on an outbreak in {name}. Modelled as a {'severe' if sev else 'COVID-like'} virus starting there."
    elif cat == "cable":
        lands = [l for l in data["landings"] if l["cc"] == cc] if cc else []
        lands.sort(key=lambda l: _hav(lat, lon, l["y"], l["x"]))
        if not lands:
            return None
        k3 = lands[:3]
        lv = {"de": [["cable", k3[0]["id"], 1]], "cur": [["cable", l["id"], 1] for l in k3[:2]], "esc": [["cable", l["id"], 1] for l in k3]}
        txt = {"de": f"One landing cut: {k3[0]['n']}.", "cur": f"{len(lv['cur'])} landings cut.", "esc": f"Every nearby landing cut: {', '.join(l['n'] for l in k3)}."}
        label, why, matched = f"Cable cuts near {name}", f"{n} recent article{'s' if n > 1 else ''} on damaged undersea cables near {name}.", [l["n"] for l in k3]
    elif cat == "cloud":
        regs = sorted([r for r in data["cloud"] if not cc or r["cc"] == cc], key=lambda r: _hav(lat, lon, r["y"], r["x"]))
        if not regs:
            return None
        lv = {"de": [["cloud", regs[0]["id"], 0.5]], "cur": [["cloud", regs[0]["id"], 1]], "esc": [["cloud", r["id"], 1] for r in regs[:4]]}
        txt = {"de": f"Half of {regs[0]['n']} down for hours.", "cur": f"{regs[0]['n']} down.", "esc": f"{min(4, len(regs))} nearby regions down together."}
        label, why, matched = f"Cloud outage near {name}", f"{n} recent article{'s' if n > 1 else ''} on a cloud or data-centre outage around {name}.", [r["n"] for r in regs[:4]]
    elif cat == "cyber":
        if not is_city_named(name, G):
            return None
        ms = (0.1, 0.3, 0.6)
        lv = {k: [["zone", [round(lon, 2), round(lat, 2), 25], m]] for k, m in zip(("de", "cur", "esc"), ms)}
        txt = {"de": "Systems restored within a day: 10% loss in a 25 km area.", "cur": "Days of disruption: 30% of what runs on computers there stops.",
               "esc": "Weeks of disruption across the city: 60%."}
        label, why = f"Cyberattack in {name}", f"{n} recent article{'s' if n > 1 else ''} on a cyberattack on infrastructure in {name}. Modelled as a 25 km zone where power, fuel and transport stop in part."
    elif cat == "chips":
        if re.search(r"gallium|germanium", title, re.I):
            site = next((s for s in data["chipsites"] if s["cat"] == "mineral"), None)
            if not site:
                return None
            lv = {"de": [["chip", site["id"], 0.3]], "cur": [["chip", site["id"], 0.6]], "esc": [["chip", site["id"], 1]]}
            txt = {"de": "Licences slow exports a little.", "cur": "Exports cut sharply.", "esc": "Exports stop."}
            label = "Gallium and germanium curbs"
        else:
            if not cc or not any(f["cc"] == cc for f in data["fabs"]):
                return None
            lv = {"de": [["fab", "cc-" + cc.lower(), 0.05]], "cur": [["fab", "cc-" + cc.lower(), 0.15]], "esc": [["fab", "cc-" + cc.lower(), 0.4]]}
            txt = {"de": "5% of the country's chip output held up.", "cur": "15% of the country's chip output held up.", "esc": "40% of the country's chip output held up."}
            label = f"Chip supply shock in {name}"
        why = f"{n} recent article{'s' if n > 1 else ''} on chip export curbs or shortages ({name})."
    if not lv:
        return None
    return _item(cat, label, why + head, url, lv, txt, lon, lat, t, n, name, matched)


def is_city_named(name, G):
    return any(c["n"] == name for c in G.cities)
