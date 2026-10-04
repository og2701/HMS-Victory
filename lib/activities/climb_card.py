"""The picture on a Climb HMS Victory post: the game's sticker look (Union Jack bunting over a
sunset, black and chrome-yellow stickers with heavy edges) with the climber's avatar and name,
their height in huge type, their place today and what they've earned, and today's top three.

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


def card_html(name: str, avatar: bytes | None, height: int, rank: int, players: int, paid: int, cap: int,
              top: list[tuple[str, int, bool]]) -> str:
    """top: today's leaders as (name, height, is_this_player)."""
    esc = html.escape
    face = (f'<img class="avatar" src="data:image/png;base64,{base64.b64encode(avatar).decode()}">' if avatar
            else f'<div class="avatar blank">{esc((name or "?")[:1].upper())}</div>')
    rows = "".join(
        f'<div class="row{" me" if me else ""} r{i + 1}"><i>{i + 1}</i><span>{esc(n)}</span><b>{h:,} m</b></div>'
        for i, (n, h, me) in enumerate(top[:3]))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face {{ font-family: 'ArchivoV'; src: url('file://{FONT}') format('truetype'); font-weight: 100 900; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ background: #111; font-family: 'ArchivoV', system-ui, sans-serif; color: #111; -webkit-font-smoothing: antialiased; }}
.card {{ position: relative; width: {W}px; overflow: hidden; padding: 0 44px 44px;
        background: linear-gradient(180deg, #b85a78 0%, #e9805e 48%, #ffcf96 100%); }}
.mast {{ position: absolute; left: 684px; top: 0; bottom: 0; width: 26px; background: rgba(70, 40, 24, .3); }}
.sailor {{ position: absolute; right: 22px; top: 150px; width: 170px; height: 184px; }}
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
.height span {{ font-size: 64px; font-weight: 900; }}
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
</style></head><body>
<div class="card"><div class="mast"></div>{SAILOR}
{_bunting()}
<div class="word"><i>CLIMB</i><b>HMS VICTORY</b></div>
<div class="who">{face}<div class="name">{esc(name)}</div></div>
<div class="height"><b>{height:,}</b><span>M</span></div>
<div class="badges">
  <div class="badge rank">{TROPHY}<b>{_ordinal(rank)}</b><small>of {players}</small></div>
  {f'<div class="badge pay">{COIN}<b>+{paid:,}</b><small>UKP today</small></div>' if paid else ''}
</div>
{f'<div class="top"><h4>TODAY\'S TOP CLIMBERS</h4>{rows}</div>' if rows else ''}
</div></body></html>"""


async def card_png(**kw) -> bytes | None:
    try:
        from lib.core.image_processing import screenshot_html
        buf = await screenshot_html(card_html(**kw), size=(W, 1600), apply_trim=False, element_selector=".card")
        return buf.getvalue()
    except Exception:
        log.warning("couldn't render the climb card", exc_info=True)
        return None
