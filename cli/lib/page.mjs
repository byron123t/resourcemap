// Reads site/index.html once and pulls constants out of its inline <script> blocks by name (main script first).
// The page is the single source of truth for layer names, views, shading options and sources,
// so the CLI evaluates the same literals instead of keeping its own copies.
//
// How: find `const NAME = ` in the main script, take the initializer (a balanced {...} / [...]
// literal, or anything up to the `;` that ends the statement), and evaluate it in a node:vm
// context. If it references another top-level const or function (e.g. `lngs(...)`), that one is
// extracted the same way and added to the context first. Browser globals get inert stubs.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

export const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
export const INDEX = process.env.ATLAS_INDEX || path.join(REPO, "site", "index.html");

// the constants the CLI depends on; tests check that each one still extracts
export const REQUIRED = ["FILES", "GROUPS", "VIEWS", "VIEW_KEYS", "VIEW_DEFAULT", "CHORO", "SOURCES", "Q", "EXTRA",
  "SIM_KIND", "SIM_CHOKE", "SIM_GROUPS", "SIM_NEWS", "LIVE"];

let html = null, ctx = null;
const cache = new Map();

export function pageHtml() {
  if (html == null) {
    try { html = readFileSync(INDEX, "utf8"); }
    catch (e) { throw new Error(`cannot read ${INDEX}: ${e.message}`); }
  }
  return html;
}

// the main app script is the longest <script> with no attributes; the other inline blocks
// (#simCore, #simWater, #simTransport, the worker texts) are searched after it
let blocks = null;
export function scripts() {
  if (blocks) return blocks;
  const h = pageHtml(), re = /<script(\s[^>]*)?>([\s\S]*?)<\/script>/g, all = [];
  let m;
  while ((m = re.exec(h))) if (!/\ssrc=/.test(m[1] || "") && m[2].trim()) all.push({ attrs: (m[1] || "").trim(), src: m[2] });
  const plain = all.filter(b => !b.attrs).sort((a, b) => b.src.length - a.src.length);
  if (!plain.length) throw new Error(`no inline <script> found in ${INDEX}`);
  const rest = all.filter(b => b !== plain[0]).sort((a, b) => (/text\/plain/.test(a.attrs) ? 1 : 0) - (/text\/plain/.test(b.attrs) ? 1 : 0));
  return (blocks = [plain[0].src, ...rest.map(b => b.src)]);
}
export function mainScript() { return scripts()[0]; }

// ---------- a small JS scanner: strings, template literals, comments, regex literals ----------
const OPEN = "([{", CLOSE = ")]}";
function skipString(s, i) {
  const q = s[i++];
  while (i < s.length) {
    const c = s[i];
    if (c === "\\") { i += 2; continue; }
    if (c === q) return i + 1;
    if (q === "`" && c === "$" && s[i + 1] === "{") { i = scanBalanced(s, i + 1); continue; }
    if (q !== "`" && c === "\n") throw new Error("unterminated string literal");
    i++;
  }
  throw new Error("unterminated string literal");
}
function regexAllowed(s, i) {
  let j = i - 1;
  while (j >= 0 && /\s/.test(s[j])) j--;
  if (j < 0) return true;
  if ("(,=:[!&|?{};+-*%<>~^".includes(s[j])) return true;
  const w = s.slice(Math.max(0, j - 7), j + 1).match(/(return|typeof|case|in|of)$/);
  return !!w;
}
function skipRegex(s, i) {
  i++;
  let cls = false;
  while (i < s.length) {
    const c = s[i];
    if (c === "\\") { i += 2; continue; }
    if (c === "\n") throw new Error("unterminated regex literal");
    if (cls) { if (c === "]") cls = false; }
    else if (c === "[") cls = true;
    else if (c === "/") { i++; while (/[a-z]/i.test(s[i] || "")) i++; return i; }
    i++;
  }
  throw new Error("unterminated regex literal");
}
// skips a string/comment/regex starting at i; returns the new index, or -1 if s[i] starts none
function skipToken(s, i) {
  const c = s[i];
  if (c === '"' || c === "'" || c === "`") return skipString(s, i);
  if (c === "/" && s[i + 1] === "/") { const e = s.indexOf("\n", i); return e < 0 ? s.length : e; }
  if (c === "/" && s[i + 1] === "*") { const e = s.indexOf("*/", i + 2); if (e < 0) throw new Error("unterminated comment"); return e + 2; }
  if (c === "/" && regexAllowed(s, i)) return skipRegex(s, i);
  return -1;
}
// s[i] is an opening bracket; returns the index just past its partner
function scanBalanced(s, i) {
  let depth = 0;
  while (i < s.length) {
    const k = skipToken(s, i);
    if (k >= 0) { i = k; continue; }
    const c = s[i];
    if (OPEN.includes(c)) depth++;
    else if (CLOSE.includes(c) && --depth === 0) return i + 1;
    i++;
  }
  throw new Error("unbalanced brackets");
}
// from i, up to the `;` that ends the statement (or the bracket that closes the enclosing block)
function scanStatement(s, i) {
  let depth = 0;
  while (i < s.length) {
    const k = skipToken(s, i);
    if (k >= 0) { i = k; continue; }
    const c = s[i];
    if (OPEN.includes(c)) depth++;
    else if (CLOSE.includes(c)) { if (depth === 0) return i; depth--; }
    else if (c === ";" && depth === 0) return i;
    i++;
  }
  return i;
}

// source text of the initializer of `const|let|var NAME = …`, or of `function NAME(…) {…}`
export function constSource(name, { quiet = false } = {}) {
  for (const s of scripts()) { const r = constIn(s, name); if (r) return r; }
  if (quiet) return null;
  throw new Error(`page constant ${name} not found in ${INDEX} (has index.html changed?)`);
}
function constIn(s, name) {
  const decl = new RegExp(`(?:^|[\\s;{(])(?:const|let|var)\\s+${name}\\s*=\\s*`, "m").exec(s);
  if (decl) {
    const start = decl.index + decl[0].length;
    // a literal ends at its closing bracket unless the statement continues (`[...].map(…)`, `(a) => …`)
    let end = scanStatement(s, start);
    if ("{[".includes(s[start])) { const lit = scanBalanced(s, start); if (/^\s*$/.test(s.slice(lit, end))) end = lit; }
    return { kind: "expr", src: s.slice(start, end).trim() };
  }
  const fn = new RegExp(`(?:^|[\\s;{])function\\s+${name}\\s*\\(`, "m").exec(s);
  if (fn) {
    const p = fn.index + fn[0].length - 1;
    const afterParams = scanBalanced(s, p);
    const body = s.indexOf("{", afterParams);
    const end = scanBalanced(s, body);
    return { kind: "fn", src: s.slice(s.indexOf("function", fn.index), end) };
  }
  return null;
}

function context() {
  if (ctx) return ctx;
  const style = { getPropertyValue: () => "" };
  const el = new Proxy(function () {}, { get: (t, k) => k === Symbol.toPrimitive ? () => "" : el, apply: () => el });
  ctx = vm.createContext({
    window: {}, document: { documentElement: {}, getElementById: () => el, createElement: () => el, querySelector: () => el, querySelectorAll: () => [] },
    getComputedStyle: () => style, location: { hash: "", href: "", search: "" }, navigator: { userAgent: "" },
    innerWidth: 1280, innerHeight: 800, devicePixelRatio: 1, localStorage: { getItem: () => null, setItem() {} },
    matchMedia: () => ({ matches: false, addEventListener() {} }), fetch: () => Promise.reject(new Error("no fetch in page context")),
    maplibregl: el, console: { log() {}, warn() {}, error() {} }, URL, URLSearchParams, TextEncoder, TextDecoder, structuredClone
  });
  return ctx;
}

function evaluate(name, depth = 0) {
  if (cache.has(name)) return cache.get(name);
  if (depth > 40) throw new Error(`page constant ${name}: dependency chain too deep`);
  const { kind, src } = constSource(name);
  const c = context();
  for (let tries = 0; tries < 60; tries++) {
    try {
      const v = kind === "fn" ? vm.runInContext(`(${src})`, c) : vm.runInContext(`(${src}\n)`, c, { filename: `index.html:${name}` });
      c[name] = v;
      cache.set(name, v);
      return v;
    } catch (e) {
      const m = /^(\w+) is not defined$/.exec(e && e.message);
      if (!m || m[1] === name || m[1] in c) throw new Error(`page constant ${name} failed to evaluate: ${e.message}`);
      c[m[1]] = evaluate(m[1], depth + 1);   // pull in the dependency, then retry
    }
  }
  throw new Error(`page constant ${name}: too many unresolved references`);
}

// Public: a page constant by name (evaluated once). Throws if it can't be found or evaluated.
export function pageConst(name) { return evaluate(name); }
export function hasConst(name) { return !!constSource(name, { quiet: true }); }

// named getters for the constants the CLI uses
export const page = {};
for (const n of REQUIRED) Object.defineProperty(page, n, { get: () => evaluate(n), enumerable: true });

const unescapeHtml = s => s.replace(/<[^>]+>/g, "").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/\s+/g, " ").trim();
export const stripHtml = s => s == null ? s : unescapeHtml(String(s));

// "Background shading" <select id="base">: [{value, label, group}]
let baseOpts = null;
export function baseOptions() {
  if (baseOpts) return baseOpts;
  const h = pageHtml(), m = /<select[^>]*id="base"[^>]*>([\s\S]*?)<\/select>/.exec(h);
  if (!m) throw new Error(`<select id="base"> not found in ${INDEX}`);
  const out = [];
  let group = "";
  const re = /<optgroup label="([^"]*)">|<option value="([^"]*)"[^>]*>([\s\S]*?)<\/option>/g;
  let t;
  while ((t = re.exec(m[1]))) {
    if (t[1] != null) group = unescapeHtml(t[1]);
    else if (t[2]) out.push({ value: t[2], label: unescapeHtml(t[3]), group });
  }
  return (baseOpts = out);
}

// sidebar checkbox labels: { groupKey: {label, note} } from <input id="lyr-KEY"> … <span class="name">Label<small>note</small>
let sideLabels = null;
export function sidebarLabels() {
  if (sideLabels) return sideLabels;
  const h = pageHtml(), out = {};
  const re = /<input type="checkbox" id="lyr-(\w+)"[\s\S]*?<span class="name">([\s\S]*?)<\/span>/g;
  let t;
  while ((t = re.exec(h))) {
    const [, key, inner] = t, sm = /<small[^>]*>([\s\S]*?)<\/small>/.exec(inner);
    out[key] = { label: unescapeHtml(inner.replace(/<small[\s\S]*$/, "")), note: sm ? unescapeHtml(sm[1]) : "" };
  }
  return (sideLabels = out);
}

// every required constant, for tests and `atlas doctor`-style checks
export function loadAll() {
  const out = {};
  for (const n of REQUIRED) out[n] = evaluate(n);
  return out;
}
