"""Rendered leaderboard cards: XP leaderboard, UKPence rich list and the medal table.

All three share one dark Discord-style layout (templates/leaderboard_card.html): a
header with the page count, a strip of headline stats, a podium for the top three on
page one, then ranked rows for the rest of the page (20 entries per page in total).

``render_*_page`` gather what the card needs (names, avatars, custom titles and
backgrounds, peerage progress, weekly balance history) and the ``build_*_html``
functions turn plain dicts into markup, so the layout can be tested without a browser.
"""

from __future__ import annotations

import math
import os
import time

from config import BASE_DIR, BOT_ID, ROLES
from database import DatabaseManager
from lib.core.constants import CHAT_LEVEL_ROLE_THRESHOLDS, CUSTOM_RANK_BACKGROUNDS
from lib.core.file_operations import read_html_template
from lib.core.image_processing import encode_image_to_data_uri, get_avatar_data_uri, screenshot_html
from lib.features.summary_html import AVATAR_COLOURS, MEDAL, esc, fmt, rgba, signed

CARD_W = 1000
PAGE_SIZE = 20
UNKNOWN_COLOUR = "#4E5058"

THEMES = {
    "xp": {"accent": "#5865F2", "text": "#C9CDFB", "first": "#5865F2",
           "first_text": "#FFFFFF", "first_sub": "#E0E3FF"},
    "rich": {"accent": "#23A55A", "text": "#A6F0C2", "first": "#1E8E4E",
             "first_text": "#FFFFFF", "first_sub": "#D1FADF"},
    "medal": {"accent": "#F0B232", "text": "#FBDC94",
              "first": "linear-gradient(180deg, #F0B232 0%, #C98A12 100%)",
              "first_text": "#1E1F22", "first_sub": "#3A2A05"},
}


# ---------- Shared helpers ----------

def compact(n) -> str:
    """17003145 -> 17.0M, 605836 -> 605.8k; under 10k stays exact."""
    n = int(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}"


def page_count(total: int) -> int:
    return max(1, math.ceil(total / PAGE_SIZE))


def _peerage_ladder():
    names = {}
    for attr, value in vars(ROLES).items():
        if attr.isupper() and isinstance(value, int):
            names.setdefault(value, attr.replace("_", " ").title())
    return [(threshold, names.get(role_id, "Rank")) for threshold, role_id in CHAT_LEVEL_ROLE_THRESHOLDS]


PEERAGES = _peerage_ladder()


def peerage(xp: int) -> dict:
    """Where an XP total sits on the peerage ladder and how far the next rung is."""
    current, floor = None, 0
    for threshold, name in PEERAGES:
        if xp >= threshold:
            current, floor = name, threshold
        else:
            span = threshold - floor
            return {"name": current, "next": name, "gap": threshold - xp,
                    "progress": (xp - floor) / span if span else 0.0}
    return {"name": current, "next": None, "gap": 0, "progress": 1.0}


def _avatar_inner(person: dict, size: int, style: str = "") -> str:
    font = max(14, size * 2 // 5)
    if person.get("avatar"):
        return (f'<img class="avatar" src="{esc(person["avatar"])}" '
                f'style="width:{size}px;height:{size}px;{style}">')
    initial = esc((person.get("name") or "?").strip()[:1].upper() or "?")
    return (f'<div class="avatar" style="width:{size}px;height:{size}px;font-size:{font}px;'
            f'background:{person.get("colour") or UNKNOWN_COLOUR};{style}">{initial}</div>')


def avatar_html(person: dict, size: int, ring: str | None = None) -> str:
    """Avatar with an optional medal-coloured border, or a rainbow ring for secret-badge holders."""
    if person.get("secret"):
        # The ring sits inside the usual footprint so names stay in line with other rows.
        inner = _avatar_inner(person, size - 6, "border:2px solid #1E1F22;")
        return f'<div class="ring secret" style="width:{size}px;height:{size}px">{inner}</div>'
    style = f"border:4px solid {ring};" if ring else ""
    return _avatar_inner(person, size, style)


def _custom_row_style(person: dict) -> str:
    bg = person.get("background")
    if not bg:
        return ""
    return (' style="background-image:linear-gradient(90deg, rgba(30,31,34,0.96) 0%, '
            'rgba(30,31,34,0.82) 55%, rgba(30,31,34,0.9) 100%), '
            f"url('{bg}')\"")


def _header(title: str, sub: str, page: int, pages: int) -> str:
    return (
        '<div class="header"><div class="brand"><div class="badge"><div class="roundel"></div></div>'
        f'<div><div class="h-title">{esc(title)}</div><div class="h-sub">{esc(sub)}</div></div></div>'
        f'<div class="page-chip">PAGE {fmt(page)} OF {fmt(pages)}</div></div>'
    )


def _stats(tiles) -> str:
    """tiles: (label, label colour, value, sub)."""
    return '<div class="stats">' + "".join(
        f'<div class="tile"><div class="label" style="color:{colour}">{esc(label)}</div>'
        f'<div class="value num">{esc(value)}</div><div class="sub">{esc(sub)}</div></div>'
        for label, colour, value, sub in tiles) + "</div>"


def _podium(places) -> str:
    """places: up to three dicts in rank order with avatar/who/note/block html."""
    order = [(1, "second"), (0, "first"), (2, "third")]
    out = []
    for idx, cls in order:
        if idx >= len(places):
            continue
        p = places[idx]
        out.append(f'<div class="place {cls}">{p["avatar"]}<div class="who ellipsis">{esc(p["name"])}</div>'
                   f'{p["note"]}<div class="block">{p["block"]}</div></div>')
    return f'<div class="widget podium">{"".join(out)}</div>' if out else ""


def _render(kind: str, body_parts) -> str:
    theme = THEMES[kind]
    template = read_html_template("templates/leaderboard_card.html")
    for token, value in (
        ("{{accent}}", theme["accent"]),
        ("{{accent_soft}}", rgba(theme["accent"], 0.16)),
        ("{{accent_edge}}", rgba(theme["accent"], 0.55)),
        ("{{accent_text}}", theme["text"]),
        ("{{first_block}}", theme["first"]),
        ("{{first_text}}", theme["first_text"]),
        ("{{first_sub}}", theme["first_sub"]),
    ):
        template = template.replace(token, value)
    # Body last, so a member's name can never be read as a template token.
    return template.replace("{{body}}", "\n".join(p for p in body_parts if p))


def _split(entries, page: int):
    """Page one opens with a podium for the top three; later pages are all rows."""
    return (entries[:3], entries[3:]) if page == 1 else ([], entries)


def _title_chip(person) -> str:
    title = person.get("title")
    return f'<div class="chip-title ellipsis">{esc(title)}</div>' if title else ""


def _who_col(person) -> str:
    title = person.get("title")
    title_html = f'<div class="title ellipsis">{esc(title)}</div>' if title else ""
    return f'<div class="who-col"><div class="name ellipsis">{esc(person["name"])}</div>{title_html}</div>'


def _row(person, middle: str, value: str) -> str:
    custom = " custom" if person.get("background") else ""
    return (f'<div class="row{custom}"{_custom_row_style(person)}>'
            f'<div class="rank">{esc(person["rank_label"])}</div>{avatar_html(person, 40)}'
            f'{_who_col(person)}{middle}<div class="value-col num">{value}</div></div>')


# ---------- XP leaderboard ----------

def build_xp_html(entries, *, page: int, pages: int, members: int, total_xp: int,
                  top_peerage: str | None, top_peerage_holders: int) -> str:
    """entries: dicts with rank, rank_label, name, avatar, colour, title, background, xp."""
    for e in entries:
        e.setdefault("peerage", peerage(e["xp"]))
    top, rest = _split(entries, page)

    closest = min((e for e in entries if e["peerage"]["next"]), key=lambda e: e["peerage"]["gap"], default=None)
    noun = "member" if top_peerage_holders == 1 else "members"
    tiles = [
        ("Total XP", "#8C95FF", compact(total_xp), "earned across the server"),
        ("Highest peerage", "#F0B232", top_peerage or "None yet",
         f"held by {fmt(top_peerage_holders)} {noun}" if top_peerage else ""),
        ("Next promotion", "#3BD47A",
         f"{fmt(closest['peerage']['gap'])} XP" if closest else "-",
         f"#{closest['rank']} to {closest['peerage']['next']}" if closest else "everyone here is maxed"),
    ]

    places = []
    for i, e in enumerate(top):
        p = e["peerage"]
        places.append({
            "name": e["name"],
            "avatar": avatar_html(e, 92 if i == 0 else 72, MEDAL[i + 1]),
            "note": _title_chip(e),
            "block": f'<div class="big num">{fmt(e["xp"])}</div><div class="small">{esc(p["name"] or "No peerage")}</div>',
        })

    rows = []
    for e in rest:
        p = e["peerage"]
        close = p["next"] and p["progress"] >= 0.9
        if p["next"]:
            gap = f'{fmt(p["gap"])} to {"next" if p["name"] else p["next"]}'
        else:
            gap = "Top rank"
        middle = (f'<div class="mid"><div class="mid-top"><span class="label">{esc(p["name"] or "No peerage")}</span>'
                  f'<span class="gap{" close" if close else ""}">{esc(gap)}</span></div>'
                  f'<div class="track"><div class="{"close" if close else ""}" '
                  f'style="width:{max(2, round(p["progress"] * 100))}%"></div></div></div>')
        rows.append(_row(e, middle, fmt(e["xp"])))

    return _render("xp", [
        _header("XP Leaderboard", f"All time · {fmt(members)} members ranked", page, pages),
        _stats(tiles),
        _podium(places),
        f'<div class="widget rows">{"".join(rows)}</div>' if rows else "",
    ])


# ---------- Rich list ----------

def sparkline(series, change) -> str:
    values = [v for v in series if v is not None]
    colour = "#23A55A" if change and change > 0 else "#F23F43" if change and change < 0 else "#80848E"
    if len(values) < 2 or max(values) == min(values):
        return (f'<svg class="spark" viewBox="0 0 250 26" fill="none"><line x1="1" y1="13" x2="249" y2="13" '
                f'stroke="{colour}" stroke-width="2.5" stroke-linecap="round"/></svg>')
    lo, hi = min(values), max(values)
    step = 248 / (len(values) - 1)
    pts = " ".join(f"{1 + i * step:.1f},{23 - (v - lo) / (hi - lo) * 20:.1f}" for i, v in enumerate(values))
    return (f'<svg class="spark" viewBox="0 0 250 26" fill="none"><polyline points="{pts}" stroke="{colour}" '
            'stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/></svg>')


def _change_text(change) -> tuple[str, str]:
    if change is None:
        return "new", "label dim"
    if change == 0:
        return "no change", "label dim"
    return signed(change), "up" if change > 0 else "down"


def build_rich_html(entries, *, page: int, pages: int, holders: int, circulation: int,
                    treasury: int, top_share: float) -> str:
    """entries: dicts with rank, rank_label, name, avatar, colour, title, background,
    balance, change (7-day, or None) and series (8 daily samples)."""
    top, rest = _split(entries, page)
    tiles = [
        ("In circulation", "#3BD47A", compact(circulation), "held by players"),
        ("The Treasury", "#DBDEE1", compact(treasury), "bank reserves, not ranked"),
        ("Top 20 hold", "#F0B232", f"{top_share:.0f}%", "of all player UKPence"),
    ]

    places = []
    for i, e in enumerate(top):
        text, cls = _change_text(e["change"])
        week = f"{text} this week" if e["change"] else text
        places.append({
            "name": e["name"],
            "avatar": avatar_html(e, 92 if i == 0 else 72, MEDAL[i + 1]),
            "note": _title_chip(e),
            "block": f'<div class="big num">{fmt(e["balance"])}</div><div class="small {cls}">{esc(week)}</div>',
        })

    rows = []
    for e in rest:
        text, cls = _change_text(e["change"])
        middle = (f'<div class="mid"><div class="mid-top"><span class="label dim">7 days</span>'
                  f'<span class="{cls}">{esc(text)}</span></div>{sparkline(e["series"], e["change"])}</div>')
        rows.append(_row(e, middle, fmt(e["balance"])))

    return _render("rich", [
        _header("Rich List", f"UKPence · {fmt(holders)} holders", page, pages),
        _stats(tiles),
        _podium(places),
        f'<div class="widget rows">{"".join(rows)}</div>' if rows else "",
    ])


# ---------- Medal table ----------

def _medal_counts(g, s, b, size: int) -> str:
    return (f'<div class="medals num"><span><i class="disc gold" style="width:{size}px;height:{size}px"></i>{g}</span>'
            f'<span><i class="disc silver" style="width:{size}px;height:{size}px"></i>{s}</span>'
            f'<span><i class="disc bronze" style="width:{size}px;height:{size}px"></i>{b}</span></div>')


def build_medal_html(entries, *, page: int, pages: int, ranked: int, awarded: dict,
                     defined: dict, secret_holders: int) -> str:
    """entries: dicts with rank, rank_label, name, avatar, colour, secret, gold, silver, bronze, total."""
    collection = defined.get("Gold", 0) + defined.get("Silver", 0) + defined.get("Bronze", 0)
    top, rest = _split(entries, page)
    tiles = [
        ("Gold", "#F0B232", fmt(awarded.get("Gold", 0)), f"awarded · {defined.get('Gold', 0)} badges"),
        ("Silver", "#C9D1DB", fmt(awarded.get("Silver", 0)), f"awarded · {defined.get('Silver', 0)} badges"),
        ("Bronze", "#D9925A", fmt(awarded.get("Bronze", 0)), f"awarded · {defined.get('Bronze', 0)} badges"),
        ("Secret", "#E28DF5", fmt(secret_holders), f"holders · {defined.get('Secret', 0)} badges"),
    ]

    places = []
    for i, e in enumerate(top):
        full = collection and e["total"] >= collection
        note = (f'<div class="chip-gold">Full set · all {collection} badges</div>' if full
                else f'<div class="note">{e["total"]} of {collection} badges</div>')
        label = ("1ST", "2ND", "3RD")[i]
        places.append({
            "name": e["name"],
            "avatar": avatar_html(e, 92 if i == 0 else 72, MEDAL[i + 1]),
            "note": note,
            "block": f'{_medal_counts(e["gold"], e["silver"], e["bronze"], 24 if i == 0 else 20)}'
                     f'<div class="small" style="letter-spacing:0.1em">{label}</div>',
        })

    heads = ('<div class="col-heads"><div style="width:34px">#</div><div style="width:40px"></div>'
             '<div style="flex:1">MEMBER</div><div style="width:170px">COLLECTION</div>'
             '<div style="width:62px;text-align:right">GOLD</div><div style="width:62px;text-align:right">SILVER</div>'
             '<div style="width:68px;text-align:right">BRONZE</div><div style="width:62px;text-align:right">TOTAL</div></div>')
    rows = []
    for e in rest:
        pct = round(e["total"] / collection * 100) if collection else 0
        rows.append(
            f'<div class="row"><div class="rank">{esc(e["rank_label"])}</div>{avatar_html(e, 40)}'
            f'<div class="who-col"><div class="name ellipsis">{esc(e["name"])}</div></div>'
            f'<div class="collection"><div class="label">{e["total"]} of {collection}</div>'
            f'<div class="track"><div style="width:{max(2, pct)}%"></div></div></div>'
            f'<div class="medal-col num"><i class="disc gold" style="width:16px;height:16px"></i>{e["gold"]}</div>'
            f'<div class="medal-col num"><i class="disc silver" style="width:16px;height:16px"></i>{e["silver"]}</div>'
            f'<div class="medal-col wide num"><i class="disc bronze" style="width:16px;height:16px"></i>{e["bronze"]}</div>'
            f'<div class="total-col num">{e["total"]}</div></div>')

    return _render("medal", [
        _header("Medal Table", f"Badges · {fmt(ranked)} members ranked", page, pages),
        _stats(tiles),
        _podium(places),
        f'<div class="widget rows with-heads">{heads}{"".join(rows)}</div>' if rows else "",
    ])


# ---------- Data gathering ----------

async def _person(client, guild, user_id, rank: int, rank_label: str | None = None) -> dict:
    member = guild.get_member(int(user_id)) if guild else None
    person = {
        "rank": rank,
        "rank_label": rank_label or str(rank),
        "user_id": str(user_id),
        "name": member.display_name if member else "Unknown member",
        "avatar": None,
        "colour": AVATAR_COLOURS[int(user_id) % len(AVATAR_COLOURS)] if member else UNKNOWN_COLOUR,
    }
    if member:
        try:
            url = member.display_avatar.with_size(128).with_static_format("png").url
            person["avatar"] = await get_avatar_data_uri(client, url)
        except Exception:
            pass
    return person


async def _prefetch_members(guild, user_ids):
    missing = [int(uid) for uid in user_ids if guild.get_member(int(uid)) is None]
    if missing:
        try:
            await guild.query_members(user_ids=missing, cache=True)
        except Exception:
            pass


def _customisations(user_ids) -> dict:
    """{user_id: {"title": str|None, "background": data URI|None}} from the rank-card shop."""
    rows = []
    if user_ids:
        placeholders = ",".join("?" * len(user_ids))
        rows = DatabaseManager.fetch_all(
            f"SELECT user_id, title, background FROM user_rank_customization WHERE user_id IN ({placeholders})",
            tuple(user_ids)) or []
    found = {str(uid): (title, bg) for uid, title, bg in rows}
    out = {}
    for uid in user_ids:
        title, bg_file = found.get(uid, (None, None))
        bg_file = bg_file or CUSTOM_RANK_BACKGROUNDS.get(uid)
        background = None
        if bg_file and bg_file != "unionjack.png":
            path = os.path.join(BASE_DIR, "data", "rank_cards", bg_file)
            if os.path.exists(path):
                background = encode_image_to_data_uri(path)
        out[uid] = {"title": title or None, "background": background}
    return out


async def _people_with_extras(client, guild, page_rows, offset):
    user_ids = [str(uid) for uid, _ in page_rows]
    await _prefetch_members(guild, user_ids)
    extras = _customisations(user_ids)
    people = []
    for i, (uid, _) in enumerate(page_rows):
        person = await _person(client, guild, uid, offset + i + 1)
        person.update(extras.get(str(uid), {}))
        people.append(person)
    return people


async def render_xp_page(client, guild, sorted_xp, offset: int) -> bytes:
    page_rows = sorted_xp[offset:offset + PAGE_SIZE]
    people = await _people_with_extras(client, guild, page_rows, offset)
    for person, (_, xp) in zip(people, page_rows):
        person["xp"] = int(xp)
    top_peerage = peerage(int(sorted_xp[0][1]))["name"] if sorted_xp else None
    holders = sum(1 for _, x in sorted_xp if peerage(int(x))["name"] == top_peerage) if top_peerage else 0
    html = build_xp_html(people, page=offset // PAGE_SIZE + 1, pages=page_count(len(sorted_xp)),
                         members=len(sorted_xp), total_xp=sum(int(x) for _, x in sorted_xp),
                         top_peerage=top_peerage, top_peerage_holders=holders)
    buf = await screenshot_html(html, size=(CARD_W, 2400), element_selector=".card")
    return buf.getvalue()


def week_history(user_id, current: int, now: int | None = None, days: int = 7):
    """(change over the week or None if there's no record that old, daily samples)."""
    now = int(now or time.time())
    start = now - days * 86400
    base = DatabaseManager.fetch_one(
        "SELECT balance FROM balance_history WHERE user_id = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
        (str(user_id), start))
    rows = DatabaseManager.fetch_all(
        "SELECT ts, balance FROM balance_history WHERE user_id = ? AND ts > ? ORDER BY ts ASC",
        (str(user_id), start)) or []
    value = base[0] if base else (rows[0][1] if rows else current)
    samples, i = [], 0
    for day in range(days + 1):
        cutoff = start + day * 86400
        while i < len(rows) and rows[i][0] <= cutoff:
            value = rows[i][1]
            i += 1
        samples.append(int(value))
    samples[-1] = int(current)
    return (int(current) - int(base[0]) if base else None), samples


async def render_rich_page(client, guild, balances, offset: int) -> bytes:
    page_rows = balances[offset:offset + PAGE_SIZE]
    people = await _people_with_extras(client, guild, page_rows, offset)
    for person, (uid, bal) in zip(people, page_rows):
        person["balance"] = int(bal)
        person["change"], person["series"] = week_history(uid, int(bal))
    circulation = sum(int(b) for _, b in balances)
    bank = DatabaseManager.fetch_one("SELECT balance FROM ukpence WHERE user_id = ?", (str(BOT_ID),))
    top_share = (sum(int(b) for _, b in balances[:PAGE_SIZE]) / circulation * 100) if circulation else 0
    html = build_rich_html(people, page=offset // PAGE_SIZE + 1, pages=page_count(len(balances)),
                           holders=sum(1 for _, b in balances if int(b) > 0), circulation=circulation,
                           treasury=int(bank[0]) if bank else 0, top_share=top_share)
    buf = await screenshot_html(html, size=(CARD_W, 2400), element_selector=".card")
    return buf.getvalue()


def badge_totals():
    """(badges defined per rarity, awards per rarity, members holding a Secret badge)."""
    defined = dict(DatabaseManager.fetch_all("SELECT rarity, COUNT(*) FROM badges GROUP BY rarity") or [])
    awarded = dict(DatabaseManager.fetch_all(
        "SELECT b.rarity, COUNT(*) FROM user_badges ub JOIN badges b ON b.id = ub.badge_id "
        "GROUP BY b.rarity") or [])
    secret = DatabaseManager.fetch_one(
        "SELECT COUNT(DISTINCT ub.user_id) FROM user_badges ub JOIN badges b ON b.id = ub.badge_id "
        "WHERE b.rarity = 'Secret'")
    return defined, awarded, int(secret[0]) if secret else 0


async def render_medal_page(client, guild, table, page_index: int) -> bytes:
    """table rows: (rank, shared, user_id, gold, silver, bronze, total, has_secret)."""
    offset = page_index * PAGE_SIZE
    page_rows = table[offset:offset + PAGE_SIZE]
    await _prefetch_members(guild, [str(r[2]) for r in page_rows])
    people = []
    for rank, shared, uid, g, s, b, total, secret in page_rows:
        person = await _person(client, guild, uid, rank, f"={rank}" if shared else str(rank))
        person.update({"gold": g, "silver": s, "bronze": b, "total": total, "secret": secret})
        people.append(person)
    defined, awarded, secret_holders = badge_totals()
    html = build_medal_html(people, page=page_index + 1, pages=page_count(len(table)), ranked=len(table),
                            awarded=awarded, defined=defined, secret_holders=secret_holders)
    buf = await screenshot_html(html, size=(CARD_W, 2400), element_selector=".card")
    return buf.getvalue()
