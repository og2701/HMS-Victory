"""Per-user 'bank statement' for /balance, gated behind a button next to the graph.

Reads the durable `user_transactions` ledger (every signed money move, with the reason that
already flows through the economy chokepoints - see economy_manager.record_transaction) and
renders a month at a time as a Components V2 card. Casino activity is collapsed to one net
line per day, and repeated identical reward lines (e.g. chat activity drips) collapse into
one per-day line with a count; everything else is itemised. Navigation buttons walk months: Previous (further
back, repeatable), This month (current month-to-date), Next (forward again).

Defaults to the last completed month when first opened.
"""

import logging
from collections import OrderedDict
from datetime import datetime

import discord
import pytz

from database import DatabaseManager
from lib.economy.economy_manager import get_bb

log = logging.getLogger(__name__)

_UK = pytz.timezone("Europe/London")
_MAX_OFFSET = 12          # how many months back the nav allows (matches retention)
_MAX_LINES = 40           # itemised lines before older entries roll into the summary
_SEP = discord.SeparatorSpacing.small

# Reason -> (label, emoji). Order matters: first substring hit wins. Pay is detected by the
# presence of a counterparty id, ahead of any text match.
_CATEGORIES = [
    ("Casino", "\U0001f3b0", ["blackjack", "roulette", "slots", "slot ", "video poker",
                               "vpoker", "videopoker", "red dog", "reddog", "three card",
                               "three-card", "tcp", "hold'em", "holdem", "poker", "higher",
                               "lower", "baccarat", "casino"]),
    ("Predictions", "\U0001f52e", ["prediction", "wager"]),
    ("Lottery", "\U0001f3ab", ["lottery"]),
    ("Rewards", "\U0001f4ac", ["chat", "stage", "booster", "top chatter", "activity",
                               "message reward", "reward", "daily"]),
    ("Benefits", "\U0001f9fe", ["benefit", "dole"]),
    ("Tree", "\U0001f333", ["tree", "water"]),
    ("Hall of Fame", "\U0001f3c6", ["hall of fame", "hof"]),
    ("Ticket", "\U0001f3ab", ["ticket"]),
    ("Tax", "\U0001f4c9", ["tax", "inactivity"]),
    ("Shop", "\U0001f6d2", ["shop", "purchase", "bought", "restock"]),
    ("Bond", "\U0001f3e6", ["bond"]),
    ("Welcome", "\U0001f44b", ["welcome"]),
    ("Admin", "⚖️", ["balance set", "admin", "manual", "unspecified"]),
]


def _categorize(reason, counterparty_id=None):
    if counterparty_id:
        return ("Pay", "\U0001f501")
    r = (reason or "").lower()
    for label, emoji, subs in _CATEGORIES:
        if any(s in r for s in subs):
            return (label, emoji)
    return ("Other", "\U0001f4b7")


def _month_bounds(offset):
    """Return (start_ts, end_ts, start_dt) for the UK-calendar month `offset` months back."""
    now = datetime.now(_UK)
    month = now.month - offset
    year = now.year
    while month <= 0:
        month += 12
        year -= 1
    start = _UK.localize(datetime(year, month, 1))
    if month == 12:
        nxt = _UK.localize(datetime(year + 1, 1, 1))
    else:
        nxt = _UK.localize(datetime(year, month + 1, 1))
    return int(start.timestamp()), int(nxt.timestamp()), start


def _sign(n):
    return f"+{n:,}" if n >= 0 else f"−{abs(n):,}"


def _name(client, cp_id):
    from config import BOT_ID
    if str(cp_id) == str(BOT_ID):
        return "HMS Victory"
    try:
        u = client.get_user(int(cp_id))
        if u:
            return discord.utils.escape_markdown(u.display_name)[:24]
    except Exception:
        pass
    return f"<@{cp_id}>"


def _describe(reason, cp_id, amount, client):
    if cp_id:
        return f"Pay {'→' if amount < 0 else '←'} {_name(client, cp_id)}"
    r = (reason or "Unspecified").strip()
    return (r[:1].upper() + r[1:])[:42]


def _gather(uid, start_ts, end_ts, client):
    """Fetch the month's rows and build (sorted display entries, totals, breakdown)."""
    rows = DatabaseManager.fetch_all(
        "SELECT ts, amount, balance_after, reason, counterparty_id FROM user_transactions "
        "WHERE user_id = ? AND ts >= ? AND ts < ? ORDER BY ts ASC",
        (uid, start_ts, end_ts)) or []

    casino_by_day = OrderedDict()       # 'YYYY-MM-DD' -> [net, count, first_ts]
    rewards_by_day = OrderedDict()      # ('YYYY-MM-DD', desc) -> [net, count, first_ts, emoji]
    entries = []                        # (ts, emoji, desc, amount)
    breakdown = OrderedDict()           # label -> net
    total_in = total_out = 0

    for ts, amount, _bal, reason, cp in rows:
        amount = int(amount)
        label, emoji = _categorize(reason, cp)
        breakdown[label] = breakdown.get(label, 0) + amount
        if amount >= 0:
            total_in += amount
        else:
            total_out += -amount
        if label == "Casino":
            day = datetime.fromtimestamp(ts, _UK).strftime("%Y-%m-%d")
            agg = casino_by_day.setdefault(day, [0, 0, ts])
            agg[0] += amount
            agg[1] += 1
        elif label == "Rewards":
            day = datetime.fromtimestamp(ts, _UK).strftime("%Y-%m-%d")
            desc = _describe(reason, cp, amount, client)
            agg = rewards_by_day.setdefault((day, desc), [0, 0, ts, emoji])
            agg[0] += amount
            agg[1] += 1
        else:
            entries.append((ts, emoji, _describe(reason, cp, amount, client), amount))

    for _day, (net, count, first_ts) in casino_by_day.items():
        plays = f"{count} play" + ("" if count == 1 else "s")
        entries.append((first_ts, "\U0001f3b0", f"Casino ({plays})", net))

    for (_day, desc), (net, count, first_ts, emoji) in rewards_by_day.items():
        if count > 1:
            desc = f"{desc} (×{count})"
        entries.append((first_ts, emoji, desc, net))

    entries.sort(key=lambda e: e[0])
    return rows, entries, total_in, total_out, breakdown


def _snapshots(uid):
    """Sorted (ts, balance) end-of-day snapshot points - exact balances for month boundaries."""
    try:
        from lib.economy.balance_graph import _snapshot_points
        return sorted(_snapshot_points(uid))
    except Exception:
        return []


def _balance_at(boundary_ts, snaps):
    """The latest snapshot balance at or before a month boundary, or None if none exists."""
    cand = [b for ts, b in snaps if ts <= boundary_ts]
    return cand[-1] if cand else None


def _live_balance_before(uid, ts):
    """Last real running balance (live rows only) before ts. Backfilled rows carry no
    running balance, so they're excluded - the caller shows '—' when this is None."""
    row = DatabaseManager.fetch_one(
        "SELECT balance_after FROM user_transactions WHERE user_id = ? AND ts < ? "
        "AND source = 'live' ORDER BY ts DESC LIMIT 1", (uid, ts))
    return int(row[0]) if row else None


def build_statement_view(*, target_id, target_name, viewer_id, offset, client):
    """Build the Components V2 statement card for one month (offset months back)."""
    uid = str(target_id)
    start_ts, end_ts, start_dt = _month_bounds(offset)
    period = start_dt.strftime("%B %Y")

    rows, entries, total_in, total_out, breakdown = _gather(uid, start_ts, end_ts, client)

    # Opening/closing prefer the exact end-of-day balance snapshots so backfilled months
    # (whose reconstructed rows lack a running balance) still reconcile. Fall back to the
    # ledger's own balance_after when no snapshot brackets the boundary.
    snaps = _snapshots(uid)
    snap_open = _balance_at(start_ts, snaps)
    opening = snap_open if snap_open is not None else _live_balance_before(uid, start_ts)
    if offset == 0:                          # current month: closing is the live balance
        closing = int(get_bb(uid))
        snap_close = closing
    else:
        snap_close = _balance_at(end_ts, snaps)
        closing = snap_close if snap_close is not None else _live_balance_before(uid, end_ts)

    # Residual: whatever the itemised entries don't explain (historical rewards/tax/benefits
    # that were never stored per-user). Only trustworthy when both boundaries are real
    # snapshots, so it stays 0 for fully-itemised live months and surfaces the rest otherwise.
    residual = 0
    if snap_open is not None and snap_close is not None:
        residual = (closing - opening) - (total_in - total_out)
    if residual:
        breakdown["Rewards & other"] = breakdown.get("Rewards & other", 0) + residual
        if residual > 0:
            total_in += residual
        else:
            total_out += -residual
    net = total_in - total_out

    accent = 0x10B981 if net > 0 else (0xEF4444 if net < 0 else 0x3B82F6)

    # Body: itemised lines, newest kept if we overflow the cap.
    lines, hidden = [], 0
    if entries:
        shown = entries
        if len(entries) > _MAX_LINES:
            hidden = len(entries) - _MAX_LINES
            shown = entries[-_MAX_LINES:]
        for ts, emoji, desc, amount in shown:
            date = datetime.fromtimestamp(ts, _UK).strftime("%d %b")
            lines.append(f"`{date}`  {emoji} {desc} · **{_sign(amount)}**")
    if residual:
        lines.append(f"\U0001f4ac Rewards & other (net) · **{_sign(residual)}**")
    body = "\n".join(lines) if lines else "_No transactions this period._"
    if hidden:
        body = f"-# {hidden} earlier entries rolled into the totals below\n" + body
    body = body[:3800]

    bd = " · ".join(f"{_emoji_for(label)} {label} {_sign(net)}"
                        for label, net in breakdown.items() if net)
    known = opening is not None and closing is not None
    open_str = f"{opening:,}" if opening is not None else "—"
    if known:
        summary = (
            f"In **{_sign(total_in)}** · Out **{_sign(-total_out)}** · Net **{_sign(net)}**\n"
            f"Closing balance · **{closing:,}** UKP"
        )
    else:
        summary = (
            f"In **{_sign(total_in)}** · Out **{_sign(-total_out)}** · "
            f"Itemised total **{_sign(net)}**\n"
            f"-# Opening/closing balance isn't recorded this far back."
        )
    if bd:
        summary += f"\n-# {bd}"

    name = discord.utils.escape_markdown(target_name)[:28]
    view = discord.ui.LayoutView(timeout=300)
    c = discord.ui.Container(accent_colour=accent)
    c.add_item(discord.ui.TextDisplay("## \U0001f9fe HMS Victory Bank — Statement"))
    c.add_item(discord.ui.TextDisplay(f"**{name}** · {period}"))
    c.add_item(discord.ui.Separator(visible=True, spacing=_SEP))
    c.add_item(discord.ui.TextDisplay(f"Opening balance · **{open_str}** UKP"))
    c.add_item(discord.ui.Separator(visible=False, spacing=_SEP))
    c.add_item(discord.ui.TextDisplay(body))
    c.add_item(discord.ui.Separator(visible=True, spacing=_SEP))
    c.add_item(discord.ui.TextDisplay(summary))
    c.add_item(_StatementNav(target_id, target_name, viewer_id, offset))
    view.add_item(c)
    return view


def _emoji_for(label):
    if label == "Pay":
        return "\U0001f501"
    if label == "Rewards & other":
        return "\U0001f4ac"
    if label == "Other":
        return "\U0001f4b7"
    for lbl, emoji, _subs in _CATEGORIES:
        if lbl == label:
            return emoji
    return "\U0001f4b7"


class _StatementNav(discord.ui.ActionRow):
    """Month navigation. Rebuilds the whole card for the new offset and edits in place."""

    def __init__(self, target_id, target_name, viewer_id, offset):
        super().__init__()
        self.target_id = int(target_id)
        self.target_name = target_name
        self.viewer_id = int(viewer_id)
        self.offset = int(offset)
        self.previous.disabled = self.offset >= _MAX_OFFSET
        self.this_month.disabled = self.offset == 0
        self.next_month.disabled = self.offset == 0

    async def _go(self, interaction, new_offset):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("That isn't for you.", ephemeral=True)
            return
        view = build_statement_view(
            target_id=self.target_id, target_name=self.target_name,
            viewer_id=self.viewer_id, offset=new_offset, client=interaction.client)
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="Previous month", emoji="◀",
                       style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, min(_MAX_OFFSET, self.offset + 1))

    @discord.ui.button(label="This month", style=discord.ButtonStyle.primary)
    async def this_month(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, 0)

    @discord.ui.button(label="Next month", emoji="▶",
                       style=discord.ButtonStyle.secondary)
    async def next_month(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, max(0, self.offset - 1))
