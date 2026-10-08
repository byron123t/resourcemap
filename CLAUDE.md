# Notes for Claude Code

- The app is one file: `site/index.html`. The map logic lives in the second `<script>` block. Read the README section "How the page works" before editing.
- Serve with `python -m http.server 8765 --directory site`. Don't open it as `file://`, because `fetch()` of `data/` fails there.
- **Verify visual changes.** Run `python tools/screenshot.py` (needs `pip install playwright && playwright install chromium`) and look at `tools/shots/*.png`. Rendering problems such as misaligned glyphs or lattice artifacts only show up at fractional zooms, so check at least 1.5, 2.4, 3.4, 4.5 and 6. Software WebGL is slow, so allow 5–15 s per screenshot.
- `#debug` in the URL exposes the map as `window.__atlasMap`.
- MapLibre is pinned to 4.7.1. Expression gotcha: `["zoom"]` may only appear as the input of a top-level `interpolate` or `step`. Wrap per-feature logic inside the stops; don't multiply a zoom curve.
- Colours are CSS tokens on `:root`, redefined twice for dark mode (the media query and `[data-theme="dark"]`). If you add a layer colour, add the token in all three places and add a line to `repaint()`.
- **Don't edit `site/data/*` by hand.** Change the pipeline and rerun `pipeline/run_all.sh`. The raw inputs are described in `raw/README.md`.
- Open task: see "Known issues" in the README. The top item is making the world-view summary look accurate rather than a grid.
