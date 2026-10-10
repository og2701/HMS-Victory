// The hero 3D pieces. Each builds its meshes once and returns update(t) for scene time t. Metadata the
// scenes need for labels (where the skyline's tallest towers stand, where each node of the web is) is
// kept on the globals below.

const SKY = {}, CLK = {}, NET = {};

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

// ---------------------------------------------------------------- the reply constellation
piece("network", g => {
  const nodes = DATA.network.nodes, edges = DATA.network.edges;
  const SC = 190;
  const av = avatarInstances(nodes.map(n => n.user), { billboard: true, edge: "#ff4f9e", edgeW: 0.07, fogK: 0.0009 });
  g.add(av.mesh);
  const maxw = Math.max(...nodes.map(n => n.w));
  const size = nodes.map(n => 13 + Math.sqrt(n.w / maxw) * 30);
  nodes.forEach((n, i) => { av.glow[i] = i < 5 ? 1 : 0; });
  av.geo.attributes.aGlow.needsUpdate = true;
  const spread = nodes.map(n => {
    const r = Math.hypot(...n.p) || 1, k = Math.pow(r, 0.45) / r;
    return n.p.map(v => v * k);
  });
  const pos = (i, t) => {
    const p = spread[i];
    return [p[0] * SC + Math.sin(t * 0.7 + i) * 2.5, p[1] * SC + Math.cos(t * 0.6 + i * 1.3) * 2.5, p[2] * SC];
  };
  const sorted = edges.slice().sort((a, b) => b[2] - a[2]);
  const maxe = sorted.length ? sorted[0][2] : 1;
  const mkLines = (list, width, opacity, colour) => {
    const geo = new LineSegmentsGeometry();
    const posArr = new Float32Array(list.length * 6), colArr = new Float32Array(list.length * 6);
    list.forEach(([a, b, n], k) => {
      const c = _c.set(colour).multiplyScalar(0.35 + 0.65 * Math.sqrt(n / maxe));
      colArr.set([c.r, c.g, c.b, c.r, c.g, c.b], k * 6);
    });
    geo.setPositions(posArr);
    geo.setColors(colArr);
    const mat = new LineMaterial({ linewidth: width, vertexColors: true, transparent: true, opacity, depthWrite: false,
      blending: THREE.AdditiveBlending });
    mat.resolution.set(W, H);
    const line = new LineSegments2(geo, mat);
    line.frustumCulled = false;
    g.add(line);
    return { geo, list, posArr };
  };
  const strong = mkLines(sorted.slice(0, 14), 6, 0.7, "#ffd1ea");
  const weak = mkLines(sorted.slice(14), 2.4, 0.45, "#ff4f9e");
  Object.assign(NET, { pos, size, nodes });
  const fillLines = (L, t, start, span) => {
    const n = L.list.length;
    L.list.forEach(([a, b], k) => {
      const pa = pos(a, t), pb = pos(b, t);
      const p = E.outCubic(prog(t, start + (k / Math.max(n, 1)) * span, 0.5));
      const ex = lerp(pa[0], pb[0], p), ey = lerp(pa[1], pb[1], p), ez = lerp(pa[2], pb[2], p);
      L.posArr.set([pa[0], pa[1], pa[2], ex, ey, ez], k * 6);
    });
    L.geo.setPositions(L.posArr);
  };
  return t => {
    nodes.forEach((n, i) => {
      const p = E.outBack(prog(t, 0.25 + i * 0.035, 0.6));
      const [x, y, z] = pos(i, t);
      if (p <= 0) { hide(av.mesh, i); return; }
      setInst(av.mesh, i, x, y, z, size[i] * p);
    });
    av.mesh.instanceMatrix.needsUpdate = true;
    fillLines(strong, t, 1.0, 1.0);
    fillLines(weak, t, 1.3, 2.2);
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
piece("mosaic", g => {
  const ids = DATA.wall;
  const COLS = 40, ROWS = Math.ceil(ids.length / COLS), S = 6.3, GAP = 6.75;
  const av = avatarInstances(ids, { billboard: false, edgeW: 0.0, fogK: 0.0005 });
  g.add(av.mesh);
  const info = ids.map((uid, i) => {
    const cx = i % COLS, cy = Math.floor(i / COLS);
    const u = (cx + 0.5) / COLS, v = (cy + 0.5) / ROWS;
    const col = _c.set(unionJack(u, v));
    av.tint.set([col.r, col.g, col.b], i * 3);
    const d = Math.hypot(u - 0.5, (v - 0.5) * 0.5);
    return { u, v, tx: (cx - COLS / 2 + 0.5) * GAP, ty: (ROWS / 2 - cy - 0.5) * GAP + 60,
      sx: (hash(i * 1.3) - 0.5) * 900, sy: 60 + (hash(i * 2.9) - 0.5) * 900, sz: 300 + hash(i * 4.7) * 400,
      at: 0.15 + d * 2.4 + hash(i * 6.1) * 0.35, spin: hash(i * 8.3) * 10 };
  });
  av.geo.attributes.aTint.needsUpdate = true;
  g.userData = { COLS, ROWS, GAP };
  return t => {
    const tintK = E.inOutCubic(prog(t, 3.6, 1.6)) * 0.62;
    for (let i = 0; i < info.length; i++) {
      const v = info[i];
      const p = E.outExpo(prog(t, v.at, 1.5));
      const wave = Math.sin(v.u * 9 - t * 2.4) * 4 * v.u + Math.sin(v.v * 5 + t * 1.6) * 1.2;
      setInst(av.mesh, i, lerp(v.sx, v.tx, p), lerp(v.sy, v.ty, p), lerp(v.sz, wave, p), S * lerp(0.6, 1, p), (1 - p) * v.spin, (1 - p) * v.spin * 0.7, 0);
      av.mix[i] = tintK;
    }
    av.geo.attributes.aMix.needsUpdate = true;
    av.mesh.instanceMatrix.needsUpdate = true;
  };
});
