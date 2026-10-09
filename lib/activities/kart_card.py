"""The pictures on UKP Kart's posts, as an arcade cabinet's screen (the user's pick, 2026-10-08): the grid filling
up as a player select, the lights going out, and the result as a high-score table, each place in its own colour.

Drawn as HTML and photographed like Countdown's, from a snapshot of the room (see kart_posts), with the
people's names already looked up. The karts are cut-outs of their concept art (data/kart/<car>.webp); the lettering
is Press Start 2P (data/fonts, OFL).
"""

from __future__ import annotations

import html
import logging
from pathlib import Path

from lib.activities import kart

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
KARTS = ROOT / "data" / "kart"
PIXEL = ROOT / "data" / "fonts" / "PressStart2P.ttf"
CAR_NAMES = {"cab": "Black Cab", "bus": "Routemaster", "thomas": "Thomas", "brum": "Brum", "mini": "Mini",
             "robin": "Three-Wheeler", "wg": "Wallace & Gromit", "fryup": "Full English", "roadman": "Roadman",
             "scooter": "Nan's Scooter"}
TRACK_NAMES = {"village": "Little Puddleton", "silverstone": "Silverstone", "wobbling": "Much Wobbling",
               "clanking": "Great Clanking", "wallop": "Nether Wallop", "rainbow": "Rainbow Road"}
# each place in its own colour, as an arcade's high-score table has them: gold for the winner, red for last
COLOURS = ["#FFD23F", "#3CF0FF", "#FF8A3D", "#7CFF6B", "#FF6BD6", "#FFFFFF", "#B58CFF", "#FF3B3B"]

_CSS = f"""@font-face{{font-family:'Pixel';src:url('file://{PIXEL}') format('truetype')}}
*{{margin:0;box-sizing:border-box;font-weight:400}} html,body{{background:transparent}}
body{{width:820px;font-family:Pixel,monospace;-webkit-font-smoothing:none}}
.card{{width:820px;padding:40px 46px 36px;position:relative;overflow:hidden;color:#fff;border:6px solid #1B1B1B;
 background:repeating-linear-gradient(0deg,rgba(255,255,255,.035) 0 1px,transparent 1px 4px),
  radial-gradient(ellipse at 50% 40%,#0E1A12 0%,#050505 75%) #050505}}
.top{{display:flex;font-size:16px;line-height:18px}}
.top div{{flex:1;display:flex;flex-direction:column;gap:8px;min-width:0}}
.top .mid{{flex:1.6;align-items:center;text-align:center}} .top .r{{align-items:flex-end;text-align:right}}
.top i{{font-style:normal;color:#FF3B3B}} .top b{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}}
.title{{text-align:center;margin:34px 0 36px}}
.title b{{display:block;font-size:54px;line-height:60px;color:#FFD23F;text-shadow:5px 5px 0 #E8361E;letter-spacing:.02em}}
.title i{{display:block;margin-top:18px;font-style:normal;font-size:20px;line-height:22px;color:#3CF0FF;letter-spacing:.08em}}
.head,.row{{display:flex;align-items:center;gap:14px}}
.head{{height:30px;font-size:14px;line-height:16px;margin-bottom:6px}}
.row{{height:58px;font-size:20px;line-height:22px;color:var(--c)}}
.row.first{{height:64px;font-size:24px;line-height:26px}}
.row.gone{{opacity:.45}}
.rk{{width:76px;flex:none;white-space:nowrap}}
.sp{{width:56px;height:46px;flex:none;object-fit:contain}}
.nm{{flex:1;min-width:0;display:flex;align-items:center;gap:10px}}
.nm b{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}}
.nm small{{flex:none;font-size:10px;line-height:12px;color:#8A8A8A}} .nm small.host{{color:#7CFF6B}}
.tm{{width:160px;flex:none;text-align:right;white-space:nowrap}} .row.first .tm,.row.first .uk{{font-size:20px}}
.uk{{width:108px;flex:none;text-align:right;white-space:nowrap}} .nil{{color:#5A5A5A}}
.out{{flex:none;text-align:right;white-space:nowrap;width:282px}}
.free .uk{{display:none}} .free .out{{width:160px}}
.car{{width:210px;flex:none;text-align:right;font-size:12px;line-height:14px;color:#8A8A8A;white-space:nowrap}}
.fill{{display:flex;align-items:center;gap:14px;height:58px;font-size:16px;line-height:18px;color:#8A8A8A}}
.fill .karts{{display:flex;gap:4px}} .fill img{{width:46px;height:36px;object-fit:contain;opacity:.7}}
.lights{{display:flex;justify-content:center;gap:18px;margin:-12px 0 34px}}
.lights i{{width:56px;height:56px;border:5px solid #000;outline:3px solid #333;background:#2FE05A;box-shadow:0 0 26px rgba(47,224,90,.75)}}
.fast{{display:flex;justify-content:center;margin-top:30px}}
.fast div{{display:flex;align-items:center;gap:18px;padding:12px 20px;border:3px solid #B58CFF;font-size:16px;line-height:18px;max-width:100%}}
.fast i{{font-style:normal;color:#B58CFF;flex:none}} .fast b{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}}
.fast em{{font-style:normal;flex:none}}
.foot{{display:flex;align-items:flex-end;margin-top:24px}}
.foot small{{flex:1;font-size:12px;line-height:14px;color:#8A8A8A;white-space:nowrap}} .foot small.r{{text-align:right}}
.foot b{{flex:none;font-size:16px;line-height:18px;color:#FFD23F}}
.dim .table{{opacity:.35}}
.stamp{{position:absolute;left:50%;top:56%;transform:translate(-50%,-50%) rotate(-8deg);padding:16px 26px;background:#050505;
 border:6px solid #FF3B3B;color:#FF3B3B;font-size:44px;line-height:48px;white-space:nowrap;box-shadow:8px 8px 0 #000}}
"""

# every name shrunk a pixel at a time until it fits its column, once as the page loads and again once the font's in
FIT = ("<script>function fit(){document.querySelectorAll('.fit').forEach(function(b){"
       "var s=parseFloat(getComputedStyle(b).fontSize);"
       "while(b.scrollWidth>b.clientWidth&&s>11){s-=1;b.style.fontSize=s+'px';b.style.lineHeight=(s+2)+'px';}});}"
       "fit();if(document.fonts)document.fonts.ready.then(fit);</script>")


def _e(text: str) -> str:
    return html.escape(text or "", quote=True)


def _page(body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>{body}{FIT}</body></html>"


def _img(path: Path, cls: str = "") -> str:
    return f'<img class="{cls}" src="file://{path}">' if path.exists() else ""


def _kart(car: str, cls: str = "sp") -> str:
    return _img(KARTS / f"{car if car in CAR_NAMES else 'cab'}.webp", cls)


def _ord(n: int) -> str:
    return "ST" if n % 10 == 1 and n % 100 != 11 else "ND" if n % 10 == 2 and n % 100 != 12 else "RD" if n % 10 == 3 and n % 100 != 13 else "TH"


def _clock(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:05.2f}"


def _track(room: dict) -> tuple[str, int]:
    return (TRACK_NAMES.get(room.get("track"), "Little Puddleton").upper(),
            kart.TRACKS.get(room.get("track"), kart.TRACKS["village"])["laps"])


def _top(left: tuple[str, str], room: dict, right: tuple[str, str]) -> str:
    track, _ = _track(room)
    return (f'<div class="top"><div><i>{left[0]}</i><b>{_e(left[1])}</b></div>'
            f'<div class="mid"><i>TRACK</i><b>{_e(track)}</b></div>'
            f'<div class="r"><i>{right[0]}</i><b>{_e(right[1])}</b></div></div>')


def _title(sub: str) -> str:
    return f'<div class="title"><b>UKP KART</b><i>- {sub} -</i></div>'


def _foot(left: str, middle: str, right: str) -> str:
    return f'<div class="foot"><small>{left}</small><b>{middle}</b><small class="r">{right}</small></div>'


def grid_page(room: dict, names: dict, event: str) -> str:
    """The room filling up (a player select), the lights going out, or the race called off before it started."""
    stake = int(room.get("stake") or 0)
    host = room["host"]
    rows = []
    for slot, u in enumerate(room["players"]):
        car = room.get("cars", {}).get(str(u), "cab")
        tag = '<small class="host">HOST</small>' if u == host else ""
        rows.append(f'<div class="row" style="--c:{COLOURS[slot % len(COLOURS)]}"><div class="rk">{slot + 1}P</div>{_kart(car)}'
                    f'<div class="nm"><b class="fit">{_e(names.get(u, "Someone").upper())}</b>{tag}</div>'
                    f'<div class="car">{_e(CAR_NAMES.get(car, "Black Cab").upper())}</div></div>')
    cpus = kart.SEATS - len(room["players"])
    if cpus > 0:
        show = "".join(_kart(c, "") for c in list(CAR_NAMES)[:min(cpus, 5)])
        rows.append(f'<div class="fill"><div class="rk"></div><div class="karts">{show}</div>'
                    f'+ {cpus} CPU {"RACER" if cpus == 1 else "RACERS"}</div>')
    ended = event in ("closed", "lapsed")
    sub = ("CALLED OFF" if event == "closed" else "NO RACE" if event == "lapsed" else "GO! GO! GO!" if event == "start"
           else "PLAYER SELECT")
    lights = '<div class="lights"><i></i><i></i><i></i></div>' if event == "start" else ""
    stamp = '<div class="stamp">GAME OVER</div>' if ended else ""
    _, laps = _track(room)
    middle = ("PRESS PLAY FOR A NEW RACE" if ended else "LIGHTS OUT" if event == "start"
              else "PRESS JOIN TO PLAY" if cpus > 0 else "GRID FULL")
    entry = ("ENTRY", f"{stake:,} UKP") if stake else ("PLAY", "FREE")
    count = f"{len(room['players'])}/{kart.SEATS} PLAYERS"
    return _page(f'<div class="card {"dim" if ended else ""}">{_top(("HOST", names.get(host, "Someone").upper()), room, entry)}'
                 f'{_title(sub)}{lights}<div class="table">{"".join(rows)}</div>'
                 f'{_foot(count, middle, f"{laps} LAPS")}{stamp}</div>')


def result_page(room: dict, names: dict) -> str:
    """The result as a high-score table: the winner's time, everyone else's gap to it, and what the people won."""
    grid = {g["id"]: g for g in room.get("grid", [])}
    fin, shares = room.get("finish", {}), room.get("shares", {})
    stake = int(room.get("stake") or 0)
    ranked = kart.order(room)
    left = set(room.get("left", []))
    first = min(fin.values()) if fin else None
    rows = []
    for place, kid in enumerate(ranked, 1):
        g = grid.get(kid, {"cpu": kid.startswith("cpu"), "car": "cab", "name": "CPU"})
        cpu = g["cpu"]
        name = (g.get("name") or "CPU") if cpu else names.get(int(kid), "Someone")
        tag = '<small>CPU</small>' if cpu else ""
        if kid in fin:
            tm = _clock(fin[kid]) if fin[kid] == first else f"+{fin[kid] - first:.2f}"
            won = int(shares.get(kid, 0))
            right = f'<div class="tm">{tm}</div><div class="uk">{f"{won:,}" if won else "<span class=nil>--</span>"}</div>'
        else:
            # a person who didn't get home is out of the game; a CPU that didn't just has no time
            right = ('<div class="tm nil">--</div><div class="uk nil">--</div>' if cpu
                     else '<div class="out">GAME OVER</div>')
        cls = "first" if place == 1 else "gone" if not cpu and int(kid) in left else ""
        rows.append(f'<div class="row {cls}" style="--c:{COLOURS[(place - 1) % len(COLOURS)]}"><div class="rk">{place}{_ord(place)}</div>'
                    f'{_kart(g.get("car", "cab"))}<div class="nm"><b class="fit">{_e(name.upper())}</b>{tag}</div>{right}</div>')
    head = ('<div class="head"><div class="rk">RANK</div><div class="sp"></div><div class="nm">NAME</div>'
            '<div class="tm">TIME</div><div class="uk">UKP</div></div>')
    # the race's fastest lap, for anyone whose page reported one
    laps = {k: t for k, t in room.get("laps", {}).items() if k in ranked}
    fast = ""
    if laps:
        kid = min(laps, key=laps.get)
        g = grid.get(kid, {})
        who = (g.get("name") or "CPU") if kid.startswith("cpu") else names.get(int(kid), "Someone")
        # the time always shows; a long name is what gets cut short
        fast = f'<div class="fast"><div><i>FASTEST LAP</i><b>{_e(who.upper())}</b><em>{_clock(laps[kid])}</em></div></div>'
    refund = room.get("how") == "refund"
    stamp = f'<div class="stamp">{"STAKES BACK" if stake else "NO FINISHERS"}</div>' if refund else ""
    pot = stake * len(room["players"])
    _, n_laps = _track(room)
    top = _top(("1UP", _clock(first) if first is not None else "--"), room,
               ("POT", f"{kart.prize(pot):,}") if stake else ("PLAY", "FREE"))
    return _page(f'<div class="card {"" if stake else "free"} {"dim" if refund else ""}">{top}{_title("HIGH SCORES")}'
                 f'<div class="table">{head}{"".join(rows)}</div>{fast}'
                 f'{_foot("CREDIT 0", "PRESS PLAY TO RACE AGAIN", f"{n_laps} LAPS")}{stamp}</div>')


def page(room: dict, names: dict, event: str) -> str:
    return result_page(room, names) if event == "over" else grid_page(room, names, event)


async def png(room: dict, names: dict, event: str) -> bytes | None:
    try:
        from lib.core.image_processing import screenshot_html
        buf = await screenshot_html(page(room, names, event), size=(900, 1700), apply_trim=False, element_selector=".card",
                                    transparent=True)
        return buf.getvalue()
    except Exception:
        log.warning("couldn't draw the kart card", exc_info=True)
        return None
