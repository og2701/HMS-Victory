"""One-time UKPence reward for earning a badge, paid from the bank (supply-conserving).

A given (user_id, badge_id) is rewarded at most once: the ``badge_rewards`` ledger table is
the idempotency record, so the live grant hook, the one-shot backfill, and any re-run all
share it and can never double-pay. Amounts come from ``config.BADGE_REWARDS`` keyed by the
badge's rarity (Bronze/Silver/Gold/Secret); a tier mapped to 0 - or an unknown rarity - pays
nothing.
"""
import logging
import time

from database import DatabaseManager

log = logging.getLogger(__name__)


def reward_amount(badge_id: str) -> int:
    """The configured reward for a badge, by its rarity. 0 if unknown / no reward."""
    import config
    row = DatabaseManager.fetch_one("SELECT rarity FROM badges WHERE id = ?", (badge_id,))
    if not row:
        return 0
    return int(getattr(config, "BADGE_REWARDS", {}).get(row[0], 0))


def already_paid(user_id, badge_id: str) -> bool:
    return DatabaseManager.fetch_one(
        "SELECT 1 FROM badge_rewards WHERE user_id = ? AND badge_id = ?",
        (str(user_id), badge_id)) is not None


def pay_badge_reward(user_id, badge_id: str) -> int:
    """Pay the one-time reward for (user, badge) if not already paid. Returns the amount paid
    (0 if no reward is configured or it was already paid). Paid from the bank via
    credit_from_bank, which mints + logs only if the bank is somehow insolvent."""
    amount = reward_amount(badge_id)
    if amount <= 0:
        return 0
    uid = str(user_id)
    # Claim first: INSERT OR IGNORE's rowcount is 1 only if WE inserted the ledger row. A
    # re-run, or the hook firing for an already-paid badge, inserts 0 rows and pays nothing.
    claimed = DatabaseManager.execute(
        "INSERT OR IGNORE INTO badge_rewards (user_id, badge_id, amount, paid_at) "
        "VALUES (?, ?, ?, ?)", (uid, badge_id, amount, int(time.time())))
    if not claimed:
        return 0
    try:
        from commands.economy.casino_base import credit_from_bank
        credit_from_bank(int(user_id), amount, reason=f"Badge reward: {badge_id}")
    except Exception:
        # Credit failed after the claim - release it so the reward can be retried rather than
        # being marked paid without the user ever receiving it.
        log.error("Badge reward credit failed for %s/%s; releasing claim", uid, badge_id,
                  exc_info=True)
        DatabaseManager.execute(
            "DELETE FROM badge_rewards WHERE user_id = ? AND badge_id = ?", (uid, badge_id))
        return 0
    return amount
