// Render the season replay page to an MP4, frame-exact, with the narration and the backing music.
//
//   PLAYWRIGHT_CORE=/path/to/node_modules/playwright-core/index.mjs \
//     node scripts/bb_chronicle/render_mp4.mjs http://localhost:5191/big_brother_chronicle.html out.mp4 voice/
//
// Recording the screen in real time drops frames and drifts from the audio, so instead each worker
// opens the page in headless Chrome, takes the player's clock away from requestAnimationFrame and
// steps it 40ms at a time, pinning every CSS and Web Animation to the same clock before each
// screenshot. Frames go straight into ffmpeg. While stepping, the page logs when each voice clip
// starts and when the music cuts for a punchline; the music is then rendered offline in the page
// on the same timeline, and ffmpeg lays the voice clips over it at those moments.
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const [, , pageUrl, outFile, voiceDir] = process.argv;
if (!pageUrl || !outFile || !voiceDir) {
  console.error("usage: render_mp4.mjs <page url> <out.mp4> <voice dir>");
  process.exit(1);
}
const FPS = 25, STEP = 1000 / FPS, W = 1280, H = 720, SCALE = 1.5;
const WORKERS = +(process.env.WORKERS || 6);
const PACE = 1.15; // the player speeds the narrator up by this much; keep in step with Voice.pace
const work = fs.mkdtempSync(path.join(path.dirname(path.resolve(outFile)), ".render-"));
const { chromium } = await import(process.env.PLAYWRIGHT_CORE || "playwright-core");
const browser = await chromium.launch({
  executablePath: process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
  args: ["--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"],
});

function run(cmd, args, opts = {}) {
  return new Promise((ok, fail) => {
    const p = spawn(cmd, args, { stdio: ["ignore", "ignore", "pipe"], ...opts });
    let err = "";
    p.stderr.on("data", d => { err += d; if (err.length > 20000) err = err.slice(-20000); });
    p.on("close", code => code === 0 ? ok() : fail(new Error(`${cmd} exited ${code}\n${err.slice(-2000)}`)));
  });
}

async function openPage() {
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: SCALE });
  const page = await ctx.newPage();
  await page.goto(pageUrl, { waitUntil: "load" });
  await page.evaluate(() => document.fonts.ready);
  // The stage alone, filling the frame.
  await page.addStyleTag({ content: `.mast,.controls,.scrub,.chapters,.explorer,.bigplay{display:none!important}
    body{background:#000}.stage-wrap{max-width:none;padding:0}
    .stage{border-radius:0;width:${W}px;height:${H}px;aspect-ratio:auto;box-shadow:none}` });
  await page.evaluate(() => {
    const B = window.BBPlayer;
    window.__log = []; window.__vt = 0; window.__cur = -1; window.__seen = new WeakMap();
    // Sound is put together afterwards, so the page only notes when it would have played.
    B.Voice.play = key => { if (key) window.__log.push({ vt: window.__vt, voice: key }); };
    B.Voice.stop = B.Voice.pause = B.Voice.resume = () => {};
    B.Music.cut = ms => window.__log.push({ vt: window.__vt, cut: ms });
    B.Music.start = B.Music.pause = B.Music.level = () => {};
    B.P.playing = false;
    document.getElementById("stage").classList.remove("paused");
    window.__frame = (vt, timingOnly) => {
      window.__vt = vt;
      const P = B.P;
      let i = P.starts.findIndex((s, k) => vt >= s && vt < s + P.plans[k].dur);
      if (i < 0) i = P.list.length - 1;
      if (i !== window.__cur) { window.__cur = i; B.go(i); }
      B.advance(Math.min(vt - P.starts[i], P.plans[i].dur));
      if (timingOnly) return;
      // Every animation runs on the replay's clock, not the wall clock.
      for (const a of document.getAnimations()) {
        if (!window.__seen.has(a)) window.__seen.set(a, vt);
        a.pause();
        a.currentTime = vt - window.__seen.get(a);
      }
    };
  });
  return page;
}

// ---- split the timeline into segments at scene boundaries, one per worker
const probe = await openPage();
const timeline = await probe.evaluate(() => ({ starts: window.BBPlayer.P.starts, total: window.BBPlayer.P.total }));
if (+process.env.LIMIT_MS) timeline.total = Math.min(timeline.total, +process.env.LIMIT_MS); // for a quick test render
const frames = Math.ceil(timeline.total / STEP);
const cuts = [0];
for (let k = 1; k < WORKERS; k++) {
  const want = timeline.total * k / WORKERS;
  const at = timeline.starts.reduce((best, s) => Math.abs(s - want) < Math.abs(best - want) ? s : best, 0);
  const f = Math.round(at / STEP);
  if (f > cuts[cuts.length - 1]) cuts.push(f);
}
cuts.push(frames);
console.log(`${(timeline.total / 60000).toFixed(2)} min, ${frames} frames, ${cuts.length - 1} segments`);

async function renderSegment(n, f0, f1) {
  const page = n === 0 ? probe : await openPage();
  const file = path.join(work, `seg${String(n).padStart(2, "0")}.mp4`);
  const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", String(FPS), file], { stdio: ["pipe", "ignore", "inherit"] });
  const done = new Promise((ok, fail) => ff.on("close", c => c === 0 ? ok() : fail(new Error("ffmpeg segment " + n))));
  for (let f = f0; f < f1; f++) {
    await page.evaluate(vt => window.__frame(vt), f * STEP);
    const jpg = await page.screenshot({ type: "jpeg", quality: 88, clip: { x: 0, y: 0, width: W, height: H } });
    if (!ff.stdin.write(jpg)) await new Promise(r => ff.stdin.once("drain", r));
    if ((f - f0) % 500 === 0) console.log(`segment ${n}: ${f - f0}/${f1 - f0}`);
  }
  ff.stdin.end();
  await done;
  return { file, log: await page.evaluate(() => window.__log) };
}

let log, video;
if (process.env.REUSE_VIDEO) {
  // The frames are already rendered; step the clock again without screenshots just to time the sound.
  video = process.env.REUSE_VIDEO;
  for (let f = 0; f < frames; f += 200) {
    await probe.evaluate(([a, b, step]) => { for (let k = a; k < b; k++) window.__frame(k * step, true); }, [f, Math.min(f + 200, frames), STEP]);
  }
  log = await probe.evaluate(() => window.__log);
} else {
  const segments = await Promise.all(cuts.slice(0, -1).map((f0, n) => renderSegment(n, f0, cuts[n + 1])));
  log = segments.flatMap(s => s.log);
  // ---- video: join the segments
  const list = path.join(work, "segments.txt");
  fs.writeFileSync(list, segments.map(s => `file '${s.file}'`).join("\n"));
  video = path.join(work, "video.mp4");
  await run("ffmpeg", ["-y", "-f", "concat", "-safe", "0", "-i", list, "-c", "copy", video]);
}
log.sort((a, b) => a.vt - b.vt);
console.log(`timeline logged: ${log.filter(e => e.voice).length} voice cues, ${log.filter(e => e.cut).length} punchline cuts`);

// ---- music: rendered offline in the page, ducked under the voice and cut for punchlines
const manifest = JSON.parse(fs.readFileSync(path.join(voiceDir, "voice.json"), "utf8"));
const voices = log.filter(e => e.voice && manifest[e.voice]);
const SR = 32000;
const samples = await probe.evaluate(async ({ total, voices, cuts, SR, PACE }) => {
  const { P, MOODS, CH, ARP, moodFor } = window.BBPlayer;
  const secs = total / 1000 + 2, c = new OfflineAudioContext(1, Math.ceil(secs * SR), SR);
  const out = c.createGain(); out.gain.value = 0; out.connect(c.destination);
  const lp = c.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 2200; lp.connect(out);
  const dl = c.createDelay(1); dl.delayTime.value = .45; const fb = c.createGain(); fb.gain.value = .3;
  dl.connect(fb); fb.connect(dl); dl.connect(out);
  const note = (freq, t, dur, type, vol, dests, attack = .01, detune = 0) => {
    const o = c.createOscillator(), g = c.createGain(); o.type = type; o.frequency.value = freq; o.detune.value = detune;
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(vol, t + attack); g.gain.exponentialRampToValueAtTime(.0001, t + dur);
    o.connect(g); [].concat(dests).forEach(d => g.connect(d)); o.start(t); o.stop(t + dur + .05);
  };
  const tick = c.createBuffer(1, Math.ceil(SR * .03), SR), td = tick.getChannelData(0);
  for (let i = 0; i < td.length; i++) td[i] = (Math.random() * 2 - 1) * (1 - i / td.length);
  const moodAt = t => { const ms = t * 1000; let i = P.starts.findIndex((s, k) => ms >= s && ms < s + P.plans[k].dur); if (i < 0) i = P.list.length - 1; return moodFor(P.list[i]); };
  const eighth = 60 / 100 / 2;
  let mood = "house", st = 0, t = .1;
  // Notes are scheduled two seconds at a time from suspend points. Scheduling the whole twenty minutes
  // up front leaves thousands of not-yet-started nodes in the graph, and Chrome walks every one of them
  // on every 128-sample block, which turns a seconds-long render into a very long one.
  const schedule = until => { for (; t < until && t < secs - 1; st++, t += eighth) {
    if (st % 8 === 0) mood = moodAt(t);
    const m = MOODS[mood], chord = CH[m.prog[Math.floor(st / 8) % 4]];
    if (st % 8 === 0) chord.forEach(f => [-7, 7].forEach(d => note(f, t, eighth * 8.5, "sawtooth", .035, lp, .6, d)));
    if (st % 4 === 0) note(chord[0] / 2, t, eighth * 3, "sine", .5, out);
    if (m.arp) note(chord[ARP[st % 8]] * 2, t, .22, "triangle", .16, [lp, dl]);
    if (m.tick) { const n = c.createBufferSource(), hp = c.createBiquadFilter(), g = c.createGain();
      n.buffer = tick; hp.type = "highpass"; hp.frequency.value = 7000; g.gain.value = st % 2 ? .05 : .12;
      n.connect(hp); hp.connect(g); g.connect(out); n.start(t); }
  } };
  schedule(2.5);
  const q = 128 / SR;
  for (let k = 2; k < secs - 1; k += 2) {
    const at = Math.round((k - .5) / q) * q;
    c.suspend(at).then(() => { schedule(k + 2.5); c.resume(); });
  }
  // The same levels the live player uses: .075 normally, .03 under the narrator, silence for a punchline.
  const ducks = voices.map(v => [v.vt / 1000, v.vt / 1000 + v.seconds / PACE]);
  const drops = cuts.map(e => [e.vt / 1000, e.vt / 1000 + e.cut / 1000]);
  const edges = [...new Set([0, ...ducks.flat(), ...drops.flat()])].sort((a, b) => a - b);
  for (const t of edges) {
    const cut = drops.some(([a, b]) => t >= a && t < b), duck = ducks.some(([a, b]) => t >= a && t < b);
    out.gain.setTargetAtTime(cut ? 0 : duck ? .03 : .075, t, cut ? .015 : .3);
  }
  const buf = await c.startRendering(), d = buf.getChannelData(0);
  const pcm = new Int16Array(d.length);
  for (let i = 0; i < d.length; i++) pcm[i] = Math.max(-1, Math.min(1, d[i])) * 32767;
  window.__pcm = pcm;
  return pcm.length;
}, { total: timeline.total, voices: voices.map(v => ({ vt: v.vt, seconds: manifest[v.voice].seconds })), cuts: log.filter(e => e.cut), SR, PACE });
const raw = path.join(work, "music.s16");
const fd = fs.openSync(raw, "w");
for (let i = 0; i < samples; i += 2_000_000) {
  const b64 = await probe.evaluate(([a, b]) => {
    const part = new Uint8Array(window.__pcm.buffer, a * 2, (Math.min(b, window.__pcm.length) - a) * 2);
    let s = ""; for (let i = 0; i < part.length; i += 0x8000) s += String.fromCharCode.apply(null, part.subarray(i, i + 0x8000));
    return btoa(s);
  }, [i, i + 2_000_000]);
  fs.writeSync(fd, Buffer.from(b64, "base64"));
}
fs.closeSync(fd);
await browser.close();

// ---- narration: each clip sped up like the player does, dropped in where it played
const inputs = ["-f", "s16le", "-ar", String(SR), "-ac", "1", "-i", raw];
const chains = [];
voices.forEach((v, k) => {
  inputs.push("-i", path.join(voiceDir, manifest[v.voice].file));
  const ms = Math.round(v.vt);
  chains.push(`[${k + 1}:a]aresample=44100,atempo=${PACE},adelay=${ms}:all=1[v${k}]`);
});
const mix = `${chains.join(";")};[0:a]aresample=44100[m];[m]${voices.map((_, k) => `[v${k}]`).join("")}amix=inputs=${voices.length + 1}:normalize=0:dropout_transition=0[a]`;
const audio = path.join(work, "audio.m4a");
await run("ffmpeg", ["-y", ...inputs, "-filter_complex", mix, "-map", "[a]", "-c:a", "aac", "-b:a", "160k", "-t", String(timeline.total / 1000), audio]);

// ---- together
await run("ffmpeg", ["-y", "-i", video, "-i", audio, "-c:v", "copy", "-c:a", "copy", "-shortest", "-movflags", "+faststart", outFile]);
fs.rmSync(work, { recursive: true, force: true });
console.log(`wrote ${outFile} (${(fs.statSync(outFile).size / 1e6).toFixed(0)} MB): ${voices.length} narration clips, ${log.filter(e => e.cut).length} punchline cuts`);
