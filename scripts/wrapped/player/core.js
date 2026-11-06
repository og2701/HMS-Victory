// Core of the Wrapped player: maths, formatting, DOM building, the declarative animation pass, the
// number rolls, pixel-reveal avatars, the pixel-dissolve transitions and the timeline itself.
//
// Everything on screen is a pure function of time. Nothing uses CSS transitions, requestAnimationFrame
// deltas or Math.random, so seeking to any moment always draws the same frame - which is what lets the
// renderer step through the video frame by frame and get a perfect result.

const W = 1080, H = 1920, FPS = 25;
const DATA = JSON.parse(document.getElementById("data").textContent);
// The personal page carries everyone's own Wrapped; ?u=<id> picks whose this one is.
if (DATA.personal) {
  const id = new URLSearchParams(location.search).get("u") || Object.keys(DATA.personal.people)[0];
  DATA.me = { ...DATA.personal.people[id], user: id };
  DATA.meta = DATA.personal.meta;
  DATA.people = { [id]: { name: DATA.me.name, avatar: DATA.me.avatar } };
  for (const r of [DATA.me.mate, DATA.me.you_reply, DATA.me.reply_you, DATA.me.fan, ...(DATA.me.circle || [])]) {
    if (r) DATA.people[r.user] = { name: r.name, avatar: r.avatar };
  }
}
const PEOPLE = DATA.people;

// ---------------------------------------------------------------- maths
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, t) => a + (b - a) * t;
const prog = (t, start, dur) => clamp((t - start) / dur);
const E = {
  lin: x => x,
  inCubic: x => x * x * x,
  inQuad: x => x * x,
  outQuad: x => 1 - (1 - x) * (1 - x),
  outCubic: x => 1 - Math.pow(1 - x, 3),
  outQuart: x => 1 - Math.pow(1 - x, 4),
  outQuint: x => 1 - Math.pow(1 - x, 5),
  outExpo: x => (x >= 1 ? 1 : 1 - Math.pow(2, -10 * x)),
  inExpo: x => (x <= 0 ? 0 : Math.pow(2, 10 * x - 10)),
  inOutCubic: x => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2),
  inOutQuint: x => (x < 0.5 ? 16 * x ** 5 : 1 - Math.pow(-2 * x + 2, 5) / 2),
  inOutSine: x => -(Math.cos(Math.PI * x) - 1) / 2,
  outBack: x => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); },
  outBackBig: x => { const c1 = 3, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); },
  outElastic: x => (x <= 0 ? 0 : x >= 1 ? 1 : Math.pow(2, -10 * x) * Math.sin((x * 10 - 0.75) * (2 * Math.PI) / 3) + 1),
};
const hash = n => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
const hash2 = (a, b) => hash(a * 57.31 + b * 113.97);
const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a)); return t * t * (3 - 2 * t); };
// rises to 1 over [a, a+d] then falls back to 0 over [b, b+d]
const pulse = (t, a, b, d = 0.3) => Math.min(prog(t, a, d), 1 - prog(t, b, d));

// ---------------------------------------------------------------- formatting
const fmt = n => Math.round(n).toLocaleString("en-GB");
const short = n => {
  const a = Math.abs(n);
  if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 1 : 2).replace(/\.?0+$/, "") + "M";
  if (a >= 1e4) return (n / 1e3).toFixed(a >= 1e5 ? 0 : 1).replace(/\.0$/, "") + "K";
  return fmt(n);
};
const pct = (now, prev) => (prev ? Math.round((now / prev - 1) * 100) : null);
const signed = n => (n > 0 ? "+" : n < 0 ? "-" : "") + fmt(Math.abs(n));
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
  "October", "November", "December"];
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const dmy = iso => { const [y, m, d] = iso.split("-").map(Number); return `${d} ${MONTHS[m - 1]}`; };
const dmyLong = iso => { const [y, m, d] = iso.split("-").map(Number); return `${d} ${MONTHS_LONG[m - 1]}`; };
const hour12 = h => (h === 0 ? "MIDNIGHT" : h === 12 ? "NOON" : h < 12 ? `${h}AM` : `${h - 12}PM`);
const name = uid => (PEOPLE[uid] ? PEOPLE[uid].name : "Someone");
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ---------------------------------------------------------------- DOM
// h("div.a.b", {style: {...}, data: {a: "up 0 .5"}}, children...) - a tiny hyperscript.
function h(sel, attrs, ...kids) {
  const [tag, ...cls] = sel.split(".");
  const el = document.createElement(tag || "div");
  if (cls.length) el.className = cls.join(" ");
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) { kids.unshift(attrs); attrs = null; }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "style") Object.assign(el.style, v);
    else if (k === "data") for (const [dk, dv] of Object.entries(v)) el.dataset[dk] = dv;
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : document.createTextNode(kid));
  return el;
}
const A = (fx, start, dur, ease) => ({ data: { a: `${fx} ${start} ${dur}${ease ? " " + ease : ""}` } });
function an(el, fx, start, dur, ease) { el.dataset.a = `${fx} ${start} ${dur}${ease ? " " + ease : ""}`; return el; }

// Text with each letter (or word) animated in turn. Letters stay grouped in their word, so a line can
// only ever wrap between words.
function split(text, fx, start, stagger, dur, ease, words = false) {
  let k = 0;
  const at = () => (start + stagger * k++).toFixed(3);
  return text.split(/(\s+)/).filter(Boolean).map(p => {
    if (/^\s+$/.test(p)) return document.createTextNode(" ");
    if (words) return an(h("span.ch", p), fx, at(), dur, ease);
    return h("span.word", [...p].map(c => an(h("span.ch", c), fx, at(), dur, ease)));
  });
}

// Custom emoji tokens and plain text to safe HTML.
function richText(text) {
  return esc(text).replace(/&lt;(a?):(\w+):(\d+)&gt;/g, (_, a, n, id) =>
    `<img class="emo" crossorigin="anonymous" src="https://cdn.discordapp.com/emojis/${id}.${a ? "gif" : "webp"}?size=96" alt="${n}">`)
    .replace(/\n/g, "<br>");
}
function emojiEl(e, id, size = 64) {
  if (id) return h("img", { src: `https://cdn.discordapp.com/emojis/${id}.webp?size=128`, crossorigin: "anonymous",
    style: { width: size + "px", height: size + "px", objectFit: "contain" } });
  return h("span", { style: { fontSize: size * 0.9 + "px", lineHeight: 1 } }, e);
}

// ---------------------------------------------------------------- the animation pass
const FX = {
  fade: (el, e) => { el.style.opacity = e; },
  up: (el, e, p) => { el.style.opacity = clamp(p * 3); el.style.transform = `translateY(${(1 - e) * 110}px)`; },
  down: (el, e, p) => { el.style.opacity = clamp(p * 3); el.style.transform = `translateY(${(e - 1) * 110}px)`; },
  left: (el, e, p) => { el.style.opacity = clamp(p * 3); el.style.transform = `translateX(${(e - 1) * 160}px)`; },
  right: (el, e, p) => { el.style.opacity = clamp(p * 3); el.style.transform = `translateX(${(1 - e) * 160}px)`; },
  pop: (el, e, p) => { el.style.opacity = clamp(p * 4); el.style.transform = `scale(${lerp(0.4, 1, e)})`; },
  slam: (el, e, p) => { el.style.opacity = clamp(p * 5); el.style.transform = `scale(${lerp(2.4, 1, e)})`; },
  zoom: (el, e, p) => { el.style.opacity = clamp(p * 2); el.style.transform = `scale(${lerp(1.25, 1, e)})`; },
  rise: (el, e) => { el.style.transform = `translateY(${(1 - e) * 105}%)`; },
  drop: (el, e) => { el.style.transform = `translateY(${(e - 1) * 105}%)`; },
  wipe: (el, e) => { el.style.clipPath = `inset(0 ${(1 - e) * 100}% 0 0)`; },
  wipeup: (el, e) => { el.style.clipPath = `inset(${(1 - e) * 100}% 0 0 0)`; },
  blur: (el, e, p) => { el.style.opacity = clamp(p * 2); el.style.filter = e >= 1 ? "" : `blur(${(1 - e) * 28}px)`; },
  growx: (el, e) => { el.style.transform = `scaleX(${e})`; },
  growy: (el, e) => { el.style.transform = `scaleY(${e})`; },
  spin: (el, e, p) => { el.style.opacity = clamp(p * 4); el.style.transform = `rotate(${(1 - e) * -200}deg) scale(${lerp(0.2, 1, e)})`; },
  type: (el, e, p) => {
    const full = el.dataset.text ?? (el.dataset.text = el.textContent);
    const n = Math.round(full.length * p);
    el.textContent = full.slice(0, n) + (p < 1 && p > 0 ? "█" : "");
  },
  count: (el, e) => {
    const v = +el.dataset.v, dec = +(el.dataset.dec || 0);
    const x = v * e;
    el.textContent = (el.dataset.pre || "") + (dec ? x.toFixed(dec) : fmt(x)) + (el.dataset.suf || "");
  },
  out: (el, e) => { el.style.opacity = 1 - e; },
  outup: (el, e) => { el.style.opacity = 1 - e; el.style.transform = `translateY(${-e * 120}px)`; },
};
const FX_EASE = { fade: "outQuad", type: "lin", count: "outExpo", pop: "outBack", slam: "outExpo", spin: "outBack",
  growx: "outExpo", growy: "outExpo", out: "inCubic", outup: "inCubic" };

function collectAnims(root) {
  const list = [];
  for (const el of root.querySelectorAll("[data-a]")) {
    for (const spec of el.dataset.a.split("|")) {
      const [fx, s, d, ease] = spec.trim().split(/\s+/);
      list.push({ el, fx: FX[fx], s: +s, d: Math.max(+d, 1e-3), ease: E[ease || FX_EASE[fx] || "outExpo"] });
    }
  }
  return list;
}
function runAnims(list, t) {
  for (const a of list) {
    const p = prog(t, a.s, a.d);
    a.fx(a.el, a.ease(p), p);
  }
}

// ---------------------------------------------------------------- number roll
// A slot-machine number: each digit is a strip of 0-9s that scrolls to rest, the right-hand digits
// spinning through more cycles than the left, so the whole thing settles together.
function roll(value, start, dur, opts = {}) {
  const text = (opts.pre || "") + (opts.text || fmt(value)) + (opts.suf || "");
  const el = h("span.rollw");
  const cols = [];
  const digits = [...text].filter(c => /\d/.test(c)).length;
  let k = 0;
  for (const c of text) {
    if (!/\d/.test(c)) { el.append(h("span.dsep", c)); continue; }
    const cycles = 1 + Math.floor(((digits - k) / digits) * 3);
    const target = cycles * 10 + +c;
    const strip = h("span.dstrip");
    for (let i = 0; i <= target; i++) strip.append(h("span", String(i % 10)));
    el.append(h("span.dcol", strip));
    cols.push({ strip, target, delay: k * (opts.stagger ?? 0.02) });
    k++;
  }
  el._roll = { cols, start, dur, ease: E[opts.ease || "outQuart"] };
  el.classList.add("rollme");
  return el;
}
function runRolls(root, t) {
  for (const el of root.querySelectorAll(".rollme")) {
    const r = el._roll;
    for (const c of r.cols) {
      const e = r.ease(prog(t, r.start + c.delay, r.dur));
      c.strip.style.transform = `translateY(${-c.target * e}em)`;
    }
  }
}

// ---------------------------------------------------------------- images
const IMG = {};
function loadImage(url) {
  if (IMG[url]) return IMG[url].ready;
  const img = new Image();
  img.crossOrigin = "anonymous";
  const ready = new Promise(ok => { img.onload = () => ok(img); img.onerror = () => ok(null); });
  img.src = url;
  IMG[url] = { img, ready };
  return ready;
}
const avatarUrl = uid => (PEOPLE[uid] ? PEOPLE[uid].avatar : "https://cdn.discordapp.com/embed/avatars/0.png");

// An avatar that resolves from a few chunky pixels to a sharp picture - pixels being "placed".
function avatar(uid, size, start = 0, dur = 0.9, cls = "") {
  const c = h("canvas", { width: size, height: size });
  const el = h("div.av" + (cls ? "." + cls : ""), { style: { width: size + "px", height: size + "px" } }, c);
  el.classList.add("pixav");
  el._av = { c, uid, size, start, dur, last: -1 };
  return el;
}
const _tmp = document.createElement("canvas");
const RES_STEPS = [4, 6, 9, 14, 22, 34, 56];
function runAvatars(root, t) {
  for (const el of root.querySelectorAll(".pixav")) {
    const a = el._av;
    const entry = IMG[avatarUrl(a.uid)];
    const img = entry && entry.img.complete && entry.img.naturalWidth ? entry.img : null;
    const p = prog(t, a.start, a.dur);
    const step = p >= 1 ? RES_STEPS.length : Math.floor(p * RES_STEPS.length);
    if (step === a.last) continue;
    a.last = step;
    const ctx = a.c.getContext("2d");
    ctx.clearRect(0, 0, a.size, a.size);
    if (!img) { ctx.fillStyle = "#2a2f4a"; ctx.fillRect(0, 0, a.size, a.size); continue; }
    if (step >= RES_STEPS.length) {
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(img, 0, 0, a.size, a.size);
      continue;
    }
    const r = RES_STEPS[step];
    _tmp.width = r; _tmp.height = r;
    const tc = _tmp.getContext("2d");
    tc.imageSmoothingEnabled = true;
    tc.drawImage(img, 0, 0, r, r);
    ctx.imageSmoothingEnabled = false;
    ctx.globalAlpha = clamp(p * 3);
    ctx.drawImage(_tmp, 0, 0, r, r, 0, 0, a.size, a.size);
    ctx.globalAlpha = 1;
  }
}

// ---------------------------------------------------------------- pixel icons
// Icons are tiny bitmaps: one character per pixel, mapped through a palette, drawn as merged SVG rects.
function pixelIcon(rows, pal, size) {
  const hgt = rows.length, wid = rows[0].length;
  let rects = "";
  rows.forEach((row, y) => {
    let x = 0;
    while (x < wid) {
      const c = row[x];
      let run = 1;
      while (x + run < wid && row[x + run] === c) run++;
      if (c !== "." && pal[c]) rects += `<rect x="${x}" y="${y}" width="${run}" height="1" fill="${pal[c]}"/>`;
      x += run;
    }
  });
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${wid} ${hgt}" width="${size}" height="${size * hgt / wid}" shape-rendering="crispEdges">${rects}</svg>`;
  return h("span", { html: svg, style: { display: "inline-block", lineHeight: 0 } });
}

// ---------------------------------------------------------------- transitions
// Scene changes are a pixel dissolve on the fx canvas: cells cover the outgoing scene in a pattern,
// the leading edge in the chapter's colour, then uncover the next one.
const PATTERNS = {
  rand: (x, y) => hash2(x, y),
  diag: (x, y, cw, ch) => clamp((x / cw + y / ch) / 2 * 0.85 + hash2(x, y) * 0.15),
  radial: (x, y, cw, ch) => clamp(Math.hypot(x / cw - 0.5, (y / ch - 0.5) * 1.6) * 1.15 * 0.85 + hash2(x, y) * 0.15),
  rows: (x, y, cw, ch) => clamp((y / ch) * 0.8 + hash2(x, y) * 0.2),
  cols: (x, y, cw, ch) => clamp((x / cw) * 0.8 + hash2(x, y) * 0.2),
  rain: (x, y, cw, ch) => clamp((y / ch) * 0.55 + hash(x * 3.1) * 0.35 + hash2(x, y) * 0.1),
};
const CELL = 30, CW = W / CELL, CH = Math.ceil(H / CELL);
function drawTransition(ctx, q, pattern, accent, invert = false) {
  if (q <= 0) return;
  const pat = PATTERNS[pattern] || PATTERNS.rand;
  for (let y = 0; y < CH; y++) {
    for (let x = 0; x < CW; x++) {
      let th = pat(x, y, CW, CH);
      if (invert) th = 1 - th;
      const v = q * 1.18 - th;
      if (v <= 0) continue;
      ctx.fillStyle = v < 0.13 ? accent : v < 0.2 ? "#ffffff" : "#04050a";
      if (v < 0.2 && v >= 0.13) ctx.globalAlpha = 0.85;
      ctx.fillRect(x * CELL, y * CELL, CELL, CELL);
      ctx.globalAlpha = 1;
    }
  }
}

// ---------------------------------------------------------------- timeline
const CHAPTERS = [];      // {key, title, accent}
const SCENES = [];        // scene definitions in order
function chapter(key, title, accent, accent2) { CHAPTERS.push({ key, title, accent, accent2: accent2 || accent }); }
function scene(def) { SCENES.push(Object.assign({ tin: 0.36, tout: 0.3, pin: "rand", pout: "rand" }, def)); }

const T = { starts: [], total: 0, cur: -1 };
function layoutTimeline() {
  let t = 0;
  for (const s of SCENES) { T.starts.push(t); t += s.dur; }
  T.total = t;
}
function sceneAt(time) {
  let i = T.starts.length - 1;
  while (i > 0 && T.starts[i] > time) i--;
  return i;
}

// Camera shake and flashes, keyed off scene time, for big reveals.
function shake(t, at, amount = 14, dur = 0.35) {
  const p = prog(t, at, dur);
  if (p <= 0 || p >= 1) return [0, 0];
  const k = (1 - p) * (1 - p) * amount;
  return [Math.sin(t * 91.7) * k, Math.cos(t * 77.3) * k];
}
