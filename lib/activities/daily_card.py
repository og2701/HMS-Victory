"""The picture on a daily score game's post (Climb HMS Victory, Spitfire): sticker-style white
boxes with heavy black edges for the player's score and today's top three, over the game's own
sky, art, wordmark and colours (STYLES), with the player's avatar and name, their place today
and what they've earned.

Rendered as HTML by the same headless Chrome as the casino cards; portrait with big type, so it
stays readable when Discord shrinks it on a phone.
"""

from __future__ import annotations

import base64
import html
import logging
import math
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
FONT = ROOT / "data" / "fonts" / "Archivo.ttf"
W = 800

COIN = ('<svg class="coin" viewBox="0 0 26 26"><circle cx="13" cy="13" r="12" fill="#E2B33C" stroke="#111" stroke-width="2"/>'
        '<circle cx="13" cy="13" r="7.5" fill="none" stroke="#A9801F" stroke-width="2.2"/>'
        '<path d="M8 8.5A7 7 0 0 1 13 6" stroke="#F7DE8A" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg>')
TROPHY = ('<svg class="trophy" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" '
          'stroke-linejoin="round"><path d="M8 4h8v5a4 4 0 0 1-8 0z"/><path d="M8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4'
          'M12 13v4M8 20h8M10 17h4"/></svg>')


# the sailor from the game and the home tile, halfway up the mast
SAILOR = """<svg class="sailor" viewBox="0 0 120 130">
  <rect x="20" y="96" width="76" height="9" rx="4.5" fill="#8a5530"/><rect x="28" y="96" width="3" height="9" fill="#d8bb84"/><rect x="85" y="96" width="3" height="9" fill="#d8bb84"/>
  <g transform="translate(60 60)"><rect x="-9" y="-3" width="18" height="19" rx="5" fill="#f4f2ec"/>
  <rect x="-9" y="1" width="18" height="2.6" fill="#24356b"/><rect x="-9" y="6" width="18" height="2.6" fill="#24356b"/><rect x="-9" y="11" width="18" height="2.6" fill="#24356b"/>
  <circle cy="-12" r="9" fill="#f1c9a5"/><ellipse cy="-20" rx="10" ry="3.6" fill="#fff"/><rect x="-8" y="-21.5" width="16" height="2.4" fill="#1a1a2a"/>
  <rect x="2" y="-14" width="2" height="2.4" fill="#1a1a1a"/>
  <path d="M-8 0 L-15 -14 M8 0 L15 -14" stroke="#f4f2ec" stroke-width="4.5" stroke-linecap="round"/>
  <rect x="-8" y="16" width="7" height="15" rx="2" fill="#1d2b52"/><rect x="1" y="16" width="7" height="12" rx="2" fill="#1d2b52"/></g></svg>"""


def _bunting() -> str:
    flags = []
    for x in range(24, 390, 43):
        t = (x + 10) / 410
        y = (1 - t) ** 2 * 10 + 2 * (1 - t) * t * 42 + t * t * 10
        tilt = math.degrees(math.atan((2 * (1 - t) * 32 - 2 * t * 32) / 410))
        flags.append(
            f'<g transform="translate({x} {y:.1f}) rotate({tilt:.1f}) translate(-14 0)">'
            '<rect width="28" height="16" fill="#012169" stroke="#111" stroke-width=".8"/>'
            '<path d="M0 0L28 16M28 0L0 16" stroke="#fff" stroke-width="3.2"/>'
            '<path d="M0 0L28 16M28 0L0 16" stroke="#C8102E" stroke-width="1.1"/>'
            '<rect x="11.5" width="5" height="16" fill="#fff"/><rect y="5.5" width="28" height="5" fill="#fff"/>'
            '<rect x="12.5" width="3" height="16" fill="#C8102E"/><rect y="6.5" width="28" height="3" fill="#C8102E"/></g>')
    return ('<svg class="bunting" viewBox="0 -8 390 68"><path d="M-10 10Q195 42 400 10" stroke="#111" stroke-width="1.5" '
            f'fill="none"/>{"".join(flags)}</svg>')


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# The Spitfire, side on and flying right as in the game: camouflage, roundel, fin flash, the
# Sky band round the tail, propeller a blur.
SPITFIRE = """<svg class="plane" viewBox="0 0 200 80">
  <ellipse cx="190" cy="40" rx="4" ry="30" fill="rgba(235,238,242,.45)"/>
  <ellipse cx="22" cy="42" rx="22" ry="5" fill="#4a5a2c" stroke="#111" stroke-width="1.6"/>
  <path d="M182 34 C160 20 120 18 80 20 C50 22 25 28 8 34 L8 44 C35 52 80 58 120 57 C150 56 170 50 182 46 Z" fill="#5c6b38" stroke="#111" stroke-width="2"/>
  <path d="M150 26 C140 22 128 22 118 26 C126 32 140 32 150 30 Z M70 24 C60 22 48 24 40 28 C50 32 62 31 70 28 Z M100 50 C90 48 76 48 66 51 C76 55 90 55 100 53 Z" fill="#7c5c36"/>
  <rect x="22" y="22" width="9" height="30" fill="#c3dccb"/>
  <path d="M30 32 C26 12 16 4 6 6 L2 36 Z" fill="#5c6b38" stroke="#111" stroke-width="2"/>
  <rect x="14" y="14" width="5" height="18" fill="#C8102E"/><rect x="9" y="12" width="5" height="20" fill="#fff"/><rect x="4" y="10" width="5" height="22" fill="#1F3A8A"/>
  <path d="M100 21 C104 6 126 4 136 20 Z" fill="#9fc6dc" stroke="#111" stroke-width="1.8"/>
  <ellipse cx="112" cy="52" rx="50" ry="6" fill="#4a5a2c" stroke="#111" stroke-width="1.6"/>
  <circle cx="64" cy="38" r="12" fill="#F2C53D"/><circle cx="64" cy="38" r="10" fill="#1F3A8A"/><circle cx="64" cy="38" r="6.4" fill="#fff"/><circle cx="64" cy="38" r="3.4" fill="#C8102E"/>
  <path d="M182 33 C192 36 194 39 194 40 C194 41 192 44 182 47 Z" fill="#C8102E" stroke="#111" stroke-width="1.6"/>
</svg>"""

# A barrage balloon on its cable.
BALLOON = """<svg class="balloon" viewBox="0 0 80 120">
  <path d="M40 46 L40 120" stroke="#333" stroke-width="1.5"/>
  <ellipse cx="40" cy="26" rx="30" ry="16" fill="#c9cdd3" stroke="#111" stroke-width="1.5"/>
  <path d="M64 22 L76 12 L74 26 Z M64 30 L76 40 L74 26 Z M62 26 L78 26" fill="#aab0b8" stroke="#111" stroke-width="1.2"/>
  <ellipse cx="30" cy="20" rx="10" ry="4" fill="#eceef1"/>
</svg>"""


ROUNDEL = ('<svg class="roundel" viewBox="0 0 100 100"><circle cx="50" cy="50" r="48" fill="#F2C53D" stroke="#111" stroke-width="3"/>'
           '<circle cx="50" cy="50" r="40" fill="#1F3A8A"/><circle cx="50" cy="50" r="26" fill="#fff"/>'
           '<circle cx="50" cy="50" r="13" fill="#C8102E"/></svg>')
SMALL_BALLOON = ('<svg class="unit" viewBox="0 0 30 16"><path d="M20 6 L27 1 L26 8 L27 15 L20 10 Z" fill="#8f97a3" stroke="#111" '
                 'stroke-width="1.4" stroke-linejoin="round"/><ellipse cx="12" cy="8" rx="11" ry="6" fill="#c9ced6" stroke="#111" '
                 'stroke-width="1.6"/><ellipse cx="9" cy="5.5" rx="5" ry="1.6" fill="#fff"/></svg>')


def _contrails() -> str:
    """Contrails curling across the top, and the roundel, where Climb has its bunting."""
    return ('<div class="head"><svg class="trails" viewBox="0 0 800 160" preserveAspectRatio="none">'
            '<path d="M-20 130 C140 30 300 170 460 70 S680 10 820 50" stroke="rgba(255,255,255,.75)" stroke-width="9" fill="none" stroke-linecap="round"/>'
            '<path d="M-20 148 C160 70 320 190 480 96 S700 46 820 84" stroke="rgba(255,255,255,.45)" stroke-width="5" fill="none" stroke-linecap="round"/>'
            f'</svg>{ROUNDEL}</div>')


# Spitfire's own colours: navy, roundel red and blue, the pale Sky of the tail band.
SPITFIRE_CSS = """
.head { position: relative; height: 123px; margin: 0 -44px; }
.trails { position: absolute; left: 0; top: 10px; width: 800px; height: 160px; }
.roundel { position: absolute; right: 40px; top: 34px; width: 150px; height: 150px; transform: rotate(-8deg); filter: drop-shadow(7px 7px 0 #111); }
.word i { background: #C8102E; color: #fff; border: 4px solid #111; box-shadow: 5px 5px 0 #111; }
.word b { background: #1F3A8A; color: #fff; border: 5px solid #111; font-size: 76px; letter-spacing: .01em; }
.name { background: #14214d; box-shadow: 6px 6px 0 #C8102E; }
.avatar { background: #CFE6D8; }
.badge.rank { background: #1F3A8A; color: #fff; }
.badge.pay { background: #CFE6D8; }
.row b { display: flex; align-items: center; gap: 10px; }
.unit { width: 44px; height: 24px; }
.height span { letter-spacing: .04em; }
"""


def _cliffs() -> str:
    return ('<svg class="cliffs" viewBox="0 0 800 220" preserveAspectRatio="none"><path d="M0 70 L120 60 L210 80 L300 66 '
            'L360 90 L360 220 L0 220 Z" fill="#f1ede2"/><path d="M0 70 L120 60 L210 80 L300 66 L360 90" fill="none" '
            'stroke="#cfc6b4" stroke-width="4"/><path d="M0 150 Q400 130 800 150 L800 220 L0 220 Z" fill="#2f5d7a"/>'
            '<path d="M40 170 Q120 162 200 170 M300 182 Q400 174 500 182 M560 166 Q660 158 760 166" stroke="rgba(255,255,255,.5)" '
            'stroke-width="4" fill="none"/></svg>')


STYLES = {
    "climb": {
        "sky": "linear-gradient(180deg, #b85a78 0%, #e9805e 48%, #ffcf96 100%)",
        "small": "CLIMB", "big": "HMS VICTORY", "unit": "M", "unit_row": "m",
        "art": lambda: f'<div class="mast"></div>{SAILOR}', "top": "TODAY'S TOP CLIMBERS",
        "head": _bunting, "css": "",
    },
    "spitfire": {
        "sky": "linear-gradient(180deg, #3a5a96 0%, #6f5f92 34%, #c9767a 64%, #f0a774 84%, #f6c98f 100%)",
        "small": "FLY THE", "big": "SPITFIRE", "unit": "BALLOONS", "unit_row": SMALL_BALLOON,
        "art": lambda: f'{_cliffs()}<div class="b1">{BALLOON}</div><div class="b2">{BALLOON}</div>{SPITFIRE}',
        "top": "TODAY'S TOP PILOTS", "head": _contrails, "css": SPITFIRE_CSS,
    },
}


def card_html(game: str, name: str, avatar: bytes | None, score: int, rank: int, players: int, paid: int,
              top: list[tuple[str, int, bool]]) -> str:
    """top: today's leaders as (name, score, is_this_player)."""
    st = STYLES[game]
    esc = html.escape
    face = (f'<img class="avatar" src="data:image/png;base64,{base64.b64encode(avatar).decode()}">' if avatar
            else f'<div class="avatar blank">{esc((name or "?")[:1].upper())}</div>')
    rows = "".join(
        f'<div class="row{" me" if me else ""} r{i + 1}"><i>{i + 1}</i><span>{esc(n)}</span><b>{h:,}{(" " + st["unit_row"]) if st["unit_row"] else ""}</b></div>'
        for i, (n, h, me) in enumerate(top[:3]))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face {{ font-family: 'ArchivoV'; src: url('file://{FONT}') format('truetype'); font-weight: 100 900; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ background: #111; font-family: 'ArchivoV', system-ui, sans-serif; color: #111; -webkit-font-smoothing: antialiased; }}
.card {{ position: relative; width: {W}px; overflow: hidden; padding: 0 44px 44px;
        background: {st["sky"]}; }}
.mast {{ position: absolute; left: 684px; top: 0; bottom: 0; width: 26px; background: rgba(70, 40, 24, .3); }}
.sailor {{ position: absolute; right: 22px; top: 150px; width: 170px; height: 184px; }}
.plane {{ position: absolute; right: 26px; top: 212px; width: 300px; height: 120px; transform: rotate(-8deg); }}
.balloon {{ width: 100%; height: 100%; }}
.b1 {{ position: absolute; right: 44px; top: 330px; width: 84px; height: 126px; opacity: .9; }}
.b2 {{ position: absolute; right: 300px; top: 318px; width: 60px; height: 90px; opacity: .7; }}
.cliffs {{ position: absolute; left: 0; right: 0; bottom: 0; width: 100%; height: 220px; }}
.bunting {{ position: relative; display: block; width: {W}px; height: 123px; margin: 0 -44px; }}
.word {{ position: relative; display: flex; flex-direction: column; align-items: flex-start; gap: 8px; margin-top: 4px; transform: rotate(-3deg); transform-origin: left; }}
.word i {{ font-style: normal; padding: 4px 16px 2px; background: #111; color: #FFC93C; font-size: 34px; line-height: 42px; font-weight: 900; letter-spacing: .02em; }}
.word b {{ padding: 2px 20px 0; background: #fff; font-size: 64px; line-height: 78px; font-weight: 900; letter-spacing: -.02em; box-shadow: 9px 9px 0 #111; }}
.who {{ position: relative; display: flex; align-items: center; gap: 20px; margin-top: 46px; }}
.avatar {{ width: 112px; height: 112px; border-radius: 50%; border: 5px solid #111; box-shadow: 6px 6px 0 #111; object-fit: cover; background: #FFC93C; }}
.avatar.blank {{ display: flex; align-items: center; justify-content: center; font-size: 56px; font-weight: 900; }}
.name {{ padding: 6px 18px 4px; background: #111; color: #fff; font-size: 44px; line-height: 54px; font-weight: 900; max-width: 540px;
         overflow: hidden; text-overflow: ellipsis; white-space: nowrap; transform: rotate(-1.5deg); }}
.height {{ position: relative; display: flex; align-items: baseline; justify-content: center; gap: 12px; margin-top: 30px; padding: 6px 0 0;
           background: #fff; border: 6px solid #111; box-shadow: 11px 11px 0 #111; transform: rotate(1.2deg); }}
.height b {{ font-size: 210px; line-height: 220px; font-weight: 900; letter-spacing: -.05em; }}
.height span {{ font-size: {"64px" if len(st["unit"]) < 3 else "40px"}; font-weight: 900; }}
.badges {{ position: relative; display: flex; gap: 22px; margin-top: 30px; }}
.badge {{ display: flex; align-items: center; gap: 14px; padding: 12px 22px 10px; border: 5px solid #111; box-shadow: 7px 7px 0 #111; font-weight: 900; }}
.badge.rank {{ background: #111; color: #FFC93C; transform: rotate(-2.5deg); }}
.badge.pay {{ background: #FFC93C; transform: rotate(2deg); }}
.badge b {{ font-size: 52px; line-height: 58px; letter-spacing: -.02em; }}
.badge small {{ font-size: 26px; font-weight: 800; opacity: .8; }}
.trophy {{ width: 46px; height: 46px; }}
.coin {{ width: 50px; height: 50px; }}
.top {{ position: relative; margin-top: 34px; padding: 18px 20px 12px; background: #fff; border: 5px solid #111; box-shadow: 9px 9px 0 #111; }}
.top h4 {{ font-size: 24px; font-weight: 900; letter-spacing: .14em; color: #E0352B; margin-bottom: 10px; }}
.row {{ display: flex; align-items: center; gap: 18px; padding: 8px 0; border-top: 3px solid #eee; }}
.row:first-of-type {{ border-top: 0; }}
.row i {{ width: 50px; height: 50px; display: flex; align-items: center; justify-content: center; font-style: normal; font-size: 28px; font-weight: 900; background: #eee; }}
.row.r1 i {{ background: #FFC93C; }} .row.r2 i {{ background: #cfd4da; }} .row.r3 i {{ background: #d9925a; }}
.row span {{ flex: 1; font-size: 34px; font-weight: 800; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.row b {{ font-size: 36px; font-weight: 900; }}
.row.me span {{ text-decoration: underline; text-decoration-thickness: 4px; text-decoration-color: #FFC93C; }}
{st["css"]}
</style></head><body>
<div class="card">{st["art"]()}
{st["head"]()}
<div class="word"><i>{st["small"]}</i><b>{st["big"]}</b></div>
<div class="who">{face}<div class="name">{esc(name)}</div></div>
<div class="height"><b>{score:,}</b><span>{st["unit"]}</span></div>
<div class="badges">
  <div class="badge rank">{TROPHY}<b>{_ordinal(rank)}</b><small>of {players}</small></div>
  {f'<div class="badge pay">{COIN}<b>+{paid:,}</b><small>UKP today</small></div>' if paid else ''}
</div>
{f'<div class="top"><h4>{st["top"]}</h4>{rows}</div>' if rows else ''}
</div></body></html>"""


async def card_png(game: str, **kw) -> bytes | None:
    try:
        from lib.core.image_processing import screenshot_html
        buf = await screenshot_html(card_html(game, **kw), size=(W, 1600), apply_trim=False, element_selector=".card")
        return buf.getvalue()
    except Exception:
        log.warning("couldn't render the %s card", game, exc_info=True)
        return None
