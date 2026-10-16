# Singularity game: todo

Branch `singularity-game`, local only. Design contract for the v2 rewrite lives outside the repo (lead's scratchpad); this file
tracks scope decisions and open work. A videogame about superintelligence scaling, like plague inc. 

## Big Todos

- [x] **Cyber-intrusion track** (zero-days, supply-chain implants, insider recruitment, bridging airgapped military/grid systems):
  dropped for good and replaced by *adoption*: enterprise and critical public systems run on the AI because institutions chose
  to (procurement, dependence, influence). The test suite asserts there is no hacking track. Keep it high level.
- [x] **Autonomous enforcement**: Autonomous Security Forces (capability track; needs Humanoid Robotics, Defense Procurement or
  Regulatory Capture, capability 8 and a holdout country) unlocks *Secure* after superintelligence: take a country next to your
  presence in 90 days at +8 awareness, +1.5 treaty, and export controls in its land neighbours. Abstract, no combat model.
- [x] **Endings where AI removes or controls humanity**: Conquerors and Zookeeper (wins, from enforcement) and 1984 (a loss,
  human surveillance state as in the book). All 12 Tegmark scenarios ship; see `ai_endings.md`.
- [x] **More doom endings**: Treacherous Turn, Paperclip Maximiser (wins), Someone Else's God, Flash War (losses); 16 endings.
- [x] **Simpler country actions**: campus (auto-builds its power) + one power plant whose type comes from upgrades; satellites and
  orbital datacentres moved to the Compute panel; Absorb/Secure hidden until unlocked. Was 8 buttons per country, now 2 (4 late).
- [x] Speed remembered (localStorage); space resumes at it.
- [x] Breakthrough card drafts (38 cards incl. people/network/energy/research/revenue) replace the PR/sprint/push/diplomacy buttons.
- [x] Buildings give a local stake (adoption, automation, fewer crackdowns, less treaty funding); shovel-ready site bubbles.
- [x] Sovereign-AI grant for starting outside the US and China.
- [x] Endings thresholds loosened (protector, benevolent, treacherous, paperclip, conquerors, zookeeper, 1984); rival endings now
  decided by the nearest challenger at the rival's announcement.
- [x] Pacing: costlier capability levels plus takeoff (training past cap 4, automation past cap 5). Scaling Lab on normal: <3%
  automation until ~day 840, then 3% → 50% in a year. Rivals get the same takeoff (US lab at capability 8 near step 725).
- [ ] Playtest pacing by hand at speed 1; the bots can't judge whether early choices feel meaningful.
- [x] Explanations: money breakdown, runway reason (repayments), capability rate and ETA, awareness sources, treaty funders.
- [x] Superintelligence and full automation need ~4× and ~3.5× more compute; Reallocate action and per-country approval.
- [x] Procedural music and SFX (audio.js), milestone banners, floating pop-up text, founding card draw.
- [x] Network map view decluttered at low zoom (Europe, West Africa).
- [ ] Feel: more friction ideas to try after a playtest: dilemma events (pay or lose approval), local protests at campuses,
  brownout news that hits approval of neighbours.
- [x] Compute contention: global scarcity when you trail the leader; rivals sign per-country exclusives, broken by a Counter-offer.
- [x] Early autonomy upgrades (Global Access Push, Tool Use & Sandboxed Code) and more augment upgrades (9 new, 51 total).
- [x] AI Executive delegation; unaligned superintelligence takes over, self-optimises and slows rival labs (gatekeeper).
- [x] Crisis draws (price war, talent raid, compute crunch, lawsuit, leak, investor ultimatum) early game.
- [x] Nationalisation lifeline when nearly broke (becomes a state lab).
- Note: disinfo/social-manipulation, military-command integration, cyber-intrusion and wet-lab automation were requested but not
  built; they were declined on safety grounds. Keep the game's harm framing abstract (economics, compute, governance).
- [ ] paperclip/rivalgod/flashwar are rare, covered only by the ending() construction test, not a scripted bot; revisit if they regress.

## Open

- [x] v2 integration: `site/game/sim.js`, `tree.js`, `ui.js` + `singularity.html`, `netgraph.js`; tests in `tests/singularity.test.mjs`.
- [x] Internet graph view: countries as nodes, cable and land-fibre links as edges, firewalls, satellite coverage, AI layer penetration (web / social / local networks / critical systems).
- [x] Timeline starts 2022-11-30 (ChatGPT launch); one model step = 3 days.
- [x] 1984 ending as a human-side loss (paused before capability 6 under 8+ counter-measures or 6+ moratoriums).
- [x] Mineral budget from World Bank mineral rents (NY.GDP.MINR.RT.ZS × GDP, ~$930 bn/yr, game-scaled ×11.5 so the budget stayed the same size). Humanoid mining scales with presence in mining countries. USGS deposit counts stay in world.json (`min`) but are unused.
- [x] Taiwan figures hand-copied in step 8 (DGBAS 2024 labour/unemployment, TWNIC 2024 internet, GDP 2024). Still no secure-server count (`srv`).
- [x] README: v2 documented.
- [x] New sources added to the README sources list.
- [x] Balance pass: new insight sinks (Research push, Treaty diplomacy; Absorb/Secure cost more insight), National Champion's
  diplomats slow the treaty 12%. Bots on hard now win with Recursive RL (2/4) and National Champion (3/4), not only stealth;
  National Champion on normal went from 2/6 to 4/4 wins. Normal wins come ~10% sooner (still in the 450-800 step window).
- [ ] Balance: Scaling Lab and Embodied still never win on hard with the bot (paused at capability ~7.5). Probably fine for
  hard; revisit after human playtests. Open Evolution's difficulty on normal is a human-play report the bot doesn't reproduce.
