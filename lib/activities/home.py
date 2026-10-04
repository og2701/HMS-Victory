"""The activity's home screen: balance, today's daily games (Wordle, the crossword, Climb HMS Victory
and the Spitfire), and the casino in the order the player last played its games.

The puzzle cards only carry what the card shows (colours for Wordle, the grid's shape for
the crossword), never letters, so the home screen can't give an answer away.
"""

from __future__ import annotations

import config
from database import DatabaseManager

VISIT = 3600     # rounds this close before a game's last one count as that visit


def _wordle(uid: int, date) -> dict:
    from lib.activities import wordle_api
    s = wordle_api.state(uid, date)
    return {
        "rows": [r["score"] for r in s["rows"]],
        "solved": s["solved"], "done": s["done"], "paid": s["paid"],
        "guesses": len(s["rows"]), "next": s["next"], "top": s["rewards"][0],
    }


def _crossword(uid: int, date) -> dict:
    from lib.activities import crossword_api
    s = crossword_api.state(uid, date)
    solved = sum(1 for e in s["entries"] if e["solved"])
    return {
        "shape": [[c is not None for c in row] for row in s["cells"]],
        "filled": [[bool(c and c["letter"]) for c in row] for row in s["cells"]],
        "solved": solved, "total": len(s["entries"]),
        "done": s["done"], "paid": s["paid"], "reward": s["reward"],
        "started": solved > 0 or s["hints"]["used"] > 0 or s["wrong"]["count"] > 0,
    }


def _score_card(key: str, uid: int, date) -> dict | None:
    """A daily score game's tile; a problem with it never takes the home screen down."""
    from lib.activities.daily_score import GAMES
    try:
        return GAMES[key].home_card(uid, date)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("couldn't load the %s home card", key, exc_info=True)
        return None


def _casino(uid: int) -> list[dict]:
    from lib.activities import casino
    from lib.activities.casino import base
    reg = casino.registry()
    last = dict(DatabaseManager.fetch_all(
        "SELECT game, MAX(timestamp) FROM casino_results WHERE user_id = ? GROUP BY game",
        (str(uid),)) or [])
    visit = dict(DatabaseManager.fetch_all(
        "SELECT r.game, SUM(r.net) FROM casino_results r "
        "JOIN (SELECT game, MAX(timestamp) AS t FROM casino_results WHERE user_id = ? GROUP BY game) m "
        "ON m.game = r.game WHERE r.user_id = ? AND r.timestamp >= m.t - ? GROUP BY r.game",
        (str(uid), str(uid), VISIT)) or [])
    playing = base.in_play_keys(uid, reg)
    games = []
    for i, key in enumerate(casino.ORDER):
        a = reg.get(key)
        if a is None:
            continue
        games.append({"key": key, "label": a.label, "lastPlayed": last.get(key),
                      "lastNet": visit.get(key), "inPlay": key in playing,
                      "open": a.enabled(), "_order": i})
    games.sort(key=lambda g: (-(g["lastPlayed"] or 0), g["_order"]))
    for g in games:
        del g["_order"]
    return games


def state(client, uid: int) -> dict:
    from lib.economy.economy_manager import get_bb
    from lib.features.wordle import _today
    date = _today()
    guild = client.get_guild(config.GUILD_ID) if client else None
    member = guild.get_member(uid) if guild else None
    return {
        "name": getattr(member, "display_name", None),
        "balance": get_bb(uid),
        "dateLabel": f"{date:%A %-d %B}",
        "wordle": _wordle(uid, date),
        "crossword": _crossword(uid, date),
        "climb": _score_card("climb", uid, date),
        "spitfire": _score_card("spitfire", uid, date),
        "casino": _casino(uid),
    }
