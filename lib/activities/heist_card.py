"""The pictures on a Bank Job's #casino post, in the activity's sticker look: the crew filling up (who's
taken which job), the job under way, and the result (who got out with how much, and who was nicked).

Drawn as HTML and photographed like Countdown's, from a snapshot of the crew (see heist_posts), with names
already looked up.
"""

from __future__ import annotations

import base64
import html
import logging
import math
from pathlib import Path

from lib.activities import heist

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
COIN = ROOT / "data" / "ukpence-small.svg"
FONTS = ROOT / "data" / "fonts"
COLOURS = ["#2A4FB0,#16265C", "#B06A1C,#5A2C0E", "#7A4AD0,#2A0F5A", "#14858C,#06303A", "#C21E3A,#2A040C", "#4A7A2A,#1E3A10"]
ROLE_ICON = {"hacker": "💻", "safecracker": "🔐", "lookout": "🔦", "wheelman": "🚐", "face": "🎩", "muscle": "💪"}

_HEAD = ("<!doctype html><html><head><meta charset='utf-8'><style>"
         "@import url('https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..900&display=block');"
         f"@font-face{{font-family:'ArchivoLocal';src:url('file://{FONTS / 'Archivo.ttf'}') format('truetype');font-weight:100 900}}")
_CSS = """*{margin:0;box-sizing:border-box} html,body{background:transparent} body{width:820px;font-family:Archivo,ArchivoLocal,sans-serif}
.card{width:820px;padding:30px 32px 32px;border-radius:28px;position:relative;overflow:hidden;color:#fff;
 background:radial-gradient(80% 60% at 74% 16%,#2C3B4F 0%,#121B28 58%,#070B11 100%)}
.card.won{background:radial-gradient(80% 60% at 74% 16%,#2F5A3A 0%,#12261A 58%,#060E09 100%)}
.card.bust{background:radial-gradient(80% 60% at 74% 16%,#5A2430 0%,#260E14 58%,#0E0508 100%)}
.door{position:absolute;right:-50px;top:-50px;width:250px;height:250px;transform:rotate(10deg);filter:drop-shadow(0 10px 18px rgba(0,0,0,.55))}
.lab{position:relative;display:inline-flex;flex-direction:column;align-items:flex-start;gap:6px;transform:rotate(-2deg);transform-origin:0 0;max-width:560px}
.lab i{font-style:normal;padding:4px 11px;background:#111;color:#FFC93C;font-weight:900;font-size:17px;letter-spacing:.14em}
.lab b{padding:4px 16px 0;background:#fff;color:#111;box-shadow:6px 6px 0 #111;font-weight:900;font-size:54px;line-height:62px}
.lab b.gold{background:#FFC93C}
.lab b.red{background:#E8364F;color:#fff}
.sub{position:relative;margin-top:22px;max-width:560px;font-weight:700;font-size:19px;line-height:26px;color:rgba(255,255,255,.75)}
.tape{display:inline-block;background:#fff;color:#111;font-weight:900;white-space:nowrap;box-shadow:4px 4px 0 #111}
.av{position:relative;overflow:hidden;width:64px;height:64px;flex:none;border-radius:50%;box-shadow:0 0 0 4px rgba(255,255,255,.5);
 display:flex;align-items:center;justify-content:center;font-weight:900;font-size:29px;color:#fff}
.av .pic{position:absolute;inset:0;width:100%;height:100%;border-radius:50%;object-fit:cover}
.av.q{background:none;box-shadow:inset 0 0 0 3px rgba(255,255,255,.25);color:rgba(255,255,255,.35)}
.seats{position:relative;display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin:34px 0 30px}
.seat{display:flex;flex-direction:column;align-items:center;gap:10px;min-width:0}
.seat .tape{max-width:118px;padding:3px 8px 0;font-size:16px;line-height:23px;overflow:hidden;text-overflow:ellipsis;transform:rotate(-1.5deg)}
.seat .role{font-style:normal;font-weight:900;font-size:12px;letter-spacing:.12em;color:#FFC93C;text-align:center}
.seat .dave{color:rgba(255,255,255,.4)}
.chips{position:relative;display:flex;flex-wrap:wrap;gap:12px}
.chip{display:flex;align-items:center;gap:8px;padding:7px 14px;background:#111;color:#fff;font-weight:900;font-size:20px;letter-spacing:.04em;
 box-shadow:inset 0 0 0 2px rgba(255,255,255,.18)}
.chip.light{background:#fff;color:#111;box-shadow:inset 0 0 0 3px #111}
.chip.gold{background:#FFC93C;color:#111;box-shadow:inset 0 0 0 3px #111}
.chip img{width:26px;height:26px}
.stamp{position:absolute;left:50%;top:52%;transform:translate(-50%,-50%) rotate(-10deg);padding:6px 22px;border-radius:8px;
 background:rgba(251,240,210,.95);box-shadow:inset 0 0 0 5px #B3122A;color:#B3122A;font-weight:900;font-size:64px;letter-spacing:.1em}
.dim .seats,.dim .chips,.dim .door{opacity:.45}
.table{position:relative;display:grid;grid-template-columns:1fr;gap:10px;margin:30px 0 26px}
.table.two{grid-template-columns:1fr 1fr}
.row{display:flex;align-items:center;gap:14px;padding:10px 16px 10px 12px;border-radius:18px;background:rgba(255,255,255,.07);
 box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.14);min-width:0;position:relative}
.row .av{width:48px;height:48px;font-size:22px;box-shadow:0 0 0 3px rgba(255,255,255,.5)}
.row .who{flex:1;min-width:0;display:flex;flex-direction:column}
.row .who b{font-weight:900;font-size:21px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .who small{font-weight:800;font-size:13px;letter-spacing:.1em;color:rgba(255,255,255,.55)}
.row .x{display:flex;flex-direction:column;align-items:flex-end}
.row .x b{font-weight:900;font-size:30px;font-variant-numeric:tabular-nums}
.row .x small{font-weight:900;font-size:15px;color:#7EE2A0;font-variant-numeric:tabular-nums}
.row.out{background:linear-gradient(#FFD56A,#F2B930);color:#111;box-shadow:inset 0 0 0 3px #111,5px 5px 0 #000}
.row.out .who small{color:rgba(0,0,0,.55)}
.row.out .x small{color:#0E6B2E}
.row.nicked{background:rgba(232,54,79,.16);box-shadow:inset 0 0 0 2px rgba(232,54,79,.6)}
.row.nicked .x b{color:#FF8A9A;font-size:22px;letter-spacing:.08em}
.row.nicked .x small{color:#FF8A9A}
.meter{position:relative;margin-top:26px;display:flex;align-items:center;gap:14px;font-weight:900;font-size:15px;letter-spacing:.14em}
.meter .bar{flex:1;height:16px;border-radius:9px;background:rgba(255,255,255,.12);overflow:hidden;box-shadow:inset 0 0 0 2px rgba(255,255,255,.18)}
.meter .bar i{display:block;height:100%;background:linear-gradient(90deg,#3CCB6E,#FFC93C 55%,#E8364F)}
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


def door_svg(open_by: float = 0.0) -> str:
    """A round vault door, its wheel turned a little; ``open_by`` (0-1) swings a gold glow out from behind it."""
    spokes = "".join(
        f'<path d="M100 100L{100 + 46 * math.sin(math.radians(a)):.1f} {100 - 46 * math.cos(math.radians(a)):.1f}" '
        f'stroke="#111" stroke-width="9" stroke-linecap="round"/>' for a in (20, 140, 260))
    bolts = "".join(f'<circle cx="{100 + 80 * math.sin(math.radians(a)):.1f}" cy="{100 - 80 * math.cos(math.radians(a)):.1f}" r="5" fill="#5B6878"/>'
                    for a in range(0, 360, 30))
    glow = f'<circle cx="100" cy="100" r="98" fill="#FFC93C" opacity="{0.35 + 0.5 * open_by:.2f}"/>' if open_by else ""
    return (f'<svg class="door" viewBox="0 0 200 200">{glow}<circle cx="100" cy="100" r="96" fill="#111"/>'
            f'<circle cx="100" cy="100" r="89" fill="#C9D1DA"/><circle cx="100" cy="100" r="86" fill="#A8B3BF"/>{bolts}'
            f'<circle cx="100" cy="100" r="62" fill="#D7DEE6" stroke="#111" stroke-width="5"/>{spokes}'
            f'<circle cx="100" cy="100" r="16" fill="#FFC93C" stroke="#111" stroke-width="5"/></svg>')


def _pic(faces: dict | None, uid) -> str:
    data = (faces or {}).get(uid)
    return f'<img class="pic" src="data:image/webp;base64,{base64.b64encode(data).decode()}">' if data else ""


def _av(room: dict, uid, names: dict, faces: dict | None = None) -> str:
    i = room["players"].index(uid) if uid in room["players"] else 0
    return (f'<span class="av" style="background:linear-gradient(135deg,{COLOURS[i % len(COLOURS)]})">'
            f'{_initial(names.get(uid, "Someone"))}{_pic(faces, uid)}</span>')


def _role(room: dict, uid) -> str:
    r = room["roles"].get(str(uid))
    return f"{ROLE_ICON.get(r, '')} {heist.ROLE_INFO[r][0].upper()}" if r else ""


def _target(room: dict) -> dict:
    return heist.TARGETS.get(room.get("target"), heist.TARGETS["post"])


def _money(room: dict) -> str:
    stake = int(room.get("stake") or 0)
    cap = room.get("cap") or _target(room)["cap"]
    up = f'<span class="chip gold">UP TO {cap:g}x</span>'
    if not stake:
        return f'<span class="chip light">A PRACTICE JOB · NOTHING STAKED</span>{up}'
    return f'<span class="chip light">{_coin()}{stake:,} BUY-IN EACH</span>{up}'


def room_page(room: dict, names: dict, event: str, faces: dict | None = None) -> str:
    """The crew getting together, or one that broke up before it went in."""
    gone = {"closed": "CALLED OFF", "lapsed": "NO-SHOW"}.get(event)
    seats = []
    for i in range(heist.SEATS):
        if i < len(room["players"]):
            uid = room["players"][i]
            seats.append(f'<div class="seat">{_av(room, uid, names, faces)}<span class="tape">{_e(names.get(uid, "Someone").upper())}</span>'
                         f'<i class="role">{_e(_role(room, uid))}</i></div>')
        else:
            seats.append('<div class="seat"><span class="av q">?</span><i class="role dave">DODGY DAVE</i></div>')
    t = _target(room)
    return _page(f"""<div class="card{' dim' if gone else ''}">{door_svg()}
      <div class="lab"><i>BANK JOB · {'CREW WANTED' if not gone else 'CREW'}</i><b>{_e(t['short'].upper())}</b></div>
      <p class="sub">{_e(t['name'])}. {_e(t['blurb'])}</p>
      <div class="seats">{''.join(seats)}</div>
      <div class="chips">{_money(room)}<span class="chip">CREW OF {t['min_crew']}+</span></div>
      {f'<div class="stamp">{gone}</div>' if gone else ''}</div>""")


def _rows(room: dict, names: dict, faces: dict | None = None) -> str:
    results = room.get("results") or {}
    out = (room.get("vault") or {}).get("out", {})
    stake = int(room.get("stake") or 0)
    ranked = sorted(room["players"], key=lambda u: (-(results.get(str(u), {}).get("take") or (out.get(str(u)) or [0, 0])[1]),
                                                    room["players"].index(u)))
    rows = []
    for u in ranked:
        r = results.get(str(u))
        if r is None:                       # still inside, or out with the take so far
            took = out.get(str(u))
            cls, big, small = ("out", f"{took[1]:.2f}x", "OUT") if took else ("", "INSIDE", "")
        elif r["how"] == "nicked":
            cls, big, small = "nicked", "NICKED", (f"-{stake:,}" if stake else "")
        else:
            cls, big = "out", f"{r['take']:.2f}x"
            small = (f"+{r['payout'] - stake:,}" if stake else ("CLEARED IT" if r["how"] == "cleared" else "GOT OUT"))
        rows.append(f'<div class="row {cls}">{_av(room, u, names, faces)}<span class="who"><b>{_e(names.get(u, "Someone"))}</b>'
                    f'<small>{_e(_role(room, u))}</small></span><span class="x"><b>{big}</b>'
                    f'{f"<small>{_e(small)}</small>" if small else ""}</span></div>')
    return f'<div class="table{" two" if len(ranked) > 3 else ""}">{"".join(rows)}</div>'


def game_page(room: dict, names: dict, faces: dict | None = None) -> str:
    """The job under way, and at the end who got out with what."""
    t = _target(room)
    over = room.get("over")
    how = room.get("how")
    v = room.get("vault") or {}
    if over:
        results = room.get("results") or {}
        everyone_out = results and all(r["how"] != "nicked" for r in results.values())
        cleared = any(r["how"] == "cleared" for r in results.values())
        if how == "clean":
            tag, title, cls = "A CLEAN JOB", "<b class='gold'>CLEAN GETAWAY</b>", "won"
        elif cleared:
            tag, title, cls = "NOBODY CAME", "<b class='gold'>CLEARED THE VAULT</b>", "won"
        elif everyone_out:
            tag, title, cls = "ALL OUT", "<b class='gold'>GOT AWAY WITH IT</b>", "won"
        elif not any(r["how"] != "nicked" for r in results.values()):
            tag, title, cls = "THE GETAWAY", "<b class='red'>ALL NICKED</b>", "bust"
        else:
            tag, title, cls = "THE GETAWAY", "<b>SOME GOT AWAY</b>", "bust"
        stake = int(room.get("stake") or 0)
        took = sum(r["payout"] for r in results.values()) if stake else 0
        chips = (f'<span class="chip light">{_coin()}{took:,} TAKEN</span>' if stake else '<span class="chip light">A PRACTICE JOB</span>')
        police = v.get("police_take")
        chips += (f'<span class="chip">THE POLICE CAME AT {police:.2f}x</span>' if police else '<span class="chip">THE POLICE NEVER CAME</span>')
        if room.get("bonus"):
            chips += '<span class="chip gold">CLEAN JOB BONUS</span>'
        body = f"""<div class="lab"><i>BANK JOB · {_e(t['short'].upper())} · {_e(tag)}</i>{title}</div>
          {_rows(room, names, faces)}<div class="chips">{chips}</div>"""
        return _page(f'<div class="card {cls}">{door_svg(1.0 if cls == "won" else 0.0)}{body}</div>')
    state = room.get("state")
    tag = {"plan": "MAKING THE PLAN", "job": "THE JOB'S ON", "report": "THE ALARM", "vault": "IN THE VAULT"}.get(state, "THE JOB'S ON")
    meter = ""
    if room.get("edge") is not None:
        alarm = (room["edge"] - heist.EDGE_BEST) / (heist.EDGE_WORST - heist.EDGE_BEST)
        meter = (f'<div class="meter">ALARM<span class="bar"><i style="width:{max(4, round(alarm * 100))}%"></i></span>'
                 f'{round(alarm * 100)}%</div>')
    twist = room.get("twist") or {}
    sub = f'<p class="sub">{_e(twist.get("name", ""))}. {_e(twist.get("text", ""))}</p>' if twist else ""
    return _page(f"""<div class="card">{door_svg(0.5 if state == 'vault' else 0.0)}
      <div class="lab"><i>BANK JOB · {_e(t['short'].upper())} · {tag}</i><b>{_e((room.get('approach') or 'the plan').upper())}</b></div>
      {sub}{meter}{_rows(room, names, faces)}<div class="chips">{_money(room)}</div></div>""")


def page(room: dict, names: dict, event: str, faces: dict | None = None) -> str:
    return (room_page(room, names, event, faces) if event in ("open", "seats", "closed", "lapsed")
            else game_page(room, names, faces))


async def png(room: dict, names: dict, event: str, faces: dict | None = None) -> bytes | None:
    try:
        from lib.core.image_processing import screenshot_html
        buf = await screenshot_html(page(room, names, event, faces), size=(900, 1500), apply_trim=False, element_selector=".card",
                                    transparent=True)
        return buf.getvalue()
    except Exception:
        log.warning("couldn't draw the bank job card", exc_info=True)
        return None
