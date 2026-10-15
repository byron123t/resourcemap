# Singularity game: todo

Branch `singularity-game`, local only. Design contract for the v2 rewrite lives outside the repo (lead's scratchpad); this file
tracks scope decisions and open work. A videogame about superintelligence scaling, like plague inc. 

## Big Todos

- **Cyber-intrusion track** (zero-days, supply-chain implants, insider recruitment, bridging airgapped military/grid systems).
  Replaced by *adoption*: enterprise and critical public systems run on the AI because institutions chose to (procurement,
  dependence, influence). Obviously keep things high level, so we're not actually creating anything cyber harmful.
- **Autonomous enforcement** (autonomous weapons securing or clearing holdout countries).
- **Endings where AI removes or controls humanity**: Conquerors, Zookeeper, and an AI-run "1984". The game currently ships 9 of the
  12 Tegmark scenarios and needs to be completed (see `ai_endings.md`).

## Open

- [x] v2 integration: `site/game/sim.js`, `tree.js`, `ui.js` + `singularity.html`, `netgraph.js`; tests in `tests/singularity.test.mjs`.
- [x] Internet graph view: countries as nodes, cable and land-fibre links as edges, firewalls, satellite coverage, AI layer penetration (web / social / local networks / critical systems).
- [x] Timeline starts 2022-11-30 (ChatGPT launch); one model step = 3 days.
- [ ] Candidate 10th ending: Tegmark's *1984* as a human-side loss (Pause treaty passed with heavy surveillance counter-measures, AI research banned). Human-run, non-violent; check with the owner before adding.
- [ ] Mineral budget uses USGS deposit counts, which over-weight the US; find a global production source.
- [ ] Taiwan has no World Bank figures (internet %, labour, GDP); the game estimates them.
- [x] README: v2 documented.
- [x] New sources added to the README sources list.
- [ ] Balance: hard is only winnable with stealth-first play; National Champion and Open Evolution are the hardest strategies on normal. Late game piles up insight (PR campaign and efficiency sprints are the only sinks).
- [ ] Network view on the map still busy at low zoom in Europe and West Africa.
