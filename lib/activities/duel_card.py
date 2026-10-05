"""The pictures on Broadside's #casino post, in the duel's sticker look: the challenge going up,
the match as it's played (the score and every round's two cards), and the result.

Drawn as HTML and photographed like the casino summaries, from a snapshot of the challenge or
match (see duel_posts), with names already looked up.
"""

from __future__ import annotations

import base64
import html
import logging
import math
import time
from pathlib import Path

from lib.activities import duel

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "data" / "broadside"
COIN = ROOT / "data" / "ukpence-small.svg"
FONTS = ROOT / "data" / "fonts"
COLOUR = {"broadside": "#C21E3A", "grapeshot": "#3A5A8A", "chainshot": "#14858C", "board": "#B06A1C",
          "ram": "#2A62C0", "fireship": "#D9482A", "evade": "#7A4AD0"}

_HEAD = ("<!doctype html><html><head><meta charset='utf-8'><style>"
         "@import url('https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..900&display=block');"
         f"@font-face{{font-family:'ArchivoLocal';src:url('file://{FONTS / 'Archivo.ttf'}') format('truetype');font-weight:100 900}}")
_CSS = """*{margin:0;box-sizing:border-box} html,body{background:transparent} body{width:820px;font-family:Archivo,ArchivoLocal,sans-serif}
.card{width:820px;padding:30px 32px 32px;border-radius:28px;position:relative;overflow:hidden;color:#fff;
 background:radial-gradient(80% 60% at 72% 18%,#22386A 0%,#0D1730 58%,#060B18 100%)}
.wheel{position:absolute;transform:rotate(-10deg)}
.ch .wheel{right:-92px;top:44px;width:280px;height:280px}
.mt .wheel{right:-56px;top:-56px;width:236px;height:236px;opacity:.55}
.lab{position:relative;display:inline-flex;flex-direction:column;align-items:flex-start;gap:6px;transform:rotate(-2deg);transform-origin:0 0}
.lab i{font-style:normal;padding:4px 11px;background:#111;color:#FFC93C;font-weight:900;font-size:17px;letter-spacing:.14em}
.lab b{padding:4px 16px 0;background:#fff;color:#111;box-shadow:6px 6px 0 #111;font-weight:900;font-size:58px;line-height:66px}
.tape{display:inline-block;background:#fff;color:#111;font-weight:900;white-space:nowrap;box-shadow:4px 4px 0 #111}
.face{position:relative;display:flex;align-items:center;gap:22px;margin:40px 0 34px}
.cap{display:flex;align-items:center;gap:14px;min-width:0}
.av{width:74px;height:74px;flex:none;border-radius:50%;background:linear-gradient(135deg,#2A4FB0,#16265C);box-shadow:0 0 0 4px #fff;
 display:flex;align-items:center;justify-content:center;font-weight:900;font-size:34px}
.av{position:relative;overflow:hidden}
.av .pic{position:absolute;inset:0;width:100%;height:100%;border-radius:50%;object-fit:cover}
.av.q{background:#22304F;color:#FFC93C}
.name{padding:3px 12px 0;font-size:27px;line-height:36px;transform:rotate(-1.5deg);max-width:270px;overflow:hidden;text-overflow:ellipsis}
.ch .face{max-width:640px;gap:16px}
.ch .av{width:62px;height:62px;font-size:28px}
.ch .cap{gap:12px}
em.v{font-style:normal;font-weight:900;font-size:30px;color:rgba(255,255,255,.55)}
.chips{position:relative;display:flex;flex-wrap:wrap;gap:12px}
.chip{display:flex;align-items:center;gap:8px;padding:7px 14px;background:#111;color:#fff;font-weight:900;font-size:20px;letter-spacing:.04em;
 box-shadow:inset 0 0 0 2px rgba(255,255,255,.18)}
.chip.light{background:#fff;color:#111;box-shadow:inset 0 0 0 3px #111}
.chip img{width:26px;height:26px}
.stamp{position:absolute;left:50%;top:52%;transform:translate(-50%,-50%) rotate(-10deg);padding:6px 22px;border-radius:8px;
 background:rgba(251,240,210,.95);box-shadow:inset 0 0 0 5px #B3122A;color:#B3122A;font-weight:900;font-size:64px;letter-spacing:.1em}
.dim .face,.dim .chips,.dim .wheel{opacity:.45}
.score{position:relative;display:flex;align-items:center;gap:26px;margin:34px 0 26px}
.side{display:flex;align-items:center;gap:16px}
.side b{font-weight:900;font-size:84px;line-height:84px;font-variant-numeric:tabular-nums}
.side.lead b{color:#FFC93C}
.rounds{position:relative;display:grid;grid-template-columns:150px repeat(var(--n),76px);gap:10px 10px;align-items:center;
 padding:18px 20px;border-radius:22px;background:rgba(255,255,255,.06);box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.12)}
.rounds .who{font-weight:900;font-size:18px;letter-spacing:.06em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#FFC93C}
.rounds .rn{text-align:center;font-weight:900;font-size:14px;letter-spacing:.12em;color:rgba(255,255,255,.55)}
.mv{position:relative;width:76px;height:76px;border-radius:16px;box-shadow:0 6px 12px rgba(0,0,0,.4)}
.mv img{width:100%;height:100%;display:block}
.mv.win{box-shadow:0 0 0 4px #FFC93C,0 0 22px rgba(255,201,60,.55)}
.mv.loss{filter:saturate(.35) brightness(.6)}
.mv .t{position:absolute;left:50%;bottom:-9px;transform:translateX(-50%);padding:1px 5px;background:#111;color:#FFC93C;font-weight:900;font-size:10px;letter-spacing:.08em;white-space:nowrap}
.why{position:relative;display:flex;flex-direction:column;gap:8px;margin-top:26px;padding:18px 22px;background:#fff;color:#111;box-shadow:7px 7px 0 #111;transform:rotate(-1deg)}
.why i{font-style:normal;font-weight:900;font-size:15px;letter-spacing:.16em;color:#127A3E}
.why i.loss{color:#C62828} .why i.draw{color:#555}
.why b{font-weight:900;font-size:28px;line-height:34px}
.foot{margin-top:28px}
.winner{background:#FFC93C}
"""


def _f(path: Path) -> str:
    return f"file://{path}"


def _e(text: str) -> str:
    return html.escape(text or "", quote=True)


def _page(body: str) -> str:
    return f"{_HEAD}{_CSS}</style></head><body>{body}</body></html>"


def wheel_svg() -> str:
    """The battle wheel, every order in its own colour, with the clockwise arrow."""
    c, ro, ri, n = 120, 100, 46, len(duel.MOVES)
    span = 360 / n
    at = lambda r, deg: (c + r * math.cos(math.radians(deg)), c + r * math.sin(math.radians(deg)))
    parts, grads = [], []
    for i, m in enumerate(duel.MOVES):
        mid = -90 + i * span
        (x0, y0), (x1, y1) = at(ro, mid - span / 2 + 1.6), at(ro, mid + span / 2 - 1.6)
        (x2, y2), (x3, y3) = at(ri, mid + span / 2 - 1.6), at(ri, mid - span / 2 + 1.6)
        parts.append(f'<path d="M{x0:.1f} {y0:.1f}A{ro} {ro} 0 0 1 {x1:.1f} {y1:.1f}L{x2:.1f} {y2:.1f}A{ri} {ri} 0 0 0 {x3:.1f} {y3:.1f}Z" '
                     f'fill="url(#g-{m})" stroke="#0B1020" stroke-width="2.5" stroke-linejoin="round"/>')
        ix, iy = at((ro + ri) / 2, mid)
        parts.append(f'<image href="{_f(ART / f"{m}.webp")}" x="{ix - 23:.1f}" y="{iy - 23:.1f}" width="46" height="46"/>')
        grads.append(f'<radialGradient id="g-{m}" cx=".5" cy=".5" r=".7"><stop offset="0" stop-color="{COLOUR[m]}"/>'
                     f'<stop offset="1" stop-color="#0B1020"/></radialGradient>')
    return (f'<svg class="wheel" viewBox="0 0 240 240"><defs>{"".join(grads)}</defs>'
            '<path d="M120 8A112 112 0 1 1 23 64" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" opacity=".9"/>'
            '<path d="M30.9 71.4L28 52L13.1 63.4" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" opacity=".9"/>'
            + "".join(parts)
            + f'<circle cx="{c}" cy="{c}" r="{ri - 6}" fill="#111" stroke="#fff" stroke-width="3"/>'
            f'<text x="{c}" y="{c - 4}" text-anchor="middle" font-family="Archivo,ArchivoLocal" font-weight="900" font-size="13" fill="#fff">BEATS</text>'
            f'<text x="{c}" y="{c + 13}" text-anchor="middle" font-family="Archivo,ArchivoLocal" font-weight="900" font-size="13" fill="#FFC93C">NEXT 3</text></svg>')


def _coin() -> str:
    return f'<img src="{_f(COIN)}">'


def _pic(faces: dict | None, uid) -> str:
    """Someone's profile picture over their initial, if we have it."""
    data = (faces or {}).get(uid)
    return f'<img class="pic" src="data:image/webp;base64,{base64.b64encode(data).decode()}">' if data else ""


def _initial(name: str) -> str:
    return _e((name.strip()[:1] or "?").upper())


def challenge_page(s: dict, names: dict, faces: dict | None = None) -> str:
    """A challenge going up, or one that came to nothing (lapsed, withdrawn, turned down)."""
    who, to = names.get(s["from"], "Someone"), (names.get(s["to"], "Someone") if s.get("to") else None)
    stake = int(s.get("stake") or 0)
    gone = {"lapsed": "LAPSED", "withdrawn": "WITHDRAWN", "declined": "DECLINED"}.get(s["event"])
    tag = "OPEN CHALLENGE" if not to else "A CHALLENGE"
    left = max(0, int(round((s.get("expires", time.time()) - time.time()) / 60)))
    chips = ([f'<span class="chip light">{_coin()}{stake:,} EACH</span>',
              f'<span class="chip">WINNER TAKES {stake * 2 - _rake(stake * 2):,}</span>'] if stake
             else ['<span class="chip light">A FRIENDLY · NOTHING STAKED</span>'])
    chips += ['<span class="chip">FIRST TO 3</span>']
    if not gone:
        chips.append(f'<span class="chip">{left} MIN TO ACCEPT</span>' if left else '<span class="chip">ABOUT TO LAPSE</span>')
    them = (f'<div class="cap"><span class="av">{_initial(to)}{_pic(faces, s.get("to"))}</span><span class="tape name">{_e(to.upper())}</span></div>' if to
            else '<div class="cap"><span class="av q">?</span><span class="tape name">ANYONE</span></div>')
    return _page(f"""<div class="card ch{' dim' if gone else ''}">{wheel_svg()}
      <div class="lab"><i>1V1 DUEL · {tag}</i><b>BROADSIDE</b></div>
      <div class="face"><div class="cap"><span class="av">{_initial(who)}{_pic(faces, s["from"])}</span><span class="tape name">{_e(who.upper())}</span></div>
        <em class="v">v</em>{them}</div>
      <div class="chips">{''.join(chips)}</div>
      {f'<div class="stamp">{gone}</div>' if gone else ''}</div>""")


def _rake(pot: int) -> int:
    try:
        from lib.economy.economy_manager import pvp_rake
        return pvp_rake(pot)
    except Exception:
        return 0


def match_page(s: dict, names: dict) -> str:
    """The match as it stands: the score, both captains' cards for every round, and the reason
    the last one went the way it did. At the end, who won and what they took."""
    a, b = names.get(s["a"], "Someone"), names.get(s["b"], "Someone")
    rounds = s.get("rounds", [])
    wa = sum(1 for r in rounds if r["w"] == "a")
    wb = sum(1 for r in rounds if r["w"] == "b")
    over = bool(s.get("over"))
    n = len(rounds)
    if over:
        how = s.get("how")
        if s.get("winner") in ("a", "b"):
            win = a if s["winner"] == "a" else b
            lose = b if s["winner"] == "a" else a
            tag = {"score": "FINAL" if n <= 5 else "FINAL · SUDDEN DEATH",
                   "left": f"FINAL · {lose.upper()} LEFT THE FIGHT",
                   "forfeit": f"FINAL · {lose.upper()} STRUCK THEIR COLOURS"}.get(how, "FINAL")
            title = f'<b class="winner">{_e(win.upper())} WINS</b>'
        else:
            tag = "FINAL · LEVEL AFTER SEVEN" if how == "draw" else "FINAL · NOBODY GAVE AN ORDER"
            title = "<b>DRAW</b>"
    else:
        tag = f"LIVE · ROUND {n + 1}" + (" · SUDDEN DEATH" if n >= 5 else " OF 5")
        title = "<b>BROADSIDE</b>"

    def cell(r: dict, side: str) -> str:
        res = "draw" if r["w"] is None else "win" if r["w"] == side else "loss"
        m = r[side]
        bg = f"background:radial-gradient(120% 100% at 50% 40%,{COLOUR[m]} 0%,#0B1020 100%)"
        tired = '<span class="t">TIMED OUT</span>' if side in r.get("auto", []) else ""
        return f'<div class="mv {res}" style="{bg}"><img src="{_f(ART / f"{m}.webp")}">{tired}</div>'

    grid = ""
    if rounds:
        grid = (f'<div class="rounds" style="--n:{n}"><span></span>'
                + "".join(f'<span class="rn">R{i + 1}</span>' for i in range(n))
                + f'<span class="who">{_e(a.upper())}</span>' + "".join(cell(r, "a") for r in rounds)
                + f'<span class="who">{_e(b.upper())}</span>' + "".join(cell(r, "b") for r in rounds) + "</div>")
    why = ""
    if rounds:
        r = rounds[-1]
        if r["w"] is None:
            line, cls, head = "Both fire the same order, and nothing comes of it.", "draw", "NOBODY SCORES"
        else:
            wm, lm = (r["a"], r["b"]) if r["w"] == "a" else (r["b"], r["a"])
            line = duel.REASONS.get((wm, lm), "")
            head = f"{(a if r['w'] == 'a' else b).upper()} WINS IT"
            cls = ""
        why = f'<div class="why"><i class="{cls}">ROUND {n} · {_e(head)}</i><b>{_e(line)}</b></div>'
    stake = int(s.get("stake") or 0)
    if over and stake:
        if s.get("winner") in ("a", "b"):
            who = a if s["winner"] == "a" else b
            money = f'<span class="chip light">{_coin()}+{int(s.get("payout", 0)):,} TO {_e(who.upper())}</span>'
        else:
            money = '<span class="chip light">STAKES RETURNED</span>'
    elif stake:
        money = f'<span class="chip light">{_coin()}{stake * 2:,} POT</span>'
    else:
        money = '<span class="chip light">A FRIENDLY</span>'
    lead_a, lead_b = (" lead" if wa > wb else ""), (" lead" if wb > wa else "")
    return _page(f"""<div class="card mt">{wheel_svg()}
      <div class="lab"><i>{_e(tag)}</i>{title}</div>
      <div class="score"><div class="side{lead_a}"><span class="tape name">{_e(a.upper())}</span><b>{wa}</b></div><em class="v">v</em>
        <div class="side{lead_b}"><b>{wb}</b><span class="tape name">{_e(b.upper())}</span></div></div>
      {grid}{why}<div class="chips foot">{money}</div></div>""")


def page(s: dict, names: dict, faces: dict | None = None) -> str:
    return (challenge_page(s, names, faces) if s["event"] in ("challenge", "lapsed", "withdrawn", "declined")
            else match_page(s, names))


async def png(s: dict, names: dict, faces: dict | None = None) -> bytes | None:
    try:
        from lib.core.image_processing import screenshot_html
        buf = await screenshot_html(page(s, names, faces), size=(900, 1300), apply_trim=False, element_selector=".card",
                                    transparent=True)
        return buf.getvalue()
    except Exception:
        log.warning("couldn't draw the broadside card", exc_info=True)
        return None
