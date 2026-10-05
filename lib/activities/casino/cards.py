"""Pictures for the activity casino's #casino posts.

While someone plays, their post carries a small strip (drawn with Pillow, so it can change
every few seconds without touching the bot's one headless Chrome). When they leave the table
the strip is swapped, once, for a card rendered from HTML: the final board if they played a
single round, or a summary of the sitting if they played more. Big wins get the result card.

The game tiles in the strip are rendered from HTML the first time a game is played and kept
on disk, so Chrome only ever draws each one once.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import html
import io
import logging
from pathlib import Path

import config

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
FONT = ROOT / "data" / "fonts" / "Archivo.ttf"
SERIF = ROOT / "data" / "fonts" / "DMSerifDisplay.ttf"     # card faces, as in the activity
ART = ROOT / "data" / "activity_casino"
EMOJI = ROOT / "data" / "emoji"
TILE_VERSION = 1

INK, PANEL, MUTED, TEXT, SOFT = "#111214", "#1E1F22", "#949BA4", "#F2F3F5", "#B5BAC1"
GREEN, RED, GOLD, EVEN = "#23A55A", "#F23F43", "#E9C46A", "#4E5058"

# Each game's scene, the same as its table in the activity.
FELT = "radial-gradient(ellipse 100% 90% at 50% 45%, #1e7351 0%, #12503a 55%, #0a2e22 100%)"
SCENE = {
    "blackjack": FELT, "higherlower": FELT, "videopoker": FELT, "reddog": FELT, "tcp": FELT, "roulette": FELT,
    "mines": "linear-gradient(180deg, #0a1530 0%, #102650 55%, #0b1b3a 100%)",
    "slots": "radial-gradient(ellipse 120% 80% at 50% 40%, #2a4fa8 0%, #1a3580 50%, #0f2257 100%)",
    "chest": "radial-gradient(ellipse 90% 80% at 50% 50%, #6a4618 0%, #2b1a0b 60%, #140c06 100%)",
    "glass": "#0b0b0e",
    "blockade": "linear-gradient(180deg, #071226 0%, #0e2747 50%, #1b4a73 64%, #061a2e 100%)",
    "darts": "radial-gradient(ellipse 100% 80% at 50% 50%, #4a1b1b 0%, #2a0f0f 60%, #150707 100%)",
    "penalty": "linear-gradient(180deg, #0a1f12 0%, #123a21 40%, #1b5a2e 70%, #164d27 100%)",
    "pennyfalls": "radial-gradient(ellipse 100% 80% at 60% 20%, #4a1650 0%, #24103a 55%, #120a26 100%)",
    "plinko": "radial-gradient(ellipse 110% 80% at 50% 25%, #16406e 0%, #0c2340 55%, #071526 100%)",
}
# A flat colour per game, for when a tile can't be rendered.
SOLID = {"mines": "#102650", "slots": "#1a3580", "chest": "#2b1a0b", "glass": "#0b0b0e", "blockade": "#0e2747",
         "darts": "#2a0f0f", "penalty": "#123a21", "pennyfalls": "#24103a", "plinko": "#0c2340"}

SUIT = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
ANCHOR = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
          'stroke-linejoin="round"><circle cx="12" cy="4.6" r="2.1"/><path d="M12 6.7V21"/><path d="M8.6 9.6h6.8"/>'
          '<path d="M4.6 13.6c.6 4.6 3.6 7.2 7.4 7.4c3.8-.2 6.8-2.8 7.4-7.4"/></svg>')
COIN = ('<svg viewBox="0 0 40 40"><circle cx="20" cy="20" r="15" fill="#d9a93a"/>'
        '<circle cx="20" cy="20" r="15" fill="none" stroke="#9c7518" stroke-width="2.4"/>'
        '<circle cx="20" cy="20" r="10.8" fill="none" stroke="#b48722" stroke-width="1.3" stroke-dasharray="1.6 1.6"/>'
        '<g fill="none" stroke="#8a6512" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
        'transform="translate(11.5 10.5) scale(.72)"><circle cx="12" cy="4.6" r="2.1"/><path d="M12 6.7V21"/>'
        '<path d="M8.6 9.6h6.8"/><path d="M4.6 13.6c.6 4.6 3.6 7.2 7.4 7.4c3.8-.2 6.8-2.8 7.4-7.4"/></g>'
        '<path d="M9.5 13A12 12 0 0 1 17 7.4" fill="none" stroke="#f4d883" stroke-width="1.6" stroke-linecap="round"/></svg>')
BALL = ('<svg viewBox="0 0 40 40"><defs><clipPath id="sh{n}"><circle cx="18" cy="23" r="12"/></clipPath></defs>'
        '<circle cx="18" cy="23" r="12" fill="#4e5b6c"/><circle cx="22" cy="27" r="11" fill="#36414e" clip-path="url(#sh{n})"/>'
        '<ellipse cx="13" cy="18" rx="3.2" ry="2.1" fill="#8999ac" transform="rotate(-30 13 18)"/>'
        '<path d="M25 14Q27.5 9.5 31 8" fill="none" stroke="#9c7a4c" stroke-width="2.2" stroke-linecap="round"/>{spark}</svg>')
SPARK = ('<path d="M32.5 1.5L33.7 5.3L37.5 6.5L33.7 7.7L32.5 11.5L31.3 7.7L27.5 6.5L31.3 5.3Z" fill="#ffb23e"/>'
         '<circle cx="32.5" cy="6.5" r="1.5" fill="#fff1c4"/>')
WHEEL = ('<svg viewBox="0 0 30 30"><circle cx="15" cy="15" r="14.5" fill="#8a6a2f"/>'
         '<circle cx="15" cy="15" r="11" fill="none" stroke="#141414" stroke-width="5"/>'
         '<circle cx="15" cy="15" r="11" fill="none" stroke="#c8202f" stroke-width="5" stroke-dasharray="1.87 1.87"/>'
         '<circle cx="15" cy="15" r="7.2" fill="#145040"/><circle cx="15" cy="15" r="2.6" fill="#e2be78"/>'
         '<circle cx="21.5" cy="9.5" r="1.1" fill="#fff"/></svg>')
PENNY = ('<svg viewBox="0 0 40 40"><circle cx="20" cy="20" r="16" fill="#c8784a"/>'
         '<circle cx="20" cy="20" r="16" fill="none" stroke="#8f4d28" stroke-width="2.4"/>'
         '<circle cx="20" cy="20" r="11.5" fill="none" stroke="#a65f37" stroke-width="1.3" stroke-dasharray="1.6 1.6"/>'
         '<g fill="none" stroke="#7a3d1a" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
         'transform="translate(11.5 10.5) scale(.72)"><circle cx="12" cy="4.6" r="2.1"/><path d="M12 6.7V21"/>'
         '<path d="M8.6 9.6h6.8"/><path d="M4.6 13.6c.6 4.6 3.6 7.2 7.4 7.4c3.8-.2 6.8-2.8 7.4-7.4"/></g>'
         '<path d="M9 13A12.5 12.5 0 0 1 17 7" fill="none" stroke="#f0b27e" stroke-width="1.6" stroke-linecap="round"/></svg>')
def _slot_colour(mult: float, top: float) -> str:
    """Plinko's slots run gold in the middle to red at the edges, by how much they pay."""
    import math
    t = 0.0 if mult <= 1 else min(1.0, math.log(mult) / math.log(max(top, 2)))
    a, b = (0xF5, 0xC5, 0x42), (0xE8, 0x43, 0x3A)
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def plinko_board(v: dict | None, width: int = 600) -> str:
    """The Plinko board as an SVG: the pegs, the ball's path down them, and the slots with the
    one it landed in lit. With no ball (v None) it's just the pegs and the medium slots."""
    from lib.activities.casino.games import plinko as P
    rows = P.ROWS
    risk = (v or {}).get("risk", "medium")
    table = [t / 10 for t in P.TENTHS.get(risk, P.TENTHS["medium"])]
    dx = width / (rows + 2)
    dy = dx * 0.86
    top, mid = dx * 0.7, width / 2
    height = top + rows * dy + dx * 1.05
    out = [f'<svg class="plinko" viewBox="0 0 {width} {height:.0f}" xmlns="http://www.w3.org/2000/svg">']
    for r in range(rows):
        for j in range(r + 3):
            x = mid + (j - (r + 2) / 2) * dx
            out.append(f'<circle cx="{x:.1f}" cy="{top + r * dy:.1f}" r="{dx * 0.11:.1f}" fill="#e9eef7"/>')
    path = (v or {}).get("path") or []
    slot = (v or {}).get("slot")
    if path:
        x, pts = mid, [(mid, top - dy * 0.8)]
        for r, step in enumerate(path):
            pts.append((x, top + r * dy - dx * 0.24))
            x += dx / 2 if step else -dx / 2
        pts.append((x, top + rows * dy + dx * 0.15))
        line = " ".join(f"{a:.1f},{b:.1f}" for a, b in pts)
        out.append(f'<polyline points="{line}" fill="none" stroke="#ff5fa8" stroke-width="{dx * 0.12:.1f}" '
                   'stroke-linejoin="round" stroke-linecap="round" opacity=".9"/>')
    sy = top + rows * dy + dx * 0.25
    for i, m in enumerate(table):
        x = mid + (i - rows / 2) * dx
        hit = i == slot
        w, h = dx * (0.98 if hit else 0.86), dx * (0.72 if hit else 0.62)
        edge = ' stroke="#fff" stroke-width="4"' if hit else ""
        out.append(f'<rect x="{x - w / 2:.1f}" y="{sy:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{dx * 0.14:.1f}" '
                   f'fill="{_slot_colour(m, max(table))}"{edge}/>')
        out.append(f'<text x="{x:.1f}" y="{sy + h * 0.66:.1f}" text-anchor="middle" font-family="ArchivoV" '
                   f'font-weight="900" font-size="{dx * (0.34 if len(f"{m:g}") < 3 else 0.28):.1f}" fill="#1a1206">{m:g}×</text>')
    if path and slot is not None:
        bx = mid + (slot - rows / 2) * dx
        out.append(f'<circle cx="{bx:.1f}" cy="{sy - dx * 0.2:.1f}" r="{dx * 0.24:.1f}" fill="#ff5fa8" stroke="#fff" stroke-width="3"/>')
    out.append("</svg>")
    return "".join(out)


PLINKO_ICON = ('<svg viewBox="0 0 40 40"><g fill="#e9eef7"><circle cx="20" cy="9" r="2"/><circle cx="14" cy="16" r="2"/>'
               '<circle cx="26" cy="16" r="2"/><circle cx="8" cy="23" r="2"/><circle cx="20" cy="23" r="2"/>'
               '<circle cx="32" cy="23" r="2"/></g><rect x="3" y="30" width="9" height="6" rx="2" fill="#e8433a"/>'
               '<rect x="15.5" y="30" width="9" height="6" rx="2" fill="#f5c542"/><rect x="28" y="30" width="9" height="6" rx="2" fill="#e8433a"/>'
               '<circle cx="23" cy="12" r="3.2" fill="#ff5fa8" stroke="#fff" stroke-width="1.2"/></svg>')
CHERRIES = ('<svg viewBox="0 0 26 26"><path d="M9 17 C10 10 14 6 19 4 M17 17 C17 11 18 7 19 4" stroke="#5fbf4a" '
            'stroke-width="1.6" fill="none" stroke-linecap="round"/><path d="M19 4 C21 3 23 4 24 6 C22 6 20 5.5 19 4 Z" '
            'fill="#5fbf4a"/><circle cx="8" cy="19" r="5" fill="#e02a44"/><circle cx="18" cy="19" r="5" fill="#b5172c"/>'
            '<circle cx="6.5" cy="17.5" r="1.3" fill="#ff8a97"/></svg>')

esc = html.escape


def signed(n: int) -> str:
    return f"+{n:,}" if n > 0 else f"−{abs(n):,}" if n < 0 else "0"


@functools.lru_cache(maxsize=None)
def _data_uri(path: str) -> str:
    p = Path(path)
    mime = {"svg": "image/svg+xml", "webp": "image/webp", "png": "image/png"}.get(p.suffix[1:], "application/octet-stream")
    try:
        return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"
    except OSError:
        return ""


def _img(name: str, cls: str = "") -> str:
    return f'<img class="{cls}" src="{_data_uri(str(ART / name))}" alt="">'


# ---- playing cards -----------------------------------------------------------------------

def _card(code, cls: str = "") -> str:
    if not code:
        return f'<div class="pc back {cls}">{ANCHOR}</div>'
    rank = "10" if code[0] == "T" else code[0]
    suit = SUIT.get(code[1], "")
    red = " red" if code[1] in "HD" else ""
    return f'<div class="pc{red} {cls}"><b>{rank}</b><span>{suit}</span></div>'


def _cards(codes, cls: str = "") -> str:
    return "".join(_card(c, cls) for c in codes)


# ---- each game's picture (tiles and header chips) ------------------------------------------

def art(key: str) -> str:
    if key == "blackjack":
        return f'<div class="fan">{_card("AS", "l")}{_card("KH", "r")}</div>'
    if key == "higherlower":
        return f'<div class="row">{_card("9H")}<svg class="arrows" viewBox="0 0 8 22"><path d="M4 1 L7.5 7 H0.5 Z" fill="#4ade80"/><path d="M4 21 L0.5 15 H7.5 Z" fill="#f87171"/></svg></div>'
    if key == "videopoker":
        return f'<div class="row sm">{_cards(["JS", "JH", "KC"])}</div>'
    if key == "reddog":
        return f'<div class="row sm">{_card("4C")}{_card(None)}{_card("JH")}</div>'
    if key == "tcp":
        return f'<div class="row sm">{_card(None)}{_card(None)}{_card(None)}</div>'
    if key == "roulette":
        return f'<div class="icon">{WHEEL}</div>'
    if key == "slots":
        return f'<div class="icon">{CHERRIES}</div>'
    if key == "mines":
        return f'<div class="icon">{COIN}</div>'
    if key == "chest":
        return _img("chest-gold.webp", "pic")
    if key == "glass":
        return '<div class="panes"><i></i><i class="held"></i></div>'
    if key == "blockade":
        return _img("ship-sailing.webp", "pic")
    if key == "darts":
        return _img("darts-board.webp", "pic")
    if key == "penalty":
        return _img("ball.webp", "pic ball")
    if key == "pennyfalls":
        return f'<div class="pennies"><i>{PENNY}</i><i>{PENNY}</i><i>{PENNY}</i></div>'
    if key == "plinko":
        return f'<div class="icon">{PLINKO_ICON}</div>'
    return ""


BASE_CSS = f"""
@font-face {{ font-family: 'ArchivoV'; src: url('file://{FONT}') format('truetype'); font-weight: 100 900; }}
@font-face {{ font-family: 'CardSerif'; src: url('file://{SERIF}') format('truetype'); }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ background: {INK}; font-family: 'ArchivoV', 'Archivo', system-ui, sans-serif; color: {TEXT}; -webkit-font-smoothing: antialiased; }}
.pc {{ position: relative; width: 96px; height: 134px; flex: none; border-radius: 10px; background: #f5f5f4; color: #171717; padding: 8px 10px;
       display: flex; flex-direction: column; font-family: 'CardSerif', Georgia, serif; box-shadow: 0 10px 22px rgba(0,0,0,.4); }}
.pc b {{ font-weight: 400; font-size: 40px; line-height: 40px; }}
.pc span {{ font-family: system-ui, sans-serif; font-size: 28px; line-height: 32px; }}
.pc.red {{ color: #dc2626; }}
.pc.back {{ background: #1b2a5c; color: #e2be78; align-items: center; justify-content: center;
            box-shadow: inset 0 0 0 4px #f5f5f4, inset 0 0 0 6px rgba(226,190,120,.6), 0 10px 22px rgba(0,0,0,.4); }}
.pc.back svg {{ width: 56%; }}
.pc.held {{ box-shadow: 0 0 0 5px {GOLD}, 0 10px 22px rgba(0,0,0,.4); }}
.fan {{ position: relative; width: 170px; height: 160px; }}
.fan .pc {{ position: absolute; }}
.fan .l {{ left: 0; top: 18px; transform: rotate(-10deg); }}
.fan .r {{ left: 66px; top: 4px; transform: rotate(8deg); }}
.row {{ display: flex; align-items: center; gap: 10px; }}
.row.sm .pc {{ width: 60px; height: 84px; border-radius: 7px; padding: 4px 6px; }}
.row.sm .pc b {{ font-size: 26px; line-height: 26px; }}
.row.sm .pc span {{ font-size: 18px; line-height: 20px; }}
.arrows {{ width: 22px; height: 60px; }}
.icon svg {{ display: block; width: 100%; height: 100%; }}
.panes {{ display: flex; gap: 12px; }}
.panes i {{ width: 56px; height: 92px; border-radius: 6px; background: rgba(255,255,255,.22); box-shadow: inset 0 0 0 2px rgba(255,255,255,.5); }}
.panes i.held {{ background: #2f8f5b; box-shadow: inset 0 0 0 2px rgba(134,239,172,.8); }}
.pic {{ display: block; object-fit: contain; }}
.pennies {{ position: relative; width: 150px; height: 130px; }}
.pennies i {{ position: absolute; width: 84px; height: 84px; filter: drop-shadow(0 8px 10px rgba(0,0,0,.45)); }}
.pennies i svg {{ display: block; width: 100%; height: 100%; }}
.pennies i:nth-child(1) {{ right: 0; bottom: 0; transform: scaleY(.55); }}
.pennies i:nth-child(2) {{ right: 8px; bottom: 13px; transform: scaleY(.55); }}
.pennies i:nth-child(3) {{ right: 54px; top: 0; transform: rotate(-24deg) scaleY(.8); }}
"""


def tile_html(key: str) -> str:
    """A 560px square of the game's scene with its picture, for the live strip."""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}
.tile {{ width: 560px; height: 560px; display: flex; align-items: center; justify-content: center; background: {SCENE.get(key, INK)}; }}
.tile .in {{ zoom: 2.1; display: flex; align-items: center; justify-content: center; }}
.tile .icon {{ width: 130px; height: 130px; }}
.tile .pic {{ width: 150px; height: 150px; }}
.tile .pic.ball {{ width: 110px; height: 110px; }}
</style></head><body><div class="tile"><div class="in">{art(key)}</div></div></body></html>"""


# ---- the final board of one round, per game ------------------------------------------------

def _who(label: str, value) -> str:
    return f'<div class="who">{esc(label)}{f" <b>{esc(str(value))}</b>" if value is not None else ""}</div>'


def board_html(key: str, v: dict) -> str:
    """The inside of the result card's big panel: the round as it ended."""
    if not v:
        return f'<div class="big-art"><div class="in">{art(key)}</div></div>'
    if key == "blackjack":
        return (f'<div class="stack">{_who("DEALER", v.get("dealerTotal"))}<div class="row">{_cards(v.get("dealer", []))}</div>'
                f'{_who("PLAYER", v.get("playerTotal"))}<div class="row">{_cards(v.get("player", []))}</div></div>')
    if key == "higherlower":
        run = v.get("run", [])
        return (f'<div class="stack"><div class="row wrap">{_cards(run, "mid")}</div>'
                f'<div class="caption">{v.get("steps", 0)} right call{"s" if v.get("steps", 0) != 1 else ""}</div></div>')
    if key == "videopoker":
        cards = "".join(_card(c, "held" if h else "") for c, h in zip(v.get("cards", []), v.get("held", [])))
        return f'<div class="stack"><div class="row">{cards}</div><div class="caption">{esc(v.get("hand") or "")}</div></div>'
    if key == "reddog":
        spread = v.get("spread")
        cap = "A pair" if v.get("pair") else "Consecutive" if v.get("consecutive") else f"Spread of {spread}" if spread is not None else ""
        return (f'<div class="stack"><div class="row">{_card(v.get("first"))}{_card(v.get("third"), "big")}{_card(v.get("second"))}</div>'
                f'<div class="caption">{esc(cap)}</div></div>')
    if key == "tcp":
        return (f'<div class="stack">{_who("DEALER", v.get("dealerHand"))}<div class="row">{_cards(v.get("dealer", []))}</div>'
                f'{_who("PLAYER", v.get("playerHand"))}<div class="row">{_cards(v.get("player", []))}</div></div>')
    if key == "roulette":
        from commands.economy import roulette as R
        n, col = v.get("number"), v.get("color", "")
        disc = {"red": "#c8202f", "black": "#141414"}.get(col, "#1e7a4a")
        won = set(v.get("won", []))
        chips = "".join(f'<div class="chip{" on" if k in won else ""}"><span>{esc(R.bet_label(k))}</span><b>{amt:,}</b></div>'
                        for k, amt in (v.get("bets") or {}).items())
        return (f'<div class="stack"><div class="disc" style="background:{disc}">{n}</div>'
                f'<div class="chips">{chips}</div></div>')
    if key == "slots":
        reels = "".join(f'<div class="reel"><img src="{_data_uri(str(EMOJI / f"{r}.svg"))}" alt=""></div>' for r in v.get("reels", []))
        return f'<div class="stack"><div class="cabinet">{reels}</div><div class="caption">{v.get("mult", 0)}× on the line</div></div>'
    if key == "mines":
        revealed, mines, hit = set(v.get("revealed", [])), set(v.get("minesAt") or []), v.get("hit")
        tiles = []
        for i in range(v.get("tiles", 24)):
            if i in revealed:
                tiles.append(f'<div class="mt coin">{COIN}</div>')
            elif i == hit:
                tiles.append(f'<div class="mt boom">{BALL.format(n=i, spark=SPARK)}</div>')
            elif i in mines:
                tiles.append(f'<div class="mt mine">{BALL.format(n=i, spark="")}</div>')
            else:
                tiles.append(f'<div class="mt ghost">{COIN}</div>')
        return f'<div class="mines" style="--cols:{v.get("cols", 4)}">{"".join(tiles)}</div>'
    if key == "chest":
        tiers = v.get("tiers", [])
        tier = int(v.get("tier", 0))
        name = str(tiers[tier].get("name", "")).lower() if tier < len(tiers) else ""
        pic = f"chest-{name}.webp" if (ART / f"chest-{name}.webp").exists() else "chest-gold.webp"
        if v.get("outcome") == "lose":
            pic = "chest-shattered.webp"
        ladder = "".join(f'<div class="step{" on" if i == tier else ""}">{esc(t.get("name", ""))} · {t.get("mult", 0)}×</div>'
                         for i, t in enumerate(tiers))
        return f'<div class="stack">{_img(pic, "pic hero-pic")}<div class="ladder">{ladder}</div></div>'
    if key == "glass":
        return f'<div class="glass">{v.get("scene", "")}</div>'
    if key == "blockade":
        won = v.get("outcome") == "win"
        cap = f'Cashed out at {v.get("mult", 0)}×' if won else f'Caught at {v.get("caught") or v.get("mult", 0)}×'
        return f'<div class="stack">{_img("ship-escaped.webp" if won else "ship-sunk.webp", "pic hero-pic")}<div class="caption">{esc(cap)}</div></div>'
    if key == "darts":
        throws = "".join(f'<div class="chip"><span>{esc(str(t.get("label", "")))}</span><b>{t.get("value", 0)}</b></div>'
                         for t in v.get("throws", []))
        return (f'<div class="stack">{_img("darts-board.webp", "pic hero-pic")}<div class="chips">{throws}</div>'
                f'<div class="caption">Total {v.get("total", 0)}</div></div>')
    if key == "pennyfalls":
        dropped, won = int(v.get("dropped", 0)), int(v.get("won", 0))
        pile = "".join(f"<i>{PENNY}</i>" for _ in range(min(12, max(3, won // 4))))
        return (f'<div class="stack"><div class="pile">{pile}</div>'
                f'<div class="caption">{won:,} coin{"s" if won != 1 else ""} back from {dropped:,}</div></div>')
    if key == "plinko":
        return (f'<div class="stack">{plinko_board(v)}'
                f'<div class="caption">{v.get("mult", 0):g}× on {esc(str(v.get("risk", "")))} risk</div></div>')
    if key == "penalty":
        goals, shots = int(v.get("goals", 0)), int(v.get("shots", 5))
        dots = "".join(f'<i class="{"on" if k < goals else ""}"></i>' for k in range(shots))
        return (f'<div class="stack">{_img("keeper-ready.webp", "pic wide-pic")}<div class="dots">{dots}</div>'
                f'<div class="caption">{goals} goal{"s" if goals != 1 else ""}</div></div>')
    return f'<div class="big-art"><div class="in">{art(key)}</div></div>'


CARD_CSS = f"""
.card {{ width: 820px; padding: 34px; background: {INK}; display: flex; flex-direction: column; gap: 22px; }}
.head {{ display: flex; align-items: center; gap: 20px; }}
.chipicon {{ width: 84px; height: 84px; flex: none; border-radius: 20px; overflow: hidden; display: flex; align-items: center; justify-content: center; }}
.chipicon .in {{ zoom: .5; display: flex; align-items: center; justify-content: center; }}
.chipicon .icon {{ width: 110px; height: 110px; }}
.chipicon .pic {{ width: 140px; height: 140px; }}
.titles {{ flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }}
.titles h1 {{ font-weight: 900; font-size: 52px; line-height: 56px; letter-spacing: -.03em; }}
.titles p {{ font-weight: 600; font-size: 26px; line-height: 30px; color: {MUTED}; }}
.pill {{ flex: none; height: 52px; padding: 0 20px; border-radius: 26px; display: flex; align-items: center; font-weight: 900; font-size: 24px; letter-spacing: .02em; }}
.panel {{ position: relative; overflow: hidden; border-radius: 22px; padding: 34px 24px; min-height: 520px; display: flex; align-items: center; justify-content: center; }}
.stats {{ display: flex; gap: 14px; }}
.stat {{ flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; padding: 18px 22px; border-radius: 20px; background: {PANEL}; }}
.stat span {{ font-weight: 600; font-size: 22px; line-height: 26px; color: {MUTED}; }}
.stat b {{ font-weight: 900; font-size: 44px; line-height: 48px; letter-spacing: -.02em; white-space: nowrap; }}
.stat small {{ font-weight: 600; font-size: 22px; line-height: 26px; color: {SOFT}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.stack {{ display: flex; flex-direction: column; align-items: center; gap: 22px; }}
.who {{ font-weight: 700; font-size: 22px; letter-spacing: .14em; color: rgba(255,255,255,.7); }}
.who b {{ color: #fff; letter-spacing: 0; font-size: 26px; margin-left: 6px; }}
.row.wrap {{ flex-wrap: wrap; justify-content: center; max-width: 700px; }}
.pc.mid {{ width: 76px; height: 106px; }}
.pc.big {{ transform: scale(1.15); }}
.caption {{ font-weight: 800; font-size: 30px; color: #fff; text-align: center; }}
.disc {{ width: 200px; height: 200px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 900; font-size: 96px;
         box-shadow: inset 0 0 0 10px rgba(255,255,255,.12), 0 14px 30px rgba(0,0,0,.4); }}
.chips {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; max-width: 700px; }}
.chips .chip {{ display: flex; align-items: center; gap: 10px; height: 48px; padding: 0 16px; border-radius: 24px; background: rgba(0,0,0,.35); font-size: 22px; }}
.chips .chip span {{ font-weight: 600; color: rgba(255,255,255,.75); }}
.chips .chip b {{ font-weight: 900; }}
.chips .chip.on {{ background: rgba(35,165,90,.3); box-shadow: inset 0 0 0 2px {GREEN}; }}
.cabinet {{ display: flex; gap: 14px; padding: 18px; border-radius: 18px; background: #0b1736; box-shadow: inset 0 0 0 3px #e2be78; }}
.reel {{ width: 150px; height: 180px; border-radius: 12px; background: linear-gradient(#fbf8f0, #e9e3d3); display: flex; align-items: center; justify-content: center; }}
.reel img {{ width: 104px; height: 104px; }}
.mines {{ display: grid; grid-template-columns: repeat(var(--cols), 104px); gap: 9px; }}
.mt {{ height: 80px; border-radius: 3px; display: flex; align-items: center; justify-content: center; background: rgba(20,42,82,.5); }}
.mt svg {{ width: 50px; height: 50px; }}
.mt.coin {{ background: rgba(217,169,58,.16); box-shadow: inset 0 0 0 2px rgba(233,196,106,.85), 0 0 22px rgba(233,196,106,.25); }}
.mt.mine {{ background: #0d1a33; box-shadow: inset 0 0 0 1.5px rgba(255,122,134,.4); }}
.mt.boom {{ background: rgba(255,77,94,.26); box-shadow: inset 0 0 0 2px #ff4d5e, 0 0 24px rgba(255,77,94,.45); }}
.mt.mine svg, .mt.boom svg {{ width: 56px; height: 56px; }}
.mt.ghost svg {{ opacity: .28; width: 44px; height: 44px; }}
.hero-pic {{ width: 300px; height: 300px; }}
.wide-pic {{ width: 600px; border-radius: 14px; }}
.ladder {{ display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; }}
.ladder .step {{ height: 44px; padding: 0 16px; border-radius: 22px; display: flex; align-items: center; background: rgba(0,0,0,.35); font-weight: 700; font-size: 22px; color: rgba(255,255,255,.7); }}
.ladder .step.on {{ background: {GOLD}; color: #1a1205; }}
.glass {{ width: 100%; display: flex; justify-content: center; }}
.glass svg {{ width: 100%; max-height: 560px; height: auto; }}
.plinko {{ display: block; width: 600px; height: auto; }}
.pile {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 6px; max-width: 560px; }}
.pile i {{ width: 82px; height: 82px; filter: drop-shadow(0 8px 10px rgba(0,0,0,.4)); }}
.pile i svg {{ display: block; width: 100%; height: 100%; }}
.dots {{ display: flex; gap: 14px; }}
.dots i {{ width: 30px; height: 30px; border-radius: 50%; background: rgba(255,255,255,.18); }}
.dots i.on {{ background: #fff; box-shadow: 0 0 0 4px rgba(255,255,255,.25); }}
.big-art .in {{ zoom: 1.6; }}
.netbox {{ position: relative; overflow: hidden; border-radius: 22px; background: {PANEL}; padding: 26px 28px 0; height: 300px; display: flex; flex-direction: column; gap: 4px; }}
.netbox span {{ font-weight: 600; font-size: 24px; line-height: 28px; color: {MUTED}; }}
.netbox .num {{ display: flex; align-items: baseline; gap: 12px; }}
.netbox .num b {{ font-weight: 900; font-size: 96px; line-height: 96px; letter-spacing: -.04em; }}
.netbox .num i {{ font-style: normal; font-weight: 700; font-size: 30px; color: {MUTED}; }}
.netbox svg {{ position: absolute; left: 0; bottom: 0; }}
.bars {{ display: flex; flex-direction: column; gap: 12px; padding: 20px 24px; border-radius: 22px; background: {PANEL}; }}
.bars .top {{ display: flex; justify-content: space-between; font-size: 22px; line-height: 26px; }}
.bars .top span {{ font-weight: 600; color: {MUTED}; }}
.bars .top b {{ font-weight: 700; }}
.bars .plot {{ display: flex; align-items: stretch; gap: 8px; height: 120px; }}
.bars .col {{ flex: 1; min-width: 0; display: flex; flex-direction: column; }}
.bars .col div {{ flex: 1; display: flex; }}
.bars .col .up {{ align-items: flex-end; }}
.bars .col i {{ display: block; width: 100%; }}
.bars .col .up i {{ border-radius: 6px 6px 0 0; }}
.bars .col .down i {{ border-radius: 0 0 6px 6px; }}
"""


def _page(body: str, key: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}{CARD_CSS}
.chipicon, .panel {{ background: {SCENE.get(key, INK)}; }}
</style></head><body>{body}</body></html>"""


def _head(key: str, label: str, sub: str, pill: tuple[str, str] | None) -> str:
    badge = ""
    if pill:
        text, colour = pill
        badge = f'<div class="pill" style="color:{colour};background:{colour}29">{esc(text)}</div>'
    return (f'<div class="head"><div class="chipicon"><div class="in">{art(key)}</div></div>'
            f'<div class="titles"><h1>{esc(label)}</h1><p>{esc(sub)}</p></div>{badge}</div>')


def _colour(n: int) -> str:
    return GREEN if n > 0 else RED if n < 0 else TEXT


def result_html(key: str, label: str, sub: str, rnd: dict, view: dict | None, *, big: bool = False) -> str:
    """One round, as it ended: the board, then what it paid."""
    net = int(rnd.get("net", 0))
    pill = ("BIG WIN", GOLD) if big else ("WON", GREEN) if net > 0 else ("LOST", RED) if net < 0 else ("EVEN", SOFT)
    staked = int(rnd.get("staked", 0))
    mult = (rnd.get("payout", 0) / staked) if staked else 0
    stats = (f'<div class="stats"><div class="stat"><span>Result</span><b style="color:{_colour(net)}">{signed(net)}</b></div>'
             f'<div class="stat"><span>Multiplier</span><b>{mult:.2f}×</b></div>'
             f'<div class="stat"><span>Stake</span><b>{staked:,}</b></div></div>')
    body = (f'<div class="card">{_head(key, label, sub, pill)}<div class="panel">{board_html(key, view or {})}</div>'
            f'{stats}</div>')
    return _page(body, key)


def _spark(totals: list[int], width: int = 752, height: int = 150) -> str:
    pts = [0] + totals
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1
    step = width / max(1, len(pts) - 1)
    xy = [(round(i * step, 1), round(height - 18 - (p - lo) / span * (height - 40), 1)) for i, p in enumerate(pts)]
    line = " ".join(f"{'M' if i == 0 else 'L'}{x} {y}" for i, (x, y) in enumerate(xy))
    colour = _colour(totals[-1] if totals else 0)
    if colour == TEXT:
        colour = MUTED
    lx, ly = xy[-1]
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"><defs><linearGradient id="nf" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{colour}" stop-opacity=".35"/><stop offset="1" stop-color="{colour}" stop-opacity="0"/></linearGradient></defs>'
            f'<path d="{line} L{width} {height} L0 {height} Z" fill="url(#nf)"/>'
            f'<path d="{line}" fill="none" stroke="{colour}" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{lx}" cy="{ly}" r="7" fill="{colour}" stroke="{PANEL}" stroke-width="3"/></svg>')


def summary_html(key: str, label: str, unit: str, history: list[dict], minutes: int) -> str:
    """Several rounds: the session's net and how it got there."""
    nets = [int(h["net"]) for h in history]
    net = sum(nets)
    totals, run = [], 0
    for n in nets:
        run += n
        totals.append(run)
    shown = history[-30:]
    peak = max((abs(int(h["net"])) for h in shown), default=1) or 1
    cols = []
    for h in shown:
        n = int(h["net"])
        pct = max(4, round(abs(n) / peak * 100)) if n else 4
        colour = GOLD if h.get("big") else GREEN if n > 0 else RED if n < 0 else EVEN
        up = f'<i style="height:{pct}%;background:{colour}"></i>' if n >= 0 else ""
        down = f'<i style="height:{pct}%;background:{colour}"></i>' if n < 0 else ""
        cols.append(f'<div class="col"><div class="up">{up}</div><div class="down">{down}</div></div>')
    won = sum(1 for n in nets if n > 0)
    lost = sum(1 for n in nets if n < 0)
    best = max(history, key=lambda h: int(h["net"]))
    worst = min(history, key=lambda h: int(h["net"]))
    wagered = sum(int(h.get("staked", 0)) for h in history)
    best_mult = max((float(h.get("multiple", 0)) for h in history), default=0)
    one = unit.rstrip("s")
    sub = f"{len(history)} {unit if len(history) != 1 else one} · {minutes} min at the table" if minutes else f"{len(history)} {unit}"
    tail = f"last {len(shown)}" if len(history) > len(shown) else f"Each {one}"
    body = f"""<div class="card">{_head(key, label, sub, None)}
<div class="netbox"><span>Net for the session</span><div class="num"><b style="color:{_colour(net)}">{signed(net)}</b><i>UKP</i></div>{_spark(totals)}</div>
<div class="bars"><div class="top"><span>{esc(tail.capitalize())}</span><b>{won} won · {lost} lost</b></div><div class="plot">{"".join(cols)}</div></div>
<div class="stats"><div class="stat"><span>Best {esc(one)}</span><b style="color:{GOLD if int(best['net']) > 0 else TEXT}">{signed(int(best['net']))}</b><small>{esc(best.get('outcome') or ' ')}</small></div>
<div class="stat"><span>Worst {esc(one)}</span><b style="color:{RED if int(worst['net']) < 0 else TEXT}">{signed(int(worst['net']))}</b><small>{esc(worst.get('outcome') or ' ')}</small></div></div>
<div class="stats"><div class="stat"><span>Win rate</span><b>{round(won / len(nets) * 100) if nets else 0}%</b></div>
<div class="stat"><span>Wagered</span><b>{wagered:,}</b></div><div class="stat"><span>Best multiplier</span><b style="color:{GOLD}">{best_mult:.2f}×</b></div></div>
</div>"""
    return _page(body, key)


# ---- rendering -----------------------------------------------------------------------------

async def _shoot(page: str, size=(900, 1600)) -> bytes | None:
    try:
        from lib.core.image_processing import screenshot_html
        buf = await screenshot_html(page, size=size, apply_trim=False, element_selector=".card, .tile")
        return buf.getvalue()
    except Exception:
        log.warning("couldn't render a casino card", exc_info=True)
        return None


def _tile_path(key: str) -> Path:
    base = Path(getattr(config, "JSON_DATA_DIR", "data"))
    return base / "activity_tiles" / f"{key}-v{TILE_VERSION}.png"


_tiles: dict[str, bytes] = {}
_tile_locks: dict[str, asyncio.Lock] = {}


async def tile_png(key: str) -> bytes | None:
    """The game's square picture for the live strip, rendered once and kept on disk."""
    if key in _tiles:
        return _tiles[key]
    lock = _tile_locks.setdefault(key, asyncio.Lock())
    async with lock:
        if key in _tiles:
            return _tiles[key]
        path = _tile_path(key)
        if path.exists():
            _tiles[key] = path.read_bytes()
            return _tiles[key]
        data = await _shoot(tile_html(key), size=(600, 600))
        if data:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            except OSError:
                pass
            _tiles[key] = data
        return data


@functools.lru_cache(maxsize=None)
def _font(size: int, weight: int):
    from PIL import ImageFont
    f = ImageFont.truetype(str(FONT), size)
    try:
        values = []
        for axis in f.get_variation_axes():
            name = axis.get("name", b"")
            name = name.decode() if isinstance(name, bytes) else str(name)
            values.append(weight if "eight" in name.lower() else axis.get("default", 100))
        f.set_variation_by_axes(values)
    except Exception:
        pass
    return f


def _hex(c: str):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def draw_live(key: str, label: str, unit: str, history: list[dict], tile: bytes | None,
              open_net: int = 0, note: str = "", status: str = "PLAYING NOW", watchers: list[str] = ()) -> bytes:
    """The strip shown while someone plays: game, rounds so far, net, and a chip per round.
    ``open_net`` and ``note`` describe a round still going (a cup of coins half played), and
    ``status`` heads it (STEPPED AWAY when they've left one unfinished)."""
    from PIL import Image, ImageDraw
    W, H = 1640, 680
    img = Image.new("RGB", (W, H), _hex(INK))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, H), fill=_hex(INK))
    # the game's picture
    box = (60, 60, 620, 620)
    if tile:
        try:
            pic = Image.open(io.BytesIO(tile)).convert("RGB").resize((560, 560))
            mask = Image.new("L", (560, 560), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, 559, 559), radius=40, fill=255)
            img.paste(pic, box[:2], mask)
        except Exception:
            tile = None
    if not tile:
        d.rounded_rectangle(box, radius=40, fill=_hex(SOLID.get(key, "#12503a")))
    x = 680
    # PLAYING NOW · 4 hands
    n = len(history)
    one = unit.rstrip("s")
    head = _font(40, 900)
    cx = x
    for ch in status:
        d.text((cx, 86), ch, font=head, fill=_hex(RED if status == "PLAYING NOW" else MUTED))
        cx += d.textlength(ch, font=head) + 5
    aside = note or (f"{n} {unit if n != 1 else one}" if n else "just sat down")
    d.text((cx + 22, 86), aside, font=_font(40, 600), fill=_hex(MUTED))
    # the game's name, shrunk to fit
    size = 108
    while size > 60 and d.textlength(label, font=_font(size, 900)) > W - x - 60:
        size -= 6
    d.text((x - 4, 150), label, font=_font(size, 900), fill=_hex(TEXT))
    # who's spectating: "Watched by Pooja, Tyler and 2 more", cut to fit
    if watchers:
        lead, small = "Watched by ", _font(40, 600)
        d.text((x, 300), lead, font=small, fill=_hex(MUTED))
        room = W - 60 - (x + d.textlength(lead, font=small))
        shown = list(watchers)
        while True:
            more = len(watchers) - len(shown)
            names = ", ".join(shown) + (f" and {more} more" if more else "")
            if len(shown) <= 1 or d.textlength(names, font=_font(40, 800)) <= room:
                break
            shown.pop()
        d.text((x + d.textlength(lead, font=small), 300), names, font=_font(40, 800), fill=_hex(GOLD))
    # net so far
    net = sum(int(h["net"]) for h in history) + int(open_net)
    d.text((x, 410), "Net so far", font=_font(44, 600), fill=_hex(MUTED))
    d.text((x - 4, 462), signed(net), font=_font(128, 900), fill=_hex(_colour(net)))
    # a chip per round, newest on the right
    chips = history[-8:]
    cw, ch_, gap = 56, 92, 14
    right, bottom = W - 60, 620
    for i, h in enumerate(reversed(chips)):
        nh = int(h["net"])
        colour = GOLD if h.get("big") else GREEN if nh > 0 else RED if nh < 0 else EVEN
        x1 = right - i * (cw + gap)
        d.rounded_rectangle((x1 - cw, bottom - ch_, x1, bottom), radius=16, fill=_hex(colour))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


async def live_png(key: str, label: str, unit: str, history: list[dict], open_net: int = 0, note: str = "",
                   status: str = "PLAYING NOW", watchers: list[str] = ()) -> bytes | None:
    try:
        tile = await tile_png(key)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, draw_live, key, label, unit, list(history), tile,
                                          open_net, note, status, list(watchers))
    except Exception:
        log.warning("couldn't draw the casino live strip", exc_info=True)
        return None


async def final_png(key: str, label: str, unit: str, history: list[dict], view: dict | None, started: float, ended: float) -> bytes | None:
    """The card a finished sitting leaves behind: the board for one round, a summary for more."""
    if not history:
        return None
    one = unit.rstrip("s")
    if len(history) == 1:
        page = result_html(key, label, f"1 {one} · left the table", history[0], view)
    else:
        minutes = max(1, round((ended - started) / 60)) if ended > started else 0
        page = summary_html(key, label, unit, history, minutes)
    return await _shoot(page)


async def big_win_png(key: str, label: str, rnd: dict, view: dict | None) -> bytes | None:
    return await _shoot(result_html(key, label, "A big win", rnd, view, big=True))
