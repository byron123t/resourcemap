#!/usr/bin/env python3
"""Where a news story happened: checks GDELT's place against what the article says.

GDELT geocodes each event from the article text with its own gazetteer, and gets it wrong often enough to matter: it picks
the wrong one of two towns with the same name (Pokrovsk in Mordoviya instead of Donetsk Oblast; "Fasher" in Kassala instead
of El Fasher in Darfur), takes a word that isn't a place for one (the Oreshnik missile as a village in Kirov Oblast, the
Basij militia as a hamlet in Fars), or puts the story where the government that commented on it sits. Here the article's own
headline and summary (og:title and og:description, fetched once per URL and cached) are matched against GeoNames, and the
place they name wins over GDELT's when they disagree. With nothing named, GDELT's place is kept only if a GeoNames place of
that name lies within 50 km of it; otherwise it moves to the same-named place in a country the story is about.

GeoNames (cities5000, admin1 codes, country info; CC BY 4.0) is downloaded once into the cache directory. Stdlib only.
"""
import gzip
import hashlib
import html
import io
import json
import math
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

GEONAMES = "https://download.geonames.org/export/dump/"
GN_FILES = ("cities5000.zip", "admin1CodesASCII.txt", "countryInfo.txt")
UA = "world-extraction-atlas/1.0 (+https://github.com/byron123t/resourcemap)"

# Regions that aren't (or aren't only) first-level divisions, and water. (name|alias, lat, lon, radius km, ISO2 or "" for water)
EXTRA = [
    ("Darfur", 13.5, 24.5, 450, "SD"), ("Kordofan", 12.5, 29.5, 400, "SD"), ("Donbas|Donbass", 48.2, 38.3, 200, "UA"),
    ("Arakan", 20.0, 93.6, 250, "MM"), ("Gaza|Gaza Strip", 31.42, 34.37, 25, "PS"), ("West Bank", 31.95, 35.25, 60, "PS"),
    ("Kashmir", 34.1, 74.8, 200, "IN"), ("Iraqi Kurdistan|Kurdistan Region", 36.2, 44.0, 220, "IQ"),
    ("Sinai", 29.6, 33.8, 200, "EG"), ("Golan Heights|Golan", 33.0, 35.75, 30, "SY"), ("Crimea", 45.3, 34.4, 150, "UA"),
    ("Nagorno-Karabakh|Karabakh", 39.85, 46.75, 60, "AZ"), ("Transnistria", 47.2, 29.4, 80, "MD"),
    ("Somaliland", 9.6, 45.5, 350, "SO"), ("Puntland", 9.0, 49.0, 350, "SO"), ("Baluchistan|Balochistan", 28.5, 65.5, 400, "PK"),
    ("Sistan", 29.5, 60.9, 300, "IR"), ("Khyber", 34.0, 71.5, 200, "PK"), ("Waziristan|North Waziristan", 32.9, 70.0, 80, "PK"),
    ("South Waziristan", 32.3, 69.6, 60, "PK"), ("Tigray", 13.8, 39.0, 200, "ET"), ("Amhara", 11.5, 38.0, 300, "ET"),
    ("Oromia", 8.0, 39.0, 450, "ET"), ("Sahel", 15.0, 0.0, 1500, ""), ("Cabo Delgado", -12.3, 39.7, 180, "MZ"),
    ("Ituri", 1.6, 29.9, 180, "CD"), ("North Kivu", -0.8, 29.0, 170, "CD"), ("South Kivu", -3.0, 28.4, 160, "CD"),
    ("Manipur", 24.8, 93.9, 120, "IN"), ("Rakhine", 20.0, 93.6, 250, "MM"), ("Idlib", 35.9, 36.6, 60, "SY"),
    ("Kherson", 46.6, 33.4, 120, "UA"), ("Zaporizhzhia", 47.5, 35.6, 120, "UA"), ("Kursk", 51.7, 36.2, 150, "RU"),
    ("Belgorod", 50.6, 37.0, 120, "RU"), ("Bryansk", 52.8, 33.5, 140, "RU"), ("South Lebanon|southern Lebanon", 33.25, 35.4, 40, "LB"),
    ("Bekaa|Beqaa", 33.85, 36.0, 50, "LB"), ("Hodeidah", 14.8, 42.95, 60, "YE"), ("Marib", 15.5, 45.3, 80, "YE"),
    ("Strait of Hormuz|Hormuz", 26.5, 56.4, 120, ""), ("Persian Gulf|Arabian Gulf", 27.0, 51.5, 400, ""),
    ("Gulf of Oman", 24.5, 58.5, 250, ""), ("Red Sea", 20.0, 38.5, 700, ""), ("Gulf of Aden", 12.5, 47.5, 350, ""),
    ("Bab el-Mandeb|Bab al-Mandab", 12.6, 43.4, 60, ""), ("Black Sea", 43.3, 34.0, 500, ""), ("Sea of Azov", 46.0, 36.5, 150, ""),
    ("Baltic Sea", 56.5, 19.0, 450, ""), ("South China Sea", 14.0, 114.0, 900, ""), ("East China Sea", 29.0, 125.0, 450, ""),
    ("Taiwan Strait", 24.3, 119.5, 150, ""), ("Yellow Sea", 36.0, 123.0, 350, ""), ("Sea of Japan", 40.0, 135.0, 600, ""),
    ("Mediterranean", 35.5, 18.0, 1500, ""), ("Aegean Sea", 38.5, 25.0, 250, ""), ("Caribbean", 15.0, -75.0, 1200, ""),
    ("Gulf of Mexico", 25.0, -90.0, 700, ""), ("Arabian Sea", 17.0, 63.0, 900, ""), ("Bay of Bengal", 15.0, 88.0, 700, ""),
    ("Suez Canal", 30.5, 32.35, 90, ""), ("Panama Canal", 9.1, -79.7, 40, ""), ("Strait of Malacca", 3.5, 100.5, 350, ""),
    ("Lake Chad", 13.2, 14.1, 120, ""), ("Gulf of Guinea", 2.0, 4.0, 700, ""), ("English Channel", 50.2, -1.0, 250, ""),
]
# Countries and the adjectives and groups that point at one ("Ukrainian", "Houthi")
COUNTRY_ALIAS = {
    "US": "US", "U.S.": "US", "USA": "US", "America": "US", "American": "US", "UK": "GB", "Britain": "GB", "British": "GB",
    "Saudi": "SA", "Thai": "TH", "Somali": "SO", "Afghan": "AF", "Burmese": "MM", "Burma": "MM", "Lebanese": "LB", "Filipino": "PH",
    "Dutch": "NL", "Chinese": "CN", "Japanese": "JP", "Turkish": "TR", "Turkey": "TR", "Türkiye": "TR", "Emirati": "AE", "UAE": "AE",
    "Emirates": "AE", "Congolese": "CD", "DR Congo": "CD", "DRC": "CD", "Mexican": "MX", "Palestinian": "PS", "Palestine": "PS",
    "Houthi": "YE", "Houthis": "YE", "Hezbollah": "LB", "Hamas": "PS", "Taliban": "AF", "IRGC": "IR", "Kremlin": "RU",
    "Ivory Coast": "CI", "Ivorian": "CI", "Czech": "CZ", "Swiss": "CH", "French": "FR", "German": "DE", "Greek": "GR",
    "Danish": "DK", "Swedish": "SE", "Finnish": "FI", "Polish": "PL", "Spanish": "ES", "Portuguese": "PT", "Irish": "IE",
    "Norwegian": "NO", "Pakistani": "PK", "Bangladeshi": "BD", "Nepali": "NP", "Sri Lankan": "LK", "Vietnam": "VN",
    "Vietnamese": "VN", "Laos": "LA", "Lao": "LA", "Syria": "SY", "Russia": "RU", "Iran": "IR", "South Korea": "KR",
    "North Korea": "KP", "Korean": "KR", "Taiwanese": "TW", "Taiwan": "TW", "Moldova": "MD", "Bolivia": "BO", "Venezuela": "VE",
    "Tanzania": "TZ", "Kosovo": "XK", "Macedonia": "MK", "Gazan": "PS", "Israeli": "IL", "Yemeni": "YE", "Sudanese": "SD",
    "Malian": "ML", "Nigerien": "NE", "Chadian": "TD", "Kurdish": "IQ", "Peruvian": "PE", "Chilean": "CL", "Argentine": "AR",
    "Brazilian": "BR", "Colombian": "CO", "Ecuadorian": "EC", "Haitian": "HT", "Cuban": "CU", "Canadian": "CA", "Australian": "AU",
    "Indian": "IN", "Indonesian": "ID", "Malaysian": "MY", "Cambodian": "KH", "Ukrainian": "UA", "Russian": "RU", "Iranian": "IR",
    "Iraqi": "IQ", "Syrian": "SY", "Egyptian": "EG", "Libyan": "LY", "Ethiopian": "ET", "Eritrean": "ER", "Kenyan": "KE",
    "Ugandan": "UG", "Rwandan": "RW", "Burundian": "BI", "Mozambican": "MZ", "Nigerian": "NG", "Ghanaian": "GH",
    "Cameroonian": "CM", "Senegalese": "SN", "Burkinabe": "BF", "Jordanian": "JO", "Qatari": "QA", "Kuwaiti": "KW", "Omani": "OM",
    "Bahraini": "BH", "Armenian": "AM", "Azerbaijani": "AZ", "Georgian": "GE", "Belarusian": "BY", "Serbian": "RS",
    "Bosnian": "BA", "Albanian": "AL", "Romanian": "RO", "Bulgarian": "BG", "Hungarian": "HU", "Estonian": "EE",
    "Latvian": "LV", "Lithuanian": "LT", "Mongolian": "MN", "Kazakh": "KZ", "Uzbek": "UZ", "Tajik": "TJ", "Kyrgyz": "KG",
    "Turkmen": "TM", "Algerian": "DZ", "Moroccan": "MA", "Tunisian": "TN", "South African": "ZA", "Zimbabwean": "ZW",
    "Zambian": "ZM", "Angolan": "AO", "Namibian": "NA", "Somalian": "SO", "South Sudanese": "SS", "Djiboutian": "DJ",
    "Gaza": "PS", "West Bank": "PS",
}
# GDELT uses FIPS 10-4 country codes; GeoNames lists most of them, not these
FIPS_EXTRA = {"GZ": "PS", "WE": "PS", "KV": "XK", "RB": "RS", "MJ": "ME", "OD": "SS", "RI": "RS", "YI": "RS"}
# words that are also the name of a town of 5,000+, so a lower-case slug can't be trusted to mean the place
COMMON = set("""
about after again against all along also amid among and another any around attack attacks back bad bank bar base bat bay
battle best big black blue border born boy bridge burn bus call camp can capital card care case cat center central chief child
city clash clean close coast cold come cook court cover cross dam day dead deal death don down dry each early east end energy
even ever fall far fast fire first five flat fort four free front gas girl gold good grand great green guard gun hall harbor
hard harm head heart help her high hill his home hope hot house how hub iron island join just keep key kill king land large
last law lead leader left life light like line little live long lord love low main major man many march market may mayor men
mill mine mobile more most mount mountain much nice north not now oak ocean old one open over palm paris park pass peace
people plain plant point police port post power president prince queen rain ram range red reform rich ride ring rising river
road rock rose round royal rule run safe saint sale salt san sand say sea second see seven shell shot side six small snow
soldier south spring stand star state still stone strike sun sure tank ten them then three time today top tower town trade
tree trust two union unity up van victoria victory view village wall war water way well west white why wide will win wind
wing winter wood work world year york young officer airport drug temple university harvest media rail price plan bell
southern northern eastern western northwest northeast southwest southeast justice than college ties federal give bend summit
parole change turn trail international dire valley gulf brush street race real twist date lakes made forest worth ridge
mission delta economy sues same midway aurora boulder mead hope liberty independence progress providence success
republic kingdom federation commonwealth emirate
january february april june july august september october november december monday tuesday wednesday thursday
friday saturday sunday christmas easter
""".split())
PERSON_TITLE = set("""mr mrs ms dr sir officer sgt sergeant capt captain lt col gen general president minister judge sen senator rep
pope king queen prince princess sheikh imam rev father saint st""".split())
PLACE_NEXT = set("""airport airfield airbase base port city town province region state district county oblast governorate valley
mayor governor police hospital university bridge dam camp border crossing market mosque church refinery plant""".split())
# newspapers and agencies named after places
MEDIA = re.compile(r"\b(?:Washington Post|New York Times|New York Post|Los Angeles Times|Jerusalem Post|Times of Israel|Moscow Times|"
                   r"Kyiv Independent|Kyiv Post|Tehran Times|Hindustan Times|Times of India|Dawn|Al Jazeera|Gulf News|Arab News|"
                   r"South China Morning Post|Bangkok Post|Jakarta Post|Straits Times|Japan Times|Korea Herald|Sydney Morning Herald|"
                   r"Irish Times|Financial Times|Toronto Star|Boston Globe|Chicago Tribune|Miami Herald|Kathmandu Post|"
                   r"Daily Nation|Addis Standard|Sudan Tribune|Radio Tamazuj|Voice of America|Radio Free Europe|"
                   r"Xinhua|Reuters|AFP|AP|Anadolu|Interfax|TASS|RIA Novosti|IRNA|Tasnim|Fars News|Mehr)\b", re.I)
# "BAMAKO, Mali, Oct. 13 (Reuters) -" or "MOGADISHU (WDN) —"
DATELINE = re.compile(r"^\s*[A-Z][A-Z .'’-]{2,30}(?:,\s*[A-Z][\w .'’-]{2,30})?(?:,?\s*\w{3,5}\.?\s*\d{1,2})?\s*(?:\([^)]{1,30}\)\s*)?[-—–:]+")
# the country an attack is aimed at: "attacks Russian oil refinery", "hits Afghanistan sites", "strikes on Ukrainian cities"
TARGET = re.compile(r"\b(?:attacks?|attacked|strikes?|struck|hits?|bomb(?:s|ed|ing)?|shell(?:s|ed|ing)|raids?|raided|targets?|targeted)"
                    r"\s+(?:on\s+|against\s+|in\s+|inside\s+)?(?:an?\s+|the\s+)?(?:\w+\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b")
LOCATIVE = re.compile(r"\b(?:in|at|near|outside|across|around|on|over|into|off|south of|north of|east of|west of)\s+(?:the\s+)?$", re.I)
# a place used for its government: "Moscow says", "Kyiv's relations with", "Tehran warns"
SPEECH = re.compile(r"\s*(?:['’]s\s+(?:relations?|ties|government|leadership|allies|position|stance|claims?|response|demands?|officials?|forces|military|army|envoy|president|leader)|"
                    r"\s*(?:has |have |is |was )?(?:says?|said|warns?|warned|accuses?|accused|claims?|claimed|denies|denied|vows?|vowed|"
                    r"urges?|urged|rejects?|rejected|condemns?|condemned|blames?|blamed|calls? for|demands?|threatens?|threatened|"
                    r"tells?|told|insists?|signals?|responds?|hits back|summons?|announces?|announced|confirms?|confirmed)\b)", re.I)
REGION_WORD = r"(?:region|province|oblast|state|governorate|district|county|prefecture|krai|kray|department|division)"


def norm(s):
    """'Ta'izz' -> 'taizz', 'Kharkiv-Oblast' -> 'kharkiv oblast'."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[’'`ʼ‘]", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def strip_article(n):
    """'el fasher' -> 'fasher', 'al hudaydah' -> 'hudaydah'."""
    return re.sub(r"^(?:al|el|ad|as|ash|ar|an|at|az|ed|es|ez)\s+(?=\w{3})", "", n)


def hav(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(min(1, math.sqrt(a)))


def _get(url, timeout, limit=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read(limit) if limit else r.read()
        if r.headers.get("Content-Encoding", "").lower() == "gzip":
            try:
                body = gzip.decompress(body)
            except (OSError, EOFError):
                body = gzip.GzipFile(fileobj=io.BytesIO(body)).read(limit or -1) if limit else b""
        return body, r.headers.get_content_charset() or "utf-8"


class Places:
    """GeoNames towns of 5,000+, first-level regions, a few named regions and seas; country names and adjectives."""

    def __init__(self, cache_dir, log=print, offline=False):
        self.log = log
        d = os.path.join(cache_dir, "geonames")
        os.makedirs(d, exist_ok=True)
        for f in GN_FILES:
            p = os.path.join(d, f)
            if not os.path.exists(p) and not offline:
                body, _ = _get(GEONAMES + f, 120)
                with open(p + ".tmp", "wb") as fh:
                    fh.write(body)
                os.replace(p + ".tmp", p)
        self.names = {}     # normalised name -> [entry]; entry = dict(k, n, lat, lon, cc, a1, pop, r)
        self.cname = {}     # ISO2 -> country name
        self.fips = dict(FIPS_EXTRA)
        self.a1name = {}    # "UA.07" -> "Kharkiv"
        with open(os.path.join(d, "countryInfo.txt"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                c = line.rstrip("\n").split("\t")
                if len(c) > 4:
                    self.cname[c[0]] = c[4]
                    if c[3]:
                        self.fips.setdefault(c[3], c[0])
        self.countries = {}  # normalised country name or adjective -> ISO2
        for iso, n in self.cname.items():
            self.countries[norm(n)] = iso
        for n, iso in COUNTRY_ALIAS.items():
            self.countries[norm(n)] = iso
        with open(os.path.join(d, "admin1CodesASCII.txt"), encoding="utf-8") as f:
            for line in f:
                c = line.rstrip("\n").split("\t")
                if len(c) >= 3:
                    self.a1name[c[0]] = c[2]
        members, alts = {}, []
        with zipfile.ZipFile(os.path.join(d, "cities5000.zip")) as z:
            text = z.read("cities5000.txt").decode("utf-8")
        for line in text.splitlines():
            c = line.split("\t")
            if len(c) < 15:
                continue
            try:
                lat, lon, pop = float(c[4]), float(c[5]), int(c[14] or 0)
            except ValueError:
                continue
            e = {"k": "city", "n": c[1], "lat": lat, "lon": lon, "cc": c[8], "a1": f"{c[8]}.{c[10]}", "pop": pop,
                 "cap": c[7] == "PPLC", "r": 15 if pop < 1_000_000 else 30}
            keys = {norm(c[1]), norm(c[2])}
            for key in keys | {strip_article(k) for k in keys}:
                if len(key) >= 3:
                    self.names.setdefault(key, []).append(e)
            if pop >= 20_000:  # other spellings ("Kiev", "Ta'izz", "Palmyra" for Tadmur), for sizeable towns
                for a in c[3].split(","):
                    if 4 <= len(a) <= 40 and re.fullmatch(r"[A-Za-z\u00C0-\u024F' .-]+", a):
                        alts.append((norm(a), e))
                        alts.append((strip_article(norm(a)), e))
            members.setdefault(e["a1"], []).append(e)
            if e["cap"]:
                members.setdefault("caps", {}).setdefault(e["cc"], []).append(e)
        for code, n in self.a1name.items():
            ms = members.get(code) if code != "caps" else None
            if not ms:
                continue
            w = sum(max(m["pop"], 1) for m in ms)
            lat = sum(m["lat"] * max(m["pop"], 1) for m in ms) / w
            lon = sum(m["lon"] * max(m["pop"], 1) for m in ms) / w
            r = max(40, sorted(hav(lat, lon, m["lat"], m["lon"]) for m in ms)[int(0.9 * (len(ms) - 1))])
            e = {"k": "region", "n": n, "lat": lat, "lon": lon, "cc": code[:2], "a1": code, "pop": w, "r": min(r, 900)}
            full = norm(n)
            short = re.sub(r"\s+" + REGION_WORD + r"$|^(?:republic of|respublika|wilayat|muhafazat)\s+", "", full).strip()
            keys = {full, short, strip_article(short)}
            if " and " in short:  # "Sistan and Baluchestan"
                keys |= {p for p in short.split(" and ") if len(p) >= 5}
            for key in keys:
                if len(key) >= 3:
                    self.names.setdefault(key, []).append(e)
        for pat, lat, lon, r, cc in EXTRA:
            for alias in pat.split("|"):
                e = {"k": "region" if cc else "sea", "n": pat.split("|")[0], "lat": lat, "lon": lon, "cc": cc, "a1": "", "pop": 0, "r": r, "x": 1}
                self.names.setdefault(norm(alias), []).append(e)
        for iso, cn in self.cname.items():  # "Kuwait airport", "Panama" as the capital named after its country
            for e in members.get("caps", {}).get(iso, []):
                if norm(e["n"]).startswith(norm(cn)) and e not in self.names.setdefault(norm(cn), []):
                    self.names[norm(cn)].append(e)
        own = set(self.names)
        self.alt = {}  # other spellings that are some other town's own name ("Pokrovsk", Engels' old name): second choices
        for key, e in alts:
            if len(key) < 4:
                continue
            d = self.names if key not in own else self.alt
            if e not in d.setdefault(key, []):
                d[key].append(e)
        self.maxlen = 5
        self.common = set()

    def learn(self, pairs):
        """Words that name a town but in news slugs mostly mean something else. pairs: (slug title, GDELT FIPS code) for many
        articles. A one-word name seen 8+ times, under half of them in articles GDELT placed in that town's country, is a word."""
        seen, home = {}, {}
        for text, fips in pairs:
            cc = self.country_of(fips)
            for key in set(norm(text).split()):
                ents = self.names.get(key)
                if not ents or key in self.countries:
                    continue
                seen[key] = seen.get(key, 0) + 1
                if any(e["cc"] == cc for e in ents):
                    home[key] = home.get(key, 0) + 1
        self.common = {k for k, n in seen.items() if n >= 8 and home.get(k, 0) < 0.5 * n}
        return len(self.common)

    # ------------------------------------------------------------------ matching

    def mentions(self, text, cased):
        """Place and country names in text: [(start, key, entries or None, iso2 or None, region_hint, next word)].
        `cased`: the text keeps its capitals (an article title), so a place must be written with one; a URL slug doesn't."""
        if not text:
            return []
        words = re.findall(r"[A-Za-z][\w'’-]*", text)
        if cased and len(words) >= 4 and sum(w[0].isupper() for w in words) >= 0.7 * len(words):
            cased = False  # Title Case Headline: capitals say nothing
        toks = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"[A-Za-zÀ-ɏ][\wÀ-ɏ'’.-]*", text)]
        out, i = [], 0
        while i < len(toks):
            hit = None
            for n in range(min(self.maxlen, len(toks) - i), 0, -1):
                raw = re.sub(r"['’]s$", "", " ".join(t[2] for t in toks[i:i + n]).rstrip("."))
                key = norm(raw)
                if not key:
                    continue
                first = toks[i][2]
                if cased and not first[0].isupper():
                    continue
                ents = self.names.get(key)
                if not ents and strip_article(key) != key:
                    key = strip_article(key)  # "al-Fashir" -> "fashir"; "at Voronezh" isn't a name, "Voronezh" is
                    ents = self.names.get(key)
                if ents and key in self.alt:
                    ents = ents + self.alt[key]
                iso = self.countries.get(key)
                if iso is None and n == 1:  # "Ukrainian" -> Ukraine, "Iraq's" -> Iraq
                    k2 = re.sub(r"s$", "", key)
                    iso = self.countries.get(k2)
                if not ents and iso is None:
                    continue
                if not cased and key in ("us", "uk") and raw.islower():
                    continue  # "attack us", "uk" in a slug could be anything; "UK" in a title is the UK
                if " " not in key and iso is None and (key in COMMON or len(key) < 4 and not cased
                                               or not cased and (key in self.common or all(e["k"] == "city" and e["pop"] < 20_000 for e in ents))):
                    continue
                nxt = norm(toks[i + n][2]) if i + n < len(toks) else ""
                if cased and " " not in key and ents and iso is None and all(e["k"] == "city" for e in ents) and (
                        i > 0 and toks[i - 1][2].rstrip(".").lower() in PERSON_TITLE
                        or nxt and toks[i + n][2][0].isupper() and not text[toks[i + n - 1][1]:toks[i + n][0]].strip()
                        and nxt not in self.names and nxt not in self.countries and nxt not in PLACE_NEXT
                        and max([e["pop"] for e in self.names.get(key, ()) if e["k"] == "city"] or [0]) < 100_000):
                    continue  # a name: "Officer Joshua Briese", "Volodymyr Zelensky"
                after = text[toks[i + n - 1][1]:toks[i + n - 1][1] + 16]
                hint = bool(re.match(r"\s+" + REGION_WORD + r"\b", after, re.I)) or bool(re.search(r"\b" + REGION_WORD + r"$", key))
                hit = (toks[i][0], key, ents, iso, hint, nxt, n)
                break
            if hit:
                out.append(hit[:6])
                i += hit[6]
            else:
                i += 1
        return out

    def coast(self, ccs):
        """Towns of 50,000+ in these countries (candidates for 'off the coast of')."""
        return [e for es in self.names.values() for e in es if e["k"] == "city" and e["cc"] in ccs and e["pop"] >= 50_000]

    def country_of(self, fips):
        return self.fips.get(fips or "", "")

    def near_named(self, name, lat, lon, km=50):
        """GeoNames entries called `name` (a GDELT place name's first part) within km of the point."""
        key = norm(name)
        ents = self.names.get(key) or self.names.get(strip_article(key)) or []
        return [e for e in ents if hav(lat, lon, e["lat"], e["lon"]) <= max(km, e["r"] if e["k"] != "city" else km)]

    def label(self, e):
        cn = self.cname.get(e["cc"], "")
        if e["k"] == "city":
            a1 = self.a1name.get(e["a1"], "")
            return ", ".join(p for p in (e["n"], a1 if a1 and a1 != e["n"] else "", cn) if p)
        if e["k"] == "region" and cn and cn != e["n"]:
            return f"{e['n']}, {cn}"
        return e["n"]

    # ------------------------------------------------------------------ the decision

    def resolve(self, gname, gfips, glat, glon, title=None, lead=None, slug=None, actors=()):
        """Best place for a story GDELT put at (glat, glon) and called gname. title/lead: the article's own title and summary
        (cased text); slug: a title made from the URL (lower case). Returns dict(n, cc, lat, lon, geo, moved_km), or None
        when the text names countries, the place is in none of them and nothing better is named.
        geo: "text" (the article names the place), "gdelt" (GDELT's place, a real town of that name), "region" (a region or sea
        the article names, or GDELT's name was one), "approx" (GDELT's place, not found in GeoNames)."""
        gcc = self.country_of(gfips)
        texts = []
        if title:
            texts.append((title, 3.0, True))
        elif slug:
            texts.append((slug, 2.5, False))
        if lead:
            texts.append((lead, 1.2, True))
        found, named, hinted = [], set(), set()   # named: countries the text names
        for t, w, cased in texts:
            t = MEDIA.sub(" ", t)
            dl = DATELINE.match(t) if w < 2 else None
            for start, key, ents, iso, hint, nxt in self.mentions(t, cased):
                if iso:
                    named.add(iso)
                    # a country that is also a town's name ("Georgia", "Lebanon, PA") means the country; "West Bank" is
                    # both, and so is a capital that has the country's name ("Djibouti") or is plainly meant ("Kuwait airport")
                    capital = [e for e in ents or () if e.get("cap") and e["cc"] == iso and (norm(e["n"]) == key or nxt in PLACE_NEXT)]
                    if not ents or not hint and not capital and not any(e.get("x") for e in ents):
                        continue
                    ents = [e for e in ents if e.get("x")] + capital if not hint else ents
                if hint:
                    hinted.add(key)
                ww = w * (0.5 if dl and start < dl.end() else 1)          # a dateline is where the reporter is
                if SPEECH.match(t[start + len(key):start + len(key) + 30]) or SPEECH.match(t[start:].split(" ", 1)[-1][:30]):
                    ww *= 0.3                                              # "Moscow says", "Kyiv's relations with"
                loc = bool(LOCATIVE.search(t[max(0, start - 12):start]))
                if loc:
                    ww *= 1.25                                             # "in Kyiv", "near Aden"
                found.append((ww, start, key, ents, loc))
        allowed = named | ({gcc} if gcc else set())
        for a in actors or ():  # GDELT's actors widen the countries a named place may be in, but don't count as named
            iso = self.countries.get(norm(a)) if a else None
            if iso:
                allowed.add(iso)
        parts = [p.strip() for p in (gname or "").split(",")]
        first = parts[0]
        alltext = " " + " ".join(norm(t) for t, _w, _c in texts) + " "
        # GDELT's town is real if GeoNames has it there, or if the article names it as a place ("at Porgera Valley", a mine
        # town GeoNames lacks; not "the Oreshnik missile")
        gok = bool(self.near_named(first, glat, glon)) or len(first) >= 4 and any(
            re.search(r"\b(?:in|at|near|outside|around|from|of)\s+(?:the\s+)?" + re.escape(first) + r"\b|\b" + re.escape(first)
                      + r"\s+(?:valley|district|region|province|town|village|city|area|county|camp|airport|port)\b", t, re.I)
            for t, _w, _c in texts)
        if len(parts) >= 3 and len(norm(parts[1])) >= 4 and f" {norm(parts[1])} " in alltext and f" {norm(first)} " in alltext:
            # the text names GDELT's town and its region ("Vienna", "Georgia"): GDELT knows that village, trust it
            gok = True
            named.discard(self.countries.get(norm(parts[1]), ""))
        score = {}
        for ww, start, key, ents, loc in found:
            if gok and key in (norm(first), strip_article(norm(first))) and key not in hinted:
                continue  # the text names GDELT's own town
            region = key in hinted   # "Voronezh region" anywhere makes every "Voronezh" the region
            pool = [e for e in ents if (e["k"] != "city") == region] or ents
            ok = [e for e in pool if not e["cc"] or e["cc"] in allowed]
            if not ok and not loc:
                continue
            if not ok:
                ok = [e for e in pool if e["k"] == "city" and (e["pop"] >= 300_000 or e.get("cap"))]
                ww *= 0.85  # a big city abroad ("Russian strikes on Kyiv" placed in Russia) counts less than one at home
                if not ok:
                    continue
            near = [e for e in ok if hav(glat, glon, e["lat"], e["lon"]) <= 300]
            pick = (min(near, key=lambda e: hav(glat, glon, e["lat"], e["lon"]) - (e["r"] if e["k"] != "city" else 0))
                    if near else max(ok, key=lambda e: (e["k"] == "city", e["pop"] * (3 if e["cc"] == gcc else 1)
                                                         / (20 if ents.index(e) >= len(self.names.get(key, ())) else 1))))
            if pick["k"] == "region" and not region:  # "Kharkiv" alone is the city, when there is a sizeable one
                cities = [e for e in self.names.get(key, []) if e["k"] == "city" and e["a1"] == pick["a1"] and e["pop"] >= 50_000]
                pick = max(cities, key=lambda e: e["pop"]) if cities else pick
            s = score.setdefault(id(pick), [0.0, pick, 0])
            if s[2] < 3:
                s[0] += ww / (1 + 0.5 * s[2])
            s[2] += 1
        for s in score.values():
            e = s[1]
            if hav(glat, glon, e["lat"], e["lon"]) <= max(100, e["r"]):
                s[0] += 1.5  # GDELT agrees, more or less
        cands = sorted(score.values(), key=lambda s: (s[0], {"city": 2, "region": 1}.get(s[1]["k"], 0), s[1]["pop"]), reverse=True)
        # GDELT's own town, when it is real and in a country the story is about, stands unless the text names somewhere else
        # more strongly than in passing
        home_ok = gok and (not named or gcc in named)
        if cands and cands[0][0] >= (2.0 if home_ok else 1.0):  # a dateline alone (0.6) never moves a story
            best, e = cands[0], cands[0][1]
            if e["k"] != "city":  # a town the text names inside that region or sea is the more exact place
                inside = [s for s in cands[1:] if s[1]["k"] == "city" and s[0] >= 1 and
                          (e["a1"] and s[1]["a1"] == e["a1"] or hav(e["lat"], e["lon"], s[1]["lat"], s[1]["lon"]) <= e["r"])]
                if inside:
                    e = inside[0][1]
            d = hav(glat, glon, e["lat"], e["lon"])
            if e["k"] == "city":
                if d <= 10:
                    return {"n": None, "cc": e["cc"], "lat": glat, "lon": glon, "geo": "text", "moved_km": 0}
                return {"n": self.label(e), "cc": e["cc"], "lat": e["lat"], "lon": e["lon"], "geo": "text", "moved_km": round(d)}
            # GDELT's place inside the region or sea the text names: keep it (GDELT knows villages GeoNames 5000 doesn't),
            # under the region's name unless the town is a real one
            g_a1 = norm(parts[1]) if len(parts) >= 3 else ""
            if d <= e["r"] or g_a1 and e["k"] == "region" and g_a1 in (norm(e["n"]), re.sub(r"\s+" + REGION_WORD + "$", "", norm(e["n"]))):
                return {"n": None if gok else self.label(e), "cc": e["cc"] or gcc, "lat": glat, "lon": glon, "geo": "text", "moved_km": 0}
            if e["k"] == "sea" and named and e["r"] >= 300:
                # "drones hit more Black Sea ships" from a story about Bulgaria: that country's port nearest the sea's middle
                ports = [c for c in self.coast(named) if hav(e["lat"], e["lon"], c["lat"], c["lon"]) <= e["r"] + 150]
                if ports:
                    c = min(ports, key=lambda c: hav(e["lat"], e["lon"], c["lat"], c["lon"]))
                    return {"n": f"{e['n']}, off {c['n']}", "cc": c["cc"], "lat": c["lat"], "lon": c["lon"], "geo": "region",
                            "moved_km": round(hav(glat, glon, c["lat"], c["lon"]))}
            return {"n": self.label(e), "cc": e["cc"] or gcc, "lat": e["lat"], "lon": e["lon"], "geo": "region", "moved_km": round(d)}
        if home_ok:
            # "Ukraine attacks Russian oil refinery" placed in Ukraine, "Pakistan hits Afghanistan sites" in Islamabad: the
            # country attacked isn't GDELT's, and the text names no place in it
            tgt = TARGET.search(title or slug or "")
            tc = tgt and self.countries.get(norm(tgt.group(1))) or self.countries.get(re.sub(r"s$", "", norm(tgt.group(1))) if tgt else "")
            if tc and tc != gcc:
                return {"n": None, "cc": gcc, "lat": glat, "lon": glon, "geo": "approx", "moved_km": 0}
            return {"n": None, "cc": gcc, "lat": glat, "lon": glon, "geo": "gdelt", "moved_km": 0}
        if named and gcc not in named:
            return None
        # GDELT's name isn't a town there: the town of that name in a country the story is about, or a region of that name
        key = norm(first)
        homs = [e for e in (self.names.get(key) or self.names.get(strip_article(key)) or []) if e["cc"] in allowed]
        if homs:
            e = max(homs, key=lambda e: (e["k"] == "city", e["pop"]))
            d = hav(glat, glon, e["lat"], e["lon"])
            if e["k"] != "city" and d <= e["r"]:
                return {"n": None, "cc": gcc, "lat": glat, "lon": glon, "geo": "region", "moved_km": 0}
            return {"n": self.label(e), "cc": e["cc"], "lat": e["lat"], "lon": e["lon"],
                    "geo": "gdelt" if e["k"] == "city" else "region", "moved_km": round(d)}
        return {"n": None, "cc": gcc, "lat": glat, "lon": glon, "geo": "approx", "moved_km": 0}


# ---------------------------------------------------------------------- article titles and summaries

def _meta(t, prop):
    for rx in (r'<meta[^>]+(?:property|name)\s*=\s*["\']%s["\'][^>]*?content\s*=\s*"([^"]*)"',
               r"<meta[^>]+(?:property|name)\s*=\s*[\"']%s[\"'][^>]*?content\s*=\s*'([^']*)'",
               r'<meta[^>]+content\s*=\s*"([^"]*)"[^>]*?(?:property|name)\s*=\s*["\']%s["\']'):
        m = re.search(rx % re.escape(prop), t, re.I)
        if m:
            return clean(m.group(1))
    return ""


def clean(s):
    s = html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", s)))
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def site_trim(title):
    """'Riots erupt in Tirana - AP News' -> 'Riots erupt in Tirana'."""
    title = re.sub(r"\s*[-|–—:]\s*(?:www\.)?[\w-]+\.(?:com|net|org|in|co\.\w\w|com\.\w\w|news)$", "", title, flags=re.I)
    parts = re.split(r"\s+[|\-–—]\s+", title)
    while len(parts) > 1 and len(parts[-1]) <= 32 and len(" - ".join(parts[:-1])) >= 20:
        parts.pop()
    return " - ".join(parts)


def prose(p, least=80):
    """Reads like a sentence, not a menu: 'Close Stay ahead with a digital subscription Subscribe Featured newsletters ...'."""
    w = p.split()
    if len(p) < least or len(w) < 8 or "{" in p or p.count("|") >= 2 or re.search(r"\bsubscri(be|ption)\b|\bcookies?\b", p, re.I):
        return False
    caps = sum(x[0].isupper() for x in w)
    return caps <= 0.45 * len(w) and bool(re.search(r"[.!?]", p))


def parse_article(raw):
    """HTML -> {"t": title, "d": summary} (each '' if missing). Text only; nothing in the page is run."""
    t = raw[:400_000]
    title = _meta(t, "og:title") or _meta(t, "twitter:title")
    if not title:
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", t)
        title = clean(m.group(1)) if m else ""
    desc = _meta(t, "og:description") or _meta(t, "description") or _meta(t, "twitter:description")
    if len(desc) < 60:  # first paragraph that reads like prose
        for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", t):
            p = clean(p)
            if prose(p):
                desc = p
                break
    if desc and not prose(desc, 40):
        desc = ""
    return {"t": site_trim(title)[:200], "d": desc[:600]}


class Articles:
    """og:title / og:description per article URL, cached in cache_dir/articles/<sha1[:2]>/<sha1>.json."""

    def __init__(self, cache_dir, log=print, max_new=400, timeout=8, offline=False):
        self.dir = os.path.join(cache_dir, "articles")
        self.log, self.max_new, self.timeout, self.offline = log, max_new, timeout, offline
        self.mem, self.fetched, self.failed = {}, 0, 0

    def _path(self, url):
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return os.path.join(self.dir, h[:2], h + ".json")

    def get(self, url):
        """Cached article meta, or None if never fetched or unavailable."""
        if url in self.mem:
            return self.mem[url]
        p = self._path(url)
        v = None
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    v = json.load(f)
            except (OSError, ValueError):
                v = None
        if v and v.get("d") and not prose(v["d"], 40):  # cached before the menu check
            v["d"] = ""
        if v and v.get("t"):
            v["t"] = site_trim(v["t"])
        self.mem[url] = v
        return v if v and (v.get("t") or v.get("d")) else None

    def _fetch(self, url):
        try:
            body, cs = _get(url, self.timeout, 600_000)
            v = parse_article(body.decode(cs if cs.lower() in ("utf-8", "iso-8859-1", "windows-1252", "latin-1") else "utf-8", "replace"))
        except Exception as e:  # noqa: BLE001 - dead links, paywalls and bot walls are common
            v = {"t": "", "d": "", "err": type(e).__name__}
        v["at"] = int(time.time())
        p = self._path(url)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(v, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)
        return url, v

    def prefetch(self, urls):
        if self.offline:
            return
        todo = []
        for u in dict.fromkeys(u for u in urls if u and u.startswith("http")):
            if u not in self.mem and not os.path.exists(self._path(u)):
                todo.append(u)
        todo = todo[:max(0, self.max_new - self.fetched)]
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=16) as ex:
            for u, v in ex.map(self._fetch, todo):
                self.mem[u] = v
                self.fetched += 1
                self.failed += 1 if v.get("err") else 0

    def prune(self, days=400):
        """Drop cache files older than `days` (the storyboard's year plus a margin)."""
        cut = time.time() - days * 86400
        n = 0
        for root, _dirs, files in os.walk(self.dir):
            for fn in files:
                p = os.path.join(root, fn)
                try:
                    if os.path.getmtime(p) < cut:
                        os.remove(p)
                        n += 1
                except OSError:
                    pass
        return n
