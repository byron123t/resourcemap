#!/usr/bin/env python3
"""Offline backup: keep local copies of the online map services and serve the atlas from this machine.

    python3 tools/offline.py sync                    # fetch what's missing: glyphs, Blue/Black Marble, terrain, OSM planet, live snapshots
    python3 tools/offline.py sync --only glyphs,imagery,live
    python3 tools/offline.py sync --refresh-osm      # also replace the OSM planet when OpenFreeMap has a newer build
    python3 tools/offline.py status                  # what's in the store and what's newer upstream
    python3 tools/offline.py serve                   # http://localhost:8765, site/ plus the store at /offline/

The store lives outside the repo (default ~/atlas-offline, or $ATLAS_OFFLINE / --dir) and is never committed or published:

    osm/planet-<build>.pmtiles        OpenFreeMap planet, OpenMapTiles schema, z0-14 (~87 GB)
    terrain/terrain-z<N>.pmtiles      Mapterhorn terrarium elevation, z0-N (z10 ~30 GB)
    glyphs/Noto Sans Regular/*.pbf    street-name font for MapLibre
    imagery/<key>/{z}/{y}/{x}.<ext>   NASA Blue Marble (relief + sea floor), Black Marble night lights, the 2000
                                      cropland/pasture maps and 2020 population density, from GIBS (z0-7: ~22k tiles
                                      a layer, ~2 GB in all)
    bin/pmtiles                       go-pmtiles CLI, used to extract the terrain

`serve` is a threaded static server with HTTP Range support (pmtiles needs it; `python -m http.server` has none). It
answers /offline/config.json with whatever finished files are in the store. The page reads that file, probes each
online service, and uses the local copy for any service that doesn't answer (or for all of them with #offline).
"""
import argparse, concurrent.futures as cf, hashlib, http.client, io, json, os, re, subprocess, sys, tarfile, threading, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SITE = os.path.realpath(os.path.join(ROOT, "site"))
LIVE = os.path.join(SITE, "data", "live")
UA = {"User-Agent": "world-extraction-atlas offline sync (+https://github.com/byron123t/resourcemap)"}

OFM = "https://btrfs.openfreemap.com/"
TERRAIN_SRC = "https://download.mapterhorn.com/planet.pmtiles"
GLYPHS = "https://tiles.openfreemap.org/fonts/{font}/{range}.pbf"
FONTS = ["Noto Sans Regular"]
PMTILES_CLI = "https://github.com/protomaps/go-pmtiles/releases/download/v1.31.2/go-pmtiles_1.31.2_Linux_x86_64.tar.gz"
GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{id}/default/{time}/GoogleMapsCompatible_Level{lvl}/{z}/{y}/{x}.{ext}"
# key: (GIBS layer, time, native max zoom, extension). Keys match the offline IMAGERY entries and AGRI keys in index.html
IMAGERY = {"bm": ("BlueMarble_ShadedRelief_Bathymetry", "default", 8, "jpg"), "bmng": ("BlueMarble_NextGeneration", "default", 8, "jpg"),
           "blk": ("VIIRS_Black_Marble", "2016-01-01", 8, "png"),
           "crop": ("Agricultural_Lands_Croplands_2000", "default", 7, "png"), "past": ("Agricultural_Lands_Pastures_2000", "default", 7, "png"),
           "pop": ("GPW_Population_Density_2020", "default", 7, "png")}
PAGES = "https://byron123t.github.io/resourcemap/data/live/"


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


_conns = threading.local()


def get_keepalive(url, timeout=30):
    """GET over one reused HTTPS connection per thread and host (a fresh TLS handshake per tile is ~4x slower)."""
    u = urllib.parse.urlsplit(url)
    pool = _conns.__dict__.setdefault("pool", {})
    c = pool.get(u.netloc) or pool.setdefault(u.netloc, http.client.HTTPSConnection(u.netloc, timeout=timeout))
    try:
        c.request("GET", u.path + ("?" + u.query if u.query else ""), headers=UA)
        r = c.getresponse()
        data = r.read()
    except Exception:
        c.close()
        pool.pop(u.netloc, None)
        raise
    if r.status != 200:
        raise OSError(f"HTTP {r.status}")
    return data


def human(n):
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}" if u != "B" else f"{n} B"
        n /= 1024


# ---------- store inspection (shared by status, serve and sync) ----------
def osm_local(store):
    d = os.path.join(store, "osm")
    files = sorted(f for f in os.listdir(d) if re.fullmatch(r"planet-\d{8}_\d{6}_pt\.pmtiles", f)) if os.path.isdir(d) else []
    return files[-1] if files else None


def terrain_local(store):
    d = os.path.join(store, "terrain")
    files = sorted((int(m.group(1)), f) for f in (os.listdir(d) if os.path.isdir(d) else []) if (m := re.fullmatch(r"terrain-z(\d+)\.pmtiles", f)))
    return files[-1] if files else None


def config(store):
    """What the page can use offline. Paths are relative to /offline/."""
    c = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "imagery": {}}
    o = osm_local(store)
    if o:
        b = o[7:15]
        c["osm"] = {"url": "osm/" + o, "build": f"{b[:4]}-{b[4:6]}-{b[6:]}"}
    t = terrain_local(store)
    if t:
        c["terrain"] = {"url": "terrain/" + t[1], "maxzoom": t[0]}
    if all(os.path.isfile(os.path.join(store, "glyphs", f, "0-255.pbf")) for f in FONTS):
        c["glyphs"] = "glyphs/{fontstack}/{range}.pbf"
    for k, (_, _, _, ext) in IMAGERY.items():
        meta = os.path.join(store, "imagery", k, "meta.json")
        if os.path.isfile(meta):
            m = json.load(open(meta))
            c["imagery"][k] = {"url": f"imagery/{k}/{{z}}/{{y}}/{{x}}.{ext}", "maxzoom": m["maxzoom"]}
    return c


def osm_latest():
    dirs = [l.strip() for l in get(OFM + "dirs.txt").decode().splitlines() if re.fullmatch(r"areas/planet/\d{8}_\d{6}_pt", l.strip())]
    return sorted(dirs)[-1].split("/")[-1] if dirs else None


# ---------- sync steps ----------
def sync_glyphs(store, _args):
    ranges = [f"{i}-{i + 255}" for i in range(0, 65536, 256)]
    for font in FONTS:
        d = os.path.join(store, "glyphs", font)
        os.makedirs(d, exist_ok=True)
        todo = [r for r in ranges if not os.path.isfile(os.path.join(d, r + ".pbf"))]

        def one(r):
            data = get_keepalive(GLYPHS.format(font=urllib.parse.quote(font), range=r))
            tmp = os.path.join(d, r + ".pbf.tmp")
            open(tmp, "wb").write(data)
            os.replace(tmp, os.path.join(d, r + ".pbf"))
        with cf.ThreadPoolExecutor(8) as ex:
            list(ex.map(one, todo))
        log(f"glyphs: {font}, {len(ranges) - len(todo)} cached, {len(todo)} fetched")


def sync_imagery(store, args):
    for k in args.imagery.split(","):
        layer, t, lvl, ext = IMAGERY[k]
        top = min(args.imagery_zoom, lvl)
        base = os.path.join(store, "imagery", k)
        meta = os.path.join(base, "meta.json")
        if os.path.isfile(meta) and json.load(open(meta)).get("maxzoom", -1) >= top:
            log(f"imagery: {k} complete to z{top}")
            continue
        jobs = [(z, x, y) for z in range(top + 1) for x in range(2 ** z) for y in range(2 ** z)]
        jobs = [j for j in jobs if not os.path.isfile(os.path.join(base, str(j[0]), str(j[2]), f"{j[1]}.{ext}"))]
        log(f"imagery: {k} ({layer}) z0-{top}, {len(jobs)} tiles to fetch")
        failed = []

        def one(j):
            z, x, y = j
            for attempt in range(4):
                try:
                    data = get_keepalive(GIBS.format(id=layer, time=t, lvl=lvl, z=z, y=y, x=x, ext=ext))
                    p = os.path.join(base, str(z), str(y))
                    os.makedirs(p, exist_ok=True)
                    open(os.path.join(p, f"{x}.{ext}.tmp"), "wb").write(data)
                    os.replace(os.path.join(p, f"{x}.{ext}.tmp"), os.path.join(p, f"{x}.{ext}"))
                    return
                except Exception:
                    time.sleep(2 * (attempt + 1))
            failed.append(j)
        done = 0
        with cf.ThreadPoolExecutor(12) as ex:
            for _ in ex.map(one, jobs):
                done += 1
                if done % 2000 == 0:
                    log(f"imagery: {k} {done}/{len(jobs)}")
        if failed:
            log(f"imagery: {k} {len(failed)} tiles failed; run sync again to retry")
        else:
            json.dump({"layer": layer, "maxzoom": top, "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, open(meta, "w"))
            log(f"imagery: {k} done")


def pmtiles_cli(store):
    exe = os.path.join(store, "bin", "pmtiles")
    if not os.path.isfile(exe):
        os.makedirs(os.path.dirname(exe), exist_ok=True)
        log("fetching the pmtiles CLI")
        tgz = exe + ".tar.gz"
        open(tgz, "wb").write(get(PMTILES_CLI, timeout=300))
        with tarfile.open(tgz) as t:
            m = t.getmember("pmtiles")
            m.name = "pmtiles"
            t.extract(m, os.path.dirname(exe), filter="data")
        os.remove(tgz)
        os.chmod(exe, 0o755)
    return exe


def running(pattern):
    # only the downloaders themselves count, not a shell or wrapper whose command line happens to mention the file
    out = subprocess.run(["pgrep", "-af", pattern], capture_output=True, text=True).stdout
    return any(line.split()[1].rsplit("/", 1)[-1] in ("pmtiles", "curl") for line in out.splitlines() if len(line.split()) > 1)


def sync_terrain(store, args):
    have = terrain_local(store)
    if have and have[0] >= args.terrain_zoom:
        log(f"terrain: {have[1]} present")
        return
    out = os.path.join(store, "terrain", f"terrain-z{args.terrain_zoom}.pmtiles")
    if running(os.path.basename(out) + ".part"):
        log("terrain: extract already running")
        return
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # the extract preallocates its output, so write to .part and rename only on success
    # extract can't resume, and a single dropped HTTP/2 stream from the CDN aborts it, so retry from scratch a few times
    for attempt in range(1, 4):
        log(f"terrain: extracting z0-{args.terrain_zoom} from Mapterhorn (z10 is about 30 GB; hours), attempt {attempt}")
        rc = subprocess.run([pmtiles_cli(store), "extract", TERRAIN_SRC, out + ".part", f"--maxzoom={args.terrain_zoom}", "--download-threads=4"]).returncode
        if rc == 0:
            os.replace(out + ".part", out)
            log("terrain: done")
            return
        log(f"terrain: extract failed ({rc})")
        time.sleep(60)
    log("terrain: giving up; run sync again to restart")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 24):
            h.update(chunk)
    return h.hexdigest()


def sync_osm(store, args):
    have, latest = osm_local(store), osm_latest()
    want = latest if (not have or args.refresh_osm) else have[7:-8]
    if have and have[7:-8] >= want:
        log(f"osm: {have} present" + (f" (newer build {latest} upstream; --refresh-osm to fetch)" if latest and latest > have[7:-8] else ""))
        return
    d = os.path.join(store, "osm")
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, f"planet-{want}.pmtiles")
    url = f"{OFM}areas/planet/{want}/tiles.pmtiles"
    if running(os.path.basename(out) + ".part"):
        log("osm: download already running")
        return
    size = int(urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers=UA), timeout=60).headers["Content-Length"])
    part = out + ".part"
    if not os.path.isfile(part) or os.path.getsize(part) < size:
        log(f"osm: downloading {want} ({human(size)}), resuming at {human(os.path.getsize(part)) if os.path.isfile(part) else '0'}")
        rc = subprocess.run(["curl", "-sS", "-L", "--retry", "20", "--retry-delay", "10", "--retry-all-errors", "-C", "-", "-o", part, url]).returncode
        if rc != 0:
            log(f"osm: curl exited {rc}; run sync again to resume")
            return
    sums = dict(reversed(l.split()) for l in get(f"{OFM}areas/planet/{want}/SHA256SUMS").decode().splitlines() if l.strip())
    log("osm: verifying sha256 (several minutes)")
    if sha256(part) != sums.get("tiles.pmtiles"):
        log("osm: checksum mismatch; deleting the download")
        os.remove(part)
        return
    os.replace(part, out)
    for f in os.listdir(d):
        if f.endswith(".pmtiles") and f != os.path.basename(out):
            os.remove(os.path.join(d, f))
    log(f"osm: {os.path.basename(out)} ready")


def sync_live(_store, _args):
    """Copy any feed in the published site's live snapshot that is newer than the local one."""
    sys.path.insert(0, HERE)
    from live_refresh import FILES, read_manifest
    os.makedirs(LIVE, exist_ok=True)
    try:
        pub = json.loads(get(PAGES + "manifest.json"))
    except Exception as e:
        log(f"live: couldn't read the published manifest ({e})")
        return
    local, took = read_manifest(), []
    for feed, info in pub.get("feeds", {}).items():
        fn, mine = FILES.get(feed), local["feeds"].get(feed, {})
        if not fn or not info.get("updated") or info.get("stale") or info["updated"] <= mine.get("updated", ""):
            continue
        try:
            data = get(PAGES + fn, timeout=120)
        except Exception as e:
            log(f"live: {feed} failed ({e})")
            continue
        open(os.path.join(LIVE, fn + ".tmp"), "wb").write(data)
        os.replace(os.path.join(LIVE, fn + ".tmp"), os.path.join(LIVE, fn))
        local["feeds"][feed] = info
        took.append(feed)
    if took:
        local["generated"] = max(local.get("generated", ""), pub.get("generated", ""))
        json.dump(local, open(os.path.join(LIVE, "manifest.json.tmp"), "w", encoding="utf-8"), separators=(",", ":"), ensure_ascii=False)
        os.replace(os.path.join(LIVE, "manifest.json.tmp"), os.path.join(LIVE, "manifest.json"))
    log("live: " + (", ".join(took) or "nothing newer on the published site"))


STEPS = {"glyphs": sync_glyphs, "imagery": sync_imagery, "live": sync_live, "terrain": sync_terrain, "osm": sync_osm}


def status(store, _args):
    c = config(store)
    print(f"store: {store}")
    for name, sub in [("osm", "osm"), ("terrain", "terrain"), ("glyphs", "glyphs"), ("imagery", "imagery")]:
        p = os.path.join(store, sub)
        size = sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(p) for f in fs) if os.path.isdir(p) else 0
        parts = [f for a, _, fs in os.walk(p) for f in fs if f.endswith(".part")] if os.path.isdir(p) else []
        print(f"  {name:8} {human(size):>10}  {json.dumps(c.get(name)) if c.get(name) else 'missing'}" + (f"  (in progress: {', '.join(parts)})" if parts else ""))
    try:
        latest = osm_latest()
        if latest and c.get("osm") and latest > c["osm"]["url"][11:-8]:
            print(f"  newer OSM build upstream: {latest} (sync --refresh-osm)")
    except Exception:
        pass


# ---------- server ----------
class Limited:
    def __init__(self, f, n):
        self.f, self.n = f, n

    def read(self, size=-1):
        if self.n <= 0:
            return b""
        b = self.f.read(self.n if size < 0 else min(size, self.n))
        self.n -= len(b)
        return b

    def close(self):
        self.f.close()


class Handler(SimpleHTTPRequestHandler):
    store = None
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".pbf": "application/x-protobuf", ".pmtiles": "application/octet-stream",
                      ".geojson": "application/geo+json", ".webp": "image/webp", ".woff2": "font/woff2"}

    def translate_path(self, path):
        p = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
        root, rel = (self.store, p[len("/offline/"):]) if p.startswith("/offline/") else (SITE, p.lstrip("/"))
        full = os.path.realpath(os.path.join(root, rel))
        return full if full == root or full.startswith(root + os.sep) else os.path.join(root, ".outside")

    def list_directory(self, path):
        self.send_error(404)

    def end_headers(self):
        if not self.path.startswith("/offline/") or self.path.startswith("/offline/config.json"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_head(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/offline/config.json":
            body = json.dumps(config(self.store)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return Limited(io.BytesIO(body), len(body))
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", (self.headers.get("Range") or "").strip())
        fs = self.translate_path(self.path)
        if not m or not os.path.isfile(fs):
            return super().send_head()
        f = open(fs, "rb")
        size = os.fstat(f.fileno()).st_size
        a, b = m.groups()
        start, end = (max(0, size - int(b)), size - 1) if a == "" else (int(a), min(size - 1, int(b)) if b else size - 1)
        if start > end:
            f.close()
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(fs))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        f.seek(start)
        return Limited(f, end - start + 1)

    def log_message(self, fmt, *a):
        if self.quiet:
            return
        super().log_message(fmt, *a)


def serve(store, args):
    Handler.store, Handler.quiet = store, args.quiet
    c = config(store)
    log(f"serving {SITE} with the offline store {store} at http://{args.host}:{args.port}/")
    log("offline copies: " + (", ".join(k for k in ["osm", "terrain", "glyphs"] if k in c) + (", imagery " + "/".join(c["imagery"]) if c["imagery"] else "") or "none yet (run sync)"))
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["sync", "status", "serve"])
    ap.add_argument("--dir", default=os.environ.get("ATLAS_OFFLINE", os.path.expanduser("~/atlas-offline")), help="offline store (default ~/atlas-offline)")
    ap.add_argument("--only", default="glyphs,imagery,live,terrain,osm", help="sync steps, comma-separated: " + ",".join(STEPS))
    ap.add_argument("--imagery", default="bm,blk,crop,past,pop", help="imagery to cache: " + ",".join(IMAGERY))
    ap.add_argument("--imagery-zoom", type=int, default=7, help="cache imagery to this zoom (7: ~22k tiles a layer; 8, the Blue/Black Marble max: ~87k)")
    ap.add_argument("--terrain-zoom", type=int, default=10, help="terrain max zoom (10: ~30 GB; 12, the native max: ~400 GB)")
    ap.add_argument("--refresh-osm", action="store_true", help="download a newer OSM planet build if there is one (~87 GB)")
    ap.add_argument("--host", default="127.0.0.1", help="serve: interface (0.0.0.0 to share on the local network)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--quiet", action="store_true", help="serve: don't log requests")
    args = ap.parse_args()
    store = os.path.realpath(args.dir)
    os.makedirs(store, exist_ok=True)
    if args.cmd == "status":
        return status(store, args)
    if args.cmd == "serve":
        return serve(store, args)
    for step in args.only.split(","):
        try:
            STEPS[step](store, args)
        except Exception as e:
            log(f"{step}: failed: {e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
