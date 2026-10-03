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
    opening = game is None                  # the table being opened, not a move's answer
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
    if opening:
        out.update(a.opening(uid))
    if round_ is not None:
        out["round"] = {"staked": round_.staked, "payout": round_.payout, "net": round_.net,
                        "outcome": round_.outcome}
    return out


_watchers: dict[tuple[int, str], dict[int, float]] = {}    # (player, table) -> {spectator: last look}
WATCHED_FOR = 6          # seconds a spectator's last look counts as still watching


def spectators(uid: int, key: str) -> list[int]:
    """Who's watching this player's table right now, earliest first."""
    import time
    now = time.time()
    looks = _watchers.get((int(uid), key), {})
    for w in [w for w, at in looks.items() if now - at >= WATCHED_FOR]:
        del looks[w]
    return list(looks)


def watched(uid: int, key: str) -> bool:
    """Is anyone spectating this player's table right now?"""
    return bool(spectators(uid, key))


def watch(uid: int, key: str, name: str | None = None, watcher: int = 0) -> dict:
    """Someone else's table, for a spectator: the view the player has (which shows nothing they
    haven't seen: no hole card, no mine layout), without their balance or career."""
    import time
    a = adapter(key)
    if a is None or not a.watchable:
        raise Refuse("That table can't be watched.")
    _watchers.setdefault((int(uid), key), {})[int(watcher)] = time.time()
    game = base.in_play(uid, key, registry()) or base.last_finished(uid, key)
    low, high = a.limits()
    return {
        "key": key,
        "label": a.label,
        "table": a.view(game) if game is not None else None,
        "inPlay": game is not None and not a.over(game),
        "balance": 0,
        "limits": {"min": low, "max": high},
        "session": sessions.summary(uid, key),
        "career": 0,
        "rules": [{"label": k, "text": v} for k, v in a.rules()],
        **a.extras(uid),
        **a.spectate(uid),
        "watching": {"uid": str(uid), "name": name or "A player", "playing": sessions.playing(uid, key)},
        "spectators": [str(w) for w in spectators(uid, key)],
    }


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
    a, game, finished = await base.play(registry(), uid, name, key, action, body, on_round=_on_round)
    progress = a.progress(game) if game is not None and not a.over(game) else None
    if action == "deal" and finished is None:
        sessions.begin(uid, key, a.label, getattr(a, "unit", "rounds"), progress)
    else:
        sessions.touch(uid, key, progress)
    return table(uid, key, game, finished)


__all__ = ["Busy", "Refuse", "ORDER", "adapter", "move", "registry", "table", "watch"]
