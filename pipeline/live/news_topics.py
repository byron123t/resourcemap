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
        self.dir, self.log, self.offline, self.last, self.fails = cache_dir, log, offline, 0.0, 0
        os.makedirs(cache_dir, exist_ok=True)

    def json(self, name, url, gap=0.0, tries=2):
        path = os.path.join(self.dir, name)
        # after three failed requests in a run (a rate-limited IP), stop asking and use the cache, so a build isn't held up
        if not self.offline and self.fails < 3:
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
                    self.fails += 1
                    self.log(f"{name}: {type(e).__name__} ({k + 1}/{tries})")
                    if self.fails >= 3:
                        self.log("three failed requests: using cached answers for the rest of this run")
                        break
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


WORSE = {"epicentre": "aftershocks and landslides spread the damage", "storm's landfall": "storm surge and inland flooding spread the damage",
         "flood": "the water keeps rising and spreads", "fire": "wind drives the fire further"}


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
                  "esc": f"Worse: {WORSE.get(what, 'it spreads')}, to {r} km at {round(dm * 100)}% damage."}[k] \
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
        place = re.sub(r"^\d+ km [NSEW]+ of |^(The )?\d{4} |\s*Earthquake$", "", p.get("place") or "").replace(" Region,", ",")
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
                   ("maize", r"maize|\bcorn\b|armyworm"), ("soy", r"soy"), ("millet", r"millet"), ("sorghum", r"sorghum"), ("cassava", r"cassava|manioc")):
        if re.search(pat, t):
            return k
    return None


# (category, query). Queries are English-language only. The storyboard shows every category; news scenarios map the ones
# that hit something the Scenarios panel models (see _topic_item).
TOPICS = [
    ("unrest", '(riots OR rioting OR curfew OR "state of emergency" OR "general strike" OR "nationwide strike" OR "martial law" OR coup) sourcelang:english'),
    ("trade", '("export controls" OR "export ban" OR "export curbs" OR tariffs OR sanctions OR embargo) (semiconductors OR chips OR "rare earth" OR gallium OR germanium OR wheat OR rice OR fertilizer OR oil OR LNG OR steel OR copper OR lithium) sourcelang:english'),
    ("industry", '(refinery OR "chemical plant" OR "power plant" OR "power station" OR mine OR dam OR pipeline OR "oil terminal" OR smelter OR factory) (explosion OR blast OR fire OR collapse OR leak OR spill OR "shut down") sourcelang:english'),
    ("energy", '(blackout OR "power outage" OR "power cuts" OR "fuel shortage" OR "gas shortage" OR "load shedding") (nationwide OR millions OR country OR grid) sourcelang:english'),
    ("shipping", '(tanker OR "cargo ship" OR vessel OR freighter) (seized OR attacked OR hijacked OR sank OR struck) sourcelang:english'),
    ("animal", '("bird flu" OR "avian influenza" OR H5N1 OR "swine fever" OR "foot-and-mouth") (poultry OR farm OR culled OR cull OR flock OR pigs OR cattle) sourcelang:english'),
    ("harvest", '(drought OR heatwave OR frost OR flooding OR locust OR "wheat rust" OR "fall armyworm" OR "Panama disease") (wheat OR corn OR maize OR rice OR soybean OR banana OR millet OR sorghum OR cassava OR coffee OR cocoa) (harvest OR crop OR yields) sourcelang:english'),
    ("outbreak", '(Ebola OR Marburg OR Nipah OR mpox OR cholera OR "novel virus" OR "mystery illness" OR "unknown disease" OR H5N1) (outbreak OR cases OR spread) sourcelang:english'),
    ("cable", '(("undersea cable" OR "subsea cable" OR "submarine cable") (cut OR damaged OR severed OR outage OR sabotage)) OR "internet shutdown" OR "internet blackout" sourcelang:english'),
    ("cloud", '(AWS OR "Amazon Web Services" OR Azure OR "Google Cloud" OR Cloudflare OR "data center") (outage OR fire OR "went down") sourcelang:english'),
    ("cyber", '(ransomware OR cyberattack OR "cyber attack" OR hackers) (port OR pipeline OR "power grid" OR refinery OR airport OR hospital OR water OR bank) sourcelang:english'),
    ("chips", '(semiconductor OR chip OR gallium OR germanium OR "rare earth") ("export controls" OR "export ban" OR shortage OR "fab fire" OR "plant shut") sourcelang:english'),
]
# storyboard categories: label shown on the page, and which topic categories feed them
STORY_CATS = {"unrest": "Unrest", "trade": "Trade", "industry": "Industry & energy", "energy": "Industry & energy", "shipping": "Shipping",
              "animal": "Food & farms", "harvest": "Food & farms", "outbreak": "Health", "cable": "Cyber & internet", "cloud": "Cyber & internet",
              "cyber": "Cyber & internet", "chips": "Trade", "quake": "Disasters", "storm": "Disasters", "flood": "Disasters", "fire": "Disasters"}
# a headline must also say something happened, not just mention the topic
MUST = {"unrest": r"riot|curfew|state of emergency|general strike|nationwide strike|martial law|coup|killed|dead|deaths|shut ?down|blockade|clashes",
        "trade": r"impos|announc|\bban\b|bans|banned|halt|tariff|sanction|restrict|curb|embargo|suspend|block",
        "industry": r"explosion|blast|fire|collapse|leak|spill|shut|killed|dead|trapped",
        "energy": r"blackout|outage|power cuts|shortage|load shedding|without power",
        "shipping": r"seized|attack|hijack|sank|sink|struck|hit",
        "animal": r"cull|outbreak|detect|confirm|infect|spread|farm",
        "harvest": r"drought|frost|flood|pest|rust|locust|armyworm|fail|loss|lost|damage|slump|plunge|fall|shortfall|disease",
        "outbreak": r"outbreak|case|confirm|spread|death|died|kill",
        "cable": r"\bcut\b|damag|sever|outage|sabotag|disrupt|shutdown|blackout",
        "cloud": r"outage|\bdown\b|went down|fire|disrupt|fail",
        "cyber": r"attack|ransomware|hack|breach|shut",
        "chips": r"\bban\b|banned|curb|control|restrict|shortage|halt|\bfire\b|shut|outage|quake|earthquake|suspend"}
# per topic: headlines that look on-topic but aren't consequential, or haven't happened
SKIP = {"chips": r"\bAI models?\b|software|propos|consider|plans? to|could|may\b|weigh",
        "trade": r"propos|consider|plans? to|could|may\b|weigh|threat|warns?|urge|talks|deal|lift|eas(e|es|ing)\b|removes?|pause",
        "unrest": r"\bsports?\b|football|soccer|fans|school|prison riot|game|concert|movie|film|video game",
        "industry": r"house fire|apartment|restaurant|car fire|bushfire|wildfire|forest fire|drill|simulation|fireworks",
        "energy": r"planned|scheduled|maintenance|tips|how to|prepare",
        "shipping": r"fishing boat|migrant|yacht|ferry"}
# unrest only counts when it is severe: deaths, a curfew or emergency, a general strike, martial law or a coup
SEVERE = {"unrest": r"killed|dead|deaths|curfew|state of emergency|general strike|nationwide|martial law|coup|toppl|ousted|army deployed|troops"}
NOT_NEWS = r"\b(drill|exercise|simulation|anniversary|lessons|years after|stock|shares|earnings|opinion|explainer|quiz|podcast)\b"


def search(F, G, cat, q, timespan="1month", log=print):
    """Articles for one topic that pass the filters, each with its place: [(article, (iso3, lon, lat, place, city?))]."""
    url = GDELT_DOC + "?" + urllib.parse.urlencode({"query": q, "mode": "artlist", "format": "json", "maxrecords": "250",
                                                     "timespan": timespan, "sort": "datedesc"})
    d = F.json(f"gdelt_{cat}.json", url, gap=8)
    out = []
    for a in (d or {}).get("articles", []):
        title = (a.get("title") or "").strip()
        if not title or re.search(NOT_NEWS, title, re.I) or not re.search(MUST[cat], title, re.I) or (cat in SKIP and re.search(SKIP[cat], title, re.I)):
            continue
        if cat in SEVERE and not re.search(SEVERE[cat], title, re.I):
            continue
        hit = G.find(title)
        if hit:
            out.append((a, hit))
    return out


def grouped(found):
    """One group per place, newest article first: {(iso3, place): [(article, hit)]}, with at least two outlets reporting."""
    groups = {}
    for a, hit in found:
        groups.setdefault((hit[0], hit[3]), []).append((a, hit))
    out = {}
    for k, lst in groups.items():
        lst.sort(key=lambda x: x[0].get("seendate") or "", reverse=True)
        outlets = {x[0].get("domain") or urllib.parse.urlparse(x[0].get("url") or "").netloc for x in lst}
        if len(outlets) >= 2:  # one outlet alone is too thin to call consequential
            out[k] = lst
    return out


def story_topics(F, G, log=print, timespan="1month"):
    """Storyboard items for every topic category: [{cat, label, t, t1, n, cc, lat, lon, a, d, title, u}]."""
    items = []
    for cat, q in TOPICS:
        found = search(F, G, cat, q, timespan, log)
        groups = grouped(found)
        for (cc, name), lst in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:8]:
            a, (cc, lon, lat, name, _) = lst[0]
            ts = sorted(_seen(x[0]) for x in lst)
            urls, seen = [], set()
            for x, _h in lst:
                u = x.get("url")
                if u and u not in seen and re.match(r"https?://", u):
                    seen.add(u); urls.append(u)
                if len(urls) == 4:
                    break
            items.append({"k": "topic", "cat": cat, "label": STORY_CATS[cat], "t": ts[0], "t1": ts[-1], "n": name, "cc": cc,
                          "lat": round(lat, 4), "lon": round(lon, 4), "a": len(lst),
                          "d": len({x[0].get("domain") for x in lst}), "title": a["title"].strip(), "u": urls})
        log(f"GDELT {cat}: {len(found)} matching articles, {len(groups)} places with 2+ outlets")
    return items


def story_disasters(F, now, log=print, G=None):
    """Storyboard items for M6+ earthquakes (USGS) and storms and floods (NASA EONET) in the last 30 days."""
    items = []
    url = USGS + "?" + urllib.parse.urlencode({"format": "geojson", "starttime": (now - timedelta(days=31)).strftime("%Y-%m-%d"), "minmagnitude": "6.0"})
    for f in (F.json("usgs_story.json", url) or {}).get("features", []):
        p, (lon, lat) = f["properties"], f["geometry"]["coordinates"][:2]
        if (p.get("mag") or 0) < 7 and p.get("alert") not in ("yellow", "orange", "red"):
            continue  # a moderate quake far from people (green alert) isn't news
        t = datetime.fromtimestamp(p["time"] / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        place = re.sub(r"^\d+ km [NSEW]+ of |^(The )?\d{4} |\s*Earthquake$", "", p.get("place") or "")
        items.append({"k": "topic", "cat": "quake", "label": STORY_CATS["quake"], "t": t, "t1": t, "n": place, "lat": round(lat, 4), "lon": round(lon, 4),
                      "a": 1, "d": 1, "mag": round(p["mag"], 1), "al": p.get("alert"), "title": f"M{p['mag']:.1f} earthquake near {place.split(',')[0]}" + (f" ({p['alert']} alert for damage)" if p.get("alert") else ""),
                      "u": [p["url"]] if p.get("url") else []})
    url = EONET + "?" + urllib.parse.urlencode({"status": "all", "days": "31", "category": "severeStorms,floods,volcanoes"})
    for e in (F.json("eonet_story.json", url) or {}).get("events", []):
        g = [x for x in e.get("geometry", []) if x.get("type") == "Point"]
        if not g:
            continue
        cat = {"severeStorms": "storm", "floods": "flood"}.get((e.get("categories") or [{}])[0].get("id"), "storm")
        title = re.sub(r"\s+\d{5,}$", "", e["title"]).strip()
        # only storms and floods that reach a city of 300,000+ (within 300 km of the track), placed at the closest pass
        best = None
        for pt in g if G else g[-1:]:
            x, y = pt["coordinates"]
            for c in (G.cities if G else []):
                if abs(c["y"] - y) < 3 and (best is None or _hav(y, x, c["y"], c["x"]) < best[0]):
                    d = _hav(y, x, c["y"], c["x"])
                    if d <= 300:
                        best = (d, c, pt)
        if G and not best:
            continue
        pt = best[2] if best else g[-1]
        place = f"near {best[1]['n']}" if best else title
        items.append({"k": "topic", "cat": cat, "label": STORY_CATS[cat], "t": g[0]["date"], "t1": g[-1]["date"], "n": place, "lat": round(pt["coordinates"][1], 4),
                      "lon": round(pt["coordinates"][0], 4), "a": 1, "d": 1, "title": title + (f" {place}" if best and place not in title else ""),
                      "u": [s["url"] for s in e.get("sources", []) if s.get("url")][:2]})
    log(f"disasters: {len(items)} storyboard item(s)")
    return items


def topics(F, G, data, now, log, timespan="3months", story=None):
    """News scenarios from topic news, one per (topic, place). With `story` (story.json's topic items) no search is made."""
    out = []
    if story is not None:
        for it in story:
            if it.get("k") != "topic" or it["cat"] not in MUST:
                continue
            a = {"title": it["title"], "url": (it.get("u") or [None])[0], "seendate": it["t1"].replace("-", "").replace(":", "")}
            x = _topic_item(it["cat"], a, it.get("cc"), it["lon"], it["lat"], it["n"], it["a"], data, G)
            if x:
                out.append(x)
        log(f"topic news from the storyboard: {len(out)} scenario(s)")
        return out
    for cat, q in TOPICS:
        groups = grouped(search(F, G, cat, q, timespan, log))
        n0 = len(out)
        for (cc, name), lst in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:6]:
            a, (cc, lon, lat, name, is_city) = lst[0]
            it = _topic_item(cat, a, cc, lon, lat, name, len(lst), data, G)
            if it:
                out.append(it)
        log(f"GDELT {cat}: {len(out) - n0} scenario(s)")
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
    elif cat == "industry":
        t_ = title.lower()
        kind = "refinery" if "refiner" in t_ else "mine" if re.search(r"\bmine|\bcolliery|\bpit\b", t_) else "lng" if "lng" in t_ else \
            "port" if re.search(r"\bport\b|oil terminal|harbou?r", t_) else "power" if re.search(r"power (plant|station)|\bdam\b|nuclear", t_) else None
        hit = kind and data["find"](kind, lon, lat, 60 if is_city_named(name, G) else 400)
        what = {"refinery": "refinery", "mine": "mine", "lng": "LNG terminal", "port": "port", "power": "power plant"}.get(kind, "site")
        if hit:
            k_ = {"refinery": "refinery", "mine": "mine", "lng": "lng", "port": "port", "power": "power"}[kind]
            lv = {k: [[k_, hit[1]["id"], m]] for k, m in zip(("de", "cur", "esc"), (0.2, 0.5, 1.0))}
            matched = [hit[1]["n"]]
            label = f"{'Mine accident' if kind == 'mine' else what.capitalize() + ' blast or fire'} near {name}"
            txt = {"de": f"Back in days: {hit[1]['n']} loses 20% for a while.", "cur": f"{hit[1]['n']} runs at half capacity.", "esc": f"{hit[1]['n']} is out for months."}
        else:
            lv = {k: [["zone", [round(lon, 2), round(lat, 2), r], m]] for k, r, m in zip(("de", "cur", "esc"), (5, 10, 20), (0.2, 0.5, 0.8))}
            label = f"Industrial accident near {name}"
            txt = {"de": "Contained to the site.", "cur": "Damage and closures within 10 km.", "esc": "Spreads: fires, leaks or evacuations within 20 km."}
        why = f"{n} recent article{'s' if n > 1 else ''} on an explosion, fire, collapse or spill at a {what} near {name}."
    elif cat == "unrest":
        ms = (0.1, 0.2, 0.4)
        lv = {k: [["zone", [round(lon, 2), round(lat, 2), r], m]] for k, r, m in zip(("de", "cur", "esc"), (15, 30, 60), ms)}
        txt = {"de": "Calms within days: little lasting disruption.", "cur": "Curfews and blockades slow roads, rail, fuel and power for weeks.", "esc": "Spreads nationwide: transport and supplies seize up across a wide area."}
        label, why = f"Unrest in {name}", f"{n} recent article{'s' if n > 1 else ''} on deadly unrest, a curfew, a state of emergency or a general strike in {name}."
    elif cat == "energy":
        lv = {k: [["zone", [round(lon, 2), round(lat, 2), r], m]] for k, r, m in zip(("de", "cur", "esc"), (50, 100, 200), (0.2, 0.4, 0.6))}
        txt = {"de": "Power is back within hours.", "cur": "Rolling cuts for days across the region.", "esc": "A long, wide blackout: plants and grid down across 200 km."}
        label, why = f"Blackout or fuel shortage in {name}", f"{n} recent article{'s' if n > 1 else ''} on blackouts, power cuts or fuel shortages in {name}."
    elif cat == "shipping":
        ck = min(data["chokes"].items(), key=lambda kv: _hav(lat, lon, kv[1][2], kv[1][1]))
        if _hav(lat, lon, ck[1][2], ck[1][1]) > 800:
            return None
        lv = {k: [["choke", ck[0], m]] for k, m in zip(("de", "cur", "esc"), (0.1, 0.25, 0.6))}
        txt = {"de": f"A few ships divert from {ck[1][0]}.", "cur": f"A quarter of traffic avoids {ck[1][0]}.", "esc": f"Most ships avoid {ck[1][0]}."}
        label, why = f"Ships attacked near {ck[1][0]}", f"{n} recent article{'s' if n > 1 else ''} on ships seized or attacked ({name})."
    elif cat == "trade":
        item = next((k for k, pat in (("wheat", r"wheat"), ("rice", r"\brice\b"), ("maize", r"maize|\bcorn\b"), ("soy", r"soy")) if re.search(pat, title, re.I)), None)
        if item and cc and (prod.get(item) or {}).get(cc) and re.search(r"export", title, re.I):
            tok = f"{item}_{cc}"
            lv = {k: [["crop", tok, m]] for k, m in zip(("de", "cur", "esc"), (0.05, 0.15, 0.3))}
            txt = {"de": "A short, partial ban.", "cur": "Exports cut for a season.", "esc": "A full ban for a year."}
            label, why = f"{item.capitalize()} export curbs in {name}", f"{n} recent article{'s' if n > 1 else ''} on {item} export curbs in {name}. Modelled as a cut in what it can sell abroad."
        elif re.search(r"semiconductor|chip|gallium|germanium|rare earth", title, re.I):
            return _topic_item("chips", a, cc, lon, lat, name, n, data, G)
        else:
            return None  # tariffs and sanctions on oil, steel or copper show on the storyboard only
    if not lv:
        return None
    return _item(cat, label, why + head, url, lv, txt, lon, lat, t, n, name, matched)


def is_city_named(name, G):
    return any(c["n"] == name for c in G.cities)
