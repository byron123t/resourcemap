"""Headless visual check: renders the map at several zoom levels and saves PNGs to tools/shots/.

Needs: pip install playwright && playwright install chromium
Run a server first:  python -m http.server 8765 --directory site
Then:                python tools/screenshot.py [--center 12,46] [--zooms 1.5,2.4,3.4,4.5,6]
Scenario:            python tools/screenshot.py --hash "sim=choke.hormuz.100" --center 56,26 --name hormuz

The page exposes the map as window.__atlasMap when the URL has #debug.
Uses the local vendor/maplibre-gl.js instead of the CDN so it works offline.
WebGL runs in software (SwiftShader), so each frame can take several seconds.
"""
import argparse, asyncio, os
from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
JS = open(os.path.join(HERE, "..", "site", "vendor", "maplibre-gl.js"), encoding="utf-8").read()

async def main(a):
    os.makedirs(os.path.join(HERE, "shots"), exist_ok=True)
    lng, lat = map(float, a.center.split(","))
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-angle=swiftshader-webgl", "--enable-unsafe-swiftshader", "--in-process-gpu"])
        pg = await b.new_page(viewport={"width": a.width, "height": a.height}, color_scheme=a.scheme)
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await pg.route("**/cdn.jsdelivr.net/**", lambda r: r.fulfill(body=JS, content_type="application/javascript"))
        await pg.goto(f"{a.url}#debug" + (f"&{a.hash.lstrip('#&')}" if a.hash else ""), wait_until="domcontentloaded")
        await pg.wait_for_function("window.__atlasMap && window.__atlasMap.loaded()", timeout=60000)
        if "sim=" in a.hash:   # wait for the scenario model to finish its first run
            await pg.wait_for_function("/Re-routed|None of them|Today's network|failed|Couldn't/.test(document.getElementById('simSum').textContent)", timeout=120000)
            print("scenario:", await pg.evaluate("document.getElementById('simSum').textContent"))
        await pg.wait_for_timeout(6000)
        for z in [float(x) for x in a.zooms.split(",")]:
            await pg.evaluate(f"(() => {{ const m = window.__atlasMap; m.jumpTo({{ center: [{lng}, {lat}], zoom: {z} }}); m.fire('zoomend'); }})()")
            await pg.wait_for_timeout(3000)
            path = os.path.join(HERE, "shots", f"{a.name + '_' if a.name else ''}z{z}.png")
            await pg.screenshot(path=path, timeout=60000)
            print("saved", path)
        print("page errors:", [e for e in errors if "fonts.g" not in e][:10])
        await b.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8765/index.html")
    ap.add_argument("--center", default="12,46")
    ap.add_argument("--zooms", default="1.5,2.4,3.4,4.5,6")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=820)
    ap.add_argument("--scheme", default="light", choices=["light", "dark"])
    ap.add_argument("--hash", default="", help="extra URL hash tokens, e.g. sim=choke.hormuz.100 or view=oil")
    ap.add_argument("--name", default="", help="prefix for the PNG names")
    asyncio.run(main(ap.parse_args()))
