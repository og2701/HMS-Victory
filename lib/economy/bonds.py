"""Bonds: fixed-term UKPence savings.

A player locks UKP for a set term; the principal is held in the Server Bank while locked,
and on maturity the bank repays principal + interest. Everything lives in the ``bonds``
table, and maturity is driven by a periodic scan (lib/bot/scheduled_tasks) rather than
in-memory timers - so it's fully restart-safe: an overdue bond simply pays out on the
next tick after the bot comes back.

Economy: opening a bond is remove_bb(to_bank) (principal -> bank); maturity / early exit
pays from the bank via add_bb(taxable=False). The bank nets -interest per matured bond,
which is the intended drain of the over-full bank back to savers. One active bond per user.
"""

import logging
import time

import discord
from discord import Interaction

import config
from database import DatabaseManager
from lib.economy.economy_manager import add_bb, remove_bb, get_bb

log = logging.getLogger(__name__)
ACCENT = discord.Colour(0x1C6B46)

_COLS = "id, user_id, principal, rate_pct, term_days, opened_ts, matures_ts, status"
_KEYS = [c.strip() for c in _COLS.split(",")]


def _row_to_bond(r):
    return dict(zip(_KEYS, r)) if r else None


def interest_for(principal: int, rate_pct: int) -> int:
    return int(principal) * int(rate_pct) // 100


def get_active(user_id):
    r = DatabaseManager.fetch_one(
        f"SELECT {_COLS} FROM bonds WHERE user_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
        (str(user_id),))
    return _row_to_bond(r)


def active_bond_principal(user_id) -> int:
    """Total principal a user has locked in active bonds (counts toward /benefits wealth)."""
    r = DatabaseManager.fetch_one(
        "SELECT COALESCE(SUM(principal), 0) FROM bonds WHERE user_id = ? AND status = 'active'",
        (str(user_id),))
    return int(r[0]) if r else 0


def _credit(uid, amount, reason):
    return add_bb(int(uid), int(amount), reason=reason, taxable=False)


def _recent_received(uid, days) -> int:
    """Total UKP /pay'd TO this user in the last ``days`` (funnelled-in capital)."""
    cutoff = int(time.time()) - days * 86400
    try:
        r = DatabaseManager.fetch_one(
            "SELECT COALESCE(SUM(amount),0) FROM pay_transfers WHERE recipient_id = ? AND timestamp > ?",
            (str(uid), cutoff))
        return int(r[0]) if r else 0
    except Exception:
        return 0


def open_bond(user_id, principal, term_days):
    """Lock ``principal`` for ``term_days``. Returns (bond, error_message)."""
    terms = getattr(config, "BOND_TERMS", {3: 2, 7: 6, 30: 30})
    if term_days not in terms:
        return None, "That isn't a valid bond term."
    if get_active(user_id):
        return None, "You already have an active bond - you can only hold one at a time."
    mx = getattr(config, "BOND_MAX", 5000)
    if principal < 1:
        return None, "Enter a positive amount of UKPence."
    if principal > mx:
        return None, f"The maximum per bond is {mx:,} UKPence."
    bal = get_bb(user_id)
    if bal < principal:
        return None, f"You don't have {principal:,} UKPence to lock."
    # Anti-funnel: UKP you've recently been /pay'd doesn't count as your own capital to
    # bond, so a big holder can't push 5k to alts/friends and have them invest it past the
    # per-person cap.
    received = _recent_received(user_id, getattr(config, "BOND_FUNNEL_LOOKBACK_DAYS", 3))
    own = max(0, bal - received)
    if principal > own:
        days = getattr(config, "BOND_FUNNEL_LOOKBACK_DAYS", 3)
        return None, (
            f"You can only bond **{own:,} UKPence** of your own. **{received:,}** arrived from "
            f"other users in the last {days} day(s) and can't be locked in a bond (anti-funnel). "
            f"Wait for it to age out, or bond a smaller amount.")
    if not remove_bb(int(user_id), int(principal), reason="Bond deposit"):
        return None, "You don't have enough UKPence."
    now = int(time.time())
    DatabaseManager.execute_insert(
        "INSERT INTO bonds (user_id, principal, rate_pct, term_days, opened_ts, matures_ts, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'active')",
        (str(user_id), int(principal), int(terms[term_days]), int(term_days), now, now + term_days * 86400))
    return get_active(user_id), None


def withdraw_early(user_id):
    """Refund principal minus the early-exit penalty (interest forfeited).
    Returns (refund, penalty, error_message)."""
    g = get_active(user_id)
    if not g:
        return 0, 0, "You don't have an active bond."
    pen_pct = getattr(config, "BOND_EARLY_PENALTY_PCT", 10)
    penalty = g["principal"] * pen_pct // 100
    refund = g["principal"] - penalty
    # Close it first so a retry can't double-refund.
    DatabaseManager.execute("UPDATE bonds SET status = 'withdrawn' WHERE id = ? AND status = 'active'", (g["id"],))
    _credit(user_id, refund, "Bond early withdrawal")
    return refund, penalty, None


async def mature_due(client):
    """Pay out every matured active bond (principal + interest) and DM the holder.
    Idempotent: each bond is flipped to 'matured' before it's paid, so a re-run or a
    crash can't double-pay."""
    now = int(time.time())
    rows = DatabaseManager.fetch_all(
        f"SELECT {_COLS} FROM bonds WHERE status = 'active' AND matures_ts <= ?", (now,)) or []
    for r in rows:
        g = _row_to_bond(r)
        interest = interest_for(g["principal"], g["rate_pct"])
        payout = g["principal"] + interest
        DatabaseManager.execute("UPDATE bonds SET status = 'matured' WHERE id = ? AND status = 'active'", (g["id"],))
        _credit(g["user_id"], payout, "Bond maturity")
        try:
            user = client.get_user(int(g["user_id"])) or await client.fetch_user(int(g["user_id"]))
            await user.send(
                f"\U0001f3e6 Your **{g['term_days']}-day bond** has matured! You get back "
                f"**{payout:,} UKPence** (your {g['principal']:,} principal + {interest:,} interest). Tidy."
            )
        except Exception:
            log.debug("bond maturity DM failed", exc_info=True)


# ---------------------------------------------------------------------------
# Panels (text only - no image render, so no interaction-expiry risk)
# ---------------------------------------------------------------------------
def _term_lines() -> str:
    terms = getattr(config, "BOND_TERMS", {3: 2, 7: 6, 30: 30})
    return "\n".join(f"- **{d} days** - {p}% return" for d, p in sorted(terms.items()))


def build_open_panel(user_id) -> discord.ui.LayoutView:
    bal = get_bb(user_id)
    mx = getattr(config, "BOND_MAX", 5000)
    pen = getattr(config, "BOND_EARLY_PENALTY_PCT", 10)
    view = discord.ui.LayoutView(timeout=300)
    box = discord.ui.Container(accent_colour=ACCENT)
    box.add_item(discord.ui.TextDisplay(
        "## \U0001f3e6 UKPence Bonds - Treasury Savings\n"
        "Lock your UKPence for a fixed term and earn interest, paid from the Server Bank.\n"
        f"Your balance: **{bal:,} UKPence**\n\n"
        f"{_term_lines()}\n\n"
        f"-# Max **{mx:,}** per bond, one at a time. Break early and you forfeit the interest "
        f"plus a {pen}% penalty on your principal."
    ))
    view.add_item(box)
    row = discord.ui.ActionRow()
    for d, p in sorted(getattr(config, "BOND_TERMS", {3: 2, 7: 6, 30: 30}).items()):
        b = discord.ui.Button(label=f"{d}d · {p}%", style=discord.ButtonStyle.success,
                              custom_id=f"bond:open:{user_id}:{d}")
        b.callback = _make_open_cb(user_id, d)
        row.add_item(b)
    view.add_item(row)
    return view


def build_status_panel(user_id, bond, *, note=None) -> discord.ui.LayoutView:
    interest = interest_for(bond["principal"], bond["rate_pct"])
    payout = bond["principal"] + interest
    pen = getattr(config, "BOND_EARLY_PENALTY_PCT", 10)
    penalty = bond["principal"] * pen // 100
    view = discord.ui.LayoutView(timeout=300)
    box = discord.ui.Container(accent_colour=ACCENT)
    txt = (
        "## \U0001f3e6 Your Bond\n"
        f"Locked: **{bond['principal']:,} UKPence**\n"
        f"Term: **{bond['term_days']} days** @ **{bond['rate_pct']}%**\n"
        f"Matures: <t:{bond['matures_ts']}:R>  (<t:{bond['matures_ts']}:f>)\n"
        f"Payout at maturity: **{payout:,} UKPence**  (+{interest:,})\n\n"
        f"-# Break early and you get back {bond['principal'] - penalty:,} "
        f"(lose the {interest:,} interest and a {penalty:,} penalty)."
    )
    if note:
        txt = f"{note}\n\n{txt}"
    box.add_item(discord.ui.TextDisplay(txt))
    view.add_item(box)
    row = discord.ui.ActionRow()
    b = discord.ui.Button(label="Withdraw Early", emoji="⚠️", style=discord.ButtonStyle.danger,
                          custom_id=f"bond:wd:{user_id}")
    b.callback = _make_withdraw_cb(user_id)
    row.add_item(b)
    view.add_item(row)
    return view


def _build_confirm_panel(user_id, bond) -> discord.ui.LayoutView:
    pen = getattr(config, "BOND_EARLY_PENALTY_PCT", 10)
    penalty = bond["principal"] * pen // 100
    interest = interest_for(bond["principal"], bond["rate_pct"])
    refund = bond["principal"] - penalty
    view = discord.ui.LayoutView(timeout=300)
    box = discord.ui.Container(accent_colour=ACCENT)
    box.add_item(discord.ui.TextDisplay(
        "## ⚠️ Withdraw early?\n"
        f"You'll get back **{refund:,} UKPence** - losing the **{interest:,}** interest and "
        f"paying a **{penalty:,}** penalty.\n"
        f"-# Leave it in and it matures to **{bond['principal'] + interest:,}** <t:{bond['matures_ts']}:R>."
    ))
    view.add_item(box)
    row = discord.ui.ActionRow()
    yes = discord.ui.Button(label="Confirm Withdrawal", style=discord.ButtonStyle.danger,
                            custom_id=f"bond:wdyes:{user_id}")
    yes.callback = _make_withdraw_confirm_cb(user_id)
    row.add_item(yes)
    no = discord.ui.Button(label="Keep it", style=discord.ButtonStyle.secondary, custom_id=f"bond:wdno:{user_id}")
    no.callback = _make_withdraw_cancel_cb(user_id)
    row.add_item(no)
    view.add_item(row)
    return view


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_open_cb(user_id, term):
    async def _cb(interaction: Interaction):
        if interaction.user.id != user_id:
            await interaction.response.send_message("This isn't your bond panel.", ephemeral=True)
            return
        await interaction.response.send_modal(BondAmountModal(user_id, term))
    return _cb


class BondAmountModal(discord.ui.Modal, title="Open a bond"):
    def __init__(self, user_id, term):
        super().__init__()
        self.user_id = user_id
        self.term = term
        mx = getattr(config, "BOND_MAX", 5000)
        self.amount = discord.ui.TextInput(
            label=f"Amount to lock for {term} days",
            placeholder=f"max {mx:,} UKPence", required=True, max_length=12)
        self.add_item(self.amount)

    async def on_submit(self, interaction: Interaction):
        raw = str(self.amount.value).replace(",", "").strip()
        try:
            amt = int(raw)
        except ValueError:
            await interaction.response.send_message("Enter a whole number of UKPence.", ephemeral=True)
            return
        g, err = open_bond(self.user_id, amt, self.term)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.edit_message(view=build_status_panel(self.user_id, g, note="✅ Bond opened!"))


def _make_withdraw_cb(user_id):
    async def _cb(interaction: Interaction):
        if interaction.user.id != user_id:
            await interaction.response.send_message("This isn't your bond.", ephemeral=True)
            return
        g = get_active(user_id)
        if not g:
            await interaction.response.edit_message(view=build_open_panel(user_id))
            return
        await interaction.response.edit_message(view=_build_confirm_panel(user_id, g))
    return _cb


def _make_withdraw_confirm_cb(user_id):
    async def _cb(interaction: Interaction):
        if interaction.user.id != user_id:
            await interaction.response.send_message("Not your bond.", ephemeral=True)
            return
        refund, penalty, err = withdraw_early(user_id)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        view = discord.ui.LayoutView(timeout=300)
        box = discord.ui.Container(accent_colour=ACCENT)
        box.add_item(discord.ui.TextDisplay(
            f"\U0001f3e6 Bond withdrawn. **{refund:,} UKPence** returned "
            f"(you forfeited the interest and paid a **{penalty:,}** penalty)."))
        view.add_item(box)
        await interaction.response.edit_message(view=view)
    return _cb


def _make_withdraw_cancel_cb(user_id):
    async def _cb(interaction: Interaction):
        if interaction.user.id != user_id:
            await interaction.response.send_message("Not your bond.", ephemeral=True)
            return
        g = get_active(user_id)
        view = build_status_panel(user_id, g) if g else build_open_panel(user_id)
        await interaction.response.edit_message(view=view)
    return _cb


async def handle_bond_command(interaction: Interaction):
    if not getattr(config, "BOND_ENABLED", True):
        await interaction.response.send_message("Bonds are closed right now.", ephemeral=True)
        return
    g = get_active(interaction.user.id)
    view = build_status_panel(interaction.user.id, g) if g else build_open_panel(interaction.user.id)
    await interaction.response.send_message(view=view, ephemeral=True)


# ---------------------------------------------------------------------------
# Bond overview (a button on /bank-status)
# ---------------------------------------------------------------------------
def bonds_overview_embed() -> discord.Embed:
    rows = DatabaseManager.fetch_all(
        f"SELECT {_COLS} FROM bonds WHERE status = 'active'") or []
    active = [_row_to_bond(r) for r in rows]
    principal = sum(b["principal"] for b in active)
    interest = sum(interest_for(b["principal"], b["rate_pct"]) for b in active)

    e = discord.Embed(title="🏦 Bond Overview", colour=ACCENT,
                      description="Fixed-term savings currently locked in the bank.")
    if not active:
        e.description = "No active bonds right now."
    e.add_field(name="Active bonds", value=f"{len(active)}", inline=True)
    e.add_field(name="Locked (principal)", value=f"{principal:,} UKP", inline=True)
    e.add_field(name="Interest owed", value=f"{interest:,} UKP", inline=True)
    e.add_field(name="Payout liability", value=f"{principal + interest:,} UKP", inline=True)

    if active:
        terms = {}
        for b in active:
            slot = terms.setdefault(b["term_days"], [0, 0, b["rate_pct"]])
            slot[0] += 1
            slot[1] += b["principal"]
        by_term = "\n".join(
            f"**{d}d** @ {slot[2]}%: {slot[0]} bond(s) · {slot[1]:,} UKP"
            for d, slot in sorted(terms.items()))
        e.add_field(name="By term", value=by_term, inline=False)
        e.add_field(name="Next maturity", value=f"<t:{min(b['matures_ts'] for b in active)}:R>", inline=True)

    m = DatabaseManager.fetch_one("SELECT COUNT(*) FROM bonds WHERE status = 'matured'")
    w = DatabaseManager.fetch_one("SELECT COUNT(*) FROM bonds WHERE status = 'withdrawn'")
    e.add_field(name="History",
                value=f"Matured: {(m[0] if m else 0)} · Early-withdrawn: {(w[0] if w else 0)}",
                inline=False)
    return e


class BondOverviewView(discord.ui.View):
    """A 'Bond Overview' button to attach to the /bank-status embed."""

    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Bond Overview", emoji="🏦", style=discord.ButtonStyle.secondary,
                       custom_id="bankstatus:bonds")
    async def overview(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=bonds_overview_embed(), ephemeral=True)
