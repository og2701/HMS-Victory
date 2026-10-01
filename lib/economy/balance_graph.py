"""Per-user 'balance over time' graph for /balance.

Two data sources, merged on a real time axis: the granular `balance_history` table (every
meaningful balance change, from any source - rewards, casino, pay, tax, etc., recorded at
the ledger chokepoints) for recent detail, plus the daily `balance_snapshots` for long-range
history from before granular logging existed, tipped with the live balance. Rendered as a
dark widget card (like the leaderboards) via the headless-Chrome pipeline: the balance line,
a bar per day (or hour / week) for what each one did, and where the money came from and went,
from the `user_transactions` ledger. Gated behind a button so /balance stays instant.
"""

import bisect
import glob
import html
import json
import logging
import os
from datetime import datetime, timedelta

import discord
import pytz

import config
from lib.economy.economy_manager import get_bb

log = logging.getLogger(__name__)

_UK = pytz.timezone("Europe/London")
_PREFIX = "ukpence_balances_"
_MAX_POINTS = 300  # cap rendered points (downsampled) so the SVG stays light
_DEFAULT_DAYS = 30  # the graph opens on the last 30 days; range buttons adjust it


def _midnight_epoch(date_str):
    y, m, d = map(int, date_str.split("-"))
    return int(_UK.localize(datetime(y, m, d)).timestamp())


def _snapshot_points(uid):
    """One (ts, balance) per daily snapshot, placed at end-of-day (the snapshot is the
    end-of-day ledger). Covers long-range history from before granular logging existed."""
    pts = []
    for path in glob.glob(os.path.join(config.BALANCE_SNAPSHOT_DIR, f"{_PREFIX}*.json")):
        date_str = os.path.basename(path)[len(_PREFIX):-len(".json")]
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception:
            continue
        if uid in data:
            try:
                pts.append((_midnight_epoch(date_str) + 86400, int(data[uid])))
            except (TypeError, ValueError):
                continue
    return pts


def _history_points(uid):
    """Granular (ts, balance) points: every meaningful balance change, from any source."""
    try:
        from database import DatabaseManager
        rows = DatabaseManager.fetch_all(
            "SELECT ts, balance FROM balance_history WHERE user_id = ? ORDER BY ts ASC", (uid,)) or []
        return [(int(ts), int(bal)) for ts, bal in rows]
    except Exception:
        log.debug("balance_history query failed", exc_info=True)
        return []


def _load_points(user_id, days=None):
    """Merged, time-ordered [(ts, balance)] from daily snapshots + granular history, tipped
    with the live balance. ``days`` limits to the last N days (None = all time); the last
    balance before the window is carried forward to the window's start so the line begins at
    the right level. Downsampled to _MAX_POINTS for rendering."""
    import time
    uid = str(user_id)
    pts = _snapshot_points(uid) + _history_points(uid)
    pts.sort(key=lambda p: p[0])
    now = int(time.time())
    current = int(get_bb(user_id))
    if not pts or pts[-1][0] < now - 30 or pts[-1][1] != current:
        pts.append((now, current))
    if days:
        cutoff = now - int(days) * 86400
        inside = [p for p in pts if p[0] >= cutoff]
        before = [p for p in pts if p[0] < cutoff]
        if before:
            # Anchor the window's left edge at the balance the user actually had then.
            inside = [(cutoff, before[-1][1])] + inside
        pts = inside
    if len(pts) > _MAX_POINTS:
        step = len(pts) / _MAX_POINTS
        keep = sorted({int(i * step) for i in range(_MAX_POINTS)} | {0, len(pts) - 1})
        pts = [pts[i] for i in keep]
    return pts


def _fmt(n):
    n = int(round(n))
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if abs(n) >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


_MINUS = "−"


def _signed(n):
    n = int(n)
    return f"+{n:,}" if n > 0 else (f"{_MINUS}{abs(n):,}" if n < 0 else "0")


_WINDOW_NAME = {1: "24 hours", 7: "7 days", 30: "30 days", 90: "90 days"}

# Category colours for the in/out bars (statement._categorize's labels)
_CAT_COLOURS = {
    "Casino": "#F472B6", "Pay": "#3BA3F5", "Rewards": "#3BD47A", "Benefits": "#2DD4BF",
    "Welcome": "#A3E635", "Predictions": "#A78BFA", "Shop": "#FB923C", "Games": "#FDE047",
    "Lottery": "#F59E0B", "Tax": "#F87171", "Bond": "#94A3B8", "Tree": "#84CC16",
    "Hall of Fame": "#FBBF24", "Ticket": "#22D3EE", "Admin": "#CBD5E1", "Other": "#6B7280",
}


def _rank(user_id):
    """(rank, holders) on the rich list, or (None, None) with nothing to rank."""
    try:
        from database import DatabaseManager
        row = DatabaseManager.fetch_one(
            "SELECT (SELECT COUNT(*) FROM ukpence WHERE balance > u.balance) + 1, "
            "(SELECT COUNT(*) FROM ukpence WHERE balance > 0) "
            "FROM ukpence u WHERE u.user_id = ? AND u.balance > 0", (str(user_id),))
        return (int(row[0]), int(row[1])) if row else (None, None)
    except Exception:
        log.debug("rich-list rank lookup failed", exc_info=True)
        return None, None


def _money_flow(user_id, start_ts, end_ts):
    """({category: amount in}, {category: amount out (negative)}) from the ledger. In and out
    are kept apart so a category you won and lost in shows both, and the totals add up."""
    try:
        from database import DatabaseManager
        from lib.economy.statement import _categorize
        rows = DatabaseManager.fetch_all(
            "SELECT amount, reason, counterparty_id FROM user_transactions "
            "WHERE user_id = ? AND ts >= ? AND ts <= ?", (str(user_id), int(start_ts), int(end_ts))) or []
    except Exception:
        log.debug("money flow query failed", exc_info=True)
        return {}, {}
    hide_tax = bool(getattr(config, "STATEMENT_HIDE_TAX", False))
    came_in, went_out = {}, {}
    for amount, reason, cp in rows:
        label, _emoji = _categorize(reason, cp)
        if hide_tax and label == "Tax":
            continue
        side = came_in if amount >= 0 else went_out
        side[label] = side.get(label, 0) + int(amount)
    return came_in, went_out


def _bucket_unit(t0, t1):
    span = t1 - t0
    if span <= 2 * 86400:
        return "hour"
    if span <= 120 * 86400:
        return "day"
    return "week"


def _bucket_edges(t0, t1, unit):
    """UK-local bucket boundaries covering [t0, t1]: hours, midnights, or Monday midnights."""
    if unit == "hour":
        start = t0 - t0 % 3600
        return list(range(start, t1 + 3600, 3600))
    first = datetime.fromtimestamp(t0, _UK).date()
    if unit == "week":
        first -= timedelta(days=first.weekday())
    step = timedelta(days=7 if unit == "week" else 1)
    edges, d = [], first
    while True:
        ts = int(_UK.localize(datetime(d.year, d.month, d.day)).timestamp())
        edges.append(ts)
        if ts > t1:
            return edges
        d += step


def _period_changes(points):
    """(unit, [(bucket start ts, change)]) - what each hour/day/week did to the balance."""
    t0, t1 = points[0][0], points[-1][0]
    unit = _bucket_unit(t0, t1)
    times = [t for t, _ in points]

    def balance_at(ts):
        i = bisect.bisect_right(times, ts) - 1
        return points[max(i, 0)][1]

    edges = _bucket_edges(t0, t1, unit)
    return unit, [(a, balance_at(b - 1) - balance_at(a - 1)) for a, b in zip(edges, edges[1:])]


def _bucket_label(ts, unit):
    d = datetime.fromtimestamp(ts, _UK)
    if unit == "hour":
        return d.strftime("%H:%M")
    if unit == "week":
        return "w/c " + d.strftime("%-d %b")
    return d.strftime("%-d %b")


def _line_svg(points, intraday):
    W, H = 944, 300
    ml, mr, mt, mb = 64, 16, 16, 40
    pw, ph = W - ml - mr, H - mt - mb
    vals = [b for _, b in points]
    vmin, vmax = min(vals), max(vals)
    if vmin == vmax:
        vmin, vmax = max(0, vmin - 1), vmax + 1
    pad = (vmax - vmin) * 0.10
    lo, hi = max(0, vmin - pad), vmax + pad
    if lo < (hi - lo) * 0.25:
        lo = 0          # near enough the floor - start at 0 rather than at an odd number like 99
    t0, t1 = points[0][0], points[-1][0]
    if t1 == t0:
        t1 = t0 + 1

    def px(ts):
        return ml + pw * (ts - t0) / (t1 - t0)

    def py(v):
        return mt + ph * (1 - (v - lo) / (hi - lo))

    xy = [(px(t), py(v)) for t, v in points]
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    area = line + f" L{xy[-1][0]:.1f},{mt + ph:.1f} L{xy[0][0]:.1f},{mt + ph:.1f} Z"
    o = []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = py(v)
        o.append(f"<line x1='{ml}' y1='{y:.1f}' x2='{ml + pw}' y2='{y:.1f}' class='grid'/>")
        o.append(f"<text x='{ml - 12}' y='{y + 6:.1f}' class='ylab'>{_fmt(v)}</text>")
    fmt = "%H:%M" if intraday else "%-d %b"
    for ts, anchor in ((t0, "start"), ((t0 + t1) // 2, "middle"), (t1, "end")):
        o.append(f"<text x='{px(ts):.1f}' y='{H - 8}' class='xlab' text-anchor='{anchor}'>"
                 f"{datetime.fromtimestamp(ts, _UK).strftime(fmt)}</text>")
    ex, ey = xy[-1]
    return (f"<svg width='{W}' height='{H}' viewBox='0 0 {W} {H}'><defs><linearGradient id='af' x1='0' y1='0' x2='0' y2='1'>"
            "<stop offset='0' stop-color='#23A55A' stop-opacity='0.32'/><stop offset='1' stop-color='#23A55A' stop-opacity='0'/>"
            f"</linearGradient></defs>{''.join(o)}<path d='{area}' fill='url(#af)'/>"
            f"<path d='{line}' fill='none' stroke='#3BD47A' stroke-width='3.5' stroke-linejoin='round' stroke-linecap='round'/>"
            f"<circle cx='{ex:.1f}' cy='{ey:.1f}' r='7' fill='#FFFFFF' stroke='#3BD47A' stroke-width='3.5'/></svg>")


def _bars_svg(changes):
    W, H, ml, mr = 944, 170, 64, 16
    pw = W - ml - mr
    up = max(max((c for _, c in changes), default=0), 1)
    down = max(-min((c for _, c in changes), default=0), 1)
    mid = 10 + (H - 20) * up / (up + down)
    bw = pw / max(len(changes), 1)
    gap = 2 if bw > 6 else 0.5
    bars = []
    for i, (_ts, c) in enumerate(changes):
        bh = max(abs(c) / (up + down) * (H - 20), 1.5)
        y = mid - bh if c >= 0 else mid
        colour = "#23A55A" if c > 0 else ("#F23F43" if c < 0 else "#4E5058")
        bars.append(f"<rect x='{ml + i * bw + gap:.1f}' y='{y:.1f}' width='{max(bw - 2 * gap, 1):.1f}' height='{bh:.1f}' fill='{colour}'/>")
    bars.append(f"<line x1='{ml}' y1='{mid:.1f}' x2='{ml + pw}' y2='{mid:.1f}' stroke='#4E5058' stroke-width='1.5'/>")
    return f"<svg width='{W}' height='{H}' viewBox='0 0 {W} {H}'>{''.join(bars)}</svg>"


def _flow_html(title, colour, flow, total, empty):
    if not flow:
        return (f"<div class='sl' style='color:{colour}'>{title} 0</div>"
                f"<div class='none'>{empty}</div>")
    items = sorted(flow.items(), key=lambda kv: -abs(kv[1]))
    segs = "".join(f"<div style='flex:{abs(v)};background:{_CAT_COLOURS.get(k, '#6B7280')}'></div>" for k, v in items)
    legend = "".join(f"<span class='lg'><i style='background:{_CAT_COLOURS.get(k, '#6B7280')}'></i>{html.escape(k)} "
                     f"<b class='num'>{_signed(v)}</b></span>" for k, v in items[:6])
    if len(items) > 6:
        legend += f"<span class='lg more'>+{len(items) - 6} more</span>"
    return (f"<div class='sl' style='color:{colour}'>{title} {_signed(total)}</div>"
            f"<div class='stack'>{segs}</div><div class='legend'>{legend}</div>")


def _build_html(display_name, points, *, days=None, avatar=None, rank=None, holders=None,
                came_in=None, went_out=None):
    """The card for one window. points: [(ts, balance)]; came_in / went_out: {category: amount}."""
    t0, t1 = points[0][0], points[-1][0]
    intraday = (t1 - t0) <= 2 * 86400
    first, last = points[0][1], points[-1][1]
    net = last - first
    window = _WINDOW_NAME.get(days, "all time") if days else "all time"
    if net > 0:
        chip = f"<span class='chip up'>&#9650; {_signed(net)} · {window}</span>"
    elif net < 0:
        chip = f"<span class='chip down'>&#9660; {_signed(net)} · {window}</span>"
    else:
        chip = f"<span class='chip flat'>No change · {window}</span>"
    if intraday:
        span = (f"{datetime.fromtimestamp(t0, _UK).strftime('%-d %b %H:%M')} to "
                f"{datetime.fromtimestamp(t1, _UK).strftime('%H:%M')}")
    else:
        span = (f"{datetime.fromtimestamp(t0, _UK).strftime('%-d %b')} to "
                f"{datetime.fromtimestamp(t1, _UK).strftime('%-d %b')}")

    name = html.escape(str(display_name)[:32])
    if avatar:
        av = f"<img class='av' src='{html.escape(avatar, quote=True)}'>"
    else:
        av = f"<div class='av'>{html.escape(str(display_name)[:1].upper() or '?')}</div>"
    badge = f"<span class='rk'>#{rank:,} RICHEST OF {holders:,}</span>" if rank and holders else ""

    unit, changes = _period_changes(points)
    best = max(changes, key=lambda x: x[1]) if changes else None
    worst = min(changes, key=lambda x: x[1]) if changes else None
    best_html = (f"<span style='color:#3BD47A'>Best {_bucket_label(best[0], unit)} {_signed(best[1])}</span>"
                 if best and best[1] > 0 else "<span></span>")
    worst_html = (f"<span style='color:#FF6B6E'>Worst {_bucket_label(worst[0], unit)} {_signed(worst[1])}</span>"
                  if worst and worst[1] < 0 else "<span></span>")

    came_in, went_out = came_in or {}, went_out or {}
    flows = (_flow_html("CAME IN", "#3BD47A", came_in, sum(came_in.values()), "Nothing came in")
             + "<div class='gap'></div>"
             + _flow_html("WENT OUT", "#FF6B6E", went_out, sum(went_out.values()), "Nothing went out"))

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'><style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@500;600;700;800&family=Outfit:wght@700;800&family=Noto+Sans+Math&family=Noto+Sans+Symbols+2&family=Noto+Color+Emoji&display=swap');
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ background:#111214; }}
body {{ width:1000px; font-family:'Inter','Noto Sans Math','Noto Sans Symbols 2','Noto Color Emoji',sans-serif; color:#F2F3F5; -webkit-font-smoothing:antialiased; }}
.card {{ width:1000px; padding:28px; display:flex; flex-direction:column; gap:14px; background:#111214; }}
.num {{ font-family:'Outfit','Inter',sans-serif; font-weight:800; font-variant-numeric:tabular-nums; }}
.head {{ display:flex; align-items:center; gap:20px; padding:4px 6px 10px; }}
.av {{ width:84px; height:84px; border-radius:50%; flex:none; object-fit:cover; background:#5865F2; display:flex; align-items:center;
       justify-content:center; font-family:'Outfit',sans-serif; font-weight:800; font-size:40px; box-shadow:0 0 0 4px #23A55A; }}
.who {{ max-width:470px; font-family:'Outfit','Noto Sans Math','Noto Sans Symbols 2','Noto Color Emoji',sans-serif; font-weight:800;
        font-size:34px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.rk {{ display:inline-block; margin-top:6px; padding:4px 12px; border-radius:2px; background:rgba(240,178,50,0.16); color:#FBDC94; font-weight:800; font-size:17px; }}
.big {{ margin-left:auto; text-align:right; }}
.big .v {{ font-size:56px; line-height:58px; white-space:nowrap; }} .big .u {{ font-size:22px; color:#949BA4; font-weight:600; }}
.chip {{ display:inline-block; margin-top:6px; padding:6px 14px; border-radius:2px; font-weight:800; font-size:19px; border:1px solid; white-space:nowrap; }}
.chip.up {{ background:rgba(35,165,90,0.16); border-color:rgba(35,165,90,0.55); color:#3BD47A; }}
.chip.down {{ background:rgba(242,63,67,0.14); border-color:rgba(242,63,67,0.55); color:#FF6B6E; }}
.chip.flat {{ background:#1E1F22; border-color:#2B2D31; color:#B5BAC1; }}
.widget {{ background:#1E1F22; border:1px solid #2B2D31; border-radius:3px; }}
.pad {{ padding:18px 0 12px; }} .wt {{ display:flex; justify-content:space-between; padding:0 22px 6px; font-size:17px; font-weight:800; letter-spacing:.1em; color:#949BA4; }}
.wt .span {{ letter-spacing:0; font-weight:600; }}
svg {{ display:block; }} .grid {{ stroke:#2B2D31; stroke-width:1; }}
.ylab {{ fill:#949BA4; font-size:17px; text-anchor:end; font-family:'Inter',sans-serif; }} .xlab {{ fill:#949BA4; font-size:17px; font-family:'Inter',sans-serif; }}
.row2 {{ display:flex; justify-content:space-between; padding:2px 22px 4px; font-size:18px; font-weight:700; }}
.sec {{ padding:18px 22px 20px; display:flex; flex-direction:column; gap:12px; }}
.sl {{ font-size:17px; font-weight:800; letter-spacing:.08em; }} .gap {{ height:4px; }}
.stack {{ display:flex; height:22px; overflow:hidden; gap:2px; }}
.legend {{ display:flex; flex-wrap:wrap; gap:8px 20px; }}
.lg {{ display:flex; align-items:center; gap:8px; font-size:18px; font-weight:600; color:#B5BAC1; }}
.lg i {{ width:12px; height:12px; }} .lg b {{ color:#F2F3F5; font-size:19px; }} .lg.more {{ color:#949BA4; }}
.none {{ font-size:18px; color:#949BA4; }}
</style></head><body><div class='card'>
  <div class='head'>{av}<div><div class='who'>{name}</div>{badge}</div>
    <div class='big'><div class='v num'>{last:,}<span class='u'> UKP</span></div>{chip}</div></div>
  <div class='widget pad'><div class='wt'><span>BALANCE</span><span class='span'>{span}</span></div>{_line_svg(points, intraday)}</div>
  <div class='widget pad'><div class='wt'><span>EACH {unit.upper()}</span></div>{_bars_svg(changes)}
    <div class='row2'>{best_html}{worst_html}</div></div>
  <div class='widget sec'>{flows}</div>
</div></body></html>"""


async def render_balance_graph(user_id, display_name, days=None, *, client=None, avatar_url=None):
    """Render the user's balance card (last ``days``, or all time) to a PNG BytesIO,
    or None if there are <2 points in the window."""
    import asyncio
    import time
    points = _load_points(user_id, days=days)
    if len(points) < 2:
        return None
    start = points[0][0] if days else 0
    (rank, holders), (came_in, went_out) = await asyncio.gather(
        asyncio.to_thread(_rank, user_id),
        asyncio.to_thread(_money_flow, user_id, start, int(time.time())))
    # Deferred like image_host below: pulling this in at module scope drags the whole
    # headless-browser stack into anything that only wants the point maths.
    from lib.core.image_processing import get_avatar_data_uri, screenshot_html
    avatar = None
    if avatar_url:
        try:
            avatar = await get_avatar_data_uri(client, avatar_url)
        except Exception:
            log.debug("balance card avatar fetch failed", exc_info=True)
    page = _build_html(display_name, points, days=days, avatar=avatar, rank=rank, holders=holders,
                       came_in=came_in, went_out=went_out)
    return await screenshot_html(page, size=(1000, 1400), element_selector=".card")


def _avatar_url(interaction, user_id):
    """The member's avatar (server one if set), small - or None if we can't see them."""
    user = None
    if interaction.guild is not None:
        user = interaction.guild.get_member(int(user_id))
    if user is None:
        user = interaction.client.get_user(int(user_id))
    if user is None:
        return None
    try:
        return str(user.display_avatar.replace(size=128, static_format="png").url)
    except Exception:
        return None


class _UserLookupSelect(discord.ui.UserSelect):
    """Native searchable member picker. Owner-only; selecting a member shows their balance
    (with its own graph button) in a fresh ephemeral reply, so lookups can be chained."""

    def __init__(self, viewer_id):
        super().__init__(placeholder="Look up another member's balance...",
                         min_values=1, max_values=1)
        self.viewer_id = int(viewer_id)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("That isn't for you.", ephemeral=True)
            return
        member = self.values[0]
        balance = int(get_bb(member.id))
        await interaction.response.send_message(
            f"💷 **{member.display_name}** has **{balance:,} UKPence**.",
            view=BalanceGraphView(member.id, member.display_name, self.viewer_id, owner_search=True),
            ephemeral=True,
        )


class BalanceGraphView(discord.ui.View):
    """A 'Show balance graph' button for the ephemeral /balance reply. `target` is whose
    balance the button graphs; `viewer` is who's allowed to press it (the two differ when
    the owner is looking someone else up). With owner_search, also attaches a member picker."""

    def __init__(self, target_id, target_name, viewer_id, *, owner_search=False):
        super().__init__(timeout=300)
        self.target_id = int(target_id)
        self.target_name = target_name
        self.viewer_id = int(viewer_id)
        if owner_search:
            self.add_item(_UserLookupSelect(self.viewer_id))

    @discord.ui.button(label="Show balance graph", emoji="\U0001f4c8",
                       style=discord.ButtonStyle.secondary)
    async def show_graph(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("That isn't for you.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            img = await render_balance_graph(self.target_id, self.target_name, days=_DEFAULT_DAYS,
                                             client=interaction.client,
                                             avatar_url=_avatar_url(interaction, self.target_id))
        except Exception:
            log.error("balance graph render failed", exc_info=True)
            img = None
        if img is None:
            await interaction.followup.send(
                "Not enough balance history yet - the graph builds up a point a day, "
                "so check back in a day or two.", ephemeral=True)
            return
        view = BalanceGraphRangeView(self.target_id, self.target_name, self.viewer_id,
                                     selected_days=_DEFAULT_DAYS)
        # Hosted embed rather than an attachment: Discord is slow to load attachments on
        # ephemeral messages (see lib/core/image_host.py).
        from lib.core.image_host import as_embed_or_file
        embed, files = await as_embed_or_file(
            interaction.client, img, f"balance_{_DEFAULT_DAYS}d.png")
        await interaction.followup.send(embed=embed, files=files, view=view,
                                        ephemeral=True)

    @discord.ui.button(label="Statement", emoji="\U0001f9fe",
                       style=discord.ButtonStyle.secondary)
    async def show_statement(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("That isn't for you.", ephemeral=True)
            return
        from lib.economy.statement import build_statement_view
        view = build_statement_view(
            target_id=self.target_id, target_name=self.target_name,
            viewer_id=self.viewer_id, offset=1, client=interaction.client)
        await interaction.response.send_message(view=view, ephemeral=True)


class BalanceGraphRangeView(discord.ui.View):
    """Range buttons (24H / 7D / 30D / 90D / All) under a rendered balance graph. Each
    re-renders the graph for that window and edits the message in place; the active range is
    highlighted. Only the original viewer can press them.

    24H leans on the granular `balance_history` rows - the daily snapshots alone would give
    it a single point - so it's only as detailed as that table, which is to say every real
    balance change since granular logging came in."""

    RANGES = [("24H", 1), ("7D", 7), ("30D", 30), ("90D", 90), ("All", None)]

    def __init__(self, target_id, target_name, viewer_id, *, selected_days=_DEFAULT_DAYS):
        super().__init__(timeout=300)
        self.target_id = int(target_id)
        self.target_name = target_name
        self.viewer_id = int(viewer_id)
        for label, days in self.RANGES:
            style = (discord.ButtonStyle.primary if days == selected_days
                     else discord.ButtonStyle.secondary)
            btn = discord.ui.Button(label=label, style=style)
            btn.callback = self._make_cb(days)
            self.add_item(btn)

    def _make_cb(self, days):
        async def _cb(interaction: discord.Interaction):
            await self._render_range(interaction, days)
        return _cb

    async def _render_range(self, interaction: discord.Interaction, days):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("That isn't for you.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            img = await render_balance_graph(self.target_id, self.target_name, days=days,
                                             client=interaction.client,
                                             avatar_url=_avatar_url(interaction, self.target_id))
        except Exception:
            log.error("balance graph range render failed", exc_info=True)
            img = None
        if img is None:
            await interaction.followup.send(
                "Not enough balance history in that range.", ephemeral=True)
            return
        view = BalanceGraphRangeView(self.target_id, self.target_name, self.viewer_id,
                                     selected_days=days)
        from lib.core.image_host import as_embed_or_file
        embed, files = await as_embed_or_file(
            interaction.client, img, f"balance_{days or 'all'}d.png")
        await interaction.edit_original_response(embed=embed, attachments=files, view=view)
