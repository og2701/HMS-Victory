"""Badge award helpers for the games (Connect 4, Higher/Lower, Blackjack, casino-wide).

Every function is best-effort: a failure here must never break a payout or a game, so
callers fire-and-forget and everything is wrapped to swallow + log errors. Awards go
through award_badge_with_notify, which is idempotent (a badge already held is a no-op),
so it's safe to call the low-threshold checks on every win.
"""

import logging
import time

from database import DatabaseManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connect 4
# ---------------------------------------------------------------------------
def record_connect4_result(winner_id, loser_id, stake):
    """Log a finished Connect 4 match (winner_id None on a draw)."""
    try:
        DatabaseManager.execute(
            "INSERT INTO connect4_results (winner_id, loser_id, stake, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (str(winner_id) if winner_id is not None else None,
             str(loser_id) if loser_id is not None else None,
             int(stake), int(time.time())),
        )
    except Exception:
        logger.error("Failed to record connect4 result", exc_info=True)


def _connect4_win_count(uid) -> int:
    row = DatabaseManager.fetch_one(
        "SELECT COUNT(*) FROM connect4_results WHERE winner_id = ?", (str(uid),))
    return row[0] if row else 0


async def award_connect4_badges(client, winner_id, stake):
    """Award the winner's Connect 4 badges from their lifetime wins + this match's stake.
    Call AFTER record_connect4_result so the count includes this win."""
    try:
        from lib.bot.event_handlers import award_badge_with_notify
        wins = _connect4_win_count(winner_id)
        if wins >= 1:
            await award_badge_with_notify(client, winner_id, "first_blood")
        if wins >= 10:
            await award_badge_with_notify(client, winner_id, "four_in_a_row")
        if wins >= 50:
            await award_badge_with_notify(client, winner_id, "grandmaster")
        if int(stake) >= 1000:
            await award_badge_with_notify(client, winner_id, "trash_talker")
    except Exception:
        logger.error("connect4 badge award failed", exc_info=True)


# ---------------------------------------------------------------------------
# Higher or Lower
# ---------------------------------------------------------------------------
async def award_higherlower_badges(client, game):
    """on_the_up: win 3 guesses in one game (steps). vertigo: cash out at >= 5x."""
    try:
        from lib.bot.event_handlers import award_badge_with_notify
        if getattr(game, "steps", 0) >= 3:
            await award_badge_with_notify(client, game.player_id, "on_the_up")
        if getattr(game, "outcome", None) == "win" and getattr(game, "cumulative", 0) >= 5.0:
            await award_badge_with_notify(client, game.player_id, "vertigo")
    except Exception:
        logger.error("higher/lower badge award failed", exc_info=True)
