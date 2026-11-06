// Render the Wrapped page to an MP4, frame-exact, or dump stills of chosen moments for a look.
//
//   PLAYWRIGHT_CORE=/path/to/node_modules/playwright-core/index.mjs \
//     node scripts/wrapped/render.mjs <page url> <out.mp4>
//   node scripts/wrapped/render.mjs <page url> --stills <out dir> 12.5 40 61.2 ...   (or scene ids)
//   node scripts/wrapped/render.mjs <personal page url> --personal <out dir> <soundtrack.m4a> [ids... | all]
//
// Recording a screen in real time drops frames. Instead each worker opens the page in headless Chrome
// (with the GPU, for the 3D) and steps window.WRAP.seek one frame at a time, screenshotting each into
// ffmpeg. Workers take whole scenes, so every segment starts on a cut, and the segments are joined
// losslessly at the end. Silent: sound is added separately, if at all.
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const args = process.argv.slice(2);
const url = args[0];
if (!url || args.length < 2) {
  console.error("usage: render.mjs <page url> <out.mp4> | --stills <dir> <time|scene>...");
  process.exit(1);
}
const W = 1080, H = 1920;
const WORKERS = +(process.env.WORKERS || 6);
const { chromium } = await import(process.env.PLAYWRIGHT_CORE || "playwright-core");
const browser = await chromium.launch({
  executablePath: process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
  args: ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist", "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"],
});

async function openPage() {
  const page = await (await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1 })).newPage();
  page.on("pageerror", e => console.error("page error:", e.message));
  await page.goto(url, { waitUntil: "load" });
  await page.waitForFunction(() => window.WRAP && window.WRAP.ready, null, { timeout: 180000 });
  return page;
}
const shoot = (page, t) => page.evaluate(x => window.WRAP.seek(x), t)
  .then(() => page.screenshot({ type: "jpeg", quality: 92, clip: { x: 0, y: 0, width: W, height: H } }));

function run(cmd, argv) {
  return new Promise((ok, fail) => {
    const p = spawn(cmd, argv, { stdio: ["ignore", "ignore", "pipe"] });
    let err = "";
    p.stderr.on("data", d => { err = (err + d).slice(-4000); });
    p.on("close", code => (code === 0 ? ok() : fail(new Error(`${cmd} exited ${code}\n${err}`))));
  });
}

const probe = await openPage();
const info = await probe.evaluate(() => ({ total: window.WRAP.total, fps: window.WRAP.fps, scenes: window.WRAP.scenes }));

if (args[1] === "--timeline") {
  // Scene ids and times, for the soundtrack mixer.
  fs.writeFileSync(args[2], JSON.stringify({ total: info.total, scenes: info.scenes.map(({ id, ch, start, dur }) => ({ id, ch, start, dur })) }));
  console.log(`wrote ${args[2]}: ${info.scenes.length} scenes, ${info.total.toFixed(1)}s`);
  await browser.close();
  process.exit(0);
}

if (args[1] === "--qa") {
  // Every scene, at the moment it's fully revealed: text running off the screen, text cut off by its
  // panel, and text sitting on top of other text. Prints the scene timings too.
  for (const s of info.scenes) {
    await probe.evaluate(x => window.WRAP.seek(x), s.start + s.dur - 0.45);
    const found = await probe.evaluate(() => {
      const root = document.querySelector(".scene.on"), out = [];
      const skip = ".label3d, .doclines, .dstrip, .stamp, .flash";
      const boxes = [];
      for (const el of root.querySelectorAll("*")) {
        if (el.closest(skip) && !el.classList.contains("rollw")) continue;
        const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) || el.classList.contains("rollw");
        if (!own) continue;
        let seen = 1;
        for (let e = el; e && e !== root; e = e.parentElement) seen *= +getComputedStyle(e).opacity;
        if (seen < 0.05) continue;
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) continue;
        const label = (el.className || el.tagName) + ": " + el.textContent.trim().slice(0, 40);
        if (r.right > 1080 - 16 || r.left < 16 || r.bottom > 1920 - 8 || r.top < 0) out.push("off screen  " + label);
        const panel = el.closest(".in");
        if (panel) {
          const p = panel.getBoundingClientRect();
          if (r.right > p.right + 1 || r.bottom > p.bottom + 1) out.push("cut off     " + label);
        }
        if (!el.closest(".rollw") || el.classList.contains("rollw")) boxes.push({ el, r, label });
      }
      for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i], b = boxes[j];
        if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
        const w = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
        const hh = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
        if (w > 6 && hh > 6 && w * hh > 0.25 * Math.min(a.r.width * a.r.height, b.r.width * b.r.height)) out.push(`overlap     ${a.label}  <>  ${b.label}`);
      }
      return out;
    });
    const tm = s.timing || {};
    console.log(`${s.id.padEnd(18)} ${s.dur.toFixed(1).padStart(5)}s  arrives ${String(tm.end).padStart(5)}  words ${String(tm.words).padStart(3)}  read ${tm.read}s`);
    for (const f of found) console.log("    " + f);
  }
  console.log(`total ${Math.floor(info.total / 60)}:${String(Math.floor(info.total % 60)).padStart(2, "0")}`);
  await browser.close();
  process.exit(0);
}

if (args[1] === "--stills") {
  const dir = args[2];
  fs.mkdirSync(dir, { recursive: true });
  for (const spec of args.slice(3)) {
    // "skyline" is the middle of that scene, "skyline+2.5" is 2.5s into it, a number is an absolute time
    const m = /^([a-z][\w-]*)(?:\+([\d.]+))?$/.exec(spec);
    let t = +spec;
    if (m) {
      const s = info.scenes.find(s => s.id === m[1]);
      if (!s) { console.error("no scene", m[1]); continue; }
      t = s.start + (m[2] != null ? +m[2] : s.dur / 2);
    }
    const file = path.join(dir, `${spec.replace(/[^\w.+-]/g, "_")}.jpg`);
    fs.writeFileSync(file, await shoot(probe, t));
    console.log(file);
  }
  await browser.close();
  process.exit(0);
}

if (args[1] === "--personal") {
  // Everyone's own Wrapped: their card (a 1080x1350 picture) and, if they have enough messages for one,
  // their video (720x1280, with the shared soundtrack under it). Files already made are skipped and
  // each lands under its final name only when complete, so a long run can stop and carry on.
  const [dir, bed, ...want] = args.slice(2);
  fs.mkdirSync(dir, { recursive: true });
  const ids = await probe.evaluate(() => window.PERSONAL_IDS);
  const pick = want.length && want[0] !== "all" ? ids.filter(([id]) => want.includes(id)) : ids;
  const jobs = pick.flatMap(([id, video]) => [[id, "card"], ...(video ? [[id, "video"]] : [])])
    .filter(([id, kind]) => !fs.existsSync(path.join(dir, `${id}.${kind === "card" ? "jpg" : "mp4"}`)));
  const at = (id, card) => `${url}${url.includes("?") ? "&" : "?"}u=${id}${card ? "&card=1" : ""}`;
  const open = async u => {
    const page = await (await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1 })).newPage();
    page.on("pageerror", e => console.error("page error:", e.message));
    await page.goto(u, { waitUntil: "load" });
    await page.waitForFunction(() => window.WRAP && window.WRAP.ready, null, { timeout: 180000 });
    return page;
  };
  console.log(`${pick.length} people: ${jobs.length} files to make`);
  let next = 0, done = 0;
  const started = Date.now();
  const worker = async () => {
    while (next < jobs.length) {
      const [id, kind] = jobs[next++];
      let page = await open(at(id, kind === "card"));
      if (kind === "card") {
        await page.evaluate(() => window.WRAP.seek(0.5));
        const jpg = await page.screenshot({ type: "jpeg", quality: 92, clip: { x: 0, y: 0, width: 1080, height: 1350 } });
        fs.writeFileSync(path.join(dir, `${id}.jpg`), jpg);
      } else {
        const total = await page.evaluate(() => window.WRAP.total), fps = info.fps;
        const part = path.join(dir, `${id}.part.mp4`);
        const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(fps), "-c:v", "mjpeg", "-i", "-",
          "-i", bed, "-map", "0:v", "-map", "1:a", "-vf", "scale=720:1280:flags=lanczos", "-c:v", "libx264", "-preset", "medium",
          "-crf", "23", "-pix_fmt", "yuv420p", "-r", String(fps), "-c:a", "copy", "-shortest", "-movflags", "+faststart", part],
          { stdio: ["pipe", "ignore", "inherit"] });
        const finished = new Promise((ok, fail) => ff.on("close", c => (c === 0 ? ok() : fail(new Error("ffmpeg " + id)))));
        for (let f = 0; f < Math.ceil(total * fps); f++) {
          let jpg;
          try {
            jpg = await shoot(page, f / fps);
          } catch (e) {
            console.error(`${id}: frame ${f} failed (${e.message.split("\n")[0]}), reopening`);
            await page.context().close().catch(() => {});
            page = await open(at(id, false));
            jpg = await shoot(page, f / fps);
          }
          if (!ff.stdin.write(jpg)) await new Promise(r => ff.stdin.once("drain", r));
        }
        ff.stdin.end();
        await finished;
        fs.renameSync(part, path.join(dir, `${id}.mp4`));
      }
      await page.context().close();
      done++;
      if (done % 10 === 0 || done === jobs.length) {
        const each = (Date.now() - started) / 1000 / done;
        console.log(`${done}/${jobs.length} files, about ${Math.round((each * (jobs.length - done)) / 60)} min to go`);
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(WORKERS, jobs.length) }, worker));
  await browser.close();
  process.exit(0);
}

const out = args[1];
const FPS = info.fps, frames = Math.ceil(info.total * FPS);
const limit = +process.env.LIMIT_S ? Math.min(frames, Math.ceil(+process.env.LIMIT_S * FPS)) : frames;
const cuts = [0];
for (let k = 1; k < WORKERS; k++) {
  const want = (limit / FPS) * k / WORKERS;
  const at = info.scenes.reduce((best, s) => (Math.abs(s.start - want) < Math.abs(best - want) ? s.start : best), 0);
  const f = Math.round(at * FPS);
  if (f > cuts[cuts.length - 1] && f < limit) cuts.push(f);
}
cuts.push(limit);
console.log(`${(limit / FPS).toFixed(1)}s, ${limit} frames at ${FPS}fps, ${cuts.length - 1} workers`);
const work = fs.mkdtempSync(path.join(path.dirname(path.resolve(out)), ".render-"));
const started = Date.now();

async function segment(n, f0, f1) {
  let page = n === 0 ? probe : await openPage();
  const file = path.join(work, `seg${String(n).padStart(2, "0")}.mp4`);
  const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
    "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-r", String(FPS), file], { stdio: ["pipe", "ignore", "inherit"] });
  const done = new Promise((ok, fail) => ff.on("close", c => (c === 0 ? ok() : fail(new Error("ffmpeg segment " + n)))));
  for (let f = f0; f < f1; f++) {
    let jpg;
    try {
      jpg = await shoot(page, f / FPS);
    } catch (e) {
      // A page can crash under several GPU-heavy workers; start a fresh one and carry on from this frame.
      console.error(`worker ${n}: frame ${f} failed (${e.message.split("\n")[0]}), reopening`);
      page = await openPage();
      jpg = await shoot(page, f / FPS);
    }
    if (!ff.stdin.write(jpg)) await new Promise(r => ff.stdin.once("drain", r));
    if ((f - f0) % 250 === 0) console.log(`worker ${n}: ${f - f0}/${f1 - f0}`);
  }
  ff.stdin.end();
  await done;
  return file;
}

const files = await Promise.all(cuts.slice(0, -1).map((f0, n) => segment(n, f0, cuts[n + 1])));
await browser.close();
const list = path.join(work, "segments.txt");
fs.writeFileSync(list, files.map(f => `file '${f}'`).join("\n"));
await run("ffmpeg", ["-y", "-f", "concat", "-safe", "0", "-i", list, "-c", "copy", "-movflags", "+faststart", out]);
fs.rmSync(work, { recursive: true, force: true });
console.log(`wrote ${out} (${(fs.statSync(out).size / 1e6).toFixed(1)} MB) in ${((Date.now() - started) / 1000).toFixed(0)}s`);
