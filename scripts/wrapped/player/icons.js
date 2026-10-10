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
  coin: [
    "............", "...yyyyyy...", "..yoooooyy..", ".yoyyyyyoyy.", ".yoyyoyyoyy.", ".yoyoooyoyy.",
    ".yoyyoyyoyy.", ".yoyyyyyoyy.", "..yoooooyy..", "...yyyyyy...", "............", "............",
  ],
};
const icon = (key, size) => pixelIcon(ICONS[key] || ICONS.celeb, ICON_PAL, size);
