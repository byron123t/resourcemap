/* Singularity: procedural music and sound effects (site/game/audio.js). WebAudio only, no sample files, so no licences.
   Exposes window.GameAudio = { sfx(name), intensity(x 0..1, dark bool), music(on), sound(on), state() }.
   The AudioContext starts on the first click or key press (browsers block audio before a gesture).

   Music: a slow pad on a four-chord loop, a sparse arpeggio and, as intensity rises, a pulse bass and hats. Intensity follows
   the game (automation, treaty, capability) and is smoothed, so the score grows from calm to frantic over a game. "dark"
   switches the loop to minor chords (treaty close, rival ahead). Settings persist in localStorage. */
(() => {
"use strict";
const KEY = "singularity.audio";
let opt = { music: true, sound: true, vol: 0.6 };
try { Object.assign(opt, JSON.parse(localStorage.getItem(KEY) || "{}")); } catch (e) { /* storage blocked */ }
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(opt)); } catch (e) { /* storage blocked */ } };

let ac = null, master = null, musicBus = null, sfxBus = null, verb = null;
let inten = 0, target = 0, dark = false, timer = null, beat = 0, nextT = 0;
const midi = n => 440 * Math.pow(2, (n - 69) / 12);

function init() {
  if (ac) { if (ac.state === "suspended") ac.resume(); return; }
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return;
  ac = new AC();
  master = ac.createGain(); master.gain.value = opt.vol; master.connect(ac.destination);
  // a cheap reverb: feedback delay through a lowpass
  verb = ac.createGain(); verb.gain.value = 0.35;
  const dl = ac.createDelay(1), fb = ac.createGain(), lp = ac.createBiquadFilter();
  dl.delayTime.value = 0.23; fb.gain.value = 0.45; lp.type = "lowpass"; lp.frequency.value = 2200;
  verb.connect(dl); dl.connect(lp); lp.connect(fb); fb.connect(dl); lp.connect(master);
  musicBus = ac.createGain(); musicBus.gain.value = opt.music ? 0.5 : 0; musicBus.connect(master); musicBus.connect(verb);
  sfxBus = ac.createGain(); sfxBus.gain.value = opt.sound ? 0.8 : 0; sfxBus.connect(master); sfxBus.connect(verb);
  nextT = ac.currentTime + 0.1;
  timer = setInterval(schedule, 100);
}
["pointerdown", "keydown"].forEach(e => addEventListener(e, init, { passive: true }));

// ---------- voices
function env(g, t, a, peak, d) { g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(peak, t + a); g.gain.exponentialRampToValueAtTime(0.0001, t + a + d); }
function tone(bus, type, f, t, a, peak, d, f2) {
  const o = ac.createOscillator(), g = ac.createGain();
  o.type = type; o.frequency.setValueAtTime(f, t); if (f2) o.frequency.exponentialRampToValueAtTime(f2, t + a + d);
  env(g, t, a, peak, d); o.connect(g); g.connect(bus); o.start(t); o.stop(t + a + d + 0.05);
}
function pad(f, t, dur, peak) {
  const lp = ac.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 500 + 1600 * inten; lp.Q.value = 0.7;
  const g = ac.createGain(); g.gain.setValueAtTime(0.0001, t); g.gain.linearRampToValueAtTime(peak, t + dur * 0.35); g.gain.linearRampToValueAtTime(0.0001, t + dur);
  lp.connect(g); g.connect(musicBus);
  for (const det of [-7, 0, 6]) { const o = ac.createOscillator(); o.type = "sawtooth"; o.frequency.value = f; o.detune.value = det; o.connect(lp); o.start(t); o.stop(t + dur + 0.05); }
}
function noise(bus, t, d, peak, hp) {
  const n = ac.sampleRate * d, b = ac.createBuffer(1, n, ac.sampleRate), x = b.getChannelData(0);
  for (let i = 0; i < n; i++) x[i] = Math.random() * 2 - 1;
  const s = ac.createBufferSource(), f = ac.createBiquadFilter(), g = ac.createGain();
  s.buffer = b; f.type = "highpass"; f.frequency.value = hp; env(g, t, 0.002, peak, d);
  s.connect(f); f.connect(g); g.connect(bus); s.start(t);
}

// ---------- music: 16 steps per bar, one chord per bar
const MAJ = [[57, 60, 64, 69], [53, 57, 60, 65], [55, 59, 62, 67], [52, 55, 59, 64]];   // Am F G Em
const MIN = [[57, 60, 64, 69], [53, 56, 60, 65], [50, 53, 57, 62], [52, 55, 58, 64]];   // Am Fm Dm E°-ish
function schedule() {
  if (!ac) return;
  inten += (target - inten) * 0.02;
  const bpm = 72 + 60 * inten, sp = 60 / bpm / 4;
  while (nextT < ac.currentTime + 0.25) {
    if (opt.music) step16(nextT, sp);
    nextT += sp; beat++;
  }
}
function step16(t, sp) {
  const bar = Math.floor(beat / 16) % 4, s = beat % 16, ch = (dark ? MIN : MAJ)[bar];
  if (s === 0) for (const n of ch.slice(0, 3)) pad(midi(n - 12), t, sp * 16, 0.035 + 0.02 * inten);
  // arpeggio: denser as intensity rises
  const every = inten < 0.25 ? 4 : inten < 0.6 ? 2 : 1;
  if (s % every === 0 && (inten > 0.08 || s % 8 === 0)) {
    const n = ch[(s / every + bar) % ch.length] + (s % 8 === 4 ? 12 : 0);
    tone(musicBus, "triangle", midi(n + 12), t, 0.005, 0.05 + 0.03 * inten, sp * 3);
  }
  if (inten > 0.35 && s % 4 === 0) tone(musicBus, "sine", midi(ch[0] - 24), t, 0.01, 0.12 * inten, sp * 3, midi(ch[0] - 25));
  if (inten > 0.6 && s % 2 === 1) noise(musicBus, t, 0.04, 0.03 * inten, 7000);
  if (inten > 0.8 && s % 8 === 4) noise(musicBus, t, 0.12, 0.05, 1500);
}

// ---------- sound effects
const last = {};
const SFX = {
  pop: t => { tone(sfxBus, "sine", 880, t, 0.003, 0.25, 0.09, 1320); },
  cash: t => { tone(sfxBus, "square", 1320, t, 0.002, 0.08, 0.06); tone(sfxBus, "square", 1760, t + 0.06, 0.002, 0.08, 0.12); },
  seed: t => { tone(sfxBus, "sine", 1500 + Math.random() * 600, t, 0.002, 0.05, 0.05); },
  buy: t => { [0, 4, 7, 12].forEach((n, i) => tone(sfxBus, "triangle", midi(72 + n), t + i * 0.06, 0.004, 0.16, 0.25)); },
  card: t => { noise(sfxBus, t, 0.08, 0.12, 2500); tone(sfxBus, "triangle", midi(79), t + 0.05, 0.004, 0.14, 0.3); tone(sfxBus, "triangle", midi(84), t + 0.12, 0.004, 0.12, 0.4); },
  draw: t => { for (let i = 0; i < 3; i++) noise(sfxBus, t + i * 0.07, 0.05, 0.08, 3000); },
  build: t => { tone(sfxBus, "sine", midi(76), t, 0.004, 0.18, 0.8); tone(sfxBus, "sine", midi(83), t + 0.004, 0.004, 0.08, 0.9); },
  level: t => { [0, 4, 7, 11, 14].forEach((n, i) => tone(sfxBus, "sawtooth", midi(60 + n), t + i * 0.05, 0.03, 0.06, 1.4)); tone(sfxBus, "sine", midi(48), t, 0.02, 0.2, 1.6); },
  bad: t => { tone(sfxBus, "sine", 110, t, 0.005, 0.3, 0.5, 70); tone(sfxBus, "sawtooth", 233, t, 0.01, 0.04, 0.4, 220); },
  alarm: t => { for (let i = 0; i < 3; i++) tone(sfxBus, "square", 660, t + i * 0.18, 0.005, 0.06, 0.1); },
  win: t => { [0, 4, 7, 12, 16, 19, 24].forEach((n, i) => tone(sfxBus, "triangle", midi(60 + n), t + i * 0.09, 0.005, 0.18, 1.2)); },
  lose: t => { [12, 8, 5, 0, -5].forEach((n, i) => tone(sfxBus, "sawtooth", midi(57 + n), t + i * 0.22, 0.02, 0.07, 0.9)); tone(sfxBus, "sine", 55, t, 0.05, 0.3, 2.5); },
  click: t => { tone(sfxBus, "sine", 1200, t, 0.001, 0.05, 0.03); },
};
const GAP = { seed: 0.25, cash: 0.1, pop: 0.05, bad: 0.6, alarm: 2, click: 0.03 };
function sfx(name) {
  if (!ac || !opt.sound || !SFX[name]) return;
  const now = ac.currentTime;
  if (last[name] && now - last[name] < (GAP[name] || 0.08)) return;
  last[name] = now; SFX[name](now + 0.01);
}

window.GameAudio = {
  sfx,
  intensity(x, d) { target = Math.max(0, Math.min(1, x)); dark = !!d; },
  music(on) { opt.music = on ?? !opt.music; save(); if (musicBus) musicBus.gain.setTargetAtTime(opt.music ? 0.5 : 0, ac.currentTime, 0.3); return opt.music; },
  sound(on) { opt.sound = on ?? !opt.sound; save(); if (sfxBus) sfxBus.gain.setTargetAtTime(opt.sound ? 0.8 : 0, ac.currentTime, 0.05); return opt.sound; },
  state: () => ({ ...opt, running: !!ac }),
};
})();
