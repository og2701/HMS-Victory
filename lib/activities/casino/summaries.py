"""Each casino game's own picture of a finished sitting (the card left in #casino when someone
plays several rounds and leaves the table).

Every game draws in its own world, matching its activity screen, and its centrepiece shows how
the sitting actually went: every Plinko ball's path into its barrel, every Glass Bridge
crossing pane by pane, every dart in the board, every kick in the goal, every blackjack hand
against the dealer, and so on.

That needs a little more than the money from each round, so ``digest(key, view)`` boils the
finished table down to what its picture needs; sessions.record keeps it with the round as "d".
``summary_html`` returns None when a sitting has too few digests to draw (rounds from before
this existed, or a digest that failed), and the caller falls back to the plain summary.
"""

from __future__ import annotations

import html
import logging
import math
import random
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / "data" / "activity_casino"
EMOJI = ROOT / "data" / "emoji"
COIN = ROOT / "data" / "ukpence-small.svg"
FONTS = ROOT / "data" / "fonts"

esc = html.escape


# ---- what each round keeps ---------------------------------------------------------------

def _d_plinko(v):
    return {"slot": int(v["slot"]), "risk": v["risk"], "mult": float(v["mult"]),
            "path": "".join("1" if b else "0" for b in v["path"])}


def _d_glass(v):
    return {"step": int(v["step"]), "steps": int(v["steps"]), "fell": v.get("outcome") != "win",
            "mults": [float(m) for m in v.get("multipliers", [])]}


def _d_mines(v):
    return {"tiles": int(v["tiles"]), "cols": int(v["cols"]), "revealed": [int(i) for i in v.get("revealed", [])],
            "hit": v.get("hit") if isinstance(v.get("hit"), int) and not isinstance(v.get("hit"), bool) else None,
            "mult": float(v.get("now") or 0), "won": v.get("outcome") == "win"}


def _d_chest(v):
    return {"tier": int(v["tier"]), "won": v.get("outcome") == "win",
            "names": [t["name"] for t in v.get("tiers", [])], "mults": [float(t["mult"]) for t in v.get("tiers", [])]}


def _d_blockade(v):
    return {"mult": float(v["mult"]), "sunk": v.get("state") == "busted"}


def _d_darts(v):
    return {"throws": [t["label"] for t in v.get("throws", [])], "total": int(v["total"]),
            "bust": int(v["total"]) > int(v.get("bust", 60))}


def _d_penalty(v):
    return {"kicks": [list(k) for k in v.get("kicks") or []], "goals": int(v["goals"]),
            "won": v.get("outcome") == "win", "shots": int(v.get("shots", 5))}


def _d_pennyfalls(v):
    return {"fed": int(v.get("dropped", 0)), "won": int(v.get("won", 0)), "golds": int(v.get("goldsWon", 0))}


def _d_roulette(v):
    return {"n": int(v["number"])}


def _d_slots(v):
    return {"reels": list(v["reels"]), "mult": float(v.get("mult") or 0)}


def _d_blackjack(v):
    return {"you": int(v["playerTotal"]), "dealer": int(v["dealerTotal"]), "outcome": v.get("outcome"),
            "doubled": bool(v.get("doubled")), "player": list(v["player"]), "dealerCards": [c for c in v["dealer"] if c]}


def _d_higherlower(v):
    return {"steps": int(v["steps"]), "won": v.get("outcome") == "win", "mult": float(v.get("cumulative") or 0),
            "run": list(v.get("run", []))}


def _d_videopoker(v):
    return {"cards": list(v["cards"]), "row": v.get("row"), "hand": v.get("hand", ""), "pays": int(v.get("pays") or 0)}


def _d_reddog(v):
    return {"cards": [v["first"], v["second"], v["third"]], "spread": v.get("spread"), "pair": bool(v.get("pair")),
            "consecutive": bool(v.get("consecutive")), "outcome": v.get("outcome"), "odds": v.get("odds")}


def _d_tcp(v):
    return {"player": list(v["player"]), "dealer": [c for c in v.get("dealer", []) if c], "hand": v.get("playerHand", ""),
            "outcome": v.get("outcome"), "bonus": int(v.get("bonus") or 0)}


DIGESTS = {
    "plinko": _d_plinko, "glass": _d_glass, "mines": _d_mines, "chest": _d_chest, "blockade": _d_blockade,
    "darts": _d_darts, "penalty": _d_penalty, "pennyfalls": _d_pennyfalls, "roulette": _d_roulette,
    "slots": _d_slots, "blackjack": _d_blackjack, "higherlower": _d_higherlower, "videopoker": _d_videopoker,
    "reddog": _d_reddog, "tcp": _d_tcp,
}


def digest(key: str, view: dict | None) -> dict | None:
    """What this game's summary picture needs from a finished round, or None."""
    fn = DIGESTS.get(key)
    if not fn or not view:
        return None
    try:
        return fn(view)
    except Exception:
        log.warning("couldn't digest a finished %s round", key, exc_info=True)
        return None


# ---- shared bits ---------------------------------------------------------------------------

_HEAD = ("<!doctype html><html><head><meta charset='utf-8'><style>"
         "@import url('https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..900"
         "&family=DM+Serif+Display&family=Josefin+Sans:wght@400;600;700&family=Caveat:wght@600;700"
         "&family=Bungee&family=VT323&display=block');"
         f"@font-face{{font-family:'ArchivoLocal';src:url('file://{FONTS / 'Archivo.ttf'}') format('truetype');font-weight:100 900}}"
         f"@font-face{{font-family:'SerifLocal';src:url('file://{FONTS / 'DMSerifDisplay.ttf'}') format('truetype')}}")
_BASE = """*{margin:0;box-sizing:border-box} html,body{background:transparent} body{width:820px;font-family:Archivo,ArchivoLocal,sans-serif}
.card{width:820px;padding:34px 30px 30px;border-radius:28px;position:relative;overflow:hidden}
.head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}
.who{text-align:right;font-weight:800;font-size:16px;letter-spacing:.16em;line-height:26px;white-space:nowrap;padding-top:6px}
.net{display:flex;align-items:baseline;gap:14px;margin:22px 2px 20px} .net span{font-weight:800;font-size:18px;letter-spacing:.18em}
.stats{display:flex;gap:12px;margin-top:20px} .s{flex:1;min-width:0;padding:14px 16px 13px;border-radius:14px}
.s span{display:block;font-weight:800;font-size:13px;letter-spacing:.18em} .s b{display:block;font-size:34px;line-height:40px;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.s small{display:block;font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pc{width:var(--w,84px);height:calc(var(--w,84px)*1.4);border-radius:calc(var(--w,84px)*.1);background:#FBFAF6;position:relative;flex:none;
 box-shadow:0 8px 18px rgba(0,0,0,.4);font-family:'DM Serif Display',SerifLocal,serif}
.pc .i{position:absolute;left:9%;top:6%;display:flex;flex-direction:column;align-items:center;gap:2px;font-size:calc(var(--w,84px)*.3);line-height:1;letter-spacing:-.04em}
.pc .m{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding-top:16%;padding-left:8%}
.serif{font-family:'DM Serif Display',SerifLocal,serif;font-weight:400}
"""


def _page(css: str, body: str) -> str:
    return f"{_HEAD}{_BASE}{css}</style></head><body>{body}</body></html>"


def _f(path: Path) -> str:
    return f"file://{path}"


def sgn(n: int) -> str:
    return f"+{n:,}" if n > 0 else (f"−{abs(n):,}" if n < 0 else "0")


def _x(m: float) -> str:
    return f"{m:.2f}×" if m < 10 else f"{m:.1f}×"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


_SUIT = {
    "H": "M12 21.5C9 19 2.5 14.6 2.5 9.2 2.5 5.9 5 3.5 8 3.5c1.8 0 3.2.9 4 2.3.8-1.4 2.2-2.3 4-2.3 3 0 5.5 2.4 5.5 5.7 0 5.4-6.5 9.8-9.5 12.3z",
    "D": "M12 1.5 20.5 12 12 22.5 3.5 12z",
    "S": "M12 1.5C9.5 5 3 8.6 3 13.4c0 2.7 2.1 4.6 4.6 4.6 1.4 0 2.6-.6 3.4-1.6L10 22h4l-1-5.6c.8 1 2 1.6 3.4 1.6 2.5 0 4.6-1.9 4.6-4.6C21 8.6 14.5 5 12 1.5z",
    "C": "M12 2a4.3 4.3 0 0 0-3.9 6.1A4.3 4.3 0 1 0 10.6 15L9.6 22h4.8l-1-7a4.3 4.3 0 1 0 2.5-6.9A4.3 4.3 0 0 0 12 2z",
}


def _suit(s: str, size: int, colour: str) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" style="display:block">'
            f'<path d="{_SUIT.get(s, _SUIT["S"])}" fill="{colour}"/></svg>')


def pc(code: str, w: int = 84) -> str:
    """A playing card from a two-character code ("AS", "TH")."""
    if not code or len(code) < 2:
        return f'<div class="pc" style="--w:{w}px;background:#24324A"></div>'
    rank, s = code[:-1], code[-1]
    rank = "10" if rank == "T" else rank
    col = "#D02B2B" if s in "HD" else "#16181C"
    return (f'<div class="pc" style="--w:{w}px;color:{col}"><div class="i">{esc(rank)}{_suit(s, int(w * .2), col)}</div>'
            f'<div class="m">{_suit(s, int(w * .5), col)}</div></div>')


def _who(n: int, unit: str, minutes: int) -> str:
    one = unit.rstrip("s")
    count = f"{n} {(unit if n != 1 else one).upper()}"
    return f"{count} · {minutes} MIN" if minutes else count


def _stat(label: str, value: str, small: str = "", style: str = "") -> str:
    return f'<div class="s"><span>{esc(label)}</span><b style="{style}">{value}</b><small>{esc(small) if small else "&nbsp;"}</small></div>'


def _net(net: int, colour_up: str, colour_down: str, cls: str = "") -> str:
    col = colour_up if net > 0 else colour_down if net < 0 else "inherit"
    return f'<div class="net"><b class="{cls}" style="color:{col}">{sgn(net)}</b><span>UKP FOR THE SESSION</span></div>'


class _Sitting:
    """The rounds of one sitting, with the sums every card wants."""

    def __init__(self, history: list[dict], unit: str, minutes: int):
        self.all = history
        self.rounds = [h for h in history if isinstance(h.get("d"), dict)]
        self.unit = unit
        self.minutes = minutes
        self.net = sum(int(h["net"]) for h in history)
        self.wagered = sum(int(h.get("staked", 0)) for h in history)
        self.won = sum(1 for h in history if int(h["net"]) > 0)
        self.lost = sum(1 for h in history if int(h["net"]) < 0)

    @property
    def who(self) -> str:
        return _who(len(self.all), self.unit, self.minutes)

    def best(self):
        return max(self.rounds, key=lambda h: int(h["net"]))

    def worst(self):
        return min(self.rounds, key=lambda h: int(h["net"]))


# ---- Plinko: every ball's way down the ship's board ------------------------------------------

_PLINKO_TABLES = {
    "low": [8, 3, 1.5, 1.3, 1.1, 1, 0.5, 1, 1.1, 1.3, 1.5, 3, 8],
    "medium": [24, 9, 4, 2, 1.1, 0.6, 0.3, 0.6, 1.1, 2, 4, 9, 24],
    "high": [150, 22, 8, 2, 0.7, 0.2, 0.2, 0.2, 0.7, 2, 8, 22, 150],
}


def _plinko_board(rounds: list[dict], table: list[float], best_slot: int) -> str:
    rows = len(table) - 1
    W, DX, DY, TOP = 343, 343 / (rows + 2), 21, 44
    SLOT_Y = TOP + rows * DY - 2
    H = SLOT_Y + 48
    PR, BR = 3.7, 10
    peg = lambda r, j: (W / 2 + (j - (r + 2) / 2) * DX, TOP + r * DY)
    slot_x = lambda i: W / 2 + (i - rows / 2) * DX

    def pos(path, s):
        def cx(k):
            return W / 2 + sum((0.5 if c == "1" else -0.5) * DX for c in path[:k])

        def cy(k):
            return TOP + k * DY - (BR + PR) + 2
        if s < 1:
            y0 = TOP - DY * 1.9
            return cx(0), y0 + (cy(0) - y0) * s * s
        k, u = int(s) - 1, s - int(s)
        fx, tx, fy, ty = cx(k), cx(k + 1), cy(k), cy(k + 1)
        return fx + (tx - fx) * (1 - (1 - u) ** 2), fy + (ty - fy) * u * u - DY * 0.42 * 4 * u * (1 - u)

    o = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="display:block">']
    o.append("""<defs>
<radialGradient id="peg" cx=".36" cy=".3" r=".75"><stop offset="0" stop-color="#FFF3C8"/><stop offset=".45" stop-color="#D6A54A"/><stop offset="1" stop-color="#5E3B05"/></radialGradient>
<filter id="soft" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="1.5"/></filter>
<filter id="blur3" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="3"/></filter>
<filter id="glow" x="-150%" y="-150%" width="400%" height="400%"><feGaussianBlur stdDeviation="6"/></filter>
<linearGradient id="wood" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#8A4422"/><stop offset=".5" stop-color="#5E2A13"/><stop offset="1" stop-color="#3A170A"/></linearGradient>
<radialGradient id="lamp" cx=".45" cy=".2" r=".8"><stop offset="0" stop-color="#FFC08A" stop-opacity=".3"/><stop offset="1" stop-color="#FFC08A" stop-opacity="0"/></radialGradient>
<linearGradient id="brass" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#FFE6A0"/><stop offset=".4" stop-color="#C9963F"/><stop offset=".75" stop-color="#7A4E0E"/><stop offset="1" stop-color="#E8C374"/></linearGradient>
<linearGradient id="stave" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#5A2C0E"/><stop offset=".22" stop-color="#B06C34"/><stop offset=".45" stop-color="#E6A866"/><stop offset=".7" stop-color="#B06C34"/><stop offset="1" stop-color="#4A2208"/></linearGradient>
<linearGradient id="iron" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#1A1A1E"/><stop offset=".4" stop-color="#6B6E78"/><stop offset=".6" stop-color="#8E929C"/><stop offset="1" stop-color="#1A1A1E"/></linearGradient>
<linearGradient id="hoop" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#5E3B05"/><stop offset=".4" stop-color="#E8C374"/><stop offset=".6" stop-color="#FFF1C2"/><stop offset="1" stop-color="#5E3B05"/></linearGradient>
<linearGradient id="plank" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#A86A3A"/><stop offset=".5" stop-color="#7A4420"/><stop offset="1" stop-color="#4A2410"/></linearGradient>
</defs>""")
    o.append(f'<rect width="{W}" height="{H}" rx="16" fill="url(#brass)"/><rect x="5" y="5" width="{W - 10}" height="{H - 10}" rx="12" fill="url(#wood)"/>')
    for k in range(12):
        y = 30 + k * 27 + math.sin(k * 7.3) * 4
        a = math.sin(k * 3.1) * 7
        o.append(f'<path d="M5 {y:.1f}C{W * .3:.0f} {y + a:.1f} {W * .62:.0f} {y - a:.1f} {W - 5} {y + a * .4:.1f}" fill="none" stroke="#1E0803" stroke-width="{.6 + (k % 4) * .22:.2f}" opacity="{.22 + (k % 3) * .05:.2f}"/>')
    o.append(f'<rect x="5" y="5" width="{W - 10}" height="{H - 10}" rx="12" fill="url(#lamp)"/>')
    for x, y in ((14, 14), (W - 14, 14), (14, H - 14), (W - 14, H - 14)):
        o.append(f'<circle cx="{x}" cy="{y}" r="3.2" fill="url(#peg)"/>')
    c = W / 2
    o.append(f'<path d="M{c - 26} 0H{c + 26}L{c + 11} {TOP - 26}H{c - 11}Z" fill="url(#brass)"/><path d="M{c - 11} {TOP - 26}H{c + 11}V{TOP - 20}H{c - 11}Z" fill="#2A1205"/>')
    pts = [peg(r, j) for r in range(rows) for j in range(r + 3)]
    for x, y in pts:
        o.append(f'<ellipse cx="{x + 1.3:.1f}" cy="{y + 2.8:.1f}" rx="{PR}" ry="{PR * .62:.1f}" fill="#120500" opacity=".6" filter="url(#soft)"/>')
    for x, y in pts:
        o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{PR}" fill="url(#peg)"/><circle cx="{x - PR * .35:.1f}" cy="{y - PR * .42:.1f}" r="{PR * .3:.2f}" fill="#fff" opacity=".9"/>')
    # every ball's way down (the most recent 40), the best ball brightest
    shown = rounds[-40:]
    faint = max(0.12, 0.45 - len(shown) * 0.008)
    for h in shown:
        d = h["d"]
        path = d["path"]
        if len(path) != rows:
            continue
        tr = [pos(path, s / 10) for s in range(0, rows * 10 + 10)]
        pts_s = " ".join(f"{x:.1f},{y:.1f}" for x, y in tr)
        hot = d["slot"] == best_slot and int(h["net"]) == max(int(r["net"]) for r in shown)
        o.append(f'<polyline points="{pts_s}" fill="none" stroke="#F7D78A" stroke-opacity="{.5 if hot else faint * .6:.2f}" stroke-width="{5 if hot else 4}" stroke-linejoin="round" filter="url(#blur3)"/>')
        o.append(f'<polyline points="{pts_s}" fill="none" stroke="#FFECBE" stroke-opacity="{.95 if hot else faint:.2f}" stroke-width="{1.6 if hot else 1.1}" stroke-linejoin="round"/>')
    # the deck, a glow behind the best barrel, the barrels
    y = SLOT_Y + 24
    o.append(f'<rect x="8" y="{y:.1f}" width="{W - 16}" height="13" rx="3" fill="url(#plank)"/>')
    o.append(f'<ellipse cx="{slot_x(best_slot):.1f}" cy="{SLOT_Y + 8:.1f}" rx="18" ry="16" fill="#F3C76A" opacity=".55" filter="url(#glow)"/>')
    top_mult = max(table)
    for i, m in enumerate(table):
        o.append(_barrel(slot_x(i), SLOT_Y, DX, m, top_mult))
    # a coin on each barrel per ball that landed in it, and the count on a dark tag when it's a
    # pile, so it reads over the pegs and trails
    counts = Counter(h["d"]["slot"] for h in rounds)
    for s, n in counts.items():
        for k in range(min(n, 5)):
            o.append(f'<image href="{_f(COIN)}" x="{slot_x(s) - 7:.1f}" y="{SLOT_Y - 18 - k * 4:.1f}" width="14" height="14"/>')
        if n > 1:
            tw, ty = 9 + 5.6 * len(str(n)), SLOT_Y - 33 - min(n, 5) * 4
            o.append(f'<rect x="{slot_x(s) - tw / 2:.1f}" y="{ty:.1f}" width="{tw:.1f}" height="12" rx="6" fill="#170A03" stroke="#E8C374" stroke-width=".9"/>'
                     f'<text x="{slot_x(s):.1f}" y="{ty + 9.2:.1f}" text-anchor="middle" font-family="Archivo" font-weight="900" font-size="9.5" fill="#FFE6A0">{n}</text>')
    o.append("</svg>")
    return "".join(o)


def _barrel(x: float, slot_y: float, dx: float, m: float, top: float) -> str:
    w = dx - 2.6
    t, b = slot_y - 7, slot_y + 25
    mid = (t + b) / 2
    l0, r0 = x - w / 2 + 1.6, x + w / 2 - 1.6
    big = m >= 8
    plate = "#A3141F" if m >= top else "#8E1B24" if m >= 20 else "#7A1A2C" if m >= 8 else "#2A3D73" if m >= 2 else "#22336A" if m >= 1 else "#172447"
    s = [f'<ellipse cx="{x + 1:.1f}" cy="{b + 1.5:.1f}" rx="{w / 2 + 1:.1f}" ry="3" fill="#000" opacity=".55" filter="url(#soft)"/>',
         f'<path d="M{l0:.1f} {t:.1f}Q{x - w / 2 - 1.4:.1f} {mid:.1f} {l0:.1f} {b:.1f}L{r0:.1f} {b:.1f}Q{x + w / 2 + 1.4:.1f} {mid:.1f} {r0:.1f} {t:.1f}Z" fill="url(#stave)"/>']
    for k in (-0.5, -0.17, 0.17, 0.5):
        sx = x + k * (w - 6)
        s.append(f'<path d="M{sx:.1f} {t + 2:.1f}Q{sx + k * 2.2:.1f} {mid:.1f} {sx:.1f} {b:.1f}" fill="none" stroke="#2A1004" stroke-width=".6" opacity=".55"/>')
    for hy in (t + 4.2, b - 5.2):
        s.append(f'<rect x="{x - w / 2 + .6:.1f}" y="{hy:.1f}" width="{w - 1.2:.1f}" height="2.4" rx="1" fill="url(#{"hoop" if big else "iron"})"/>')
    s.append(f'<ellipse cx="{x:.1f}" cy="{t:.1f}" rx="{(r0 - l0) / 2 + .4:.1f}" ry="3.2" fill="#E6A866"/><ellipse cx="{x:.1f}" cy="{t + .5:.1f}" rx="{(r0 - l0) / 2 - 1.2:.1f}" ry="2.2" fill="#140702"/>')
    pw = w - 5
    lab = f"{m:g}"
    s.append(f'<rect x="{x - pw / 2:.1f}" y="{mid - 5.2:.1f}" width="{pw:.1f}" height="10.4" rx="2" fill="{plate}" stroke="{"#E8C374" if big else "#0A0F20"}" stroke-width=".6"/>')
    s.append(f'<text x="{x:.1f}" y="{mid + 3.1:.1f}" text-anchor="middle" font-family="DM Serif Display" font-size="{7.4 if len(lab) >= 3 else 8.8}" fill="#F7DC94">{lab}</text>')
    return "".join(s)


def _plinko(st: _Sitting) -> tuple[str, str]:
    risks = Counter(h["d"]["risk"] for h in st.rounds)
    risk = risks.most_common(1)[0][0]
    table = _PLINKO_TABLES.get(risk, _PLINKO_TABLES["medium"])
    best, worst = st.best(), st.worst()
    other = sum(n for r, n in risks.items() if r != risk)
    css = """
.card{color:#F3D58A;background:radial-gradient(120% 70% at 50% 30%,#1C2E57 0%,#0D1730 58%,#060B18 100%);box-shadow:inset 0 0 0 2px rgba(201,150,63,.55)}
.plate{display:inline-flex;align-items:center;gap:12px;padding:12px 22px 10px;border-radius:10px;font:400 34px/1 'DM Serif Display',SerifLocal,serif;letter-spacing:.1em;color:#3A2405;
 text-shadow:0 1px 0 rgba(255,236,180,.8);background:linear-gradient(135deg,#FFE6A0 0%,#C9963F 40%,#8A5A14 75%,#E8C374 100%);box-shadow:0 6px 14px rgba(0,0,0,.55),inset 0 1px 0 rgba(255,255,255,.6)}
.plate i{width:7px;height:7px;border-radius:50%;background:#5E3B05} .who{color:#8C9CC4}
.net b{font:400 92px/88px 'DM Serif Display',SerifLocal,serif;text-shadow:0 4px 14px rgba(0,0,0,.45)} .net span{color:#8C9CC4}
.board{border-radius:20px;overflow:hidden;filter:drop-shadow(0 14px 22px rgba(0,0,0,.5))}
.riskcap{font-weight:800;font-size:14px;letter-spacing:.18em;color:#8C9CC4;margin:-6px 4px 12px;text-align:right}
.s{background:linear-gradient(180deg,#1F3366,#0F1C3D);box-shadow:inset 0 0 0 2px #C9963F} .s span,.s small{color:#8C9CC4} .s b{font-family:'DM Serif Display',SerifLocal,serif;font-weight:400;color:#F3D58A}
.s.red{background:radial-gradient(120% 140% at 50% 0%,#B72A33 0%,#7A121B 100%);box-shadow:inset 0 0 0 2px #E8C374} .s.red span,.s.red small{color:#F2C6B0} .s.red b{color:#FFF1C9}
"""
    risk_note = f"{risk.upper()} RISK" + (f" · {_plural(other, 'BALL').upper()} ON {'/'.join(r.upper() for r in risks if r != risk)}" if other else "")
    body = (f'<div class="card"><div class="head"><div class="plate"><i></i>PLINKO<i></i></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#7CE0A0", "#F28B82")}<div class="riskcap">{risk_note}</div><div class="board">{_plinko_board(st.rounds, table, best["d"]["slot"])}</div>'
            f'<div class="stats"><div class="s red"><span>BEST BALL</span><b>{best["d"]["mult"]:g}×</b><small>{sgn(int(best["net"]))} on {esc(best["d"]["risk"])}</small></div>'
            + _stat("WORST", f'{worst["d"]["mult"]:g}×', f'{sgn(int(worst["net"]))} on {worst["d"]["risk"]}')
            + _stat("WAGERED", f"{st.wagered:,}", f"{st.won} won · {st.lost} lost")
            + "</div></div>")
    return css, body


# ---- Glass Bridge: each crossing pane by pane ------------------------------------------------

def _glass(st: _Sitting) -> tuple[str, str]:
    shown = st.rounds[-10:]
    steps = max(h["d"]["steps"] for h in shown)
    mults = next((h["d"]["mults"] for h in reversed(shown) if h["d"]["mults"]), [])
    lanes = []
    for i, h in enumerate(shown):
        d = h["d"]
        cells = []
        for k in range(steps):
            cls = "ok" if k < d["step"] else ("broke" if k == d["step"] and d["fell"] else "dark")
            cells.append(f'<div class="pair {cls}"><i></i><i></i></div>')
        if d["fell"]:
            end = '<div class="flag fell">FELL</div>'
        else:
            m = d["mults"][d["step"] - 1] if d["mults"] and d["step"] else 0
            end = f'<div class="flag">{"ACROSS" if d["step"] >= d["steps"] else "CASHED"}<b>{_x(m)}</b></div>'
        n = int(h["net"])
        lanes.append(f'<div class="lane"><span class="n">{len(st.rounds) - len(shown) + i + 1}</span><div class="panes">{"".join(cells)}</div>{end}'
                     f'<b class="v {"w" if n > 0 else "l"}">{sgn(n)}</b></div>')
    pane_w = min(50, int((448 - (steps - 1) * 6) / max(steps, 1)))
    furthest = max(st.rounds, key=lambda h: h["d"]["step"])
    cashed = [h for h in st.rounds if not h["d"]["fell"]]
    best_cash = max((h["d"]["mults"][h["d"]["step"] - 1] for h in cashed if h["d"]["mults"] and h["d"]["step"]), default=0)
    css = """
.card{color:#fff;background:radial-gradient(70% 45% at 50% 0%,rgba(255,61,127,.28),rgba(255,61,127,0) 70%),radial-gradient(90% 60% at 50% 40%,#16181F 0%,#07080B 70%);box-shadow:inset 0 0 0 1px rgba(255,255,255,.08)}
.t{font:900 52px/50px Archivo,ArchivoLocal;font-stretch:80%;letter-spacing:.02em;text-transform:uppercase} .t em{font-style:normal;color:#FF3D7F}
.who{color:#8A8F9C} .net b{font:900 96px/90px Archivo,ArchivoLocal;font-stretch:75%;text-shadow:0 0 30px rgba(255,61,127,.35)} .net span{color:#8A8F9C}
.bridge{padding:18px 18px 10px;border-radius:18px;background:linear-gradient(180deg,#1B1E26,#101218);box-shadow:inset 0 0 0 1px rgba(255,255,255,.07)}
.cap{display:flex;font-weight:800;font-size:13px;color:#6C7280;margin-bottom:12px;padding-left:34px;gap:6px}
.cap span{text-align:center}
.lane{display:flex;align-items:center;gap:12px;height:54px} .n{width:22px;font-weight:800;font-size:16px;color:#6C7280;text-align:right}
.panes{display:flex;gap:6px;width:448px}
.pair{height:42px;display:flex;flex-direction:column;gap:3px;padding:3px;border-radius:6px;background:linear-gradient(180deg,#4A505B,#2B2F37);box-shadow:inset 0 1px 0 rgba(255,255,255,.25),0 3px 6px rgba(0,0,0,.5)}
.pair i{flex:1;border-radius:3px;background:rgba(200,220,230,.10);box-shadow:inset 0 0 0 1px rgba(255,255,255,.12)}
.pair.ok i:first-child{background:linear-gradient(180deg,rgba(235,250,255,.95),rgba(170,215,235,.55));box-shadow:0 0 12px rgba(180,230,255,.45)}
.pair.broke i:first-child{background:repeating-linear-gradient(135deg,rgba(255,61,127,.95) 0 3px,rgba(255,61,127,.25) 3px 6px);box-shadow:0 0 14px rgba(255,61,127,.65)}
.pair.broke i:last-child{background:rgba(120,255,190,.55)} .pair.dark{opacity:.45}
.flag{width:108px;font-weight:900;font-size:13px;letter-spacing:.14em;color:#F5C542} .flag b{display:block;font-size:20px;letter-spacing:0} .flag.fell{color:#FF5A6B}
.v{margin-left:auto;font:900 28px Archivo,ArchivoLocal;font-stretch:80%} .v.w{color:#7CFFB2} .v.l{color:#FF5A6B}
.s{background:rgba(255,255,255,.04);box-shadow:inset 0 0 0 1px rgba(255,255,255,.08)} .s span,.s small{color:#8A8F9C} .s b{font-weight:900;font-stretch:80%}
""" + f".pair{{width:{pane_w}px}} .cap span{{width:{pane_w}px}}"
    cap = "".join(f"<span>{m:g}×</span>" for m in [round(x, 2 if x < 10 else 1) for x in mults[:steps]])
    tail = f" (LAST {len(shown)})" if len(st.rounds) > len(shown) else ""
    fd = furthest["d"]
    body = (f'<div class="card"><div class="head"><div class="t">Glass <em>Bridge</em></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#7CFFB2", "#FF5A6B")}<div class="bridge"><div class="cap">{cap}</div>{"".join(lanes)}</div>'
            f'<div class="stats">'
            + _stat("FURTHEST", _plural(fd["step"], "pane"), ("fell on the next" if fd["fell"] else "and cashed") + tail.lower())
            + _stat("CASHED OUT", f"{len(cashed)} of {len(st.rounds)}", f"best {_x(best_cash)}" if cashed else "every one fell")
            + _stat("WAGERED", f"{st.wagered:,}", "UKP") + "</div></div>")
    return css, body


# ---- Mines: the board, tile by tile, over the sitting -----------------------------------------

_CANNONBALL = ('<svg viewBox="0 0 40 40"><circle cx="18" cy="23" r="12" fill="#4e5b6c"/><circle cx="22" cy="27" r="9" fill="#36414e"/>'
               '<ellipse cx="13" cy="18" rx="3.2" ry="2.1" fill="#8999ac" transform="rotate(-30 13 18)"/>'
               '<path d="M25 14Q27.5 9.5 31 8" fill="none" stroke="#9c7a4c" stroke-width="2.2" stroke-linecap="round"/>'
               '<path d="M32.5 1.5L33.7 5.3L37.5 6.5L33.7 7.7L32.5 11.5L31.3 7.7L27.5 6.5L31.3 5.3Z" fill="#ffb23e"/></svg>')


def _mines(st: _Sitting) -> tuple[str, str]:
    d0 = st.rounds[-1]["d"]
    tiles, cols = d0["tiles"], d0["cols"]
    coins, booms = Counter(), Counter()
    for h in st.rounds:
        coins.update(h["d"]["revealed"])
        if h["d"]["hit"] is not None:
            booms[h["d"]["hit"]] += 1
    mx = max(coins.values(), default=1) or 1
    cells = []
    for i in range(tiles):
        if booms[i]:
            cells.append(f'<div class="t boom">{_CANNONBALL}<b>sank {booms[i]}</b></div>')
        elif coins[i]:
            a = coins[i] / mx
            cells.append(f'<div class="t" style="background:linear-gradient(180deg,rgba(242,196,90,{.18 + .62 * a:.2f}),rgba(180,130,40,{.12 + .5 * a:.2f}));'
                         f'box-shadow:inset 0 0 0 1.5px rgba(242,196,90,{.25 + .6 * a:.2f}),0 0 {int(22 * a)}px rgba(242,196,90,{.35 * a:.2f})">'
                         f'<img src="{_f(COIN)}"><b>{coins[i]}</b></div>')
        else:
            cells.append('<div class="t none"></div>')
    found = sum(coins.values())
    sank = sum(1 for h in st.rounds if h["d"]["hit"] is not None)
    best = max(st.rounds, key=lambda h: (h["d"]["won"], h["d"]["mult"]))
    css = """
.card{color:#fff;background:radial-gradient(60% 30% at 82% 6%,rgba(255,244,214,.22),rgba(255,244,214,0) 70%),linear-gradient(180deg,#0a1530 0%,#102650 45%,#0b1b3a 70%,#040a17 100%)}
.moon{position:absolute;left:250px;top:-46px;width:110px;height:110px;border-radius:50%;background:radial-gradient(circle at 40% 40%,#FFF8E1,#E9DDB6 60%,#BFAF85);box-shadow:0 0 60px rgba(255,240,200,.35)}
.t1{font:400 56px/52px 'DM Serif Display',SerifLocal,serif;color:#F7E3A6} .t1 small{display:block;font:700 15px Archivo,ArchivoLocal;letter-spacing:.22em;color:#8FA3CC;margin-top:8px}
.who{color:#8FA3CC;position:relative} .net b{font:400 96px/90px 'DM Serif Display',SerifLocal,serif} .net span{color:#8FA3CC}
.grid{display:grid;gap:10px;padding:18px;border-radius:20px;background:rgba(4,10,23,.55);box-shadow:inset 0 0 0 1px rgba(143,163,204,.2)}
.t{height:88px;border-radius:14px;display:flex;align-items:center;justify-content:center;gap:10px}
.t img{width:34px;height:34px} .t b{font:400 34px 'DM Serif Display',SerifLocal,serif;color:#FFF3C8} .t.none{background:rgba(255,255,255,.04);box-shadow:inset 0 0 0 1px rgba(143,163,204,.15)}
.t.boom{background:linear-gradient(180deg,#3a2230,#1e1420);box-shadow:inset 0 0 0 1.5px rgba(255,120,90,.6),0 0 18px rgba(255,90,60,.35)} .t.boom svg{width:48px;height:48px} .t.boom b{font:800 15px Archivo,ArchivoLocal;letter-spacing:.1em;color:#FF9A7A}
.cap{display:flex;justify-content:space-between;font-weight:800;font-size:14px;letter-spacing:.18em;color:#8FA3CC;margin:0 4px 12px}
.s{background:rgba(255,255,255,.05);box-shadow:inset 0 0 0 1px rgba(143,163,204,.25)} .s span,.s small{color:#8FA3CC} .s b{font-family:'DM Serif Display',SerifLocal,serif;font-weight:400;color:#F7E3A6}
""" + f".grid{{grid-template-columns:repeat({cols},1fr)}}"
    body = (f'<div class="card"><div class="moon"></div><div class="head"><div class="t1">Mines<small>FIND THE DOUBLOONS</small></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#7CE0A0", "#F28B82", "serif")}<div class="cap"><span>EVERY TILE YOU TURNED</span><span>{_plural(sank, "CANNONBALL").upper()}</span></div>'
            f'<div class="grid">{"".join(cells)}</div><div class="stats">'
            + _stat("DOUBLOONS", f"{found:,}", f"found in {_plural(len(st.rounds), 'round')}")
            + _stat("BEST CASH-OUT", _x(best["d"]["mult"]) if best["d"]["won"] else "none", f"after {_plural(len(best['d']['revealed']), 'coin')}" if best["d"]["won"] else "")
            + _stat("SANK", f"{sank} of {len(st.rounds)}", "rounds lost to a cannonball") + "</div></div>")
    return css, body


# ---- Chest Upgrade: how high each round dared --------------------------------------------------

def _chest(st: _Sitting) -> tuple[str, str]:
    names = next((h["d"]["names"] for h in reversed(st.rounds) if h["d"]["names"]), ["Wood", "Silver", "Gold", "Diamond"])
    mults = next((h["d"]["mults"] for h in reversed(st.rounds) if h["d"]["mults"]), [1, 1.8, 3.5, 8])
    n = len(names)
    reached, opened, smashed = [0] * n, [0] * n, [0] * n
    for h in st.rounds:
        t, won = h["d"]["tier"], h["d"]["won"]
        for k in range(min(t, n - 1) + 1):
            reached[k] += 1
        if won:
            opened[min(t, n - 1)] += 1
        elif t + 1 < n:
            smashed[t + 1] += 1
    art = lambda name: _f(ART / f"chest-{name.lower()}.webp")
    shelves = "".join(
        f'<div class="sh"><img src="{art(names[i])}"><b>{esc(names[i])}</b><em>{mults[i]:g}×</em><div class="ct">'
        f'<span>reached <i>{reached[i]}</i></span><span class="tk">opened <i>{opened[i]}</i></span>'
        f'<span class="br{"" if smashed[i] else " dim"}">smashed <i>{smashed[i]}</i></span></div></div>' for i in range(n))
    strip = "".join(f'<img src="{art(names[min(h["d"]["tier"], n - 1)]) if h["d"]["won"] else _f(ART / "chest-shattered.webp")}">' for h in st.rounds[-14:])
    top = max((i for i in range(n) if opened[i]), default=None)
    css = """
.card{color:#FCEBD0;background:radial-gradient(90% 55% at 50% 42%,#5a3a14 0%,#2b1a0b 55%,#140c06 100%)}
.t1{font:400 52px/50px 'DM Serif Display',SerifLocal,serif;color:#F5C86A} .t1 small{display:block;font:700 15px Archivo,ArchivoLocal;letter-spacing:.22em;color:#C9A574;margin-top:8px}
.who{color:#C9A574} .net b{font:400 96px/90px 'DM Serif Display',SerifLocal,serif} .net span{color:#C9A574}
.shelves{display:flex;gap:12px} .sh{flex:1;display:flex;flex-direction:column;align-items:center;padding:16px 10px 14px;border-radius:16px;
 background:linear-gradient(180deg,rgba(255,220,160,.07),rgba(0,0,0,.25));box-shadow:inset 0 0 0 1px rgba(245,200,106,.22)}
.sh img{width:150px;height:120px;object-fit:contain;filter:drop-shadow(0 10px 12px rgba(0,0,0,.55))} .sh b{font:400 28px 'DM Serif Display',SerifLocal,serif;margin-top:6px} .sh em{font:800 16px Archivo,ArchivoLocal;font-style:normal;color:#F5C86A}
.ct{display:flex;flex-direction:column;gap:4px;margin-top:10px;width:100%} .ct span{display:flex;justify-content:space-between;font-weight:700;font-size:14px;letter-spacing:.06em;color:#C9A574;padding:4px 8px;border-radius:7px;background:rgba(0,0,0,.25)}
.ct i{font-style:normal;font-family:'DM Serif Display',SerifLocal,serif;font-size:20px;color:#FCEBD0} .ct .tk i{color:#8FE3A0} .ct .br i{color:#F07A6A} .ct .dim{opacity:.45}
.cap{font-weight:800;font-size:14px;letter-spacing:.18em;color:#C9A574;margin:22px 4px 10px}
.strip{display:flex;gap:6px;padding:12px;border-radius:14px;background:rgba(0,0,0,.3)} .strip img{width:48px;height:42px;object-fit:contain}
.s{background:rgba(0,0,0,.28);box-shadow:inset 0 0 0 1px rgba(245,200,106,.22)} .s span,.s small{color:#C9A574} .s b{font-family:'DM Serif Display',SerifLocal,serif;font-weight:400}
"""
    lost = sum(1 for h in st.rounds if not h["d"]["won"])
    body = (f'<div class="card"><div class="head"><div class="t1">Chest Upgrade<small>HOW HIGH DID YOU DARE</small></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#8FE3A0", "#F07A6A", "serif")}<div class="shelves">{shelves}</div>'
            f'<div class="cap">EACH ROUND, WHERE IT ENDED</div><div class="strip">{strip}</div><div class="stats">'
            + _stat("BEST CHEST OPENED", esc(names[top]) if top is not None else "none", f"{mults[top]:g}× the stake" if top is not None else "every upgrade smashed")
            + _stat("SMASHED", f"{lost} of {len(st.rounds)}", "chasing the next chest")
            + _stat("WAGERED", f"{st.wagered:,}", "UKP") + "</div></div>")
    return css, body


# ---- Blockade Run: every run as a course on the chart -------------------------------------------

def _blockade(st: _Sitting) -> tuple[str, str]:
    shown = st.rounds[-9:]
    cap = max(25.0, max(h["d"]["mult"] for h in shown))
    X = lambda m: 60 + (math.log(max(m, 1)) / math.log(cap)) * 610
    gap = 46 if len(shown) > 6 else 56
    H = 34 + len(shown) * gap
    lanes = []
    for k, h in enumerate(shown):
        d = h["d"]
        y = 34 + k * gap
        col, lab = ("#FF6B6B", "#FF8A8A") if d["sunk"] else ("#F5C542", "#F5D27A")
        lanes.append(f'<path d="M60 {y}H{X(d["mult"]):.0f}" stroke="{col}" stroke-width="2.5" stroke-dasharray="7 6" opacity=".85"/>'
                     f'<image href="{_f(ART / ("ship-sunk.webp" if d["sunk"] else "ship-escaped.webp"))}" x="{X(d["mult"]) - 26:.0f}" y="{y - 26}" width="52" height="52"/>'
                     f'<text x="{X(d["mult"]) + 32:.0f}" y="{y + 7}" font-family="Archivo" font-weight="900" font-size="20" fill="{lab}">{_x(d["mult"])}{"" if d["sunk"] else " ✓"}</text>')
    grid = "".join(f'<path d="M{X(m):.0f} 8V{H - 6}" stroke="rgba(170,210,240,.14)"/><text x="{X(m):.0f}" y="{H + 16}" text-anchor="middle" font-family="Archivo" font-weight="700" font-size="14" fill="#7FA6C8">{m:g}×</text>'
                   for m in (1, 2, 5, 10, 25) if m <= cap)
    escaped = [h for h in st.rounds if not h["d"]["sunk"]]
    best = max(escaped, key=lambda h: h["d"]["mult"]) if escaped else None
    css = """
.card{color:#EAF4FF;background:linear-gradient(180deg,#071226 0%,#0e2747 42%,#1b4a73 52%,#0c2a47 53%,#061a2e 100%)}
.t1{font:900 54px/50px Archivo,ArchivoLocal;font-stretch:75%;letter-spacing:.02em;text-transform:uppercase;color:#F5D27A} .t1 small{display:block;font:700 15px Archivo,ArchivoLocal;letter-spacing:.22em;color:#7FA6C8;margin-top:8px}
.who{color:#7FA6C8} .net b{font:900 100px/92px Archivo,ArchivoLocal;font-stretch:75%} .net span{color:#7FA6C8}
.chart{border-radius:20px;padding:10px 6px 4px;background:linear-gradient(180deg,rgba(4,16,30,.75),rgba(8,40,70,.6));box-shadow:inset 0 0 0 1px rgba(127,166,200,.25)}
.s{background:rgba(4,16,30,.55);box-shadow:inset 0 0 0 1px rgba(127,166,200,.25)} .s span,.s small{color:#7FA6C8} .s b{font-weight:900;font-stretch:80%}
"""
    body = (f'<div class="card"><div class="head"><div class="t1">Blockade Run<small>HOW FAR EACH RUN SAILED</small></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#7CE0A0", "#FF8A8A")}<div class="chart"><svg viewBox="0 0 760 {H + 24}" width="100%">{grid}{"".join(lanes)}</svg></div><div class="stats">'
            + _stat("FURTHEST ESCAPE", _x(best["d"]["mult"]) if best else "none", f"{sgn(int(best['net']))}" if best else "every run was caught", "color:#F5D27A")
            + _stat("MADE IT OUT", f"{len(escaped)} of {len(st.rounds)}", f"{len(st.rounds) - len(escaped)} sunk by the blockade")
            + _stat("WAGERED", f"{st.wagered:,}", "UKP") + "</div></div>")
    return css, body


# ---- Darts: every dart in the board, every round on the chalkboard -------------------------------

_ORDER = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5]
_BEDS = {"bullseye": (0, 0.006), "bull": (0.019, 0.027), "inner": (0.05, 0.165), "treble": (0.185, 0.192),
         "outer": (0.21, 0.28), "double": (0.297, 0.305), "wall": (0.47, 0.52)}


def _landing(label: str, rnd: random.Random):
    """Where on the board (fractions of its box) a dart with this label went in, as the game
    places it (src/casino/darts.ts)."""
    parts = label.split()
    if label == "Bullseye":
        bed, deg = _BEDS["bullseye"], rnd.random() * 360
    elif label == "Bull":
        bed, deg = _BEDS["bull"], rnd.random() * 360
    elif len(parts) == 2 and parts[0] in ("Single", "Double", "Treble") and parts[1].isdigit() and int(parts[1]) in _ORDER:
        kind = parts[0]
        bed = _BEDS["treble"] if kind == "Treble" else _BEDS["double"] if kind == "Double" else (_BEDS["inner"] if rnd.random() < .4 else _BEDS["outer"])
        deg = -90 + _ORDER.index(int(parts[1])) * 18 + (rnd.random() - .5) * 11
    else:
        bed = _BEDS["wall"]
        deg = -170 + rnd.random() * 50 if rnd.random() < .5 else -60 + rnd.random() * 50
    rad = bed[0] + rnd.random() * (bed[1] - bed[0])
    a = math.radians(deg)
    return 0.5007 + rad * math.cos(a), 0.5014 + rad * math.sin(a), (rnd.random() - .5) * 22


def _short(label: str) -> str:
    parts = label.split()
    if label in ("Bullseye", "Bull"):
        return "BULL" if label == "Bullseye" else "25"
    if len(parts) == 2 and parts[0] in ("Single", "Double", "Treble"):
        return parts[0][0] + parts[1]
    return "MISS"


def _darts(st: _Sitting) -> tuple[str, str]:
    rnd = random.Random(len(st.rounds) * 7919 + st.net)
    BS, DS = 420, 96
    darts = []
    for h in st.rounds[-12:]:
        for lab in h["d"]["throws"]:
            x, y, tilt = _landing(lab, rnd)
            darts.append(f'<img class="dt" src="{_f(ART / "dart.webp")}" style="left:{x * BS - DS * .285:.1f}px;top:{y * BS - DS * .769:.1f}px;'
                         f'width:{DS}px;height:{DS}px;transform-origin:28.5% 76.9%;transform:rotate({tilt:.1f}deg)">')
    chalk = []
    for h in st.rounds[-9:]:
        d = h["d"]
        m = float(h.get("multiple") or 0)
        res = "BUST" if d["bust"] else (f"{m:g}×" if m else "no pay")
        chalk.append(f'<li><span>{" ".join(_short(t) for t in d["throws"])}</span><b class="{"b" if d["bust"] else ""}">{d["total"]} · {res}</b></li>')
    best = st.best()
    busts = sum(1 for h in st.rounds if h["d"]["bust"])
    css = """
.card{color:#F4E6D0;background:radial-gradient(100% 60% at 50% 36%,#4a1b1b 0%,#2a0f0f 55%,#150707 100%)}
.t1{font:700 64px/56px Caveat,cursive;color:#F5D27A} .t1 small{display:block;font:700 15px Archivo,ArchivoLocal;letter-spacing:.22em;color:#C99A8A;margin-top:6px}
.who{color:#C99A8A} .net b{font:400 96px/90px 'DM Serif Display',SerifLocal,serif} .net span{color:#C99A8A}
.row{display:flex;gap:16px;align-items:center}
.bd{position:relative;width:420px;height:420px;flex:none;filter:drop-shadow(0 16px 24px rgba(0,0,0,.6))} .bd>img.b{width:420px;height:420px;display:block}
.dt{position:absolute;filter:drop-shadow(-3px 6px 4px rgba(0,0,0,.45))}
.chalk{flex:1;align-self:stretch;padding:16px 18px;border-radius:12px;background:linear-gradient(180deg,#26302A,#1A211D);box-shadow:inset 0 0 0 6px #5A3A1E,inset 0 0 0 8px #3A2414,0 10px 20px rgba(0,0,0,.4)}
.chalk h4{font:700 28px Caveat,cursive;color:#EDEDE3;margin-bottom:6px} .chalk ul{list-style:none} .chalk li{display:flex;justify-content:space-between;gap:8px;font:600 22px/40px Caveat,cursive;color:#DADBD0;border-bottom:1px dashed rgba(255,255,255,.12)}
.chalk li b{font-weight:700;color:#F5D27A;white-space:nowrap} .chalk li b.b{color:#FF8A8A;text-decoration:line-through}
.s{background:rgba(0,0,0,.28);box-shadow:inset 0 0 0 1px rgba(245,210,122,.22)} .s span,.s small{color:#C99A8A} .s b{font-family:'DM Serif Display',SerifLocal,serif;font-weight:400}
"""
    bm = float(best.get("multiple") or 0)
    body = (f'<div class="card"><div class="head"><div class="t1">Darts<small>NEAREST 60 WITHOUT GOING OVER</small></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#7CE0A0", "#FF8A8A", "serif")}<div class="row"><div class="bd"><img class="b" src="{_f(ART / "darts-board.webp")}">{"".join(darts)}</div>'
            f'<div class="chalk"><h4>Tonight\'s throws</h4><ul>{"".join(chalk)}</ul></div></div><div class="stats">'
            + _stat("BEST ROUND", f'{best["d"]["total"]} · {bm:g}×' if bm else str(best["d"]["total"]), sgn(int(best["net"])), "color:#F5D27A")
            + _stat("BUSTED", f"{busts} of {len(st.rounds)}", "went over 60")
            + _stat("WAGERED", f"{st.wagered:,}", "UKP") + "</div></div>")
    return css, body


# ---- Penalties: every kick placed in the keeper's goal --------------------------------------------

_AT = {"tl": (20, 26), "tr": (80, 26), "c": (50, 43), "bl": (17, 69), "br": (83, 69)}


def _penalty(st: _Sitting) -> tuple[str, str]:
    rnd = random.Random(len(st.rounds) * 104729 + st.net)
    GW = 760
    GH = GW * 507 / 900
    marks, scored, saved = [], 0, 0
    for h in st.rounds:
        for spot, result in h["d"]["kicks"]:
            if spot not in _AT:
                continue
            goal = result == "goal"
            scored += goal
            saved += not goal
            px, py = _AT[spot]
            x = (px + rnd.uniform(-5, 5)) / 100 * GW
            y = (py + rnd.uniform(-6, 6)) / 100 * GH
            sc = 46 / 99
            marks.append(f'<div class="kick{"" if goal else " saved"}" style="left:{x:.0f}px;top:{y:.0f}px">'
                         f'<img src="{_f(ART / "ball.webp")}" style="width:{240 * sc:.1f}px;height:{160 * sc:.1f}px;left:{-122.5 * sc:.1f}px;top:{-77 * sc:.1f}px">'
                         + ("" if goal else '<svg viewBox="0 0 40 40"><path d="M10 10l20 20M30 10L10 30" stroke="#FF3B4E" stroke-width="7" stroke-linecap="round"/></svg>')
                         + "</div>")
    rows = []
    shown = st.rounds[-6:]
    for i, h in enumerate(shown):
        d = h["d"]
        dots = "".join(f'<i class="{"g" if r == "goal" else "m"}"></i>' for _s, r in d["kicks"][:d["shots"]])
        dots += "".join('<i class="e"></i>' for _ in range(max(0, d["shots"] - len(d["kicks"]))))
        n = int(h["net"])
        rows.append(f'<div class="so"><span>{len(st.rounds) - len(shown) + i + 1}</span>{dots}<b class="{"w" if n > 0 else "l"}">{sgn(n)}</b></div>')
    perfect = [h for h in st.rounds if h["d"]["goals"] >= h["d"]["shots"]]
    best = st.best()
    css = """
.card{color:#fff;background:radial-gradient(90% 35% at 50% 0%,rgba(255,255,255,.16),rgba(255,255,255,0) 100%),linear-gradient(180deg,#0a1f12 0%,#123a21 40%,#1b5a2e 70%,#164d27 100%)}
.t1{font:900 56px/52px Archivo,ArchivoLocal;font-stretch:72%;text-transform:uppercase;letter-spacing:.02em} .t1 small{display:block;font:700 15px Archivo,ArchivoLocal;letter-spacing:.22em;color:#A8D4B4;margin-top:8px}
.who{color:#A8D4B4} .net b{font:900 100px/92px Archivo,ArchivoLocal;font-stretch:72%;text-shadow:0 4px 18px rgba(0,0,0,.35)} .net span{color:#A8D4B4}
.goal{position:relative;width:760px;border-radius:18px;overflow:hidden;box-shadow:0 14px 26px rgba(0,0,0,.45),inset 0 0 0 1px rgba(255,255,255,.15)}
.goal>img.k{width:100%;height:100%;display:block}
.kick{position:absolute;width:0;height:0} .kick img{position:absolute;filter:drop-shadow(0 4px 5px rgba(0,0,0,.45))}
.kick.saved img{opacity:.75} .kick svg{position:absolute;left:-17px;top:-17px;width:34px;height:34px;filter:drop-shadow(0 2px 2px rgba(0,0,0,.5))}
.sos{display:flex;flex-direction:column;gap:8px;margin-top:16px}
.so{display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:12px;background:rgba(0,0,0,.25)} .so span{width:22px;font-weight:800;color:#A8D4B4}
.so i{width:30px;height:30px;border-radius:50%} .so i.g{background:radial-gradient(circle at 35% 30%,#fff,#d8e2dc)} .so i.m{background:#FF5A6B;box-shadow:inset 0 0 0 4px #7a1520} .so i.e{box-shadow:inset 0 0 0 2px rgba(255,255,255,.18)}
.so b{margin-left:auto;font:900 26px Archivo,ArchivoLocal;font-stretch:80%} .so b.w{color:#F5D27A} .so b.l{color:#FFB3B3}
.s{background:rgba(0,0,0,.25);box-shadow:inset 0 0 0 1px rgba(255,255,255,.12)} .s span,.s small{color:#A8D4B4} .s b{font-weight:900;font-stretch:80%}
""" + f".goal{{height:{GH:.0f}px}}"
    first = ("PERFECT FIVE", "once" if len(perfect) == 1 else f"{len(perfect)} times", "every kick scored") if perfect else \
        ("BEST SHOOTOUT", sgn(int(best["net"])), f"{_plural(best['d']['goals'], 'goal')}")
    body = (f'<div class="card"><div class="head"><div class="t1">Penalties<small>WHERE EVERY KICK WENT</small></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#FFFFFF", "#FFB3B3")}<div class="goal"><img class="k" src="{_f(ART / "keeper-ready.webp")}">{"".join(marks)}</div>'
            f'<div class="sos">{"".join(rows)}</div><div class="stats">'
            + _stat(first[0], first[1], first[2], "color:#F5D27A")
            + _stat("KICKS", str(scored + saved), f"{scored} scored · {saved} saved")
            + _stat("WAGERED", f"{st.wagered:,}", "UKP") + "</div></div>")
    return css, body


# ---- Davy Jones' Locker: coins in against coins back, cup by cup ----------------------------------

def _pennyfalls(st: _Sitting) -> tuple[str, str]:
    shown = st.rounds[-5:]
    biggest = max(max(h["d"]["fed"], h["d"]["won"]) for h in shown) or 1
    per = max(4, math.ceil(biggest / 26))           # coins per disc, so the tallest stack fits
    cols = []
    for i, h in enumerate(shown):
        d = h["d"]

        def stack(n, cls):
            k = math.ceil(n / per) if n else 0
            return "".join(f'<i class="{cls}" style="bottom:{j * 8}px"></i>' for j in range(k)) + \
                f'<b style="bottom:{k * 8 + 18}px">{n:,}</b>'
        n = int(h["net"])
        cols.append(f'<div class="cup"><div class="pair"><div class="st">{stack(d["fed"], "c")}</div><div class="st">{stack(d["won"], "w")}</div></div>'
                    f'<span>CUP {len(st.rounds) - len(shown) + i + 1}</span><em class="{"up" if n > 0 else "dn"}">{sgn(n)}</em>'
                    + (f'<u>{_plural(d["golds"], "GOLD COIN").upper()}</u>' if d["golds"] else "") + "</div>")
    fed = sum(h["d"]["fed"] for h in st.rounds)
    won = sum(h["d"]["won"] for h in st.rounds)
    golds = sum(h["d"]["golds"] for h in st.rounds)
    best = st.best()
    css = """
.card{color:#EAF6F2;background:radial-gradient(90% 55% at 50% 40%,#0e4a4a 0%,#08303a 55%,#041a22 100%)}
.t1{font:400 50px/50px 'DM Serif Display',SerifLocal,serif;color:#F2C28A} .t1 small{display:block;font:700 15px Archivo,ArchivoLocal;letter-spacing:.22em;color:#7FB8B0;margin-top:8px}
.who{color:#7FB8B0} .net b{font:400 96px/90px 'DM Serif Display',SerifLocal,serif} .net span{color:#7FB8B0}
.shelf{display:flex;justify-content:space-around;padding:18px 18px 14px;border-radius:20px;background:linear-gradient(180deg,rgba(0,0,0,.15),rgba(0,0,0,.4));box-shadow:inset 0 0 0 1px rgba(127,184,176,.25)}
.cup{display:flex;flex-direction:column;align-items:center;gap:6px;width:136px} .pair{display:flex;gap:10px;align-items:flex-end;height:260px}
.st{position:relative;width:52px;height:100%} .st i{position:absolute;left:4px;width:44px;height:14px;border-radius:50%;box-shadow:0 2px 0 rgba(0,0,0,.35)}
.st i.c{background:radial-gradient(ellipse at 40% 35%,#E9A27A,#B5653E 60%,#7A3A1A)} .st i.w{background:radial-gradient(ellipse at 40% 35%,#FFF0B8,#E8B947 60%,#8A5A0C)}
.st b{position:absolute;left:-10px;right:-10px;text-align:center;font:400 22px 'DM Serif Display',SerifLocal,serif}
.cup span{font-weight:800;font-size:13px;letter-spacing:.18em;color:#7FB8B0} .cup em{font:400 26px 'DM Serif Display',SerifLocal,serif;font-style:normal} .cup em.up{color:#7CE0A0} .cup em.dn{color:#FF9A8A}
.cup u{text-decoration:none;font-weight:800;font-size:11px;letter-spacing:.16em;color:#2A1A05;background:#F5C542;padding:3px 7px;border-radius:5px}
.legend{display:flex;gap:18px;margin:14px 6px 0;font-weight:700;font-size:14px;letter-spacing:.1em;color:#7FB8B0} .legend i{display:inline-block;width:14px;height:14px;border-radius:50%;margin-right:6px;vertical-align:-2px}
.s{background:rgba(0,0,0,.25);box-shadow:inset 0 0 0 1px rgba(127,184,176,.25)} .s span,.s small{color:#7FB8B0} .s b{font-family:'DM Serif Display',SerifLocal,serif;font-weight:400}
"""
    body = (f'<div class="card"><div class="head"><div class="t1">Davy Jones\' Locker<small>COINS IN, COINS OVER THE EDGE</small></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#7CE0A0", "#FF9A8A", "serif")}<div class="shelf">{"".join(cols)}</div>'
            f'<div class="legend"><span><i style="background:#B5653E"></i>COINS FED IN</span><span><i style="background:#E8B947"></i>COINS WON BACK</span></div><div class="stats">'
            + _stat("COINS FED", f"{fed:,}", "into the machine")
            + _stat("WON BACK", f"{won:,}", _plural(golds, "gold coin") + " dropped" if golds else "over the edge")
            + _stat("BEST CUP", sgn(int(best["net"])), "", "color:#F2C28A") + "</div></div>")
    return css, body


# ---- Roulette: the table's number board and the wheel ----------------------------------------------

_RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
_WHEEL = [0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26]


def _roulette(st: _Sitting) -> tuple[str, str]:
    nums = [h["d"]["n"] for h in st.rounds]
    board = "".join(f'<div class="nr"><span class="blk">{n if n and n not in _RED else ""}</span><span class="z">{n if n == 0 else ""}</span>'
                    f'<span class="red">{n if n in _RED else ""}</span></div>' for n in reversed(nums[-12:]))
    hot = Counter(nums)
    wheel = []
    for i, n in enumerate(_WHEEL):
        a0, a1, am = (math.radians(-90 + (i + o) * 360 / 37) for o in (-.5, .5, 0))
        c = "#0E7A3E" if n == 0 else ("#C0202A" if n in _RED else "#141414")
        p = lambda r, a: (160 + r * math.cos(a), 160 + r * math.sin(a))
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p(150, a0), p(150, a1), p(104, a1), p(104, a0)
        wheel.append(f'<path d="M{x0:.1f} {y0:.1f}A150 150 0 0 1 {x1:.1f} {y1:.1f}L{x2:.1f} {y2:.1f}A104 104 0 0 0 {x3:.1f} {y3:.1f}Z" fill="{c}" stroke="#C9A14A" stroke-width="1"/>')
        tx, ty = p(132, am)
        wheel.append(f'<text x="{tx:.1f}" y="{ty + 4:.1f}" text-anchor="middle" font-family="Archivo" font-weight="800" font-size="11" fill="#fff" transform="rotate({i * 360 / 37:.1f} {tx:.1f} {ty:.1f})">{n}</text>')
        if hot[n]:
            hx, hy = p(92, am)
            wheel.append(f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="{min(16, 6 + hot[n] * 3)}" fill="#F5D27A" opacity=".9"/>'
                         f'<text x="{hx:.1f}" y="{hy + 4:.1f}" text-anchor="middle" font-family="Archivo" font-weight="900" font-size="11" fill="#2A1A05">{hot[n] if hot[n] > 1 else ""}</text>')
    top_n, top_c = hot.most_common(1)[0]
    note = f"{top_n} CAME UP {['', 'ONCE', 'TWICE', 'THREE TIMES', 'FOUR TIMES'][top_c] if top_c < 5 else f'{top_c} TIMES'}" if top_c > 1 else "NO NUMBER CAME UP TWICE"
    best = st.best()
    css = """
.card{color:#fff;background:radial-gradient(ellipse 120% 70% at 50% 38%,#17583f 0%,#0f3d2d 55%,#0a2e22 100%)}
.t1{font:400 56px/52px 'DM Serif Display',SerifLocal,serif;color:#E2BE78} .t1 small{display:block;font:700 15px 'Josefin Sans',sans-serif;letter-spacing:.22em;color:#9FC7B4;margin-top:8px}
.who{color:#9FC7B4;font-family:'Josefin Sans',sans-serif} .net b{font:400 96px/90px 'DM Serif Display',SerifLocal,serif} .net span{color:#9FC7B4;font-family:'Josefin Sans',sans-serif}
.row{display:flex;gap:18px;align-items:stretch}
.mq{width:250px;flex:none;padding:14px 12px;border-radius:14px;background:#050505;box-shadow:inset 0 0 0 3px #2A2A2A,0 10px 22px rgba(0,0,0,.5)}
.mq h4{font:700 13px Archivo,ArchivoLocal;letter-spacing:.2em;color:#888;text-align:center;margin-bottom:8px}
.nr{display:grid;grid-template-columns:1fr 50px 1fr;height:34px;align-items:center;font:400 30px VT323,monospace;text-align:center}
.nr .red{color:#FF3B3B;text-shadow:0 0 8px rgba(255,59,59,.7)} .nr .blk{color:#F2F2F2;text-shadow:0 0 6px rgba(255,255,255,.5)} .nr .z{color:#33E07A;text-shadow:0 0 8px rgba(51,224,122,.7)}
.nr:first-of-type{background:rgba(255,255,255,.08);border-radius:6px}
.wh{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:10px;border-radius:18px;background:rgba(0,0,0,.18)}
.wh p{font:600 16px 'Josefin Sans',sans-serif;letter-spacing:.12em;color:#9FC7B4;margin-top:10px}
.s{background:rgba(0,0,0,.22);box-shadow:inset 0 0 0 1px rgba(226,190,120,.28)} .s span,.s small{color:#9FC7B4;font-family:'Josefin Sans',sans-serif} .s b{font-family:'DM Serif Display',SerifLocal,serif;font-weight:400}
"""
    paid = sum(1 for h in st.all if int(h.get("payout", 0)) > 0)
    body = (f'<div class="card"><div class="head"><div class="t1">Roulette<small>WHERE THE BALL LANDED</small></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#7CE0A0", "#FF8A8A")}<div class="row"><div class="mq"><h4>LAST {min(12, len(nums))}</h4>{board}</div>'
            f'<div class="wh"><svg viewBox="0 0 320 320" width="420"><defs><radialGradient id="wd"><stop offset="0" stop-color="#8A5A2A"/><stop offset="1" stop-color="#4A2C10"/></radialGradient></defs>'
            f'<circle cx="160" cy="160" r="158" fill="#3A2410"/>{"".join(wheel)}<circle cx="160" cy="160" r="70" fill="url(#wd)"/><circle cx="160" cy="160" r="22" fill="#E2BE78"/></svg><p>{note}</p></div></div><div class="stats">'
            + _stat("BIGGEST WIN", sgn(int(best["net"])) if int(best["net"]) > 0 else "none", f"on {best['d']['n']}" if int(best["net"]) > 0 else "", "color:#E2BE78")
            + _stat("HIT RATE", f"{paid} of {len(st.all)}", "spins that paid")
            + _stat("WAGERED", f"{st.wagered:,}", "UKP") + "</div></div>")
    return css, body


# ---- Fruit Machine: the best spin and every spin ------------------------------------------------

_SYMBOLS = {"crown", "union", "lion", "rose", "anchor", "pound", "cherry"}


def _slots(st: _Sitting) -> tuple[str, str]:
    sym = lambda k: _f(EMOJI / f"{k if k in _SYMBOLS else 'anchor'}.svg")
    best = max(st.rounds, key=lambda h: h["d"]["mult"])
    shown = st.rounds[-21:]
    tape = "".join(f'<div class="sp{" hit" if h["d"]["mult"] else ""}">' + "".join(f'<img src="{sym(s)}">' for s in h["d"]["reels"])
                   + f'<b>{(str(round(h["d"]["mult"], 1)).rstrip("0").rstrip(".") + "×") if h["d"]["mult"] else ""}</b></div>' for h in shown)
    bm = best["d"]["mult"]
    names = {"crown": "CROWNS", "union": "UNION JACKS", "lion": "LIONS", "rose": "ROSES", "anchor": "ANCHORS", "pound": "POUNDS", "cherry": "CHERRIES"}
    reels = best["d"]["reels"]
    if bm and len(set(reels)) == 1:
        line = f"THREE {names.get(reels[0], 'OF A KIND')} · {bm:g}×"
    elif bm:
        line = f"PAID {bm:g}×"
    else:
        line = "NO WINNING LINE"
    paid = sum(1 for h in st.rounds if h["d"]["mult"])
    big_reels = "".join(f'<img src="{sym(s)}">' for s in reels)
    css = """
.card{color:#fff;background:radial-gradient(ellipse 120% 70% at 50% 35%,#2a4fa8 0%,#1a3580 50%,#0f2257 100%)}
.t1{font:400 48px/50px Bungee,sans-serif;color:#FFD23F;text-shadow:0 3px 0 #B8860B} .t1 small{display:block;font:700 15px Archivo,ArchivoLocal;letter-spacing:.22em;color:#A8B8E8;margin-top:8px;text-shadow:none}
.who{color:#A8B8E8} .net b{font:400 92px/90px Bungee,sans-serif;text-shadow:0 4px 0 rgba(0,0,0,.35)} .net span{color:#A8B8E8}
.win{padding:18px;border-radius:22px;background:linear-gradient(180deg,#C9A14A,#7A5A1E);box-shadow:0 12px 24px rgba(0,0,0,.45)}
.win .in{display:flex;justify-content:space-around;padding:18px;border-radius:14px;background:linear-gradient(180deg,#fff 0%,#E8E4DA 50%,#fff 100%);box-shadow:inset 0 6px 14px rgba(0,0,0,.35)}
.win img{width:150px;height:150px} .win p{text-align:center;font:400 26px Bungee,sans-serif;color:#2A1A05;margin-top:10px}
.cap{font-weight:800;font-size:14px;letter-spacing:.18em;color:#A8B8E8;margin:20px 4px 10px}
.tape{display:grid;grid-template-columns:repeat(7,1fr);gap:8px}
.sp{display:flex;flex-direction:column;align-items:center;gap:2px;padding:8px 4px;border-radius:10px;background:rgba(0,0,0,.25)} .sp img{width:34px;height:34px}
.sp b{font:400 15px Bungee,sans-serif;color:#FFD23F;min-height:18px} .sp.hit{background:rgba(255,210,63,.18);box-shadow:inset 0 0 0 2px #FFD23F}
.s{background:rgba(0,0,0,.25);box-shadow:inset 0 0 0 1px rgba(168,184,232,.3)} .s span,.s small{color:#A8B8E8} .s b{font-family:Bungee,sans-serif;font-weight:400;font-size:30px}
"""
    body = (f'<div class="card"><div class="head"><div class="t1">Fruit Machine<small>BEST SPIN OF THE SESSION</small></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#7CFFB2", "#FF9A9A")}<div class="win"><div class="in">{big_reels}</div><p>{line}</p></div>'
            f'<div class="cap">{"EVERY SPIN" if len(st.rounds) <= len(shown) else f"THE LAST {len(shown)} SPINS"}</div><div class="tape">{tape}</div><div class="stats">'
            + _stat("BEST SPIN", f"{bm:g}×" if bm else "0×", sgn(int(best["net"])), "color:#FFD23F")
            + _stat("PAID", f"{paid} of {len(st.rounds)}", "spins")
            + _stat("WAGERED", f"{st.wagered:,}", "UKP") + "</div></div>")
    return css, body


# ---- the felt card games -------------------------------------------------------------------

_FELT = """
.card{color:#fff;background:radial-gradient(ellipse 120% 70% at 50% 38%,#17583f 0%,#0f3d2d 55%,#0a2e22 100%)}
.t1{font:400 56px/52px 'DM Serif Display',SerifLocal,serif;color:#E2BE78} .t1 small{display:block;font:700 15px 'Josefin Sans',sans-serif;letter-spacing:.22em;color:#9FC7B4;margin-top:8px}
.who{color:#9FC7B4;font-family:'Josefin Sans',sans-serif} .net b{font:400 96px/90px 'DM Serif Display',SerifLocal,serif} .net span{color:#9FC7B4;font-family:'Josefin Sans',sans-serif}
.felt{padding:22px;border-radius:22px;background:radial-gradient(120% 90% at 50% 0%,rgba(255,255,255,.07),rgba(0,0,0,.18));box-shadow:inset 0 0 0 2px rgba(226,190,120,.35)}
.lab{font:700 15px 'Josefin Sans',sans-serif;letter-spacing:.2em;color:#9FC7B4;margin-bottom:12px;display:flex;justify-content:space-between;gap:12px}
.hand{display:flex;gap:10px;align-items:center}
.s{background:rgba(0,0,0,.22);box-shadow:inset 0 0 0 1px rgba(226,190,120,.28)} .s span,.s small{color:#9FC7B4;font-family:'Josefin Sans',sans-serif} .s b{font-family:'DM Serif Display',SerifLocal,serif;font-weight:400}
"""


def _blackjack(st: _Sitting) -> tuple[str, str]:
    best = st.best()
    b = best["d"]
    tiles = []
    for h in st.rounds[-16:]:
        d = h["d"]
        n = int(h["net"])
        you = d["you"]
        if d["outcome"] == "blackjack":
            top, cls, face = "BLACKJACK", "bj", "BJ"
        elif you > 21:
            top, cls, face = "BUST", "l", str(you)
        elif d["outcome"] == "push":
            top, cls, face = "PUSH", "p", str(you)
        elif n > 0:
            top, cls, face = ("DOUBLED" if d["doubled"] else "WIN"), "w", str(you)
        else:
            top, cls, face = ("DOUBLED" if d["doubled"] else "LOSE"), "l", str(you)
        vs = "<em>&nbsp;</em>" if you > 21 else f'<em>v {"BUST" if d["dealer"] > 21 else d["dealer"]}</em>'
        tiles.append(f'<div class="hd {cls}"><small>{top}</small><b>{face}</b>{vs}<i>{sgn(n)}</i></div>')
    if b["outcome"] == "blackjack":
        title = "BLACKJACK"
    elif b["doubled"]:
        title = f"DOUBLED, MADE {b['you']}"
    else:
        title = f"{b['you']} BEAT {'A BUST' if b['dealer'] > 21 else b['dealer']}" if int(best["net"]) > 0 else f"{b['you']}"
    blackjacks = sum(1 for h in st.rounds if h["d"]["outcome"] == "blackjack")
    busts = sum(1 for h in st.rounds if h["d"]["you"] > 21)
    pushes = sum(1 for h in st.all if int(h["net"]) == 0)
    w = 96 if len(b["player"]) <= 3 else 76
    css = _FELT + """
.big{display:flex;align-items:center;gap:22px} .big .side{display:flex;flex-direction:column;gap:10px} .big em{font:700 14px 'Josefin Sans',sans-serif;font-style:normal;letter-spacing:.18em;color:#9FC7B4}
.big .vs{font:400 30px 'DM Serif Display',SerifLocal,serif;color:#9FC7B4;padding-top:30px}
.hands{display:grid;grid-template-columns:repeat(8,1fr);gap:8px;margin-top:6px}
.hd{display:flex;flex-direction:column;align-items:center;padding:8px 4px 7px;border-radius:12px;background:rgba(0,0,0,.22);box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.1)}
.hd small{font:700 10px Archivo,ArchivoLocal;letter-spacing:.08em} .hd b{font:400 34px/36px 'DM Serif Display',SerifLocal,serif} .hd em{font:700 13px 'Josefin Sans',sans-serif;font-style:normal;color:#9FC7B4;min-height:16px}
.hd i{font:800 13px Archivo,ArchivoLocal;font-style:normal;margin-top:2px}
.hd.w{box-shadow:inset 0 0 0 1.5px rgba(124,224,160,.55)} .hd.w small,.hd.w i{color:#7CE0A0}
.hd.l{box-shadow:inset 0 0 0 1.5px rgba(255,138,138,.45)} .hd.l small,.hd.l i{color:#FF9A9A} .hd.l b{color:#E8D0D0}
.hd.p small,.hd.p i{color:#C8D0D8} .hd.bj{background:rgba(226,190,120,.16);box-shadow:inset 0 0 0 2px #E2BE78} .hd.bj small,.hd.bj i,.hd.bj b{color:#E2BE78}
"""
    body = (f'<div class="card"><div class="head"><div class="t1">Blackjack<small>{_plural(len(st.all), "HAND").upper()} AGAINST THE DEALER</small></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#7CE0A0", "#FF8A8A")}<div class="felt"><div class="lab"><span>BIGGEST HAND · {esc(title)}</span><span style="color:#7CE0A0">{sgn(int(best["net"]))}</span></div>'
            f'<div class="big"><div class="side"><em>YOU · {"BLACKJACK" if b["outcome"] == "blackjack" else b["you"]}</em><div class="hand">{"".join(pc(c, w) for c in b["player"])}</div></div>'
            f'<div class="vs">{"beat" if int(best["net"]) > 0 else "v"}</div><div class="side"><em>DEALER · {"BUST" if b["dealer"] > 21 else b["dealer"]}</em><div class="hand">{"".join(pc(c, w) for c in b["dealerCards"])}</div></div></div>'
            f'<div class="lab" style="margin-top:22px"><span>{"EVERY HAND" if len(st.rounds) <= 16 else "THE LAST 16 HANDS"} · YOU v DEALER</span><span>{st.won} WON · {st.lost} LOST · {pushes} PUSH</span></div>'
            f'<div class="hands">{"".join(tiles)}</div></div><div class="stats">'
            + _stat("BLACKJACKS", str(blackjacks), "paid 3:2" if blackjacks else "", "color:#E2BE78")
            + _stat("BUSTED", str(busts), "went over 21")
            + _stat("WAGERED", f"{st.wagered:,}", "UKP") + "</div></div>")
    return css, body


def _higherlower(st: _Sitting) -> tuple[str, str]:
    best = max(st.rounds, key=lambda h: (h["d"]["steps"], h["d"]["won"]))
    run = best["d"]["run"][-7:]
    arrows = []
    for a, b in zip(run, run[1:]):
        ra, rb = "23456789TJQKA".find(a[0]), "23456789TJQKA".find(b[0])
        arrows.append("↑" if rb > ra else "↓" if rb < ra else "=")
    cards = "".join(pc(c, 82) + (f'<div class="ar">{arrows[i]}</div>' if i < len(arrows) else "") for i, c in enumerate(run))
    top = max(h["d"]["steps"] for h in st.rounds) or 1
    bars = "".join(f'<i class="{"" if h["d"]["won"] else "x"}" style="height:{max(6, int(h["d"]["steps"] / top * 100)) + 10}px"></i>' for h in st.rounds[-16:])
    cashed = sum(1 for h in st.rounds if h["d"]["won"])
    bd = best["d"]
    css = _FELT + """.ar{font:900 30px Archivo,ArchivoLocal;color:#E2BE78} .run{display:flex;align-items:center;gap:6px;justify-content:center}
.bar{display:flex;gap:6px;align-items:flex-end;height:120px;margin-top:20px} .bar i{flex:1;border-radius:6px 6px 0 0;background:linear-gradient(180deg,#E2BE78,#8A6A2A)} .bar i.x{background:linear-gradient(180deg,#C0485A,#6A1A24)}"""
    body = (f'<div class="card"><div class="head"><div class="t1">Higher or Lower<small>THE LONGEST RUN</small></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#7CE0A0", "#FF8A8A")}<div class="felt"><div class="lab"><span>{_plural(bd["steps"], "RIGHT GUESS", "RIGHT").upper()} IN A ROW</span>'
            f'<span>{("CASHED AT " + _x(bd["mult"])) if bd["won"] else "THEN A WRONG ONE"}</span></div><div class="run">{cards}</div>'
            f'<div class="lab" style="margin-top:22px"><span>EVERY ROUND, HOW FAR IT GOT</span><span>RED = A WRONG GUESS</span></div><div class="bar">{bars}</div></div><div class="stats">'
            + _stat("LONGEST RUN", str(bd["steps"]), _x(bd["mult"]) if bd["won"] else "", "color:#E2BE78")
            + _stat("CASHED OUT", f"{cashed} of {len(st.rounds)}", "rounds")
            + _stat("WAGERED", f"{st.wagered:,}", "UKP") + "</div></div>")
    return css, body


def _reddog(st: _Sitting) -> tuple[str, str]:
    best = st.best()
    bd = best["d"]
    a, m, b = bd["cards"]
    if bd["outcome"] == "trips":
        head = "THREE OF A KIND · PAID 11:1"
    elif bd["spread"]:
        head = f"SPREAD OF {bd['spread']} · PAID {bd['odds']}:1" if int(best["net"]) > 0 else f"SPREAD OF {bd['spread']}"
    else:
        head = "PUSH"
    tiles = []
    for h in st.rounds[-8:]:
        d = h["d"]
        n = int(h["net"])
        tiles.append(f'<div class="h"><div class="mini">{"".join(pc(c, 40) for c in d["cards"] if c)}</div>'
                     f'<b class="{"w" if n > 0 else "l" if n < 0 else ""}">{sgn(n) if n else "PUSH"}</b></div>')
    trips = sum(1 for h in st.rounds if h["d"]["outcome"] == "trips")
    css = _FELT + """.rd{display:flex;align-items:center;gap:14px;justify-content:center} .sp{font:400 30px 'DM Serif Display',SerifLocal,serif;color:#E2BE78;padding:0 8px}
.hs{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:20px} .h{padding:10px;border-radius:12px;background:rgba(0,0,0,.2);display:flex;flex-direction:column;align-items:center;gap:6px}
.h .mini{display:flex;gap:4px} .h b{font:700 14px 'Josefin Sans',sans-serif;letter-spacing:.12em} .h b.w{color:#7CE0A0} .h b.l{color:#FF9A9A}"""
    middle = f'{pc(a, 104)}<span class="sp">→</span>{pc(m, 118)}<span class="sp">←</span>{pc(b, 104)}' if m else f'{pc(a, 104)}{pc(b, 104)}'
    body = (f'<div class="card"><div class="head"><div class="t1">Red Dog<small>THE HAND THAT PAID BEST</small></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#7CE0A0", "#FF8A8A")}<div class="felt"><div class="lab"><span>{esc(head)}</span><span style="color:#7CE0A0">{sgn(int(best["net"]))}</span></div>'
            f'<div class="rd">{middle}</div><div class="hs">{"".join(tiles)}</div></div><div class="stats">'
            + _stat("THREE OF A KIND", str(trips), "paid 11:1" if trips else "", "color:#E2BE78")
            + _stat("WON", f"{st.won} of {len(st.all)}", "hands")
            + _stat("WAGERED", f"{st.wagered:,}", "UKP") + "</div></div>")
    return css, body


_TCP_ORDER = ["Straight Flush", "Three of a Kind", "Straight", "Flush", "Pair", "High Card"]


def _tcp_rank(name: str) -> str:
    for k in _TCP_ORDER:
        if k.lower() in (name or "").lower():
            return k
    return "High Card"


def _tcp(st: _Sitting) -> tuple[str, str]:
    best = max(st.rounds, key=lambda h: (len(_TCP_ORDER) - _TCP_ORDER.index(_tcp_rank(h["d"]["hand"])), int(h["net"])))
    bd = best["d"]
    tally = Counter(_tcp_rank(h["d"]["hand"]) for h in st.rounds)
    cells = "".join(f'<div><b style="{"color:#E2BE78" if k == _tcp_rank(bd["hand"]) else ""}">{tally[k]}</b><span>{k.upper()}</span></div>'
                    for k in reversed(_TCP_ORDER) if tally[k] or k in ("High Card", "Pair"))
    beat = sum(1 for h in st.rounds if int(h["net"]) > 0)
    css = _FELT + """.tc{display:flex;gap:30px;justify-content:center;align-items:flex-end} .tc .s2{display:flex;flex-direction:column;align-items:center;gap:10px}
.tc em{font:700 14px 'Josefin Sans',sans-serif;font-style:normal;letter-spacing:.18em;color:#9FC7B4}
.tally{display:flex;gap:10px;margin-top:20px} .tally div{flex:1;padding:12px;border-radius:12px;background:rgba(0,0,0,.2);text-align:center}
.tally b{display:block;font:400 30px 'DM Serif Display',SerifLocal,serif} .tally span{font:700 13px 'Josefin Sans',sans-serif;letter-spacing:.14em;color:#9FC7B4}"""
    dealer = f'<div class="s2"><em>DEALER</em><div class="hand">{"".join(pc(c, 70) for c in bd["dealer"])}</div></div>' if bd["dealer"] else ""
    body = (f'<div class="card"><div class="head"><div class="t1">Three Card Poker<small>THE BEST HAND DEALT</small></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#7CE0A0", "#FF8A8A")}<div class="felt"><div class="lab"><span>{esc(_tcp_rank(bd["hand"]).upper())}{" · BONUS " + str(bd["bonus"]) + "×" if bd["bonus"] else ""}</span>'
            f'<span>{sgn(int(best["net"]))}</span></div><div class="tc"><div class="s2"><em>YOU</em><div class="hand">{"".join(pc(c, 104) for c in bd["player"])}</div></div>{dealer}</div>'
            f'<div class="tally">{cells}</div></div><div class="stats">'
            + _stat("BEST HAND", esc(_tcp_rank(bd["hand"])), sgn(int(best["net"])), "color:#E2BE78")
            + _stat("BEAT THE DEALER", f"{beat} of {len(st.rounds)}", "hands")
            + _stat("WAGERED", f"{st.wagered:,}", "UKP") + "</div></div>")
    return css, body


_VP_ROWS = [(9, "ROYAL FLUSH"), (8, "STRAIGHT FLUSH"), (7, "FOUR OF A KIND"), (6, "FULL HOUSE"), (5, "FLUSH"),
            (4, "STRAIGHT"), (3, "THREE OF A KIND"), (2, "TWO PAIR"), (1, "JACKS OR BETTER")]


def _videopoker(st: _Sitting) -> tuple[str, str]:
    try:
        from commands.economy import video_poker as VP
        pays = {cat: VP._pays(cat) for cat, _ in _VP_ROWS}
    except Exception:
        pays = {9: 25, 8: 20, 7: 16, 6: 9, 5: 7, 4: 4, 3: 3, 2: 2, 1: 1}
    hits = Counter(h["d"]["row"] for h in st.rounds if h["d"]["row"] is not None)
    best = max(st.rounds, key=lambda h: (h["d"]["row"] if h["d"]["row"] is not None else 0, int(h["net"])))
    bd = best["d"]
    rows = "".join(f'<div class="{"on" if bd["row"] == cat else ""}"><span>{name}</span><i>{"×" + str(hits[cat]) if hits[cat] else ""}</i><span>{pays.get(cat, "")}</span></div>'
                   for cat, name in _VP_ROWS)
    paid = sum(1 for h in st.rounds if h["d"]["row"] is not None)
    label = dict(_VP_ROWS).get(bd["row"], "NOTHING")
    css = """
.card{color:#FFE14D;background:linear-gradient(180deg,#0B1A8C 0%,#06106A 60%,#040A45 100%)}
.t1{font:400 50px/46px VT323,monospace;letter-spacing:.04em;color:#FFE14D;text-shadow:0 0 12px rgba(255,225,77,.5)} .t1 small{display:block;font:700 15px Archivo,ArchivoLocal;letter-spacing:.22em;color:#8FA0FF;margin-top:8px;text-shadow:none}
.who{color:#8FA0FF} .net b{font:400 104px/90px VT323,monospace;text-shadow:0 0 18px rgba(124,255,178,.35)} .net span{color:#8FA0FF}
.ptab{padding:14px 18px;border-radius:12px;background:#000A3C;box-shadow:inset 0 0 0 3px #2B3BCF}
.ptab div{display:flex;justify-content:space-between;font:400 30px/36px VT323,monospace;color:#FFE14D} .ptab div.on{background:#C21F1F;color:#fff;border-radius:4px;padding:0 6px;margin:0 -6px}
.ptab div i{font-style:normal;color:#8FA0FF;margin-left:auto;margin-right:26px} .ptab div.on i{color:#FFD0D0}
.best{display:flex;gap:10px;justify-content:center;margin-top:18px} .lbl{text-align:center;font:400 34px VT323,monospace;color:#fff;margin-top:10px;letter-spacing:.1em}
.s{background:#000A3C;box-shadow:inset 0 0 0 2px #2B3BCF} .s span,.s small{color:#8FA0FF} .s b{font-family:VT323,monospace;font-weight:400;font-size:44px;line-height:42px}
"""
    body = (f'<div class="card"><div class="head"><div class="t1">VIDEO POKER<small>JACKS OR BETTER</small></div><div class="who">{st.who}</div></div>'
            f'{_net(st.net, "#7CFFB2", "#FF9A9A")}<div class="ptab">{rows}</div>'
            f'<div class="best">{"".join(pc(c, 112) for c in bd["cards"])}</div><div class="lbl">{label}{f" · PAID {pays.get(bd["row"], 0)}" if bd["row"] is not None else ""}</div><div class="stats">'
            + _stat("BEST HAND", label, sgn(int(best["net"])), "color:#FFE14D")
            + _stat("PAID", f"{paid} of {len(st.rounds)}", "hands")
            + _stat("WAGERED", f"{st.wagered:,}", "UKP") + "</div></div>")
    return css, body


BUILDERS = {
    "plinko": _plinko, "glass": _glass, "mines": _mines, "chest": _chest, "blockade": _blockade, "darts": _darts,
    "penalty": _penalty, "pennyfalls": _pennyfalls, "roulette": _roulette, "slots": _slots, "blackjack": _blackjack,
    "higherlower": _higherlower, "reddog": _reddog, "tcp": _tcp, "videopoker": _videopoker,
}


def summary_html(key: str, unit: str, history: list[dict], minutes: int) -> str | None:
    """This game's own picture of the sitting, or None to use the plain one."""
    build = BUILDERS.get(key)
    if not build:
        return None
    st = _Sitting(history, unit, minutes)
    # rounds from before digests existed can't be drawn; with too few that can, use the plain card
    if not st.rounds or (len(st.rounds) < 2 and len(st.rounds) < len(history)):
        return None
    try:
        css, body = build(st)
    except Exception:
        log.warning("couldn't draw the %s summary; using the plain one", key, exc_info=True)
        return None
    return _page(css, body)
