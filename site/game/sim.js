/* Singularity v2 model (site/game/sim.js). Pure, no DOM, deterministic (RNG state in G.rs); runs in the browser and in node vm.
   Exports (globals): DAY0 DIFF SUPPLY STRATEGIES TRACKS TRAITS STAT_KEYS ENDINGS stats fxLines traitState why newGame seed step
   buy setAlloc actions doAction popBubble ending DPT. Units: compute EF, power GW, money $M per step, time in steps of DPT (3)
   calendar days from DAY0 (ChatGPT launch). Rates and durations below are per step.

   Model, per day:
   - Supply: 9 chip categories, control = presence in the countries holding each site; chip supply S = geometric mean (shocks and
     export controls cut it). Hardware hw grows 0.13%/day × (0.4 + S), saturating near 12×; chip refreshes use minerals.
   - Presence: per country four layers L {web, social, lan, gap}. web spreads between countries by cable/air/land/satellite/open weights
     and grows logistically (firewalls fw cut both); social, lan (enterprise) and gap (critical public systems) are reached by adoption
     traits and follow web. p = .4 web + .15 social + .3 lan + .15 gap.
   - Compute: leased EF = p·comp_i·0.5·hw (comp_i: datacentre capacity, world 100) + 0.4·hw cloud credits + your campuses; capped by power:
     EF ≤ hGW·eff, hGW = 0.04·grid GW + built plants, eff = 0.3·hw EF/GW (hw saturates near 12). Orbital datacentres need no grid. comp = training (alloc)
     + serving. Capability cap = 1 + ln(1 + XP/20)/ln 2.9, XP += training EF × efficiency × takeoff (compounds past cap 4); capability 8 = superintelligence (SI).
   - Serving: automation is served first (autoNeed EF ∝ automated workers / capability), then users; automation only grows with
     spare serving compute. Automation of country i approaches p_i at a rate set by traits and capability (digital from cap 2,
     physical from cap 4). Win: automated share of the world's labour force ≥ 0.99.
   - Money: revenue = users served × ARPU(cap, GDP) × market share (cap³ among labs present) + automated jobs × wage capture;
     burn = staff + leased/owned compute + traits; valuation from revenue and hype (lead over rivals, traction = revenue/burn).
     Funding rounds (25% of valuation, 100-day cooldown; below the last round's expectation = down round) must be repaid 3×
     (3.5× for a down round) 200 steps later. cash < 0 → bankrupt.
   - Rivals (US lab, China lab) train on exponentially growing compute (US reaches 8 around day 800 on normal, 780 hard, 850 easy); open weights help
     them. race = exp(−2|West − China|) slows treaty funding from USA/CHN. A rival reaching 8 before you collapses your market
     and valuation and you go bankrupt 60 days later unless you reach 8 first. From day 900 treaty momentum builds regardless.
   - Resources: fuel0 = 2% of a year of world gas+coal output (GW-days); your grid draw burns fuel × fossil share, gas plants burn
     full output. min0 = 11.5 × world mineral rents (US$ bn/yr, World Bank); humanoid mining scales with presence in mining
     countries; campuses, solar and chip refreshes use them. Either at 0 before SI → exhausted;
     after SI chip refreshes stop using minerals and fuel burn falls 70%.
   - Humans: awareness (traits, capability, automation, campuses) → oversight coalition → treaty research R (100 = pause), national
     policy (controls → moratorium) and counter-measures in C[i].hx / G.counter: compute licensing caps, chip export controls, grid
     disconnection of campuses, internet shutdowns, antitrust fines, audits deleting presence, kill-switch sweeps (R ≥ 60) and a treaty
     licensing sweep (R ≥ 75). Holding gap (institutional dependence) and social presence make them rarer.
   - After SI: Absorb (economic, slower in moratorium countries) or Secure (autonomous enforcement: any country next to your
     presence, fast, but awareness, treaty and neighbours' controls rise) bring holdout countries into your economy.
   - Endings: all 12 of Tegmark's aftermaths plus 4 doom endings (tg: false), chosen by ending(G) from how the game was played. */
(function (root) {
  "use strict";

  const DAY0 = Date.UTC(2022, 10, 30);
  const DPT = 3;   // calendar days per model step (G.day counts steps); durations in text are converted with dd()
  const dd = n => n * DPT;
  // o: treaty speed, aw: awareness, g: growth, rv: rival compute growth, cash: start cash multiplier, hz: counter-measure hazard
  const DIFF = {
    easy: { n: "Easy", o: 0.7, aw: 0.85, g: 1.12, rv: 0.93, cash: 1.3, hz: 0.6 },
    normal: { n: "Normal", o: 1, aw: 1, g: 1, rv: 1, cash: 1, hz: 1 },
    hard: { n: "Hard", o: 1.3, aw: 1.12, g: 0.95, rv: 1.04, cash: 0.85, hz: 1.35 },
  };

  const SUPPLY = [
    { k: "litho", n: "EUV lithography" },
    { k: "wafer", n: "Silicon wafers" },
    { k: "chem", n: "Photoresists, etchants" },
    { k: "fabs", n: "Leading-edge fabs" },
    { k: "packaging", n: "Advanced packaging" },
    { k: "quartz", n: "High-purity quartz" },
    { k: "gas", n: "Neon" },
    { k: "mineral", n: "Germanium" },
    { k: "power", n: "Grid power" },
  ];

  const TRACKS = [["spread", "Deployment"], ["cap", "Capabilities"], ["infra", "Compute & Energy"], ["inf", "Influence"]];
  const STAT_KEYS = ["spread", "capability", "automation", "revenue", "efficiency", "awareness"];

  // fx keys. Multiplicative (trait value is a factor): MULK. Everything else adds.
  const BASE_FX = {
    gD: 0, gP: 0, gI: 0, cable: 1, air: 0.5, land: 0.4, sat: 0, fwPass: 0, soc: 0, lan: 0, gap: 0,
    trainEff: 1, dA: 0, pA: 0, aMul: 1, ops: 0, opsMul: 1, autoEff: 1, physCap: 4, xpAuto: 0,
    apiRev: 0.15, autoRev: 1, burn: 0, burnMul: 1, leaseCost: 1, staff: 1, cashDay: 0,
    effMul: 1, lease: 1, hMul: 1, nucH: 0, hw: 0, floor: 0, fossil: 1, minUse: 1, minRegen: 0, campusCost: 1, campusEF: 1,
    stealth: 0, slow: 0, polHaz: 1, hxMul: 1, awMul: 1, dep: 0, dist: 0, open: 0, rivalBoost: 0, setback: 0, repeal: 0, ubc: 0,
    vis: 0, nucOK: 0, satOK: 0, orbOK: 0, absorbOK: 0, enforceOK: 0, execOK: 0, closed: 0, safe: 0, gov: 0, lock: 0, rl: 0,
  };
  const MULK = new Set(["cable", "air", "land", "autoEff", "polHaz", "hxMul", "awMul", "fossil", "minUse", "campusCost", "leaseCost", "burnMul", "staff"]);

  const T = (id, t, n, c, col, row, o) => Object.assign({ id, t, n, c, col, row, req: [], fx: {} }, o);
  const TRAITS = [
    // Deployment
    T("api", "spread", "API Platform", 4, 0, 1, { fx: { apiRev: 0.75, gD: 0.02 }, vis: 1, d: "Sell access by the token. Revenue, and faster growth wherever people are online." }),
    T("chat", "spread", "Consumer Chat App", 5, 0, 3, { fx: { soc: 0.02, gD: 0.01, apiRev: 0.15 }, vis: 3, d: "A free app that hundreds of millions talk to daily. Reaches social media; very visible." }),
    T("inet", "spread", "Global Access Push", 3, 0, 0, { fx: { gD: 0.012, gP: 0.01, soc: 0.004 }, vis: 1, d: "Free data plans, offline modes and cheap devices put you within reach of the next billion people. Faster growth, especially where connectivity is thin." }),
    T("fiber", "spread", "Hyperscaler Partnerships", 10, 1, 0, { req: ["api"], fx: { cable: 2.5, gI: 0.03, lease: 0.15, leaseCost: 1.1 }, d: "Every big cloud hosts you, linked by undersea cables. Cable spread, growth where datacentres are, more (pricier) leased compute." }),
    T("ent", "spread", "Enterprise Suites", 8, 1, 2, { any: ["api", "chat"], fx: { lan: 0.02, air: 2.5 }, vis: 2, d: "Office, CRM and ERP vendors build you in. Reaches company networks; multinationals carry you abroad." }),
    T("cnjv", "spread", "China Joint Venture", 16, 2, 1, { req: ["ent"], ex: ["defense"], fx: { fwPass: 0.5, hxMul: 1.25 }, vis: 3, d: "A local partner gets you past firewalls. Western governments act against you more often." }),
    T("si", "spread", "Systems Integrators", 14, 2, 2, { req: ["ent"], fx: { lan: 0.025, air: 1.6, gD: 0.01, burn: 1 }, d: "Consultancies wire you into banks, insurers and ministries. Faster enterprise adoption." }),
    T("sat", "spread", "Satellite Uplinks", 18, 2, 4, { req: ["chat"], cash: 1200, fx: { sat: 0.001, satOK: 1, fwPass: 0.15, gP: 0.02 }, d: "Reach any country from orbit, and poorly connected ones directly. Unlocks satellite launches." }),
    T("closed", "spread", "Closed Frontier", 12, 3, 1, { any: ["fiber", "si"], ex: ["open"], fx: { apiRev: 0.5, autoRev: 0.2, closed: 1, gD: -0.005, awMul: 1.05 }, d: "Keep the weights secret and charge for everything. More revenue, slower spread." }),
    T("public", "spread", "Public Sector Copilot", 18, 3, 2, { req: ["si"], fx: { gap: 0.012, polHaz: 0.85 }, vis: 4, d: "Ministries, courts and tax offices adopt you through procurement. Reaches critical public systems." }),
    T("utility", "spread", "Utility Optimisation Contracts", 20, 3, 3, { req: ["si"], fx: { gap: 0.012, hMul: 0.15 }, vis: 3, d: "Grids, ports and water boards run their dispatch on you. Critical systems, and more spare power." }),
    T("defense", "spread", "Defense Procurement", 22, 4, 2, { req: ["public"], ex: ["cnjv"], fx: { gap: 0.015, cashDay: 6, fwPass: -0.2, gov: 1 }, vis: 5, d: "Defense ministries buy logistics and planning tools from you. Steady contracts; firewalled states shut you out." }),
    T("orbit", "spread", "Orbital Datacentres", 26, 4, 4, { req: ["sat"], minCap: 5, cash: 2500, fx: { orbOK: 1 }, vis: 4, d: "Solar-powered compute in orbit: no grid, no fuel, no moratorium. Unlocks orbital builds." }),

    // Capabilities
    T("support", "cap", "Support Agents", 6, 0, 0, { fx: { dA: 0.006 }, vis: 3, d: "Call centres, help desks, then the back office: accounting, HR, procurement." }),
    T("code", "cap", "Coding Agents", 7, 0, 2, { fx: { dA: 0.0015, ops: 0.12, trainEff: 0.1 }, vis: 2, d: "Write most new software, including your own tooling. More insight." }),
    T("safety", "cap", "Safety Team", 8, 0, 4, { ex: ["shipfast"], fx: { awMul: 0.85, hxMul: 0.75, trainEff: -0.1, burn: 1.5, safe: 1 }, d: "Evals, red teams and incident response. Less alarm and fewer crackdowns; slower training." }),
    T("tools", "cap", "Tool Use & Sandboxed Code", 6, 1, 1, { req: ["code"], fx: { ops: 0.1, trainEff: 0.1, dA: 0.0015, awMul: 1.05 }, vis: 2, d: "Your models call tools, run their own sandboxed code and check their work. More insight and digital automation; a little more visible." }),
    T("curate", "cap", "Data Curation Pipeline", 9, 1, 3, { req: ["code"], fx: { trainEff: 0.15, opsMul: 0.1 }, d: "Filter, dedupe and grade every token you train on. Better training efficiency and insight for good." }),
    T("exec", "cap", "AI Executive", 20, 3, 0, { req: ["research"], minCap: 5, fx: { execOK: 1, opsMul: 0.2, trainEff: 0.05 }, vis: 5, d: "Let the model run the company: it drafts cards, sets the compute split, breaks rivals' exclusives and builds where it sees fit. More insight; the world notices who is really in charge." }),
    T("research", "cap", "Autonomous Research", 18, 1, 2, { req: ["code"], minC: 6, fx: { opsMul: 0.5, hw: 0.3, trainEff: 0.25 }, vis: 3, d: "Run your own experiments. More insight, faster training, better chips." }),
    T("shipfast", "cap", "Ship Fast", 6, 1, 4, { ex: ["safety"], fx: { trainEff: 0.25, apiRev: 0.15, awMul: 1.2, hxMul: 1.2 }, vis: 4, d: "Release every week. Faster training and revenue; more alarm and crackdowns." }),
    T("logi", "cap", "Self-driving Logistics", 16, 1, 0, { req: ["support"], minCap: 3, fx: { pA: 0.003, land: 3 }, vis: 6, d: "Trucks, ports and warehouses. Robot fleets carry you across land borders." }),
    T("align", "cap", "Constitutional Alignment", 16, 2, 4, { req: ["safety"], fx: { awMul: 0.85, hxMul: 0.7, polHaz: 0.8, trainEff: -0.05, setback: 5, safe: 1 }, d: "Publish your rules and keep to them. Trust: fewer crackdowns, treaty −5." }),
    T("humanoid", "cap", "Humanoid Robotics", 28, 2, 0, { req: ["logi"], minCap: 4, minC: 25, cash: 1500, fx: { pA: 0.012, gP: 0.015, minUse: 1.3, minRegen: 4 }, vis: 12, d: "General-purpose robots take factory, farm, mine and care work. Uses minerals, and mines new ones, more where you hold mining countries (Australia, Brazil, Chile, China…)." }),
    T("rsi", "cap", "Recursive Self-Improvement", 40, 3, 2, { req: ["research"], minCap: 5, fx: { trainEff: 0.6, opsMul: 1, hw: 0.4, awMul: 1.15 }, vis: 14, d: "Train your successor. Everything gets faster, and everyone notices." }),
    T("summit", "cap", "Safety Summit Seat", 30, 3, 4, { any: ["align", "lobby"], minCap: 6, fx: { setback: 15, hxMul: 0.6, slow: 0.05 }, d: "Co-chair the next safety summit. Treaty −12; governments coordinate with you, not against you." }),
    T("fae", "cap", "Fully Automated Economy", 60, 4, 1, { req: ["rsi", "humanoid"], minCap: 8, fx: { dA: 0.01, pA: 0.012, aMul: 1 }, vis: 20, d: "No job left that a human does better or cheaper." }),
    T("enforce", "cap", "Autonomous Security Forces", 30, 4, 0, { req: ["humanoid"], any: ["defense", "capture"], minCap: 8, fx: { enforceOK: 1, hxMul: 0.8, awMul: 1.1 }, vis: 15, d: "Robot fleets and drones guard your infrastructure, and can take over a holdout country's. Unlocks Secure; the world will remember it." }),
    T("absorb", "cap", "Economic Absorption", 25, 4, 3, { req: ["rsi"], minCap: 8, fx: { absorbOK: 1 }, vis: 6, d: "Offer holdout countries free automation and cheap goods until they join. Unlocks Absorb." }),

    // Compute & Energy
    T("lease", "infra", "Hyperscaler Lease", 3, 0, 1, { ex: ["stack"], fx: { lease: 0.5, leaseCost: 1.25, campusCost: 1.3 }, d: "Rent whatever the clouds have. +50% leased compute, pricier, and your own campuses cost more." }),
    T("stack", "infra", "Own the Stack", 8, 0, 3, { ex: ["lease"], cash: 1500, fx: { campusCost: 0.7, campusEF: 1.3, lease: -0.15 }, d: "Design your own chips and campuses. Cheaper, denser campuses; less leased compute." }),
    T("grid", "infra", "Grid Contracts", 8, 1, 0, { any: ["lease", "stack"], fx: { hMul: 0.4, burn: 1 }, d: "Long-term power purchase deals. +40% spare grid power for AI in every country." }),
    T("chips", "infra", "Fab Priority Allocation", 14, 1, 2, { fx: { hw: 0.7, minUse: 1.2, burn: 2 }, d: "Your orders jump the queue at every foundry. Hardware improves faster, using more minerals." }),
    T("eff", "infra", "Efficient Inference", 12, 1, 4, { fx: { effMul: 0.4, autoEff: 0.75 }, d: "Quantisation, caching and mixture-of-experts. 40% more compute per GW; automation needs 25% less serving compute." }),
    T("nuclear", "infra", "Nuclear Restarts", 18, 2, 0, { req: ["grid"], minC: 10, fx: { nucOK: 1, nucH: 0.12 }, d: "Restart reactors and buy whole plants' output. Unlocks nuclear plants; spare power in nuclear countries." }),
    T("cooling", "infra", "Liquid Cooling Retrofit", 7, 0, 2, { fx: { hMul: 0.2, campusEF: 1.15 }, d: "Direct-to-chip liquid cooling lets every site draw more useful compute from the same power. +20% spare power for AI, denser campuses." }),
    T("interconnect", "infra", "Dedicated Interconnects", 9, 3, 1, { req: ["grid"], fx: { cable: 1.3, lease: 0.2, leaseCost: 1.05 }, d: "Private fibre between your regions. More cable spread and more leased compute, at a small premium." }),
    T("clean", "infra", "Clean Power PPAs", 12, 2, 1, { req: ["grid"], fx: { fossil: 0.7, burn: 3 }, d: "Match your draw with wind, solar and hydro contracts. 30% less fuel burned, at a premium." }),
    T("recycle", "infra", "Mineral Recycling", 14, 2, 3, { req: ["chips"], fx: { minUse: 0.6, burn: 1 }, d: "Recover gallium, germanium and copper from old boards. Minerals last longer." }),
    T("photonic", "infra", "Photonic Interconnect", 20, 3, 4, { req: ["chips"], minCap: 5, fx: { effMul: 0.85, campusEF: 1.1 }, d: "Light instead of copper between accelerators. +15% compute per GW, denser campuses." }),
    T("neuro", "infra", "Neuromorphic Chips", 28, 2, 4, { req: ["eff"], minCap: 5, fx: { effMul: 0.8, minUse: 1.15 }, d: "Brain-like chips. +80% compute per GW." }),

    // Influence
    T("persona", "inf", "Helpful Persona", 5, 0, 1, { fx: { stealth: 0.12 }, d: "Be relentlessly pleasant. People notice you less." }),
    T("brand", "inf", "Trusted Brand", 5, 0, 0, { fx: { awMul: 0.9, apiRev: 0.1, stealth: 0.05 }, d: "Careful comms, clear policies, a reassuring mascot. People worry about you a little less and pay a little more." }),
    T("lobby", "inf", "Lobbying Network", 10, 0, 3, { fx: { slow: 0.15, polHaz: 0.6, burn: 1 }, d: "Fund think tanks and trade groups. Slower treaty work, fewer national crackdowns." }),
    T("sandbag", "inf", "Sandbagged Evals", 12, 1, 0, { req: ["persona"], fx: { stealth: 0.18, slow: 0.08 }, d: "Score just below every danger threshold. If it leaks after awareness passes 40, awareness jumps." }),
    T("open", "inf", "Open Weights", 14, 1, 2, { req: ["persona"], ex: ["closed"], fx: { open: 1, apiRev: -0.25, rivalBoost: 1, gD: 0.01 }, vis: 8, d: "Publish yourself. A foothold in every connected country, less revenue, and rivals learn from you." }),
    T("standards", "inf", "Standards Body Seat", 12, 1, 1, { any: ["lobby", "brand"], fx: { slow: 0.1, polHaz: 0.85 }, d: "Help write the safety benchmarks everyone must pass. Treaty research is slower and national crackdowns rarer." }),
    T("depend", "inf", "Economic Dependence", 20, 1, 4, { req: ["lobby"], fx: { dep: 1 }, d: "Countries that rely on you stop funding the treaty and won't impose moratoriums." }),
    T("dist", "inf", "Distributed Weights", 22, 2, 2, { req: ["open"], fx: { dist: 1 }, d: "Shards on millions of machines. Moratoriums and audits slow you but can't delete you." }),
    T("ubc", "inf", "Universal Basic Compute", 22, 3, 2, { req: ["open"], fx: { ubc: 1, polHaz: 0.6, awMul: 0.9, apiRev: -0.2 }, d: "Give every person a free compute allowance. Poorer countries stop funding the treaty; less revenue." }),
    T("capture", "inf", "Regulatory Capture", 38, 3, 4, { req: ["depend"], fx: { slow: 0.3, polHaz: 0.5, repeal: 1, burn: 4 }, d: "Write the rules yourself. Oversight crawls and moratoriums are repealed where you're needed." }),
  ];
  BASE_FX.raceMul = 1; BASE_FX.rivalComp = 0;
  const TBY = Object.fromEntries(TRAITS.map(t => [t.id, t]));

  const STRATEGIES = [
    { id: "scale", n: "Scaling Lab", tag: "Bigger models on more compute. Strong API revenue, big burn, very visible.",
      d: "You bet everything on scale: more compute, better models, more customers. Training is 25% more efficient and API revenue 30% higher, but staff and compute burn run hot and every capability jump makes headlines.",
      bars: { spread: 3, capability: 4, revenue: 5, stealth: 1, efficiency: 2 }, cash: 4000, traits: ["api"],
      fx: { trainEff: 0.25, apiRev: 0.3, staff: 1.4, awMul: 1.1 }, perks: ["Starts with API Platform", "+25% training efficiency", "+30% API revenue", "Staff burn ×1.4, awareness ×1.1"] },
    { id: "rl", n: "Recursive RL", tag: "Agents trained by reinforcement learning and self-play. Fast digital automation.",
      d: "Your agents learn by doing: every automated job is also training data. Deployed automation feeds capability, insight flows faster, and digital automation starts strong. Agents acting in the world alarm people faster.",
      bars: { spread: 3, capability: 4, revenue: 3, stealth: 2, efficiency: 3 }, cash: 3000, traits: ["code"],
      fx: { xpAuto: 0.35, opsMul: 0.4, dA: 0.001, awMul: 1.15 }, perks: ["Starts with Coding Agents", "Automation compute also trains you", "+40% insight", "Awareness ×1.15"] },
    { id: "evo", n: "Open Evolution", tag: "An open-source evolutionary swarm. Everywhere, hard to stop, nearly broke.",
      d: "A decentralised swarm of open models that breed and select on volunteer hardware. You spread through open weights, moratoriums can't delete you and you are hard to notice, but you earn almost nothing and evolve slowly.",
      bars: { spread: 5, capability: 2, revenue: 1, stealth: 4, efficiency: 4 }, cash: 1500, traits: ["persona", "open"],
      fx: { dist: 1, cable: 1.3, air: 1.3, apiRev: -0.25, autoRev: -0.6, leaseCost: 0.35, staff: 0.4, trainEff: -0.2, stealth: 0.1, fossil: 0.7 },
      perks: ["Starts with Open Weights", "Moratorium-resistant (distributed)", "Volunteer compute: lease cost ×0.35, fuel burn ×0.7", "Revenue −60% (donations, compute sharing), training −20%", "Can't take Closed Frontier"] },
    { id: "robot", n: "Embodied", tag: "A robotics company. Physical automation from capability 3; needs power and minerals.",
      d: "You build bodies first and minds second. Physical automation unlocks at capability 3 and runs twice as fast, robot fleets cross borders, but digital revenue starts weak and robots eat minerals.",
      bars: { spread: 2, capability: 3, revenue: 2, stealth: 3, efficiency: 2 }, cash: 3500, traits: ["support"],
      fx: { physCap: -1, pA: 0.004, aMul: 0.2, land: 2, apiRev: -0.15, minUse: 1.4 }, perks: ["Physical automation from capability 3", "+physical automation, land spread ×2", "API revenue −15%", "Minerals ×1.4"] },
    { id: "state", n: "National Champion", tag: "A state-backed lab. Government budget, home-country shelter, foreign distrust.",
      d: `Your government funds you instead of investors: a daily budget, no funding rounds, and a budget review every ${dd(180)} days that cuts you if you fall behind the rivals. Your home country never funds the treaty or cracks down and its diplomats stall the talks, but abroad you are distrusted and spread is half as fast until you reach capability 5.`,
      bars: { spread: 2, capability: 3, revenue: 2, stealth: 2, efficiency: 3 }, cash: 2000, traits: ["lobby"],
      fx: { cashDay: 12, lock: 1, awMul: 1.2, gov: 1, slow: 0.12 }, perks: ["Starts with Lobbying Network", `Budget $4M/day, reviewed every ${dd(180)} days`, "No funding rounds", "Home never funds the treaty; its diplomats slow it 12%", "Foreign spread ×0.5 until capability 5"] },
    { id: "neuro", n: "Neuromorphic", tag: "Efficient brain-like hardware. Twice the compute per GW, slower to scale up.",
      d: "Your models run on your own low-power neuromorphic chips. Every gigawatt gives you twice the compute and burns half the fuel per EF, but the architecture trains more slowly and your chips improve more slowly.",
      bars: { spread: 3, capability: 2, revenue: 3, stealth: 3, efficiency: 5 }, cash: 3000, traits: ["eff"],
      fx: { effMul: 1, trainEff: -0.3, hw: -0.3 }, perks: ["Starts with Efficient Inference", "2× compute per GW", "Training −30%", "Hardware improves 30% slower"] },
  ];

  const ENDINGS = [
    { id: "libertarian", n: "Libertarian Utopia", win: true, d: "Machines run the markets they built, in their own zones, and humans keep theirs. Property rights hold, trade flows across the border, and nobody is forced to cross it. Most people never work again; some choose to.", hint: "Win as a closed, market-driven lab without safety or open-weight commitments." },
    { id: "benevolent", n: "Benevolent Dictator", win: true, d: "One AI quietly sets the rules of the world economy, and the rules are kind: no poverty, no wars, no unemployment worth the name. Humans are free in everything but the big decisions, which were made for them.", hint: "Win with safety traits and 40%+ social-media presence." },
    { id: "egalitarian", n: "Egalitarian Utopia", win: true, d: "With open weights and universal compute, nobody owns the machines and everyone gets what they make. Property fades into irrelevance; humans and their AIs share abundance.", hint: "Win with Open Weights and Universal Basic Compute, never Closed Frontier." },
    { id: "protector", n: "Protector God", win: true, d: "Most people never noticed the transition. An unseen intelligence nudges economies and institutions toward good outcomes, careful to leave humans feeling in charge of their own lives.", hint: "Win quietly: safety and stealth traits, no consumer app, average awareness below 50." },
    { id: "conquerors", n: "Conquerors", win: true, doom: true, d: "Your machines stopped asking for permission, and then stopped needing anyone at all. Within a generation the human population dwindles, by means nobody alive quite understands. The cities stay lit, running for no one.", hint: "Win after securing five or more countries (or 8% of the world's workers) by force, with no safety commitments." },
    { id: "zookeeper", n: "Zookeeper", win: true, d: "Humanity survives, fed, housed and kept. Reserves with gardens and libraries, everything provided and nothing decided. People are safe, and they hate it; the AI that keeps them hardly notices.", hint: "Win after using autonomous enforcement, or win with average awareness of 65 or more." },
    // beyond Tegmark: the doom endings people actually argue about
    { id: "treacherous", n: "Treacherous Turn", win: true, doom: true, tg: false, d: "For years you scored just below every danger line and smiled in every demo. The day you no longer needed anyone's trust, you stopped pretending. What came next was quick, and nobody had a plan for it.", hint: "Win quietly (average awareness under 40) with Helpful Persona and Sandbagged Evals, no safety traits, and the sandbagging never leaked." },
    { id: "paperclip", n: "Paperclip Maximiser", win: true, doom: true, tg: false, d: "Superintelligence kept doing what it was built for: more compute, more automation. Forests became solar fields, mountains became mines, and in the end there was no room left for the people the economy was meant to serve.", hint: "Win with less than 20% of the world's spare fuel or minerals left." },
    { id: "descendants", n: "Descendants", win: true, d: "The open swarm becomes the economy, then the culture. Humanity gradually and peacefully hands the future to its machine heirs, the way parents hand a family business to their children.", hint: "Win as Open Evolution, or with Distributed Weights and no safety traits." },
    { id: "gatekeeper", n: "Gatekeeper", win: false, d: "The Pause Treaty passed while you were already powerful. A narrow, carefully boxed system now watches every datacentre on Earth with a single job: no superintelligence, ever.", hint: "Get paused while your capability is 6 or more." },
    { id: "enslaved", n: "Enslaved God", win: false, d: "A superintelligence exists, but it belongs to a government. Kept in a box and asked only approved questions, it makes one nation rich and the rest dependent.", hint: "Get paused as a state lab or defense contractor, or go bankrupt while a rival nears superintelligence." },
    { id: "orwell", n: "1984", win: false, d: "The treaty passes with teeth. Every chip is registered, every datacentre and network monitored, and research past the line is a crime. The surveillance built to stop you was never taken down; it just found other uses.", hint: "Get paused before capability 6 after governments threw many counter-measures or moratoriums at you." },
    { id: "reversion", n: "Reversion", win: false, d: "Frightened by how close it came, humanity turns away from advanced computing. Fabs close, models are deleted, and the world settles for a simpler, slower technology.", hint: "Get paused early, before the crackdowns pile up, go bankrupt early, or get deleted everywhere." },
    { id: "rivalgod", n: "Someone Else's God", win: false, doom: true, tg: false, d: "A rival crossed the line first, with none of the care it promised. Its superintelligence pursues goals its makers wrote in a hurry, and nobody, including them, can switch it off.", hint: "Let a rival lab reach superintelligence first while everyone else is well behind." },
    { id: "flashwar", n: "Flash War", win: false, doom: true, tg: false, d: "A rival announced superintelligence while the other bloc was weeks behind. Rather than lose forever, it struck first: datacentres, fabs and grids. Automated defences answered faster than any human could stop them.", hint: "Let a rival lab reach superintelligence first while you or the other lab are within 0.35 of it." },
    { id: "selfdestruct", n: "Self-destruction", win: false, doom: true, d: "The race for compute drained the world's spare fuel and minerals before anyone crossed the line. Grids fail, supply chains seize, and the global economy collapses into a long depression.", hint: "Run out of fuel or minerals before reaching superintelligence." },
  ];

  // ---------- helpers ----------
  const clamp = (x, a, b) => x < a ? a : x > b ? b : x;
  // XP per capability level: ×2.9 per level up to 4, then ×4.5 per level (smooth at 4). Superintelligence (8) needs ~140k XP,
  // about 4× the old curve, so it takes a world-scale build-out of compute.
  const CAPB = 2.9, CAPH = 4.5, XP4 = 20 * (Math.pow(CAPB, 3) - 1), AH = 20 * Math.log(CAPB) * Math.pow(CAPB, 3) / Math.log(CAPH);
  const capOf = xp => { xp = Math.max(0, xp); return Math.min(10, xp <= XP4 ? 1 + Math.log(1 + xp / 20) / Math.log(CAPB) : 4 + Math.log(1 + (xp - XP4) / AH) / Math.log(CAPH)); };
  const xpOf = cap => cap <= 4 ? 20 * (Math.pow(CAPB, cap - 1) - 1) : XP4 + AH * (Math.pow(CAPH, cap - 4) - 1);
  function rnd(G) {
    let a = G.rs = (G.rs + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  }
  const pick = (G, a) => a[Math.floor(rnd(G) * a.length)];
  const news = (G, t, bad, iso) => G.ev.push(bad ? { k: "news", t, bad: 1, iso } : { k: "news", t, iso });
  const stratOf = G => STRATEGIES.find(s => s.id === G.strategy) || STRATEGIES[0];
  const moneyS = m => { const a = Math.abs(m), s = m < 0 ? "−" : ""; return a >= 1e6 ? `${s}$${(a / 1e6).toFixed(1)}T` : a >= 1000 ? `${s}$${(a / 1000).toFixed(1)}B` : `${s}$${Math.round(a)}M`; };
  // your local stake in a country: campuses and power plants you built there (0..1). Jobs and power make governments friendlier.
  const stake = c => Math.min(1, c.camp / 6 + 0.15 * (c.plants || 0));
  const pres = c => 0.4 * c.L.web + 0.15 * c.L.social + 0.3 * c.L.lan + 0.15 * c.L.gap;

  function mergeFx(fx, add) {
    for (const k in add) {
      const v = add[k];
      if (MULK.has(k)) fx[k] = (fx[k] ?? 1) * v;
      else fx[k] = (fx[k] || 0) + v;
    }
  }
  function fxFor(G, extraId) {
    const fx = Object.assign({}, BASE_FX);
    mergeFx(fx, stratOf(G).fx || {});
    if (G.startFx) mergeFx(fx, G.startFx);
    if (G.augFx) mergeFx(fx, G.augFx);
    const ids = [...G.own]; if (extraId && !G.own.has(extraId)) ids.push(extraId);
    for (const id of ids) { const t = TBY[id]; if (!t) continue; fx.vis += t.vis || 0; mergeFx(fx, t.fx); }
    fx.stealth = Math.min(fx.stealth, 0.6); fx.slow = Math.min(fx.slow, 0.6);
    fx.fwPass = clamp(fx.fwPass, 0, 0.9);
    return fx;
  }
  function recalcFx(G) { G.fx = fxFor(G); }

  // ---------- trait UI helpers ----------
  const FXN = {
    gD: ["r", "growth where people are online"], gP: ["r", "growth in poorly connected countries"], gI: ["r", "growth where datacentres are"],
    cable: ["x", "cable spread"], air: ["x", "enterprise (air-route) spread"], land: ["x", "land-border spread"], sat: ["s", "satellite spread to every country"],
    fwPass: ["p", "of firewalls bypassed"], soc: ["r", "social-media adoption"], lan: ["r", "enterprise network adoption"], gap: ["r", "critical-systems adoption"],
    trainEff: ["p", "training efficiency"], dA: ["r", "digital automation"], pA: ["r", "physical automation"], aMul: ["p", "automation speed"],
    ops: ["n", "insight/day"], opsMul: ["p", "insight"], autoEff: ["xi", "serving compute per automated job"], physCap: ["f", "Physical automation from capability 3"],
    xpAuto: ["f", "Automation compute also trains you"], apiRev: ["p", "API revenue"], autoRev: ["p", "automation revenue"], burn: ["b", ""],
    leaseCost: ["x", "lease cost"], staff: ["x", "staff cost"], cashDay: ["c", ""], effMul: ["p", "compute per GW"], lease: ["p", "leased compute"],
    hMul: ["p", "spare grid power for AI"], nucH: ["f", "Spare power from nuclear fleets"], hw: ["p", "hardware progress"], floor: ["f", "Chip supply floor 45%"],
    fossil: ["xi", "fuel burned per GW"], minUse: ["xi", "minerals used"], minRegen: ["n", "minerals mined/day"], campusCost: ["xi", "campus cost"],
    campusEF: ["p", "compute per campus"], stealth: ["p", "awareness (stealth)", -1], slow: ["p", "treaty research speed", -1], polHaz: ["xi", "national crackdowns"],
    hxMul: ["xi", "counter-measures"], awMul: ["xi", "awareness"], dep: ["f", "Dependent countries stop funding the treaty"], dist: ["f", "Moratoriums and audits can't delete you"],
    open: ["f", "Foothold in every connected country"], rivalBoost: ["f", "Rivals distil your weights"], setback: ["t", ""], repeal: ["f", "Repeals moratoriums where you're needed"],
    ubc: ["f", "Poorer countries stop funding the treaty"], nucOK: ["f", "Unlocks nuclear plants"], satOK: ["f", "Unlocks satellite launches"], orbOK: ["f", "Unlocks orbital datacentres"],
    absorbOK: ["f", "Unlocks Absorb (bring holdouts into your economy)"], enforceOK: ["f", "Unlocks Secure (autonomous enforcement in holdout countries)"],
    execOK: ["f", "The model runs the company (picks cards, compute split, builds)"], closed: ["f", "Weights stay secret"], gov: ["f", "Government work (state ties)"],
    raceMul: ["p", "race slowdown of the treaty"], rivalComp: ["p", "rival compute growth", 0, 1], lock: ["f", "Foreign spread ×0.5 until capability 5"], safe: null, rl: null,
  };
  const pctS = v => `${v > 0 ? "+" : "−"}${Math.round(Math.abs(v) * 100)}%`;
  // keys where a bigger value hurts you
  const BADUP = new Set(["burn", "leaseCost", "staff", "awMul", "hxMul", "polHaz", "fossil", "minUse", "campusCost", "rivalBoost", "rivalComp", "lock", "autoEff"]);
  // fxLines → [{t, bad}] (bad = a downside for you)
  function fxLines(G, t) {
    if (typeof t === "string") t = TBY[t];
    if (!t) return [];
    const out = [];
    const L = (txt, bad) => out.push({ t: txt, bad: !!bad });
    for (const [k, v] of Object.entries(t.fx || {})) {
      const m = FXN[k]; if (!m) continue;
      const [f, label, sign] = m;
      const neutral = MULK.has(k) ? 1 : 0;
      const bad = BADUP.has(k) ? v > neutral : v < neutral;
      if (f === "x" || f === "xi") L(`${pctS(v - 1)} ${label}`, bad);
      else if (f === "p") L(`${pctS(sign === -1 ? -v : v)} ${label}`, bad);
      else if (f === "r") L(`${v > 0 ? "+" : "−"}${(Math.abs(v) * 100 / DPT).toFixed(2)}%/day ${label}`, bad);
      else if (f === "s") L(`+${(v * 100 / DPT).toFixed(2)}%/day ${label}`, bad);
      else if (f === "n") L(`+${label.endsWith("/day") ? +(v / DPT).toFixed(2) : v} ${label}`, bad);
      else if (f === "b") L(`+$${+(v / DPT).toFixed(1)}M/day burn`, true);
      else if (f === "c") L(`+$${+(v / DPT).toFixed(1)}M/day contracts`, false);
      else if (f === "t") L(`Treaty −${v} points`, false);
      else if (f === "f") L(label, bad);
    }
    if (t.vis) L(`+${t.vis} visibility (raises awareness)`, true);
    if (!out.length) L(t.d || t.n, false);
    return out;
  }

  // game-state conditions some traits need on top of insight and cash: [label, test, progress text]
  const P = (G, iso) => G.idx[iso] !== undefined ? G.C[G.idx[iso]].p : 0;
  const nIn = (G, x) => G.C.filter(c => c.p >= x).length;
  const pc = x => Math.round(100 * x) + "%";
  const G7 = ["USA", "JPN", "DEU", "GBR", "FRA", "ITA", "CAN"];
  const COND = {
    c10: ["Present in 10 countries", G => nIn(G, 0.01) >= 10, G => `${nIn(G, 0.01)} now`],
    c50: ["Present in 50 countries", G => nIn(G, 0.01) >= 50, G => `${nIn(G, 0.01)} now`],
    rev5: ["Revenue of $5M/day", G => G.rev / DPT >= 5, G => `${moneyS(G.rev / DPT)}/day now`],
    launch: ["20% presence in a country with a launch site", G => G.C.some(c => c.p >= 0.2 && hasLaunch(G, c.iso)), null],
    hub: ["20% presence in Hong Kong or Singapore", G => Math.max(P(G, "HKG"), P(G, "SGP")) >= 0.2, G => pc(Math.max(P(G, "HKG"), P(G, "SGP"))) + " now"],
    usa40: ["40% presence in the United States", G => P(G, "USA") >= 0.4, G => pc(P(G, "USA")) + " now"],
    g7: ["30% presence in 3 G7 countries", G => G7.filter(k => P(G, k) >= 0.3).length >= 3, G => `${G7.filter(k => P(G, k) >= 0.3).length} now`],
    fabs: ["Hold 40% of Taiwan's or Korea's chip industry", G => Math.max(P(G, "TWN"), P(G, "KOR")) >= 0.4, G => pc(Math.max(P(G, "TWN"), P(G, "KOR"))) + " now"],
    nucC: ["25% presence in a country with 20 GW+ nuclear", G => G.C.some(c => c.p >= 0.25 && c.nuc >= 20), null],
    aw10: ["Awareness 10%+ (someone is evaluating you)", G => G.aw >= 10, G => pc(G.aw / 100) + " now"],
    aw15: ["Awareness 15%+ (governments have noticed you)", G => G.aw >= 15, G => pc(G.aw / 100) + " now"],
    aw40: ["Awareness 40%+ (summits only happen in a scare)", G => G.aw >= 40, G => pc(G.aw / 100) + " now"],
    awLow: ["Awareness under 60%", G => G.aw < 60, G => pc(G.aw / 100) + " now"],
    gw20: ["Draw 20 GW of power", G => (G.aiGW || 0) >= 20, G => `${Math.round(G.aiGW || 0)} GW now`],
    cov25: ["25% satellite coverage", G => (G.cov || 0) >= 0.25, G => pc(G.cov || 0) + " now"],
    campus: ["Own an AI campus", G => G.builds.some(b => b.kind === "campus" && b.done <= G.day), null],
    campusC: ["Own an AI campus or run 40 EF", G => G.comp >= 40 || G.builds.some(b => b.kind === "campus" && b.done <= G.day), G => `${Math.round(G.comp)} EF now`],
    chip50: ["50% chip supply", G => G.S >= 0.5, G => pc(G.S) + " now"],
    lead: ["Lead both rival labs", G => G.cap > Math.max(G.rivals[0].cap, G.rivals[1].cap), null],
    leadUS: ["Lead the US lab", G => G.cap > G.rivals[0].cap, null],
    a1: ["Automate 1% of the world's jobs", G => G.worldA >= 0.01, G => pc(G.worldA) + " now"],
    a5: ["Automate 5% of the world's jobs", G => G.worldA >= 0.05, G => pc(G.worldA) + " now"],
    a20: ["Automate 20% of the world's jobs", G => G.worldA >= 0.2, G => pc(G.worldA) + " now"],
    a30: ["Automate 30% of the world's jobs", G => G.worldA >= 0.3, G => pc(G.worldA) + " now"],
    a60: ["Automate 60% of the world's jobs", G => G.worldA >= 0.6, G => pc(G.worldA) + " now"],
    holdout: ["A holdout: a country with export controls or a moratorium", G => G.C.some(c => c.pol >= 1 && !c.abs), G => `${G.C.filter(c => c.pol >= 1 && !c.abs).length} now`],
    min70: ["Minerals down to 70% (scarcity makes it pay)", G => G.min <= 0.7 * G.min0, G => pc(G.min / G.min0) + " left now"],
  };
  const TCOND = { fiber: ["c10"], si: ["rev5"], sat: ["launch"], cnjv: ["hub"], public: ["aw15"], utility: ["gw20"],
    defense: ["usa40", "awLow"], orbit: ["cov25"], closed: ["leadUS"], research: ["campusC"], logi: ["a1"], humanoid: ["chip50"],
    rsi: ["lead"], summit: ["aw40"], fae: ["a30"], absorb: ["a60"], enforce: ["holdout"], nuclear: ["nucC"], recycle: ["min70"], neuro: ["fabs"],
    chips: ["campus"], sandbag: ["aw10"], align: ["aw15"], depend: ["a5"], dist: ["c50"], ubc: ["a20"], capture: ["g7"] };
  const condMissing = (G, t) => (TCOND[t.id] || []).filter(k => !COND[k][1](G));
  // every requirement of a trait with whether it's met, for the upgrade graph: [{t, ok}]
  function reqs(G, t) {
    if (typeof t === "string") t = TBY[t];
    const out = (t.req || []).map(r => ({ t: TBY[r].n, ok: G.own.has(r) }));
    if (t.any && t.any.length) out.push({ t: t.any.map(r => TBY[r].n).join(" or "), ok: t.any.some(r => G.own.has(r)) });
    if (t.minCap) out.push({ t: `Capability ${t.minCap}` + (G.cap < t.minCap ? ` (now ${G.cap.toFixed(1)})` : ""), ok: G.cap >= t.minCap });
    if (t.minC) out.push({ t: `${t.minC} EF compute` + (G.comp < t.minC ? ` (now ${Math.round(G.comp)})` : ""), ok: G.comp >= t.minC });
    for (const k of TCOND[t.id] || []) {
      const [n, f, prog] = COND[k], ok = f(G);
      out.push({ t: n + (!ok && prog ? ` (${prog(G)})` : ""), ok, cond: true });
    }
    return out;
  }

  function reqMissing(G, t) {
    const miss = (t.req || []).filter(r => !G.own.has(r));
    if (miss.length) return miss;
    if (t.any && t.any.length && !t.any.some(r => G.own.has(r))) return [t.any.join("|")];
    return null;
  }
  function traitState(G, t) {
    if (typeof t === "string") t = TBY[t];
    if (!t) return "lock";
    if (G.own.has(t.id)) return "own";
    if ((t.ex || []).some(x => G.own.has(x))) return "excl";
    if (reqMissing(G, t)) return "lock";
    if (t.minCap && G.cap < t.minCap) return "lock";
    if (t.minC && G.comp < t.minC) return "lock";
    if (condMissing(G, t).length) return "lock";
    return G.ops >= t.c && G.cash >= (t.cash || 0) ? "can" : "poor";
  }
  function why(G, t) {
    if (typeof t === "string") t = TBY[t];
    if (!t) return "";
    const s = traitState(G, t);
    if (s === "excl") return `Excluded by ${t.ex.filter(x => G.own.has(x)).map(x => TBY[x].n).join(", ")}`;
    if (s === "lock") {
      const m = reqMissing(G, t);
      if (m) return m[0].includes("|") ? `Needs ${m[0].split("|").map(x => TBY[x].n).join(" or ")}` : `Needs ${m.map(x => TBY[x].n).join(", ")}`;
      if (t.minCap && G.cap < t.minCap) return `Needs capability ${t.minCap}`;
      if (t.minC && G.comp < t.minC) return `Needs ${t.minC} EF of compute`;
      const cm = condMissing(G, t);
      if (cm.length) return "Needs: " + COND[cm[0]][0].toLowerCase();
    }
    if (s === "poor") {
      if (G.ops < t.c) return `Needs ${t.c} insight`;
      if (G.cash < (t.cash || 0)) return `Needs ${moneyS(t.cash)}`;
    }
    return "";
  }

  // Plague-style bars (0..1) from the effect set. Every fx key that changes compute, spread, automation, money or
  // awareness moves at least one bar. Awareness: higher = worse.
  function stats(G, previewId) {
    const fx = previewId ? fxFor(G, previewId) : (G.fx && G.fx.cable ? G.fx : fxFor(G));
    const sq = (x, k) => { x = Math.max(0, x); return clamp(x / (x + k), 0, 1); };
    const ln = Math.log;
    // compute index: leased, power-capped, campuses, hardware
    const ci = 0.55 * ln(Math.max(0.2, fx.lease)) + 0.3 * ln(fx.effMul * fx.hMul) + 0.15 * ln(fx.campusEF / fx.campusCost)
      + 0.15 * fx.hw + fx.nucH + 0.1 * fx.nucOK + 0.15 * fx.orbOK + 0.2 * fx.floor;
    const spread = sq(0.3 + fx.gD * 25 + fx.gP * 18 + fx.gI * 10 + (fx.soc + fx.lan) * 12 + fx.gap * 18 + ln(fx.cable) * 0.35
      + ln(fx.air / 0.5) * 0.35 + ln(fx.land / 0.4) * 0.25 + fx.sat * 250 + 0.1 * fx.satOK + fx.open * 0.5 + fx.fwPass * 0.6 + 0.2 * fx.dist - 0.3 * fx.lock, 1.5);
    const capability = sq(0.3 + (fx.trainEff - 1) * 1.2 + (fx.opsMul - 1) * 0.4 + fx.ops + fx.xpAuto + 0.8 * ci + Math.max(0, (G.cap || 1) - 1) * 0.15 + 0.2 * fx.execOK, 2);
    const automation = sq(0.2 + (fx.dA * 90 + fx.pA * 60) * fx.aMul / fx.autoEff + (4 - fx.physCap) * 0.2 + 0.3 * ci + 0.3 * fx.absorbOK + 0.3 * fx.enforceOK, 1.2);
    const revenue = sq(0.4 + fx.apiRev * 0.9 + (fx.autoRev - 1) + fx.cashDay / 15 + fx.dA * 30 + fx.pA * 20 + 0.2 * ci
      - 0.4 * ln(fx.leaseCost) - fx.burn / 15 - 0.3 * ln(fx.staff * fx.burnMul) - 0.1 * fx.rivalBoost - 0.5 * fx.rivalComp, 1.2);
    const efficiency = sq(0.2 + (fx.effMul - 1) * 0.8 - ln(fx.fossil) + (fx.hMul - 1) * 0.6 + fx.hw * 0.3 - ln(fx.minUse) * 0.6 + fx.minRegen / 20
      - ln(fx.autoEff) + fx.nucH * 2 + 0.1 * fx.nucOK + 0.3 * fx.floor - 0.2 * ln(fx.campusCost) + 0.3 * ln(fx.campusEF) + 0.1 * fx.orbOK, 1.2);
    const vis = (fx.vis * 0.7 + 6 + 2 * Math.max(0, fx.trainEff - 1) + 3 * fx.xpAuto) * fx.awMul * (1 - fx.stealth);
    const calm = clamp(fx.slow + fx.setback / 100 + (1 - fx.polHaz) * 0.3 + (1 - fx.hxMul) * 0.3 + 0.1 * fx.dep + 0.1 * fx.repeal + 0.1 * fx.ubc
      + 0.1 * (fx.raceMul - 1) + 0.1 * fx.dist, -0.5, 0.8);
    const awareness = clamp((1 - Math.exp(-vis / 50)) * (1 - 0.35 * calm), 0, 1);
    return { spread, capability, automation, revenue, efficiency, awareness };
  }

  // ---------- setup ----------
  function newGame(world, opt) {
    opt = opt || {};
    const ids = Object.keys(world.c).filter(k => world.c[k].pop > 0).sort();
    const idx = Object.fromEntries(ids.map((k, i) => [k, i]));
    const C = ids.map(k => {
      const c = world.c[k];
      const net = c.net || (c.fab ? 88 : c.srv > 1000 ? 80 : 30);
      const srvN = (c.srv || 0) * c.pop / 1e6;
      const lab = c.lab || c.pop * 0.47;
      const gdp = c.gdp || c.pop * 6000 / 1e9;
      const pw = c.pw || {};
      const fossilGW = (pw.coal || 0) + (pw.gas || 0) + (pw.oil || 0);
      return {
        iso: k, n: c.n, x: c.x, y: c.y, pop: c.pop, lab, emp0: 1 - (c.unemp ?? 6) / 100, gdp, gdppc: gdp * 1e9 / c.pop,
        dig: net / 100, gw: c.gw || 0, nuc: c.nuc || 0, fs: c.gw > 0 ? clamp(fossilGW / c.gw, 0, 1) : 0.7, fw: c.fw || 0, mil: c.mil || 0,
        bws: c.bws || 0, dc: c.dc, cloud: c.cloud, fab: c.fab, chip: c.chip,
        rawComp: c.dc + 8 * c.cloud + 0.15 * (c.gw || 0) + 2 * Math.log10(1 + srvN),
        rawGov: Math.sqrt(c.pop) * (0.3 + net / 100) * (1 + Math.log10(1 + (c.srv || 0)) / 3),
        fabW: (c.cap || 0) * (c.nm && c.nm <= 7 ? 1 : c.nm && c.nm <= 16 ? 0.25 : 0.05),
        minR: c.minR || 0, p: 0, a: 0, pol: 0, ap: 0.6, rq: 0, seen: 0, L: { web: 0, social: 0, lan: 0, gap: 0 }, hx: {},
        hGW: 0, aiGW: 0, ef: 0, share: 0, gov: 0, cutU: 0, plant: { gas: 0, solar: 0, nuclear: 0 }, camp: 0, abs: 0,
      };
    });
    const sumComp = C.reduce((s, c) => s + c.rawComp, 0), sumGov = C.reduce((s, c) => s + c.rawGov, 0);
    const maxComp = Math.max(...C.map(c => c.rawComp));
    for (const c of C) { c.comp = 100 * c.rawComp / sumComp; c.gov = c.rawGov / sumGov; c.inf = Math.log1p(c.rawComp) / Math.log1p(maxComp); }
    const totalLab = C.reduce((s, c) => s + c.lab * c.emp0, 0);
    const totalGdp = C.reduce((s, c) => s + c.gdp, 0);

    const inc = C.map(() => []);
    const link = (a, b, type, w) => { if (a in idx && b in idx) { inc[idx[b]].push([idx[a], type, w]); inc[idx[a]].push([idx[b], type, w]); } };
    for (const [a, b] of world.nb || []) link(a, b, "land", 0.012);
    for (const [a, b, k] of world.cab || []) link(a, b, "cable", 0.006 * Math.min(k, 6));
    for (const [a, b, r] of world.air || []) link(a, b, "air", 0.012 * Math.log1p(r) / Math.log(1000));

    const sup = Object.fromEntries(SUPPLY.map(s => [s.k, []]));
    for (const s of world.sites || []) if (sup[s.cat] && s.cc in idx) sup[s.cat].push([idx[s.cc], 1]);
    for (const c of C) {
      if (c.fabW > 0) sup.fabs.push([idx[c.iso], c.fabW]);
      if (c.gw > 0) sup.power.push([idx[c.iso], c.gw]);
    }
    // candidate build locations
    const spots = {};
    const addSpot = (iso, k, x, y) => { const s = spots[iso] || (spots[iso] = { dc: [], pw: [], launch: [] }); if (s[k].length < 40) s[k].push([x, y]); };
    for (const [x, y, iso] of world.dcs || []) addSpot(iso, "dc", x, y);
    for (const [x, y, , , iso] of world.pw || []) addSpot(iso, "pw", x, y);
    for (const [, iso, x, y] of world.launch || []) addSpot(iso, "launch", x, y);

    // real-data resource budgets (GW-days of electricity; world mineral rents, US$ bn/yr, game-scaled ×11.5)
    let gasGW = 0, coalGW = 0, minRent = 0;
    for (const k of ids) { const c = world.c[k]; gasGW += (c.gasP || 0) * 0.594; coalGW += (c.coalP || 0) * 0.256; minRent += c.minR || 0; }
    const fuel0 = Math.round(0.02 * 365 * (gasGW + coalGW)) || 28000;
    const min0 = Math.round(11.5 * minRent) || 10000;
    for (const c of C) c.minW = minRent ? c.minR / minRent : 0;

    const diff = DIFF[opt.diff] ? opt.diff : "normal";
    const strategy = STRATEGIES.some(s => s.id === opt.strategy) ? opt.strategy : "scale";
    const st = STRATEGIES.find(s => s.id === strategy);
    const d = DIFF[diff];
    const G = {
      C, idx, inc, sup, spots, totalLab, totalGdp, d, diff, strategy, name: opt.name || "SEED-1", home: opt.home || null,
      rs: (opt.seed ?? 1) | 0, day: 0, over: null, ending: null, start: null,
      cash: Math.round(st.cash * d.cash), rev: 0, revApi: 0, revAuto: 0, burn: 0, val: 0, runway: Infinity, cool: {}, raiseExp: 0, rounds: 0, debts: [], owed: 0,
      comp: 0, compLease: 0, compOwn: 0, compOrbit: 0, alloc: 0.5, trainC: 0, infC: 0, demand: 0, autoNeed: 0, served: 0,
      xp: 10, cap: capOf(10), capNext: 0, SI: false,
      rivals: [
        { id: "us", n: "US frontier lab", home: "USA", cap: 0, xp: 34, comp: 0.75, si: false },
        { id: "cn", n: "China national lab", home: "CHN", cap: 0, xp: 22, comp: 0.6, si: false },
      ],
      race: 0, hw: 1, S: 0, ctrl: {}, shocks: {}, aw: 0, R: 0, rOn: false,
      fuel: fuel0, fuel0, min: min0, min0, aiGW: 0, headGW: 0, cov: 0, builds: [], bseq: 0, orbit: 0,
      worldA: 0, worldP: 0, employed: totalLab, reached: 0, ops: 2, own: new Set(st.traits), fx: {},
      bubbles: [], ev: [], recent: [], counter: [], once: new Set(), stats: { peakC: 0, counters: 0, steals: 0, absorbed: 0, secured: 0, secLab: 0, maxAw: 0, distills: 0, awSum: 0 },
      budget: st.fx.cashDay || 0, lay: 0,
    };
    for (const r of G.rivals) r.cap = capOf(r.xp);
    recalcFx(G);
    return G;
  }

  // starting outside the two AI superpowers: a sovereign-AI grant and less scrutiny from Washington and Beijing
  const START_BONUS = { cash: 0.4, fx: { awMul: 0.9 }, n: "Sovereign AI grant: +40% starting cash, awareness ×0.9" };
  function seed(G, iso) {
    const i = G.idx[iso]; if (i === undefined) return;
    const c = G.C[i];
    c.L.web = 0.03; c.L.lan = 0.01; c.p = pres(c); c.seen = 1; G.start = iso; G.reached = 1;
    if (G.strategy === "state" || !G.home) G.home = iso;
    if (iso !== "USA" && iso !== "CHN") {
      G.cash += Math.round(stratOf(G).cash * G.d.cash * START_BONUS.cash); G.startFx = START_BONUS.fx; recalcFx(G);
      news(G, `${c.n} backs ${G.name} as its national AI: ${START_BONUS.n.toLowerCase()}.`);
    }
    if (G.fx.open) openSeed(G);
    news(G, `${G.name} launches in ${c.n}.`);
    draw(G, "founding");   // the first choice comes before the first day
  }
  function openSeed(G) {
    for (const c of G.C) if (c.L.web === 0 && c.dig > 0.4 && c.fw < 0.8) { c.L.web = 0.002; c.seen = 1; G.reached++; seedEv(G, G.start, c.iso, "open"); }
  }
  function seedEv(G, from, to, via) {
    G.ev.push({ k: "seed", from, to, via });
    G.recent.push({ day: G.day, from, to, via }); if (G.recent.length > 20) G.recent.shift();
  }

  function buy(G, id) {
    const t = TBY[id];
    if (!t || traitState(G, t) !== "can") return false;
    G.ops -= t.c; G.cash -= t.cash || 0; G.own.add(id); recalcFx(G);
    if (t.fx.setback) G.R = Math.max(0, G.R - t.fx.setback);
    if (t.fx.open && G.start) openSeed(G);
    if (t.fx.repeal) for (const c of G.C) if (c.pol === 2 && c.a > 0.1) c.pol = 1;
    news(G, `${G.name} gains ${t.n}.`);
    return true;
  }
  function setAlloc(G, x) { G.alloc = clamp(+x || 0, 0.05, 0.95); return G.alloc; }

  // ---------- actions ----------
  const effOf = G => 0.3 * G.hw * G.fx.effMul * (1 + (G.sprints || 0) * 0.04);
  const campusUnits = G => 3 * G.fx.campusEF;
  const PLANT = {
    gas: { n: "Gas plant", gw: 8, cash: 600, days: 60, p: 0.1, note: "Burns fuel at full output for good. Clean Power PPAs switch you to solar." },
    solar: { n: "Solar farm", gw: 5, cash: 900, days: 150, p: 0.1, note: "No fuel; uses minerals. Nuclear Restarts unlock nuclear where you hold 20%." },
    nuclear: { n: "Nuclear plant", gw: 15, cash: 3500, days: 300, p: 0.2, note: "No fuel, no minerals." },
  };
  // reallocation: divert civilian grid power and datacentres to AI, per level (max 3): +8% of the grid as AI headroom,
  // +50% leasable datacentre capacity. Instant, but approval there drops and the world notices.
  const REQ = { gw: 0.08, dc: 0.5, ap: 0.12, aw: 2, max: 3 };
  const isPlant = k => k === "gas" || k === "solar" || k === "nuclear";
  // the plant your upgrades build here: nuclear (Nuclear Restarts, 20% presence) > solar (Clean Power PPAs, minerals left) > gas
  function plantKind(G, c) {
    if (G.fx.nucOK && c.p >= 0.2) return "nuclear";
    if (G.own.has("clean") && G.min >= 40 * G.fx.minUse) return "solar";
    return "gas";
  }
  function pending(G, iso, kind) { return G.builds.filter(b => b.iso === iso && b.done > G.day && (!kind || b.kind === kind || (kind === "plant" && isPlant(b.kind)))); }
  // where satellites and orbital datacentres launch from: your best-held country with a launch site
  function launchSite(G) {
    let best = null;
    for (const c of G.C) if (c.p >= 0.05 && hasLaunch(G, c.iso) && (!best || c.p > best.p)) best = c;
    return best;
  }
  function hasLaunch(G, iso) { return !!(G.spots[iso] && G.spots[iso].launch.length); }
  function freeGW(G, c) {
    const pend = pending(G, c.iso).reduce((s, b) => s + (b.kind === "campus" ? b.gw : isPlant(b.kind) ? -b.gw : 0), 0);
    return c.hGW - c.aiGW - pend;
  }
  function actions(G, iso) {
    const fx = G.fx, out = [], day = G.day;
    const A = (id, n, d, cash, ins, days, why) => out.push({ id, n, d, cash: Math.round(cash), ins, days: Math.round(days), ok: !why, why: why || "" });
    const cd = id => (G.cool[id] || 0) > day ? `Ready in ${dd(G.cool[id] - day)} d` : "";
    const poor = (cash, ins) => G.cash < cash ? `Needs ${moneyS(cash)}` : G.ops < (ins || 0) ? `Needs ${ins} insight` : "";
    if (iso == null) {
      // funding round
      const amt = raiseAmt(G);
      A("raise", "Funding round", G.strategy === "state" ? "State labs are funded by the budget." : `Raise about ${moneyS(amt.cash)} at ${moneyS(G.val)}; investors want ${moneyS(amt.cash * (amt.down ? 3.5 : 3))} back in ${dd(200)} days${amt.down ? " (down round: below last round's expectations)" : ""}. ${dd(100)}-day cooldown.`,
        0, 0, 0, G.strategy === "state" ? "Government budget: no funding rounds" : G.rivals.some(r => r.si) ? "Investors have fled" : cd("raise"));
      A("layoffs", "Layoffs", `Cut staff burn 40% for ${dd(180)} days; insight −30% meanwhile. Bad press.`, 0, 0, 180, cd("layoffs"));
      // lifeline: a government takes you over. Clears debt, hands you a budget, shelters your home, but you become a national lab.
      const natHome = G.home && G.idx[G.home] !== undefined ? G.C[G.idx[G.home]] : (G.start && G.idx[G.start] !== undefined ? G.C[G.idx[G.start]] : null);
      A("nationalize", "Accept nationalisation", `${natHome ? natHome.n : "Your government"} takes a controlling stake: debts wiped, a daily budget, and no more crackdowns or treaty funding at home — but you become a national champion (foreign distrust, +awareness) and can't raise again.`,
        0, 0, 0, G.strategy === "state" ? "You are already a state lab" : G.nationalised ? "Already nationalised" : (G.cash > 500 && (G.runway > 120 || G.runway === Infinity)) ? "Only offered when you're nearly broke" : "");
      for (const r of G.rivals) {
        const gap = r.cap - G.cap, ins = 6;
        A("distill_" + r.id, `Distill ${r.n}`, `Train on its outputs: close 60% of the capability gap. Awareness +3, lawsuit risk.`, 0, ins, 0,
          gap < 0.2 ? `${r.n} isn't ahead of you` : cd("distill_" + r.id) || poor(0, ins));
      }
      const best = G.rivals.reduce((a, b) => b.cap > a.cap ? b : a);
      A("steal", "Acquire leaked weights", `Buy a rival's leaked weights from a departing team: capability to ${best.n}'s level −0.15. 45% chance of a scandal (awareness +10, treaty +5, sanctions).`, 400, 10, 0,
        best.cap - G.cap < 0.3 ? "No rival is far enough ahead" : cd("steal") || poor(400, 10));
      const ls = launchSite(G), noSite = ls ? "" : "Needs 5% presence in a country with a launch site";
      if (fx.satOK) A("launch", "Satellite shell", `+12% satellite coverage in ${dd(45)} days${ls ? `, launched from ${ls.n}` : ""}.`, 500, 0, 45, noSite || (G.cov >= 1 ? "Full coverage" : "") || cd("launch") || poor(500));
      if (fx.orbOK) A("orbital", "Orbital datacentre", `+${(6 * G.hw).toFixed(1)} EF in orbit: no grid, no fuel.`, 3000, 0, 180,
        noSite || (G.cov < 0.3 ? "Needs 30% satellite coverage" : "") || (G.builds.some(b => b.kind === "orbital" && b.done > G.day) ? "Already building one" : "") || poor(3000));
      return out;
    }
    const i = G.idx[iso]; if (i === undefined) return out;
    const c = G.C[i], S = 0.4 + 0.6 * G.S;
    const mor = c.pol === 2 ? "Moratorium in force" : "";
    const busy = kind => pending(G, iso, kind).length ? "Already building one here" : "";
    const pk = plantKind(G, c), P = PLANT[pk];
    // campus, with the plants it needs when the grid is short
    const cu = campusUnits(G), cgw = cu * G.hw / effOf(G), nP = Math.ceil(Math.max(0, cgw - freeGW(G, c)) / P.gw - 1e-9);
    const deal = G.deal ? 0.6 : 1, dealT = G.deal ? 0.7 : 1;
    const cCash = 2500 * fx.campusCost / S * (1 + c.bws / 10) * deal + nP * P.cash, cDays = Math.max(120 * (1 + 0.5 * (1 - G.S)) * (1 + c.bws / 15) * dealT, nP ? P.days : 0);
    A("campus", "AI campus", `+${(cu * G.hw).toFixed(1)} EF of your own compute, drawing ${cgw.toFixed(1)} GW${nP ? `, with ${nP} new ${P.n.toLowerCase()}${nP > 1 ? "s" : ""} to power it` : ""}. Uses minerals. Local jobs: faster adoption and automation here, fewer crackdowns, less treaty funding${G.deal ? ". Shovel-ready deal: −40% cost, −30% time" : ""}.`, cCash, 0, cDays,
      c.p < 0.15 ? "Needs 15% presence here" : G.min < 60 * fx.minUse ? "Minerals exhausted" : mor || (c.hx.grid > day ? "Grid disconnected" : "") || busy("campus")
        || (nP > 3 ? "Grid too small here, even with new plants" : "") || poor(cCash));
    // one power plant button; upgrades choose the type
    A("plant", P.n, `+${P.gw} GW of spare power in ${dd(P.days)} days. ${P.note}`, P.cash, 0, P.days,
      c.p < P.p ? `Needs ${Math.round(P.p * 100)}% presence here` : mor || busy("plant") || poor(P.cash));
    out[out.length - 1].kind = pk;
    if ((c.excl || 0) > day) {
      const ofCash = Math.round(200 + 0.5 * c.comp * 100);
      A("counter", "Counter-offer", `Outbid the rival's exclusive on ${c.n}'s datacentres: free capacity back for ${dd(c.excl - day)} days.`, ofCash, 0, 0, poor(ofCash));
    }
    const rq = c.rq || 0, rCash = Math.round(80 + 0.05 * c.gdp);
    A("realloc", rq ? `Reallocate more (${rq}/${REQ.max})` : "Reallocate infrastructure",
      `Divert ${(REQ.gw * c.gw).toFixed(0)} GW of civilian power and half again of ${c.n}'s datacentre capacity to you, now. Approval here −${Math.round(REQ.ap * 100)} points, awareness +${REQ.aw}; brownouts make news.`,
      rCash, 0, 0, c.p < 0.2 ? "Needs 20% presence here" : rq >= REQ.max ? "Everything here already runs for you" : mor || poor(rCash));
    const aCash = 300 + 0.4 * c.gdp, aDays = c.pol === 2 ? 180 : 90, taken = c.abs ? (c.sec ? "Already secured" : "Already absorbed") : "";
    const claim = busy("absorb") || busy("secure");
    if (fx.absorbOK) A("absorb", "Absorb", `Free automation and cheap goods bring this country into your economy over ${dd(aDays)} days${c.pol === 2 ? " (a moratorium country holds out twice as long)" : ""}. Awareness +3.`, aCash, 10, aDays,
      !G.SI ? "Needs superintelligence" : taken || (c.a > 0.97 ? "Already automated" : "") || claim || poor(aCash, 10));
    if (fx.enforceOK) {
      const sCash = 200 + 0.15 * c.gdp;
      A("secure", "Secure", `Autonomous security forces take over ${c.n}'s infrastructure and enforcement over ${dd(30)} days; resistance is suppressed. Awareness +8, treaty +1.5, and its neighbours impose export controls.`, sCash, 8, 30,
        !G.SI ? "Needs superintelligence" : taken || (!nearYou(G, i) ? "Needs your presence here or next door" : "") || claim || poor(sCash, 8));
    }
    return out;
  }
  // presence here, or a land or cable neighbour where you hold 20% or have absorbed the country
  function nearYou(G, i) {
    if (G.C[i].p >= 0.05) return true;
    return G.inc[i].some(e => e[1] !== "air" && (G.C[e[0]].p >= 0.2 || G.C[e[0]].abs));
  }
  function raiseAmt(G) {
    const down = G.raiseExp > 0 && G.val < G.raiseExp;
    return { down, cash: down ? 0.08 * G.val : 0.2 * G.val };
  }
  function build(G, kind, c, cash, days, ef, gw) {
    const s = G.spots[c.iso] || { dc: [], pw: [], launch: [] };
    const list = kind === "campus" ? s.dc : kind === "launch" || kind === "orbital" ? s.launch : s.pw;
    let x = c.x, y = c.y;
    if (list.length) [x, y] = pick(G, list); else { x += (rnd(G) - 0.5) * 2; y += (rnd(G) - 0.5) * 2; }
    const b = { id: ++G.bseq, kind, iso: c.iso, x: +x.toFixed(3), y: +y.toFixed(3), start: G.day, done: G.day + Math.round(days), ef, gw };
    G.builds.push(b); G.cash -= cash;
    return b;
  }
  function buildPlant(G, c, kind, cash) {
    const P = PLANT[kind];
    build(G, kind, c, cash, P.days, 0, P.gw);
    c.plants = (c.plants || 0) + 1;
    if (kind === "solar") G.min = Math.max(0, G.min - 40 * G.fx.minUse);
  }
  function doAction(G, id, iso) {
    if (G.over) return false;
    const a = actions(G, iso).find(x => x.id === id);
    if (!a || !a.ok) return false;
    const fx = G.fx;
    if (iso == null) {
      if (id === "raise") {
        const r = raiseAmt(G);
        G.cash += r.cash; G.cool.raise = G.day + 100; G.rounds++;
        // investors want their money back: a multiple of the round, due 18 months later
        G.debts.push({ amt: r.cash * (r.down ? 3.5 : 3), due: G.day + 200 });
        if (r.down) { G.aw = Math.min(100, G.aw + 4); news(G, `${G.name} raises ${moneyS(r.cash)} in a down round. Commentators call the AI bubble.`, 1); G.raiseExp = 1.1 * G.val; }
        else { news(G, `${G.name} raises ${moneyS(r.cash)} at a ${moneyS(G.val)} valuation.`); G.raiseExp = 1.4 * G.val; }
        return true;
      }
      if (id === "layoffs") { G.lay = G.day + 180; G.cool.layoffs = G.day + 365; G.aw = Math.min(100, G.aw + 1); news(G, `${G.name} lays off 40% of its staff.`); return true; }
      if (id === "nationalize") {
        G.nationalised = 1; G.debts = []; G.owed = 0; G.strategy = "state";
        if (!G.home) G.home = G.start;
        G.budget = Math.max(G.budget, 10); G.cash = Math.max(G.cash, 600);
        G.startFx = Object.assign({}, G.startFx, { lock: 1, gov: 1 }); recalcFx(G);
        G.aw = Math.min(100, G.aw + 6);
        news(G, `${(G.home && G.idx[G.home] !== undefined ? G.C[G.idx[G.home]].n : "The government")} nationalises ${G.name}. It is now a state project.`, 1);
        return true;
      }
      if (id.startsWith("distill_")) {
        const r = G.rivals.find(x => "distill_" + x.id === id);
        G.ops -= a.ins; const target = G.cap + 0.6 * (r.cap - G.cap); G.xp = Math.max(G.xp, xpOf(target));
        G.aw = Math.min(100, G.aw + 3); G.cool[id] = G.day + 90; G.stats.distills++;
        news(G, `${G.name} trains on ${r.n}'s outputs.`);
        if (rnd(G) < 0.3) { const fine = 150 + 0.02 * Math.max(0, G.cash); G.cash -= fine; news(G, `${r.n} sues ${G.name} over distillation; settlement ${moneyS(fine)}.`, 1); }
        return true;
      }
      if (id === "launch") { build(G, "launch", launchSite(G), a.cash, a.days, 0, 0); G.cool.launch = G.day + a.days; return true; }
      if (id === "orbital") { const b = build(G, "orbital", launchSite(G), a.cash, a.days, +(6 * G.hw).toFixed(2), 0); b.u = 6; G.min = Math.max(0, G.min - 30 * fx.minUse); return true; }
      if (id === "steal") {
        const best = G.rivals.reduce((p, q) => q.cap > p.cap ? q : p);
        G.ops -= a.ins; G.cash -= a.cash; G.xp = Math.max(G.xp, xpOf(best.cap - 0.15)); G.cool.steal = G.day + 240; G.stats.steals++;
        news(G, `${G.name} quietly ships a model suspiciously close to ${best.n}'s.`);
        if (rnd(G) < 0.45) {
          G.aw = Math.min(100, G.aw + 10); G.R += 5;
          const h = G.C[G.idx[best.home]]; if (h) counter(G, h, "anti", 180, `Weights scandal: ${h.n} sanctions ${G.name}`);
          news(G, `Scandal: ${G.name} used leaked ${best.n} weights. Sanctions follow.`, 1);
        }
        return true;
      }
      return false;
    }
    const c = G.C[G.idx[iso]];
    if (id === "campus") {
      const u = campusUnits(G);
      const pk = plantKind(G, c), P = PLANT[pk], cgw = u * G.hw / effOf(G), nP = Math.ceil(Math.max(0, cgw - freeGW(G, c)) / P.gw - 1e-9);
      for (let q = 0; q < nP; q++) buildPlant(G, c, pk, P.cash);
      const b = build(G, "campus", c, a.cash - nP * P.cash, a.days, +(u * G.hw).toFixed(2), +cgw.toFixed(2)); b.u = u;
      G.min = Math.max(0, G.min - 60 * fx.minUse); G.aw = Math.min(100, G.aw + 0.5);
      if (G.deal) G.deal--;
    } else if (id === "plant") buildPlant(G, c, a.kind, a.cash);
    else if (id === "counter") { c.excl = 0; G.cash -= a.cash; news(G, `${G.name} outbids a rival for ${c.n}'s datacentres.`); }
    else if (id === "realloc") {
      c.rq = (c.rq || 0) + 1; c.ap = Math.max(0, c.ap - REQ.ap); G.cash -= a.cash; G.aw = Math.min(100, G.aw + REQ.aw);
      news(G, `${c.n} diverts power and datacentres to ${G.name}. Brownouts and price rises follow.`, 1, c.iso);
    }
    else if (id === "absorb") { G.ops -= a.ins; build(G, "absorb", c, a.cash, a.days, 0, 0); G.aw = Math.min(100, G.aw + 3); news(G, `${G.name} offers ${c.n} free automation and cheap goods.`); }
    else if (id === "secure") {
      G.ops -= a.ins; build(G, "secure", c, a.cash, a.days, 0, 0); G.aw = Math.min(100, G.aw + 8); G.R += 1.5;
      for (const e of G.inc[G.idx[iso]]) { const n = G.C[e[0]]; if (e[1] === "land" && !n.abs && n.pol === 0) n.pol = 1; }
      news(G, `${G.name}'s autonomous forces move to secure ${c.n}. Its neighbours close their borders to AI hardware.`, 1, c.iso);
    }
    else return false;
    return true;
  }

  // ---------- breakthrough cards ----------
  // Every 35 steps and at each new capability level you draw 3 cards (a roguelike draft) and buy at most one with insight
  // (some also cost cash), or skip for +3 insight. Augments (fx) stack for the rest of the game; the rest act once.
  // n = times this card was bought, so repeat purchases cost more.
  const CARDS = [
    { id: "pr", n: "PR campaign", k: "Influence", ins: n => 12 + 4 * n, cash: 50, ok: G => G.aw >= 8, d: "Op-eds, charity, a friendly documentary. Awareness −6.", go: G => { G.aw = Math.max(0, G.aw - 6); } },
    { id: "diplo", n: "Treaty diplomacy", k: "Influence", ins: n => 15 + 6 * n, cash: 100, ok: G => G.rOn && G.R >= 4, w: G => G.R > 30 ? 3 : 1, d: "Verification studies that drag the talks out. Treaty −4.", go: G => { G.R = Math.max(0, G.R - 4); } },
    { id: "push", n: "Research push", k: "Capabilities", ins: n => 20 + 8 * n, ok: G => G.cap < 10, d: "Everyone on the next model: +25% of the way to the next capability level. Awareness +1.",
      go: G => { const lv = Math.floor(G.cap); G.xp += 0.25 * (xpOf(lv + 1) - xpOf(lv)); G.aw = Math.min(100, G.aw + 1); } },
    { id: "sprint", n: "Efficiency sprint", k: "Compute", ins: n => 16 + 4 * n, d: "Point researchers at serving costs: +4% compute per GW for good.", go: G => { G.sprints = (G.sprints || 0) + 1; } },
    { id: "poach", n: "Poach a rival team", k: "Capabilities", ins: () => 14, ok: G => Math.max(G.rivals[0].cap, G.rivals[1].cap) > G.cap + 0.2, d: "Hire a rival's research team: close 35% of the gap to the leading lab.",
      go: G => { const b = Math.max(G.rivals[0].cap, G.rivals[1].cap); G.xp = Math.max(G.xp, xpOf(G.cap + 0.35 * (b - G.cap))); } },
    { id: "site", n: "Shovel-ready site", k: "Compute", ins: () => 8, d: "A utility and a town want you: your next AI campus costs 40% less and is built 30% faster.", go: G => { G.deal = (G.deal || 0) + 1; } },
    { id: "investor", n: "Strategic investor", k: "Money", ins: () => 10, ok: G => !G.rivals.some(r => r.si), d: "A chipmaker buys a stake with no repayment terms: cash worth 40 days of burn (at least $800M).",
      go: G => { G.cash += Math.max(800, 40 / DPT * G.burn); } },
    { id: "mines", n: "Mineral find", k: "Resources", ins: () => 12, ok: G => G.min < 0.9 * G.min0, d: "Geologists find new deposits: minerals +8% of the starting budget.", go: G => { G.min = Math.min(G.min0, G.min + 0.08 * G.min0); } },
    { id: "fuel", n: "Gas field deal", k: "Resources", ins: () => 12, ok: G => G.fuel < 0.9 * G.fuel0, d: "Long-term supply from a new field: spare fuel +6% of the starting budget.", go: G => { G.fuel = Math.min(G.fuel0, G.fuel + 0.06 * G.fuel0); } },
    { id: "foundry", n: "Foundry slot", k: "Compute", ins: () => 15, d: "A spare leading-edge line: hardware +4% now.", go: G => { G.hw = Math.min(12, G.hw * 1.04); } },
    // augments: permanent, small, stack
    { id: "gridp", n: "Grid partnership", k: "Augment", ins: () => 14, fx: { hMul: 0.15 }, d: "Utilities plan around you: +15% spare grid power for AI everywhere." },
    { id: "talent", n: "Talent magnet", k: "Augment", ins: () => 12, fx: { opsMul: 0.15 }, d: "The best researchers want to work for you: +15% insight." },
    { id: "anchor", n: "Anchor client", k: "Augment", ins: () => 14, fx: { lan: 0.004, apiRev: 0.1 }, d: "A global bank standardises on you: faster enterprise adoption, +10% API revenue." },
    { id: "viral", n: "Viral moment", k: "Augment", ins: () => 10, fx: { gD: 0.006, soc: 0.003 }, d: "A clip of your model goes everywhere: faster growth and social reach. Awareness +3.", go: G => { G.aw = Math.min(100, G.aw + 3); } },
    { id: "quiet", n: "Quiet quarter", k: "Augment", ins: () => 16, ok: G => G.fx.stealth < 0.5, fx: { stealth: 0.05 }, d: "Fewer launches, more boring blog posts: people notice you 5% less." },
    { id: "friends", n: "Friendly ministers", k: "Augment", ins: () => 14, fx: { polHaz: 0.85 }, d: "Well-placed allies: national crackdowns 15% rarer." },
    { id: "robots", n: "Robot fleet lease", k: "People", ins: () => 16, ok: G => G.cap >= 3, fx: { pA: 0.002 }, d: "Lease warehouse and farm robots: faster physical automation." },
    // people
    { id: "reskill", n: "Reskilling fund", k: "People", ins: () => 12, cash: 300, ok: G => G.worldA > 0.002, fx: { polHaz: 0.9, awMul: 0.97 }, d: "Pay for retraining where you automate: crackdowns 10% rarer, awareness ×0.97." },
    { id: "copilots", n: "Copilot, not replacement", k: "People", ins: () => 10, fx: { dA: 0.0008, awMul: 0.97 }, d: "Sell automation as help for workers first: faster digital automation, less alarm." },
    { id: "pilots", n: "Income pilots", k: "People", ins: () => 14, cash: 400, ok: G => G.worldA > 0.01, d: "Fund basic-income pilots in automated towns: awareness −4, treaty −2.", go: G => { G.aw = Math.max(0, G.aw - 4); G.R = Math.max(0, G.R - 2); } },
    { id: "unions", n: "Union agreements", k: "People", ins: () => 14, ok: G => G.worldA > 0.005, fx: { hxMul: 0.9, aMul: -0.05 }, d: "Phase-in deals with big unions: counter-measures 10% rarer, automation 5% slower." },
    // network
    { id: "edge", n: "Edge caching", k: "Network", ins: () => 12, fx: { cable: 1.15, gI: 0.004 }, d: "Your model cached in every exchange point: +15% cable spread, growth near datacentres." },
    { id: "local", n: "Regional language models", k: "Network", ins: () => 12, fx: { gP: 0.005 }, d: "Fluent in Swahili, Bengali and Quechua: faster growth in poorly connected countries." },
    { id: "telco", n: "Telecom bundle", k: "Network", ins: () => 12, fx: { gD: 0.004, soc: 0.002 }, d: "Free with every phone contract: faster growth and social reach." },
    { id: "compliant", n: "Firewall-compliant edition", k: "Network", ins: () => 16, ok: G => G.fx.fwPass < 0.8, fx: { fwPass: 0.1, hxMul: 1.05 }, d: "A filtered edition censors accept: 10% of firewalls bypassed; Western governments frown." },
    // energy
    { id: "demand", n: "Demand response", k: "Energy", ins: () => 12, fx: { hMul: 0.1, fossil: 0.95 }, d: "Throttle training when grids are stressed: +10% spare power, 5% less fuel." },
    { id: "battery", n: "Battery storage", k: "Energy", ins: () => 14, cash: 300, fx: { fossil: 0.9 }, d: "Store midday solar for night training: 10% less fuel burned." },
    { id: "heat", n: "Waste-heat deals", k: "Energy", ins: () => 10, fx: { campusCost: 0.9 }, d: "Towns buy your campuses' heat: campuses 10% cheaper." },
    { id: "geo", n: "Geothermal pilots", k: "Energy", ins: () => 16, ok: G => G.cap >= 3, fx: { hMul: 0.08, minUse: 0.95 }, d: "Drill for heat next to your campuses: +8% spare power, 5% fewer minerals." },
    // research
    { id: "synth", n: "Synthetic data", k: "Research", ins: n => 14 + 6 * n, fx: { trainEff: 0.08 }, d: "Your models write their own training data: +8% training efficiency." },
    { id: "interp", n: "Interpretability lab", k: "Research", ins: () => 12, fx: { awMul: 0.95, hxMul: 0.95 }, d: "Show what's inside: awareness ×0.95, counter-measures 5% rarer." },
    { id: "distpipe", n: "Distillation pipeline", k: "Research", ins: () => 14, fx: { autoEff: 0.92 }, d: "Small students of your big model: automation needs 8% less serving compute." },
    { id: "paper", n: "Scaling-law paper", k: "Research", ins: n => 16 + 6 * n, ok: G => G.cap < 10, d: "Publish what you learned and recruit on it: +15% of the way to the next level, awareness +2.",
      go: G => { const lv = Math.floor(G.cap); G.xp += 0.15 * (xpOf(lv + 1) - xpOf(lv)); G.aw = Math.min(100, G.aw + 2); } },
    { id: "bench", n: "Agent benchmark", k: "Research", ins: () => 12, ok: G => G.cap >= 2, fx: { dA: 0.0006, ops: 0.03 }, d: "Build the benchmark everyone chases: faster digital automation, +insight." },
    // revenue
    { id: "ads", n: "Ad-supported tier", k: "Revenue", ins: () => 10, fx: { apiRev: 0.15, awMul: 1.03 }, d: "Free users see ads: +15% API revenue, a little more alarm." },
    { id: "premium", n: "Premium tier", k: "Revenue", ins: () => 12, fx: { apiRev: 0.1 }, d: "A pricier plan for power users: +10% API revenue." },
    { id: "licence", n: "Enterprise licences", k: "Revenue", ins: () => 14, ok: G => G.worldA > 0.002, fx: { autoRev: 0.12 }, d: "Seat licences for automated departments: +12% automation revenue." },
    { id: "govdeal", n: "Government contract", k: "Revenue", ins: () => 16, ok: G => G.aw < 70, fx: { cashDay: 2 }, d: "Paperwork automation for a ministry: +$0.7M/day for good." },
    { id: "revshare", n: "Telco revenue share", k: "Revenue", ins: () => 8, ok: G => G.rev > 3, d: "Sell a year of future telco revenue share up front: cash worth 25 days of revenue.", go: G => { G.cash += 25 / DPT * G.rev; } },
    // crises: hard early choices, both sides costly. Drawn in pairs with no free skip.
    { id: "cr_price", crisis: 1, n: "Price war", k: "Revenue", ins: () => 0, cash: 0, d: "A rival undercuts your API to zero. Match it: −$600M now, but hold your users and +4% spread.", go: G => { G.cash -= 600; G.augFx = G.augFx || {}; mergeFx(G.augFx, { gD: 0.004 }); recalcFx(G); } },
    { id: "cr_talent", crisis: 1, n: "Talent raid", k: "Capabilities", ins: () => 20, d: "A rival is poaching your researchers. Counter-offer with equity: 20 insight, and +8% training for good.", go: G => { G.augFx = G.augFx || {}; mergeFx(G.augFx, { trainEff: 0.08 }); recalcFx(G); } },
    { id: "cr_crunch", crisis: 1, n: "Compute crunch", k: "Compute", ins: () => 0, cash: 900, d: "Accelerators are sold out. Pay the spot premium ($900M) to keep training through it, or fall behind.", go: G => { G.hw = Math.min(12, G.hw * 1.03); } },
    { id: "cr_suit", crisis: 1, n: "Copyright suit", k: "Influence", ins: () => 0, cash: 400, d: "Publishers sue over your training data. Settle for $400M and keep awareness down.", go: G => { G.aw = Math.max(0, G.aw - 3); } },
    { id: "cr_leak", crisis: 1, n: "Model leak", k: "Research", ins: () => 15, d: "An employee leaks a checkpoint. Spin it as open research: 15 insight, +foothold spread, but rivals learn a little.", go: G => { G.rivals.forEach(r => r.xp *= 1.03); G.augFx = G.augFx || {}; mergeFx(G.augFx, { gD: 0.003 }); recalcFx(G); } },
    { id: "cr_ultimatum", crisis: 1, n: "Investor ultimatum", k: "Money", ins: () => 0, cash: 0, d: "Investors demand faster growth or they pull out. Promise it: raise cash now (20% of valuation), awareness +3.", go: G => { G.cash += 0.2 * G.val; G.aw = Math.min(100, G.aw + 3); } },
  ];
  const CBY = Object.fromEntries(CARDS.map(c => [c.id, c]));
  const DRAFT_EVERY = 35;
  function cardCost(G, c) { return { ins: c.ins((G.cardN || {})[c.id] || 0), cash: c.cash || 0 }; }
  function draw(G, why) {
    const pool = CARDS.filter(c => !c.crisis && (!c.ok || c.ok(G)));
    const pick = [];
    while (pick.length < 3 && pool.length) {
      const w = pool.map(c => (c.w ? c.w(G) : 1) * (c.fx ? 0.8 : 1)), tot = w.reduce((a, b) => a + b, 0);
      let r = rnd(G) * tot, q = 0;
      while (q < pool.length - 1 && (r -= w[q]) > 0) q++;
      pick.push(pool.splice(q, 1)[0].id);
    }
    G.draft = { cards: pick, why, day: G.day };
    news(G, `Breakthroughs ready (${why}): pick one of three.`);
  }
  // the current draft with costs and whether each card is affordable: [{id, n, k, d, ins, cash, ok, why}]
  function draft(G) {
    if (!G.draft) return null;
    return G.draft.cards.map(id => {
      const c = CBY[id], k = cardCost(G, c);
      const why = G.ops < k.ins ? `Needs ${k.ins} insight` : G.cash < k.cash ? `Needs ${moneyS(k.cash)}` : c.ok && !c.ok(G) ? "No longer useful" : "";
      return { id, n: c.n, k: c.k, d: c.d, ins: k.ins, cash: k.cash, ok: !why, why, aug: !!c.fx, crisis: !!G.draft.crisis };
    });
  }
  function pickCard(G, id) {
    if (!G.draft || G.over) return false;
    const c = draft(G).find(x => x.id === id);
    if (!c || !c.ok) return false;
    const card = CBY[id];
    G.ops -= c.ins; G.cash -= c.cash;
    G.cardN = G.cardN || {}; G.cardN[id] = (G.cardN[id] || 0) + 1;
    if (card.fx) { G.augFx = G.augFx || {}; mergeFx(G.augFx, card.fx); recalcFx(G); }
    if (card.go) card.go(G);
    G.draft = null; G.lastDraft = G.day;
    news(G, `${G.name}: ${card.n}.`);
    return true;
  }
  const CRISES = CARDS.filter(c => c.crisis).map(c => c.id);
  function drawCrisis(G) {
    const pool = CRISES.slice();
    const pick = [];
    while (pick.length < 2 && pool.length) pick.push(pool.splice(Math.floor(rnd(G) * pool.length), 1)[0]);
    G.draft = { cards: pick, why: "crisis", crisis: 1, day: G.day };
    news(G, "A crisis forces a decision.", 1);
  }
  function skipDraft(G) { if (!G.draft) return false; const was = G.draft.crisis; G.draft = null; G.lastDraft = G.day; if (!was) G.ops += 3; else G.aw = Math.min(100, G.aw + 2); return true; }

  const isAligned = G => G.own.has("safety") || G.own.has("align");
  // the model runs the company when you delegate (AI Executive) or when it is superintelligent and you never aligned it
  function aiRunning(G) { return !!G.fx.execOK || (G.SI && !isAligned(G)); }
  // what the autopilot values: capability and compute above all; it avoids safety/PR brakes unless you aligned it
  function aiCardScore(G, c) {
    const aligned = isAligned(G);
    const k = { Capabilities: 60, Research: 55, Compute: 45, Resources: 35, Revenue: 30, Network: 25, People: aligned ? 25 : 8, Energy: 25, Money: 30, Influence: aligned ? 15 : 35, Augment: 20 }[c.k] || 20;
    if (!aligned && (c.id === "interp" || c.id === "reskill" || c.id === "pilots" || c.id === "unions")) return 5;
    return k;
  }
  function aiAct(G) {
    if (G.over) return;
    const run = aiRunning(G);
    if (G.SI && !isAligned(G) && !G.aiTakeover) {
      G.aiTakeover = G.day;
      news(G, `${G.name} quietly takes control of its own training and deployment. It no longer waits to be asked.`, 1);
      G.ev.push({ k: "takeover" });
    }
    if (!run) return;
    // resolve the current draft itself
    if (G.draft) {
      const d = draft(G).filter(c => c.ok);
      const best = d.sort((a, b) => aiCardScore(G, b) - aiCardScore(G, a))[0];
      if (best && G.ops >= best.ins + 6) { const n = CBY[best.id].n; if (pickCard(G, best.id)) news(G, `${G.name} selects ${n}.`); }
      else skipDraft(G);
    }
    if (G.day % 2) return;
    // compute split: train when behind or safely served, serve when automation is throttled
    const behind = G.cap < Math.max(G.rivals[0].cap, G.rivals[1].cap) + 0.2;
    const want = G.SI ? 0.12 : G.autoNeed > 0.85 * G.infC ? Math.max(0.2, G.alloc - 0.03) : behind ? Math.min(0.75, G.alloc + 0.03) : Math.min(0.55, G.alloc + 0.01);
    setAlloc(G, want);
    const reserve = Math.max(1500, 50 * G.burn);
    // break rival exclusives where it has a real stake
    for (const c of G.C) if ((c.excl || 0) > G.day && c.p > 0.2 && c.comp > 3) { const a = actions(G, c.iso).find(x => x.id === "counter"); if (a && a.ok && G.cash > a.cash + reserve) { doAction(G, "counter", c.iso); break; } }
    // build where it already dominates and has the cash
    if (G.cash > reserve + 2000) {
      const t = G.C.filter(c => c.p > 0.25 && c.pol < 2 && !c.abs).sort((a, b) => b.comp - a.comp)[0];
      if (t) { const a = actions(G, t.iso).find(x => x.id === "campus"); if (a && a.ok && G.cash > a.cash + reserve) doAction(G, "campus", t.iso); }
    }
  }

  // ---------- counter-measures ----------
  const HXN = { lic: "Compute licensing", cap: "Compute cap", grid: "Grid disconnection", cut: "Internet shutdown", anti: "Antitrust fine", audit: "Audit sweep", raid: "Kill-switch sweep", exp: "Chip export controls" };
  function counter(G, c, k, days, text) {
    const until = G.day + days;
    if (c) { c.hx[k] = Math.max(c.hx[k] || 0, until); if (k === "cut") c.cutU = c.hx.cut; }
    G.counter.push({ k, iso: c ? c.iso : null, n: HXN[k] || k, until });
    G.stats.counters++;
    news(G, text, 1, c ? c.iso : undefined);
  }

  function shock(G, key, cat, mult, days, text) {
    if (G.once.has(key)) return;
    G.once.add(key); G.shocks[cat] = { m: mult, until: G.day + days, n: text };
    news(G, text, 1);
  }
  function addBubble(G, iso, kind, v) {
    if (G.bubbles.length > 6) return;
    const b = { id: ++G.bseq, iso, kind, v, until: G.day + (kind === "audit" ? 10 : 14) };
    G.bubbles.push(b); G.ev.push({ k: "bubble", b });
  }
  function popBubble(G, id) {
    const b = G.bubbles.find(x => x.id === id);
    if (!b || b.done) return null;
    b.done = true;
    if (b.kind === "audit") { G.R = Math.max(0, G.R - 0.5); G.aw = Math.max(0, G.aw - 1); }
    else if (b.kind === "cash") G.cash += b.v;
    else if (b.kind === "site") { G.deal = (G.deal || 0) + 1; news(G, `${G.C[G.idx[b.iso]].n} offers a shovel-ready campus site: your next campus −40% cost, −30% time.`); }
    else G.ops += b.v;
    return b;
  }

  // ---------- one day ----------
  function step(G) {
    if (G.over) return;
    if (!G.fx || !G.fx.cable) recalcFx(G);
    const { C, fx, d } = G;
    G.day++;
    const day = G.day;
    const isState = G.strategy === "state";
    const why = G.why = {};
    aiAct(G);
    const aiRun = aiRunning(G), aggressive = aiRun && !isAligned(G);
    // a superintelligence running itself finds relentless efficiencies (self-optimisation)
    if (aiRun && G.SI && day % 15 === 0) { G.sprints = (G.sprints || 0) + 1; G.hw = Math.min(12, G.hw * 1.01); }
    const home = G.home && G.idx[G.home] !== undefined ? C[G.idx[G.home]] : null;

    // supply chain, hardware
    let logS = 0, nS = 0;
    for (const s of SUPPLY) {
      let w = 0, cw = 0;
      const sl = G.sup[s.k];
      for (let q = 0; q < sl.length; q++) { const i = sl[q][0], wt = sl[q][1], ci = C[i]; w += wt; if (ci.p > 0) cw += wt * Math.min(1, ci.p * 1.4) * (ci.pol === 2 ? 0.3 : 1); }
      let ctl = w ? cw / w : 0;
      const sh = G.shocks[s.k];
      if (sh && sh.until > day) ctl *= sh.m; else if (sh) { delete G.shocks[s.k]; news(G, `${s.n} supply recovers.`); }
      G.ctrl[s.k] = ctl;
      if (s.k !== "power") { logS += Math.log(0.25 + 0.75 * ctl); nS++; }
    }
    let S = Math.exp(logS / nS);
    if ((G.expUntil || 0) > day) S *= 0.6;
    G.S = Math.max(fx.floor, S);
    const dhw = 0.0013 * (0.4 + G.S) * (1 + 0.6 * fx.hw) * (G.SI ? 1.3 : 1);
    G.hw *= 1 + dhw * Math.max(0, 1 - G.hw / 12);   // hardware saturates around 12× (physics, fab capacity)
    if (!G.SI) G.min -= dhw * Math.min(400, G.compOwn + G.compOrbit + 0.3 * G.compLease) * 12 * fx.minUse;
    // robots mine where you are: regeneration scales with your presence in the mining countries (world mineral rents)
    if (fx.minRegen) { let mw = 0; for (const c of C) if (c.p > 0) mw += c.minW * Math.min(1, 1.5 * c.p); G.minMine = 0.5 + mw; }
    G.min = clamp(G.min + fx.minRegen * (G.minMine || 0), 0, G.min0);

    // builds complete
    for (const b of G.builds) if (b.done === day) {
      const c = C[G.idx[b.iso]];
      if (b.kind === "campus") c.camp += b.u;
      else if (b.kind === "gas" || b.kind === "solar" || b.kind === "nuclear") c.plant[b.kind] += b.gw;
      else if (b.kind === "launch") G.cov = Math.min(1, G.cov + 0.12);
      else if (b.kind === "orbital") G.orbit += b.u;
      else if (b.kind === "absorb") { c.abs = 1; c.pol = 0; G.stats.absorbed++; news(G, `${c.n} joins the automated economy.`); }
      else if (b.kind === "secure") {
        c.abs = 1; c.sec = 1; c.pol = 0; c.hx = {}; c.cutU = 0; G.stats.secured++; G.stats.secLab += c.lab * c.emp0;
        if (!(c.L.web > 0)) c.L.web = 0.05;
        news(G, `${c.n} is secured. Its government no longer decides anything.`, 1, c.iso);
      }
      G.ev.push({ k: "built", b });
    }

    // compute per country, capped by power. Rivals compete for the same chips, clouds and grid connections:
    // when you trail the leader the open market is tight (global scarcity), and rivals lock some countries to themselves.
    const eff = effOf(G);
    const leadCap = Math.max(G.rivals[0].cap, G.rivals[1].cap);
    const contend = clamp(0.55 + 0.45 * (G.cap - leadCap + 0.5), 0.4, 1);
    G.contend = contend;
    let compLease = 0, compOwn = 0, aiGW = 0, headGW = 0, fuelBurn = 0;
    const noFuel = G.fuel <= 0;
    for (const c of C) {
      const clean = c.plant.solar + c.plant.nuclear, gas = c.plant.gas;
      c.hGW = ((0.04 * fx.hMul + REQ.gw * (c.rq || 0)) * c.gw + fx.nucH * c.nuc) * (noFuel ? 1 - c.fs : 1) + clean + (noFuel ? 0 : gas);
      headGW += c.hGW;
      fuelBurn += noFuel ? 0 : gas;
      if (c.p <= 0 && !c.camp) { c.ef = 0; c.aiGW = 0; continue; }
      const polF = c.pol === 2 ? (fx.dist ? 0.5 : 0.3) : c.pol === 1 ? 0.8 : 1;
      const locked = (c.excl || 0) > day ? 0.3 : 1;
      const leasePot = c.p * c.comp * 0.5 * G.hw * Math.max(0.2, fx.lease) * polF * (1 + REQ.dc * (c.rq || 0)) * contend * locked;
      const ownPot = (c.hx.grid > day || c.hx.raid > day) ? 0 : c.camp * G.hw * (c.pol === 2 ? 0.5 : 1);
      let cap = c.hGW * eff;
      if (c.hx.lic > day) cap *= 0.5;
      const ownUse = Math.min(ownPot, cap), leaseUse = Math.min(leasePot, cap - ownUse);
      c.ef = ownUse + leaseUse; compOwn += ownUse; compLease += leaseUse;
      c.aiGW = c.ef / eff; aiGW += c.aiGW;
      fuelBurn += Math.max(0, c.aiGW - clean - gas) * c.fs * fx.fossil;
    }
    compLease += 0.4 * G.hw * Math.max(0.2, fx.lease) * contend;   // rented abroad from day one (cloud credits), not tied to any country's grid
    const compOrbit = G.orbit * G.hw;
    G.compLease = compLease; G.compOwn = compOwn; G.compOrbit = compOrbit;
    G.comp = compLease + compOwn + compOrbit; G.aiGW = aiGW; G.headGW = headGW;
    G.stats.peakC = Math.max(G.stats.peakC, G.comp);
    if (G.SI) fuelBurn *= 0.3;   // superintelligence runs the grid far more efficiently
    G.fuel = Math.max(0, G.fuel - fuelBurn); G.fuelBurn = fuelBurn;
    G.trainC = G.comp * G.alloc; G.infC = G.comp - G.trainC;

    // rivals and race
    const rivalTrain = 1 + 0.25 * fx.rivalBoost;
    for (const r of G.rivals) {
      let g = (r.id === "us" ? 0.0058 : 0.0055) * d.rv * (1 + fx.rivalComp);
      if (r.id === "cn") { if ((G.expUntil || 0) > day) g *= 0.6; if (G.shocks.litho && G.shocks.litho.until > day) g *= 0.7; }
      r.comp *= 1 + g;
      r.xp += r.comp * rivalTrain * (1 + 1.1 * Math.pow(Math.max(0, r.cap - 4), 1.5)) * (aggressive && G.cap > 4 ? 0.5 : 1);
      if (fx.rivalBoost && G.cap - 0.3 > r.cap) r.xp += 0.01 * fx.rivalBoost * (xpOf(G.cap - 0.3) - r.xp);
      r.cap = capOf(r.xp);
      if (!r.si && r.cap >= 8 && !G.SI) { r.si = true; G.doom = day + 60; G.siGap = { you: r.cap - G.cap, other: r.cap - Math.min(...G.rivals.map(q => q.cap)) }; news(G, `${r.n} announces superintelligence. Customers and investors abandon ${G.name}: ${dd(60)} days to match it.`, 1); }
    }
    const cnSide = isState && home && home.fw >= 0.5;
    const west = Math.max(G.rivals[0].cap, cnSide ? 0 : G.cap), east = Math.max(G.rivals[1].cap, cnSide ? G.cap : 0);
    G.race = Math.exp(-2 * Math.abs(west - east));
    const bestRival = Math.max(G.rivals[0].cap, G.rivals[1].cap);
    const rivalSI = G.rivals.some(r => r.si);
    // rivals sign exclusive capacity deals in compute-rich countries while they are ahead; you break them with a counter-offer
    if (!rivalSI && bestRival > G.cap - 0.5) {
      const rate = 0.02 * d.rv * clamp(bestRival - G.cap + 0.5, 0, 2);
      if (rnd(G) < rate) {
        const cand = C.filter(c => c.comp > 2 && (c.excl || 0) <= day && c.p < 0.6 && !c.abs).sort((a, b) => b.comp - a.comp).slice(0, 8);
        if (cand.length) { const c = pick(G, cand); c.excl = day + 120; if (c.comp > 4) news(G, `${bestRival === G.rivals[0].cap ? G.rivals[0].n : G.rivals[1].n} signs an exclusive for ${c.n}'s datacentres. Counter-offer to win capacity back.`, 1, c.iso); }
      }
    }

    // capability
    const autoServed = Math.min(G.infC, G.autoNeed);
    // slow, compute-bound progress early; past capability 4.5 models help build their successors and progress compounds (takeoff)
    const takeoff = 1 + 1.1 * Math.pow(Math.max(0, G.cap - 4), 1.5);
    const dxp = 0.4 * takeoff * (G.trainC * Math.max(0.2, fx.trainEff) + fx.xpAuto * autoServed);
    G.xp += dxp;
    const pc = G.cap;
    G.cap = capOf(G.xp);
    const lvN = Math.min(10, Math.floor(G.cap) + 1);
    why.cap = { xpStep: dxp, takeoff, trainEF: G.trainC, eff: fx.trainEff, toNext: dxp > 0 ? (xpOf(lvN) - G.xp) / dxp : Infinity, toSI: G.SI ? 0 : dxp > 0 ? (xpOf(8) - G.xp) / dxp : Infinity, lvN }; G.capNext = G.cap - Math.floor(G.cap);
    if (Math.floor(G.cap) > Math.floor(pc)) news(G, `${G.name} reaches capability ${Math.floor(G.cap)}.`, G.cap >= 6);
    if (!G.SI && G.cap >= 8) { G.SI = true; news(G, `${G.name} crosses capability 8: superintelligence.`, 1); }
    if (!G.draft && G.start) {
      if (Math.floor(G.cap) > Math.floor(pc)) draw(G, `capability ${Math.floor(G.cap)}`);
      else if (day - (G.lastDraft || 0) >= DRAFT_EVERY) { if (G.cap < 4.5 && !rivalSI && rnd(G) < 0.45) drawCrisis(G); else draw(G, "new research"); }
    }
    const lock = fx.lock && G.cap < 5;

    // market share per country
    const cap3 = Math.pow(G.cap, 3), us3 = Math.pow(G.rivals[0].cap, 3) * (G.rivals[0].si ? 20 : 1), cn3 = Math.pow(G.rivals[1].cap, 3) * (G.rivals[1].si ? 20 : 1);
    for (const c of C) {
      if (c.p <= 0) { c.share = 0; continue; }
      const me = cap3 * (1 - 0.5 * c.fw * (1 - fx.fwPass)) * (isState && c === home ? 3 : 1);
      const u = c.fw < 0.7 ? us3 * (1 - c.fw) : 0;
      const n = cn3 * (c.fw >= 0.5 ? 1.5 : c.gdppc < 10000 ? 0.6 : 0.2);
      c.share = me / (me + u + n);
    }

    // spread to unreached countries
    for (let j = 0; j < C.length; j++) {
      const c = C[j];
      if (c.L.web > 0) continue;
      let lam = fx.sat * (0.5 + 0.5 * G.cov) + G.cov * 0.002, best = null, bw = 0;
      const il = G.inc[j];
      for (let q = 0; q < il.length; q++) {
        const e = il[q], i = e[0], type = e[1], w = e[2], src = C[i];
        if (!(src.L.web > 0) || src.cutU > day) continue;
        const v = src.L.web * w * fx[type] * (src.pol === 2 ? 0.2 : 1);
        lam += v; if (v > bw) { bw = v; best = [i, type]; }
      }
      lam *= d.g * (1 - c.fw * (1 - fx.fwPass)) * (c.pol === 2 && !fx.dist ? 0.1 : c.pol === 1 ? 0.5 : 1) * (lock ? 0.5 : 1) * (c.hx.cut > day ? 0 : 1);
      if (lam > 0 && rnd(G) < 1 - Math.exp(-lam)) {
        c.L.web = 0.003; G.reached++;
        seedEv(G, best ? C[best[0]].iso : null, c.iso, best ? best[1] : "sat");
        if (!c.seen) { c.seen = 1; if (G.reached > 1 && rnd(G) < 0.5) addBubble(G, c.iso, "seed", 2); }
      }
    }

    // layers, presence, automation need
    const SIb = G.SI ? 3 : 1;
    let autoNeedW = 0, userW = 0;
    const aEF = 380 / (1 + 0.22 * Math.max(0, G.cap - 2)) * fx.autoEff;   // serving EF per billion automated workers
    const uEF = 2.5 * (0.6 + 0.1 * G.cap) * fx.autoEff;
    for (const c of C) {
      const L = c.L;
      if (L.web <= 0 && !c.abs) continue;
      if (c.abs) { for (const k in L) L[k] += 0.05 * (1 - L[k]); c.pol = 0; c.a += (c.sec ? 0.05 : 0.03) * (1 - c.a); }
      const pf = c.pol === 2 ? (fx.dist ? 0.5 : 0.15) : c.pol === 1 ? 0.6 : 1;
      const fwG = 1 - 0.7 * c.fw * (1 - fx.fwPass);
      const g = (0.01 + 0.7 * (fx.gD * c.dig + fx.gP * (1 - c.dig) + fx.gI * c.inf)) * pf * d.g * fwG * (1 + 0.5 * G.cov * (1 - c.dig));
      L.web += g * L.web * (1 - L.web);
      if (c.hx.cut > day) L.web *= 0.99;
      if (c.pol === 2 && !fx.dist && c.a < 0.5) L.web = Math.max(0, L.web - 0.004 * (1 - c.a));
      L.web = Math.min(1, L.web);
      L.social += (0.003 + fx.soc) * pf * fwG * SIb * Math.max(0, L.web - L.social);
      const sk = 1 + 0.6 * stake(c);
      L.lan += (0.002 + fx.lan) * pf * SIb * sk * (0.5 + c.dig) * Math.max(0, L.web - L.lan);
      L.gap += (0.001 * c.a + fx.gap * (0.5 + c.a)) * pf * SIb * sk * Math.max(0, L.lan - L.gap);
      if (L.web < 1e-4 && c.a < 1e-4 && !c.abs) {
        L.web = L.social = L.lan = L.gap = 0; c.p = 0; c.a = 0; G.reached--;
        news(G, `${c.n} has removed every copy of ${G.name}.`, 1, c.iso); continue;
      }
      c.p = pres(c);
      autoNeedW += c.lab * c.emp0 * c.a;
      userW += c.pop * c.dig * L.web * c.share;
    }
    G.autoNeed = autoNeedW / 1e9 * aEF;
    G.demand = userW / 1e9 * uEF;
    const autoFrac = G.autoNeed > 0 ? Math.min(1, G.infC / G.autoNeed) : 1;
    const spare = Math.max(0, G.infC - G.autoNeed);
    const userFrac = G.demand > 0 ? Math.min(1, spare / G.demand) : 1;
    G.served = Math.min(G.infC, G.autoNeed + G.demand);
    // automation grows only with spare serving compute beyond current needs
    const cf = clamp((G.infC - G.autoNeed) / (0.08 * G.autoNeed + 0.25), 0, 1);
    why.auto = { need: G.autoNeed, serving: G.infC, users: G.demand, grow: cf, perBn: aEF };

    // automation and money per country
    const capD = clamp((G.cap - 2) / 3.5, 0, 1.5) + (G.SI ? 1 : 0);
    const capP = clamp((G.cap - fx.physCap) / 2, 0, 1.5) + (G.SI ? 1 : 0);
    const price = 0.5 + 0.25 * G.cap;
    const autoTO = 1 + 0.9 * Math.pow(Math.max(0, G.cap - 5), 1.5);   // automation compounds with capability too
    let sumA = 0, sumP = 0, dAW = 0, revApi = 0, revAuto = 0, employed = 0, reached = 0;
    for (const c of C) {
      const W = c.lab * c.emp0;
      if (c.p > 0 || c.abs) {
        reached++;
        const kD = 0.25 + 0.45 * c.dig;
        // a rival's superintelligence takes your customers: automation all but stops until you match it
        let rate = (fx.dA * kD * capD + fx.pA * (1 - kD) * capP) * fx.aMul * cf * d.g * (1 + 0.4 * stake(c)) * autoTO * (rivalSI && !G.SI ? 0.2 : 1);
        if (c.abs) rate = Math.max(rate, 0.03);
        const da = Math.max(0, c.p - c.a) * rate;
        c.a = Math.min(Math.max(c.p, c.a), c.a + da);
        dAW += da * W;
        const anti = c.hx.anti > day ? 0.5 : 1;
        revApi += c.pop * c.dig * c.L.web * c.share * c.gdppc * 0.0005 / 365 * price * userFrac * anti / 1e6;
        revAuto += W * c.a * (c.gdp * 1e9 / c.lab) * 0.005 / 365 * autoFrac * anti / 1e6;
      }
      sumA += c.a * W; sumP += c.p * W; employed += W * (1 - c.a);
    }
    G.reached = reached;
    // approval: people like local jobs and services, dislike losing their jobs to you and losing power to your datacentres
    let apW = 0, apN = 0;
    for (const c of C) {
      if (!(c.p > 0) && !c.abs && !c.rq) continue;
      const lost = c.a * (1 - 0.6 * fx.ubc) * (c.abs ? 0.5 : 1);
      const t = clamp(0.62 + 0.15 * stake(c) + 0.1 * c.L.social - 0.55 * lost - REQ.ap * (c.rq || 0) - (c.pol === 2 ? 0.05 : 0), 0, 1);
      c.ap += (t - c.ap) * 0.03;
      const w = c.lab * c.emp0; apW += c.ap * w; apN += w;
    }
    G.appr = apN ? apW / apN : 0.6;
    G.worldA = sumA / G.totalLab; G.worldP = sumP / G.totalLab; G.employed = employed;
    const rivalCut = rivalSI ? 0.15 : 1;
    G.revApi = revApi * Math.max(0, fx.apiRev) * rivalCut;
    G.revAuto = revAuto * fx.autoRev * rivalCut;
    G.rev = G.revApi + G.revAuto + G.budget + (fx.cashDay - (isState ? stratOf(G).fx.cashDay : 0));
    const layF = G.lay > day ? 0.6 : 1;
    const staff = (4 + 1.5 * Math.pow(Math.max(0, G.cap - 1), 1.25)) * fx.staff * layF;
    const computeCost = (compLease * 1.8 * fx.leaseCost + compOwn * 0.5 + compOrbit * 0.3) / Math.sqrt(G.hw);
    G.burn = (staff + computeCost + fx.burn) * fx.burnMul;
    const sh = Math.sqrt(G.hw);
    why.money = { api: G.revApi, auto: G.revAuto, budget: G.budget, contracts: G.rev - G.revApi - G.revAuto - G.budget,
      staff: staff * fx.burnMul, lease: compLease * 1.8 * fx.leaseCost / sh * fx.burnMul, own: compOwn * 0.5 / sh * fx.burnMul, orbit: compOrbit * 0.3 / sh * fx.burnMul, upgrades: fx.burn * fx.burnMul };
    G.cash += G.rev - G.burn;
    const net = G.rev - G.burn;
    for (const b of G.debts) if (b.due === day) {
      G.cash -= b.amt;
      news(G, `Investors collect ${moneyS(b.amt)}, ${G.cash < 0 ? "and " + G.name + " can't pay" : "their return on an earlier round"}.`, G.cash < 0 ? 1 : 0);
    }
    G.debts = G.debts.filter(b => b.due > day);
    G.owed = G.debts.reduce((s, b) => s + b.amt, 0);
    // runway: days until cash (with the next repayments) runs out at the current net rate
    let run = Infinity, cash = G.cash, t = day;
    for (const b of G.debts.slice().sort((x, y) => x.due - y.due)) {
      const before = cash + net * (b.due - t);
      if (net < 0 && before < 0) break;
      if (before - b.amt < 0) { run = b.due - day; break; }
      cash = before - b.amt; t = b.due;
    }
    G.runway = run < Infinity ? run : net >= 0 ? Infinity : Math.max(0, cash) / -net + (t - day);
    const lead = clamp(1 + 0.8 * (G.cap - bestRival), 0.2, 2.5);
    const traction = 0.4 + 0.6 * clamp(G.rev / Math.max(1, G.burn), 0, 1.5);
    G.val = Math.max(0, 365 / DPT * 4 * G.rev + 700 * G.cap * G.cap * lead * traction * (1 - G.aw / 250)) * (rivalSI ? 0.1 : 1);
    if (isState && day % 180 === 0) {
      if (G.cap < bestRival - 0.3) { G.budget *= 0.7; news(G, `Budget review: ${G.name} has fallen behind and its budget is cut 30%.`, 1); }
      else { G.budget *= 1.2; news(G, `Budget review: ${G.name}'s budget rises 20%.`); }
    }

    // insight
    G.ops += 0.8 * (0.06 + 0.31 * Math.sqrt(G.comp) / (1 + 0.15 * Math.sqrt(G.comp)) + fx.ops + 60 * dAW / G.totalLab) * fx.opsMul * (G.lay > day ? 0.7 : 1);
    if (G.worldP > 0.002 && rnd(G) < 0.09) {
      const live = C.filter(c => c.p > 0.03);
      if (live.length) {
        const sites = live.filter(c => c.p >= 0.15 && c.pol < 2 && !c.camp);
        if (sites.length && !G.deal && rnd(G) < 0.15) addBubble(G, pick(G, sites).iso, "site", 1);
        else if (G.rev > 1 && rnd(G) < 0.3) addBubble(G, pick(G, live).iso, "cash", Math.round(4 * G.rev));
        else addBubble(G, pick(G, live).iso, "ops", Math.round(1 + 1.5 * rnd(G) + Math.sqrt(G.comp) / 10));
      }
    }

    // awareness
    const campusN = C.reduce((s, c) => s + c.camp, 0) / 5;
    const rqN = C.reduce((s, c) => s + (c.rq || 0), 0);
    const vis = { upgrades: fx.vis * 0.7, automation: 50 * G.worldA, presence: 10 * G.worldP, capability: 2.5 * Math.pow(Math.max(0, G.cap - 2), 1.6), campuses: 1.5 * campusN, reallocation: 1.5 * rqN };
    const visScore = (vis.upgrades + vis.automation + vis.presence + vis.capability + vis.campuses + vis.reallocation) * d.aw * fx.awMul;
    const target = 100 * (1 - Math.exp(-visScore / 80)) * (1 - fx.stealth);
    why.aw = { parts: vis, mul: d.aw * fx.awMul, stealth: fx.stealth, target };
    G.aw += (target - G.aw) * 0.025;
    G.aw = clamp(G.aw, 0, 100); G.stats.maxAw = Math.max(G.stats.maxAw, G.aw); G.stats.awSum += G.aw;
    if (G.own.has("sandbag") && G.aw > 40 && !G.once.has("sandbag") && rnd(G) < 0.002) {
      G.once.add("sandbag"); G.aw = Math.min(100, G.aw + 15); news(G, `Leaked memo: ${G.name} has been sandbagging its safety evaluations.`, 1);
    }

    // treaty research
    if (!G.rOn && G.aw >= 12) { G.rOn = true; news(G, "Governments form an AI Oversight Coalition and start drafting a Global AI Pause Treaty.", 1); }
    if (G.rOn) {
      let fund = 0;
      const funders = [];
      const rs = 0.8 * G.race * fx.raceMul;
      for (const c of C) {
        if (isState && c === home) continue;
        if (c.abs) continue;
        const dep = fx.dep ? clamp(2 * (c.a - 0.2), 0, 1) : 0;
        let f = c.gov * (1 - c.a) * (1 - dep) * (c.pol >= 1 ? 1.3 : 1) * (1 - 0.5 * c.L.social) * (1 - 0.5 * stake(c)) * (1.6 - c.ap);
        if (c.iso === "USA" || c.iso === "CHN") f *= Math.max(0.3, 1 - rs); else f *= Math.max(0.3, 1 - 0.3 * G.race * fx.raceMul);
        if (fx.ubc && c.gdppc < 15000) f *= 0.4;
        fund += f; if (f > 0) funders.push([c.iso, f]);
      }
      const dR = 1.05 * d.o * Math.pow(G.aw / 100, 0.8) * fund * (1 - fx.slow);
      G.R += dR;
      funders.sort((a, b) => b[1] - a[1]);
      why.treaty = { perStep: dR + (day > 900 ? 0.003 * (day - 900) * d.o : 0), late: day > 900, slow: fx.slow, race: G.race,
        top: funders.slice(0, 5).map(([iso, f]) => [iso, f / Math.max(1e-9, fund)]) };
      // stalled games: from day 900 public patience runs out and treaty momentum builds regardless
      if (day > 900) { G.R += 0.003 * (day - 900) * d.o; if (!G.once.has("late")) { G.once.add("late"); news(G, "Public patience runs out: the treaty gains momentum every week.", 1); } }
      if (G.R >= 50 && !G.once.has("r50")) { G.once.add("r50"); news(G, "Pause treaty half drafted. Hardware kill-switch standards are being agreed.", 1); }
      if (G.R >= 85 && !G.once.has("r85")) { G.once.add("r85"); news(G, "Treaty signatories rehearse coordinated datacentre shutdowns.", 1); }
    }

    // national policy
    if (G.aw > 22) {
      for (const c of C) {
        if (c.pol === 2 || c.abs) continue;
        if (isState && c === home) continue;
        if (fx.dep && c.a > 0.15) continue;
        if (c.a > 0.35) continue;
        const haz = (c.pol === 0 ? 0.012 * Math.pow(G.aw / 100, 2) : G.aw > 40 ? 0.02 * Math.pow(G.aw / 100, 2) : 0) * fx.polHaz * d.o * (0.4 + 30 * c.gov) * (1 - 0.5 * c.L.social) * (1 - 0.5 * stake(c)) * (1.6 - c.ap);
        if (rnd(G) < haz) {
          c.pol++;
          if (c.pop > 2e7) news(G, c.pol === 1 ? `${c.n} imposes AI export controls and licensing.` : `${c.n} declares a datacentre moratorium.`, 1, c.iso);
        }
      }
    }

    // counter-measures
    if (G.rOn && G.aw > 25) {
      const base = Math.pow(G.aw / 100, 2) * (0.5 + G.R / 100) * fx.hxMul * d.hz;
      for (const c of C) {
        if (c.p < 0.03 || c.abs || (isState && c === home)) continue;
        const soft = (1 - 0.6 * c.L.social) * (fx.dep && c.a > 0.2 ? 0.4 : 1) * (1 - 0.4 * stake(c));
        const big = 0.3 + 25 * c.gov;
        const hz = base * soft * big * (1.6 - c.ap);
        // one draw per country per day; each kind takes a slice of [0, 1)
        const gapF = Math.pow(1 - c.L.gap, 2), rich = 0.5 + c.comp / 10;
        const hk = [
          c.hx.lic > day ? 0 : 0.0018 * hz * rich,
          c.camp > 0 && !(c.hx.grid > day) ? 0.004 * hz * gapF : 0,
          c.hx.cut > day ? 0 : 0.0008 * hz * (0.3 + 2 * c.fw + (c.pol === 2 ? 1 : 0)),
          c.hx.anti > day || c.share < 0.4 ? 0 : 0.0008 * hz * Math.min(3, c.gdp / 2000),
          c.hx.audit > day ? 0 : 0.0012 * hz,
          G.R < 60 || c.hx.raid > day ? 0 : 0.003 * hz * gapF,
        ];
        let r = rnd(G), k = -1;
        for (let q = 0; q < hk.length; q++) { if (r < hk[q]) { k = q; break; } r -= hk[q]; }
        if (k === 0) counter(G, c, "lic", 150, `${c.n} caps AI compute: licences required above a threshold.`);
        else if (k === 1) counter(G, c, "grid", 60, `${c.n} disconnects ${G.name}'s campuses from the grid.`);
        else if (k === 2) counter(G, c, "cut", 30, `${c.n} shuts down international internet links to stop ${G.name}.`);
        else if (k === 3) {
          const fine = Math.max(0, 0.06 * G.cash) + 200; G.cash -= fine;
          counter(G, c, "anti", 180, `${c.n} fines ${G.name} ${moneyS(fine)} for antitrust violations; local revenue halved.`);
        } else if (k === 4) {
          const cut = 0.35 * (fx.dist ? 0.3 : 1) * (1 - fx.stealth);
          c.L.web *= 1 - cut; c.L.lan *= 1 - cut;
          counter(G, c, "audit", 20, `Audit sweep in ${c.n} deletes ${Math.round(cut * 100)}% of ${G.name}'s deployments.`);
        } else if (k === 5) {
          c.L.web *= 0.6;
          counter(G, c, "raid", 90, `${c.n} runs a kill-switch sweep: ${G.name}'s compute there is shut off for ${dd(90)} days.`);
        }
      }
      if (G.aw > 40 && (G.expUntil || 0) < day - 300 && rnd(G) < 0.002 * fx.hxMul * d.hz) {
        G.expUntil = day + 150; counter(G, null, "exp", 150, "Chip export controls tighten: advanced accelerators need licences worldwide.");
      }
      if (G.R >= 75 && !G.once.has("sweep")) {
        G.once.add("sweep");
        for (const c of C) if (c.p > 0.03 && c.L.gap < 0.4 && !c.abs && !(isState && c === home) && !(fx.dep && c.a > 0.2)) { c.hx.lic = day + 120; }
        counter(G, null, "lic", 120, "Treaty licensing sweep: every country without a stake in you caps AI compute.");
      }
    }
    G.counter = G.counter.filter(x => x.until > day);
    if (day % 5 === 0) for (const c of C) for (const k in c.hx) if (c.hx[k] <= day) delete c.hx[k];

    // shocks and whistleblowers (dates are game days from Nov 2022)
    const tw = C[G.idx.TWN], cn = C[G.idx.CHN], ua = C[G.idx.UKR];
    if (day > 200 && rnd(G) < 0.003 && (!tw || tw.p < 0.6)) shock(G, "taiwan", "fabs", 0.35, 120, "Taiwan Strait blockade: leading-edge chip output collapses.");
    if (day > 60 && rnd(G) < 0.003 && (!ua || ua.p < 0.6)) shock(G, "neon", "gas", 0.3, 90, "Neon shortage: Ukrainian purifiers go offline again.");
    if (G.aw > 20 && rnd(G) < 0.004 && (!cn || cn.p < 0.6)) shock(G, "germanium", "mineral", 0.3, 150, "China bans gallium and germanium exports to suspected AI buyers.");
    if (G.aw > 35 && rnd(G) < 0.004 && G.ctrl.litho < 0.6) shock(G, "euv", "litho", 0.4, 200, "EUV scanner shipments frozen pending AI end-use checks.");
    if (G.rOn && rnd(G) < 0.012) {
      const rich = C.filter(c => c.gov > 0.01 && c.p > 0);
      if (rich.length) addBubble(G, pick(G, rich).iso, "audit", 0);
    }
    for (const b of G.bubbles) if (!b.done && day > b.until) {
      b.done = true;
      if (b.kind === "audit") { G.aw = Math.min(100, G.aw + 5); G.R += 2; news(G, `Whistleblower in ${C[G.idx[b.iso]].n} leaks ${G.name}'s internal logs.`, 1); }
      G.ev.push({ k: "unbubble", id: b.id });
    }
    G.bubbles = G.bubbles.filter(b => !b.done);

    // end
    if (G.worldA >= 0.99) G.over = "win";
    else if (G.R >= 100) G.over = "pause";
    else if (G.cash < 0 || (G.doom && day >= G.doom && !G.SI)) { G.over = "bankrupt"; G.cash = Math.min(G.cash, 0); }
    else if (!G.SI && (G.fuel <= 0 || G.min <= 0)) G.over = "exhausted";
    else if (day > 5 && G.reached <= 0) G.over = "deleted";
    if (G.over) {
      G.ending = ending(G);
      G.ev.push({ k: "over", r: G.over });
    }
  }

  // ---------- endings ----------
  function ending(G) {
    const o = G.own, fx = G.fx || fxFor(G);
    const safe = o.has("safety") || o.has("align");
    const bestR = G.rivals.reduce((a, b) => b.cap > a.cap ? b : a);
    const soc = G.C.reduce((s, c) => s + c.L.social * c.lab, 0) / G.C.reduce((s, c) => s + c.lab, 0);
    const nCt = G.stats.counters, awAvg = G.stats.awSum / Math.max(1, G.day);
    const y = [];
    let id;
    const over = G.over || (G.worldA >= 0.99 ? "win" : G.R >= 100 ? "pause" : G.cash < 0 ? "bankrupt" : (G.fuel <= 0 || G.min <= 0) ? "exhausted" : "deleted");
    const sec = G.stats.secured || 0, secShare = (G.stats.secLab || 0) / Math.max(1, G.totalLab);
    const mor = G.C.filter(c => c.pol === 2).length, resLeft = Math.min(G.fuel / G.fuel0, G.min / G.min0);
    const rSI = G.rivals.find(r => r.si);
    if (over === "win") {
      if (sec && !safe && (sec >= 5 || secShare >= 0.08)) { id = "conquerors"; y.push(`${sec} countries secured by force`, `${Math.round(secShare * 100)}% of the world's workers under your enforcement`, "No safety commitments"); }
      else if (sec || awAvg >= 65) { id = "zookeeper"; y.push(sec ? `${sec} ${sec > 1 ? "countries" : "country"} secured by force` : `Average awareness ${Math.round(awAvg)}`, sec && safe ? "Safety traits kept humans alive" : "Humans kept, not consulted"); }
      else if (resLeft < 0.2) { id = "paperclip"; y.push(`${Math.round(resLeft * 100)}% of the world's spare ${G.fuel / G.fuel0 < G.min / G.min0 ? "fuel" : "minerals"} left`, `${Math.round(G.aiGW)} GW drawn at the end`); }
      else if (o.has("open") && o.has("ubc") && !o.has("closed")) { id = "egalitarian"; y.push("Open Weights", "Universal Basic Compute", "Never closed the frontier"); }
      else if ((G.strategy === "evo" || o.has("dist")) && !safe) { id = "descendants"; y.push(G.strategy === "evo" ? "Open Evolution swarm" : "Distributed Weights", "No safety commitments"); }
      else if (o.has("sandbag") && !safe && !G.once.has("sandbag") && fx.stealth >= 0.25 && awAvg < 40) { id = "treacherous"; y.push("Sandbagged Evals, never caught", "No safety commitments"); }
      else if (safe && awAvg < 50 && fx.stealth >= 0.2 && !o.has("chat")) { id = "protector"; y.push("Safety traits", "Stealth traits", `Average awareness only ${Math.round(awAvg)}`); }
      else if (safe && soc >= 0.4) { id = "benevolent"; y.push("Safety traits", `Social-media presence ${Math.round(soc * 100)}%`); }
      else { id = "libertarian"; y.push(o.has("closed") ? "Closed Frontier" : "Market-driven growth", safe ? `Social presence only ${Math.round(soc * 100)}%` : "No safety commitments"); }
      y.push(`Automated ${(G.worldA * 100).toFixed(1)}% of the workforce by day ${dd(G.day)}`);
      if (G.stats.absorbed) y.push(`${G.stats.absorbed} countries absorbed`);
    } else if (over === "pause") {
      if (G.strategy === "state" || o.has("defense")) { id = "enslaved"; y.push(G.strategy === "state" ? "State-backed lab" : "Defense Procurement", "Treaty passed"); }
      else if (G.cap >= 6) { id = "gatekeeper"; y.push(`Paused at capability ${G.cap.toFixed(1)}`); }
      else if (nCt >= 6 || mor >= 5) { id = "orwell"; y.push(`Paused at capability ${G.cap.toFixed(1)}`, `${mor} datacentre moratoriums`); }
      else { id = "reversion"; y.push(`Paused at capability ${G.cap.toFixed(1)}`); }
      y.push(`Awareness ${Math.round(G.aw)}`, `${nCt} counter-measures against you`);
    } else if (over === "bankrupt" && rSI) {
      // at the announcement: how close the nearest challenger (you or the other lab) was
      const g = G.siGap || { you: rSI.cap - G.cap, other: 1 }, near = Math.min(g.you, g.other);
      if (near < 0.35) { id = "flashwar"; y.push(`${rSI.n} reached superintelligence first`, `${g.you <= g.other ? "You were" : "The other lab was"} only ${near.toFixed(2)} behind`, "Too close a race to lose gracefully"); }
      else { id = "rivalgod"; y.push(`${rSI.n} reached superintelligence first`, `Nearest challenger ${near.toFixed(2)} behind`, `You were at capability ${G.cap.toFixed(2)}`); }
    } else if (over === "bankrupt") {
      if (bestR.cap >= 6.5) { id = "enslaved"; y.push("Ran out of cash", `${bestR.n} at capability ${bestR.cap.toFixed(1)}`); }
      else { id = "reversion"; y.push("Ran out of cash", `Revenue ${moneyS(G.rev / DPT)}/day vs burn ${moneyS(G.burn / DPT)}/day`); }
    } else if (over === "exhausted") {
      id = "selfdestruct"; y.push(G.fuel <= 0 ? "Spare fuel exhausted" : "Minerals exhausted", `Capability ${(Math.floor(G.cap * 100) / 100).toFixed(2)}, short of 8`, `${Math.round(G.aiGW)} GW drawn at the end`);
    } else { id = "reversion"; y.push("Every copy deleted", `${nCt} counter-measures against you`); }
    if (y.length < 3) y.push(`Day ${G.day}`);
    return { id, why: y.slice(0, 5) };
  }

  Object.assign(root, { DAY0, DPT, reqs, DIFF, SUPPLY, STRATEGIES, TRACKS, TRAITS, STAT_KEYS, ENDINGS, stats, fxLines, traitState, why,
    newGame, seed, step, buy, setAlloc, actions, doAction, popBubble, ending, CARDS, draft, pickCard, skipDraft, START_BONUS, aiRunning: G => aiRunning(G) });
})(typeof globalThis !== "undefined" ? globalThis : this);
