"""The HMS casino inside the activity: the bot's house games behind a JSON API.

base.py holds the shared plumbing (bets, stakes, the in-play store, one move at a time),
sessions.py the #casino posts, and games/ one adapter per game. This module ties them
together for server.py: the registry, a table's JSON, and a move that also counts towards
the player's sitting.
"""

from __future__ import annotations

import logging

from lib.activities.casino import base, sessions
from lib.activities.casino.base import Busy, Refuse

log = logging.getLogger(__name__)

# Home lists games in this order until a player has history to sort by.
ORDER = ("blackjack", "slots", "mines", "roulette", "higherlower", "videopoker", "reddog",
         "tcp", "chest", "glass", "blockade", "darts", "penalty", "pennyfalls")

_registry: dict | None = None


def registry() -> dict:
    global _registry
    if _registry is None:
        from lib.activities.casino.games import ADAPTERS
        _registry = {a.key: a for a in ADAPTERS}
    return _registry


def adapter(key: str):
    return registry().get(key)


def _career(uid: int) -> int:
    from database import DatabaseManager
    row = DatabaseManager.fetch_one(
        "SELECT COALESCE(SUM(net), 0) FROM casino_results WHERE user_id = ?", (str(uid),))
    return int(row[0]) if row else 0


def table(uid: int, key: str, game=None, round_=None) -> dict:
    """Everything the table screen needs: the hand (if any), balance, limits and ledger."""
    from lib.economy.economy_manager import get_bb
    a = adapter(key)
    if game is None:
        game = base.in_play(uid, key, registry()) or base.last_finished(uid, key)
    low, high = a.limits()
    out = {
        "key": key,
        "label": a.label,
        "table": a.view(game) if game is not None else None,
        "inPlay": game is not None and not a.over(game),
        "balance": get_bb(uid),
        "limits": {"min": low, "max": high},
        "session": sessions.summary(uid, key),
        "career": _career(uid),
        "rules": [{"label": k, "text": v} for k, v in a.rules()],
        **a.extras(uid),
    }
    if round_ is not None:
        out["round"] = {"staked": round_.staked, "payout": round_.payout, "net": round_.net,
                        "outcome": round_.outcome}
    return out


def _on_round(uid: int, key: str, rnd, game=None) -> None:
    a = adapter(key)
    view = None
    if game is not None:
        try:
            view = a.view(game)
        except Exception:
            log.warning("couldn't read the finished %s table", key, exc_info=True)
    sessions.record(uid, key, a.label, getattr(a, "unit", "rounds"), rnd, view)


async def move(uid: int, name: str, key: str, action: str, body: dict) -> dict:
    """Play one move and return the table after it. Raises Refuse with the reason."""
    _, game, finished = await base.play(registry(), uid, name, key, action, body, on_round=_on_round)
    return table(uid, key, game, finished)


__all__ = ["Busy", "Refuse", "ORDER", "adapter", "move", "registry", "table"]
