"""Step 7: other domains: chips, cloud, biolabs, air links, country indicators, food trade (stdlib only).

Writes to site/data/:
  fabs.geojson         semiconductor fabs       Wikipedia "List of semiconductor fabrication plants" (CC BY-SA 4.0),
                                                placed on OSM works/industrial areas via Overpass (ODbL)
  chip_sites.geojson   chip supply-chain chokepoints (hand list in CHIP_SITES, one public source URL each)
  cloud.geojson        cloud regions            github.com/jasonwilbur/mcp-server-cloud-regions (MIT)
  biolabs.geojson      BSL-4 laboratories       Wikipedia "Biosafety level" (CC BY-SA 4.0) + Wikipedia coordinates
  air.json             country-to-country air routes  OpenFlights routes.dat (ODbL 1.0, frozen 2014)
  domains.json         per-country indicators (World Bank CC BY 4.0; Growth Lab Atlas HS92 CC0) and food
                       production/trade (FAOSTAT QCL + detailed trade matrix, CC BY 4.0), plus all sources

Raw downloads are cached in raw/domains/. Flags:
  --offline        use only the cache (fail if a required file is missing)
  --only=a,b       run only these sections (fabs, chips, cloud, biolabs, air, domains)
  --refresh-atlas  re-stream the 452 MB Growth Lab Atlas CSV (otherwise raw/domains/atlas_ic.csv is reused)
  --refresh-fao    re-download the FAOSTAT zips (otherwise the cached zips are reused)
The FAOSTAT zips (~450 MB) and the Atlas extract are downloaded once and kept.
"""
import os
ROOT = os.environ.get("ATLAS_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import csv, io, json, math, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request, zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser

W = ROOT
OUT = f"{W}/site/data"; os.makedirs(OUT, exist_ok=True)
RAW = f"{W}/raw/domains"; os.makedirs(RAW, exist_ok=True)
NE = f"{W}/raw/naturalearth"
UA = "resourcemap-pipeline/1.0 (+https://github.com/byron123t/resourcemap)"
OFFLINE = "--offline" in sys.argv
ONLY = next((a.split("=", 1)[1].split(",") for a in sys.argv if a.startswith("--only=")), None)
FETCHLOG = f"{RAW}/_fetch.json"
fetchlog = json.load(open(FETCHLOG)) if os.path.exists(FETCHLOG) else {}
DOMAINS = f"{OUT}/domains.json"
SOURCES = {}   # name -> {url, licence, retrieved}; written into domains.json

def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------- HTTP with cache (same conventions as step 5) ----------
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

def logfetch(name, url, hdr=None):
    fetchlog[name] = {"url": url, "retrieved": now_utc(),
                      "last_modified": (hdr or {}).get("Last-Modified") or (hdr or {}).get("last-modified")}
    json.dump(fetchlog, open(FETCHLOG, "w"), indent=1)

def cached(name, url, ua=UA, sleep=1, **kw):
    """Return bytes of raw/domains/<name>, downloading it unless --offline."""
    p = f"{RAW}/{name}"
    if not OFFLINE:
        print("fetch", url)
        b, hdr = http(url, ua=ua, **kw)
        open(p, "wb").write(b)
        logfetch(name, url, hdr)
        time.sleep(sleep)
    if not os.path.exists(p): raise SystemExit(f"missing {p}; run without --offline")
    return open(p, "rb").read()

def cached_big(name, url, refresh=False):
    """Stream a large file to raw/domains/<name> once and keep it (re-download only with refresh)."""
    p = f"{RAW}/{name}"
    if os.path.exists(p) and not refresh:
        if name not in fetchlog:  # placed by hand or by an older run: record the file time
            fetchlog[name] = {"url": url, "retrieved": datetime.fromtimestamp(os.path.getmtime(p), timezone.utc)
                              .strftime("%Y-%m-%dT%H:%M:%SZ")}
        return p
    if OFFLINE: raise SystemExit(f"missing {p}; run without --offline")
    print("fetch", url, "(large; streamed to disk)")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r, open(p + ".part", "wb") as f:
        hdr = dict(r.headers)
        while True:
            b = r.read(1 << 20)
            if not b: break
            f.write(b)
    os.replace(p + ".part", p)
    logfetch(name, url, hdr)
    return p

def src(name, fetchname, licence, url=None, **kw):
    e = fetchlog.get(fetchname, {})
    d = {"url": url or e.get("url"), "licence": licence, "retrieved": e.get("retrieved")}
    d.update(kw)
    SOURCES[name] = {k: v for k, v in d.items() if v is not None}

def r4(x): return round(float(x), 4)

def pt(lon, lat, props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [r4(lon), r4(lat)]},
            "properties": {k: v for k, v in props.items() if v not in (None, "")}}

def dump_geo(name, feats):
    p = f"{OUT}/{name}.geojson"
    json.dump({"type": "FeatureCollection", "features": feats}, open(p, "w", encoding="utf-8"),
              separators=(",", ":"), ensure_ascii=False)
    print(f"{name}.geojson", len(feats), "features", round(os.path.getsize(p) / 1e3, 1), "kB")

def dump_json(name, obj):
    p = f"{OUT}/{name}"
    json.dump(obj, open(p, "w", encoding="utf-8"), separators=(",", ":"), ensure_ascii=False)
    print(name, round(os.path.getsize(p) / 1e3, 1), "kB")

def dist_km(a, b):
    x = math.radians(b[0] - a[0]) * math.cos(math.radians((a[1] + b[1]) / 2))
    return 6371 * math.hypot(x, math.radians(b[1] - a[1]))

def fold(s):
    """Lowercase ASCII fold for name matching."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()

def slug(s, maxlen=60):
    s = re.sub(r"[^a-z0-9]+", "-", fold(s)).strip("-")
    return s[:maxlen].strip("-") or "x"

def uniq_ids(feats):
    seen = Counter()
    for f in feats:
        b = f["properties"]["id"]; seen[b] += 1
        if seen[b] > 1:
            n = seen[b]
            while f"{b}-{n}" in seen: n += 1
            f["properties"]["id"] = f"{b}-{n}"; seen[f"{b}-{n}"] += 1

# ---------- countries (Natural Earth ADM0_A3) ----------
_ne = json.load(open(f"{NE}/ne_50m_admin_0_countries.geojson", encoding="utf-8"))["features"]
ADM = {f["properties"]["ADM0_A3"] for f in _ne}
ISO2, N3, ISO3, CNAME = {}, {}, {}, {}
for pref in (True, False):  # main entries first so e.g. AU -> AUS, not Indian Ocean Ter.
    for f in _ne:
        p = f["properties"]; a = p["ADM0_A3"]
        if (a == p.get("ISO_A3_EH")) != pref: continue
        for k in ("ISO_A2_EH", "ISO_A2"):
            v = p.get(k)
            if v and v != "-99": ISO2.setdefault(v, a)
        for k in ("ISO_N3_EH", "ISO_N3"):
            v = p.get(k)
            if v and v != "-99": N3.setdefault(str(int(v)), a)
        for k in ("ADM0_A3", "ISO_A3_EH", "ISO_A3", "WB_A3", "SOV_A3"):
            v = p.get(k)
            if v and v != "-99" and (k != "SOV_A3" or v in ADM): ISO3.setdefault(v, a)
        for k in ("NAME", "NAME_LONG", "ADMIN", "NAME_EN", "FORMAL_EN", "NAME_SORT", "BRK_NAME", "GEOUNIT"):
            if p.get(k): CNAME.setdefault(fold(p[k]), a)
ISO3.setdefault("XKX", "KOS"); ISO2.setdefault("XK", "KOS")
for a, b in {"usa": "USA", "us": "USA", "u s": "USA", "united states": "USA", "uk": "GBR", "england": "GBR",
             "scotland": "GBR", "wales": "GBR", "northern ireland": "GBR", "south korea": "KOR", "korea": "KOR",
             "republic of korea": "KOR", "korea south": "KOR", "north korea": "PRK", "russia": "RUS",
             "russian federation": "RUS", "czech republic": "CZE", "czechia": "CZE", "taiwan": "TWN",
             "republic of china": "TWN", "china": "CHN", "prc": "CHN", "people s republic of china": "CHN",
             "mainland china": "CHN", "hong kong": "HKG", "macau": "MAC", "holland": "NLD", "the netherlands": "NLD",
             "netherlands": "NLD", "ivory coast": "CIV", "cote d ivoire": "CIV", "vietnam": "VNM", "viet nam": "VNM",
             "laos": "LAO", "iran": "IRN", "syria": "SYR", "bolivia": "BOL", "venezuela": "VEN", "tanzania": "TZA",
             "turkey": "TUR", "turkiye": "TUR", "uae": "ARE", "united arab emirates": "ARE", "singapore": "SGP",
             "malaysia": "MYS", "philippines": "PHL", "the philippines": "PHL", "israel": "ISR",
             "puerto rico": "PRI", "scotland uk": "GBR", "macedonia": "MKD", "north macedonia": "MKD",
             "bosnia and herzegovina": "BIH", "dr congo": "COD", "democratic republic of the congo": "COD",
             "gabon": "GAB", "eswatini": "SWZ", "swaziland": "SWZ"}.items():
    CNAME[a] = b
CNAME_ADM = {}  # fold(country name) restricted to valid codes
def country_of(text):
    t = fold(text)
    if t in CNAME: return CNAME[t]
    for k in sorted(CNAME, key=len, reverse=True):  # "United States, Arizona" etc.
        if len(k) > 3 and re.search(rf"\b{re.escape(k)}\b", t): return CNAME[k]
    return None

# ---------- place lookup (labels.json cities, NE populated places, country points) ----------
_labels = json.load(open(f"{OUT}/labels.json", encoding="utf-8"))
CITY = defaultdict(list)    # (iso3, fold(name)) -> [(lon, lat, pop)]
CITY_ANY = defaultdict(list)
CPT = {}
for l in _labels:
    if l["t"] == "c" and l.get("k"): CPT[l["k"]] = (l["x"], l["y"])
    if l["t"] == "p":
        CITY[(l.get("k"), fold(l["n"]))].append((l["x"], l["y"], l.get("pop") or 0))
        CITY_ANY[fold(l["n"])].append((l["x"], l["y"], l.get("pop") or 0, l.get("k")))
for f in json.load(open(f"{NE}/ne_10m_populated_places_simple.geojson", encoding="utf-8"))["features"]:
    p = f["properties"]; k = ISO3.get(p.get("adm0_a3"), p.get("adm0_a3"))
    for nm in {p.get("name"), p.get("nameascii")} - {None}:
        key = (k, fold(nm))
        if key not in CITY:
            CITY[key].append((p["longitude"], p["latitude"], p.get("pop_max") or 0))
CBOX = {}
def _walk(c):
    if isinstance(c[0], (int, float)): yield c
    else:
        for x in c: yield from _walk(x)
for f in _ne:  # fallback country point = label point; bbox for sanity checks
    p = f["properties"]
    if p["ADM0_A3"] not in CPT and p.get("LABEL_X") is not None: CPT[p["ADM0_A3"]] = (p["LABEL_X"], p["LABEL_Y"])
    xs, ys = zip(*((c[0], c[1]) for c in _walk(f["geometry"]["coordinates"])))
    CBOX[p["ADM0_A3"]] = (min(xs), min(ys), max(xs), max(ys))

def in_country(cc, xy, pad=0.3):
    b = CBOX.get(cc)
    return bool(b) and b[0] - pad <= xy[0] <= b[2] + pad and b[1] - pad <= xy[1] <= b[3] + pad

CITY_ALIAS = {"hsinchu science park": "hsinchu", "tainan science park": "tainan", "southern taiwan science park": "tainan",
              "central taiwan science park": "taichung", "hwaseong": "hwaseong", "giheung": "yongin",
              "giheung yongin": "yongin", "kiheung": "yongin", "onyang": "asan", "tangjeong": "asan",
              "pyeongtaek": "pyeongtaek", "icheon": "icheon", "ichon": "icheon", "cheongju": "cheongju",
              "chongju": "cheongju", "gumi": "gumi", "kumi": "gumi", "wuxi": "wuxi", "dalian": "dalian",
              "chandler": "chandler", "hillsboro": "hillsboro", "rio rancho": "rio rancho",
              "kiryat gat": "qiryat gat", "leixlip": "leixlip", "malta": "malta", "east fishkill": "east fishkill",
              "boise": "boise", "manassas": "manassas", "lehi": "lehi", "dresden": "dresden",
              "crolles": "crolles", "rousset": "rousset", "catania": "catania", "kumamoto": "kumamoto",
              "kikuyo": "kumamoto", "yokkaichi": "yokkaichi", "kitakami": "kitakami", "hiroshima": "hiroshima",
              "higashihiroshima": "hiroshima", "taoyuan": "taoyuan", "linkou": "taoyuan", "houli": "taichung",
              "baoshan": "hsinchu", "zhunan": "miaoli", "zhubei": "hsinchu", "longtan": "taoyuan",
              "pudong": "shanghai", "jinqiao": "shanghai", "zhangjiang": "shanghai", "songjiang": "shanghai",
              "beijing economic technological development area": "beijing", "tianjin": "tianjin",
              "nanjing": "nanjing", "jiangning": "nanjing", "pukou": "nanjing", "xi an": "xi an", "xian": "xi an",
              "hefei": "hefei", "wuhan": "wuhan", "shenzhen": "shenzhen", "chengdu": "chengdu",
              "chongqing": "chongqing", "singapore": "singapore", "kulim": "kulim", "penang": "george town",
              "bayan lepas": "george town", "san antonio": "san antonio", "sherman": "sherman",
              "richardson": "richardson", "lehi utah": "lehi", "phoenix": "phoenix", "taylor": "taylor",
              "austin": "austin", "clay": "syracuse", "camillus": "syracuse", "new albany": "columbus",
              "licking county": "columbus", "magdeburg": "magdeburg", "villach": "villach",
              "regensburg": "regensburg", "reutlingen": "reutlingen", "nijmegen": "nijmegen",
              "hamburg": "hamburg", "itzehoe": "itzehoe", "agrate brianza": "monza", "migdal haemek": "haifa",
              "naka": "hitachinaka", "hitachinaka": "hitachinaka", "nishiwaki": "nishiwaki", "yokkaichi kitakami": "yokkaichi"}

def city_point(cc, parts):
    """Best city point for location text parts (in the order to try). Returns (lon, lat) or None."""
    cands = []
    for p in parts:
        f = fold(re.sub(r"\(.*?\)", "", p))
        if not f: continue
        keys = [f, CITY_ALIAS.get(f)]
        keys += [w for w in [f.replace(" city", ""), f.replace(" county", ""), f.split(" ")[0] if len(f.split(" ")[0]) > 3 else None]]
        for k in keys:
            if not k: continue
            if (cc, k) in CITY:
                c = max(CITY[(cc, k)], key=lambda t: t[2]); return (c[0], c[1])
        cands.append(f)
    return None

def place(cc, loc_text, city_first=False):
    """Point for "Country, Region, City" text (or "City, Region" with city_first). Returns (xy, "city"|"country")."""
    parts = [p.strip() for p in re.split(r"[,/;]| - ", loc_text or "") if p.strip()]
    if not city_first: parts = parts[::-1]
    if cc:
        c = city_point(cc, [p for p in parts if country_of(p) != cc or len(parts) == 1])
        if c: return c, "city"
        if cc in CPT: return CPT[cc], "country"
    return None, None

# ---------- HTML tables ----------
class Tables(HTMLParser):
    """Collect top-level tables as rows of cells {text, links, rs, cs}. Skips refs, styles, hidden spans."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.depth, self.skip, self.cell, self.row = [], 0, [], None, None
        self.tags = []
    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag in ("br", "img", "hr", "meta", "link", "wbr", "input"):
            if tag == "br" and self.cell is not None and not self.skip: self.cell["text"] += " , "
            return
        self.tags.append(tag)
        cls, sty = a.get("class") or "", (a.get("style") or "").replace(" ", "")
        if self.skip or tag in ("style", "script") or (tag == "sup" and "reference" in cls) \
                or "display:none" in sty or "sortkey" in cls or "mw-ref" in cls:
            self.skip.append(tag); return
        if tag == "table":
            self.depth += 1
            if self.depth == 1: self.tables.append({"attrs": a, "rows": [], "pos": self.getpos()})
        if self.depth != 1: return
        if tag == "tr": self.row = []; self.tables[-1]["rows"].append(self.row)
        elif tag in ("td", "th") and self.row is not None:
            def iv(v):
                try: return max(1, int(re.sub(r"\D", "", v or "1") or 1))
                except ValueError: return 1
            self.cell = {"text": "", "links": [], "rs": iv(a.get("rowspan")), "cs": iv(a.get("colspan")), "th": tag == "th"}
            self.row.append(self.cell)
        elif tag == "a" and self.cell is not None and a.get("href", "").startswith("/wiki/") and "new" not in cls:
            self.cell["links"].append(urllib.parse.unquote(a["href"][6:].split("#")[0]).replace("_", " "))
    def handle_endtag(self, tag):
        if tag in ("br", "img", "hr", "meta", "link", "wbr", "input"): return
        while self.tags:
            t = self.tags.pop()
            if self.skip and self.skip[-1] == t: self.skip.pop()
            elif not self.skip:
                if t == "table": self.depth -= 1
                if self.depth == 1 or t == "table":
                    if t in ("td", "th"): self.cell = None
                    elif t == "tr": self.row = None
            if t == tag: break
    def handle_data(self, d):
        if self.cell is not None and not self.skip and self.depth == 1: self.cell["text"] += d

def clean(s):
    s = re.sub(r"\[\s*(\d+|[a-z]|citation needed|note \d+)\s*\]", "", s or "")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*Archived \d{4}-\d\d-\d\d at the Wayback Machine", "", s)
    s = re.sub(r"\s*,\s*(?:,\s*)+", ", ", s)    # empty parts left by <br>
    s = re.sub(r"\s+,", ",", s)
    s = re.sub(r",(?=[^\s\d])", ", ", s)        # keep 40,000 intact
    return s.strip(" ,")

def grid(table):
    """Expand rowspan/colspan into a list of rows of cells (cells shared across spans)."""
    out, pend = [], {}
    for row in table["rows"]:
        r, col, it = [], 0, iter(row)
        cells = list(row)
        i = 0
        while i < len(cells) or col in pend:
            if col in pend:
                c, left = pend[col]
                r.append(c)
                if left > 1: pend[col] = (c, left - 1)
                else: del pend[col]
                col += 1; continue
            c = cells[i]; i += 1
            for _ in range(c["cs"]):
                r.append(c)
                if c["rs"] > 1: pend[col] = (c, c["rs"] - 1)
                col += 1
        out.append(r)
    return out

def wiki_parse(page, fname):
    url = f"https://en.wikipedia.org/w/api.php?action=parse&page={urllib.parse.quote(page)}&prop=text&format=json"
    b = cached(fname, url)
    return json.loads(b)["parse"]["text"]["*"]

# =====================================================================
# 1. Semiconductor fabs
# =====================================================================
OPERATORS = [  # (short name, regex on the company text, OSM regex or None)
    ("TSMC", r"TSMC|Taiwan Semiconductor Manufacturing|台積|台积", r"TSMC|台積|台积|Taiwan Semiconductor"),
    ("ESMC", r"\bESMC\b|European Semiconductor Manufacturing", None),
    ("JASM", r"\bJASM\b|Japan Advanced Semiconductor Manufacturing", r"JASM"),
    ("Samsung", r"Samsung", r"Samsung|삼성"),
    ("SK hynix", r"SK ?Hynix|Hynix", r"SK ?hynix|하이닉스|海力士"),
    ("Intel", r"\bIntel\b", r"^Intel\b|\bIntel (Corp|Fab|Ronler|Ocotillo|Rio|Aloha|Hawthorn|Jones|D1)|英特尔"),
    ("Micron", r"Micron", r"Micron|美光"),
    ("UMC", r"\bUMC\b|United Microelectronics|聯華", r"\bUMC\b|聯華|联华|United Microelectronics"),
    ("GlobalFoundries", r"GlobalFoundries|Global Foundries|GF\b", r"GlobalFoundries|Global ?Foundries"),
    ("SMIC", r"\bSMIC\b|Semiconductor Manufacturing International|中芯", r"SMIC|中芯"),
    ("Kioxia", r"Kioxia|Toshiba Memory|Flash Alliance|Flash Partners|Flash Forward", r"Kioxia|キオクシア"),
    ("Western Digital", r"Western Digital|SanDisk", None),
    ("Texas Instruments", r"Texas Instruments|\bTI\b", r"Texas Instruments"),
    ("Infineon", r"Infineon", r"Infineon"),
    ("STMicroelectronics", r"STMicro|\bST\b", r"STMicro|ST ?Microelectronics"),
    ("NXP", r"\bNXP\b", r"NXP"),
    ("Hua Hong", r"Hua ?Hong|华虹|華虹|Grace Semiconductor|HHGrace", r"Hua ?Hong|华虹|華虹"),
    ("CXMT", r"CXMT|ChangXin|长鑫", r"CXMT|长鑫|ChangXin"),
    ("YMTC", r"YMTC|Yangtze Memory|长江存储", r"YMTC|长江存储|Yangtze Memory"),
    ("Renesas", r"Renesas", r"Renesas|ルネサス"),
    ("Sony", r"\bSony\b", r"\bSony\b|ソニー"),
    ("Bosch", r"\bBosch\b", r"\bBosch\b"),
    ("onsemi", r"\bon ?semi|ON Semiconductor", r"onsemi|ON Semiconductor"),
    ("Analog Devices", r"Analog Devices|Linear Technology|Maxim Integrated", r"Analog Devices"),
    ("Microchip", r"Microchip|Atmel", r"Microchip"),
    ("Tower", r"Tower ?Semiconductor|TowerJazz|\bTower\b", r"Tower ?Semiconductor|TowerJazz"),
    ("Vanguard", r"Vanguard International|\bVIS\b|VSMC", r"Vanguard International|世界先進"),
    ("PSMC", r"\bPSMC\b|Powerchip|力積電", r"Powerchip|力積電|PSMC"),
    ("Nanya", r"Nanya|Nan Ya Tech|南亞科", r"Nanya|南亞科"),
    ("Winbond", r"Winbond|華邦", r"Winbond|華邦"),
    ("Rohm", r"\bRohm\b|ROHM|Lapis", r"ROHM|ローム"),
    ("Wolfspeed", r"Wolfspeed|\bCree\b", r"Wolfspeed"),
    ("Macronix", r"Macronix|旺宏", None),
    ("Fujitsu", r"Fujitsu", None), ("Mitsubishi Electric", r"Mitsubishi", None), ("Toshiba", r"Toshiba", None),
    ("X-FAB", r"X-?FAB", None), ("SkyWater", r"SkyWater", None), ("Polar", r"Polar Semiconductor", None),
    ("Nexperia", r"Nexperia", None), ("DB HiTek", r"DB ?HiTek|Dongbu", None), ("Magnachip", r"Magnachip", None),
    ("Silterra", r"SilTerra", None), ("Episil", r"Episil", None), ("Ams Osram", r"\bams\b|Osram", None),
    ("Qorvo", r"Qorvo|TriQuint|RF Micro", None), ("Skyworks", r"Skyworks", None), ("Broadcom", r"Broadcom|Avago", None),
    ("Hitachi", r"Hitachi", None), ("Panasonic", r"Panasonic|Nuvoton", None), ("Seiko Epson", r"Epson", None),
    ("Nichia", r"Nichia", None), ("Lfoundry", r"LFoundry", None), ("Elmos", r"Elmos", None),
]
OPS = [(n, re.compile(r, re.I), re.compile(o, re.I) if o else None) for n, r, o in OPERATORS]

def op_short(company):
    c = clean(company)
    for n, rx, _ in OPS:
        if rx.search(c): return n
    c = re.sub(r"\s*\(.*?\)", "", c).split(",")[0].strip()
    c = re.sub(r"\b(Co\.?,?|Ltd\.?|Inc\.?|Corp(oration)?\.?|Limited|GmbH|AG|S\.?A\.?|LLC|plc|Company)\b", "", c).strip(" ,.–-")
    return c[:40] or None

def numbers(s):
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s or "")
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]

SUSPECT_NM = []

def parse_nm(s, who=""):
    """Smallest node in nm. µm/um/micron values (anywhere in the cell, if no "nm") are converted to nm."""
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", clean(s)).replace("µ", "u").replace("μ", "u")
    if not s: return None
    micro = bool(re.search(r"\d\s*(um|microns?|micrometres?|micrometers?)\b", s, re.I))
    has_nm = bool(re.search(r"\d\s*nm\b", s, re.I))
    v = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(um|microns?|micrometres?|micrometers?|nm)?", s, re.I):
        x, u = float(m.group(1)), (m.group(2) or "").lower()
        if u and u != "nm" or (micro and not has_nm and not u and x < 20): x *= 1000
        elif x < 1.4 and u != "nm":
            SUSPECT_NM.append(f"{who}: '{s}' -> {x} read as µm"); x *= 1000
        if 1 <= x <= 20000: v.append(x)
    if not v: return None
    x = min(v); x = round(x, 1); return int(x) if x == int(x) else x

def parse_w(s):
    v = [int(x) for x in numbers(clean(s)) if 25 <= x <= 450]
    return max(v) if v else None

def parse_cap(s):
    s = clean(s)
    if not s or re.search(r"^\s*(n/?a|unknown|\?)\s*$", s, re.I): return None
    s = re.sub(r"(\d+(?:\.\d+)?)\s*[kK]\b", lambda m: str(float(m.group(1)) * 1000), s)
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)
    s = re.sub(r"\(.*?\)", "", s)
    # ranges a–b -> midpoint; "+" or "and" -> sum of parts
    tot, any_ = 0.0, False
    for part in re.split(r"\+| and |;", s):
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:–|-|to|~)\s*(\d+(?:\.\d+)?)", part)
        if m: tot += (float(m.group(1)) + float(m.group(2))) / 2; any_ = True; continue
        n = [x for x in numbers(part) if x >= 10]
        if n: tot += n[0]; any_ = True
    return round(tot) if any_ and tot >= 10 else None

def parse_year(s):
    m = re.search(r"\b(19[5-9]\d|20[0-4]\d)\b", clean(s))
    return int(m.group(1)) if m else None

RX_CONS = re.compile(r"under construction|construction|being built|building|ramping|groundbreak|broke ground|fitting out", re.I)
RX_PLAN = re.compile(r"\bplanned\b|\bplan(s)? to\b|announced|proposed|expected|future|will (start|begin|be)|approved|scheduled|TBD|postponed|delayed|on hold|cancel", re.I)

def status(*texts, year=None):
    # notes in brackets usually describe another phase ("2020 (P7 under construction)")
    t = " ".join(re.sub(r"\(.*?\)", " ", clean(x)) for x in texts if x)
    if RX_CONS.search(t): return "construction"
    if RX_PLAN.search(t): return "planned"
    if year and year > datetime.now().year: return "planned"
    return "operating"

PHASE = re.compile(r"(?:[\s,;(-]*\b(?:P|Ph|Phase)\s*\d+[A-Z]?(?:\s*(?:[-–&,/]|and|to)\s*(?:P|Ph|Phase)?\s*\d+[A-Z]?)*\)?)+\s*$", re.I)

def base_name(n):
    b = PHASE.sub("", n).strip(" ,;-–")
    return b or n

OSM_RX = ("TSMC|台積|台积|Samsung|삼성|Intel|Micron|SK ?hynix|하이닉스|UMC|聯華|GlobalFoundries|SMIC|中芯|Kioxia|キオクシア|"
          "Texas Instruments|Infineon|STMicro|NXP|Hua ?Hong|华虹|CXMT|长鑫|YMTC|长江存储|Renesas|Sony|Bosch|onsemi|"
          "ON Semiconductor|Analog Devices|Microchip|Tower Semiconductor|TowerJazz|Vanguard International|世界先進|PSMC|"
          "Powerchip|力積電|Nanya|南亞科|Winbond|華邦|Rohm|Wolfspeed|JASM")

def overpass(q, fname, label):
    """Run an Overpass query, cached as raw/domains/<fname>; one retry, then fall back to the cache."""
    p, d = (f"{RAW}/{fname}" if fname else None), None
    if not OFFLINE:
        for k in range(2):
            try:
                print("fetch overpass", f"({label})")
                b, _ = http("https://overpass-api.de/api/interpreter", data={"data": q}, timeout=330, tries=1)
                d = json.loads(b)
                if "remark" in d and "runtime error" in d["remark"]: raise RuntimeError(d["remark"][:200])
                if p:
                    open(p, "wb").write(b)
                    logfetch(fname, "https://overpass-api.de/api/interpreter")
                break
            except Exception as e:
                print(f"  overpass failed: {e}")
                if k == 0: time.sleep(30)
    if d is None and p and os.path.exists(p):
        print(f"  using cached {fname}"); d = json.load(open(p))
    return d

def osm_near(boxes, fname="osm_fabs.json", chunk=12):
    """OSM works/industrial areas named like a chip maker within ~30 km boxes around town points.
    Cached as raw/domains/<fname>; a failed chunk falls back to the cached elements."""
    p = f"{RAW}/{fname}"
    old = json.load(open(p)) if os.path.exists(p) else None
    if OFFLINE: return old
    els, failed = {}, 0
    for i in range(0, len(boxes), chunk):
        bb = ["%.3f,%.3f,%.3f,%.3f" % (y - 0.3, x - 0.35, y + 0.3, x + 0.35) for x, y in boxes[i:i + chunk]]
        body = "".join(f'nwr["{k}"="{v}"]["{t}"~"{OSM_RX}",i]({b});' for b in bb
                       for k, v in (("man_made", "works"), ("landuse", "industrial"), ("building", "industrial"))
                       for t in ("name", "operator"))
        d = overpass(f"[out:json][timeout:180];({body});out center tags;", None, f"fab operators, boxes {i + 1}-{i + len(bb)}")
        if d is None: failed += 1; continue
        for e in d.get("elements", []): els[e["type"] + str(e["id"])] = e
        time.sleep(2)
    if failed and old:
        for e in old.get("elements", []): els.setdefault(e["type"] + str(e["id"]), e)
    if not els: return old
    d = {"elements": list(els.values())}
    json.dump(d, open(p, "w"))
    logfetch(fname, "https://overpass-api.de/api/interpreter")
    print(f"  osm: {len(els)} elements from {len(boxes)} boxes ({failed} chunks failed)")
    return d

NODE_OVERRIDES = [  # (operator, regex on fold(name + location), ISO3, smallest node nm, status or None, source)
    ("TSMC", r"\bfab 12\b", "TWN", 3, "operating", "https://www.tsmc.com/english/dedicatedFoundry/manufacturing/fab_capacity"),
    ("TSMC", r"\bfab 15\b", "TWN", 7, "operating", "https://www.tsmc.com/english/dedicatedFoundry/manufacturing/fab_capacity"),
    ("TSMC", r"\bfab 18\b", "TWN", 3, "operating", "https://en.wikipedia.org/wiki/TSMC#Facilities"),
    ("TSMC", r"\bfab 20\b", "TWN", 2, "operating", "https://pr.tsmc.com/english/news/3204"),
    ("TSMC", r"\bfab 21\b|arizona", "USA", 4, "operating", "https://en.wikipedia.org/wiki/TSMC_Arizona"),
    ("Samsung", r"\bs3\b", "KOR", 3, "operating", "https://semiconductor.samsung.com/foundry/"),
    ("Samsung", r"pyeongtaek", "KOR", 12, None, "https://news.samsung.com/global/samsung-electronics-begins-mass-production-at-new-ev-line-in-pyeongtaek"),
    ("Intel", r"\bfab 34\b", "IRL", 7, "operating", "https://www.intel.com/content/www/us/en/newsroom/news/intel-4-high-volume-manufacturing-ireland.html"),
    ("Intel", r"\bd1x\b", "USA", 2, "operating", "https://en.wikipedia.org/wiki/Intel_18A"),
    ("SK hynix", r"\bm16\b", "KOR", 12, None, "https://news.skhynix.com/sk-hynix-completes-m16/"),
]

TITLE_ALIAS = {"Chunan Science Park": "Zhunan", "Chunan": "Zhunan", "Eumsung-Kun": "Eumseong County",
               "Kulim Hi-Tech Park": "Kulim", "Atsugi Operation Base": "Atsugi", "Miyasaki": "Miyazaki (city)",
               "Kunamoto": "Kumamoto", "Pan-Yu": "Panyu District", "Xian": "Xi'an", "Hsin-Chu Science Park": "Hsinchu Science Park",
               "Miao-Li": "Miaoli", "Longtan Science Park": "Longtan District", "Eumsung": "Eumseong County"}

# Hand placements: site coordinates for big sites the city/OSM steps place badly, and missing fabs.
# key: (operator, fold(base plant name) or a regex on it, ISO3) -> (lon, lat)
OVERRIDES = [
    ("TSMC", r"\bfab 12\b", "TWN", (121.0040, 24.7740)),       # Hsinchu Science Park
    ("TSMC", r"\bfab 20\b", "TWN", (121.0290, 24.7680)),       # Baoshan, Hsinchu County
    ("TSMC", r"\bfab 14\b", "TWN", (120.2780, 23.1100)),       # Southern Taiwan Science Park
    ("TSMC", r"\bfab 18\b", "TWN", (120.2880, 23.1230)),       # STSP
    ("TSMC", r"\bfab 15\b", "TWN", (120.6150, 24.2110)),       # Central Taiwan Science Park
    ("TSMC", r"\bfab 25\b", "TWN", (120.6150, 24.2110)),
    ("TSMC", r"\bfab 22\b", "TWN", (120.2960, 22.7290)),       # Nanzi, Kaohsiung
    ("TSMC", r"\bfab 21\b|arizona", "USA", (-112.1430, 33.7530)),  # north Phoenix
    ("TSMC", r"\bfab 16\b", "CHN", (118.6420, 32.1240)),       # Pukou, Nanjing
    ("TSMC", r"\bfab 10\b", "CHN", (121.2080, 31.0170)),       # Songjiang, Shanghai
    ("JASM", r".", "JPN", (130.8370, 32.8810)),                # Kikuyo, Kumamoto
    ("TSMC", r"\bfab 23\b|kumamoto|jasm", "JPN", (130.8370, 32.8810)),
    ("ESMC", r".", "DEU", (13.7220, 51.1270)),                 # Dresden-Wilschdorf
    ("Samsung", r"pyeongtaek", "KOR", (127.0460, 37.0230)),
    ("Samsung", r"hwaseong", "KOR", (127.0590, 37.2060)),
    ("Samsung", r"giheung|kiheung|yongin", "KOR", (127.0720, 37.2300)),
    ("Samsung", r"taylor", "USA", (-97.4400, 30.5400)),
    ("Samsung", r"austin", "USA", (-97.6190, 30.3890)),
    ("Samsung", r"xi ?an", "CHN", (108.7760, 34.1960)),
    ("Intel", r"ronler|hillsboro|d1", "USA", (-122.9620, 45.5410)),
    ("Intel", r"ocotillo|chandler|fab 42|fab 52|fab 62|fab 32|fab 12", "USA", (-111.8880, 33.2700)),
    ("Intel", r"rio rancho|fab 11|fab 9", "USA", (-106.6390, 35.2420)),
    ("Intel", r"leixlip|ireland|fab 24|fab 34", "IRL", (-6.5130, 53.3660)),
    ("Intel", r"ohio|new albany|licking", "USA", (-82.7280, 40.0910)),
    ("SK hynix", r"icheon|m14|m16|m10", "KOR", (127.4840, 37.2540)),
    ("SK hynix", r"cheongju|m11|m12|m15", "KOR", (127.4310, 36.6400)),
    ("SK hynix", r"wuxi", "CHN", (120.4100, 31.5300)),
    ("Micron", r"boise|id1", "USA", (-116.1480, 43.5270)),
    ("Micron", r"clay|new york", "USA", (-76.1850, 43.1850)),
    ("Micron", r"manassas", "USA", (-77.5240, 38.7590)),
    ("Micron", r"hiroshima", "JPN", (132.7120, 34.4130)),
    ("Micron", r"taichung|houli", "TWN", (120.6110, 24.2140)),
    ("Micron", r"taoyuan|linkou", "TWN", (121.3640, 25.0730)),
]
ADD_FABS = [  # missing from the Wikipedia list (or listed under another name); added unless already present
    {"o": "TSMC", "n": "Fab 16", "cc": "CHN", "loc": "China, Nanjing", "s": "operating", "w": 300, "nm": 12, "y": 2018,
     "cap": 20000, "prod": "Foundry"},
    {"o": "ESMC", "n": "ESMC Dresden", "cc": "DEU", "loc": "Germany, Dresden", "s": "construction", "w": 300, "nm": 12,
     "y": 2027, "cap": 40000, "prod": "Foundry (TSMC, Bosch, Infineon, NXP joint venture)"},
    {"o": "TSMC", "n": "Fab 22", "cc": "TWN", "loc": "Taiwan, Kaohsiung", "s": "construction", "w": 300, "nm": 2,
     "prod": "Foundry"},
    {"o": "TSMC", "n": "Fab 20", "cc": "TWN", "loc": "Taiwan, Hsinchu, Baoshan", "s": "operating", "w": 300, "nm": 2,
     "y": 2025, "prod": "Foundry (N2)", "dupe": r"\bfab 20\b"},
    {"o": "Intel", "n": "Fab 34", "cc": "IRL", "loc": "Ireland, County Kildare, Leixlip", "s": "operating", "w": 300,
     "nm": 7, "y": 2023, "prod": "Intel 4 / Intel 3 logic", "dupe": r"\bfab 34\b"},
    {"o": "Samsung", "n": "Taylor", "cc": "USA", "loc": "United States, Texas, Taylor", "s": "construction", "w": 300,
     "nm": 2, "prod": "Foundry", "dupe": r"taylor"},
    {"o": "Micron", "n": "Clay (New York)", "cc": "USA", "loc": "United States, New York, Clay", "s": "construction",
     "w": 300, "prod": "DRAM"},
    {"o": "Micron", "n": "ID1 Boise", "cc": "USA", "loc": "United States, Idaho, Boise", "s": "construction", "w": 300,
     "prod": "DRAM"},
]

def fabs():
    html = wiki_parse("List_of_semiconductor_fabrication_plants", "wiki_fabs.json")
    tp = Tables(); tp.feed(html)
    tab = next((t for t in tp.tables if t["attrs"].get("id") == "opensemifabs"), None)
    if tab is None:  # fall back to the first wikitable before the "Closed plants" heading
        cut = html.find('id="Closed_plants"')
        tab = next(t for t in tp.tables if "wikitable" in (t["attrs"].get("class") or ""))
    rows = grid(tab)
    head = [clean(c["text"]).lower() for c in rows[0]]
    def col(*keys):
        for i, h in enumerate(head):
            if any(k in h for k in keys): return i
        return None
    C = dict(co=col("company"), pn=col("plant name"), loc=col("location"), cost=col("cost"), sp=col("started"),
             w=col("wafer"), nm=col("node"), cap=col("capacity"), tech=col("technology /", "products"))
    print("fabs: columns", C, "rows", len(rows) - 1)
    recs, bad = [], []
    for r in rows[1:]:
        if all(c["th"] for c in r) or len(r) < len(head): bad.append(" | ".join(clean(c["text"]) for c in r)[:120]); continue
        g = lambda k: clean(r[C[k]]["text"]) if C[k] is not None and C[k] < len(r) else ""
        company, loc = g("co"), g("loc")
        if not company: bad.append(" | ".join(clean(c["text"]) for c in r)[:120]); continue
        cc = country_of(loc.split(",")[0]) or country_of(loc)
        if not cc: bad.append(f"no country: {company} | {loc}"); continue
        o = op_short(company)
        name = g("pn") or (loc.split(",")[-1].strip() if loc else "")
        y = parse_year(g("sp"))
        links = [l for l in r[C["loc"]]["links"] if not CNAME.get(fold(l))]
        recs.append({"o": o, "n": name, "cc": cc, "loc": loc, "links": links, "nm": parse_nm(g("nm"), f"{company} {g('pn')}"), "w": parse_w(g("w")),
                     "cap": parse_cap(g("cap")), "y": y, "prod": g("tech"),
                     "s": status(g("tech"), g("sp"), g("cap"), year=y), "company": company})
    print(f"fabs: parsed {len(recs)} rows, skipped {len(bad)}")
    for b in bad[:15]: print("   skip:", b)
    # collapse phases of one fab: same operator, base name and location
    groups = {}
    for r in recs:
        k = (r["o"], fold(base_name(r["n"])), fold(r["loc"]))
        groups.setdefault(k, []).append(r)
    sites = []
    rank = {"operating": 0, "construction": 1, "planned": 2}
    for (o, bn, _), rs in groups.items():
        s = dict(rs[0]); s["n"] = base_name(rs[0]["n"]) if len(rs) > 1 else rs[0]["n"]
        caps = [r["cap"] for r in rs if r["cap"] and PHASE.search(r["n"])]
        whole = [r["cap"] for r in rs if r["cap"] and not PHASE.search(r["n"])]  # a "whole fab" row, if any
        s["cap"] = max(sum(caps), max(whole, default=0)) or None
        # smallest node over all phases, but over running phases only when they have one: a planned
        # phase's node (Fab 21 P3 "1.6 & 2") should not be shown as what the site makes today
        nms = [r["nm"] for r in rs if r["nm"] and r["s"] == "operating"] or [r["nm"] for r in rs if r["nm"]]
        s["nm"] = min(nms) if nms else None
        ws = [r["w"] for r in rs if r["w"]]; s["w"] = max(ws) if ws else None
        ys = [r["y"] for r in rs if r["y"]]; s["y"] = min(ys) if ys else None
        s["s"] = min((r["s"] for r in rs), key=rank.get)
        s["phases"] = len(rs)
        sites.append(s)
    for s in sites:  # leading-edge nodes and status the list misses (NODE_OVERRIDES)
        nf = fold(s["n"] + " " + s["loc"])
        for o, rx, cc, nm, st, _url in NODE_OVERRIDES:
            if o == s["o"] and cc == s["cc"] and re.search(rx, nf):
                if not s.get("nm") or s["nm"] > nm: s["nm"] = nm
                if st: s["s"] = st
                break
    for x in SUSPECT_NM: print("   suspicious node (<1.4 without nm):", x)
    print(f"fabs: {len(recs)} rows -> {len(sites)} sites after collapsing phases ({sum(1 for s in sites if s['phases'] > 1)} multi-phase)")
    for a in ADD_FABS:
        nb = fold(a["n"])
        dup = [s for s in sites if s["o"] == a["o"] and s["cc"] == a["cc"] and
               (nb == fold(s["n"]) or a.get("dupe") and re.search(a["dupe"], fold(s["n"] + " " + s["loc"])))]
        if dup: print(f"   hand fab already listed: {a['o']} {a['n']} -> {dup[0]['n']} ({dup[0]['loc']})"); continue
        sites.append(dict({k: v for k, v in a.items() if k != "dupe"}, links=[], added=True))
    # --- town point: linked place article (Wikipedia coordinates), city labels, then a guessed article title ---
    def guesses(s):
        parts = [p.strip() for p in s["loc"].split(",") if p.strip() and not CNAME.get(fold(p))]
        parts = [p for p in parts if not re.search(r"\d{3}", p)]
        g = []
        for i in range(len(parts) - 1, -1, -1):
            if i > 0: g.append(f"{parts[i]}, {parts[i - 1]}")
            g.append(parts[i])
        # the plant name often holds the town ("Oita", "Y5 (at Yokkaichi Operations)", "Shiga Factory")
        nm = re.sub(r"\b(Plant|Factory|Site|Works|Campus|Operations|Technology Center|Building|Fab|Line|Phase|"
                    r"Power Device|Semiconductor|Inc|Co|Ltd|at)\b\.?", " ", s["n"])
        nparts = [re.sub(r"\s+", " ", p).strip(" .-#") for p in re.split(r"[,()/]", nm)]
        g += [p for p in nparts if len(p) > 3 and not re.search(r"\d", p)]
        out = []
        for t in g:
            t = TITLE_ALIAS.get(t, t)
            for v in (t, re.sub(r"(-shi|-cho|-machi|-gun|-si|-kun| City)$", "", t), re.sub(r"(\w)-(\w)", lambda m: m.group(1) + m.group(2).lower(), t)):
                if v and v not in out: out.append(v)
            if s["cc"] == "JPN" and not t.endswith("Prefecture"): out.append(t + " Prefecture")
        return out
    coords = wiki_coords([t for s in sites for t in s["links"] + guesses(s)], "fabs")
    for s in sites: s["guess"] = guesses(s)
    for s in sites:
        s["cxy"], s["cgeo"] = None, None
        for t in reversed(s["links"]):
            if t in coords and in_country(s["cc"], coords[t]): s["cxy"], s["cgeo"] = coords[t], "city"; break
        if not s["cxy"]:
            xy, g = place(s["cc"], s["loc"])
            if g == "city": s["cxy"], s["cgeo"] = xy, g
        if not s["cxy"]:
            xy = city_point(s["cc"], s["guess"])
            if xy: s["cxy"], s["cgeo"] = xy, "city"
        if not s["cxy"]:
            for t in s["guess"]:
                if t in coords and in_country(s["cc"], coords[t]): s["cxy"], s["cgeo"] = coords[t], "city"; break
        if not s["cxy"] and s["cc"] in CPT: s["cxy"], s["cgeo"] = CPT[s["cc"]], "country"
    # --- OSM works/industrial areas named like the operator, near each town (bbox chunks keep Overpass fast) ---
    boxes = sorted({(round(s["cxy"][0] * 2) / 2, round(s["cxy"][1] * 2) / 2) for s in sites
                    if s["cgeo"] == "city" and any(n == s["o"] and orx for n, _, orx in OPS)})
    osm = osm_near(boxes)
    cands = defaultdict(list)
    for e in (osm or {}).get("elements", []):
        c = e.get("center") or ({"lat": e["lat"], "lon": e["lon"]} if "lat" in e else None)
        if not c: continue
        tg = e.get("tags", {})
        txt = " ".join(tg.get(k, "") for k in ("name", "name:en", "operator", "brand", "official_name", "name:zh", "name:ko", "name:ja"))
        for n, _, orx in OPS:
            if orx and orx.search(txt):
                cands[n].append(((c["lon"], c["lat"]), fold(" ".join(tg.get(k, "") for k in ("name", "name:en", "ref"))),
                                 e["type"][0] + str(e["id"])))
                break
    print("fabs: OSM candidates", sum(map(len, cands.values())), dict(Counter({k: len(v) for k, v in cands.items()}).most_common(12)))
    feats, how = [], Counter()
    for s in sites:
        xy, geo = None, None
        nmfold = fold(s["n"] + " " + s["loc"])
        for o, rx, cc, c in OVERRIDES:
            if o == s["o"] and cc == s["cc"] and re.search(rx, nmfold):
                xy, geo = c, "hand"; break
        cxy, cgeo = s["cxy"], s["cgeo"]
        if not xy and cands.get(s["o"]) and cgeo == "city":
            near = [(dist_km(cxy, q[0]), q) for q in cands[s["o"]]]
            near = [t for t in near if t[0] <= 30]
            if near:
                fabno = re.search(r"\bfab\s*(\d+[a-z]?)\b", fold(s["n"]))
                tok = [t for t in near if fabno and re.search(rf"\b(fab\s*)?{fabno.group(1)}\b", t[1][1])]
                best = min(tok or near, key=lambda t: t[0])
                xy, geo = best[1][0], "osm"
        if not xy and cxy: xy, geo = cxy, cgeo
        if not xy: print("   unplaced:", s["o"], s["n"], s["loc"]); continue
        how[geo] += 1
        cap300 = round(s["cap"] * (s["w"] / 300) ** 2) if s.get("cap") and s.get("w") else None
        pid = slug(f"{s['o']} {s['n']}")
        feats.append(pt(xy[0], xy[1], {"id": pid, "n": s["n"], "o": s["o"], "cc": s["cc"], "loc": s["loc"],
                                       "nm": s.get("nm"), "w": s.get("w"), "cap": s.get("cap"), "cap300": cap300,
                                       "s": s["s"], "y": s.get("y"), "prod": (s.get("prod") or "")[:80].strip(),
                                       "geo": "city" if geo == "hand" else geo}))
    print("fabs placed by:", dict(how), "(hand = OVERRIDES, written as geo=city)")
    feats.sort(key=lambda f: -(f["properties"].get("cap300") or 0))
    uniq_ids(feats)
    dump_geo("fabs", feats)
    src("fabs", "wiki_fabs.json", "CC BY-SA 4.0",
        url="https://en.wikipedia.org/wiki/List_of_semiconductor_fabrication_plants", count=len(feats))
    if osm: src("fabs_osm", "osm_fabs.json", "ODbL 1.0, © OpenStreetMap contributors", count=how["osm"])

# =====================================================================
# 2. Chip supply-chain chokepoints (hand list)
# =====================================================================
CHIP_SITES = [
    # (name, operator, cat, ISO3, lon, lat, why, url)
    ("ASML Veldhoven", "ASML", "litho", "NLD", 5.4060, 51.4070,
     "Only maker of EUV lithography machines, needed for every chip below 7 nm.", "https://en.wikipedia.org/wiki/ASML_Holding"),
    ("Zeiss SMT Oberkochen", "Carl Zeiss SMT", "litho", "DEU", 10.1050, 48.7830,
     "Sole supplier of the mirrors and optics inside ASML's EUV and DUV scanners.", "https://en.wikipedia.org/wiki/Carl_Zeiss_SMT"),
    ("Trumpf Ditzingen", "Trumpf", "litho", "DEU", 9.0590, 48.8270,
     "Builds the CO2 drive lasers that make the tin plasma in every EUV light source.", "https://en.wikipedia.org/wiki/Trumpf"),
    ("Cymer San Diego", "Cymer (ASML)", "litho", "USA", -117.0790, 33.0190,
     "Makes the EUV light sources and most of the DUV excimer lasers used in lithography.", "https://en.wikipedia.org/wiki/Cymer"),
    ("Shin-Etsu Handotai Shirakawa", "Shin-Etsu Handotai", "wafer", "JPN", 140.1900, 37.1300,
     "Main plant of the world's largest maker of silicon wafers.", "https://en.wikipedia.org/wiki/Shin-Etsu_Chemical"),
    ("SUMCO Imari", "SUMCO", "wafer", "JPN", 129.8800, 33.2650,
     "Largest site of the second-largest silicon wafer maker; with Shin-Etsu it supplies about half the world.", "https://en.wikipedia.org/wiki/Sumco"),
    ("GlobalWafers Hsinchu", "GlobalWafers", "wafer", "TWN", 121.0100, 24.7800,
     "Headquarters of the third-largest silicon wafer maker.", "https://en.wikipedia.org/wiki/GlobalWafers"),
    ("Siltronic Burghausen", "Siltronic", "wafer", "DEU", 12.8350, 48.1830,
     "Europe's largest silicon wafer plant, inside the Wacker polysilicon complex.", "https://en.wikipedia.org/wiki/Siltronic"),
    ("SK Siltron Gumi", "SK Siltron", "wafer", "KOR", 128.3940, 36.1050,
     "Korea's only large silicon wafer maker, supplying Samsung and SK hynix.", "https://en.wikipedia.org/wiki/SK_Siltron"),
    ("JSR Yokkaichi", "JSR", "chem", "JPN", 136.6250, 34.9440,
     "Main plant of a top photoresist maker; Japanese firms make most of the world's EUV and ArF resists.", "https://en.wikipedia.org/wiki/JSR_Corporation"),
    ("Tokyo Ohka Kawasaki", "Tokyo Ohka Kogyo", "chem", "JPN", 139.6590, 35.5800,
     "Largest photoresist maker by share, a key supplier of EUV resist.", "https://en.wikipedia.org/wiki/Tokyo_Ohka_Kogyo"),
    ("Shin-Etsu Naoetsu", "Shin-Etsu Chemical", "chem", "JPN", 138.2410, 37.1790,
     "Makes photoresists, photomask blanks and wafers for leading-edge fabs.", "https://en.wikipedia.org/wiki/Shin-Etsu_Chemical"),
    ("Stella Chemifa Izumiotsu", "Stella Chemifa", "chem", "JPN", 135.3980, 34.5000,
     "A leading maker of ultra-pure hydrogen fluoride for wafer etching and cleaning.", "https://www.stella-chemifa.co.jp/en/"),
    ("Ajinomoto ABF Kawasaki", "Ajinomoto Fine-Techno", "packaging", "JPN", 139.7480, 35.5320,
     "Near-monopoly maker of ABF build-up film used in almost every CPU and GPU package substrate.", "https://en.wikipedia.org/wiki/Ajinomoto_Build-up_Film"),
    ("Spruce Pine quartz (Sibelco)", "Sibelco", "quartz", "USA", -82.0700, 35.9000,
     "Spruce Pine mines supply most of the world's high-purity quartz for crucibles that grow silicon crystals.", "https://en.wikipedia.org/wiki/Spruce_Pine,_North_Carolina"),
    ("Spruce Pine quartz (The Quartz Corp)", "The Quartz Corp", "quartz", "USA", -82.0400, 35.9300,
     "Second high-purity quartz producer in Spruce Pine; the district has almost no substitute.", "https://en.wikipedia.org/wiki/Spruce_Pine,_North_Carolina"),
    ("Ingas Mariupol", "Ingas", "gas", "UKR", 37.5500, 47.0950,
     "Supplied a large share of the world's semiconductor-grade neon for DUV lasers; destroyed in 2022.", "https://en.wikipedia.org/wiki/Siege_of_Mariupol"),
    ("Cryoin Odesa", "Cryoin", "gas", "UKR", 30.7300, 46.4800,
     "Neon, krypton and xenon purifier that halted output during the 2022 invasion.", "https://en.wikipedia.org/wiki/Neon#Production"),
    ("Yunnan Germanium Lincang", "Yunnan Lincang Xinyuan Germanium", "mineral", "CHN", 100.0900, 23.8800,
     "China's largest germanium producer; China controls most germanium supply and restricted exports in 2023.", "https://en.wikipedia.org/wiki/Germanium#Production"),
    ("ASE Kaohsiung", "ASE Technology", "packaging", "TWN", 120.3040, 22.7250,
     "Main site of the world's largest chip packaging and test company.", "https://en.wikipedia.org/wiki/ASE_Technology_Holding"),
    ("TSMC AP6 Zhunan", "TSMC", "packaging", "TWN", 120.9000, 24.6970,
     "TSMC's largest advanced packaging plant, making CoWoS for AI accelerators.", "https://en.wikipedia.org/wiki/TSMC"),
    ("Amkor K5 Songdo", "Amkor Technology", "packaging", "KOR", 126.6460, 37.3810,
     "Amkor's largest factory, packaging chips for Korean and US customers.", "https://en.wikipedia.org/wiki/Amkor_Technology"),
    ("Amkor Bac Ninh", "Amkor Technology", "packaging", "VNM", 105.9900, 21.2100,
     "Amkor's Vietnamese packaging plant, part of the shift of back-end work out of China.", "https://en.wikipedia.org/wiki/Amkor_Technology"),
    ("JCET Jiangyin", "JCET Group", "packaging", "CHN", 120.2850, 31.9100,
     "Headquarters of China's largest and the world's third-largest chip packager.", "https://en.wikipedia.org/wiki/JCET"),
    ("Intel Penang", "Intel", "packaging", "MYS", 100.2720, 5.3010,
     "Intel's main assembly and test hub, which packages most of its processors.", "https://en.wikipedia.org/wiki/Intel"),
    ("Hemlock Semiconductor", "Hemlock Semiconductor", "wafer", "USA", -84.1960, 43.4710,
     "Largest US maker of hyper-pure polysilicon, the feedstock for silicon wafers.", "https://en.wikipedia.org/wiki/Hemlock_Semiconductor_Corporation"),
    ("Wacker Burghausen", "Wacker Chemie", "wafer", "DEU", 12.8300, 48.1870,
     "One of the few makers of semiconductor-grade polysilicon outside China.", "https://en.wikipedia.org/wiki/Wacker_Chemie"),
]

def chips():
    feats = [pt(lon, lat, {"id": slug(n), "n": n, "o": o, "cat": cat, "cc": cc, "why": why, "url": url})
             for n, o, cat, cc, lon, lat, why, url in CHIP_SITES]
    uniq_ids(feats)
    dump_geo("chip_sites", feats)
    SOURCES["chip_sites"] = {"url": "hand list in pipeline/step7_domains.py (each feature has its own url)",
                             "licence": "facts from cited public pages; Wikipedia text CC BY-SA 4.0", "count": len(feats)}

# =====================================================================
# 3. Cloud regions
# =====================================================================
CLOUD_URL = "https://raw.githubusercontent.com/jasonwilbur/mcp-server-cloud-regions/main/data/regions.json"
PROV = {"aws": "AWS", "azure": "Azure", "gcp": "GCP", "oci": "Oracle", "alibaba": "Alibaba", "digitalocean": "DigitalOcean",
        "paperspace": "Paperspace", "hetzner": "Hetzner", "vultr": "Vultr", "linode": "Akamai", "ovh": "OVHcloud",
        "scaleway": "Scaleway", "coreweave": "CoreWeave", "crusoe": "Crusoe", "lambda": "Lambda", "ibm": "IBM",
        "tencent": "Tencent", "huawei": "Huawei"}

def cloud():
    d = json.loads(cached("cloud_regions.json", CLOUD_URL))
    feats, nocc = [], 0
    for r in d["regions"]:
        L = r.get("location") or {}
        if L.get("latitude") is None or L.get("longitude") is None: continue
        cc = ISO2.get(L.get("countryCode") or "") or country_of(L.get("country") or "")
        if not cc: nocc += 1
        p = r.get("provider") or ""
        feats.append(pt(L["longitude"], L["latitude"], {
            "id": slug(f"{p}-{r.get('regionCode') or r.get('id')}"), "n": r.get("displayName") or r.get("regionCode"),
            "p": PROV.get(p, p.title()), "code": r.get("regionCode"), "city": L.get("city"), "cc": cc,
            "az": r.get("availabilityZones") or None, "s": r.get("status")}))
    uniq_ids(feats)
    print("cloud per provider:", dict(Counter(f["properties"]["p"] for f in feats)), "no country:", nocc)
    dump_geo("cloud", feats)
    src("cloud", "cloud_regions.json", "MIT (github.com/jasonwilbur/mcp-server-cloud-regions)", count=len(feats),
        version=d.get("metadata", {}).get("lastUpdated"))

# =====================================================================
# 4. BSL-4 labs
# =====================================================================
def wiki_coords(titles, tag):
    """{title: (lon, lat)} for Wikipedia articles, batched 50 per request; cached as raw/domains/wikicoords_<tag>.json."""
    p = f"{RAW}/wikicoords_{tag}.json"
    if OFFLINE:
        if not os.path.exists(p): raise SystemExit(f"missing {p}; run without --offline")
        return {k: tuple(v) for k, v in json.load(open(p)).items()}
    out, titles = {}, sorted(set(titles))
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        url = ("https://en.wikipedia.org/w/api.php?action=query&prop=coordinates&colimit=max&redirects=1&format=json&titles="
               + urllib.parse.quote("|".join(chunk)))
        b, _ = http(url); d = json.loads(b)["query"]
        back = {}
        for k in ("normalized", "redirects"):
            for m in d.get(k, []): back[m["to"]] = back.get(m["from"], m["from"])
        for pg in d.get("pages", {}).values():
            if pg.get("coordinates"):
                c = pg["coordinates"][0]; t = pg["title"]
                orig = back.get(t, t); orig = back.get(orig, orig)
                out[orig] = out[t] = (c["lon"], c["lat"])
        time.sleep(1)
    # articles without GeoData coordinates (many towns): Wikidata P625 via the enwiki sitelink
    rest = [t for t in titles if t not in out]
    for i in range(0, len(rest), 50):
        chunk = rest[i:i + 50]
        url = ("https://www.wikidata.org/w/api.php?action=wbgetentities&sites=enwiki&props=claims|sitelinks"
               "&sitefilter=enwiki&format=json&titles=" + urllib.parse.quote("|".join(chunk)))
        try: b, _ = http(url)
        except Exception as e: print("  wikidata failed:", e); break
        for ent in json.loads(b).get("entities", {}).values():
            t = ent.get("sitelinks", {}).get("enwiki", {}).get("title")
            for c in ent.get("claims", {}).get("P625", [])[:1]:
                v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
                if t and v and v.get("globe", "").endswith("Q2"): out.setdefault(t, (v["longitude"], v["latitude"]))
        time.sleep(1)
    json.dump(out, open(p, "w"), indent=0)
    logfetch(f"wikicoords_{tag}.json", "https://en.wikipedia.org/w/api.php?action=query&prop=coordinates")
    return out

def biolabs():
    html = wiki_parse("Biosafety level", "wiki_bsl.json")
    tp = Tables(); tp.feed(html)
    i = html.find("List of BSL-4 facilities")
    tab = next(t for t in tp.tables if "wikitable" in (t["attrs"].get("class") or "") and
               "country" in clean(" ".join(c["text"] for c in t["rows"][0])).lower())
    rows = grid(tab)
    head = [clean(c["text"]).lower() for c in rows[0]]
    ci = lambda *k: next((j for j, h in enumerate(head) if any(x in h for x in k)), None)
    C = dict(cc=ci("country"), loc=ci("location", "city"), n=ci("name", "facility"), y=ci("date", "year"),
             d=ci("description", "notes"))
    recs, bad = [], []
    for r in rows[1:]:
        if C["n"] >= len(r):
            bad.append(" | ".join(clean(c["text"]) for c in r)[:100]); continue
        g = lambda k: r[C[k]] if C[k] is not None and C[k] < len(r) else {"text": "", "links": []}
        name = clean(g("n")["text"])
        if not name or g("n") is g("loc"): bad.append(" | ".join(clean(c["text"]) for c in r)[:100]); continue
        cc = country_of(clean(g("cc")["text"]))
        if not cc: bad.append("no country: " + clean(g("cc")["text"]) + " | " + name); continue
        link = g("n")["links"][0] if g("n")["links"] else None
        yt, dt = clean(g("y")["text"]), clean(g("d")["text"])
        s = "operating"
        if re.search(r"expected|planned|proposed|future|announced|under construction|construction|being built|to open|will open", yt + " " + dt, re.I) \
                and not re.search(r"\b(opened|operational|operating since|began operat|in operation)\b", yt + " " + dt, re.I):
            s = "construction" if re.search(r"construction|being built", yt + " " + dt, re.I) else "planned"
        if parse_year(yt) and parse_year(yt) > datetime.now().year: s = "planned" if s == "operating" else s
        recs.append({"n": name, "cc": cc, "city": clean(g("loc")["text"]), "y": parse_year(yt), "s": s, "link": link,
                     "cityLink": (g("loc")["links"] or [None])[0]})
    print(f"biolabs: {len(recs)} rows, skipped {len(bad)}")
    for b in bad[:10]: print("   skip:", b)
    coords = wiki_coords([r[k] for r in recs for k in ("link", "cityLink") if r[k]], "bsl")
    feats, how = [], Counter()
    seen = set()
    for r in recs:
        k = (fold(r["n"]), r["cc"], fold(r["city"]))
        if k in seen: continue
        seen.add(k)
        xy, geo = None, None
        # town: the linked place article, else the city/country lookup
        cxy, cgeo = (coords[r["cityLink"]], "city") if r["cityLink"] in coords else place(r["cc"], r["city"], city_first=True)
        if r["link"] and r["link"] in coords:
            xy, geo = coords[r["link"]], "wiki"
            # the linked article may be about a parent body elsewhere (e.g. a ministry HQ)
            if cxy and dist_km(xy, cxy) > (60 if cgeo == "city" else 2500): xy, geo = None, None
        if not xy: xy, geo = cxy, cgeo
        if not xy: print("   unplaced:", r); continue
        how[geo] += 1
        feats.append(pt(xy[0], xy[1], {"id": slug(f"{r['cc']} {r['n']}"), "n": r["n"], "cc": r["cc"], "city": r["city"],
                                       "y": r["y"], "s": r["s"], "geo": geo,
                                       "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(r["link"].replace(" ", "_")) if r["link"] else None}))
    uniq_ids(feats)
    print("biolabs placed by:", dict(how), "status:", dict(Counter(f["properties"]["s"] for f in feats)))
    dump_geo("biolabs", feats)
    src("biolabs", "wiki_bsl.json", "CC BY-SA 4.0", url="https://en.wikipedia.org/wiki/Biosafety_level#List_of_BSL-4_facilities",
        count=len(feats))

# =====================================================================
# 5. Air connectivity (OpenFlights)
# =====================================================================
ROUTES_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/routes.dat"

def air():
    b = cached("routes.dat", ROUTES_URL)
    ap = {}
    for f in json.load(open(f"{OUT}/airports.geojson", encoding="utf-8"))["features"]:
        p = f["properties"]; cc = ISO2.get(p.get("c") or "")
        if not cc: continue
        v = (cc, f["geometry"]["coordinates"], p.get("ia") or p.get("i"), p.get("n"))
        if p.get("ia"): ap.setdefault(p["ia"], v)
        if p.get("i"): ap.setdefault(p["i"], v)
    routes, miss, cs = set(), Counter(), 0
    for row in csv.reader(io.StringIO(b.decode("utf-8", "replace"))):
        if len(row) < 7: continue
        if row[6].strip() == "Y": cs += 1; continue
        routes.add((row[0], row[2], row[4]))
    pairs, dom, deg = Counter(), Counter(), Counter()
    for al, s, d in routes:
        a, z = ap.get(s), ap.get(d)
        if not a: miss[s] += 1
        if not z: miss[d] += 1
        if not a or not z: continue
        deg[s] += 1; deg[d] += 1
        if a[0] == z[0]: dom[a[0]] += 1
        else: pairs[tuple(sorted((a[0], z[0])))] += 1
    hubs = {}
    for code, n in deg.most_common():
        cc, xy, ia, nm = ap[code]
        if cc not in hubs: hubs[cc] = [r4(xy[0]), r4(xy[1]), ia, nm]
    out = {"year": 2014, "pairs": [[a, z, w] for (a, z), w in sorted(pairs.items())], "dom": dict(sorted(dom.items())),
           "hubs": dict(sorted(hubs.items()))}
    print(f"air: {len(routes)} airline routes (dropped {cs} codeshares), {sum(miss.values())} route ends on unknown "
          f"airports ({len(miss)} codes), {len(pairs)} country pairs, {len(hubs)} hubs")
    dump_json("air.json", out)
    src("air", "routes.dat", "ODbL 1.0 (OpenFlights; data frozen June 2014)", count=len(pairs))

# =====================================================================
# 6. Country indicators + food
# =====================================================================
WB = {"net": "IT.NET.USER.ZS", "bb": "IT.NET.BBND.P2", "srv": "IT.NET.SECR.P6", "beds": "SH.MED.BEDS.ZS", "docs": "SH.MED.PHYS.ZS",
      "minr": "NY.GDP.MINR.RT.ZS"}   # mineral rents, % of GDP
# Internet censorship level, 0 (open) .. 100 (sealed), approximating Wikipedia "Internet censorship and surveillance by
# country" (CC BY-SA 4.0) pervasive / substantial / selective classes. Unlisted countries are treated as open (0).
CENSORSHIP = {"PRK": 97, "CHN": 90, "IRN": 85, "TKM": 85, "ERI": 70, "AFG": 60, "CUB": 60, "RUS": 60, "SYR": 60,
              "MMR": 55, "VNM": 55, "SAU": 50, "BLR": 50, "UZB": 50, "ETH": 45, "BHR": 45, "ARE": 40, "TJK": 40,
              "LAO": 40, "EGY": 35, "PAK": 35, "SDN": 35, "VEN": 35, "KAZ": 30, "AZE": 30, "QAT": 30, "OMN": 30,
              "TUR": 30, "KWT": 25, "THA": 20, "BGD": 20, "JOR": 20, "IND": 15}
# Taiwan is absent from the World Bank WDI; fill the internet figure from the TWNIC Taiwan Internet Report 2024 (adults online).
TAIWAN_NET = 88.4
ATLAS_URL = "https://dataverse.harvard.edu/api/access/datafile/13685110"
QCL_URL = "https://fenixservices.fao.org/faostat/static/bulkdownloads/Production_Crops_Livestock_E_All_Data_(Normalized).zip"
TM_URL = "https://fenixservices.fao.org/faostat/static/bulkdownloads/Trade_DetailedTradeMatrix_E_All_Data_(Normalized).zip"
FOOD_ITEMS = {"wheat": "Wheat", "maize": "Maize", "rice": "Rice (milled eq.)", "soy": "Soybeans", "banana": "Bananas",
              "chicken": "Chicken meat"}
FAO_PROD = {15: ("wheat", 1), 56: ("maize", 1), 27: ("rice", 0.67), 236: ("soy", 1), 486: ("banana", 1), 1058: ("chicken", 1)}
FAO_TRADE = {**FAO_PROD, 28: ("rice", 0.8), 31: ("rice", 1), 32: ("rice", 1)}
FOOD_YEAR = 2023

def atlas_ic():
    """Filtered rows (iso3, hs, year, export, import) of HS 8541/8542, streamed once into raw/domains/atlas_ic.csv."""
    p = f"{RAW}/atlas_ic.csv"
    if os.path.exists(p) and "--refresh-atlas" not in sys.argv:
        if "atlas_ic.csv" not in fetchlog:
            fetchlog["atlas_ic.csv"] = {"url": ATLAS_URL, "retrieved": datetime.fromtimestamp(os.path.getmtime(p), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    else:
        if OFFLINE: raise SystemExit(f"missing {p}; run without --offline")
        print("fetch", ATLAS_URL, "(~452 MB, streamed; only HS 8541/8542 kept)")
        req = urllib.request.Request(ATLAS_URL, headers={"User-Agent": UA})
        rows, years = [], set()
        with urllib.request.urlopen(req, timeout=300) as r:
            rd = csv.reader(io.TextIOWrapper(r, encoding="utf-8", newline=""))
            hdr = next(rd); ix = {k: i for i, k in enumerate(hdr)}
            for row in rd:
                if row[ix["product_hs92_code"]] not in ("8541", "8542"): continue
                rows.append([row[ix["country_iso3_code"]], row[ix["product_hs92_code"]], row[ix["year"]],
                             row[ix["export_value"]], row[ix["import_value"]]])
                years.add(int(row[ix["year"]]))
        keep = sorted(years)[-3:]
        with open(p + ".part", "w", newline="") as f:
            w = csv.writer(f); w.writerow(["iso3", "hs92", "year", "export_value", "import_value"])
            w.writerows(r for r in rows if int(r[2]) in keep)
        os.replace(p + ".part", p)
        logfetch("atlas_ic.csv", ATLAS_URL)
    return list(csv.DictReader(open(p, newline="")))

def m49(code):
    c = (code or "").strip().strip("'")
    if not c.isdigit(): return None
    return N3.get(str(int(c)))

def fao_rows(zpath, member_prefix, want):
    """Yield csv rows (as lists) of the main data file whose text passes want(line)."""
    z = zipfile.ZipFile(zpath)
    name = next(n for n in z.namelist() if n.startswith(member_prefix) and n.endswith("(Normalized).csv"))
    with z.open(name) as fh:
        tw = io.TextIOWrapper(fh, encoding="latin-1", newline="")
        hdr = next(csv.reader([tw.readline()]))
        yield hdr
        yield from csv.reader(l for l in tw if want(l))

def food():
    qcl = cached_big("fao_qcl.zip", QCL_URL, refresh="--refresh-fao" in sys.argv)
    tm = cached_big("fao_tm.zip", TM_URL, refresh="--refresh-fao" in sys.argv)
    # production: {item: {year: {iso3: t}}}
    prod = defaultdict(lambda: defaultdict(Counter))
    it = fao_rows(qcl, "Production_Crops_Livestock_E_All_Data", lambda l: '"Production"' in l)
    h = next(it); ix = {k: i for i, k in enumerate(h)}
    for r in it:
        try: code, y = int(r[ix["Item Code"]]), int(r[ix["Year"]])
        except ValueError: continue
        if code not in FAO_PROD or y < FOOD_YEAR - 5 or r[ix["Unit"]] != "t": continue
        a = m49(r[ix["Area Code (M49)"]]); v = num(r[ix["Value"]])
        if not a or v is None: continue
        item, f = FAO_PROD[code]
        prod[item][y][a] += v * f
    # trade (importer-reported): {item: {year: Counter[(exp, imp)]}}
    trade = defaultdict(lambda: defaultdict(Counter))
    codes = tuple(f'"{c}"' for c in FAO_TRADE)
    print("food: streaming the FAOSTAT trade matrix (8.5 GB csv inside the zip; a few minutes)")
    t0 = time.time()
    it = fao_rows(tm, "Trade_DetailedTradeMatrix_E_All_Data", lambda l: '"Import quantity"' in l and any(c in l for c in codes))
    h = next(it); ix = {k: i for i, k in enumerate(h)}
    for r in it:
        try: code, y = int(r[ix["Item Code"]]), int(r[ix["Year"]])
        except ValueError: continue
        if code not in FAO_TRADE or y < FOOD_YEAR - 5 or r[ix["Unit"]] != "t": continue
        imp, exp = m49(r[ix["Reporter Country Code (M49)"]]), m49(r[ix["Partner Country Code (M49)"]])
        v = num(r[ix["Value"]])
        if not imp or not exp or imp == exp or v is None: continue
        item, f = FAO_TRADE[code]
        trade[item][y][(exp, imp)] += v * f
    print(f"  trade matrix read in {time.time() - t0:.0f}s")
    out = {"year": FOOD_YEAR, "items": FOOD_ITEMS, "prod": {}, "trade": {}, "itemYears": {}}
    for item in FOOD_ITEMS:
        py = FOOD_YEAR if prod[item].get(FOOD_YEAR) else max(prod[item], default=None)
        ty = FOOD_YEAR if trade[item].get(FOOD_YEAR) else max(trade[item], default=None)
        out["prod"][item] = {k: round(v / 1000, 1) for k, v in sorted(prod[item].get(py, {}).items()) if v >= 50}
        fl = [[e, i, round(v / 1000, 1)] for (e, i), v in trade[item].get(ty, {}).items() if v >= 1000]
        out["trade"][item] = sorted(fl, key=lambda x: -x[2])
        if py != FOOD_YEAR or ty != FOOD_YEAR: out["itemYears"][item] = {"prod": py, "trade": ty}
        print(f"  {item}: prod {len(out['prod'][item])} countries ({py}), {len(fl)} flows ({ty}), "
              f"world prod {sum(out['prod'][item].values()) / 1000:.0f} Mt")
    if not out["itemYears"]: del out["itemYears"]
    return out

def num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None

def domains():
    countries = defaultdict(dict); years = {}
    for k, ind in WB.items():
        url = f"https://api.worldbank.org/v2/country/all/indicator/{ind}?format=json&per_page=20000&mrnev=1"
        d = json.loads(cached(f"wb_{ind}.json", url))
        ys = Counter()
        for e in d[1] or []:
            a = ISO3.get(e.get("countryiso3code") or "")
            v = num(e.get("value"))
            if not a or v is None: continue
            countries[a][k] = round(v, 2 if v < 10 else 1); countries[a][k + "Y"] = int(e["date"]); ys[int(e["date"])] += 1
        years[k] = ys.most_common(1)[0][0] if ys else None
        print(f"domains: {k} ({ind}) {sum(ys.values())} countries, mode year {years[k]}")
        src(f"wb_{k}", f"wb_{ind}.json", "CC BY 4.0 (World Bank WDI)", indicator=ind)
    rows = atlas_ic()
    ly = max(int(r["year"]) for r in rows)
    agg = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        if int(r["year"]) != ly: continue
        a = ISO3.get(r["iso3"])
        if not a: continue
        agg[a][0] += num(r["export_value"]) or 0; agg[a][1] += num(r["import_value"]) or 0
    for a, (x, m) in agg.items():
        if x >= 1e6: countries[a]["chipX"] = round(x / 1e9, 3)
        if m >= 1e6: countries[a]["chipM"] = round(m / 1e9, 3)
    years["chip"] = ly
    top = sorted(agg.items(), key=lambda t: -t[1][0])[:5]
    print(f"domains: chips {ly}, {len(agg)} countries; top exporters", [(a, round(x / 1e9)) for a, (x, _) in top])
    src("atlas_chips", "atlas_ic.csv", "CC0 1.0 (Harvard Growth Lab, Atlas of Economic Complexity, HS92 4-digit)",
        note="HS 8541+8542 exports and imports, US$")
    for a, lvl in CENSORSHIP.items():
        countries[a]["cens"] = lvl
    years["cens"] = 2023
    src("wiki_censorship", None, "CC BY-SA 4.0 (Wikipedia, \"Internet censorship and surveillance by country\")",
        note="hand-listed approximation, 0 open .. 100 sealed")
    if "net" not in countries["TWN"]:
        countries["TWN"]["net"] = TAIWAN_NET; countries["TWN"]["netY"] = 2024
        src("twnic", None, "TWNIC Taiwan Internet Report 2024", note="Taiwan internet use; Taiwan is not in the World Bank WDI")
    fd = food()
    years["food"] = fd["year"]
    src("fao_qcl", "fao_qcl.zip", "CC BY 4.0 (FAO FAOSTAT)")
    src("fao_tm", "fao_tm.zip", "CC BY 4.0 (FAO FAOSTAT, detailed trade matrix)")
    return {"updated": now_utc(), "years": years, "countries": {a: countries[a] for a in sorted(countries)}, "food": fd}

if __name__ == "__main__":
    print("step7", "(offline)" if OFFLINE else "")
    old = json.load(open(DOMAINS)) if os.path.exists(DOMAINS) else {}
    SOURCES.update(old.get("sources", {}))
    dom = None
    for name, fn in {"fabs": fabs, "chips": chips, "cloud": cloud, "biolabs": biolabs, "air": air, "domains": domains}.items():
        if ONLY and name not in ONLY: continue
        r = fn(); json.dump(fetchlog, open(FETCHLOG, "w"), indent=1)
        if name == "domains": dom = r
    if dom is None:
        if not old: print("domains.json not written (run with the domains section once)"); sys.exit(0)
        dom = old
    dom["sources"] = SOURCES
    dump_json("domains.json", dom)
