"""Bonds: fixed-term UKPence savings.

A player locks UKP for a set term; the principal is held in the Server Bank while locked,
and on maturity the bank repays principal + interest. Everything lives in the ``bonds``
table, and maturity is driven by a periodic scan (lib/bot/scheduled_tasks) rather than
in-memory timers - so it's fully restart-safe: an overdue bond simply pays out on the
next tick after the bot comes back.

Economy: each bond state change and its UKP movement share one SQLite transaction.
The bank nets -interest per matured bond, which is the intended drain of the over-full
bank back to savers. One active bond per user.

Rates are tiered and stored per bond in basis points (rate_bp on the first tier_split UKP,
upper_rate_bp on the rest), so a config change never touches a bond already running. Bonds
opened before that have NULL bp columns and are paid on their whole-percent rate_pct.
"""

import logging
import sqlite3
import time

import discord
from discord import Interaction

import config
from database import DatabaseManager
from lib.economy.bank_manager import BankManager
from lib.economy.economy_manager import UKPenceManager, get_bb
from lib.features.inbox import create_notification

log = logging.getLogger(__name__)
ACCENT = discord.Colour(0x1C6B46)

_COLS = ("id, user_id, principal, rate_pct, term_days, opened_ts, matures_ts, status, "
         "rate_bp, upper_rate_bp, tier_split")
_DEFAULT_TERMS_BP = {3: (100, 50), 7: (300, 160), 30: (1500, 800)}
_KEYS = [c.strip() for c in _COLS.split(",")]


def _row_to_bond(r):
    return dict(zip(_KEYS, r)) if r else None


def interest_for(principal: int, rate_pct: int, rate_bp=None, upper_rate_bp=None,
                 tier_split=None) -> int:
    """Interest a bond pays at maturity.

    Legacy bonds (no basis-point rates stored) pay principal * rate_pct // 100, unchanged.
    Tiered bonds pay rate_bp on the first tier_split UKP and upper_rate_bp on the rest."""
    principal = int(principal)
    if rate_bp is None:
        return principal * int(rate_pct) // 100
    upper_bp = int(rate_bp if upper_rate_bp is None else upper_rate_bp)
    split = principal if tier_split is None else max(0, int(tier_split))
    lower = min(principal, split)
    upper = principal - lower
    return lower * int(rate_bp) // 10000 + upper * upper_bp // 10000


def bond_interest(bond) -> int:
    """interest_for() on a bond dict as returned by get_active() and friends."""
    return interest_for(bond["principal"], bond["rate_pct"], bond.get("rate_bp"),
                        bond.get("upper_rate_bp"), bond.get("tier_split"))


def _terms_bp() -> dict:
    return getattr(config, "BOND_TERMS_BP", _DEFAULT_TERMS_BP)


def _tier_split() -> int:
    return int(getattr(config, "BOND_TIER_SPLIT", 5000))


def _fmt_bp(bp) -> str:
    """1500 -> '15%', 160 -> '1.6%', 53 -> '0.53%'."""
    return f"{int(bp) / 100:.2f}".rstrip("0").rstrip(".") + "%"


def rate_label(bond) -> str:
    """Human rate for a bond: '30%' for a legacy bond, '15% / 8% over 5,000' for a tiered one."""
    if bond.get("rate_bp") is None:
        return f"{bond['rate_pct']}%"
    lo, hi = bond["rate_bp"], bond.get("upper_rate_bp")
    split = bond.get("tier_split")
    if hi is None or hi == lo or split is None or bond["principal"] <= split:
        return _fmt_bp(lo)
    return f"{_fmt_bp(lo)} / {_fmt_bp(hi)} over {int(split):,}"


def _short_rate(bond) -> str:
    """'30%' for a legacy bond, '15% / 8%' for a tiered one (no split amount)."""
    if bond.get("rate_bp") is None:
        return f"{bond['rate_pct']}%"
    hi = bond.get("upper_rate_bp")
    if hi is None or hi == bond["rate_bp"]:
        return _fmt_bp(bond["rate_bp"])
    return f"{_fmt_bp(bond['rate_bp'])} / {_fmt_bp(hi)}"


def total_active_principal() -> int:
    """All principal currently locked in active bonds (the bank is holding it for savers)."""
    r = DatabaseManager.fetch_one(
        "SELECT COALESCE(SUM(principal), 0) FROM bonds WHERE status = 'active'")
    return int(r[0]) if r else 0


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
    terms = _terms_bp()
    if term_days not in terms:
        return None, "That isn't a valid bond term."
    rate_bp, upper_rate_bp = (int(x) for x in terms[term_days])
    tier_split = _tier_split()
    book_limit = getattr(config, "BOND_BOOK_LIMIT", None)
    if get_active(user_id):
        return None, "You already have an active bond - you can only hold one at a time."
    mx = getattr(config, "BOND_MAX", 10000)
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
    now = int(time.time())
    change = None
    bond_id = None
    try:
        with DatabaseManager.locked_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM bonds WHERE user_id = ? AND status = 'active' LIMIT 1",
                (str(user_id),),
            )
            if cursor.fetchone() is not None:
                return None, "You already have an active bond - you can only hold one at a time."
            if book_limit is not None:
                cursor.execute(
                    "SELECT COALESCE(SUM(principal), 0) FROM bonds WHERE status = 'active'")
                if int(cursor.fetchone()[0]) + int(principal) > int(book_limit):
                    return None, ("The Treasury's bond book is full right now. Try a smaller "
                                  "amount or check back after the next maturity.")

            change = UKPenceManager._remove_amount_in_transaction(
                cursor,
                int(user_id),
                int(principal),
                "Bond deposit",
                now,
            )
            if change is None:
                return None, "You don't have enough UKPence."
            BankManager._deposit_in_transaction(
                cursor,
                int(principal),
                "Bond deposit",
                now,
            )
            cursor.execute(
                "INSERT INTO bonds "
                "(user_id, principal, rate_pct, term_days, opened_ts, matures_ts, status, "
                "rate_bp, upper_rate_bp, tier_split) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                (
                    str(user_id),
                    int(principal),
                    # rate_pct is kept (NOT NULL, read by older queries) as the first-tier
                    # rate rounded to a whole percent; rate_bp is what actually pays.
                    int(round(rate_bp / 100)),
                    int(term_days),
                    now,
                    now + term_days * 86400,
                    rate_bp,
                    upper_rate_bp,
                    tier_split,
                ),
            )
            bond_id = cursor.lastrowid
    except sqlite3.Error:
        log.error("bond open transaction failed", exc_info=True)
        return None, "The bond could not be opened safely. Your balance was not changed."

    UKPenceManager._finish_change(change, bankrupt=True)
    row = DatabaseManager.fetch_one(
        f"SELECT {_COLS} FROM bonds WHERE id = ?", (bond_id,)
    )
    return _row_to_bond(row), None


def withdraw_early(user_id):
    """Refund principal minus the early-exit penalty (interest forfeited).
    Returns (refund, penalty, error_message)."""
    now = int(time.time())
    change = None
    penalty = refund = 0
    try:
        with DatabaseManager.locked_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT {_COLS} FROM bonds "
                "WHERE user_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
                (str(user_id),),
            )
            g = _row_to_bond(cursor.fetchone())
            if not g:
                return 0, 0, "You don't have an active bond."

            pen_pct = getattr(config, "BOND_EARLY_PENALTY_PCT", 10)
            penalty = g["principal"] * pen_pct // 100
            refund = g["principal"] - penalty
            if not BankManager._withdraw_in_transaction(
                cursor, refund, "Bond early withdrawal", now
            ):
                return 0, 0, "The Server Bank cannot cover this withdrawal right now."
            change = UKPenceManager._add_amount_in_transaction(
                cursor,
                int(user_id),
                refund,
                "Bond early withdrawal",
                now,
            )
            cursor.execute(
                "UPDATE bonds SET status = 'withdrawn' "
                "WHERE id = ? AND status = 'active'",
                (g["id"],),
            )
            if cursor.rowcount != 1:
                raise sqlite3.IntegrityError("active bond changed during early withdrawal")
    except sqlite3.Error:
        log.error("bond early-withdrawal transaction failed", exc_info=True)
        return 0, 0, "The bond could not be withdrawn safely. Your balance was not changed."

    UKPenceManager._finish_change(change, high_roller=True)
    return refund, penalty, None


async def mature_due(client):
    """Pay out every matured active bond (principal + interest) and DM the holder.
    Idempotent: state and payout commit together, so a re-run or crash can neither
    double-pay nor mark an unpaid bond as matured."""
    now = int(time.time())
    rows = DatabaseManager.fetch_all(
        f"SELECT {_COLS} FROM bonds WHERE status = 'active' AND matures_ts <= ?", (now,)) or []
    for r in rows:
        queued = _row_to_bond(r)
        change = None
        try:
            with DatabaseManager.locked_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"SELECT {_COLS} FROM bonds "
                    "WHERE id = ? AND status = 'active' AND matures_ts <= ?",
                    (queued["id"], now),
                )
                g = _row_to_bond(cursor.fetchone())
                if not g:
                    continue
                interest = bond_interest(g)
                payout = g["principal"] + interest
                if not BankManager._withdraw_in_transaction(
                    cursor, payout, "Bond maturity", now
                ):
                    log.error(
                        "bank cannot cover matured bond id=%s payout=%s; left active for retry",
                        g["id"],
                        payout,
                    )
                    continue
                change = UKPenceManager._add_amount_in_transaction(
                    cursor,
                    int(g["user_id"]),
                    payout,
                    "Bond maturity",
                    now,
                )
                cursor.execute(
                    "UPDATE bonds SET status = 'matured' "
                    "WHERE id = ? AND status = 'active'",
                    (g["id"],),
                )
                if cursor.rowcount != 1:
                    raise sqlite3.IntegrityError("active bond changed during maturity")
        except sqlite3.Error:
            log.error("bond maturity transaction failed for id=%s", queued["id"], exc_info=True)
            continue

        UKPenceManager._finish_change(change, high_roller=True)
        notice_body = (
            f"Your {g['term_days']}-day bond matured and paid {payout:,} UKPence "
            f"({g['principal']:,} principal plus {interest:,} interest)."
        )
        try:
            create_notification(
                g["user_id"],
                "economy",
                "Bond matured",
                notice_body,
                created_at=now,
            )
        except Exception:
            # Inbox persistence and Discord delivery are deliberately independent:
            # a temporary storage failure must not suppress the existing DM path.
            log.warning("bond maturity inbox notification failed", exc_info=True)
        try:
            user = client.get_user(int(g["user_id"])) or await client.fetch_user(int(g["user_id"]))
            await user.send(
                f"\U0001f3e6 Your **{g['term_days']}-day bond** has matured! You get back "
                f"**{payout:,} UKPence** (your {g['principal']:,} principal + {interest:,} interest). Tidy."
            )
        except Exception:
            log.debug("bond maturity DM failed", exc_info=True)
        # Badges: total bond interest earned (incl. this one) >= 10000 -> Bond Villain.
        from lib.features.income_badges import award_badge_safe, record_income_source
        matured = DatabaseManager.fetch_all(
            "SELECT principal, rate_pct, rate_bp, upper_rate_bp, tier_split FROM bonds "
            "WHERE user_id = ? AND status = 'matured'",
            (str(g["user_id"]),)) or []
        if sum(interest_for(*row) for row in matured) >= 10000:
            await award_badge_safe(client, g["user_id"], "bond_villain")
        await record_income_source(client, g["user_id"], "bond")


def forfeit_bonds_on_leave(user_id) -> int:
    """Forfeit all active bonds for a user when they leave the server.
    The principal remains in the Server Bank (reclaimed) and no interest is paid.
    Returns total principal forfeited."""
    try:
        with DatabaseManager.locked_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, principal FROM bonds WHERE user_id = ? AND status = 'active'",
                (str(user_id),),
            )
            active = cursor.fetchall()
            if not active:
                return 0
            total_principal = sum(row[1] for row in active)
            cursor.execute(
                "UPDATE bonds SET status = 'forfeited' WHERE user_id = ? AND status = 'active'",
                (str(user_id),),
            )
            log.info(
                "forfeited %s active bond(s) (%s UKP total) for leaving user %s",
                len(active),
                total_principal,
                user_id,
            )
            return total_principal
    except sqlite3.Error:
        log.error("failed to forfeit active bonds for leaving user %s", user_id, exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Panels (text only - no image render, so no interaction-expiry risk)
# ---------------------------------------------------------------------------
def _term_lines() -> str:
    split = _tier_split()
    mx = int(getattr(config, "BOND_MAX", 10000))
    lines = []
    for d, (lo, hi) in sorted(_terms_bp().items()):
        top = interest_for(mx, 0, lo, hi, split)
        if hi == lo or mx <= split:
            lines.append(f"- **{d} days**: {_fmt_bp(lo)} (up to +{top:,})")
        else:
            lines.append(f"- **{d} days**: {_fmt_bp(lo)} on the first {split:,}, "
                         f"{_fmt_bp(hi)} on the rest (up to +{top:,})")
    return "\n".join(lines)


def build_open_panel(user_id) -> discord.ui.LayoutView:
    bal = get_bb(user_id)
    mx = getattr(config, "BOND_MAX", 10000)
    pen = getattr(config, "BOND_EARLY_PENALTY_PCT", 10)
    demurrage_note = (" Bonded UKP counts toward wealth demurrage."
                      if getattr(config, "BOND_COUNTS_FOR_DEMURRAGE", True) else "")
    view = discord.ui.LayoutView(timeout=300)
    box = discord.ui.Container(accent_colour=ACCENT)
    box.add_item(discord.ui.TextDisplay(
        "## \U0001f3e6 UKPence Bonds - Treasury Savings\n"
        f"Lock up to **{mx:,} UKPence** for a fixed term and earn interest, paid from the Server Bank.\n"
        f"Your balance: **{bal:,} UKPence**\n\n"
        f"{_term_lines()}\n\n"
        f"-# Max **{mx:,}** per bond, one at a time. Break early and you forfeit the interest "
        f"plus a {pen}% penalty on your principal.{demurrage_note}"
    ))
    view.add_item(box)
    row = discord.ui.ActionRow()
    for d, (lo, _hi) in sorted(_terms_bp().items()):
        b = discord.ui.Button(label=f"{d}d · {_fmt_bp(lo)}", style=discord.ButtonStyle.success,
                              custom_id=f"bond:open:{user_id}:{d}")
        b.callback = _make_open_cb(user_id, d)
        row.add_item(b)
    view.add_item(row)
    return view


def _interest_split_text(bond) -> str:
    """'  (+750 on the first 5,000, +400 on the rest)' for a tiered bond that spans both tiers."""
    if bond.get("rate_bp") is None or bond.get("tier_split") is None:
        return ""
    split = int(bond["tier_split"])
    if bond["principal"] <= split or bond.get("upper_rate_bp") in (None, bond["rate_bp"]):
        return ""
    lo = split * int(bond["rate_bp"]) // 10000
    hi = (bond["principal"] - split) * int(bond["upper_rate_bp"]) // 10000
    return f"\n-# +{lo:,} on the first {split:,}, +{hi:,} on the rest"


def build_status_panel(user_id, bond, *, note=None) -> discord.ui.LayoutView:
    interest = bond_interest(bond)
    payout = bond["principal"] + interest
    pen = getattr(config, "BOND_EARLY_PENALTY_PCT", 10)
    penalty = bond["principal"] * pen // 100
    view = discord.ui.LayoutView(timeout=300)
    box = discord.ui.Container(accent_colour=ACCENT)
    txt = (
        "## \U0001f3e6 Your Bond\n"
        f"Locked: **{bond['principal']:,} UKPence**\n"
        f"Term: **{bond['term_days']} days** @ **{rate_label(bond)}**\n"
        f"Matures: <t:{bond['matures_ts']}:R>  (<t:{bond['matures_ts']}:f>)\n"
        f"Payout at maturity: **{payout:,} UKPence**  (+{interest:,}){_interest_split_text(bond)}\n\n"
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
    interest = bond_interest(bond)
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
        mx = getattr(config, "BOND_MAX", 10000)
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
        from lib.features.income_badges import award_badge_safe
        await award_badge_safe(interaction.client, self.user_id, "saver")
        if self.term == 30:
            await award_badge_safe(interaction.client, self.user_id, "long_game")


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
        from lib.features.income_badges import award_badge_safe
        await award_badge_safe(interaction.client, user_id, "paper_hands")
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
    interest = sum(bond_interest(b) for b in active)

    e = discord.Embed(title="🏦 Bond Overview", colour=ACCENT,
                      description="Fixed-term savings currently locked in the bank.")
    if not active:
        e.description = "No active bonds right now."
    e.add_field(name="Active bonds", value=f"{len(active)}", inline=True)
    e.add_field(name="Locked (principal)", value=f"{principal:,} UKP", inline=True)
    e.add_field(name="Interest owed", value=f"{interest:,} UKP", inline=True)
    e.add_field(name="Payout liability", value=f"{principal + interest:,} UKP", inline=True)
    limit = getattr(config, "BOND_BOOK_LIMIT", None)
    if limit:
        e.add_field(name="Book limit", value=f"{principal:,} / {int(limit):,} UKP", inline=True)

    if active:
        terms = {}
        for b in active:
            slot = terms.setdefault(b["term_days"], [0, 0, set()])
            slot[0] += 1
            slot[1] += b["principal"]
            slot[2].add(_short_rate(b))
        by_term = "\n".join(
            f"**{d}d** @ {', '.join(sorted(slot[2]))}: {slot[0]} bond(s) · {slot[1]:,} UKP"
            for d, slot in sorted(terms.items()))
        e.add_field(name="By term", value=by_term, inline=False)
        e.add_field(name="Next maturity", value=f"<t:{min(b['matures_ts'] for b in active)}:R>", inline=True)

    m = DatabaseManager.fetch_one("SELECT COUNT(*) FROM bonds WHERE status = 'matured'")
    w = DatabaseManager.fetch_one("SELECT COUNT(*) FROM bonds WHERE status = 'withdrawn'")
    f_count = DatabaseManager.fetch_one("SELECT COUNT(*) FROM bonds WHERE status = 'forfeited'")
    hist_text = f"Matured: {(m[0] if m else 0)} · Early-withdrawn: {(w[0] if w else 0)}"
    if f_count and f_count[0] > 0:
        hist_text += f" · Forfeited on leave: {f_count[0]}"
    e.add_field(name="History", value=hist_text, inline=False)
    return e


# The button itself now lives on BankStatusView in commands/economy/bank_commands.py,
# alongside the Tax & Reserves panel - bonds_overview_embed() above is what it renders.
