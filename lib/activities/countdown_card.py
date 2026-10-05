"""The pictures on Countdown's #casino post, in the activity's sticker look: the room filling up,
the game round by round (the letters, the longest word and the scores), and the result.

Drawn as HTML and photographed like Broadside's, from a snapshot of the room (see
countdown_posts), with names already looked up.
"""

from __future__ import annotations

import html
import logging
import math
from pathlib import Path

from lib.activities import countdown

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
COIN = ROOT / "data" / "ukpence-small.svg"
FONTS = ROOT / "data" / "fonts"
COLOURS = ["#2A4FB0,#16265C", "#B06A1C,#5A2C0E", "#7A4AD0,#2A0F5A", "#14858C,#06303A", "#C21E3A,#2A040C", "#4A7A2A,#1E3A10"]

_HEAD = ("<!doctype html><html><head><meta charset='utf-8'><style>"
         "@import url('https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..900&display=block');"
         f"@font-face{{font-family:'ArchivoLocal';src:url('file://{FONTS / 'Archivo.ttf'}') format('truetype');font-weight:100 900}}")
_CSS = """*{margin:0;box-sizing:border-box} html,body{background:transparent} body{width:820px;font-family:Archivo,ArchivoLocal,sans-serif}
.card{width:820px;padding:30px 32px 32px;border-radius:28px;position:relative;overflow:hidden;color:#fff;
 background:radial-gradient(80% 60% at 72% 18%,#22386A 0%,#0D1730 58%,#060B18 100%)}
.clock{position:absolute;right:-46px;top:-40px;width:232px;height:232px;transform:rotate(8deg);filter:drop-shadow(0 10px 18px rgba(0,0,0,.5))}
.lab{position:relative;display:inline-flex;flex-direction:column;align-items:flex-start;gap:6px;transform:rotate(-2deg);transform-origin:0 0}
.lab i{font-style:normal;padding:4px 11px;background:#111;color:#FFC93C;font-weight:900;font-size:17px;letter-spacing:.14em}
.lab b{padding:4px 16px 0;background:#fff;color:#111;box-shadow:6px 6px 0 #111;font-weight:900;font-size:58px;line-height:66px}
.lab b.winner{background:#FFC93C}
.tape{display:inline-block;background:#fff;color:#111;font-weight:900;white-space:nowrap;box-shadow:4px 4px 0 #111}
.av{width:64px;height:64px;flex:none;border-radius:50%;box-shadow:0 0 0 4px rgba(255,255,255,.5);display:flex;align-items:center;
 justify-content:center;font-weight:900;font-size:29px;color:#fff}
.av.q{background:none;box-shadow:inset 0 0 0 3px rgba(255,255,255,.25);color:rgba(255,255,255,.35)}
.seats{position:relative;display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin:44px 0 34px}
.seat{display:flex;flex-direction:column;align-items:center;gap:12px;min-width:0}
.seat .tape{max-width:118px;padding:3px 8px 0;font-size:17px;line-height:24px;overflow:hidden;text-overflow:ellipsis;transform:rotate(-1.5deg)}
.seat .host{font-style:normal;font-weight:900;font-size:12px;letter-spacing:.14em;color:#FFC93C;margin-top:-4px}
.chips{position:relative;display:flex;flex-wrap:wrap;gap:12px}
.chip{display:flex;align-items:center;gap:8px;padding:7px 14px;background:#111;color:#fff;font-weight:900;font-size:20px;letter-spacing:.04em;
 box-shadow:inset 0 0 0 2px rgba(255,255,255,.18)}
.chip.light{background:#fff;color:#111;box-shadow:inset 0 0 0 3px #111}
.chip img{width:26px;height:26px}
.stamp{position:absolute;left:50%;top:52%;transform:translate(-50%,-50%) rotate(-10deg);padding:6px 22px;border-radius:8px;
 background:rgba(251,240,210,.95);box-shadow:inset 0 0 0 5px #B3122A;color:#B3122A;font-weight:900;font-size:64px;letter-spacing:.1em}
.dim .seats,.dim .chips,.dim .clock{opacity:.45}
.board{position:relative;display:flex;gap:9px;margin:40px 0 0}
.tile{width:64px;height:78px;border-radius:10px;display:flex;align-items:center;justify-content:center;color:#111;font-weight:900;font-size:40px;
 background:linear-gradient(#FFFDF5,#F2E8D2);box-shadow:inset 0 0 0 3px #111,5px 5px 0 #111}
.best{position:relative;display:flex;flex-direction:column;gap:12px;margin-top:28px;padding:18px 22px 20px;background:#fff;color:#111;
 box-shadow:7px 7px 0 #111;transform:rotate(-1deg)}
.best i{font-style:normal;font-weight:900;font-size:15px;letter-spacing:.16em;color:#127A3E}
.best i.none{color:#555}
.ws{display:flex;flex-wrap:wrap;gap:10px 22px}
.w{display:flex;align-items:baseline;gap:10px}
.w b{font-weight:900;font-size:34px;line-height:38px;letter-spacing:.03em}
.w em{font-style:normal;font-weight:900;font-size:17px;letter-spacing:.06em;color:#555}
.dc{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding-top:12px;border-top:2px dashed rgba(17,17,17,.2);
 font-weight:900;font-size:13px;letter-spacing:.14em}
.dc img{width:24px;height:24px}
.dc b{padding:3px 8px 1px;background:#111;color:#fff;font-size:17px;letter-spacing:.04em}
.table{position:relative;display:grid;grid-template-columns:1fr;gap:10px;margin:30px 0 26px}
.table.two{grid-template-columns:1fr 1fr}
.row{display:flex;align-items:center;gap:14px;padding:10px 18px 10px 12px;border-radius:18px;background:rgba(255,255,255,.07);
 box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.14);min-width:0}
.row .av{width:48px;height:48px;font-size:22px;box-shadow:0 0 0 3px rgba(255,255,255,.5)}
.row span:not(.av){flex:1;min-width:0;font-weight:900;font-size:22px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row b{font-weight:900;font-size:34px;font-variant-numeric:tabular-nums}
.row.top{background:linear-gradient(#FFD56A,#F2B930);color:#111;box-shadow:inset 0 0 0 3px #111,5px 5px 0 #000}
.row.gone{opacity:.45}
.lv .table{margin-bottom:0}
.lv .chips{margin-top:26px}
"""


def _f(path: Path) -> str:
    return f"file://{path}"


def _e(text: str) -> str:
    return html.escape(text or "", quote=True)


def _page(body: str) -> str:
    return f"{_HEAD}{_CSS}</style></head><body>{body}</body></html>"


def _coin() -> str:
    return f'<img src="{_f(COIN)}">'


def _initial(name: str) -> str:
    return _e((name.strip()[:1] or "?").upper())


def clock_svg(elapsed: float = 19) -> str:
    """The show's clock, a round under way: the top half filling gold as the hand sweeps."""
    c, r = 100, 84
    deg = max(0.0, min(179.9, elapsed / 30 * 180))
    x, y = c + r * math.sin(math.radians(deg)), c - r * math.cos(math.radians(deg))
    ticks = "".join(
        f'<path d="M{c + (70 if i % 3 == 0 else 74) * math.sin(math.radians(i * 30)):.1f} {c - (70 if i % 3 == 0 else 74) * math.cos(math.radians(i * 30)):.1f}'
        f'L{c + 84 * math.sin(math.radians(i * 30)):.1f} {c - 84 * math.cos(math.radians(i * 30)):.1f}" stroke="#111" '
        f'stroke-width="{5 if i % 3 == 0 else 3}" stroke-linecap="round"/>' for i in range(12))
    return (f'<svg class="clock" viewBox="0 0 200 200"><circle cx="100" cy="100" r="98" fill="#111"/>'
            f'<circle cx="100" cy="100" r="91" fill="#fff"/><circle cx="100" cy="100" r="86" fill="#F7EEDB"/>'
            f'<path d="M100 100L100 16A84 84 0 0 1 {x:.1f} {y:.1f}Z" fill="#FFC93C"/>{ticks}'
            f'<path d="M100 100V22" stroke="#111" stroke-width="5" stroke-linecap="round" transform="rotate({deg:.1f} 100 100)"/>'
            f'<circle cx="100" cy="100" r="9" fill="#111"/></svg>')


def _av(room: dict, uid, names: dict, cls: str = "") -> str:
    i = room["players"].index(uid) if uid in room["players"] else 0
    return (f'<span class="av {cls}" style="background:linear-gradient(135deg,{COLOURS[i % len(COLOURS)]})">'
            f'{_initial(names.get(uid, "Someone"))}</span>')


def _money(room: dict) -> str:
    stake = int(room.get("stake") or 0)
    pot = stake * len(room["players"])
    if not stake:
        return '<span class="chip light">A FRIENDLY · NOTHING STAKED</span>'
    return (f'<span class="chip light">{_coin()}{stake:,} EACH</span>'
            f'<span class="chip">POT {pot:,} · WINNER TAKES {_prize(pot):,}</span>')


def _prize(pot: int) -> int:
    try:
        return countdown.prize(pot)
    except Exception:
        return pot


def room_page(room: dict, names: dict, event: str) -> str:
    """The room filling up, or one that never started (closed by its host, or lapsed)."""
    gone = {"closed": "CLOSED", "lapsed": "LAPSED"}.get(event)
    seats = []
    for i in range(countdown.SEATS):
        if i < len(room["players"]):
            uid = room["players"][i]
            host = '<i class="host">HOST</i>' if uid == room["host"] else ""
            seats.append(f'<div class="seat">{_av(room, uid, names)}<span class="tape">{_e(names.get(uid, "Someone").upper())}</span>{host}</div>')
        else:
            seats.append('<div class="seat"><span class="av q">?</span></div>')
    return _page(f"""<div class="card lb{' dim' if gone else ''}">{clock_svg()}
      <div class="lab"><i>UP TO {countdown.SEATS} PLAYERS · {'ROOM OPEN' if not gone else 'ROOM'}</i><b>COUNTDOWN</b></div>
      <div class="seats">{''.join(seats)}</div>
      <div class="chips">{_money(room)}<span class="chip">{countdown.ROUNDS} ROUNDS</span></div>
      {f'<div class="stamp">{gone}</div>' if gone else ''}</div>""")


def _table(room: dict, names: dict, top: set) -> str:
    scores = room.get("scores", {})
    ranked = sorted(room["players"], key=lambda u: (-scores.get(str(u), 0), room["players"].index(u)))
    rows = "".join(
        f'<div class="row{" top" if u in top else ""}{" gone" if u in room.get("left", []) else ""}">{_av(room, u, names)}'
        f'<span>{_e(names.get(u, "Someone"))}</span><b>{scores.get(str(u), 0)}</b></div>' for u in ranked)
    return f'<div class="table{" two" if len(ranked) > 3 else ""}">{rows}</div>'


def _last_round(room: dict, names: dict) -> str:
    """The latest round's letters, its longest word(s), and what Dictionary Corner found."""
    done = [(i, r) for i, r in enumerate(room.get("rounds", []), 1) if r.get("results")]
    if not done:
        return ""
    n, rd = done[-1]
    tiles = "".join(f'<span class="tile">{_e(ch.upper())}</span>' for ch in rd["letters"])
    scored = [(int(u), r) for u, r in rd["results"].items() if r["points"]]
    if scored:
        head = f'<i>ROUND {n} · LONGEST WORD SCORES</i>'
        ws = "".join(f'<span class="w"><b>{_e(r["word"].upper())}</b><em>{_e(names.get(u, "Someone").upper())} +{r["points"]}</em></span>'
                     for u, r in scored)
    else:
        head, ws = f'<i class="none">ROUND {n} · NOBODY FOUND A WORD</i>', ""
    corner = rd.get("corner") or []
    dc = (f'<div class="dc">{_coin()}DICTIONARY CORNER {"".join(f"<b>{_e(w.upper())}</b>" for w in corner)}</div>' if corner else "")
    return f'<div class="board">{tiles}</div><div class="best">{head}{f"<div class=ws>{ws}</div>" if ws else ""}{dc}</div>'


def game_page(room: dict, names: dict) -> str:
    """The game as it stands after each round, and at the end who won and what they took."""
    scores = room.get("scores", {})
    present = [u for u in room["players"] if u not in room.get("left", [])]
    if room.get("over"):
        winners = list(room.get("winners", []))
        if room.get("how") == "refund" or not winners:
            tag, title = f"FINAL · AFTER {len(room['rounds'])} ROUNDS", "<b>ALL SQUARE</b>"
        elif len(winners) > 1:
            tag, title = f"FINAL · AFTER {len(room['rounds'])} ROUNDS", "<b class='winner'>A TIE AT THE TOP</b>"
        else:
            tag = f"FINAL · AFTER {len(room['rounds'])} ROUNDS"
            title = f"<b class='winner'>{_e(names.get(winners[0], 'Someone').upper())} WINS</b>"
        stake = int(room.get("stake") or 0)
        if not stake:
            money = '<span class="chip light">A FRIENDLY</span>'
        elif room.get("how") == "refund":
            money = '<span class="chip light">STAKES RETURNED</span>'
        else:
            who = " & ".join(names.get(w, "Someone").upper() for w in winners)
            money = f'<span class="chip light">{_coin()}+{int(room.get("share", 0)):,} TO {_e(who)}</span>'
        top = set(winners)
    else:
        n = len(room["rounds"])
        done = bool(room["rounds"] and room["rounds"][-1].get("results"))
        tag = f"LIVE · {'AFTER ' if done else ''}ROUND {n} OF {countdown.ROUNDS}"
        title = "<b>COUNTDOWN</b>"
        money = _money(room)
        best = max((scores.get(str(u), 0) for u in present), default=0)
        top = {u for u in present if best and scores.get(str(u), 0) == best}
    return _page(f"""<div class="card lv">{clock_svg(30 if room.get("over") else 19)}
      <div class="lab"><i>{_e(tag)}</i>{title}</div>
      {_last_round(room, names)}
      {_table(room, names, top)}
      <div class="chips">{money}</div></div>""")


def page(room: dict, names: dict, event: str) -> str:
    return room_page(room, names, event) if event in ("open", "seats", "closed", "lapsed") else game_page(room, names)


async def png(room: dict, names: dict, event: str) -> bytes | None:
    try:
        from lib.core.image_processing import screenshot_html
        buf = await screenshot_html(page(room, names, event), size=(900, 1500), apply_trim=False, element_selector=".card",
                                    transparent=True)
        return buf.getvalue()
    except Exception:
        log.warning("couldn't draw the countdown card", exc_info=True)
        return None
