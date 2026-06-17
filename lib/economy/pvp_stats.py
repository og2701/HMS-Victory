"""Result log for 1v1 PvP wager games (Connect 4, Battleship, ... and any future ones).

Every finished match is appended to the ``pvp_results`` table via ``record_result`` - new
games just call it with their own ``game`` key, no schema changes needed. Best-effort: a
stats failure must never break a payout, so writes swallow + log rather than raise.

Outcome is one of: 'win' (played to a finish), 'forfeit' (opponent timed out), 'draw'.
Voided/refunded games are NOT recorded (no contest).
"""

import time
import logging

from database import DatabaseManager

log = logging.getLogger(__name__)


def record_result(game: str, winner_id, loser_id, stake, outcome: str = "win") -> None:
    """Append one finished PvP match. winner_id/loser_id may be None on a draw."""
    try:
        DatabaseManager.execute(
            "INSERT INTO pvp_results (game, winner_id, loser_id, stake, outcome, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(game),
             str(winner_id) if winner_id is not None else None,
             str(loser_id) if loser_id is not None else None,
             int(stake), str(outcome), int(time.time())),
        )
    except Exception:
        log.error("Failed to record pvp result (%s)", game, exc_info=True)


def win_count(game: str, user_id) -> int:
    row = DatabaseManager.fetch_one(
        "SELECT COUNT(*) FROM pvp_results WHERE game = ? AND winner_id = ?",
        (str(game), str(user_id)))
    return row[0] if row else 0


def games_played(game: str, user_id) -> int:
    row = DatabaseManager.fetch_one(
        "SELECT COUNT(*) FROM pvp_results WHERE game = ? AND (winner_id = ? OR loser_id = ?)",
        (str(game), str(user_id), str(user_id)))
    return row[0] if row else 0


def record(user_id) -> dict:
    """A user's overall PvP record across all games: {wins, losses, draws}."""
    uid = str(user_id)
    wins = (DatabaseManager.fetch_one(
        "SELECT COUNT(*) FROM pvp_results WHERE winner_id = ?", (uid,)) or [0])[0]
    losses = (DatabaseManager.fetch_one(
        "SELECT COUNT(*) FROM pvp_results WHERE loser_id = ? AND outcome != 'draw'", (uid,)) or [0])[0]
    draws = (DatabaseManager.fetch_one(
        "SELECT COUNT(*) FROM pvp_results WHERE outcome = 'draw' AND (winner_id = ? OR loser_id = ?)",
        (uid, uid)) or [0])[0]
    return {"wins": wins, "losses": losses, "draws": draws}
