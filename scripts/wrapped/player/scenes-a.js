// Scenes, part one: shared components, the cold open, the title, and the Numbers, Awards and People
// chapters. Each scene builds its DOM once and is then driven by update(t) with t in seconds from the
// scene's start; anything with data-a animates itself (see core.js).

chapter("intro", "WRAPPED", "#ff2e4d");
chapter("numbers", "THE NUMBERS", "#38d9ff");
chapter("awards", "THE AWARDS", "#a86bff");
chapter("people", "THE PEOPLE", "#ff4f9e");
chapter("words", "THE WORDS", "#c6ff3d");
chapter("casino", "THE CASINO", "#1fe08f");
chapter("money", "THE MONEY", "#ffc23d");
chapter("games", "THE GAMES", "#ff7a2e");
chapter("badges", "THE BADGES", "#6f8bff");
chapter("outro", "THANK YOU", "#ff2e4d");

const M = DATA.meta;
const FULL = M.full_year;
const YEAR = M.year;
const RANGE = `1 ${MONTHS[0]} TO ${dmy(M.until)}`;

// ---------------------------------------------------------------- components
const block = (top, style, ...kids) => h("div.pad", { style: Object.assign({ top: top + "px" }, style || {}) }, ...kids);
const kicker = (text, at = 0.1) => an(h("div.kick", text), "left", at, 0.7);
const words = (text, cls, at, stagger = 0.06, fx = "up") => h("div." + cls, split(text, fx, at, stagger, 0.75, null, true));
const letters = (text, cls, at, stagger = 0.03, fx = "up") => h("div." + cls, split(text, fx, at, stagger, 0.7));
const line = (text, at, cls = "body", fx = "up") => an(h("div." + cls, text), fx, at, 0.7);
const html = (markup, at, cls = "body", fx = "up") => an(h("div." + cls, { html: markup }), fx, at, 0.7);
const b = s => `<b class="white">${esc(s)}</b>`;
const ba = s => `<b class="acc">${esc(s)}</b>`;
const canvasLayer = () => h("canvas.layer", { width: W, height: H });
const pctChip = (now, prev, at) => {
  const p = pct(now, prev);
  if (p == null) return null;
  return an(h("span.chip." + (p >= 0 ? "up" : "down"), `${p >= 0 ? "+" : ""}${p}% ON ${YEAR - 1}`), "pop", at, 0.5);
};

function valueEl(v, at) {
  const m = /^([+-]?)([\d,]+)(%?)$/.exec(v);
  if (m) return roll(+m[2].replace(/,/g, ""), at, 1.3, { pre: m[1], suf: m[3] });
  return an(h("span", v), "pop", at, 0.6);
}

function awardCard(a, at, opts = {}) {
  const pic = a.b
    ? h("div.cav.duo", avatar(a.a, opts.av || 132, at + 0.5, 0.9), avatar(a.b, opts.av || 132, at + 0.6, 0.9))
    : h("div.cav", a.user ? avatar(a.user, opts.av || 228, at + 0.5, 0.9) : icon(a.icon || a.key, 200));
  const who = a.b ? `${name(a.a)} & ${name(a.b)}` : a.user ? name(a.user) : a.who || "";
  const front = h("div.face",
    h("div.cardf.px", h("div.in.px",
      h("div.sheen"),
      h("div.ctop", icon(a.icon || a.key, opts.compact ? 58 : 78), h("div.ctitle", a.title)),
      pic,
      h("div.cname" + (a.b ? ".two" : ""), who),
      h("div.cval", valueEl(a.value, at + 0.55)),
      h("div.cline", a.line),
      a.runners && a.runners.length ? h("div.crun", "then " + a.runners.map(name).join(" · ")) : null)));
  const back = h("div.face.back", h("div.cardf.px", h("div.in.px", h("div.cardback", icon("crowd", 150)))));
  const card = h("div.card3d.flipme", front, back);
  card._flip = { at, dur: 1.0 };
  return h("div.cardslot" + (opts.compact ? ".compact" : ""), { style: { width: (opts.w || 470) + "px", height: (opts.h || 720) + "px" } }, card);
}
function runFlips(root, t) {
  for (const c of root.querySelectorAll(".flipme")) {
    const p = prog(t, c._flip.at, c._flip.dur);
    // Fade the slot, not the card: opacity below 1 on the card itself flattens its 3D, and the front
    // face would show through mirrored while it's still turned away.
    c.parentElement.style.opacity = clamp(p * 6);
    c.style.transform = `translateY(${(1 - E.outExpo(p)) * 160}px) rotateY(${lerp(180, 0, E.outBack(p))}deg) rotateX(${(1 - E.outCubic(p)) * 18}deg)`;
    const sh = c.querySelector(".sheen");
    if (sh) {
      const s = ((t - c._flip.at - 0.6) / 2.4) % 1;
      sh.style.transform = `translateX(${lerp(-70, 70, s < 0 ? 0 : s)}%)`;
    }
  }
}

function message(m, at, opts = {}) {
  const reacts = (m.reactions || []).slice(0, 5).map((r, k) =>
    an(h("div.react.px", emojiEl(r.e, r.id, 34), roll(r.n, at + 0.55 + k * 0.08, 1)), "pop", at + 0.5 + k * 0.08, 0.5));
  const el = h("div.msg.px", { style: opts.style || {} },
    avatar(m.user, 88, at + 0.05, 0.7),
    h("div", { style: { flex: 1, minWidth: 0 } },
      h("div.who", h("span.name", name(m.user)), h("span.when", m.when || "")),
      h("div.text", { html: richText(m.text) }),
      reacts.length ? h("div.reacts", reacts) : null));
  return an(el, opts.fx || "up", at, 0.75);
}

// Particles streaming along a curve from a to b - replies, reactions, coins. Each particle is born at a
// staggered moment, so a stream starts from its source rather than appearing all at once.
function flow(ctx, t, a, b, n, o = {}) {
  const speed = o.speed ?? 0.55, curve = o.curve ?? 160, size = o.size ?? 12, spread = o.spread ?? 40, seed = o.seed ?? 1;
  const start = o.start ?? 0, stop = o.stop ?? 1e9;
  const cx = (a[0] + b[0]) / 2 + (o.bend ?? 0), cy = (a[1] + b[1]) / 2 - curve;
  const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1, nx = -dy / len, ny = dx / len;
  for (let i = 0; i < n; i++) {
    const h1 = hash(i * 1.37 + seed * 9.1), h2 = hash(i * 2.11 + seed * 3.3), h3 = hash(i * 4.7 + seed);
    const life = (t - start) * speed - h1;
    if (life < 0) continue;
    if (t - (life % 1) / speed > stop) continue;
    const ph = life % 1, u = 1 - ph;
    let x = u * u * a[0] + 2 * u * ph * cx + ph * ph * b[0];
    let y = u * u * a[1] + 2 * u * ph * cy + ph * ph * b[1];
    const j = (h2 - 0.5) * spread * Math.sin(ph * Math.PI);
    x += nx * j; y += ny * j;
    const s = size * (0.6 + h3 * 0.8);
    ctx.globalAlpha = Math.sin(ph * Math.PI) * (o.alpha ?? 1);
    ctx.fillStyle = o.colors ? o.colors[i % o.colors.length] : (o.color || "#fff");
    ctx.fillRect(Math.round(x - s / 2), Math.round(y - s / 2), s, s);
  }
  ctx.globalAlpha = 1;
}
// The centre of an element in stage pixels, from layout offsets, so entrance animations don't move it.
function posOf(el) {
  let x = el.offsetWidth / 2, y = el.offsetHeight / 2;
  for (let e = el; e && e.id !== "stage"; e = e.offsetParent) { x += e.offsetLeft; y += e.offsetTop; }
  return [x, y];
}

function chapterScene(key, num, title, sub, extra = {}) {
  scene(Object.assign({
    id: "ch-" + key, ch: key, dur: 3.0, fixed: true, boardGlow: 2.6, bloom: 1.05, pin: "diag", pout: "radial",
    build(r) {
      r.append(h("div.scrim-mid"),
        h("div.abs.center", { style: { left: 0, right: 0, top: "520px" } },
          an(h("div.chnum", String(num).padStart(2, "0")), "slam", 0.05, 0.6),
          h("div.mega", { style: { fontSize: "186px", marginTop: "36px" } }, split(title, "up", 0.22, 0.035, 0.6)),
          sub ? an(h("div.h3", { style: { marginTop: "44px", color: "var(--mute)" } }, sub), "up", 0.7, 0.6) : null));
    },
  }, extra));
}

// ---------------------------------------------------------------- cold open
scene({
  id: "cold", ch: "intro", dur: 5.6, board: false, bloom: 0.5, pin: "radial", pout: "radial",
  build(r) {
    const first = (DATA.words.first || []).slice(0, 3);
    const jan1 = M.since.endsWith("-01-01");
    r.append(h("div.flash"),
      h("div.abs.center", { style: { left: 0, right: 0, top: "330px" } },
        an(h("div.kick.plain", { style: { color: "var(--white)", fontSize: "30px" } },
          jan1 ? `1 JANUARY ${YEAR}` : dmyLong(M.since).toUpperCase()), "type", 0.15, 0.6),
        an(h("div.clock.mega", { style: { fontSize: "250px", marginTop: "40px", letterSpacing: ".02em" } }, "23:59:57"), "fade", 0.1, 0.3)),
      h("div.abs", { style: { left: "56px", right: "56px", top: "700px" } },
        an(h("div.kick", `THE FIRST MESSAGES OF ${YEAR}`), "left", 1.9, 0.6),
        jan1 && first.length ? an(h("div.small", { style: { marginTop: "12px" } },
          `Sent within ${Math.max(...first.map(m => +m.time.slice(-2))) || 1} seconds of midnight`), "left", 2.0, 0.6) : null),
      h("div.abs", { style: { left: "56px", right: "56px", top: "860px", display: "flex", flexDirection: "column", gap: "26px" } },
        first.map((m, k) => message({ user: m.user, text: m.text, when: `${jan1 ? "1 Jan" : dmy(M.since)} at ${m.time}` }, 2.4 + k * 0.95))));
  },
  update(t, r) {
    const seq = ["23:59:57", "23:59:58", "23:59:59", "00:00:00", "00:00:01"];
    const k = clamp(Math.floor((t - 0.45) / 0.34), 0, seq.length - 1);
    const clock = r.querySelector(".clock");
    clock.textContent = seq[k];
    clock.style.color = k >= 3 ? "var(--white)" : "var(--dim)";
    const midnight = 0.45 + 3 * 0.34;
    r.querySelector(".flash").style.opacity = 0.55 * (1 - prog(t, midnight, 0.35)) * (t >= midnight ? 1 : 0);
    const [sx, sy] = shake(t, midnight, 16);
    clock.style.transform = `translate(${sx}px, ${sy}px) scale(${1 + 0.12 * (1 - E.outCubic(prog(t, midnight, 0.5))) * (t >= midnight ? 1 : 0)})`;
  },
});

// ---------------------------------------------------------------- title
scene({
  id: "title", ch: "intro", dur: 7.4, fixed: true, gl: "flag", bloom: 0.75, pin: "radial", pout: "diag",
  cam: t => {
    const k = E.inOutCubic(prog(t, 0.0, 3.8));
    const a = lookCam([30, 60, 90], [0, 72, -40], 50), bcam = lookCam([0, 84, 600], [0, 74, 0], 42);
    const c = mixCam(a, bcam, k);
    c.p[0] += Math.sin(t * 0.4) * 10;
    c.p[2] -= t * 4;
    return c;
  },
  build(r) {
    r.append(h("div.scrim-top", { style: { height: "760px" } }), h("div.scrim-bot", { style: { height: "760px" } }),
      block(250, { textAlign: "center" },
        an(h("div.kick", { style: { justifyContent: "center", color: "var(--white)" } }, "UKPLACE PRESENTS"), "fade", 1.0, 0.6),
        h("div.mega", { style: { fontSize: "250px", marginTop: "34px" } }, split("WRAPPED", "slam", 1.55, 0.06, 0.55))),
      block(1290, { textAlign: "center" },
        h("div.yr", an(h("div.mega.outline", { style: { fontSize: "420px" } }, String(YEAR)), "fade", 2.2, 0.4),
          an(h("div.mega.yrfill", { style: { fontSize: "420px" } }, String(YEAR)), "wipe", 2.55, 0.9, "inOutCubic")),
        an(h("div.kick", { style: { justifyContent: "center", marginTop: "34px", color: "var(--white)" } },
          FULL ? "THE YEAR IN FULL" : `THE YEAR SO FAR · ${RANGE}`), "up", 3.1, 0.7)));
  },
  update(t, r) {
    const [sx, sy] = shake(t, 1.75, 12);
    r.style.transform = `translate(${sx}px, ${sy}px)`;
  },
});

// ---------------------------------------------------------------- the numbers
chapterScene("numbers", 1, "THE NUMBERS", "Everything we said, all year");

const NOW = DATA.numbers.now, PREV = DATA.numbers.prev;
const DAYS_IN = Math.round((new Date(M.until) - new Date(M.since)) / 864e5) + 1;

scene({
  id: "messages", ch: "numbers", dur: 6.0, gl: "tower", bloom: 0.9,
  cam: t => orbit(0, 0, 0, 240, 0.7 + t * 0.16, 150 + t * 4, 92),
  build(r) {
    const every = Math.round((DAYS_IN * 86400) / NOW.messages);
    r.append(h("div.scrim-top", { style: { height: "980px" } }),
      block(230, {},
        kicker("MESSAGES SENT", 0.1),
        h("div.mega.glow", { style: { fontSize: "236px", marginTop: "28px" } }, roll(NOW.messages, 0.25, 3.5, { ease: "outQuart" })),
        h("div.row", { style: { gap: "22px", marginTop: "34px" } }, pctChip(NOW.messages, PREV.messages, 3.4),
          DATA.race.passed_on ? html(`Passed the whole of ${YEAR - 1} on ${b(dmyLong(DATA.race.passed_on))}`, 3.5, "small") : null),
        html(`That's one every ${b(every + " seconds")}, day and night, for ${b(DAYS_IN + " days")}.`, 4.1, "h3",
          "up")));
  },
});

scene({
  id: "stats", ch: "numbers", dur: 6.0, boardGlow: 1.3,
  build(r) {
    const bibles = NOW.words / 783137;
    const tiles = [
      { k: "WORDS", v: NOW.words, sub: `About ${Math.round(bibles)} King James Bibles' worth`, p: PREV.words, art: ["waffler", "book"] },
      { k: "PEOPLE WHO POSTED", v: NOW.people, sub: `Up from ${fmt(PREV.people)}`, p: PREV.people, art: "crowd" },
      { k: "REACTIONS", v: NOW.reactions, sub: "Thumbs, skulls, crying faces", p: PREV.reactions, art: ["thumbs", "killer", "crying"] },
      { k: "FILES AND PICTURES", v: NOW.files, sub: `Plus ${fmt(NOW.voice_notes)} voice notes`, p: PREV.files, art: ["media", "voice"] },
    ];
    r.append(h("div.scrim-mid"),
      block(230, {}, kicker("THE YEAR IN NUMBERS"), words("IT ALL ADDS UP", "title", 0.2)),
      h("div.abs", { style: { left: "56px", right: "56px", top: "560px", display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: "28px" } },
        tiles.map((x, k) => an(h("div.frame.acc.px", { style: { height: "560px" } },
          h("div.in.px.tile",
            h("div.tk", x.k),
            h("div.tileart", (x.art === "crowd" ? crowdArt() : x.art.map(key => icon(key, 92)))
              .map((el, j) => an(h("div.bob", { data: { k: j + k * 3 } }, el), "pop", 0.7 + k * 0.15 + j * 0.1, 0.6))),
            h("div.big", { style: { fontSize: x.v >= 1e6 ? "112px" : "150px", marginTop: "auto" } }, roll(x.v, 0.5 + k * 0.18, 2.2)),
            h("div.small", { style: { marginTop: "18px" } }, x.sub),
            h("div", { style: { marginTop: "22px" } }, pctChip(x.v, x.p, 2.4 + k * 0.15)))), "up", 0.35 + k * 0.15, 0.8))));
  },
  update(t, r) {
    r.querySelectorAll(".bob").forEach(el => { el.firstChild.style.transform = `translateY(${Math.round(Math.sin(t * 2.2 + +el.dataset.k) * 2) * 4}px)`; });
  },
});

scene({
  id: "race", ch: "numbers", dur: 7.0, revealEnd: 5.4, boardGlow: 0.6, pin: "cols",
  build(r) {
    const rc = DATA.race;
    r._cv = canvasLayer();
    r.append(h("div.scrim-mid"), r._cv,
      block(230, {}, kicker("THE RACE"), words(`${YEAR} VS ${YEAR - 1}`, "title", 0.2),
        html(`Messages so far, added up day by day. ${ba("Blue")} is ${YEAR}, <span class="red"><b>red</b></span> is ${YEAR - 1}.`, 0.5, "small")),
      h("div.abs.racelabel", { style: { left: "80px", top: "0px" } }, h("div.kick.plain.rl1", ""), h("div.h2.rl2", "")),
      block(1560, {},
        rc.passed_on ? html(`Already ${ba(Math.round((rc.this_total / rc.last_total - 1) * 100) + "%")} more than the whole of ${YEAR - 1}.`, 5.0, "h3") :
          html(`${Math.round((rc.this_total / rc.last_total) * 100)}% of the way to all of ${YEAR - 1}.`, 5.0, "h3"),
        !FULL ? line(`With ${Math.round((new Date(YEAR + "-12-31") - new Date(M.until)) / 864e5)} days still to go.`, 5.6, "body") : null));
  },
  update(t, r) {
    const rc = DATA.race, ctx = r._cv.getContext("2d");
    ctx.clearRect(0, 0, W, H);
    const X0 = 96, X1 = 984, Y0 = 1420, Y1 = 640;
    const maxV = Math.max(rc.this_total, rc.last_total) * 1.06;
    const cum = arr => { let s = 0; return arr.map(n => (s += n)); };
    const last = cum(rc.last), now = cum(rc.this);
    const xs = i => X0 + (i / 365) * (X1 - X0), ys = v => Y0 - (v / maxV) * (Y0 - Y1);
    // axes
    ctx.fillStyle = "rgba(255,255,255,.12)";
    for (let m = 0; m <= 12; m++) ctx.fillRect(xs(m * 30.4), Y1 - 20, 2, Y0 - Y1 + 20);
    ctx.font = "18px Pixel"; ctx.fillStyle = "#6b7399";
    for (let m = 0; m < 12; m += 2) ctx.fillText(MONTHS[m], xs(m * 30.4) + 6, Y0 + 40);
    // last year's total: the finish line
    const fy = ys(rc.last_total), fp = E.outExpo(prog(t, 0.2, 1));
    ctx.fillStyle = "#ff2e4d";
    for (let x = X0; x < X0 + (X1 - X0) * fp; x += 28) ctx.fillRect(x, fy - 3, 16, 6);
    ctx.font = "20px Pixel";
    ctx.globalAlpha = fp;
    ctx.fillText(`ALL OF ${YEAR - 1}: ${fmt(rc.last_total)}`, X0, fy - 22);
    ctx.globalAlpha = 1;
    const drawLine = (arr, upto, colour, width, glow) => {
      if (upto < 1) return;
      ctx.save();
      ctx.strokeStyle = colour; ctx.lineWidth = width; ctx.lineJoin = "round";
      if (glow) { ctx.shadowColor = colour; ctx.shadowBlur = 30; }
      ctx.beginPath();
      for (let i = 0; i < upto; i++) { const x = xs(i), y = ys(arr[i]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
      ctx.stroke();
      ctx.restore();
    };
    drawLine(last, Math.floor(last.length * E.inOutCubic(prog(t, 0.4, 1.4))), "rgba(255,46,77,.75)", 6, false);
    const k = E.inOutCubic(prog(t, 1.6, 3.4));
    const upto = Math.max(1, Math.floor(now.length * k));
    drawLine(now, upto, "#38d9ff", 10, true);
    const i = upto - 1, x = xs(i), y = ys(now[i]);
    ctx.fillStyle = "#fff";
    ctx.shadowColor = "#38d9ff"; ctx.shadowBlur = 40;
    ctx.fillRect(x - 12, y - 12, 24, 24);
    ctx.shadowBlur = 0;
    const lab = r.querySelector(".racelabel");
    lab.style.left = clamp(x - 150, 60, 640) + "px";
    lab.style.top = (y - 170) + "px";
    lab.style.opacity = clamp((t - 1.6) * 4);
    const d = new Date(new Date(rc.this_start).getTime() + i * 864e5).toISOString().slice(0, 10);
    lab.querySelector(".rl1").textContent = dmy(d);
    lab.querySelector(".rl2").textContent = fmt(now[i]);
    // the moment it passes last year
    const pass = rc.passed_on ? rc.this.length && Math.round((new Date(rc.passed_on) - new Date(rc.this_start)) / 864e5) : -1;
    if (pass >= 0) {
      const tp = 1.6 + 3.4 * (pass / now.length);
      const bp = prog(t, tp, 0.9);
      if (bp > 0) {
        const px = xs(pass), py = ys(now[pass]);
        for (let q = 0; q < 26; q++) {
          const a = (q / 26) * Math.PI * 2 + hash(q) * 0.3, dist = E.outExpo(bp) * (120 + hash(q * 3) * 160);
          ctx.globalAlpha = 1 - bp;
          ctx.fillStyle = q % 3 ? "#38d9ff" : "#ffffff";
          ctx.fillRect(px + Math.cos(a) * dist - 7, py + Math.sin(a) * dist - 7, 14, 14);
        }
        ctx.globalAlpha = 1;
        const [sx, sy] = shake(t, tp, 12);
        r._cv.style.transform = `translate(${sx}px, ${sy}px)`;
      }
    }
  },
});

// The calendar's captioned days: the busiest, at least a week apart so the labels don't collide.
function pickMoments(n = 3) {
  const out = [];
  for (const d of DATA.calendar.top) {
    if (out.every(o => Math.abs(new Date(o.day) - new Date(d.day)) > 6 * 864e5)) out.push(d);
    if (out.length === n) break;
  }
  return out;
}

scene({
  id: "skyline", ch: "numbers", dur: 9.4, gl: "skyline", bloom: 0.95, pin: "rows",
  // From January's end, looking down the year: the towers rise away from the camera in a wave, and the
  // busiest weeks stand tallest at the far end.
  cam: t => {
    const half = SKY.length / 2;
    const k = E.inOutSine(prog(t, 0.3, 4.2));
    const a = lookCam([24, lerp(34, 120, k), -half - lerp(26, 80, k)], [0, lerp(8, 22, k), lerp(-half + 50, 30, k)], 50);
    const bcam = lookCam([60, 165, -half - 150], [0, 66, 44], 46);
    const c = mixCam(a, bcam, E.inOutCubic(prog(t, 4.3, 2.0)));
    c.p[0] -= prog(t, 6.3, 3.1) * 40;
    return c;
  },
  build(r) {
    r.append(h("div.scrim-top", { style: { height: "640px" } }),
      block(230, {}, kicker(`EVERY DAY OF ${YEAR}`), words("THE SKYLINE", "title", 0.2),
        line("Each tower is a day. The taller, the busier.", 0.6, "small")));
    // The busiest days get a numbered tag over their tower and a line in the legend underneath.
    const moments = pickMoments();
    r.append(h("div.scrim-bot", { style: { height: "560px" } }),
      h("div.abs.col", { style: { left: "56px", right: "56px", top: "1480px", gap: "18px" } },
        moments.map((m, k) => an(h("div.lrow", h("div.daytag", String(k + 1)),
          h("div", { style: { flex: 1, minWidth: 0 } }, h("div.h3", m.caption || (m.channel ? "#" + m.channel.replace(/^.*｜/, "") : dmyLong(m.day))),
            h("div.tiny", `${dmyLong(m.day).toUpperCase()} · ${fmt(m.n)} MESSAGES`))), "left", 5.4 + k * 0.3, 0.6))));
    r._labels = moments.map((m, k) => { const el = h("div.label3d.daytag", String(k + 1)); r.append(el); return { el, m }; });
    r._months = SKY.months.map(mo => { const el = h("div.label3d.tiny", MONTHS[mo.m - 1]); r.append(el); return { el, mo }; });
  },
  update(t, r) {
    r._labels.forEach(({ el, m }, k) => {
      const c = SKY.cols.find(c => c.iso === m.day);
      if (!c) return;
      const [x, y, z] = toScreen(c.x, c.top + 3, c.z);
      const p = prog(t, 5.4 + k * 0.3, 0.6);
      el.style.left = x + "px";
      el.style.top = (y - 10) + "px";
      el.style.opacity = z < 1 ? E.outCubic(p) : 0;
      el.style.transform = `translate(-50%, ${-100 + (1 - E.outBack(p)) * 60}%)`;
    });
    r._months.forEach(({ el, mo }) => {
      const [x, y, z] = toScreen(-26, 0, mo.z);
      el.style.left = x + "px"; el.style.top = (y - 10) + "px";
      el.style.opacity = z < 1 && x > 30 && x < W - 30 ? clamp((t - 0.8) * 2) * 0.9 : 0;
    });
  },
});

scene({
  id: "clock", ch: "numbers", dur: 6.6, revealEnd: 3.4, gl: "clock", bloom: 0.95, pin: "radial",
  cam: t => orbit(0, 0, 0, 190 + t * 3, 0.15 + t * 0.07, 400 - t * 6, 18),
  build(r) {
    const hrs = DATA.calendar.hours, peak = hrs.indexOf(Math.max(...hrs));
    const quiet = hrs.indexOf(Math.min(...hrs));
    const wk = DATA.calendar.weekdays, top = wk.indexOf(Math.max(...wk)), low = wk.indexOf(Math.min(...wk));
    const wmax = Math.max(...wk);
    r.append(h("div.scrim-top", { style: { height: "700px" } }), h("div.scrim-bot", { style: { height: "560px" } }),
      block(230, {}, kicker("WHEN WE TALK"),
        h("div.title", split("PEAK HOUR:", "up", 0.2, 0.03, 0.6), " ", an(h("span.acc", hour12(peak)), "slam", 2.9, 0.5)),
        line("Every message of the year, by the hour it was sent. Each column is one hour.", 0.5, "small"),
        html(`${b(fmt(hrs[peak]))} messages at ${hour12(peak).toLowerCase()}. The quietest: ${hour12(quiet).toLowerCase()}, with ${fmt(hrs[quiet])}.`, 3.3, "small")),
      block(1500, {},
        h("div.row", { style: { gap: "12px", alignItems: "flex-end", height: "170px" } },
          wk.map((n, i) => h("div.col", { style: { flex: 1, alignItems: "center", gap: "12px", justifyContent: "flex-end", height: "100%" } },
            an(h("div.pxcol", { style: { height: (n / wmax) * 120 + "px", background: i === top ? "var(--acc)" : "rgba(255,255,255,.25)" } }), "growy", 3.6 + i * 0.06, 0.6),
            h("div.tiny", { style: { color: i === top ? "var(--acc)" : "" } }, DAYS[i].slice(0, 3))))),
        html(`${ba(DAYS[top])} is the busiest day. ${b(DAYS[low])} is the quietest.`, 4.2, "h3")));
    r._hl = [0, 6, 12, 18].map(hr => { const el = h("div.label3d.tiny", { style: { color: "var(--white)" } }, hour12(hr)); r.append(el); return { el, hr }; });
  },
  update(t, r) {
    r._hl.forEach(({ el, hr }) => {
      const a = (hr / 24) * Math.PI * 2, R = CLK.R + 22;
      const [x, y, z] = toScreen(Math.sin(a) * R, 2, -Math.cos(a) * R);
      el.style.left = x + "px"; el.style.top = (y + 16) + "px";
      el.style.opacity = z < 1 ? clamp((t - 0.6) * 2) : 0;
    });
  },
});

scene({
  id: "channels", ch: "numbers", dur: 6.6, boardGlow: 0.8,
  build(r) {
    const ch = DATA.channels.top;
    const clean = n => n.replace(/^(\S+)｜/, "$1 ");
    const total = NOW.messages;
    const head = ch[0], rest = ch.slice(1, 8), max = rest.length ? rest[0].n : 1;
    const chip = c => c.prev_rank == null ? h("span.chip.new", "NEW") :
      c.prev_rank > c.rank ? h("span.chip.up", `▲${c.prev_rank - c.rank}`) :
        c.prev_rank < c.rank ? h("span.chip.down", `▼${c.rank - c.prev_rank}`) : null;
    const climber = ch.filter(c => c.prev_rank).sort((a, bb) => (bb.prev_rank - bb.rank) - (a.prev_rank - a.rank))[0];
    r.append(h("div.scrim-mid"),
      block(230, {}, kicker("WHERE IT HAPPENED"), words("TOP CHANNELS", "title", 0.2)),
      block(500, {},
        an(h("div.frame.acc.px", h("div.in.px", { style: { padding: "34px 38px" } },
          h("div.row", { style: { justifyContent: "space-between" } }, h("div.h2", clean(head.name)), h("div.h2.acc", roll(head.n, 0.6, 1.6))),
          h("div.small", { style: { marginTop: "10px" } }, `${Math.round((head.n / total) * 100)}% of every message sent. Everywhere else shares the rest.`))), "up", 0.4, 0.8),
        h("div.col.listpanel", { style: { gap: "18px", marginTop: "36px" } },
          rest.map((c, k) => an(h("div.lrow", { style: { height: "96px" } },
            h("div.rank", `#${c.rank}`),
            h("div", { style: { flex: 1, minWidth: 0 } },
              h("div.row", { style: { justifyContent: "space-between", gap: "16px" } },
                h("div.h3", { style: { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } }, clean(c.name)),
                h("div.row", { style: { gap: "14px" } }, chip(c), h("div.h3.acc", fmt(c.n)))),
              h("div", { style: { height: "16px", marginTop: "10px", background: "rgba(255,255,255,.06)" } },
                an(h("div.pxbar", { style: { width: (c.n / max) * 100 + "%" } }), "growx", 1.1 + k * 0.1, 1.0)))), "left", 0.9 + k * 0.1, 0.7))),
        climber && climber.prev_rank - climber.rank >= 5 ?
          html(`${ba("#" + clean(climber.name).replace(/^\S+\s/, ""))} climbed ${climber.prev_rank - climber.rank} places on ${YEAR - 1}.`, 2.6, "h3") : null));
  },
});

// ---------------------------------------------------------------- the awards
chapterScene("awards", 2, "THE AWARDS", "No speeches.");

// Six awards to a page: two across, three down. A page with four or fewer uses the bigger cards.
const AWARD_PAGES = [
  ["AROUND THE CLOCK", ["night_owl", "early_bird", "lunch", "weekend", "committed", "one_day"]],
  ["HOW YOU TALK", ["waffler", "silent", "shouter", "nosey", "apologist", "british"]],
  ["WHAT YOU SHARE", ["media", "plug", "voice", "sticker", "emoji", "meme"]],
  ["THE CROWD PLEASERS", ["hype", "crowd", "comedian", "heartthrob", "celeb", "hall_of_famer"]],
  ["THE REGULARS", ["gossip", "tourist", "glue", "starter", "killer", "bot_friend"]],
  ["AROUND THE SERVER", ["welcomer", "wordle", "bump", "tree", "oracle", "roasted"]],
  ["BAD HABITS", ["perfectionist", "dry", "laugh_only", "fake_gn", "own_fan", "ratio"]],
];
const AWARDS = Object.fromEntries(DATA.awards.map(a => [a.key, a]));
const SHOWN = [];
function cardGrid(list, at0 = 0.15) {
  const compact = list.length > 4;
  return h("div.abs", { style: { left: "56px", right: "56px", top: compact ? "270px" : "290px", display: "grid",
    gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: compact ? "24px 28px" : "36px 28px", justifyItems: "center" } },
    list.map((a, k) => awardCard(a, at0 + k * (compact ? 0.16 : 0.22), compact ? { compact: true, h: 512, av: a.b ? 118 : 150 } : {})));
}
AWARD_PAGES.forEach(([title, keys], g) => {
  const list = keys.map(k => AWARDS[k]).filter(Boolean);
  if (!list.length) return;
  SHOWN.push(...list);
  scene({
    id: "awards-" + g, ch: "awards", dur: 5.2, boardGlow: 1.2, bloom: 0.9, pin: g % 2 ? "cols" : "diag",
    build(r) {
      r.append(h("div.scrim-mid"), block(190, {}, kicker(title, 0.05)), cardGrid(list));
    },
  });
});

const wins = {};
SHOWN.forEach(a => { (wins[a.user] = wins[a.user] || []).push(a); });
const MAIN = Object.entries(wins).sort((a, bb) => bb[1].length - a[1].length)[0];
if (MAIN && MAIN[1].length >= 3) {
  const [uid, list] = MAIN;
  scene({
    id: "main-character", ch: "awards", dur: 5.8, boardGlow: 2, bloom: 1.1, pin: "radial",
    build(r) {
      r.append(h("div.scrim-mid"),
        block(230, { textAlign: "center" }, an(h("div.kick", { style: { justifyContent: "center" } }, "MAIN CHARACTER ENERGY"), "fade", 0.1, 0.5),
          h("div.title", { style: { marginTop: "24px" } }, split(`${list.length} AWARDS.`, "slam", 0.3, 0.05, 0.5)),
          h("div.title.acc", split("ONE PERSON.", "slam", 0.9, 0.05, 0.5))),
        h("div.abs.center.orbitwrap", { style: { left: 0, right: 0, top: "640px", height: "760px" } },
          h("div.orbit", list.map((a, k) => h("div.obadge", { data: { k } }, icon(a.key, 110)))),
          an(h("div", avatar(uid, 360, 1.3, 1.1, "ring")), "pop", 1.2, 0.8)),
        block(1440, { textAlign: "center" }, an(h("div.big", name(uid)), "up", 2.2, 0.7),
          line(list.map(a => a.title).join(" · "), 2.6, "small")));
    },
    update(t, r) {
      const n = list.length;
      r.querySelectorAll(".obadge").forEach((el, k) => {
        const a = (k / n) * Math.PI * 2 + t * 0.6;
        const p = E.outBack(prog(t, 1.5 + k * 0.08, 0.7));
        const depth = Math.cos(a);
        const x = Math.sin(a) * 420 * p, y = depth * 150 * p + 20;
        el.style.transform = `translate(${x}px, ${y}px) scale(${(0.72 + depth * 0.28) * p})`;
        el.style.zIndex = depth > 0 ? 3 : 1;
        el.style.opacity = clamp(p) * (0.5 + 0.5 * (depth + 1) / 2);
      });
    },
  });
}

// ---------------------------------------------------------------- the people
chapterScene("people", 3, "THE PEOPLE", "Who talked to who");
const SOC = DATA.social;

scene({
  id: "replied", ch: "people", dur: 6.2, boardGlow: 0.8, bloom: 1,
  build(r) {
    const [top, ...rest] = SOC.replied;
    r._cv = canvasLayer();
    r.append(h("div.scrim-mid"), r._cv,
      block(230, {}, kicker("THE MOST REPLIED-TO"), words("EVERYONE'S TALKING TO", "title", 0.2, 0.05)),
      h("div.abs.center", { style: { left: 0, right: 0, top: "560px" } },
        an(h("div", r._hero = avatar(top.user, 330, 0.5, 1, "ring")), "pop", 0.4, 0.8),
        an(h("div.big", { style: { marginTop: "30px", fontSize: "130px" } }, name(top.user)), "up", 1.0, 0.7),
        html(`${b(fmt(top.n))} replies from ${b(fmt(top.from))} different people`, 1.4, "h3")),
      block(1300, {}, h("div.col.listpanel", { style: { gap: "16px" } }, rest.slice(0, 4).map((x, k) =>
        an(h("div.lrow", h("div.rank", `#${k + 2}`), avatar(x.user, 76, 2.0 + k * 0.1, 0.6), h("div.h3", { style: { flex: 1 } }, name(x.user)),
          h("div.h3.acc", fmt(x.n))), "left", 1.9 + k * 0.1, 0.6)))));
  },
  update(t, r) {
    const ctx = r._cv.getContext("2d");
    ctx.clearRect(0, 0, W, H);
    const c = r._c || (r._c = posOf(r._hero));
    for (let k = 0; k < 14; k++) {
      const a = (k / 14) * Math.PI * 2 + 0.3;
      flow(ctx, t, [c[0] + Math.cos(a) * 760, c[1] + Math.sin(a) * 900], c, 10, { start: 0.4 + k * 0.04, speed: 0.7, curve: 0, spread: 60, seed: k + 1, size: 10, colors: ["#ff4f9e", "#ffd1ea", "#ffffff"], alpha: 0.85 });
    }
  },
});

scene({
  id: "duos", ch: "people", dur: 6.6, boardGlow: 0.8, bloom: 1,
  build(r) {
    const [d, ...more] = SOC.duos;
    r._cv = canvasLayer();
    r.append(h("div.scrim-mid"), r._cv,
      block(230, {}, kicker("DUO OF THE YEAR"), words("INSEPARABLE", "title", 0.2),
        line("The two people who replied to each other the most", 0.5, "small")),
      h("div.abs.row", { style: { left: "70px", right: "70px", top: "560px", justifyContent: "space-between" } },
        an(h("div.center", r._a = avatar(d.a, 290, 0.4, 0.9, "ring"), h("div.h3", { style: { marginTop: "22px", maxWidth: "330px" } }, name(d.a))), "left", 0.3, 0.8),
        an(h("div.center", r._b = avatar(d.b, 290, 0.55, 0.9, "ring"), h("div.h3", { style: { marginTop: "22px", maxWidth: "330px" } }, name(d.b))), "right", 0.45, 0.8)),
      block(1010, { textAlign: "center" },
        h("div.mega.acc.glow", { style: { fontSize: "220px" } }, roll(d.n, 1.0, 2)),
        line("replies between them", 1.5, "h3"),
        h("div.row", { style: { marginTop: "30px", height: "34px", gap: "6px" } },
          an(h("div", { style: { flex: d.ab, height: "100%", background: "var(--acc)" } }), "growx", 2.2, 0.9),
          an(h("div", { style: { flex: d.ba, height: "100%", background: "#ffd1ea", transformOrigin: "right" } }), "growx", 2.2, 0.9)),
        h("div.row", { style: { justifyContent: "space-between", marginTop: "14px" } },
          line(`${fmt(d.ab)} one way`, 2.6, "small"), line(`${fmt(d.ba)} the other`, 2.6, "small"))),
      block(1500, {}, h("div.col.listpanel", { style: { gap: "14px" } }, more.slice(0, 2).map((x, k) =>
        an(h("div.lrow", h("div.rank", `#${k + 2}`), avatar(x.a, 64, 3.1 + k * 0.1, 0.5), avatar(x.b, 64, 3.15 + k * 0.1, 0.5),
          h("div.small.white", { style: { flex: 1 } }, `${name(x.a)} & ${name(x.b)}`), h("div.h3.acc", fmt(x.n))), "left", 3.0 + k * 0.12, 0.6)))));
  },
  update(t, r) {
    const ctx = r._cv.getContext("2d");
    ctx.clearRect(0, 0, W, H);
    const d = SOC.duos[0];
    const A = r._pa || (r._pa = posOf(r._a)), B = r._pb || (r._pb = posOf(r._b));
    const tot = d.ab + d.ba;
    flow(ctx, t, A, B, Math.round(44 * d.ab / tot), { start: 0.9, speed: 0.75, curve: 130, size: 12, color: "#ff4f9e", seed: 3 });
    flow(ctx, t, B, A, Math.round(44 * d.ba / tot), { start: 0.9, speed: 0.75, curve: -130, size: 12, color: "#ffd1ea", seed: 7 });
  },
});

scene({
  id: "connections", ch: "people", dur: 7.2, boardGlow: 0.8, bloom: 1,
  build(r) {
    const ow = SOC.one_way[0], fan = SOC.fans[0], mate = SOC.mates[0];
    r._cv = canvasLayer();
    r._pairs = [];
    const panel = (at, kick, a, bUser, text) => {
      const pa = avatar(a, 140, at + 0.2, 0.7, "ring"), pb = avatar(bUser, 140, at + 0.3, 0.7, "ring");
      r._pairs.push([pa, pb]);
      return an(h("div.frame.px", h("div.in.px", { style: { padding: "28px 36px" } },
        h("div.kick", kick),
        h("div.row", { style: { justifyContent: "space-between", marginTop: "22px" } }, pa, pb),
        h("div.h3", { style: { marginTop: "20px", fontSize: "44px" }, html: text }))), "up", at, 0.7);
    };
    r.append(h("div.scrim-mid"),
      h("div.abs.col", { style: { left: "56px", right: "56px", top: "200px", gap: "26px" } },
        ow ? panel(0.2, "ONE-WAY TRAFFIC", ow.from, ow.to,
          `${b(name(ow.from))} replied to ${b(name(ow.to))} ${ba(fmt(ow.n))} times. ${fmt(ow.back)} came back.`) : null,
        fan ? panel(1.2, "BIGGEST FAN", fan.from, fan.to,
          `${b(name(fan.from))} reacted to ${b(name(fan.to))}'s messages ${ba(fmt(fan.n))} times.`) : null,
        mate ? panel(2.2, "PROPER MATES", mate.a, mate.b,
          `${b(name(mate.a))} and ${b(name(mate.b))}: ${ba(fmt(mate.ab))} reactions one way, ${ba(fmt(mate.ba))} the other.`) : null),
      r._cv);
  },
  update(t, r) {
    const ctx = r._cv.getContext("2d");
    ctx.clearRect(0, 0, W, H);
    const pts = r._pts || (r._pts = r._pairs.map(([a, b]) => [posOf(a), posOf(b)]));
    const ow = SOC.one_way[0];
    const [p1, p2, p3] = pts;
    if (ow && p1) {
      flow(ctx, t, p1[0], p1[1], 30, { start: 0.6, speed: 0.8, curve: 40, size: 11, color: "#ff4f9e", seed: 2 });
      flow(ctx, t, p1[1], p1[0], Math.max(1, Math.round(30 * ow.back / ow.n)), { start: 0.6, speed: 0.8, curve: -40, size: 11, color: "#ffd1ea", seed: 5 });
    }
    if (p2) flow(ctx, t, p2[0], p2[1], 26, { start: 1.6, speed: 0.8, curve: 60, size: 13, colors: ["#ffc23d", "#ff4f9e", "#ffffff"], seed: 9 });
    if (p3) {
      flow(ctx, t, p3[0], p3[1], 18, { start: 2.6, speed: 0.8, curve: 50, size: 11, color: "#ff4f9e", seed: 11 });
      flow(ctx, t, p3[1], p3[0], 18, { start: 2.6, speed: 0.8, curve: -50, size: 11, color: "#ffd1ea", seed: 13 });
    }
  },
});

// Duo awards: the pairs, on the same cards as the awards.
const DUO_ICON = { argument: "argument", tag_team: "tag_team", night_shift: "night_owl", same_schedule: "same_schedule",
  ping_pong: "ping_pong", business: "coin", rivals: "rivals" };
if ((DATA.duo_awards || []).length) {
  scene({
    id: "duo-awards", ch: "people", dur: 5.2, boardGlow: 1.2, bloom: 0.9, pin: "diag",
    build(r) {
      const list = DATA.duo_awards.map(d => Object.assign({}, d, { icon: DUO_ICON[d.key] || "glue" }));
      r.append(h("div.scrim-mid"), block(190, {}, kicker("THE DUOS", 0.05)), cardGrid(list));
    },
  });
}

// The three people who all reply to each other most, as a triangle.
const TRIO = DATA.trio;
if (TRIO) {
  scene({
    id: "trio", ch: "people", dur: 6, boardGlow: 0.9, bloom: 1, pin: "radial",
    build(r) {
      r._cv = canvasLayer();
      r._spots = [[540, 760], [250, 1290], [830, 1290]];
      r._av = TRIO.people.map((u, k) => avatar(u, 230, 0.5 + k * 0.2, 0.8, "ring"));
      r.append(h("div.scrim-mid"), r._cv,
        block(230, {}, kicker("THE THREE MUSKETEERS"), words("ONE FOR ALL", "title", 0.2),
          line("The three people who all reply to each other the most", 0.5, "small")),
        ...TRIO.people.map((u, k) => {
          const [x, y] = r._spots[k];
          return an(h("div.abs.center", { style: { left: x - 160 + "px", top: y - 120 + "px", width: "320px" } },
            r._av[k], h("div.h3", { style: { marginTop: "16px" } }, name(u))), "pop", 0.4 + k * 0.2, 0.7);
        }),
        ...[[0, 1], [1, 2], [0, 2]].map(([a, bb], k) => {
          const [x1, y1] = r._spots[a], [x2, y2] = r._spots[bb];
          return an(h("div.abs.trilabel", { style: { left: (x1 + x2) / 2 - 90 + "px", top: (y1 + y2) / 2 - 40 + "px" } },
            h("div.h3.acc", fmt(TRIO.pairs[k])), h("div.tiny.white", "REPLIES")), "pop", 1.8 + k * 0.25, 0.6);
        }));
    },
    update(t, r) {
      const ctx = r._cv.getContext("2d");
      ctx.clearRect(0, 0, W, H);
      const pts = r._spots.map(([x, y]) => [x, y]);
      [[0, 1], [1, 2], [0, 2]].forEach(([a, bb], k) => {
        const p = E.outCubic(prog(t, 1.2 + k * 0.25, 0.8));
        if (p <= 0) return;
        const [x1, y1] = pts[a], [x2, y2] = pts[bb];
        ctx.save();
        ctx.strokeStyle = "#ff4f9e"; ctx.lineWidth = 12; ctx.shadowColor = "#ff4f9e"; ctx.shadowBlur = 30;
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(lerp(x1, x2, p), lerp(y1, y2, p)); ctx.stroke();
        ctx.restore();
        flow(ctx, t, pts[a], pts[bb], 10, { start: 2.0, speed: 0.6, curve: 0, spread: 18, size: 10, color: "#ffd1ea", seed: 20 + k });
      });
    },
  });
}

// The web, as a ring: the busiest repliers round the edge, and a chord for every pair who talk a lot,
// thicker the more they do. Then the three strongest pairs light up one at a time.
scene({
  id: "network", ch: "people", dur: 9, revealEnd: 7.4, boardGlow: 0.5, bloom: 0.8, pin: "radial",
  build(r) {
    const N = DATA.network.nodes, maxw = Math.max(...N.map(n => n.w));
    const cx = 540, cy = 1080, R = 330;
    r._cv = canvasLayer();
    r._pos = N.map((n, i) => {
      const a = -Math.PI / 2 + (i / N.length) * Math.PI * 2;
      return [cx + Math.cos(a) * R, cy + Math.sin(a) * R, a];
    });
    const nodes = N.map((n, i) => {
      const [x, y, a] = r._pos[i], size = Math.round(78 + 48 * Math.sqrt(n.w / maxw));
      const lx = cx + Math.cos(a) * (R + size / 2 + 50), ly = cy + Math.sin(a) * (R + size / 2 + 36);
      return [
        an(h("div.abs", { style: { left: x - size / 2 + "px", top: y - size / 2 + "px" } }, avatar(n.user, size, 0.3 + i * 0.05, 0.6, "ring")), "pop", 0.25 + i * 0.05, 0.6),
        an(h("div.abs.ringname", { style: { left: clamp(lx - 90, 24, W - 204) + "px", top: ly - 14 + "px" } }, name(n.user)), "fade", 0.6 + i * 0.05, 0.5),
      ];
    }).flat();
    r.append(h("div.scrim-mid"), r._cv, ...nodes,
      block(230, {}, kicker("THE WEB"), words("WHO TALKS TO WHO", "title", 0.2),
        line(`The ${N.length} people who reply the most. The thicker the line, the more two of them reply to each other.`, 0.5, "small")),
      r._cap = h("div.abs.center", { style: { left: "56px", right: "56px", top: "1580px", height: "150px" } }));
    r._edges = DATA.network.edges;
    r._caps = r._edges.slice(0, 3).map(([a, bb, n]) => h("div.ringcap", h("div.h2", `${name(N[a].user)} & ${name(N[bb].user)}`),
      h("div.h3.acc", `${fmt(n)} replies`)));
    r._caps.forEach(c => r._cap.append(c));
  },
  update(t, r) {
    const ctx = r._cv.getContext("2d");
    ctx.clearRect(0, 0, W, H);
    const E2 = r._edges, maxe = E2.length ? E2[0][2] : 1, cx = 540, cy = 1080;
    const focus = [0, 1, 2].findIndex(j => t >= 3.4 + j * 1.35 && t < 3.4 + (j + 1) * 1.35);
    E2.forEach(([a, bb, n], k) => {
      const p = E.outCubic(prog(t, 1.0 + k * 0.045, 0.6));
      if (p <= 0) return;
      const [x1, y1] = r._pos[a], [x2, y2] = r._pos[bb];
      const mx = lerp((x1 + x2) / 2, cx, 0.45), my = lerp((y1 + y2) / 2, cy, 0.45);
      const hot = k === focus;
      ctx.save();
      ctx.globalAlpha = (focus >= 0 && !hot ? 0.18 : 0.75) * p;
      ctx.strokeStyle = hot ? "#ffffff" : "#ff4f9e";
      ctx.lineWidth = 2 + 13 * Math.sqrt(n / maxe) + (hot ? 4 : 0);
      if (hot) { ctx.shadowColor = "#ff4f9e"; ctx.shadowBlur = 40; }
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      const ex = lerp(x1, x2, p), ey = lerp(y1, y2, p);
      ctx.quadraticCurveTo(lerp(x1, mx, p), lerp(y1, my, p), ex, ey);
      ctx.stroke();
      ctx.restore();
    });
    r._caps.forEach((c, j) => {
      const on = pulse(t, 3.4 + j * 1.35, 3.4 + (j + 1) * 1.35 - 0.25, 0.25) || (j === 2 && t >= 3.4 + 2 * 1.35 ? prog(t, 3.4 + 2 * 1.35, 0.25) : 0);
      c.style.opacity = on;
      c.style.transform = `translateY(${(1 - on) * 30}px)`;
    });
  },
});

// Friend groups, found in the reply network and named after what sets them apart.
if ((DATA.squads || []).length) {
  scene({
    id: "squads", ch: "people", dur: 6, boardGlow: 0.9, pin: "rows",
    build(r) {
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker("THE SQUADS"), words("FRIEND GROUPS", "title", 0.2),
          line("Groups who mostly reply to each other, found in the web and named after what sets them apart", 0.5, "small")),
        h("div.abs.col", { style: { left: "56px", right: "56px", top: "520px", gap: "20px" } },
          DATA.squads.map((sq, k) => an(h("div.frame.px", h("div.in.px", { style: { padding: "22px 28px" } },
            h("div.row", { style: { justifyContent: "space-between", alignItems: "baseline", gap: "20px" } },
              h("div.h3.acc", sq.name), h("div.tiny.white", { style: { whiteSpace: "nowrap" } }, `${sq.size} PEOPLE`)),
            h("div.small", { style: { marginTop: "2px" } }, `They ${sq.why}`),
            h("div.row.faces", { style: { marginTop: "14px" } }, sq.people.map((u, j) => avatar(u, 86, 0.7 + k * 0.3 + j * 0.05, 0.5, "ring"))))),
            "left", 0.5 + k * 0.3, 0.7))));
    },
  });
}

// Reaction duos: for each reaction, the pair who traded it most, counting only what went both ways.
const RD = (SOC.reaction_duos || []);
[RD.slice(0, 9), RD.slice(9, 18)].forEach((list, g) => {
  if (!list.length) return;
  scene({
    id: "reaction-duos-" + g, ch: "people", dur: 6, boardGlow: 0.9, pin: g ? "rain" : "cols",
    build(r) {
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker("REACTION DUOS"), words(g ? "AND THE REST" : "EMOJI BUDDIES", "title", 0.2),
          line("For each reaction, the two people who traded it most. It only counts if it went both ways.", 0.5, "small")),
        h("div.abs.col.listpanel", { style: { left: "56px", right: "56px", top: "560px", gap: "10px" } },
          list.map((d, k) => an(h("div.lrow", { style: { height: "118px" } },
            h("div.rdemo", emojiEl(d.e, d.id, 76)),
            avatar(d.a, 76, 0.8 + k * 0.08, 0.5), avatar(d.b, 76, 0.85 + k * 0.08, 0.5),
            h("div.small.white", { style: { flex: 1, lineHeight: "1.15" } }, `${name(d.a)} & ${name(d.b)}`),
            h("div.h3.acc", { style: { whiteSpace: "nowrap" } }, `${fmt(d.ab)} & ${fmt(d.ba)}`)), "left", 0.6 + k * 0.08, 0.5))));
    },
  });
});

// Shut: the reaction that times its author out.
const SH = DATA.shut;
if (SH && SH.total) {
  scene({
    id: "shut", ch: "people", dur: 6, boardGlow: 1, bloom: 1, pin: "radial",
    build(r) {
      const ms = SH.most_shut[0], top = SH.shutters[0], rv = SH.rivals;
      const card = (at, kick, uid, big, line2) => an(h("div.frame.px", h("div.in.px", { style: { padding: "24px 30px" } },
        h("div.kick", kick), h("div.lrow", { style: { marginTop: "16px" } }, avatar(uid, 110, at + 0.2, 0.6, "ring"),
          h("div", { style: { flex: 1, paddingRight: "190px" } }, h("div.h3", name(uid)), h("div.small", { html: line2 }))),
        h("div.big.acc", { style: { position: "absolute", right: "30px", top: "24px", fontSize: "84px" } }, big))), "left", at, 0.7);
      r.append(h("div.scrim-mid"),
        block(230, {}, h("div.row", { style: { gap: "26px" } }, an(h("div", emojiEl("Shut", SH.emoji_id, 120)), "spin", 0.1, 0.7),
          h("div", kicker("SHUT", 0.2), h("div.title", roll(SH.total, 0.3, 1.4), " SHUTS"))),
          line("Every :Shut: reaction this year. Each one times the author out.", 0.6, "small")),
        h("div.abs.col", { style: { left: "56px", right: "56px", top: "640px", gap: "22px" } },
          ms ? card(0.9, "MOST SHUT", ms.user, fmt(ms.n), `shut ${b(fmt(ms.n))} times this year, and timed out ${b(fmt(ms.timeouts))} times all told`) : null,
          top ? card(1.5, "TRIGGER HAPPY", top.user, fmt(top.n), `handed out ${b(fmt(top.n))} shuts`) : null,
          rv ? an(h("div.frame.px", h("div.in.px", { style: { padding: "24px 30px" } }, h("div.kick", "SHUT RIVALS"),
            h("div.lrow", { style: { marginTop: "16px" } }, avatar(rv.a, 96, 2.3, 0.6, "ring"), avatar(rv.b, 96, 2.4, 0.6, "ring"),
              h("div.small.white", { style: { flex: 1 } }, `${name(rv.a)} & ${name(rv.b)}`),
              h("div.h3.acc", { style: { whiteSpace: "nowrap" } }, `${rv.ab} & ${rv.ba}`)))), "left", 2.1, 0.7) : null),
        SH.coins ? block(1660, {}, html(`${b(fmt(SH.coins))} shutcoins bought from the shop, to shut people without a role`, 2.8, "small")) : null);
    },
  });
}

// The busiest ten minutes of the year in any one channel.
const BUSY = DATA.crowds && DATA.crowds.busiest;
if (BUSY) {
  scene({
    id: "busiest", ch: "people", dur: 6, boardGlow: 1, bloom: 1, pin: "radial",
    build(r) {
      const hr = +BUSY.time.slice(0, 2), mins = BUSY.time.slice(3);
      const when = `${dmyLong(BUSY.day)}, ${hr % 12 || 12}${mins === "00" ? "" : ":" + mins}${hr < 12 ? "am" : "pm"}`;
      r._crowd = h("div.crowdgrid", Array.from({ length: BUSY.people }, (_, k) =>
        an(h("div", crowdArt()[k % 5]), "pop", 0.9 + hash(k) * 1.2, 0.4)));
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker("THE BUSIEST TEN MINUTES"),
          h("div.mega.acc.glow", { style: { fontSize: "300px", marginTop: "20px" } }, roll(BUSY.people, 0.3, 1.6)),
          line(`different people talking in #${BUSY.channel} at once`, 0.8, "h3")),
        h("div.abs", { style: { left: "56px", right: "56px", top: "820px" } }, r._crowd),
        block(1420, {}, BUSY.caption ? an(h("div.h2", BUSY.caption), "up", 2.4, 0.7) : null,
          line(when, 2.7, "body")));
    },
  });
}

scene({
  id: "rookies", ch: "people", dur: 6.6, boardGlow: 0.9,
  build(r) {
    const [rk, ...rks] = DATA.rookies;
    r.append(h("div.scrim-mid"),
      block(230, {}, kicker("ROOKIE OF THE YEAR"), words("NEW FACES", "title", 0.2, 0.05)),
      block(450, {}, an(h("div.row", { style: { gap: "34px" } }, avatar(rk.user, 240, 0.5, 0.9, "ring"),
        h("div", h("div.h2", name(rk.user)), h("div.big.acc", { style: { fontSize: "120px", marginTop: "8px" } }, roll(rk.n, 0.8, 1.6)),
          h("div.small", `messages since their first ever, on ${dmyLong(rk.first)}`))), "left", 0.4, 0.8),
        h("div.row", { style: { gap: "22px", marginTop: "30px" } }, rks.map((x, k) =>
          an(h("div.lrow", { style: { flex: 1, minWidth: 0 } }, avatar(x.user, 70, 1.5 + k * 0.1, 0.5), h("div", { style: { minWidth: 0 } }, h("div.small.white", name(x.user)), h("div.tiny", `${fmt(x.n)} SINCE ${dmy(x.first)}`))), "up", 1.4 + k * 0.12, 0.6)))),
      block(1030, {}, kicker("GLOW-UPS", 2.2), h("div.col", { style: { gap: "20px", marginTop: "26px" } }, DATA.glowups.map((g, k) =>
        an(h("div.frame.px", h("div.in.px", { style: { padding: "22px 28px" } }, h("div.lrow",
          avatar(g.user, 92, 2.6 + k * 0.15, 0.6),
          h("div", { style: { flex: 1 } }, h("div.h3", name(g.user)),
            h("div.row", { style: { gap: "16px", marginTop: "8px" } }, h("div.small", fmt(g.prev)), h("div.small.acc", "→"), h("div.h3.acc", roll(g.n, 2.8 + k * 0.15, 1.3)))),
          h("div.chip.up", `x${(g.n / Math.max(g.prev, 1)).toFixed(1)}`)))), "left", 2.5 + k * 0.15, 0.7)))));
  },
});

// Hall of Fame: slides of three, each with its own title (the picks file sets them).
(DATA.hall_of_fame || []).forEach((slide, g) => {
  if (!slide.messages || !slide.messages.length) return;
  scene({
    id: "hof-" + g, ch: "people", dur: 6.2, boardGlow: 1.1, pin: ["diag", "rain", "cols", "rows"][g % 4],
    build(r) {
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker("HALL OF FAME"), words(slide.title, "title", 0.2)),
        h("div.abs.col", { style: { left: "56px", right: "56px", top: "520px", gap: "30px" } },
          slide.messages.map((m, k) => message({ user: m.user, text: m.text, when: dmyLong(m.day), reactions: m.reactions }, 0.4 + k * 0.6))));
    },
  });
});
