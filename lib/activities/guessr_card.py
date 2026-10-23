"""The picture on a UKGuessr post, once someone's pinned all three of the day's spots: the game's own look (the
night-blue sky, the illustrated map and magnifier, the chunky UK GUESSR wordmark) round sticker-style white boxes
with the day's total, each round's score and how far off the pin was, their place today, the reward, and today's
top three. It never shows where the spots were, so it doesn't give the day away to anyone yet to play.

Rendered as HTML by the same headless Chrome as the other activity cards; portrait with big type, so it stays
readable when Discord shrinks it on a phone.
"""

from __future__ import annotations

import base64
import html
import logging

from lib.activities.daily_card import COIN, FONT, ROOT, TROPHY, W, _ordinal

log = logging.getLogger(__name__)

WORD_FONT = ROOT / "data" / "fonts" / "LilitaOne.woff2"
HERO_ART = ROOT / "data" / "games" / "guessr-hero.svg"
PIN = ('<svg class="pin" viewBox="-15 -40 30 41"><path d="M0 0C-4 -9 -13 -15 -13 -24A13 13 0 1 1 13 -24C13 -15 4 -9 0 0Z" '
       'fill="#E0352B" stroke="#111" stroke-width="3"/><circle cy="-24" r="5" fill="#fff"/></svg>')


def _km(km: float | None) -> str:
    if km is None:
        return "TIME UP"
    return "SPOT ON" if km < 0.05 else f"{km:,.1f} KM" if km < 10 else f"{round(km):,} KM"


def card_html(name: str, avatar: bytes | None, date_label: str, total: int, most: int,
              rounds: list[tuple[int, float]], rank: int, players: int, paid: int,
              top: list[tuple[str, int, bool]]) -> str:
    """rounds: each round's (score, km off); top: today's leaders as (name, score, is_this_player)."""
    esc = html.escape
    face = (f'<img class="avatar" src="data:image/png;base64,{base64.b64encode(avatar).decode()}">' if avatar
            else f'<div class="avatar blank">{esc((name or "?")[:1].upper())}</div>')
    chips = "".join(
        f'<div class="round t{i % 3}"><small>ROUND {i + 1}</small><b>{score:,}</b>'
        f'<div class="meter"><i style="width:{max(4, min(100, score / 50)):.0f}%"></i></div><span>{PIN}{_km(km)}</span></div>'
        for i, (score, km) in enumerate(rounds))
    rows = "".join(
        f'<div class="row{" me" if me else ""} r{i + 1}"><i>{i + 1}</i><span>{esc(n)}</span><b>{s:,}</b></div>'
        for i, (n, s, me) in enumerate(top[:3]))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face {{ font-family: 'ArchivoV'; src: url('file://{FONT}') format('truetype'); font-weight: 100 900; }}
@font-face {{ font-family: 'Lilita'; src: url('file://{WORD_FONT}') format('woff2'); }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ background: #0d1530; font-family: 'ArchivoV', system-ui, sans-serif; color: #111; -webkit-font-smoothing: antialiased; }}
.card {{ position: relative; width: {W}px; overflow: hidden; padding: 0 44px 44px;
        background: radial-gradient(circle at 78% 16%, #33589c 0, #1c2f5e 34%, #111b3c 70%, #0b1128 100%); }}
.dots {{ position: absolute; inset: 0; background-image: radial-gradient(rgba(255,255,255,.07) 2px, transparent 2.5px); background-size: 34px 34px; }}
.hero {{ position: absolute; right: 10px; top: 26px; width: 318px; transform: rotate(5deg); filter: drop-shadow(10px 12px 0 rgba(0,0,0,.35)); }}
.date {{ position: relative; display: inline-block; margin-top: 48px; padding: 6px 16px 4px; background: #FFC93C; border: 4px solid #111;
        box-shadow: 6px 6px 0 #111; font-size: 26px; line-height: 32px; font-weight: 900; letter-spacing: .08em; transform: rotate(-2deg); }}
.word {{ position: relative; margin-top: 26px; font-family: 'Lilita', 'ArchivoV', sans-serif; line-height: .9; transform: rotate(-4deg); transform-origin: left; }}
.word span {{ display: block; font-size: 84px; color: #fff; -webkit-text-stroke: 10px #111; paint-order: stroke fill; }}
.word b {{ display: block; font-weight: 400; font-size: 136px; letter-spacing: .01em; color: #FFC93C; -webkit-text-stroke: 12px #111;
          paint-order: stroke fill; text-shadow: 9px 9px 0 #E0352B; }}
.who {{ position: relative; display: flex; align-items: center; gap: 20px; margin-top: 44px; }}
.avatar {{ width: 112px; height: 112px; border-radius: 50%; border: 5px solid #111; box-shadow: 6px 6px 0 #111; object-fit: cover; background: #FFC93C; }}
.avatar.blank {{ display: flex; align-items: center; justify-content: center; font-size: 56px; font-weight: 900; }}
.name {{ padding: 6px 18px 4px; background: #111; color: #fff; font-size: 44px; line-height: 54px; font-weight: 900; max-width: 540px;
         overflow: hidden; text-overflow: ellipsis; white-space: nowrap; transform: rotate(-1.5deg); border: 3px solid #fff; }}
.total {{ position: relative; display: flex; align-items: baseline; justify-content: center; gap: 14px; margin-top: 30px; padding: 4px 0 0;
          background: #fff; border: 6px solid #111; box-shadow: 11px 11px 0 #111; transform: rotate(1.2deg); }}
.total b {{ font-family: 'Lilita', 'ArchivoV', sans-serif; font-weight: 400; font-size: 156px; line-height: 190px; letter-spacing: .01em; }}
.total span {{ font-size: 40px; font-weight: 900; color: #777; white-space: nowrap; }}
.rounds {{ position: relative; display: flex; gap: 18px; margin-top: 34px; }}
.round {{ flex: 1; display: flex; flex-direction: column; align-items: flex-start; gap: 6px; padding: 14px 16px 14px; background: #fff;
          border: 5px solid #111; box-shadow: 7px 7px 0 #111; }}
.round.t0 {{ transform: rotate(-2deg); }} .round.t1 {{ transform: rotate(1deg) translateY(6px); }} .round.t2 {{ transform: rotate(-1deg); }}
.round small {{ font-size: 20px; font-weight: 900; letter-spacing: .12em; color: #E0352B; }}
.round b {{ font-family: 'Lilita', 'ArchivoV', sans-serif; font-weight: 400; font-size: 60px; line-height: 64px; }}
.meter {{ width: 100%; height: 16px; border: 3px solid #111; background: #eee; }}
.meter i {{ display: block; height: 100%; background: #FFC93C; border-right: 3px solid #111; }}
.round span {{ display: flex; align-items: center; gap: 6px; margin-top: 4px; font-size: 24px; font-weight: 900; white-space: nowrap; }}
.pin {{ width: 20px; height: 27px; flex: none; }}
.badges {{ position: relative; display: flex; gap: 22px; margin-top: 34px; }}
.badge {{ display: flex; align-items: center; gap: 14px; padding: 12px 22px 10px; border: 5px solid #111; box-shadow: 7px 7px 0 #111; font-weight: 900; }}
.badge.rank {{ background: #111; color: #FFC93C; border-color: #fff; transform: rotate(-2.5deg); }}
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
<div class="card"><div class="dots"></div><img class="hero" src="file://{HERO_ART}">
<div class="date">{esc(date_label.upper())}</div>
<div class="word"><span>UK</span><b>GUESSR</b></div>
<div class="who">{face}<div class="name">{esc(name)}</div></div>
<div class="total"><b>{total:,}</b><span>/ {most:,}</span></div>
<div class="rounds">{chips}</div>
<div class="badges">
  <div class="badge rank">{TROPHY}<b>{_ordinal(rank)}</b><small>of {players}</small></div>
  {f'<div class="badge pay">{COIN}<b>+{paid:,}</b><small>UKP</small></div>' if paid else ''}
</div>
{f'<div class="top"><h4>TODAY\'S BEST GUESSERS</h4>{rows}</div>' if rows else ''}
</div></body></html>"""


async def card_png(**kw) -> bytes | None:
    try:
        from lib.core.image_processing import screenshot_html
        buf = await screenshot_html(card_html(**kw), size=(W, 1700), apply_trim=False, element_selector=".card")
        return buf.getvalue()
    except Exception:
        log.warning("couldn't render the UKGuessr card", exc_info=True)
        return None
