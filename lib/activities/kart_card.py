"""The pictures on UKP Kart's posts, in the activity's road-sign look: the grid filling up, the lights going
out, and the result with everyone's place in a speed-limit roundel and their name on a yellow number plate.

Drawn as HTML and photographed like Countdown's, from a snapshot of the room (see kart_posts), with the
people's names already looked up. The karts are cut-outs of their concept art (data/kart/<car>.webp).
"""

from __future__ import annotations

import base64
import html
import logging
from pathlib import Path

from lib.activities import kart

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
KARTS = ROOT / "data" / "kart"
COIN = ROOT / "data" / "ukpence-small.svg"
FONTS = ROOT / "data" / "fonts"
CAR_NAMES = {"cab": "Black Cab", "bus": "Routemaster", "thomas": "Thomas", "brum": "Brum", "mini": "Mini",
             "robin": "Three-Wheeler", "wg": "Wallace & Gromit", "fryup": "Full English", "roadman": "Roadman",
             "scooter": "Nan's Scooter"}
TRACK_NAMES = {"village": "Little Puddleton", "silverstone": "Silverstone", "wobbling": "Much Wobbling",
               "clanking": "Great Clanking"}

_HEAD = ("<!doctype html><html><head><meta charset='utf-8'><style>"
         "@import url('https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..900&display=block');"
         f"@font-face{{font-family:'ArchivoLocal';src:url('file://{FONTS / 'Archivo.ttf'}') format('truetype');font-weight:100 900}}")
_CSS = """*{margin:0;box-sizing:border-box} html,body{background:transparent} body{width:820px;font-family:Archivo,ArchivoLocal,sans-serif}
.card{width:820px;padding:0 0 34px;position:relative;overflow:hidden;color:#fff;background:#2E3036;border-radius:26px;
 box-shadow:inset 22px 0 0 #2E3036,inset 30px 0 0 #F2C21B,inset 36px 0 0 #2E3036,inset 44px 0 0 #F2C21B,
            inset -22px 0 0 #2E3036,inset -30px 0 0 #F2C21B,inset -36px 0 0 #2E3036,inset -44px 0 0 #F2C21B}
.banner{position:relative;padding:22px 0 16px;text-align:center;background:
 repeating-conic-gradient(#141414 0 25%,#F4F0E6 0 50%) top left/22px 22px repeat-x,
 repeating-conic-gradient(#141414 0 25%,#F4F0E6 0 50%) bottom left/22px 22px repeat-x,#C8102E}
.banner b{display:block;font-weight:900;font-size:76px;line-height:1;letter-spacing:.04em;text-shadow:0 5px 0 rgba(0,0,0,.3);padding-top:12px}
.sub{display:flex;justify-content:center;gap:12px;margin:22px 64px 0;flex-wrap:wrap}
.sign{display:flex;align-items:center;gap:10px;padding:9px 16px 7px;font-weight:900;font-size:24px;letter-spacing:.06em;
 border:4px solid #fff;outline:3px solid currentColor}
.sign.route{background:#00703C;outline-color:#00703C;color:#fff}
.sign.route i{font-style:normal;color:#F7C51E}
.sign.money{background:#1D5EBD;outline-color:#1D5EBD}
.sign.money img{width:30px;height:30px}
.sign.fast{background:#6B2FD6;outline-color:#6B2FD6;color:#fff;max-width:640px}
.sign.fast b{font-variant-numeric:tabular-nums}
.sign.fast i{font-style:normal;color:#E4D6FF;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.sign.fun{background:#fff;color:#141414;border-color:#141414;outline-color:#fff}
.rows{display:flex;flex-direction:column;gap:12px;margin:28px 64px 0}
.row{display:flex;align-items:center;gap:14px;height:86px;padding:0 12px 0 10px;background:#1E2026;border:3px solid #3C3F47;position:relative}
.row.top{border-color:#F7C51E;box-shadow:0 0 0 2px #1E2026,0 0 0 5px #F7C51E}
.row.cpu{height:62px;opacity:.6}
.row.gone{opacity:.45}
.place{width:66px;height:66px;flex:none;border-radius:50%;background:#fff;border:8px solid #D4202A;color:#141414;display:flex;
 align-items:center;justify-content:center;font-weight:900;font-size:30px}
.place i{font-style:normal;font-size:12px;align-self:flex-start;margin-top:10px}
.row.cpu .place{width:48px;height:48px;border-width:6px;font-size:22px}
.row.cpu .place i{font-size:9px;margin-top:7px}
.av{width:58px;height:58px;flex:none;border-radius:50%;overflow:hidden;background:#555;position:relative;display:flex;align-items:center;
 justify-content:center;font-weight:900;font-size:26px}
.av img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.plate{flex:1;min-width:0;display:flex;align-items:stretch;height:52px;background:#F7C51E;border:3px solid #141414;border-radius:5px}
.plate span{width:24px;background:#1F4BB8;color:#F7C51E;font-size:10px;font-weight:800;display:flex;align-items:flex-end;justify-content:center;padding-bottom:4px}
.plate b{flex:1;min-width:0;padding:4px 12px 0;color:#141414;font-weight:900;font-size:27px;letter-spacing:.05em;display:flex;align-items:center;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row.cpu .plate{height:40px;background:#C9CCD3}
.row.cpu .plate span{background:#5B606B;color:#C9CCD3}
.row.cpu .plate b{font-size:20px}
.kart{width:120px;height:80px;flex:none;object-fit:contain;filter:drop-shadow(0 5px 5px rgba(0,0,0,.5))}
.row.cpu .kart{width:84px;height:56px}
.host{position:absolute;left:84px;top:-12px;padding:2px 8px 1px;background:#00703C;border:2px solid #fff;font-weight:900;font-size:13px;letter-spacing:.12em}
.won{flex:none;min-width:120px;text-align:right;font-weight:900;font-size:30px;font-variant-numeric:tabular-nums}
.won.pay{color:#5BE37D}
.won small{display:block;font-size:16px;color:#C9CCD3;letter-spacing:.04em}
.row.cpu .won{font-size:20px;min-width:90px}
.fill{margin:16px 64px 0;padding:14px 18px;border:3px dashed #5B606B;color:#C9CCD3;font-weight:900;font-size:22px;letter-spacing:.06em;
 display:flex;align-items:center;gap:14px}
.fill .karts{display:flex;gap:2px}
.fill .karts img{width:58px;height:40px;object-fit:contain;opacity:.75}
.lights{display:flex;justify-content:center;margin:30px 0 4px}
.lights div{display:flex;gap:14px;padding:14px;background:#1E2026;border:4px solid #0D0E11}
.lights i{width:64px;height:64px;border-radius:50%;background:#34363D}
.lights i.g{background:#2FE05A;box-shadow:0 0 30px 8px rgba(47,224,90,.6)}
.stamp{position:absolute;left:50%;top:54%;transform:translate(-50%,-50%) rotate(-9deg);padding:8px 26px;background:#fff;color:#D4202A;
 border:8px solid #D4202A;font-weight:900;font-size:62px;letter-spacing:.08em}
.dim .rows,.dim .fill{opacity:.4}
"""


def _e(text: str) -> str:
    return html.escape(text or "", quote=True)


def _page(body: str) -> str:
    return f"{_HEAD}{_CSS}</style></head><body>{body}{FIT}</body></html>"


def _img(path: Path, cls: str = "") -> str:
    return f'<img class="{cls}" src="file://{path}">' if path.exists() else ""


def _kart(car: str, cls: str = "kart") -> str:
    return _img(KARTS / f"{car if car in CAR_NAMES else 'cab'}.webp", cls)


def _ord(n: int) -> str:
    return "st" if n % 10 == 1 and n % 100 != 11 else "nd" if n % 10 == 2 and n % 100 != 12 else "rd" if n % 10 == 3 and n % 100 != 13 else "th"


def _av(uid, names: dict, faces: dict | None) -> str:
    data = (faces or {}).get(uid)
    pic = f'<img src="data:image/webp;base64,{base64.b64encode(data).decode()}">' if data else ""
    initial = _e((names.get(uid, "?").strip()[:1] or "?").upper())
    return f'<span class="av">{initial}{pic}</span>'


# every plate's name shrunk a pixel at a time until it fits, once as the page loads and again once the font's in
FIT = ("<script>function fit(){document.querySelectorAll('.plate b').forEach(function(b){"
       "var s=parseFloat(getComputedStyle(b).fontSize);if(b.scrollWidth>b.clientWidth)b.style.letterSpacing='0';"
       "while(b.scrollWidth>b.clientWidth&&s>12){s-=1;b.style.fontSize=s+'px';}});}"
       "fit();if(document.fonts)document.fonts.ready.then(fit);</script>")


def _plate(name: str, cpu: bool = False) -> str:
    """A name on a number plate, in smaller letters the longer it is so it fits (a CPU's plate is wider); the page
    shrinks it further if it still doesn't (FIT)."""
    base, room = (20, 22) if cpu else (27, 10)
    size = base if len(name) <= room else max(14, round(base * room / len(name)))
    style = "" if size == base else f' style="font-size:{size}px;letter-spacing:.02em"'
    return f'<div class="plate"><span>UK</span><b{style}>{_e(name.upper())}</b></div>'


def _clock(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:05.2f}"


def _signs(room: dict) -> str:
    stake = int(room.get("stake") or 0)
    laps = kart.TRACKS.get(room.get("track"), kart.TRACKS["village"])["laps"]
    route = f'<div class="sign route">{_e(TRACK_NAMES.get(room.get("track"), "Little Puddleton").upper())} <i>{laps} LAPS</i></div>'
    if not stake:
        return f'<div class="sub">{route}<div class="sign fun">FOR FUN</div></div>'
    coin = _img(COIN)
    pot = stake * len(room["players"])
    return (f'<div class="sub">{route}<div class="sign money">{coin}{stake:,} EACH</div>'
            f'<div class="sign money">{coin}POT {kart.prize(pot) if pot else 0:,}</div></div>')


def grid_page(room: dict, names: dict, event: str, faces: dict | None = None) -> str:
    """The room filling up, or the lights going out."""
    rows = []
    for u in room["players"]:
        car = room.get("cars", {}).get(str(u), "cab")
        host = '<em class="host">HOST</em>' if u == room["host"] else ""
        rows.append(f'<div class="row">{host}{_av(u, names, faces)}{_plate(names.get(u, "Someone"))}{_kart(car)}</div>')
    cpus = kart.SEATS - len(room["players"])
    fill = ""
    if cpus > 0:
        show = "".join(_kart(c, "") for c in list(CAR_NAMES)[:min(cpus, 5)])
        fill = f'<div class="fill"><div class="karts">{show}</div>+ {cpus} CPU {"KART" if cpus == 1 else "KARTS"}</div>'
    lights = '<div class="lights"><div><i></i><i></i><i class="g"></i></div></div>' if event == "start" else ""
    ended = event in ("closed", "lapsed")
    stamp = f'<div class="stamp">{"CALLED OFF" if event == "closed" else "NO RACE"}</div>' if ended else ""
    return _page(f'<div class="card {"dim" if ended else ""}"><div class="banner"><b>UKP KART</b></div>{_signs(room)}'
                 f'{lights}<div class="rows">{"".join(rows)}</div>{fill}{stamp}</div>')


def result_page(room: dict, names: dict, faces: dict | None = None) -> str:
    grid = {g["id"]: g for g in room.get("grid", [])}
    fin, shares = room.get("finish", {}), room.get("shares", {})
    ranked = kart.order(room)
    winners = set(room.get("winners", []))
    rows = []
    for place, kid in enumerate(ranked, 1):
        g = grid.get(kid, {"cpu": kid.startswith("cpu"), "car": "cab", "name": "CPU"})
        cpu = g["cpu"]
        name = g.get("name") or "CPU" if cpu else names.get(int(kid), "Someone")
        if kid in fin:
            right = f'<div class="won">{_clock(fin[kid])}</div>'
        else:
            right = '<div class="won"><small>DID NOT</small>FINISH</div>' if not cpu else '<div class="won">-</div>'
        if kid in shares and shares[kid]:
            right = f'<div class="won pay">+{int(shares[kid]):,}<small>{_clock(fin.get(kid, 0))}</small></div>'
        cls = "cpu" if cpu else "top" if kid in winners else "gone" if int(kid) in room.get("left", []) else ""
        face = "" if cpu else _av(int(kid), names, faces)
        rows.append(f'<div class="row {cls}"><div class="place">{place}<i>{_ord(place)}</i></div>{face}{_plate(name, cpu)}'
                    f'{_kart(g.get("car", "cab"))}{right}</div>')
    refund = '<div class="stamp">STAKES BACK</div>' if room.get("how") == "refund" and room.get("stake") else ""
    # the race's fastest lap, F1 style, for anyone whose page reported one
    laps = {k: t for k, t in room.get("laps", {}).items() if k in ranked}
    fast = ""
    if laps:
        kid = min(laps, key=laps.get)
        g = grid.get(kid, {})
        who = (g.get("name") or "CPU") if kid.startswith("cpu") else names.get(int(kid), "Someone")
        fast = f'<div class="sub"><div class="sign fast">FASTEST LAP <b>{_clock(laps[kid])}</b> <i>{_e(who.upper())}</i></div></div>'
    return _page(f'<div class="card"><div class="banner"><b>UKP KART</b></div>{_signs(room)}{fast}'
                 f'<div class="rows">{"".join(rows)}</div>{refund}</div>')


def page(room: dict, names: dict, event: str, faces: dict | None = None) -> str:
    return result_page(room, names, faces) if event == "over" else grid_page(room, names, event, faces)


async def png(room: dict, names: dict, event: str, faces: dict | None = None) -> bytes | None:
    try:
        from lib.core.image_processing import screenshot_html
        buf = await screenshot_html(page(room, names, event, faces), size=(900, 1700), apply_trim=False, element_selector=".card",
                                    transparent=True)
        return buf.getvalue()
    except Exception:
        log.warning("couldn't draw the kart card", exc_info=True)
        return None
