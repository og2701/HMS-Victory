// Scenes, part two: the Words, Casino, Money and Games chapters, Big Brother, the door, and the finale.

// ---------------------------------------------------------------- the words
chapterScene("words", 4, "THE WORDS", "What we actually said");
const WD = DATA.words;

scene({
  id: "months", ch: "words", dur: 7.8, boardGlow: 0.9,
  build(r) {
    const rows = WD.by_month.filter(m => m.words.length);
    r.append(h("div.scrim-mid"),
      block(230, {}, kicker("WHAT WE TALKED ABOUT"), words("THE YEAR IN WORDS", "title", 0.2),
        line("The words that jumped out each month, compared with the rest of the year", 0.5, "small")),
      h("div.abs.col", { style: { left: "56px", right: "56px", top: "480px", gap: "8px" } },
        rows.map((m, k) => {
          const at = 0.5 + k * 0.38;
          return an(h("div.mrow",
            h("div.mlab", MONTHS[+m.month.slice(5, 7) - 1]),
            h("div.mwords", m.words.slice(0, 3).map((w, j) =>
              an(h("span.mw" + (j ? "" : ".first"), w.w), "type", at + 0.12 + j * 0.16, 0.35)))), "left", at, 0.6);
        })),
    );
  },
});

scene({
  id: "laugh", ch: "words", dur: 6.6, boardGlow: 0.8,
  build(r) {
    const order = ["lol", "lmao", "haha", "😭", "😂", "💀"];
    const L = Object.fromEntries(WD.laugh.map(x => [x.k, x]));
    const rows = order.map(k => L[k]).filter(Boolean);
    const max = Math.max(...rows.flatMap(x => [x.now, x.prev]));
    const lol = L.lol, cry = L["😭"];
    const overtook = lol && cry && cry.now > lol.now && cry.prev < lol.prev;
    r.append(h("div.scrim-mid"),
      block(230, {}, kicker("THE LAUGH INDEX"), words("HOW WE LAUGHED", "title", 0.2),
        line(`Per 10,000 messages, ${YEAR - 1} against ${YEAR}`, 0.6, "small")),
      h("div.abs.col", { style: { left: "56px", right: "56px", top: "540px", gap: "14px" } },
        rows.map((x, k) => {
          const at = 0.7 + k * 0.18, ch = pct(x.now, x.prev);
          return an(h("div.lrow", { style: { height: "136px" } },
            h("div.llab", x.k),
            h("div.col", { style: { flex: 1, gap: "10px" } },
              h("div.row", { style: { gap: "14px" } }, h("div", { style: { height: "30px", width: (x.prev / max) * 470 + "px", background: "rgba(255,255,255,.22)" } }), h("div.tiny", `${x.prev}`)),
              h("div.row", { style: { gap: "14px" } }, an(h("div.pxbar", { style: { height: "46px", width: (x.now / max) * 470 + "px" } }), "growx", at + 0.3, 1), h("div.h3.acc", `${x.now}`))),
            ch != null ? h("span.chip." + (ch >= 0 ? "up" : "down"), `${ch >= 0 ? "+" : ""}${ch}%`) : null), "left", at, 0.6);
        })),
      overtook ? h("div.abs.center", { style: { left: 0, right: 0, top: "1580px" } },
        an(h("div.big", { style: { fontSize: "130px" } }, h("span", "😭"), " OVERTOOK ", h("span.acc", "LOL")), "slam", 3.4, 0.5)) : null);
  },
  update(t, r) {
    const [sx, sy] = shake(t, 3.5, 14);
    r.style.transform = `translate(${sx}px, ${sy}px)`;
  },
});

scene({
  id: "emoji", ch: "words", dur: 6.6, boardGlow: 0.9,
  build(r) {
    const half = (list, top, at, title) => {
      const items = list.slice(0, 6), max = items[0] ? items[0].n : 1;
      return h("div.abs", { style: { left: "56px", right: "56px", top: top + "px" } },
        kicker(title, at),
        h("div.row", { style: { alignItems: "flex-end", gap: "18px", height: "500px", marginTop: "20px" } },
          items.map((e, k) => h("div.col", { style: { flex: 1, alignItems: "center", justifyContent: "flex-end", height: "100%", gap: "12px" } },
            an(h("div", emojiEl(e.e, e.id, 104)), "pop", at + 0.5 + k * 0.1, 0.6),
            h("div.tiny.white", short(e.n)),
            an(h("div.pxcol", { style: { width: "100%", height: Math.max(18, (e.n / max) * 260) + "px", background: k ? "rgba(255,255,255,.22)" : "var(--acc)" } }), "growy", at + 0.3 + k * 0.1, 0.8)))));
    };
    const wordle = WD.emoji.slice(0, 6).some(e => e.e === "🟩" || e.e === "⬛");
    r.append(h("div.scrim-mid"),
      half(WD.emoji, 220, 0.1, "MOST USED IN MESSAGES"),
      wordle ? an(h("div.abs.tiny", { style: { left: "56px", top: "860px", color: "var(--white)" } }, "THE GREEN AND BLACK SQUARES ARE WORDLE'S FAULT"), "fade", 1.6, 0.5) : null,
      half(WD.reactions, 1010, 1.6, "MOST USED AS REACTIONS"));
  },
});

scene({
  id: "longest", ch: "words", dur: 5.2, boardGlow: 0.7,
  build(r) {
    const L = WD.longest;
    const doc = h("div.doclines");
    const lens = (L.lines && L.lines.length ? L.lines : [400]).flatMap(n => n === 0 ? [0] : Array.from({ length: Math.ceil(n / 42) }, (_, i) => (i === Math.ceil(n / 42) - 1 ? (n % 42 || 42) : 42)));
    for (let rep = 0; rep < 3; rep++) lens.forEach((n, i) => doc.append(h("div.dl", { style: { width: n ? (n / 42) * 100 * (0.82 + hash(i + rep) * 0.18) + "%" : "0", height: n ? "26px" : "18px" } })));
    r._doc = doc;
    r.append(h("div.scrim-top", { style: { height: "600px" } }),
      block(230, {}, kicker("THE LONGEST MESSAGE"), words("STILL READING IT", "title", 0.2)),
      h("div.abs", { style: { left: "110px", right: "110px", top: "540px", height: "860px" } },
        h("div.frame.px", { style: { height: "100%" } },
          h("div.in.px", { style: { padding: "40px", overflow: "hidden", background: "rgba(240,242,255,.94)" } }, doc))),
      h("div.abs", { style: { left: "70px", top: "700px", transform: "rotate(-8deg)" } },
        an(h("div.stamp", { style: { fontSize: "54px" } }, `${fmt(L.chars)} CHARACTERS`), "slam", 1.5, 0.45)),
      block(1470, {}, html(`${fmt(L.words)} words from ${b(name(L.user))} on ${dmyLong(L.day)}.`, 2.2, "h3"),
        line("We won't read it out.", 2.8, "body")));
  },
  update(t, r) {
    r._doc.style.transform = `translateY(${-E.inCubic(prog(t, 0.3, 5.6)) * 1800}px)`;
  },
});

// ---------------------------------------------------------------- the casino
const CS = DATA.casino;
if (CS) {
  chapterScene("casino", 5, "THE CASINO", `Open since ${dmyLong(CS.since)}`);

  scene({
    id: "casino-totals", ch: "casino", dur: 6.4, revealEnd: 4.4, gl: "chips", bloom: 0.95,
    cam: t => orbit(0, 0, 0, 150, 0.6 + t * 0.16, 105 - t * 3, 40),
    build(r) {
      r.append(h("div.scrim-top", { style: { height: "1000px" } }), h("div.scrim-bot", { style: { height: "520px" } }),
        block(230, {}, kicker("STAKED AT THE TABLES"),
          h("div.mega.glow", { style: { fontSize: "210px", marginTop: "26px" } }, roll(CS.staked, 0.25, 2.9)),
          html(`UKPence, over ${b(fmt(CS.bets))} bets from ${b(fmt(CS.players))} people`, 1.4, "h3")),
        block(1560, {}, an(h("div.row", { style: { gap: "24px" } }, h("div.chipdot"),
          h("div", h("div.h2", h("span.red", "The house took "), roll(CS.house, 3.3, 1.2)),
            h("div.small", `${(CS.house / CS.staked * 100).toFixed(1)}p in every pound`))), "up", 3.2, 0.6)));
    },
  });

  scene({
    id: "casino-games", ch: "casino", dur: 6.8, boardGlow: 0.8,
    build(r) {
      r._cv = canvasLayer();
      const games = CS.games.slice(0, 7);
      const losers = CS.games.filter(g => g.house < 0 && g.bets >= 500).map(g => g.game);
      r.append(h("div.scrim-mid"), r._cv,
        block(230, {}, kicker("WHERE IT WENT"), words("THE GAMES", "title", 0.2), line("Every UKPence staked, split by game", 0.5, "small")),
        h("div.abs.center", { style: { left: 0, right: 0, top: "690px", height: "0" } },
          an(h("div.center", h("div.tiny", "BIGGEST GAME"), h("div.h2", games[0].game.toUpperCase()), h("div.big.acc", { style: { fontSize: "110px" } }, `${Math.round(games[0].staked / CS.staked * 100)}%`)), "pop", 1.6, 0.6)),
        h("div.abs.col", { style: { left: "56px", right: "56px", top: "1060px", gap: "12px" } },
          games.map((g, k) => an(h("div.lrow", { style: { height: "62px" } }, h("div.gsw", { style: { background: GAME_COL[k % GAME_COL.length] } }),
            h("div.h3", { style: { flex: 1 } }, g.game), h("div.small.white", short(g.staked)),
            h("span.chip." + (g.house >= 0 ? "down" : "up"), g.house >= 0 ? `HOUSE +${short(g.house)}` : `PLAYERS +${short(-g.house)}`)), "left", 1.8 + k * 0.08, 0.5))),
        losers.length ? block(1600, {}, html(`Only ${losers.map(b).join(" and ")} paid out more than ${losers.length > 1 ? "they" : "it"} took.`, 3.0, "h3")) : null);
    },
    update(t, r) {
      const ctx = r._cv.getContext("2d");
      ctx.clearRect(0, 0, W, H);
      const cx = 540, cy = 690, R = 300, Ri = 190;
      const spin = (1 - E.outCubic(prog(t, 0.2, 2.4))) * Math.PI * 6;
      const grow = E.outCubic(prog(t, 0.2, 1.6));
      let a = -Math.PI / 2 + spin;
      CS.games.forEach((g, k) => {
        const sweep = (g.staked / CS.staked) * Math.PI * 2 * grow;
        ctx.beginPath();
        ctx.arc(cx, cy, R, a, a + sweep);
        ctx.arc(cx, cy, Ri, a + sweep, a, true);
        ctx.closePath();
        ctx.fillStyle = k < 7 ? GAME_COL[k] : "#3a4166";
        ctx.fill();
        a += sweep;
      });
      // the wheel's frets
      ctx.fillStyle = "#04050a";
      for (let k = 0; k < 37; k++) {
        const aa = (k / 37) * Math.PI * 2 + spin;
        ctx.save(); ctx.translate(cx, cy); ctx.rotate(aa); ctx.fillRect(Ri - 2, -2, R - Ri + 4, 4); ctx.restore();
      }
      const ball = spin * -1.6 + t * 0.4;
      const br = lerp(R + 40, R - 26, E.outBounce ? 1 : E.outCubic(prog(t, 1.6, 0.8)));
      ctx.fillStyle = "#fff";
      ctx.shadowColor = "#fff"; ctx.shadowBlur = 20;
      ctx.beginPath(); ctx.arc(cx + Math.cos(ball) * br, cy + Math.sin(ball) * br, 13, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
    },
  });

  scene({
    id: "casino-moments", ch: "casino", dur: 7.4, revealEnd: 5.6, boardGlow: 0.8,
    build(r) {
      const win = CS.biggest_wins[0], wd = CS.worst_day, bd = CS.best_day;
      const coaster = wd.user === bd.user && Math.abs(new Date(wd.day) - new Date(bd.day)) <= 3 * 864e5;
      r._cv = canvasLayer();
      r.append(h("div.scrim-mid"), r._cv,
        block(220, {}, an(h("div.frame.acc.px", { style: { height: "640px" } }, h("div.in.px", { style: { padding: "40px" } },
          h("div.kick", "BIGGEST SINGLE WIN"),
          h("div.row", { style: { gap: "30px", marginTop: "34px" } }, avatar(win.user, 190, 0.5, 0.8, "ring"),
            h("div", h("div.h2", name(win.user)), h("div.small", `${win.game}, ${dmyLong(win.day)}`))),
          h("div.mega.acc.glow", { style: { fontSize: "190px", marginTop: "40px" } }, roll(win.net, 0.8, 1.6, { pre: "+" })),
          h("div.small", `from a ${fmt(win.staked)} stake`))), "up", 0.2, 0.7)),
        block(920, {}, an(h("div.frame.px", { style: { height: "760px" } }, h("div.in.px", { style: { padding: "40px" } },
          h("div.kick", coaster ? "THE ROLLERCOASTER" : "THE WORST DAY"),
          h("div.row", { style: { gap: "30px", marginTop: "34px" } }, avatar(wd.user, 150, 2.6, 0.8, "ring"), h("div.h2", name(wd.user))),
          coaster ? h("div.col", { style: { marginTop: "40px", gap: "6px" } },
            html(`${ba(signed(bd.net))} on ${dmyLong(bd.day)}`, 3.2, "h2"),
            html(`<span class="red">${signed(wd.net)}</span> on ${dmyLong(wd.day)}`, 4.4, "h2")) :
            h("div.col", { style: { marginTop: "40px" } }, html(`<span class="red">${signed(wd.net)}</span> on ${dmyLong(wd.day)}`, 3.2, "h2")))), "up", 2.4, 0.7)));
      r._coaster = coaster;
    },
    update(t, r) {
      const ctx = r._cv.getContext("2d");
      ctx.clearRect(0, 0, W, H);
      const bp = prog(t, 2.2, 1.3);
      if (bp > 0 && bp < 1) {
        for (let q = 0; q < 60; q++) {
          const a = hash(q * 1.7) * Math.PI * 2, sp = 300 + hash(q * 2.3) * 700;
          const x = 540 + Math.cos(a) * sp * E.outCubic(bp), y = 640 + Math.sin(a) * sp * E.outCubic(bp) + 900 * bp * bp;
          ctx.globalAlpha = 1 - bp;
          ctx.fillStyle = q % 4 ? "#ffc23d" : "#fff1b0";
          ctx.fillRect(x - 10, y - 10, 20, 20);
        }
        ctx.globalAlpha = 1;
      }
      if (r._coaster) {
        const X0 = 640, X1 = 990, Ytop = 1330, Ybot = 1600;
        const pts = [[0, 0.75], [0.2, 0.7], [0.45, 0.05], [0.55, 0.0], [0.62, 0.3], [0.8, 0.95], [1, 1]];
        const k = E.inOutCubic(prog(t, 3.0, 2.4));
        ctx.strokeStyle = "#1fe08f"; ctx.lineWidth = 10; ctx.shadowColor = "#1fe08f"; ctx.shadowBlur = 24;
        ctx.beginPath();
        for (let i = 0; i <= 60 * k; i++) {
          const u = i / 60;
          let s = 0; while (s < pts.length - 2 && pts[s + 1][0] < u) s++;
          const [ua, va] = pts[s], [ub, vb] = pts[s + 1];
          const v = lerp(va, vb, E.inOutSine((u - ua) / (ub - ua)));
          const x = X0 + u * (X1 - X0), y = Ytop + v * (Ybot - Ytop);
          if (u > 0.55) ctx.strokeStyle = "#ff2e4d";
          i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
        }
        ctx.stroke();
        ctx.shadowBlur = 0;
      }
    },
  });

  scene({
    id: "casino-awards", ch: "casino", dur: 5.4, boardGlow: 1.2, bloom: 0.9,
    build(r) {
      const cards = [
        { key: "chips", title: "High Roller", user: CS.high_rollers[0].user, value: short(CS.high_rollers[0].staked), line: "UKPence staked" },
        { key: "clover", title: "Lucky Sod", user: CS.biggest_wins[0].user, value: "+" + fmt(CS.biggest_wins[0].net), line: `in one go, on ${CS.biggest_wins[0].game}` },
        { key: "trophy", title: "Beat the House", user: CS.winners[0].user, value: "+" + fmt(CS.winners[0].net), line: "up overall" },
        { key: "coin", title: "House's Best Friend", user: CS.losers[0].user, value: signed(CS.losers[0].net), line: `across ${fmt(CS.losers[0].bets)} bets` },
      ];
      r.append(h("div.scrim-mid"), block(196, {}, kicker("CASINO AWARDS", 0.05)),
        h("div.abs", { style: { left: "56px", right: "56px", top: "290px", display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: "36px 28px", justifyItems: "center" } },
          cards.map((a, k) => awardCard(a, 0.15 + k * 0.22))));
    },
  });
}
const GAME_COL = ["#1fe08f", "#ffc23d", "#ff2e4d", "#38d9ff", "#a86bff", "#ff7a2e", "#ff4f9e"];

// ---------------------------------------------------------------- the money
const MN = DATA.money;
chapterScene("money", 6, "THE MONEY", "Where the UKPence went");

// When the supply stops moving for a month or more, the economy was closed: say so, and draw the
// drift down and the flat line after it.
const SUP = MN.supply || [];
const SUP_END = SUP.length ? SUP[SUP.length - 1][1] : 0;
let lockAt = SUP.length - 1;
while (lockAt > 0 && Math.abs(SUP[lockAt - 1][1] - SUP_END) / SUP_END < 0.005) lockAt--;
const LOCKED = SUP.length > 1 && lockAt > 0 && (new Date(SUP[SUP.length - 1][0]) - new Date(SUP[lockAt][0])) / 864e5 >= 30;
const LOCK_DAY = LOCKED ? SUP[lockAt][0] : null;
scene({
  id: "supply", ch: "money", dur: 7.2, revealEnd: 3.2, boardGlow: 0.8,
  build(r) {
    r._cv = canvasLayer();
    const last = SUP.length ? SUP[SUP.length - 1][1] : 0;
    const low = SUP.length ? Math.min(...SUP.slice(0, Math.max(lockAt, 1)).map(x => x[1])) : 0;
    r.append(h("div.scrim-mid"), r._cv,
      block(230, {}, kicker(LOCKED ? "A CLOSED ECONOMY" : "IN CIRCULATION"),
        h("div.mega.glow", { style: { fontSize: "220px", marginTop: "24px" } }, roll(last, 0.3, 2.2)),
        LOCKED ? html(`UKPence in existence since ${b(dmyLong(LOCK_DAY))}. Not one more, not one less.`, 1.2, "h3") :
          line(`UKPence in circulation, from ${fmt(SUP[0] ? SUP[0][1] : 0)} on ${SUP[0] ? dmyLong(SUP[0][0]) : ""}`, 1.2, "small")),
      LOCKED ? an(h("div.abs.tiny", { style: { left: "70px", top: "1135px", color: "var(--white)" } }, `DRIFTED DOWN TO ${fmt(low)}`), "fade", 1.8, 0.5) : null,
      LOCKED ? an(h("div.abs.chip.new", { style: { right: "70px", top: "720px" } }, "LOCKED"), "pop", 2.9, 0.5) : null,
      block(1250, {}, kicker("IN AND OUT", 3.0),
        h("div.row", { style: { gap: "24px", marginTop: "30px" } },
          an(h("div.frame.px", { style: { flex: 1 } }, h("div.in.px", { style: { padding: "28px" } }, h("div.tiny", "BOOSTER BONUSES PAID"), h("div.big.acc", { style: { fontSize: "100px", marginTop: "14px" } }, roll(MN.booster, 3.4, 1.4, { pre: "+" })))), "up", 3.2, 0.6),
          an(h("div.frame.px", { style: { flex: 1 } }, h("div.in.px", { style: { padding: "28px" } }, h("div.tiny", "INACTIVITY TAX TAKEN"), h("div.big.red", { style: { fontSize: "100px", marginTop: "14px" } }, roll(MN.tax, 3.6, 1.4, { pre: "-" })))), "up", 3.4, 0.6)),
        Math.abs(MN.booster - MN.tax) / Math.max(MN.booster, 1) < 0.05 ? html(`Within ${fmt(Math.abs(MN.booster - MN.tax))} of cancelling each other out.`, 4.8, "h3") : null));
  },
  update(t, r) {
    const ctx = r._cv.getContext("2d");
    ctx.clearRect(0, 0, W, H);
    if (SUP.length < 2) return;
    const vals = SUP.map(s => s[1]), lo = Math.min(...vals) * 0.9, hi = Math.max(...vals) * 1.04;
    const X0 = 70, X1 = 1010, Y0 = 1110, Y1 = 760;
    const k = E.inOutCubic(prog(t, 0.4, 2.6));
    const n = Math.max(2, Math.floor(vals.length * k));
    const px = i => X0 + (i / (vals.length - 1)) * (X1 - X0), py = v => Y0 - ((v - lo) / (hi - lo)) * (Y0 - Y1);
    const path = (from, to) => { ctx.beginPath(); for (let i = from; i < to; i++) { i > from ? ctx.lineTo(px(i), py(vals[i])) : ctx.moveTo(px(i), py(vals[i])); } };
    ctx.save();
    ctx.lineWidth = 9; ctx.lineJoin = "round";
    // the drift, then the locked line in gold
    const split = LOCKED ? lockAt : vals.length;
    ctx.strokeStyle = LOCKED ? "rgba(255,255,255,.55)" : "#ffc23d";
    path(0, Math.min(n, split));
    ctx.stroke();
    if (LOCKED && n > split) {
      // the jump up to the locked total, then the flat line
      ctx.strokeStyle = "#ffc23d"; ctx.shadowColor = "#ffc23d"; ctx.shadowBlur = 30;
      ctx.setLineDash([]);
      path(split - 1, n);
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.strokeStyle = "rgba(255,194,61,.5)"; ctx.lineWidth = 3; ctx.setLineDash([10, 10]);
      ctx.beginPath(); ctx.moveTo(px(split - 1), py(vals[split - 2] || vals[0])); ctx.lineTo(px(split - 1), py(vals[split])); ctx.stroke();
    }
    ctx.restore();
  },
});

scene({
  id: "richest", ch: "money", dur: 6.4, gl: "coins", bloom: 1,
  cam: t => orbit(0, 0, 0, 170, 0.5 + t * 0.12, 150 - t * 5, 6),
  build(r) {
    const medal = ["#ffc23d", "#d4d7e8", "#c98a4b"];
    r.append(h("div.scrim-top", { style: { height: "1000px" } }), h("div.scrim-bot", { style: { height: "380px" } }),
      block(230, {}, kicker("THE RICH LIST"), words("THE RICHEST", "title", 0.2), line("The biggest UKPence balances right now", 0.5, "small")),
      block(500, {}, h("div.col.listpanel", { style: { gap: "20px" } }, MN.richest.slice(0, 3).map((x, k) =>
        an(h("div.lrow", h("div.rank", { style: { color: medal[k] } }, `#${k + 1}`), avatar(x.user, 104, 0.6 + k * 0.15, 0.6),
          h("div.h2", { style: { flex: 1 } }, name(x.user)), h("div.h2", { style: { color: medal[k] } }, roll(x.n, 0.7 + k * 0.15, 1.4))), "left", 0.5 + k * 0.15, 0.6)))),
      block(1640, {}, html(`The 10 richest hold ${ba(Math.round(MN.top10_share * 100) + "%")} of all UKPence.`, 2.6, "h3")));
  },
});

scene({
  id: "pay", ch: "money", dur: 7.0, revealEnd: 5.2, boardGlow: 0.8,
  build(r) {
    const bp = MN.biggest_pay, gv = MN.givers[0];
    r._cv = canvasLayer();
    r.append(h("div.scrim-mid"), r._cv,
      block(230, {}, kicker("/PAY"), h("div.title", roll(MN.pay_n, 0.3, 1.4), " PAYMENTS"),
        html(`${b(fmt(MN.pay_total))} UKPence passed hand to hand`, 0.8, "small")),
      bp ? block(540, {}, an(h("div.frame.acc.px", { style: { height: "740px" } }, h("div.in.px", { style: { padding: "40px" } },
        h("div.kick", "THE BIGGEST ONE"),
        h("div.row", { style: { justifyContent: "space-between", marginTop: "34px" } },
          h("div.center", { style: { width: "320px" } }, r._a = avatar(bp.from, 170, 1.4, 0.7, "ring"), h("div.small.white", { style: { marginTop: "14px" } }, name(bp.from))),
          h("div.center", { style: { width: "320px" } }, r._b = avatar(bp.to, 170, 1.55, 0.7, "ring"), h("div.small.white", { style: { marginTop: "14px" } }, name(bp.to)))),
        h("div.big.acc", { style: { marginTop: "30px", textAlign: "center" } }, roll(bp.n, 1.6, 1.4)),
        bp.returned ? html("...and the exact same amount came straight back, the same day.", 3.8, "h3") : line(dmyLong(bp.day), 3.2, "small"))), "up", 1.1, 0.7)) : null,
      gv ? block(1330, {}, an(h("div.frame.px", h("div.in.px", { style: { padding: "34px 40px" } }, h("div.kick", "THE PHILANTHROPIST"),
        h("div.lrow", { style: { marginTop: "24px" } }, avatar(gv.user, 120, 4.6, 0.7, "ring"),
          h("div", h("div.h2", name(gv.user)), html(`gave away ${ba(fmt(gv.n))} to ${b(gv.people + " people")}`, 4.8, "h3"))))), "up", 4.4, 0.7)) : null);
  },
  update(t, r) {
    const ctx = r._cv.getContext("2d");
    ctx.clearRect(0, 0, W, H);
    const bp = MN.biggest_pay;
    if (!bp) return;
    const A = r._pa || (r._pa = posOf(r._a)), B = r._pb || (r._pb = posOf(r._b));
    flow(ctx, t, A, B, 26, { start: 1.8, stop: 3.5, speed: 0.9, curve: 120, size: 16, colors: ["#ffc23d", "#fff1b0"], seed: 4 });
    if (bp.returned) flow(ctx, t, B, A, 26, { start: 3.6, speed: 0.9, curve: -120, size: 16, colors: ["#ffc23d", "#fff1b0"], seed: 8 });
  },
});

scene({
  id: "rags", ch: "money", dur: 6.2, boardGlow: 0.8,
  build(r) {
    const rg = MN.rags;
    r._cv = canvasLayer();
    r.append(h("div.scrim-mid"), r._cv,
      rg ? block(230, {}, kicker("RAGS TO RICHES"), line("The biggest climb of the year, from someone's lowest balance to their highest", 0.3, "small"),
        h("div.row", { style: { gap: "30px", marginTop: "30px" } }, avatar(rg.user, 170, 0.4, 0.8, "ring"),
        h("div", h("div.h2", name(rg.user)), h("div.small", `${dmyLong(rg.from)} to ${dmyLong(rg.to)}`))),
        h("div.row", { style: { gap: "26px", alignItems: "baseline", marginTop: "30px" } },
          an(h("div.big", { style: { color: "var(--dim)" } }, fmt(rg.low)), "fade", 0.9, 0.4), an(h("div.big.acc", "→"), "left", 1.1, 0.5),
          h("div.mega.acc.glow", { style: { fontSize: "200px" } }, roll(rg.high, 1.2, 1.8)))) : null,
      block(1130, {}, kicker("THE SHOP", 2.6), h("div.col.listpanel", { style: { gap: "16px", marginTop: "26px" } }, MN.shop.slice(0, 4).map((s, k) =>
        an(h("div.lrow", { style: { height: "86px" } }, h("div.rank", `#${k + 1}`), h("div.h3", { style: { flex: 1 } }, s.item),
          h("div.small.white", `${fmt(s.n)} sold`), h("div.h3.acc", { style: { width: "220px", textAlign: "right" } }, short(s.spent))), "left", 2.9 + k * 0.12, 0.6)))));
  },
  update(t, r) {
    const ctx = r._cv.getContext("2d");
    ctx.clearRect(0, 0, W, H);
    const k = E.inOutCubic(prog(t, 1.0, 2.2));
    ctx.strokeStyle = "#ffc23d"; ctx.lineWidth = 8; ctx.shadowColor = "#ffc23d"; ctx.shadowBlur = 26;
    ctx.beginPath();
    for (let i = 0; i <= 80 * k; i++) {
      const u = i / 80, x = 70 + u * 940, y = 1000 - Math.pow(u, 2.2) * 120 - Math.sin(u * 30) * 6 * u;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.stroke();
    ctx.shadowBlur = 0;
  },
});

// ---------------------------------------------------------------- the games
const GM = DATA.games || {};
chapterScene("games", 7, "THE GAMES", "Karts, dragons and a lot of newspapers");

if (GM.kart) {
  scene({
    id: "kart", ch: "games", dur: 6.4, boardGlow: 0.8,
    build(r) {
      const K = GM.kart, champ = K.champions[0];
      r._cv = canvasLayer();
      r.append(h("div.scrim-mid"), r._cv,
        block(230, {}, kicker("UKP KART"), h("div.title", roll(K.races, 0.3, 1.2), " RACES"), line(`${K.racers} drivers on the grid so far`, 0.7, "small")),
        champ ? block(560, {}, an(h("div.row", { style: { gap: "30px" } }, avatar(champ.user, 190, 0.8, 0.8, "ring"),
          h("div", h("div.kick", "CHAMPION"), h("div.h2", { style: { marginTop: "12px" } }, name(champ.user)),
            h("div.big.acc", { style: { fontSize: "110px" } }, roll(champ.wins, 1.1, 1), " WINS"))), "left", 0.7, 0.7)) : null,
        block(1010, {}, kicker("LAP RECORDS", 2.0), h("div.col.listpanel", { style: { gap: "12px", marginTop: "22px" } }, K.laps.map((l, k) =>
          an(h("div.lrow", { style: { height: "84px" } }, h("div.h3", { style: { width: "380px" } }, l.track), avatar(l.user, 60, 2.4 + k * 0.1, 0.5),
            h("div.small.white", { style: { flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } }, name(l.user)),
            h("div.h3.acc", `${l.lap.toFixed(2)}s`)), "left", 2.2 + k * 0.1, 0.5)))));
    },
    update(t, r) {
      const ctx = r._cv.getContext("2d");
      ctx.clearRect(0, 0, W, H);
      const cx = 820, cy = 360, path = u => [cx + Math.sin(u * Math.PI * 2) * 170 + Math.sin(u * Math.PI * 6) * 30, cy + Math.sin(u * Math.PI * 4) * 70];
      ctx.globalAlpha = 0.5 * clamp(t * 2);
      ctx.fillStyle = "#3a4166";
      for (let i = 0; i < 160; i++) { const [x, y] = path(i / 160); ctx.fillRect(x - 10, y - 10, 20, 20); }
      ctx.globalAlpha = 1;
      for (let car = 0; car < 3; car++) {
        const u = (t * (0.28 - car * 0.03) + car * 0.08) % 1;
        const [x, y] = path(u);
        ctx.fillStyle = ["#ff7a2e", "#38d9ff", "#ffc23d"][car];
        ctx.shadowColor = ctx.fillStyle; ctx.shadowBlur = 20;
        ctx.fillRect(x - 13, y - 13, 26, 26);
      }
      ctx.shadowBlur = 0;
    },
  });
}

if ((GM.daily && GM.daily.length) || GM.skyrim) {
  scene({
    id: "daily", ch: "games", dur: 6.8, boardGlow: 0.9,
    build(r) {
      const unit = { Climb: "metres climbed", Paperboy: "papers delivered", Spitfire: "points scored" };
      const sk = GM.skyrim;
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker("THE DAILY GAMES"), words("EVERY SINGLE DAY", "title", 0.2)),
        h("div.abs.col", { style: { left: "56px", right: "56px", top: "500px", gap: "22px" } },
          (GM.daily || []).map((d, k) => an(h("div.frame.px", h("div.in.px", { style: { padding: "26px 34px" } },
            h("div.row", { style: { justifyContent: "space-between" } }, h("div.kick", d.label.toUpperCase()), h("div.tiny", `SINCE ${dmy(d.since)}`)),
            h("div.row", { style: { justifyContent: "space-between", alignItems: "flex-end", marginTop: "14px" } },
              h("div", h("div.big", { style: { fontSize: "104px" } }, roll(d.total, 0.6 + k * 0.2, 1.5)), h("div.small", unit[d.label] || "")),
              d.best ? h("div.center", { style: { width: "260px" } }, avatar(d.best.user, 84, 1.0 + k * 0.2, 0.6), h("div.tiny.white", { style: { marginTop: "10px" } }, `BEST: ${fmt(d.best.n)}`)) : null))), "left", 0.4 + k * 0.18, 0.7)),
          sk ? an(h("div.frame.acc.px", h("div.in.px", { style: { padding: "26px 34px" } },
            h("div.row", { style: { justifyContent: "space-between" } }, h("div.kick", "SKYRIM"), h("div.tiny", `SINCE ${dmy(sk.since)}`)),
            h("div.skgrid", [["DELVES", sk.delves], ["KILLS", sk.kills], ["DRAGONS", sk.dragons], ["DEATHS", sk.deaths]].map(([k, v], i) =>
              h("div", h("div.big", { style: { fontSize: "66px" } }, roll(v, 1.4 + i * 0.1, 1.3)), h("div.tiny", k)))),
            h("div.lrow", { style: { marginTop: "18px" } }, avatar(sk.most_dead.user, 70, 2.2, 0.6),
              h("div.small.white", sk.most_dead.user === sk.dragonborn.user ?
                `${name(sk.most_dead.user)}: most deaths (${sk.most_dead.n}) and most dragons (${sk.dragonborn.n})` :
                `${name(sk.most_dead.user)} died the most: ${sk.most_dead.n} times`)))), "left", 1.0, 0.7) : null));
    },
  });
}

const BB = DATA.big_brother;
if (BB) {
  scene({
    id: "big-brother", ch: "games", dur: 6.8, boardGlow: 0.6, bloom: 1.1, pin: "radial",
    build(r) {
      const days = Math.round((new Date(BB.ended) - new Date(BB.started)) / 864e5) + 1;
      r.append(h("div.scrim-mid"),
        h("div.abs.center", { style: { left: 0, right: 0, top: "200px" } }, h("div.eye", icon("eye_open", 280))),
        block(560, { textAlign: "center" }, an(h("div.kick", { style: { justifyContent: "center", color: "var(--red)" } }, "BIG BROTHER"), "fade", 0.6, 0.4),
          h("div.title", { style: { marginTop: "18px" } }, split(`${days} DAYS. ${BB.housemates} HOUSEMATES.`, "up", 0.8, 0.03, 0.6))),
        h("div.abs", { style: { left: "56px", right: "56px", top: "860px", display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: "22px" } },
          [["MESSAGES IN THE HOUSE", BB.messages], ["VOTES CAST", BB.votes], ["DIARY ROOM ENTRIES", BB.diary], ["CHALLENGES", BB.challenges]].map(([k, v], i) =>
            an(h("div.frame.px", h("div.in.px", { style: { padding: "26px 30px" } }, h("div.big", { style: { fontSize: "100px" } }, roll(v, 1.4 + i * 0.1, 1.3)), h("div.tiny", k))), "up", 1.3 + i * 0.1, 0.6))),
        BB.winner ? block(1330, { textAlign: "center" }, an(h("div.center", h("div.kick", { style: { justifyContent: "center" } }, "THE WINNER"),
          h("div", { style: { marginTop: "22px" } }, avatar(BB.winner, 220, 3.0, 0.9, "ring")),
          h("div.h2", { style: { marginTop: "18px" } }, name(BB.winner))), "pop", 2.8, 0.7)) : null);
    },
    update(t, r) {
      const lid = 1 - E.outCubic(prog(t, 0.2, 0.6)) + pulse(t, 4.6, 4.75, 0.1);
      r.querySelector(".eye").style.transform = `scaleY(${clamp(1 - lid, 0.06, 1)})`;
    },
  });
}

// Rivals and the lottery: worth a slide once there's enough of them (by December, probably).
if ((GM.rival && GM.rival.n >= 20) || (MN.lottery && MN.lottery.length >= 5)) {
  scene({
    id: "extras", ch: "games", dur: 6.0, boardGlow: 0.9,
    build(r) {
      const rv = GM.rival, lt = MN.lottery && MN.lottery[0];
      const lotWins = lt ? MN.lottery.filter(x => x.user === lt.user).length : 0;
      r.append(h("div.scrim-mid"), block(230, {}, kicker("AND THE REST"), words("ODDS AND ENDS", "title", 0.2)),
        h("div.abs.col", { style: { left: "56px", right: "56px", top: "500px", gap: "26px" } },
          rv ? an(h("div.frame.px", h("div.in.px", { style: { padding: "30px 36px" } }, h("div.kick", "RIVALS"),
            h("div.row", { style: { justifyContent: "space-between", marginTop: "22px" } },
              h("div.center", avatar(rv.a, 130, 0.7, 0.6, "ring"), h("div.small.white", { style: { marginTop: "10px" } }, name(rv.a))),
              h("div.big", `${rv.a_wins}-${rv.b_wins}`),
              h("div.center", avatar(rv.b, 130, 0.8, 0.6, "ring"), h("div.small.white", { style: { marginTop: "10px" } }, name(rv.b)))),
            h("div.small", { style: { marginTop: "14px", textAlign: "center" } }, `${rv.n} head-to-heads`))), "up", 0.5, 0.7) : null,
          lt ? an(h("div.frame.px", h("div.in.px", { style: { padding: "30px 36px" } }, h("div.kick", "THE LOTTERY"),
            h("div.lrow", { style: { marginTop: "22px" } }, avatar(lt.user, 110, 1.5, 0.6, "ring"),
              h("div", h("div.h2", name(lt.user)), h("div.small", lotWins > 1 ? `won it ${lotWins} times, ${fmt(MN.lottery.filter(x => x.user === lt.user).reduce((s, x) => s + x.prize, 0))} UKPence in all` : `won ${fmt(lt.prize)} UKPence`))))), "up", 1.3, 0.7) : null));
    },
  });
}

// ---------------------------------------------------------------- the badges
// Bronze, silver, gold and the secret ones. The secret ones are only ever counted, never named or shown.
const BD = DATA.badges;
if (BD) {
  const TIERS = ["Bronze", "Silver", "Gold", "Secret"];
  const tcls = t => "t-" + t.toLowerCase();
  const rainbow = (r, t) => {
    for (const el of r.querySelectorAll(".f-secret"))
      el.style.background = `conic-gradient(from ${t * 90}deg, ${RAINBOW.join(", ")}, ${RAINBOW[0]})`;
    r.querySelectorAll(".t-secret").forEach((el, i) => { el.style.color = `hsl(${(t * 140 + i * 40) % 360} 95% 68%)`; });
  };
  chapterScene("badges", 8, "THE BADGES", "Bronze, silver, gold and the secret ones");

  scene({
    id: "badge-haul", ch: "badges", dur: 6.6, boardGlow: 0.9,
    build(r) {
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker(`SINCE ${dmyLong(BD.since).toUpperCase()}`),
          h("div.title", roll(BD.n, 0.3, 1.6), " BADGES"),
          line(`earned by ${fmt(BD.people)} of you`, 0.7, "h3")),
        h("div.abs.tiers", { style: { left: "56px", right: "56px", top: "640px" } }, TIERS.map((t, i) =>
          an(h("div.frame.px.f-" + t.toLowerCase(), h("div.in.px.tier", medalIcon(t, 110),
            h("div", h("div.big." + tcls(t), roll(BD.tiers[t] || 0, 1.0 + i * 0.2, 1.3)),
              h("div.tiny.white", { style: { marginTop: "8px" } }, t.toUpperCase()),
              h("div.tiny", `OUT OF ${BD.catalogue[t]}`)))), "up", 0.8 + i * 0.15, 0.6))),
        BD.paid ? block(1330, {}, kicker("PAID OUT", 2.2),
          h("div.big.acc", { style: { fontSize: "150px", marginTop: "20px" } }, roll(BD.paid, 2.4, 1.4)),
          line("UKPence in badge rewards", 2.6, "h3")) : null);
    },
    update(t, r) { rainbow(r, t); },
  });

  scene({
    id: "medal-table", ch: "badges", dur: 7.4, boardGlow: 0.8,
    build(r) {
      const cols = ["Gold", "Silver", "Bronze", "Secret"];
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker("THE MEDAL TABLE"), words("GOLDS FIRST", "title", 0.2),
          line("Ranked like the Olympics: golds, then silvers, then bronzes", 0.5, "small")),
        h("div.abs.col", { style: { left: "56px", right: "56px", top: "590px", gap: "12px" } },
          an(h("div.mhead", cols.map(c => h("span", medalIcon(c, 44)))), "fade", 0.6, 0.5),
          BD.table.map((x, k) => an(h("div.medalrow.px" + (k ? "" : ".first"),
            h("div.rank", String(k + 1)), avatar(x.user, 64, 0.7 + (7 - k) * 0.16, 0.5),
            h("div.who.h3", name(x.user)),
            cols.map(c => h("div.mcol." + tcls(c), String(x[c.toLowerCase()] || 0)))),
          k ? "left" : "pop", 0.7 + (BD.table.length - 1 - k) * 0.16, k ? 0.6 : 0.7))));
    },
    update(t, r) { rainbow(r, t); },
  });

  scene({
    id: "badge-rarest", ch: "badges", dur: 7.4, boardGlow: 0.8,
    build(r) {
      const c = BD.common;
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker("THE RAREST"), words("HARDLY ANYONE", "title", 0.2)),
        h("div.abs.col", { style: { left: "56px", right: "56px", top: "470px", gap: "22px" } }, BD.rarest.map((x, k) =>
          an(h("div.frame.px.f-" + x.tier.toLowerCase(), h("div.in.px", { style: { display: "flex", alignItems: "center", gap: "26px", padding: "22px 26px" } },
            h("div.btile.px", emojiEl(x.icon, null, 92)),
            h("div", { style: { flex: 1, minWidth: 0 } },
              h("div.h3", x.name), h("div.small", { style: { fontSize: "28px" } }, x.desc),
              h("div.lrow", { style: { gap: "14px", marginTop: "14px" } }, x.holders.map((u, i) => avatar(u, 54, 1.0 + k * 0.3 + i * 0.08, 0.5)),
                h("div.small.white", { style: { fontSize: "28px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } },
                  x.holders.map(name).join(" & ")))),
            h("div.center", { style: { width: "120px", flex: "none" } },
              h("div.big." + tcls(x.tier), { style: { fontSize: "120px" } }, roll(x.n, 0.9 + k * 0.3, 0.8)),
              h("div.tiny", x.n === 1 ? "HOLDER" : "HOLDERS")))), "left", 0.5 + k * 0.3, 0.7))),
        c ? block(1430, {}, kicker("THE MOST COMMON", 2.0), an(h("div.lrow", { style: { marginTop: "20px" } },
          h("div.btile.px", { style: { width: "110px", height: "110px" } }, emojiEl(c.icon, null, 70)),
          h("div", { style: { minWidth: 0 } }, h("div.h3", c.name), h("div.small", `${fmt(c.n)} of you have it`))), "left", 2.2, 0.7)) : null);
    },
  });

  if (BD.unclaimed.length) {
    scene({
      id: "badge-unclaimed", ch: "badges", dur: 6.6, boardGlow: 0.7,
      build(r) {
        const n = BD.unclaimed.length;
        r.append(h("div.scrim-mid"),
          block(230, {}, kicker("STILL UP FOR GRABS"), words("UNCLAIMED", "title", 0.2),
            line(FULL ? `${n} badges nobody earned. Next year, maybe.` : `${n} badges nobody has earned. There's still time.`, 0.6, "small")),
          h("div.abs.bgrid", { style: { left: "56px", right: "56px", top: "640px" } }, BD.unclaimed.slice(0, 16).map((x, k) =>
            an(h("div.frame.px.bslot.f-" + x.tier.toLowerCase(), h("div.in.px", h("div.locked", emojiEl(x.icon, null, 72)), h("div.tiny", x.name))),
              "pop", 0.9 + k * 0.07, 0.5))));
      },
    });
  }

  const SC = BD.secrets;
  scene({
    id: "badge-secrets", ch: "badges", dur: 7.4, boardGlow: 1.1,
    build(r) {
      const n = SC.found_by.length;
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker("THE SECRET ONES"), words(`${n} SECRETS`, "title", 0.2),
          line("We're not saying what they are.", 0.6, "small")),
        h("div.abs.sgrid", { style: { left: "56px", right: "56px", top: "560px" } }, SC.found_by.map((c, k) =>
          an(h("div.frame.px.f-secret", h("div.in.px", medalIcon("Secret", 76),
            h("div.big" + (c ? ".t-secret" : ".red"), { style: { fontSize: "68px" } }, c ? roll(c, 1.1 + k * 0.08, 1) : "0"),
            h("div.tiny", c ? "FOUND IT" : "NOBODY YET"))), "pop", 0.8 + k * 0.08, 0.5))),
        SC.hunter ? block(1490, {}, kicker("TOP SECRET HUNTER", 2.3), an(h("div.lrow", { style: { marginTop: "20px" } },
          avatar(SC.hunter.user, 120, 2.6, 0.7, "ring"),
          h("div", { style: { minWidth: 0 } }, h("div.h2", name(SC.hunter.user)),
            h("div.small", `found ${SC.hunter.n} of the ${n}. ${fmt(SC.finders)} of you have found at least one.`))), "left", 2.5, 0.7)) : null);
    },
    update(t, r) { rainbow(r, t); },
  });
}

// ---------------------------------------------------------------- outro
const SV = DATA.server;
scene({
  id: "door", ch: "outro", dur: 6.0, revealEnd: 3.0, boardGlow: 1, pin: "diag",
  build(r) {
    r._cv = canvasLayer();
    r.append(h("div.scrim-mid"), r._cv,
      block(230, {}, kicker("THE DOOR"), words("COMINGS AND GOINGS", "title", 0.2),
        line(`Since ${dmyLong(SV.first_day)}`, 0.6, "small")),
      h("div.abs", { style: { left: "56px", right: "56px", top: "1040px", display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: "22px" } },
        [["JOINED", SV.joined, "acc"], ["LEFT", SV.left, "red"], ["BANNED", SV.banned, "red"], ["HERE NOW", SV.members, "white"]].map(([k, v, c], i) =>
          an(h("div.frame.px", h("div.in.px", { style: { padding: "26px 30px" } }, h("div.big." + c, { style: { fontSize: "104px" } }, roll(v, 1.0 + i * 0.15, 1.4)), h("div.tiny", k))), "up", 0.9 + i * 0.12, 0.6))));
  },
  update(t, r) {
    const ctx = r._cv.getContext("2d");
    ctx.clearRect(0, 0, W, H);
    const dx = 540, dy = 760;
    ctx.fillStyle = "#0e1230"; ctx.fillRect(dx - 90, dy - 170, 180, 300);
    ctx.fillStyle = "#ff2e4d"; ctx.fillRect(dx - 90, dy - 170, 180, 12); ctx.fillRect(dx - 90, dy - 170, 12, 300); ctx.fillRect(dx + 78, dy - 170, 12, 300);
    ctx.fillStyle = "#ffc23d"; ctx.fillRect(dx + 40, dy - 10, 14, 14);
    const tot = SV.joined + SV.left;
    flow(ctx, t, [-60, dy - 20], [dx, dy - 20], Math.round(60 * SV.joined / tot), { start: 0.3, speed: 0.65, curve: 60, size: 14, color: "#c6ff3d", seed: 3 });
    flow(ctx, t, [dx, dy + 40], [W + 60, dy + 40], Math.round(60 * SV.left / tot), { start: 0.6, speed: 0.65, curve: -60, size: 14, color: "#ff2e4d", seed: 6 });
  },
});

scene({
  id: "finale", ch: "outro", dur: 28, fixed: true, gl: "mosaic", bloom: 0.35, boardGlow: 0.7, pin: "radial", pout: "radial",
  cam: t => {
    // From the face at the centre of the flag out to the whole flag, fitted to the frame's width.
    const f = MOS.first || { tx: 0, ty: 60 };
    const fit = (MOS.width + 24) / (2 * Math.tan((42 * Math.PI) / 360) * (W / H));
    const k = E.inOutCubic(prog(t, 0.6, 5.0));
    const a = lookCam([f.tx, f.ty, 64], [f.tx, f.ty, 0], 40), bcam = lookCam([0, MOS.Y0 + 16, fit], [0, MOS.Y0 + 16, 0], 42);
    return mixCam(a, bcam, k);
  },
  build(r) {
    r.append(h("div.scrim-top", { style: { height: "760px" } }), h("div.scrim-bot", { style: { height: "720px" } }),
      block(250, { textAlign: "center" },
        h("div.mega", { style: { fontSize: "260px" } }, split("THANK YOU", "slam", 5.2, 0.05, 0.5)),
        html(`${b(fmt(NOW.messages))} messages. ${b(fmt(SV.members))} of you.`, 6.0, "h3")),
      block(1440, { textAlign: "center" },
        an(h("div.kick", { style: { justifyContent: "center", color: "var(--white)", fontSize: "30px" } },
          FULL ? `SEE YOU IN ${YEAR + 1}` : "THE YEAR ISN'T OVER YET"), "up", 7.0, 0.7),
        an(h("div.brandmark", ujIcon(72), "UKPLACE"), "fade", 21.6, 1.2)));
  },
  update(t, r) {
    const [sx, sy] = shake(t, 5.3, 10);
    r.style.transform = `translate(${sx}px, ${sy}px)`;
  },
});

// ---------------------------------------------------------------- interludes
// Single funny messages, each a punchline to the scene it follows. The picks file places them; they
// never sit between a chapter card and its first scene.
(DATA.interludes || []).forEach((it, k) => {
  const at = SCENES.findIndex(s => s.id === it.after);
  if (at < 0) return;
  scene({
    id: "meanwhile-" + k, ch: SCENES[at].ch, dur: 4.6, boardGlow: 1.4, bloom: 0.9, pin: "radial", pout: "radial",
    build(r) {
      const m = it.message;
      r.append(h("div.scrim-mid"),
        h("div.abs.center", { style: { left: 0, right: 0, top: "640px" } }, an(h("div.kick", { style: { justifyContent: "center" } }, it.kicker || "MEANWHILE..."), "type", 0.1, 0.5)),
        h("div.abs", { style: { left: "56px", right: "56px", top: "740px" } },
          h("div.bigmsg", message({ user: m.user, text: m.text, when: dmyLong(m.day), reactions: m.reactions }, 0.35, { fx: "pop" }))));
    },
  });
  SCENES.splice(at + 1, 0, SCENES.pop());
});
