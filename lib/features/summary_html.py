"""Render the daily/weekly/monthly server summary card.

``post_summary`` gathers the numbers into a plain ``card`` dict; this module turns it
into HTML (``build_summary_html``, pure and testable) and then a PNG. The layout is a
dark Discord-style stack of widgets: messages/members with sparklines, an activity view
that changes with the period (hourly bars, a day-by-hour heatmap, or a month calendar),
headline counters, channel share, a podium of chatters and reactor chips.
"""

from __future__ import annotations

import html
from datetime import date

from lib.core.image_processing import screenshot_html
from lib.core.file_operations import read_html_template

PERIODS = {
    "daily": {"accent": "#5865F2", "text": "#C9CDFB", "label": "DAILY", "title": "Daily recap"},
    "weekly": {"accent": "#23A55A", "text": "#A6F0C2", "label": "WEEKLY", "title": "Weekly recap"},
    "monthly": {"accent": "#F0B232", "text": "#FBDC94", "label": "MONTHLY", "title": "Monthly recap"},
}

BLURPLE = "#5865F2"
GREEN = "#23A55A"
CHANNEL_COLOURS = ["#5865F2", "#EB459E", "#F0B232", "#23A55A"]
OTHER_COLOUR = "#4E5058"
HEAT_LEVELS = ["#2B2D31", "#2F3466", "#3A44A8", "#4C58E0", "#7983F5", "#B9BFFF"]
CAL_LEVELS = ["#2B2D31", "#4A3A12", "#7A5A12", "#B7841A", "#F0B232"]
AVATAR_COLOURS = ["#3B5BDB", "#C2410C", "#0F766E", "#7C3AED", "#15803D",
                  "#BE185D", "#475569", "#B45309", "#0369A1", "#9F1239"]
MEDAL = {1: "#F0B232", 2: "#B8BEC9", 3: "#C98B4E"}
CARD_W = 1000
# Hero widgets split the card in two: padding 28 each side, 14 gap, 1px borders, 22px padding.
SPARK_W, SPARK_H = (CARD_W - 56 - 14) // 2 - 46, 84

ICON_MESSAGES = ('<svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" '
                 'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
                 '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>')
ICON_MEMBERS = ('<svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" '
                'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
                '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.6 3.3-5.5 6.5-5.5s5.9 1.9 6.5 5.5"/>'
                '<path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18.5 14.8c1.7.7 2.8 2.4 3 5.2"/></svg>')

def esc(value) -> str:
    return html.escape(str(value), quote=True)


def fmt(n) -> str:
    return f"{int(n):,}"


def signed(n) -> str:
    n = int(n)
    return f"+{n:,}" if n > 0 else (f"−{abs(n):,}" if n < 0 else "0")


def rgba(hex_colour: str, alpha: float) -> str:
    h = hex_colour.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def hour_label(h: int) -> str:
    suffix = "am" if h < 12 else "pm"
    return f"{h % 12 or 12}{suffix}"


def short_name(name: str, limit: int = 18) -> str:
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


def pct_change(current, previous):
    if not previous:
        return None
    return (current - previous) / previous * 100


def sparkline(series, colour: str) -> str:
    """Area + line chart sized to the hero widget, with a dot on the latest value."""
    values = [int(v) for v in series if v is not None]
    if len(values) < 2:
        return f'<svg class="spark" viewBox="0 0 {SPARK_W} {SPARK_H}"></svg>'
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    step = (SPARK_W - 8) / (len(values) - 1)
    pts = [(4 + i * step, 78 - (v - lo) / span * 66) for i, v in enumerate(values)]
    if hi == lo:
        pts = [(x, 45) for x, _ in pts]
    line = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
    area = f"{line} L{pts[-1][0]:.1f} {SPARK_H} L{pts[0][0]:.1f} {SPARK_H} Z"
    lx, ly = pts[-1]
    return (f'<svg class="spark" viewBox="0 0 {SPARK_W} {SPARK_H}" fill="none">'
            f'<path d="{area}" fill="{rgba(colour, 0.2)}"/>'
            f'<path d="{line}" stroke="{colour}" stroke-width="3.5" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="5" fill="#FFFFFF" stroke="{colour}" stroke-width="3"/></svg>')


def avatar(person: dict, extra_style: str = "") -> str:
    if person.get("avatar"):
        return f'<img class="avatar" src="{esc(person["avatar"])}" style="{extra_style}">'
    colour = person.get("colour") or AVATAR_COLOURS[0]
    initial = esc((person.get("name") or "?").strip()[:1].upper() or "?")
    return f'<div class="avatar" style="background:{colour};{extra_style}">{initial}</div>'


# ---------- Sections ----------

def _header(card) -> str:
    period = PERIODS[card["frequency"]]
    return (
        '<div class="header"><div class="brand"><div class="badge"><div class="roundel"></div></div>'
        f'<div><div class="h-title">{period["title"]}</div>'
        f'<div class="h-date">{esc(card["subtitle"])}</div></div></div>'
        f'<div class="period"><i></i>{period["label"]}</div></div>'
    )


def _delta_chip(text: str, value) -> str:
    cls = "up" if value and value > 0 else ("down" if value and value < 0 else "flat")
    return f'<span class="delta {cls}">{text}</span>'


def _hero(card) -> str:
    change = pct_change(card["messages"], card.get("messages_prev"))
    msg_chip = _delta_chip(f"{'+' if change >= 0 else chr(0x2212)}{abs(change):.0f}%", round(change)) \
        if change is not None else ""
    member_change = card.get("members_change")
    mem_chip = _delta_chip(signed(member_change), member_change) if member_change is not None else ""
    widgets = [
        ("Messages", ICON_MESSAGES, BLURPLE, msg_chip, card["messages"],
         card.get("message_series", []), card.get("message_caption", "")),
        ("Members", ICON_MEMBERS, GREEN, mem_chip, card["members"],
         card.get("member_series", []), card.get("member_caption", "")),
    ]
    out = []
    for label, icon, colour, chip, value, series, caption in widgets:
        out.append(
            '<div class="widget hero grow">'
            f'<div class="w-head"><div class="label"><span class="icon" style="background:{colour}">{icon}</span>'
            f'{label}</div>{chip}</div>'
            f'<div class="num big">{fmt(value)}</div>'
            f'{sparkline(series, colour)}'
            f'<div class="caption">{esc(caption)}</div></div>'
        )
    return f'<div class="row">{"".join(out)}</div>'


def _hours_widget(hours, accent) -> str:
    peak = max(hours) if hours else 0
    peak_hour = hours.index(peak) if peak else None
    bars = []
    for v in hours:
        height = max(4, round(v / peak * 100)) if peak else 4
        if peak and v == peak:
            colour = accent
        elif peak and v >= peak * 0.6:
            colour = rgba(accent, 0.7)
        else:
            colour = "#3A3D44"
        bars.append(f'<div style="height:{height}px;background:{colour}"></div>')
    note = f"<i></i>Peak {hour_label(peak_hour)} · {fmt(peak)} msgs" if peak_hour is not None else ""
    return (
        f'<div class="widget"><div class="w-head"><div class="w-title">Activity by hour</div>'
        f'<div class="w-note">{note}</div></div>'
        f'<div class="bars">{"".join(bars)}</div>'
        '<div class="axis"><span>12am</span><span>6am</span><span>12pm</span><span>6pm</span><span>11pm</span></div></div>'
    )


def heat_level(value: int, peak: int) -> int:
    if not value or not peak:
        return 0
    r = value / peak
    return 1 if r < 0.1 else 2 if r < 0.25 else 3 if r < 0.45 else 4 if r < 0.7 else 5


def _week_widget(days) -> str:
    peak = max((max(h) for _, h in days), default=0)
    peak_at = None
    rows = []
    for day, hours in days:
        cells = []
        for hour, v in enumerate(hours):
            is_peak = peak and v == peak and peak_at is None
            if is_peak:
                peak_at = (day, hour)
            cells.append(f'<div class="{"peak" if is_peak else ""}" '
                         f'style="background:{HEAT_LEVELS[heat_level(v, peak)]}"></div>')
        rows.append(f'<div class="heat-row"><b>{day.strftime("%a")}</b>{"".join(cells)}</div>')
    note = (f"<i></i>Peak: {peak_at[0].strftime('%a')} {hour_label(peak_at[1])} · {fmt(peak)} msgs"
            if peak_at else "")
    return (
        f'<div class="widget"><div class="w-head"><div class="w-title">When people talked</div>'
        f'<div class="w-note">{note}</div></div>'
        f'<div class="heat">{"".join(rows)}</div>'
        '<div class="axis indent"><span>12am</span><span>6am</span><span>12pm</span><span>6pm</span><span>11pm</span></div></div>'
    )


def cal_level(value, peak) -> int:
    if not value or not peak:
        return 0
    r = value / peak
    return 1 if r < 0.4 else 2 if r < 0.6 else 3 if r < 0.8 else 4


def calendar_weeks(days):
    """Lay (date, count) pairs out Monday-first, padding the first/last week with None."""
    if not days:
        return []
    cells = [None] * days[0][0].weekday() + list(days)
    while len(cells) % 7:
        cells.append(None)
    return [cells[i:i + 7] for i in range(0, len(cells), 7)]


def _month_widget(days) -> str:
    counted = [(d, v) for d, v in days if v]
    peak = max((v for _, v in counted), default=0)
    best = max(counted, key=lambda dv: dv[1]) if counted else None
    quiet = min(counted, key=lambda dv: dv[1]) if counted else None
    head = "".join(f'<div class="cal-head">{n}</div>' for n in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"))
    rows = [f'<div class="cal-row">{head}</div>']
    for week in calendar_weeks(days):
        cells = []
        for cell in week:
            if cell is None:
                cells.append("<div></div>")
                continue
            d, v = cell
            level = cal_level(v, peak)
            classes = ["day"] + (["light"] if level == 4 else []) + (["best"] if best and d == best[0] else [])
            value = f"{v / 1000:.1f}k" if v and v >= 1000 else (str(v) if v else "-")
            cells.append(f'<div class="{" ".join(classes)}" style="background:{CAL_LEVELS[level]}">'
                         f'<b>{d.day}</b><span>{value}</span></div>')
        rows.append(f'<div class="cal-row">{"".join(cells)}</div>')
    note = f"<i></i>Best: {best[0].strftime('%a %-d %b')} · {fmt(best[1])}" if best else ""
    avg = sum(v for _, v in counted) / len(days) if days else 0
    foot = ""
    if quiet:
        foot = (f'<div class="cal-foot"><span>Quietest: {quiet[0].strftime("%a %-d %b")} · {fmt(quiet[1])}</span>'
                f'<span>Average {fmt(round(avg))} a day</span></div>')
    return (
        f'<div class="widget"><div class="w-head"><div class="w-title">Day by day</div>'
        f'<div class="w-note">{note}</div></div>'
        f'<div class="cal">{"".join(rows)}</div>{foot}</div>'
    )


def _activity(card) -> str:
    activity = card.get("activity") or {}
    kind = activity.get("kind")
    if kind == "hours":
        return _hours_widget(activity["hours"], PERIODS[card["frequency"]]["accent"])
    if kind == "week":
        return _week_widget(activity["days"])
    if kind == "month":
        return _month_widget(activity["days"])
    return ""


def _tile(label, dot, value, sub="", bad=False) -> str:
    text = fmt(value) if isinstance(value, int) else str(value)
    size = ' style="font-size:32px"' if len(text) > 6 else ""
    sub_html = f'<div class="sub{" bad" if bad else ""}">{esc(sub)}</div>' if sub else ""
    return (f'<div class="tile"><div class="label"><i style="background:{dot}"></i>{label}</div>'
            f'<div class="value num"{size}>{text}</div>{sub_html}</div>')


def _counters(card) -> str:
    banned = card.get("banned", 0)
    removed = card.get("reactions_removed", 0)
    lost = card.get("boosts_lost", 0)
    first = "".join([
        _tile("Joined", "#23A55A", card.get("joined", 0)),
        _tile("Left", "#F23F43", card.get("left", 0), f"{fmt(banned)} bans" if banned else "", bad=True),
        _tile("Reactions", "#F0B232", card.get("reactions", 0), f"{fmt(removed)} removed" if removed else ""),
        _tile("Boosts", "#EB459E", card.get("boosts_gained", 0), f"{fmt(lost)} lost" if lost else ""),
    ])
    second = "".join([
        _tile("Chatting", BLURPLE, card.get("chatting", 0), "people posted"),
        _tile("New voices", "#57F287", card.get("new_voices", 0), "first post on record"),
        _tile("Media", "#00A8FC", card.get("media", 0), "images & files"),
        _tile("Deleted", "#80848E", card.get("deleted", 0), "messages"),
    ])
    return f'<div class="row">{first}</div><div class="row">{second}</div>'


def _pct_label(p: float) -> str:
    return "<1%" if 0 < p < 1 else f"{p:.0f}%"


def _channels(card) -> str:
    total = card.get("messages", 0)
    channels = card.get("channels", [])[:4]
    if not total or not channels:
        return ""
    entries = [(name, count, CHANNEL_COLOURS[i], False) for i, (name, count) in enumerate(channels)]
    other = total - sum(c for _, c in channels)
    if other > 0:
        entries.append(("Everywhere else", other, OTHER_COLOUR, True))
    bar = "".join(f'<div style="width:{max(count / total * 100, 1):.1f}%;background:{colour}"></div>'
                  for _, count, colour, _ in entries)
    legend = "".join(
        f'<div class="legend-row{" other" if is_other else ""}"><i style="background:{colour}"></i>'
        f'<div class="name ellipsis">{esc(name)}</div><div class="count num">{fmt(count)}</div>'
        f'<div class="pct">{_pct_label(count / total * 100)}</div></div>'
        for name, count, colour, is_other in entries)
    return (
        f'<div class="widget"><div class="w-head"><div class="w-title">Where people talked</div>'
        f'<div class="w-note">share of {fmt(total)} msgs</div></div>'
        f'<div class="share">{bar}</div><div class="legend">{legend}</div></div>'
    )


def _podium(card) -> str:
    people = card.get("chatters", [])
    if not people:
        return ""
    names = {0: "first", 1: "second", 2: "third"}
    labels = {0: "1ST", 1: "2ND", 2: "3RD"}
    places = []
    for idx in (1, 0, 2):
        if idx >= len(people):
            continue
        p = people[idx]
        places.append(
            f'<div class="place {names[idx]}">'
            f'{avatar(p, f"border-color:{MEDAL[idx + 1]}")}'
            f'<div class="who ellipsis">{esc(p["name"])}</div>'
            f'<div class="block"><div class="num">{fmt(p["count"])}</div><small>{labels[idx]}</small></div></div>'
        )
    runners = "".join(
        f'<div class="runner"><span class="rank">{i + 1}</span>{avatar(p)}'
        f'<div class="who ellipsis">{esc(p["name"])}</div><div class="num">{fmt(p["count"])}</div></div>'
        for i, p in enumerate(people[3:5], start=3))
    also = "".join(
        f'<span>{i + 1}&nbsp;&nbsp;{esc(short_name(p["name"]))}&nbsp;&nbsp;{fmt(p["count"])}</span>'
        for i, p in enumerate(people[5:10], start=5))
    return (
        '<div class="widget"><div class="w-head"><div class="w-title">Chattiest members</div>'
        '<div class="w-note">messages sent</div></div>'
        f'<div class="podium">{"".join(places)}</div>'
        + (f'<div class="row" style="gap:12px">{runners}</div>' if runners else "")
        + (f'<div class="also">{also}</div>' if also else "")
        + "</div>"
    )


def _reactors(card) -> str:
    people = card.get("reactors", [])
    if not people:
        return ""
    chips = "".join(
        f'<div class="chip{" top" if i == 0 else ""}">{avatar(p)}'
        f'<div class="who ellipsis">{esc(p["name"])}</div><div class="num">{fmt(p["count"])}</div></div>'
        for i, p in enumerate(people))
    return (
        '<div class="widget"><div class="w-head"><div class="w-title">Top reactors</div>'
        '<div class="w-note">reactions given</div></div>'
        f'<div class="chips">{chips}</div></div>'
    )


def build_summary_html(card: dict) -> str:
    period = PERIODS[card["frequency"]]
    body = "\n".join(part for part in (
        _header(card), _hero(card), _activity(card), _counters(card),
        _channels(card), _podium(card), _reactors(card),
    ) if part)
    template = read_html_template("templates/summary.html")
    for token, value in (
        ("{{card_width}}", f"{CARD_W}px"),
        ("{{spark_width}}", f"{SPARK_W}px"),
        ("{{accent}}", period["accent"]),
        ("{{accent_soft}}", rgba(period["accent"], 0.18)),
        ("{{accent_edge}}", rgba(period["accent"], 0.55)),
        ("{{accent_text}}", period["text"]),
    ):
        template = template.replace(token, value)
    # Body last, so nothing a member typed into their name can be treated as a token.
    return template.replace("{{body}}", body)


async def create_summary_image(card: dict):
    return await screenshot_html(build_summary_html(card), size=(CARD_W, 2600), element_selector=".card")
