"""Per-user 'balance over time' graph for /balance.

Two data sources, merged on a real time axis: the granular `balance_history` table (every
meaningful balance change, from any source - rewards, casino, pay, tax, etc., recorded at
the ledger chokepoints) for recent detail, plus the daily `balance_snapshots` for long-range
history from before granular logging existed, tipped with the live balance. Rendered as an
inline-SVG line chart (no charting deps) via the headless-Chrome pipeline, styled to match
the /ukpeconomy dashboard. Gated behind a button so /balance stays instant.
"""

import glob
import json
import logging
import os
from datetime import datetime

import discord
import pytz

import config
from lib.core.image_processing import screenshot_html
from lib.economy.economy_manager import get_bb

log = logging.getLogger(__name__)

_UK = pytz.timezone("Europe/London")
_PREFIX = "ukpence_balances_"
_MAX_POINTS = 300  # cap rendered points (downsampled) so the SVG stays light


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


def _load_points(user_id):
    """Merged, time-ordered [(ts, balance)] from daily snapshots + granular history, tipped
    with the live balance. Downsampled to _MAX_POINTS for rendering."""
    import time
    uid = str(user_id)
    pts = _snapshot_points(uid) + _history_points(uid)
    pts.sort(key=lambda p: p[0])
    now = int(time.time())
    current = int(get_bb(user_id))
    if not pts or pts[-1][0] < now - 30 or pts[-1][1] != current:
        pts.append((now, current))
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


def _build_html(display_name, points):
    W, H = 1040, 440
    ml, mr, mt, mb = 96, 48, 36, 70
    pw, ph = W - ml - mr, H - mt - mb
    vals = [b for _, b in points]
    vmin, vmax = min(vals), max(vals)
    if vmin == vmax:
        vmin = max(0, vmin - 1)
        vmax = vmax + 1
    pad = (vmax - vmin) * 0.10
    lo, hi = max(0, vmin - pad), vmax + pad
    if hi == lo:
        hi = lo + 1
    n = len(points)
    t0, t1 = points[0][0], points[-1][0]
    if t1 == t0:
        t1 = t0 + 1

    def px(ts):
        return ml + pw * (ts - t0) / (t1 - t0)

    def py(v):
        return mt + ph * (1 - (v - lo) / (hi - lo))

    pts = [(px(t), py(v)) for t, v in points]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = (f"M {ml:.1f},{mt + ph:.1f} "
            + " ".join(f"L {x:.1f},{y:.1f}" for x, y in pts)
            + f" L {ml + pw:.1f},{mt + ph:.1f} Z")

    rows = 4
    grid = []
    for k in range(rows + 1):
        gv = lo + (hi - lo) * k / rows
        gy = py(gv)
        grid.append(f"<line x1='{ml}' y1='{gy:.1f}' x2='{ml + pw}' y2='{gy:.1f}' class='grid'/>")
        grid.append(f"<text x='{ml - 14}' y='{gy + 6:.1f}' class='ylab'>{_fmt(gv)}</text>")

    def datelabel(ts):
        return datetime.fromtimestamp(ts, _UK).strftime("%-d %b")

    xlab = []
    for ts in (t0, (t0 + t1) // 2, t1):
        xlab.append(f"<text x='{px(ts):.1f}' y='{mt + ph + 38:.1f}' class='xlab'>{datelabel(ts)}</text>")

    markers = ""
    if n <= 30:
        markers = "".join(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3.5' class='dot'/>" for x, y in pts)
    ex, ey = pts[-1]
    end_dot = f"<circle cx='{ex:.1f}' cy='{ey:.1f}' r='6' class='enddot'/>"

    first, last = points[0][1], points[-1][1]
    net = last - first
    up = net > 0
    flat = net == 0
    color = "#10b981" if up else ("#ef4444" if not flat else "#3b82f6")
    arrow = "▲" if up else ("▼" if not flat else "▬")
    sign = "+" if net >= 0 else ""
    span = f"{datelabel(t0)} to {datelabel(t1)}"

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=Outfit:wght@600;800&display=swap');
* {{ margin:0; padding:0; box-sizing:border-box; }}
html,body {{ overflow:hidden; }}
::-webkit-scrollbar {{ width:0; height:0; }}
body {{ background:#0a0e1a; display:flex; align-items:center; justify-content:center;
        padding:16px; font-family:'Inter',sans-serif; }}
.card {{ width:{W}px; background:rgba(0,0,0,0.85); border:4px solid #CF142B; border-radius:20px;
         padding:28px 30px; box-shadow:0 16px 50px rgba(0,0,0,0.6); }}
.head {{ display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:6px; }}
.title {{ font-family:'Outfit',sans-serif; font-weight:800; color:#fff; font-size:30px;
          letter-spacing:0.5px; }}
.sub {{ color:rgba(255,255,255,0.45); font-size:14px; margin-top:6px; }}
.now {{ text-align:right; }}
.now .lab {{ color:rgba(255,255,255,0.45); font-size:13px; text-transform:uppercase;
             letter-spacing:1.5px; }}
.now .val {{ font-family:'Outfit',sans-serif; font-weight:800; color:#fff; font-size:38px; }}
.now .unit {{ color:rgba(255,255,255,0.4); font-size:16px; font-weight:600; }}
.delta {{ color:{color}; font-size:16px; font-weight:700; margin-top:2px; }}
svg {{ display:block; margin-top:10px; }}
.grid {{ stroke:rgba(255,255,255,0.08); stroke-width:1; }}
.ylab {{ fill:rgba(255,255,255,0.5); font-size:14px; text-anchor:end; font-family:'Inter',sans-serif; }}
.xlab {{ fill:rgba(255,255,255,0.5); font-size:14px; text-anchor:middle; font-family:'Inter',sans-serif; }}
.line {{ fill:none; stroke:{color}; stroke-width:3.5; stroke-linejoin:round; stroke-linecap:round;
         filter:drop-shadow(0 0 6px {color}88); }}
.dot {{ fill:{color}; }}
.enddot {{ fill:#fff; stroke:{color}; stroke-width:3; }}
</style></head><body>
<div class='card'>
  <div class='head'>
    <div>
      <div class='title'>{discord.utils.escape_markdown(display_name)[:28]}</div>
      <div class='sub'>Balance over time · {span}</div>
    </div>
    <div class='now'>
      <div class='lab'>Current</div>
      <div class='val'>{last:,}<span class='unit'> UKP</span></div>
      <div class='delta'>{arrow} {sign}{net:,} UKP</div>
    </div>
  </div>
  <svg width='{W}' height='{H}' viewBox='0 0 {W} {H}'>
    <defs>
      <linearGradient id='fill' x1='0' y1='0' x2='0' y2='1'>
        <stop offset='0%' stop-color='{color}' stop-opacity='0.35'/>
        <stop offset='100%' stop-color='{color}' stop-opacity='0'/>
      </linearGradient>
    </defs>
    {''.join(grid)}
    <path d='{area}' fill='url(#fill)'/>
    <polyline class='line' points='{poly}'/>
    {markers}{end_dot}
    {''.join(xlab)}
  </svg>
</div></body></html>"""


async def render_balance_graph(user_id, display_name):
    """Render the user's balance history to a PNG BytesIO, or None if there's <2 points."""
    points = _load_points(user_id)
    if len(points) < 2:
        return None
    html = _build_html(display_name, points)
    return await screenshot_html(html, size=(1120, 780), apply_trim=True)


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
            img = await render_balance_graph(self.target_id, self.target_name)
        except Exception:
            log.error("balance graph render failed", exc_info=True)
            img = None
        if img is None:
            await interaction.followup.send(
                "Not enough balance history yet - the graph builds up a point a day, "
                "so check back in a day or two.", ephemeral=True)
            return
        await interaction.followup.send(
            file=discord.File(img, filename="balance_graph.png"), ephemeral=True)
