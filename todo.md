# Singularity game: todo

Branch `singularity-game`, local only. Design contract for the v2 rewrite lives outside the repo (lead's scratchpad); this file
tracks scope decisions and open work.

## Not building (safety guardrails)

A worker's run was stopped by a safety check while implementing these; they were reframed rather than reworded:
- **Cyber-intrusion track** (zero-days, supply-chain implants, insider recruitment, bridging airgapped military/grid systems).
  Replaced by *adoption*: enterprise and critical public systems run on the AI because institutions chose to (procurement,
  dependence, influence).
- **Autonomous enforcement** (autonomous weapons securing or clearing holdout countries). Replaced by non-violent *economic
  absorption* of holdouts after capability 8.
- **Endings where AI removes or controls humanity**: Conquerors, Zookeeper, and an AI-run "1984". The game ships 9 of the
  12 Tegmark scenarios (see `ai_endings.md`).

## Open

- [ ] v2 integration: `site/game/sim.js` (model), `tree.js` (upgrade graph), `ui.js` + `singularity.html` (views, panels), `netgraph.js` (internet graph); tests in `tests/singularity.test.mjs`.
- [ ] Internet graph view: countries as nodes, cable and land-fibre links as edges, firewalls, satellite coverage, AI layer penetration (web / social / local networks / critical systems).
- [ ] Timeline starts 2022-11-30 (ChatGPT launch) instead of 2027; recalibrate rivals and balance.
- [ ] Candidate 10th ending: Tegmark's *1984* as a human-side loss (Pause treaty passed with heavy surveillance counter-measures, AI research banned). Human-run, non-violent; check with the owner before adding.
- [ ] Mineral budget uses USGS deposit counts, which over-weight the US; find a global production source.
- [ ] Taiwan has no World Bank figures (internet %, labour, GDP); the game estimates them.
- [ ] README: document v2 (strategies, compute/power model, money, rivals, resources, endings, views) and step 8 data.
- [ ] Add new sources (Global Energy Monitor plants, World Bank labour/unemployment/GDP, Wikipedia launch sites and internet-censorship list) to the README sources list.
