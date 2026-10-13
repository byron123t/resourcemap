#!/usr/bin/env python3
"""Keep site/data/live/ fresh during local development.

    python3 tools/live_refresh.py                 # fetch all live feeds once
    python3 tools/live_refresh.py --every 30      # ...and again every 30 minutes until Ctrl+C
    python3 tools/live_refresh.py --ci            # first copy in any newer feeds from the latest CI
                                                  # `live-data` artifact (needs `gh`), e.g. global ships
    python3 tools/live_refresh.py --ci --only events,fires --archive raw/archive

Each cycle optionally pulls the CI artifact, keeping only feeds whose manifest `updated`
is newer than the local copy, then runs pipeline/live/fetch_live.py with the remaining
options passed through. Ships are skipped (and keep their old snapshot) unless
AISSTREAM_API_KEY is set. Open tabs pick up new snapshots within 10 minutes.
"""
import argparse, io, json, os, shutil, subprocess, sys, time, zipfile
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LIVE = os.path.join(ROOT, "site", "data", "live")
REPO = "byron123t/resourcemap"
FILES = {"events": "events.geojson", "fires": "fires.json", "ships": "ships.geojson", "aircraft": "aircraft.geojson",
         "jets": "jets.geojson", "airliners": "airliners.geojson", "navy": "navy.geojson", "story": "story.json"}


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def read_manifest():
    try:
        with open(os.path.join(LIVE, "manifest.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"feeds": {}}


def pull_ci():
    """Copy feeds from the newest CI artifact that are newer than the local ones."""
    try:
        aid = subprocess.run(["gh", "api", f"repos/{REPO}/actions/artifacts?name=live-data&per_page=1",
                              "--jq", ".artifacts[0].id"], capture_output=True, text=True, timeout=60, check=True).stdout.strip()
        blob = subprocess.run(["gh", "api", f"repos/{REPO}/actions/artifacts/{aid}/zip"],
                              capture_output=True, timeout=300, check=True).stdout
    except Exception as e:
        log(f"CI pull failed: {e}")
        return
    z = zipfile.ZipFile(io.BytesIO(blob))
    ci = json.loads(z.read("manifest.json"))
    local = read_manifest()
    took = []
    for feed, info in ci.get("feeds", {}).items():
        fn = FILES.get(feed)
        mine = local["feeds"].get(feed, {})
        if not fn or fn not in z.namelist() or not info.get("updated") or info.get("stale"):
            continue
        if info["updated"] <= mine.get("updated", "") and not mine.get("stale"):
            continue
        tmp = os.path.join(LIVE, fn + ".tmp")
        with open(tmp, "wb") as f:
            f.write(z.read(fn))
        os.replace(tmp, os.path.join(LIVE, fn))
        local["feeds"][feed] = info
        took.append(f"{feed} ({info['updated'][5:16].replace('T', ' ')}Z)")
    if took:
        local["generated"] = max(local.get("generated", ""), ci.get("generated", ""))
        tmp = os.path.join(LIVE, "manifest.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(local, f, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, os.path.join(LIVE, "manifest.json"))
    log("from CI: " + (", ".join(took) or "nothing newer"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--every", type=float, default=0, help="repeat every N minutes (default: run once)")
    ap.add_argument("--ci", action="store_true", help="pull newer feeds from the latest CI artifact first")
    ap.add_argument("--no-fetch", action="store_true", help="only pull from CI, don't run fetch_live.py")
    args, passthrough = ap.parse_known_args()
    os.makedirs(LIVE, exist_ok=True)
    if args.ci and not shutil.which("gh"):
        ap.error("--ci needs the GitHub CLI (gh) logged in")
    while True:
        t0 = time.time()
        if args.ci:
            pull_ci()
        if not args.no_fetch:
            cmd = [sys.executable, "-I", os.path.join(ROOT, "pipeline", "live", "fetch_live.py")] + passthrough
            log("running " + " ".join(cmd[2:]))
            rc = subprocess.run(cmd, cwd=ROOT).returncode
            log(f"fetch_live.py exited {rc} after {time.time() - t0:.0f} s")
        if not args.every:
            return
        wait = max(60, args.every * 60 - (time.time() - t0))
        log(f"next run in {wait / 60:.0f} min")
        time.sleep(wait)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
