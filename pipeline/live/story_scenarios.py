#!/usr/bin/env python3
"""Turn the week's storyboard surges into ready-made Scenarios.

Run weekly by .github/workflows/scenarios-weekly.yml after the story feed; devs can run it locally:

    python3 -I pipeline/live/fetch_live.py --only story
    python3 -I pipeline/live/story_scenarios.py [--days 7] [--story PATH] [--out PATH]
    python3 -I pipeline/live/story_scenarios.py --check      # validate the file only (after hand review)

Reads site/data/live/story.json (week window, plus month-window surges that ended in the
last --days) and keeps the surges that look like strikes or fighting (headline keywords,
or CAMEO 19/20-heavy when there is no headline) and sit near something the Scenarios panel
can break: refineries, oil ports, LNG terminals and field clusters from energy_graph.json,
power plants of 1 GW or more, cable landings, and the eight sea chokepoints. Each one
becomes a zone shock sized to cover what it matched, plus cable and chokepoint shocks.

Writes site/data/scenarios_weekly.json:
    {"updated": ISO, "source": ..., "items": [{"label", "why", "url", "shocks", "place", "t", "z", "matched"}]}
The page shows at most 12 items in the "This week's news" tab and ignores the extra keys,
which are there for the reviewer (a person, or the Claude step in the workflow).
Matching is keyword- and distance-based, so expect some noise; the workflow's review step
drops it before the file is committed.

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
MAX_ITEMS = 12
ENERGY_KM, POWER_KM, CABLE_KM, CHOKE_KM = 75, 75, 50, 150
POWER_MIN_MW = 1000
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
NOISE = r"\bschool\b|\bshooting\b|\bshooter\b|\bgunman\b|\bexecution\b|\btrial\b|\bcourt\b|\blawsuit\b|\bcharges?\b|\barrest(ed|s)?\b|\bprotests?\b|\bmurder\b|\bhostage\b|\bjournalist\b|\belection\b"
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
    log(f"{len(energy)} energy sites, {len(power)} power plants ≥{POWER_MIN_MW} MW, {len(cables)} cable landings")
    return Grid(energy), Grid(power), Grid(cables)


def classify(item):
    """(label verb, sea story?) for a surge, or None if it doesn't look like a strike or fighting."""
    title = (item.get("title") or "").lower()
    r = item.get("r") or [0, 0, 0]
    heavy = (r[1] + r[2]) / max(1, sum(r))
    if not title:
        return ("Fighting", False) if heavy >= 0.9 and item.get("e", 0) >= 20 else None
    verb = next((v for pat, v in STRIKE if re.search(pat, title)), None)
    if not verb:
        return None
    if re.search(NOISE, title) and verb in ("Attacks", "Fighting"):
        return None  # "shooting at school", "arrested amid protests over October attacks"
    return verb, bool(re.search(SEA, title))


def place(item):
    return (item.get("n") or "").split(",")[0].strip() or f"{item['lat']:.1f}°, {item['lon']:.1f}°"


def describe(sites):
    """'Kremenchuk refinery, Kremenchuk TPP and 2 more' from matched sites, most important first."""
    def rank(s):
        d, p = s
        return (KIND_ORDER.index(p["k"]) if p["k"] in KIND_ORDER else 5 if p["k"] == "power" else 6, d)
    seen, parts = set(), []
    for d, p in sorted(sites, key=rank):
        nm = p["n"]
        if p["k"] == "power":
            nm = f"{nm} ({p['mw'] / 1000:.1f} GW{', ' + p['f'] if p.get('f') else ''})"
        elif p["k"] in KIND and KIND[p["k"]].split()[0].lower() not in nm.lower():
            nm = f"{nm} ({KIND[p['k']]})"
        if nm not in seen:
            seen.add(nm)
            parts.append(nm)
    if len(parts) > 3:
        return ", ".join(parts[:3]) + f" and {len(parts) - 3} more"
    return " and ".join([", ".join(parts[:-1]), parts[-1]]) if len(parts) > 1 else parts[0]


def build(story, days, grids):
    energy, power, cables = grids
    now = datetime.strptime(story["updated"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    since = now - timedelta(days=days)
    W = story.get("windows", {})
    cand = list(W.get("week", {}).get("items", []))
    for it in W.get("month", {}).get("items", []):
        try:
            if datetime.strptime(it["t1"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) >= since:
                cand.append(it)
        except (KeyError, ValueError):
            pass
    cand.sort(key=lambda it: -(it.get("z") or 0))
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
        sites = energy.within(lon, lat, ENERGY_KM) + power.within(lon, lat, POWER_KM)
        lands = cables.within(lon, lat, CABLE_KM)
        chokes = [(haversine_km(lat, lon, y, x), s) for s, (_, x, y) in CHOKES.items()]
        chokes = [s for d, s in sorted(chokes) if d <= CHOKE_KM]
        if not sites and not (sea and (lands or chokes)):
            continue
        r = it.get("r") or [0, 0, 0]
        heavy = (r[1] + r[2]) / max(1, sum(r))
        m = round(min(1.0, 0.3 + 0.4 * heavy + (0.2 if r[2] else 0)) * 20) / 20  # 30-90% damage, more for CAMEO 19/20
        shocks = []
        if sites:
            km = max(25, min(80, 5 * math.ceil(max(d for d, _ in sites) / 5)))
            shocks.append(["zone", [round(lon, 2), round(lat, 2), km], m])
        if sea:
            shocks += [["cable", p["id"], 1] for _, p in lands[:3]]
            shocks += [["choke", s, 0.5] for s in chokes[:1]]
        if not shocks:
            continue
        name = place(it)
        what = describe(sites) if sites else ", ".join(p["n"] for _, p in lands[:3]) or CHOKES[chokes[0]][0]
        headline = (it.get("title") or "").strip()
        why = f"{what} within {shocks[0][1][2] if shocks[0][0] == 'zone' else CABLE_KM} km."
        if headline:
            why += f" In the news: {headline[:110] + '…' if len(headline) > 110 else headline + '.'}"
        urls = [u for u in (it.get("u") or []) if isinstance(u, str) and re.match(r"https?://", u)]
        out.append({"label": f"{verb} near {name}", "why": why, "url": urls[0] if urls else None, "shocks": shocks,
                    "place": it.get("n"), "t": it.get("t"), "z": it.get("z"),
                    "matched": [p["n"] for _, p in sites[:8]] + [p["n"] for _, p in lands[:3]] + [CHOKES[s][0] for s in chokes[:1]]})
        kept.append((lat, lon))
        if len(out) >= MAX_ITEMS:
            break
    return out


SIM_KINDS = {"refinery", "field", "port", "lng", "pipeline", "choke", "zone", "power", "mine", "cable", "water"}


def check(doc):
    """Problems that would make the page drop an item (mirrors the loader in index.html), as strings."""
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        return ["top level must be an object with an items list"]
    bad = []
    if len(doc["items"]) > MAX_ITEMS:
        bad.append(f"{len(doc['items'])} items; the page shows only the first {MAX_ITEMS}")
    for i, x in enumerate(doc["items"]):
        tag = f"item {i} ({x.get('label') if isinstance(x, dict) else x!r})"
        if not isinstance(x, dict) or not isinstance(x.get("label"), str) or not x["label"].strip():
            bad.append(f"{tag}: needs a label")
            continue
        if x.get("url") is not None and not re.match(r"https?://", str(x["url"])):
            bad.append(f"{tag}: url must be http(s) or null")
        sh = x.get("shocks")
        if not isinstance(sh, list) or not sh:
            bad.append(f"{tag}: needs at least one shock")
            continue
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
            if not ok:
                bad.append(f"{tag}: bad shock {s!r}")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=7, help="also take month-window surges that ended this many days back (default 7)")
    ap.add_argument("--story", default=os.path.join(DATA, "live", "story.json"))
    ap.add_argument("--out", default=os.path.join(DATA, "scenarios_weekly.json"))
    ap.add_argument("--check", action="store_true", help="only validate --out and exit 1 on problems")
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
    items = build(story, args.days, facilities())
    for x in items:
        log(f"{x['label']}: {len(x['shocks'])} shock(s), z {x['z']}")
    doc = {"updated": story["updated"], "source": "GDELT storyboard surges matched to sites within "
           f"{ENERGY_KM} km (cables {CABLE_KM} km, chokepoints {CHOKE_KM} km); see pipeline/live/story_scenarios.py",
           "items": items}
    bad = check(doc)
    if bad:
        raise SystemExit("generated file fails its own check: " + "; ".join(bad))
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)  # indented so the weekly PR diff is readable
        f.write("\n")
    os.replace(tmp, args.out)
    log(f"{len(items)} scenario(s) -> {args.out}")


if __name__ == "__main__":
    main()
