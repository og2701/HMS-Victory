// The 3D world behind every scene: a living r/place-style pixel board stretching to the horizon, and
// the hero pieces that rise out of it (a voxel Union Jack, the message tower, the calendar skyline, the
// 24-hour ring, the reply constellation, casino chips, coin stacks, and the avatar mosaic).
//
// Pieces are built once up front and animated as pure functions of scene time; the renderer draws one
// frame per seek. Bloom makes anything bright glow.

const WORLD = { pieces: {} };
const INK = 0x04050a;
const PLACE = ["#6d001a", "#be0039", "#ff4500", "#ffa800", "#ffd635", "#fff8b8", "#00a368", "#00cc78", "#7eed56",
  "#00756f", "#009eaa", "#00ccc0", "#2450a4", "#3690ea", "#51e9f4", "#493ac1", "#6a5cff", "#94b3ff", "#811e9f",
  "#b44ac0", "#e4abff", "#de107f", "#ff3881", "#ff99aa", "#6d482f", "#9c6926", "#ffb470", "#000000", "#515252",
  "#898d90", "#d4d7d9", "#ffffff"];
const UJ = { blue: "#0a1f78", white: "#f4f6ff", red: "#e0102e" };

function initWorld() {
  const canvas = document.getElementById("gl");
  const r = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true, powerPreference: "high-performance" });
  r.setPixelRatio(1);
  r.setSize(W, H, false);
  r.toneMapping = THREE.ACESFilmicToneMapping;
  r.toneMappingExposure = 1.0;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(INK);
  scene.fog = new THREE.FogExp2(INK, 0.0026);
  const cam = new THREE.PerspectiveCamera(42, W / H, 0.5, 5000);
  scene.add(new THREE.HemisphereLight(0xa8b8ff, 0x200a18, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 2.4);
  key.position.set(140, 300, 220);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xff3355, 1.5);
  rim.position.set(-260, 90, -200);
  scene.add(rim);
  const composer = new EffectComposer(r);
  composer.addPass(new RenderPass(scene, cam));
  const bloom = new UnrealBloomPass(new THREE.Vector2(W, H), 0.8, 0.6, 0.55);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());
  Object.assign(WORLD, { r, scene, cam, composer, bloom });
  WORLD.board = makeBoard();
  scene.add(WORLD.board.mesh);
}

// ---------------------------------------------------------------- the board
const BOARD_VS = `
varying vec2 vUv; varying vec3 vW;
void main(){ vUv = uv; vec4 w = modelMatrix * vec4(position, 1.); vW = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }`;
const BOARD_FS = `
uniform sampler2D map; uniform float uTime, uN, uGlow, uArt; uniform vec3 uAccent, uFog, uCam;
varying vec2 vUv; varying vec3 vW;
float h21(vec2 p){ p = fract(p * vec2(233.34, 851.73)); p += dot(p, p + 23.45); return fract(p.x * p.y); }
vec3 place(float k){
  k = floor(k * 10.);
  if (k < 1.) return vec3(1., .27, 0.); if (k < 2.) return vec3(1., .84, .21); if (k < 3.) return vec3(0., .8, .47);
  if (k < 4.) return vec3(.21, .56, .92); if (k < 5.) return vec3(.32, .91, .96); if (k < 6.) return vec3(.42, .36, 1.);
  if (k < 7.) return vec3(.71, .29, .75); if (k < 8.) return vec3(1., .22, .51); if (k < 9.) return vec3(1., 1., 1.);
  return vec3(.85, .05, .2);
}
void main(){
  vec2 g = vUv * uN; vec2 cell = floor(g); vec2 f = fract(g);
  float ins = .1;
  float sq = step(ins, f.x) * step(ins, f.y) * step(f.x, 1. - ins) * step(f.y, 1. - ins);
  vec4 art = texture2D(map, (cell + .5) / uN);
  vec3 col = vec3(.010, .012, .024) + vec3(.008, .009, .02) * h21(cell);
  col = mix(col, art.rgb * .3, art.a * uArt);
  float r = h21(cell + 3.7);
  float cyc = uTime * .32 + r * 9.;
  float id = floor(cyc); float ph = fract(cyc);
  float on = step(.986, h21(cell + id * 1.618));
  vec3 pc = mix(place(h21(cell * 1.3 + id)), uAccent, step(.5, h21(cell + id * 7.1)));
  col += pc * on * (1. - smoothstep(0., .85, ph)) * 2.4 * uGlow;
  col *= mix(.18, 1., sq);
  float d = length(vW - uCam);
  col = mix(col, uFog, 1. - exp(-d * .0021));
  gl_FragColor = vec4(col, 1.);
}`;

function makeBoard() {
  const N = 400, SIZE = 2400;
  const cv = document.createElement("canvas");
  cv.width = cv.height = N;
  paintBoard(cv.getContext("2d"), N);
  const tex = new THREE.CanvasTexture(cv);
  tex.magFilter = tex.minFilter = THREE.NearestFilter;
  tex.generateMipmaps = false;
  const mat = new THREE.ShaderMaterial({
    uniforms: {
      map: { value: tex }, uTime: { value: 0 }, uN: { value: N }, uGlow: { value: 1 }, uArt: { value: 1 },
      uAccent: { value: new THREE.Color("#38d9ff") }, uFog: { value: new THREE.Color(INK) }, uCam: { value: new THREE.Vector3() },
    },
    vertexShader: BOARD_VS, fragmentShader: BOARD_FS,
  });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(SIZE, SIZE), mat);
  mesh.rotation.x = -Math.PI / 2;
  return { mesh, mat, N, SIZE, cell: SIZE / N };
}

// Community "artworks" scattered over the board, the way r/place fills up: flags, hearts, blocks of
// colour, a big Union Jack in the middle. Seeded, so it's the same board every render.
function paintBoard(ctx, N) {
  ctx.clearRect(0, 0, N, N);
  let seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const c = (x, y, w, hgt, col, a = 1) => { ctx.globalAlpha = a; ctx.fillStyle = col; ctx.fillRect(x, y, w, hgt); };
  const flag = (x, y, w, hgt) => {
    for (let j = 0; j < hgt; j++) for (let i = 0; i < w; i++) c(x + i, y + j, 1, 1, unionJack(i / w, j / hgt), 0.9);
  };
  flag(N / 2 - 20, N / 2 - 10, 40, 20);
  const tricolours = [["#002395", "#ffffff", "#ed2939"], ["#000000", "#dd0000", "#ffce00"], ["#169b62", "#ffffff", "#ff883e"],
    ["#0055a4", "#ffffff", "#ef4135"], ["#21468b", "#ffffff", "#ae1c28"], ["#ffffff", "#d52b1e", "#ffffff"]];
  const heart = ["01100110", "11111111", "11111111", "11111111", "01111110", "00111100", "00011000"];
  for (let k = 0; k < 150; k++) {
    const x = Math.floor(rnd() * N), y = Math.floor(rnd() * N);
    if (Math.abs(x - N / 2) < 30 && Math.abs(y - N / 2) < 18) continue;
    const kind = rnd();
    if (kind < 0.18) {
      const t = tricolours[Math.floor(rnd() * tricolours.length)], w = 9 + Math.floor(rnd() * 12), hh = Math.round(w * 0.6);
      for (let i = 0; i < 3; i++) c(x + Math.floor((w * i) / 3), y, Math.ceil(w / 3), hh, t[i], 0.85);
    } else if (kind < 0.3) {
      flag(x, y, 16 + Math.floor(rnd() * 10), 8 + Math.floor(rnd() * 5));
    } else if (kind < 0.42) {
      const col = PLACE[Math.floor(rnd() * PLACE.length)];
      heart.forEach((row, j) => [...row].forEach((b, i) => b === "1" && c(x + i, y + j, 1, 1, col, 0.9)));
    } else if (kind < 0.75) {
      const w = 4 + Math.floor(rnd() * 22), hh = 4 + Math.floor(rnd() * 16);
      c(x, y, w, hh, PLACE[Math.floor(rnd() * PLACE.length)], 0.5 + rnd() * 0.4);
      if (rnd() < 0.5) c(x + 1, y + 1, w - 2, hh - 2, PLACE[Math.floor(rnd() * PLACE.length)], 0.7);
    } else {
      const col = PLACE[Math.floor(rnd() * PLACE.length)];
      for (let i = 0; i < 40; i++) c(x + Math.floor(rnd() * 14), y + Math.floor(rnd() * 14), 1, 1, col, 0.8);
    }
  }
  ctx.globalAlpha = 1;
}

function unionJack(u, v) {
  const fx = u * 60, fy = v * 30, hyp = Math.hypot(1, 0.5);
  const cx = Math.abs(fx - 30), cy = Math.abs(fy - 15);
  if (cx < 3 || cy < 3) return UJ.red;
  if (cx < 5 || cy < 5) return UJ.white;
  const sA = (fy - fx / 2) / hyp, sB = (fy - (30 - fx / 2)) / hyp;
  const left = fx < 30, top = fy < 15;
  const dA = Math.abs(sA), dB = Math.abs(sB);
  if ((left && top) || (!left && !top)) {
    if (dA < 3) return (left ? sA > 0 && sA < 2 : sA < 0 && sA > -2) ? UJ.red : UJ.white;
  } else if (dB < 3) {
    return (left ? sB < 0 && sB > -2 : sB > 0 && sB < 2) ? UJ.red : UJ.white;
  }
  return UJ.blue;
}

// ---------------------------------------------------------------- helpers
const _m = new THREE.Matrix4(), _q = new THREE.Quaternion(), _p = new THREE.Vector3(), _s = new THREE.Vector3(),
  _e = new THREE.Euler(), _c = new THREE.Color();
function setInst(mesh, i, x, y, z, s = 1, rx = 0, ry = 0, rz = 0, sy = null) {
  _p.set(x, y, z);
  _q.setFromEuler(_e.set(rx, ry, rz));
  _s.set(s, sy == null ? s : sy, s);
  mesh.setMatrixAt(i, _m.compose(_p, _q, _s));
}
function voxels(count, size = 1, opts = {}) {
  const geo = opts.geo || new THREE.BoxGeometry(size * 0.9, size * 0.9, size * 0.9);
  const mat = opts.basic
    ? new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false })
    : new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: opts.rough ?? 0.42, metalness: opts.metal ?? 0.12 });
  const m = new THREE.InstancedMesh(geo, mat, count);
  m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  m.frustumCulled = false;
  for (let i = 0; i < count; i++) m.setColorAt(i, _c.set(0xffffff));
  return m;
}
function hide(mesh, i) { setInst(mesh, i, 0, -9999, 0, 0.0001); }
function toScreen(x, y, z) {
  _p.set(x, y, z).project(WORLD.cam);
  return [(_p.x * 0.5 + 0.5) * W, (-_p.y * 0.5 + 0.5) * H, _p.z];
}
function piece(name, build) { WORLD.pieces[name] = { build, group: null, update: null }; }
function buildPieces() {
  for (const [name, p] of Object.entries(WORLD.pieces)) {
    const group = new THREE.Group();
    p.update = p.build(group) || (() => {});
    p.group = group;
    group.visible = false;
    WORLD.scene.add(group);
  }
}
const lookCam = (p, l, fov = 42) => ({ p, l, fov });
function orbit(cx, cy, cz, radius, angle, height, ly = cy) {
  return lookCam([cx + Math.sin(angle) * radius, cy + height, cz + Math.cos(angle) * radius], [cx, ly, cz]);
}
function mixCam(a, b, k) {
  return { p: a.p.map((v, i) => lerp(v, b.p[i], k)), l: a.l.map((v, i) => lerp(v, b.l[i], k)), fov: lerp(a.fov || 42, b.fov || 42, k) };
}
// When a scene has no camera of its own, the camera glides over the board.
function driftCam(T) {
  const z = 900 - T * 2.4, x = 420 + Math.sin(T * 0.05) * 90;
  return lookCam([x, 130 + Math.sin(T * 0.13) * 14, z], [x - 30 + Math.sin(T * 0.05 + 0.6) * 40, 0, z - 300]);
}

// ---------------------------------------------------------------- avatar atlas
// Every avatar the 3D scenes use, packed into one texture so hundreds can be drawn in a single call.
const ATLAS = { size: 4096, tile: 128, slots: {}, next: 0 };
function buildAtlas(uids) {
  const per = ATLAS.size / ATLAS.tile;
  const cv = document.createElement("canvas");
  cv.width = cv.height = ATLAS.size;
  const ctx = cv.getContext("2d");
  ctx.fillStyle = "#1a1d33";
  ctx.fillRect(0, 0, ATLAS.size, ATLAS.size);
  for (const uid of uids) {
    if (ATLAS.slots[uid] != null || ATLAS.next >= per * per) continue;
    const k = ATLAS.next++;
    ATLAS.slots[uid] = k;
    const entry = IMG[avatarUrl(uid)];
    const img = entry && entry.img.naturalWidth ? entry.img : null;
    const x = (k % per) * ATLAS.tile, y = Math.floor(k / per) * ATLAS.tile;
    if (img) ctx.drawImage(img, x, y, ATLAS.tile, ATLAS.tile);
  }
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  ATLAS.tex = tex;
  ATLAS.per = per;
}
const AV_VS = `
attribute vec2 aSlot; attribute vec3 aTint; attribute float aMix; attribute float aGlow;
uniform float uBill;
varying vec2 vUv; varying vec2 vSlot; varying vec3 vTint; varying float vMix; varying float vGlow; varying float vFogD;
void main(){
  vUv = uv; vSlot = aSlot; vTint = aTint; vMix = aMix; vGlow = aGlow;
  vec4 mv;
  if (uBill > .5) {
    vec4 c = modelViewMatrix * instanceMatrix * vec4(0., 0., 0., 1.);
    float s = length(instanceMatrix[0].xyz);
    mv = c + vec4(position.xy * s, 0., 0.);
  } else {
    mv = modelViewMatrix * instanceMatrix * vec4(position, 1.);
  }
  vFogD = -mv.z;
  gl_Position = projectionMatrix * mv;
}`;
const AV_FS = `
uniform sampler2D uAtlas; uniform float uPer; uniform vec3 uEdge; uniform float uEdgeW; uniform vec3 uFog; uniform float uFogK;
varying vec2 vUv; varying vec2 vSlot; varying vec3 vTint; varying float vMix; varying float vGlow; varying float vFogD;
void main(){
  vec2 e = min(vUv, 1. - vUv);
  if (e.x < .07 && e.y < .07) discard;
  vec2 uv = vec2((vSlot.x + clamp(vUv.x, .01, .99)) / uPer, 1. - (vSlot.y + 1. - clamp(vUv.y, .01, .99)) / uPer);
  vec3 c = texture2D(uAtlas, uv).rgb;
  c = mix(c, vTint, vMix);
  float edge = step(min(e.x, e.y), uEdgeW);
  c = mix(c, uEdge * (1. + vGlow * 2.), edge);
  c *= 1. + vGlow * .35;
  c = mix(c, uFog, 1. - exp(-vFogD * uFogK));
  gl_FragColor = vec4(c, 1.);
}`;
function avatarInstances(uids, opts = {}) {
  const n = uids.length;
  const geo = new THREE.PlaneGeometry(1, 1);
  const slot = new Float32Array(n * 2), tint = new Float32Array(n * 3), mixA = new Float32Array(n), glow = new Float32Array(n);
  uids.forEach((uid, i) => {
    const k = ATLAS.slots[uid] ?? 0;
    slot[i * 2] = k % ATLAS.per;
    slot[i * 2 + 1] = Math.floor(k / ATLAS.per);
  });
  const mat = new THREE.ShaderMaterial({
    uniforms: {
      uAtlas: { value: ATLAS.tex }, uPer: { value: ATLAS.per }, uBill: { value: opts.billboard ? 1 : 0 },
      uEdge: { value: new THREE.Color(opts.edge || "#ffffff") }, uEdgeW: { value: opts.edgeW ?? 0.04 },
      uFog: { value: new THREE.Color(INK) }, uFogK: { value: opts.fogK ?? 0.0012 },
    },
    vertexShader: AV_VS, fragmentShader: AV_FS, side: THREE.DoubleSide,
  });
  const m = new THREE.InstancedMesh(geo, mat, n);
  m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  m.frustumCulled = false;
  geo.setAttribute("aSlot", new THREE.InstancedBufferAttribute(slot, 2));
  geo.setAttribute("aTint", new THREE.InstancedBufferAttribute(tint, 3));
  geo.setAttribute("aMix", new THREE.InstancedBufferAttribute(mixA, 1));
  geo.setAttribute("aGlow", new THREE.InstancedBufferAttribute(glow, 1));
  return { mesh: m, tint, mix: mixA, glow, geo };
}

// ---------------------------------------------------------------- per-frame
function renderWorld(Tg, def, t, ch) {
  const b = WORLD.board;
  b.mesh.visible = def.board !== false;
  b.mat.uniforms.uTime.value = Tg;
  b.mat.uniforms.uAccent.value.set(ch.accent);
  b.mat.uniforms.uGlow.value = def.boardGlow ?? 1;
  b.mat.uniforms.uArt.value = def.boardArt ?? 1;
  const want = [].concat(def.gl || []);
  for (const [name, p] of Object.entries(WORLD.pieces)) {
    p.group.visible = want.includes(name);
  }
  const c = def.cam ? def.cam(t) : driftCam(Tg);
  WORLD.cam.position.set(...c.p);
  WORLD.cam.lookAt(...c.l);
  WORLD.cam.fov = c.fov || 42;
  WORLD.cam.updateProjectionMatrix();
  WORLD.cam.updateMatrixWorld();
  b.mat.uniforms.uCam.value.copy(WORLD.cam.position);
  for (const name of want) WORLD.pieces[name].update(t, def);
  WORLD.bloom.strength = def.bloom ?? 0.8;
  WORLD.composer.render();
}
