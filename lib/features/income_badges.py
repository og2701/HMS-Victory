"""Shared badge helpers for the economy features.

Kept in its own module (importing only config + file_operations, and lazily importing
award_badge_with_notify) so any feature can use it without circular imports.
"""

import logging

import config
from lib.core.file_operations import load_json_file, save_json_file

log = logging.getLogger(__name__)


async def award_badge_safe(client, user_id, badge_id):
    """Award a badge (with the usual DM/notify), swallowing any error - a badge must never
    break a payout. No-op if the user already has it."""
    try:
        from lib.bot.event_handlers import award_badge_with_notify
        await award_badge_with_notify(client, int(user_id), badge_id)
    except Exception:
        log.debug("badge award failed: %s -> %s", badge_id, user_id, exc_info=True)


async def record_income_source(client, user_id, source):
    """Track the distinct income sources a user has earned from; award 'jack_of_all_trades'
    once they hit 5. Sources are short keys like 'chat', 'tree', 'benefits', 'bond', 'casino'."""
    try:
        store = load_json_file(config.EARNED_SOURCES_FILE) or {}
        srcs = set(store.get(str(user_id), []))
        if source not in srcs:
            srcs.add(source)
            store[str(user_id)] = sorted(srcs)
            save_json_file(config.EARNED_SOURCES_FILE, store)
        if len(srcs) >= 5:
            await award_badge_safe(client, user_id, "jack_of_all_trades")
    except Exception:
        log.debug("income source record failed", exc_info=True)


def bump_daily_income(source_key, amount):
    """Add `amount` to today's (UK) running total for an income source in the persistent
    economy metrics file, so /ukpeconomy's 'Recent Injections' board can report it.
    economy_transactions is a drained queue, so we aggregate here instead. Best-effort."""
    try:
        import pytz
        from datetime import datetime
        from lib.economy.economy_manager import EconomyMetrics
        if not amount:
            return
        today = datetime.now(pytz.timezone("Europe/London")).strftime("%Y-%m-%d")
        EconomyMetrics.update_daily_metric(today, source_key, int(amount))
    except Exception:
        log.debug("bump_daily_income failed: %s", source_key, exc_info=True)
