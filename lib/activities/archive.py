"""Every finished game in the activity's games against people kept for good, all of it: Countdown's rounds and words,
Broadside's moves (practice against HMS Victory too). One row per game in game_records with the game as it ended (its
own JSON, so nothing about it is lost), and one per player in game_record_players with their place, how it went for
them and what they won, for looking someone up. UKP Kart has its own tables (kart_races, kart_results), with times.

Best-effort, like pvp_stats: a failure here never touches a payout."""

import json
import logging
import time

log = logging.getLogger(__name__)


def keep(game: str, gid: str, mode: str, stake: int, outcome: str, players: list[dict], data: dict,
         ended: float | None = None) -> None:
    """players: [{"user_id", "place", "result", "payout"}] (user_id None for HMS Victory in practice)."""
    try:
        from database import DatabaseManager
        DatabaseManager.execute(
            "INSERT OR REPLACE INTO game_records (game, id, mode, stake, outcome, ended, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (game, str(gid), mode, int(stake or 0), str(outcome), int(ended or time.time()), json.dumps(data, default=str)))
        DatabaseManager.execute("DELETE FROM game_record_players WHERE game = ? AND id = ?", (game, str(gid)))
        for p in players:
            DatabaseManager.execute(
                "INSERT INTO game_record_players (game, id, user_id, place, result, payout) VALUES (?, ?, ?, ?, ?, ?)",
                (game, str(gid), None if p.get("user_id") is None else str(p["user_id"]), p.get("place"),
                 p.get("result"), int(p.get("payout") or 0)))
    except Exception:
        log.error("couldn't keep %s game %s", game, gid, exc_info=True)
