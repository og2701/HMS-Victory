// The personal Wrapped: one member's year in under a minute, ending on their card. The page carries
// everyone's (personal.json); ?u=<id> picks whose, and ?card=1 shows only the card, for the picture.
// Every scene has a fixed length, so every video runs the same time and shares one soundtrack.

const M = DATA.meta;
const YEAR = M.year;
const FULL = M.full_year;
const ME = DATA.me;
const CARD_ONLY = new URLSearchParams(location.search).has("card");
// for render.mjs --personal: whose Wrapped this page can make, and who gets a video
window.PERSONAL_IDS = Object.entries(DATA.personal.people).map(([id, p]) => [id, p.video]);

chapter("intro", "WRAPPED", "#ff2e4d");
chapter("p-msgs", "YOUR MESSAGES", "#38d9ff");
chapter("p-when", "YOUR TIMES", "#a86bff");
chapter("p-where", "YOUR CHANNELS", "#c6ff3d");
chapter("p-people", "YOUR PEOPLE", "#ff4f9e");
chapter("p-vibe", "YOUR VIBE", "#1fe08f");
chapter("p-you", "THIS WAS YOU", "#ffc23d");
chapter("p-hit", "YOUR BIGGEST HIT", "#38d9ff");
chapter("p-trophies", "YOUR TROPHIES", "#ff7a2e");
chapter("outro", "YOUR CARD", "#ff2e4d");

// ---------------------------------------------------------------- components
const block = (top, style, ...kids) => h("div.pad", { style: Object.assign({ top: top + "px" }, style || {}) }, ...kids);
const kicker = (text, at = 0.1) => an(h("div.kick", text), "left", at, 0.7);
const line = (text, at, cls = "body", fx = "up") => an(h("div." + cls, text), fx, at, 0.7);
const hourName = hr => `${hr % 12 || 12}${hr < 12 ? "AM" : "PM"}`;
const topPct = Math.max(1, Math.ceil((ME.rank / ME.of) * 100));
const change = (now, prev, at) => {
  if (!prev) return an(h("span.chip.new", `NEW IN ${YEAR}`), "pop", at, 0.5);
  const p = pct(now, prev);
  return an(h("span.chip." + (p >= 0 ? "up" : "down"), `${p >= 0 ? "+" : ""}${p}% ON ${YEAR - 1}`), "pop", at, 0.5);
};
const stat = (label, value, at, extra = {}) => an(h("div.frame.px", h("div.in.px", { style: { padding: "24px 28px" } },
  h("div.big", { style: { fontSize: extra.size || "86px" } }, value), h("div.tiny", { style: { marginTop: "8px" } }, label))), "up", at, 0.6);
const personRow = (label, ref, text, at) => an(h("div.lrow.listpanel", { style: { padding: "18px 22px" } },
  avatar(ref.user, 96, at + 0.15, 0.6), h("div", { style: { minWidth: 0 } }, h("div.tiny", label),
    h("div.h3", { style: { fontSize: "44px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } }, ref.name),
    h("div.small", text))), "left", at, 0.6);

// Highlights: the stats this person stands out for most (extract.py picks them), each with its words in
// the video and, shorter, on the card.
const HL = {
  laughs: ["LAUGHS ON YOUR MESSAGES", "LAUGHS GOT"], hearts: ["HEARTS ON YOUR MESSAGES", "HEARTS GOT"],
  reacts: ["REACTIONS ON YOUR MESSAGES", "REACTIONS"], given: ["REACTIONS YOU GAVE OUT", "REACTS GIVEN"],
  swears: [ME.top_swear ? `SWEARS, MOSTLY ${ME.top_swear.w.toUpperCase()}` : "SWEARS", "SWEARS"],
  poo: ["TIMES YOU BROUGHT UP POO", "POOS"], gifs: ["GIFS SENT", "GIFS"], stickers: ["STICKERS SENT", "STICKERS"],
  voice: ["VOICE NOTES", "VOICE NOTES"], files: ["PICTURES AND FILES", "PICTURES"], questions: ["QUESTIONS ASKED", "QUESTIONS"],
  caps: ["MESSAGES IN ALL CAPS", "ALL CAPS"], hof: ["IN THE HALL OF FAME", "HALL OF FAME"], badges: ["BADGES EARNED", "BADGES"],
  casino: ["CASINO GAMES PLAYED", "CASINO GAMES"],
};
const HIGH = (ME.highlights || []).filter(x => HL[x.k]);

// One of their messages, as it looked in Discord, with its reactions.
function bubble(m, at) {
  const reacts = (m.reactions || []).map((x, k) => an(h("div.react.px", emojiEl(x.e, x.id, 34), String(x.n)), "pop", at + 0.5 + k * 0.08, 0.5));
  return h("div.msg.px", avatar(ME.user, 88, at, 0.7),
    h("div", { style: { flex: 1, minWidth: 0 } }, h("div.who", h("span.name", ME.name), h("span.when", dmyLong(m.day))),
      h("div.text", { html: richText(m.text) }), reacts.length ? h("div.reacts", reacts) : null));
}

// The card: the year on one picture, in the video's last scene and on its own (?card=1) at 1080x1350.
function card(at = 0) {
  const tile = (label, ...kids) => h("div.ptile.px", h("div.tiny", label), ...kids);
  const md = ME.medals || {};
  const mate = ME.mate || ME.you_reply;
  const who = ref => (ref ? h("div.lrow", { style: { gap: "14px", marginTop: "14px" } }, avatar(ref.user, 64, at, 0.6),
    h("div.small.white", { style: { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } }, ref.name)) : h("div.pval", "-"));
  const high = x => (x.k === "badges"
    ? tile("BADGES", h("div.pmedals", ...["Gold", "Silver", "Bronze", "Secret"].map(t => h("div.row", { style: { gap: "6px" } },
      medalIcon(t, 30), h("div.small.white", { style: { fontWeight: 800 } }, String(md[t.toLowerCase()] || 0))))))
    : tile(HL[x.k][1], h("div.pval", short(x.n))));
  const fill = [...HIGH.slice(0, 3).map(high), tile("DAYS YOU SHOWED UP", h("div.pval", String(ME.days))),
    tile("BIGGEST DAY", h("div.pval", ME.busiest ? dmy(ME.busiest.day) : "-")), tile("WORDS", h("div.pval", short(ME.words)))].slice(0, 3);
  return h("div.pcard",
    h("div.row", { style: { justifyContent: "space-between" } },
      h("div.brandmark", { style: { opacity: 1 } }, ujIcon(44), `UKPLACE WRAPPED ${String(YEAR).slice(2)}`),
      h("div.tiny.white", FULL ? String(YEAR) : `1 JAN - ${dmy(M.until)}`)),
    h("div.lrow", { style: { marginTop: "40px", gap: "30px" } }, avatar(ME.user, 170, at, 0.8, "ring"),
      h("div", { style: { minWidth: 0 } }, h("div.h2", { style: { fontSize: "56px" } }, ME.name),
        h("div.kick.plain", { style: { color: "var(--gold)", marginTop: "14px" } }, ME.persona.title),
        h("div.small", { style: { marginTop: "8px" } }, ME.persona.line))),
    h("div", { style: { marginTop: "30px" } }, h("div.mega", { style: { fontSize: "150px", lineHeight: ".85" } }, fmt(ME.msgs)),
      h("div.h3", { style: { marginTop: "12px" } }, `messages · #${fmt(ME.rank)} of ${fmt(ME.of)} · top ${topPct}%`)),
    h("div.pgrid", { style: { marginTop: "36px" } },
      tile("BEST MATE", who(mate)),
      tile("BIGGEST FAN", who(ME.fan)),
      tile("TOP EMOJI", h("div.row", { style: { gap: "14px", marginTop: "12px" } }, ME.emoji ? emojiEl(ME.emoji.e, ME.emoji.id, 60) : null,
        h("div.pval", ME.emoji ? `×${short(ME.emoji.n)}` : "-"))),
      tile("PEAK HOUR", h("div.pval", hourName(ME.peak))),
      tile("TOP CHANNEL", h("div.pval", { style: { fontSize: "40px" } }, ME.channels[0] ? "#" + ME.channels[0].name : "-")),
      tile("BEST STREAK", h("div.pval", `${ME.streak} DAYS`)),
      ...fill),
    h("div.pfoot", ME.awards.length ? ME.awards.slice(0, 3).map(a => h("span.chip", a.toUpperCase())) :
      h("div.small", ME.hof ? `${ME.hof} messages in the Hall of Fame` : `${fmt(ME.days)} days you showed up`)));
}

// ---------------------------------------------------------------- the scenes
if (CARD_ONLY) {
  document.getElementById("hud").style.display = "none";
  scene({ id: "p-cardonly", ch: "outro", dur: 1, fixed: true, board: false,
    build(r) { r.append(h("div.abs", { style: { left: 0, top: 0 } }, card(-5))); } });
} else {
  scene({
    id: "p-intro", ch: "intro", dur: 4.5, fixed: true, boardGlow: 1.6, bloom: 0.9, pin: "radial",
    build(r) {
      r.append(h("div.scrim-mid"),
        h("div.abs.center", { style: { left: "56px", right: "56px", top: "330px" } },
          an(h("div.kick", { style: { justifyContent: "center" } }, `UKPLACE WRAPPED ${YEAR}`), "type", 0.2, 0.6),
          h("div", { style: { marginTop: "70px" } }, avatar(ME.user, 400, 0.4, 1.3, "ring")),
          an(h("div.title", { style: { marginTop: "70px", fontSize: "100px" } }, ME.name), "up", 1.3, 0.7),
          line(FULL ? "This was your year." : "Your year so far.", 1.9, "h3")));
    },
  });

  scene({
    id: "p-msgs", ch: "p-msgs", dur: 6.5, fixed: true, boardGlow: 1,
    build(r) {
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker("YOUR MESSAGES"),
          h("div.mega.acc.glow", { style: { fontSize: "250px", marginTop: "24px" } }, roll(ME.msgs, 0.3, 1.8)),
          h("div.row", { style: { gap: "18px", marginTop: "16px", flexWrap: "wrap" } },
            line(`#${fmt(ME.rank)} of ${fmt(ME.of)} · top ${topPct}%`, 1.0, "h3"), change(ME.msgs, ME.prev, 1.5))),
        h("div.abs", { style: { left: "56px", right: "56px", top: "900px", display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: "22px" } },
          stat("WORDS", roll(ME.words, 1.9, 1.3), 1.7),
          stat("DAYS YOU SHOWED UP", roll(ME.days, 2.0, 1.2), 1.85),
          stat("DAYS IN A ROW, YOUR LONGEST STREAK", roll(ME.streak, 2.1, 1.2), 2.0),
          stat("MESSAGES A DAY", String(Math.round(ME.msgs / Math.max(ME.days, 1))), 2.15)));
    },
  });

  scene({
    id: "p-when", ch: "p-when", dur: 6.5, fixed: true, boardGlow: 1,
    build(r) {
      const d = ME.busiest ? new Date(ME.busiest.day) : null, max = Math.max(...ME.hours, 1);
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker("YOUR BIGGEST DAY"),
          d ? h("div.title", { style: { marginTop: "16px" } }, `${d.getDate()} ${MONTHS_LONG[d.getMonth()].toUpperCase()}`) : null,
          d ? line(`${fmt(ME.busiest.n)} messages in one day`, 0.7, "h3") : null),
        block(640, {}, kicker("YOUR HOURS", 1.2),
          h("div.row", { style: { alignItems: "flex-end", gap: "6px", height: "420px", marginTop: "26px" } },
            ME.hours.map((n, hr) => h("div.col", { style: { flex: 1, alignItems: "center", justifyContent: "flex-end", height: "100%", gap: "8px" } },
              an(h("div.pxcol", { style: { height: Math.max(8, (n / max) * 360) + "px", background: hr === ME.peak ? "var(--acc)" : "rgba(255,255,255,.25)" } }), "growy", 1.4 + hr * 0.03, 0.6),
              h("div.tiny", { style: { fontSize: "14px" } }, hr % 6 === 0 ? hourName(hr) : ""))))),
        block(1240, {}, an(h("div.h2", "You peak at ", h("span.acc", hourName(ME.peak))), "up", 2.4, 0.7)));
    },
  });

  scene({
    id: "p-where", ch: "p-where", dur: 5, fixed: true, boardGlow: 1,
    build(r) {
      const max = ME.channels[0] ? ME.channels[0].n : 1;
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker("WHERE YOU TALKED"), an(h("div.title", { style: { marginTop: "16px" } }, ME.channels[0] ? "#" + ME.channels[0].name : "EVERYWHERE"), "up", 0.3, 0.7),
          ME.channels[0] ? line(`${Math.round((ME.channels[0].n / ME.msgs) * 100)}% of your messages`, 0.7, "h3") : null),
        h("div.abs.col", { style: { left: "56px", right: "56px", top: "640px", gap: "18px" } }, ME.channels.map((c, k) =>
          an(h("div.lrow.listpanel", { style: { padding: "22px 26px", gap: "20px" } },
            h("div.rank", String(k + 1)), h("div", { style: { flex: 1, minWidth: 0 } },
              h("div.h3", { style: { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } }, "#" + c.name),
              h("div", { style: { height: "26px", marginTop: "10px" } }, an(h("div.pxbar", { style: { width: (c.n / max) * 100 + "%" } }), "growx", 1.2 + k * 0.15, 0.9))),
            h("div.h3.acc", { style: { width: "170px", flex: "none", textAlign: "right" } }, fmt(c.n))), "left", 1.0 + k * 0.15, 0.6))));
    },
  });

  scene({
    id: "p-people", ch: "p-people", dur: 7, fixed: true, boardGlow: 1,
    build(r) {
      const mate = ME.mate;
      const rows = [];
      if (ME.you_reply && (!mate || ME.you_reply.user !== mate.user)) rows.push(["YOU REPLIED TO MOST", ME.you_reply, `${fmt(ME.you_reply.n)} replies`]);
      if (ME.reply_you && (!mate || ME.reply_you.user !== mate.user)) rows.push(["REPLIED TO YOU MOST", ME.reply_you, `${fmt(ME.reply_you.n)} replies`]);
      if (ME.fan) rows.push(["YOUR BIGGEST FAN", ME.fan, `${fmt(ME.fan.n)} reactions on your messages`]);
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker("YOUR PEOPLE")),
        mate ? block(340, {}, an(h("div.frame.acc.px", h("div.in.px", { style: { padding: "34px 36px" } },
          h("div.kick.plain", "YOUR BEST MATE"),
          h("div.lrow", { style: { marginTop: "26px", gap: "26px" } }, avatar(ME.user, 150, 0.6, 0.7, "ring"), avatar(mate.user, 150, 0.75, 0.7, "ring"),
            h("div", { style: { minWidth: 0 } }, h("div.h2", { style: { fontSize: "54px" } }, mate.name),
              h("div.small", `${fmt(mate.n)} replies each way`))))), "pop", 0.4, 0.7)) : null,
        h("div.abs.col", { style: { left: "56px", right: "56px", top: mate ? "790px" : "360px", gap: "18px" } },
          rows.slice(0, 3).map(([label, ref, text], k) => personRow(label, ref, text, 1.6 + k * 0.3))));
    },
  });

  scene({
    id: "p-vibe", ch: "p-vibe", dur: 7, fixed: true, boardGlow: 1,
    build(r) {
      const e = ME.emoji;
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker("YOUR VIBE"),
          e ? h("div.lrow", { style: { marginTop: "30px", gap: "34px" } }, an(h("div", emojiEl(e.e, e.id, 220)), "pop", 0.4, 0.7),
            h("div", h("div.big.acc", { style: { fontSize: "130px" } }, roll(e.n, 0.6, 1.2)), line("times, your favourite emoji", 0.9, "h3"))) : null),
        h("div.abs", { style: { left: "56px", right: "56px", top: e ? "720px" : "380px", display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: "22px" } },
          HIGH.slice(0, 4).map((x, k) => stat(HL[x.k][0], roll(x.n, 1.6 + k * 0.1, 1.2), 1.4 + k * 0.15))));
    },
  });

  scene({
    id: "p-you", ch: "p-you", dur: 5, fixed: true, boardGlow: 1.6, bloom: 1.05,
    build(r) {
      r.append(h("div.scrim-mid"),
        h("div.abs.center", { style: { left: "56px", right: "56px", top: "560px" } },
          an(h("div.kick", { style: { justifyContent: "center" } }, FULL ? "THIS YEAR YOU WERE" : "SO FAR THIS YEAR YOU'RE"), "type", 0.1, 0.5),
          h("div.mega.acc", { style: { fontSize: "150px", marginTop: "40px", lineHeight: ".9" } }, split(ME.persona.title, "slam", 0.35, 0.06, 0.5, null, true)),
          line(ME.persona.line, 1.3, "h3"),
          ME.persona.beat ? line(`More than ${ME.persona.beat}% of us.`, 1.7, "h3 acc") : null));
    },
    update(t, r) {
      const [sx, sy] = shake(t, 0.5, 10);
      r.style.transform = `translate(${sx}px, ${sy}px)`;
    },
  });

  scene({
    id: "p-hit", ch: "p-hit", dur: 6, fixed: true, boardGlow: 1.2,
    build(r) {
      const m = ME.hit || ME.first;
      r.append(h("div.scrim-mid"),
        block(300, {}, kicker(ME.hit ? "YOUR BIGGEST HIT" : `YOUR FIRST MESSAGE OF ${YEAR}`),
          line(ME.hit ? `${fmt(ME.hit.n)} reactions, your most of the year` : "Where your year started", 0.4, "h3")),
        m ? h("div.abs", { style: { left: "56px", right: "56px", top: "600px" } }, h("div.bigmsg", an(bubble(m, 0.7), "pop", 0.6, 0.8))) : null);
    },
  });

  scene({
    id: "p-trophies", ch: "p-trophies", dur: 5, fixed: true, boardGlow: 1,
    build(r) {
      const md = ME.medals || {}, cs = ME.casino;
      r.append(h("div.scrim-mid"),
        block(230, {}, kicker("YOUR TROPHIES"),
          ME.awards.length
            ? h("div.col", { style: { gap: "14px", marginTop: "26px" } }, ME.awards.slice(0, 5).map((a, k) =>
              an(h("div.lrow.listpanel", { style: { padding: "16px 22px" } }, icon("trophy", 44), h("div.h3", a)), "left", 0.4 + k * 0.15, 0.6)))
            : HIGH.length > 4
              ? h("div.col", { style: { gap: "14px", marginTop: "26px" } }, HIGH.slice(4, 7).map((x, k) =>
                an(h("div.lrow.listpanel", { style: { padding: "16px 22px" } }, icon("trophy", 44),
                  h("div.h3", `Top ${Math.max(1, 100 - x.pct)}% for ${HL[x.k][1].toLowerCase()}`)), "left", 0.4 + k * 0.15, 0.6)))
              : line("No awards this year. Next year's yours.", 0.4, "h3")),
        block(1010, {}, kicker("YOUR BADGES", 1.6),
          h("div.row", { style: { gap: "34px", marginTop: "24px" } }, ["Gold", "Silver", "Bronze", "Secret"].map((t, k) =>
            an(h("div.center", medalIcon(t, 96), h("div.big", { style: { fontSize: "70px", marginTop: "10px" } }, String(md[t.toLowerCase()] || 0)),
              h("div.tiny", t.toUpperCase())), "pop", 1.8 + k * 0.1, 0.5)))),
        block(1480, {},
          ME.hof ? line(`${ME.hof} of your messages made the Hall of Fame`, 2.4, "h3") : null,
          cs ? line(`${fmt(cs.games)} casino games, ${cs.net >= 0 ? "up" : "down"} ${fmt(Math.abs(cs.net))} UKPence`, 2.6, "h3") : null));
    },
  });

  scene({
    id: "p-card", ch: "outro", dur: 7, fixed: true, boardGlow: 1.2, pout: "radial",
    build(r) {
      r.append(h("div.scrim-mid"),
        h("div.abs", { style: { left: "54px", top: "190px", width: "1080px", height: "1350px", transformOrigin: "top left", transform: "scale(.9)" } },
          an(h("div", card(0.6)), "pop", 0.2, 0.8)),
        h("div.abs.center", { style: { left: 0, right: 0, top: "1490px" } },
          an(h("div.kick", { style: { justifyContent: "center", color: "var(--white)", fontSize: "30px" } },
            FULL ? `SEE YOU IN ${YEAR + 1}` : "THE YEAR ISN'T OVER YET"), "up", 1.6, 0.7),
          line("/wrapped to get yours", 2.2, "h3")));
    },
  });
}
