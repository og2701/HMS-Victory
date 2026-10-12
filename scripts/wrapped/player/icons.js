// Hand-drawn pixel icons for the awards: one character per pixel, "." is empty.
const ICON_PAL = {
  w: "#f4f6ff", k: "#2b3050", y: "#ffc23d", o: "#ff7a2e", r: "#ff2e4d", b: "#2d5bff", c: "#38d9ff",
  p: "#ff8fc4", v: "#a86bff", g: "#1fe08f", n: "#c98a4b", s: "#a3abd0",
};
const ICONS = {
  night_owl: [
    "....yyyy....", "..yyyyy.....", ".yyyy.......", ".yyy......w.", "yyy.........", "yyy.....w...",
    "yyy.........", "yyyy........", ".yyyy.......", ".yyyyyy...y.", "..yyyyyyyy..", "....yyyy....",
  ],
  early_bird: [
    ".....yy.....", ".y...yy...y.", "..y......y..", "....oooo....", "...oyyyyo...", "yy.oyyyyo.yy",
    "yy.oyyyyo.yy", "...oyyyyo...", "....oooo....", "..y......y..", ".y...yy...y.", ".....yy.....",
  ],
  waffler: [
    "wwwwwwwwwwww", "w..........w", "w.cccccccc.w", "w..........w", "w.ccccccccc.", "w..........w",
    "w.cccccc...w", "w..........w", "wwwwwwwwwwww", "...ww.......", "..ww........", ".w..........",
  ],
  silent: [
    "............", "............", "..wwwwwwww..", ".w........w.", ".w..cc....w.", ".w..cc....w.",
    ".w........w.", "..wwwwwwww..", "...ww.......", "..w.........", "............", "............",
  ],
  shouter: [
    "..........r.", "........rrr.", "......rrwrr.", "..wwrrwwwrr.", ".wwwrwwwwrr.", ".wwwrwwwwrr.",
    ".wwwrwwwwrr.", "..wwrrwwwrr.", "...ww.rrwrr.", "...ww...rrr.", "..........r.", "............",
  ],
  nosey: [
    "...yyyyyy...", "..yyyyyyyy..", "..yy....yy..", "........yy..", ".......yyy..", ".....yyyy...",
    "....yyy.....", "....yy......", "....yy......", "............", "....yy......", "....yy......",
  ],
  british: [
    "............", "...w..w.....", "....w..w....", "...w..w.....", ".wwwwwwwww..", ".wbbrrbbwwww",
    ".wrrrrrrw..w", ".wbbrrbbw..w", ".wwwwwwwwwww", "..wwwwwww...", ".sssssssss..", "............",
  ],
  apologist: [
    "............", ".......pppp.", "......pppppp", ".....ppwpwpp", "....ppwwwwpp", "...ppwpwpwp.",
    "..ppwwwwpp..", ".ppwpwpwp...", "ppwwwwpp....", "pppppp......", ".pppp.......", "............",
  ],
  perfectionist: [
    "..........rr", ".........rrr", "........yyr.", ".......yyy..", "......yyy...", ".....yyy....",
    "....yyy.....", "...yyy......", "..nyy.......", "..nn........", ".k..........", "............",
  ],
  fastest: [
    ".......yy...", "......yy....", ".....yy.....", "....yy......", "...yyyyyy...", "......yy....",
    ".....yy.....", "....yy......", "...yy.......", "..yy........", ".y..........", "............",
  ],
  media: [
    "............", "....kkkk....", ".wwwwwwwwww.", ".wwwkkkkwww.", ".wwkcccckww.", ".wwkcwwckww.",
    ".wwkcwwckww.", ".wwkcccckww.", ".wwwkkkkwww.", ".wwwwwwwwww.", "............", "............",
  ],
  plug: [
    "...s....s...", "...s....s...", "..wwwwwwww..", "..wwwwwwww..", "..wwwwwwww..", "...wwwwww...",
    "....wwww....", ".....ww.....", ".....ww.....", ".....yy.....", "....yy......", "...yy.......",
  ],
  voice: [
    "....wwww....", "...wwkkww...", "...wkwwkw...", "...wwkkww...", "...wkwwkw...", "...wwwwww...",
    ".s.wwwwww.s.", ".s..wwww..s.", "..s......s..", "...ssssss...", ".....ss.....", "...ssssss...",
  ],
  emoji: [
    "............", "...yyyyyy...", "..yyyyyyyy..", ".yyykyykyyy.", ".yyykyykyyy.", ".yyyyyyyyyy.",
    ".yykyyyykyy.", ".yyykkkkyyy.", "..yyyyyyyy..", "...yyyyyy...", "............", "............",
  ],
  hype: [
    ".....r......", "....rr......", "....rro.....", "...rroo..r..", "...rooo.rr..", "..rrooyorr..",
    "..roooyyor..", ".rroyyyyyorr", ".rooyyyyyyor", ".rooywwwyyor", "..rooywwyor.", "...rrooorr..",
  ],
  crowd: [
    "............", ".y...yy...y.", ".yy..yy..yy.", ".yyy.yy.yyy.", ".yyyyyyyyyy.", ".yyrryybbyy.",
    ".yyrryybbyy.", ".yyyyyyyyyy.", ".oooooooooo.", "............", "............", "............",
  ],
  celeb: [
    ".....yy.....", ".....yy.....", "....yyyy....", "yyyyyyyyyyyy", ".yyyyyyyyyy.", "..yyyyyyyy..",
    "...yyyyyy...", "..yyyyyyyy..", "..yyy..yyy..", ".yyy....yyy.", ".yy......yy.", "............",
  ],
  gossip: [
    "............", "...cccccc...", "..cc....cc..", ".cc..cccc.c.", ".c..cc..c.c.", ".c.cc...c.c.",
    ".c.cc..cc.c.", ".c..cccc.cc.", ".cc.........", "..cc....cc..", "...cccccc...", "............",
  ],
  tourist: [
    "....rrrr....", "...rrrrrr...", "..rrrwwrrr..", "..rrwwwwrr..", "..rrwwwwrr..", "..rrrwwrrr..",
    "...rrrrrr...", "....rrrr....", ".....rr.....", ".....rr.....", "............", "....ssss....",
  ],
  glue: [
    "............", "vvvvvvv.....", "v.....v.....", "v.....v.....", "v...ppppppp.", "v...p.v...p.",
    "vvvvpvv...p.", "....p.....p.", "....p.....p.", "....ppppppp.", "............", "............",
  ],
  bot_friend: [
    ".....rr.....", ".....ss.....", ".ssssssssss.", ".sccssssccs.", ".sccssssccs.", ".ssssssssss.",
    ".sskkkkkkss.", ".ssssssssss.", "...ssssss...", "..ssssssss..", ".ss.ssss.ss.", "....ss.ss...",
  ],
  killer: [
    "............", "...wwwwww...", "..wwwwwwww..", ".wwwwwwwwww.", ".wkkwwwwkkw.", ".wkkwwwwkkw.",
    ".wwwwkkwwww.", "..wwwwwwww..", "...wkwkwk...", "...wwwwww...", "............", "............",
  ],
  starter: [
    "....g.......", "....gg......", "....ggg.....", "....gggg....", "....ggggg...", "....gggggg..",
    "....ggggg...", "....gggg....", "....ggg.....", "....gg......", "....g.......", "............",
  ],
  committed: [
    "............", "..r......r..", ".rrrrrrrrrr.", ".rrrrrrrrrr.", ".wwwwwwwwww.", ".wkwkwkwkww.",
    ".wwwwwwwwww.", ".wkwkwkwkww.", ".wwwwwwwwww.", ".wkwkwkwwww.", ".wwwwwwwwww.", "............",
  ],
  rookie: [
    "............", "...gg..gg...", "..gggggggg..", "...gg.gg....", ".....g......", ".....g......",
    "....nnnn....", "...nnnnnn...", "...nnnnnn...", "....nnnn....", "............", "............",
  ],
  glowup: [
    "............", ".........ggg", "..........gg", ".........g.g", "........g...", "...g...g....",
    "..g.g.g.....", ".g...g......", "g...........", "............", "............", "............",
  ],
  chips: [
    "............", "..gggggggg..", ".gwgwgwgwgg.", "..gggggggg..", "..wwwwwwww..", ".wgwgwgwgww.",
    "..wwwwwwww..", "..gggggggg..", ".gwgwgwgwgg.", "..gggggggg..", "............", "............",
  ],
  clover: [
    "............", "...gg..gg...", "..gggggggg..", "..gggggggg..", "...gggggg...", ".gggg..gggg.",
    "gggggggggggg", "gggggggggggg", ".gggg..gggg.", ".....nn.....", "......nn....", "............",
  ],
  trophy: [
    "............", ".yyyyyyyyyy.", "yyyyyyyyyyyy", "y.yyyyyyyy.y", "y.yyyyyyyy.y", ".yyyyyyyyyy.",
    "...yyyyyy...", "....yyyy....", ".....yy.....", "....yyyy....", "...oooooo...", "............",
  ],
  eye_open: [
    "....rrrrrrrr....", "..rrwwwwwwwwrr..", ".rwwwwrrrrwwwwr.", "rwwwwrrkkrrwwwwr", "rwwwwrkkkkrwwwwr",
    "rwwwwrrkkrrwwwwr", ".rwwwwrrrrwwwwr.", "..rrwwwwwwwwrr..", "....rrrrrrrr....",
  ],
  lunch: [
    "............", "............", "...nnnnnn...", "..nnnnnnnn..", ".gggggggggg.", ".rrrrrrrrrr.",
    ".yyyyyyyyyy.", ".nnnnnnnnnn.", "..nnnnnnnn..", "............", "............", "............",
  ],
  weekend: [
    "............", "............", "..vvvvvvvv..", "..vvvvvvvv..", ".vvvvvvvvvv.", "vvppppppppvv",
    "vvppppppppvv", "vvvvvvvvvvvv", ".vv......vv.", ".s........s.", "............", "............",
  ],
  one_day: [
    "....ssss....", ".....ww.....", "...wwwwww...", "..ww....ww..", ".w...r....w.", ".w...r....w.",
    ".w...rrr..w.", ".w........w.", "..ww....ww..", "...wwwwww...", "............", "............",
  ],
  sticker: [
    "............", ".yyyyyyyy...", ".yyyyyyyyy..", ".yykyykyyyy.", ".yyyyyyyyyy.", ".yykyyyykyy.",
    ".yyykkkkyyw.", ".yyyyyyyyww.", ".yyyyyyywww.", "..yyyyyywww.", "............", "............",
  ],
  comedian: [
    "............", "...yyyyyy...", "..yyyyyyyy..", ".ykkyyyykky.", ".yyyyyyyyyy.", "cykkkkkkkkyc",
    "cykwwwwwwkyc", ".yykrrrrkyy.", "..yyykkyyy..", "...yyyyyy...", "............", "............",
  ],
  heartthrob: [
    "............", "..rrr..rrr..", ".rrrrrrrrrr.", ".rwrrrrrrrr.", ".rrrrrrrrrr.", ".rrrrrrrrrr.",
    "..rrrrrrrr..", "...rrrrrr...", "....rrrr....", ".....rr.....", "............", "............",
  ],
  meme: [
    "............", "wwwwwwwwwwww", "w..........w", "w.y........w", "w......g...w", "w.....ggg..w",
    "w..g.ggggg.w", "w.gggggggggw", "wwwwwwwwwwww", "............", "............", "............",
  ],
  hall_of_famer: [
    ".yyyyyyyyyy.", ".y........y.", ".y...ww...y.", ".y..wwww..y.", ".y.wwwwww.y.", ".y..wwww..y.",
    ".y..w..w..y.", ".y........y.", ".yyyyyyyyyy.", "....yyyy....", "...oooooo...", "............",
  ],
  welcomer: [
    "............", ".....y......", "..y..y.y....", "..y.yy.y....", "..yyyyyy.y..", "..yyyyyyyy..",
    "..yyyyyyy...", "..yyyyyyy...", "...yyyyy....", "....yyy.....", "............", "............",
  ],
  wordle: [
    "............", ".ggg.yyy.sss", ".ggg.yyy.sss", ".ggg.yyy.sss", "............", ".ggg.ggg.yyy",
    ".ggg.ggg.yyy", ".ggg.ggg.yyy", "............", ".ggg.ggg.ggg", ".ggg.ggg.ggg", ".ggg.ggg.ggg",
  ],
  bump: [
    "............", ".....gg.....", "....gggg....", "...gggggg...", "..gggggggg..", ".gggggggggg.",
    "....gggg....", "....gggg....", "....gggg....", "....gggg....", "............", "............",
  ],
  tree: [
    ".....gg.....", "....gggg....", "...gggggg...", "..gggggggg..", "...gggggg...", "..gggggggg..",
    ".gggggggggg.", ".....nn.....", ".....nn.....", "...nnnnnn...", "............", "............",
  ],
  oracle: [
    "............", "....vvvv....", "..vvcvvvvv..", ".vvcwvvvvvv.", ".vvvvvvvvvv.", ".vvvvvvvpvv.",
    ".vvvvvvpvvv.", "..vvvvvvvv..", "....vvvv....", "..nnnnnnnn..", ".nnnnnnnnnn.", "............",
  ],
  roasted: [
    "............", ".......www..", "......wwww..", ".....nnww...", "...nnnnn....", "..nnonnnn...",
    ".nnnnnonn...", ".nnonnnnn...", ".nnnnnnn....", "..nnnnn.....", "............", "............",
  ],
  argument: [
    "............", "wwwwwww.....", "wrrrrrw.....", "wwwwwww.....", ".w..........", "............",
    ".....ccccccc", ".....cbbbbbc", ".....ccccccc", "..........c.", "............", "............",
  ],
  tag_team: [
    "............", "..rr....cc..", ".rrrr..cccc.", "rrrrrrcccccc", "..rr....cc..", "..rr....cc..",
    "..rr....cc..", "..rr....cc..", "............", "............", "............", "............",
  ],
  same_schedule: [
    "............", "...wwwwww...", "..w......w..", ".w...c....w.", ".w...c....w.", ".w...cccc.w.",
    ".w........w.", "..w......w..", "...wwwwww...", "............", "............", "............",
  ],
  ping_pong: [
    "............", ".rrrr.......", "rrrrrr......", "rrrrrr...ww.", "rrrrrr...ww.", ".rrrr.......",
    "...nn.......", "....nn......", ".....nn.....", "............", "............", "............",
  ],
  rivals: [
    "s..........s", ".s........s.", "..s......s..", "...s....s...", "....s..s....", ".....ss.....",
    ".....ss.....", "....s..s....", "...n....n...", "..n......n..", "............", "............",
  ],
  book: [
    "............", "............", ".wwwww.wwwww", ".wsssw.wsssw", ".wwwww.wwwww", ".wsssw.wsssw",
    ".wwwww.wwwww", ".wsssw.wsssw", ".wwwww.wwwww", ".bbbbbbbbbbb", "............", "............",
  ],
  thumbs: [
    "............", "....yy......", "...yyy......", "...yyy......", "..yyyy......", "yyyyyyyyyy..",
    "yyyyyyyyyy..", "y.yyyyyyyy..", "yyyyyyyyyy..", "y.yyyyyyyy..", "yyyyyyyyy...", "............",
  ],
  crying: [
    "............", "...yyyyyy...", "..yyyyyyyy..", ".yykkyykkyy.", ".yccyyyyccy.", ".ycyyyyyycy.",
    ".ycykkkkycy.", ".ycykrrkycy.", "..cyyyyyyc..", "...yyyyyy...", "............", "............",
  ],
  coin: [
    "............", "...yyyyyy...", "..yoooooyy..", ".yoyyyyyoyy.", ".yoyyoyyoyy.", ".yoyoooyoyy.",
    ".yoyyoyyoyy.", ".yoyyyyyoyy.", "..yoooooyy..", "...yyyyyy...", "............", "............",
  ],
  // the asterisk that comes straight after a typo
  typo: [
    "............", ".....yy.....", ".y...yy...y.", "..yy.yy.yy..", "...yyyyyy...", "....yyyy....",
    "...yyyyyy...", "..yy.yy.yy..", ".y...yy...y.", ".....yy.....", "............", "............",
  ],
  // three messages stacked up from the same side, no reply in between
  monologue: [
    "cccccccc....", "cwwwwwwc....", "cccccccc....", ".c..........", "cccccccccc..", "cwwwwwwwwc..",
    "cccccccccc..", ".c..........", "ccccccccccc.", "cwwwwwwwwwc.", "ccccccccccc.", ".c..........",
  ],
  // a phone still lit up under "zz"
  fake_gn: [
    "........ww..", ".........w..", "........ww..", "..sssssss...", "..scccccs...", "..scwwwcs...",
    "..scccccs...", "..scwwccs...", "..scccccs...", "..scccccs...", "..sssssss...", "....sss.....",
  ],
  // a hand mirror with a heart in it
  own_fan: [
    "...ssssss...", "..s......s..", ".s..r..r..s.", ".s.rrrrrr.s.", ".s.rrrrrr.s.", ".s..rrrr..s.",
    ".s...rr...s.", "..s......s..", "...ssssss...", ".....nn.....", ".....nn.....", ".....nn.....",
  ],
};
const icon = (key, size) => pixelIcon(ICONS[key] || ICONS.celeb, ICON_PAL, size);

// A little crowd for the "people who posted" tile: one sprite, five outfits.
const PERSON = ["..hhhh..", "..hhhh..", "..ffff..", "..ffff..", "...ff...", ".cccccc.", "cccccccc", "c.cccc.c",
  "c.cccc.c", "..c..c..", "..c..c..", "..k..k.."];
const OUTFITS = [["#3a2a1a", "#f2c9a0", "#2d5bff"], ["#ffd23f", "#e8b48a", "#ff2e4d"], ["#111111", "#8d5a3b", "#1fe08f"],
  ["#c2410c", "#f6d2b8", "#a86bff"], ["#6b4a2b", "#d9a07a", "#f4f6ff"]];
function crowdArt() {
  return OUTFITS.map(([hair, face, cloth]) => pixelIcon(PERSON, { h: hair, f: face, c: cloth, k: "#2b3050" }, 52));
}

// The brand mark: a tiny pixel Union Jack (three coloured squares read as the French flag).
function ujIcon(size) {
  const rows = [];
  for (let j = 0; j < 12; j++) {
    let row = "";
    for (let i = 0; i < 24; i++) {
      const c = unionJack((i + 0.5) / 24, (j + 0.5) / 12);
      row += c === UJ.blue ? "b" : c === UJ.white ? "w" : "r";
    }
    rows.push(row);
  }
  return pixelIcon(rows, { b: "#1d3a9e", w: "#f4f6ff", r: "#e0102e" }, size);
}

// Badge medals, one per tier, on a red-and-blue ribbon. The secret one is dark with a question mark and
// a rim that runs through the rainbow, like the secret badge ring on the rank cards.
const MEDAL = [
  "bbb......rrr", ".bbb....rrr.", "..bbb..rrr..", "...bbbrrr...", "...dddddd...", "..dmmmmmmd..",
  ".dmlmmwmmmd.", "dmlmmmwmmmmd", "dmmwwwwwwwmd", "dmmmwwwwwmmd", "dmmmmwwwmmmd", "dmmmwwmwwmmd",
  ".dmmwmmmwmd.", "..dmmmmmmd..", "...dddddd...",
];
const MEDAL_PAL = {
  Gold: { m: "#ffc23d", d: "#a8700c", l: "#fff1b0", w: "#fff7d6" },
  Silver: { m: "#c0c6cf", d: "#69728a", l: "#f4f6ff", w: "#ffffff" },
  Bronze: { m: "#c98b4e", d: "#74441d", l: "#f0c08f", w: "#ffd9b0" },
};
const QMARK = [[6, 5], [6, 6], [6, 7], [7, 4], [7, 8], [8, 8], [9, 7], [10, 6], [12, 6]];
const RAINBOW = ["#ff2e4d", "#ff7a2e", "#ffc23d", "#1fe08f", "#38d9ff", "#a86bff"];
function medalIcon(tier, size) {
  const ribbon = { b: "#2d5bff", r: "#e0102e" };
  if (tier !== "Secret") return pixelIcon(MEDAL, { ...MEDAL_PAL[tier] || MEDAL_PAL.Bronze, ...ribbon }, size);
  const rows = MEDAL.map((row, y) => [...row].map((c, x) => {
    if (c === "d") return String(Math.floor(((Math.atan2(y - 9, x - 5.5) / Math.PI + 1) / 2) * 6) % 6);
    if (c === "m" || c === "l" || c === "w") return QMARK.some(([qy, qx]) => qy === y && qx === x) ? "w" : "m";
    return c;
  }).join(""));
  const pal = { ...ribbon, m: "#1a1f3d", w: "#f4f6ff" };
  RAINBOW.forEach((c, i) => { pal[i] = c; });
  return pixelIcon(rows, pal, size);
}
