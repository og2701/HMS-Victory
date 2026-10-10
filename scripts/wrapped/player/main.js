// Boot: wait for fonts and every picture, build the world and every scene, then either play (in a
// browser) or wait to be stepped frame by frame (by render.mjs, through window.WRAP.seek).

const stage = document.getElementById("stage");
const fxCanvas = document.getElementById("fx");
fxCanvas.width = W; fxCanvas.height = H;
const fctx = fxCanvas.getContext("2d");
const CH_SPAN = {};

function buildHud() {
  const hud = document.getElementById("hud");
  const segs = CHAPTERS.filter(c => c.key !== "intro").map(c => h("div.seg", { data: { ch: c.key } }, h("i")));
  hud.append(h("div.bar", segs),
    h("div.meta", h("div.brand", h("b"), `UKPLACE WRAPPED ${String(YEAR).slice(2)}`), h("div.chap", "")));
  SCENES.forEach((s, i) => {
    const span = CH_SPAN[s.ch] || (CH_SPAN[s.ch] = [T.starts[i], 0]);
    span[1] = T.starts[i] + s.dur;
  });
}
function hud(time, s, ch) {
  const el = document.getElementById("hud");
  el.style.opacity = s.ch === "intro" ? 0 : 1;
  for (const seg of el.querySelectorAll(".seg")) {
    const span = CH_SPAN[seg.dataset.ch];
    const f = span ? clamp((time - span[0]) / (span[1] - span[0])) : 0;
    seg.firstChild.style.transform = `scaleX(${f})`;
    seg.firstChild.style.background = seg.dataset.ch === s.ch ? ch.accent : "var(--white)";
  }
  el.querySelector(".chap").textContent = ch.title;
}

function seek(time) {
  time = clamp(time, 0, T.total - 1e-4);
  const i = sceneAt(time), s = SCENES[i], t = time - T.starts[i];
  const ch = CHAPTERS.find(c => c.key === s.ch);
  if (i !== T.cur) {
    if (T.cur >= 0) SCENES[T.cur].root.classList.remove("on");
    s.root.classList.add("on");
    stage.style.setProperty("--acc", ch.accent);
    s.root.querySelectorAll(".pixav").forEach(el => { el._av.last = -1; });
    T.cur = i;
  }
  renderWorld(time, s, t, ch);
  s.root.style.transform = "";
  runAnims(s.anims, t);
  runRolls(s.root, t);
  runAvatars(s.root, t);
  runFlips(s.root, t);
  if (s.update) s.update(t, s.root);
  hud(time, s, ch);
  fctx.clearRect(0, 0, W, H);
  if (i > 0 && t < s.tin) drawTransition(fctx, 1 - E.outCubic(prog(t, 0, s.tin)), s.pin, ch.accent, true);
  const out = prog(t, s.dur - s.tout, s.tout);
  if (out > 0) drawTransition(fctx, E.inCubic(out), s.pout, ch.accent);
}

async function boot() {
  const ui = document.getElementById("ui");
  await Promise.all([document.fonts.load("900 100px Archivo"), document.fonts.load("20px Pixel")]);
  await document.fonts.ready;
  await Promise.all(Object.keys(PEOPLE).map(uid => loadImage(avatarUrl(uid))));
  initWorld();
  buildAtlas([...new Set([...DATA.network.nodes.map(n => n.user), ...DATA.wall])]);
  buildPieces();
  layoutTimeline();
  for (const s of SCENES) {
    const r = h("section.scene", { data: { id: s.id } });
    // Scenes pass optional parts as null; Element.append would print those as the text "null".
    const append = r.append.bind(r);
    r.append = (...kids) => append(...kids.flat().filter(k => k != null && k !== false));
    s.root = r;
    ui.append(r);
    s.build(r);
    s.anims = collectAnims(r);
  }
  buildHud();
  await Promise.all([...ui.querySelectorAll("img")].map(img => (img.complete ? null : new Promise(ok => { img.onload = img.onerror = ok; }))));
  // Compile every piece's shaders once so the first frame of each scene isn't the slow one.
  for (const p of Object.values(WORLD.pieces)) p.group.visible = true;
  WORLD.r.compile(WORLD.scene, WORLD.cam);
  seek(0);
  window.WRAP = { ready: true, total: T.total, fps: FPS, seek,
    scenes: SCENES.map((s, i) => ({ id: s.id, ch: s.ch, start: T.starts[i], dur: s.dur })) };
  if (!navigator.webdriver || /play/.test(location.hash)) preview();
}

// In a browser: fit the stage to the window and play. Space pauses, arrows skip scenes, and the URL
// hash can start anywhere: #t=42.5 or #scene=skyline.
function preview() {
  document.body.classList.add("preview");
  const fit = () => { stage.style.transform = `translate(-50%, -50%) scale(${Math.min(innerWidth / W, innerHeight / H)})`; };
  fit();
  addEventListener("resize", fit);
  let at = 0, playing = true, last = performance.now();
  const m = /t=([\d.]+)/.exec(location.hash), sc = /scene=([\w-]+)/.exec(location.hash);
  if (m) at = +m[1];
  if (sc) { const k = SCENES.findIndex(s => s.id === sc[1]); if (k >= 0) at = T.starts[k]; }
  if (/pause/.test(location.hash)) playing = false;
  addEventListener("keydown", e => {
    if (e.code === "Space") playing = !playing;
    if (e.code === "ArrowRight") at = T.starts[Math.min(sceneAt(at) + 1, SCENES.length - 1)];
    if (e.code === "ArrowLeft") at = T.starts[Math.max(sceneAt(at) - 1, 0)];
  });
  window.WRAP.goto = x => { at = x; seek(at); };
  window.WRAP.pause = () => { playing = false; };
  const tick = now => {
    if (playing) at = (at + (now - last) / 1000) % T.total;
    last = now;
    seek(at);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

boot();
