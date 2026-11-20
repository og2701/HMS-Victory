// The hero 3D pieces. Each builds its meshes once and returns update(t) for scene time t. Metadata the
// scenes need for labels (where the skyline's tallest towers stand, where each node of the web is) is
// kept on the globals below.

const SKY = {}, CLK = {}, MOS = {};

// ---------------------------------------------------------------- voxel Union Jack (title)
piece("flag", g => {
  const NX = 66, NY = 33, S = 3.2;
  const m = voxels(NX * NY, S, { rough: 0.32, metal: 0.18 });
  g.add(m);
  const info = [];
  for (let j = 0; j < NY; j++) for (let i = 0; i < NX; i++) {
    const k = j * NX + i;
    m.setColorAt(k, _c.set(unionJack((i + 0.5) / NX, (j + 0.5) / NY)));
    const r1 = hash(k * 1.7), r2 = hash(k * 3.1), r3 = hash(k * 5.9);
    // They start as a cloud around the camera's opening view, so the first frames are full of flying
    // pixels rather than empty sky.
    info.push({ i, j, tx: (i - NX / 2 + 0.5) * S, ty: (NY / 2 - j) * S + 72,
      sx: (r1 - 0.5) * 420, sy: 20 + r2 * 200, sz: 40 + r3 * 260,
      delay: 0.05 + (i / NX) * 1.1 + r1 * 0.4, rx: r2 * 8, ry: r3 * 8 });
  }
  m.instanceColor.needsUpdate = true;
  return t => {
    for (let k = 0; k < info.length; k++) {
      const v = info[k];
      const p = E.outExpo(prog(t, v.delay, 1.3));
      const wave = Math.sin(v.i * 0.2 - t * 2.6) * 5 * (v.i / NX + 0.2) + Math.sin(v.j * 0.28 + t * 1.7) * 1.4;
      setInst(m, k, lerp(v.sx, v.tx, p), lerp(v.sy, v.ty, p), lerp(v.sz, wave, p), lerp(0.4, 1, p), (1 - p) * v.rx, (1 - p) * v.ry, 0);
    }
    m.instanceMatrix.needsUpdate = true;
  };
});

// ---------------------------------------------------------------- the message tower
piece("tower", g => {
  const F = 7, S = 4.4, N = Math.max(60, Math.min(2000, Math.round(DATA.numbers.now.messages / 1000)));
  const m = voxels(N, S, { rough: 0.3, metal: 0.2 });
  g.add(m);
  const cols = ["#38d9ff", "#2d5bff", "#f4f6ff", "#7ae4ff", "#1b3aa8", "#38d9ff"];
  const info = [];
  for (let i = 0; i < N; i++) {
    m.setColorAt(i, _c.set(cols[Math.floor(hash(i * 2.3) * cols.length)]));
    const layer = Math.floor(i / (F * F)), cell = i % (F * F);
    info.push({ x: (cell % F - F / 2 + 0.5) * S * 1.04, z: (Math.floor(cell / F) - F / 2 + 0.5) * S * 1.04, y: layer * S * 1.04 + S / 2,
      start: 0.25 + (i / N) * 3.4, jx: (hash(i * 7.7) - 0.5) * 140, jz: (hash(i * 9.1) - 0.5) * 140 });
  }
  m.instanceColor.needsUpdate = true;
  g.userData.height = info[N - 1].y;
  return t => {
    for (let i = 0; i < N; i++) {
      const v = info[i];
      const p = prog(t, v.start, 0.6);
      if (p <= 0) { hide(m, i); continue; }
      const e = E.inQuad(p);
      setInst(m, i, lerp(v.x + v.jx, v.x, e), lerp(v.y + 320, v.y, e), lerp(v.z + v.jz, v.z, e), 1, (1 - p) * 3, (1 - p) * 2, 0);
    }
    m.instanceMatrix.needsUpdate = true;
  };
});

// ---------------------------------------------------------------- the calendar skyline
piece("skyline", g => {
  const days = DATA.calendar.days;
  const first = new Date(days[0][0] + "T12:00:00Z");
  const dow0 = (first.getUTCDay() + 6) % 7;
  const max = Math.max(...days.map(d => d[1])), min = Math.min(...days.map(d => d[1]));
  const LV = 26, S = 4.4, GAP = 5.6;
  const top = new Set(pickMoments().map(t => t.day));
  const weeks = Math.ceil((days.length + dow0) / 7);
  const colsInfo = days.map(([iso, n], k) => {
    const idx = k + dow0;
    const f = (n - min) / Math.max(max - min, 1);
    return { iso, n, f, w: Math.floor(idx / 7), r: idx % 7, cubes: 1 + Math.round(Math.pow(f, 1.25) * LV), hot: top.has(iso) };
  });
  const total = colsInfo.reduce((a, c) => a + c.cubes, 0);
  const m = voxels(total, S, { rough: 0.28, metal: 0.25 });
  g.add(m);
  const ramp = ["#101a52", "#1d3cc4", "#2d7bff", "#38d9ff", "#a6f0ff", "#ffffff"];
  const hotRamp = ["#5a0a1e", "#ff2e4d", "#ff7a2e", "#ffc23d", "#fff2c2"];
  const cube = [];
  for (const c of colsInfo) {
    const x = (c.r - 3) * GAP, z = (c.w - weeks / 2 + 0.5) * GAP;
    c.x = x; c.z = z; c.top = c.cubes * S;
    c.start = 0.5 + (c.w / weeks) * 3.4 + c.r * 0.03;
    for (let k = 0; k < c.cubes; k++) {
      const f = Math.min(1, (k / Math.max(c.cubes - 1, 1)) * Math.pow(c.f, 0.6) * 1.1), rampSet = c.hot ? hotRamp : ramp;
      const pos = f * (rampSet.length - 1), a = Math.floor(pos);
      const col = _c.set(rampSet[Math.min(a, rampSet.length - 1)]).lerp(new THREE.Color(rampSet[Math.min(a + 1, rampSet.length - 1)]), pos - a);
      m.setColorAt(cube.length, col);
      cube.push({ x, z, y: k * S + S / 2, at: c.start + k * 0.016 });
    }
  }
  m.instanceColor.needsUpdate = true;
  Object.assign(SKY, { weeks, GAP, S, cols: colsInfo, length: weeks * GAP,
    hot: colsInfo.filter(c => c.hot), months: [] });
  colsInfo.forEach(c => { if (c.iso.endsWith("-01") || c === colsInfo[0]) SKY.months.push({ m: +c.iso.slice(5, 7), z: c.z }); });
  return t => {
    for (let i = 0; i < cube.length; i++) {
      const c = cube[i];
      const p = prog(t, c.at, 0.3);
      if (p <= 0) { hide(m, i); continue; }
      const s = E.outBack(p);
      setInst(m, i, c.x, c.y * lerp(0.6, 1, E.outCubic(p)), c.z, s);
    }
    m.instanceMatrix.needsUpdate = true;
  };
});

// ---------------------------------------------------------------- the 24-hour ring
piece("clock", g => {
  const hours = DATA.calendar.hours, max = Math.max(...hours);
  const peak = hours.indexOf(max);
  const R = 58, S = 4.6, LV = 26;
  const colour = h => (h <= 4 ? "#6a5cff" : h <= 10 ? "#ffc23d" : h <= 16 ? "#38d9ff" : "#ff4f9e");
  const cubes = [];
  hours.forEach((n, hr) => {
    const levels = 2 + Math.round((n / max) * LV);
    const a = (hr / 24) * Math.PI * 2;
    for (let L = 0; L < levels; L++) for (let q = 0; q < 4; q++) {
      const tx = (q % 2 - 0.5) * S * 1.05, rz = (Math.floor(q / 2) - 0.5) * S * 1.05;
      const rr = R + rz;
      cubes.push({ hr, L, x: Math.sin(a) * rr + Math.cos(a) * tx, z: -Math.cos(a) * rr + Math.sin(a) * tx, y: L * S + S / 2, ry: -a,
        col: hr === peak ? (L >= levels - 3 ? "#ffffff" : "#ff2e4d") : colour(hr), at: 0.45 + hr * 0.075 + L * 0.012 });
    }
  });
  const m = voxels(cubes.length, S, { rough: 0.3, metal: 0.2 });
  cubes.forEach((c, i) => m.setColorAt(i, _c.set(c.col)));
  m.instanceColor.needsUpdate = true;
  g.add(m);
  const hub = new THREE.Mesh(new THREE.CylinderGeometry(16, 16, 3, 32), new THREE.MeshStandardMaterial({ color: 0x1a2050, roughness: 0.4, metalness: 0.5 }));
  hub.position.y = 1.5;
  g.add(hub);
  const hand = new THREE.Mesh(new THREE.BoxGeometry(2.2, 2.2, R - 6), new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false }));
  const handPivot = new THREE.Group();
  hand.position.set(0, 4, -(R - 6) / 2);
  handPivot.add(hand);
  g.add(handPivot);
  Object.assign(CLK, { R, S, peak, max, hours, levels: hours.map(n => 2 + Math.round((n / max) * LV)) });
  return t => {
    for (let i = 0; i < cubes.length; i++) {
      const c = cubes[i];
      const p = prog(t, c.at, 0.28);
      if (p <= 0) { hide(m, i); continue; }
      setInst(m, i, c.x, c.y, c.z, E.outBack(p), 0, c.ry, 0);
    }
    m.instanceMatrix.needsUpdate = true;
    // The hand sweeps a full turn as the hours rise, then swings round to point at the busiest hour.
    const sweep = E.inOutCubic(prog(t, 0.45, 1.9)) * Math.PI * 2;
    const target = Math.PI * 2 + (peak / 24) * Math.PI * 2;
    handPivot.rotation.y = -lerp(sweep, target, E.outBack(prog(t, 2.4, 0.9)));
  };
});

// ---------------------------------------------------------------- casino chip stacks
piece("chips", g => {
  const c = DATA.casino;
  if (!c) return;
  const unit = 50000;
  const n = Math.max(20, Math.round(c.staked / unit)), house = Math.max(1, Math.round(c.house / unit));
  const weights = [1.05, 1.5, 0.85, 1.25, 2.3, 1.1, 0.75, 1.35, 0.95];
  const wsum = weights.reduce((a, b) => a + b, 0);
  const per = weights.map(w => Math.round((w / wsum) * n));
  const geo = new THREE.CylinderGeometry(7, 7, 1.7, 32);
  const m = voxels(n + house, 1, { geo, rough: 0.3, metal: 0.35 });
  g.add(m);
  const cols = ["#1fe08f", "#f4f6ff", "#0f9d63", "#1fe08f", "#f4f6ff", "#ffc23d"];
  const chips = [];
  let k = 0;
  per.forEach((cnt, s) => {
    const sx = (s % 3 - 1) * 17, sz = (Math.floor(s / 3) - 1) * 17;
    for (let j = 0; j < cnt && k < n; j++, k++) {
      m.setColorAt(k, _c.set(cols[(j + s) % cols.length]));
      chips.push({ x: sx + (hash(k) - 0.5) * 0.8, z: sz + (hash(k * 3) - 0.5) * 0.8, y: j * 1.8 + 0.9, at: 0.25 + (k / n) * 2.6 });
    }
  });
  const tallest = weights.indexOf(Math.max(...weights));
  const baseH = per[tallest] * 1.8;
  for (let j = 0; j < house; j++, k++) {
    m.setColorAt(k, _c.set("#ff2e4d"));
    chips.push({ x: (tallest % 3 - 1) * 17, z: (Math.floor(tallest / 3) - 1) * 17, y: baseH + j * 1.8 + 0.9, at: 3.2 + j * 0.09, house: true });
  }
  m.instanceColor.needsUpdate = true;
  g.userData.top = baseH + house * 1.8;
  return t => {
    chips.forEach((ch, i) => {
      const p = prog(t, ch.at, 0.42);
      if (p <= 0) { hide(m, i); return; }
      const e = E.inQuad(p);
      setInst(m, i, ch.x, lerp(ch.y + 160, ch.y, e), ch.z, 1, 0, (1 - p) * 4, (1 - p) * 0.6);
    });
    m.instanceMatrix.needsUpdate = true;
  };
});

// ---------------------------------------------------------------- coin stacks (the richest 100)
piece("coins", g => {
  const vals = (DATA.money.top100 || DATA.money.richest.map(r => r.n)).slice(0, 100);
  const N = vals.length, side = Math.ceil(Math.sqrt(N));
  const cells = [];
  for (let i = 0; i < side; i++) for (let j = 0; j < side; j++) cells.push([i - (side - 1) / 2, j - (side - 1) / 2]);
  cells.sort((a, b) => Math.hypot(...a) - Math.hypot(...b) || Math.atan2(a[1], a[0]) - Math.atan2(b[1], b[0]));
  const unit = Math.max(500, vals[0] / 40);
  const coins = [];
  vals.forEach((v, r) => {
    const cnt = Math.max(1, Math.round(v / unit));
    const [cx, cz] = cells[r];
    for (let j = 0; j < cnt; j++) coins.push({ r, x: cx * 13, z: cz * 13, y: j * 1.35 + 0.7, at: 0.3 + r * 0.022 + j * 0.02, gold: r < 10 });
  });
  const geo = new THREE.CylinderGeometry(5, 5, 1.2, 28);
  const m = voxels(coins.length, 1, { geo, rough: 0.32, metal: 0.25 });
  coins.forEach((c, i) => m.setColorAt(i, _c.set(c.gold ? (c.r === 0 ? "#fff1b0" : "#ffc23d") : "#a8743a")));
  m.instanceColor.needsUpdate = true;
  g.add(m);
  return t => {
    coins.forEach((c, i) => {
      const p = prog(t, c.at, 0.35);
      if (p <= 0) { hide(m, i); return; }
      setInst(m, i, c.x, lerp(c.y + 60, c.y, E.outCubic(p)), c.z, 1, 0, (1 - p) * 3, 0);
    });
    m.instanceMatrix.needsUpdate = true;
  };
});

// ---------------------------------------------------------------- the avatar flag (finale)
// The most active members in the middle, spiralling out by rank, tinted into a Union Jack. The camera
// starts on the face in the centre and pulls back until the whole flag fits the frame.
piece("mosaic", g => {
  const ids = DATA.wall;
  const COLS = 32, ROWS = 16, S = 6.3, GAP = 6.75, Y0 = 60;
  const cells = [];
  for (let cy = 0; cy < ROWS; cy++) for (let cx = 0; cx < COLS; cx++) {
    const dx = (cx - (COLS - 1) / 2) / 2, dy = cy - (ROWS - 1) / 2;
    cells.push({ cx, cy, d: Math.hypot(dx, dy), a: Math.atan2(dy, dx) });
  }
  // The nine most active fill the 3x3 block in the middle (#1 at its centre), then the rest spiral out.
  const mid = cells.find(c => c.cx === COLS / 2 && c.cy === ROWS / 2);
  const ring = c => Math.max(Math.abs(c.cx - mid.cx), Math.abs(c.cy - mid.cy));
  const inner = c => ring(c) <= 1;
  cells.sort((p, q) => inner(q) - inner(p) || (inner(p) ? ring(p) - ring(q) : 0) || p.d - q.d || p.a - q.a);
  const n = Math.min(ids.length, cells.length);
  const av = avatarInstances(ids.slice(0, n), { billboard: false, edgeW: 0.0, fogK: 0.0005 });
  g.add(av.mesh);
  const info = ids.slice(0, n).map((uid, i) => {
    const { cx, cy } = cells[i];
    const u = (cx + 0.5) / COLS, v = (cy + 0.5) / ROWS;
    const col = _c.set(unionJack(u, v));
    av.tint.set([col.r, col.g, col.b], i * 3);
    return { u, v, tx: (cx - COLS / 2 + 0.5) * GAP, ty: (ROWS / 2 - cy - 0.5) * GAP + Y0,
      sx: (hash(i * 1.3) - 0.5) * 900, sy: Y0 + (hash(i * 2.9) - 0.5) * 900, sz: 300 + hash(i * 4.7) * 400,
      at: i < 9 ? -2 : 0.1 + (i / n) * 3.0 + hash(i * 6.1) * 0.15, spin: hash(i * 8.3) * 10 };
  });
  av.geo.attributes.aTint.needsUpdate = true;
  Object.assign(MOS, { first: info[0], width: COLS * GAP, Y0 });  // info[0] is #1, in the centre of the 3x3
  return t => {
    const tintK = E.inOutCubic(prog(t, 3.8, 1.6)) * 0.6;
    for (let i = 0; i < info.length; i++) {
      const v = info[i];
      const p = E.outExpo(prog(t, v.at, 1.4));
      const wave = Math.sin(v.u * 9 - t * 2.4) * 3 * v.u + Math.sin(v.v * 5 + t * 1.6) * 1.0;
      setInst(av.mesh, i, lerp(v.sx, v.tx, p), lerp(v.sy, v.ty, p), lerp(v.sz, wave, p), S * lerp(0.6, 1, p), (1 - p) * v.spin, (1 - p) * v.spin * 0.7, 0);
      av.mix[i] = tintK;
    }
    av.geo.attributes.aMix.needsUpdate = true;
    av.mesh.instanceMatrix.needsUpdate = true;
  };
});
