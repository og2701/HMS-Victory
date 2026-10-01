"""The /ukpeconomy card: the state of the UKPence economy as one dark widget card.

How the closed supply splits between players and the bank, what players held over the last
30 days (the daily balance snapshots), headline tiles, this week's money flow between the
bank and players (the user_transactions ledger, sorted with /statement's categories), the
richest five, wealth brackets and yesterday's payouts. Posted daily at 00:05 and on demand.
"""

import asyncio
import html
import json
import logging
import os
import time
from datetime import datetime, timedelta

import discord
import pytz

import config
from database import DatabaseManager
from lib.economy.economy_manager import get_all_balances

logger = logging.getLogger(__name__)

_UK = pytz.timezone("Europe/London")
_MINUS = "−"

# Yesterday's payouts = bank -> player rewards, summed per source. economy_transactions is a
# drained processing queue (rows are deleted after they're posted to the log channel), so it's
# useless for history. Instead each source writes a running daily total into the persistent
# economy_metrics.json, and we read yesterday's totals here. Casino/lottery are left out: net
# of stakes they're a sink, and they show up in the money flow instead.
_PAYOUTS = [
    ("Boosters", "booster_rewards_total"), ("Bumps", "bump_total"), ("Stage", "stage_rewards_total"),
    ("Tree", "tree_total"), ("Welcome", "welcome_total"), ("Benefits", "benefits_total"),
    ("Chat", "chat_activity_total"), ("Top chatters", "chat_rewards_total"), ("Tickets", "ticket_total"),
    ("Hall of Fame", "hof_total"),
]

# Category colours for the flow bands (statement._categorize's labels, as on the balance card)
_CAT_COLOURS = {
    "Casino": "#F472B6", "Rewards": "#3BD47A", "Tax": "#F87171", "Bond": "#94A3B8", "Predictions": "#A78BFA",
    "Welcome": "#A3E635", "Benefits": "#2DD4BF", "Shop": "#FB923C", "Admin": "#CBD5E1", "Lucky Dip": "#C026D3",
    "Games": "#FDE047", "Fines": "#E11D48", "Lottery": "#F59E0B", "Other": "#6B7280",
}
_AVATAR_COLOURS = ["#5865F2", "#EB459E", "#23A55A", "#F0B232", "#3BA3F5"]


def _fmt(n):
    n = int(round(n))
    a = abs(n)
    s = f"{a / 1000:.1f}k".replace(".0k", "k") if a >= 1000 else str(a)
    return (_MINUS if n < 0 else "") + s


def _signed(n):
    n = int(n)
    return f"+{n:,}" if n > 0 else (f"{_MINUS}{abs(n):,}" if n < 0 else "0")


def _player_balances():
    bot = str(config.BOT_ID)
    return {uid: int(bal) for uid, bal in get_all_balances().items() if uid != bot}


def _summary(balances):
    """Headline numbers for {user_id: balance} (the bot already left out)."""
    pos = sorted(v for v in balances.values() if v > 0)
    n = len(pos)
    gini = (2 * sum((i + 1) * v for i, v in enumerate(pos)) / (n * sum(pos)) - (n + 1) / n) if n else 0.0
    # The median ignores the long tail of near-empty accounts, which would pin it near zero.
    over = [v for v in pos if v > 250]
    m = len(over) // 2
    median = (over[m] if len(over) % 2 else (over[m - 1] + over[m]) / 2) if over else 0
    brackets = {"1-1k": 0, "1k-10k": 0, "10k-100k": 0, "100k+": 0}
    for v in pos:
        brackets["1-1k" if v <= 1000 else "1k-10k" if v <= 10000 else "10k-100k" if v <= 100000 else "100k+"] += 1
    top = sorted(balances.items(), key=lambda kv: -kv[1])[:5]
    return {"total": sum(balances.values()), "holders": n, "over_1k": sum(1 for v in pos if v > 1000),
            "median": median, "gini": gini, "brackets": brackets, "top": top}


def _record_circulation(total):
    """Log the player-held total and return how it moved since about a day ago (None with no
    snapshot that old). The daily post lands at 00:05, so yesterday's snapshot can be a few
    seconds short of 24h; anything 23h or older counts."""
    now = int(time.time())
    DatabaseManager.execute("INSERT INTO circulation_snapshots (timestamp, total_circulation) VALUES (?, ?)", (now, total))
    DatabaseManager.execute("DELETE FROM circulation_snapshots WHERE timestamp < ?", (now - 48 * 3600,))
    row = DatabaseManager.fetch_one(
        "SELECT total_circulation FROM circulation_snapshots WHERE timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
        (now - 23 * 3600,))
    return total - int(row[0]) if row else None


def _holdings_series(total, days=30):
    """[(date, player-held total)] from the daily balance snapshots, tipped with the live total."""
    bot = str(config.BOT_ID)
    snap_dir = getattr(config, "BALANCE_SNAPSHOT_DIR", "balance_snapshots")
    today = datetime.now(_UK).date()
    points = []
    for back in range(days, 0, -1):
        day = today - timedelta(days=back)
        try:
            with open(os.path.join(snap_dir, f"ukpence_balances_{day:%Y-%m-%d}.json"), "r") as f:
                data = json.load(f)
            points.append((day, sum(int(v) for uid, v in data.items() if uid != bot)))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    points.append((today, total))
    return points


def _week_flows(days=7):
    """{category: net amount that went to players} over the last week. Transfers are left out:
    they only move money between players."""
    from lib.economy.statement import _categorize
    rows = DatabaseManager.fetch_all(
        "SELECT amount, reason, counterparty_id FROM user_transactions WHERE ts >= ? AND user_id != ?",
        (int(time.time()) - days * 86400, str(config.BOT_ID))) or []
    net = {}
    for amount, reason, cp in rows:
        label = _categorize(reason, cp)[0]
        if label != "Transfers":
            net[label] = net.get(label, 0) + int(amount)
    return net


def _split_flows(net, floor=0.05):
    """(to_players, to_bank) as [(label, amount)], biggest first. Anything under 5% of all the
    money moved is merged into one band per side, so every band is tall enough to label."""
    to_players = sorted(((k, v) for k, v in net.items() if v > 0), key=lambda kv: -kv[1])
    to_bank = sorted(((k, -v) for k, v in net.items() if v < 0), key=lambda kv: -kv[1])
    grand = sum(v for _, v in to_players) + sum(v for _, v in to_bank)

    def merge(items, label):
        big = [(k, v) for k, v in items if v >= grand * floor]
        rest = sum(v for _, v in items if v < grand * floor)
        return big + ([(label, rest)] if rest else [])

    return merge(to_players, "Smaller payouts"), merge(to_bank, "Smaller sinks")


def _yesterday_payouts():
    path = getattr(config, "ECONOMY_METRICS_FILE", None)
    key = (datetime.now(_UK) - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        with open(path, "r") as f:
            metrics = json.load(f).get(key, {})
    except (OSError, ValueError, TypeError, AttributeError):
        return []
    out = []
    for label, metric in _PAYOUTS:
        try:
            amount = int(metrics.get(metric, 0) or 0)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            out.append((label, amount))
    return sorted(out, key=lambda p: -p[1])


def _chart_svg(series, w=944, h=200):
    if len(series) < 2:
        return "<div class='none' style='padding:12px 22px 6px'>Not enough history yet</div>"
    ml, mr, mt, mb = 64, 14, 12, 34
    vals = [v for _, v in series]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.15 or 1
    lo, hi = lo - pad, hi + pad
    pw, ph = w - ml - mr, h - mt - mb
    xy = [(ml + pw * i / (len(vals) - 1), mt + ph * (1 - (v - lo) / (hi - lo))) for i, v in enumerate(vals)]
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    o = []
    for k in range(4):
        y = mt + ph * (1 - k / 3)
        o.append(f"<line x1='{ml}' y1='{y:.1f}' x2='{ml + pw}' y2='{y:.1f}' class='grid'/>")
        o.append(f"<text x='{ml - 10}' y='{y + 6:.1f}' class='ylab'>{_fmt(lo + (hi - lo) * k / 3)}</text>")
    for i, anchor in ((0, "start"), (len(series) // 2, "middle"), (len(series) - 1, "end")):
        o.append(f"<text x='{xy[i][0]:.1f}' y='{h - 6}' class='xlab' text-anchor='{anchor}'>{series[i][0]:%-d %b}</text>")
    ex, ey = xy[-1]
    return (f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}'><defs><linearGradient id='af' x1='0' y1='0' x2='0' y2='1'>"
            "<stop offset='0' stop-color='#23A55A' stop-opacity='0.32'/><stop offset='1' stop-color='#23A55A' stop-opacity='0'/>"
            f"</linearGradient></defs>{''.join(o)}<path d='{line} L{ex:.1f},{mt + ph} L{xy[0][0]:.1f},{mt + ph} Z' fill='url(#af)'/>"
            f"<path d='{line}' fill='none' stroke='#3BD47A' stroke-width='3.5' stroke-linejoin='round'/>"
            f"<circle cx='{ex:.1f}' cy='{ey:.1f}' r='6' fill='#FFFFFF' stroke='#3BD47A' stroke-width='3'/></svg>")


def _flow_svg(to_players, to_bank, bank, players, supply, w=944, pool=150, span=240):
    """The bank on the left, players on the right, and this week's money as bands between them."""
    out, back = sum(v for _, v in to_players), sum(v for _, v in to_bank)
    lx, rx = 30, w - 30 - pool
    scale = span / (out + back)
    o, y = [], 70.0

    def bands(items):
        nonlocal y
        for label, amount in items:
            h = max(amount * scale, 26)
            o.append(f"<rect x='{lx + pool}' y='{y:.1f}' width='{rx - lx - pool}' height='{h:.1f}' "
                     f"fill='{_CAT_COLOURS.get(label, '#6B7280')}' opacity='0.55'/>")
            o.append(f"<text x='{w / 2}' y='{y + h / 2 + 6.5:.1f}' class='band'>{html.escape(label)} {_fmt(amount)}</text>")
            y += h + 6

    o.append(f"<text x='{w / 2}' y='58' class='dir' fill='#3BD47A'>PAID OUT {out:,} &#8594;</text>")
    bands(to_players)
    y += 44
    o.append(f"<text x='{w / 2}' y='{y - 14:.0f}' class='dir' fill='#FF6B6E'>&#8592; TAKEN BACK {back:,}</text>")
    bands(to_bank)
    bottom = y + 14
    ph = bottom - 40
    mid = 40 + ph / 2
    pools = []
    for x, name, colour, fill, amount in ((lx, "THE BANK", "#F0B232", "#2A2416", bank), (rx, "PLAYERS", "#3BD47A", "#10291B", players)):
        cx = x + pool / 2
        share = amount / supply * 100 if supply else 0
        pools.append(f"<rect x='{x}' y='40' width='{pool}' height='{ph:.0f}' fill='{fill}' stroke='{colour}' stroke-width='2'/>"
                     f"<text x='{cx}' y='26' class='pool' fill='{colour}'>{name}</text>"
                     f"<text x='{cx}' y='{mid + 11:.0f}' class='poolv'>{_fmt(amount)}</text>"
                     f"<text x='{cx}' y='{mid + 41:.0f}' class='pools'>{share:.0f}% of supply</text>")
    return f"<svg width='{w}' height='{bottom + 20:.0f}' viewBox='0 0 {w} {bottom + 20:.0f}'>{''.join(pools)}{''.join(o)}</svg>"


def _build_html(s, *, bank, change24, series, to_players, to_bank, payouts, richest, date_label):
    """The card. s: _summary(); richest: [(name, avatar data URI or None, balance)]."""
    players, supply = s["total"], s["total"] + bank
    pp = players / supply * 100 if supply else 0

    if change24 is None:
        chip = "<span class='chip flat'>No 24h data yet</span>"
    else:
        chip = f"<span class='chip {'up' if change24 >= 0 else 'down'}'>Players {_signed(change24)} in 24h</span>"

    month = series[-1][1] - series[0][1] if len(series) > 1 else 0
    tiles = "".join(
        f"<div class='tile'><div class='l' style='color:{c}'>{l}</div><div class='v num'>{v}</div><div class='s'>{sub}</div></div>"
        for l, v, sub, c in (
            ("Holders", f"{s['holders']:,}", f"{s['over_1k']:,} with 1k+", "#8C95FF"),
            ("Median", f"{int(s['median']):,}", "of balances over 250", "#3BD47A"),
            ("Top 5 hold", f"{sum(b for _, _, b in richest) / players * 100 if players else 0:.0f}%",
             f"{sum(b for _, _, b in richest):,} UKP", "#F0B232"),
            ("Inequality", f"{s['gini']:.2f}", "Gini · 1 = one person has it all", "#FF6B6E")))

    week = sum(v for _, v in to_players) - sum(v for _, v in to_bank)
    if to_players or to_bank:
        flow = _flow_svg(to_players, to_bank, bank, players, supply)
    else:
        flow = "<div class='none' style='padding:10px 22px 14px'>No money moved between the bank and players this week</div>"

    rows = []
    for i, (name, avatar, bal) in enumerate(richest):
        av = (f"<img class='av' src='{html.escape(avatar, quote=True)}'>" if avatar else
              f"<span class='av' style='background:{_AVATAR_COLOURS[i % 5]}'>{html.escape(name[:1].upper() or '?')}</span>")
        rows.append(f"<div class='tr'><span class='rk num' style='color:{['#F0B232', '#C0C6CF', '#C98B4E'][i] if i < 3 else '#949BA4'}'>{i + 1}</span>"
                    f"{av}<span class='nm'>{html.escape(name)}</span><span class='num bv'>{bal:,}</span></div>")
    btop = max(s["brackets"].values()) or 1
    brackets = "".join(f"<div class='br'><span>{k}</span><div class='bt'><div style='width:{v / btop * 100:.1f}%'></div></div>"
                       f"<span class='num'>{v:,}</span></div>" for k, v in s["brackets"].items())
    pays = "".join(f"<span class='pc'>{k} <b class='num'>{v:,}</b></span>" for k, v in payouts) or \
        "<span class='none'>Nothing paid out yesterday</span>"

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'><style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@500;600;700;800&family=Outfit:wght@700;800&family=Noto+Sans+Math&family=Noto+Sans+Symbols+2&family=Noto+Color+Emoji&display=swap');
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ background:#111214; }}
body {{ width:1000px; font-family:'Inter','Noto Sans Math','Noto Sans Symbols 2','Noto Color Emoji',sans-serif; color:#F2F3F5; -webkit-font-smoothing:antialiased; }}
.card {{ width:1000px; padding:28px; display:flex; flex-direction:column; gap:14px; background:#111214; }}
.num {{ font-family:'Outfit','Inter',sans-serif; font-weight:800; font-variant-numeric:tabular-nums; }}
.head {{ display:flex; align-items:center; justify-content:space-between; padding:4px 6px 10px; }}
.brand {{ display:flex; align-items:center; gap:16px; }}
.badge {{ width:60px; height:60px; border-radius:3px; background:#F0B232; display:flex; align-items:center; justify-content:center; }}
.roundel {{ width:34px; height:34px; border-radius:50%; background:radial-gradient(circle,#CF142B 0 30%,#fff 30% 54%,#00247D 54% 78%,#fff 78%); }}
.t {{ font-family:'Outfit',sans-serif; font-weight:800; font-size:34px; }} .st {{ font-size:19px; color:#B5BAC1; font-weight:500; margin-top:4px; }}
.chip {{ padding:7px 14px; border-radius:2px; font-weight:800; font-size:19px; border:1px solid; white-space:nowrap; }}
.chip.up {{ background:rgba(35,165,90,0.16); border-color:rgba(35,165,90,0.55); color:#3BD47A; }}
.chip.down {{ background:rgba(242,63,67,0.14); border-color:rgba(242,63,67,0.55); color:#FF6B6E; }}
.chip.flat {{ background:#1E1F22; border-color:#2B2D31; color:#B5BAC1; }}
.widget {{ background:#1E1F22; border:1px solid #2B2D31; border-radius:3px; }}
.wt {{ font-size:17px; font-weight:800; letter-spacing:.1em; color:#949BA4; }}
.supply {{ padding:20px 22px 18px; display:flex; flex-direction:column; gap:12px; }}
.sp {{ display:flex; height:30px; gap:3px; }} .sp div {{ display:flex; align-items:center; padding:0 12px; font-weight:800; font-size:17px; color:#0B1A10; white-space:nowrap; overflow:hidden; }}
.sl {{ display:flex; justify-content:space-between; font-size:20px; font-weight:700; }} .sl b {{ font-family:'Outfit',sans-serif; font-size:30px; }}
.chart, .flowbox {{ padding:16px 0 10px; }} .chart .wt, .flowbox .wt {{ padding:0 22px 4px; display:flex; justify-content:space-between; }}
.flowbox {{ padding-bottom:4px; }}
svg {{ display:block; }} .grid {{ stroke:#2B2D31; stroke-width:1; }}
.ylab {{ fill:#949BA4; font-size:16px; text-anchor:end; font-family:'Inter',sans-serif; }} .xlab {{ fill:#949BA4; font-size:16px; font-family:'Inter',sans-serif; }}
.band {{ fill:#F2F3F5; font-size:18px; font-weight:800; text-anchor:middle; font-family:'Inter',sans-serif; }}
.dir, .pool {{ font-size:16px; font-weight:800; letter-spacing:2px; text-anchor:middle; font-family:'Inter',sans-serif; }} .pool {{ font-size:17px; }}
.poolv {{ fill:#F2F3F5; font-size:32px; font-weight:800; text-anchor:middle; font-family:'Outfit',sans-serif; }}
.pools {{ fill:#B5BAC1; font-size:17px; font-weight:700; text-anchor:middle; font-family:'Inter',sans-serif; }}
.tiles {{ display:flex; gap:14px; }}
.tile {{ flex:1; background:#1E1F22; border:1px solid #2B2D31; border-radius:3px; padding:16px 18px; display:flex; flex-direction:column; gap:5px; }}
.tile .l {{ font-size:17px; font-weight:700; }} .tile .v {{ font-size:34px; line-height:38px; }} .tile .s {{ font-size:15px; color:#949BA4; font-weight:600; }}
.two {{ display:flex; gap:14px; }} .two > .widget {{ flex:1; min-width:0; padding:18px 22px; }}
.tr {{ display:flex; align-items:center; gap:14px; padding:9px 0; border-bottom:1px solid #2B2D31; }} .tr:last-child {{ border:none; }}
.rk {{ width:22px; font-size:22px; }}
.av {{ width:36px; height:36px; border-radius:50%; flex:none; object-fit:cover; display:flex; align-items:center; justify-content:center; font-family:'Outfit',sans-serif; font-weight:800; font-size:17px; }}
.nm {{ flex:1; min-width:0; font-size:20px; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }} .bv {{ font-size:22px; }}
.br {{ display:flex; align-items:center; gap:12px; padding:10px 0; font-size:19px; font-weight:700; }} .br span:first-child {{ width:96px; }}
.br .num {{ width:64px; text-align:right; font-size:21px; }}
.bt {{ flex:1; height:12px; background:#2B2D31; display:flex; }} .bt div {{ background:#5865F2; height:12px; }}
.pays {{ padding:18px 22px; display:flex; flex-direction:column; gap:12px; }} .pcs {{ display:flex; flex-wrap:wrap; gap:8px; }}
.pc {{ padding:7px 12px; background:#2B2D31; border-radius:2px; font-size:18px; font-weight:600; color:#B5BAC1; }} .pc b {{ color:#F2F3F5; font-size:19px; }}
.none {{ font-size:18px; color:#949BA4; }}
</style></head><body><div class='card'>
  <div class='head'><div class='brand'><div class='badge'><div class='roundel'></div></div>
    <div><div class='t'>UKPence Economy</div><div class='st'>{date_label}</div></div></div>{chip}</div>
  <div class='widget supply'><div class='wt'>THE {supply:,} UKP SUPPLY</div>
    <div class='sp'><div style='flex:{max(players, 1)};background:#3BD47A'>PLAYERS {pp:.0f}%</div><div style='flex:{max(bank, 1)};background:#F0B232'>BANK {100 - pp:.0f}%</div></div>
    <div class='sl'><span>Held by players <b>{players:,}</b></span><span>In the bank <b>{bank:,}</b></span></div></div>
  <div class='widget chart'><div class='wt'><span>HELD BY PLAYERS · 30 DAYS</span><span style='color:{'#3BD47A' if month >= 0 else '#FF6B6E'}'>{_signed(month)}</span></div>
    {_chart_svg(series)}</div>
  <div class='tiles'>{tiles}</div>
  <div class='widget flowbox'><div class='wt'><span>MONEY FLOW · LAST 7 DAYS</span><span style='color:{'#3BD47A' if week >= 0 else '#FF6B6E'}'>Players {_signed(week)}</span></div>
    {flow}</div>
  <div class='two'><div class='widget'><div class='wt' style='padding-bottom:6px'>RICHEST</div>{''.join(rows)}</div>
    <div class='widget'><div class='wt' style='padding-bottom:6px'>WEALTH BRACKETS</div>{brackets}</div></div>
  <div class='widget pays'><div class='wt'>YESTERDAY'S PAYOUTS · {sum(v for _, v in payouts):,} UKP</div><div class='pcs'>{pays}</div></div>
</div></body></html>"""


async def _richest(guild, client, top):
    """[(name, avatar data URI or None, balance)] for the top five, by server name where we can."""
    from lib.core.image_processing import get_avatar_data_uri

    async def one(uid, bal):
        user = guild.get_member(int(uid)) if guild else None
        if user is None and client is not None:
            user = client.get_user(int(uid))
        if user is None:
            return "Former member", None, bal
        avatar = None
        try:
            avatar = await get_avatar_data_uri(client, str(user.display_avatar.replace(size=128, static_format="png").url))
        except Exception:
            logger.debug("economy card avatar fetch failed", exc_info=True)
        return user.display_name, avatar, bal

    return list(await asyncio.gather(*(one(uid, bal) for uid, bal in top)))


async def create_economy_stats_image(guild: discord.Guild, client: discord.Client):
    """Render the economy card to a PNG BytesIO."""
    from lib.core.image_processing import screenshot_html
    from lib.economy.bank_manager import BankManager

    balances = await asyncio.to_thread(_player_balances)
    s = _summary(balances)
    bank = int(BankManager.get_balance())
    change24 = await asyncio.to_thread(_record_circulation, s["total"])
    series, net, payouts = await asyncio.gather(
        asyncio.to_thread(_holdings_series, s["total"]),
        asyncio.to_thread(_week_flows),
        asyncio.to_thread(_yesterday_payouts))
    to_players, to_bank = _split_flows(net)
    richest = await _richest(guild, client, s["top"])
    page = _build_html(s, bank=bank, change24=change24, series=series, to_players=to_players, to_bank=to_bank,
                       payouts=payouts, richest=richest, date_label=datetime.now(_UK).strftime("%-d %B %Y"))
    return await screenshot_html(page, size=(1000, 2000), element_selector=".card")
