"""Link-preview image (site/og.jpg, 1200x630) and launch stills for posts (tools/shots/launch/).

Needs: pip install playwright pillow && playwright install chromium
Run a server first:  python -m http.server 8765 --directory site   (live/ data makes the storyboard stills richer)
Then:                python tools/og_image.py            # site/og.jpg only
                     python tools/og_image.py --stills   # also tools/shots/launch/*.png (1600x1000 and phone)

The page loads with the welcome card marked as seen and the panel hidden where that reads better.
WebGL runs in software (SwiftShader), so each shot takes a while.
"""
import argparse, asyncio, os
from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
# name, viewport, hash, javascript to run after load, seconds to wait
OG = ("og", (1200, 630), "view=shipping&sim=choke.hormuz.100&at=58,22,2.6", "document.getElementById('toggleSheet').click()", 20)
STILLS = [
    ("hormuz-scenario", (1600, 1000), "view=shipping&sim=choke.hormuz.100&at=60,20,2.4", "", 25),
    ("gulf-oil-gas", (1600, 1000), "view=oil&at=51,27,5.2", "document.getElementById('toggleSheet').click()", 15),
    ("storyboard-week", (1600, 1000), "view=overview", "[...document.querySelectorAll('.seg.mode button')].find(b => b.dataset.mode === 'story').click()", 15),
    ("europe-power-night", (1600, 1000), "view=grid&at=10,49,4.6", "document.getElementById('toggleSheet').click()", 15),
    ("taiwan-chips", (1600, 1000), "view=chips&at=121,24,6.8", "", 15),
    ("thermal-riyadh", (1600, 1000), "view=oil&at=46.7,24.7,10", "document.querySelector('[data-style=thermal]').click()", 25),
    ("phone-scenario", (390, 844), "view=shipping&sim=choke.hormuz.100", "", 25),
]

async def shot(b, base, name, size, hash_, js, wait, path):
    ctx = await b.new_context(viewport={"width": size[0], "height": size[1]}, device_scale_factor=1 if size[0] > 600 else 2,
                              is_mobile=size[0] < 600, has_touch=size[0] < 600)
    await ctx.add_init_script("try { localStorage.setItem('atlas.welcomed', '1') } catch (e) {}")
    pg = await ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    await pg.goto(f"{base}#debug&{hash_}", wait_until="domcontentloaded")
    await pg.wait_for_function("window.__atlasMap && window.__atlasMap.loaded()", timeout=90000)
    await pg.wait_for_timeout(4000)
    if js:
        await pg.evaluate(js)
    await pg.wait_for_timeout(wait * 1000)
    # the keyboard hint bar is for people at the page, not for pictures of it
    await pg.add_style_tag(content=".key-hints { display: none !important; }" + (" .maplibregl-ctrl-bottom-right { display: none !important; }" if name == "og" else ""))
    await pg.screenshot(path=path)
    print("saved", path, "errors:", errors[:3])
    await ctx.close()

async def main(a):
    base = a.url.rstrip("/") + "/"
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-angle=swiftshader-webgl", "--enable-unsafe-swiftshader", "--in-process-gpu"])
        n, size, h, js, w = OG
        png = os.path.join(HERE, "shots", "og.png"); os.makedirs(os.path.dirname(png), exist_ok=True)
        await shot(b, base, n, size, h, js, w, png)
        # a JPEG keeps the preview small (link unfurlers give up on big images)
        from PIL import Image
        Image.open(png).convert("RGB").save(os.path.join(HERE, "..", "site", "og.jpg"), quality=84, optimize=True, progressive=True)
        if a.stills:
            out = os.path.join(HERE, "shots", "launch"); os.makedirs(out, exist_ok=True)
            for n, size, h, js, w in STILLS:
                await shot(b, base, n, size, h, js, w, os.path.join(out, n + ".png"))
        await b.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default="http://localhost:8765/")
    ap.add_argument("--stills", action="store_true", help="also save launch stills to tools/shots/launch/")
    asyncio.run(main(ap.parse_args()))
